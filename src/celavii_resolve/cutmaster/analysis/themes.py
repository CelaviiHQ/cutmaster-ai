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


HOOK_DEDUP_WINDOW_S = 1.0


def _normalize_analysis(analysis: StoryAnalysis) -> StoryAnalysis:
    """Harden Gemini's StoryAnalysis output: sort, dedupe, drop invalid rows.

    Gemini is mostly well-behaved here but occasionally returns duplicated
    hook quotes (same line, slightly different word-boundary timestamps) and
    chapter lists whose order drifts from strictly-chronological. The UI
    assumes both are sorted; downstream validators will also want non-
    overlapping chapters. Do this once at the boundary so every consumer
    gets a clean StoryAnalysis — cheap, deterministic, no extra LLM call.
    """
    # --- chapters: sort by start, drop zero/negative-duration, drop overlaps.
    chapters = sorted(
        (c for c in analysis.chapters if c.end_s > c.start_s),
        key=lambda c: c.start_s,
    )
    kept_chapters: list[Chapter] = []
    for ch in chapters:
        if kept_chapters and ch.start_s < kept_chapters[-1].end_s:
            # Overlap — clip the new one's start up to the previous end.
            if ch.end_s <= kept_chapters[-1].end_s:
                continue  # fully contained, drop
            ch = Chapter(start_s=kept_chapters[-1].end_s, end_s=ch.end_s, title=ch.title)
        kept_chapters.append(ch)

    # --- hooks: dedupe within HOOK_DEDUP_WINDOW_S, sort chronologically.
    sorted_hooks = sorted(analysis.hook_candidates, key=lambda h: h.start_s)
    kept_hooks: list[HookCandidate] = []
    for h in sorted_hooks:
        if kept_hooks and abs(h.start_s - kept_hooks[-1].start_s) < HOOK_DEDUP_WINDOW_S:
            # Keep whichever has the higher engagement score.
            if h.engagement_score > kept_hooks[-1].engagement_score:
                kept_hooks[-1] = h
            continue
        kept_hooks.append(h)

    return StoryAnalysis(
        chapters=kept_chapters,
        hook_candidates=kept_hooks,
        theme_candidates=list(dict.fromkeys(analysis.theme_candidates)),
    )


def analyze_themes(transcript: list[dict], preset: PresetBundle) -> StoryAnalysis:
    """Produce a `StoryAnalysis` for the Configure screen."""
    raw = llm.call_structured(
        agent="theme",
        prompt=_prompt(transcript, preset),
        response_schema=StoryAnalysis,
        temperature=0.3,
    )
    return _normalize_analysis(raw)
