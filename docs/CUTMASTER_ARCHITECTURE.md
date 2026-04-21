# CutMaster AI — Architecture

This is the map. Read this once before diving into `src/cutmaster_ai/cutmaster/` and you'll save yourself an afternoon tracing imports.

For **setup** (API keys, running the panel, Claude Code plugin), see [`SETUP.md`](./SETUP.md). For the **chronological how-we-got-here**, see [`Implementation/cutmaster_ai/v2/proposal.md`](../Implementation/cutmaster_ai/v2/proposal.md).

---

## 1. What CutMaster does

A DaVinci Resolve-side agent that takes a raw (or assembled) timeline and produces a narrative cut on a new timeline, with B-roll markers, optional captions, and optional short-form reformat. Two consumers hit the same Python core:

| Entry point | Transport | Consumer |
|---|---|---|
| `cutmaster-ai` | MCP stdio | Claude Code / Desktop |
| `cutmaster-ai-panel` | HTTP `127.0.0.1:8765` | React panel bundled inside Resolve's Workflow Integration (or a browser tab) |

Every CutMaster function is a plain Python function the HTTP panel calls directly; MCP gets thin `@mcp.tool` adapters.

---

## 2. Data flow

```
                     ┌─────────────────────────────────┐
                     │       DaVinci Resolve           │
                     │   (Studio, scripting on)        │
                     └───────────────┬─────────────────┘
                                     │  media pool items,
                                     │  timeline object
                                     ▼
    ┌──────────────────────────────────────────────────────────┐
    │ pipeline.run_analyze                                     │
    │                                                          │
    │  vfr.py          → rejects VFR sources (Phase 0)         │
    │                                                          │
    │  ffmpeg_audio.py OR per_clip_stt.py                      │
    │      ↓                                                   │
    │  stt.py → stt_gemini.py / stt_deepgram.py  (dispatched)  │
    │      ↓                                                   │
    │  speaker_reconcile.py                                    │
    │      · collapse_to_solo  (expected=1)                    │
    │      · reconcile_with_llm  (expected≥2 + per-clip)       │
    │      ↓                                                   │
    │  scrubber.py → filler / dead-air / restart removal       │
    └───────────────┬──────────────────────────────────────────┘
                    │  run["transcript"], run["scrubbed"]
                    │  (persisted to ~/.cutmaster/cutmaster/<id>.json)
                    ▼
    ┌──────────────────────────────────────────────────────────┐
    │  Configure screen (UI)                                   │
    │    themes.py      → story chapters + hook candidates     │
    │    auto_detect.py → recommend a preset                   │
    │  User picks preset, themes, excludes, format, speakers…  │
    └───────────────┬──────────────────────────────────────────┘
                    │  UserSettings
                    ▼
    ┌──────────────────────────────────────────────────────────┐
    │ http/routes/cutmaster/build.py::build_plan               │
    │                                                          │
    │  branch on preset/mode:                                  │
    │    · preset=clip_hunter   → Clip Hunter Director         │
    │    · preset=tightener     → no Director, deterministic   │
    │    · timeline_mode=assembled → assembled Director        │
    │    · else                 → raw-dump Director (v1)       │
    │                                                          │
    │  director.py  (3 prompt variants, 1 retry loop)          │
    │      ↓                                                   │
    │  marker_agent.py  (skipped for tightener / clip hunter)  │
    │      ↓                                                   │
    │  resolve_segments.py  → per-item source-frame pieces     │
    │                         (auto-splits cross-boundary)     │
    │                         (fps-aware src-in/out)           │
    └───────────────┬──────────────────────────────────────────┘
                    │  run["plan"]
                    ▼
    ┌──────────────────────────────────────────────────────────┐
    │ execute.py                                               │
    │                                                          │
    │  snapshot.py      → .drp of the project (rollback)       │
    │  captions.py      → SRT + subtitle track (optional)      │
    │  formats.py       → new-timeline resolution              │
    │  time_mapping.py  → marker positions on new timeline     │
    │                                                          │
    │  → creates <source>_AI_Cut  (or _AI_Clip_N)              │
    └──────────────────────────────────────────────────────────┘
```

**Key invariant**: every agent call routes through `llm.call_structured` with a Pydantic `response_schema` and an optional validator that feeds errors back on retry. The validator is what killed the verbatim-timestamp regressions in v1.

---

## 3. Run state & events

Every `POST /cutmaster/analyze` creates a **run** identified by a 12-char hex ID. Everything else is keyed by that run.

**On disk** (`~/.cutmaster/cutmaster/<run_id>.json`):

```python
{
    "run_id": "578b231e5efa",
    "timeline_name": "Timeline 1",
    "preset": "vlog",
    "created_at": "2026-04-17T20:56:36",
    "status": "done",              # pending | running | done | failed
    "stages": {...},               # latest event per stage
    "events": [...],               # full event log (replayed to late SSE subscribers)
    "transcript": [...],           # raw STT words
    "scrubbed": [...],             # post-scrub words
    "speaker_reconciliation": {...},  # v2-6 follow-up, optional
    "plan": {
        "preset": ...,
        "user_settings": {...},
        "director": {...},         # DirectorPlan.model_dump()
        "markers": {...},          # MarkerPlan.model_dump()
        "resolved_segments": [...],
        # mode-specific:
        "tightener": {...},        # when preset=tightener
        "clip_hunter": {...},      # when preset=clip_hunter
    },
    "execute": {...},              # what Resolve actually did
    "error": null
}
```

**Live events** flow through two channels:

1. **In-memory** `asyncio.Queue` per run → `GET /cutmaster/events/{run_id}` over SSE. Events arrive as `{stage, status, message, data, ts}` with `status ∈ {started, complete, failed, progress}`. The stream closes on `done` or `error`.
2. **Persistent** — every event also appends to `run["events"]` so late subscribers replay history before live events resume. This is what makes the browser-reload resume work.

Debug artefacts next to the run state:

- `<run_id>.director_prompt.txt` — the exact string sent to the Director for the last build (all three variants).
- `<run_id>/clip_*.wav` — per-clip WAVs when per-clip STT is on.
- `per-clip-stt/<sha1>.json` — cached per-clip transcripts keyed by source-path + frame range.

---

## 4. Extension points

### 4.1 Add an STT provider

```python
# src/cutmaster_ai/cutmaster/stt/whisper.py
from .stt import TranscriptResponse, TranscriptWord


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def transcribe(audio_path: Path, model: str | None = None) -> TranscriptResponse:
    # ... call OpenAI /v1/audio/transcriptions with timestamp_granularities=["word"]
    words = [TranscriptWord(word=w["word"], start_time=w["start"],
                            end_time=w["end"], speaker_id="S1")
             for w in api_response["words"]]
    return TranscriptResponse(words=words)
```

Then register in `stt.py::transcribe_audio`:

```python
if chosen == "whisper":
    from .stt_whisper import transcribe as _whisper_transcribe
    return _whisper_transcribe(audio_path, model)
```

…and in `available_providers()`. The UI's Transcription service card picks it up automatically.

### 4.2 Add a content-type preset

Add a `PresetBundle` in `cutmaster/data/presets.py`:

```python
TRAVEL_VLOG = PresetBundle(
    key="travel_vlog",
    label="Travel Vlog",
    role="travel documentary editor",
    hook_rule="the first landscape or arrival shot",
    pacing="breathe on wide shots, cut tight on action",
    cue_vocabulary=["arrived at", "this is", "look at that", "check out"],
    marker_vocabulary=["B-Roll: {location}", "Cutaway: {subject}"],
    theme_axes=["locations", "food", "people", "moments"],
    scrub_defaults={"remove_fillers": True, "remove_dead_air": True,
                    "collapse_restarts": True, "dead_air_threshold_s": 0.8},
    exclude_categories=[
        ExcludeCategory(key="logistics", label="Logistics talk",
                        description="Train times, hotel check-ins, route planning.",
                        checked_by_default=True),
        # ... 3-5 more, see existing presets for patterns
    ],
    default_custom_focus_placeholder="e.g. emphasise the street food scenes",
    # Optional: speaker_awareness fragment if the preset is interview-like.
)
```

Add to `PRESETS` dict + the `Preset` Literal. Invariant tests in `tests/cutmaster/test_presets.py` enforce the shape — add the key to `CONTENT_TYPE_PRESETS` there.

Run `pytest tests/cutmaster/test_presets.py -v` and the Preset picker should show your new card.

### 4.3 Add a Director variant

Pattern used by all three existing variants (raw-dump, assembled, clip-hunter):

1. Define the schema in `cutmaster/core/director.py`:
   ```python
   class MyDirectorPlan(BaseModel):
       ...  # what the model must return
   ```
2. Write the prompt renderer:
   ```python
   def _my_prompt(preset, transcript, user_settings) -> str:
       exclude = _exclude_block(preset, user_settings)
       focus = _focus_block(user_settings)
       speakers = _speaker_block(preset, transcript, user_settings)
       ...
   ```
3. Write the validator — returns `list[str]` of errors that get fed back on retry:
   ```python
   def validate_my_plan(plan, transcript, ...) -> list[str]:
       errors = []
       # verbatim timestamp, ranges, ordering, etc.
       return errors
   ```
4. Write the agent entry:
   ```python
   def build_my_plan(transcript, preset, user_settings) -> MyDirectorPlan:
       prompt = _my_prompt(preset, transcript, user_settings)
       return llm.call_structured(
           agent="director", prompt=prompt, response_schema=MyDirectorPlan,
           validate=lambda p: validate_my_plan(p, transcript),
           temperature=0.4,
       )
   ```
5. Branch in `http/routes/cutmaster/build.py::build_plan` — the earlier the branch the higher the priority. Tightener and Clip Hunter branch before assembled/raw-dump because they short-circuit later stages.

### 4.4 Add an output Format

Add a `FormatSpec` in `cutmaster/media/formats.py` (width, height, max duration, safe zones, reframe default). No wiring changes needed — Configure screen auto-lists it and Execute reads it via the same path as the existing three.

---

## 5. Invariants worth knowing

Paid-for lessons. Every bullet here is defensive code that exists because something bit us.

- **Timeline markers are relative, not absolute.** `Timeline.AddMarker(frameId, ...)` treats `frameId` as a 0-based offset from timeline start. Going through the absolute frame (`86400 + offset`) parks markers an hour past the end. Enforced in `execute.py`.

- **LLM STT extrapolates past audio end.** Gemini sometimes produces word timestamps well beyond the actual WAV duration. Every STT path clamps to `audio_duration + 0.25s` post-hoc. Provider-agnostic.

- **Source fps vs timeline fps matter.** A 30 fps source on a 24 fps timeline produces a mismatch between timeline-frames and source-media-frames. `frame_math._source_fps` reads the media's FPS property; `resolve_segments` scales by `source_fps / tl_fps` before passing to `AppendToTimeline`. Miss this and every piece lands at 80 % of intended duration.

- **Verbatim timestamps are sacred.** The Director's `start_s` / `end_s` must match word-start / word-end times exactly — no rounding, no paraphrasing. `director._build_timestamp_sets` + `validate_plan` catch rounding in the retry loop. Applies to raw, assembled, and clip-hunter variants.

- **Auto-split is the common case.** On raw-dump timelines, 10–20 % of Director-picked segments cross a source-clip boundary. `resolve_segments` splits them into multiple `ResolvedCutSegment`s anchored at the right source frames. Speed-ramped clips surface a warning — they currently land frame-accurate but human-verified only.

- **Per-clip STT speakers are clip-local.** Gemini transcribing each clip independently assigns `S1` / `S2` fresh per clip — `speaker_reconcile.py` exists to stitch them into a global roster when the user sets `expected_speakers`.

- **Structured outputs are mandatory.** Every LLM call goes through `llm.call_structured` with a Pydantic `response_schema`. Without it Gemini returns bare arrays, string-typed numbers, and missing fields. Never call `client.models.generate_content` directly.

- **Panel state isn't persistent by default.** `persist.ts` + `/cutmaster/state/<id>` resume a browser reload. The backend state file is the source of truth; localStorage just remembers the run ID.

---

## 6. Module map

One line per module. When you need to read one, know what it owns.

```
cutmaster/
├── __init__.py                    package marker
│
├── core/                          pipeline + agents + state + execute
│   ├── pipeline.py                analyze orchestrator (vfr → audio → STT → speakers → scrub)
│   ├── state.py                   run dict persistence, SSE queue, emit helper
│   ├── director.py                raw / assembled / clip-hunter schemas + prompts + validators
│   ├── execute.py                 build new timeline, drop markers, write SRT
│   └── snapshot.py                .drp project snapshot before mutation
│
├── stt/                           speech-to-text + speaker handling
│   ├── base.py                    provider dispatch + shared TranscriptResponse schema
│   ├── gemini.py                  Gemini backend (≤ 8 min audio validated)
│   ├── deepgram.py                Deepgram Nova-3 backend (long-form + diarization)
│   ├── per_clip.py                v2-6 per-item extraction + cache + parallel STT
│   ├── speakers.py                detect / stats / apply_labels (pure helpers)
│   └── reconcile.py               cross-clip solo collapse + LLM reconciler
│
├── analysis/                      LLM agents + heuristic analysers
│   ├── scrubber.py                filler / dead-air / restart removal
│   ├── auto_detect.py             picks a preset from the scrubbed transcript
│   ├── themes.py                  chapters + hook candidates + theme axes
│   ├── marker_agent.py            B-roll / cutaway suggestions over selected words
│   ├── tightener.py               no-LLM per-take word-block segmenter
│   └── captions.py                SRT + subtitle-track generation
│
├── media/                         media IO + time math
│   ├── vfr.py                     ffprobe-based VFR detection
│   ├── ffmpeg_audio.py            concat extraction (v1 default)
│   ├── frame_math.py              timeline ↔ frame ↔ source-fps conversions
│   ├── time_mapping.py            source → new-timeline position (captions + markers)
│   └── formats.py                 horizontal / vertical_short / square specs
│
├── resolve_ops/                   DaVinci Resolve API operations
│   ├── segments.py                CutSegment[] → per-item source-frame pieces (fps-aware)
│   ├── source_mapper.py           timeline seconds → source frames (with speed)
│   ├── subclips.py                subclip creation helpers
│   └── assembled.py               timeline-item → take-entry helpers
│
└── data/                          static bundles + registries
    ├── presets.py                 9 preset bundles + Preset Literal
    └── excludes.py                ExcludeCategory schema (preset-declared UI filters)
```

LLM dispatch lives one level up at `intelligence/llm.py` — shared by CutMaster
and all single-shot `intelligence/` tools (vision, color_assist, critique).

```
http/routes/cutmaster/      feature-split FastAPI package
├── __init__.py             aggregates sub-routers under /cutmaster prefix
├── _models.py              Pydantic request/response models
├── _helpers.py             _require_scrubbed, _dump_director_prompt
├── analyze.py              POST /analyze, /events/{id}, /state/{id}
├── presets.py              /presets, /formats, /stt-providers, detect, analyze-themes
├── info.py                 /source-aspect, /project-info, /speakers, /director-prompt
├── build.py                POST /build-plan — the 4-branch Director+Marker+resolver
└── execute.py              POST /execute, POST /delete-cut
```

---

## 7. Reading order for a new contributor

Thirty minutes to build a mental model:

1. This file.
2. [`cutmaster/core/pipeline.py`](../src/cutmaster_ai/cutmaster/core/pipeline.py) — top-down, `run_analyze` orchestrator.
3. [`cutmaster/core/director.py`](../src/cutmaster_ai/cutmaster/core/director.py) — three prompt variants; read the docstrings before the prompt bodies.
4. [`cutmaster/core/execute.py`](../src/cutmaster_ai/cutmaster/core/execute.py) — how a plan becomes a real Resolve timeline.
5. [`http/routes/cutmaster/build.py`](../src/cutmaster_ai/http/routes/cutmaster/build.py) — the best place to see all preset branches at once.
6. The panel: [`apps/panel/src/App.tsx`](../apps/panel/src/App.tsx) → any screen you touched.

Then pick one item from §4's extension points and add it. Shipping a new preset is the fastest way to understand the whole data flow.
