"""Deepgram STT backend — Nova-3 with word-level timestamps + diarization.

Why Deepgram exists here: Gemini Flash-Lite truncates word-level output
past ~8-minute audio because the per-call output token budget caps at
~8k tokens. 9 000 words of verbose JSON doesn't fit. Deepgram's
streaming pre-recorded endpoint has no such cap — it returns the full
word array regardless of source length, and bundles speaker diarization
in the same response so we skip a whole reconciliation step on
interview-style content.

This module intentionally uses raw :mod:`httpx` instead of the
``deepgram-sdk`` package — one less optional dep, the REST surface is
tiny, and we already ship httpx via FastAPI's test client.

Configuration (env vars):

- ``DEEPGRAM_API_KEY`` — required.
- ``CELAVII_DEEPGRAM_MODEL`` — optional, defaults to ``"nova-3"``.
- ``CELAVII_DEEPGRAM_LANGUAGE`` — optional, defaults to ``"en"``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import TranscriptResponse, TranscriptWord

log = logging.getLogger("celavii-resolve.cutmaster.stt_deepgram")


DEFAULT_MODEL = os.environ.get("CELAVII_DEEPGRAM_MODEL", "nova-3")
DEFAULT_LANGUAGE = os.environ.get("CELAVII_DEEPGRAM_LANGUAGE", "en")
API_URL = "https://api.deepgram.com/v1/listen"
# Pre-recorded endpoints are compute-bound; 45-min interviews settle
# well under a minute in practice, but Deepgram occasionally queues —
# give the request room without hanging a run forever.
REQUEST_TIMEOUT_S = 300.0


def is_configured() -> bool:
    """``True`` when ``DEEPGRAM_API_KEY`` is set (SDK not required)."""
    return bool(os.environ.get("DEEPGRAM_API_KEY"))


def transcribe(
    audio_path: Path,
    model: str | None = None,
    *,
    language: str | None = None,
    diarize: bool = True,
    smart_format: bool = True,
) -> TranscriptResponse:
    """Deepgram-backed implementation of :func:`stt.transcribe_audio`."""
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY not set. Add it to .env or the environment.")

    import httpx  # deferred — kept out of module import for lightweight CLI use

    used_model = model or DEFAULT_MODEL
    used_language = language or DEFAULT_LANGUAGE
    log.info(
        "Sending %s to Deepgram (%s, lang=%s, diarize=%s)",
        audio_path.name,
        used_model,
        used_language,
        diarize,
    )

    params: dict[str, str] = {
        "model": used_model,
        "language": used_language,
        "punctuate": "true",
        "smart_format": "true" if smart_format else "false",
        "diarize": "true" if diarize else "false",
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/wav",
    }

    with open(audio_path, "rb") as f:
        body = f.read()

    try:
        response = httpx.post(
            API_URL,
            params=params,
            headers=headers,
            content=body,
            timeout=REQUEST_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Deepgram request failed: {exc}") from exc

    if response.status_code >= 400:
        # Deepgram returns JSON error bodies that are concise and user-
        # facing — surface the message verbatim.
        raise RuntimeError(f"Deepgram {response.status_code}: {response.text.strip()[:500]}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Deepgram returned non-JSON: {exc}") from exc

    words = _map_deepgram_words(payload)
    if not words:
        raise RuntimeError(
            "Deepgram response carried no word-level timestamps; check the "
            "audio file and language setting."
        )
    return TranscriptResponse(words=words)


def _map_deepgram_words(payload: dict) -> list[TranscriptWord]:
    """Extract the first-channel alternative's word list into our schema.

    Deepgram payload shape (simplified)::

        {
          "results": {
            "channels": [
              {
                "alternatives": [
                  {
                    "words": [
                      {"word": "hi", "start": 0.1, "end": 0.4,
                       "punctuated_word": "Hi,", "speaker": 0, ...},
                      ...
                    ]
                  }
                ]
              }
            ]
          }
        }

    Notes:
    - Prefer ``punctuated_word`` when ``smart_format=true`` so casing /
      punctuation land on the stored transcript (the Director prompt
      renders these verbatim).
    - ``speaker`` is 0-indexed; we surface it as ``"S{n+1}"`` so rosters
      align with Gemini's ``S1`` / ``S2`` convention.
    """
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    if not channels:
        return []

    alternatives = channels[0].get("alternatives") or []
    if not alternatives:
        return []

    raw_words = alternatives[0].get("words") or []
    out: list[TranscriptWord] = []
    for w in raw_words:
        spoken = w.get("punctuated_word") or w.get("word") or ""
        if not spoken:
            continue
        speaker = w.get("speaker")
        if speaker is None:
            speaker_id = "S1"
        else:
            try:
                speaker_id = f"S{int(speaker) + 1}"
            except (TypeError, ValueError):
                speaker_id = "S1"
        try:
            start = float(w["start"])
            end = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        out.append(
            TranscriptWord(
                word=spoken,
                speaker_id=speaker_id,
                start_time=start,
                end_time=end,
            )
        )
    return out
