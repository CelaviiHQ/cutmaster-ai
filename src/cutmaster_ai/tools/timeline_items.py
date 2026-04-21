"""Timeline item tools — versions, takes, flags, color groups, playhead."""

import json

from ..config import mcp
from ..constants import CLIP_COLORS, VERSION_TYPES
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser
from .timeline_edit import _get_timeline_item

# ---------------------------------------------------------------------------
# Versions (color grade versions)
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_versions(
    version_type: str = "local",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """List all grade versions on a timeline item.

    Args:
        version_type: 'local' or 'remote'.
        track_type: Track type.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    if version_type not in VERSION_TYPES:
        return f"Invalid version type. Valid: {', '.join(sorted(VERSION_TYPES))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    versions = item.GetVersionNameList(version_type) or []
    current = item.GetCurrentVersion()
    return json.dumps(
        {
            "versions": versions,
            "current": _ser(current),
            "type": version_type,
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def cutmaster_add_version(
    name: str,
    version_type: str = "local",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Add a new grade version to a timeline item.

    Args:
        name: Version name.
        version_type: 'local' or 'remote'.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.AddVersion(name, version_type)
    return f"Version '{name}' added." if result else "Failed to add version."


@mcp.tool
@safe_resolve_call
def cutmaster_load_version(
    name: str,
    version_type: str = "local",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Switch to a specific grade version.

    Args:
        name: Version name to load.
        version_type: 'local' or 'remote'.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.LoadVersionByName(name, version_type)
    return f"Loaded version '{name}'." if result else f"Failed to load version '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_version(
    name: str,
    version_type: str = "local",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Delete a grade version.

    Args:
        name: Version name to delete.
        version_type: 'local' or 'remote'.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.DeleteVersionByName(name, version_type)
    return f"Deleted version '{name}'." if result else f"Failed to delete version '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_rename_version(
    old_name: str,
    new_name: str,
    version_type: str = "local",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Rename a grade version.

    Args:
        old_name: Current version name.
        new_name: New version name.
        version_type: 'local' or 'remote'.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.RenameVersionByName(old_name, new_name, version_type)
    return f"Renamed '{old_name}' to '{new_name}'." if result else "Failed to rename version."


# ---------------------------------------------------------------------------
# Takes
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_takes(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """List all takes on a timeline item."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    count = item.GetTakesCount() or 0
    selected = item.GetSelectedTakeIndex()
    takes = []
    for i in range(1, count + 1):  # 1-based
        take = item.GetTakeByIndex(i)
        takes.append(_ser(take) if take else {"index": i})
    return json.dumps({"takes": takes, "selected": selected, "count": count}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_select_take(
    take_index: int,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Select a specific take on a timeline item.

    Args:
        take_index: 1-based take index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.SelectTakeByIndex(take_index)
    return f"Selected take {take_index}." if result else f"Failed to select take {take_index}."


@mcp.tool
@safe_resolve_call
def cutmaster_finalize_take(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Finalize the current take (remove other takes, keep selected)."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.FinalizeTake()
    return "Take finalized." if result else "Failed to finalize take."


# ---------------------------------------------------------------------------
# Flags & colors
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_add_item_flag(
    color: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Add a flag to a timeline item.

    Args:
        color: Flag color (same as clip colors).
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.AddFlag(color)
    return f"Added {color} flag." if result else "Failed to add flag."


@mcp.tool
@safe_resolve_call
def cutmaster_get_item_flags(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get all flags on a timeline item."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    flags = item.GetFlagList() or []
    return json.dumps({"flags": flags}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_clear_item_flags(
    color: str = "",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Clear flags from a timeline item.

    Args:
        color: Flag color to clear, or empty to clear all.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.ClearFlags(color)
    return "Flags cleared." if result else "Failed to clear flags."


@mcp.tool
@safe_resolve_call
def cutmaster_set_item_clip_color(
    color: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Set the clip color on a timeline item.

    Args:
        color: Clip color name.
    """
    if color not in CLIP_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(CLIP_COLORS))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.SetClipColor(color)
    return f"Clip color set to {color}." if result else "Failed to set clip color."


# ---------------------------------------------------------------------------
# Color groups
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_color_groups() -> str:
    """List all color groups in the current project."""
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    names = [g.GetName() for g in groups if g]
    return json.dumps({"color_groups": names}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_assign_to_color_group(
    group_name: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Assign a timeline item to a color group.

    Args:
        group_name: Color group name.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    result = item.AssignToColorGroup(group)
    return f"Assigned to color group '{group_name}'." if result else "Failed to assign."


# ---------------------------------------------------------------------------
# Playhead / timecode
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_get_playhead_position() -> str:
    """Get the current playhead timecode."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    tc = tl.GetCurrentTimecode()
    return f"Playhead at {tc}"


@mcp.tool
@safe_resolve_call
def cutmaster_set_playhead_position(timecode: str) -> str:
    """Set the playhead to a specific timecode.

    Args:
        timecode: Timecode string (e.g. '01:00:05:12').
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.SetCurrentTimecode(timecode)
    return f"Playhead moved to {timecode}." if result else f"Failed to set playhead to {timecode}."


@mcp.tool
@safe_resolve_call
def cutmaster_get_current_video_item() -> str:
    """Get the timeline item currently under the playhead."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.GetCurrentVideoItem()
    if not item:
        return "No video item at the playhead position."
    return json.dumps(
        {
            "name": item.GetName(),
            "duration": item.GetDuration(),
            "unique_id": item.GetUniqueId(),
        },
        indent=2,
    )
