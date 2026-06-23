"""Tests for the agent-loop CLI surface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_loop import cli


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
# `init`
# ---------------------------------------------------------------------------


def test_init_creates_file(tmp_path, capsys):
    out_path = tmp_path / "plan_checkpoints.json"
    rc = cli.main(["init", "--stack", "go", "--state", str(out_path)])
    assert rc == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert data["project"]["test_cmd"] == "go test ./..."
    assert data["checkpoints"][0]["status"] == "pending"
    assert "Created" in capsys.readouterr().out


def test_init_explicit_stack_go(tmp_path):
    out_path = tmp_path / "plan_checkpoints.json"
    cli.main(["init", "--stack", "go", "--state", str(out_path)])
    data = json.loads(out_path.read_text())
    assert data["project"]["build_cmd"] == "go build ./..."
    assert data["project"]["test_cmd"] == "go test ./..."
    assert data["project"]["lint_cmd"] == "golangci-lint run"


def test_init_explicit_stack_python_has_no_build_cmd(tmp_path):
    out_path = tmp_path / "plan_checkpoints.json"
    cli.main(["init", "--stack", "python", "--state", str(out_path)])
    data = json.loads(out_path.read_text())
    # Python has no build step — key must be absent, not None
    assert "build_cmd" not in data["project"]
    assert data["project"]["test_cmd"] == "pytest"


def test_init_already_exists_error(tmp_path, capsys):
    out_path = tmp_path / "plan_checkpoints.json"
    out_path.write_text("{}")
    rc = cli.main(["init", "--stack", "go", "--state", str(out_path)])
    assert rc == 2
    assert "force" in capsys.readouterr().err.lower()


def test_init_force_overwrites(tmp_path):
    out_path = tmp_path / "plan_checkpoints.json"
    out_path.write_text("{}")
    rc = cli.main(["init", "--stack", "rust", "--state", str(out_path), "--force"])
    assert rc == 0
    data = json.loads(out_path.read_text())
    assert data["project"]["test_cmd"] == "cargo test"


def test_init_generated_file_validates(tmp_path):
    from agent_loop.state import load_state

    out_path = tmp_path / "plan_checkpoints.json"
    cli.main(["init", "--stack", "node", "--state", str(out_path)])
    state = load_state(out_path)
    assert state.branch == "feature-branch"
    assert len(state.checkpoints) == 1
