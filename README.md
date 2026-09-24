# agent-loop

A checkpoint-gated developer/reviewer loop. One coding-agent CLI plays **developer** and writes the code for the current checkpoint; another plays **reviewer** and approves or sends it back. The loop commits per checkpoint, refuses to run on `main`/`master`, and halts after a configurable number of failed review attempts.

Originally extracted from a Flutter project's `run_loop.sh`. Project-agnostic: build / test / lint commands are declared per project in `plan_checkpoints.json`.

## Install

```console
pipx install agent-loop
# or
uv tool install agent-loop
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
agent-loop validate                                  # schema-check the state file
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
ALLOW_AUTO_MODE_CLAUDE=1   # claude --dangerously-skip-permissions
ALLOW_AUTO_MODE_CODEX=1    # codex exec --dangerously-bypass-approvals-and-sandbox
ALLOW_AUTO_MODE_GROK=1     # grok --always-approve
ALLOW_AUTO_MODE_ANTIGRAVITY=1  # agy --dangerously-skip-permissions
```

The loop refuses to start unless the assigned provider's auto mode gate is set, so unattended runs cannot stall on a permission prompt.

## Run logs and the audit trail

Every run writes a log to `loop_<YYYYMMDD>_<HHMMSS>.log`. Where that lands is resolved in this order:

1. `--log-dir <path>` — explicit CLI flag, wins over everything.
2. `LOG_DIR=<path>` — environment override.
3. `<repo-root>/logs` — the default, resolved from the git root regardless of your current working directory (falls back to a relative `logs/` outside a git repo).

### Logs are committed as audit artifacts

Logs under the repository are **tracked and committed**, not ignored. The loop commits them alongside code at each checkpoint (`built`, `revision`, `approved`) and on every exit path (`audit: halted`, `audit: run failed`, `audit: completed run`), so a run's history survives in git even when it fails.

Two consequences worth knowing:

- **Each commit holds a partial log.** The log is still being appended to while the loop commits it, so a checkpoint commit captures the log *as of that moment*. The closing `audit:` commit flushes the tail. Bytes written after that land in the next run's first commit — partial by design, never lost.
- **A modified log does not block the next run.** The preflight worktree check ignores changes under the log directory (and only there); real source changes still refuse to start the loop.

Point `--log-dir` outside the repository and the loop warns that logs will not be committed, then runs normally.

### What the log contains

Agent stdout and stderr are streamed into the log as well as to your terminal, so the log can hold the agents' actual working output, not just the loop's own bookkeeping — subject to `--audit-level`, below. Alongside it the loop emits structured, one-line JSON events that are easy to grep or parse:

| Event | Emitted when |
| --- | --- |
| `AGENT_TRACE` | A developer/reviewer invocation starts, its prompt, and its result (`action` is `start`, `prompt`, or `finish`) |
| `REVIEW_COMMENT` | The reviewer returns, carrying its notes and resulting status |
| `APPROVAL_COMMENT` | A checkpoint is approved, or skipped because it already was |
| `COMMIT_STATEMENT` | A commit is made (with hash) or found unnecessary |
| `RUN_OUTCOME` | The run ends: `completed`, `halted`, or `failed` |

```console
$ grep RUN_OUTCOME logs/loop_20260816_101500.log
[agent-loop] RUN_OUTCOME {"branch": "feature-x", "event": "RUN_OUTCOME", "outcome": "completed"}
```

### `--audit-level`: how much of that ends up in git

Because logs are committed, anything an agent prints — or writes into a checkpoint's `review_notes` — becomes part of permanent git history the moment its commit is made. `--audit-level` controls how much of that actually reaches the log:

| Level | Raw agent stdout/stderr | Prompts, review notes, comments | Structured events |
| --- | --- | --- | --- |
| `full` | logged as-is | logged as-is | logged |
| `redacted` | scrubbed for known secret shapes first | scrubbed first | logged |
| `off` (default) | not logged at all | replaced with a `<suppressed: N chars>` placeholder | logged |

Resolution order: `--audit-level <level>` flag, then `AGENT_LOOP_AUDIT_LEVEL` env var, then `off`.

```console
agent-loop run --audit-level full       # everything, unfiltered
agent-loop run --audit-level redacted   # best-effort secret scrub
agent-loop run                          # off — structured log events only (default)
```

At every level, agents still receive the real, unredacted prompt — `--audit-level` only changes what gets *logged*, never what an agent is told to do. The state file retains `review_notes` as written by the agents and is committed with checkpoint changes, regardless of audit level.

**`redacted` is a best-effort net, not a guarantee.** It catches known secret *shapes* — AWS access keys, GitHub/Slack/OpenAI tokens, bearer tokens, PEM private-key blocks, `key: value`-style assignments — via regex over live, line-streamed subprocess output. It cannot catch a project's own custom secret formats, and a secret split across two flushed writes can slip through. Treat committed logs as something a human should skim before pushing, not as pre-cleared for a public remote. `off` suppresses raw content in the log, but does not redact the state file.

The `--log-dir` worktree exemption above only ever tolerates changes to log *files*; it has no bearing on what those files contain — that's entirely `--audit-level`'s job.

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

Download the example as your starting point and edit `branch`, `plan_file`, and the project block to match your stack:

```
curl -L https://raw.githubusercontent.com/dcurioustech/agent-loop/main/examples/plan_checkpoints.example.json \
  -o plan_checkpoints.json
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
