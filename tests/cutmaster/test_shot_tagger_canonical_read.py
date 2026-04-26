"""Tests for the canonical-read sample grid + manifest-driven cache lookup.

These cover the bug surfaced by live-Resolve diagnosis on
``Timeline 1_AI_Cut_17``: the writer (analyze on the source timeline)
caches at ``{0.3, 5.0, 10.0, …, src_dur − 0.3}``, but the reader (paint
/ stamp on a cut timeline) was naïvely calling ``plan_samples`` which
generates ``{in_s + 0.3, in_s + 5.0, …}`` — so non-zero-``in_s`` cuts
missed every cached tag.

:func:`plan_canonical_read_samples` reconstructs the writer's grid
intersected with ``[in_s, out_s]`` so cuts that use mid-clip ranges
hit the cache. :func:`iter_cached_tags_for_cut_item` is the high-level
helper the painter and stamper now call.
"""

from __future__ import annotations

import json

import pytest

from cutmaster_ai.cutmaster.analysis import shot_tagger
from cutmaster_ai.cutmaster.analysis.shot_tagger import (
    FRAME_EDGE_OFFSET_S,
    FRAME_STRIDE_S,
    ShotTag,
    VideoItemSpec,
    iter_cached_tags_for_cut_item,
    plan_canonical_read_samples,
)

# ---------------------------------------------------------------------------
# plan_canonical_read_samples — pure / deterministic
# ---------------------------------------------------------------------------


def test_canonical_grid_full_source_in_zero():
    """``in_s == 0``, ``out_s == source_dur`` ⇒ full writer grid."""
    samples = plan_canonical_read_samples(
        "/m/a.mov",
        in_s=0.0,
        out_s=20.0,
        timeline_offset_s=0.0,
        source_dur_s=20.0,
    )
    src_ts = [round(s.source_ts_s, 3) for s in samples]
    # Edge + strides + end edge
    assert src_ts == [0.3, 5.0, 10.0, 15.0, 19.7]


def test_canonical_grid_mid_clip_window():
    """Cut starts at 12s into a 60s source — should hit 15.0, 20.0, 25.0, …
    (writer grid points that fall inside the window), not 12.3 / 17.3 / etc."""
    samples = plan_canonical_read_samples(
        "/m/a.mov",
        in_s=12.0,
        out_s=32.5,
        timeline_offset_s=100.0,
        source_dur_s=60.0,
    )
    src_ts = sorted(round(s.source_ts_s, 3) for s in samples)
    # Edge (0.3) is < in_s, dropped. Strides 15, 20, 25, 30 are in window.
    # End edge 59.7 is > out_s, dropped.
    assert src_ts == [15.0, 20.0, 25.0, 30.0]
    # Timeline offsets are reprojected: tl = offset + (src_ts - in_s).
    tl_for_15 = next(s.timeline_ts_s for s in samples if abs(s.source_ts_s - 15.0) < 1e-3)
    assert abs(tl_for_15 - (100.0 + 3.0)) < 1e-3


def test_canonical_grid_skips_when_window_outside_grid():
    """Tiny mid-clip window that falls between grid points returns no samples."""
    samples = plan_canonical_read_samples(
        "/m/a.mov",
        in_s=2.0,
        out_s=4.0,
        timeline_offset_s=0.0,
        source_dur_s=60.0,
    )
    # Grid: 0.3, 5.0, 10.0, …, 59.7. None fall in [2.0, 4.0].
    assert samples == []


def test_canonical_grid_zero_duration_safe():
    assert (
        plan_canonical_read_samples(
            "/m/a.mov", in_s=0.0, out_s=10.0, timeline_offset_s=0.0, source_dur_s=0.0
        )
        == []
    )
    assert (
        plan_canonical_read_samples(
            "/m/a.mov", in_s=5.0, out_s=2.0, timeline_offset_s=0.0, source_dur_s=10.0
        )
        == []
    )


def test_canonical_grid_short_source():
    """Source shorter than 2 * edge offset has no usable samples."""
    samples = plan_canonical_read_samples(
        "/m/a.mov",
        in_s=0.0,
        out_s=0.4,
        timeline_offset_s=0.0,
        source_dur_s=0.4,
    )
    # 0.3 falls in window, end-edge 0.1 is too small (< edge offset).
    src_ts = [round(s.source_ts_s, 3) for s in samples]
    assert src_ts == [0.3]


def test_canonical_grid_constants_unchanged():
    """If anyone bumps FRAME_EDGE_OFFSET_S / FRAME_STRIDE_S, this test
    blows up early so the cache-key contract is reviewed deliberately."""
    assert FRAME_EDGE_OFFSET_S == 0.3
    assert FRAME_STRIDE_S == 5.0


# ---------------------------------------------------------------------------
# iter_cached_tags_for_cut_item — manifest-driven happy path + fallback
# ---------------------------------------------------------------------------


def _seed_manifest(tmp_root, source_path: str, duration_s: float) -> None:
    cache_dir = tmp_root / shot_tagger._cache_dir(source_path).name
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(
        json.dumps({"source_path": source_path, "duration_s": duration_s})
    )


def _seed_tag(tmp_root, source_path: str, ts_s: float, tag: ShotTag) -> None:
    cache_dir = tmp_root / shot_tagger._cache_dir(source_path).name
    cache_dir.mkdir(parents=True, exist_ok=True)
    ts_ms = int(round(ts_s * 1000))
    (cache_dir / f"{ts_ms:010d}.json").write_text(json.dumps(tag.model_dump()))


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    """Redirect the cache root to a tmp dir for hermetic disk I/O."""
    monkeypatch.setattr(shot_tagger, "CACHE_ROOT", tmp_path)
    return tmp_path


def test_iter_cached_tags_hits_writer_grid_for_mid_clip_cut(cache_root):
    """Regression for Timeline 1_AI_Cut_17: cut starts mid-clip; reader
    should hit the writer's canonical grid, not the in_s-offset grid."""
    src = "/m/a.mov"
    _seed_manifest(cache_root, src, 60.0)
    # Writer wrote tags at the canonical grid points.
    _seed_tag(cache_root, src, 5.0, ShotTag(shot_type="wide"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 10.0, ShotTag(shot_type="wide"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 15.0, ShotTag(shot_type="closeup"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 20.0, ShotTag(shot_type="closeup"))  # type: ignore[arg-type]

    spec = VideoItemSpec(
        item_index=0,
        source_name="A001",
        timeline_offset_s=100.0,
        duration_s=10.0,
        segments=[(src, 12.0, 22.0)],  # cut item is mid-source
    )

    pairs = iter_cached_tags_for_cut_item(spec)

    # Window [12, 22] intersects canonical grid {5,10,15,20} at {15, 20}.
    assert len(pairs) == 2
    assert {p[0].source_ts_s for p in pairs} == {15.0, 20.0}
    assert all(t.shot_type == "closeup" for _s, t in pairs)


def test_iter_cached_tags_full_source_cut(cache_root):
    """Cut item spans full source ⇒ all canonical samples land."""
    src = "/m/b.mov"
    _seed_manifest(cache_root, src, 10.0)
    _seed_tag(cache_root, src, 0.3, ShotTag(shot_type="medium"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 5.0, ShotTag(shot_type="medium"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 9.7, ShotTag(shot_type="medium"))  # type: ignore[arg-type]

    spec = VideoItemSpec(
        item_index=0,
        source_name="B001",
        timeline_offset_s=0.0,
        duration_s=10.0,
        segments=[(src, 0.0, 10.0)],
    )

    pairs = iter_cached_tags_for_cut_item(spec)
    assert len(pairs) == 3
    assert all(t.shot_type == "medium" for _s, t in pairs)


def test_iter_cached_tags_falls_back_when_no_manifest(cache_root):
    """No manifest ⇒ legacy plan_samples path. Preserves behaviour for
    pre-fix caches (in_s == 0 still works; in_s != 0 still misses, as
    before — the user must re-run analyze to write a manifest)."""
    src = "/m/c.mov"
    # No manifest seeded. Tags at the legacy in_s-baked grid.
    _seed_tag(cache_root, src, 0.3, ShotTag(shot_type="broll"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 5.0, ShotTag(shot_type="broll"))  # type: ignore[arg-type]
    _seed_tag(cache_root, src, 9.7, ShotTag(shot_type="broll"))  # type: ignore[arg-type]

    spec_zero = VideoItemSpec(
        item_index=0,
        source_name="C001",
        timeline_offset_s=0.0,
        duration_s=10.0,
        segments=[(src, 0.0, 10.0)],
    )
    pairs = iter_cached_tags_for_cut_item(spec_zero)
    # Legacy grid for in_s=0 over a 10s window matches what we wrote.
    assert len(pairs) == 3


def test_iter_cached_tags_no_tags_returns_empty(cache_root):
    src = "/m/d.mov"
    _seed_manifest(cache_root, src, 30.0)
    spec = VideoItemSpec(
        item_index=0,
        source_name="D001",
        timeline_offset_s=0.0,
        duration_s=10.0,
        segments=[(src, 5.0, 15.0)],
    )
    assert iter_cached_tags_for_cut_item(spec) == []


def test_resolve_source_duration_via_manifest(cache_root):
    src = "/m/e.mov"
    _seed_manifest(cache_root, src, 42.5)
    assert shot_tagger._resolve_source_duration(src) == 42.5


def test_resolve_source_duration_missing_returns_none(cache_root):
    assert shot_tagger._resolve_source_duration("/m/never.mov") is None


def test_resolve_source_duration_malformed_returns_none(cache_root, tmp_path):
    src = "/m/f.mov"
    cache_dir = tmp_path / shot_tagger._cache_dir(src).name
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text("{not json")
    assert shot_tagger._resolve_source_duration(src) is None
