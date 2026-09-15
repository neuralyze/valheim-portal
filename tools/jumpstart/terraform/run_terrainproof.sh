#!/usr/bin/env bash
# Build and run TerrainProof in a SANDBOX COPY of the Valheim dedicated server,
# against a COPY of the world being measured.
#
# Mirrors tools/jumpstart/blueprints/run_patchscan.sh: rsync the server tree
# (minus the live BepInEx directory) into scratch, drop in a BepInEx core plus
# exactly one plugin, run it, kill it. The world is copied too, so the live save
# is never opened by a second process - the sandbox server would otherwise take
# the save's file lock and write its own generation over it.
#
# Usage:
#   VH_SRC=/path/to/server/install \
#   WORLD_SRC=/path/to/worlds_local/Ulfsland WORLD=Ulfsland \
#   ZONES="4,5;4,6" POINTS="centre,241.5,353.5;outside,241.5,300" \
#   OUT=/tmp/terrainproof.tsv \
#   tools/jumpstart/terraform/run_terrainproof.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
WORLD_SRC="${WORLD_SRC:?set WORLD_SRC to the world directory to copy}"
WORLD="${WORLD:?set WORLD to the save name}"
ZONES="${ZONES:?set ZONES}"
POINTS="${POINTS:?set POINTS}"
OUT="${OUT:?set OUT to the output path}"
SANDBOX="${SANDBOX:-/tmp/terrainproof/vh}"

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -d "$WORLD_SRC" ] || { echo "no world directory $WORLD_SRC" >&2; exit 1; }

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  echo "staging sandbox copy in $SANDBOX (mirror of $VH_SRC, live BepInEx excluded)" >&2
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
fi

rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"

SAVE="$SANDBOX/proof-save"
rm -rf "$SAVE"
mkdir -p "$SAVE/worlds_local"
cp -a "$WORLD_SRC" "$SAVE/worlds_local/$WORLD"

MANAGED="$SANDBOX/valheim_server_Data/Managed"
mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" \
  -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/TerrainProof.dll" \
  "$HERE/TerrainProof.cs"

rm -f "$OUT"
mkdir -p "$(dirname "$OUT")"

cd "$SANDBOX"
set +e
env \
  DOORSTOP_ENABLED=1 \
  DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
  LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
  LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
  SteamAppId=892970 \
  TERRAINPROOF_ZONES="$ZONES" \
  TERRAINPROOF_POINTS="$POINTS" \
  TERRAINPROOF_OUT="$OUT" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name terrainproof -port 3458 -world "$WORLD" -password terrainproof1 \
    -savedir "$SAVE" >&2
rc=$?
set -e

[ -s "$OUT" ] || { echo "FAILED: no proof output at $OUT (raw_exit=$rc)" >&2; exit 1; }
echo "terrainproof ok: $OUT (raw_exit=$rc)" >&2
cat "$OUT"
