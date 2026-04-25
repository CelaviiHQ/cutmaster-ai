"""Axis-keyed gate helpers for the three sensory layers.

Single chokepoint per layer. ``analyze.py`` and ``build.py`` both call
through these helpers so activation flows through the same shape, with
no preset/sentinel divergence and one mental model: ``(cut_intent,
timeline_mode)`` → matrix row → resolved bool.

The functions accept a ``Mapping`` (typically ``UserSettings.model_dump()``
on the build path or a synthetic ``{"sensory_master_enabled": ...,
"layer_*_enabled": ...}`` envelope on the analyze path) and read the
four sensory fields via ``.get(...)``. This keeps the build call sites
unchanged and lets analyze hand-build a small dict without unpacking.
"""

from __future__ import annotations

from collections.abc import Mapping

from ....cutmaster.data.presets import resolve_sensory_layers_by_axes

# Mirrors ``_PRESET_TO_CUT_INTENT`` in ``build.py`` — duplicated here so
# the analyze handler can collapse ``preset → cut_intent`` without
# importing from ``build.py`` (which would create a circular dep) and
# without leaning on the legacy preset-keyed resolver Phase 4 deletes.
_PRESET_TO_CUT_INTENT: dict[str, str] = {
    "tightener": "surgical_tighten",
    "clip_hunter": "multi_clip",
    "short_generator": "assembled_short",
}


def preset_to_cut_intent(preset: str) -> str:
    """Collapse a legacy preset key to its matching cut intent.

    Cut-intent presets map to their named intent; content-type presets
    fall through to ``"narrative"`` so the matrix lookup hits the
    timeline_mode-keyed row (raw_dump / rough_cut / curated / assembled).
    """
    return _PRESET_TO_CUT_INTENT.get(preset, "narrative")


def _resolve(
    settings: Mapping,
    *,
    cut_intent: str,
    timeline_mode: str,
) -> tuple[bool, bool, bool]:
    return resolve_sensory_layers_by_axes(
        master_enabled=bool(settings.get("sensory_master_enabled")),
        c_override=settings.get("layer_c_enabled"),
        a_override=settings.get("layer_a_enabled"),
        audio_override=settings.get("layer_audio_enabled"),
        cut_intent=cut_intent,  # type: ignore[arg-type]
        timeline_mode=timeline_mode,  # type: ignore[arg-type]
    )


def layer_c_enabled(
    settings: Mapping,
    *,
    cut_intent: str,
    timeline_mode: str,
) -> bool:
    """Resolve Layer C (shot tagging) for the given axis pair."""
    layer_c, _, _ = _resolve(settings, cut_intent=cut_intent, timeline_mode=timeline_mode)
    return layer_c


def layer_a_enabled(
    settings: Mapping,
    *,
    cut_intent: str,
    timeline_mode: str,
) -> bool:
    """Resolve Layer A (boundary validator) for the given axis pair."""
    _, layer_a, _ = _resolve(settings, cut_intent=cut_intent, timeline_mode=timeline_mode)
    return layer_a


def layer_audio_enabled(
    settings: Mapping,
    *,
    cut_intent: str,
    timeline_mode: str,
) -> bool:
    """Resolve Layer Audio (DSP cues) for the given axis pair."""
    _, _, layer_audio = _resolve(settings, cut_intent=cut_intent, timeline_mode=timeline_mode)
    return layer_audio
