"""Auto-detect preset + target length from the scrubbed transcript.

Cascade pipeline:

  - Tier 0 (metadata, deferred): Resolve source metadata — added in
    Phase 2 of the optimization proposal.
  - Tier 1 (structure): 12 signals derived from Deepgram output alone
    (speaker turns, overlap, words/sec, question rate, pause shape,
    low-confidence cluster density, scrubber-derived filler/restart/
    dead-air rates). See :mod:`structure`.
  - Tier 2 (cue vocabulary): distinctiveness-weighted overlap against
    each preset's ``cue_vocabulary``. See :mod:`cue_vocab`.
  - Tier 3 (opening sentence): cheap LLM call — deferred to Phase 3.
  - Tier 4 (full-band LLM): the existing model classifier, now invoked
    only when Tiers 0-2 fail to converge.

Merge + confidence math lives in :mod:`scoring`. Final preset is the
argmax of the merged scores; confidence is derived from the margin
between the top two presets (independent-tier agreement), not from the
model's self-assessment.
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel, Field

from ....intelligence import llm
from ...data.presets import PRESETS, Preset, get_preset
from .._sentences import coalesce_to_sentences as _coalesce_to_sentences
from .cue_vocab import score_by_cue_vocabulary
from .metadata import score_by_metadata
from .opening import classify_opening_sentence
from .scoring import (
    PresetScores,
    is_ambiguous_band,
    is_high_confidence,
    margin_to_confidence,
    merge,
    top_n,
)
from .structure import compute_signals, score_by_transcript_structure

log = logging.getLogger("cutmaster-ai.cutmaster.auto_detect")

# Band sampling — three 60 s windows (open / middle / close) give the
# classifier enough signal separation to tell a bio-read opener from the
# body of a keynote. A single 5-minute head window couldn't.
BAND_SECONDS = 60.0

# Presets that never auto-detect — they're mode/state constructs (Tightener
# is an assembled-mode micro-scrub; Clip Hunter / Short Generator are
# multi-candidate content-agnostic tools). Exposing them in the LLM prompt
# just invites miscalls.
_NON_AUTO_PRESETS = {"tightener", "clip_hunter", "short_generator"}


class CascadeSignals(BaseModel):
    """Per-tier scores surfaced to the panel for reasoning display.

    Each tier's top-3 presets with score — the Configure screen can
    render a compact "why this preset" breakdown (Phase 4 UX hook) and
    operators can eyeball which tiers carried or missed a decision.
    """

    top1: tuple[str, float] | None = Field(
        default=None,
        description="Highest-scoring preset after the full cascade merge.",
    )
    top2: tuple[str, float] | None = Field(
        default=None,
        description="Runner-up preset after the full cascade merge.",
    )
    margin: float = Field(
        default=0.0,
        description="Gap between top1 and top2 — drives confidence.",
    )
    tiers_invoked: list[str] = Field(
        default_factory=list,
        description="Which tiers contributed signal ('tier0'..'tier4').",
    )
    elapsed_ms: int = Field(default=0, description="Wall-clock cost of the classification call.")


class _PresetRecommendationCore(BaseModel):
    """Fields the Tier 4 LLM must fill in.

    Kept separate from :class:`PresetRecommendation` because Gemini's
    response-schema validator rejects the ``tuple[...]`` fields on
    :class:`CascadeSignals` (prefix-item schemas are not allowed in
    google-genai's JSON Schema subset). Splitting the telemetry field
    out lets the LLM see a schema it actually supports.
    """

    preset: Preset = Field(description="Recommended preset key.")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="1–2 sentences on why this preset fits.")
    suggested_target_length_s: int | None = Field(
        default=None,
        description=(
            "A sensible default target length in seconds the Configure "
            "screen should prefill. Null when no signal exists to guess."
        ),
    )
    alternatives: list[Preset] = Field(
        default_factory=list,
        description=(
            "Up to 2 runner-up presets the editor should consider when "
            "confidence is low. Empty when the top pick is confident."
        ),
    )


class PresetRecommendation(_PresetRecommendationCore):
    signals: CascadeSignals | None = Field(
        default=None,
        description=(
            "Cascade telemetry — top-1 / top-2 / margin / tiers invoked "
            "/ elapsed. Populated by the local cascade; left null by "
            "third-party callers that build recommendations directly."
        ),
    )


# ---------------------------------------------------------------------------
# Deterministic signals
# ---------------------------------------------------------------------------


def _first_sentence_text(transcript: list[dict]) -> str:
    """Return the first coalesced sentence's text, or "" when unavailable."""
    if not transcript:
        return ""
    sentences = _coalesce_to_sentences(transcript[: min(len(transcript), 80)])
    if not sentences:
        return ""
    return str(sentences[0].get("text", "")).strip()


def _duration_s(transcript: list[dict]) -> float:
    if not transcript:
        return 0.0
    return float(transcript[-1].get("end_time", 0.0))


def _speaker_turn_count(transcript: list[dict]) -> int:
    """Number of times the active speaker changes across the transcript.

    A monologue talk has O(1) turns; a true two-speaker interview has
    O(100); a multi-guest podcast has more. Better signal than raw
    speaker count, which a short bio intro can inflate.
    """
    last = None
    count = 0
    for w in transcript:
        s = w.get("speaker_id")
        if s != last:
            if last is not None:
                count += 1
            last = s
    return count


def _suggested_target_length(preset_key: str, duration_s: float) -> int | None:
    """Suggest a default target length given preset pacing + source length.

    Rule of thumb: take the smaller of (~7 % of source) or (8 × preset's
    ``target_segment_s``), clamped to the preset's plausible output range.
    Short-form and mode presets get ``None`` (the Configure screen shows
    these with their own per-preset inputs).
    """
    if preset_key in _NON_AUTO_PRESETS or preset_key == "auto":
        return None
    try:
        preset = get_preset(preset_key)
    except KeyError:
        return None
    if duration_s <= 0:
        return None
    by_ratio = duration_s * 0.07
    by_pacing = preset.target_segment_s * 8.0
    suggested = min(by_ratio, by_pacing)
    # Clamp to a reasonable band: ≥ 45 s (sub-minute cuts land in Short
    # Generator's lane) and ≤ 10 min (anything longer should come from the
    # editor explicitly, not a guess).
    suggested = max(45.0, min(600.0, suggested))
    return int(round(suggested / 15.0) * 15)  # round to nearest 15 s


# ---------------------------------------------------------------------------
# Heuristic override
# ---------------------------------------------------------------------------


def _heuristic_preset(
    duration_s: float, turns: int, n_speakers: int
) -> PresetRecommendation | None:
    """Try to classify without calling the LLM. Returns None when ambiguous.

    Only fires on high-confidence cases — anything else falls through to
    the model. Kept intentionally narrow: wrong heuristic is worse than a
    slow heuristic.
    """
    if duration_s <= 0:
        return None

    # Monologue on a long-form timeline → presentation or tutorial.
    # Tutorials use step cues the model is better at — defer there.
    # Presentation wins when the talk is genuinely long and turns are sparse.
    if n_speakers == 1 and duration_s >= 600 and turns <= 2:
        target = _suggested_target_length("presentation", duration_s)
        return PresetRecommendation(
            preset="presentation",
            confidence=0.85,
            reasoning=(
                f"Single speaker across {duration_s / 60:.0f} min with no speaker "
                f"changes — matches keynote/conference-talk cadence."
            ),
            suggested_target_length_s=target,
            alternatives=["tutorial"],
        )

    # Clean two-speaker back-and-forth → interview.
    if n_speakers == 2 and turns >= 20:
        target = _suggested_target_length("interview", duration_s)
        return PresetRecommendation(
            preset="interview",
            confidence=0.85,
            reasoning=(
                f"Two speakers exchanging across {turns} turns — classic interview pattern."
            ),
            suggested_target_length_s=target,
            alternatives=["podcast"],
        )

    # Multi-voice long-form → podcast.
    if n_speakers >= 3 and duration_s >= 900:
        target = _suggested_target_length("podcast", duration_s)
        return PresetRecommendation(
            preset="podcast",
            confidence=0.85,
            reasoning=(
                f"{n_speakers} distinct speakers across {duration_s / 60:.0f} min — "
                "multi-guest podcast pattern."
            ),
            suggested_target_length_s=target,
            alternatives=["interview"],
        )

    return None


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------


def _band(transcript: list[dict], start_s: float, end_s: float) -> list[dict]:
    window = [w for w in transcript if start_s <= float(w.get("start_time", 0.0)) < end_s]
    if not window:
        return []
    sentences = _coalesce_to_sentences(window)
    # Drop the per-word fidelity the classifier doesn't need — keep time
    # range, speaker, text.
    return [
        {
            "spk": s["spk"],
            "t": s["t"],
            "text": s["text"],
        }
        for s in sentences
    ]


def _signals_summary_block(
    combined: PresetScores | None,
    per_tier: dict[str, PresetScores] | None,
) -> str:
    """Render a top-3 preset ranking with per-tier evidence for the LLM.

    Gives the model explicit numbers to justify (or override) its pick
    instead of re-classifying blind. Empty when we have no scores yet —
    callers constructing prompts outside the cascade (tests) still work.
    """
    if not combined:
        return ""
    ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:3]
    if not ranked:
        return ""

    per_tier = per_tier or {}
    has_tier3 = any((per_tier.get("tier3") or {}).get(k, 0.0) > 0 for k, _ in ranked)
    header_tiers = "0-3" if has_tier3 else "0-2"
    lines = [f"SIGNALS SUMMARY (Tier {header_tiers} cascade scores — higher = stronger fit):"]
    for key, score in ranked:
        t0 = (per_tier.get("tier0") or {}).get(key, 0.0)
        t1 = (per_tier.get("tier1") or {}).get(key, 0.0)
        t2 = (per_tier.get("tier2") or {}).get(key, 0.0)
        row = (
            f"  - {key}: combined {score:.2f} (metadata {t0:.2f}, structure {t1:.2f}, cues {t2:.2f}"
        )
        if has_tier3:
            t3 = (per_tier.get("tier3") or {}).get(key, 0.0)
            row += f", opener {t3:.2f}"
        row += ")"
        lines.append(row)
    lines.append("")
    lines.append(
        "Your pick must be one of the three above. Justify against these "
        "numbers in `reasoning`; override only when the transcript evidence "
        "is decisive."
    )
    return "\n".join(lines)


def _prompt(
    transcript: list[dict],
    duration_s: float,
    n_speakers: int,
    turns: int,
    allowed: list[str] | None = None,
    *,
    combined: PresetScores | None = None,
    per_tier: dict[str, PresetScores] | None = None,
) -> str:
    allowed_set = set(allowed) if allowed else None
    choices = "\n".join(
        f"  - {key}: {bundle.label} — {bundle.role}"
        for key, bundle in PRESETS.items()
        if key not in _NON_AUTO_PRESETS and (allowed_set is None or key in allowed_set)
    )

    bands: dict[str, list[dict]] = {"open": _band(transcript, 0.0, BAND_SECONDS)}
    if duration_s > BAND_SECONDS * 3:
        mid = max(BAND_SECONDS, duration_s / 2 - BAND_SECONDS / 2)
        bands["middle"] = _band(transcript, mid, mid + BAND_SECONDS)
        bands["close"] = _band(transcript, max(0.0, duration_s - BAND_SECONDS), duration_s)

    band_dump = "\n".join(
        f"  {name} band ({len(rows)} sentences):\n  " + json.dumps(rows, separators=(",", ":"))
        for name, rows in bands.items()
    )

    signals_block = _signals_summary_block(combined, per_tier)
    signals_section = f"\n{signals_block}\n" if signals_block else ""

    return f"""Classify this transcript into ONE of the content-type presets below. The recommendation will be shown to the user as a suggestion — they can override, so your calibrated confidence matters more than being right every time.

PRESETS (pick exactly one key):
{choices}

METADATA:
  total_duration_s: {duration_s:.0f}
  total_duration_min: {duration_s / 60:.1f}
  speaker_count: {n_speakers}
  speaker_turn_count: {turns}
{signals_section}
TRANSCRIPT BANDS (sentence-coalesced — three 60 s windows unless the piece is too short):
{band_dump}

Return a `PresetRecommendation` with:
- `preset`: one of {sorted(allowed_set) if allowed_set else sorted(k for k in PRESETS if k not in _NON_AUTO_PRESETS)}.
- `confidence`: 0.0–1.0. Use ≥0.8 only when the content type is unambiguous.
- `reasoning`: 1–2 sentences.
- `suggested_target_length_s`: a sensible default target length in seconds the user could use without further thought. Consider source duration and the preset's pacing. Null only when the preset has no natural default.
- `alternatives`: up to 2 runner-up preset keys (ordered most-to-least plausible) when your confidence is below 0.5. Leave empty when confident.

Tie-breakers:
- One speaker with no speaker changes across 10+ minutes → usually presentation.
- Two speakers with 20+ turns → usually interview.
- Three or more speakers across 15+ minutes → usually podcast.
- Short (<3 min), energetic, single speaker → likely vlog or reaction.
- Step-by-step instruction with UI/tool cues → tutorial.
"""


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _cascade_reasoning(
    top: tuple[str, float],
    second: tuple[str, float],
    signals: dict,
    path: str,
) -> str:
    """One-line human summary of how the cascade picked this preset."""
    margin = top[1] - second[1]
    dur_min = signals.get("duration_s", 0.0) / 60.0
    return (
        f"Cascade path: {path}. Top: {top[0]} ({top[1]:.2f}) over {second[0]} "
        f"({second[1]:.2f}), margin {margin:.2f}. Source: {dur_min:.1f} min, "
        f"{signals.get('speaker_count', 0)} speaker(s), "
        f"{signals.get('speaker_turn_count', 0)} turns."
    )


def _alternatives_for(combined: PresetScores, chosen: str, high_conf: bool) -> list[Preset]:
    """Up to 2 runner-up presets. Empty when confidence is high."""
    if high_conf:
        return []
    alts: list[Preset] = []
    for key, _ in top_n(combined, n=3):
        if key == chosen:
            continue
        if key in _NON_AUTO_PRESETS:
            continue
        alts.append(key)  # type: ignore[arg-type]
        if len(alts) == 2:
            break
    return alts


def _guard_preset(key: str) -> Preset:
    """Return ``key`` as a ``Preset`` literal, defaulting to vlog if invalid."""
    if key in PRESETS and key not in _NON_AUTO_PRESETS:
        return key  # type: ignore[return-value]
    return "vlog"


def _llm_classify(
    transcript: list[dict],
    duration_s: float,
    n_speakers: int,
    turns: int,
    signals: dict,
    candidates: list[str],
    *,
    combined: PresetScores | None = None,
    per_tier: dict[str, PresetScores] | None = None,
) -> PresetRecommendation:
    """Tier 4 — full-band LLM over the narrowed candidate set.

    Restricts the exposed preset list to the cascade's top-3 candidates
    and prepends a SIGNALS SUMMARY block showing each candidate's
    per-tier scores so the model has to justify its pick against
    objective evidence.
    """
    prompt = _prompt(
        transcript,
        duration_s,
        n_speakers,
        turns,
        allowed=candidates,
        combined=combined,
        per_tier=per_tier,
    )
    rec = llm.call_structured(
        agent="autodetect",
        prompt=prompt,
        response_schema=_PresetRecommendationCore,
        temperature=0.2,
    )
    if rec.preset not in PRESETS or rec.preset in _NON_AUTO_PRESETS:
        return PresetRecommendation(
            preset=_guard_preset(candidates[0] if candidates else "vlog"),
            confidence=0.3,
            reasoning=f"(model returned unknown preset '{rec.preset}' — defaulted to top cascade candidate)",
            suggested_target_length_s=_suggested_target_length(candidates[0], duration_s)
            if candidates
            else None,
            alternatives=[],
        )
    if rec.suggested_target_length_s is None:
        rec = rec.model_copy(
            update={"suggested_target_length_s": _suggested_target_length(rec.preset, duration_s)}
        )
    if rec.alternatives:
        filtered = [a for a in rec.alternatives if a in PRESETS and a not in _NON_AUTO_PRESETS]
        rec = rec.model_copy(update={"alternatives": filtered[:2]})
    # Lift to the full shape so callers can attach CascadeSignals.
    return PresetRecommendation.model_validate(rec.model_dump())


def _tiers_invoked_list(run_state: dict | None, tier3_fired: bool, tier4_fired: bool) -> list[str]:
    """Names of tiers that ran during this classification pass."""
    invoked = ["tier1", "tier2"]
    if run_state is not None:
        invoked.insert(0, "tier0")
    if tier3_fired:
        invoked.append("tier3")
    if tier4_fired:
        invoked.append("tier4")
    return invoked


def _attach_signals(
    rec: PresetRecommendation,
    top: tuple[str, float],
    second: tuple[str, float],
    margin: float,
    *,
    tiers_invoked: list[str],
    elapsed_ms: int,
) -> PresetRecommendation:
    """Return a copy of ``rec`` with a populated :class:`CascadeSignals` field."""
    sig = CascadeSignals(
        top1=(top[0], round(float(top[1]), 4)),
        top2=(second[0], round(float(second[1]), 4)),
        margin=round(float(margin), 4),
        tiers_invoked=list(tiers_invoked),
        elapsed_ms=elapsed_ms,
    )
    return rec.model_copy(update={"signals": sig})


def _log_cascade(rec: PresetRecommendation, path: str) -> None:
    """Emit the structured ``autodetect.cascade`` telemetry line.

    Name + ``extra`` payload match the pattern the intelligence.llm
    module uses so downstream log aggregators can correlate classifier
    picks with model calls.
    """
    sig = rec.signals
    if sig is None:
        return
    log.info(
        "autodetect.cascade path=%s pick=%s conf=%.2f margin=%.3f tiers=%s elapsed_ms=%d",
        path,
        rec.preset,
        rec.confidence,
        sig.margin,
        ",".join(sig.tiers_invoked),
        sig.elapsed_ms,
        extra={
            "autodetect_cascade": {
                "path": path,
                "pick": rec.preset,
                "confidence": rec.confidence,
                "top1": sig.top1,
                "top2": sig.top2,
                "margin": sig.margin,
                "tiers_invoked": sig.tiers_invoked,
                "elapsed_ms": sig.elapsed_ms,
            }
        },
    )


def detect_preset(
    transcript: list[dict],
    run_state: dict | None = None,
) -> PresetRecommendation:
    """Classify a transcript into a preset recommendation via the cascade.

    Accepts an optional ``run_state`` so Tiers 0 (metadata, Phase 2) and
    the result cache (``run["autodetect_signals"]``) can participate. The
    function is safe to call with just ``transcript`` — Tier 0 simply
    contributes neutral zeros.
    """
    # Re-entry cache — mirrors the themes_cache pattern.
    if run_state is not None:
        cached = run_state.get("autodetect_signals")
        if cached and "recommendation" in cached:
            try:
                return PresetRecommendation.model_validate(cached["recommendation"])
            except Exception:
                pass  # fall through to recompute

    started = time.monotonic()
    duration_s = _duration_s(transcript)
    scrub_counts = (run_state or {}).get("scrub_counts")

    from .scoring import empty_scores

    t0 = score_by_metadata(run_state) if run_state else empty_scores()
    t1 = score_by_transcript_structure(transcript, scrub_counts=scrub_counts)
    t2 = score_by_cue_vocabulary(transcript)
    t3: PresetScores = empty_scores()
    tier3_fired = False
    tier4_fired = False

    signals = compute_signals(transcript, scrub_counts)
    combined = merge((t0, t1, t2, t3))

    ranked = top_n(combined, n=3)
    if not ranked or ranked[0][1] <= 0:
        return PresetRecommendation(
            preset="vlog",
            confidence=0.0,
            reasoning="Transcript empty or uninformative — defaulted to vlog.",
            suggested_target_length_s=None,
            alternatives=[],
            signals=CascadeSignals(
                top1=None,
                top2=None,
                margin=0.0,
                tiers_invoked=[],
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ),
        )

    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else (top[0], 0.0)
    margin = top[1] - second[1]

    # Tier 3 — opening-sentence micro-classifier. Only fires in the
    # ambiguous band [0.1, 0.25): confident picks don't need it, and
    # very low margins should defer to Tier 4's full-band view instead.
    if is_ambiguous_band(margin):
        opening = _first_sentence_text(transcript)
        if opening:
            log.info(
                "autodetect: margin %.2f ambiguous — invoking Tier 3 opening-sentence classifier",
                margin,
            )
            t3 = classify_opening_sentence(opening)
            tier3_fired = any(v > 0 for v in t3.values())
            combined = merge((t0, t1, t2, t3))
            ranked = top_n(combined, n=3)
            top = ranked[0]
            second = ranked[1] if len(ranked) > 1 else (top[0], 0.0)
            margin = top[1] - second[1]

    n_speakers = signals["speaker_count"]
    turns = signals["speaker_turn_count"]

    if is_high_confidence(margin):
        chosen = _guard_preset(top[0])
        rec = PresetRecommendation(
            preset=chosen,
            confidence=margin_to_confidence(margin),
            reasoning=_cascade_reasoning(
                top,
                second,
                signals,
                path="tiers 0-3" if tier3_fired else "tiers 0-2",
            ),
            suggested_target_length_s=_suggested_target_length(chosen, duration_s),
            alternatives=_alternatives_for(combined, chosen, high_conf=True),
        )
        rec = _attach_signals(
            rec,
            top,
            second,
            margin,
            tiers_invoked=_tiers_invoked_list(run_state, tier3_fired, tier4_fired=False),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        _cache_recommendation(run_state, signals, t0, t1, t2, t3, rec)
        _log_cascade(rec, "cascade-only")
        return rec

    # Tier 4 — narrow candidate set, let the LLM pick.
    candidates = [k for k, _ in ranked if k not in _NON_AUTO_PRESETS][:3]
    log.info(
        "autodetect: margin %.2f below high-confidence threshold — invoking LLM over %s",
        margin,
        candidates,
    )
    tier4_fired = True
    try:
        rec = _llm_classify(
            transcript,
            duration_s,
            n_speakers,
            turns,
            signals,
            candidates,
            combined=combined,
            per_tier={"tier0": t0, "tier1": t1, "tier2": t2, "tier3": t3},
        )
    except Exception as exc:
        log.warning("autodetect LLM tier failed (%s) — falling back to cascade top-1", exc)
        chosen = _guard_preset(top[0])
        rec = PresetRecommendation(
            preset=chosen,
            confidence=margin_to_confidence(margin),
            reasoning=_cascade_reasoning(
                top,
                second,
                signals,
                path=(
                    "tiers 0-3 (LLM fallback failed)"
                    if tier3_fired
                    else "tiers 0-2 (LLM fallback failed)"
                ),
            ),
            suggested_target_length_s=_suggested_target_length(chosen, duration_s),
            alternatives=_alternatives_for(combined, chosen, high_conf=False),
        )

    rec = _attach_signals(
        rec,
        top,
        second,
        margin,
        tiers_invoked=_tiers_invoked_list(run_state, tier3_fired, tier4_fired),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    _cache_recommendation(run_state, signals, t0, t1, t2, t3, rec)
    _log_cascade(rec, "tier4")
    return rec


def _cache_recommendation(
    run_state: dict | None,
    signals: dict,
    t0: PresetScores,
    t1: PresetScores,
    t2: PresetScores,
    t3: PresetScores,
    rec: PresetRecommendation,
) -> None:
    """Store the cascade outputs on run state so re-entry is free.

    Mirrors the themes_cache pattern in [presets route]. The panel hitting
    ``/detect-preset`` twice in a row (Configure re-entry) now returns
    instantly instead of recomputing signals + scores.
    """
    if run_state is None:
        return
    run_state["autodetect_signals"] = {
        "signals": signals,
        "tier0": t0,
        "tier1": t1,
        "tier2": t2,
        "tier3": t3,
        "recommendation": rec.model_dump(),
    }
    # Persist only if the run has an id — some test harnesses pass a
    # plain dict without the state machinery.
    run_id = run_state.get("run_id")
    if run_id:
        from ...core import state

        try:
            state.save(run_state)
        except Exception as exc:
            log.debug("autodetect cache persistence skipped: %s", exc)
