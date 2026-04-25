"""Phase 2 of story-critic — `/build-plan` integration tests.

The critic is wrapped behind ``CUTMASTER_ENABLE_STORY_CRITIC``. With the
flag off the build path is byte-identical to pre-Phase 2; with it on,
each builder return path persists a ``coherence_report`` block on the
plan. LLM failures must NEVER fail the structural build — coherence is
advisory.

The critic LLM is mocked at the ``story_critic.critique`` boundary so
no Gemini calls happen; the existing ``_llm`` injection seam is reserved
for unit tests.
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
    AssembledDirectorPlan,
    AssembledItemSelection,
    ClipCandidate,
    ClipHunterPlan,
    CutSegment,
    DirectorPlan,
    WordSpan,
)
from cutmaster_ai.cutmaster.resolve_ops.segments import ResolvedCutSegment  # noqa: E402
from cutmaster_ai.http.app import create_app  # noqa: E402
from cutmaster_ai.http.routes import cutmaster as routes  # noqa: E402
from cutmaster_ai.intelligence import story_critic  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures + scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    yield
    state._QUEUES.clear()


@pytest.fixture
def scrubbed_run():
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


def _flag_on(monkeypatch):
    monkeypatch.setenv("CUTMASTER_ENABLE_STORY_CRITIC", "1")


def _flag_off(monkeypatch):
    monkeypatch.delenv("CUTMASTER_ENABLE_STORY_CRITIC", raising=False)


def _good_report() -> story_critic.CoherenceReport:
    return story_critic.CoherenceReport(
        score=82,
        hook_strength=85,
        arc_clarity=80,
        transitions=78,
        resolution=84,
        issues=[],
        summary="Reads cleanly.",
        verdict="ship",
    )


def _per_candidate_report(n: int) -> story_critic.PerCandidateCoherenceReport:
    return story_critic.PerCandidateCoherenceReport(
        candidates=[_good_report() for _ in range(n)],
        best_candidate_index=0,
        summary=f"{n} candidates graded.",
    )


def _mock_critic(monkeypatch, *, return_value=None, raises: Exception | None = None):
    """Patch ``story_critic.critique`` and capture invocations.

    Returns a list that accumulates ``(plan_type_name, kwargs)`` tuples
    so tests can assert which native plan shape the route fed in.
    """
    calls: list[tuple[str, dict]] = []

    def _fake(plan, *, transcript=None, takes=None, axes=None, _llm=None):
        calls.append(
            (
                type(plan).__name__,
                {
                    "transcript_len": len(transcript) if transcript else 0,
                    "takes_len": len(takes) if takes else 0,
                    "cut_intent": axes.cut_intent if axes else None,
                },
            )
        )
        if raises is not None:
            raise raises
        return return_value if return_value is not None else _good_report()

    monkeypatch.setattr(story_critic, "critique", _fake)
    return calls


def _stub_resolver_and_resolve(monkeypatch):
    """Stub Resolve boilerplate + frame resolver so build-plan never touches Resolve."""
    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    import cutmaster_ai.cutmaster.core.pipeline as pipeline_mod
    import cutmaster_ai.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "_boilerplate", lambda: (MagicMock(), MagicMock(), MagicMock())
    )
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs, **_kw: [])
    return fake_tl


# ---------------------------------------------------------------------------
# Flag-off — no critic call, no coherence_report
# ---------------------------------------------------------------------------


def test_build_plan_flag_off_skips_critic_no_report(client, monkeypatch, scrubbed_run):
    _flag_off(monkeypatch)
    calls = _mock_critic(monkeypatch)

    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    assert "coherence_report" not in persisted["plan"]
    assert calls == []  # critic was never called


# ---------------------------------------------------------------------------
# Flag-on — raw_dump path (DirectorPlan, single-cut shape)
# ---------------------------------------------------------------------------


def test_build_plan_flag_on_raw_dump_attaches_single_report(client, monkeypatch, scrubbed_run):
    _flag_on(monkeypatch)
    calls = _mock_critic(monkeypatch)

    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    coherence = persisted["plan"].get("coherence_report")
    assert coherence is not None
    assert coherence["kind"] == "single"
    assert coherence["report"]["score"] == 82
    assert coherence["report"]["verdict"] == "ship"

    assert len(calls) == 1
    plan_type, ctx = calls[0]
    assert plan_type == "DirectorPlan"
    assert ctx["transcript_len"] > 0
    assert ctx["takes_len"] == 0


# ---------------------------------------------------------------------------
# Flag-on — LLM raises → build still 200, no report attached
# ---------------------------------------------------------------------------


def test_build_plan_flag_on_llm_failure_does_not_fail_build(
    client, monkeypatch, scrubbed_run, caplog
):
    _flag_on(monkeypatch)
    _mock_critic(monkeypatch, raises=RuntimeError("gemini timeout"))

    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    with caplog.at_level("WARNING"):
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
    assert "coherence_report" not in persisted["plan"]
    assert any(
        "story_critic.skipped" in rec.message and "llm_error" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# Flag-on — clip_hunter (per-candidate dispatch)
# ---------------------------------------------------------------------------


def test_build_plan_flag_on_clip_hunter_attaches_per_candidate_report(
    client, monkeypatch, scrubbed_run
):
    _flag_on(monkeypatch)
    calls = _mock_critic(monkeypatch, return_value=_per_candidate_report(2))

    hunter = ClipHunterPlan(
        candidates=[
            ClipCandidate(start_s=0.0, end_s=0.95, engagement_score=0.9, quote="Q1"),
            ClipCandidate(start_s=1.2, end_s=2.0, engagement_score=0.7, quote="Q2"),
        ],
        reasoning="two picks",
    )
    monkeypatch.setattr(routes.build, "build_clip_hunter_plan", lambda *_a, **_k: hunter)
    fake_tl = _stub_resolver_and_resolve(monkeypatch)

    # clip_hunter's resolver returns one segment per call so /execute can swap.
    def fake_resolver(_tl, segs, **_kw):
        return [
            ResolvedCutSegment(
                start_s=float(s.start_s),
                end_s=float(s.end_s),
                reason=s.reason,
                source_item_id=f"UID_{s.start_s}",
                source_item_name="t.mov",
                source_in_frame=0,
                source_out_frame=10,
                timeline_start_frame=0,
                timeline_end_frame=10,
                speed=1.0,
                speed_ramped=False,
                warnings=[],
            )
            for s in segs
        ]

    monkeypatch.setattr(routes.build, "resolve_segments", fake_resolver)
    _ = fake_tl

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "clip_hunter",
            "content_type": "vlog",
            "user_settings": {"target_length_s": 1, "num_clips": 2},
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    coherence = persisted["plan"].get("coherence_report")
    assert coherence is not None
    assert coherence["kind"] == "per_candidate"
    assert len(coherence["report"]["candidates"]) == 2

    assert len(calls) == 1
    plan_type, ctx = calls[0]
    assert plan_type == "ClipHunterPlan"
    assert ctx["cut_intent"] == "multi_clip"


# ---------------------------------------------------------------------------
# Per-build opt-in via UserSettings.story_critic_enabled
# ---------------------------------------------------------------------------


def test_build_plan_user_opt_in_runs_critic_when_env_flag_off(client, monkeypatch, scrubbed_run):
    """`UserSettings.story_critic_enabled=true` runs the critic even when
    the server-wide env var is unset. Mirrors the Configure-screen toggle."""
    _flag_off(monkeypatch)
    calls = _mock_critic(monkeypatch)

    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "content_type": "vlog",
            "cut_intent": "narrative",
            "user_settings": {
                "target_length_s": 60,
                "themes": [],
                "cut_intent": "narrative",
                "story_critic_enabled": True,
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    coherence = persisted["plan"].get("coherence_report")
    assert coherence is not None
    assert coherence["kind"] == "single"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Flag-on — assembled (grades native AssembledDirectorPlan, NOT flat)
# ---------------------------------------------------------------------------


def test_build_plan_flag_on_assembled_grades_native_plan_shape(client, monkeypatch, scrubbed_run):
    _flag_on(monkeypatch)
    calls = _mock_critic(monkeypatch)

    assembled = AssembledDirectorPlan(
        hook_index=0,
        selections=[
            AssembledItemSelection(item_index=0, kept_word_spans=[WordSpan(a=0, b=1)]),
        ],
        reasoning="hook",
    )
    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", lambda *_a, **_k: assembled)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    monkeypatch.setattr(
        routes.build,
        "read_items_on_track",
        lambda _tl, track_index=1: [
            {"item_index": 0, "source_name": "t.mov", "start_s": 0.0, "end_s": 2.0}
        ],
    )
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "content_type": "vlog",
            "cut_intent": "narrative",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "assembled",
                "reorder_allowed": True,
                "cut_intent": "narrative",
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    assert persisted["plan"].get("coherence_report", {}).get("kind") == "single"

    assert len(calls) == 1
    plan_type, ctx = calls[0]
    # The route MUST grade the native AssembledDirectorPlan (with takes),
    # not the synthesised flat DirectorPlan.
    assert plan_type == "AssembledDirectorPlan"
    assert ctx["takes_len"] == 1
    assert ctx["transcript_len"] == 0


# ---------------------------------------------------------------------------
# Phase 6 — auto-rework loop on raw_dump
# ---------------------------------------------------------------------------


def _seq_critic(monkeypatch, reports: list[story_critic.CoherenceReport]):
    """Patch ``story_critic.critique`` to return a sequence of reports, one
    per call. Captures ``_critic_feedback`` from the Director's settings on
    each Director invocation so tests can assert the rework prompt was wired.
    """
    calls: list[story_critic.CoherenceReport] = []
    counter = {"i": 0}

    def _fake(plan, *, transcript=None, takes=None, axes=None, _llm=None):
        i = counter["i"]
        counter["i"] += 1
        rpt = reports[min(i, len(reports) - 1)]
        calls.append(rpt)
        return rpt

    monkeypatch.setattr(story_critic, "critique", _fake)
    return calls


def _rework_report() -> story_critic.CoherenceReport:
    return story_critic.CoherenceReport(
        score=58,
        hook_strength=55,
        arc_clarity=60,
        transitions=55,
        resolution=50,
        issues=[
            story_critic.CoherenceIssue(
                segment_index=0,
                severity="error",
                category="weak_hook",
                message="opener doesn't pull",
            )
        ],
        summary="Hook is weak.",
        verdict="rework",
    )


def _capture_director_calls(monkeypatch):
    """Capture every build_cut_plan call site so tests can assert the
    rework pass received ``_critic_feedback`` in its settings dict."""
    captured: list[dict] = []
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )

    def _fake_build(transcript, preset, settings, resolved=None):
        captured.append(dict(settings))
        return plan

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)
    return captured


def test_build_plan_critic_ship_skips_rework(client, monkeypatch, scrubbed_run):
    """First-pass verdict=ship → no rework, history has 1 envelope."""
    _flag_on(monkeypatch)
    director_calls = _capture_director_calls(monkeypatch)
    critic_calls = _seq_critic(monkeypatch, [_good_report()])  # verdict=ship
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text

    # Director called once (no rework), critic called once
    assert len(director_calls) == 1
    assert "_critic_feedback" not in director_calls[0]
    assert len(critic_calls) == 1

    persisted = state.load(scrubbed_run["run_id"])
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 1
    assert history[0]["report"]["verdict"] == "ship"
    assert persisted["plan"]["coherence_report"]["report"]["verdict"] == "ship"


def test_build_plan_critic_rework_triggers_director_repick(
    client, monkeypatch, scrubbed_run, caplog
):
    """First-pass verdict=rework → Director re-called with feedback,
    second critic grades the new plan, history has 2 envelopes."""
    _flag_on(monkeypatch)
    director_calls = _capture_director_calls(monkeypatch)
    # First report triggers rework; second report ships
    critic_calls = _seq_critic(monkeypatch, [_rework_report(), _good_report()])
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    with caplog.at_level("INFO", logger="cutmaster-ai.http.cutmaster"):
        r = client.post(
            "/cutmaster/build-plan",
            json={
                "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text

    # Director ran twice; second invocation MUST carry _critic_feedback
    assert len(director_calls) == 2
    assert "_critic_feedback" not in director_calls[0]
    fb = director_calls[1].get("_critic_feedback")
    assert fb is not None
    assert fb["verdict"] == "rework"
    assert fb["score"] == 58
    assert fb["issues"][0]["category"] == "weak_hook"

    # Critic graded both passes
    assert len(critic_calls) == 2

    # Persisted state carries both envelopes; final report is the second
    persisted = state.load(scrubbed_run["run_id"])
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 2
    assert history[0]["report"]["verdict"] == "rework"
    assert history[1]["report"]["verdict"] == "ship"
    assert persisted["plan"]["coherence_report"]["report"]["verdict"] == "ship"

    # Telemetry: rework_triggered + rework_completed both fired
    triggered = [
        rec
        for rec in caplog.records
        if getattr(rec, "event", None) == "story_critic.rework_triggered"
    ]
    completed = [
        rec
        for rec in caplog.records
        if getattr(rec, "event", None) == "story_critic.rework_completed"
    ]
    assert len(triggered) == 1
    assert len(completed) == 1
    assert completed[0].score_delta == 24  # 82 - 58
    assert completed[0].crossed_to_ship is True


def test_build_plan_critic_rework_max_zero_disables_loop(client, monkeypatch, scrubbed_run):
    """CUTMASTER_STORY_CRITIC_REWORK_MAX=0 → no rework even on verdict=rework."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "0")
    director_calls = _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_rework_report()])
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text

    # Director called once even though critic flagged rework
    assert len(director_calls) == 1

    persisted = state.load(scrubbed_run["run_id"])
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 1
    assert history[0]["report"]["verdict"] == "rework"


def test_build_plan_critic_rework_director_failure_ships_original(
    client, monkeypatch, scrubbed_run, caplog
):
    """When the Director's rework call raises, ship the original plan with
    v1 history attached. Coherence is advisory; the structural plan is
    valid."""
    _flag_on(monkeypatch)

    # Patch build_cut_plan: succeed on first call, raise on second
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    counter = {"i": 0}

    def _fake_build(transcript, preset, settings, resolved=None):
        i = counter["i"]
        counter["i"] += 1
        if i >= 1:
            raise RuntimeError("director timeout on rework")
        return plan

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)
    _seq_critic(monkeypatch, [_rework_report()])  # only first pass grades
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    _stub_resolver_and_resolve(monkeypatch)

    with caplog.at_level("WARNING", logger="cutmaster-ai.http.cutmaster"):
        r = client.post(
            "/cutmaster/build-plan",
            json={
                "run_id": scrubbed_run["run_id"],
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
    assert r.status_code == 200, r.text  # build did NOT fail

    persisted = state.load(scrubbed_run["run_id"])
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 1
    assert history[0]["report"]["verdict"] == "rework"
    # rework_director_failed log fired
    assert any(
        getattr(rec, "event", None) == "story_critic.rework_director_failed"
        for rec in caplog.records
    )
