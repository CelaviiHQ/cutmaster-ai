# Telemetry — structured log events

CutMaster AI emits structured log events at key decision points so log
aggregators (Loki, Datadog, plain `jq` over a file) can trend behaviour
without code changes. Each event is a `log.info(<event_name>, extra={...})`
call where `extra["event"]` matches the message — that double-naming
lets queries either grep the message or filter on the field, whichever
the sink prefers.

This file is the source of truth for **what events exist, when they
fire, and what fields they carry.** Adding a new structured event?
Append a section here in the same commit.

## Conventions

- **Logger name:** `cutmaster-ai.http.cutmaster` for HTTP-layer events;
  `cutmaster-ai.<module>` for pipeline-internal events.
- **Identifiers:** every event carries `run_id` so a single build's
  events can be joined with a one-field filter.
- **Naming:** `<domain>.<noun>` or `<domain>_<noun>` — pick one per
  domain and stay consistent. `axis_resolution.decided` and
  `sensory_resolution` are the established forms.
- **Cardinality:** one line per build, not one per gate-call. If you're
  tempted to log inside a helper that runs N times, lift the call up.

## Events

### `axis_resolution.decided`

Fires once per build, immediately after `pipeline.stash_resolved_axes`
runs. Captures the full three-axis recipe (content type, cut intent,
pacing, selection strategy) so log aggregators can trend cut-intent
provenance and pacing-curve outliers.

| Field | Type | Notes |
|---|---|---|
| `event` | `"axis_resolution.decided"` | message duplicate |
| `run_id` | `str` | join key |
| `content_type` | `str` | resolved content type |
| `cut_intent` | `str` | resolved cut intent |
| `cut_intent_source` | `"user" \| "auto" \| "forced"` | provenance |
| `duration_s` | `float` | rounded to 0.01s |
| `num_clips` | `int \| None` | from `UserSettings` |
| `timeline_mode` | `str` | post-normalisation mode |
| `reorder_mode` | `str` | resolved reorder strategy |
| `pacing_target_s` / `pacing_min_s` / `pacing_max_s` | `float` | segment pacing curve |
| `selection_strategy` | `str` | resolved strategy |
| `prompt_builder` | `str` | which Director prompt builder runs |
| `rationale` | `str` | one-line natural-language summary |
| `unusual` | `bool` | flagged when the axis combo is rare |

Source: [build.py](../src/cutmaster_ai/http/routes/cutmaster/build.py)
near `_effective_content_type` / `pipeline.stash_resolved_axes`.

### `sensory_resolution`

Fires once per build, immediately after `axis_resolution.decided`.
Captures master toggle + per-layer overrides + the resolved triple so
matrix-tuning queries can trend (a) which `SENSORY_MATRIX` rows fire
most, (b) override-vs-default split per layer, (c) divergence between
panel-supplied overrides and the matrix default.

| Field | Type | Notes |
|---|---|---|
| `event` | `"sensory_resolution"` | message duplicate |
| `run_id` | `str` | join key |
| `cut_intent` | `str` | matches `axis_resolution.decided` |
| `timeline_mode` | `str` | matches `axis_resolution.decided` |
| `matrix_row` | `str` | output of `axes_to_sensory_key(cut_intent, timeline_mode)` |
| `master` | `bool` | `sensory_master_enabled` |
| `overrides` | `{c, a, audio: bool \| None}` | raw panel/API values; `None` means "follow matrix" |
| `resolved` | `{c, a, audio: bool}` | final per-layer enabled flags after resolver |

Source: [_sensory_gates.py:log_sensory_resolution](../src/cutmaster_ai/http/routes/cutmaster/_sensory_gates.py).

**Joining the two events:** `axis_resolution.decided` and
`sensory_resolution` share `run_id`, `cut_intent`, `timeline_mode`. A
single query keyed on `run_id` returns the full per-build recipe.

## Sample queries

```bash
# Drop into your dev log file and pivot.
rg "sensory_resolution" /tmp/cutmaster.log | head

# Most-fired matrix rows over the last N runs:
rg -o '"matrix_row":\s*"[^"]+"' /tmp/cutmaster.log | sort | uniq -c | sort -rn

# Builds where the panel overrode a Layer-C default (off → on):
rg "sensory_resolution" /tmp/cutmaster.log | jq 'select(.overrides.c == true and .resolved.c == true)'
```
