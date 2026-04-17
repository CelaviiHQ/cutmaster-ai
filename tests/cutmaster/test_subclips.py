"""Unit tests for cutmaster.subclips — mock AppendToTimeline."""

from unittest.mock import MagicMock

import pytest

from celavii_resolve.cutmaster.subclips import append_subclips_with_ranges


def _fake_project():
    tl = MagicMock()
    proj = MagicMock()
    proj.GetCurrentTimeline.return_value = tl
    return proj, tl


def _fake_media_pool(items_by_uid):
    """Build a media pool whose root folder yields the given items."""
    root = MagicMock()
    root.GetClipList.return_value = [
        _fake_mp_item(uid, name) for uid, name in items_by_uid.items()
    ]
    root.GetSubFolderList.return_value = []
    mp = MagicMock()
    mp.GetRootFolder.return_value = root
    return mp


def _fake_mp_item(uid, name):
    m = MagicMock()
    m.GetUniqueId.return_value = uid
    m.GetName.return_value = name
    return m


def test_appends_with_ranges_and_omits_media_type_for_both():
    project, _ = _fake_project()
    mp = _fake_media_pool({"UID1": "clip.mov"})
    mp.AppendToTimeline.return_value = [MagicMock()]

    segments = [
        {"source_item_id": "UID1", "start_frame": 100, "end_frame": 200},
    ]
    result = append_subclips_with_ranges(project, mp, segments)

    assert result["appended"] == 1
    assert result["errors"] == []
    call_args = mp.AppendToTimeline.call_args[0][0]
    assert len(call_args) == 1
    info = call_args[0]
    assert info["startFrame"] == 100
    assert info["endFrame"] == 200
    assert info["trackIndex"] == 1
    # 'both' should NOT set mediaType → Resolve appends V + linked A
    assert "mediaType" not in info


def test_media_type_video_only_sets_media_type_1():
    project, _ = _fake_project()
    mp = _fake_media_pool({"UID1": "c.mov"})
    mp.AppendToTimeline.return_value = [MagicMock()]

    segments = [{"source_item_id": "UID1", "start_frame": 0, "end_frame": 50,
                 "media_type": "video"}]
    append_subclips_with_ranges(project, mp, segments)
    assert mp.AppendToTimeline.call_args[0][0][0]["mediaType"] == 1


def test_rejects_inverted_range():
    project, _ = _fake_project()
    mp = _fake_media_pool({"UID1": "c.mov"})

    segments = [{"source_item_id": "UID1", "start_frame": 200, "end_frame": 100}]
    result = append_subclips_with_ranges(project, mp, segments)

    assert result["appended"] == 0
    assert any("end_frame" in e for e in result["errors"])
    mp.AppendToTimeline.assert_not_called()


def test_missing_source_item_logged_as_error():
    project, _ = _fake_project()
    mp = _fake_media_pool({})  # empty pool

    segments = [{"source_item_id": "GHOST", "start_frame": 0, "end_frame": 50}]
    result = append_subclips_with_ranges(project, mp, segments)

    assert result["appended"] == 0
    assert any("not found" in e for e in result["errors"])


def test_no_current_timeline_raises():
    project = MagicMock()
    project.GetCurrentTimeline.return_value = None
    mp = _fake_media_pool({})
    with pytest.raises(ValueError, match="No current timeline"):
        append_subclips_with_ranges(project, mp, [])


def test_partial_success_mixed_segments():
    project, _ = _fake_project()
    mp = _fake_media_pool({"UID1": "c.mov"})
    mp.AppendToTimeline.return_value = [MagicMock()]  # one successful append

    segments = [
        {"source_item_id": "UID1", "start_frame": 0, "end_frame": 50},
        {"source_item_id": "GHOST", "start_frame": 0, "end_frame": 50},  # bad
    ]
    result = append_subclips_with_ranges(project, mp, segments)

    assert result["appended"] == 1
    assert len(result["errors"]) == 1
    assert len(mp.AppendToTimeline.call_args[0][0]) == 1  # only the good one
