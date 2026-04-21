# Auto-detect fixture labeling

The cascade's weight tuning is bounded by the size + quality of the
labeled fixture set at
[`tests/cutmaster/fixtures/autodetect/`](../tests/cutmaster/fixtures/autodetect/).
Every real-world misclassification an editor corrects is a free
calibration signal — this doc is how we bank them.

## Fixture file shape

```json
{
  "label": "interview",
  "source": "real-corrected-2026-04-21",
  "notes": "one-line rationale",
  "run_state": {
    "source_meta": {"clip_count": 1, "fps": 30, "aspect": 1.78},
    "scrub_counts": {"filler": 4, "restart": 1, "dead_air": 0, "original": 620}
  },
  "transcript": [
    {"word": "Welcome.", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    ...
  ]
}
```

- `label` — the correct content-type preset. Must not be a mode preset
  (`tightener` / `clip_hunter` / `short_generator`).
- `source` — `synthetic-v1` for generated fixtures, `real-corrected-YYYY-MM-DD`
  for editor-banked ones, `clip:<absolute-path>` when the transcript came
  straight from a named project.
- `notes` — one-line human summary of why this fixture is informative.
- `run_state.source_meta` — drives Tier 0 (metadata). Leave off when the
  fixture is transcript-only.
- `run_state.scrub_counts` — lets Tier 1's filler/restart/dead-air
  signals fire. Grab from `run["scrub_counts"]` (persisted by
  [`pipeline.py::_scrub_stage`](../src/cutmaster_ai/cutmaster/core/pipeline.py)).
- `transcript` — the Deepgram word list. Trim to a representative
  ~10-minute window so CI stays fast.

## Banking a correction

1. Editor opens Configure, runs auto-detect, then flips the preset to
   the correct one. (Flagging misclassifications is the Configure
   screen's job.)
2. Grab the run state:
   ```bash
   cat ~/CutMasterRuns/<run_id>.json | jq '{scrubbed, source_meta, scrub_counts}' > /tmp/run.json
   ```
3. Trim the scrubbed transcript to a 5-10 min window that's
   representative of the editor's correction (usually the first few
   minutes are enough — openers carry a lot of the structural signal).
4. Assemble the JSON fixture (above shape). The `label` is the preset
   the editor picked, not what the cascade guessed.
5. Place it under
   `tests/cutmaster/fixtures/autodetect/<label>/real_NNN.json` where
   `NNN` increments per label.
6. Run `uv run pytest tests/cutmaster/test_autodetect_fixtures.py -v`
   — the new fixture is auto-discovered. If it fails, that's the
   bug we wanted to catch; ship the fix alongside the fixture.

## Running the fixture suite

```bash
uv run pytest tests/cutmaster/test_autodetect_fixtures.py -v
```

Parametrized per fixture. LLM calls are stubbed to fail so the suite
stays offline + deterministic — a fixture that depends on Tier 4 to
classify correctly is a bad fixture.

`test_fixture_coverage_report` skips with a list of presets that have
zero fixtures — use it as a running TODO of what to bank next.

## Coverage target

At least two fixtures per classifiable preset, ideally mixing synthetic
(reproducible from test builders) and real-corrected (representative
of actual editor usage).

## Weight tuning (Phase 4.5)

The cascade's per-tier weights in
[`auto_detect/scoring.py`](../src/cutmaster_ai/cutmaster/analysis/auto_detect/scoring.py)
are hand-tuned at
`DEFAULT_WEIGHTS = (0.15, 0.35, 0.25, 0.25)`. Phase 4.5 of the
[autodetect-cascade proposal](../Implementation/optimizaiton/autodetect-cascade.md)
reserves space for a calibration pass against these fixtures — **not
started** until the fixture set reaches ~5 real-corrected transcripts
per preset. Tuning on synthetic-only fixtures would overfit to the
test builders.
