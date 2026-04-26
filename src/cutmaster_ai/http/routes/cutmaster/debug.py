"""Debug endpoints — read-only inspection of build artefacts.

Currently surfaces the Director prompt dumped per build. The prompt is
already written by ``_helpers._dump_director_prompt`` whenever a build
runs; this endpoint just serves it back so editors can review what the
model actually saw without shelling into the run-state directory.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ....cutmaster.core import state

log = logging.getLogger("cutmaster-ai.http.cutmaster")

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

    Source: ``state.RUN_ROOT / "{run_id}.director_prompt[.<N>].txt"`` —
    written by every successful Build (one file per iteration once the
    iterative critic loop runs). Returns 404 when the file is missing
    (no build has run yet, or the dump path was cleaned up).

    Query param ``pass`` (FastAPI alias of ``pass_``):

    - omitted → first-pass dump (``.director_prompt.txt``).
    - ``"1"`` .. ``"5"`` → that iteration's numbered dump
      (``.director_prompt.<N>.txt``); used by the panel's stepped lift
      ladder. Falls back to ``.rework.txt`` when ``pass=1`` and no
      numbered file is on disk so older runs keep resolving.
    - ``"rework"`` → alias for ``pass=1``. Logged as a deprecation.
    - any other value → 400.
    """
    if pass_ is None:
        return _serve_first_pass(run_id)

    if pass_ == "rework":
        log.info(
            "debug.prompt.deprecated_pass_alias run_id=%s pass=rework",
            run_id,
            extra={
                "event": "debug.prompt.deprecated_pass_alias",
                "run_id": run_id,
            },
        )
        return _serve_numbered_pass(run_id, 1)

    try:
        idx = int(pass_)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown pass {pass_!r} — supported: numeric pass index "
                "(e.g. '1') or legacy 'rework' alias"
            ),
        ) from None
    if idx < 1:
        raise HTTPException(
            status_code=400,
            detail=f"pass index must be ≥ 1 (got {idx})",
        )
    return _serve_numbered_pass(run_id, idx)


def _serve_first_pass(run_id: str) -> PlainTextResponse:
    path = state.RUN_ROOT / f"{run_id}.director_prompt.txt"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no Director prompt cached for run {run_id} — run a build first",
        )
    return PlainTextResponse(path.read_text(encoding="utf-8"))


def _serve_numbered_pass(run_id: str, idx: int) -> PlainTextResponse:
    primary = state.RUN_ROOT / f"{run_id}.director_prompt.{idx}.txt"
    if primary.exists():
        return PlainTextResponse(primary.read_text(encoding="utf-8"))

    # Back-compat: previously-persisted runs only emitted .rework.txt for
    # the single-rework path (today's "pass 1" in the iterative loop).
    if idx == 1:
        legacy = state.RUN_ROOT / f"{run_id}.director_prompt.rework.txt"
        if legacy.exists():
            return PlainTextResponse(legacy.read_text(encoding="utf-8"))

    raise HTTPException(
        status_code=404,
        detail=(
            f"no Director prompt cached for run {run_id} pass={idx} — "
            "either the iteration didn't fire or the dump was cleaned up"
        ),
    )
