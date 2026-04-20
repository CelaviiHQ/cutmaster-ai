"""v4 Phase 4.3 tests — Layer Audio DSP cues + Director injection.

ffmpeg is NOT invoked in these tests; we exercise the pure-arithmetic
parts (pause derivation, silence-tail detection, RMS mean, cue
composition) and the metadata parsers (fed canned ffmpeg output
strings) plus the Director prompt rendering.

Real ffmpeg behaviour is covered by the pipeline-level integration
path (hit via Resolve + a real WAV) and by the v4 acceptance run — not
scoped here.
"""

from __future__ import annotations

import json

import pytest

from celavii_resolve.cutmaster.analysis import audio_cues
from celavii_resolve.cutmaster.analysis.audio_cues import (
    _parse_rms_envelope,
    _silence_tails,
    _word_pauses,
    _word_rms_means,
    attach_cues_to_transcript,
    derive_cues,
    summarise_cues,
)
from celavii_resolve.cutmaster.core import director
from celavii_resolve.cutmaster.data.presets import get_preset

# ---------------------------------------------------------------------------
# Pure arithmetic
# ---------------------------------------------------------------------------


def _w(start: float, end: float, word: str = "w") -> dict:
    return {"word": word, "start_time": start, "end_time": end, "speaker_id": "S1"}


def test_word_pauses_first_and_last_have_zero_outside_pauses():
    words = [_w(0.0, 0.3, "hi"), _w(0.4, 0.7, "there"), _w(1.0, 1.4, "you")]
    pauses = _word_pauses(words)
    # First word has no prior → pause_before=0.
    assert pauses[0][0] == 0
    # Last word has no successor → pause_after=0.
    assert pauses[-1][1] == 0


def test_word_pauses_compute_gaps_in_ms():
    words = [_w(0.0, 0.3), _w(0.9, 1.2)]
    pauses = _word_pauses(words)
    # 0.9 - 0.3 = 0.6s = 600ms
    assert pauses[0][1] == 600  # pause_after of first word
    assert pauses[1][0] == 600  # pause_before of second word


def test_word_pauses_clamps_negative_gaps_to_zero():
    """Overlapping words (STT rare edge case) shouldn't produce negative pauses."""
    words = [_w(0.0, 1.0), _w(0.5, 1.5)]
    pauses = _word_pauses(words)
    assert pauses[1][0] == 0  # would be -500ms → clamped


def test_word_pauses_tolerates_malformed_timestamps():
    words = [{"word": "x"}, _w(0.0, 0.3)]
    pauses = _word_pauses(words)
    assert pauses[0] == (0, 0)
    # Second word's pause_before skips the malformed prior cleanly.
    assert isinstance(pauses[1][0], int)


def test_silence_tails_matches_region_within_grace():
    words = [_w(0.0, 1.0), _w(2.0, 3.0)]
    # Silence from 1.05s to 1.55s = 500ms qualifies (≥ 400ms min_tail).
    silences = [(1.05, 1.55)]
    tails = _silence_tails(words, silences)
    assert tails == [True, False]


def test_silence_tails_rejects_when_silence_starts_late():
    words = [_w(0.0, 1.0)]
    # Silence starts 500ms after word end → outside grace window.
    silences = [(1.5, 2.5)]
    assert _silence_tails(words, silences) == [False]


def test_silence_tails_rejects_when_duration_too_short():
    words = [_w(0.0, 1.0)]
    # Starts on time but only 200ms long.
    silences = [(1.05, 1.25)]
    assert _silence_tails(words, silences) == [False]


def test_word_rms_means_averages_overlapping_chunks():
    words = [_w(0.0, 0.3), _w(0.5, 0.9)]
    envelope = [(0.0, -20.0), (0.1, -18.0), (0.2, -22.0), (0.5, -10.0), (0.8, -12.0)]
    means = _word_rms_means(words, envelope)
    assert means[0] == pytest.approx(-20.0)  # mean of -20, -18, -22
    assert means[1] == pytest.approx(-11.0)  # mean of -10, -12


def test_word_rms_means_returns_none_when_no_overlap():
    words = [_w(10.0, 11.0)]
    envelope = [(0.0, -20.0), (1.0, -18.0)]
    assert _word_rms_means(words, envelope) == [None]


def test_derive_cues_composes_pauses_silence_rms_delta():
    words = [_w(0.0, 0.3, "alpha"), _w(1.0, 1.5, "beta"), _w(2.0, 2.5, "gamma")]
    # 450ms silence — safely above the 400ms floor without float-arithmetic
    # pitfalls (1.95 - 1.55 drifts below 0.4 due to IEEE-754 subtraction).
    silences = [(1.55, 2.0)]
    envelope = [(0.1, -20.0), (1.2, -15.0), (2.1, -25.0)]
    cues = derive_cues(words, silences, envelope)

    assert len(cues) == 3
    assert cues[0]["pause_before_ms"] == 0
    assert cues[0]["pause_after_ms"] == 700  # 1.0 - 0.3
    assert cues[1]["is_silence_tail"] is True
    assert cues[2]["is_silence_tail"] is False
    # rms deltas: 0 → (-20), 1 → -15-(-20)=+5, 2 → -25-(-15)=-10
    assert cues[0]["rms_db_delta"] == 0.0  # no prior to diff against
    assert cues[1]["rms_db_delta"] == pytest.approx(5.0)
    assert cues[2]["rms_db_delta"] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Metadata parsing (canned ffmpeg output)
# ---------------------------------------------------------------------------


def test_parse_rms_envelope_pairs_time_with_rms():
    sample = """
frame:0    pts:0 pts_time:0.000000
lavfi.astats.Overall.RMS_level=-22.08
frame:1    pts:1600 pts_time:0.100000
lavfi.astats.Overall.RMS_level=-21.50
frame:2    pts:3200 pts_time:0.200000
lavfi.astats.Overall.RMS_level=-25.10
"""
    env = _parse_rms_envelope(sample)
    assert env == [
        (0.0, -22.08),
        (0.1, -21.5),
        (0.2, -25.1),
    ]


def test_parse_rms_envelope_normalises_minus_inf():
    sample = """
frame:0    pts:0 pts_time:0.000000
lavfi.astats.Overall.RMS_level=-inf
frame:1    pts:1600 pts_time:0.100000
lavfi.astats.Overall.RMS_level=nan
"""
    env = _parse_rms_envelope(sample)
    assert env[0] == (0.0, -100.0)
    assert env[1] == (0.1, -100.0)


def test_parse_rms_envelope_skips_frames_without_rms():
    """A frame line with no following RMS line must not hallucinate a value."""
    sample = """
frame:0    pts:0 pts_time:0.000000
frame:1    pts:1600 pts_time:0.100000
lavfi.astats.Overall.RMS_level=-20.0
"""
    env = _parse_rms_envelope(sample)
    assert env == [(0.1, -20.0)]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_roundtrip(tmp_path, monkeypatch):
    """Canonical cache load/save path under a monkey-patched cache root."""
    monkeypatch.setattr(audio_cues, "CACHE_ROOT", tmp_path)
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"RIFFfake" + b"\x00" * 100)
    cues = [
        {"pause_before_ms": 0, "pause_after_ms": 100, "rms_db_delta": 0.0, "is_silence_tail": False}
    ]
    audio_cues._save_cached(wav, cues)
    loaded = audio_cues._load_cached(wav)
    assert loaded == cues


def test_cache_invalidates_when_wav_rewrites(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_cues, "CACHE_ROOT", tmp_path)
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"v1" + b"\x00" * 10)
    audio_cues._save_cached(wav, [{"pause_before_ms": 1}])
    # Rewrite the WAV — size changes → cache key changes → old entry doesn't hit.
    wav.write_bytes(b"v2" + b"\x00" * 50)
    assert audio_cues._load_cached(wav) is None


def test_cache_roundtrip_survives_json_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_cues, "CACHE_ROOT", tmp_path)
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"data")
    cue_list = [{"pause_before_ms": 100, "pause_after_ms": 200}]
    audio_cues._save_cached(wav, cue_list)
    path = audio_cues._cache_path(wav)
    payload = json.loads(path.read_text())
    assert payload["wav_path"] == str(wav)
    assert payload["cues"] == cue_list


# ---------------------------------------------------------------------------
# Orchestrator / transcript attachment / summary
# ---------------------------------------------------------------------------


def test_compute_audio_cues_falls_back_to_pause_only(tmp_path, monkeypatch):
    """When ffmpeg can't be invoked, compute_audio_cues still returns pauses."""
    monkeypatch.setattr(audio_cues, "CACHE_ROOT", tmp_path)
    # Stub the ffmpeg-dependent probes to return empty lists (simulating
    # ffmpeg failures without monkey-patching subprocess).
    monkeypatch.setattr(audio_cues, "probe_silences", lambda *a, **k: [])
    monkeypatch.setattr(audio_cues, "probe_rms_envelope", lambda *a, **k: [])
    wav = tmp_path / "silent.wav"
    wav.write_bytes(b"RIFFfake")
    words = [_w(0.0, 0.5), _w(1.5, 2.0)]
    cues = audio_cues.compute_audio_cues(wav, words, use_cache=False)
    assert len(cues) == 2
    assert cues[1]["pause_before_ms"] == 1000
    assert cues[0]["is_silence_tail"] is False  # no silence signal
    assert cues[0]["rms_db_delta"] == 0.0  # no envelope


def test_attach_cues_mutation_safety():
    words = [_w(0.0, 0.3, "alpha")]
    cues = [
        {"pause_before_ms": 0, "pause_after_ms": 100, "rms_db_delta": 0.0, "is_silence_tail": False}
    ]
    out = attach_cues_to_transcript(words, cues)
    assert out[0]["audio_cue"]["pause_after_ms"] == 100
    # Original input not mutated.
    assert "audio_cue" not in words[0]


def test_attach_cues_length_mismatch_returns_transcript_unchanged():
    words = [_w(0.0, 0.3), _w(0.4, 0.7)]
    cues = [{"pause_before_ms": 0}]  # too short
    out = attach_cues_to_transcript(words, cues)
    assert "audio_cue" not in out[0]
    assert len(out) == 2


def test_summarise_cues_counts_significant_signals():
    cues = [
        {"pause_before_ms": 800, "pause_after_ms": 0, "rms_db_delta": 0.0, "is_silence_tail": True},
        {
            "pause_before_ms": 0,
            "pause_after_ms": 100,
            "rms_db_delta": -6.0,
            "is_silence_tail": False,
        },
        {"pause_before_ms": 0, "pause_after_ms": 0, "rms_db_delta": 0.0, "is_silence_tail": False},
    ]
    summary = summarise_cues(cues)
    assert summary == {
        "words_total": 3,
        "significant_pause_hits": 1,  # first word's pause_before=800
        "silence_tail_hits": 1,
        "rms_delta_hits": 1,  # second word's -6dB delta
    }


# ---------------------------------------------------------------------------
# Director prompt block
# ---------------------------------------------------------------------------


def _cued(start: float, end: float, word: str, cue: dict) -> dict:
    w = _w(start, end, word)
    w["audio_cue"] = cue
    return w


def test_audio_cue_block_empty_without_cues():
    words = [_w(0.0, 0.3)]
    assert director._audio_cue_block(words) == ""


def test_audio_cue_block_shows_only_significant_cues():
    words = [
        # Significant — long pause_after + silence tail.
        _cued(
            0.0,
            0.5,
            "okay",
            {
                "pause_before_ms": 0,
                "pause_after_ms": 620,
                "rms_db_delta": 0.0,
                "is_silence_tail": True,
            },
        ),
        # Not significant — everything under floor.
        _cued(
            1.0,
            1.3,
            "and",
            {
                "pause_before_ms": 200,
                "pause_after_ms": 100,
                "rms_db_delta": 0.5,
                "is_silence_tail": False,
            },
        ),
        # Significant — hard reset pause_before.
        _cued(
            3.0,
            3.4,
            "wait",
            {
                "pause_before_ms": 1250,
                "pause_after_ms": 0,
                "rms_db_delta": -5.2,
                "is_silence_tail": False,
            },
        ),
    ]
    block = director._audio_cue_block(words)
    assert "AUDIO CUES" in block
    assert '"okay"' in block
    assert '"wait"' in block
    # Non-significant word must not appear.
    assert '"and"' not in block
    # Reason annotations are present.
    assert "natural endpoint" in block
    assert "hard reset" in block


def test_audio_cue_block_mode_assembled_footer():
    words = [
        _cued(
            0.0,
            0.3,
            "x",
            {
                "pause_before_ms": 900,
                "pause_after_ms": 0,
                "rms_db_delta": 0.0,
                "is_silence_tail": False,
            },
        ),
    ]
    block_assembled = director._audio_cue_block(words, mode="assembled")
    block_generic = director._audio_cue_block(words, mode="raw_dump")
    assert "Assembled mode" in block_assembled
    assert "Assembled mode" not in block_generic


def test_audio_cue_block_mode_short_generator_footer():
    words = [
        _cued(
            0.0,
            0.3,
            "x",
            {
                "pause_before_ms": 0,
                "pause_after_ms": 700,
                "rms_db_delta": 0.0,
                "is_silence_tail": True,
            },
        ),
    ]
    block = director._audio_cue_block(words, mode="short_generator")
    assert "Short Generator" in block


def test_audio_cue_block_truncates_overflow(monkeypatch):
    monkeypatch.setattr(director, "_AUDIO_CUE_MAX_ROWS", 2)
    words = [
        _cued(
            i,
            i + 0.2,
            f"w{i}",
            {
                "pause_before_ms": 900,
                "pause_after_ms": 0,
                "rms_db_delta": 0.0,
                "is_silence_tail": False,
            },
        )
        for i in range(5)
    ]
    block = director._audio_cue_block(words)
    assert "... 3 more cues omitted" in block


def test_slim_strips_audio_cue_alongside_shot_tag():
    w = _cued(
        0.0,
        0.3,
        "hi",
        {
            "pause_before_ms": 0,
            "pause_after_ms": 100,
            "rms_db_delta": 0.0,
            "is_silence_tail": False,
        },
    )
    w["shot_tag"] = {"shot_type": "closeup"}
    slimmed = director._slim_transcript_for_prompt([w])
    assert "audio_cue" not in slimmed[0]
    assert "shot_tag" not in slimmed[0]
    # Original untouched.
    assert "audio_cue" in w


@pytest.mark.parametrize(
    "builder",
    ["flat_prompt", "clip_hunter", "short_generator", "assembled", "curated", "rough_cut"],
)
def test_audio_cue_block_injected_into_every_builder(builder):
    preset = get_preset("vlog")
    cue = {
        "pause_before_ms": 0,
        "pause_after_ms": 900,
        "rms_db_delta": 0.0,
        "is_silence_tail": True,
    }
    words = [
        _cued(0.0, 0.3, "alpha", cue),
        _w(0.5, 0.8, "beta"),
    ]

    if builder == "flat_prompt":
        prompt = director._prompt(preset, words, user_settings={})
    elif builder == "clip_hunter":
        prompt = director._clip_hunter_prompt(
            preset, words, user_settings={}, target_clip_length_s=30.0, num_clips=3
        )
    elif builder == "short_generator":
        prompt = director._short_generator_prompt(
            preset, words, user_settings={}, target_short_length_s=45.0, num_shorts=2
        )
    elif builder == "assembled":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        prompt = director._assembled_prompt(preset, takes, user_settings={})
    elif builder == "curated":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        prompt = director._curated_prompt(preset, takes, user_settings={})
    elif builder == "rough_cut":
        takes = [
            {
                "item_index": 0,
                "source_name": "a",
                "start_s": 0.0,
                "end_s": 1.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        groups = [{"group_id": "g0", "item_indexes": [0], "signal": "color"}]
        prompt = director._rough_cut_prompt(preset, takes, groups, user_settings={})
    else:
        pytest.fail(f"unknown builder {builder}")

    assert "AUDIO CUES" in prompt
    assert '"alpha"' in prompt
    # The JSON-serialised transcript / takes must NOT carry audio_cue —
    # the slimmer is responsible for stripping it.
    assert '"audio_cue"' not in prompt


def test_prompt_without_audio_cues_omits_block():
    preset = get_preset("vlog")
    words = [_w(0.0, 0.3), _w(0.5, 0.8)]  # no audio_cue
    prompt = director._prompt(preset, words, user_settings={})
    assert "AUDIO CUES" not in prompt


# Mode-aware footer guidance lives on _audio_cue_block; the flat_prompt
# and clip_hunter builders pick "raw_dump" / "clip_hunter" — verify
# their prompts don't carry the Assembled-specific sentence.
def test_raw_dump_prompt_uses_generic_audio_footer():
    preset = get_preset("vlog")
    words = [
        _cued(
            0.0,
            0.3,
            "x",
            {
                "pause_before_ms": 900,
                "pause_after_ms": 0,
                "rms_db_delta": 0.0,
                "is_silence_tail": False,
            },
        ),
    ]
    prompt = director._prompt(preset, words, user_settings={})
    assert "AUDIO CUES" in prompt
    assert "Assembled mode" not in prompt
    assert "Short Generator" not in prompt


def test_assembled_prompt_uses_assembled_footer():
    preset = get_preset("vlog")
    words = [
        _cued(
            0.0,
            0.3,
            "x",
            {
                "pause_before_ms": 900,
                "pause_after_ms": 0,
                "rms_db_delta": 0.0,
                "is_silence_tail": False,
            },
        ),
    ]
    takes = [
        {
            "item_index": 0,
            "source_name": "a",
            "start_s": 0.0,
            "end_s": 1.0,
            "transcript": [dict(w, i=i) for i, w in enumerate(words)],
        }
    ]
    prompt = director._assembled_prompt(preset, takes, user_settings={})
    assert "Assembled mode" in prompt
