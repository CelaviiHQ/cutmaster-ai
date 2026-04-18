"""Director agent — scrubbed transcript → selected `CutSegment[]`.

Picks contiguous word-aligned blocks the editor should keep. Enforces
verbatim timestamps via a post-validation retry loop; if the model rounds
``12.450 → 12.45`` the response is rejected and the model gets a chance to
fix it.

Model-agnostic: the actual LLM call goes through ``llm.call_structured``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from . import llm

if TYPE_CHECKING:
    from .presets import PresetBundle


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
            errors.append(
                f"segment[{i}]: end_s {seg.end_s} must be > start_s {seg.start_s}"
            )
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
    if (tgt := user_settings.get("target_length_s")):
        mins = tgt / 60.0
        lines.append(f"- Target length: ~{mins:.1f} minutes")
    if (themes := user_settings.get("themes")):
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
        f"support it.\n\"{focus}\""
    )


def _prompt(preset: PresetBundle, transcript: list[dict], user_settings: dict | None) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    optional_blocks = "\n\n".join(b for b in (exclude, focus) if b)
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
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
{json.dumps(transcript, separators=(",", ":"))}

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
        ..., ge=0,
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
    optional_blocks = "\n\n".join(b for b in (exclude, focus) if b)
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""

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
{json.dumps(takes, separators=(",", ":"))}

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
            f"hook_index {plan.hook_index} out of range for "
            f"{len(plan.selections)} selections"
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
            errors.append(
                f"selections[{i}]: take {sel.item_index} has no transcript"
            )
            continue

        last_b = -1
        for j, span in enumerate(sel.kept_word_spans):
            if span.a > span.b:
                errors.append(
                    f"selections[{i}].spans[{j}]: a={span.a} > b={span.b}"
                )
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
    start_s: float = Field(..., description="Start of the candidate on the source timeline, in seconds.")
    end_s: float = Field(..., description="End of the candidate on the source timeline, in seconds.")
    quote: str = Field(
        default="",
        description="The key line that anchors the clip — used in the Review tabs.",
    )
    engagement_score: float = Field(
        ..., ge=0.0, le=1.0,
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


def _clip_hunter_prompt(
    preset: PresetBundle,
    transcript: list[dict],
    user_settings: dict | None,
    target_clip_length_s: float,
    num_clips: int,
) -> str:
    exclude = _exclude_block(preset, user_settings)
    focus = _focus_block(user_settings)
    optional_blocks = "\n\n".join(b for b in (exclude, focus) if b)
    optional_section = f"\n\n{optional_blocks}" if optional_blocks else ""
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

USER SETTINGS
{_user_settings_block(user_settings)}
- Target clip length: {target_clip_length_s:.0f} s
- Number of candidates: {num_clips}{optional_section}

TRANSCRIPT (JSON array):
{json.dumps(transcript, separators=(",", ":"))}

Return a `ClipHunterPlan` with:
- `candidates`: list of {num_clips} entries, ranked by engagement (descending).
- `reasoning`: 1-2 sentences on how you chose.
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
        errors.append(
            f"expected {num_clips} candidates (±1), got {len(plan.candidates)}"
        )

    low = target_clip_length_s * (1.0 - duration_tolerance)
    high = target_clip_length_s * (1.0 + duration_tolerance)

    sorted_by_start = sorted(plan.candidates, key=lambda c: c.start_s)
    for i, cand in enumerate(plan.candidates):
        if cand.end_s <= cand.start_s:
            errors.append(
                f"candidate[{i}]: end_s {cand.end_s} must be > start_s {cand.start_s}"
            )
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
        preset, transcript, user_settings, target_clip_length_s, num_clips,
    )
    return llm.call_structured(
        agent="director",
        prompt=prompt,
        response_schema=ClipHunterPlan,
        validate=lambda plan: validate_clip_hunter_plan(
            plan, transcript, target_clip_length_s, num_clips,
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
    return [CutSegment(
        start_s=cand.start_s,
        end_s=cand.end_s,
        reason=cand.quote or cand.reasoning,
    )]


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
            segments.append(CutSegment(
                start_s=float(words[span.a]["start_time"]),
                end_s=float(words[span.b]["end_time"]),
                reason=f"take {sel.item_index}: '{take.get('source_name', '')}'",
            ))
    return segments, hook_cut_index
