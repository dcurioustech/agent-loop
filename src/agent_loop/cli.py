"""agent-loop command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import safety
from .orchestrator import LoopHalted, run_loop
from .providers import get_provider, known_provider_names
from .providers.base import ProviderError
from .stacks import STACKS, detect_stack, known_stack_names
from .state import InvalidPlanState, load_state

DEFAULT_STATE = Path("plan_checkpoints.json")


def _add_state_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"Path to plan_checkpoints.json (default: {DEFAULT_STATE})",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-loop",
        description="Checkpoint-gated developer/reviewer loop across coding-agent CLIs.",
    )
    sub = parser.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="Run the developer/reviewer loop.")
    _add_state_arg(run)
    run.add_argument(
        "--developer",
        choices=known_provider_names(),
        default="claude",
        help="CLI to play the developer role (default: claude).",
    )
    run.add_argument(
        "--reviewer",
        choices=known_provider_names(),
        default="codex",
        help="CLI to play the reviewer role (default: codex).",
    )
    run.add_argument("--max-review-attempts", type=int, default=3)
    run.add_argument("--timeout", type=int, default=1800, help="Per-agent-call timeout in seconds.")
    run.add_argument("--log-dir", type=Path, default=None)

    validate = sub.add_parser("validate", help="Schema-check the state file.")
    _add_state_arg(validate)

    status = sub.add_parser("status", help="One-line summary of each checkpoint.")
    _add_state_arg(status)

    init_p = sub.add_parser(
        "init", help="Generate plan_checkpoints.json for this project."
    )
    init_p.add_argument(
        "--stack",
        choices=known_stack_names(),
        default=None,
        help="Tech stack (auto-detected if omitted).",
    )
    init_p.add_argument("--branch", default="feature-branch")
    init_p.add_argument("--plan-file", default="docs/implementation_plan.md")
    init_p.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"Output path (default: {DEFAULT_STATE})",
    )
    init_p.add_argument(
        "--force", action="store_true", help="Overwrite existing file."
    )

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


_STARTER_CHECKPOINT = {
    "id": "phase0",
    "name": "Project Setup",
    "status": "pending",
    "scope": "Create the feature branch and document the agreed approach in the plan file.",
    "exit_criteria": [
        "Feature branch exists and is checked out",
        "Plan file documents the agreed approach",
    ],
    "attempts": 0,
    "review_notes": "",
}


def _cmd_init(args) -> int:
    if args.state.exists() and not args.force:
        print(f"error: {args.state} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    stack_name = args.stack or detect_stack(Path.cwd()) or "make"
    cfg = STACKS[stack_name]

    project: dict = {
        k: cfg[k]
        for k in ("build_cmd", "test_cmd", "lint_cmd")
        if cfg.get(k) is not None
    }
    project["verify_in_review"] = True

    payload = {
        "plan_file": args.plan_file,
        "branch": args.branch,
        "project": project,
        "checkpoints": [_STARTER_CHECKPOINT],
    }

    args.state.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Created {args.state} (stack: {stack_name})")
    return 0


def _cmd_validate(args) -> int:
    try:
        state = load_state(args.state)
    except InvalidPlanState as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"OK  {args.state} ({len(state.checkpoints)} checkpoints, branch={state.branch})")
    for cp in state.checkpoints:
        print(f"  {cp['id']:10s} {cp['status']:9s} {cp['name']}")
    return 0


def _cmd_status(args) -> int:
    try:
        state = load_state(args.state)
    except InvalidPlanState as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"branch={state.branch}  plan={state.plan_file}  state={args.state}")
    for cp in state.checkpoints:
        attempts = cp.get("attempts", 0)
        print(f"  {cp['id']:10s} {cp['status']:9s} attempts={attempts}  {cp['name']}")
    return 0


def _cmd_run(args) -> int:
    try:
        dev = get_provider(args.developer)
        rev = get_provider(args.reviewer)
    except ProviderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        dev.preflight()
        rev.preflight()
        safety.require_danger_gates([dev, rev])
    except (ProviderError, safety.DangerGateError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    log_dir = args.log_dir or safety.default_log_dir()
    log_path = safety.open_log_file(log_dir)
    safety.tee_stdout_to(log_path)
    print(f"### agent-loop | developer={dev.name} reviewer={rev.name} log={log_path}")

    try:
        with safety.lockfile(safety.default_lock_path()):
            run_loop(
                state_path=args.state,
                developer=dev,
                reviewer=rev,
                max_review_attempts=args.max_review_attempts,
                timeout=args.timeout,
                log_dir=log_dir,
            )
    except safety.LockHeld as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except LoopHalted as e:
        print(f"halted: {e}", file=sys.stderr)
        return 1
    except InvalidPlanState as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "init":
        return _cmd_init(args)
    parser.print_help(sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
