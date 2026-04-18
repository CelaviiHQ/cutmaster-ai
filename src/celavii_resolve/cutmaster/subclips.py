"""Append subclips with explicit source-frame ranges.

Wraps ``mediaPool.AppendToTimeline(clip_infos)`` in a typed helper that
accepts ``(media_pool_item_id, start_frame, end_frame, track_index,
media_type)`` segments. Phase 0 (v0_append_ranges.py) validated the underlying
API is frame-accurate.
"""

from __future__ import annotations

import json
from typing import TypedDict

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _find_clip

MEDIA_TYPE = {"video": 1, "audio": 2}


class SubclipSegment(TypedDict, total=False):
    source_item_id: str  # required — uniquely identifies the media pool item
    start_frame: int  # required — inclusive, source frame
    end_frame: int  # required — exclusive, source frame
    track_index: int  # default 1
    media_type: str  # 'video' | 'audio' | 'both' (default 'both')


def append_subclips_with_ranges(
    project,
    media_pool,
    segments: list[SubclipSegment],
) -> dict:
    """Append a batch of ranged subclips to the current timeline.

    Each segment describes a range of a source clip to be appended. Linked
    audio follows the video by default (``media_type='both'``); pass
    ``'video'`` or ``'audio'`` to restrict.

    Returns:
        ``{"appended": <int>, "segments": [<per-segment debug>],
           "errors": [<any segment-level errors>]}``
    """
    tl = project.GetCurrentTimeline()
    if not tl:
        raise ValueError("No current timeline.")

    root = media_pool.GetRootFolder()
    clip_infos: list[dict] = []
    per_segment: list[dict] = []
    errors: list[str] = []

    for idx, seg in enumerate(segments):
        sid = seg.get("source_item_id")
        start = seg.get("start_frame")
        end = seg.get("end_frame")
        track = int(seg.get("track_index", 1))
        media_type = str(seg.get("media_type", "both")).lower()

        if sid is None or start is None or end is None:
            errors.append(f"segment[{idx}]: missing source_item_id/start/end")
            continue
        if end <= start:
            errors.append(f"segment[{idx}]: end_frame {end} <= start_frame {start}")
            continue

        mp_item = _find_clip(root, sid)
        if mp_item is None:
            errors.append(f"segment[{idx}]: source_item_id {sid} not found")
            continue

        info: dict = {
            "mediaPoolItem": mp_item,
            "startFrame": int(start),
            "endFrame": int(end),
            "trackIndex": track,
        }
        if media_type in ("video", "audio"):
            info["mediaType"] = MEDIA_TYPE[media_type]
        # 'both' omits mediaType entirely → Resolve appends V+linked A

        clip_infos.append(info)
        per_segment.append(
            {
                "index": idx,
                "source_item_name": mp_item.GetName(),
                "start_frame": int(start),
                "end_frame": int(end),
                "frames": int(end) - int(start),
                "track_index": track,
                "media_type": media_type,
            }
        )

    if not clip_infos:
        return {"appended": 0, "segments": per_segment, "errors": errors or ["no valid segments"]}

    items = media_pool.AppendToTimeline(clip_infos) or []

    return {
        "appended": len(items),
        "segments": per_segment,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_append_subclips_with_ranges(segments: list[dict]) -> str:
    """Append a batch of ranged subclips to the current timeline.

    Each segment must contain ``source_item_id``, ``start_frame``, and
    ``end_frame``. Optional ``track_index`` (default 1) and ``media_type``
    (``'video'``, ``'audio'``, or ``'both'``; default ``'both'``).

    Linked audio follows the video by default — pass ``media_type='video'``
    to restrict to V-only, or ``'audio'`` for A-only.

    Returns a JSON payload with ``appended`` count, per-segment debug, and
    any segment-level errors.
    """
    _, project, mp = _boilerplate()
    result = append_subclips_with_ranges(project, mp, segments)  # type: ignore[arg-type]
    return json.dumps(result)
