# Prompt baselines — Phase 0.6

Golden prompt strings captured from the six Director builders against
current `main` **before** the Phase 3 rewrite (three-axis-model).

## Contents

Twelve `.txt` files, one per (builder × content-type) pair:

| Builder            | Content types captured |
|--------------------|------------------------|
| `flat`             | `vlog`, `interview`    |
| `assembled`        | `vlog`, `interview`    |
| `clip_hunter`      | `vlog`, `interview`    |
| `short_generator`  | `vlog`, `interview`    |
| `curated`          | `vlog`, `interview`    |
| `rough_cut`        | `vlog`, `interview`    |

## Usage

- Phase 3 snapshot tests assert flag-off output matches these baselines
  byte-for-byte.
- Phase 3 token-budget regression compares flag-on output length to the
  corresponding baseline (±5% bound).
- After intentional prompt changes, re-run the capture script and
  commit the diff **in the same commit** as the wording change.

## Re-capture

```bash
uv run python tests/cutmaster/fixtures/prompt_baselines/_capture.py
```

The script uses a fixed short transcript + fixed takes + fixed groups
so re-runs are deterministic.
