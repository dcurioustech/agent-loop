"""Lockfile, log directory, and danger-flag gating."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .providers.base import Provider


class LockHeld(RuntimeError):
    pass


class DangerGateError(RuntimeError):
    pass


@contextmanager
def lockfile(path: Path):
    try:
        path.mkdir(parents=False, exist_ok=False)
    except FileExistsError as e:
        raise LockHeld(
            f"Another loop appears to be running (lock: {path}). "
            "Remove the lock only if you are sure no loop process is active."
        ) from e
    try:
        yield
    finally:
        try:
            path.rmdir()
        except OSError:
            pass


def open_log_file(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"loop_{stamp}.log"


def require_danger_gates(providers: list[Provider]) -> None:
    """Refuse to launch if any assigned provider's write-mode env isn't set.

    Mirrors the bash script's refusal for prompt-only Claude — every coding-agent
    CLI in v1 expects yolo/danger mode for unattended runs.
    """
    blocked = [p for p in providers if not p.dangerous_enabled]
    if not blocked:
        return

    lines = [
        "These providers are assigned to a role that writes files, but their",
        "write-mode env gate is not set:",
        "",
    ]
    for p in blocked:
        lines.append(f"  {p.name:8s}  set {p.danger_env}=1")
    lines.extend(
        [
            "",
            "Unattended runs would otherwise block on a permission prompt.",
            "Re-run with the missing env vars exported.",
        ]
    )
    raise DangerGateError("\n".join(lines))


def tee_stdout_to(log_path: Path) -> None:
    """Mirror stdout+stderr to a log file in addition to the terminal."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", buffering=1)

    class _Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, data):
            for s in self.streams:
                s.write(data)

        def flush(self):
            for s in self.streams:
                s.flush()

    sys.stdout = _Tee(sys.stdout, log_fh)
    sys.stderr = _Tee(sys.stderr, log_fh)


def default_log_dir() -> Path:
    return Path(os.environ.get("LOG_DIR", "/tmp/agent_loop_logs"))


def default_lock_path() -> Path:
    return Path(os.environ.get("LOCK_DIR", "/tmp/agent_loop.lock"))
