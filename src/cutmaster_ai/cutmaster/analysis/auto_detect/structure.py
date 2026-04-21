"""Tier 1 — score presets from transcript structure alone.

Pure-function module. Every signal is computed from Deepgram output
already on the run state; no network calls. Signals whose source field
is uniformly absent (e.g. per-word ``confidence`` on Gemini STT runs)
degrade to neutral 0 rather than failing the cascade.

Signals computed (see the proposal for full rationale):

  - speaker_count, speaker_turn_count, speaker_overlap_rate
  - words_per_second
  - median_sentence_length
  - question_rate                         (% sentences ending in ?)
  - filler_rate, restart_rate, dead_air_rate    (from scrubber counts)
  - pause_p95 (long-tail pause)
  - low_confidence_cluster_density
  - primary_speaker_share                 (top speaker's word share)
"""

from __future__ import annotations

import statistics

from .._sentences import sentence_spans
from .scoring import PresetScores, empty_scores


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_signals(
    transcript: list[dict],
    scrub_counts: dict | None = None,
) -> dict:
    """Extract raw signal values from a transcript + optional scrub counts.

    Exposed separately from :func:`score_by_transcript_structure` so
    tests can inspect the intermediate values and the Tier 4 LLM prompt
    can show them to the model.
    """
    if not transcript:
        return {
            "duration_s": 0.0,
            "word_count": 0,
            "speaker_count": 0,
            "speaker_turn_count": 0,
            "speaker_overlap_rate": 0.0,
            "words_per_second": 0.0,
            "median_sentence_length": 0.0,
            "question_rate": 0.0,
            "filler_rate": 0.0,
            "restart_rate": 0.0,
            "dead_air_rate": 0.0,
            "pause_p95": 0.0,
            "low_confidence_cluster_density": 0.0,
            "primary_speaker_share": 0.0,
        }

    duration_s = float(transcript[-1].get("end_time", 0.0))
    word_count = len(transcript)
    wps = word_count / duration_s if duration_s > 0 else 0.0

    speakers: dict[str, int] = {}
    for w in transcript:
        sid = (w.get("speaker_id") or "").strip()
        if sid:
            speakers[sid] = speakers.get(sid, 0) + 1
    speaker_count = len(speakers)
    top_share = max(speakers.values(), default=0) / word_count if word_count else 0.0

    turns = 0
    last = None
    for w in transcript:
        sid = w.get("speaker_id")
        if sid != last and last is not None:
            turns += 1
        last = sid

    overlap_pairs = 0
    for i in range(1, len(transcript)):
        prev = transcript[i - 1]
        cur = transcript[i]
        if prev.get("speaker_id") != cur.get("speaker_id") and float(
            cur.get("start_time", 0)
        ) < float(prev.get("end_time", 0)):
            overlap_pairs += 1
    overlap_rate = overlap_pairs / word_count if word_count else 0.0

    spans = sentence_spans(transcript)
    sentence_lengths = [b - a + 1 for a, b in spans]
    median_sent_len = statistics.median(sentence_lengths) if sentence_lengths else 0.0

    q_ends = 0
    for _a, b in spans:
        last_word = str(transcript[b].get("word", "")).rstrip(")\"'").rstrip()
        if last_word.endswith("?"):
            q_ends += 1
    question_rate = q_ends / len(spans) if spans else 0.0

    pauses: list[float] = []
    for i in range(1, len(transcript)):
        gap = float(transcript[i].get("start_time", 0.0)) - float(
            transcript[i - 1].get("end_time", 0.0)
        )
        if gap > 0:
            pauses.append(gap)
    if pauses:
        pauses.sort()
        p95 = pauses[min(len(pauses) - 1, int(len(pauses) * 0.95))]
    else:
        p95 = 0.0

    # Low-confidence cluster density — applause/music/crowd noise signal.
    low_conf_words = sum(
        1 for w in transcript if w.get("confidence") is not None and float(w["confidence"]) < 0.5
    )
    have_confidence = any(w.get("confidence") is not None for w in transcript)
    low_conf_density = (
        (low_conf_words / (duration_s / 60.0)) if (have_confidence and duration_s > 0) else 0.0
    )

    # Scrubber-derived rates. Source of truth is ``run["scrub_counts"]``
    # written by pipeline._scrub_stage. When absent we report zeros — the
    # cascade simply weights the remaining signals.
    filler_rate = restart_rate = dead_air_rate = 0.0
    if scrub_counts:
        original = max(1, int(scrub_counts.get("original", word_count)))
        filler_rate = int(scrub_counts.get("filler", 0)) / original
        restart_rate = int(scrub_counts.get("restart", 0)) / original
        dead_air_rate = int(scrub_counts.get("dead_air", 0)) / original

    return {
        "duration_s": duration_s,
        "word_count": word_count,
        "speaker_count": speaker_count,
        "speaker_turn_count": turns,
        "speaker_overlap_rate": overlap_rate,
        "words_per_second": wps,
        "median_sentence_length": float(median_sent_len),
        "question_rate": question_rate,
        "filler_rate": filler_rate,
        "restart_rate": restart_rate,
        "dead_air_rate": dead_air_rate,
        "pause_p95": p95,
        "low_confidence_cluster_density": low_conf_density,
        "primary_speaker_share": top_share,
    }


def _score_from_signals(sig: dict) -> PresetScores:
    """Map raw signals onto per-preset scores. Coefficients hand-tuned."""
    s = empty_scores()
    dur = sig["duration_s"]
    spk = sig["speaker_count"]
    turns = sig["speaker_turn_count"]
    overlap = sig["speaker_overlap_rate"]
    wps = sig["words_per_second"]
    med_sent = sig["median_sentence_length"]
    q_rate = sig["question_rate"]
    filler = sig["filler_rate"]
    restart = sig["restart_rate"]
    dead_air = sig["dead_air_rate"]
    p95 = sig["pause_p95"]
    low_conf = sig["low_confidence_cluster_density"]
    primary_share = sig["primary_speaker_share"]

    # --- presentation ----------------------------------------------------
    # Single dominant speaker, long duration, long sentences, long
    # deliberate pauses, low filler, occasional low-confidence clusters
    # (applause).
    s["presentation"] += _clamp((primary_share - 0.85) / 0.15) * 0.35 if spk <= 2 else 0.0
    s["presentation"] += _clamp(dur / 1200.0) * 0.15  # +full weight at 20 min
    s["presentation"] += _clamp((med_sent - 12) / 10.0) * 0.15
    s["presentation"] += _clamp((p95 - 1.0) / 2.0) * 0.10
    s["presentation"] += _clamp(low_conf / 2.0) * 0.10
    s["presentation"] += _clamp(1 - filler * 10) * 0.10

    # --- interview -------------------------------------------------------
    # Two speakers, many turns, moderate question rate, no overlap.
    if spk == 2:
        s["interview"] += 0.35
    elif spk >= 2:
        s["interview"] += 0.1
    s["interview"] += _clamp(turns / 40.0) * 0.25
    s["interview"] += _clamp(q_rate * 10) * 0.25  # 10 % ? ≈ full
    s["interview"] += _clamp(1 - overlap * 20) * 0.10
    s["interview"] += _clamp(dur / 900.0) * 0.05

    # --- podcast ---------------------------------------------------------
    # 3+ speakers, long duration, some overlap.
    if spk >= 3:
        s["podcast"] += 0.40
    elif spk == 2:
        s["podcast"] += 0.15
    s["podcast"] += _clamp(dur / 1800.0) * 0.20  # 30 min = full
    s["podcast"] += _clamp(overlap * 30) * 0.20
    s["podcast"] += _clamp(turns / 60.0) * 0.20

    # --- tutorial --------------------------------------------------------
    # Short sentences, very low filler, very low restart, 1 speaker. The
    # low-filler/restart/dead-air signals are "clean speech" indicators,
    # not tutorial-specific — gate them behind a speaker-count prior so
    # a clean-speaking podcast doesn't get misclassified as tutorial.
    if spk > 2:
        s["tutorial"] = 0.0  # 3+ speakers is never a tutorial
    else:
        if spk <= 1:
            s["tutorial"] += 0.25
        s["tutorial"] += _clamp((10 - med_sent) / 8.0) * 0.25
        s["tutorial"] += _clamp(1 - filler * 20) * 0.20
        s["tutorial"] += _clamp(1 - restart * 30) * 0.15
        s["tutorial"] += _clamp(1 - dead_air * 5) * 0.10
        s["tutorial"] += _clamp(wps / 2.5) * 0.05

    # --- vlog ------------------------------------------------------------
    # Single-speaker format. Fast wps, moderate filler, short-to-medium.
    if spk > 2:
        s["vlog"] = 0.0
    else:
        if spk <= 1:
            s["vlog"] += 0.20
        s["vlog"] += _clamp(wps / 3.0) * 0.25
        s["vlog"] += _clamp(filler * 15) * 0.15
        s["vlog"] += _clamp(1 - dur / 1800.0) * 0.20
        s["vlog"] += _clamp((10 - med_sent) / 10.0) * 0.20

    # --- product_demo ----------------------------------------------------
    # Single speaker, low filler, short-to-medium duration, crisp pacing.
    # Same gate as tutorial: 3+ speakers is never a product demo.
    if spk > 2:
        s["product_demo"] = 0.0
    else:
        if spk <= 1:
            s["product_demo"] += 0.20
        s["product_demo"] += _clamp(1 - filler * 20) * 0.20
        s["product_demo"] += _clamp((12 - med_sent) / 10.0) * 0.15
        s["product_demo"] += _clamp(1 - dur / 1200.0) * 0.25
        s["product_demo"] += _clamp(wps / 2.5) * 0.20

    # --- reaction --------------------------------------------------------
    # Very short, high filler, high wps. 10+ min content is never a
    # reaction — hard gate before accumulating the other indicators.
    if dur > 600:
        s["reaction"] = 0.0
    else:
        s["reaction"] += _clamp(1 - dur / 360.0) * 0.35
        s["reaction"] += _clamp(filler * 20) * 0.25
        s["reaction"] += _clamp(wps / 3.5) * 0.20
        s["reaction"] += _clamp(1 - p95 / 1.5) * 0.15
        if spk >= 2:
            s["reaction"] += 0.05

    # --- wedding ---------------------------------------------------------
    # Very mixed content, low wps, high dead-air (natural pauses), long p95.
    s["wedding"] += _clamp(1 - wps / 2.0) * 0.30
    s["wedding"] += _clamp(dead_air * 5) * 0.20
    s["wedding"] += _clamp(p95 / 3.0) * 0.25
    s["wedding"] += _clamp(dur / 2400.0) * 0.15  # often ≥ 40 min
    s["wedding"] += 0.10 if 2 <= spk <= 8 else 0.0

    return s


def score_by_transcript_structure(
    transcript: list[dict],
    scrub_counts: dict | None = None,
) -> PresetScores:
    """Public entry. Compute raw signals, then map to per-preset scores.

    Short-circuits on an empty transcript — "low filler" etc. evaluate
    as a perfect score on vacuous input, which would otherwise leak into
    the cascade as phantom preset votes.
    """
    if not transcript:
        return empty_scores()
    signals = compute_signals(transcript, scrub_counts)
    return _score_from_signals(signals)
