"""Shared sentence-boundary helpers for transcript consumers.

Multiple consumers need to reason about sentences, not raw words:

- Director prompts, which ship a sentence-coalesced transcript so the
  model picks whole-thought ranges instead of mid-phrase cuts.
- Auto-detect Tier 1 signals (median sentence length, question rate,
  pause-shape statistics).

Keeping this in one module avoids cross-package imports
(``analysis → core``) and keeps the boundary rules in one place so
changes (e.g. tuning the pause fallback) don't drift between callers.

Boundary rule — punctuation-first:

- When ANY word in the transcript carries terminal punctuation
  (``. ? ! …``), sentence breaks require punctuation OR speaker change.
  Pauses alone do not split — that historically cut mid-thought on
  natural breaths ("...because it's <pause> the product...").
- When no punctuation is present we fall back to a generous pause floor
  (:data:`SENTENCE_PAUSE_FALLBACK_S`).
"""

from __future__ import annotations

_SENTENCE_PUNCT = (".", "?", "!", "…")
SENTENCE_PAUSE_FALLBACK_S = 0.9


def _word_ends_sentence(word: str) -> bool:
    w = (word or "").rstrip().rstrip(")\"'")
    return bool(w) and w.endswith(_SENTENCE_PUNCT)


def has_reliable_punctuation(transcript: list[dict]) -> bool:
    """True when at least one word carries terminal punctuation.

    Deepgram (with ``smart_format=true``) and Gemini STT both emit
    punctuation; a couple of test fixtures and older Whisper configs
    don't. When punctuation is reliable we ignore pause heuristics
    entirely — breaths mid-thought are not sentence breaks.
    """
    return any(_word_ends_sentence(str(w.get("word", ""))) for w in transcript)


def sentence_spans(transcript: list[dict]) -> list[tuple[int, int]]:
    """Return ``(first_word_idx, last_word_idx)`` inclusive pairs per sentence."""
    if not transcript:
        return []
    use_pause_fallback = not has_reliable_punctuation(transcript)
    spans: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(transcript)):
        prev = transcript[i - 1]
        cur = transcript[i]
        speaker_changed = prev.get("speaker_id") != cur.get("speaker_id")
        punct_break = _word_ends_sentence(str(prev.get("word", "")))
        pause_break = False
        if use_pause_fallback:
            gap = float(cur.get("start_time", 0.0)) - float(prev.get("end_time", 0.0))
            pause_break = gap >= SENTENCE_PAUSE_FALLBACK_S
        if punct_break or pause_break or speaker_changed:
            spans.append((start, i - 1))
            start = i
    spans.append((start, len(transcript) - 1))
    return spans


def sentence_edge_times(transcript: list[dict]) -> tuple[list[float], list[float]]:
    """Return ``(sentence_start_times, sentence_end_times)``, each sorted ascending."""
    spans = sentence_spans(transcript)
    starts = sorted({float(transcript[a]["start_time"]) for a, _ in spans})
    ends = sorted({float(transcript[b]["end_time"]) for _, b in spans})
    return starts, ends


def coalesce_to_sentences(transcript: list[dict]) -> list[dict]:
    """Return one row per sentence, shaped for prompts.

    Each row::

        {"i": int, "spk": str, "t": [t_start, t_end], "text": str}

    On a 44-minute interview this drops a word-level JSON payload from
    ~760 kB to ~30-60 kB. Consumers that need word-level fidelity
    (the scrubber, per-clip STT) keep using the raw transcript; anything
    that picks ranges in seconds uses this.
    """
    spans = sentence_spans(transcript)
    out: list[dict] = []
    for i, (a, b) in enumerate(spans):
        first = transcript[a]
        last = transcript[b]
        text = " ".join(str(w.get("word", "")) for w in transcript[a : b + 1])
        out.append(
            {
                "i": i,
                "spk": first.get("speaker_id", ""),
                "t": [round(float(first["start_time"]), 3), round(float(last["end_time"]), 3)],
                "text": text.strip(),
            }
        )
    return out
