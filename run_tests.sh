#!/usr/bin/env bash
# Run animoflow-blender unit tests without triggering the Blender addon
# __init__.py. pytest loads parent package __init__.py before each test;
# since this repo IS the Blender addon package, we work around it by
# copying only the pure modules (no __init__.py) into a temp dir laid out
# the way the tests expect (tests/../animoflow_blender/<module>.py).
set -e
REPO=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

mkdir -p "$TMP/animoflow_blender"
cp "$REPO/animoflow_blender/api.py"      "$TMP/animoflow_blender/"
cp "$REPO/animoflow_blender/geometry.py" "$TMP/animoflow_blender/"
cp "$REPO/animoflow_blender/payload.py"  "$TMP/animoflow_blender/"
cp -r "$REPO/tests" "$TMP/"
rm -rf "$TMP/tests/headless"  # headless suite runs inside real Blender only
cat > "$TMP/conftest.py" << 'EOF'
import sys
from unittest.mock import MagicMock
if "bpy" not in sys.modules:
    bpy = MagicMock()
    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy.props
    sys.modules["bpy.types"] = bpy.types
    sys.modules["bpy.utils"] = bpy.utils
EOF

cd "$TMP"
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import pytest" 2>/dev/null; then
    echo "error: '$PYTHON' has no pytest — set PYTHON=/path/to/python-with-pytest" >&2
    exit 2
fi
"$PYTHON" -m pytest tests/ -v "$@"
