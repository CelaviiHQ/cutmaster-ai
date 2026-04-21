"""Detect near-duplicate takes across clips on a per-clip-STT transcript.

When the editor drops three takes of "so the mall is closing today" on the
timeline, per-clip STT transcribes all three and the Director sees the
same line three times. Without take-awareness it can pick spans from all
three, producing a stuttering cut. This module groups clips whose
transcripts are substantially the same so the prompt + validator can
force the Director to choose *one* take per group.

Approach: join each clip's words into a lowercased text, compare pairs
with :class:`difflib.SequenceMatcher`, union-find clips whose ratio is at
or above ``similarity_threshold`` (default 0.6). O(N²) pairwise but on
realistic timelines (≤ 30 clips) it's well under a second — no embedding
service / optional dependency needed.

Only runs on per-clip-STT transcripts where words carry ``clip_index``.
Whole-timeline STT returns an empty grouping cleanly.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

log = logging.getLogger("cutmaster-ai.cutmaster.take_dedup")


DEFAULT_SIMILARITY_THRESHOLD = 0.6
# Clips shorter than this add noise to the comparison — a 4-word take is
# too thin to call "substantially the same line" as another 4-word take.
MIN_WORDS_FOR_DEDUP = 20


def _clip_texts(transcript: list[dict]) -> dict[int, str]:
    """Build ``{clip_index: joined lowercased text}`` for eligible clips."""
    buckets: dict[int, list[str]] = {}
    for w in transcript:
        ci = w.get("clip_index")
        if ci is None:
            continue
        word = str(w.get("word", "")).strip()
        if not word:
            continue
        buckets.setdefault(int(ci), []).append(word)
    return {
        ci: " ".join(words).lower()
        for ci, words in buckets.items()
        if len(words) >= MIN_WORDS_FOR_DEDUP
    }


def detect_take_groups(
    transcript: list[dict],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[list[int]]:
    """Return groups of clip indices that are near-duplicate takes.

    Each returned list contains ≥ 2 clip_index values — singletons aren't
    emitted since the validator doesn't need them. Groups are ordered by
    first-clip-index ascending so the output is deterministic and
    readable in prompt blocks.
    """
    texts = _clip_texts(transcript)
    indices = sorted(texts.keys())
    if len(indices) < 2:
        return []

    parent: dict[int, int] = {i: i for i in indices}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i_idx, a in enumerate(indices):
        text_a = texts[a]
        for b in indices[i_idx + 1 :]:
            ratio = SequenceMatcher(None, text_a, texts[b]).ratio()
            if ratio >= similarity_threshold:
                log.info("take_dedup: clip %d ~ clip %d (ratio %.2f)", a, b, ratio)
                union(a, b)

    roots: dict[int, list[int]] = {}
    for ci in indices:
        roots.setdefault(find(ci), []).append(ci)

    return sorted(
        (sorted(g) for g in roots.values() if len(g) > 1),
        key=lambda g: g[0],
    )
