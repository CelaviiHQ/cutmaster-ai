"""Shared helpers across cutmaster route modules."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ....cutmaster.core import state

log = logging.getLogger("cutmaster-ai.http.cutmaster")


def _require_scrubbed(run_id: str) -> tuple[dict, list[dict]]:
    """Load a run and return ``(state_dict, scrubbed_words)`` or HTTP 400."""
    run = state.load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    scrubbed = run.get("scrubbed") or []
    if not scrubbed:
        raise HTTPException(
            status_code=400,
            detail=f"run {run_id} has no scrubbed transcript — analyze first",
        )
    return run, scrubbed


def _dump_director_prompt(run_id: str, prompt_text: str) -> str:
    """Write the Director prompt to disk + log the path for debugging.

    Lands at ``~/.cutmaster/cutmaster/<run_id>.director_prompt.txt`` — one file
    per run, overwritten on each Build. Gives you a ground-truth look at the
    exact text Gemini will see (including every optional block). Returns the
    path as a string so the caller can surface it in the response if it wants.
    """
    path = state.RUN_ROOT / f"{run_id}.director_prompt.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt_text, encoding="utf-8")
    log.info(
        "Director prompt (%d chars) written to %s",
        len(prompt_text),
        path,
    )
    return str(path)
