#!/usr/bin/env bash
# Headless end-to-end test pipeline for the AnimoFlow Blender addon.
#
# What it does, every run:
#   1. Builds a fresh dist/animoflow_blender.zip
#   2. Launches Blender headless with an ISOLATED user-scripts dir
#      (your real installed addon is never touched)
#   3. Installs + enables the fresh zip, configures cloud prefs with the
#      HF token, then runs the test suites in tests/headless/:
#        - UI/registration checks (no network)
#        - live connect against the HF Space (/v1/tasks etc.)
#        - real generations (matrix-driven; burns ZeroGPU quota)
#
# Usage:
#   scripts/test_headless.sh              # full matrix (~7 generations)
#   SMOKE=1 scripts/test_headless.sh      # text/mdm only (1 generation)
#   MATRIX=none scripts/test_headless.sh  # ui + connect only, no GPU
#
# Token: env ANIMOFLOW_HF_TOKEN, or gitignored tests/headless/hf_token.txt.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

if [ -z "${ANIMOFLOW_HF_TOKEN:-}" ] && [ -f "$REPO/tests/headless/hf_token.txt" ]; then
    ANIMOFLOW_HF_TOKEN="$(cat "$REPO/tests/headless/hf_token.txt")"
fi
if [ -z "${ANIMOFLOW_HF_TOKEN:-}" ]; then
    echo "error: ANIMOFLOW_HF_TOKEN not set and tests/headless/hf_token.txt missing" >&2
    exit 2
fi
export ANIMOFLOW_HF_TOKEN

"$REPO/scripts/build.sh" >/dev/null
# The headless runner installs via the legacy addon path, so use the
# legacy-format zip (build.sh cleans dist/ first — the glob is unambiguous).
LEGACY_ZIP="$(ls "$REPO"/dist/animoflow_blender-*-legacy.zip | head -1)"
echo "[test-headless] built $LEGACY_ZIP"

# Isolate from the real user install: fresh scripts dir per run.
SANDBOX="$(mktemp -d /tmp/animoflow-blender-test.XXXXXX)"
export BLENDER_USER_SCRIPTS="$SANDBOX/scripts"
mkdir -p "$BLENDER_USER_SCRIPTS"
trap 'rm -rf "$SANDBOX"' EXIT
echo "[test-headless] sandbox: $BLENDER_USER_SCRIPTS"

MATRIX="${MATRIX:-full}"
if [ "${SMOKE:-0}" = "1" ]; then MATRIX="smoke"; fi
echo "[test-headless] matrix: $MATRIX"

# --online-mode is REQUIRED: Blender 5 gates network behind
# bpy.app.online_access, which --factory-startup defaults to OFF, and the
# addon guards its fetches on that flag ("enable Allow Online Access").
# Without this the live connect/generate suites fail before any request.
exec "$BLENDER" --background --factory-startup --online-mode --python-exit-code 1 \
    --python "$REPO/tests/headless/run_headless.py" -- \
    --zip "$LEGACY_ZIP" --matrix "$MATRIX"
