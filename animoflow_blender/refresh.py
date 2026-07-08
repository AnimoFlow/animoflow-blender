"""
Asynchronous server-data refresh — tasks/models/characters populate
without blocking the UI thread and without the user clicking anything.

Threading contract:
* The worker thread touches NOTHING in bpy except
  `bpy.app.timers.register` (documented thread-safe). It calls
  `panel.refresh_server_data` (pure network + module-level list
  swaps — enum callbacks only ever read those lists by reference) and
  `preferences.set_connection_status` (a module-level string).
* The redraw-tagging runs on the main thread via a one-shot timer.
  In `blender --background` there are no windows — the timer is a no-op
  there; the headless harness observes `panel._tasks_data` and the
  connection status directly.

Startup flow (fixes "I had to refresh the panel a few times"):
`__init__.register()` schedules `startup_refresh` on a 1 s one-shot
timer (register() itself runs in a restricted context where prefs are
not reliably readable). If preferences are configured, the catalog
loads automatically — the panel self-populates shortly after Blender
starts, no Connect click needed.
"""
import threading

import bpy

ADDON = __package__

_refresh_lock = threading.Lock()
_refreshing = False


def is_refreshing():
    return _refreshing


def _tag_redraw():
    """Main-thread only. Redraw the 3D viewport + Preferences panes."""
    try:
        wm = bpy.context.window_manager
    except Exception:
        return None
    if not wm:
        return None
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "PREFERENCES"):
                area.tag_redraw()
    return None  # one-shot timer


def refresh_async(base_url, task_id="text", api_key=""):
    """Kick a background refresh of characters/tasks/models. No-op if a
    refresh is already in flight. Returns immediately."""
    global _refreshing
    from . import panel
    from . import preferences as prefs_mod

    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    prefs_mod.set_connection_status("busy: connecting…")

    def _worker():
        global _refreshing
        try:
            panel.refresh_server_data(base_url, task_id=task_id, api_key=api_key)
            n_chars = len([c for c in panel._character_items if c[0] != "__loading__"])
            n_models = len([m for m in panel._model_items if m[0] != "__loading__"])
            n_tasks = len([t for t in panel._task_items if t[0] != "__loading__"])
            plural = lambda n, s: f"{n} {s}{'' if n == 1 else 's'}"
            prefs_mod.set_connection_status(
                "ok: connected — "
                f"{plural(n_tasks, 'task')}, {plural(n_models, 'model')}, "
                f"{plural(n_chars, 'character')} available"
            )
        except Exception as exc:
            prefs_mod.set_connection_status(f"err: {exc}")
        finally:
            with _refresh_lock:
                _refreshing = False
            # Redraw must happen on the main thread; timers.register is
            # the one bpy API that is safe to call from a worker thread.
            try:
                bpy.app.timers.register(_tag_redraw)
            except Exception:
                pass  # background mode teardown — nothing to redraw

    threading.Thread(target=_worker, daemon=True, name="animoflow-refresh").start()


def refresh_from_prefs(context=None):
    """Read preferences and refresh if configured. Safe to call from
    operators, prefs-update callbacks, and the startup timer."""
    ctx = context or bpy.context
    addon = ctx.preferences.addons.get(ADDON)
    if addon is None:
        return False
    prefs = addon.preferences
    base_url = prefs.get_base_url()
    if not base_url:
        return False
    api_key = prefs.api_key.strip() if prefs.server_mode == "cloud" else ""
    task_id = "text"
    scene = getattr(ctx, "scene", None)
    if scene is not None and hasattr(scene, "animoflow_props"):
        task_id = scene.animoflow_props.task or "text"
        if task_id == "__loading__":
            task_id = "text"
    refresh_async(base_url, task_id=task_id, api_key=api_key)
    return True


def startup_refresh():
    """One-shot bpy.app.timers callback scheduled by register()."""
    try:
        refresh_from_prefs()
    except Exception as exc:
        # Never let a startup failure break addon registration; surface
        # it in the connection banner instead of swallowing it.
        from . import preferences as prefs_mod
        prefs_mod.set_connection_status(f"err: {exc}")
    return None  # one-shot
