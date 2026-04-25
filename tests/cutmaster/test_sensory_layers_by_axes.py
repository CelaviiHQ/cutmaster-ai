"""Tests for the axis-keyed sensory-layer resolver.

Three layers of coverage:

- ``axes_to_sensory_key`` collapses every ``(cut_intent, timeline_mode)``
  pair onto the existing 6 ``SENSORY_MATRIX`` rows per §5 of the design
  doc (full 5×4 grid).
- ``resolve_sensory_layers_by_axes`` honours override precedence + master
  toggle on representative rows.
- The full Cartesian sweep (108 cases) pins the resolver's truth table
  over the entire state space.
"""

from __future__ import annotations

import pytest

from cutmaster_ai.cutmaster.data.presets import (
    SENSORY_MATRIX,
    axes_to_sensory_key,
    resolve_sensory_layers_by_axes,
)

# ---------------------------------------------------------------- axes_to_key


# Full §5 truth table — 5 cut_intents × 4 timeline_modes = 20 cells. The
# resolver short-circuits on cut_intent before ``timeline_mode`` is
# consulted for everything except ``narrative``, so most rows are
# defensive against a future regression that introduces a mode-dependent
# branch.
@pytest.mark.parametrize(
    "cut_intent,timeline_mode,expected",
    [
        # multi_clip → clip_hunter row regardless of mode
        ("multi_clip", "raw_dump", "clip_hunter"),
        ("multi_clip", "rough_cut", "clip_hunter"),
        ("multi_clip", "curated", "clip_hunter"),
        ("multi_clip", "assembled", "clip_hunter"),
        # assembled_short → short_generator row regardless of mode
        ("assembled_short", "raw_dump", "short_generator"),
        ("assembled_short", "rough_cut", "short_generator"),
        ("assembled_short", "curated", "short_generator"),
        ("assembled_short", "assembled", "short_generator"),
        # surgical_tighten only valid with assembled, but the function is
        # data-only — it returns "assembled" for any mode (axis_compat
        # blocks the invalid combos earlier in the request flow).
        ("surgical_tighten", "raw_dump", "assembled"),
        ("surgical_tighten", "rough_cut", "assembled"),
        ("surgical_tighten", "curated", "assembled"),
        ("surgical_tighten", "assembled", "assembled"),
        # narrative × <mode> → mode's row
        ("narrative", "raw_dump", "raw_dump"),
        ("narrative", "rough_cut", "rough_cut"),
        ("narrative", "curated", "curated"),
        ("narrative", "assembled", "assembled"),
        # peak_highlight × * → raw_dump row
        ("peak_highlight", "raw_dump", "raw_dump"),
        ("peak_highlight", "rough_cut", "raw_dump"),
        ("peak_highlight", "curated", "raw_dump"),
        ("peak_highlight", "assembled", "raw_dump"),
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


# --------------------------------------------------------- full Cartesian sweep
#
# Pin the resolver's truth table over the full state space so any future
# matrix or precedence change fails CI loudly instead of drifting through
# incidental coverage. 6 rows × 3 layers × 3 override states × 2 master
# states = 108 cases, generated programmatically from ``SENSORY_MATRIX``
# so adding a row auto-extends coverage.


def _build_cartesian_cases() -> list:
    cases = []
    for row_key, row in SENSORY_MATRIX.items():
        for layer in ("c", "a", "audio"):
            for override in (None, True, False):
                for master in (True, False):
                    cases.append((row_key, layer, override, master, getattr(row, layer)))
    return cases


_CARTESIAN_CASES = _build_cartesian_cases()


# Map matrix row key back to a representative ``(cut_intent, timeline_mode)``
# pair so the resolver receives axis-keyed inputs. Mirrors the §5 collapse
# table in reverse — uses the canonical intent each row was designed for.
_ROW_TO_AXES: dict[str, tuple[str, str]] = {
    "raw_dump": ("narrative", "raw_dump"),
    "rough_cut": ("narrative", "rough_cut"),
    "curated": ("narrative", "curated"),
    "assembled": ("narrative", "assembled"),
    "clip_hunter": ("multi_clip", "raw_dump"),
    "short_generator": ("assembled_short", "raw_dump"),
}


@pytest.mark.parametrize("row_key,layer,override,master,level", _CARTESIAN_CASES)
def test_resolver_full_cartesian_truth_table(row_key, layer, override, master, level):
    """Override beats matrix; master gates ``default`` level; ``opt_in`` /
    ``off`` stay off without an explicit override regardless of master."""
    cut_intent, timeline_mode = _ROW_TO_AXES[row_key]
    overrides = {"c": None, "a": None, "audio": None}
    overrides[layer] = override

    layers = resolve_sensory_layers_by_axes(
        master_enabled=master,
        c_override=overrides["c"],
        a_override=overrides["a"],
        audio_override=overrides["audio"],
        cut_intent=cut_intent,
        timeline_mode=timeline_mode,
    )
    layer_index = {"c": 0, "a": 1, "audio": 2}[layer]
    actual = layers[layer_index]

    expected = override if override is not None else (master and level == "default")
    assert actual is expected, (
        f"row={row_key} layer={layer} override={override} master={master} "
        f"level={level} → expected {expected}, got {actual}"
    )
