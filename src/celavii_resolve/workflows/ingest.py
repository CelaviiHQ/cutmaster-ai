"""Ingest workflow — import media, organise into bins, and set metadata.

A compound tool that chains multiple granular operations into a single
high-level action for common media ingest scenarios.
"""

import json
import os
from pathlib import Path

from ..config import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _find_bin


@mcp.tool
@safe_resolve_call
def celavii_ingest_media(
    source_path: str,
    target_bin: str = "",
    create_bin: bool = False,
    set_metadata: dict | None = None,
    recursive: bool = False,
    media_types: str = "all",
) -> str:
    """Import media from a folder into the media pool with optional organisation.

    This workflow:
    1. Scans the source path for media files
    2. Creates the target bin if requested
    3. Imports all matching files into the bin
    4. Optionally sets metadata on all imported clips

    Args:
        source_path: Absolute path to folder or file(s) to import.
        target_bin: Target bin path (e.g. 'Footage/Day1'). Uses current bin if empty.
        create_bin: Create the target bin if it doesn't exist.
        set_metadata: Optional dict of metadata to apply to all imported clips
                      (e.g. {"Scene": "1", "Comments": "Interview"}).
        recursive: Scan sub-folders for media files.
        media_types: Filter — 'all', 'video', 'audio', or 'image'.
    """
    _, _, mp = _boilerplate()

    # 1. Resolve source files
    source = Path(source_path)
    if not source.exists():
        return f"Error: Source path '{source_path}' does not exist."

    ext_filter = set()
    if media_types == "video":
        ext_filter = VIDEO_EXTS
    elif media_types == "audio":
        ext_filter = AUDIO_EXTS
    elif media_types == "image":
        ext_filter = IMAGE_EXTS
    else:
        ext_filter = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

    if source.is_file():
        files = [str(source)]
    elif source.is_dir():
        if recursive:
            files = [str(f) for f in source.rglob("*") if f.suffix.lower() in ext_filter]
        else:
            files = [str(f) for f in source.iterdir() if f.is_file() and f.suffix.lower() in ext_filter]
    else:
        return f"Error: '{source_path}' is not a file or directory."

    if not files:
        return f"No matching media files found in '{source_path}' (filter: {media_types})."

    # 2. Set up target bin
    if target_bin:
        folder = _find_bin(mp.GetRootFolder(), target_bin)
        if not folder and create_bin:
            # Create nested bins
            parts = target_bin.strip("/").split("/")
            if parts and parts[0] == "Master":
                parts = parts[1:]
            current = mp.GetRootFolder()
            for part in parts:
                existing = next(
                    (s for s in (current.GetSubFolderList() or []) if s.GetName() == part),
                    None,
                )
                if existing:
                    current = existing
                else:
                    new_folder = mp.AddSubFolder(current, part)
                    if not new_folder:
                        return f"Error: Failed to create bin '{part}'."
                    current = new_folder
            folder = current
        elif not folder:
            return f"Error: Bin '{target_bin}' not found. Set create_bin=True to create it."
        mp.SetCurrentFolder(folder)

    # 3. Import media
    items = mp.ImportMedia(files)
    if not items:
        return "Error: Failed to import media files. Check file paths and formats."

    imported_count = len(items)

    # 4. Apply metadata if provided
    metadata_applied = 0
    if set_metadata:
        for item in items:
            try:
                for key, value in set_metadata.items():
                    item.SetMetadata(key, value)
                metadata_applied += 1
            except (AttributeError, TypeError):
                pass

    # Build result
    result = {
        "imported": imported_count,
        "source": source_path,
        "target_bin": target_bin or "(current bin)",
        "files_scanned": len(files),
    }
    if set_metadata:
        result["metadata_applied"] = metadata_applied
        result["metadata_keys"] = list(set_metadata.keys())

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_ingest_with_bins(
    source_path: str,
    bin_by: str = "subfolder",
) -> str:
    """Import media and auto-organise into bins based on folder structure.

    Creates bins mirroring the source directory structure and imports
    each folder's media into its corresponding bin.

    Args:
        source_path: Root folder to scan.
        bin_by: Organisation strategy — 'subfolder' creates bins matching
                the source folder structure.
    """
    _, _, mp = _boilerplate()

    source = Path(source_path)
    if not source.is_dir():
        return f"Error: '{source_path}' is not a directory."

    all_exts = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
    total_imported = 0
    bins_created = []

    root_folder = mp.GetRootFolder()

    for dirpath, _dirnames, filenames in os.walk(source):
        media_files = [
            os.path.join(dirpath, f)
            for f in filenames
            if Path(f).suffix.lower() in all_exts
        ]
        if not media_files:
            continue

        # Calculate relative bin path
        rel = os.path.relpath(dirpath, source_path)
        if rel == ".":
            bin_name = source.name
        else:
            bin_name = f"{source.name}/{rel}"

        # Create bin structure
        parts = bin_name.split(os.sep)
        current = root_folder
        for part in parts:
            existing = next(
                (s for s in (current.GetSubFolderList() or []) if s.GetName() == part),
                None,
            )
            if existing:
                current = existing
            else:
                new_folder = mp.AddSubFolder(current, part)
                if new_folder:
                    current = new_folder
                    bins_created.append(part)

        mp.SetCurrentFolder(current)
        items = mp.ImportMedia(media_files)
        if items:
            total_imported += len(items)

    return json.dumps({
        "total_imported": total_imported,
        "bins_created": len(bins_created),
        "source": source_path,
    }, indent=2)
