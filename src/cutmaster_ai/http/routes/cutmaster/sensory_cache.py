"""v4 Phase 4.5.3 — sensory-layer cache management.

The three sensory layers land their caches under
``~/.cutmaster/cutmaster/{shot-tags,boundary-frames,audio-cues}/v1/``.
Source-file-keyed directories mean caches cycle naturally when media
is replaced, but a manual "clear everything" path is still useful for:

- debugging schema-drift false positives,
- reclaiming disk after a long editing sprint,
- validating cold-cache cost in CI.

Per-layer selectivity so a user who only wants to retag shots (e.g.
after a prompt-engineering pass) doesn't nuke their boundary-frame
cache too.

Gating: consistent with the rest of the panel API — trusts the default
127.0.0.1 bind. If network exposure becomes a real concern, add
middleware covering the whole destructive surface (``/delete-run``,
``/delete-cut``, ``/delete-all-cuts``, this endpoint) in one pass. See
proposal §"Decisions" #4.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

log = logging.getLogger("cutmaster-ai.http.cutmaster")


# Must stay in sync with the ``CACHE_ROOT`` constants in each module.
# Imported lazily inside the handler to avoid dragging ffmpeg / pydantic
# schemas into the route module at import time.
_LAYER_KEYS = ("c", "a", "audio")


class SensoryCacheClearRequest(BaseModel):
    """Request body — which layers to clear.

    ``layers`` accepts either the literal string ``"all"`` or an explicit
    subset of ``["c", "a", "audio"]``. Unknown entries raise 422 via
    Pydantic.
    """

    layers: Literal["all"] | list[Literal["c", "a", "audio"]] = Field(
        default="all",
        description=(
            "'all' (default) wipes shot-tags, boundary-frames, and "
            "audio-cues caches. Pass a subset (e.g. ['c']) to clear "
            "only specific layers."
        ),
    )


class LayerClearResult(BaseModel):
    """One per layer the request targeted."""

    layer: Literal["c", "a", "audio"]
    path: str
    cleared: bool
    existed_before: bool
    bytes_freed: int


class SensoryCacheClearResponse(BaseModel):
    results: list[LayerClearResult]


def _cache_roots() -> dict[str, Path]:
    """Resolve the three cache roots by reading each module's constant.

    Kept as a function so the import happens at call time — the vision /
    audio modules import Pydantic models that define schemas, and we
    don't want them in-memory on every panel process just because the
    /cutmaster router is mounted.
    """
    from ....cutmaster.analysis.audio_cues import CACHE_ROOT as AUDIO_ROOT
    from ....cutmaster.analysis.boundary_validator import CACHE_ROOT as BOUNDARY_ROOT
    from ....cutmaster.analysis.shot_tagger import CACHE_ROOT as SHOT_TAG_ROOT

    return {
        "c": SHOT_TAG_ROOT,
        "a": BOUNDARY_ROOT,
        "audio": AUDIO_ROOT,
    }


def _directory_size_bytes(path: Path) -> int:
    """Best-effort recursive size. Missing dirs / unreadable files → 0 contribution."""
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


router = APIRouter()


@router.post("/sensory-cache/clear", response_model=SensoryCacheClearResponse)
async def clear_sensory_cache(body: SensoryCacheClearRequest) -> SensoryCacheClearResponse:
    """Clear one or more sensory-layer caches.

    Returns per-layer outcomes so the panel can surface "freed N MB"
    feedback. Best-effort — failed deletions surface as ``cleared=false``
    rather than erroring the whole request.
    """
    targets = _LAYER_KEYS if body.layers == "all" else tuple(body.layers)
    roots = _cache_roots()

    results: list[LayerClearResult] = []
    for layer in targets:
        root = roots[layer]
        existed = root.exists()
        bytes_before = _directory_size_bytes(root)
        cleared = False
        if existed:
            try:
                shutil.rmtree(root)
                cleared = True
            except OSError as exc:
                log.warning("sensory-cache: failed to clear %s: %s", root, exc)
        results.append(
            LayerClearResult(
                layer=layer,
                path=str(root),
                cleared=cleared,
                existed_before=existed,
                bytes_freed=bytes_before if cleared else 0,
            )
        )

    log.info(
        "sensory-cache clear",
        extra={
            "path": "/cutmaster/sensory-cache/clear",
            "method": "POST",
        },
    )
    return SensoryCacheClearResponse(results=results)
