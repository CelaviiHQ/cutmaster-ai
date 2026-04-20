"""Stable Pydantic request/response models for third-party plugins.

This module is the **public, versioned** import surface for plugins that
need to share shapes with OSS endpoints (e.g. a ``panel_routes`` plugin
that decorates or augments a CutMaster flow). Plugins must import from
here rather than reaching into ``celavii_resolve.http.routes.*._models``,
which is private and may be restructured without notice.

Breaking changes to any name exported here require a major-version bump
per :file:`SURFACE.md`. Adding new names is non-breaking.
"""

from __future__ import annotations

from .routes.cutmaster._models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzeThemesRequest,
    BuildPlanRequest,
    CloneRunRequest,
    DeleteAllCutsRequest,
    DeleteCutRequest,
    DeleteRunRequest,
    DetectPresetRequest,
    ExecuteRequest,
    ProjectInfoResponse,
    RunListResponse,
    RunSummary,
    SourceAspectResponse,
    SpeakerRosterEntry,
    SpeakerRosterResponse,
    TimelineInfo,
    UserSettings,
)

__all__ = [
    "AnalyzeRequest",
    "AnalyzeResponse",
    "AnalyzeThemesRequest",
    "BuildPlanRequest",
    "CloneRunRequest",
    "DeleteAllCutsRequest",
    "DeleteCutRequest",
    "DeleteRunRequest",
    "DetectPresetRequest",
    "ExecuteRequest",
    "ProjectInfoResponse",
    "RunListResponse",
    "RunSummary",
    "SourceAspectResponse",
    "SpeakerRosterEntry",
    "SpeakerRosterResponse",
    "TimelineInfo",
    "UserSettings",
]
