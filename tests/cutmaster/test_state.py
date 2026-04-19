"""Tests for cutmaster.state — run creation, persistence, event queue."""

import asyncio
import json

import pytest

from celavii_resolve.cutmaster.core import state


@pytest.fixture
def isolated_run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    # clear any leaked queues / locks / tasks between tests
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()
    yield tmp_path
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()


def test_new_run_has_expected_fields(isolated_run_root):
    run = state.new_run("Timeline 1", preset="vlog")
    assert run["timeline_name"] == "Timeline 1"
    assert run["preset"] == "vlog"
    assert run["status"] == "pending"
    assert len(run["run_id"]) == 12
    assert run["events"] == []


def test_save_and_load_round_trip(isolated_run_root):
    run = state.new_run("T1")
    state.save(run)

    loaded = state.load(run["run_id"])
    assert loaded is not None
    assert loaded["run_id"] == run["run_id"]
    assert loaded["timeline_name"] == "T1"


def test_load_missing_returns_none(isolated_run_root):
    assert state.load("does_not_exist") is None


def test_save_is_atomic_no_tmp_file_left(isolated_run_root):
    run = state.new_run("T1")
    state.save(run)
    tmp = state.run_path(run["run_id"]).with_suffix(".tmp")
    assert not tmp.exists()


def test_append_event_updates_stages(isolated_run_root):
    run = state.new_run("T1")
    evt = state.make_event("vfr_check", "complete", message="ok", data={"x": 1})
    state.append_event(run, evt)
    assert run["events"] == [evt]
    assert run["stages"]["vfr_check"]["status"] == "complete"


@pytest.mark.asyncio
async def test_emit_pushes_to_queue_and_persists(isolated_run_root):
    run = state.new_run("T1")
    state.save(run)
    await state.emit(run, stage="stt", status="started", message="hi")

    # queue has the event
    q = state.get_queue(run["run_id"])
    assert q.qsize() == 1
    event = q.get_nowait()
    assert event["stage"] == "stt"
    assert event["status"] == "started"

    # persisted to disk
    reloaded = state.load(run["run_id"])
    assert reloaded is not None
    assert reloaded["events"][0]["stage"] == "stt"
    assert reloaded["stages"]["stt"]["status"] == "started"


def test_audio_path_for_creates_parent(isolated_run_root):
    p = state.audio_path_for("abc123")
    assert p.parent.exists()
    assert p.name == "abc123.wav"


def test_state_file_is_valid_json(isolated_run_root):
    run = state.new_run("T1")
    asyncio.run(state.emit(run, stage="stt", status="started", data={"k": [1, 2, 3]}))
    raw = state.run_path(run["run_id"]).read_text()
    parsed = json.loads(raw)
    assert parsed["events"][0]["data"] == {"k": [1, 2, 3]}


# ---------------------------------------------------------------------------
# state.update, task registry, cooperative cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_applies_mutator_and_persists(isolated_run_root):
    run = state.new_run("T1")
    state.save(run)

    def _mutate(d):
        d["status"] = "running"
        d["foo"] = 42

    updated = await state.update(run["run_id"], _mutate)
    assert updated is not None
    assert updated["status"] == "running"
    assert updated["foo"] == 42

    reloaded = state.load(run["run_id"])
    assert reloaded["status"] == "running"
    assert reloaded["foo"] == 42


@pytest.mark.asyncio
async def test_update_missing_run_returns_none(isolated_run_root):
    assert await state.update("nope", lambda d: None) is None


@pytest.mark.asyncio
async def test_emit_preserves_external_cancel(isolated_run_root):
    """A /cancel that lands between stages must survive the next emit.

    Reproduces the race: pipeline holds an in-memory dict with
    status='running'; /cancel writes status='cancelled' to disk; pipeline
    then emits the next stage event. The lock + disk-merge in emit() must
    not let the pipeline overwrite the cancel.
    """
    run = state.new_run("T1")
    run["status"] = "running"
    state.save(run)

    # External writer (acting like /cancel) flips the status on disk.
    await state.update(run["run_id"], lambda d: d.update({"status": "cancelled"}))

    # Pipeline emits its next stage event using its stale in-memory dict.
    assert run["status"] == "running"  # still stale locally
    await state.emit(run, stage="stt", status="started", message="about to start")

    # Both on-disk state AND the in-memory dict now reflect the cancel.
    assert run["status"] == "cancelled"
    reloaded = state.load(run["run_id"])
    assert reloaded["status"] == "cancelled"


@pytest.mark.asyncio
async def test_raise_if_cancelled(isolated_run_root):
    run = state.new_run("T1")
    state.save(run)

    # Not cancelled → no raise.
    state.raise_if_cancelled(run["run_id"])

    await state.update(run["run_id"], lambda d: d.update({"status": "cancelled"}))

    with pytest.raises(asyncio.CancelledError):
        state.raise_if_cancelled(run["run_id"])


@pytest.mark.asyncio
async def test_task_registry_set_get_drop(isolated_run_root):
    async def _noop():
        await asyncio.sleep(0)

    task = asyncio.create_task(_noop())
    state.set_task("abc", task)
    assert state.get_task("abc") is task

    await task  # completes → done_callback auto-drops
    await asyncio.sleep(0)  # let the callback fire
    assert state.get_task("abc") is None


@pytest.mark.asyncio
async def test_cancel_run_task_cancels_running_task(isolated_run_root):
    async def _long():
        await asyncio.sleep(10)

    task = asyncio.create_task(_long())
    state.set_task("abc", task)

    assert state.cancel_run_task("abc") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    # Done callback cleared the registry entry.
    assert state.get_task("abc") is None


@pytest.mark.asyncio
async def test_cancel_run_task_missing_returns_false(isolated_run_root):
    assert state.cancel_run_task("nope") is False
