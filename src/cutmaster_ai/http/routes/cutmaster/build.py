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
    resolve_sensory_layers,
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


# v4 Phase 4.4: per-layer activation flows through
# :func:`resolve_sensory_layers` so the matrix in ``data/presets.py`` stays
# the single source of truth. Clip Hunter's Layer-A entry is "off" in the
# matrix — each candidate is one span with no internal transitions to
# validate — so the resolver returns False there regardless of master.
# Assembled is similarly gated off. Short Generator (preset, not mode)
# and linear modes (raw_dump / rough_cut / curated) share the same
# resolver path.


def _layer_a_enabled_for_preset(settings: dict, preset_name: str) -> bool:
    """Preset-scoped Layer A gate (Short Generator / Clip Hunter path).

    Short Generator isn't a ``timeline_mode`` — it's a preset with its own
    multi-candidate structure. The resolver recognises the preset key
    directly via :func:`sensory_mode_key`; timeline_mode is unused here
    (passed as empty string for a deterministic lookup).
    """
    _, layer_a, _ = resolve_sensory_layers(
        master_enabled=bool(settings.get("sensory_master_enabled")),
        c_override=settings.get("layer_c_enabled"),
        a_override=settings.get("layer_a_enabled"),
        audio_override=settings.get("layer_audio_enabled"),
        preset=preset_name,
        timeline_mode="",
    )
    return layer_a


def _layer_a_enabled(settings: dict, mode: str) -> bool:
    """Whether the outer boundary-validator loop should wrap this run.

    Explicit ``layer_a_enabled`` override wins (tri-state: True / False /
    None-means-defer). Otherwise the matrix × master toggle resolves the
    effective flag. When neither is set, the Director runs unwrapped and
    the build path is byte-identical to v3.
    """
    _, layer_a, _ = resolve_sensory_layers(
        master_enabled=bool(settings.get("sensory_master_enabled")),
        c_override=settings.get("layer_c_enabled"),
        a_override=settings.get("layer_a_enabled"),
        audio_override=settings.get("layer_audio_enabled"),
        # ``preset`` field isn't on the settings dict (it's on the request
        # envelope). For mode-scoped resolution we feed only the
        # timeline_mode key so the matrix hits its linear-mode rows.
        preset="",
        timeline_mode=mode,
    )
    return layer_a


async def _director_or_validated(
    *,
    mode: str,
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

    Returns ``(plan, BoundaryValidationResult | None)``. Result is ``None``
    when Layer A is off so callers can skip the warnings surface.
    """
    if not _layer_a_enabled(settings, mode):
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


def _plan_warnings(*plans) -> list[str]:
    """Extract ``_validation_errors`` from any ``llm.call_structured`` result.

    When the Director (or any agent invoked with ``accept_best_effort=True``)
    exhausts its retry budget while still failing validation, ``llm.py``
    stamps the offending plan object with a ``_validation_errors`` list and
    returns it anyway. Without this helper those silent failures would only
    surface in server logs — the panel would render the best-of-bad plan as
    if everything succeeded. Walks every supplied plan, dedupes errors,
    returns ``[]`` when no plan carries warnings.
    """
    out: list[str] = []
    seen: set[str] = set()
    for p in plans:
        if p is None:
            continue
        errors = getattr(p, "_validation_errors", None) or []
        for e in errors:
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out


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


@router.post("/build-plan")
async def build_plan(body: BuildPlanRequest) -> dict:
    """Run Director → Marker → resolve source frames. Dry-run: no Resolve mutation.

    Writes the plan to the run's state file and returns it. Phase 6 (execute)
    will load the same state and actually build the timeline.
    """
    run, scrubbed = _require_scrubbed(body.run_id)
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown preset '{body.preset}'")
    preset = get_preset(body.preset)
    settings_dict = body.user_settings.model_dump()
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
            if _layer_a_enabled_for_preset(settings_dict, body.preset):

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
                if _layer_a_enabled(settings_dict, mode):

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
                if _layer_a_enabled(settings_dict, mode):

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

    # Marker agent runs against the flat CutSegment list in both modes.
    try:
        markers: MarkerPlan = await with_timeout(
            asyncio.to_thread(suggest_markers, plan, scrubbed, preset, settings_dict),
            MARKER_TIMEOUT_S,
            "Marker agent",
        )
    except Exception as exc:
        log.exception("Marker agent failed for run %s", body.run_id)
        raise HTTPException(status_code=500, detail=f"Marker agent failed: {exc}")

    # Resolve source frames — identical in both modes.
    try:
        resolved = await asyncio.to_thread(
            resolve_segments, tl, plan.selected_clips, video_track=video_track_idx
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"source-frame mapping failed: {exc}")

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

    # Story-critic — Phase 2. Grades the *native* plan shape so curated /
    # assembled adapters see ordered selections + word-index spans rather
    # than the synthesised flat DirectorPlan. Raw-dump grades `plan`
    # directly (it IS the native shape).
    coherence = _run_critic_or_skip(
        _critic_native_plan,
        transcript=scrubbed if _critic_native_takes is None else None,
        takes=_critic_native_takes,
        axes=resolved_axes,
        run_id=body.run_id,
        user_opt_in=settings_dict.get("story_critic_enabled"),
    )
    if coherence is not None:
        run["plan"]["coherence_report"] = _wrap_coherence(coherence)

    await _persist_plan(body.run_id, run["plan"])

    return run["plan"]
