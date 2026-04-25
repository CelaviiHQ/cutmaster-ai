"""Cross-language parity for the axis-resolution heuristic.

The panel's ``apps/panel/src/axes.ts::resolveCutIntent`` is a hand-ported
mirror of ``cutmaster_ai.cutmaster.data.axis_resolution.resolve_cut_intent``.
Drift between the two would silently desync the panel's resolved chip
from the server's decision. This test replays every fixture under
``tests/cutmaster/fixtures/axis_resolution/`` through the TS resolver
(via Node's native ``--experimental-strip-types``) and asserts the
discrete output (intent + source) matches the labelled expectation.

Skipped gracefully when Node is unavailable or older than 22.6 — the
test is a parity gate, not a hard dependency.

Reason strings are intentionally not compared. They're UX copy and
diverge slightly by design (e.g. Python writes "44s under 45s",
TS writes "44s under 45s" too, but pinning the literal text would
shackle future copy edits without protecting any actual contract).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "cutmaster" / "fixtures" / "axis_resolution"
_PANEL_DIR = _REPO_ROOT / "apps" / "panel"
_REPLAY_SCRIPT = _PANEL_DIR / "scripts" / "replay-fixtures.ts"


def _node_supports_strip_types() -> bool:
    """``--experimental-strip-types`` lands in Node 22.6+."""
    if shutil.which("node") is None:
        return False
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    # ``v22.22.1`` -> (22, 22, 1)
    try:
        major, minor, *_ = (int(p) for p in out.lstrip("v").split("."))
    except ValueError:
        return False
    return (major, minor) >= (22, 6)


_SKIP_REASON = (
    "Node 22.6+ with --experimental-strip-types support not available — "
    "panel parity test requires native TS stripping. Install Node 22.6+ "
    "(or skip this test in environments without the panel toolchain)."
)


@pytest.fixture(scope="module")
def ts_results() -> list[dict[str, str]]:
    """Run every fixture through the TS resolver in a single Node invocation."""
    if not _node_supports_strip_types():
        pytest.skip(_SKIP_REASON)
    if not _REPLAY_SCRIPT.exists():
        pytest.skip(f"Replay script missing at {_REPLAY_SCRIPT}")

    fixture_paths = sorted(_FIXTURE_DIR.glob("*.json"))
    inputs = [json.loads(p.read_text())["input"] for p in fixture_paths]

    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "./scripts/replay-fixtures.ts",
        ],
        input=json.dumps(inputs),
        cwd=str(_PANEL_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"replay-fixtures.ts failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return json.loads(proc.stdout)


def _heuristic_fixtures() -> list[tuple[int, str, dict]]:
    """Fixtures the heuristic actually owns — ``cut_intent_source != "user"``.

    Returns ``(index_in_full_fixture_list, name, fixture)`` so the test
    can reach into the TS results array (which is keyed by full-list
    index, not heuristic-only index).
    """
    out = []
    for idx, p in enumerate(sorted(_FIXTURE_DIR.glob("*.json"))):
        f = json.loads(p.read_text())
        if f["expected"]["cut_intent_source"] != "user":
            out.append((idx, p.name, f))
    return out


_HEURISTIC = _heuristic_fixtures()


@pytest.mark.parametrize(
    ("idx", "name", "fixture"), _HEURISTIC, ids=[name for _, name, _ in _HEURISTIC]
)
def test_ts_resolveCutIntent_matches_python(
    idx: int, name: str, fixture: dict, ts_results: list[dict[str, str]]
) -> None:
    """TS resolver agrees with Python on intent + source for every auto/forced cell."""
    exp = fixture["expected"]
    got = ts_results[idx]
    assert got["intent"] == exp["cut_intent"], (
        f"{name}: TS picked intent={got['intent']!r}, Python expected {exp['cut_intent']!r}"
    )
    assert got["source"] == exp["cut_intent_source"], (
        f"{name}: TS picked source={got['source']!r}, Python expected {exp['cut_intent_source']!r}"
    )


def test_heuristic_fixture_count_floor() -> None:
    """At least 6 of the 15 starter fixtures must exercise the heuristic.

    User-supplied fixtures don't test the resolver — they test wiring.
    Today's split: 9 user, 4 auto, 2 forced = 6 heuristic. Drop below
    that and this parity gate degrades; raise it as real-corrected
    fixtures land.
    """
    assert len(_HEURISTIC) >= 6, (
        f"Expected ≥6 heuristic fixtures (auto + forced); found {len(_HEURISTIC)}"
    )
