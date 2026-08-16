# `agent-loop init` implementation plan

## Goal

Add `agent-loop init`, which turns a feature described as plain English or a Markdown file into a validated `plan_checkpoints.json` that can be run by the existing developer/reviewer loop.

## Command contract

Support a positional feature description and a mutually exclusive `--feature-file` path. Add `--state`, `--branch`, `--plan-file`, `--provider`, `--model`, `--timeout`, and `--force`. The command must not overwrite an existing state file unless `--force` is supplied.

## Generation and validation

Use the selected agent provider to return JSON only. Capture its output, accept raw or fenced JSON, validate it against the same schema used by `load_state`, and write the formatted file only after validation succeeds. Generated checkpoints begin as `pending`, with `attempts: 0` and empty `review_notes`.

The branch comes from `--branch` or a sanitized `feature/<slug>` default. A Markdown input becomes the default `plan_file`; plain English uses the configurable/default plan path. Project commands remain absent unless they are explicitly supplied or confidently known.

## Safety and compatibility

Keep the existing developer/reviewer streaming behavior unchanged. Add a dedicated captured-output provider execution path for `init`. Refuse non-zero provider results, timeouts, invalid/mixed output, protected branches, and schema-invalid plans without writing the state file.

## Verification

Add isolated tests using a mocked provider process for input routing, CLI defaults/overrides, output parsing, schema failures, timeout/non-zero behavior, overwrite protection, and compatibility with `load_state`, `validate`, `status`, and `run`. Document both invocation forms in the README.
