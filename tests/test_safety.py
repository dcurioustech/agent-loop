"""Tests for log-directory resolution."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent_loop import safety


def test_default_log_dir_honors_log_dir_environment_variable(monkeypatch, tmp_path):
    configured = tmp_path / "configured-logs"
    monkeypatch.setenv("LOG_DIR", str(configured))

    assert safety.default_log_dir() == configured


def test_default_log_dir_uses_logs_directory_at_git_repository_root(monkeypatch, tmp_path):
    """An unset LOG_DIR resolves relative to the current Git repository."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert safety.default_log_dir() == tmp_path / "logs"


def test_default_log_dir_falls_back_to_relative_logs_outside_git_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert safety.default_log_dir() == Path("logs")
