/** Typed HTTP client for the cutmaster-ai-panel backend. */

import type {
  BuildPlanResult,
  FormatSpec,
  PresetBundle,
  PresetRecommendation,
  ProjectInfo,
  RunListResponse,
  RunState,
  SpeakerRosterEntry,
  SttProviderKey,
  SttProviderList,
  StoryAnalysis,
  TrackListResponse,
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

  projectInfo: () => http<ProjectInfo>("/cutmaster/project-info"),

  sttProviders: () =>
    http<SttProviderList>("/cutmaster/stt-providers"),

  listFormats: () =>
    http<{ formats: FormatSpec[] }>("/cutmaster/formats"),

  sourceAspect: (runId: string) =>
    http<SourceAspectInfo>(`/cutmaster/source-aspect/${runId}`),

  speakers: (runId: string) =>
    http<{ speakers: SpeakerRosterEntry[] }>(`/cutmaster/speakers/${runId}`),

  tracks: (timelineName: string) =>
    http<TrackListResponse>(
      `/cutmaster/tracks/${encodeURIComponent(timelineName)}`,
    ),

  analyze: (
    timelineName: string,
    preset: string,
    options?: {
      perClipStt?: boolean;
      expectedSpeakers?: number | null;
      sttProvider?: SttProviderKey | null;
      sensoryMasterEnabled?: boolean;
      layerCEnabled?: boolean;
      layerAudioEnabled?: boolean;
      videoTrack?: number | null;
      audioTrack?: number | null;
    },
  ) =>
    http<{ run_id: string; status: string }>("/cutmaster/analyze", {
      method: "POST",
      body: JSON.stringify({
        timeline_name: timelineName,
        preset,
        ...(options?.perClipStt ? { per_clip_stt: true } : {}),
        ...(options?.expectedSpeakers != null
          ? { expected_speakers: options.expectedSpeakers }
          : {}),
        ...(options?.sttProvider
          ? { stt_provider: options.sttProvider }
          : {}),
        ...(options?.sensoryMasterEnabled
          ? { sensory_master_enabled: true }
          : {}),
        ...(options?.layerCEnabled ? { layer_c_enabled: true } : {}),
        ...(options?.layerAudioEnabled ? { layer_audio_enabled: true } : {}),
        ...(options?.videoTrack != null ? { video_track: options.videoTrack } : {}),
        ...(options?.audioTrack != null ? { audio_track: options.audioTrack } : {}),
      }),
    }),

  getState: (runId: string) =>
    http<RunState>(`/cutmaster/state/${runId}`),

  cancel: (runId: string) =>
    http<{ run_id: string; status: string; noop: boolean }>(
      `/cutmaster/cancel/${runId}`,
      { method: "POST" },
    ),

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

  themesCache: (runId: string) =>
    http<{ preset: string; analysis: StoryAnalysis }>(
      `/cutmaster/themes-cache/${runId}`,
    ),

  buildPlan: (
    runId: string,
    preset: string,
    userSettings: UserSettings,
    contentType?: string | null,
    /**
     * Optional story-critic feedback from a prior build, surfaced into
     * the Director's first call as a rework prompt. Used by the panel's
     * "Regenerate with recommendations" button on the Cut-health card.
     */
    criticFeedback?: Record<string, unknown> | null,
  ) =>
    http<BuildPlanResult>("/cutmaster/build-plan", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        preset,
        // Phase 5.8 — three-axis Axis 1. Panel sends ``content_type``
        // explicitly when set; backend validator wins over legacy
        // preset remapping when both arrive. Omit when null so
        // pre-three-axis clients stay wire-compatible.
        ...(contentType ? { content_type: contentType } : {}),
        user_settings: userSettings,
        ...(criticFeedback ? { critic_feedback: criticFeedback } : {}),
      }),
    }),

  execute: (
    runId: string,
    candidateIndex?: number,
    customName?: string | null,
    replaceExisting: boolean = false,
  ) =>
    http<ExecuteResult>("/cutmaster/execute", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        ...(candidateIndex != null ? { candidate_index: candidateIndex } : {}),
        ...(customName && customName.trim() ? { custom_name: customName.trim() } : {}),
        ...(replaceExisting ? { replace_existing: true } : {}),
      }),
    }),

  deleteCut: (runId: string) =>
    http<DeleteCutResult>("/cutmaster/delete-cut", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  deleteAllCuts: (runId: string) =>
    http<{ deleted: string[]; skipped: string[] }>("/cutmaster/delete-all-cuts", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  listRuns: (opts?: { limit?: number; status?: string; timeline?: string }) => {
    const params = new URLSearchParams();
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.status) params.set("status", opts.status);
    if (opts?.timeline) params.set("timeline", opts.timeline);
    const qs = params.toString();
    return http<RunListResponse>(`/cutmaster/runs${qs ? `?${qs}` : ""}`);
  },

  deleteRun: (runId: string) =>
    http<{ run_id: string; removed: string[] }>("/cutmaster/delete-run", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  cloneRun: (runId: string) =>
    http<{
      run_id: string;
      cloned_from: string;
      timeline_name: string;
      preset: string;
      status: string;
      has_transcript: boolean;
    }>("/cutmaster/clone-run", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  paintShotColors: (
    timelineName: string,
    opts?: { overwrite?: boolean; videoTrack?: number },
  ) =>
    http<PaintShotColorsResult>("/cutmaster/paint-shot-colors", {
      method: "POST",
      body: JSON.stringify({
        timeline_name: timelineName,
        ...(opts?.overwrite ? { overwrite: true } : {}),
        ...(opts?.videoTrack ? { video_track: opts.videoTrack } : {}),
      }),
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
  replaced_timelines?: string[];
}

export interface PaintShotColorsResult {
  timeline_name: string;
  total_items: number;
  painted: number;
  skipped_already_colored: number;
  skipped_no_tags: number;
  skipped_unknown: number;
  rows: Array<{
    item_index: number;
    action:
      | "painted"
      | "skipped_already_colored"
      | "skipped_no_tags"
      | "skipped_unknown";
    shot_type?: string | null;
    color?: string | null;
  }>;
  color_legend: Record<string, string>;
}

export interface DeleteCutResult {
  deleted: boolean;
  timeline?: string;
  reason?: string;
  snapshot_preserved_at?: string;
}
