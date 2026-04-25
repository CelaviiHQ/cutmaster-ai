"""Tests for the axis resolution pure-function layer."""

from __future__ import annotations

import math

import pytest

from cutmaster_ai.cutmaster.data.axis_resolution import (
    IncompatibleAxesError,
    ResolvedAxes,
    all_matrix_cells,
    resolve_axes,
    resolve_cut_intent,
    resolve_pacing,
    resolve_prompt_builder,
)
from cutmaster_ai.cutmaster.data.content_profiles import get_content_profile

CONTENT_TYPES = (
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
)
CUT_INTENTS = (
    "narrative",
    "peak_highlight",
    "multi_clip",
    "assembled_short",
    "surgical_tighten",
)
TIMELINE_MODES = ("raw_dump", "rough_cut", "curated", "assembled")


# -------------------------------------------------------- matrix coverage


def test_matrix_has_all_forty_cells() -> None:
    assert len(all_matrix_cells()) == 40


def test_every_matrix_cell_resolves_for_compatible_timeline_mode() -> None:
    """Each of the 40 cells should produce a ResolvedAxes for at least one
    compatible timeline_mode — no cell is unreachable."""
    from cutmaster_ai.cutmaster.data.axis_compat import cut_intent_mode_compatible

    for content, intent in all_matrix_cells():
        compatible_modes = [m for m in TIMELINE_MODES if cut_intent_mode_compatible(intent, m)]
        assert compatible_modes, f"{content} × {intent} has no compatible timeline_mode"
        # Pick the first compatible mode and prove resolution works.
        axes = resolve_axes(content, intent, duration_s=120, timeline_mode=compatible_modes[0])
        assert isinstance(axes, ResolvedAxes)


# ---------------------------------------------------------- auto-resolution


@pytest.mark.parametrize(
    "duration_s, expected",
    [
        (44, "peak_highlight"),
        (45, "peak_highlight"),
        (46, "peak_highlight"),
        (119, "peak_highlight"),
        (120, "narrative"),
        (121, "narrative"),
        (599, "narrative"),
        (600, "narrative"),
        (601, "narrative"),
    ],
)
def test_auto_resolution_duration_bands(duration_s: int, expected: str) -> None:
    """Vlog content at each duration threshold — walks the standard bands."""
    intent, _, _ = resolve_cut_intent(
        "vlog", duration_s=duration_s, num_clips=1, timeline_mode="raw_dump"
    )
    # Vlog under 2min defaults to assembled_short (Product Demo / Vlog
    # exception), so 45–120s range diverges from the pure duration table.
    if 45 <= duration_s < 120:
        assert intent == "assembled_short"
    else:
        assert intent == expected


def test_auto_resolution_interview_45s_picks_peak() -> None:
    """Interview content under 45s → peak_highlight (no vlog/product_demo
    exception applies)."""
    intent, _, _ = resolve_cut_intent(
        "interview", duration_s=44, num_clips=1, timeline_mode="raw_dump"
    )
    assert intent == "peak_highlight"


def test_auto_resolution_reaction_under_10min_picks_peak() -> None:
    """Reaction content's mid-band exception — always peak-hunts under 10min."""
    intent, _, _ = resolve_cut_intent(
        "reaction", duration_s=300, num_clips=1, timeline_mode="raw_dump"
    )
    assert intent == "peak_highlight"


def test_auto_resolution_product_demo_under_45s_picks_assembled_short() -> None:
    intent, _, _ = resolve_cut_intent(
        "product_demo", duration_s=30, num_clips=1, timeline_mode="raw_dump"
    )
    assert intent == "assembled_short"


def test_num_clips_gt_one_always_wins() -> None:
    """num_clips > 1 is an explicit user signal — beats every heuristic."""
    for content in CONTENT_TYPES:
        for duration in (10, 90, 300, 1200):
            intent, _, _ = resolve_cut_intent(
                content, duration_s=duration, num_clips=3, timeline_mode="raw_dump"
            )
            assert intent == "multi_clip", f"{content} @ {duration}s"


def test_num_clips_gt_one_beats_surgical_shortcut() -> None:
    """Explicit num_clips overrides the assembled+scrubbed heuristic.

    Confirmed in Phase 1 design review: user-set num_clips is a stronger
    signal than the surgical-tighten autodetect."""
    intent, reason, _ = resolve_cut_intent(
        "interview",
        duration_s=600,
        num_clips=3,
        timeline_mode="assembled",
        takes_already_scrubbed=True,
    )
    assert intent == "multi_clip"
    assert "num_clips" in reason


def test_surgical_shortcut_fires_when_num_clips_is_one() -> None:
    intent, reason, _ = resolve_cut_intent(
        "interview",
        duration_s=600,
        num_clips=1,
        timeline_mode="assembled",
        takes_already_scrubbed=True,
    )
    assert intent == "surgical_tighten"
    assert "scrubbed" in reason


# ------------------------------------------------------------ matrix cells


def test_wedding_narrative_preserves_macro() -> None:
    axes = resolve_axes("wedding", "narrative", duration_s=600, timeline_mode="raw_dump")
    assert axes.reorder_mode == "preserve_macro"


def test_wedding_peak_highlight_frees_reorder() -> None:
    axes = resolve_axes("wedding", "peak_highlight", duration_s=60, timeline_mode="raw_dump")
    assert axes.reorder_mode == "free"


def test_podcast_multi_clip_is_per_clip_chronological() -> None:
    axes = resolve_axes("podcast", "multi_clip", duration_s=1800, timeline_mode="raw_dump")
    assert axes.reorder_mode == "per_clip_chronological"


def test_interview_multi_clip_is_per_clip_chronological() -> None:
    """Confirms per_clip_chronological covers more than one cell — the new
    reorder value serves Interview just as well as Podcast multi-clip."""
    axes = resolve_axes("interview", "multi_clip", duration_s=1800, timeline_mode="raw_dump")
    assert axes.reorder_mode == "per_clip_chronological"


def test_tutorial_multi_clip_is_unusual() -> None:
    axes = resolve_axes("tutorial", "multi_clip", duration_s=1200, timeline_mode="raw_dump")
    assert axes.unusual is True


def test_non_unusual_combinations_flagged_false() -> None:
    axes = resolve_axes("vlog", "narrative", duration_s=600, timeline_mode="raw_dump")
    assert axes.unusual is False


# ------------------------------------------------------------------ pacing


def test_interview_peak_highlight_60s_pacing_matches_example() -> None:
    """Documented example from design doc Appendix: {~3, ~7, ~18}.

    Pacing-curve constants are Phase-6 calibration targets per Open Q 1;
    we assert order-of-magnitude agreement (within 25%) rather than
    exact equality."""
    profile = get_content_profile("interview")
    pacing = resolve_pacing(profile, pacing_modifier=0.35, duration_s=60)
    assert pacing.target == pytest.approx(7, rel=0.25)
    assert pacing.min == pytest.approx(3, rel=0.25)
    assert pacing.max == pytest.approx(18, rel=0.25)
    assert pacing.min < pacing.target < pacing.max


def test_interview_narrative_600s_pacing_matches_example() -> None:
    """Documented example: {~8, ~22, ~55}."""
    profile = get_content_profile("interview")
    pacing = resolve_pacing(profile, pacing_modifier=1.0, duration_s=600)
    assert pacing.target == pytest.approx(22, rel=0.25)
    assert pacing.min == pytest.approx(8, rel=0.25)
    assert pacing.max == pytest.approx(55, rel=0.25)


def test_pacing_min_never_below_two_seconds() -> None:
    """Hard floor — no prompt should ever ask for sub-2s segments."""
    profile = get_content_profile("reaction")
    pacing = resolve_pacing(profile, pacing_modifier=0.2, duration_s=30)
    assert pacing.min >= 2.0


def test_pacing_bounds_monotonic() -> None:
    profile = get_content_profile("vlog")
    for dur in (30, 60, 120, 600, 1800):
        for mod in (0.25, 0.5, 1.0, 1.5):
            p = resolve_pacing(profile, pacing_modifier=mod, duration_s=dur)
            assert p.min < p.target < p.max


def test_pacing_zero_duration_does_not_divide_by_zero() -> None:
    profile = get_content_profile("vlog")
    p = resolve_pacing(profile, pacing_modifier=1.0, duration_s=0)
    assert math.isfinite(p.target) and p.target > 0


# -------------------------------------------------------------- compat layer


def test_surgical_tighten_on_raw_dump_raises() -> None:
    with pytest.raises(IncompatibleAxesError):
        resolve_axes("interview", "surgical_tighten", duration_s=300, timeline_mode="raw_dump")


def test_multi_clip_on_assembled_raises() -> None:
    with pytest.raises(IncompatibleAxesError):
        resolve_axes("vlog", "multi_clip", duration_s=300, timeline_mode="assembled")


def test_resolve_prompt_builder_raises_on_incompatible() -> None:
    with pytest.raises(IncompatibleAxesError):
        resolve_prompt_builder("surgical_tighten", "rough_cut")


# ------------------------------------------------------- prompt-builder routing


@pytest.mark.parametrize(
    "cut_intent, timeline_mode, expected",
    [
        ("narrative", "raw_dump", "_prompt"),
        ("narrative", "rough_cut", "_rough_cut_prompt"),
        ("narrative", "curated", "_curated_prompt"),
        ("narrative", "assembled", "_assembled_prompt"),
        ("peak_highlight", "raw_dump", "_prompt"),
        ("peak_highlight", "rough_cut", "_prompt"),
        ("peak_highlight", "curated", "_prompt"),
        ("peak_highlight", "assembled", "_prompt"),
        ("multi_clip", "raw_dump", "_clip_hunter_prompt"),
        ("multi_clip", "rough_cut", "_clip_hunter_prompt"),
        ("multi_clip", "curated", "_clip_hunter_prompt"),
        ("assembled_short", "raw_dump", "_short_generator_prompt"),
        ("assembled_short", "rough_cut", "_short_generator_prompt"),
        ("assembled_short", "curated", "_short_generator_prompt"),
        ("assembled_short", "assembled", "_short_generator_prompt"),
        ("surgical_tighten", "assembled", "_assembled_prompt"),
    ],
)
def test_prompt_builder_routing(cut_intent: str, timeline_mode: str, expected: str) -> None:
    assert resolve_prompt_builder(cut_intent, timeline_mode) == expected  # type: ignore[arg-type]


# --------------------------------------------------------- reorder override


def test_reorder_allowed_false_forces_locked_even_when_cell_says_free() -> None:
    """Legacy reorder_allowed override: user's explicit "no reorder" setting
    beats the matrix cell."""
    axes = resolve_axes(
        "wedding",
        "peak_highlight",
        duration_s=60,
        timeline_mode="raw_dump",
        reorder_allowed=False,
    )
    assert axes.reorder_mode == "locked"
    assert any("reorder_allowed" in r for r in axes.rationale)


def test_reorder_allowed_false_is_a_no_op_when_cell_is_already_locked() -> None:
    axes = resolve_axes(
        "interview",
        "narrative",
        duration_s=600,
        timeline_mode="raw_dump",
        reorder_allowed=False,
    )
    assert axes.reorder_mode == "locked"
    # No override rationale entry when the cell already locked.
    assert not any("overrides matrix cell" in r for r in axes.rationale)


# --------------------------------------------------------- cut_intent=None


def test_cut_intent_none_triggers_auto_resolution() -> None:
    axes = resolve_axes("interview", None, duration_s=60, timeline_mode="raw_dump")
    assert axes.cut_intent == "peak_highlight"
    assert any("Auto-resolved" in r for r in axes.rationale)


def test_cut_intent_none_with_num_clips_picks_multi_clip() -> None:
    axes = resolve_axes("interview", None, duration_s=600, timeline_mode="raw_dump", num_clips=3)
    assert axes.cut_intent == "multi_clip"


# ----------------------------------------------------------- invalid inputs


def test_auto_detect_content_type_rejected() -> None:
    """resolve_axes never accepts auto_detect — callers must run the cascade
    first. Phase 1 design: ContentType (8) != RequestedContentType (9)."""
    with pytest.raises(KeyError):
        resolve_axes("auto_detect", "narrative", duration_s=60, timeline_mode="raw_dump")  # type: ignore[arg-type]


def test_rationale_is_always_populated() -> None:
    axes = resolve_axes("vlog", "narrative", duration_s=300, timeline_mode="raw_dump")
    assert len(axes.rationale) >= 2  # matrix cell + pacing, at minimum


# ---------------------------------------------------------- cut_intent_source


def test_user_supplied_intent_marked_user_source() -> None:
    """An explicit ``cut_intent`` argument tags the result with ``"user"``."""
    axes = resolve_axes("vlog", "narrative", duration_s=300, timeline_mode="raw_dump")
    assert axes.cut_intent_source == "user"


def test_auto_resolution_marks_auto_source() -> None:
    """Duration-band auto resolution tags the result with ``"auto"``."""
    axes = resolve_axes("interview", None, duration_s=300, timeline_mode="raw_dump")
    assert axes.cut_intent == "narrative"
    assert axes.cut_intent_source == "auto"


def test_num_clips_override_marks_forced_source() -> None:
    """``num_clips > 1`` forces the result regardless of duration — tagged ``"forced"``."""
    axes = resolve_axes("interview", None, duration_s=600, timeline_mode="raw_dump", num_clips=4)
    assert axes.cut_intent == "multi_clip"
    assert axes.cut_intent_source == "forced"


def test_takes_already_scrubbed_marks_forced_source() -> None:
    """Surgical-tighten shortcut on assembled+scrubbed source is a forced override."""
    axes = resolve_axes(
        "vlog",
        None,
        duration_s=600,
        timeline_mode="assembled",
        takes_already_scrubbed=True,
    )
    assert axes.cut_intent == "surgical_tighten"
    assert axes.cut_intent_source == "forced"
