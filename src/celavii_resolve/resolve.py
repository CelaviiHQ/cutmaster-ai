"""DaVinci Resolve connection management and shared helpers.

Provides lazy connection with auto-launch, the ``_boilerplate()`` helper for
quick access to the core API objects, and utility functions for navigating the
media pool, finding clips, and sanitising paths.
"""

import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger("celavii-resolve")

# ---------------------------------------------------------------------------
# Globals — populated lazily on first tool call
# ---------------------------------------------------------------------------
_resolve = None
_dvr_script = None
_module_loaded = False


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _system() -> str:
    """Return normalised platform name: 'darwin', 'windows', or 'linux'."""
    return platform.system().lower()


def _resolve_module_path() -> str | None:
    """Return the Resolve scripting Modules directory for the current OS.

    Checks the ``RESOLVE_SCRIPT_API`` env-var first, then falls back to the
    standard installation path per platform.
    """
    override = os.getenv("RESOLVE_SCRIPT_API")
    if override:
        modules = os.path.join(override, "Modules")
        if os.path.isdir(modules):
            return modules
        if os.path.isdir(override):
            return override

    paths: dict[str, list[str]] = {
        "darwin": [
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting/Modules",
            os.path.expanduser(
                "~/Library/Application Support/Blackmagic Design/"
                "DaVinci Resolve/Developer/Scripting/Modules"
            ),
        ],
        "windows": [
            os.path.expandvars(
                r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve"
                r"\Support\Developer\Scripting\Modules"
            ),
        ],
        "linux": [
            "/opt/resolve/Developer/Scripting/Modules",
            "/opt/resolve/libs/Fusion/Developer/Scripting/Modules",
        ],
    }

    for p in paths.get(_system(), paths["linux"]):
        if os.path.isdir(p):
            return p

    return None


def _resolve_lib_path() -> str | None:
    """Return the path to fusionscript shared library."""
    libs: dict[str, list[str]] = {
        "darwin": [
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/"
            "Contents/Libraries/Fusion/fusionscript.so",
        ],
        "windows": [
            os.path.expandvars(
                r"%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
            ),
        ],
        "linux": [
            "/opt/resolve/libs/Fusion/fusionscript.so",
        ],
    }
    for p in libs.get(_system(), libs["linux"]):
        if os.path.isfile(p):
            return p
    return None


def _resolve_app_path() -> str | None:
    """Return the Resolve application path for auto-launch."""
    apps: dict[str, str] = {
        "darwin": "/Applications/DaVinci Resolve/DaVinci Resolve.app",
        "windows": r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
        "linux": "/opt/resolve/bin/resolve",
    }
    p = apps.get(_system())
    if p and os.path.exists(p):
        return p
    return None


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------


def _load_module():
    """Import DaVinciResolveScript into the global ``_dvr_script``."""
    global _dvr_script, _module_loaded

    if _module_loaded:
        return

    _module_loaded = True

    mod_path = _resolve_module_path()
    if mod_path is None:
        log.error(
            "Could not find DaVinci Resolve Scripting Modules. "
            "Set RESOLVE_SCRIPT_API or install Resolve Studio."
        )
        return

    if mod_path not in sys.path:
        sys.path.insert(0, mod_path)

    # Also set env vars that Resolve's module may look for
    lib_path = _resolve_lib_path()
    if lib_path:
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib_path)

    api_dir = os.path.dirname(mod_path)
    os.environ.setdefault("RESOLVE_SCRIPT_API", api_dir)

    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-untyped]

        _dvr_script = dvr
        log.info("DaVinciResolveScript module loaded from %s", mod_path)
    except ImportError as exc:
        log.error("Cannot import DaVinciResolveScript: %s", exc)
        _dvr_script = None


# ---------------------------------------------------------------------------
# Connection management — lazy + auto-launch
# ---------------------------------------------------------------------------


def _try_connect():
    """Attempt a single connection to a running Resolve instance.

    Returns the Resolve object or ``None``.
    """
    global _resolve

    _load_module()
    if _dvr_script is None:
        return None

    try:
        _resolve = _dvr_script.scriptapp("Resolve")
        if _resolve:
            try:
                name = _resolve.GetProductName()
                ver = _resolve.GetVersionString()
                log.info("Connected to %s %s", name, ver)
            except Exception:
                log.info("Connected to DaVinci Resolve")
        return _resolve
    except Exception as exc:
        log.error("Connection error: %s", exc)
        _resolve = None
        return None


def _launch_resolve() -> bool:
    """Launch DaVinci Resolve and poll until it responds (up to 60 s)."""
    app = _resolve_app_path()
    if not app:
        log.error("DaVinci Resolve application not found — cannot auto-launch")
        return False

    sys_name = _system()
    try:
        if sys_name == "darwin":
            subprocess.Popen(["open", app])
        else:
            subprocess.Popen([app])
    except Exception as exc:
        log.error("Failed to launch Resolve: %s", exc)
        return False

    log.info("Launched DaVinci Resolve, waiting for it to respond...")

    for i in range(30):  # 30 x 2 s = 60 s
        time.sleep(2)
        if _try_connect():
            log.info("Resolve responded after %d s", (i + 1) * 2)
            return True

    log.warning("Resolve did not respond within 60 s after launch")
    return False


def get_resolve():
    """Return the Resolve scripting object, connecting lazily.

    On first call (or after losing connection) the function:

    1. Tries to connect to a running instance.
    2. If that fails, auto-launches Resolve and waits up to 60 s.

    Returns ``None`` if all attempts fail.
    """
    global _resolve

    if _resolve is not None:
        return _resolve

    # Try connecting to an already-running Resolve
    if _try_connect():
        return _resolve

    # Not running — launch automatically
    log.info("Resolve not running, attempting auto-launch...")
    _launch_resolve()
    return _resolve


# ---------------------------------------------------------------------------
# Boilerplate — quick access to core API objects
# ---------------------------------------------------------------------------


def _boilerplate():
    """Return ``(resolve, project, media_pool)`` or raise ``ValueError``.

    Every tool that needs a project should call this first.  The raised
    ``ValueError`` is caught by ``@safe_resolve_call`` and returned as an
    error string to the LLM.
    """
    resolve = get_resolve()
    if not resolve:
        raise ValueError("Error: DaVinci Resolve is not running or not reachable.")

    pm = resolve.GetProjectManager()
    if not pm:
        raise ValueError("Error: Could not access the Project Manager.")

    project = pm.GetCurrentProject()
    if not project:
        raise ValueError("Error: No project is currently open in Resolve.")

    media_pool = project.GetMediaPool()
    return resolve, project, media_pool


# ---------------------------------------------------------------------------
# Studio detection
# ---------------------------------------------------------------------------


def is_studio() -> bool:
    """Return ``True`` if the connected Resolve instance is Studio edition."""
    resolve = get_resolve()
    if not resolve:
        return False
    try:
        product = resolve.GetProductName()
        if product and "Studio" in product:
            return True
    except (AttributeError, TypeError):
        pass
    try:
        ver = resolve.GetVersionString()
        if ver and "Studio" in ver:
            return True
    except (AttributeError, TypeError):
        pass
    return False


def _require_studio(feature_name: str) -> None:
    """Raise ``ValueError`` if Resolve is not the Studio edition."""
    if not is_studio():
        raise ValueError(
            f"'{feature_name}' requires DaVinci Resolve Studio. "
            f"The free edition does not support this feature."
        )


# ---------------------------------------------------------------------------
# Media pool helpers
# ---------------------------------------------------------------------------


def _collect_clips_recursive(folder) -> dict:
    """Walk the media pool depth-first, returning a flat dict mapping
    both the full filename and the stem (without extension) to the clip
    object.  This allows lookup by either form.
    """
    result: dict[str, object] = {}
    for clip in folder.GetClipList() or []:
        try:
            name = clip.GetName()
        except Exception:
            continue
        result[name] = clip
        result[Path(name).stem] = clip
    for sub in folder.GetSubFolderList() or []:
        result.update(_collect_clips_recursive(sub))
    return result


def _find_clip(folder, clip_id: str):
    """Recursively search the folder tree for a clip by unique ID."""
    for clip in folder.GetClipList() or []:
        try:
            if clip.GetUniqueId() == clip_id:
                return clip
        except Exception:
            continue
    for sub in folder.GetSubFolderList() or []:
        found = _find_clip(sub, clip_id)
        if found:
            return found
    return None


def _find_clip_by_name(media_pool, clip_name: str):
    """Find a clip in the media pool by name (stem or full filename)."""
    pool_clips = _collect_clips_recursive(media_pool.GetRootFolder())
    return pool_clips.get(clip_name) or pool_clips.get(Path(clip_name).stem)


def _find_bin(root_folder, bin_path: str):
    """Locate a media pool folder by name or ``/``-separated path.

    Supports both ``"SubFolder"`` (recursive name search) and
    ``"Master/Sub/Deep"`` (path-based navigation).
    """
    if "/" in bin_path:
        current = root_folder
        for seg in (s for s in bin_path.split("/") if s):
            found = next(
                (sub for sub in (current.GetSubFolderList() or []) if sub.GetName() == seg),
                None,
            )
            if found is None:
                return None
            current = found
        return current

    # Recursive name search
    def _search(folder):
        if folder.GetName() == bin_path:
            return folder
        for sub in folder.GetSubFolderList() or []:
            result = _search(sub)
            if result is not None:
                return result
        return None

    return _search(root_folder)


def _enumerate_bins(folder, prefix: str = "") -> list[dict]:
    """Recursively list all bins as ``{"path": ..., "clip_count": ...}``."""
    name = folder.GetName()
    path = f"{prefix}/{name}" if prefix else name
    clips = folder.GetClipList() or []
    entries: list[dict] = [{"path": path, "clip_count": len(clips)}]
    for sub in folder.GetSubFolderList() or []:
        entries.extend(_enumerate_bins(sub, path))
    return entries


def _navigate_folder(media_pool, path: str):
    """Navigate to a folder by ``/``-separated path like ``Master/Sub``.

    Returns the folder object or ``None``.
    """
    root = media_pool.GetRootFolder()
    if not path or path in ("Master", "/", ""):
        return root

    parts = path.strip("/").split("/")
    if parts and parts[0] == "Master":
        parts = parts[1:]

    current = root
    for part in parts:
        found = False
        for sub in current.GetSubFolderList() or []:
            if sub.GetName() == part:
                current = sub
                found = True
                break
        if not found:
            return None
    return current


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _resolve_safe_dir(path: str) -> str:
    """Redirect sandbox / temp paths that Resolve cannot access.

    On macOS (``/var/folders``), Linux (``/tmp``), and Windows
    (``AppData\\Local\\Temp``) the OS-level sandbox directories are
    invisible to Resolve.  This function replaces them with
    ``~/Documents/resolve-exports``.
    """
    system_temp = tempfile.gettempdir()
    _is_sandbox = False

    sys_name = _system()
    if sys_name == "darwin":
        _is_sandbox = path.startswith("/var/") or path.startswith("/private/var/")
    elif sys_name == "linux":
        _is_sandbox = path.startswith("/tmp") or path.startswith("/var/tmp")
    elif sys_name == "windows":
        try:
            _is_sandbox = os.path.commonpath(
                [os.path.abspath(path), os.path.abspath(system_temp)]
            ) == os.path.abspath(system_temp)
        except ValueError:
            _is_sandbox = False

    if _is_sandbox:
        safe = os.path.join(os.path.expanduser("~"), "Documents", "resolve-exports")
        os.makedirs(safe, exist_ok=True)
        return safe
    return path


def _validate_path_within(path: str, allowed_root: str) -> bool:
    """Return ``True`` if *path* resolves within *allowed_root*.

    Used to prevent path-traversal attacks on preset import/export.
    """
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(allowed_root)
        return real_path.startswith(real_root + os.sep) or real_path == real_root
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _ser(obj):
    """Recursively convert Resolve API objects to JSON-safe values."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _ser(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(v) for v in obj]
    return str(obj)
