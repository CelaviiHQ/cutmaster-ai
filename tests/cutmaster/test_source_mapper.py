"""Unit tests for cutmaster.source_mapper — mock Resolve objects."""

from unittest.mock import MagicMock

import pytest

from celavii_resolve.cutmaster.source_mapper import (
    TimelineMappingError,
    timeline_time_to_source,
)


def _fake_mp_item(uid: str, name: str, speed_str: str = "100"):
    m = MagicMock()
    m.GetUniqueId.return_value = uid
    m.GetName.return_value = name
    m.GetClipProperty.side_effect = lambda k: {"Speed": speed_str}.get(k)
    return m


def _fake_item(start, end, mp_item, left_offset=240, item_speed=None):
    it = MagicMock()
    it.GetStart.return_value = start
    it.GetEnd.return_value = end
    it.GetLeftOffset.return_value = left_offset
    it.GetMediaPoolItem.return_value = mp_item
    it.GetProperty.side_effect = lambda k: {"Speed": item_speed}.get(k) if item_speed else None
    return it


def _fake_timeline(fps, start_frame, items):
    tl = MagicMock()
    tl.GetSetting.side_effect = lambda k: fps if k == "timelineFrameRate" else None
    tl.GetStartFrame.return_value = start_frame
    tl.GetItemListInTrack.return_value = items
    return tl


def test_maps_midpoint_of_item():
    mp = _fake_mp_item("UID1", "clip.mov")
    item = _fake_item(start=86400, end=86520, mp_item=mp, left_offset=240)
    tl = _fake_timeline(24.0, 86400, [item])

    # Timeline seconds 2.5 → timeline frame 86460 → offset into item = 60
    result = timeline_time_to_source(tl, 2.5)
    assert result["source_item_id"] == "UID1"
    assert result["source_item_name"] == "clip.mov"
    assert result["source_frame"] == 300
    assert result["speed"] == 1.0
    assert result["speed_ramped"] is False


def test_gap_raises():
    mp = _fake_mp_item("UID1", "c.mov")
    item = _fake_item(86400, 86500, mp)  # covers t=0..~4.16s @ 24fps
    tl = _fake_timeline(24.0, 86400, [item])
    with pytest.raises(TimelineMappingError, match="gap"):
        timeline_time_to_source(tl, 100.0)


def test_empty_track_raises():
    tl = _fake_timeline(24.0, 86400, [])
    with pytest.raises(TimelineMappingError, match="No items"):
        timeline_time_to_source(tl, 0.0)


def test_missing_mp_item_raises():
    item = _fake_item(86400, 86500, mp_item=None)
    tl = _fake_timeline(24.0, 86400, [item])
    with pytest.raises(TimelineMappingError, match="no media pool item"):
        timeline_time_to_source(tl, 1.0)


def test_speed_ramp_flagged_and_applied():
    mp = _fake_mp_item("UID2", "fast.mov", speed_str="200")  # 2x
    item = _fake_item(86400, 86520, mp_item=mp, left_offset=0)
    tl = _fake_timeline(24.0, 86400, [item])

    # Timeline 1.0s → timeline frame 86424 → offset 24 frames in timeline
    # At 2x speed, source advances by 24 * 2 = 48 frames
    result = timeline_time_to_source(tl, 1.0)
    assert result["source_frame"] == 48
    assert result["speed"] == 2.0
    assert result["speed_ramped"] is True
