"""Single chokepoint for all LLM calls across intelligence tools and AI products.

v1 targets Gemini via google-genai + ``response_schema``. Every agent goes
through ``call_structured`` so:
  - model selection is env-var tunable per agent
  - retry + validation logic lives in one place
  - swapping in a second provider (e.g. Anthropic) is a dispatcher change,
    not a rewrite of every agent.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from ..config import get_gemini_client

log = logging.getLogger("celavii-resolve.intelligence.llm")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Model dispatch — env var overrides per agent
# ---------------------------------------------------------------------------


DEFAULTS: dict[str, str] = {
    "director": "gemini-3-flash-preview",
    "marker": "gemini-3-flash-preview",
    "autodetect": "gemini-3.1-flash-lite-preview",
    "theme": "gemini-3.1-flash-lite-preview",
    "reconcile": "gemini-3.1-flash-lite-preview",
    "stt": "gemini-3.1-flash-lite-preview",  # stt.py has its own override for legacy
}


def model_for(agent: str) -> str:
    """Resolve the model slug for a given agent.

    Checks ``CELAVII_<AGENT>_MODEL`` env var, then falls back to default.
    """
    env_key = f"CELAVII_{agent.upper()}_MODEL"
    return os.environ.get(env_key) or DEFAULTS[agent]


# ---------------------------------------------------------------------------
# Structured call with retry + validation
# ---------------------------------------------------------------------------


class AgentError(RuntimeError):
    """Raised when an agent's output is still invalid after all retries."""


def call_structured(
    agent: str,
    prompt: str,
    response_schema: type[T],
    *,
    temperature: float = 0.3,
    max_retries: int = 3,
    validate: Callable[[T], list[str]] | None = None,
) -> T:
    """Call Gemini with ``response_schema`` enforcement + optional validator.

    If ``validate`` returns a non-empty list of errors, the call is retried
    with the errors fed back into the prompt. After ``max_retries``, raises
    :class:`AgentError` with the final validation errors.

    Args:
        agent: Logical agent name — used to pick the model via env override.
        prompt: System+user prompt concatenated (Gemini has no system slot).
        response_schema: Pydantic model Gemini must match.
        temperature: Sampling temperature (default 0.3 — agents should be
            mostly deterministic).
        max_retries: Max attempts including the first call.
        validate: Optional callback returning a list of error strings; empty
            list means accept.
    """
    client = get_gemini_client()
    if client is None:
        raise ValueError("GEMINI_API_KEY not set. Add it to .env or the environment.")

    from google.genai import types  # deferred — optional dependency

    model = model_for(agent)
    log.info("agent=%s model=%s", agent, model)

    last_errors: list[str] = []
    for attempt in range(1, max_retries + 1):
        retry_prompt = prompt
        if last_errors:
            retry_prompt += (
                "\n\nYour previous response failed validation with these errors. "
                "Fix them and return a corrected response:\n"
                + "\n".join(f"- {e}" for e in last_errors)
            )

        response = client.models.generate_content(
            model=model,
            contents=[retry_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=temperature,
            ),
        )

        parsed = _parse_response(response, response_schema)

        if validate is not None:
            errors = validate(parsed)
            if errors:
                log.warning("agent=%s attempt=%d validation_errors=%d", agent, attempt, len(errors))
                last_errors = errors
                continue

        return parsed

    raise AgentError(
        f"{agent} agent failed after {max_retries} retries. Last errors: {last_errors}"
    )


def _parse_response(response, schema: type[T]) -> T:
    """Extract a Pydantic instance from the Gemini response, tolerating both
    the SDK's ``.parsed`` attribute and a raw JSON fallback.
    """
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"model returned non-JSON: {exc}") from exc

    # Tolerate bare-array responses by wrapping if schema has a single list
    # field (the way TranscriptResponse does).
    if isinstance(payload, list):
        list_fields = [
            name
            for name, f in schema.model_fields.items()
            if getattr(f.annotation, "__origin__", None) is list
        ]
        if len(list_fields) == 1:
            payload = {list_fields[0]: payload}

    try:
        return schema.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        raise AgentError(f"schema validation failed: {exc}") from exc
