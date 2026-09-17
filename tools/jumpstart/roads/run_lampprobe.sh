#!/usr/bin/env bash
# Build and run LampProbe in a SANDBOX COPY of the Valheim dedicated server.
#
# Same shape as blueprints/run_piecegeometry.sh, and deliberately so: rsync the
# server tree (minus the live BepInEx directory) into scratch, drop in a BepInEx
# core plus exactly one plugin, run it on a throwaway world and port, kill it.
# The live server, world, config and mod profile are never touched -- this runs
# while Ulfsland is up and three writers are queued on the token.
#
# Needs a LOADED WORLD, because ZNetScene only publishes `m_prefabs` once the
# game scene is up. Expect ~30-60 s.
#
# THE SANDBOX IS PER-AGENT BY DEFAULT.  `/tmp/piecegeom/vh` is shared and its
# BepInEx tree can be owned by an earlier run's uid, in which case the clean
# step fails, the scan never runs, and a STALE output file still exists -- the
# exact silent-fallback defect `ribbon.zone_patches` documents. Validate by
# OUTPUT, never by exit status: the plugin SIGKILLs the process once the file is
# flushed, so a success is always raw_exit=137.
#
# Usage:
#   VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
#   OUT=/tmp/roads/lampprobe.jsonl \
#   [LAMPPROBE_MATCH=torch,lantern,...] [LAMPPROBE_NAMES=piece_groundtorch,...] \
#   [SANDBOX=/tmp/lampprobe/vh] [PORT=3461] \
#   tools/jumpstart/roads/run_lampprobe.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
OUT="${OUT:?set OUT to the output JSONL path}"
SANDBOX="${SANDBOX:-/tmp/lampprobe/vh}"
PORT="${PORT:-3461}"

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
  -r:"$MANAGED/SoftReferenceableAssets.dll" \
  -r:"$MANAGED/MagicaClothV2.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/LampProbe.dll" \
  "$HERE/LampProbe.cs"

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
  LAMPPROBE_OUT="$OUT" \
  LAMPPROBE_MATCH="${LAMPPROBE_MATCH:-torch,lantern,brazier,candle,lamp,sconce}" \
  LAMPPROBE_NAMES="${LAMPPROBE_NAMES:-}" \
  LAMPPROBE_TIMEOUT="${LAMPPROBE_TIMEOUT:-600}" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name lampprobe -port "$PORT" -world LampProbeScratch -password lampprobe1 \
    -savedir "$SANDBOX/scratch-save" >&2
rc=$?
set -e

[ -s "$OUT" ] || { echo "FAILED: no lamp probe output at $OUT (raw_exit=$rc)" >&2; exit 1; }
echo "lampprobe ok: $OUT ($(wc -l <"$OUT") rows, raw_exit=$rc)" >&2
