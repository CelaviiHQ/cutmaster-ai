"""Tests for cutmaster.excludes — schema + default-picker helper (v2-0)."""

import pytest
from pydantic import ValidationError

from celavii_resolve.cutmaster.excludes import (
    ExcludeCategory,
    default_exclude_keys,
)
from celavii_resolve.cutmaster.presets import PRESETS, get_preset


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


def test_every_preset_has_exclude_categories_populated():
    # v2-0 landed the schema; v2-1 populates per-preset lists. Every
    # preset must now ship a non-empty, well-typed category list.
    for bundle in PRESETS.values():
        assert bundle.exclude_categories, (
            f"{bundle.key} ships no exclude_categories — v2-1 requires ≥1"
        )
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
