"""Tests for cutmaster.presets — bundle completeness + lookup."""

import pytest

from cutmaster_ai.cutmaster.data.presets import PRESETS, all_presets, get_preset

# Content-type presets run the Director + Marker pipeline and must carry
# cue / marker / theme vocabulary so the agents have something to reason
# about. Workflow presets (v2-3's tightener) skip the LLMs entirely and
# are exempt from those invariants.
CONTENT_TYPE_PRESETS = {
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
    "clip_hunter",
    "short_generator",
}
WORKFLOW_PRESETS = {"tightener"}


def test_all_expected_presets_registered():
    assert set(PRESETS) == CONTENT_TYPE_PRESETS | WORKFLOW_PRESETS


def test_each_content_type_preset_has_required_fields():
    for key in CONTENT_TYPE_PRESETS:
        bundle = PRESETS[key]
        assert bundle.role, f"{bundle.key} missing role"
        assert bundle.hook_rule, f"{bundle.key} missing hook_rule"
        assert bundle.cue_vocabulary, f"{bundle.key} missing cue_vocabulary"
        assert bundle.marker_vocabulary, f"{bundle.key} missing marker_vocabulary"
        assert bundle.theme_axes, f"{bundle.key} missing theme_axes"


def test_every_preset_has_role_and_hook_rule():
    # Universal even for workflow presets — the UI uses these to describe
    # the preset in the picker.
    for bundle in PRESETS.values():
        assert bundle.role
        assert bundle.hook_rule


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
