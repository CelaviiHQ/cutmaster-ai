"""Marker agent — reads selected cuts and drops B-Roll / cutaway markers.

Markers are suggestions for the editor, not commands. Spec §3.1 marker
vocabulary templates come from the preset.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from . import llm
from .speakers import apply_speaker_labels, detect_speakers

if TYPE_CHECKING:
    from .director import DirectorPlan
    from .presets import PresetBundle


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class MarkerSuggestion(BaseModel):
    """A single marker suggestion.

    ``at_s`` is in the ORIGINAL timeline's time (seconds). The execute stage
    maps it to the new timeline's frame after segments are stitched.
    """

    at_s: float = Field(description="Time (s) in the original timeline where the cue occurs.")
    color: str = Field(default="Blue")
    name: str
    note: str = Field(default="")
    duration_frames: int = Field(default=1, ge=1)


class MarkerPlan(BaseModel):
    markers: list[MarkerSuggestion]


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def _prompt(
    preset: PresetBundle,
    plan: DirectorPlan,
    transcript: list[dict],
    user_settings: dict | None = None,
) -> str:
    # Extract just the words inside the selected_clips so the model sees
    # exactly what the final cut contains.
    selected_words: list[dict] = []
    for seg in plan.selected_clips:
        for w in transcript:
            if seg.start_s <= w["start_time"] and w["end_time"] <= seg.end_s + 1e-3:
                selected_words.append(w)

    # v2-5: honour user speaker labels so marker names can reference "Host"
    # / "Guest" directly instead of the STT's raw ``S1`` / ``S2`` tags.
    labels = (user_settings or {}).get("speaker_labels") or None
    selected_words = apply_speaker_labels(selected_words, labels)
    roster = detect_speakers(selected_words)
    speaker_hint = ""
    if len(roster) >= 2:
        speaker_hint = (
            "\n\nSPEAKER CONTEXT — each word carries a `speaker_id`; "
            f"speakers in this cut: {', '.join(roster)}. When a marker is "
            "speaker-specific (a visual referenced by one person only), "
            "include the speaker tag in the marker's `name` so the editor "
            'can tell them apart (e.g. "Cutaway — Guest: {subject}").'
        )

    cue_list = ", ".join(f'"{c}"' for c in preset.cue_vocabulary)
    mv_list = "\n".join(f"  - {m}" for m in preset.marker_vocabulary)

    return f"""You are a {preset.role}. Read the words below (already the final selected cut) and suggest B-Roll or cutaway markers wherever the speaker explicitly refers to something visual.

CUE VOCABULARY — look for these phrases or paraphrases:
{cue_list}

MARKER VOCABULARY — use these name templates:
{mv_list}

RULES:
1. Only suggest a marker when the text clearly references an external visual the viewer would benefit from seeing.
2. ``at_s`` must match the ``start_time`` of the cue word (the moment the cue begins).
3. Do not over-mark — quality over quantity. If nothing is worth marking, return an empty ``markers`` array.
4. Keep ``name`` short (< 60 chars). Put detail in ``note``.{speaker_hint}

SELECTED WORDS (JSON array):
{json.dumps(selected_words, separators=(",", ":"))}
"""


def suggest_markers(
    plan: DirectorPlan,
    transcript: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> MarkerPlan:
    """Run the Marker agent over a Director plan."""
    return llm.call_structured(
        agent="marker",
        prompt=_prompt(preset, plan, transcript, user_settings),
        response_schema=MarkerPlan,
        temperature=0.3,
    )
