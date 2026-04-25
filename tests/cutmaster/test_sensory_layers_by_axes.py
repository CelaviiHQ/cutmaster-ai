"""Tests for the axis-keyed sensory-layer resolver (Phase 6.1 + 6.2).

Two layers of coverage:

- ``axes_to_sensory_key`` collapses every (cut_intent, timeline_mode) pair
  onto the existing 6 SENSORY_MATRIX rows per §5 of the design doc.
- The legacy ``resolve_sensory_layers(preset=, timeline_mode=)`` delegates
  to ``resolve_sensory_layers_by_axes`` and returns byte-identical results
  for every (preset, mode) pair the production code passes today.
"""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.data.presets import (
    SENSORY_MATRIX,
    axes_to_sensory_key,
    resolve_sensory_layers,
    resolve_sensory_layers_by_axes,
    sensory_mode_key,
)

# ---------------------------------------------------------------- axes_to_key


@pytest.mark.parametrize(
    "cut_intent,timeline_mode,expected",
    [
        # multi_clip → clip_hunter row regardless of mode
        ("multi_clip", "raw_dump", "clip_hunter"),
        ("multi_clip", "rough_cut", "clip_hunter"),
        ("multi_clip", "curated", "clip_hunter"),
        # assembled_short → short_generator row regardless of mode
        ("assembled_short", "raw_dump", "short_generator"),
        ("assembled_short", "assembled", "short_generator"),
        # surgical_tighten only valid with assembled, but the function is
        # data-only — it returns "assembled" for any mode (axis_compat
        # blocks the invalid combos earlier in the request flow).
        ("surgical_tighten", "assembled", "assembled"),
        ("surgical_tighten", "raw_dump", "assembled"),
        # narrative × <mode> → mode's row
        ("narrative", "raw_dump", "raw_dump"),
        ("narrative", "rough_cut", "rough_cut"),
        ("narrative", "curated", "curated"),
        ("narrative", "assembled", "assembled"),
        # peak_highlight × * → raw_dump row
        ("peak_highlight", "raw_dump", "raw_dump"),
        ("peak_highlight", "assembled", "raw_dump"),
        ("peak_highlight", "curated", "raw_dump"),
    ],
)
def test_axes_to_sensory_key_mapping(cut_intent, timeline_mode, expected):
    assert axes_to_sensory_key(cut_intent, timeline_mode) == expected


def test_axes_to_sensory_key_unknown_mode_falls_through():
    """Unknown timeline_mode for narrative falls back to raw_dump."""
    assert axes_to_sensory_key("narrative", "made_up_mode") == "raw_dump"


def test_axes_to_sensory_key_unknown_intent_falls_through():
    """Unknown cut_intent (defensive) falls back to raw_dump."""
    assert axes_to_sensory_key("zzz_unknown", "raw_dump") == "raw_dump"


# --------------------------------------------------------- by_axes resolution


def test_by_axes_master_off_returns_all_false():
    """Master toggle off → every layer off unless explicitly overridden."""
    layers = resolve_sensory_layers_by_axes(
        master_enabled=False,
        c_override=None,
        a_override=None,
        audio_override=None,
        cut_intent="narrative",
        timeline_mode="raw_dump",
    )
    assert layers == (False, False, False)


def test_by_axes_master_on_returns_default_layers():
    """Master on → ``"default"`` layers fire; ``"opt_in"`` / ``"off"`` stay off."""
    # raw_dump row: c=default, a=default, audio=opt_in
    layers = resolve_sensory_layers_by_axes(
        master_enabled=True,
        c_override=None,
        a_override=None,
        audio_override=None,
        cut_intent="narrative",
        timeline_mode="raw_dump",
    )
    assert layers == (True, True, False)


def test_by_axes_explicit_override_beats_matrix():
    """Explicit override (True/False) wins over master + matrix level."""
    # assembled row: c=default, a=off, audio=default — but a_override=True
    # forces it on regardless of the "off" level.
    layers = resolve_sensory_layers_by_axes(
        master_enabled=True,
        c_override=False,
        a_override=True,
        audio_override=None,
        cut_intent="surgical_tighten",
        timeline_mode="assembled",
    )
    assert layers == (False, True, True)


def test_by_axes_clip_hunter_row_via_multi_clip():
    """multi_clip × any → clip_hunter row (c=default, a=off, audio=opt_in)."""
    layers = resolve_sensory_layers_by_axes(
        master_enabled=True,
        c_override=None,
        a_override=None,
        audio_override=None,
        cut_intent="multi_clip",
        timeline_mode="raw_dump",
    )
    assert layers == (True, False, False)


def test_by_axes_short_generator_row_via_assembled_short():
    """assembled_short × any → short_generator row (all three default)."""
    layers = resolve_sensory_layers_by_axes(
        master_enabled=True,
        c_override=None,
        a_override=None,
        audio_override=None,
        cut_intent="assembled_short",
        timeline_mode="raw_dump",
    )
    assert layers == (True, True, True)


# ---------------------------------------------------- legacy-shim parity loop


# Every (preset, timeline_mode) pair the legacy shim sees in production.
# Pairing this list against the by-axes path proves the migration is
# byte-identical wherever the legacy function is still called.
_LEGACY_CALL_SHAPES = [
    # Content-type presets across all four modes
    ("vlog", "raw_dump"),
    ("vlog", "rough_cut"),
    ("vlog", "curated"),
    ("vlog", "assembled"),
    ("interview", "raw_dump"),
    ("wedding", "rough_cut"),
    ("podcast", "curated"),
    ("presentation", "assembled"),
    ("tutorial", "raw_dump"),
    ("reaction", "rough_cut"),
    ("product_demo", "curated"),
    # Cut-intent presets — timeline_mode is overridden to assembled for
    # tightener (preserved by the shim); ignored entirely for clip_hunter
    # / short_generator (their key collapse skips mode).
    ("tightener", "raw_dump"),
    ("tightener", "assembled"),
    ("clip_hunter", "raw_dump"),
    ("clip_hunter", "rough_cut"),
    ("short_generator", "raw_dump"),
    ("short_generator", "assembled"),
]


@pytest.mark.parametrize("preset,timeline_mode", _LEGACY_CALL_SHAPES)
@pytest.mark.parametrize("master", [True, False])
def test_legacy_shim_matches_pre_phase6_behaviour(preset, timeline_mode, master):
    """Legacy shim returns the same row that the pre-Phase-6 ``sensory_mode_key``
    + matrix lookup would have produced — proves the delegation is byte-clean."""
    expected_key = sensory_mode_key(preset, timeline_mode)
    expected_row = SENSORY_MATRIX[expected_key]

    def _expected(level):
        if not master:
            return False
        return level == "default"

    expected = (
        _expected(expected_row.c),
        _expected(expected_row.a),
        _expected(expected_row.audio),
    )
    actual = resolve_sensory_layers(
        master_enabled=master,
        c_override=None,
        a_override=None,
        audio_override=None,
        preset=preset,
        timeline_mode=timeline_mode,
    )
    assert actual == expected
