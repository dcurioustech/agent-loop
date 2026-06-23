"""Tests for stack detection and template data."""
from pathlib import Path

import pytest

from agent_loop.stacks import STACKS, detect_stack, known_stack_names


@pytest.fixture()
def tmp_project(tmp_path):
    return tmp_path


def _touch(directory: Path, name: str) -> None:
    (directory / name).touch()


def test_detect_flutter(tmp_project):
    _touch(tmp_project, "pubspec.yaml")
    assert detect_stack(tmp_project) == "flutter"


def test_detect_go(tmp_project):
    _touch(tmp_project, "go.mod")
    assert detect_stack(tmp_project) == "go"


def test_detect_node(tmp_project):
    _touch(tmp_project, "package.json")
    assert detect_stack(tmp_project) == "node"


def test_detect_rust(tmp_project):
    _touch(tmp_project, "Cargo.toml")
    assert detect_stack(tmp_project) == "rust"


def test_detect_python_pyproject(tmp_project):
    _touch(tmp_project, "pyproject.toml")
    assert detect_stack(tmp_project) == "python"


def test_detect_python_requirements(tmp_project):
    _touch(tmp_project, "requirements.txt")
    assert detect_stack(tmp_project) == "python"


def test_detect_make_fallback(tmp_project):
    _touch(tmp_project, "Makefile")
    assert detect_stack(tmp_project) == "make"


def test_detect_none(tmp_project):
    assert detect_stack(tmp_project) is None


def test_known_stack_names():
    names = known_stack_names()
    for expected in ("flutter", "go", "node", "rust", "python", "make"):
        assert expected in names


def test_all_stacks_have_test_cmd():
    for name, cfg in STACKS.items():
        assert "test_cmd" in cfg, f"stack {name!r} missing test_cmd"
        assert cfg["test_cmd"], f"stack {name!r} has empty test_cmd"


def test_make_not_detected_before_others(tmp_project):
    # A project with both Makefile and go.mod should be detected as 'go', not 'make'
    _touch(tmp_project, "Makefile")
    _touch(tmp_project, "go.mod")
    assert detect_stack(tmp_project) == "go"
