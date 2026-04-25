"""Story-coherence critic — second-pass LLM judgement on a built cut.

A pure function ``critique(plan, transcript=…, takes=…, axes=…)`` that
takes a structurally-valid plan, slices the transcript words behind each
segment, and asks a separate LLM (the "critic") whether the picks form a
coherent cut. The critic never re-picks; it only grades.

Stateless and idempotent: same plan + same transcript + same axes →
same prompt → same call. No reads, no writes, no Resolve SDK.

See `Implementation/optimizaiton/story-critic.md` Phase 1 for the design
and the rubric this module owns.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from ..cutmaster.core.director import (
    AssembledDirectorPlan,
    ClipHunterPlan,
    CuratedDirectorPlan,
    DirectorPlan,
    ShortGeneratorPlan,
)
from ..cutmaster.data.axis_resolution import ResolvedAxes
from . import llm as _llm_module

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Public schema
# ---------------------------------------------------------------------------


Verdict = Literal["ship", "review", "rework"]

CoherenceCategory = Literal[
    "non_sequitur",
    "weak_hook",
    "missing_setup",
    "abrupt_transition",
    "redundancy",
    "unresolved_thread",
    "inverted_arc",
    "weak_resolution",
    "buried_lede",
]

Severity = Literal["info", "warning", "error"]


class CoherenceIssue(BaseModel):
    segment_index: int = Field(
        description="Index of the segment the issue points at; -1 means whole-cut."
    )
    pair_index: int | None = Field(
        default=None,
        description="Set on transition issues — refers to segment_index → segment_index+1.",
    )
    severity: Severity
    category: CoherenceCategory
    message: str = Field(description="Editor-readable explanation, ≤2 sentences.")
    suggestion: str | None = Field(default=None, description="Optional remediation hint.")


class CoherenceReport(BaseModel):
    """One critic verdict on one coherent cut.

    ``arc_clarity`` and ``transitions`` are ``None`` for ``surgical_tighten``
    cuts where those dimensions don't apply (only filler-removal is graded).
    """

    score: int = Field(ge=0, le=100, description="Overall coherence (0–100).")
    hook_strength: int = Field(ge=0, le=100)
    arc_clarity: int | None = Field(default=None, ge=0, le=100)
    transitions: int | None = Field(default=None, ge=0, le=100)
    resolution: int = Field(ge=0, le=100)
    issues: list[CoherenceIssue] = Field(default_factory=list)
    summary: str = Field(description="1–2 sentences, editor voice.")
    verdict: Verdict = Field(description="Derived server-side from score.")


class PerCandidateCoherenceReport(BaseModel):
    """Wraps N CoherenceReports for plans that emit N standalone outputs
    (ClipHunterPlan, ShortGeneratorPlan)."""

    candidates: list[CoherenceReport]
    best_candidate_index: int = Field(
        ge=0, description="Index of the candidate with the highest overall score."
    )
    summary: str = Field(default="", description="1–2 sentences across the candidate set.")


# ---------------------------------------------------------------------------
# Internal adapter shape
# ---------------------------------------------------------------------------


class CritiqueSegment(BaseModel):
    index: int
    start_s: float
    end_s: float
    arc_role: str | None = None
    reason: str = ""
    text: str = ""


class CriticInput(BaseModel):
    """One coherent cut to grade, in critic-shaped form."""

    hook_index: int
    segments: list[CritiqueSegment]
    rationale: str = ""
    label: str = ""  # candidate theme / take note for per-candidate dispatch


# ---------------------------------------------------------------------------
# Verdict + issue-cap helpers
# ---------------------------------------------------------------------------


_MAX_ISSUES = 7
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _derive_verdict(score: int) -> Verdict:
    """`<60` → rework, `60–79` → review, `≥80` → ship."""
    if score < 60:
        return "rework"
    if score < 80:
        return "review"
    return "ship"


def _cap_issues(issues: list[CoherenceIssue]) -> list[CoherenceIssue]:
    """Keep at most ``_MAX_ISSUES``, highest-severity-first."""
    if len(issues) <= _MAX_ISSUES:
        return issues
    ranked = sorted(
        enumerate(issues),
        key=lambda pair: (_SEVERITY_RANK.get(pair[1].severity, 9), pair[0]),
    )
    kept = sorted(ranked[:_MAX_ISSUES], key=lambda pair: pair[0])
    return [issue for _, issue in kept]


# ---------------------------------------------------------------------------
# Transcript slicing
# ---------------------------------------------------------------------------


def _slice_transcript(transcript: list[dict], start_s: float, end_s: float) -> str:
    """Join the words whose [start_time, end_time] sit inside [start_s, end_s].

    Mirrors the panel's transcript-expansion filter so the critic sees
    exactly the words the editor reads when expanding a row.
    """
    words = [
        str(w.get("word", ""))
        for w in transcript
        if float(w.get("start_time", -1.0)) >= start_s and float(w.get("end_time", -1.0)) <= end_s
    ]
    return " ".join(w for w in words if w).strip()


def _slice_take_words(take: dict, a: int, b: int) -> str:
    words = take.get("transcript") or []
    if not words:
        return ""
    a = max(0, a)
    b = min(len(words) - 1, b)
    return " ".join(str(words[i].get("word", "")) for i in range(a, b + 1)).strip()


# ---------------------------------------------------------------------------
# Adapters — one per plan shape
# ---------------------------------------------------------------------------


def _adapt_director_plan(plan: DirectorPlan, transcript: list[dict]) -> CriticInput:
    segments = [
        CritiqueSegment(
            index=i,
            start_s=seg.start_s,
            end_s=seg.end_s,
            arc_role=seg.arc_role,
            reason=seg.reason,
            text=_slice_transcript(transcript, seg.start_s, seg.end_s),
        )
        for i, seg in enumerate(plan.selected_clips)
    ]
    return CriticInput(
        hook_index=plan.hook_index,
        segments=segments,
        rationale=plan.reasoning,
    )


def _adapt_assembled_plan(plan: AssembledDirectorPlan, takes: list[dict]) -> CriticInput:
    take_by_index = {t["item_index"]: t for t in takes}
    segments: list[CritiqueSegment] = []
    idx = 0
    for sel in plan.selections:
        take = take_by_index.get(sel.item_index)
        if take is None:
            continue
        words = take.get("transcript") or []
        for span in sel.kept_word_spans:
            if not words:
                continue
            try:
                start_s = float(words[span.a]["start_time"])
                end_s = float(words[span.b]["end_time"])
            except (IndexError, KeyError, TypeError):
                continue
            segments.append(
                CritiqueSegment(
                    index=idx,
                    start_s=start_s,
                    end_s=end_s,
                    arc_role=None,
                    reason=f"take {sel.item_index}: '{take.get('source_name', '')}'",
                    text=_slice_take_words(take, span.a, span.b),
                )
            )
            idx += 1
    return CriticInput(
        hook_index=plan.hook_index,
        segments=segments,
        rationale=plan.reasoning,
    )


def _adapt_curated_plan(plan: CuratedDirectorPlan, takes: list[dict]) -> CriticInput:
    take_by_index = {t["item_index"]: t for t in takes}
    selections_in_play_order = sorted(plan.selections, key=lambda s: s.order)
    segments: list[CritiqueSegment] = []
    hook_index = 0
    idx = 0
    for sel in selections_in_play_order:
        take = take_by_index.get(sel.item_index)
        if take is None:
            continue
        words = take.get("transcript") or []
        if not words:
            continue
        if sel.order == plan.hook_order:
            hook_index = idx
        for span in sel.kept_word_spans:
            try:
                start_s = float(words[span.a]["start_time"])
                end_s = float(words[span.b]["end_time"])
            except (IndexError, KeyError, TypeError):
                continue
            segments.append(
                CritiqueSegment(
                    index=idx,
                    start_s=start_s,
                    end_s=end_s,
                    arc_role=None,
                    reason=f"take {sel.item_index}: '{take.get('source_name', '')}'",
                    text=_slice_take_words(take, span.a, span.b),
                )
            )
            idx += 1
    return CriticInput(
        hook_index=hook_index,
        segments=segments,
        rationale=plan.reasoning,
    )


def _adapt_clip_hunter_plan(plan: ClipHunterPlan, transcript: list[dict]) -> list[CriticInput]:
    """Each ClipCandidate becomes its own CriticInput — N reports, one per pick."""
    inputs: list[CriticInput] = []
    for cand in plan.candidates:
        seg = CritiqueSegment(
            index=0,
            start_s=cand.start_s,
            end_s=cand.end_s,
            arc_role=None,
            reason=cand.reasoning,
            text=_slice_transcript(transcript, cand.start_s, cand.end_s),
        )
        inputs.append(
            CriticInput(
                hook_index=0,
                segments=[seg],
                rationale=cand.reasoning,
                label=cand.quote or cand.suggested_caption,
            )
        )
    return inputs


def _adapt_short_generator_plan(
    plan: ShortGeneratorPlan, transcript: list[dict]
) -> list[CriticInput]:
    """Each ShortCandidate's spans collapse to a list of CritiqueSegments —
    spans inside one short are already a coherent unit, so the rubric grades
    the *short* (one CriticInput) rather than each jump cut individually."""
    inputs: list[CriticInput] = []
    for cand in plan.candidates:
        segs = [
            CritiqueSegment(
                index=i,
                start_s=span.start_s,
                end_s=span.end_s,
                arc_role=span.role or None,
                reason=span.role or "",
                text=_slice_transcript(transcript, span.start_s, span.end_s),
            )
            for i, span in enumerate(cand.spans)
        ]
        inputs.append(
            CriticInput(
                hook_index=0,
                segments=segs,
                rationale=cand.reasoning,
                label=cand.theme,
            )
        )
    return inputs


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_RUBRIC_BY_INTENT: dict[str, str] = {
    "narrative": (
        "Grade against a 3-act / setup-payoff shape. The hook must earn position 0, "
        "the body must escalate or develop, the closer must land the through-line."
    ),
    "peak_highlight": (
        "Single-beat content — grade hook strength and emotional payoff. A "
        "discernible 3-act shape is not required; arc_clarity may score lower "
        "without that being a defect."
    ),
    "assembled_short": (
        "Per-short — grade through-line cohesion and jump-cut tightness. Spans "
        "must compound the theme; redundancy across spans is a defect."
    ),
    "multi_clip": (
        "Per-clip — grade each clip's internal pull. Each candidate stands alone; "
        "do not compare candidates to each other inside one report."
    ),
    "surgical_tighten": (
        "Filler-removal only. Score `hook_strength` and `resolution`. **Set "
        "`arc_clarity` and `transitions` to 0** — the runtime nulls them out for "
        "you because surgical tighten preserves the source order. Only flag issues "
        "when the tightening damaged meaning."
    ),
}


def _segments_for_prompt(input_: CriticInput) -> list[dict]:
    return [
        {
            "index": s.index,
            "start_s": round(s.start_s, 3),
            "end_s": round(s.end_s, 3),
            "arc_role": s.arc_role,
            "reason": s.reason,
            "text": s.text,
        }
        for s in input_.segments
    ]


def _critic_prompt(input_: CriticInput, axes: ResolvedAxes) -> str:
    """Render the single critic prompt. ≤80 lines including the rubric."""
    rubric = _RUBRIC_BY_INTENT.get(
        axes.cut_intent,
        "Grade against general story coherence: hook, arc, transitions, resolution.",
    )
    label_line = f"\nCANDIDATE LABEL: {input_.label}\n" if input_.label else ""
    return f"""You are a senior film editor reviewing a colleague's cut.

You are NOT picking segments. The picks are made. Your job is to read the
words inside each segment and judge whether the cut tells a coherent story.

CUT INTENT: {axes.cut_intent} ({axes.content_type})
RUBRIC: {rubric}
{label_line}
DIRECTOR'S RATIONALE:
{input_.rationale or "(none provided)"}

HOOK INDEX: {input_.hook_index} (0-based, into the segments below)

SEGMENTS (JSON, in play order):
{json.dumps(_segments_for_prompt(input_), separators=(",", ":"))}

Score each dimension 0–100:
- `score` — overall coherence; the editor's gut answer to "would I ship this?"
- `hook_strength` — does segment[hook_index] pull the viewer forward?
- `arc_clarity` — is the cut's shape (setup → development → resolve) discernible?
- `transitions` — do successive segments connect, or is there whiplash?
- `resolution` — does the closer land the through-line?

Then list at most {_MAX_ISSUES} `issues`. Each issue must reference a real
segment by `segment_index`, or `-1` for whole-cut observations. Use
`pair_index` to point at the gap between segment N and N+1 on transition
issues. Severity: `error` for ship-blockers, `warning` for editor-judgement
calls, `info` for nice-to-knows. Categories: non_sequitur, weak_hook,
missing_setup, abrupt_transition, redundancy, unresolved_thread,
inverted_arc, weak_resolution, buried_lede.

Finish with a 1–2 sentence `summary` in editor voice. Do not propose
remediations beyond the optional `suggestion` field on each issue.
"""


# ---------------------------------------------------------------------------
# LLM response shape — drives the structured-output schema
# ---------------------------------------------------------------------------


class _CritiqueLLMResponse(BaseModel):
    """What the model returns. Verdict is derived server-side."""

    score: int = Field(ge=0, le=100)
    hook_strength: int = Field(ge=0, le=100)
    arc_clarity: int = Field(ge=0, le=100)
    transitions: int = Field(ge=0, le=100)
    resolution: int = Field(ge=0, le=100)
    issues: list[CoherenceIssue] = Field(default_factory=list)
    summary: str


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


_LlmCallable = Callable[..., _CritiqueLLMResponse]


def _default_llm(prompt: str) -> _CritiqueLLMResponse:
    return _llm_module.call_structured(
        agent="story_critic",
        prompt=prompt,
        response_schema=_CritiqueLLMResponse,
        temperature=0.3,
        max_retries=2,
    )


def _finalize_report(raw: _CritiqueLLMResponse, axes: ResolvedAxes) -> CoherenceReport:
    """Apply the surgical-tighten carve-out, cap issues, derive verdict."""
    arc_clarity: int | None = raw.arc_clarity
    transitions: int | None = raw.transitions
    if axes.cut_intent == "surgical_tighten":
        arc_clarity = None
        transitions = None
    return CoherenceReport(
        score=raw.score,
        hook_strength=raw.hook_strength,
        arc_clarity=arc_clarity,
        transitions=transitions,
        resolution=raw.resolution,
        issues=_cap_issues(list(raw.issues)),
        summary=raw.summary,
        verdict=_derive_verdict(raw.score),
    )


def critique(
    plan: DirectorPlan
    | AssembledDirectorPlan
    | CuratedDirectorPlan
    | ClipHunterPlan
    | ShortGeneratorPlan,
    *,
    transcript: list[dict] | None = None,
    takes: list[dict] | None = None,
    axes: ResolvedAxes,
    _llm: _LlmCallable | None = None,
) -> CoherenceReport | PerCandidateCoherenceReport:
    """Grade a built plan for story coherence.

    Args:
        plan: One of the five gradeable plan shapes.
        transcript: Flat scrubbed transcript (DirectorPlan, ClipHunterPlan,
            ShortGeneratorPlan).
        takes: Take list (AssembledDirectorPlan, CuratedDirectorPlan).
        axes: ResolvedAxes for the run — supplies the intent-aware rubric.
        _llm: Injectable callable taking ``(prompt: str)`` and returning a
            ``_CritiqueLLMResponse``. Defaults to :func:`_default_llm`.

    Returns:
        ``CoherenceReport`` for one-cut plans;
        ``PerCandidateCoherenceReport`` for ClipHunter / ShortGenerator.
    """
    llm_call = _llm or _default_llm

    # One-cut shapes ---------------------------------------------------------
    if isinstance(plan, DirectorPlan):
        if transcript is None:
            raise ValueError("DirectorPlan critique requires `transcript=`.")
        input_ = _adapt_director_plan(plan, transcript)
        prompt = _critic_prompt(input_, axes)
        return _finalize_report(llm_call(prompt), axes)

    if isinstance(plan, AssembledDirectorPlan):
        if takes is None:
            raise ValueError("AssembledDirectorPlan critique requires `takes=`.")
        input_ = _adapt_assembled_plan(plan, takes)
        prompt = _critic_prompt(input_, axes)
        return _finalize_report(llm_call(prompt), axes)

    if isinstance(plan, CuratedDirectorPlan):
        if takes is None:
            raise ValueError("CuratedDirectorPlan critique requires `takes=`.")
        input_ = _adapt_curated_plan(plan, takes)
        prompt = _critic_prompt(input_, axes)
        return _finalize_report(llm_call(prompt), axes)

    # Per-candidate shapes ---------------------------------------------------
    if isinstance(plan, ClipHunterPlan):
        if transcript is None:
            raise ValueError("ClipHunterPlan critique requires `transcript=`.")
        inputs = _adapt_clip_hunter_plan(plan, transcript)
        return _per_candidate(inputs, axes, llm_call)

    if isinstance(plan, ShortGeneratorPlan):
        if transcript is None:
            raise ValueError("ShortGeneratorPlan critique requires `transcript=`.")
        inputs = _adapt_short_generator_plan(plan, transcript)
        return _per_candidate(inputs, axes, llm_call)

    raise TypeError(f"Unsupported plan type for critique: {type(plan).__name__}")


def _per_candidate(
    inputs: list[CriticInput],
    axes: ResolvedAxes,
    llm_call: _LlmCallable,
) -> PerCandidateCoherenceReport:
    if not inputs:
        return PerCandidateCoherenceReport(
            candidates=[],
            best_candidate_index=0,
            summary="No candidates to grade.",
        )
    reports = [_finalize_report(llm_call(_critic_prompt(inp, axes)), axes) for inp in inputs]
    best_index = max(range(len(reports)), key=lambda i: reports[i].score)
    summary = (
        f"Top pick: candidate {best_index} (score {reports[best_index].score}). "
        f"{len(reports)} candidate(s) graded."
    )
    return PerCandidateCoherenceReport(
        candidates=reports,
        best_candidate_index=best_index,
        summary=summary,
    )
