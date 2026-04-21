"""Tier 0 — score presets from Resolve source metadata alone.

Runs before any transcript reading. Signals come from ``run["source_meta"]``
(persisted by :func:`cutmaster.core.pipeline._vfr_check`):

  - ``clip_count``   — 1 = raw capture (interview/podcast/presentation);
                        many = already-cut vlog/tutorial.
  - ``aspect``       — 9:16 rules out interview/presentation; 16:9 is
                        content-neutral but nudges away from reaction.
  - ``fps``          — 50–60 leans action/product demo over presentation.
  - ``total_duration_s`` — derived from scrubbed transcript when absent
                        from source_meta. Very short (<3 min) ⇒ reaction.

Each signal contributes a small ``[0, 1]`` score per preset, summed and
normalized. When ``run_state`` is ``None`` or ``source_meta`` is absent
(pre-Phase-2 runs, tests, direct callers) the scorer contributes neutral
zeros — the cascade still works, just without this tier's evidence.
"""

from __future__ import annotations

from .scoring import PresetScores, empty_scores


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _duration_from_run(run_state: dict) -> float:
    meta_dur = (run_state.get("source_meta") or {}).get("total_duration_s")
    if meta_dur:
        return float(meta_dur)
    scrubbed = run_state.get("scrubbed") or run_state.get("transcript") or []
    if scrubbed:
        return float(scrubbed[-1].get("end_time", 0.0))
    return 0.0


def score_by_metadata(run_state: dict | None) -> PresetScores:
    """Score presets from Resolve source metadata. Safe when state is sparse."""
    scores = empty_scores()
    if run_state is None:
        return scores

    meta = run_state.get("source_meta") or {}
    clip_count = int(meta.get("clip_count") or 0)
    aspect = float(meta.get("aspect") or 0.0)
    fps = float(meta.get("fps") or 0.0)
    duration_s = _duration_from_run(run_state)

    # --- clip_count --------------------------------------------------------
    # 1 clip on a long timeline is the raw-capture fingerprint: an
    # interview / podcast / presentation recorded in a single take. 30+
    # clips indicates a pre-edited piece — vlog or tutorial.
    if clip_count == 1:
        scores["interview"] += 0.4
        scores["podcast"] += 0.4
        scores["presentation"] += 0.4
    elif clip_count >= 30:
        scores["vlog"] += 0.4
        scores["tutorial"] += 0.3
        scores["product_demo"] += 0.2
    elif clip_count >= 10:
        # Partially cut — could be any format. Mild boost for vlog/tutorial.
        scores["vlog"] += 0.15
        scores["tutorial"] += 0.1

    # --- aspect ratio ------------------------------------------------------
    # 9:16 (~0.56) is a vertical phone format — incompatible with seated
    # interviews and stage talks. 16:9 (~1.78) is content-neutral.
    if 0 < aspect < 1.0:  # portrait / square-ish
        scores["vlog"] += 0.35
        scores["reaction"] += 0.25
        scores["product_demo"] += 0.15
        # Strong negative signal — zero them out rather than merely
        # discounting. Vertical-framed interviews / presentations are
        # vanishingly rare in the editing workflows this serves.
        scores["interview"] = 0.0
        scores["presentation"] = 0.0

    # --- frame rate --------------------------------------------------------
    # 50-60 fps is the action / product-demo tell (motion capture, UI
    # demos, gameplay). Presentations and interviews are universally 24/25/30.
    if fps >= 47.0:
        scores["product_demo"] += 0.25
        scores["vlog"] += 0.10
        scores["presentation"] += 0.0  # explicit no-op, documents the rule

    # --- total duration ----------------------------------------------------
    # <3 min ⇒ reaction or short vlog. >30 min ⇒ long-form (interview /
    # podcast / presentation / wedding).
    if 0 < duration_s < 180:
        scores["reaction"] += 0.3
        scores["vlog"] += 0.15
    elif duration_s >= 1800:
        scores["interview"] += 0.15
        scores["podcast"] += 0.2
        scores["presentation"] += 0.15
        scores["wedding"] += 0.15

    # Clamp so no single preset exceeds 1.0 after multiple signal bumps.
    return {k: _clamp(v) for k, v in scores.items()}
