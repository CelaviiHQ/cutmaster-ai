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


def test_director_prompt_rework_pass_returns_rework_dump(client: TestClient) -> None:
    """``?pass=rework`` serves the rework-pass dump (Phase 6 of story-critic)."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "run-with-rework"
    first = "You are a podcast editor.\n\nSENTENCES: [...]"
    rework = first + "\n\nPREVIOUS ATTEMPT — REWORK NEEDED: ..."
    (state.RUN_ROOT / f"{run_id}.director_prompt.txt").write_text(first, encoding="utf-8")
    (state.RUN_ROOT / f"{run_id}.director_prompt.rework.txt").write_text(rework, encoding="utf-8")

    # Default still serves the v1 dump.
    r1 = client.get(f"/cutmaster/debug/prompt/{run_id}")
    assert r1.status_code == 200
    assert r1.text == first

    # ?pass=rework serves the rework dump.
    r2 = client.get(f"/cutmaster/debug/prompt/{run_id}?pass=rework")
    assert r2.status_code == 200
    assert r2.text == rework


def test_director_prompt_rework_pass_404_when_no_rework_run(client: TestClient) -> None:
    """When the build didn't trigger rework, ``?pass=rework`` returns 404."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "ship-on-first"
    (state.RUN_ROOT / f"{run_id}.director_prompt.txt").write_text("first", encoding="utf-8")

    r = client.get(f"/cutmaster/debug/prompt/{run_id}?pass=rework")
    assert r.status_code == 404


def test_director_prompt_unknown_pass_rejected_with_400(client: TestClient) -> None:
    r = client.get("/cutmaster/debug/prompt/anything?pass=garbage")
    assert r.status_code == 400
    assert "garbage" in r.json()["detail"]


def test_director_prompt_numbered_pass_serves_per_iteration_dump(client: TestClient) -> None:
    """`?pass=N` serves the iterative-loop's per-iteration dump."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "iterative"
    pass1 = "iteration 1 prompt"
    pass2 = "iteration 2 prompt"
    pass3 = "iteration 3 prompt"
    (state.RUN_ROOT / f"{run_id}.director_prompt.1.txt").write_text(pass1, encoding="utf-8")
    (state.RUN_ROOT / f"{run_id}.director_prompt.2.txt").write_text(pass2, encoding="utf-8")
    (state.RUN_ROOT / f"{run_id}.director_prompt.3.txt").write_text(pass3, encoding="utf-8")

    for n, expected in [(1, pass1), (2, pass2), (3, pass3)]:
        r = client.get(f"/cutmaster/debug/prompt/{run_id}?pass={n}")
        assert r.status_code == 200, r.text
        assert r.text == expected


def test_director_prompt_pass_rework_aliases_pass_1(client: TestClient) -> None:
    """`?pass=rework` resolves to the numbered pass=1 dump for back-compat."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "alias"
    body = "iteration 1 prompt"
    (state.RUN_ROOT / f"{run_id}.director_prompt.1.txt").write_text(body, encoding="utf-8")

    r = client.get(f"/cutmaster/debug/prompt/{run_id}?pass=rework")
    assert r.status_code == 200
    assert r.text == body


def test_director_prompt_pass_1_falls_back_to_legacy_rework_dump(client: TestClient) -> None:
    """For runs persisted before the numbered convention, `?pass=1` reads
    the legacy ``.rework.txt`` sidecar."""
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "legacy"
    body = "legacy rework dump"
    (state.RUN_ROOT / f"{run_id}.director_prompt.rework.txt").write_text(body, encoding="utf-8")

    r = client.get(f"/cutmaster/debug/prompt/{run_id}?pass=1")
    assert r.status_code == 200
    assert r.text == body


def test_director_prompt_numbered_pass_404_when_iteration_didnt_fire(
    client: TestClient,
) -> None:
    state.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "two-passes"
    (state.RUN_ROOT / f"{run_id}.director_prompt.1.txt").write_text("p1", encoding="utf-8")

    r = client.get(f"/cutmaster/debug/prompt/{run_id}?pass=3")
    assert r.status_code == 404
    assert "pass=3" in r.json()["detail"]


def test_director_prompt_pass_zero_rejected(client: TestClient) -> None:
    r = client.get("/cutmaster/debug/prompt/x?pass=0")
    assert r.status_code == 400
    assert "≥ 1" in r.json()["detail"]
