"""Resolve-free unit tests for shot_color_painter.

The painter has three deterministic decision paths we want to lock in
without booting Resolve:

- Modal-tag selection across cached samples (with a tie tiebreak).
- Manual-color guard (skip a pre-coloured item; honour ``overwrite``).
- ``unknown`` shot type → no paint, even when other tags exist.

Resolve's TimelineItem and shot_tagger.build_video_item_specs are
stubbed via fakes so the tests stay hermetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cutmaster_ai.cutmaster.analysis import shot_tagger
from cutmaster_ai.cutmaster.analysis.shot_color_painter import (
    COLOR_BY_SHOT_TYPE,
    paint_shot_colors_on_timeline,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeItem:
    color: str = ""
    set_color_returns: bool = True

    def GetClipColor(self) -> str:
        return self.color

    def SetClipColor(self, color: str) -> bool:
        self.color = color
        return self.set_color_returns


class FakeTimeline:
    """Walks a fixed item list. ``GetItemListInTrack`` mirrors Resolve's
    ``("video", n)`` shape but only V1 is exercised here."""

    def __init__(self, items: list[FakeItem]):
        self._items = items

    def GetItemListInTrack(self, kind: str, track: int):
        assert kind == "video" and track == 1
        return list(self._items)


@dataclass
class FakeSample:
    """Mirrors :class:`shot_tagger.FrameSample` with the fields the painter touches."""

    source_path: str
    source_ts_s: float


def _spec(idx: int, *, source: str = "/m/a.mov") -> shot_tagger.VideoItemSpec:
    """Minimal spec — only ``item_index`` matters; segments aren't read by the
    painter (it goes through ``plan_samples`` which we stub)."""
    return shot_tagger.VideoItemSpec(
        item_index=idx,
        source_name=f"item_{idx}",
        timeline_offset_s=0.0,
        duration_s=10.0,
        segments=[(source, 0.0, 10.0)],
    )


def _patch_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeline: FakeTimeline,
    specs: list[shot_tagger.VideoItemSpec],
    samples_by_item: dict[int, list[FakeSample]],
    tags_by_sample: dict[tuple[str, float], shot_tagger.ShotTag | None],
) -> None:
    """Wire up the four seams the painter calls into."""
    # Painter does lazy imports inside the function, so patch at the
    # source modules — patching ``shot_color_painter._boilerplate`` would
    # never be looked up.
    from cutmaster_ai import resolve as resolve_mod
    from cutmaster_ai.cutmaster.core import pipeline as pipeline_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", lambda: (None, object(), None))
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda project, name: timeline)
    # Specs come from shot_tagger.
    monkeypatch.setattr(
        shot_tagger,
        "build_video_item_specs",
        lambda tl, project=None, *, video_track=1: specs,
    )
    # plan_samples is keyed on the spec → look up by item_index.
    monkeypatch.setattr(
        shot_tagger,
        "plan_samples",
        lambda spec: samples_by_item.get(spec.item_index, []),
    )
    # Cache loader returns whatever we registered for that (path, ts) pair.
    monkeypatch.setattr(
        shot_tagger,
        "_load_cached_tag",
        lambda path, ts: tags_by_sample.get((path, ts)),
    )


def _tag(shot_type: str = "closeup") -> shot_tagger.ShotTag:
    return shot_tagger.ShotTag(shot_type=shot_type)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_paints_modal_shot_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three closeup tags + one wide → paints Orange (modal=closeup)."""
    item = FakeItem()
    tl = FakeTimeline([item])
    spec = _spec(0)
    samples = [FakeSample("/m/a.mov", t) for t in (0.3, 5.0, 9.7)]
    extra = FakeSample("/m/a.mov", 4.0)
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[spec],
        samples_by_item={0: [*samples, extra]},
        tags_by_sample={
            ("/m/a.mov", 0.3): _tag("closeup"),
            ("/m/a.mov", 5.0): _tag("closeup"),
            ("/m/a.mov", 9.7): _tag("closeup"),
            ("/m/a.mov", 4.0): _tag("wide"),
        },
    )

    out = paint_shot_colors_on_timeline("Cut1")

    assert out["painted"] == 1
    assert out["skipped_already_colored"] == 0
    assert out["rows"][0]["action"] == "painted"
    assert out["rows"][0]["color"] == COLOR_BY_SHOT_TYPE["closeup"]
    assert item.color == "Orange"


def test_skips_already_colored_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-painted item is preserved unless overwrite=True."""
    item = FakeItem(color="Green")  # editor's manual paint
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("closeup")},
    )

    out = paint_shot_colors_on_timeline("Cut1")

    assert out["painted"] == 0
    assert out["skipped_already_colored"] == 1
    assert out["rows"][0]["action"] == "skipped_already_colored"
    assert out["rows"][0]["color"] == "Green"
    assert item.color == "Green"  # untouched


def test_overwrite_replaces_manual_color(monkeypatch: pytest.MonkeyPatch) -> None:
    item = FakeItem(color="Green")
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("broll")},
    )

    out = paint_shot_colors_on_timeline("Cut1", overwrite=True)

    assert out["painted"] == 1
    assert item.color == "Blue"  # broll → Blue


def test_unknown_shot_type_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    item = FakeItem()
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={("/m/a.mov", 0.3): _tag("unknown")},
    )

    out = paint_shot_colors_on_timeline("Cut1")

    assert out["painted"] == 0
    assert out["skipped_unknown"] == 1
    assert item.color == ""  # untouched


def test_no_cached_tags_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    item = FakeItem()
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: [FakeSample("/m/a.mov", 0.3)]},
        tags_by_sample={},  # cache miss
    )

    out = paint_shot_colors_on_timeline("Cut1")

    assert out["painted"] == 0
    assert out["skipped_no_tags"] == 1
    assert item.color == ""


def test_legend_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity-check the legend the panel uses to render swatches."""
    item = FakeItem()
    tl = FakeTimeline([item])
    _patch_world(
        monkeypatch,
        timeline=tl,
        specs=[_spec(0)],
        samples_by_item={0: []},
        tags_by_sample={},
    )

    out = paint_shot_colors_on_timeline("Cut1")

    assert out["color_legend"] == COLOR_BY_SHOT_TYPE
    # Six known shot types → six entries (unknown is intentionally absent).
    assert "unknown" not in out["color_legend"]
    assert set(out["color_legend"].keys()) == {
        "closeup",
        "medium",
        "wide",
        "over_shoulder",
        "broll",
        "title_card",
    }


def test_timeline_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from cutmaster_ai import resolve as resolve_mod
    from cutmaster_ai.cutmaster.core import pipeline as pipeline_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", lambda: (None, object(), None))
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda *a, **k: None)

    with pytest.raises(ValueError, match="not found"):
        paint_shot_colors_on_timeline("Missing")
