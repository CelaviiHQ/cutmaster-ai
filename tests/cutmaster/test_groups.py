"""Tests for cutmaster.resolve_ops.groups — Rough cut group detector."""

from cutmaster_ai.cutmaster.resolve_ops.groups import (
    DEFAULT_SIMILARITY_THRESHOLD,
    GroupedItem,
    all_singletons,
    detect_groups,
    detect_groups_by_color,
    detect_groups_by_flag,
    detect_groups_by_similarity,
)


def _item(
    idx: int,
    *,
    color: str = "",
    flags: list[str] | None = None,
) -> GroupedItem:
    return GroupedItem(
        item_index=idx,
        source_name=f"take_{idx}.mov",
        start_s=float(idx * 10),
        end_s=float((idx + 1) * 10),
        clip_color=color,
        flags=flags or [],
    )


def _w(word: str) -> dict:
    return {"word": word, "start_time": 0.0, "end_time": 0.1, "speaker_id": "S1"}


# --- color signal ---------------------------------------------------------


def test_color_groups_adjacent_same_color():
    items = [
        _item(0, color="Orange"),
        _item(1, color="Orange"),
        _item(2, color="Blue"),
        _item(3, color="Blue"),
        _item(4, color="Blue"),
    ]
    groups = detect_groups_by_color(items)
    assert groups is not None
    assert [g["item_indexes"] for g in groups] == [[0, 1], [2, 3, 4]]
    assert all(g["signal"] == "color" for g in groups)


def test_color_returns_none_when_no_colors():
    items = [_item(0), _item(1), _item(2)]
    assert detect_groups_by_color(items) is None


def test_color_makes_uncoloured_items_singletons():
    items = [
        _item(0, color="Orange"),
        _item(1),
        _item(2, color="Orange"),
    ]
    groups = detect_groups_by_color(items)
    assert groups is not None
    # Non-adjacent Orange items don't merge across an uncoloured gap.
    assert [g["item_indexes"] for g in groups] == [[0], [1], [2]]
    assert [g["signal"] for g in groups] == ["color", "singleton", "color"]


# --- flag signal ----------------------------------------------------------


def test_flag_groups_adjacent_items():
    items = [
        _item(0, flags=["Red"]),
        _item(1, flags=["Red"]),
        _item(2, flags=["Green"]),
    ]
    groups = detect_groups_by_flag(items)
    assert groups is not None
    assert [g["item_indexes"] for g in groups] == [[0, 1], [2]]


def test_flag_returns_none_when_no_flags():
    items = [_item(0), _item(1)]
    assert detect_groups_by_flag(items) is None


# --- similarity signal ----------------------------------------------------


def test_similarity_merges_near_identical_retakes():
    items = [_item(i) for i in range(3)]
    transcripts = [
        [_w("the"), _w("rocket"), _w("launched"), _w("at"), _w("dawn")],
        [_w("the"), _w("rocket"), _w("launched"), _w("before"), _w("dawn")],
        [_w("totally"), _w("different"), _w("unrelated"), _w("sentence"), _w("here")],
    ]
    groups = detect_groups_by_similarity(items, transcripts, threshold=0.5)
    assert [g["item_indexes"] for g in groups] == [[0, 1], [2]]
    assert groups[0]["signal"] == "similarity"
    assert groups[1]["signal"] == "singleton"


def test_similarity_below_threshold_keeps_singletons():
    items = [_item(0), _item(1)]
    transcripts = [
        [_w("rocket"), _w("launched")],
        [_w("submarine"), _w("dived")],
    ]
    groups = detect_groups_by_similarity(items, transcripts, threshold=0.75)
    assert [g["item_indexes"] for g in groups] == [[0], [1]]


def test_similarity_empty_input():
    assert detect_groups_by_similarity([], []) == []


def test_similarity_length_mismatch_raises():
    try:
        detect_groups_by_similarity([_item(0)], [])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# --- dispatcher -----------------------------------------------------------


def test_dispatcher_prefers_color_over_similarity():
    items = [
        _item(0, color="Orange"),
        _item(1, color="Orange"),
    ]
    # transcripts are completely different — similarity would keep them apart
    transcripts = [
        [_w("alpha")],
        [_w("beta")],
    ]
    groups = detect_groups(items, transcripts)
    assert [g["signal"] for g in groups] == ["color"]
    assert [g["item_indexes"] for g in groups] == [[0, 1]]


def test_dispatcher_falls_back_to_similarity():
    items = [_item(0), _item(1)]
    transcripts = [
        [_w("same"), _w("words"), _w("here")],
        [_w("same"), _w("words"), _w("here")],
    ]
    groups = detect_groups(items, transcripts)
    assert groups[0]["signal"] == "similarity"


def test_dispatcher_empty():
    assert detect_groups([], []) == []


# --- all_singletons helper -------------------------------------------------


def test_all_singletons_detects_degenerate_partition():
    items = [_item(0), _item(1)]
    transcripts = [[_w("alpha")], [_w("beta")]]
    groups = detect_groups(items, transcripts)
    assert all_singletons(groups) is True


def test_all_singletons_false_when_any_group_has_multiple_items():
    items = [_item(0, color="Orange"), _item(1, color="Orange")]
    groups = detect_groups(items, [[], []])
    assert all_singletons(groups) is False


def test_default_threshold_is_0_75():
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.75
