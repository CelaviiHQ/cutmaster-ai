"""Speech-to-text via Gemini Flash — word-level timestamps with schema enforcement.

Phase 0 (v0_gemini_precision.py) locked the model to
``gemini-3.1-flash-lite-preview`` with a Pydantic ``response_schema`` to
eliminate shape ambiguity (wrapped object vs bare array) and string-vs-float
timestamp inconsistency.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import get_gemini_client

log = logging.getLogger("celavii-resolve.cutmaster.stt")

DEFAULT_MODEL = os.environ.get("CELAVII_STT_MODEL", "gemini-3.1-flash-lite-preview")

PROMPT = (
    "You are an expert audio transcription engine. Analyze this audio file. "
    "Return a JSON object with a `words` array. Each item contains: `word` "
    "(string), `speaker_id` (string, e.g. 'S1'), `start_time` (seconds, 3 "
    "decimals), `end_time` (seconds, 3 decimals). Absolute precision required. "
    "Preserve punctuation as part of the word it belongs to."
)


class TranscriptWord(BaseModel):
    word: str
    speaker_id: str = Field(default="S1")
    start_time: float
    end_time: float


class TranscriptResponse(BaseModel):
    words: list[TranscriptWord]


def transcribe_audio(audio_path: Path, model: str | None = None) -> TranscriptResponse:
    """Upload ``audio_path`` to Gemini and return a schema-validated transcript.

    Raises:
        ValueError: GEMINI_API_KEY not set.
        FileNotFoundError: audio_path missing.
        RuntimeError: Gemini returned a response that failed schema validation.
    """
    client = get_gemini_client()
    if client is None:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to .env or the environment before running."
        )

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))

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

    # Prefer the SDK's parsed Pydantic object when available
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, TranscriptResponse):
        return parsed

    # Fallback: raw JSON → validate
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
