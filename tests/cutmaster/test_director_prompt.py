"""Tests for Director prompt rendering (v2-1).

Covers the EXCLUDE CATEGORIES and USER FOCUS blocks. We don't call the
LLM — we just inspect the prompt string the Director would send.
"""

from celavii_resolve.cutmaster import director
from celavii_resolve.cutmaster.presets import get_preset

TRANSCRIPT = [
    {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
]


def test_prompt_without_excludes_or_focus_has_no_optional_blocks():
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert "EXCLUDE CATEGORIES" not in prompt
    assert "USER FOCUS" not in prompt


def test_prompt_with_selected_excludes_renders_labels_and_descriptions():
    preset = get_preset("wedding")
    settings = {
        "exclude_categories": ["mc_talking", "vendor_mentions"],
        "custom_focus": None,
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "EXCLUDE CATEGORIES" in prompt
    # Labels (human, not keys) must be rendered so the LLM can reason.
    assert "MC / DJ housekeeping" in prompt
    assert "Vendor mentions" in prompt
    # Descriptions must also appear.
    assert "caterers, florists" in prompt
    # Unselected categories must NOT leak into the prompt.
    assert "Legal formalities" not in prompt


def test_prompt_drops_unknown_exclude_keys_silently():
    """UI bugs that send a key the preset doesn't declare must not crash
    the Director. Unknown keys are filtered; known keys still render."""
    preset = get_preset("wedding")
    settings = {
        "exclude_categories": ["mc_talking", "ghost_category_that_does_not_exist"],
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "MC / DJ housekeeping" in prompt
    assert "ghost_category" not in prompt


def test_prompt_with_custom_focus_renders_focus_block():
    preset = get_preset("product_demo")
    settings = {"custom_focus": "emphasise battery life"}
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "USER FOCUS" in prompt
    assert "emphasise battery life" in prompt


def test_prompt_with_blank_focus_is_ignored():
    preset = get_preset("vlog")
    settings = {"custom_focus": "   "}
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "USER FOCUS" not in prompt


def test_prompt_with_both_excludes_and_focus():
    preset = get_preset("podcast")
    settings = {
        "exclude_categories": ["ad_reads"],
        "custom_focus": "keep the debate about remote work",
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "EXCLUDE CATEGORIES" in prompt
    assert "Ad / sponsor reads" in prompt
    assert "USER FOCUS" in prompt
    assert "keep the debate about remote work" in prompt


def test_every_content_type_preset_bundles_exclude_categories_and_placeholder():
    """v2-1 exit criterion: every Director-driven preset ships exclusion
    options + a focus placeholder hint. Workflow presets like Tightener
    (v2-3) skip the Director and don't need them."""
    from celavii_resolve.cutmaster.presets import PRESETS

    content_type_presets = [
        "vlog", "product_demo", "wedding", "interview",
        "tutorial", "podcast", "reaction",
    ]
    for key in content_type_presets:
        bundle = PRESETS[key]
        assert bundle.exclude_categories, (
            f"{bundle.key} has no exclude_categories — v2-1 expected ≥4 per preset"
        )
        assert len(bundle.exclude_categories) >= 4, (
            f"{bundle.key} has only {len(bundle.exclude_categories)} categories; "
            f"v2-1 spec calls for ≥4–6 per preset"
        )
        assert bundle.default_custom_focus_placeholder.strip(), (
            f"{bundle.key} has an empty custom-focus placeholder"
        )


def test_exclude_category_keys_are_unique_per_preset():
    from celavii_resolve.cutmaster.presets import PRESETS

    for bundle in PRESETS.values():
        keys = [c.key for c in bundle.exclude_categories]
        assert len(keys) == len(set(keys)), f"{bundle.key} has duplicate keys"
