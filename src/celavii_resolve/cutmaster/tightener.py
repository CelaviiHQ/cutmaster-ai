"""Tightener mode — per-take word-block segmentation (no Director LLM).

v2-3 is a configuration of v2-2's assembled pipeline: `timeline_mode=assembled`
and `reorder_allowed=false` are forced, the transcript is aggressively scrubbed,
and this module directly converts the scrubbed words into CutSegments. No
Director call — the scrubber's output IS the edit.

Inside a take, the scrubber has already removed filler + dead-air words,
leaving time gaps where they used to live. We split the take into CutSegments
on any gap larger than ``gap_threshold_s``. This yields a tight, filler-free
playback that preserves the take's original order.

Pure — no Resolve, no LLM. Testable with plain word dicts.
"""

from __future__ import annotations

from .director import CutSegment

DEFAULT_BLOCK_GAP_S = 0.3


def _segment_take(
    take: dict,
    gap_threshold_s: float,
) -> list[CutSegment]:
    """Split one take's scrubbed transcript into contiguous kept-word blocks.

    A new block starts whenever the gap between consecutive kept words
    exceeds ``gap_threshold_s`` — i.e. the scrubber removed something in
    between. Returns one ``CutSegment`` per block.
    """
    words = take.get("transcript") or []
    if not words:
        return []

    segments: list[CutSegment] = []
    item_index = take.get("item_index", -1)
    source_name = take.get("source_name", "")
    block_start = 0
    total_blocks = 1  # minimum — refined when we hit gaps

    # First pass: count how many blocks we'll emit so reason strings
    # can carry "block i/N" annotation without a second loop.
    for i in range(1, len(words)):
        gap = float(words[i]["start_time"]) - float(words[i - 1]["end_time"])
        if gap > gap_threshold_s:
            total_blocks += 1

    block_no = 1
    for i in range(1, len(words)):
        gap = float(words[i]["start_time"]) - float(words[i - 1]["end_time"])
        if gap > gap_threshold_s:
            reason = (
                f"take {item_index}: '{source_name}' block {block_no}/{total_blocks}"
                if total_blocks > 1
                else f"take {item_index}: '{source_name}'"
            )
            segments.append(
                CutSegment(
                    start_s=float(words[block_start]["start_time"]),
                    end_s=float(words[i - 1]["end_time"]),
                    reason=reason,
                )
            )
            block_start = i
            block_no += 1

    tail_reason = (
        f"take {item_index}: '{source_name}' block {block_no}/{total_blocks}"
        if total_blocks > 1
        else f"take {item_index}: '{source_name}'"
    )
    segments.append(
        CutSegment(
            start_s=float(words[block_start]["start_time"]),
            end_s=float(words[-1]["end_time"]),
            reason=tail_reason,
        )
    )
    return segments


def build_tightener_segments(
    takes: list[dict],
    gap_threshold_s: float = DEFAULT_BLOCK_GAP_S,
) -> list[CutSegment]:
    """Produce Tightener CutSegments for a list of takes.

    ``takes`` follows the same shape as
    :func:`assembled.build_take_entries` returns. Empty takes (no kept
    words) are skipped — they contribute nothing to the cut. Take order
    is preserved; cross-take reordering is out of scope for Tightener.
    """
    segments: list[CutSegment] = []
    for take in takes:
        segments.extend(_segment_take(take, gap_threshold_s))
    return segments


def tightener_stats(
    original_transcript: list[dict],
    takes: list[dict],
    segments: list[CutSegment],
) -> dict:
    """Compute summary stats for the Review screen.

    Returns a dict with:
      - ``kept_words``: words surviving the scrubber (sum across takes)
      - ``original_words``: length of the raw transcript
      - ``percent_tighter``: 1 - (segment_total_duration / take_total_duration)
        — i.e. how much time was trimmed. Clamped to [0, 1]; 0 if there
        are no takes / segments.
    """
    kept_words = sum(len(t.get("transcript") or []) for t in takes)
    original_words = len(original_transcript)

    take_total = sum(float(t.get("end_s", 0.0)) - float(t.get("start_s", 0.0)) for t in takes)
    segment_total = sum(s.end_s - s.start_s for s in segments)

    if take_total <= 0:
        percent_tighter = 0.0
    else:
        percent_tighter = max(0.0, min(1.0, 1.0 - (segment_total / take_total)))

    return {
        "kept_words": kept_words,
        "original_words": original_words,
        "percent_tighter": percent_tighter,
        "take_total_s": take_total,
        "segment_total_s": segment_total,
    }
