"""Tests for ``sensory_resolution`` structured-log telemetry.

The build endpoint emits one ``sensory_resolution`` line per build with
the inputs (master + overrides + axis pair) and the resolved triple.
Aggregators key on ``extra["event"]`` to filter the firehose.
"""

from __future__ import annotations

import logging

import pytest

from cutmaster_ai.http.routes.cutmaster._sensory_gates import log_sensory_resolution


def test_log_sensory_resolution_emits_expected_fields(caplog):
    settings = {
        "sensory_master_enabled": True,
        "layer_c_enabled": False,
        "layer_a_enabled": None,
        "layer_audio_enabled": None,
    }
    with caplog.at_level(logging.INFO, logger="cutmaster-ai.http.cutmaster"):
        log_sensory_resolution(
            "run-abc",
            settings,
            cut_intent="narrative",
            timeline_mode="raw_dump",
        )

    records = [r for r in caplog.records if getattr(r, "event", None) == "sensory_resolution"]
    assert len(records) == 1, "expected exactly one sensory_resolution record"
    rec = records[0]

    # Top-level identifiers and axis pair.
    assert rec.run_id == "run-abc"
    assert rec.cut_intent == "narrative"
    assert rec.timeline_mode == "raw_dump"
    assert rec.matrix_row == "raw_dump"
    assert rec.master is True

    # Override echo — explicit ``False`` must reach the log so aggregators
    # can distinguish "force off" from "follow matrix" (None).
    assert rec.overrides == {"c": False, "a": None, "audio": None}

    # raw_dump row: c=default, a=default, audio=opt_in. Override forces
    # c=False; a follows default+master=True → True; audio opt_in stays
    # off without an explicit override.
    assert rec.resolved == {"c": False, "a": True, "audio": False}


def test_log_sensory_resolution_master_off_resolves_all_false(caplog):
    """Master off + no overrides → every layer off, regardless of row."""
    settings = {
        "sensory_master_enabled": False,
        "layer_c_enabled": None,
        "layer_a_enabled": None,
        "layer_audio_enabled": None,
    }
    with caplog.at_level(logging.INFO, logger="cutmaster-ai.http.cutmaster"):
        log_sensory_resolution(
            "run-xyz",
            settings,
            cut_intent="assembled_short",
            timeline_mode="raw_dump",
        )

    records = [r for r in caplog.records if getattr(r, "event", None) == "sensory_resolution"]
    assert records[-1].resolved == {"c": False, "a": False, "audio": False}
    # Multi-candidate preset still picks its row even when master is off.
    assert records[-1].matrix_row == "short_generator"


@pytest.mark.parametrize(
    "cut_intent,timeline_mode,expected_row",
    [
        ("multi_clip", "raw_dump", "clip_hunter"),
        ("surgical_tighten", "assembled", "assembled"),
        ("narrative", "rough_cut", "rough_cut"),
    ],
)
def test_matrix_row_field_tracks_axes_collapse(caplog, cut_intent, timeline_mode, expected_row):
    """``matrix_row`` mirrors ``axes_to_sensory_key`` so log queries can
    pivot on the row directly without recomputing the collapse."""
    settings = {"sensory_master_enabled": True}
    with caplog.at_level(logging.INFO, logger="cutmaster-ai.http.cutmaster"):
        log_sensory_resolution(
            "run-1", settings, cut_intent=cut_intent, timeline_mode=timeline_mode
        )
    records = [r for r in caplog.records if getattr(r, "event", None) == "sensory_resolution"]
    assert records[-1].matrix_row == expected_row
