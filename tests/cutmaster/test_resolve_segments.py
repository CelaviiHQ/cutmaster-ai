"""Unit tests for cutmaster.resolve_segments — mock Resolve items."""

from unittest.mock import MagicMock

import pytest

from cutmaster_ai.cutmaster.core.director import CutSegment
from cutmaster_ai.cutmaster.resolve_ops.segments import resolve_segments


def _mp_item(
    uid: str,
    name: str,
    speed_pct: str = "100",
    source_fps: str | None = None,
):
    m = MagicMock()
    m.GetUniqueId.return_value = uid
    m.GetName.return_value = name
    props = {"Speed": speed_pct}
    if source_fps is not None:
        props["FPS"] = source_fps
    m.GetClipProperty.side_effect = lambda k: props.get(k)
    return m


def _tl_item(start: int, end: int, mp_item, name: str = "", left_offset: int = 0):
    it = MagicMock()
    it.GetStart.return_value = start
    it.GetEnd.return_value = end
    it.GetLeftOffset.return_value = left_offset
    if name:
        it.GetName.return_value = name
    elif mp_item is not None:
        it.GetName.return_value = mp_item.GetName.return_value
    else:
        it.GetName.return_value = "compound_or_nested"
    it.GetMediaPoolItem.return_value = mp_item
    it.GetProperty.side_effect = lambda k: None
    return it


def _timeline(fps: float, start_frame: int, items: list):
    tl = MagicMock()
    tl.GetSetting.side_effect = lambda k: fps if k == "timelineFrameRate" else None
    tl.GetStartFrame.return_value = start_frame
    tl.GetItemListInTrack.return_value = items
    return tl


# ---------------------------------------------------------------------------
# Single-item segments (no split needed)
# ---------------------------------------------------------------------------


def test_segment_entirely_within_one_item():
    mp = _mp_item("UID1", "clip_A.mov")
    item = _tl_item(start=86400, end=86640, mp_item=mp)  # timeline 0..10s @ 24fps
    tl = _timeline(24.0, 86400, [item])

    [seg] = resolve_segments(tl, [CutSegment(start_s=2.0, end_s=5.0, reason="pick")])
    assert seg.source_item_id == "UID1"
    assert seg.source_item_name == "clip_A.mov"
    assert seg.part_index == 0 and seg.part_total == 1
    # 2s..5s at 24fps = 48..120 source frames
    assert seg.source_in_frame == 48
    assert seg.source_out_frame == 120
    assert seg.reason == "pick"
    assert seg.timeline_start_frame == 86448
    assert seg.timeline_end_frame == 86520


def test_left_offset_applied():
    mp = _mp_item("UID1", "clip.mov")
    # Source clip starts at frame 1000 (GetLeftOffset=1000)
    item = _tl_item(start=86400, end=86640, mp_item=mp, left_offset=1000)
    tl = _timeline(24.0, 86400, [item])

    [seg] = resolve_segments(tl, [CutSegment(start_s=1.0, end_s=2.0, reason="")])
    # offset_in_item = 24, source_frame = 1000 + 24 = 1024
    assert seg.source_in_frame == 1024
    assert seg.source_out_frame == 1048


# ---------------------------------------------------------------------------
# Cross-boundary segments — the whole point of this module
# ---------------------------------------------------------------------------


def test_auto_split_across_two_items():
    mp_a = _mp_item("UID_A", "clip_A.mov")
    mp_b = _mp_item("UID_B", "clip_B.mov")
    # Two items butted end-to-end at frame 86520 (t=5s)
    item_a = _tl_item(start=86400, end=86520, mp_item=mp_a)  # 0..5s
    item_b = _tl_item(start=86520, end=86640, mp_item=mp_b)  # 5..10s
    tl = _timeline(24.0, 86400, [item_a, item_b])

    # Segment 3s..7s crosses the boundary
    parts = resolve_segments(tl, [CutSegment(start_s=3.0, end_s=7.0, reason="hook")])

    assert len(parts) == 2
    a, b = parts

    # Piece 1: clip_A, 3s..5s (source frames 72..120)
    assert a.source_item_id == "UID_A"
    assert a.source_in_frame == 72
    assert a.source_out_frame == 120
    assert a.part_index == 0 and a.part_total == 2
    assert a.reason == "hook (part 1/2)"

    # Piece 2: clip_B, starts at item_b's t=0 (5s in timeline), goes 2s (to t=7s)
    assert b.source_item_id == "UID_B"
    assert b.source_in_frame == 0
    assert b.source_out_frame == 48
    assert b.part_index == 1 and b.part_total == 2
    assert b.reason == "hook (part 2/2)"

    # The pieces cover the full original range
    assert a.start_s == 3.0 and a.end_s == 5.0
    assert b.start_s == 5.0 and b.end_s == 7.0


def test_auto_split_across_three_items():
    mps = [_mp_item(f"UID_{x}", f"clip_{x}.mov") for x in ("A", "B", "C")]
    items = [
        _tl_item(86400, 86448, mps[0]),  # 0..2s
        _tl_item(86448, 86496, mps[1]),  # 2..4s
        _tl_item(86496, 86544, mps[2]),  # 4..6s
    ]
    tl = _timeline(24.0, 86400, items)

    parts = resolve_segments(tl, [CutSegment(start_s=1.0, end_s=5.0, reason="")])
    assert len(parts) == 3
    assert [p.source_item_name for p in parts] == ["clip_A.mov", "clip_B.mov", "clip_C.mov"]
    # Each piece has the correct part_total
    assert all(p.part_total == 3 for p in parts)
    assert [p.part_index for p in parts] == [0, 1, 2]


def test_segment_entirely_in_gap_raises():
    mp = _mp_item("UID1", "c.mov")
    item = _tl_item(86400, 86520, mp)  # 0..5s
    # Leave a gap 5..10s, then another item 10..15s
    item2 = _tl_item(86640, 86760, _mp_item("UID2", "c2.mov"))
    tl = _timeline(24.0, 86400, [item, item2])

    # Segment 6s..8s falls entirely in the gap
    with pytest.raises(ValueError, match="does not overlap"):
        resolve_segments(tl, [CutSegment(start_s=6.0, end_s=8.0, reason="")])


def test_segment_spanning_gap_produces_pieces_for_both_sides():
    mp_a = _mp_item("UID_A", "c_a.mov")
    mp_b = _mp_item("UID_B", "c_b.mov")
    item_a = _tl_item(86400, 86520, mp_a)  # 0..5s
    item_b = _tl_item(86640, 86760, mp_b)  # 10..15s, gap 5..10s
    tl = _timeline(24.0, 86400, [item_a, item_b])

    # 3s..12s spans [item_a tail] + [gap] + [item_b head]
    parts = resolve_segments(tl, [CutSegment(start_s=3.0, end_s=12.0, reason="")])
    # Gap produces no piece, so we get 2 pieces
    assert len(parts) == 2
    assert parts[0].source_item_name == "c_a.mov"
    assert parts[1].source_item_name == "c_b.mov"
    # The gap is not covered — reader should notice the discontinuity
    assert parts[0].end_s < parts[1].start_s


def test_compound_clip_raises_clear_error():
    item = _tl_item(86400, 86520, mp_item=None)
    tl = _timeline(24.0, 86400, [item])
    with pytest.raises(ValueError, match="media pool item"):
        resolve_segments(tl, [CutSegment(start_s=0.0, end_s=5.0, reason="")])


def test_inverted_range_raises():
    mp = _mp_item("UID1", "c.mov")
    item = _tl_item(86400, 86640, mp)
    tl = _timeline(24.0, 86400, [item])
    with pytest.raises(ValueError, match="non-positive duration"):
        resolve_segments(tl, [CutSegment(start_s=5.0, end_s=2.0, reason="")])


# ---------------------------------------------------------------------------
# fps-aware source-frame math (v2-8)
# ---------------------------------------------------------------------------


def test_source_fps_matching_timeline_still_passes_1_to_1():
    """Baseline: source 24 fps on 24 fps timeline → unchanged math."""
    mp = _mp_item("UID1", "c.mov", source_fps="24")
    item = _tl_item(86400, 86640, mp)
    tl = _timeline(24.0, 86400, [item])
    [seg] = resolve_segments(tl, [CutSegment(start_s=2.0, end_s=5.0, reason="")])
    assert seg.source_in_frame == 48  # 2s at 24fps
    assert seg.source_out_frame == 120  # 5s at 24fps


def test_source_30fps_on_24fps_timeline_scales_to_source_frames():
    """The v2-6 marker-past-end bug: 30 fps source on a 24 fps timeline.
    Timeline-seconds → source-frames must scale by source_fps / tl_fps,
    otherwise Resolve under-appends each piece to tl_fps/source_fps of the
    intended duration."""
    mp = _mp_item("UID1", "c.mov", source_fps="30")
    # Item covers timeline frames 86400..86640 (10s @ 24fps = 10s real-time,
    # which on a 30fps source is 300 source-frames).
    item = _tl_item(86400, 86640, mp)
    tl = _timeline(24.0, 86400, [item])
    [seg] = resolve_segments(tl, [CutSegment(start_s=2.0, end_s=5.0, reason="")])
    # 2s real-time → frame 60 of the source media; 5s → frame 150.
    assert seg.source_in_frame == 60
    assert seg.source_out_frame == 150
    assert any("differs from timeline fps" in w for w in seg.warnings)


def test_source_fps_mismatch_respects_left_offset():
    mp = _mp_item("UID1", "c.mov", source_fps="30")
    # Item starts at source-frame 900 (30s into the file).
    item = _tl_item(86400, 86640, mp, left_offset=900)
    tl = _timeline(24.0, 86400, [item])
    [seg] = resolve_segments(tl, [CutSegment(start_s=1.0, end_s=2.0, reason="")])
    # offset_in_item = 24 tl-frames × 30/24 = 30 source-frames, + left=900
    assert seg.source_in_frame == 930
    assert seg.source_out_frame == 960


def test_missing_source_fps_property_falls_back_to_timeline_fps():
    """Older mocks / compound items without an FPS clip property must still
    produce the v1 math rather than zero-duration pieces."""
    mp = _mp_item("UID1", "c.mov", source_fps=None)
    item = _tl_item(86400, 86640, mp)
    tl = _timeline(24.0, 86400, [item])
    [seg] = resolve_segments(tl, [CutSegment(start_s=2.0, end_s=5.0, reason="")])
    assert seg.source_in_frame == 48
    assert seg.source_out_frame == 120
    # No fps-mismatch warning when we fell back to tl fps.
    assert not any("differs from timeline fps" in w for w in seg.warnings)


def test_speed_ramped_item_warnings():
    mp = _mp_item("UID1", "fast.mov", speed_pct="200")  # 2x
    item = _tl_item(86400, 86520, mp)
    tl = _timeline(24.0, 86400, [item])
    [seg] = resolve_segments(tl, [CutSegment(start_s=1.0, end_s=2.0, reason="")])
    assert seg.speed == 2.0
    assert seg.speed_ramped is True
    assert any("speed-ramped" in w for w in seg.warnings)
    # offset_in_item=24 timeline frames, 2x speed → 48 source frames
    assert seg.source_in_frame == 48
    assert seg.source_out_frame == 96
