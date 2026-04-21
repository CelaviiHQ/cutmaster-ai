"""Timeouts for external (STT / LLM) calls.

Wraps blocking STT / Director / Marker calls with asyncio.wait_for so a
stuck provider can't hang a run forever. The wait_for just stops
awaiting — the underlying ``asyncio.to_thread`` keeps the HTTP request
running in the background, orphaned. Documented residual: we don't
forcibly kill provider threads, we just refuse to block the pipeline
on them.

All timeouts are overridable via ``CUTMASTER_*_TIMEOUT_S`` env vars so an
operator debugging a slow provider can widen them without code changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable
from typing import TypeVar

log = logging.getLogger("cutmaster-ai.cutmaster.timeouts")


def _env_seconds(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        val = int(raw)
        return val if val > 0 else default
    except ValueError:
        log.warning("Invalid %s=%r — falling back to %ds", name, raw, default)
        return default


# Ten minutes for STT: long-form podcasts and multi-clip batches can take
# several minutes on Gemini; Deepgram finishes in seconds but cold-starts
# can still stretch.
STT_TIMEOUT_S: int = _env_seconds("CUTMASTER_STT_TIMEOUT_S", 600)

# Five minutes for Director calls: Flash 2.5 usually returns in 10-40s;
# retries for strict-JSON failures can push past 2 minutes.
DIRECTOR_TIMEOUT_S: int = _env_seconds("CUTMASTER_DIRECTOR_TIMEOUT_S", 300)

# Two minutes for the Marker agent and speaker reconciler: smaller prompts,
# no retry loop.
MARKER_TIMEOUT_S: int = _env_seconds("CUTMASTER_MARKER_TIMEOUT_S", 120)

T = TypeVar("T")


class ExternalTimeout(RuntimeError):
    """Raised when an external call exceeds its configured timeout.

    The orphaned thread may still be executing in the background; this
    exception just unblocks the pipeline so the user can cancel or retry.
    """


async def with_timeout(awaitable: Awaitable[T], timeout_s: int, label: str) -> T:
    """Await ``awaitable`` with a budget; raise :class:`ExternalTimeout` on expiry.

    ``label`` surfaces in the exception message so operators can tell which
    provider stalled without grepping stack traces.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except TimeoutError as exc:
        raise ExternalTimeout(
            f"{label} did not complete within {timeout_s}s "
            "(orphan thread may still be running — check provider status)"
        ) from exc
