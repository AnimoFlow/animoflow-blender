"""
Shared helpers for the headless suites — scene builders and assertions.
Imported by the suites via run_headless's _load_suite (plain files, not a
package), so suites load this with importlib too.
"""
import os

import bpy


def addon_module(name="animoflow_blender"):
    """The live addon package as imported by Blender."""
    import importlib
    return importlib.import_module(name)


def addon_sub(sub, name="animoflow_blender"):
    import importlib
    return importlib.import_module(f"{name}.{sub}")


def props():
    return bpy.context.scene.animoflow_props


def snapshot_objects():
    return set(bpy.data.objects.keys())


def new_armatures(before):
    return [
        ob for ob in bpy.data.objects
        if ob.name not in before and ob.type == "ARMATURE"
    ]


def assert_armature_with_action(before, expected_duration_s, fps=30, tol=0.15):
    """Assert exactly one new armature landed with an action of roughly
    the expected length. Returns the armature."""
    arms = new_armatures(before)
    assert len(arms) == 1, f"expected 1 new armature, found {len(arms)}"
    arm = arms[0]
    assert arm.animation_data and arm.animation_data.action, \
        f"armature {arm.name} has no action"
    # Viewport usability: no giant icosphere bone shapes (the glTF
    # importer's BLENDER heuristic buries the character under them).
    shaped = [p.name for p in arm.pose.bones if p.custom_shape]
    assert not shaped, f"pose bones carry custom shapes: {shaped[:3]}…"
    fr = arm.animation_data.action.frame_range
    n_frames = fr[1] - fr[0]
    expected = expected_duration_s * fps
    assert abs(n_frames - expected) <= expected * tol, (
        f"action is {n_frames:.0f} frames, expected ~{expected:.0f} "
        f"(±{tol * 100:.0f}%)"
    )
    return arm


def action_keyframe_count(arm):
    """Max keyframe count across the action's fcurves — a proxy for the
    baked sampling density. glTF stores keyframe times in SECONDS, so
    the frame RANGE after import scales with scene fps no matter what
    the server baked; the keyframe COUNT is the honest signal that
    output_fps actually changed the resample."""
    act = arm.animation_data.action

    def _iter(action):
        legacy = getattr(action, "fcurves", None)
        if legacy is not None:
            yield from legacy
            return
        for layer in action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    yield from cb.fcurves

    return max((len(fc.keyframe_points) for fc in _iter(act)), default=0)


def root_world_pos_at(arm, frame):
    """World position of the armature's root bone at `frame`."""
    bpy.context.scene.frame_set(int(frame))
    root = arm.pose.bones[0]
    world = arm.matrix_world @ root.matrix
    return world.translation.copy()


def cleanup_generated(before):
    """Delete everything imported since `before` (objects + orphaned data)."""
    doomed = [ob for ob in bpy.data.objects if ob.name not in before]
    if doomed:
        bpy.data.batch_remove(doomed)
    for coll in (bpy.data.actions, bpy.data.armatures, bpy.data.meshes,
                 bpy.data.materials, bpy.data.images):
        orphans = [d for d in coll if d.users == 0]
        if orphans:
            bpy.data.batch_remove(orphans)


def make_l_curve(name="AF_Test_L_Curve"):
    """Poly curve on the ground, deliberately OFF the origin and rotated:
    (1, 0.5) → (3, 0.5) → (3, 2) in Blender ground coords — 2 m along
    +X, then a 1.5 m LEFT turn to +Y. Exercises the server-side
    trajectory restore (translation AND rotation are non-identity) plus
    chirality: the imported motion must lie on this exact curve."""
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    pts = [(1.0, 0.5, 0.0), (3.0, 0.5, 0.0), (3.0, 2.0, 0.0)]
    spline.points.add(len(pts) - 1)
    for p, (x, y, z) in zip(spline.points, pts):
        p.co = (x, y, z, 1.0)
    ob = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(ob)
    return ob


L_CURVE_START = (1.0, 0.5)
L_CURVE_END = (3.0, 2.0)


def run_generation(ctx, expected_duration_s, fps=30, tol=0.15,
                   check_payload=None, kfb=False):
    """Shared end-to-end path: collect → build → [check_payload(body)]
    → generate_cloud → import → assert. Payload assertions run BEFORE
    the GPU call so a payload bug never burns quota. Returns
    (payload, result, armature)."""
    payload_mod = addon_sub("payload")
    api = addon_sub("api")
    operators = addon_sub("operators")

    spec = payload_mod.collect_inputs(bpy.context)
    body = payload_mod.build_payload(spec)
    if check_payload is not None:
        check_payload(body)

    def _progress(tuple_data):
        if isinstance(tuple_data, list) and len(tuple_data) >= 2 and \
                isinstance(tuple_data[1], str) and tuple_data[1].strip():
            print(f"[headless]   stage: {tuple_data[1]}")

    before = snapshot_objects()
    result = api.generate_cloud(ctx["space_url"], ctx["token"], body,
                                on_progress=_progress)
    size = os.path.getsize(result["path"])
    assert size > 100_000, f"output GLB suspiciously small: {size} bytes"
    print(f"[headless]   GLB: {size / 1024:.0f} KB at {result['path']}")

    operators.import_output(bpy.context.scene, result["path"], fps)
    arm = assert_armature_with_action(before, expected_duration_s, fps=fps, tol=tol)
    # Sampling-density check (keyframe COUNT, not frame range — see
    # action_keyframe_count docstring). With the keyframe builder OFF
    # the server bakes dense at output_fps; with it ON the full-skeleton
    # RDP must have cut the density substantially while the clip length
    # (asserted above) stays intact.
    n_keys = action_keyframe_count(arm)
    expected_dense = expected_duration_s * fps
    if kfb:
        assert 2 <= n_keys <= expected_dense * 0.55, (
            f"action has {n_keys} keys — expected well under the dense "
            f"{expected_dense:.0f} with keyframe_builder on (RDP ignored?)"
        )
        print(f"[headless]   keyframe density (KFB): {n_keys} keys vs "
              f"{expected_dense:.0f} dense")
    else:
        assert abs(n_keys - expected_dense) <= expected_dense * 0.25, (
            f"action has {n_keys} keys, expected ~{expected_dense:.0f} "
            f"(±25%) for output_fps={fps} — server resample ignored?"
        )
        print(f"[headless]   keyframe density: {n_keys} keys @ {fps} fps")
    return body, result, arm
