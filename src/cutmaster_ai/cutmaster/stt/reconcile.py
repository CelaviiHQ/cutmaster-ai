"""Cross-clip speaker reconciliation (v2-6 follow-up).

Per-clip STT transcribes each timeline item in isolation, so the
``speaker_id`` values Gemini assigns are **clip-local**: clip 0's "S1" is
not necessarily the same person as clip 1's "S1". Stitching those
transcripts without reconciliation gives a fake multi-speaker roster for
solo vlogs (each clip starts fresh at S1/S2/…) or an inconsistent mapping
for real multi-speaker content.

This module provides two reconciliation strategies driven by a
user-supplied ``expected_speakers`` hint set on the Preset screen:

- **1 (solo)** → :func:`collapse_to_solo` — pure, no LLM. Every word's
  ``speaker_id`` becomes ``"S1"``. Right for vlog / tutorial / solo
  podcast-to-camera shoots.
- **≥2** → :func:`reconcile_with_llm` — pulls representative quotes per
  (clip, clip-local-id), makes one cheap Gemini-Flash-Lite call, and
  applies the returned mapping so every clip agrees on a global
  ``S1..SN`` namespace.
- **None / 0** → no reconciliation; caller leaves the raw per-clip IDs
  alone.

All helpers below are pure except :func:`reconcile_with_llm`, which goes
through :mod:`llm` and is therefore mockable via a ``caller`` injection
argument for tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ...intelligence import llm

log = logging.getLogger("cutmaster-ai.cutmaster.speaker_reconcile")


# ---------------------------------------------------------------------------
# Solo collapse — trivial, no LLM
# ---------------------------------------------------------------------------


def collapse_to_solo(transcript: list[dict]) -> list[dict]:
    """Return a copy of ``transcript`` with every ``speaker_id`` → ``"S1"``.

    Input is not mutated. Words that were already ``"S1"`` still get a fresh
    dict so downstream consumers can rely on identity.
    """
    out: list[dict] = []
    for w in transcript:
        new = dict(w)
        new["speaker_id"] = "S1"
        out.append(new)
    return out


# ---------------------------------------------------------------------------
# LLM reconciliation
# ---------------------------------------------------------------------------


class _ReconcileMapEntry(BaseModel):
    """One row of the LLM's mapping response."""

    clip_index: int = Field(..., ge=0)
    local_id: str = Field(..., description="The per-clip speaker_id as transcribed.")
    global_id: str = Field(
        ...,
        description="Global speaker id in S1..SN form where N ≤ expected_speakers.",
    )


class SpeakerReconciliation(BaseModel):
    """Structured response from the reconciler."""

    mapping: list[_ReconcileMapEntry]
    detected_speakers: int = Field(
        ...,
        ge=1,
        description=(
            "How many distinct real-world speakers the model believes are present "
            "after reconciliation (may differ from the user's expected count)."
        ),
    )
    reasoning: str = Field(
        default="",
        description="Short explanation — surfaced in the run state for the UI.",
    )


def _collect_local_samples(
    transcript: list[dict],
    max_samples_per_key: int = 3,
    max_sample_words: int = 12,
) -> list[dict]:
    """Build one sample row per (clip_index, speaker_id) for the LLM prompt.

    Each row carries up to ``max_samples_per_key`` distinct short quotes so
    the model has something to ground the mapping on. Short quotes keep the
    prompt compact — 3 clips × 4 speakers × 3 quotes × ~10 words is under
    500 tokens even before the mapping response.
    """
    # Keyed by (clip_index, local_speaker_id).
    buckets: dict[tuple[int, str], list[list[str]]] = {}
    running_words: dict[tuple[int, str], list[str]] = {}

    for w in transcript:
        clip = w.get("clip_index")
        sid = w.get("speaker_id")
        if clip is None or not sid:
            continue
        key = (int(clip), str(sid))
        running_words.setdefault(key, []).append(str(w.get("word", "")))

    for key, words in running_words.items():
        # Chunk the running words into short quotes — split every ~12 words.
        quotes: list[list[str]] = []
        current: list[str] = []
        for token in words:
            current.append(token)
            if len(current) >= max_sample_words:
                quotes.append(current)
                current = []
                if len(quotes) >= max_samples_per_key:
                    break
        if current and len(quotes) < max_samples_per_key:
            quotes.append(current)
        buckets[key] = quotes[:max_samples_per_key]

    samples: list[dict] = []
    for (clip, sid), quote_tokens in sorted(buckets.items()):
        samples.append(
            {
                "clip_index": clip,
                "local_id": sid,
                "quotes": [" ".join(q) for q in quote_tokens],
                "word_count": len(running_words[(clip, sid)]),
            }
        )
    return samples


def _reconcile_prompt(
    samples: list[dict],
    expected_speakers: int,
) -> str:
    """Render the reconciler prompt. Kept small so Flash-Lite is cheap."""
    import json as _json

    return f"""You resolve cross-clip speaker identities on a video-editing transcript.

Each source clip was transcribed independently, so the `speaker_id`s are **clip-local**: clip 0's "S1" is not necessarily the same person as clip 1's "S1". Your job is to assign a consistent **global** id in the form S1..S{expected_speakers} (at most {expected_speakers}) to every (clip_index, local_id) pair below, so identical people across clips share the same global id.

Use the quotes as your only evidence: matching tone, vocabulary, role (interviewer vs interviewee), and content continuity. When uncertain, prefer merging over splitting — the editor would rather see one roster entry covering a probable match than two fragmented ones.

If you believe fewer than {expected_speakers} real speakers exist, reflect that in `detected_speakers` and use only S1..Sn where n is what you actually found. If you believe more exist, still cap the mapping at S{expected_speakers} (merge the rarest ones) and note it in `reasoning`.

SAMPLES (JSON):
{_json.dumps(samples, separators=(",", ":"))}

Return a `SpeakerReconciliation` with:
- `mapping`: one entry per input (clip_index, local_id) pair.
- `detected_speakers`: the count you actually used (1 ≤ n ≤ {expected_speakers}).
- `reasoning`: 1–2 sentences on how you decided.
"""


def _validate_reconciliation(
    plan: SpeakerReconciliation,
    samples: list[dict],
    expected_speakers: int,
) -> list[str]:
    """Check coverage + id shape. Fed into the call_structured retry loop."""
    errors: list[str] = []
    sample_keys = {(s["clip_index"], s["local_id"]) for s in samples}
    mapped_keys = {(e.clip_index, e.local_id) for e in plan.mapping}

    missing = sample_keys - mapped_keys
    extra = mapped_keys - sample_keys
    if missing:
        errors.append(
            f"mapping is missing {len(missing)} (clip_index, local_id) pair(s): {sorted(missing)}"
        )
    if extra:
        errors.append(f"mapping references {len(extra)} unknown pair(s): {sorted(extra)}")

    used_globals: set[str] = set()
    for entry in plan.mapping:
        gid = entry.global_id
        if not (gid.startswith("S") and gid[1:].isdigit()):
            errors.append(f"global_id '{gid}' must match S<number>")
            continue
        n = int(gid[1:])
        if n < 1 or n > expected_speakers:
            errors.append(f"global_id {gid} outside S1..S{expected_speakers}")
        used_globals.add(gid)

    if plan.detected_speakers < 1 or plan.detected_speakers > expected_speakers:
        errors.append(f"detected_speakers={plan.detected_speakers} outside 1..{expected_speakers}")

    return errors


def _apply_mapping(
    transcript: list[dict],
    mapping: dict[tuple[int, str], str],
) -> list[dict]:
    """Rewrite ``speaker_id`` on every word per the mapping. Unmapped words
    fall through unchanged (defensive — shouldn't happen after validation)."""
    out: list[dict] = []
    for w in transcript:
        key = (int(w.get("clip_index", -1)), str(w.get("speaker_id", "")))
        if key in mapping:
            new = dict(w)
            new["speaker_id"] = mapping[key]
            out.append(new)
        else:
            out.append(w)
    return out


def reconcile_with_llm(
    transcript: list[dict],
    expected_speakers: int,
    *,
    caller: Callable[..., SpeakerReconciliation] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Cross-clip speaker reconciliation via a structured Gemini call.

    Args:
        transcript: stitched per-clip STT output (each word carries
            ``clip_index`` + ``speaker_id``).
        expected_speakers: user-supplied hint (≥2). Caps the global roster.
        caller: test seam — defaults to :func:`llm.call_structured`.

    Returns:
        ``(new_transcript, summary)`` where ``summary`` carries
        ``{"detected_speakers", "mapping", "reasoning", "roster"}``.

    Raises:
        ValueError: the transcript has no ``clip_index`` annotations — the
            caller should only invoke this on per-clip STT output.
        llm.AgentError: the reconciler failed validation after all retries.
    """
    if expected_speakers < 2:
        raise ValueError(
            "reconcile_with_llm requires expected_speakers >= 2; "
            "use collapse_to_solo for the solo case"
        )

    samples = _collect_local_samples(transcript)
    if not samples:
        raise ValueError("transcript has no clip_index annotations — is per_clip_stt on?")

    # Shortcut: only one (clip, local_id) pair means one local speaker total
    # and nothing to reconcile. Collapse to S1 so downstream sees a clean
    # roster without paying for an LLM call.
    if len(samples) == 1:
        mapping = {(samples[0]["clip_index"], samples[0]["local_id"]): "S1"}
        new_transcript = _apply_mapping(transcript, mapping)
        return new_transcript, {
            "detected_speakers": 1,
            "mapping": [
                {"clip_index": c, "local_id": lid, "global_id": gid}
                for (c, lid), gid in mapping.items()
            ],
            "reasoning": "only one local speaker across all clips — trivial merge",
            "roster": ["S1"],
        }

    prompt = _reconcile_prompt(samples, expected_speakers)
    call = caller or (
        lambda: llm.call_structured(
            agent="reconcile",
            prompt=prompt,
            response_schema=SpeakerReconciliation,
            validate=lambda p: _validate_reconciliation(p, samples, expected_speakers),
            temperature=0.2,
        )
    )
    plan: SpeakerReconciliation = call()

    mapping = {(entry.clip_index, entry.local_id): entry.global_id for entry in plan.mapping}
    new_transcript = _apply_mapping(transcript, mapping)

    roster = sorted(
        {e.global_id for e in plan.mapping},
        key=lambda s: int(s[1:]) if s[1:].isdigit() else 999,
    )
    summary = {
        "detected_speakers": plan.detected_speakers,
        "mapping": [e.model_dump() for e in plan.mapping],
        "reasoning": plan.reasoning,
        "roster": roster,
    }
    log.info(
        "Reconciled speakers: %d → %d (roster=%s)",
        len(samples),
        len(roster),
        roster,
    )
    return new_transcript, summary
