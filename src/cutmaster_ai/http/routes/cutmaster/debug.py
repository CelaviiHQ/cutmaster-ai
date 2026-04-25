"""Debug endpoints — read-only inspection of build artefacts.

Currently surfaces the Director prompt dumped per build. The prompt is
already written by ``_helpers._dump_director_prompt`` whenever a build
runs; this endpoint just serves it back so editors can review what the
model actually saw without shelling into the run-state directory.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ....cutmaster.core import state

router = APIRouter()


@router.get(
    "/debug/prompt/{run_id}",
    response_class=PlainTextResponse,
    responses={404: {"description": "No prompt cached for this run"}},
)
async def director_prompt(
    run_id: str,
    pass_: str | None = Query(default=None, alias="pass"),
) -> PlainTextResponse:
    """Return the Director prompt that was sent to Gemini for this run.

    Source: ``state.RUN_ROOT / "{run_id}.director_prompt.txt"`` — written
    by every successful Build. Returns 404 when the file is missing
    (no build has run yet, or the dump path was cleaned up).

    Query param ``pass`` (FastAPI alias of ``pass_``) selects which dump
    to return — ``"rework"`` serves
    ``<run_id>.director_prompt.rework.txt`` (story-critic Phase 6 rework
    pass; only present when the rework loop fired). Default returns the
    first-pass dump.
    """
    suffix = ""
    if pass_:
        if pass_ != "rework":
            raise HTTPException(
                status_code=400,
                detail=f"unknown pass {pass_!r} — supported: 'rework'",
            )
        suffix = ".rework"
    path = state.RUN_ROOT / f"{run_id}.director_prompt{suffix}.txt"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no Director prompt cached for run {run_id}{suffix or ''} — run a build first",
        )
    return PlainTextResponse(path.read_text(encoding="utf-8"))
