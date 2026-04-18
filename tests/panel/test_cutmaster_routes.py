"""Route-level tests for /cutmaster/* — pipeline is mocked so we don't touch Resolve."""

import asyncio
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve.cutmaster.core import state  # noqa: E402
from celavii_resolve.http.app import create_app  # noqa: E402
from celavii_resolve.http.routes import cutmaster as routes  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    yield
    state._QUEUES.clear()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Client with a stub pipeline that emits a fake done event."""

    async def fake_pipeline(*, run_id: str, timeline_name: str, **_kwargs) -> None:
        run = state.load(run_id)
        if run is None:
            return
        await state.emit(run, stage="vfr_check", status="complete", message="stub pass")
        await state.emit(
            run, stage="done", status="complete", message=f"stubbed pipeline for {timeline_name}"
        )
        run["status"] = "done"
        state.save(run)

    monkeypatch.setattr(routes.analyze, "run_analyze", fake_pipeline)
    return TestClient(create_app())


def test_analyze_returns_run_id(client: TestClient):
    r = client.post("/cutmaster/analyze", json={"timeline_name": "Timeline 1", "preset": "vlog"})
    assert r.status_code == 200
    body = r.json()
    assert "run_id" in body
    assert body["status"] == "pending"


def test_analyze_persists_run_file(client: TestClient):
    r = client.post("/cutmaster/analyze", json={"timeline_name": "T1"})
    run_id = r.json()["run_id"]
    persisted = state.load(run_id)
    assert persisted is not None
    assert persisted["timeline_name"] == "T1"


def test_state_endpoint_returns_full_run(client: TestClient):
    r = client.post("/cutmaster/analyze", json={"timeline_name": "T1"})
    run_id = r.json()["run_id"]

    # Give the stub pipeline a moment to complete
    async def _wait():
        for _ in range(20):
            st = state.load(run_id)
            if st and st.get("status") == "done":
                return
            await asyncio.sleep(0.02)

    asyncio.run(_wait())

    s = client.get(f"/cutmaster/state/{run_id}")
    assert s.status_code == 200
    data = s.json()
    assert data["status"] == "done"
    stages = {e["stage"] for e in data["events"]}
    assert "vfr_check" in stages
    assert "done" in stages


def test_state_endpoint_404_for_unknown_run(client: TestClient):
    assert client.get("/cutmaster/state/does_not_exist").status_code == 404


def test_events_404_for_unknown_run(client: TestClient):
    assert client.get("/cutmaster/events/does_not_exist").status_code == 404


def test_events_replay_persisted_events(client: TestClient):
    """SSE endpoint replays events that were persisted before subscription."""
    r = client.post("/cutmaster/analyze", json={"timeline_name": "T1"})
    run_id = r.json()["run_id"]

    # Wait for stub pipeline to fully complete
    async def _wait():
        for _ in range(50):
            st = state.load(run_id)
            if st and st.get("status") == "done":
                return
            await asyncio.sleep(0.02)

    asyncio.run(_wait())

    with client.stream("GET", f"/cutmaster/events/{run_id}") as resp:
        assert resp.status_code == 200
        body = resp.read().decode()

    # Body contains SSE-formatted events with our stages.
    assert "vfr_check" in body
    assert "done" in body
    # The data: lines are JSON — find at least one and parse it
    data_lines = [line for line in body.splitlines() if line.startswith("data:")]
    assert data_lines
    payload = json.loads(data_lines[0][len("data: ") :])
    assert "stage" in payload
