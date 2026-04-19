"""Structured logging for celavii-resolve.

Two modes, selected by the ``CELAVII_LOG_FORMAT`` env var:

- unset / anything other than ``"json"`` → human-readable (legacy default).
- ``"json"`` → one JSON object per line, schema below.

Log record schema (JSON mode):

    {
      "ts":          float (epoch seconds — matches state.make_event()['ts']),
      "level":       "INFO" | "WARNING" | ...,
      "logger":      hyphenated celavii-resolve.* name,
      "msg":         the formatted log message,
      "run_id":      12-char hex, optional (from the ContextVar below),
      "stage":       pipeline stage name, optional,
      "status":      "started" | "complete" | "failed", optional,
      "elapsed_ms":  int, optional — only on stage-closing transitions,
      <allowlisted extras>
    }

Sensitive payloads — transcripts, prompts, raw user input — are deliberately
NOT in the allowlist. Adding them would leak into log aggregators.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# run_id propagation via ContextVar
# ---------------------------------------------------------------------------

# Lowercase module-level per stdlib convention. Default None so records
# outside a run context emit with no run_id field at all.
_run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "celavii_run_id", default=None
)


def get_run_id() -> str | None:
    """Current run_id for the calling task/thread, or None."""
    return _run_id_ctx.get()


@contextmanager
def with_run_id(run_id: str) -> Iterator[None]:
    """Bind ``run_id`` as the current log context for the wrapped block.

    Works across ``await`` because ContextVars are asyncio-aware. Crossing
    into a worker thread requires ``contextvars.copy_context().run(fn)``
    at the hand-off point — see ``celavii_resolve.http.routes.cutmaster``
    handlers that call ``asyncio.to_thread`` for examples.
    """
    token = _run_id_ctx.set(run_id)
    try:
        yield
    finally:
        _run_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# LogFilter — injects run_id + any allowlisted extras onto every record
# ---------------------------------------------------------------------------


# Keys permitted to pass through from `log.info(..., extra={...})` into the
# JSON record. Anything not on this list is silently dropped so we can't
# accidentally log transcripts, prompts, or other sensitive payloads.
ALLOWED_EXTRA_KEYS = frozenset(
    {
        "run_id",
        "stage",
        "status",
        "elapsed_ms",
        "clip_count",
        "segment_count",
        "word_count",
        "error_type",
        "provider",
        "preset",
        "timeline_name",
        "mode",
        "method",
        "path",
        "status_code",
        "duration_ms",
    }
)


class RunIdFilter(logging.Filter):
    """Attach the current ContextVar run_id to every record as ``record.run_id``."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "run_id") or getattr(record, "run_id", None) is None:
            record.run_id = get_run_id()
        return True


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record, keys pinned to the schema."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        run_id = getattr(record, "run_id", None)
        if run_id:
            payload["run_id"] = run_id

        # Allowlisted extras — anything a caller passed via `extra=` that
        # isn't a standard LogRecord attribute AND is on the allowlist.
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key == "run_id":
                continue
            if key in ALLOWED_EXTRA_KEYS and value is not None:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    """Human-readable fallback. Adds [run_id] when present."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s  %(levelname)-7s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        run_id = getattr(record, "run_id", None)
        if run_id:
            return f"{base}  [run_id={run_id}]"
        return base


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def configure_logging(level: int = logging.INFO) -> None:
    """Install the celavii log handler on the root logger.

    Idempotent — calling this twice replaces the existing handler rather
    than stacking duplicates. Safe to invoke from both the stdio entry
    point and the panel main().
    """
    fmt_name = os.getenv("CELAVII_LOG_FORMAT", "").strip().lower()
    formatter: logging.Formatter = JsonFormatter() if fmt_name == "json" else HumanFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RunIdFilter())

    root = logging.getLogger()
    # Replace existing handlers so repeated calls don't duplicate lines.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)


# ---------------------------------------------------------------------------
# Stage-timing helper — used by cutmaster.core.state.emit
# ---------------------------------------------------------------------------

# Tracks (run_id, stage) → epoch-seconds of the most recent "started" event.
# Populated on 'started', consumed on 'complete'/'failed' to compute
# elapsed_ms. In-memory only; stale entries leak if a started event never
# closes, which is fine — they're tiny.
_STAGE_STARTED: dict[tuple[str, str], float] = {}


def stage_started(run_id: str, stage: str) -> None:
    _STAGE_STARTED[(run_id, stage)] = time.time()


def stage_elapsed_ms(run_id: str, stage: str) -> int | None:
    """Pop the recorded start time and return ms elapsed, or None if unknown."""
    started = _STAGE_STARTED.pop((run_id, stage), None)
    if started is None:
        return None
    return int((time.time() - started) * 1000)
