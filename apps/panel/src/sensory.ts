/** Client-side mirror of `data/presets.py::SENSORY_MATRIX`.
 *
 * Kept in sync by hand — schema drift is caught by the /check-work pass
 * and the matrix sits in a file named next to `types.ts` so it's obvious
 * where to update. The resolver here decides which per-layer flags the
 * ConfigureScreen master toggle should set when it flips on.
 */

import type { PresetKey, TimelineMode, UserSettings } from "./types";

export type SensoryLayer = "c" | "a" | "audio";
export type ActivationLevel = "default" | "opt_in" | "off";

interface SensoryRow {
    c: ActivationLevel;
    a: ActivationLevel;
    audio: ActivationLevel;
}

export const SENSORY_MATRIX: Record<string, SensoryRow> = {
    raw_dump: { c: "default", a: "default", audio: "opt_in" },
    rough_cut: { c: "default", a: "default", audio: "opt_in" },
    curated: { c: "default", a: "default", audio: "opt_in" },
    assembled: { c: "default", a: "off", audio: "default" },
    clip_hunter: { c: "default", a: "off", audio: "opt_in" },
    short_generator: { c: "default", a: "default", audio: "default" },
};

export const SENSORY_SUBTITLES: Record<string, string> = {
    raw_dump:
        "Shot tagging + cut validation. Adds 30–60s on first analyze; cached after.",
    rough_cut:
        "Shot tagging helps pick winners between A/B takes; cut validation between takes.",
    curated:
        "Shot-variety tagging across takes; cut validation at take boundaries.",
    assembled:
        "Gesture-aware filler tightening + pause detection. Within-take cuts only.",
    clip_hunter:
        "Visual-energy scoring boosts engagement ranking; clip in/out validated.",
    short_generator:
        "Full stack — shot tagging, span boundary validation, beat-aware hook timing.",
};

export function sensoryModeKey(
    preset: PresetKey,
    timelineMode: TimelineMode | undefined,
): string {
    if (preset === "short_generator" || preset === "clip_hunter") return preset;
    if (preset === "tightener") return "assembled";
    if (
        timelineMode === "raw_dump" ||
        timelineMode === "rough_cut" ||
        timelineMode === "curated" ||
        timelineMode === "assembled"
    ) {
        return timelineMode;
    }
    return "raw_dump";
}

export interface ResolvedSensory {
    c: boolean;
    a: boolean;
    audio: boolean;
}

/** Given the current settings, compute effective per-layer booleans.
 *  Mirrors `resolve_sensory_layers` in the backend. */
export function resolveSensoryLayers(
    settings: UserSettings,
    preset: PresetKey,
): ResolvedSensory {
    const key = sensoryModeKey(preset, settings.timeline_mode);
    const row = SENSORY_MATRIX[key] ?? SENSORY_MATRIX.raw_dump;
    const master = !!settings.sensory_master_enabled;
    const pick = (level: ActivationLevel, override: boolean | null | undefined): boolean => {
        if (override === true || override === false) return override;
        if (!master) return false;
        return level === "default";
    };
    return {
        c: pick(row.c, settings.layer_c_enabled ?? null),
        a: pick(row.a, settings.layer_a_enabled ?? null),
        audio: pick(row.audio, settings.layer_audio_enabled ?? null),
    };
}
