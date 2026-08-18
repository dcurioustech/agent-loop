# Contributing to agent-loop

Thank you for helping improve agent-loop. Bug reports, documentation fixes,
tests, and code changes are welcome.

By participating, you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).
Please report security vulnerabilities privately as described in
[SECURITY.md](SECURITY.md), rather than opening a public issue.

## Before you start

For a substantial change, open an issue before investing significant effort.
Describe the problem, the proposed behavior, and any alternatives you
considered. Small fixes can go directly to a pull request.

Search existing issues and pull requests first to avoid duplicate work. Keep
each contribution focused on one concern.

## Development setup

agent-loop requires Python 3.9 or newer. Create an isolated environment and
install the project in editable mode with its test dependency:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . pytest
```

Run the test suite from the repository root:

```bash
python -m pytest
```

The external coding-agent CLIs are not needed for the unit tests; tests mock
provider invocations where appropriate.

## Making a change

1. Fork the repository and create a descriptive branch from the default branch.
2. Add or update tests for behavior changes.
3. Follow the existing code style: use type hints, small focused functions,
   and clear docstrings where behavior is not self-explanatory.
4. Update the README or other documentation when user-facing behavior changes.
5. Run `python -m pytest` and make sure the complete suite passes.
6. Commit with a concise, imperative subject that explains the change.

Do not commit credentials, provider transcripts containing secrets, virtual
environments, or generated local artifacts. In particular, review audit logs
carefully before publishing them; the README explains their persistence and
redaction limitations.

## Pull requests

A pull request should:

- explain the problem and the chosen solution;
- link related issues (for example, `Closes #123`);
- describe user-visible changes and compatibility considerations;
- list the checks you ran; and
- contain tests and documentation appropriate to the change.

Maintainers may request revisions to keep the project safe, focused, and
maintainable. All automated checks and review conversations should be resolved
before merge. Contributions are accepted under the project's
[MIT License](LICENSE).

## Reporting bugs

Include the agent-loop version or commit, Python version, operating system,
provider names, the smallest reproducible plan, the command you ran, expected
behavior, and actual behavior. Remove tokens and other sensitive material from
logs before attaching them.
