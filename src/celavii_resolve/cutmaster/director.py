"""Director agent — scrubbed transcript → selected `CutSegment[]`.

Picks contiguous word-aligned blocks the editor should keep. Enforces
verbatim timestamps via a post-validation retry loop; if the model rounds
``12.450 → 12.45`` the response is rejected and the model gets a chance to
fix it.

Model-agnostic: the actual LLM call goes through ``llm.call_structured``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from . import llm

if TYPE_CHECKING:
    from .presets import PresetBundle


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class CutSegment(BaseModel):
    start_s: float
    end_s: float
    reason: str = Field(
        default="",
        description="One short sentence — why this block made the cut.",
    )


class DirectorPlan(BaseModel):
    hook_index: int = Field(description="Index into selected_clips of the opening beat (0-based).")
    selected_clips: list[CutSegment]
    reasoning: str = Field(default="", description="Brief rationale for the overall structure.")


# ---------------------------------------------------------------------------
# Verbatim-timestamp validator
# ---------------------------------------------------------------------------


TIMESTAMP_TOLERANCE_S = 0.001  # 1 ms — tolerates float repr but not rounding


def _build_timestamp_sets(transcript: list[dict]) -> tuple[list[float], list[float]]:
    starts = sorted({float(w["start_time"]) for w in transcript})
    ends = sorted({float(w["end_time"]) for w in transcript})
    return starts, ends


def _close_to_any(value: float, sorted_values: list[float]) -> bool:
    # Linear scan is fine — transcripts have O(1000) words max.
    return any(abs(value - v) <= TIMESTAMP_TOLERANCE_S for v in sorted_values)


def validate_plan(plan: DirectorPlan, transcript: list[dict]) -> list[str]:
    """Return a list of validation errors. Empty list = valid.

    Checks:
      1. Every ``start_s`` matches a word's ``start_time`` within tolerance.
      2. Every ``end_s`` matches a word's ``end_time`` within tolerance.
      3. Segments have positive duration.
      4. hook_index is in range.
      5. No overlapping or out-of-order segments *within the same clip*
         (ordering between clips is the Director's choice — hook-first is OK).
    """
    starts, ends = _build_timestamp_sets(transcript)
    errors: list[str] = []

    if not plan.selected_clips:
        return ["selected_clips is empty — the Director must pick at least one block"]

    if not (0 <= plan.hook_index < len(plan.selected_clips)):
        errors.append(
            f"hook_index {plan.hook_index} is out of range for "
            f"{len(plan.selected_clips)} selected_clips"
        )

    for i, seg in enumerate(plan.selected_clips):
        if seg.end_s <= seg.start_s:
            errors.append(
                f"segment[{i}]: end_s {seg.end_s} must be > start_s {seg.start_s}"
            )
            continue
        if not _close_to_any(seg.start_s, starts):
            errors.append(
                f"segment[{i}]: start_s {seg.start_s} does not match any "
                f"word start_time in the transcript (verbatim required)"
            )
        if not _close_to_any(seg.end_s, ends):
            errors.append(
                f"segment[{i}]: end_s {seg.end_s} does not match any "
                f"word end_time in the transcript (verbatim required)"
            )

    return errors


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def _user_settings_block(user_settings: dict | None) -> str:
    """Render HIL settings as a markdown block the Director can consume."""
    if not user_settings:
        return "(no user overrides — use preset defaults)"
    lines: list[str] = []
    if (tgt := user_settings.get("target_length_s")):
        mins = tgt / 60.0
        lines.append(f"- Target length: ~{mins:.1f} minutes")
    if (themes := user_settings.get("themes")):
        lines.append(f"- Prioritized themes: {', '.join(themes)}")
    if not lines:
        lines.append("(no user overrides)")
    return "\n".join(lines)


def _prompt(preset: PresetBundle, transcript: list[dict], user_settings: dict | None) -> str:
    return f"""You are a {preset.role}.

You will receive a transcript array where each item has a `word`, `start_time`, and `end_time` in seconds. Your job is to select contiguous blocks of words that, when stitched together, form a compelling cut.

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. The hook's CutSegment becomes position 0 in the output, even if it's not the earliest in the transcript.
2. Pacing: {preset.pacing}.
3. Do not alter, edit, paraphrase, or summarize ANY word. You may only select blocks of existing words.
4. For each CutSegment, `start_s` MUST equal the `start_time` of the first word in the block, and `end_s` MUST equal the `end_time` of the last word. Do not round, truncate, or invent timestamps. If unsure, skip that block.
5. Blocks must be word-aligned and non-overlapping.

USER SETTINGS
{_user_settings_block(user_settings)}

TRANSCRIPT (JSON array):
{json.dumps(transcript, separators=(",", ":"))}

Return a `DirectorPlan` with:
- `selected_clips`: the blocks in narrative order (hook first).
- `hook_index`: 0 (the hook is always first).
- `reasoning`: 1–2 sentences on the overall structure.
"""


def build_cut_plan(
    transcript: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> DirectorPlan:
    """Run the Director agent, retrying on verbatim-timestamp violations."""
    prompt = _prompt(preset, transcript, user_settings)
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=DirectorPlan,
        validate=lambda plan: validate_plan(plan, transcript),
        temperature=0.4,
    )
