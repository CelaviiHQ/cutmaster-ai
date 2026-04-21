"""Auto-detect video + dialogue tracks on a Resolve timeline.

Real-world timelines rarely put picture on V1 and dialogue on A1 only.
Editors stack music on A3, B-roll on V2, VO on A1 while interview audio
lives on A2 + A3, etc. Hardcoding ``("video", 1)`` / ``("audio", 1)``
broke CutMaster on those.

The design is split in two: this module is the deterministic picker,
and the HTTP ``/tracks/{timeline_name}`` endpoint + panel card expose
explicit overrides when the heuristic picks wrong. Full design in
``Implementation/refactor/source-track-picker.md``.

Rules:

1. **Video:** prefer V1 if non-empty, else the lowest-numbered non-empty
   video track. Raises :class:`NoSourceTrackError` if every video track
   is empty — the editor has no picture edit to cut from.
2. **Audio:** prefer tracks named ``Dialogue`` / ``Dialog`` / ``VO`` /
   ``Voice`` / ``Voiceover`` / ``Speech`` (case-insensitive, partial
   match). Else the lowest-numbered non-empty track whose name does NOT
   match ``Music`` / ``SFX`` / ``Ambience`` / ``BGM`` / ``Score`` /
   ``FX`` / ``Foley``. Else the lowest-numbered non-empty track. Raises
   :class:`NoDialogueTrackError` when every audio track is empty.

Naming heuristics are intentionally forgiving — a track named "Dialog
(host)" matches the dialogue pattern; "Music Bed 2" matches the music
exclusion. Empty track names (the default "A1", "A2" Resolve hands out)
fall through to the lowest-numbered non-empty rule.
"""

from __future__ import annotations

import logging
from typing import TypedDict

log = logging.getLogger("cutmaster-ai.cutmaster.track_picker")


class TrackPickerError(Exception):
    """Base class for track-selection failures."""


class NoSourceTrackError(TrackPickerError):
    """No video track has any items — nothing to cut from."""


class NoDialogueTrackError(TrackPickerError):
    """No audio track has any items — nothing to transcribe."""


# Case-insensitive partial match patterns.
_DIALOGUE_HINTS: tuple[str, ...] = (
    "dialogue",
    "dialog",
    "voiceover",
    "voice",
    "speech",
    "vo",
)

_NON_DIALOGUE_HINTS: tuple[str, ...] = (
    "music",
    "sfx",
    "ambience",
    "ambient",
    "bgm",
    "score",
    "foley",
    "fx",
)


class TrackInfo(TypedDict):
    index: int
    name: str
    item_count: int
    picked_by_default: bool


def _name_matches(name: str, hints: tuple[str, ...]) -> bool:
    """Case-insensitive substring match against any hint."""
    if not name:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


def _count_items(tl, track_type: str, index: int) -> int:
    try:
        items = tl.GetItemListInTrack(track_type, index) or []
    except Exception:
        # Defensive — some older Resolve builds throw on out-of-range
        # track indexes instead of returning None.
        return 0
    return len(items)


def _track_name(tl, track_type: str, index: int) -> str:
    try:
        name = tl.GetTrackName(track_type, index)
    except Exception:
        return ""
    return str(name or "")


def _track_count(tl, track_type: str) -> int:
    try:
        count = tl.GetTrackCount(track_type)
    except Exception:
        return 0
    try:
        return int(count or 0)
    except (TypeError, ValueError):
        return 0


def pick_video_track(tl) -> int:
    """Return the 1-based video track most likely to be the picture edit.

    Prefers V1 if non-empty. Otherwise the lowest-numbered non-empty
    track. The editor's picture edit almost always lives on the lowest
    track that has content — higher tracks carry B-roll / overlays.
    """
    count = _track_count(tl, "video")
    if count <= 0:
        raise NoSourceTrackError("Timeline has no video tracks at all.")

    if _count_items(tl, "video", 1) > 0:
        return 1

    for idx in range(2, count + 1):
        if _count_items(tl, "video", idx) > 0:
            log.info("pick_video_track: V1 empty — falling back to V%d", idx)
            return idx

    raise NoSourceTrackError("Every video track on this timeline is empty — nothing to cut from.")


def pick_audio_tracks(tl) -> list[int]:
    """Return the 1-based audio tracks likely to carry dialogue.

    Heuristic:
      1. Any non-empty track whose name matches a dialogue hint.
      2. Else the lowest-numbered non-empty track whose name does NOT
         match a music/SFX/ambience hint.
      3. Else the lowest-numbered non-empty track.

    Returns a list so the override UI can preselect multi-track dialogue
    (interview host + guest on A1+A2). The current backend consumers
    take the first entry; future work can extend to multi-track STT.
    """
    count = _track_count(tl, "audio")
    if count <= 0:
        raise NoDialogueTrackError("Timeline has no audio tracks at all.")

    non_empty: list[tuple[int, str]] = []
    for idx in range(1, count + 1):
        if _count_items(tl, "audio", idx) > 0:
            non_empty.append((idx, _track_name(tl, "audio", idx)))

    if not non_empty:
        raise NoDialogueTrackError(
            "No audio items found on this timeline — rename a track to "
            "'Dialogue' or drop your mic audio in before analyzing."
        )

    # Rule 1 — explicit dialogue hints win.
    dialogue_matches = [idx for idx, name in non_empty if _name_matches(name, _DIALOGUE_HINTS)]
    if dialogue_matches:
        return dialogue_matches

    # Rule 2 — lowest-numbered non-dialogue-labelled + not music.
    for idx, name in non_empty:
        if not _name_matches(name, _NON_DIALOGUE_HINTS):
            return [idx]

    # Rule 3 — everything's labelled music/sfx. Pick the first anyway.
    first_idx, _ = non_empty[0]
    log.warning(
        "pick_audio_tracks: every non-empty track is labelled music/SFX; falling back to A%d",
        first_idx,
    )
    return [first_idx]


def list_video_tracks(tl) -> list[TrackInfo]:
    """Structured enumeration of every video track (override UI feed)."""
    count = _track_count(tl, "video")
    try:
        picked = pick_video_track(tl)
    except NoSourceTrackError:
        picked = -1
    out: list[TrackInfo] = []
    for idx in range(1, count + 1):
        out.append(
            TrackInfo(
                index=idx,
                name=_track_name(tl, "video", idx) or f"V{idx}",
                item_count=_count_items(tl, "video", idx),
                picked_by_default=(idx == picked),
            )
        )
    return out


def list_audio_tracks(tl) -> list[TrackInfo]:
    """Structured enumeration of every audio track (override UI feed).

    Multiple tracks may be flagged ``picked_by_default`` when the
    dialogue heuristic matches several names — the UI can render them
    as a multi-select or collapse to the first.
    """
    count = _track_count(tl, "audio")
    try:
        picked = set(pick_audio_tracks(tl))
    except NoDialogueTrackError:
        picked = set()
    out: list[TrackInfo] = []
    for idx in range(1, count + 1):
        out.append(
            TrackInfo(
                index=idx,
                name=_track_name(tl, "audio", idx) or f"A{idx}",
                item_count=_count_items(tl, "audio", idx),
                picked_by_default=(idx in picked),
            )
        )
    return out
