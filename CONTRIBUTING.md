# Contributing to agent-loop

Contributions are welcome through issues and pull requests.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest
python -m pytest
```

## Pull requests

- Work on a feature branch; the loop refuses to target `main` or `master`.
- Add or update tests for behavioral changes.
- Keep checkpoints independently reviewable and their exit criteria objective.
- Include the provider/CLI combinations used to verify provider changes.
- Review the generated audit logs and checkpoint state before pushing. The loop
  commits them by default, so keep credentials and private content out of both.

By contributing, you agree that your contribution is licensed under the MIT
License.
