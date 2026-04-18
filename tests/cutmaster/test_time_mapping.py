"""Tests for cutmaster.time_mapping — source → new-timeline mapper.

Covers the two scenarios execute and captions both rely on:
  1. A moment on the source timeline that falls inside a kept piece must
     map to the concatenated-cut position (running offset + in-piece delta).
  2. A moment in a cut-out gap must return None so the caller can skip it.
"""

import pytest

from celavii_resolve.cutmaster.media.time_mapping import (
    map_source_to_new_timeline,
    remap_words_to_new_timeline,
)


def _piece(start: float, end: float) -> dict:
    return {"start_s": start, "end_s": end}


def test_map_inside_first_piece():
    resolved = [_piece(10.0, 12.0), _piece(30.0, 33.0)]
    # 11.0s on source = 1.0s into piece 1 = 1.0s on new timeline.
    assert map_source_to_new_timeline(resolved, 11.0) == 1.0


def test_map_inside_second_piece_accumulates_offset():
    resolved = [_piece(10.0, 12.0), _piece(30.0, 33.0)]
    # Piece 1 is 2.0s long; 31.0s on source = 1.0s into piece 2 → 3.0s on new.
    assert map_source_to_new_timeline(resolved, 31.0) == 3.0


def test_map_in_gap_returns_none():
    resolved = [_piece(10.0, 12.0), _piece(30.0, 33.0)]
    # 20.0s lives between the two selected pieces — it's cut out.
    assert map_source_to_new_timeline(resolved, 20.0) is None


def test_map_before_first_piece_returns_none():
    resolved = [_piece(10.0, 12.0)]
    assert map_source_to_new_timeline(resolved, 5.0) is None


def test_map_after_last_piece_returns_none():
    resolved = [_piece(10.0, 12.0)]
    assert map_source_to_new_timeline(resolved, 100.0) is None


def test_map_on_boundary_inclusive():
    # The start/end endpoints are inclusive — this matches marker behaviour.
    resolved = [_piece(10.0, 12.0)]
    assert map_source_to_new_timeline(resolved, 10.0) == 0.0
    assert map_source_to_new_timeline(resolved, 12.0) == 2.0


def test_remap_words_filters_and_translates():
    resolved = [_piece(10.0, 12.0), _piece(30.0, 33.0)]
    words = [
        {"word": "first", "start_time": 10.2, "end_time": 10.6, "speaker_id": "S1"},
        {"word": "(cut)", "start_time": 20.0, "end_time": 20.3, "speaker_id": "S1"},
        {"word": "second", "start_time": 30.5, "end_time": 31.0, "speaker_id": "S1"},
    ]
    out = remap_words_to_new_timeline(words, resolved)
    assert len(out) == 2
    assert out[0]["word"] == "first"
    assert out[0]["start_time"] == pytest.approx(0.2)
    assert out[1]["word"] == "second"
    assert out[1]["start_time"] == pytest.approx(2.5)


def test_remap_word_straddling_boundary_preserves_duration():
    # Word starts inside piece 1 but ends past piece-1's cut — remap keeps
    # the original duration relative to the mapped start so captions don't
    # shrink to zero.
    resolved = [_piece(10.0, 12.0)]
    words = [
        {"word": "cut-off", "start_time": 11.8, "end_time": 13.0, "speaker_id": "S1"},
    ]
    out = remap_words_to_new_timeline(words, resolved)
    assert len(out) == 1
    # new_start = 1.8, original duration was 1.2 → new_end = 3.0
    assert out[0]["start_time"] == pytest.approx(1.8)
    assert out[0]["end_time"] == pytest.approx(3.0)
