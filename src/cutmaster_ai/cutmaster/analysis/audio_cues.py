"""Layer Audio — deterministic DSP cues from the timeline's audio.

Runs post-scrub during analyze (gated on ``layer_audio_enabled``). Purely
deterministic — no LLM calls, no new Python dependencies beyond the
ffmpeg binary the rest of the pipeline already requires.

For every scrubbed word the layer attaches an ``audio_cue`` dict:

    {
      "pause_before_ms":  int   # silence before the word's start
      "pause_after_ms":   int   # silence after the word's end
      "rms_db_delta":     float # this word's mean RMS minus previous word's
      "is_silence_tail":  bool  # silence ≥ 400ms at ≤ -40dB directly after
    }

Derivations:

- **Pauses** — pure arithmetic on STT timestamps. No ffmpeg.
- **Silence regions** — one ffmpeg ``silencedetect`` pass per WAV.
- **RMS envelope** — one ffmpeg ``astats`` pass per WAV at 100ms window.

Laughter / breath detection is explicitly deferred to v4.1 because
spectral features need numpy, which the repo otherwise avoids. The
ffmpeg-only path covers the high-leverage Assembled-mode use case
(filler tightening on natural pause endpoints) without the dep.

Cache: per-source-file under
``~/.cutmaster/cutmaster/audio-cues/v1/<sha1(wav_path)>/cues.json``.
Invalidated when the WAV is regenerated because file-size-and-mtime
join the hash (re-analyze always rebuilds the concat WAV, so the
cache naturally cycles).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cutmaster-ai.cutmaster.audio_cues")


CACHE_ROOT = Path.home() / ".cutmaster" / "cutmaster" / "audio-cues" / "v1"

# Silence detection thresholds. -40 dB matches the proposal's
# is_silence_tail definition; 0.3s catches even short intentional beats
# so "within 100ms after word end, lasting ≥ 400ms" matches accurately.
SILENCE_NOISE_DB = -40.0
SILENCE_MIN_DURATION_S = 0.3

# RMS envelope window — 100ms balances resolution vs. parse cost. At
# 48kHz mono that's 4800 samples per chunk; at 16kHz mono (the STT rate)
# it's 1600 samples. We stay sample-rate-agnostic by using asetnsamples
# computed from the probed rate.
RMS_WINDOW_MS = 100

# Significance thresholds — what earns a row in the Director prompt
# block. Kept conservative so a long transcript still yields a bounded
# block (~30-80 rows for a typical 10-min source).
SIGNIFICANT_PAUSE_MS = 600
SIGNIFICANT_RMS_DELTA_DB = 4.0
# Hard cap on how many rows the Director prompt block can carry.
# Beyond this, the tail is summarised as "... N more cues omitted".
MAX_CUE_ROWS = 120


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_key(wav_path: Path) -> str:
    """sha1(path + size + mtime) so cache invalidates on WAV regeneration.

    The pipeline rebuilds the concat WAV on every analyze, so path alone
    would falsely hit the cache after a re-analyze. Including mtime +
    size keeps the cache useful for clone-runs / dry re-invocations on
    the same WAV.
    """
    try:
        stat = wav_path.stat()
        key_material = f"{wav_path}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        key_material = str(wav_path)
    return hashlib.sha1(key_material.encode("utf-8")).hexdigest()


def _cache_path(wav_path: Path) -> Path:
    return CACHE_ROOT / _cache_key(wav_path) / "cues.json"


def _load_cached(wav_path: Path) -> list[dict] | None:
    path = _cache_path(wav_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        log.warning("audio-cues cache %s unreadable: %s", path, exc)
        return None
    cues = payload.get("cues")
    if not isinstance(cues, list):
        return None
    return cues


def _save_cached(wav_path: Path, cues: list[dict]) -> None:
    path = _cache_path(wav_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps({"wav_path": str(wav_path), "cues": cues}))
    except OSError as exc:
        log.warning("audio-cues cache write failed (%s): %s", path, exc)


# ---------------------------------------------------------------------------
# ffmpeg helpers — silencedetect + astats
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg is not on PATH.")


_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def probe_silences(
    wav_path: Path,
    *,
    noise_db: float = SILENCE_NOISE_DB,
    min_duration_s: float = SILENCE_MIN_DURATION_S,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect once, return ``[(start_s, end_s), ...]``.

    Uses ffmpeg's ``silencedetect`` audio filter, which emits
    ``silence_start`` / ``silence_end`` lines on stderr. Parsing is
    line-based so corrupt / non-matching lines drop silently — we only
    pick up the structured markers.

    Returns an empty list on ffmpeg failure; the caller treats that as
    "no silence signal" and continues with pause-only cues.
    """
    _require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        str(wav_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_duration_s}",
        "-f",
        "null",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        log.warning("silencedetect failed on %s: %s", wav_path.name, r.stderr.strip()[:200])
        return []

    silences: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in r.stderr.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start is not None:
            current_start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end is not None and current_start is not None:
            end_s = float(m_end.group(1))
            silences.append((current_start, end_s))
            current_start = None
    return silences


_METADATA_TIME_RE = re.compile(r"pts_time:\s*([\d.]+)")
_METADATA_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-?inf|nan)")


def probe_rms_envelope(
    wav_path: Path,
    *,
    window_ms: int = RMS_WINDOW_MS,
    sample_rate: int = 16000,
) -> list[tuple[float, float]]:
    """Run one ffmpeg pass, return ``[(time_s, rms_db), ...]`` at ``window_ms``.

    Uses ``asetnsamples`` to chunk audio into fixed-length windows,
    then ``astats`` with ``metadata=1:reset=1`` to emit per-chunk
    Overall.RMS_level, and ``ametadata=mode=print`` to dump those
    metadata lines to stdout.

    ``-inf`` / ``nan`` RMS values (pure silence chunks) are normalised
    to a conservative floor of -100 dB so downstream arithmetic (deltas)
    stays well-defined.
    """
    _require_ffmpeg()
    # Samples per window = sample_rate * window_ms / 1000.
    nsamples = max(1, int(sample_rate * window_ms / 1000))
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        str(wav_path),
        "-af",
        (
            f"aresample={sample_rate},"
            f"asetnsamples=n={nsamples}:p=0,"
            "astats=metadata=1:reset=1,"
            "ametadata=mode=print"
        ),
        "-f",
        "null",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        log.warning("astats pass failed on %s: %s", wav_path.name, r.stderr.strip()[:200])
        return []

    # ametadata=print emits to stdout for stream 0 audio; different
    # ffmpeg builds route it to stdout OR stderr so we read both to
    # stay robust across distributions.
    haystack = (r.stdout or "") + "\n" + (r.stderr or "")
    return _parse_rms_envelope(haystack)


def _parse_rms_envelope(text: str) -> list[tuple[float, float]]:
    """Pair each ``pts_time`` line with the following ``RMS_level`` line.

    ffmpeg's ametadata output alternates one frame stanza per chunk:

        frame:N    pts:N    pts_time:T
        lavfi.astats.Overall.RMS_level=-XX.YY

    A missing RMS line is skipped (don't invent a data point).
    """
    envelope: list[tuple[float, float]] = []
    pending_time: float | None = None
    for line in text.splitlines():
        m_time = _METADATA_TIME_RE.search(line)
        if m_time is not None:
            pending_time = float(m_time.group(1))
            continue
        m_rms = _METADATA_RMS_RE.search(line)
        if m_rms is not None and pending_time is not None:
            raw = m_rms.group(1)
            if raw in ("-inf", "nan"):
                rms = -100.0
            else:
                rms = float(raw)
            envelope.append((pending_time, rms))
            pending_time = None
    return envelope


# ---------------------------------------------------------------------------
# Per-word cue derivation (pure functions — trivially testable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WordWindow:
    start_s: float
    end_s: float


def _word_pauses(words: list[dict]) -> list[tuple[int, int]]:
    """Return ``[(pause_before_ms, pause_after_ms), ...]`` per word."""
    out: list[tuple[int, int]] = []
    for i, w in enumerate(words):
        try:
            start = float(w["start_time"])
            end = float(w["end_time"])
        except (KeyError, TypeError, ValueError):
            out.append((0, 0))
            continue
        before = 0
        after = 0
        if i > 0:
            try:
                prev_end = float(words[i - 1]["end_time"])
                before = max(0, int(round((start - prev_end) * 1000)))
            except (KeyError, TypeError, ValueError):
                before = 0
        if i < len(words) - 1:
            try:
                next_start = float(words[i + 1]["start_time"])
                after = max(0, int(round((next_start - end) * 1000)))
            except (KeyError, TypeError, ValueError):
                after = 0
        out.append((before, after))
    return out


def _silence_tails(
    words: list[dict],
    silences: list[tuple[float, float]],
    *,
    grace_s: float = 0.1,
    min_tail_s: float = 0.4,
) -> list[bool]:
    """Flag words whose end is directly followed by a silence of ≥ ``min_tail_s``.

    A silence region qualifies when its ``start_s`` lies in
    ``[word_end - grace_s, word_end + grace_s]`` AND its duration is at
    least ``min_tail_s``. Silences input must be sorted by start (true
    for the raw ffmpeg output).
    """
    out: list[bool] = []
    # Two-pointer walk to stay O(n+m).
    j = 0
    for w in words:
        try:
            end = float(w["end_time"])
        except (KeyError, TypeError, ValueError):
            out.append(False)
            continue
        tail = False
        # Advance j past silences ending before the word.
        while j < len(silences) and silences[j][1] < end - grace_s:
            j += 1
        # Scan silences whose start is within the grace window.
        k = j
        while k < len(silences) and silences[k][0] <= end + grace_s:
            s_start, s_end = silences[k]
            if s_start >= end - grace_s and (s_end - s_start) >= min_tail_s:
                tail = True
                break
            k += 1
        out.append(tail)
    return out


def _word_rms_means(
    words: list[dict],
    envelope: list[tuple[float, float]],
) -> list[float | None]:
    """Mean RMS-dB over each word's time window; ``None`` when no chunks overlap."""
    if not envelope:
        return [None] * len(words)

    # Two-pointer sweep — envelope is sorted by time.
    means: list[float | None] = []
    i = 0
    for w in words:
        try:
            start = float(w["start_time"])
            end = float(w["end_time"])
        except (KeyError, TypeError, ValueError):
            means.append(None)
            continue
        # Advance i past chunks that end before the word starts.
        while i < len(envelope) and envelope[i][0] < start - 0.05:
            i += 1
        samples: list[float] = []
        k = i
        while k < len(envelope) and envelope[k][0] <= end + 0.05:
            samples.append(envelope[k][1])
            k += 1
        means.append(sum(samples) / len(samples) if samples else None)
    return means


def derive_cues(
    words: list[dict],
    silences: list[tuple[float, float]],
    envelope: list[tuple[float, float]],
) -> list[dict]:
    """Pure: (words, silences, rms_envelope) → list of per-word cue dicts.

    Separated from the ffmpeg-running orchestrator so tests can
    exercise the arithmetic without invoking ffmpeg.
    """
    pauses = _word_pauses(words)
    tails = _silence_tails(words, silences)
    rms_means = _word_rms_means(words, envelope)

    cues: list[dict] = []
    for i, _w in enumerate(words):
        before, after = pauses[i]
        cue: dict = {
            "pause_before_ms": before,
            "pause_after_ms": after,
            "is_silence_tail": tails[i],
        }
        current = rms_means[i]
        prev = rms_means[i - 1] if i > 0 else None
        if current is not None and prev is not None:
            cue["rms_db_delta"] = round(current - prev, 1)
        else:
            cue["rms_db_delta"] = 0.0
        cues.append(cue)
    return cues


# ---------------------------------------------------------------------------
# Orchestrator + transcript attachment
# ---------------------------------------------------------------------------


def compute_audio_cues(
    wav_path: Path,
    words: list[dict],
    *,
    use_cache: bool = True,
) -> list[dict]:
    """Main entry: run ffmpeg twice (silencedetect + astats), derive cues.

    Returns the per-word cue list (parallel to ``words``). Any ffmpeg
    failure falls back to pause-only cues so the stage never blocks
    analyze progression. The cache short-circuits subsequent calls on
    the same WAV unless its size/mtime changed.
    """
    if use_cache:
        cached = _load_cached(wav_path)
        if cached is not None and len(cached) == len(words):
            log.info("audio_cues cache hit (%s)", wav_path.name)
            return cached

    try:
        silences = probe_silences(wav_path)
    except Exception as exc:
        log.warning("silencedetect crashed (%s): %s", wav_path.name, exc)
        silences = []

    try:
        envelope = probe_rms_envelope(wav_path)
    except Exception as exc:
        log.warning("astats crashed (%s): %s", wav_path.name, exc)
        envelope = []

    cues = derive_cues(words, silences, envelope)
    if use_cache and len(cues) == len(words):
        _save_cached(wav_path, cues)
    return cues


def attach_cues_to_transcript(
    transcript: list[dict],
    cues: list[dict],
) -> list[dict]:
    """Annotate each word with ``audio_cue``. Returns new list of dict copies.

    ``cues`` must be parallel to ``transcript``; a length mismatch
    returns the transcript unchanged (log a warning) so Layer Audio
    never corrupts the run record on a bad compute.
    """
    if len(transcript) != len(cues):
        log.warning(
            "audio_cues length mismatch: transcript=%d cues=%d — attaching skipped",
            len(transcript),
            len(cues),
        )
        return list(transcript)

    out: list[dict] = []
    for word, cue in zip(transcript, cues, strict=True):
        new_word = dict(word)
        new_word["audio_cue"] = dict(cue)
        out.append(new_word)
    return out


# ---------------------------------------------------------------------------
# Stats helper for SSE events + plan surface
# ---------------------------------------------------------------------------


def summarise_cues(cues: list[dict]) -> dict:
    """Compact summary for the SSE event payload + run state.

    Keeps counts small + comparable across runs without leaking the
    per-word cue payload into structured logs (the allowlist wouldn't
    pass it anyway).
    """
    total = len(cues)
    pause_hits = sum(
        1
        for c in cues
        if (c.get("pause_before_ms") or 0) >= SIGNIFICANT_PAUSE_MS
        or (c.get("pause_after_ms") or 0) >= SIGNIFICANT_PAUSE_MS
    )
    silence_tail_hits = sum(1 for c in cues if c.get("is_silence_tail"))
    rms_hits = sum(1 for c in cues if abs(c.get("rms_db_delta") or 0.0) >= SIGNIFICANT_RMS_DELTA_DB)
    return {
        "words_total": total,
        "significant_pause_hits": pause_hits,
        "silence_tail_hits": silence_tail_hits,
        "rms_delta_hits": rms_hits,
    }
