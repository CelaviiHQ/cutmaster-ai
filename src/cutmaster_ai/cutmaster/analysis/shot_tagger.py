"""Layer C — shot tagging via Gemini vision for each timeline video item.

Runs once per source file during analyze; tags cache under
``~/.cutmaster/cutmaster/shot-tags/v1/<sha1(source_path)>/<ts_ms>.json``
keyed on the SOURCE path + SOURCE timestamp (not timeline time) so
re-ordering / duplicating takes on the timeline reuses previously-tagged
frames for free.

Sampling cadence (per timeline video item):

- 1 frame at ``item_start + 0.3s`` (past the edit in-point).
- 1 frame every 5s within the item.
- 1 frame at ``item_end - 0.3s`` when the item is >= 1s long.

One batched multimodal Gemini call per item (not per frame) via the
chokepoint :func:`intelligence.llm.call_structured`. The model sees the
frames in temporal order and returns a parallel ``ShotTag`` array.

After tagging, each transcript word gains a ``shot_tag`` field pointing
to the most-recent tag whose timeline-time precedes the word. Phase 4.1
renders these as a coalesced-range block in the Director prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ._sanitize import sanitize_prose

log = logging.getLogger("cutmaster-ai.cutmaster.shot_tagger")


CACHE_ROOT = Path.home() / ".cutmaster" / "cutmaster" / "shot-tags" / "v1"

# Sampling tuneables. Surfaced as constants so tests can monkeypatch; env
# overrides aren't exposed because re-tagging on cadence change would
# invalidate the cache anyway (timestamps are the cache key).
FRAME_EDGE_OFFSET_S = 0.3
FRAME_STRIDE_S = 5.0
# Hard cap so pathological 30-minute single items don't send 360 frames in
# one call. At >20 frames the Gemini prompt starts dominating cost without
# improving tag quality. Overflow items chunk into multiple calls.
MAX_FRAMES_PER_CALL = 20


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ShotTag(BaseModel):
    """One tag per sampled frame. Mirrors the v4 proposal schema."""

    shot_type: Literal[
        "closeup",
        "medium",
        "wide",
        "over_shoulder",
        "broll",
        "title_card",
        "unknown",
    ] = "unknown"
    framing: Literal[
        "speaker_centered",
        "speaker_side",
        "no_speaker",
        "unknown",
    ] = "unknown"
    gesture_intensity: Literal[
        "still",
        "calm",
        "emphatic",
        "unknown",
    ] = "unknown"
    visual_energy: int = Field(default=0, ge=0, le=10)
    notable: str | None = Field(default=None, max_length=80)

    # v4 Phase 4.5.1 — strip PII-ish patterns if the model slips past
    # the GUARDRAILS block. Belt-and-braces: the prompt explicitly
    # forbids OCR / personal details, and this scrub is a second line.
    @field_validator("notable", mode="after")
    @classmethod
    def _sanitize_notable(cls, value: str | None) -> str | None:
        return sanitize_prose(value)


class ShotTagResponse(BaseModel):
    """Schema enforced on the Gemini multimodal call.

    ``tags`` length must equal the frame count supplied — the caller
    validates this via ``call_structured``'s ``validate`` hook.
    """

    tags: list[ShotTag] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_dir(source_path: str) -> Path:
    from ..media.ffmpeg_frames import source_key

    return CACHE_ROOT / source_key(source_path)


def _cache_path(source_path: str, ts_s: float) -> Path:
    ts_ms = int(round(float(ts_s) * 1000))
    return _cache_dir(source_path) / f"{ts_ms:010d}.json"


def _load_cached_tag(source_path: str, ts_s: float) -> ShotTag | None:
    path = _cache_path(source_path, ts_s)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        log.warning("shot-tag cache %s unreadable: %s", path, exc)
        return None
    try:
        return ShotTag.model_validate(payload)
    except Exception as exc:
        log.warning("shot-tag cache %s failed validation: %s", path, exc)
        return None


def _save_cached_tag(source_path: str, ts_s: float, tag: ShotTag) -> None:
    path = _cache_path(source_path, ts_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(tag.model_dump(), default=str))
    except OSError as exc:
        log.warning("shot-tag cache write failed (%s): %s", path, exc)


def _write_manifest(source_path: str, duration_s: float) -> None:
    """Best-effort manifest — informative only, never read back by the code.

    Useful when poking around the cache directory; a later schema bump
    can promote to v2/ without touching this file.
    """
    from datetime import datetime

    cache_dir = _cache_dir(source_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_path": source_path,
        "duration_s": round(duration_s, 3),
        "last_tagged_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        (cache_dir / "manifest.json").write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        log.debug("manifest write skipped: %s", exc)


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass
class VideoItemSpec:
    """What the tagger needs to know about one timeline video item.

    ``segments`` is a list of ``(source_path, in_s, out_s)`` covering the
    item, resolved through compounds via ``source_resolver``. Simple
    file-backed items collapse to a single segment.
    """

    item_index: int  # 0-based within V1
    source_name: str
    timeline_offset_s: float
    duration_s: float
    segments: list[tuple[str, float, float]]


def build_video_item_specs(
    tl, project=None, *, video_track: int | None = None
) -> list[VideoItemSpec]:
    """Walk the picked video track and return one spec per item.

    Mirrors :func:`stt.per_clip.build_clip_audio_specs` but for the video
    side. Separate helper because audio / video tracks aren't guaranteed
    to be 1:1 (adjustment clips, B-roll only on V1, etc.) — pairing
    happens implicitly via the stitched transcript's timeline timestamps.

    ``video_track`` is 1-based. ``None`` (default) auto-picks via
    :func:`track_picker.pick_video_track`.
    """
    from ..media.frame_math import _timeline_fps, _timeline_start_frame
    from ..media.source_resolver import resolve_item_to_segments
    from ..resolve_ops.track_picker import pick_video_track

    if project is None:
        from ...resolve import _boilerplate  # lazy — Resolve connection

        _, project, _ = _boilerplate()

    if video_track is None:
        video_track = pick_video_track(tl)

    fps = _timeline_fps(tl)
    tl_start = _timeline_start_frame(tl)
    items = tl.GetItemListInTrack("video", video_track) or []
    out: list[VideoItemSpec] = []

    for idx, item in enumerate(items):
        mp_item = item.GetMediaPoolItem()
        if not mp_item:
            log.info("Video item %d has no media pool item (generator?); skipping", idx)
            continue

        segments = resolve_item_to_segments(project, item, outer_fps=fps)
        if not segments:
            log.info(
                "Video item %d ('%s') could not be resolved to a source file; skipping",
                idx,
                mp_item.GetName() or "?",
            )
            continue

        duration_frames = item.GetDuration()
        timeline_offset_frame = item.GetStart() - tl_start
        seg_tuples = [(str(s.path), s.in_s, s.out_s) for s in segments]

        source_name = str(mp_item.GetName() or f"item_{idx}")
        if len(segments) > 1:
            source_name = f"{source_name} (compound, {len(segments)} segments)"

        out.append(
            VideoItemSpec(
                item_index=idx,
                source_name=source_name,
                timeline_offset_s=timeline_offset_frame / fps,
                duration_s=duration_frames / fps,
                segments=seg_tuples,
            )
        )

    return out


# ---------------------------------------------------------------------------
# Sampling plan
# ---------------------------------------------------------------------------


@dataclass
class FrameSample:
    """One frame to extract — carries both source-time (for extraction +
    cache key) and timeline-time (for attaching the resulting tag to
    transcript words).
    """

    source_path: str
    source_ts_s: float
    timeline_ts_s: float


def plan_samples(spec: VideoItemSpec) -> list[FrameSample]:
    """Derive the sample timestamps for one video item.

    Walks the item's segments in order; each segment contributes a start
    edge, intermediate frames every ``FRAME_STRIDE_S``, and an end edge.
    Edges inside the item (segment boundaries) are not duplicated — only
    the first segment's start edge and the last segment's end edge show
    up, matching the "once per item" cadence in the proposal.
    """
    segments = spec.segments
    if not segments:
        return []

    samples: list[FrameSample] = []
    tl_cursor = spec.timeline_offset_s
    item_end_tl = spec.timeline_offset_s + spec.duration_s

    for seg_idx, (path, in_s, out_s) in enumerate(segments):
        seg_dur = max(0.0, out_s - in_s)
        seg_start_tl = tl_cursor
        seg_end_tl = tl_cursor + seg_dur

        # Start edge — only on the first segment.
        if seg_idx == 0 and seg_dur > FRAME_EDGE_OFFSET_S:
            samples.append(
                FrameSample(
                    source_path=path,
                    source_ts_s=in_s + FRAME_EDGE_OFFSET_S,
                    timeline_ts_s=seg_start_tl + FRAME_EDGE_OFFSET_S,
                )
            )

        # Intermediate strides. Start at the first multiple of stride past
        # the edge offset to avoid double-sampling the start frame.
        t = FRAME_STRIDE_S
        while t < seg_dur - FRAME_EDGE_OFFSET_S:
            samples.append(
                FrameSample(
                    source_path=path,
                    source_ts_s=in_s + t,
                    timeline_ts_s=seg_start_tl + t,
                )
            )
            t += FRAME_STRIDE_S

        # End edge — only on the last segment.
        if seg_idx == len(segments) - 1 and seg_dur > FRAME_EDGE_OFFSET_S:
            end_src = out_s - FRAME_EDGE_OFFSET_S
            # Skip if we'd duplicate the start frame (item < 0.6s).
            if (
                samples
                and abs(samples[-1].timeline_ts_s - (item_end_tl - FRAME_EDGE_OFFSET_S)) < 0.1
            ):
                pass
            else:
                samples.append(
                    FrameSample(
                        source_path=path,
                        source_ts_s=end_src,
                        timeline_ts_s=item_end_tl - FRAME_EDGE_OFFSET_S,
                    )
                )

        tl_cursor = seg_end_tl

    return samples


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------


_PROMPT = """\
You are a video-editorial shot-classification assistant. The attached
images are sampled frames from a single source clip, in temporal order.

For EACH image, return one object in the `tags` array following this
schema exactly (enum values are lowercase):

- shot_type: closeup | medium | wide | over_shoulder | broll | title_card | unknown
- framing:   speaker_centered | speaker_side | no_speaker | unknown
- gesture_intensity: still | calm | emphatic | unknown
- visual_energy: integer 0..10 (overall scene energy / motion)
- notable: optional short prose <= 80 characters, or null

GUARDRAILS (strict):

- DO NOT transcribe, summarise, or describe any on-screen text, slides,
  whiteboards, documents, notes, or user-interface content.
- DO NOT identify, describe, or speculate about specific individuals by
  name, role, or personal attribute.
- `notable` must describe VISUAL COMPOSITION ONLY — e.g. "speaker leans
  in", "product close-up", "quick cut to b-roll". Leave it null if
  nothing visually noteworthy stands out.

Return `tags` with EXACTLY the same number of entries as images supplied,
in the same order. Do not add commentary or additional keys."""


def _tag_item_sync(
    spec: VideoItemSpec,
    samples: list[FrameSample],
) -> list[ShotTag]:
    """Synchronous worker: extract frames, call Gemini, return tags.

    Honours the per-frame cache: if every sample is already cached, skips
    the Gemini call entirely. Otherwise extracts ONLY missing frames and
    asks Gemini for tags on those, then stitches cached + fresh back into
    a parallel array.
    """
    from ...intelligence.llm import call_structured
    from ..media.ffmpeg_frames import extract_frames

    # Partition into cached / uncached.
    result: list[ShotTag | None] = [None] * len(samples)
    missing_indices: list[int] = []
    for i, s in enumerate(samples):
        cached = _load_cached_tag(s.source_path, s.source_ts_s)
        if cached is not None:
            result[i] = cached
        else:
            missing_indices.append(i)

    if not missing_indices:
        return [t for t in result if t is not None]

    # Chunk by source_path so ffmpeg only opens each file once, and chunk
    # further so any single Gemini call stays under MAX_FRAMES_PER_CALL.
    by_source: dict[str, list[int]] = {}
    for i in missing_indices:
        by_source.setdefault(samples[i].source_path, []).append(i)

    for source_path, indices in by_source.items():
        # Extract in slices of MAX_FRAMES_PER_CALL.
        for start in range(0, len(indices), MAX_FRAMES_PER_CALL):
            slice_idxs = indices[start : start + MAX_FRAMES_PER_CALL]
            ts_list = [samples[i].source_ts_s for i in slice_idxs]
            try:
                frames = extract_frames(source_path, ts_list)
            except Exception as exc:
                log.warning(
                    "frame extraction failed for %s: %s — leaving tags as unknown",
                    Path(source_path).name,
                    exc,
                )
                for i in slice_idxs:
                    result[i] = ShotTag()
                continue

            images = [(data, "image/jpeg") for data in frames]
            expected_count = len(slice_idxs)

            def _validate(resp: ShotTagResponse, expected: int = expected_count) -> list[str]:
                if len(resp.tags) != expected:
                    return [f"tags length mismatch: got {len(resp.tags)}, expected {expected}"]
                return []

            try:
                resp = call_structured(
                    "shot_tagger",
                    _PROMPT,
                    ShotTagResponse,
                    images=images,
                    validate=_validate,
                    accept_best_effort=True,
                )
            except Exception as exc:
                log.warning(
                    "shot_tagger call failed for %s: %s — leaving tags as unknown",
                    Path(source_path).name,
                    exc,
                )
                for i in slice_idxs:
                    result[i] = ShotTag()
                continue

            # Pad / truncate to match slice length (accept_best_effort can
            # return a short response).
            tags = list(resp.tags)
            if len(tags) < len(slice_idxs):
                tags.extend([ShotTag()] * (len(slice_idxs) - len(tags)))
            elif len(tags) > len(slice_idxs):
                tags = tags[: len(slice_idxs)]

            for i, tag in zip(slice_idxs, tags, strict=True):
                result[i] = tag
                _save_cached_tag(samples[i].source_path, samples[i].source_ts_s, tag)

    # Fill any residual None (shouldn't happen, defensive).
    return [t if t is not None else ShotTag() for t in result]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class TaggedFrame:
    """Flat record attached to the run state / transcript words."""

    item_index: int
    source_path: str
    source_ts_s: float
    timeline_ts_s: float
    tag: ShotTag


async def tag_video_items(
    specs: list[VideoItemSpec],
    *,
    on_item_done=None,
    max_concurrency: int = 3,
) -> tuple[list[TaggedFrame], dict]:
    """Run the tagger across every spec. Returns ``(tagged_frames, stats)``.

    ``on_item_done`` is an optional ``async`` callback invoked with
    ``(spec_index, spec, tagged_frames_for_this_item)`` after each item
    completes — the pipeline wires it to an SSE progress emit.

    Concurrency is capped at ``max_concurrency`` to stay polite with the
    Gemini quota; the v4 proposal's rate-limiting pass (Phase 4.5.2) may
    tighten this further.
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    all_tagged: list[list[TaggedFrame]] = [[] for _ in specs]
    stats = {
        "items_total": len(specs),
        "items_tagged": 0,
        "frames_total": 0,
        "frames_cache_hits": 0,
    }

    async def _one(idx: int, spec: VideoItemSpec) -> None:
        samples = plan_samples(spec)
        if not samples:
            if on_item_done is not None:
                await on_item_done(idx, spec, [])
            return

        # Count cache hits up front so the stats reflect pre-call state.
        hits = 0
        for s in samples:
            if _cache_path(s.source_path, s.source_ts_s).exists():
                hits += 1

        async with sem:
            tags = await asyncio.to_thread(_tag_item_sync, spec, samples)

        tagged = [
            TaggedFrame(
                item_index=spec.item_index,
                source_path=s.source_path,
                source_ts_s=s.source_ts_s,
                timeline_ts_s=s.timeline_ts_s,
                tag=tag,
            )
            for s, tag in zip(samples, tags, strict=True)
        ]
        all_tagged[idx] = tagged
        stats["items_tagged"] += 1
        stats["frames_total"] += len(tagged)
        stats["frames_cache_hits"] += hits

        _write_manifest(
            spec.segments[0][0] if spec.segments else spec.source_name,
            spec.duration_s,
        )

        if on_item_done is not None:
            await on_item_done(idx, spec, tagged)

    await asyncio.gather(*(_one(i, s) for i, s in enumerate(specs)))

    flat: list[TaggedFrame] = []
    for group in all_tagged:
        flat.extend(group)
    flat.sort(key=lambda t: t.timeline_ts_s)
    return flat, stats


# ---------------------------------------------------------------------------
# Attaching tags to transcript words
# ---------------------------------------------------------------------------


def attach_tags_to_transcript(
    transcript: list[dict],
    tagged: list[TaggedFrame],
) -> list[dict]:
    """Annotate each word with the nearest preceding shot tag.

    ``tagged`` must be sorted by ``timeline_ts_s``. For each word, we
    bisect to find the tag with the largest ``timeline_ts_s <= word.start_time``
    and copy its dict representation onto ``word["shot_tag"]``. Words that
    sit before the first tag fall back to the first tag so every word has
    a tag (the Director prompt assumes this).

    Returns a new list of dict copies; the input transcript is not mutated.
    """
    if not tagged:
        return list(transcript)

    # Pre-serialise tag payloads — cheap, and avoids re-running model_dump
    # for every word.
    tag_ts = [t.timeline_ts_s for t in tagged]
    tag_payloads = [
        {
            "item_index": t.item_index,
            "source_ts_s": round(t.source_ts_s, 3),
            "timeline_ts_s": round(t.timeline_ts_s, 3),
            **t.tag.model_dump(),
        }
        for t in tagged
    ]

    import bisect

    out: list[dict] = []
    for word in transcript:
        start = float(word.get("start_time", 0.0))
        # Find rightmost tag with timeline_ts_s <= start
        idx = bisect.bisect_right(tag_ts, start) - 1
        if idx < 0:
            idx = 0
        new_word = dict(word)
        new_word["shot_tag"] = tag_payloads[idx]
        out.append(new_word)
    return out
