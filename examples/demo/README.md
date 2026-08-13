# Meetup demo runbook

A self-contained scaffold for demoing `agent-loop` live: a tiny word-frequency
CLI that's missing a `--top N` flag, plus a `plan_checkpoints.json` that
gives a developer-agent and a reviewer-agent real work to do implementing it.

Sequenced to hit agent-loop's safety refusals **deterministically, with no
agent CLI calls needed for most of them** — the exact commands and expected
output below were verified against this repo's current code, not guessed.

Budget: ~6-7 minutes of the 20-minute talk.

## 0. Setup (do this before the talk, not live)

`agent-loop` commits per checkpoint, so this scaffold needs to be its **own**
git repo — not nested inside your `agent-loop` checkout (a nested `.git`
shows up as an embedded-repo gitlink and just causes confusion). Copy it out:

```
cp -r examples/demo ~/agent-loop-demo
cd ~/agent-loop-demo
git init -b main
git add -A
git commit -m "initial scaffold"
```

Install `agent-loop` itself (from your `agent-loop` checkout) and confirm the
provider CLI(s) you'll use are on PATH — see the main `README.md`. Run
`make test` once here to see the baseline: `TestWordCounts` /
`TestFormatCounts` pass, `TestMostCommon` / `TestTopFlag` fail — that's the
real, verifiable feature work the checkpoints below implement.

**Don't run as root** — `claude --dangerously-skip-permissions` (and likely
the other CLIs' equivalent flags) refuse outright under root/sudo. Not an
agent-loop thing, just a thing to not discover live.

## 1. Deterministic opener — no agents involved (~1 min)

```
agent-loop validate
agent-loop status
```

Shows the checkpoint lifecycle (`pending` / `built` / `approved`) and schema
validation with zero LLM calls — safe, fast, reliable way to open the demo.

## 2. Guaranteed refusal #1 — protected branch (~30s)

The branch guard checks the `"branch"` field **declared in the plan file**,
not whatever branch you currently have checked out — so this is the cheapest
possible refusal to demo, and fails before touching git or any provider CLI
at all:

```
agent-loop validate --state plan_checkpoints.protected-branch.json
```

```
error: Refusing protected target branch: main
```

`plan_checkpoints.protected-branch.json` is a demo-only prop shipped
alongside the real plan, purely so this refusal is guaranteed on stage
regardless of which branch you happen to be on.

## 3. Guaranteed refusal #2 — danger gate not set (~30s)

Back to the real plan. Preflight (is the CLI installed?) and the danger-gate
check both happen *before* agent-loop ever looks at git state, so this fires
next even though the worktree is currently clean:

```
agent-loop run --developer claude --reviewer claude
```

(swap in whichever providers you have installed — same idea).

```
error: These providers are assigned to a role that writes files, but their
write-mode env gate is not set:

  claude    set ALLOW_DANGEROUS_CLAUDE=1
  ...

Unattended runs would otherwise block on a permission prompt.
Re-run with the missing env vars exported.
```

No agent CLI is invoked — `agent-loop` won't risk an unattended run stalling
on a permission prompt from the underlying CLI.

## 4. Guaranteed refusal #3 — dirty worktree (~30s)

```
export ALLOW_DANGEROUS_CLAUDE=1     # only the provider(s) you're using
touch scratch.txt
agent-loop run --developer claude --reviewer claude
```

```
error: Worktree must be clean before running the loop.
Outstanding changes:
?? scratch.txt
```

This one only fires *after* the danger gate is set (it's checked inside the
loop itself, right after the protected-branch check) — prevents the agent
from committing pre-existing unrelated changes under a checkpoint's message.

```
rm scratch.txt
```

## 5. The real run + audit trail (~3-4 min)

Worktree is clean, danger gate is set from step 4:

```
agent-loop run --developer claude --reviewer claude \
  --log-dir ./demo-logs --max-review-attempts 2
```

Narrate while it runs: developer implements `cp1_most_common`, commits,
reviewer checks it against the exit criteria and either approves or sends it
back with notes — if it does, that's a live rejection-then-revision cycle,
worth pausing on. If the reviewer approves on the first pass, that's fine
too — the interesting part is what happens next either way.

**Audit trail beat** — as soon as there's at least one completed checkpoint:

```
ls ./demo-logs
cat ./demo-logs/*.log | less     # or just `cat` the newest file
```

Every developer/reviewer/revision prompt and the agent's full raw response is
in there, timestamped, one file per `run` invocation — point out this is what
you'd hand a teammate (or an auditor) to answer "what did the AI actually do
and why did it think the checkpoint was done," without re-running anything.

### Fallback if nothing rejects on stage

LLM reviewers being picky is common but not guaranteed. If you want to
*guarantee* showing the halt-after-max-attempts behavior without gambling on
a live rejection, don't try to force it live — instead say "here's the test
that proves this deterministically" and run, back in the `agent-loop` repo
itself:

```
pytest tests/test_orchestrator.py -k halt -v
```

That's `test_loop_halts_when_max_review_attempts_exceeded` — same mechanism,
zero risk of an anticlimactic pass on stage.

## 6. Close (~30s)

```
agent-loop status
```

Point out: even with everything approved, nothing merged to `main`
automatically — that's still a deliberate human step. Wrap with "no merge
happened, no surprises."

## Gotchas to rehearse around

- `ALLOW_DANGEROUS_*` is a shell export, not persisted anywhere — if you
  switch terminal tabs mid-demo you'll trip refusal #3 again unintentionally.
- If a run gets hard-killed (not a clean Ctrl-C), `agent-loop`'s lockfile can
  get left behind and the next `run` fails with `LockHeld`. Know the lock
  path ahead of time so you can `rmdir` it live instead of stalling — prefer
  a graceful Ctrl-C over `kill -9` if you need to abort during rehearsal.
- The branch guard checks the plan file's declared branch, *not* your current
  git checkout — don't assume "I'm on `main` right now" is what triggers it.
