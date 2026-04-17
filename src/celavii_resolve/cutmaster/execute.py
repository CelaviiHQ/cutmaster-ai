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
from typing import Any

from ..resolve import _boilerplate
from .pipeline import _find_timeline_by_name
from .snapshot import snapshot_project
from .subclips import append_subclips_with_ranges


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


def _map_marker_to_new_timeline(
    resolved: list[dict],
    at_s: float,
) -> float | None:
    """Translate an original-timeline marker time to the NEW timeline time.

    Returns the new-timeline position in seconds, or ``None`` if the marker
    falls between selected segments (i.e. the editor cut that moment out).
    """
    running = 0.0
    for piece in resolved:
        piece_dur = piece["end_s"] - piece["start_s"]
        if piece["start_s"] <= at_s <= piece["end_s"]:
            return running + (at_s - piece["start_s"])
        running += piece_dur
    return None


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
        new_pos_s = _map_marker_to_new_timeline(resolved, at_s)
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

    return {
        "new_timeline_name": new_name,
        "appended": append_result["appended"],
        "append_errors": append_result.get("errors") or [],
        "markers_added": markers_added,
        "markers_skipped": markers_skipped,
        "snapshot_path": snap["path"],
        "snapshot_size_kb": snap["size_kb"],
    }
