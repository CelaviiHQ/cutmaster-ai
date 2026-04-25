# Axis-resolution labelled fixtures

Each `.json` file is a labelled (input, expected) pair the resolver
must produce. Every fixture under this tree is discovered automatically
by `tests/cutmaster/test_axis_fixtures.py` — no registration step.

## Format

```json
{
  "label": "Human-readable description shown in test failure messages",
  "source": "synthetic-v1 | real-corrected-YYYY-MM-DD | clip:<path>",
  "notes": "free-text rationale for why this fixture exists (optional)",
  "input": {
    "content_type": "vlog",
    "cut_intent": "narrative",          // or null for auto-resolution
    "duration_s": 300,
    "timeline_mode": "raw_dump",
    "num_clips": 1,
    "reorder_allowed": true,
    "takes_already_scrubbed": false
  },
  "expected": {
    "content_type": "vlog",
    "cut_intent": "narrative",
    "cut_intent_source": "user",        // user | auto | forced
    "reorder_mode": "preserve_macro",
    "selection_strategy": "narrative-arc",
    "prompt_builder": "_prompt",
    "unusual": false
  }
}
```

The fixture test asserts every key in `expected` matches the resolver
output exactly. Pacing constants are deliberately **not** in the
fixture set — they're calibration targets for Phase 6.6 against real
corrected runs and are covered by separate tests in
`test_axis_resolution.py`.

## Coverage strategy (Phase 6.4 starter set)

The 15 fixtures shipped with Phase 6 split three ways:

| # | Bucket | Coverage |
|---|---|---|
| 1–8 | Nominal cells | One fixture per content type at a representative `(cut_intent, timeline_mode)` cell. Walks the matrix without overlapping. |
| 9–12 | Auto-resolution edges | Duration-band boundaries (44s, 90s, 120s, 60s wedding) — pin the duration-band table from §6 of the design doc. |
| 13–15 | Overrides + unusual | Tutorial × Multi-clip (the only `unusual=true` cell), num_clips-forced multi_clip, takes-already-scrubbed-forced surgical_tighten. |

## Adding real corrections

The whole point of the fixture set is to grow it from real editor
corrections — synthetic fixtures only validate our own assumptions.
The flow:

1. Editor flips Axis 2 from Auto to a different value (or marks the
   resolver's pick wrong) on the Configure screen.
2. Capture the run state via `python -m cutmaster_ai.cli dump-run <run_id>`
   (or copy `~/CutMasterRuns/<run_id>.json`).
3. Strip everything except the resolved-axes inputs (content_type,
   duration, timeline_mode, num_clips, reorder/scrubbed flags) and the
   editor's correct cut_intent + reorder_mode.
4. Set `source` to `real-corrected-YYYY-MM-DD`; add a one-line `notes`
   rationale.
5. Drop the file under this directory with a numeric prefix that keeps
   the discovery order stable.

## Calibration loop (Phase 6.6)

Once the corrected-run set hits ~10 fixtures, the `duration_factor`
constants in `axis_resolution.py::resolve_pacing` should be re-fit
against them. Process documented at
[`docs/axis-resolution-fixtures.md`](../../../../docs/axis-resolution-fixtures.md).
