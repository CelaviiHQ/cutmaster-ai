"""Media utilities — frame export, thumbnail extraction, and sidecar metadata.

Provides helpers used by the AI tools to get visual data out of Resolve
for analysis by Gemini or other vision models.
"""

import base64
import logging
import os
import tempfile
from pathlib import Path

from ..resolve import _boilerplate, _resolve_safe_dir

log = logging.getLogger("celavii-resolve")


def export_current_frame(
    output_dir: str = "",
    format: str = "jpg",
) -> dict:
    """Export the current frame from the timeline as an image file.

    Uses Resolve's GrabStill + ExportStills pipeline to get a frame out.
    Returns a dict with 'path', 'base64', and metadata, or 'error'.

    Args:
        output_dir: Directory for the exported image. Uses a safe temp dir if empty.
        format: Image format — 'jpg', 'png', 'tif', or 'dpx'.

    Returns:
        Dict with keys: path, base64 (if jpg/png), width, height, timecode, error.
    """
    try:
        _, project, _ = _boilerplate()
    except ValueError as exc:
        return {"error": str(exc)}

    tl = project.GetCurrentTimeline()
    if not tl:
        return {"error": "No current timeline."}

    gallery = project.GetGallery()
    if not gallery:
        return {"error": "Could not access Gallery."}

    album = gallery.GetCurrentStillAlbum()
    if not album:
        return {"error": "No current still album. Open the Gallery and select an album."}

    # Grab still from current frame
    still = tl.GrabStill()
    if not still:
        return {"error": "Failed to grab still from current frame."}

    # Set up export directory
    if output_dir:
        export_dir = _resolve_safe_dir(output_dir)
    else:
        export_dir = _resolve_safe_dir(tempfile.gettempdir())
    os.makedirs(export_dir, exist_ok=True)

    # Export the still
    prefix = "celavii_frame"
    success = album.ExportStills([still], export_dir, prefix, format)
    if not success:
        # Try fallback formats
        for fallback in ("tif", "dpx", "jpg"):
            if fallback != format:
                success = album.ExportStills([still], export_dir, prefix, fallback)
                if success:
                    format = fallback
                    break

    if not success:
        return {"error": "Failed to export still. Check Gallery album permissions."}

    # Find the exported file
    exported_file = None
    for f in Path(export_dir).glob(f"{prefix}*"):
        if f.suffix.lower().lstrip(".") in ("jpg", "jpeg", "png", "tif", "tiff", "dpx", "exr"):
            exported_file = str(f)
            break

    if not exported_file:
        return {"error": f"Export reported success but no file found in {export_dir}."}

    result = {
        "path": exported_file,
        "format": format,
    }

    # Get timecode
    try:
        result["timecode"] = tl.GetCurrentTimecode()
    except (AttributeError, TypeError):
        pass

    # Get timeline info
    try:
        result["timeline"] = tl.GetName()
    except (AttributeError, TypeError):
        pass

    # Encode as base64 for API calls (jpg/png only — reasonable sizes)
    if format in ("jpg", "png") and os.path.isfile(exported_file):
        try:
            file_size = os.path.getsize(exported_file)
            if file_size < 20 * 1024 * 1024:  # < 20 MB
                with open(exported_file, "rb") as f:
                    result["base64"] = base64.b64encode(f.read()).decode("utf-8")
                result["size_bytes"] = file_size
        except Exception as exc:
            log.warning("Failed to encode frame as base64: %s", exc)

    # Clean up the still from the gallery (don't pollute the album)
    try:
        album.DeleteStills([still])
    except (AttributeError, TypeError):
        pass

    return result


def get_timeline_frame_info() -> dict:
    """Get metadata about the current frame without exporting it.

    Returns timecode, frame number, timeline name, and current clip info.
    """
    try:
        _, project, _ = _boilerplate()
    except ValueError as exc:
        return {"error": str(exc)}

    tl = project.GetCurrentTimeline()
    if not tl:
        return {"error": "No current timeline."}

    info = {
        "timeline": tl.GetName(),
    }

    try:
        info["timecode"] = tl.GetCurrentTimecode()
    except (AttributeError, TypeError):
        pass

    try:
        info["start_frame"] = tl.GetStartFrame()
        info["end_frame"] = tl.GetEndFrame()
    except (AttributeError, TypeError):
        pass

    # Get current video item under playhead
    try:
        item = tl.GetCurrentVideoItem()
        if item:
            info["current_clip"] = item.GetName()
            info["clip_duration"] = item.GetDuration()
    except (AttributeError, TypeError):
        pass

    return info
