"""Stamp shot metadata on a cut timeline (markers + smart-bin metadata).

Resolve's TimelineItem has no per-item Description/Keywords field —
``hasattr(item, "SetMetadata")`` lies (the attribute exists but raises
on call) and ``item.GetProperty()`` is transform-only. The only two
viable persistence surfaces are:

1. **TimelineItem.AddMarker(frame, color, name, note, duration, customData)**
   — per-item, scoped to this cut, queryable via ``GetMarkers()``,
   removable via ``DeleteMarkerByCustomData()``. We use this for the
   structured per-cut record. The ``customData`` payload is a JSON
   blob namespaced under ``CM_NAMESPACE`` so re-stamping locates and
   removes prior CutMaster markers without touching editor markers.

2. **MediaPoolItem.SetMetadata("Keywords"|"Description", value)** — the
   only surface that powers Resolve's smart-bin search ("Keyword
   contains closeup"). The catch: it propagates to every timeline
   instance of that source clip. Editors who don't want their source
   metadata touched can pass ``touch_media_pool=False``.

Both reuse the cached tags written by analyze (no new vision calls).
Idempotent: re-running cleans up prior CutMaster markers first and
overwrites its own metadata keys; editor-set fields (Comments, manual
keywords) are not touched.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from . import shot_tagger

log = logging.getLogger("cutmaster-ai.cutmaster.shot_metadata_stamper")


# Marker payloads land under this namespace in ``customData`` so re-stamps
# can locate prior CutMaster markers without disturbing editor markers.
CM_NAMESPACE = "cutmaster.shot.v1"

# Marker color used for shot-tag stamps. Lavender is rarely used by
# editors for narrative work, so collisions with manual markers are
# unlikely — and the customData namespace is the actual idempotency key.
MARKER_COLOR = "Lavender"

# Description prefix lets editors recognise CutMaster-stamped clips at
# a glance and lets us idempotently rewrite our own line without
# touching anything an editor wrote before / after.
DESCRIPTION_PREFIX = "[CutMaster]"


@dataclass
class StampResult:
    """One row per timeline item examined."""

    item_index: int  # 0-based on the cut timeline's video track
    action: str  # "stamped" | "skipped_no_tags" | "skipped_unknown"
    shot_type: str | None = None
    framing: str | None = None
    gesture_intensity: str | None = None
    notable: str | None = None
    marker_added: bool = False
    media_pool_updated: bool = False
    media_pool_clip_name: str | None = None


def _summarise_tags(tags: list[shot_tagger.ShotTag]) -> dict[str, Any] | None:
    """Pick the modal tag set across cached samples.

    Returns the modal `shot_type`, `framing`, `gesture_intensity`, mean
    `visual_energy`, and the most common non-empty `notable` (or None).
    """
    if not tags:
        return None
    types = [t.shot_type for t in tags]
    framings = [t.framing for t in tags]
    gestures = [t.gesture_intensity for t in tags]
    energies = [t.visual_energy for t in tags]
    notables = [t.notable for t in tags if t.notable]

    modal_type, _ = Counter(types).most_common(1)[0]
    modal_framing, _ = Counter(framings).most_common(1)[0]
    modal_gesture, _ = Counter(gestures).most_common(1)[0]
    notable = Counter(notables).most_common(1)[0][0] if notables else None
    avg_energy = round(sum(energies) / len(energies)) if energies else 0

    return {
        "shot_type": modal_type,
        "framing": modal_framing,
        "gesture_intensity": modal_gesture,
        "visual_energy": avg_energy,
        "notable": notable,
    }


def _format_human(summary: dict[str, Any]) -> str:
    """Compact human line for marker name + Description."""
    parts = [summary["shot_type"]]
    if summary["framing"] != "unknown":
        parts.append(summary["framing"])
    if summary["gesture_intensity"] != "unknown":
        parts.append(summary["gesture_intensity"])
    parts.append(f"energy {summary['visual_energy']}")
    out = " · ".join(parts)
    if summary["notable"]:
        out = f"{out} · {summary['notable']}"
    return out


def _format_keywords(summary: dict[str, Any]) -> str:
    """Comma-separated keywords for smart-bin search."""
    bits = [summary["shot_type"]]
    if summary["framing"] != "unknown":
        bits.append(summary["framing"])
    if summary["gesture_intensity"] != "unknown":
        bits.append(summary["gesture_intensity"])
    return ", ".join(bits)


def _list_cm_marker_frames(item: Any) -> list[int]:
    """Return the frame indices of every CutMaster marker on ``item``.

    Reads ``GetMarkers()`` (``{frame: {color, name, note, customData,
    duration}}``) and filters by ``customData`` namespace prefix.
    """
    try:
        markers = item.GetMarkers() or {}
    except Exception as exc:
        log.debug("GetMarkers failed: %s", exc)
        return []
    out: list[int] = []
    for frame, payload in markers.items():
        cd = (payload or {}).get("customData") or ""
        if isinstance(cd, str) and cd.startswith(CM_NAMESPACE):
            out.append(int(frame))
    return out


def _clean_prior_markers(item: Any) -> int:
    """Delete every prior CutMaster marker on this item.

    Resolve's ``DeleteMarkerAtFrame`` is observed to return falsy values
    even on successful deletion in some Studio builds, so we ignore the
    return and verify removal via a fresh ``GetMarkers()`` read.
    """
    before = _list_cm_marker_frames(item)
    if not before:
        return 0
    for frame in before:
        try:
            item.DeleteMarkerAtFrame(frame)
        except Exception as exc:
            log.debug("DeleteMarkerAtFrame(%s) failed: %s", frame, exc)
    after = set(_list_cm_marker_frames(item))
    return sum(1 for f in before if f not in after)


def _stamp_media_pool(
    mp_item: Any,
    summary: dict[str, Any],
) -> tuple[bool, str | None]:
    """Write Keywords + Description to the source MediaPoolItem.

    Returns ``(updated, clip_name)``. ``updated`` is True iff at least
    one of the two metadata keys was successfully written.
    """
    if mp_item is None:
        return False, None

    clip_name: str | None = None
    try:
        clip_name = str(mp_item.GetName() or "") or None
    except Exception:
        pass

    keywords = _format_keywords(summary)
    description = f"{DESCRIPTION_PREFIX} {_format_human(summary)}"

    # SetMetadata returns True on success. Resolve quietly accepts both
    # the dict form and the (key, value) form — we use the explicit
    # form for clarity.
    ok_kw = False
    ok_desc = False
    try:
        ok_kw = bool(mp_item.SetMetadata("Keywords", keywords))
    except Exception as exc:
        log.warning("SetMetadata(Keywords) failed on '%s': %s", clip_name, exc)
    try:
        ok_desc = bool(mp_item.SetMetadata("Description", description))
    except Exception as exc:
        log.warning("SetMetadata(Description) failed on '%s': %s", clip_name, exc)

    return (ok_kw or ok_desc), clip_name


def stamp_shot_metadata_on_timeline(
    timeline_name: str,
    *,
    add_markers: bool = True,
    touch_media_pool: bool = True,
    video_track: int = 1,
) -> dict[str, Any]:
    """Stamp shot metadata on each video item of ``timeline_name``.

    Args:
        timeline_name: The cut timeline (typically
            ``buildResult.new_timeline_name``).
        add_markers: When True (default), add a CutMaster marker at
            frame 0 of each item carrying the structured shot record
            in ``customData``. Idempotent — prior CutMaster markers are
            removed first.
        touch_media_pool: When True (default), write Keywords +
            Description on each item's source MediaPoolItem so smart
            bins can search by shot type. NB: this propagates to every
            timeline instance of that source. Editors who want
            per-cut-only stamping should pass ``False``.
        video_track: 1-based track index. Default V1.

    Returns:
        Aggregate counts + per-row :class:`StampResult` list serialised
        as dicts so the HTTP layer can pass it to the panel directly.

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
    spec_by_idx = {s.item_index: s for s in specs}

    rows: list[StampResult] = []
    stamped = 0
    skipped_no_tags = 0
    skipped_unknown = 0
    markers_removed_total = 0
    media_pool_writes = 0

    for idx, item in enumerate(items):
        spec = spec_by_idx.get(idx)
        if spec is None:
            continue

        # See shot_tagger.plan_canonical_read_samples — the reader must
        # walk the writer's grid (origin 0), not the cut item's offset
        # grid, otherwise non-zero-``in_s`` cuts miss every cached tag.
        tags: list[shot_tagger.ShotTag] = [
            tag for _sample, tag in shot_tagger.iter_cached_tags_for_cut_item(spec)
        ]

        if not tags:
            rows.append(StampResult(item_index=idx, action="skipped_no_tags"))
            skipped_no_tags += 1
            continue

        summary = _summarise_tags(tags)
        if summary is None or summary["shot_type"] == "unknown":
            rows.append(
                StampResult(
                    item_index=idx,
                    action="skipped_unknown",
                    shot_type=summary["shot_type"] if summary else None,
                )
            )
            skipped_unknown += 1
            continue

        # Always purge prior CutMaster markers on this item, even when
        # add_markers=False — leaving stale stamps behind would lie
        # about the current state of the cut.
        markers_removed_total += _clean_prior_markers(item)

        marker_added = False
        if add_markers:
            # customData format: ``<namespace>:<json>`` so a simple
            # ``startswith(CM_NAMESPACE)`` reliably identifies our
            # markers regardless of the JSON payload shape. Editors'
            # customData (raw JSON, plain strings) won't false-match.
            payload = f"{CM_NAMESPACE}:" + json.dumps(summary, default=str)
            try:
                item.AddMarker(
                    0,  # relative frame within the item
                    MARKER_COLOR,
                    summary["shot_type"],
                    _format_human(summary),
                    1,
                    payload,
                )
            except Exception as exc:
                log.warning("AddMarker failed on item %d: %s", idx, exc)
            # AddMarker returns falsy in some Studio builds even on
            # success — verify via GetMarkers() rather than trust the
            # return value. A CM-namespaced marker at frame 0 means
            # the call landed.
            try:
                markers_now = item.GetMarkers() or {}
                m = markers_now.get(0) or {}
                cd = m.get("customData") or ""
                marker_added = isinstance(cd, str) and cd.startswith(CM_NAMESPACE)
            except Exception as exc:
                log.debug("post-AddMarker GetMarkers failed: %s", exc)

        media_pool_updated = False
        media_pool_clip_name: str | None = None
        if touch_media_pool:
            try:
                mp_item = item.GetMediaPoolItem()
            except Exception:
                mp_item = None
            media_pool_updated, media_pool_clip_name = _stamp_media_pool(mp_item, summary)
            if media_pool_updated:
                media_pool_writes += 1

        rows.append(
            StampResult(
                item_index=idx,
                action="stamped",
                shot_type=summary["shot_type"],
                framing=summary["framing"],
                gesture_intensity=summary["gesture_intensity"],
                notable=summary["notable"],
                marker_added=marker_added,
                media_pool_updated=media_pool_updated,
                media_pool_clip_name=media_pool_clip_name,
            )
        )
        stamped += 1

    return {
        "timeline_name": timeline_name,
        "total_items": len(items),
        "stamped": stamped,
        "skipped_no_tags": skipped_no_tags,
        "skipped_unknown": skipped_unknown,
        "markers_removed": markers_removed_total,
        "media_pool_writes": media_pool_writes,
        "rows": [r.__dict__ for r in rows],
        "namespace": CM_NAMESPACE,
        "marker_color": MARKER_COLOR,
        "options": {
            "add_markers": add_markers,
            "touch_media_pool": touch_media_pool,
        },
    }


def clear_shot_metadata_on_timeline(
    timeline_name: str,
    *,
    video_track: int = 1,
) -> dict[str, Any]:
    """Remove every CutMaster marker on ``timeline_name``.

    Editor's manual markers (any color/customData not under
    :data:`CM_NAMESPACE`) are left untouched. MediaPoolItem metadata
    is *not* cleared — clearing source metadata would affect every
    timeline instance and isn't reversible without the editor's
    knowledge of what was there before.
    """
    from ...resolve import _boilerplate
    from ..core.pipeline import _find_timeline_by_name

    _, project, _ = _boilerplate()
    tl = _find_timeline_by_name(project, timeline_name)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_name}' not found")

    items = tl.GetItemListInTrack("video", video_track) or []
    removed = 0
    for item in items:
        removed += _clean_prior_markers(item)

    return {
        "timeline_name": timeline_name,
        "markers_removed": removed,
        "namespace": CM_NAMESPACE,
    }
