"""Color assist tools — AI-driven CDL recommendations and look development.

Uses Gemini vision to analyze frames and suggest color corrections,
then optionally applies them directly to the node graph.
"""

import json
import re

from ..config import get_gemini_client, mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate
from ..utils.media import export_current_frame


def _require_gemini():
    """Return the Gemini client or raise ValueError."""
    client = get_gemini_client()
    if client is None:
        raise ValueError(
            "AI color tools require GEMINI_API_KEY. Set it in your environment or .env file."
        )
    return client


def _parse_cdl_from_text(text: str) -> dict | None:
    """Extract CDL values from Gemini's text response.

    Looks for patterns like:
        Slope: 1.1 1.0 0.95
        Offset: 0.01 0.0 -0.02
        Power: 1.0 1.0 1.05
        Saturation: 1.1
    """
    cdl = {}

    for key in ("Slope", "Offset", "Power"):
        pattern = rf"{key}\s*[:=]\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            cdl[key] = f"{match.group(1)} {match.group(2)} {match.group(3)}"

    sat_pattern = r"Saturation\s*[:=]\s*([\d.+-]+)"
    sat_match = re.search(sat_pattern, text, re.IGNORECASE)
    if sat_match:
        cdl["Saturation"] = sat_match.group(1)

    return cdl if cdl else None


@mcp.tool
@safe_resolve_call
def cutmaster_color_assist(
    intent: str = "",
    apply: bool = False,
    node: str = "auto",
    six_node_mode: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Get AI-powered color correction suggestions for the current frame.

    Exports the frame, sends it to Gemini for visual analysis, and returns
    CDL (slope/offset/power/saturation) recommendations. Optionally applies
    them directly.

    Works standalone OR within the 6-node log grading structure
    (use `six_node_mode=True` after running `cutmaster_setup_log_grade`).
    In 6-node mode, corrections are applied to the correct nodes:
    white balance → WB (node 1), exposure → EXP (node 2), etc.

    Args:
        intent: What look are you going for? (e.g. 'warm cinematic',
                'cool desaturated', 'neutral balance', 'punchy contrast').
                Leave empty for auto-correction suggestions.
        apply: If True, apply the suggested CDL values to the clip.
        node: Which node to apply CDL to. 'auto' applies to node 1 (or the
              node best suited in 6-node mode). Use 'wb', 'exp', 'sat', 'curves'
              to target specific 6-node structure nodes, or an integer like '2'.
        six_node_mode: True when working within the 6-node log grading structure
                       (set up by cutmaster_setup_log_grade). AI will give advice
                       tailored to each node's role rather than a single CDL.
        track_type: Track type for the target clip.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    import os

    client = _require_gemini()

    # Export frame
    frame = export_current_frame(format="jpg")
    if "error" in frame:
        return f"Error: {frame['error']}"

    base64_data = frame.get("base64")
    if not base64_data:
        return "Error: Could not encode frame for analysis."

    # Resolve node index
    node_map = {"wb": 1, "exp": 2, "sat": 3, "curves": 4, "cst": 5, "lut": 6}
    if node == "auto":
        node_index = 1
    elif node.lower() in node_map:
        node_index = node_map[node.lower()]
    else:
        try:
            node_index = int(node)
        except ValueError:
            node_index = 1

    # Build prompt
    intent_text = (
        f"The desired look is: {intent}" if intent else "Suggest neutral, balanced corrections."
    )

    if six_node_mode:
        prompt = (
            "You are a professional colorist working in DaVinci Resolve using a 6-node structure:\n"
            "  Node 1 (WB) — White balance via Offset\n"
            "  Node 2 (EXP) — Exposure via Lift/Gamma/Gain\n"
            "  Node 3 (SAT) — Saturation\n"
            "  Node 4 (CURVES) — S-curve contrast, Hue vs Sat/Hue\n"
            "  Node 5 (CST) — Color Space Transform (Log → Rec.709, already applied)\n"
            "  Node 6 (LUT) — Film look LUT at ~0.20 key output gain\n\n"
            f"{intent_text}\n\n"
            "Analyze this Rec.709-converted frame (after CST) and suggest corrections.\n"
            "Remember: less is more. Subtle, tasteful adjustments only.\n\n"
            "Provide EXACT CDL values for the primary correction node:\n"
            "Slope: R G B\n"
            "Offset: R G B\n"
            "Power: R G B\n"
            "Saturation: value\n\n"
            "Then provide node-specific advice for WB, EXP, SAT, and CURVES nodes.\n"
            "Mention the vectorscope and waveform readings you'd expect to see."
        )
    else:
        prompt = (
            "You are a professional colorist analyzing a video frame. "
            f"{intent_text}\n\n"
            "Analyze this frame and recommend ASC CDL corrections.\n"
            "You MUST respond with EXACT numeric values in this format:\n\n"
            "Slope: R G B (gain, typically 0.8–1.3)\n"
            "Offset: R G B (lift, typically -0.1 to 0.1)\n"
            "Power: R G B (gamma, typically 0.8–1.2)\n"
            "Saturation: value (typically 0.8–1.3)\n\n"
            "Then explain your reasoning in 2-3 sentences.\n"
            "Example:\n"
            "Slope: 1.05 1.0 0.95\n"
            "Offset: 0.01 0.0 -0.01\n"
            "Power: 1.0 1.0 1.02\n"
            "Saturation: 1.1\n"
            "Reasoning: The image has a slight blue cast..."
        )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_data,
                        }
                    },
                ]
            }
        ],
    )

    analysis_text = response.text
    cdl = _parse_cdl_from_text(analysis_text)

    result = {
        "analysis": analysis_text,
        "timecode": frame.get("timecode", "unknown"),
        "cdl_parsed": cdl,
        "target_node": node_index,
        "applied": False,
        "six_node_mode": six_node_mode,
    }

    # Apply if requested and CDL was parsed
    if apply and cdl:
        _, project, _ = _boilerplate()
        tl = project.GetCurrentTimeline()
        if tl:
            items = tl.GetItemListInTrack(track_type, track_index) or []
            if item_index < len(items):
                item = items[item_index]
                cdl_payload = {"NodeIndex": str(node_index), **cdl}
                if item.SetCDL(cdl_payload):
                    result["applied"] = True
                    result["applied_to_node"] = node_index
                else:
                    result["apply_error"] = "Failed to set CDL values on the clip."
            else:
                result["apply_error"] = f"Item index {item_index} out of range."

    # Clean up
    path = frame.get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_match_to_reference(
    reference_path: str,
    apply: bool = False,
    track_type: str = "video",
    track_index: int = 1,
    item_index: int = 0,
) -> str:
    """Match the current frame's color to a reference image using AI.

    Compares the current frame to a reference and suggests CDL values
    to make them match. Optionally applies the correction.

    Args:
        reference_path: Path to the reference image.
        apply: If True, apply the suggested CDL values.
        track_type: Track type.
        track_index: 1-based track index.
        item_index: 0-based item index.
    """
    import base64 as b64
    import os

    client = _require_gemini()

    # Export current frame
    frame = export_current_frame(format="jpg")
    if "error" in frame:
        return f"Error: {frame['error']}"

    current_b64 = frame.get("base64")
    if not current_b64:
        return "Error: Could not encode current frame."

    # Load reference
    if not os.path.isfile(reference_path):
        return f"Error: Reference file '{reference_path}' not found."

    with open(reference_path, "rb") as f:
        ref_b64 = b64.b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are a professional colorist. Image 1 is the CURRENT frame that needs "
        "correction. Image 2 is the REFERENCE that it should match.\n\n"
        "Analyze both frames and provide EXACT CDL values to make Image 1 match "
        "Image 2's color, contrast, and tone.\n\n"
        "You MUST respond with values in this EXACT format:\n"
        "Slope: R G B\n"
        "Offset: R G B\n"
        "Power: R G B\n"
        "Saturation: value\n\n"
        "Then explain what corrections are needed in 2-3 sentences."
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": current_b64}},
                    {"inline_data": {"mime_type": "image/jpeg", "data": ref_b64}},
                ]
            }
        ],
    )

    analysis_text = response.text
    cdl = _parse_cdl_from_text(analysis_text)

    result = {
        "analysis": analysis_text,
        "cdl_parsed": cdl,
        "reference": reference_path,
        "timecode": frame.get("timecode", "unknown"),
        "applied": False,
    }

    if apply and cdl:
        _, project, _ = _boilerplate()
        tl = project.GetCurrentTimeline()
        if tl:
            items = tl.GetItemListInTrack(track_type, track_index) or []
            if item_index < len(items):
                cdl_payload = {"NodeIndex": "1", **cdl}
                if items[item_index].SetCDL(cdl_payload):
                    result["applied"] = True

    # Clean up
    path = frame.get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass

    return json.dumps(result, indent=2)
