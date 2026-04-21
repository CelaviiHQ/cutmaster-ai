"""Tests for Axis 2 × Axis 4 compatibility matrix."""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.data.axis_compat import (
    cut_intent_mode_compatible,
    cut_intent_mode_incompatibility_reason,
)

CUT_INTENTS = (
    "narrative",
    "peak_highlight",
    "multi_clip",
    "assembled_short",
    "surgical_tighten",
)
TIMELINE_MODES = ("raw_dump", "rough_cut", "curated", "assembled")


def test_all_twenty_cells_produce_boolean() -> None:
    """Every (cut_intent, timeline_mode) cell must evaluate — no KeyErrors."""
    for ci in CUT_INTENTS:
        for tm in TIMELINE_MODES:
            assert isinstance(cut_intent_mode_compatible(ci, tm), bool)


@pytest.mark.parametrize("mode", ["raw_dump", "rough_cut", "curated"])
def test_surgical_tighten_blocks_non_assembled(mode: str) -> None:
    assert not cut_intent_mode_compatible("surgical_tighten", mode)
    reason = cut_intent_mode_incompatibility_reason("surgical_tighten", mode)
    assert reason and "assembled" in reason.lower()


def test_surgical_tighten_allows_assembled() -> None:
    assert cut_intent_mode_compatible("surgical_tighten", "assembled")
    assert cut_intent_mode_incompatibility_reason("surgical_tighten", "assembled") is None


def test_multi_clip_blocks_assembled() -> None:
    assert not cut_intent_mode_compatible("multi_clip", "assembled")
    reason = cut_intent_mode_incompatibility_reason("multi_clip", "assembled")
    assert reason and "multi-clip" in reason.lower()


@pytest.mark.parametrize("mode", ["raw_dump", "rough_cut", "curated"])
def test_multi_clip_allows_non_assembled(mode: str) -> None:
    assert cut_intent_mode_compatible("multi_clip", mode)


@pytest.mark.parametrize("mode", TIMELINE_MODES)
def test_narrative_compatible_with_every_mode(mode: str) -> None:
    assert cut_intent_mode_compatible("narrative", mode)


@pytest.mark.parametrize("mode", TIMELINE_MODES)
def test_peak_highlight_compatible_with_every_mode(mode: str) -> None:
    """Peak highlight × assembled is explicitly allowed per the resolved
    design decision — reuse an already-assembled cut's best moment."""
    assert cut_intent_mode_compatible("peak_highlight", mode)


@pytest.mark.parametrize("mode", TIMELINE_MODES)
def test_assembled_short_compatible_with_every_mode(mode: str) -> None:
    assert cut_intent_mode_compatible("assembled_short", mode)


def test_exactly_four_cells_blocked() -> None:
    """Block set is stable — surgical_tighten×{non-assembled} + multi_clip×assembled."""
    blocked = {
        (ci, tm)
        for ci in CUT_INTENTS
        for tm in TIMELINE_MODES
        if not cut_intent_mode_compatible(ci, tm)
    }
    assert blocked == {
        ("surgical_tighten", "raw_dump"),
        ("surgical_tighten", "rough_cut"),
        ("surgical_tighten", "curated"),
        ("multi_clip", "assembled"),
    }
