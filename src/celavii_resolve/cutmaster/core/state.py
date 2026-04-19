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
from collections.abc import Callable
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


# ---------------------------------------------------------------------------
# Per-run async locks — serialise load→mutate→save across concurrent HTTP
# handlers (chiefly /cancel racing the pipeline's emit chain).
# ---------------------------------------------------------------------------

_LOCKS: dict[str, asyncio.Lock] = {}


def get_lock(run_id: str) -> asyncio.Lock:
    """Return (or create) the per-run asyncio.Lock.

    Locks are keyed by run_id and live for the life of the process. Creation
    is lazy; tests that want a clean slate should ``_LOCKS.clear()``.
    """
    lock = _LOCKS.get(run_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[run_id] = lock
    return lock


async def update(run_id: str, mutator: Callable[[dict], Any]) -> dict | None:
    """Atomically load → mutate → save a run under the per-run lock.

    ``mutator`` receives the loaded dict and mutates it in place (return
    value is ignored). Returns the persisted dict, or ``None`` if the run
    doesn't exist on disk.

    Use this in every HTTP handler that reads-then-writes a run file so
    concurrent handlers can't clobber each other. The pipeline's own
    ``emit()`` also takes the lock, so an out-of-band ``/cancel`` that
    lands mid-pipeline can't be trampled by the next stage's event.
    """
    async with get_lock(run_id):
        current = load(run_id)
        if current is None:
            return None
        mutator(current)
        save(current)
        return current


# ---------------------------------------------------------------------------
# Task registry — so /cancel can actually interrupt the analyze task.
# ---------------------------------------------------------------------------

_TASKS: dict[str, asyncio.Task] = {}


def set_task(run_id: str, task: asyncio.Task) -> None:
    """Register the asyncio.Task running the analyze pipeline for a run.

    Auto-drops on completion via ``add_done_callback`` so callers don't have
    to remember to clean up on the happy path.
    """
    _TASKS[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: _TASKS.pop(rid, None))


def get_task(run_id: str) -> asyncio.Task | None:
    return _TASKS.get(run_id)


def drop_task(run_id: str) -> None:
    _TASKS.pop(run_id, None)


def cancel_run_task(run_id: str) -> bool:
    """Cancel the analyze task for a run if one is registered.

    Returns True if a task was found and ``.cancel()`` was called on it;
    False if no task was registered (e.g. run is past analyze, or was never
    kicked off in-process).
    """
    task = _TASKS.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


# ---------------------------------------------------------------------------
# Cooperative cancel — checkpoint the pipeline calls between stages.
# ---------------------------------------------------------------------------


def raise_if_cancelled(run_id: str) -> None:
    """Raise ``asyncio.CancelledError`` if the run's persisted status is
    'cancelled'.

    Reads from disk so an out-of-band ``/cancel`` (which writes the flag
    via :func:`update`) is visible even when the caller holds a stale
    in-memory dict. Raising ``CancelledError`` (a BaseException in
    Python 3.11+) skips the pipeline's ``except Exception`` crash handler
    so it doesn't get reported as an error — cancellation is a clean exit.
    """
    current = load(run_id)
    if current and current.get("status") == "cancelled":
        raise asyncio.CancelledError(f"run {run_id} cancelled by user")


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

    Acquires the per-run lock so emits can't race with an out-of-band
    ``/cancel`` that's also writing the same file. If the on-disk record
    shows a ``cancelled`` status we weren't aware of, propagate it into
    the in-memory dict before saving — otherwise the pipeline would
    silently overwrite the cancel flag on its next stage transition.

    Returns the event dict so callers can include it in logs / test assertions.
    """
    event = make_event(stage, status, message, data)
    async with get_lock(state["run_id"]):
        persisted = load(state["run_id"])
        if persisted and persisted.get("status") == "cancelled":
            state["status"] = "cancelled"
            if "cancelled_at" in persisted:
                state["cancelled_at"] = persisted["cancelled_at"]
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


# ---------------------------------------------------------------------------
# Run listing / deletion / cloning (Batch 3)
# ---------------------------------------------------------------------------


def _summarise(run: dict, path: Path) -> dict:
    """Return a compact summary dict for a loaded run record."""
    try:
        stat = path.stat()
    except OSError:
        stat = None  # type: ignore[assignment]
    return {
        "run_id": run.get("run_id", path.stem),
        "created_at": run.get("created_at"),
        "timeline_name": run.get("timeline_name", ""),
        "preset": run.get("preset", "auto"),
        "status": run.get("status", "unknown"),
        "has_transcript": bool(run.get("transcript") or run.get("scrubbed")),
        "has_plan": bool(run.get("plan")),
        "execute_history": run.get("execute_history") or [],
        "size_kb": (stat.st_size / 1024.0) if stat else 0.0,
        "last_modified": stat.st_mtime if stat else 0.0,
    }


def list_runs() -> list[dict]:
    """Scan RUN_ROOT and return one summary dict per run file.

    Sorted by ``last_modified`` descending. Unreadable / malformed run
    files are skipped (never crash the listing).
    """
    if not RUN_ROOT.is_dir():
        return []
    summaries: list[dict] = []
    for path in RUN_ROOT.glob("*.json"):
        if path.suffix == ".tmp":
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Skipping unreadable run file %s: %s", path, exc)
            continue
        summaries.append(_summarise(data, path))
    summaries.sort(key=lambda s: s["last_modified"], reverse=True)
    return summaries


def delete_run(run_id: str) -> dict:
    """Remove a run's state JSON and any cached audio.

    Does not touch Resolve timelines or the .drp snapshot — the snapshot
    is the user's rollback path and stays on disk. Returns a dict listing
    the paths actually removed so the caller can report them.

    Also clears the in-memory queue / lock / task entries for the run so
    stale references don't leak across process lifetime.
    """
    removed: list[str] = []

    # Run JSON.
    path = run_path(run_id)
    if path.exists():
        try:
            path.unlink()
            removed.append(str(path))
        except OSError as exc:
            log.warning("Failed to unlink %s: %s", path, exc)

    # Concatenated-audio WAV.
    wav = EXTRACT_ROOT / f"{run_id}.wav"
    if wav.exists():
        try:
            wav.unlink()
            removed.append(str(wav))
        except OSError as exc:
            log.warning("Failed to unlink %s: %s", wav, exc)

    # Per-clip audio directory (created by the per_clip_stt path).
    per_clip_dir = EXTRACT_ROOT / run_id
    if per_clip_dir.is_dir():
        for child in per_clip_dir.iterdir():
            try:
                child.unlink()
            except OSError as exc:
                log.warning("Failed to unlink %s: %s", child, exc)
        try:
            per_clip_dir.rmdir()
            removed.append(str(per_clip_dir))
        except OSError as exc:
            log.warning("Failed to rmdir %s: %s", per_clip_dir, exc)

    # In-memory bookkeeping.
    _QUEUES.pop(run_id, None)
    _LOCKS.pop(run_id, None)
    task = _TASKS.pop(run_id, None)
    if task is not None and not task.done():
        task.cancel()

    return {"run_id": run_id, "removed": removed}


# Fields carried over when cloning. Everything else (events, stages,
# plan, execute, execute_history, error, cancelled_at, status) resets
# so the cloned run lands fresh in the Configure stage.
_CLONE_FIELDS = (
    "timeline_name",
    "preset",
    "transcript",
    "scrubbed",
    "story_analysis",
    "speaker_reconciliation",
    # Persisted Configure choices (mirrored up from plan.user_settings on
    # /build-plan) so a clone lands with the editor's last settings intact.
    "user_settings",
)


def clone_run(source_run_id: str) -> dict | None:
    """Deep-copy a run's analysis state into a new run_id.

    Returns the newly-saved run dict, or ``None`` if the source doesn't
    exist. The clone keeps transcript + scrubbed words (so STT never
    re-runs) and source metadata; it drops everything tied to a specific
    build — plan, execute, history, events.
    """
    import copy

    src = load(source_run_id)
    if src is None:
        return None

    new = new_run(src.get("timeline_name", ""), preset=src.get("preset", "auto"))
    for field in _CLONE_FIELDS:
        if field in src:
            new[field] = copy.deepcopy(src[field])
    new["status"] = "done" if new.get("scrubbed") else "pending"
    new["cloned_from"] = source_run_id
    save(new)
    return new
