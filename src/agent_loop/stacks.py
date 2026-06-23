"""Tech-stack detection and command templates."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

STACKS: dict[str, dict] = {
    "flutter": {
        "detect": ["pubspec.yaml"],
        "build_cmd": "flutter build",
        "test_cmd": "flutter test",
        "lint_cmd": "flutter analyze",
    },
    "go": {
        "detect": ["go.mod"],
        "build_cmd": "go build ./...",
        "test_cmd": "go test ./...",
        "lint_cmd": "golangci-lint run",
    },
    "node": {
        "detect": ["package.json"],
        "build_cmd": "npm run build",
        "test_cmd": "npm test",
        "lint_cmd": "npm run lint",
    },
    "rust": {
        "detect": ["Cargo.toml"],
        "build_cmd": "cargo build",
        "test_cmd": "cargo test",
        "lint_cmd": "cargo clippy",
    },
    "python": {
        "detect": ["pyproject.toml", "requirements.txt", "setup.py"],
        "build_cmd": None,
        "test_cmd": "pytest",
        "lint_cmd": "ruff check .",
    },
    "make": {
        "detect": ["Makefile"],
        "build_cmd": "make build",
        "test_cmd": "make test",
        "lint_cmd": "make lint",
    },
}


def detect_stack(project_dir: Path) -> Optional[str]:
    """Return the first matching stack name, or None if unrecognised."""
    for name, cfg in STACKS.items():
        if name == "make":
            continue
        for marker in cfg["detect"]:
            if (project_dir / marker).is_file():
                return name
    if (project_dir / "Makefile").is_file():
        return "make"
    return None


def known_stack_names() -> list[str]:
    return list(STACKS.keys())
