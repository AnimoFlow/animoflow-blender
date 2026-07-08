#!/bin/bash
# Build the two distributable zips for the AnimoFlow Blender addon:
#
#   dist/animoflow_blender-<version>.zip         Blender 4.2+ extension format
#                                                (blender_manifest.toml at zip ROOT)
#   dist/animoflow_blender-<version>-legacy.zip  Blender 3.6-4.1 legacy addon
#                                                (animoflow_blender/ folder inside,
#                                                bl_info-based)
#
# Version is single-sourced from animoflow_blender/blender_manifest.toml.
# If a Blender >= 4.2 binary is found, the extension zip is built AND
# validated with Blender's own tooling; otherwise a plain zip fallback is
# used and a warning printed.
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$REPO_ROOT/animoflow_blender"
DIST="$REPO_ROOT/dist"
MANIFEST="$PKG/blender_manifest.toml"

# --- Version: single source of truth is the extension manifest ------------
VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$MANIFEST" | head -1)"
if [ -z "$VERSION" ]; then
    echo "error: could not parse version from $MANIFEST" >&2
    exit 1
fi

EXT_ZIP="$DIST/animoflow_blender-$VERSION.zip"
LEGACY_ZIP="$DIST/animoflow_blender-$VERSION-legacy.zip"

mkdir -p "$DIST"
rm -f "$DIST"/animoflow_blender*.zip

# --- Find a Blender >= 4.2 binary (needed for `--command extension`) ------
find_blender() {
    local candidates=()
    local app
    for app in /Applications/Blender*.app/Contents/MacOS/Blender; do
        [ -x "$app" ] && candidates+=("$app")
    done
    if command -v blender >/dev/null 2>&1; then
        candidates+=("$(command -v blender)")
    fi
    local b ver
    for b in "${candidates[@]}"; do
        ver="$("$b" --version 2>/dev/null | head -1 | awk '{print $2}')"
        [ -n "$ver" ] || continue
        # version >= 4.2 ?
        if [ "$(printf '%s\n' "4.2" "$ver" | sort -V | head -1)" = "4.2" ]; then
            echo "$b"
            return 0
        fi
    done
    return 1
}

# --- Extension zip (Blender 4.2+) ------------------------------------------
if BLENDER="$(find_blender)"; then
    echo "[build] using Blender: $BLENDER"
    "$BLENDER" --command extension validate "$PKG"
    "$BLENDER" --command extension build \
        --source-dir "$PKG" \
        --output-filepath "$EXT_ZIP"
else
    echo "[build] WARNING: no Blender >= 4.2 binary found — building the" >&2
    echo "[build] extension zip with plain zip; it was NOT validated by" >&2
    echo "[build] 'blender --command extension validate'." >&2
    # Extension format: manifest must sit at the ZIP ROOT — zip the files
    # INSIDE animoflow_blender/, not the folder itself.
    (cd "$PKG" && zip -r "$EXT_ZIP" . \
        --exclude "*.pyc" --exclude "*__pycache__*" --exclude ".*")
fi
echo "Built: $EXT_ZIP (Blender 4.2+ extension)"

# --- Legacy zip (Blender 3.6-4.1, bl_info-based) ---------------------------
cd "$REPO_ROOT"
zip -r "$LEGACY_ZIP" animoflow_blender/ \
    --exclude "*.pyc" --exclude "*__pycache__*" --exclude "*/.*"
echo "Built: $LEGACY_ZIP (Blender 3.6-4.1 legacy addon)"
