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


def test_build_plan_critic_rework_regression_guard_keeps_better_pass(
    client, monkeypatch, scrubbed_run, caplog
):
    """When pass 2 scores LOWER than pass 1, the regression-guard keeps
    pass 1's plan and final report. History records both envelopes so
    the panel can surface the regression chip; telemetry carries
    ``kept_pass="first"`` so we can audit the decision."""
    _flag_on(monkeypatch)

    pass1_plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="pass1")],
        reasoning="pass1",
    )
    pass2_plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="pass2")],
        reasoning="pass2",
    )
    counter = {"i": 0}

    def _fake_build(transcript, preset, settings, resolved=None):
        i = counter["i"]
        counter["i"] += 1
        return pass1_plan if i == 0 else pass2_plan

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)

    # Pass 1 scores 65 (verdict=review → triggers rework); pass 2 scores
    # 45 (verdict=rework, lower than pass 1) — guard must keep pass 1.
    pass1 = story_critic.CoherenceReport(
        score=65,
        hook_strength=70,
        arc_clarity=60,
        transitions=65,
        resolution=70,
        issues=[],
        summary="ok-ish",
        verdict="review",
    )
    pass2 = story_critic.CoherenceReport(
        score=45,
        hook_strength=60,
        arc_clarity=30,
        transitions=40,
        resolution=50,
        issues=[],
        summary="worse",
        verdict="rework",
    )
    _seq_critic(monkeypatch, [pass1, pass2])
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

    persisted = state.load(scrubbed_run["run_id"])
    # Final plan is pass 1's (higher score). The shipped Director plan
    # carries the pass 1 reasoning string.
    assert persisted["plan"]["director"]["reasoning"] == "pass1"
    # History records both passes for auditability.
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 2
    assert history[0]["report"]["score"] == 65
    assert history[1]["report"]["score"] == 45
    # The mirrored coherence_report points at pass 1 (the kept one).
    assert persisted["plan"]["coherence_report"]["report"]["score"] == 65

    completed = [
        rec
        for rec in caplog.records
        if getattr(rec, "event", None) == "story_critic.rework_completed"
    ]
    assert len(completed) == 1
    assert completed[0].kept_pass == "first"
    assert completed[0].score_delta == -20


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


# ---------------------------------------------------------------------------
# Iterative-loop telemetry — story_critic.iteration + story_critic.loop_terminated
# ---------------------------------------------------------------------------
#
# Phase 1 reachable subset of `loop_terminated.reason`: {shipped,
# max_iterations, director_failed}. The other three values (plateau,
# regression, token_budget) are reserved by the proposal but only become
# reachable once Phase 2 lifts MAX above 1; tests here assert they do
# NOT fire under MAX=1.
#
# `iteration_index` is bounded to {0, 1} for Phase 1: 1 iteration on
# no-rework, 2 on a single rework. These bounds widen in Phase 2 when
# REWORK_MAX defaults to 3.


def _iteration_records(caplog) -> list:
    return [
        rec for rec in caplog.records if getattr(rec, "event", None) == "story_critic.iteration"
    ]


def _terminated_records(caplog) -> list:
    return [
        rec
        for rec in caplog.records
        if getattr(rec, "event", None) == "story_critic.loop_terminated"
    ]


def test_loop_telemetry_shipped_on_first_pass(client, monkeypatch, scrubbed_run, caplog):
    """verdict=ship on pass 1 → 1 iteration record + 1 terminated record
    with reason=shipped. The no-rework hot path (most builds) MUST emit
    these even though it returns before any rework logic runs."""
    _flag_on(monkeypatch)
    _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_good_report()])
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

    iterations = _iteration_records(caplog)
    terminated = _terminated_records(caplog)
    assert len(iterations) == 1
    assert iterations[0].iteration_index == 0
    assert iterations[0].score == 82
    assert iterations[0].verdict == "ship"
    assert iterations[0].delta_from_prev is None
    assert iterations[0].n_issues == 0

    assert len(terminated) == 1
    assert terminated[0].reason == "shipped"
    assert terminated[0].iterations_run == 1
    assert terminated[0].final_score == 82


def test_loop_telemetry_max_iterations_when_rework_disabled(
    client, monkeypatch, scrubbed_run, caplog
):
    """Critic flags rework but REWORK_MAX=0 → loop terminated as
    max_iterations after one pass (env disables the rework attempt)."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "0")
    _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_rework_report()])
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

    iterations = _iteration_records(caplog)
    terminated = _terminated_records(caplog)
    assert len(iterations) == 1
    assert iterations[0].iteration_index == 0
    assert len(terminated) == 1
    assert terminated[0].reason == "max_iterations"
    assert terminated[0].iterations_run == 1


def test_loop_telemetry_two_iterations_on_rework(client, monkeypatch, scrubbed_run, caplog):
    """Pass 1 verdict=rework → rework fires → pass 2 verdict=ship.
    Two iteration records with delta_from_prev wired on iter 1, and
    a terminated record with reason=shipped (because pass 2 shipped)."""
    _flag_on(monkeypatch)
    _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_rework_report(), _good_report()])
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

    iterations = _iteration_records(caplog)
    terminated = _terminated_records(caplog)
    assert [rec.iteration_index for rec in iterations] == [0, 1]
    assert iterations[0].delta_from_prev is None
    assert iterations[1].delta_from_prev == 24  # 82 - 58

    assert len(terminated) == 1
    assert terminated[0].reason == "shipped"
    assert terminated[0].iterations_run == 2
    assert terminated[0].final_score == 82


def test_loop_telemetry_max_iterations_when_neither_pass_ships(
    client, monkeypatch, scrubbed_run, caplog
):
    """REWORK_MAX=1: pass 1 verdict=rework, pass 2 verdict=review →
    one rework fires, neither passes ship, loop exits as max_iterations.

    Pinned to MAX=1 so this test exercises the historical one-shot-rework
    path explicitly. Phase 2's wider iteration count is covered by
    `test_loop_runs_to_max_when_improving`.
    """
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "1")
    _capture_director_calls(monkeypatch)
    pass1 = _rework_report()
    pass2 = story_critic.CoherenceReport(
        score=70,  # higher so kept_pass="second"; verdict still not ship
        hook_strength=70,
        arc_clarity=70,
        transitions=70,
        resolution=70,
        issues=[],
        summary="better but not ship",
        verdict="review",
    )
    _seq_critic(monkeypatch, [pass1, pass2])
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

    terminated = _terminated_records(caplog)
    assert len(terminated) == 1
    assert terminated[0].reason == "max_iterations"
    assert terminated[0].iterations_run == 2
    assert terminated[0].final_score == 70  # pass 2 (kept_pass="second")


def test_loop_telemetry_director_failed_reason(client, monkeypatch, scrubbed_run, caplog):
    """When the Director's rework call raises, terminated.reason is
    director_failed and only iteration 0 was recorded."""
    _flag_on(monkeypatch)

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
    _seq_critic(monkeypatch, [_rework_report()])
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

    iterations = _iteration_records(caplog)
    terminated = _terminated_records(caplog)
    assert len(iterations) == 1
    assert iterations[0].iteration_index == 0

    assert len(terminated) == 1
    assert terminated[0].reason == "director_failed"
    assert terminated[0].iterations_run == 1


def test_loop_telemetry_no_log_when_critic_skipped(client, monkeypatch, scrubbed_run, caplog):
    """Flag off → critic skipped → loop never starts → NO iteration
    or loop_terminated records. Phase 1.3 explicitly defers this branch
    to story_critic.skipped, which already exists."""
    _flag_off(monkeypatch)
    _capture_director_calls(monkeypatch)
    _mock_critic(monkeypatch)
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

    assert _iteration_records(caplog) == []
    assert _terminated_records(caplog) == []
    assert any(getattr(rec, "event", None) == "story_critic.skipped" for rec in caplog.records)


def test_loop_telemetry_reason_in_documented_set(client, monkeypatch, scrubbed_run, caplog):
    """The full set of reachable termination reasons under Phase 2.
    Plateau / regression / token_budget are now reachable; the Phase 1
    bound asserting them unreachable was relaxed when MAX defaulted to 3.
    """
    _flag_on(monkeypatch)
    _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_rework_report(), _good_report()])
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

    terminated = _terminated_records(caplog)
    assert len(terminated) == 1
    assert terminated[0].reason in {
        "shipped",
        "max_iterations",
        "director_failed",
        "plateau",
        "regression",
        "token_budget",
    }


# ---------------------------------------------------------------------------
# Phase 2 — bounded iterative loop, plateau / regression / budget exits
# ---------------------------------------------------------------------------
#
# These tests pin REWORK_MAX explicitly so they stay deterministic when
# the default changes again. MIN_DELTA defaults to 3; tests pin it when
# they need a specific threshold.


def _report(score: int, verdict: str = "review") -> story_critic.CoherenceReport:
    return story_critic.CoherenceReport(
        score=score,
        hook_strength=score,
        arc_clarity=score,
        transitions=score,
        resolution=score,
        issues=[
            story_critic.CoherenceIssue(
                segment_index=0,
                severity="warning",
                category="weak_hook",
                message="placeholder",
            )
        ]
        if verdict != "ship"
        else [],
        summary=f"score={score} verdict={verdict}",
        verdict=verdict,
    )


def test_loop_runs_to_max_when_improving(client, monkeypatch, scrubbed_run, caplog):
    """Three increasing scores, none ship, none plateau (deltas above
    MIN_DELTA). Loop runs to MAX_ATTEMPTS and exits as max_iterations."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "3")
    _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_report(50), _report(60), _report(70), _report(78)])
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

    iterations = _iteration_records(caplog)
    terminated = _terminated_records(caplog)
    assert [rec.iteration_index for rec in iterations] == [0, 1, 2, 3]
    assert iterations[1].delta_from_prev == 10
    assert iterations[2].delta_from_prev == 10
    assert iterations[3].delta_from_prev == 8

    assert len(terminated) == 1
    assert terminated[0].reason == "max_iterations"
    assert terminated[0].iterations_run == 4  # 1 initial + 3 reworks
    assert terminated[0].final_score == 78


def test_loop_exits_on_plateau(client, monkeypatch, scrubbed_run, caplog):
    """Three flat scores (delta < MIN_DELTA twice in a row) → plateau
    exit. Loop should fire only one rework before plateau triggers on
    iteration 1's delta. With reports [60, 61, 60] and MIN_DELTA=3:
    iter 1 delta=+1 → plateau → exit. Only 1 rework fires (not 3)."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "3")
    director_calls = _capture_director_calls(monkeypatch)
    _seq_critic(monkeypatch, [_report(60), _report(61), _report(60)])
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

    terminated = _terminated_records(caplog)
    assert len(terminated) == 1
    assert terminated[0].reason == "plateau"
    # 2 director calls = first build + 1 rework, NOT 3 reworks.
    assert len(director_calls) == 2


def test_loop_exits_on_regression(client, monkeypatch, scrubbed_run, caplog):
    """Pass 1 score=70, pass 2 score=55 (delta=-15 ≤ -MIN_DELTA) →
    regression exit, shipped envelope is pass 1 (the higher score).
    Pinned to MAX=3 to prove regression triggers before exhaustion."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "3")

    plan_a = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="pass0")],
        reasoning="pass0",
    )
    plan_b = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="pass1")],
        reasoning="pass1",
    )
    counter = {"i": 0}

    def _fake_build(transcript, preset, settings, resolved=None):
        i = counter["i"]
        counter["i"] += 1
        return plan_a if i == 0 else plan_b

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)
    _seq_critic(monkeypatch, [_report(70), _report(55)])
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

    terminated = _terminated_records(caplog)
    assert len(terminated) == 1
    assert terminated[0].reason == "regression"
    assert terminated[0].final_score == 70  # pass 0 wins

    persisted = state.load(scrubbed_run["run_id"])
    # Shipped Director plan is pass 0's (higher score).
    assert persisted["plan"]["director"]["reasoning"] == "pass0"


def test_loop_exits_on_token_budget(client, monkeypatch, scrubbed_run, caplog):
    """Synthetic token usage on each plan exceeds the budget after the
    first rework. Loop exits with reason=token_budget before spending
    on a third Director call."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_TOKEN_BUDGET", "200")

    director_calls: list[dict] = []
    plan_template = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )

    def _fake_build(transcript, preset, settings, resolved=None):
        director_calls.append(dict(settings))
        plan = DirectorPlan(
            hook_index=plan_template.hook_index,
            selected_clips=list(plan_template.selected_clips),
            reasoning=f"call{len(director_calls)}",
        )
        # Stash 150 in / 50 out per Director call so a single call already
        # exceeds the 200-token budget.
        object.__setattr__(plan, "_token_usage", {"in": 150, "out": 50})
        return plan

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)
    _seq_critic(monkeypatch, [_report(50), _report(60), _report(70)])
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

    terminated = _terminated_records(caplog)
    assert len(terminated) == 1
    assert terminated[0].reason == "token_budget"
    # The cost rail must short-circuit BEFORE the third Director call.
    assert len(director_calls) <= 2


def test_rework_max_clamps_to_ceiling(client, monkeypatch, scrubbed_run, caplog):
    """REWORK_MAX=999 clamps to 5 and emits a warning."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "999")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "3")
    _capture_director_calls(monkeypatch)
    # Strictly increasing scores so neither plateau nor ship triggers
    # before the iteration ceiling. 6 reports = 1 initial + 5 reworks.
    _seq_critic(
        monkeypatch,
        [_report(40), _report(50), _report(60), _report(70), _report(78), _report(85)],
    )
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

    iterations = _iteration_records(caplog)
    # Exactly 6 iteration records: iter 0 + 5 reworks (clamped from 999).
    assert [rec.iteration_index for rec in iterations] == [0, 1, 2, 3, 4, 5]

    # Ceiling-clamp warning fired
    assert any(
        "exceeds ceiling" in rec.getMessage() and "999" in rec.getMessage()
        for rec in caplog.records
        if rec.levelname == "WARNING"
    )


def test_pick_shipped_envelope_ties_pick_latest(client, monkeypatch, scrubbed_run, caplog):
    """Three identical scores → ship the LAST iteration, not the first.
    Locks the latest-wins-on-ties tie-break against future refactors
    that revert to plain `max(history, key=...)` (which is first-wins)."""
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "0")  # disable plateau

    plans = [
        DirectorPlan(
            hook_index=0,
            selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason=f"pass{i}")],
            reasoning=f"pass{i}",
        )
        for i in range(4)
    ]
    counter = {"i": 0}

    def _fake_build(transcript, preset, settings, resolved=None):
        i = counter["i"]
        counter["i"] += 1
        return plans[min(i, len(plans) - 1)]

    monkeypatch.setattr(routes.build, "build_cut_plan", _fake_build)
    # Three identical scores. Verdict review (not ship) so loop continues.
    _seq_critic(
        monkeypatch,
        [_report(65), _report(65), _report(65), _report(65)],
    )
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

    persisted = state.load(scrubbed_run["run_id"])
    history = persisted["plan"]["coherence_history"]
    assert len(history) == 4
    # All four entries scored 65; latest wins on ties → ship pass 3.
    assert persisted["plan"]["director"]["reasoning"] == "pass3"
    assert persisted["plan"]["coherence_report"]["report"]["score"] == 65


def test_rework_prompt_carries_prior_passes_history(client, monkeypatch, scrubbed_run, caplog):
    """Phase 3: by iteration N, the Director prompt must summarise the
    issues from passes 1..N-1 so the model doesn't re-break what an
    earlier pass already fixed. We verify by capturing each rework
    Director call's settings dict and asserting:

    - Iteration 1 (first rework): no `history` (it's the first prior pass).
    - Iteration 2: history has 1 prior snapshot.
    - Iteration 3: history has 2 prior snapshots.
    - The last snapshot in history is iteration N-2's issue category.

    This covers sub-steps 3.1 + 3.3 — the wire payload — without
    coupling to the prompt's exact rendered text (which 3.4 is too
    brittle to assert without the dump file).
    """
    _flag_on(monkeypatch)
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_REWORK_MAX", "3")
    monkeypatch.setenv("CUTMASTER_STORY_CRITIC_MIN_DELTA", "0")  # disable plateau

    director_calls = _capture_director_calls(monkeypatch)

    p1 = story_critic.CoherenceReport(
        score=50,
        hook_strength=50,
        arc_clarity=50,
        transitions=50,
        resolution=50,
        issues=[
            story_critic.CoherenceIssue(
                segment_index=0,
                severity="error",
                category="weak_hook",
                message="opener doesn't pull",
            )
        ],
        summary="hook is weak",
        verdict="rework",
    )
    p2 = story_critic.CoherenceReport(
        score=58,
        hook_strength=60,
        arc_clarity=55,
        transitions=55,
        resolution=58,
        issues=[
            story_critic.CoherenceIssue(
                segment_index=2,
                severity="warning",
                category="abrupt_transition",
                message="seg 1 to 2 jumps",
            )
        ],
        summary="hook better; transitions rough",
        verdict="review",
    )
    p3 = story_critic.CoherenceReport(
        score=66,
        hook_strength=68,
        arc_clarity=64,
        transitions=58,
        resolution=70,
        issues=[
            story_critic.CoherenceIssue(
                segment_index=3,
                severity="warning",
                category="non_sequitur",
                message="seg 3 doesn't follow",
            )
        ],
        summary="closer but seg 3 dangles",
        verdict="review",
    )
    p4 = story_critic.CoherenceReport(
        score=75,
        hook_strength=78,
        arc_clarity=70,
        transitions=72,
        resolution=78,
        issues=[],
        summary="reads well",
        verdict="ship",
    )
    _seq_critic(monkeypatch, [p1, p2, p3, p4])
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

    # 4 director calls: initial + 3 reworks.
    assert len(director_calls) == 4
    assert "_critic_feedback" not in director_calls[0]

    fb1 = director_calls[1]["_critic_feedback"]
    assert fb1["score"] == 50
    assert fb1["history"] == []  # iteration 1 has no prior history yet

    fb2 = director_calls[2]["_critic_feedback"]
    assert fb2["score"] == 58
    assert len(fb2["history"]) == 1
    assert fb2["history"][0]["score"] == 50
    assert fb2["history"][0]["issues"][0]["category"] == "weak_hook"

    fb3 = director_calls[3]["_critic_feedback"]
    assert fb3["score"] == 66
    assert len(fb3["history"]) == 2
    assert [snap["score"] for snap in fb3["history"]] == [50, 58]
    assert fb3["history"][1]["issues"][0]["category"] == "abrupt_transition"


def test_rework_prompt_block_renders_multi_pass_when_history_present(monkeypatch):
    """Phase 3.4: the Director's prompt-block helper switches to the
    PREVIOUS ATTEMPTS multi-pass header once history is non-empty, and
    falls back to PREVIOUS ATTEMPT singular when history is empty.
    Asserts the contract directly on the helper so we don't need to
    diff a full prompt dump.
    """
    from cutmaster_ai.cutmaster.core.director import _critic_feedback_block

    # No history → singular header.
    single = _critic_feedback_block(
        {
            "_critic_feedback": {
                "score": 60,
                "verdict": "rework",
                "summary": "hook weak",
                "issues": [
                    {
                        "segment_index": 0,
                        "severity": "error",
                        "category": "weak_hook",
                        "message": "opener doesn't pull",
                    }
                ],
                "history": [],
            }
        }
    )
    assert "PREVIOUS ATTEMPT — REWORK NEEDED" in single
    assert "PREVIOUS ATTEMPTS" not in single
    assert "Pass 1" not in single

    # 2-entry history → plural header + Pass 1 / Pass 2 / Pass 3 markers.
    multi = _critic_feedback_block(
        {
            "_critic_feedback": {
                "score": 66,
                "verdict": "review",
                "summary": "closer but seg 3 dangles",
                "issues": [
                    {
                        "segment_index": 3,
                        "severity": "warning",
                        "category": "non_sequitur",
                        "message": "seg 3 doesn't follow",
                    }
                ],
                "history": [
                    {
                        "score": 50,
                        "verdict": "rework",
                        "summary": "hook is weak",
                        "issues": [{"category": "weak_hook"}],
                    },
                    {
                        "score": 58,
                        "verdict": "review",
                        "summary": "transitions rough",
                        "issues": [{"category": "abrupt_transition"}],
                    },
                ],
            }
        }
    )
    assert "PREVIOUS ATTEMPTS — DO NOT REPEAT THESE FAILURES" in multi
    assert "Pass 1" in multi
    assert "Pass 2" in multi
    assert "Pass 3" in multi  # current pass is iteration N = len(history)+1 = 3
    assert "weak_hook" in multi
    assert "abrupt_transition" in multi
    assert "non_sequitur" in multi
    # Caps at 3 issue categories per prior pass — many-issues case
    many = _critic_feedback_block(
        {
            "_critic_feedback": {
                "score": 60,
                "verdict": "rework",
                "issues": [{"category": "x"}],
                "history": [
                    {
                        "score": 40,
                        "verdict": "rework",
                        "issues": [{"category": f"cat_{i}"} for i in range(7)],
                    }
                ],
            }
        }
    )
    assert "+4 more" in many  # 7 - 3 capped
