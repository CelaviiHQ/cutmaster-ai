"""Director agent — scrubbed transcript → selected `CutSegment[]`.

Picks contiguous word-aligned blocks the editor should keep. Enforces
verbatim timestamps via a post-validation retry loop; if the model rounds
``12.450 → 12.45`` the response is rejected and the model gets a chance to
fix it.

Model-agnostic: the actual LLM call goes through ``llm.call_structured``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ...intelligence import llm
from ..analysis._sentences import (
    _SENTENCE_PUNCT,
    SENTENCE_PAUSE_FALLBACK_S,
    _word_ends_sentence,
)
from ..analysis._sentences import (
    coalesce_to_sentences as _coalesce_to_sentences,
)
from ..analysis._sentences import (
    has_reliable_punctuation as _has_reliable_punctuation,
)
from ..analysis._sentences import (
    sentence_edge_times as _sentence_edge_times,
)
from ..analysis._sentences import (
    sentence_spans as _sentence_spans,
)
from ..stt.per_clip import clip_metadata_table
from ..stt.speakers import apply_speaker_labels, detect_speakers, speaker_stats

if TYPE_CHECKING:
    from ..data.presets import PresetBundle

log = logging.getLogger("cutmaster-ai.cutmaster.director")


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class CutSegment(BaseModel):
    start_s: float
    end_s: float
    reason: str = Field(
        default="",
        description="One short sentence — why this block made the cut.",
    )


class DirectorPlan(BaseModel):
    hook_index: int = Field(description="Index into selected_clips of the opening beat (0-based).")
    selected_clips: list[CutSegment]
    reasoning: str = Field(default="", description="Brief rationale for the overall structure.")


def _bounded_director_plan(min_n: int, max_n: int | None) -> type[BaseModel]:
    """Build a DirectorPlan variant with ``selected_clips`` length bounds.

    Gemini honors ``minItems`` / ``maxItems`` on structured outputs, so
    capping N server-side lets the model produce valid plans on the first
    attempt instead of falling into a validator loop that rejects the
    count and burns retries. ``max_n=None`` leaves the upper bound open
    (useful when ``target_length_s`` is unset).
    """
    from pydantic import create_model

    field_kwargs: dict = {"min_length": max(1, min_n)}
    if max_n is not None:
        field_kwargs["max_length"] = max(max_n, field_kwargs["min_length"])
    suffix = f"{field_kwargs['min_length']}_{field_kwargs.get('max_length', 'N')}"
    return create_model(
        f"DirectorPlan_{suffix}",
        hook_index=(
            int,
            Field(description="Index into selected_clips of the opening beat (0-based)."),
        ),
        selected_clips=(list[CutSegment], Field(**field_kwargs)),
        reasoning=(
            str,
            Field(default="", description="Brief rationale for the overall structure."),
        ),
    )


# ---------------------------------------------------------------------------
# Verbatim-timestamp validator
# ---------------------------------------------------------------------------


TIMESTAMP_TOLERANCE_S = 0.001  # 1 ms — tolerates float repr but not rounding

# Sentence helpers imported from ``analysis._sentences`` at top of file.
# Re-exported under the historical private aliases so the rest of this
# module (and any test that imports them from here) keeps working.
_SENTENCE_REEXPORTS = (
    SENTENCE_PAUSE_FALLBACK_S,
    _SENTENCE_PUNCT,
    _word_ends_sentence,
    _coalesce_to_sentences,
    _has_reliable_punctuation,
    _sentence_edge_times,
    _sentence_spans,
)


def _build_timestamp_sets(transcript: list[dict]) -> tuple[list[float], list[float]]:
    starts = sorted({float(w["start_time"]) for w in transcript})
    ends = sorted({float(w["end_time"]) for w in transcript})
    return starts, ends


def _close_to_any(value: float, sorted_values: list[float]) -> bool:
    # Linear scan is fine — transcripts have O(1000) words max.
    return any(abs(value - v) <= TIMESTAMP_TOLERANCE_S for v in sorted_values)


def _duration_budget(
    target_length_s: float, target_segment_s: float = 22.0
) -> tuple[float, float, int, float]:
    """Return (floor_s, ceiling_s, min_segments, avg_span_s) for a target length.

    Lite models satisfice on soft ranges, so we hand them pre-solved
    arithmetic (N segments × D seconds = target) and reject plans that
    come in below the 75 % floor. ``target_segment_s`` comes from the
    preset (vlog ~18 s, podcast ~35 s, etc.) — the pre-solve matches the
    pacing the prompt is already asking for instead of forcing a fixed
    minimum segment count across every preset.
    """
    min_segments = max(3, round(target_length_s / target_segment_s))
    avg_span = target_length_s / min_segments
    low = target_length_s * 0.75
    high = target_length_s * 1.25
    return low, high, min_segments, avg_span


HOOK_TOLERANCE_S = 2.0
COVERAGE_THRESHOLD = 0.7
CONFIDENCE_FLOOR = 0.6


def _nearest_word(target_time: float, transcript: list[dict], key: str) -> dict | None:
    """Return the transcript word whose ``key`` ("start_time" / "end_time")
    is closest to ``target_time`` within TIMESTAMP_TOLERANCE_S, else None."""
    best: dict | None = None
    best_delta = TIMESTAMP_TOLERANCE_S
    for w in transcript:
        v = w.get(key)
        if v is None:
            continue
        delta = abs(float(v) - target_time)
        if delta <= best_delta:
            best = w
            best_delta = delta
    return best


def validate_plan(
    plan: DirectorPlan,
    transcript: list[dict],
    target_length_s: float | None = None,
    selected_hook_s: float | None = None,
    preset: PresetBundle | None = None,
    chapters: list[dict] | None = None,
) -> list[str]:
    """Return a list of validation errors. Empty list = valid.

    Checks:
      1. Every ``start_s`` matches a word's ``start_time`` within tolerance.
      2. Every ``end_s`` matches a word's ``end_time`` within tolerance.
      3. Segments have positive duration.
      4. hook_index is in range.
      5. When ``target_length_s`` is set: total span-sum within
         ``[0.75×, 1.25×]`` of target.
      6. When ``preset`` has pacing bounds: each segment's duration lies
         within ``[min_segment_s, max_segment_s]``.
      7. When the transcript carries ``clip_index`` (per-clip STT) and
         ``preset`` is set: at least COVERAGE_THRESHOLD of eligible clips
         (≥ 30 words) are touched by some segment.
      8. When transcript words carry ``confidence`` (Deepgram runs): each
         segment's first and last word has confidence >= CONFIDENCE_FLOOR.
      9. When ``preset.reorder_mode != "free"``: non-hook segments respect
         source-time order (locked) or chapter-order (preserve_macro).
    """
    # Sentence-edge timestamps are the only valid cut points — this is
    # what prevents mid-phrase starts like "gonna break all the software"
    # or mid-clause ends like "most weird multi chain". The Director
    # prompt only exposes sentence-edge times, so this also catches models
    # that hallucinate per-word timestamps.
    starts, ends = _sentence_edge_times(transcript)
    errors: list[str] = []

    if not plan.selected_clips:
        return ["selected_clips is empty — the Director must pick at least one block"]

    if not (0 <= plan.hook_index < len(plan.selected_clips)):
        errors.append(
            f"hook_index {plan.hook_index} is out of range for "
            f"{len(plan.selected_clips)} selected_clips"
        )

    for i, seg in enumerate(plan.selected_clips):
        if seg.end_s <= seg.start_s:
            errors.append(f"segment[{i}]: end_s {seg.end_s} must be > start_s {seg.start_s}")
            continue
        if not _close_to_any(seg.start_s, starts):
            nearest = min(starts, key=lambda t: abs(t - seg.start_s)) if starts else None
            hint = f" — nearest sentence start is {nearest:.3f}s" if nearest is not None else ""
            errors.append(
                f"segment[{i}]: start_s {seg.start_s} is not a sentence start "
                f"(mid-sentence cuts are not allowed){hint}"
            )
        if not _close_to_any(seg.end_s, ends):
            nearest = min(ends, key=lambda t: abs(t - seg.end_s)) if ends else None
            hint = f" — nearest sentence end is {nearest:.3f}s" if nearest is not None else ""
            errors.append(
                f"segment[{i}]: end_s {seg.end_s} is not a sentence end "
                f"(mid-sentence cuts are not allowed){hint}"
            )

    if selected_hook_s is not None and plan.selected_clips:
        first = (
            plan.selected_clips[plan.hook_index]
            if 0 <= plan.hook_index < len(plan.selected_clips)
            else plan.selected_clips[0]
        )
        drift = abs(first.start_s - selected_hook_s)
        if drift > HOOK_TOLERANCE_S:
            errors.append(
                f"hook drift: your hook segment starts at {first.start_s:.2f}s "
                f"but the editor picked {selected_hook_s:.2f}s. Pick a block "
                f"whose first word starts within {HOOK_TOLERANCE_S:.1f}s of "
                f"{selected_hook_s:.2f}s and place it at hook_index."
            )

    # Confidence gate: only fires when the transcript actually carries
    # per-word confidence (Deepgram). Skipping silently on Gemini-STT runs
    # is the graceful-degradation path — the validator isn't forcing a
    # transcript upgrade on users who haven't opted in to Deepgram.
    has_confidence = any(w.get("confidence") is not None for w in transcript)
    if has_confidence:
        for i, seg in enumerate(plan.selected_clips):
            if seg.end_s <= seg.start_s:
                continue  # already flagged above
            first = _nearest_word(seg.start_s, transcript, "start_time")
            last = _nearest_word(seg.end_s, transcript, "end_time")
            if first is not None:
                c = first.get("confidence")
                if c is not None and float(c) < CONFIDENCE_FLOOR:
                    errors.append(
                        f"segment[{i}]: starts on low-confidence word "
                        f"'{first.get('word', '?')}' (conf {float(c):.2f} < "
                        f"{CONFIDENCE_FLOOR:.2f}). Move the start to the next "
                        "crisply-transcribed word boundary."
                    )
            if last is not None:
                c = last.get("confidence")
                if c is not None and float(c) < CONFIDENCE_FLOOR:
                    errors.append(
                        f"segment[{i}]: ends on low-confidence word "
                        f"'{last.get('word', '?')}' (conf {float(c):.2f} < "
                        f"{CONFIDENCE_FLOOR:.2f}). Move the end to the previous "
                        "crisply-transcribed word boundary."
                    )

    if preset is not None:
        min_s = preset.min_segment_s
        max_s = preset.max_segment_s
        for i, seg in enumerate(plan.selected_clips):
            duration = seg.end_s - seg.start_s
            if duration < min_s:
                errors.append(
                    f"segment[{i}]: {duration:.1f}s is under the {min_s:.0f}s pacing floor "
                    f"for this {preset.label} preset — extend the block or drop it."
                )
            elif duration > max_s:
                errors.append(
                    f"segment[{i}]: {duration:.1f}s exceeds the {max_s:.0f}s pacing ceiling "
                    f"for this {preset.label} preset — split or tighten the block."
                )

    # Take-group dedup: each group is one line performed multiple times.
    # The prompt asks the Director to pick one per group; the validator
    # enforces it and also collapses duplicates into "virtual" units
    # before the coverage calculation runs (otherwise a 3-take group
    # inflates the eligible denominator).
    from ..analysis.take_dedup import detect_take_groups

    take_groups = detect_take_groups(transcript)
    clip_to_group: dict[int, int] = {}
    for gi, grp in enumerate(take_groups):
        for ci in grp:
            clip_to_group[ci] = gi

    if preset is not None:
        covered = _covered_clip_indexes(plan, transcript)
        # Reject when >1 clip from the same take-group is used.
        group_hits: dict[int, list[int]] = {}
        for ci in covered:
            gi = clip_to_group.get(ci)
            if gi is None:
                continue
            group_hits.setdefault(gi, []).append(ci)
        for gi, hits in group_hits.items():
            if len(hits) > 1:
                errors.append(
                    f"take-group {gi + 1}: plan uses clip_index {sorted(hits)} "
                    f"from the same duplicate-take group. Pick ONE clip from "
                    f"this group and drop spans from the others."
                )

        eligible = _eligible_clip_indexes(transcript)
        if len(eligible) >= 2:
            # Collapse take-groups so a 3-take group counts as 1 unit in
            # both numerator and denominator.
            def _unit(ci: int) -> tuple[int, int]:
                return (0, clip_to_group[ci]) if ci in clip_to_group else (1, ci)

            eligible_units = {_unit(ci) for ci in eligible}
            covered_units = {_unit(ci) for ci in covered & eligible}
            touched = len(covered_units)
            ratio = touched / len(eligible_units)
            log.info(
                "director: per-clip coverage %d/%d (%.0f%%, threshold %.0f%%, "
                "%d take-group(s) collapsed)",
                touched,
                len(eligible_units),
                ratio * 100,
                COVERAGE_THRESHOLD * 100,
                len(take_groups),
            )
            if ratio < COVERAGE_THRESHOLD:
                missing_units = eligible_units - covered_units
                missing_clips = sorted(clip_id for kind, clip_id in missing_units if kind == 1)
                missing_groups = sorted(gi for kind, gi in missing_units if kind == 0)
                hint_parts = []
                if missing_clips:
                    hint_parts.append(f"clip_index {missing_clips[:5]}")
                if missing_groups:
                    hint_parts.append(
                        f"take-group(s) {sorted(missing_groups)} (pick any one clip from each)"
                    )
                errors.append(
                    f"per-clip coverage: touched {touched}/{len(eligible_units)} units "
                    f"({ratio * 100:.0f}%, threshold {COVERAGE_THRESHOLD * 100:.0f}%). "
                    f"Add segments from {' / '.join(hint_parts)} "
                    "so every substantial clip the editor placed appears at least once."
                )

    if target_length_s and target_length_s > 0:
        target_segment_s = preset.target_segment_s if preset else 22.0
        low, high, _min_segs_unused, avg_span = _duration_budget(target_length_s, target_segment_s)
        total = sum(max(0.0, seg.end_s - seg.start_s) for seg in plan.selected_clips)
        log.info(
            "director: plan total=%.1fs target=%.0fs floor=%.1fs ceiling=%.1fs segments=%d",
            total,
            target_length_s,
            low,
            high,
            len(plan.selected_clips),
        )
        # Count bound — Gemini treats schema ``maxItems`` as advisory on
        # flash-lite, so enforce it here. Both ends are ±25 % of the
        # pre-solved ideal count (same tolerance the duration window uses),
        # so plan size and plan length get policed on matching bands.
        ideal_n = target_length_s / max(target_segment_s, 1.0)
        min_count = max(2, round(ideal_n * 0.75))
        max_segments = max(min_count + 1, round(ideal_n * 1.25))
        n = len(plan.selected_clips)
        if n < min_count:
            errors.append(
                f"segment count {n} is below the {min_count}-segment floor "
                f"for a {target_length_s:.0f}s target with ~{target_segment_s:.0f}s "
                f"pacing. Add {min_count - n} more segment(s)."
            )
        elif n > max_segments:
            errors.append(
                f"segment count {n} exceeds the {max_segments}-segment ceiling "
                f"for a {target_length_s:.0f}s target with ~{target_segment_s:.0f}s "
                f"pacing. Drop the {n - max_segments} weakest segment(s)."
            )
        if total < low:
            deficit = target_length_s - total
            needed_extra = max(1, round(deficit / max(avg_span, 1.0)))
            errors.append(
                f"plan total {total:.1f}s is {deficit:.1f}s short of the "
                f"{target_length_s:.0f}s target (floor {low:.1f}s). "
                f"You have {len(plan.selected_clips)} segments; "
                f"add ~{needed_extra} MORE segments of ~{avg_span:.0f}s each "
                f"(from currently-unused parts of the transcript) so the "
                f"total reaches ~{target_length_s:.0f}s. Do NOT lengthen "
                f"existing segments past the pacing bounds — add new ones."
            )
        elif total > high:
            excess = total - target_length_s
            drop_n = max(1, round(excess / max(avg_span, 1.0)))
            errors.append(
                f"plan total {total:.1f}s is {excess:.1f}s over the "
                f"{target_length_s:.0f}s target (ceiling {high:.1f}s). "
                f"Drop the {drop_n} weakest segment(s) so the total lands "
                f"at ~{target_length_s:.0f}s. Do NOT just shorten existing "
                f"segments past the min-pacing bound."
            )

    # Reorder policy: hook is exempt (floated to position 0). Non-hook
    # segments must obey the preset's reorder_mode.
    mode = getattr(preset, "reorder_mode", "free") if preset is not None else "free"
    if mode in ("locked", "preserve_macro") and len(plan.selected_clips) > 1:
        non_hook = [(i, seg) for i, seg in enumerate(plan.selected_clips) if i != plan.hook_index]
        if mode == "locked":
            prev_start = -1.0
            for i, seg in non_hook:
                if seg.start_s < prev_start:
                    errors.append(
                        f"segment[{i}] starts at {seg.start_s:.2f}s but the "
                        f"previous non-hook segment started at {prev_start:.2f}s. "
                        f"This preset's reorder policy is LOCKED — non-hook "
                        f"segments must play in ascending source-time order."
                    )
                    break
                prev_start = seg.start_s
        elif mode == "preserve_macro" and chapters:

            def chapter_of(t: float) -> int:
                for ci, ch in enumerate(chapters):
                    if ch["start_s"] <= t < ch["end_s"]:
                        return ci
                return -1

            prev_chapter = -1
            for i, seg in non_hook:
                this_chapter = chapter_of(seg.start_s)
                if this_chapter == -1:
                    continue  # segment outside any chapter — ignore
                if this_chapter < prev_chapter:
                    errors.append(
                        f"segment[{i}] is from chapter {this_chapter + 1} but "
                        f"the previous non-hook segment was from chapter "
                        f"{prev_chapter + 1}. This preset's reorder policy is "
                        f"PRESERVE_MACRO — chapters must play in source order."
                    )
                    break
                prev_chapter = max(prev_chapter, this_chapter)

    return errors


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def _user_settings_block(user_settings: dict | None) -> str:
    """Render HIL settings (length + themes) as a markdown block.

    Exclusion categories and custom focus are rendered by dedicated helpers
    below so the Director prompt can address them with stronger instructions.
    """
    if not user_settings:
        return "(no user overrides — use preset defaults)"
    lines: list[str] = []
    if tgt := user_settings.get("target_length_s"):
        mins = tgt / 60.0
        lines.append(f"- Target length: ~{mins:.1f} minutes")
    if themes := user_settings.get("themes"):
        lines.append(f"- Prioritized themes: {', '.join(themes)}")
    if not lines:
        lines.append("(no user overrides)")
    return "\n".join(lines)


def _exclude_block(
    preset: PresetBundle,
    user_settings: dict | None,
) -> str:
    """Render EXCLUDE CATEGORIES as a markdown block, or empty string.

    Cross-references the keys the user ticked on the Configure screen
    against the preset's declared category definitions so the Director
    receives the full human description (not just the snake_case key).
    Unknown keys are silently dropped — this is the wire contract between
    the UI and the Director, not a place to surface UI bugs.
    """
    if not user_settings:
        return ""
    selected_keys = user_settings.get("exclude_categories") or []
    if not selected_keys:
        return ""

    key_to_cat = {c.key: c for c in preset.exclude_categories}
    rendered: list[str] = []
    for key in selected_keys:
        cat = key_to_cat.get(key)
        if cat is None:
            continue
        rendered.append(f"- **{cat.label}** — {cat.description}")
    if not rendered:
        return ""

    header = (
        "EXCLUDE CATEGORIES — the editor has ticked these boxes. "
        "Drop any block whose primary content falls into one of these "
        "categories, even if the words are otherwise on-topic. When the "
        "transcript briefly touches an excluded category inside an "
        "otherwise valuable block, tighten the block's start/end around "
        "the keep-worthy words rather than taking the whole block."
    )
    return f"{header}\n" + "\n".join(rendered)


def _speaker_block(
    preset: PresetBundle,
    transcript: list[dict],
    user_settings: dict | None,
) -> str:
    """Render SPEAKER GUIDANCE as a markdown block, or empty string.

    Only emits when the preset carries a non-empty ``speaker_awareness``
    fragment AND the transcript actually contains at least two distinct
    speakers. Single-speaker content (vlog-to-camera, tutorial voiceover)
    skips the block even on the Interview preset, because the guidance
    talks about "the interviewer" vs "the guest" and would be noise.

    The speaker list shown to the model reflects any user-supplied labels
    (e.g. ``S1 → Host``) so the prompt reads in human terms.
    """
    awareness = (preset.speaker_awareness or "").strip()
    if not awareness:
        return ""

    # Build the roster from whatever labels the user applied — the caller
    # will have relabelled the transcript before serialising it, so the
    # block must agree with the serialised form.
    labels = (user_settings or {}).get("speaker_labels") or None
    relabeled = apply_speaker_labels(transcript, labels)
    speakers = detect_speakers(relabeled)
    if len(speakers) < 2:
        return ""

    counts = speaker_stats(relabeled)
    roster = "\n".join(f"- **{sid}** — {counts.get(sid, 0)} words" for sid in speakers)
    header = (
        "SPEAKER GUIDANCE — each word in the transcript carries a "
        "`speaker_id`. Use it to make better keep/drop choices per the "
        "rules below."
    )
    return f"{header}\n{roster}\n\n{awareness}"


def _maybe_relabel_transcript(
    transcript: list[dict],
    user_settings: dict | None,
) -> list[dict]:
    """Return the transcript with user-supplied speaker labels applied.

    Thin wrapper so every Director-facing serialisation path flows through
    the same helper — the SPEAKER GUIDANCE block and the JSON transcript
    must both show the same labels or the model gets confused.
    """
    labels = (user_settings or {}).get("speaker_labels") or None
    return apply_speaker_labels(transcript, labels)


def _slim_transcript_for_prompt(transcript: list[dict]) -> list[dict]:
    """Drop ``clip_metadata`` + ``shot_tag`` off every word before JSON-serialising.

    v2-6 attaches full clip metadata to every word for the pipeline's
    internal use. When the transcript hits the Director prompt though, the
    CLIP METADATA table at the top of the prompt already carries
    ``source_name`` / ``duration_s`` / ``timeline_offset_s`` once per
    clip — repeating that on each of ~1,000 words inflates the payload by
    roughly 3×. The ``clip_index`` integer stays so the Director can still
    cross-reference words back to the table.

    v4 Layer C / Layer Audio do the same for ``shot_tag`` + ``audio_cue``
    — the SHOT TAGS block and AUDIO CUES block carry significant signal
    once per range / per significant word, so keeping them on every word
    of the JSON transcript is pure duplication.

    Words without these fields pass through unchanged — v1 runs, the
    concat STT path, and sensory-off builds all satisfy that.
    """
    drop = ("clip_metadata", "shot_tag", "audio_cue")
    out: list[dict] = []
    for w in transcript:
        if not any(k in w for k in drop):
            out.append(w)
            continue
        out.append({k: v for k, v in w.items() if k not in drop})
    return out


def _shape_for_prompt(
    transcript: list[dict],
    user_settings: dict | None,
) -> list[dict]:
    """Relabel + slim — the two transforms every Director-facing transcript needs."""
    transcript = _maybe_relabel_transcript(transcript, user_settings)
    return _slim_transcript_for_prompt(transcript)


def _clip_metadata_block(transcript: list[dict]) -> str:
    """Render a CLIP METADATA block when words carry ``clip_metadata`` (v2-6).

    Per-clip STT (``UserSettings.per_clip_stt=True``) annotates every word
    with ``clip_index`` + ``clip_metadata``; older analyze runs don't.
    When metadata is absent this returns an empty string and the block is
    skipped — same wire contract as the exclude / focus / speaker blocks.
    """
    table = clip_metadata_table(transcript)
    if not table:
        return ""
    return (
        "CLIP METADATA — each word below carries a `clip_index` linking "
        "it to one of these source clips. Treat short, dated, or one-off "
        "clips as more editorially 'precious' than long meandering clips; "
        "consider the clip's position when deciding structure.\n"
        f"{table}"
    )


# v4 Layer C: hard cap on how many rows the SHOT TAGS block can carry
# before the overflow summary kicks in. On a 60-min source, coalesced
# ranges typically land around 50-120 — 150 is the safety valve that
# keeps the prompt bounded even when shot variety spikes.
_SHOT_TAG_MAX_ROWS = 150


def _shot_tag_block(transcript: list[dict]) -> str:
    """Render a SHOT TAGS block when words carry ``shot_tag`` (v4 Layer C).

    Coalesces consecutive words that share the same tag identity into a
    single row so a 10,000-word transcript yields ~50-150 rows, not one
    per word. Identity = ``(item_index, timeline_ts_s)`` — the tuple the
    shot_tagger writes when it attaches tags. Words without ``shot_tag``
    cause an empty string return (same contract as the other optional
    blocks — sensory-off runs emit nothing).

    The format mirrors the v4 proposal example. Missing / unknown fields
    are omitted row-by-row so the row stays compact when the tagger
    didn't have signal (e.g. framing=unknown).

    When row count exceeds ``_SHOT_TAG_MAX_ROWS``, the tail is summarised
    as "... N more ranges omitted" — the prompt stays bounded.
    """
    tagged_words = [w for w in transcript if w.get("shot_tag")]
    if not tagged_words:
        return ""

    # Coalesce by (item_index, timeline_ts_s). Track word index within the
    # passed-in transcript so the row label is "words A-B" against the
    # same ordering the prompt sees.
    rows: list[tuple[int, int, dict]] = []
    current_key: tuple | None = None
    current_start = 0
    current_tag: dict | None = None

    for idx, word in enumerate(transcript):
        tag = word.get("shot_tag")
        if tag is None:
            # Untagged word breaks the current run. Close out any open run.
            if current_key is not None and current_tag is not None:
                rows.append((current_start, idx - 1, current_tag))
                current_key = None
                current_tag = None
            continue

        key = (tag.get("item_index"), tag.get("timeline_ts_s"))
        if key != current_key:
            if current_key is not None and current_tag is not None:
                rows.append((current_start, idx - 1, current_tag))
            current_key = key
            current_start = idx
            current_tag = tag

    if current_key is not None and current_tag is not None:
        rows.append((current_start, len(transcript) - 1, current_tag))

    if not rows:
        return ""

    # Drop rows where every tag field is unknown / default — they add noise
    # without carrying signal.
    def _has_signal(tag: dict) -> bool:
        if tag.get("shot_type") not in (None, "unknown"):
            return True
        if tag.get("framing") not in (None, "unknown"):
            return True
        if tag.get("gesture_intensity") not in (None, "unknown"):
            return True
        if int(tag.get("visual_energy") or 0) > 0:
            return True
        return bool(tag.get("notable"))

    rows = [r for r in rows if _has_signal(r[2])]
    if not rows:
        return ""

    lines = [
        "SHOT TAGS (per word range, derived from Gemini vision pass):",
        "",
    ]

    truncated = max(0, len(rows) - _SHOT_TAG_MAX_ROWS)
    for a, b, tag in rows[:_SHOT_TAG_MAX_ROWS]:
        label = f"words {a}-{b}"
        parts: list[str] = []
        item_idx = tag.get("item_index")
        if item_idx is not None:
            parts.append(f"item={item_idx}")
        for field_name, prefix in (
            ("shot_type", "shot"),
            ("framing", "framing"),
            ("gesture_intensity", "gest"),
        ):
            val = tag.get(field_name)
            if val and val != "unknown":
                parts.append(f"{prefix}={val}")
        energy = tag.get("visual_energy")
        if energy is not None and int(energy) > 0:
            parts.append(f"energy={int(energy)}")
        notable = tag.get("notable")
        if notable:
            parts.append(f'"{notable}"')
        lines.append(f"  {label:<16} " + "  ".join(parts))

    if truncated:
        lines.append(f"  ... {truncated} more ranges omitted")

    lines.append("")
    lines.append(
        "Prefer: not cutting mid-emphatic-gesture · alternating shot types · "
        "opening on higher visual_energy for hook impact. These tags are "
        "advisory — narrative / pacing / transcript semantics still win."
    )
    return "\n".join(lines)


# v4 Layer Audio block — significance thresholds + hard row cap. Kept in
# sync with audio_cues.SIGNIFICANT_PAUSE_MS but redeclared here so the
# renderer doesn't import ffmpeg-adjacent code at Director import time.
_AUDIO_CUE_PAUSE_MS_FLOOR = 600
_AUDIO_CUE_RMS_DELTA_FLOOR = 4.0
_AUDIO_CUE_MAX_ROWS = 120


def _audio_cue_block(transcript: list[dict], *, mode: str | None = None) -> str:
    """Render AUDIO CUES when words carry ``audio_cue`` (v4 Layer Audio).

    Shows only the SIGNIFICANT cues — natural endpoints (big
    pause_after or silence_tail) and hard resets (big pause_before or
    noticeable RMS drop). A typical 10-minute scrubbed transcript
    yields ~30-80 rows; anything beyond :data:`_AUDIO_CUE_MAX_ROWS`
    truncates with a summary line.

    ``mode`` optionally tailors the "Prefer:" footer. Per the v4
    activation matrix, Assembled + Short Generator get pause-aware
    guidance by default; raw_dump / curated / rough_cut get the
    generic hint. Unknown / missing mode → generic hint.

    Words without ``audio_cue`` cause an empty-string return (same
    contract as the other optional blocks).
    """
    rows: list[tuple[int, dict, str]] = []
    for idx, w in enumerate(transcript):
        cue = w.get("audio_cue")
        if not cue:
            continue
        before = int(cue.get("pause_before_ms") or 0)
        after = int(cue.get("pause_after_ms") or 0)
        delta = float(cue.get("rms_db_delta") or 0.0)
        tail = bool(cue.get("is_silence_tail"))

        significant = (
            before >= _AUDIO_CUE_PAUSE_MS_FLOOR
            or after >= _AUDIO_CUE_PAUSE_MS_FLOOR
            or tail
            or abs(delta) >= _AUDIO_CUE_RMS_DELTA_FLOOR
        )
        if not significant:
            continue

        # Short reason annotation helps the model pick the right cut
        # intent at a glance without re-parsing the numbers.
        reasons: list[str] = []
        if tail and after >= _AUDIO_CUE_PAUSE_MS_FLOOR:
            reasons.append("natural endpoint — cut candidate")
        elif tail:
            reasons.append("silence tail — cut candidate")
        elif after >= _AUDIO_CUE_PAUSE_MS_FLOOR:
            reasons.append("trailing pause — cut candidate")
        if before >= _AUDIO_CUE_PAUSE_MS_FLOOR:
            reasons.append("hard reset — cut candidate")
        if abs(delta) >= _AUDIO_CUE_RMS_DELTA_FLOOR:
            reasons.append(f"volume shift {'+' if delta >= 0 else ''}{delta:.1f} dB")

        rows.append((idx, cue, " · ".join(reasons)))

    if not rows:
        return ""

    lines = [
        "AUDIO CUES (derived from signal — only significant cues shown):",
        "",
    ]

    truncated = max(0, len(rows) - _AUDIO_CUE_MAX_ROWS)
    for idx, cue, reason in rows[:_AUDIO_CUE_MAX_ROWS]:
        word = transcript[idx].get("word", "?")
        parts: list[str] = [f'word {idx} "{word}"']
        pb = int(cue.get("pause_before_ms") or 0)
        pa = int(cue.get("pause_after_ms") or 0)
        if pb:
            parts.append(f"pause_before={pb}ms")
        if pa:
            parts.append(f"pause_after={pa}ms")
        if cue.get("is_silence_tail"):
            parts.append("is_silence_tail=true")
        delta = float(cue.get("rms_db_delta") or 0.0)
        if abs(delta) >= _AUDIO_CUE_RMS_DELTA_FLOOR:
            parts.append(f"rms_delta={delta:+.1f}dB")
        head = "  " + "  ".join(parts)
        if reason:
            head = f"{head:<70} ({reason})"
        lines.append(head)

    if truncated:
        lines.append(f"  ... {truncated} more cues omitted")

    lines.append("")
    lines.append(_audio_cue_footer(mode))
    return "\n".join(lines)


def _audio_cue_footer(mode: str | None) -> str:
    """Mode-aware guidance lines appended to the AUDIO CUES block."""
    base = (
        "Prefer: cutting on natural endpoints (pause_after ≥ 400ms or "
        "is_silence_tail=true). Avoid cutting on words flagged as hard "
        "resets (pause_before > 800ms) unless you're opening a new beat."
    )
    if mode == "assembled":
        return (
            base + " Assembled mode: tighten every pause_before / pause_after "
            "> 800ms unless it lands on a narrative beat — the editor "
            "already locked shot order, pause trimming is the main "
            "signal you contribute."
        )
    if mode == "short_generator":
        return (
            base + " Short Generator: every span transition should land on a "
            "beat boundary — align span starts to is_silence_tail cues "
            "where the transcript allows."
        )
    return base


def _boundary_rejections_block(user_settings: dict | None) -> str:
    """Render BOUNDARY REJECTIONS when the outer validator loop retried.

    Layer A (:mod:`analysis.boundary_validator`) flags cuts that land on
    visually disruptive frame pairs. The validator loop
    (:mod:`core.validator_loop`) re-invokes the Director with those
    rejections injected via ``user_settings["_boundary_rejections"]``.

    For multi-candidate plans (Short Generator) the loop also sets
    ``user_settings["_candidate_roster"]`` so the retry carries the
    previous attempt's candidate themes + engagement order. Locking
    the roster prevents the model from reshuffling candidates between
    retries, which would make per-candidate rejections meaningless.

    Wire contract: both keys are internal — ``_user_settings_block``
    doesn't read them, so they never leak into the USER SETTINGS
    summary. Empty / missing → empty block (first attempt + sensory-off
    runs).
    """
    if not user_settings:
        return ""
    rejections = user_settings.get("_boundary_rejections") or []
    roster = user_settings.get("_candidate_roster") or []
    if not rejections:
        return ""

    # Multi-candidate mode is active when any rejection carries a
    # non-default candidate_index OR the caller supplied a roster.
    multi_candidate = bool(roster) or any((r.get("candidate_index") or 0) != 0 for r in rejections)

    lines = [
        "BOUNDARY REJECTIONS — your previous plan contained visually jarring cuts:",
        "",
    ]

    if multi_candidate and roster:
        lines.append(
            "Previous attempt's candidate roster — KEEP these themes and their "
            "engagement order on retry:"
        )
        for entry in roster:
            try:
                idx = int(entry.get("candidate_index", 0))
            except (TypeError, ValueError):
                idx = 0
            theme = (entry.get("theme") or "").strip() or "(no theme)"
            lines.append(f'  candidate {idx}: "{theme}"')
        lines.append("")

    for r in rejections:
        try:
            cut_index = int(r.get("cut_index", 0))
        except (TypeError, ValueError):
            cut_index = 0
        reason = (r.get("reason") or "").strip() or "(no reason supplied)"
        if multi_candidate:
            try:
                cand_idx = int(r.get("candidate_index") or 0)
            except (TypeError, ValueError):
                cand_idx = 0
            prefix = f"candidate {cand_idx}, cut {cut_index}"
        else:
            prefix = f"cut {cut_index}"
        lines.append(f"  {prefix}: {reason}")
        suggestion = (r.get("suggestion") or "").strip()
        if suggestion:
            lines.append(f"    suggestion: {suggestion}")

    lines.append("")
    if multi_candidate:
        lines.append(
            "For each rejected cut above: keep the same candidate (same theme, "
            "same engagement rank) but pick DIFFERENT word boundaries for that "
            "specific span transition. Candidates + cuts not listed above may "
            "stay as-is. Do NOT reshuffle candidate order or rewrite themes."
        )
    else:
        lines.append(
            "Pick DIFFERENT word boundaries for the cuts above. The transcript has "
            "other defensible in/out points — find them. Cuts not listed above may "
            "stay as-is. Do NOT just return the same plan."
        )
    return "\n".join(lines)


def _shape_takes_for_prompt(
    takes: list[dict],
    user_settings: dict | None,
) -> list[dict]:
    """Assembled-mode counterpart to ``_shape_for_prompt``.

    Rewrites each take's embedded transcript to apply user speaker labels
    (so the SPEAKER GUIDANCE roster and the serialised words agree) and
    strips redundant ``clip_metadata`` off each word — the CLIP METADATA
    table the prompt renders at the top already carries file / duration /
    offset once per clip.
    """
    labels = (user_settings or {}).get("speaker_labels") or None
    out: list[dict] = []
    for take in takes:
        words = take.get("transcript") or []
        words = apply_speaker_labels(words, labels)
        words = _slim_transcript_for_prompt(words)
        new_take = dict(take)
        new_take["transcript"] = words
        out.append(new_take)
    return out


def _focus_block(user_settings: dict | None) -> str:
    """Render USER FOCUS as a markdown block, or empty string."""
    if not user_settings:
        return ""
    focus = (user_settings.get("custom_focus") or "").strip()
    if not focus:
        return ""
    return (
        "USER FOCUS — treat this as a soft priority: when two candidate "
        "blocks compete for the same slot, prefer the one that serves "
        "the focus. Do NOT force content in if the transcript doesn't "
        f'support it.\n"{focus}"'
    )


def _selected_hook_block(user_settings: dict | None) -> str:
    """Render the SELECTED HOOK block when the editor picked one in the UI."""
    if not user_settings:
        return ""
    raw = user_settings.get("selected_hook_s")
    if raw is None:
        return ""
    try:
        hook_at_s = float(raw)
    except (TypeError, ValueError):
        return ""
    return f"""### SELECTED HOOK — NON-NEGOTIABLE
The editor chose the quote that begins at **{hook_at_s:.2f}s** as the hook \
of this cut. Your first `selected_clip` MUST start within 2.0 seconds of \
{hook_at_s:.2f}s (window: [{max(0, hook_at_s - 2):.2f}s, \
{hook_at_s + 2:.2f}s]). Build the rest of the narrative outward from that \
opening beat. Do not substitute a different hook, even if you think \
another line would be stronger."""


def _target_length_recipe_block(
    user_settings: dict | None, preset: PresetBundle | None = None
) -> str:
    """Render the NON-NEGOTIABLE target-length recipe block, or empty string.

    Only emits when ``target_length_s`` is set. Pre-solves the segment-count
    arithmetic using the preset's ``target_segment_s`` so the recipe agrees
    with the structured-pacing bounds rendered elsewhere in the prompt.
    """
    if not user_settings:
        return ""
    target = user_settings.get("target_length_s")
    if not target or float(target) <= 0:
        return ""
    target = float(target)
    target_segment_s = preset.target_segment_s if preset else 22.0
    min_segment_s = preset.min_segment_s if preset else 3.0
    max_segment_s = preset.max_segment_s if preset else 40.0
    low, high, min_segments, avg_span = _duration_budget(target, target_segment_s)
    return f"""### TARGET LENGTH — NON-NEGOTIABLE
The final cut runs **{target:.0f} seconds** on screen. Not half that. Not a \
minute-viable highlight. **{target:.0f}s.** The editor is paying to fill \
the full runtime with the best moments available — a cut that comes in \
under {low:.0f}s is a failed deliverable.

### RECIPE — follow exactly to hit the target:
- Pick **at least {min_segments} segments**.
- Average segment length **~{avg_span:.0f} seconds** (individual segments \
{min_segment_s:.0f}–{max_segment_s:.0f}s are legal; aim for ~{target_segment_s:.0f}s).
- {min_segments} × {avg_span:.0f}s = {target:.0f}s. If your plan comes in \
below {low:.0f}s, you picked too few segments — go back and add more from \
the transcript.

If the transcript genuinely does not have {target:.0f}s of content worth \
keeping, still pick as close to {target:.0f}s as the material allows. Do \
not under-cut to protect editorial purity; the editor will drop segments \
they don't like in review."""


def _pacing_block(preset: PresetBundle | None) -> str:
    """Render structured pacing bounds as an explicit PACING block."""
    if preset is None:
        return ""
    return (
        "### PACING BOUNDS\n"
        f"Every individual segment must run between "
        f"**{preset.min_segment_s:.0f}s** and **{preset.max_segment_s:.0f}s**. "
        f"Aim for ~**{preset.target_segment_s:.0f}s** per segment — that's the "
        f"pacing this content type ({preset.label}) expects. "
        f"Segments shorter than {preset.min_segment_s:.0f}s feel like jump cuts; "
        f"longer than {preset.max_segment_s:.0f}s kills retention. "
        f"Style note: {preset.pacing}."
    )


def _reorder_mode_block(preset: PresetBundle | None, chapters: list[dict] | None = None) -> str:
    """Render REORDER POLICY block matching the preset's reorder_mode."""
    if preset is None:
        return ""
    mode = getattr(preset, "reorder_mode", "free")
    if mode == "free":
        return ""
    if mode == "locked":
        return (
            "### REORDER POLICY — LOCKED\n"
            "Place segments in the SAME source-time order they appear in the "
            "transcript. The hook is the only exception: you may float it to "
            "position 0 in the output even if it's not the earliest, but "
            "every subsequent segment must play in ascending source-time "
            "order. Do not reorder for pacing or drama."
        )
    if mode == "preserve_macro":
        ch_note = ""
        if chapters:
            ch_list = "\n".join(
                f"  - Chapter {i + 1}: {c['start_s']:.1f}s – {c['end_s']:.1f}s — {c.get('title', '')}"
                for i, c in enumerate(chapters)
            )
            ch_note = f"\nChapter boundaries:\n{ch_list}"
        return (
            "### REORDER POLICY — PRESERVE MACRO ORDER\n"
            "Chapters must play in source-time order (earlier chapters before "
            "later chapters). Within a chapter you may reorder individual "
            "segments for pacing. The hook is exempt — it always goes to "
            "position 0 in the output. Do not move content from a later "
            "chapter ahead of content from an earlier chapter."
            f"{ch_note}"
        )
    return ""


def _take_groups_block(transcript: list[dict]) -> str:
    """Render TAKE GROUPS rule when near-duplicate takes exist."""
    from ..analysis.take_dedup import detect_take_groups

    groups = detect_take_groups(transcript)
    if not groups:
        return ""
    lines = []
    for i, g in enumerate(groups, start=1):
        lines.append(f"  - Group {i}: clip_index {g} — duplicate takes of the same line")
    joined = "\n".join(lines)
    return (
        "### TAKE GROUPS — NON-NEGOTIABLE\n"
        "The editor put multiple takes of the same content on the timeline. "
        "Each group below is one line, performed more than once — you MUST "
        "use only ONE clip from each group (your pick). Using two or three "
        "clips from the same group produces a stuttering cut.\n"
        f"{joined}"
    )


def _coverage_block(transcript: list[dict]) -> str:
    """Render PER-CLIP COVERAGE rule when the transcript carries clip_index."""
    eligible = _eligible_clip_indexes(transcript)
    if len(eligible) < 2:
        return ""  # single-clip timelines or concat STT — skip the rule
    return (
        "### PER-CLIP COVERAGE — NON-NEGOTIABLE\n"
        f"The editor placed {len(eligible)} substantial source clips on the "
        "timeline (≥ 30 words each, listed in the CLIP METADATA table). "
        "Each was chosen deliberately — you must touch every eligible clip "
        "at least once with a segment unless:\n"
        "- its content is fully captured by an excluded category, OR\n"
        "- every word in it was scrubbed out as filler / dead air.\n"
        "Collapsing the cut onto 2–3 'best' clips and ignoring the rest is "
        "a failed deliverable — the editor will see the missing clips and "
        "reject the plan."
    )


def _eligible_clip_indexes(transcript: list[dict], min_words: int = 30) -> set[int]:
    """Return the set of clip_index values with ≥ ``min_words`` words."""
    counts: dict[int, int] = {}
    for w in transcript:
        ci = w.get("clip_index")
        if ci is None:
            continue
        counts[int(ci)] = counts.get(int(ci), 0) + 1
    return {ci for ci, n in counts.items() if n >= min_words}


def _covered_clip_indexes(plan: DirectorPlan, transcript: list[dict]) -> set[int]:
    """Return the set of clip_index values with at least one word inside a selected segment."""
    # Sort segments once; scan transcript linearly.
    intervals = sorted(
        ((s.start_s, s.end_s) for s in plan.selected_clips if s.end_s > s.start_s),
        key=lambda iv: iv[0],
    )
    covered: set[int] = set()
    for w in transcript:
        ci = w.get("clip_index")
        if ci is None:
            continue
        t = float(w.get("start_time", 0.0))
        for start_s, end_s in intervals:
            if start_s <= t < end_s:
                covered.add(int(ci))
                break
            if t < start_s:
                break
    return covered


def _prompt(preset: PresetBundle, transcript: list[dict], user_settings: dict | None) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    speakers = _speaker_block(preset, transcript, user_settings)
    clip_meta = _clip_metadata_block(transcript)
    shot_tags = _shot_tag_block(transcript)
    audio_cues = _audio_cue_block(transcript, mode="raw_dump")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b for b in (exclude, focus, speakers, clip_meta, shot_tags, audio_cues, boundary_rej) if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    recipe = _target_length_recipe_block(user_settings, preset)
    hook = _selected_hook_block(user_settings)
    pacing = _pacing_block(preset)
    coverage = _coverage_block(transcript)
    take_groups = _take_groups_block(transcript)
    chapters = (user_settings or {}).get("chapters") if user_settings else None
    reorder = _reorder_mode_block(preset, chapters)
    recipe_section = "\n\n".join(
        b for b in (hook, recipe, pacing, coverage, take_groups, reorder) if b
    )
    recipe_section = f"\n\n{recipe_section}" if recipe_section else ""
    relabelled = _maybe_relabel_transcript(transcript, user_settings)
    sentences = _coalesce_to_sentences(relabelled)
    return f"""You are a {preset.role}.

You will receive a SENTENCES array where each row is one sentence with an index `i`, speaker `spk`, time range `t=[t_start, t_end]` in seconds, and the sentence `text`. Your job is to select a contiguous RANGE of sentences (one or more) per CutSegment and emit the range's outer timestamps. Stitched together, the segments form a compelling cut.{recipe_section}

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. The hook's CutSegment becomes position 0 in the output, even if it's not the earliest in the transcript.
2. Every segment's duration must respect the PACING BOUNDS block above.
3. Do not alter, edit, paraphrase, or summarize ANY word. You may only select whole sentences.
4. For each CutSegment, `start_s` MUST equal `t[0]` of the first sentence in the range, and `end_s` MUST equal `t[1]` of the last sentence in the range. Never pick a timestamp that is not a sentence edge.
5. Segments must not overlap. Each sentence appears in at most one segment.

USER SETTINGS
{_user_settings_block(user_settings)}{optional_section}

SENTENCES (JSON array):
{json.dumps(sentences, separators=(",", ":"))}

Return a `DirectorPlan` with:
- `selected_clips`: the blocks in narrative order (hook first).
- `hook_index`: 0 (the hook is always first).
- `reasoning`: 1–2 sentences on the overall structure.
"""


def build_cut_plan(
    transcript: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> DirectorPlan:
    """Run the Director agent, retrying on verbatim-timestamp violations."""
    prompt = _prompt(preset, transcript, user_settings)
    target_length_s: float | None = None
    selected_hook_s: float | None = None
    chapters = None
    if user_settings:
        raw_t = user_settings.get("target_length_s")
        if raw_t:
            try:
                target_length_s = float(raw_t)
            except (TypeError, ValueError):
                target_length_s = None
        raw_h = user_settings.get("selected_hook_s")
        if raw_h is not None:
            try:
                selected_hook_s = float(raw_h)
            except (TypeError, ValueError):
                selected_hook_s = None
        chapters = user_settings.get("chapters")
    # Derive selected_clips length bounds so Gemini enforces the count
    # server-side. With a target length we compute min/max from the
    # preset's pacing; without one we only enforce a minimum of 2 (hook +
    # at least one body segment) to catch Directors that return a single-
    # segment plan on long source material.
    if target_length_s:
        expected = target_length_s / max(preset.target_segment_s, 1.0)
        min_n = max(2, round(expected * 0.75))
        max_n = max(min_n, round(expected * 1.25))
    else:
        min_n = 2
        max_n = None
    schema = _bounded_director_plan(min_n, max_n)
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=schema,
        validate=lambda plan: validate_plan(
            plan, transcript, target_length_s, selected_hook_s, preset, chapters
        ),
        temperature=0.4,
        max_retries=5,
        accept_best_effort=True,
    )


# ---------------------------------------------------------------------------
# Assembled-mode Director (v2-2)
# ---------------------------------------------------------------------------
#
# In assembled mode the editor has pre-cut the timeline into takes on V1.
# Boundaries are sacred: the Director never crosses them. What remains
# user-controllable (via UserSettings):
#
#   - scrubbing within a take (filler / dead-air cleanup) — the scrubbed
#     transcript comes in already-cleaned; Director picks word-index spans
#     from it.
#   - reordering whole takes (``reorder_allowed`` flag) — when false, the
#     server-side validator enforces the input order and the retry loop
#     re-prompts.
#
# Wire contract: the caller hands the Director a list of Take dicts shaped
# like :class:`AssembledTakeEntry`; the Director returns word-index spans
# so verbatim timestamp validation (the hard-won v1 safeguard) is bypassed
# entirely — spans reference positions in the same transcript the prompt
# showed, so there's no rounding surface.


class WordSpan(BaseModel):
    a: int = Field(..., ge=0, description="Inclusive start word-index into the take's transcript.")
    b: int = Field(..., ge=0, description="Inclusive end word-index into the take's transcript.")


class AssembledItemSelection(BaseModel):
    item_index: int = Field(
        ...,
        ge=0,
        description="0-based index into the input TAKES array.",
    )
    kept_word_spans: list[WordSpan] = Field(
        ...,
        description="Ranges of word indices to keep from this take. Non-overlapping, ascending.",
    )


class AssembledDirectorPlan(BaseModel):
    hook_index: int = Field(
        ...,
        description="Index into selections (0-based) identifying the hook take.",
    )
    selections: list[AssembledItemSelection]
    reasoning: str = Field(default="", description="1-2 sentences on overall structure.")


def _reorder_instruction(reorder_allowed: bool) -> str:
    if reorder_allowed:
        return (
            "You MAY reorder takes: return `selections` in the order the cut "
            "should play, with the hook's take first. You may drop takes that "
            "don't belong in the cut."
        )
    return (
        "You MUST NOT reorder takes: return `selections` with `item_index` "
        "values in strictly ascending order (the same order they appear in "
        "the input). You may still drop takes that don't belong in the cut. "
        "Hook is the take you want viewers to see first; set hook_index to "
        "identify which of the surviving selections plays that role, even "
        "though order stays fixed."
    )


def _assembled_prompt(
    preset: PresetBundle,
    takes: list[dict],
    user_settings: dict | None,
) -> str:
    """Render the assembled-mode prompt.

    ``takes`` shape per entry:
        {
          "item_index": int,
          "source_name": str,
          "start_s": float, "end_s": float,
          "transcript": [{"i": int, "word": str,
                          "start_time": float, "end_time": float,
                          "speaker_id": str}, ...]
        }
    """
    reorder_allowed = bool((user_settings or {}).get("reorder_allowed", True))
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    # Flatten all takes' transcripts for speaker detection — speakers span
    # takes, not just one item. Detection reads the same relabelled view the
    # JSON below will show the model, so the roster and the words agree.
    flat_words: list[dict] = []
    for t in takes:
        flat_words.extend(t.get("transcript") or [])
    speakers = _speaker_block(preset, flat_words, user_settings)
    clip_meta = _clip_metadata_block(flat_words)
    shot_tags = _shot_tag_block(flat_words)
    audio_cues = _audio_cue_block(flat_words, mode="assembled")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b for b in (exclude, focus, speakers, clip_meta, shot_tags, audio_cues, boundary_rej) if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    takes_for_prompt = _shape_takes_for_prompt(takes, user_settings)

    return f"""You are a {preset.role}.

The editor has pre-cut this timeline into takes on the video track. Each TAKE below is one timeline item — a self-contained clip. Your job is to choose which takes survive and which word-index spans inside each take are kept.

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. Set `hook_index` to the position of the hook take within your returned `selections` array.
2. Pacing: {preset.pacing}.
3. You MUST NOT merge material across takes. Every kept_word_span references word indices within ONE take's transcript.
4. kept_word_spans must reference valid `i` values from that take's transcript. Spans are inclusive on both ends: [a, b] keeps words i=a through i=b.
5. Within a take, spans must be non-overlapping and in ascending order of `a`.
6. Omit takes entirely when they don't belong in the cut — do NOT include empty `kept_word_spans` arrays.
7. {_reorder_instruction(reorder_allowed)}

USER SETTINGS
{_user_settings_block(user_settings)}{optional_section}

TAKES (JSON array):
{json.dumps(takes_for_prompt, separators=(",", ":"))}

Return an `AssembledDirectorPlan` with:
- `selections`: list of {{item_index, kept_word_spans}} entries in play order (hook's take at position `hook_index`).
- `hook_index`: index into selections (0-based).
- `reasoning`: 1–2 sentences on the overall structure.
"""


def validate_assembled_plan(
    plan: AssembledDirectorPlan,
    takes: list[dict],
    reorder_allowed: bool = True,
) -> list[str]:
    """Validate an assembled plan against the input takes.

    Checks:
      1. `selections` is non-empty.
      2. Every `item_index` corresponds to a real take.
      3. No take appears twice.
      4. Every span has a <= b and both indices are in-range for that take's transcript.
      5. Spans within a take are non-overlapping and in ascending order.
      6. `hook_index` is in range.
      7. When reorder_allowed is False, selections' item_index sequence is strictly ascending.
    """
    errors: list[str] = []
    if not plan.selections:
        return ["selections is empty — the Director must pick at least one take"]

    if not (0 <= plan.hook_index < len(plan.selections)):
        errors.append(
            f"hook_index {plan.hook_index} out of range for {len(plan.selections)} selections"
        )

    take_by_index = {t["item_index"]: t for t in takes}
    seen_takes: set[int] = set()
    prev_item_index: int | None = None

    for i, sel in enumerate(plan.selections):
        if sel.item_index in seen_takes:
            errors.append(f"selections[{i}]: item_index {sel.item_index} appears twice")
        seen_takes.add(sel.item_index)

        take = take_by_index.get(sel.item_index)
        if take is None:
            errors.append(
                f"selections[{i}]: item_index {sel.item_index} does not match any input take"
            )
            continue

        if (
            not reorder_allowed
            and prev_item_index is not None
            and sel.item_index <= prev_item_index
        ):
            errors.append(
                f"selections[{i}]: item_index {sel.item_index} breaks input order "
                f"(must be > {prev_item_index}; reorder_allowed=false)"
            )
        prev_item_index = sel.item_index

        if not sel.kept_word_spans:
            errors.append(
                f"selections[{i}]: kept_word_spans is empty — drop the take entirely instead"
            )
            continue

        transcript_len = len(take.get("transcript") or [])
        if transcript_len == 0:
            errors.append(f"selections[{i}]: take {sel.item_index} has no transcript")
            continue

        last_b = -1
        for j, span in enumerate(sel.kept_word_spans):
            if span.a > span.b:
                errors.append(f"selections[{i}].spans[{j}]: a={span.a} > b={span.b}")
                continue
            if span.a >= transcript_len or span.b >= transcript_len:
                errors.append(
                    f"selections[{i}].spans[{j}]: [{span.a},{span.b}] "
                    f"out of range for take with {transcript_len} words"
                )
                continue
            if span.a <= last_b:
                errors.append(
                    f"selections[{i}].spans[{j}]: start a={span.a} overlaps previous span end {last_b}"
                )
            last_b = span.b

    return errors


def build_assembled_cut_plan(
    takes: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> AssembledDirectorPlan:
    """Run the assembled-mode Director, retrying on structural violations."""
    reorder_allowed = bool((user_settings or {}).get("reorder_allowed", True))
    prompt = _assembled_prompt(preset, takes, user_settings)
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=AssembledDirectorPlan,
        validate=lambda plan: validate_assembled_plan(plan, takes, reorder_allowed),
        temperature=0.4,
        max_retries=5,
        accept_best_effort=True,
    )


# ---------------------------------------------------------------------------
# Clip Hunter Director (v2-4)
# ---------------------------------------------------------------------------
#
# Different optimisation target: the Director returns N candidate clips,
# not one narrative cut. Each candidate is a self-contained, engagement-
# dense moment — a viewer with zero context should grasp it. The user
# then picks one (or, eventually, all) to execute into separate cut
# timelines.
#
# Verbatim timestamps still apply — candidates cite real word start/end
# times. The validator additionally enforces non-overlap, per-candidate
# duration bounds, and rank-order (highest engagement first).


class ClipCandidate(BaseModel):
    start_s: float = Field(
        ..., description="Start of the candidate on the source timeline, in seconds."
    )
    end_s: float = Field(
        ..., description="End of the candidate on the source timeline, in seconds."
    )
    quote: str = Field(
        default="",
        description="The key line that anchors the clip — used in the Review tabs.",
    )
    engagement_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Director's confidence this clip is viral-worthy (0–1, higher is better).",
    )
    suggested_caption: str = Field(
        default="",
        description="Short social caption (≤120 chars) the user can copy to their upload.",
    )
    reasoning: str = Field(
        default="",
        description="One sentence on why this moment was picked.",
    )


class ClipHunterPlan(BaseModel):
    candidates: list[ClipCandidate] = Field(
        ...,
        description="Clip candidates in descending engagement order. First entry is the top pick.",
    )
    reasoning: str = Field(
        default="",
        description="Brief overall note on how candidates were chosen.",
    )


def _themes_block(user_settings: dict | None) -> str:
    """Render DETECTED THEMES as a markdown block, or empty string.

    The theme-analysis stage populates ``user_settings.themes`` with topic
    tags for the episode. For Clip Hunter in particular, these act as
    semantic anchors: candidates touching detected themes are more likely
    to resonate with the episode's audience than random "viral" picks.
    """
    if not user_settings:
        return ""
    themes = user_settings.get("themes") or []
    if not themes:
        return ""
    header = (
        "DETECTED THEMES — the theme analyser surfaced these topics from "
        "the episode. Candidates that touch one or more of these themes "
        "are preferred over candidates about peripheral topics. Use the "
        "themes as ranking signal, not as a hard filter."
    )
    return header + "\n" + "\n".join(f"- {t}" for t in themes)


def _clip_hunter_prompt(
    preset: PresetBundle,
    transcript: list[dict],
    user_settings: dict | None,
    target_clip_length_s: float,
    num_clips: int,
) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    themes = _themes_block(user_settings)
    speakers = _speaker_block(preset, transcript, user_settings)
    clip_meta = _clip_metadata_block(transcript)
    shot_tags = _shot_tag_block(transcript)
    audio_cues = _audio_cue_block(transcript, mode="clip_hunter")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b
        for b in (
            exclude,
            focus,
            themes,
            speakers,
            clip_meta,
            shot_tags,
            audio_cues,
            boundary_rej,
        )
        if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    transcript_for_prompt = _shape_for_prompt(transcript, user_settings)
    low = target_clip_length_s * 0.6
    high = target_clip_length_s * 1.4
    return f"""You are a {preset.role}.

You will receive a transcript array. Your job is to surface the {num_clips} most viral-worthy, self-contained moments as a ranked list of clip candidates.

RULES — follow exactly:
1. Each candidate must be {low:.0f}–{high:.0f} seconds long (target {target_clip_length_s:.0f} s).
2. Each candidate must be self-contained: a viewer with zero context must grasp the moment without the rest of the recording.
3. Candidates must NOT overlap each other — cover different regions of the transcript.
4. Return candidates in descending engagement order (the strongest pick at index 0).
5. For each candidate, `start_s` MUST equal the `start_time` of the first word in the clip, and `end_s` MUST equal the `end_time` of the last word. Do not round, truncate, or invent timestamps.
6. {preset.hook_rule} — use that heuristic to rank engagement.
7. Pacing note: {preset.pacing}.
8. `quote` should be 4–10 words drawn from the clip — use it to identify the moment.
9. `suggested_caption` ≤ 120 characters, ready to paste on TikTok / Shorts / Reels.
10. Prefer candidates that touch the DETECTED THEMES block (when present) — they reflect the episode's real subject matter, not just surface-level drama.

USER SETTINGS
{_user_settings_block(user_settings)}
- Target clip length: {target_clip_length_s:.0f} s
- Number of candidates: {num_clips}{optional_section}

TRANSCRIPT (JSON array):
{json.dumps(transcript_for_prompt, separators=(",", ":"))}

Return a `ClipHunterPlan` with:
- `candidates`: list of {num_clips} entries, ranked by engagement (descending).
- `reasoning`: 1-2 sentences on how you chose, referencing which detected themes each top candidate covers.
"""


def validate_clip_hunter_plan(
    plan: ClipHunterPlan,
    transcript: list[dict],
    target_clip_length_s: float,
    num_clips: int,
    duration_tolerance: float = 0.4,
) -> list[str]:
    """Validate a ClipHunterPlan against the transcript.

    Checks:
      1. ``candidates`` has exactly ``num_clips`` entries (accept N-1 to N+1
         to tolerate Gemini off-by-ones; reject anything further off).
      2. Each ``start_s`` / ``end_s`` matches a word boundary (verbatim).
      3. Positive duration within ``(1 - tol)``…``(1 + tol)`` of target.
      4. Candidates are non-overlapping on the source timeline.
      5. Engagement scores are in [0, 1] (Pydantic enforces; we also check
         monotone non-increase so "rank-order" is real).
    """
    starts, ends = _build_timestamp_sets(transcript)
    errors: list[str] = []

    if not plan.candidates:
        return ["candidates is empty — the Director must produce at least one clip"]

    # 1. count leniency
    if abs(len(plan.candidates) - num_clips) > 1:
        errors.append(f"expected {num_clips} candidates (±1), got {len(plan.candidates)}")

    low = target_clip_length_s * (1.0 - duration_tolerance)
    high = target_clip_length_s * (1.0 + duration_tolerance)

    sorted_by_start = sorted(plan.candidates, key=lambda c: c.start_s)
    for i, cand in enumerate(plan.candidates):
        if cand.end_s <= cand.start_s:
            errors.append(f"candidate[{i}]: end_s {cand.end_s} must be > start_s {cand.start_s}")
            continue
        duration = cand.end_s - cand.start_s
        if not (low <= duration <= high):
            errors.append(
                f"candidate[{i}]: duration {duration:.1f}s outside target "
                f"range [{low:.1f}, {high:.1f}]s"
            )
        if not _close_to_any(cand.start_s, starts):
            errors.append(
                f"candidate[{i}]: start_s {cand.start_s} does not match any "
                f"word start_time (verbatim required)"
            )
        if not _close_to_any(cand.end_s, ends):
            errors.append(
                f"candidate[{i}]: end_s {cand.end_s} does not match any "
                f"word end_time (verbatim required)"
            )

    # Non-overlap check (on a copy sorted by start_s).
    for j in range(1, len(sorted_by_start)):
        prev = sorted_by_start[j - 1]
        curr = sorted_by_start[j]
        if curr.start_s < prev.end_s:
            errors.append(
                f"candidates overlap: [{prev.start_s:.1f}, {prev.end_s:.1f}] and "
                f"[{curr.start_s:.1f}, {curr.end_s:.1f}] — pick distinct regions"
            )

    # Rank-order check: engagement must be non-increasing across the list.
    for j in range(1, len(plan.candidates)):
        if plan.candidates[j].engagement_score > plan.candidates[j - 1].engagement_score:
            errors.append(
                f"candidate[{j}] engagement {plan.candidates[j].engagement_score} > "
                f"candidate[{j - 1}] {plan.candidates[j - 1].engagement_score} — "
                "candidates must be ranked descending"
            )

    return errors


def build_clip_hunter_plan(
    transcript: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
    target_clip_length_s: float = 60.0,
    num_clips: int = 3,
) -> ClipHunterPlan:
    """Run the Clip Hunter Director. Retries on structural violations."""
    prompt = _clip_hunter_prompt(
        preset,
        transcript,
        user_settings,
        target_clip_length_s,
        num_clips,
    )
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=ClipHunterPlan,
        validate=lambda plan: validate_clip_hunter_plan(
            plan,
            transcript,
            target_clip_length_s,
            num_clips,
        ),
        temperature=0.5,
        max_retries=5,
        accept_best_effort=True,
    )


def candidate_to_segments(cand: ClipCandidate) -> list[CutSegment]:
    """Convert a ClipCandidate into a one-element CutSegment list.

    A candidate is a single contiguous range on the source timeline; the
    existing :func:`resolve_segments.resolve_segments` auto-splits across
    timeline-item boundaries if the candidate spans multiple takes, so
    the caller gets multiple ResolvedCutSegments for free.
    """
    return [
        CutSegment(
            start_s=cand.start_s,
            end_s=cand.end_s,
            reason=cand.quote or cand.reasoning,
        )
    ]


# ---------------------------------------------------------------------------
# Short Generator Director — assembled multi-span shorts from scattered moments
# ---------------------------------------------------------------------------
#
# Where Clip Hunter *extracts* a single contiguous moment, Short Generator
# *composes* a punchy short from multiple scattered spans across the source.
# Each candidate is an assembled reel: 3–8 spans chosen for a through-line
# theme, jump-cut together into one 45–90 s output.
#
# Output schema deliberately mirrors ClipHunterPlan's tabbed shape so the
# Review UI can reuse the per-candidate selector. The difference is that a
# ShortCandidate carries a list of spans instead of one range.


class ShortSpan(BaseModel):
    """One source-timeline range contributing to an assembled short."""

    start_s: float = Field(
        ...,
        description="Source-timeline start, seconds. Must equal a word's start_time (verbatim).",
    )
    end_s: float = Field(
        ..., description="Source-timeline end, seconds. Must equal a word's end_time (verbatim)."
    )
    role: str = Field(
        default="",
        description="One short label per span — 'hook', 'setup', 'payoff', 'callback', 'close'.",
    )


class ShortCandidate(BaseModel):
    theme: str = Field(
        ...,
        description="The through-line of this short — one phrase (4-8 words).",
    )
    spans: list[ShortSpan] = Field(
        ...,
        description="3–8 source spans in play order. First span is the hook.",
    )
    # total_s is computed server-side from the spans; keeping it optional
    # means Gemini's arithmetic slip-ups (regularly off by a few seconds
    # on multi-span sums) don't blow up the validator. The validator
    # computes the canonical value before every check.
    total_s: float = Field(
        default=0.0,
        description="Computed server-side from span durations; field ignored if provided.",
    )
    engagement_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Director's confidence this short will retain (0–1).",
    )
    suggested_caption: str = Field(
        default="",
        description="Social caption (≤120 chars) ready for TikTok / Shorts / Reels.",
    )
    reasoning: str = Field(
        default="",
        description="1–2 sentences on why these spans, in this order.",
    )


class ShortGeneratorPlan(BaseModel):
    candidates: list[ShortCandidate] = Field(
        ...,
        description="Short candidates in descending engagement order.",
    )
    reasoning: str = Field(
        default="",
        description="Brief overall note on how shorts were composed.",
    )


def _short_generator_prompt(
    preset: PresetBundle,
    transcript: list[dict],
    user_settings: dict | None,
    target_short_length_s: float,
    num_shorts: int,
) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    themes = _themes_block(user_settings)
    speakers = _speaker_block(preset, transcript, user_settings)
    clip_meta = _clip_metadata_block(transcript)
    shot_tags = _shot_tag_block(transcript)
    audio_cues = _audio_cue_block(transcript, mode="short_generator")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b
        for b in (
            exclude,
            focus,
            themes,
            speakers,
            clip_meta,
            shot_tags,
            audio_cues,
            boundary_rej,
        )
        if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    transcript_for_prompt = _shape_for_prompt(transcript, user_settings)
    # Recipe approach: pre-solve the arithmetic for the model. Don't ask it to
    # sum spans — tell it exactly how many spans and what length each should be.
    # Lite models satisfice on numeric constraints; a concrete recipe gives
    # them something deterministic to imitate instead of a range to minimise.
    low = max(target_short_length_s * 0.75, target_short_length_s - 15)  # aspirational floor
    min_spans = max(6, min(8, round(target_short_length_s / 8)))  # ~8s avg spans
    avg_span = target_short_length_s / min_spans
    return f"""You are a {preset.role}.

You receive a transcript and will **compose {num_shorts} punchy assembled shorts** — each a list of source spans jump-cut together around a single through-line theme.

### TARGET LENGTH — NON-NEGOTIABLE
Each short runs **{target_short_length_s:.0f} seconds** on screen. Not 20s. Not 30s. **{target_short_length_s:.0f}s.** This is a YouTube/TikTok/Reels short — editors pay you to fill the full runtime with the best moments, not to deliver the minimum viable cut.

### RECIPE — follow exactly to hit the target:
- Pick **at least {min_spans} spans** per short (you can go up to 8).
- Each span should average **~{avg_span:.0f} seconds** (individual spans 3–25s are legal; aim for 5–12s).
- The {min_spans} × {avg_span:.0f}s math lands you on {target_short_length_s:.0f}s. If your short comes in below {low:.0f}s you picked too few spans — go back and add more from the transcript.

If the transcript genuinely does not have {target_short_length_s:.0f}s of content worth keeping on a given theme, skip that theme and pick a different one with enough material. This transcript is long enough — find {num_shorts} themes that each have {target_short_length_s:.0f}s of worthwhile content.

### RULES:
1. Each short has a clear THEME — a through-line (e.g. "why AR replaces phones", "the loneliness debate"). Every span must serve that theme.
2. The FIRST span is the HOOK — it must earn the next 5s on its own. Use {preset.hook_rule}.
3. Subsequent spans advance the short: setup → payoff, claim → callback, question → answer. Mark the role on each span.
4. Each span's `start_s` MUST equal the `start_time` of its first word; `end_s` MUST equal `end_time` of its last word. Verbatim — no rounding.
5. Within a short, spans MUST NOT overlap each other in source time. Across shorts, overlap is allowed.
6. Pacing: {preset.pacing}.
7. Return candidates in descending engagement order.
8. `suggested_caption` ≤ 120 chars, social-ready.

USER SETTINGS
{_user_settings_block(user_settings)}
- Target short length: {target_short_length_s:.0f}s (≥ {min_spans} spans, aim for ~{avg_span:.0f}s each)
- Number of shorts: {num_shorts}{optional_section}

TRANSCRIPT (JSON array):
{json.dumps(transcript_for_prompt, separators=(",", ":"))}

Return a `ShortGeneratorPlan` with:
- `candidates`: {num_shorts} entries, ranked by engagement (descending).
- `reasoning`: 1-2 sentences on how you composed them.
"""


def validate_short_generator_plan(
    plan: ShortGeneratorPlan,
    transcript: list[dict],
    target_short_length_s: float,
    num_shorts: int,
    duration_tolerance: float = 0.5,
) -> list[str]:
    """Validate a ShortGeneratorPlan.

    Checks:
      1. Candidate count is ``num_shorts`` (±1 leniency).
      2. Each candidate has 3–8 spans.
      3. Every span's ``start_s`` / ``end_s`` matches a verbatim word boundary.
      4. Per span: ``end_s > start_s``, span ≤ 25 s.
      5. Per candidate: spans are non-overlapping in source time.
      6. Per candidate: **computed** span-sum within ±tolerance of target.
         The model's ``total_s`` field is ignored and overwritten — Gemini's
         arithmetic on multi-span sums was off by 5–10s in practice, so we
         compute the canonical total from spans and mutate the candidate in
         place before downstream code reads it.
      7. Engagement scores monotone non-increasing.
    """
    starts, ends = _build_timestamp_sets(transcript)
    errors: list[str] = []

    if not plan.candidates:
        return ["candidates is empty — at least one short required"]

    if abs(len(plan.candidates) - num_shorts) > 1:
        errors.append(f"expected {num_shorts} candidates (±1), got {len(plan.candidates)}")

    # Asymmetric bounds: shorts can be meaningfully shorter than target (20s is a
    # valid TikTok/Reels short) but runaway-long shorts defeat the format. Floor
    # is min(target*(1-tol), 20s) so a 60s target still accepts 20-30s shorts
    # when the content's natural breakpoints land there.
    low = min(target_short_length_s * (1.0 - duration_tolerance), 15.0)
    high = target_short_length_s * (1.0 + duration_tolerance)

    # Diagnostic: dump the shape the model returned so we can see whether it's
    # picking too-few spans or too-short spans. One line per candidate.
    for i, cand in enumerate(plan.candidates):
        span_durs = [round(s.end_s - s.start_s, 2) for s in cand.spans]
        log.info(
            "short_generator: cand[%d] theme=%r n_spans=%d durs=%s sum=%.1fs target=%.0fs",
            i,
            cand.theme,
            len(cand.spans),
            span_durs,
            sum(span_durs),
            target_short_length_s,
        )

    prev_score = 1.01
    for i, cand in enumerate(plan.candidates):
        if not (3 <= len(cand.spans) <= 8):
            errors.append(f"candidate[{i}]: {len(cand.spans)} spans — must be 3–8")
        # Canonical total = span-sum. Overwrite whatever the model reported
        # so downstream code (UI, execute) sees the real number.
        computed = sum(s.end_s - s.start_s for s in cand.spans)
        cand.total_s = computed
        if not (low <= computed <= high):
            errors.append(
                f"candidate[{i}]: total {computed:.1f}s outside [{low:.1f}, {high:.1f}]s — "
                f"add more spans or extend existing ones to reach {target_short_length_s:.0f}s"
            )

        # Per-span verbatim + duration.
        for j, span in enumerate(cand.spans):
            if span.end_s <= span.start_s:
                errors.append(f"candidate[{i}].spans[{j}]: end_s {span.end_s} must be > start_s")
                continue
            if span.end_s - span.start_s > 25.0:
                errors.append(
                    f"candidate[{i}].spans[{j}]: {span.end_s - span.start_s:.1f}s — "
                    "single span over 25 s defeats the jump-cut format"
                )
            if not _close_to_any(span.start_s, starts):
                errors.append(
                    f"candidate[{i}].spans[{j}]: start_s {span.start_s} not a word boundary"
                )
            if not _close_to_any(span.end_s, ends):
                errors.append(f"candidate[{i}].spans[{j}]: end_s {span.end_s} not a word boundary")

        # Non-overlap in source time (within this candidate).
        sorted_spans = sorted(cand.spans, key=lambda s: s.start_s)
        for k in range(1, len(sorted_spans)):
            if sorted_spans[k].start_s < sorted_spans[k - 1].end_s:
                errors.append(
                    f"candidate[{i}]: source-time overlap between spans "
                    f"[{sorted_spans[k - 1].start_s:.1f}, {sorted_spans[k - 1].end_s:.1f}] "
                    f"and [{sorted_spans[k].start_s:.1f}, {sorted_spans[k].end_s:.1f}]"
                )

        if cand.engagement_score > prev_score:
            errors.append(
                f"candidate[{i}]: engagement {cand.engagement_score:.2f} > prev "
                f"{prev_score:.2f} — must rank descending"
            )
        prev_score = cand.engagement_score

    return errors


def build_short_generator_plan(
    transcript: list[dict],
    preset: PresetBundle,
    user_settings: dict | None,
    target_short_length_s: float,
    num_shorts: int,
) -> ShortGeneratorPlan:
    """Run the Short Generator Director, retrying on validation errors."""
    prompt = _short_generator_prompt(
        preset, transcript, user_settings, target_short_length_s, num_shorts
    )
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=ShortGeneratorPlan,
        validate=lambda plan: validate_short_generator_plan(
            plan, transcript, target_short_length_s, num_shorts
        ),
        temperature=0.5,
        max_retries=5,
        accept_best_effort=True,
    )


def short_candidate_to_segments(cand: ShortCandidate) -> list[CutSegment]:
    """Convert a ShortCandidate into a list of CutSegments in play order.

    Short Generator candidates carry their spans in the order the short
    should play — the resolver appends them end-to-end, producing the
    assembled short on the new timeline.
    """
    return [
        CutSegment(
            start_s=span.start_s,
            end_s=span.end_s,
            reason=f"{span.role or 'span'}: {cand.theme}",
        )
        for span in cand.spans
    ]


def expand_assembled_plan(
    plan: AssembledDirectorPlan,
    takes: list[dict],
) -> tuple[list[CutSegment], int]:
    """Convert an AssembledDirectorPlan into timeline-seconds `CutSegment`s.

    Returns ``(segments, hook_cut_segment_index)``. Callers feed ``segments``
    into the existing :func:`resolve_segments.resolve_segments` resolver,
    which maps them to source frames. Because spans stay within one item,
    the resolver's auto-split path never fires.

    ``hook_cut_segment_index`` is the index within the flat segments list
    that corresponds to the Director's chosen hook take (the first span of
    that take). Useful so downstream UIs can label the hook beat.
    """
    take_by_index = {t["item_index"]: t for t in takes}
    segments: list[CutSegment] = []
    hook_cut_index = 0
    for sel_pos, sel in enumerate(plan.selections):
        take = take_by_index[sel.item_index]
        words = take["transcript"]
        if sel_pos == plan.hook_index and sel.kept_word_spans:
            hook_cut_index = len(segments)
        for span in sel.kept_word_spans:
            segments.append(
                CutSegment(
                    start_s=float(words[span.a]["start_time"]),
                    end_s=float(words[span.b]["end_time"]),
                    reason=f"take {sel.item_index}: '{take.get('source_name', '')}'",
                )
            )
    return segments, hook_cut_index


# ---------------------------------------------------------------------------
# Curated Director (v2-11) — every take must appear at least once
# ---------------------------------------------------------------------------
#
# Curated mode's contract: the editor has finalised their selects (no
# duplicates) but hasn't committed to narrative order. The agent is free to
# reorder takes, split them into non-contiguous spans, and interleave across
# takes — but **every take must contribute at least one span** to the output.
#
# Reuses AssembledItemSelection + WordSpan — the schema is identical; the
# difference is the invariant. We rename the top-level plan so both shapes
# can coexist in docs / debuggers.


class CuratedItemSelection(BaseModel):
    """Same shape as AssembledItemSelection, but a take may appear multiple
    times — once per non-contiguous span cluster the Director wants to
    interleave. ``order`` is the play-order position across all selections."""

    order: int = Field(
        ...,
        ge=0,
        description="0-based play-order position; unique across selections.",
    )
    item_index: int = Field(
        ...,
        ge=0,
        description="0-based index into the input TAKES array.",
    )
    kept_word_spans: list[WordSpan] = Field(
        ...,
        description="Ranges of word indices to keep from this take. Non-overlapping, ascending within the selection.",
    )


class CuratedDirectorPlan(BaseModel):
    hook_order: int = Field(
        ...,
        description="``order`` value identifying the hook selection.",
    )
    selections: list[CuratedItemSelection]
    reasoning: str = Field(default="", description="1-2 sentences on overall structure.")


def _curated_prompt(
    preset: PresetBundle,
    takes: list[dict],
    user_settings: dict | None,
) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    flat_words: list[dict] = []
    for t in takes:
        flat_words.extend(t.get("transcript") or [])
    speakers = _speaker_block(preset, flat_words, user_settings)
    clip_meta = _clip_metadata_block(flat_words)
    shot_tags = _shot_tag_block(flat_words)
    audio_cues = _audio_cue_block(flat_words, mode="curated")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b for b in (exclude, focus, speakers, clip_meta, shot_tags, audio_cues, boundary_rej) if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    takes_for_prompt = _shape_takes_for_prompt(takes, user_settings)

    return f"""You are a {preset.role}.

The editor has curated their final takes (A-roll picked — no duplicates). Your job is to arrange them into the strongest narrative. You may split a take into multiple non-contiguous spans and interleave those spans with other takes' spans.

HARD RULE — CURATED INVARIANT:
Every take listed below MUST contribute at least one span to your plan. Dropping a take is not allowed — the editor explicitly promised these are the keepers.

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. Set `hook_order` to the ``order`` value of the hook selection.
2. Pacing: {preset.pacing}.
3. You MAY reorder takes freely to build the best narrative.
4. You MAY include the same take more than once at non-overlapping spans, each with its own ``order`` value — use this for callbacks when dramatically stronger.
5. kept_word_spans reference valid `i` values from that take's transcript. Spans are inclusive on both ends.
6. Within a single selection, spans are non-overlapping and ascending. Across selections of the same take, the spans must also be pairwise non-overlapping.
7. ``order`` values are unique, 0-based, and form a contiguous sequence (0, 1, 2, ..., N-1) describing play order.

USER SETTINGS
{_user_settings_block(user_settings)}{optional_section}

TAKES (JSON array):
{json.dumps(takes_for_prompt, separators=(",", ":"))}

Return a `CuratedDirectorPlan` with:
- `selections`: list of {{order, item_index, kept_word_spans}} entries. Every input take's item_index must appear in at least one selection.
- `hook_order`: the ``order`` value of the hook selection.
- `reasoning`: 1–2 sentences on the overall structure.
"""


def validate_curated_plan(
    plan: CuratedDirectorPlan,
    takes: list[dict],
) -> list[str]:
    """Validate a curated plan.

    Curated invariant: every input take.item_index appears in ≥1 selection.
    All other structural checks are shared with the rough-cut validator.
    """
    errors = _curated_span_checks(plan, takes)
    selected_items = {s.item_index for s in plan.selections}
    missing = [t["item_index"] for t in takes if t["item_index"] not in selected_items]
    if missing:
        errors.append(
            f"Curated invariant violated: takes {missing} contributed no spans — "
            "every take must appear at least once"
        )
    return errors


def build_curated_cut_plan(
    takes: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> CuratedDirectorPlan:
    """Run the Curated Director, retrying on invariant violations."""
    prompt = _curated_prompt(preset, takes, user_settings)
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=CuratedDirectorPlan,
        validate=lambda plan: validate_curated_plan(plan, takes),
        temperature=0.4,
        max_retries=5,
        accept_best_effort=True,
    )


def expand_curated_plan(
    plan: CuratedDirectorPlan,
    takes: list[dict],
) -> tuple[list[CutSegment], int]:
    """Convert a CuratedDirectorPlan into timeline-seconds `CutSegment`s.

    Segments are emitted in ``order`` sequence. Returns
    ``(segments, hook_cut_segment_index)`` where ``hook_cut_segment_index``
    is the index of the first segment belonging to the hook selection.
    """
    take_by_index = {t["item_index"]: t for t in takes}
    sorted_sels = sorted(plan.selections, key=lambda s: s.order)
    segments: list[CutSegment] = []
    hook_cut_index = 0
    for sel in sorted_sels:
        take = take_by_index[sel.item_index]
        words = take["transcript"]
        if sel.order == plan.hook_order and sel.kept_word_spans:
            hook_cut_index = len(segments)
        for span in sel.kept_word_spans:
            segments.append(
                CutSegment(
                    start_s=float(words[span.a]["start_time"]),
                    end_s=float(words[span.b]["end_time"]),
                    reason=f"take {sel.item_index}: '{take.get('source_name', '')}'",
                )
            )
    return segments, hook_cut_index


# ---------------------------------------------------------------------------
# Rough cut Director (v2-11) — every group must appear at least once
# ---------------------------------------------------------------------------
#
# Rough cut's contract: the editor's timeline has A/B alternates grouped
# (by color, flag, or transcript similarity). The agent picks one winner
# per group — or intercuts two when dramatically stronger — and sequences
# the winners.
#
# Input: takes + groups (where each group names a set of item_indexes that
# are alternates of each other). Output: a CuratedDirectorPlan shape, but
# the validator enforces group coverage instead of take coverage.


def _rough_cut_prompt(
    preset: PresetBundle,
    takes: list[dict],
    groups: list[dict],
    user_settings: dict | None,
) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    flat_words: list[dict] = []
    for t in takes:
        flat_words.extend(t.get("transcript") or [])
    speakers = _speaker_block(preset, flat_words, user_settings)
    clip_meta = _clip_metadata_block(flat_words)
    shot_tags = _shot_tag_block(flat_words)
    audio_cues = _audio_cue_block(flat_words, mode="rough_cut")
    boundary_rej = _boundary_rejections_block(user_settings)
    optional_blocks = "\n\n".join(
        b for b in (exclude, focus, speakers, clip_meta, shot_tags, audio_cues, boundary_rej) if b
    )
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    takes_for_prompt = _shape_takes_for_prompt(takes, user_settings)
    groups_for_prompt = [
        {
            "group_id": g["group_id"],
            "item_indexes": g["item_indexes"],
            "signal": g.get("signal", "unknown"),
        }
        for g in groups
    ]

    return f"""You are a {preset.role}.

The editor has delivered a rough cut — candidate takes with A/B (or more) alternates for the same moment. Each GROUP below is a set of alternate takes the editor wants considered together. Your job: pick a winner per group (or intercut two when dramatically stronger), then sequence the winners into the strongest narrative.

HARD RULE — ROUGH CUT INVARIANT:
Every group below MUST contribute at least one span to your plan. Dropping a whole group is not allowed — the editor marked each group as a moment that matters.

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. Set `hook_order` to the ``order`` value of the hook selection.
2. Pacing: {preset.pacing}.
3. For each group, typically pick ONE winning take. Choose two from the same group only when intercutting them is dramatically stronger than either alone.
4. Across groups you MAY reorder freely.
5. kept_word_spans reference valid `i` values from that take's transcript. Spans inclusive on both ends, non-overlapping within a selection.
6. ``order`` values are unique, 0-based, and form a contiguous sequence describing play order.
7. You may omit alternate takes within a group — just ensure the group itself is represented.

USER SETTINGS
{_user_settings_block(user_settings)}{optional_section}

GROUPS (each group is a set of alternates):
{json.dumps(groups_for_prompt, separators=(",", ":"))}

TAKES (JSON array):
{json.dumps(takes_for_prompt, separators=(",", ":"))}

Return a `CuratedDirectorPlan` with:
- `selections`: list of {{order, item_index, kept_word_spans}} entries. Every group's item set must intersect the union of selected item_indexes at least once.
- `hook_order`: the ``order`` value of the hook selection.
- `reasoning`: 1–2 sentences on winner choices and narrative structure.
"""


def validate_rough_cut_plan(
    plan: CuratedDirectorPlan,
    takes: list[dict],
    groups: list[dict],
) -> list[str]:
    """Validate a rough-cut plan.

    Reuses the curated validator's checks, then swaps the take-coverage
    invariant for group-coverage: every group must have at least one of
    its item_indexes present in the selections.
    """
    # Reuse curated's span-level checks, but drop its take-coverage
    # invariant — rough cut *wants* to drop alternate takes within groups.
    errors = _curated_span_checks(plan, takes)

    selected_items = {s.item_index for s in plan.selections}
    uncovered_groups = [
        g["group_id"] for g in groups if not (set(g["item_indexes"]) & selected_items)
    ]
    if uncovered_groups:
        errors.append(
            f"Rough cut invariant violated: groups {uncovered_groups} contributed no spans — "
            "every group must have at least one winner"
        )
    return errors


def _curated_span_checks(
    plan: CuratedDirectorPlan,
    takes: list[dict],
) -> list[str]:
    """Shared structural checks between curated + rough_cut validators.

    Runs every validation step from :func:`validate_curated_plan` *except*
    the "every take appears" invariant. Rough cut substitutes its own
    group-coverage rule.
    """
    errors: list[str] = []
    if not plan.selections:
        return ["selections is empty"]

    orders = [s.order for s in plan.selections]
    if sorted(orders) != list(range(len(plan.selections))):
        errors.append(
            f"order values {sorted(orders)} are not a contiguous 0..{len(plan.selections) - 1} permutation"
        )
    if len(set(orders)) != len(orders):
        errors.append("duplicate order values — each selection must have a unique order")
    if plan.hook_order not in {s.order for s in plan.selections}:
        errors.append(f"hook_order {plan.hook_order} does not match any selection's order value")

    take_by_index = {t["item_index"]: t for t in takes}
    spans_per_take: dict[int, list[tuple[int, int]]] = {}

    for pos, sel in enumerate(plan.selections):
        take = take_by_index.get(sel.item_index)
        if take is None:
            errors.append(
                f"selections[{pos}]: item_index {sel.item_index} does not match any input take"
            )
            continue
        if not sel.kept_word_spans:
            errors.append(
                f"selections[{pos}]: kept_word_spans is empty — drop the selection entirely instead"
            )
            continue
        transcript_len = len(take.get("transcript") or [])
        if transcript_len == 0:
            errors.append(f"selections[{pos}]: take {sel.item_index} has no transcript")
            continue
        last_b = -1
        for j, span in enumerate(sel.kept_word_spans):
            if span.a > span.b:
                errors.append(f"selections[{pos}].spans[{j}]: a={span.a} > b={span.b}")
                continue
            if span.a >= transcript_len or span.b >= transcript_len:
                errors.append(
                    f"selections[{pos}].spans[{j}]: [{span.a},{span.b}] "
                    f"out of range for take with {transcript_len} words"
                )
                continue
            if span.a <= last_b:
                errors.append(
                    f"selections[{pos}].spans[{j}]: start a={span.a} overlaps previous span end {last_b}"
                )
            last_b = span.b
            spans_per_take.setdefault(sel.item_index, []).append((span.a, span.b))

    for item_index, span_list in spans_per_take.items():
        span_list.sort()
        for i in range(1, len(span_list)):
            if span_list[i][0] <= span_list[i - 1][1]:
                errors.append(
                    f"take {item_index}: spans {span_list[i - 1]} and {span_list[i]} overlap across selections"
                )

    return errors


def build_rough_cut_plan(
    takes: list[dict],
    groups: list[dict],
    preset: PresetBundle,
    user_settings: dict | None = None,
) -> CuratedDirectorPlan:
    """Run the Rough cut Director, retrying on invariant violations."""
    prompt = _rough_cut_prompt(preset, takes, groups, user_settings)
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=CuratedDirectorPlan,
        validate=lambda plan: validate_rough_cut_plan(plan, takes, groups),
        temperature=0.4,
        max_retries=5,
        accept_best_effort=True,
    )
