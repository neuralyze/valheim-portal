#!/usr/bin/env bash
# Builds NeuralyzeWorldSeed.dll against a running server's own assemblies, so the Harmony patch is
# always compiled against the Valheim build it will actually run on.
#
# Usage: ./build.sh CONTAINER   (e.g. valheim-server-MyWorld)
set -euo pipefail
CONTAINER=${1:-}
[[ -n $CONTAINER ]] || { echo "usage: $0 CONTAINER   (e.g. valheim-server-MyWorld)" >&2; exit 2; }
OUT=$(cd "$(dirname "$0")" && pwd)
REF=$(mktemp -d); trap 'rm -rf "$REF"' EXIT

for f in valheim_server_Data/Managed/assembly_valheim.dll \
         valheim_server_Data/Managed/UnityEngine.dll \
         valheim_server_Data/Managed/UnityEngine.CoreModule.dll \
         valheim_server_Data/Managed/netstandard.dll \
         BepInEx/core/0Harmony.dll \
         BepInEx/core/BepInEx.dll; do
  docker cp "$CONTAINER:/opt/valheim/bepinex/$f" "$REF/" 2>/dev/null || {
    echo "missing $f in $CONTAINER" >&2; exit 1; }
done

REFS=(); for f in "$REF"/*.dll; do REFS+=("-r:$f"); done
mcs -target:library -langversion:latest -nologo \
    -out:"$OUT/NeuralyzeWorldSeed.dll" "${REFS[@]}" "$OUT/WorldSeed.cs"
echo "built $OUT/NeuralyzeWorldSeed.dll ($(stat -c%s "$OUT/NeuralyzeWorldSeed.dll") bytes)"
echo "deploy to <world>/config_merged/bepinex/plugins/NeuralyzeWorldSeed/ and set ForcedSeedName"
