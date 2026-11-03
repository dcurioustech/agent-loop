"""Turn exceptions into short, actionable messages for the command line.

Every error the CLI reports goes through `report`, which prints::

    error: <what went wrong>
      hint: <what to do about it>

The message says *what* failed; the hint (when we can offer one) says *how to
fix it*. Hints are chosen from the exception type and, where the type alone is
too broad, from well-known phrases in its message. Anything we do not
recognise is still reported in one line rather than as a Python traceback;
set ``AGENT_LOOP_DEBUG=1`` to get the traceback back.
"""
from __future__ import annotations

import errno
import os
import sys
import traceback
from typing import List, Optional, TextIO, Tuple

from .audit import InvalidAuditLevel
from .git_ops import GitError
from .orchestrator import LoopHalted
from .plan_init import PlanOutputParseError, ProviderExecutionError, StateFileExistsError
from .providers.base import ProviderError
from .safety import AutoModeGateError, LockHeld
from .state import (
    CheckpointNotFound,
    DuplicateCheckpointError,
    InvalidPlanState,
    ProtectedBranchError,
)

DEBUG_ENV = "AGENT_LOOP_DEBUG"

#: Exit code for a run stopped with Ctrl-C (128 + SIGINT), as shells report it.
EXIT_INTERRUPTED = 130


def debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _hints_for_git(msg: str) -> List[str]:
    low = msg.lower()
    if "not a git repository" in low:
        return ["Run agent-loop from inside your project's git repository (or run 'git init' first)."]
    if "not installed or not on path" in low:
        return ["Install git and make sure it is on your PATH."]
    if "worktree must be clean" in low:
        return ["Commit or stash the changes listed above ('git stash -u'), then re-run."]
    if "log directory cannot be the repository root" in low:
        return ["Pass a subdirectory instead, e.g. '--log-dir logs' or LOG_DIR=logs."]
    if "protected branch" in low:
        return ["Set 'branch' in plan_checkpoints.json to a feature branch, e.g. 'feature/my-change'."]
    if "please tell me who you are" in low or "user.email" in low or "user.name" in low:
        return [
            "Configure a git identity: git config user.name \"Your Name\" && "
            "git config user.email you@example.com"
        ]
    if "would be overwritten by checkout" in low:
        return ["Commit or stash your local changes before switching branches."]
    if "index.lock" in low:
        return ["Another git process may be running. If not, delete .git/index.lock and retry."]
    return []


def _hints_for_plan_state(exc: InvalidPlanState) -> List[str]:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, ProtectedBranchError):
        return ["Set 'branch' in the state file to a feature branch, e.g. 'feature/my-change'."]
    if isinstance(exc, DuplicateCheckpointError):
        return ["Give every checkpoint a unique 'id' (e.g. phase0, phase1, ...)."]
    if "state file not found" in low:
        return [
            "Generate one with 'agent-loop init \"Describe your feature\"', "
            "or point at an existing file with --state PATH."
        ]
    if "not valid json" in low:
        return ["Fix the JSON syntax at the reported line/column, then run 'agent-loop validate'."]
    if "cannot read state file" in low:
        return ["Check that the file exists and that you have permission to read it."]
    return ["Run 'agent-loop validate' after fixing the state file; see README for the schema."]


def _hints_for_provider(msg: str) -> List[str]:
    low = msg.lower()
    if "not on path" in low:
        return ["Install that CLI (see the README's provider table) or pick another with --developer/--reviewer/--provider."]
    if "unknown provider" in low:
        return ["Choose one of the providers listed above."]
    return []


def _hints_for_provider_execution(msg: str) -> List[str]:
    low = msg.lower()
    if "timed out" in low:
        return ["Increase the limit with --timeout SECONDS, or simplify the feature description."]
    return [
        "Check that the CLI is logged in and works on its own, then retry.",
        "You can also try a different planner with --provider.",
    ]


def _hints_for_os_error(exc: OSError) -> List[str]:
    if exc.errno in (errno.EACCES, errno.EPERM):
        return ["Check the file/directory permissions, or choose a different location."]
    if exc.errno == errno.ENOSPC:
        return ["Free up disk space and retry."]
    if exc.errno == errno.ENOENT:
        return ["Check that the path exists."]
    if exc.errno == errno.EROFS:
        return ["The location is read-only; choose a writable location."]
    return []


def _describe_os_error(exc: OSError) -> str:
    reason = exc.strerror or str(exc)
    if exc.filename is not None:
        return f"{reason}: {exc.filename}"
    return reason


def describe(exc: BaseException) -> Tuple[str, List[str], str]:
    """Return ``(message, hints, prefix)`` for `exc`.

    ``prefix`` is the label printed before the message ("error", "halted", ...).
    """
    msg = str(exc).strip()

    if isinstance(exc, KeyboardInterrupt):
        return (
            "interrupted by user (Ctrl-C)",
            ["Progress so far is saved in the state file; re-run the same command to resume."],
            "stopped",
        )
    if isinstance(exc, LoopHalted):
        return (
            msg,
            [
                "Read the reviewer's 'review_notes' in the state file and the run log.",
                "Fix the code by hand or adjust the plan, then re-run to resume "
                "(or allow more rounds with --max-review-attempts N).",
            ],
            "halted",
        )
    if isinstance(exc, LockHeld):
        return (
            msg,
            ["If no other agent-loop is running, remove the lock directory and retry."],
            "error",
        )
    if isinstance(exc, AutoModeGateError):
        # The message already spells out exactly which variables to set.
        return msg, [], "error"
    if isinstance(exc, InvalidPlanState):
        return msg, _hints_for_plan_state(exc), "error"
    if isinstance(exc, CheckpointNotFound):
        return str(exc), ["Check the checkpoint ids in the state file."], "error"
    if isinstance(exc, GitError):
        return f"git: {msg}", _hints_for_git(msg), "error"
    if isinstance(exc, ProviderError):
        return msg, _hints_for_provider(msg), "error"
    if isinstance(exc, ProviderExecutionError):
        return msg, _hints_for_provider_execution(msg), "error"
    if isinstance(exc, PlanOutputParseError):
        return (
            f"could not understand the generated plan: {msg}",
            [
                "The planning CLI did not return the expected JSON. Retrying often "
                "helps; otherwise try another --provider or --model.",
            ],
            "error",
        )
    if isinstance(exc, StateFileExistsError):
        return msg, ["Use --force to overwrite it, or choose another path with --state."], "error"
    if isinstance(exc, InvalidAuditLevel):
        return msg, ["Fix or unset AGENT_LOOP_AUDIT_LEVEL, or pass --audit-level."], "error"
    if isinstance(exc, UnicodeDecodeError):
        return (
            "file is not valid UTF-8 text",
            ["Save the file as UTF-8 and retry."],
            "error",
        )
    if isinstance(exc, OSError):
        return _describe_os_error(exc), _hints_for_os_error(exc), "error"

    detail = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
    return (
        f"unexpected error: {detail}",
        [
            f"Re-run with {DEBUG_ENV}=1 for the full traceback, and please report it at "
            "https://github.com/dcurioustech/agent-loop/issues"
        ],
        "error",
    )


def format_error(exc: BaseException, context: Optional[str] = None) -> str:
    """Render `exc` as the multi-line text `report` prints.

    `context` (e.g. "unable to write plan_checkpoints.json") is prepended to
    the message so the user knows which step failed.
    """
    message, hints, prefix = describe(exc)
    if context:
        message = f"{context}: {message}"
    lines = [f"{prefix}: {message}"]
    lines.extend(f"  hint: {h}" for h in hints)
    return "\n".join(lines)


def report(
    exc: BaseException,
    context: Optional[str] = None,
    stream: Optional[TextIO] = None,
) -> None:
    """Print a friendly description of `exc` (and, in debug mode, its traceback)."""
    stream = stream if stream is not None else sys.stderr
    if debug_enabled():
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=stream)
    print(format_error(exc, context), file=stream)


def fail(message: str, *hints: str, stream: Optional[TextIO] = None) -> int:
    """Print a plain error with optional hints and return exit code 2."""
    stream = stream if stream is not None else sys.stderr
    print(f"error: {message}", file=stream)
    for h in hints:
        print(f"  hint: {h}", file=stream)
    return 2
