"""Tests for the CLI provider adapters.

These tests pin the exact argv each adapter emits so the loop can never silently
fall back to a permission-prompted invocation in an unattended run.
"""
from __future__ import annotations

import pytest

from agent_loop.providers import (
    PROVIDERS,
    ProviderError,
    get_provider,
    known_provider_names,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_all_four_providers_are_registered():
    assert known_provider_names() == ["claude", "codex", "gemini", "grok"]


def test_get_provider_rejects_unknown_name():
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider("not-a-real-cli")


# ---------------------------------------------------------------------------
# Default (safe) argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("claude", ["claude", "-p", "PROMPT", "--output-format", "text"]),
        ("codex", ["codex", "exec", "--full-auto", "PROMPT"]),
        ("grok", ["grok", "-p", "PROMPT"]),
        ("gemini", ["gemini", "-p", "PROMPT"]),
    ],
)
def test_default_argv_is_safe_mode(monkeypatch, name, expected):
    # Make sure no danger env leaks in from the host shell.
    for env in (
        "ALLOW_DANGEROUS_CLAUDE",
        "ALLOW_DANGEROUS_CODEX",
        "ALLOW_DANGEROUS_GROK",
        "ALLOW_DANGEROUS_GEMINI",
    ):
        monkeypatch.delenv(env, raising=False)

    provider = get_provider(name)
    assert provider.build_argv("PROMPT") == expected
    assert provider.dangerous_enabled is False


# ---------------------------------------------------------------------------
# Dangerous (write-mode) argv when the per-provider gate is set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, env_var, expected",
    [
        (
            "claude",
            "ALLOW_DANGEROUS_CLAUDE",
            ["claude", "-p", "PROMPT", "--dangerously-skip-permissions", "--output-format", "text"],
        ),
        (
            "codex",
            "ALLOW_DANGEROUS_CODEX",
            ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "PROMPT"],
        ),
        (
            "grok",
            "ALLOW_DANGEROUS_GROK",
            ["grok", "-p", "PROMPT", "--always-approve"],
        ),
        (
            "gemini",
            "ALLOW_DANGEROUS_GEMINI",
            ["gemini", "-p", "PROMPT", "--approval-mode", "yolo"],
        ),
    ],
)
def test_dangerous_argv_when_gate_set(monkeypatch, name, env_var, expected):
    monkeypatch.setenv(env_var, "1")
    provider = get_provider(name)
    assert provider.dangerous_enabled is True
    assert provider.build_argv("PROMPT") == expected


@pytest.mark.parametrize(
    "name, env_var",
    [
        ("claude", "ALLOW_DANGEROUS_CLAUDE"),
        ("codex", "ALLOW_DANGEROUS_CODEX"),
        ("grok", "ALLOW_DANGEROUS_GROK"),
        ("gemini", "ALLOW_DANGEROUS_GEMINI"),
    ],
)
def test_danger_env_is_strict_one(monkeypatch, name, env_var):
    """Anything other than the literal string "1" must NOT enable danger mode.

    Avoids surprises with values like "true", "yes", or "0".
    """
    for raw in ("0", "true", "yes", "True", ""):
        monkeypatch.setenv(env_var, raw)
        assert get_provider(name).dangerous_enabled is False, (
            f"{name} treated env value {raw!r} as enabled"
        )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr("agent_loop.providers.base.shutil.which", lambda _name: None)
    with pytest.raises(ProviderError, match="not on PATH"):
        get_provider("claude").preflight()


def test_preflight_passes_when_binary_present(monkeypatch):
    monkeypatch.setattr(
        "agent_loop.providers.base.shutil.which",
        lambda _name: "/usr/local/bin/whatever",
    )
    get_provider("codex").preflight()  # no raise


# ---------------------------------------------------------------------------
# Prompts with shell-special chars should round-trip as one argv slot, not be
# split by the adapter. We pass argv directly to subprocess so the shell never
# tokenises it; the test guards against an accidental shlex.split() regression.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["claude", "codex", "grok", "gemini"])
def test_prompt_with_quotes_and_spaces_stays_single_token(name):
    nasty = 'hello "world" with spaces; rm -rf /'
    argv = get_provider(name).build_argv(nasty)
    assert nasty in argv
    # The nasty string must appear as a single argv element, not split.
    assert sum(1 for a in argv if a == nasty) == 1


# ---------------------------------------------------------------------------
# Sanity: every registered provider declares the required class attributes
# ---------------------------------------------------------------------------


def test_every_provider_declares_required_attrs():
    for name, cls in PROVIDERS.items():
        assert cls.name == name
        assert isinstance(cls.binary, str) and cls.binary
        assert cls.danger_env.startswith("ALLOW_DANGEROUS_")
