"""
Blender-side entry point for the headless test pipeline.

Invoked by scripts/test_headless.sh as:
    blender --background --factory-startup --python-exit-code 1 \
        --python tests/headless/run_headless.py -- --zip <zip> --matrix <name>

Installs the freshly built addon zip into the (isolated) user scripts
dir, enables it, configures cloud preferences from ANIMOFLOW_HF_TOKEN,
then runs every suite in this directory. Prints a report table and exits
non-zero on any failure.
"""
import argparse
import importlib.util
import os
import sys
import time
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = "animoflow_blender"
SPACE_URL = "https://animoflow-animoflow-demo.hf.space"


def _parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True)
    p.add_argument("--matrix", default="full", choices=["none", "smoke", "full"])
    return p.parse_args(argv)


def _install_addon(zip_path):
    # Uninstall any prior copy (paranoia — the sandbox starts empty).
    if ADDON in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_disable(module=ADDON)
    bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True)
    bpy.ops.preferences.addon_refresh()
    bpy.ops.preferences.addon_enable(module=ADDON)
    if ADDON not in bpy.context.preferences.addons:
        raise RuntimeError(f"addon_enable succeeded but '{ADDON}' not in addons")
    print(f"[headless] installed + enabled {ADDON} from {zip_path}")


def _configure_prefs(token):
    prefs = bpy.context.preferences.addons[ADDON].preferences
    prefs.server_mode = "cloud"
    prefs.api_key = token
    if not prefs.is_configured():
        raise RuntimeError("preferences not configured after setting token")
    base_url = prefs.get_base_url()
    if base_url != SPACE_URL:
        raise RuntimeError(f"unexpected base_url {base_url!r}")
    print(f"[headless] prefs configured for cloud mode → {base_url}")


def _load_suite(filename):
    path = os.path.join(HERE, filename)
    name = "headless_" + os.path.splitext(filename)[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    args = _parse_args()
    token = os.environ.get("ANIMOFLOW_HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ANIMOFLOW_HF_TOKEN not set")

    _install_addon(args.zip)
    _configure_prefs(token)

    ctx = {
        "token": token,
        "space_url": SPACE_URL,
        "matrix": args.matrix,
        "addon": ADDON,
    }

    suites = ["test_ui.py", "test_connect.py"]
    if args.matrix != "none":
        suites.append("test_generate.py")

    results = []  # (suite, name, ok, seconds, error)
    for suite_file in suites:
        mod = _load_suite(suite_file)
        for name, fn in mod.collect(ctx):
            t0 = time.time()
            try:
                fn(ctx)
                results.append((suite_file, name, True, time.time() - t0, ""))
                print(f"[headless] PASS {suite_file}::{name} ({time.time() - t0:.1f}s)")
            except Exception as exc:
                traceback.print_exc()
                results.append((suite_file, name, False, time.time() - t0, str(exc)))
                print(f"[headless] FAIL {suite_file}::{name}: {exc}")

    # ----- report -----
    print("\n" + "=" * 78)
    print(f"{'suite':<18} {'test':<34} {'result':<7} {'sec':>6}")
    print("-" * 78)
    n_fail = 0
    for suite_file, name, ok, secs, err in results:
        print(f"{suite_file:<18} {name:<34} {'PASS' if ok else 'FAIL':<7} {secs:>6.1f}")
        if not ok:
            n_fail += 1
    print("-" * 78)
    print(f"{len(results)} tests, {n_fail} failed  (matrix: {args.matrix})")
    for suite_file, name, ok, _, err in results:
        if not ok:
            print(f"  FAIL {suite_file}::{name}\n       {err[:300]}")
    print("=" * 78)

    if n_fail:
        sys.exit(1)
    print("[headless] ALL GREEN")


main()
