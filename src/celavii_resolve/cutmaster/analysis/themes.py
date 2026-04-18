"""Theme analysis — chapters + hook candidates + theme axes for the HIL step.

Runs between scrub and the configure screen. Cheap call: Gemini Flash Lite.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ...intelligence import llm

if TYPE_CHECKING:
    from ..data.presets import PresetBundle


class Chapter(BaseModel):
    start_s: float
    end_s: float
    title: str


class HookCandidate(BaseModel):
    start_s: float
    end_s: float
    text: str
    engagement_score: float = Field(ge=0.0, le=1.0)


class StoryAnalysis(BaseModel):
    chapters: list[Chapter]
    hook_candidates: list[HookCandidate]
    theme_candidates: list[str] = Field(
        description="Short phrases — editable topic tags the user checks/unchecks."
    )


def _prompt(transcript: list[dict], preset: PresetBundle) -> str:
    axes = ", ".join(preset.theme_axes)
    return f"""You are a {preset.role}. Analyse the following transcript and produce a structural summary that will be shown to the user in the Configure screen.

TASK:
1. Break the transcript into 3–8 chapters (broad beats). For each, output ``start_s`` / ``end_s`` matching word timestamps and a 3–6 word ``title``.
2. Identify 3–5 HOOK candidates — short, high-engagement quotes (≤ 8 seconds) the user could place first. Include the exact ``text`` and an ``engagement_score`` (0.0–1.0).
3. Extract 5–12 ``theme_candidates`` — short topic tags the user can check/uncheck. Focus on these axes: {axes}.

Do NOT invent timestamps. Any ``start_s`` / ``end_s`` must appear in the transcript's word timings.

TRANSCRIPT (JSON array, each item has `word`, `start_time`, `end_time`):
{json.dumps(transcript, separators=(",", ":"))}
"""


def analyze_themes(transcript: list[dict], preset: PresetBundle) -> StoryAnalysis:
    """Produce a `StoryAnalysis` for the Configure screen."""
    return llm.call_structured(
        agent="theme",
        prompt=_prompt(transcript, preset),
        response_schema=StoryAnalysis,
        temperature=0.3,
    )
