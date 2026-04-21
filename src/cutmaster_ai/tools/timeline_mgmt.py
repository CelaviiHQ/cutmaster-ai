"""Timeline management tools — CRUD, tracks, import/export, duplication."""

import json

from ..config import mcp
from ..constants import EXPORT_TYPES, TRACK_TYPES
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _find_clip_by_name

# ---------------------------------------------------------------------------
# Timeline CRUD
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_timelines() -> str:
    """List all timelines in the current project."""
    _, project, _ = _boilerplate()
    count = project.GetTimelineCount() or 0
    if count == 0:
        return "No timelines in the current project."
    timelines = []
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            timelines.append({"index": i, "name": tl.GetName()})
    return json.dumps({"timelines": timelines, "count": count}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_get_current_timeline() -> str:
    """Get info about the currently active timeline."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No timeline is currently active."
    info = {
        "name": tl.GetName(),
        "unique_id": tl.GetUniqueId(),
        "start_frame": tl.GetStartFrame(),
        "end_frame": tl.GetEndFrame(),
    }
    try:
        info["start_timecode"] = tl.GetStartTimecode()
    except (AttributeError, TypeError):
        pass
    for tt in ("video", "audio", "subtitle"):
        try:
            info[f"{tt}_tracks"] = tl.GetTrackCount(tt) or 0
        except (AttributeError, TypeError):
            pass
    return json.dumps(info, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_create_timeline(name: str) -> str:
    """Create a new empty timeline.

    Args:
        name: Name for the new timeline.
    """
    _, _, mp = _boilerplate()
    tl = mp.CreateEmptyTimeline(name)
    return f"Timeline '{name}' created." if tl else f"Failed to create timeline '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_create_timeline_from_clips(name: str, clip_names: list[str]) -> str:
    """Create a timeline populated with the specified clips.

    Args:
        name: Timeline name.
        clip_names: List of clip names from the media pool.
    """
    _, _, mp = _boilerplate()
    clips = [c for n in clip_names if (c := _find_clip_by_name(mp, n))]
    if not clips:
        return "No matching clips found in the media pool."
    tl = mp.CreateTimelineFromClips(name, clips)
    return (
        f"Timeline '{name}' created with {len(clips)} clip(s)."
        if tl
        else "Failed to create timeline."
    )


@mcp.tool
@safe_resolve_call
def cutmaster_set_current_timeline(name: str) -> str:
    """Switch to a timeline by name.

    Args:
        name: Timeline name.
    """
    _, project, _ = _boilerplate()
    count = project.GetTimelineCount() or 0
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            result = project.SetCurrentTimeline(tl)
            return f"Switched to timeline '{name}'." if result else "Failed to switch."
    return f"Timeline '{name}' not found."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_timelines(names: list[str]) -> str:
    """Delete timelines by name.

    Args:
        names: List of timeline names to delete.
    """
    _, project, mp = _boilerplate()
    count = project.GetTimelineCount() or 0
    timelines = []
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() in names:
            timelines.append(tl)
    if not timelines:
        return "No matching timelines found."
    result = mp.DeleteTimelines(timelines)
    return f"Deleted {len(timelines)} timeline(s)." if result else "Failed to delete timelines."


@mcp.tool
@safe_resolve_call
def cutmaster_duplicate_timeline(new_name: str = "") -> str:
    """Duplicate the current timeline.

    Args:
        new_name: Name for the duplicate. Uses auto-name if empty.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline to duplicate."
    dup = tl.DuplicateTimeline(new_name) if new_name else tl.DuplicateTimeline()
    return f"Timeline duplicated as '{dup.GetName()}'." if dup else "Failed to duplicate timeline."


@mcp.tool
@safe_resolve_call
def cutmaster_set_timeline_name(name: str) -> str:
    """Rename the current timeline.

    Args:
        name: New name for the timeline.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.SetName(name)
    return f"Timeline renamed to '{name}'." if result else "Failed to rename timeline."


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_get_track_count(track_type: str = "video") -> str:
    """Get the number of tracks of a given type.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
    """
    if track_type not in TRACK_TYPES:
        return f"Invalid track type. Valid: {', '.join(sorted(TRACK_TYPES))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    count = tl.GetTrackCount(track_type) or 0
    return f"{count} {track_type} track(s)."


@mcp.tool
@safe_resolve_call
def cutmaster_add_track(track_type: str, sub_type: str = "") -> str:
    """Add a track to the current timeline.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        sub_type: Optional sub-type (e.g. 'mono', 'stereo', '5.1' for audio).
    """
    if track_type not in TRACK_TYPES:
        return f"Invalid track type. Valid: {', '.join(sorted(TRACK_TYPES))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.AddTrack(track_type, sub_type) if sub_type else tl.AddTrack(track_type)
    return f"Added {track_type} track." if result else "Failed to add track."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_track(track_type: str, track_index: int) -> str:
    """Delete a track from the current timeline.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
    """
    if track_type not in TRACK_TYPES:
        return f"Invalid track type. Valid: {', '.join(sorted(TRACK_TYPES))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.DeleteTrack(track_type, track_index)
    return f"Deleted {track_type} track {track_index}." if result else "Failed to delete track."


@mcp.tool
@safe_resolve_call
def cutmaster_get_track_name(track_type: str, track_index: int) -> str:
    """Get the name of a track.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    name = tl.GetTrackName(track_type, track_index)
    return f"{track_type} track {track_index}: '{name}'"


@mcp.tool
@safe_resolve_call
def cutmaster_set_track_name(track_type: str, track_index: int, name: str) -> str:
    """Rename a track.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        name: New track name.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.SetTrackName(track_type, track_index, name)
    return f"Track renamed to '{name}'." if result else "Failed to rename track."


@mcp.tool
@safe_resolve_call
def cutmaster_set_track_enabled(track_type: str, track_index: int, enabled: bool) -> str:
    """Enable or disable a track.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        enabled: True to enable, False to disable.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.SetTrackEnable(track_type, track_index, enabled)
    state = "enabled" if enabled else "disabled"
    return f"Track {track_type} {track_index} {state}." if result else f"Failed to {state} track."


@mcp.tool
@safe_resolve_call
def cutmaster_set_track_lock(track_type: str, track_index: int, locked: bool) -> str:
    """Lock or unlock a track.

    Args:
        track_type: 'video', 'audio', or 'subtitle'.
        track_index: 1-based track index.
        locked: True to lock, False to unlock.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.SetTrackLock(track_type, track_index, locked)
    state = "locked" if locked else "unlocked"
    return f"Track {track_type} {track_index} {state}." if result else f"Failed to {state} track."


# ---------------------------------------------------------------------------
# Timeline import / export
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_export_timeline(
    path: str,
    export_type: str = "FCPXML",
    export_subtype: str = "",
) -> str:
    """Export the current timeline to a file.

    Args:
        path: Output file path.
        export_type: Format — AAF, DRT, EDL, FCP7XML, FCPXML, CSV, TAB, OTIO.
        export_subtype: Optional subtype (e.g. FCPXML version).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    type_val = EXPORT_TYPES.get(export_type.upper())
    if type_val is None:
        return f"Invalid export type '{export_type}'. Valid: {', '.join(EXPORT_TYPES.keys())}"
    if export_subtype:
        result = tl.Export(path, type_val, export_subtype)
    else:
        result = tl.Export(path, type_val)
    return f"Timeline exported to {path} as {export_type}." if result else "Failed to export."


@mcp.tool
@safe_resolve_call
def cutmaster_import_timeline(path: str) -> str:
    """Import a timeline from a file (AAF, EDL, XML, FCPXML, OTIO).

    Args:
        path: Path to the timeline file.
    """
    _, _, mp = _boilerplate()
    timelines = mp.ImportTimelineFromFile(path)
    if timelines:
        names = [tl.GetName() for tl in timelines if tl]
        return f"Imported {len(timelines)} timeline(s): {', '.join(names)}"
    return f"Failed to import timeline from {path}."


@mcp.tool
@safe_resolve_call
def cutmaster_append_clips_to_timeline(clip_names: list[str]) -> str:
    """Append clips from the media pool to the end of the current timeline.

    Args:
        clip_names: List of clip names to append.
    """
    _, _, mp = _boilerplate()
    clips = [c for n in clip_names if (c := _find_clip_by_name(mp, n))]
    if not clips:
        return "No matching clips found."
    items = mp.AppendToTimeline(clips)
    if items:
        return f"Appended {len(items)} clip(s) to the timeline."
    return "Failed to append clips."
