"""Media storage tools — volumes, file browsing, and import to media pool."""

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import get_resolve


@mcp.tool
@safe_resolve_call
def cutmaster_list_volumes() -> str:
    """List all mounted media storage volumes."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    ms = resolve.GetMediaStorage()
    if not ms:
        return "Error: Could not access Media Storage."
    volumes = ms.GetMountedVolumeList() or []
    if not volumes:
        return "No mounted volumes found."
    return "Mounted volumes:\n" + "\n".join(f"  - {v}" for v in volumes)


@mcp.tool
@safe_resolve_call
def cutmaster_browse_storage(path: str) -> str:
    """List sub-folders in a media storage path.

    Args:
        path: Absolute path to browse (e.g. '/Volumes/Media').
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    ms = resolve.GetMediaStorage()
    if not ms:
        return "Error: Could not access Media Storage."
    folders = ms.GetSubFolderList(path) or []
    if not folders:
        return f"No sub-folders found at {path}."
    return f"Sub-folders in {path}:\n" + "\n".join(f"  - {f}" for f in folders)


@mcp.tool
@safe_resolve_call
def cutmaster_list_files_in_storage(path: str) -> str:
    """List files in a media storage path.

    Args:
        path: Absolute path to list files from.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    ms = resolve.GetMediaStorage()
    if not ms:
        return "Error: Could not access Media Storage."
    files = ms.GetFileList(path) or []
    if not files:
        return f"No files found at {path}."
    return f"{len(files)} file(s) in {path}:\n" + "\n".join(f"  - {f}" for f in files[:100])


@mcp.tool
@safe_resolve_call
def cutmaster_import_from_storage(paths: list[str]) -> str:
    """Import media files from storage directly into the media pool.

    Args:
        paths: List of absolute file paths to import.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    ms = resolve.GetMediaStorage()
    if not ms:
        return "Error: Could not access Media Storage."
    items = ms.AddItemListToMediaPool(paths)
    if items:
        return f"Imported {len(items)} item(s) into the media pool."
    return "Failed to import media. Check file paths and formats."


@mcp.tool
@safe_resolve_call
def cutmaster_reveal_in_storage(path: str) -> str:
    """Reveal a file or folder in the Media Storage browser.

    Args:
        path: Absolute path to reveal.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    ms = resolve.GetMediaStorage()
    if not ms:
        return "Error: Could not access Media Storage."
    result = ms.RevealInStorage(path)
    return f"Revealed {path} in Media Storage." if result else f"Failed to reveal {path}."
