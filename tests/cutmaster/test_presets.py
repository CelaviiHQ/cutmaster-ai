"""Tests for cutmaster.presets — bundle completeness + lookup."""

import pytest

from celavii_resolve.cutmaster.presets import PRESETS, all_presets, get_preset

EXPECTED = {"vlog", "product_demo", "wedding", "interview", "tutorial", "podcast", "reaction"}


def test_all_seven_presets_registered():
    assert set(PRESETS) == EXPECTED


def test_each_preset_has_required_fields():
    for bundle in PRESETS.values():
        assert bundle.role, f"{bundle.key} missing role"
        assert bundle.hook_rule, f"{bundle.key} missing hook_rule"
        assert bundle.cue_vocabulary, f"{bundle.key} missing cue_vocabulary"
        assert bundle.marker_vocabulary, f"{bundle.key} missing marker_vocabulary"
        assert bundle.theme_axes, f"{bundle.key} missing theme_axes"


def test_get_preset_returns_bundle():
    vlog = get_preset("vlog")
    assert vlog.key == "vlog"
    assert vlog.label == "Vlog"


def test_get_preset_unknown_raises():
    with pytest.raises(KeyError, match="Unknown preset"):
        get_preset("martian")


def test_all_presets_ordering_is_stable():
    # Same call twice must return identical order (UI depends on this)
    assert [p.key for p in all_presets()] == [p.key for p in all_presets()]


def test_preset_keys_are_url_safe():
    # UI uses these as query/path params
    for key in PRESETS:
        assert key.replace("_", "").isalnum(), f"non-url-safe key: {key}"
