"""Tests for Director prompt rendering (v2-1).

Covers the EXCLUDE CATEGORIES and USER FOCUS blocks. We don't call the
LLM — we just inspect the prompt string the Director would send.
"""

from celavii_resolve.cutmaster import director
from celavii_resolve.cutmaster.presets import get_preset

TRANSCRIPT = [
    {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
    {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
]


def test_prompt_without_excludes_or_focus_has_no_optional_blocks():
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert "EXCLUDE CATEGORIES" not in prompt
    assert "USER FOCUS" not in prompt


def test_prompt_with_selected_excludes_renders_labels_and_descriptions():
    preset = get_preset("wedding")
    settings = {
        "exclude_categories": ["mc_talking", "vendor_mentions"],
        "custom_focus": None,
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "EXCLUDE CATEGORIES" in prompt
    # Labels (human, not keys) must be rendered so the LLM can reason.
    assert "MC / DJ housekeeping" in prompt
    assert "Vendor mentions" in prompt
    # Descriptions must also appear.
    assert "caterers, florists" in prompt
    # Unselected categories must NOT leak into the prompt.
    assert "Legal formalities" not in prompt


def test_prompt_drops_unknown_exclude_keys_silently():
    """UI bugs that send a key the preset doesn't declare must not crash
    the Director. Unknown keys are filtered; known keys still render."""
    preset = get_preset("wedding")
    settings = {
        "exclude_categories": ["mc_talking", "ghost_category_that_does_not_exist"],
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "MC / DJ housekeeping" in prompt
    assert "ghost_category" not in prompt


def test_prompt_with_custom_focus_renders_focus_block():
    preset = get_preset("product_demo")
    settings = {"custom_focus": "emphasise battery life"}
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "USER FOCUS" in prompt
    assert "emphasise battery life" in prompt


def test_prompt_with_blank_focus_is_ignored():
    preset = get_preset("vlog")
    settings = {"custom_focus": "   "}
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "USER FOCUS" not in prompt


def test_prompt_with_both_excludes_and_focus():
    preset = get_preset("podcast")
    settings = {
        "exclude_categories": ["ad_reads"],
        "custom_focus": "keep the debate about remote work",
    }
    prompt = director._prompt(preset, TRANSCRIPT, settings)
    assert "EXCLUDE CATEGORIES" in prompt
    assert "Ad / sponsor reads" in prompt
    assert "USER FOCUS" in prompt
    assert "keep the debate about remote work" in prompt


def test_every_content_type_preset_bundles_exclude_categories_and_placeholder():
    """v2-1 exit criterion: every Director-driven preset ships exclusion
    options + a focus placeholder hint. Workflow presets like Tightener
    (v2-3) skip the Director and don't need them."""
    from celavii_resolve.cutmaster.presets import PRESETS

    content_type_presets = [
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "reaction",
        "clip_hunter",
    ]
    for key in content_type_presets:
        bundle = PRESETS[key]
        assert bundle.exclude_categories, (
            f"{bundle.key} has no exclude_categories — v2-1 expected ≥4 per preset"
        )
        assert len(bundle.exclude_categories) >= 4, (
            f"{bundle.key} has only {len(bundle.exclude_categories)} categories; "
            f"v2-1 spec calls for ≥4–6 per preset"
        )
        assert bundle.default_custom_focus_placeholder.strip(), (
            f"{bundle.key} has an empty custom-focus placeholder"
        )


# ---------------------------------------------------------------------------
# v2-5 speaker-aware blocks
# ---------------------------------------------------------------------------


TWO_SPEAKER_TRANSCRIPT = [
    {"word": "Tell", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
    {"word": "me", "start_time": 0.3, "end_time": 0.5, "speaker_id": "S1"},
    {"word": "about", "start_time": 0.5, "end_time": 0.8, "speaker_id": "S1"},
    {"word": "it.", "start_time": 0.8, "end_time": 1.0, "speaker_id": "S1"},
    {"word": "Well,", "start_time": 1.1, "end_time": 1.4, "speaker_id": "S2"},
    {"word": "it", "start_time": 1.4, "end_time": 1.6, "speaker_id": "S2"},
    {"word": "was", "start_time": 1.6, "end_time": 1.8, "speaker_id": "S2"},
    {"word": "great.", "start_time": 1.8, "end_time": 2.2, "speaker_id": "S2"},
]


def test_interview_preset_renders_speaker_block_on_two_speaker_transcript():
    preset = get_preset("interview")
    prompt = director._prompt(preset, TWO_SPEAKER_TRANSCRIPT, user_settings={})
    assert "SPEAKER GUIDANCE" in prompt
    # Roster shown with counts so the model knows who speaks more.
    assert "S1" in prompt and "S2" in prompt
    # Preset guidance verbiage bleeds through.
    assert "interviewer" in prompt.lower()


def test_single_speaker_transcript_suppresses_speaker_block():
    """Speaker-aware presets still skip the block when only one speaker
    exists — e.g. a solo interviewer-only practice run."""
    preset = get_preset("interview")
    solo = [
        {"word": "yes", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
        {"word": "right", "start_time": 0.3, "end_time": 0.6, "speaker_id": "S1"},
    ]
    prompt = director._prompt(preset, solo, user_settings={})
    assert "SPEAKER GUIDANCE" not in prompt


def test_non_speaker_aware_preset_never_renders_speaker_block():
    """Vlog + Tutorial + Wedding leave speaker_awareness empty on purpose;
    the Director would ignore speaker context on monologue content."""
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TWO_SPEAKER_TRANSCRIPT, user_settings={})
    assert "SPEAKER GUIDANCE" not in prompt


def test_speaker_labels_are_applied_to_block_and_transcript():
    preset = get_preset("podcast")
    settings = {"speaker_labels": {"S1": "Host", "S2": "Guest"}}
    prompt = director._prompt(preset, TWO_SPEAKER_TRANSCRIPT, settings)
    # Block roster uses the labels, not raw STT ids.
    assert "Host" in prompt and "Guest" in prompt
    # Serialised transcript JSON also carries the labels so the model sees
    # the same names in both places.
    assert '"speaker_id":"Host"' in prompt
    assert '"speaker_id":"Guest"' in prompt


def test_clip_hunter_prompt_shows_speaker_block_when_awareness_set():
    """Clip Hunter uses the content-type preset Podcast has an awareness
    fragment; when Clip Hunter IS the preset, its own awareness setting
    governs. The default Clip Hunter bundle is speaker-neutral, so the
    block should be absent even with 2 speakers."""
    preset = get_preset("clip_hunter")
    dense = [
        {
            "word": f"w{i}",
            "start_time": i * 1.0,
            "end_time": i * 1.0 + 0.9,
            "speaker_id": "S1" if i % 2 == 0 else "S2",
        }
        for i in range(60)
    ]
    prompt = director._clip_hunter_prompt(
        preset,
        dense,
        user_settings={},
        target_clip_length_s=30.0,
        num_clips=2,
    )
    assert "SPEAKER GUIDANCE" not in prompt


def test_assembled_prompt_speaker_block_reflects_flattened_takes():
    """Assembled prompt must flatten across takes so speakers detected in
    *any* take show up in the roster."""
    preset = get_preset("interview")
    takes = [
        {
            "item_index": 0,
            "source_name": "take_a.mov",
            "start_s": 0.0,
            "end_s": 5.0,
            "transcript": [
                {"i": 0, "word": "hi", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
            ],
        },
        {
            "item_index": 1,
            "source_name": "take_b.mov",
            "start_s": 5.0,
            "end_s": 10.0,
            "transcript": [
                {"i": 0, "word": "thanks", "start_time": 5.0, "end_time": 5.4, "speaker_id": "S2"},
            ],
        },
    ]
    prompt = director._assembled_prompt(preset, takes, user_settings={})
    assert "SPEAKER GUIDANCE" in prompt
    assert "S1" in prompt and "S2" in prompt


def test_prompt_renders_clip_metadata_block_when_words_carry_clip_index():
    preset = get_preset("vlog")
    transcript = [
        {
            "word": "Hello",
            "start_time": 0.0,
            "end_time": 0.4,
            "speaker_id": "S1",
            "clip_index": 0,
            "clip_metadata": {
                "source_name": "DJI_0006.MP4",
                "duration_s": 3.0,
                "timeline_offset_s": 0.0,
            },
        },
        {
            "word": "world.",
            "start_time": 3.1,
            "end_time": 3.6,
            "speaker_id": "S1",
            "clip_index": 1,
            "clip_metadata": {
                "source_name": "DJI_0007.MP4",
                "duration_s": 2.5,
                "timeline_offset_s": 3.0,
            },
        },
    ]
    prompt = director._prompt(preset, transcript, user_settings={})
    assert "CLIP METADATA" in prompt
    assert "DJI_0006.MP4" in prompt
    assert "DJI_0007.MP4" in prompt


def test_prompt_strips_per_word_clip_metadata_to_save_tokens():
    """The CLIP METADATA table renders clip info once; embedding the same
    dict on every word roughly triples the prompt. v2-6 payoff should keep
    `clip_index` on words and drop `clip_metadata`."""
    preset = get_preset("vlog")
    transcript = [
        {
            "word": "Hello",
            "start_time": 0.0,
            "end_time": 0.4,
            "speaker_id": "S1",
            "clip_index": 0,
            "clip_metadata": {
                "source_name": "DJI_0006.MP4",
                "duration_s": 3.0,
                "timeline_offset_s": 0.0,
                "source_path": "/tmp/x.mov",
            },
        },
    ]
    prompt = director._prompt(preset, transcript, user_settings={})
    # Metadata block still renders (the table).
    assert "DJI_0006.MP4" in prompt
    # But it must NOT appear as a JSON value inside the per-word array —
    # that would mean we're sending the redundant metadata to Gemini.
    _, _, tail = prompt.partition("TRANSCRIPT (JSON array):")
    assert "clip_metadata" not in tail
    assert "source_path" not in tail
    # clip_index still there so the Director can cross-reference.
    assert "clip_index" in tail


def test_prompt_no_clip_metadata_block_when_absent():
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert "CLIP METADATA" not in prompt


def test_every_preset_has_speaker_awareness_field():
    """Schema-level invariant: after v2-5 every preset carries the field,
    even if empty. Guards against someone adding a preset without it."""
    from celavii_resolve.cutmaster.presets import PRESETS

    for bundle in PRESETS.values():
        assert hasattr(bundle, "speaker_awareness")


def test_interview_and_podcast_are_the_only_speaker_aware_presets():
    """Ship list guard: future presets that want speaker awareness should
    update this test consciously, not drift in."""
    from celavii_resolve.cutmaster.presets import PRESETS

    speaker_aware = {k for k, b in PRESETS.items() if (b.speaker_awareness or "").strip()}
    assert speaker_aware == {"interview", "podcast"}


def test_exclude_category_keys_are_unique_per_preset():
    from celavii_resolve.cutmaster.presets import PRESETS

    for bundle in PRESETS.values():
        keys = [c.key for c in bundle.exclude_categories]
        assert len(keys) == len(set(keys)), f"{bundle.key} has duplicate keys"
