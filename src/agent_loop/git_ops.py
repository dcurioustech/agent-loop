"""Git operations the orchestrator relies on."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .state import PROTECTED_BRANCHES


class GitError(RuntimeError):
    pass


@dataclass
class CommitResult:
    committed: bool
    commit_id: str | None
    message: str


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


def require_clean_worktree(log_dir: Path | None = None) -> None:
    res = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    lines = [ln for ln in res.stdout.strip().splitlines() if ln]
    if not lines:
        return

    if log_dir is not None:
        try:
            root = repo_root()
            rel_log_dir = log_dir.resolve().relative_to(root.resolve())
            prefix = f"{rel_log_dir}/"
            lines = [
                ln
                for ln in lines
                if not (ln.startswith("?? ") and ln[3:].startswith(prefix))
            ]
        except (ValueError, GitError):
            pass

    if lines:
        raise GitError(
            "Worktree must be clean before running the loop.\n"
            f"Outstanding changes:\n" + "\n".join(lines)
        )



def commit_checkpoint_changes(
    message: str, log_dir: Path | None = None
) -> CommitResult:
    print(f"[agent-loop] [commit] attempt: {message}", flush=True)
    _run(["git", "add", "-A"])

    cached = _run(["git", "diff", "--cached", "--quiet"], check=False)
    if cached.returncode == 0:
        print(f"[agent-loop] no commit needed: {message}", flush=True)
        print(
            f"[agent-loop] [commit] outcome: no changes to commit ({message})",
            flush=True,
        )
        return CommitResult(committed=False, commit_id=None, message=message)

    _run(["git", "commit", "-m", message, "--quiet"])
    commit_id = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    print(
        f"[agent-loop] [commit] outcome: commit {commit_id} ({message})",
        flush=True,
    )
    return CommitResult(committed=True, commit_id=commit_id, message=message)

