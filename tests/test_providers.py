"""Tests for the CLI provider adapters.

These tests pin the exact argv each adapter emits so the loop can never silently
fall back to a permission-prompted invocation in an unattended run.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_loop.providers import (
    PROVIDERS,
    ProviderError,
    get_provider,
    known_provider_names,
)
from agent_loop.providers.base import CapturedResult


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_all_five_providers_are_registered():
    assert known_provider_names() == ["antigravity", "claude", "codex", "gemini", "grok"]


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
        ("antigravity", ["agy", "-p", "PROMPT"]),
    ],
)
def test_default_argv_is_safe_mode(monkeypatch, name, expected):
    # Make sure no danger env leaks in from the host shell.
    for env in (
        "ALLOW_DANGEROUS_CLAUDE",
        "ALLOW_DANGEROUS_CODEX",
        "ALLOW_DANGEROUS_GROK",
        "ALLOW_DANGEROUS_GEMINI",
        "ALLOW_DANGEROUS_ANTIGRAVITY",
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
        (
            "antigravity",
            "ALLOW_DANGEROUS_ANTIGRAVITY",
            ["agy", "-p", "PROMPT", "--dangerously-skip-permissions"],
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
        ("antigravity", "ALLOW_DANGEROUS_ANTIGRAVITY"),
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
# Model pinning: appended only when a model is supplied, and with the right flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        (
            "claude",
            ["claude", "-p", "PROMPT", "--output-format", "text", "--model", "M"],
        ),
        ("codex", ["codex", "exec", "--full-auto", "-m", "M", "PROMPT"]),
        ("grok", ["grok", "-p", "PROMPT", "--model", "M"]),
        ("gemini", ["gemini", "-p", "PROMPT", "-m", "M"]),
        ("antigravity", ["agy", "-p", "PROMPT", "--model", "M"]),
    ],
)
def test_model_is_appended_with_provider_flag(monkeypatch, name, expected):
    for env in (
        "ALLOW_DANGEROUS_CLAUDE",
        "ALLOW_DANGEROUS_CODEX",
        "ALLOW_DANGEROUS_GROK",
        "ALLOW_DANGEROUS_GEMINI",
        "ALLOW_DANGEROUS_ANTIGRAVITY",
    ):
        monkeypatch.delenv(env, raising=False)

    provider = get_provider(name, "M")
    assert provider.model == "M"
    assert provider.build_argv("PROMPT") == expected


@pytest.mark.parametrize("name", ["claude", "codex", "grok", "gemini"])
def test_no_model_flag_when_model_absent(name):
    # Both None and empty string mean "let the CLI pick its own default".
    for provider in (get_provider(name), get_provider(name, None), get_provider(name, "")):
        argv = provider.build_argv("PROMPT")
        assert "--model" not in argv and "-m" not in argv
        assert provider.model is None


def test_codex_model_precedes_positional_prompt():
    # The model flag must sit before the prompt or `codex exec` mis-parses it.
    argv = get_provider("codex", "M").build_argv("PROMPT")
    assert argv.index("M") < argv.index("PROMPT")


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


@pytest.mark.parametrize("name", ["claude", "codex", "grok", "gemini", "antigravity"])
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


# ---------------------------------------------------------------------------
# run_captured: the init-only, non-streaming execution path. `run` (used by
# the developer/reviewer loop) must be completely untouched by any of this.
# ---------------------------------------------------------------------------


def test_run_captured_returns_stdout_stderr_and_returncode_on_success(monkeypatch):
    captured_argv = {}

    def fake_run(argv, timeout, capture_output, text):
        captured_argv["argv"] = argv
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout='{"ok": true}', stderr=""
        )

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    provider = get_provider("claude")
    result = provider.run_captured("PROMPT", timeout=30)

    assert result == CapturedResult(
        returncode=0, stdout='{"ok": true}', stderr="", timed_out=False
    )
    assert captured_argv["argv"] == provider.build_argv("PROMPT")


def test_run_captured_reports_non_zero_exit_without_raising(monkeypatch):
    def fake_run(argv, timeout, capture_output, text):
        return subprocess.CompletedProcess(
            argv, returncode=1, stdout="", stderr="boom"
        )

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    result = get_provider("claude").run_captured("PROMPT", timeout=30)

    assert result.returncode == 1
    assert result.stderr == "boom"
    assert result.timed_out is False


def test_run_captured_reports_timeout_with_partial_output(monkeypatch):
    def fake_run(argv, timeout, capture_output, text):
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=timeout, output="partial-out", stderr="partial-err"
        )

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    result = get_provider("claude").run_captured("PROMPT", timeout=5)

    assert result.timed_out is True
    assert result.returncode == 124
    assert result.stdout == "partial-out"
    assert result.stderr == "partial-err"


def test_run_captured_timeout_with_no_partial_output_yields_empty_strings(monkeypatch):
    def fake_run(argv, timeout, capture_output, text):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    result = get_provider("claude").run_captured("PROMPT", timeout=5)

    assert result.timed_out is True
    assert result.stdout == ""
    assert result.stderr == ""


def test_run_captured_does_not_print_to_stdout(monkeypatch, capsys):
    def fake_run(argv, timeout, capture_output, text):
        return subprocess.CompletedProcess(argv, returncode=0, stdout="x", stderr="")

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    get_provider("claude").run_captured("PROMPT", timeout=30)

    assert capsys.readouterr().out == ""


def test_run_still_uses_streaming_subprocess_call_unchanged(monkeypatch):
    """Guards against `run_captured` accidentally changing `run`'s behavior."""
    calls = []

    def fake_run(argv, timeout):
        calls.append({"argv": argv, "timeout": timeout})
        return subprocess.CompletedProcess(argv, returncode=0)

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    provider = get_provider("claude")
    rc = provider.run("PROMPT", timeout=30)

    assert rc == 0
    assert calls == [{"argv": provider.build_argv("PROMPT"), "timeout": 30}]


def test_run_still_times_out_the_same_way_unchanged(monkeypatch, capsys):
    def fake_run(argv, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr("agent_loop.providers.base.subprocess.run", fake_run)
    rc = get_provider("claude").run("PROMPT", timeout=5)

    assert rc == 124
    assert "timed out after 5s" in capsys.readouterr().out
