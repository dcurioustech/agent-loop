"""Controls how much of an agent's raw output reaches the committed log.

Run logs are git-committed audit artifacts (see `git_ops.commit_checkpoint_changes`),
which makes anything written to them effectively permanent — much harder to walk
back than a `/tmp` file. `--audit-level` trades completeness of that record
against exposure of whatever the developer/reviewer agents happen to print or
write while doing their work:

- ``full``:     nothing is touched. Agent output and agent-authored free text
                (prompts, review notes) are logged exactly as produced.
- ``redacted``: known secret *shapes* (AWS keys, GitHub/Slack/OpenAI tokens,
                bearer tokens, PEM private key blocks, `key: value`-style
                assignments) are scrubbed before anything is printed or logged.
                This is a best-effort net, not a guarantee — it cannot catch a
                project's own custom secret formats, and it runs on live,
                streamed subprocess output rather than a byte buffer, so a
                secret split across two flushed writes can slip through.
- ``off``:      raw agent output and agent-authored free text are not printed
                or logged at all. Only structured event metadata (which agent
                ran, what was approved, what was committed) reaches the log.
"""
from __future__ import annotations

import os
import re

AUDIT_LEVELS = ("full", "redacted", "off")
DEFAULT_AUDIT_LEVEL = "off"

_ENV_VAR = "AGENT_LOOP_AUDIT_LEVEL"


class InvalidAuditLevel(ValueError):
    pass


def default_audit_level() -> str:
    """The configured level: env override if set and valid, else the default."""
    configured = os.environ.get(_ENV_VAR)
    if not configured:
        return DEFAULT_AUDIT_LEVEL
    validate(configured)
    return configured


def validate(level: str) -> None:
    if level not in AUDIT_LEVELS:
        raise InvalidAuditLevel(
            f"Invalid audit level {level!r}; must be one of {', '.join(AUDIT_LEVELS)}."
        )


_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")

# High-confidence secret shapes only — anything looser produces enough false
# positives to make "redacted" logs unreadable without meaningfully raising
# recall. checked in order; each substitution runs on the previous pass's output.
_LINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.]{20,}")),
    (
        "assigned-secret",
        re.compile(
            r"""(?ix)
            \b(api[_-]?key|secret|token|password|passwd)\b
            \s*[:=]\s*
            ['"]?[^\s'"]{6,}['"]?
            """
        ),
    ),
]


def redact(text: str) -> str:
    """Best-effort scrub of common secret shapes from a complete string.

    Suitable for whole values already assembled in memory — prompts, review
    notes, checkpoint comments. For output arriving line-by-line from a live
    subprocess, use `StreamRedactor` instead so a PEM block split across
    lines is still caught.
    """
    text = _PRIVATE_KEY_BLOCK.sub("[REDACTED:private-key-block]", text)
    for name, pattern in _LINE_PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


class StreamRedactor:
    """Applies `redact` to output arriving one line at a time.

    Holds just enough state to span a PEM private-key block across multiple
    `feed_line` calls, which a single-call `redact(text)` on each line in
    isolation cannot do.
    """

    def __init__(self) -> None:
        self._in_private_key = False

    def feed_line(self, line: str) -> str:
        if self._in_private_key:
            if _PRIVATE_KEY_END.search(line):
                self._in_private_key = False
            return ""

        if _PRIVATE_KEY_BLOCK.search(line):
            return redact(line)  # BEGIN and END both landed on one line
        if _PRIVATE_KEY_BEGIN.search(line):
            self._in_private_key = True
            return "[REDACTED:private-key-block]\n"

        return redact(line)


def prepare_text(text: str, audit_level: str) -> str:
    """Apply an audit level to a complete piece of agent-authored text."""
    if audit_level == "off":
        return f"<suppressed by audit-level=off: {len(text)} chars>"
    if audit_level == "redacted":
        return redact(text)
    return text
