# Auto-detect labeled fixtures

Each ``.json`` file is a labeled transcript the cascade is expected to
classify correctly. Format::

    {
      "label": "interview",        # content-type preset key (never a mode preset)
      "source": "synthetic-v1 | real-corrected-YYYY-MM-DD | clip:<path>",
      "notes": "…free-text rationale (optional)…",
      "run_state": {                # optional — Tier 0 lifts confidence
        "source_meta": {"clip_count": 1, "fps": 30, "aspect": 1.78},
        "scrub_counts": {"filler": 4, "restart": 1, "dead_air": 0, "original": 620}
      },
      "transcript": [
        {"word": "Welcome.", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        …
      ]
    }

## Adding real corrections

Phase 4's calibration loop depends on the fixture set growing over
time. The short version:

1. Editor runs auto-detect and flags a misclassification (or confirms a
   borderline pick) via the Configure screen.
2. Dump the scrubbed transcript with ``python -m cutmaster_ai.cli dump-run <run_id>``
   (or copy from the Resolve run state at ``~/CutMasterRuns/<run_id>.json``).
3. Trim to a representative ~10-minute window to keep CI fast.
4. Set ``label`` to the editor's correct choice; ``source`` to
   ``real-corrected-YYYY-MM-DD``; add a one-line ``notes`` rationale.
5. Add the file under ``tests/cutmaster/fixtures/autodetect/<label>/``.

The fixture test (``test_autodetect_fixtures.py``) discovers every
``.json`` in this tree — no registration needed.

## Coverage target

At least two fixtures per classifiable preset, ideally a mix of
synthetic (reproducible) and real-corrected (representative). Missing
presets are listed in the test report so gaps are visible.

## Weight tuning

``auto_detect/scoring.py`` currently carries hand-tuned weights. Once
the fixture set crosses ~5 real-corrected transcripts per preset, the
tuning loop in Phase 4.5 can run against them (proposal:
``Implementation/optimizaiton/autodetect-cascade.md``).
