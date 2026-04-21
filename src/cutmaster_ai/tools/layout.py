"""Layout and preset tools — UI layout presets, burn-in presets, LUT management."""

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, get_resolve

# ---------------------------------------------------------------------------
# Layout presets
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_save_layout_preset(name: str) -> str:
    """Save the current UI layout as a preset.

    Args:
        name: Preset name.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.SaveLayoutPreset(name)
    return f"Layout preset '{name}' saved." if result else f"Failed to save preset '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_load_layout_preset(name: str) -> str:
    """Load a UI layout preset.

    Args:
        name: Preset name.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.LoadLayoutPreset(name)
    return f"Layout preset '{name}' loaded." if result else f"Failed to load preset '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_update_layout_preset(name: str) -> str:
    """Update an existing layout preset with the current UI state.

    Args:
        name: Preset name to update.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.UpdateLayoutPreset(name)
    return f"Layout preset '{name}' updated." if result else f"Failed to update preset '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_delete_layout_preset(name: str) -> str:
    """Delete a layout preset.

    Args:
        name: Preset name to delete.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.DeleteLayoutPreset(name)
    return f"Layout preset '{name}' deleted." if result else f"Failed to delete preset '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_export_layout_preset(name: str, path: str) -> str:
    """Export a layout preset to a file.

    Args:
        name: Preset name to export.
        path: Output file path.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.ExportLayoutPreset(name, path)
    return f"Preset '{name}' exported to {path}." if result else "Failed to export preset."


@mcp.tool
@safe_resolve_call
def celavii_import_layout_preset(path: str, name: str = "") -> str:
    """Import a layout preset from a file.

    Args:
        path: Path to the preset file.
        name: Optional name override for the imported preset.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    if name:
        result = resolve.ImportLayoutPreset(path, name)
    else:
        result = resolve.ImportLayoutPreset(path)
    return f"Layout preset imported from {path}." if result else "Failed to import preset."


# ---------------------------------------------------------------------------
# Burn-in presets
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_import_burn_in_preset(path: str) -> str:
    """Import a burn-in preset from a file.

    Args:
        path: Path to the burn-in preset file.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.ImportBurnInPreset(path)
    return f"Burn-in preset imported from {path}." if result else "Failed to import burn-in preset."


@mcp.tool
@safe_resolve_call
def celavii_export_burn_in_preset(name: str, path: str) -> str:
    """Export a burn-in preset to a file.

    Args:
        name: Burn-in preset name.
        path: Output file path.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    result = resolve.ExportBurnInPreset(name, path)
    return f"Burn-in preset '{name}' exported to {path}." if result else "Failed to export."


# ---------------------------------------------------------------------------
# LUT management
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_refresh_lut_list() -> str:
    """Refresh the project's LUT list (re-scan LUT directories)."""
    _, project, _ = _boilerplate()
    result = project.RefreshLUTList()
    return "LUT list refreshed." if result else "Failed to refresh LUT list."


# ---------------------------------------------------------------------------
# Keyframe mode
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_keyframe_mode() -> str:
    """Get the current keyframe mode."""
    from ..constants import KEYFRAME_MODES

    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    mode = resolve.GetKeyframeMode()
    mode_name = next((k for k, v in KEYFRAME_MODES.items() if v == mode), str(mode))
    return f"Keyframe mode: {mode_name} ({mode})"


@mcp.tool
@safe_resolve_call
def celavii_set_keyframe_mode(mode: str) -> str:
    """Set the keyframe mode.

    Args:
        mode: 'All', 'Color', or 'Sizing'.
    """
    from ..constants import KEYFRAME_MODES

    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    mode_val = KEYFRAME_MODES.get(mode)
    if mode_val is None:
        return f"Invalid mode '{mode}'. Valid: {', '.join(KEYFRAME_MODES.keys())}"
    result = resolve.SetKeyframeMode(mode_val)
    return f"Keyframe mode set to {mode}." if result else "Failed to set keyframe mode."
