"""Conform workflow — import EDL/XML, relink media, and verify.

Compound tools for conform and round-trip editorial workflows.
"""

import json
import os

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _collect_clips_recursive


@mcp.tool
@safe_resolve_call
def celavii_conform_timeline(
    timeline_path: str,
    media_path: str = "",
    relink: bool = True,
) -> str:
    """Import a timeline file and relink to source media.

    This workflow:
    1. Imports a timeline from EDL/XML/AAF/FCPXML/OTIO
    2. Optionally relinks offline clips to media at the specified path
    3. Reports conforming status (online vs offline clips)

    Args:
        timeline_path: Path to the timeline file (EDL, XML, AAF, FCPXML, OTIO).
        media_path: Path to source media for relinking. Skip relinking if empty.
        relink: Whether to attempt automatic relinking.
    """
    _, project, mp = _boilerplate()

    if not os.path.isfile(timeline_path):
        return f"Error: Timeline file '{timeline_path}' not found."

    # 1. Import timeline
    if media_path:
        options = {"sourceClipsPath": media_path}
        timelines = mp.ImportTimelineFromFile(timeline_path, options)
    else:
        timelines = mp.ImportTimelineFromFile(timeline_path)

    if not timelines:
        return f"Error: Failed to import timeline from '{timeline_path}'."

    imported_names = [tl.GetName() for tl in timelines if tl]

    # 2. Switch to first imported timeline and check status
    tl = timelines[0]
    project.SetCurrentTimeline(tl)

    # 3. Count clips and check online status
    total_clips = 0
    online_clips = 0
    offline_clips = 0

    for track_type in ("video", "audio"):
        track_count = tl.GetTrackCount(track_type) or 0
        for ti in range(1, track_count + 1):
            items = tl.GetItemListInTrack(track_type, ti) or []
            for item in items:
                total_clips += 1
                try:
                    mpi = item.GetMediaPoolItem()
                    if mpi and mpi.GetClipProperty("File Path"):
                        online_clips += 1
                    else:
                        offline_clips += 1
                except (AttributeError, TypeError):
                    offline_clips += 1

    result = {
        "imported_timelines": imported_names,
        "total_clips": total_clips,
        "online": online_clips,
        "offline": offline_clips,
        "source_file": timeline_path,
    }
    if media_path:
        result["media_path"] = media_path

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_relink_offline_clips(
    media_path: str,
    bin_path: str = "",
) -> str:
    """Relink all offline clips in a bin to media at a new path.

    Scans the media pool for clips without valid file paths and
    attempts to relink them to files in the specified directory.

    Args:
        media_path: Directory containing source media files.
        bin_path: Bin to scan. Scans entire pool if empty.
    """
    _, _, mp = _boilerplate()

    if not os.path.isdir(media_path):
        return f"Error: Media path '{media_path}' is not a directory."

    root = mp.GetRootFolder()
    if bin_path:
        from ..resolve import _find_bin

        folder = _find_bin(root, bin_path)
        if not folder:
            return f"Error: Bin '{bin_path}' not found."
    else:
        folder = root

    # Collect all clips
    all_clips = _collect_clips_recursive(folder)
    offline = []

    for name, clip in all_clips.items():
        # Skip stems (we only want full filenames)
        if "." not in name:
            continue
        try:
            file_path = clip.GetClipProperty("File Path")
            if not file_path or not os.path.isfile(file_path):
                offline.append(clip)
        except (AttributeError, TypeError):
            offline.append(clip)

    if not offline:
        return "All clips are online — no relinking needed."

    # Attempt relink
    result = mp.RelinkClips(offline, media_path)

    return json.dumps(
        {
            "attempted": len(offline),
            "relinked": result,
            "media_path": media_path,
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_verify_timeline_media() -> str:
    """Verify that all clips in the current timeline are online.

    Reports any offline or missing media with clip names and track positions.
    """
    _, project, _ = _boilerplate()

    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    issues = []
    total = 0

    for track_type in ("video", "audio"):
        track_count = tl.GetTrackCount(track_type) or 0
        for ti in range(1, track_count + 1):
            items = tl.GetItemListInTrack(track_type, ti) or []
            for idx, item in enumerate(items):
                total += 1
                try:
                    mpi = item.GetMediaPoolItem()
                    if not mpi:
                        issues.append({
                            "clip": item.GetName(),
                            "track": f"{track_type} {ti}",
                            "index": idx,
                            "issue": "No media pool item",
                        })
                        continue
                    file_path = mpi.GetClipProperty("File Path")
                    if not file_path:
                        issues.append({
                            "clip": item.GetName(),
                            "track": f"{track_type} {ti}",
                            "index": idx,
                            "issue": "No file path",
                        })
                    elif not os.path.isfile(file_path):
                        issues.append({
                            "clip": item.GetName(),
                            "track": f"{track_type} {ti}",
                            "index": idx,
                            "issue": f"File missing: {file_path}",
                        })
                except (AttributeError, TypeError):
                    issues.append({
                        "clip": item.GetName(),
                        "track": f"{track_type} {ti}",
                        "index": idx,
                        "issue": "Could not verify",
                    })

    return json.dumps(
        {
            "timeline": tl.GetName(),
            "total_clips": total,
            "issues": len(issues),
            "all_online": len(issues) == 0,
            "details": issues if issues else "All clips online",
        },
        indent=2,
    )
