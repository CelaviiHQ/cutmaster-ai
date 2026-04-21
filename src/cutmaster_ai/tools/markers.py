"""Marker tools — add, delete, list, and update markers on timelines and clips."""

import json

from ..config import mcp
from ..constants import MARKER_COLORS
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _find_clip_by_name, _ser
from .timeline_edit import _get_timeline_item

# ---------------------------------------------------------------------------
# Timeline markers
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_add_timeline_marker(
    frame: int,
    color: str = "Blue",
    name: str = "",
    note: str = "",
    duration: int = 1,
    custom_data: str = "",
) -> str:
    """Add a marker to the current timeline.

    Args:
        frame: Frame number for the marker.
        color: Marker color (Blue, Cyan, Green, Yellow, Red, Pink, etc.).
        name: Marker name.
        note: Marker note text.
        duration: Marker duration in frames.
        custom_data: Optional custom data string.
    """
    if color not in MARKER_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(MARKER_COLORS))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.AddMarker(frame, color, name, note, duration, custom_data)
    return (
        f"Marker added at frame {frame}." if result else "Failed — marker may exist at that frame."
    )


@mcp.tool
@safe_resolve_call
def celavii_get_timeline_markers() -> str:
    """Get all markers on the current timeline."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    markers = tl.GetMarkers() or {}
    return json.dumps(_ser(markers), indent=2)


@mcp.tool
@safe_resolve_call
def celavii_delete_timeline_marker_at_frame(frame: int) -> str:
    """Delete a timeline marker at a specific frame.

    Args:
        frame: Frame number of the marker to delete.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.DeleteMarkerAtFrame(frame)
    return f"Marker deleted at frame {frame}." if result else f"No marker at frame {frame}."


@mcp.tool
@safe_resolve_call
def celavii_delete_timeline_markers_by_color(color: str) -> str:
    """Delete all timeline markers of a specific color.

    Args:
        color: Marker color to delete.
    """
    if color not in MARKER_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(MARKER_COLORS))}"
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.DeleteMarkersByColor(color)
    return f"Deleted all {color} markers." if result else f"No {color} markers found."


@mcp.tool
@safe_resolve_call
def celavii_update_timeline_marker_custom_data(frame: int, custom_data: str) -> str:
    """Update the custom data on a timeline marker.

    Args:
        frame: Frame number of the marker.
        custom_data: New custom data string.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.UpdateMarkerCustomData(frame, custom_data)
    return "Custom data updated." if result else "Failed to update — no marker at that frame."


# ---------------------------------------------------------------------------
# Timeline item (clip) markers
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_add_item_marker(
    frame: int,
    color: str = "Blue",
    name: str = "",
    note: str = "",
    duration: int = 1,
    custom_data: str = "",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Add a marker to a timeline item (clip marker, frame offset relative to clip start).

    Args:
        frame: Frame offset from clip start (0-based).
        color: Marker color.
        name: Marker name.
        note: Marker note.
        duration: Marker duration in frames.
        custom_data: Optional custom data.
        track_type: Track type.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    if color not in MARKER_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(MARKER_COLORS))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.AddMarker(frame, color, name, note, duration, custom_data)
    return (
        f"Clip marker added at frame {frame}."
        if result
        else "Failed — marker may exist at that frame."
    )


@mcp.tool
@safe_resolve_call
def celavii_get_item_markers(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get all markers on a timeline item."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    markers = item.GetMarkers() or {}
    return json.dumps(_ser(markers), indent=2)


@mcp.tool
@safe_resolve_call
def celavii_delete_item_marker_at_frame(
    frame: int,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Delete a marker on a timeline item at a specific frame offset.

    Args:
        frame: Frame offset of the marker.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.DeleteMarkerAtFrame(frame)
    return f"Clip marker deleted at frame {frame}." if result else f"No marker at frame {frame}."


@mcp.tool
@safe_resolve_call
def celavii_delete_item_markers_by_color(
    color: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Delete all markers of a specific color on a timeline item.

    Args:
        color: Marker color to delete.
    """
    if color not in MARKER_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(MARKER_COLORS))}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.DeleteMarkersByColor(color)
    return f"Deleted all {color} clip markers." if result else f"No {color} markers found."


# ---------------------------------------------------------------------------
# Media pool item markers
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_add_clip_marker(
    clip_name: str,
    frame: int,
    color: str = "Blue",
    name: str = "",
    note: str = "",
    duration: int = 1,
    custom_data: str = "",
) -> str:
    """Add a marker to a media pool clip.

    Args:
        clip_name: Clip name.
        frame: Frame number for the marker.
        color: Marker color.
        name: Marker name.
        note: Marker note.
        duration: Duration in frames.
        custom_data: Optional custom data.
    """
    if color not in MARKER_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(MARKER_COLORS))}"
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.AddMarker(frame, color, name, note, duration, custom_data)
    return f"Marker added at frame {frame} on '{clip_name}'." if result else "Failed to add marker."


@mcp.tool
@safe_resolve_call
def celavii_get_clip_markers(clip_name: str) -> str:
    """Get all markers on a media pool clip.

    Args:
        clip_name: Clip name.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    markers = clip.GetMarkers() or {}
    return json.dumps(_ser(markers), indent=2)
