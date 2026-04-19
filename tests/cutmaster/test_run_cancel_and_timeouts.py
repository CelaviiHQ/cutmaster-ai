"""Run cancellation + external-call timeout behaviour.

Covers:
- ``with_timeout`` raises :class:`ExternalTimeout` past the budget, passes
  through the fast-path unchanged.
- ``_check_cancel`` raises :class:`ExecuteCancelled` when the flag is on.
- ``/cancel`` flips ``run['execute']['cancel_requested']`` when an execute
  is in progress, without crashing when no analyze task is registered.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve.cutmaster.core import state  # noqa: E402
from celavii_resolve.cutmaster.core.execute import (  # noqa: E402
    ExecuteCancelled,
    _check_cancel,
)
from celavii_resolve.cutmaster.core.timeouts import (  # noqa: E402
    ExternalTimeout,
    with_timeout,
)
from celavii_resolve.http.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()
    yield
    state._QUEUES.clear()
    state._LOCKS.clear()
    state._TASKS.clear()


# ---------------------------------------------------------------------------
# with_timeout (2b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_timeout_returns_fast_result():
    async def _fast():
        return "ok"

    assert await with_timeout(_fast(), 5, "fast") == "ok"


@pytest.mark.asyncio
async def test_with_timeout_raises_external_timeout_on_stall():
    async def _stall():
        await asyncio.sleep(10)

    with pytest.raises(ExternalTimeout) as exc_info:
        # Use a very short budget so the test stays fast.
        await with_timeout(_stall(), 1, "stall test")
    assert "stall test" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _check_cancel + ExecuteCancelled (2c)
# ---------------------------------------------------------------------------


def test_check_cancel_no_op_when_callable_is_none():
    # Should not raise.
    _check_cancel(None, "any stage")


def test_check_cancel_no_op_when_flag_clear():
    _check_cancel(lambda: False, "any stage")


def test_check_cancel_raises_when_flag_set():
    with pytest.raises(ExecuteCancelled) as exc_info:
        _check_cancel(lambda: True, "pre-append")
    assert "pre-append" in str(exc_info.value)


# ---------------------------------------------------------------------------
# /cancel dispatching on execute-running state (2a)
# ---------------------------------------------------------------------------


def test_cancel_flips_execute_flag_when_execute_in_progress():
    """Simulates an execute that's mid-build: on-disk run has
    ``execute.status == 'running'``. /cancel must set ``cancel_requested``
    and still return 200 without needing an analyze task in the registry.
    """
    client = TestClient(create_app())

    run = state.new_run("T1", preset="vlog")
    run["status"] = "done"  # analyze already finished
    run["execute"] = {"status": "running", "started_at": 0.0}
    state.save(run)

    r = client.post(f"/cutmaster/cancel/{run['run_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["noop"] is False
    assert body["execute_running"] is True
    assert body["analyze_task_cancelled"] is False  # no task registered

    persisted = state.load(run["run_id"])
    assert persisted["status"] == "cancelled"
    assert persisted["execute"]["cancel_requested"] is True


def test_cancel_is_noop_for_already_cancelled_run():
    """'done' isn't terminal for cancellation — execute can still fire.
    Only 'failed' and 'cancelled' short-circuit to a noop.
    """
    client = TestClient(create_app())

    run = state.new_run("T1")
    run["status"] = "cancelled"
    state.save(run)

    r = client.post(f"/cutmaster/cancel/{run['run_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["noop"] is True
    assert body["status"] == "cancelled"


def test_cancel_done_run_without_execute_running_still_cancels():
    """A completed analyze ('done') with no execute running is cancellable —
    the user might just want to abandon the run. Flips status to cancelled.
    """
    client = TestClient(create_app())

    run = state.new_run("T1")
    run["status"] = "done"
    state.save(run)

    r = client.post(f"/cutmaster/cancel/{run['run_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["noop"] is False
    assert body["status"] == "cancelled"
    assert body["execute_running"] is False
