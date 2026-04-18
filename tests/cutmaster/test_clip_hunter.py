"""Tests for Clip Hunter Director — schema, validator, prompt, expansion."""

from celavii_resolve.cutmaster import director
from celavii_resolve.cutmaster.director import (
    ClipCandidate,
    ClipHunterPlan,
    candidate_to_segments,
    validate_clip_hunter_plan,
)
from celavii_resolve.cutmaster.presets import get_preset


def _transcript() -> list[dict]:
    """Dense transcript covering 180s so we can validate 60s clips."""
    return [
        {"word": f"w{i}", "start_time": i * 1.0, "end_time": i * 1.0 + 0.9, "speaker_id": "S1"}
        for i in range(180)
    ]


def _cand(start: float, end: float, score: float, quote: str = "x") -> ClipCandidate:
    return ClipCandidate(
        start_s=start,
        end_s=end,
        engagement_score=score,
        quote=quote,
        suggested_caption="cap",
        reasoning="because",
    )


# ------------------------- validator -----------------------------------


def test_valid_plan_passes():
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 60.9, 0.9),
            _cand(61.0, 121.9, 0.7),
            _cand(122.0, 179.9, 0.5),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 3)
    assert errors == [], errors


def test_empty_candidates_rejected():
    plan = ClipHunterPlan(candidates=[])
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 3)
    assert errors
    assert "empty" in errors[0]


def test_count_off_by_more_than_one_rejected():
    # Requested 5, returned 2 — off by 3.
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 60.9, 0.9),
            _cand(61.0, 121.9, 0.7),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 5)
    assert any("expected 5 candidates" in e for e in errors)


def test_count_off_by_one_tolerated():
    # Requested 3, returned 2 — off by 1, acceptable.
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 60.9, 0.9),
            _cand(61.0, 121.9, 0.7),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 3)
    assert not any("expected" in e for e in errors)


def test_duration_outside_tolerance_rejected():
    # Target 60s, tolerance 0.4 → range [36, 84]. A 120s clip fails.
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 120.9, 0.9),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 1)
    assert any("duration" in e for e in errors)


def test_overlapping_candidates_rejected():
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 60.9, 0.9),
            _cand(30.0, 90.9, 0.8),  # overlaps the first
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 2)
    assert any("overlap" in e for e in errors)


def test_verbatim_timestamps_enforced():
    # Rounded start — 0.95 isn't a word boundary in the fixture (word 0
    # ends at 0.9). Validator should reject.
    plan = ClipHunterPlan(
        candidates=[
            ClipCandidate(
                start_s=0.95,
                end_s=60.9,
                engagement_score=0.9,
                quote="",
                suggested_caption="",
                reasoning="",
            ),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 1)
    assert any("verbatim" in e for e in errors)


def test_rank_order_violation_rejected():
    # Second candidate is MORE engaging than the first — ranking inverted.
    plan = ClipHunterPlan(
        candidates=[
            _cand(0.0, 60.9, 0.5),
            _cand(61.0, 121.9, 0.9),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 2)
    assert any("ranked descending" in e for e in errors)


def test_negative_duration_rejected():
    plan = ClipHunterPlan(
        candidates=[
            ClipCandidate(
                start_s=50.0,
                end_s=40.0,
                engagement_score=0.8,
                quote="",
                suggested_caption="",
                reasoning="",
            ),
        ]
    )
    errors = validate_clip_hunter_plan(plan, _transcript(), 60.0, 1)
    assert any("end_s" in e and "start_s" in e for e in errors)


# ------------------------- prompt --------------------------------------


def test_prompt_includes_target_length_and_count():
    preset = get_preset("clip_hunter")
    prompt = director._clip_hunter_prompt(
        preset,
        _transcript()[:5],
        {"reorder_allowed": True},
        60.0,
        3,
    )
    assert "Target clip length: 60" in prompt
    assert "Number of candidates: 3" in prompt
    # Range hint (60 ±40%) lands in the rules block.
    assert "36" in prompt and "84" in prompt
    # Descending-rank rule is present.
    assert "descending engagement order" in prompt


def test_prompt_renders_exclude_and_focus_blocks():
    preset = get_preset("clip_hunter")
    prompt = director._clip_hunter_prompt(
        preset,
        _transcript()[:5],
        {"exclude_categories": ["ad_reads"], "custom_focus": "the debate"},
        60.0,
        3,
    )
    assert "EXCLUDE CATEGORIES" in prompt
    assert "Ad / sponsor reads" in prompt
    assert "USER FOCUS" in prompt
    assert "the debate" in prompt


# ------------------------- expansion -----------------------------------


def test_candidate_to_segments_single_entry():
    cand = _cand(10.0, 70.0, 0.8, quote="that line")
    segs = candidate_to_segments(cand)
    assert len(segs) == 1
    assert segs[0].start_s == 10.0
    assert segs[0].end_s == 70.0
    assert "that line" in segs[0].reason


def test_candidate_to_segments_falls_back_to_reasoning_when_quote_empty():
    cand = ClipCandidate(
        start_s=0.0,
        end_s=60.0,
        engagement_score=0.8,
        quote="",
        suggested_caption="",
        reasoning="punchline at the end",
    )
    segs = candidate_to_segments(cand)
    assert "punchline at the end" in segs[0].reason
