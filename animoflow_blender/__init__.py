"""
AnimoFlow Blender Addon
Generate motion animations from text — powered by AnimoFlow.
"""

bl_info = {
    "name": "AnimoFlow",
    "author": "AnimoFlow",
    "version": (0, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > AnimoFlow",
    "description": "Generate motion from text, trajectories, waypoints and timelines via AnimoFlow",
    "category": "Animation",
    "doc_url": "https://animoflow.ai",
}

from . import operators, panel, preferences, refresh

import bpy


def register():
    preferences.register()
    operators.register()
    panel.register()
    # Auto-populate tasks/models/characters shortly after startup —
    # register() itself runs in a restricted context, so defer via a
    # one-shot timer. No manual Connect click needed when a token is
    # already saved.
    bpy.app.timers.register(refresh.startup_refresh, first_interval=1.0)


def unregister():
    if bpy.app.timers.is_registered(refresh.startup_refresh):
        bpy.app.timers.unregister(refresh.startup_refresh)
    panel.unregister()
    operators.unregister()
    preferences.unregister()
