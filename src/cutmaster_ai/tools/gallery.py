"""Gallery tools — still albums, stills management, export/import, power grades."""

import json
import os

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _resolve_safe_dir

# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_gallery_albums() -> str:
    """List all still albums in the gallery."""
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    albums = gallery.GetGalleryStillAlbums() or []
    names = []
    for a in albums:
        try:
            names.append(a.GetLabel(a) if hasattr(a, "GetLabel") else str(a))
        except Exception:
            names.append(str(a))
    return json.dumps({"albums": names, "count": len(names)}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_get_current_album() -> str:
    """Get the currently selected still album."""
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    stills = album.GetStills() or []
    return json.dumps({"still_count": len(stills)}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_set_current_album(album_index: int) -> str:
    """Set the current still album by index.

    Args:
        album_index: 0-based album index from cutmaster_list_gallery_albums.
    """
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    albums = gallery.GetGalleryStillAlbums() or []
    if album_index < 0 or album_index >= len(albums):
        return f"Album index {album_index} out of range (0-{len(albums) - 1})."
    result = gallery.SetCurrentStillAlbum(albums[album_index])
    return f"Set current album to index {album_index}." if result else "Failed to set album."


# ---------------------------------------------------------------------------
# Power grade albums
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_power_grade_albums() -> str:
    """List all power grade albums."""
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    albums = gallery.GetGalleryPowerGradeAlbums() or []
    return json.dumps({"power_grade_albums": len(albums)}, indent=2)


# ---------------------------------------------------------------------------
# Stills
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_stills() -> str:
    """List all stills in the current album."""
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    stills = album.GetStills() or []
    still_info = []
    for i, s in enumerate(stills):
        info = {"index": i}
        try:
            info["label"] = album.GetLabel(s)
        except (AttributeError, TypeError):
            pass
        still_info.append(info)
    return json.dumps({"stills": still_info, "count": len(still_info)}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_set_still_label(still_index: int, label: str) -> str:
    """Set the label on a still in the current album.

    Args:
        still_index: 0-based still index.
        label: Label text.
    """
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    stills = album.GetStills() or []
    if still_index < 0 or still_index >= len(stills):
        return f"Still index {still_index} out of range."
    result = album.SetLabel(stills[still_index], label)
    return f"Label set to '{label}'." if result else "Failed to set label."


@mcp.tool
@safe_resolve_call
def cutmaster_export_stills(
    output_path: str,
    still_indices: list[int] | None = None,
    prefix: str = "still",
    format: str = "dpx",
) -> str:
    """Export stills from the current album to files.

    Args:
        output_path: Directory to export stills to.
        still_indices: 0-based indices of stills to export (all if omitted).
        prefix: Filename prefix.
        format: Image format ('dpx', 'tif', 'jpg', 'png').
    """
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    stills = album.GetStills() or []
    if not stills:
        return "No stills in the current album."

    safe_path = _resolve_safe_dir(output_path)
    os.makedirs(safe_path, exist_ok=True)

    if still_indices is not None:
        selected = [stills[i] for i in still_indices if 0 <= i < len(stills)]
    else:
        selected = stills

    if not selected:
        return "No valid stills selected."

    result = album.ExportStills(selected, safe_path, prefix, format)
    return (
        f"Exported {len(selected)} still(s) to {safe_path}."
        if result
        else "Failed to export stills."
    )


@mcp.tool
@safe_resolve_call
def cutmaster_import_stills(file_paths: list[str]) -> str:
    """Import stills into the current album.

    Args:
        file_paths: List of image file paths to import.
    """
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    result = album.ImportStills(file_paths)
    return f"Imported {len(file_paths)} still(s)." if result else "Failed to import stills."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_stills(still_indices: list[int]) -> str:
    """Delete stills from the current album.

    Args:
        still_indices: 0-based indices of stills to delete.
    """
    _, project, _ = _boilerplate()
    gallery = project.GetGallery()
    if not gallery:
        return "Error: Could not access the Gallery."
    album = gallery.GetCurrentStillAlbum()
    if not album:
        return "No current album selected."
    stills = album.GetStills() or []
    selected = [stills[i] for i in still_indices if 0 <= i < len(stills)]
    if not selected:
        return "No valid stills selected."
    result = album.DeleteStills(selected)
    return f"Deleted {len(selected)} still(s)." if result else "Failed to delete stills."
