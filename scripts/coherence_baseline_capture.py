#!/usr/bin/env python3
"""Capture story-critic baseline fixtures from real runs.

Phase 0b.1 of ``Implementation/optimizaiton/story-critic.md``.

Walks ``state.RUN_ROOT`` (default ``~/.cutmaster/cutmaster/``) for runs
that have a persisted plan, lists them, and dumps a chosen run's plan +
scrubbed transcript + resolved axes to
``tests/cutmaster/fixtures/coherence_reports/inputs/{run_id}.fixture.json``
in the shape the critic adapter (Phase 1.2) expects.

Pre-9bf8e73 plans (without ``arc_role`` on segments) are accepted —
the critic adapter handles ``arc_role=None`` gracefully (Phase 1.5).

Usage::

    uv run python scripts/coherence_baseline_capture.py list
    uv run python scripts/coherence_baseline_capture.py capture <run_id>
    uv run python scripts/coherence_baseline_capture.py capture <run_id> --notes "broken hook, missing payoff"

The ``--notes`` flag tags the fixture as a real-correction (lands in
``real_corrections/`` instead of ``inputs/``) and writes a sibling
``.notes.md`` file with the editor's reasoning.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cutmaster_ai.cutmaster.core import state  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "cutmaster" / "fixtures" / "coherence_reports"
INPUTS_DIR = FIXTURE_ROOT / "inputs"
CORRECTIONS_DIR = FIXTURE_ROOT / "real_corrections"


def _list_runs() -> list[dict]:
    """Return summaries of every run that has a persisted plan."""
    if not state.RUN_ROOT.exists():
        return []
    out: list[dict] = []
    for path in sorted(state.RUN_ROOT.glob("*.json")):
        # Skip prompt dumps; only run-state files.
        if path.name.endswith(".director_prompt.txt"):
            continue
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plan = run.get("plan")
        if not plan:
            continue
        out.append(
            {
                "run_id": run.get("run_id") or path.stem,
                "timeline_name": run.get("timeline_name") or "(unknown)",
                "preset": run.get("preset") or "(none)",
                "kind": _plan_kind(plan),
                "duration_s": _plan_duration_s(run, plan),
                "created_at": run.get("created_at") or "",
                "has_arc_roles": _plan_has_arc_roles(plan),
                "has_resolved_axes": bool(plan.get("resolved_axes")),
            }
        )
    return out


def _plan_kind(plan: dict) -> str:
    """Identify which builder produced this plan, for adapter dispatch."""
    if plan.get("clip_hunter"):
        return "clip_hunter"
    if plan.get("short_generator"):
        return "short_generator"
    if plan.get("assembled_director"):
        return "assembled"
    if plan.get("curated_director"):
        return "curated"
    if plan.get("director"):
        return "director"
    return "unknown"


def _plan_has_arc_roles(plan: dict) -> bool:
    director = plan.get("director") or {}
    clips = director.get("selected_clips") or []
    return any(seg.get("arc_role") for seg in clips)


def _plan_duration_s(run: dict, plan: dict) -> float | None:
    """Best-effort cut duration. Falls back to scrubbed-transcript span."""
    director = plan.get("director")
    if director and director.get("selected_clips"):
        clips = director["selected_clips"]
        return sum(c["end_s"] - c["start_s"] for c in clips)
    scrubbed = run.get("scrubbed") or []
    if scrubbed:
        return float(scrubbed[-1]["end_time"]) - float(scrubbed[0]["start_time"])
    return None


def cmd_list() -> int:
    runs = _list_runs()
    if not runs:
        print(f"No runs with persisted plans found under {state.RUN_ROOT}")
        return 0
    print(f"{len(runs)} run(s) with plans under {state.RUN_ROOT}:\n")
    print(f"{'run_id':<14} {'kind':<16} {'preset':<14} {'dur':>7} {'arc':<5} {'axes':<5} timeline")
    print("-" * 90)
    for r in runs:
        dur = f"{r['duration_s']:.1f}s" if r["duration_s"] is not None else "?"
        arc = "yes" if r["has_arc_roles"] else "no"
        axes = "yes" if r["has_resolved_axes"] else "no"
        print(
            f"{r['run_id'][:14]:<14} {r['kind']:<16} {r['preset'][:14]:<14} "
            f"{dur:>7} {arc:<5} {axes:<5} {r['timeline_name']}"
        )
    print()
    print("To capture: uv run python scripts/coherence_baseline_capture.py capture <run_id>")
    return 0


def cmd_capture(run_id: str, notes: str | None) -> int:
    run = state.load(run_id)
    if run is None:
        print(f"ERROR: run {run_id} not found under {state.RUN_ROOT}", file=sys.stderr)
        return 2
    plan = run.get("plan")
    if not plan:
        print(f"ERROR: run {run_id} has no persisted plan — run a build first", file=sys.stderr)
        return 2

    fixture = {
        "run_id": run_id,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "kind": _plan_kind(plan),
        "run_meta": {
            "timeline_name": run.get("timeline_name"),
            "preset": run.get("preset"),
            "duration_s": _plan_duration_s(run, plan),
            "created_at": run.get("created_at"),
        },
        "resolved_axes": plan.get("resolved_axes"),
        "plan": plan,
        "scrubbed_transcript": run.get("scrubbed") or run.get("transcript") or [],
    }

    target_dir = CORRECTIONS_DIR if notes else INPUTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = target_dir / f"{run_id}.fixture.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")

    if notes:
        notes_path = target_dir / f"{run_id}.notes.md"
        notes_path.write_text(
            f"# Real-correction notes for run `{run_id}`\n\n"
            f"Captured: {fixture['captured_at']}\n\n"
            f"## What's wrong\n\n{notes}\n",
            encoding="utf-8",
        )
        print(f"Captured CORRECTION → {fixture_path.relative_to(REPO_ROOT)}")
        print(f"Notes              → {notes_path.relative_to(REPO_ROOT)}")
    else:
        print(f"Captured BASELINE → {fixture_path.relative_to(REPO_ROOT)}")

    if not fixture["resolved_axes"]:
        print(
            "  WARN: no resolved_axes on plan — pre-Phase-4.6 build. "
            "The critic adapter will infer cut_intent from the legacy preset.",
            file=sys.stderr,
        )
    if fixture["kind"] == "director" and not _plan_has_arc_roles(plan):
        print(
            "  NOTE: plan has no arc_role on segments — pre-9bf8e73 build. "
            "The critic adapter handles arc_role=None per Phase 1.5.",
            file=sys.stderr,
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture story-critic baseline fixtures from real runs."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List runs with persisted plans.")
    cap = sub.add_parser("capture", help="Dump one run's plan + transcript as a fixture.")
    cap.add_argument("run_id", help="Run id to capture (see ``list``).")
    cap.add_argument(
        "--notes",
        default=None,
        help="If set, lands the fixture under real_corrections/ with this prose as .notes.md.",
    )
    args = parser.parse_args(argv)

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "capture":
        return cmd_capture(args.run_id, args.notes)
    parser.error(f"unknown command {args.cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
