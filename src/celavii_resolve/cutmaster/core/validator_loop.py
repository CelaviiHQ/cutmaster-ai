"""Outer retry loop wrapping a Director call with Layer A boundary validation.

v4 introduces a second, plan-quality retry on top of the schema-level
retry inside :func:`intelligence.llm.call_structured`. The distinction:

- **Inner retry (existing, in llm.py)** — handles malformed JSON or
  Pydantic validation errors from the model. Retries feed the errors
  back into the prompt so the model can correct its structured output.
- **Outer retry (this module)** — accepts the structured plan but asks
  Layer A whether the proposed CUT POINTS actually work visually. When
  the validator returns ``jarring`` verdicts, this loop re-invokes the
  Director with rejections appended to the prompt. Cap at
  ``MAX_BOUNDARY_RETRIES`` retries; remaining ``jarring`` / ``borderline``
  verdicts become warnings on the plan so the editor has the final say.

The loop is mode-agnostic — it calls a ``director_fn`` closure supplied
by :mod:`http.routes.cutmaster.build`. Each mode wires its own closure
because the arguments (transcript vs. takes vs. takes+groups) differ.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("celavii-resolve.cutmaster.validator_loop")


# Keep the constant importable so tests / future phases can monkeypatch.
MAX_BOUNDARY_RETRIES = 2


@dataclass
class BoundaryValidationResult:
    """Loop output: final plan + verdicts + any remaining warnings."""

    plan: Any  # DirectorPlan / AssembledDirectorPlan / CuratedDirectorPlan / ...
    verdicts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retries_used: int = 0
    skipped: bool = False  # True when the loop short-circuited (e.g. no samples)

    def to_summary(self) -> dict:
        """Compact payload for the /build-plan response + structured logs."""
        return {
            "retries_used": self.retries_used,
            "verdicts": self.verdicts,
            "warnings": self.warnings,
            "skipped": self.skipped,
        }


async def run_with_boundary_validation(
    *,
    director_fn: Callable[[list[dict] | None, list[dict] | None], Awaitable[Any]],
    build_samples: Callable[[Any], list],
    extract_candidate_roster: Callable[[Any], list[dict]] | None = None,
    max_retries: int = MAX_BOUNDARY_RETRIES,
) -> BoundaryValidationResult:
    """Run the Director with boundary validation retries.

    Args:
        director_fn: Awaitable accepting
            ``(rejections: list[dict] | None, candidate_roster: list[dict] | None)``.
            The caller embeds both into the prompt-level user_settings
            (``_boundary_rejections`` + ``_candidate_roster``) so the
            Director can re-address per-candidate cuts without reshuffling
            theme ordering. Linear modes ignore the roster arg and pass
            ``None``. Must always return a plan-like object.
        build_samples: Callable plan → list[BoundarySample]. Returning an
            empty list short-circuits the loop (no boundary signal to
            retry against). Called on every attempt.
        extract_candidate_roster: Optional callable plan → list[dict].
            When provided, the loop calls it on each attempt to produce
            the "keep this theme order" roster for the NEXT retry. Each
            dict carries ``candidate_index`` + ``theme``. Linear-plan
            callers leave this as ``None``; Short Generator supplies it
            so themes + engagement rank survive retries intact.
        max_retries: Max RE-TRIES (not total attempts). 0 = evaluate once,
            no retry. Defaults to :data:`MAX_BOUNDARY_RETRIES` (2).

    Returns:
        :class:`BoundaryValidationResult`. On success without retries,
        ``retries_used == 0``; if the loop exhausts retries with remaining
        ``jarring`` verdicts, those surface in ``warnings`` and the final
        plan (best-effort) is returned.
    """
    from ..analysis.boundary_validator import validate_boundaries

    rejections: list[dict] | None = None
    roster: list[dict] | None = None
    last_verdicts: list[dict] = []
    last_plan: Any = None

    for attempt in range(max_retries + 1):
        plan = await director_fn(rejections, roster)
        last_plan = plan

        # Build the roster BEFORE validation so a subsequent retry can
        # use the themes from THIS attempt (the one being validated).
        if extract_candidate_roster is not None:
            try:
                roster = extract_candidate_roster(plan)
            except Exception as exc:
                log.info(
                    "extract_candidate_roster raised (%s) — continuing without roster",
                    exc,
                )
                roster = None

        samples = build_samples(plan)
        if not samples:
            log.info(
                "boundary_validator: no samples on attempt %d — accepting plan",
                attempt,
            )
            return BoundaryValidationResult(
                plan=plan, verdicts=[], warnings=[], retries_used=attempt, skipped=True
            )

        verdicts = await asyncio.to_thread(validate_boundaries, samples)
        last_verdicts = [v.model_dump() for v in verdicts]

        jarring = [v for v in verdicts if v.verdict == "jarring"]
        borderline = [v for v in verdicts if v.verdict == "borderline"]
        log.info(
            "boundary_validator: attempt=%d cuts=%d jarring=%d borderline=%d",
            attempt,
            len(samples),
            len(jarring),
            len(borderline),
        )

        # Multi-candidate flag: if the caller supplied a roster extractor,
        # or any verdict this batch carries a non-zero candidate_index,
        # warnings render with the "candidate N, cut M" qualifier so
        # candidate 0 (top rank) stays addressable in Review.
        multi_candidate = extract_candidate_roster is not None or any(
            getattr(v, "candidate_index", 0) for v in verdicts
        )

        if not jarring:
            return BoundaryValidationResult(
                plan=plan,
                verdicts=last_verdicts,
                warnings=[_format_warning(v, multi_candidate) for v in borderline],
                retries_used=attempt,
            )

        if attempt >= max_retries:
            # Exhausted retries — surface remaining jarring + borderline as
            # warnings. Editor sees them in Review and can shift by hand.
            warnings = [_format_warning(v, multi_candidate) for v in jarring + borderline]
            log.info(
                "boundary_validator: retries exhausted — %d jarring cut(s) fall through to warnings",
                len(jarring),
            )
            return BoundaryValidationResult(
                plan=plan,
                verdicts=last_verdicts,
                warnings=warnings,
                retries_used=attempt,
            )

        rejections = [
            {
                "candidate_index": v.candidate_index,
                "cut_index": v.cut_index,
                "reason": v.reason,
                "suggestion": v.suggestion,
            }
            for v in jarring
        ]

    # Unreachable — the loop either hits the no-samples shortcut, the
    # no-jarring exit, or the retries-exhausted exit.
    return BoundaryValidationResult(
        plan=last_plan,
        verdicts=last_verdicts,
        warnings=[],
        retries_used=max_retries,
    )


def _format_warning(verdict, multi_candidate: bool = False) -> str:
    """Render a verdict as a single-line Review-screen warning.

    In multi-candidate mode (Short Generator — any roster extractor
    supplied, or any verdict with candidate_index > 0) the prefix is
    ``candidate N, cut M`` so candidate 0 (top rank) stays
    distinguishable from candidate 1, 2… cut_index collisions.

    In single-plan mode (linear modes with candidate_index always 0)
    the prefix is just ``cut M`` — no spurious "candidate 0" noise.
    """
    label = verdict.verdict.upper()
    cand_idx = getattr(verdict, "candidate_index", 0) or 0
    if multi_candidate:
        header = f"candidate {cand_idx}, cut {verdict.cut_index} ({label})"
    else:
        header = f"cut {verdict.cut_index} ({label})"
    parts = [header]
    if verdict.reason:
        parts.append(f"— {verdict.reason}")
    if verdict.suggestion:
        parts.append(f"(suggest: {verdict.suggestion})")
    return " ".join(parts)
