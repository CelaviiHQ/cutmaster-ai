"""LUT Library — registry, installer, and scanner for camera manufacturer LUTs.

Manages a library of camera-specific LUTs that aren't bundled with DaVinci Resolve.
Downloads official manufacturer LUTs on demand and installs them where Resolve
and the 6-node grading workflow can find them automatically.

LUTs are installed to:
  ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Celavii/{vendor}/

The registry maps cameras → official LUT download URLs → local install paths.
Once installed, `celavii_setup_log_grade(camera="osmo pocket 3")` picks them up
automatically — no more "apply CST manually" messages.
"""

import json
import logging
import os
import platform
import shutil
import zipfile
from pathlib import Path

from ..config import mcp
from ..errors import safe_resolve_call

log = logging.getLogger("celavii-resolve")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SYSTEM = platform.system()

if _SYSTEM == "Darwin":
    _USER_LUT_BASE = (
        Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
    )
elif _SYSTEM == "Windows":
    _USER_LUT_BASE = (
        Path(os.environ.get("APPDATA", "")) / "Blackmagic Design/DaVinci Resolve/Support/LUT"
    )
else:  # Linux
    _USER_LUT_BASE = Path.home() / ".local/share/DaVinciResolve/LUT"

CELAVII_LUT_DIR = _USER_LUT_BASE / "Celavii"

# ---------------------------------------------------------------------------
# LUT Registry — camera → LUT metadata
# ---------------------------------------------------------------------------

# Each entry defines:
#   vendor:      Manufacturer name (used as subfolder)
#   cameras:     List of camera models this applies to
#   log_format:  Name of the log profile
#   lut_files:   List of LUT files that should exist after install
#   cst_lut:     Which file from lut_files to use as the default CST
#   download_url: Direct download URL (zip or .cube). None if manual download needed.
#   download_page: Web page where user can manually download (fallback)
#   install_notes: Human-readable instructions
#   resolve_cst:  Resolve OFX Color Space Transform settings (if supported natively)

LUT_REGISTRY: dict[str, dict] = {
    # -----------------------------------------------------------------------
    # DJI — D-Log M (Osmo Pocket 3, Mini 3 Pro, Mini 4 Pro, Air 3, Mavic 3)
    # -----------------------------------------------------------------------
    "dji-dlog-m": {
        "vendor": "DJI",
        "cameras": [
            "Osmo Pocket 3",
            "Osmo Action 5 Pro",
            "Mini 3 Pro",
            "Mini 4 Pro",
            "Air 3",
            "Mavic 3",
            "Mavic 3 Pro",
            "Avata 2",
        ],
        "log_format": "D-Log M",
        "lut_files": [
            "DJI_DLogM_to_Rec709.cube",
        ],
        "cst_lut": "DJI_DLogM_to_Rec709.cube",
        "download_url": None,  # DJI requires manual download from their site
        "download_page": "https://www.dji.com/downloads/products/mini-4-pro",
        "install_notes": (
            "1. Go to https://www.dji.com/downloads and find your camera model\n"
            "2. Download the D-Log M to Rec.709 LUT (usually a .zip file)\n"
            "3. Run: celavii_install_lut_file('/path/to/downloaded/DJI_DLogM_to_Rec709.cube', 'dji-dlog-m')\n"
            "   Or unzip and copy the .cube file to the Celavii/DJI/ folder."
        ),
        "resolve_cst": {
            "input_color_space": "DJI D-Gamut",
            "input_gamma": "D-Log M",
        },
    },
    # -----------------------------------------------------------------------
    # DJI — D-Log (legacy: Phantom 4 Pro, Inspire 2 / X5S / X7)
    # Note: These already have built-in Resolve LUTs, but users may want
    # the official DJI versions which are slightly different.
    # -----------------------------------------------------------------------
    "dji-dlog": {
        "vendor": "DJI",
        "cameras": ["Inspire 2", "X5S", "X7", "Phantom 4 Pro V2"],
        "log_format": "D-Log",
        "lut_files": [
            "DJI_DLog_to_Rec709.cube",
        ],
        "cst_lut": "DJI_DLog_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://www.dji.com/downloads/products/zenmuse-x7",
        "install_notes": (
            "DJI legacy D-Log cameras (X7, Phantom 4 Pro) already have built-in\n"
            "Resolve LUTs. This entry is for installing the official DJI versions."
        ),
        "resolve_cst": {
            "input_color_space": "DJI D-Gamut",
            "input_gamma": "D-Log",
        },
    },
    # -----------------------------------------------------------------------
    # Insta360
    # -----------------------------------------------------------------------
    "insta360": {
        "vendor": "Insta360",
        "cameras": [
            "X5",
            "X4",
            "X3",
            "X2",
            "Ace Pro 2",
            "Ace Pro",
            "Ace",
            "GO 3S",
            "GO 3",
            "ONE RS",
        ],
        "log_format": "Insta360 Log",
        "lut_files": [
            "Insta360_Log_to_Rec709.cube",
        ],
        "cst_lut": "Insta360_Log_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://onlinemanual.insta360.com/x4/en-us/faq/video/lut",
        "install_notes": (
            "1. Go to https://onlinemanual.insta360.com/x4/en-us/faq/video/lut\n"
            "   or search 'Insta360 LUT download' for your specific model\n"
            "2. Download the LUT pack (usually a .zip)\n"
            "3. Run: celavii_install_lut_file('/path/to/Insta360_Log_to_Rec709.cube', 'insta360')"
        ),
        "resolve_cst": None,  # Not in Resolve's OFX CST
    },
    # -----------------------------------------------------------------------
    # GoPro
    # -----------------------------------------------------------------------
    "gopro": {
        "vendor": "GoPro",
        "cameras": ["Hero 13", "Hero 12", "Hero 11", "Hero 10", "Hero 9"],
        "log_format": "GP-Log / Protune Flat",
        "lut_files": [
            "GoPro_Protune_to_Rec709.cube",
        ],
        "cst_lut": "GoPro_Protune_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://community.gopro.com/s/article/GoPro-LUTs",
        "install_notes": (
            "1. Go to https://community.gopro.com/s/article/GoPro-LUTs\n"
            "2. Download the official GoPro LUT pack\n"
            "3. Run: celavii_install_lut_file('/path/to/GoPro_Protune_to_Rec709.cube', 'gopro')"
        ),
        "resolve_cst": {
            "input_color_space": "Rec.709",
            "input_gamma": "GoPro Protune Flat",
        },
    },
    # -----------------------------------------------------------------------
    # Apple iPhone (ProRes Log)
    # -----------------------------------------------------------------------
    "iphone-log": {
        "vendor": "Apple",
        "cameras": ["iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 15 Pro Max", "iPhone 15 Pro"],
        "log_format": "Apple Log",
        "lut_files": [
            "Apple_Log_to_Rec709.cube",
        ],
        "cst_lut": "Apple_Log_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://support.apple.com/en-us/108329",
        "install_notes": (
            "Apple Log is natively supported in Resolve's Color Space Transform OFX.\n"
            "You don't need a LUT file — just apply CST to node 5 and set:\n"
            "  Input Color Space: Apple Log\n"
            "  Input Gamma: Apple Log\n\n"
            "If you prefer a LUT: download from Apple Developer or search 'Apple Log LUT'."
        ),
        "resolve_cst": {
            "input_color_space": "Apple Log",
            "input_gamma": "Apple Log",
        },
    },
    # -----------------------------------------------------------------------
    # Canon (C-Log2 / C-Log3)
    # -----------------------------------------------------------------------
    "canon-clog3": {
        "vendor": "Canon",
        "cameras": ["R5 C", "R5 II", "C70", "C300 III", "C500 II"],
        "log_format": "Canon Log 3",
        "lut_files": [
            "Canon_CLog3_to_Rec709.cube",
        ],
        "cst_lut": "Canon_CLog3_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://www.usa.canon.com/support/p/cinema-eos-c70",
        "install_notes": (
            "Canon Log 3 is supported in Resolve's Color Space Transform OFX.\n"
            "Apply CST to node 5: Input Color Space: Cinema Gamut, Input Gamma: Canon Log 3.\n"
            "For a LUT file, download from Canon's support page for your camera model."
        ),
        "resolve_cst": {
            "input_color_space": "Cinema Gamut",
            "input_gamma": "Canon Log 3",
        },
    },
    "canon-clog2": {
        "vendor": "Canon",
        "cameras": ["C200", "C300 II", "1DX III"],
        "log_format": "Canon Log 2",
        "lut_files": [
            "Canon_CLog2_to_Rec709.cube",
        ],
        "cst_lut": "Canon_CLog2_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://www.usa.canon.com/support",
        "install_notes": (
            "Canon Log 2 is supported in Resolve's CST OFX.\n"
            "Apply CST to node 5: Input Color Space: Cinema Gamut, Input Gamma: Canon Log 2."
        ),
        "resolve_cst": {
            "input_color_space": "Cinema Gamut",
            "input_gamma": "Canon Log 2",
        },
    },
    # -----------------------------------------------------------------------
    # Nikon (N-Log)
    # -----------------------------------------------------------------------
    "nikon-nlog": {
        "vendor": "Nikon",
        "cameras": ["Z9", "Z8", "Z6 III", "Z5"],
        "log_format": "N-Log",
        "lut_files": [
            "Nikon_NLog_to_Rec709.cube",
        ],
        "cst_lut": "Nikon_NLog_to_Rec709.cube",
        "download_url": "https://downloadcenter.nikonimglib.com/en/download/fw/311.html",
        "download_page": "https://downloadcenter.nikonimglib.com/en/products/548/Z_8.html",
        "install_notes": (
            "Nikon provides official N-Log LUTs on their Download Center.\n"
            "1. Go to https://downloadcenter.nikonimglib.com and search for your camera\n"
            "2. Download the N-Log 3D LUT\n"
            "3. Run: celavii_install_lut_file('/path/to/NLog_to_Rec709.cube', 'nikon-nlog')"
        ),
        "resolve_cst": {
            "input_color_space": "Nikon N-Gamut",
            "input_gamma": "N-Log",
        },
    },
    # -----------------------------------------------------------------------
    # Fujifilm (F-Log / F-Log2)
    # -----------------------------------------------------------------------
    "fujifilm-flog2": {
        "vendor": "Fujifilm",
        "cameras": ["X-H2S", "X-H2", "X-T5", "GFX 100 II"],
        "log_format": "F-Log2",
        "lut_files": [
            "Fujifilm_FLog2_to_Rec709.cube",
        ],
        "cst_lut": "Fujifilm_FLog2_to_Rec709.cube",
        "download_url": None,
        "download_page": "https://fujifilm-x.com/en-us/support/lut/",
        "install_notes": (
            "Fujifilm provides official F-Log2 LUTs on their support site.\n"
            "1. Go to https://fujifilm-x.com/en-us/support/lut/\n"
            "2. Download the F-Log2 to Rec.709 LUT\n"
            "3. Run: celavii_install_lut_file('/path/to/FLog2_to_Rec709.cube', 'fujifilm-flog2')"
        ),
        "resolve_cst": {
            "input_color_space": "FujiFilm F-Gamut",
            "input_gamma": "F-Log2",
        },
    },
}

# Alias mapping for natural camera names → registry keys
LUT_REGISTRY_ALIASES: dict[str, str] = {
    # DJI D-Log M
    "osmo pocket 3": "dji-dlog-m",
    "osmo pocket": "dji-dlog-m",
    "osmo action 5": "dji-dlog-m",
    "dji mini 3 pro": "dji-dlog-m",
    "dji mini 4 pro": "dji-dlog-m",
    "dji mini 3": "dji-dlog-m",
    "dji mini 4": "dji-dlog-m",
    "dji air 3": "dji-dlog-m",
    "dji mavic 3": "dji-dlog-m",
    "mavic 3": "dji-dlog-m",
    "mavic 3 pro": "dji-dlog-m",
    "avata 2": "dji-dlog-m",
    "dlog-m": "dji-dlog-m",
    "dlogm": "dji-dlog-m",
    "d-log m": "dji-dlog-m",
    # Insta360
    "insta360 x5": "insta360",
    "insta360 x4": "insta360",
    "insta360 x3": "insta360",
    "insta360 x2": "insta360",
    "insta360 ace pro": "insta360",
    "insta360 ace pro 2": "insta360",
    "insta360 ace": "insta360",
    "insta360 go 3s": "insta360",
    "insta360 go 3": "insta360",
    "x5": "insta360",
    "x4": "insta360",
    "x3": "insta360",
    # GoPro
    "gopro hero 13": "gopro",
    "gopro hero 12": "gopro",
    "gopro hero 11": "gopro",
    "gopro hero": "gopro",
    "hero 13": "gopro",
    "hero 12": "gopro",
    "protune": "gopro",
    "gp-log": "gopro",
    # iPhone
    "iphone 16 pro max": "iphone-log",
    "iphone 16 pro": "iphone-log",
    "iphone 15 pro max": "iphone-log",
    "iphone 15 pro": "iphone-log",
    "iphone log": "iphone-log",
    "iphone prores": "iphone-log",
    "apple log": "iphone-log",
    # Canon
    "canon r5c": "canon-clog3",
    "canon r5 c": "canon-clog3",
    "canon r5 ii": "canon-clog3",
    "canon c70": "canon-clog3",
    "canon c300 iii": "canon-clog3",
    "canon c300": "canon-clog3",
    "clog3": "canon-clog3",
    "c-log3": "canon-clog3",
    "canon log 3": "canon-clog3",
    "clog2": "canon-clog2",
    "c-log2": "canon-clog2",
    "canon log 2": "canon-clog2",
    # Nikon
    "nikon z9": "nikon-nlog",
    "nikon z8": "nikon-nlog",
    "nikon z6 iii": "nikon-nlog",
    "nikon z6": "nikon-nlog",
    "nikon z5": "nikon-nlog",
    "n-log": "nikon-nlog",
    "nlog": "nikon-nlog",
    # Fujifilm
    "fujifilm x-h2s": "fujifilm-flog2",
    "fujifilm x-h2": "fujifilm-flog2",
    "fujifilm x-t5": "fujifilm-flog2",
    "x-h2s": "fujifilm-flog2",
    "x-h2": "fujifilm-flog2",
    "flog2": "fujifilm-flog2",
    "f-log2": "fujifilm-flog2",
    "fuji flog": "fujifilm-flog2",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_registry_key(camera: str) -> str | None:
    """Resolve a camera name to a registry key."""
    key = camera.lower().strip()
    if key in LUT_REGISTRY:
        return key
    if key in LUT_REGISTRY_ALIASES:
        return LUT_REGISTRY_ALIASES[key]
    # Partial match
    for alias, target in LUT_REGISTRY_ALIASES.items():
        if alias in key or key in alias:
            return target
    return None


def _get_vendor_dir(vendor: str) -> Path:
    """Get the install directory for a vendor's LUTs."""
    return CELAVII_LUT_DIR / vendor


def _get_installed_luts(registry_key: str) -> list[Path]:
    """Return list of installed LUT files for a registry entry."""
    entry = LUT_REGISTRY.get(registry_key)
    if not entry:
        return []
    vendor_dir = _get_vendor_dir(entry["vendor"])
    installed = []
    for lut_file in entry["lut_files"]:
        path = vendor_dir / lut_file
        if path.is_file():
            installed.append(path)
    return installed


def get_cst_lut_for_camera(camera: str) -> str | None:
    """Return the installed CST LUT path for a camera, or None.

    Used by celavii_setup_log_grade to auto-detect installed LUTs.
    """
    key = _resolve_registry_key(camera)
    if not key:
        return None
    entry = LUT_REGISTRY.get(key)
    if not entry:
        return None
    cst_file = entry.get("cst_lut")
    if not cst_file:
        return None
    path = _get_vendor_dir(entry["vendor"]) / cst_file
    if path.is_file():
        return str(path)
    return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool
@safe_resolve_call
def celavii_lut_library_status() -> str:
    """Show the status of the LUT library — what's installed and what's available.

    Lists every supported camera, whether its LUT is installed, and
    download instructions for missing LUTs.
    """
    entries = []
    for key, entry in LUT_REGISTRY.items():
        installed = _get_installed_luts(key)
        vendor_dir = _get_vendor_dir(entry["vendor"])

        entries.append(
            {
                "key": key,
                "vendor": entry["vendor"],
                "cameras": entry["cameras"],
                "log_format": entry["log_format"],
                "installed": len(installed) > 0,
                "installed_files": [str(p) for p in installed],
                "expected_files": [str(vendor_dir / f) for f in entry["lut_files"]],
                "has_resolve_cst": entry.get("resolve_cst") is not None,
                "download_page": entry.get("download_page", ""),
            }
        )

    installed_count = sum(1 for e in entries if e["installed"])
    total = len(entries)

    # Also scan for any extra .cube files in the Celavii LUT directory
    extra_luts = []
    if CELAVII_LUT_DIR.is_dir():
        for cube in CELAVII_LUT_DIR.rglob("*.cube"):
            rel = str(cube.relative_to(CELAVII_LUT_DIR))
            known = any(
                rel.endswith(f) for entry in LUT_REGISTRY.values() for f in entry["lut_files"]
            )
            if not known and "DECSFILM" not in rel:
                extra_luts.append(str(cube))

    return json.dumps(
        {
            "summary": f"{installed_count}/{total} camera LUT packs installed",
            "lut_directory": str(CELAVII_LUT_DIR),
            "cameras": entries,
            "extra_luts": extra_luts,
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_install_lut_file(
    file_path: str,
    camera: str,
) -> str:
    """Install a downloaded LUT file into the Celavii LUT library.

    Use this after downloading a manufacturer LUT. The file will be
    copied to the correct location and automatically picked up by
    celavii_setup_log_grade next time you use it.

    Args:
        file_path: Path to the .cube file (or .zip containing .cube files).
        camera: Camera name or registry key (e.g. 'osmo pocket 3', 'insta360',
                'gopro', 'iphone', 'canon c70', 'nikon z8', 'fuji x-h2s').
    """
    key = _resolve_registry_key(camera)
    if not key:
        return (
            f"Camera '{camera}' not found in registry. "
            f"Available: {', '.join(sorted(LUT_REGISTRY.keys()))}"
        )

    entry = LUT_REGISTRY[key]
    vendor_dir = _get_vendor_dir(entry["vendor"])
    vendor_dir.mkdir(parents=True, exist_ok=True)

    source = Path(file_path)
    if not source.exists():
        return f"Error: File not found: {file_path}"

    installed = []

    if source.suffix.lower() == ".zip":
        # Extract .cube files from zip
        try:
            with zipfile.ZipFile(source, "r") as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".cube") and not name.startswith("__MACOSX"):
                        # Extract to vendor directory
                        cube_name = Path(name).name
                        target = vendor_dir / cube_name
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        installed.append(str(target))
        except zipfile.BadZipFile:
            return f"Error: '{file_path}' is not a valid zip file."

    elif source.suffix.lower() == ".cube":
        # Copy single .cube file
        # Use the expected filename from the registry if it's the CST LUT
        target_name = entry.get("cst_lut", source.name)
        target = vendor_dir / target_name
        shutil.copy2(source, target)
        installed.append(str(target))

    else:
        return f"Error: Unsupported file type '{source.suffix}'. Expected .cube or .zip."

    if not installed:
        return "No .cube files found in the archive."

    # Verify the CST LUT is now available
    cst_path = get_cst_lut_for_camera(camera)

    return json.dumps(
        {
            "installed": installed,
            "vendor": entry["vendor"],
            "camera": camera,
            "registry_key": key,
            "cst_lut_ready": cst_path is not None,
            "cst_lut_path": cst_path,
            "next": (
                f"Your LUT is installed! Now run:\n"
                f"  celavii_setup_log_grade(camera='{camera}')\n"
                f"and the CST will be applied automatically to node 5."
            ),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_get_lut_install_guide(camera: str) -> str:
    """Get step-by-step instructions for installing the LUT for a specific camera.

    Tells you exactly where to download it, what file to look for,
    and how to install it so the 6-node workflow picks it up automatically.

    Args:
        camera: Camera name (e.g. 'osmo pocket 3', 'insta360 x5', 'gopro hero 12').
    """
    key = _resolve_registry_key(camera)
    if not key:
        return f"Camera '{camera}' not found in the LUT registry. Supported cameras:\n" + "\n".join(
            f"  - {k}: {', '.join(v['cameras'])}" for k, v in LUT_REGISTRY.items()
        )

    entry = LUT_REGISTRY[key]
    installed = _get_installed_luts(key)
    vendor_dir = _get_vendor_dir(entry["vendor"])

    guide = {
        "camera": camera,
        "registry_key": key,
        "vendor": entry["vendor"],
        "log_format": entry["log_format"],
        "supported_cameras": entry["cameras"],
        "already_installed": len(installed) > 0,
    }

    if installed:
        guide["status"] = "Already installed and ready to use!"
        guide["installed_files"] = [str(p) for p in installed]
        guide["usage"] = f"celavii_setup_log_grade(camera='{camera}')"
    else:
        guide["status"] = "Not installed yet"
        guide["install_directory"] = str(vendor_dir)

        # Provide two options: OFX CST (if supported) or LUT file
        options = []

        if entry.get("resolve_cst"):
            cst = entry["resolve_cst"]
            options.append(
                {
                    "option": "A — Use Resolve's built-in Color Space Transform (no download needed)",
                    "steps": [
                        "In the Color page, select node 5 (CST)",
                        "Open Effects panel (top-left icon)",
                        "Search for 'Color Space Transform'",
                        "Drag it onto node 5",
                        f"Set Input Color Space: {cst['input_color_space']}",
                        f"Set Input Gamma: {cst['input_gamma']}",
                        "Leave Output as Rec.709 / Gamma 2.4",
                    ],
                }
            )

        options.append(
            {
                "option": "B — Download and install the manufacturer LUT",
                "steps": entry["install_notes"].split("\n"),
                "download_page": entry.get("download_page", ""),
                "after_download": (
                    f"Run: celavii_install_lut_file('/path/to/your_downloaded_file.cube', '{camera}')\n"
                    f"Then: celavii_setup_log_grade(camera='{camera}') will apply it automatically."
                ),
            }
        )

        guide["options"] = options

    return json.dumps(guide, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_scan_lut_folder() -> str:
    """Scan the Resolve LUT folders and show all installed LUTs.

    Checks both the system LUT folder and the Celavii user LUT folder.
    Useful for finding LUTs you've already installed manually.
    """
    results = {
        "celavii_lut_dir": str(CELAVII_LUT_DIR),
        "celavii_luts": [],
        "system_lut_dir": str(_USER_LUT_BASE),
        "system_vendors": {},
    }

    # Scan Celavii directory
    if CELAVII_LUT_DIR.is_dir():
        for cube in sorted(CELAVII_LUT_DIR.rglob("*.cube")):
            rel = str(cube.relative_to(CELAVII_LUT_DIR))
            size_kb = cube.stat().st_size / 1024
            results["celavii_luts"].append(
                {
                    "path": rel,
                    "size_kb": round(size_kb, 1),
                }
            )

    # Scan system LUT directory by vendor
    if _USER_LUT_BASE.is_dir():
        for vendor_dir in sorted(_USER_LUT_BASE.iterdir()):
            if vendor_dir.is_dir() and vendor_dir.name != "Celavii":
                cubes = sorted(vendor_dir.rglob("*.cube"))
                dats = sorted(vendor_dir.rglob("*.dat"))
                files = cubes + dats
                if files:
                    results["system_vendors"][vendor_dir.name] = [
                        str(f.relative_to(_USER_LUT_BASE)) for f in files[:20]
                    ]

    results["total_celavii"] = len(results["celavii_luts"])
    results["total_system_vendors"] = len(results["system_vendors"])

    return json.dumps(results, indent=2)
