"""Map a timeline time → (media pool item, source frame).

Gemini transcribes a flat audio file of the whole timeline; its timestamps
are relative to that flattened audio, not to any specific source clip. This
module walks the timeline's V1 track to answer: at timeline time T, which
source clip is playing and at what source frame?

Phase 0 (v0_source_mapping.py) validated that ``GetLeftOffset()`` on a
timeline item equals ``GetSourceStartFrame()`` on unspeed-ramped clips and
that the basic offset math is correct. Speed-ramped clips are flagged in the
return payload so the pipeline can decide whether to skip them.
"""

from __future__ import annotations

import json

from ...config import mcp
from ...errors import safe_resolve_call
from ...resolve import _boilerplate
from ..media.frame_math import _timeline_fps, _timeline_start_frame


class TimelineMappingError(ValueError):
    """Raised when a timeline time cannot be mapped to a source clip."""


def _item_clip_speed(item, mp_item) -> float:
    """Return the effective playback speed as a ratio (1.0 = normal)."""
    # Prefer the timeline item's property when available (handles retimes),
    # fall back to the media pool item's stored speed.
    try:
        s = item.GetProperty("Speed")
        if s is not None:
            return float(s)
    except Exception:
        pass
    try:
        raw = mp_item.GetClipProperty("Speed") if mp_item else None
        return float(raw) / 100.0 if raw else 1.0
    except Exception:
        return 1.0


def timeline_time_to_source(tl, seconds: float, track_index: int = 1) -> dict:
    """Resolve a timeline time to the source clip frame at that moment.

    Args:
        tl: Resolve timeline object.
        seconds: Timeline time in seconds (0 = timeline start).
        track_index: Video track to scan (default V1).

    Returns:
        ``{"source_item_id": str, "source_item_name": str, "source_frame": int,
           "speed": float, "speed_ramped": bool}``

    Raises:
        TimelineMappingError: the time falls in a gap, the item has no media
            pool item (compound/nested/generator), or the track is empty.
    """
    fps = _timeline_fps(tl)
    start = _timeline_start_frame(tl)
    target_frame = start + round(seconds * fps)

    items = tl.GetItemListInTrack("video", track_index) or []
    if not items:
        raise TimelineMappingError(f"No items on video track {track_index}.")

    for item in items:
        item_start = item.GetStart()
        item_end = item.GetEnd()  # exclusive
        if not (item_start <= target_frame < item_end):
            continue

        mp_item = item.GetMediaPoolItem()
        if not mp_item:
            raise TimelineMappingError(
                f"Timeline item at t={seconds:.3f}s has no media pool item "
                "(compound clip, nested timeline, or generator)."
            )

        offset_in_item = target_frame - item_start
        speed = _item_clip_speed(item, mp_item)
        try:
            left_offset = int(item.GetLeftOffset() or 0)
        except Exception:
            left_offset = 0
        source_frame = left_offset + int(round(offset_in_item * speed))

        return {
            "source_item_id": mp_item.GetUniqueId(),
            "source_item_name": mp_item.GetName(),
            "source_frame": source_frame,
            "speed": speed,
            "speed_ramped": speed != 1.0,
        }

    raise TimelineMappingError(
        f"Timeline time t={seconds:.3f}s (frame {target_frame}) falls in a "
        f"gap on video track {track_index}."
    )


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_resolve_timeline_time_to_source(
    seconds: float,
    track_index: int = 1,
) -> str:
    """Map a timeline time (seconds) to the source clip + source frame playing then.

    Args:
        seconds: Timeline time in seconds (0 = start of timeline).
        track_index: 1-based video track to scan (default V1).

    Returns a JSON payload with ``source_item_id``, ``source_item_name``,
    ``source_frame``, ``speed``, and ``speed_ramped``. Returns an error
    string if the time falls in a gap or the item has no source media.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    try:
        result = timeline_time_to_source(tl, float(seconds), int(track_index))
    except TimelineMappingError as exc:
        return f"Error: {exc}"
    return json.dumps(result)
