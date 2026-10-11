#!/usr/bin/env bash
# Fetch and build the Micropolis (SimCity Classic) engine used by mayor/micropolis.
# The engine is GPL-3.0 (MicropolisCore via gymtropolis); it is downloaded, not vendored here.
# Needs: git, g++ (Linux or WSL). Usage: bash tools/setup_micropolis.sh [target_dir]
set -euo pipefail
DIR="${1:-third_party/gymtropolis}"
COMMIT=6237efe2d1106951268d1bbf86003d2ab56c244e   # pinned: tested with this revision
if [ ! -d "$DIR/.git" ]; then
  git clone https://github.com/elmihailol/gymtropolis "$DIR"
fi
git -C "$DIR" fetch --depth 1 origin "$COMMIT" 2>/dev/null || true
git -C "$DIR" checkout -q "$COMMIT"
# add the budget functions to the engine's C API (once)
HERE="$(cd "$(dirname "$0")" && pwd)"
if ! grep -q "mp_update_fund_effects" "$DIR/gymtropolis/csrc/capi.cpp"; then
  git -C "$DIR" checkout -q -- gymtropolis/csrc/capi.cpp   # drop an older version of the patch
  cat "$HERE/micropolis_patches/budget_capi.cpp" >> "$DIR/gymtropolis/csrc/capi.cpp"
fi
make -C "$DIR"
echo "engine ready: $DIR/gymtropolis/_engine.so"
