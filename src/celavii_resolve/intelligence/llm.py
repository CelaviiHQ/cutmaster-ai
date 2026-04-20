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
import threading
import time
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from ..config import get_gemini_client

log = logging.getLogger("celavii-resolve.intelligence.llm")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# v4 Phase 4.5 — shared vision-call concurrency gate
# ---------------------------------------------------------------------------
#
# Long timelines can trigger dozens of Gemini vision calls (shot_tagger +
# boundary_validator + validator_loop retries). Without a budget, a single
# analyze can saturate the quota and bleed into whatever the user does
# next. Semaphore is shared across both vision agents so the total
# in-flight ceiling is bounded regardless of which layer fires.
#
# Default 3 — plenty for the panel's one-editor-at-a-time usage while
# staying polite to free-tier quotas. Override via
# ``CELAVII_VISION_CONCURRENCY`` (positive int).


def _vision_concurrency_limit() -> int:
    raw = os.environ.get("CELAVII_VISION_CONCURRENCY", "").strip()
    if not raw:
        return 3
    try:
        v = int(raw)
    except ValueError:
        log.warning("CELAVII_VISION_CONCURRENCY not an int (%s), using 3", raw)
        return 3
    return max(1, v)


_VISION_SEM = threading.Semaphore(_vision_concurrency_limit())

# (data, mime_type) — the minimal shape callers need to supply. Keeping the
# public surface as plain bytes + str avoids coupling callers to google-genai
# types; the dispatcher wraps them into ``types.Part.from_bytes`` internally.
ImagePart = tuple[bytes, str]


# ---------------------------------------------------------------------------
# Model dispatch — env var overrides per agent
# ---------------------------------------------------------------------------


DEFAULTS: dict[str, str] = {
    # gemini-3-flash-preview was observed running 3:40 per call in
    # preview-channel queues — moving Director + Marker to the lite-preview
    # (same model the other agents use) brings calls back to 2–10 s.
    "director": "gemini-3.1-flash-lite-preview",
    "marker": "gemini-3.1-flash-lite-preview",
    "autodetect": "gemini-3.1-flash-lite-preview",
    "theme": "gemini-3.1-flash-lite-preview",
    "reconcile": "gemini-3.1-flash-lite-preview",
    "stt": "gemini-3.1-flash-lite-preview",  # stt.py has its own override for legacy
    # v4 Layer C / Layer A vision agents. Same lite default; per-agent
    # overrides via CELAVII_SHOT_TAGGER_MODEL / CELAVII_BOUNDARY_VALIDATOR_MODEL.
    "shot_tagger": "gemini-3.1-flash-lite-preview",
    "boundary_validator": "gemini-3.1-flash-lite-preview",
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
    accept_best_effort: bool = False,
    images: Sequence[ImagePart] | None = None,
) -> T:
    """Call Gemini with ``response_schema`` enforcement + optional validator.

    If ``validate`` returns a non-empty list of errors, the call is retried
    with the errors fed back into the prompt. After ``max_retries``:
      - ``accept_best_effort=True``: return the attempt with the fewest
        validation errors (caller surfaces the remaining errors as warnings
        rather than failing the request).
      - ``accept_best_effort=False`` (default): raise :class:`AgentError`.

    Args:
        agent: Logical agent name — used to pick the model via env override.
        prompt: System+user prompt concatenated (Gemini has no system slot).
        response_schema: Pydantic model Gemini must match.
        temperature: Sampling temperature (default 0.3 — agents should be
            mostly deterministic).
        max_retries: Max attempts including the first call.
        validate: Optional callback returning a list of error strings; empty
            list means accept.
        accept_best_effort: When all retries fail, return the best attempt
            instead of raising. The returned object's ``_validation_errors``
            attribute (set via object.__setattr__) carries the final error
            list so the caller can surface warnings. Use only for agents
            whose output is a *suggestion* reviewed by the editor.
        images: Optional list of ``(bytes, mime_type)`` tuples for vision
            agents. Appended to the request contents after the prompt so
            the model sees ``[prompt, image_0, image_1, ...]``. v4 Layer C
            (shot tagging) and Layer A (boundary validator) use this to
            inject sampled frames. All vision calls go through here so
            cost telemetry + retry logic stay in one place.
    """
    client = get_gemini_client()
    if client is None:
        raise ValueError("GEMINI_API_KEY not set. Add it to .env or the environment.")

    from google.genai import types  # deferred — optional dependency

    model = model_for(agent)
    log.info("agent=%s model=%s", agent, model)

    image_parts: list = []
    if images:
        for data, mime in images:
            if not data:
                raise ValueError("image data must be non-empty bytes")
            image_parts.append(types.Part.from_bytes(data=data, mime_type=mime))

    best_parsed: T | None = None
    best_errors: list[str] = []
    last_errors: list[str] = []

    for attempt in range(1, max_retries + 1):
        retry_prompt = prompt
        if last_errors:
            retry_prompt += (
                "\n\nYour previous response failed validation with these errors. "
                "Fix them and return a corrected response:\n"
                + "\n".join(f"- {e}" for e in last_errors)
            )

        contents: list = [retry_prompt, *image_parts] if image_parts else [retry_prompt]

        # Vision calls go through the shared semaphore so shot_tagger +
        # boundary_validator + any future vision agent never exceed the
        # configured concurrent-call budget. Non-vision calls skip the
        # gate entirely — they're already cheap by comparison.
        call_start = time.monotonic()
        if image_parts:
            with _VISION_SEM:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        temperature=temperature,
                    ),
                )
        else:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=temperature,
                ),
            )
        elapsed_ms = int((time.monotonic() - call_start) * 1000)

        # v4 Phase 4.5.5 — per-call cost telemetry. Logs only; no UI
        # surface. Gemini exposes usage under ``usage_metadata`` with
        # ``prompt_token_count`` / ``candidates_token_count``. Guarded
        # because older SDK versions may not populate it.
        tokens_in = _safe_token_count(response, "prompt_token_count")
        tokens_out = _safe_token_count(response, "candidates_token_count")
        log.info(
            "agent=%s attempt=%d elapsed_ms=%d tokens_in=%s tokens_out=%s",
            agent,
            attempt,
            elapsed_ms,
            tokens_in,
            tokens_out,
            extra={
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "elapsed_ms": elapsed_ms,
                "cache_hit": False,  # in-process call, not a cache replay
            },
        )

        parsed = _parse_response(response, response_schema)

        if validate is None:
            return parsed

        errors = validate(parsed)
        if not errors:
            return parsed

        log.warning("agent=%s attempt=%d validation_errors=%d", agent, attempt, len(errors))
        if best_parsed is None or len(errors) < len(best_errors):
            best_parsed = parsed
            best_errors = errors
        last_errors = errors

    if accept_best_effort and best_parsed is not None:
        log.warning(
            "agent=%s using best-effort after %d retries (%d remaining errors)",
            agent,
            max_retries,
            len(best_errors),
        )
        try:
            object.__setattr__(best_parsed, "_validation_errors", best_errors)
        except Exception:
            pass  # Pydantic v2 allows __setattr__ on extra attrs; swallow otherwise
        return best_parsed

    raise AgentError(
        f"{agent} agent failed after {max_retries} retries. Last errors: {last_errors}"
    )


def _safe_token_count(response, attr: str) -> int | None:
    """Best-effort extract of a token count from Gemini's ``usage_metadata``.

    Older google-genai SDK versions (and some response shapes) don't
    populate ``usage_metadata``; those calls log ``tokens_in=None`` /
    ``tokens_out=None`` rather than crashing the telemetry path.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    value = getattr(usage, attr, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
