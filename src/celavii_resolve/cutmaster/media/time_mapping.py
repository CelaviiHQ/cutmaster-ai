"""Pure time-domain mapping between the source timeline and the cut timeline.

Execute builds the new timeline by appending selected source ranges. A
moment that lived at second ``T`` on the source timeline now lives at a
different second in the cut — either inside one of the kept pieces or
skipped entirely (when the editor cut it out).

This module owns that single mapping so execute + captions + any future
consumer agree on the arithmetic. Kept deliberately pure (no Resolve
imports) so it can be tested trivially.
"""

from __future__ import annotations


def map_source_to_new_timeline(
    resolved: list[dict],
    at_s: float,
) -> float | None:
    """Translate a source-timeline time to the cut-timeline time.

    ``resolved`` is the ``ResolvedCutSegment[]`` list produced by
    :mod:`resolve_segments`, serialised as dicts (``{"start_s", "end_s", ...}``).
    Returns the cut-timeline position in seconds, or ``None`` if ``at_s``
    falls between selected segments (the editor cut it out).

    The algorithm walks the resolved list in order, accumulating each
    piece's duration; when ``at_s`` falls inside a piece, it returns the
    accumulated offset plus the in-piece delta. This mirrors how
    :func:`execute.execute_plan` appends pieces end-to-end.
    """
    running = 0.0
    for piece in resolved:
        piece_dur = piece["end_s"] - piece["start_s"]
        if piece["start_s"] <= at_s <= piece["end_s"]:
            return running + (at_s - piece["start_s"])
        running += piece_dur
    return None


def remap_words_to_new_timeline(
    words: list[dict],
    resolved: list[dict],
) -> list[dict]:
    """Filter ``words`` to those surviving the cut, remapping timestamps.

    For each word whose ``start_time`` falls inside a kept piece, emit a
    copy with ``start_time`` / ``end_time`` replaced by new-timeline
    seconds. Words that straddle a cut boundary keep their original
    duration offset from the remapped start — good enough for captions,
    which rarely care about sub-frame alignment.

    Used by caption generation (see :mod:`captions`) and is a natural
    fit for any future per-word subtitle overlay.
    """
    out: list[dict] = []
    for w in words:
        start = float(w["start_time"])
        end = float(w["end_time"])
        new_start = map_source_to_new_timeline(resolved, start)
        if new_start is None:
            continue
        new_end = map_source_to_new_timeline(resolved, end)
        if new_end is None:
            # Word crosses a cut boundary — preserve the duration from the
            # mapped start so the caption still reads cleanly.
            new_end = new_start + (end - start)
        out.append({**w, "start_time": new_start, "end_time": new_end})
    return out
