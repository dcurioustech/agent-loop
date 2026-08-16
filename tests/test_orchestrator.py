"""Tests for the developer/reviewer loop.

Strategy: inject FakeProviders that record received prompts and mutate the
plan_checkpoints.json file the way real agents would. Stub out git_ops so the
loop can run against a temp file with no real repo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pytest

from agent_loop import orchestrator
from agent_loop.orchestrator import LoopHalted, run_loop
from agent_loop.providers.base import Provider
from agent_loop.state import load_state


def _plan(tmp_path: Path, status: str = "pending", attempts: int = 0) -> Path:
    p = tmp_path / "plan_checkpoints.json"
    p.write_text(
        json.dumps(
            {
                "plan_file": "docs/plan.md",
                "branch": "feature-x",
                "project": {
                    "test_cmd": "pytest -q",
                    "lint_cmd": "ruff check .",
                    "verify_in_review": True,
                },
                "checkpoints": [
                    {
                        "id": "phase0",
                        "name": "Setup",
                        "status": status,
                        "scope": "Bootstrap things",
                        "exit_criteria": ["A", "B"],
                        "attempts": attempts,
                        "review_notes": "",
                    }
                ],
            },
            indent=2,
        )
    )
    return p


@dataclass
class FakeProvider(Provider):
    name: str = "fake"
    binary: str = "fake"
    danger_env: str = "ALLOW_DANGEROUS_FAKE"
    on_call: Optional[Callable[[str], int]] = None
    prompts: list[str] = field(default_factory=list)

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover
        return [self.binary, prompt]

    def run(self, prompt: str, timeout: int) -> int:  # type: ignore[override]
        self.prompts.append(prompt)
        return self.on_call(prompt) if self.on_call else 0

    def preflight(self) -> None:  # bypass shutil.which
        return


@pytest.fixture(autouse=True)
def _stub_git(monkeypatch):
    """Replace every git_ops call with a no-op so the loop can run anywhere."""
    monkeypatch.setattr(orchestrator.git_ops, "ensure_branch", lambda branch: None)
    monkeypatch.setattr(
        orchestrator.git_ops, "require_clean_worktree", lambda log_dir=None: None
    )
    commits: list[str] = []
    monkeypatch.setattr(
        orchestrator.git_ops,
        "commit_checkpoint_changes",
        lambda msg, log_dir=None: commits.append(msg),
    )
    return commits


@pytest.fixture(autouse=True)
def _bypass_danger_gates(monkeypatch):
    monkeypatch.setattr(orchestrator.safety, "require_danger_gates", lambda providers: None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_developer_writes_then_reviewer_approves(tmp_path, _stub_git):
    state_path = _plan(tmp_path)

    def dev_writes_built(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "I did the work")
        s.save()
        return 0

    def reviewer_approves(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    dev = FakeProvider(name="dev", on_call=dev_writes_built)
    rev = FakeProvider(name="rev", on_call=reviewer_approves)

    run_loop(state_path=state_path, developer=dev, reviewer=rev, max_review_attempts=3, timeout=30)

    final = load_state(state_path)
    assert final.get("phase0")["status"] == "approved"
    assert final.get("phase0")["attempts"] == 1
    assert _stub_git == ["phase0: built", "phase0: approved", "audit: completed run"]
    assert len(dev.prompts) == 1
    assert len(rev.prompts) == 1


def test_already_approved_checkpoint_is_skipped(tmp_path, _stub_git):
    state_path = _plan(tmp_path, status="approved")
    dev = FakeProvider(name="dev", on_call=lambda _p: pytest.fail("dev must not run"))
    rev = FakeProvider(name="rev", on_call=lambda _p: pytest.fail("rev must not run"))

    run_loop(state_path=state_path, developer=dev, reviewer=rev, max_review_attempts=3, timeout=30)
    assert _stub_git == []


def test_already_built_resumes_at_review_gate(tmp_path, _stub_git):
    state_path = _plan(tmp_path, status="built")

    def reviewer_approves(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    dev = FakeProvider(name="dev", on_call=lambda _p: pytest.fail("dev must not re-run"))
    rev = FakeProvider(name="rev", on_call=reviewer_approves)

    run_loop(state_path=state_path, developer=dev, reviewer=rev, max_review_attempts=3, timeout=30)
    assert _stub_git == ["phase0: approved", "audit: completed run"]


# ---------------------------------------------------------------------------
# Retry path
# ---------------------------------------------------------------------------


def test_reviewer_rejects_then_developer_fixes_then_approved(tmp_path, _stub_git):
    state_path = _plan(tmp_path)

    dev_calls = {"n": 0}
    rev_calls = {"n": 0}

    def dev(_prompt):
        dev_calls["n"] += 1
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field(
            "phase0",
            "review_notes",
            "v1" if dev_calls["n"] == 1 else "fixed per notes",
        )
        s.save()
        return 0

    def rev(_prompt):
        rev_calls["n"] += 1
        s = load_state(state_path)
        if rev_calls["n"] == 1:
            s.set_field("phase0", "status", "built")
            s.set_field("phase0", "review_notes", "fix A and B")
        else:
            s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    run_loop(
        state_path=state_path,
        developer=FakeProvider(name="dev", on_call=dev),
        reviewer=FakeProvider(name="rev", on_call=rev),
        max_review_attempts=3,
        timeout=30,
    )

    final = load_state(state_path)
    assert final.get("phase0")["status"] == "approved"
    assert final.get("phase0")["attempts"] == 2
    assert dev_calls["n"] == 2
    assert rev_calls["n"] == 2
    assert _stub_git == [
        "phase0: built",
        "phase0: revision 1",
        "phase0: approved",
        "audit: completed run",
    ]


def test_loop_halts_when_max_review_attempts_exceeded(tmp_path, _stub_git):
    state_path = _plan(tmp_path)

    def dev_always_built(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "tried")
        s.save()
        return 0

    def rev_always_rejects(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "still missing X")
        s.save()
        return 0

    with pytest.raises(LoopHalted, match="phase0"):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev_always_built),
            reviewer=FakeProvider(name="rev", on_call=rev_always_rejects),
            max_review_attempts=2,
            timeout=30,
        )
    final = load_state(state_path)
    assert final.get("phase0")["attempts"] == 2
    assert final.get("phase0")["status"] == "built"
    assert _stub_git == [
        "phase0: built",
        "phase0: revision 1",
        "phase0: halted",
    ]



# ---------------------------------------------------------------------------
# Project block interpolation
# ---------------------------------------------------------------------------


def test_reviewer_prompt_mentions_configured_commands(tmp_path, _stub_git):
    state_path = _plan(tmp_path)

    def dev(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def rev(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    dev_p = FakeProvider(name="dev", on_call=dev)
    rev_p = FakeProvider(name="rev", on_call=rev)
    run_loop(state_path=state_path, developer=dev_p, reviewer=rev_p, max_review_attempts=3, timeout=30)

    review_prompt = rev_p.prompts[0]
    assert "pytest -q" in review_prompt
    assert "ruff check ." in review_prompt
    # Never reference flutter explicitly — project-agnostic.
    assert "flutter" not in review_prompt.lower()


def test_prompts_include_checkpoint_scope_and_exit_criteria(tmp_path, _stub_git):
    state_path = _plan(tmp_path)

    def dev(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def rev(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    dev_p = FakeProvider(name="dev", on_call=dev)
    rev_p = FakeProvider(name="rev", on_call=rev)
    run_loop(state_path=state_path, developer=dev_p, reviewer=rev_p, max_review_attempts=3, timeout=30)

    for prompt in (dev_p.prompts[0], rev_p.prompts[0]):
        assert "Bootstrap things" in prompt
        assert "- A" in prompt
        assert "- B" in prompt
        assert "phase0" in prompt


# ---------------------------------------------------------------------------
# Multiple checkpoints in order
# ---------------------------------------------------------------------------


def test_loop_processes_checkpoints_in_declared_order(tmp_path, _stub_git):
    p = tmp_path / "plan_checkpoints.json"
    p.write_text(
        json.dumps(
            {
                "plan_file": "docs/plan.md",
                "branch": "feature-x",
                "checkpoints": [
                    {
                        "id": "phase0",
                        "name": "First",
                        "status": "pending",
                        "scope": "first scope",
                        "exit_criteria": ["a"],
                        "attempts": 0,
                        "review_notes": "",
                    },
                    {
                        "id": "phase1",
                        "name": "Second",
                        "status": "pending",
                        "scope": "second scope",
                        "exit_criteria": ["b"],
                        "attempts": 0,
                        "review_notes": "",
                    },
                ],
            },
            indent=2,
        )
    )

    seen: list[str] = []

    def dev(prompt):
        for cid in ("phase0", "phase1"):
            if cid in prompt:
                seen.append(f"dev:{cid}")
                s = load_state(p)
                s.set_field(cid, "status", "built")
                s.save()
                return 0
        raise AssertionError("dev prompt referenced no known checkpoint")

    def rev(prompt):
        for cid in ("phase0", "phase1"):
            if cid in prompt:
                seen.append(f"rev:{cid}")
                s = load_state(p)
                s.set_field(cid, "status", "approved")
                s.save()
                return 0
        raise AssertionError("rev prompt referenced no known checkpoint")

    run_loop(
        state_path=p,
        developer=FakeProvider(name="dev", on_call=dev),
        reviewer=FakeProvider(name="rev", on_call=rev),
        max_review_attempts=3,
        timeout=30,
    )
    assert seen == ["dev:phase0", "rev:phase0", "dev:phase1", "rev:phase1"]


def test_run_loop_passes_custom_log_dir_to_commit(tmp_path, monkeypatch):
    state_path = _plan(tmp_path)
    custom_log_dir = tmp_path / "custom_logs"
    recorded_log_dirs: list[Path] = []

    monkeypatch.setattr(orchestrator.git_ops, "ensure_branch", lambda branch: None)
    monkeypatch.setattr(
        orchestrator.git_ops, "require_clean_worktree", lambda log_dir=None: None
    )
    monkeypatch.setattr(
        orchestrator.git_ops,
        "commit_checkpoint_changes",
        lambda msg, log_dir=None: recorded_log_dirs.append(log_dir),
    )

    def dev_writes_built(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def reviewer_approves(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    run_loop(
        state_path=state_path,
        developer=FakeProvider(name="dev", on_call=dev_writes_built),
        reviewer=FakeProvider(name="rev", on_call=reviewer_approves),
        log_dir=custom_log_dir,
    )

    assert recorded_log_dirs == [custom_log_dir, custom_log_dir, custom_log_dir]


def test_run_loop_defaults_to_safety_default_log_dir(tmp_path, monkeypatch):
    state_path = _plan(tmp_path)
    default_log_dir = tmp_path / "default_repo_logs"
    monkeypatch.setattr(orchestrator.safety, "default_log_dir", lambda: default_log_dir)

    recorded_log_dirs: list[Path] = []
    monkeypatch.setattr(orchestrator.git_ops, "ensure_branch", lambda branch: None)
    monkeypatch.setattr(
        orchestrator.git_ops, "require_clean_worktree", lambda log_dir=None: None
    )
    monkeypatch.setattr(
        orchestrator.git_ops,
        "commit_checkpoint_changes",
        lambda msg, log_dir=None: recorded_log_dirs.append(log_dir),
    )

    def dev_writes_built(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.save()
        return 0

    def reviewer_approves(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.save()
        return 0

    run_loop(
        state_path=state_path,
        developer=FakeProvider(name="dev", on_call=dev_writes_built),
        reviewer=FakeProvider(name="rev", on_call=reviewer_approves),
    )

    assert recorded_log_dirs == [default_log_dir, default_log_dir, default_log_dir]


# ---------------------------------------------------------------------------
# Phase 1 Audit Trail and Lifecycle Tests
# ---------------------------------------------------------------------------


def test_audit_log_captures_traces_with_role_and_checkpoint_context(
    tmp_path, capsys
):
    state_path = _plan(tmp_path)

    def dev_build(_prompt):
        print("dev: implementing features step by step")
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "Implemented feature with tests")
        s.save()
        return 0

    def rev_approve(_prompt):
        print("rev: inspecting diff and running tests")
        s = load_state(state_path)
        s.set_field("phase0", "status", "approved")
        s.set_field("phase0", "review_notes", "All criteria pass")
        s.save()
        return 0

    dev = FakeProvider(name="dev", on_call=dev_build)
    rev = FakeProvider(name="rev", on_call=rev_approve)

    run_loop(
        state_path=state_path,
        developer=dev,
        reviewer=rev,
        max_review_attempts=3,
        timeout=30,
    )

    out = capsys.readouterr().out

    # Invocation headers with role and checkpoint context
    assert "[agent-loop] [developer] [phase0] starting initial build" in out
    assert "dev: implementing features step by step" in out
    assert "[agent-loop] [developer] [phase0] initial build finished (exit code: 0)" in out
    assert "[agent-loop] [developer] [phase0] notes:\nImplemented feature with tests" in out

    assert "[agent-loop] [reviewer] [phase0] starting review (attempt 1/3)" in out
    assert "rev: inspecting diff and running tests" in out
    assert "[agent-loop] [reviewer] [phase0] review finished (exit code: 0)" in out
    assert "[agent-loop] [reviewer] [phase0] approval comment (attempt 1/3):\nAll criteria pass" in out


def test_audit_log_captures_reviewer_change_requests_and_revision_trail(
    tmp_path, capsys
):
    state_path = _plan(tmp_path)
    rev_count = {"n": 0}

    def dev_build(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "v1 complete")
        s.save()
        return 0

    def rev_review(_prompt):
        rev_count["n"] += 1
        s = load_state(state_path)
        if rev_count["n"] == 1:
            s.set_field("phase0", "status", "built")
            s.set_field("phase0", "review_notes", "Exit criterion A missing edge case test")
        else:
            s.set_field("phase0", "status", "approved")
            s.set_field("phase0", "review_notes", "Approved with edge case test")
        s.save()
        return 0

    def dev_revise(_prompt):
        print("dev: adding edge case test for criterion A")
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "Added edge case test for criterion A")
        s.save()
        return 0

    dev = FakeProvider(name="dev", on_call=dev_build)
    # Dev will be called first for build, then for revision
    calls = {"dev": 0}
    def dev_handler(prompt):
        calls["dev"] += 1
        return dev_build(prompt) if calls["dev"] == 1 else dev_revise(prompt)
    dev.on_call = dev_handler

    rev = FakeProvider(name="rev", on_call=rev_review)

    run_loop(
        state_path=state_path,
        developer=dev,
        reviewer=rev,
        max_review_attempts=3,
        timeout=30,
    )

    out = capsys.readouterr().out

    # Check that change requests are logged as review comments
    assert "[agent-loop] [reviewer] [phase0] review comment (attempt 1/3):\nExit criterion A missing edge case test" in out
    # Check revision invocation and notes
    assert "[agent-loop] [developer] [phase0] starting revision (attempt 1)" in out
    assert "dev: adding edge case test for criterion A" in out
    assert "[agent-loop] [developer] [phase0] revision finished (exit code: 0)" in out
    assert "[agent-loop] [developer] [phase0] revision notes:\nAdded edge case test for criterion A" in out
    # Check final approval comment
    assert "[agent-loop] [reviewer] [phase0] approval comment (attempt 2/3):\nApproved with edge case test" in out


def test_audit_log_captures_halt_and_commits_halt_state(tmp_path, capsys, _stub_git):
    state_path = _plan(tmp_path)

    def dev_build(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "draft 1")
        s.save()
        return 0

    def rev_reject(_prompt):
        s = load_state(state_path)
        s.set_field("phase0", "status", "built")
        s.set_field("phase0", "review_notes", "Criterion B failed")
        s.save()
        return 0

    with pytest.raises(LoopHalted, match="phase0 not approved after 1 attempts"):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev_build),
            reviewer=FakeProvider(name="rev", on_call=rev_reject),
            max_review_attempts=1,
            timeout=30,
        )

    out = capsys.readouterr().out
    assert "[agent-loop] [reviewer] [phase0] review comment (attempt 1/1):\nCriterion B failed" in out
    assert "[agent-loop] [phase0] halted: phase0 not approved after 1 attempts" in out
    assert _stub_git == ["phase0: built", "phase0: halted"]


def test_audit_log_captures_failure_and_commits_failure_state(
    tmp_path, capsys, _stub_git
):
    state_path = _plan(tmp_path)

    def dev_explodes(_prompt):
        raise RuntimeError("Unexpected agent crash")

    with pytest.raises(RuntimeError, match="Unexpected agent crash"):
        run_loop(
            state_path=state_path,
            developer=FakeProvider(name="dev", on_call=dev_explodes),
            reviewer=FakeProvider(name="rev"),
            max_review_attempts=3,
            timeout=30,
        )

    out = capsys.readouterr().out
    assert "[agent-loop] run failed: Unexpected agent crash" in out
    assert _stub_git == ["audit: run failed"]




