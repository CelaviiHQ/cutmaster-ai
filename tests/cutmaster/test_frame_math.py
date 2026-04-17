"""Unit tests for cutmaster.frame_math — no Resolve required."""

import pytest

from celavii_resolve.cutmaster.frame_math import (
    frame_to_seconds,
    seconds_to_frame,
)


class FakeTimeline:
    def __init__(self, fps: float, start_frame: int = 86400) -> None:
        self._fps = fps
        self._start = start_frame

    def GetSetting(self, key: str):
        return self._fps if key == "timelineFrameRate" else None

    def GetStartFrame(self) -> int:
        return self._start


@pytest.mark.parametrize(
    "fps,seconds,expected_frame",
    [
        (24.0, 0.0, 86400),
        (24.0, 1.0, 86424),
        (24.0, 10.0, 86640),
        (23.976, 1.0, 86424),       # banker's rounds 23.976 to 24
        (29.97, 1.001, 86430),       # 29.97 * 1.001 = 30.0000...
        (60.0, 0.5, 86430),
    ],
)
def test_seconds_to_frame(fps, seconds, expected_frame):
    tl = FakeTimeline(fps)
    assert seconds_to_frame(tl, seconds) == expected_frame


def test_start_frame_offset_applied():
    tl = FakeTimeline(24.0, start_frame=0)
    assert seconds_to_frame(tl, 10.0) == 240


def test_frame_to_seconds_roundtrip():
    tl = FakeTimeline(24.0)
    for sec in (0.0, 1.0, 2.5, 10.0):
        assert frame_to_seconds(tl, seconds_to_frame(tl, sec)) == pytest.approx(sec, abs=1 / 48)


def test_bankers_rounding_half_frame():
    tl = FakeTimeline(24.0)
    # 0.5/24 exactly — banker's rounds to even
    # seconds_to_frame(tl, 0.5 / 24) = round(0.5) → 0 (even)
    half_frame_seconds = 0.5 / 24
    result = seconds_to_frame(tl, half_frame_seconds)
    assert result == 86400  # rounded down to even


def test_missing_fps_raises():
    class NoFps:
        def GetSetting(self, _):
            return None

        def GetStartFrame(self):
            return 0

    with pytest.raises(ValueError, match="timelineFrameRate"):
        seconds_to_frame(NoFps(), 1.0)
