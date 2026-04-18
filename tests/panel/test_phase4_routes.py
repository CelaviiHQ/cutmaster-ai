"""Route tests for Phase 4: /presets, /detect-preset, /analyze-themes, /build-plan.

The LLM layer is mocked via monkeypatching the route module imports so we
don't call real Gemini and don't need Resolve.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve.cutmaster.analysis.auto_detect import PresetRecommendation  # noqa: E402
from celavii_resolve.cutmaster.analysis.marker_agent import (  # noqa: E402
    MarkerPlan,
    MarkerSuggestion,
)
from celavii_resolve.cutmaster.analysis.themes import (  # noqa: E402
    Chapter,
    HookCandidate,
    StoryAnalysis,
)
from celavii_resolve.cutmaster.core import state  # noqa: E402
from celavii_resolve.cutmaster.core.director import CutSegment, DirectorPlan  # noqa: E402
from celavii_resolve.cutmaster.resolve_ops.segments import ResolvedCutSegment  # noqa: E402
from celavii_resolve.http.app import create_app  # noqa: E402
from celavii_resolve.http.routes import cutmaster as routes  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "EXTRACT_ROOT", tmp_path / "audio")
    state._QUEUES.clear()
    yield
    state._QUEUES.clear()


@pytest.fixture
def scrubbed_run():
    """A run record with a scrubbed transcript already populated."""
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


def test_list_presets(client: TestClient):
    r = client.get("/cutmaster/presets")
    assert r.status_code == 200
    keys = [p["key"] for p in r.json()["presets"]]
    assert {
        "vlog",
        "product_demo",
        "wedding",
        "interview",
        "tutorial",
        "podcast",
        "reaction",
    } <= set(keys)


def test_list_formats(client: TestClient):
    r = client.get("/cutmaster/formats")
    assert r.status_code == 200
    keys = [f["key"] for f in r.json()["formats"]]
    assert keys == ["horizontal", "vertical_short", "square"]
    # Shape check for the Configure screen's length-clamp logic.
    for f in r.json()["formats"]:
        assert "width" in f and "height" in f
        assert "safe_zones" in f


def test_build_plan_accepts_and_persists_v2_10_format_fields(
    client,
    monkeypatch,
    scrubbed_run,
):
    """v2-10 adds `format` / `captions_enabled` / `safe_zones_enabled` to
    UserSettings. The route must accept them, validate `format` as a
    Literal, and round-trip them through persisted state."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": 60,
                "themes": [],
                "format": "vertical_short",
                "captions_enabled": True,
                "safe_zones_enabled": True,
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved = persisted["plan"]["user_settings"]
    assert saved["format"] == "vertical_short"
    assert saved["captions_enabled"] is True
    assert saved["safe_zones_enabled"] is True


def test_build_plan_assembled_mode_uses_assembled_director(
    client,
    monkeypatch,
    scrubbed_run,
):
    """When timeline_mode='assembled', the route should call
    build_assembled_cut_plan (not the v1 Director), feed it items from
    read_items_on_track, and expand the result into normal CutSegments
    before the Marker / resolver run."""
    from celavii_resolve.cutmaster.core.director import (
        AssembledDirectorPlan,
        AssembledItemSelection,
        WordSpan,
    )

    call_log: dict[str, object] = {}

    def fake_build_assembled(takes, preset, settings):
        call_log["takes"] = takes
        call_log["settings"] = settings
        return AssembledDirectorPlan(
            hook_index=0,
            selections=[
                AssembledItemSelection(
                    item_index=0,
                    kept_word_spans=[WordSpan(a=0, b=1)],
                )
            ],
            reasoning="hook",
        )

    def forbidden_build_cut_plan(*_a, **_k):
        raise AssertionError("v1 Director should not run in assembled mode")

    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", fake_build_assembled)
    monkeypatch.setattr(routes.build, "build_cut_plan", forbidden_build_cut_plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    # Stub items on the timeline: one take covering [0, 2) s.
    fake_items = [
        {
            "item_index": 0,
            "source_name": "take1.mov",
            "start_s": 0.0,
            "end_s": 2.0,
        }
    ]
    monkeypatch.setattr(routes.build, "read_items_on_track", lambda _tl, track_index=1: fake_items)

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    # Resolver receives the expanded segments — stub it to a known value.
    resolved_stub = [
        ResolvedCutSegment(
            start_s=0.0,
            end_s=0.95,
            reason="take 0",
            source_item_id="UID1",
            source_item_name="take1.mov",
            source_in_frame=0,
            source_out_frame=23,
            timeline_start_frame=0,
            timeline_end_frame=23,
            speed=1.0,
            speed_ramped=False,
            warnings=[],
        ),
    ]
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: resolved_stub)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": None,
                "themes": [],
                "timeline_mode": "assembled",
                "reorder_allowed": True,
            },
        },
    )
    assert r.status_code == 200, r.text

    # The assembled Director received a take payload shaped from the scrubbed
    # transcript — words falling inside item 0's range are its transcript.
    assert "takes" in call_log
    take = call_log["takes"][0]
    assert take["item_index"] == 0
    assert take["source_name"] == "take1.mov"
    assert len(take["transcript"]) > 0
    # Settings round-trip carries the mode.
    assert call_log["settings"]["timeline_mode"] == "assembled"

    persisted = state.load(scrubbed_run["run_id"])
    saved = persisted["plan"]["user_settings"]
    assert saved["timeline_mode"] == "assembled"
    assert saved["reorder_allowed"] is True
    # The expanded plan was persisted as a normal DirectorPlan shape.
    assert len(persisted["plan"]["director"]["selected_clips"]) == 1


def test_build_plan_assembled_uses_raw_transcript_when_takes_already_scrubbed(
    client,
    monkeypatch,
    scrubbed_run,
):
    """When takes_already_scrubbed=true, build-plan must feed the raw
    transcript (not the scrubbed one) into the assembled Director."""
    from celavii_resolve.cutmaster.core.director import (
        AssembledDirectorPlan,
        AssembledItemSelection,
        WordSpan,
    )

    # Seed run with a raw transcript that differs from scrubbed.
    run = state.load(scrubbed_run["run_id"])
    run["transcript"] = [
        {"word": "umm", "start_time": 0.0, "end_time": 0.3, "speaker_id": "S1"},
        {"word": "hello", "start_time": 0.3, "end_time": 0.7, "speaker_id": "S1"},
    ]
    state.save(run)

    seen_takes: dict[str, object] = {}

    def fake_build_assembled(takes, preset, settings):
        seen_takes["takes"] = takes
        return AssembledDirectorPlan(
            hook_index=0,
            selections=[
                AssembledItemSelection(
                    item_index=0,
                    kept_word_spans=[WordSpan(a=0, b=0)],
                )
            ],
            reasoning="",
        )

    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", fake_build_assembled)
    monkeypatch.setattr(
        routes.build, "build_cut_plan", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))
    monkeypatch.setattr(
        routes.build,
        "read_items_on_track",
        lambda _tl, track_index=1: [
            {"item_index": 0, "source_name": "t.mov", "start_s": 0.0, "end_s": 1.0},
        ],
    )
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "_boilerplate", lambda: (MagicMock(), MagicMock(), MagicMock())
    )
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "timeline_mode": "assembled",
                "takes_already_scrubbed": True,
            },
        },
    )
    assert r.status_code == 200, r.text

    # The take's transcript should include the filler 'umm' (raw) — proof
    # we consumed run["transcript"] not run["scrubbed"].
    take_words = [t["word"] for t in seen_takes["takes"][0]["transcript"]]
    assert "umm" in take_words


def test_build_plan_clip_hunter_stores_candidates_and_skips_director(
    client,
    monkeypatch,
    scrubbed_run,
):
    """When preset='clip_hunter', /build-plan must call the Clip Hunter
    Director (NOT the v1 or assembled Director), persist all candidates
    with their resolved_segments, default selected_index to 0, and skip
    the Marker LLM entirely."""
    from celavii_resolve.cutmaster.core.director import ClipCandidate, ClipHunterPlan

    def fake_hunter(transcript, preset, settings, target, num):
        return ClipHunterPlan(
            candidates=[
                ClipCandidate(
                    start_s=0.0,
                    end_s=0.95,
                    engagement_score=0.9,
                    quote="Hello world",
                    suggested_caption="Say hi.",
                    reasoning="opener",
                ),
                ClipCandidate(
                    start_s=1.2,
                    end_s=2.0,
                    engagement_score=0.7,
                    quote="Look at this.",
                    suggested_caption="Check it out.",
                    reasoning="pointer",
                ),
            ],
            reasoning="Two punchy lines",
        )

    def forbidden_director(*_a, **_k):
        raise AssertionError("clip_hunter path must NOT call the v1 Director")

    def forbidden_assembled(*_a, **_k):
        raise AssertionError("clip_hunter path must NOT call the assembled Director")

    def forbidden_marker(*_a, **_k):
        raise AssertionError("clip_hunter path must NOT call the Marker agent")

    monkeypatch.setattr(routes.build, "build_clip_hunter_plan", fake_hunter)
    monkeypatch.setattr(routes.build, "build_cut_plan", forbidden_director)
    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", forbidden_assembled)
    monkeypatch.setattr(routes.build, "suggest_markers", forbidden_marker)

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "_boilerplate", lambda: (MagicMock(), MagicMock(), MagicMock())
    )
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)

    # Distinguish per-candidate resolver output by tagging the source item.
    def fake_resolver(_tl, segs):
        # Each call returns a single resolved segment carrying the segment's
        # start_s so the test can assert the mapping.
        return [
            ResolvedCutSegment(
                start_s=float(s.start_s),
                end_s=float(s.end_s),
                reason=s.reason,
                source_item_id=f"UID_{s.start_s}",
                source_item_name="take.mov",
                source_in_frame=0,
                source_out_frame=10,
                timeline_start_frame=0,
                timeline_end_frame=10,
                speed=1.0,
                speed_ramped=False,
                warnings=[],
            )
            for s in segs
        ]

    monkeypatch.setattr(routes.build, "resolve_segments", fake_resolver)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "clip_hunter",
            # Short-source guard requires source_s >= num * target * 0.6.
            # Fixture scrubbed_run spans ~2s, so target_length_s=1 with 2
            # clips fits (needs ≥1.2s).
            "user_settings": {"target_length_s": 1, "num_clips": 2},
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    ch = persisted["plan"]["clip_hunter"]
    assert len(ch["candidates"]) == 2
    assert ch["selected_index"] == 0
    assert ch["target_clip_length_s"] == 1
    assert ch["num_clips"] == 2
    # Each candidate carries its resolved_segments slice (for /execute).
    assert len(ch["candidates"][0]["resolved_segments"]) == 1
    assert len(ch["candidates"][1]["resolved_segments"]) == 1
    # Top-level resolved_segments defaults to candidate 0.
    assert persisted["plan"]["resolved_segments"][0]["source_item_id"] == "UID_0.0"


def test_build_plan_clip_hunter_rejects_sources_past_60min(
    client,
    monkeypatch,
    scrubbed_run,
):
    """Proposal §4.7: Clip Hunter hard-caps source duration at 60 min for v2."""
    run = state.load(scrubbed_run["run_id"])
    run["scrubbed"] = [
        {
            "word": "endless",
            "start_time": 0.0,
            "end_time": 60 * 60 + 1.0,
            "speaker_id": "S1",
        }
    ]
    state.save(run)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "clip_hunter",
            "user_settings": {"num_clips": 3},
        },
    )
    assert r.status_code == 400
    assert "60 min" in r.text


def test_execute_clip_hunter_swaps_resolved_segments_to_selected_candidate(
    client,
    monkeypatch,
    scrubbed_run,
):
    """POST /execute with candidate_index must swap the plan's
    resolved_segments to that candidate's slice before execute_plan runs,
    and name the new timeline ``<source>_AI_Clip_N``."""
    # Seed a persisted clip_hunter plan by hand (skip the build step).
    run = state.load(scrubbed_run["run_id"])
    cand0_segs = [
        {
            "start_s": 0.0,
            "end_s": 10.0,
            "reason": "hook",
            "source_item_id": "UID_0",
            "source_item_name": "t.mov",
            "source_in_frame": 0,
            "source_out_frame": 240,
            "timeline_start_frame": 0,
            "timeline_end_frame": 240,
            "speed": 1.0,
            "speed_ramped": False,
            "part_index": 0,
            "part_total": 1,
            "warnings": [],
        }
    ]
    cand1_segs = [
        {
            "start_s": 60.0,
            "end_s": 70.0,
            "reason": "second",
            "source_item_id": "UID_1",
            "source_item_name": "t.mov",
            "source_in_frame": 1440,
            "source_out_frame": 1680,
            "timeline_start_frame": 1440,
            "timeline_end_frame": 1680,
            "speed": 1.0,
            "speed_ramped": False,
            "part_index": 0,
            "part_total": 1,
            "warnings": [],
        }
    ]
    run["plan"] = {
        "preset": "clip_hunter",
        "user_settings": {"num_clips": 2, "target_length_s": 10},
        "director": {"hook_index": 0, "selected_clips": [], "reasoning": ""},
        "markers": {"markers": []},
        "resolved_segments": cand0_segs,
        "clip_hunter": {
            "candidates": [
                {
                    "start_s": 0.0,
                    "end_s": 10.0,
                    "quote": "a",
                    "engagement_score": 0.9,
                    "suggested_caption": "",
                    "reasoning": "",
                    "resolved_segments": cand0_segs,
                },
                {
                    "start_s": 60.0,
                    "end_s": 70.0,
                    "quote": "b",
                    "engagement_score": 0.7,
                    "suggested_caption": "",
                    "reasoning": "",
                    "resolved_segments": cand1_segs,
                },
            ],
            "selected_index": 0,
            "target_clip_length_s": 10,
            "num_clips": 2,
            "duration_warning": None,
            "source_duration_s": 120.0,
        },
    }
    state.save(run)

    captured: dict = {}

    def fake_execute_plan(run_arg, name_suffix="_AI_Cut"):
        captured["resolved_segments"] = run_arg["plan"]["resolved_segments"]
        captured["name_suffix"] = name_suffix
        return {
            "new_timeline_name": f"{run_arg['timeline_name']}{name_suffix}",
            "appended": 1,
            "append_errors": [],
            "markers_added": 0,
            "markers_skipped": [],
            "snapshot_path": "/tmp/snap.drp",
            "snapshot_size_kb": 1.0,
        }

    monkeypatch.setattr(routes.execute, "execute_plan", fake_execute_plan)

    # Pick candidate index 1.
    r = client.post(
        "/cutmaster/execute",
        json={"run_id": scrubbed_run["run_id"], "candidate_index": 1},
    )
    assert r.status_code == 200, r.text
    # Execute saw cand1's segments, not cand0's.
    assert captured["resolved_segments"][0]["source_item_id"] == "UID_1"
    assert captured["name_suffix"] == "_AI_Clip_2"


def test_execute_clip_hunter_rejects_out_of_range_candidate(
    client,
    monkeypatch,
    scrubbed_run,
):
    run = state.load(scrubbed_run["run_id"])
    run["plan"] = {
        "preset": "clip_hunter",
        "user_settings": {},
        "director": {"hook_index": 0, "selected_clips": [], "reasoning": ""},
        "markers": {"markers": []},
        "resolved_segments": [],
        "clip_hunter": {
            "candidates": [
                {
                    "start_s": 0.0,
                    "end_s": 10.0,
                    "quote": "",
                    "engagement_score": 0.8,
                    "suggested_caption": "",
                    "reasoning": "",
                    "resolved_segments": [],
                }
            ],
            "selected_index": 0,
            "target_clip_length_s": 10,
            "num_clips": 1,
            "duration_warning": None,
            "source_duration_s": 60.0,
        },
    }
    state.save(run)

    r = client.post(
        "/cutmaster/execute",
        json={"run_id": scrubbed_run["run_id"], "candidate_index": 5},
    )
    assert r.status_code == 400
    assert "out of range" in r.text


def test_build_plan_tightener_skips_director_and_returns_stats(
    client,
    monkeypatch,
    scrubbed_run,
):
    """When preset='tightener', /build-plan must NOT call the Director or
    Marker LLMs and must return a `tightener` summary block with
    kept/original/percent-tighter counts."""
    run = state.load(scrubbed_run["run_id"])
    # Raw transcript with a scrubbable filler and a gap.
    run["transcript"] = [
        {"word": "Hello", "start_time": 0.0, "end_time": 0.4, "speaker_id": "S1"},
        {"word": "um", "start_time": 0.4, "end_time": 0.6, "speaker_id": "S1"},
        {"word": "world.", "start_time": 0.6, "end_time": 1.0, "speaker_id": "S1"},
        {"word": "Second", "start_time": 3.0, "end_time": 3.4, "speaker_id": "S1"},
        {"word": "take.", "start_time": 3.4, "end_time": 3.8, "speaker_id": "S1"},
    ]
    state.save(run)

    def forbidden_director(*_a, **_k):
        raise AssertionError("tightener path must NOT call the Director")

    def forbidden_assembled(*_a, **_k):
        raise AssertionError("tightener path must NOT call the assembled Director")

    def forbidden_marker(*_a, **_k):
        raise AssertionError("tightener path must NOT call the Marker agent")

    monkeypatch.setattr(routes.build, "build_cut_plan", forbidden_director)
    monkeypatch.setattr(routes.build, "build_assembled_cut_plan", forbidden_assembled)
    monkeypatch.setattr(routes.build, "suggest_markers", forbidden_marker)

    fake_items = [
        {
            "item_index": 0,
            "source_name": "take1.mov",
            "start_s": 0.0,
            "end_s": 4.0,
        }
    ]
    monkeypatch.setattr(routes.build, "read_items_on_track", lambda _tl, track_index=1: fake_items)

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod, "_boilerplate", lambda: (MagicMock(), MagicMock(), MagicMock())
    )
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "tightener",
            "user_settings": {},
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved = persisted["plan"]["user_settings"]
    # Route must normalise these even if caller omitted them.
    assert saved["timeline_mode"] == "assembled"
    assert saved["reorder_allowed"] is False
    # Tightener stats must land on the plan.
    tight = persisted["plan"]["tightener"]
    assert tight["original_words"] == 5
    # Filler 'um' dropped → kept 4.
    assert tight["kept_words"] == 4


def test_build_plan_tightener_errors_when_raw_transcript_missing(
    client,
    monkeypatch,
    scrubbed_run,
):
    """Tightener reads run['transcript']. If analyze somehow finished
    without populating it (unlikely but possible on legacy runs), the
    route must surface a clean 400 instead of silently falling back."""
    run = state.load(scrubbed_run["run_id"])
    run["transcript"] = []  # wipe it
    state.save(run)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "tightener",
            "user_settings": {},
        },
    )
    assert r.status_code == 400
    assert "raw transcript" in r.text


def test_build_plan_rejects_unknown_format(client, monkeypatch, scrubbed_run):
    """Pydantic's Literal guard should 422 on an invalid format key."""
    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {
                "target_length_s": 60,
                "themes": [],
                "format": "ultrawide",  # not a valid key
            },
        },
    )
    assert r.status_code == 422


def test_stt_providers_endpoint_lists_both(client: TestClient, monkeypatch):
    """v2-8.1: /cutmaster/stt-providers drives the Preset-screen selector.
    Reports the default + configured state for each backend."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_xxx")
    monkeypatch.setattr(
        "celavii_resolve.cutmaster.stt.gemini.is_configured",
        lambda: True,
    )
    r = client.get("/cutmaster/stt-providers")
    assert r.status_code == 200
    body = r.json()
    keys = [p["key"] for p in body["providers"]]
    assert set(keys) == {"gemini", "deepgram"}
    by_key = {p["key"]: p for p in body["providers"]}
    assert by_key["gemini"]["configured"] is True
    assert by_key["deepgram"]["configured"] is True
    assert "default" in body


def test_analyze_accepts_stt_provider(client: TestClient, monkeypatch):
    captured: dict = {}

    async def fake_run_analyze(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(routes.analyze, "run_analyze", fake_run_analyze)
    r = client.post(
        "/cutmaster/analyze",
        json={
            "timeline_name": "T1",
            "stt_provider": "deepgram",
        },
    )
    assert r.status_code == 200
    import time as _time

    for _ in range(10):
        if captured:
            break
        _time.sleep(0.01)
    assert captured.get("stt_provider") == "deepgram"


def test_analyze_rejects_unknown_stt_provider(client: TestClient):
    r = client.post(
        "/cutmaster/analyze",
        json={
            "timeline_name": "T1",
            "stt_provider": "martian",
        },
    )
    assert r.status_code == 422


def test_project_info_returns_timelines_with_current_flag(
    client: TestClient,
    monkeypatch,
):
    """v2-8: /cutmaster/project-info lists every timeline in the open project
    and marks which one is currently active in Resolve. Drives the Preset
    screen's timeline picker."""
    fake_current = MagicMock()
    fake_current.GetName.return_value = "Timeline 2"

    fake_tls = [
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ]
    fake_tls[0].GetName.return_value = "Timeline 1"
    fake_tls[1].GetName.return_value = "Timeline 2"
    fake_tls[2].GetName.return_value = "B-Roll"
    for tl in fake_tls:
        tl.GetItemListInTrack.side_effect = lambda _kind, _idx: []

    fake_project = MagicMock()
    fake_project.GetName.return_value = "Wedding 2026"
    fake_project.GetCurrentTimeline.return_value = fake_current
    fake_project.GetTimelineCount.return_value = 3
    fake_project.GetTimelineByIndex.side_effect = lambda i: fake_tls[i - 1] if 1 <= i <= 3 else None

    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod,
        "_boilerplate",
        lambda: (MagicMock(), fake_project, MagicMock()),
    )

    r = client.get("/cutmaster/project-info")
    assert r.status_code == 200
    body = r.json()
    assert body["project_name"] == "Wedding 2026"
    names = [t["name"] for t in body["timelines"]]
    assert names == ["Timeline 1", "Timeline 2", "B-Roll"]
    is_current = {t["name"]: t["is_current"] for t in body["timelines"]}
    assert is_current == {"Timeline 1": False, "Timeline 2": True, "B-Roll": False}


def test_project_info_503_when_resolve_unreachable(
    client: TestClient,
    monkeypatch,
):
    import celavii_resolve.resolve as resolve_mod

    def boom():
        raise RuntimeError("Resolve is not running")

    monkeypatch.setattr(resolve_mod, "_boilerplate", boom)
    r = client.get("/cutmaster/project-info")
    assert r.status_code == 503


def test_analyze_accepts_per_clip_stt_flag(client: TestClient, monkeypatch):
    """v2-6: AnalyzeRequest must accept per_clip_stt and forward it to
    run_analyze so the pipeline branches into the per-clip STT path."""
    from celavii_resolve.cutmaster.core import pipeline as pipeline_mod

    captured: dict = {}

    async def fake_run_analyze(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(routes.analyze, "run_analyze", fake_run_analyze)
    # The analyze route spawns the task via asyncio.create_task; invoking
    # the fake is synchronous enough that we can just await it in the route.
    _ = pipeline_mod

    r = client.post(
        "/cutmaster/analyze",
        json={
            "timeline_name": "T1",
            "preset": "auto",
            "per_clip_stt": True,
        },
    )
    assert r.status_code == 200
    # Let the scheduled task run.
    import time as _time

    for _ in range(10):
        if captured:
            break
        _time.sleep(0.01)
    assert captured.get("per_clip_stt") is True
    assert captured.get("timeline_name") == "T1"


def test_analyze_accepts_expected_speakers(client: TestClient, monkeypatch):
    """v2-6 follow-up: expected_speakers must flow from AnalyzeRequest into
    run_analyze so the pipeline can dispatch to the reconciler."""
    captured: dict = {}

    async def fake_run_analyze(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(routes.analyze, "run_analyze", fake_run_analyze)

    r = client.post(
        "/cutmaster/analyze",
        json={
            "timeline_name": "T1",
            "per_clip_stt": True,
            "expected_speakers": 2,
        },
    )
    assert r.status_code == 200
    import time as _time

    for _ in range(10):
        if captured:
            break
        _time.sleep(0.01)
    assert captured.get("expected_speakers") == 2
    assert captured.get("per_clip_stt") is True


def test_analyze_rejects_expected_speakers_out_of_range(client: TestClient):
    # 0 and 11 both fall outside the ge=1, le=10 constraint.
    for bad in (0, 11, -1):
        r = client.post(
            "/cutmaster/analyze",
            json={
                "timeline_name": "T1",
                "expected_speakers": bad,
            },
        )
        assert r.status_code == 422, f"expected 422 for {bad}, got {r.status_code}"


def test_analyze_per_clip_stt_defaults_to_false(client: TestClient, monkeypatch):
    captured: dict = {}

    async def fake_run_analyze(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(routes.analyze, "run_analyze", fake_run_analyze)

    r = client.post("/cutmaster/analyze", json={"timeline_name": "T1"})
    assert r.status_code == 200
    import time as _time

    for _ in range(10):
        if captured:
            break
        _time.sleep(0.01)
    assert captured.get("per_clip_stt") is False


def test_speakers_endpoint_returns_roster_from_scrubbed(
    client: TestClient,
    scrubbed_run,
):
    """v2-5: /cutmaster/speakers/{run_id} must return unique speaker ids in
    first-appearance order, annotated with per-speaker word counts."""
    # Seed a two-speaker scrubbed transcript.
    run = state.load(scrubbed_run["run_id"])
    run["scrubbed"] = [
        {"word": "hi", "start_time": 0.0, "end_time": 0.2, "speaker_id": "S1"},
        {"word": "yo", "start_time": 0.3, "end_time": 0.5, "speaker_id": "S2"},
        {"word": "again", "start_time": 0.6, "end_time": 0.9, "speaker_id": "S1"},
    ]
    state.save(run)

    r = client.get(f"/cutmaster/speakers/{scrubbed_run['run_id']}")
    assert r.status_code == 200
    body = r.json()
    assert [s["speaker_id"] for s in body["speakers"]] == ["S1", "S2"]
    counts = {s["speaker_id"]: s["word_count"] for s in body["speakers"]}
    assert counts == {"S1": 2, "S2": 1}


def test_speakers_endpoint_404_on_unknown_run(client: TestClient):
    r = client.get("/cutmaster/speakers/does-not-exist")
    assert r.status_code == 404


def test_build_plan_round_trips_speaker_labels(
    client,
    monkeypatch,
    scrubbed_run,
):
    """v2-5: UserSettings.speaker_labels must persist through /build-plan
    and be reachable on the saved plan's user_settings."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(
        resolve_mod,
        "_boilerplate",
        lambda: (MagicMock(), MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "_find_timeline_by_name",
        lambda _p, _n: fake_tl,
    )
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "interview",
            "user_settings": {
                "speaker_labels": {"S1": "Host", "S2": "Guest"},
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved = persisted["plan"]["user_settings"]
    assert saved["speaker_labels"] == {"S1": "Host", "S2": "Guest"}


def test_detect_preset(client: TestClient, scrubbed_run, monkeypatch):
    recommendation = PresetRecommendation(
        preset="vlog", confidence=0.85, reasoning="energy + first-person"
    )
    monkeypatch.setattr(routes.auto_detect_mod, "detect_preset", lambda _t: recommendation)

    r = client.post("/cutmaster/detect-preset", json={"run_id": scrubbed_run["run_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == "vlog"
    assert body["confidence"] == 0.85


def test_detect_preset_requires_scrubbed(client: TestClient):
    # Create a run without running analyze — scrubbed is empty.
    run = state.new_run("T1")
    state.save(run)
    r = client.post("/cutmaster/detect-preset", json={"run_id": run["run_id"]})
    assert r.status_code == 400


def test_analyze_themes(client: TestClient, scrubbed_run, monkeypatch):
    fake = StoryAnalysis(
        chapters=[Chapter(start_s=0.0, end_s=2.0, title="Intro")],
        hook_candidates=[
            HookCandidate(start_s=0.0, end_s=0.95, text="Hello world.", engagement_score=0.8)
        ],
        theme_candidates=["greeting", "demonstration"],
    )
    monkeypatch.setattr(routes.themes_mod, "analyze_themes", lambda _t, _p: fake)

    r = client.post(
        "/cutmaster/analyze-themes",
        json={"run_id": scrubbed_run["run_id"], "preset": "vlog"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["chapters"]) == 1
    assert body["theme_candidates"] == ["greeting", "demonstration"]


def test_analyze_themes_rejects_unknown_preset(client: TestClient, scrubbed_run):
    r = client.post(
        "/cutmaster/analyze-themes",
        json={"run_id": scrubbed_run["run_id"], "preset": "not_a_preset"},
    )
    assert r.status_code == 400


def test_build_plan(client: TestClient, scrubbed_run, monkeypatch):
    # Mock Director
    director_plan = DirectorPlan(
        hook_index=0,
        selected_clips=[
            CutSegment(start_s=0.0, end_s=0.95, reason="opening"),
            CutSegment(start_s=1.2, end_s=2.0, reason="payoff"),
        ],
        reasoning="hook → payoff",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *a, **k: director_plan)

    # Mock Marker
    marker_plan = MarkerPlan(
        markers=[
            MarkerSuggestion(at_s=1.2, name="B-Roll: target", note=""),
        ]
    )
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *a, **k: marker_plan)

    # Mock Resolve + source-frame resolver
    fake_project = MagicMock()
    fake_tl = MagicMock()
    fake_tl.GetName.return_value = "Timeline 1"

    def fake_boilerplate():
        return None, fake_project, None

    # The route imports _boilerplate lazily from ..resolve — patch the source module
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(routes, "_find_timeline_by_name", lambda _p, _n: fake_tl, raising=False)
    # pipeline._find_timeline_by_name is what's actually called
    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)

    resolved = [
        ResolvedCutSegment(
            start_s=0.0,
            end_s=0.95,
            reason="opening",
            source_item_id="UID1",
            source_item_name="clip.mov",
            source_in_frame=0,
            source_out_frame=23,
            timeline_start_frame=86400,
            timeline_end_frame=86423,
            speed=1.0,
            speed_ramped=False,
            warnings=[],
        ),
        ResolvedCutSegment(
            start_s=1.2,
            end_s=2.0,
            reason="payoff",
            source_item_id="UID1",
            source_item_name="clip.mov",
            source_in_frame=29,
            source_out_frame=48,
            timeline_start_frame=86429,
            timeline_end_frame=86448,
            speed=1.0,
            speed_ramped=False,
            warnings=[],
        ),
    ]
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: resolved)

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {"target_length_s": 90, "themes": ["greeting"]},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] == "vlog"
    assert len(body["director"]["selected_clips"]) == 2
    assert len(body["markers"]["markers"]) == 1
    assert len(body["resolved_segments"]) == 2

    # Plan persists on the run state
    persisted = state.load(scrubbed_run["run_id"])
    assert persisted["plan"]["preset"] == "vlog"


def test_build_plan_accepts_v2_fields_additively(client, monkeypatch, scrubbed_run):
    """v2-0 adds exclude_categories + custom_focus to UserSettings.
    The route must accept them and round-trip them through persisted state
    without requiring any pipeline wiring yet (that lands in v2-1).
    """
    # Minimal mocks — we only assert the new fields are accepted + persisted.
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="one beat",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        routes.build,
        "suggest_markers",
        lambda *_a, **_k: MarkerPlan(markers=[]),
    )

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "wedding",
            "user_settings": {
                "target_length_s": 120,
                "themes": [],
                "exclude_categories": ["vendor_mentions", "mc_talking"],
                "custom_focus": "emphasise the vows",
            },
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved_settings = persisted["plan"]["user_settings"]
    assert saved_settings["exclude_categories"] == ["vendor_mentions", "mc_talking"]
    assert saved_settings["custom_focus"] == "emphasise the vows"


def test_build_plan_omitted_v2_fields_use_safe_defaults(client, monkeypatch, scrubbed_run):
    """v1 clients don't send exclude_categories / custom_focus. Route must
    accept the old payload shape unchanged (defaults fill in)."""
    plan = DirectorPlan(
        hook_index=0,
        selected_clips=[CutSegment(start_s=0.0, end_s=0.95, reason="hook")],
        reasoning="ok",
    )
    monkeypatch.setattr(routes.build, "build_cut_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(routes.build, "suggest_markers", lambda *_a, **_k: MarkerPlan(markers=[]))

    fake_tl = MagicMock()
    fake_tl.GetSetting.return_value = "24"

    def fake_boilerplate():
        return MagicMock(), MagicMock(), MagicMock()

    import celavii_resolve.cutmaster.core.pipeline as pipeline_mod
    import celavii_resolve.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_boilerplate", fake_boilerplate)
    monkeypatch.setattr(pipeline_mod, "_find_timeline_by_name", lambda _p, _n: fake_tl)
    monkeypatch.setattr(routes.build, "resolve_segments", lambda _tl, _segs: [])

    r = client.post(
        "/cutmaster/build-plan",
        json={
            "run_id": scrubbed_run["run_id"],
            "preset": "vlog",
            "user_settings": {"target_length_s": 60, "themes": []},
        },
    )
    assert r.status_code == 200, r.text

    persisted = state.load(scrubbed_run["run_id"])
    saved_settings = persisted["plan"]["user_settings"]
    assert saved_settings["exclude_categories"] == []
    assert saved_settings["custom_focus"] is None
