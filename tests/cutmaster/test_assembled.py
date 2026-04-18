"""Tests for cutmaster.assembled — per-item transcript splitter + take builder."""

from celavii_resolve.cutmaster.assembled import (
    ItemSummary,
    build_take_entries,
    split_transcript_per_item,
)


def _item(idx: int, start: float, end: float, name: str = "clip.mov") -> ItemSummary:
    return ItemSummary(item_index=idx, source_name=name, start_s=start, end_s=end)


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start_time": start, "end_time": end, "speaker_id": "S1"}


def test_split_assigns_words_to_overlapping_item():
    items = [_item(0, 0.0, 10.0), _item(1, 10.0, 20.0)]
    transcript = [
        _w("one", 1.0, 1.5),
        _w("two", 9.5, 9.9),
        _w("three", 10.2, 10.8),
        _w("four", 19.5, 19.9),
    ]
    out = split_transcript_per_item(transcript, items)
    assert len(out) == 2
    assert [w["word"] for w in out[0]] == ["one", "two"]
    assert [w["word"] for w in out[1]] == ["three", "four"]


def test_split_drops_words_in_gaps():
    # Item 0 spans [0, 5); item 1 spans [10, 15). Words between 5 and 10 are gap material.
    items = [_item(0, 0.0, 5.0), _item(1, 10.0, 15.0)]
    transcript = [
        _w("kept", 2.0, 2.5),
        _w("gap", 7.0, 7.5),
        _w("kept2", 11.0, 11.5),
    ]
    out = split_transcript_per_item(transcript, items)
    assert [w["word"] for w in out[0]] == ["kept"]
    assert [w["word"] for w in out[1]] == ["kept2"]


def test_split_first_match_wins_for_boundary_words():
    # Word at exactly 5.0 falls inside item 0's [0, 5) — NO, exclusive end.
    # Falls inside item 1's [5, 10). The first-match loop should put it there.
    items = [_item(0, 0.0, 5.0), _item(1, 5.0, 10.0)]
    transcript = [_w("boundary", 5.0, 5.5)]
    out = split_transcript_per_item(transcript, items)
    assert [w["word"] for w in out[0]] == []
    assert [w["word"] for w in out[1]] == ["boundary"]


def test_build_take_entries_indexes_words_per_take():
    items = [_item(0, 0.0, 10.0, "first.mov"), _item(1, 10.0, 20.0, "second.mov")]
    per_item = [
        [_w("hello", 0.0, 0.5), _w("world.", 0.5, 1.0)],
        [_w("goodbye.", 11.0, 11.5)],
    ]
    takes = build_take_entries(items, per_item)
    assert len(takes) == 2
    assert takes[0]["item_index"] == 0
    assert takes[0]["source_name"] == "first.mov"
    assert [t["word"] for t in takes[0]["transcript"]] == ["hello", "world."]
    assert [t["i"] for t in takes[0]["transcript"]] == [0, 1]
    assert takes[1]["transcript"][0]["word"] == "goodbye."
    assert takes[1]["transcript"][0]["i"] == 0


def test_build_take_entries_preserves_empty_takes():
    items = [_item(0, 0.0, 5.0), _item(1, 5.0, 10.0)]
    per_item = [[_w("one", 0.0, 0.5)], []]
    takes = build_take_entries(items, per_item)
    assert takes[0]["transcript"]
    assert takes[1]["transcript"] == []
    # item_index mapping stays stable even when a take has no words.
    assert takes[1]["item_index"] == 1
