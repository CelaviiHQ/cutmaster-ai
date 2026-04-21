"""v4 Phase 4.2 tests — Layer A boundary validator + retry loop.

Resolve-free slices covering:

- BoundaryVerdict / BoundaryVerdictResponse schema
- validator_loop retry behaviour with a stubbed director + stubbed validator
- _boundary_rejections_block renderer + injection
- _format_warning helper

Real frame extraction + Gemini vision calls are not exercised here —
they need Resolve + GEMINI_API_KEY. The multimodal chokepoint itself
is covered by tests/cutmaster/test_llm_helper.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cutmaster_ai.cutmaster.analysis.boundary_validator import (
    BoundarySample,
    BoundaryVerdict,
    BoundaryVerdictResponse,
)
from cutmaster_ai.cutmaster.core import director, validator_loop
from cutmaster_ai.cutmaster.core.director import CutSegment
from cutmaster_ai.cutmaster.core.validator_loop import (
    BoundaryValidationResult,
    _format_warning,
    run_with_boundary_validation,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_boundary_verdict_defaults_to_smooth():
    v = BoundaryVerdict(cut_index=3)
    assert v.verdict == "smooth"
    assert v.reason == ""
    assert v.suggestion == ""


def test_boundary_verdict_rejects_unknown_label():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BoundaryVerdict(cut_index=0, verdict="terrible")


def test_verdict_response_accepts_empty():
    resp = BoundaryVerdictResponse()
    assert resp.verdicts == []


# ---------------------------------------------------------------------------
# validator_loop retry behaviour (stubbed director + stubbed validator)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_verdicts(monkeypatch):
    """Replace the real Gemini-backed validator with a scripted sequence."""
    calls = {"count": 0}
    scripted: list[list[BoundaryVerdict]] = []

    def _impl(samples):
        idx = calls["count"]
        calls["count"] += 1
        if idx >= len(scripted):
            return []
        return scripted[idx]

    monkeypatch.setattr(
        "cutmaster_ai.cutmaster.analysis.boundary_validator.validate_boundaries",
        _impl,
    )
    return {"scripted": scripted, "calls": calls}


@pytest.mark.asyncio
async def test_loop_accepts_plan_when_all_smooth(stub_verdicts):
    stub_verdicts["scripted"].append([BoundaryVerdict(cut_index=1, verdict="smooth")])

    async def director_fn(rejections, roster):
        assert rejections is None  # first attempt, no rejections yet
        assert roster is None  # linear mode — no roster supplied
        return MagicMock(name="plan")

    def build_samples(_plan):
        return [_sample(1)]

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=build_samples,
    )
    assert result.retries_used == 0
    assert result.warnings == []
    assert not result.skipped
    assert stub_verdicts["calls"]["count"] == 1


@pytest.mark.asyncio
async def test_loop_short_circuits_when_no_samples(stub_verdicts):
    async def director_fn(_rejections, _roster):
        return MagicMock(name="plan")

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [],
    )
    assert result.skipped is True
    assert result.retries_used == 0
    # Validator never called.
    assert stub_verdicts["calls"]["count"] == 0


@pytest.mark.asyncio
async def test_loop_retries_on_jarring_then_accepts(stub_verdicts):
    # First pass: one jarring cut. Second pass: all smooth.
    stub_verdicts["scripted"].extend(
        [
            [
                BoundaryVerdict(
                    cut_index=2, verdict="jarring", reason="axis jump", suggestion="shift 0.3s"
                )
            ],
            [BoundaryVerdict(cut_index=2, verdict="smooth")],
        ]
    )
    observed_rejections: list[list[dict] | None] = []

    async def director_fn(rejections, _roster):
        observed_rejections.append(rejections)
        return MagicMock(name="plan")

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [_sample(2)],
    )
    assert result.retries_used == 1
    assert result.warnings == []
    # First call had no rejections; retry carried them.
    assert observed_rejections[0] is None
    assert observed_rejections[1] is not None
    assert observed_rejections[1][0]["cut_index"] == 2
    assert observed_rejections[1][0]["reason"] == "axis jump"
    # Linear plans also carry candidate_index=0 on every rejection so
    # short_generator and linear modes share a single wire format.
    assert observed_rejections[1][0]["candidate_index"] == 0


@pytest.mark.asyncio
async def test_loop_exhausts_retries_then_falls_through_to_warnings(stub_verdicts):
    # All three passes return jarring.
    jarring = [
        BoundaryVerdict(
            cut_index=4, verdict="jarring", reason="mid-gesture", suggestion="shift 0.5s"
        )
    ]
    stub_verdicts["scripted"].extend([jarring, jarring, jarring])

    async def director_fn(_rejections, _roster):
        return MagicMock(name="plan")

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [_sample(4)],
        max_retries=2,
    )
    assert result.retries_used == 2
    assert len(result.warnings) == 1
    assert "cut 4" in result.warnings[0]
    assert "JARRING" in result.warnings[0]
    # 1 initial + 2 retries = 3 calls
    assert stub_verdicts["calls"]["count"] == 3


@pytest.mark.asyncio
async def test_loop_surfaces_borderline_as_warnings_without_retry(stub_verdicts):
    """Borderline alone doesn't trigger a retry — it surfaces as a warning."""
    stub_verdicts["scripted"].append(
        [BoundaryVerdict(cut_index=1, verdict="borderline", reason="subtle framing shift")]
    )

    async def director_fn(_rejections, _roster):
        return MagicMock(name="plan")

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [_sample(1)],
    )
    assert result.retries_used == 0
    assert len(result.warnings) == 1
    assert "BORDERLINE" in result.warnings[0]
    assert stub_verdicts["calls"]["count"] == 1


@pytest.mark.asyncio
async def test_loop_honours_monkeypatched_max_retries(stub_verdicts, monkeypatch):
    """MAX_BOUNDARY_RETRIES module constant is importable + overridable."""
    jarring = [BoundaryVerdict(cut_index=0, verdict="jarring", reason="x")]
    stub_verdicts["scripted"].extend([jarring] * 10)

    async def director_fn(_rejections, _roster):
        return MagicMock()

    # Caller passes 0 → evaluate once, no retry.
    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [_sample(0)],
        max_retries=0,
    )
    assert result.retries_used == 0
    assert stub_verdicts["calls"]["count"] == 1
    # Default constant is exposed + non-zero.
    assert validator_loop.MAX_BOUNDARY_RETRIES == 2


def test_validation_result_summary_keys():
    result = BoundaryValidationResult(
        plan=MagicMock(),
        verdicts=[{"cut_index": 1, "verdict": "smooth"}],
        warnings=["cut 2 (JARRING) — axis jump"],
        retries_used=1,
    )
    summary = result.to_summary()
    assert set(summary) == {"retries_used", "verdicts", "warnings", "skipped"}
    assert summary["retries_used"] == 1
    assert summary["verdicts"][0]["cut_index"] == 1


# ---------------------------------------------------------------------------
# _format_warning
# ---------------------------------------------------------------------------


def test_format_warning_handles_missing_reason_and_suggestion():
    v = BoundaryVerdict(cut_index=7, verdict="jarring")
    assert _format_warning(v) == "cut 7 (JARRING)"


def test_format_warning_includes_reason_and_suggestion():
    v = BoundaryVerdict(
        cut_index=3,
        verdict="jarring",
        reason="mid-swing hand",
        suggestion="shift 0.4s earlier",
    )
    out = _format_warning(v)
    assert "cut 3 (JARRING)" in out
    assert "mid-swing hand" in out
    assert "shift 0.4s earlier" in out


# ---------------------------------------------------------------------------
# _boundary_rejections_block
# ---------------------------------------------------------------------------


def test_rejections_block_empty_without_settings():
    assert director._boundary_rejections_block(None) == ""
    assert director._boundary_rejections_block({}) == ""
    assert director._boundary_rejections_block({"_boundary_rejections": []}) == ""


def test_rejections_block_renders_reason_and_suggestion():
    settings = {
        "_boundary_rejections": [
            {
                "cut_index": 2,
                "reason": "axis jump — speaker crosses frame",
                "suggestion": "shift 0.2s earlier",
            },
            {"cut_index": 5, "reason": "lighting flash", "suggestion": ""},
        ]
    }
    block = director._boundary_rejections_block(settings)
    assert "BOUNDARY REJECTIONS" in block
    assert "cut 2" in block
    assert "axis jump" in block
    assert "suggestion: shift 0.2s earlier" in block
    assert "cut 5" in block
    assert "lighting flash" in block
    # Empty suggestion shouldn't leak an empty suggestion line.
    assert block.count("suggestion:") == 1
    # Footer instructs the model to avoid returning the same plan.
    assert "DIFFERENT word boundaries" in block


def test_rejections_block_tolerates_missing_fields():
    settings = {"_boundary_rejections": [{"cut_index": "bogus"}]}
    # Doesn't crash; coerces gracefully.
    block = director._boundary_rejections_block(settings)
    assert "cut 0" in block
    assert "no reason supplied" in block


def test_rejections_injected_only_when_present():
    from cutmaster_ai.cutmaster.data.presets import get_preset

    preset = get_preset("vlog")
    transcript = [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
    ]
    # Without rejections — block absent.
    prompt_clean = director._prompt(preset, transcript, user_settings={})
    assert "BOUNDARY REJECTIONS" not in prompt_clean

    # With rejections — block present in the flat prompt.
    settings = {
        "_boundary_rejections": [
            {"cut_index": 1, "reason": "framing mismatch", "suggestion": "try earlier"}
        ]
    }
    prompt_with = director._prompt(preset, transcript, user_settings=settings)
    assert "BOUNDARY REJECTIONS" in prompt_with
    assert "framing mismatch" in prompt_with
    assert "try earlier" in prompt_with


@pytest.mark.parametrize(
    "builder",
    ["flat_prompt", "clip_hunter", "short_generator", "assembled", "curated", "rough_cut"],
)
def test_rejections_block_wired_into_every_builder(builder):
    """Every one of the six builders must render the rejections block when set."""
    from cutmaster_ai.cutmaster.data.presets import get_preset

    preset = get_preset("vlog")
    transcript = [
        {"word": "alpha", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "beta", "start_time": 0.5, "end_time": 1.0, "speaker_id": "S1"},
    ]
    settings = {
        "_boundary_rejections": [
            {"cut_index": 0, "reason": "axis-cross demo", "suggestion": "pick different word"}
        ]
    }

    if builder == "flat_prompt":
        prompt = director._prompt(preset, transcript, settings)
    elif builder == "clip_hunter":
        prompt = director._clip_hunter_prompt(
            preset, transcript, settings, target_clip_length_s=30.0, num_clips=3
        )
    elif builder == "short_generator":
        prompt = director._short_generator_prompt(
            preset, transcript, settings, target_short_length_s=45.0, num_shorts=2
        )
    elif builder == "assembled":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(transcript)],
            }
        ]
        prompt = director._assembled_prompt(preset, takes, settings)
    elif builder == "curated":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(transcript)],
            }
        ]
        prompt = director._curated_prompt(preset, takes, settings)
    elif builder == "rough_cut":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(transcript)],
            }
        ]
        groups = [{"group_id": "g0", "item_indexes": [0], "signal": "color"}]
        prompt = director._rough_cut_prompt(preset, takes, groups, settings)
    else:
        pytest.fail(f"unknown builder {builder}")

    assert "BOUNDARY REJECTIONS" in prompt
    assert "axis-cross demo" in prompt


# ---------------------------------------------------------------------------
# CutSegment shaping (guard rails)
# ---------------------------------------------------------------------------


def test_boundary_sample_round_trip():
    sample = BoundarySample(
        cut_index=5,
        out_source_path="/srv/a.mov",
        out_source_ts_s=12.345,
        in_source_path="/srv/b.mov",
        in_source_ts_s=0.1,
    )
    assert sample.cut_index == 5
    assert sample.out_source_ts_s == 12.345


def test_cutsegment_is_unchanged_by_phase_4_2():
    """Sanity: phase 4.2 must not mutate the Director output schema."""
    seg = CutSegment(start_s=0.0, end_s=1.5, reason="hook")
    assert seg.start_s == 0.0
    assert seg.end_s == 1.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample(cut_index: int, candidate_index: int = 0) -> BoundarySample:
    return BoundarySample(
        cut_index=cut_index,
        candidate_index=candidate_index,
        out_source_path="/srv/a.mov",
        out_source_ts_s=1.0,
        in_source_path="/srv/b.mov",
        in_source_ts_s=0.5,
    )


# ---------------------------------------------------------------------------
# Multi-candidate (Short Generator) — candidate_index addressing
# ---------------------------------------------------------------------------


def test_boundary_sample_candidate_index_default_zero():
    s = BoundarySample(
        cut_index=1,
        out_source_path="/a",
        out_source_ts_s=0.0,
        in_source_path="/b",
        in_source_ts_s=0.0,
    )
    assert s.candidate_index == 0


def test_boundary_verdict_carries_candidate_index():
    v = BoundaryVerdict(candidate_index=2, cut_index=3, verdict="jarring", reason="x")
    assert v.candidate_index == 2
    assert v.cut_index == 3


def test_format_warning_includes_candidate_in_multi_candidate_mode():
    v = BoundaryVerdict(
        candidate_index=2,
        cut_index=3,
        verdict="jarring",
        reason="mid-swing",
        suggestion="shift 0.3s",
    )
    out = _format_warning(v, multi_candidate=True)
    assert "candidate 2" in out
    assert "cut 3" in out
    assert "mid-swing" in out
    assert "shift 0.3s" in out


def test_format_warning_omits_candidate_in_linear_mode():
    v = BoundaryVerdict(cut_index=3, verdict="borderline")
    out = _format_warning(v, multi_candidate=False)
    assert "candidate" not in out.lower()
    assert "cut 3" in out


def test_format_warning_shows_candidate_zero_in_multi_candidate_mode():
    """Candidate 0 is the top-ranked short — still gets the qualifier so
    the editor can distinguish it from candidate 1's cuts."""
    v = BoundaryVerdict(candidate_index=0, cut_index=1, verdict="jarring", reason="x")
    out = _format_warning(v, multi_candidate=True)
    assert "candidate 0, cut 1" in out


def test_rejections_block_multi_candidate_with_roster():
    settings = {
        "_candidate_roster": [
            {"candidate_index": 0, "theme": "AR replaces phones"},
            {"candidate_index": 1, "theme": "loneliness debate"},
            {"candidate_index": 2, "theme": "RTO policy"},
        ],
        "_boundary_rejections": [
            {
                "candidate_index": 2,
                "cut_index": 3,
                "reason": "hand mid-swing",
                "suggestion": "shift 0.4s earlier",
            },
        ],
    }
    block = director._boundary_rejections_block(settings)
    assert "BOUNDARY REJECTIONS" in block
    # Roster renders before the rejections, with theme ordering preserved.
    assert "KEEP these themes" in block
    assert '"AR replaces phones"' in block
    assert '"loneliness debate"' in block
    assert '"RTO policy"' in block
    assert "candidate 2, cut 3" in block
    assert "hand mid-swing" in block
    assert "shift 0.4s earlier" in block
    # Multi-candidate footer differs from linear footer.
    assert "Do NOT reshuffle" in block
    assert "Do NOT just return the same plan" not in block


def test_rejections_block_infers_multi_candidate_from_rejection_index_alone():
    """Missing roster but non-zero candidate_index still renders multi-candidate."""
    settings = {
        "_boundary_rejections": [
            {"candidate_index": 1, "cut_index": 2, "reason": "axis"},
        ]
    }
    block = director._boundary_rejections_block(settings)
    assert "candidate 1, cut 2" in block
    assert "Do NOT reshuffle" in block


def test_rejections_block_linear_mode_preserved():
    """Explicit candidate_index=0 on every rejection keeps linear format."""
    settings = {
        "_boundary_rejections": [
            {"candidate_index": 0, "cut_index": 3, "reason": "hard cut"},
        ]
    }
    block = director._boundary_rejections_block(settings)
    assert "cut 3" in block
    assert "candidate 0, cut 3" not in block
    # Linear footer.
    assert "Do NOT just return the same plan" in block


# ---------------------------------------------------------------------------
# validator_loop roster passthrough (multi-candidate mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_passes_roster_to_director_on_retry(stub_verdicts):
    """extract_candidate_roster → roster arg on next director call."""
    jarring = BoundaryVerdict(candidate_index=1, cut_index=2, verdict="jarring", reason="x")
    stub_verdicts["scripted"].extend(
        [
            [jarring],
            [BoundaryVerdict(candidate_index=1, cut_index=2, verdict="smooth")],
        ]
    )
    observed: list[tuple] = []

    async def director_fn(rejections, roster):
        observed.append((rejections, roster))
        # Return a minimal plan with two candidates, each exposing theme.
        plan = MagicMock()
        plan.candidates = [
            MagicMock(theme="alpha theme"),
            MagicMock(theme="beta theme"),
        ]
        return plan

    def extract_roster(plan):
        return [{"candidate_index": i, "theme": c.theme} for i, c in enumerate(plan.candidates)]

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [_sample(2, candidate_index=1)],
        extract_candidate_roster=extract_roster,
    )
    assert result.retries_used == 1
    # First call: no rejections, no roster (loop hasn't computed one yet).
    assert observed[0] == (None, None)
    # Retry call: rejections AND roster from the previous plan.
    rej, roster = observed[1]
    assert rej is not None and len(rej) == 1
    assert rej[0]["candidate_index"] == 1
    assert rej[0]["cut_index"] == 2
    assert roster is not None and len(roster) == 2
    assert roster[0]["theme"] == "alpha theme"
    assert roster[1]["candidate_index"] == 1


@pytest.mark.asyncio
async def test_loop_exhausts_with_multi_candidate_warnings(stub_verdicts):
    # Two distinct candidates both contain jarring cuts that never resolve.
    jarring_both = [
        BoundaryVerdict(candidate_index=0, cut_index=1, verdict="jarring", reason="a"),
        BoundaryVerdict(candidate_index=2, cut_index=4, verdict="jarring", reason="b"),
    ]
    stub_verdicts["scripted"].extend([jarring_both] * 5)

    async def director_fn(_rejections, _roster):
        plan = MagicMock()
        plan.candidates = [MagicMock(theme=f"t{i}") for i in range(3)]
        return plan

    result = await run_with_boundary_validation(
        director_fn=director_fn,
        build_samples=lambda _plan: [
            _sample(1, candidate_index=0),
            _sample(4, candidate_index=2),
        ],
        extract_candidate_roster=lambda p: [
            {"candidate_index": i, "theme": c.theme} for i, c in enumerate(p.candidates)
        ],
        max_retries=2,
    )
    assert result.retries_used == 2
    assert len(result.warnings) == 2
    # Both warnings carry the candidate qualifier.
    assert any("candidate 0, cut 1" in w for w in result.warnings)
    assert any("candidate 2, cut 4" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# build_short_generator_boundary_samples — Resolve-free slice
# ---------------------------------------------------------------------------


def test_short_generator_sample_builder_tags_all_candidates(monkeypatch):
    """Every candidate's spans should produce samples with matching candidate_index."""
    from cutmaster_ai.cutmaster.analysis import boundary_validator

    # Stub out the Resolve-facing helpers so we can run without DaVinci.
    monkeypatch.setattr(
        boundary_validator,
        "_locate_source_frame",
        lambda _tl, _proj, ts: ("/tmp/x.mov", float(ts)),
    )

    # Build two synthetic candidates with ShortCandidate-shaped duck types.
    from cutmaster_ai.cutmaster.core.director import ShortCandidate, ShortSpan

    cand_a = ShortCandidate(
        theme="theme A",
        spans=[
            ShortSpan(start_s=0.0, end_s=2.0, quote="q1", role="hook"),
            ShortSpan(start_s=4.0, end_s=6.0, quote="q2", role="setup"),
            ShortSpan(start_s=8.0, end_s=10.0, quote="q3", role="payoff"),
        ],
        engagement_score=0.9,
    )
    cand_b = ShortCandidate(
        theme="theme B",
        spans=[
            ShortSpan(start_s=0.0, end_s=1.0, quote="q1", role="hook"),
            ShortSpan(start_s=3.0, end_s=4.0, quote="q2", role="payoff"),
        ],
        engagement_score=0.7,
    )

    samples = boundary_validator.build_short_generator_boundary_samples(
        tl=MagicMock(),
        candidates=[cand_a, cand_b],
        project=MagicMock(),
    )
    # Candidate A: 3 spans → 2 internal cuts.
    # Candidate B: 2 spans → 1 internal cut.
    assert len(samples) == 3
    assert [s.candidate_index for s in samples] == [0, 0, 1]
    # cut_index is per-candidate, not global.
    assert [s.cut_index for s in samples] == [1, 2, 1]


def test_short_generator_sample_builder_skips_single_span_candidates():
    from cutmaster_ai.cutmaster.analysis import boundary_validator
    from cutmaster_ai.cutmaster.core.director import ShortCandidate, ShortSpan

    cand_solo = ShortCandidate(
        theme="solo theme",
        spans=[ShortSpan(start_s=0.0, end_s=2.0, quote="q", role="hook")],
        engagement_score=0.5,
    )

    samples = boundary_validator.build_short_generator_boundary_samples(
        tl=MagicMock(),
        candidates=[cand_solo],
        project=MagicMock(),
    )
    # No internal cuts → no samples. No exception either.
    assert samples == []
