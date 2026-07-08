"""
Unit tests for animoflow_blender/geometry.py — pure Python, no Blender.
"""
import math
import os
import sys
import types
import importlib

import pytest

# Load geometry/payload through a stub package so `from . import geometry`
# in payload.py resolves without executing the addon __init__.py (which
# needs bpy).
_pkg_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "animoflow_blender"))
if "animoflow_blender" not in sys.modules:
    _pkg = types.ModuleType("animoflow_blender")
    _pkg.__path__ = [_pkg_dir]
    sys.modules["animoflow_blender"] = _pkg

geometry = importlib.import_module("animoflow_blender.geometry")


class TestBlenderGroundToApi:
    def test_mapping(self):
        assert geometry.blender_ground_to_api(1.5, 2.0) == (1.5, -2.0)


class TestResamplePolyline:
    def test_drops_near_coincident(self):
        pts = [(0, 0), (0.001, 0), (1, 0)]
        out = geometry.resample_polyline(pts, min_spacing=0.01)
        assert out == [(0, 0), (1, 0)]

    def test_keeps_endpoint(self):
        pts = [(0, 0), (1, 0), (1.001, 0)]
        out = geometry.resample_polyline(pts, min_spacing=0.01)
        assert out[-1] == (1.001, 0)

    def test_caps_point_count(self):
        pts = [(i * 0.1, 0.0) for i in range(1000)]
        out = geometry.resample_polyline(pts, max_points=200)
        assert len(out) == 200
        assert out[0] == pts[0]
        assert out[-1] == pts[-1]

    def test_empty(self):
        assert geometry.resample_polyline([]) == []


class TestWaypointFrames:
    def test_basic_conversion(self):
        assert geometry.waypoint_t_frames(3.0, 6.0) == 90

    def test_clamps_to_last_frame(self):
        # t at the very end of a 6 s clip: frame 180 clamps to 179.
        assert geometry.waypoint_t_frames(6.0, 6.0) == 179

    def test_clamps_negative_to_zero(self):
        assert geometry.waypoint_t_frames(-1.0, 6.0) == 0


class TestInflateDuration:
    def test_no_bump_needed(self):
        assert geometry.inflate_duration_for(2.0, 4.0) == 4.0

    def test_bumps_to_next_half_second(self):
        # t=4.2 + 0.5 padding = 4.7 → ceil to 5.0
        assert geometry.inflate_duration_for(4.2, 4.0) == 5.0

    def test_caps_at_max(self):
        assert geometry.inflate_duration_for(12.0, 4.0, max_duration=9.0) == 9.0


class TestPolylineLength:
    def test_l_shape(self):
        assert geometry.polyline_length([(0, 0), (0, 2), (1.5, 2)]) == pytest.approx(3.5)


class TestPaceWarningLines:
    def test_feasible_walk_no_warning(self):
        # 4 m in 4 s = 1.0 m/s — a stroll.
        assert geometry.pace_warning_lines(4.0, 4.0) is None

    def test_at_threshold_no_warning(self):
        assert geometry.pace_warning_lines(8.0, 4.0) is None  # exactly 2.0 m/s

    def test_too_fast_warns_with_suggestions(self):
        # 12 m in 3 s = 4 m/s — faster than a run.
        lines = geometry.pace_warning_lines(12.0, 3.0)
        assert lines is not None
        assert "4.0 m/s" in lines[0]
        # walk: 12/1.4 = 8.57 → 9.0 s; run: 12/3.5 = 3.43 → 3.5 s
        assert "Walk ≥ 9 s" in lines[1] and "Run ≥ 3.5 s" in lines[1]

    def test_lines_stay_short_for_blender_labels(self):
        # Blender N-panel labels truncate at ~30 chars — every line of
        # every plausible message must fit (caught live: a wrapped
        # sentence ellipsized mid-number).
        for length, dur in ((12.0, 3.0), (999.9, 0.5), (7.3, 2.0)):
            lines = geometry.pace_warning_lines(length, dur)
            assert lines is not None
            for row in lines:
                assert len(row) <= 34, f"{row!r} too long ({len(row)})"

    def test_degenerate_inputs(self):
        assert geometry.pace_warning_lines(0.0, 4.0) is None
        assert geometry.pace_warning_lines(4.0, 0.0) is None


class TestWaypointPaceWarningLines:
    def test_relaxed_legs_no_warning(self):
        pts = [(0, 0, 0.0), (1, 0, 1.0), (1, 1, 2.0)]  # 1 m/s legs
        assert geometry.waypoint_pace_warning_lines(pts) is None

    def test_one_fast_leg_warns(self):
        # leg #2 covers 5 m in 1 s = 5 m/s; total path 6 m.
        pts = [(0, 0, 0.0), (1, 0, 1.0), (6, 0, 2.0)]
        lines = geometry.waypoint_pace_warning_lines(pts)
        assert lines is not None
        assert "Leg #2" in lines[0] and "5.0 m/s" in lines[0]
        # total 6 m → walk 6/1.4=4.29→4.5 s; run 6/3.5=1.71→2 s
        assert "Walk ≥ 4.5 s" in lines[2] and "Run ≥ 2 s" in lines[2]
        for row in lines:
            assert len(row) <= 34, f"{row!r} too long ({len(row)})"

    def test_origin_only(self):
        assert geometry.waypoint_pace_warning_lines([(0, 0, 0.0)]) is None

    def test_bad_ordering_skipped(self):
        # dt <= 0 legs are ignored (ordering errors reported elsewhere).
        pts = [(0, 0, 0.0), (1, 0, 2.0), (2, 0, 1.0)]
        assert geometry.waypoint_pace_warning_lines(pts) is None
