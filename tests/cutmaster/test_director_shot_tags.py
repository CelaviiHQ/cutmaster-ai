"""v4 Phase 4.1 tests — SHOT TAGS block injection into Director prompts.

Covers the coalesce-by-tag-identity renderer, overflow truncation, and
injection into every one of the six prompt builders. No LLM calls —
just inspection of the prompt string.
"""

from __future__ import annotations

import copy

import pytest

from celavii_resolve.cutmaster.core import director
from celavii_resolve.cutmaster.data.presets import get_preset


def _tag(
    *,
    item_index: int = 0,
    timeline_ts_s: float,
    shot_type: str = "closeup",
    framing: str = "speaker_centered",
    gesture_intensity: str = "calm",
    visual_energy: int = 4,
    notable: str | None = None,
) -> dict:
    """Build a shot_tag dict shaped like shot_tagger.attach_tags_to_transcript."""
    return {
        "item_index": item_index,
        "source_ts_s": 0.0,
        "timeline_ts_s": timeline_ts_s,
        "shot_type": shot_type,
        "framing": framing,
        "gesture_intensity": gesture_intensity,
        "visual_energy": visual_energy,
        "notable": notable,
    }


def _words(n: int, tag: dict | None, start: float = 0.0, step: float = 0.3) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        w: dict = {
            "word": f"w{i}",
            "start_time": start + i * step,
            "end_time": start + (i + 1) * step,
            "speaker_id": "S1",
        }
        if tag is not None:
            w["shot_tag"] = dict(tag)
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# Block renderer
# ---------------------------------------------------------------------------


def test_block_empty_when_no_tags():
    transcript = _words(5, tag=None)
    assert director._shot_tag_block(transcript) == ""


def test_block_empty_when_all_tags_are_unknown():
    # Row-level filter: every field is default -> row gets dropped entirely.
    blank_tag = _tag(
        timeline_ts_s=0.0,
        shot_type="unknown",
        framing="unknown",
        gesture_intensity="unknown",
        visual_energy=0,
        notable=None,
    )
    transcript = _words(5, tag=blank_tag)
    assert director._shot_tag_block(transcript) == ""


def test_block_coalesces_consecutive_identical_tags():
    tag_a = _tag(
        timeline_ts_s=0.0,
        shot_type="closeup",
        gesture_intensity="emphatic",
        visual_energy=8,
        notable="speaker leans in",
    )
    tag_b = _tag(timeline_ts_s=5.0, shot_type="medium", gesture_intensity="calm", visual_energy=4)
    transcript = _words(4, tag=tag_a) + _words(6, tag=tag_b, start=5.0)
    block = director._shot_tag_block(transcript)

    assert "SHOT TAGS" in block
    assert "words 0-3" in block
    assert "words 4-9" in block
    assert "shot=closeup" in block
    assert "shot=medium" in block
    assert '"speaker leans in"' in block
    # Guidance footer is part of the block.
    assert "Prefer:" in block


def test_block_omits_unknown_fields_per_row():
    """framing=unknown must not appear as 'framing=unknown' in the row."""
    tag = _tag(
        timeline_ts_s=0.0,
        shot_type="wide",
        framing="unknown",
        gesture_intensity="unknown",
        visual_energy=2,
    )
    transcript = _words(3, tag=tag)
    block = director._shot_tag_block(transcript)
    assert "shot=wide" in block
    assert "framing=unknown" not in block
    assert "gest=unknown" not in block
    # Energy > 0 still shows up.
    assert "energy=2" in block


def test_block_separates_when_item_index_differs_same_timeline_ts():
    """Same timeline_ts but different item_index should NOT coalesce."""
    a = _tag(item_index=0, timeline_ts_s=1.0, shot_type="closeup")
    b = _tag(item_index=1, timeline_ts_s=1.0, shot_type="wide")
    transcript = _words(2, tag=a) + _words(2, tag=b, start=10.0)
    block = director._shot_tag_block(transcript)
    assert "words 0-1" in block
    assert "words 2-3" in block


def test_block_handles_untagged_words_between_tagged_runs():
    tag = _tag(timeline_ts_s=0.0, shot_type="closeup")
    transcript = _words(2, tag=tag) + _words(2, tag=None, start=2.0) + _words(2, tag=tag, start=4.0)
    block = director._shot_tag_block(transcript)
    # Two separate runs: 0-1 and 4-5. Untagged 2-3 breaks the coalesce.
    assert "words 0-1" in block
    assert "words 4-5" in block


def test_block_truncates_with_summary_when_over_cap(monkeypatch):
    monkeypatch.setattr(director, "_SHOT_TAG_MAX_ROWS", 3)
    # 5 distinct tag identities -> 5 rows -> 2 truncated.
    transcript: list[dict] = []
    for i in range(5):
        t = _tag(timeline_ts_s=float(i), shot_type="closeup", visual_energy=i + 1)
        transcript.extend(_words(2, tag=t, start=float(i)))
    block = director._shot_tag_block(transcript)
    assert "... 2 more ranges omitted" in block


def test_block_indices_reference_passed_in_transcript():
    """Row labels must match the position of words in the argument list,
    not any absolute timeline index. This matters because prompt builders
    flatten takes' transcripts differently in each mode."""
    tag = _tag(timeline_ts_s=0.0, shot_type="closeup")
    # Prepend 3 untagged words; tagged run starts at index 3.
    transcript = _words(3, tag=None) + _words(2, tag=tag, start=3.0)
    block = director._shot_tag_block(transcript)
    assert "words 3-4" in block


# ---------------------------------------------------------------------------
# Slim + prompt injection
# ---------------------------------------------------------------------------


def test_slim_strips_shot_tag_from_words():
    tag = _tag(timeline_ts_s=0.0)
    transcript = _words(2, tag=tag)
    slimmed = director._slim_transcript_for_prompt(copy.deepcopy(transcript))
    for w in slimmed:
        assert "shot_tag" not in w
    # Input wasn't mutated.
    assert "shot_tag" in transcript[0]


def test_slim_preserves_words_without_shot_tag():
    transcript = _words(2, tag=None)
    assert director._slim_transcript_for_prompt(transcript) == transcript


@pytest.mark.parametrize(
    "builder",
    [
        "flat_prompt",
        "clip_hunter",
        "short_generator",
        "assembled",
        "curated",
        "rough_cut",
    ],
)
def test_all_six_prompt_builders_inject_shot_tag_block(builder):
    preset = get_preset("vlog")
    tag = _tag(timeline_ts_s=0.0, shot_type="closeup", notable="leans in")
    words = _words(4, tag=tag)

    if builder == "flat_prompt":
        prompt = director._prompt(preset, words, user_settings={})
    elif builder == "clip_hunter":
        prompt = director._clip_hunter_prompt(
            preset, words, user_settings={}, target_clip_length_s=30.0, num_clips=3
        )
    elif builder == "short_generator":
        prompt = director._short_generator_prompt(
            preset, words, user_settings={}, target_short_length_s=45.0, num_shorts=2
        )
    elif builder == "assembled":
        takes = [
            {
                "item_index": 0,
                "source_name": "clip_a",
                "start_s": 0.0,
                "end_s": 4.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        prompt = director._assembled_prompt(preset, takes, user_settings={})
    elif builder == "curated":
        takes = [
            {
                "item_index": 0,
                "source_name": "clip_a",
                "start_s": 0.0,
                "end_s": 4.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        prompt = director._curated_prompt(preset, takes, user_settings={})
    elif builder == "rough_cut":
        takes = [
            {
                "item_index": 0,
                "source_name": "clip_a",
                "start_s": 0.0,
                "end_s": 4.0,
                "transcript": [dict(w, i=i) for i, w in enumerate(words)],
            }
        ]
        groups = [{"group_id": "g0", "item_indexes": [0], "signal": "color"}]
        prompt = director._rough_cut_prompt(preset, takes, groups, user_settings={})
    else:
        pytest.fail(f"unknown builder: {builder}")

    assert "SHOT TAGS" in prompt
    assert "shot=closeup" in prompt
    assert '"leans in"' in prompt
    # JSON-serialised transcript/takes must NOT carry shot_tag — the
    # slimmer is responsible for stripping it.
    assert '"shot_tag"' not in prompt


def test_prompt_without_tags_omits_shot_tags_header():
    preset = get_preset("vlog")
    words = _words(3, tag=None)
    prompt = director._prompt(preset, words, user_settings={})
    assert "SHOT TAGS" not in prompt
