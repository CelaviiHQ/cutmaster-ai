"""Assembly workflow — create timelines and populate them with clips.

Compound tools for common editorial assembly patterns.
"""

import json

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _find_bin, _find_clip_by_name


@mcp.tool
@safe_resolve_call
def cutmaster_quick_assembly(
    timeline_name: str,
    clip_names: list[str] | None = None,
    bin_path: str = "",
    sort_by: str = "name",
) -> str:
    """Create a timeline and add clips to it in one step.

    This workflow:
    1. Creates a new empty timeline
    2. Finds the specified clips (or all clips from a bin)
    3. Appends them to the timeline in order

    Args:
        timeline_name: Name for the new timeline.
        clip_names: Specific clip names to add. If empty, adds all clips from bin_path.
        bin_path: Bin to pull clips from (used when clip_names is empty).
        sort_by: Sort order — 'name' (alphabetical) or 'none' (pool order).
    """
    _, project, mp = _boilerplate()

    # 1. Create timeline
    tl = mp.CreateEmptyTimeline(timeline_name)
    if not tl:
        return f"Error: Failed to create timeline '{timeline_name}'."

    # 2. Gather clips
    if clip_names:
        clips = []
        not_found = []
        for name in clip_names:
            c = _find_clip_by_name(mp, name)
            if c:
                clips.append(c)
            else:
                not_found.append(name)
    else:
        # Get all clips from a bin
        if bin_path:
            folder = _find_bin(mp.GetRootFolder(), bin_path)
            if not folder:
                return f"Error: Bin '{bin_path}' not found."
        else:
            folder = mp.GetCurrentFolder() or mp.GetRootFolder()
        clips = folder.GetClipList() or []
        not_found = []

    if not clips:
        return f"Timeline '{timeline_name}' created but no clips found to add."

    # 3. Sort if requested
    if sort_by == "name":
        try:
            clips.sort(key=lambda c: c.GetName().lower())
        except (AttributeError, TypeError):
            pass

    # 4. Append to timeline
    project.SetCurrentTimeline(tl)
    items = mp.AppendToTimeline(clips)

    result = {
        "timeline": timeline_name,
        "clips_added": len(items) if items else 0,
        "clips_requested": len(clips),
    }
    if not_found:
        result["not_found"] = not_found

    return json.dumps(result, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_assembly_from_bin(
    timeline_name: str,
    bin_path: str,
    video_tracks: int = 1,
    audio_tracks: int = 2,
) -> str:
    """Create a timeline from all clips in a bin with track configuration.

    This workflow:
    1. Creates a timeline from the bin's clips
    2. Sets up the requested number of video/audio tracks
    3. Names the tracks automatically

    Args:
        timeline_name: Name for the new timeline.
        bin_path: Bin path containing the clips.
        video_tracks: Number of video tracks to create (min 1).
        audio_tracks: Number of audio tracks to create (min 1).
    """
    _, project, mp = _boilerplate()

    folder = _find_bin(mp.GetRootFolder(), bin_path)
    if not folder:
        return f"Error: Bin '{bin_path}' not found."

    clips = folder.GetClipList() or []
    if not clips:
        return f"Error: No clips in bin '{bin_path}'."

    # Create timeline from clips
    tl = mp.CreateTimelineFromClips(timeline_name, clips)
    if not tl:
        return f"Error: Failed to create timeline '{timeline_name}'."

    project.SetCurrentTimeline(tl)

    # Add extra tracks if needed
    current_v = tl.GetTrackCount("video") or 1
    current_a = tl.GetTrackCount("audio") or 1

    tracks_added = []
    for _ in range(max(0, video_tracks - current_v)):
        if tl.AddTrack("video"):
            tracks_added.append("video")
    for _ in range(max(0, audio_tracks - current_a)):
        if tl.AddTrack("audio"):
            tracks_added.append("audio")

    # Name video tracks
    final_v = tl.GetTrackCount("video") or 1
    for i in range(1, final_v + 1):
        tl.SetTrackName("video", i, f"V{i}")
    final_a = tl.GetTrackCount("audio") or 1
    for i in range(1, final_a + 1):
        tl.SetTrackName("audio", i, f"A{i}")

    return json.dumps(
        {
            "timeline": timeline_name,
            "clips": len(clips),
            "video_tracks": final_v,
            "audio_tracks": final_a,
            "tracks_added": len(tracks_added),
        },
        indent=2,
    )


@mcp.tool
@safe_resolve_call
def cutmaster_add_clips_to_track(
    clip_names: list[str],
    track_type: str = "video",
    track_index: int = 1,
) -> str:
    """Add specific clips to a specific track on the current timeline.

    Args:
        clip_names: List of clip names to add.
        track_type: Target track type ('video', 'audio', 'subtitle').
        track_index: 1-based target track index.
    """
    _, project, mp = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "Error: No current timeline."

    clips = []
    not_found = []
    for name in clip_names:
        c = _find_clip_by_name(mp, name)
        if c:
            clips.append(c)
        else:
            not_found.append(name)

    if not clips:
        return "Error: No matching clips found."

    # AppendToTimeline with track info
    clip_infos = [
        {
            "mediaPoolItem": c,
            "trackIndex": track_index,
            "mediaType": 1 if track_type == "video" else 2,
        }
        for c in clips
    ]
    items = mp.AppendToTimeline(clip_infos)

    result = {"added": len(items) if items else 0, "track": f"{track_type} {track_index}"}
    if not_found:
        result["not_found"] = not_found
    return json.dumps(result, indent=2)
