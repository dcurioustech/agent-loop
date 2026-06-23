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
| gemini   | <https://github.com/google-gemini/gemini-cli> |

## Usage

In a consumer repo containing `plan_checkpoints.json`:

```
agent-loop validate                                  # schema-check + CLI preflight
agent-loop status                                    # one-line summary per checkpoint
agent-loop run                                       # developer=claude, reviewer=codex (defaults)
agent-loop run --developer codex --reviewer claude
agent-loop run --developer grok  --reviewer gemini
```

Safety envs (per provider, off by default):

```
ALLOW_DANGEROUS_CLAUDE=1   # claude --dangerously-skip-permissions
ALLOW_DANGEROUS_CODEX=1    # codex exec --dangerously-bypass-approvals-and-sandbox
ALLOW_DANGEROUS_GROK=1     # grok --always-approve
ALLOW_DANGEROUS_GEMINI=1   # gemini --approval-mode yolo
```

The loop refuses to start unless the assigned provider's danger gate is set, so unattended runs cannot stall on a permission prompt.

## Bootstrapping a new consumer repo

Run `agent-loop init` inside your project directory. It auto-detects the tech stack from marker files and writes a pre-filled `plan_checkpoints.json`:

```
agent-loop init                    # auto-detect stack
agent-loop init --stack go         # explicit stack
agent-loop init --branch my-feat   # custom branch name
agent-loop validate                # confirm schema is valid
```

Supported stacks (detected from marker files):

| Stack    | Detected by       | Default commands                              |
|----------|-------------------|-----------------------------------------------|
| flutter  | `pubspec.yaml`    | `flutter build` / `flutter test` / `flutter analyze` |
| go       | `go.mod`          | `go build ./...` / `go test ./...` / `golangci-lint run` |
| node     | `package.json`    | `npm run build` / `npm test` / `npm run lint` |
| rust     | `Cargo.toml`      | `cargo build` / `cargo test` / `cargo clippy` |
| python   | `pyproject.toml` etc. | `pytest` / `ruff check .`               |
| make     | `Makefile`        | `make build` / `make test` / `make lint`      |

Stack-specific example files live in `examples/` (e.g. `examples/go.example.json`). Alternatively, copy the generic example and edit manually:

```
cp ~/Documents/workspace/agent-loop-tool/examples/plan_checkpoints.example.json \
   ./plan_checkpoints.json
```

The generic example covers all three checkpoint statuses (`pending` / `built` / `approved`) and shows a per-checkpoint `test_cmd` override on top of the project-level default.

## `plan_checkpoints.json` schema

```jsonc
{
  "plan_file": "docs/implementation_plan.md",
  "branch": "feature-branch",
  "project": {
    "build_cmd": "make build",
    "test_cmd":  "make test",
    "lint_cmd":  "make lint",
    "verify_in_review": true
  },
  "checkpoints": [
    {
      "id": "phase0",
      "name": "...",
      "status": "pending",       // pending | built | approved
      "scope": "...",
      "exit_criteria": ["..."],
      "attempts": 0,
      "review_notes": ""
    }
  ]
}
```

Per-checkpoint `build_cmd` / `test_cmd` / `lint_cmd` overrides are also supported. The reviewer prompt includes these commands so the agent knows how to verify.
