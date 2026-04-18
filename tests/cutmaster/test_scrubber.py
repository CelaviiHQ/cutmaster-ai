"""Unit tests for cutmaster.scrubber — deterministic, no network, no Resolve."""

from celavii_resolve.cutmaster.analysis.scrubber import ScrubParams, scrub


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "speaker_id": "S1", "start_time": start, "end_time": end}


def test_removes_standalone_fillers():
    words = [
        _w("So", 0.0, 0.3),
        _w("um", 0.3, 0.5),
        _w("this", 0.5, 0.8),
        _w("is", 0.8, 1.0),
        _w("uh", 1.0, 1.2),
        _w("a", 1.2, 1.4),
        _w("test", 1.4, 1.8),
    ]
    result = scrub(words)
    assert result.counts["filler"] == 2
    kept_text = [w["word"] for w in result.kept]
    assert "um" not in kept_text
    assert "uh" not in kept_text
    assert kept_text == ["So", "this", "is", "a", "test"]


def test_filler_matching_is_case_insensitive_and_punctuation_tolerant():
    words = [
        _w("UM,", 0.0, 0.3),
        _w("Uh!", 0.3, 0.5),
        _w("Word", 0.5, 0.8),
    ]
    result = scrub(words)
    assert result.counts["filler"] == 2
    assert [w["word"] for w in result.kept] == ["Word"]


def test_collapse_restart_prefix():
    # "I was going, I was going to say hello" → keep only second run
    words = [
        _w("I", 0.0, 0.1),
        _w("was", 0.1, 0.3),
        _w("going,", 0.3, 0.6),
        _w("I", 0.8, 0.9),  # repeat starts here (inside 3s window)
        _w("was", 0.9, 1.1),
        _w("going", 1.1, 1.4),
        _w("to", 1.4, 1.55),
        _w("say", 1.55, 1.8),
        _w("hello", 1.8, 2.2),
    ]
    result = scrub(words)
    kept_text = [w["word"] for w in result.kept]
    assert kept_text[:3] == ["I", "was", "going"]  # second occurrence kept
    assert kept_text[-2:] == ["say", "hello"]
    assert result.counts["restart"] == 3


def test_restart_outside_window_not_collapsed():
    # Same phrase repeated 10 seconds later should NOT be treated as a restart.
    words = [
        _w("I", 0.0, 0.1),
        _w("was", 0.1, 0.3),
        _w("going", 0.3, 0.6),
        # 10s gap — out of 3s window
        _w("filler", 0.6, 12.0),
        _w("I", 12.0, 12.1),
        _w("was", 12.1, 12.3),
        _w("going", 12.3, 12.6),
    ]
    params = ScrubParams(remove_fillers=False)  # keep 'filler' to preserve spacing
    result = scrub(words, params)
    # No restart collapsing should fire
    assert result.counts["restart"] == 0


def test_disable_all_rules_keeps_everything():
    words = [
        _w("um", 0.0, 0.2),
        _w("and", 0.2, 0.4),
        _w("um", 0.4, 0.6),
    ]
    params = ScrubParams(remove_fillers=False, remove_dead_air=False, collapse_restarts=False)
    result = scrub(words, params)
    assert result.kept_count == 3
    assert result.counts == {"filler": 0, "dead_air": 0, "restart": 0}


def test_empty_input():
    result = scrub([])
    assert result.kept == []
    assert result.kept_count == 0
    assert result.original_count == 0


def test_result_totals_consistent():
    words = [
        _w(x, i * 0.2, (i + 1) * 0.2) for i, x in enumerate(["hi", "um", "there", "uh", "friend"])
    ]
    result = scrub(words)
    assert len(result.kept) + len(result.removed) == result.original_count
    assert result.kept_count == len(result.kept)
