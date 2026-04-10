"""Grade workflow — apply LUTs, adjust CDL, copy grades, and save stills.

Compound tools for common color grading workflows.
"""

import json
import os

from ..config import mcp
from ..errors import safe_resolve_call
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
    # ARRI
    "arri-logc": f"{_LUT_BASE}/Arri/Arri Alexa LogC to Rec709.dat",
    # Blackmagic
    "braw-4k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic 4K Film to Rec709.cube",
    "braw-46k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic 4.6K Film to Rec709.cube",
    "braw-pocket4k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic Pocket 4K Film to Extended Video v4.cube",
    "braw-pocket6k": f"{_LUT_BASE}/Blackmagic Design/Blackmagic Pocket 6K Film to Extended Video v4.cube",
    # RED
    "red-log3g10": f"{_LUT_BASE}/RED/RWG_Log3G10_to_REC709_BT1886_with_LOW_CONTRAST_and_R_3_Soft_size_33.cube",
    # DJI
    "dji-dlog": f"{_LUT_BASE}/DJI/DJI_X7_DLOG2Rec709.cube",
    # Panasonic
    "panasonic-vlog": f"{_LUT_BASE}/Panasonic/V-Log to V-709.cube",
}

# Alias expansions so users can type natural names
_CAMERA_ALIASES: dict[str, str] = {
    "slog3": "sony-slog3",
    "s-log3": "sony-slog3",
    "sony fx3": "sony-slog3",
    "sony fx6": "sony-slog3",
    "sony fx9": "sony-slog3",
    "sony a7": "sony-slog3",
    "sony a1": "sony-slog3",
    "arri alexa": "arri-logc",
    "logc": "arri-logc",
    "log-c": "arri-logc",
    "blackmagic": "braw-4k",
    "bmpcc4k": "braw-pocket4k",
    "bmpcc6k": "braw-pocket6k",
    "red": "red-log3g10",
    "dji": "dji-dlog",
    "vlog": "panasonic-vlog",
    "v-log": "panasonic-vlog",
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


def _resolve_camera_format(camera: str) -> str | None:
    """Resolve a camera name/alias to a CST LUT key."""
    key = camera.lower().strip()
    if key in CAMERA_CST_LUTS:
        return key
    if key in _CAMERA_ALIASES:
        return _CAMERA_ALIASES[key]
    # Partial match
    for alias, target in _CAMERA_ALIASES.items():
        if alias in key or key in alias:
            return target
    return None


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
    apply_to_all: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
    set_color_management: bool = True,
) -> str:
    """Set up the 6-node log footage grading structure on a clip or all clips.

    Implements the proven 6-node workflow:
      Node 1 — WB     (White Balance via Offset wheel)
      Node 2 — EXP    (Exposure via Lift/Gamma/Gain)
      Node 3 — SAT    (Saturation)
      Node 4 — CURVES (S-curve contrast + Hue vs Sat)
      Node 5 — CST    (Log → Rec.709 Color Space Transform)
      Node 6 — LUT    (Film look at reduced gain, ~0.15–0.25)

    Nodes 1-4 work in the camera's Log space for maximum dynamic range.
    The CST in node 5 converts to Rec.709. The LUT in node 6 is the
    'icing on the cake' — subtle, tasteful, not destructive.

    Args:
        camera: Camera log format. Options:
                'sony-slog3' (FX3/FX6/FX9/A7/A1),
                'arri-logc' (Alexa),
                'braw-pocket4k', 'braw-pocket6k', 'braw-4k', 'braw-46k',
                'red-log3g10', 'dji-dlog', 'panasonic-vlog'.
                Aliases like 'slog3', 'arri alexa', 'bmpcc6k' also work.
        look_lut: Look LUT for node 6. Use 'decsfilm' for the DECSFILM.cube LUT,
                  'kodak2383', 'fuji3513-d55', 'fuji3513-d60', 'fuji3513-d65'
                  for built-in Resolve film looks, or an absolute file path.
        lut_gain: Key Output Gain for the LUT node (0.0–1.0). Default 0.20.
                  This makes the LUT subtle — 'icing on the cake'.
                  Recommended range: 0.10–0.30.
        apply_to_all: True to apply the structure to every clip on the track.
                      False to apply only to the clip at item_index.
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
        for setting_key in ("colorScienceMode", "timelineColorSpace", "outputColorSpace"):
            try:
                if setting_key == "timelineColorSpace" or setting_key == "outputColorSpace":
                    project.SetSetting(setting_key, "Rec.709-A")
            except (AttributeError, TypeError):
                pass
        warnings_global.append(
            "Tip: Confirm Color Management is set to Rec.709-A in Project Settings > Color Management"
        )

    # 2. Switch to Color page
    resolve.OpenPage("color")

    # 3. Resolve camera format → CST LUT
    cam_key = _resolve_camera_format(camera)
    cst_lut_path = CAMERA_CST_LUTS.get(cam_key or "", "")
    if not cst_lut_path or not os.path.isfile(cst_lut_path):
        warnings_global.append(
            f"No built-in CST LUT found for '{camera}'. "
            f"Manually apply Color Space Transform OFX to node 5."
        )
        cst_lut_path = ""

    # 4. Resolve look LUT
    lut_key = look_lut.lower().strip()
    if lut_key == "decsfilm":
        look_lut_path = DECSFILM_LUT
    elif lut_key in FILM_LOOK_LUTS:
        look_lut_path = FILM_LOOK_LUTS[lut_key]
    elif os.path.isabs(look_lut) and os.path.isfile(look_lut):
        look_lut_path = look_lut
    else:
        look_lut_path = ""
        warnings_global.append(
            f"Look LUT '{look_lut}' not found. Valid built-ins: "
            f"decsfilm, {', '.join(FILM_LOOK_LUTS.keys())}. "
            f"Or pass an absolute file path."
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
        result = _setup_6nodes_on_item(item, cst_lut_path, look_lut_path, lut_gain)
        results.append(result)

    return json.dumps(
        {
            "setup": "6-Node Log Grade",
            "camera": camera,
            "cst_lut": os.path.basename(cst_lut_path) if cst_lut_path else "manual",
            "look_lut": os.path.basename(look_lut_path) if look_lut_path else "manual",
            "lut_gain": lut_gain,
            "clips_processed": len(results),
            "results": results,
            "global_notes": warnings_global,
            "next_steps": [
                "1. WB node: Use Offset wheel to center the vectorscope blob",
                "2. EXP node: Use Lift/Gamma/Gain wheels against the Waveform (0=black, 100=white)",
                "3. SAT node: Bump Sat from 50 → 60–70, use Hue vs Sat for specific colors",
                "4. CURVES node: Draw an S-curve for punch (highlights up, shadows down)",
                "5. CST node: Verify Color Space Transform is set to your camera's log profile",
                f"6. LUT node: Key output gain is {lut_gain:.2f} — adjust in Key tab if needed",
            ],
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_list_cst_luts() -> str:
    """List all available camera CST (Color Space Transform) LUTs and film looks.

    Shows which built-in Resolve LUTs are available for log-to-Rec.709
    conversion by camera format, plus the DECSFILM custom LUT.
    """
    available = {}
    for key, path in CAMERA_CST_LUTS.items():
        available[key] = {
            "path": path,
            "exists": os.path.isfile(path),
        }

    film_looks = {}
    for key, path in FILM_LOOK_LUTS.items():
        film_looks[key] = {
            "path": path,
            "exists": os.path.isfile(path),
        }

    custom = {
        "decsfilm": {
            "path": DECSFILM_LUT,
            "exists": os.path.isfile(DECSFILM_LUT),
        }
    }

    aliases = list(_CAMERA_ALIASES.keys())

    return json.dumps(
        {
            "cst_luts": available,
            "film_looks": film_looks,
            "custom_luts": custom,
            "camera_aliases": aliases,
        },
        indent=2,
    )
