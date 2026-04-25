"""Axis resolution — pure function layer.

Takes the four axes (content_type, cut_intent, duration, timeline_mode) plus
optional overrides and composes a :class:`ResolvedAxes` that downstream
Director prompt builders consume.

No Resolve SDK calls, no LLM calls, no filesystem reads — deterministic
and side-effect free. Phase 3 wires the six ``build_*_plan`` functions
in :mod:`cutmaster_ai.cutmaster.core.director` to read from this.

Design references:
  - :doc:`docs/THREE_AXIS_MODEL.md` §4 (interaction matrix)
  - :doc:`docs/THREE_AXIS_MODEL.md` §5 (Axis 2 × Axis 4 routing)
  - :doc:`docs/THREE_AXIS_MODEL.md` §6 (auto-resolution rules)
  - :doc:`docs/THREE_AXIS_MODEL.md` Appendix (pacing formula)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .axis_compat import cut_intent_mode_incompatibility_reason
from .content_profiles import (
    CONTENT_PROFILES,
    ContentProfile,
    ContentType,
    ReorderMode,
    get_content_profile,
)
from .cut_intents import CUT_INTENTS, CutIntent, SelectionStrategy, get_cut_intent
from .presets import TimelineMode

PromptBuilder = Literal[
    "_prompt",
    "_assembled_prompt",
    "_clip_hunter_prompt",
    "_short_generator_prompt",
    "_curated_prompt",
    "_rough_cut_prompt",
]

CutIntentSource = Literal["user", "auto", "forced"]
"""Provenance tag on :attr:`ResolvedAxes.cut_intent_source`.

- ``"user"``    — caller passed an explicit ``cut_intent`` value.
- ``"auto"``    — :func:`resolve_cut_intent` picked it from duration / content.
- ``"forced"``  — a hard rule (``num_clips > 1``, takes-already-scrubbed
                  shortcut) overrode the duration heuristic.
"""


class IncompatibleAxesError(ValueError):
    """Raised when a ``(cut_intent, timeline_mode)`` pair is not supported."""


class SegmentPacing(BaseModel):
    """Resolved per-segment duration bounds in seconds."""

    min: float = Field(ge=0.0)
    target: float = Field(gt=0.0)
    max: float = Field(gt=0.0)


class ResolvedAxes(BaseModel):
    """Fully resolved recipe for a single cut."""

    content_type: ContentType
    cut_intent: CutIntent
    cut_intent_source: CutIntentSource = "user"
    reorder_mode: ReorderMode
    segment_pacing: SegmentPacing
    selection_strategy: SelectionStrategy
    prompt_builder: PromptBuilder
    rationale: list[str] = Field(default_factory=list)
    unusual: bool = False


class _Cell(BaseModel):
    """One (content_type, cut_intent) matrix entry."""

    reorder_mode: ReorderMode
    selection_strategy: SelectionStrategy
    pacing_modifier: float = Field(gt=0.0)
    unusual: bool = False


# --------------------------------------------------------------------- matrix

# The 40 (content_type, cut_intent) cells per §4 of the design doc.
# Only reorder_mode, selection_strategy, and pacing_modifier vary per cell;
# content-profile defaults (cue vocab, speaker awareness, etc.) come from
# CONTENT_PROFILES unchanged.
#
# Tutorial × Multi-clip is blank in the design doc ("unusual — steps are
# linear"); we populate it with conservative defaults and mark unusual=True
# so the UI can warn without blocking.

_MATRIX: dict[tuple[ContentType, CutIntent], _Cell] = {
    # Vlog row
    ("vlog", "narrative"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("vlog", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.4
    ),
    ("vlog", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.6
    ),
    ("vlog", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.25
    ),
    ("vlog", "surgical_tighten"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Interview row
    ("interview", "narrative"): _Cell(
        reorder_mode="locked", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("interview", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.35
    ),
    ("interview", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.55
    ),
    ("interview", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.3
    ),
    ("interview", "surgical_tighten"): _Cell(
        reorder_mode="locked", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Wedding row
    ("wedding", "narrative"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("wedding", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.4
    ),
    ("wedding", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.6
    ),
    ("wedding", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.25
    ),
    ("wedding", "surgical_tighten"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Podcast row
    ("podcast", "narrative"): _Cell(
        reorder_mode="locked", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("podcast", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.35
    ),
    ("podcast", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.55
    ),
    ("podcast", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.3
    ),
    ("podcast", "surgical_tighten"): _Cell(
        reorder_mode="locked", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Presentation row
    ("presentation", "narrative"): _Cell(
        reorder_mode="locked", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("presentation", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.35
    ),
    ("presentation", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.55
    ),
    ("presentation", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.3
    ),
    ("presentation", "surgical_tighten"): _Cell(
        reorder_mode="locked", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Product Demo row
    ("product_demo", "narrative"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("product_demo", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.4
    ),
    ("product_demo", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.6
    ),
    ("product_demo", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.3
    ),
    ("product_demo", "surgical_tighten"): _Cell(
        reorder_mode="preserve_macro", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Tutorial row — Multi-clip is marked unusual (steps are usually linear)
    ("tutorial", "narrative"): _Cell(
        reorder_mode="locked", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("tutorial", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.4
    ),
    ("tutorial", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological",
        selection_strategy="top-n",
        pacing_modifier=0.55,
        unusual=True,
    ),
    ("tutorial", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.3
    ),
    ("tutorial", "surgical_tighten"): _Cell(
        reorder_mode="locked", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
    # Reaction row
    ("reaction", "narrative"): _Cell(
        reorder_mode="locked", selection_strategy="narrative-arc", pacing_modifier=1.0
    ),
    ("reaction", "peak_highlight"): _Cell(
        reorder_mode="free", selection_strategy="peak-hunt", pacing_modifier=0.3
    ),
    ("reaction", "multi_clip"): _Cell(
        reorder_mode="per_clip_chronological", selection_strategy="top-n", pacing_modifier=0.55
    ),
    ("reaction", "assembled_short"): _Cell(
        reorder_mode="free", selection_strategy="montage", pacing_modifier=0.25
    ),
    ("reaction", "surgical_tighten"): _Cell(
        reorder_mode="locked", selection_strategy="preserve-takes", pacing_modifier=1.0
    ),
}


# ------------------------------------------------------------------- routing

# §5 prompt-builder routing. A sparse table — lookups that miss go through
# the explicit cascade in resolve_prompt_builder() so the code path reads
# like the design doc.
_PROMPT_ROUTING: dict[tuple[CutIntent, TimelineMode], PromptBuilder] = {
    ("narrative", "raw_dump"): "_prompt",
    ("narrative", "rough_cut"): "_rough_cut_prompt",
    ("narrative", "curated"): "_curated_prompt",
    ("narrative", "assembled"): "_assembled_prompt",
    # surgical_tighten only valid with assembled — other modes blocked by axis_compat
    ("surgical_tighten", "assembled"): "_assembled_prompt",
}


def resolve_prompt_builder(cut_intent: CutIntent, timeline_mode: TimelineMode) -> PromptBuilder:
    """Pick the Director prompt builder for a ``(cut_intent, timeline_mode)`` pair.

    Raises :class:`IncompatibleAxesError` when the pair is blocked by the
    Axis 2 × Axis 4 compatibility matrix.
    """
    reason = cut_intent_mode_incompatibility_reason(cut_intent, timeline_mode)
    if reason is not None:
        raise IncompatibleAxesError(reason)

    if (cut_intent, timeline_mode) in _PROMPT_ROUTING:
        return _PROMPT_ROUTING[(cut_intent, timeline_mode)]

    # peak_highlight × any -> _prompt with peak strategy (the flat builder
    # handles both narrative × raw_dump and peak-hunt paths).
    if cut_intent == "peak_highlight":
        return "_prompt"
    # multi_clip × any-non-assembled -> _clip_hunter_prompt.
    if cut_intent == "multi_clip":
        return "_clip_hunter_prompt"
    # assembled_short × any -> _short_generator_prompt (its own synthesis path).
    if cut_intent == "assembled_short":
        return "_short_generator_prompt"

    # Defensive fallback — should not be reachable given the matrix above.
    raise IncompatibleAxesError(
        f"No prompt builder for cut_intent={cut_intent!r} × timeline_mode={timeline_mode!r}"
    )


# --------------------------------------------------------------- auto-resolve


def resolve_cut_intent(
    content_type: ContentType,
    duration_s: float,
    num_clips: int,
    timeline_mode: TimelineMode,
    *,
    takes_already_scrubbed: bool = False,
) -> tuple[CutIntent, str, CutIntentSource]:
    """Pick a cut intent when the user left Axis 2 on Auto.

    Returns ``(cut_intent, reason, source)`` — the reason is a short
    English string captured in :attr:`ResolvedAxes.rationale` so the
    panel can explain the decision; ``source`` is ``"forced"`` when a
    hard rule (num_clips > 1, takes-already-scrubbed shortcut)
    overrode the duration heuristic, else ``"auto"``.

    Precedence (per design review):
      1. Explicit ``num_clips > 1`` — user signal beats every heuristic
         (source=``"forced"``).
      2. Surgical-tighten shortcut — only when the timeline is assembled
         *and* the source flags takes as already scrubbed
         (source=``"forced"``).
      3. Duration bands — §6 of the design doc, with content-type
         exceptions for Product Demo / Vlog (lean assembled_short under
         2 minutes) and Reaction (always peak-hunts) (source=``"auto"``).
    """
    if num_clips > 1:
        return (
            "multi_clip",
            f"num_clips={num_clips} > 1 → multi-clip harvesting",
            "forced",
        )

    if timeline_mode == "assembled" and takes_already_scrubbed:
        return (
            "surgical_tighten",
            "timeline is assembled and takes are already scrubbed → surgical tighten",
            "forced",
        )

    if duration_s < 45:
        if content_type == "product_demo":
            return (
                "assembled_short",
                f"{duration_s:.0f}s under 45s; Product Demo prefers assembled shorts",
                "auto",
            )
        return (
            "peak_highlight",
            f"{duration_s:.0f}s under 45s → peak highlight",
            "auto",
        )

    if duration_s < 120:
        if content_type in ("product_demo", "vlog"):
            return (
                "assembled_short",
                f"{duration_s:.0f}s under 2min; {content_type} prefers assembled shorts",
                "auto",
            )
        return (
            "peak_highlight",
            f"{duration_s:.0f}s under 2min → peak highlight",
            "auto",
        )

    if duration_s < 600:
        if content_type == "reaction":
            return (
                "peak_highlight",
                f"{duration_s:.0f}s under 10min; reaction content peak-hunts",
                "auto",
            )
        return (
            "narrative",
            f"{duration_s:.0f}s under 10min → narrative arc",
            "auto",
        )

    return (
        "narrative",
        f"{duration_s:.0f}s long-form → narrative arc",
        "auto",
    )


# ------------------------------------------------------------------- pacing


def resolve_pacing(
    content_profile: ContentProfile,
    pacing_modifier: float,
    duration_s: float,
) -> SegmentPacing:
    """Compose per-segment duration bounds per the Appendix formula.

    ``duration_factor = clamp(0.8, (duration_s / 180) ** 0.15, 1.1)`` —
    gently inflates pacing for long-form and shrinks it for short-form
    so a 10-minute Narrative cut doesn't inherit a 60-second feel.
    Constants are provisional (Phase 6 calibration).
    """
    base = content_profile.default_target_segment_s
    duration_factor = _clamp(0.8, (max(duration_s, 1.0) / 180.0) ** 0.15, 1.1)
    target = base * pacing_modifier * duration_factor
    minimum = max(2.0, target * 0.4)
    maximum = target * 2.5
    return SegmentPacing(min=minimum, target=target, max=maximum)


def _clamp(lo: float, x: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------- top-level


def resolve_axes(
    content_type: ContentType,
    cut_intent: CutIntent | None,
    duration_s: float,
    timeline_mode: TimelineMode,
    *,
    num_clips: int = 1,
    reorder_allowed: bool = True,
    takes_already_scrubbed: bool = False,
) -> ResolvedAxes:
    """Compose a fully resolved cut recipe.

    ``cut_intent=None`` triggers auto-resolution via :func:`resolve_cut_intent`.
    ``reorder_allowed=False`` forces ``reorder_mode="locked"`` regardless of
    what the matrix cell would otherwise emit (legacy user override).

    Raises :class:`KeyError` when ``content_type`` is not in
    ``CONTENT_PROFILES`` (``auto_detect`` callers must run the cascade first).
    Raises :class:`IncompatibleAxesError` when the resolved
    ``(cut_intent, timeline_mode)`` pair is blocked.
    """
    profile = get_content_profile(content_type)
    rationale: list[str] = []
    cut_intent_source: CutIntentSource

    if cut_intent is None:
        cut_intent, reason, cut_intent_source = resolve_cut_intent(
            content_type,
            duration_s,
            num_clips,
            timeline_mode,
            takes_already_scrubbed=takes_already_scrubbed,
        )
        rationale.append(f"Auto-resolved cut_intent: {reason}")
    else:
        # Validate the user-supplied value early — downstream code assumes
        # CUT_INTENTS[cut_intent] exists.
        get_cut_intent(cut_intent)
        cut_intent_source = "user"

    cell = _MATRIX[(content_type, cut_intent)]
    rationale.append(
        f"{profile.label} × {CUT_INTENTS[cut_intent].label}: "
        f"reorder={cell.reorder_mode}, strategy={cell.selection_strategy}, "
        f"pacing×{cell.pacing_modifier}"
    )

    reorder_mode: ReorderMode = cell.reorder_mode
    if not reorder_allowed and reorder_mode != "locked":
        rationale.append(f"reorder_allowed=False overrides matrix cell ({reorder_mode} → locked)")
        reorder_mode = "locked"

    pacing = resolve_pacing(profile, cell.pacing_modifier, duration_s)
    rationale.append(
        f"Pacing resolved to {{min={pacing.min:.1f}, target={pacing.target:.1f}, "
        f"max={pacing.max:.1f}}} over {duration_s:.0f}s"
    )

    builder = resolve_prompt_builder(cut_intent, timeline_mode)
    rationale.append(f"Prompt builder: {builder}")

    return ResolvedAxes(
        content_type=content_type,
        cut_intent=cut_intent,
        cut_intent_source=cut_intent_source,
        reorder_mode=reorder_mode,
        segment_pacing=pacing,
        selection_strategy=cell.selection_strategy,
        prompt_builder=builder,
        rationale=rationale,
        unusual=cell.unusual,
    )


# ------------------------------------------------------------- coverage util


# Surfaced for tests — the canonical set of all (content_type, cut_intent)
# pairs the matrix covers.
def all_matrix_cells() -> list[tuple[ContentType, CutIntent]]:
    """Every (content_type, cut_intent) pair the interaction matrix defines."""
    return list(_MATRIX.keys())


assert len(_MATRIX) == len(CONTENT_PROFILES) * len(CUT_INTENTS), (
    f"Matrix has {len(_MATRIX)} cells; expected "
    f"{len(CONTENT_PROFILES) * len(CUT_INTENTS)} (content × intent)."
)
