"""Retroactive story-critic endpoint — Phase 4 of story-critic.md.

POST /cutmaster/critique/{run_id} — load a previously-built plan, run the
critic against it again, persist + return the new report. The live build
path (``build.py``) wraps the critic behind ``CUTMASTER_ENABLE_STORY_CRITIC``
so old builds (or builds run with the flag off) carry no ``coherence_report``.
This endpoint backfills them on demand.

Only re-grades what's persisted on ``run["plan"]``; assembled / curated
builds drop their native take-aware plan after the build completes, so
the retroactive critique sees the synthetic flat ``DirectorPlan``. That
loses some structure but still produces a useful verdict — the live
path remains the canonical place to grade native shapes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ....cutmaster.core import state
from ....cutmaster.data.axis_resolution import ResolvedAxes
from ....intelligence import story_critic
from .build import _emit_completed, _persist_plan, _wrap_coherence

log = logging.getLogger("cutmaster-ai.http.cutmaster")

router = APIRouter()


def _reconstruct_plan(plan_dict: dict):
    """Rebuild a critic-ready plan from the persisted ``run["plan"]`` shape.

    Returns ``(plan, kind)`` where ``kind`` is ``"single"`` /
    ``"per_candidate"``. Raises :class:`ValueError` when the persisted
    shape can't be matched to any critic-supported plan.
    """
    from ....cutmaster.core.director import (
        ClipCandidate,
        ClipHunterPlan,
        DirectorPlan,
        ShortCandidate,
        ShortGeneratorPlan,
    )

    clip_hunter = plan_dict.get("clip_hunter")
    if clip_hunter:
        candidates = clip_hunter.get("candidates") or []
        if clip_hunter.get("mode") == "short_generator":
            shorts = [ShortCandidate.model_validate(c) for c in candidates]
            return ShortGeneratorPlan(candidates=shorts, reasoning=""), "per_candidate"
        clips = [ClipCandidate.model_validate(c) for c in candidates]
        return ClipHunterPlan(candidates=clips, reasoning=""), "per_candidate"

    director = plan_dict.get("director")
    if not director:
        raise ValueError("plan has neither `clip_hunter` nor `director` — nothing to grade")
    return DirectorPlan.model_validate(director), "single"


@router.post(
    "/critique/{run_id}",
    responses={
        404: {"description": "Run or built plan missing"},
        422: {"description": "Plan was built before resolved_axes existed — rebuild required"},
        500: {"description": "Critic LLM failed"},
    },
)
async def recritique(run_id: str) -> dict:
    """Re-run the story-critic against an already-built plan.

    Returns the wrapped coherence envelope (``{"kind": ..., "report": ...}``)
    and persists it onto ``run["plan"]["coherence_report"]`` so subsequent
    Review-screen loads see the new report.

    Status codes:
      * **404** — no run, or run has no built plan.
      * **422** — built plan lacks ``resolved_axes``. The critic needs the
        resolved cut intent to pick the right rubric; pre-Phase-4.6
        builds don't carry it. Rebuilding the plan re-runs the axis
        resolver and unblocks the critic.
      * **500** — the critic LLM call raised. Unlike the live path
        (which swallows + logs ``story_critic.skipped``), the retroactive
        endpoint surfaces the failure so the editor knows their click
        didn't produce a report.
    """
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    plan_dict = run.get("plan")
    if not plan_dict:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id} has no built plan — run /build-plan first",
        )

    axes_raw = plan_dict.get("resolved_axes")
    if not axes_raw:
        raise HTTPException(
            status_code=422,
            detail=(
                "plan was built before resolved_axes was persisted — "
                "rebuild the plan (Configure → Build) so the critic can "
                "read the cut intent"
            ),
        )
    try:
        axes = ResolvedAxes.model_validate(axes_raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"resolved_axes malformed: {exc}")

    try:
        plan, _kind = _reconstruct_plan(plan_dict)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    transcript = run.get("scrubbed") or run.get("transcript") or []

    # The live-path helper swallows LLM failures (build is structurally
    # valid; coherence is advisory). Retroactive callers explicitly asked
    # for a critique — surface failures as 500 so the panel can show the
    # error rather than silently render the previous report.
    import time

    from ....intelligence.llm import model_for

    model = model_for("story_critic")
    started = time.monotonic()
    try:
        report = story_critic.critique(
            plan,
            transcript=transcript,
            takes=None,
            axes=axes,
        )
    except Exception as exc:
        log.warning(
            "story_critic.recritique_failed run_id=%s err=%s",
            run_id,
            exc,
            extra={
                "event": "story_critic.skipped",
                "run_id": run_id,
                "reason": "llm_error",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "model": model,
                "trigger": "retroactive",
            },
        )
        raise HTTPException(status_code=500, detail=f"critic LLM failed: {exc}")
    latency_ms = int((time.monotonic() - started) * 1000)

    # Fire the same enriched completed-log shape the live path emits.
    _emit_completed(run_id, report, axes, model, latency_ms)

    envelope = _wrap_coherence(report)
    plan_dict["coherence_report"] = envelope
    await _persist_plan(run_id, plan_dict)
    return envelope
