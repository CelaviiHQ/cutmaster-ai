/** Typed HTTP client for the celavii-resolve-panel backend. */

import type {
  BuildPlanResult,
  FormatSpec,
  PresetBundle,
  PresetRecommendation,
  RunState,
  StoryAnalysis,
  UserSettings,
} from "./types";

export interface SourceAspectInfo {
  width: number;
  height: number;
  aspect: number;
  recommended_format: "horizontal" | "vertical_short" | "square";
}

/** All requests are same-origin in production (served from the Python app).
 *  In dev, Vite proxies /cutmaster/* and /ping to 127.0.0.1:8765. */
const BASE = "";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path} — ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  ping: () => http<{ ok: boolean; service: string; version: string }>("/ping"),

  listPresets: () =>
    http<{ presets: PresetBundle[] }>("/cutmaster/presets"),

  listFormats: () =>
    http<{ formats: FormatSpec[] }>("/cutmaster/formats"),

  sourceAspect: (runId: string) =>
    http<SourceAspectInfo>(`/cutmaster/source-aspect/${runId}`),

  analyze: (timelineName: string, preset: string) =>
    http<{ run_id: string; status: string }>("/cutmaster/analyze", {
      method: "POST",
      body: JSON.stringify({ timeline_name: timelineName, preset }),
    }),

  getState: (runId: string) =>
    http<RunState>(`/cutmaster/state/${runId}`),

  detectPreset: (runId: string) =>
    http<PresetRecommendation>("/cutmaster/detect-preset", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  analyzeThemes: (runId: string, preset: string) =>
    http<StoryAnalysis>("/cutmaster/analyze-themes", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, preset }),
    }),

  buildPlan: (runId: string, preset: string, userSettings: UserSettings) =>
    http<BuildPlanResult>("/cutmaster/build-plan", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        preset,
        user_settings: userSettings,
      }),
    }),

  execute: (runId: string, candidateIndex?: number) =>
    http<ExecuteResult>("/cutmaster/execute", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        ...(candidateIndex != null ? { candidate_index: candidateIndex } : {}),
      }),
    }),

  deleteCut: (runId: string) =>
    http<DeleteCutResult>("/cutmaster/delete-cut", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),
};

export interface ExecuteResult {
  new_timeline_name: string;
  appended: number;
  append_errors: string[];
  markers_added: number;
  markers_skipped: Array<{ name?: string; original_at_s: number; reason: string }>;
  snapshot_path: string;
  snapshot_size_kb: number;
  format?: { format: string; width: number; height: number; resolution_warning?: string };
  captions?: {
    enabled: boolean;
    lines?: number;
    path?: string | null;
    subtitle_track?: { ok: boolean; method?: string; reason?: string; error?: string };
  };
  safe_zones?: { enabled: boolean; added?: number; reason?: string };
}

export interface DeleteCutResult {
  deleted: boolean;
  timeline?: string;
  reason?: string;
  snapshot_preserved_at?: string;
}
