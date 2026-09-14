#!/usr/bin/env bash
# Re-solve EVERY jumpstart base placement for a world after a re-roll. One
# command, three stages, no manual survey:
#
#   1. tools/seedscan/run_scan.sh      8 m biome + height grid for the seed
#   2. tools/seedscan/run_locscan.sh   ZoneSystem location dump for the seed
#   3. solve_placements.py             coarse search -> 1 m patch scan -> verdict
#
# Stages 1 and 2 are cached by seed: re-running with the same seed reuses the
# artefacts, so an accidental second run costs seconds rather than a minute.
#
# Usage:
#   VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
#   WORLD=tools/jumpstart/worlds/Ulfsland SEED=Pirate68 \
#   [WORK=/tmp/jumpstart-solve] [SANDBOX=/tmp/bp_sandbox] [WRITE=1] \
#   tools/jumpstart/blueprints/resolve.sh
#
# WRITE=1 updates the `solved`/`shortlist` blocks in place. Without it the run
# is a dry run that prints the same verdicts and touches nothing.
#
# Nothing here writes to the live server: every stage runs the dedicated server
# out of an rsync'd sandbox on a throwaway world and port.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir (<VALHEIM_ROOT>/<World>/data/bepinex)}"
WORLD="${WORLD:?set WORLD to tools/jumpstart/worlds/<World>}"
SEED="${SEED:?set SEED to the world seed string}"
WORK="${WORK:-/tmp/jumpstart-solve}"
SANDBOX="${SANDBOX:-/tmp/jumpstart-solve/vh}"
STEP="${STEP:-8}"

HASH="$(printf '%s' "$SEED" | md5sum | cut -c1-12)"
GRID_DIR="$WORK/grid-$HASH"
LOC_DIR="$WORK/loc"
SEEDFILE="$WORK/seed-$HASH.txt"
mkdir -p "$WORK" "$GRID_DIR" "$LOC_DIR"
printf '%s\n' "$SEED" > "$SEEDFILE"

if [ -s "$GRID_DIR/00000.biome" ]; then
  echo "[1/3] grid cached: $GRID_DIR/00000.biome" >&2
else
  echo "[1/3] scanning ${STEP} m biome+height grid for $SEED (rivers included)" >&2
  # PREGEN=1 is NOT optional. run_scan.sh defaults it to 0, and without it
  # WorldGenerator.AddRivers reads an empty river dictionary, so the height plane
  # has no rivers or lakes at all. MEASURED on Pirate68: the river-free grid
  # reports 33.97 m at (-275, 260) where the truth is 28.9 m -- it calls a river
  # bed dry land by 5 m, and the solver approved sites on that basis. The extra
  # cost is 21 s against 15 s for the whole world.
  VH_SRC="$VH_SRC" SEEDS="$SEEDFILE" OUT="$GRID_DIR" SANDBOX="$SANDBOX" \
    STEP="$STEP" HEIGHT=1 PREGEN=1 "$REPO/tools/seedscan/run_scan.sh" >&2
fi

if [ -s "$LOC_DIR/$HASH.json" ]; then
  echo "[2/3] locations cached: $LOC_DIR/$HASH.json" >&2
else
  echo "[2/3] dumping ZoneSystem locations for $SEED (one world boot, ~1 min)" >&2
  VH_SRC="$VH_SRC" SEEDS="$SEEDFILE" OUT="$LOC_DIR" SANDBOX="$SANDBOX" \
    "$REPO/tools/seedscan/run_locscan.sh" >&2
fi

echo "[3/3] solving placements" >&2
exec python3 "$HERE/solve_placements.py" \
  --world "$WORLD" \
  --seed "$SEED" \
  --grid "$GRID_DIR/00000.biome" \
  --locations "$LOC_DIR/$HASH.json" \
  --vh-src "$VH_SRC" \
  --patch "$WORK/patch-$HASH.bin" \
  --patch-sandbox "$SANDBOX" \
  ${WRITE:+--write} \
  "$@"
