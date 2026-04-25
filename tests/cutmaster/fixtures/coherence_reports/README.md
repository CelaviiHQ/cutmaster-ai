# Coherence-report fixtures (story-critic Phase 0b)

Single-rater taste baseline for the story-critic (proposed in
[`Implementation/optimizaiton/story-critic.md`](../../../../Implementation/optimizaiton/story-critic.md)).

**This is one editor's preferences, not "ground truth."** The critic's
calibration measures fit-to-rater, not absolute quality. If the user
base grows past one editor, capture additional baselines.

## Layout

```
inputs/
    {run_id}.fixture.json   # plan + transcript + resolved_axes per cut

real_corrections/
    {run_id}.fixture.json   # cuts that were flagged broken in production
    {run_id}.notes.md       # editor's notes on what was actually wrong

human_baselines.json        # per-fixture scores (overall + 4 sub-scores + top issues)
human_baselines.template.json   # the shape; copy to human_baselines.json and fill in
```

## Workflow

### Phase 0b.1 — capture 10 fixtures

Pick **10 builds from the past month** spanning ≥4 content types and ≥3
cut intents. Run the dumper script:

```bash
# List candidate runs (only ones with persisted plans)
uv run python scripts/coherence_baseline_capture.py list

# Dump a chosen run
uv run python scripts/coherence_baseline_capture.py capture <run_id>
```

The dumper writes `inputs/{run_id}.fixture.json` with `{plan,
scrubbed_transcript, resolved_axes, run_meta}`. Pre-9bf8e73 plans
(without `arc_role` on segments) are accepted — the critic adapter
handles `arc_role=None` gracefully (Phase 1.5).

### Phase 0b.2 — score them

Open `human_baselines.template.json`, copy to `human_baselines.json`,
add one entry per fixture. **Time-box to ~15 min/cut, ~1.5 h total.**

For each cut, score on the 0–100 scale:

| Field | Question to ask |
|---|---|
| `score` | Overall — would you ship this cut as-is? 100 = yes, 0 = unwatchable. |
| `hook` | Does the opening segment pull you forward? |
| `arc` | Is there a discernible setup → resolve shape? |
| `transitions` | Do successive segments connect, or is it whiplash? |
| `resolution` | Does the closing segment land the through-line? |

Then list **1–3 specific concerns** in plain prose under `top_issues`.
Be concrete: "rambling intro doesn't preview the punchline" beats
"weak hook." The critic's matching against your concept-language is
what Phase 5.2.1 measures.

### Phase 0b.3 — own the limitation

`human_baselines.json` is one editor's taste. Calibration in Phase 5
treats it as the rater the system is built for, not as ground truth.
This is documented in §"Open questions" #6 of the proposal — read it
before treating any number from this fixture set as objective.

## Fixture file shape (inputs/)

```json
{
  "run_id": "abc123",
  "captured_at": "2026-04-25T14:50:00Z",
  "run_meta": {
    "timeline_name": "AIENG_AI_Cut",
    "preset": "presentation",
    "duration_s": 2640.0,
    "created_at": "2026-04-22T09:12:00Z"
  },
  "resolved_axes": {
    "content_type": "presentation",
    "cut_intent": "multi_clip",
    "...": "..."
  },
  "plan": {
    "kind": "director",
    "director": { "selected_clips": [...], "hook_index": 0, "reasoning": "..." },
    "markers": { "markers": [...] },
    "...": "..."
  },
  "scrubbed_transcript": [
    {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    "..."
  ]
}
```

The `kind` field tags which plan shape this fixture exercises — so the
critic dispatches to the right adapter (`director` → `CoherenceReport`,
`clip_hunter` / `short_generator` → `PerCandidateCoherenceReport`).

## When fixtures fail

If running Phase 5.2 reveals the critic disagrees with you on most of
this set, the workflow is in
[`docs/story-critic-calibration.md`](../../../../docs/story-critic-calibration.md)
(landed in Phase 5.7). Don't change the fixtures to match the critic
— change the prompt or the model.
