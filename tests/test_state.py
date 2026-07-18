"""Tests for plan_checkpoints.json load/save/validate semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_loop.state import (
    CheckpointNotFound,
    InvalidPlanState,
    PlanState,
    load_state,
)


def _minimal_plan(branch: str = "feature-x") -> dict:
    return {
        "plan_file": "docs/plan.md",
        "branch": branch,
        "checkpoints": [
            {
                "id": "phase0",
                "name": "Setup",
                "status": "pending",
                "scope": "Bootstrap things",
                "exit_criteria": ["A", "B"],
                "attempts": 0,
                "review_notes": "",
            },
            {
                "id": "phase1",
                "name": "Build",
                "status": "approved",
                "scope": "Make widgets",
                "exit_criteria": ["X"],
                "attempts": 1,
                "review_notes": "ok",
            },
        ],
    }


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "plan_checkpoints.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


# ---------------------------------------------------------------------------
# Load + schema
# ---------------------------------------------------------------------------


def test_load_round_trip(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    assert isinstance(state, PlanState)
    assert state.plan_file == "docs/plan.md"
    assert state.branch == "feature-x"
    assert [cp["id"] for cp in state.checkpoints] == ["phase0", "phase1"]


def test_load_rejects_missing_top_level_field(tmp_path):
    bad = _minimal_plan()
    del bad["branch"]
    p = _write(tmp_path, bad)
    with pytest.raises(InvalidPlanState, match="branch"):
        load_state(p)


def test_load_rejects_protected_branch(tmp_path):
    for protected in ("main", "master"):
        p = _write(tmp_path, _minimal_plan(branch=protected))
        with pytest.raises(InvalidPlanState, match="protected"):
            load_state(p)


def test_load_rejects_unknown_checkpoint_status(tmp_path):
    bad = _minimal_plan()
    bad["checkpoints"][0]["status"] = "wibble"
    p = _write(tmp_path, bad)
    with pytest.raises(InvalidPlanState, match="status"):
        load_state(p)


def test_load_rejects_duplicate_checkpoint_id(tmp_path):
    bad = _minimal_plan()
    bad["checkpoints"][1]["id"] = "phase0"
    p = _write(tmp_path, bad)
    with pytest.raises(InvalidPlanState, match="duplicate"):
        load_state(p)


def test_load_rejects_checkpoint_missing_required_field(tmp_path):
    bad = _minimal_plan()
    del bad["checkpoints"][0]["exit_criteria"]
    p = _write(tmp_path, bad)
    with pytest.raises(InvalidPlanState, match="exit_criteria"):
        load_state(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(InvalidPlanState, match="not found"):
        load_state(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Project block (optional, language-agnostic)
# ---------------------------------------------------------------------------


def test_project_block_defaults_to_empty(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    assert state.build_cmd is None
    assert state.test_cmd is None
    assert state.lint_cmd is None
    assert state.verify_in_review is False


def test_project_block_is_loaded(tmp_path):
    payload = _minimal_plan()
    payload["project"] = {
        "build_cmd": "make build",
        "test_cmd": "pytest",
        "lint_cmd": "ruff check .",
        "verify_in_review": True,
    }
    p = _write(tmp_path, payload)
    state = load_state(p)
    assert state.build_cmd == "make build"
    assert state.test_cmd == "pytest"
    assert state.lint_cmd == "ruff check ."
    assert state.verify_in_review is True


def test_per_checkpoint_command_overrides_project_default(tmp_path):
    payload = _minimal_plan()
    payload["project"] = {"test_cmd": "pytest"}
    payload["checkpoints"][0]["test_cmd"] = "pytest tests/phase0"
    p = _write(tmp_path, payload)
    state = load_state(p)
    assert state.command_for("phase0", "test_cmd") == "pytest tests/phase0"
    assert state.command_for("phase1", "test_cmd") == "pytest"


# ---------------------------------------------------------------------------
# models block
# ---------------------------------------------------------------------------


def test_models_block_defaults_to_empty(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    assert state.models == {}
    assert state.model_for("developer") is None
    assert state.model_for("reviewer") is None


def test_models_block_is_loaded(tmp_path):
    payload = _minimal_plan()
    payload["models"] = {"developer": "opus-x", "reviewer": "codex-y"}
    p = _write(tmp_path, payload)
    state = load_state(p)
    assert state.model_for("developer") == "opus-x"
    assert state.model_for("reviewer") == "codex-y"


def test_models_block_survives_save_round_trip(tmp_path):
    payload = _minimal_plan()
    payload["models"] = {"developer": "opus-x"}
    p = _write(tmp_path, payload)
    load_state(p).save()
    assert load_state(p).model_for("developer") == "opus-x"


def test_explicit_null_models_block_is_treated_as_empty(tmp_path):
    # `"models": null` must not crash model_for(); it means "not configured".
    payload = _minimal_plan()
    payload["models"] = None
    p = _write(tmp_path, payload)
    state = load_state(p)
    assert state.models == {}
    assert state.model_for("developer") is None


def test_load_rejects_unknown_model_role(tmp_path):
    payload = _minimal_plan()
    payload["models"] = {"architect": "opus-x"}
    p = _write(tmp_path, payload)
    with pytest.raises(InvalidPlanState, match="role"):
        load_state(p)


def test_load_rejects_non_string_model(tmp_path):
    payload = _minimal_plan()
    payload["models"] = {"developer": ""}
    p = _write(tmp_path, payload)
    with pytest.raises(InvalidPlanState, match="developer"):
        load_state(p)


# ---------------------------------------------------------------------------
# get / set / save
# ---------------------------------------------------------------------------


def test_get_checkpoint_returns_dict(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    cp = state.get("phase0")
    assert cp["name"] == "Setup"
    assert cp["status"] == "pending"


def test_get_unknown_checkpoint_raises(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    with pytest.raises(CheckpointNotFound, match="phase42"):
        state.get("phase42")


def test_set_field_mutates_and_persists(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    state.set_field("phase0", "status", "built")
    state.set_field("phase0", "review_notes", "hello")
    state.set_field("phase0", "attempts", 2)
    state.save()

    reloaded = load_state(p)
    cp = reloaded.get("phase0")
    assert cp["status"] == "built"
    assert cp["review_notes"] == "hello"
    assert cp["attempts"] == 2


def test_set_field_rejects_invalid_status(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    with pytest.raises(InvalidPlanState, match="status"):
        state.set_field("phase0", "status", "almost_done")


def test_pending_checkpoints_skips_approved(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    assert [cp["id"] for cp in state.pending_checkpoints()] == ["phase0"]


def test_pending_checkpoints_includes_built(tmp_path):
    payload = _minimal_plan()
    payload["checkpoints"][0]["status"] = "built"
    p = _write(tmp_path, payload)
    state = load_state(p)
    # "built" means built-but-not-approved, so still needs work.
    assert [cp["id"] for cp in state.pending_checkpoints()] == ["phase0"]


def test_save_pretty_prints_with_trailing_newline(tmp_path):
    p = _write(tmp_path, _minimal_plan())
    state = load_state(p)
    state.set_field("phase0", "review_notes", "abc")
    state.save()
    raw = p.read_text()
    assert raw.endswith("\n")
    assert "  " in raw  # indented
