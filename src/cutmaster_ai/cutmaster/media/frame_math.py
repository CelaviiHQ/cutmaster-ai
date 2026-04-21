"""Seconds ↔ frame conversion — single source of truth.

Phase 0 validated the Resolve API round-trip (see v0_append_ranges.py and
v0_source_mapping.py). No other module in the pipeline is allowed to convert
seconds to frames; they all call through here so rounding and offset rules
are identical everywhere.
"""

from __future__ import annotations

import json

from ...config import mcp
from ...errors import safe_resolve_call
from ...resolve import _boilerplate

# ---------------------------------------------------------------------------
# Plain functions — called from the HTTP backend and cutmaster pipeline
# ---------------------------------------------------------------------------


def _timeline_fps(tl) -> float:
    fps = tl.GetSetting("timelineFrameRate")
    if fps is None:
        raise ValueError("Timeline has no timelineFrameRate setting.")
    return float(fps)


def _timeline_start_frame(tl) -> int:
    """Return the timeline's starting frame (Resolve's default is 86400)."""
    try:
        return int(tl.GetStartFrame())
    except Exception:
        return 0


def _source_fps(mp_item, fallback: float | None = None) -> float:
    """Return the source media's native frame rate.

    When source fps differs from timeline fps (e.g. 30 fps source on a
    24 fps timeline), Resolve conforms the item to real-time on the
    timeline. Without this value, conversions between timeline-frames and
    source-media-frames silently compress or stretch pieces — the bug
    that parked v2-6 markers past the end of the cut timeline.

    Uses Resolve's ``GetClipProperty("FPS")`` which returns a string like
    ``"24"`` / ``"29.97"``. Falls back to ``fallback`` (typically the
    timeline fps) so callers on single-fps projects don't need to branch.
    Returns ``0.0`` if no usable value can be read.
    """
    if mp_item is None:
        return float(fallback or 0.0)
    try:
        raw = mp_item.GetClipProperty("FPS")
    except Exception:
        return float(fallback or 0.0)
    if not raw:
        return float(fallback or 0.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(fallback or 0.0)
    return value if value > 0 else float(fallback or 0.0)


def seconds_to_frame(tl, seconds: float) -> int:
    """Convert timeline-seconds to absolute timeline frame number.

    Uses banker's rounding (half-to-even) on the fractional frame so that
    consistent half-frame inputs don't bias in one direction.
    """
    fps = _timeline_fps(tl)
    return _timeline_start_frame(tl) + round(seconds * fps)


def frame_to_seconds(tl, frame: int) -> float:
    """Convert an absolute timeline frame to seconds relative to timeline start."""
    fps = _timeline_fps(tl)
    return (int(frame) - _timeline_start_frame(tl)) / fps


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_seconds_to_frame(seconds: float) -> str:
    """Convert timeline-seconds to an absolute timeline frame on the current timeline.

    Args:
        seconds: Time in seconds, relative to the timeline start.

    Returns a JSON string like:
        {"seconds": 12.45, "frame": 86699, "fps": 24.0, "start_frame": 86400}
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    frame = seconds_to_frame(tl, seconds)
    return json.dumps(
        {
            "seconds": float(seconds),
            "frame": int(frame),
            "fps": _timeline_fps(tl),
            "start_frame": _timeline_start_frame(tl),
        }
    )


@mcp.tool
@safe_resolve_call
def cutmaster_frame_to_seconds(frame: int) -> str:
    """Convert an absolute timeline frame to seconds (relative to timeline start).

    Args:
        frame: Timeline frame number.

    Returns a JSON string like:
        {"frame": 86700, "seconds": 12.5, "fps": 24.0, "start_frame": 86400}
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    seconds = frame_to_seconds(tl, int(frame))
    return json.dumps(
        {
            "frame": int(frame),
            "seconds": float(seconds),
            "fps": _timeline_fps(tl),
            "start_frame": _timeline_start_frame(tl),
        }
    )
