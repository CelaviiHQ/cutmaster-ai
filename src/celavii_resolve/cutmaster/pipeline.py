"""CutMaster analyze pipeline — VFR check → audio extract → STT → scrub.

Runs as an asyncio background task. Each stage emits an event to the run's
queue (for live SSE) and persists to disk (for restart-tolerant state).

Phase 3 scope: analyze only. Director + Marker agents arrive in Phase 4.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import state
from .scrubber import ScrubParams, scrub

log = logging.getLogger("celavii-resolve.cutmaster.pipeline")


def _find_timeline_by_name(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == name:
            return t
    return None


async def _vfr_check(tl, run, emit) -> bool:
    """Scan all V1 source files for VFR. Returns True on pass."""
    await emit(run, stage="vfr_check", status="started",
               message="Checking source media for variable frame rate")

    from .vfr import detect_vfr  # lazy — avoids ffprobe requirement at import

    items = tl.GetItemListInTrack("video", 1) or []
    seen: set[str] = set()
    problems: list[dict] = []
    for item in items:
        mp_item = item.GetMediaPoolItem()
        if not mp_item:
            continue
        src = mp_item.GetClipProperty("File Path")
        if not src or src in seen:
            continue
        seen.add(src)
        try:
            result = await asyncio.to_thread(detect_vfr, Path(src))
        except Exception as exc:
            log.warning("VFR probe failed for %s: %s", src, exc)
            continue
        if result.get("is_vfr"):
            problems.append(result)

    if problems:
        await emit(run, stage="vfr_check", status="failed",
                   message=f"{len(problems)} VFR file(s) detected — transcode to CFR first",
                   data={"files": problems})
        return False

    await emit(run, stage="vfr_check", status="complete",
               message=f"Checked {len(seen)} unique source file(s), all CFR",
               data={"checked": len(seen)})
    return True


async def _extract_audio(tl, run, emit) -> tuple[Path, float] | None:
    await emit(run, stage="audio_extract", status="started",
               message="Reassembling timeline audio via ffmpeg")
    from .ffmpeg_audio import extract_timeline_audio  # lazy

    wav_path = state.audio_path_for(run["run_id"])
    try:
        result = await asyncio.to_thread(extract_timeline_audio, tl, wav_path)
    except Exception as exc:
        await emit(run, stage="audio_extract", status="failed",
                   message=f"ffmpeg extraction failed: {exc}")
        return None

    await emit(run, stage="audio_extract", status="complete",
               message=f"Wrote {result['duration_s']:.1f}s WAV ({result['segments']} segment(s))",
               data=result)
    return wav_path, float(result["duration_s"])


async def _transcribe_per_clip(tl, run, emit) -> list[dict] | None:
    """v2-6: run STT per timeline audio item, stitch the results.

    Falls back cleanly if any take has no media-pool backing — each skipped
    item surfaces in the event payload so the user can diagnose.
    """
    await emit(run, stage="stt", status="started",
               message="Transcribing per clip (Gemini parallel)")

    from .per_clip_stt import (  # lazy — avoids ffmpeg / Gemini at import
        build_clip_audio_specs,
        extract_per_clip_audio,
        transcribe_per_clip,
    )

    specs = build_clip_audio_specs(tl)
    if not specs:
        await emit(run, stage="stt", status="failed",
                   message="no audio items with source backing for per-clip STT")
        return None

    extract_dir = state.audio_path_for(run["run_id"]).parent / run["run_id"]
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        specs = await asyncio.to_thread(extract_per_clip_audio, specs, extract_dir)
    except Exception as exc:
        await emit(run, stage="stt", status="failed",
                   message=f"per-clip ffmpeg extract failed: {exc}")
        return None

    try:
        stitched, stats = await transcribe_per_clip(specs)
    except Exception as exc:
        await emit(run, stage="stt", status="failed",
                   message=f"per-clip STT failed: {exc}")
        return None

    run["transcript"] = stitched
    state.save(run)

    await emit(
        run, stage="stt", status="complete",
        message=(
            f"Transcribed {len(stitched)} words across {len(specs)} clips "
            f"(cache: {stats['cache_hits']} hits / {stats['cache_misses']} misses)"
        ),
        data={
            "word_count": len(stitched),
            "clips": len(specs),
            "cache_hits": stats["cache_hits"],
            "cache_misses": stats["cache_misses"],
            "dropped_out_of_range": stats["dropped"],
        },
    )
    return stitched


async def _transcribe(
    wav_path: Path,
    audio_duration_s: float,
    run,
    emit,
) -> list[dict] | None:
    await emit(run, stage="stt", status="started",
               message="Transcribing with Gemini (word-level timestamps)")
    from .stt import transcribe_audio  # lazy

    try:
        transcript = await asyncio.to_thread(transcribe_audio, wav_path)
    except Exception as exc:
        await emit(run, stage="stt", status="failed",
                   message=f"STT failed: {exc}")
        return None

    raw_words = [w.model_dump() for w in transcript.words]
    # Guard: Gemini sometimes extrapolates timestamps past the end of the
    # audio. Drop any word whose end_time exceeds the actual WAV duration,
    # plus a 0.25s grace for rounding.
    limit = audio_duration_s + 0.25
    words = [w for w in raw_words if w["end_time"] <= limit]
    dropped = len(raw_words) - len(words)

    run["transcript"] = words
    state.save(run)

    msg = f"Transcribed {len(words)} words"
    if dropped:
        msg += f" (dropped {dropped} with timestamps past audio end of {audio_duration_s:.1f}s)"
    await emit(run, stage="stt", status="complete",
               message=msg,
               data={"word_count": len(words), "dropped_out_of_range": dropped})
    return words


async def _scrub_stage(words: list[dict], params: ScrubParams, run, emit) -> list[dict]:
    await emit(run, stage="scrub", status="started",
               message="Removing fillers, dead air, and restarts")

    result = await asyncio.to_thread(scrub, words, params)
    run["scrubbed"] = result.kept
    state.save(run)

    await emit(run, stage="scrub", status="complete",
               message=(
                   f"Kept {result.kept_count}/{result.original_count} words "
                   f"(removed {result.counts['filler']} filler, "
                   f"{result.counts['restart']} restart)"
               ),
               data=result.model_dump(exclude={"kept", "removed"}))
    return result.kept


async def run_analyze(
    run_id: str,
    timeline_name: str,
    preset: str = "auto",
    scrub_params: ScrubParams | None = None,
    per_clip_stt: bool = False,
) -> None:
    """Top-level analyze orchestrator.

    Loads state by run_id, runs stages in sequence, emits events. Exceptions
    are caught and converted to a final ``error`` event so the SSE stream
    always terminates cleanly.
    """
    run = state.load(run_id)
    if run is None:
        log.error("run_analyze: run_id %s not found", run_id)
        return
    run["status"] = "running"
    state.save(run)

    try:
        # Lazy import Resolve bridge — avoids import-time Resolve dependency for tests
        from ..resolve import _boilerplate  # noqa: PLC0415

        _, project, _ = _boilerplate()
        tl = _find_timeline_by_name(project, timeline_name)
        if tl is None:
            await state.emit(run, stage="error", status="failed",
                             message=f"Timeline '{timeline_name}' not found in project")
            run["status"] = "failed"
            run["error"] = f"timeline '{timeline_name}' not found"
            state.save(run)
            return

        if not await _vfr_check(tl, run, state.emit):
            run["status"] = "failed"
            run["error"] = "vfr_detected"
            state.save(run)
            await state.emit(run, stage="done", status="failed", message="halted on VFR")
            return

        if per_clip_stt:
            # v2-6: skip the global concat, run STT per timeline item, and
            # attach clip_index + clip_metadata to every word.
            words = await _transcribe_per_clip(tl, run, state.emit)
            if words is None:
                run["status"] = "failed"
                run["error"] = "per_clip_stt_failed"
                state.save(run)
                await state.emit(run, stage="done", status="failed",
                                 message="halted on per-clip STT")
                return
        else:
            audio_result = await _extract_audio(tl, run, state.emit)
            if audio_result is None:
                run["status"] = "failed"
                run["error"] = "audio_extract_failed"
                state.save(run)
                await state.emit(run, stage="done", status="failed", message="halted on audio extract")
                return
            wav_path, audio_duration_s = audio_result

            words = await _transcribe(wav_path, audio_duration_s, run, state.emit)
            if words is None:
                run["status"] = "failed"
                run["error"] = "stt_failed"
                state.save(run)
                await state.emit(run, stage="done", status="failed", message="halted on STT")
                return

        await _scrub_stage(words, scrub_params or ScrubParams(), run, state.emit)

        run["status"] = "done"
        state.save(run)
        await state.emit(run, stage="done", status="complete",
                         message="Analyze complete — ready for configure step")

    except Exception as exc:
        log.exception("Pipeline crashed")
        run["status"] = "failed"
        run["error"] = str(exc)
        state.save(run)
        await state.emit(run, stage="error", status="failed", message=str(exc))
        await state.emit(run, stage="done", status="failed", message="crashed")
