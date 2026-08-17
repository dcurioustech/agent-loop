# Repo logs and enhanced tracing implementation plan

## Goal

Change the default log directory from `/tmp/agent_loop_logs` to `./logs` in the repository directory, while preserving `LOG_DIR` environment variable overrides. Enhance log output to include explicit agent traces, commit statements (with commit hashes), review comments, and approval comments.

## Log location changes

- Update `safety.default_log_dir()` to check `LOG_DIR` env var first, then attempt to resolve `git_ops.repo_root() / "logs"`, falling back to `Path("logs")` if outside a git repository.
- Ensure `logs/` is ignored in `.gitignore`.

## Log content enhancements

Update `orchestrator.py` and `git_ops.py` to output structured entries tee'd to the log file:
- **Agent traces**: Log when developer/reviewer agent starts, prompt details, and completion traces.
- **Commit statements**: Log git commit actions with commit message and commit hash, or log when no commit was required.
- **Review comments**: Log specific review feedback/notes submitted by the reviewer.
- **Approval comments**: Log checkpoint approval status and approval summary/notes.

## Verification

Add unit tests covering log directory resolution, trace outputs, commit statement logging, and review/approval log statements. Ensure all `pytest` tests pass cleanly.
