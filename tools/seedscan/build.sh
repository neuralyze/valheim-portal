#!/usr/bin/env bash
# Compile SeedScan.cs + LocScan.cs into one BepInEx plugin assembly.
# SeedScan queries WorldGenerator (biomes/heights); LocScan queries ZoneSystem
# (location placement). Both idle unless their env vars are set.
#
# Usage:
#   MANAGED=/path/to/valheim_server_Data/Managed \
#   BEPINEX_CORE=/path/to/BepInEx/core \
#   tools/seedscan/build.sh [outdir]
#
# Requires: mcs (mono-devel). Output: <outdir>/SeedScan.dll (default /tmp/seedscan/build).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGED="${MANAGED:?set MANAGED to the valheim_server_Data/Managed directory}"
BEPINEX_CORE="${BEPINEX_CORE:?set BEPINEX_CORE to the BepInEx/core directory}"
OUT="${1:-/tmp/seedscan/build}"

mkdir -p "$OUT"

for dll in "$MANAGED/assembly_valheim.dll" "$MANAGED/assembly_utils.dll" \
           "$MANAGED/UnityEngine.CoreModule.dll" "$MANAGED/netstandard.dll" \
           "$MANAGED/SoftReferenceableAssets.dll" \
           "$BEPINEX_CORE/BepInEx.dll"; do
  [ -f "$dll" ] || { echo "missing $dll" >&2; exit 1; }
done

mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" \
  -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" \
  -r:"$MANAGED/SoftReferenceableAssets.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$BEPINEX_CORE/BepInEx.dll" \
  -r:"$BEPINEX_CORE/0Harmony.dll" \
  -out:"$OUT/SeedScan.dll" \
  "$HERE/SeedScan.cs" "$HERE/LocScan.cs"

echo "built $OUT/SeedScan.dll"
