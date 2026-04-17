"""CutMaster run state — JSON persistence + in-memory event queues.

Each ``POST /cutmaster/analyze`` creates a run identified by ``run_id``.
Pipeline stages append events to an asyncio queue (consumed by the SSE
endpoint) and update a JSON state file on disk (durable across restarts).

Runs live at ``~/.celavii/cutmaster/<run_id>.json``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any


log = logging.getLogger("celavii-resolve.cutmaster.state")

RUN_ROOT = Path.home() / ".celavii" / "cutmaster"
EXTRACT_ROOT = Path.home() / ".celavii" / "cutmaster" / "audio"


# ---------------------------------------------------------------------------
# Run records
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def new_run(timeline_name: str, preset: str = "auto") -> dict:
    """Create an in-memory run record. Call ``save()`` to persist."""
    return {
        "run_id": uuid.uuid4().hex[:12],
        "timeline_name": timeline_name,
        "preset": preset,
        "created_at": _now_iso(),
        "status": "pending",
        "stages": {},
        "events": [],
        "transcript": [],
        "scrubbed": [],
        "error": None,
    }


def run_path(run_id: str) -> Path:
    return RUN_ROOT / f"{run_id}.json"


def save(state: dict) -> Path:
    """Atomically persist run state to disk."""
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = run_path(state["run_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(path)
    return path


def load(run_id: str) -> dict | None:
    path = run_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not load run %s: %s", run_id, exc)
        return None


def append_event(state: dict, event: dict) -> None:
    """Append an event to the state (in-memory). Call ``save()`` after."""
    state.setdefault("events", []).append(event)
    stage = event.get("stage")
    if stage:
        state.setdefault("stages", {})[stage] = {
            "status": event.get("status"),
            "ts": event.get("ts"),
            "data": event.get("data"),
            "message": event.get("message"),
        }


# ---------------------------------------------------------------------------
# In-memory event queues — for SSE streaming of live runs
# ---------------------------------------------------------------------------

_QUEUES: dict[str, asyncio.Queue] = {}


def get_queue(run_id: str) -> asyncio.Queue:
    """Return (or create) the event queue for a run."""
    if run_id not in _QUEUES:
        _QUEUES[run_id] = asyncio.Queue()
    return _QUEUES[run_id]


def drop_queue(run_id: str) -> None:
    _QUEUES.pop(run_id, None)


def make_event(
    stage: str,
    status: str,
    message: str = "",
    data: Any = None,
) -> dict:
    """Build a uniformly-shaped event record."""
    return {
        "stage": stage,
        "status": status,
        "message": message,
        "data": data,
        "ts": time.time(),
    }


async def emit(
    state: dict,
    *,
    stage: str,
    status: str,
    message: str = "",
    data: Any = None,
) -> dict:
    """Emit an event: push to queue, append to state, persist to disk.

    Returns the event dict so callers can include it in logs / test assertions.
    """
    event = make_event(stage, status, message, data)
    append_event(state, event)
    save(state)
    await get_queue(state["run_id"]).put(event)
    return event


# ---------------------------------------------------------------------------
# Audio file path for a run
# ---------------------------------------------------------------------------


def audio_path_for(run_id: str) -> Path:
    EXTRACT_ROOT.mkdir(parents=True, exist_ok=True)
    return EXTRACT_ROOT / f"{run_id}.wav"
