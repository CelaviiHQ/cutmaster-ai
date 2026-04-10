"""Grade workflow — apply LUTs, adjust CDL, copy grades, and save stills.

Compound tools for common color grading workflows.
"""

import json
import os

from ..config import mcp
from ..errors import safe_resolve_call
from ..lut_registry import get_cst_lut_for_camera
from ..resolve import _boilerplate

# ---------------------------------------------------------------------------
# Camera format → built-in Resolve CST LUT mapping
# ---------------------------------------------------------------------------

_LUT_BASE = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"

CAMERA_CST_LUTS: dict[str, str] = {
    # Sony
    "sony-slog3": f"{_LUT_BASE}/Sony/SLog3SGamut3.CineToLC-709TypeA.cube",
    "sony-slog3-lc709": f"{_LUT_BASE}/Sony/SLog3SGamut3.CineToLC-709.cube",
    "sony-slog3-slog2": f"{_LUT_BASE}/Sony/SLog3SGamut3.CineToSLog2-709.cube",
    "sony-slog3-cine709": f"{_LUT_BASE}/Sony/SLog3SGamut3.CineToCine+709.cube",
    # ARRI
    "arri-logc": f"{_LUT_BASE}/Arri/Arri Alexa LogC to Rec709.dat",
    # Blackmagic
    "braw-4k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic 4K Film to Rec709.cube",
    "braw-46k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic 4.6K Film to Rec709.cube",
    "braw-pocket4k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic Pocket 4K Film to Extended Video v4.cube",
    "braw-pocket6k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic Pocket 6K Film to Extended Video v4.cube",
    "braw-gen5": f"{_LUT_BASE}/Blackmagic Design/Blackmagic Gen 5 Film to Extended Video.cube",
    # RED
    "red-log3g10": f"{_LUT_BASE}/RED/RWG_Log3G10_to_REC709_BT1886_with_LOW_CONTRAST_and_R_3_Soft_size_33.cube",
    # DJI (older cameras with standard D-Log)
    "dji-dlog-phantom4": f"{_LUT_BASE}/DJI/DJI_Phantom4_DLOG2Rec709.cube",
    "dji-dlog-x7": f"{_LUT_BASE}/DJI/DJI_X7_DLOG2Rec709.cube",
    # Panasonic
    "panasonic-vlog": f"{_LUT_BASE}/Panasonic/V-Log to V-709.cube",
    # Olympus
    "olympus-omlog400": f"{_LUT_BASE}/Olympus/Olympus OM-Log400_to_BT.709_v1.0.cube",
    # Samsung
    "samsung-log": f"{_LUT_BASE}/Samsung/Samsung Log to Rec709.cube",
}

# Cameras that require manual CST (no built-in Resolve LUT exists for them).
# These cameras use newer/proprietary log formats not yet in Resolve's library.
# For these, users should apply the Color Space Transform OFX to node 5 manually,
# or install manufacturer LUTs and pass the path via cst_lut_path.
CAMERA_MANUAL_CST: dict[str, dict] = {
    # DJI D-Log M — newer format used by Osmo Pocket 3, Mini 3 Pro, Mini 4 Pro,
    # Air 3, Mavic 3, Avata 2. NOT the same as D-Log.
    "dji-dlog-m": {
        "cameras": ["Osmo Pocket 3", "Mini 3 Pro", "Mini 4 Pro", "Air 3", "Mavic 3", "Avata 2"],
        "resolve_cst": {
            "input_color_space": "DJI D-Gamut",
            "input_gamma": "D-Log M",
        },
        "lut_url": "https://www.dji.com/downloads/video/D-Log-M-LUT",
        "note": (
            "D-Log M is supported in Resolve's Color Space Transform OFX (Resolve 18.5+). "
            "In node 5: Effects > Color Space Transform > Input: DJI D-Gamut / D-Log M. "
            "Or download the official DJI D-Log M LUTs and pass cst_lut_path."
        ),
    },
    # Insta360 — uses a proprietary log profile. No Resolve CST OFX support.
    "insta360": {
        "cameras": ["X4", "X3", "X2", "Ace Pro", "Ace", "GO 3", "ONE RS"],
        "resolve_cst": None,
        "lut_url": "https://www.insta360.com/download/insta360-x4",
        "note": (
            "Insta360 Log is not in Resolve's Color Space Transform library. "
            "Download the official Insta360 LUT pack from insta360.com, "
            "install it to ~/Library/.../DaVinci Resolve/LUT/, "
            "then pass the path as cst_lut_path."
        ),
    },
    # GoPro — Protune is a mild log-like profile, not a true log format.
    "gopro": {
        "cameras": ["GoPro Hero 12/11/10/9/8 (Protune flat)"],
        "resolve_cst": {
            "input_color_space": "Rec.709",
            "input_gamma": "GoPro Protune Flat",
        },
        "lut_url": "https://community.gopro.com/s/article/How-to-Download-GoPro-LUTs",
        "note": (
            "GoPro Protune Flat is supported in Resolve's CST OFX. "
            "Alternatively download official GoPro LUTs from the GoPro Community Hub."
        ),
    },
    # iPhone ProRes Log (iPhone 15 Pro, 16 Pro)
    "iphone-log": {
        "cameras": ["iPhone 15 Pro", "iPhone 16 Pro"],
        "resolve_cst": {
            "input_color_space": "Apple Log",
            "input_gamma": "Apple Log",
        },
        "lut_url": "https://support.apple.com/downloads/luts",
        "note": (
            "Apple Log is supported in Resolve's Color Space Transform OFX (Resolve 18+). "
            "In node 5: Effects > Color Space Transform > Input Color Space: Apple Log. "
            "Or use Apple's official LUTs from developer.apple.com/download/all/."
        ),
    },
}

# Alias expansions so users can type natural names
_CAMERA_ALIASES: dict[str, str] = {
    # Sony
    "slog3": "sony-slog3",
    "s-log3": "sony-slog3",
    "sony fx3": "sony-slog3",
    "sony fx6": "sony-slog3",
    "sony fx9": "sony-slog3",
    "sony fx30": "sony-slog3",
    "sony a7": "sony-slog3",
    "sony a7s": "sony-slog3",
    "sony a1": "sony-slog3",
    "sony zv-e1": "sony-slog3",
    # ARRI
    "arri alexa": "arri-logc",
    "logc": "arri-logc",
    "log-c": "arri-logc",
    "alexa": "arri-logc",
    # Blackmagic
    "blackmagic": "braw-4k",
    "bmpcc4k": "braw-pocket4k",
    "bmpcc6k": "braw-pocket6k",
    "pocket 4k": "braw-pocket4k",
    "pocket 6k": "braw-pocket6k",
    # RED
    "red": "red-log3g10",
    "red komodo": "red-log3g10",
    "red monstro": "red-log3g10",
    # DJI legacy D-Log
    "dji": "dji-dlog-phantom4",
    "dji phantom": "dji-dlog-phantom4",
    "dji phantom4": "dji-dlog-phantom4",
    "dji x7": "dji-dlog-x7",
    # DJI D-Log M (manual CST)
    "dji-dlogm": "dji-dlog-m",
    "dlog-m": "dji-dlog-m",
    "dlogm": "dji-dlog-m",
    "osmo pocket 3": "dji-dlog-m",
    "osmo pocket": "dji-dlog-m",
    "dji mini 3": "dji-dlog-m",
    "dji mini 4": "dji-dlog-m",
    "dji mini3": "dji-dlog-m",
    "dji mini4": "dji-dlog-m",
    "dji air 3": "dji-dlog-m",
    "dji mavic 3": "dji-dlog-m",
    "mavic 3": "dji-dlog-m",
    "avata 2": "dji-dlog-m",
    # Insta360
    "insta360 x4": "insta360",
    "insta360 x3": "insta360",
    "insta360 x2": "insta360",
    "insta360 ace": "insta360",
    "insta360 go": "insta360",
    "x4": "insta360",
    "x3": "insta360",
    # GoPro
    "gopro hero": "gopro",
    "protune": "gopro",
    # iPhone
    "iphone": "iphone-log",
    "apple log": "iphone-log",
    "iphone 15 pro": "iphone-log",
    "iphone 16 pro": "iphone-log",
    # Panasonic
    "vlog": "panasonic-vlog",
    "v-log": "panasonic-vlog",
    "lumix": "panasonic-vlog",
    "gh5": "panasonic-vlog",
    "gh6": "panasonic-vlog",
    "s5": "panasonic-vlog",
    # Olympus / OM System
    "olympus": "olympus-omlog400",
    "om system": "olympus-omlog400",
    "omlog": "olympus-omlog400",
    # Samsung
    "samsung": "samsung-log",
    "samsung log": "samsung-log",
}

# Film look LUTs (node 6 defaults when no custom LUT provided)
FILM_LOOK_LUTS: dict[str, str] = {
    "kodak2383": f"{_LUT_BASE}/Film Looks/Rec709 Kodak 2383 D65.cube",
    "fuji3513-d55": f"{_LUT_BASE}/Film Looks/Rec709 Fujifilm 3513DI D55.cube",
    "fuji3513-d60": f"{_LUT_BASE}/Film Looks/Rec709 Fujifilm 3513DI D60.cube",
    "fuji3513-d65": f"{_LUT_BASE}/Film Looks/Rec709 Fujifilm 3513DI D65.cube",
}

# DECSFILM custom LUT (installed to user folder)
_USER_LUT_BASE = os.path.expanduser(
    "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
)
DECSFILM_LUT = f"{_USER_LUT_BASE}/Celavii/DECSFILM.cube"

# 6-node structure labels
_NODE_LABELS = ["WB", "EXP", "SAT", "CURVES", "CST", "LUT"]


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
    has_cdl = any(
        [
            slope_r != 1.0,
            slope_g != 1.0,
            slope_b != 1.0,
            offset_r != 0.0,
            offset_g != 0.0,
            offset_b != 0.0,
            power_r != 1.0,
            power_g != 1.0,
            power_b != 1.0,
            saturation != 1.0,
        ]
    )
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

    return json.dumps(
        {
            "clip": item.GetName(),
            "actions": actions,
        },
        indent=2,
    )


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

    return json.dumps(
        {
            "lut": lut_path,
            "applied": applied,
            "failed": failed,
            "total": len(targets),
        },
        indent=2,
    )


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
    return json.dumps(
        {
            "source": source.GetName(),
            "copied_to": len(targets),
            "success": bool(result),
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Log footage 6-node grading workflow
# ---------------------------------------------------------------------------


def _resolve_camera_format(camera: str) -> tuple[str | None, bool]:
    """Resolve a camera name/alias to a CST LUT key or manual-CST key.

    Returns (key, is_manual) where is_manual=True means no built-in LUT
    exists and the user must apply the Color Space Transform OFX manually.
    """
    key = camera.lower().strip()

    # Explicit skip
    if key in ("none", "manual", "skip", ""):
        return None, True

    # Direct match in LUT library
    if key in CAMERA_CST_LUTS:
        return key, False

    # Direct match in manual-CST table
    if key in CAMERA_MANUAL_CST:
        return key, True

    # Alias lookup
    alias_target = _CAMERA_ALIASES.get(key)
    if alias_target:
        if alias_target in CAMERA_MANUAL_CST:
            return alias_target, True
        return alias_target, False

    # Partial / fuzzy match — check aliases
    for alias, target in _CAMERA_ALIASES.items():
        if alias in key or key in alias:
            if target in CAMERA_MANUAL_CST:
                return target, True
            return target, False

    return None, True  # unknown camera → treat as manual


def _setup_6nodes_on_item(
    item,
    cst_lut_path: str,
    look_lut_path: str,
    lut_gain: float,
) -> dict:
    """Set up the 6-node log grading structure on a single timeline item.

    Returns a dict of actions taken and any warnings.
    """
    actions = []
    warnings = []

    graph = item.GetNodeGraph(False)  # timeline-level graph
    if not graph:
        return {"error": "Could not access node graph."}

    # Check existing nodes — if only the default 1 node exists, build the structure
    existing = graph.GetNumNodes() or 0

    # Add nodes until we have 6
    for _ in range(max(0, 6 - existing)):
        graph.AddNode()

    # Label all 6 nodes
    for idx, label in enumerate(_NODE_LABELS, start=1):
        graph.SetNodeLabel(idx, label)
    actions.append("Created 6 nodes: WB · EXP · SAT · CURVES · CST · LUT")

    # Node 5 (CST): apply log-to-Rec.709 conversion LUT
    if cst_lut_path and os.path.isfile(cst_lut_path):
        if graph.SetLUT(5, cst_lut_path):
            actions.append(f"CST node: applied {os.path.basename(cst_lut_path)}")
        else:
            warnings.append(
                "CST LUT apply failed — apply Color Space Transform OFX manually to node 5"
            )
    else:
        warnings.append(
            "CST node (5): manually drag 'Color Space Transform' from Effects onto this node, "
            "then set Input Color Space + Gamma for your camera"
        )

    # Node 6 (LUT): apply look LUT
    if look_lut_path and os.path.isfile(look_lut_path):
        if graph.SetLUT(6, look_lut_path):
            actions.append(f"LUT node: applied {os.path.basename(look_lut_path)}")
        else:
            warnings.append(f"Look LUT apply failed: {look_lut_path}")
    else:
        warnings.append(
            f"Look LUT not found at '{look_lut_path}' — apply your LUT manually to node 6"
        )

    # Set Key Output Gain on node 6 (LUT opacity — the 'secret' from the workflow)
    try:
        result = graph.SetNodeKeyOutputGain(6, lut_gain)
        if result:
            actions.append(f"LUT node key output gain set to {lut_gain:.2f} (subtle finish)")
        else:
            warnings.append(
                f"Could not set key output gain automatically — "
                f"go to node 6 > Key tab > Key Output Gain → {lut_gain:.2f}"
            )
    except (AttributeError, TypeError):
        warnings.append(
            f"Key output gain API not available — "
            f"manually set node 6 > Key tab > Key Output Gain → {lut_gain:.2f}"
        )

    return {"clip": item.GetName(), "actions": actions, "warnings": warnings}


@mcp.tool
@safe_resolve_call
def celavii_setup_log_grade(
    camera: str = "sony-slog3",
    look_lut: str = "decsfilm",
    lut_gain: float = 0.20,
    cst_lut_path: str = "",
    apply_to_all: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    set_color_management: bool = True,
) -> str:
    """Set up the 6-node log footage grading structure on a clip or all clips.

    Works with ANY camera that shoots in a log profile — Sony, ARRI, Blackmagic,
    RED, DJI (both D-Log and D-Log M), Panasonic, Olympus, Samsung, iPhone,
    GoPro, Insta360, and more. Natural camera names and aliases all work.

    Node structure:
      Node 1 — WB     (White Balance via Offset wheel)
      Node 2 — EXP    (Exposure via Lift/Gamma/Gain)
      Node 3 — SAT    (Saturation)
      Node 4 — CURVES (S-curve contrast + Hue vs Sat)
      Node 5 — CST    (Log → Rec.709 Color Space Transform)
      Node 6 — LUT    (Film look at reduced gain, ~0.15–0.25)

    Nodes 1-4 work in Log space for maximum dynamic range. Node 5 converts to
    Rec.709. Node 6 is the 'icing on the cake' — subtle, tasteful, not destructive.

    Cameras with built-in Resolve LUTs (CST applied automatically):
      Sony     — 'slog3', 'sony fx3', 'sony a7s', 'zv-e1', etc.
      ARRI     — 'arri-logc', 'alexa'
      BMD      — 'bmpcc4k', 'bmpcc6k', 'braw-4k', 'braw-46k', 'braw-gen5'
      RED      — 'red', 'red komodo', 'red monstro'
      DJI legacy — 'dji', 'dji phantom4', 'dji x7'
      Panasonic — 'vlog', 'gh5', 'gh6', 's5', 'lumix'
      Olympus  — 'olympus', 'om system', 'omlog'
      Samsung  — 'samsung', 'samsung-log'

    Cameras requiring manual CST (node 5 created, instructions provided):
      DJI D-Log M — 'osmo pocket 3', 'dji mini 4', 'mavic 3', 'air 3', 'avata 2'
      Insta360    — 'insta360', 'x4', 'x3', 'insta360 ace'
      GoPro       — 'gopro', 'gopro hero', 'protune'
      iPhone      — 'iphone', 'iphone 16 pro', 'apple log'

    Args:
        camera: Camera name or log format key. Natural names work (see above).
                Use 'none' or 'manual' to skip CST entirely.
        look_lut: Look LUT for node 6:
                  'decsfilm' — DECSFILM.cube (your custom LUT, installed)
                  'kodak2383', 'fuji3513-d55', 'fuji3513-d60', 'fuji3513-d65'
                  Or an absolute .cube file path. Use 'none' to skip.
        lut_gain: Key Output Gain for node 6 (0.0–1.0). Default 0.20.
                  The 'secret' — keeps the LUT subtle. Range: 0.10–0.30.
        cst_lut_path: Override — absolute path to any .cube file to use as the CST.
                      Use this if you've downloaded the manufacturer's LUT
                      (e.g. DJI D-Log M LUT, Insta360 LUT, GoPro LUT).
        apply_to_all: True to apply to every clip on the track.
        track_type: Track type ('video').
        track_index: 1-based track index.
        item_index: 0-based clip index (ignored when apply_to_all=True).
        set_color_management: Set project timeline + output color space to Rec.709-A.
    """
    resolve, project, _ = _boilerplate()

    results = []
    warnings_global = []

    # 1. Set project color management
    if set_color_management:
        for setting_key in ("timelineColorSpace", "outputColorSpace"):
            try:
                project.SetSetting(setting_key, "Rec.709-A")
            except (AttributeError, TypeError):
                pass
        warnings_global.append("Tip: Confirm Rec.709-A in Project Settings > Color Management")

    # 2. Switch to Color page
    resolve.OpenPage("color")

    # 3. Resolve CST LUT path
    resolved_cst_path = ""
    manual_cst_info = None

    if cst_lut_path:
        # Explicit override wins
        if os.path.isfile(cst_lut_path):
            resolved_cst_path = cst_lut_path
        else:
            warnings_global.append(f"cst_lut_path '{cst_lut_path}' not found — node 5 left empty.")
    else:
        cam_key, is_manual = _resolve_camera_format(camera)

        if is_manual:
            # Check Celavii LUT library first — user may have installed the LUT
            library_lut = get_cst_lut_for_camera(camera)
            if library_lut:
                resolved_cst_path = library_lut
                warnings_global.append(
                    f"Using installed LUT from Celavii library: {os.path.basename(library_lut)}"
                )
            elif cam_key and cam_key in CAMERA_MANUAL_CST:
                manual_cst_info = CAMERA_MANUAL_CST[cam_key]
                warnings_global.append(
                    f"⚠ '{camera}' has no built-in Resolve CST LUT. "
                    f"Install one with celavii_install_lut_file() or see "
                    f"'manual_cst_instructions' for how to set up node 5."
                )
            elif cam_key not in (None, "none", "manual", "skip"):
                # Unknown camera — also check LUT library as last resort
                library_lut = get_cst_lut_for_camera(camera)
                if library_lut:
                    resolved_cst_path = library_lut
                else:
                    warnings_global.append(
                        f"⚠ Camera '{camera}' not recognised. Node 5 (CST) created empty. "
                        f"Apply Color Space Transform OFX manually, or pass cst_lut_path."
                    )
        elif cam_key:
            lut_file = CAMERA_CST_LUTS.get(cam_key, "")
            if os.path.isfile(lut_file):
                resolved_cst_path = lut_file
            else:
                warnings_global.append(f"Expected LUT not found: {lut_file}. Node 5 left empty.")

    # 4. Resolve look LUT
    lut_key = look_lut.lower().strip()
    if lut_key == "decsfilm":
        look_lut_path = DECSFILM_LUT
    elif lut_key in FILM_LOOK_LUTS:
        look_lut_path = FILM_LOOK_LUTS[lut_key]
    elif os.path.isabs(look_lut) and os.path.isfile(look_lut):
        look_lut_path = look_lut
    elif lut_key in ("none", "skip", ""):
        look_lut_path = ""
    else:
        look_lut_path = ""
        warnings_global.append(
            f"Look LUT '{look_lut}' not found. "
            f"Built-ins: decsfilm, {', '.join(FILM_LOOK_LUTS.keys())}. "
            f"Or pass an absolute .cube path."
        )

    # 5. Apply to clip(s)
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline open."

    items = tl.GetItemListInTrack(track_type, track_index) or []
    if not items:
        return f"Error: No clips on {track_type} track {track_index}."

    if apply_to_all:
        targets = list(enumerate(items))
    else:
        if item_index >= len(items):
            return f"Error: Item index {item_index} out of range ({len(items)} clips)."
        targets = [(item_index, items[item_index])]

    for _idx, item in targets:
        result = _setup_6nodes_on_item(item, resolved_cst_path, look_lut_path, lut_gain)
        results.append(result)

    output: dict = {
        "setup": "6-Node Log Grade",
        "camera": camera,
        "cst_lut": (
            os.path.basename(resolved_cst_path) if resolved_cst_path else "⚠ manual required"
        ),
        "look_lut": os.path.basename(look_lut_path) if look_lut_path else "none",
        "lut_gain": lut_gain,
        "clips_processed": len(results),
        "results": results,
        "global_notes": warnings_global,
        "next_steps": [
            "1. WB node: Use Offset wheel to center the vectorscope blob",
            "2. EXP node: Use Lift/Gamma/Gain wheels with Waveform (0=black, 100=white)",
            "3. SAT node: Bump Sat 50 → 60–70, use Hue vs Sat for individual colors",
            "4. CURVES node: Draw an S-curve (highlights up, shadows down)",
            "5. CST node: Verify log → Rec.709 conversion matches your camera",
            f"6. LUT node: Key output gain = {lut_gain:.2f} — adjust in Key tab if needed",
        ],
    }

    # Detailed instructions for cameras that need manual CST
    if manual_cst_info:
        cst_block: dict = {
            "what": f"'{camera}' has no built-in Resolve LUT — apply CST manually to node 5.",
            "cameras_this_applies_to": manual_cst_info.get("cameras", []),
            "note": manual_cst_info.get("note", ""),
            "lut_download": manual_cst_info.get("lut_url", ""),
        }
        if manual_cst_info.get("resolve_cst"):
            cst_block["resolve_cst_settings"] = {
                "how": "Effects panel → search 'Color Space Transform' → drag onto node 5",
                **manual_cst_info["resolve_cst"],
            }
        output["manual_cst_instructions"] = cst_block

    return json.dumps(output, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_list_cst_luts() -> str:
    """List all supported cameras and CST LUTs for the 6-node log grading workflow.

    Shows:
    - Cameras with automatic built-in Resolve LUTs (CST applied automatically)
    - Cameras that need manual CST OFX setup, with exact settings and LUT download links
    - Available film look LUTs for node 6
    - Your custom DECSFILM LUT
    - All camera name aliases
    """
    # Built-in LUT cameras
    builtin = {}
    for key, path in CAMERA_CST_LUTS.items():
        builtin[key] = {"path": path, "exists": os.path.isfile(path), "mode": "automatic"}

    # Manual CST cameras
    manual = {}
    for key, info in CAMERA_MANUAL_CST.items():
        manual[key] = {
            "cameras": info.get("cameras", []),
            "mode": "manual — apply Color Space Transform OFX to node 5",
            "resolve_cst_settings": info.get("resolve_cst"),
            "lut_download": info.get("lut_url", ""),
            "note": info.get("note", ""),
        }

    # Film looks
    film_looks = {}
    for key, path in FILM_LOOK_LUTS.items():
        film_looks[key] = {"path": path, "exists": os.path.isfile(path)}

    # Custom LUTs
    custom = {"decsfilm": {"path": DECSFILM_LUT, "exists": os.path.isfile(DECSFILM_LUT)}}

    return json.dumps(
        {
            "automatic_cst_cameras": builtin,
            "manual_cst_cameras": manual,
            "film_look_luts": film_looks,
            "custom_luts": custom,
            "camera_aliases": sorted(_CAMERA_ALIASES.keys()),
            "tip": (
                "Pass cst_lut_path to celavii_setup_log_grade to use any .cube file "
                "as the CST for cameras not listed here."
            ),
        },
        indent=2,
    )
