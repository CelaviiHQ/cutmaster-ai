"""Score merging + confidence math for the auto-detect cascade.

Each tier produces a ``PresetScores`` mapping — preset key to a [0, 1]
score. The cascade merges tier scores with per-tier weights; the final
preset is the argmax; confidence is derived from the margin between
the top two scores (not from a model's self-assessment).
"""

from __future__ import annotations

from ...data.presets import PRESETS

PresetScores = dict[str, float]

# Weights for Tier 0-3 respectively. Calibrated by feel for Phase 1;
# revisited against a labeled fixture set in Phase 4.
DEFAULT_WEIGHTS: tuple[float, float, float, float] = (0.15, 0.35, 0.25, 0.25)

# Margin thresholds for the confidence tiers.
HIGH_CONFIDENCE_MARGIN = 0.25
AMBIGUOUS_MARGIN_LOW = 0.10

# Presets the cascade should never pick — they're workflow / mode presets
# that surface via explicit UI buttons, not content-type classification.
NON_CLASSIFIABLE_PRESETS = frozenset({"tightener", "clip_hunter", "short_generator", "auto"})


def classifiable_presets() -> list[str]:
    """All preset keys the cascade is allowed to return."""
    return [k for k in PRESETS if k not in NON_CLASSIFIABLE_PRESETS]


def empty_scores() -> PresetScores:
    """Return a neutral score dict — every classifiable preset at 0.0."""
    return {k: 0.0 for k in classifiable_presets()}


def _normalize(scores: PresetScores) -> PresetScores:
    """Scale scores so the max is 1.0; leave zeros alone."""
    if not scores:
        return {}
    mx = max(scores.values(), default=0.0)
    if mx <= 0:
        return dict(scores)
    return {k: v / mx for k, v in scores.items()}


def merge(
    tiers: tuple[PresetScores, PresetScores, PresetScores, PresetScores],
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
) -> PresetScores:
    """Combine tier-0..tier-3 scores with weights. Absent tiers contribute 0."""
    t0, t1, t2, t3 = tiers
    n0, n1, n2, n3 = _normalize(t0), _normalize(t1), _normalize(t2), _normalize(t3)
    w0, w1, w2, w3 = weights
    out: PresetScores = {}
    for k in classifiable_presets():
        out[k] = (
            w0 * n0.get(k, 0.0) + w1 * n1.get(k, 0.0) + w2 * n2.get(k, 0.0) + w3 * n3.get(k, 0.0)
        )
    return out


def top_n(scores: PresetScores, n: int = 3) -> list[tuple[str, float]]:
    """Sort scores descending; return the top ``n`` as (key, score) tuples."""
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]


def margin_to_confidence(margin: float) -> float:
    """Convert the top-1/top-2 gap into a [0, 1] confidence value.

    A margin of 0.25 maps to ~0.85; 0.5 to ~1.0. The function is
    deliberately non-linear — small margins should map to visibly low
    confidence so the UI can branch on it.
    """
    if margin <= 0:
        return 0.0
    # Sigmoid-ish curve scaled so HIGH_CONFIDENCE_MARGIN lands at ~0.85.
    value = margin / HIGH_CONFIDENCE_MARGIN
    return min(1.0, 0.85 * value + 0.0 if value <= 1 else 0.85 + 0.15 * min(1.0, value - 1))


def is_high_confidence(margin: float) -> bool:
    return margin >= HIGH_CONFIDENCE_MARGIN


def is_ambiguous_band(margin: float) -> bool:
    """True when the opening-sentence classifier (Tier 3) is worth calling."""
    return AMBIGUOUS_MARGIN_LOW <= margin < HIGH_CONFIDENCE_MARGIN
