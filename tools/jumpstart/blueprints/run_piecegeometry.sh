#!/usr/bin/env bash
# Build and run PieceGeometry in a SANDBOX COPY of the Valheim dedicated server.
#
# Same shape as run_patchscan.sh: rsync the server tree (minus the live BepInEx
# directory) into scratch, drop in a BepInEx core plus exactly one plugin, run
# it on a throwaway world and port, kill it. No live mod, config, world or
# profile is written, and the live server is never touched.
#
# Unlike PatchScan this needs a LOADED WORLD, because ZNetScene only publishes
# `m_prefabs` once the game scene is up. Expect ~30-60 s.
#
# Usage:
#   VH_SRC=/path/to/server/install OUT=/tmp/piece_geometry.tsv \
#   [SANDBOX=/tmp/piecegeom/vh] [PORT=3459] \
#   tools/jumpstart/blueprints/run_piecegeometry.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
OUT="${OUT:?set OUT to the output TSV path}"
SANDBOX="${SANDBOX:-/tmp/piecegeom/vh}"
PORT="${PORT:-3459}"

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -f "$VH_SRC/BepInEx/core/BepInEx.Preloader.dll" ] || { echo "no BepInEx core in $VH_SRC" >&2; exit 1; }

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  echo "staging sandbox copy in $SANDBOX (mirror of $VH_SRC, live BepInEx excluded)" >&2
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
fi

rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"

MANAGED="$SANDBOX/valheim_server_Data/Managed"
for dll in "$MANAGED/assembly_valheim.dll" "$MANAGED/UnityEngine.CoreModule.dll" \
           "$MANAGED/UnityEngine.PhysicsModule.dll" "$MANAGED/netstandard.dll" \
           "$SANDBOX/BepInEx/core/BepInEx.dll"; do
  [ -f "$dll" ] || { echo "missing $dll" >&2; exit 1; }
done

mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" \
  -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.PhysicsModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/PieceGeometry.dll" \
  "$HERE/PieceGeometry.cs"

rm -f "$OUT"
mkdir -p "$(dirname "$OUT")" "$SANDBOX/scratch-save"

cd "$SANDBOX"
set +e
env \
  DOORSTOP_ENABLED=1 \
  DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
  LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
  LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
  SteamAppId=892970 \
  PIECEGEOM_OUT="$OUT" \
  PIECEGEOM_TIMEOUT="${PIECEGEOM_TIMEOUT:-600}" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name piecegeom -port "$PORT" -world PieceGeomScratch -password piecegeom1 \
    -savedir "$SANDBOX/scratch-save" >&2
rc=$?
set -e

# The plugin SIGKILLs the process once the output is flushed, so exit status is
# always 137 on success. Validate by output, like run_patchscan.sh does.
[ -s "$OUT" ] || { echo "FAILED: no geometry output at $OUT (raw_exit=$rc)" >&2; exit 1; }
echo "piecegeom ok: $OUT ($(wc -l <"$OUT") rows, raw_exit=$rc)" >&2
