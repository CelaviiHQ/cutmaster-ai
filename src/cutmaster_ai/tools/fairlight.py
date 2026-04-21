"""Fairlight audio tools — audio insertion, voice isolation, track info."""

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate, _require_studio, get_resolve


@mcp.tool
@safe_resolve_call
def cutmaster_insert_audio_at_playhead(
    file_path: str,
    start_offset: int = 0,
    duration: int = 0,
) -> str:
    """Insert an audio file at the playhead position on the current audio track.

    Args:
        file_path: Absolute path to the audio file.
        start_offset: Start offset in frames within the audio file.
        duration: Duration in frames (0 = full clip).
    """
    _, project, _ = _boilerplate()
    result = project.InsertAudioToCurrentTrackAtPlayhead(file_path, start_offset, duration)
    return "Audio inserted at playhead." if result else "Failed to insert audio."


@mcp.tool
@safe_resolve_call
def cutmaster_voice_isolation(
    track_type: str = "audio",
    track_index: int = 1,
) -> str:
    """Apply voice isolation to an audio track (Studio only).

    Isolates dialogue from background noise.

    Args:
        track_type: Should be 'audio'.
        track_index: 1-based audio track index.
    """
    _require_studio("Voice Isolation")
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    # Voice isolation is typically applied via the Fairlight page
    # Using the timeline setting approach
    resolve = get_resolve()
    resolve.OpenPage("fairlight")
    return (
        "Switched to Fairlight page for voice isolation. "
        "Voice isolation must be applied through the Fairlight inspector."
    )


@mcp.tool
@safe_resolve_call
def cutmaster_get_audio_track_info() -> str:
    """Get information about all audio tracks in the current timeline."""
    import json

    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    count = tl.GetTrackCount("audio") or 0
    tracks = []
    for i in range(1, count + 1):
        info = {"index": i, "name": tl.GetTrackName("audio", i)}
        try:
            info["enabled"] = tl.GetIsTrackEnabled("audio", i)
        except (AttributeError, TypeError):
            pass
        try:
            info["locked"] = tl.GetIsTrackLocked("audio", i)
        except (AttributeError, TypeError):
            pass
        items = tl.GetItemListInTrack("audio", i) or []
        info["clip_count"] = len(items)
        tracks.append(info)
    return json.dumps({"audio_tracks": tracks, "count": count}, indent=2)


@mcp.tool
@safe_resolve_call
def cutmaster_set_audio_track_volume(
    track_index: int,
    volume: float,
) -> str:
    """Set the volume of an audio track.

    Args:
        track_index: 1-based audio track index.
        volume: Volume level (0.0 = silent, 1.0 = unity gain).
    """
    _, project, _ = _boilerplate()
    tl = project.GetCurrentTimeline()
    if not tl:
        return "No current timeline."
    # Volume is set via timeline settings
    result = tl.SetSetting(f"audioTrack{track_index}Volume", str(volume))
    if result:
        return f"Audio track {track_index} volume set to {volume}."
    return "Failed to set volume. Track volume may need to be set via Fairlight page."
