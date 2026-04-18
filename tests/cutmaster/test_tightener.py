"""Tests for cutmaster.tightener — per-take block segmentation + stats."""

import pytest

from celavii_resolve.cutmaster.analysis.tightener import (
    build_tightener_segments,
    tightener_stats,
)


def _take(idx: int, words: list[tuple[str, float, float]], name: str = "t.mov") -> dict:
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


# ------------------------- segmenter -----------------------------------


def test_empty_takes_yields_no_segments():
    assert build_tightener_segments([]) == []


def test_empty_transcript_take_is_skipped():
    t = {
        "item_index": 0,
        "source_name": "silent.mov",
        "start_s": 0.0,
        "end_s": 5.0,
        "transcript": [],
    }
    assert build_tightener_segments([t]) == []


def test_single_contiguous_take_becomes_one_segment():
    take = _take(
        0,
        [
            ("Hello", 0.0, 0.4),
            ("world", 0.45, 0.8),  # tiny gap, below threshold
            ("today.", 0.82, 1.2),
        ],
    )
    segs = build_tightener_segments([take], gap_threshold_s=0.3)
    assert len(segs) == 1
    assert segs[0].start_s == pytest.approx(0.0)
    assert segs[0].end_s == pytest.approx(1.2)


def test_large_gap_splits_take_into_blocks():
    take = _take(
        0,
        [
            ("first", 0.0, 0.4),
            ("block.", 0.4, 0.8),
            # 1.5s gap — scrubber removed a filler here
            ("second", 2.3, 2.7),
            ("block.", 2.7, 3.1),
        ],
    )
    segs = build_tightener_segments([take], gap_threshold_s=0.3)
    assert len(segs) == 2
    assert segs[0].start_s == pytest.approx(0.0)
    assert segs[0].end_s == pytest.approx(0.8)
    assert segs[1].start_s == pytest.approx(2.3)
    assert segs[1].end_s == pytest.approx(3.1)


def test_multiple_takes_preserve_order():
    takes = [
        _take(0, [("alpha", 0.0, 0.4), ("beta", 0.4, 0.8)], "t0.mov"),
        _take(1, [("gamma", 10.0, 10.4), ("delta", 10.4, 10.8)], "t1.mov"),
    ]
    segs = build_tightener_segments(takes)
    assert len(segs) == 2
    assert segs[0].start_s == pytest.approx(0.0)
    assert segs[1].start_s == pytest.approx(10.0)


def test_segment_reason_includes_take_and_block_info():
    take = _take(
        0,
        [
            ("a", 0.0, 0.2),
            ("b", 0.2, 0.4),
            # gap
            ("c", 1.0, 1.2),
        ],
        name="clip1.mov",
    )
    segs = build_tightener_segments([take], gap_threshold_s=0.3)
    assert "take 0" in segs[0].reason
    assert "clip1.mov" in segs[0].reason
    assert "block 1/2" in segs[0].reason
    assert "block 2/2" in segs[1].reason


def test_single_block_reason_omits_block_suffix():
    take = _take(0, [("only", 0.0, 0.5)], name="one.mov")
    segs = build_tightener_segments([take])
    assert len(segs) == 1
    assert "block" not in segs[0].reason


def test_gap_threshold_is_configurable():
    take = _take(
        0,
        [
            ("a", 0.0, 0.5),
            ("b", 0.9, 1.2),  # 0.4s gap
        ],
    )
    # At threshold=0.5 the gap stays within one block.
    assert len(build_tightener_segments([take], gap_threshold_s=0.5)) == 1
    # At threshold=0.3 the gap splits the take.
    assert len(build_tightener_segments([take], gap_threshold_s=0.3)) == 2


# ------------------------- stats ---------------------------------------


def test_stats_reports_kept_and_original_counts():
    raw = [{"word": str(i), "start_time": i * 0.1, "end_time": i * 0.1 + 0.05} for i in range(10)]
    takes = [_take(0, [("a", 0.0, 0.4), ("b", 0.5, 0.9)])]
    segs = build_tightener_segments(takes)
    stats = tightener_stats(raw, takes, segs)
    assert stats["original_words"] == 10
    assert stats["kept_words"] == 2


def test_stats_percent_tighter_zero_when_no_gaps():
    # Single-block take: segments cover the full take interval.
    take = _take(0, [("a", 0.0, 0.4), ("b", 0.4, 0.8)])
    # Patch take end to match word end exactly so percent_tighter is ~0.
    take["start_s"] = 0.0
    take["end_s"] = 0.8
    segs = build_tightener_segments([take])
    stats = tightener_stats([], [take], segs)
    assert stats["percent_tighter"] == pytest.approx(0.0, abs=1e-6)


def test_stats_percent_tighter_positive_when_gaps_remove_time():
    take = _take(0, [("a", 0.0, 0.4), ("b", 2.0, 2.4)])
    take["start_s"] = 0.0
    take["end_s"] = 2.4
    segs = build_tightener_segments([take], gap_threshold_s=0.3)
    # Take = 2.4s; segments = (0.4 + 0.4) = 0.8s → ~0.67 tighter.
    stats = tightener_stats([], [take], segs)
    assert 0.6 < stats["percent_tighter"] < 0.7


def test_stats_handles_zero_take_duration():
    stats = tightener_stats([], [], [])
    assert stats["percent_tighter"] == 0.0
    assert stats["kept_words"] == 0
