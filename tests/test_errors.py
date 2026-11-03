"""Tests for user-facing error messages (`agent_loop.errors`) and their CLI wiring."""
from __future__ import annotations

import errno
import json
import subprocess

import pytest

from agent_loop import cli, errors, git_ops
from agent_loop.orchestrator import LoopHalted
from agent_loop.plan_init import PlanOutputParseError, ProviderExecutionError
from agent_loop.providers.base import ProviderError
from agent_loop.safety import LockHeld
from agent_loop.state import InvalidPlanState, ProtectedBranchError, load_state


# ---------------------------------------------------------------------------
# format_error / describe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, prefix, hint_fragment",
    [
        (InvalidPlanState("State file not found: x.json"), "error", "agent-loop init"),
        (ProtectedBranchError("Refusing protected target branch: main"), "error", "feature branch"),
        (git_ops.GitError("Not a git repository"), "error", "git init"),
        (git_ops.GitError("Worktree must be clean before running the loop."), "error", "stash"),
        (ProviderError("Missing CLI for provider 'x': 'x' not on PATH."), "error", "Install"),
        (ProviderExecutionError("claude timed out after 5s while generating a plan"), "error", "--timeout"),
        (PlanOutputParseError("Provider produced no output"), "error", "--provider"),
        (LockHeld("Another loop appears to be running"), "error", "lock"),
        (LoopHalted("phase1 not approved after 3 attempts"), "halted", "--max-review-attempts"),
        (KeyboardInterrupt(), "stopped", "resume"),
        (PermissionError(errno.EACCES, "Permission denied", "/x"), "error", "permission"),
    ],
)
def test_known_errors_get_a_prefix_and_actionable_hint(exc, prefix, hint_fragment):
    text = errors.format_error(exc)
    first, *rest = text.splitlines()
    assert first.startswith(f"{prefix}: ")
    assert rest, "expected at least one hint line"
    assert all(line.startswith("  hint: ") for line in rest)
    assert hint_fragment.lower() in text.lower()


def test_git_errors_are_labelled_as_git():
    assert errors.format_error(git_ops.GitError("boom")).startswith("error: git: boom")


def test_os_error_shows_reason_and_path_not_errno_tuple():
    exc = PermissionError(errno.EACCES, "Permission denied", "/root/plan.json")
    first = errors.format_error(exc).splitlines()[0]
    assert first == "error: Permission denied: /root/plan.json"
    assert "Errno" not in first


def test_context_is_prepended():
    exc = OSError(errno.ENOSPC, "No space left on device")
    text = errors.format_error(exc, context="unable to write plan.json")
    assert text.startswith("error: unable to write plan.json: No space left on device")
    assert "disk space" in text


def test_unknown_exception_points_at_debug_switch():
    text = errors.format_error(ZeroDivisionError("division by zero"))
    assert text.startswith("error: unexpected error: ZeroDivisionError: division by zero")
    assert errors.DEBUG_ENV in text


def test_report_includes_traceback_only_in_debug_mode(monkeypatch, capsys):
    try:
        raise ValueError("kaboom")
    except ValueError as e:
        exc = e

    monkeypatch.delenv(errors.DEBUG_ENV, raising=False)
    errors.report(exc)
    assert "Traceback" not in capsys.readouterr().err

    monkeypatch.setenv(errors.DEBUG_ENV, "1")
    errors.report(exc)
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "unexpected error: ValueError: kaboom" in err


def test_fail_prints_message_and_hints(capsys):
    assert errors.fail("bad thing", "do this", "or that") == 2
    assert capsys.readouterr().err.splitlines() == [
        "error: bad thing",
        "  hint: do this",
        "  hint: or that",
    ]


# ---------------------------------------------------------------------------
# Underlying messages
# ---------------------------------------------------------------------------


def test_invalid_json_reports_line_and_column(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text('{\n  "branch": "x",\n  oops\n}')
    with pytest.raises(InvalidPlanState, match=r"line 3, column 3"):
        load_state(p)


def test_unreadable_state_file_is_invalid_plan_state(tmp_path):
    p = tmp_path / "plan.json"
    p.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(InvalidPlanState, match="Cannot read state file"):
        load_state(p)


def test_failed_git_command_surfaces_git_stderr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    with pytest.raises(git_ops.GitError, match=r"'git checkout' failed .*nope"):
        git_ops._run(["git", "checkout", "nope"])


def test_missing_git_binary_is_a_git_error(monkeypatch):
    def missing(*_a, **_k):
        raise FileNotFoundError(errno.ENOENT, "No such file", "git")

    monkeypatch.setattr(git_ops.subprocess, "run", missing)
    with pytest.raises(git_ops.GitError, match="not installed or not on PATH"):
        git_ops.repo_root()


# ---------------------------------------------------------------------------
# CLI wiring: no tracebacks reach the user
# ---------------------------------------------------------------------------


def test_cli_missing_state_file_gives_hint(tmp_path, capsys):
    rc = cli.main(["status", "--state", str(tmp_path / "missing.json")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error: State file not found" in err
    assert "hint:" in err and "agent-loop init" in err


def test_cli_unexpected_exception_is_reported_not_raised(monkeypatch, capsys):
    def boom(_args):
        raise RuntimeError("something odd")

    monkeypatch.setitem(cli._COMMANDS, "status", boom)
    monkeypatch.delenv(errors.DEBUG_ENV, raising=False)
    rc = cli.main(["status"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unexpected error: RuntimeError: something odd" in err
    assert "Traceback" not in err


def test_cli_ctrl_c_exits_130_with_resume_hint(monkeypatch, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setitem(cli._COMMANDS, "status", interrupted)
    rc = cli.main(["status"])
    assert rc == errors.EXIT_INTERRUPTED
    err = capsys.readouterr().err
    assert "interrupted" in err and "resume" in err


def test_cli_run_reports_git_error_from_loop(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan_checkpoints.json"
    plan.write_text(
        json.dumps(
            {
                "plan_file": "p.md",
                "branch": "feature-x",
                "checkpoints": [
                    {
                        "id": "a",
                        "name": "n",
                        "status": "pending",
                        "scope": "s",
                        "exit_criteria": ["e"],
                        "attempts": 0,
                        "review_notes": "",
                    }
                ],
            }
        )
    )

    class _P:
        name = "fake"
        model = None
        auto_mode_enabled = True

        def preflight(self):
            pass

    def not_a_repo(**_k):
        raise git_ops.GitError("Not a git repository (or any parent directory)")

    monkeypatch.setattr(cli, "get_provider", lambda *_a: _P())
    monkeypatch.setattr(cli, "run_loop", not_a_repo)
    monkeypatch.setattr(cli.safety, "tee_stdout_to", lambda _p: None)
    monkeypatch.setenv("LOCK_DIR", str(tmp_path / "lock"))

    rc = cli.main(
        ["run", "--state", str(plan), "--log-dir", str(tmp_path / "logs")]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "error: git: Not a git repository" in err
    assert "git init" in err


# ---------------------------------------------------------------------------
# Reporting through an unwritable run log
# ---------------------------------------------------------------------------


class _FullDisk:
    name = "loop.log"

    def write(self, _data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self):
        raise OSError(errno.ENOSPC, "No space left on device")


class _Terminal:
    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data
        return len(data)

    def flush(self):
        pass


def test_report_survives_full_run_log_without_duplicating(monkeypatch):
    """Terminal copy already written, log copy fails: no raise, printed once."""
    from agent_loop.safety import Tee

    terminal = _Terminal()
    monkeypatch.setattr(errors.sys, "stderr", Tee(terminal, _FullDisk()))
    errors.report(git_ops.GitError("boom"))
    assert terminal.text.count("error: git: boom") == 1
    assert errors.fail("bad", "fix it") == 2
    assert "error: bad\n  hint: fix it\n" in terminal.text


def test_report_falls_back_to_process_stderr_when_stream_fails(monkeypatch):
    fallback = _Terminal()
    monkeypatch.setattr(errors.sys, "__stderr__", fallback)
    errors.report(git_ops.GitError("boom"), stream=_FullDisk())
    assert "error: git: boom" in fallback.text


def test_cli_returns_2_when_run_log_fills_up(tmp_path, monkeypatch):
    """The scenario from review: an error raised mid-run with the log tee'd to a full disk."""
    from agent_loop.safety import Tee

    terminal = _Terminal()
    plan = tmp_path / "plan_checkpoints.json"
    plan.write_text(
        json.dumps(
            {
                "plan_file": "p.md",
                "branch": "feature-x",
                "checkpoints": [
                    {
                        "id": "a",
                        "name": "n",
                        "status": "pending",
                        "scope": "s",
                        "exit_criteria": ["e"],
                        "attempts": 0,
                        "review_notes": "",
                    }
                ],
            }
        )
    )

    class _P:
        name = "fake"
        model = None
        auto_mode_enabled = True

        def preflight(self):
            pass

    def full_disk_tee(_path):
        monkeypatch.setattr(cli.sys, "stdout", Tee(terminal, _FullDisk()))
        monkeypatch.setattr(cli.sys, "stderr", Tee(terminal, _FullDisk()))

    def loop_fails(**_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(cli, "get_provider", lambda *_a: _P())
    monkeypatch.setattr(cli.safety, "tee_stdout_to", full_disk_tee)
    monkeypatch.setattr(cli, "run_loop", loop_fails)
    monkeypatch.setenv("LOCK_DIR", str(tmp_path / "lock"))

    # The banner print after tee setup also hits the full log; that must not
    # escape either.
    rc = cli.main(["run", "--state", str(plan), "--log-dir", str(tmp_path / "logs")])
    assert rc == 2
    assert "No space left on device" in terminal.text
