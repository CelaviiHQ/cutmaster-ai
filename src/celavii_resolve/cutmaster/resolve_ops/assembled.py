"""Assembled-mode plumbing — split a global transcript into per-take transcripts.

v2-2's assembled-mode Director needs its input shaped as a list of takes, each
with its own word list (indexed from 0). The pipeline reads the source timeline
items, splits the scrubbed (or raw) transcript by which item each word falls
into, and hands the result to :func:`director.build_assembled_cut_plan`.

The two helpers here are deliberately pure — they accept "item summary" dicts
shaped like what the HTTP route gathers from Resolve, so this module can be
unit-tested without mocking the Resolve API.
"""

from __future__ import annotations

from typing import TypedDict


class ItemSummary(TypedDict):
    """The minimum slice of a ``TimelineItem`` we need for take splitting."""

    item_index: int  # 0-based index within the source-timeline video track 1
    source_name: str  # media-pool clip name, for prompt context
    start_s: float  # timeline seconds — inclusive
    end_s: float  # timeline seconds — exclusive


def _word_timeline_seconds(word: dict) -> float:
    """Each STT/scrubbed entry stores timeline seconds under ``start_time``."""
    return float(word["start_time"])


def split_transcript_per_item(
    transcript: list[dict],
    items: list[ItemSummary],
) -> list[list[dict]]:
    """Partition a global transcript into one list per item.

    Words are assigned to the first item whose ``[start_s, end_s)`` interval
    contains the word's ``start_time``. Words outside every item's range are
    silently dropped — they happen at timeline gaps (before the first take,
    between takes, or past the last take), and no take owns them.

    Item entries in the returned list follow the input item order. Word order
    within each per-item list preserves the transcript's original ordering.
    """
    per_item: list[list[dict]] = [[] for _ in items]
    for word in transcript:
        t = _word_timeline_seconds(word)
        for i, item in enumerate(items):
            if item["start_s"] <= t < item["end_s"]:
                per_item[i].append(word)
                break
    return per_item


def build_take_entries(
    items: list[ItemSummary],
    per_item_transcripts: list[list[dict]],
) -> list[dict]:
    """Shape items + per-item transcripts into the Director's take payload.

    Each entry matches the docstring in :func:`director._assembled_prompt`:

        {
          "item_index": int,
          "source_name": str,
          "start_s": float, "end_s": float,
          "transcript": [{"i": int, "word": str,
                          "start_time": float, "end_time": float,
                          "speaker_id": str}, ...]
        }

    The ``i`` field is the word's 0-based index within this take's transcript —
    the Director references these in ``kept_word_spans``. Takes with zero
    words are still emitted (the Director is free to drop them) so the
    caller's item_index → input-position mapping stays stable.
    """
    out: list[dict] = []
    for item, words in zip(items, per_item_transcripts, strict=True):
        transcript = [
            {
                "i": i,
                "word": w.get("word", ""),
                "start_time": float(w["start_time"]),
                "end_time": float(w["end_time"]),
                "speaker_id": w.get("speaker_id", ""),
            }
            for i, w in enumerate(words)
        ]
        out.append(
            {
                "item_index": item["item_index"],
                "source_name": item["source_name"],
                "start_s": float(item["start_s"]),
                "end_s": float(item["end_s"]),
                "transcript": transcript,
            }
        )
    return out


def read_items_on_track(tl, track_index: int = 1) -> list[ItemSummary]:
    """Read the timeline's V{track_index} items into ``ItemSummary`` dicts.

    Thin Resolve adapter so the HTTP route can stay short. Kept at the bottom
    of this module and called only when we're already inside build-plan.
    Requires ``tl`` to be a Resolve ``Timeline``.
    """
    from .frame_math import _timeline_fps, _timeline_start_frame

    fps = _timeline_fps(tl)
    tl_start_frame = _timeline_start_frame(tl)
    items = tl.GetItemListInTrack("video", track_index) or []

    out: list[ItemSummary] = []
    for idx, item in enumerate(items):
        mp_item = item.GetMediaPoolItem()
        name = mp_item.GetName() if mp_item is not None else f"item_{idx}"
        start_frame = item.GetStart() - tl_start_frame
        end_frame = item.GetEnd() - tl_start_frame
        out.append(
            ItemSummary(
                item_index=idx,
                source_name=str(name),
                start_s=start_frame / fps,
                end_s=end_frame / fps,
            )
        )
    return out
