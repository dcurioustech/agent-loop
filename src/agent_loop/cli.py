"""agent-loop command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import safety
from .git_ops import GitError
from .orchestrator import LoopHalted, run_loop
from .providers import get_provider, known_provider_names
from .providers.base import ProviderError
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
    run.add_argument(
        "--developer-model",
        default=None,
        help="Model for the developer CLI. Overrides plan 'models.developer'; "
        "if neither is set, the CLI picks its own default.",
    )
    run.add_argument(
        "--reviewer-model",
        default=None,
        help="Model for the reviewer CLI. Overrides plan 'models.reviewer'; "
        "if neither is set, the CLI picks its own default.",
    )
    run.add_argument("--max-review-attempts", type=int, default=3)
    run.add_argument("--timeout", type=int, default=1800, help="Per-agent-call timeout in seconds.")
    run.add_argument("--log-dir", type=Path, default=None)

    validate = sub.add_parser("validate", help="Schema-check the state file.")
    _add_state_arg(validate)

    status = sub.add_parser("status", help="One-line summary of each checkpoint.")
    _add_state_arg(status)

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


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
        state = load_state(args.state)
    except InvalidPlanState as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Precedence per role: CLI flag > plan 'models.<role>' > the CLI's default.
    dev_model = args.developer_model or state.model_for("developer")
    rev_model = args.reviewer_model or state.model_for("reviewer")

    try:
        dev = get_provider(args.developer, dev_model)
        rev = get_provider(args.reviewer, rev_model)
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
    def _role(p) -> str:
        return f"{p.name}({p.model})" if p.model else p.name

    print(f"### agent-loop | developer={_role(dev)} reviewer={_role(rev)} log={log_path}")

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
    except (InvalidPlanState, GitError) as e:
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
    parser.print_help(sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
