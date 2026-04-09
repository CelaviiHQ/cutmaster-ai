"""MCP Resources — passive, read-only data exposed via resolve:// URIs.

Resources let the LLM inspect Resolve state without a tool call, reducing
token overhead for context gathering.
"""

import json

from .config import mcp
from .resolve import _boilerplate, _enumerate_bins, get_resolve, is_studio


@mcp.resource("resolve://version")
def resource_version() -> str:
    """Resolve version, edition (Free vs Studio), and current page."""
    resolve = get_resolve()
    if not resolve:
        return json.dumps({"error": "DaVinci Resolve is not running."})
    ver = "unknown"
    product = "unknown"
    page = "unknown"
    try:
        ver = resolve.GetVersionString() or ver
    except (AttributeError, TypeError):
        pass
    try:
        product = resolve.GetProductName() or product
    except (AttributeError, TypeError):
        pass
    try:
        page = resolve.GetCurrentPage() or page
    except (AttributeError, TypeError):
        pass
    return json.dumps(
        {
            "product": product,
            "version": ver,
            "studio": is_studio(),
            "current_page": page,
        },
        indent=2,
    )


@mcp.resource("resolve://project")
def resource_project() -> str:
    """Current project name, timeline count, and key settings."""
    resolve = get_resolve()
    if not resolve:
        return json.dumps({"error": "DaVinci Resolve is not running."})
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    if not project:
        return json.dumps({"error": "No project is currently open."})

    tl_count = 0
    current_tl = None
    try:
        tl_count = project.GetTimelineCount() or 0
    except (AttributeError, TypeError):
        pass
    try:
        tl = project.GetCurrentTimeline()
        current_tl = tl.GetName() if tl else None
    except (AttributeError, TypeError):
        pass

    return json.dumps(
        {
            "name": project.GetName(),
            "timeline_count": tl_count,
            "current_timeline": current_tl,
            "unique_id": project.GetUniqueId(),
        },
        indent=2,
    )


@mcp.resource("resolve://timelines")
def resource_timelines() -> str:
    """All timelines in the current project with track counts."""
    try:
        _, project, _ = _boilerplate()
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    count = project.GetTimelineCount() or 0
    timelines = []
    for i in range(1, count + 1):  # 1-based
        tl = project.GetTimelineByIndex(i)
        if not tl:
            continue
        info = {"name": tl.GetName(), "index": i}
        try:
            info["video_tracks"] = tl.GetTrackCount("video") or 0
            info["audio_tracks"] = tl.GetTrackCount("audio") or 0
            info["subtitle_tracks"] = tl.GetTrackCount("subtitle") or 0
        except (AttributeError, TypeError):
            pass
        try:
            info["start_frame"] = tl.GetStartFrame()
            info["end_frame"] = tl.GetEndFrame()
        except (AttributeError, TypeError):
            pass
        timelines.append(info)

    return json.dumps({"timelines": timelines, "count": count}, indent=2)


@mcp.resource("resolve://bins")
def resource_bins() -> str:
    """Media pool bin tree with clip counts per folder."""
    try:
        _, _, media_pool = _boilerplate()
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    root = media_pool.GetRootFolder()
    bins = _enumerate_bins(root)
    return json.dumps({"bins": bins}, indent=2)


@mcp.resource("resolve://render-queue")
def resource_render_queue() -> str:
    """Render jobs in the queue with statuses."""
    try:
        _, project, _ = _boilerplate()
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    jobs = project.GetRenderJobList() or []
    is_rendering = False
    try:
        is_rendering = project.IsRenderingInProgress()
    except (AttributeError, TypeError):
        pass

    job_list = []
    for job in jobs:
        entry = dict(job) if isinstance(job, dict) else {"raw": str(job)}
        # Try to get status for each job
        job_id = entry.get("JobId")
        if job_id:
            try:
                status = project.GetRenderJobStatus(job_id)
                if status:
                    entry["status"] = dict(status)
            except (AttributeError, TypeError):
                pass
        job_list.append(entry)

    return json.dumps(
        {
            "jobs": job_list,
            "job_count": len(job_list),
            "is_rendering": is_rendering,
        },
        indent=2,
    )
