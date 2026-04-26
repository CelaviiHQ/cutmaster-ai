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


def _dump_director_prompt(
    run_id: str,
    prompt_text: str,
    *,
    suffix: str | None = None,
    pass_index: int | None = None,
) -> str:
    """Write the Director prompt to disk + log the path for debugging.

    Filename layout — three forms, all serve the same content:

    - ``<run_id>.director_prompt.txt`` — first pass, no kwargs.
    - ``<run_id>.director_prompt.<N>.txt`` — pass N (0 = first, 1+ rework)
      when ``pass_index`` is set. The numbered form is what the panel's
      stepped lift-ladder reads via ``GET /debug/prompt/<id>?pass=N``.
    - ``<run_id>.director_prompt.<suffix>.txt`` — legacy named form
      (``suffix="rework"`` is the only one in use today). Kept for
      back-compat with already-persisted runs the panel may still link to.

    The numbered form is also written to the legacy ``.rework.txt``
    sidecar when ``pass_index == 1`` so previously-persisted runs keep
    resolving without a panel update. Overwritten on each call.
    """
    base = f"{run_id}.director_prompt"
    suffix_token: str | None
    if pass_index is not None:
        suffix_token = str(pass_index)
    elif suffix:
        suffix_token = suffix
    else:
        suffix_token = None

    name = base + (f".{suffix_token}.txt" if suffix_token else ".txt")
    path = state.RUN_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt_text, encoding="utf-8")
    log.info(
        "Director prompt%s (%d chars) written to %s",
        f" [{suffix_token}]" if suffix_token else "",
        len(prompt_text),
        path,
    )

    # Numbered form aliases the legacy ``.rework.txt`` for pass 1 so the
    # panel's earlier convention keeps working without a redeploy.
    if pass_index == 1:
        legacy = state.RUN_ROOT / f"{base}.rework.txt"
        legacy.write_text(prompt_text, encoding="utf-8")

    return str(path)
