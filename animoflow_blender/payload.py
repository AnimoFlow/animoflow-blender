"""
Payload construction for all four AnimoFlow tasks.

Two layers:

* `collect_inputs(context)` — bpy-side. Runs on the MAIN thread inside
  the Generate operator (never in the worker). Reads scene properties,
  samples the trajectory curve, reads waypoint empties, and returns a
  plain "spec" dict with no bpy references — safe to hand to a thread.

* `build_payload(spec)` — pure Python, no bpy. Turns a spec into the
  exact `GenerateRequest` JSON body the server expects. Raises
  `PayloadError` with an actionable message on every invalid state.
  NO SILENT FALLBACK — invalid input never degrades to a default.

Contract notes (animoflow-api api/main.py, verified 2026-07-02):
* `GenerateRequest` and all input models are `extra="forbid"` — never
  send `cfg` / `time_steps` (those exist only in `params_schema` for
  display; `cfg` is legal only inside `TimelineInput`).
* Timeline payloads omit top-level `model` and `duration` (web parity —
  the server routes timeline to priorMDM and derives length from the
  segments).
* `compress_output: False` always — Blender's glTF importer rejects
  EXT_meshopt_compression.
"""
from . import geometry


class PayloadError(RuntimeError):
    """User-actionable payload validation failure."""


PROMPT_MIN = 3
PROMPT_MAX = 500


def _clean_prompt(raw, required, what="Prompt"):
    """Validate a prompt against the server's 3–500 char window."""
    prompt = (raw or "").strip()
    if not prompt:
        if required:
            raise PayloadError(f"{what} is empty — describe the motion first.")
        return None
    if len(prompt) < PROMPT_MIN:
        raise PayloadError(f"{what} is too short (min {PROMPT_MIN} characters).")
    if len(prompt) > PROMPT_MAX:
        raise PayloadError(f"{what} is too long (max {PROMPT_MAX} characters).")
    return prompt


def _duration_range_for_model(tasks_data, task_id, model):
    """Look up the model's duration window from the cached /v1/tasks
    catalog. Returns (min, max) or None when the catalog has no entry."""
    for t in tasks_data or []:
        if t.get("id") != task_id:
            continue
        for m in t.get("models", []):
            if m.get("type") == model:
                schema = (m.get("params_schema") or {}).get("duration") or {}
                lo, hi = schema.get("min"), schema.get("max")
                if lo is not None and hi is not None:
                    return float(lo), float(hi)
    return None


# ---------------------------------------------------------------------------
# bpy-side input collection (main thread only)
# ---------------------------------------------------------------------------

def _sample_curve_points(ob, depsgraph):
    """Sample a curve object into ordered Blender ground-plane (x, y)
    world coordinates. Explicit spline evaluation for BEZIER/POLY;
    evaluated-mesh fallback for other types (NURBS)."""
    from mathutils import Vector
    from mathutils.geometry import interpolate_bezier

    curve = ob.data
    splines = curve.splines
    if len(splines) == 0:
        raise PayloadError(f"Trajectory curve '{ob.name}' has no splines — draw or add points first.")
    if len(splines) > 1:
        raise PayloadError(
            f"Trajectory curve '{ob.name}' has {len(splines)} splines — "
            "keep exactly one (delete the extras in Edit Mode)."
        )
    spline = splines[0]
    if getattr(spline, "use_cyclic_u", False):
        raise PayloadError(
            f"Trajectory curve '{ob.name}' is cyclic — trajectories must have "
            "a start and an end (uncheck Cyclic in the curve settings)."
        )

    mw = ob.matrix_world
    local_pts = []
    if spline.type == "BEZIER":
        bps = spline.bezier_points
        if len(bps) < 2:
            raise PayloadError(f"Trajectory curve '{ob.name}' needs at least 2 control points.")
        res = max(2, curve.resolution_u)
        for i in range(len(bps) - 1):
            a, b = bps[i], bps[i + 1]
            seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res + 1)
            local_pts.extend(seg if i == 0 else seg[1:])
    elif spline.type == "POLY":
        local_pts = [Vector(p.co[:3]) for p in spline.points]
    else:
        # NURBS etc. — evaluate through the depsgraph; the wire-mesh of a
        # single un-beveled spline keeps vertex order along the curve.
        ev = ob.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            local_pts = [v.co.copy() for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
        if not local_pts:
            raise PayloadError(
                f"Could not evaluate trajectory curve '{ob.name}' "
                f"(spline type {spline.type})."
            )

    world = [mw @ Vector(p) for p in local_pts]
    return [(v.x, v.y) for v in world]


def curve_length_quick(ob):
    """Approximate ground-plane length of a curve object WITHOUT a
    depsgraph evaluation — safe to call from panel draw() every redraw.
    Pure math on control points (BEZIER via interpolate_bezier, POLY
    directly); returns None for other spline types or invalid curves —
    callers skip the live display then (generate-time validation still
    covers those)."""
    from mathutils import Vector
    from mathutils.geometry import interpolate_bezier

    if ob is None or ob.type != "CURVE" or len(ob.data.splines) != 1:
        return None
    spline = ob.data.splines[0]
    mw = ob.matrix_world
    if spline.type == "BEZIER":
        bps = spline.bezier_points
        if len(bps) < 2:
            return None
        pts = []
        for i in range(len(bps) - 1):
            a, b = bps[i], bps[i + 1]
            seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, 9)
            pts.extend(seg if i == 0 else seg[1:])
    elif spline.type == "POLY":
        if len(spline.points) < 2:
            return None
        pts = [Vector(p.co[:3]) for p in spline.points]
    else:
        return None
    world = [mw @ Vector(p) for p in pts]
    return geometry.polyline_length([(v.x, v.y) for v in world])


def collect_inputs(context):
    """Read everything the payload needs from the scene. Main thread only."""
    props = context.scene.animoflow_props
    from . import panel  # cached /v1/tasks catalog for duration clamping

    task = props.task
    spec = {
        "task": task,
        "prompt": props.prompt,
        "model": props.model,
        "character": props.character,
        "duration": float(props.duration),
        "seed": int(props.seed),
        "output_fps": int(props.output_fps),
        "cfg": float(props.cfg),
        "keyframe_builder": bool(props.keyframe_builder),
        "keyframe_builder_error_degrees": float(props.keyframe_builder_error_degrees),
        "tasks_data": panel._tasks_data,
    }

    if task in ("__loading__", ""):
        raise PayloadError("Not connected — click Connect in the panel first.")
    if spec["model"] in ("__loading__", "") and task != "timeline":
        raise PayloadError("No model selected — connect to the server first.")

    if task == "trajectory":
        curve_ob = getattr(props, "trajectory_curve", None)
        if curve_ob is None:
            raise PayloadError(
                "No trajectory curve set — click 'Draw Trajectory' or pick a "
                "curve object in the panel."
            )
        depsgraph = context.evaluated_depsgraph_get()
        spec["curve_points_blender"] = _sample_curve_points(curve_ob, depsgraph)

    elif task == "waypoint":
        items = getattr(props, "waypoints", None)
        # Freshly created/moved empties have a stale matrix_world until
        # the depsgraph re-evaluates (bites in background mode, and in the
        # GUI when Generate is clicked in the same tick as a move).
        context.view_layer.update()
        collected = []
        for i, item in enumerate(items or []):
            if item.empty is None:
                raise PayloadError(
                    f"Waypoint #{i + 1} lost its marker object (was it deleted?) — "
                    "remove the row or add a new waypoint."
                )
            loc = item.empty.matrix_world.translation
            collected.append({"bx": loc.x, "by": loc.y, "t_sec": float(item.t_sec)})
        spec["waypoints"] = collected

    elif task == "timeline":
        items = getattr(props, "segments", None)
        spec["segments"] = [
            {"prompt": s.prompt, "duration": float(s.duration)} for s in (items or [])
        ]

    return spec


# ---------------------------------------------------------------------------
# Pure payload builder (thread-safe, pytest-testable)
# ---------------------------------------------------------------------------

def build_payload(spec):
    """Turn a spec dict from `collect_inputs()` into a GenerateRequest body."""
    task = spec["task"]
    builders = {
        "text": _build_text,
        "trajectory": _build_trajectory,
        "waypoint": _build_waypoints,
        "timeline": _build_timeline,
    }
    if task not in builders:
        raise PayloadError(f"Unsupported task '{task}'.")
    return builders[task](spec)


def _base_payload(spec, clamp_duration=True):
    """Fields shared by all simple-mode (non-timeline) payloads."""
    character = (spec.get("character") or "").strip()
    if not character or character == "__loading__":
        raise PayloadError("No character selected — connect to the server first.")

    duration = float(spec["duration"])
    if clamp_duration:
        rng = _duration_range_for_model(
            spec.get("tasks_data"), spec["task"], spec.get("model")
        )
        if rng:
            duration = min(rng[1], max(rng[0], duration))

    return {
        "model": spec["model"],
        "character": character,
        "duration": round(duration, 1),
        "seed": int(spec.get("seed", -1)),
        # Guidance scale (simple mode). Server clamps to the model range.
        "cfg": round(float(spec.get("cfg", 7.5)), 2),
        # Bake the animation at the panel's Output FPS — the server
        # resamples the model's native rate to this before rigging.
        "output_fps": int(spec.get("output_fps", 30)),
        "keyframe_builder": bool(spec.get("keyframe_builder", False)),
        "keyframe_builder_error_degrees": float(
            spec.get("keyframe_builder_error_degrees", 3.0)
        ),
        # Blender's glTF importer rejects EXT_meshopt_compression.
        "compress_output": False,
    }


def _build_text(spec):
    payload = _base_payload(spec)
    payload["input"] = {
        "type": "text",
        "prompt": _clean_prompt(spec.get("prompt"), required=True),
    }
    return payload


def _build_trajectory(spec):
    pts_blender = spec.get("curve_points_blender") or []
    if len(pts_blender) < 2:
        raise PayloadError("Trajectory needs at least 2 points — draw a longer curve.")

    # Send the curve RAW (exactly where the user drew it) — the server
    # canonicalizes internally for the model and places the generated
    # motion back on the original curve (traj_restore, 2026-07-02), so
    # the imported clip lands on the drawn Blender curve.
    api_pts = [geometry.blender_ground_to_api(x, y) for x, y in pts_blender]
    api_pts = geometry.resample_polyline(api_pts)
    if len(api_pts) < 2 or geometry.polyline_length(api_pts) < 0.05:
        raise PayloadError(
            "Trajectory is too short — draw at least ~5 cm of travel "
            "(a single point can't define a path)."
        )
    curve_2d = [[x, z] for x, z in api_pts]

    payload = _base_payload(spec)
    input_obj = {
        "type": "trajectory",
        "curve_2d": curve_2d,
        # Web-UI parity: fixed trapezoidal velocity profile (priorMDM only,
        # ignored by other models).
        "accel_frac": 0.25,
        "decel_frac": 0.25,
    }
    prompt = _clean_prompt(spec.get("prompt"), required=False, what="Style prompt")
    if prompt:
        input_obj["prompt"] = prompt
    payload["input"] = input_obj
    return payload


def _build_waypoints(spec):
    pins = spec.get("waypoints") or []
    if len(pins) < 2:
        raise PayloadError(
            "Add at least 2 waypoints — the first is the start pin "
            "(the clip begins there at t=0), plus at least one later point."
        )
    if len(pins) > geometry.WP_MAX_POINTS:
        raise PayloadError(
            f"Too many waypoints — max {geometry.WP_MAX_POINTS} pins "
            "(start + 5 more)."
        )

    # Row #1 is the start pin at t=0 (its time is locked in the UI);
    # later times must be strictly ascending and positive.
    times = [w["t_sec"] for w in pins]
    for i, t in enumerate(times[1:], start=2):
        if t <= 0:
            raise PayloadError(f"Waypoint #{i} time must be greater than 0 s.")
        if t <= times[i - 2]:
            raise PayloadError(
                f"Waypoint times must strictly increase — #{i} "
                f"({t:.1f} s) is not after #{i - 1} ({times[i - 2]:.1f} s). "
                "Reorder or edit the times in the list."
            )

    # Auto-inflate the clip so the last waypoint fits (web parity).
    duration = float(spec["duration"])
    rng = _duration_range_for_model(
        spec.get("tasks_data"), spec["task"], spec.get("model")
    ) or (2.0, 9.0)
    duration = geometry.inflate_duration_for(times[-1], duration, max_duration=rng[1])
    if times[-1] + geometry.WP_DURATION_PADDING_S > rng[1]:
        raise PayloadError(
            f"Waypoint at {times[-1]:.1f} s doesn't fit — the model caps clips "
            f"at {rng[1]:.0f} s. Use earlier times."
        )

    spec = dict(spec, duration=duration)
    payload = _base_payload(spec)

    # Pins go RAW (as placed) — the server canonicalizes the set
    # (translate the start pin to the origin + rotate the first leg to
    # +Z, exactly like trajectories) and restores the output onto the
    # pins, so the path may start anywhere in the scene.
    wp_list = []
    for i, w in enumerate(pins):
        x, z = geometry.blender_ground_to_api(w["bx"], w["by"])
        wp_list.append({
            "x": round(x, 4),
            "y": 0.0,
            "z": round(z, 4),
            "t": 0 if i == 0 else geometry.waypoint_t_frames(w["t_sec"], duration),
        })

    input_obj = {"type": "waypoints", "waypoints": wp_list}
    prompt = _clean_prompt(spec.get("prompt"), required=False)
    if prompt:
        input_obj["prompt"] = prompt
    payload["input"] = input_obj
    return payload


def _build_timeline(spec):
    segments = spec.get("segments") or []
    if len(segments) < geometry.TIMELINE_MIN_SEGMENTS:
        raise PayloadError("Add at least 2 segments to the timeline.")

    total = 0.0
    clean = []
    for i, seg in enumerate(segments):
        prompt = _clean_prompt(seg.get("prompt"), required=True,
                               what=f"Segment #{i + 1} prompt")
        dur = float(seg.get("duration", 0))
        if not (geometry.TIMELINE_SEG_MIN_S <= dur <= geometry.TIMELINE_SEG_MAX_S):
            raise PayloadError(
                f"Segment #{i + 1} duration {dur:.1f} s is out of range "
                f"({geometry.TIMELINE_SEG_MIN_S}–{geometry.TIMELINE_SEG_MAX_S} s)."
            )
        total += dur
        clean.append({"prompt": prompt, "duration": round(dur, 1)})
    if total > geometry.TIMELINE_MAX_TOTAL_S:
        raise PayloadError(
            f"Timeline is {total:.1f} s — the cap is "
            f"{geometry.TIMELINE_MAX_TOTAL_S:.0f} s. Shorten or remove segments."
        )

    character = (spec.get("character") or "").strip()
    if not character or character == "__loading__":
        raise PayloadError("No character selected — connect to the server first.")

    input_obj = {"type": "timeline", "segments": clean}
    seed = int(spec.get("seed", -1))
    if seed >= 0:
        input_obj["seed"] = seed
    # Timeline guidance goes INSIDE the input (TimelineInput.cfg) — the
    # top-level cfg is simple-mode only. priorMDM (the timeline model)
    # clamps 0–10 server-side.
    input_obj["cfg"] = round(float(spec.get("cfg", 2.5)), 2)

    # Web parity: timeline omits top-level model/duration — the server
    # routes to priorMDM and derives length from the segments.
    return {
        "input": input_obj,
        "character": character,
        "output_fps": int(spec.get("output_fps", 30)),
        "keyframe_builder": bool(spec.get("keyframe_builder", False)),
        "keyframe_builder_error_degrees": float(
            spec.get("keyframe_builder_error_degrees", 3.0)
        ),
        "compress_output": False,
    }
