"""Analyze pipeline endpoints: POST /analyze, GET /events/{run_id}, GET /state/{run_id}."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ....cutmaster.core import state
from ....cutmaster.core.pipeline import run_analyze
from ....logging_setup import with_run_id
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

    async def _run_with_ctx(run_id: str) -> None:
        # Bind run_id for structured logs inside this task. ContextVars
        # are asyncio-aware so awaits inside run_analyze inherit the id.
        with with_run_id(run_id):
            await run_analyze(
                run_id=run_id,
                timeline_name=body.timeline_name,
                preset=body.preset,
                scrub_params=body.scrub_params,
                per_clip_stt=body.per_clip_stt,
                expected_speakers=body.expected_speakers,
                stt_provider=body.stt_provider,
                layer_c_enabled=body.layer_c_enabled,
                layer_audio_enabled=body.layer_audio_enabled,
            )

    task = asyncio.create_task(_run_with_ctx(run["run_id"]))
    # Register so /cancel can .cancel() the in-flight task.
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

    # Close on "cancelled" too so clients that cancel mid-run get a clean
    # terminal event instead of waiting on the keepalive.
    _TERMINAL_STAGES = {"done", "error", "cancelled"}

    async def gen():
        # Replay any events that already fired before the subscriber arrived.
        # Only send the historical ones; live events continue from the queue.
        persisted = state.load(run_id) or {}
        for past in persisted.get("events", []):
            yield {
                "event": past.get("stage", "event"),
                "data": json.dumps(past),
            }
            if past.get("stage") in _TERMINAL_STAGES:
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
            if event.get("stage") in _TERMINAL_STAGES:
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

    # Track what the mutator observed so we can report dispatch outcome.
    dispatched: dict[str, bool] = {"noop": False, "execute_running": False}

    def _mutate(data: dict) -> None:
        execute = data.get("execute") or {}
        exec_running = execute.get("status") == "running"
        # Top-level 'done' is not terminal: an execute can still fire from
        # a done analyze. Only 'failed' and 'cancelled' are fully terminal
        # — and even then, an in-flight execute wins over the terminal
        # status (rare but possible if something raced).
        top_terminal = data.get("status") in {"failed", "cancelled"}
        if top_terminal and not exec_running:
            dispatched["noop"] = True
            return
        if exec_running:
            # Signal execute_plan's cancel_check to abort at its next
            # checkpoint. The /execute handler then deletes any partially
            # built timeline before returning 409.
            execute["cancel_requested"] = True
            data["execute"] = execute
            dispatched["execute_running"] = True
        data["status"] = "cancelled"
        data["cancelled_at"] = state._now_iso()  # type: ignore[attr-defined]
        state.append_event(data, cancel_event)

    updated = await state.update(run_id, _mutate)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    if dispatched["noop"]:
        return {"run_id": run_id, "status": updated["status"], "noop": True}

    # Interrupt the in-flight analyze task at its next await point.
    # cancel_run_task returns False if nothing's registered (e.g. analyze
    # already finished or this run is past build-plan).
    analyze_cancelled = state.cancel_run_task(run_id)

    queue = state.get_queue(run_id)
    try:
        queue.put_nowait(cancel_event)
    except asyncio.QueueFull:  # pragma: no cover — queue is unbounded in practice
        pass

    log.info(
        "Run %s cancelled: analyze_task=%s execute_running=%s",
        run_id,
        analyze_cancelled,
        dispatched["execute_running"],
    )
    return {
        "run_id": run_id,
        "status": "cancelled",
        "noop": False,
        "analyze_task_cancelled": analyze_cancelled,
        "execute_running": dispatched["execute_running"],
    }
