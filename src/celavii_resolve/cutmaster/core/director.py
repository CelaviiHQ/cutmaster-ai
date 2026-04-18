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
from ..stt.per_clip import clip_metadata_table
from ..stt.speakers import apply_speaker_labels, detect_speakers, speaker_stats

if TYPE_CHECKING:
    from ..data.presets import PresetBundle

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Verbatim-timestamp validator
# ---------------------------------------------------------------------------


TIMESTAMP_TOLERANCE_S = 0.001  # 1 ms — tolerates float repr but not rounding


def _build_timestamp_sets(transcript: list[dict]) -> tuple[list[float], list[float]]:
    starts = sorted({float(w["start_time"]) for w in transcript})
    ends = sorted({float(w["end_time"]) for w in transcript})
    return starts, ends


def _close_to_any(value: float, sorted_values: list[float]) -> bool:
    # Linear scan is fine — transcripts have O(1000) words max.
    return any(abs(value - v) <= TIMESTAMP_TOLERANCE_S for v in sorted_values)


def validate_plan(plan: DirectorPlan, transcript: list[dict]) -> list[str]:
    """Return a list of validation errors. Empty list = valid.

    Checks:
      1. Every ``start_s`` matches a word's ``start_time`` within tolerance.
      2. Every ``end_s`` matches a word's ``end_time`` within tolerance.
      3. Segments have positive duration.
      4. hook_index is in range.
      5. No overlapping or out-of-order segments *within the same clip*
         (ordering between clips is the Director's choice — hook-first is OK).
    """
    starts, ends = _build_timestamp_sets(transcript)
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
            errors.append(
                f"segment[{i}]: start_s {seg.start_s} does not match any "
                f"word start_time in the transcript (verbatim required)"
            )
        if not _close_to_any(seg.end_s, ends):
            errors.append(
                f"segment[{i}]: end_s {seg.end_s} does not match any "
                f"word end_time in the transcript (verbatim required)"
            )

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
    """Drop ``clip_metadata`` off every word before JSON-serialising.

    v2-6 attaches full clip metadata to every word for the pipeline's
    internal use. When the transcript hits the Director prompt though, the
    CLIP METADATA table at the top of the prompt already carries
    ``source_name`` / ``duration_s`` / ``timeline_offset_s`` once per
    clip — repeating that on each of ~1,000 words inflates the payload by
    roughly 3×. The ``clip_index`` integer stays so the Director can still
    cross-reference words back to the table.

    Words without ``clip_metadata`` pass through unchanged — v1 runs and
    the concat STT path don't need this path.
    """
    out: list[dict] = []
    for w in transcript:
        if "clip_metadata" not in w:
            out.append(w)
            continue
        out.append({k: v for k, v in w.items() if k != "clip_metadata"})
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


def _prompt(preset: PresetBundle, transcript: list[dict], user_settings: dict | None) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    speakers = _speaker_block(preset, transcript, user_settings)
    clip_meta = _clip_metadata_block(transcript)
    optional_blocks = "\n\n".join(b for b in (exclude, focus, speakers, clip_meta) if b)
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    transcript_for_prompt = _shape_for_prompt(transcript, user_settings)
    return f"""You are a {preset.role}.

You will receive a transcript array where each item has a `word`, `start_time`, and `end_time` in seconds. Your job is to select contiguous blocks of words that, when stitched together, form a compelling cut.

RULES — follow exactly:
1. Identify the HOOK: {preset.hook_rule}. The hook's CutSegment becomes position 0 in the output, even if it's not the earliest in the transcript.
2. Pacing: {preset.pacing}.
3. Do not alter, edit, paraphrase, or summarize ANY word. You may only select blocks of existing words.
4. For each CutSegment, `start_s` MUST equal the `start_time` of the first word in the block, and `end_s` MUST equal the `end_time` of the last word. Do not round, truncate, or invent timestamps. If unsure, skip that block.
5. Blocks must be word-aligned and non-overlapping.

USER SETTINGS
{_user_settings_block(user_settings)}{optional_section}

TRANSCRIPT (JSON array):
{json.dumps(transcript_for_prompt, separators=(",", ":"))}

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
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=DirectorPlan,
        validate=lambda plan: validate_plan(plan, transcript),
        temperature=0.4,
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus, speakers, clip_meta) if b)
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus, themes, speakers, clip_meta) if b)
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus, themes, speakers, clip_meta) if b)
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
    transcript_for_prompt = _shape_for_prompt(transcript, user_settings)
    low = target_short_length_s * 0.5
    high = target_short_length_s * 1.5
    return f"""You are a {preset.role}.

You receive a transcript and will **compose {num_shorts} punchy assembled shorts** — each a list of 3–8 source spans jump-cut together around a single through-line theme.

### CRITICAL DURATION CONSTRAINT
For every short, sum the duration of its spans (end_s − start_s) across all spans in the short. That sum **MUST be between {low:.0f} and {high:.0f} seconds**. A short whose spans sum to less than {low:.0f} s is rejected — add more spans until the sum is in range. Target is {target_short_length_s:.0f} s.

### RULES — follow exactly:
1. Each short has 3–8 spans. If the combined duration is below {low:.0f} s with 3 spans, add more spans (up to 8) or make individual spans longer.
2. Each short has a clear THEME — a through-line (e.g. "why AR replaces phones", "the loneliness debate"). Every span must serve that theme.
3. The FIRST span of each short is the HOOK — it must earn the next 5 s on its own. Use {preset.hook_rule}.
4. Subsequent spans advance the short: setup → payoff, claim → callback, question → answer. Mark the role on each span.
5. Each span's `start_s` MUST equal the `start_time` of its first word; `end_s` MUST equal `end_time` of its last word. Verbatim — no rounding.
6. Individual spans should be 3–25 s. A span under 3 s is almost always too short to read on camera — prefer longer punchy spans over sub-3s fragments.
7. Within a short, spans MUST NOT overlap each other in source time.
8. Across different shorts, cross-short overlap is allowed (two shorts can reference the same hook).
9. Pacing: {preset.pacing}.
10. Return candidates in descending engagement order.
11. `suggested_caption` ≤ 120 chars, social-ready.

### BEFORE YOU RESPOND
For each short, mentally compute: sum of (end_s − start_s) across all spans. If that number is below {low:.0f}, the short is invalid — extend spans or add more until the sum is {low:.0f}–{high:.0f} s. Do not submit a short whose spans sum below the minimum.

USER SETTINGS
{_user_settings_block(user_settings)}
- Target short length: {target_short_length_s:.0f} s (acceptable range {low:.0f}–{high:.0f} s)
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus, speakers, clip_meta) if b)
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus, speakers, clip_meta) if b)
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
    )
