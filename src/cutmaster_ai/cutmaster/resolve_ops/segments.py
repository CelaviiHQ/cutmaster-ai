"""Map Director ``CutSegment[]`` timeline-seconds to source-clip frames.

The Director returns ``start_s`` / ``end_s`` in timeline-seconds relative to
the analysed timeline's start. To execute the plan we need to know, for each
segment: which media pool item is playing, and at what source in/out frames.

A Director segment may span multiple timeline items (for raw-dump timelines
with clips butted end-to-end, this is the common case — sentences cross
camera takes). We **auto-split**: one logical segment becomes N timeline
pieces, one per overlapping item. Each piece still plays back seamlessly
because the pieces are anchored at the same source frames that were on the
original timeline.

Reads Resolve; does not mutate anything. Safe for dry-run preview.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..core.director import CutSegment
from ..media.frame_math import _source_fps, _timeline_fps, _timeline_start_frame
from .source_mapper import _item_clip_speed


class ResolvedCutSegment(BaseModel):
    """A CutSegment enriched with the Resolve-specific fields needed to append.

    If the source Director segment crossed a timeline-item boundary, it
    becomes multiple ``ResolvedCutSegment`` entries in order, each with
    ``part_index`` / ``part_total`` set. UIs should render them as a group.
    """

    start_s: float
    end_s: float
    reason: str
    # Resolved via source walk:
    source_item_id: str
    source_item_name: str
    source_in_frame: int
    source_out_frame: int
    # Debug/UI:
    timeline_start_frame: int
    timeline_end_frame: int
    speed: float
    speed_ramped: bool
    # Split info — index within the originating Director segment
    part_index: int = 0
    part_total: int = 1
    warnings: list[str] = []


def _find_overlapping_pieces(
    items: list,
    seg_start_frame: int,
    seg_end_frame: int,
) -> list[tuple]:
    """Return a list of (item, mp_item, tl_in, tl_out) for items that
    overlap the given timeline-frame range, in timeline order.

    Raises ``ValueError`` if an overlapping item has no media pool item
    (compound clips / nested timelines are not yet supported).
    """
    pieces: list[tuple] = []
    for item in items:
        item_start = item.GetStart()
        item_end = item.GetEnd()  # exclusive
        tl_in = max(item_start, seg_start_frame)
        tl_out = min(item_end, seg_end_frame)
        if tl_out <= tl_in:
            continue
        mp_item = item.GetMediaPoolItem()
        if mp_item is None:
            raise ValueError(
                f"timeline item '{item.GetName()}' at [{item_start}, {item_end}] "
                "has no media pool item (compound / nested / generator). "
                "auto-split requires simple source clips."
            )
        pieces.append((item, mp_item, tl_in, tl_out))
    return pieces


def resolve_segments(
    tl, segments: list[CutSegment], *, video_track: int | None = None
) -> list[ResolvedCutSegment]:
    """Turn timeline-seconds segments into source-frame pieces.

    Cross-boundary segments are auto-split into per-item pieces. If the
    entire segment falls in a gap (no overlapping items on the picked
    video track), raises :class:`ValueError`.

    ``video_track`` is 1-based; ``None`` auto-picks via
    :func:`track_picker.pick_video_track`.
    """
    from .track_picker import pick_video_track

    if video_track is None:
        video_track = pick_video_track(tl)

    fps = _timeline_fps(tl)
    tl_start = _timeline_start_frame(tl)
    items = tl.GetItemListInTrack("video", video_track) or []

    out: list[ResolvedCutSegment] = []

    for seg in segments:
        seg_start_frame = tl_start + round(seg.start_s * fps)
        seg_end_frame = tl_start + round(seg.end_s * fps)

        if seg_end_frame <= seg_start_frame:
            raise ValueError(f"segment [{seg.start_s},{seg.end_s}]s has non-positive duration")

        try:
            pieces = _find_overlapping_pieces(items, seg_start_frame, seg_end_frame)
        except ValueError as exc:
            raise ValueError(f"segment [{seg.start_s:.3f},{seg.end_s:.3f}]s: {exc}")

        if not pieces:
            raise ValueError(
                f"segment [{seg.start_s:.3f},{seg.end_s:.3f}]s does not overlap "
                f"any item on video track 1"
            )

        part_total = len(pieces)
        for i, (item, mp_item, tl_in, tl_out) in enumerate(pieces):
            offset_in_item = tl_in - item.GetStart()  # timeline-frames
            duration = tl_out - tl_in  # timeline-frames
            speed = _item_clip_speed(item, mp_item)
            try:
                left_offset = int(item.GetLeftOffset() or 0)
            except Exception:
                left_offset = 0

            # Source-vs-timeline fps: AppendToTimeline wants startFrame/endFrame
            # in source-media frames. A 30 fps clip on a 24 fps timeline needs
            # its timeline-frame offsets scaled by source_fps / tl_fps, else
            # Resolve under-appends each piece by tl_fps/source_fps (the v2-6
            # bug that parked markers past the cut timeline's end).
            src_fps = _source_fps(mp_item, fallback=fps)
            fps_ratio = (src_fps / fps) if fps > 0 else 1.0

            src_in = left_offset + int(round(offset_in_item * fps_ratio * speed))
            src_out = left_offset + int(round((offset_in_item + duration) * fps_ratio * speed))

            piece_start_s = (tl_in - tl_start) / fps
            piece_end_s = (tl_out - tl_start) / fps

            reason = seg.reason
            if part_total > 1:
                reason = (
                    f"{seg.reason} (part {i + 1}/{part_total})"
                    if seg.reason
                    else f"(part {i + 1}/{part_total})"
                )

            warnings: list[str] = []
            if speed != 1.0:
                warnings.append(f"speed-ramped source ({speed}×) — verify in viewer")
            if abs(src_fps - fps) > 0.01:
                warnings.append(
                    f"source fps {src_fps:.2f} differs from timeline fps "
                    f"{fps:.2f} — pieces placed at real-time"
                )

            out.append(
                ResolvedCutSegment(
                    start_s=piece_start_s,
                    end_s=piece_end_s,
                    reason=reason,
                    source_item_id=mp_item.GetUniqueId(),
                    source_item_name=mp_item.GetName(),
                    source_in_frame=src_in,
                    source_out_frame=src_out,
                    timeline_start_frame=tl_in,
                    timeline_end_frame=tl_out,
                    speed=speed,
                    speed_ramped=speed != 1.0,
                    part_index=i,
                    part_total=part_total,
                    warnings=warnings,
                )
            )

    return out
