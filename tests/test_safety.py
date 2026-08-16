"""Tests for safety module: default_log_dir, open_log_file, lockfile, danger gates."""
import os
import re
import subprocess
from pathlib import Path

import pytest

from agent_loop import git_ops, safety


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True)
    return path


def test_default_log_dir_resolves_to_repo_root_logs(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    log_dir = safety.default_log_dir()
    assert log_dir == repo / "logs"


def test_default_log_dir_resolves_regardless_of_caller_cwd(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    nested = repo / "sub" / "dir" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    log_dir = safety.default_log_dir()
    assert log_dir == repo / "logs"


def test_default_log_dir_raises_outside_git_repo(tmp_path, monkeypatch):
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()
    monkeypatch.chdir(non_repo)

    with pytest.raises(git_ops.GitError, match="Not a git repository"):
        safety.default_log_dir()


def test_open_log_file_creates_directory(tmp_path):
    log_dir = tmp_path / "logs" / "nested"
    assert not log_dir.exists()

    log_path = safety.open_log_file(log_dir)
    assert log_dir.is_dir()
    assert log_path.parent == log_dir
    assert re.match(r"^loop_\d{8}_\d{6}\.log$", log_path.name)


def test_tee_stdout_to_creates_parent_dir_and_writes(tmp_path):
    log_dir = tmp_path / "logs" / "tee_test"
    log_file = log_dir / "test.log"
    assert not log_dir.exists()

    orig_stdout = safety.sys.stdout
    orig_stderr = safety.sys.stderr
    try:
        safety.tee_stdout_to(log_file)
        assert log_dir.is_dir()
        print("hello via tee")
    finally:
        safety.sys.stdout = orig_stdout
        safety.sys.stderr = orig_stderr

    assert "hello via tee\n" in log_file.read_text()
