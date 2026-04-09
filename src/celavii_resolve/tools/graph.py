"""Color group graph tools — pre/post-clip node graphs and color group management."""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate

# ---------------------------------------------------------------------------
# Color group CRUD
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_create_color_group(name: str) -> str:
    """Create a new color group.

    Args:
        name: Name for the color group.
    """
    _, project, _ = _boilerplate()
    group = project.AddColorGroup(name)
    return f"Color group '{name}' created." if group else f"Failed to create color group '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_delete_color_group(name: str) -> str:
    """Delete a color group by name.

    Args:
        name: Color group name.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == name), None)
    if not group:
        return f"Color group '{name}' not found."
    result = project.DeleteColorGroup(group)
    return f"Color group '{name}' deleted." if result else f"Failed to delete '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_get_color_group_clips(
    group_name: str,
) -> str:
    """Get all timeline items assigned to a color group.

    Args:
        group_name: Color group name.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    clips = group.GetClipsInTimeline(tl) or []
    clip_names = [c.GetName() for c in clips]
    return json.dumps(
        {"group": group_name, "clips": clip_names, "count": len(clip_names)}, indent=2
    )


# ---------------------------------------------------------------------------
# Pre-clip / post-clip node graphs
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_pre_clip_graph(group_name: str) -> str:
    """Get the pre-clip node graph for a color group.

    Args:
        group_name: Color group name.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    graph = group.GetPreClipNodeGraph()
    if not graph:
        return "No pre-clip graph available."
    num_nodes = graph.GetNumNodes() or 0
    nodes = []
    for i in range(1, num_nodes + 1):
        node = {"index": i}
        try:
            node["label"] = graph.GetNodeLabel(i)
            node["enabled"] = graph.GetNodeEnabled(i)
        except (AttributeError, TypeError):
            pass
        nodes.append(node)
    return json.dumps({"graph": "pre-clip", "group": group_name, "nodes": nodes}, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_get_post_clip_graph(group_name: str) -> str:
    """Get the post-clip node graph for a color group.

    Args:
        group_name: Color group name.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    graph = group.GetPostClipNodeGraph()
    if not graph:
        return "No post-clip graph available."
    num_nodes = graph.GetNumNodes() or 0
    nodes = []
    for i in range(1, num_nodes + 1):
        node = {"index": i}
        try:
            node["label"] = graph.GetNodeLabel(i)
            node["enabled"] = graph.GetNodeEnabled(i)
        except (AttributeError, TypeError):
            pass
        nodes.append(node)
    return json.dumps({"graph": "post-clip", "group": group_name, "nodes": nodes}, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_set_group_graph_lut(
    group_name: str,
    node_index: int,
    lut_path: str,
    graph_type: str = "pre",
) -> str:
    """Apply a LUT to a node in a color group's pre/post-clip graph.

    Args:
        group_name: Color group name.
        node_index: 1-based node index.
        lut_path: Path to the LUT file.
        graph_type: 'pre' for pre-clip, 'post' for post-clip.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    if graph_type == "pre":
        graph = group.GetPreClipNodeGraph()
    elif graph_type == "post":
        graph = group.GetPostClipNodeGraph()
    else:
        return "Invalid graph_type. Use 'pre' or 'post'."
    if not graph:
        return f"No {graph_type}-clip graph available."
    result = graph.SetLUT(node_index, lut_path)
    return (
        f"LUT applied to {graph_type}-clip node {node_index}." if result else "Failed to apply LUT."
    )


@mcp.tool
@safe_resolve_call
def celavii_add_group_graph_node(
    group_name: str,
    graph_type: str = "pre",
) -> str:
    """Add a node to a color group's pre/post-clip graph.

    Args:
        group_name: Color group name.
        graph_type: 'pre' for pre-clip, 'post' for post-clip.
    """
    _, project, _ = _boilerplate()
    groups = project.GetColorGroupsList() or []
    group = next((g for g in groups if g.GetName() == group_name), None)
    if not group:
        return f"Color group '{group_name}' not found."
    if graph_type == "pre":
        graph = group.GetPreClipNodeGraph()
    elif graph_type == "post":
        graph = group.GetPostClipNodeGraph()
    else:
        return "Invalid graph_type. Use 'pre' or 'post'."
    if not graph:
        return f"No {graph_type}-clip graph available."
    idx = graph.AddNode()
    return (
        f"Node added at index {idx} in {graph_type}-clip graph." if idx else "Failed to add node."
    )
