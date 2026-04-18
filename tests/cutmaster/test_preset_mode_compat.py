"""Exhaustive Preset × Mode compatibility matrix (v2-11)."""

import pytest

from celavii_resolve.cutmaster.data.presets import (
    PRESETS,
    preset_mode_compatible,
    preset_mode_incompatibility_reason,
)

MODES = ("raw_dump", "rough_cut", "curated", "assembled")

# 36 cells minus "auto" which isn't a real preset (routes resolve it).
REAL_PRESETS = [k for k in PRESETS if k != "auto"]


def test_36_cells_have_only_3_incompatibilities():
    blocked = [(p, m) for p in REAL_PRESETS for m in MODES if not preset_mode_compatible(p, m)]
    assert sorted(blocked) == sorted(
        [
            ("tightener", "raw_dump"),
            ("tightener", "rough_cut"),
            ("tightener", "curated"),
        ]
    )


def test_tightener_only_works_with_assembled():
    for mode in MODES:
        compat = preset_mode_compatible("tightener", mode)
        assert compat is (mode == "assembled"), f"tightener x {mode}"


def test_clip_hunter_compatible_across_all_modes():
    for mode in MODES:
        assert preset_mode_compatible("clip_hunter", mode), f"clip_hunter x {mode}"


@pytest.mark.parametrize(
    "preset",
    ["vlog", "product_demo", "wedding", "interview", "tutorial", "podcast", "reaction"],
)
def test_content_presets_orthogonal_to_mode(preset):
    for mode in MODES:
        assert preset_mode_compatible(preset, mode), f"{preset} x {mode}"


def test_incompatibility_reason_returned_only_for_blocked_combos():
    assert preset_mode_incompatibility_reason("tightener", "raw_dump")
    assert preset_mode_incompatibility_reason("tightener", "assembled") is None
    assert preset_mode_incompatibility_reason("vlog", "curated") is None
