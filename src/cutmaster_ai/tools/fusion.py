"""Fusion tools — composition lifecycle, node graph, and Lua scripting."""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser
from .timeline_edit import _get_timeline_item

# ---------------------------------------------------------------------------
# Fusion composition lifecycle
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_get_fusion_comp_count(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get the number of Fusion compositions on a timeline item."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    count = item.GetFusionCompCount() or 0
    names = item.GetFusionCompNameList() or []
    return json.dumps({"count": count, "names": names}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_add_fusion_comp(
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Add a new Fusion composition to a timeline item."""
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    comp = item.AddFusionComp()
    return "Fusion composition added." if comp else "Failed to add Fusion comp."


@mcp.tool
@safe_resolve_call
def cutmaster_import_fusion_comp(
    path: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Import a Fusion composition from a .comp file.

    Args:
        path: Path to the .comp file.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    comp = item.ImportFusionComp(path)
    return f"Fusion comp imported from {path}." if comp else "Failed to import Fusion comp."


@mcp.tool
@safe_resolve_call
def cutmaster_export_fusion_comp(
    path: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Export a Fusion composition to a .comp file.

    Args:
        path: Output file path.
        comp_index: 1-based composition index.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.ExportFusionComp(path, comp_index)
    return f"Fusion comp exported to {path}." if result else "Failed to export Fusion comp."


@mcp.tool
@safe_resolve_call
def cutmaster_delete_fusion_comp(
    comp_name: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Delete a Fusion composition by name.

    Args:
        comp_name: Name of the composition to delete.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.DeleteFusionCompByName(comp_name)
    return f"Deleted Fusion comp '{comp_name}'." if result else f"Failed to delete '{comp_name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_load_fusion_comp(
    comp_name: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Load/activate a Fusion composition by name.

    Args:
        comp_name: Name of the composition to load.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    comp = item.LoadFusionCompByName(comp_name)
    return f"Loaded Fusion comp '{comp_name}'." if comp else f"Failed to load '{comp_name}'."


@mcp.tool
@safe_resolve_call
def cutmaster_rename_fusion_comp(
    old_name: str,
    new_name: str,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Rename a Fusion composition.

    Args:
        old_name: Current composition name.
        new_name: New composition name.
    """
    _, project, _ = _boilerplate()
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    result = item.RenameFusionCompByName(old_name, new_name)
    return f"Renamed '{old_name}' to '{new_name}'." if result else "Failed to rename comp."


# ---------------------------------------------------------------------------
# Fusion node graph (via FusionComp object)
# ---------------------------------------------------------------------------


def _get_comp(project, track_type, track_index, item_index, comp_index=1):
    """Get a FusionComp object. Returns (item, comp) or raises ValueError."""
    _, item = _get_timeline_item(project, track_type, track_index, item_index)
    comp = item.GetFusionCompByIndex(comp_index)
    if not comp:
        raise ValueError(f"No Fusion comp at index {comp_index}.")
    return item, comp


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_add_tool(
    tool_type: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    name: str = "",
) -> str:
    """Add a tool/node to a Fusion composition.

    Args:
        tool_type: Fusion tool ID (e.g. 'Blur', 'ColorCorrector', 'Transform',
                   'Background', 'Text', 'Merge', 'MediaIn', 'MediaOut').
        comp_index: 1-based composition index.
        name: Optional custom name for the tool.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    if name:
        tool = comp.AddTool(tool_type, -32768, -32768, name)
    else:
        tool = comp.AddTool(tool_type)
    return f"Tool '{tool_type}' added." if tool else f"Failed to add tool '{tool_type}'."


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_find_tool(
    tool_name: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Find a tool in a Fusion composition by name.

    Args:
        tool_name: Name of the tool to find.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    tool = comp.FindTool(tool_name)
    if not tool:
        return f"Tool '{tool_name}' not found."
    attrs = {}
    try:
        attrs = tool.GetAttrs() or {}
    except (AttributeError, TypeError):
        pass
    return json.dumps({"name": tool_name, "found": True, "attrs": _ser(attrs)}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_delete_tool(
    tool_name: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Delete a tool from a Fusion composition.

    Args:
        tool_name: Name of the tool to delete.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    tool = comp.FindTool(tool_name)
    if not tool:
        return f"Tool '{tool_name}' not found."
    tool.Delete()
    return f"Tool '{tool_name}' deleted."


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_connect(
    output_tool: str,
    input_tool: str,
    input_name: str = "Input",
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Connect two tools in a Fusion composition.

    Args:
        output_tool: Name of the tool providing output.
        input_tool: Name of the tool receiving input.
        input_name: Input name on the receiving tool (default 'Input').
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    out_tool = comp.FindTool(output_tool)
    in_tool = comp.FindTool(input_tool)
    if not out_tool:
        return f"Output tool '{output_tool}' not found."
    if not in_tool:
        return f"Input tool '{input_tool}' not found."
    try:
        inp = in_tool[input_name]
        inp.ConnectTo(out_tool.Output)
        return f"Connected {output_tool}.Output -> {input_tool}.{input_name}"
    except Exception as exc:
        return f"Failed to connect: {exc}"


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_set_input(
    tool_name: str,
    input_name: str,
    value: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Set an input value on a Fusion tool.

    Args:
        tool_name: Tool name.
        input_name: Input parameter name.
        value: Value to set (auto-converted to number if possible).
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    tool = comp.FindTool(tool_name)
    if not tool:
        return f"Tool '{tool_name}' not found."
    # Try numeric conversion
    try:
        v = float(value)
        if v == int(v):
            v = int(v)
    except ValueError:
        v = value
    try:
        inp = tool[input_name]
        inp[comp.CurrentTime] = v
        return f"Set {tool_name}.{input_name} = {v}"
    except Exception as exc:
        return f"Failed to set input: {exc}"


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_get_input(
    tool_name: str,
    input_name: str,
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get the current value of an input on a Fusion tool.

    Args:
        tool_name: Tool name.
        input_name: Input parameter name.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    tool = comp.FindTool(tool_name)
    if not tool:
        return f"Tool '{tool_name}' not found."
    try:
        value = tool[input_name][comp.CurrentTime]
        return f"{tool_name}.{input_name} = {_ser(value)}"
    except Exception as exc:
        return f"Failed to get input: {exc}"


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_get_tool_list(
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """List all tools in a Fusion composition."""
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    tools = comp.GetToolList() or {}
    tool_list = []
    for name, tool in tools.items():
        info = {"name": name}
        try:
            info["type"] = tool.GetAttrs().get("TOOLS_RegID", "")
        except (AttributeError, TypeError):
            pass
        tool_list.append(info)
    return json.dumps({"tools": tool_list, "count": len(tool_list)}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_get_comp_info(
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get information about a Fusion composition (frame range, current time)."""
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    info = {}
    try:
        attrs = comp.GetAttrs() or {}
        info["start"] = attrs.get("COMPN_RenderStart")
        info["end"] = attrs.get("COMPN_RenderEnd")
        info["global_start"] = attrs.get("COMPN_GlobalStart")
        info["global_end"] = attrs.get("COMPN_GlobalEnd")
        info["current_time"] = comp.CurrentTime
    except (AttributeError, TypeError):
        pass
    return json.dumps(_ser(info), indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_render(
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    wait: bool = True,
) -> str:
    """Render a Fusion composition.

    Args:
        comp_index: 1-based composition index.
        wait: If True, wait for render to complete.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    result = comp.Render(wait)
    return "Fusion render complete." if result else "Fusion render failed or was cancelled."


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_undo(
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Start an undo group in a Fusion composition.

    Call cutmaster_fusion_end_undo after making changes to group them.
    """
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    comp.StartUndo("CutMaster Edit")
    return "Undo group started."


@mcp.tool
@safe_resolve_call
def cutmaster_fusion_end_undo(
    comp_index: int = 1,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """End an undo group in a Fusion composition."""
    _, project, _ = _boilerplate()
    _, comp = _get_comp(project, track_type, track_index, item_index, comp_index)
    comp.EndUndo(True)
    return "Undo group ended."


# ---------------------------------------------------------------------------
# Insert Fusion composition into timeline
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def cutmaster_insert_fusion_comp_into_timeline() -> str:
    """Insert a new Fusion composition clip at the playhead in the timeline."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    item = tl.InsertFusionCompositionIntoTimeline()
    return "Fusion composition inserted into timeline." if item else "Failed to insert Fusion comp."
