"""Execute a build-plan against Resolve — snapshot, build timeline, drop markers.

This is the only mutating step in the pipeline. Before running it the caller
must have a completed ``run["plan"]`` produced by the Director + Marker +
resolve_segments chain (Phase 4).

Safeguards:
  - Pre-flight: verify the source timeline still exists and fps matches the
    timeline that will be created.
  - Snapshot: export the project to a ``.drp`` before any mutation
    (restorable through Resolve's Project Manager if the user hates the cut).
  - New timeline only: never edit the source timeline in place.
  - Unique name: if ``<timeline>_AI_Cut`` already exists, append a suffix.

Reads ``run["plan"]["resolved_segments"]`` as the append order. Maps marker
timestamps from the ORIGINAL timeline's time domain to the NEW timeline's
time domain (since selected segments are concatenated, identical content
lands at different clock times).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..resolve import _boilerplate
from . import captions
from .formats import FormatSpec, get_format
from .pipeline import _find_timeline_by_name
from .snapshot import snapshot_project
from .subclips import append_subclips_with_ranges
from .time_mapping import map_source_to_new_timeline, remap_words_to_new_timeline

log = logging.getLogger("celavii-resolve.cutmaster.execute")


class ExecuteError(RuntimeError):
    """Raised when execute fails pre-flight or mid-build."""


def _unique_timeline_name(project, base: str) -> str:
    """Return ``base`` (or ``base_N``) that doesn't collide with an existing timeline."""
    existing: set[str] = set()
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t:
            existing.add(t.GetName())
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _apply_format(new_tl, spec: FormatSpec) -> dict[str, Any]:
    """Set the cut timeline's resolution to the target format.

    Timelines inherit resolution from the project by default; we flip
    ``useCustomSettings`` first so our width/height take effect without
    touching project-wide settings (which would affect the source
    timeline too). Per spec: Resolve's scripting API surface for these
    keys is empirically validated — we tolerate failures so this phase
    doesn't block on a per-build API quirk.
    """
    result: dict[str, Any] = {
        "format": spec.key,
        "width": spec.width,
        "height": spec.height,
    }
    try:
        new_tl.SetSetting("useCustomSettings", "1")
    except Exception as exc:  # pragma: no cover — Resolve edge case
        log.info("execute: SetSetting useCustomSettings failed (%s)", exc)
    try:
        new_tl.SetSetting("timelineResolutionWidth", str(spec.width))
        new_tl.SetSetting("timelineResolutionHeight", str(spec.height))
    except Exception as exc:  # pragma: no cover — Resolve edge case
        log.warning("execute: could not set timeline resolution (%s)", exc)
        result["resolution_warning"] = str(exc)
    return result


def _write_captions_file(
    kept_words: list[dict],
    snapshot_path: str,
) -> dict[str, Any]:
    """Build caption lines from kept (already-remapped) words and write an SRT."""
    if not kept_words:
        return {"lines": 0, "path": None}
    lines = captions.build_caption_lines(kept_words)
    if not lines:
        return {"lines": 0, "path": None}
    srt_path = Path(snapshot_path).with_suffix(".srt")
    captions.write_srt(lines, srt_path)
    return {"lines": len(lines), "path": str(srt_path)}


def _populate_subtitle_track(media_pool, new_tl, srt_path: str) -> dict[str, Any]:
    """Best-effort: import the SRT and drop it onto a new subtitle track.

    Resolve's scripting surface for subtitle tracks is inconsistent across
    versions (20+ exposes ``ImportSubtitlesFromFile`` on some builds). We
    try the most-likely method and report success/failure; a missing
    subtitle track doesn't fail the whole run — the SRT on disk is the
    authoritative artefact.
    """
    # Try to add a subtitle track so the import has somewhere to land.
    try:
        new_tl.AddTrack("subtitle")
    except Exception as exc:  # pragma: no cover — API quirk
        return {"ok": False, "reason": f"AddTrack('subtitle') raised: {exc}"}

    for method_name in ("ImportSubtitlesFromFile", "ImportSubtitles"):
        method = getattr(new_tl, method_name, None)
        if callable(method):
            try:
                ok = method(srt_path)
                return {"ok": bool(ok), "method": method_name}
            except Exception as exc:  # pragma: no cover — API quirk
                return {"ok": False, "method": method_name, "error": str(exc)}
    return {
        "ok": False,
        "reason": "no ImportSubtitles* method on Timeline — SRT on disk is authoritative",
    }


def _drop_safe_zones(new_tl, spec: FormatSpec) -> dict[str, Any]:
    """Drop platform-UI safe-zone guides. Best-effort, non-fatal.

    Resolve's scripting API for inserting generators at specific positions
    is version-dependent; we attempt ``InsertGeneratorIntoTimeline`` and
    fall back to reporting skipped if the API doesn't cooperate. Full
    implementation slated for the v2-10 manual spike once the correct
    method signatures are confirmed against a live Resolve install.
    """
    zones = spec.safe_zones
    if all(
        pct <= 0
        for pct in (
            zones.top_pct,
            zones.bottom_pct,
            zones.left_pct,
            zones.right_pct,
        )
    ):
        return {"added": 0, "reason": "format declares no safe zones"}
    insert = getattr(new_tl, "InsertGeneratorIntoTimeline", None)
    if not callable(insert):
        return {
            "added": 0,
            "reason": "InsertGeneratorIntoTimeline unavailable — manual spike pending",
        }
    # Placeholder: we know which zones to cover but the generator needs
    # explicit size + position parameters that are not uniformly exposed
    # across Resolve versions. Track as v2-10 follow-up.
    return {
        "added": 0,
        "reason": "safe-zone generator placement requires Resolve-version spike",
    }


def execute_plan(run: dict) -> dict:
    """Build the cut timeline in Resolve. Returns a summary dict.

    Raises :class:`ExecuteError` on any pre-flight or build failure.
    """
    plan = run.get("plan")
    if not plan:
        raise ExecuteError("run has no plan — call /cutmaster/build-plan first")

    resolved: list[dict] = plan.get("resolved_segments") or []
    if not resolved:
        raise ExecuteError("plan has no resolved_segments")

    markers: list[dict] = (plan.get("markers") or {}).get("markers") or []
    user_settings: dict = plan.get("user_settings") or {}

    # v2-10 fields, all optional — fall back to v1 defaults.
    try:
        fmt_spec = get_format(user_settings.get("format") or "horizontal")
    except KeyError:
        fmt_spec = get_format("horizontal")
    captions_enabled = bool(user_settings.get("captions_enabled"))
    safe_zones_enabled = bool(user_settings.get("safe_zones_enabled"))

    resolve, project, media_pool = _boilerplate()

    # 1. Pre-flight
    source_tl = _find_timeline_by_name(project, run["timeline_name"])
    if source_tl is None:
        raise ExecuteError(
            f"Source timeline '{run['timeline_name']}' not found (renamed or deleted since analyze)"
        )

    source_fps = float(source_tl.GetSetting("timelineFrameRate"))

    # 2. Snapshot (before any mutation)
    log.info("execute: snapshotting project before build")
    snap = snapshot_project(
        resolve, project, label=f"pre_cutmaster_{run['run_id']}"
    )

    # 3. Create new timeline
    new_name = _unique_timeline_name(project, f"{run['timeline_name']}_AI_Cut")
    new_tl = media_pool.CreateEmptyTimeline(new_name)
    if not new_tl:
        raise ExecuteError(f"CreateEmptyTimeline('{new_name}') returned None")

    new_fps = float(new_tl.GetSetting("timelineFrameRate"))
    if abs(new_fps - source_fps) > 0.01:
        # Abort + clean up so we don't leave a bad timeline behind
        media_pool.DeleteTimelines([new_tl])
        raise ExecuteError(
            f"New timeline fps {new_fps} does not match source {source_fps}. "
            "Set Project Settings → Timeline frame rate to match and retry."
        )

    # 3a. Format-specific sizing (v2-10). Applied before append so clips
    # land into a frame of the intended aspect.
    format_info = _apply_format(new_tl, fmt_spec)
    project.SetCurrentTimeline(new_tl)

    # 4. Append segments in order — linked audio follows by default
    segments_payload: list[dict] = []
    for piece in resolved:
        segments_payload.append({
            "source_item_id": piece["source_item_id"],
            "start_frame": piece["source_in_frame"],
            "end_frame": piece["source_out_frame"],
            "track_index": 1,
            "media_type": "both",
        })

    append_result = append_subclips_with_ranges(project, media_pool, segments_payload)
    if append_result["appended"] == 0:
        media_pool.DeleteTimelines([new_tl])
        raise ExecuteError(
            f"AppendToTimeline returned 0 items. errors={append_result.get('errors')}"
        )

    # 5. Drop markers mapped to the new timeline's time domain.
    #
    # NOTE: Timeline.AddMarker(frameId, ...) takes a frame RELATIVE to the
    # timeline's start (0-based within the timeline), NOT an absolute
    # timeline frame. GetMarkers() returns the same relative keys. Passing
    # the absolute frame (86400 + offset) parks markers an hour past the
    # end of the timeline where Resolve's UI doesn't render them.
    markers_added = 0
    markers_skipped: list[dict[str, Any]] = []

    for marker in markers:
        at_s = float(marker.get("at_s", 0.0))
        new_pos_s = map_source_to_new_timeline(resolved, at_s)
        if new_pos_s is None:
            markers_skipped.append({
                "name": marker.get("name"),
                "original_at_s": at_s,
                "reason": "falls between selected segments (cut out)",
            })
            continue

        new_frame = round(new_pos_s * new_fps)  # relative, not absolute
        ok = new_tl.AddMarker(
            new_frame,
            marker.get("color") or "Blue",
            marker.get("name") or "",
            marker.get("note") or "",
            int(marker.get("duration_frames") or 1),
        )
        if ok:
            markers_added += 1
        else:
            markers_skipped.append({
                "name": marker.get("name"),
                "original_at_s": at_s,
                "new_frame": new_frame,
                "reason": "AddMarker returned False (duplicate frame?)",
            })

    # 6. Captions (v2-10). Build caption lines from the transcript the
    # Director saw, restricted to words that survived the cut, then write
    # an SRT next to the snapshot and best-effort populate a subtitle
    # track. The basis switch matters in assembled + takes_already_scrubbed
    # mode: the Director picked spans on the raw transcript, so captions
    # must come from raw — otherwise they'd show a polished subset that
    # doesn't match the word-indices the plan referenced.
    caption_info: dict[str, Any] = {"enabled": captions_enabled}
    if captions_enabled:
        transcript_basis = (
            run.get("transcript")
            if user_settings.get("takes_already_scrubbed")
            else run.get("scrubbed")
        ) or []
        kept_words = remap_words_to_new_timeline(transcript_basis, resolved)
        caption_info["basis"] = (
            "raw" if user_settings.get("takes_already_scrubbed") else "scrubbed"
        )
        caption_info.update(_write_captions_file(kept_words, snap["path"]))
        if caption_info.get("path"):
            caption_info["subtitle_track"] = _populate_subtitle_track(
                media_pool, new_tl, caption_info["path"]
            )

    # 7. Safe-zone guides (v2-10). Opt-in; no-ops on horizontal format.
    safe_zone_info: dict[str, Any] = {"enabled": safe_zones_enabled}
    if safe_zones_enabled:
        safe_zone_info.update(_drop_safe_zones(new_tl, fmt_spec))

    return {
        "new_timeline_name": new_name,
        "appended": append_result["appended"],
        "append_errors": append_result.get("errors") or [],
        "markers_added": markers_added,
        "markers_skipped": markers_skipped,
        "snapshot_path": snap["path"],
        "snapshot_size_kb": snap["size_kb"],
        "format": format_info,
        "captions": caption_info,
        "safe_zones": safe_zone_info,
    }
