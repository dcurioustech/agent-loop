"""Tests for the shared plan-generation foundations (plan_init.py)."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from agent_loop.plan_init import (
    DuplicateCheckpointError,
    InvalidPlanState,
    PlanOutputParseError,
    ProtectedBranchError,
    ProviderExecutionError,
    StateFileExistsError,
    build_generated_payload,
    build_init_prompt,
    generate_plan_json,
    new_checkpoint,
    normalize_checkpoints,
    parse_plan_json,
    run_provider_captured,
    slugify,
    validate_generated_payload,
    write_generated_payload,
)
from agent_loop.providers.base import CapturedResult, Provider
from agent_loop.state import load_state


def _checkpoints() -> list[dict]:
    return [
        new_checkpoint("phase0", "Setup", "Bootstrap things", ["A", "B"]),
        new_checkpoint("phase1", "Build", "Make widgets", ["X"]),
    ]


# ---------------------------------------------------------------------------
# Canonical shape
# ---------------------------------------------------------------------------


def test_new_checkpoint_has_canonical_pending_shape():
    cp = new_checkpoint("phase0", "Setup", "Bootstrap things", ["A", "B"])
    assert cp == {
        "id": "phase0",
        "name": "Setup",
        "status": "pending",
        "scope": "Bootstrap things",
        "exit_criteria": ["A", "B"],
        "attempts": 0,
        "review_notes": "",
    }


def test_build_generated_payload_includes_required_top_level_fields():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    assert payload["plan_file"] == "docs/plan.md"
    assert payload["branch"] == "feature/x"
    assert [cp["id"] for cp in payload["checkpoints"]] == ["phase0", "phase1"]
    assert "project" not in payload
    assert "models" not in payload


def test_build_generated_payload_includes_optional_blocks_when_given():
    payload = build_generated_payload(
        "docs/plan.md",
        "feature/x",
        _checkpoints(),
        project={"test_cmd": "pytest"},
        models={"developer": "opus-x"},
    )
    assert payload["project"] == {"test_cmd": "pytest"}
    assert payload["models"] == {"developer": "opus-x"}


# ---------------------------------------------------------------------------
# Validation of in-memory payloads (before anything is written)
# ---------------------------------------------------------------------------


def test_validate_generated_payload_accepts_canonical_shape():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)  # must not raise


def test_validate_generated_payload_rejects_protected_branch():
    payload = build_generated_payload("docs/plan.md", "main", _checkpoints())
    with pytest.raises(ProtectedBranchError, match="protected"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_duplicate_checkpoint_ids():
    checkpoints = _checkpoints()
    checkpoints[1]["id"] = "phase0"
    payload = build_generated_payload("docs/plan.md", "feature/x", checkpoints)
    with pytest.raises(DuplicateCheckpointError, match="duplicate"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_malformed_payload():
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    del payload["checkpoints"][0]["exit_criteria"]
    with pytest.raises(InvalidPlanState, match="exit_criteria"):
        validate_generated_payload(payload)


def test_validate_generated_payload_rejects_non_dict():
    with pytest.raises(InvalidPlanState, match="object"):
        validate_generated_payload(["not", "a", "dict"])  # type: ignore[arg-type]


def test_validate_generated_payload_does_not_touch_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Compatibility: a validated generated payload loads through load_state
# ---------------------------------------------------------------------------


def test_validated_payload_round_trips_through_load_state(tmp_path):
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    validate_generated_payload(payload)

    import json

    state_path = tmp_path / "plan_checkpoints.json"
    state_path.write_text(json.dumps(payload, indent=2))

    state = load_state(state_path)
    assert state.branch == "feature/x"
    assert [cp["id"] for cp in state.checkpoints] == ["phase0", "phase1"]
    assert state.get("phase0")["status"] == "pending"
    assert state.get("phase0")["attempts"] == 0
    assert state.get("phase0")["review_notes"] == ""


# ---------------------------------------------------------------------------
# Captured provider execution
# ---------------------------------------------------------------------------


class FakeProvider(Provider):
    name = "fake"
    binary = "fake"
    danger_env = "ALLOW_DANGEROUS_FAKE"

    def __init__(self, result: CapturedResult) -> None:
        super().__init__()
        self._result = result
        self.prompts: list[str] = []
        self.timeouts: list[int] = []

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover
        return [self.binary, prompt]

    def run_captured(self, prompt: str, timeout: int) -> CapturedResult:
        self.prompts.append(prompt)
        self.timeouts.append(timeout)
        return self._result


def test_run_provider_captured_returns_stdout_on_success():
    provider = FakeProvider(CapturedResult(returncode=0, stdout="OUT", stderr=""))
    assert run_provider_captured(provider, "PROMPT", timeout=30) == "OUT"
    assert provider.prompts == ["PROMPT"]
    assert provider.timeouts == [30]


def test_run_provider_captured_raises_on_timeout():
    provider = FakeProvider(
        CapturedResult(returncode=124, stdout="", stderr="", timed_out=True)
    )
    with pytest.raises(ProviderExecutionError, match="timed out after 30s"):
        run_provider_captured(provider, "PROMPT", timeout=30)


def test_run_provider_captured_raises_on_non_zero_exit():
    provider = FakeProvider(CapturedResult(returncode=1, stdout="", stderr="boom"))
    with pytest.raises(ProviderExecutionError, match="exited with code 1.*boom"):
        run_provider_captured(provider, "PROMPT", timeout=30)


def test_run_provider_captured_non_zero_message_without_stderr():
    provider = FakeProvider(CapturedResult(returncode=2, stdout="", stderr="   "))
    with pytest.raises(ProviderExecutionError, match="exited with code 2"):
        run_provider_captured(provider, "PROMPT", timeout=30)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_build_init_prompt_embeds_feature_text():
    prompt = build_init_prompt("Add a login page")
    assert "Add a login page" in prompt


def test_build_init_prompt_requests_json_only_no_prose():
    prompt = build_init_prompt("Add a login page")
    assert "ONLY a single JSON object" in prompt
    assert "no prose" in prompt


def test_build_init_prompt_requests_project_agnostic_ordered_checkpoints():
    prompt = build_init_prompt("Add a login page")
    assert "project-agnostic" in prompt
    assert "ordered" in prompt
    assert "exit_criteria" in prompt
    assert "objective" in prompt


# ---------------------------------------------------------------------------
# JSON parsing: raw JSON or a single fenced block, nothing else
# ---------------------------------------------------------------------------


def test_parse_plan_json_accepts_raw_json():
    assert parse_plan_json('{"checkpoints": []}') == {"checkpoints": []}


def test_parse_plan_json_accepts_single_json_fence():
    raw = '```json\n{"checkpoints": []}\n```'
    assert parse_plan_json(raw) == {"checkpoints": []}


def test_parse_plan_json_accepts_fence_without_json_tag():
    raw = '```\n{"checkpoints": []}\n```'
    assert parse_plan_json(raw) == {"checkpoints": []}


def test_parse_plan_json_strips_surrounding_whitespace():
    raw = '\n\n  {"checkpoints": []}  \n\n'
    assert parse_plan_json(raw) == {"checkpoints": []}


def test_parse_plan_json_rejects_empty_output():
    with pytest.raises(PlanOutputParseError, match="no output"):
        parse_plan_json("   ")


def test_parse_plan_json_rejects_prose_before_json():
    raw = 'Here is the plan:\n{"checkpoints": []}'
    with pytest.raises(PlanOutputParseError, match="not valid JSON"):
        parse_plan_json(raw)


def test_parse_plan_json_rejects_prose_after_json():
    raw = '{"checkpoints": []}\nHope this helps!'
    with pytest.raises(PlanOutputParseError, match="not valid JSON"):
        parse_plan_json(raw)


def test_parse_plan_json_rejects_prose_around_a_fence():
    raw = 'Here is the plan:\n```json\n{"checkpoints": []}\n```'
    with pytest.raises(PlanOutputParseError, match="mixes prose"):
        parse_plan_json(raw)


def test_parse_plan_json_rejects_prose_after_a_fence():
    raw = '```json\n{"checkpoints": []}\n```\nHope this helps!'
    with pytest.raises(PlanOutputParseError, match="mixes prose"):
        parse_plan_json(raw)


def test_parse_plan_json_rejects_more_than_one_fence():
    raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
    with pytest.raises(PlanOutputParseError, match="mixes prose|more than one"):
        parse_plan_json(raw)


def test_parse_plan_json_rejects_invalid_json():
    with pytest.raises(PlanOutputParseError, match="not valid JSON"):
        parse_plan_json("{not json}")


def test_parse_plan_json_rejects_invalid_json_inside_fence():
    raw = "```json\n{not json}\n```"
    with pytest.raises(PlanOutputParseError, match="not valid JSON"):
        parse_plan_json(raw)


# ---------------------------------------------------------------------------
# Checkpoint normalization
# ---------------------------------------------------------------------------


def _minimal_cp(cid: str = "phase0") -> dict:
    return {"id": cid, "name": "Setup", "scope": "Bootstrap", "exit_criteria": ["A"]}


def test_normalize_checkpoints_accepts_checkpoints_key():
    result = normalize_checkpoints({"checkpoints": [_minimal_cp()]})
    assert result == [new_checkpoint("phase0", "Setup", "Bootstrap", ["A"])]


def test_normalize_checkpoints_accepts_bare_list():
    result = normalize_checkpoints([_minimal_cp()])
    assert result == [new_checkpoint("phase0", "Setup", "Bootstrap", ["A"])]


def test_normalize_checkpoints_forces_canonical_pending_shape():
    cp = _minimal_cp()
    cp.update({"status": "approved", "attempts": 5, "review_notes": "stale"})
    result = normalize_checkpoints({"checkpoints": [cp]})
    assert result[0]["status"] == "pending"
    assert result[0]["attempts"] == 0
    assert result[0]["review_notes"] == ""


def test_normalize_checkpoints_rejects_missing_checkpoints_list():
    with pytest.raises(PlanOutputParseError, match="checkpoints"):
        normalize_checkpoints({"not_checkpoints": []})


def test_normalize_checkpoints_rejects_empty_list():
    with pytest.raises(PlanOutputParseError, match="non-empty"):
        normalize_checkpoints({"checkpoints": []})


def test_normalize_checkpoints_rejects_non_dict_entry():
    with pytest.raises(PlanOutputParseError, match="JSON object"):
        normalize_checkpoints({"checkpoints": ["not-a-dict"]})


def test_normalize_checkpoints_rejects_missing_required_field():
    cp = _minimal_cp()
    del cp["scope"]
    with pytest.raises(PlanOutputParseError, match="scope"):
        normalize_checkpoints({"checkpoints": [cp]})


def test_normalize_checkpoints_rejects_non_list_exit_criteria():
    cp = _minimal_cp()
    cp["exit_criteria"] = "just do it well"
    with pytest.raises(PlanOutputParseError, match="exit_criteria"):
        normalize_checkpoints({"checkpoints": [cp]})


# ---------------------------------------------------------------------------
# End-to-end: provider execution -> parsed JSON
# ---------------------------------------------------------------------------


def test_generate_plan_json_runs_prompt_and_parses_result():
    provider = FakeProvider(
        CapturedResult(returncode=0, stdout='{"checkpoints": []}', stderr="")
    )
    result = generate_plan_json(provider, "Add a login page", timeout=30)
    assert result == {"checkpoints": []}
    assert "Add a login page" in provider.prompts[0]


def test_generate_plan_json_propagates_execution_error():
    provider = FakeProvider(CapturedResult(returncode=1, stdout="", stderr="boom"))
    with pytest.raises(ProviderExecutionError):
        generate_plan_json(provider, "Add a login page", timeout=30)


def test_generate_plan_json_propagates_parse_error():
    provider = FakeProvider(
        CapturedResult(returncode=0, stdout="not json at all", stderr="")
    )
    with pytest.raises(PlanOutputParseError):
        generate_plan_json(provider, "Add a login page", timeout=30)


# ---------------------------------------------------------------------------
# slugify — used by the CLI to derive the default feature/<slug> branch
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates_spaces():
    assert slugify("Add a login page") == "add-a-login-page"


def test_slugify_collapses_punctuation_runs():
    assert slugify("Fix bug #42: null pointer!!") == "fix-bug-42-null-pointer"


def test_slugify_strips_leading_and_trailing_hyphens():
    assert slugify("  ---weird input---  ") == "weird-input"


def test_slugify_truncates_to_max_len():
    slug = slugify("a" * 100, max_len=10)
    assert slug == "a" * 10


def test_slugify_truncation_does_not_leave_trailing_hyphen():
    # Truncating "abcde-fghij-klmno" to 6 chars lands exactly on a hyphen.
    slug = slugify("abcde fghij klmno", max_len=6)
    assert slug == "abcde"


def test_slugify_falls_back_to_feature_when_nothing_alphanumeric_survives():
    assert slugify("!!!") == "feature"
    assert slugify("") == "feature"


def test_slugify_transliterates_accented_characters_to_ascii_base():
    # Without NFKD folding these collapse to "caf-r-servation", losing letters.
    assert slugify("Café réservation") == "cafe-reservation"
    assert slugify("naïve résumé façade") == "naive-resume-facade"


def test_slugify_output_is_always_ascii():
    for text in ["Café", "日本語サポート", "🎉 party", "Ünïcödé"]:
        assert slugify(text).isascii()


def test_slugify_normalizes_before_truncating():
    # "é" folds to one character before the cut; folding after truncation
    # would let the combining accent consume part of the budget.
    assert slugify("éééééééééé", max_len=4) == "eeee"


# ---------------------------------------------------------------------------
# write_generated_payload — atomic write used only after validation succeeds
# ---------------------------------------------------------------------------


def test_write_generated_payload_writes_formatted_json(tmp_path):
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    write_generated_payload(path, payload)
    assert json.loads(path.read_text()) == payload
    assert path.read_text().endswith("\n")


def test_write_generated_payload_overwrites_existing_file_with_force(tmp_path):
    path = tmp_path / "plan_checkpoints.json"
    path.write_text("stale content")
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    write_generated_payload(path, payload, force=True)
    assert json.loads(path.read_text()) == payload


def test_write_generated_payload_refuses_existing_file_without_force(tmp_path):
    path = tmp_path / "plan_checkpoints.json"
    path.write_text("stale content")
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())

    with pytest.raises(StateFileExistsError):
        write_generated_payload(path, payload)

    # The pre-existing file is the one that must survive, byte for byte.
    assert path.read_text() == "stale content"
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


def test_write_generated_payload_refuses_file_appearing_after_the_cli_check(tmp_path):
    """The race the up-front `--state` existence check cannot close."""
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    real_fsync = os.fsync

    def racing_fsync(fd):
        # Stand in for another process creating the target mid-write.
        if not path.exists():
            path.write_text("written by someone else")
        return real_fsync(fd)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("agent_loop.plan_init.os.fsync", racing_fsync)
    try:
        with pytest.raises(StateFileExistsError):
            write_generated_payload(path, payload)
    finally:
        monkeypatch.undo()

    assert path.read_text() == "written by someone else"
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


# ---------------------------------------------------------------------------
# The no-hardlink fallback (exFAT, some network mounts) must be exactly as
# exclusive as the os.link path — an exists()-then-replace would not be.
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_hardlinks(monkeypatch):
    """Simulate a filesystem whose link() fails with something other than EEXIST."""

    def unsupported_link(src, dst):
        raise OSError(errno.EPERM, "hardlinks not supported")

    monkeypatch.setattr("agent_loop.plan_init.os.link", unsupported_link)


def test_write_generated_payload_still_writes_without_hardlink_support(
    tmp_path, _no_hardlinks
):
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())

    write_generated_payload(path, payload)

    assert json.loads(path.read_text()) == payload
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


def test_write_generated_payload_refuses_existing_file_without_hardlink_support(
    tmp_path, _no_hardlinks
):
    path = tmp_path / "plan_checkpoints.json"
    path.write_text("stale content")
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())

    with pytest.raises(StateFileExistsError):
        write_generated_payload(path, payload)

    assert path.read_text() == "stale content"
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


def test_no_hardlink_fallback_refuses_file_appearing_after_the_cli_check(
    tmp_path, monkeypatch, _no_hardlinks
):
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    real_fsync = os.fsync

    def racing_fsync(fd):
        # Stand in for a concurrent init publishing the target mid-write.
        if not path.exists():
            path.write_text("written by someone else")
        return real_fsync(fd)

    monkeypatch.setattr("agent_loop.plan_init.os.fsync", racing_fsync)

    with pytest.raises(StateFileExistsError):
        write_generated_payload(path, payload)

    assert path.read_text() == "written by someone else"
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


def test_no_hardlink_fallback_never_replaces_a_file_it_did_not_create(
    tmp_path, monkeypatch, _no_hardlinks
):
    """Regression guard against an exists()-then-replace fallback.

    The test above cannot catch that shape: it creates the rival before the
    publish step, which a check-then-replace also refuses. The killing
    interleaving is a rival appearing *between* the check and the replace, so
    the rival is created from inside `Path.exists` — the probe only a
    check-then-replace consults. The exclusive-creation fallback never calls
    it, so no rival appears and the write just succeeds.

    Either way the invariant holds: os.replace must only ever land on the
    empty placeholder this process claimed, never on someone else's payload.
    """
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    real_exists = Path.exists
    real_replace = os.replace
    clobbered: list[str] = []

    def racing_exists(self, *a, **kw):
        if self == path and not real_exists(self, *a, **kw):
            # Lost the race: the rival lands right after the check says "free".
            self.write_text("written by someone else")
            return False
        return real_exists(self, *a, **kw)

    def guarded_replace(src, dst):
        # Anything non-empty at `dst` is a payload this process did not write.
        existing = Path(dst)
        if existing.exists() and existing.read_bytes():
            clobbered.append(existing.read_text())
        return real_replace(src, dst)

    monkeypatch.setattr(Path, "exists", racing_exists)
    monkeypatch.setattr("agent_loop.plan_init.os.replace", guarded_replace)

    try:
        write_generated_payload(path, payload)
    except StateFileExistsError:
        pass

    assert clobbered == [], f"publish overwrote a rival payload: {clobbered}"


def test_no_hardlink_fallback_leaves_no_placeholder_when_publishing_fails(
    tmp_path, monkeypatch, _no_hardlinks
):
    """A failed publish must not leave a zero-byte file blocking the next run."""
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())

    def boom(src, dst):
        raise OSError(errno.EIO, "disk fell over")

    monkeypatch.setattr("agent_loop.plan_init.os.replace", boom)

    with pytest.raises(OSError):
        write_generated_payload(path, payload)

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_write_generated_payload_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    write_generated_payload(path, payload)
    assert json.loads(path.read_text()) == payload


def test_write_generated_payload_leaves_no_tmp_files_behind(tmp_path):
    path = tmp_path / "plan_checkpoints.json"
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())
    write_generated_payload(path, payload)
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]


def test_write_generated_payload_does_not_corrupt_existing_file_on_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "plan_checkpoints.json"
    path.write_text("original content")
    payload = build_generated_payload("docs/plan.md", "feature/x", _checkpoints())

    def boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr("agent_loop.plan_init.json.dump", boom)
    with pytest.raises(RuntimeError):
        write_generated_payload(path, payload)

    assert path.read_text() == "original content"
    assert [p.name for p in tmp_path.iterdir()] == ["plan_checkpoints.json"]
