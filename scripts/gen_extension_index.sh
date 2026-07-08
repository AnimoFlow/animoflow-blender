#!/bin/bash
# Generate a self-hosted Blender extension repository index from dist/.
#
# Wraps `blender --command extension server-generate --repo-dir=<dir>`:
# copies the extension zip(s) from dist/ into dist/extension-repo/ and
# generates index.json next to them. Users add the index URL as a remote
# repository in Blender 4.2+ (Preferences > Get Extensions > Repositories)
# and get auto-updates.
#
# PUBLISHING: at the public flip, dist/extension-repo/ is published to the
# webui at /extensions/ — i.e. the repository URL animators add is
#   https://animoflow.ai/extensions/index.json
# (swap the host for the production domain when it goes live).
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$REPO_ROOT/dist"
OUT="$DIST/extension-repo"

# --- Find a Blender >= 4.2 binary (server-generate needs it) ---------------
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
        if [ "$(printf '%s\n' "4.2" "$ver" | sort -V | head -1)" = "4.2" ]; then
            echo "$b"
            return 0
        fi
    done
    return 1
}

if ! BLENDER="$(find_blender)"; then
    echo "error: no Blender >= 4.2 binary found (checked /Applications/Blender*.app and PATH)." >&2
    echo "       'extension server-generate' requires Blender 4.2+ — install it, then re-run." >&2
    exit 1
fi

# Extension zips only — the -legacy.zip is bl_info-format and does not
# belong in an extension repository.
EXT_ZIPS=()
for z in "$DIST"/animoflow_blender-*.zip; do
    case "$z" in
        *-legacy.zip) ;;
        *) [ -f "$z" ] && EXT_ZIPS+=("$z") ;;
    esac
done
if [ "${#EXT_ZIPS[@]}" -eq 0 ]; then
    echo "error: no extension zip in $DIST — run scripts/build.sh first." >&2
    exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"
cp "${EXT_ZIPS[@]}" "$OUT/"

echo "[gen-index] using Blender: $BLENDER"
"$BLENDER" --command extension server-generate --repo-dir="$OUT"

echo "Generated: $OUT/index.json"
echo "Publish dist/extension-repo/ to the webui at /extensions/ (public flip)."
