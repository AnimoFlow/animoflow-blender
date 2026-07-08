"""
Headless suite: registration + property + payload-validation checks.
No network, no GPU. Each test is (name, fn(ctx)) via collect(ctx).
"""
import importlib.util
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
if "headless_helpers" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "headless_helpers", os.path.join(HERE, "helpers.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["headless_helpers"] = _mod
    _spec.loader.exec_module(_mod)
helpers = sys.modules["headless_helpers"]


def test_operators_registered(ctx):
    for op in ("generate", "connect", "refresh_server_data"):
        assert hasattr(bpy.ops.animoflow, op), f"bpy.ops.animoflow.{op} missing"


def test_scene_props_exist(ctx):
    p = helpers.props()
    for name in ("task", "character", "prompt", "duration", "output_fps",
                 "seed", "model", "keyframe_builder",
                 "keyframe_builder_error_degrees", "trajectory_curve",
                 "waypoints", "waypoints_index", "segments", "segments_index",
                 "cfg"):
        assert hasattr(p, name), f"scene prop '{name}' missing"


def test_panel_registered(ctx):
    assert hasattr(bpy.types, "ANIMOFLOW_PT_main") or \
        any(getattr(c, "bl_idname", "") == "ANIMOFLOW_PT_main"
            for c in bpy.types.Panel.__subclasses__()), "N-panel not registered"


def test_enum_callbacks_safe_before_connect(ctx):
    # Enum item callbacks must never raise (or hit the network) pre-connect.
    panel = helpers.addon_sub("panel")
    assert panel._task_enum_items(None, None)
    assert panel._model_enum_items(None, None)
    assert panel._character_enum_items(None, None)


def test_payload_validation_raises_loudly(ctx):
    payload = helpers.addon_sub("payload")
    PE = payload.PayloadError
    base = {
        "task": "text", "prompt": "a person walks", "model": "mdm",
        "character": "Y_bot", "duration": 4.0, "seed": -1,
        "keyframe_builder": False, "keyframe_builder_error_degrees": 3.0,
        "tasks_data": [],
    }

    def raises(spec_over, needle):
        try:
            payload.build_payload({**base, **spec_over})
        except PE as exc:
            assert needle in str(exc).lower(), f"{needle!r} not in {exc}"
            return
        raise AssertionError(f"PayloadError not raised for {spec_over}")

    raises({"prompt": ""}, "empty")
    raises({"prompt": "ab"}, "short")
    raises({"character": "__loading__"}, "character")
    raises({"task": "trajectory", "curve_points_blender": []}, "trajectory")
    raises({"task": "waypoint", "waypoints": []}, "waypoint")
    raises({"task": "timeline", "segments": []}, "at least 2")
    raises({"task": "keyframes"}, "unsupported")


def test_generate_without_connect_reports_error(ctx):
    # The Generate operator must refuse cleanly (no thread spawned, no
    # crash) pre-connect while the task enum still shows the placeholder.
    # bpy.ops re-raises operator ERROR reports as RuntimeError — both a
    # clean CANCELLED and that RuntimeError are acceptable; an unhandled
    # traceback of any other kind is not.
    try:
        result = bpy.ops.animoflow.generate()
        assert result == {"CANCELLED"}, f"expected CANCELLED, got {result}"
    except RuntimeError as exc:
        assert "connect" in str(exc).lower(), f"unexpected error: {exc}"
    state = helpers.addon_sub("operators").get_state()
    assert not state["running"], "worker thread must not start pre-connect"


def test_waypoint_marker_locked_to_floor(ctx):
    operators = helpers.addon_sub("operators")
    p = helpers.props()
    item, _ = operators.add_waypoint(bpy.context, (1.0, 2.0, 0.0))
    try:
        assert item.empty.location[2] == 0.0, "marker not on the floor"
        assert item.empty.lock_location[2], "marker Z channel not locked"
        # First row is the START pin: t locked to 0.
        assert item.t_sec == 0.0, "start pin t_sec must be 0"
        item2, _ = operators.add_waypoint(bpy.context, (2.0, 2.0, 0.0))
        assert item2.t_sec > 0.0, "second pin must get a positive default time"
    finally:
        while len(p.waypoints):
            operators.remove_waypoint(bpy.context, len(p.waypoints) - 1)


def test_segment_add_uses_example_prompts(ctx):
    """Web parity: the + operator seeds new segments from the example
    bank (never repeating the previous segment's prompt) with random
    3.0–5.0 s durations in 0.5 s steps."""
    operators = helpers.addon_sub("operators")
    p = helpers.props()
    p.segments.clear()
    try:
        # 4 segments: 4 × max-5 s stays under the 20 s cap, so no segment
        # gets cap-clamped below the random 3–5 s window (a 5th could).
        for _ in range(4):
            operators.add_segment(bpy.context)
        prompts = [s.prompt for s in p.segments]
        bank = set(operators.TIMELINE_EXAMPLE_PROMPTS)
        assert all(pr in bank for pr in prompts), f"off-bank prompt: {prompts}"
        assert all(a != b for a, b in zip(prompts, prompts[1:])), \
            f"consecutive repeat: {prompts}"
        for s in p.segments:
            assert 3.0 <= s.duration <= 5.0, s.duration
            assert abs(s.duration * 2 - round(s.duration * 2)) < 1e-6, s.duration
    finally:
        p.segments.clear()


def collect(ctx):
    return [
        ("operators_registered", test_operators_registered),
        ("waypoint_marker_floor_locked", test_waypoint_marker_locked_to_floor),
        ("segment_add_example_prompts", test_segment_add_uses_example_prompts),
        ("scene_props_exist", test_scene_props_exist),
        ("panel_registered", test_panel_registered),
        ("enum_callbacks_safe", test_enum_callbacks_safe_before_connect),
        ("payload_validation_loud", test_payload_validation_raises_loudly),
        ("generate_preconnect_cancelled", test_generate_without_connect_reports_error),
    ]
