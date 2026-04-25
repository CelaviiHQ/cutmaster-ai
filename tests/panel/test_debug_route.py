"""Route test for /cutmaster/debug/prompt/{run_id}.

The Director prompt is dumped per build by ``_dump_director_prompt``;
this endpoint just serves it back as text/plain so the Review screen
can show editors what Gemini actually saw.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cutmaster_ai.cutmaster.core import state  # noqa: E402
from cutmaster_ai.http.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    yield
    state._QUEUES.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_director_prompt_returns_dumped_text(client: TestClient) -> None:
    """When a build has dumped its prompt, the route returns it verbatim."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "run-with-prompt"
    body = "You are a podcast editor.\n\nSENTENCES: [...]"
    (state.RUN_ROOT / f"{run_id}.director_prompt.txt").write_text(body, encoding="utf-8")

    response = client.get(f"/cutmaster/debug/prompt/{run_id}")
    assert response.status_code == 200
    assert response.text == body
    # PlainTextResponse default content-type — keep editors out of JSON-parse hell.
    assert response.headers["content-type"].startswith("text/plain")


def test_director_prompt_returns_404_when_missing(client: TestClient) -> None:
    """No build run yet → no dump → 404 with a helpful detail string."""
    response = client.get("/cutmaster/debug/prompt/never-built")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "never-built" in detail
    assert "build" in detail.lower()
