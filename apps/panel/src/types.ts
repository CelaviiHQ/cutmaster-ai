/** TypeScript mirror of the backend Pydantic models. */

/**
 * @deprecated since Phase 4 of the three-axis model. Send
 * ``content_type`` + ``UserSettings.cut_intent`` on new requests. The
 * backend auto-remaps ``PresetKey`` for one release so legacy clients
 * keep working; Phase 7 removes the alias.
 */
export type PresetKey =
  | "vlog"
  | "product_demo"
  | "wedding"
  | "interview"
  | "tutorial"
  | "podcast"
  | "presentation"
  | "reaction"
  | "tightener"
  | "clip_hunter"
  | "short_generator"
  | "auto";

// -----------------------------------------------------------------------
// Three-axis model (Phase 4) — content type, cut intent, resolved axes.
// -----------------------------------------------------------------------

/** The 8 resolved content types. Never contains ``auto_detect``. */
export type ContentType =
  | "vlog"
  | "product_demo"
  | "wedding"
  | "interview"
  | "tutorial"
  | "podcast"
  | "presentation"
  | "reaction";

/** Wire-level content types. ``auto_detect`` means "run the cascade". */
export type RequestedContentType = ContentType | "auto_detect";

/** Axis 2 — what the user is making from the content. */
export type CutIntent =
  | "narrative"
  | "peak_highlight"
  | "multi_clip"
  | "assembled_short"
  | "surgical_tighten";

/** How the Director may reorder segments relative to source time. */
export type ReorderMode =
  | "free"
  | "preserve_macro"
  | "locked"
  | "per_clip_chronological";

/** Which axis strategy drives segment selection. */
export type SelectionStrategy =
  | "narrative-arc"
  | "peak-hunt"
  | "top-n"
  | "montage"
  | "preserve-takes";

/** Which Director prompt builder the resolved axes route to. */
export type PromptBuilder =
  | "_prompt"
  | "_assembled_prompt"
  | "_clip_hunter_prompt"
  | "_short_generator_prompt"
  | "_curated_prompt"
  | "_rough_cut_prompt";

/** Per-segment duration bounds in seconds. */
export interface SegmentPacing {
  min: number;
  target: number;
  max: number;
}

/** Provenance tag on `ResolvedAxes.cut_intent_source` — populated by the
 *  backend axis resolver (Phase 6.3). `"user"` / `"auto"` / `"forced"`. */
export type CutIntentSource = "user" | "auto" | "forced";

/** Fully resolved cut recipe — Axis 1 × Axis 2 × duration × timeline_mode. */
export interface ResolvedAxes {
  content_type: ContentType;
  cut_intent: CutIntent;
  cut_intent_source?: CutIntentSource;
  reorder_mode: ReorderMode;
  segment_pacing: SegmentPacing;
  selection_strategy: SelectionStrategy;
  prompt_builder: PromptBuilder;
  rationale: string[];
  unusual: boolean;
}

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
  min_segment_s: number;
  target_segment_s: number;
  max_segment_s: number;
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

export interface CascadeSignals {
  /** Top-scoring preset after the full cascade merge: [key, score]. */
  top1: [string, number] | null;
  /** Runner-up preset after the full cascade merge: [key, score]. */
  top2: [string, number] | null;
  /** Gap between top1 and top2 — drives the final confidence value. */
  margin: number;
  /** Tiers that contributed signal ("tier0".."tier4"). */
  tiers_invoked: string[];
  /** Wall-clock cost of the classification call, in milliseconds. */
  elapsed_ms: number;
}

export interface PresetRecommendation {
  preset: PresetKey;
  confidence: number;
  reasoning: string;
  /** Sensible default target length the Configure screen can prefill. Null
   *  when no signal exists to guess (e.g. Tightener / Clip Hunter). */
  suggested_target_length_s?: number | null;
  /** Runner-up presets when confidence is low. Empty when confident. */
  alternatives?: PresetKey[];
  /** Cascade telemetry — null when recommendation built outside the cascade. */
  signals?: CascadeSignals | null;
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

/**
 * Story-arc function a segment plays in the final cut. ``null`` when
 * the producer (legacy fallback path, tightener) didn't ask the model
 * to label — the panel renders no badge in that case.
 */
export type ArcRole =
  | "hook"
  | "setup"
  | "reinforce"
  | "escalate"
  | "resolve"
  | "cta";

export interface CutSegment {
  start_s: number;
  end_s: number;
  reason: string;
  arc_role?: ArcRole | null;
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

/**
 * Story-coherence critic verdict band. Derived server-side from the
 * overall score (`<60` → rework, `60-79` → review, `≥80` → ship).
 */
export type Verdict = "ship" | "review" | "rework";

/**
 * Issue category emitted by the critic. Mirrors the Pydantic Literal in
 * `intelligence/story_critic.py` exactly — keep in sync if extended.
 */
export type CoherenceCategory =
  | "non_sequitur"
  | "weak_hook"
  | "missing_setup"
  | "abrupt_transition"
  | "redundancy"
  | "unresolved_thread"
  | "inverted_arc"
  | "weak_resolution"
  | "buried_lede";

export type CoherenceSeverity = "info" | "warning" | "error";

export interface CoherenceIssue {
  /** Index of the segment the issue points at; `-1` means whole-cut. */
  segment_index: number;
  /** Set on transition issues — refers to seg N → N+1. */
  pair_index?: number | null;
  severity: CoherenceSeverity;
  category: CoherenceCategory;
  message: string;
  suggestion?: string | null;
}

export interface CoherenceReport {
  score: number;
  hook_strength: number;
  /** Null on `surgical_tighten` cuts — that intent doesn't grade arc. */
  arc_clarity: number | null;
  /** Null on `surgical_tighten` cuts — that intent doesn't grade transitions. */
  transitions: number | null;
  resolution: number;
  issues: CoherenceIssue[];
  summary: string;
  verdict: Verdict;
}

export interface PerCandidateCoherenceReport {
  candidates: CoherenceReport[];
  best_candidate_index: number;
  summary: string;
}

/**
 * Envelope persisted on `run["plan"]["coherence_report"]`. The `kind`
 * tag lets the panel branch on shape without sniffing fields.
 */
export type CoherenceReportEnvelope =
  | { kind: "single"; report: CoherenceReport }
  | { kind: "per_candidate"; report: PerCandidateCoherenceReport };

/**
 * Vlogger-friendly translation of a Director validator warning.
 * Backend `_humanise_validator_warning` builds these from the raw
 * validator strings so the panel can render plain English with
 * optional inline actions instead of dumping `segment[2]: starts on
 * low-confidence word…` at the editor.
 */
export type PlanWarningActionKind =
  | "configure_hook"
  | "configure_target_length"
  | "regenerate";

export interface PlanWarningAction {
  label: string;
  kind: PlanWarningActionKind;
  payload?: Record<string, unknown>;
}

export type PlanWarningKind =
  | "low_confidence_hook"
  | "low_confidence_start"
  | "low_confidence_end"
  | "low_coverage"
  | "segment_too_short"
  | "segment_too_long"
  | "duplicate_takes"
  | "other";

export interface PlanWarning {
  kind: PlanWarningKind;
  title: string;
  detail: string;
  action?: PlanWarningAction;
  /** Original validator string — kept for tooltips / debugging. */
  raw: string;
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
  /** Three-axis recipe (Phase 4.6). Present when the request supplied
   *  axes-keyed context; absent for pure legacy-preset calls. */
  resolved_axes?: ResolvedAxes;
  /** Story-critic verdict on the built cut. ``null`` / absent when the
   *  flag is off, the LLM failed, or no resolved_axes were available. */
  coherence_report?: CoherenceReportEnvelope | null;
  /** Story-critic Phase 6 history. Carries 1 envelope (no rework / single
   *  pass) or 2 envelopes (rework fired) — the final one mirrors
   *  ``coherence_report``. Lets the Review screen show "Pass 1: 58 →
   *  Pass 2: 82" when the auto-rework loop lifted the verdict. */
  coherence_history?: CoherenceReportEnvelope[];
  /** Validation residue from a best-effort Director call (the model failed
   *  a hard constraint but llm.call_structured returned the best-of-bad
   *  plan after exhausting retries). Empty / absent when the plan
   *  satisfies every constraint. Surfaced so the editor learns when
   *  their hook / target / pacing pick wasn't fully honoured. */
  plan_warnings?: PlanWarning[];
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
  // Three-axis Axis 2 (Phase 4). null = auto-resolve by duration / num_clips.
  cut_intent?: CutIntent | null;
  // v4 Phase 4.4 sensory-layer toggles. Master drives the matrix; the
  // per-layer fields are tri-state overrides (null = defer to matrix,
  // true = force on, false = force off).
  sensory_master_enabled?: boolean;
  layer_c_enabled?: boolean | null;
  layer_a_enabled?: boolean | null;
  layer_audio_enabled?: boolean | null;
  // Story-critic per-build opt-in. null/false = skip, true = run.
  // Server env var CUTMASTER_ENABLE_STORY_CRITIC=1 always wins when set.
  story_critic_enabled?: boolean | null;
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

export interface TrackInfo {
  index: number;
  name: string;
  item_count: number;
  picked_by_default: boolean;
}

export interface TrackListResponse {
  video_tracks: TrackInfo[];
  audio_tracks: TrackInfo[];
  picked_video: number | null;
  picked_audio: number | null;
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
  /** Three-axis Axis 1, populated from the legacy ``preset`` alias. */
  content_type?: RequestedContentType | null;
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
