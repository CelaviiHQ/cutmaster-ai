"""Axis 2 cut intents.

Five intents describing *what the user is making* from the content on
the timeline. Paired with an :class:`~cutmaster_ai.cutmaster.data.content_profiles.ContentProfile`
to produce a fully resolved cut recipe (see future ``axis_resolution.py``).

Legacy migration — the three cut-intent presets in
:mod:`cutmaster_ai.cutmaster.data.presets` (TIGHTENER / CLIP_HUNTER /
SHORT_GENERATOR) map onto ``surgical_tighten`` / ``multi_clip`` /
``assembled_short`` respectively. The other two intents (``narrative``,
``peak_highlight``) are new and cover combinations the 11-preset model
couldn't express.

Per the design doc §Resolved design decisions:

- ``hook_rule`` and ``marker_vocabulary`` default to the content profile
  but can be overridden here when the cut intent has a distinct shape
  (e.g. multi_clip's ``Clip: {topic}`` marker format).
- ``role`` is usually content-owned; three of the five intents
  (multi_clip, assembled_short, surgical_tighten) override it because
  they're specialised editors, not just pacing variants.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .content_profiles import ReorderMode

CutIntent = Literal[
    "narrative",
    "peak_highlight",
    "multi_clip",
    "assembled_short",
    "surgical_tighten",
]

SelectionStrategy = Literal[
    "narrative-arc",
    "peak-hunt",
    "top-n",
    "montage",
    "preserve-takes",
]


class CutIntentBundle(BaseModel):
    """Describes the shape of the output, independent of content type."""

    key: CutIntent
    label: str
    description: str
    selection_strategy: SelectionStrategy
    # Multiplier applied to the content profile's default_target_segment_s.
    # 1.0 = identical pacing to content default; 0.4 = aggressive
    # short-form compression; 0.6 = peak-highlight snappiness.
    pacing_modifier: float = Field(
        default=1.0,
        ge=0.2,
        le=2.0,
        description=(
            "Multiplier on ContentProfile.default_target_segment_s during "
            "pacing resolution. Bounds chosen to prevent runaway pacing."
        ),
    )
    # When set, wins over the content profile's default_reorder_mode.
    # ``None`` means 'inherit from content profile'.
    default_reorder_mode: ReorderMode | None = None
    # Overrides — None = inherit from the content profile.
    role_override: str | None = None
    hook_rule_override: str | None = None
    marker_vocabulary_override: list[str] | None = None


NARRATIVE = CutIntentBundle(
    key="narrative",
    label="Narrative",
    description=(
        "Tell a coherent story using the content's natural arc. Keeps the "
        "content profile's pacing and reorder policy intact."
    ),
    selection_strategy="narrative-arc",
    pacing_modifier=1.0,
    default_reorder_mode=None,
)


PEAK_HIGHLIGHT = CutIntentBundle(
    key="peak_highlight",
    label="Peak highlight",
    description=(
        "Pull the single highest-energy moment as a short reel or trailer. "
        "Tight pacing, free reorder, minimal setup."
    ),
    selection_strategy="peak-hunt",
    pacing_modifier=0.6,
    default_reorder_mode="free",
    hook_rule_override=(
        "the single highest-engagement moment in the window — peak emotion, "
        "strongest quote, or most quotable exchange"
    ),
)


MULTI_CLIP = CutIntentBundle(
    key="multi_clip",
    label="Multi-clip",
    description=(
        "Surface N self-contained clips from a long-form recording. Each "
        "clip stands alone; viewer needs zero context to grasp the moment."
    ),
    selection_strategy="top-n",
    pacing_modifier=1.0,
    default_reorder_mode=None,
    role_override=(
        "viral-moments editor — finds quotable, self-contained exchanges in a long-form recording"
    ),
    hook_rule_override=(
        "the single most quotable, tension-rich, or emotionally clear moment in the window"
    ),
    marker_vocabulary_override=["Clip: {topic}", "Hook: {line}"],
)


ASSEMBLED_SHORT = CutIntentBundle(
    key="assembled_short",
    label="Assembled short",
    description=(
        "Compose one 45–90 s short from 3–8 scattered spans. Jump cuts "
        "welcome; each beat earns its screen time."
    ),
    selection_strategy="montage",
    pacing_modifier=0.4,
    default_reorder_mode="free",
    role_override=(
        "TikTok / Reels editor specialising in punchy, jump-cut shorts "
        "assembled from scattered moments"
    ),
    hook_rule_override=(
        "the strongest opening statement that earns the next five seconds of attention"
    ),
    marker_vocabulary_override=["Hook: {line}", "Beat: {topic}"],
)


SURGICAL_TIGHTEN = CutIntentBundle(
    key="surgical_tighten",
    label="Surgical tighten",
    description=(
        "Preserve take order; drop filler, dead air, and restarts inside "
        "each take. Requires an already-assembled timeline."
    ),
    selection_strategy="preserve-takes",
    pacing_modifier=1.0,
    default_reorder_mode="locked",
    role_override=(
        "no-LLM tightener — skips the Director and relies on per-take word-block segmentation"
    ),
    hook_rule_override="preserve the original opening of each take; no narrative reordering",
    marker_vocabulary_override=[],
)


CUT_INTENTS: dict[CutIntent, CutIntentBundle] = {
    "narrative": NARRATIVE,
    "peak_highlight": PEAK_HIGHLIGHT,
    "multi_clip": MULTI_CLIP,
    "assembled_short": ASSEMBLED_SHORT,
    "surgical_tighten": SURGICAL_TIGHTEN,
}


def get_cut_intent(key: str) -> CutIntentBundle:
    """Return the ``CutIntentBundle`` for ``key`` or raise :class:`KeyError`."""
    if key not in CUT_INTENTS:
        raise KeyError(f"Unknown cut intent '{key}'. Valid: {sorted(CUT_INTENTS)}")
    return CUT_INTENTS[key]  # type: ignore[index]


def all_cut_intents() -> list[CutIntentBundle]:
    """Return cut intents in canonical display order."""
    return list(CUT_INTENTS.values())
