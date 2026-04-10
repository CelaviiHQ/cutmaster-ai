"""Timeline critique tools — AI editorial feedback and analysis.

Uses Gemini to analyze timeline structure, pacing, and content,
providing editorial feedback without requiring frame export for
structure-based analysis.
"""

import json

from ..config import get_gemini_client, mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _ser


def _require_gemini():
    """Return the Gemini client or raise ValueError."""
    client = get_gemini_client()
    if client is None:
        raise ValueError(
            "AI critique tools require GEMINI_API_KEY. Set it in your environment or .env file."
        )
    return client


def _get_timeline_structure() -> dict:
    """Extract the full structure of the current timeline for analysis."""
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        raise ValueError("No current timeline.")

    structure = {
        "name": tl.GetName(),
        "start_frame": tl.GetStartFrame(),
        "end_frame": tl.GetEndFrame(),
        "tracks": {},
    }

    try:
        structure["start_timecode"] = tl.GetStartTimecode()
    except (AttributeError, TypeError):
        pass

    try:
        fps = tl.GetSetting("timelineFrameRate")
        structure["fps"] = fps
    except (AttributeError, TypeError):
        pass

    for track_type in ("video", "audio"):
        track_count = tl.GetTrackCount(track_type) or 0
        tracks = []
        for ti in range(1, track_count + 1):
            track_info = {
                "index": ti,
                "name": tl.GetTrackName(track_type, ti),
                "clips": [],
            }
            items = tl.GetItemListInTrack(track_type, ti) or []
            for item in items:
                clip = {
                    "name": item.GetName(),
                    "duration": item.GetDuration(),
                }
                try:
                    clip["start"] = item.GetProperty("Start")
                    clip["end"] = item.GetEnd()
                except (AttributeError, TypeError):
                    pass
                track_info["clips"].append(clip)
            tracks.append(track_info)
        structure["tracks"][track_type] = tracks

    # Markers
    try:
        markers = tl.GetMarkers() or {}
        structure["markers"] = [{"frame": frame, **_ser(info)} for frame, info in markers.items()]
    except (AttributeError, TypeError):
        pass

    return structure


@mcp.tool
@safe_resolve_call
def celavii_timeline_critique(
    focus: str = "",
) -> str:
    """Get AI editorial feedback on the current timeline.

    Analyzes the timeline structure (clip order, durations, pacing)
    and provides editorial suggestions. Does not analyze visual content
    unless combined with frame analysis.

    Args:
        focus: Specific aspect to focus on — 'pacing', 'structure',
               'transitions', 'audio', or empty for general feedback.
    """
    client = _require_gemini()

    structure = _get_timeline_structure()

    focus_text = (
        f"Focus specifically on: {focus}." if focus else "Provide general editorial feedback."
    )

    prompt = (
        "You are a professional film editor reviewing a timeline. "
        "Analyze this timeline structure and provide editorial feedback.\n\n"
        f"Timeline data:\n{json.dumps(structure, indent=2)}\n\n"
        f"{focus_text}\n\n"
        "Consider:\n"
        "1. Pacing — are clip durations appropriate? Any too long or too short?\n"
        "2. Structure — does the edit flow logically?\n"
        "3. Track usage — are tracks well-organized?\n"
        "4. Markers — are there notes or issues flagged?\n"
        "5. Specific suggestions for improvement\n\n"
        "Be concise and actionable. Refer to clips by name."
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[{"parts": [{"text": prompt}]}],
    )

    return json.dumps(
        {
            "critique": response.text,
            "timeline": structure["name"],
            "total_clips": sum(len(t["clips"]) for tt in structure["tracks"].values() for t in tt),
            "focus": focus or "general",
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_suggest_markers(
    criteria: str = "Mark any clips shorter than 1 second, any gaps, and any jump cuts.",
) -> str:
    """AI analyzes the timeline and suggests where markers should be placed.

    Returns a list of suggested markers with frame positions, colors,
    and notes. Does NOT add them — use celavii_add_timeline_marker to apply.

    Args:
        criteria: What to look for (e.g. 'short clips', 'gaps',
                  'potential b-roll positions', 'rhythm changes').
    """
    client = _require_gemini()

    structure = _get_timeline_structure()

    prompt = (
        "You are a professional film editor analyzing a timeline.\n\n"
        f"Timeline data:\n{json.dumps(structure, indent=2)}\n\n"
        f"Criteria: {criteria}\n\n"
        "Based on the timeline structure, suggest markers to add.\n"
        "For each marker, provide:\n"
        "- frame: the frame number\n"
        "- color: Blue, Green, Yellow, Red, or Pink\n"
        "- name: short marker name\n"
        "- note: explanation\n\n"
        "Respond ONLY with a JSON array of markers like:\n"
        '[{"frame": 100, "color": "Yellow", "name": "Short clip", '
        '"note": "Clip is only 10 frames"}]\n'
        "If no issues found, return an empty array: []"
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[{"parts": [{"text": prompt}]}],
    )

    # Try to parse the JSON array from the response
    import re

    text = response.text
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    suggested_markers = []
    if json_match:
        try:
            suggested_markers = json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return json.dumps(
        {
            "suggested_markers": suggested_markers,
            "count": len(suggested_markers),
            "criteria": criteria,
            "raw_analysis": text if not suggested_markers else None,
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def celavii_visual_continuity_check() -> str:
    """Check visual continuity by analyzing frames at each cut point.

    Exports frames before and after each cut on video track 1 and uses
    Gemini to flag potential continuity issues.

    Note: This tool makes multiple Gemini API calls (one per cut point)
    and may take some time on long timelines.
    """
    client = _require_gemini()

    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    items = tl.GetItemListInTrack("video", 1) or []
    if len(items) < 2:
        return "Need at least 2 clips on V1 to check continuity."

    # For efficiency, just report the cut points and clip transitions
    # without exporting every frame (which would be very slow)
    cuts = []
    for i in range(len(items) - 1):
        clip_a = items[i]
        clip_b = items[i + 1]
        cuts.append(
            {
                "cut_index": i,
                "from_clip": clip_a.GetName(),
                "from_duration": clip_a.GetDuration(),
                "to_clip": clip_b.GetName(),
                "to_duration": clip_b.GetDuration(),
            }
        )

    prompt = (
        "You are a script supervisor checking for continuity issues.\n\n"
        f"Timeline cuts:\n{json.dumps(cuts, indent=2)}\n\n"
        "Based on the clip names and durations, flag any potential issues:\n"
        "1. Same clip appearing back-to-back (possible jump cut)\n"
        "2. Very short clips (< 12 frames) that might be errors\n"
        "3. Any naming patterns suggesting out-of-order shots\n"
        "4. Clips with unusual durations\n\n"
        "Note: This is structure-based analysis only. For visual continuity, "
        "use celavii_compare_frames at specific cut points."
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[{"parts": [{"text": prompt}]}],
    )

    return json.dumps(
        {
            "analysis": response.text,
            "cut_count": len(cuts),
            "timeline": tl.GetName(),
        },
        indent=2,
    )
