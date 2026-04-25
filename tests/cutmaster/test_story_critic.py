"""Story-critic Phase 1 tests — schema, adapters, dispatch, finalisation.

The critic LLM is mocked everywhere via the ``_llm`` injection seam in
:func:`critique`. No real Gemini calls; no Resolve. Pure-function coverage.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cutmaster_ai.cutmaster.core.director import (
    AssembledDirectorPlan,
    AssembledItemSelection,
    ClipCandidate,
    ClipHunterPlan,
    CuratedDirectorPlan,
    CuratedItemSelection,
    CutSegment,
    DirectorPlan,
    ShortCandidate,
    ShortGeneratorPlan,
    ShortSpan,
    WordSpan,
)
from cutmaster_ai.cutmaster.data.axis_resolution import resolve_axes
from cutmaster_ai.intelligence import story_critic as sc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _axes(
    cut_intent: str = "narrative",
    content_type: str = "vlog",
    timeline_mode: str = "raw_dump",
):
    # surgical_tighten only works on already-assembled timelines (per the
    # axis-compat matrix), so the helper picks "assembled" automatically when
    # the caller asks for that intent.
    if cut_intent == "surgical_tighten" and timeline_mode == "raw_dump":
        timeline_mode = "assembled"
    return resolve_axes(
        content_type=content_type,
        cut_intent=cut_intent,
        duration_s=120.0,
        timeline_mode=timeline_mode,
    )


def _transcript() -> list[dict]:
    # Two sentences. Word boundaries land at 0.0–3.3 (sentence A) and
    # 3.5–4.7 (sentence B).
    return [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "everyone,", "start_time": 0.5, "end_time": 1.0, "speaker_id": "S1"},
        {"word": "today", "start_time": 1.0, "end_time": 1.3, "speaker_id": "S1"},
        {"word": "we're", "start_time": 1.3, "end_time": 1.55, "speaker_id": "S1"},
        {"word": "talking", "start_time": 1.55, "end_time": 2.0, "speaker_id": "S1"},
        {"word": "about", "start_time": 2.0, "end_time": 2.4, "speaker_id": "S1"},
        {"word": "remote", "start_time": 2.4, "end_time": 2.8, "speaker_id": "S1"},
        {"word": "work.", "start_time": 2.8, "end_time": 3.3, "speaker_id": "S1"},
        {"word": "So", "start_time": 3.5, "end_time": 3.7, "speaker_id": "S2"},
        {"word": "what's", "start_time": 3.7, "end_time": 4.0, "speaker_id": "S2"},
        {"word": "your", "start_time": 4.0, "end_time": 4.2, "speaker_id": "S2"},
        {"word": "take?", "start_time": 4.2, "end_time": 4.7, "speaker_id": "S2"},
    ]


def _take(item_index: int) -> dict:
    transcript = _transcript()
    return {
        "item_index": item_index,
        "source_name": f"clip_{item_index:02d}.mov",
        "transcript": [dict(w, i=i) for i, w in enumerate(transcript)],
    }


def _llm_returning(payload: dict):
    """Build an injectable critic that returns a fixed payload (1 call)."""
    calls: list[str] = []

    def _fake(prompt: str) -> sc._CritiqueLLMResponse:
        calls.append(prompt)
        return sc._CritiqueLLMResponse.model_validate(payload)

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


def _llm_returning_each(payloads: list[dict]):
    """Build an injectable critic that returns one payload per call, in order."""
    calls: list[str] = []
    counter = {"i": 0}

    def _fake(prompt: str) -> sc._CritiqueLLMResponse:
        i = counter["i"]
        counter["i"] += 1
        calls.append(prompt)
        return sc._CritiqueLLMResponse.model_validate(payloads[i])

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


_GOOD_PAYLOAD = {
    "score": 82,
    "hook_strength": 85,
    "arc_clarity": 80,
    "transitions": 78,
    "resolution": 84,
    "issues": [
        {
            "segment_index": 1,
            "severity": "warning",
            "category": "abrupt_transition",
            "message": "Speaker change feels sudden.",
        }
    ],
    "summary": "Reads cleanly; one transition could breathe more.",
}


# ---------------------------------------------------------------------------
# 1.1 — schema bounds
# ---------------------------------------------------------------------------


def test_coherence_report_rejects_negative_score():
    with pytest.raises(ValidationError):
        sc.CoherenceReport(
            score=-1,
            hook_strength=50,
            resolution=50,
            summary="x",
            verdict="rework",
        )


def test_coherence_report_rejects_score_over_100():
    with pytest.raises(ValidationError):
        sc.CoherenceReport(
            score=101,
            hook_strength=50,
            resolution=50,
            summary="x",
            verdict="rework",
        )


def test_coherence_report_accepts_null_arc_and_transitions():
    rpt = sc.CoherenceReport(
        score=70,
        hook_strength=70,
        arc_clarity=None,
        transitions=None,
        resolution=70,
        summary="x",
        verdict="review",
    )
    assert rpt.arc_clarity is None
    assert rpt.transitions is None


# ---------------------------------------------------------------------------
# 1.2 — adapters per plan shape
# ---------------------------------------------------------------------------


def test_adapter_director_plan_slices_text_and_carries_arc_role():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=3.3, reason="open", arc_role="hook"),
            CutSegment(start_s=3.5, end_s=4.7, reason="close", arc_role="resolve"),
        ],
        reasoning="setup-resolve",
    )
    inp = sc._adapt_director_plan(plan, _transcript())
    assert inp.hook_index == 0
    assert len(inp.segments) == 2
    assert inp.segments[0].arc_role == "hook"
    assert "Hello" in inp.segments[0].text
    assert "remote" in inp.segments[0].text
    assert "take?" in inp.segments[1].text


def test_adapter_director_plan_handles_missing_arc_role():
    """Pre-9bf8e73 back-compat — segments without arc_role still adapt."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=3.3, reason="open"),
        ],
    )
    inp = sc._adapt_director_plan(plan, _transcript())
    assert inp.segments[0].arc_role is None


def test_adapter_assembled_plan():
    plan = AssembledDirectorPlan(
        hook_index=0,
        selections=[
            AssembledItemSelection(
                item_index=0,
                kept_word_spans=[WordSpan(a=0, b=3), WordSpan(a=4, b=7)],
            ),
        ],
        reasoning="one take",
    )
    inp = sc._adapt_assembled_plan(plan, [_take(0)])
    assert len(inp.segments) == 2
    assert inp.segments[0].text.startswith("Hello")
    assert "remote" in inp.segments[1].text


def test_adapter_curated_plan_orders_by_play_order():
    plan = CuratedDirectorPlan(
        hook_order=1,
        selections=[
            CuratedItemSelection(order=1, item_index=0, kept_word_spans=[WordSpan(a=0, b=3)]),
            CuratedItemSelection(order=0, item_index=1, kept_word_spans=[WordSpan(a=8, b=11)]),
        ],
        reasoning="reordered",
    )
    inp = sc._adapt_curated_plan(plan, [_take(0), _take(1)])
    # Play order: order=0 first (take 1), then order=1 (take 0).
    # hook_index points at the segment whose source order == hook_order.
    assert "take?" in inp.segments[0].text  # take 1's words 8–11
    assert "Hello" in inp.segments[1].text  # take 0's words 0–3
    assert inp.hook_index == 1


def test_adapter_clip_hunter_returns_one_input_per_candidate():
    plan = ClipHunterPlan(
        candidates=[
            ClipCandidate(start_s=0.0, end_s=3.3, engagement_score=0.9, reasoning="r1"),
            ClipCandidate(start_s=3.5, end_s=4.7, engagement_score=0.6, reasoning="r2"),
        ],
    )
    inputs = sc._adapt_clip_hunter_plan(plan, _transcript())
    assert len(inputs) == 2
    assert "Hello" in inputs[0].segments[0].text
    assert "take?" in inputs[1].segments[0].text


def test_adapter_short_generator_collapses_spans():
    plan = ShortGeneratorPlan(
        candidates=[
            ShortCandidate(
                theme="remote work",
                spans=[
                    ShortSpan(start_s=0.0, end_s=3.3, role="hook"),
                    ShortSpan(start_s=3.5, end_s=4.7, role="payoff"),
                ],
                engagement_score=0.85,
            ),
        ],
    )
    inputs = sc._adapt_short_generator_plan(plan, _transcript())
    assert len(inputs) == 1
    assert len(inputs[0].segments) == 2
    assert inputs[0].segments[0].arc_role == "hook"
    assert inputs[0].label == "remote work"


# ---------------------------------------------------------------------------
# 1.3 — prompt structure
# ---------------------------------------------------------------------------


def test_prompt_contains_intent_segments_and_rationale():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=3.3, reason="open", arc_role="hook"),
        ],
        reasoning="single beat",
    )
    inp = sc._adapt_director_plan(plan, _transcript())
    prompt = sc._critic_prompt(inp, _axes(cut_intent="narrative"))
    assert "narrative" in prompt
    assert "single beat" in prompt
    assert "Hello" in prompt
    assert "hook" in prompt  # arc_role surfaces


# ---------------------------------------------------------------------------
# 1.4 — dispatch + LLM mock
# ---------------------------------------------------------------------------


def test_critique_director_plan_returns_report_with_derived_verdict():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=3.3, reason="open")],
    )
    fake = _llm_returning(_GOOD_PAYLOAD)
    rpt = sc.critique(plan, transcript=_transcript(), axes=_axes(), _llm=fake)
    assert isinstance(rpt, sc.CoherenceReport)
    assert rpt.score == 82
    assert rpt.verdict == "ship"
    assert len(fake.calls) == 1


def test_critique_clip_hunter_dispatches_per_candidate():
    plan = ClipHunterPlan(
        candidates=[
            ClipCandidate(start_s=0.0, end_s=3.3, engagement_score=0.9),
            ClipCandidate(start_s=3.5, end_s=4.7, engagement_score=0.6),
            ClipCandidate(start_s=0.0, end_s=4.7, engagement_score=0.5),
        ],
    )
    payloads = [
        {**_GOOD_PAYLOAD, "score": 60},
        {**_GOOD_PAYLOAD, "score": 90},  # best
        {**_GOOD_PAYLOAD, "score": 75},
    ]
    fake = _llm_returning_each(payloads)
    rpt = sc.critique(
        plan,
        transcript=_transcript(),
        axes=_axes(cut_intent="multi_clip"),
        _llm=fake,
    )
    assert isinstance(rpt, sc.PerCandidateCoherenceReport)
    assert len(rpt.candidates) == 3
    assert rpt.best_candidate_index == 1
    assert len(fake.calls) == 3


def test_critique_requires_transcript_for_director_plan():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=3.3)],
    )
    with pytest.raises(ValueError, match="transcript"):
        sc.critique(plan, axes=_axes(), _llm=_llm_returning(_GOOD_PAYLOAD))


def test_critique_requires_takes_for_assembled_plan():
    plan = AssembledDirectorPlan(
        hook_index=0,
        selections=[AssembledItemSelection(item_index=0, kept_word_spans=[WordSpan(a=0, b=3)])],
    )
    with pytest.raises(ValueError, match="takes"):
        sc.critique(plan, axes=_axes(), _llm=_llm_returning(_GOOD_PAYLOAD))


# ---------------------------------------------------------------------------
# 1.5 — back-compat covered above; explicit smoke
# ---------------------------------------------------------------------------


def test_critique_accepts_pre_arc_role_plan():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=3.3),  # arc_role defaults to None
            CutSegment(start_s=3.5, end_s=4.7),
        ],
    )
    fake = _llm_returning(_GOOD_PAYLOAD)
    rpt = sc.critique(plan, transcript=_transcript(), axes=_axes(), _llm=fake)
    assert isinstance(rpt, sc.CoherenceReport)


# ---------------------------------------------------------------------------
# 1.6 — issue cap
# ---------------------------------------------------------------------------


def test_issue_cap_truncates_to_seven_highest_severity_first():
    bloated = {
        **_GOOD_PAYLOAD,
        "issues": [
            {"segment_index": i, "severity": sev, "category": "redundancy", "message": f"i{i}"}
            for i, sev in enumerate(
                ["info", "info", "info", "info", "warning", "warning", "error", "error", "info"]
            )
        ],
    }
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=3.3)],
    )
    rpt = sc.critique(
        plan,
        transcript=_transcript(),
        axes=_axes(),
        _llm=_llm_returning(bloated),
    )
    assert len(rpt.issues) == 7
    severities = {iss.severity for iss in rpt.issues}
    # Both errors must survive the cap.
    assert severities >= {"error"}
    assert sum(1 for iss in rpt.issues if iss.severity == "error") == 2


# ---------------------------------------------------------------------------
# Verdict derivation + surgical-tighten carve-out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [(0, "rework"), (59, "rework"), (60, "review"), (79, "review"), (80, "ship"), (100, "ship")],
)
def test_verdict_bands(score: int, expected: str):
    assert sc._derive_verdict(score) == expected


def test_surgical_tighten_nulls_arc_and_transitions():
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=3.3)],
    )
    rpt = sc.critique(
        plan,
        transcript=_transcript(),
        axes=_axes(cut_intent="surgical_tighten"),
        _llm=_llm_returning(_GOOD_PAYLOAD),
    )
    assert isinstance(rpt, sc.CoherenceReport)
    assert rpt.arc_clarity is None
    assert rpt.transitions is None
    # Hook + resolution untouched.
    assert rpt.hook_strength == _GOOD_PAYLOAD["hook_strength"]
    assert rpt.resolution == _GOOD_PAYLOAD["resolution"]
