"""Output format specs — horizontal / vertical_short / square.

Format is **orthogonal to Preset**: any preset can target any format.
``FormatSpec`` bundles the target resolution, a platform-length cap, and
safe-zone metadata so the execute step can build the cut timeline at the
right dimensions and the Configure screen can warn about platform UI
chrome.

Keep this module pure — no Resolve imports. Resolve-side plumbing lives
in :mod:`execute` and reads the values from here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Format = Literal["horizontal", "vertical_short", "square"]


ReframeMode = Literal["center_crop", "smart_reframe", "none"]
"""How the executor should handle an aspect mismatch between source and target.

- ``center_crop`` — default when source aspect ≠ target. Uses Resolve's
  image-scaling preset so all appended clips auto-fill the new timeline
  frame with a center-biased crop.
- ``smart_reframe`` — v3 hook for Resolve Studio's Smart Reframe. v2 ships
  only if the empirical spike confirms a reliable API surface.
- ``none`` — source already matches target (vertical phone shoot into a
  vertical cut). Do nothing; skip the crop step entirely.
"""


class SafeZones(BaseModel):
    """Percentages of the target frame that platform UI chrome hides.

    Measured from each edge. A ``bottom_pct`` of 18 means the bottom 18%
    of the frame is likely occluded by TikTok's caption / UI stack, so the
    editor should avoid parking key content there.
    """

    top_pct: float = 0.0
    bottom_pct: float = 0.0
    left_pct: float = 0.0
    right_pct: float = 0.0


class FormatSpec(BaseModel):
    """Complete output-format description consumed by execute + UI."""

    key: Format
    label: str
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    max_duration_s: float | None = Field(
        default=None,
        description="Platform length cap (None = unbounded). UI uses this to clamp target_length_s.",
    )
    safe_zones: SafeZones = Field(default_factory=SafeZones)
    reframe_default: ReframeMode = "center_crop"

    @property
    def aspect(self) -> float:
        return self.width / self.height


HORIZONTAL = FormatSpec(
    key="horizontal",
    label="Horizontal (16:9)",
    width=1920,
    height=1080,
    max_duration_s=None,
    safe_zones=SafeZones(),
    reframe_default="center_crop",
)

VERTICAL_SHORT = FormatSpec(
    key="vertical_short",
    label="Vertical short (9:16)",
    width=1080,
    height=1920,
    # TikTok's cap is 10 min but the meaningful "short" band is ≤60 s.
    # YouTube Shorts is 60 s; Reels is 90 s. Default to 90 s so Reels is
    # reachable without edits; Shorts users still clamp down in the UI.
    max_duration_s=90.0,
    # Measured off TikTok + Reels default UI screenshots.
    safe_zones=SafeZones(top_pct=8.0, bottom_pct=18.0, right_pct=12.0),
    reframe_default="center_crop",
)

SQUARE = FormatSpec(
    key="square",
    label="Square (1:1)",
    width=1080,
    height=1080,
    max_duration_s=None,
    safe_zones=SafeZones(),
    reframe_default="center_crop",
)


FORMATS: dict[str, FormatSpec] = {
    HORIZONTAL.key: HORIZONTAL,
    VERTICAL_SHORT.key: VERTICAL_SHORT,
    SQUARE.key: SQUARE,
}


def get_format(key: str) -> FormatSpec:
    """Return the FormatSpec for ``key``. Raises :class:`KeyError` if unknown."""
    if key not in FORMATS:
        raise KeyError(f"Unknown format '{key}'. Valid: {sorted(FORMATS)}")
    return FORMATS[key]


def all_formats() -> list[FormatSpec]:
    """Return the full format list in UI-display order."""
    return [HORIZONTAL, VERTICAL_SHORT, SQUARE]


# ---------------------------------------------------------------------------
# Source-aspect helpers
# ---------------------------------------------------------------------------


def detect_source_aspect(
    source_width: int,
    source_height: int,
    tolerance: float = 0.05,
) -> Format:
    """Classify a source timeline's pixel dimensions into a ``Format`` key.

    Ratios within ``tolerance`` (default ±5%) of the target aspect collapse
    to that format. Unknown aspects default to ``horizontal`` since that's
    the safest assumption for typical delivery pipelines.
    """
    if source_width <= 0 or source_height <= 0:
        return "horizontal"
    source_aspect = source_width / source_height
    for spec in (VERTICAL_SHORT, SQUARE, HORIZONTAL):
        if abs(source_aspect - spec.aspect) <= tolerance * spec.aspect:
            return spec.key
    return "horizontal"


def recommend_format(source_width: int, source_height: int) -> FormatSpec:
    """Recommend an output format for a given source aspect.

    Currently a pass-through to :func:`detect_source_aspect`: we recommend
    shipping the source's native aspect unless the user overrides. The UI
    uses this to preselect the Format picker and to suppress the
    aspect-mismatch reframe when source and target already match.
    """
    key = detect_source_aspect(source_width, source_height)
    return get_format(key)


def needs_reframe(
    spec: FormatSpec,
    source_width: int,
    source_height: int,
    tolerance: float = 0.05,
) -> bool:
    """Whether the executor should apply a crop / reframe step.

    ``False`` when source aspect matches target within ``tolerance``
    (e.g. 9:16 phone into a Short). ``True`` otherwise.
    """
    if source_width <= 0 or source_height <= 0:
        return True
    source_aspect = source_width / source_height
    return abs(source_aspect - spec.aspect) > tolerance * spec.aspect
