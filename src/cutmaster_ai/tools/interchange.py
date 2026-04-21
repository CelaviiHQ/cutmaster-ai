"""Interchange format tools — timeline import/export via OTIO, EDL, XML, FCPXML.

These tools provide higher-level interchange operations beyond the basic
timeline export tool in timeline_mgmt.py, including OTIO-based workflows.
"""

from ..config import mcp
from ..constants import EXPORT_TYPES
from ..errors import safe_resolve_call
from ..resolve import _boilerplate


@mcp.tool
@safe_resolve_call
def cutmaster_export_edl(output_path: str) -> str:
    """Export the current timeline as an EDL (Edit Decision List).

    Args:
        output_path: Output file path (should end with .edl).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.Export(output_path, EXPORT_TYPES["EDL"])
    return f"EDL exported to {output_path}." if result else "Failed to export EDL."


@mcp.tool
@safe_resolve_call
def cutmaster_export_fcpxml(output_path: str) -> str:
    """Export the current timeline as FCPXML (Final Cut Pro XML).

    Args:
        output_path: Output file path (should end with .fcpxml).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.Export(output_path, EXPORT_TYPES["FCPXML"])
    return f"FCPXML exported to {output_path}." if result else "Failed to export FCPXML."


@mcp.tool
@safe_resolve_call
def cutmaster_export_aaf(output_path: str) -> str:
    """Export the current timeline as AAF (Advanced Authoring Format).

    Args:
        output_path: Output file path (should end with .aaf).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.Export(output_path, EXPORT_TYPES["AAF"])
    return f"AAF exported to {output_path}." if result else "Failed to export AAF."


@mcp.tool
@safe_resolve_call
def cutmaster_export_otio(output_path: str) -> str:
    """Export the current timeline as OTIO (OpenTimelineIO).

    Args:
        output_path: Output file path (should end with .otio).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.Export(output_path, EXPORT_TYPES["OTIO"])
    return f"OTIO exported to {output_path}." if result else "Failed to export OTIO."


@mcp.tool
@safe_resolve_call
def cutmaster_export_csv(output_path: str) -> str:
    """Export the current timeline as CSV.

    Args:
        output_path: Output file path (should end with .csv).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    result = tl.Export(output_path, EXPORT_TYPES["CSV"])
    return f"CSV exported to {output_path}." if result else "Failed to export CSV."


@mcp.tool
@safe_resolve_call
def cutmaster_import_timeline_file(
    path: str,
    source_clips_path: str = "",
) -> str:
    """Import a timeline from a file (AAF, EDL, XML, FCPXML, OTIO).

    Supports importing with optional source clip path for relinking.

    Args:
        path: Path to the timeline file.
        source_clips_path: Optional path to source clips for relinking.
    """
    _, _, mp = _boilerplate()
    if source_clips_path:
        options = {"sourceClipsPath": source_clips_path}
        timelines = mp.ImportTimelineFromFile(path, options)
    else:
        timelines = mp.ImportTimelineFromFile(path)
    if timelines:
        names = [tl.GetName() for tl in timelines if tl]
        return f"Imported {len(timelines)} timeline(s): {', '.join(names)}"
    return f"Failed to import timeline from {path}."
