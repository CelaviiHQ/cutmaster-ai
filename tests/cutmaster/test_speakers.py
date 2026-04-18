"""Tests for `cutmaster.speakers` — detection, stats, label application."""

from celavii_resolve.cutmaster.stt.speakers import (
    apply_speaker_labels,
    detect_speakers,
    speaker_stats,
)


def _w(word: str, sid: str, t: float) -> dict:
    return {"word": word, "speaker_id": sid, "start_time": t, "end_time": t + 0.3}


# ------------------------- detect_speakers ---------------------------------


def test_detect_speakers_returns_ids_in_first_appearance_order():
    transcript = [
        _w("hello", "S1", 0.0),
        _w("there", "S2", 0.5),
        _w("again", "S1", 1.0),
        _w("sure", "S3", 1.5),
    ]
    assert detect_speakers(transcript) == ["S1", "S2", "S3"]


def test_detect_speakers_empty_on_empty_transcript():
    assert detect_speakers([]) == []


def test_detect_speakers_skips_missing_and_empty_ids():
    transcript = [
        _w("a", "", 0.0),
        {"word": "b", "start_time": 0.5, "end_time": 0.8},  # no speaker_id
        _w("c", "S1", 1.0),
    ]
    assert detect_speakers(transcript) == ["S1"]


# ------------------------- speaker_stats -----------------------------------


def test_speaker_stats_counts_words_per_speaker():
    transcript = [
        _w("a", "S1", 0.0),
        _w("b", "S1", 0.5),
        _w("c", "S2", 1.0),
    ]
    assert speaker_stats(transcript) == {"S1": 2, "S2": 1}


def test_speaker_stats_ignores_empty_ids():
    transcript = [_w("a", "", 0.0), _w("b", "S1", 0.5)]
    assert speaker_stats(transcript) == {"S1": 1}


# ------------------------- apply_speaker_labels ----------------------------


def test_apply_labels_rewrites_speaker_id_and_preserves_other_fields():
    transcript = [_w("hi", "S1", 0.0), _w("yo", "S2", 0.5)]
    out = apply_speaker_labels(transcript, {"S1": "Host", "S2": "Guest"})
    assert out[0]["speaker_id"] == "Host"
    assert out[1]["speaker_id"] == "Guest"
    # Other fields intact.
    assert out[0]["word"] == "hi"
    assert out[0]["start_time"] == 0.0


def test_apply_labels_does_not_mutate_input():
    transcript = [_w("hi", "S1", 0.0)]
    apply_speaker_labels(transcript, {"S1": "Host"})
    assert transcript[0]["speaker_id"] == "S1"


def test_apply_labels_leaves_unmapped_speakers_unchanged():
    transcript = [_w("hi", "S1", 0.0), _w("yo", "S2", 0.5)]
    out = apply_speaker_labels(transcript, {"S1": "Host"})  # S2 omitted
    assert out[0]["speaker_id"] == "Host"
    assert out[1]["speaker_id"] == "S2"


def test_apply_labels_with_none_or_empty_returns_input_unchanged():
    transcript = [_w("hi", "S1", 0.0)]
    assert apply_speaker_labels(transcript, None) is transcript
    assert apply_speaker_labels(transcript, {}) is transcript


def test_apply_labels_ignores_blank_label_values():
    transcript = [_w("hi", "S1", 0.0), _w("yo", "S2", 0.5)]
    # S1 mapped to blank — must fall through, not blank out.
    out = apply_speaker_labels(transcript, {"S1": "   ", "S2": "Guest"})
    assert out[0]["speaker_id"] == "S1"
    assert out[1]["speaker_id"] == "Guest"
