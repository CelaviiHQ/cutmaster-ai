"""Tier 3 — opening-sentence micro-classifier.

Rhetorical openers (``"Welcome back to the channel"``, ``"Thank you for
having me"``, ``"Today we're making"``) are near-deterministic preset
signals that structural metrics don't see. A ~200-token LLM call on the
first coalesced sentence catches them cheaply.

Gating lives in the cascade orchestrator: Tier 3 only fires when the
Tier 0-2 margin sits in the ambiguous band (see
:func:`scoring.is_ambiguous_band`). Outside that band Tier 3 is pure
overhead — confident picks don't need it, and low-confidence picks
should defer to Tier 4's full-band view.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ....intelligence import llm
from ...data.presets import Preset
from .scoring import NON_CLASSIFIABLE_PRESETS, PresetScores, empty_scores

log = logging.getLogger("cutmaster-ai.cutmaster.auto_detect.opening")


class OpeningClassification(BaseModel):
    """LLM response — one preset label and a self-rated confidence."""

    preset: Preset = Field(description="Which preset the opener sounds like.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0–1.0 model confidence. Use ≥0.7 only when the opener is unambiguous.",
    )


def _validate(parsed: OpeningClassification) -> list[str]:
    """Reject workflow / mode presets — Tier 3 only picks content types."""
    if parsed.preset in NON_CLASSIFIABLE_PRESETS:
        return [
            f"preset '{parsed.preset}' is a mode/workflow preset, not a content type — "
            "pick one of the auto-eligible presets instead"
        ]
    return []


def _prompt(sentence: str) -> str:
    return f"""Classify this opening sentence of a video into the most likely content-type preset.

Opening sentence:
"{sentence.strip()}"

Heuristics:
- "Welcome back to the channel" / "Hey guys" / "What's up" → vlog
- "Thank you for having me" / "It's great to be here" → interview (guest arrival)
- "Today we're making" / "Let me show you how to" → tutorial
- "Oh my god did you see" / "No way" / "Wait what" → reaction
- "Good afternoon everyone" / "It's a pleasure to be here" → presentation
- "Let me walk you through this product" / "Notice the difference" → product_demo
- Wedding / multi-speaker natural chatter → wedding or podcast

Return an `OpeningClassification` with your best single guess and a calibrated
confidence. Use ≥0.7 only when the opener is unambiguous; use ≤0.3 when the
sentence is generic (greeting, "hello", single word, etc.).
"""


def classify_opening_sentence(sentence: str) -> PresetScores:
    """Ask the micro-classifier which preset the opening sentence signals.

    Returns a ``PresetScores`` dict with the chosen preset weighted by
    confidence and the rest at 0. Malformed or empty input returns
    neutral zeros so the cascade never crashes on a blank opener.
    """
    if not sentence or not sentence.strip():
        return empty_scores()

    try:
        parsed = llm.call_structured(
            agent="autodetect",
            prompt=_prompt(sentence),
            response_schema=OpeningClassification,
            temperature=0.1,
            max_retries=2,
            validate=_validate,
            accept_best_effort=True,
        )
    except Exception as exc:
        log.warning("opening classifier failed (%s) — contributing neutral scores", exc)
        return empty_scores()

    if parsed.preset in NON_CLASSIFIABLE_PRESETS:
        return empty_scores()

    scores = empty_scores()
    if parsed.preset in scores:
        scores[parsed.preset] = float(parsed.confidence)
    return scores
