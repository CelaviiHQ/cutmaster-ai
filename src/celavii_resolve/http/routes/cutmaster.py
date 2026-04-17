"""HTTP routes for CutMaster — POST /analyze, GET /events/{id}, GET /state/{id}.

Kicks off the analyze pipeline as an asyncio task and streams stage events
over Server-Sent Events. Phase 4 will extend this with /detect-preset,
/build-plan, and /execute.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ...cutmaster import auto_detect as auto_detect_mod
from ...cutmaster import state
from ...cutmaster import themes as themes_mod
from ...cutmaster.assembled import (
    build_take_entries,
    read_items_on_track,
    split_transcript_per_item,
)
from ...cutmaster.director import (
    DirectorPlan,
    build_assembled_cut_plan,
    build_cut_plan,
    expand_assembled_plan,
)
from ...cutmaster.execute import ExecuteError, execute_plan
from ...cutmaster.formats import all_formats
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
            except TimeoutError:
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


@router.get("/formats")
async def list_formats() -> dict:
    """List all output-format specs (horizontal / vertical_short / square)."""
    return {"formats": [f.model_dump() for f in all_formats()]}


class SourceAspectResponse(BaseModel):
    width: int
    height: int
    aspect: float
    recommended_format: str


@router.get("/source-aspect/{run_id}", response_model=SourceAspectResponse)
async def source_aspect(run_id: str) -> SourceAspectResponse:
    """Read the source timeline's pixel dimensions and recommend a Format.

    Used by the Configure screen to preselect the Format picker and to
    suppress the aspect-mismatch reframe when source and target already
    match (e.g. a 9:16 phone vlog into a Short).
    """
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    from ...cutmaster.formats import recommend_format
    from ...cutmaster.pipeline import _find_timeline_by_name
    from ...resolve import _boilerplate  # lazy — avoids import-time Resolve dependency

    try:
        _, project, _ = _boilerplate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

    tl = _find_timeline_by_name(project, run["timeline_name"])
    if tl is None:
        raise HTTPException(
            status_code=400,
            detail=f"timeline '{run['timeline_name']}' not found",
        )

    # Resolve timelines expose pixel dims through GetSetting; fall back to
    # project-level settings if the timeline inherits ("useCustomSettings"=0).
    def _read_int(obj, key: str) -> int:
        try:
            v = obj.GetSetting(key)
        except Exception:
            return 0
        try:
            return int(v) if v else 0
        except (TypeError, ValueError):
            return 0

    w = _read_int(tl, "timelineResolutionWidth") or _read_int(project, "timelineResolutionWidth")
    h = _read_int(tl, "timelineResolutionHeight") or _read_int(project, "timelineResolutionHeight")
    if w <= 0 or h <= 0:
        raise HTTPException(
            status_code=400,
            detail="could not read timeline resolution — check Project Settings",
        )

    rec = recommend_format(w, h)
    return SourceAspectResponse(
        width=w,
        height=h,
        aspect=w / h,
        recommended_format=rec.key,
    )


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
    # v2-0 groundwork: content-category exclusion + free-text focus.
    # The Director prompt wiring lands in v2-1; these fields are accepted
    # and round-tripped through state now so older clients (v1 panel) keep
    # working and newer clients can start sending them.
    exclude_categories: list[str] = Field(
        default_factory=list,
        description="Preset-defined ExcludeCategory.key values the user has ticked.",
    )
    custom_focus: str | None = Field(
        default=None,
        description="Free-text focus hint fed to the Director in v2-1.",
    )
    # v2-10: output format adaptation. Defaults to horizontal so v1 clients
    # and first-time users get their existing behaviour. The execute step
    # consumes this to set the new timeline's resolution and drive caption
    # + crop handling.
    format: Literal["horizontal", "vertical_short", "square"] = Field(
        default="horizontal",
        description="Output format key.",
    )
    captions_enabled: bool = Field(
        default=False,
        description="When true, execute writes an SRT next to the snapshot and populates a subtitle track.",
    )
    safe_zones_enabled: bool = Field(
        default=False,
        description="When true and format is non-horizontal, execute drops platform-UI safe-zone guides on V2.",
    )
    # v2-2: assembled-mode controls. Defaults preserve v1 behaviour.
    timeline_mode: Literal["raw_dump", "assembled"] = Field(
        default="raw_dump",
        description=(
            "'raw_dump' (v1 default) — Director picks word-level ranges anywhere, "
            "auto-splits across item boundaries. 'assembled' — Director never "
            "crosses take boundaries; within-take scrubbing and optional reordering "
            "remain user-controllable via reorder_allowed and takes_already_scrubbed."
        ),
    )
    reorder_allowed: bool = Field(
        default=True,
        description=(
            "Assembled mode only. When false, the server-side validator rejects "
            "plans whose take order differs from input order (retry loop re-prompts)."
        ),
    )
    takes_already_scrubbed: bool = Field(
        default=False,
        description=(
            "Assembled mode only. When true, build-plan uses the raw transcript "
            "(no filler / dead-air cleanup) because the editor already polished "
            "each take. Default false — editor picked takes but hasn't scrubbed."
        ),
    )


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
    settings_dict = body.user_settings.model_dump()
    mode = body.user_settings.timeline_mode

    # v2-2: assembled mode uses a different Director. Both paths converge on
    # the same CutSegment + resolver pipeline from step 2 onward.
    if mode == "assembled":
        if body.user_settings.takes_already_scrubbed:
            transcript_for_takes = run.get("transcript") or []
            if not transcript_for_takes:
                raise HTTPException(
                    status_code=400,
                    detail="takes_already_scrubbed=true but run has no raw transcript",
                )
        else:
            transcript_for_takes = scrubbed

        from ...cutmaster.pipeline import _find_timeline_by_name
        from ...resolve import _boilerplate  # lazy

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

        items = read_items_on_track(tl, track_index=1)
        if not items:
            raise HTTPException(
                status_code=400,
                detail="timeline has no items on video track 1 — assembled mode needs takes",
            )
        per_item = split_transcript_per_item(transcript_for_takes, items)
        takes = build_take_entries(items, per_item)

        try:
            assembled_plan = await asyncio.to_thread(
                build_assembled_cut_plan, takes, preset, settings_dict
            )
        except Exception as exc:
            log.exception("Assembled Director failed for run %s", body.run_id)
            raise HTTPException(
                status_code=500, detail=f"Assembled Director failed: {exc}"
            )

        selected_clips, hook_cut_index = expand_assembled_plan(assembled_plan, takes)
        plan = DirectorPlan(
            hook_index=hook_cut_index,
            selected_clips=selected_clips,
            reasoning=assembled_plan.reasoning,
        )
    else:
        # v1 raw-dump path — unchanged.
        try:
            plan = await asyncio.to_thread(
                build_cut_plan, scrubbed, preset, settings_dict
            )
        except Exception as exc:
            log.exception("Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Director agent failed: {exc}")

        from ...cutmaster.pipeline import _find_timeline_by_name
        from ...resolve import _boilerplate  # lazy

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

    # Marker agent runs against the flat CutSegment list in both modes.
    try:
        markers: MarkerPlan = await asyncio.to_thread(
            suggest_markers, plan, scrubbed, preset
        )
    except Exception as exc:
        log.exception("Marker agent failed for run %s", body.run_id)
        raise HTTPException(status_code=500, detail=f"Marker agent failed: {exc}")

    # Resolve source frames — identical in both modes.
    try:
        resolved = await asyncio.to_thread(resolve_segments, tl, plan.selected_clips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"source-frame mapping failed: {exc}")

    run["plan"] = {
        "preset": body.preset,
        "user_settings": settings_dict,
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
