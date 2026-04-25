# Axis-resolution fixtures + calibration workflow

The axis resolver
([`axis_resolution.py`](../src/cutmaster_ai/cutmaster/data/axis_resolution.py))
turns four user-facing axes (content type, cut intent, duration,
timeline mode) into a fully-resolved cut recipe. Two parts of that
recipe are **discrete** (matrix cells: reorder mode, selection
strategy, prompt builder) and one part is **continuous** (segment
pacing min/target/max).

The discrete decisions are pinned by labelled fixtures
([`tests/cutmaster/fixtures/axis_resolution/`](../tests/cutmaster/fixtures/axis_resolution/));
the continuous pacing curve gets re-fit as real corrections accumulate.

## What a fixture looks like

See [the fixture README](../tests/cutmaster/fixtures/axis_resolution/README.md)
for the JSON schema. Every fixture is `(input axes, expected resolver
output)` — the test harness asserts the resolver hits the labelled
output exactly for the discrete fields.

## Fixture sources

| Source value | Meaning | When to use |
|---|---|---|
| `synthetic-v1` | Hand-written from the design doc / matrix | Phase 6 starter set; replaced over time |
| `real-corrected-YYYY-MM-DD` | Captured from an editor flipping the resolver's pick on the Configure screen | Preferred — represents real distribution |
| `clip:<absolute-path>` | Sourced from a specific reference clip on disk | Reproducibility for in-repo media |

Synthetic fixtures validate the resolver against our own assumptions.
Real corrections validate it against actual editor distribution. Phase
6 ships 15 synthetic; the calibration loop replaces them as real ones
arrive.

## Capturing a real correction

1. Editor opens the Configure screen on a finished run.
2. Editor either:
   - flips Axis 2 from Auto to a different value, or
   - changes the resolved chip's reorder mode to something the matrix
     wouldn't have produced.
3. Capture the run state:
   ```bash
   python -m cutmaster_ai.cli dump-run <run_id> > /tmp/run.json
   ```
   (Or copy `~/CutMasterRuns/<run_id>.json` directly.)
4. Strip everything except the seven resolver inputs + the editor's
   correct expected output. Use the README schema verbatim.
5. `source` = `real-corrected-2026-04-25` (today's ISO date).
6. `notes` = one sentence on why the editor changed it. Keep it short
   — the fixture file is the artefact, not the prose.
7. Save under
   `tests/cutmaster/fixtures/axis_resolution/<NN>_<descriptor>.json`
   with the next sequential prefix.

## Calibration loop (Phase 6.6 — blocked pending real fixtures)

The pacing formula in
[`resolve_pacing`](../src/cutmaster_ai/cutmaster/data/axis_resolution.py)
is `target = base × pacing_modifier × duration_factor` where
`duration_factor = clamp(0.8, (duration_s / 180)^0.15, 1.1)`. The
constants `(0.8, 180, 0.15, 1.1)` are provisional — they reproduce
the design-doc reference points within ±25% but are not fit against
editor data.

Calibration kicks off when there are **≥10 real-corrected fixtures**
spanning at least 5 distinct content types (any fewer and we risk
overfitting the curve to a narrow distribution). The process:

1. **Capture corrections.** Editor flags a pacing-driven outlier on
   Configure — segments that came back too short / too long. Save the
   correction as a fixture with the editor's preferred
   `pacing.target` carried in `notes` (not `expected`, which would
   freeze it).
2. **Score current curve.** Run the existing curve against every real
   fixture, compute (predicted target − preferred target) / preferred,
   and report mean / max / per-content-type breakdown.
3. **Identify outliers.** Fixtures where the prediction error exceeds
   ±30% are candidates for curve adjustment.
4. **Refit.** Try alternative shapes — piecewise linear, logistic,
   per-content-type modifiers. The simplest function that brings every
   fixture inside ±15% wins.
5. **Re-snapshot.** Update the constants in `resolve_pacing`; the
   discrete fixtures stay green (calibration only moves continuous
   numbers); design-doc reference tests in
   [`test_axis_resolution.py`](../tests/cutmaster/test_axis_resolution.py)
   tighten as confidence grows.

## When fixtures fail

A failing fixture means one of three things:

1. **Resolver regression.** Something in
   `axis_resolution.py` changed the behaviour of an established cell.
   Fix the resolver — fixtures are the spec.
2. **Matrix update needed.** A new content type / cut intent pair was
   added; existing fixtures don't cover it; you intend the resolver
   to produce a new value. Update the fixture's `expected` block in
   the same commit as the resolver change. Reviewer should see both.
3. **Calibration drift.** Pacing constants moved and a discrete
   fixture happened to depend on them. This shouldn't happen — pacing
   isn't asserted in fixtures. If it does, the fixture is overspecified;
   trim it.

## Cross-references

- [Axis-resolution unit tests](../tests/cutmaster/test_axis_resolution.py)
  — design-doc-pinned pacing examples + edge cases.
- [Sensory-layer fixtures](../tests/cutmaster/test_sensory_layers_by_axes.py)
  — Phase 6.1 byte-parity coverage for the matrix relocation.
- [Three-axis design doc §6](THREE_AXIS_MODEL.md#6-axis-2-auto-resolution)
  — duration-band auto-resolution rules (the source of truth for
  fixtures 9–12).
