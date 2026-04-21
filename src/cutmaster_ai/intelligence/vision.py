"""Vision tools — Gemini-powered frame analysis, OCR, and visual inspection.

These tools export frames from the Resolve timeline and send them to
Google's Gemini model for visual analysis. Requires GEMINI_API_KEY.
"""

import json

from ..config import get_gemini_client, mcp
from ..errors import safe_resolve_call
from ..utils.media import export_current_frame, get_timeline_frame_info


def _require_gemini():
    """Return the Gemini client or raise ValueError."""
    client = get_gemini_client()
    if client is None:
        raise ValueError(
            "AI vision tools require GEMINI_API_KEY. Set it in your environment or .env file."
        )
    return client


def _analyze_frame(prompt: str, model: str = "gemini-2.0-flash") -> str:
    """Export the current frame and analyze it with Gemini.

    Internal helper used by all vision tools.
    """
    client = _require_gemini()

    # Export current frame
    frame = export_current_frame(format="jpg")
    if "error" in frame:
        return f"Error exporting frame: {frame['error']}"

    base64_data = frame.get("base64")
    if not base64_data:
        return "Error: Could not encode frame for analysis. Try jpg or png format."

    # Build Gemini request
    response = client.models.generate_content(
        model=model,
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

    result = {
        "analysis": response.text,
        "timecode": frame.get("timecode", "unknown"),
        "timeline": frame.get("timeline", "unknown"),
    }

    # Clean up exported file
    import os

    path = frame.get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def celavii_analyze_frame(
    prompt: str = "Describe this video frame in detail, including composition, lighting, color palette, and any text visible.",
) -> str:
    """Analyze the current timeline frame using Gemini vision AI.

    Exports the frame at the playhead and sends it to Gemini for analysis.
    Requires GEMINI_API_KEY to be set.

    Args:
        prompt: Custom analysis prompt. Default provides a comprehensive description.
    """
    return _analyze_frame(prompt)


@mcp.tool
@safe_resolve_call
def celavii_ocr_frame(
    language: str = "English",
) -> str:
    """Extract text from the current frame using Gemini OCR.

    Reads any visible text in the current frame — titles, lower thirds,
    signs, subtitles, watermarks, etc.

    Args:
        language: Expected language of the text. Helps improve accuracy.
    """
    prompt = (
        f"Extract ALL text visible in this video frame. "
        f"The text is expected to be in {language}. "
        f"Return the text organized by position (top, middle, bottom of frame). "
        f"Include any text in graphics, lower thirds, signs, or overlays. "
        f"If no text is visible, say 'No text detected'."
    )
    return _analyze_frame(prompt)


@mcp.tool
@safe_resolve_call
def celavii_describe_shot() -> str:
    """Get a cinematography-focused description of the current frame.

    Analyzes shot type, camera angle, lens, lighting, and composition.
    Useful for logging, metadata, or editorial notes.
    """
    prompt = (
        "Analyze this video frame from a cinematography perspective. Describe:\n"
        "1. Shot type (wide, medium, close-up, extreme close-up, etc.)\n"
        "2. Camera angle (eye level, high angle, low angle, bird's eye, dutch)\n"
        "3. Estimated focal length range (wide, normal, telephoto)\n"
        "4. Lighting style (natural, studio, hard, soft, high-key, low-key)\n"
        "5. Color temperature (warm, neutral, cool)\n"
        "6. Composition notes (rule of thirds, symmetry, leading lines, depth)\n"
        "7. Subject description\n"
        "Be concise — one line per point."
    )
    return _analyze_frame(prompt)


@mcp.tool
@safe_resolve_call
def celavii_compare_frames(
    reference_path: str,
    comparison_prompt: str = "",
) -> str:
    """Compare the current frame to a reference image.

    Exports the current frame and sends both images to Gemini for comparison.
    Useful for color matching, continuity checking, or A/B review.

    Args:
        reference_path: Path to the reference image file.
        comparison_prompt: Custom comparison prompt. Default compares color and composition.
    """
    import base64 as b64
    import os

    client = _require_gemini()

    # Export current frame
    frame = export_current_frame(format="jpg")
    if "error" in frame:
        return f"Error exporting frame: {frame['error']}"

    current_b64 = frame.get("base64")
    if not current_b64:
        return "Error: Could not encode current frame."

    # Load reference image
    if not os.path.isfile(reference_path):
        return f"Error: Reference file '{reference_path}' not found."

    with open(reference_path, "rb") as f:
        ref_bytes = f.read()
    ref_b64 = b64.b64encode(ref_bytes).decode("utf-8")

    # Determine reference mime type
    ext = os.path.splitext(reference_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    ref_mime = mime_map.get(ext, "image/jpeg")

    prompt = comparison_prompt or (
        "Compare these two video frames:\n"
        "Image 1 is the CURRENT frame from the timeline.\n"
        "Image 2 is the REFERENCE frame.\n\n"
        "Analyze differences in:\n"
        "1. Color balance and temperature\n"
        "2. Exposure and contrast\n"
        "3. Saturation\n"
        "4. Overall look/mood\n"
        "5. Suggest CDL adjustments (slope, offset, power, saturation) to make "
        "the current frame match the reference."
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
                            "data": current_b64,
                        }
                    },
                    {
                        "inline_data": {
                            "mime_type": ref_mime,
                            "data": ref_b64,
                        }
                    },
                ]
            }
        ],
    )

    result = {
        "comparison": response.text,
        "timecode": frame.get("timecode", "unknown"),
        "reference": reference_path,
    }

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
def celavii_frame_info() -> str:
    """Get metadata about the current frame without AI analysis.

    Returns timecode, timeline name, and current clip info.
    No Gemini API key required.
    """
    info = get_timeline_frame_info()
    return json.dumps(info, indent=2)
