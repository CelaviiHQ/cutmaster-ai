"""Speech-to-text — provider-pluggable dispatch over a shared schema.

v1 validated Gemini (``gemini-3.1-flash-lite-preview``) with a Pydantic
``response_schema`` to kill shape ambiguity and string-vs-float timestamp
regressions on ~8-minute audio. Beyond ~8 min the model's output token
budget truncates per-word timestamps, which is why we've added Deepgram
Nova-3 as an alternative backend — no token caps, word-level timestamps
+ diarization in one pass, and pro-grade accuracy on long-form audio.

Provider selection (in order of precedence):

  1. The ``provider`` argument to :func:`transcribe_audio` (caller override).
  2. ``CELAVII_STT_PROVIDER`` env var.
  3. Default: ``"gemini"``.

Keeping the old ``transcribe_audio`` signature working is intentional —
existing callers (``pipeline._transcribe``, ``per_clip_stt._default_transcribe``)
don't need to learn about providers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger("celavii-resolve.cutmaster.stt")


def _resolve_default_provider() -> str:
    """Pick the default STT provider.

    Order:
      1. ``CELAVII_STT_PROVIDER`` env var (explicit opt-in wins).
      2. Deepgram if ``DEEPGRAM_API_KEY`` is set — empirically gives
         better long-form transcripts than Gemini STT.
      3. Gemini as the universal fallback.
    """
    explicit = os.environ.get("CELAVII_STT_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.environ.get("DEEPGRAM_API_KEY"):
        return "deepgram"
    return "gemini"


DEFAULT_PROVIDER = _resolve_default_provider()


# ---------------------------------------------------------------------------
# Shared schema — every provider lands here.
# ---------------------------------------------------------------------------


class TranscriptWord(BaseModel):
    word: str
    speaker_id: str = Field(default="S1")
    start_time: float
    end_time: float


class TranscriptResponse(BaseModel):
    words: list[TranscriptWord]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def transcribe_audio(
    audio_path: Path,
    model: str | None = None,
    provider: str | None = None,
) -> TranscriptResponse:
    """Transcribe ``audio_path`` through the chosen provider.

    Args:
        audio_path: Path to a local audio file.
        model: Optional provider-specific model override. Each backend
            documents its own default.
        provider: Optional override. Falls back to ``CELAVII_STT_PROVIDER``
            env var, then to Gemini.

    Raises:
        FileNotFoundError: the audio file is missing.
        ValueError: unknown provider, or the provider is missing its API
            key / SDK.
        RuntimeError: the provider responded but the payload didn't match
            the ``TranscriptResponse`` schema.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))

    chosen = (provider or DEFAULT_PROVIDER).lower()
    if chosen == "gemini":
        from .stt_gemini import transcribe as _gemini_transcribe

        return _gemini_transcribe(audio_path, model)
    if chosen == "deepgram":
        from .stt_deepgram import transcribe as _deepgram_transcribe

        return _deepgram_transcribe(audio_path, model)
    raise ValueError(f"unknown STT provider '{chosen}'. Valid: 'gemini', 'deepgram'.")


def available_providers() -> dict[str, bool]:
    """Report which providers are configured. Surfaces in the UI / logs.

    Each provider self-reports via its module's ``is_configured()`` so we
    don't import optional dependencies at module-load time.
    """
    status: dict[str, bool] = {}
    try:
        from .stt_gemini import is_configured as _gemini_ok

        status["gemini"] = _gemini_ok()
    except Exception:
        status["gemini"] = False
    try:
        from .stt_deepgram import is_configured as _deepgram_ok

        status["deepgram"] = _deepgram_ok()
    except Exception:
        status["deepgram"] = False
    return status
