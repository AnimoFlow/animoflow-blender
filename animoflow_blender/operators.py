"""
AnimoFlow operators: Generate (modal, background-threaded).
"""
import os
import threading
import time

import bpy

from . import api

# ---------------------------------------------------------------------------
# Module-level job state — shared between the background thread and
# the modal operator (which runs on the main thread).
# ---------------------------------------------------------------------------
_state = {
    "running": False,
    "status": "idle",   # idle | generating | polling | downloading | importing | done | error
    "message": "",
    "stage": None,             # human-readable stage label from the server
    "progress": 0.0,           # 0.0–1.0 from server (still tracked, not displayed)
    "enhanced_prompt": None,   # resolved prompt from pipeline_resolved (if prompt_enhance ran)
    "output_fps": 30,          # fps of the generated motion (set scene fps on import)
    "output_path": None,          # canonical: path to the downloaded file (.glb or .fbx)
    "fbx_path": None,              # legacy alias kept for back-compat with panel code
    "error": None,
    "job_id": None,
}


def get_state():
    return _state


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _friendly_error(result: dict) -> str:
    """Delegate to api.friendly_error — kept here for backwards compat."""
    return api.friendly_error(result)


def _worker(base_url, api_key, payload, output_fps=30):
    """Runs in a daemon thread. `payload` is a prebuilt GenerateRequest
    body from `payload.build_payload()` (built on the MAIN thread inside
    Generate.execute \u2014 no bpy access here). Writes results into _state."""
    try:
        _state["status"] = "generating"
        _state["message"] = "Generating motion\u2026"
        _state["progress"] = 0.0
        _state["output_fps"] = output_fps

        # Cloud-mode short-circuit: the HF Space's /generate_full endpoint
        # is a single blocking call \u2014 no job_id, no polling, no separate
        # download step. We skip straight to "importing".
        if api.is_hf_space(base_url):
            _state["message"] = "Generating\u2026 (your HF quota)"
            _state["stage"] = "Generating"

            def _on_progress(tuple_data):
                # The Space's _generate_full yields a 4-tuple where slot 1
                # is the human-readable stage string (e.g. "Generating\u2026 (45%)",
                # "Inverse Kinematics\u2026 (70%)"). Surface it so the panel's
                # live label tracks the same labels the webui shows.
                if isinstance(tuple_data, list) and len(tuple_data) >= 2:
                    label = tuple_data[1]
                    if isinstance(label, str) and label.strip():
                        _state["stage"] = label
                        _state["message"] = label

            result = api.generate_cloud(
                base_url, api_key, payload, on_progress=_on_progress,
            )
            _state["enhanced_prompt"] = result.get("rewritten")
            _state["output_path"] = result["path"]
            _state["fbx_path"] = result["path"]  # back-compat alias
            _state["status"] = "importing"
            return

        job_id = api.generate(base_url, payload, api_key=api_key)
        _state["job_id"] = job_id
        _state["status"] = "polling"

        while True:
            time.sleep(1.0)
            result = api.poll_job(base_url, job_id, api_key=api_key)
            s = result.get("status", "")
            # Server-side stage label drives the user-facing message now —
            # progress bar was inaccurate / jumpy between pipeline stages,
            # so we show "Generating…" / "Inverse Kinematics…" / "Rigging…"
            # / "Post processing…" instead. Progress still tracked on state
            # in case future code wants it, but not displayed.
            _state["progress"] = result.get("progress", _state["progress"])
            stage_label = result.get("stage")
            if stage_label:
                _state["stage"]   = stage_label
                _state["message"] = stage_label

            if s == "done":
                # Capture output fps from server response
                if result.get("output_fps"):
                    _state["output_fps"] = result["output_fps"]

                # Extract enhanced prompt if present
                resolved = result.get("pipeline_resolved") or []
                for stage in resolved:
                    if stage.get("stage") == "preprocess" and stage.get("type") == "prompt_enhance":
                        out = stage.get("output") or {}
                        _state["enhanced_prompt"] = out.get("prompt")
                        break

                _state["status"]  = "downloading"
                _state["message"] = "Downloading\u2026"
                _state["stage"]   = "Downloading…"
                out_path = api.download_output(base_url, result["download_url"], api_key=api_key)
                _state["output_path"] = out_path
                _state["fbx_path"]    = out_path   # back-compat alias
                _state["status"] = "importing"   # signal main thread to import
                return

            elif s in ("failed", "error"):
                raise RuntimeError(_friendly_error(result))

            elif s == "cancelled":
                raise RuntimeError("Job was cancelled.")

            elif not stage_label:
                # No stage label yet (job just started) — show a neutral
                # "Starting…" instead of percentage noise.
                _state["message"] = "Starting\u2026"

    except Exception as exc:
        _state["error"] = str(exc)
        _state["status"] = "error"
        _state["running"] = False


# ---------------------------------------------------------------------------
# Output import — module-level so the headless test harness can call it
# directly (modal operators are unreliable in `blender --background`).
# ---------------------------------------------------------------------------

def import_output(scene, out_path, output_fps=30):
    """Import a downloaded .glb/.gltf/.fbx into the scene and set the
    scene FPS. Raises on any failure — callers surface the error."""
    if not out_path or not os.path.exists(out_path):
        raise RuntimeError(f"Output file missing: {out_path!r}")
    ext = os.path.splitext(out_path)[1].lower()

    # Set the scene FPS BEFORE importing: the glTF importer converts
    # keyframe times (stored in seconds) to frame numbers using the
    # scene FPS at import time. Importing at the default 24 fps and
    # switching to 30 afterwards would leave the action ~25% short and
    # make it play back too fast. (Caught by the headless harness.)
    scene.render.fps = output_fps
    scene.render.fps_base = 1.0

    # Dispatch on extension. Terminal format is now .glb
    # (bezier-preserving CUBICSPLINE); .fbx still accepted for
    # backwards compatibility with older pipelines.
    names_before = {ob.name for ob in bpy.data.objects}
    if ext in (".glb", ".gltf"):
        # bone_heuristic='BLENDER' (the 4.2+ default) gives every bone a
        # giant Icosphere custom shape (~120–196× scale on our rigs) that
        # buries the character in the viewport. 'TEMPERANCE' imports
        # plain bones, sane lengths, no shape meshes. Fall back for
        # Blender <4.2 importers that predate the parameter.
        try:
            bpy.ops.import_scene.gltf(filepath=out_path,
                                      bone_heuristic='TEMPERANCE')
        except TypeError:
            bpy.ops.import_scene.gltf(filepath=out_path)
            for ob in bpy.data.objects:
                if ob.type == "ARMATURE":
                    for pb in ob.pose.bones:
                        pb.custom_shape = None
    else:
        bpy.ops.import_scene.fbx(
            filepath=out_path,
            use_anim=True,
            automatic_bone_orientation=True,
            use_image_search=True,
        )

    # Draw the imported rig as thin sticks. Octahedral bones (the default)
    # poke through the character surface at the head, fingers, and toes —
    # the armature reads as sitting ON TOP of the mesh. STICK is display
    # metadata only: animation data, rest pose, and the keyframe builder
    # are untouched. Scoped to armatures this import created.
    for ob in bpy.data.objects:
        if ob.type == "ARMATURE" and ob.name not in names_before:
            ob.data.display_type = 'STICK'

    # Unpack embedded textures to disk so Blender can display them.
    # Applies to FBX imports; glTF's import_scene.gltf writes textures
    # to disk natively when the .glb contains images.
    tex_dir = os.path.join(os.path.dirname(out_path), "textures")
    os.makedirs(tex_dir, exist_ok=True)
    unpacked = 0
    for img in bpy.data.images:
        if img.packed_file and img.name not in ('Render Result', 'Viewer Node'):
            try:
                src = img.filepath_raw or img.filepath or ""
                basename = os.path.basename(src) if src else (img.name + ".png")
                abs_path = os.path.join(tex_dir, basename)
                with open(abs_path, 'wb') as fout:
                    fout.write(img.packed_file.data)
                img.filepath = abs_path
                img.filepath_raw = abs_path
                img.source = 'FILE'
                img.reload()
                unpacked += 1
            except Exception as _e:
                print(f"[AnimoFlow] Could not unpack {img.name}: {_e}")
    if unpacked:
        print(f"[AnimoFlow] Unpacked {unpacked} texture(s) to {tex_dir}")


# ---------------------------------------------------------------------------
# Generate operator
# ---------------------------------------------------------------------------

class ANIMOFLOW_OT_Generate(bpy.types.Operator):
    """Generate a motion animation via AnimoFlow and import the FBX"""
    bl_idname = "animoflow.generate"
    bl_label = "Generate"
    bl_options = {"REGISTER"}

    _timer = None

    # -- modal loop -----------------------------------------------------------

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        # Redraw the N-panel so status text updates live
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        status = _state["status"]

        if status == "importing":
            out_path = _state.get("output_path") or _state.get("fbx_path")
            output_fps = _state.get("output_fps", 30)
            try:
                import_output(context.scene, out_path, output_fps)
                _state["status"] = "done"
                _state["message"] = f"Done! ({output_fps} fps)"
            except Exception as exc:
                ext = os.path.splitext(out_path)[1].lower() if out_path else ""
                _state["status"] = "error"
                _state["error"] = f"Import failed ({ext}): {exc}"

            _state["running"] = False
            self._finish(context)
            return {"FINISHED"}

        if status == "error":
            self.report({"ERROR"}, _state["error"] or "Unknown error")
            _state["running"] = False
            self._finish(context)
            return {"FINISHED"}

        return {"PASS_THROUGH"}

    # -- execute (entry point) ------------------------------------------------

    def execute(self, context):
        if _state["running"]:
            self.report({"WARNING"}, "AnimoFlow: generation already in progress")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__package__].preferences
        props = context.scene.animoflow_props

        base_url = prefs.get_base_url()
        if not base_url:
            self.report({"ERROR"}, "AnimoFlow: not configured — open Preferences > Add-ons > AnimoFlow")
            return {"CANCELLED"}

        # Build the request body on the MAIN thread (payload.collect_inputs
        # touches bpy data — curve sampling, waypoint empties). All
        # validation errors surface here, before any network traffic.
        from . import payload as payload_mod
        try:
            spec = payload_mod.collect_inputs(context)
            request_body = payload_mod.build_payload(spec)
        except payload_mod.PayloadError as exc:
            self.report({"ERROR"}, f"AnimoFlow: {exc}")
            return {"CANCELLED"}

        # Reset state
        _state.update({
            "running": True,
            "status": "generating",
            "message": "Starting\u2026",
            "stage": None,
            "progress": 0.0,
            "enhanced_prompt": None,
            "output_fps": props.output_fps,
            "output_path": None,
            "fbx_path": None,
            "error": None,
            "job_id": None,
        })

        api_key = prefs.api_key.strip() if prefs.server_mode == "cloud" else ""

        t = threading.Thread(
            target=_worker,
            args=(base_url, api_key, request_body),
            kwargs={"output_fps": props.output_fps},
            daemon=True,
        )
        t.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        return self.execute(context)

    def _finish(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None


# ---------------------------------------------------------------------------
# Connect operator (Preferences-pane primary action)
# ---------------------------------------------------------------------------

class ANIMOFLOW_OT_Connect(bpy.types.Operator):
    """Connect to AnimoFlow and load tasks, models and characters"""
    bl_idname = "animoflow.connect"
    bl_label = "Connect"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import preferences as prefs_mod
        from . import refresh

        prefs = context.preferences.addons[__package__].preferences
        if not prefs.get_base_url():
            if prefs.server_mode == "cloud":
                msg = "Paste your Hugging Face token first, then click Connect."
            else:
                msg = "Set a server URL first, then click Connect."
            prefs_mod.set_connection_status(f"err: {msg}")
            self.report({"WARNING"}, msg)
            return {"CANCELLED"}

        # Async — the status banner flips busy → ok/err when the worker
        # finishes and the panel redraws itself. Never blocks the UI.
        refresh.refresh_from_prefs(context)
        self.report({"INFO"}, "Connecting to AnimoFlow…")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Trajectory draw operator (GUI sugar — the tested path is assigning any
# existing curve to props.trajectory_curve; this button just makes the
# common case one click)
# ---------------------------------------------------------------------------

class ANIMOFLOW_OT_TrajectoryDraw(bpy.types.Operator):
    """Create a trajectory curve and activate the Draw tool on the floor"""
    bl_idname = "animoflow.trajectory_draw"
    bl_label = "Draw Trajectory"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        curve = bpy.data.curves.new("AF_Trajectory", type="CURVE")
        curve.dimensions = "3D"
        ob = bpy.data.objects.new("AF_Trajectory", curve)
        context.scene.collection.objects.link(ob)
        context.scene.animoflow_props.trajectory_curve = ob

        for other in context.selected_objects:
            other.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob

        # The curve Draw tool projects onto a plane through the 3D
        # cursor — pin it to the world origin so strokes land on Z=0.
        context.scene.cursor.location = (0.0, 0.0, 0.0)

        # Trajectories are 2-D: snap the viewport to top orthographic so
        # the cursor-depth plane IS the floor and strokes land exactly on
        # Z=0 (drawing from an angled view would put points off-plane —
        # the sampler drops Z anyway, but what you see should be what
        # you get).
        space = getattr(context, "space_data", None)
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None:
            from mathutils import Quaternion
            rv3d.view_perspective = "ORTHO"
            rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))  # top view

        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.wm.tool_set_by_id(name="builtin.draw")
            paint = context.scene.tool_settings.curve_paint_settings
            paint.depth_mode = "CURSOR"
        except Exception as exc:
            # Tool ids / paint settings are the version-fragile part
            # (Blender 5). The curve exists and is assigned — the user
            # can still edit it manually; say so instead of failing.
            self.report(
                {"WARNING"},
                f"Curve created, but couldn't activate the Draw tool ({exc}). "
                "Edit the curve manually, then Generate.",
            )
            return {"FINISHED"}

        self.report(
            {"INFO"},
            "Draw the path on the floor. Press Tab when done, then Generate.",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Waypoint operators (empties + panel list). Core logic lives in plain
# functions so the headless harness can drive the real code path without
# bpy.ops context requirements.
# ---------------------------------------------------------------------------

WAYPOINT_MAX_PINS = 6  # web UI's cap (start pin + 5 more)
_WAYPOINT_COLLECTION = "AnimoFlow"


def _waypoint_collection(scene):
    coll = bpy.data.collections.get(_WAYPOINT_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(_WAYPOINT_COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def add_waypoint(context, location=None):
    """Create a waypoint marker + list row. Returns the new item.

    The FIRST row is the START pin: the clip begins there at t=0 (its
    time is locked; the server canonicalizes the set — translate+rotate
    to its trained frame — and restores the output onto the pins, so
    the path may start anywhere). Later rows carry ascending times.
    Raises RuntimeError at the web UI's 6-pin cap."""
    from . import geometry

    props = context.scene.animoflow_props
    if len(props.waypoints) >= WAYPOINT_MAX_PINS:
        raise RuntimeError(
            f"Waypoint cap reached — max {WAYPOINT_MAX_PINS} pins "
            "(start + 5 more)."
        )

    if location is None:
        location = tuple(context.scene.cursor.location)
    x, y = float(location[0]), float(location[1])

    is_start = len(props.waypoints) == 0
    ob = bpy.data.objects.new("AF_Start" if is_start else "AF_Waypoint", None)
    ob.empty_display_type = "SPHERE"
    ob.empty_display_size = 0.18 if is_start else 0.15
    ob.location = (x, y, 0.0)  # waypoints live on the floor
    # Waypoints are 2-D: lock the Z channel so the marker can't be
    # dragged off the floor (the payload ignores Z regardless, but the
    # viewport shouldn't suggest height matters).
    ob.lock_location[2] = True
    _waypoint_collection(context.scene).objects.link(ob)

    item = props.waypoints.add()
    item.empty = ob
    if is_start:
        item.t_sec = 0.0
    else:
        prev_t = props.waypoints[-2].t_sec
        item.t_sec = prev_t + (1.0 if prev_t else 2.0)
    props.waypoints_index = len(props.waypoints) - 1

    # Auto-inflate the clip duration so the new waypoint fits (web parity).
    bumped = False
    if not is_start:
        inflated = geometry.inflate_duration_for(item.t_sec, props.duration)
        bumped = inflated > props.duration
        if bumped:
            props.duration = inflated
    return item, bumped


def remove_waypoint(context, index):
    """Remove list row `index` and its marker empty (if it still exists)."""
    props = context.scene.animoflow_props
    if not (0 <= index < len(props.waypoints)):
        raise RuntimeError(f"No waypoint at row {index + 1}.")
    item = props.waypoints[index]
    if item.empty is not None:
        try:
            bpy.data.objects.remove(item.empty)
        except ReferenceError:
            pass  # already deleted in the viewport
    props.waypoints.remove(index)
    props.waypoints_index = min(index, len(props.waypoints) - 1)


class ANIMOFLOW_OT_WaypointAdd(bpy.types.Operator):
    """Add a waypoint: snaps to top view, then click the floor to place it"""
    bl_idname = "animoflow.waypoint_add"
    bl_label = "Add Waypoint"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        props = context.scene.animoflow_props
        if len(props.waypoints) >= WAYPOINT_MAX_PINS:
            self.report({"WARNING"},
                        f"Waypoint cap reached — max {WAYPOINT_MAX_PINS} pins.")
            return {"CANCELLED"}

        # Waypoints are 2-D: snap to top orthographic (same treatment as
        # Draw Trajectory) so the click lands unambiguously on the floor.
        space = getattr(context, "space_data", None)
        rv3d = getattr(space, "region_3d", None)
        if rv3d is None:
            # Headless / no viewport: fall back to the 3D-cursor drop so
            # scripted callers keep working.
            return self.execute(context)
        from mathutils import Quaternion
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))  # top view

        context.window_manager.modal_handler_add(self)
        label = "start pin" if len(props.waypoints) == 0 else "waypoint"
        context.workspace.status_text_set(
            f"Click the floor to place the {label} — Esc / right-click cancels"
        )
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            from bpy_extras import view3d_utils
            from mathutils import Vector
            # The operator was invoked from the N-panel, so context.region
            # is the sidebar — find the 3D viewport region under the mouse.
            for area in context.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for region in area.regions:
                    if region.type != "WINDOW":
                        continue
                    if not (region.x <= event.mouse_x < region.x + region.width
                            and region.y <= event.mouse_y < region.y + region.height):
                        continue
                    coord = (event.mouse_x - region.x, event.mouse_y - region.y)
                    rv3d = area.spaces.active.region_3d
                    loc = view3d_utils.region_2d_to_location_3d(
                        region, rv3d, coord, Vector((0.0, 0.0, 0.0)))
                    context.workspace.status_text_set(None)
                    try:
                        item, bumped = add_waypoint(context, (loc.x, loc.y, 0.0))
                    except RuntimeError as exc:
                        self.report({"WARNING"}, str(exc))
                        return {"CANCELLED"}
                    if bumped:
                        props = context.scene.animoflow_props
                        self.report({"INFO"},
                                    f"Duration bumped to {props.duration:.1f} s "
                                    f"to fit the waypoint at {item.t_sec:.1f} s.")
                    return {"FINISHED"}
            return {"RUNNING_MODAL"}  # click outside the viewport — keep waiting
        if event.type in ("ESC", "RIGHTMOUSE"):
            context.workspace.status_text_set(None)
            return {"CANCELLED"}
        # Let navigation (orbit/zoom) through so the user can reframe.
        if event.type in ("MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def execute(self, context):
        # Non-interactive path (headless tests, scripts): 3D-cursor drop.
        try:
            item, bumped = add_waypoint(context)
        except RuntimeError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ANIMOFLOW_OT_WaypointRemove(bpy.types.Operator):
    """Remove the selected waypoint (and its marker)"""
    bl_idname = "animoflow.waypoint_remove"
    bl_label = "Remove Waypoint"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.animoflow_props
        try:
            remove_waypoint(context, props.waypoints_index)
        except RuntimeError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ANIMOFLOW_OT_WaypointClear(bpy.types.Operator):
    """Remove all waypoints and their markers"""
    bl_idname = "animoflow.waypoint_clear"
    bl_label = "Clear Waypoints"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.animoflow_props
        while len(props.waypoints):
            remove_waypoint(context, len(props.waypoints) - 1)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Timeline segment operators
# ---------------------------------------------------------------------------

TIMELINE_TOTAL_CAP_S = 20.0

# Web-parity example prompts (timeline.js EXAMPLE_PROMPTS — keep in
# sync): a new segment grabs a fresh idea instead of always defaulting
# to "A person walks". Picked at random, avoiding the previous
# segment's prompt when possible.
TIMELINE_EXAMPLE_PROMPTS = (
    "A person walks",
    "A person sits while crossing legs",
    "A person makes a long leap forward",
    "A person is running in a circle",
    "A person waves with their right hand",
    "A person dances energetically",
    "A person picks something up off the floor",
    "A person punches forward then backs away",
)
_SEG_RANDOM_MIN_S = 3.0
_SEG_RANDOM_MAX_S = 5.0


def add_segment(context, prompt=None, duration=None):
    """Append a timeline segment. Raises RuntimeError at the 20 s cap.

    With no explicit prompt/duration, mirrors the webui's + tile: a
    random example prompt (different from the previous segment's when
    possible) and a random 3.0–5.0 s duration in 0.5 s steps."""
    import random

    props = context.scene.animoflow_props
    total = sum(s.duration for s in props.segments)
    if total + 0.5 > TIMELINE_TOTAL_CAP_S:
        raise RuntimeError(
            f"Timeline is full — total is capped at {TIMELINE_TOTAL_CAP_S:.0f} s."
        )
    if prompt is None:
        prev = props.segments[-1].prompt if len(props.segments) else None
        pool = [p for p in TIMELINE_EXAMPLE_PROMPTS if p != prev] \
            or list(TIMELINE_EXAMPLE_PROMPTS)
        prompt = random.choice(pool)
    if duration is None:
        span = _SEG_RANDOM_MAX_S - _SEG_RANDOM_MIN_S
        duration = round((_SEG_RANDOM_MIN_S + random.random() * span) * 2) / 2

    seg = props.segments.add()
    seg.prompt = prompt
    seg.duration = min(duration, TIMELINE_TOTAL_CAP_S - total)
    props.segments_index = len(props.segments) - 1
    return seg


class ANIMOFLOW_OT_SegmentAdd(bpy.types.Operator):
    """Append a segment to the timeline"""
    bl_idname = "animoflow.segment_add"
    bl_label = "Add Segment"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            add_segment(context)
        except RuntimeError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ANIMOFLOW_OT_SegmentRemove(bpy.types.Operator):
    """Remove the selected segment (a timeline needs at least 2)"""
    bl_idname = "animoflow.segment_remove"
    bl_label = "Remove Segment"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.animoflow_props
        idx = props.segments_index
        if not (0 <= idx < len(props.segments)):
            return {"CANCELLED"}
        props.segments.remove(idx)
        props.segments_index = min(idx, len(props.segments) - 1)
        return {"FINISHED"}


class ANIMOFLOW_OT_SegmentMove(bpy.types.Operator):
    """Move the selected segment up or down"""
    bl_idname = "animoflow.segment_move"
    bl_label = "Move Segment"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=[("UP", "Up", ""), ("DOWN", "Down", "")],
        default="UP",
    )

    def execute(self, context):
        props = context.scene.animoflow_props
        idx = props.segments_index
        target = idx - 1 if self.direction == "UP" else idx + 1
        if not (0 <= idx < len(props.segments)) or \
                not (0 <= target < len(props.segments)):
            return {"CANCELLED"}
        props.segments.move(idx, target)
        props.segments_index = target
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Refresh server data operator (characters + models)
# ---------------------------------------------------------------------------

class ANIMOFLOW_OT_RefreshServerData(bpy.types.Operator):
    """Fetch the task, model and character lists from the AnimoFlow server"""
    bl_idname = "animoflow.refresh_server_data"
    bl_label = "Refresh"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from . import refresh
        if not refresh.refresh_from_prefs(context):
            self.report({"WARNING"}, "AnimoFlow is not configured.")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ANIMOFLOW_OT_Generate,
    ANIMOFLOW_OT_Connect,
    ANIMOFLOW_OT_TrajectoryDraw,
    ANIMOFLOW_OT_WaypointAdd,
    ANIMOFLOW_OT_WaypointRemove,
    ANIMOFLOW_OT_WaypointClear,
    ANIMOFLOW_OT_SegmentAdd,
    ANIMOFLOW_OT_SegmentRemove,
    ANIMOFLOW_OT_SegmentMove,
    ANIMOFLOW_OT_RefreshServerData,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
