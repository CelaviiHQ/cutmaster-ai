"""CutMaster HTTP routes — aggregates the feature sub-routers under /cutmaster.

Each sub-module owns one slice of the panel's backend:
    analyze.py   — POST /analyze, GET /events/{id}, GET /state/{id}
    presets.py   — preset/format/STT catalog + detect-preset + analyze-themes
    info.py      — read-only source/project/speakers/director-prompt endpoints
    build.py     — POST /build-plan (Director + Marker + source-frame map)
    execute.py   — POST /execute (materialise in Resolve) + POST /delete-cut

Module-level aliases ``auto_detect_mod`` and ``themes_mod`` are re-exported
so existing tests that monkey-patch the old flat module keep working.
"""

from __future__ import annotations

from fastapi import APIRouter

# Re-exports for test-suite monkeypatching (kept intentionally).
from ....cutmaster.analysis import auto_detect as auto_detect_mod  # noqa: F401
from ....cutmaster.analysis import themes as themes_mod  # noqa: F401
from . import analyze, build, execute, info, presets

router = APIRouter(prefix="/cutmaster", tags=["cutmaster"])
router.include_router(analyze.router)
router.include_router(presets.router)
router.include_router(info.router)
router.include_router(build.router)
router.include_router(execute.router)

__all__ = ["router", "auto_detect_mod", "themes_mod"]
