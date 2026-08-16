# agent-loop

A checkpoint-gated developer/reviewer loop. One coding-agent CLI plays **developer** and writes the code for the current checkpoint; another plays **reviewer** and approves or sends it back. The loop commits per checkpoint, refuses to run on `main`/`master`, and halts after a configurable number of failed review attempts.

Originally extracted from a Flutter project's `run_loop.sh`. Project-agnostic: build / test / lint commands are declared per project in `plan_checkpoints.json`.

## Install

```
pipx install -e ~/Documents/workspace/agent-loop-tool
# or
uv tool install -e ~/Documents/workspace/agent-loop-tool
```

Requires at least one of these CLIs on PATH, depending on the roles you pick:

| Provider | Install                                |
|----------|----------------------------------------|
| claude   | <https://docs.claude.com/claude-code>  |
| codex    | <https://github.com/openai/codex>      |
| grok     | <https://docs.x.ai/docs/grok-cli>      |
| antigravity | <https://antigravity.google/docs/cli-overview> (binary: `agy`) |

## Usage

In a consumer repo containing `plan_checkpoints.json`:

```
agent-loop validate                                  # schema-check + CLI preflight
agent-loop status                                    # one-line summary per checkpoint
agent-loop run                                       # developer=claude, reviewer=codex (defaults)
agent-loop run --developer codex --reviewer claude
agent-loop run --developer grok  --reviewer antigravity
agent-loop run --developer antigravity --reviewer claude
agent-loop run --developer-model claude-opus-4-8 --reviewer-model gpt-5-codex
```

### Choosing the model

`--developer` / `--reviewer` pick the CLI; the *model* is resolved per role with this
precedence (first match wins):

1. `--developer-model` / `--reviewer-model` on the command line
2. a `models` block in `plan_checkpoints.json` (see schema below)
3. whatever default the CLI itself resolves (its config file / env / built-in)

So if you set nothing, each CLI keeps using its own default model — the loop never
overrides it. Model names are provider-specific, so a value pinned for one role only
makes sense for the CLI you assigned to that role. Under the hood the model is passed as
`--model <name>` (claude/grok) or `-m <name>` (codex/antigravity).

Safety envs (per provider, off by default):

```
ALLOW_DANGEROUS_CLAUDE=1   # claude --dangerously-skip-permissions
ALLOW_DANGEROUS_CODEX=1    # codex exec --dangerously-bypass-approvals-and-sandbox
ALLOW_DANGEROUS_GROK=1     # grok --always-approve
ALLOW_DANGEROUS_ANTIGRAVITY=1  # agy --dangerously-skip-permissions
```

The loop refuses to start unless the assigned provider's danger gate is set, so unattended runs cannot stall on a permission prompt.

## Generating a plan (`agent-loop init`)

`agent-loop init` turns a feature description into a schema-valid `plan_checkpoints.json`
by asking a coding-agent CLI to break it into ordered, independently reviewable
checkpoints. It runs the provider once, non-interactively, captures its output, and
only writes the state file after the result parses as JSON and passes the same
validation `load_state` applies — nothing is written on a provider error, a timeout,
malformed output, or a schema failure.

Plain-English input:

```
agent-loop init "Add a login page with email/password auth and a logout button"
```

Markdown input (e.g. an existing design doc):

```
agent-loop init --feature-file docs/feature.md
```

Either form accepts:

| Flag           | Default                                                              |
|----------------|-----------------------------------------------------------------------|
| `--state`      | `plan_checkpoints.json`                                               |
| `--branch`     | sanitized `feature/<slug>` derived from the feature text (or the `--feature-file` filename) |
| `--plan-file`  | `docs/implementation_plan.md` for plain-English input, or the `--feature-file` path for Markdown input |
| `--provider`   | `claude` — any provider from `agent-loop run`'s table can generate the plan |
| `--model`      | the provider CLI's own default |
| `--timeout`    | `1800` seconds |
| `--force`      | off — refuses to overwrite an existing `--state` file |

```
agent-loop init "Add CSV export to the reports page" \
  --provider codex --model gpt-5-codex --timeout 600 --branch feature/csv-export
```

By default `init` refuses to touch an existing state file so you don't accidentally
clobber checkpoint progress; pass `--force` to regenerate and overwrite it. A target
branch of `main`/`master` is rejected before the provider is ever invoked, same as
`agent-loop run`. Generated checkpoints always start `pending` with `attempts: 0` and
empty `review_notes`, regardless of what the provider returned for those fields.

A freshly generated file is immediately usable:

```
agent-loop init "Add a login page" && agent-loop validate && agent-loop status
```

## Bootstrapping a new consumer repo

Copy the shipped example as your starting point and edit `branch`, `plan_file`, and the project block to match your stack:

```
cp ~/Documents/workspace/agent-loop-tool/examples/plan_checkpoints.example.json \
   ./plan_checkpoints.json
agent-loop validate
```

The example covers all three checkpoint statuses (`pending` / `built` / `approved`) and shows a per-checkpoint `test_cmd` override on top of the project-level default.

## `plan_checkpoints.json` schema

```jsonc
{
  "plan_file": "docs/implementation_plan.md",
  "branch": "feature-branch",
  "project": {
    "build_cmd": "flutter build web --release",
    "test_cmd":  "flutter test",
    "lint_cmd":  "flutter analyze",
    "verify_in_review": true
  },
  "models": {                    // optional; per-role model, overridden by CLI flags
    "developer": "claude-opus-4-8",
    "reviewer":  "gpt-5-codex"
  },
  "checkpoints": [
    {
      "id": "phase0",
      "name": "...",
      "status": "pending",       // pending | built | approved
      "scope": "...",
      "exit_criteria": ["..."],   // non-empty; each entry a non-empty string
      "attempts": 0,              // non-negative integer
      "review_notes": ""
    }
  ]
}
```

Per-checkpoint `build_cmd` / `test_cmd` / `lint_cmd` overrides are also supported. The reviewer prompt includes these commands so the agent knows how to verify.
