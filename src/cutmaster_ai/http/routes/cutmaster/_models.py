"""Pydantic request/response models shared across cutmaster route modules."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ....cutmaster.analysis.scrubber import ScrubParams
from ....cutmaster.data.content_profiles import RequestedContentType
from ....cutmaster.data.cut_intents import CutIntent

log = logging.getLogger("cutmaster-ai.http.cutmaster.models")


# ---------------------------------------------------------------------------
# Phase 4.2 — legacy preset → (content_type, cut_intent, overrides) remapper
# ---------------------------------------------------------------------------
#
# Translates the 12-preset picker's output into the three-axis model's
# explicit variables. Four call sites (AnalyzeRequest, AnalyzeThemesRequest,
# BuildPlanRequest, RunSummary) use this to absorb legacy clients during
# the deprecation window. Phase 7 removes the remapper + the structured
# log once 30 days of zero ``legacy_preset_alias_used`` traffic confirm
# no lingering callers.

# Three legacy presets are cut intents wearing a content-type costume;
# the rest are first-class content types. "auto" means "run the cascade".
_LEGACY_CUT_INTENT_PRESETS: dict[str, tuple[RequestedContentType, CutIntent, dict]] = {
    "tightener": ("auto_detect", "surgical_tighten", {"timeline_mode": "assembled"}),
    "clip_hunter": ("auto_detect", "multi_clip", {}),
    "short_generator": ("auto_detect", "assembled_short", {}),
}

_LEGACY_CONTENT_TYPE_PRESETS: frozenset[str] = frozenset(
    {
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "presentation",
        "reaction",
    }
)


def _remap_legacy_preset(raw: str) -> tuple[RequestedContentType, CutIntent | None, dict]:
    """Split a legacy preset key into ``(content_type, cut_intent, overrides)``.

    Cut-intent presets (tightener / clip_hunter / short_generator) map to
    ``auto_detect`` + the matching intent; ``tightener`` additionally
    forces ``timeline_mode=assembled``. Content-type presets (vlog,
    interview, ...) map to themselves with ``cut_intent=None`` so the
    resolver auto-picks via duration bands. ``"auto"`` keeps
    ``auto_detect`` and leaves the cut intent unresolved.

    Unknown keys fall back to ``("auto_detect", None, {})`` — the resolver
    + compat check produce the right error downstream rather than the
    remapper raising on a typo.
    """
    if raw in _LEGACY_CUT_INTENT_PRESETS:
        return _LEGACY_CUT_INTENT_PRESETS[raw]
    if raw in _LEGACY_CONTENT_TYPE_PRESETS:
        return (raw, None, {})  # type: ignore[return-value]
    # "auto" + unknown both resolve through the cascade during analyze;
    # the build-plan handler rejects unknown presets before this matters.
    return ("auto_detect", None, {})


def _apply_legacy_preset_alias(values: Any, endpoint: str) -> Any:
    """Populate ``content_type`` / ``cut_intent`` from a legacy ``preset`` field.

    When the caller only supplied the legacy ``preset`` string (no
    ``content_type`` override), remap and emit a structured log entry
    so Phase 7's 30-day telemetry gate has quantitative traffic.

    Accepts the pydantic ``@model_validator(mode="after")`` model-instance
    shape; returns the instance (possibly with fields updated).
    """
    # Only remap when the caller hasn't already set the axes-keyed fields.
    raw_preset = getattr(values, "preset", None)
    content_type = getattr(values, "content_type", None)
    if not raw_preset or content_type is not None:
        return values

    mapped_ct, mapped_intent, overrides = _remap_legacy_preset(raw_preset)

    if hasattr(values, "content_type"):
        values.content_type = mapped_ct
    # cut_intent lives on UserSettings for BuildPlanRequest; AnalyzeRequest
    # doesn't carry UserSettings — we only surface the mapped value via the
    # structured log so pipeline/build stages can re-derive it.
    user_settings = getattr(values, "user_settings", None)
    if user_settings is not None and getattr(user_settings, "cut_intent", None) is None:
        user_settings.cut_intent = mapped_intent
        if "timeline_mode" in overrides:
            user_settings.timeline_mode = overrides["timeline_mode"]  # type: ignore[assignment]

    log.info(
        "legacy_preset_alias_used",
        extra={
            "event": "legacy_preset_alias_used",
            "endpoint": endpoint,
            "preset": raw_preset,
            "mapped_content_type": mapped_ct,
            "mapped_cut_intent": mapped_intent,
        },
    )
    return values


class AnalyzeRequest(BaseModel):
    timeline_name: str
    preset: str = Field(
        default="auto",
        description=(
            "DEPRECATED alias for ``content_type`` + ``cut_intent`` — kept "
            "during the three-axis migration so legacy clients keep "
            "working. See Phase 4 of "
            "Implementation/workflow/three-axis-model.md. When ``content_type`` "
            "is unset, the server remaps ``preset`` and emits a "
            "``legacy_preset_alias_used`` structured log."
        ),
    )
    content_type: RequestedContentType | None = Field(
        default=None,
        description=(
            "Three-axis Axis 1 — the resolved content-type key (``vlog``, "
            "``interview``, ...) or the ``auto_detect`` sentinel. When set, "
            "wins over the legacy ``preset`` field."
        ),
    )
    scrub_params: ScrubParams | None = Field(default=None)
    per_clip_stt: bool = Field(
        default=False,
        description=(
            "v2-6: when true, extract one WAV per timeline audio item and "
            "transcribe each clip separately. Words get clip_index + "
            "clip_metadata for the Director prompt; per-clip results cache "
            "under ~/.cutmaster/cutmaster/per-clip-stt so re-analyze on a "
            "trimmed timeline only re-transcribes changed takes. Off by "
            "default during v2 A/B trial."
        ),
    )
    expected_speakers: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description=(
            "v2-6 follow-up: expected number of distinct real-world speakers "
            "across the shoot. Only meaningful with per_clip_stt=True, where "
            "Gemini assigns clip-local speaker_ids that don't align across "
            "clips. When 1, the pipeline trivially collapses every speaker "
            "to S1 (no LLM call). When >=2, a cheap Gemini-Flash-Lite "
            "reconciliation call remaps clip-local IDs onto a consistent "
            "global roster. When None, raw per-clip IDs are left in place."
        ),
    )
    stt_provider: Literal["gemini", "deepgram"] | None = Field(
        default=None,
        description=(
            "Per-run STT backend override. 'gemini' is the v1 default; "
            "'deepgram' routes through Deepgram Nova-3 (requires "
            "DEEPGRAM_API_KEY) and is the right choice for long-form "
            "content since it has no word-level output token cap. "
            "Falls back to CUTMASTER_STT_PROVIDER env var, then to 'gemini'."
        ),
    )
    # Source-track overrides. None = auto-pick via
    # :func:`track_picker.pick_video_track` / ``pick_audio_tracks``.
    video_track: int | None = Field(
        default=None,
        ge=1,
        description=(
            "1-based video track to treat as the picture edit. None "
            "auto-picks (prefer V1 if non-empty, else lowest-numbered "
            "non-empty)."
        ),
    )
    audio_track: int | None = Field(
        default=None,
        ge=1,
        description=(
            "1-based audio track to transcribe. None auto-picks (prefer "
            "dialogue-labelled tracks, else lowest non-music non-empty)."
        ),
    )
    # v4 Phase 4.4: panel resolves master + matrix + overrides client-side
    # and sends the final booleans here. ``sensory_master_enabled`` is
    # round-tripped for parity with UserSettings but isn't consulted by
    # the pipeline — the resolver already baked it in.
    sensory_master_enabled: bool = Field(
        default=False,
        description=(
            "v4 'Shot-aware editing' master toggle. Round-tripped for "
            "completeness; pipeline reads layer_c_enabled / "
            "layer_audio_enabled directly."
        ),
    )
    # v4 Layer C — shot tagging during analyze. Tri-state to match the
    # build envelope: True = force on, False = force off, None = follow
    # the SENSORY_MATRIX row for the resolved (cut_intent, timeline_mode)
    # cell. Default is None so pure-API callers that flip just
    # ``sensory_master_enabled`` get matrix-driven defaults without
    # having to know each row. Panel callers already send a concrete
    # bool, so the widening is backwards-compatible.
    layer_c_enabled: bool | None = Field(
        default=None,
        description=(
            "v4 Layer C: when true, sample frames from each V1 video "
            "item post-scrub and tag them via Gemini vision. Tags cache "
            "under ~/.cutmaster/cutmaster/shot-tags/v1/<sha1(source_path)>/ "
            "so re-analyze after edits reuses prior work. No-op without "
            "GEMINI_API_KEY (stage emits 'failed' and the pipeline "
            "continues with un-annotated transcript). None = follow "
            "the matrix default for the preset's row."
        ),
    )
    # v4 Layer Audio — deterministic DSP cues. No Gemini dependency;
    # runs on the ffmpeg binary the pipeline already requires. Tri-state
    # for the same reason as Layer C: omission means "follow the matrix"
    # so script callers don't have to pre-resolve.
    layer_audio_enabled: bool | None = Field(
        default=None,
        description=(
            "v4 Layer Audio: when true, run ffmpeg silencedetect + astats "
            "over timeline audio and attach per-word cues "
            "(pause_before_ms, pause_after_ms, rms_db_delta, "
            "is_silence_tail) to the scrubbed transcript. No Gemini API "
            "key needed. Cues cache under "
            "~/.cutmaster/cutmaster/audio-cues/v1/<sha1(wav_signature)>/. "
            "Stage is best-effort: ffmpeg failures surface as a failed "
            "event but the pipeline continues with unannotated words. "
            "None = follow the matrix default for the preset's row."
        ),
    )

    @model_validator(mode="after")
    def _apply_legacy_alias(self) -> AnalyzeRequest:
        return _apply_legacy_preset_alias(self, endpoint="analyze")


class AnalyzeResponse(BaseModel):
    run_id: str
    status: str


class SourceAspectResponse(BaseModel):
    width: int
    height: int
    aspect: float
    recommended_format: str


class TimelineInfo(BaseModel):
    name: str
    is_current: bool
    item_count: int


class TrackInfoResponse(BaseModel):
    """One row per track — feeds the PresetPickScreen override picker."""

    index: int
    name: str
    item_count: int
    picked_by_default: bool


class TrackListResponse(BaseModel):
    video_tracks: list[TrackInfoResponse]
    audio_tracks: list[TrackInfoResponse]
    picked_video: int | None = Field(
        default=None,
        description="1-based auto-picked video track, or None when every track is empty.",
    )
    picked_audio: int | None = Field(
        default=None,
        description="1-based auto-picked dialogue track, or None when every track is empty.",
    )


class ProjectInfoResponse(BaseModel):
    project_name: str
    timelines: list[TimelineInfo]


class SpeakerRosterEntry(BaseModel):
    speaker_id: str
    word_count: int


class SpeakerRosterResponse(BaseModel):
    speakers: list[SpeakerRosterEntry]


class DetectPresetRequest(BaseModel):
    run_id: str


class AnalyzeThemesRequest(BaseModel):
    run_id: str
    preset: str = Field(
        default="auto",
        description=(
            "DEPRECATED alias for ``content_type`` — kept during the three-axis migration window."
        ),
    )
    content_type: RequestedContentType | None = Field(
        default=None,
        description="Three-axis Axis 1 — wins over ``preset`` when set.",
    )

    @model_validator(mode="after")
    def _apply_legacy_alias(self) -> AnalyzeThemesRequest:
        return _apply_legacy_preset_alias(self, endpoint="analyze_themes")


class UserSettings(BaseModel):
    target_length_s: int | None = None
    themes: list[str] = []
    scrub_params: ScrubParams | None = None
    # Three-axis Axis 2. ``None`` = auto-resolve by duration / heuristics.
    # Populated by the legacy-preset remapper when the caller sent a
    # cut-intent preset (``tightener`` / ``clip_hunter`` / ``short_generator``).
    # See Phase 4 of Implementation/workflow/three-axis-model.md.
    cut_intent: CutIntent | None = Field(
        default=None,
        description=(
            "Three-axis Axis 2 — cut intent (narrative / peak_highlight / "
            "multi_clip / assembled_short / surgical_tighten). ``None`` "
            "lets the axis resolver auto-pick from duration and num_clips."
        ),
    )
    # v2-0 groundwork: content-category exclusion + free-text focus.
    # The Director prompt wiring lands in v2-1; these fields are accepted
    # and round-tripped through state now so older clients (v1 panel) keep
    # working and newer clients can start sending them.
    exclude_categories: list[str] = Field(
        default_factory=list,
        description="Preset-defined ExcludeCategory.key values the user has ticked.",
    )
    custom_focus: str | None = Field(
        default=None,
        description="Free-text focus hint fed to the Director in v2-1.",
    )
    # v2-10: output format adaptation. Defaults to horizontal so v1 clients
    # and first-time users get their existing behaviour. The execute step
    # consumes this to set the new timeline's resolution and drive caption
    # + crop handling.
    format: Literal["horizontal", "vertical_short", "square"] = Field(
        default="horizontal",
        description="Output format key.",
    )
    captions_enabled: bool = Field(
        default=False,
        description="When true, execute writes an SRT next to the snapshot and populates a subtitle track.",
    )
    safe_zones_enabled: bool = Field(
        default=False,
        description="When true and format is non-horizontal, execute drops platform-UI safe-zone guides on V2.",
    )
    # v2-2 + v2-11: timeline-state ladder. Defaults preserve v1 behaviour.
    timeline_mode: Literal["raw_dump", "rough_cut", "curated", "assembled"] = Field(
        default="raw_dump",
        description=(
            "Decision-delegation ladder describing the editor's handoff state. "
            "'raw_dump' (v1 default) — pile of content; agent picks keepers + "
            "sequences + tightens. 'rough_cut' (v2-11) — candidates with "
            "alternates (A/B selects); agent picks winners per group + "
            "sequences + tightens. 'curated' (v2-11) — final selects, no "
            "duplicates, no order; agent keeps all + sequences + tightens. "
            "'assembled' (v2-2) — cut is locked; agent tightens only."
        ),
    )
    reorder_allowed: bool = Field(
        default=True,
        description=(
            "Assembled mode only. When false, the server-side validator rejects "
            "plans whose take order differs from input order (retry loop re-prompts)."
        ),
    )
    takes_already_scrubbed: bool = Field(
        default=False,
        description=(
            "Assembled mode only. When true, build-plan uses the raw transcript "
            "(no filler / dead-air cleanup) because the editor already polished "
            "each take. Default false — editor picked takes but hasn't scrubbed."
        ),
    )
    # Multi-candidate count — used by Clip Hunter (number of candidate
    # clips returned), Short Generator (number of shorts), and the
    # multi_clip cut intent more generally. Ceiling matches the panel's
    # stepper chip set [1, 2, 3, 5, 8, 10]; cap at 10 so editors can't
    # request runaway cardinalities while still covering every chip.
    num_clips: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Number of candidate clips / shorts to produce. Used by Clip "
            "Hunter and Short Generator; also informs cut-intent resolution "
            "when >1 (multi-clip harvesting). Range 1–10."
        ),
    )
    # v2-5: speaker labels. Map of STT speaker_id → human label
    # ({"S1": "Host", "S2": "Guest"}). Director + Marker prompts read these
    # so the agents can reason about roles directly. Empty / None leaves
    # the raw STT ids in place (v1 behaviour).
    speaker_labels: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional {speaker_id: label} rename map. When set, Director + "
            "Marker prompts show the human labels instead of the raw STT ids."
        ),
    )
    # v3-hook: editor picks one of the surfaced HookCandidates in the
    # Configure screen. When set, the Director must open the cut within
    # tolerance of this source-time (validator rejects otherwise).
    selected_hook_s: float | None = Field(
        default=None,
        description=(
            "Source-time in seconds of the HookCandidate the editor picked. "
            "When set, the Director's first selected_clip must start within "
            "a tolerance of this value or the plan is rejected."
        ),
    )
    # v4 sensory layers. The master toggle drives the Configure-screen
    # UX copy (Phase 4.4); per-layer flags let power users override the
    # mode-aware defaults in ``data/presets.py::SENSORY_MATRIX``.
    # Consumption lands in 4.1 (Director prompt) and 4.2 (boundary
    # validator). Defaulting to False keeps the v3 build-plan path
    # byte-identical when the editor never opens the card.
    sensory_master_enabled: bool = Field(
        default=False,
        description=(
            "v4 'Shot-aware editing' master toggle. When true, the "
            "per-mode activation matrix in data/presets.py resolves "
            "which sensory layers (C / A / Audio) run for this preset."
        ),
    )
    # Per-layer overrides — tri-state so the resolver can distinguish
    # "defer to matrix" (None) from "force on" (True) / "force off"
    # (False). Pydantic v2 preserves ``None`` as the default so older
    # clients that never send these keys land on matrix semantics.
    layer_c_enabled: bool | None = Field(
        default=None,
        description=(
            "v4 Layer C override (shot tagging). None = defer to matrix "
            "+ master toggle; True = force on; False = force off. The "
            "Director prompt renders the shot-tag block only when this "
            "resolves true."
        ),
    )
    layer_a_enabled: bool | None = Field(
        default=None,
        description=(
            "v4 Layer A override (boundary validator). None = defer to "
            "matrix + master toggle; True/False force. Forces the "
            "post-plan retry loop on/off."
        ),
    )
    layer_audio_enabled: bool | None = Field(
        default=None,
        description=(
            "v4 Layer Audio override (DSP cues). None = defer to matrix "
            "+ master toggle; True/False force the ffmpeg audio-cue pass."
        ),
    )
    # Story-critic per-build opt-in. Tri-state so the build helper can
    # tell "user explicitly off" from "user didn't pick" — the env var
    # ``CUTMASTER_ENABLE_STORY_CRITIC`` always wins when truthy. See
    # Implementation/optimizaiton/story-critic.md (Phase 5.6 flips the
    # default-on semantics; until then this stays opt-in).
    story_critic_enabled: bool | None = Field(
        default=None,
        description=(
            "Per-build story-critic toggle. None / False = skip; True = "
            "run the critic if the env var hasn't already forced it on. "
            "The env var CUTMASTER_ENABLE_STORY_CRITIC=1 overrides this "
            "for every build (server-wide kill-switch / forced-on)."
        ),
    )


class BuildPlanRequest(BaseModel):
    run_id: str
    preset: str = Field(
        description=(
            "DEPRECATED alias for ``content_type`` — kept during the "
            "three-axis migration window. When ``content_type`` is unset, "
            "the server remaps and emits ``legacy_preset_alias_used``."
        ),
    )
    content_type: RequestedContentType | None = Field(
        default=None,
        description=(
            "Three-axis Axis 1 — content type. ``None`` falls back to the "
            "legacy ``preset`` remapping."
        ),
    )
    user_settings: UserSettings = Field(default_factory=UserSettings)
    critic_feedback: dict | None = Field(
        default=None,
        description=(
            "Optional story-critic report from a prior build, fed into "
            "the Director's first call as a rework prompt. Editor-driven "
            "regenerate-with-recommendations: the panel sends the previous "
            "build's coherence_report so the Director addresses its issues "
            "instead of starting blind. The auto-rework loop runs on top "
            "as normal. Shape matches the dict produced by "
            "``_critic_feedback_payload`` (score / verdict / summary / "
            "issues / history)."
        ),
    )

    @model_validator(mode="after")
    def _apply_legacy_alias(self) -> BuildPlanRequest:
        return _apply_legacy_preset_alias(self, endpoint="build_plan")


class ExecuteRequest(BaseModel):
    run_id: str
    candidate_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Clip Hunter only: index of the candidate to build. Defaults to "
            "the top-ranked candidate (index 0) when omitted."
        ),
    )
    custom_name: str | None = Field(
        default=None,
        description=(
            "Optional user-supplied name for the new timeline. When set, "
            "replaces the auto '<source>_AI_Cut' naming. Still uniqueified "
            "to avoid clobbering an existing timeline."
        ),
    )
    replace_existing: bool = Field(
        default=False,
        description=(
            "When true, after the new timeline builds successfully, delete "
            "any pre-existing timeline in the project with the same base "
            "name. Safer than overwrite: the new timeline is verified first, "
            "and the .drp snapshot is preserved either way."
        ),
    )


class DeleteCutRequest(BaseModel):
    run_id: str


class DeleteAllCutsRequest(BaseModel):
    run_id: str


class PaintShotColorsRequest(BaseModel):
    """Reuses cached shot tags; never makes new vision calls."""

    timeline_name: str
    overwrite: bool = False
    video_track: int = 1


class DeleteRunRequest(BaseModel):
    run_id: str


class CloneRunRequest(BaseModel):
    run_id: str


class RunSummary(BaseModel):
    """Compact metadata for one run, suitable for list views.

    ``execute_history`` is intentionally included verbatim (not a count)
    so the panel can render per-cut badges without a second round-trip.
    It's already small — one dict per build.
    """

    run_id: str
    created_at: str | None = None
    timeline_name: str
    preset: str
    content_type: RequestedContentType | None = Field(
        default=None,
        description=(
            "Three-axis Axis 1. Populated from the legacy ``preset`` alias "
            "at load time so the panel's run list can render the new "
            "content-type label without a second round-trip."
        ),
    )
    status: str
    has_transcript: bool
    has_plan: bool
    execute_history: list[dict] = Field(default_factory=list)
    size_kb: float
    last_modified: float

    @model_validator(mode="after")
    def _apply_legacy_alias(self) -> RunSummary:
        return _apply_legacy_preset_alias(self, endpoint="run_summary")


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int
    truncated: bool
