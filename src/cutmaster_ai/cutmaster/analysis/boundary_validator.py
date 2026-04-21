"""Layer A — post-plan boundary validator for Director cuts.

Runs AFTER the Director emits ``plan.selected_clips``. For each proposed
cut (i.e. the transition between two consecutive selected clips), pulls
the literal last frame of the outgoing segment and the first frame of
the incoming segment, then issues a single batched Gemini call that
returns one verdict per cut.

Verdicts drive the outer retry loop in :mod:`core.validator_loop` —
``jarring`` cuts feed rejections back into the Director prompt; after
the retry cap is exhausted, remaining ``jarring`` / ``borderline``
verdicts surface as warnings on the plan so the editor sees them in
Review.

Cache: boundary frames land under
``~/.cutmaster/cutmaster/boundary-frames/v1/<sha1(source_path)>/<ts_ms>.jpg``.
Different retries often propose the same boundary (the Director may
only shift one cut), so the cache hit rate is lower than Layer C's tag
cache but still meaningful.

All vision calls route through :func:`intelligence.llm.call_structured`
so cost telemetry + retry logic stay centralised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ._sanitize import sanitize_prose

log = logging.getLogger("cutmaster-ai.cutmaster.boundary_validator")


CACHE_ROOT = Path.home() / ".cutmaster" / "cutmaster" / "boundary-frames" / "v1"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BoundaryVerdict(BaseModel):
    """One verdict per proposed cut.

    Linear-plan modes (raw_dump / curated / rough_cut) leave
    ``candidate_index`` at its default of 0 — there's only one plan.
    Short Generator populates it per candidate so verdicts for
    ``candidates[2]``'s 3rd cut are addressable as
    ``(candidate_index=2, cut_index=3)`` without collisions across
    the N candidates being validated in one batch.
    """

    candidate_index: int = Field(
        default=0,
        ge=0,
        description=(
            "For multi-candidate plans (Short Generator), which candidate "
            "this cut belongs to. Zero for linear plans."
        ),
    )
    cut_index: int = Field(..., ge=0, description="Index of the cut within its candidate.")
    verdict: Literal["smooth", "borderline", "jarring"] = "smooth"
    reason: str = Field(default="", max_length=200)
    suggestion: str = Field(default="", max_length=200)

    # v4 Phase 4.5.1 — PII scrub on free-prose fields. The prompt already
    # forbids OCR / identifying individuals; this is a second line of
    # defence before the strings hit logs / the Review screen warnings.
    @field_validator("reason", "suggestion", mode="after")
    @classmethod
    def _sanitize_prose(cls, value: str) -> str:
        return sanitize_prose(value) or ""


class BoundaryVerdictResponse(BaseModel):
    """Wire schema enforced on the Gemini multimodal call."""

    verdicts: list[BoundaryVerdict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Boundary sample — resolved from a Director plan
# ---------------------------------------------------------------------------


@dataclass
class BoundarySample:
    """One proposed cut's boundary frames, already mapped to source files.

    ``cut_index`` is the index of the INCOMING segment in the Director's
    ``selected_clips``. So ``cut_index=1`` is the transition from
    ``selected_clips[0]`` → ``selected_clips[1]``.

    ``candidate_index`` identifies which multi-span candidate this cut
    lives in (Short Generator). Linear plans leave it at 0.
    """

    cut_index: int
    out_source_path: str
    out_source_ts_s: float
    in_source_path: str
    in_source_ts_s: float
    candidate_index: int = 0


def _locate_source_frame(
    tl, project, timeline_ts_s: float, *, video_track: int = 1
) -> tuple[str, float] | None:
    """Map a timeline-seconds instant to ``(source_path, source_ts_s)``.

    Walks items on ``video_track``, finds the item overlapping the
    requested timeline frame, then leverages
    :func:`source_resolver.resolve_item_to_segments` to traverse through
    compounds / nested timelines. Returns ``None`` for gaps, generator
    items, or otherwise-unresolvable content — the caller treats those
    as "no boundary sample" rather than crashing.
    """
    from ..media.frame_math import _timeline_fps, _timeline_start_frame
    from ..media.source_resolver import resolve_item_to_segments

    fps = _timeline_fps(tl)
    tl_start = _timeline_start_frame(tl)
    target_frame = tl_start + int(round(float(timeline_ts_s) * fps))

    items = tl.GetItemListInTrack("video", video_track) or []
    for item in items:
        start = int(item.GetStart())
        end = int(item.GetEnd())
        if not (start <= target_frame < end):
            continue
        mp_item = item.GetMediaPoolItem()
        if mp_item is None:
            log.info(
                "boundary at tl=%.3fs lands on item with no media pool item; skipping",
                timeline_ts_s,
            )
            return None
        try:
            segments = resolve_item_to_segments(project, item, outer_fps=fps)
        except Exception as exc:
            log.info("resolve_item_to_segments failed at tl=%.3fs: %s", timeline_ts_s, exc)
            return None
        if not segments:
            return None

        # Local seconds inside this item.
        local_s = (target_frame - start) / fps
        # Clamp to the last representable second so the final frame of a
        # clip still maps cleanly (otherwise float rounding can push it
        # past the last segment's out_s).
        cumul = 0.0
        for seg in segments:
            seg_dur = max(0.0, seg.out_s - seg.in_s)
            if local_s < cumul + seg_dur:
                offset = local_s - cumul
                return str(seg.path), seg.in_s + offset
            cumul += seg_dur
        # Fell off the end — clamp to the last segment's out frame.
        last = segments[-1]
        return str(last.path), max(last.in_s, last.out_s - (1.0 / fps))

    log.info(
        "boundary at tl=%.3fs falls in a V%d gap; skipping",
        timeline_ts_s,
        video_track,
    )
    return None


def build_boundary_samples(
    tl,
    segments,
    project=None,
    *,
    candidate_index: int = 0,
    video_track: int | None = None,
) -> list[BoundarySample]:
    """Resolve each consecutive cut pair in ``segments`` to a ``BoundarySample``.

    For N segments there are N-1 cuts. Segments that cannot be mapped to
    a source frame (gaps / compounds without matching project timelines)
    drop out of the sample list silently — the validator treats those as
    "no signal" rather than failing the whole plan.

    ``project`` is optional; when omitted the Resolve bridge is used.
    ``candidate_index`` tags every produced sample so multi-candidate
    plans (Short Generator) can feed all candidates' cuts through the
    same batched Gemini call without cut_index collisions.
    """
    from ..resolve_ops.track_picker import pick_video_track

    if project is None:
        from ...resolve import _boilerplate  # lazy

        _, project, _ = _boilerplate()

    if video_track is None:
        video_track = pick_video_track(tl)

    samples: list[BoundarySample] = []
    for i in range(len(segments) - 1):
        outgoing = segments[i]
        incoming = segments[i + 1]

        out_loc = _locate_source_frame(tl, project, float(outgoing.end_s), video_track=video_track)
        in_loc = _locate_source_frame(tl, project, float(incoming.start_s), video_track=video_track)
        if out_loc is None or in_loc is None:
            continue
        samples.append(
            BoundarySample(
                cut_index=i + 1,
                candidate_index=candidate_index,
                out_source_path=out_loc[0],
                out_source_ts_s=out_loc[1],
                in_source_path=in_loc[0],
                in_source_ts_s=in_loc[1],
            )
        )

    return samples


def build_short_generator_boundary_samples(
    tl, candidates, project=None, *, video_track: int | None = None
) -> list[BoundarySample]:
    """Walk every Short-Generator candidate's spans and emit one
    sample per internal cut.

    Candidates with fewer than 2 spans contribute nothing (no internal
    cuts). Per the v4 activation matrix, Short Generator validates
    "every span transition" across ALL candidates — a candidate
    engagement-ranked to position N still gets its cuts reviewed so the
    editor can see the whole cost picture before shortlisting.

    ``candidates`` is a list of ``ShortCandidate`` pydantic instances
    from ``ShortGeneratorPlan.candidates``. Kept duck-typed so tests
    can supply dict-shaped fakes without pulling the Director deps.
    """
    from ..core.director import short_candidate_to_segments

    samples: list[BoundarySample] = []
    for ci, cand in enumerate(candidates):
        segs = short_candidate_to_segments(cand)
        if len(segs) < 2:
            continue
        samples.extend(
            build_boundary_samples(
                tl,
                segs,
                project=project,
                candidate_index=ci,
                video_track=video_track,
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------


_PROMPT = """\
You are a video-editorial cut-quality reviewer. The attached images are
ordered pairs: for each proposed cut you will see

  [outgoing frame, incoming frame]

in sequence, so the Nth pair is images at positions (2N, 2N+1).

For EACH cut, return one object in the `verdicts` array matching this
schema:

- candidate_index: integer — MUST equal the candidate_index supplied
  in the CUT INDEX LIST below. Zero for linear plans (raw_dump / curated
  / rough_cut); non-zero for Short Generator candidates.
- cut_index: integer — MUST equal the cut_index supplied in the
  CUT INDEX LIST below (same order as the image pairs).
- verdict: "smooth" | "borderline" | "jarring"
- reason: short phrase (<=200 chars) — what makes the cut feel the way
  it does. Cite the visual cause (shot-scale jump, gesture mid-swing,
  lighting mismatch, axis jump, etc.). Leave blank for smooth cuts.
- suggestion: short phrase (<=200 chars) — an actionable fix the editor
  could try (e.g. "shift 0.4s earlier to land on gesture completion").
  Leave blank for smooth cuts.

GUIDANCE:

- "smooth" — shot-type continuity or deliberate contrast; the cut feels
  intentional.
- "borderline" — noticeable but not disruptive; e.g. similar shots with
  slight framing mismatch, or a minor gesture cut mid-beat.
- "jarring" — disruptive: hand/arm visibly mid-swing, 180-degree axis
  jump, lighting flash, empty-frame cross with no compositional echo.

GUARDRAILS (strict):

- DO NOT transcribe or describe any on-screen text, slides, or UI.
- DO NOT identify individuals by name, role, or personal attribute.
- Describe VISUAL COMPOSITION ONLY.

Return `verdicts` with EXACTLY one entry per cut in the CUT INDEX LIST,
in the same order. Every (candidate_index, cut_index) pair from the
list MUST appear in the response. Do not add extra keys or commentary."""


def _cache_dir_for(source_path: str) -> Path:
    from ..media.ffmpeg_frames import source_key

    return CACHE_ROOT / source_key(source_path)


def validate_boundaries(samples: list[BoundarySample]) -> list[BoundaryVerdict]:
    """Extract boundary frames, call Gemini, return a parallel verdict list.

    Returns an empty list when ``samples`` is empty or when frame extraction
    / the vision call fails outright (the caller treats a missing result
    as "no veto" and moves on).
    """
    if not samples:
        return []

    from ...intelligence.llm import call_structured
    from ..media.ffmpeg_frames import extract_frames

    # Extract frames per (source_path) batched to minimise ffmpeg startup
    # cost. Preserve ordering by mapping (path, ts) pairs back to slots.
    images: list[tuple[bytes, str]] = []
    keys: list[tuple[int, int]] = []  # (candidate_index, cut_index) per sample

    for sample in samples:
        for path, ts in (
            (sample.out_source_path, sample.out_source_ts_s),
            (sample.in_source_path, sample.in_source_ts_s),
        ):
            try:
                frames = extract_frames(
                    path,
                    [ts],
                    cache_dir=_cache_dir_for(path),
                )
            except Exception as exc:
                log.warning(
                    "boundary frame extract failed (%s @ %.3fs): %s",
                    Path(path).name,
                    ts,
                    exc,
                )
                return []
            images.append((frames[0], "image/jpeg"))
        keys.append((sample.candidate_index, sample.cut_index))

    cut_list = "\n".join(f"  - candidate_index={ci}, cut_index={k}" for ci, k in keys)
    prompt = f"{_PROMPT}\n\nCUT INDEX LIST (in image-pair order):\n{cut_list}\n"

    expected = len(samples)

    def _validate(resp: BoundaryVerdictResponse) -> list[str]:
        errors: list[str] = []
        if len(resp.verdicts) != expected:
            errors.append(
                f"verdicts length mismatch: got {len(resp.verdicts)}, expected {expected}"
            )
        seen_pairs = {(v.candidate_index, v.cut_index) for v in resp.verdicts}
        missing = [pair for pair in keys if pair not in seen_pairs]
        if missing:
            errors.append(
                "verdicts missing pairs: "
                + ", ".join(f"(cand={c},cut={k})" for c, k in missing[:5])
            )
        return errors

    try:
        resp = call_structured(
            "boundary_validator",
            prompt,
            BoundaryVerdictResponse,
            images=images,
            validate=_validate,
            accept_best_effort=True,
        )
    except Exception as exc:
        log.warning("boundary_validator call failed: %s — treating as smooth", exc)
        return []

    # Pad any missing (candidate_index, cut_index) pairs with smooth
    # verdicts so the caller sees a full parallel array regardless of
    # accept_best_effort's short responses.
    verdicts = list(resp.verdicts)
    seen_pairs = {(v.candidate_index, v.cut_index) for v in verdicts}
    for ci, k in keys:
        if (ci, k) not in seen_pairs:
            verdicts.append(BoundaryVerdict(candidate_index=ci, cut_index=k, verdict="smooth"))

    # Drop any verdicts the model hallucinated for (candidate, cut) pairs
    # we didn't supply — they're unaddressable in the retry loop.
    verdicts = [v for v in verdicts if (v.candidate_index, v.cut_index) in set(keys)]

    return verdicts
