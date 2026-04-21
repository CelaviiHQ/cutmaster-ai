"""Cascade behaviour tests — no LLM calls should happen on clear-cut content.

Each fixture builds a synthetic transcript with the structural shape
of the target preset; the cascade should classify it confidently from
Tiers 1+2 alone. The LLM client is monkey-patched to raise so any
accidental invocation surfaces as a test failure.
"""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.analysis.auto_detect import detect_preset
from cutmaster_ai.cutmaster.analysis.auto_detect.cue_vocab import (
    score_by_cue_vocabulary,
)
from cutmaster_ai.cutmaster.analysis.auto_detect.scoring import (
    classifiable_presets,
    merge,
    top_n,
)
from cutmaster_ai.cutmaster.analysis.auto_detect.structure import (
    compute_signals,
    score_by_transcript_structure,
)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Guarantee the cascade never calls the LLM during these tests."""
    import cutmaster_ai.intelligence.llm as llm_mod

    def _raise(*a, **kw):
        raise AssertionError("LLM was called — cascade should resolve offline")

    monkeypatch.setattr(llm_mod, "call_structured", _raise)


def _word(
    text: str, start: float, end: float, speaker: str = "S1", confidence: float | None = 0.95
) -> dict:
    w: dict = {
        "word": text,
        "start_time": start,
        "end_time": end,
        "speaker_id": speaker,
    }
    if confidence is not None:
        w["confidence"] = confidence
    return w


def _sentence(words: list[str], start: float, speaker: str = "S1", wps: float = 2.0) -> list[dict]:
    out = []
    t = start
    for w in words:
        dur = 1.0 / wps
        out.append(_word(w, t, t + dur, speaker))
        t += dur
    return out


def _build_interview(duration_s: float = 900.0) -> list[dict]:
    """Two-speaker back-and-forth with question rate ~15%."""
    words: list[dict] = []
    t = 0.0
    question_templates = [
        "What do you think about this?",
        "How did that make you feel?",
        "Can you tell us more?",
    ]
    answer_templates = [
        "I remember when I first encountered that idea.",
        "The thing is, it really changed everything for me.",
        "What happened was completely unexpected at the time.",
    ]
    while t < duration_s:
        q = question_templates[len(words) % len(question_templates)]
        words.extend(_sentence(q.split(), t, speaker="S1"))
        t += 3.0
        a = answer_templates[len(words) % len(answer_templates)]
        words.extend(_sentence(a.split(), t, speaker="S2"))
        t += 6.0
    return words


def _build_podcast(duration_s: float = 1800.0) -> list[dict]:
    """3+ speakers, long form, speaking-of transitions."""
    words: list[dict] = []
    t = 0.0
    speakers = ["S1", "S2", "S3", "S4"]
    templates = [
        "Speaking of that topic, I have a story to share.",
        "That reminds me of something that happened last year.",
        "On that note, let's talk about the bigger picture.",
        "Moving on to the next thing I wanted to mention here.",
    ]
    i = 0
    while t < duration_s:
        spk = speakers[i % len(speakers)]
        text = templates[i % len(templates)]
        words.extend(_sentence(text.split(), t, speaker=spk))
        t += 5.0
        i += 1
    return words


def _build_presentation(duration_s: float = 1800.0) -> list[dict]:
    """One dominant speaker, long deliberate pauses, rhetorical cues."""
    words: list[dict] = []
    t = 0.0
    templates = [
        "Imagine what it would mean to rebuild the entire system from scratch.",
        "The key insight here is that nobody asked the obvious question first.",
        "Think about the last time you truly understood a complex idea deeply.",
        "So here's the thing that most people miss when they look at this.",
    ]
    i = 0
    while t < duration_s:
        text = templates[i % len(templates)]
        words.extend(_sentence(text.split(), t, speaker="S1", wps=1.7))
        t += 6.0
        # Deliberate pause between sentences — long p95 signal.
        t += 1.5
        i += 1
    return words


def _build_tutorial(duration_s: float = 600.0) -> list[dict]:
    """Step cues, single speaker, crisp pacing."""
    words: list[dict] = []
    t = 0.0
    templates = [
        "Step one is to click the file menu.",
        "Next you will select the export option.",
        "Then drag the timeline to the deliver page.",
        "Type the output filename in this field.",
        "Finally press the render button to start.",
    ]
    i = 0
    while t < duration_s:
        text = templates[i % len(templates)]
        words.extend(_sentence(text.split(), t, speaker="S1", wps=2.5))
        t += 3.0
        i += 1
    return words


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------


def test_classifiable_presets_excludes_mode_presets():
    keys = set(classifiable_presets())
    for mode_only in ("tightener", "clip_hunter", "short_generator", "auto"):
        assert mode_only not in keys


def test_compute_signals_empty_transcript():
    sig = compute_signals([])
    assert sig["duration_s"] == 0.0
    assert sig["word_count"] == 0


def test_compute_signals_basic():
    words = _build_interview(duration_s=120.0)
    sig = compute_signals(words)
    assert sig["speaker_count"] == 2
    assert sig["speaker_turn_count"] > 5
    assert sig["question_rate"] > 0.2
    assert sig["words_per_second"] > 0.5


# ---------------------------------------------------------------------------
# Cascade classification tests
# ---------------------------------------------------------------------------


def test_interview_detected_without_llm():
    rec = detect_preset(_build_interview(duration_s=900.0))
    assert rec.preset == "interview"
    assert rec.confidence > 0.5


def test_podcast_detected_without_llm():
    rec = detect_preset(_build_podcast(duration_s=1800.0))
    assert rec.preset == "podcast"
    assert rec.confidence > 0.5


def test_presentation_detected_without_llm():
    rec = detect_preset(_build_presentation(duration_s=1800.0))
    # With one speaker, no questions, deliberate pauses, and rhetorical
    # openers the cascade should land on presentation OR at least have it
    # as the top/second pick.
    assert rec.preset in {"presentation", "tutorial"}
    if rec.preset == "tutorial":
        assert "presentation" in (rec.alternatives or [])


def test_tutorial_step_cues_detected():
    rec = detect_preset(_build_tutorial(duration_s=600.0))
    assert rec.preset == "tutorial"


def test_suggested_target_length_is_reasonable():
    rec = detect_preset(_build_interview(duration_s=900.0))
    assert rec.suggested_target_length_s is not None
    assert 45 <= rec.suggested_target_length_s <= 600


def test_empty_transcript_returns_safe_default():
    rec = detect_preset([])
    assert rec.preset == "vlog"
    assert rec.confidence == 0.0


# ---------------------------------------------------------------------------
# Degradation tests — cascade still works when fields are missing
# ---------------------------------------------------------------------------


def test_no_confidence_signals_degrade_gracefully():
    """Gemini STT runs don't populate per-word confidence — cascade must still work."""
    words = _build_interview(duration_s=900.0)
    for w in words:
        w.pop("confidence", None)
    rec = detect_preset(words)
    assert rec.preset == "interview"


def test_no_punctuation_falls_back_to_pauses():
    """Unpunctuated transcript still partitions into sentences via pauses."""
    # Strip punctuation and the sentence coalescer falls back to pause-based
    # breaks. The cascade still delivers a non-empty top preset.
    words = _build_interview(duration_s=900.0)
    for w in words:
        w["word"] = w["word"].rstrip(".?!,")
    rec = detect_preset(words)
    assert rec.preset in classifiable_presets()


# ---------------------------------------------------------------------------
# Cache re-entry
# ---------------------------------------------------------------------------


def test_cache_hit_skips_recomputation(monkeypatch):
    words = _build_interview(duration_s=900.0)
    run_state: dict = {"scrubbed": words, "run_id": "__test_cache__"}
    rec1 = detect_preset(words, run_state=run_state)

    # Poison the scorers — if cache is honored the second call never invokes them.
    import cutmaster_ai.cutmaster.analysis.auto_detect.structure as structure_mod

    def _raise(*a, **kw):
        raise AssertionError("cache bypassed — scorer called twice")

    monkeypatch.setattr(structure_mod, "score_by_transcript_structure", _raise)

    rec2 = detect_preset(words, run_state=run_state)
    assert rec1.preset == rec2.preset
    assert rec1.confidence == rec2.confidence


# ---------------------------------------------------------------------------
# Scoring math
# ---------------------------------------------------------------------------


def test_merge_weights_tier1_heaviest():
    """Tier 1 has the largest weight (0.35); a top-score in Tier 1 beats the same in other tiers."""
    t1 = {k: 0.0 for k in classifiable_presets()}
    t1["interview"] = 1.0
    t2 = {k: 0.0 for k in classifiable_presets()}
    t2["tutorial"] = 1.0
    empty = {k: 0.0 for k in classifiable_presets()}
    combined = merge((empty, t1, t2, empty))
    top = top_n(combined, 1)[0]
    assert top[0] == "interview"


def test_cue_vocabulary_score_has_value_for_tutorial():
    words = _build_tutorial(duration_s=300.0)
    scores = score_by_cue_vocabulary(words)
    # "step one" / "click" / "drag" / "type" are all in tutorial's vocab
    assert scores["tutorial"] > 0.5
    top = max(scores.items(), key=lambda kv: kv[1])
    assert top[0] == "tutorial"


def test_structure_score_gates_multi_speaker_from_single_speaker_presets():
    words = _build_podcast(duration_s=1800.0)
    scores = score_by_transcript_structure(words)
    # With 4 speakers tutorial/vlog/product_demo must score 0.0 on the
    # structure tier — they're single-speaker formats.
    assert scores["tutorial"] == 0.0
    assert scores["vlog"] == 0.0
    assert scores["product_demo"] == 0.0
    assert scores["podcast"] > 0.5
