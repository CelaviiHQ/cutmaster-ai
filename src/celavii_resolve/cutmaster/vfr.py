"""Variable frame rate detection via ffprobe.

CutMaster refuses to proceed on VFR media because Gemini's word timestamps
will drift relative to video frames (see spec §9 risks). Phase 0
(v0_vfr_detect.py) validated the CFR half on DJI footage; VFR half pending
an iPhone sample.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import mcp
from ..errors import safe_resolve_call


class VFRProbeError(RuntimeError):
    """ffprobe failed or returned unusable output."""


def _ratio(s: str) -> float:
    try:
        num, den = s.split("/")
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    except Exception:
        return 0.0


def detect_vfr(path: Path, tolerance: float = 0.01) -> dict:
    """Probe a media file and return frame-rate-mode info.

    Returns:
        ``{"path": str, "r_frame_rate": str, "avg_frame_rate": str,
           "r_fps": float, "avg_fps": float, "is_vfr": bool,
           "nb_frames": str | None, "duration_s": float | None}``

    Raises:
        FileNotFoundError: file does not exist or ffprobe missing.
        VFRProbeError: ffprobe errored or reported no frame rate.
    """
    if not shutil.which("ffprobe"):
        raise FileNotFoundError("ffprobe not on PATH.")
    if not Path(path).exists():
        raise FileNotFoundError(str(path))

    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate,nb_frames,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise VFRProbeError(f"ffprobe failed: {r.stderr.strip()}")

    streams = json.loads(r.stdout).get("streams") or []
    if not streams:
        raise VFRProbeError("ffprobe returned no video stream.")
    stream = streams[0]
    r_fps_str = stream.get("r_frame_rate", "0/1")
    avg_fps_str = stream.get("avg_frame_rate", "0/1")
    r_fps = _ratio(r_fps_str)
    avg_fps = _ratio(avg_fps_str)
    if r_fps == 0.0 or avg_fps == 0.0:
        raise VFRProbeError(f"Could not determine frame rate (r={r_fps_str}, avg={avg_fps_str}).")

    diff = abs(r_fps - avg_fps) / max(r_fps, avg_fps)
    return {
        "path": str(path),
        "r_frame_rate": r_fps_str,
        "avg_frame_rate": avg_fps_str,
        "r_fps": r_fps,
        "avg_fps": avg_fps,
        "is_vfr": diff > tolerance,
        "nb_frames": stream.get("nb_frames"),
        "duration_s": float(stream["duration"]) if "duration" in stream else None,
    }


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_detect_vfr(path: str, tolerance: float = 0.01) -> str:
    """Detect whether a media file is variable frame rate (VFR).

    Args:
        path: Absolute path to a video file.
        tolerance: Relative difference between r_frame_rate and avg_frame_rate
            above which we declare VFR. Default 0.01 (1%).

    Returns a JSON payload including ``is_vfr``, ``r_fps``, ``avg_fps``,
    ``nb_frames``, and ``duration_s``.
    """
    result = detect_vfr(Path(path), float(tolerance))
    return json.dumps(result)
