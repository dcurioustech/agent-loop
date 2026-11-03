"""agent-loop command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import audit, errors, plan_init, safety
from .orchestrator import LoopHalted, run_loop
from .providers import get_provider, known_provider_names
from .providers.base import ProviderError
from .git_ops import GitError
from .state import PROTECTED_BRANCHES, InvalidPlanState, load_state

DEFAULT_STATE = Path("plan_checkpoints.json")


def _add_state_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"Path to plan_checkpoints.json (default: {DEFAULT_STATE})",
    )


def positive_int(value: str) -> int:
    """argparse type for counts and timeouts: a zero/negative one can never succeed.

    Named without a leading underscore because argparse builds its rejection
    message from `__name__` ("invalid positive_int value: 'abc'").
    """
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


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
    run.add_argument("--max-review-attempts", type=positive_int, default=3)
    run.add_argument(
        "--timeout",
        type=positive_int,
        default=1800,
        help="Per-agent-call timeout in seconds.",
    )
    run.add_argument("--log-dir", type=Path, default=None)
    run.add_argument(
        "--audit-level",
        choices=audit.AUDIT_LEVELS,
        default=None,
        help="How much of the agents' output is committed to the run log: "
        "'full' logs it as-is, 'redacted' scrubs known secret shapes first, "
        "'off' logs only structured events (no raw agent output or notes). "
        "Defaults to $AGENT_LOOP_AUDIT_LEVEL, or 'off' if that is unset — "
        "logs are git-committed audit artifacts, so this leans safe by "
        "default. Neither 'redacted' nor 'off' is a compliance guarantee; "
        "see README for what each level actually does.",
    )

    validate = sub.add_parser("validate", help="Schema-check the state file.")
    _add_state_arg(validate)

    status = sub.add_parser("status", help="One-line summary of each checkpoint.")
    _add_state_arg(status)

    init = sub.add_parser(
        "init",
        help="Generate plan_checkpoints.json from a feature description or Markdown file.",
    )
    init.add_argument(
        "feature",
        nargs="?",
        default=None,
        help="Plain-English feature description (mutually exclusive with --feature-file).",
    )
    init.add_argument(
        "--feature-file",
        type=Path,
        default=None,
        help="Path to a Markdown file describing the feature.",
    )
    _add_state_arg(init)
    init.add_argument(
        "--branch",
        default=None,
        help="Target branch (default: sanitized feature/<slug>).",
    )
    init.add_argument(
        "--plan-file",
        default=None,
        help="'plan_file' recorded in the state (default: the --feature-file path, or "
        f"{plan_init.DEFAULT_PLAN_FILE} for plain-English input).",
    )
    init.add_argument(
        "--provider",
        choices=known_provider_names(),
        default="claude",
        help="CLI used to generate the plan (default: claude).",
    )
    init.add_argument("--model", default=None, help="Model for the planning CLI.")
    init.add_argument(
        "--timeout",
        type=positive_int,
        default=1800,
        help="Provider call timeout in seconds.",
    )
    init.add_argument(
        "--force", action="store_true", help="Overwrite an existing state file."
    )

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _cmd_validate(args) -> int:
    try:
        state = load_state(args.state)
    except InvalidPlanState as e:
        errors.report(e)
        return 2

    print(f"OK  {args.state} ({len(state.checkpoints)} checkpoints, branch={state.branch})")
    for cp in state.checkpoints:
        print(f"  {cp['id']:10s} {cp['status']:9s} {cp['name']}")
    return 0


def _cmd_status(args) -> int:
    try:
        state = load_state(args.state)
    except InvalidPlanState as e:
        errors.report(e)
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
        errors.report(e)
        return 2

    # Precedence per role: CLI flag > plan 'models.<role>' > the CLI's default.
    dev_model = args.developer_model or state.model_for("developer")
    rev_model = args.reviewer_model or state.model_for("reviewer")

    try:
        dev = get_provider(args.developer, dev_model)
        rev = get_provider(args.reviewer, rev_model)
    except ProviderError as e:
        errors.report(e)
        return 2

    try:
        dev.preflight()
        rev.preflight()
        safety.require_auto_mode_gates([dev, rev])
    except (ProviderError, safety.AutoModeGateError) as e:
        errors.report(e)
        return 2

    try:
        audit_level = args.audit_level or audit.default_audit_level()
    except audit.InvalidAuditLevel as e:
        errors.report(e)
        return 2

    log_dir = args.log_dir or safety.default_log_dir()
    try:
        log_path = safety.open_log_file(log_dir)
        safety.tee_stdout_to(log_path)
    except OSError as e:
        errors.report(e, context=f"unable to create run log in {log_dir}")
        return 2
    def _role(p) -> str:
        return f"{p.name}({p.model})" if p.model else p.name

    print(
        f"### agent-loop | developer={_role(dev)} reviewer={_role(rev)} "
        f"log={log_path} audit-level={audit_level}"
    )

    try:
        with safety.lockfile(safety.default_lock_path()):
            run_loop(
                state_path=args.state,
                developer=dev,
                reviewer=rev,
                max_review_attempts=args.max_review_attempts,
                timeout=args.timeout,
                log_dir=log_dir,
                audit_level=audit_level,
            )
    except LoopHalted as e:
        errors.report(e)
        return 1
    except (safety.LockHeld, InvalidPlanState, GitError, ProviderError) as e:
        errors.report(e)
        return 2
    except OSError as e:
        errors.report(e, context="the loop stopped")
        return 2
    return 0


def _cmd_init(args) -> int:
    # Which source was *supplied*, not which one turned out to be usable: an
    # empty FEATURE alongside --feature-file is ambiguous input, not a
    # single-source invocation.
    have_text = args.feature is not None
    have_file = args.feature_file is not None

    if have_text and have_file:
        return errors.fail(
            "provide a feature description or --feature-file, not both",
            'Use either: agent-loop init "Describe your feature"',
            "or:         agent-loop init --feature-file docs/feature.md",
        )
    if not have_text and not have_file:
        return errors.fail(
            "provide a feature description or --feature-file",
            'Example: agent-loop init "Add a dark-mode toggle to settings"',
        )

    feature_text = (args.feature or "").strip()
    if have_text and not feature_text:
        return errors.fail(
            "feature description is empty",
            "Describe what you want built in a sentence or two.",
        )

    if have_file:
        if not args.feature_file.is_file():
            return errors.fail(
                f"feature file not found: {args.feature_file}",
                "Check the path (it is relative to the current directory).",
            )
        try:
            feature_text = args.feature_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            errors.report(e, context=f"unable to read feature file {args.feature_file}")
            return 2
        if not feature_text:
            return errors.fail(
                f"feature file is empty: {args.feature_file}",
                "Add a description of the feature to the file and retry.",
            )
        slug_source = args.feature_file.stem
        default_plan_file = str(args.feature_file)
    else:
        slug_source = feature_text
        default_plan_file = plan_init.DEFAULT_PLAN_FILE

    branch = args.branch or f"feature/{plan_init.slugify(slug_source)}"
    plan_file = args.plan_file or default_plan_file

    if branch in PROTECTED_BRANCHES:
        return errors.fail(
            f"refusing protected target branch: {branch}",
            "The loop never works directly on main/master; pass a feature branch "
            "with --branch, e.g. --branch feature/my-change.",
        )

    if args.state.exists() and not args.force:
        return errors.fail(
            f"{args.state} already exists; use --force to overwrite",
            "Or write the new plan elsewhere with --state PATH.",
        )

    try:
        provider = get_provider(args.provider, args.model)
        provider.preflight()
    except ProviderError as e:
        errors.report(e)
        return 2

    try:
        data = plan_init.generate_plan_json(provider, feature_text, args.timeout)
        checkpoints = plan_init.normalize_checkpoints(data)
        payload = plan_init.build_generated_payload(plan_file, branch, checkpoints)
        plan_init.validate_generated_payload(payload)
    except (
        plan_init.ProviderExecutionError,
        plan_init.PlanOutputParseError,
        InvalidPlanState,
    ) as e:
        errors.report(e)
        return 2

    try:
        plan_init.write_generated_payload(args.state, payload, force=args.force)
    except plan_init.StateFileExistsError:
        # Only reachable if the file appeared after the check above.
        return errors.fail(
            f"{args.state} already exists; use --force to overwrite",
            "Or write the new plan elsewhere with --state PATH.",
        )
    except OSError as e:
        errors.report(e, context=f"unable to write {args.state}")
        return 2

    print(
        f"wrote {args.state} (branch={branch}, plan_file={plan_file}, "
        f"{len(checkpoints)} checkpoints)"
    )
    return 0


_COMMANDS = {
    "run": _cmd_run,
    "validate": _cmd_validate,
    "status": _cmd_status,
    "init": _cmd_init,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS.get(args.cmd)
    if handler is None:
        parser.print_help(sys.stderr)
        raise SystemExit(2)
    # Last line of defence: whatever slips past the per-command handling is
    # still reported as a one-line message with a hint, never a traceback
    # (unless AGENT_LOOP_DEBUG=1 asks for one).
    try:
        return handler(args)
    except KeyboardInterrupt as e:
        errors.report(e)
        return errors.EXIT_INTERRUPTED
    except Exception as e:  # noqa: BLE001 - deliberately catch-all for the CLI
        errors.report(e)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
