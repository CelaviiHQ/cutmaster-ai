"""Capture golden-prompt baselines from the six Director builders.

Phase 0.6 of the three-axis-model plan. Run this once against current
main BEFORE any Phase 3 work lands so the "no regression" success
criterion has a real reference point.

Usage:
    uv run python tests/cutmaster/fixtures/prompt_baselines/_capture.py

Writes one ``{builder}__{preset}.txt`` per (builder, preset) pair under
this directory. Do not edit the outputs by hand — re-run the script
after intentional prompt changes and commit the diff.
"""

from __future__ import annotations

from pathlib import Path

from cutmaster_ai.cutmaster.core import director
from cutmaster_ai.cutmaster.data.presets import get_preset

OUT_DIR = Path(__file__).parent

TRANSCRIPT: list[dict] = [
    {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    {"word": "everyone,", "start_time": 0.5, "end_time": 1.0, "speaker_id": "S1"},
    {"word": "today", "start_time": 1.0, "end_time": 1.3, "speaker_id": "S1"},
    {"word": "we're", "start_time": 1.3, "end_time": 1.55, "speaker_id": "S1"},
    {"word": "talking", "start_time": 1.55, "end_time": 2.0, "speaker_id": "S1"},
    {"word": "about", "start_time": 2.0, "end_time": 2.4, "speaker_id": "S1"},
    {"word": "remote", "start_time": 2.4, "end_time": 2.8, "speaker_id": "S1"},
    {"word": "work.", "start_time": 2.8, "end_time": 3.3, "speaker_id": "S1"},
    {"word": "So", "start_time": 3.5, "end_time": 3.7, "speaker_id": "S2"},
    {"word": "what's", "start_time": 3.7, "end_time": 4.0, "speaker_id": "S2"},
    {"word": "your", "start_time": 4.0, "end_time": 4.2, "speaker_id": "S2"},
    {"word": "take?", "start_time": 4.2, "end_time": 4.7, "speaker_id": "S2"},
]


def _take(item_idx: int) -> dict:
    return {
        "item_index": item_idx,
        "source_name": f"clip_{item_idx:02d}.mov",
        "start_s": TRANSCRIPT[0]["start_time"],
        "end_s": TRANSCRIPT[-1]["end_time"],
        "transcript": [dict(w, i=i) for i, w in enumerate(TRANSCRIPT)],
    }


TAKES: list[dict] = [_take(0), _take(1)]
GROUPS: list[dict] = [{"group_id": "g0", "item_indexes": [0, 1], "signal": "color"}]

# Two representative content types per builder: vlog (single-speaker
# baseline) and interview (multi-speaker, exercises speaker block).
PRESETS = ("vlog", "interview")


def _write(name: str, body: str) -> None:
    path = OUT_DIR / f"{name}.txt"
    path.write_text(body, encoding="utf-8")
    print(f"wrote {path.relative_to(OUT_DIR.parents[3])} ({len(body)} chars)")


def main() -> None:
    for key in PRESETS:
        preset = get_preset(key)

        _write(f"flat__{key}", director._prompt(preset, TRANSCRIPT, user_settings={}))
        _write(
            f"assembled__{key}",
            director._assembled_prompt(preset, TAKES, user_settings={"reorder_allowed": True}),
        )
        _write(
            f"clip_hunter__{key}",
            director._clip_hunter_prompt(
                preset,
                TRANSCRIPT,
                user_settings={},
                target_clip_length_s=30.0,
                num_clips=3,
            ),
        )
        _write(
            f"short_generator__{key}",
            director._short_generator_prompt(
                preset,
                TRANSCRIPT,
                user_settings={},
                target_short_length_s=45.0,
                num_shorts=2,
            ),
        )
        _write(
            f"curated__{key}",
            director._curated_prompt(preset, TAKES, user_settings={}),
        )
        _write(
            f"rough_cut__{key}",
            director._rough_cut_prompt(preset, TAKES, GROUPS, user_settings={}),
        )


if __name__ == "__main__":
    main()
