#!/usr/bin/env bash
# Copy the v12 sound engine from the caltrain-sounds repo (unchanged, plus one header line naming where it came from).
#   pi/engine/sync.sh [path to caltrain-sounds] [commit]
# tests/test_engine_sync.py checks the copies still match when that repo is on this computer.
set -euo pipefail
SRC="${1:-/workspace/projects/caltrain-sounds}"
REV="$(git -C "$SRC" rev-parse --short HEAD)"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in app/engine.js app/buses.js app/samples/load.js; do
  out="$HERE/$(basename "$f")"
  { echo "// vendored from caltrain-sounds $f at $REV (pi/engine/sync.sh); do not edit here"; cat "$SRC/$f"; } > "$out"
done
echo "synced from $SRC at $REV"
