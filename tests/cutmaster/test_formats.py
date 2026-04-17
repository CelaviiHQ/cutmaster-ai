"""Tests for cutmaster.formats — FormatSpec registry + aspect helpers."""

import pytest

from celavii_resolve.cutmaster.formats import (
    HORIZONTAL,
    SQUARE,
    VERTICAL_SHORT,
    all_formats,
    detect_source_aspect,
    get_format,
    needs_reframe,
    recommend_format,
)


def test_three_formats_registered():
    keys = [f.key for f in all_formats()]
    assert keys == ["horizontal", "vertical_short", "square"]


def test_get_format_unknown_raises():
    with pytest.raises(KeyError, match="Unknown format"):
        get_format("ultrawide")


def test_aspect_ratios_are_sensible():
    assert HORIZONTAL.aspect > 1.0
    assert VERTICAL_SHORT.aspect < 1.0
    assert SQUARE.aspect == pytest.approx(1.0)


def test_detect_horizontal_source():
    assert detect_source_aspect(1920, 1080) == "horizontal"
    assert detect_source_aspect(3840, 2160) == "horizontal"


def test_detect_vertical_source():
    assert detect_source_aspect(1080, 1920) == "vertical_short"
    # 4K vertical shoot
    assert detect_source_aspect(2160, 3840) == "vertical_short"


def test_detect_square_source():
    assert detect_source_aspect(1080, 1080) == "square"


def test_detect_invalid_dims_defaults_to_horizontal():
    assert detect_source_aspect(0, 0) == "horizontal"
    assert detect_source_aspect(-1, 1080) == "horizontal"


def test_recommend_matches_detection():
    assert recommend_format(1080, 1920).key == "vertical_short"
    assert recommend_format(1920, 1080).key == "horizontal"


def test_needs_reframe_matched_source():
    # 9:16 source → Short: no reframe needed
    assert needs_reframe(VERTICAL_SHORT, 1080, 1920) is False
    # 16:9 source → horizontal: no reframe needed
    assert needs_reframe(HORIZONTAL, 1920, 1080) is False


def test_needs_reframe_mismatched_source():
    # 16:9 source → Short: reframe required
    assert needs_reframe(VERTICAL_SHORT, 1920, 1080) is True
    # 16:9 source → Square: reframe required
    assert needs_reframe(SQUARE, 1920, 1080) is True


def test_short_has_length_cap():
    assert VERTICAL_SHORT.max_duration_s is not None
    assert VERTICAL_SHORT.max_duration_s > 0


def test_horizontal_has_no_length_cap():
    assert HORIZONTAL.max_duration_s is None


def test_short_has_safe_zones():
    # TikTok / Reels UI overlays content — safe zones must be non-zero.
    zones = VERTICAL_SHORT.safe_zones
    assert zones.bottom_pct > 0
    assert zones.right_pct > 0
