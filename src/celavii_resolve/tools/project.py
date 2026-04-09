"""Project management tools — CRUD, settings, databases, folders."""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser, get_resolve

# ---------------------------------------------------------------------------
# Version / status
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_version() -> str:
    """Get DaVinci Resolve product name, version, and current page."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    return json.dumps(
        {
            "product": resolve.GetProductName(),
            "version": resolve.GetVersionString(),
            "page": resolve.GetCurrentPage(),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_switch_page(page: str) -> str:
    """Switch to a Resolve page.

    Valid pages: media, cut, edit, fusion, color, fairlight, deliver
    """
    from ..constants import PAGES

    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    page = page.lower()
    if page not in PAGES:
        return f"Error: Invalid page '{page}'. Valid: {', '.join(sorted(PAGES))}"
    result = resolve.OpenPage(page)
    return f"Switched to {page} page." if result else f"Failed to switch to {page} page."


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_list_projects() -> str:
    """List all projects in the current database folder."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    if not pm:
        return "Error: Could not access the Project Manager."
    projects = pm.GetProjectListInCurrentFolder() or []
    if not projects:
        return "No projects found in the current folder."
    return "Projects:\n" + "\n".join(f"  - {p}" for p in projects)


@mcp.tool
@safe_resolve_call
def celavii_get_current_project() -> str:
    """Get the name and details of the currently open project."""
    _, project, _ = _boilerplate()
    tl_count = project.GetTimelineCount() or 0
    current_tl = None
    try:
        tl = project.GetCurrentTimeline()
        current_tl = tl.GetName() if tl else None
    except (AttributeError, TypeError):
        pass
    return json.dumps(
        {
            "name": project.GetName(),
            "unique_id": project.GetUniqueId(),
            "timeline_count": tl_count,
            "current_timeline": current_tl,
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_create_project(name: str) -> str:
    """Create a new project with the given name."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    if not pm:
        return "Error: Could not access the Project Manager."
    project = pm.CreateProject(name)
    if project:
        return f"Project '{name}' created and opened."
    return f"Failed to create project '{name}'. A project with that name may already exist."


@mcp.tool
@safe_resolve_call
def celavii_open_project(name: str) -> str:
    """Open an existing project by name."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    if not pm:
        return "Error: Could not access the Project Manager."
    projects = pm.GetProjectListInCurrentFolder() or []
    if name not in projects:
        return (
            f"Error: Project '{name}' not found. "
            f"Available: {', '.join(projects) if projects else 'none'}"
        )
    result = pm.LoadProject(name)
    return f"Project '{name}' opened." if result else f"Failed to open project '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_save_project() -> str:
    """Save the current project."""
    _, project, _ = _boilerplate()
    name = project.GetName()
    result = project.SaveProject()  # type: ignore[attr-defined]
    # SaveProject is on ProjectManager in some API versions
    if not result:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()  # type: ignore[union-attr]
        result = pm.SaveProject()
    return f"Project '{name}' saved." if result else f"Failed to save project '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_close_project() -> str:
    """Close the current project (returns to Project Manager)."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        return "No project is open."
    name = project.GetName()
    result = pm.CloseProject(project)
    return f"Project '{name}' closed." if result else f"Failed to close project '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_delete_project(name: str) -> str:
    """Delete a project by name. The project must not be currently open."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.DeleteProject(name)
    return f"Project '{name}' deleted." if result else f"Failed to delete project '{name}'."


# ---------------------------------------------------------------------------
# Project import / export / archive
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_export_project(name: str, path: str, with_stills: bool = True) -> str:
    """Export a project to a .drp file.

    Args:
        name: Project name to export.
        path: Destination file path (should end with .drp).
        with_stills: Include gallery stills in the export.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.ExportProject(name, path, with_stills)
    return f"Project '{name}' exported to {path}." if result else f"Failed to export '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_import_project(path: str, name: str = "") -> str:
    """Import a project from a .drp file.

    Args:
        path: Path to the .drp file.
        name: Optional name for the imported project (uses file name if empty).
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    if name:
        result = pm.ImportProject(path, name)
    else:
        result = pm.ImportProject(path)
    return f"Project imported from {path}." if result else f"Failed to import from {path}."


@mcp.tool
@safe_resolve_call
def celavii_archive_project(
    name: str,
    path: str,
    archive_src_media: bool = True,
    archive_render_cache: bool = False,
    archive_proxy_media: bool = False,
) -> str:
    """Archive a project to a .dra file with optional media.

    Args:
        name: Project name to archive.
        path: Destination archive path.
        archive_src_media: Include source media files.
        archive_render_cache: Include render cache.
        archive_proxy_media: Include proxy media.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.ArchiveProject(
        name, path, archive_src_media, archive_render_cache, archive_proxy_media
    )
    return f"Project '{name}' archived to {path}." if result else f"Failed to archive '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_restore_project(path: str, name: str = "") -> str:
    """Restore a project from a .dra archive.

    Args:
        path: Path to the .dra archive file.
        name: Optional name for the restored project.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    if name:
        result = pm.RestoreProject(path, name)
    else:
        result = pm.RestoreProject(path)
    return f"Project restored from {path}." if result else f"Failed to restore from {path}."


# ---------------------------------------------------------------------------
# Project folders (in Project Manager)
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_list_project_folders() -> str:
    """List folders in the current Project Manager location."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    folders = pm.GetFolderListInCurrentFolder() or []
    if not folders:
        return "No folders in the current location."
    return "Folders:\n" + "\n".join(f"  - {f}" for f in folders)


@mcp.tool
@safe_resolve_call
def celavii_open_project_folder(name: str) -> str:
    """Navigate into a Project Manager folder."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.OpenFolder(name)
    return f"Opened folder '{name}'." if result else f"Failed to open folder '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_goto_root_folder() -> str:
    """Navigate to the root of the Project Manager."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.GotoRootFolder()
    return "Navigated to root folder." if result else "Failed to navigate to root folder."


@mcp.tool
@safe_resolve_call
def celavii_goto_parent_folder() -> str:
    """Navigate up one level in the Project Manager."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.GotoParentFolder()
    return "Navigated to parent folder." if result else "Already at root folder."


@mcp.tool
@safe_resolve_call
def celavii_create_project_folder(name: str) -> str:
    """Create a new folder in the current Project Manager location."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.CreateFolder(name)
    return f"Folder '{name}' created." if result else f"Failed to create folder '{name}'."


@mcp.tool
@safe_resolve_call
def celavii_delete_project_folder(name: str) -> str:
    """Delete a folder in the Project Manager. Must be empty."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.DeleteFolder(name)
    return f"Folder '{name}' deleted." if result else f"Failed to delete folder '{name}'."


# ---------------------------------------------------------------------------
# Database management
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_current_database() -> str:
    """Get the currently active database."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    db = pm.GetCurrentDatabase()
    return json.dumps(_ser(db), indent=2)


@mcp.tool
@safe_resolve_call
def celavii_list_databases() -> str:
    """List all available databases."""
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    dbs = pm.GetDatabaseList() or []
    return json.dumps([_ser(d) for d in dbs], indent=2)


@mcp.tool
@safe_resolve_call
def celavii_switch_database(db_name: str, db_type: str = "Disk") -> str:
    """Switch to a different database.

    Args:
        db_name: Name of the database.
        db_type: Database type — 'Disk', 'PostgreSQL', or 'Cloud'.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."
    pm = resolve.GetProjectManager()
    result = pm.SetCurrentDatabase({"DbType": db_type, "DbName": db_name})
    return f"Switched to database '{db_name}'." if result else f"Failed to switch to '{db_name}'."


# ---------------------------------------------------------------------------
# Project settings
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_get_project_setting(key: str = "") -> str:
    """Get a project setting by key, or all settings if key is empty.

    Common keys: timelineResolutionWidth, timelineResolutionHeight,
    timelineFrameRate, colorScienceMode, audioCaptureNumChannels
    """
    _, project, _ = _boilerplate()
    if key:
        value = project.GetSetting(key)
        return json.dumps({key: value}, indent=2)
    # Get all settings — pass empty string
    settings = project.GetSetting("")
    return json.dumps(_ser(settings), indent=2)


@mcp.tool
@safe_resolve_call
def celavii_set_project_setting(key: str, value: str) -> str:
    """Set a project setting.

    Args:
        key: Setting key (e.g. 'timelineResolutionWidth').
        value: Setting value as string.
    """
    _, project, _ = _boilerplate()
    result = project.SetSetting(key, value)
    return f"Set {key} = {value}." if result else f"Failed to set {key}. Check key name and value."
