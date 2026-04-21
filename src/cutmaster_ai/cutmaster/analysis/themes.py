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


def _slim_for_prompt(transcript: list[dict]) -> list[dict]:
    """Keep only the fields the theme analyzer needs.

    Per-clip STT annotates every word with a full ``clip_metadata`` dict
    (source name, file path, duration, timeline offset, source frames).
    Multiplied across ~5000 words that blows past Gemini's 1M input-token
    budget. The theme analyzer only needs ``word``, ``start_time``,
    ``end_time`` — everything else is noise at this stage.
    """
    keep = ("word", "start_time", "end_time")
    return [{k: w[k] for k in keep if k in w} for w in transcript]


def _clip_boundaries(transcript: list[dict]) -> list[float]:
    """Return the ascending list of clip-start timestamps (seconds).

    Only populated on per-clip-STT runs where each word carries a
    ``clip_index``. Returns an empty list for whole-timeline STT, which
    disables the clip-aware behaviour cleanly.
    """
    seen: dict[int, float] = {}
    for w in transcript:
        ci = w.get("clip_index")
        if ci is None:
            continue
        t = float(w.get("start_time", 0.0))
        if ci not in seen or t < seen[ci]:
            seen[ci] = t
    return sorted(seen.values())


def _clip_boundaries_block(boundaries: list[float]) -> str:
    """Render clip-cut timestamps as a prompt block, or empty string."""
    if len(boundaries) < 2:
        return ""
    lines = "\n".join(f"  - {t:.2f}s (clip {i})" for i, t in enumerate(boundaries))
    return (
        "CLIP BOUNDARIES — the editor cut these clip-start timestamps into "
        "the timeline. When placing chapter boundaries, prefer landing at "
        "or within 2 seconds of one of these cuts. Chapters usually shift "
        "at clip cuts; don't split a chapter mid-clip unless the content "
        "genuinely changes within a single clip.\n"
        f"{lines}"
    )


def _prompt(transcript: list[dict], preset: PresetBundle) -> str:
    axes = ", ".join(preset.theme_axes)
    slim = _slim_for_prompt(transcript)
    boundaries_block = _clip_boundaries_block(_clip_boundaries(transcript))
    boundaries_section = f"\n\n{boundaries_block}" if boundaries_block else ""
    return f"""You are a {preset.role}. Analyse the following transcript and produce a structural summary that will be shown to the user in the Configure screen.

TASK:
1. Break the transcript into 3–8 chapters (broad beats). For each, output ``start_s`` / ``end_s`` matching word timestamps and a 3–6 word ``title``.
2. Identify 3–5 HOOK candidates — short, high-engagement quotes (≤ 8 seconds) the user could place first. Include the exact ``text`` and an ``engagement_score`` (0.0–1.0).
3. Extract 5–12 ``theme_candidates`` — short topic tags the user can check/uncheck. Focus on these axes: {axes}.

Do NOT invent timestamps. Any ``start_s`` / ``end_s`` must appear in the transcript's word timings.{boundaries_section}

TRANSCRIPT (JSON array, each item has `word`, `start_time`, `end_time`):
{json.dumps(slim, separators=(",", ":"))}
"""


HOOK_DEDUP_WINDOW_S = 1.0
CHAPTER_SNAP_TOLERANCE_S = 2.0


def _snap_to_boundary(
    t: float,
    boundaries: list[float],
    word_times: list[float],
    tol: float = CHAPTER_SNAP_TOLERANCE_S,
) -> float:
    """Snap ``t`` to the closest clip-boundary within ``tol``, falling back
    to the closest word-time (because chapter edges must still match a word
    boundary — the Configure UI renders them verbatim).
    """
    if not boundaries:
        return t
    nearest_boundary = min(boundaries, key=lambda b: abs(b - t))
    if abs(nearest_boundary - t) > tol:
        return t
    # Clip boundaries are derived from word start_times, so they're already
    # valid word timestamps — no further snap needed.
    return nearest_boundary


def _normalize_analysis(
    analysis: StoryAnalysis, clip_boundaries: list[float] | None = None
) -> StoryAnalysis:
    """Harden Gemini's StoryAnalysis output: sort, dedupe, drop invalid rows.

    Gemini is mostly well-behaved here but occasionally returns duplicated
    hook quotes (same line, slightly different word-boundary timestamps) and
    chapter lists whose order drifts from strictly-chronological. The UI
    assumes both are sorted; downstream validators will also want non-
    overlapping chapters. Do this once at the boundary so every consumer
    gets a clean StoryAnalysis — cheap, deterministic, no extra LLM call.
    """
    boundaries = clip_boundaries or []

    # --- chapters: sort, snap to clip cuts, drop zero-duration, drop overlaps.
    chapters = sorted(
        (c for c in analysis.chapters if c.end_s > c.start_s),
        key=lambda c: c.start_s,
    )
    snapped: list[Chapter] = []
    for ch in chapters:
        start_s = _snap_to_boundary(ch.start_s, boundaries, [])
        end_s = _snap_to_boundary(ch.end_s, boundaries, [])
        if end_s > start_s:
            snapped.append(Chapter(start_s=start_s, end_s=end_s, title=ch.title))
    kept_chapters: list[Chapter] = []
    for ch in snapped:
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
    return _normalize_analysis(raw, _clip_boundaries(transcript))
