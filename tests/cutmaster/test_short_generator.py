"""Tests for Short Generator Director (v2-13)."""

from cutmaster_ai.cutmaster.core.director import (
    ShortCandidate,
    ShortGeneratorPlan,
    ShortSpan,
    short_candidate_to_segments,
    validate_short_generator_plan,
)


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start_time": start, "end_time": end, "speaker_id": "S1"}


def _transcript() -> list[dict]:
    """100-word synthetic transcript covering 0-100s with 1s words."""
    return [_w(f"w{i}", float(i), float(i) + 0.9) for i in range(100)]


def _span(start: float, end: float, role: str = "") -> ShortSpan:
    return ShortSpan(start_s=start, end_s=end, role=role)


def _cand(
    theme: str,
    spans: list[tuple[float, float, str]],
    engagement: float = 0.9,
) -> ShortCandidate:
    span_objs = [_span(s, e, r) for s, e, r in spans]
    total = sum(s.end_s - s.start_s for s in span_objs)
    return ShortCandidate(
        theme=theme,
        spans=span_objs,
        total_s=total,
        engagement_score=engagement,
        suggested_caption="caption",
        reasoning="reasoning",
    )


def test_valid_plan_passes():
    transcript = _transcript()
    # 3 spans of ~20s each starting on word boundaries
    plan = ShortGeneratorPlan(
        candidates=[
            _cand("theme A", [(0.0, 19.9, "hook"), (30.0, 49.9, "setup"), (60.0, 79.9, "payoff")])
        ],
        reasoning="ok",
    )
    assert validate_short_generator_plan(plan, transcript, 60.0, 1) == []


def test_rejects_too_few_spans():
    transcript = _transcript()
    plan = ShortGeneratorPlan(
        candidates=[_cand("one", [(0.0, 29.9, "hook"), (50.0, 79.9, "close")])],
        reasoning="ok",
    )
    errors = validate_short_generator_plan(plan, transcript, 60.0, 1)
    assert any("3–8" in e or "2 spans" in e for e in errors)


def test_rejects_span_over_25s():
    transcript = _transcript()
    plan = ShortGeneratorPlan(
        candidates=[
            _cand(
                "long",
                [(0.0, 29.9, "hook"), (35.0, 44.9, "setup"), (60.0, 79.9, "close")],
            )
        ],
        reasoning="ok",
    )
    errors = validate_short_generator_plan(plan, transcript, 60.0, 1)
    assert any("over 25" in e.lower() or "25 s" in e for e in errors)


def test_rejects_overlapping_spans():
    transcript = _transcript()
    plan = ShortGeneratorPlan(
        candidates=[
            _cand(
                "overlap",
                [
                    (0.0, 19.9, "hook"),
                    (15.0, 34.9, "setup"),  # overlaps with first
                    (40.0, 59.9, "close"),
                ],
            )
        ],
        reasoning="ok",
    )
    errors = validate_short_generator_plan(plan, transcript, 60.0, 1)
    assert any("overlap" in e for e in errors)


def test_rejects_total_outside_tolerance():
    transcript = _transcript()
    # Three ~3s spans = ~9s total, well under the 30s floor (target 60s
    # × (1 - 0.5 tolerance)). Must be rejected.
    plan = ShortGeneratorPlan(
        candidates=[
            _cand(
                "short",
                [(0.0, 2.9, "a"), (10.0, 12.9, "b"), (20.0, 22.9, "c")],
            )
        ],
        reasoning="ok",
    )
    errors = validate_short_generator_plan(plan, transcript, 60.0, 1)
    assert any("total" in e.lower() and "outside" in e for e in errors)


def test_rejects_non_descending_engagement():
    transcript = _transcript()
    plan = ShortGeneratorPlan(
        candidates=[
            _cand(
                "a",
                [(0.0, 19.9, "h"), (25.0, 44.9, "s"), (50.0, 69.9, "p")],
                engagement=0.5,
            ),
            _cand(
                "b",
                [(0.0, 19.9, "h"), (25.0, 44.9, "s"), (50.0, 69.9, "p")],
                engagement=0.9,  # higher than first — must descend
            ),
        ],
        reasoning="ok",
    )
    errors = validate_short_generator_plan(plan, transcript, 60.0, 2)
    assert any("rank descending" in e for e in errors)


def test_validator_overwrites_total_s_with_computed_value():
    """Gemini's arithmetic on multi-span sums is unreliable — the validator
    recomputes total_s from spans and mutates the candidate in place so
    downstream code never sees the wrong number."""
    transcript = _transcript()
    cand = _cand(
        "t",
        [(0.0, 19.9, "h"), (25.0, 44.9, "s"), (50.0, 69.9, "p")],
    )
    real_total = cand.total_s
    # Simulate Gemini reporting a wildly wrong total.
    cand = cand.model_copy(update={"total_s": real_total + 20.0})
    plan = ShortGeneratorPlan(candidates=[cand], reasoning="ok")
    errors = validate_short_generator_plan(plan, transcript, 60.0, 1)
    # No disagreement error — validator just overwrites.
    assert not any("disagrees" in e for e in errors)
    # The candidate's total_s now reflects the real span-sum.
    assert abs(plan.candidates[0].total_s - real_total) < 0.01


def test_short_candidate_to_segments_preserves_order():
    cand = _cand(
        "t",
        [(30.0, 40.9, "hook"), (5.0, 9.9, "setup"), (60.0, 69.9, "close")],
    )
    segs = short_candidate_to_segments(cand)
    # Play order (not source order) must be preserved — jump cuts are legal.
    assert [s.start_s for s in segs] == [30.0, 5.0, 60.0]
    assert [s.end_s for s in segs] == [40.9, 9.9, 69.9]
