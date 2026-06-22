"""Git operations the orchestrator relies on."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .state import PROTECTED_BRANCHES


class GitError(RuntimeError):
    pass


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def repo_root() -> Path:
    res = _run(["git", "rev-parse", "--show-toplevel"], check=False)
    if res.returncode != 0:
        raise GitError("Not a git repository")
    return Path(res.stdout.strip())


def current_branch() -> str:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def ensure_branch(branch: str) -> None:
    if branch in PROTECTED_BRANCHES:
        raise GitError(f"Refusing protected branch: {branch}")
    repo_root()  # raises if not a repo
    if current_branch() == branch:
        return
    exists = _run(["git", "rev-parse", "--verify", branch], check=False).returncode == 0
    if exists:
        _run(["git", "checkout", branch])
    else:
        _run(["git", "checkout", "-b", branch])


def require_clean_worktree() -> None:
    res = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    if res.stdout.strip():
        raise GitError(
            "Worktree must be clean before running the loop.\n"
            f"Outstanding changes:\n{res.stdout}"
        )


def commit_checkpoint_changes(message: str, log_dir: Path) -> None:
    root = repo_root()
    log_dir = log_dir.resolve()
    try:
        log_dir.relative_to(root)
        inside = True
    except ValueError:
        inside = False

    if inside:
        _run(
            [
                "git",
                "add",
                "-A",
                "--",
                ".",
                f":(exclude){log_dir.relative_to(root)}",
            ]
        )
    else:
        _run(["git", "add", "-A"])

    cached = _run(["git", "diff", "--cached", "--quiet"], check=False)
    if cached.returncode == 0:
        print(f"[agent-loop] no commit needed: {message}", flush=True)
        return

    _run(["git", "commit", "-m", message, "--quiet"])
