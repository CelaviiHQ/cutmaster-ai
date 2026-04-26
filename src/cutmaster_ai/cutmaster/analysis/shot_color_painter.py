"""Paint Resolve clip colors on a cut timeline based on cached shot tags.

Read-only against the shot-tag cache (no new vision calls). For each
video item on the *cut* timeline, picks the modal ``shot_type`` from
cached tags covering that item's source span, then sets the timeline
item's clip color via ``TimelineItem.SetClipColor`` — provided the
editor hasn't already coloured it manually.

Design notes
------------
- Tags are keyed on ``source_path + source_ts_s`` (see
  :mod:`shot_tagger`), so cached tags written during analyze on the
  *source* timeline are reused verbatim when the same media reappears
  on the cut timeline. No new Gemini calls are made.
- ``build_video_item_specs`` works for any timeline that contains
  media-pool-backed items, so we reuse it for the cut timeline.
- Skips items whose existing ``GetClipColor()`` is non-empty so manual
  editor colors are never overwritten (overrideable via ``overwrite``).
- Idempotent: re-running paints the same colors. No state in run dict.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from . import shot_tagger

log = logging.getLogger("cutmaster-ai.cutmaster.shot_color_painter")


# Mapping per design discussion. 7 ShotTag.shot_type literals collapse
# to 6 named Resolve clip colors; "unknown" is intentionally omitted so
# unknown items stay uncoloured (preserves manual paint and avoids
# burning a default color on noise).
COLOR_BY_SHOT_TYPE: dict[str, str] = {
    "closeup": "Orange",
    "medium": "Lime",
    "wide": "Teal",
    "over_shoulder": "Violet",
    "broll": "Blue",
    "title_card": "Pink",
}


@dataclass
class PaintResult:
    """One row per timeline item examined."""

    item_index: int  # 0-based on the cut timeline's video track
    action: str  # "painted" | "skipped_already_colored" | "skipped_no_tags" | "skipped_unknown"
    shot_type: str | None = None
    color: str | None = None  # color applied (painted) or already-set color (skipped)


def paint_shot_colors_on_timeline(
    timeline_name: str,
    *,
    overwrite: bool = False,
    video_track: int = 1,
) -> dict[str, Any]:
    """Walk ``timeline_name``'s video items and paint clip colors by modal shot type.

    Args:
        timeline_name: The cut timeline (typically ``buildResult.new_timeline_name``).
        overwrite: When True, replace pre-existing clip colors. Default
            False so the editor's manual paint survives.
        video_track: 1-based track index. Default V1.

    Returns:
        ``{"timeline_name", "total_items", "painted", "skipped_already_colored",
        "skipped_no_tags", "skipped_unknown", "rows"}``. ``rows`` is the
        per-item :class:`PaintResult` list serialised as dicts so the
        HTTP layer can pass it to the panel directly.

    Raises:
        ValueError: timeline not found.
    """
    from ...resolve import _boilerplate
    from ..core.pipeline import _find_timeline_by_name

    _, project, _ = _boilerplate()
    tl = _find_timeline_by_name(project, timeline_name)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_name}' not found")

    specs = shot_tagger.build_video_item_specs(tl, project, video_track=video_track)
    items = tl.GetItemListInTrack("video", video_track) or []

    rows: list[PaintResult] = []
    painted = 0
    skipped_already = 0
    skipped_no_tags = 0
    skipped_unknown = 0

    # ``specs`` skips items without media-pool backing (generators, etc.).
    # Index back into ``items`` by spec.item_index so we paint the right
    # row even when the spec list is shorter than the timeline item list.
    spec_by_idx = {s.item_index: s for s in specs}

    for idx, item in enumerate(items):
        spec = spec_by_idx.get(idx)
        if spec is None:
            # Generator / unsupported item — silently skip; not surfaced.
            continue

        # Tally cached tags. Misses (no cached frame) are ignored — we
        # only reason about what the analyze pass actually saw.
        tags: list[shot_tagger.ShotTag] = []
        for sample in shot_tagger.plan_samples(spec):
            cached = shot_tagger._load_cached_tag(sample.source_path, sample.source_ts_s)
            if cached is not None:
                tags.append(cached)

        if not tags:
            rows.append(PaintResult(item_index=idx, action="skipped_no_tags"))
            skipped_no_tags += 1
            continue

        # Modal shot_type. ``Counter.most_common(1)`` returns the
        # alphabetically-first key on ties, which is deterministic
        # across runs — important for idempotence.
        types = [t.shot_type for t in tags]
        modal, _count = Counter(types).most_common(1)[0]

        if modal == "unknown" or modal not in COLOR_BY_SHOT_TYPE:
            rows.append(PaintResult(item_index=idx, action="skipped_unknown", shot_type=modal))
            skipped_unknown += 1
            continue

        # Manual-color guard. Empty string / None ⇒ unpainted.
        existing = ""
        try:
            existing = str(item.GetClipColor() or "")
        except Exception as exc:
            log.debug("GetClipColor failed on item %d: %s", idx, exc)

        if existing and not overwrite:
            rows.append(
                PaintResult(
                    item_index=idx,
                    action="skipped_already_colored",
                    shot_type=modal,
                    color=existing,
                )
            )
            skipped_already += 1
            continue

        target = COLOR_BY_SHOT_TYPE[modal]
        ok = False
        try:
            ok = bool(item.SetClipColor(target))
        except Exception as exc:
            log.warning("SetClipColor(%s) failed on item %d: %s", target, idx, exc)

        if ok:
            rows.append(
                PaintResult(item_index=idx, action="painted", shot_type=modal, color=target)
            )
            painted += 1
        else:
            # Treat a False return like an unknown-skip — the editor
            # gets a row in the result but no color was applied.
            rows.append(PaintResult(item_index=idx, action="skipped_unknown", shot_type=modal))
            skipped_unknown += 1

    return {
        "timeline_name": timeline_name,
        "total_items": len(items),
        "painted": painted,
        "skipped_already_colored": skipped_already,
        "skipped_no_tags": skipped_no_tags,
        "skipped_unknown": skipped_unknown,
        "rows": [r.__dict__ for r in rows],
        "color_legend": dict(COLOR_BY_SHOT_TYPE),
    }
