"""Unit tests for v4 Layer C shot tagging — Resolve-free slices.

Covers the deterministic bits that don't need Gemini / ffmpeg:

- Sampling cadence (FRAME_EDGE_OFFSET_S + FRAME_STRIDE_S).
- Cache round-trip (ShotTag → JSON → ShotTag).
- Tag attachment to transcript words via bisect.

The Gemini call path is exercised via the multimodal ``images=`` test
in ``test_llm_helper`` — no real network here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cutmaster_ai.cutmaster.analysis import shot_tagger
from cutmaster_ai.cutmaster.analysis.shot_tagger import (
    FRAME_EDGE_OFFSET_S,
    FRAME_STRIDE_S,
    ShotTag,
    VideoItemSpec,
    attach_tags_to_transcript,
    plan_samples,
)


def _spec(segments, *, item_index=0, timeline_offset=0.0, source_name="clip_a"):
    duration = sum(out_s - in_s for _, in_s, out_s in segments)
    return VideoItemSpec(
        item_index=item_index,
        source_name=source_name,
        timeline_offset_s=timeline_offset,
        duration_s=duration,
        segments=segments,
    )


def test_plan_samples_single_segment_stride():
    # 12s item → edge (0.3), 5, 10, edge (11.7)
    spec = _spec([("/tmp/a.mov", 0.0, 12.0)])
    samples = plan_samples(spec)
    source_ts = [round(s.source_ts_s, 3) for s in samples]
    assert source_ts[0] == FRAME_EDGE_OFFSET_S
    assert source_ts[-1] == pytest.approx(12.0 - FRAME_EDGE_OFFSET_S)
    # intermediate strides at 5.0 and 10.0
    assert FRAME_STRIDE_S in source_ts
    assert 2 * FRAME_STRIDE_S in source_ts


def test_plan_samples_timeline_offset_propagates():
    spec = _spec([("/tmp/a.mov", 30.0, 35.0)], timeline_offset=100.0)
    samples = plan_samples(spec)
    # Every sample's timeline_ts_s should be offset by 100.
    for s in samples:
        assert s.timeline_ts_s == pytest.approx(s.source_ts_s - 30.0 + 100.0)


def test_plan_samples_skips_tiny_items():
    # Item shorter than the edge offset → no samples.
    spec = _spec([("/tmp/a.mov", 0.0, 0.1)])
    assert plan_samples(spec) == []


def test_plan_samples_empty_segments():
    spec = VideoItemSpec(
        item_index=0,
        source_name="empty",
        timeline_offset_s=0.0,
        duration_s=0.0,
        segments=[],
    )
    assert plan_samples(spec) == []


def test_plan_samples_multi_segment_no_duplicate_inner_edges():
    # Two 6s segments should not produce end-of-seg-0 + start-of-seg-1 edges.
    spec = _spec(
        [
            ("/tmp/a.mov", 0.0, 6.0),
            ("/tmp/b.mov", 0.0, 6.0),
        ]
    )
    samples = plan_samples(spec)
    # Only first segment gets a start edge, only last gets an end edge.
    start_edges = [s for s in samples if s.source_ts_s == FRAME_EDGE_OFFSET_S]
    assert len(start_edges) == 1  # only first seg
    assert samples[0].source_path == "/tmp/a.mov"
    assert samples[-1].source_path == "/tmp/b.mov"


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(shot_tagger, "CACHE_ROOT", tmp_path)
    tag = ShotTag(
        shot_type="closeup",
        framing="speaker_centered",
        gesture_intensity="emphatic",
        visual_energy=7,
        notable="speaker leans in",
    )
    shot_tagger._save_cached_tag("/srv/media/clip.mov", 1.234, tag)
    loaded = shot_tagger._load_cached_tag("/srv/media/clip.mov", 1.234)
    assert loaded is not None
    assert loaded.shot_type == "closeup"
    assert loaded.visual_energy == 7
    # Different timestamp = different file = miss.
    assert shot_tagger._load_cached_tag("/srv/media/clip.mov", 2.0) is None
    # Different source path = different directory = miss.
    assert shot_tagger._load_cached_tag("/other/clip.mov", 1.234) is None


def test_cache_key_ms_rounding(tmp_path, monkeypatch):
    """1.234s and 1.2344s land in the same cache file (ms resolution)."""
    monkeypatch.setattr(shot_tagger, "CACHE_ROOT", tmp_path)
    tag = ShotTag(shot_type="medium")
    shot_tagger._save_cached_tag("/srv/clip.mov", 1.234, tag)
    # 1.2344 rounds to the same ms bucket.
    assert shot_tagger._load_cached_tag("/srv/clip.mov", 1.2344) is not None


def test_attach_tags_uses_latest_preceding_tag():
    transcript = [
        {"word": "hello", "start_time": 0.1, "end_time": 0.3},
        {"word": "world", "start_time": 5.6, "end_time": 5.9},
        {"word": "bye", "start_time": 11.0, "end_time": 11.4},
    ]
    tagged = [
        shot_tagger.TaggedFrame(
            item_index=0,
            source_path="/tmp/a.mov",
            source_ts_s=0.3,
            timeline_ts_s=0.3,
            tag=ShotTag(shot_type="closeup"),
        ),
        shot_tagger.TaggedFrame(
            item_index=0,
            source_path="/tmp/a.mov",
            source_ts_s=5.0,
            timeline_ts_s=5.0,
            tag=ShotTag(shot_type="medium"),
        ),
        shot_tagger.TaggedFrame(
            item_index=1,
            source_path="/tmp/b.mov",
            source_ts_s=0.3,
            timeline_ts_s=10.3,
            tag=ShotTag(shot_type="wide"),
        ),
    ]
    annotated = attach_tags_to_transcript(transcript, tagged)
    assert annotated[0]["shot_tag"]["shot_type"] == "closeup"
    assert annotated[1]["shot_tag"]["shot_type"] == "medium"
    assert annotated[2]["shot_tag"]["shot_type"] == "wide"


def test_attach_tags_word_before_first_tag_falls_back():
    transcript = [{"word": "x", "start_time": 0.0, "end_time": 0.1}]
    tagged = [
        shot_tagger.TaggedFrame(
            item_index=0,
            source_path="/tmp/a.mov",
            source_ts_s=1.0,
            timeline_ts_s=5.0,
            tag=ShotTag(shot_type="closeup"),
        ),
    ]
    annotated = attach_tags_to_transcript(transcript, tagged)
    assert annotated[0]["shot_tag"]["shot_type"] == "closeup"


def test_attach_tags_no_tags_passes_through():
    transcript = [{"word": "solo", "start_time": 0.0, "end_time": 0.2}]
    annotated = attach_tags_to_transcript(transcript, [])
    assert annotated == transcript
    assert "shot_tag" not in annotated[0]


def test_source_key_stable_and_unique():
    from cutmaster_ai.cutmaster.media.ffmpeg_frames import source_key

    a = source_key("/srv/media/clip.mov")
    b = source_key("/srv/media/clip.mov")
    c = source_key("/srv/media/other.mov")
    assert a == b
    assert a != c
    assert len(a) == 40  # sha1 hex


def test_build_video_item_specs_skips_unresolvable(monkeypatch):
    """Items without an MP item or empty segments should not crash the walk."""

    class FakeItem:
        def __init__(self, *, mp, segments_fn_key=None):
            self._mp = mp
            self._key = segments_fn_key

        def GetMediaPoolItem(self):
            return self._mp

        def GetDuration(self):
            return 24  # one second at 24fps

        def GetStart(self):
            return 0

    class FakeTl:
        def __init__(self, items):
            self._items = items

        def GetItemListInTrack(self, track, idx):
            return self._items if track == "video" and idx == 1 else []

    class FakeMp:
        def GetName(self):
            return "clipA"

    # Two items: one with MP but resolve_item_to_segments returns [];
    # one without MP entirely. Neither should appear in the result.
    items = [FakeItem(mp=FakeMp()), FakeItem(mp=None)]
    tl = FakeTl(items)

    monkeypatch.setattr(
        shot_tagger,
        "build_video_item_specs",
        shot_tagger.build_video_item_specs,  # keep real impl
    )
    # Stub the Resolve-facing helpers so we can run without DaVinci.
    import cutmaster_ai.cutmaster.media.frame_math as fm
    import cutmaster_ai.cutmaster.media.source_resolver as sr

    monkeypatch.setattr(fm, "_timeline_fps", lambda _tl: 24.0)
    monkeypatch.setattr(fm, "_timeline_start_frame", lambda _tl: 0)
    monkeypatch.setattr(sr, "resolve_item_to_segments", lambda *_a, **_k: [])

    # Passing video_track=1 explicitly bypasses the track_picker call
    # (which requires GetTrackCount / GetTrackName that this fake tl
    # doesn't implement).
    specs = shot_tagger.build_video_item_specs(tl, project=object(), video_track=1)
    assert specs == []


def test_shot_tag_response_validator():
    from cutmaster_ai.cutmaster.analysis.shot_tagger import ShotTagResponse

    # Count mismatch is the primary validation; schema level handles the rest.
    resp = ShotTagResponse(tags=[ShotTag(), ShotTag()])
    assert len(resp.tags) == 2
    # Path coverage for Literal enforcement.
    with pytest.raises(ValidationError):
        ShotTag(shot_type="nonsense")


def test_cache_path_ms_format(tmp_path, monkeypatch):
    monkeypatch.setattr(shot_tagger, "CACHE_ROOT", tmp_path)
    p = shot_tagger._cache_path("/srv/x.mov", 42.5)
    assert p.name == "0000042500.json"  # zero-padded ms
    assert Path(p).parent.name == shot_tagger._cache_dir("/srv/x.mov").name
