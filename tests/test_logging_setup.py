"""Structured logging — JSON formatter, run_id propagation, stage timings."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time

import pytest

from cutmaster_ai import logging_setup


@pytest.fixture(autouse=True)
def reset_handlers():
    """Give each test a pristine root logger + clean context + stage dict."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    logging_setup._STAGE_STARTED.clear()
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logging_setup._STAGE_STARTED.clear()


def _attach_capture_handler(formatter: logging.Formatter) -> io.StringIO:
    """Install a single StreamHandler on the root logger and return its sink."""
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    handler.setFormatter(formatter)
    handler.addFilter(logging_setup.RunIdFilter())
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return sink


def _parse_json_lines(sink: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


def test_json_formatter_emits_schema_keys():
    sink = _attach_capture_handler(logging_setup.JsonFormatter())
    log = logging.getLogger("cutmaster-ai.test")
    log.info("hello")

    records = _parse_json_lines(sink)
    assert len(records) == 1
    rec = records[0]
    assert set(rec.keys()) >= {"ts", "level", "logger", "msg"}
    assert rec["level"] == "INFO"
    assert rec["logger"] == "cutmaster-ai.test"
    assert rec["msg"] == "hello"
    # ts is epoch seconds, not ISO.
    assert isinstance(rec["ts"], float)
    assert abs(rec["ts"] - time.time()) < 5


def test_json_formatter_allowlists_extras():
    sink = _attach_capture_handler(logging_setup.JsonFormatter())
    log = logging.getLogger("cutmaster-ai.test")
    log.info(
        "stage done",
        extra={
            "stage": "stt",
            "status": "complete",
            "elapsed_ms": 1234,
            "transcript": ["secret", "words"],  # NOT allowlisted — must drop.
            "prompt": "do not leak me",  # NOT allowlisted — must drop.
        },
    )

    rec = _parse_json_lines(sink)[0]
    assert rec["stage"] == "stt"
    assert rec["status"] == "complete"
    assert rec["elapsed_ms"] == 1234
    assert "transcript" not in rec
    assert "prompt" not in rec


# ---------------------------------------------------------------------------
# run_id propagation
# ---------------------------------------------------------------------------


def test_with_run_id_injects_field():
    sink = _attach_capture_handler(logging_setup.JsonFormatter())
    log = logging.getLogger("cutmaster-ai.test")

    log.info("outside context")
    with logging_setup.with_run_id("abc123def456"):
        log.info("inside context")
    log.info("after context")

    recs = _parse_json_lines(sink)
    assert "run_id" not in recs[0]
    assert recs[1]["run_id"] == "abc123def456"
    assert "run_id" not in recs[2]


@pytest.mark.asyncio
async def test_run_id_survives_asyncio_to_thread():
    """ContextVars are asyncio-aware; ``asyncio.to_thread`` also copies
    the context by default (Py 3.9+), so thread workers see the id."""
    sink = _attach_capture_handler(logging_setup.JsonFormatter())
    log = logging.getLogger("cutmaster-ai.test")

    def _in_thread():
        log.info("from thread")

    with logging_setup.with_run_id("thread_test"):
        await asyncio.to_thread(_in_thread)

    rec = _parse_json_lines(sink)[0]
    assert rec["run_id"] == "thread_test"


# ---------------------------------------------------------------------------
# Stage-timing helpers
# ---------------------------------------------------------------------------


def test_stage_elapsed_ms_returns_none_without_start():
    assert logging_setup.stage_elapsed_ms("run1", "stt") is None


def test_stage_elapsed_ms_pops_after_close():
    logging_setup.stage_started("run1", "stt")
    elapsed = logging_setup.stage_elapsed_ms("run1", "stt")
    assert elapsed is not None
    assert elapsed >= 0
    # Second pop returns None.
    assert logging_setup.stage_elapsed_ms("run1", "stt") is None


# ---------------------------------------------------------------------------
# configure_logging — mode switching + handler replacement
# ---------------------------------------------------------------------------


def test_configure_logging_picks_json_via_env(monkeypatch):
    monkeypatch.setenv("CUTMASTER_LOG_FORMAT", "json")
    logging_setup.configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logging_setup.JsonFormatter)


def test_configure_logging_defaults_to_human(monkeypatch):
    monkeypatch.delenv("CUTMASTER_LOG_FORMAT", raising=False)
    logging_setup.configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logging_setup.HumanFormatter)


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.setenv("CUTMASTER_LOG_FORMAT", "json")
    logging_setup.configure_logging()
    logging_setup.configure_logging()
    root = logging.getLogger()
    # Second call must replace, not stack.
    assert len(root.handlers) == 1
