"""STT (speech-to-text) subpackage.

Re-exports the base API so existing consumers can keep importing from
``cutmaster_ai.cutmaster.stt`` directly. Provider implementations live
in ``deepgram``, ``gemini``, etc.
"""

from .base import (
    DEFAULT_PROVIDER,
    TranscriptResponse,
    TranscriptWord,
    available_providers,
    transcribe_audio,
)

__all__ = [
    "DEFAULT_PROVIDER",
    "TranscriptResponse",
    "TranscriptWord",
    "available_providers",
    "transcribe_audio",
]
