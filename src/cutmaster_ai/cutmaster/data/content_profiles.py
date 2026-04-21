"""Axis 1 content profiles.

Eight content types describing *what's on the timeline* — role, hook
rule, cue vocabulary, pacing default, speaker awareness, exclude
categories. The three-axis model splits these fields off from the
legacy :class:`~cutmaster_ai.cutmaster.data.presets.PresetBundle` so
that the same content profile can be reused across multiple cut
intents (Axis 2). Values here are lifted verbatim from the matching
``PresetBundle`` instance in ``presets.py`` — single source of truth
during the dual-model window.

Companion modules:
  - :mod:`cutmaster_ai.cutmaster.data.cut_intents` — Axis 2 bundles.
  - :mod:`cutmaster_ai.cutmaster.data.axis_compat` — compatibility
    matrix across Axis 2 × Axis 4 (timeline_mode).

The canonical 4-value ``ReorderMode`` literal is declared here;
``PresetBundle.reorder_mode`` in ``presets.py`` keeps a narrower
3-value literal because no legacy preset uses the new
``per_clip_chronological`` value (see the dual-model notice in
``presets.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .excludes import ExcludeCategory
from .presets import PRESETS

ContentType = Literal[
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
]
"""The 8 resolved content types. Every value keys into ``CONTENT_PROFILES``.

Use this type inside the pipeline — ``ResolvedAxes.content_type``, cascade
output, Director prompt plumbing. Never contains ``auto_detect``.
"""

RequestedContentType = Literal[
    "vlog",
    "product_demo",
    "wedding",
    "interview",
    "tutorial",
    "podcast",
    "presentation",
    "reaction",
    "auto_detect",
]
"""The 9 wire-level content types. ``auto_detect`` is a sentinel meaning
"ask the cascade" and only appears on request boundaries
(``AnalyzeRequest.content_type`` and friends). Resolvers must map it to
one of the 8 ``ContentType`` values before producing a ``ResolvedAxes``.
"""

# Four values: the existing three from PresetBundle + per_clip_chronological,
# introduced in the three-axis model for cases like Interview or Podcast
# multi-clip where each emitted clip must preserve internal source-time
# order. Exported so presets.py and axis_resolution.py share the vocabulary.
ReorderMode = Literal[
    "free",
    "preserve_macro",
    "locked",
    "per_clip_chronological",
]


class ContentProfile(BaseModel):
    """Describes the content on the timeline, independent of cut intent."""

    key: ContentType
    label: str
    role: str
    hook_rule: str
    pacing: str
    cue_vocabulary: list[str]
    marker_vocabulary: list[str]
    theme_axes: list[str]
    scrub_defaults: dict = Field(default_factory=dict)
    exclude_categories: list[ExcludeCategory] = Field(default_factory=list)
    speaker_awareness: str = ""
    default_custom_focus_placeholder: str = ""
    default_target_segment_s: float = Field(
        default=18.0,
        description="Preferred per-segment duration in seconds. Input into pacing resolution.",
    )
    default_min_segment_s: float = 3.0
    default_max_segment_s: float = 40.0
    default_reorder_mode: ReorderMode = "free"


def _lift(key: str) -> ContentProfile:
    """Build a ``ContentProfile`` by copying the matching ``PresetBundle``."""
    p = PRESETS[key]
    return ContentProfile(
        key=key,  # type: ignore[arg-type]
        label=p.label,
        role=p.role,
        hook_rule=p.hook_rule,
        pacing=p.pacing,
        cue_vocabulary=list(p.cue_vocabulary),
        marker_vocabulary=list(p.marker_vocabulary),
        theme_axes=list(p.theme_axes),
        scrub_defaults=dict(p.scrub_defaults),
        exclude_categories=list(p.exclude_categories),
        speaker_awareness=p.speaker_awareness,
        default_custom_focus_placeholder=p.default_custom_focus_placeholder,
        default_target_segment_s=p.target_segment_s,
        default_min_segment_s=p.min_segment_s,
        default_max_segment_s=p.max_segment_s,
        default_reorder_mode=p.reorder_mode,  # type: ignore[arg-type]
    )


CONTENT_PROFILES: dict[ContentType, ContentProfile] = {
    "vlog": _lift("vlog"),
    "product_demo": _lift("product_demo"),
    "wedding": _lift("wedding"),
    "interview": _lift("interview"),
    "tutorial": _lift("tutorial"),
    "podcast": _lift("podcast"),
    "presentation": _lift("presentation"),
    "reaction": _lift("reaction"),
}


def get_content_profile(key: str) -> ContentProfile:
    """Return the ``ContentProfile`` for ``key`` or raise :class:`KeyError`."""
    if key not in CONTENT_PROFILES:
        raise KeyError(f"Unknown content type '{key}'. Valid: {sorted(CONTENT_PROFILES)}")
    return CONTENT_PROFILES[key]  # type: ignore[index]


def all_content_profiles() -> list[ContentProfile]:
    """Return content profiles in canonical display order."""
    return list(CONTENT_PROFILES.values())
