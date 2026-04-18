"""Gemini STT backend — preserved as the v1 default.

Uploads a WAV to Gemini Flash-Lite and asks for structured word-level
timestamps under ``TranscriptResponse``. Validated on audio ≤ 8 min;
longer audio truncates at the model's output-token budget (the reason
Deepgram was added as an alternative for long-form).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..config import get_gemini_client
from .stt import TranscriptResponse

log = logging.getLogger("celavii-resolve.cutmaster.stt_gemini")


DEFAULT_MODEL = os.environ.get("CELAVII_STT_MODEL", "gemini-3.1-flash-lite-preview")

PROMPT = (
    "You are an expert audio transcription engine. Analyze this audio file. "
    "Return a JSON object with a `words` array. Each item contains: `word` "
    "(string), `speaker_id` (string, e.g. 'S1'), `start_time` (seconds, 3 "
    "decimals), `end_time` (seconds, 3 decimals). Absolute precision required. "
    "Preserve punctuation as part of the word it belongs to."
)


def is_configured() -> bool:
    """``True`` when the Gemini client is available (API key + SDK)."""
    try:
        return get_gemini_client() is not None
    except Exception:
        return False


def transcribe(audio_path: Path, model: str | None = None) -> TranscriptResponse:
    """Gemini-backed implementation of :func:`stt.transcribe_audio`."""
    client = get_gemini_client()
    if client is None:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to .env or the environment before running."
        )

    from google.genai import types  # deferred — package is optional

    used_model = model or DEFAULT_MODEL
    log.info("Uploading %s to Gemini (%s)", audio_path.name, used_model)
    uploaded = client.files.upload(file=str(audio_path))

    response = client.models.generate_content(
        model=used_model,
        contents=[PROMPT, uploaded],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TranscriptResponse,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, TranscriptResponse):
        return parsed

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON: {exc}") from exc

    if isinstance(payload, list):
        payload = {"words": payload}
    try:
        return TranscriptResponse.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        raise RuntimeError(f"Gemini response failed schema validation: {exc}") from exc
