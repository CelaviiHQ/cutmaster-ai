"""Tests for Axis 1 content profiles.

Verifies:
  - All 8 content types are present (Presentation included).
  - Every profile lifts its fields from the matching legacy PresetBundle
    so the dual-model window stays in sync.
  - Field sanity: cue vocab non-empty, pacing default in a plausible
    range, exclude categories populated.
"""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.data.content_profiles import (
    CONTENT_PROFILES,
    all_content_profiles,
    get_content_profile,
)
from cutmaster_ai.cutmaster.data.presets import PRESETS

EXPECTED_KEYS = {
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
}


def test_eight_content_profiles_present() -> None:
    assert set(CONTENT_PROFILES) == EXPECTED_KEYS


def test_presentation_is_first_class() -> None:
    presentation = get_content_profile("presentation")
    assert presentation.key == "presentation"
    assert presentation.label == "Presentation / Keynote"
    assert presentation.speaker_awareness  # non-empty
    assert presentation.default_target_segment_s == 45.0


def test_all_content_profiles_returns_canonical_order() -> None:
    keys = [p.key for p in all_content_profiles()]
    assert keys == [
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "presentation",
        "reaction",
    ]


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_content_profile_round_trips_from_preset(key: str) -> None:
    profile = get_content_profile(key)
    preset = PRESETS[key]
    assert profile.label == preset.label
    assert profile.role == preset.role
    assert profile.hook_rule == preset.hook_rule
    assert profile.cue_vocabulary == preset.cue_vocabulary
    assert profile.marker_vocabulary == preset.marker_vocabulary
    assert profile.theme_axes == preset.theme_axes
    assert profile.scrub_defaults == preset.scrub_defaults
    assert profile.default_target_segment_s == preset.target_segment_s
    assert profile.default_min_segment_s == preset.min_segment_s
    assert profile.default_max_segment_s == preset.max_segment_s
    assert profile.default_reorder_mode == preset.reorder_mode
    assert profile.speaker_awareness == preset.speaker_awareness


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_content_profile_core_fields_sane(key: str) -> None:
    p = get_content_profile(key)
    assert p.cue_vocabulary, f"{key} has empty cue_vocabulary"
    assert p.theme_axes, f"{key} has empty theme_axes"
    assert p.exclude_categories, f"{key} has empty exclude_categories"
    assert 5.0 <= p.default_target_segment_s <= 60.0
    assert p.default_min_segment_s < p.default_target_segment_s
    assert p.default_target_segment_s < p.default_max_segment_s


def test_get_content_profile_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        get_content_profile("not_a_content_type")


def test_get_content_profile_rejects_legacy_cut_intent_keys() -> None:
    # The three legacy cut-intent presets must NOT be content types.
    for intent_key in ("tightener", "clip_hunter", "short_generator"):
        with pytest.raises(KeyError):
            get_content_profile(intent_key)


def test_get_content_profile_rejects_auto_detect() -> None:
    """``auto_detect`` is a RequestedContentType only — it's a sentinel for
    "ask the cascade" and must never resolve to a profile directly."""
    with pytest.raises(KeyError):
        get_content_profile("auto_detect")


def test_requested_content_type_superset_of_content_type() -> None:
    """RequestedContentType = ContentType + {auto_detect}. Keep them aligned
    so wire-side code and resolved-side code can't drift."""
    from typing import get_args

    from cutmaster_ai.cutmaster.data.content_profiles import (
        ContentType,
        RequestedContentType,
    )

    resolved = set(get_args(ContentType))
    requested = set(get_args(RequestedContentType))
    assert requested - resolved == {"auto_detect"}
    assert resolved - requested == set()
