# Three-Axis Cut Model

**Status:** design proposal — not yet implemented. Revised after validation against the current code.
**Supersedes:** the single-preset `PresetBundle` model in [`src/cutmaster_ai/cutmaster/data/presets.py`](../src/cutmaster_ai/cutmaster/data/presets.py).

This document explains why today's preset picker produces confusing results, what the axes are, and how the matrix resolves to concrete Director-prompt variables. Read this before touching the preset schema or the Configure screen. Implementation steps live in [`Implementation/workflow/three-axis-model.md`](../Implementation/workflow/three-axis-model.md).

---

## 1. The problem

Today the Configure screen asks one question: **"Pick a preset."** The twelve options (eleven presets + `auto`) conflate three independent concerns:

| Preset today        | What it actually encodes                              |
|---------------------|-------------------------------------------------------|
| Vlog                | *content type* (what's on the timeline)               |
| Interview           | *content type*                                        |
| Wedding             | *content type*                                        |
| Podcast             | *content type*                                        |
| Presentation        | *content type*                                        |
| Product Demo        | *content type*                                        |
| Tutorial            | *content type*                                        |
| Reaction            | *content type*                                        |
| Clip Hunter         | **cut intent** (multi-output — N clips from long-form) |
| Short Generator     | **cut intent** (assembled montage reel)               |
| Tightener           | **cut intent** (surgical preserve-takes operation)    |
| Auto                | *cascade trigger* — auto-resolve content type         |

The last three aren't content types — they're *what the user is making*. Placing them in the same picker as Vlog/Interview/etc. forces users to choose between describing the footage and describing the deliverable.

Concrete symptom: a user with interview footage who wants a 60-second short has to pick either **Interview** (right analysis, wrong pacing — 22 s beats in a 60 s cut ≈ 2–3 segments) or **Short Generator** (right pacing, wrong analysis — loses speaker-awareness and quote-hunting). Neither fits.

The `PresetBundle` schema bakes this conflation into the data model: input-profile fields (`role`, `cue_vocabulary`, `speaker_awareness`, `scrub_defaults`) sit next to output-profile fields (`target_segment_s`, `pacing`, `reorder_mode`) with no separation.

---

## 2. The axes

Any cut the user wants is specified by four independent variables. Three are picked in Configure; the fourth (`timeline_mode`) describes the source's edit-lifecycle state and is already a shipped concept.

```
                 ┌─── AXIS 1 ─────────── What is this footage?
                 │
   user intent ──┼─── AXIS 2 ─────────── What am I making from it?
                 │
                 ├─── AXIS 3 ─────────── How long / how many?
                 │
                 └─── AXIS 4 ─────────── What edit state is the source in?
                                         (timeline_mode — existing)
```

### Axis 1 — Content type (8 options)

Describes the raw. Drives analysis behaviour: what cues to listen for, what themes to probe, how to handle multi-speaker audio, which categories to exclude.

- Auto-detect
- Vlog
- Interview
- Wedding
- Podcast
- Presentation
- Product Demo
- Tutorial
- Reaction

### Axis 2 — Cut intent (5 options)

Describes the deliverable. Drives arrangement behaviour and prompt routing.

| Intent              | Meaning                                                                |
|---------------------|------------------------------------------------------------------------|
| Narrative cut       | One coherent cut preserving (or lightly re-sequencing) the source arc  |
| Peak highlight      | Single strongest moment, free to reorder                               |
| Multi-clip          | N independent clips, each internally chronological                     |
| Assembled short     | Montage-style reel stitched from multiple non-adjacent moments         |
| Surgical tighten    | Preserve all takes and ordering; remove only dead air, restarts, filler |

### Axis 3 — Duration / count

Existing `target_length_s` (seconds) plus, for Multi-clip, `num_clips` (1–5, already on `UserSettings`).

### Axis 4 — Timeline mode (existing `UserSettings.timeline_mode`)

`raw_dump` / `rough_cut` / `curated` / `assembled`. Describes how much editorial work has already happened on the source timeline and thus which Director prompt builder runs.

Axis 4 is not a new concept — it already gates which of the six prompt builders in [`director.py`](../src/cutmaster_ai/cutmaster/core/director.py) executes. The three-axis model preserves it and specifies how it interacts with Axis 2.

---

## 3. What each axis controls

The Director prompt reads four resolved variables:

| Variable              | Set by                                             | Example values                                                      |
|-----------------------|----------------------------------------------------|---------------------------------------------------------------------|
| `reorder_mode`        | content_type × cut_intent                          | `locked`, `preserve_macro`, `free`, `per_clip_chronological`        |
| `segment_pacing`      | content_type (base) × cut_intent (modifier) × duration | `{min: 3, target: 8, max: 15}` (seconds)                        |
| `selection_strategy`  | cut_intent                                         | `narrative-arc`, `peak-hunt`, `top-n`, `montage`, `preserve-takes`  |
| `prompt_builder`      | cut_intent × timeline_mode                         | `_prompt`, `_assembled_prompt`, `_clip_hunter_prompt`, …            |

### Source of truth for pacing

To avoid schema drift, pacing is resolved by a single formula:

```
resolved_target_s = content_profile.default_target_segment_s × cut_intent.pacing_modifier × duration_factor
```

- `content_profile.default_target_segment_s` — lives on the content profile only (lifted from today's `PresetBundle.target_segment_s`).
- `cut_intent.pacing_modifier` — float. `narrative` = 1.0, `peak_highlight` ≈ 0.4, `assembled_short` ≈ 0.3, `surgical_tighten` = 1.0 (bounds preserved, pacing irrelevant), `multi_clip` ≈ 0.6.
- `duration_factor` — a small adjustment (`0.8`–`1.1`) that tightens pacing for short outputs. Tuned against fixtures in the implementation's Phase 6.

`min_segment_s` and `max_segment_s` scale proportionally. No "tight/medium/loose" pacing profile names; no matrix cells carry numeric pacing.

### `reorder_mode` uses the existing vocabulary

Today's [`PresetBundle.reorder_mode`](../src/cutmaster_ai/cutmaster/data/presets.py#L75) enum: `free` / `preserve_macro` / `locked`. The three-axis model keeps all three and adds one value for the Podcast × Multi-clip case:

- `locked` — output order matches source order exactly (except the hook, floated to position 0).
- `preserve_macro` — reorder allowed within chapter / take groups; not across them.
- `free` — any order.
- `per_clip_chronological` — **new**. Each produced clip is internally `locked`; clips themselves aren't ordered by source time. Only meaningful when `cut_intent = multi_clip`.

### Interaction with `reorder_allowed`

[`UserSettings.reorder_allowed`](../src/cutmaster_ai/http/routes/cutmaster/_models.py#L225) is an existing assembled-mode boolean override. The resolution rule is: **enforce `locked` when `reorder_mode == "locked"` OR `reorder_allowed == False`**. The editor's explicit override still wins.

---

## 4. The interaction matrix

Rows = Axis 1. Columns = Axis 2. Cell = `(reorder_mode, selection_strategy, pacing_modifier)` + a short description.

| Content ↓ / Intent →  | Narrative                                 | Peak Highlight                           | Multi-clip                                | Assembled Short                          | Surgical Tighten                          |
|-----------------------|-------------------------------------------|------------------------------------------|-------------------------------------------|------------------------------------------|-------------------------------------------|
| **Vlog**              | `preserve_macro` / narrative-arc / 1.0    | `free` / peak-hunt / 0.4                 | `per_clip_chronological` / top-n / 0.6    | `free` / montage / 0.25                  | `preserve_macro` / preserve-takes / 1.0   |
| **Interview**         | `locked` / narrative-arc / 1.0            | `free` / peak-hunt / 0.35                | `per_clip_chronological` / top-n / 0.55   | `free` / montage / 0.3                   | `locked` / preserve-takes / 1.0           |
| **Wedding**           | `preserve_macro` / narrative-arc / 1.0    | `free` / peak-hunt / 0.4                 | `per_clip_chronological` / top-n / 0.6    | `free` / montage / 0.25                  | `preserve_macro` / preserve-takes / 1.0   |
| **Podcast**           | `locked` / narrative-arc / 1.0            | `free` / peak-hunt / 0.35                | `per_clip_chronological` / top-n / 0.55   | `free` / montage / 0.3                   | `locked` / preserve-takes / 1.0           |
| **Presentation**      | `locked` / narrative-arc / 1.0            | `free` / peak-hunt / 0.35                | `per_clip_chronological` / top-n / 0.55   | `free` / montage / 0.3                   | `locked` / preserve-takes / 1.0           |
| **Product Demo**      | `preserve_macro` / narrative-arc / 1.0    | `free` / peak-hunt / 0.4                 | `per_clip_chronological` / top-n / 0.6    | `free` / montage / 0.3                   | `preserve_macro` / preserve-takes / 1.0   |
| **Tutorial**          | `locked` / narrative-arc / 1.0            | `free` / peak-hunt / 0.4                 | *unusual — steps are linear*              | `free` / montage / 0.3                   | `locked` / preserve-takes / 1.0           |
| **Reaction**          | `locked` / narrative-arc / 1.0 (sync)     | `free` / peak-hunt / 0.3                 | `per_clip_chronological` / top-n / 0.55   | `free` / montage / 0.25                  | `locked` / preserve-takes / 1.0           |

Content-type base values (`default_target_segment_s`, `speaker_awareness`, cue vocab, theme axes) come from the existing presets unchanged. Only `reorder_mode`, `selection_strategy`, and `pacing_modifier` come from the cell.

### Load-bearing cells

These are where Axis 2 actually flips behaviour, not just tweaks pacing:

- **Interview × Peak Highlight vs Interview × Narrative** — same content, same `preserve_macro`-vs-`locked` difference as today, but the peak-highlight path opens up `free` reorder so "strongest quote regardless of position" becomes expressible.
- **Podcast × Multi-clip** — `per_clip_chronological` is the new reorder mode: each clip internally locked, clips themselves selected for punch.
- **Wedding × Peak Highlight** — `free` reorder unlocks the 60 s teaser pulling kiss + vows + first dance out of chronology, while Wedding × Narrative keeps today's `preserve_macro` for the ceremony film.

### Unusual combinations

Not nonsensical, but worth a gentle UI confirmation:

- Tutorial × Multi-clip — tutorials are linear; clip-harvesting is rare.
- Wedding × Surgical Tighten without Narrative — probably meant Wedding × Narrative.

These render an inline hint. They don't block.

---

## 5. Axis 2 × Axis 4 (cut intent × timeline mode)

Not every cut intent is valid in every timeline mode. The existing [`_INCOMPATIBLE` matrix](../src/cutmaster_ai/cutmaster/data/presets.py#L833) already blocks `tightener × {raw_dump, rough_cut, curated}`; the three-axis model relocates that constraint under Axis 2:

| Cut intent          | raw_dump | rough_cut | curated | assembled |
|---------------------|----------|-----------|---------|-----------|
| Narrative           | ✓        | ✓         | ✓       | ✓         |
| Peak highlight      | ✓        | ✓         | ✓       | ✓         |
| Multi-clip          | ✓        | ✓         | ✓       | ✗ (source is already a single cut) |
| Assembled short     | ✓        | ✓         | ✓       | ✓         |
| Surgical tighten    | ✗ (source not assembled) | ✗ | ✗ | ✓         |

`cut_intent = surgical_tighten` forces `timeline_mode = assembled` — this is today's tightener behaviour, preserved.

### Prompt-builder routing

The six existing prompt builders in [`director.py`](../src/cutmaster_ai/cutmaster/core/director.py) stay; `(cut_intent, timeline_mode)` picks one:

| cut_intent × timeline_mode                     | Prompt builder today         |
|-------------------------------------------------|------------------------------|
| `narrative` × `raw_dump`                        | `_prompt`                    |
| `narrative` × `rough_cut`                       | `_rough_cut_prompt`          |
| `narrative` × `curated`                         | `_curated_prompt`            |
| `narrative` × `assembled`                       | `_assembled_prompt`          |
| `peak_highlight` × any                          | `_prompt` (with peak strategy) |
| `multi_clip` × any (except assembled)           | `_clip_hunter_prompt`        |
| `assembled_short` × any                         | `_short_generator_prompt`    |
| `surgical_tighten` × `assembled` (forced)       | `_assembled_prompt`          |

Each builder renames its use of `preset.role` / `preset.hook_rule` / `preset.pacing` to read from resolved variables. Prompt routing logic stays identical in shape; the dispatch key just becomes `cut_intent` instead of `preset`.

### Sensory matrix keys

[`SENSORY_MATRIX`](../src/cutmaster_ai/cutmaster/data/presets.py#L887) keys today include `clip_hunter` and `short_generator` as pseudo-modes. Under the new model the matrix is keyed by `(cut_intent, timeline_mode)` with the same shape:

```
(multi_clip, *)            → clip_hunter row's sensory defaults
(assembled_short, *)       → short_generator row's sensory defaults
(surgical_tighten, assembled) → assembled row's sensory defaults
(narrative, raw_dump)      → raw_dump row
(narrative, rough_cut)     → rough_cut row
(narrative, curated)       → curated row
(narrative, assembled)     → assembled row
(peak_highlight, *)        → raw_dump row  (default — revisit if telemetry shows divergence)
```

---

## 6. Axis 2 auto-resolution

Most users don't want to pick a cut intent. Axis 2 defaults to **Auto** and resolves from duration + content type:

```
if timeline_mode == "assembled" and source carries the "takes_already_scrubbed" marker:
    → Surgical Tighten
elif num_clips > 1:
    → Multi-clip
elif duration < 45 s:
    → Peak Highlight                 (except Product Demo → Assembled Short)
elif duration < 120 s:
    → Peak Highlight                 (except Product Demo/Vlog → Assembled Short)
elif duration < 600 s:
    → Narrative                      (except Reaction → Peak Highlight)
else:
    → Narrative
```

**Veterans always see the resolved value.** Auto is not hidden magic — the Configure screen shows `Auto → Peak Highlight` once content type and duration are known. Clicking the chip exposes the full Axis 2 picker for override.

---

## 7. Cascade handoff (Axis 1 `auto_detect`)

When `content_type = auto_detect`, the [autodetect cascade](../Implementation/optimizaiton/autodetect-cascade.md) runs synchronously before axis resolution:

```
request(content_type=auto_detect, cut_intent=..., duration, timeline_mode)
  ↓
cascade.detect_preset(transcript, run_state) → content_type ∈ Axis 1 (8 values)
  ↓
resolve_axes(content_type, cut_intent, duration, timeline_mode) → ResolvedAxes
  ↓
Director prompt builder reads ResolvedAxes
```

The cascade's codomain is the **seven non-auto content types in Axis 1** (Vlog, Interview, Wedding, Podcast, Presentation, Product Demo, Tutorial, Reaction). Presentation stays in scope — it is a first-class content type today and survives the migration. The cascade never returns `auto_detect` itself, and never returns a cut intent.

Cascade failures fall back to Vlog (the historical default) so axis resolution always has a concrete content type to work with.

---

## 8. Why this is simpler, not more complex

Adding an axis sounds like more UI. In practice it removes the source of confusion and shrinks the primary picker:

| Aspect                          | Today                                     | Proposed                                 |
|---------------------------------|-------------------------------------------|------------------------------------------|
| Content-type picker options     | 12 (8 content + 3 intents + auto)         | 9 (8 content + auto)                     |
| Visible knobs for casual users  | 1 preset + duration                       | 1 content type + duration (Auto intent)  |
| Knobs available to specialists  | 1 preset                                  | content type + intent + duration         |
| Expressible combinations        | 12                                        | 8 × 5 = 40                               |
| Prompt coherence                | preset conflates intent; overloaded vars  | four resolved variables, single source of truth for pacing |

A hobbyist picking Interview + 60 s gets auto-resolved to **Interview × Peak Highlight × 60 s** without touching Axis 2. A podcast clips editor picks **Podcast × Multi-clip × 60 s × num_clips=5** — impossible to express today.

---

## 9. Relationship to the autodetect cascade

The cascade proposal at [`Implementation/optimizaiton/autodetect-cascade.md`](../Implementation/optimizaiton/autodetect-cascade.md) is the **implementation of Axis 1 auto-detection** under this model.

| Concern                                          | Owned by          |
|--------------------------------------------------|-------------------|
| How content type is detected from the transcript | cascade           |
| What the content-type field *means* in the prompt | three-axis model  |
| How cut intent is chosen                          | three-axis model  |
| How the four axes combine into Director variables | three-axis model (resolution layer) |

Concrete alignment:

- Cascade Open Question 5 ("Tightener / Clip Hunter / Short Generator — exclude from the cascade") is resolved here: those three move to Axis 2, and the cascade's content-type codomain is the 8 Axis 1 values (7 concrete + `auto_detect` trigger, which never loops back into the cascade).
- `PresetRecommendation` becomes a `ContentTypeRecommendation`; `preset_key` becomes `content_type`. Route-layer public surface stays the same.
- `run["autodetect_signals"]` cache (cascade Phase 1.7) keeps working unchanged.
- Tier 4 narrowed candidate set shrinks from 11 → 7 candidates. Simpler prompt.

The two proposals can ship in either order. The cascade improves *how* Axis 1 is detected without touching what Axis 1 *is*; the three-axis model defines what Axis 1 means without touching detection.

---

## 10. Effect on the Director prompt

Today, each prompt builder opens with:

```
You are a {preset.role}.
…
Pacing: {preset.pacing}; segments {preset.min_segment_s}–{preset.max_segment_s} s.
```

After:

```
You are a {content_profile.role} producing a {cut_intent.label}.
Arrange beats: {reorder_mode}.
Pacing: {min}–{target}–{max} s.
Selection strategy: {selection_strategy}.
{content_profile.speaker_awareness, if applicable}
{content_profile.cue_vocabulary}
```

Same token budget. Four resolved variables replace one overloaded preset. Instructions stop contradicting themselves when content type and desired output disagree.

---

## 11. Migration path summary

The full implementation plan is in [`Implementation/workflow/three-axis-model.md`](../Implementation/workflow/three-axis-model.md). Legacy preset keys map forward so saved sessions still open:

| Old preset        | → content_type   | → cut_intent       | → forced timeline_mode |
|-------------------|------------------|--------------------|------------------------|
| `clip_hunter`     | `auto_detect`    | `multi_clip`       | (unchanged)            |
| `short_generator` | `auto_detect`    | `assembled_short`  | (unchanged)            |
| `tightener`       | `auto_detect`    | `surgical_tighten` | `assembled`            |

---

## 12. What this is not

- **Not a rewrite of the pipeline.** Scrubber, STT, cascade, marker agent, and cut-plan executor stay unchanged. Only the prompt input variables, the preset schema, and the Configure screen change.
- **Not more knobs for everyone.** Casual users still answer one question (content type) and one number (duration). Axis 2 is invisible until they want it; Axis 4 (timeline_mode) is already a separate Configure control and stays there.
- **Not a new LLM surface.** Axis resolution is deterministic. No additional roundtrips.

---

## Appendix — Resolved variable reference

### `reorder_mode`

| Value                     | Meaning                                                                   |
|---------------------------|---------------------------------------------------------------------------|
| `locked`                  | Output order matches source order exactly (except hook floated to pos 0). |
| `preserve_macro`          | Reorder within chapter / take boundaries; not across.                     |
| `free`                    | Any order.                                                                |
| `per_clip_chronological`  | Each produced clip internally locked; clips themselves unordered.         |

### `selection_strategy`

| Value              | Meaning                                                                   |
|--------------------|---------------------------------------------------------------------------|
| `narrative-arc`    | Select beats that form a setup → development → payoff arc.                |
| `peak-hunt`        | Select the single highest-impact moment.                                  |
| `top-n`            | Select N independent high-impact moments.                                 |
| `montage`          | Select many short fragments stitched with pacing-driven rhythm.           |
| `preserve-takes`   | Select nothing; trim only filler within the existing take order.          |

### Pacing resolution

```
base_target = content_profile.default_target_segment_s
modified    = base_target × cut_intent.pacing_modifier
duration_factor = clamp(0.8, (duration_s / 180)^0.15, 1.1)
resolved_target = modified × duration_factor
resolved_min    = max(2.0, resolved_target × 0.4)
resolved_max    = resolved_target × 2.5
```

Examples (Interview, `default_target_segment_s=22`):

| Cut intent     | Duration | `pacing_modifier` | Resolved `{min, target, max}` |
|----------------|----------|-------------------|-------------------------------|
| Narrative      | 600 s    | 1.0               | `{8, 22, 55}`                 |
| Peak highlight | 60 s     | 0.35              | `{3, 7, 18}`                  |
| Narrative      | 60 s     | 1.0               | `{8, 20, 50}`                 |

Curve shape (`^0.15`) and constants are provisional; Phase 6 calibrates them against labelled fixtures.
