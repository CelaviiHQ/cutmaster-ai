# CutMaster AI v4 — Sensory-aware Director

v1 shipped the pipeline. v2 shipped modes / presets / formats / speakers. v3 shipped the UI revamp and API hardening. **v4 gives the Director senses.**

Today every Director variant — Raw-dump, Assembled, Curated, Rough-cut, Clip Hunter, Short Generator — reasons from transcribed words alone. That's the biggest remaining blind spot. The workflow already does an excellent job at cutting, planning, and composing from transcript; adding **pre-classified shot metadata + post-plan boundary validation + deterministic audio cues** is a major step up on the one thing it genuinely can't see.

v4 is three independent sensory layers — **Layer C** (shot tagging), **Layer A** (boundary validator), **Layer Audio** (DSP cues) — with per-mode activation so each layer runs where it pays off and stays out of the way where it doesn't.

**Target:** ~9 days for a single dev for the full three-layer shape.
**Compatibility:** v3 and earlier stay fully functional. The feature is opt-in; default off.
**Model:** **`gemini-3.1-flash-lite-preview`** for vision layers (matches the default agent model in [intelligence/llm.py](../../../src/celavii_resolve/intelligence/llm.py)). If vision quality proves insufficient on lite, upgrade to full `gemini-3.1-flash` per-agent via `CELAVII_SHOT_TAGGER_MODEL` / `CELAVII_BOUNDARY_VALIDATOR_MODEL` env overrides. DSP for audio (no LLM).

---

## Design principles

1. **Durable state beats per-build recompute.** Tags, boundary judgements, and audio cues are cached by source-file hash. Plan regenerations, clone-runs, and preset swaps reuse them for free.
2. **Each layer opts in independently.** C, A, and Audio are separable. A user or mode can enable any combination.
3. **Mode-aware defaults.** One user-facing toggle — "AI sensory analysis" — activates the right layer mix for the current mode. Advanced panel exposes per-layer overrides.
4. **No cliff when a dependency is missing.** No Gemini key → Layer C and A skip with a clear event; pipeline completes. No ffmpeg filter → Audio layer skips. Zero regressions.
5. **Feed text, not pixels, to the Director's main prompt.** Tags and audio cues render as lightweight tables. Only the boundary validator sees frames, and only at candidate cut points.
6. **Sensitive-content guardrails.** Prompts explicitly forbid OCR of on-screen text, identification of individuals, or description of private information. `notable` fields are bounded.
7. **One LLM chokepoint.** All new vision calls route through [`intelligence/llm.py`](../../../src/celavii_resolve/intelligence/llm.py) — either by extending `call_structured` with an optional `images=` parameter or by adding a sibling `call_structured_multimodal`. No per-analyzer `generate_content` calls. Dispatch lives in one place.
8. **Logger namespace preserved.** New modules use the dotted hierarchy: `celavii-resolve.cutmaster.shot_tagger`, `celavii-resolve.cutmaster.boundary_validator`, `celavii-resolve.cutmaster.audio_cues`, `celavii-resolve.cutmaster.ffmpeg_frames`, `celavii-resolve.cutmaster.validator_loop` — matching every existing cutmaster module.

---

## End-to-end flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  ANALYZE  (one-time per source; cached thereafter)                  │
│                                                                     │
│     ┌───────┐     ┌───────────┐     ┌─────┐     ┌──────────┐        │
│     │  VFR  │ ──> │  Extract  │ ──> │ STT │ ──> │   Scrub  │        │
│     │ check │     │   audio   │     │     │     │ fillers  │        │
│     └───────┘     └───────────┘     └─────┘     └────┬─────┘        │
│                                                      │              │
│               ┌──────────────────────────────────────┴───┐          │
│               ▼                                          ▼          │
│   ╔══════════════════╗                    ╔══════════════════════╗  │
│   ║  LAYER C         ║                    ║  LAYER AUDIO         ║  │
│   ║  Shot tagging    ║                    ║  Cue extraction      ║  │
│   ║  (per item,      ║                    ║  (DSP: pauses,       ║  │
│   ║   Gemini 3.1)    ║                    ║   RMS, onsets)       ║  │
│   ║                  ║                    ║                      ║  │
│   ║  cache: source-  ║                    ║  cache: source-      ║  │
│   ║   file keyed     ║                    ║   file keyed         ║  │
│   ╚════════┬═════════╝                    ╚═══════════┬══════════╝  │
│            │                                          │             │
│            └─── transcript words gain ───────────────┘              │
│                 .shot_tag + .audio_cue                              │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CONFIGURE  (user picks preset/format/focus; settings saved)        │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  BUILD-PLAN                                                         │
│                                                                     │
│     ┌────────────────────────────────────────────────┐              │
│     │  DIRECTOR  (text + shot tags + audio cues)     │              │
│     │  picks cut boundaries, reorders, tightens      │              │
│     └──────────────────────┬─────────────────────────┘              │
│                            │ candidate plan                         │
│                            ▼                                        │
│     ╔════════════════════════════════════════════════╗              │
│     ║  LAYER A                                       ║              │
│     ║  Boundary validator                            ║              │
│     ║  (fetch first/last frames at each proposed cut,║              │
│     ║   single batched Gemini call → jarring array)  ║              │
│     ╚══════╦═════════════════════════════════════════╝              │
│            │                                                        │
│     ┌──────┴────────┐                                               │
│     │ all smooth?   │──── NO ──► feed rejections back into ──┐      │
│     └──────┬────────┘              Director retry loop        │     │
│            │                       ▲                          │     │
│           YES                      └──────────────────────────┘     │
│            │                                                        │
│            ▼                                                        │
│     ┌────────────────────────────┐                                  │
│     │  Marker agent              │                                  │
│     │  Source-frame resolver     │                                  │
│     └──────────────┬─────────────┘                                  │
└────────────────────┼────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  REVIEW  (editor sees plan + tags + flagged cuts; approves or tweaks│
│           hook / themes / length → regenerate-plan jumps to Director)│
└────────────────────┬────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  EXECUTE  → Resolve timeline + snapshot + captions + safe zones     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Per-mode activation matrix

Each mode gets a different sensory mix because each mode makes different kinds of cuts:

| Mode | What's a cut? | Layer C | Layer A | Layer Audio |
|---|---|---|---|---|
| **Raw dump** | Word-level, anywhere in the source | ✅ global shot-variety + pacing | ✅ every cut is a new juxtaposition | ⚠️ natural-pause hints (opt-in) |
| **Rough cut** | Pick-winner between A/B takes + sequence | ✅ `visual_energy` / `gesture` chooses the stronger take | ✅ between takes | ⚠️ within-take tightening (opt-in) |
| **Curated** | Between takes + tighten within | ✅ shot-variety across takes | ✅ between takes | ⚠️ within-take tightening (opt-in) |
| **Assembled** | Within-take only (filler / breath / pauses) | ✅ gesture awareness so we don't cut mid-gesture | ❌ same take, same shot — continuity given | ✅ **THE signal** — breath/pause/filler detection |
| **Clip Hunter** | In/out of each extracted span | ✅ `visual_energy` feeds engagement scoring | ✅ clip in/out points | ⚠️ pause/silence cues (opt-in; laughter/emphasis deferred to v4.1) |
| **Short Generator** | Every span boundary in a multi-span reel | ✅ visual energy per span | ✅ every span transition | ✅ beat-aware hook timing |

Legend: ✅ = active by default when the master toggle is on · ⚠️ = opt-in under Advanced · ❌ = never activated (waste of cost).

**Design call:** one user-facing toggle ("AI sensory analysis") with mode-aware auto-activation. An Advanced expand exposes per-layer overrides for power users and debugging.

---

## Layer C — Shot tagging (pre-classification)

Runs **once per source file** during analyze, cached thereafter. Samples frames from each timeline item via ffmpeg and asks Gemini 3.1 Flash for structured shot metadata. Tags attach to transcript words by timestamp.

### Tag schema

```jsonc
{
  "shot_type":         "closeup | medium | wide | over_shoulder | broll | title_card | unknown",
  "framing":           "speaker_centered | speaker_side | no_speaker | unknown",
  "gesture_intensity": "still | calm | emphatic | unknown",
  "visual_energy":     0,         // int 0..10 — scene energy, drives Clip Hunter ranking
  "notable":           null       // optional ≤80-char prose, bounded; "speaker leans in"
}
```

### Sampling cadence

Per timeline item:
- 1 frame at `item.start_s + 0.3s` (past the edit in-point).
- 1 frame every **5s** within the item.
- 1 frame at `item.end_s - 0.3s`.

One **batched** Gemini call per item (not per frame). The model sees temporal sequence and returns a parallel `ShotTag` array.

### Cache

```
~/.celavii/cutmaster/shot-tags/v1/
  <sha1(source_path)>/
    manifest.json       # { source_path, duration_s, last_tagged_at }
    <timestamp_ms>.json # one file per sampled frame → ShotTag
```

Source-keyed (not run-keyed) — tags survive reruns, clone-runs, and cross-project reuse of the same media.

### Director consumption

A new `_shot_tag_block(transcript)` helper renders a coalesced table below the existing CLIP METADATA block:

```
SHOT TAGS (per word range, derived from Gemini vision pass):

  words 0-47    item=0  shot=closeup   gest=emphatic  energy=8  "speaker leans in"
  words 48-112  item=0  shot=medium    gest=calm      energy=4
  words 113-145 item=1  shot=broll     framing=no_speaker       "product close-up"
  words 146-189 item=1  shot=closeup   gest=emphatic  energy=9
  ...

Prefer: not cutting mid-emphatic-gesture · alternating shot types ·
opening on higher visual_energy for hook impact. These tags are advisory —
narrative / pacing / transcript semantics still win.
```

Injected into all six prompt builders in `cutmaster/core/director.py`.

---

## Layer A — Boundary validator (post-plan)

Runs **after the Director produces a candidate plan**. For each proposed cut, pulls the literal last frame of the outgoing segment and first frame of the incoming segment. One batched Gemini call reviews the whole plan and returns a parallel array of verdicts.

### Verdict schema

```jsonc
{
  "cut_index": 7,
  "verdict":   "smooth | jarring | borderline",
  "reason":    "closeup→wide mid-gesture — hand visibly mid-swing",
  "suggestion":"shift 0.4s earlier to land on gesture completion"
}
```

### Retry-loop integration

A **new outer retry loop** (`core/validator_loop.py`) wraps each Director variant's entry point (`build_cut_plan`, `build_assembled_cut_plan`, etc.). This is distinct from the inner retry inside [`intelligence/llm.py:call_structured`](../../../src/celavii_resolve/intelligence/llm.py) — that one handles malformed JSON / response-schema violations and stays unchanged.

- `smooth` / `borderline` → plan accepted, loop exits.
- `jarring` → loop re-invokes the Director variant with structured rejections appended to the prompt: "cut 7 at t=47.2s was rejected: closeup→wide mid-gesture. Pick a different word boundary."
- After 2 retry cycles, remaining `jarring` cuts are surfaced in the Review screen as warnings rather than blocking execute — the editor has final say.

### Frame fetch

Batched ffmpeg: `-ss <t> -vframes 1` per (source_path, timestamp) tuple. Same cache path pattern as Layer C but under `boundary-frames/v1/`. Cache hit rate is lower than C (different retries propose different boundaries) but frames for identical timestamps still hit.

### Skipped in Assembled mode

Within-take cuts don't change the shot. Running A here would burn tokens to confirm frames are visually identical. Explicitly off by default in Assembled.

---

## Layer Audio — DSP cues (deterministic)

Runs **during analyze, parallel to Layer C**. No LLM — deterministic DSP on the already-extracted WAV. Attaches per-word cues.

### Cue schema (v4.0 — deterministic, dependency-free)

```jsonc
{
  "pause_before_ms":  420,     // silence duration immediately preceding this word
  "pause_after_ms":   0,
  "rms_db_delta":     -3.2,    // change from prior word — captures trailing-off energy
  "is_silence_tail":  true     // RMS below -40dB for ≥400ms after the word
}
```

### Derivation

- **Pauses** — diff between word timestamps (already in STT output); no new work.
- **RMS envelope** — single ffmpeg `astats` / `silencedetect` pass per WAV, segmented to per-word windows.
- **`is_silence_tail`** — derived from the same ffmpeg pass; marks natural cut points.

### Explicitly out of 4.0

- **Laughter / breath detection.** These need spectral analysis (FFT → flatness or MFCC-style features), which means pulling **numpy** into [pyproject.toml] — a new dependency the repo currently doesn't carry. Deferred to v4.1 once quality of the other cues is validated and the dep is justified. If real users ask for laughter detection specifically (Clip Hunter engagement signal), fast-track it.

The v4.0 audio layer is therefore **pure ffmpeg + arithmetic** — no new Python dependencies.

### Director consumption (Assembled + Short Generator primarily)

Rendered as a compact annotation block:

```
AUDIO CUES (per word, derived from signal):

  word 47 "okay"  pause_after=620ms  is_silence_tail=true  (natural endpoint — cut candidate)
  word 62 "so"    pause_before=840ms rms_delta=-4.1        (trailing + reset — cut candidate)
  word 103 "wait" pause_before=1250ms                      (hard reset — cut candidate)
  ...

Prefer: cutting on natural endpoints (pause_after > 400ms or
is_silence_tail=true) · in Assembled mode, tighten any
pause_after / pause_before > 800ms except on narrative beats.
```

### Non-goals for v4.0

- Music beat detection (onset tracking for music-video-style cuts).
- Prosody / sentiment analysis from audio.
- Speaker-emotion classification.

Kept out intentionally so Audio stays deterministic, dependency-light, and fast.

---

## UX — single toggle, mode-aware defaults

**Configure screen** gains one section:

```
┌─ Shot-aware editing ──────────────────────────────────────────┐
│                                                               │
│  [✓] Enable                                                   │
│                                                               │
│  For this preset (Raw dump): shot tagging + cut validation.   │
│  Adds ~30-60s on first analyze of new footage. Cached after.  │
│                                                               │
│  ▸ Advanced (3 layers, per-layer overrides)  [collapsed]      │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

The subtitle line updates dynamically based on the active mode — copy derived from the activation matrix above.

Expanded Advanced:

```
  [✓] Layer C — Shot tagging          (auto-active in this mode)
  [✓] Layer A — Boundary validation   (auto-active in this mode)
  [ ] Layer Audio — DSP cues          (opt-in in this mode)
```

Power users can override defaults; the master switch still gates the whole feature.

---

## Module layout

```
src/celavii_resolve/cutmaster/
├── analysis/
│   ├── shot_tagger.py               (NEW — Layer C orchestrator)
│   ├── boundary_validator.py        (NEW — Layer A orchestrator)
│   └── audio_cues.py                (NEW — Layer Audio DSP pass)
├── media/
│   └── ffmpeg_frames.py             (NEW — batched frame extraction, cached)
├── core/
│   ├── pipeline.py                  (modify — insert shot_tag + audio_cues stages)
│   ├── director.py                  (modify — _shot_tag_block + _audio_cue_block
│   │                                             injected into all prompt builders)
│   └── validator_loop.py            (NEW — boundary-validator retry wiring)
└── data/
    └── presets.py                   (modify — default layer-activation per preset-mode)
src/celavii_resolve/http/routes/cutmaster/
├── _models.py                       (modify — UserSettings.sensory_* flags)
└── build.py                         (modify — run boundary validator after Director,
                                      route rejections through retry loop)
```

---

## Phase breakdown — ~9 days

### Phase 4.0 — Layer C core (2 days) ✅

Durable infrastructure: frame extraction + cache + tagger + schema + multimodal LLM hook.

- ✅ **4.0.1** Extend [`intelligence/llm.py`](../../../src/celavii_resolve/intelligence/llm.py) with an `images=` parameter on `call_structured` (or add `call_structured_multimodal`). Route every new vision call through it — no per-analyzer `generate_content`. Defaults route to `gemini-3.1-flash-lite-preview`; `CELAVII_<AGENT>_MODEL` env overrides apply.
- ✅ **4.0.2** `media/ffmpeg_frames.py` — batched extraction at timestamps, source-file-keyed cache.
- ✅ **4.0.3** `analysis/shot_tagger.py` — orchestrator: sample plan → cache lookup → multimodal call per item → attach tags to transcript words.
- ✅ **4.0.4** Pipeline stage `shot_tag` post-scrub, gated on `user_settings.layer_c_enabled`. SSE progress events per item.
- ✅ **4.0.5** `UserSettings.layer_c_enabled` + `UserSettings.sensory_master_enabled` fields.

Landed in commit `ea46b41`.

### Phase 4.1 — Director consumption of tags (1 day) ✅

- ✅ **4.1.1** `_shot_tag_block(transcript)` renderer (coalesced ranges).
- ✅ **4.1.2** Inject into all six prompt builders.
- ✅ **4.1.3** Prompt-level guidance block ("prefer…").

Landed in commit `84d7139`.

### Phase 4.2 — Layer A boundary validator (2 days) ✅

Lands the "propose → review → re-plan" half of the flow.

- ✅ **4.2.1** `analysis/boundary_validator.py` — pull first/last frame per candidate cut, batch Gemini call with verdict schema. **Extended with per-candidate addressing (`candidate_index`) so Short Generator's N-candidate batches validate in one call.**
- ✅ **4.2.2** `core/validator_loop.py` — wire rejections back through the existing Director retry machinery. Cap at 2 retries before falling through to warnings. **Extended with `extract_candidate_roster` hook so retries carry theme/engagement order and prevent reshuffling between attempts.**
- ✅ **4.2.3** `build.py` — call boundary validator after every applicable Director variant. Wired for raw_dump, curated, rough_cut (linear plans) AND short_generator (multi-candidate plans with full per-candidate validation + theme roster). Assembled permanently skipped per matrix (within-take cuts keep same shot). Clip Hunter skipped — single-span candidates have no internal transitions.
- ✅ **4.2.4** Review-screen data surface exposed via `plan.boundary_validation.{verdicts,warnings,retries_used,skipped}`. Backend-only for this phase; panel UI pill lands alongside Phase 4.4's Configure card.

Lands in the next commit alongside this proposal update. Covers all applicable modes in one pass (the initial plan scoped short_generator as a followup but the user required full-pipeline coverage before moving to 4.3 — candidate-aware wiring landed same-session).

### Phase 4.3 — Layer Audio DSP cues (1.5 days) ✅

- ✅ **4.3.1** `analysis/audio_cues.py` — ffmpeg `silencedetect` pass + `astats` RMS envelope + pure-arithmetic pause derivation. Laughter/breath heuristics deferred to v4.1 per the dependency-free constraint (numpy would be needed). Source-file-keyed cache under `audio-cues/v1/<sha1(path+size+mtime)>/cues.json` — invalidates automatically when the concat WAV is rebuilt on re-analyze.
- ✅ **4.3.2** Pipeline stage `audio_cues` post-scrub, post-shot_tag. Gated on `AnalyzeRequest.layer_audio_enabled`. Falls back cleanly when ffmpeg fails (pause-only cues from STT timestamps). For per-clip STT runs (which lack a concat WAV), extracts one on demand via `ffmpeg_audio.extract_timeline_audio`.
- ✅ **4.3.3** `_audio_cue_block(transcript, mode)` renderer — shows only SIGNIFICANT cues (pause ≥ 600ms, silence tails, RMS delta ≥ 4dB) with reason annotations ("natural endpoint", "hard reset", etc.). Capped at 120 rows with overflow summary. `_slim_transcript_for_prompt` strips `audio_cue` alongside `clip_metadata` + `shot_tag`. Injected into all six prompt builders.
- ✅ **4.3.4** Mode-aware footer in `_audio_cue_footer(mode)`: Assembled mode gets "tighten every pause > 800ms unless it lands on a narrative beat"; Short Generator gets "align span starts to is_silence_tail cues where transcript allows"; other modes get the generic "prefer natural endpoints" hint. All six builders pass their own `mode=` kwarg.

Lands in the next commit alongside this proposal update.

### Phase 4.4 — Per-mode activation + panel UI (1 day) ✅

- ✅ **4.4.1** `data/presets.py` — `SENSORY_MATRIX` + `resolve_sensory_layers(master, *overrides, preset, timeline_mode)` helper. Six rows per the proposal matrix (raw_dump / rough_cut / curated / assembled / clip_hunter / short_generator). Multi-candidate presets collapse onto their preset key; tightener collapses to assembled. Subtitle copy lives in `SENSORY_MODE_SUBTITLES` next to the matrix so schema drift is obvious.
- ✅ **4.4.2** `_models.py` — per-layer flags widened to tri-state (`bool | None`) so `None` = defer-to-matrix, `True` = force on, `False` = force off. `AnalyzeRequest` grows `sensory_master_enabled`. `build.py::_layer_a_enabled` + `_layer_a_enabled_for_preset` refactored to delegate to `resolve_sensory_layers`; Clip Hunter now gates off via matrix instead of the old hardcoded check. `analyze.py` applies server-side resolver for non-panel API callers (panel sends final booleans already).
- ✅ **4.4.3** Configure-screen `SensoryCard` with master toggle, dynamic subtitle driven by `sensoryModeKey(preset, timeline_mode)`, collapsed `<details>` Advanced expand showing live effective state for Layers C / A / Audio plus "(forced on/off)" labels. TS mirror of the matrix lives in `apps/panel/src/sensory.ts`. Pre-analyze master toggle also lands on the Preset screen under an Advanced `<details>` so new flows can opt in before analyze fires.
- ✅ **4.4.4** Auto-save — piggybacks on the existing `useEffect([runId, userSettings, cutName])` debounce in `App.tsx`. New fields flow through `onSettingsChange` like every other setting; no new wiring needed.

### Phase 4.5 — Hardening + observability (1 day) ✅

- ✅ **4.5.1** GUARDRAILS block on both vision prompts (shot_tagger already had it; boundary_validator extended). Post-hoc PII scrubber in `cutmaster/analysis/_sanitize.py` — redacts email / phone / SSN patterns — wired to `ShotTag.notable` + `BoundaryVerdict.reason` / `suggestion` via Pydantic `@field_validator(mode="after")` so every deserialised response gets scrubbed before it hits cache / logs / the Review screen.
- ✅ **4.5.2** Shared `threading.Semaphore` in `intelligence/llm.py` (env-tunable via `CELAVII_VISION_CONCURRENCY`, default 3). Acquired only when `images` is supplied so non-vision agents stay uncapped. Shot tagger still has its own async asyncio.Semaphore at the orchestrator layer; the module-level sync semaphore bounds the *actual* Gemini in-flight count across both layers.
- ✅ **4.5.3** `POST /cutmaster/sensory-cache/clear` in `http/routes/cutmaster/sensory_cache.py`. Body `{ "layers": "all" | ["c","a","audio"] }`; per-layer outcomes carry `cleared`, `existed_before`, `bytes_freed` so the panel can surface "freed N MB". Gating consistent with the rest of the destructive surface per §Decisions #4.
- ✅ **4.5.4** `logging_setup.ALLOWED_EXTRA_KEYS` grows seven entries: `tokens_in`, `tokens_out`, `model`, `cache_hit`, `cache_hits`, `frame_count`, `retry_count`. Stage-level emitters: `_shot_tag_stage` logs `frame_count` + `cache_hits`; `_audio_cues_stage` logs `word_count`; `validator_loop` logs `retry_count` + `frame_count` on accept and exhausted-retry paths.
- ✅ **4.5.5** Per-call cost telemetry in `call_structured` — wraps every Gemini invocation with `time.monotonic()` + reads `response.usage_metadata.{prompt_token_count, candidates_token_count}` through `_safe_token_count` (guarded for older SDK versions). Logs `tokens_in`, `tokens_out`, `model`, `elapsed_ms`, `cache_hit=False` on every call. No UI surface per §Decisions #1; aggregators can trend cost via the allowlisted keys.

---

## Out of scope for v4.0

- **Music / beat detection.** The audio layer is DSP-only, not ML. Music-video workflows need a dedicated pass.
- **Speaker-emotion classification.** Tempting, high hallucination risk on 3.1 Flash. Revisit when we have a validation set.
- **Vision in the *main* Director prompt (Option B).** Tags + validator is the agreed architecture; raw frames in the primary prompt stays off-table for v4.
- **Per-tag editor overrides in UI.** If tag quality issues surface, add a manual override in v4.6.
- **Cross-project cache sharing.** `~/.celavii/` is per-user. Teams wanting shared caches on network drives — out of scope.
- **Short-video vs frames experiment.** 3.1 Flash accepts both; v4 stays frames-only. Worth A/B-ing in v4.7.
- **Marker agent using tags.** Obvious extension (B-roll markers from `shot_type=broll`) — follow-up.
- **Resolve thumbnail reuse.** We extract via ffmpeg. Resolve's cached thumbs could shave latency in-app; deferred.

---

## Risks & mitigations

### R1 — Upload latency on first analyze
**Impact:** First run on 60-min source adds 30–90s before Configure is ready.
**Mitigation:** Gemini Files API (already used by STT), SSE per-item progress events, mode-aware sampling (don't tag items that won't enter the plan in Assembled mode).

### R2 — Schema drift silently breaks cached tags
**Impact:** v4.1 bumps a schema, old caches deserialise into garbage fields.
**Mitigation:** Every cache path is versioned (`shot-tags/v1/`, `boundary-frames/v1/`, `audio-cues/v1/`). Schema changes bump to `v2/`. Old cache becomes unreachable, not corrupt.

### R3 — Gemini prompt drift captures sensitive content
**Impact:** `notable` field describes on-screen whiteboards, faces, private text.
**Mitigation:** Explicit prompt guardrail on all vision calls, `notable` capped at 80 chars, sits outside the v3 logging allowlist so it's never in aggregators, per-string sanitizer strips anything resembling an email / phone / SSN pattern.

### R4 — Retry storms in the validator loop
**Impact:** Director loops 3× on a 60-min source, each retry sends 50+ boundary-frame comparisons.
**Mitigation:** Retry cap of 2. Remaining rejections surface in Review as warnings — the editor decides. Frame cache amortises repeated comparisons.

### R5 — First-run cost shock
**Impact:** Real user runs analyze on a 60-min podcast, burns ~$0.03 of Gemini credits.
**Mitigation:** Opt-in default. Panel copy surfaces the cached-after-first-run pitch. Execute history records per-build cost so users can audit.

### R6 — Mode-mismatch waste
**Impact:** User enables the master toggle on Assembled mode and expects dramatic improvements; gets modest audio-driven tightening and wonders what happened.
**Mitigation:** Dynamic subtitle copy explains what the master toggle actually activates per mode. Advanced expand makes the mix visible. Cost telemetry in Review so users see why a mode yielded a small change.

### R7 — Director prompt length balloon
**Impact:** Tags + audio cues + CLIP METADATA + existing prompt exceeds the model's attention window on long sources.
**Mitigation:** Tag-block and audio-cue block both use coalesced-range rendering (one row per stable range), not per-word rows. On a 60-min source, blocks stay ~50-150 lines even with full coverage. Hard length cap with overflow summary ("... 34 more ranges omitted").

### R8 — DSP heuristics misfire
**Impact:** Laughter detection fires on applause; breath detection fires on air-conditioning.
**Mitigation:** Heuristics are flags, not gates — Director prompt treats them as hints. False-positive rate measurable via qualitative review. If quality is bad, the layer is independently toggleable off while keeping C + A on.

---

## Success criteria

### v4.0 (all three layers shipped)

- Full pipeline runs end-to-end on a 5-min clip with master toggle on, completing in ≤2× non-sensory first-run time and ≤1.2× cached rerun time.
- All six Director variants include the relevant blocks when layers are active and none when layers are off.
- Zero regressions: every existing test stays green. The `sensory_master_enabled=false` path is byte-identical to v3.
- Boundary-validator rejection → retry → acceptance loop works on at least one real session where a cut was visually rejected and the retry produced a better one.
- No `GEMINI_API_KEY` → all vision layers skip with clear events; Audio layer still runs; pipeline completes.

### v4.1+ (qualitative, post-ship)

- Blind A/B on ≥10 real editorial sessions shows preference for sensory-on over sensory-off at ≥60%.
- Cache hit rate > 90% on reruns within a single project.
- Opt-in rate > 40% among users with a Gemini key after one month of availability.

---

## Decisions (all five open questions resolved)

1. **Cost telemetry → logs only.** Every vision call logs `tokens_in`, `tokens_out`, `model`, `elapsed_ms`, `cache_hit`. No Review-screen UI surface in v4.0. Logs feed workflow-cost analysis + optimisation opportunities. `tokens_in` / `tokens_out` / `cache_hit` added to the `logging_setup.py` allowlist. Phase 4.5.5 becomes **per-call telemetry in logs**, not a Review-screen badge.
2. **Validator retry cap → 2.** Caps worst-case latency. Remaining `jarring` cuts after 2 retries fall through to Review-screen warnings.
3. **Advanced expand → collapsed by default.** Power-user escape hatch; doesn't clutter the Configure card for the typical editor.
4. **Cache-clear gating → none.** Consistent with the rest of the panel API: every destructive endpoint (`/delete-run`, `/delete-cut`, `/delete-all-cuts`, now `/sensory-cache/clear`) trusts the default 127.0.0.1 bind. If network exposure becomes a real concern, add middleware covering the whole destructive surface in a separate hardening pass — don't gate one endpoint in isolation.
5. **Panel master-toggle label → "Shot-aware editing".** Reads as familiar craft, not a feature flag. Dynamic subtitle under the toggle explains what the current preset/mode activates.

---

## References

- v3 API hardening retrospective: [../v3/api_hardening.md](../v3/api_hardening.md)
- v3 Director intelligence backlog: [../v3/hardening.md](../v3/hardening.md)
- Archived v4 draft (pre-reassessment): [./proposal_archive.md](./proposal_archive.md)
- Per-clip STT cache convention: [src/celavii_resolve/cutmaster/stt/per_clip.py](../../../src/celavii_resolve/cutmaster/stt/per_clip.py)
- ffmpeg audio extraction (convention reuse): [src/celavii_resolve/cutmaster/media/ffmpeg_audio.py](../../../src/celavii_resolve/cutmaster/media/ffmpeg_audio.py)
- Director prompt builders (injection points): [src/celavii_resolve/cutmaster/core/director.py](../../../src/celavii_resolve/cutmaster/core/director.py)
- Structured-log allowlist (v3 batch 6): [src/celavii_resolve/logging_setup.py](../../../src/celavii_resolve/logging_setup.py)
