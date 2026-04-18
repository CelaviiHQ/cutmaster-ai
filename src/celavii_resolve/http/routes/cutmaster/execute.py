"""POST /execute — build the cut timeline in Resolve, and POST /delete-cut — undo it."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from ....cutmaster.core import state
from ....cutmaster.core.execute import ExecuteError, execute_plan
from ._models import DeleteCutRequest, ExecuteRequest

log = logging.getLogger("celavii-resolve.http.cutmaster")

router = APIRouter()


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
                detail=(f"candidate_index {idx} out of range for {len(cands)} candidate(s)"),
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

    # Per-candidate timelines get a distinct suffix so repeat builds don't
    # clobber each other. Short Generator uses _AI_Short_N; Clip Hunter
    # keeps _AI_Clip_N for backward compat.
    name_suffix = "_AI_Cut"
    if clip_hunter:
        sel_idx = clip_hunter.get("selected_index", 0)
        mode = clip_hunter.get("mode", "clip_hunter")
        prefix = "Short" if mode == "short_generator" else "Clip"
        name_suffix = f"_AI_{prefix}_{sel_idx + 1}"

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

    from ....resolve import _boilerplate  # lazy

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

    return {
        "deleted": bool(ok),
        "timeline": new_name,
        "snapshot_preserved_at": exec_result.get("snapshot_path"),
    }
