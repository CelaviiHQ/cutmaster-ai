"""Group detection for Rough cut mode (v2-11).

Rough cut's contract: *"these are candidate takes including A/B alternates —
pick winners per group, then sequence them."* The detector clusters adjacent
timeline items into groups so the Director can pick one winner per group.

Signal priority (first match wins):
  1. Clip color — adjacent items sharing a non-empty color are one group.
  2. Flag — adjacent items sharing any flag color are one group.
  3. Transcript similarity — adjacent items whose non-filler word sets have
     Jaccard similarity ≥ threshold (default 0.75).
  4. Singleton fallback — each item is its own group.

The first three produce semantically-grouped clusters; the fourth means rough
cut degrades to Curated's contract (every take appears once). The Review
screen surfaces this so editors understand the fallback.

Pure module — no Resolve imports. The Resolve-facing adapter
``read_items_with_grouping_signals`` lives at the bottom and only runs when
we're already inside the build-plan route.
"""

from __future__ import annotations

from typing import TypedDict

from .assembled import ItemSummary

# The scrubber treats these word payloads as filler; we strip them before
# computing Jaccard similarity so A/B retakes don't score low purely because
# one has more "um"s than the other.
_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "um",
        "uh",
        "uhh",
        "umm",
        "er",
        "ah",
        "hmm",
        "like",
        "you",
        "know",
        "so",
        "and",
        "the",
        "a",
        "to",
        "of",
    }
)

DEFAULT_SIMILARITY_THRESHOLD: float = 0.75


class GroupedItem(TypedDict, total=False):
    """An item summary plus the raw grouping signals from Resolve."""

    item_index: int
    source_name: str
    start_s: float
    end_s: float
    clip_color: str  # "" when unset
    flags: list[str]  # [] when unset


class Group(TypedDict):
    """A cluster of adjacent ``item_index`` values that the Director treats
    as alternates for the same moment. Exactly one winner is expected per
    group; the validator rejects plans that drop an entire group.
    """

    group_id: int
    item_indexes: list[int]
    signal: str  # "color" | "flag" | "similarity" | "singleton"


# ---------------------------------------------------------------------------
# Pure helpers — testable without Resolve
# ---------------------------------------------------------------------------


def _normalise_tokens(transcript: list[dict]) -> set[str]:
    """Lowercase + strip filler + return a set. Empty strings are dropped."""
    out: set[str] = set()
    for word in transcript:
        raw = str(word.get("word", "")).strip().lower()
        if not raw or raw in _FILLER_WORDS:
            continue
        out.add(raw)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_groups_by_color(items: list[GroupedItem]) -> list[Group] | None:
    """Cluster adjacent items that share a non-empty clip color.

    Returns ``None`` when no item has a color set (signal absent). Returns a
    group list when at least one item is colored — items without a color
    become singleton groups in that case, so the caller gets a well-formed
    partition regardless.
    """
    if not any((it.get("clip_color") or "") for it in items):
        return None
    return _cluster_by_key(items, key_fn=lambda it: it.get("clip_color") or "", signal="color")


def detect_groups_by_flag(items: list[GroupedItem]) -> list[Group] | None:
    """Cluster adjacent items that share at least one flag color.

    Returns ``None`` when no item has any flags. When at least one does,
    items without flags still become singleton groups so downstream code
    never sees a partial partition.
    """
    if not any(it.get("flags") for it in items):
        return None

    def flag_key(it: GroupedItem) -> str:
        flags = it.get("flags") or []
        return flags[0] if flags else ""

    return _cluster_by_key(items, key_fn=flag_key, signal="flag")


def _cluster_by_key(
    items: list[GroupedItem],
    *,
    key_fn,
    signal: str,
) -> list[Group]:
    groups: list[Group] = []
    current_key: str | None = None
    current: list[int] = []
    for it in items:
        key = key_fn(it)
        if key and key == current_key:
            current.append(it["item_index"])
        else:
            if current:
                groups.append(
                    {
                        "group_id": len(groups),
                        "item_indexes": current,
                        "signal": signal if current_key else "singleton",
                    }
                )
            current = [it["item_index"]]
            current_key = key if key else None
    if current:
        groups.append(
            {
                "group_id": len(groups),
                "item_indexes": current,
                "signal": signal if current_key else "singleton",
            }
        )
    return groups


def detect_groups_by_similarity(
    items: list[GroupedItem],
    per_item_transcripts: list[list[dict]],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[Group]:
    """Cluster adjacent items whose non-filler word sets are similar.

    The pairwise Jaccard score between consecutive items drives the merge
    decision: if score ≥ threshold, the items join the current cluster;
    otherwise a new cluster opens. Scores below threshold produce singleton
    groups.
    """
    if len(items) != len(per_item_transcripts):
        raise ValueError(
            f"items ({len(items)}) / per_item_transcripts "
            f"({len(per_item_transcripts)}) length mismatch"
        )
    if not items:
        return []

    tokens = [_normalise_tokens(t) for t in per_item_transcripts]
    groups: list[Group] = []
    current: list[int] = [items[0]["item_index"]]
    current_signal = "singleton"
    for idx in range(1, len(items)):
        score = _jaccard(tokens[idx - 1], tokens[idx])
        if score >= threshold:
            current.append(items[idx]["item_index"])
            current_signal = "similarity"
        else:
            groups.append(
                {
                    "group_id": len(groups),
                    "item_indexes": current,
                    "signal": current_signal,
                }
            )
            current = [items[idx]["item_index"]]
            current_signal = "singleton"
    groups.append(
        {
            "group_id": len(groups),
            "item_indexes": current,
            "signal": current_signal,
        }
    )
    return groups


def detect_groups(
    items: list[GroupedItem],
    per_item_transcripts: list[list[dict]],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[Group]:
    """Dispatcher — signal priority: color → flag → similarity → singleton.

    Every path returns a complete partition of the input items. The
    ``signal`` field on each group tells the caller which path fired,
    useful for the Review-screen banner that explains grouping to editors.
    """
    if not items:
        return []

    by_color = detect_groups_by_color(items)
    if by_color is not None:
        return by_color

    by_flag = detect_groups_by_flag(items)
    if by_flag is not None:
        return by_flag

    return detect_groups_by_similarity(items, per_item_transcripts, threshold=similarity_threshold)


def all_singletons(groups: list[Group]) -> bool:
    """True when every group contains exactly one item.

    When this holds, Rough cut's invariant (every group appears) is
    equivalent to Curated's (every take appears). The UI flags this with a
    banner so editors know they got Curated behaviour from a Rough cut run.
    """
    return all(len(g["item_indexes"]) == 1 for g in groups)


# ---------------------------------------------------------------------------
# Resolve adapter — only runs inside a live /build-plan call
# ---------------------------------------------------------------------------


def read_items_with_grouping_signals(tl, track_index: int = 1) -> list[GroupedItem]:
    """Read V{track_index} items with their clip color + flags.

    Mirrors ``assembled.read_items_on_track`` — same geometry, plus the two
    grouping signals. We keep this adapter here (not in ``assembled``) so
    callers that don't need grouping don't pay the extra API round-trips.
    """
    from ..media.frame_math import _timeline_fps, _timeline_start_frame

    fps = _timeline_fps(tl)
    tl_start_frame = _timeline_start_frame(tl)
    items = tl.GetItemListInTrack("video", track_index) or []

    out: list[GroupedItem] = []
    for idx, item in enumerate(items):
        mp_item = item.GetMediaPoolItem()
        name = mp_item.GetName() if mp_item is not None else f"item_{idx}"
        start_frame = item.GetStart() - tl_start_frame
        end_frame = item.GetEnd() - tl_start_frame

        try:
            color = str(item.GetClipColor() or "")
        except Exception:
            color = ""
        try:
            flags = list(item.GetFlagList() or [])
        except Exception:
            flags = []

        out.append(
            GroupedItem(
                item_index=idx,
                source_name=str(name),
                start_s=start_frame / fps,
                end_s=end_frame / fps,
                clip_color=color,
                flags=flags,
            )
        )
    return out


def to_item_summary(grouped: list[GroupedItem]) -> list[ItemSummary]:
    """Strip grouping signals — downstream code wants the v2-2 shape."""
    return [
        ItemSummary(
            item_index=it["item_index"],
            source_name=it["source_name"],
            start_s=it["start_s"],
            end_s=it["end_s"],
        )
        for it in grouped
    ]
