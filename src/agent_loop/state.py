"""Load, validate, mutate, and save plan_checkpoints.json."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ALLOWED_STATUSES = ("pending", "built", "approved")
PROTECTED_BRANCHES = ("main", "master")
REQUIRED_TOP_LEVEL = ("plan_file", "branch", "checkpoints")
REQUIRED_CHECKPOINT_FIELDS = (
    "id",
    "name",
    "status",
    "scope",
    "exit_criteria",
    "attempts",
    "review_notes",
)
COMMAND_FIELDS = ("build_cmd", "test_cmd", "lint_cmd")
MODEL_ROLES = ("developer", "reviewer")


class InvalidPlanState(ValueError):
    """Raised when plan_checkpoints.json fails schema validation."""


class CheckpointNotFound(KeyError):
    def __init__(self, cid: str):
        super().__init__(cid)
        self.cid = cid

    def __str__(self) -> str:
        return f"Unknown checkpoint: {self.cid}"


@dataclass
class PlanState:
    path: Path
    plan_file: str
    branch: str
    checkpoints: list[dict]
    project: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience accessors for the project block
    # ------------------------------------------------------------------

    @property
    def build_cmd(self) -> Optional[str]:
        return self.project.get("build_cmd")

    @property
    def test_cmd(self) -> Optional[str]:
        return self.project.get("test_cmd")

    @property
    def lint_cmd(self) -> Optional[str]:
        return self.project.get("lint_cmd")

    @property
    def verify_in_review(self) -> bool:
        return bool(self.project.get("verify_in_review", False))

    def command_for(self, cid: str, field_name: str) -> Optional[str]:
        if field_name not in COMMAND_FIELDS:
            raise ValueError(f"Unknown command field: {field_name}")
        cp = self.get(cid)
        return cp.get(field_name) or self.project.get(field_name)

    def model_for(self, role: str) -> Optional[str]:
        """Model pinned for a role in the plan, or None to defer to the CLI."""
        if role not in MODEL_ROLES:
            raise ValueError(f"Unknown model role: {role}")
        return self.models.get(role)

    # ------------------------------------------------------------------
    # Checkpoint queries / mutations
    # ------------------------------------------------------------------

    def get(self, cid: str) -> dict:
        for cp in self.checkpoints:
            if cp["id"] == cid:
                return cp
        raise CheckpointNotFound(cid)

    def ids(self) -> list[str]:
        return [cp["id"] for cp in self.checkpoints]

    def pending_checkpoints(self) -> Iterable[dict]:
        return [cp for cp in self.checkpoints if cp["status"] != "approved"]

    def set_field(self, cid: str, field_name: str, value: Any) -> None:
        cp = self.get(cid)
        if field_name == "status" and value not in ALLOWED_STATUSES:
            raise InvalidPlanState(
                f"checkpoint {cid}: invalid status {value!r}; allowed: {ALLOWED_STATUSES}"
            )
        cp[field_name] = value

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        payload: dict[str, Any] = {
            "plan_file": self.plan_file,
            "branch": self.branch,
        }
        if self.project:
            payload["project"] = self.project
        if self.models:
            payload["models"] = self.models
        payload["checkpoints"] = self.checkpoints

        with self.path.open("w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")


def load_state(path: Path | str) -> PlanState:
    path = Path(path)
    if not path.is_file():
        raise InvalidPlanState(f"State file not found: {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise InvalidPlanState(f"State file is not valid JSON: {e}") from e

    _validate(raw)
    return PlanState(
        path=path,
        plan_file=raw["plan_file"],
        branch=raw["branch"],
        checkpoints=raw["checkpoints"],
        # `or {}` (not a get-default) so an explicit `null` becomes {} too.
        project=raw.get("project") or {},
        models=raw.get("models") or {},
    )


def _validate(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise InvalidPlanState("Top-level must be a JSON object")

    for field_name in REQUIRED_TOP_LEVEL:
        if field_name not in raw:
            raise InvalidPlanState(f"Missing top-level field: {field_name}")

    branch = raw["branch"]
    if not isinstance(branch, str) or not branch:
        raise InvalidPlanState("'branch' must be a non-empty string")
    if branch in PROTECTED_BRANCHES:
        raise InvalidPlanState(f"Refusing protected target branch: {branch}")

    if not isinstance(raw["plan_file"], str) or not raw["plan_file"]:
        raise InvalidPlanState("'plan_file' must be a non-empty string")

    checkpoints = raw["checkpoints"]
    if not isinstance(checkpoints, list) or not checkpoints:
        raise InvalidPlanState("'checkpoints' must be a non-empty list")

    seen_ids: set[str] = set()
    for idx, cp in enumerate(checkpoints):
        if not isinstance(cp, dict):
            raise InvalidPlanState(f"checkpoint #{idx} must be a JSON object")
        for f in REQUIRED_CHECKPOINT_FIELDS:
            if f not in cp:
                raise InvalidPlanState(
                    f"checkpoint #{idx}: missing required field '{f}'"
                )
        if cp["status"] not in ALLOWED_STATUSES:
            raise InvalidPlanState(
                f"checkpoint {cp['id']}: invalid status {cp['status']!r}; "
                f"allowed: {ALLOWED_STATUSES}"
            )
        if not isinstance(cp["exit_criteria"], list):
            raise InvalidPlanState(
                f"checkpoint {cp['id']}: 'exit_criteria' must be a list"
            )
        if cp["id"] in seen_ids:
            raise InvalidPlanState(f"duplicate checkpoint id: {cp['id']}")
        seen_ids.add(cp["id"])

    project = raw.get("project")
    if project is not None and not isinstance(project, dict):
        raise InvalidPlanState("'project' must be a JSON object when present")

    models = raw.get("models")
    if models is not None:
        if not isinstance(models, dict):
            raise InvalidPlanState("'models' must be a JSON object when present")
        for role, model in models.items():
            if role not in MODEL_ROLES:
                raise InvalidPlanState(
                    f"'models' has unknown role {role!r}; allowed: {MODEL_ROLES}"
                )
            if not isinstance(model, str) or not model:
                raise InvalidPlanState(f"'models.{role}' must be a non-empty string")
