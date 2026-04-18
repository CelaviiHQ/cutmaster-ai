"""Resolve a timeline item to its underlying source file(s) + time ranges.

Timeline items can be backed by:

1. A file-backed media pool item (``Type = "Video"`` / ``"Video + Audio"``).
   Trivial — read ``File Path`` and compute the in/out seconds.
2. A compound clip (``Type = "Timeline"``), where the MP item is itself a
   Resolve timeline. The outer item's ``SourceStartFrame / Duration``
   address a sub-range of that inner timeline, and we have to walk it to
   find the real source file. Inner timelines can themselves contain
   compounds, so the walk is recursive.

Both paths converge on the same output: a list of :class:`SourceSegment`
tuples, each pointing at a real file + seconds range. Empty list means the
item could not be resolved (generator / unmatched compound / broken link).

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

    ``outer_fps`` is the fps of the timeline that ``item`` lives on. Needed
    because ``item.GetDuration()`` is in that timeline's frames, whereas
    ``GetSourceStartFrame()`` is in the item's own source-media frames.
    """
    duration_frames = int(item.GetDuration())
    try:
        src_start_frame = int(item.GetSourceStartFrame() or 0)
    except Exception:
        src_start_frame = 0

    return _resolve(
        project,
        item_mp=item.GetMediaPoolItem(),
        src_start_frame=src_start_frame,
        duration_outer_frames=duration_frames,
        outer_fps=outer_fps,
        depth=0,
    )


def _resolve(
    project,
    *,
    item_mp,
    src_start_frame: int,
    duration_outer_frames: int,
    outer_fps: float,
    depth: int,
) -> list[SourceSegment]:
    if item_mp is None or depth > _MAX_DEPTH:
        return []

    file_path = item_mp.GetClipProperty("File Path") or ""
    if file_path:
        # Leaf: file-backed media pool item.
        src = Path(file_path)
        if not src.exists():
            log.warning("Source file missing on disk: %s", src)
            return []
        src_fps = _source_fps(item_mp, fallback=outer_fps)
        in_s = src_start_frame / src_fps
        # Duration measured on the outer timeline — always real-time.
        out_s = in_s + duration_outer_frames / outer_fps
        return [SourceSegment(src, in_s, out_s, src_fps)]

    # Compound — find the matching project timeline by name.
    compound_name = item_mp.GetName() or ""
    inner_tl = _find_timeline_by_name(project, compound_name)
    if not inner_tl:
        log.warning(
            "Compound MP item '%s' has no matching project timeline; cannot resolve.",
            compound_name,
        )
        return []

    inner_fps = _timeline_fps(inner_tl)
    inner_start_frame = _timeline_start_frame(inner_tl)

    # ``src_start_frame`` on a compound is expressed in the inner timeline's
    # frames at the *inner* fps. ``duration_outer_frames`` is outer timeline
    # frames; convert to inner-timeline frames via the fps ratio.
    inner_duration_frames = int(round(duration_outer_frames * (inner_fps / outer_fps)))
    seek_start = inner_start_frame + src_start_frame
    seek_end = seek_start + inner_duration_frames

    # Walk inner audio track 1 items — audio and video are typically aligned,
    # and we only need audio for STT. If the caller wants video we switch to
    # video track 1 in the walk; for now audio covers both ffmpeg_audio and
    # per-clip STT use cases.
    inner_items = inner_tl.GetItemListInTrack("audio", 1) or []
    if not inner_items:
        inner_items = inner_tl.GetItemListInTrack("video", 1) or []

    out: list[SourceSegment] = []
    cursor = seek_start
    for inner_item in inner_items:
        i_start = int(inner_item.GetStart())
        i_end = int(inner_item.GetEnd())
        if i_end <= cursor:
            continue
        if i_start >= seek_end:
            break
        # Overlap with [cursor, seek_end)
        slice_start = max(cursor, i_start)
        slice_end = min(seek_end, i_end)
        slice_duration_inner = slice_end - slice_start
        if slice_duration_inner <= 0:
            continue

        offset_into_inner = slice_start - i_start
        try:
            inner_src_start = int(inner_item.GetSourceStartFrame() or 0)
        except Exception:
            inner_src_start = 0

        # Recurse with a synthetic item pointing at the inner MP + adjusted
        # source offset. Duration is expressed in ``inner_fps`` frames, so
        # that's the ``outer_fps`` for the recursive call.
        sub = _resolve(
            project,
            item_mp=inner_item.GetMediaPoolItem(),
            src_start_frame=inner_src_start + offset_into_inner,
            duration_outer_frames=slice_duration_inner,
            outer_fps=inner_fps,
            depth=depth + 1,
        )
        if not sub:
            # Couldn't resolve this sub-range — abort the whole item rather
            # than silently returning a partial transcript.
            return []
        out.extend(sub)
        cursor = slice_end
        if cursor >= seek_end:
            break

    if cursor < seek_end:
        # Gap at the tail — compound item extends past its inner timeline's
        # last clip. Treat as unresolvable so the caller can downgrade cleanly.
        log.warning(
            "Compound '%s' has a %d-frame gap past the inner timeline's last item.",
            compound_name,
            seek_end - cursor,
        )
        return []

    return out
