"""Tests for the Deepgram STT backend mapper + dispatch."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from cutmaster_ai.cutmaster import stt as stt_module
from cutmaster_ai.cutmaster.stt import TranscriptResponse, available_providers
from cutmaster_ai.cutmaster.stt.deepgram import _map_deepgram_words, is_configured

# ------------------------- mapper ----------------------------------------


def test_mapper_extracts_words_from_first_alternative():
    payload = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {
                                    "word": "hello",
                                    "punctuated_word": "Hello,",
                                    "start": 0.1,
                                    "end": 0.4,
                                    "speaker": 0,
                                },
                                {
                                    "word": "world",
                                    "punctuated_word": "world.",
                                    "start": 0.5,
                                    "end": 0.9,
                                    "speaker": 0,
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    }
    words = _map_deepgram_words(payload)
    assert len(words) == 2
    # Prefer punctuated form — Director prompt renders verbatim.
    assert words[0].word == "Hello,"
    assert words[0].speaker_id == "S1"
    assert words[0].start_time == 0.1
    assert words[0].end_time == 0.4


def test_mapper_shifts_speaker_indices_one_based():
    """Deepgram speaker ids are 0-indexed; we surface S1..SN to match Gemini."""
    payload = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {"word": "a", "start": 0.0, "end": 0.2, "speaker": 0},
                                {"word": "b", "start": 0.3, "end": 0.5, "speaker": 1},
                                {"word": "c", "start": 0.6, "end": 0.8, "speaker": 2},
                            ],
                        }
                    ],
                }
            ],
        },
    }
    words = _map_deepgram_words(payload)
    assert [w.speaker_id for w in words] == ["S1", "S2", "S3"]


def test_mapper_defaults_to_s1_when_speaker_missing():
    """Diarization off → Deepgram omits `speaker`. Default to single-speaker."""
    payload = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {"word": "solo", "start": 0.0, "end": 0.4},
                            ],
                        }
                    ],
                }
            ],
        },
    }
    words = _map_deepgram_words(payload)
    assert len(words) == 1
    assert words[0].speaker_id == "S1"


def test_mapper_drops_zero_duration_words():
    payload = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {"word": "a", "start": 0.0, "end": 0.2},
                                {"word": "b", "start": 0.3, "end": 0.3},  # zero dur
                                {"word": "c", "start": 0.5, "end": 0.4},  # inverted
                            ],
                        }
                    ],
                }
            ],
        },
    }
    words = _map_deepgram_words(payload)
    assert [w.word for w in words] == ["a"]


def test_mapper_returns_empty_on_empty_payload():
    assert _map_deepgram_words({}) == []
    assert _map_deepgram_words({"results": {}}) == []
    assert _map_deepgram_words({"results": {"channels": []}}) == []
    assert (
        _map_deepgram_words(
            {
                "results": {"channels": [{"alternatives": []}]},
            }
        )
        == []
    )


# ------------------------- configuration ---------------------------------


def test_is_configured_reads_env_var(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert is_configured() is False
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    assert is_configured() is True


def test_available_providers_reports_both(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    # Gemini's is_configured reaches into the real client; patch to
    # a deterministic True.
    with patch(
        "cutmaster_ai.cutmaster.stt.gemini.is_configured",
        return_value=True,
    ):
        status = available_providers()
    assert status == {"gemini": True, "deepgram": True}


# ------------------------- dispatch --------------------------------------


def test_dispatch_raises_on_unknown_provider(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    with pytest.raises(ValueError, match="unknown STT provider"):
        stt_module.transcribe_audio(audio, provider="martian")


def test_dispatch_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        stt_module.transcribe_audio("/nonexistent/audio.wav", provider="gemini")


def test_dispatch_routes_to_deepgram_when_selected(tmp_path, monkeypatch):
    """End-to-end dispatch: a provider='deepgram' call must invoke the
    Deepgram module (not the Gemini one)."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    calls: dict[str, str] = {}

    def fake_deepgram(path, model=None, **kw):
        calls["path"] = str(path)
        return TranscriptResponse(words=[])

    def forbidden(*_a, **_k):  # pragma: no cover - guards regression
        raise AssertionError("gemini path must not run when provider=deepgram")

    monkeypatch.setattr(
        "cutmaster_ai.cutmaster.stt.deepgram.transcribe",
        fake_deepgram,
    )
    monkeypatch.setattr(
        "cutmaster_ai.cutmaster.stt.gemini.transcribe",
        forbidden,
    )

    resp = stt_module.transcribe_audio(audio, provider="deepgram")
    assert isinstance(resp, TranscriptResponse)
    assert calls["path"] == str(audio)


def test_dispatch_falls_back_to_env_var(tmp_path, monkeypatch):
    from cutmaster_ai.cutmaster.stt import base as stt_base

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    monkeypatch.setenv("CUTMASTER_STT_PROVIDER", "deepgram")
    # transcribe_audio reads DEFAULT_PROVIDER as a module-level name from
    # base.py, so patching the package re-export wouldn't take effect.
    monkeypatch.setattr(stt_base, "DEFAULT_PROVIDER", "deepgram")

    calls: dict[str, str] = {}

    def fake_deepgram(path, model=None, **kw):
        calls["hit"] = str(path)
        return TranscriptResponse(words=[])

    monkeypatch.setattr(
        "cutmaster_ai.cutmaster.stt.deepgram.transcribe",
        fake_deepgram,
    )
    stt_module.transcribe_audio(audio)
    assert "hit" in calls


# ------------------------- network call (mocked) -------------------------


def test_transcribe_raises_when_api_key_missing(tmp_path, monkeypatch):
    from cutmaster_ai.cutmaster.stt import deepgram as stt_deepgram

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        stt_deepgram.transcribe(audio)


def test_transcribe_parses_good_response(tmp_path, monkeypatch):
    """Happy path through the real :func:`transcribe` — mock ``httpx.post``
    to return a well-formed Deepgram payload and ensure we get a clean
    ``TranscriptResponse`` out the other side."""
    from cutmaster_ai.cutmaster.stt import deepgram as stt_deepgram

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {
                                    "words": [
                                        {
                                            "word": "yes",
                                            "punctuated_word": "Yes.",
                                            "start": 0.1,
                                            "end": 0.5,
                                            "speaker": 0,
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                },
            }

    captured: dict[str, object] = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        captured["headers"] = kw.get("headers")
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    resp = stt_deepgram.transcribe(audio)
    assert isinstance(resp, TranscriptResponse)
    assert resp.words[0].word == "Yes."
    assert resp.words[0].speaker_id == "S1"
    assert captured["url"] == "https://api.deepgram.com/v1/listen"
    assert captured["params"]["model"] == "nova-3"
    assert captured["params"]["diarize"] == "true"
    assert captured["headers"]["Authorization"] == "Token dg_xxx"


def test_transcribe_surfaces_http_errors(tmp_path, monkeypatch):
    from cutmaster_ai.cutmaster.stt import deepgram as stt_deepgram

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    class _BadResponse:
        status_code = 401
        text = '{"err_code":"INVALID_AUTH"}'

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: _BadResponse())
    with pytest.raises(RuntimeError, match="Deepgram 401"):
        stt_deepgram.transcribe(audio)


def test_transcribe_rejects_empty_word_list(tmp_path, monkeypatch):
    from cutmaster_ai.cutmaster.stt import deepgram as stt_deepgram

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    class _EmptyResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {"results": {"channels": [{"alternatives": [{"words": []}]}]}}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: _EmptyResponse())
    with pytest.raises(RuntimeError, match="no word-level timestamps"):
        stt_deepgram.transcribe(audio)


# ------------------------- guard against env pollution -------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Some CI environments set these — null them unless the test sets them."""
    for key in ("CUTMASTER_STT_PROVIDER", "DEEPGRAM_API_KEY"):
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)
    yield
