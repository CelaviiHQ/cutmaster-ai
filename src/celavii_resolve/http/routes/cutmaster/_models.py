"""Pydantic request/response models shared across cutmaster route modules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ....cutmaster.analysis.scrubber import ScrubParams


class AnalyzeRequest(BaseModel):
    timeline_name: str
    preset: str = "auto"
    scrub_params: ScrubParams | None = Field(default=None)
    per_clip_stt: bool = Field(
        default=False,
        description=(
            "v2-6: when true, extract one WAV per timeline audio item and "
            "transcribe each clip separately. Words get clip_index + "
            "clip_metadata for the Director prompt; per-clip results cache "
            "under ~/.celavii/cutmaster/per-clip-stt so re-analyze on a "
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
            "Falls back to CELAVII_STT_PROVIDER env var, then to 'gemini'."
        ),
    )


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
    preset: str


class UserSettings(BaseModel):
    target_length_s: int | None = None
    themes: list[str] = []
    scrub_params: ScrubParams | None = None
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
    # v2-4: Clip Hunter — number of candidate clips to surface. target_length_s
    # is reused as the per-clip target duration when preset=clip_hunter.
    num_clips: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Clip Hunter only. How many candidate clips to return (1–5).",
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


class BuildPlanRequest(BaseModel):
    run_id: str
    preset: str
    user_settings: UserSettings = Field(default_factory=UserSettings)


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


class DeleteCutRequest(BaseModel):
    run_id: str
