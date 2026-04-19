"""POST /execute — build the cut timeline in Resolve, and POST /delete-cut — undo it."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException

from ....cutmaster.core import state
from ....cutmaster.core.execute import ExecuteCancelled, ExecuteError, execute_plan
from ._models import DeleteAllCutsRequest, DeleteCutRequest, ExecuteRequest

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

    # User-supplied name overrides the auto-suffix. For candidate builds we
    # still append the index so "Build all" doesn't collide.
    custom_name: str | None = None
    if body.custom_name and body.custom_name.strip():
        base = body.custom_name.strip()
        if clip_hunter:
            sel_idx = clip_hunter.get("selected_index", 0)
            custom_name = f"{base}_{sel_idx + 1}"
        else:
            custom_name = base

    # Mark execute as in-flight so /cancel can target it. The marker is a
    # dict so we can attach ``cancel_requested`` from /cancel without
    # clobbering it. Success path overwrites this with the real execute
    # result below.
    def _mark_running(d: dict) -> None:
        d["execute"] = {"status": "running", "started_at": time.time()}

    await state.update(body.run_id, _mark_running)

    def _cancel_check() -> bool:
        # Disk read per checkpoint (~5 per build). Cheap enough and avoids
        # plumbing a shared in-memory flag across the thread boundary.
        cur = state.load(body.run_id)
        if cur is None:
            return False
        return bool((cur.get("execute") or {}).get("cancel_requested"))

    try:
        result = await asyncio.to_thread(execute_plan, run, name_suffix, custom_name, _cancel_check)
    except ExecuteCancelled as exc:
        log.info("execute cancelled for run %s: %s", body.run_id, exc)

        def _record_abort(d: dict) -> None:
            d.pop("execute", None)
            d.setdefault("execute_history", []).append(
                {
                    "new_timeline_name": None,
                    "custom_name": custom_name,
                    "aborted": True,
                    "at": time.time(),
                }
            )
            # /cancel already set status=cancelled; leave it.

        await state.update(body.run_id, _record_abort)
        raise HTTPException(
            status_code=409,
            detail={"cancelled": True, "reason": str(exc)},
        )
    except ExecuteError as exc:
        # Clear the in-progress marker so the run can be retried.
        await state.update(body.run_id, lambda d: d.pop("execute", None))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("execute crashed for run %s", body.run_id)
        await state.update(body.run_id, lambda d: d.pop("execute", None))
        raise HTTPException(status_code=500, detail=f"execute failed: {exc}")

    new_name = result.get("new_timeline_name")

    # Replace-existing: remove prior timelines that share the requested base
    # name, but only after the new build succeeded. We never delete the cut
    # we just created. Records the removals so the UI can report them.
    replaced: list[str] = []
    if body.replace_existing and new_name and custom_name and custom_name.strip():
        desired = custom_name.strip()
        if new_name != desired:
            replaced = await asyncio.to_thread(
                _delete_timelines_by_name,
                desired,
                new_name,
            )
            result["replaced_timelines"] = replaced

    history_entry = {
        "new_timeline_name": new_name,
        "custom_name": custom_name,
        "replaced_timelines": replaced,
        "snapshot_path": result.get("snapshot_path"),
        "at": time.time(),
    }

    # Atomic final write. We also persist the Clip Hunter candidate swap
    # (``plan.resolved_segments`` + selected_index) that this handler made
    # in memory earlier — apply it to the fresh on-disk dict so concurrent
    # writers don't see a stale snapshot. The cancel_check above catches
    # /cancel landing mid-build; this final write runs only after a
    # successful build, so overwriting status is safe.
    plan_after_swap = run.get("plan")

    def _mutate(d: dict) -> None:
        if plan_after_swap is not None:
            d["plan"] = plan_after_swap
        d["execute"] = result
        d.setdefault("execute_history", []).append(history_entry)
        d["status"] = "done"

    await state.update(body.run_id, _mutate)

    return result


def _delete_timelines_by_name(target_name: str, keep_name: str) -> list[str]:
    """Delete every timeline matching ``target_name`` except ``keep_name``.

    Runs inside ``asyncio.to_thread`` so the Resolve calls don't block the
    event loop. Returns the names actually removed.
    """
    from ....resolve import _boilerplate  # lazy

    _, project, media_pool = _boilerplate()
    removed: list[str] = []
    # Snapshot the timeline list first — DeleteTimelines reshuffles indices.
    to_delete = []
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if not tl:
            continue
        name = tl.GetName()
        if name == target_name and name != keep_name:
            to_delete.append((name, tl))
    for name, tl in to_delete:
        try:
            if media_pool.DeleteTimelines([tl]):
                removed.append(name)
        except Exception:
            log.exception("failed to delete prior timeline %s", name)
    return removed


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
        await state.update(body.run_id, lambda d: d.pop("execute", None))
        return {"deleted": False, "reason": f"timeline '{new_name}' not found"}

    ok = media_pool.DeleteTimelines([target])

    def _mutate(d: dict) -> None:
        d.pop("execute", None)
        hist = d.get("execute_history") or []
        d["execute_history"] = [h for h in hist if h.get("new_timeline_name") != new_name]

    await state.update(body.run_id, _mutate)

    return {
        "deleted": bool(ok),
        "timeline": new_name,
        "snapshot_preserved_at": exec_result.get("snapshot_path"),
    }


@router.post("/delete-all-cuts")
async def delete_all_cuts(body: DeleteAllCutsRequest) -> dict:
    """Delete every timeline this run has ever built.

    Walks ``run['execute_history']`` and deletes any matching timeline still
    present in the project. Missing timelines (already deleted by hand) are
    skipped without error. The .drp snapshot from each build stays on disk.
    """
    run = state.load(body.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {body.run_id} not found")

    history: list[dict] = run.get("execute_history") or []
    if not history:
        raise HTTPException(
            status_code=400,
            detail="no cut history recorded for this run",
        )

    from ....resolve import _boilerplate  # lazy

    try:
        _, project, media_pool = _boilerplate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

    wanted = {h.get("new_timeline_name") for h in history if h.get("new_timeline_name")}
    to_delete = []
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() in wanted:
            to_delete.append((tl.GetName(), tl))

    removed: list[str] = []
    for name, tl in to_delete:
        try:
            if media_pool.DeleteTimelines([tl]):
                removed.append(name)
        except Exception:
            log.exception("failed to delete timeline %s", name)

    def _mutate(d: dict) -> None:
        d.pop("execute", None)
        d["execute_history"] = []

    await state.update(body.run_id, _mutate)

    return {
        "deleted": removed,
        "skipped": sorted(wanted - set(removed)),
    }
