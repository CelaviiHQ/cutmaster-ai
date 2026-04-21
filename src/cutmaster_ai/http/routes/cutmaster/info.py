"""Read-only info endpoints: source aspect, project/timeline list, speakers, director prompt dump."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ....cutmaster.core import state
from ._models import (
    ProjectInfoResponse,
    SourceAspectResponse,
    SpeakerRosterEntry,
    SpeakerRosterResponse,
    TimelineInfo,
)

router = APIRouter()


@router.get("/source-aspect/{run_id}", response_model=SourceAspectResponse)
async def source_aspect(run_id: str) -> SourceAspectResponse:
    """Read the source timeline's pixel dimensions and recommend a Format.

    Used by the Configure screen to preselect the Format picker and to
    suppress the aspect-mismatch reframe when source and target already
    match (e.g. a 9:16 phone vlog into a Short).
    """
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    from ....cutmaster.core.pipeline import _find_timeline_by_name
    from ....cutmaster.media.formats import recommend_format
    from ....resolve import _boilerplate  # lazy — avoids import-time Resolve dependency

    try:
        _, project, _ = _boilerplate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

    tl = _find_timeline_by_name(project, run["timeline_name"])
    if tl is None:
        raise HTTPException(
            status_code=400,
            detail=f"timeline '{run['timeline_name']}' not found",
        )

    def _read_int(obj, key: str) -> int:
        try:
            v = obj.GetSetting(key)
        except Exception:
            return 0
        try:
            return int(v) if v else 0
        except (TypeError, ValueError):
            return 0

    w = _read_int(tl, "timelineResolutionWidth") or _read_int(project, "timelineResolutionWidth")
    h = _read_int(tl, "timelineResolutionHeight") or _read_int(project, "timelineResolutionHeight")
    if w <= 0 or h <= 0:
        raise HTTPException(
            status_code=400,
            detail="could not read timeline resolution — check Project Settings",
        )

    rec = recommend_format(w, h)
    return SourceAspectResponse(
        width=w,
        height=h,
        aspect=w / h,
        recommended_format=rec.key,
    )


@router.get("/director-prompt/{run_id}")
async def director_prompt(run_id: str) -> dict:
    """Return the last-rendered Director prompt for this run (debug helper)."""
    path = state.RUN_ROOT / f"{run_id}.director_prompt.txt"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no prompt saved for run {run_id} — run Build plan first",
        )
    return {
        "run_id": run_id,
        "path": str(path),
        "prompt": path.read_text(encoding="utf-8"),
    }


@router.get("/project-info", response_model=ProjectInfoResponse)
async def project_info() -> ProjectInfoResponse:
    """Return the open project's name + every timeline in it.

    Drives the Preset screen's timeline picker: instead of typing a name
    free-hand, the user sees every timeline in the current project and
    which one is active in Resolve. Returns a 503 when Resolve isn't
    reachable so the UI can fall back to the legacy text input.
    """
    from ....resolve import _boilerplate  # lazy

    try:
        _, project, _ = _boilerplate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Resolve unreachable: {exc}")

    try:
        project_name = project.GetName() or "(untitled project)"
    except Exception:
        project_name = "(unknown project)"

    current = project.GetCurrentTimeline()
    current_name = current.GetName() if current else None

    timelines: list[TimelineInfo] = []
    try:
        count = int(project.GetTimelineCount() or 0)
    except Exception:
        count = 0

    from ....cutmaster.media.source_resolver import count_effective_cuts

    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl is None:
            continue
        name = tl.GetName() or f"Timeline {i}"
        try:
            item_count = count_effective_cuts(project, tl)
        except Exception:
            item_count = 0
        timelines.append(
            TimelineInfo(
                name=name,
                is_current=(name == current_name),
                item_count=item_count,
            )
        )

    return ProjectInfoResponse(project_name=project_name, timelines=timelines)


@router.get("/speakers/{run_id}", response_model=SpeakerRosterResponse)
async def speakers(run_id: str) -> SpeakerRosterResponse:
    """Return the speaker roster detected in this run's scrubbed transcript.

    Drives the Configure screen's speaker-rename form (v2-5): entries are
    in first-appearance order, annotated with word-count so the editor can
    guess which one is host vs guest. Falls back to the raw transcript if
    scrubbing hasn't happened yet — single-speaker runs return an empty
    roster the UI can hide.
    """
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    from ....cutmaster.stt.speakers import detect_speakers, speaker_stats

    transcript = run.get("scrubbed") or run.get("transcript") or []
    ids = detect_speakers(transcript)
    counts = speaker_stats(transcript)
    return SpeakerRosterResponse(
        speakers=[SpeakerRosterEntry(speaker_id=sid, word_count=counts.get(sid, 0)) for sid in ids],
    )
