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
