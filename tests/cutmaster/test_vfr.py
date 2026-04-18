"""Unit tests for cutmaster.vfr — pure-logic parts only, no ffprobe needed."""

import pytest

from celavii_resolve.cutmaster.media.vfr import _ratio


@pytest.mark.parametrize(
    "s,expected",
    [
        ("30000/1001", 29.97002997),
        ("24/1", 24.0),
        ("0/0", 0.0),
        ("not a ratio", 0.0),
        ("", 0.0),
    ],
)
def test_ratio_parsing(s, expected):
    assert _ratio(s) == pytest.approx(expected)
