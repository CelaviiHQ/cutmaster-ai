"""Tier 2 — score presets by their cue-vocabulary overlap with the transcript.

Each preset already declares a ``cue_vocabulary`` in its bundle (e.g.
Tutorial carries ``"step one"``, ``"click"``, ``"select"``; Product Demo
carries ``"notice the"``, ``"the difference is"``). We score the
transcript against each list, weighted by **distinctiveness** — a cue
that appears in many presets' vocabularies is a weak signal for any of
them; a cue unique to one preset is a strong signal.

Matching is case-insensitive, word-boundary aware (so ``"step one"``
matches ``"Step one,"`` but not ``"misstepone"``), and multi-word
phrases match contiguous word sequences.
"""

from __future__ import annotations

import re

from ...data.presets import PRESETS
from .scoring import NON_CLASSIFIABLE_PRESETS, PresetScores, empty_scores


def _tokenize(word: str) -> str:
    """Strip trailing punctuation and lowercase for matching."""
    return re.sub(r"[^\w'-]+$", "", word or "").lower()


def _distinctiveness_weights() -> dict[str, float]:
    """Return ``{normalized_cue: 1 / count}`` across all classifiable presets.

    Computed once at import time via :func:`_CUE_WEIGHTS`. A cue shared
    between N presets contributes ``1/N`` to each preset's score; a
    unique cue contributes ``1.0``.
    """
    counts: dict[str, int] = {}
    for key, bundle in PRESETS.items():
        if key in NON_CLASSIFIABLE_PRESETS:
            continue
        for cue in bundle.cue_vocabulary:
            norm = cue.strip().lower()
            if norm:
                counts[norm] = counts.get(norm, 0) + 1
    return {cue: 1.0 / n for cue, n in counts.items()}


_CUE_WEIGHTS: dict[str, float] = _distinctiveness_weights()


def _preset_cues() -> dict[str, list[tuple[str, list[str], float]]]:
    """Pre-tokenize each preset's cue list once at import time.

    Returns ``{preset_key: [(original_cue, [token, ...], weight), ...]}``.
    """
    out: dict[str, list[tuple[str, list[str], float]]] = {}
    for key, bundle in PRESETS.items():
        if key in NON_CLASSIFIABLE_PRESETS:
            continue
        items: list[tuple[str, list[str], float]] = []
        for cue in bundle.cue_vocabulary:
            norm = cue.strip().lower()
            if not norm:
                continue
            tokens = [t for t in re.split(r"\s+", norm) if t]
            if tokens:
                items.append((cue, tokens, _CUE_WEIGHTS.get(norm, 1.0)))
        out[key] = items
    return out


_PRESET_CUES = _preset_cues()


def score_by_cue_vocabulary(transcript: list[dict]) -> PresetScores:
    """Count distinctiveness-weighted cue hits per preset, normalized.

    A cue of N tokens matches when N consecutive transcript words (after
    tokenization) equal the cue's tokens. Overlapping matches count once
    per starting position. Final per-preset score is scaled to [0, 1] by
    dividing through by the highest hit count; downstream weighting in
    :mod:`scoring` handles the per-tier weight.
    """
    if not transcript:
        return empty_scores()

    words = [_tokenize(str(w.get("word", ""))) for w in transcript]
    raw: dict[str, float] = {}

    for key, items in _PRESET_CUES.items():
        total = 0.0
        for _original, tokens, weight in items:
            n = len(tokens)
            if n == 1:
                target = tokens[0]
                total += sum(1 for w in words if w == target) * weight
            else:
                for i in range(len(words) - n + 1):
                    if words[i : i + n] == tokens:
                        total += weight
        raw[key] = total

    # Normalize so the highest-scoring preset hits 1.0; the scoring layer
    # handles inter-tier weighting and prevents a "tutorial with 40 hits"
    # from swamping a preset with naturally lower cue density.
    mx = max(raw.values(), default=0.0)
    if mx <= 0:
        return empty_scores()
    return {k: v / mx for k, v in raw.items()}
