"""Render tools — queue, presets, formats, codecs, and render control."""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser, get_resolve

# ---------------------------------------------------------------------------
# Render formats & codecs
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_formats() -> str:
    """List all available render formats."""
    _, project, _ = _boilerplate()
    formats = project.GetRenderFormats() or {}
    return json.dumps(_ser(formats), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_codecs(format_name: str) -> str:
    """List available codecs for a render format.

    Args:
        format_name: Format name (e.g. 'mp4', 'mov', 'mxf').
    """
    _, project, _ = _boilerplate()
    codecs = project.GetRenderCodecs(format_name) or {}
    return json.dumps(_ser(codecs), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_resolutions(format_name: str, codec: str) -> str:
    """List available resolutions for a format/codec combination.

    Args:
        format_name: Format name.
        codec: Codec name.
    """
    _, project, _ = _boilerplate()
    resolutions = project.GetRenderResolutions(format_name, codec) or []
    return json.dumps(_ser(resolutions), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_set_render_format_and_codec(format_name: str, codec: str) -> str:
    """Set the current render format and codec.

    Args:
        format_name: Format name (e.g. 'mp4', 'mov', 'mxf').
        codec: Codec name (e.g. 'H264', 'H265', 'ProRes422HQ').
    """
    _, project, _ = _boilerplate()
    result = project.SetCurrentRenderFormatAndCodec(format_name, codec)
    return f"Render set to {format_name}/{codec}." if result else "Failed to set format/codec."


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_format_and_codec() -> str:
    """Get the current render format and codec."""
    _, project, _ = _boilerplate()
    info = project.GetCurrentRenderFormatAndCodec() or {}
    return json.dumps(_ser(info), indent=2)


# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_settings() -> str:
    """Get all current render settings."""
    _, project, _ = _boilerplate()
    settings = project.GetRenderSettings() or {}
    return json.dumps(_ser(settings), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_set_render_settings(settings: dict) -> str:
    """Set render settings.

    Args:
        settings: Dictionary of settings to apply. Common keys:
            - TargetDir: Output directory path
            - CustomName: Output file name
            - FormatWidth / FormatHeight: Resolution
            - FrameRate: Output frame rate
            - MarkIn / MarkOut: Frame range
            - IsExportVideo / IsExportAudio: Include video/audio
    """
    _, project, _ = _boilerplate()
    result = project.SetRenderSettings(settings)
    return "Render settings applied." if result else "Failed to apply render settings."


# ---------------------------------------------------------------------------
# Render presets
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_list_render_presets() -> str:
    """List all available render presets."""
    _, project, _ = _boilerplate()
    presets = project.GetRenderPresetList() or []
    if not presets:
        return "No render presets available."
    return "Render presets:\n" + "\n".join(f"  - {p}" for p in presets)


@mcp.tool
@safe_resolve_call
def cutmaster_load_render_preset(name: str) -> str:
    """Load a render preset by name.

    Args:
        name: Preset name.
    """
    _, project, _ = _boilerplate()
    result = project.LoadRenderPreset(name)
    return f"Render preset '{name}' loaded." if result else f"Failed to load preset '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_save_render_preset(name: str) -> str:
    """Save current render settings as a new preset.

    Args:
        name: Name for the new preset.
    """
    _, project, _ = _boilerplate()
    result = project.SaveAsNewRenderPreset(name)
    return f"Render preset '{name}' saved." if result else f"Failed to save preset '{name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_import_render_preset(path: str) -> str:
    """Import a render preset from a file.

    Args:
        path: Path to the preset file.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.ImportRenderPreset(path)
    return f"Render preset imported from {path}." if result else "Failed to import preset."


@mcp.tool
@safe_resolve_call
def cutmaster_export_render_preset(name: str, path: str) -> str:
    """Export a render preset to a file.

    Args:
        name: Preset name to export.
        path: Output file path.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.ExportRenderPreset(name, path)
    return f"Preset '{name}' exported to {path}." if result else "Failed to export preset."


# ---------------------------------------------------------------------------
# Render queue
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_add_render_job() -> str:
    """Add the current timeline to the render queue with current settings."""
    _, project, _ = _boilerplate()
    job_id = project.AddRenderJob()
    return f"Render job added (ID: {job_id})." if job_id else "Failed to add render job."


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_jobs() -> str:
    """List all render jobs in the queue."""
    _, project, _ = _boilerplate()
    jobs = project.GetRenderJobList() or []
    if not jobs:
        return "Render queue is empty."
    return json.dumps(_ser(jobs), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_delete_render_job(job_id: str) -> str:
    """Delete a render job from the queue.

    Args:
        job_id: Render job ID.
    """
    _, project, _ = _boilerplate()
    result = project.DeleteRenderJob(job_id)
    return f"Render job {job_id} deleted." if result else f"Failed to delete job {job_id}."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_all_render_jobs() -> str:
    """Clear all render jobs from the queue."""
    _, project, _ = _boilerplate()
    result = project.DeleteAllRenderJobs()
    return "All render jobs deleted." if result else "Failed to clear render queue."


# ---------------------------------------------------------------------------
# Render execution
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_start_render(job_ids: list[str] | None = None, interactive: bool = False) -> str:
    """Start rendering jobs in the queue.

    Args:
        job_ids: Specific job IDs to render (all if omitted).
        interactive: If True, show render dialog.
    """
    _, project, _ = _boilerplate()
    if job_ids:
        result = project.StartRendering(job_ids, interactive)
    else:
        result = project.StartRendering()
    return "Rendering started." if result else "Failed to start rendering."


@mcp.tool
@safe_resolve_call
def cutmaster_stop_render() -> str:
    """Stop the current render."""
    _, project, _ = _boilerplate()
    project.StopRendering()
    return "Rendering stopped."


@mcp.tool
@safe_resolve_call
def cutmaster_is_rendering() -> str:
    """Check if rendering is currently in progress."""
    _, project, _ = _boilerplate()
    result = project.IsRenderingInProgress()
    return "Rendering is in progress." if result else "No rendering in progress."


@mcp.tool
@safe_resolve_call
def cutmaster_get_render_job_status(job_id: str) -> str:
    """Get the status of a specific render job.

    Args:
        job_id: Render job ID.
    """
    _, project, _ = _boilerplate()
    status = project.GetRenderJobStatus(job_id)
    return json.dumps(_ser(status), indent=2) if status else f"No status for job {job_id}."
