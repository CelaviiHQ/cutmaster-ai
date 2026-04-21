"""Media pool tools — bins, clips, import, metadata, search, and organisation."""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import (
    _boilerplate,
    _collect_clips_recursive,
    _enumerate_bins,
    _find_bin,
    _find_clip_by_name,
    _ser,
)

# ---------------------------------------------------------------------------
# Bin / folder operations
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_bins() -> str:
    """List all bins (folders) in the media pool with clip counts."""
    _, _, mp = _boilerplate()
    bins = _enumerate_bins(mp.GetRootFolder())
    return json.dumps(bins, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_get_current_bin() -> str:
    """Get the currently selected bin in the media pool."""
    _, _, mp = _boilerplate()
    folder = mp.GetCurrentFolder()
    if not folder:
        return "No current bin selected."
    clips = folder.GetClipList() or []
    return json.dumps(
        {
            "name": folder.GetName(),
            "clip_count": len(clips),
            "unique_id": folder.GetUniqueId(),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def cutmaster_set_current_bin(bin_path: str) -> str:
    """Set the current bin by name or path (e.g. 'Master/Footage/Day1').

    Args:
        bin_path: Bin name or /-separated path from root.
    """
    _, _, mp = _boilerplate()
    folder = _find_bin(mp.GetRootFolder(), bin_path)
    if not folder:
        return f"Bin '{bin_path}' not found."
    result = mp.SetCurrentFolder(folder)
    return f"Current bin set to '{bin_path}'." if result else f"Failed to set bin '{bin_path}'."


@mcp.tool
@safe_resolve_call
def cutmaster_create_bin(name: str, parent_path: str = "") -> str:
    """Create a new bin (folder) in the media pool.

    Args:
        name: Name for the new bin.
        parent_path: Parent bin path. Uses current bin if empty.
    """
    _, _, mp = _boilerplate()
    if parent_path:
        parent = _find_bin(mp.GetRootFolder(), parent_path)
        if not parent:
            return f"Parent bin '{parent_path}' not found."
    else:
        parent = mp.GetCurrentFolder() or mp.GetRootFolder()
    new_folder = mp.AddSubFolder(parent, name)
    return f"Bin '{name}' created." if new_folder else f"Failed to create bin '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_bins(bin_names: list[str]) -> str:
    """Delete bins by name. Bins must be empty.

    Args:
        bin_names: List of bin names to delete.
    """
    _, _, mp = _boilerplate()
    root = mp.GetRootFolder()
    folders = []
    not_found = []
    for name in bin_names:
        f = _find_bin(root, name)
        if f:
            folders.append(f)
        else:
            not_found.append(name)
    if not folders:
        return f"No matching bins found. Not found: {', '.join(not_found)}"
    result = mp.DeleteFolders(folders)
    msg = f"Deleted {len(folders)} bin(s)."
    if not_found:
        msg += f" Not found: {', '.join(not_found)}"
    return msg if result else "Failed to delete bins."


@mcp.tool
@safe_resolve_call
def cutmaster_move_bins(bin_names: list[str], target_path: str) -> str:
    """Move bins to a different parent bin.

    Args:
        bin_names: List of bin names to move.
        target_path: Destination bin path.
    """
    _, _, mp = _boilerplate()
    root = mp.GetRootFolder()
    target = _find_bin(root, target_path)
    if not target:
        return f"Target bin '{target_path}' not found."
    folders = [f for name in bin_names if (f := _find_bin(root, name))]
    if not folders:
        return "No matching bins found."
    result = mp.MoveFolders(folders, target)
    return f"Moved {len(folders)} bin(s) to '{target_path}'." if result else "Failed to move bins."


@mcp.tool
@safe_resolve_call
def cutmaster_refresh_bins() -> str:
    """Refresh the media pool folder structure."""
    _, _, mp = _boilerplate()
    result = mp.RefreshFolders()
    return "Media pool refreshed." if result else "Failed to refresh media pool."


# ---------------------------------------------------------------------------
# Clip operations
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_clips(bin_path: str = "") -> str:
    """List clips in a bin. Defaults to the current bin.

    Args:
        bin_path: Bin name/path, or empty for current bin.
    """
    _, _, mp = _boilerplate()
    if bin_path:
        folder = _find_bin(mp.GetRootFolder(), bin_path)
        if not folder:
            return f"Bin '{bin_path}' not found."
    else:
        folder = mp.GetCurrentFolder() or mp.GetRootFolder()
    clips = folder.GetClipList() or []
    if not clips:
        return f"No clips in '{folder.GetName()}'."
    clip_list = []
    for c in clips:
        info = {"name": c.GetName()}
        try:
            props = c.GetClipProperty() or {}
            info["duration"] = props.get("Duration", "")
            info["fps"] = props.get("FPS", "")
            info["resolution"] = (
                f"{props.get('Resolution Width', '')}x{props.get('Resolution Height', '')}"
            )
        except (AttributeError, TypeError):
            pass
        clip_list.append(info)
    return json.dumps(
        {"bin": folder.GetName(), "clips": clip_list, "count": len(clip_list)}, indent=2
    )


@mcp.tool
@safe_resolve_call
def cutmaster_search_clips(query: str, bin_path: str = "") -> str:
    """Search for clips by name substring (case-insensitive).

    Args:
        query: Search string to match against clip names.
        bin_path: Optionally limit search to a specific bin.
    """
    _, _, mp = _boilerplate()
    if bin_path:
        folder = _find_bin(mp.GetRootFolder(), bin_path)
        if not folder:
            return f"Bin '{bin_path}' not found."
    else:
        folder = mp.GetRootFolder()
    query_lower = query.lower()
    matches, seen = [], set()
    for name in _collect_clips_recursive(folder):
        if query_lower in name.lower() and name not in seen:
            seen.add(name)
            matches.append(name)
            if len(matches) >= 50:
                break
    if not matches:
        return f"No clips matching '{query}' found."
    return f"{len(matches)} match(es):\n" + "\n".join(f"  - {m}" for m in matches)


@mcp.tool
@safe_resolve_call
def cutmaster_get_clip_info(clip_name: str) -> str:
    """Get all properties and metadata for a media pool clip.

    Args:
        clip_name: Clip name (with or without extension).
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found in media pool."
    return json.dumps(
        {
            "name": clip.GetName(),
            "unique_id": clip.GetUniqueId(),
            "media_id": clip.GetMediaId(),
            "properties": _ser(clip.GetClipProperty() or {}),
            "metadata": _ser(clip.GetMetadata() or {}),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def cutmaster_set_clip_metadata(clip_name: str, key: str, value: str) -> str:
    """Set a metadata field on a media pool clip.

    Args:
        clip_name: Clip name.
        key: Metadata key (e.g. 'Description', 'Comments', 'Keywords').
        value: Value to set.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.SetMetadata(key, value)
    return f"Set {key}='{value}' on '{clip_name}'." if result else "Failed to set metadata."


@mcp.tool
@safe_resolve_call
def cutmaster_set_clip_property(clip_name: str, key: str, value: str) -> str:
    """Set a property on a media pool clip.

    Args:
        clip_name: Clip name.
        key: Property key (e.g. 'Clip Color', 'Reel Name', 'PAR').
        value: Property value.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.SetClipProperty(key, value)
    return f"Set {key}='{value}' on '{clip_name}'." if result else "Failed to set property."


@mcp.tool
@safe_resolve_call
def cutmaster_import_media(file_paths: list[str], bin_path: str = "") -> str:
    """Import media files into the media pool.

    Args:
        file_paths: List of absolute file paths to import.
        bin_path: Target bin path. Uses current bin if empty.
    """
    _, _, mp = _boilerplate()
    if bin_path:
        folder = _find_bin(mp.GetRootFolder(), bin_path)
        if not folder:
            return f"Target bin '{bin_path}' not found."
        mp.SetCurrentFolder(folder)
    items = mp.ImportMedia(file_paths)
    if items:
        return f"Imported {len(items)} item(s) into the media pool."
    return "Failed to import media. Check file paths and formats."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_clips(clip_names: list[str]) -> str:
    """Delete clips from the media pool by name.

    Args:
        clip_names: List of clip names to delete.
    """
    _, _, mp = _boilerplate()
    clips = []
    not_found = []
    for name in clip_names:
        c = _find_clip_by_name(mp, name)
        if c:
            clips.append(c)
        else:
            not_found.append(name)
    if not clips:
        return f"No matching clips found. Not found: {', '.join(not_found)}"
    result = mp.DeleteClips(clips)
    msg = f"Deleted {len(clips)} clip(s)."
    if not_found:
        msg += f" Not found: {', '.join(not_found)}"
    return msg if result else "Failed to delete clips."


@mcp.tool
@safe_resolve_call
def cutmaster_move_clips(clip_names: list[str], target_bin: str) -> str:
    """Move clips to a different bin.

    Args:
        clip_names: List of clip names to move.
        target_bin: Destination bin name/path.
    """
    _, _, mp = _boilerplate()
    target = _find_bin(mp.GetRootFolder(), target_bin)
    if not target:
        return f"Target bin '{target_bin}' not found."
    clips = [c for name in clip_names if (c := _find_clip_by_name(mp, name))]
    if not clips:
        return "No matching clips found."
    result = mp.MoveClips(clips, target)
    return f"Moved {len(clips)} clip(s) to '{target_bin}'." if result else "Failed to move clips."


@mcp.tool
@safe_resolve_call
def cutmaster_set_clip_color(clip_name: str, color: str) -> str:
    """Set the clip color label on a media pool clip.

    Args:
        clip_name: Clip name.
        color: Color name (Orange, Apricot, Yellow, Lime, Olive, Green, Teal,
               Navy, Blue, Purple, Violet, Pink, Tan, Beige, Brown, Chocolate).
    """
    from ..constants import CLIP_COLORS

    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    if color not in CLIP_COLORS:
        return f"Invalid color '{color}'. Valid: {', '.join(sorted(CLIP_COLORS))}"
    result = clip.SetClipColor(color)
    return f"Set clip color to {color}." if result else "Failed to set clip color."


@mcp.tool
@safe_resolve_call
def cutmaster_clear_clip_color(clip_name: str) -> str:
    """Clear the clip color label from a media pool clip.

    Args:
        clip_name: Clip name.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.ClearClipColor()
    return "Clip color cleared." if result else "Failed to clear clip color."


@mcp.tool
@safe_resolve_call
def cutmaster_relink_clips(clip_names: list[str], new_path: str) -> str:
    """Relink clips to a new media path.

    Args:
        clip_names: List of clip names to relink.
        new_path: New base path for the media files.
    """
    _, _, mp = _boilerplate()
    clips = [c for name in clip_names if (c := _find_clip_by_name(mp, name))]
    if not clips:
        return "No matching clips found."
    result = mp.RelinkClips(clips, new_path)
    return f"Relinked {len(clips)} clip(s) to {new_path}." if result else "Failed to relink."


@mcp.tool
@safe_resolve_call
def cutmaster_unlink_clips(clip_names: list[str]) -> str:
    """Unlink clips from their media files (make offline).

    Args:
        clip_names: List of clip names to unlink.
    """
    _, _, mp = _boilerplate()
    clips = [c for name in clip_names if (c := _find_clip_by_name(mp, name))]
    if not clips:
        return "No matching clips found."
    result = mp.UnlinkClips(clips)
    return f"Unlinked {len(clips)} clip(s)." if result else "Failed to unlink."


@mcp.tool
@safe_resolve_call
def cutmaster_link_proxy_media(clip_name: str, proxy_path: str) -> str:
    """Link proxy media to a clip.

    Args:
        clip_name: Clip name.
        proxy_path: Path to the proxy media file.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.LinkProxyMedia(proxy_path)
    return f"Linked proxy media to '{clip_name}'." if result else "Failed to link proxy."


@mcp.tool
@safe_resolve_call
def cutmaster_unlink_proxy_media(clip_name: str) -> str:
    """Unlink proxy media from a clip.

    Args:
        clip_name: Clip name.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.UnlinkProxyMedia()
    return f"Unlinked proxy from '{clip_name}'." if result else "Failed to unlink proxy."


@mcp.tool
@safe_resolve_call
def cutmaster_replace_clip(clip_name: str, new_path: str) -> str:
    """Replace a clip's media with a different file.

    Args:
        clip_name: Clip name to replace.
        new_path: Path to the replacement media file.
    """
    _, _, mp = _boilerplate()
    clip = _find_clip_by_name(mp, clip_name)
    if not clip:
        return f"Clip '{clip_name}' not found."
    result = clip.ReplaceClip(new_path)
    return f"Replaced '{clip_name}' with {new_path}." if result else "Failed to replace clip."


@mcp.tool
@safe_resolve_call
def cutmaster_export_metadata(path: str, clip_names: list[str] | None = None) -> str:
    """Export media pool metadata to a CSV file.

    Args:
        path: Output file path.
        clip_names: Optional list of specific clips. Exports all if omitted.
    """
    _, _, mp = _boilerplate()
    if clip_names:
        clips = [c for name in clip_names if (c := _find_clip_by_name(mp, name))]
        result = mp.ExportMetadata(path, clips) if clips else False
    else:
        result = mp.ExportMetadata(path)
    return f"Metadata exported to {path}." if result else "Failed to export metadata."
