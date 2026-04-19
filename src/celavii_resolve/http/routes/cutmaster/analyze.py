"""Analyze pipeline endpoints: POST /analyze, GET /events/{run_id}, GET /state/{run_id}."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ....cutmaster.core import state
from ....cutmaster.core.pipeline import run_analyze
from ._models import AnalyzeRequest, AnalyzeResponse

log = logging.getLogger("celavii-resolve.http.cutmaster")

router = APIRouter()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    """Kick off the analyze pipeline in the background, return a run_id.

    The client should then open an SSE connection at ``/cutmaster/events/{run_id}``
    to receive stage progress. Final state is always available at
    ``/cutmaster/state/{run_id}`` once the run finishes.
    """
    run = state.new_run(body.timeline_name, body.preset)
    state.save(run)

    log.info(
        "Analyze run %s starting for timeline '%s' (preset=%s)",
        run["run_id"],
        body.timeline_name,
        body.preset,
    )

    task = asyncio.create_task(
        run_analyze(
            run_id=run["run_id"],
            timeline_name=body.timeline_name,
            preset=body.preset,
            scrub_params=body.scrub_params,
            per_clip_stt=body.per_clip_stt,
            expected_speakers=body.expected_speakers,
            stt_provider=body.stt_provider,
        )
    )
    # Register so /cancel can .cancel() the in-flight task (Batch 1b).
    state.set_task(run["run_id"], task)

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


@router.post("/cancel/{run_id}")
async def cancel(run_id: str) -> dict:
    """Mark a run as cancelled. v3-2.2.

    This does NOT hard-abort any in-flight LLM / STT request — those complete
    on their own and become orphaned results. The marker is advisory: the UI
    uses it to free the user to return to the Preset screen immediately, and
    any subscribers to the SSE stream receive a terminal ``cancelled`` event
    so they can tear down their listeners.
    """
    cancel_event = {
        "stage": "cancelled",
        "status": "cancelled",
        "message": "Run cancelled by user",
        "data": None,
    }

    def _mutate(data: dict) -> None:
        if data.get("status") in {"complete", "failed", "cancelled", "done"}:
            # Short-circuit handled below by the caller via the sentinel.
            data["_cancel_noop"] = True
            return
        data["status"] = "cancelled"
        data["cancelled_at"] = state._now_iso()  # type: ignore[attr-defined]
        state.append_event(data, cancel_event)

    updated = await state.update(run_id, _mutate)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    if updated.pop("_cancel_noop", False):
        return {"run_id": run_id, "status": updated["status"], "noop": True}

    # Interrupt the in-flight analyze task at its next await point (Batch 1b).
    # The cooperative raise_if_cancelled checkpoints in the pipeline also
    # catch workers mid-`asyncio.to_thread` between stages.
    state.cancel_run_task(run_id)

    queue = state.get_queue(run_id)
    try:
        queue.put_nowait(cancel_event)
    except asyncio.QueueFull:  # pragma: no cover — queue is unbounded in practice
        pass

    log.info("Run %s marked as cancelled via /cancel", run_id)
    return {"run_id": run_id, "status": "cancelled", "noop": False}
