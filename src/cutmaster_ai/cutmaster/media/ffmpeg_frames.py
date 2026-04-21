"""Batched frame extraction via ffmpeg for the v4 vision layers.

Given a source file and a list of timestamps (in source-seconds), pulls
one JPEG frame per timestamp. Called by:

- ``analysis/shot_tagger`` during analyze (one call per timeline item).
- ``analysis/boundary_validator`` during build-plan (last/first frames per
  proposed cut).

Keeps the interface small — returns JPEG bytes so the caller can feed them
straight into the multimodal ``call_structured`` chokepoint without
intermediate temp files. Disk caching of raw JPEGs happens one layer up
(under ``boundary-frames/v1/`` for the validator), not here — shot-tag
caching is keyed on the tag JSON rather than the underlying pixels.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("cutmaster-ai.cutmaster.ffmpeg_frames")


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg is not on PATH.")


def source_key(source_path: str | Path) -> str:
    """Stable sha1 of a source path — used as the cache directory name.

    Callers that want to share cached payloads across source paths (e.g.
    when a user moves media) would need a content hash; path-keyed is the
    v4 design call because per-clip STT uses the same convention and the
    cost of re-tagging on a move is bounded (one Gemini call per item).
    """
    return hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()


def extract_frames(
    source_path: str | Path,
    timestamps_s: list[float],
    *,
    width: int = 640,
    jpeg_quality: int = 5,
    cache_dir: Path | None = None,
) -> list[bytes]:
    """Extract one JPEG per ``timestamps_s`` entry from ``source_path``.

    Returns a parallel list of JPEG bytes. When ``cache_dir`` is given,
    frames land on disk at ``<cache_dir>/<ts_ms>.jpg`` and are reused on
    subsequent calls — the boundary validator relies on this so repeated
    retries proposing the same cut points hit cache.

    Args:
        source_path: Path to the video file.
        timestamps_s: Seconds into the source file, one frame per entry.
        width: Scaled frame width in pixels (height keeps aspect via -1).
            640px keeps payload ~40 KB/frame and the vision model has plenty
            of signal; bumping above 1024 rarely improves tag quality.
        jpeg_quality: ffmpeg ``-q:v`` (1-31, lower = better). 5 is the
            default tradeoff; raise toward 2 for reference caching, lower
            toward 10 for bulk shot-tag sampling if token cost matters.
        cache_dir: When set, reuse / write JPEGs at
            ``<cache_dir>/<int(ts*1000)>.jpg``. Caller is responsible for
            namespacing (typically ``boundary-frames/v1/<source_key>/``).

    Raises:
        FileNotFoundError: ffmpeg not on PATH, or ``source_path`` missing.
        RuntimeError: any ffmpeg subprocess returns non-zero.
    """
    _require_ffmpeg()
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"Source file missing: {src}")

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    frames: list[bytes] = []
    for ts in timestamps_s:
        ts_ms = int(round(float(ts) * 1000))
        cache_path = cache_dir / f"{ts_ms:010d}.jpg" if cache_dir is not None else None

        if cache_path is not None and cache_path.exists():
            frames.append(cache_path.read_bytes())
            continue

        data = _extract_one(src, ts, width=width, jpeg_quality=jpeg_quality)
        if cache_path is not None:
            try:
                cache_path.write_bytes(data)
            except OSError as exc:
                log.warning("frame cache write failed (%s): %s", cache_path, exc)
        frames.append(data)

    return frames


def _extract_one(src: Path, ts_s: float, *, width: int, jpeg_quality: int) -> bytes:
    """Run ffmpeg once, return the JPEG as bytes (piped through stdout).

    ``-ss`` before ``-i`` uses ffmpeg's fast seek which lands on the nearest
    keyframe; accuracy is bounded by GOP size which is fine for shot
    classification / boundary review (both tolerant of ±100ms). If a caller
    later needs frame-accurate extraction they can move ``-ss`` after ``-i``
    — at ~5x the decode cost.
    """
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, ts_s):.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-1",
        "-q:v",
        str(int(jpeg_quality)),
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, check=False)
    if r.returncode != 0 or not r.stdout:
        stderr = r.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg frame extract failed on {src.name} @ {ts_s:.3f}s: {stderr}")
    return r.stdout
