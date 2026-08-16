"""Tests for git_ops module: repo_root, ensure_branch, clean worktree, commit_checkpoint_changes."""
import subprocess
from pathlib import Path

import pytest

from agent_loop import git_ops


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True)
    return path


def test_repo_root_resolves_from_root_and_subdirs(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    assert git_ops.repo_root() == repo

    nested = repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert git_ops.repo_root() == repo


def test_repo_root_raises_when_not_in_git(tmp_path, monkeypatch):
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()
    monkeypatch.chdir(non_repo)
    with pytest.raises(git_ops.GitError, match="Not a git repository"):
        git_ops.repo_root()


def test_commit_checkpoint_changes_stages_and_commits_logs(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    # Simulate code change
    (repo / "src.py").write_text("print('hello')")
    # Simulate generated log file in repository logs/ directory
    logs_dir = repo / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "loop_20260816_120000.log"
    log_file.write_text("agent trace log content\n")

    git_ops.commit_checkpoint_changes("phase0: built", log_dir=logs_dir)

    # Verify both src.py and logs/loop_...log are tracked and in git commit
    ls_files = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert "src.py" in ls_files
    assert "logs/loop_20260816_120000.log" in ls_files

    # Verify commit log contains the commit
    git_log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert git_log == "phase0: built"


def test_commit_checkpoint_changes_from_subdir_stages_logs(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    subdir = repo / "sub"
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    logs_dir = repo / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "loop_test.log"
    log_file.write_text("sub-dir run log\n")

    git_ops.commit_checkpoint_changes("phase0: built")

    ls_files = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert "logs/loop_test.log" in ls_files


def test_commit_checkpoint_changes_no_op_when_clean(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    git_ops.commit_checkpoint_changes("no op commit")

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert head_before == head_after
    assert "no commit needed: no op commit" in capsys.readouterr().out


def test_commit_checkpoint_changes_logs_attempt_and_commit_id(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    (repo / "new_file.py").write_text("x = 1\n")
    res = git_ops.commit_checkpoint_changes("phase0: built")

    assert res.committed is True
    assert res.commit_id is not None
    assert res.message == "phase0: built"

    head_short = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert res.commit_id == head_short

    out = capsys.readouterr().out
    assert "[agent-loop] [commit] attempt: phase0: built" in out
    assert f"[agent-loop] [commit] outcome: commit {head_short} (phase0: built)" in out


def test_commit_checkpoint_changes_logs_no_change_explicit_outcome(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    res = git_ops.commit_checkpoint_changes("phase0: built")

    assert res.committed is False
    assert res.commit_id is None
    assert res.message == "phase0: built"

    out = capsys.readouterr().out
    assert "[agent-loop] [commit] attempt: phase0: built" in out
    assert "[agent-loop] no commit needed: phase0: built" in out
    assert "[agent-loop] [commit] outcome: no changes to commit (phase0: built)" in out


def test_commit_workflow_preserves_outcomes_across_sequential_commits(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    logs_dir = repo / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "loop_run.log"

    # Step 1: Built
    (repo / "file1.py").write_text("step 1\n")
    log_file.write_text("[agent-loop] [developer] [phase0] initial build finished\n")
    res1 = git_ops.commit_checkpoint_changes("phase0: built", log_dir=logs_dir)
    assert res1.committed is True

    # Step 2: Next step records previous outcome in the log file
    with open(log_file, "a") as f:
        f.write(f"[agent-loop] [commit] outcome: commit {res1.commit_id} (phase0: built)\n")
        f.write("[agent-loop] [reviewer] [phase0] review finished\n")

    (repo / "file2.py").write_text("step 2\n")
    res2 = git_ops.commit_checkpoint_changes("phase0: approved", log_dir=logs_dir)
    assert res2.committed is True

    # Check that in the second commit, the log file contains the commit outcome of the first commit
    log_content_in_git = subprocess.run(
        ["git", "show", f"HEAD:logs/loop_run.log"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert f"[agent-loop] [commit] outcome: commit {res1.commit_id} (phase0: built)" in log_content_in_git
    assert "[agent-loop] [reviewer] [phase0] review finished" in log_content_in_git


def test_end_to_end_real_git_repo_audit_trail_and_clean_worktree(tmp_path, monkeypatch):
    import json
    from agent_loop import safety
    from agent_loop.orchestrator import run_loop
    from agent_loop.providers.base import Provider
    from agent_loop.state import load_state

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    plan_path = repo / "plan_checkpoints.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_file": "docs/plan.md",
                "branch": "feature-x",
                "checkpoints": [
                    {
                        "id": "phase0",
                        "name": "Setup",
                        "status": "pending",
                        "scope": "Bootstrap things",
                        "exit_criteria": ["A"],
                        "attempts": 0,
                        "review_notes": "",
                    }
                ],
            },
            indent=2,
        )
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, capture_output=True)

    log_dir = repo / "logs"
    log_file = log_dir / "loop_run.log"
    safety.tee_stdout_to(log_file)

    class DevProvider(Provider):
        name = "fake_dev"
        binary = "fake_dev"
        danger_env = "ALLOW_DANGEROUS_FAKE"

        def build_argv(self, prompt: str) -> list[str]:
            return [self.binary, prompt]

        def preflight(self) -> None:
            return None

        def run(self, prompt: str, timeout: int) -> int:
            (repo / "feature.py").write_text("print('feature built')\n")
            s = load_state(plan_path)
            s.set_field("phase0", "status", "built")
            s.set_field("phase0", "review_notes", "Added feature.py")
            s.save()
            return 0

    class RevProvider(Provider):
        name = "fake_rev"
        binary = "fake_rev"
        danger_env = "ALLOW_DANGEROUS_FAKE"

        def build_argv(self, prompt: str) -> list[str]:
            return [self.binary, prompt]

        def preflight(self) -> None:
            return None

        def run(self, prompt: str, timeout: int) -> int:
            s = load_state(plan_path)
            s.set_field("phase0", "status", "approved")
            s.set_field("phase0", "review_notes", "Verified feature.py")
            s.save()
            return 0

    run_loop(
        state_path=plan_path,
        developer=DevProvider(),
        reviewer=RevProvider(),
        max_review_attempts=3,
        timeout=30,
        log_dir=log_dir,
    )

    commits = subprocess.run(
        ["git", "log", "--pretty=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert commits[0] == "audit: completed run"
    assert commits[1] == "phase0: approved"
    assert commits[2] == "phase0: built"

    assert log_file.exists()
    ls_files = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert "logs/loop_run.log" in ls_files

    log_text = log_file.read_text()
    assert "[agent-loop] [developer] [phase0] starting initial build" in log_text
    assert "[agent-loop] [developer] [phase0] initial build finished (exit code: 0)" in log_text
    assert "[agent-loop] [developer] [phase0] notes:\nAdded feature.py" in log_text
    assert "[agent-loop] [commit] attempt: phase0: built" in log_text
    assert "[agent-loop] [reviewer] [phase0] starting review (attempt 1/3)" in log_text
    assert "[agent-loop] [reviewer] [phase0] review finished (exit code: 0)" in log_text
    assert "[agent-loop] [reviewer] [phase0] approval comment (attempt 1/3):\nVerified feature.py" in log_text
    assert "[agent-loop] [commit] attempt: phase0: approved" in log_text


def test_end_to_end_real_git_repo_halted_run_commits_audit_trail(tmp_path, monkeypatch):
    import json
    from agent_loop import safety
    from agent_loop.orchestrator import LoopHalted, run_loop
    from agent_loop.providers.base import Provider
    from agent_loop.state import load_state

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    plan_path = repo / "plan_checkpoints.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_file": "docs/plan.md",
                "branch": "feature-x",
                "checkpoints": [
                    {
                        "id": "phase0",
                        "name": "Setup",
                        "status": "pending",
                        "scope": "Bootstrap things",
                        "exit_criteria": ["A"],
                        "attempts": 0,
                        "review_notes": "",
                    }
                ],
            },
            indent=2,
        )
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, capture_output=True)

    log_dir = repo / "logs"
    log_file = log_dir / "loop_run.log"
    safety.tee_stdout_to(log_file)

    class DevProvider(Provider):
        name = "fake_dev"
        binary = "fake_dev"
        danger_env = "ALLOW_DANGEROUS_FAKE"

        def build_argv(self, prompt: str) -> list[str]:
            return [self.binary, prompt]

        def preflight(self) -> None:
            return None

        def run(self, prompt: str, timeout: int) -> int:
            s = load_state(plan_path)
            s.set_field("phase0", "status", "built")
            s.set_field("phase0", "review_notes", "v1")
            s.save()
            return 0

    class RevProvider(Provider):
        name = "fake_rev"
        binary = "fake_rev"
        danger_env = "ALLOW_DANGEROUS_FAKE"

        def build_argv(self, prompt: str) -> list[str]:
            return [self.binary, prompt]

        def preflight(self) -> None:
            return None

        def run(self, prompt: str, timeout: int) -> int:
            s = load_state(plan_path)
            s.set_field("phase0", "status", "built")
            s.set_field("phase0", "review_notes", "Criteria not met")
            s.save()
            return 0

    with pytest.raises(LoopHalted):
        run_loop(
            state_path=plan_path,
            developer=DevProvider(),
            reviewer=RevProvider(),
            max_review_attempts=1,
            timeout=30,
            log_dir=log_dir,
        )

    commits = subprocess.run(
        ["git", "log", "--pretty=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert commits[0] == "phase0: halted"
    assert commits[1] == "phase0: built"

    log_in_git = subprocess.run(
        ["git", "show", "HEAD:logs/loop_run.log"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "[agent-loop] [reviewer] [phase0] review comment (attempt 1/1):\nCriteria not met" in log_in_git
    assert "[agent-loop] [phase0] halted: phase0 not approved after 1 attempts" in log_in_git


