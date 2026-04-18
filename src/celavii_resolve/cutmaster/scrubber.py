"""Deterministic transcript scrubber — filler removal, dead air, restart collapse.

Runs before the Director LLM so the agent sees a clean transcript. Every
dropped word is logged with a reason so the UI can surface diffs.

No LLM in this module — all rule-based. Preset parameters (spec §3.1) get
threaded in via ``ScrubParams``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Sensible defaults — vlog-style.
DEFAULT_FILLERS = frozenset(
    {
        "um",
        "uh",
        "ah",
        "er",
        "erm",
        "uhh",
        "umm",
        "uhm",
        "hmm",
        "mhm",
        "mmm",
    }
)

DEFAULT_DEAD_AIR_S = 0.7


RemovalReason = Literal["filler", "dead_air", "restart"]


class ScrubParams(BaseModel):
    remove_fillers: bool = True
    filler_vocabulary: list[str] = Field(default_factory=lambda: sorted(DEFAULT_FILLERS))
    remove_dead_air: bool = True
    dead_air_threshold_s: float = DEFAULT_DEAD_AIR_S
    collapse_restarts: bool = True
    restart_min_run: int = 3  # min consecutive tokens to treat as a restart prefix
    restart_window_s: float = 3.0  # repeat must start within this much of the discarded end


class ScrubResult(BaseModel):
    kept: list[dict]
    removed: list[dict]  # each removed word has _reason
    counts: dict[str, int]  # {"filler": n, "dead_air": n, "restart": n}
    original_count: int
    kept_count: int


def _normalize(tok: str) -> str:
    return tok.strip().strip(".,!?;:…—-\"'`").lower()


def _is_filler(word: dict, vocab: frozenset[str]) -> bool:
    return _normalize(word["word"]) in vocab


def _mark_dead_air(words: list[dict], threshold_s: float) -> set[int]:
    """Return indices of words preceded by a gap > threshold.

    The gap itself isn't a word — we don't remove anything here, we just
    track which words follow a long silence so we can optionally drop them
    if they're also on the filler list or if we're collapsing whole pauses.

    Current behavior: we DO NOT remove normal words just for following a
    gap; we only drop filler words that happen to appear during dead air,
    plus any empty-text tokens. This keeps content words safe.
    """
    flagged: set[int] = set()
    for i in range(1, len(words)):
        gap = words[i].get("start_time", 0.0) - words[i - 1].get("end_time", 0.0)
        if gap > threshold_s:
            flagged.add(i)
    return flagged


def _find_restart_runs(
    words: list[dict],
    min_run: int,
    window_s: float,
) -> set[int]:
    """Detect 'I was going, I was going to say...' style restarts.

    Scans for a run of ``min_run`` consecutive tokens that repeats verbatim
    within ``window_s`` after the run ends. Marks the FIRST occurrence for
    removal (keep the completed thought).

    Returns a set of indices to drop.
    """
    drop: set[int] = set()
    n = len(words)
    norm = [_normalize(w["word"]) for w in words]

    i = 0
    while i < n - min_run * 2 + 1:
        run_end = i + min_run
        run_tokens = norm[i:run_end]
        # Look for the next occurrence of this exact run
        # within the time window, after the run.
        run_end_time = words[run_end - 1].get("end_time", 0.0)
        for j in range(run_end, n - min_run + 1):
            if words[j].get("start_time", 0.0) - run_end_time > window_s:
                break
            if norm[j : j + min_run] == run_tokens:
                # Mark the early occurrence [i, run_end) for removal.
                # Extend through any further matched prefix between i..j.
                for k in range(i, j):
                    drop.add(k)
                i = j
                break
        i += 1
    return drop


def scrub(words: list[dict], params: ScrubParams | None = None) -> ScrubResult:
    """Apply deterministic scrubbing rules.

    Input words are dicts with ``word``, ``start_time``, ``end_time``,
    and optionally ``speaker_id``.
    """
    p = params or ScrubParams()
    vocab = frozenset(w.lower() for w in p.filler_vocabulary)
    n0 = len(words)

    counts = {"filler": 0, "dead_air": 0, "restart": 0}

    dead_air_idx = _mark_dead_air(words, p.dead_air_threshold_s) if p.remove_dead_air else set()
    restart_idx = (
        _find_restart_runs(words, p.restart_min_run, p.restart_window_s)
        if p.collapse_restarts
        else set()
    )

    kept: list[dict] = []
    removed: list[dict] = []

    for i, w in enumerate(words):
        reason: RemovalReason | None = None
        norm = _normalize(w["word"])

        if not norm:  # bare punctuation or empty
            reason = "filler"
        elif p.remove_fillers and norm in vocab:
            # Upgrade reason when filler happens during dead air — still "filler"
            reason = "filler"
        elif i in restart_idx:
            reason = "restart"
        elif i in dead_air_idx and norm in vocab:
            # redundant with the filler branch, kept for clarity
            reason = "dead_air"

        if reason:
            counts[reason] += 1
            removed.append({**w, "_reason": reason})
        else:
            kept.append(w)

    return ScrubResult(
        kept=kept,
        removed=removed,
        counts=counts,
        original_count=n0,
        kept_count=len(kept),
    )
