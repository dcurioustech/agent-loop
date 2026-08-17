"""Tests for the redaction and audit-level plumbing in agent_loop.audit."""
from __future__ import annotations

import pytest

from agent_loop import audit


# ---------------------------------------------------------------------------
# redact() — whole-string secret shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36,
        "xoxb-1234567890-abcdefghij",
        "sk-" + "a" * 24,
    ],
)
def test_redact_scrubs_known_token_shapes(secret):
    text = f"here is a secret: {secret} end"
    assert secret not in audit.redact(text)
    assert "[REDACTED:" in audit.redact(text)


def test_redact_scrubs_bearer_token():
    text = "Authorization: Bearer " + "a" * 40
    out = audit.redact(text)
    assert "a" * 40 not in out
    assert "[REDACTED:bearer-token]" in out


def test_redact_scrubs_assigned_secret():
    out = audit.redact('api_key = "sk_live_abcdefghijklmnop"')
    assert "sk_live_abcdefghijklmnop" not in out


def test_redact_scrubs_single_line_private_key_block():
    out = audit.redact(
        "-----BEGIN RSA PRIVATE KEY-----ABCDEFG-----END RSA PRIVATE KEY-----"
    )
    assert "ABCDEFG" not in out
    assert "[REDACTED:private-key-block]" in out


def test_redact_scrubs_multiline_private_key_block():
    text = (
        "before\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBase64bodylinegoeshere\n"
        "moreBase64==\n"
        "-----END PRIVATE KEY-----\n"
        "after"
    )
    out = audit.redact(text)
    assert "MIIBase64bodylinegoeshere" not in out
    assert "before" in out and "after" in out


def test_redact_leaves_ordinary_text_untouched():
    text = "The developer added a test for checkpoint phase0 and fixed the bug."
    assert audit.redact(text) == text


def test_redact_does_not_flag_bare_mentions_without_a_value():
    """Talking about secrets in prose shouldn't itself trigger a redaction."""
    text = "Make sure the password field is never logged in plaintext."
    assert audit.redact(text) == text


# ---------------------------------------------------------------------------
# StreamRedactor — line-by-line, spans a PEM block across feed_line calls
# ---------------------------------------------------------------------------


def test_stream_redactor_scrubs_secret_on_a_single_line():
    r = audit.StreamRedactor()
    out = r.feed_line("token: " + "x" * 40 + "\n")
    assert "x" * 40 not in out


def test_stream_redactor_passes_through_ordinary_lines():
    r = audit.StreamRedactor()
    assert r.feed_line("building the project\n") == "building the project\n"


def test_stream_redactor_spans_a_private_key_block_across_lines():
    r = audit.StreamRedactor()
    lines = [
        "before\n",
        "-----BEGIN PRIVATE KEY-----\n",
        "secretbase64body\n",
        "-----END PRIVATE KEY-----\n",
        "after\n",
    ]
    out = "".join(r.feed_line(ln) for ln in lines)
    assert "secretbase64body" not in out
    assert "before" in out and "after" in out
    assert "[REDACTED:private-key-block]" in out


def test_stream_redactor_resumes_scrubbing_normal_lines_after_key_block():
    r = audit.StreamRedactor()
    for ln in ["-----BEGIN PRIVATE KEY-----\n", "body\n", "-----END PRIVATE KEY-----\n"]:
        r.feed_line(ln)
    assert r.feed_line("password: abcdef123456\n").find("abcdef123456") == -1


# ---------------------------------------------------------------------------
# prepare_text() — the level -> text-handling mapping
# ---------------------------------------------------------------------------


def test_prepare_text_full_is_unchanged():
    text = "notes containing " + "AKIAABCDEFGHIJKLMNOP"
    assert audit.prepare_text(text, "full") == text


def test_prepare_text_redacted_scrubs():
    text = "notes containing AKIAABCDEFGHIJKLMNOP"
    out = audit.prepare_text(text, "redacted")
    assert "AKIAABCDEFGHIJKLMNOP" not in out
    assert "notes containing" in out


def test_prepare_text_off_suppresses_content_entirely():
    text = "notes containing a secret value"
    out = audit.prepare_text(text, "off")
    assert text not in out
    assert str(len(text)) in out


# ---------------------------------------------------------------------------
# Level validation and env-var default resolution
# ---------------------------------------------------------------------------


def test_validate_accepts_known_levels():
    for level in audit.AUDIT_LEVELS:
        audit.validate(level)  # must not raise


def test_validate_rejects_unknown_level():
    with pytest.raises(audit.InvalidAuditLevel):
        audit.validate("verbose")


def test_default_audit_level_is_off_with_no_env(monkeypatch):
    monkeypatch.delenv("AGENT_LOOP_AUDIT_LEVEL", raising=False)
    assert audit.default_audit_level() == "off"


def test_default_audit_level_honors_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_LOOP_AUDIT_LEVEL", "full")
    assert audit.default_audit_level() == "full"


def test_default_audit_level_rejects_invalid_env_value(monkeypatch):
    monkeypatch.setenv("AGENT_LOOP_AUDIT_LEVEL", "nonsense")
    with pytest.raises(audit.InvalidAuditLevel):
        audit.default_audit_level()
