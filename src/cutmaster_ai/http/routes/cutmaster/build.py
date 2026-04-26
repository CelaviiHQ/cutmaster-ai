"""POST /build-plan — the Director + Marker + source-frame-resolution pipeline.

Branches by preset + timeline_mode:
  clip_hunter → N candidate clips, no marker agent
  tightener   → aggressive re-scrub + per-take segments, no Director
  assembled   → take-aware Director (no cross-take cuts)
  raw_dump    → word-level Director (v1 default)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from fastapi import APIRouter, HTTPException

from ....cutmaster.analysis.boundary_validator import (
    build_boundary_samples,
    build_short_generator_boundary_samples,
)
from ....cutmaster.analysis.marker_agent import MarkerPlan, suggest_markers
from ....cutmaster.analysis.scrubber import ScrubParams, scrub
from ....cutmaster.analysis.tightener import (
    DEFAULT_BLOCK_GAP_S,
    build_tightener_segments,
    tightener_stats,
)
from ....cutmaster.core import director as director_mod
from ....cutmaster.core import pipeline, state
from ....cutmaster.core.director import (
    CutSegment,
    DirectorPlan,
    build_assembled_cut_plan,
    build_clip_hunter_plan,
    build_curated_cut_plan,
    build_cut_plan,
    build_rough_cut_plan,
    build_short_generator_plan,
    candidate_to_segments,
    expand_assembled_plan,
    expand_curated_plan,
    short_candidate_to_segments,
)
from ....cutmaster.core.timeouts import (
    DIRECTOR_TIMEOUT_S,
    MARKER_TIMEOUT_S,
    with_timeout,
)
from ....cutmaster.core.validator_loop import (
    BoundaryValidationResult,
    run_with_boundary_validation,
)
from ....cutmaster.data.axis_compat import (
    cut_intent_mode_incompatibility_reason,
)
from ....cutmaster.data.axis_resolution import (
    ResolvedAxes,
)
from ....cutmaster.data.presets import (
    PRESETS,
    get_preset,
    preset_mode_compatible,
    preset_mode_incompatibility_reason,
)
from ....cutmaster.resolve_ops.assembled import (
    build_take_entries,
    read_items_on_track,
    split_transcript_per_item,
)
from ....cutmaster.resolve_ops.groups import (
    DEFAULT_SIMILARITY_THRESHOLD,
    all_singletons,
    detect_groups,
    read_items_with_grouping_signals,
    to_item_summary,
)
from ....cutmaster.resolve_ops.segments import resolve_segments
from ._helpers import _dump_director_prompt, _require_scrubbed
from ._models import BuildPlanRequest
from ._sensory_gates import (
    layer_a_enabled as _gate_layer_a_enabled,
)
from ._sensory_gates import (
    log_sensory_resolution,
)

log = logging.getLogger("cutmaster-ai.http.cutmaster")

router = APIRouter()


# ---------------------------------------------------------------------------
# Phase 4.5 + 4.6 — three-axis compat check + resolved-axes plumbing
# ---------------------------------------------------------------------------


# Legacy preset keys that pre-decide the cut intent. Mirrors
# ``_LEGACY_CUT_INTENT_PRESETS`` in ``_models.py`` but lives here so the
# build handler can run the axis-compat guard without an import-cycle.
_PRESET_TO_CUT_INTENT: dict[str, str] = {
    "tightener": "surgical_tighten",
    "clip_hunter": "multi_clip",
    "short_generator": "assembled_short",
}


def _effective_cut_intent(body: BuildPlanRequest) -> str | None:
    """Derive the cut intent for compatibility / axis resolution.

    ``UserSettings.cut_intent`` wins when set (new-API callers). Otherwise
    legacy cut-intent presets map to their matching intent; content-type
    presets leave it ``None`` so ``resolve_axes`` can auto-pick.
    """
    explicit = body.user_settings.cut_intent
    if explicit is not None:
        return explicit
    return _PRESET_TO_CUT_INTENT.get(body.preset)


def _transcript_duration_s(scrubbed: list[dict]) -> float:
    """Return the last word's ``end_time`` — used as ``duration_s`` input."""
    if not scrubbed:
        return 0.0
    try:
        return float(scrubbed[-1].get("end_time", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _effective_content_type(body: BuildPlanRequest) -> str | None:
    """Prefer the new ``content_type`` field; fall back to remapping the preset."""
    if body.content_type is not None:
        return body.content_type
    # ``body.preset`` is always a legacy key post-validation (cut-intent
    # presets map to auto_detect, content-type presets map to themselves).
    # Return the raw preset name when it's a content-type key; None otherwise
    # so the resolver skips (cascade must already have run during analyze).
    from ._models import _LEGACY_CONTENT_TYPE_PRESETS

    if body.preset in _LEGACY_CONTENT_TYPE_PRESETS:
        return body.preset
    return None


# Per-layer activation flows through :func:`resolve_sensory_layers_by_axes`
# (via :mod:`_sensory_gates`) so the matrix in ``data/presets.py`` stays
# the single source of truth. Clip Hunter's Layer-A entry is "off" in the
# matrix — each candidate is one span with no internal transitions to
# validate — so the resolver returns False there regardless of master.
# Assembled is similarly gated off. Short Generator (preset, not mode)
# and linear modes (raw_dump / rough_cut / curated) share the same
# resolver path.


def _layer_a_enabled(settings: dict, *, cut_intent: str, timeline_mode: str) -> bool:
    """Whether the outer boundary-validator loop should wrap this run.

    Explicit ``layer_a_enabled`` override wins (tri-state: True / False /
    None-means-defer). Otherwise the matrix × master toggle resolves the
    effective flag. When neither is set, the Director runs unwrapped and
    the build path is byte-identical to v3. One helper covers every path
    (linear modes + multi-candidate presets) — the axis-keyed resolver
    handles the row collapse uniformly via ``axes_to_sensory_key``.
    """
    return _gate_layer_a_enabled(settings, cut_intent=cut_intent, timeline_mode=timeline_mode)


def _cut_intent_for(
    body: BuildPlanRequest,
    resolved_axes: ResolvedAxes | None,
) -> str:
    """Best-available cut intent for sensory-gate lookups.

    ``resolved_axes`` is the canonical source once axis resolution has
    run. Before that (or if it was skipped), ``_effective_cut_intent``
    handles the legacy preset → intent collapse. Final fallback is
    ``"narrative"`` — same default the matrix uses for unknown intents.
    """
    if resolved_axes is not None:
        return resolved_axes.cut_intent
    explicit = _effective_cut_intent(body)
    return explicit if explicit is not None else "narrative"


async def _director_or_validated(
    *,
    mode: str,
    cut_intent: str,
    settings: dict,
    base_call,
    get_selected_clips,
    tl,
    project,
    video_track: int = 1,
):
    """Invoke the Director; when Layer A is active, wrap with the retry loop.

    ``base_call`` is an awaitable taking the effective settings dict and
    returning a plan. ``get_selected_clips(plan)`` extracts the CutSegment
    list the validator compares frame pairs on — different for flat plans
    (``plan.selected_clips``) vs. curated/rough-cut plans (which need
    ``expand_curated_plan`` first; the caller handles that in the closure).

    ``cut_intent`` and ``mode`` together key into the sensory matrix so
    Layer A activation is symmetric with the resolver's ``(cut_intent,
    timeline_mode)`` contract.

    Returns ``(plan, BoundaryValidationResult | None)``. Result is ``None``
    when Layer A is off so callers can skip the warnings surface.
    """
    if not _layer_a_enabled(settings, cut_intent=cut_intent, timeline_mode=mode):
        plan = await base_call(settings)
        return plan, None

    async def _director_fn(rejections, roster):
        effective = dict(settings)
        if rejections:
            effective["_boundary_rejections"] = rejections
        if roster:
            effective["_candidate_roster"] = roster
        return await base_call(effective)

    def _build_samples(plan):
        try:
            segments = get_selected_clips(plan)
        except Exception as exc:
            log.info("layer A: get_selected_clips raised (%s) — skipping validator", exc)
            return []
        return build_boundary_samples(tl, segments, project=project, video_track=video_track)

    # Linear plans have no candidate roster — omitting
    # extract_candidate_roster keeps the loop in single-plan mode.
    result = await run_with_boundary_validation(
        director_fn=_director_fn,
        build_samples=_build_samples,
    )
    return result.plan, result


def _plan_warnings(*plans) -> list[dict]:
    """Extract ``_validation_errors`` from any ``llm.call_structured`` result
    and translate each into a structured, vlogger-friendly warning.

    When the Director (or any agent invoked with ``accept_best_effort=True``)
    exhausts its retry budget while still failing validation, ``llm.py``
    stamps the offending plan object with a ``_validation_errors`` list and
    returns it anyway. Without this helper those silent failures would only
    surface in server logs — the panel would render the best-of-bad plan as
    if everything succeeded.

    The raw validator strings (``"segment[2]: starts on low-confidence word
    'This' (conf 0.56 < 0.60)…"``) are written for the Director's retry
    prompt, not for non-technical editors. This helper regex-translates
    them into ``{kind, title, detail, action?}`` records so the panel can
    render plain English with optional inline actions instead of dumping
    the raw string. Walks every supplied plan, dedupes by raw string,
    returns ``[]`` when no plan carries warnings.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for p in plans:
        if p is None:
            continue
        errors = getattr(p, "_validation_errors", None) or []
        for e in errors:
            if e in seen:
                continue
            seen.add(e)
            out.append(_humanise_validator_warning(e))
    return out


# Compiled once — these patterns are stable contracts with the Director's
# validator strings (see cutmaster.core.director.validate_plan). If a
# validator message is reworded, the matching branch falls back to
# ``kind="other"`` and the original string surfaces verbatim, so the
# editor still sees something useful.
_RE_LOW_CONF_START = re.compile(r"segment\[(\d+)\]: starts on low-confidence word '([^']*)'")
_RE_LOW_CONF_END = re.compile(r"segment\[(\d+)\]: ends on low-confidence word '([^']*)'")
_RE_COVERAGE = re.compile(
    r"per-clip coverage: touched (\d+)/(\d+) units \((\d+)%, threshold (\d+)%\)"
)
_RE_FLOOR = re.compile(r"segment\[(\d+)\]: ([\d.]+)s is under the ([\d.]+)s pacing floor")
_RE_CEILING = re.compile(r"segment\[(\d+)\]: ([\d.]+)s exceeds the ([\d.]+)s pacing ceiling")
_RE_DUPLICATE_TAKES = re.compile(
    r"take-group \d+: plan uses clip_index \[([\d, ]+)\] from the same duplicate-take group"
)


def _humanise_validator_warning(raw: str) -> dict:
    """Map a raw validator string to a structured panel warning.

    Returns ``{kind, title, detail, action?}`` where:
      - ``kind`` is a stable enum the frontend can branch on
      - ``title`` is a short vlogger-language headline (~5-8 words)
      - ``detail`` is one sentence explaining what happened
      - ``action`` (optional) is ``{label, kind, payload?}`` describing
        an inline button the panel renders. ``action.kind`` values:
          - ``configure_hook`` — return to Configure with the hook field
          - ``configure_target_length`` — return to Configure
          - ``regenerate`` — trigger a fresh build (cheap nudge for
            non-deterministic situations)

    Always returns a record. Unknown shapes fall through to
    ``{kind: "other", title: "Heads up", detail: <raw>}`` so the editor
    still sees the validator's words rather than an empty alert.
    """
    if m := _RE_LOW_CONF_START.search(raw):
        seg_one_based = int(m.group(1)) + 1
        word = m.group(2)
        return {
            "kind": "low_confidence_hook",
            "title": "Your hook starts mid-word",
            "detail": (
                f"Segment {seg_one_based} begins on the word “{word}” "
                "— the AI wasn't fully sure where that word started, so the "
                "cut may begin a beat later than you intended."
            ),
            "action": {
                "label": "Pick a different hook moment",
                "kind": "configure_hook",
            },
            "raw": raw,
        }
    if m := _RE_LOW_CONF_END.search(raw):
        seg_one_based = int(m.group(1)) + 1
        word = m.group(2)
        return {
            "kind": "low_confidence_end",
            "title": "A segment ends mid-word",
            "detail": (
                f"Segment {seg_one_based} ends on “{word}” "
                "— the transcription was unclear here, so the segment "
                "may cut a beat earlier than expected."
            ),
            "raw": raw,
        }
    if m := _RE_COVERAGE.search(raw):
        used, total, _ratio, _threshold = (
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4)),
        )
        missing = total - used
        return {
            "kind": "low_coverage",
            "title": (
                f"{missing} of your clip{'s' if missing != 1 else ''} "
                f"{'were' if missing != 1 else 'was'} left out"
            ),
            "detail": (
                f"The AI used {used} of your {total} clips. Some footage "
                "you placed on the timeline may be missing from this cut."
            ),
            "action": {
                "label": "Try Regenerate",
                "kind": "regenerate",
            },
            "raw": raw,
        }
    if m := _RE_FLOOR.search(raw):
        seg_one_based = int(m.group(1)) + 1
        actual = float(m.group(2))
        floor = float(m.group(3))
        return {
            "kind": "segment_too_short",
            "title": "A segment is shorter than the preset prefers",
            "detail": (
                f"Segment {seg_one_based} runs {actual:.1f}s — under the "
                f"{floor:.0f}s pacing floor for this preset. The cut may "
                "feel rushed at this beat."
            ),
            "action": {
                "label": "Adjust target length",
                "kind": "configure_target_length",
            },
            "raw": raw,
        }
    if m := _RE_CEILING.search(raw):
        seg_one_based = int(m.group(1)) + 1
        actual = float(m.group(2))
        ceiling = float(m.group(3))
        return {
            "kind": "segment_too_long",
            "title": "A segment is longer than the preset prefers",
            "detail": (
                f"Segment {seg_one_based} runs {actual:.1f}s — over the "
                f"{ceiling:.0f}s pacing ceiling for this preset. The cut "
                "may drag at this beat."
            ),
            "action": {
                "label": "Adjust target length",
                "kind": "configure_target_length",
            },
            "raw": raw,
        }
    if _RE_DUPLICATE_TAKES.search(raw):
        return {
            "kind": "duplicate_takes",
            "title": "Two takes of the same line ended up in the cut",
            "detail": (
                "The AI picked more than one clip from a group it identified "
                "as duplicate takes. You may hear the same line twice."
            ),
            "action": {
                "label": "Try Regenerate",
                "kind": "regenerate",
            },
            "raw": raw,
        }
    return {
        "kind": "other",
        "title": "Heads up",
        "detail": raw,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Phase 2 of story-critic — flag-gated coherence pass
# ---------------------------------------------------------------------------


def _story_critic_enabled() -> bool:
    """Truthy values: 1 / true / yes / on (case-insensitive)."""
    raw = os.environ.get("CUTMASTER_ENABLE_STORY_CRITIC", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _wrap_coherence(report) -> dict:
    """Wrap a critic report with its kind tag for the panel to branch on.

    Per Implementation/optimizaiton/story-critic.md §1.2a — single-cut
    shapes get ``{"kind": "single", ...}``; per-candidate shapes get
    ``{"kind": "per_candidate", ...}``. The shape on disk is what the
    Review screen reads.
    """
    from ....intelligence.story_critic import PerCandidateCoherenceReport

    kind = "per_candidate" if isinstance(report, PerCandidateCoherenceReport) else "single"
    return {"kind": kind, "report": report.model_dump()}


def _emit_skipped(run_id: str, reason: str, *, level: int = logging.INFO, **extra) -> None:
    """Structured ``story_critic.skipped`` log.

    ``reason`` is one of: ``flag_off`` / ``no_axes`` / ``llm_error`` /
    ``unsupported_plan_shape``. Extra kwargs are stamped on the LogRecord
    so downstream telemetry consumers can filter without parsing the
    message string. Mirrors the ``axis_resolution.decided`` log shape
    from Phase 6.3 of the three-axis model.
    """
    payload = {"event": "story_critic.skipped", "run_id": run_id, "reason": reason, **extra}
    log.log(
        level,
        "story_critic.skipped reason=%s run_id=%s",
        reason,
        run_id,
        extra=payload,
    )


def _emit_completed(run_id: str, report, axes, model: str, latency_ms: int) -> None:
    """Structured ``story_critic.completed`` log carrying the full payload.

    Phase 4.1 widens the shape so a quality dashboard can be built without
    re-instrumenting the call site. Per-candidate reports log aggregates
    (n_candidates / best_index / mean_score) — per-candidate score
    histograms can live downstream.
    """
    from ....intelligence.story_critic import CoherenceReport

    base = {
        "event": "story_critic.completed",
        "run_id": run_id,
        "content_type": axes.content_type if axes else None,
        "cut_intent": axes.cut_intent if axes else None,
        "model": model,
        "latency_ms": latency_ms,
    }
    if isinstance(report, CoherenceReport):
        payload = {
            **base,
            "kind": "single",
            "score": report.score,
            "hook_strength": report.hook_strength,
            "arc_clarity": report.arc_clarity,
            "transitions": report.transitions,
            "resolution": report.resolution,
            "n_issues": len(report.issues),
            "verdict": report.verdict,
        }
        log.info(
            "story_critic.completed run_id=%s score=%d verdict=%s n_issues=%d latency_ms=%d",
            run_id,
            report.score,
            report.verdict,
            len(report.issues),
            latency_ms,
            extra=payload,
        )
    else:
        scores = [c.score for c in report.candidates] or [0]
        payload = {
            **base,
            "kind": "per_candidate",
            "n_candidates": len(report.candidates),
            "best_candidate_index": report.best_candidate_index,
            "mean_score": sum(scores) // len(scores),
            "max_score": max(scores),
            "min_score": min(scores),
        }
        log.info(
            "story_critic.completed run_id=%s n_candidates=%d best=%d max=%d latency_ms=%d",
            run_id,
            len(report.candidates),
            report.best_candidate_index,
            max(scores),
            latency_ms,
            extra=payload,
        )


def _run_critic_or_skip(
    plan,
    *,
    transcript=None,
    takes=None,
    axes,
    run_id: str,
    user_opt_in: bool | None = None,
):
    """Grade the plan if the critic is enabled AND axes are present; else skip.

    Enable precedence:
      1. ``CUTMASTER_ENABLE_STORY_CRITIC=1`` (server-wide forced-on / kill-switch)
      2. ``user_opt_in == True`` (per-build setting from the Configure screen)
      3. Otherwise skip with reason ``flag_off``.

    LLM failures never propagate — the structural plan is already valid;
    coherence is advisory. Logs:
      * ``story_critic.skipped`` on flag-off / no-axes / llm-error
      * ``story_critic.completed`` on success (Phase 4.1 widens shape).
    """
    if not (_story_critic_enabled() or user_opt_in is True):
        _emit_skipped(run_id, "flag_off")
        return None
    if axes is None:
        _emit_skipped(run_id, "no_axes")
        return None

    import time

    from ....intelligence import story_critic
    from ....intelligence.llm import model_for

    model = model_for("story_critic")
    started = time.monotonic()
    try:
        report = story_critic.critique(
            plan,
            transcript=transcript,
            takes=takes,
            axes=axes,
        )
    except Exception as exc:
        _emit_skipped(
            run_id,
            "llm_error",
            level=logging.WARNING,
            error=str(exc),
            error_type=type(exc).__name__,
            model=model,
        )
        return None

    latency_ms = int((time.monotonic() - started) * 1000)
    _emit_completed(run_id, report, axes, model, latency_ms)
    return report


# ---------------------------------------------------------------------------
# Phase 6 of story-critic — auto-rework loop
# ---------------------------------------------------------------------------
#
# When the first-pass critic verdict is `rework` or `review` (or any issue
# is severity=error), feed the critic's findings back into the Director
# prompt and re-pick. Bounded to one rework pass max so a flapping critic
# can't burn the LLM budget. Per-candidate plans (ClipHunterPlan /
# ShortGeneratorPlan) are out of scope — they're N standalone outputs, not
# one coherent narrative the rework loop knows how to redo.
#
# Engineered defaults (no calibration required):
#   - Trigger when verdict in {"rework", "review"} OR any error-severity
#   - Skip rework when verdict == "ship" (don't pay 2× to fix what's good)
#   - Hard cap 1 retry; CUTMASTER_STORY_CRITIC_REWORK_MAX overrides


_REWORK_MAX_DEFAULT = 3
_REWORK_MAX_CEILING = 5
_MIN_DELTA_DEFAULT = 3
_TOKEN_BUDGET_DEFAULT = 300_000


def _rework_max_attempts() -> int:
    """Read ``CUTMASTER_STORY_CRITIC_REWORK_MAX``.

    Default ``3`` — the iterative critic loop runs up to three rework
    iterations before shipping the best-scoring envelope from history.
    Floor clamped to ``0`` (disables rework). Ceiling clamped to ``5``
    so a runaway env override can't skip the upper bound; the previous
    parser only floored at zero, which Phase 2 of the proposal tightens.
    """
    raw = os.environ.get("CUTMASTER_STORY_CRITIC_REWORK_MAX", "").strip()
    if not raw:
        return _REWORK_MAX_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "CUTMASTER_STORY_CRITIC_REWORK_MAX not an int (%r), using %d",
            raw,
            _REWORK_MAX_DEFAULT,
        )
        return _REWORK_MAX_DEFAULT
    if value > _REWORK_MAX_CEILING:
        log.warning(
            "CUTMASTER_STORY_CRITIC_REWORK_MAX=%d exceeds ceiling %d; clamping",
            value,
            _REWORK_MAX_CEILING,
        )
        return _REWORK_MAX_CEILING
    return max(0, value)


def _rework_min_delta() -> int:
    """Read ``CUTMASTER_STORY_CRITIC_MIN_DELTA`` (default 3, clamped ≥0).

    Plateau / regression floor: when ``|delta| < MIN_DELTA`` the loop exits
    with ``reason="plateau"``; when ``delta <= -MIN_DELTA`` it exits with
    ``reason="regression"``. Both behaviours kick in at iteration ≥1.
    """
    raw = os.environ.get("CUTMASTER_STORY_CRITIC_MIN_DELTA", "").strip()
    if not raw:
        return _MIN_DELTA_DEFAULT
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning(
            "CUTMASTER_STORY_CRITIC_MIN_DELTA not an int (%r), using %d",
            raw,
            _MIN_DELTA_DEFAULT,
        )
        return _MIN_DELTA_DEFAULT


def _rework_token_budget() -> int:
    """Read ``CUTMASTER_STORY_CRITIC_TOKEN_BUDGET`` (default 300_000).

    Hard cost rail. The loop exits with ``reason="token_budget"`` once
    the per-build sum of ``in + out`` Gemini tokens (across critic +
    Director calls) crosses this ceiling. Provisional default sized for
    ≥3 iterations on top of today's ~90k single-pass baseline; Phase 0
    of the proposal calibrates against a real measurement.
    """
    raw = os.environ.get("CUTMASTER_STORY_CRITIC_TOKEN_BUDGET", "").strip()
    if not raw:
        return _TOKEN_BUDGET_DEFAULT
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning(
            "CUTMASTER_STORY_CRITIC_TOKEN_BUDGET not an int (%r), using %d",
            raw,
            _TOKEN_BUDGET_DEFAULT,
        )
        return _TOKEN_BUDGET_DEFAULT


def _read_token_usage(plan) -> int:
    """Extract and sum the ``_token_usage`` stash put on the parsed plan
    by ``call_structured``. Returns 0 when the attribute is missing
    (e.g. a stub Director used by tests, or a plan that bypassed
    ``call_structured``)."""
    usage = getattr(plan, "_token_usage", None)
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("in", "out"):
        v = usage.get(key)
        if isinstance(v, int):
            total += v
    return total


def _envelope_score(envelope: dict) -> int:
    """Pull a comparable score out of a single-cut or per-candidate
    envelope. Per-candidate envelopes return the best candidate's score;
    they never round-trip through the rework loop today, but the helper
    is defensive."""
    rep = envelope.get("report") or {}
    if envelope.get("kind") == "per_candidate":
        cands = rep.get("candidates") or []
        if not cands:
            return 0
        best_idx = rep.get("best_candidate_index", 0)
        if 0 <= best_idx < len(cands):
            return int(cands[best_idx].get("score", 0))
        return max(int(c.get("score", 0)) for c in cands)
    return int(rep.get("score", 0))


def _pick_shipped_envelope(history: list[dict]) -> tuple[int, dict]:
    """Return ``(index, envelope)`` for the plan that should ship.

    Iterative critic loop: the highest-scoring envelope wins. Ties are
    broken in favour of the **latest** iteration (matches today's
    in-loop ``second.score >= first.score`` regression-guard, which
    preferred the second pass on ties). Python's ``max`` returns the
    *first* match on ties, so the tie-break is implemented by reverse-
    scanning history with ``>=``. A 65 = 65 = 65 history therefore
    ships the last entry. Locked under
    ``test_pick_shipped_envelope_ties_pick_latest``.
    """
    if not history:
        raise ValueError("coherence_history must contain at least one entry")
    best_idx = 0
    best_score = _envelope_score(history[0])
    for idx in range(1, len(history)):
        score = _envelope_score(history[idx])
        if score >= best_score:
            best_idx = idx
            best_score = score
    return best_idx, history[best_idx]


def _should_rework(report) -> bool:
    """Decide whether the critic's verdict warrants a re-pick.

    Trigger:
      * verdict in {"rework", "review"} (anything not yet "ship")
      * OR any issue carries ``severity == "error"``

    Per-candidate reports (PerCandidateCoherenceReport) never trigger —
    the loop doesn't know which candidate to redo or what to swap with.
    """
    from ....intelligence.story_critic import CoherenceReport

    if not isinstance(report, CoherenceReport):
        return False
    if report.verdict in ("rework", "review"):
        return True
    return any(iss.severity == "error" for iss in report.issues)


def _critic_feedback_snapshot(report) -> dict:
    """Compact dict shape for one critic pass — used both as the
    'current' feedback and as each entry in ``history``."""
    return {
        "score": report.score,
        "verdict": report.verdict,
        "summary": report.summary,
        "issues": [iss.model_dump() for iss in report.issues],
    }


def _critic_feedback_payload(report, prior_snapshots: list[dict] | None = None) -> dict:
    """Serialise a CoherenceReport into the dict the Director's
    ``_critic_feedback_block`` consumes between passes.

    Top-level fields describe the most recent critic pass (the one the
    next Director call must address). ``history`` carries the snapshots
    of every prior pass in this build, oldest first, so iteration N's
    prompt sees what passes 1..N-1 already tried. Empty / missing on
    the first rework.
    """
    snapshot = _critic_feedback_snapshot(report)
    snapshot["history"] = list(prior_snapshots) if prior_snapshots else []
    return snapshot


def _emit_iteration(
    run_id: str,
    *,
    iteration_index: int,
    report,
    delta_from_prev: int | None,
    tokens_spent_iteration: int | None = None,
    tokens_spent_total: int | None = None,
    n_issues_unchanged: int | None = None,
    axes=None,
) -> None:
    """Per-pass iteration record. Emitted once per critic call inside
    ``_critic_with_rework``. Today's loop emits 1 (no rework) or 2 (one
    rework). The ``tokens_*`` and ``n_issues_unchanged`` fields are
    placeholders until the iterative-loop proposal's Phase 2 / Phase 4
    plumbing land; null in Phase 1.
    """
    payload = {
        "event": "story_critic.iteration",
        "run_id": run_id,
        "iteration_index": iteration_index,
        "score": report.score,
        "verdict": report.verdict,
        "delta_from_prev": delta_from_prev,
        "n_issues": len(report.issues),
        "n_issues_unchanged": n_issues_unchanged,
        "tokens_spent_iteration": tokens_spent_iteration,
        "tokens_spent_total": tokens_spent_total,
        "cut_intent": axes.cut_intent if axes else None,
        "content_type": axes.content_type if axes else None,
    }
    log.info(
        "story_critic.iteration run_id=%s iter=%d score=%d verdict=%s",
        run_id,
        iteration_index,
        report.score,
        report.verdict,
        extra=payload,
    )


def _emit_loop_terminated(
    run_id: str,
    *,
    reason: str,
    final_score: int | None,
    iterations_run: int,
    tokens_spent_total: int | None = None,
    axes=None,
) -> None:
    """One terminal record per loop run. Reason is one of
    ``{shipped, max_iterations, director_failed}`` in Phase 1; the
    proposal reserves ``plateau``, ``regression``, ``token_budget``
    for Phase 2 so downstream consumers don't have to retrofit the
    schema. The critic-skipped path (loop never starts) deliberately
    does NOT emit — ``coherence_history is None`` already signals it
    and ``story_critic.skipped`` is the load-bearing log there.
    """
    payload = {
        "event": "story_critic.loop_terminated",
        "run_id": run_id,
        "reason": reason,
        "final_score": final_score,
        "iterations_run": iterations_run,
        "tokens_spent_total": tokens_spent_total,
        "cut_intent": axes.cut_intent if axes else None,
        "content_type": axes.content_type if axes else None,
    }
    log.info(
        "story_critic.loop_terminated run_id=%s reason=%s iterations=%d final_score=%s",
        run_id,
        reason,
        iterations_run,
        final_score,
        extra=payload,
    )


def _emit_rework_triggered(run_id: str, report, axes) -> None:
    payload = {
        "event": "story_critic.rework_triggered",
        "run_id": run_id,
        "score": report.score,
        "verdict": report.verdict,
        "n_issues": len(report.issues),
        "cut_intent": axes.cut_intent if axes else None,
        "content_type": axes.content_type if axes else None,
    }
    log.info(
        "story_critic.rework_triggered run_id=%s first_score=%d first_verdict=%s n_issues=%d",
        run_id,
        report.score,
        report.verdict,
        len(report.issues),
        extra=payload,
    )


async def _critic_with_rework(
    initial_plan,
    *,
    transcript=None,
    takes=None,
    axes,
    run_id: str,
    settings_dict: dict,
    rebuild_fn,
):
    """Iterative critic loop. Runs the critic, and on a non-``ship``
    verdict re-calls the Director with the critic's findings up to
    ``CUTMASTER_STORY_CRITIC_REWORK_MAX`` times. Returns
    ``(final_plan, history)``.

    Loop exit branches (``story_critic.loop_terminated.reason``):

    - ``shipped`` — a critic pass returned ``verdict="ship"``.
    - ``max_iterations`` — exhausted ``REWORK_MAX`` reworks without
      hitting ``ship``, OR a rework critic call failed (we still
      shipped the rework plan — history records what we have).
    - ``plateau`` — ``|delta| < MIN_DELTA`` between consecutive iterations.
    - ``regression`` — ``delta <= -MIN_DELTA`` (the new pass got materially worse).
    - ``director_failed`` — ``rebuild_fn`` raised; ship the prior plan.
    - ``token_budget`` — total Gemini tokens this build crossed
      ``CUTMASTER_STORY_CRITIC_TOKEN_BUDGET``.

    The ship decision goes through ``_pick_shipped_envelope`` — the
    highest-scoring envelope wins; ties resolve to the latest iteration
    (matching the in-loop ``second.score >= first.score`` semantic from
    the previous one-shot rework).

    ``history`` is a list of wrapped envelopes; ``None`` only when the
    critic was skipped (flag off / no axes / llm error) — preserves the
    legacy "no coherence_report on the plan" behaviour.

    ``rebuild_fn`` is an async callable that takes the critic's feedback
    payload and returns a NEW native plan. Each Director branch wires its
    own (raw_dump → build_cut_plan, etc.) so the helper stays builder-
    agnostic. Failures inside ``rebuild_fn`` are swallowed: the build
    ships the prior best plan with the history collected so far.
    """
    user_opt_in = settings_dict.get("story_critic_enabled")
    max_attempts = _rework_max_attempts()
    min_delta = _rework_min_delta()
    token_budget = _rework_token_budget()

    first = _run_critic_or_skip(
        initial_plan,
        transcript=transcript,
        takes=takes,
        axes=axes,
        run_id=run_id,
        user_opt_in=user_opt_in,
    )
    if first is None:
        # Critic skipped (flag off / no axes / llm error). Per the
        # iterative-critic-loop proposal Phase 1.3, no loop_terminated
        # log here — `coherence_history is None` plus the existing
        # `story_critic.skipped` log are the load-bearing signals.
        return initial_plan, None

    history: list[dict] = [_wrap_coherence(first)]
    plans: list = [initial_plan]
    reports: list = [first]
    # `snapshots` mirrors `reports` in feedback-block shape so each rework
    # call sees the full prior-attempt list (Phase 3 of the proposal —
    # without it the model thrashes on issues an earlier pass already
    # fixed).
    snapshots: list[dict] = [_critic_feedback_snapshot(first)]
    tokens_total = _read_token_usage(initial_plan) + _read_token_usage(first)

    _emit_iteration(
        run_id,
        iteration_index=0,
        report=first,
        delta_from_prev=None,
        tokens_spent_iteration=tokens_total,
        tokens_spent_total=tokens_total,
        axes=axes,
    )

    def _terminate(reason: str, iterations_run: int) -> tuple:
        _, shipped = _pick_shipped_envelope(history)
        shipped_score = _envelope_score(shipped)
        _emit_loop_terminated(
            run_id,
            reason=reason,
            final_score=shipped_score,
            iterations_run=iterations_run,
            tokens_spent_total=tokens_total,
            axes=axes,
        )
        ship_idx, _ = _pick_shipped_envelope(history)
        return plans[ship_idx], history

    if not _should_rework(first):
        return _terminate("shipped", 1)
    if max_attempts < 1:
        # Env disables rework even though the verdict requested one.
        return _terminate("max_iterations", 1)

    _emit_rework_triggered(run_id, first, axes)

    for attempt_idx in range(max_attempts):
        iteration_index = attempt_idx + 1

        # Cost rail: skip the rebuild if we'd cross the budget. Critic
        # tokens already counted from the prior pass; rebuild + critic
        # of the new plan are the spend we're guarding against.
        if tokens_total >= token_budget:
            return _terminate("token_budget", iteration_index)

        prior_report = reports[-1]
        # `prior_snapshots` is everything BEFORE the snapshot we treat as
        # "current" — that's snapshots[:-1]. The most recent snapshot is
        # the one being addressed by this rework call, surfaced as the
        # top-level fields of the feedback dict.
        feedback = _critic_feedback_payload(prior_report, prior_snapshots=snapshots[:-1])
        try:
            new_plan = await rebuild_fn(feedback, iteration_index=iteration_index)
        except Exception as exc:
            log.warning(
                "story_critic.rework_director_failed run_id=%s err=%s",
                run_id,
                exc,
                extra={
                    "event": "story_critic.rework_director_failed",
                    "run_id": run_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return _terminate("director_failed", iteration_index)

        plan_tokens = _read_token_usage(new_plan)
        tokens_total += plan_tokens

        new_report = _run_critic_or_skip(
            new_plan,
            transcript=transcript,
            takes=takes,
            axes=axes,
            run_id=run_id,
            user_opt_in=user_opt_in,
        )
        if new_report is None:
            # Critic call failed on the rework plan; ship what we have.
            # History records the iterations whose critic returned. Use
            # max_iterations rather than a separate critic_failed reason
            # so downstream consumers stay on the documented set.
            return _terminate("max_iterations", iteration_index)

        critic_tokens = _read_token_usage(new_report)
        tokens_total += critic_tokens
        plans.append(new_plan)
        reports.append(new_report)
        snapshots.append(_critic_feedback_snapshot(new_report))
        history.append(_wrap_coherence(new_report))

        delta = new_report.score - prior_report.score
        _emit_iteration(
            run_id,
            iteration_index=iteration_index,
            report=new_report,
            delta_from_prev=delta,
            tokens_spent_iteration=plan_tokens + critic_tokens,
            tokens_spent_total=tokens_total,
            axes=axes,
        )

        # Back-compat: emit the legacy two-pass rework_completed event
        # the first time we land a rework iteration. Downstream telemetry
        # consumers (panel chips, log dashboards) still subscribe to it.
        if iteration_index == 1:
            kept_pass = "second" if new_report.score >= prior_report.score else "first"
            crossed = prior_report.verdict != "ship" and new_report.verdict == "ship"
            _emit_rework_completed(
                run_id,
                first=prior_report,
                second=new_report,
                axes=axes,
                crossed_to_ship=crossed,
                kept_pass=kept_pass,
            )

        if new_report.verdict == "ship":
            return _terminate("shipped", iteration_index + 1)
        # MIN_DELTA=0 disables both plateau and regression gates so the
        # loop runs to MAX_ATTEMPTS regardless of score movement; useful
        # for tests that need deterministic iteration counts.
        if min_delta > 0:
            if delta <= -min_delta:
                return _terminate("regression", iteration_index + 1)
            if abs(delta) < min_delta:
                return _terminate("plateau", iteration_index + 1)

    # Exhausted REWORK_MAX without hitting ship / plateau / regression.
    return _terminate("max_iterations", max_attempts + 1)


def _emit_rework_completed(
    run_id: str,
    *,
    first,
    second,
    axes,
    crossed_to_ship: bool,
    kept_pass: str = "second",
) -> None:
    score_delta = (second.score - first.score) if (first and second) else 0
    payload = {
        "event": "story_critic.rework_completed",
        "run_id": run_id,
        "first_score": first.score if first else None,
        "second_score": second.score if second else None,
        "score_delta": score_delta,
        "first_verdict": first.verdict if first else None,
        "second_verdict": second.verdict if second else None,
        "crossed_to_ship": crossed_to_ship,
        "kept_pass": kept_pass,
        "cut_intent": axes.cut_intent if axes else None,
        "content_type": axes.content_type if axes else None,
    }
    log.info(
        "story_critic.rework_completed run_id=%s delta=%+d first=%d→second=%d crossed_to_ship=%s kept=%s",
        run_id,
        score_delta,
        first.score if first else 0,
        second.score if second else 0,
        crossed_to_ship,
        kept_pass,
        extra=payload,
    )


async def _persist_plan(run_id: str, plan: dict) -> None:
    """Atomically write ``run['plan']`` and mirror user_settings up one level.

    The top-level ``run['user_settings']`` mirror survives clone-run (which
    drops the plan) so the cloned run lands at Configure with the editor's
    last choices pre-populated.
    """

    def _apply(d: dict) -> None:
        d["plan"] = plan
        settings = plan.get("user_settings")
        if settings is not None:
            d["user_settings"] = settings

    await state.update(run_id, _apply)


def _attempt_telemetry(plan_obj) -> dict:
    """Pull retry/validation residue off a returned LLM plan for SSE emit.

    ``llm.call_structured`` stashes ``_attempt_history`` and
    ``_validation_errors`` on the parsed object via ``object.__setattr__``
    when the validator burned at least one retry. Returns a small dict
    with attempt count + final residual error count + token cost so the
    Building-plan UI can show "Director (3 attempts, 0 issues, 14k tok)"
    instead of just "complete".
    """
    history = getattr(plan_obj, "_attempt_history", None) or []
    residual = getattr(plan_obj, "_validation_errors", None) or []
    tokens = getattr(plan_obj, "_token_usage", None) or {}
    # First successful attempt = retries+1; fully-validated first call has
    # no history at all so attempts=1 covers the happy path.
    attempts = (len(history) + 1) if history else 1
    return {
        "attempts": attempts,
        "validation_errors": len(residual),
        "tokens_in": tokens.get("in"),
        "tokens_out": tokens.get("out"),
    }


@router.post("/build-plan")
async def build_plan(body: BuildPlanRequest) -> dict:
    """Run Director → Marker → resolve source frames. Dry-run: no Resolve mutation.

    Writes the plan to the run's state file and returns it. Phase 6 (execute)
    will load the same state and actually build the timeline.
    """
    run, scrubbed = _require_scrubbed(body.run_id)
    # Emit a started event so the panel's Building-plan poller can show
    # something is in flight before any LLM call returns. The matching
    # complete event fires just before /build-plan returns its dict; the
    # except branches emit failed so the row never gets stuck.
    await state.emit(
        run,
        stage="build_director",
        status="started",
        message="Director composing the cut",
    )
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown preset '{body.preset}'")
    preset = get_preset(body.preset)
    settings_dict = body.user_settings.model_dump()
    # Editor-driven "Regenerate with recommendations": the panel passes a
    # prior build's critic report as ``critic_feedback`` so this build's
    # Director sees it as the rework prompt block on its FIRST call. The
    # auto-rework loop then runs on top as normal. The underscore prefix
    # is the same idiom every internal rebuild_fn uses; ``_user_settings_block``
    # ignores underscore-prefixed keys so it never leaks into the prompt's
    # USER SETTINGS summary.
    if body.critic_feedback:
        settings_dict["_critic_feedback"] = body.critic_feedback
    mode = body.user_settings.timeline_mode

    # Source-track index picked during analyze (track_picker auto-detect
    # or explicit AnalyzeRequest override) and persisted on the run.
    # Older runs (pre-picker) don't have this field — default to 1 so
    # they still build against the legacy V1 assumption.
    video_track_idx = int(run.get("video_track") or 1)

    # v2-11 / Phase 4.5: compatibility guard + reorder=false handling.
    # Must run before the preset-specific branches so an incompatible combo
    # returns 400 rather than a confusing Director-side failure.
    #
    # Tightener is a self-normalising workflow preset — its own branch
    # forces assembled+reorder_off later. Skip the guard for it so callers
    # that don't know the constraint (or v1 clients) don't break.
    #
    # Primary gate is the three-axis matrix (`cut_intent_mode_compatible`)
    # keyed on the effective ``(cut_intent, timeline_mode)`` pair. The
    # legacy ``preset_mode_compatible`` helper stays as a belt-and-braces
    # fallback during the migration window — removed in Phase 7.
    if body.preset != "tightener":
        effective_intent = _effective_cut_intent(body)
        if effective_intent is not None:
            axis_reason = cut_intent_mode_incompatibility_reason(effective_intent, mode)
            if axis_reason is not None:
                raise HTTPException(status_code=400, detail=axis_reason)
        if not preset_mode_compatible(body.preset, mode):
            raise HTTPException(
                status_code=400,
                detail=preset_mode_incompatibility_reason(body.preset, mode)
                or f"preset '{body.preset}' is not compatible with mode '{mode}'",
            )
    if mode == "curated" and not body.user_settings.reorder_allowed:
        # Curated + reorder_off is semantically equivalent to Assembled —
        # normalise silently and log so /state reflects what actually ran.
        log.info(
            "cutmaster.build: normalising curated+reorder_off → assembled run_id=%s",
            body.run_id,
        )
        mode = "assembled"
        settings_dict["timeline_mode"] = "assembled"
    if mode == "rough_cut" and not body.user_settings.reorder_allowed:
        # Rough cut *drops* alternates; Assembled does not. Silent
        # normalisation would lose semantics — reject explicitly.
        raise HTTPException(
            status_code=400,
            detail=(
                "rough_cut + reorder_allowed=false is not supported — Rough "
                "cut drops alternates (which Assembled never does). Use "
                "Assembled to preserve order, or Rough cut with reordering on."
            ),
        )
    log.info(
        "cutmaster.build: mode=%s preset=%s run_id=%s",
        mode,
        body.preset,
        body.run_id,
    )

    # Phase 4.6: compute the three-axis resolution once so every stage
    # (prompt builder, compat check, downstream telemetry) reads the
    # same recipe. ``None`` when the caller didn't supply axis-keyed
    # context — the flag gate in the prompt builders falls back to the
    # legacy preset path and the render is byte-identical to pre-Phase 3.
    # Persistence + resolution both live in ``pipeline.stash_resolved_axes``
    # so the build route and any future analyze-side caller stash the
    # same shape on ``run["resolved_axes"]``.
    duration_s = _transcript_duration_s(scrubbed)
    content_type = _effective_content_type(body)
    resolved_axes: ResolvedAxes | None = None
    if content_type is not None:
        resolved_axes = pipeline.stash_resolved_axes(
            run,
            content_type=content_type,
            cut_intent=_effective_cut_intent(body),
            duration_s=duration_s,
            timeline_mode=mode,
            num_clips=body.user_settings.num_clips,
            reorder_allowed=body.user_settings.reorder_allowed,
            takes_already_scrubbed=body.user_settings.takes_already_scrubbed,
        )
    if resolved_axes is not None:
        # Phase 6.3 — structured ``axis_resolution.decided`` telemetry.
        # One line per build, with the full resolved recipe as ``extra``
        # fields so log aggregators can trend (a) cut-intent provenance
        # (user / auto / forced), (b) pacing curve outliers, and
        # (c) Phase 7's 30-day legacy-alias gate (cross-checked against
        # ``legacy_preset_alias_used`` from Phase 4.3).
        log.info(
            "axis_resolution.decided",
            extra={
                "event": "axis_resolution.decided",
                "run_id": body.run_id,
                "content_type": resolved_axes.content_type,
                "cut_intent": resolved_axes.cut_intent,
                "cut_intent_source": resolved_axes.cut_intent_source,
                "duration_s": round(duration_s, 2),
                "num_clips": body.user_settings.num_clips,
                "timeline_mode": mode,
                "reorder_mode": resolved_axes.reorder_mode,
                "pacing_target_s": round(resolved_axes.segment_pacing.target, 2),
                "pacing_min_s": round(resolved_axes.segment_pacing.min, 2),
                "pacing_max_s": round(resolved_axes.segment_pacing.max, 2),
                "selection_strategy": resolved_axes.selection_strategy,
                "prompt_builder": resolved_axes.prompt_builder,
                "rationale": resolved_axes.rationale,
                "unusual": resolved_axes.unusual,
            },
        )

    # Sibling of ``axis_resolution.decided`` — one ``sensory_resolution``
    # line per build, capturing master + per-layer overrides + the
    # resolved triple. Fires before any Layer-A gate decision so the
    # log line is consistent regardless of which build path runs
    # (raw_dump / SG / clip_hunter / curated / rough_cut / assembled).
    log_sensory_resolution(
        body.run_id,
        settings_dict,
        cut_intent=_cut_intent_for(body, resolved_axes),
        timeline_mode=mode,
    )

    # v4 Layer A: populated by the wrapping loop in modes that enable it.
    # Stays None for modes where Layer A is skipped (assembled, tightener,
    # clip_hunter, short_generator) or for runs with Layer A off.
    boundary_result: BoundaryValidationResult | None = None

    # v2-4: Clip Hunter — different optimisation target (N candidate clips
    # ranked by engagement, not one narrative cut). Each candidate is stored
    # on the plan so the Review UI can let the user pick; /execute reads the
    # chosen candidate_index to build exactly that clip's timeline.
    if body.preset == "clip_hunter":
        # Long-source gate (proposal §4.7). Hard-block beyond v2's 60-min
        # ceiling; warn the user in the plan output between 15 min and the
        # ceiling so they can downsize if Director quality dips.
        last_word_end = float(scrubbed[-1].get("end_time", 0.0)) if scrubbed else 0.0
        if last_word_end > 60 * 60:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"source is {last_word_end / 60:.1f} min; Clip Hunter "
                    f"v2 ceiling is 60 min. Chunk + summarise pipeline is "
                    f"deferred to v3 per proposal §4.7."
                ),
            )
        duration_warning: str | None = None
        if last_word_end > 15 * 60:
            duration_warning = (
                f"source is {last_word_end / 60:.1f} min — Clip Hunter was "
                "validated on ≤8 min audio. Expect some timestamp drift and "
                "run the v2-4 spike before trusting results (proposal §4.7)."
            )

        target_clip_length_s = float(body.user_settings.target_length_s or 60)
        num_clips = body.user_settings.num_clips

        # Short-source feasibility guard. The Clip Hunter validator enforces
        # non-overlapping candidates at ~0.6× target length minimum. If the
        # source is too short for N × minimum-length clips, the retry loop
        # burns 3 × 3-minute LLM calls before failing — and the user just
        # sees a dead-air Review screen. Short-circuit with a specific
        # 400 that tells them exactly what to change.
        min_required_s = num_clips * target_clip_length_s * 0.6
        if last_word_end > 0 and last_word_end < min_required_s:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"source is {last_word_end:.1f}s; not enough for {num_clips} "
                    f"non-overlapping {target_clip_length_s:.0f}s clips "
                    f"(needs ≥{min_required_s:.0f}s at minimum duration tolerance). "
                    f"Try fewer clips or a shorter target length."
                ),
            )

        _dump_director_prompt(
            body.run_id,
            director_mod._clip_hunter_prompt(
                preset,
                scrubbed,
                settings_dict,
                target_clip_length_s,
                num_clips,
            ),
        )

        try:
            hunter_plan = await with_timeout(
                asyncio.to_thread(
                    build_clip_hunter_plan,
                    scrubbed,
                    preset,
                    settings_dict,
                    target_clip_length_s,
                    num_clips,
                    resolved=resolved_axes,
                ),
                DIRECTOR_TIMEOUT_S,
                "Clip Hunter Director",
            )
        except Exception as exc:
            log.exception("Clip Hunter Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Clip Hunter Director failed: {exc}")

        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        # Resolve per-candidate segments. Auto-split handles candidates that
        # happen to cross timeline-item boundaries in raw-dump sources.
        candidates_payload: list[dict] = []
        for cand in hunter_plan.candidates:
            segs = candidate_to_segments(cand)
            try:
                resolved = await asyncio.to_thread(
                    resolve_segments, tl, segs, video_track=video_track_idx
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"clip [{cand.start_s:.2f},{cand.end_s:.2f}]: {exc}",
                )
            candidates_payload.append(
                {
                    **cand.model_dump(),
                    "resolved_segments": [r.model_dump() for r in resolved],
                }
            )

        # Default selection: top-ranked candidate (index 0). User overrides
        # via /execute's candidate_index.
        top_segments = candidates_payload[0]["resolved_segments"] if candidates_payload else []
        plan = DirectorPlan(
            hook_index=0,
            selected_clips=[
                CutSegment(
                    start_s=float(s["start_s"]),
                    end_s=float(s["end_s"]),
                    reason=s.get("reason", ""),
                )
                for s in top_segments
            ],
            reasoning=hunter_plan.reasoning,
        )
        # Skip the Marker LLM — Clip Hunter candidates are self-contained,
        # B-roll cue markers don't add value at this granularity.
        markers = MarkerPlan(markers=[])

        run["plan"] = {
            "preset": body.preset,
            "user_settings": settings_dict,
            "director": plan.model_dump(),
            "markers": markers.model_dump(),
            "resolved_segments": top_segments,
            "clip_hunter": {
                "candidates": candidates_payload,
                "selected_index": 0,
                "target_clip_length_s": target_clip_length_s,
                "num_clips": num_clips,
                "duration_warning": duration_warning,
                "source_duration_s": last_word_end,
            },
        }
        if resolved_axes is not None:
            run["plan"]["resolved_axes"] = resolved_axes.model_dump()
        coherence = _run_critic_or_skip(
            hunter_plan,
            transcript=scrubbed,
            axes=resolved_axes,
            run_id=body.run_id,
            user_opt_in=settings_dict.get("story_critic_enabled"),
        )
        if coherence is not None:
            run["plan"]["coherence_report"] = _wrap_coherence(coherence)
        await _persist_plan(body.run_id, run["plan"])
        return run["plan"]

    # v2-13: Short Generator — assembled multi-span reels. Each candidate is
    # 3–8 spans jump-cut into one 45–90s short. Surface structure mirrors
    # Clip Hunter (N candidates stored, executed per-candidate_index) but the
    # per-candidate payload carries a list of spans so execute appends them
    # end-to-end on the new timeline.
    if body.preset == "short_generator":
        last_word_end = float(scrubbed[-1].get("end_time", 0.0)) if scrubbed else 0.0
        target_short_length_s = float(body.user_settings.target_length_s or 60)
        num_shorts = body.user_settings.num_clips

        # Short Generator needs at least (num_shorts * 3) seconds of content —
        # 3 spans minimum per short is non-negotiable per the validator.
        min_required_s = num_shorts * 3.0
        if last_word_end > 0 and last_word_end < min_required_s:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"source is {last_word_end:.1f}s; Short Generator needs "
                    f"≥{min_required_s:.0f}s for {num_shorts} shorts "
                    f"(each short = 3+ spans). Try fewer shorts."
                ),
            )

        _dump_director_prompt(
            body.run_id,
            director_mod._short_generator_prompt(
                preset,
                scrubbed,
                settings_dict,
                target_short_length_s,
                num_shorts,
            ),
        )

        # Resolve tl up front so the short-generator Layer A validator
        # (when active) can map every candidate's span transitions to
        # source frames before the Director call completes. Same tl
        # consumed downstream by resolve_segments per candidate.
        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        async def _sg_base(eff_settings: dict):
            return await with_timeout(
                asyncio.to_thread(
                    build_short_generator_plan,
                    scrubbed,
                    preset,
                    eff_settings,
                    target_short_length_s,
                    num_shorts,
                    resolved=resolved_axes,
                ),
                DIRECTOR_TIMEOUT_S,
                "Short Generator Director",
            )

        try:
            # Short Generator → assembled_short cut intent. ``timeline_mode``
            # is unused by the matrix for multi-candidate presets but the
            # axis-keyed resolver still wants a value, so pass the safe
            # ``"raw_dump"`` placeholder (matches the row collapse).
            if _layer_a_enabled(
                settings_dict, cut_intent="assembled_short", timeline_mode="raw_dump"
            ):

                async def _sg_director(rejections, roster):
                    eff = dict(settings_dict)
                    if rejections:
                        eff["_boundary_rejections"] = rejections
                    if roster:
                        eff["_candidate_roster"] = roster
                    return await _sg_base(eff)

                def _sg_samples(plan):
                    return build_short_generator_boundary_samples(
                        tl,
                        plan.candidates,
                        project=project,
                        video_track=video_track_idx,
                    )

                def _sg_roster(plan):
                    return [
                        {"candidate_index": i, "theme": cand.theme}
                        for i, cand in enumerate(plan.candidates)
                    ]

                boundary_result = await run_with_boundary_validation(
                    director_fn=_sg_director,
                    build_samples=_sg_samples,
                    extract_candidate_roster=_sg_roster,
                )
                short_plan = boundary_result.plan
            else:
                short_plan = await _sg_base(settings_dict)
        except Exception as exc:
            log.exception("Short Generator Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Short Generator Director failed: {exc}")

        # Resolve spans per candidate. Unlike Clip Hunter, each candidate
        # carries multiple CutSegments — resolver handles them identically
        # to Raw-dump / Assembled multi-span plans.
        candidates_payload: list[dict] = []
        for cand in short_plan.candidates:
            segs = short_candidate_to_segments(cand)
            try:
                resolved = await asyncio.to_thread(
                    resolve_segments, tl, segs, video_track=video_track_idx
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"short '{cand.theme}': {exc}",
                )
            candidates_payload.append(
                {
                    **cand.model_dump(),
                    "resolved_segments": [r.model_dump() for r in resolved],
                }
            )

        top_segments = candidates_payload[0]["resolved_segments"] if candidates_payload else []
        plan = DirectorPlan(
            hook_index=0,
            selected_clips=[
                CutSegment(
                    start_s=float(s["start_s"]),
                    end_s=float(s["end_s"]),
                    reason=s.get("reason", ""),
                )
                for s in top_segments
            ],
            reasoning=short_plan.reasoning,
        )
        markers = MarkerPlan(markers=[])

        # Reuse the clip_hunter key so execute.py's existing per-candidate
        # swap logic works unchanged — the fields line up deliberately.
        run["plan"] = {
            "preset": body.preset,
            "user_settings": settings_dict,
            "director": plan.model_dump(),
            "markers": markers.model_dump(),
            "resolved_segments": top_segments,
            "clip_hunter": {
                "candidates": candidates_payload,
                "selected_index": 0,
                "target_clip_length_s": target_short_length_s,
                "num_clips": num_shorts,
                "duration_warning": None,
                "source_duration_s": last_word_end,
                "mode": "short_generator",
            },
        }
        if boundary_result is not None:
            run["plan"]["boundary_validation"] = boundary_result.to_summary()
        if resolved_axes is not None:
            run["plan"]["resolved_axes"] = resolved_axes.model_dump()
        coherence = _run_critic_or_skip(
            short_plan,
            transcript=scrubbed,
            axes=resolved_axes,
            run_id=body.run_id,
            user_opt_in=settings_dict.get("story_critic_enabled"),
        )
        if coherence is not None:
            run["plan"]["coherence_report"] = _wrap_coherence(coherence)
        await _persist_plan(body.run_id, run["plan"])
        return run["plan"]

    # v2-3: Tightener preset forces assembled + reorder_off, re-scrubs the
    # raw transcript with aggressive defaults, skips the Director entirely,
    # and emits one CutSegment per contiguous kept-word block per take.
    # Settings get normalised so /state reflects what actually ran.
    if body.preset == "tightener":
        settings_dict["timeline_mode"] = "assembled"
        settings_dict["reorder_allowed"] = False

        raw_transcript = run.get("transcript") or []
        if not raw_transcript:
            raise HTTPException(
                status_code=400,
                detail="run has no raw transcript — re-analyze before running Tightener",
            )

        # Aggressive scrub: user-provided params win; otherwise preset defaults.
        if body.user_settings.scrub_params:
            tight_params = body.user_settings.scrub_params
        else:
            tight_params = ScrubParams(**preset.scrub_defaults)
        tight_scrub = scrub(raw_transcript, tight_params)
        tight_scrubbed = tight_scrub.kept

        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        items = read_items_on_track(tl, track_index=video_track_idx)
        if not items:
            raise HTTPException(
                status_code=400,
                detail=f"timeline has no items on V{video_track_idx} — Tightener needs takes",
            )
        per_item = split_transcript_per_item(tight_scrubbed, items)
        takes = build_take_entries(items, per_item)

        segments = build_tightener_segments(takes, gap_threshold_s=DEFAULT_BLOCK_GAP_S)
        if not segments:
            raise HTTPException(
                status_code=400,
                detail="Tightener produced no segments — every take was fully scrubbed out",
            )

        plan = DirectorPlan(
            hook_index=0,
            selected_clips=segments,
            reasoning=(
                f"Tightener: {len(segments)} block(s) across {len(takes)} take(s), "
                f"filler={tight_scrub.counts.get('filler', 0)}, "
                f"dead_air={tight_scrub.counts.get('dead_air', 0)}"
            ),
        )
        # Marker agent is deliberately skipped — Tightener is a no-Director
        # workflow and marker cues depend on narrative context the editor
        # is already managing by hand.
        markers = MarkerPlan(markers=[])
        tighten_summary = tightener_stats(raw_transcript, takes, segments)

        try:
            resolved = await asyncio.to_thread(
                resolve_segments, tl, segments, video_track=video_track_idx
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"source-frame mapping failed: {exc}")

        run["plan"] = {
            "preset": body.preset,
            "user_settings": settings_dict,
            "director": plan.model_dump(),
            "markers": markers.model_dump(),
            "resolved_segments": [r.model_dump() for r in resolved],
            "tightener": tighten_summary,
        }
        if resolved_axes is not None:
            run["plan"]["resolved_axes"] = resolved_axes.model_dump()
        coherence = _run_critic_or_skip(
            plan,
            transcript=scrubbed,
            axes=resolved_axes,
            run_id=body.run_id,
            user_opt_in=settings_dict.get("story_critic_enabled"),
        )
        if coherence is not None:
            run["plan"]["coherence_report"] = _wrap_coherence(coherence)
        await _persist_plan(body.run_id, run["plan"])
        return run["plan"]

    # Story-critic Phase 6 — coherence history. Populated by branches that
    # run the rework loop (currently raw_dump only); ``None`` means the
    # catch-all critic call at the bottom should single-pass-grade as in
    # Phase 2. Stays alongside ``_critic_native_plan`` / ``_critic_native_takes``
    # which the catch-all marker + persist sites consume.
    coherence_history: list[dict] | None = None

    # v2-11: Curated + Rough cut share most of assembled's plumbing (reading
    # V1 items, splitting transcript per take, reusing the per-take Director
    # output shape). The differences are the Director function called and
    # whether a group detector runs first.
    if mode in ("curated", "rough_cut"):
        if body.user_settings.takes_already_scrubbed:
            transcript_for_takes = run.get("transcript") or []
            if not transcript_for_takes:
                raise HTTPException(
                    status_code=400,
                    detail="takes_already_scrubbed=true but run has no raw transcript",
                )
        else:
            transcript_for_takes = scrubbed

        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        # Rough cut needs grouping signals (color + flags); Curated only
        # needs the take geometry. Read both through the grouping adapter
        # for Rough cut, fall back to the simpler adapter for Curated.
        if mode == "rough_cut":
            grouped_items = read_items_with_grouping_signals(tl, track_index=video_track_idx)
            if not grouped_items:
                raise HTTPException(
                    status_code=400,
                    detail=f"timeline has no items on V{video_track_idx} — Rough cut needs takes",
                )
            items = to_item_summary(grouped_items)
        else:
            grouped_items = None
            items = read_items_on_track(tl, track_index=video_track_idx)
            if not items:
                raise HTTPException(
                    status_code=400,
                    detail=f"timeline has no items on V{video_track_idx} — Curated needs takes",
                )

        per_item = split_transcript_per_item(transcript_for_takes, items)
        takes = build_take_entries(items, per_item)

        def _curated_samples(plan):
            # Curated / rough-cut plans don't carry a flat selected_clips
            # list — expand_curated_plan builds one from the take indexes.
            try:
                segs, _hook = expand_curated_plan(plan, takes)
            except Exception as exc:
                log.info("layer A: expand_curated_plan raised (%s) — skipping", exc)
                return []
            return build_boundary_samples(tl, segs, project=project, video_track=video_track_idx)

        if mode == "rough_cut":
            groups = detect_groups(
                grouped_items,
                per_item,
                similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
            )
            singletons = all_singletons(groups)
            _dump_director_prompt(
                body.run_id,
                director_mod._rough_cut_prompt(preset, takes, groups, settings_dict),
            )

            async def _rc_base(eff_settings: dict):
                return await with_timeout(
                    asyncio.to_thread(
                        build_rough_cut_plan,
                        takes,
                        groups,
                        preset,
                        eff_settings,
                        resolved=resolved_axes,
                    ),
                    DIRECTOR_TIMEOUT_S,
                    "Rough cut Director",
                )

            try:
                if _layer_a_enabled(
                    settings_dict,
                    cut_intent=_cut_intent_for(body, resolved_axes),
                    timeline_mode=mode,
                ):

                    async def _rc_director(rejections, roster):
                        eff = dict(settings_dict)
                        if rejections:
                            eff["_boundary_rejections"] = rejections
                        if roster:
                            eff["_candidate_roster"] = roster
                        return await _rc_base(eff)

                    boundary_result = await run_with_boundary_validation(
                        director_fn=_rc_director,
                        build_samples=_curated_samples,
                    )
                    curated_plan = boundary_result.plan
                else:
                    curated_plan = await _rc_base(settings_dict)
            except Exception as exc:
                log.exception("Rough cut Director failed for run %s", body.run_id)
                raise HTTPException(status_code=500, detail=f"Rough cut Director failed: {exc}")
        else:
            groups = []
            singletons = False
            _dump_director_prompt(
                body.run_id,
                director_mod._curated_prompt(preset, takes, settings_dict),
            )

            async def _cur_base(eff_settings: dict):
                return await with_timeout(
                    asyncio.to_thread(
                        build_curated_cut_plan,
                        takes,
                        preset,
                        eff_settings,
                        resolved=resolved_axes,
                    ),
                    DIRECTOR_TIMEOUT_S,
                    "Curated Director",
                )

            try:
                if _layer_a_enabled(
                    settings_dict,
                    cut_intent=_cut_intent_for(body, resolved_axes),
                    timeline_mode=mode,
                ):

                    async def _cur_director(rejections, roster):
                        eff = dict(settings_dict)
                        if rejections:
                            eff["_boundary_rejections"] = rejections
                        if roster:
                            eff["_candidate_roster"] = roster
                        return await _cur_base(eff)

                    boundary_result = await run_with_boundary_validation(
                        director_fn=_cur_director,
                        build_samples=_curated_samples,
                    )
                    curated_plan = boundary_result.plan
                else:
                    curated_plan = await _cur_base(settings_dict)
            except Exception as exc:
                log.exception("Curated Director failed for run %s", body.run_id)
                raise HTTPException(status_code=500, detail=f"Curated Director failed: {exc}")

        selected_clips, hook_cut_index = expand_curated_plan(curated_plan, takes)
        plan = DirectorPlan(
            hook_index=hook_cut_index,
            selected_clips=selected_clips,
            reasoning=curated_plan.reasoning,
        )
        # Stash the native (non-flat) plan for the story-critic — the
        # CuratedDirectorPlan adapter knows about ordered selections; the
        # synthetic flat DirectorPlan loses that structure.
        _critic_native_plan: object = curated_plan
        _critic_native_takes: list[dict] | None = takes
        # Stash mode-specific metadata for the Review screen. Merged into
        # the final response after marker / resolve run.
        _v2_11_meta: dict = {
            "mode": mode,
            "takes_used": sorted({s.item_index for s in curated_plan.selections}),
            "total_takes": len(takes),
        }
        if mode == "rough_cut":
            _v2_11_meta["groups"] = [dict(g) for g in groups]
            _v2_11_meta["all_singletons"] = singletons

        # Phase 6 of story-critic: rework loop for curated / rough_cut.
        # Re-call the matching builder with critic feedback in settings.
        # On rework, re-expand to a flat DirectorPlan so marker + resolve
        # downstream consume the corrected segments.
        async def _rebuild_curated_or_rough(feedback: dict, *, iteration_index: int):
            eff = {**settings_dict, "_critic_feedback": feedback}
            if mode == "rough_cut":
                builder = build_rough_cut_plan
                args: tuple = (takes, groups, preset, eff)
                prompt_text = director_mod._rough_cut_prompt(
                    preset, takes, groups, eff, resolved=resolved_axes
                )
            else:
                builder = build_curated_cut_plan
                args = (takes, preset, eff)
                prompt_text = director_mod._curated_prompt(
                    preset, takes, eff, resolved=resolved_axes
                )
            _dump_director_prompt(body.run_id, prompt_text, pass_index=iteration_index)
            return await with_timeout(
                asyncio.to_thread(builder, *args, resolved=resolved_axes),
                DIRECTOR_TIMEOUT_S,
                f"{mode} Director (rework)",
            )

        curated_plan, coherence_history = await _critic_with_rework(
            curated_plan,
            transcript=None,
            takes=takes,
            axes=resolved_axes,
            run_id=body.run_id,
            settings_dict=settings_dict,
            rebuild_fn=_rebuild_curated_or_rough,
        )
        # Re-expand whichever plan we ended up with; expander is deterministic
        # so this is cheap even when no rework happened.
        selected_clips, hook_cut_index = expand_curated_plan(curated_plan, takes)
        plan = DirectorPlan(
            hook_index=hook_cut_index,
            selected_clips=selected_clips,
            reasoning=curated_plan.reasoning,
        )
        _critic_native_plan = curated_plan
        _v2_11_meta["takes_used"] = sorted({s.item_index for s in curated_plan.selections})

    # v2-2: assembled mode uses a different Director. Both paths converge on
    # the same CutSegment + resolver pipeline from step 2 onward.
    elif mode == "assembled":
        if body.user_settings.takes_already_scrubbed:
            transcript_for_takes = run.get("transcript") or []
            if not transcript_for_takes:
                raise HTTPException(
                    status_code=400,
                    detail="takes_already_scrubbed=true but run has no raw transcript",
                )
        else:
            transcript_for_takes = scrubbed

        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        items = read_items_on_track(tl, track_index=video_track_idx)
        if not items:
            raise HTTPException(
                status_code=400,
                detail=f"timeline has no items on V{video_track_idx} — assembled mode needs takes",
            )
        per_item = split_transcript_per_item(transcript_for_takes, items)
        takes = build_take_entries(items, per_item)

        _dump_director_prompt(
            body.run_id,
            director_mod._assembled_prompt(preset, takes, settings_dict),
        )

        try:
            assembled_plan = await with_timeout(
                asyncio.to_thread(
                    build_assembled_cut_plan,
                    takes,
                    preset,
                    settings_dict,
                    resolved=resolved_axes,
                ),
                DIRECTOR_TIMEOUT_S,
                "Assembled Director",
            )
        except Exception as exc:
            log.exception("Assembled Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Assembled Director failed: {exc}")

        # Phase 6 of story-critic: rework loop for assembled mode. The
        # AssembledDirectorPlan adapter knows about ordered selections +
        # word-index spans so the critic grades the native shape, not the
        # synthesised flat DirectorPlan.
        async def _rebuild_assembled(feedback: dict, *, iteration_index: int):
            eff = {**settings_dict, "_critic_feedback": feedback}
            _dump_director_prompt(
                body.run_id,
                director_mod._assembled_prompt(preset, takes, eff, resolved=resolved_axes),
                pass_index=iteration_index,
            )
            return await with_timeout(
                asyncio.to_thread(
                    build_assembled_cut_plan,
                    takes,
                    preset,
                    eff,
                    resolved=resolved_axes,
                ),
                DIRECTOR_TIMEOUT_S,
                "Assembled Director (rework)",
            )

        assembled_plan, coherence_history = await _critic_with_rework(
            assembled_plan,
            transcript=None,
            takes=takes,
            axes=resolved_axes,
            run_id=body.run_id,
            settings_dict=settings_dict,
            rebuild_fn=_rebuild_assembled,
        )
        selected_clips, hook_cut_index = expand_assembled_plan(assembled_plan, takes)
        plan = DirectorPlan(
            hook_index=hook_cut_index,
            selected_clips=selected_clips,
            reasoning=assembled_plan.reasoning,
        )
        _critic_native_plan = assembled_plan
        _critic_native_takes = takes
    else:
        # v1 raw-dump path. Batch 7: inject cached chapters so the Director
        # prompt + reorder-mode validator can honour preserve_macro policies.
        cached_analysis = run.get("story_analysis") or {}
        chapters = (cached_analysis.get("analysis") or {}).get("chapters") or []
        if chapters:
            settings_dict = {**settings_dict, "chapters": chapters}
        _dump_director_prompt(
            body.run_id,
            director_mod._prompt(preset, scrubbed, settings_dict),
        )

        # Resolve tl up front so the Marker + segment resolver can consume
        # it below, AND so v4 Layer A (when active) can map proposed cut
        # boundaries to source frames without a second Resolve round-trip.
        from ....cutmaster.core.pipeline import _find_timeline_by_name
        from ....resolve import _boilerplate  # lazy

        try:
            _, project, _ = _boilerplate()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

        tl = _find_timeline_by_name(project, run["timeline_name"])
        if tl is None:
            raise HTTPException(
                status_code=400,
                detail=f"timeline '{run['timeline_name']}' not found (was it renamed?)",
            )

        try:
            plan, boundary_result = await _director_or_validated(
                mode=mode,
                cut_intent=_cut_intent_for(body, resolved_axes),
                settings=settings_dict,
                base_call=lambda settings: with_timeout(
                    asyncio.to_thread(
                        build_cut_plan, scrubbed, preset, settings, resolved=resolved_axes
                    ),
                    DIRECTOR_TIMEOUT_S,
                    "Director",
                ),
                get_selected_clips=lambda plan: plan.selected_clips,
                tl=tl,
                project=project,
                video_track=video_track_idx,
            )
        except Exception as exc:
            log.exception("Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Director agent failed: {exc}")

        _critic_native_plan = plan
        _critic_native_takes = None

        # Phase 6 of story-critic: rework loop (raw_dump path only in v0).
        # Run critic NOW (before marker + resolve) so a rework re-pick
        # doesn't waste a marker LLM call + resolver round-trip on a plan
        # we're about to discard. assembled / curated / tightener still
        # use the catch-all single-pass critic at the bottom of this
        # handler — wired in a follow-up bite.
        async def _rebuild_raw_dump(feedback: dict, *, iteration_index: int):
            eff = {**settings_dict, "_critic_feedback": feedback}
            # Dump the augmented prompt so editors can review what the
            # model was told to fix on this iteration. Lands at
            # ``<run_id>.director_prompt.<N>.txt`` next to the first-pass
            # dump; iteration 1 also writes a ``.rework.txt`` alias for
            # back-compat with persisted runs.
            _dump_director_prompt(
                body.run_id,
                director_mod._prompt(preset, scrubbed, eff),
                pass_index=iteration_index,
            )
            new_plan, _ = await _director_or_validated(
                mode=mode,
                cut_intent=_cut_intent_for(body, resolved_axes),
                settings=eff,
                base_call=lambda settings: with_timeout(
                    asyncio.to_thread(
                        build_cut_plan,
                        scrubbed,
                        preset,
                        settings,
                        resolved=resolved_axes,
                    ),
                    DIRECTOR_TIMEOUT_S,
                    "Director (rework)",
                ),
                get_selected_clips=lambda p: p.selected_clips,
                tl=tl,
                project=project,
                video_track=video_track_idx,
            )
            return new_plan

        plan, coherence_history = await _critic_with_rework(
            plan,
            transcript=scrubbed,
            takes=None,
            axes=resolved_axes,
            run_id=body.run_id,
            settings_dict=settings_dict,
            rebuild_fn=_rebuild_raw_dump,
        )
        _critic_native_plan = plan

    # Director phase complete — emit telemetry the Building-plan UI surfaces
    # as "Director (N attempts, M issues)" so the editor can see when the
    # validator loop went all the way to best-effort instead of converging.
    director_telemetry = _attempt_telemetry(plan)
    await state.emit(
        run,
        stage="build_director",
        status="complete",
        message=(
            f"Plan built · {director_telemetry['attempts']} attempt(s)"
            + (
                f" · {director_telemetry['validation_errors']} unresolved issue(s)"
                if director_telemetry["validation_errors"]
                else ""
            )
        ),
        data=director_telemetry,
    )

    # Marker agent runs against the flat CutSegment list in both modes.
    await state.emit(
        run,
        stage="build_marker",
        status="started",
        message="Marker agent picking B-roll cues",
    )
    try:
        markers: MarkerPlan = await with_timeout(
            asyncio.to_thread(suggest_markers, plan, scrubbed, preset, settings_dict),
            MARKER_TIMEOUT_S,
            "Marker agent",
        )
    except Exception as exc:
        log.exception("Marker agent failed for run %s", body.run_id)
        await state.emit(
            run,
            stage="build_marker",
            status="failed",
            message=f"Marker agent failed: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"Marker agent failed: {exc}")
    marker_telemetry = _attempt_telemetry(markers)
    await state.emit(
        run,
        stage="build_marker",
        status="complete",
        message=(
            f"{len(markers.markers)} marker(s) suggested · "
            f"{marker_telemetry['attempts']} attempt(s)"
        ),
        data={**marker_telemetry, "marker_count": len(markers.markers)},
    )

    # Resolve source frames — identical in both modes.
    await state.emit(
        run,
        stage="build_frames",
        status="started",
        message="Mapping segments to Resolve source frames",
    )
    try:
        resolved = await asyncio.to_thread(
            resolve_segments, tl, plan.selected_clips, video_track=video_track_idx
        )
    except ValueError as exc:
        await state.emit(
            run,
            stage="build_frames",
            status="failed",
            message=f"source-frame mapping failed: {exc}",
        )
        raise HTTPException(status_code=400, detail=f"source-frame mapping failed: {exc}")
    await state.emit(
        run,
        stage="build_frames",
        status="complete",
        message=f"Resolved {len(resolved)} segment(s) to source frames",
        data={"segment_count": len(resolved)},
    )

    run["plan"] = {
        "preset": body.preset,
        "user_settings": settings_dict,
        "director": plan.model_dump(),
        "markers": markers.model_dump(),
        "resolved_segments": [r.model_dump() for r in resolved],
    }
    # Surface llm best-effort validation residue so the panel can warn the
    # user when the Director failed a constraint (e.g. selected_hook_s drift
    # > HOOK_TOLERANCE_S) but llm.call_structured returned the best-of-bad
    # plan anyway. Without this the failure is server-log-only and the
    # editor sees a "successful" plan that quietly violates their pick.
    warnings = _plan_warnings(plan, _critic_native_plan)
    if warnings:
        run["plan"]["plan_warnings"] = warnings
    # Phase 4.6: surface the three-axis recipe so the Review UI can show
    # the resolved chip ("Interview · Peak Highlight · 60 s → 3/7/17 s")
    # without re-deriving from preset + settings.
    if resolved_axes is not None:
        run["plan"]["resolved_axes"] = resolved_axes.model_dump()
    # v2-11: attach mode-specific metadata for Curated / Rough cut runs.
    if mode in ("curated", "rough_cut"):
        run["plan"]["timeline_state"] = _v2_11_meta  # type: ignore[name-defined]
    # v4 Phase 4.2: surface boundary-validator warnings so the Review
    # screen can show remaining jarring / borderline cuts alongside the
    # plan. Only present when Layer A ran — consumers treat absence as
    # "validator didn't weigh in" rather than "zero issues".
    if boundary_result is not None:
        run["plan"]["boundary_validation"] = boundary_result.to_summary()

    # Story-critic. Phase 2 single-pass grading for paths the rework loop
    # doesn't cover yet (assembled / curated / rough_cut / tightener);
    # raw_dump pre-populates ``coherence_history`` via Phase 6's
    # ``_critic_with_rework`` so we just persist what it built.
    if coherence_history is None:
        coherence = _run_critic_or_skip(
            _critic_native_plan,
            transcript=scrubbed if _critic_native_takes is None else None,
            takes=_critic_native_takes,
            axes=resolved_axes,
            run_id=body.run_id,
            user_opt_in=settings_dict.get("story_critic_enabled"),
        )
        if coherence is not None:
            envelope = _wrap_coherence(coherence)
            run["plan"]["coherence_report"] = envelope
            run["plan"]["coherence_history"] = [envelope]
    else:
        # Rework-aware path: history carries 1 (no rework) or 2 (rework
        # fired) envelopes. The mirrored ``coherence_report`` points at
        # whichever entry scored highest — when the regression-guard
        # kept pass 1, that's pass 1, not the chronologically-last one.
        run["plan"]["coherence_history"] = coherence_history
        _, shipped_envelope = _pick_shipped_envelope(coherence_history)
        run["plan"]["coherence_report"] = shipped_envelope

    await _persist_plan(body.run_id, run["plan"])

    return run["plan"]
