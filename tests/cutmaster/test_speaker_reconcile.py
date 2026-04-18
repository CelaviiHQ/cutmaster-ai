"""Tests for cross-clip speaker reconciliation (v2-6 follow-up)."""

from __future__ import annotations

import pytest

from celavii_resolve.cutmaster.speaker_reconcile import (
    SpeakerReconciliation,
    _apply_mapping,
    _collect_local_samples,
    _ReconcileMapEntry,
    _validate_reconciliation,
    collapse_to_solo,
    reconcile_with_llm,
)


def _w(word: str, clip: int, sid: str, t: float) -> dict:
    return {
        "word": word,
        "speaker_id": sid,
        "start_time": t,
        "end_time": t + 0.3,
        "clip_index": clip,
    }


# ------------------------- collapse ---------------------------------------


def test_collapse_rewrites_every_speaker_to_s1():
    transcript = [
        _w("hi", 0, "S1", 0.0),
        _w("there", 1, "S2", 2.0),
        _w("again", 2, "S3", 4.0),
    ]
    out = collapse_to_solo(transcript)
    assert [w["speaker_id"] for w in out] == ["S1", "S1", "S1"]


def test_collapse_does_not_mutate_input():
    transcript = [_w("hi", 0, "S2", 0.0)]
    collapse_to_solo(transcript)
    assert transcript[0]["speaker_id"] == "S2"


def test_collapse_preserves_other_fields():
    transcript = [_w("hi", 1, "S2", 0.5)]
    out = collapse_to_solo(transcript)
    assert out[0]["word"] == "hi"
    assert out[0]["start_time"] == 0.5
    assert out[0]["clip_index"] == 1


# ------------------------- sampling ---------------------------------------


def test_collect_samples_returns_one_row_per_unique_pair():
    transcript = (
        [_w(f"a{i}", 0, "S1", i) for i in range(20)]
        + [_w(f"b{i}", 0, "S2", i + 100) for i in range(5)]
        + [_w(f"c{i}", 1, "S1", i + 200) for i in range(15)]
    )
    samples = _collect_local_samples(transcript)
    keys = {(s["clip_index"], s["local_id"]) for s in samples}
    assert keys == {(0, "S1"), (0, "S2"), (1, "S1")}


def test_collect_samples_quotes_are_bounded():
    transcript = [_w(f"w{i}", 0, "S1", i) for i in range(50)]
    samples = _collect_local_samples(
        transcript,
        max_samples_per_key=3,
        max_sample_words=12,
    )
    assert len(samples) == 1
    assert len(samples[0]["quotes"]) == 3
    for q in samples[0]["quotes"]:
        assert len(q.split()) <= 12
    assert samples[0]["word_count"] == 50


def test_collect_samples_skips_rows_missing_clip_index():
    transcript = [
        {"word": "x", "speaker_id": "S1", "start_time": 0, "end_time": 1},
        _w("y", 0, "S1", 1.0),
    ]
    samples = _collect_local_samples(transcript)
    assert len(samples) == 1


# ------------------------- mapping application ----------------------------


def test_apply_mapping_rewrites_matching_keys():
    transcript = [
        _w("a", 0, "S1", 0.0),
        _w("b", 1, "S2", 1.0),
        _w("c", 2, "S1", 2.0),
    ]
    mapping = {
        (0, "S1"): "S1",
        (1, "S2"): "S1",
        (2, "S1"): "S2",
    }
    out = _apply_mapping(transcript, mapping)
    assert [w["speaker_id"] for w in out] == ["S1", "S1", "S2"]
    # Input unmodified.
    assert [w["speaker_id"] for w in transcript] == ["S1", "S2", "S1"]


def test_apply_mapping_passes_through_unmapped_words():
    transcript = [_w("a", 0, "S1", 0.0), _w("b", 7, "S9", 1.0)]
    out = _apply_mapping(transcript, {(0, "S1"): "S3"})
    assert out[0]["speaker_id"] == "S3"
    assert out[1]["speaker_id"] == "S9"  # unmapped untouched


# ------------------------- validator --------------------------------------


def _plan(entries: list[tuple[int, str, str]], detected: int) -> SpeakerReconciliation:
    return SpeakerReconciliation(
        mapping=[
            _ReconcileMapEntry(clip_index=c, local_id=lid, global_id=g) for c, lid, g in entries
        ],
        detected_speakers=detected,
    )


def test_validator_passes_on_full_coverage():
    samples = [
        {"clip_index": 0, "local_id": "S1", "quotes": [], "word_count": 3},
        {"clip_index": 1, "local_id": "S1", "quotes": [], "word_count": 3},
    ]
    plan = _plan([(0, "S1", "S1"), (1, "S1", "S1")], detected=1)
    assert _validate_reconciliation(plan, samples, expected_speakers=2) == []


def test_validator_flags_missing_pairs():
    samples = [
        {"clip_index": 0, "local_id": "S1", "quotes": [], "word_count": 3},
        {"clip_index": 1, "local_id": "S1", "quotes": [], "word_count": 3},
    ]
    plan = _plan([(0, "S1", "S1")], detected=1)
    errors = _validate_reconciliation(plan, samples, expected_speakers=2)
    assert any("missing" in e for e in errors)


def test_validator_flags_out_of_range_globals():
    samples = [{"clip_index": 0, "local_id": "S1", "quotes": [], "word_count": 3}]
    plan = _plan([(0, "S1", "S9")], detected=1)
    errors = _validate_reconciliation(plan, samples, expected_speakers=2)
    assert any("outside S1..S2" in e for e in errors)


def test_validator_flags_non_s_ids():
    samples = [{"clip_index": 0, "local_id": "S1", "quotes": [], "word_count": 3}]
    plan = _plan([(0, "S1", "Host")], detected=1)
    errors = _validate_reconciliation(plan, samples, expected_speakers=1)
    assert any("S<number>" in e for e in errors)


def test_validator_flags_detected_out_of_range():
    samples = [{"clip_index": 0, "local_id": "S1", "quotes": [], "word_count": 3}]
    plan = _plan([(0, "S1", "S1")], detected=3)
    errors = _validate_reconciliation(plan, samples, expected_speakers=2)
    assert any("detected_speakers" in e for e in errors)


# ------------------------- orchestrator -----------------------------------


def test_reconcile_shortcuts_single_local_speaker():
    """One clip with one local speaker → trivial merge, no LLM call."""
    transcript = [_w("a", 0, "S1", 0.0), _w("b", 0, "S1", 1.0)]

    def forbid():  # pragma: no cover
        raise AssertionError("caller must not invoke the LLM")

    out, summary = reconcile_with_llm(
        transcript,
        expected_speakers=2,
        caller=forbid,
    )
    assert [w["speaker_id"] for w in out] == ["S1", "S1"]
    assert summary["detected_speakers"] == 1
    assert summary["roster"] == ["S1"]


def test_reconcile_merges_same_speaker_across_clips():
    """The classic v2-6 bug: 4 clips assign S1/S2/.../S4 independently but
    it's actually one person. Expected count=1 isn't allowed here (use
    collapse_to_solo), so simulate with expected=2 and a caller that returns
    a merging mapping."""
    transcript = [_w("a", 0, "S1", 0.0)] + [_w("b", 1, "S2", 10.0)] + [_w("c", 2, "S4", 20.0)]

    def fake_call() -> SpeakerReconciliation:
        return SpeakerReconciliation(
            mapping=[
                _ReconcileMapEntry(clip_index=0, local_id="S1", global_id="S1"),
                _ReconcileMapEntry(clip_index=1, local_id="S2", global_id="S1"),
                _ReconcileMapEntry(clip_index=2, local_id="S4", global_id="S1"),
            ],
            detected_speakers=1,
            reasoning="same voice across clips",
        )

    out, summary = reconcile_with_llm(
        transcript,
        expected_speakers=2,
        caller=fake_call,
    )
    assert [w["speaker_id"] for w in out] == ["S1", "S1", "S1"]
    assert summary["detected_speakers"] == 1
    assert summary["roster"] == ["S1"]


def test_reconcile_preserves_genuine_distinct_speakers():
    transcript = [
        _w("host_line", 0, "S1", 0.0),
        _w("guest_line", 0, "S2", 1.0),
        _w("host_2", 1, "S1", 10.0),
        _w("guest_2", 1, "S2", 11.0),
    ]

    def fake_call() -> SpeakerReconciliation:
        return SpeakerReconciliation(
            mapping=[
                _ReconcileMapEntry(clip_index=0, local_id="S1", global_id="S1"),
                _ReconcileMapEntry(clip_index=0, local_id="S2", global_id="S2"),
                _ReconcileMapEntry(clip_index=1, local_id="S1", global_id="S1"),
                _ReconcileMapEntry(clip_index=1, local_id="S2", global_id="S2"),
            ],
            detected_speakers=2,
        )

    out, summary = reconcile_with_llm(
        transcript,
        expected_speakers=2,
        caller=fake_call,
    )
    ids = [w["speaker_id"] for w in out]
    assert ids == ["S1", "S2", "S1", "S2"]
    assert summary["roster"] == ["S1", "S2"]


def test_reconcile_rejects_expected_less_than_two():
    with pytest.raises(ValueError, match="expected_speakers >= 2"):
        reconcile_with_llm([_w("a", 0, "S1", 0)], expected_speakers=1)


def test_reconcile_rejects_transcript_without_clip_index():
    transcript = [{"word": "a", "speaker_id": "S1", "start_time": 0, "end_time": 1}]
    with pytest.raises(ValueError, match="no clip_index"):
        reconcile_with_llm(transcript, expected_speakers=2)
