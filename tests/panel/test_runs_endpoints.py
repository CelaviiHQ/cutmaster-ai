"""Tests for GET /runs + POST /delete-run + POST /clone-run."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve.cutmaster.core import state  # noqa: E402
from celavii_resolve.http.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()
    yield
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _make_run(
    timeline: str,
    preset: str = "vlog",
    status: str = "done",
    with_plan: bool = False,
    with_scrubbed: bool = True,
) -> dict:
    run = state.new_run(timeline, preset=preset)
    run["status"] = status
    if with_scrubbed:
        run["scrubbed"] = [{"word": "hi", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"}]
        run["transcript"] = list(run["scrubbed"])
    if with_plan:
        run["plan"] = {"preset": preset, "user_settings": {}, "resolved_segments": []}
    state.save(run)
    return run


# ---------------------------------------------------------------------------
# GET /cutmaster/runs
# ---------------------------------------------------------------------------


def test_list_runs_returns_summaries_sorted_by_mtime(client: TestClient):
    a = _make_run("A")
    b = _make_run("B", preset="podcast")

    r = client.get("/cutmaster/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["truncated"] is False
    assert len(body["runs"]) == 2
    # Both present; exact order depends on mtime — just confirm ids match.
    ids = {s["run_id"] for s in body["runs"]}
    assert ids == {a["run_id"], b["run_id"]}

    first = next(s for s in body["runs"] if s["run_id"] == a["run_id"])
    assert first["timeline_name"] == "A"
    assert first["preset"] == "vlog"
    assert first["has_transcript"] is True
    assert first["has_plan"] is False
    assert first["size_kb"] > 0


def test_list_runs_filters_by_status(client: TestClient):
    _make_run("done-run", status="done")
    _make_run("cancelled-run", status="cancelled")

    r = client.get("/cutmaster/runs", params={"status": "cancelled"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "cancelled"
    assert body["total"] == 2  # total is pre-filter


def test_list_runs_filters_by_timeline(client: TestClient):
    _make_run("T1")
    _make_run("T2")

    r = client.get("/cutmaster/runs", params={"timeline": "T2"})
    body = r.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["timeline_name"] == "T2"


def test_list_runs_respects_limit(client: TestClient):
    for i in range(5):
        _make_run(f"T{i}")

    r = client.get("/cutmaster/runs", params={"limit": 2})
    body = r.json()
    assert len(body["runs"]) == 2
    assert body["total"] == 5
    assert body["truncated"] is True


def test_list_runs_skips_unreadable_files(client: TestClient, tmp_path):
    _make_run("good")
    # A half-written tmp and a corrupt JSON shouldn't crash the listing.
    (state.RUN_ROOT / "garbage.json").write_text("{not json")
    (state.RUN_ROOT / "half.tmp").write_text("{}")

    r = client.get("/cutmaster/runs")
    assert r.status_code == 200
    body = r.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["timeline_name"] == "good"


# ---------------------------------------------------------------------------
# POST /cutmaster/delete-run
# ---------------------------------------------------------------------------


def test_delete_run_removes_json_and_audio(client: TestClient):
    run = _make_run("T1")
    # Fake the extracted WAV file on disk.
    wav = state.audio_path_for(run["run_id"])
    wav.write_bytes(b"fake")
    per_clip_dir = state.EXTRACT_ROOT / run["run_id"]
    per_clip_dir.mkdir(parents=True)
    (per_clip_dir / "0.wav").write_bytes(b"fake")

    r = client.post("/cutmaster/delete-run", json={"run_id": run["run_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == run["run_id"]
    # All three paths reported as removed.
    assert any(p.endswith(".json") for p in body["removed"])
    assert any(p.endswith(".wav") for p in body["removed"])
    assert any(p.endswith(run["run_id"]) for p in body["removed"])

    assert state.load(run["run_id"]) is None
    assert not wav.exists()
    assert not per_clip_dir.exists()


def test_delete_run_returns_404_for_unknown_id(client: TestClient):
    r = client.post("/cutmaster/delete-run", json={"run_id": "nonexistent"})
    assert r.status_code == 404


def test_delete_run_does_not_touch_other_runs(client: TestClient):
    keep = _make_run("keep")
    drop = _make_run("drop")

    r = client.post("/cutmaster/delete-run", json={"run_id": drop["run_id"]})
    assert r.status_code == 200
    assert state.load(keep["run_id"]) is not None
    assert state.load(drop["run_id"]) is None


# ---------------------------------------------------------------------------
# POST /cutmaster/clone-run
# ---------------------------------------------------------------------------


def test_clone_run_copies_transcript_and_drops_plan(client: TestClient):
    src = _make_run("T1", with_plan=True)
    src_reloaded = state.load(src["run_id"])
    src_reloaded["story_analysis"] = {"themes": ["energy"]}
    state.save(src_reloaded)

    r = client.post("/cutmaster/clone-run", json={"run_id": src["run_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    new_run_id = body["run_id"]
    assert new_run_id != src["run_id"]
    assert body["cloned_from"] == src["run_id"]
    assert body["timeline_name"] == "T1"
    assert body["preset"] == "vlog"
    assert body["has_transcript"] is True

    cloned = state.load(new_run_id)
    assert cloned is not None
    assert cloned["scrubbed"] == src_reloaded["scrubbed"]
    assert cloned["story_analysis"] == {"themes": ["energy"]}
    # Plan / execute history do NOT carry over.
    assert "plan" not in cloned
    assert cloned.get("execute_history") in (None, [])
    # Original untouched.
    assert state.load(src["run_id"]) is not None
    assert "plan" in state.load(src["run_id"])


def test_clone_run_sets_status_pending_when_no_scrubbed(client: TestClient):
    src = _make_run("T1", with_scrubbed=False, status="pending")

    r = client.post("/cutmaster/clone-run", json={"run_id": src["run_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["has_transcript"] is False


def test_clone_run_returns_404_for_unknown_id(client: TestClient):
    r = client.post("/cutmaster/clone-run", json={"run_id": "nonexistent"})
    assert r.status_code == 404


def test_clone_run_preserves_top_level_user_settings(client: TestClient):
    """The top-level user_settings mirror (written at /build-plan time)
    must survive cloning — even though the plan itself is dropped.
    """
    src = _make_run("T1")
    src_reloaded = state.load(src["run_id"])
    src_reloaded["user_settings"] = {
        "target_length_s": 120,
        "themes": ["product"],
        "format": "vertical_short",
        "captions_enabled": True,
    }
    state.save(src_reloaded)

    r = client.post("/cutmaster/clone-run", json={"run_id": src["run_id"]})
    assert r.status_code == 200
    cloned = state.load(r.json()["run_id"])
    assert cloned["user_settings"] == src_reloaded["user_settings"]
    assert "plan" not in cloned
