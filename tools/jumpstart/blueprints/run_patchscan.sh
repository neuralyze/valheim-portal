#!/usr/bin/env bash
# Build and run PatchScan in a SANDBOX COPY of the Valheim dedicated server.
#
# Mirrors tools/seedscan/run_scan.sh: rsync the server tree (minus the live
# BepInEx directory) into scratch, drop in a BepInEx core plus exactly one
# plugin, run it, kill it. No live mod, config, world or profile is written.
#
# Usage:
#   VH_SRC=/path/to/server/install \
#   SEED=Pirate68 REQ=/tmp/patch_req.tsv OUT=/tmp/patch.bin \
#   [SANDBOX=/tmp/patchscan/vh] \
#   tools/jumpstart/blueprints/run_patchscan.sh
#
# REQ is a TSV: <id>\t<centre_x>\t<centre_z>\t<half_extent_m>\t<step_m>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
SEED="${SEED:?set SEED to the seed string}"
REQ="${REQ:?set REQ to the patch request TSV}"
OUT="${OUT:?set OUT to the output .patch path}"
SANDBOX="${SANDBOX:-/tmp/patchscan/vh}"

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -f "$VH_SRC/BepInEx/core/BepInEx.Preloader.dll" ] || { echo "no BepInEx core in $VH_SRC" >&2; exit 1; }
[ -s "$REQ" ] || { echo "empty request file $REQ" >&2; exit 1; }

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
           "$MANAGED/netstandard.dll" "$SANDBOX/BepInEx/core/BepInEx.dll"; do
  [ -f "$dll" ] || { echo "missing $dll" >&2; exit 1; }
done

mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" \
  -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/PatchScan.dll" \
  "$HERE/PatchScan.cs"

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
  PATCHSCAN_SEED="$SEED" \
  PATCHSCAN_REQ="$REQ" \
  PATCHSCAN_OUT="$OUT" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name patchscan -port 3457 -world PatchScanScratch -password patchscan1 \
    -savedir "$SANDBOX/scratch-save" >&2
rc=$?
set -e

# The plugin SIGKILLs the process once the output is flushed, so exit status is
# always 137 on success. Validate by output, like run_scan.sh does.
[ -s "$OUT" ] || { echo "FAILED: no patch output at $OUT (raw_exit=$rc)" >&2; exit 1; }
echo "patchscan ok: $OUT ($(stat -c%s "$OUT") bytes, raw_exit=$rc)" >&2
