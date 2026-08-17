"""End-to-end tests that drive a real git repository.

The rest of the git tests monkeypatch ``git_ops._run``, which makes them blind
to how git actually behaves — an ignored path named in a pathspec, a tracked
log file left modified by the tee, and so on. These tests shell out to git for
real so that class of bug cannot slip through again.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pytest

from agent_loop import git_ops, orchestrator, safety
from agent_loop.orchestrator import LoopHalted, run_loop
from agent_loop.providers.base import Provider
from agent_loop.state import load_state


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A real git repo with one commit, checked out on a non-protected branch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "work", cwd=root)
    _git("config", "user.email", "loop@example.com", cwd=root)
    _git("config", "user.name", "Agent Loop", cwd=root)
    (root / "code.py").write_text("original\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "initial", cwd=root)
    # git_ops shells out against the process CWD.
    monkeypatch.chdir(root)
    return root


def _committed_files(repo: Path, ref: str = "HEAD") -> set[str]:
    out = _git("show", "--name-only", "--format=", ref, cwd=repo).stdout
    return {line for line in out.splitlines() if line}


def _tracked_files(repo: Path) -> set[str]:
    out = _git("ls-files", cwd=repo).stdout
    return {line for line in out.splitlines() if line}


# ---------------------------------------------------------------------------
# Logs are committed as audit artifacts
# ---------------------------------------------------------------------------


def test_checkpoint_commit_includes_log_files(repo):
    """The whole point of the feature: run logs land in the checkpoint commit."""
    logs = repo / "logs"
    logs.mkdir()
    (logs / "loop_1.log").write_text("run output so far\n")
    (repo / "code.py").write_text("agent edited this\n")

    git_ops.commit_checkpoint_changes("phase0: built", log_dir=logs)

    assert _committed_files(repo) == {"code.py", "logs/loop_1.log"}


def test_commit_succeeds_when_logs_dir_exists(repo):
    """Regression: `.gitignore` + an explicit `:(exclude)logs` pathspec made git
    exit 1 ("paths are ignored by one of your .gitignore files"), crashing every
    checkpoint commit. Neither mechanism may come back."""
    logs = repo / "logs"
    logs.mkdir()
    (logs / "loop_1.log").write_text("output\n")
    (repo / "code.py").write_text("changed\n")

    git_ops.commit_checkpoint_changes("phase0: built", log_dir=logs)

    assert "logs/loop_1.log" in _tracked_files(repo)


def test_second_run_starts_cleanly_after_prior_log_committed(repo):
    """Regression: the tee keeps appending after the final commit, so last run's
    log is tracked *and modified* when the next run's preflight runs. That must
    not block the loop."""
    logs = repo / "logs"
    logs.mkdir()
    log = logs / "loop_1.log"

    # Run 1: brand new log is untracked — preflight must allow it.
    log.write_text("run 1 output\n")
    git_ops.require_clean_worktree(log_dir=logs)
    git_ops.commit_checkpoint_changes("audit: completed run", log_dir=logs)

    # The tee flushes its tail after that final commit.
    log.write_text("run 1 output\n### All checkpoints approved.\n")
    assert _git("status", "--porcelain", cwd=repo).stdout.rstrip("\n") == " M logs/loop_1.log"

    # Run 2: preflight must still pass.
    git_ops.require_clean_worktree(log_dir=logs)


def test_custom_in_repo_log_dir_is_committed_and_does_not_block(repo):
    """`--log-dir audit` is inside the repo, so it is audited like ./logs."""
    audit = repo / "audit"
    audit.mkdir()
    (audit / "loop_1.log").write_text("output\n")

    # Preflight sees only the log dir, so the run is allowed to start.
    git_ops.require_clean_worktree(log_dir=audit)

    (repo / "code.py").write_text("changed\n")
    git_ops.commit_checkpoint_changes("phase0: built", log_dir=audit)
    assert "audit/loop_1.log" in _committed_files(repo)

    (audit / "loop_1.log").write_text("output\ntail\n")
    git_ops.require_clean_worktree(log_dir=audit)


def test_real_source_changes_still_block_the_loop(repo):
    """The log-dir exemption must not turn into a blanket 'always clean'."""
    logs = repo / "logs"
    logs.mkdir()
    (logs / "loop_1.log").write_text("output\n")
    (repo / "code.py").write_text("uncommitted human edit\n")

    with pytest.raises(git_ops.GitError, match="Worktree must be clean"):
        git_ops.require_clean_worktree(log_dir=logs)


# ---------------------------------------------------------------------------
# Log directory outside the repository
# ---------------------------------------------------------------------------


def test_external_log_dir_warns_and_commits_nothing_extra(repo, tmp_path, capsys):
    external = tmp_path / "outside"
    external.mkdir()
    (external / "loop_1.log").write_text("output\n")
    (repo / "code.py").write_text("changed\n")

    git_ops.commit_checkpoint_changes("phase0: built", log_dir=external)

    out = capsys.readouterr().out
    assert "WARNING" in out and "outside the repository" in out
    assert _committed_files(repo) == {"code.py"}


def test_external_log_dir_warns_only_once_per_run(repo, tmp_path, capsys):
    """A multi-checkpoint run must not repeat the same warning on every commit."""
    external = tmp_path / "outside-repeat"
    external.mkdir()

    for n, msg in enumerate(["phase0: built", "phase0: approved"]):
        (repo / "code.py").write_text(f"change {n}\n")
        git_ops.commit_checkpoint_changes(msg, log_dir=external)

    assert capsys.readouterr().out.count("outside the repository") == 1


def test_log_dir_containing_spaces_is_still_exempt(repo):
    """git quotes such paths in porcelain output; the filter must see through it."""
    logs = repo / "my logs"
    logs.mkdir()
    (logs / "loop_1.log").write_text("output\n")

    git_ops.require_clean_worktree(log_dir=logs)


def test_external_log_dir_does_not_exempt_worktree_changes(repo, tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    (repo / "code.py").write_text("uncommitted edit\n")

    with pytest.raises(git_ops.GitError):
        git_ops.require_clean_worktree(log_dir=external)


def test_repository_root_as_log_dir_is_rejected(repo):
    """The repository root cannot be used as log_dir, as that would bypass all
    security checks by matching every file in the working tree."""
    (repo / "code.py").write_text("uncommitted edit\n")

    # Attempting to use repo root as log_dir must fail in require_clean_worktree
    with pytest.raises(
        git_ops.GitError,
        match="Log directory cannot be the repository root itself",
    ):
        git_ops.require_clean_worktree(log_dir=repo)

    # It must also fail in commit_checkpoint_changes
    (repo / "code.py").write_text("some change\n")
    with pytest.raises(
        git_ops.GitError,
        match="Log directory cannot be the repository root itself",
    ):
        git_ops.commit_checkpoint_changes("test: change", log_dir=repo)


# ---------------------------------------------------------------------------
# Run lifecycle commits, driven through run_loop against real git
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider(Provider):
    name: str = "fake"
    binary: str = "fake"
    danger_env: str = "ALLOW_DANGEROUS_FAKE"
    on_call: Optional[Callable[[str], int]] = None
    prompts: list[str] = field(default_factory=list)

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover
        return [self.binary, prompt]

    def run(self, prompt: str, timeout: int, audit_level: str = "full") -> int:  # type: ignore[override]
        self.prompts.append(prompt)
        return self.on_call(prompt) if self.on_call else 0

    def preflight(self) -> None:
        return


def _plan(repo: Path, status: str = "pending") -> Path:
    p = repo / "plan_checkpoints.json"
    p.write_text(
        json.dumps(
            {
                "plan_file": "docs/plan.md",
                "branch": "work",
                "project": {
                    "test_cmd": "pytest -q",
                    "lint_cmd": "ruff check .",
                    "verify_in_review": True,
                },
                "checkpoints": [
                    {
                        "id": "phase0",
                        "name": "Setup",
                        "status": status,
                        "scope": "Bootstrap",
                        "exit_criteria": ["A"],
                        "attempts": 0,
                        "review_notes": "",
                    }
                ],
            },
            indent=2,
        )
    )
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "add plan", cwd=repo)
    return p


def _subjects(repo: Path) -> list[str]:
    return _git("log", "--format=%s", cwd=repo).stdout.splitlines()


def _prepare_logs(repo: Path) -> Path:
    logs = repo / "logs"
    logs.mkdir()
    (logs / "loop_1.log").write_text("run output\n")
    return logs


def test_completion_commits_audit_tail(repo, monkeypatch):
    """The closing commit is what flushes the log tail written after the last
    checkpoint commit — without it those bytes never reach git."""
    state_path = _plan(repo)
    logs = _prepare_logs(repo)
    log = logs / "loop_1.log"

    # Model the tee: output keeps being appended after every commit, so each
    # commit leaves a fresh tail behind for the next one to pick up.
    real_commit = git_ops.commit_checkpoint_changes

    def commit_then_keep_logging(message, log_dir=None):
        real_commit(message, log_dir=log_dir)
        with log.open("a") as fh:
            fh.write(f"output after {message}\n")

    monkeypatch.setattr(
        orchestrator.git_ops, "commit_checkpoint_changes", commit_then_keep_logging
    )

    def dev(_p):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def rev(_p):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    run_loop(
        state_path=state_path,
        developer=FakeProvider(name="dev", on_call=dev),
        reviewer=FakeProvider(name="rev", on_call=rev),
        max_review_attempts=3,
        timeout=30,
        log_dir=logs,
    )

    assert _subjects(repo)[0] == "audit: completed run"
    assert "logs/loop_1.log" in _tracked_files(repo)
    # The tail written after "phase0: approved" made it into the final commit.
    committed_log = _git("show", "HEAD:logs/loop_1.log", cwd=repo).stdout
    assert "output after phase0: approved" in committed_log


def test_halt_commits_state_before_raising(repo):
    state_path = _plan(repo)
    logs = _prepare_logs(repo)

    def dev(_p):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def rev(_p):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "still wrong")
        s.save()
        return 0

    with pytest.raises(LoopHalted):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev),
            reviewer=FakeProvider(name="rev", on_call=rev),
            max_review_attempts=2,
            timeout=30,
            log_dir=logs,
        )

    assert _subjects(repo)[0] == "audit: halted"
    assert _git("status", "--porcelain", cwd=repo).stdout.strip() == ""


def test_unexpected_failure_commits_state_before_raising(repo):
    state_path = _plan(repo)
    logs = _prepare_logs(repo)

    def dev(_p):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev),
            reviewer=FakeProvider(name="rev"),
            max_review_attempts=3,
            timeout=30,
            log_dir=logs,
        )

    assert _subjects(repo)[0] == "audit: run failed"


def test_fully_resumed_run_still_reports_completion(repo, capsys):
    """A run with nothing left to do must still print the completion banner."""
    state_path = _plan(repo, status="approved")
    logs = _prepare_logs(repo)

    run_loop(
        state_path=state_path,
        developer=FakeProvider(name="dev", on_call=lambda _p: pytest.fail("no dev")),
        reviewer=FakeProvider(name="rev", on_call=lambda _p: pytest.fail("no rev")),
        max_review_attempts=3,
        timeout=30,
        log_dir=logs,
    )

    assert "All checkpoints approved" in capsys.readouterr().out
    assert _subjects(repo)[0] == "audit: completed run"


def test_audit_commit_failure_does_not_mask_original_error(repo, monkeypatch):
    """If the closing commit itself fails, the real exception must survive."""
    state_path = _plan(repo)
    logs = _prepare_logs(repo)

    def boom(*_a, **_k):
        raise git_ops.GitError("git is unavailable")

    monkeypatch.setattr(orchestrator.git_ops, "commit_checkpoint_changes", boom)

    def dev(_p):
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev),
            reviewer=FakeProvider(name="rev"),
            max_review_attempts=3,
            timeout=30,
            log_dir=logs,
        )


# ---------------------------------------------------------------------------
# --audit-level end to end: real subprocess agent, real tee, real commit.
#
# This is the strongest check available: it drives the exact path a secret
# would actually take — a real child process prints it, Provider.run's pump
# streams it through the real tee, and the result lands in a real git commit
# — rather than asserting on any single layer in isolation.
# ---------------------------------------------------------------------------


@dataclass
class _SecretPrintingProvider(Provider):
    """A real subprocess that prints a secret and drives checkpoint state."""

    name: str = "leaky"
    binary: str = sys.executable
    danger_env: str = "ALLOW_DANGEROUS_LEAKY"
    secret: str = "AKIAABCDEFGHIJKLMNOP"
    state_path: Path = None  # type: ignore[assignment]
    new_status: str = "built"

    def build_argv(self, prompt: str) -> list[str]:
        script = (
            f"print('the api key is {self.secret}')\n"
            "import json\n"
            f"p = r'{self.state_path}'\n"
            "d = json.load(open(p))\n"
            f"d['checkpoints'][0]['status'] = '{self.new_status}'\n"
            "json.dump(d, open(p, 'w'), indent=2)\n"
        )
        return [sys.executable, "-u", "-c", script]

    def preflight(self) -> None:
        return


@pytest.fixture
def _restore_stdout():
    """tee_stdout_to permanently rebinds sys.stdout; undo that after the test."""
    real_stdout, real_stderr = sys.stdout, sys.stderr
    yield
    sys.stdout, sys.stderr = real_stdout, real_stderr


@pytest.mark.parametrize(
    "level,secret_should_survive",
    [("full", True), ("redacted", False), ("off", False)],
)
def test_audit_level_controls_whether_secret_reaches_the_committed_log(
    repo, _restore_stdout, level, secret_should_survive
):
    state_path = _plan(repo, status="pending")
    log_dir = repo / "logs"
    log_path = safety.open_log_file(log_dir)
    safety.tee_stdout_to(log_path)

    dev = _SecretPrintingProvider(state_path=state_path, new_status="built")
    rev = _SecretPrintingProvider(state_path=state_path, new_status="approved")

    run_loop(
        state_path=state_path,
        developer=dev,
        reviewer=rev,
        max_review_attempts=3,
        timeout=30,
        log_dir=log_dir,
        audit_level=level,
    )

    rel_log = log_path.relative_to(repo)
    committed = _git("show", f"HEAD:{rel_log.as_posix()}", cwd=repo).stdout

    assert (dev.secret in committed) == secret_should_survive
    if not secret_should_survive:
        # The run must still be visible in the log — just without the secret.
        assert "AGENT_TRACE" in committed
