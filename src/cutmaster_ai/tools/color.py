"""Color grading tools — nodes, CDL, LUTs, grades, stills, and node graph."""

import json

from ..config import mcp
from ..constants import NODE_CACHE_MODES
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser
from .timeline_edit import _get_timeline_item

# ---------------------------------------------------------------------------
# CDL (ASC Color Decision List)
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_cdl(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get the CDL values of a timeline item.

    Returns slope, offset, power (SOP) and saturation values.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    cdl = item.GetCDL()
    return json.dumps(_ser(cdl), indent=2) if cdl else "No CDL data available."


@mcp.tool
@safe_resolve_call
def celavii_set_cdl(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    slope_r: float = 1.0,
    slope_g: float = 1.0,
    slope_b: float = 1.0,
    offset_r: float = 0.0,
    offset_g: float = 0.0,
    offset_b: float = 0.0,
    power_r: float = 1.0,
    power_g: float = 1.0,
    power_b: float = 1.0,
    saturation: float = 1.0,
) -> str:
    """Set CDL values on a timeline item.

    Args:
        slope_r/g/b: Slope (gain) for each channel.
        offset_r/g/b: Offset for each channel.
        power_r/g/b: Power (gamma) for each channel.
        saturation: Global saturation.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    cdl = {
        "NodeIndex": "1",
        "Slope": f"{slope_r} {slope_g} {slope_b}",
        "Offset": f"{offset_r} {offset_g} {offset_b}",
        "Power": f"{power_r} {power_g} {power_b}",
        "Saturation": str(saturation),
    }
    result = item.SetCDL(cdl)
    return "CDL values applied." if result else "Failed to set CDL values."


# ---------------------------------------------------------------------------
# Node graph
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_node_graph(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Get the node graph for a timeline item.

    Args:
        clip_level: True for clip-level graph, False for timeline-level.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    num_nodes = graph.GetNumNodes() or 0
    nodes = []
    for i in range(1, num_nodes + 1):  # 1-based
        node = {"index": i}
        try:
            node["label"] = graph.GetNodeLabel(i)
        except (AttributeError, TypeError):
            pass
        try:
            node["enabled"] = graph.GetNodeEnabled(i)
        except (AttributeError, TypeError):
            pass
        try:
            node["lut"] = graph.GetLUT(i) or ""
        except (AttributeError, TypeError):
            pass
        try:
            node["tools"] = graph.GetToolsInNode(i) or []
        except (AttributeError, TypeError):
            pass
        try:
            node["cache_mode"] = graph.GetNodeCacheMode(i)
        except (AttributeError, TypeError):
            pass
        nodes.append(node)
    return json.dumps({"num_nodes": num_nodes, "nodes": nodes}, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_add_node(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Add a new node to the node graph.

    Args:
        clip_level: True for clip-level graph, False for timeline-level.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    node_idx = graph.AddNode()
    return f"Node added at index {node_idx}." if node_idx else "Failed to add node."


@mcp.tool
@safe_resolve_call
def celavii_set_node_label(
    node_index: int,
    label: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Set the label on a color node.

    Args:
        node_index: 1-based node index.
        label: Label text.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    result = graph.SetNodeLabel(node_index, label)
    return f"Node {node_index} labeled '{label}'." if result else "Failed to set label."


@mcp.tool
@safe_resolve_call
def celavii_set_node_enabled(
    node_index: int,
    enabled: bool,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Enable or disable a color node.

    Args:
        node_index: 1-based node index.
        enabled: True to enable, False to disable.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    result = graph.SetNodeEnabled(node_index, enabled)
    state = "enabled" if enabled else "disabled"
    return f"Node {node_index} {state}." if result else f"Failed to {state} node."


@mcp.tool
@safe_resolve_call
def celavii_set_lut(
    node_index: int,
    lut_path: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Apply a LUT to a color node.

    Args:
        node_index: 1-based node index.
        lut_path: Absolute path to the LUT file (.cube, .3dl, etc.).
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    result = graph.SetLUT(node_index, lut_path)
    return f"LUT applied to node {node_index}." if result else "Failed to apply LUT."


@mcp.tool
@safe_resolve_call
def celavii_get_lut(
    node_index: int,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Get the LUT path applied to a node.

    Args:
        node_index: 1-based node index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    lut = graph.GetLUT(node_index)
    return f"Node {node_index} LUT: {lut}" if lut else f"No LUT on node {node_index}."


@mcp.tool
@safe_resolve_call
def celavii_set_node_cache_mode(
    node_index: int,
    mode: str = "Smart",
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    clip_level: bool = False,
) -> str:
    """Set the cache mode on a color node.

    Args:
        node_index: 1-based node index.
        mode: 'None', 'Smart', or 'On'.
    """
    mode_val = NODE_CACHE_MODES.get(mode)
    if mode_val is None:
        return f"Invalid mode '{mode}'. Valid: {', '.join(NODE_CACHE_MODES.keys())}"
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    graph = item.GetNodeGraph(clip_level)
    if not graph:
        return "No node graph available."
    result = graph.SetNodeCacheMode(node_index, mode_val)
    return f"Node {node_index} cache mode set to {mode}." if result else "Failed to set cache mode."


# ---------------------------------------------------------------------------
# Grade copy
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_copy_grades(
    source_track_type: str = "video",
    source_track_index: int = 1,
    source_item_index: int = 0,
    target_item_indices: list[int] | None = None,
    target_track_type: str = "video",
    target_track_index: int = 1,
) -> str:
    """Copy color grades from one timeline item to others.

    Args:
        source_track_type: Source track type.
        source_track_index: Source 1-based track index.
        source_item_index: Source 0-based item index.
        target_item_indices: 0-based indices of target items.
        target_track_type: Target track type.
        target_track_index: Target 1-based track index.
    """
    _, project, _ = _boilerplate()
    _, source = _get_timeline_item(
        project, source_track_type, source_track_index, source_item_index
    )
    tl = project.GetCurrentTimeline()
    target_items = tl.GetItemListInTrack(target_track_type, target_track_index) or []
    indices = target_item_indices or []
    targets = [target_items[i] for i in indices if 0 <= i < len(target_items)]
    if not targets:
        return "No valid target items."
    result = source.CopyGrades(targets)
    return f"Grades copied to {len(targets)} item(s)." if result else "Failed to copy grades."


# ---------------------------------------------------------------------------
# Stills (grab from timeline)
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_grab_still() -> str:
    """Grab a still from the current frame in the timeline."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    still = tl.GrabStill()
    return "Still grabbed to gallery." if still else "Failed to grab still."


@mcp.tool
@safe_resolve_call
def celavii_apply_grade_from_drx(
    drx_path: str,
    grade_index: int = 0,
    track_type: str = "video",
    track_index: int = 1,
    item_indices: list[int] | None = None,
) -> str:
    """Apply a grade from a .drx file to timeline items.

    Args:
        drx_path: Path to the .drx grade file.
        grade_index: Grade index within the file (0-based).
        track_type: Track type.
        track_index: 1-based track index.
        item_indices: 0-based indices of target items.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    items = tl.GetItemListInTrack(track_type, track_index) or []
    idxs = item_indices or []
    targets = [items[i] for i in idxs if 0 <= i < len(items)]
    if not targets:
        return "No valid target items."
    result = tl.ApplyGradeFromDRX(drx_path, grade_index, targets)
    return f"Grade applied to {len(targets)} item(s)." if result else "Failed to apply grade."
