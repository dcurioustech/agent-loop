"""Git operations the orchestrator relies on."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .state import PROTECTED_BRANCHES


class GitError(RuntimeError):
    pass


def _log_commit_statement(*, message: str, status: str, commit_hash: str | None) -> None:
    """Print a commit outcome that is mirrored into the active loop log."""
    fields = {
        "commit_hash": commit_hash,
        "event": "COMMIT_STATEMENT",
        "message": message,
        "status": status,
    }
    print(
        f"[agent-loop] COMMIT_STATEMENT {json.dumps(fields, sort_keys=True)}",
        flush=True,
    )


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


def _relative_log_prefix(log_dir: Path | None) -> str | None:
    """Return the repo-relative ``logs/`` prefix, or None if not inside the repo."""
    if log_dir is None:
        return None
    try:
        rel = log_dir.resolve().relative_to(repo_root().resolve())
    except (ValueError, GitError):
        return None
    return f"{rel}/" if str(rel) != "." else ""


def require_clean_worktree(log_dir: Path | None = None) -> None:
    """Refuse to start on a dirty worktree, tolerating the loop's own log files.

    Logs are committed as audit artifacts, so a log file from a previous run is
    both *tracked* and *modified* by the time the next run starts: the tee keeps
    appending after the final commit of the run that created it. Untracked logs
    (a brand new run) and modified logs (that trailing tail) are therefore both
    expected, and neither should block the loop.
    """
    res = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]

    prefix = _relative_log_prefix(log_dir)
    if prefix is not None:
        # Porcelain v1 status codes are two columns followed by a space, so the
        # path starts at index 3. Paths containing spaces or other special
        # characters come back double-quoted.
        lines = [ln for ln in lines if not ln[3:].lstrip('"').startswith(prefix)]

    if lines:
        raise GitError(
            "Worktree must be clean before running the loop.\n"
            "Outstanding changes:\n" + "\n".join(lines)
        )


#: Log directories already warned about, so a multi-checkpoint run says it once.
_WARNED_EXTERNAL_LOG_DIRS: set[str] = set()


def commit_checkpoint_changes(message: str, log_dir: Path) -> None:
    if _relative_log_prefix(log_dir) is None:
        key = str(log_dir)
        if key not in _WARNED_EXTERNAL_LOG_DIRS:
            _WARNED_EXTERNAL_LOG_DIRS.add(key)
            print(
                f"[agent-loop] WARNING: log directory {log_dir} is outside the "
                "repository; run logs will not be committed as audit artifacts.",
                flush=True,
            )

    _run(["git", "add", "-A"])

    cached = _run(["git", "diff", "--cached", "--quiet"], check=False)
    if cached.returncode == 0:
        _log_commit_statement(message=message, status="no_changes", commit_hash=None)
        return

    _run(["git", "commit", "-m", message, "--quiet"])
    commit_hash = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    _log_commit_statement(message=message, status="committed", commit_hash=commit_hash)
