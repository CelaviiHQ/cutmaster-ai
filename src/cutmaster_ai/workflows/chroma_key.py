"""Chroma key workflow — green/blue screen removal via Fusion's DeltaKeyer.

Builds a Fusion composition with a DeltaKeyer node to key out a solid-color
background (typically green or blue screen). Works entirely within Resolve —
no ffmpeg workarounds needed.
"""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate

# ---------------------------------------------------------------------------
# Preset key colors (normalized 0–1 RGB for Fusion)
# ---------------------------------------------------------------------------

KEY_COLOR_PRESETS: dict[str, dict[str, float]] = {
    "green": {"Red": 0.0, "Green": 1.0, "Blue": 0.0},
    "blue": {"Red": 0.0, "Green": 0.0, "Blue": 1.0},
    "bright-green": {"Red": 0.0, "Green": 1.0, "Blue": 0.0},  # #00FF00
    "chroma-green": {"Red": 0.0, "Green": 0.81, "Blue": 0.28},  # typical studio green
    "chroma-blue": {"Red": 0.07, "Green": 0.18, "Blue": 0.72},  # typical studio blue
}


def _get_timeline_item(project, track_type, track_index, item_index):
    """Get a timeline item by track/index. Returns (timeline, item)."""
    tl = project.GetCurrentTimeline()
    if not tl:
        raise ValueError("No current timeline.")
    items = tl.GetItemListInTrack(track_type, track_index) or []
    if item_index >= len(items):
        raise ValueError(
            f"Item index {item_index} out of range ({len(items)} clips on "
            f"{track_type} track {track_index})."
        )
    return tl, items[item_index]


def _setup_chroma_key_comp(
    item,
    key_color: dict[str, float],
    gain: float,
    balance: float,
    clean_fg: float,
    clean_bg: float,
    erode: float,
    blur: float,
    spill_method: str,
    spill_strength: float,
) -> dict:
    """Build the DeltaKeyer Fusion comp on a single timeline item.

    Returns a dict of actions taken, warnings, and the comp/tool names.
    """
    actions = []
    warnings = []

    # Ensure a Fusion comp exists — use comp 1 or add one
    comp_count = item.GetFusionCompCount() or 0
    if comp_count == 0:
        comp = item.AddFusionComp()
        if not comp:
            return {"error": "Failed to create Fusion composition."}
        actions.append("Created new Fusion composition")
    else:
        comp = item.GetFusionCompByIndex(1)
        if not comp:
            return {"error": "Failed to access Fusion composition."}
        actions.append("Using existing Fusion composition (index 1)")

    comp.StartUndo("CutMaster Chroma Key")

    try:
        # Find existing MediaIn and MediaOut
        tools = comp.GetToolList() or {}
        media_in = None
        media_out = None
        for _name, tool in tools.items():
            try:
                tool_id = tool.GetAttrs().get("TOOLS_RegID", "")
                if tool_id == "MediaIn":
                    media_in = tool
                elif tool_id == "MediaOut":
                    media_out = tool
            except (AttributeError, TypeError):
                pass

        if not media_in:
            return {"error": "No MediaIn node found in Fusion comp."}
        if not media_out:
            return {"error": "No MediaOut node found in Fusion comp."}

        media_in_name = media_in.GetAttrs().get("TOOLS_Name", "MediaIn1")
        media_out_name = media_out.GetAttrs().get("TOOLS_Name", "MediaOut1")

        # Add the DeltaKeyer
        keyer = comp.AddTool("DeltaKeyer")
        if not keyer:
            return {"error": "Failed to add DeltaKeyer tool."}
        keyer_name = keyer.GetAttrs().get("TOOLS_Name", "DeltaKeyer1")
        actions.append(f"Added DeltaKeyer node: {keyer_name}")

        # Connect: MediaIn → DeltaKeyer → MediaOut
        keyer["Input"].ConnectTo(media_in.Output)
        actions.append(f"Connected {media_in_name} → {keyer_name}")

        media_out["Input"].ConnectTo(keyer.Output)
        actions.append(f"Connected {keyer_name} → {media_out_name}")

        # Set the key color
        t = comp.CurrentTime
        keyer["KeyColor"][t] = key_color
        actions.append(
            f"Key color set: R={key_color['Red']:.2f} "
            f"G={key_color['Green']:.2f} B={key_color['Blue']:.2f}"
        )

        # Keying parameters
        keyer["Gain"][t] = gain
        keyer["Balance"][t] = balance
        actions.append(f"Gain={gain:.2f}, Balance={balance:.2f}")

        # Matte cleanup
        if clean_fg > 0:
            keyer["CleanForeground"][t] = clean_fg
        if clean_bg > 0:
            keyer["CleanBackground"][t] = clean_bg
        if erode != 0:
            keyer["ErodeAlpha"][t] = erode
        if blur > 0:
            keyer["BlurAlpha"][t] = blur
        actions.append(
            f"Matte: CleanFG={clean_fg:.2f}, CleanBG={clean_bg:.2f}, "
            f"Erode={erode:.3f}, Blur={blur:.2f}"
        )

        # Spill suppression
        if spill_method == "green":
            keyer["SpillMethod"][t] = 1  # Green suppression
        elif spill_method == "blue":
            keyer["SpillMethod"][t] = 2  # Blue suppression
        else:
            keyer["SpillMethod"][t] = 0  # None

        if spill_strength > 0:
            keyer["SpillStrength"][t] = spill_strength
            actions.append(f"Spill suppression: {spill_method}, strength={spill_strength:.2f}")

    except Exception as exc:
        warnings.append(f"Error during setup: {exc}")
    finally:
        comp.EndUndo(True)

    return {
        "clip": item.GetName(),
        "keyer": keyer_name,
        "actions": actions,
        "warnings": warnings,
    }


@mcp.tool
@safe_resolve_call
def cutmaster_chroma_key(
    key_color: str = "green",
    key_color_r: float = -1.0,
    key_color_g: float = -1.0,
    key_color_b: float = -1.0,
    gain: float = 1.0,
    balance: float = 0.0,
    clean_fg: float = 0.05,
    clean_bg: float = 0.05,
    erode: float = 0.001,
    blur: float = 1.0,
    spill_method: str = "auto",
    spill_strength: float = 1.0,
    apply_to_all: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Apply chroma key (green/blue screen removal) to timeline clips via Fusion DeltaKeyer.

    Builds a Fusion composition with a DeltaKeyer node that keys out the
    specified background color. Works with any solid-color backdrop — green
    screen, blue screen, or custom colors.

    Presets: 'green', 'blue', 'bright-green' (#00FF00), 'chroma-green'
    (studio green), 'chroma-blue' (studio blue).

    For custom key colors, set key_color_r/g/b (0.0–1.0 range) — these
    override the preset.

    The node chain is: MediaIn → DeltaKeyer → MediaOut.

    Args:
        key_color: Color preset name. See presets above.
        key_color_r: Custom key red (0.0–1.0). Set all three to override preset.
        key_color_g: Custom key green (0.0–1.0).
        key_color_b: Custom key blue (0.0–1.0).
        gain: Keyer gain — higher values key more aggressively. Default 1.0.
        balance: Keyer balance (-1.0 to 1.0). Adjusts matte edge. Default 0.0.
        clean_fg: Clean foreground amount (0.0–1.0). Solidifies the subject. Default 0.05.
        clean_bg: Clean background amount (0.0–1.0). Removes background remnants. Default 0.05.
        erode: Matte edge erosion. Positive shrinks, negative expands. Default 0.001.
        blur: Matte edge blur/softness. Default 1.0.
        spill_method: Spill suppression: 'auto' (detects from key_color), 'green', 'blue', 'none'.
        spill_strength: Spill suppression strength (0.0–1.0). Default 1.0.
        apply_to_all: True to apply to every clip on the track.
        track_type: Track type.
        track_index: 1-based track index.
        item_index: 0-based clip index (ignored when apply_to_all=True).
    """
    resolve, project, _ = _boilerplate()

    # Resolve key color
    if key_color_r >= 0 and key_color_g >= 0 and key_color_b >= 0:
        color = {"Red": key_color_r, "Green": key_color_g, "Blue": key_color_b}
    else:
        preset_key = key_color.lower().strip()
        color = KEY_COLOR_PRESETS.get(preset_key)
        if not color:
            return (
                f"Error: Unknown key color preset '{key_color}'. "
                f"Available: {', '.join(KEY_COLOR_PRESETS.keys())}. "
                f"Or set key_color_r/g/b for a custom color."
            )

    # Auto-detect spill method from key color
    if spill_method == "auto":
        if color["Green"] > color["Red"] and color["Green"] > color["Blue"]:
            spill_method = "green"
        elif color["Blue"] > color["Red"] and color["Blue"] > color["Green"]:
            spill_method = "blue"
        else:
            spill_method = "none"

    # Switch to Fusion page
    resolve.OpenPage("fusion")

    # Get clips
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    items = tl.GetItemListInTrack(track_type, track_index) or []
    if not items:
        return f"Error: No clips on {track_type} track {track_index}."

    if apply_to_all:
        targets = list(enumerate(items))
    else:
        if item_index >= len(items):
            return f"Error: Item index {item_index} out of range ({len(items)} clips)."
        targets = [(item_index, items[item_index])]

    results = []
    for _idx, item in targets:
        result = _setup_chroma_key_comp(
            item=item,
            key_color=color,
            gain=gain,
            balance=balance,
            clean_fg=clean_fg,
            clean_bg=clean_bg,
            erode=erode,
            blur=blur,
            spill_method=spill_method,
            spill_strength=spill_strength,
        )
        results.append(result)

    succeeded = sum(1 for r in results if "error" not in r)
    failed = sum(1 for r in results if "error" in r)

    return json.dumps(
        {
            "workflow": "Chroma Key (Fusion DeltaKeyer)",
            "key_color": color,
            "spill_suppression": spill_method,
            "clips_processed": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
            "next_steps": [
                "1. Review the key in Fusion page — check alpha channel (A button in viewer)",
                "2. Adjust Gain/Balance on the DeltaKeyer if edges aren't clean",
                "3. Use CleanForeground/CleanBackground to solidify the matte",
                "4. Fine-tune spill suppression if green/blue fringing is visible",
                "5. Add a Background node + Merge if you want to composite over a new background",
            ],
        },
        indent=2,
    )
