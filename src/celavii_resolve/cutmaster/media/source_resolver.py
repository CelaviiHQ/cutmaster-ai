"""Resolve a timeline item to its underlying source file(s) + time ranges.

Timeline items can be backed by:

1. A file-backed media pool item (``Type = "Video"`` / ``"Video + Audio"``).
   Trivial — read ``File Path`` and compute the in/out seconds.
2. A compound clip (``Type = "Timeline"``), where the MP item is itself a
   Resolve timeline. The outer item's ``SourceStartFrame / Duration``
   address a sub-range of that inner timeline, and we have to walk it to
   find the real source file. Inner timelines can themselves contain
   compounds, so the walk is recursive.

All internal math is carried out in **seconds** to avoid confusion between
outer-timeline fps, inner-timeline fps, and source-media fps. Each level's
fps is read from Resolve at the appropriate moment and used exactly once.

Used by:
- ``ffmpeg_audio.extract_timeline_audio`` (whole-timeline concat)
- ``stt.per_clip.build_clip_audio_specs`` (per-clip extraction)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .frame_math import _source_fps, _timeline_fps, _timeline_start_frame

log = logging.getLogger("celavii-resolve.cutmaster.source_resolver")

_MAX_DEPTH = 5


@dataclass(frozen=True)
class SourceSegment:
    """One contiguous range of a real source file."""

    path: Path
    in_s: float  # seconds into the source file
    out_s: float  # exclusive
    source_fps: float


def _find_timeline_by_name(project, name: str):
    """Return the first project timeline whose name matches ``name``, else None."""
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == name:
            return t
    return None


def resolve_item_to_segments(
    project,
    item,
    *,
    outer_fps: float,
) -> list[SourceSegment]:
    """Resolve ``item`` to a list of ``SourceSegment`` covering its duration.

    ``outer_fps`` is the fps of the timeline ``item`` sits on. Needed to
    convert ``item.GetDuration()`` (outer-timeline frames) to seconds.
    """
    mp_item = item.GetMediaPoolItem()
    if mp_item is None:
        return []

    duration_outer_frames = int(item.GetDuration())
    duration_s = duration_outer_frames / outer_fps

    # ``src_start_frame`` units depend on what ``mp_item`` is:
    #   - file-backed media:   source-media frames at source-media fps
    #   - compound/timeline:   inner-timeline frames at inner-timeline fps
    # ``_source_fps(mp_item)`` reports the correct fps for either case
    # (Resolve serves source fps for files, inner-timeline fps for
    # compounds), so dividing gives the correct seconds-offset regardless.
    try:
        src_start_frame = int(item.GetSourceStartFrame() or 0)
    except Exception:
        src_start_frame = 0
    src_fps = _source_fps(mp_item, fallback=outer_fps)
    if src_fps <= 0:
        src_fps = outer_fps
    src_start_s = src_start_frame / src_fps

    return _resolve(
        project,
        mp_item=mp_item,
        src_start_s=src_start_s,
        duration_s=duration_s,
        depth=0,
    )


def _resolve(
    project,
    *,
    mp_item,
    src_start_s: float,
    duration_s: float,
    depth: int,
) -> list[SourceSegment]:
    """Recursively resolve one (mp_item, [src_start_s, src_start_s+duration_s)) slice.

    ``src_start_s`` is seconds into ``mp_item``'s internal timeline
    (equivalently seconds into the source file for file-backed items).
    ``duration_s`` is real-time seconds — timelines conform source media
    to real time, so this is invariant across fps changes.
    """
    if mp_item is None or depth > _MAX_DEPTH:
        return []

    file_path = mp_item.GetClipProperty("File Path") or ""
    if file_path:
        # Leaf: file-backed media pool item.
        src = Path(file_path)
        if not src.exists():
            log.warning("Source file missing on disk: %s", src)
            return []
        src_fps = _source_fps(mp_item, fallback=0.0)
        if src_fps <= 0:
            # Shouldn't happen for file-backed media; fall back to 30fps
            # only to avoid a divide-by-zero in downstream callers.
            src_fps = 30.0
        return [SourceSegment(src, src_start_s, src_start_s + duration_s, src_fps)]

    # Compound — find the matching project timeline by name.
    compound_name = mp_item.GetName() or ""
    inner_tl = _find_timeline_by_name(project, compound_name)
    if not inner_tl:
        log.warning(
            "Compound MP item '%s' has no matching project timeline; cannot resolve.",
            compound_name,
        )
        return []

    inner_fps = _timeline_fps(inner_tl)
    inner_start_frame = _timeline_start_frame(inner_tl)

    # Convert our [src_start_s, +duration_s) window into inner-timeline frames.
    seek_start_frame = inner_start_frame + int(round(src_start_s * inner_fps))
    seek_end_frame = seek_start_frame + int(round(duration_s * inner_fps))

    inner_items = inner_tl.GetItemListInTrack("audio", 1) or []
    if not inner_items:
        inner_items = inner_tl.GetItemListInTrack("video", 1) or []

    out: list[SourceSegment] = []
    cursor_frame = seek_start_frame
    for inner_item in inner_items:
        i_start = int(inner_item.GetStart())
        i_end = int(inner_item.GetEnd())
        if i_end <= cursor_frame:
            continue
        if i_start >= seek_end_frame:
            break

        slice_start_frame = max(cursor_frame, i_start)
        slice_end_frame = min(seek_end_frame, i_end)
        slice_duration_s = (slice_end_frame - slice_start_frame) / inner_fps
        if slice_duration_s <= 0:
            continue

        # Source-time within the inner item where our slice begins:
        # inner_item.GetSourceStartFrame() is in the inner item's own source fps
        # (either source-media fps for a file-backed inner item, or the inner
        # item's inner-timeline fps for a nested compound). Divide by that fps
        # to get seconds, then add the offset of our slice inside the inner item
        # (measured in inner-timeline fps seconds).
        inner_mp = inner_item.GetMediaPoolItem()
        if inner_mp is None:
            return []
        try:
            inner_src_start_frame = int(inner_item.GetSourceStartFrame() or 0)
        except Exception:
            inner_src_start_frame = 0
        inner_src_fps = _source_fps(inner_mp, fallback=inner_fps)
        if inner_src_fps <= 0:
            inner_src_fps = inner_fps
        inner_item_src_start_s = inner_src_start_frame / inner_src_fps
        offset_into_inner_s = (slice_start_frame - i_start) / inner_fps
        slice_src_start_s = inner_item_src_start_s + offset_into_inner_s

        sub = _resolve(
            project,
            mp_item=inner_mp,
            src_start_s=slice_src_start_s,
            duration_s=slice_duration_s,
            depth=depth + 1,
        )
        if not sub:
            # Couldn't resolve this sub-range — abort the whole item rather
            # than silently returning a partial transcript.
            return []
        out.extend(sub)
        cursor_frame = slice_end_frame
        if cursor_frame >= seek_end_frame:
            break

    if cursor_frame < seek_end_frame:
        log.warning(
            "Compound '%s' has a %d-frame gap past the inner timeline's last item.",
            compound_name,
            seek_end_frame - cursor_frame,
        )
        return []

    return out
