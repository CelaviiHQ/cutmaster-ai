"""Auto-detect preset from the first chunk of transcript.

Returns a ``PresetRecommendation`` the UI shows to the user. They can accept
or override in the Configure screen.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ...intelligence import llm
from ..data.presets import PRESETS, Preset

WINDOW_SECONDS = 300.0  # analyse the first 5 min


class PresetRecommendation(BaseModel):
    preset: Preset = Field(description="Recommended preset key.")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="1–2 sentences on why this preset fits.")


def _prompt(transcript: list[dict]) -> str:
    choices = "\n".join(
        f"  - {key}: {bundle.label} — {bundle.role}" for key, bundle in PRESETS.items()
    )
    # Truncate — only need the first window_seconds of audio, stripped to words only
    head = [
        {"t": round(w["start_time"], 2), "w": w["word"]}
        for w in transcript
        if w["start_time"] <= WINDOW_SECONDS
    ]
    return f"""Classify the following transcript into ONE of the content-type presets below. The recommendation will be shown to the user as a suggestion — they can override, so do your best guess with a calibrated confidence.

PRESETS:
{choices}

TRANSCRIPT (first {WINDOW_SECONDS:.0f} seconds, word list):
{json.dumps(head, separators=(",", ":"))}

Return a `PresetRecommendation` with:
- `preset`: one of {sorted(PRESETS)}.
- `confidence`: 0.0–1.0. Use ≥0.8 only when the content type is unambiguous.
- `reasoning`: 1–2 sentences.
"""


def detect_preset(transcript: list[dict]) -> PresetRecommendation:
    """Classify a transcript into a preset recommendation."""
    prompt = _prompt(transcript)
    rec = llm.call_structured(
        agent="autodetect",
        prompt=prompt,
        response_schema=PresetRecommendation,
        temperature=0.2,
    )
    # Guard: if model hallucinates a key, degrade to vlog with low confidence.
    if rec.preset not in PRESETS:
        return PresetRecommendation(
            preset="vlog",
            confidence=0.0,
            reasoning=f"(model returned unknown preset '{rec.preset}' — defaulted to vlog)",
        )
    return rec
