"""The checkpoint-gated developer/reviewer loop."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import git_ops, safety
from .providers.base import Provider
from .state import PlanState, load_state


class LoopHalted(RuntimeError):
    """Raised when a checkpoint cannot be approved within max_review_attempts."""


def _log_event(event: str, **fields: Any) -> None:
    """Print a stable, machine-readable event captured by the loop log tee."""
    print(
        f"[agent-loop] {event} "
        f"{json.dumps({'event': event, **fields}, sort_keys=True, default=str)}",
        flush=True,
    )


def _run_agent(
    *,
    provider: Provider,
    role: str,
    checkpoint: str,
    prompt: str,
    timeout: int,
    run_kind: str,
) -> int:
    """Run an agent while emitting start, prompt, and completion trace events."""
    common = {
        "checkpoint": checkpoint,
        "provider": provider.name,
        "role": role,
        "run_kind": run_kind,
    }
    _log_event("AGENT_TRACE", action="start", timeout_seconds=timeout, **common)
    _log_event("AGENT_TRACE", action="prompt", prompt=prompt, **common)
    try:
        return_code = provider.run(prompt, timeout=timeout)
    except Exception as exc:
        _log_event(
            "AGENT_TRACE",
            action="finish",
            error=str(exc),
            error_type=type(exc).__name__,
            outcome="error",
            **common,
        )
        raise

    _log_event(
        "AGENT_TRACE",
        action="finish",
        outcome="completed",
        return_code=return_code,
        **common,
    )
    return return_code


# ---------------------------------------------------------------------------
# Prompt templates — project-agnostic; commands come from state.project.
# ---------------------------------------------------------------------------


def _criteria_block(checkpoint: dict) -> str:
    return "\n".join(f"- {c}" for c in checkpoint["exit_criteria"])


def _verify_clause(state: PlanState, cid: str) -> str:
    if not state.verify_in_review:
        return ""
    parts: list[str] = []
    for label, field_name in (
        ("Run the build", "build_cmd"),
        ("Run the tests", "test_cmd"),
        ("Run the linter", "lint_cmd"),
    ):
        cmd = state.command_for(cid, field_name)
        if cmd:
            parts.append(f"- {label}: `{cmd}`")
    if not parts:
        return ""
    return (
        "\n\nIndependently verify the work by running these project commands:\n"
        + "\n".join(parts)
    )


def developer_prompt(state: PlanState, cid: str) -> str:
    cp = state.get(cid)
    return f"""You are executing the agreed plan in {state.plan_file}.
Work ONLY on checkpoint '{cid}' ({cp['name']}). Do not start any later phase.

Scope:
{cp['scope']}

This phase is complete only when ALL of these exit criteria are objectively met:
{_criteria_block(cp)}

When finished, in {state.path.name} set checkpoint '{cid}' status to 'built' and
write a concise summary (files touched, tests added, how each exit criterion is
met) into its 'review_notes' field.
"""


def reviewer_prompt(state: PlanState, cid: str) -> str:
    cp = state.get(cid)
    verify = _verify_clause(state, cid)
    return f"""Review ONLY checkpoint '{cid}' ({cp['name']}) of the plan in {state.plan_file}.
Read {state.path.name} for the developer's 'review_notes', then independently
verify the work in the repo.{verify}

Scope for this checkpoint:
{cp['scope']}

Approve ONLY if every one of these exit criteria is genuinely met:
{_criteria_block(cp)}

If all pass: set checkpoint '{cid}' status to 'approved' in {state.path.name}.
If a criterion genuinely fails: set status to 'built' and write SPECIFIC,
actionable, criterion-referenced fixes into 'review_notes'. Do not nitpick style
or request scope beyond this phase.

Do not merge to main/master. All final merging must wait for explicit human approval.
"""


def revision_prompt(state: PlanState, cid: str, review_notes: str) -> str:
    return f"""The reviewer did NOT approve checkpoint '{cid}' of the plan.
Apply ONLY these requested fixes — do not add scope or pull work from later phases:

{review_notes}

When done, in {state.path.name} set checkpoint '{cid}' status back to 'built' and
update 'review_notes' describing exactly what you changed.

Do not merge to main/master. All final merging must wait for explicit human approval.
"""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_loop(
    *,
    state_path: Path,
    developer: Provider,
    reviewer: Provider,
    max_review_attempts: int = 3,
    timeout: int = 1800,
    log_dir: Optional[Path] = None,
) -> None:
    state = load_state(state_path)
    git_ops.ensure_branch(state.branch)
    git_ops.require_clean_worktree()

    log_dir = log_dir or safety.default_log_dir()

    for cid in state.ids():
        cp = state.get(cid)
        if cp["status"] == "approved":
            print(f">> {cid} already approved — skip", flush=True)
            _log_event(
                "APPROVAL_COMMENT",
                approval_status="approved",
                checkpoint=cid,
                comment=cp["review_notes"],
                source="existing_checkpoint",
            )
            continue

        print(
            f"\n============================================================"
            f"\n  CHECKPOINT {cid} — {cp['name']}"
            f"\n============================================================",
            flush=True,
        )

        if cp["status"] != "built":
            _run_agent(
                provider=developer,
                role="developer",
                checkpoint=cid,
                prompt=developer_prompt(state, cid),
                timeout=timeout,
                run_kind="build",
            )
            git_ops.commit_checkpoint_changes(f"{cid}: built", log_dir=log_dir)
            state = load_state(state_path)  # reload after agent mutation
        else:
            print(f">> {cid} already built — resuming at review gate", flush=True)

        while True:
            cp = state.get(cid)
            attempts = cp["attempts"] + 1
            state.set_field(cid, "attempts", attempts)
            state.save()
            print(f"--- {cid} review attempt {attempts}/{max_review_attempts} ---", flush=True)

            _run_agent(
                provider=reviewer,
                role="reviewer",
                checkpoint=cid,
                prompt=reviewer_prompt(state, cid),
                timeout=timeout,
                run_kind="review",
            )
            state = load_state(state_path)
            reviewed_checkpoint = state.get(cid)
            status = reviewed_checkpoint["status"]
            notes = reviewed_checkpoint["review_notes"]
            _log_event(
                "REVIEW_COMMENT",
                checkpoint=cid,
                comment=notes,
                review_attempt=attempts,
                status=status,
            )

            if status == "approved":
                print(f">> {cid} APPROVED", flush=True)
                _log_event(
                    "APPROVAL_COMMENT",
                    approval_status="approved",
                    checkpoint=cid,
                    comment=notes,
                    review_attempt=attempts,
                    source="reviewer",
                )
                git_ops.commit_checkpoint_changes(f"{cid}: approved", log_dir=log_dir)
                break

            if attempts >= max_review_attempts:
                raise LoopHalted(
                    f"{cid} not approved after {attempts} attempts — halting for human review."
                )

            _run_agent(
                provider=developer,
                role="developer",
                checkpoint=cid,
                prompt=revision_prompt(state, cid, notes),
                timeout=timeout,
                run_kind="revision",
            )
            git_ops.commit_checkpoint_changes(
                f"{cid}: revision {attempts}", log_dir=log_dir
            )
            state = load_state(state_path)

    print(f"\n### All checkpoints approved on branch {state.branch}.", flush=True)
    print(
        "### No merge was performed. Review the branch and merge to main/master "
        "only after explicit human approval.",
        flush=True,
    )
