# Auto-detect cascade

**Status:** PROPOSED — no code yet.
**Goal:** Replace the current "one heuristic OR one LLM" flow with a cascade of independent signals. Most runs converge without an LLM call; the LLM only fires when independent tiers can't agree, and it picks from a narrowed candidate set.
**Owner:** TBD.

## Why

[auto_detect.py](../../src/cutmaster_ai/cutmaster/analysis/auto_detect.py) currently does:

1. A narrow deterministic heuristic for three cases only (1 spk + long → presentation; 2 spk + turns → interview; 3+ spk + long → podcast).
2. Otherwise, one LLM call over three transcript bands.

The model is the only path for **vlog, product_demo, wedding, tutorial, reaction** and for any ambiguous interview/podcast/presentation. Two problems follow:

- **Confidence is the model's self-assessment**, which flash-lite systematically overstates. Editors see "85% — interview" on a talk that's really a podcast.
- **Network-dependent.** If Gemini is slow or unreachable, autodetect fails. The recent run took ~6 s for the theme analyzer on the same transcript that could be pre-classified in <20 ms.

We have more free signal than we use. Every autodetect run already holds the Deepgram output: per-word `word`, `start_time`, `end_time`, `speaker_id`, `confidence`, plus the inserted sentence punctuation. From those raw fields alone we can compute:

- **Deepgram-derived signals** — speaker count, speaker turn count, speaker overlap rate (simultaneous speech detection from timestamp overlaps), words-per-second, median sentence length (from Deepgram's punctuation), **question rate** (% of sentences ending in `?` — a near-deterministic interview signal), pause-length distribution (not just mean, but shape: presentations have bimodal pause distributions; reactions are tight), low-confidence cluster density (applause/laughter/crowd noise clusters correlate with presentation / live-event content), and per-speaker word share.
- **Scrubber output** — filler rate, restart rate, dead-air rate (all computed from Deepgram words).
- **Resolve / run state** — total duration, frame rate, aspect ratio, source clip count.
- **Preset metadata** — each preset's `cue_vocabulary`, already defined per preset in [presets.py](../../src/cutmaster_ai/cutmaster/data/presets.py).

None of these go into the current classifier — the model sees only the word stream and the explicit `speaker_count` / `speaker_turn_count` fields we added in the last autodetect pass. Everything else is thrown away before the classifier runs.

## Design

A five-tier cascade. Each tier produces a **score per preset**, not a pick. The final preset is the argmax of accumulated scores; confidence is derived from agreement across tiers (not from any single model's self-assessment).

### Tier 0 — Source metadata (≤ 1 ms, no transcript reading)

| Signal | Source | Narrows |
|---|---|---|
| `total_duration_s` | `transcript[-1].end_time` | <3 min ⇒ reaction; 30+ min ⇒ interview/podcast/presentation |
| `clip_count` | Resolve timeline (needs threading run state) | 1 clip ⇒ raw capture; many clips ⇒ already-cut vlog/tutorial |
| `aspect_ratio` | Resolve project | 9:16 rules out interview and presentation |
| `frame_rate` | Resolve | 50-60 fps leans product_demo / action |

### Tier 1 — Transcript structure, derived entirely from Deepgram output (≤ 20 ms, no LLM)

Every signal in this tier is computed from the data we already persist on the run state: Deepgram's per-word `word`, `start_time`, `end_time`, `speaker_id`, `confidence`, and the punctuation it inserts into the word tokens themselves (`.`, `?`, `!`, commas). Nothing here requires a second transcript pass or any external API.

| Signal | Derived from | Discriminates |
|---|---|---|
| **Speaker count** | diarization `speaker_id` | 1 = monologue; 2 = interview; 3+ = podcast |
| **Speaker turn count** | adjacent `speaker_id` changes | Separates "1 spk with cameo" from "true interview" |
| **Speaker overlap rate** | pairs of words from different speakers where `start_time[i] < end_time[i-1]` | High overlap ⇒ informal podcast / reaction; zero overlap ⇒ interview / presentation |
| **Words per second** | `word_count / total_duration_s` | High ⇒ vlog/reaction; low ⇒ wedding/presentation |
| **Median sentence length** | sentence coalescer over Deepgram punctuation | Short ⇒ tutorial; long ⇒ presentation/interview |
| **Question rate** | `% of sentences ending in "?"` | Interview gold signal — interviewer asking questions |
| **Filler rate** | scrubber output (consumes Deepgram words) | Low ⇒ scripted (tutorial, demo); high ⇒ spontaneous |
| **Restart rate** | scrubber output | Near-zero = scripted; high = unscripted |
| **Dead-air rate** | inter-word gaps from Deepgram timestamps | Wedding-high; tutorial-low |
| **Pause-length distribution** | quartiles of inter-word gaps | Presentation = long deliberate pauses (high p95); reaction = very short (tight p95) |
| **Low-confidence cluster density** | Deepgram `confidence < 0.5` events per minute | Crowd noise / applause / music clusters suggest presentation or live event |
| **Per-speaker word share** | `word_count_per_speaker / total_word_count` | Interview host usually has 20-40 %; podcast is 30/30/30; presentation is 95 %+ |

The Deepgram fields we're now using that the current implementation ignores:

- **`confidence`** — low-confidence clusters are a reliable applause/laughter/crowd-noise signal for presentation/podcast (Layer Audio DSP cues would confirm, but we don't need to wait for Layer Audio to run).
- **Sentence-terminal punctuation** — especially `?`, which is near-deterministic for interview content.
- **Per-word `start_time` / `end_time` gap distribution** — not just the mean (dead-air rate) but the shape. Presentations have bimodal distributions (short inside a thought, long between thoughts); reactions are tight.
- **Speaker-overlap detection from timestamps** — Deepgram gives us per-word speaker + timestamps, so we can count words where two speakers are talking simultaneously. This separates conversational (podcast) from formal (interview) spoken formats.

### Signal degradation rules (STT-provider independence)

`confidence` and punctuation are STT-provider-dependent: the Deepgram path populates both; the Gemini STT path may not. Each Tier 1 signal must degrade gracefully rather than failing the cascade:

| Signal | Degradation when source field is absent |
|---|---|
| Low-confidence cluster density | All words have `confidence is None` → signal returns neutral 0 (skipped). |
| Question rate | No `?` in any word → signal returns neutral 0 (skipped). |
| Median sentence length | No terminal punctuation → fall back to pause-based sentence breaks (`_sentence_spans` already handles this). |
| Pause distribution | Always available (timestamps are universal). |
| Speaker overlap rate | Always available if speaker diarization ran. |

The cascade never fails because one signal is blank — it just re-weights the remaining signals. A Gemini-STT run produces a slightly less-confident classification, not a crash.

### Tier 2 — Cue vocabulary overlap (≤ 20 ms, no LLM)

Score the transcript against each preset's `cue_vocabulary`. Weighted by distinctiveness: tutorial's `"step one"`, `"click"`, `"select"`, `"drag"` are strong signals; vlog's `"as you can see"` overlaps with product_demo and is generic. Distinctiveness is computed offline as `1 / (number of presets that include this cue)`.

### Tier 3 — Opening-sentence micro-classifier (≤ 1 s, cheap LLM)

Send only the first coalesced sentence. Return a single preset label. ~200 tokens in, ~10 tokens out. Catches the rhetorical openers the structural signals can't see:

- "Welcome back to the channel" → vlog
- "Thank you for having me" → interview (guest)
- "Today we're making" → tutorial
- "Oh my god, did you see" → reaction
- "Good afternoon, everyone" → presentation

Only runs when Tiers 0-2 don't converge. Dropped entirely when the margin from Tier 0+1+2 already clears the confidence bar.

### Tier 4 — Full-band LLM over narrowed candidate set

Current implementation, but improved:

- Fed the **top 3 candidates from accumulated scores**, not all 11 presets.
- Fed the **tier 0-2 signal summary** alongside the bands, so the model has to justify its pick against objective evidence.
- Used only when Tiers 0-3 don't land a confident answer.

### Tier 5 — Vision (reserved)

Sample 2-3 frames: stage talk vs talking head vs UI vs handheld. Only fires when Tier 4 still can't decide. Not in scope for this phase — listed for completeness.

## Scoring & confidence

Each tier produces `scores: dict[preset_key, float]` in `[0, 1]`. The cascade merges with weights:

```python
def merge(t0, t1, t2, t3):
    out = {}
    for k in PRESETS:
        out[k] = (
            0.15 * t0.get(k, 0)
            + 0.35 * t1.get(k, 0)
            + 0.25 * t2.get(k, 0)
            + 0.25 * t3.get(k, 0)
        )
    return out
```

Confidence = margin between top and second, clamped to `[0, 1]`. Margin ≥ 0.25 → `confidence ≥ 0.85` (high); margin in `[0.1, 0.25]` → moderate; margin < 0.1 → low (surface alternatives).

Weights and thresholds get calibrated against a labeled fixture set (see Open Questions).

## Codebase patterns adopted

The cascade reuses three existing conventions rather than inventing new ones:

1. **Run-state caching** — the themes analyzer caches its result on `run["story_analysis"]` keyed by preset so Configure re-entry is <50 ms ([`presets.py::analyze_themes`](../../src/cutmaster_ai/http/routes/cutmaster/presets.py#L91)). The cascade does the same under `run["autodetect_signals"]` — second click on "auto", or Back/Forward through Configure, never recomputes.
2. **Shared helper modules** — prose scrubbing lives in [`_sanitize.py`](../../src/cutmaster_ai/cutmaster/analysis/_sanitize.py) and is imported by `shot_tagger.py` and `boundary_validator.py`. The sentence coalescer follows this pattern as a new `_sentences.py` instead of being copy-pasted or cross-imported from `core/`.
3. **`call_structured` with validator + `accept_best_effort`** — the standard LLM-call shape across the codebase ([`intelligence/llm.py`](../../src/cutmaster_ai/intelligence/llm.py)). Tier 3 and Tier 4 both use it; Tier 3's validator rejects non-auto-eligible preset keys so a hallucinated `tightener` classification can't leak out.

## Control flow

```python
def detect_preset(
    transcript: list[dict],
    run_state: dict | None = None,
) -> PresetRecommendation:
    # Re-entry cache (mirrors the themes_cache pattern).
    if run_state is not None:
        cached = run_state.get("autodetect_signals")
        if cached:
            return cached["recommendation"]

    scrub_counts = (run_state or {}).get("scrub_counts")

    t0 = score_by_metadata(run_state) if run_state else {}
    t1 = score_by_transcript_structure(transcript, scrub_counts=scrub_counts)
    t2 = score_by_cue_vocabulary(transcript)

    signals = {"tier0": t0, "tier1": t1, "tier2": t2}
    combined = merge(t0, t1, t2, {})   # tier3 empty for now
    top = top_n(combined, n=2)
    margin = top[0].score - top[1].score

    if margin >= HIGH_CONFIDENCE_MARGIN:
        rec = recommendation(top[0], confidence=margin_to_conf(margin),
                             alternatives=[], signals=signals)
        _cache(run_state, signals, rec)
        return rec

    # Narrow to top 3 for LLM tiers
    candidates = top_n(combined, n=3)

    # Tier 3: opening-sentence micro-classifier (only in the ambiguous band)
    if 0.1 <= margin < 0.25:
        t3 = classify_opening_sentence(first_sentence(transcript))
        combined = merge(t0, t1, t2, t3)
        top = top_n(combined, n=2)
        margin = top[0].score - top[1].score
    if margin >= HIGH_CONFIDENCE_MARGIN:
        rec = recommendation(top[0], confidence=margin_to_conf(margin),
                             alternatives=[], signals=signals)
        _cache(run_state, signals, rec)
        return rec

    # Tier 4: full-band LLM over narrowed candidates
    pick = full_band_llm(transcript_bands, candidates, signals)
    rec = recommendation(
        pick,
        confidence=margin_to_conf(margin),
        alternatives=[c.key for c in candidates if c.key != pick.key][:2],
        signals=signals,
    )
    _cache(run_state, signals, rec)
    return rec
```

## File layout

The existing module is [`auto_detect.py`](../../src/cutmaster_ai/cutmaster/analysis/auto_detect.py) (snake-case). The proposal promotes it to a subpackage **of the same name** so we don't end up with `auto_detect.py` and `autodetect/` coexisting (two files named differently for the same feature — confusing to navigate).

```
src/cutmaster_ai/cutmaster/analysis/
├── _sentences.py                # NEW — shared sentence coalescer (moved from core/director.py)
├── _sanitize.py                 # existing pattern — shared prose scrubber
└── auto_detect/                 # was auto_detect.py; becomes a subpackage
    ├── __init__.py              # re-exports detect_preset + PresetRecommendation
    ├── metadata.py              # Tier 0 scorer
    ├── structure.py             # Tier 1 scorer
    ├── cue_vocab.py             # Tier 2 scorer
    ├── opening.py               # Tier 3 (opening-sentence LLM)
    └── scoring.py               # merge + confidence math
```

- **Public surface unchanged.** `cutmaster.analysis.auto_detect.detect_preset(transcript, run_state=None) -> PresetRecommendation` — callers keep the same import path because the subpackage re-exports from `__init__.py`.
- **`_sentences.py` is a prerequisite.** The sentence coalescer currently lives in [`core/director.py`](../../src/cutmaster_ai/cutmaster/core/director.py); moving it to `analysis/_sentences.py` removes the `analysis → core` dependency the current auto-detect creates (layering violation).
- **`_sanitize.py` is the pattern reference.** Shared prose scrubber already lives there — this PR adds a second helper module following the same shape.

## Phased implementation

Each phase ships independently, each reduces LLM calls by a measurable amount. Tick each step `[x]` as it lands; keep unchecked `[ ]` items in the proposal so the tracker stays honest.

### Phase 1 — Prerequisites + Tiers 1 + 2 (structure + cue vocabulary)

**Status:** landed (uncommitted — all sub-steps complete, 15 cascade tests + suite green, ruff clean).
**Estimated code:** ~300 lines across the subpackage + ~50 lines of scoring test fixtures (plus the sentence-coalescer move in step 1.0, which is a pure rename).
**Expected effect:** 50-70 % of runs skip the LLM on clearly-typed content. Cost per analyze drops; classifier latency drops from ~5 s to ~20 ms on the fast path.

Implementation steps:

- [x] **1.0** Extract the sentence coalescer to a shared module to remove the `analysis → core` dependency before auto-detect imports it.
  - Move `_coalesce_to_sentences`, `_sentence_spans`, `_sentence_edge_times`, `_has_reliable_punctuation`, `_word_ends_sentence`, `SENTENCE_PAUSE_FALLBACK_S`, `_SENTENCE_PUNCT` from [`core/director.py`](../../src/cutmaster_ai/cutmaster/core/director.py) into `cutmaster/analysis/_sentences.py`.
  - `core/director.py` re-imports the public names — no behaviour change; the 791 existing tests stay green.
- [x] **1.1** Convert `auto_detect.py` to `auto_detect/` subpackage:
  - `git mv` the file to `auto_detect/__init__.py`.
  - `__init__.py` keeps `detect_preset` + `PresetRecommendation` as the public surface; add a top-of-file re-export comment so the callers' import path (`from cutmaster.analysis.auto_detect import detect_preset`) keeps working verbatim.
- [x] **1.2** Persist scrubber `counts` on run state. Scrubber already returns `counts = {"filler": n, "dead_air": n, "restart": n}` ([scrubber.py:52](../../src/cutmaster_ai/cutmaster/analysis/scrubber.py#L52)) but the pipeline discards it. One-line change in [`pipeline.py`](../../src/cutmaster_ai/cutmaster/core/pipeline.py) after the scrub stage: `run["scrub_counts"] = result.counts`. Makes `filler_rate`, `restart_rate`, `dead_air_rate` computable in Tier 1 without re-running the scrubber.
- [x] **1.3** Add `auto_detect/scoring.py` — `PresetScores` type alias (`dict[str, float]`), `merge(scores_by_tier, weights) → PresetScores`, `top_n(scores, n)`, `margin_to_confidence(margin) → float`.
- [x] **1.4** Add `auto_detect/structure.py` — `score_by_transcript_structure(transcript, scrub_counts=None) → PresetScores` computing all 12 Tier 1 signals. Apply the degradation rules from the Tier 1 section: a signal whose source field is uniformly absent contributes neutral 0 rather than failing.
- [x] **1.5** Add `auto_detect/cue_vocab.py` — compute distinctiveness weights at import time (`1 / preset_count_containing_cue`), expose `score_by_cue_vocabulary(transcript) → PresetScores`. Matching is case-insensitive and word-boundary-aware (a preset's `"step one"` must match `"step one,"` and `"Step One."` but not `"step one"` embedded in `"misstepone"`).
- [x] **1.6** Rewrite `detect_preset` in `auto_detect/__init__.py` as the cascade orchestrator; keep the public signature (new optional `run_state` param is fine — callers passing only `transcript` still work). Fold the existing speaker-count heuristic in as a Tier 1 signal — don't keep it as a separate branch.
- [x] **1.7** Cache the signals on run state as `run["autodetect_signals"] = {...}` (keyed by the preset key so cascade re-entry is instant; follows the [themes_cache pattern](../../src/cutmaster_ai/http/routes/cutmaster/presets.py#L91)).
- [x] **1.8** Add unit tests: one transcript fixture per preset the cascade should resolve without the LLM (interview, podcast, presentation, tutorial at minimum). Assert `confidence ≥ 0.85` and that the LLM client is not invoked (monkey-patch `llm.call_structured` to raise).
- [x] **1.9** Regression tests: the existing two autodetect tests must still pass against the cascade.
- [x] **1.10** Run `uv run pytest tests/ -q` and `uv run ruff check src/` clean.

### Phase 2 — Tier 0 metadata + signals surfaced to LLM

**Status:** landed (uncommitted — all sub-steps complete, 21 cascade tests + suite green, ruff clean).
**Estimated code:** ~100 lines + prompt change.
**Expected effect:** another 10-20 % of previously-ambiguous runs resolve. Tier 4 accuracy improves because the task is narrower.

Implementation steps:

- [x] **2.0** Persist timeline metadata on run state during analyze. [`pipeline.py::_vfr_check`](../../src/cutmaster_ai/cutmaster/core/pipeline.py#L41) already calls `tl.GetItemListInTrack(...)`; extend the stage to also write:
  - `run["source_meta"] = {"clip_count": int, "fps": float, "width": int, "height": int, "aspect": float}`
  - Prefer reading from the existing [`/source-aspect/{run_id}` helper](../../src/cutmaster_ai/http/routes/cutmaster/info.py#L21) for `width/height/aspect` so there's one source of truth. Refactor that endpoint to read from `run["source_meta"]` instead of hitting Resolve every request (free performance win).
- [x] **2.1** Thread `run_state` *(landed in Phase 1 step 1.6 — detect_preset already accepts run_state)* through `detect_preset`. The function already accepts `run_state: dict | None = None` from step 1.6 — this step is the caller change.
- [x] **2.2** *(landed in Phase 1 — route already passes run into detect_preset)*. One-line fix in [`presets.py::detect_preset`](../../src/cutmaster_ai/http/routes/cutmaster/presets.py#L60): `_require_scrubbed` already returns `(run, scrubbed)`; the route currently drops `run`. Replace `_, scrubbed = _require_scrubbed(body.run_id)` with `run, scrubbed = _require_scrubbed(body.run_id)` and pass `run` into `detect_preset(scrubbed, run_state=run)`.
- [x] **2.3** Add `auto_detect/metadata.py` — `score_by_metadata(run_state) → PresetScores` using `clip_count`, `aspect`, `fps`, and `total_duration_s` (derived from `run["scrubbed"][-1]["end_time"]` when not present in `source_meta`).
- [x] **2.4** Extend the cascade in `auto_detect/__init__.py` to call `score_by_metadata` as Tier 0 and merge with Tier 1-2. When `run_state` is `None`, Tier 0 contributes zeros — cascade still works (this preserves backwards compatibility for any direct caller).
- [x] **2.5** When Tier 4 (full-band LLM) runs, include a `SIGNALS SUMMARY` block in the prompt listing the top-3 preset scores from Tier 0-2 with a one-line rationale ("tutorial scored 0.72 on cue overlap and 0.58 on structure"). Forces the model to justify its pick against objective evidence.
- [x] **2.6** *(landed in Phase 1 — `_llm_classify` already receives narrowed top-3 candidates).* Narrow Tier 4's candidate list to the cascade's top 3 (don't re-expose all 11 presets to the model).
- [x] **2.7** Tests: add a fixture where Tier 0 metadata alone disambiguates (e.g. 9:16 aspect rules out interview/presentation; > 30 clips rules out raw-capture interview).

### Phase 3 — Tier 3 opening-sentence micro-classifier

**Status:** pending.
**Estimated code:** ~60 lines.
**Expected effect:** resolves rhetorical-opener cases the structural signals miss ("welcome back", "thank you for having me").

Implementation steps:

- [ ] **3.1** Add `auto_detect/opening.py` — `classify_opening_sentence(sentence) → PresetScores` calling [`intelligence.llm.call_structured`](../../src/cutmaster_ai/intelligence/llm.py) with:
  - A minimal Pydantic response schema (`OpeningClassification` with `preset: Preset` and `confidence: float`).
  - A ~200-token prompt showing only the single coalesced opening sentence.
  - `validate=` callback that rejects `preset` values outside the auto-eligible set (no `tightener`/`clip_hunter`/`short_generator`) and `accept_best_effort=True` so a malformed response degrades to neutral scores rather than erroring.
- [ ] **3.2** Gate the call: only runs when the Tier 0-2 margin is in `[0.1, 0.25]`. Cascade skips Tier 3 entirely outside that band (high margin → confident without it; very low margin → defer to Tier 4 which sees more context).
- [ ] **3.3** Mock `llm.call_structured` in the unit tests so this stays deterministic. Add a fixture where Tier 0-2 produce a tie between `vlog` and `tutorial` and assert the Tier 3 mock output breaks the tie correctly.

### Phase 4 — Calibration + telemetry

**Status:** pending.
**Estimated code:** ~100 lines + fixture set.
**Expected effect:** weights move from hand-tuned guesses to evidence-backed values; the Configure screen can show why a pick was made.

Implementation steps:

- [ ] **4.1** Add a `signals` field to `PresetRecommendation` (backend + TS types) carrying per-tier top-3 score vectors.
- [ ] **4.2** Log a structured `autodetect.cascade` entry per run: top-1 / top-2 preset, margin, LLM tiers invoked, elapsed_ms.
- [ ] **4.3** Build a labeled fixture set at `tests/cutmaster/fixtures/autodetect/` — 10-20 transcripts covering every preset. Source: trim real runs the user has corrected.
- [ ] **4.4** Add `tests/cutmaster/test_autodetect_fixtures.py` asserting each fixture resolves to the expected preset with `confidence ≥ 0.7`.
- [ ] **4.5** Tune tier weights in `scoring.py` constants against the fixture set until all pass.
- [ ] **4.6** Document the fixture-labeling workflow in a short `docs/autodetect-fixtures.md` so future corrections are easy to add.

**Not in scope for this phase:** automatic weight learning. Weights stay hand-tuned until there's enough telemetry to justify a solver pass.

### Phase 5 — Tier 5 vision (deferred)

**Status:** deferred — only build if Phase 4 telemetry shows Tier 4 still misclassifying on cases a frame would disambiguate (e.g. product_demo vs tutorial).

Implementation steps (placeholder — revisit before starting):

- [ ] **5.1** Identify the failure modes from Phase 4 telemetry. Only proceed if ≥ 5 % of classifications would flip given a frame signal.
- [ ] **5.2** Sample 2-3 frames (start, middle, near-end) via `media.ffmpeg_frames`.
- [ ] **5.3** Send through `intelligence.llm` vision path with a narrow schema: `{stage_talk | talking_head | ui_demo | handheld_outdoor | other}`.
- [ ] **5.4** Fold the vision label into Tier 5 scores and merge with cascade.
- [ ] **5.5** Gate behind `CUTMASTER_AUTODETECT_VISION=1` until the failure-mode baseline justifies always-on.

## Open questions

1. **Where does `run_state` get threaded in?** `detect_preset` is called from [presets route](../../src/cutmaster_ai/http/routes/cutmaster/presets.py) which already has the run loaded. Low-risk to add.
2. **Fixture set.** We don't have labeled transcripts to calibrate against. Minimum viable: ask users to correct any misclassifications on first runs and bank their corrections as labels.
3. **Cue distinctiveness weights** — computed once from `PRESETS` at module import, or recomputed if presets change? Probably module-level constant, regenerated by a pre-commit hook. Not a blocker.
4. **Telemetry destination.** No telemetry infra yet. Initial version logs to the existing run-log stream and we eyeball.
5. **How to handle Tightener / Clip Hunter / Short Generator.** These are mode/workflow presets, not content types. Exclude from the cascade (as today) — they surface via UI buttons, not auto-detect.

## Out of scope

- Vision classifier (Tier 5). Reserved for a later phase once we see Tier 4 failure modes.
- Automatic target-length tuning from editor history. Current `suggested_target_length_s` formula is good enough; history-based tuning is a different feature.
- Multi-language heuristics. Current cue vocab is English-only. Internationalisation is a separate proposal.
- Replacing the panel's "auto" button. The UX surface stays the same; this is a backend-only refactor.

## Success criteria

- **LLM calls per autodetect:** current 100 % → target ≤ 40 % after Phase 1, ≤ 25 % after Phase 3.
- **Median autodetect latency:** current ~5 s → target ≤ 500 ms on the fast path (Tiers 0-2 only, no network).
- **Misclassification rate** on a labeled fixture set: establish baseline before Phase 1, ≤ half of that after Phase 3.
- **Deepgram-signal utility:** each Tier 1 signal's discriminative power is logged (per-signal contribution to the top-1 preset's score) so we can cull signals that turn out to be noise. A signal that never swings a decision is a signal we shouldn't be computing.
- **No regressions on the six presets the current implementation already handles correctly** — catch with regression tests pinned to labeled fixtures.

## Rollout

All phases are additive — the existing `detect_preset(transcript) -> PresetRecommendation` signature stays. No panel changes needed for Phases 1-3 (the UI already consumes `suggested_target_length_s` and `alternatives`; richer `signals` data arrives in Phase 4).
