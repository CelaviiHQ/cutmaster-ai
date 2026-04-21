"""Unit tests for cutmaster.resolve_ops.track_picker."""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.resolve_ops.track_picker import (
    NoDialogueTrackError,
    NoSourceTrackError,
    list_audio_tracks,
    list_video_tracks,
    pick_audio_tracks,
    pick_video_track,
)


class FakeTimeline:
    """Mimics the subset of Resolve Timeline used by track_picker."""

    def __init__(
        self,
        *,
        video_tracks: list[tuple[str, int]] | None = None,
        audio_tracks: list[tuple[str, int]] | None = None,
    ) -> None:
        # Each tuple is (name, item_count). Index is 1-based.
        self._video = video_tracks or []
        self._audio = audio_tracks or []

    def GetTrackCount(self, track_type: str) -> int:  # noqa: N802
        if track_type == "video":
            return len(self._video)
        if track_type == "audio":
            return len(self._audio)
        return 0

    def GetTrackName(self, track_type: str, index: int) -> str:  # noqa: N802
        data = self._video if track_type == "video" else self._audio
        if 1 <= index <= len(data):
            return data[index - 1][0]
        return ""

    def GetItemListInTrack(self, track_type: str, index: int):  # noqa: N802
        data = self._video if track_type == "video" else self._audio
        if 1 <= index <= len(data):
            # Return a list of the right length — content doesn't matter.
            return [object()] * data[index - 1][1]
        return []


class TestPickVideoTrack:
    def test_v1_populated_wins(self):
        tl = FakeTimeline(video_tracks=[("V1", 10), ("V2", 3)])
        assert pick_video_track(tl) == 1

    def test_falls_back_when_v1_empty(self):
        """Picture edit on V2 with V1 empty — common multi-cam layout."""
        tl = FakeTimeline(video_tracks=[("V1", 0), ("V2", 25)])
        assert pick_video_track(tl) == 2

    def test_skips_multiple_empty_tracks(self):
        tl = FakeTimeline(video_tracks=[("V1", 0), ("V2", 0), ("V3", 5)])
        assert pick_video_track(tl) == 3

    def test_no_video_tracks_raises(self):
        tl = FakeTimeline(video_tracks=[])
        with pytest.raises(NoSourceTrackError):
            pick_video_track(tl)

    def test_all_video_empty_raises(self):
        tl = FakeTimeline(video_tracks=[("V1", 0), ("V2", 0)])
        with pytest.raises(NoSourceTrackError):
            pick_video_track(tl)


class TestPickAudioTracks:
    def test_a1_populated_wins(self):
        tl = FakeTimeline(audio_tracks=[("A1", 5), ("A2", 2)])
        assert pick_audio_tracks(tl) == [1]

    def test_falls_back_when_a1_empty(self):
        """Dialogue on A2 with A1 empty — editor separated picture from sync audio."""
        tl = FakeTimeline(audio_tracks=[("A1", 0), ("A2", 1)])
        assert pick_audio_tracks(tl) == [2]

    def test_dialogue_named_track_beats_a1(self):
        tl = FakeTimeline(
            audio_tracks=[("A1", 3), ("Dialogue", 8), ("Music", 2)],
        )
        assert pick_audio_tracks(tl) == [2]

    def test_multiple_dialogue_tracks_returned_in_order(self):
        """Interview with host + guest on separately-named tracks."""
        tl = FakeTimeline(
            audio_tracks=[
                ("Dialog Host", 10),
                ("Music", 3),
                ("Dialog Guest", 12),
            ],
        )
        assert pick_audio_tracks(tl) == [1, 3]

    def test_skips_music_track_when_lower_numbered(self):
        tl = FakeTimeline(audio_tracks=[("Music", 4), ("A2", 6)])
        assert pick_audio_tracks(tl) == [2]

    def test_all_music_timeline_falls_through_to_lowest(self):
        """Everything's labelled music/SFX — still returns a track.

        Caller hits ``NoDialogueTrackError`` only when the timeline is
        literally empty; a music-only timeline transcribes the music
        track and probably produces garbage, but we don't veto it.
        """
        tl = FakeTimeline(audio_tracks=[("Music Bed", 4), ("SFX", 2)])
        assert pick_audio_tracks(tl) == [1]

    def test_no_audio_tracks_raises(self):
        tl = FakeTimeline(audio_tracks=[])
        with pytest.raises(NoDialogueTrackError):
            pick_audio_tracks(tl)

    def test_all_empty_raises(self):
        tl = FakeTimeline(audio_tracks=[("A1", 0), ("Music", 0)])
        with pytest.raises(NoDialogueTrackError):
            pick_audio_tracks(tl)

    def test_partial_match_dialogue(self):
        """'VO Narration' should match the 'vo' hint."""
        tl = FakeTimeline(audio_tracks=[("A1", 3), ("VO Narration", 8)])
        assert pick_audio_tracks(tl) == [2]

    def test_partial_match_music_excluded(self):
        """'Music Bed 2' should match the music exclusion."""
        tl = FakeTimeline(
            audio_tracks=[("Music Bed 2", 10), ("A2", 3)],
        )
        assert pick_audio_tracks(tl) == [2]


class TestListTracks:
    def test_list_video_marks_picked(self):
        tl = FakeTimeline(video_tracks=[("V1", 0), ("V2", 10)])
        result = list_video_tracks(tl)
        assert [t["index"] for t in result] == [1, 2]
        assert [t["picked_by_default"] for t in result] == [False, True]
        assert [t["item_count"] for t in result] == [0, 10]

    def test_list_audio_marks_all_dialogue_matches(self):
        tl = FakeTimeline(
            audio_tracks=[("Dialog A", 5), ("Music", 3), ("Dialog B", 7)],
        )
        result = list_audio_tracks(tl)
        assert [t["picked_by_default"] for t in result] == [True, False, True]

    def test_list_audio_empty_timeline_no_picks(self):
        tl = FakeTimeline(audio_tracks=[("A1", 0), ("A2", 0)])
        result = list_audio_tracks(tl)
        assert all(not t["picked_by_default"] for t in result)

    def test_list_video_falls_back_track_name(self):
        """When Resolve returns empty name, fall back to V{n} label."""
        tl = FakeTimeline(video_tracks=[("", 0), ("", 4)])
        result = list_video_tracks(tl)
        assert result[0]["name"] == "V1"
        assert result[1]["name"] == "V2"
