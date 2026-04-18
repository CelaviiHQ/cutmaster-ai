"""Tests for cutmaster.excludes — schema + default-picker helper (v2-0)."""

import pytest
from pydantic import ValidationError

from celavii_resolve.cutmaster.data.excludes import (
    ExcludeCategory,
    default_exclude_keys,
)
from celavii_resolve.cutmaster.data.presets import PRESETS, get_preset


def test_exclude_category_requires_core_fields():
    cat = ExcludeCategory(
        key="vendor_mentions",
        label="Vendor mentions",
        description="Drop audio where the speaker thanks vendors.",
    )
    assert cat.key == "vendor_mentions"
    assert cat.checked_by_default is False


def test_exclude_category_rejects_missing_required():
    with pytest.raises(ValidationError):
        ExcludeCategory(key="x", label="X")  # description missing


def test_default_exclude_keys_returns_only_checked():
    cats = [
        ExcludeCategory(key="a", label="A", description="…", checked_by_default=True),
        ExcludeCategory(key="b", label="B", description="…", checked_by_default=False),
        ExcludeCategory(key="c", label="C", description="…", checked_by_default=True),
    ]
    assert default_exclude_keys(cats) == ["a", "c"]


def test_default_exclude_keys_empty_list():
    assert default_exclude_keys([]) == []


def test_every_content_type_preset_has_exclude_categories_populated():
    # v2-0 landed the schema; v2-1 populates per-preset lists for every
    # content-type preset (Director-driven). Workflow presets like
    # Tightener (v2-3) skip the Director entirely so exclusion categories
    # are irrelevant for them.
    from celavii_resolve.cutmaster.data.presets import PRESETS as _P  # local alias

    content_type_presets = {k for k, p in _P.items() if p.exclude_categories}
    # At minimum the 8 content-type presets must populate excludes.
    assert content_type_presets >= {
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "reaction",
        "clip_hunter",
    }
    for key in content_type_presets:
        bundle = _P[key]
        for cat in bundle.exclude_categories:
            assert isinstance(cat, ExcludeCategory)
            assert cat.key and cat.label and cat.description


def test_every_preset_has_custom_focus_placeholder_field():
    for bundle in PRESETS.values():
        assert isinstance(bundle.default_custom_focus_placeholder, str)


def test_get_preset_model_dump_includes_new_fields():
    # The panel consumes .model_dump() via /cutmaster/presets.
    dumped = get_preset("wedding").model_dump()
    assert "exclude_categories" in dumped
    assert "default_custom_focus_placeholder" in dumped
