"""Shared foundations for building `plan_checkpoints.json` payloads.

Provides the canonical generated-state shape (a payload that satisfies the
same schema `load_state` enforces) plus reusable in-memory validation, so
that plan-generation code (see the `init` subcommand, added in a later
phase) can validate a provider-produced payload before writing anything to
disk.
"""
from __future__ import annotations

from typing import Any, Optional

from .state import (
    DuplicateCheckpointError,
    InvalidPlanState,
    ProtectedBranchError,
    validate_payload,
)

__all__ = [
    "DuplicateCheckpointError",
    "InvalidPlanState",
    "ProtectedBranchError",
    "new_checkpoint",
    "build_generated_payload",
    "validate_generated_payload",
]


def new_checkpoint(cid: str, name: str, scope: str, exit_criteria: list[str]) -> dict:
    """Build a single checkpoint in its canonical freshly-generated shape.

    Always starts `pending`, with zero attempts and no review notes,
    regardless of what a provider returned for those fields.
    """
    return {
        "id": cid,
        "name": name,
        "status": "pending",
        "scope": scope,
        "exit_criteria": list(exit_criteria),
        "attempts": 0,
        "review_notes": "",
    }


def build_generated_payload(
    plan_file: str,
    branch: str,
    checkpoints: list[dict],
    project: Optional[dict] = None,
    models: Optional[dict] = None,
) -> dict[str, Any]:
    """Assemble a top-level payload in the canonical generated-state shape."""
    payload: dict[str, Any] = {
        "plan_file": plan_file,
        "branch": branch,
        "checkpoints": checkpoints,
    }
    if project:
        payload["project"] = project
    if models:
        payload["models"] = models
    return payload


def validate_generated_payload(payload: dict) -> None:
    """Validate an in-memory generated payload before it is written to disk.

    Raises `InvalidPlanState` (or the more specific `ProtectedBranchError` /
    `DuplicateCheckpointError` subclasses) on failure; raises nothing on
    success.
    """
    validate_payload(payload)
