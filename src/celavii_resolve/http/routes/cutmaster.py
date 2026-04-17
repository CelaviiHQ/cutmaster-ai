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

from ...cutmaster import state
from ...cutmaster.pipeline import run_analyze
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
