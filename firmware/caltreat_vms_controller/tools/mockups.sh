#!/bin/sh
# Build the host simulator from the firmware's page engine, run it, render LED-look PNG/GIF mockups.
# Usage: tools/mockups.sh OUTDIR     (needs g++ and Python 3 with Pillow)
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$1"
out=$(cd "$1" && pwd)
frames="$here/.pio/sim"   # build dir, ignored by git
mkdir -p "$frames"
g++ -std=c++17 -O2 -Wall -Wextra -I"$here/include" "$here/tools/sim/sim.cpp" "$here/src/vms_core.cpp" -o "$frames/sim"
"$frames/sim" "$frames"
python3 "$here/tools/mockups.py" "$frames" "$out"
