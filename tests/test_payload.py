"""
Unit tests for animoflow_blender/payload.py `build_payload` — pure Python,
no Blender. `collect_inputs` (bpy-side) is covered by the headless harness.
"""
import os
import sys
import types
import importlib

import pytest

_pkg_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "animoflow_blender"))
if "animoflow_blender" not in sys.modules:
    _pkg = types.ModuleType("animoflow_blender")
    _pkg.__path__ = [_pkg_dir]
    sys.modules["animoflow_blender"] = _pkg

payload = importlib.import_module("animoflow_blender.payload")
PayloadError = payload.PayloadError

# Minimal /v1/tasks catalog mirroring the live server (duration 2–9 s).
TASKS_DATA = [
    {"id": t, "models": [
        {"type": m, "params_schema": {"duration": {"min": 2.0, "max": 9.0}}}
        for m in models
    ]}
    for t, models in [
        ("text", ["mdm", "kimodo", "momask"]),
        ("trajectory", ["priormdm", "kimodo"]),
        ("timeline", ["priormdm"]),
        ("waypoint", ["kimodo"]),
    ]
]


def _spec(task="text", **over):
    base = {
        "task": task,
        "prompt": "a person walks forward",
        "model": {"text": "mdm", "trajectory": "priormdm",
                  "timeline": "priormdm", "waypoint": "kimodo"}[task],
        "character": "Y_bot",
        "duration": 4.0,
        "seed": 42,
        "output_fps": 30,
        "cfg": 7.5,
        "keyframe_builder": False,
        "keyframe_builder_error_degrees": 3.0,
        "tasks_data": TASKS_DATA,
    }
    base.update(over)
    return base


class TestText:
    def test_shape(self):
        body = payload.build_payload(_spec())
        assert body["input"] == {"type": "text", "prompt": "a person walks forward"}
        assert body["model"] == "mdm"
        assert body["character"] == "Y_bot"
        assert body["duration"] == 4.0
        assert body["seed"] == 42
        assert body["output_fps"] == 30
        assert body["cfg"] == 7.5  # top-level cfg is accepted in simple mode
        assert body["compress_output"] is False
        # extra="forbid" on the server — time_steps is display-only, never sent
        assert "time_steps" not in body

    def test_output_fps_carried(self):
        body = payload.build_payload(_spec(output_fps=60))
        assert body["output_fps"] == 60

    def test_cfg_carried(self):
        body = payload.build_payload(_spec(cfg=12.5))
        assert body["cfg"] == 12.5  # server clamps per-model

    def test_empty_prompt_raises(self):
        with pytest.raises(PayloadError, match="empty"):
            payload.build_payload(_spec(prompt="   "))

    def test_short_prompt_raises(self):
        with pytest.raises(PayloadError, match="short"):
            payload.build_payload(_spec(prompt="ab"))

    def test_long_prompt_raises(self):
        with pytest.raises(PayloadError, match="long"):
            payload.build_payload(_spec(prompt="x" * 501))

    def test_duration_clamped_to_model_window(self):
        body = payload.build_payload(_spec(duration=30.0))
        assert body["duration"] == 9.0
        body = payload.build_payload(_spec(duration=1.0))
        assert body["duration"] == 2.0

    def test_missing_character_raises(self):
        with pytest.raises(PayloadError, match="character"):
            payload.build_payload(_spec(character="__loading__"))


class TestTrajectory:
    def _pts(self):
        # Blender ground coords: forward 2 m (+y), then 1.5 m to the +x.
        return [(0.0, 0.0), (0.0, 2.0), (1.5, 2.0)]

    def test_shape(self):
        body = payload.build_payload(
            _spec("trajectory", curve_points_blender=self._pts()))
        inp = body["input"]
        assert inp["type"] == "trajectory"
        assert inp["accel_frac"] == 0.25 and inp["decel_frac"] == 0.25
        assert inp["prompt"] == "a person walks forward"
        curve = inp["curve_2d"]
        assert len(curve) == 3
        # RAW curve, blender (x,y) → API [x,−y] only — no client-side
        # canonicalization (the server owns it and restores the motion
        # onto the drawn curve).
        assert curve[0] == [0.0, 0.0]
        assert curve[1] == [pytest.approx(0.0), pytest.approx(-2.0)]
        assert curve[2] == [pytest.approx(1.5), pytest.approx(-2.0)]

    def test_blank_prompt_omitted(self):
        body = payload.build_payload(
            _spec("trajectory", prompt="", curve_points_blender=self._pts()))
        assert "prompt" not in body["input"]

    def test_too_few_points_raises(self):
        with pytest.raises(PayloadError, match="2 points"):
            payload.build_payload(
                _spec("trajectory", curve_points_blender=[(0.0, 0.0)]))

    def test_collapsed_curve_raises(self):
        pts = [(0.0, 0.0), (0.001, 0.0)]
        with pytest.raises(PayloadError, match="too short"):
            payload.build_payload(_spec("trajectory", curve_points_blender=pts))


class TestWaypoints:
    def _wps(self):
        # Row #1 is the START pin (t=0, placed anywhere); later rows timed.
        return [
            {"bx": 1.0, "by": 0.5, "t_sec": 0.0},
            {"bx": 1.0, "by": 1.5, "t_sec": 2.0},
            {"bx": 2.0, "by": 0.0, "t_sec": 4.0},
        ]

    def test_shape(self):
        body = payload.build_payload(_spec("waypoint", waypoints=self._wps()))
        wps = body["input"]["waypoints"]
        # Pins go RAW (no implicit origin, no client canonicalization —
        # the server translates+rotates and restores).
        assert len(wps) == 3
        assert wps[0] == {"x": 1.0, "y": 0.0, "z": -0.5, "t": 0}
        assert wps[1]["t"] == 60 and wps[2]["t"] == 120
        # blender (1,1.5) → api (1,-1.5)
        assert wps[1]["x"] == 1.0 and wps[1]["z"] == -1.5
        for w in wps:
            assert w["y"] == 0.0
        ts = [w["t"] for w in wps]
        assert ts == sorted(ts) and len(set(ts)) == len(ts)

    def test_single_pin_raises(self):
        with pytest.raises(PayloadError, match="at least 2"):
            payload.build_payload(
                _spec("waypoint", waypoints=self._wps()[:1]))

    def test_empty_raises(self):
        with pytest.raises(PayloadError, match="at least 2"):
            payload.build_payload(_spec("waypoint", waypoints=[]))

    def test_too_many_raises(self):
        wps = [{"bx": i, "by": 0, "t_sec": float(i)} for i in range(7)]
        with pytest.raises(PayloadError, match="Too many"):
            payload.build_payload(_spec("waypoint", waypoints=wps))

    def test_non_ascending_times_raise(self):
        wps = self._wps()
        wps[2]["t_sec"] = 1.0  # before pin #2
        with pytest.raises(PayloadError, match="strictly increase"):
            payload.build_payload(_spec("waypoint", waypoints=wps))

    def test_zero_time_after_start_raises(self):
        wps = self._wps()
        wps[1]["t_sec"] = 0.0
        with pytest.raises(PayloadError, match="greater than 0"):
            payload.build_payload(_spec("waypoint", waypoints=wps))

    def test_duration_auto_inflates(self):
        wps = self._wps()
        wps[2]["t_sec"] = 6.2
        body = payload.build_payload(_spec("waypoint", waypoints=wps, duration=4.0))
        # 6.2 + 0.5 padding → ceil-to-half = 6.7 → 7.0
        assert body["duration"] == 7.0

    def test_unfittable_time_raises(self):
        wps = self._wps()
        wps[2]["t_sec"] = 8.8  # 8.8 + 0.5 > 9.0 cap
        with pytest.raises(PayloadError, match="doesn't fit"):
            payload.build_payload(_spec("waypoint", waypoints=wps))


class TestTimeline:
    def _segs(self):
        return [
            {"prompt": "a person walks forward", "duration": 3.0},
            {"prompt": "a person waves", "duration": 3.0},
        ]

    def test_shape(self):
        body = payload.build_payload(_spec("timeline", segments=self._segs()))
        assert body["input"]["type"] == "timeline"
        assert body["input"]["segments"] == self._segs()
        assert body["input"]["seed"] == 42
        assert body["input"]["cfg"] == 7.5   # cfg lives inside TimelineInput
        assert "cfg" not in body               # never top-level for timeline
        assert body["character"] == "Y_bot"
        assert body["output_fps"] == 30
        # Web parity: no top-level model/duration for timeline.
        assert "model" not in body
        assert "duration" not in body

    def test_random_seed_omitted(self):
        body = payload.build_payload(_spec("timeline", segments=self._segs(), seed=-1))
        assert "seed" not in body["input"]

    def test_one_segment_raises(self):
        with pytest.raises(PayloadError, match="at least 2"):
            payload.build_payload(_spec("timeline", segments=self._segs()[:1]))

    def test_segment_duration_out_of_range_raises(self):
        segs = self._segs()
        segs[0]["duration"] = 8.5
        with pytest.raises(PayloadError, match="out of range"):
            payload.build_payload(_spec("timeline", segments=segs))

    def test_total_cap_raises(self):
        segs = [{"prompt": "a person walks forward", "duration": 7.0}] * 3
        with pytest.raises(PayloadError, match="cap"):
            payload.build_payload(_spec("timeline", segments=segs))

    def test_segment_prompt_validated(self):
        segs = self._segs()
        segs[1]["prompt"] = ""
        with pytest.raises(PayloadError, match="Segment #2"):
            payload.build_payload(_spec("timeline", segments=segs))


class TestDispatch:
    def test_unknown_task_raises(self):
        with pytest.raises(PayloadError, match="Unsupported"):
            payload.build_payload(_spec() | {"task": "keyframes"})
