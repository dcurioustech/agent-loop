"""Tests for the agent-loop CLI surface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_loop import cli, plan_init
from agent_loop.providers.base import CapturedResult, Provider, ProviderError


def _plan(tmp_path: Path, **overrides) -> Path:
    payload = {
        "plan_file": "docs/plan.md",
        "branch": "feature-x",
        "checkpoints": [
            {
                "id": "phase0",
                "name": "Setup",
                "status": "approved",
                "scope": "scope0",
                "exit_criteria": ["a"],
                "attempts": 1,
                "review_notes": "ok",
            },
            {
                "id": "phase1",
                "name": "Build",
                "status": "pending",
                "scope": "scope1",
                "exit_criteria": ["b"],
                "attempts": 0,
                "review_notes": "",
            },
        ],
    }
    payload.update(overrides)
    p = tmp_path / "plan_checkpoints.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


# ---------------------------------------------------------------------------
# `validate`
# ---------------------------------------------------------------------------


def test_validate_returns_zero_on_good_plan(tmp_path, capsys):
    p = _plan(tmp_path)
    rc = cli.main(["validate", "--state", str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "phase0" in out and "phase1" in out


def test_validate_returns_nonzero_on_bad_plan(tmp_path, capsys):
    p = tmp_path / "plan_checkpoints.json"
    p.write_text("{}")
    rc = cli.main(["validate", "--state", str(p)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "plan_file" in err or "Missing" in err


def test_validate_returns_nonzero_when_state_missing(tmp_path, capsys):
    rc = cli.main(["validate", "--state", str(tmp_path / "missing.json")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# `status`
# ---------------------------------------------------------------------------


def test_status_prints_one_line_per_checkpoint(tmp_path, capsys):
    p = _plan(tmp_path)
    rc = cli.main(["status", "--state", str(p)])
    assert rc == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    cp_lines = [ln for ln in lines if "phase" in ln]
    assert len(cp_lines) == 2
    assert any("phase0" in ln and "approved" in ln for ln in cp_lines)
    assert any("phase1" in ln and "pending" in ln for ln in cp_lines)


def test_status_includes_branch(tmp_path, capsys):
    p = _plan(tmp_path)
    cli.main(["status", "--state", str(p)])
    out = capsys.readouterr().out
    assert "feature-x" in out


# ---------------------------------------------------------------------------
# `run` — argument parsing and routing (the loop itself is monkeypatched)
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_run_preconditions(monkeypatch):
    """Bypass binary preflight, danger gates, lockfile, and log tee for `run` tests."""
    from agent_loop.providers.base import Provider

    monkeypatch.setattr(Provider, "preflight", lambda self: None)
    monkeypatch.setattr(cli.safety, "require_danger_gates", lambda providers: None)
    monkeypatch.setattr(cli.safety, "open_log_file", lambda _d: Path("/tmp/agent_loop_test.log"))
    monkeypatch.setattr(cli.safety, "tee_stdout_to", lambda _p: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_lock(_path):
        yield

    monkeypatch.setattr(cli.safety, "lockfile", fake_lock)


def test_run_invokes_loop_with_defaults(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path)
    captured: dict = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    rc = cli.main(["run", "--state", str(p)])
    assert rc == 0
    assert captured["state_path"] == p
    assert captured["developer"].name == "claude"
    assert captured["reviewer"].name == "codex"
    assert captured["max_review_attempts"] == 3
    assert captured["timeout"] == 1800


def test_run_accepts_provider_overrides(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(cli, "run_loop", lambda **kw: captured.update(kw))
    cli.main(
        [
            "run",
            "--state",
            str(p),
            "--developer",
            "grok",
            "--reviewer",
            "gemini",
            "--max-review-attempts",
            "5",
            "--timeout",
            "60",
        ]
    )
    assert captured["developer"].name == "grok"
    assert captured["reviewer"].name == "gemini"
    assert captured["max_review_attempts"] == 5
    assert captured["timeout"] == 60


def test_run_uses_plan_models_when_no_flags(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path, models={"developer": "opus-x", "reviewer": "codex-y"})
    captured: dict = {}
    monkeypatch.setattr(cli, "run_loop", lambda **kw: captured.update(kw))
    cli.main(["run", "--state", str(p)])
    assert captured["developer"].model == "opus-x"
    assert captured["reviewer"].model == "codex-y"


def test_run_flags_override_plan_models(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path, models={"developer": "opus-x", "reviewer": "codex-y"})
    captured: dict = {}
    monkeypatch.setattr(cli, "run_loop", lambda **kw: captured.update(kw))
    cli.main(
        [
            "run",
            "--state",
            str(p),
            "--developer-model",
            "flag-dev",
            "--reviewer-model",
            "flag-rev",
        ]
    )
    assert captured["developer"].model == "flag-dev"
    assert captured["reviewer"].model == "flag-rev"


def test_run_defaults_to_no_model(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(cli, "run_loop", lambda **kw: captured.update(kw))
    cli.main(["run", "--state", str(p)])
    assert captured["developer"].model is None
    assert captured["reviewer"].model is None


def test_run_rejects_unknown_provider(tmp_path, capsys):
    p = _plan(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["run", "--state", str(p), "--developer", "bogus"])


def test_run_returns_nonzero_on_loop_halt(tmp_path, monkeypatch, _stub_run_preconditions):
    p = _plan(tmp_path)

    def fake_run_loop(**_):
        from agent_loop.orchestrator import LoopHalted

        raise LoopHalted("phase1 not approved")

    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    rc = cli.main(["run", "--state", str(p)])
    assert rc != 0


def test_no_args_prints_help_and_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    # argparse-style: 2 on usage error
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# `init` — plan generation via a mocked provider
# ---------------------------------------------------------------------------


def _provider_checkpoint(cid: str = "phase0") -> dict:
    return {"id": cid, "name": "Setup", "scope": "Bootstrap", "exit_criteria": ["A"]}


def _plan_json(*ids: str) -> str:
    ids = ids or ("phase0",)
    return json.dumps({"checkpoints": [_provider_checkpoint(i) for i in ids]})


class _FakeInitProvider(Provider):
    name = "fake"
    binary = "fake"
    danger_env = "ALLOW_DANGEROUS_FAKE"

    def __init__(
        self,
        model=None,
        *,
        stdout: str = "",
        returncode: int = 0,
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        super().__init__(model)
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out
        self.captured_calls: list[tuple[str, int]] = []

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover
        return [self.binary, prompt]

    def preflight(self) -> None:
        return None

    def run_captured(self, prompt: str, timeout: int) -> CapturedResult:
        self.captured_calls.append((prompt, timeout))
        return CapturedResult(
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
        )


@pytest.fixture
def _stub_init_provider(monkeypatch):
    """Route cli.get_provider to a fake, recording the name/model it was asked for."""
    state = {"provider": _FakeInitProvider(stdout=_plan_json())}
    calls: dict = {}

    def fake_get_provider(name, model=None):
        calls["name"] = name
        calls["model"] = model
        return state["provider"]

    monkeypatch.setattr(cli, "get_provider", fake_get_provider)
    return state, calls


def test_init_accepts_plain_english_and_writes_default_plan_file(
    tmp_path, _stub_init_provider
):
    state, calls = _stub_init_provider
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc == 0
    payload = json.loads(state_path.read_text())
    assert payload["plan_file"] == plan_init.DEFAULT_PLAN_FILE
    assert payload["branch"] == "feature/add-a-login-page"
    assert payload["checkpoints"][0]["status"] == "pending"
    assert payload["checkpoints"][0]["attempts"] == 0
    assert payload["checkpoints"][0]["review_notes"] == ""
    assert "Add a login page" in state["provider"].captured_calls[0][0]


def test_init_accepts_feature_file_and_uses_it_as_default_plan_file(
    tmp_path, _stub_init_provider
):
    _state, _calls = _stub_init_provider
    feature_file = tmp_path / "user_auth.md"
    feature_file.write_text("# User auth\nAdd login and signup flows.")
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(
        ["init", "--feature-file", str(feature_file), "--state", str(state_path)]
    )

    assert rc == 0
    payload = json.loads(state_path.read_text())
    assert payload["plan_file"] == str(feature_file)
    assert payload["branch"] == "feature/user-auth"


def test_init_rejects_both_feature_and_feature_file(tmp_path, capsys, _stub_init_provider):
    feature_file = tmp_path / "f.md"
    feature_file.write_text("content")
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(
        [
            "init",
            "Add a login page",
            "--feature-file",
            str(feature_file),
            "--state",
            str(state_path),
        ]
    )

    assert rc != 0
    assert not state_path.exists()
    assert "not both" in capsys.readouterr().err


def test_init_rejects_neither_feature_nor_feature_file(tmp_path, capsys, _stub_init_provider):
    state_path = tmp_path / "plan_checkpoints.json"
    rc = cli.main(["init", "--state", str(state_path)])
    assert rc != 0
    assert not state_path.exists()
    assert "feature description" in capsys.readouterr().err


def test_init_rejects_missing_feature_file(tmp_path, capsys, _stub_init_provider):
    state_path = tmp_path / "plan_checkpoints.json"
    rc = cli.main(
        [
            "init",
            "--feature-file",
            str(tmp_path / "missing.md"),
            "--state",
            str(state_path),
        ]
    )
    assert rc != 0
    assert not state_path.exists()
    assert "not found" in capsys.readouterr().err


def test_init_rejects_empty_feature_file(tmp_path, capsys, _stub_init_provider):
    feature_file = tmp_path / "empty.md"
    feature_file.write_text("   \n  ")
    state_path = tmp_path / "plan_checkpoints.json"
    rc = cli.main(
        ["init", "--feature-file", str(feature_file), "--state", str(state_path)]
    )
    assert rc != 0
    assert not state_path.exists()
    assert "empty" in capsys.readouterr().err


def test_init_flags_override_branch_plan_file_provider_model_timeout(
    tmp_path, _stub_init_provider
):
    state, calls = _stub_init_provider
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(
        [
            "init",
            "Add a login page",
            "--state",
            str(state_path),
            "--branch",
            "feature/custom-branch",
            "--plan-file",
            "docs/custom_plan.md",
            "--provider",
            "codex",
            "--model",
            "some-model",
            "--timeout",
            "42",
        ]
    )

    assert rc == 0
    payload = json.loads(state_path.read_text())
    assert payload["branch"] == "feature/custom-branch"
    assert payload["plan_file"] == "docs/custom_plan.md"
    assert calls["name"] == "codex"
    assert calls["model"] == "some-model"
    assert state["provider"].captured_calls[0][1] == 42


def test_init_refuses_existing_state_without_force(tmp_path, capsys, _stub_init_provider):
    state_path = tmp_path / "plan_checkpoints.json"
    state_path.write_text("original content")

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert state_path.read_text() == "original content"
    assert "--force" in capsys.readouterr().err


def test_init_overwrites_existing_state_with_force(tmp_path, _stub_init_provider):
    state_path = tmp_path / "plan_checkpoints.json"
    state_path.write_text("original content")

    rc = cli.main(
        ["init", "Add a login page", "--state", str(state_path), "--force"]
    )

    assert rc == 0
    payload = json.loads(state_path.read_text())
    assert payload["branch"] == "feature/add-a-login-page"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_init_rejects_protected_branch(tmp_path, capsys, _stub_init_provider, branch):
    state, _calls = _stub_init_provider
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(
        [
            "init",
            "Add a login page",
            "--state",
            str(state_path),
            "--branch",
            branch,
        ]
    )

    assert rc != 0
    assert not state_path.exists()
    assert "protected" in capsys.readouterr().err
    # Fails fast: never even calls out to the provider.
    assert state["provider"].captured_calls == []


def test_init_does_not_write_state_on_provider_failure(tmp_path, capsys, monkeypatch):
    provider = _FakeInitProvider(returncode=1, stderr="boom")
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert not state_path.exists()
    assert "boom" in capsys.readouterr().err


def test_init_does_not_write_state_on_provider_timeout(tmp_path, capsys, monkeypatch):
    provider = _FakeInitProvider(timed_out=True, returncode=124)
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert not state_path.exists()
    assert "timed out" in capsys.readouterr().err


def test_init_does_not_write_state_on_malformed_output(tmp_path, capsys, monkeypatch):
    provider = _FakeInitProvider(stdout="not json at all")
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert not state_path.exists()


def test_init_does_not_write_state_on_schema_validation_failure(tmp_path, capsys, monkeypatch):
    # Two checkpoints sharing an id fails the same schema check load_state uses.
    provider = _FakeInitProvider(stdout=_plan_json("phase0", "phase0"))
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert not state_path.exists()
    assert "duplicate" in capsys.readouterr().err


def test_init_does_not_overwrite_existing_file_on_failure_even_with_force(
    tmp_path, monkeypatch
):
    provider = _FakeInitProvider(returncode=1, stderr="boom")
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"
    state_path.write_text("original content")

    rc = cli.main(
        ["init", "Add a login page", "--state", str(state_path), "--force"]
    )

    assert rc != 0
    assert state_path.read_text() == "original content"


def test_init_rejects_missing_provider_binary(tmp_path, capsys, monkeypatch):
    class _MissingBinaryProvider(_FakeInitProvider):
        def preflight(self) -> None:
            raise ProviderError("Missing CLI for provider 'fake': 'fake' not on PATH.")

    provider = _MissingBinaryProvider()
    monkeypatch.setattr(cli, "get_provider", lambda name, model=None: provider)
    state_path = tmp_path / "plan_checkpoints.json"

    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])

    assert rc != 0
    assert not state_path.exists()
    assert "not on PATH" in capsys.readouterr().err


def test_init_rejects_unknown_provider_flag(tmp_path):
    state_path = tmp_path / "plan_checkpoints.json"
    with pytest.raises(SystemExit):
        cli.main(
            [
                "init",
                "Add a login page",
                "--state",
                str(state_path),
                "--provider",
                "bogus",
            ]
        )


def test_init_generated_state_is_usable_by_validate_and_status(
    tmp_path, capsys, _stub_init_provider
):
    state_path = tmp_path / "plan_checkpoints.json"
    rc = cli.main(["init", "Add a login page", "--state", str(state_path)])
    assert rc == 0
    capsys.readouterr()

    rc_validate = cli.main(["validate", "--state", str(state_path)])
    assert rc_validate == 0
    assert "OK" in capsys.readouterr().out

    rc_status = cli.main(["status", "--state", str(state_path)])
    assert rc_status == 0
    out = capsys.readouterr().out
    assert "phase0" in out and "pending" in out
