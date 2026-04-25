/**
 * Client-side axis helpers — Phase 5 UI support for the three-axis model.
 *
 * Mirrors the Python authorities in
 * ``src/cutmaster_ai/cutmaster/data/{cut_intents,axis_compat,axis_resolution}.py``
 * so the panel can preview what the server will pick without a round-trip.
 *
 * Keep these in lockstep with the backend. The shapes are intentionally
 * narrow (the panel only needs the picker + compat + resolved-chip copy);
 * richer behaviour (full matrix lookups, pacing formula) stays server-side.
 */

import type { ContentType, CutIntent, CutIntentSource, TimelineMode } from "./types";

/** One-line catalogue of the five cut intents for the Axis 2 picker. */
export interface CutIntentInfo {
  key: CutIntent;
  label: string;
  description: string;
}

/**
 * Catalogue mirroring ``cut_intents.py``. Descriptions lifted verbatim —
 * keep in sync (no dedicated test; this is UX copy, not a contract).
 */
export const CUT_INTENTS: readonly CutIntentInfo[] = [
  {
    key: "narrative",
    label: "Narrative",
    description:
      "Tell a coherent story using the content's natural arc. Keeps the content profile's pacing and reorder policy intact.",
  },
  {
    key: "peak_highlight",
    label: "Peak highlight",
    description:
      "Pull the single highest-energy moment as a short reel or trailer. Tight pacing, free reorder, minimal setup.",
  },
  {
    key: "multi_clip",
    label: "Multi-clip",
    description:
      "Surface N self-contained clips from a long-form recording. Each clip stands alone.",
  },
  {
    key: "assembled_short",
    label: "Assembled short",
    description:
      "Compose one 45–90s short from 3–8 scattered spans. Jump cuts welcome; each beat earns its screen time.",
  },
  {
    key: "surgical_tighten",
    label: "Surgical tighten",
    description:
      "Preserve take order; drop filler, dead air, and restarts inside each take. Requires an already-assembled timeline.",
  },
];

/**
 * Axis 2 × Axis 4 compatibility matrix — mirrors
 * ``cutmaster/data/axis_compat.py::_AXIS2_TIMELINE_INCOMPATIBLE``. Returns
 * the rejection reason when the pair is blocked, or ``null`` when
 * compatible.
 */
export function cutIntentModeIncompatibilityReason(
  cutIntent: CutIntent,
  mode: TimelineMode,
): string | null {
  if (cutIntent === "surgical_tighten" && mode === "raw_dump") {
    return "Surgical tighten preserves take order — the source timeline must already be assembled. Switch to Assembled.";
  }
  if (cutIntent === "surgical_tighten" && mode === "rough_cut") {
    return "Surgical tighten can't pick between A/B alternates — it expects a single committed take per beat. Switch to Assembled.";
  }
  if (cutIntent === "surgical_tighten" && mode === "curated") {
    return "Surgical tighten needs the takes arranged in playback order — Curated hasn't committed to one yet. Switch to Assembled.";
  }
  if (cutIntent === "multi_clip" && mode === "assembled") {
    return "Multi-clip extraction assumes raw material. The source is already a single assembled cut — pick a different cut intent, or start from a non-assembled timeline.";
  }
  return null;
}

/**
 * Duration-band heuristic for Axis 2 auto-resolution — mirrors
 * ``cutmaster/data/axis_resolution.py::resolve_cut_intent``.
 *
 * The proposal's precedence:
 *   1. ``numClips > 1`` wins — explicit user signal.
 *   2. surgical-tighten shortcut when ``assembled + takes_already_scrubbed``.
 *   3. Duration bands with content-type exceptions.
 *
 * Returns ``{intent, reason}`` — the reason drives the resolved chip copy.
 */
export function resolveCutIntent(
  contentType: ContentType,
  durationS: number,
  numClips: number,
  mode: TimelineMode,
  takesAlreadyScrubbed: boolean,
): { intent: CutIntent; reason: string; source: CutIntentSource } {
  if (numClips > 1) {
    return {
      intent: "multi_clip",
      reason: `num_clips=${numClips} > 1 → multi-clip harvesting`,
      source: "forced",
    };
  }
  if (mode === "assembled" && takesAlreadyScrubbed) {
    return {
      intent: "surgical_tighten",
      reason:
        "timeline is assembled and takes are already scrubbed → surgical tighten",
      source: "forced",
    };
  }
  const d = Math.max(0, durationS);
  if (d < 45) {
    if (contentType === "product_demo") {
      return {
        intent: "assembled_short",
        reason: `${d.toFixed(0)}s under 45s; Product Demo prefers assembled shorts`,
        source: "auto",
      };
    }
    return {
      intent: "peak_highlight",
      reason: `${d.toFixed(0)}s under 45s → peak highlight`,
      source: "auto",
    };
  }
  if (d < 120) {
    if (contentType === "product_demo" || contentType === "vlog") {
      return {
        intent: "assembled_short",
        reason: `${d.toFixed(0)}s under 2min; ${contentType} prefers assembled shorts`,
        source: "auto",
      };
    }
    return {
      intent: "peak_highlight",
      reason: `${d.toFixed(0)}s under 2min → peak highlight`,
      source: "auto",
    };
  }
  if (d < 600) {
    if (contentType === "reaction") {
      return {
        intent: "peak_highlight",
        reason: `${d.toFixed(0)}s under 10min; reaction content peak-hunts`,
        source: "auto",
      };
    }
    return {
      intent: "narrative",
      reason: `${d.toFixed(0)}s under 10min → narrative arc`,
      source: "auto",
    };
  }
  return {
    intent: "narrative",
    reason: `${d.toFixed(0)}s long-form → narrative arc`,
    source: "auto",
  };
}

/**
 * Look up a cut-intent by key. Returns ``null`` for unknown keys —
 * surfacing a render gap beats crashing.
 */
export function getCutIntent(key: CutIntent): CutIntentInfo | null {
  return CUT_INTENTS.find((ci) => ci.key === key) ?? null;
}

/** Cells flagged ``unusual=true`` in ``axis_resolution.py::_MATRIX``. */
const UNUSUAL_CELLS: ReadonlySet<`${ContentType}|${CutIntent}`> = new Set([
  "tutorial|multi_clip",
]);

/** True when the (content_type, cut_intent) pair carries the "unusual" flag. */
export function isUnusualCombination(
  contentType: ContentType,
  cutIntent: CutIntent,
): boolean {
  return UNUSUAL_CELLS.has(`${contentType}|${cutIntent}`);
}
