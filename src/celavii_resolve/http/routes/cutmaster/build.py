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

from fastapi import APIRouter, HTTPException

from ....cutmaster.analysis.marker_agent import MarkerPlan, suggest_markers
from ....cutmaster.analysis.scrubber import ScrubParams, scrub
from ....cutmaster.analysis.tightener import (
    DEFAULT_BLOCK_GAP_S,
    build_tightener_segments,
    tightener_stats,
)
from ....cutmaster.core import director as director_mod
from ....cutmaster.core import state
from ....cutmaster.core.director import (
    CutSegment,
    DirectorPlan,
    build_assembled_cut_plan,
    build_clip_hunter_plan,
    build_cut_plan,
    candidate_to_segments,
    expand_assembled_plan,
)
from ....cutmaster.data.presets import PRESETS, get_preset
from ....cutmaster.resolve_ops.assembled import (
    build_take_entries,
    read_items_on_track,
    split_transcript_per_item,
)
from ....cutmaster.resolve_ops.segments import resolve_segments
from ._helpers import _dump_director_prompt, _require_scrubbed
from ._models import BuildPlanRequest

log = logging.getLogger("celavii-resolve.http.cutmaster")

router = APIRouter()


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
            hunter_plan = await asyncio.to_thread(
                build_clip_hunter_plan,
                scrubbed,
                preset,
                settings_dict,
                target_clip_length_s,
                num_clips,
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
                resolved = await asyncio.to_thread(resolve_segments, tl, segs)
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

        items = read_items_on_track(tl, track_index=1)
        if not items:
            raise HTTPException(
                status_code=400,
                detail="timeline has no items on video track 1 — assembled mode needs takes",
            )
        per_item = split_transcript_per_item(transcript_for_takes, items)
        takes = build_take_entries(items, per_item)

        _dump_director_prompt(
            body.run_id,
            director_mod._assembled_prompt(preset, takes, settings_dict),
        )

        try:
            assembled_plan = await asyncio.to_thread(
                build_assembled_cut_plan, takes, preset, settings_dict
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
    else:
        # v1 raw-dump path — unchanged.
        _dump_director_prompt(
            body.run_id,
            director_mod._prompt(preset, scrubbed, settings_dict),
        )
        try:
            plan = await asyncio.to_thread(build_cut_plan, scrubbed, preset, settings_dict)
        except Exception as exc:
            log.exception("Director failed for run %s", body.run_id)
            raise HTTPException(status_code=500, detail=f"Director agent failed: {exc}")

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
