#!/usr/bin/env bash
# Run SeedScan inside a SANDBOX COPY of the Valheim dedicated server.
#
# It never touches the live install: rsync mirrors the server tree (minus the
# live BepInEx directory) into a scratch dir, then copies ONLY BepInEx/core and
# BepInEx/config into the sandbox with a plugins directory containing nothing
# but SeedScan.dll. No live mod, config, world or profile is read-write.
#
# Usage:
#   VH_SRC=/path/to/server/install \
#   SEEDS=/tmp/seeds.txt \
#   OUT=/tmp/seedscan/out \
#   [SANDBOX=/tmp/seedscan/vh] [STEP=128] [EXTENT=10496] [PREGEN=0] [HEIGHT=0] \
#   tools/seedscan/run_scan.sh
#
# VH_SRC is the directory containing valheim_server.x86_64, UnityPlayer.so,
# valheim_server_Data and BepInEx (for the portal hosts:
# <VALHEIM_ROOT>/<World>/data/bepinex).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
SEEDS="${SEEDS:?set SEEDS to a file of seed strings, one per line}"
OUT="${OUT:?set OUT to an output directory}"
SANDBOX="${SANDBOX:-/tmp/seedscan/vh}"
STEP="${STEP:-128}"
EXTENT="${EXTENT:-10496}"
PREGEN="${PREGEN:-0}"
HEIGHT="${HEIGHT:-0}"

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -f "$VH_SRC/BepInEx/core/BepInEx.Preloader.dll" ] || { echo "no BepInEx core in $VH_SRC" >&2; exit 1; }

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  echo "staging sandbox copy in $SANDBOX (mirror of $VH_SRC, live BepInEx excluded)"
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
fi

# Minimal BepInEx: core loader only, and a plugins dir holding just our plugin.
rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"

MANAGED="$SANDBOX/valheim_server_Data/Managed" \
BEPINEX_CORE="$SANDBOX/BepInEx/core" \
  "$HERE/build.sh" "$SANDBOX/BepInEx/plugins" >/dev/null

mkdir -p "$OUT" "$SANDBOX/scratch-save"
rm -f "$OUT"/*.biome "$OUT"/index.tsv "$OUT"/seedscan.log

cd "$SANDBOX"
set +e
env \
  DOORSTOP_ENABLED=1 \
  DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
  LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
  LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
  SteamAppId=892970 \
  SEEDSCAN_SEEDS="$SEEDS" \
  SEEDSCAN_OUT="$OUT" \
  SEEDSCAN_STEP="$STEP" \
  SEEDSCAN_EXTENT="$EXTENT" \
  SEEDSCAN_PREGEN="$PREGEN" \
  SEEDSCAN_HEIGHT="$HEIGHT" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name seedscan -port 3456 -world SeedScanScratch -password seedscan1 \
    -savedir "$SANDBOX/scratch-save"
rc=$?
set -e

# The plugin hard-kills the process once outputs are flushed (Unity's shutdown
# path deadlocks if called from Awake in -batchmode), so exit status is SIGKILL
# on success. Validate by output instead.
want=$(grep -cvE '^\s*(#|$)' "$SEEDS")
got=$(ls -1 "$OUT"/*.biome 2>/dev/null | wc -l)
echo "raw_exit=$rc  seeds=$want  grids=$got"
[ "$got" -eq "$want" ] || { echo "FAILED: expected $want grids, got $got" >&2; exit 1; }
echo "ok"
