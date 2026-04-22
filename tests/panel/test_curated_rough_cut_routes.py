"""Route tests for v2-11: Curated + Rough cut + compatibility guards."""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cutmaster_ai.cutmaster.analysis.marker_agent import MarkerPlan  # noqa: E402
from cutmaster_ai.cutmaster.core import state  # noqa: E402
from cutmaster_ai.cutmaster.resolve_ops.segments import ResolvedCutSegment  # noqa: E402
from cutmaster_ai.http.app import create_app  # noqa: E402
from cutmaster_ai.http.routes import cutmaster as routes  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    yield
    state._QUEUES.clear()


@pytest.fixture
def scrubbed_run():
    run = state.new_run("Timeline 1", preset="auto")
    run["status"] = "done"
    run["scrubbed"] = [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.5, "speaker_id": "S1"},
        {"word": "world.", "start_time": 0.5, "end_time": 0.95, "speaker_id": "S1"},
        {"word": "Look", "start_time": 1.2, "end_time": 1.45, "speaker_id": "S1"},
        {"word": "at", "start_time": 1.45, "end_time": 1.55, "speaker_id": "S1"},
        {"word": "this.", "start_time": 1.55, "end_time": 2.0, "speaker_id": "S1"},
    ]
    state.save(run)
    return run


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _stub_resolve(monkeypatch):
    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import cutmaster_ai.cutmaster.core.pipeline as pipeline_mod
    import cutmaster_ai.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    monkeypatch.setattr(
        routes.build,
        "resolve_segments",
        lambda _tl, _segs, **_kw: [
            ResolvedCutSegment(
                start_s=0.0,
                end_s=0.95,
                reason="stub",
                source_item_id="UID1",
                source_item_name="take1.mov",
                source_in_frame=0,
                source_out_frame=23,
                timeline_start_frame=0,
                timeline_end_frame=23,
                speed=1.0,
                speed_ramped=False,
                warnings=[],
            )
        ],
    )


def test_compat_helper_blocks_tightener_across_non_assembled_modes():
    """The compat helper is authoritative for the UI grey-out logic. The
    route skips the guard for Tightener (it self-normalises to assembled)
    but the helper is what the UI reads."""
    from cutmaster_ai.cutmaster.data.presets import (
        preset_mode_compatible,
        preset_mode_incompatibility_reason,
    )

    for mode in ("raw_dump", "rough_cut", "curated"):
        assert not preset_mode_compatible("tightener", mode)
        assert preset_mode_incompatibility_reason("tightener", mode)
    assert preset_mode_compatible("tightener", "assembled")


def test_rough_cut_reorder_false_returns_400(client, scrubbed_run):
    """Rough cut + reorder_off must explicitly reject (not normalise to assembled)."""
    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "rough_cut",
                "reorder_allowed": False,
            },
        },
    )
    assert r.status_code == 400
    assert "rough_cut" in r.json()["detail"] or "reorder" in r.json()["detail"]


def test_curated_reorder_false_normalises_to_assembled(client, monkeypatch, scrubbed_run):
    """Curated + reorder_off collapses to Assembled — same contract."""
    from cutmaster_ai.cutmaster.core.director import (
        AssembledDirectorPlan,
        AssembledItemSelection,
        WordSpan,
    )

    _stub_resolve(monkeypatch)
    fake_items = [{"item_index": 0, "source_name": "take1.mov", "start_s": 0.0, "end_s": 2.0}]
    monkeypatch.setattr(routes.build, "read_items_on_track", lambda _tl, track_index=1: fake_items)

    def fake_assembled(takes, preset, settings, resolved=None):
        return AssembledDirectorPlan(
            hook_index=0,
            selections=[AssembledItemSelection(item_index=0, kept_word_spans=[WordSpan(a=0, b=1)])],
            reasoning="ok",
        )

    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", fake_assembled)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "curated",
                "reorder_allowed": False,
            },
        },
    )
    assert r.status_code == 200, r.text
    saved = state.load(scrubbed_run["run_id"])["plan"]["user_settings"]
    # The normalisation should land the run under assembled mode.
    assert saved["timeline_mode"] == "assembled"


def test_curated_mode_calls_curated_director(client, monkeypatch, scrubbed_run):
    from cutmaster_ai.cutmaster.core.director import (
        CuratedDirectorPlan,
        CuratedItemSelection,
        WordSpan,
    )

    _stub_resolve(monkeypatch)
    fake_items = [
        {"item_index": 0, "source_name": "take1.mov", "start_s": 0.0, "end_s": 1.0},
        {"item_index": 1, "source_name": "take2.mov", "start_s": 1.0, "end_s": 2.0},
    ]
    monkeypatch.setattr(routes.build, "read_items_on_track", lambda _tl, track_index=1: fake_items)

    call_log: dict = {}

    def fake_curated(takes, preset, settings, resolved=None):
        call_log["takes"] = takes
        return CuratedDirectorPlan(
            hook_order=0,
            selections=[
                CuratedItemSelection(order=0, item_index=0, kept_word_spans=[WordSpan(a=0, b=0)]),
                CuratedItemSelection(order=1, item_index=1, kept_word_spans=[WordSpan(a=0, b=0)]),
            ],
            reasoning="ok",
        )

    def forbidden(*_a, **_k):
        raise AssertionError("assembled Director must not run in curated mode")

    monkeypatch.setattr(routes.build, "build_curated_cut_plan", fake_curated)
    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", forbidden)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "curated",
            },
        },
    )
    assert r.status_code == 200, r.text
    assert "takes" in call_log and len(call_log["takes"]) == 2
    persisted = state.load(scrubbed_run["run_id"])["plan"]
    assert persisted["timeline_state"]["mode"] == "curated"
    assert persisted["timeline_state"]["total_takes"] == 2


def test_rough_cut_mode_runs_group_detector_and_director(client, monkeypatch, scrubbed_run):
    from cutmaster_ai.cutmaster.core.director import (
        CuratedDirectorPlan,
        CuratedItemSelection,
        WordSpan,
    )

    _stub_resolve(monkeypatch)
    # Two adjacent items with identical transcripts — similarity detector
    # should cluster them into one group, then the Director picks a winner.
    grouped_items = [
        {
            "item_index": 0,
            "source_name": "A.mov",
            "start_s": 0.0,
            "end_s": 1.0,
            "clip_color": "Orange",
            "flags": [],
        },
        {
            "item_index": 1,
            "source_name": "B.mov",
            "start_s": 1.0,
            "end_s": 2.0,
            "clip_color": "Orange",  # same color → one group
            "flags": [],
        },
    ]
    monkeypatch.setattr(
        routes.build,
        "read_items_with_grouping_signals",
        lambda _tl, track_index=1: grouped_items,
    )

    call_log: dict = {}

    def fake_rough(takes, groups, preset, settings, resolved=None):
        call_log["takes"] = takes
        call_log["groups"] = groups
        # Pick take 0 as the winner of the single color group.
        return CuratedDirectorPlan(
            hook_order=0,
            selections=[
                CuratedItemSelection(order=0, item_index=0, kept_word_spans=[WordSpan(a=0, b=0)])
            ],
            reasoning="picked A",
        )

    monkeypatch.setattr(routes.build, "build_rough_cut_plan", fake_rough)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "rough_cut",
            },
        },
    )
    assert r.status_code == 200, r.text
    # Detector clustered two same-color items into a single group.
    assert len(call_log["groups"]) == 1
    assert call_log["groups"][0]["signal"] == "color"
    assert sorted(call_log["groups"][0]["item_indexes"]) == [0, 1]
    meta = state.load(scrubbed_run["run_id"])["plan"]["timeline_state"]
    assert meta["mode"] == "rough_cut"
    assert len(meta["groups"]) == 1
    assert meta["all_singletons"] is False


def test_rough_cut_all_singletons_flag_set(client, monkeypatch, scrubbed_run):
    """No colors / flags and dissimilar transcripts → every item is its own group."""
    from cutmaster_ai.cutmaster.core.director import (
        CuratedDirectorPlan,
        CuratedItemSelection,
        WordSpan,
    )

    _stub_resolve(monkeypatch)
    grouped_items = [
        {
            "item_index": 0,
            "source_name": "A.mov",
            "start_s": 0.0,
            "end_s": 1.0,
            "clip_color": "",
            "flags": [],
        },
        {
            "item_index": 1,
            "source_name": "B.mov",
            "start_s": 1.2,
            "end_s": 2.0,
            "clip_color": "",
            "flags": [],
        },
    ]
    monkeypatch.setattr(
        routes.build,
        "read_items_with_grouping_signals",
        lambda _tl, track_index=1: grouped_items,
    )

    def fake_rough(takes, groups, preset, settings, resolved=None):
        # Two singleton groups → must cover both.
        return CuratedDirectorPlan(
            hook_order=0,
            selections=[
                CuratedItemSelection(order=0, item_index=0, kept_word_spans=[WordSpan(a=0, b=0)]),
                CuratedItemSelection(order=1, item_index=1, kept_word_spans=[WordSpan(a=0, b=0)]),
            ],
            reasoning="ok",
        )

    monkeypatch.setattr(routes.build, "build_rough_cut_plan", fake_rough)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "rough_cut",
            },
        },
    )
    assert r.status_code == 200, r.text
    meta = state.load(scrubbed_run["run_id"])["plan"]["timeline_state"]
    assert meta["all_singletons"] is True
