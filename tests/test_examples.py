"""Smoke-test the shipped example so it can't silently drift from the schema."""
from __future__ import annotations

from pathlib import Path

from agent_loop.state import load_state

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "plan_checkpoints.example.json"


def test_example_loads_and_exercises_every_status():
    state = load_state(EXAMPLE)
    statuses = {cp["status"] for cp in state.checkpoints}
    # Example should cover the full status spectrum so README readers see
    # what each one looks like in real data.
    assert statuses == {"pending", "built", "approved"}


def test_example_demonstrates_per_checkpoint_override():
    state = load_state(EXAMPLE)
    # phase1 has its own test_cmd; phase0 falls back to the project default.
    assert state.command_for("phase1", "test_cmd") != state.project.get("test_cmd")
    assert state.command_for("phase0", "test_cmd") == state.project.get("test_cmd")


def test_example_is_project_agnostic():
    # Don't bake a specific stack (flutter, npm, cargo) into the public example.
    raw = EXAMPLE.read_text().lower()
    for stack in ("flutter", "npm ", "cargo ", "go test", "pytest"):
        assert stack not in raw, f"example shouldn't mention {stack!r}"


# ---------------------------------------------------------------------------
# Stack-specific examples
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
STACK_EXAMPLES = ["flutter", "go", "node", "rust", "python"]


def test_stack_examples_valid():
    for name in STACK_EXAMPLES:
        path = EXAMPLES_DIR / f"{name}.example.json"
        assert path.exists(), f"missing example: {path}"
        state = load_state(path)
        assert len(state.checkpoints) >= 1
        statuses = {cp["status"] for cp in state.checkpoints}
        assert statuses <= {"pending", "built", "approved"}


def test_stack_examples_have_test_cmd():
    for name in STACK_EXAMPLES:
        path = EXAMPLES_DIR / f"{name}.example.json"
        state = load_state(path)
        # At least one checkpoint or the project block should resolve a test_cmd
        assert state.project.get("test_cmd") or any(
            cp.get("test_cmd") for cp in state.checkpoints
        ), f"{name}.example.json has no test_cmd anywhere"
