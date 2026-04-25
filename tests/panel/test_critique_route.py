"""Phase 4 — `POST /cutmaster/critique/{run_id}` retroactive endpoint tests.

Plus telemetry-shape coverage for ``story_critic.completed`` /
``story_critic.skipped`` log records (the live build path's enriched log,
shared with the retroactive endpoint via ``_emit_completed``).

The critic LLM is mocked at the ``story_critic.critique`` boundary —
no Gemini calls, no Resolve.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cutmaster_ai.cutmaster.analysis.marker_agent import MarkerPlan  # noqa: E402
from cutmaster_ai.cutmaster.core import state  # noqa: E402
from cutmaster_ai.cutmaster.core.director import (  # noqa: E402
    CutSegment,
    DirectorPlan,
)
from cutmaster_ai.http.app import create_app  # noqa: E402
from cutmaster_ai.http.routes import cutmaster as routes  # noqa: E402
from cutmaster_ai.intelligence import story_critic  # noqa: E402


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


def _good_report() -> story_critic.CoherenceReport:
    return story_critic.CoherenceReport(
        score=82,
        hook_strength=85,
        arc_clarity=80,
        transitions=78,
        resolution=84,
        issues=[],
        summary="Solid.",
        verdict="ship",
    )


def _seed_run_with_built_plan(*, with_axes: bool = True) -> dict:
    """Create a run that already has a persisted DirectorPlan + scrubbed
    transcript. Optionally drop ``resolved_axes`` to simulate a pre-Phase-4.6
    legacy plan (used for the 422 test)."""
    run = state.new_run("Timeline 1", preset="vlog")
    run["status"] = "done"
    run["scrubbed"] = [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
    ]
    plan_dict: dict = {
        "preset": "vlog",
        "user_settings": {},
        "director": DirectorPlan(
            hook_index=0,
            selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
            reasoning="ok",
        ).model_dump(),
        "markers": MarkerPlan(markers=[]).model_dump(),
        "resolved_segments": [],
    }
    if with_axes:
        plan_dict["resolved_axes"] = {
            "content_type": "vlog",
            "cut_intent": "narrative",
            "cut_intent_source": "user",
            "reorder_mode": "preserve_macro",
            "segment_pacing": {"min": 6.0, "target": 22.0, "max": 60.0},
            "selection_strategy": "narrative-arc",
            "prompt_builder": "_prompt",
            "rationale": [],
            "unusual": False,
        }
    run["plan"] = plan_dict
    state.save(run)
    return run


# ---------------------------------------------------------------------------
# 4.3 — route 200 / 404 / 422
# ---------------------------------------------------------------------------


def test_critique_route_404_when_run_missing(client):
    r = client.post("/cutmaster/critique/does-not-exist")
    assert r.status_code == 404


def test_critique_route_404_when_plan_missing(client):
    run = state.new_run("Timeline 1", preset="vlog")
    state.save(run)  # no plan yet
    r = client.post(f"/cutmaster/critique/{run['run_id']}")
    assert r.status_code == 404


def test_critique_route_422_when_resolved_axes_missing(client):
    run = _seed_run_with_built_plan(with_axes=False)
    r = client.post(f"/cutmaster/critique/{run['run_id']}")
    assert r.status_code == 422
    assert "resolved_axes" in r.json()["detail"]


def test_critique_route_200_returns_envelope_and_persists(client, monkeypatch):
    run = _seed_run_with_built_plan()
    monkeypatch.setattr(
        story_critic,
        "critique",
        lambda plan, *, transcript=None, takes=None, axes=None, _llm=None: _good_report(),
    )

    r = client.post(f"/cutmaster/critique/{run['run_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "single"
    assert body["report"]["score"] == 82
    assert body["report"]["verdict"] == "ship"

    persisted = state.load(run["run_id"])
    assert persisted["plan"]["coherence_report"]["kind"] == "single"
    assert persisted["plan"]["coherence_report"]["report"]["score"] == 82


def test_critique_route_500_when_llm_raises(client, monkeypatch):
    run = _seed_run_with_built_plan()

    def _boom(*_a, **_k):
        raise RuntimeError("gemini timeout")

    monkeypatch.setattr(story_critic, "critique", _boom)

    r = client.post(f"/cutmaster/critique/{run['run_id']}")
    assert r.status_code == 500
    assert "critic LLM failed" in r.json()["detail"]
    persisted = state.load(run["run_id"])
    # Failure must NOT leave a stale envelope on the plan.
    assert "coherence_report" not in persisted["plan"]


# ---------------------------------------------------------------------------
# 4.5 — telemetry log shape
# ---------------------------------------------------------------------------


def test_completed_log_carries_full_payload(client, monkeypatch, caplog):
    """`story_critic.completed` must emit a structured `extra` payload
    with every field downstream telemetry needs (Phase 4.1)."""
    run = _seed_run_with_built_plan()
    monkeypatch.setattr(
        story_critic,
        "critique",
        lambda plan, *, transcript=None, takes=None, axes=None, _llm=None: _good_report(),
    )

    with caplog.at_level("INFO", logger="cutmaster-ai.http.cutmaster"):
        r = client.post(f"/cutmaster/critique/{run['run_id']}")
    assert r.status_code == 200

    completed = [
        rec for rec in caplog.records if getattr(rec, "event", None) == "story_critic.completed"
    ]
    assert len(completed) == 1
    rec = completed[0]
    assert rec.run_id == run["run_id"]
    assert rec.cut_intent == "narrative"
    assert rec.content_type == "vlog"
    assert rec.kind == "single"
    assert rec.score == 82
    assert rec.verdict == "ship"
    assert rec.n_issues == 0
    assert rec.hook_strength == 85
    assert rec.arc_clarity == 80
    assert rec.transitions == 78
    assert rec.resolution == 84
    assert isinstance(rec.latency_ms, int)
    assert isinstance(rec.model, str) and rec.model


def test_skipped_log_carries_reason_and_run_id(client, monkeypatch, caplog):
    """Live-path skip-on-flag-off emits a structured `extra` with reason."""
    monkeypatch.delenv("CUTMASTER_ENABLE_STORY_CRITIC", raising=False)

    # Drive a live build to exercise the helper's flag-off branch.
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"
    import cutmaster_ai.cutmaster.core.pipeline as pipeline_mod
    import cutmaster_ai.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "_boilerplate", lambda: (MagicMock(), MagicMock(), MagicMock())
    )
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs, **_kw: [])

    run = state.new_run("Timeline 1", preset="auto")
    run["scrubbed"] = [
        {"word": "Hi", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "there.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
    ]
    state.save(run)

    with caplog.at_level("INFO", logger="cutmaster-ai.http.cutmaster"):
        r = client.post(
            "/cutmaster/build-plan",
            json={
                "run_id": run["run_id"],
                "preset": "vlog",
                "content_type": "vlog",
                "cut_intent": "narrative",
                "user_settings": {
                    "target_length_s": 60,
                    "themes": [],
                    "cut_intent": "narrative",
                },
            },
        )
    assert r.status_code == 200

    skipped = [
        rec for rec in caplog.records if getattr(rec, "event", None) == "story_critic.skipped"
    ]
    assert any(rec.reason == "flag_off" and rec.run_id == run["run_id"] for rec in skipped)
