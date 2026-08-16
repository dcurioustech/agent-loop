"""The checkpoint-gated developer/reviewer loop."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import git_ops, safety
from .providers.base import Provider
from .state import PlanState, load_state


class LoopHalted(RuntimeError):
    """Raised when a checkpoint cannot be approved within max_review_attempts."""


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
    log_dir = log_dir or safety.default_log_dir()
    git_ops.ensure_branch(state.branch)
    git_ops.require_clean_worktree(log_dir=log_dir)

    any_processed = False
    try:
        for cid in state.ids():
            cp = state.get(cid)
            if cp["status"] == "approved":
                print(f">> {cid} already approved — skip", flush=True)
                continue

            any_processed = True
            print(
                f"\n============================================================"
                f"\n  CHECKPOINT {cid} — {cp['name']}"
                f"\n============================================================",
                flush=True,
            )

            if cp["status"] != "built":
                print(
                    f"[agent-loop] [developer] [{cid}] starting initial build",
                    flush=True,
                )
                rc = developer.run(developer_prompt(state, cid), timeout=timeout)
                print(
                    f"[agent-loop] [developer] [{cid}] initial build finished (exit code: {rc})",
                    flush=True,
                )
                state = load_state(state_path)  # reload after agent mutation
                cp = state.get(cid)
                dev_notes = cp.get("review_notes", "").strip()
                if dev_notes:
                    print(
                        f"[agent-loop] [developer] [{cid}] notes:\n{dev_notes}",
                        flush=True,
                    )
                git_ops.commit_checkpoint_changes(
                    f"{cid}: built", log_dir=log_dir
                )
                state = load_state(state_path)
            else:
                print(
                    f">> {cid} already built — resuming at review gate",
                    flush=True,
                )

            while True:
                cp = state.get(cid)
                attempts = cp["attempts"] + 1
                state.set_field(cid, "attempts", attempts)
                state.save()
                print(
                    f"--- {cid} review attempt {attempts}/{max_review_attempts} ---",
                    flush=True,
                )
                print(
                    f"[agent-loop] [reviewer] [{cid}] starting review (attempt {attempts}/{max_review_attempts})",
                    flush=True,
                )
                rc = reviewer.run(reviewer_prompt(state, cid), timeout=timeout)
                print(
                    f"[agent-loop] [reviewer] [{cid}] review finished (exit code: {rc})",
                    flush=True,
                )
                state = load_state(state_path)
                status = state.get(cid)["status"]
                notes = state.get(cid).get("review_notes", "").strip()

                if status == "approved":
                    print(f">> {cid} APPROVED", flush=True)
                    if notes:
                        print(
                            f"[agent-loop] [reviewer] [{cid}] approval comment (attempt {attempts}/{max_review_attempts}):\n{notes}",
                            flush=True,
                        )
                    else:
                        print(
                            f"[agent-loop] [reviewer] [{cid}] approval comment (attempt {attempts}/{max_review_attempts}): Checkpoint approved",
                            flush=True,
                        )
                    git_ops.commit_checkpoint_changes(
                        f"{cid}: approved", log_dir=log_dir
                    )
                    break

                if notes:
                    print(
                        f"[agent-loop] [reviewer] [{cid}] review comment (attempt {attempts}/{max_review_attempts}):\n{notes}",
                        flush=True,
                    )
                else:
                    print(
                        f"[agent-loop] [reviewer] [{cid}] review comment (attempt {attempts}/{max_review_attempts}): Changes requested",
                        flush=True,
                    )

                if attempts >= max_review_attempts:
                    halt_msg = (
                        f"{cid} not approved after {attempts} attempts — "
                        "halting for human review."
                    )
                    print(f"[agent-loop] [{cid}] halted: {halt_msg}", flush=True)
                    git_ops.commit_checkpoint_changes(
                        f"{cid}: halted", log_dir=log_dir
                    )
                    raise LoopHalted(halt_msg)

                print(
                    f"[agent-loop] [developer] [{cid}] starting revision (attempt {attempts})",
                    flush=True,
                )
                rc = developer.run(
                    revision_prompt(state, cid, notes), timeout=timeout
                )
                print(
                    f"[agent-loop] [developer] [{cid}] revision finished (exit code: {rc})",
                    flush=True,
                )
                state = load_state(state_path)
                cp = state.get(cid)
                rev_notes = cp.get("review_notes", "").strip()
                if rev_notes:
                    print(
                        f"[agent-loop] [developer] [{cid}] revision notes:\n{rev_notes}",
                        flush=True,
                    )
                git_ops.commit_checkpoint_changes(
                    f"{cid}: revision {attempts}", log_dir=log_dir
                )
                state = load_state(state_path)

        if any_processed:
            print(
                f"\n### All checkpoints approved on branch {state.branch}.",
                flush=True,
            )
            print(
                "### No merge was performed. Review the branch and merge to main/master "
                "only after explicit human approval.",
                flush=True,
            )
            git_ops.commit_checkpoint_changes(
                "audit: completed run", log_dir=log_dir
            )
    except LoopHalted:
        raise
    except Exception as e:
        print(f"[agent-loop] run failed: {e}", flush=True)
        try:
            git_ops.commit_checkpoint_changes(
                "audit: run failed", log_dir=log_dir
            )
        except Exception:
            pass
        raise

