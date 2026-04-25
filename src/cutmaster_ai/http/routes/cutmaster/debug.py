"""Debug endpoints — read-only inspection of build artefacts.

Currently surfaces the Director prompt dumped per build. The prompt is
already written by ``_helpers._dump_director_prompt`` whenever a build
runs; this endpoint just serves it back so editors can review what the
model actually saw without shelling into the run-state directory.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ....cutmaster.core import state

router = APIRouter()


@router.get(
    "/debug/prompt/{run_id}",
    response_class=PlainTextResponse,
    responses={404: {"description": "No prompt cached for this run"}},
)
async def director_prompt(run_id: str) -> PlainTextResponse:
    """Return the Director prompt that was sent to Gemini for this run.

    Source: ``state.RUN_ROOT / "{run_id}.director_prompt.txt"`` — written
    by every successful Build. Returns 404 when the file is missing
    (no build has run yet, or the dump path was cleaned up).
    """
    path = state.RUN_ROOT / f"{run_id}.director_prompt.txt"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no Director prompt cached for run {run_id} — run a build first",
        )
    return PlainTextResponse(path.read_text(encoding="utf-8"))
