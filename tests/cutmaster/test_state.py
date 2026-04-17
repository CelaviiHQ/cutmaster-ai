"""Tests for cutmaster.state — run creation, persistence, event queue."""

import asyncio
import json

import pytest

from celavii_resolve.cutmaster import state


@pytest.fixture
def isolated_run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    # clear any leaked queues between tests
    state._QUEUES.clear()
    yield tmp_path
    state._QUEUES.clear()


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
    asyncio.run(
        state.emit(run, stage="stt", status="started", data={"k": [1, 2, 3]})
    )
    raw = state.run_path(run["run_id"]).read_text()
    parsed = json.loads(raw)
    assert parsed["events"][0]["data"] == {"k": [1, 2, 3]}
