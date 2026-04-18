"""Tests for Curated + Rough cut Director validators and plan expansion (v2-11)."""

from celavii_resolve.cutmaster.core.director import (
    CuratedDirectorPlan,
    CuratedItemSelection,
    WordSpan,
    expand_curated_plan,
    validate_curated_plan,
    validate_rough_cut_plan,
)


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start_time": start, "end_time": end, "speaker_id": "S1"}


def _take(idx: int, words: list[tuple[str, float, float]]) -> dict:
    return {
        "item_index": idx,
        "source_name": f"take_{idx}.mov",
        "start_s": idx * 10.0,
        "end_s": (idx + 1) * 10.0,
        "transcript": [_w(w, s, e) for w, s, e in words],
    }


def _sel(order: int, item_index: int, spans: list[tuple[int, int]]) -> CuratedItemSelection:
    return CuratedItemSelection(
        order=order,
        item_index=item_index,
        kept_word_spans=[WordSpan(a=a, b=b) for a, b in spans],
    )


# --- curated: happy path --------------------------------------------------


def test_curated_valid_plan_passes():
    takes = [
        _take(0, [("alpha", 0.0, 0.5), ("beta", 0.6, 1.0)]),
        _take(1, [("gamma", 10.0, 10.5), ("delta", 10.6, 11.0)]),
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[_sel(0, 1, [(0, 1)]), _sel(1, 0, [(0, 1)])],
    )
    assert validate_curated_plan(plan, takes) == []


def test_curated_rejects_plan_missing_a_take():
    takes = [
        _take(0, [("a", 0.0, 0.5)]),
        _take(1, [("b", 10.0, 10.5)]),
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[_sel(0, 0, [(0, 0)])],  # drops take 1
    )
    errors = validate_curated_plan(plan, takes)
    assert any("Curated invariant violated" in e and "[1]" in e for e in errors)


def test_curated_allows_same_take_twice_with_distinct_spans():
    takes = [
        _take(0, [("a", 0.0, 0.5), ("b", 0.6, 1.0), ("c", 1.1, 1.5), ("d", 1.6, 2.0)]),
        _take(1, [("e", 10.0, 10.5)]),
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[
            _sel(0, 0, [(0, 1)]),
            _sel(1, 1, [(0, 0)]),
            _sel(2, 0, [(2, 3)]),  # callback to take 0, non-overlapping
        ],
    )
    assert validate_curated_plan(plan, takes) == []


def test_curated_rejects_overlapping_cross_selection_spans():
    takes = [
        _take(0, [("a", 0.0, 0.5), ("b", 0.6, 1.0), ("c", 1.1, 1.5)]),
        _take(1, [("d", 10.0, 10.5)]),
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[
            _sel(0, 0, [(0, 2)]),
            _sel(1, 1, [(0, 0)]),
            _sel(2, 0, [(1, 2)]),  # overlaps selection 0
        ],
    )
    errors = validate_curated_plan(plan, takes)
    assert any("overlap across selections" in e for e in errors)


def test_curated_rejects_non_contiguous_order_values():
    takes = [_take(0, [("a", 0.0, 0.5)]), _take(1, [("b", 10.0, 10.5)])]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[_sel(0, 0, [(0, 0)]), _sel(5, 1, [(0, 0)])],  # skips 1..4
    )
    errors = validate_curated_plan(plan, takes)
    assert any("contiguous" in e for e in errors)


def test_curated_rejects_hook_order_mismatch():
    takes = [_take(0, [("a", 0.0, 0.5)])]
    plan = CuratedDirectorPlan(
        hook_order=99,
        selections=[_sel(0, 0, [(0, 0)])],
    )
    errors = validate_curated_plan(plan, takes)
    assert any("hook_order" in e for e in errors)


# --- curated: expand ------------------------------------------------------


def test_expand_emits_segments_in_order_sequence():
    takes = [
        _take(0, [("a", 0.0, 0.5), ("b", 0.6, 1.0)]),
        _take(1, [("c", 10.0, 10.5), ("d", 10.6, 11.0)]),
    ]
    plan = CuratedDirectorPlan(
        hook_order=1,
        selections=[
            _sel(1, 0, [(0, 1)]),  # take 0 plays second
            _sel(0, 1, [(0, 1)]),  # take 1 plays first
        ],
    )
    segments, hook_idx = expand_curated_plan(plan, takes)
    # Sorted by order: take 1 first, then take 0
    assert segments[0].start_s == 10.0 and segments[0].end_s == 11.0
    assert segments[1].start_s == 0.0 and segments[1].end_s == 1.0
    # Hook is order=1 which sorts to position 1; its first segment is index 1.
    assert hook_idx == 1


# --- rough cut: happy path -----------------------------------------------


def test_rough_cut_valid_plan_with_one_winner_per_group():
    takes = [_take(i, [("w", float(i), float(i) + 0.5)]) for i in range(4)]
    groups = [
        {"group_id": 0, "item_indexes": [0, 1], "signal": "color"},
        {"group_id": 1, "item_indexes": [2, 3], "signal": "color"},
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[_sel(0, 1, [(0, 0)]), _sel(1, 2, [(0, 0)])],
    )
    assert validate_rough_cut_plan(plan, takes, groups) == []


def test_rough_cut_rejects_plan_dropping_entire_group():
    takes = [_take(i, [("w", float(i), float(i) + 0.5)]) for i in range(4)]
    groups = [
        {"group_id": 0, "item_indexes": [0, 1], "signal": "color"},
        {"group_id": 1, "item_indexes": [2, 3], "signal": "color"},
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[_sel(0, 0, [(0, 0)]), _sel(1, 1, [(0, 0)])],  # no take from group 1
    )
    errors = validate_rough_cut_plan(plan, takes, groups)
    assert any("Rough cut invariant violated" in e and "[1]" in e for e in errors)


def test_rough_cut_allows_intercutting_two_from_same_group():
    takes = [_take(i, [("w", float(i), float(i) + 0.5)]) for i in range(4)]
    groups = [
        {"group_id": 0, "item_indexes": [0, 1], "signal": "color"},
        {"group_id": 1, "item_indexes": [2, 3], "signal": "color"},
    ]
    plan = CuratedDirectorPlan(
        hook_order=0,
        selections=[
            _sel(0, 0, [(0, 0)]),
            _sel(1, 1, [(0, 0)]),  # both alternates of group 0 — legal
            _sel(2, 2, [(0, 0)]),
        ],
    )
    assert validate_rough_cut_plan(plan, takes, groups) == []
