"""Delivery workflow — configure render, queue, start, and monitor.

Compound tools for common render/export scenarios.
"""

import json

from ..config import mcp
from ..constants import RENDER_PRESETS
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _resolve_safe_dir


@mcp.tool
@safe_resolve_call
def celavii_quick_deliver(
    preset: str = "h264",
    output_path: str = "",
    filename: str = "",
    resolution: str = "",
) -> str:
    """One-command render: set format, configure output, queue, and start.

    This workflow:
    1. Switches to the Deliver page
    2. Sets the render format/codec from a preset shorthand
    3. Configures the output path and filename
    4. Adds the job to the render queue
    5. Starts rendering

    Args:
        preset: Render shorthand — h264, h265, prores422, prores422hq,
                prores4444, proxylt, dnxhd, dnxhr, tiff, dpx, exr.
                Or a custom Resolve render preset name.
        output_path: Output directory. Defaults to ~/Documents/resolve-exports.
        filename: Output filename (without extension). Defaults to timeline name.
        resolution: Optional resolution override (e.g. '1920x1080', '3840x2160').
    """
    resolve, project, _ = _boilerplate()

    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline to render."

    # 1. Switch to Deliver page
    resolve.OpenPage("deliver")

    # 2. Set format/codec
    preset_info = RENDER_PRESETS.get(preset.lower())
    if preset_info:
        fmt = preset_info["format"]
        codec = preset_info["codec"]
        if not project.SetCurrentRenderFormatAndCodec(fmt, codec):
            return f"Error: Failed to set render format {fmt}/{codec}."
    else:
        # Try as a named Resolve preset
        if not project.LoadRenderPreset(preset):
            valid = ", ".join(sorted(RENDER_PRESETS.keys()))
            return f"Error: Unknown preset '{preset}'. Built-in: {valid}. Or use a Resolve preset name."

    # 3. Configure output
    settings = {}
    if output_path:
        safe_path = _resolve_safe_dir(output_path)
        settings["TargetDir"] = safe_path
    else:
        import os

        default_dir = os.path.join(os.path.expanduser("~"), "Documents", "resolve-exports")
        os.makedirs(default_dir, exist_ok=True)
        settings["TargetDir"] = default_dir

    if filename:
        settings["CustomName"] = filename
    else:
        settings["CustomName"] = tl.GetName()

    if resolution:
        parts = resolution.lower().split("x")
        if len(parts) == 2:
            settings["FormatWidth"] = parts[0]
            settings["FormatHeight"] = parts[1]

    if settings:
        project.SetRenderSettings(settings)

    # 4. Add to render queue
    job_id = project.AddRenderJob()
    if not job_id:
        return "Error: Failed to add render job to queue."

    # 5. Start rendering
    result = project.StartRendering([job_id])

    return json.dumps(
        {
            "status": "rendering" if result else "queued",
            "job_id": job_id,
            "preset": preset,
            "output": settings.get("TargetDir", ""),
            "filename": settings.get("CustomName", ""),
            "timeline": tl.GetName(),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_batch_deliver(
    presets: list[str],
    output_path: str = "",
) -> str:
    """Queue multiple renders with different presets and start them all.

    Useful for delivering multiple formats at once (e.g. h264 for web,
    prores422hq for archive, proxylt for editing).

    Args:
        presets: List of preset shorthands (e.g. ['h264', 'prores422hq']).
        output_path: Base output directory. Each preset gets a subfolder.
    """
    import os

    resolve, project, _ = _boilerplate()

    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline to render."

    resolve.OpenPage("deliver")

    base_dir = output_path or os.path.join(os.path.expanduser("~"), "Documents", "resolve-exports")
    base_dir = _resolve_safe_dir(base_dir)

    job_ids = []
    errors = []

    for preset in presets:
        preset_info = RENDER_PRESETS.get(preset.lower())
        if not preset_info:
            errors.append(f"Unknown preset: {preset}")
            continue

        fmt = preset_info["format"]
        codec = preset_info["codec"]
        if not project.SetCurrentRenderFormatAndCodec(fmt, codec):
            errors.append(f"Failed to set format for {preset}")
            continue

        preset_dir = os.path.join(base_dir, preset.lower())
        os.makedirs(preset_dir, exist_ok=True)

        project.SetRenderSettings({
            "TargetDir": preset_dir,
            "CustomName": tl.GetName(),
        })

        job_id = project.AddRenderJob()
        if job_id:
            job_ids.append({"preset": preset, "job_id": job_id})
        else:
            errors.append(f"Failed to queue {preset}")

    # Start all jobs
    started = False
    if job_ids:
        ids = [j["job_id"] for j in job_ids]
        started = project.StartRendering(ids)

    result = {
        "jobs_queued": len(job_ids),
        "jobs": job_ids,
        "rendering": started,
        "output_base": base_dir,
    }
    if errors:
        result["errors"] = errors

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_render_status() -> str:
    """Check the status of all render jobs in the queue.

    Returns each job's completion percentage, status, and timing.
    """
    _, project, _ = _boilerplate()

    is_rendering = project.IsRenderingInProgress()
    jobs = project.GetRenderJobList() or []

    job_statuses = []
    for job in jobs:
        entry = {}
        if isinstance(job, dict):
            entry["job_id"] = job.get("JobId", "")
            entry["timeline"] = job.get("TimelineName", "")
            entry["target_dir"] = job.get("TargetDir", "")
            job_id = job.get("JobId")
            if job_id:
                try:
                    status = project.GetRenderJobStatus(job_id)
                    if status and isinstance(status, dict):
                        entry["status"] = status.get("JobStatus", "")
                        entry["completion"] = status.get("CompletionPercentage", 0)
                        entry["time_remaining"] = status.get("EstimatedTimeRemainingInMs", 0)
                except (AttributeError, TypeError):
                    pass
        job_statuses.append(entry)

    return json.dumps(
        {
            "is_rendering": is_rendering,
            "job_count": len(job_statuses),
            "jobs": job_statuses,
        },
        indent=2,
    )
