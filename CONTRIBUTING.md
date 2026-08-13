# Contributing to agent-loop

Thanks for taking a look. This is a small, stdlib-only Python project — the bar for
contributing is meant to be low.

## Dev setup

```
git clone https://github.com/dcurioustech/agent-loop
cd agent-loop
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

No runtime dependencies are declared on purpose (`agent-loop` shells out to coding-agent
CLIs rather than calling any LLM API directly). Please don't add a runtime dependency
without opening an issue to discuss it first.

## Running tests

```
pip install pytest
pytest
```

The suite is stdlib/pytest only (no mocking framework beyond `monkeypatch`). Please add or
update tests for any behavior change — PRs without test coverage for the change they make
will likely be sent back for that before anything else.

## Project layout

```
src/agent_loop/
├── cli.py            # argparse entry point: validate / status / run subcommands
├── orchestrator.py   # the developer/reviewer loop itself
├── state.py           # PlanState: load/validate/mutate/save plan_checkpoints.json
├── safety.py           # lockfile, log-dir tee, danger-env gate enforcement
├── git_ops.py           # git subprocess wrappers (branch, clean-worktree, commit)
└── providers/
    ├── base.py           # abstract Provider: build_argv() / preflight() / run()
    └── <name>.py          # one file per coding-agent CLI wrapped
```

## Adding a new provider

Providers wrap a coding-agent CLI binary — see any file in `src/agent_loop/providers/` for
the pattern (e.g. `claude.py` or `codex.py`). To add one:

1. Create `src/agent_loop/providers/<name>.py` with a class subclassing `Provider`
   (`providers/base.py`), implementing `build_argv()` and `preflight()`.
2. Register it with the `@register` decorator from `providers/__init__.py`.
3. Add an `ALLOW_DANGEROUS_<NAME>` env-gate check consistent with the existing providers —
   `agent-loop` should never be able to run a provider unattended unless that gate is
   explicitly set to the literal string `"1"`.
4. Add it to `--developer`/`--reviewer` choices (`cli.py` picks these up from the registry
   automatically) and to the provider table in `README.md`.
5. Add tests in `tests/test_providers.py` mirroring the existing provider tests.

## Pull requests

- Keep PRs focused — one behavior change per PR is easier to review than a bundle.
- Run `pytest` locally before opening the PR.
- Explain *why*, not just *what*, in the PR description if the change isn't obvious from
  the diff.
- Safety-related behavior (the branch guard, dirty-worktree guard, danger-gate opt-in, and
  max-review-attempts halt) is core to the project's design — changes that weaken any of
  these by default need a clear justification in the PR description.

## Reporting bugs / requesting features

Open a GitHub issue. Include your OS, Python version, and which provider CLI(s) you were
using if the issue is provider-specific.
