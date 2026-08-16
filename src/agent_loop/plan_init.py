"""Shared foundations for building `plan_checkpoints.json` payloads.

Provides the canonical generated-state shape (a payload that satisfies the
same schema `load_state` enforces) plus reusable in-memory validation, so
that plan-generation code (see the `init` subcommand, added in a later
phase) can validate a provider-produced payload before writing anything to
disk.

Also provides the `init`-only plan-generation path: running a provider in
captured (non-streaming) mode with a strict JSON-only prompt, then parsing
and normalizing whatever it returns into checkpoints in the canonical shape
above. The developer/reviewer loop is untouched by any of this.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from .providers.base import Provider
from .state import (
    DuplicateCheckpointError,
    InvalidPlanState,
    ProtectedBranchError,
    validate_payload,
)

__all__ = [
    "DEFAULT_PLAN_FILE",
    "DuplicateCheckpointError",
    "InvalidPlanState",
    "PlanOutputParseError",
    "ProtectedBranchError",
    "ProviderExecutionError",
    "build_generated_payload",
    "build_init_prompt",
    "generate_plan_json",
    "new_checkpoint",
    "normalize_checkpoints",
    "parse_plan_json",
    "run_provider_captured",
    "slugify",
    "validate_generated_payload",
    "write_generated_payload",
]

#: `plan_file` recorded for a plain-English feature description, matching the
#: convention documented in the README's schema example.
DEFAULT_PLAN_FILE = "docs/implementation_plan.md"


class ProviderExecutionError(RuntimeError):
    """Raised when the planning provider exits non-zero or times out."""


class PlanOutputParseError(ValueError):
    """Raised when provider output is not raw JSON or a single JSON code fence."""


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


_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    """Turn arbitrary text into a short, branch-name-safe slug.

    Lowercases, collapses any run of non-alphanumeric characters into a
    single hyphen, and trims to `max_len`. Falls back to "feature" if
    nothing alphanumeric survives.
    """
    slug = _SLUG_COLLAPSE_RE.sub("-", text.strip().lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or "feature"


def write_generated_payload(path: Path, payload: dict) -> None:
    """Write `payload` as formatted JSON to `path` atomically.

    Writes to a temp file in the same directory first, then renames it into
    place, so a crash or interrupt never leaves `path` truncated or holding
    partial JSON. Callers must validate `payload` before calling this.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Captured provider execution (init only — the run/review loop keeps using
# Provider.run's streaming behavior, unchanged).
# ---------------------------------------------------------------------------


def run_provider_captured(provider: Provider, prompt: str, timeout: int) -> str:
    """Run `provider` non-interactively and return its captured stdout.

    Raises `ProviderExecutionError` on a timeout or a non-zero exit rather
    than handing the caller a partial or garbage result.
    """
    result = provider.run_captured(prompt, timeout=timeout)
    if result.timed_out:
        raise ProviderExecutionError(
            f"{provider.name} timed out after {timeout}s while generating a plan"
        )
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ProviderExecutionError(
            f"{provider.name} exited with code {result.returncode} while "
            f"generating a plan{suffix}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Prompt: project-agnostic, ordered, independently reviewable checkpoints,
# JSON-only output.
# ---------------------------------------------------------------------------

INIT_PROMPT_TEMPLATE = """You are generating an implementation plan for a checkpoint-gated developer/reviewer coding loop. Convert the feature description below into an ordered list of independently reviewable checkpoints.

Feature description:
{feature_text}

Rules:
- Break the feature into 2 to 6 sequential checkpoints, ordered so each one builds on the ones before it and can be developed and reviewed in isolation.
- Each checkpoint must be project-agnostic: do not assume a specific language, framework, build tool, or test command unless the feature description names one.
- Each checkpoint needs a short unique id (e.g. "phase0", "phase1", ...), a short name, a "scope" describing exactly what to build, and an "exit_criteria" list of objective, independently verifiable conditions. Do not use vague criteria like "code is clean" or "works well".
- Do not include "status", "attempts", or "review_notes" fields; they are assigned automatically.
- Output ONLY a single JSON object and nothing else: no prose, no explanation, no markdown headings, before or after it. You may wrap the JSON in a single ```json code fence, or output raw JSON with no fence at all — never mix prose with either form.

Output JSON shape exactly:
{{
  "checkpoints": [
    {{"id": "phase0", "name": "...", "scope": "...", "exit_criteria": ["...", "..."]}}
  ]
}}
"""


def build_init_prompt(feature_text: str) -> str:
    """Build the strict, JSON-only planning prompt for `feature_text`."""
    return INIT_PROMPT_TEMPLATE.format(feature_text=feature_text)


# ---------------------------------------------------------------------------
# Parsing: raw JSON or a single Markdown-fenced JSON block, nothing else.
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"\A```(?:json)?\s*\n(?P<body>.*?)\n```\s*\Z", re.DOTALL)


def parse_plan_json(raw_output: str) -> Any:
    """Parse `raw_output` as raw JSON or as a single ```json fenced block.

    Rejects empty output, output that mixes prose with a fence (or has more
    than one fence), and invalid JSON — always with a `PlanOutputParseError`
    describing why.
    """
    text = raw_output.strip()
    if not text:
        raise PlanOutputParseError("Provider produced no output")

    fence_count = text.count("```")
    if fence_count == 0:
        candidate = text
    elif fence_count == 2:
        fence_match = _FENCE_RE.match(text)
        if not fence_match:
            raise PlanOutputParseError(
                "Provider output mixes prose with a code fence; expected raw "
                "JSON or a single JSON code fence with nothing else outside it"
            )
        candidate = fence_match.group("body")
    else:
        raise PlanOutputParseError(
            "Provider output contains more than one code fence; expected raw "
            "JSON or a single JSON code fence"
        )

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise PlanOutputParseError(f"Provider output is not valid JSON: {e}") from e


REQUIRED_CHECKPOINT_INPUT_FIELDS = ("id", "name", "scope", "exit_criteria")


def normalize_checkpoints(data: Any) -> list[dict]:
    """Extract and normalize checkpoints from parsed provider JSON.

    Accepts either `{"checkpoints": [...]}` or a bare `[...]` list. Each
    checkpoint is rebuilt with `new_checkpoint` so status/attempts/review_notes
    always start canonical, regardless of what the provider returned.
    """
    checkpoints = data.get("checkpoints") if isinstance(data, dict) else data

    if not isinstance(checkpoints, list) or not checkpoints:
        raise PlanOutputParseError(
            "Provider output must contain a non-empty 'checkpoints' list"
        )

    normalized: list[dict] = []
    for idx, cp in enumerate(checkpoints):
        if not isinstance(cp, dict):
            raise PlanOutputParseError(f"checkpoint #{idx} must be a JSON object")
        missing = [f for f in REQUIRED_CHECKPOINT_INPUT_FIELDS if f not in cp]
        if missing:
            raise PlanOutputParseError(
                f"checkpoint #{idx} is missing required field(s): {', '.join(missing)}"
            )
        if not isinstance(cp["exit_criteria"], list):
            raise PlanOutputParseError(
                f"checkpoint #{idx} ('{cp.get('id')}'): 'exit_criteria' must be a list"
            )
        normalized.append(
            new_checkpoint(cp["id"], cp["name"], cp["scope"], cp["exit_criteria"])
        )
    return normalized


def generate_plan_json(provider: Provider, feature_text: str, timeout: int) -> Any:
    """Run the init prompt through `provider` and parse its captured output.

    Composes `run_provider_captured` with `parse_plan_json`. Callers combine
    the result with `normalize_checkpoints` and `build_generated_payload` to
    get a payload ready for `validate_generated_payload`.
    """
    prompt = build_init_prompt(feature_text)
    stdout = run_provider_captured(provider, prompt, timeout)
    return parse_plan_json(stdout)
