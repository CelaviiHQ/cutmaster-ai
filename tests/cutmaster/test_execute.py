"""Unit tests for cutmaster.execute — marker mapping + name collision, no Resolve."""

from celavii_resolve.cutmaster.execute import (
    _map_marker_to_new_timeline,
    _unique_timeline_name,
)


def _piece(start_s, end_s):
    return {"start_s": start_s, "end_s": end_s}


def test_marker_in_first_piece():
    resolved = [_piece(1.71, 5.81), _piece(11.24, 15.34)]
    # Marker @2.46s — offset 0.75s into piece 0, which starts at 0 on new timeline
    assert _map_marker_to_new_timeline(resolved, 2.46) == 2.46 - 1.71


def test_marker_in_second_piece_accumulates_prior_durations():
    resolved = [_piece(1.71, 5.81), _piece(11.24, 15.34)]
    # Piece 0 duration = 4.10. Marker @13.64s offset 2.40s into piece 1.
    # New timeline position = 4.10 + 2.40 = 6.50s
    expected = (5.81 - 1.71) + (13.64 - 11.24)
    actual = _map_marker_to_new_timeline(resolved, 13.64)
    assert abs(actual - expected) < 1e-9


def test_marker_in_gap_returns_none():
    resolved = [_piece(1.71, 5.81), _piece(11.24, 15.34)]
    # 8.0s is between pieces — editor cut it out
    assert _map_marker_to_new_timeline(resolved, 8.0) is None


def test_marker_past_end_returns_none():
    resolved = [_piece(0.0, 5.0)]
    assert _map_marker_to_new_timeline(resolved, 100.0) is None


def test_marker_exactly_on_boundary():
    # Marker lands right at piece end — should map to piece[0] end.
    resolved = [_piece(0.0, 4.0), _piece(10.0, 15.0)]
    # at_s=4.0 is <= piece[0].end_s, so maps to 4.0 on new timeline
    assert _map_marker_to_new_timeline(resolved, 4.0) == 4.0


def test_three_piece_marker_mapping():
    resolved = [_piece(0.0, 2.0), _piece(5.0, 8.0), _piece(12.0, 15.0)]
    # Marker @13.5s is in piece 2. Prior durations: 2.0 + 3.0 = 5.0
    # Offset into piece 2: 1.5. New position: 5.0 + 1.5 = 6.5
    assert _map_marker_to_new_timeline(resolved, 13.5) == 6.5


# --- unique name collision ---


def _fake_project(names):
    class T:
        def __init__(self, n): self.n = n
        def GetName(self): return self.n

    class P:
        def GetTimelineCount(self): return len(names)
        def GetTimelineByIndex(self, i): return T(names[i - 1])

    return P()


def test_unique_name_no_collision():
    assert _unique_timeline_name(_fake_project(["Timeline 1"]), "Vlog_AI_Cut") == "Vlog_AI_Cut"


def test_unique_name_one_collision():
    assert _unique_timeline_name(_fake_project(["Vlog_AI_Cut"]), "Vlog_AI_Cut") == "Vlog_AI_Cut_2"


def test_unique_name_multiple_collisions():
    names = ["Vlog_AI_Cut", "Vlog_AI_Cut_2", "Vlog_AI_Cut_3"]
    assert _unique_timeline_name(_fake_project(names), "Vlog_AI_Cut") == "Vlog_AI_Cut_4"
