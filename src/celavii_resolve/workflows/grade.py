"""Grade workflow — apply LUTs, adjust CDL, copy grades, and save stills.

Compound tools for common color grading workflows.
"""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate


@mcp.tool
@safe_resolve_call
def celavii_quick_grade(
    lut_path: str = "",
    node_label: str = "LUT",
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
    grab_still: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Apply a LUT and/or CDL to a timeline item in one step.

    This workflow:
    1. Gets the item's node graph
    2. Applies a LUT to node 1 (if lut_path provided)
    3. Sets CDL values (if non-default values provided)
    4. Labels the node
    5. Optionally grabs a still to the gallery

    Args:
        lut_path: Path to a LUT file (.cube, .3dl). Skip if empty.
        node_label: Label for the graded node.
        slope_r/g/b: CDL slope (gain) values.
        offset_r/g/b: CDL offset values.
        power_r/g/b: CDL power (gamma) values.
        saturation: CDL saturation.
        grab_still: Grab a still of the result to the gallery.
        track_type: Track type.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """

    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    items = tl.GetItemListInTrack(track_type, track_index) or []
    if item_index >= len(items):
        return f"Error: Item index {item_index} out of range."
    item = items[item_index]

    actions = []

    # Get node graph
    graph = item.GetNodeGraph(False)  # timeline-level
    if not graph:
        return "Error: Could not access node graph."

    # Apply LUT
    if lut_path:
        if graph.SetLUT(1, lut_path):
            actions.append(f"LUT applied: {lut_path}")
        else:
            actions.append(f"Failed to apply LUT: {lut_path}")

    # Set CDL (only if non-default values)
    has_cdl = any([
        slope_r != 1.0, slope_g != 1.0, slope_b != 1.0,
        offset_r != 0.0, offset_g != 0.0, offset_b != 0.0,
        power_r != 1.0, power_g != 1.0, power_b != 1.0,
        saturation != 1.0,
    ])
    if has_cdl:
        cdl = {
            "NodeIndex": "1",
            "Slope": f"{slope_r} {slope_g} {slope_b}",
            "Offset": f"{offset_r} {offset_g} {offset_b}",
            "Power": f"{power_r} {power_g} {power_b}",
            "Saturation": str(saturation),
        }
        if item.SetCDL(cdl):
            actions.append("CDL values applied")
        else:
            actions.append("Failed to apply CDL")

    # Label node
    if graph.SetNodeLabel(1, node_label):
        actions.append(f"Node labeled: {node_label}")

    # Grab still
    if grab_still:
        still = tl.GrabStill()
        if still:
            actions.append("Still grabbed to gallery")
        else:
            actions.append("Failed to grab still")

    return json.dumps({
        "clip": item.GetName(),
        "actions": actions,
    }, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_batch_apply_lut(
    lut_path: str,
    track_type: str = "video",
    track_index: int = 1,
    item_indices: list[int] | None = None,
    node_index: int = 1,
) -> str:
    """Apply a LUT to multiple timeline items at once.

    Args:
        lut_path: Path to the LUT file.
        track_type: Track type.
        track_index: 1-based track index.
        item_indices: 0-based indices of items. Applies to all if omitted.
        node_index: 1-based node index to apply the LUT to.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    items = tl.GetItemListInTrack(track_type, track_index) or []
    if not items:
        return f"No items on {track_type} track {track_index}."

    if item_indices is not None:
        targets = [(i, items[i]) for i in item_indices if 0 <= i < len(items)]
    else:
        targets = list(enumerate(items))

    applied = 0
    failed = 0
    for _idx, item in targets:
        graph = item.GetNodeGraph(False)
        if graph and graph.SetLUT(node_index, lut_path):
            applied += 1
        else:
            failed += 1

    return json.dumps({
        "lut": lut_path,
        "applied": applied,
        "failed": failed,
        "total": len(targets),
    }, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_copy_grade_to_all(
    source_item_index: int = 0,
    track_type: str = "video",
    track_index: int = 1,
) -> str:
    """Copy the grade from one clip to all other clips on the same track.

    Args:
        source_item_index: 0-based index of the source clip.
        track_type: Track type.
        track_index: 1-based track index.
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    items = tl.GetItemListInTrack(track_type, track_index) or []
    if source_item_index >= len(items):
        return f"Error: Source index {source_item_index} out of range."

    source = items[source_item_index]
    targets = [item for i, item in enumerate(items) if i != source_item_index]

    if not targets:
        return "No other clips on this track to copy to."

    result = source.CopyGrades(targets)
    return json.dumps({
        "source": source.GetName(),
        "copied_to": len(targets),
        "success": bool(result),
    }, indent=2)
