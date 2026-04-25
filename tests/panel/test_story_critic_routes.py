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
