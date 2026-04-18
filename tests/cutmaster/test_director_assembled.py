"""Tests for assembled-mode Director — schema, validator, prompt, expansion."""

import pytest

from celavii_resolve.cutmaster.core import director
from celavii_resolve.cutmaster.core.director import (
    AssembledDirectorPlan,
    AssembledItemSelection,
    WordSpan,
    expand_assembled_plan,
    validate_assembled_plan,
)
from celavii_resolve.cutmaster.data.presets import get_preset


def _take(idx: int, words: list[tuple[str, float, float]], name: str = "clip.mov") -> dict:
    return {
        "item_index": idx,
        "source_name": name,
        "start_s": words[0][1] if words else 0.0,
        "end_s": words[-1][2] if words else 0.0,
        "transcript": [
            {"i": i, "word": w, "start_time": s, "end_time": e, "speaker_id": "S1"}
            for i, (w, s, e) in enumerate(words)
        ],
    }


def _plan(selections: list[tuple[int, list[tuple[int, int]]]], hook: int = 0):
    return AssembledDirectorPlan(
        hook_index=hook,
        selections=[
            AssembledItemSelection(
                item_index=item_idx,
                kept_word_spans=[WordSpan(a=a, b=b) for (a, b) in spans],
            )
            for (item_idx, spans) in selections
        ],
        reasoning="test",
    )


# ------------------------- validator -----------------------------------


def test_valid_plan_single_take():
    take = _take(0, [("Hello", 0.0, 0.5), ("world.", 0.5, 1.0)])
    plan = _plan([(0, [(0, 1)])])
    assert validate_assembled_plan(plan, [take]) == []


def test_empty_selections_rejected():
    plan = AssembledDirectorPlan(hook_index=0, selections=[], reasoning="")
    take = _take(0, [("x", 0.0, 0.5)])
    errors = validate_assembled_plan(plan, [take])
    assert errors and "empty" in errors[0]


def test_duplicate_take_selection_rejected():
    take = _take(0, [("one", 0.0, 0.5), ("two", 0.5, 1.0)])
    plan = _plan([(0, [(0, 0)]), (0, [(1, 1)])])
    errors = validate_assembled_plan(plan, [take])
    assert any("appears twice" in e for e in errors)


def test_unknown_item_index_rejected():
    take = _take(0, [("x", 0.0, 0.5)])
    plan = _plan([(5, [(0, 0)])])
    errors = validate_assembled_plan(plan, [take])
    assert any("does not match any input take" in e for e in errors)


def test_out_of_range_span_rejected():
    take = _take(0, [("x", 0.0, 0.5), ("y", 0.5, 1.0)])
    plan = _plan([(0, [(0, 5)])])
    errors = validate_assembled_plan(plan, [take])
    assert any("out of range" in e for e in errors)


def test_overlapping_spans_rejected():
    take = _take(0, [("a", 0.0, 0.5), ("b", 0.5, 1.0), ("c", 1.0, 1.5)])
    plan = _plan([(0, [(0, 1), (1, 2)])])  # b=1 overlaps next a=1
    errors = validate_assembled_plan(plan, [take])
    assert any("overlaps" in e for e in errors)


def test_swapped_span_indices_rejected():
    take = _take(0, [("a", 0.0, 0.5), ("b", 0.5, 1.0)])
    plan = AssembledDirectorPlan(
        hook_index=0,
        selections=[
            AssembledItemSelection(
                item_index=0,
                kept_word_spans=[WordSpan(a=1, b=0)],
            )
        ],
        reasoning="",
    )
    errors = validate_assembled_plan(plan, [take])
    assert any("a=1" in e and "b=0" in e for e in errors)


def test_hook_index_out_of_range():
    take = _take(0, [("x", 0.0, 0.5)])
    plan = _plan([(0, [(0, 0)])], hook=3)
    errors = validate_assembled_plan(plan, [take])
    assert any("hook_index" in e for e in errors)


def test_reorder_disabled_rejects_swapped_takes():
    takes = [
        _take(0, [("a", 0.0, 0.5)]),
        _take(1, [("b", 1.0, 1.5)]),
    ]
    # Director picked take 1 then take 0 — not allowed when reorder_allowed=False.
    plan = _plan([(1, [(0, 0)]), (0, [(0, 0)])])
    errors = validate_assembled_plan(plan, takes, reorder_allowed=False)
    assert any("breaks input order" in e for e in errors)


def test_reorder_disabled_accepts_monotonic_selection():
    takes = [
        _take(0, [("a", 0.0, 0.5)]),
        _take(1, [("b", 1.0, 1.5)]),
        _take(2, [("c", 2.0, 2.5)]),
    ]
    plan = _plan([(0, [(0, 0)]), (2, [(0, 0)])])  # Skipping take 1 is fine
    assert validate_assembled_plan(plan, takes, reorder_allowed=False) == []


def test_reorder_enabled_allows_any_order():
    takes = [
        _take(0, [("a", 0.0, 0.5)]),
        _take(1, [("b", 1.0, 1.5)]),
    ]
    plan = _plan([(1, [(0, 0)]), (0, [(0, 0)])])
    assert validate_assembled_plan(plan, takes, reorder_allowed=True) == []


def test_empty_spans_in_selection_rejected():
    take = _take(0, [("a", 0.0, 0.5)])
    plan = AssembledDirectorPlan(
        hook_index=0,
        selections=[AssembledItemSelection(item_index=0, kept_word_spans=[])],
        reasoning="",
    )
    errors = validate_assembled_plan(plan, [take])
    assert any("drop the take entirely" in e for e in errors)


# ------------------------- prompt -----------------------------------


def test_prompt_reorder_on_instructs_reorder_freedom():
    take = _take(0, [("x", 0.0, 0.5)])
    prompt = director._assembled_prompt(get_preset("vlog"), [take], {"reorder_allowed": True})
    assert "MAY reorder" in prompt


def test_prompt_reorder_off_instructs_strict_order():
    take = _take(0, [("x", 0.0, 0.5)])
    prompt = director._assembled_prompt(get_preset("vlog"), [take], {"reorder_allowed": False})
    assert "MUST NOT reorder" in prompt
    assert "strictly ascending" in prompt


def test_prompt_includes_excludes_and_focus_when_set():
    take = _take(0, [("x", 0.0, 0.5)])
    prompt = director._assembled_prompt(
        get_preset("wedding"),
        [take],
        {
            "exclude_categories": ["vendor_mentions"],
            "custom_focus": "the vows",
            "reorder_allowed": True,
        },
    )
    assert "EXCLUDE CATEGORIES" in prompt
    assert "Vendor mentions" in prompt
    assert "USER FOCUS" in prompt
    assert "the vows" in prompt


# ------------------------- expand -----------------------------------


def test_expand_single_take_single_span():
    take = _take(0, [("Hello", 0.0, 0.5), ("world.", 0.5, 1.0)])
    plan = _plan([(0, [(0, 1)])])
    segments, hook_idx = expand_assembled_plan(plan, [take])
    assert len(segments) == 1
    assert segments[0].start_s == pytest.approx(0.0)
    assert segments[0].end_s == pytest.approx(1.0)
    assert hook_idx == 0


def test_expand_multi_take_preserves_director_order():
    takes = [
        _take(0, [("a", 0.0, 0.5), ("b", 0.5, 1.0)], "first.mov"),
        _take(1, [("c", 2.0, 2.5), ("d", 2.5, 3.0)], "second.mov"),
    ]
    plan = _plan([(1, [(0, 1)]), (0, [(0, 0)])], hook=0)
    segments, hook_idx = expand_assembled_plan(plan, takes)
    assert len(segments) == 2
    # Director order: take 1 first, take 0 second
    assert segments[0].start_s == pytest.approx(2.0)
    assert segments[0].end_s == pytest.approx(3.0)
    assert segments[1].start_s == pytest.approx(0.0)
    assert segments[1].end_s == pytest.approx(0.5)
    # Hook is take 1's first segment, which landed at index 0
    assert hook_idx == 0


def test_expand_multi_span_per_take_creates_multiple_segments():
    take = _take(
        0,
        [
            ("a", 0.0, 0.5),
            ("b", 0.5, 1.0),
            ("c", 2.0, 2.5),
            ("d", 2.5, 3.0),
        ],
    )
    plan = _plan([(0, [(0, 1), (2, 3)])])
    segments, _ = expand_assembled_plan(plan, [take])
    assert len(segments) == 2
    assert segments[0].start_s == pytest.approx(0.0)
    assert segments[0].end_s == pytest.approx(1.0)
    assert segments[1].start_s == pytest.approx(2.0)
    assert segments[1].end_s == pytest.approx(3.0)


def test_expand_hook_maps_to_first_span_of_hook_take():
    takes = [
        _take(0, [("a", 0.0, 0.5)]),
        _take(1, [("b", 1.0, 1.5), ("c", 1.5, 2.0)]),
    ]
    # take 0 comes first, take 1 second, but hook is take 1 → hook_idx=1
    plan = _plan([(0, [(0, 0)]), (1, [(0, 1)])], hook=1)
    segments, hook_idx = expand_assembled_plan(plan, takes)
    assert len(segments) == 2
    assert hook_idx == 1
