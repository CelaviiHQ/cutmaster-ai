"""Content-category exclusion schema (v2-0 groundwork).

Axis 2 of the three-axis scrubbing model (see :mod:`cutmaster` header).
Presets declare a curated list of exclusion *categories* they care about;
users check which ones to apply on the Configure screen; the Director
prompt consumes the selected keys and drops matching content during
segment selection.

This module only defines the schema — v2-0 is additive. The actual
per-preset category lists land in v2-1 (:mod:`presets`) and the prompt
wiring lands in :mod:`director` at the same time.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExcludeCategory(BaseModel):
    """One toggleable content-exclusion category declared by a preset.

    ``key`` is the stable machine identifier the Director prompt receives;
    ``label`` and ``description`` drive the Configure screen's checkbox UI.
    ``checked_by_default`` controls whether the box is pre-ticked when the
    user first picks the preset.
    """

    key: str = Field(
        ...,
        description="Stable machine identifier (snake_case). Sent to the Director.",
    )
    label: str = Field(
        ...,
        description="Short human-readable label for the Configure checkbox.",
    )
    description: str = Field(
        ...,
        description="One-sentence tooltip clarifying what content this drops.",
    )
    checked_by_default: bool = Field(
        default=False,
        description="Whether the checkbox starts ticked for this preset.",
    )


def default_exclude_keys(categories: list[ExcludeCategory]) -> list[str]:
    """Return the list of keys whose ``checked_by_default`` is ``True``.

    Convenience helper the panel calls when first rendering the Configure
    screen for a preset.
    """
    return [c.key for c in categories if c.checked_by_default]
