/** TypeScript mirror of the backend Pydantic models. */

export type PresetKey =
  | "vlog"
  | "product_demo"
  | "wedding"
  | "interview"
  | "tutorial"
  | "podcast"
  | "reaction"
  | "tightener"
  | "clip_hunter"
  | "short_generator"
  | "auto";

export interface ExcludeCategory {
  key: string;
  label: string;
  description: string;
  checked_by_default: boolean;
}

export interface PresetBundle {
  key: PresetKey;
  label: string;
  role: string;
  hook_rule: string;
  pacing: string;
  cue_vocabulary: string[];
  marker_vocabulary: string[];
  theme_axes: string[];
  scrub_defaults: Record<string, unknown>;
  exclude_categories: ExcludeCategory[];
  default_custom_focus_placeholder: string;
}

export interface TranscriptWord {
  word: string;
  speaker_id: string;
  start_time: number;
  end_time: number;
}

export interface PresetRecommendation {
  preset: PresetKey;
  confidence: number;
  reasoning: string;
}

export interface Chapter {
  start_s: number;
  end_s: number;
  title: string;
}

export interface HookCandidate {
  start_s: number;
  end_s: number;
  text: string;
  engagement_score: number;
}

export interface StoryAnalysis {
  chapters: Chapter[];
  hook_candidates: HookCandidate[];
  theme_candidates: string[];
}

export interface CutSegment {
  start_s: number;
  end_s: number;
  reason: string;
}

export interface DirectorPlan {
  hook_index: number;
  selected_clips: CutSegment[];
  reasoning: string;
}

export interface MarkerSuggestion {
  at_s: number;
  color: string;
  name: string;
  note: string;
  duration_frames: number;
}

export interface MarkerPlan {
  markers: MarkerSuggestion[];
}

export interface ResolvedCutSegment {
  start_s: number;
  end_s: number;
  reason: string;
  source_item_id: string;
  source_item_name: string;
  source_in_frame: number;
  source_out_frame: number;
  timeline_start_frame: number;
  timeline_end_frame: number;
  speed: number;
  speed_ramped: boolean;
  warnings: string[];
}

export interface TightenerSummary {
  kept_words: number;
  original_words: number;
  percent_tighter: number;
  take_total_s: number;
  segment_total_s: number;
}

export interface GroupInfo {
  group_id: number;
  item_indexes: number[];
  signal: "color" | "flag" | "similarity" | "singleton";
}

export interface TimelineStateMeta {
  mode: "curated" | "rough_cut";
  takes_used: number[];
  total_takes: number;
  groups?: GroupInfo[];
  all_singletons?: boolean;
}

export interface BuildPlanResult {
  preset: PresetKey;
  user_settings: UserSettings;
  director: DirectorPlan;
  markers: MarkerPlan;
  resolved_segments: ResolvedCutSegment[];
  tightener?: TightenerSummary;
  clip_hunter?: ClipHunterSummary;
  timeline_state?: TimelineStateMeta;
}

export type FormatKey = "horizontal" | "vertical_short" | "square";

export interface SafeZones {
  top_pct: number;
  bottom_pct: number;
  left_pct: number;
  right_pct: number;
}

export interface FormatSpec {
  key: FormatKey;
  label: string;
  width: number;
  height: number;
  max_duration_s: number | null;
  safe_zones: SafeZones;
  reframe_default: "center_crop" | "smart_reframe" | "none";
}

export type TimelineMode = "raw_dump" | "rough_cut" | "curated" | "assembled";

export interface UserSettings {
  target_length_s: number | null;
  themes: string[];
  scrub_params?: Record<string, unknown> | null;
  exclude_categories?: string[];
  custom_focus?: string | null;
  format?: FormatKey;
  captions_enabled?: boolean;
  safe_zones_enabled?: boolean;
  timeline_mode?: TimelineMode;
  reorder_allowed?: boolean;
  takes_already_scrubbed?: boolean;
  num_clips?: number;
  speaker_labels?: Record<string, string> | null;
  selected_hook_s?: number | null;
  // v4 Phase 4.4 sensory-layer toggles. Master drives the matrix; the
  // per-layer fields are tri-state overrides (null = defer to matrix,
  // true = force on, false = force off).
  sensory_master_enabled?: boolean;
  layer_c_enabled?: boolean | null;
  layer_a_enabled?: boolean | null;
  layer_audio_enabled?: boolean | null;
}

export interface SpeakerRosterEntry {
  speaker_id: string;
  word_count: number;
}

export interface TimelineInfo {
  name: string;
  is_current: boolean;
  item_count: number;
}

export interface ProjectInfo {
  project_name: string;
  timelines: TimelineInfo[];
}

export type SttProviderKey = "gemini" | "deepgram";

export interface SttProviderInfo {
  key: SttProviderKey;
  label: string;
  configured: boolean;
}

export interface SttProviderList {
  default: SttProviderKey;
  providers: SttProviderInfo[];
}

export interface ClipCandidate {
  // Clip Hunter shape: one contiguous span.
  start_s?: number;
  end_s?: number;
  quote?: string;
  // Short Generator shape: multiple spans around a theme.
  theme?: string;
  spans?: { start_s: number; end_s: number; role?: string }[];
  total_s?: number;
  // Shared fields.
  engagement_score: number;
  suggested_caption: string;
  reasoning: string;
  resolved_segments: ResolvedCutSegment[];
}

export interface ClipHunterSummary {
  candidates: ClipCandidate[];
  selected_index: number;
  target_clip_length_s: number;
  num_clips: number;
  duration_warning: string | null;
  source_duration_s: number;
  mode?: "clip_hunter" | "short_generator";
}

export interface ScrubParams {
  remove_fillers?: boolean;
  remove_dead_air?: boolean;
  collapse_restarts?: boolean;
  dead_air_threshold_s?: number;
}

export type StageName =
  | "vfr_check"
  | "audio_extract"
  | "stt"
  | "scrub"
  | "done"
  | "error";

export interface PipelineEvent {
  stage: StageName | string;
  status: "started" | "complete" | "failed" | "progress" | string;
  message: string;
  data: unknown;
  ts: number;
}

export interface RunState {
  run_id: string;
  timeline_name: string;
  preset: string;
  created_at: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  stages: Record<string, Partial<PipelineEvent>>;
  events: PipelineEvent[];
  transcript: TranscriptWord[];
  scrubbed: TranscriptWord[];
  plan?: BuildPlanResult;
  user_settings?: UserSettings;
  review_state?: {
    selected_candidate?: number | null;
    custom_name?: string | null;
    replace_existing?: boolean;
  };
  execute_history?: ExecuteHistoryEntry[];
  error: string | null;
}

export interface ExecuteHistoryEntry {
  new_timeline_name: string | null;
  custom_name: string | null;
  replaced_timelines?: string[];
  snapshot_path?: string | null;
  at: number;
  aborted?: boolean;
}

export interface RunSummary {
  run_id: string;
  created_at: string | null;
  timeline_name: string;
  preset: string;
  status: string;
  has_transcript: boolean;
  has_plan: boolean;
  execute_history: ExecuteHistoryEntry[];
  size_kb: number;
  last_modified: number;
}

export interface RunListResponse {
  runs: RunSummary[];
  total: number;
  truncated: boolean;
}
