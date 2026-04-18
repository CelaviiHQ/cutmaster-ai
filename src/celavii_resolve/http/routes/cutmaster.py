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
    CutSegment,
    DirectorPlan,
    build_assembled_cut_plan,
    build_clip_hunter_plan,
    build_cut_plan,
    candidate_to_segments,
    expand_assembled_plan,
)
from ...cutmaster.execute import ExecuteError, execute_plan
from ...cutmaster.formats import all_formats
from ...cutmaster.marker_agent import MarkerPlan, suggest_markers
from ...cutmaster.pipeline import run_analyze
from ...cutmaster.presets import PRESETS, all_presets, get_preset
from ...cutmaster.resolve_segments import resolve_segments
from ...cutmaster.scrubber import ScrubParams, scrub
from ...cutmaster.tightener import (
    DEFAULT_BLOCK_GAP_S,
    build_tightener_segments,
    tightener_stats,
)

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


class SpeakerRosterEntry(BaseModel):
    speaker_id: str
    word_count: int


class SpeakerRosterResponse(BaseModel):
    speakers: list[SpeakerRosterEntry]


@router.get("/speakers/{run_id}", response_model=SpeakerRosterResponse)
async def speakers(run_id: str) -> SpeakerRosterResponse:
    """Return the speaker roster detected in this run's scrubbed transcript.

    Drives the Configure screen's speaker-rename form (v2-5): entries are
    in first-appearance order, annotated with word-count so the editor can
    guess which one is host vs guest. Falls back to the raw transcript if
    scrubbing hasn't happened yet — single-speaker runs return an empty
    roster the UI can hide.
    """
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    from ...cutmaster.speakers import detect_speakers, speaker_stats

    transcript = run.get("scrubbed") or run.get("transcript") or []
    ids = detect_speakers(transcript)
    counts = speaker_stats(transcript)
    return SpeakerRosterResponse(
        speakers=[
            SpeakerRosterEntry(speaker_id=sid, word_count=counts.get(sid, 0))
            for sid in ids
        ],
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
    # v2-4: Clip Hunter — number of candidate clips to surface. target_length_s
    # is reused as the per-clip target duration when preset=clip_hunter.
    num_clips: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Clip Hunter only. How many candidate clips to return (1–5).",
    )
    # v2-5: speaker labels. Map of STT speaker_id → human label
    # ({"S1": "Host", "S2": "Guest"}). Director + Marker prompts read these
    # so the agents can reason about roles directly. Empty / None leaves
    # the raw STT ids in place (v1 behaviour).
    speaker_labels: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional {speaker_id: label} rename map. When set, Director + "
            "Marker prompts show the human labels instead of the raw STT ids."
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

    # v2-4: Clip Hunter — different optimisation target (N candidate clips
    # ranked by engagement, not one narrative cut). Each candidate is stored
    # on the plan so the Review UI can let the user pick; /execute reads the
    # chosen candidate_index to build exactly that clip's timeline.
    if body.preset == "clip_hunter":
        # Long-source gate (proposal §4.7). Hard-block beyond v2's 60-min
        # ceiling; warn the user in the plan output between 15 min and the
        # ceiling so they can downsize if Director quality dips.
        last_word_end = (
            float(scrubbed[-1].get("end_time", 0.0)) if scrubbed else 0.0
        )
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

        try:
            hunter_plan = await asyncio.to_thread(
                build_clip_hunter_plan,
                scrubbed, preset, settings_dict,
                target_clip_length_s, num_clips,
            )
        except Exception as exc:
            log.exception("Clip Hunter Director failed for run %s", body.run_id)
            raise HTTPException(
                status_code=500, detail=f"Clip Hunter Director failed: {exc}"
            )

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

        # Resolve per-candidate segments. Auto-split handles candidates that
        # happen to cross timeline-item boundaries in raw-dump sources.
        candidates_payload: list[dict] = []
        for cand in hunter_plan.candidates:
            segs = candidate_to_segments(cand)
            try:
                resolved = await asyncio.to_thread(resolve_segments, tl, segs)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"clip [{cand.start_s:.2f},{cand.end_s:.2f}]: {exc}",
                )
            candidates_payload.append({
                **cand.model_dump(),
                "resolved_segments": [r.model_dump() for r in resolved],
            })

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
        state.save(run)
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
                detail="timeline has no items on video track 1 — Tightener needs takes",
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
            resolved = await asyncio.to_thread(resolve_segments, tl, segments)
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
        state.save(run)
        return run["plan"]

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
            suggest_markers, plan, scrubbed, preset, settings_dict
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
    candidate_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Clip Hunter only: index of the candidate to build. Defaults to "
            "the top-ranked candidate (index 0) when omitted."
        ),
    )


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

    # Clip Hunter: swap resolved_segments to the selected candidate before
    # execute runs. execute_plan reads run["plan"]["resolved_segments"] as
    # its work queue, so this is the single surface that matters.
    clip_hunter = run["plan"].get("clip_hunter")
    if clip_hunter:
        idx = (
            body.candidate_index
            if body.candidate_index is not None
            else clip_hunter.get("selected_index", 0)
        )
        cands = clip_hunter.get("candidates") or []
        if idx < 0 or idx >= len(cands):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"candidate_index {idx} out of range for "
                    f"{len(cands)} candidate(s)"
                ),
            )
        chosen = cands[idx]
        run["plan"]["resolved_segments"] = chosen["resolved_segments"]
        run["plan"]["director"]["selected_clips"] = [
            {
                "start_s": float(s["start_s"]),
                "end_s": float(s["end_s"]),
                "reason": s.get("reason", ""),
            }
            for s in chosen["resolved_segments"]
        ]
        clip_hunter["selected_index"] = idx

    # Clip Hunter timelines get a per-candidate suffix so they don't
    # overwrite each other if the user executes multiple candidates.
    name_suffix = "_AI_Cut"
    if clip_hunter:
        sel_idx = clip_hunter.get("selected_index", 0)
        name_suffix = f"_AI_Clip_{sel_idx + 1}"

    try:
        result = await asyncio.to_thread(execute_plan, run, name_suffix)
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
