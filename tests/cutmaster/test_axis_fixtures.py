"""Phase 6.5 — fixture-driven coverage of axis resolution.

Discovers every ``.json`` file under
``tests/cutmaster/fixtures/axis_resolution/`` and asserts the resolver
output matches the labelled expectation. Adding a fixture requires no
test changes — drop a new ``.json`` matching the README format and the
parametrize collector picks it up.

Pacing constants are deliberately **not** asserted here; they're
calibration targets for Phase 6.6. The fixtures pin the discrete
decisions (cut_intent, source, reorder_mode, selection_strategy,
prompt_builder, unusual flag) which won't change with calibration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cutmaster_ai.cutmaster.data.axis_resolution import resolve_axes

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "axis_resolution"


def _discover_fixtures() -> list[tuple[str, dict]]:
    """Load every ``.json`` fixture under the axis_resolution directory.

    Returns ``(filename, parsed_dict)`` so test failure output names the
    failing fixture without dumping the whole JSON blob.
    """
    files = sorted(_FIXTURE_DIR.glob("*.json"))
    return [(f.name, json.loads(f.read_text())) for f in files]


_FIXTURES = _discover_fixtures()
_FIXTURE_IDS = [name for name, _ in _FIXTURES]


def test_fixture_count_meets_phase6_target() -> None:
    """Phase 6.4 pins the starter set at 15 fixtures.

    If you're adding real-corrected fixtures and the count grows past 15,
    update this floor — never lower it.
    """
    assert len(_FIXTURES) >= 15, (
        f"Phase 6.4 starter set requires ≥15 fixtures; found {len(_FIXTURES)}"
    )


@pytest.mark.parametrize("fixture", [f for _, f in _FIXTURES], ids=_FIXTURE_IDS)
def test_fixture_resolves_to_expected(fixture: dict) -> None:
    """Every fixture's resolver output matches its ``expected`` block exactly."""
    inp = fixture["input"]
    exp = fixture["expected"]

    axes = resolve_axes(
        inp["content_type"],
        inp["cut_intent"],
        duration_s=inp["duration_s"],
        timeline_mode=inp["timeline_mode"],
        num_clips=inp.get("num_clips", 1),
        reorder_allowed=inp.get("reorder_allowed", True),
        takes_already_scrubbed=inp.get("takes_already_scrubbed", False),
    )

    # Discrete fields — calibration won't move these.
    assert axes.content_type == exp["content_type"]
    assert axes.cut_intent == exp["cut_intent"]
    assert axes.cut_intent_source == exp["cut_intent_source"]
    assert axes.reorder_mode == exp["reorder_mode"]
    assert axes.selection_strategy == exp["selection_strategy"]
    assert axes.prompt_builder == exp["prompt_builder"]
    assert axes.unusual == exp["unusual"]


def test_every_fixture_carries_required_metadata() -> None:
    """README schema check — every fixture has ``label``, ``source``,
    ``input``, ``expected`` keys. Catches drift before runtime."""
    required = {"label", "source", "input", "expected"}
    for name, fixture in _FIXTURES:
        missing = required - fixture.keys()
        assert not missing, f"{name} missing keys: {missing}"


def test_fixtures_cover_all_eight_content_types() -> None:
    """The starter set must touch every Axis 1 value at least once.

    Adding fixtures focused on a single content type is fine; this
    floor protects against accidental coverage regression.
    """
    all_content_types = {
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "presentation",
        "reaction",
    }
    seen = {f["input"]["content_type"] for _, f in _FIXTURES}
    missing = all_content_types - seen
    assert not missing, f"Content types with no fixture coverage: {missing}"


def test_fixtures_cover_all_three_cut_intent_sources() -> None:
    """``user`` / ``auto`` / ``forced`` provenance buckets each get exercised."""
    sources = {f["expected"]["cut_intent_source"] for _, f in _FIXTURES}
    assert sources == {"user", "auto", "forced"}, (
        f"Expected all three provenance buckets in fixture set; got {sources}"
    )
