"""Labeled-fixture regression — the cascade must resolve each labeled
transcript to its expected preset.

Fixture format: see ``tests/cutmaster/fixtures/autodetect/README.md``.

Tests discover every ``.json`` under the fixtures tree and assert:

  - ``rec.preset == fixture["label"]`` — the core correctness bar.
  - For real-corrected fixtures (``source`` starts with ``real-``):
    ``rec.confidence >= 0.7`` — the proposal's calibration target.

LLM tiers are stubbed so the test stays deterministic + offline. Every
fixture must therefore classify via Tiers 0-3 alone (or land on the
right answer even when the Tier 4 fallback kicks in).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cutmaster_ai.cutmaster.analysis.auto_detect import detect_preset

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "autodetect"


def _discover_fixtures() -> list[tuple[str, Path]]:
    """Return a list of (label, fixture_path) pairs for pytest parametrize."""
    if not FIXTURES_ROOT.exists():
        return []
    pairs: list[tuple[str, Path]] = []
    for label_dir in sorted(FIXTURES_ROOT.iterdir()):
        if not label_dir.is_dir() or label_dir.name.startswith("_"):
            continue
        for fx in sorted(label_dir.glob("*.json")):
            pairs.append((label_dir.name, fx))
    return pairs


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """Keep fixture runs offline + deterministic.

    Tier 4's LLM call is forced to raise so the cascade's fallback path
    kicks in — a fixture that needs Tier 4 to pick correctly is a bad
    fixture. Tier 3's call is stubbed to neutral zeros for the same
    reason.
    """
    import cutmaster_ai.intelligence.llm as llm_mod

    def _no_llm(*a, **kw):
        raise RuntimeError("LLM disabled for fixture tests")

    monkeypatch.setattr(llm_mod, "call_structured", _no_llm)


@pytest.mark.parametrize(
    "label,fixture_path",
    _discover_fixtures(),
    ids=lambda v: v if isinstance(v, str) else v.stem,
)
def test_fixture_resolves_to_labeled_preset(label: str, fixture_path: Path):
    data = json.loads(fixture_path.read_text())
    assert data["label"] == label, (
        f"fixture {fixture_path.name}: label field {data['label']!r} "
        f"doesn't match parent directory {label!r}"
    )

    rec = detect_preset(data["transcript"], run_state=data.get("run_state"))
    assert rec.preset == label, (
        f"{fixture_path.relative_to(FIXTURES_ROOT.parent.parent)}: "
        f"expected {label!r}, got {rec.preset!r} (conf={rec.confidence:.2f}, "
        f"margin={rec.signals.margin if rec.signals else '?'})"
    )

    source = str(data.get("source", ""))
    if source.startswith("real-"):
        assert rec.confidence >= 0.7, (
            f"{fixture_path.name}: real-corrected fixture must clear the "
            f"0.7 confidence bar (got {rec.confidence:.2f})"
        )


def test_fixture_coverage_report():
    """Flag presets that don't yet have a labeled fixture.

    Not a hard failure — the calibration work accumulates over time —
    but surfaces which presets need real-corrected transcripts next.
    """
    from cutmaster_ai.cutmaster.analysis.auto_detect.scoring import classifiable_presets

    covered = (
        {d.name for d in FIXTURES_ROOT.iterdir() if d.is_dir()} if FIXTURES_ROOT.exists() else set()
    )
    missing = sorted(set(classifiable_presets()) - covered)
    if missing:
        pytest.skip(f"No fixtures yet for: {missing}")
