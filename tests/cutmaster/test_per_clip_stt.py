"""Tests for per-clip STT (v2-6) — specs, cache, stitching, orchestrator."""

from __future__ import annotations

import asyncio
import json

from cutmaster_ai.cutmaster.stt.per_clip import (
    ClipAudioSpec,
    clip_metadata_table,
    load_cached_words,
    save_cached_words,
    stitch_transcripts,
    transcribe_per_clip,
)


def _spec(
    idx: int,
    path: str = "/tmp/take.mov",
    in_f: int = 0,
    out_f: int = 240,
    offset: float = 0.0,
    duration: float = 10.0,
    name: str | None = None,
    fps: float = 24.0,
) -> ClipAudioSpec:
    in_s = in_f / fps
    out_s = out_f / fps
    return ClipAudioSpec(
        item_index=idx,
        source_name=name or f"take_{idx}.mov",
        source_path=path,
        source_in_frame=in_f,
        source_out_frame=out_f,
        timeline_offset_s=offset,
        duration_s=duration,
        segments=[(path, in_s, out_s)],
    )


# ------------------------- cache key stability -----------------------------


def test_cache_key_is_stable_across_instances():
    a = _spec(0, "/a.mov", 100, 340)
    b = _spec(0, "/a.mov", 100, 340)
    assert a.cache_key == b.cache_key


def test_cache_key_changes_when_source_range_changes():
    """Trimming a take must invalidate its cache; sibling takes don't."""
    base = _spec(0, "/a.mov", 100, 340)
    trimmed = _spec(0, "/a.mov", 100, 300)  # shortened
    moved = _spec(0, "/a.mov", 120, 360)  # shifted in
    other_source = _spec(0, "/b.mov", 100, 340)
    keys = {base.cache_key, trimmed.cache_key, moved.cache_key, other_source.cache_key}
    assert len(keys) == 4


# ------------------------- cache round-trip --------------------------------


def test_cache_round_trip(tmp_path):
    spec = _spec(0)
    words = [
        {"word": "hi", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
    ]
    save_cached_words(spec, words, root=tmp_path)
    loaded = load_cached_words(spec, root=tmp_path)
    assert loaded == words


def test_cache_miss_returns_none(tmp_path):
    assert load_cached_words(_spec(0), root=tmp_path) is None


def test_cache_file_corrupt_returns_none(tmp_path):
    spec = _spec(0)
    save_cached_words(spec, [{"word": "x", "start_time": 0, "end_time": 1}], root=tmp_path)
    # Corrupt the file.
    path = tmp_path / f"{spec.cache_key}.json"
    path.write_text("not-json{{{")
    assert load_cached_words(spec, root=tmp_path) is None


# ------------------------- stitching ---------------------------------------


def test_stitch_offsets_to_timeline_seconds_and_attaches_metadata():
    specs = [
        _spec(0, offset=0.0, duration=5.0),
        _spec(1, offset=5.0, duration=5.0, name="take_1.mov"),
    ]
    per_clip = [
        [
            {"word": "hi", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"},
            {"word": "there", "start_time": 0.4, "end_time": 0.9, "speaker_id": "S1"},
        ],
        [
            {"word": "yeah", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S2"},
        ],
    ]
    out = stitch_transcripts(specs, per_clip)
    # Timestamps are offset into timeline seconds.
    assert out[0]["start_time"] == 0.0
    assert out[2]["start_time"] == 5.0
    # clip_index + clip_metadata land on every word.
    assert out[0]["clip_index"] == 0
    assert out[2]["clip_index"] == 1
    assert out[2]["clip_metadata"]["source_name"] == "take_1.mov"
    assert out[0]["clip_metadata"]["duration_s"] == 5.0


def test_stitch_sorts_by_timeline_start_time():
    """Even if specs arrive out of timeline order, output is time-sorted."""
    specs = [_spec(0, offset=5.0, duration=3.0), _spec(1, offset=0.0, duration=5.0)]
    per_clip = [
        [{"word": "late", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"}],
        [{"word": "early", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"}],
    ]
    out = stitch_transcripts(specs, per_clip)
    assert [w["word"] for w in out] == ["early", "late"]


def test_stitch_drops_words_past_clip_end():
    specs = [_spec(0, offset=0.0, duration=5.0)]
    per_clip = [
        [
            {"word": "ok", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"},
            # End past duration + grace — must drop.
            {"word": "dropped", "start_time": 0.4, "end_time": 5.3, "speaker_id": "S1"},
        ]
    ]
    out = stitch_transcripts(specs, per_clip)
    assert len(out) == 1
    assert out[0]["word"] == "ok"


def test_stitch_rejects_mismatched_list_lengths():
    try:
        stitch_transcripts([_spec(0)], [[], []])
    except ValueError as exc:
        assert "must have the same length" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


# ------------------------- orchestrator ------------------------------------


def _dummy_transcribe(spec: ClipAudioSpec) -> list[dict]:
    return [
        {"word": f"w{spec.item_index}", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    ]


def test_transcribe_per_clip_uses_cache(tmp_path):
    spec = _spec(0, offset=0.0, duration=2.0)
    # Pre-seed cache — transcribe_fn should not be invoked.
    save_cached_words(
        spec,
        [
            {"word": "cached", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"},
        ],
        root=tmp_path,
    )

    calls: list[int] = []

    def forbid(_spec):
        calls.append(1)
        return []

    stitched, stats = asyncio.run(
        transcribe_per_clip(
            [spec],
            cache_root=tmp_path,
            transcribe_fn=forbid,
        )
    )
    assert stats == {"cache_hits": 1, "cache_misses": 0, "dropped": 0}
    assert stitched[0]["word"] == "cached"
    assert stitched[0]["clip_metadata"]["source_name"] == "take_0.mov"
    assert calls == []  # fn never called


def test_transcribe_per_clip_writes_new_entries_to_cache(tmp_path):
    spec = _spec(0, offset=0.0, duration=2.0)
    stitched, stats = asyncio.run(
        transcribe_per_clip(
            [spec],
            cache_root=tmp_path,
            transcribe_fn=_dummy_transcribe,
        )
    )
    assert stats == {"cache_hits": 0, "cache_misses": 1, "dropped": 0}
    # Next call should hit cache.
    _, stats2 = asyncio.run(
        transcribe_per_clip(
            [spec],
            cache_root=tmp_path,
            transcribe_fn=lambda _s: (_ for _ in ()).throw(AssertionError("should not run")),
        )
    )
    assert stats2["cache_hits"] == 1
    # Cache payload is valid JSON with expected shape.
    payload = json.loads((tmp_path / f"{spec.cache_key}.json").read_text())
    assert payload["source_path"] == spec.source_path
    assert payload["words"][0]["word"] == "w0"
    _ = stitched  # silence


def test_transcribe_per_clip_can_run_without_cache(tmp_path):
    spec = _spec(0, offset=0.0, duration=2.0)
    stitched, stats = asyncio.run(
        transcribe_per_clip(
            [spec],
            use_cache=False,
            cache_root=tmp_path,
            transcribe_fn=_dummy_transcribe,
        )
    )
    assert stats == {"cache_hits": 0, "cache_misses": 1, "dropped": 0}
    assert stitched[0]["word"] == "w0"
    # No cache file written.
    assert list(tmp_path.iterdir()) == []


def test_transcribe_per_clip_mixed_hit_and_miss(tmp_path):
    s0 = _spec(0, offset=0.0, duration=2.0)
    s1 = _spec(1, "/b.mov", in_f=10, out_f=100, offset=2.0, duration=3.0)
    # Only s0 cached.
    save_cached_words(
        s0,
        [
            {"word": "cached", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
        ],
        root=tmp_path,
    )

    stitched, stats = asyncio.run(
        transcribe_per_clip(
            [s0, s1],
            cache_root=tmp_path,
            transcribe_fn=_dummy_transcribe,
        )
    )
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 1
    # Stitched output is timeline-ordered: cached (offset 0) before new (offset 2).
    assert stitched[0]["word"] == "cached"
    assert stitched[1]["word"] == "w1"
    assert stitched[1]["start_time"] == 2.0  # offset applied


# ------------------------- prompt helper -----------------------------------


def test_clip_metadata_table_renders_deduped_entries():
    specs = [
        _spec(0, offset=0.0, duration=3.0),
        _spec(1, offset=3.0, duration=2.5, name="second.mov"),
    ]
    per_clip = [
        [
            {"word": "a", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
            {"word": "b", "start_time": 0.3, "end_time": 0.6, "speaker_id": "S1"},
        ],
        [{"word": "c", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S2"}],
    ]
    transcript = stitch_transcripts(specs, per_clip)
    table = clip_metadata_table(transcript)
    # Two clip rows + header + separator.
    assert table.count("\n") >= 3
    assert "take_0.mov" in table
    assert "second.mov" in table
    assert "3.0s" in table  # duration


def test_clip_metadata_table_empty_when_no_clip_metadata():
    transcript = [
        {"word": "a", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"},
    ]
    assert clip_metadata_table(transcript) == ""
