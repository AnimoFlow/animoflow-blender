"""
Headless suite: live connect/refresh against the HF Space (no GPU).
Validates the /v1 catalog endpoints through the addon's own refresh path.
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

EXPECTED_TASKS = {"text", "trajectory", "timeline", "waypoint"}
EXPECTED_MODELS = {
    "text": {"mdm", "kimodo", "momask"},
    "trajectory": {"priormdm", "kimodo"},
    "timeline": {"priormdm"},
    "waypoint": {"kimodo"},
}


def test_refresh_server_data(ctx):
    panel = helpers.addon_sub("panel")
    panel.refresh_server_data(ctx["space_url"], task_id="text",
                              api_key=ctx["token"])
    task_ids = {t["id"] for t in panel._tasks_data}
    missing = EXPECTED_TASKS - task_ids
    assert not missing, f"tasks missing from /v1/tasks: {missing}"


def test_characters_loaded(ctx):
    panel = helpers.addon_sub("panel")
    chars = [c[0] for c in panel._character_items]
    assert "Y_bot" in chars, f"Y_bot missing from characters: {chars}"
    assert chars[0] == "Y_bot", f"Y_bot must be first (default), got {chars[0]}"


def test_models_per_task(ctx):
    panel = helpers.addon_sub("panel")
    for task_id, expected in EXPECTED_MODELS.items():
        panel.refresh_models_for_task(task_id)
        got = {m[0] for m in panel._model_items if m[0] != "__loading__"}
        missing = expected - got
        assert not missing, f"task {task_id}: models missing {missing} (got {got})"
    # Leave the enum on the text task for downstream suites.
    panel.refresh_models_for_task("text")


def _wait_for_status(prefs_mod, timeout_s=20.0):
    """Poll the connection status until the async refresh worker lands
    on ok:/err:. The worker thread sets the status directly (no timer
    needed), so this works in --background too."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = prefs_mod.get_connection_status()
        if status.startswith(("ok:", "err:")):
            return status
        time.sleep(0.2)
    return prefs_mod.get_connection_status()


def test_connect_operator_async(ctx):
    prefs_mod = helpers.addon_sub("preferences")
    prefs_mod.set_connection_status("")
    result = bpy.ops.animoflow.connect()
    assert result == {"FINISHED"}, f"connect returned {result}"
    status = _wait_for_status(prefs_mod)
    assert status.startswith("ok:"), f"connection status not ok: {status!r}"
    print(f"[headless]   status: {status}")


def test_startup_autorefresh_path(ctx):
    """The exact chain register() schedules: startup_refresh → thread →
    catalog populated + status ok, with no operator involved. Fixes
    'I had to refresh the panel a few times'."""
    panel = helpers.addon_sub("panel")
    prefs_mod = helpers.addon_sub("preferences")
    refresh = helpers.addon_sub("refresh")

    # Simulate a cold panel: wipe the catalog and status.
    panel._tasks_data = []
    panel._task_items = [("__loading__", "Connect to server first", "")]
    prefs_mod.set_connection_status("")

    refresh.startup_refresh()  # what the register() timer fires
    status = _wait_for_status(prefs_mod)
    assert status.startswith("ok:"), f"startup refresh failed: {status!r}"
    assert panel._tasks_data, "tasks catalog empty after startup refresh"
    assert not refresh.is_refreshing(), "refresh flag stuck"


def collect(ctx):
    return [
        ("refresh_server_data", test_refresh_server_data),
        ("characters_loaded", test_characters_loaded),
        ("models_per_task", test_models_per_task),
        ("connect_operator_async", test_connect_operator_async),
        ("startup_autorefresh", test_startup_autorefresh_path),
    ]
