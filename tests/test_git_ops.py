"""Tests for git commit statements emitted by the loop."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_loop import git_ops


def _commit_statement(captured_output: str) -> dict:
    line = next(
        line
        for line in captured_output.splitlines()
        if line.startswith("[agent-loop] COMMIT_STATEMENT ")
    )
    return json.loads(line.removeprefix("[agent-loop] COMMIT_STATEMENT "))


def test_commit_checkpoint_changes_logs_commit_message_and_hash(monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    commands: list[list[str]] = []

    monkeypatch.setattr(git_ops, "repo_root", lambda: root)

    def fake_run(args, check=True):
        commands.append(args)
        if args[:4] == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(args, 1)
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc123\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.commit_checkpoint_changes("phase2: built", log_dir=root / "logs")

    statement = _commit_statement(capsys.readouterr().out)
    assert statement == {
        "commit_hash": "abc123",
        "event": "COMMIT_STATEMENT",
        "message": "phase2: built",
        "status": "committed",
    }
    assert ["git", "commit", "-m", "phase2: built", "--quiet"] in commands


def test_commit_checkpoint_changes_logs_when_no_commit_is_required(monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()

    monkeypatch.setattr(git_ops, "repo_root", lambda: root)

    def fake_run(args, check=True):
        if args[:4] == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.commit_checkpoint_changes("phase2: approved", log_dir=root / "logs")

    statement = _commit_statement(capsys.readouterr().out)
    assert statement == {
        "commit_hash": None,
        "event": "COMMIT_STATEMENT",
        "message": "phase2: approved",
        "status": "no_changes",
    }
