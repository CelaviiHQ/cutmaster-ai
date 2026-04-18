"""Tests for Director verbatim-timestamp validation. No LLM calls."""

from celavii_resolve.cutmaster.core.director import (
    CutSegment,
    DirectorPlan,
    validate_plan,
)


def _transcript() -> list[dict]:
    return [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "world,", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
        {"word": "this", "start_time": 1.2, "end_time": 1.45, "speaker_id": "S1"},
        {"word": "is", "start_time": 1.45, "end_time": 1.6, "speaker_id": "S1"},
        {"word": "the", "start_time": 1.6, "end_time": 1.75, "speaker_id": "S1"},
        {"word": "hook.", "start_time": 1.75, "end_time": 2.25, "speaker_id": "S1"},
    ]


def _plan(segments, hook_index=0):
    return DirectorPlan(
        selected_clips=[CutSegment(**s) for s in segments],
        hook_index=hook_index,
        reasoning="",
    )


def test_valid_plan_has_no_errors():
    plan = _plan([{"start_s": 0.0, "end_s": 0.95, "reason": ""}])
    assert validate_plan(plan, _transcript()) == []


def test_rounded_timestamp_rejected():
    # Transcript has 1.45; model returns 1.5 (rounded). Should fail.
    plan = _plan([{"start_s": 0.0, "end_s": 1.5, "reason": ""}])
    errors = validate_plan(plan, _transcript())
    assert errors
    assert any("end_s" in e and "verbatim" in e for e in errors)


def test_truncated_timestamp_rejected():
    # Transcript has 0.95; model returns 0.9 (truncated to 1 decimal).
    plan = _plan([{"start_s": 0.0, "end_s": 0.9, "reason": ""}])
    errors = validate_plan(plan, _transcript())
    assert any("end_s" in e and "0.9" in e for e in errors)


def test_inverted_range_rejected():
    plan = _plan([{"start_s": 1.6, "end_s": 0.5, "reason": ""}])
    errors = validate_plan(plan, _transcript())
    assert any("must be >" in e for e in errors)


def test_empty_plan_rejected():
    plan = DirectorPlan(selected_clips=[], hook_index=0, reasoning="")
    errors = validate_plan(plan, _transcript())
    assert any("empty" in e.lower() for e in errors)


def test_hook_index_out_of_range_rejected():
    plan = _plan(
        [{"start_s": 0.0, "end_s": 0.95, "reason": ""}],
        hook_index=5,
    )
    errors = validate_plan(plan, _transcript())
    assert any("hook_index" in e for e in errors)


def test_repr_float_equivalence_accepted():
    # 1.60 is the same float as 1.6 — must not spuriously fail.
    plan = _plan([{"start_s": 1.60, "end_s": 2.25, "reason": ""}])
    assert validate_plan(plan, _transcript()) == []
