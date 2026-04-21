"""Axis 2 × Axis 4 compatibility matrix.

Mirrors the legacy :data:`~cutmaster_ai.cutmaster.data.presets._INCOMPATIBLE`
matrix but keys on ``(cut_intent, timeline_mode)`` instead of
``(preset, timeline_mode)``. Surface (``cut_intent_mode_compatible`` /
``cut_intent_mode_incompatibility_reason``) matches the legacy helpers
shape so call sites can swap over without ceremony (see Phase 4.5 of
the three-axis-model plan).

Blocked cells per §5 of the design doc:

- ``surgical_tighten`` requires an already-assembled timeline — every
  non-assembled mode is blocked.
- ``multi_clip`` on an already-assembled timeline is blocked (the
  source is already a single cut; multi-clip extraction assumes raw
  material).
"""

from __future__ import annotations

from .cut_intents import CutIntent
from .presets import TimelineMode

_AXIS2_TIMELINE_INCOMPATIBLE: dict[tuple[CutIntent, TimelineMode], str] = {
    ("surgical_tighten", "raw_dump"): (
        "Surgical tighten preserves take order — the source timeline must "
        "already be assembled. Switch to Assembled."
    ),
    ("surgical_tighten", "rough_cut"): (
        "Surgical tighten can't pick between A/B alternates — it expects a "
        "single committed take per beat. Switch to Assembled."
    ),
    ("surgical_tighten", "curated"): (
        "Surgical tighten needs the takes arranged in playback order — "
        "Curated hasn't committed to one yet. Switch to Assembled."
    ),
    ("multi_clip", "assembled"): (
        "Multi-clip extraction assumes raw material. The source is already "
        "a single assembled cut — pick a different cut intent, or start "
        "from a non-assembled timeline."
    ),
}


def cut_intent_mode_compatible(cut_intent: str, timeline_mode: str) -> bool:
    """True when the ``(cut_intent, timeline_mode)`` combination is supported."""
    return (cut_intent, timeline_mode) not in _AXIS2_TIMELINE_INCOMPATIBLE


def cut_intent_mode_incompatibility_reason(cut_intent: str, timeline_mode: str) -> str | None:
    """Return the block reason for ``(cut_intent, timeline_mode)`` or ``None``."""
    return _AXIS2_TIMELINE_INCOMPATIBLE.get((cut_intent, timeline_mode))
