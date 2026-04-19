"""Run management endpoints: list, delete, clone.

These are per-run lifecycle operations distinct from the analyze/build/
execute pipeline. Scope is deliberately limited to run-state files on
disk — Resolve timelines and project snapshots are never touched from
this module.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ....cutmaster.core import state
from ._models import (
    CloneRunRequest,
    DeleteRunRequest,
    RunListResponse,
    RunSummary,
)

log = logging.getLogger("celavii-resolve.http.cutmaster")

router = APIRouter()


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = Query(100, ge=1, le=500),
    status: str | None = Query(None),
    timeline: str | None = Query(None),
) -> RunListResponse:
    """Return run summaries sorted by last-modified descending.

    Filters:
      - ``status`` — exact match against ``run['status']``
      - ``timeline`` — exact match against ``run['timeline_name']``
      - ``limit`` — cap on returned entries (default 100, max 500)

    Unreadable run files are silently skipped so one corrupt JSON
    doesn't kill the listing.
    """
    summaries = state.list_runs()
    total = len(summaries)

    if status:
        summaries = [s for s in summaries if s.get("status") == status]
    if timeline:
        summaries = [s for s in summaries if s.get("timeline_name") == timeline]

    truncated = len(summaries) > limit
    summaries = summaries[:limit]

    return RunListResponse(
        runs=[RunSummary(**s) for s in summaries],
        total=total,
        truncated=truncated,
    )


@router.post("/delete-run")
async def delete_run(body: DeleteRunRequest) -> dict:
    """Delete a run's state JSON and cached audio.

    Never touches Resolve — timelines this run may have built stay in
    the project. Use /cutmaster/delete-cut or /delete-all-cuts first if
    you also want to remove the rendered timelines.
    """
    if state.load(body.run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {body.run_id} not found")

    result = state.delete_run(body.run_id)
    log.info("Deleted run %s — removed %d file(s)", body.run_id, len(result["removed"]))
    return result


@router.post("/clone-run")
async def clone_run(body: CloneRunRequest) -> dict:
    """Clone a run's analysis state into a new run_id.

    Copies transcript, scrubbed words, preset, and source-timeline
    reference. Drops the plan, execute result, events, and history so
    the clone starts fresh at the Configure step — STT never re-runs.
    The original run is untouched.
    """
    cloned = state.clone_run(body.run_id)
    if cloned is None:
        raise HTTPException(status_code=404, detail=f"run {body.run_id} not found")

    log.info(
        "Cloned run %s → %s (timeline=%s preset=%s)",
        body.run_id,
        cloned["run_id"],
        cloned.get("timeline_name"),
        cloned.get("preset"),
    )
    return {
        "run_id": cloned["run_id"],
        "cloned_from": body.run_id,
        "timeline_name": cloned.get("timeline_name"),
        "preset": cloned.get("preset"),
        "status": cloned.get("status"),
        "has_transcript": bool(cloned.get("transcript") or cloned.get("scrubbed")),
    }
