"""Tests for the shared plan-generation foundations (plan_init.py)."""
from __future__ import annotations

import pytest

from agent_loop.plan_init import (
    DuplicateCheckpointError,
    InvalidPlanState,
    ProtectedBranchError,
    build_generated_payload,
    new_checkpoint,
    validate_generated_payload,
)
from agent_loop.state import load_state


def _checkpoints() -> list[dict]:
    return [
        new_checkpoint("phase0", "Setup", "Bootstrap things", ["A", "B"]),
        new_checkpoint("phase1", "Build", "Make widgets", ["X"]),
    ]


# ---------------------------------------------------------------------------
# Canonical shape
# ---------------------------------------------------------------------------


def test_new_checkpoint_has_canonical_pending_shape():
    cp = new_checkpoint("phase0", "Setup", "Bootstrap things", ["A", "B"])
    assert cp == {
        "id": "phase0",
        "name": "Setup",
        "status": "pending",
        "scope": "Bootstrap things",
        "exit_criteria": ["A", "B"],
        "attempts": 0,
        "review_notes": "",
    }


def test_build_generated_payload_includes_required_top_level_fields():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    assert payload["plan_file"] == "docs/plan.md"
    assert payload["branch"] == "feature/x"
    assert [cp["id"] for cp in payload["checkpoints"]] == ["phase0", "phase1"]
    assert "project" not in payload
    assert "models" not in payload


def test_build_generated_payload_includes_optional_blocks_when_given():
    payload = build_generated_payload(
        "docs/plan.md",
        "feature/x",
        _checkpoints(),
        project={"test_cmd": "pytest"},
        models={"developer": "opus-x"},
    )
    assert payload["project"] == {"test_cmd": "pytest"}
    assert payload["models"] == {"developer": "opus-x"}


# ---------------------------------------------------------------------------
# Validation of in-memory payloads (before anything is written)
# ---------------------------------------------------------------------------


def test_validate_generated_payload_accepts_canonical_shape():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)  # must not raise


def test_validate_generated_payload_rejects_protected_branch():
    payload = build_generated_payload("docs/plan.md", "main", _checkpoints())
    with pytest.raises(ProtectedBranchError, match="protected"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_duplicate_checkpoint_ids():
    checkpoints = _checkpoints()
    checkpoints[1]["id"] = "phase0"
    payload = build_generated_payload("docs/plan.md", "feature/x", checkpoints)
    with pytest.raises(DuplicateCheckpointError, match="duplicate"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_malformed_payload():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    del payload["checkpoints"][0]["exit_criteria"]
    with pytest.raises(InvalidPlanState, match="exit_criteria"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_non_dict():
    with pytest.raises(InvalidPlanState, match="object"):
        validate_generated_payload(["not", "a", "dict"])  # type: ignore[arg-type]


def test_validate_generated_payload_does_not_touch_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Compatibility: a validated generated payload loads through load_state
# ---------------------------------------------------------------------------


def test_validated_payload_round_trips_through_load_state(tmp_path):
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)

    import json

    state_path = tmp_path / "plan_checkpoints.json"
    state_path.write_text(json.dumps(payload, indent=2))

    state = load_state(state_path)
    assert state.branch == "feature/x"
    assert [cp["id"] for cp in state.checkpoints] == ["phase0", "phase1"]
    assert state.get("phase0")["status"] == "pending"
    assert state.get("phase0")["attempts"] == 0
    assert state.get("phase0")["review_notes"] == ""
