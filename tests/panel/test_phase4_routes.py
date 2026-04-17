"""Route tests for Phase 4: /presets, /detect-preset, /analyze-themes, /build-plan.

The LLM layer is mocked via monkeypatching the route module imports so we
don't call real Gemini and don't need Resolve.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve.cutmaster import state  # noqa: E402
from celavii_resolve.cutmaster.auto_detect import PresetRecommendation  # noqa: E402
from celavii_resolve.cutmaster.director import CutSegment, DirectorPlan  # noqa: E402
from celavii_resolve.cutmaster.marker_agent import MarkerPlan, MarkerSuggestion  # noqa: E402
from celavii_resolve.cutmaster.resolve_segments import ResolvedCutSegment  # noqa: E402
from celavii_resolve.cutmaster.themes import Chapter, HookCandidate, StoryAnalysis  # noqa: E402
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
def scrubbed_run():
    """A run record with a scrubbed transcript already populated."""
    run = state.new_run("Timeline 1", preset="auto")
    run["status"] = "done"
    run["scrubbed"] = [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
        {"word": "Look", "start_time": 1.2, "end_time": 1.45, "speaker_id": "S1"},
        {"word": "at", "start_time": 1.45, "end_time": 1.55, "speaker_id": "S1"},
        {"word": "this.", "start_time": 1.55, "end_time": 2.0, "speaker_id": "S1"},
    ]
    state.save(run)
    return run


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_list_presets(client: TestClient):
    r = client.get("/cutmaster/presets")
    assert r.status_code == 200
    keys = [p["key"] for p in r.json()["presets"]]
    assert {"vlog", "product_demo", "wedding", "interview",
            "tutorial", "podcast", "reaction"} <= set(keys)


def test_list_formats(client: TestClient):
    r = client.get("/cutmaster/formats")
    assert r.status_code == 200
    keys = [f["key"] for f in r.json()["formats"]]
    assert keys == ["horizontal", "vertical_short", "square"]
    # Shape check for the Configure screen's length-clamp logic.
    for f in r.json()["formats"]:
        assert "width" in f and "height" in f
        assert "safe_zones" in f


def test_build_plan_accepts_and_persists_v2_10_format_fields(
    client, monkeypatch, scrubbed_run,
):
    """v2-10 adds `format` / `captions_enabled` / `safe_zones_enabled` to
    UserSettings. The route must accept them, validate `format` as a
    Literal, and round-trip them through persisted state."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": 60,
                "themes": [],
                "format": "vertical_short",
                "captions_enabled": True,
                "safe_zones_enabled": True,
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved = persisted["plan"]["user_settings"]
    assert saved["format"] == "vertical_short"
    assert saved["captions_enabled"] is True
    assert saved["safe_zones_enabled"] is True


def test_build_plan_rejects_unknown_format(client, monkeypatch, scrubbed_run):
    """Pydantic's Literal guard should 422 on an invalid format key."""
    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": 60,
                "themes": [],
                "format": "ultrawide",  # not a valid key
            },
        },
    )
    assert r.status_code == 422


def test_detect_preset(client: TestClient, scrubbed_run, monkeypatch):
    recommendation = PresetRecommendation(
        preset="vlog", confidence=0.85, reasoning="energy + first-person"
    )
    monkeypatch.setattr(routes.auto_detect_mod, "detect_preset", lambda _t: recommendation)

    r = client.post("/cutmaster/detect-preset", json={"run_id": scrubbed_run["run_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == "vlog"
    assert body["confidence"] == 0.85


def test_detect_preset_requires_scrubbed(client: TestClient):
    # Create a run without running analyze — scrubbed is empty.
    run = state.new_run("T1")
    state.save(run)
    r = client.post("/cutmaster/detect-preset", json={"run_id": run["run_id"]})
    assert r.status_code == 400


def test_analyze_themes(client: TestClient, scrubbed_run, monkeypatch):
    fake = StoryAnalysis(
        chapters=[Chapter(start_s=0.0, end_s=2.0, title="Intro")],
        hook_candidates=[
            HookCandidate(start_s=0.0, end_s=0.95, text="Hello world.", engagement_score=0.8)
        ],
        theme_candidates=["greeting", "demonstration"],
    )
    monkeypatch.setattr(routes.themes_mod, "analyze_themes", lambda _t, _p: fake)

    r = client.post(
        "/cutmaster/analyze-themes",
        json={"run_id": scrubbed_run["run_id"], "preset": "vlog"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["chapters"]) == 1
    assert body["theme_candidates"] == ["greeting", "demonstration"]


def test_analyze_themes_rejects_unknown_preset(client: TestClient, scrubbed_run):
    r = client.post(
        "/cutmaster/analyze-themes",
        json={"run_id": scrubbed_run["run_id"], "preset": "not_a_preset"},
    )
    assert r.status_code == 400


def test_build_plan(client: TestClient, scrubbed_run, monkeypatch):
    # Mock Director
    director_plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=0.95, reason="opening"),
            CutSegment(start_s=1.2, end_s=2.0, reason="payoff"),
        ],
        reasoning="hook → payoff",
    )
    monkeypatch.setattr(routes, "build_cut_plan", lambda *a, **k: director_plan)

    # Mock Marker
    marker_plan = MarkerPlan(markers=[
        MarkerSuggestion(at_s=1.2, name="B-Roll: target", note=""),
    ])
    monkeypatch.setattr(routes, "suggest_markers", lambda *a, **k: marker_plan)

    # Mock Resolve + source-frame resolver
    fake_project = MagicMock()
    fake_tl = MagicMock()
    fake_tl.GetName.return_value = "Timeline 1"

    def fake_boilerplate():
        return None, fake_project, None

    # The route imports _boilerplate lazily from ..resolve — patch the source module
    import celavii_resolve.resolve as resolve_mod
    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(routes, "_find_timeline_by_name",
                        lambda _p, _n: fake_tl, raising=False)
    # pipeline._find_timeline_by_name is what's actually called
    import celavii_resolve.cutmaster.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)

    resolved = [
        ResolvedCutSegment(
            start_s=0.0, end_s=0.95, reason="opening",
            source_item_id="UID1", source_item_name="clip.mov",
            source_in_frame=0, source_out_frame=23,
            timeline_start_frame=86400, timeline_end_frame=86423,
            speed=1.0, speed_ramped=False, warnings=[],
        ),
        ResolvedCutSegment(
            start_s=1.2, end_s=2.0, reason="payoff",
            source_item_id="UID1", source_item_name="clip.mov",
            source_in_frame=29, source_out_frame=48,
            timeline_start_frame=86429, timeline_end_frame=86448,
            speed=1.0, speed_ramped=False, warnings=[],
        ),
    ]
    monkeypatch.setattr(routes, "resolve_segments", lambda _tl, _segs: resolved)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {"target_length_s": 90, "themes": ["greeting"]},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] == "vlog"
    assert len(body["director"]["selected_clips"]) == 2
    assert len(body["markers"]["markers"]) == 1
    assert len(body["resolved_segments"]) == 2

    # Plan persists on the run state
    persisted = state.load(scrubbed_run["run_id"])
    assert persisted["plan"]["preset"] == "vlog"


def test_build_plan_accepts_v2_fields_additively(client, monkeypatch, scrubbed_run):
    """v2-0 adds exclude_categories + custom_focus to UserSettings.
    The route must accept them and round-trip them through persisted state
    without requiring any pipeline wiring yet (that lands in v2-1).
    """
    # Minimal mocks — we only assert the new fields are accepted + persisted.
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="one beat",
    )
    monkeypatch.setattr(routes, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        routes,
        "suggest_markers",
        lambda *_a, **_k: MarkerPlan(markers=[]),
    )

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "wedding",
            "user_settings": {
                "target_length_s": 120,
                "themes": [],
                "exclude_categories": ["vendor_mentions", "mc_talking"],
                "custom_focus": "emphasise the vows",
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved_settings = persisted["plan"]["user_settings"]
    assert saved_settings["exclude_categories"] == ["vendor_mentions", "mc_talking"]
    assert saved_settings["custom_focus"] == "emphasise the vows"


def test_build_plan_omitted_v2_fields_use_safe_defaults(client, monkeypatch, scrubbed_run):
    """v1 clients don't send exclude_categories / custom_focus. Route must
    accept the old payload shape unchanged (defaults fill in)."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {"target_length_s": 60, "themes": []},
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved_settings = persisted["plan"]["user_settings"]
    assert saved_settings["exclude_categories"] == []
    assert saved_settings["custom_focus"] is None
