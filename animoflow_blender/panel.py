"""
AnimoFlow N-panel (View3D > Sidebar > AnimoFlow tab).
"""
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import api
from . import geometry as _geometry
from . import preferences as prefs_mod
from .operators import get_state


def _draw_warning_lines(box, lines):
    """Stack pre-shortened warning lines (first row carries the warning
    icon). Blender labels truncate rather than wrap, so the lines must
    already be short — see geometry.pace_warning_lines."""
    for i, row in enumerate(lines):
        box.label(text=row, icon="ERROR" if i == 0 else "BLANK1")

# ---------------------------------------------------------------------------
# Character, task, and model lists — kept at module level so EnumProperty
# callbacks are stable (Blender requires a consistent reference for dynamic
# enums).
# ---------------------------------------------------------------------------
_character_items = [("Y_bot", "Y-Bot", "")]
_task_items  = [("__loading__", "Connect to server first", "")]
_model_items = [("__loading__", "Connect to server first", "")]

# Full task catalog as returned by /v1/tasks; used to look up models per task.
_tasks_data: list[dict] = []


def refresh_characters(base_url, api_key=""):
    global _character_items
    chars = api.fetch_characters(base_url, api_key=api_key)
    # Order: Y_bot first (the default we want pre-selected), then the rest
    # alphabetically. Blender's EnumProperty with a callback-based `items`
    # has no separate `default` knob — the first item wins.
    chars = sorted(chars, key=lambda c: (0 if c == "Y_bot" else 1, c))
    _character_items = [(c, c.replace("_", " "), "") for c in chars]


def refresh_tasks(base_url, api_key=""):
    """Fetch the task catalog from /v1/tasks and rebuild the task enum.

    Only tasks with status != 'future' are shown as selectable; 'future'
    tasks appear with a suffix so the user knows they are coming.
    (Blender's EnumProperty has no disabled-item concept, so we include
    all non-future tasks and mark future ones clearly.)
    """
    global _task_items, _tasks_data
    tasks = api.fetch_tasks(base_url, api_key=api_key)
    _tasks_data = tasks

    items = []
    for t in tasks:
        label = t.get("display_name", t["id"])
        status = t.get("status", "active")
        if status == "future":
            label += " (coming soon)"
        desc = t.get("description", "")
        items.append((t["id"], label, desc))

    _task_items = items or [("__loading__", "Connect to server first", "")]


def refresh_models_for_task(task_id: str):
    """Rebuild the model enum from the cached _tasks_data for *task_id*.

    Available models are shown normally. Models that are unavailable or have
    status='future' are excluded — Blender EnumProperty has no disabled-item
    concept. If ALL models are unavailable, they are shown with a suffix so
    the user knows why.
    """
    global _model_items
    task_entry = next((t for t in _tasks_data if t["id"] == task_id), None)
    if task_entry is None:
        _model_items = [("__loading__", "Connect to server first", "")]
        return

    models = task_entry.get("models", [])
    usable = [
        m for m in models
        if m.get("available", True) and m.get("status", "active") != "future"
    ]

    def _label(m):
        # Product decision: no "beta"/"wip" qualifiers in the addon —
        # a model the server lists as usable is shown plainly.
        return m.get("display_name", m["type"])

    def _desc(m):
        # Tooltip from server metadata — model_class + tagline, e.g.
        # "Diffusion — Light, fast, quietly excellent".
        parts = [p for p in (m.get("model_class"), m.get("tagline")) if p]
        return " — ".join(parts)

    if usable:
        _model_items = [
            (m["type"], _label(m), _desc(m))
            for m in usable
        ]
    elif models:
        # All containers down — include them with a warning
        _model_items = [
            (m["type"], f"{m.get('display_name', m['type'])} (unavailable)", "")
            for m in models
            if m.get("status", "active") != "future"  # still skip pure-future
        ] or [("__loading__", "No models available", "")]
    else:
        _model_items = [("__loading__", "No models for this task", "")]


def refresh_server_data(base_url, task_id="text", api_key=""):
    """Convenience: refresh characters + task catalog + models for *task_id*."""
    refresh_characters(base_url, api_key=api_key)
    refresh_tasks(base_url, api_key=api_key)
    refresh_models_for_task(task_id)


def _duration_range(task_id, model):
    """(min, max) duration window for *model* under *task_id* from the
    cached catalog, or None when not connected/known."""
    for t in _tasks_data:
        if t.get("id") != task_id:
            continue
        for m in t.get("models", []):
            if m.get("type") == model:
                schema = (m.get("params_schema") or {}).get("duration") or {}
                lo, hi = schema.get("min"), schema.get("max")
                if lo is not None and hi is not None:
                    return float(lo), float(hi)
    return None


def _cfg_spec(task_id, model):
    """(min, max, default) guidance-scale spec for *model* under
    *task_id* from the cached /v1/tasks catalog, or None when not
    connected/known. Matches the server's _model_cfg_spec."""
    for t in _tasks_data:
        if t.get("id") != task_id:
            continue
        for m in t.get("models", []):
            if m.get("type") == model:
                schema = (m.get("params_schema") or {}).get("cfg") or {}
                lo, hi, d = schema.get("min"), schema.get("max"), schema.get("default")
                if lo is not None and hi is not None and d is not None:
                    return float(lo), float(hi), float(d)
    return None


def _on_model_change(self, context):
    """When the selected model changes, snap the cfg slider to that
    model's catalog default (MDM/Kimodo 7.5, MoMask 5.0, priorMDM 2.5).
    The user can then tweak; the server clamps to the model's range."""
    spec = _cfg_spec(self.task, self.model)
    if spec is not None:
        self.cfg = spec[2]


def _character_enum_items(self, context):
    return _character_items or [("Y_bot", "Y-Bot", "")]


def _task_enum_items(self, context):
    return _task_items or [("__loading__", "Connect to server first", "")]


def _model_enum_items(self, context):
    return _model_items or [("__loading__", "Connect to server first", "")]


# ---------------------------------------------------------------------------
# Scene properties (stored per .blend file)
# ---------------------------------------------------------------------------

class AnimoFlowWaypointItem(bpy.types.PropertyGroup):
    """One waypoint: a marker empty in the viewport plus the time
    (seconds) the character should reach it. Row #1 is the START pin —
    the clip begins there at t=0 (time locked in the UI); it can be
    placed anywhere, the server canonicalizes and restores (web-UI
    parity)."""
    empty: PointerProperty(
        name="Marker",
        description="Empty object marking this waypoint's position "
                    "(move it in the viewport to move the waypoint)",
        type=bpy.types.Object,
    )
    t_sec: FloatProperty(
        name="Time (s)",
        description="When the character should reach this waypoint. "
                    "Times must strictly increase from row to row "
                    "(row #1 is the start and stays at 0)",
        default=2.0,
        min=0.0,
        soft_max=8.5,
        step=10,   # 0.1 s
        precision=1,
    )


class AnimoFlowTimelineSegment(bpy.types.PropertyGroup):
    """One timeline segment: a prompt and how long it should last.
    Segments are stitched server-side by priorMDM (double-take)."""
    prompt: StringProperty(
        name="Prompt",
        description="What happens during this segment",
        default="A person walks",
    )
    duration: FloatProperty(
        name="Duration (s)",
        description="Segment length in seconds (0.5–8.0; timeline total "
                    "is capped at 20 s)",
        default=3.0,
        min=0.5,
        max=8.0,
        step=50,   # 0.5 s
        precision=1,
    )


class ANIMOFLOW_UL_Waypoints(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        if item.empty is not None:
            row.label(text=item.empty.name, icon="EMPTY_AXIS")
        else:
            row.label(text="(marker deleted)", icon="ERROR")
        if index == 0:
            # Start pin: the clip begins here — time locked at 0.
            row.label(text="start · t = 0", icon="LOCKED")
        else:
            row.prop(item, "t_sec", text="t")


class ANIMOFLOW_UL_TimelineSegments(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=f"{index + 1}")
        row.prop(item, "prompt", text="")
        sub = row.row(align=True)
        sub.scale_x = 0.45
        sub.prop(item, "duration", text="")


class AnimoFlowSceneProperties(bpy.types.PropertyGroup):

    # -- Task (fetched from /v1/tasks — no hardcoded list here) --
    def _on_task_change(self, context):
        """Rebuild the model enum whenever the user switches task."""
        refresh_models_for_task(self.task)

    task: EnumProperty(
        name="Task",
        description="What kind of motion generation to perform (fetched from server)",
        items=_task_enum_items,
        update=_on_task_change,
    )

    # -- Shared --
    character: EnumProperty(
        name="Character",
        description="Rigged character to animate",
        items=_character_enum_items,
    )

    # -- Text to Motion --
    prompt: StringProperty(
        name="Prompt",
        description="Describe the motion you want",
        default="a person walking",
    )

    # -- Trajectory --
    def _poll_curve(self, obj):
        return obj.type == "CURVE"

    trajectory_curve: PointerProperty(
        name="Trajectory Curve",
        description="Curve object on the ground plane (X-Y) the character "
                    "should follow. Use 'Draw Trajectory' to sketch one, or "
                    "pick any existing curve",
        type=bpy.types.Object,
        poll=_poll_curve,
    )

    # -- Waypoints --
    waypoints: CollectionProperty(type=AnimoFlowWaypointItem)
    waypoints_index: IntProperty(default=0)

    # -- Timeline --
    segments: CollectionProperty(type=AnimoFlowTimelineSegment)
    segments_index: IntProperty(default=0)

    # -- Generation settings --
    duration: FloatProperty(
        name="Duration (sec)",
        description="Length of motion to generate in seconds",
        default=4.0,
        min=1.0,
        max=30.0,
        step=50,  # 0.5s steps
        precision=1,
    )
    output_fps: IntProperty(
        name="Output FPS",
        description="Frame rate the server bakes the animation at (the model's "
                    "native rate is resampled to this). Scene FPS is set to "
                    "match after import.",
        default=30,
        min=10,
        max=120,
    )
    seed: IntProperty(
        name="Seed",
        description="Random seed (-1 = random each time)",
        default=-1,
        min=-1,
    )
    model: EnumProperty(
        name="Model",
        description="Motion generation model (fetched from server)",
        items=_model_enum_items,
        update=_on_model_change,
    )
    cfg: FloatProperty(
        name="Guidance (cfg)",
        description="Classifier-free guidance scale. Higher = follows the "
                    "prompt more strictly; lower = looser, often smoother. "
                    "Snaps to the model's default when you switch model; the "
                    "server clamps to the model's valid range",
        default=7.5,
        min=0.0,
        max=20.0,
        step=10,   # 0.1
        precision=1,
    )

    keyframe_builder: BoolProperty(
        name="Keyframe Builder",
        description="Reduce dense per-frame keyframes to sparse bezier keyframes "
                    "(RDP-style decimation + Blender's FBX simplifier). "
                    "Slower export (~10–20s) but yields animator-editable curves.",
        default=False,
    )
    keyframe_builder_error_degrees: FloatProperty(
        name="KFB Error (deg)",
        description="Max allowed per-frame deviation during keyframe reduction. "
                    "Higher = fewer keyframes, looser animation. Default 3.0.",
        default=3.0,
        min=0.1,
        max=30.0,
        step=10,   # 0.1 step
        precision=1,
    )




# ---------------------------------------------------------------------------
# N-panel
# ---------------------------------------------------------------------------

class ANIMOFLOW_PT_Main(bpy.types.Panel):
    bl_label = "AnimoFlow"
    bl_idname = "ANIMOFLOW_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AnimoFlow"

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences

        # -- Not configured: show setup prompt, disable everything else --
        if not prefs.is_configured():
            box = layout.box()
            col = box.column(align=True)
            col.label(text="AnimoFlow is not configured.", icon="ERROR")
            col.label(text="Go to Edit \u2192 Preferences \u2192")
            col.label(text="Add-ons \u2192 AnimoFlow to get started.")
            layout.separator()
            layout.enabled = False  # grey out the rest

        # -- Connection status: configured but catalog not loaded yet --
        # (auto-refresh normally fills this within ~1\u20132 s of startup; the
        # box surfaces progress/errors and offers a manual Connect so the
        # user never wonders whether the token "took".)
        elif not _tasks_data:
            status = prefs_mod.get_connection_status()
            box = layout.box()
            col = box.column(align=True)
            if status.startswith("busy:"):
                col.label(text=status[5:].strip().capitalize(), icon="SORTTIME")
            elif status.startswith("err:"):
                col.label(text="Connection failed:", icon="ERROR")
                msg = status[4:].strip()
                col.label(text=msg[:44])
                if len(msg) > 44:
                    col.label(text=msg[44:88])
                col.operator("animoflow.connect", text="Retry", icon="LINKED")
            else:
                col.label(text="Not connected.", icon="INFO")
                col.operator("animoflow.connect", text="Connect", icon="LINKED")
            layout.separator()

        props = context.scene.animoflow_props
        state = get_state()

        # -- Character (always at top) --
        col = layout.column(align=True)
        row = col.row(align=True)
        row.label(text="Character")
        row.operator("animoflow.refresh_server_data", text="", icon="FILE_REFRESH")
        col.prop(props, "character", text="")

        layout.separator()

        # -- Task selector (fetched from /v1/tasks — no hardcoding) --
        layout.prop(props, "task", text="Task")

        layout.separator()

        # -- Task-specific UI --
        task_id = props.task

        if task_id == "text":
            layout.prop(props, "prompt", text="")

        elif task_id == "trajectory":
            layout.prop(props, "prompt", text="Style prompt")
            box = layout.box()
            box.label(text="Path on the floor (X-Y plane)", icon="CURVE_PATH")
            box.prop(props, "trajectory_curve", text="Curve")
            box.operator("animoflow.trajectory_draw", icon="GREASEPENCIL")
            if props.trajectory_curve is None:
                box.label(text="Draw a path, or pick an existing curve.",
                          icon="INFO")
            else:
                # Pace feasibility — cheap control-point math, no
                # depsgraph (see payload.curve_length_quick). Generation
                # is never blocked; this only guides the duration choice.
                from . import payload as _payload_mod
                _length = _payload_mod.curve_length_quick(props.trajectory_curve)
                if _length:
                    _warn = _geometry.pace_warning_lines(_length, props.duration)
                    if _warn:
                        _draw_warning_lines(box, _warn)
                    else:
                        box.label(text=f"{_length:.1f} m · "
                                       f"≈ {_length / max(props.duration, 0.1):.1f} m/s")

        elif task_id == "timeline":
            box = layout.box()
            box.label(text="Sequence segments", icon="NLA")
            row = box.row()
            row.template_list("ANIMOFLOW_UL_TimelineSegments", "",
                              props, "segments", props, "segments_index",
                              rows=3)
            col = row.column(align=True)
            col.operator("animoflow.segment_add", text="", icon="ADD")
            col.operator("animoflow.segment_remove", text="", icon="REMOVE")
            col.separator()
            col.operator("animoflow.segment_move", text="",
                         icon="TRIA_UP").direction = "UP"
            col.operator("animoflow.segment_move", text="",
                         icon="TRIA_DOWN").direction = "DOWN"
            total = sum(s.duration for s in props.segments)
            if len(props.segments) < 2:
                box.label(text="Add at least 2 segments.", icon="INFO")
            elif total > 20.0:
                box.label(text=f"Total {total:.1f} s — cap is 20 s.",
                          icon="ERROR")
            else:
                box.label(text=f"Total: {total:.1f} s (cap 20 s)")

        elif task_id == "waypoint":
            layout.prop(props, "prompt", text="Style prompt")
            box = layout.box()
            box.label(text="Pin #1 is the start — place it anywhere", icon="PINNED")
            row = box.row()
            row.template_list("ANIMOFLOW_UL_Waypoints", "",
                              props, "waypoints", props, "waypoints_index",
                              rows=3)
            col = row.column(align=True)
            col.operator("animoflow.waypoint_add", text="", icon="ADD")
            col.operator("animoflow.waypoint_remove", text="", icon="REMOVE")
            col.separator()
            col.operator("animoflow.waypoint_clear", text="", icon="TRASH")
            n = len(props.waypoints)
            box.label(text=f"{n}/6 pins (Add → click the floor to place)")
            times = [w.t_sec for w in props.waypoints]
            if any(b <= a for a, b in zip(times, times[1:])):
                box.label(text="Times must strictly increase.", icon="ERROR")
            else:
                # Pace feasibility across legs (row #1 is the start, t=0).
                pts = []
                for w in props.waypoints:
                    if w.empty is not None:
                        loc = w.empty.matrix_world.translation
                        pts.append((loc.x, loc.y, float(w.t_sec)))
                _warn = _geometry.waypoint_pace_warning_lines(pts)
                if _warn:
                    _draw_warning_lines(box, _warn)

        elif task_id == "keyframes":
            box = layout.box()
            box.label(text="Keyframes task — coming soon", icon="TIME")

        layout.separator()

        # -- Generation settings --
        box = layout.box()
        box.label(text="Generation Settings", icon="SETTINGS")
        col = box.column(align=True)
        # Timeline derives its length from the segments — no duration row.
        # The model row IS shown for every task (web parity: the webui
        # shows the model card even where only one model exists), but the
        # timeline payload omits it — the server routes it internally.
        if task_id != "timeline":
            col.prop(props, "duration")
            rng = _duration_range(task_id, props.model)
            if rng and not (rng[0] <= props.duration <= rng[1]):
                col.label(
                    text=f"Clamped to {rng[0]:g}–{rng[1]:g} s for this model",
                    icon="ERROR",
                )
        col.prop(props, "output_fps")
        col.prop(props, "seed")
        col.prop(props, "model", text="Model")
        col.prop(props, "cfg")
        cfg_rng = _cfg_spec(task_id, props.model)
        if cfg_rng and not (cfg_rng[0] <= props.cfg <= cfg_rng[1]):
            col.label(
                text=f"Clamped to {cfg_rng[0]:g}–{cfg_rng[1]:g} for this model",
                icon="ERROR",
            )

        layout.separator()

        # -- Post-processing --
        box = layout.box()
        box.label(text="Post-Processing", icon="MODIFIER")
        col = box.column(align=True)
        col.prop(props, "keyframe_builder")
        # Only expose the tolerance slider when the node is enabled —
        # keeps the panel tidy for users who never touch it.
        if props.keyframe_builder:
            col.prop(props, "keyframe_builder_error_degrees", text="   Error (deg)")

        layout.separator()

        # -- Generate button / live status --
        running = state["running"]

        if running:
            # Stage text from the server (e.g. "Generating…",
            # "Inverse Kinematics…", "Rigging…", "Post processing…").
            # No percentage — progress bar was inaccurate / jumpy between
            # pipeline stages so we dropped it in v1.3.0.
            label = state.get("message") or state.get("stage") or "Starting\u2026"
            layout.label(text=label, icon="SORTTIME")
            row = layout.row()
            row.enabled = False
            row.operator("animoflow.generate", text="Generating\u2026", icon="PLAY")
        else:
            status = state["status"]
            if status == "done":
                layout.label(text="Done!", icon="CHECKMARK")
                # Show enhanced prompt if available
                enhanced = state.get("enhanced_prompt")
                if enhanced:
                    box = layout.box()
                    box.label(text="Enhanced prompt:", icon="INFO")
                    # Word-wrap long prompts across multiple lines
                    words = enhanced.split()
                    line = ""
                    for word in words:
                        if len(line) + len(word) + 1 > 40:
                            box.label(text=line)
                            line = word
                        else:
                            line = (line + " " + word).strip()
                    if line:
                        box.label(text=line)
            elif status == "error":
                box = layout.box()
                box.label(text=state.get("error", "Unknown error."), icon="ERROR")

            layout.operator("animoflow.generate", text="Generate", icon="PLAY")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    AnimoFlowWaypointItem,
    AnimoFlowTimelineSegment,
    ANIMOFLOW_UL_Waypoints,
    ANIMOFLOW_UL_TimelineSegments,
    AnimoFlowSceneProperties,
    ANIMOFLOW_PT_Main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.animoflow_props = bpy.props.PointerProperty(type=AnimoFlowSceneProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.animoflow_props
