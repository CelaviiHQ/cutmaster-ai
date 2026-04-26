"""Resolve-free unit tests for shot_metadata_stamper.

The stamper has three deterministic decision paths to lock in:

- Modal-tag summary across cached samples (multi-field, not just shot_type).
- Marker write goes to ``TimelineItem.AddMarker(0, color, name, note, 1, customData)``
  with the JSON payload namespaced under ``CM_NAMESPACE`` so re-stamps
  can locate prior CutMaster markers.
- MediaPoolItem.SetMetadata writes Keywords + Description (skippable
  via ``touch_media_pool=False``).
- Clear path removes only CutMaster-namespaced markers, never editor markers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cutmaster_ai.cutmaster.analysis import shot_metadata_stamper, shot_tagger
from cutmaster_ai.cutmaster.analysis.shot_metadata_stamper import (
    CM_NAMESPACE,
    MARKER_COLOR,
    clear_shot_metadata_on_timeline,
    stamp_shot_metadata_on_timeline,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeMediaPoolItem:
    name: str = "source.mov"
    metadata: dict[str, str] = field(default_factory=dict)
    set_metadata_returns: bool = True

    def GetName(self) -> str:
        return self.name

    def SetMetadata(self, key: str, value: str) -> bool:
        self.metadata[key] = value
        return self.set_metadata_returns


@dataclass
class FakeTimelineItem:
    """Tracks markers as a frame->payload dict, mirroring Resolve's GetMarkers shape."""

    markers: dict[int, dict] = field(default_factory=dict)
    media_pool_item: FakeMediaPoolItem | None = None
    add_marker_returns: bool = True
    delete_marker_returns: bool = True

    def GetMarkers(self) -> dict[int, dict]:
        return dict(self.markers)

    def AddMarker(
        self,
        frame: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        customData: str = "",
    ) -> bool:
        self.markers[frame] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": duration,
            "customData": customData,
        }
        return self.add_marker_returns

    def DeleteMarkerAtFrame(self, frame: int) -> bool:
        if frame in self.markers:
            del self.markers[frame]
            return self.delete_marker_returns
        return False

    def GetMediaPoolItem(self) -> FakeMediaPoolItem | None:
        return self.media_pool_item


class FakeTimeline:
    def __init__(self, items: list[FakeTimelineItem]):
        self._items = items

    def GetItemListInTrack(self, kind: str, track: int):
        assert kind == "video" and track == 1
        return list(self._items)


@dataclass
class FakeSample:
    source_path: str
    source_ts_s: float


def _spec(idx: int) -> shot_tagger.VideoItemSpec:
    return shot_tagger.VideoItemSpec(
        item_index=idx,
        source_name=f"item_{idx}",
        timeline_offset_s=0.0,
        duration_s=10.0,
        segments=[("/m/a.mov", 0.0, 10.0)],
    )


def _patch_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeline: FakeTimeline,
    specs: list[shot_tagger.VideoItemSpec],
    samples_by_item: dict[int, list[FakeSample]],
    tags_by_sample: dict[tuple[str, float], shot_tagger.ShotTag | None],
) -> None:
    monkeypatch.setattr(
        shot_metadata_stamper,
        "_boilerplate",
        lambda: (None, object(), None),
        raising=False,
    )
    from cutmaster_ai.cutmaster.core import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda project, name: timeline)
    monkeypatch.setattr(
        shot_tagger,
        "build_video_item_specs",
        lambda tl, project=None, *, video_track=1: specs,
    )
    monkeypatch.setattr(
        shot_tagger,
        "plan_samples",
        lambda spec: samples_by_item.get(spec.item_index, []),
    )
    monkeypatch.setattr(
        shot_tagger,
        "_load_cached_tag",
        lambda path, ts: tags_by_sample.get((path, ts)),
    )


def _tag(
    shot_type: str = "closeup",
    framing: str = "speaker_centered",
    gesture: str = "calm",
    energy: int = 4,
    notable: str | None = None,
) -> shot_tagger.ShotTag:
    return shot_tagger.ShotTag(
        shot_type=shot_type,  # type: ignore[arg-type]
        framing=framing,  # type: ignore[arg-type]
        gesture_intensity=gesture,  # type: ignore[arg-type]
        visual_energy=energy,
        notable=notable,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stamps_marker_and_media_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    mp = FakeMediaPoolItem(name="A001.mov")
    item = FakeTimelineItem(media_pool_item=mp)
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={
            0: [FakeSample("/m/a.mov", t) for t in (0.3, 5.0, 9.7)],
        },
        tags_by_sample={
            ("/m/a.mov", 0.3): _tag("closeup", notable="coffee mug"),
            ("/m/a.mov", 5.0): _tag("closeup"),
            ("/m/a.mov", 9.7): _tag("wide"),  # outvoted by 2 closeups
        },
    )

    out = stamp_shot_metadata_on_timeline("Cut1")

    assert out["stamped"] == 1
    assert out["media_pool_writes"] == 1
    assert out["rows"][0]["action"] == "stamped"
    assert out["rows"][0]["shot_type"] == "closeup"
    assert out["rows"][0]["marker_added"] is True
    assert out["rows"][0]["media_pool_updated"] is True

    # Marker landed at frame 0 with the right color + namespace payload.
    assert 0 in item.markers
    m = item.markers[0]
    assert m["color"] == MARKER_COLOR
    assert m["name"] == "closeup"
    # customData format: "<namespace>:<json>" — startswith check works.
    assert m["customData"].startswith(CM_NAMESPACE + ":")
    assert "closeup" in m["customData"]

    # MediaPoolItem got both fields.
    assert "Keywords" in mp.metadata and "closeup" in mp.metadata["Keywords"]
    assert mp.metadata["Description"].startswith("[CutMaster]")
    assert "closeup" in mp.metadata["Description"]


def test_touch_media_pool_false_skips_source(monkeypatch: pytest.MonkeyPatch) -> None:
    mp = FakeMediaPoolItem()
    item = FakeTimelineItem(media_pool_item=mp)
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("broll")},
    )

    out = stamp_shot_metadata_on_timeline("Cut1", touch_media_pool=False)

    assert out["stamped"] == 1
    assert out["media_pool_writes"] == 0
    assert out["rows"][0]["marker_added"] is True
    assert out["rows"][0]["media_pool_updated"] is False
    assert mp.metadata == {}  # source untouched


def test_add_markers_false_only_writes_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    mp = FakeMediaPoolItem()
    item = FakeTimelineItem(media_pool_item=mp)
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("medium")},
    )

    out = stamp_shot_metadata_on_timeline("Cut1", add_markers=False)

    assert out["stamped"] == 1
    assert item.markers == {}  # no marker
    assert "medium" in mp.metadata["Keywords"]


def test_re_stamp_purges_prior_cm_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running the stamper must not stack duplicate CM markers."""
    mp = FakeMediaPoolItem()
    item = FakeTimelineItem(media_pool_item=mp)
    # Pre-seed: one CutMaster marker (would-be stale) + one editor marker.
    item.markers[0] = {
        "color": MARKER_COLOR,
        "name": "old",
        "note": "stale",
        "duration": 1,
        "customData": f"{CM_NAMESPACE}:hash-old",
    }
    item.markers[42] = {
        "color": "Red",
        "name": "editor's mark",
        "note": "do not delete",
        "duration": 1,
        "customData": "editor-private",
    }
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("over_shoulder")},
    )

    out = stamp_shot_metadata_on_timeline("Cut1")

    assert out["markers_removed"] == 1
    # Editor's marker is preserved.
    assert 42 in item.markers
    assert item.markers[42]["name"] == "editor's mark"
    # New CM marker is at frame 0 with fresh data.
    assert item.markers[0]["name"] == "over_shoulder"


def test_unknown_shot_type_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    mp = FakeMediaPoolItem()
    item = FakeTimelineItem(media_pool_item=mp)
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("unknown")},
    )

    out = stamp_shot_metadata_on_timeline("Cut1")

    assert out["stamped"] == 0
    assert out["skipped_unknown"] == 1
    assert item.markers == {}
    assert mp.metadata == {}


def test_no_cached_tags_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    mp = FakeMediaPoolItem()
    item = FakeTimelineItem(media_pool_item=mp)
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={},  # cache miss
    )

    out = stamp_shot_metadata_on_timeline("Cut1")

    assert out["stamped"] == 0
    assert out["skipped_no_tags"] == 1
    assert item.markers == {}


def test_clear_only_removes_cm_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    item = FakeTimelineItem()
    item.markers[0] = {
        "color": MARKER_COLOR,
        "name": "closeup",
        "note": "x",
        "duration": 1,
        "customData": f"{CM_NAMESPACE}:hash",
    }
    item.markers[100] = {
        "color": "Yellow",
        "name": "editor pin",
        "note": "y",
        "duration": 1,
        "customData": "editor-flag",
    }
    tl = FakeTimeline([item])
    monkeypatch.setattr(
        shot_metadata_stamper,
        "_boilerplate",
        lambda: (None, object(), None),
        raising=False,
    )
    from cutmaster_ai.cutmaster.core import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda project, name: tl)

    out = clear_shot_metadata_on_timeline("Cut1")

    assert out["markers_removed"] == 1
    assert 0 not in item.markers  # CM marker gone
    assert 100 in item.markers  # editor marker survived


def test_timeline_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shot_metadata_stamper,
        "_boilerplate",
        lambda: (None, object(), None),
        raising=False,
    )
    from cutmaster_ai.cutmaster.core import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda *a, **k: None)

    with pytest.raises(ValueError, match="not found"):
        stamp_shot_metadata_on_timeline("Missing")
    with pytest.raises(ValueError, match="not found"):
        clear_shot_metadata_on_timeline("Missing")
