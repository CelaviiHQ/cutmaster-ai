"""Speaker-aware transcript helpers (v2-5).

STT already writes a ``speaker_id`` on every word (e.g. ``"S1"`` / ``"S2"``);
v1 ignored it end-to-end. v2-5 surfaces it to the Director for the Interview
and Podcast presets so the model can prefer guest answers over interviewer
filler, and lets the user rename ``S1`` → ``Host`` etc. so the prompt reads
in human terms.

All helpers are pure and Resolve-free — exercised directly in unit tests.
"""

from __future__ import annotations


def detect_speakers(transcript: list[dict]) -> list[str]:
    """Return unique ``speaker_id`` values in first-appearance order.

    Words missing ``speaker_id`` (or with an empty one) are ignored. Order
    matters: the UI's rename form renders inputs in the order speakers first
    spoke, which is what the editor expects.
    """
    seen: list[str] = []
    for w in transcript:
        sid = (w.get("speaker_id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.append(sid)
    return seen


def speaker_stats(transcript: list[dict]) -> dict[str, int]:
    """Return ``{speaker_id: word_count}`` across the transcript.

    Used by the UI to disambiguate which speaker is likely the host vs
    guest when the user has to label them (the higher word count is
    usually the host on interview content).
    """
    counts: dict[str, int] = {}
    for w in transcript:
        sid = (w.get("speaker_id") or "").strip()
        if not sid:
            continue
        counts[sid] = counts.get(sid, 0) + 1
    return counts


def apply_speaker_labels(
    transcript: list[dict],
    labels: dict[str, str] | None,
) -> list[dict]:
    """Return a new transcript with ``speaker_id`` rewritten per ``labels``.

    Empty / missing labels for a speaker fall through unchanged, so partial
    labelling ("Host" named, "S2" left alone) works. The input list is not
    mutated — each word dict is shallow-copied before its ``speaker_id`` is
    replaced. Keeps the rest of the word payload (``word``, ``start_time``,
    ``end_time``, any ``i`` index already set by the assembled builder)
    untouched.
    """
    if not labels:
        return transcript
    cleaned = {k: v.strip() for k, v in labels.items() if v and v.strip()}
    if not cleaned:
        return transcript

    out: list[dict] = []
    for w in transcript:
        sid = w.get("speaker_id")
        if sid in cleaned:
            relabeled = dict(w)
            relabeled["speaker_id"] = cleaned[sid]
            out.append(relabeled)
        else:
            out.append(w)
    return out
