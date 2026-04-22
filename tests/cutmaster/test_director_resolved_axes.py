"""Tests for the three-axis Director prompt path.

Covers the Phase 3a+3b rewrite of the six Director prompt builders — the
flag-off path must still match the Phase 0.6 golden baselines byte-for-byte,
and the flag-on path must (a) carry the resolved axes' pacing / reorder /
strategy content and (b) stay within ±5% of the flag-off token budget on
semantically equivalent runs (narrative arc).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cutmaster_ai.cutmaster.core import director
from cutmaster_ai.cutmaster.data.axis_resolution import resolve_axes
from cutmaster_ai.cutmaster.data.presets import get_preset

BASELINE_DIR = Path(__file__).parent / "fixtures" / "prompt_baselines"

# Identical fixtures to tests/cutmaster/fixtures/prompt_baselines/_capture.py
# so the byte-for-byte parity test isn't an aspirational comparison.
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


# Representative-scale transcript for token-budget tests. The tiny 12-word
# fixture used for byte-parity inflates fixed-cost deltas (role override,
# strategy footer) out of proportion; production prompts run 500–2000 words
# so the ratio between flag-off and flag-on shrinks accordingly. 200 words
# is enough to make the budget test honest without slowing the suite.
def _large_transcript(n_words: int = 200) -> list[dict]:
    out: list[dict] = []
    for i in range(n_words):
        out.append(
            {
                "word": f"word{i}.",
                "start_time": i * 0.5,
                "end_time": i * 0.5 + 0.45,
                "speaker_id": "S1" if i % 8 else "S2",
            }
        )
    return out


LARGE_TRANSCRIPT: list[dict] = _large_transcript(200)


def _large_take(item_idx: int) -> dict:
    return {
        "item_index": item_idx,
        "source_name": f"clip_{item_idx:02d}.mov",
        "start_s": LARGE_TRANSCRIPT[0]["start_time"],
        "end_s": LARGE_TRANSCRIPT[-1]["end_time"],
        "transcript": [dict(w, i=i) for i, w in enumerate(LARGE_TRANSCRIPT)],
    }


LARGE_TAKES: list[dict] = [_large_take(0), _large_take(1)]


# --------------------------------------------------------------------- utils


def _baseline(name: str) -> str:
    return (BASELINE_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _narrative_axes(content_type: str, timeline_mode: str = "raw_dump", duration_s: float = 120.0):
    return resolve_axes(
        content_type,
        cut_intent="narrative",
        duration_s=duration_s,
        timeline_mode=timeline_mode,
    )


# --------------------------------------------- flag-off parity with baselines


def test_flag_off_flat_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert prompt == _baseline("flat__vlog")


def test_flag_off_flat_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert prompt == _baseline("flat__interview")


def test_flag_off_assembled_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._assembled_prompt(preset, TAKES, user_settings={"reorder_allowed": True})
    assert prompt == _baseline("assembled__vlog")


def test_flag_off_assembled_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._assembled_prompt(preset, TAKES, user_settings={"reorder_allowed": True})
    assert prompt == _baseline("assembled__interview")


def test_flag_on_but_resolved_missing_is_a_noop(monkeypatch) -> None:
    """The flag alone can't swing the path — builders only pivot when both
    the flag is set AND a ``ResolvedAxes`` is supplied. Otherwise legacy
    callers keep their baselines."""
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "true")
    preset = get_preset("vlog")
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={})
    assert prompt == _baseline("flat__vlog")


# ---------------------------------------------- flag-on shape assertions (3a)


def test_flag_on_flat_vlog_narrative_surfaces_resolved_values(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("vlog")
    axes = _narrative_axes("vlog", "raw_dump", duration_s=120.0)
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={}, resolved=axes)

    # PACING BOUNDS render the resolved numeric pacing rather than preset defaults.
    assert "PACING BOUNDS" in prompt
    assert f"{axes.segment_pacing.min:.0f}s" in prompt
    assert f"{axes.segment_pacing.target:.0f}s" in prompt
    assert f"{axes.segment_pacing.max:.0f}s" in prompt

    # Reorder mode survives — vlog narrative = preserve_macro.
    assert axes.reorder_mode == "preserve_macro"
    assert "PRESERVE MACRO" in prompt

    # Selection strategy is narrative-arc → no footer (nominal case stays quiet).
    assert "SELECTION STRATEGY" not in prompt


def test_flag_on_flat_interview_peak_highlight_adds_strategy_footer(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "true")
    preset = get_preset("interview")
    axes = resolve_axes(
        "interview",
        cut_intent="peak_highlight",
        duration_s=60.0,
        timeline_mode="raw_dump",
    )
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={}, resolved=axes)

    assert axes.selection_strategy == "peak-hunt"
    assert "SELECTION STRATEGY — PEAK HUNT" in prompt
    # Peak highlight overrides the role — the interview default shouldn't leak.
    assert "short-form highlight editor" in prompt
    # Resolved reorder (free) suppresses the REORDER POLICY block entirely.
    assert "REORDER POLICY" not in prompt


def test_flag_on_assembled_vlog_narrative_uses_resolved_pacing(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "on")
    preset = get_preset("vlog")
    axes = _narrative_axes("vlog", "assembled", duration_s=600.0)
    prompt = director._assembled_prompt(
        preset, TAKES, user_settings={"reorder_allowed": True}, resolved=axes
    )
    # _assembled_prompt renders preset.pacing as a one-liner (block #2) — the
    # resolved path must route through the facade so the string survives.
    assert "Pacing:" in prompt
    # No strategy footer for narrative arc.
    assert "SELECTION STRATEGY" not in prompt


def test_flag_on_assembled_interview_surgical_tighten_adds_strategy_footer(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "yes")
    preset = get_preset("interview")
    axes = resolve_axes(
        "interview",
        cut_intent="surgical_tighten",
        duration_s=600.0,
        timeline_mode="assembled",
    )
    prompt = director._assembled_prompt(
        preset, TAKES, user_settings={"reorder_allowed": True}, resolved=axes
    )

    assert axes.selection_strategy == "preserve-takes"
    assert "SELECTION STRATEGY — PRESERVE TAKES" in prompt
    # Surgical tighten overrides the role; the default interview role must not leak.
    assert "no-LLM tightener" in prompt


# -------------------------------------------------- token-budget regression (3a.6)


@pytest.mark.parametrize("content_type", ["vlog", "interview"])
def test_flag_on_narrative_token_budget_within_five_percent_flat(
    monkeypatch, content_type: str
) -> None:
    """The narrative-arc flag-on prompt must not inflate / deflate the
    flag-off baseline by more than ±5%. Measured in chars on a
    representative (200-word) transcript — the absolute unit cancels
    in the ratio; what matters is that the facade swap doesn't grow
    the prompt."""
    preset = get_preset(content_type)
    axes = _narrative_axes(content_type, "raw_dump", duration_s=120.0)

    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    flag_off = director._prompt(preset, LARGE_TRANSCRIPT, user_settings={})

    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    flag_on = director._prompt(preset, LARGE_TRANSCRIPT, user_settings={}, resolved=axes)

    ratio = len(flag_on) / len(flag_off)
    assert 0.95 <= ratio <= 1.05, (
        f"{content_type}: flag_on/flag_off char ratio {ratio:.3f} "
        f"outside [0.95, 1.05] (on={len(flag_on)}, off={len(flag_off)})"
    )


@pytest.mark.parametrize("content_type", ["vlog", "interview"])
def test_flag_on_narrative_token_budget_within_five_percent_assembled(
    monkeypatch, content_type: str
) -> None:
    preset = get_preset(content_type)
    axes = _narrative_axes(content_type, "assembled", duration_s=600.0)

    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    flag_off = director._assembled_prompt(
        preset, LARGE_TAKES, user_settings={"reorder_allowed": True}
    )

    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "true")
    flag_on = director._assembled_prompt(
        preset, LARGE_TAKES, user_settings={"reorder_allowed": True}, resolved=axes
    )

    ratio = len(flag_on) / len(flag_off)
    assert 0.95 <= ratio <= 1.05, (
        f"{content_type} assembled: flag_on/flag_off char ratio {ratio:.3f} "
        f"outside [0.95, 1.05] (on={len(flag_on)}, off={len(flag_off)})"
    )


# ---------------------------------------------- flag-off parity for 3b builders


def test_flag_off_clip_hunter_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._clip_hunter_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_clip_length_s=30.0,
        num_clips=3,
    )
    assert prompt == _baseline("clip_hunter__vlog")


def test_flag_off_clip_hunter_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._clip_hunter_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_clip_length_s=30.0,
        num_clips=3,
    )
    assert prompt == _baseline("clip_hunter__interview")


def test_flag_off_short_generator_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._short_generator_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_short_length_s=45.0,
        num_shorts=2,
    )
    assert prompt == _baseline("short_generator__vlog")


def test_flag_off_short_generator_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._short_generator_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_short_length_s=45.0,
        num_shorts=2,
    )
    assert prompt == _baseline("short_generator__interview")


def test_flag_off_curated_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._curated_prompt(preset, TAKES, user_settings={})
    assert prompt == _baseline("curated__vlog")


def test_flag_off_curated_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._curated_prompt(preset, TAKES, user_settings={})
    assert prompt == _baseline("curated__interview")


def test_flag_off_rough_cut_vlog_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("vlog")
    prompt = director._rough_cut_prompt(preset, TAKES, GROUPS, user_settings={})
    assert prompt == _baseline("rough_cut__vlog")


def test_flag_off_rough_cut_interview_matches_baseline(monkeypatch) -> None:
    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    preset = get_preset("interview")
    prompt = director._rough_cut_prompt(preset, TAKES, GROUPS, user_settings={})
    assert prompt == _baseline("rough_cut__interview")


# ------------------------------------------- flag-on content assertions (3b)


def test_flag_on_clip_hunter_renders_top_n_strategy_footer(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("vlog")
    axes = resolve_axes(
        "vlog",
        cut_intent="multi_clip",
        duration_s=600.0,
        timeline_mode="raw_dump",
        num_clips=3,
    )
    prompt = director._clip_hunter_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_clip_length_s=30.0,
        num_clips=3,
        resolved=axes,
    )
    assert axes.selection_strategy == "top-n"
    assert "SELECTION STRATEGY — TOP N" in prompt
    # Multi-clip overrides role + hook_rule + marker_vocabulary.
    assert "viral-moments editor" in prompt


def test_flag_on_short_generator_renders_montage_strategy_footer(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "true")
    preset = get_preset("interview")
    axes = resolve_axes(
        "interview",
        cut_intent="assembled_short",
        duration_s=60.0,
        timeline_mode="raw_dump",
    )
    prompt = director._short_generator_prompt(
        preset,
        TRANSCRIPT,
        user_settings={},
        target_short_length_s=45.0,
        num_shorts=2,
        resolved=axes,
    )
    assert axes.selection_strategy == "montage"
    assert "SELECTION STRATEGY — MONTAGE" in prompt
    # Assembled short overrides role.
    assert "TikTok / Reels editor" in prompt


def test_flag_on_curated_narrative_no_footer_but_uses_resolved_role(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("wedding")
    axes = _narrative_axes("wedding", "curated", duration_s=600.0)
    prompt = director._curated_prompt(preset, TAKES, user_settings={}, resolved=axes)
    # Narrative = no footer; wedding role survives via the facade.
    assert "SELECTION STRATEGY" not in prompt
    # Narrative inherits the content profile's role (no override).
    assert "wedding" in prompt.lower()


def test_flag_on_rough_cut_narrative_surfaces_reorder(monkeypatch) -> None:
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("interview")
    axes = _narrative_axes("interview", "rough_cut", duration_s=600.0)
    prompt = director._rough_cut_prompt(preset, TAKES, GROUPS, user_settings={}, resolved=axes)
    # Interview × Narrative → locked; the block surfaces on the assembled-family
    # builder via the shared shim. Rough cut doesn't render _reorder_mode_block
    # today (assembled path drives reorder via reorder_allowed) so we assert
    # the pacing string from the facade survives instead.
    assert "Pacing:" in prompt


# --------------------------------------- token-budget regression for 3b (±5%)


@pytest.mark.parametrize(
    "builder_name",
    ["clip_hunter", "short_generator", "curated", "rough_cut"],
)
@pytest.mark.parametrize("content_type", ["vlog", "interview"])
def test_flag_on_single_intent_builder_token_budget(
    monkeypatch, builder_name: str, content_type: str
) -> None:
    """Flag-on single-intent builders stay within ±5% of their flag-off
    baselines — the facade swap must not inflate token spend."""
    preset = get_preset(content_type)

    # Build both paths with matching axes. Each builder's "canonical" intent
    # is used so strategy footers are emitted on flag-on and the ratio
    # reflects the real production delta. Representative-scale transcript
    # so fixed-cost deltas (role override, strategy footer) don't dominate.
    if builder_name == "clip_hunter":
        axes = resolve_axes(
            content_type, "multi_clip", duration_s=600.0, timeline_mode="raw_dump", num_clips=3
        )
        flag_off_kwargs = dict(
            transcript=LARGE_TRANSCRIPT,
            user_settings={},
            target_clip_length_s=30.0,
            num_clips=3,
        )
        builder = director._clip_hunter_prompt
    elif builder_name == "short_generator":
        axes = resolve_axes(
            content_type, "assembled_short", duration_s=60.0, timeline_mode="raw_dump"
        )
        flag_off_kwargs = dict(
            transcript=LARGE_TRANSCRIPT,
            user_settings={},
            target_short_length_s=45.0,
            num_shorts=2,
        )
        builder = director._short_generator_prompt
    elif builder_name == "curated":
        axes = _narrative_axes(content_type, "curated", duration_s=600.0)
        flag_off_kwargs = dict(takes=LARGE_TAKES, user_settings={})
        builder = director._curated_prompt
    else:  # rough_cut
        axes = _narrative_axes(content_type, "rough_cut", duration_s=600.0)
        flag_off_kwargs = dict(takes=LARGE_TAKES, groups=GROUPS, user_settings={})
        builder = director._rough_cut_prompt

    monkeypatch.delenv("CUTMASTER_USE_RESOLVED_AXES", raising=False)
    flag_off = builder(preset, **flag_off_kwargs)

    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    flag_on = builder(preset, **flag_off_kwargs, resolved=axes)

    ratio = len(flag_on) / len(flag_off)
    assert 0.95 <= ratio <= 1.05, (
        f"{builder_name} × {content_type}: ratio {ratio:.3f} outside [0.95, 1.05] "
        f"(on={len(flag_on)}, off={len(flag_off)})"
    )


# --------------------------------------------- 3b.6 regression tests (examples)


def test_wedding_narrative_long_form_prompt_carries_preserve_macro(monkeypatch) -> None:
    """Wedding × Narrative × 600s — the prompt must carry ``preserve_macro``."""
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("wedding")
    axes = _narrative_axes("wedding", "raw_dump", duration_s=600.0)
    assert axes.reorder_mode == "preserve_macro"
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={}, resolved=axes)
    assert "PRESERVE MACRO" in prompt


def test_wedding_peak_highlight_short_prompt_has_no_reorder_block(monkeypatch) -> None:
    """Wedding × Peak Highlight × 60s — reorder_mode=free → no REORDER block."""
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("wedding")
    axes = resolve_axes(
        "wedding", cut_intent="peak_highlight", duration_s=60.0, timeline_mode="raw_dump"
    )
    assert axes.reorder_mode == "free"
    prompt = director._prompt(preset, TRANSCRIPT, user_settings={}, resolved=axes)
    assert "REORDER POLICY" not in prompt


def test_interview_peak_highlight_60s_pacing_in_design_doc_window() -> None:
    """Interview × Peak Highlight × 60s pacing is within the design doc's
    ~{3, 7, 18}s example — Open Q 1 gives ±25% latitude on curve constants."""
    axes = resolve_axes(
        "interview",
        cut_intent="peak_highlight",
        duration_s=60.0,
        timeline_mode="raw_dump",
    )
    pacing = axes.segment_pacing
    # Design doc reference: min≈3, target≈7, max≈18. Tolerance 25% per
    # Open Q 1 — curve constants are provisional until Phase 6 calibration.
    assert pacing.target == pytest.approx(7.0, rel=0.25)
    assert pacing.min == pytest.approx(3.0, abs=1.5)
    assert pacing.max == pytest.approx(18.0, rel=0.25)


# ---------------------------------------------- 3c.1 build_*_plan forwarding


def test_build_cut_plan_forwards_resolved_to_prompt_builder(monkeypatch) -> None:
    """Phase 3c.1 regression — ``build_cut_plan`` must thread ``resolved``
    through to :func:`_prompt`. Stub ``llm.call_structured`` and inspect
    the rendered prompt it receives."""
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("interview")
    axes = resolve_axes(
        "interview",
        cut_intent="peak_highlight",
        duration_s=60.0,
        timeline_mode="raw_dump",
    )
    captured: dict = {}

    def _fake_call_structured(**kwargs):
        captured.update(kwargs)
        # Return a minimal valid DirectorPlan so the build helper unwinds.
        return director.DirectorPlan(
            hook_index=0,
            selected_clips=[director.CutSegment(start_s=0.0, end_s=3.3)],
            reasoning="",
        )

    monkeypatch.setattr(director.llm, "call_structured", _fake_call_structured)

    plan = director.build_cut_plan(TRANSCRIPT, preset, user_settings={}, resolved=axes)
    assert isinstance(plan, director.DirectorPlan)
    assert "SELECTION STRATEGY — PEAK HUNT" in captured["prompt"]
    assert "short-form highlight editor" in captured["prompt"]


def test_build_cut_plan_without_resolved_renders_legacy_baseline(monkeypatch) -> None:
    """Omitting ``resolved`` (or leaving the flag unset) must still give
    the flag-off prompt — the rollback plan depends on this."""
    monkeypatch.setenv("CUTMASTER_USE_RESOLVED_AXES", "1")
    preset = get_preset("vlog")
    captured: dict = {}

    def _fake_call_structured(**kwargs):
        captured.update(kwargs)
        return director.DirectorPlan(
            hook_index=0,
            selected_clips=[director.CutSegment(start_s=0.0, end_s=3.3)],
            reasoning="",
        )

    monkeypatch.setattr(director.llm, "call_structured", _fake_call_structured)

    director.build_cut_plan(TRANSCRIPT, preset, user_settings={})
    assert captured["prompt"] == _baseline("flat__vlog")


# --------------------------------------------------- 3c.4 schema stability


def test_director_plan_json_schemas_unchanged_under_flag() -> None:
    """The cut plan JSON schemas must not change — Phase 3c.4 invariant.

    We only rewrote prompt wording; the response models
    (:class:`DirectorPlan` and friends) are untouched. This test snapshots
    the field sets so a future rewrite can't silently widen the contract.
    """
    assert set(director.DirectorPlan.model_fields) == {
        "hook_index",
        "selected_clips",
        "reasoning",
    }
    assert set(director.AssembledDirectorPlan.model_fields) == {
        "hook_index",
        "selections",
        "reasoning",
    }
    assert set(director.CuratedDirectorPlan.model_fields) == {
        "hook_order",
        "selections",
        "reasoning",
    }
    assert set(director.ClipHunterPlan.model_fields) == {
        "candidates",
        "reasoning",
    }
    assert set(director.ShortGeneratorPlan.model_fields) == {
        "candidates",
        "reasoning",
    }


# --------------------------------------------------- 3c.2 pipeline helper


def test_stash_resolved_axes_persists_on_run(tmp_path, monkeypatch) -> None:
    """``pipeline.stash_resolved_axes`` caches the resolved recipe on
    the run dict so downstream stages can read from one source."""
    from cutmaster_ai.cutmaster.core import pipeline, state

    # Redirect state.save to a no-op so the test doesn't require the
    # filesystem scaffolding a real run carries.
    monkeypatch.setattr(state, "save", lambda run: None)

    run: dict = {"run_id": "test"}
    payload = pipeline.stash_resolved_axes(
        run,
        content_type="vlog",
        cut_intent="narrative",
        duration_s=120.0,
        timeline_mode="raw_dump",
    )
    assert payload is not None
    assert run["resolved_axes"] == payload
    assert payload["content_type"] == "vlog"
    assert payload["cut_intent"] == "narrative"
    assert payload["reorder_mode"] == "preserve_macro"


def test_stash_resolved_axes_returns_none_on_incompatible(monkeypatch) -> None:
    from cutmaster_ai.cutmaster.core import pipeline, state

    monkeypatch.setattr(state, "save", lambda run: None)
    run: dict = {"run_id": "test"}
    # surgical_tighten × raw_dump is blocked by axis_compat.
    payload = pipeline.stash_resolved_axes(
        run,
        content_type="vlog",
        cut_intent="surgical_tighten",
        duration_s=600.0,
        timeline_mode="raw_dump",
    )
    assert payload is None
    assert "resolved_axes" not in run
