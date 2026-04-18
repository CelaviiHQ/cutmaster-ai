"""Per-clip STT — transcribe each timeline audio item separately (v2-6).

v1 concatenated the whole timeline into one WAV and handed it to Gemini.
That works but throws away per-clip context: the Director has no idea
which words came from which source file, and can't reason about clip
metadata (duration, camera shot time, file name) when picking takes.

v2-6 runs STT per timeline audio item:

  - one WAV per item (no concat)
  - one Gemini call per WAV, parallel via asyncio.to_thread
  - word timestamps are local to each clip (0 = clip start); we offset them
    by the clip's timeline position so the downstream transcript is still
    expressed in timeline seconds (same contract v1 had)
  - every word gains ``clip_index`` + ``clip_metadata`` so the Director
    prompt can show a metadata table

Cache: per-clip results keyed by ``(source_path, src_in_frame, src_out_frame)``
land under ``~/.celavii/cutmaster/per-clip-stt/<sha1>.json``. Re-running
analyze on a modified timeline only re-transcribes items whose source range
changed; untouched takes are loaded from disk (a full re-transcribe of a
15-take wedding interview dropped from ~40s to ~4s in the prototype).

All helpers here are pure-Python — the Resolve-facing
:func:`build_clip_audio_specs` is guarded so unit tests can exercise every
other path without Resolve or ffmpeg.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("celavii-resolve.cutmaster.per_clip_stt")


CACHE_ROOT = Path.home() / ".celavii" / "cutmaster" / "per-clip-stt"


@dataclass
class ClipAudioSpec:
    """Metadata + file pointers for one timeline audio item.

    ``source_in_frame`` / ``source_out_frame`` pin the cache key so that
    trimming a take invalidates its cache entry while leaving sibling
    takes untouched.
    """

    item_index: int  # 0-based within the audio track
    source_name: str  # media-pool clip name (user-facing)
    source_path: str  # absolute path to source file
    source_in_frame: int  # inclusive start frame in source
    source_out_frame: int  # exclusive end frame in source
    timeline_offset_s: float  # timeline seconds where this clip starts
    duration_s: float
    wav_path: str = ""  # filled by extract_per_clip_audio

    @property
    def cache_key(self) -> str:
        payload = f"{self.source_path}|{self.source_in_frame}|{self.source_out_frame}|v1"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def metadata(self) -> dict:
        """User-facing metadata attached to every word from this clip."""
        return {
            "source_name": self.source_name,
            "source_path": self.source_path,
            "duration_s": round(self.duration_s, 3),
            "timeline_offset_s": round(self.timeline_offset_s, 3),
            "source_in_frame": self.source_in_frame,
            "source_out_frame": self.source_out_frame,
        }


# ---------------------------------------------------------------------------
# Timeline → specs (Resolve-facing)
# ---------------------------------------------------------------------------


def build_clip_audio_specs(tl, track_index: int = 1) -> list[ClipAudioSpec]:
    """Read audio track ``track_index`` and return one ``ClipAudioSpec`` per item.

    Mirrors :func:`ffmpeg_audio.extract_timeline_audio`'s walk but keeps each
    item separate. Items without a media-pool backing (compound/nested
    clips) are skipped with a warning — per-clip STT can't handle them yet.
    """
    from .frame_math import _timeline_fps, _timeline_start_frame

    fps = _timeline_fps(tl)
    tl_start = _timeline_start_frame(tl)
    items = tl.GetItemListInTrack("audio", track_index) or []
    out: list[ClipAudioSpec] = []

    for idx, item in enumerate(items):
        mp_item = item.GetMediaPoolItem()
        if not mp_item:
            log.warning(
                "Audio item %d has no media pool item (compound/nested); "
                "skipping per-clip STT for this take",
                idx,
            )
            continue
        src_path = mp_item.GetClipProperty("File Path") or ""
        if not src_path:
            log.warning("Audio item %d has no File Path; skipping", idx)
            continue

        duration_frames = item.GetDuration()
        try:
            src_start = item.GetSourceStartFrame() or 0
        except Exception:
            src_start = 0
        src_end = src_start + duration_frames

        timeline_offset_frame = item.GetStart() - tl_start

        out.append(
            ClipAudioSpec(
                item_index=idx,
                source_name=str(mp_item.GetName() or f"item_{idx}"),
                source_path=str(src_path),
                source_in_frame=int(src_start),
                source_out_frame=int(src_end),
                timeline_offset_s=timeline_offset_frame / fps,
                duration_s=duration_frames / fps,
            )
        )

    return out


# ---------------------------------------------------------------------------
# ffmpeg — one WAV per spec
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg is not on PATH.")


def extract_per_clip_audio(
    specs: list[ClipAudioSpec],
    out_dir: Path,
    sample_rate: int = 16000,
    channels: int = 1,
) -> list[ClipAudioSpec]:
    """Extract one WAV per spec into ``out_dir``. Mutates specs' ``wav_path``.

    The FPS hint used to derive source frames lives on the spec indirectly
    (timeline-offset + duration); for ffmpeg we work in seconds from the
    source start — converting frames back via ``source_in_frame / fps``
    would introduce drift, so we use the duration directly alongside the
    frame-derived in-point via ``source_in_frame``. Because ffmpeg's ``-ss``
    accepts fractional seconds, we read FPS from each source via ffprobe
    only as a last resort; specs already carry the timeline fps through
    ``timeline_offset_s`` and ``duration_s``.
    """
    _require_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        wav = out_dir / f"clip_{spec.item_index:03d}_{spec.cache_key[:8]}.wav"
        src = Path(spec.source_path)
        if not src.exists():
            raise FileNotFoundError(f"Source file missing: {src}")

        # We don't know the source fps from the spec alone — but ffmpeg's
        # -ss/-to takes seconds, and the duration is authoritative.
        # Convert source-in-frame to seconds via ffprobe once per source if
        # we need it; a simpler, equally correct approach is to use the
        # timeline-derived duration and the spec's source-in offset in
        # frames * (1 / src_fps). To avoid another ffprobe dance we expose
        # the FPS back-derived from the caller: specs built from a Resolve
        # timeline carry ``source_in_frame`` already in source frames, so
        # we need the source fps — ffprobe it lazily.
        src_fps = _probe_fps(src)
        in_s = spec.source_in_frame / src_fps
        out_s = in_s + spec.duration_s

        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{in_s:.3f}",
                "-to",
                f"{out_s:.3f}",
                "-i",
                str(src),
                "-vn",
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                str(wav),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg per-clip extract failed on {src.name}: {r.stderr.strip()}")
        spec.wav_path = str(wav)

    return specs


def _probe_fps(src: Path) -> float:
    """Return the media fps via ffprobe, or 24.0 as a last-ditch fallback.

    Audio-only sources return 0 — the caller uses timeline-derived duration
    in that case, so we just need a non-zero divisor.
    """
    if not shutil.which("ffprobe"):
        return 24.0
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "json",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return 24.0
    try:
        payload = json.loads(r.stdout)
        streams = payload.get("streams") or []
        if not streams:
            return 24.0
        rate = streams[0].get("r_frame_rate", "24/1")
        num, den = rate.split("/")
        fps = float(num) / max(float(den), 1.0)
        return fps if fps > 0 else 24.0
    except Exception:
        return 24.0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_path_for(spec: ClipAudioSpec, root: Path | None = None) -> Path:
    base = root or CACHE_ROOT
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{spec.cache_key}.json"


def load_cached_words(
    spec: ClipAudioSpec,
    root: Path | None = None,
) -> list[dict] | None:
    """Return the cached word list for ``spec`` or None on miss."""
    path = cache_path_for(spec, root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        log.warning("per-clip cache %s unreadable: %s", path, exc)
        return None
    words = payload.get("words")
    if not isinstance(words, list):
        return None
    return words


def save_cached_words(
    spec: ClipAudioSpec,
    words: list[dict],
    root: Path | None = None,
) -> None:
    path = cache_path_for(spec, root)
    payload = {
        "cache_key": spec.cache_key,
        "source_path": spec.source_path,
        "source_in_frame": spec.source_in_frame,
        "source_out_frame": spec.source_out_frame,
        "words": words,
    }
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------


def _stitch_one(spec: ClipAudioSpec, clip_words: list[dict]) -> list[dict]:
    """Offset per-clip word timestamps into timeline seconds + annotate.

    Words whose ``end_time`` exceeds the clip's duration are silently
    dropped — Gemini occasionally extrapolates past the clip end, same
    failure mode v1 guarded against at the global level.
    """
    metadata = spec.metadata()
    offset = spec.timeline_offset_s
    limit = spec.duration_s + 0.25  # grace

    stitched: list[dict] = []
    for w in clip_words:
        end = float(w.get("end_time", 0.0))
        if end > limit:
            continue
        start = float(w.get("start_time", 0.0))
        stitched.append(
            {
                "word": w.get("word", ""),
                "speaker_id": w.get("speaker_id", "S1"),
                "start_time": round(start + offset, 3),
                "end_time": round(end + offset, 3),
                "clip_index": spec.item_index,
                "clip_metadata": metadata,
            }
        )
    return stitched


def stitch_transcripts(
    specs: list[ClipAudioSpec],
    per_clip_words: list[list[dict]],
) -> list[dict]:
    """Flatten per-clip word lists into one timeline-ordered transcript.

    ``per_clip_words[i]`` is the STT result for ``specs[i]``; the two lists
    must share an index. Output is sorted by timeline ``start_time`` so the
    Director still sees words in playback order even if upstream sorted
    specs differently.
    """
    if len(specs) != len(per_clip_words):
        raise ValueError(
            f"specs ({len(specs)}) and per_clip_words ({len(per_clip_words)}) "
            "must have the same length"
        )
    flat: list[dict] = []
    for spec, words in zip(specs, per_clip_words, strict=True):
        flat.extend(_stitch_one(spec, words))
    flat.sort(key=lambda w: w["start_time"])
    return flat


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def transcribe_per_clip(
    specs: list[ClipAudioSpec],
    *,
    use_cache: bool = True,
    cache_root: Path | None = None,
    max_concurrency: int = 4,
    transcribe_fn=None,
    provider: str | None = None,
) -> tuple[list[dict], dict]:
    """Run STT across every spec, returning ``(stitched_transcript, stats)``.

    ``transcribe_fn(spec) -> list[dict]`` is injectable so tests avoid
    Gemini; default implementation calls :func:`stt.transcribe_audio` on
    ``spec.wav_path`` and unwraps the Pydantic result.

    ``provider`` overrides the STT backend per run (``"gemini"`` or
    ``"deepgram"``); used by pipeline to honour the user's Preset-screen
    choice without mutating env vars. When ``transcribe_fn`` is supplied
    the provider value is ignored (tests own the dispatch).

    Cache isolation: when ``cache_root`` is left as ``None``, each
    provider gets its own subdirectory under :data:`CACHE_ROOT`
    (``per-clip-stt/<provider>/``) so switching providers mid-run never
    serves a stale cross-provider transcript. Tests supply an explicit
    ``cache_root`` and bypass that logic.

    Stats: ``{"cache_hits": n, "cache_misses": n, "dropped": n}``.
    """
    if transcribe_fn is None:
        transcribe_fn = _make_default_transcribe(provider)

    if cache_root is None and provider:
        cache_root = CACHE_ROOT / provider.lower()

    sem = asyncio.Semaphore(max(1, max_concurrency))
    results: list[list[dict]] = [[] for _ in specs]
    stats = {"cache_hits": 0, "cache_misses": 0, "dropped": 0}

    async def _one(i: int, spec: ClipAudioSpec) -> None:
        if use_cache:
            cached = load_cached_words(spec, cache_root)
            if cached is not None:
                results[i] = cached
                stats["cache_hits"] += 1
                return
        stats["cache_misses"] += 1
        async with sem:
            words = await asyncio.to_thread(transcribe_fn, spec)
        if use_cache:
            save_cached_words(spec, words, cache_root)
        results[i] = words

    await asyncio.gather(*(_one(i, s) for i, s in enumerate(specs)))

    stitched = stitch_transcripts(specs, results)
    stats["dropped"] = sum(len(w) for w in results) - len(stitched)
    return stitched, stats


def _make_default_transcribe(provider: str | None):
    """Bind ``provider`` into a closure matching ``transcribe_fn``'s signature."""

    def _run(spec: ClipAudioSpec) -> list[dict]:
        if not spec.wav_path:
            raise ValueError(
                f"spec for item {spec.item_index} has no wav_path — call "
                "extract_per_clip_audio first"
            )
        from .stt import transcribe_audio  # lazy

        resp = transcribe_audio(Path(spec.wav_path), provider=provider)
        return [w.model_dump() for w in resp.words]

    return _run


# Kept for callers that imported the old name directly.
def _default_transcribe(spec: ClipAudioSpec) -> list[dict]:
    return _make_default_transcribe(None)(spec)


# ---------------------------------------------------------------------------
# Prompt helper
# ---------------------------------------------------------------------------


def clip_metadata_table(transcript: list[dict]) -> str:
    """Render a compact markdown table of clip metadata for the Director.

    Consumed by :func:`director._clip_metadata_block`. Returns an empty
    string when the transcript has no ``clip_metadata`` annotations (v1
    transcripts + the concatenated-audio path both satisfy that).
    """
    seen: dict[int, dict] = {}
    for w in transcript:
        idx = w.get("clip_index")
        if idx is None:
            continue
        if idx not in seen:
            seen[idx] = w.get("clip_metadata") or {}
    if not seen:
        return ""

    lines = [
        "| clip | source | duration | timeline pos |",
        "|------|--------|---------:|-------------:|",
    ]
    for idx in sorted(seen):
        meta = seen[idx]
        name = meta.get("source_name", "?")
        dur = meta.get("duration_s", 0.0)
        off = meta.get("timeline_offset_s", 0.0)
        lines.append(f"| {idx} | {name} | {dur:.1f}s | {off:.1f}s |")
    return "\n".join(lines)


@dataclass
class PerClipStatus:
    """Stage summary for the /events SSE + run state."""

    specs_total: int
    cache_hits: int
    cache_misses: int
    words_total: int
    dropped_out_of_range: int
    items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "specs_total": self.specs_total,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "words_total": self.words_total,
            "dropped_out_of_range": self.dropped_out_of_range,
            "items": self.items,
        }
