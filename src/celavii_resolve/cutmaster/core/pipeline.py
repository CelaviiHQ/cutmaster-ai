"""CutMaster analyze pipeline — VFR check → audio extract → STT → scrub.

Runs as an asyncio background task. Each stage emits an event to the run's
queue (for live SSE) and persists to disk (for restart-tolerant state).

Phase 3 scope: analyze only. Director + Marker agents arrive in Phase 4.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..analysis.scrubber import ScrubParams, scrub
from . import state
from .timeouts import MARKER_TIMEOUT_S, STT_TIMEOUT_S, with_timeout

log = logging.getLogger("celavii-resolve.cutmaster.pipeline")


def _find_timeline_by_name(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == name:
            return t
    return None


async def _vfr_check(tl, run, emit) -> bool:
    """Scan all V1 source files for VFR. Returns True on pass."""
    await emit(
        run,
        stage="vfr_check",
        status="started",
        message="Checking source media for variable frame rate",
    )

    from ..media.vfr import detect_vfr  # lazy — avoids ffprobe requirement at import

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
        await emit(
            run,
            stage="vfr_check",
            status="failed",
            message=f"{len(problems)} VFR file(s) detected — transcode to CFR first",
            data={"files": problems},
        )
        return False

    await emit(
        run,
        stage="vfr_check",
        status="complete",
        message=f"Checked {len(seen)} unique source file(s), all CFR",
        data={"checked": len(seen)},
    )
    return True


async def _extract_audio(tl, run, emit) -> tuple[Path, float] | None:
    await emit(
        run,
        stage="audio_extract",
        status="started",
        message="Reassembling timeline audio via ffmpeg",
    )
    from ..media.ffmpeg_audio import extract_timeline_audio  # lazy

    wav_path = state.audio_path_for(run["run_id"])
    try:
        result = await asyncio.to_thread(extract_timeline_audio, tl, wav_path)
    except Exception as exc:
        await emit(
            run, stage="audio_extract", status="failed", message=f"ffmpeg extraction failed: {exc}"
        )
        return None

    await emit(
        run,
        stage="audio_extract",
        status="complete",
        message=f"Wrote {result['duration_s']:.1f}s WAV ({result['segments']} segment(s))",
        data=result,
    )
    return wav_path, float(result["duration_s"])


async def _transcribe_per_clip(
    tl,
    run,
    emit,
    stt_provider: str | None = None,
) -> list[dict] | None:
    """v2-6: run STT per timeline audio item, stitch the results.

    Falls back cleanly if any take has no media-pool backing — each skipped
    item surfaces in the event payload so the user can diagnose.
    """
    from ..stt.per_clip import (  # lazy — avoids ffmpeg / Gemini at import
        build_clip_audio_specs,
        extract_per_clip_audio,
        transcribe_per_clip,
    )

    # Per-clip mode still does audio extraction — emit the stage event so the
    # Analyze UI shows a green check instead of a perpetual pending spinner.
    await emit(
        run,
        stage="audio_extract",
        status="started",
        message="Extracting audio per timeline item (ffmpeg)",
    )

    specs = build_clip_audio_specs(tl)
    if not specs:
        await emit(
            run,
            stage="audio_extract",
            status="failed",
            message="no audio items with source backing for per-clip STT",
        )
        return None

    extract_dir = state.audio_path_for(run["run_id"]).parent / run["run_id"]
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        specs = await asyncio.to_thread(extract_per_clip_audio, specs, extract_dir)
    except Exception as exc:
        await emit(
            run,
            stage="audio_extract",
            status="failed",
            message=f"per-clip ffmpeg extract failed: {exc}",
        )
        return None

    total_duration = sum(s.duration_s for s in specs)
    await emit(
        run,
        stage="audio_extract",
        status="complete",
        message=(f"Extracted {len(specs)} per-clip WAV(s) — {total_duration:.1f}s total"),
        data={"clips": len(specs), "duration_s": total_duration, "mode": "per_clip"},
    )

    # Resolve the effective provider up front so cache isolation + the
    # event label both reflect what we're actually about to run.
    from ..stt import DEFAULT_PROVIDER

    effective_provider = (stt_provider or DEFAULT_PROVIDER).lower()
    await emit(
        run,
        stage="stt",
        status="started",
        message=f"Transcribing per clip in parallel ({effective_provider})",
        data={"provider": effective_provider},
    )

    try:
        stitched, stats = await with_timeout(
            transcribe_per_clip(
                specs,
                provider=effective_provider,
            ),
            STT_TIMEOUT_S,
            f"per-clip STT ({effective_provider})",
        )
    except Exception as exc:
        await emit(run, stage="stt", status="failed", message=f"per-clip STT failed: {exc}")
        return None

    run["transcript"] = stitched
    state.save(run)

    await emit(
        run,
        stage="stt",
        status="complete",
        message=(
            f"Transcribed {len(stitched)} words across {len(specs)} clips "
            f"via {effective_provider} "
            f"(cache: {stats['cache_hits']} hits / {stats['cache_misses']} misses)"
        ),
        data={
            "word_count": len(stitched),
            "clips": len(specs),
            "cache_hits": stats["cache_hits"],
            "cache_misses": stats["cache_misses"],
            "dropped_out_of_range": stats["dropped"],
            "provider": effective_provider,
        },
    )
    return stitched


async def _reconcile_speakers(
    transcript: list[dict],
    expected_speakers: int,
    run,
    emit,
) -> list[dict]:
    """Apply the user's speaker-count hint to the transcript.

    Two transcript flavours show up here:

    - **Per-clip STT** (``v2-6``) — words carry ``clip_index`` so each
      clip's speaker IDs are local and need cross-clip reconciliation.
    - **Concat STT** (v1 default) — one big WAV → Gemini already assigns
      global IDs. Reconciliation across clips is unnecessary; only the
      solo-collapse hint is worth acting on.

    Behaviour:

    - ``expected_speakers == 1`` → always collapse every ``speaker_id`` to
      ``"S1"`` regardless of transcript flavour (useful on vlog-to-camera
      shoots where Gemini occasionally invents a second speaker).
    - ``expected_speakers >= 2`` + per-clip transcript → one cheap
      Flash-Lite call that remaps clip-local IDs onto a global roster.
    - ``expected_speakers >= 2`` + concat transcript → no-op; Gemini's
      global IDs already satisfy the count. Emit a "kept as-is" event.
    - anything else → no-op (caller shouldn't have invoked us).

    Errors are surfaced as stage events but never halt the pipeline —
    a failed reconciliation leaves the original transcript in place so
    downstream stages keep working.
    """
    from ..stt.reconcile import (  # lazy — avoids Gemini client at import
        collapse_to_solo,
        reconcile_with_llm,
    )

    if expected_speakers == 1:
        await emit(
            run, stage="speakers", status="started", message="Collapsing to single-speaker mode"
        )
        new_transcript = collapse_to_solo(transcript)
        run["transcript"] = new_transcript
        run["speaker_reconciliation"] = {
            "expected_speakers": 1,
            "detected_speakers": 1,
            "strategy": "collapse",
            "roster": ["S1"],
        }
        state.save(run)
        await emit(
            run,
            stage="speakers",
            status="complete",
            message="Collapsed to a single speaker (S1)",
            data={"detected": 1, "roster": ["S1"]},
        )
        return new_transcript

    if expected_speakers < 2:
        return transcript

    has_clip_index = any("clip_index" in w for w in transcript)
    if not has_clip_index:
        # Concat STT — Gemini already produced cross-clip-consistent IDs.
        # Record the user's hint in state but skip the LLM reconciler
        # (nothing to reconcile).
        run["speaker_reconciliation"] = {
            "expected_speakers": expected_speakers,
            "strategy": "skip_concat",
            "reasoning": (
                "Concat STT already produced global speaker IDs; "
                "cross-clip reconciliation not needed."
            ),
        }
        state.save(run)
        await emit(
            run,
            stage="speakers",
            status="complete",
            message=(
                f"Concat STT: keeping Gemini's global IDs for up to {expected_speakers} speaker(s)"
            ),
            data={"strategy": "skip_concat"},
        )
        return transcript

    await emit(
        run,
        stage="speakers",
        status="started",
        message=(f"Reconciling cross-clip speaker IDs (target: {expected_speakers})"),
    )
    try:
        new_transcript, summary = await with_timeout(
            asyncio.to_thread(
                reconcile_with_llm,
                transcript,
                expected_speakers,
            ),
            MARKER_TIMEOUT_S,
            "speaker reconciliation",
        )
    except Exception as exc:
        log.exception("Speaker reconciliation failed")
        await emit(
            run,
            stage="speakers",
            status="failed",
            message=(f"reconciliation failed — keeping raw per-clip IDs: {exc}"),
        )
        return transcript

    run["transcript"] = new_transcript
    run["speaker_reconciliation"] = {
        "expected_speakers": expected_speakers,
        "strategy": "llm",
        **summary,
    }
    state.save(run)
    await emit(
        run,
        stage="speakers",
        status="complete",
        message=(
            f"Reconciled to {summary['detected_speakers']} speaker(s): "
            f"{', '.join(summary['roster'])}"
        ),
        data={
            "detected": summary["detected_speakers"],
            "roster": summary["roster"],
        },
    )
    return new_transcript


async def _transcribe(
    wav_path: Path,
    audio_duration_s: float,
    run,
    emit,
    stt_provider: str | None = None,
) -> list[dict] | None:
    from ..stt import DEFAULT_PROVIDER, transcribe_audio  # lazy

    effective_provider = (stt_provider or DEFAULT_PROVIDER).lower()
    await emit(
        run,
        stage="stt",
        status="started",
        message=f"Transcribing with word-level timestamps ({effective_provider})",
        data={"provider": effective_provider},
    )

    try:
        transcript = await with_timeout(
            asyncio.to_thread(
                transcribe_audio,
                wav_path,
                None,
                effective_provider,
            ),
            STT_TIMEOUT_S,
            f"STT ({effective_provider})",
        )
    except Exception as exc:
        await emit(run, stage="stt", status="failed", message=f"STT failed: {exc}")
        return None

    raw_words = [w.model_dump() for w in transcript.words]
    # Guard: LLM STT occasionally extrapolates timestamps past the end of
    # the audio. Drop any word whose end_time exceeds the actual WAV
    # duration, plus a 0.25s grace for rounding. (Not hit by Deepgram in
    # practice but the clamp is cheap and provider-agnostic.)
    limit = audio_duration_s + 0.25
    words = [w for w in raw_words if w["end_time"] <= limit]
    dropped = len(raw_words) - len(words)

    run["transcript"] = words
    state.save(run)

    msg = f"Transcribed {len(words)} words via {effective_provider}"
    if dropped:
        msg += f" (dropped {dropped} with timestamps past audio end of {audio_duration_s:.1f}s)"
    await emit(
        run,
        stage="stt",
        status="complete",
        message=msg,
        data={
            "word_count": len(words),
            "dropped_out_of_range": dropped,
            "provider": effective_provider,
        },
    )
    return words


async def _scrub_stage(words: list[dict], params: ScrubParams, run, emit) -> list[dict]:
    await emit(
        run, stage="scrub", status="started", message="Removing fillers, dead air, and restarts"
    )

    result = await asyncio.to_thread(scrub, words, params)
    run["scrubbed"] = result.kept
    state.save(run)

    await emit(
        run,
        stage="scrub",
        status="complete",
        message=(
            f"Kept {result.kept_count}/{result.original_count} words "
            f"(removed {result.counts['filler']} filler, "
            f"{result.counts['restart']} restart)"
        ),
        data=result.model_dump(exclude={"kept", "removed"}),
    )
    return result.kept


async def _shot_tag_stage(tl, scrubbed_words: list[dict], run, emit) -> list[dict]:
    """v4 Layer C — tag each timeline video item with Gemini vision.

    Annotates every scrubbed word with a ``shot_tag`` dict and persists
    both the per-item ``tagged_frames`` list (for Review-screen display
    in a future phase) and the annotated transcript + scrubbed arrays.

    Best-effort: if frame extraction / the vision call fails entirely,
    the stage emits ``failed`` but returns the unmodified scrubbed words
    so downstream stages (Director in 4.1+) keep working. No cliff on
    missing GEMINI_API_KEY — ``call_structured`` raises and we catch.
    """
    from ..analysis.shot_tagger import (
        TaggedFrame,
        attach_tags_to_transcript,
        build_video_item_specs,
        tag_video_items,
    )

    await emit(
        run,
        stage="shot_tag",
        status="started",
        message="Tagging shots via Gemini vision",
    )

    try:
        specs = await asyncio.to_thread(build_video_item_specs, tl)
    except Exception as exc:
        log.warning("shot_tag build_video_item_specs failed: %s", exc)
        await emit(
            run,
            stage="shot_tag",
            status="failed",
            message=f"could not enumerate video items: {exc}",
        )
        return scrubbed_words

    if not specs:
        await emit(
            run,
            stage="shot_tag",
            status="complete",
            message="no taggable video items on V1 — skipping",
            data={"items_total": 0},
        )
        return scrubbed_words

    async def _on_item(idx: int, spec, tagged: list[TaggedFrame]) -> None:
        # Intermediate progress event — lets the panel tick per item
        # instead of waiting for the whole batch to finish.
        await emit(
            run,
            stage="shot_tag",
            status="progress",
            message=f"tagged item {idx + 1}/{len(specs)} ({spec.source_name})",
            data={
                "item_index": spec.item_index,
                "frames": len(tagged),
                "items_done": idx + 1,
                "items_total": len(specs),
            },
        )

    try:
        tagged_all, stats = await tag_video_items(specs, on_item_done=_on_item)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("shot_tag stage crashed")
        await emit(
            run,
            stage="shot_tag",
            status="failed",
            message=f"shot tagging failed: {exc}",
        )
        return scrubbed_words

    # Persist raw tagged frames — small, useful for Review + debugging.
    run["shot_tags"] = [
        {
            "item_index": t.item_index,
            "source_path": t.source_path,
            "source_ts_s": round(t.source_ts_s, 3),
            "timeline_ts_s": round(t.timeline_ts_s, 3),
            **t.tag.model_dump(),
        }
        for t in tagged_all
    ]

    # Attach tags to BOTH the raw transcript and the scrubbed copy so the
    # Director prompt (4.1) and any downstream consumer sees them.
    if run.get("transcript"):
        run["transcript"] = attach_tags_to_transcript(run["transcript"], tagged_all)
    annotated_scrubbed = attach_tags_to_transcript(scrubbed_words, tagged_all)
    run["scrubbed"] = annotated_scrubbed
    state.save(run)

    await emit(
        run,
        stage="shot_tag",
        status="complete",
        message=(
            f"Tagged {stats['frames_total']} frame(s) across "
            f"{stats['items_tagged']}/{stats['items_total']} item(s) "
            f"(cache: {stats['frames_cache_hits']} hit(s))"
        ),
        data=stats,
    )
    return annotated_scrubbed


async def run_analyze(
    run_id: str,
    timeline_name: str,
    preset: str = "auto",
    scrub_params: ScrubParams | None = None,
    per_clip_stt: bool = False,
    expected_speakers: int | None = None,
    stt_provider: str | None = None,
    layer_c_enabled: bool = False,
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
        from ...resolve import _boilerplate  # noqa: PLC0415

        _, project, _ = _boilerplate()
        tl = _find_timeline_by_name(project, timeline_name)
        if tl is None:
            await state.emit(
                run,
                stage="error",
                status="failed",
                message=f"Timeline '{timeline_name}' not found in project",
            )
            run["status"] = "failed"
            run["error"] = f"timeline '{timeline_name}' not found"
            state.save(run)
            return

        state.raise_if_cancelled(run_id)

        if not await _vfr_check(tl, run, state.emit):
            run["status"] = "failed"
            run["error"] = "vfr_detected"
            state.save(run)
            await state.emit(run, stage="done", status="failed", message="halted on VFR")
            return

        state.raise_if_cancelled(run_id)

        if per_clip_stt:
            # v2-6: skip the global concat, run STT per timeline item, and
            # attach clip_index + clip_metadata to every word.
            words = await _transcribe_per_clip(
                tl,
                run,
                state.emit,
                stt_provider=stt_provider,
            )
            if words is None:
                run["status"] = "failed"
                run["error"] = "per_clip_stt_failed"
                state.save(run)
                await state.emit(
                    run, stage="done", status="failed", message="halted on per-clip STT"
                )
                return
            state.raise_if_cancelled(run_id)
            # Cross-clip speaker reconciliation — only runs when the user
            # supplied a count, so v2-6 legacy behaviour (raw per-clip IDs)
            # stays the default.
            if expected_speakers:
                words = await _reconcile_speakers(
                    words,
                    expected_speakers,
                    run,
                    state.emit,
                )
        else:
            audio_result = await _extract_audio(tl, run, state.emit)
            if audio_result is None:
                run["status"] = "failed"
                run["error"] = "audio_extract_failed"
                state.save(run)
                await state.emit(
                    run, stage="done", status="failed", message="halted on audio extract"
                )
                return
            wav_path, audio_duration_s = audio_result

            state.raise_if_cancelled(run_id)

            words = await _transcribe(
                wav_path,
                audio_duration_s,
                run,
                state.emit,
                stt_provider=stt_provider,
            )
            if words is None:
                run["status"] = "failed"
                run["error"] = "stt_failed"
                state.save(run)
                await state.emit(run, stage="done", status="failed", message="halted on STT")
                return
            # Solo-speaker collapse works for concat STT too: Gemini
            # sometimes invents S2/S3 on single-speaker content. Multi-
            # speaker hints are a no-op on concat STT (global IDs already).
            if expected_speakers:
                words = await _reconcile_speakers(
                    words,
                    expected_speakers,
                    run,
                    state.emit,
                )

        state.raise_if_cancelled(run_id)

        scrubbed = await _scrub_stage(words, scrub_params or ScrubParams(), run, state.emit)

        if layer_c_enabled:
            state.raise_if_cancelled(run_id)
            await _shot_tag_stage(tl, scrubbed, run, state.emit)

        run["status"] = "done"
        state.save(run)
        await state.emit(
            run,
            stage="done",
            status="complete",
            message="Analyze complete — ready for configure step",
        )

    except asyncio.CancelledError:
        # User cancelled via /cancel. The endpoint already pushed a
        # `cancelled` event and wrote status=cancelled. Emit one more
        # terminal event here so SSE clients that resume mid-run get a
        # clean stage=cancelled terminator even if the endpoint's
        # queue.put raced with their subscribe. emit() is idempotent
        # enough — it appends to events and persists under the lock.
        log.info("Pipeline cancelled for run %s", run_id)
        try:
            await state.emit(
                run,
                stage="cancelled",
                status="cancelled",
                message="pipeline exited on cancel",
            )
        except Exception:  # pragma: no cover — best-effort
            log.exception("Failed to emit terminal cancelled event for %s", run_id)
        raise
    except Exception as exc:
        log.exception("Pipeline crashed")
        run["status"] = "failed"
        run["error"] = str(exc)
        state.save(run)
        await state.emit(run, stage="error", status="failed", message=str(exc))
        await state.emit(run, stage="done", status="failed", message="crashed")
