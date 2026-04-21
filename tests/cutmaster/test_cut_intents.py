"""Tests for Axis 2 cut-intent bundles."""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.data.cut_intents import (
    CUT_INTENTS,
    all_cut_intents,
    get_cut_intent,
)

EXPECTED_KEYS = {
    "narrative",
    "peak_highlight",
    "multi_clip",
    "assembled_short",
    "surgical_tighten",
}

VALID_STRATEGIES = {
    "narrative-arc",
    "peak-hunt",
    "top-n",
    "montage",
    "preserve-takes",
}


def test_five_cut_intents_present() -> None:
    assert set(CUT_INTENTS) == EXPECTED_KEYS


def test_all_cut_intents_returns_canonical_order() -> None:
    keys = [c.key for c in all_cut_intents()]
    assert keys == [
        "narrative",
        "peak_highlight",
        "multi_clip",
        "assembled_short",
        "surgical_tighten",
    ]


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_cut_intent_basic_fields(key: str) -> None:
    ci = get_cut_intent(key)
    assert ci.key == key
    assert ci.label
    assert ci.description
    assert ci.selection_strategy in VALID_STRATEGIES
    assert 0.2 <= ci.pacing_modifier <= 2.0


def test_pacing_modifier_bounds_rejected_below() -> None:
    # Pydantic should reject modifiers outside [0.2, 2.0].
    from cutmaster_ai.cutmaster.data.cut_intents import CutIntentBundle

    with pytest.raises(ValueError):
        CutIntentBundle(
            key="narrative",
            label="bad",
            description="bad",
            selection_strategy="narrative-arc",
            pacing_modifier=0.1,
        )


def test_pacing_modifier_bounds_rejected_above() -> None:
    from cutmaster_ai.cutmaster.data.cut_intents import CutIntentBundle

    with pytest.raises(ValueError):
        CutIntentBundle(
            key="narrative",
            label="bad",
            description="bad",
            selection_strategy="narrative-arc",
            pacing_modifier=2.5,
        )


def test_narrative_inherits_everything_from_content() -> None:
    """Narrative is the "default" — no overrides, inherits all content
    profile values."""
    ci = get_cut_intent("narrative")
    assert ci.default_reorder_mode is None
    assert ci.role_override is None
    assert ci.hook_rule_override is None
    assert ci.marker_vocabulary_override is None


def test_multi_clip_overrides_role_and_marker() -> None:
    ci = get_cut_intent("multi_clip")
    assert ci.role_override and "viral" in ci.role_override.lower()
    assert ci.marker_vocabulary_override == ["Clip: {topic}", "Hook: {line}"]


def test_assembled_short_overrides_role_and_marker() -> None:
    ci = get_cut_intent("assembled_short")
    assert ci.role_override and "tiktok" in ci.role_override.lower()
    assert ci.marker_vocabulary_override == ["Hook: {line}", "Beat: {topic}"]
    # Shorts compress pacing — modifier must be below 1.0.
    assert ci.pacing_modifier < 1.0


def test_surgical_tighten_forces_locked_reorder() -> None:
    ci = get_cut_intent("surgical_tighten")
    assert ci.default_reorder_mode == "locked"
    assert ci.role_override and "tightener" in ci.role_override.lower()
    # No Marker LLM runs for tightener — marker vocab empty.
    assert ci.marker_vocabulary_override == []


def test_peak_highlight_frees_reorder_and_tightens_pacing() -> None:
    ci = get_cut_intent("peak_highlight")
    assert ci.default_reorder_mode == "free"
    assert ci.pacing_modifier < 1.0
    assert ci.hook_rule_override and "highest" in ci.hook_rule_override.lower()


def test_get_cut_intent_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        get_cut_intent("not_a_cut_intent")


def test_get_cut_intent_rejects_content_type_keys() -> None:
    # Content-type keys must NOT be cut intents.
    for content_key in ("vlog", "interview", "presentation"):
        with pytest.raises(KeyError):
            get_cut_intent(content_key)
