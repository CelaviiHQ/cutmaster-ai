"""Write a pre-execute project snapshot as a ``.drp`` file.

Phase 0 (v0_saveas.py) caught that ``project.SaveAs`` does not exist on the
Resolve Project object. The real snapshot path is
``ProjectManager.ExportProject(name, path, False)``.

Snapshots default to ``~/Documents/celavii-snapshots/``. Restore flow: user
re-imports the ``.drp`` via the Project Manager UI (or a future tool).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate

DEFAULT_SNAPSHOT_DIR = Path.home() / "Documents" / "celavii-snapshots"


def snapshot_project(
    resolve,
    project,
    snapshot_dir: Path | None = None,
    label: str = "pre_cutmaster",
) -> dict:
    """Flush + export the current project to a ``.drp`` at ``snapshot_dir``.

    Returns ``{"path": str, "size_kb": float, "project": str, "label": str}``.

    Raises:
        RuntimeError: SaveProject or ExportProject returned False, or the
            file does not exist after the call.
    """
    pm = resolve.GetProjectManager()
    orig_name = project.GetName()

    if not pm.SaveProject():
        raise RuntimeError("SaveProject() returned False before snapshot.")

    target_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = target_dir / f"{orig_name}_{label}_{ts}.drp"

    if not pm.ExportProject(orig_name, str(out_path), False):
        raise RuntimeError(f"ExportProject returned False for '{orig_name}'.")
    if not out_path.exists():
        raise RuntimeError(f"ExportProject reported success but no file at {out_path}.")

    return {
        "path": str(out_path),
        "size_kb": out_path.stat().st_size / 1024,
        "project": orig_name,
        "label": label,
    }


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_snapshot_project(snapshot_dir: str = "", label: str = "pre_cutmaster") -> str:
    """Export the current project to a .drp snapshot (non-destructive backup).

    Use before any destructive or large-scale timeline operation. If the user
    hates the result, they can re-import the .drp via Resolve's Project
    Manager.

    Args:
        snapshot_dir: Destination directory. Defaults to
            ``~/Documents/celavii-snapshots``.
        label: Short label baked into the filename (default ``pre_cutmaster``).

    Returns a JSON payload with ``path``, ``size_kb``, ``project``, ``label``.
    """
    resolve, project, _ = _boilerplate()
    sdir = Path(snapshot_dir).expanduser() if snapshot_dir else None
    result = snapshot_project(resolve, project, sdir, label)
    return json.dumps(result)
