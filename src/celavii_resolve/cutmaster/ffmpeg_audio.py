"""Extract the current timeline's audio to a WAV via ffmpeg on source files.

Bypasses Resolve's render queue (which locks the UI) by reading source media
paths directly and reassembling audio to match the edit list. Validated in
phase 0 (v0_ffmpeg_concat.py) with 0-frame drift on a 9-segment DJI timeline.

Falls back with a clear error if any timeline item has no accessible source
path — the caller can switch to ``celavii_quick_deliver`` audio-only.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate
from .frame_math import _source_fps, _timeline_fps


def _require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FileNotFoundError(f"{tool} is not on PATH.")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def extract_timeline_audio(
    tl,
    out_path: Path,
    track_index: int = 1,
    sample_rate: int = 16000,
    channels: int = 1,
) -> dict:
    """Concatenate audio from the timeline's source files into ``out_path``.

    Returns ``{"path": str, "duration_s": float, "segments": int, "sample_rate": int}``.

    Raises:
        FileNotFoundError: ffmpeg/ffprobe not on PATH, or a source file is gone.
        ValueError: the track is empty or items have no media pool item.
    """
    _require_ffmpeg()

    fps = _timeline_fps(tl)
    items = tl.GetItemListInTrack("audio", track_index) or []
    if not items:
        raise ValueError(f"No items on audio track {track_index}.")

    segments: list[tuple[Path, float, float]] = []
    for item in items:
        mp_item = item.GetMediaPoolItem()
        if not mp_item:
            raise ValueError(
                f"Audio item '{item.GetName()}' has no media pool item "
                "(compound/nested/generator). Cannot reconstruct via ffmpeg."
            )
        src_str = mp_item.GetClipProperty("File Path")
        if not src_str:
            raise ValueError(f"Audio item '{mp_item.GetName()}' has no File Path.")
        src = Path(src_str)
        if not src.exists():
            raise FileNotFoundError(f"Source file missing: {src}")

        duration_frames = item.GetDuration()
        try:
            src_start_frame = item.GetSourceStartFrame() or 0
        except Exception:
            src_start_frame = 0

        # `src_start_frame` is in source-media frames; when source fps differs
        # from timeline fps (e.g. 30 fps source on a 24 fps timeline), dividing
        # by `fps` would seek ffmpeg to the wrong moment. Use the source's
        # native fps for the seek, and the timeline fps for the real-time
        # duration (which Resolve has already conformed).
        src_fps = _source_fps(mp_item, fallback=fps)
        in_s = src_start_frame / src_fps
        out_s = in_s + duration_frames / fps
        segments.append((src, in_s, out_s))

    out_path = Path(out_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        seg_wavs: list[Path] = []
        for i, (src, in_s, out_s) in enumerate(segments):
            wav = tmp_dir / f"seg_{i:03d}.wav"
            r = _run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{in_s:.3f}", "-to", f"{out_s:.3f}",
                "-i", str(src),
                "-vn", "-ac", str(channels), "-ar", str(sample_rate),
                str(wav),
            ])
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg extract failed on {src.name}: {r.stderr.strip()}")
            seg_wavs.append(wav)

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text("".join(f"file '{w}'\n" for w in seg_wavs))

        r = _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", str(out_path),
        ])
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {r.stderr.strip()}")

    duration_s = sum(out_s - in_s for _, in_s, out_s in segments)
    return {
        "path": str(out_path),
        "duration_s": duration_s,
        "segments": len(segments),
        "sample_rate": sample_rate,
        "channels": channels,
    }


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_extract_timeline_audio(
    out_path: str = "",
    track_index: int = 1,
    sample_rate: int = 16000,
    channels: int = 1,
) -> str:
    """Extract the current timeline's audio to a WAV via ffmpeg (source files).

    Args:
        out_path: Destination WAV path. If empty, writes to
            ``~/Documents/celavii-extracts/<timeline>_<ts>.wav``.
        track_index: 1-based audio track to read (default A1).
        sample_rate: Output sample rate (default 16000 Hz, optimal for STT).
        channels: Output channel count (default 1 mono).

    Returns a JSON payload with ``path``, ``duration_s``, ``segments``.

    Notes:
        - Bypasses Resolve's render queue, so the UI does not lock.
        - Fails if any timeline item is a compound/nested/generator or if
          the source file has moved. In that case fall back to
          ``celavii_quick_deliver`` with an audio-only preset.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    if not out_path:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        extract_dir = Path.home() / "Documents" / "celavii-extracts"
        extract_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(extract_dir / f"{tl.GetName()}_{ts}.wav")

    result = extract_timeline_audio(tl, Path(out_path), int(track_index),
                                    int(sample_rate), int(channels))
    return json.dumps(result)
