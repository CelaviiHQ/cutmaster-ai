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

log = logging.getLogger("cutmaster-ai.intelligence.llm")

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
# ``CUTMASTER_VISION_CONCURRENCY`` (positive int).


def _vision_concurrency_limit() -> int:
    raw = os.environ.get("CUTMASTER_VISION_CONCURRENCY", "").strip()
    if not raw:
        return 3
    try:
        v = int(raw)
    except ValueError:
        log.warning("CUTMASTER_VISION_CONCURRENCY not an int (%s), using 3", raw)
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
    # overrides via CUTMASTER_SHOT_TAGGER_MODEL / CUTMASTER_BOUNDARY_VALIDATOR_MODEL.
    "shot_tagger": "gemini-3.1-flash-lite-preview",
    "boundary_validator": "gemini-3.1-flash-lite-preview",
    # Story-coherence critic (Implementation/optimizaiton/story-critic.md).
    # Defaults to the same lite-preview slug as the other agents because the
    # non-lite gemini-3.1-flash is not available on the v1beta endpoint.
    # Override with CUTMASTER_STORY_CRITIC_MODEL once a non-lite slug ships.
    "story_critic": "gemini-3.1-flash-lite-preview",
}


def model_for(agent: str) -> str:
    """Resolve the model slug for a given agent.

    Checks ``CUTMASTER_<AGENT>_MODEL`` env var, then falls back to default.
    """
    env_key = f"CUTMASTER_{agent.upper()}_MODEL"
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
    summarise_attempt: Callable[[T], str] | None = None,
) -> T:
    """Call Gemini with ``response_schema`` enforcement + optional validator.

    If ``validate`` returns a non-empty list of errors, the call is retried
    with the errors fed back into the prompt. After ``max_retries``:
      - ``accept_best_effort=True``: return the attempt with the fewest
        validation errors (caller surfaces the remaining errors as warnings
        rather than failing the request).
      - ``accept_best_effort=False`` (default): raise :class:`AgentError`.

    Retry feedback is cumulative: every prior attempt's errors (plus its
    optional summary) are rendered into a ``PREVIOUS ATTEMPTS`` block so
    the model can see what it already tried instead of getting only the
    most recent failure. Without this, multi-constraint validators
    (e.g. Director coverage + duration + boundaries) can random-walk —
    the model fixes constraint A on attempt 2, breaks constraint B,
    then re-introduces A's failure on attempt 3 because it has no memory
    of the trade-off it made.

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
        summarise_attempt: Optional callback that turns a parsed (but
            invalid) attempt into a one-line summary. Rendered above the
            attempt's error list in the cumulative feedback block so the
            model can distinguish "I tried 11 segments at 261s" from
            "I tried 10 segments at 165s" — different shapes, different
            failure modes. Receives the same parsed object the validator
            saw; should not raise (raises are caught and the summary is
            simply skipped).
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
    # Cumulative attempt history — every prior attempt's errors and
    # optional summary, rendered into the next retry's prompt so the
    # model sees the full trade-off space, not just the last failure.
    attempt_history: list[dict] = []
    # Sum token usage across every retry inside this call_structured
    # invocation. The iterative-critic-loop reads `_token_usage` off the
    # returned plan to feed its per-iteration token-budget check, so any
    # retry that fired must be billed even if its parse was discarded.
    total_tokens_in = 0
    total_tokens_out = 0

    for attempt in range(1, max_retries + 1):
        retry_prompt = prompt + _build_retry_block(attempt_history)

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
        if tokens_in is not None:
            total_tokens_in += tokens_in
        if tokens_out is not None:
            total_tokens_out += tokens_out
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
            _stash_token_usage(parsed, total_tokens_in, total_tokens_out)
            return parsed

        errors = validate(parsed)
        if not errors:
            _stash_token_usage(parsed, total_tokens_in, total_tokens_out)
            return parsed

        log.warning("agent=%s attempt=%d validation_errors=%d", agent, attempt, len(errors))
        if best_parsed is None or len(errors) < len(best_errors):
            best_parsed = parsed
            best_errors = errors

        entry: dict = {"attempt": attempt, "errors": errors}
        if summarise_attempt is not None:
            try:
                summary = summarise_attempt(parsed)
            except Exception as exc:  # noqa: BLE001 — never let a summary helper kill a retry
                log.warning(
                    "agent=%s summarise_attempt raised %s; rendering attempt without summary",
                    agent,
                    exc,
                )
            else:
                if summary:
                    entry["summary"] = summary
        attempt_history.append(entry)

    if accept_best_effort and best_parsed is not None:
        log.warning(
            "agent=%s using best-effort after %d retries (%d remaining errors)",
            agent,
            max_retries,
            len(best_errors),
        )
        try:
            object.__setattr__(best_parsed, "_validation_errors", best_errors)
            object.__setattr__(best_parsed, "_attempt_history", attempt_history)
        except Exception:
            pass  # Pydantic v2 allows __setattr__ on extra attrs; swallow otherwise
        _stash_token_usage(best_parsed, total_tokens_in, total_tokens_out)
        return best_parsed

    raise AgentError(
        f"{agent} agent failed after {max_retries} retries. "
        f"Last errors: {attempt_history[-1]['errors'] if attempt_history else []}"
    )


def _build_retry_block(history: list[dict]) -> str:
    """Render the cumulative ``PREVIOUS ATTEMPTS`` block for the next retry.

    Returns the empty string for the first attempt (no history yet).
    Each prior attempt is rendered as ``Attempt N: <summary>`` followed
    by an indented bullet list of the validation errors that fired on
    that attempt. The model gets the full trade-off picture instead of
    only the last failure, which is what stops the random-walk between
    "fix coverage by adding clip X" → "now duration is over" → "drop X
    again" cycles.

    Token cost: roughly 30-80 tokens per attempt (1 summary line + 2-5
    error lines). Five attempts add ~300 tokens to the prompt — cheap
    next to the retry's regenerate cost.
    """
    if not history:
        return ""
    lines: list[str] = [
        "",
        "PREVIOUS ATTEMPTS — these failed validation. Do not repeat the same",
        "approach; produce something materially different that satisfies",
        "every constraint listed in the rules above.",
        "",
    ]
    for entry in history:
        n = entry["attempt"]
        summary = entry.get("summary")
        errs = entry.get("errors") or []
        header = f"Attempt {n}: {summary}" if summary else f"Attempt {n}:"
        lines.append(header)
        for err in errs:
            lines.append(f"  - {err}")
        lines.append("")
    lines.append(
        "Now produce a plan that avoids every failure mode above and satisfies "
        "every constraint in the rules section."
    )
    return "\n".join(lines)


def _stash_token_usage(parsed, tokens_in: int, tokens_out: int) -> None:
    """Attach summed retry-token totals to the returned parsed object.

    The iterative-critic-loop reads ``getattr(plan, "_token_usage", {})``
    off the returned Director plan and accumulates the totals across
    iterations to enforce ``CUTMASTER_STORY_CRITIC_TOKEN_BUDGET``.
    Callers that don't care simply never look at the attribute.
    Mirrors the existing ``_validation_errors`` stash idiom.
    """
    try:
        object.__setattr__(parsed, "_token_usage", {"in": tokens_in, "out": tokens_out})
    except Exception:
        pass


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
