"""
Headless suite: real generations against the HF Space (burns ZeroGPU
quota — the token's own allocation). Matrix-driven:

    smoke: text/mdm only (1 generation, ~10 s GPU)
    full:  every task × model combo the web UI exposes (~7 generations)

The matrix below is the single place to extend when new task types
land in the addon.
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

SEED = 42
DURATION_S = 4.0

# (task, model) — extended per phase as tasks land in the addon.
MATRIX_SMOKE = [("text", "mdm")]
MATRIX_FULL = [
    ("text", "mdm"),
    ("text-kfb", "mdm"),        # keyframe builder on → sparse curves
    ("text", "momask"),
    ("text", "kimodo"),
    ("trajectory", "priormdm"),
    ("trajectory", "kimodo"),   # wip/beta server-side
    ("waypoint", "kimodo"),     # wip/beta server-side
    ("timeline", "priormdm"),
]


def _set_common_props(task, model):
    panel = helpers.addon_sub("panel")
    p = helpers.props()
    p.task = task           # triggers _on_task_change → model enum rebuild
    if model:
        p.model = model
    p.character = "Y_bot"
    p.prompt = "a person walks forward and waves"
    p.duration = DURATION_S
    p.seed = SEED
    p.output_fps = 30


def _make_text_test(task, model):
    # mdm runs at 60 fps to prove the panel's Output FPS reaches the
    # server resample (keyframe-density assert in run_generation);
    # the other models stay on the default 30.
    fps = 60 if model == "mdm" else 30

    def run(ctx):
        before = helpers.snapshot_objects()
        try:
            _set_common_props(task, model)
            helpers.props().output_fps = fps
            body, result, arm = helpers.run_generation(ctx, DURATION_S, fps=fps)
            assert body["input"]["type"] == "text"
            assert body["model"] == model
            assert body["output_fps"] == fps
        finally:
            helpers.props().output_fps = 30
            helpers.cleanup_generated(before)
    return run


def _make_trajectory_test(task, model):
    """Off-origin L-curve on the Blender floor: (1, 0.5) → (3, 0.5) →
    (3, 2). The payload carries the RAW curve; the server canonicalizes
    for the model and restores the output onto the curve (traj_restore).
    A correct end-to-end pipeline therefore imports motion that STARTS
    near (1, 0.5) and ENDS near (3, 2) — this asserts the restore's
    translation, its rotation, AND chirality (a mirrored pipeline would
    end near (3, -1) instead)."""
    def run(ctx):
        before = helpers.snapshot_objects()
        try:
            _set_common_props(task, model)
            p = helpers.props()
            p.prompt = "a person walks"
            p.duration = 6.0  # 3.5 m of travel needs more than 4 s
            curve_ob = helpers.make_l_curve()
            p.trajectory_curve = curve_ob

            def check(body):
                inp = body["input"]
                assert inp["type"] == "trajectory", inp
                assert inp["accel_frac"] == 0.25 and inp["decel_frac"] == 0.25
                curve = inp["curve_2d"]
                # RAW blender→API mapping, no canonicalization:
                # blender (1,0.5) → api (1,-0.5) etc.
                assert abs(curve[0][0] - 1.0) < 1e-4 and \
                    abs(curve[0][1] - (-0.5)) < 1e-4, f"bad start {curve[0]}"
                assert abs(curve[-1][0] - 3.0) < 1e-4 and \
                    abs(curve[-1][1] - (-2.0)) < 1e-4, f"bad end {curve[-1]}"

            body, result, arm = helpers.run_generation(ctx, 6.0,
                                                       check_payload=check)

            fr = arm.animation_data.action.frame_range
            start = helpers.root_world_pos_at(arm, fr[0])
            end = helpers.root_world_pos_at(arm, fr[1])
            print(f"[headless]   root start: ({start.x:.2f}, {start.y:.2f})"
                  f"  end: ({end.x:.2f}, {end.y:.2f})")
            sx, sy = helpers.L_CURVE_START
            ex, ey = helpers.L_CURVE_END
            d_start = ((start.x - sx) ** 2 + (start.y - sy) ** 2) ** 0.5
            d_end = ((end.x - ex) ** 2 + (end.y - ey) ** 2) ** 0.5
            assert d_start < 0.8, (
                f"motion starts {d_start:.2f} m from the drawn curve start "
                f"({start.x:.2f},{start.y:.2f}) vs ({sx},{sy}) — traj_restore "
                "translation broken?"
            )
            assert d_end < 1.2, (
                f"motion ends {d_end:.2f} m from the drawn curve end "
                f"({end.x:.2f},{end.y:.2f}) vs ({ex},{ey}) — traj_restore "
                "rotation/chirality broken?"
            )
            # Chirality guard: mirrored output would end near (3, -1).
            assert end.y > 0.2, (
                f"CHIRALITY: left turn came back as y={end.y:.2f} — "
                "mapping/restore mirrored"
            )
        finally:
            helpers.cleanup_generated(before)
            helpers.props().trajectory_curve = None
    return run


def _make_waypoint_test(task, model):
    """Start-anywhere pins via the real add_waypoint path: the START pin
    at blender (1, 0.5) [t=0, first row], then (3, 0.5) at t=2 s (first
    leg along +x — exercises the server's rotation canonicalization) and
    (3, 2) at t=4 s (a turn — chirality). The server canonicalizes the
    set (translate+rotate, like trajectories) and restores the output
    onto the pins, so the imported motion must START at the start pin
    and pass each pin near its frame. Generous tolerances (soft
    constraint model)."""
    def run(ctx):
        operators = helpers.addon_sub("operators")
        before = helpers.snapshot_objects()
        p = helpers.props()
        try:
            _set_common_props(task, model)
            p.prompt = "a person walks"
            p.duration = 4.0

            operators.add_waypoint(bpy.context, (1.0, 0.5, 0.0))   # start
            item2, _ = operators.add_waypoint(bpy.context, (3.0, 0.5, 0.0))
            item2.t_sec = 2.0
            item3, _ = operators.add_waypoint(bpy.context, (3.0, 2.0, 0.0))
            item3.t_sec = 4.0
            p.duration = 4.0  # reset add-time inflation; builder re-inflates

            def check(body):
                wps = body["input"]["waypoints"]
                assert len(wps) == 3, wps
                # RAW pins, blender (x,y) → api (x,−y); start first at t=0.
                assert wps[0] == {"x": 1.0, "y": 0.0, "z": -0.5, "t": 0}, wps
                assert (wps[1]["x"], wps[1]["z"]) == (3.0, -0.5), wps
                assert (wps[2]["x"], wps[2]["z"]) == (3.0, -2.0), wps
                assert [w["t"] for w in wps] == [0, 60, 120], wps
                assert body["duration"] == 4.5, body["duration"]

            # build_payload inflates 4.0 → 4.5 (t_last 4.0 + 0.5 padding).
            body, result, arm = helpers.run_generation(ctx, 4.5, tol=0.2,
                                                       check_payload=check)

            fr = arm.animation_data.action.frame_range
            start = helpers.root_world_pos_at(arm, fr[0])
            at2 = helpers.root_world_pos_at(arm, fr[0] + 60)
            at3 = helpers.root_world_pos_at(arm, fr[0] + 120)
            print(f"[headless]   root start: ({start.x:.2f}, {start.y:.2f})"
                  f"  @pin2: ({at2.x:.2f}, {at2.y:.2f})"
                  f"  @pin3: ({at3.x:.2f}, {at3.y:.2f})")
            d0 = ((start.x - 1.0) ** 2 + (start.y - 0.5) ** 2) ** 0.5
            d2 = ((at2.x - 3.0) ** 2 + (at2.y - 0.5) ** 2) ** 0.5
            d3 = ((at3.x - 3.0) ** 2 + (at3.y - 2.0) ** 2) ** 0.5
            assert d0 < 0.7, f"start {d0:.2f} m off the start pin — restore broken?"
            assert d2 < 1.2, f"pin2 miss by {d2:.2f} m — rotation canonicalization broken?"
            assert d3 < 1.2, f"pin3 miss by {d3:.2f} m — chirality/restore broken?"
        finally:
            # Clear waypoint rows through the real path (also deletes empties).
            while len(p.waypoints):
                operators.remove_waypoint(bpy.context, len(p.waypoints) - 1)
            helpers.cleanup_generated(before)
    return run


def _make_timeline_test(task, model):
    def run(ctx):
        operators = helpers.addon_sub("operators")
        before = helpers.snapshot_objects()
        p = helpers.props()
        try:
            _set_common_props(task, None)
            p.segments.clear()
            operators.add_segment(bpy.context, "a person walks forward", 3.0)
            operators.add_segment(bpy.context, "a person waves with the right hand", 3.0)

            body, result, arm = helpers.run_generation(ctx, 6.0, tol=0.25)
            assert body["input"]["type"] == "timeline"
            assert len(body["input"]["segments"]) == 2
            assert body["input"]["seed"] == SEED
            # Web parity: no top-level model/duration for timeline.
            assert "model" not in body and "duration" not in body
        finally:
            p.segments.clear()
            helpers.cleanup_generated(before)
    return run


def _make_text_kfb_test(task, model):
    """Keyframe builder ON: the server's full-skeleton RDP must return
    sparse animator-editable curves — key count well under dense while
    the clip length is preserved."""
    def run(ctx):
        p = helpers.props()
        before = helpers.snapshot_objects()
        try:
            _set_common_props("text", model)
            p.keyframe_builder = True
            p.keyframe_builder_error_degrees = 3.0

            def check(body):
                assert body["keyframe_builder"] is True, body
                assert body["keyframe_builder_error_degrees"] == 3.0, body

            body, result, arm = helpers.run_generation(
                ctx, DURATION_S, kfb=True, check_payload=check)
        finally:
            p.keyframe_builder = False
            helpers.cleanup_generated(before)
    return run


_BUILDERS = {
    "text": _make_text_test,
    "text-kfb": _make_text_kfb_test,
    "trajectory": _make_trajectory_test,
    "waypoint": _make_waypoint_test,
    "timeline": _make_timeline_test,
}


def collect(ctx):
    matrix = MATRIX_SMOKE if ctx["matrix"] == "smoke" else MATRIX_FULL
    # Optional filter for phase-by-phase iteration without burning the
    # whole matrix, e.g. ANIMOFLOW_ONLY="trajectory" or "trajectory:kimodo".
    only = os.environ.get("ANIMOFLOW_ONLY", "").strip()
    if only:
        wanted = {tuple((c.split(":") + [None])[:2]) for c in only.split(",")}
        matrix = [
            (t, m) for t, m in matrix
            if (t, m) in wanted or (t, None) in wanted
        ]
        if not matrix:
            raise RuntimeError(f"ANIMOFLOW_ONLY={only!r} matched no matrix rows")
    tests = []
    for task, model in matrix:
        builder = _BUILDERS.get(task)
        if builder is None:
            raise RuntimeError(
                f"matrix row ({task}, {model}) has no test builder — "
                "extend _BUILDERS when the task lands in the addon"
            )
        tests.append((f"gen_{task}_{model or 'auto'}", builder(task, model)))
    return tests
