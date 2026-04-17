"""HTTP routes for CutMaster — POST /analyze, GET /events/{id}, GET /state/{id}.

Kicks off the analyze pipeline as an asyncio task and streams stage events
over Server-Sent Events. Phase 4 will extend this with /detect-preset,
/build-plan, and /execute.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ...cutmaster import auto_detect as auto_detect_mod
from ...cutmaster import state
from ...cutmaster import themes as themes_mod
from ...cutmaster.director import DirectorPlan, build_cut_plan
from ...cutmaster.execute import ExecuteError, execute_plan
from ...cutmaster.marker_agent import MarkerPlan, suggest_markers
from ...cutmaster.pipeline import run_analyze
from ...cutmaster.presets import PRESETS, all_presets, get_preset
from ...cutmaster.resolve_segments import resolve_segments
from ...cutmaster.scrubber import ScrubParams


log = logging.getLogger("celavii-resolve.http.cutmaster")

router = APIRouter(prefix="/cutmaster", tags=["cutmaster"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    timeline_name: str
    preset: str = "auto"
    scrub_params: ScrubParams | None = Field(default=None)


class AnalyzeResponse(BaseModel):
    run_id: str
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Kick off the analyze pipeline in the background, return a run_id.

    The client should then open an SSE connection at ``/cutmaster/events/{run_id}``
    to receive stage progress. Final state is always available at
    ``/cutmaster/state/{run_id}`` once the run finishes.
    """
    run = state.new_run(body.timeline_name, body.preset)
    state.save(run)

    log.info("Analyze run %s starting for timeline '%s' (preset=%s)",
             run["run_id"], body.timeline_name, body.preset)

    asyncio.create_task(
        run_analyze(
            run_id=run["run_id"],
            timeline_name=body.timeline_name,
            preset=body.preset,
            scrub_params=body.scrub_params,
        )
    )

    return AnalyzeResponse(run_id=run["run_id"], status="pending")


@router.get("/events/{run_id}")
async def events(run_id: str):
    """SSE stream of pipeline events for a run.

    The stream closes when a ``done`` or ``error`` event arrives.
    """
    if state.load(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    queue = state.get_queue(run_id)

    async def gen():
        # Replay any events that already fired before the subscriber arrived.
        # Only send the historical ones; live events continue from the queue.
        persisted = state.load(run_id) or {}
        for past in persisted.get("events", []):
            yield {
                "event": past.get("stage", "event"),
                "data": json.dumps(past),
            }
            if past.get("stage") in {"done", "error"}:
                return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=300.0)
            except asyncio.TimeoutError:
                yield {"event": "keepalive", "data": "{}"}
                continue

            yield {
                "event": event.get("stage", "event"),
                "data": json.dumps(event),
            }
            if event.get("stage") in {"done", "error"}:
                state.drop_queue(run_id)
                return

    return EventSourceResponse(gen())


@router.get("/state/{run_id}")
async def get_state(run_id: str) -> dict:
    """Return the current persisted state for a run (including full transcript)."""
    data = state.load(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return data


# ---------------------------------------------------------------------------
# Phase 4 routes
# ---------------------------------------------------------------------------


@router.get("/presets")
async def list_presets() -> dict:
    """List all preset bundles (metadata only — useful for the panel's picker)."""
    return {"presets": [p.model_dump() for p in all_presets()]}


def _require_scrubbed(run_id: str) -> tuple[dict, list[dict]]:
    """Load a run and return ``(state_dict, scrubbed_words)`` or HTTP 400."""
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    scrubbed = run.get("scrubbed") or []
    if not scrubbed:
        raise HTTPException(
            status_code=400,
            detail=f"run {run_id} has no scrubbed transcript — analyze first",
        )
    return run, scrubbed


class DetectPresetRequest(BaseModel):
    run_id: str


@router.post("/detect-preset")
async def detect_preset(body: DetectPresetRequest) -> dict:
    """Classify the scrubbed transcript into a preset recommendation."""
    _, scrubbed = _require_scrubbed(body.run_id)
    rec = await asyncio.to_thread(auto_detect_mod.detect_preset, scrubbed)
    return rec.model_dump()


class AnalyzeThemesRequest(BaseModel):
    run_id: str
    preset: str


@router.post("/analyze-themes")
async def analyze_themes(body: AnalyzeThemesRequest) -> dict:
    """Produce chapters + hook candidates + theme axes for the Configure screen."""
    _, scrubbed = _require_scrubbed(body.run_id)
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown preset '{body.preset}'")
    preset = get_preset(body.preset)
    analysis = await asyncio.to_thread(themes_mod.analyze_themes, scrubbed, preset)
    return analysis.model_dump()


class UserSettings(BaseModel):
    target_length_s: int | None = None
    themes: list[str] = []
    scrub_params: ScrubParams | None = None


class BuildPlanRequest(BaseModel):
    run_id: str
    preset: str
    user_settings: UserSettings = Field(default_factory=UserSettings)


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

    # 1. Director
    try:
        plan: DirectorPlan = await asyncio.to_thread(
            build_cut_plan, scrubbed, preset, body.user_settings.model_dump()
        )
    except Exception as exc:
        log.exception("Director failed for run %s", body.run_id)
        raise HTTPException(status_code=500, detail=f"Director agent failed: {exc}")

    # 2. Marker
    try:
        markers: MarkerPlan = await asyncio.to_thread(
            suggest_markers, plan, scrubbed, preset
        )
    except Exception as exc:
        log.exception("Marker agent failed for run %s", body.run_id)
        raise HTTPException(status_code=500, detail=f"Marker agent failed: {exc}")

    # 3. Resolve source frames (reads Resolve; does not mutate)
    from ...resolve import _boilerplate  # lazy
    from ...cutmaster.pipeline import _find_timeline_by_name

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
        resolved = await asyncio.to_thread(resolve_segments, tl, plan.selected_clips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"source-frame mapping failed: {exc}")

    # 4. Persist the plan onto the run state so /state/{id} and /execute see it
    run["plan"] = {
        "preset": body.preset,
        "user_settings": body.user_settings.model_dump(),
        "director": plan.model_dump(),
        "markers": markers.model_dump(),
        "resolved_segments": [r.model_dump() for r in resolved],
    }
    state.save(run)

    return run["plan"]


class ExecuteRequest(BaseModel):
    run_id: str


@router.post("/execute")
async def execute(body: ExecuteRequest) -> dict:
    """Build the cut timeline in Resolve using the persisted plan.

    Pre-flight checks + project snapshot to ``.drp`` run before any mutation.
    Never edits the source timeline in place.
    """
    run = state.load(body.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {body.run_id} not found")
    if not run.get("plan"):
        raise HTTPException(
            status_code=400,
            detail=f"run {body.run_id} has no plan — call /cutmaster/build-plan first",
        )

    try:
        result = await asyncio.to_thread(execute_plan, run)
    except ExecuteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("execute crashed for run %s", body.run_id)
        raise HTTPException(status_code=500, detail=f"execute failed: {exc}")

    run["execute"] = result
    run["status"] = "done"
    state.save(run)

    return result


class DeleteCutRequest(BaseModel):
    run_id: str


@router.post("/delete-cut")
async def delete_cut(body: DeleteCutRequest) -> dict:
    """Delete the timeline created by the most recent execute for this run.

    The .drp snapshot is left untouched — it's the user's real rollback path.
    """
    run = state.load(body.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {body.run_id} not found")
    exec_result = run.get("execute")
    if not exec_result:
        raise HTTPException(status_code=400, detail="no cut timeline recorded for this run")

    new_name = exec_result.get("new_timeline_name")
    if not new_name:
        raise HTTPException(status_code=400, detail="execute result missing new_timeline_name")

    from ...resolve import _boilerplate  # lazy

    try:
        _, project, media_pool = _boilerplate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

    target = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == new_name:
            target = t
            break

    if target is None:
        # Already gone — still return ok so the UI can reset
        run.pop("execute", None)
        state.save(run)
        return {"deleted": False, "reason": f"timeline '{new_name}' not found"}

    ok = media_pool.DeleteTimelines([target])
    run.pop("execute", None)
    state.save(run)

    return {"deleted": bool(ok), "timeline": new_name,
            "snapshot_preserved_at": exec_result.get("snapshot_path")}
