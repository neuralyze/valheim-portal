#!/usr/bin/env bash
# Builds EverybodyShim.dll - the BepInEx preloader patcher that converts
# ZRoutedRpc.Everybody from a 1.0.12 const back into a runtime static field.
# The diagnosis, the measurements behind it, and the patcher contract this
# targets are documented at the top of EverybodyShim.cs.
#
# Cross-compiled on Linux with Mono's compiler, same as tools/vrfixes/build.sh.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/EverybodyShim.dll}"

# Reference assemblies. The game's Managed directory supplies mscorlib/System
# (we compile -nostdlib against the runtime the patcher will actually run on);
# BepInEx/core supplies Mono.Cecil and BepInEx.Logging. Point VALHEIM_MANAGED
# and BEPINEX_CORE at an existing install to build against a different one.
#
# With neither set, the references are extracted from the named running server
# container, because that is the assembly set the patcher will actually face.
container="${VALHEIM_CONTAINER:-valheim-server-Ulfsland}"
refroot="${EVERYBODYSHIM_REFS:-${TMPDIR:-/tmp}/everybodyshim-refs}"
managed="${VALHEIM_MANAGED:-$refroot/managed}"
bepinex_core="${BEPINEX_CORE:-$refroot/core}"

if [[ ! -d $managed || ! -d $bepinex_core ]]; then
    docker inspect "$container" >/dev/null 2>&1 || {
        echo "no reference assemblies and container $container is not present." >&2
        echo "set VALHEIM_MANAGED and BEPINEX_CORE, or VALHEIM_CONTAINER." >&2
        exit 1
    }
    mkdir -p "$refroot"
    [[ -d $managed ]] ||
        docker cp "$container:/opt/valheim/server/valheim_server_Data/Managed" "$managed"
    [[ -d $bepinex_core ]] ||
        docker cp "$container:/opt/valheim/bepinex/BepInEx/core" "$bepinex_core"
fi

refs=()
for name in mscorlib.dll System.dll System.Core.dll; do
    [[ -f $managed/$name ]] || { echo "missing $managed/$name" >&2; exit 1; }
    refs+=("-r:$managed/$name")
done
# Mono.Cecil for the rewrite, BepInEx for ManualLogSource. Deliberately not the
# whole core directory: 0Harmony20.dll redefines HarmonyLib types and makes
# every reference ambiguous (CS0433), the same trap tools/vrfixes/build.sh hits.
for name in Mono.Cecil.dll BepInEx.dll; do
    [[ -f $bepinex_core/$name ]] || { echo "missing $bepinex_core/$name" >&2; exit 1; }
    refs+=("-r:$bepinex_core/$name")
done

sources=()
for cs in "$here"/*.cs; do sources+=("$cs"); done

# -nostdlib: the game ships its own mscorlib, and letting Mono's corlib in as
# well makes every core type ambiguous (CS0433/CS1685).
mcs -nostdlib -target:library -langversion:latest -nologo -optimize+ \
    -out:"$out" "${refs[@]}" "${sources[@]}"

[[ -f $out ]] || { echo "build produced no assembly" >&2; exit 1; }
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
