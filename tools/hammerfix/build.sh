#!/usr/bin/env bash
# Builds HammerFix.dll - the BepInEx plugin that repairs the hammer build menu.
# The diagnosis, the measurements behind it and every deliberate non-choice are
# documented at the top of HammerFix.cs.
#
# Cross-compiled on Linux with Mono's compiler, same as tools/everybodyshim and
# tools/vrfixes. -nostdlib is REQUIRED: the game ships its own mscorlib and
# System.Core, and letting Mono's in as well makes HashSet<T>, Expression and
# the core types ambiguous (CS0433/CS1685) - the same trap the probe hit first.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/HammerFix.dll}"

# With neither VALHEIM_MANAGED nor BEPINEX_CORE set, the references are taken
# from the named server container, because that is the assembly set the plugin
# will actually face. A local install works too - point them at it.
container="${VALHEIM_CONTAINER:-valheim-server-Ulfsland}"
refroot="${HAMMERFIX_REFS:-${TMPDIR:-/tmp}/hammerfix-refs}"
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
for name in mscorlib.dll System.dll System.Core.dll netstandard.dll \
            assembly_valheim.dll assembly_utils.dll assembly_guiutils.dll \
            UnityEngine.dll UnityEngine.CoreModule.dll UnityEngine.UI.dll; do
    [[ -f $managed/$name ]] || { echo "missing $managed/$name" >&2; exit 1; }
    refs+=("-r:$managed/$name")
done
# BepInEx for BaseUnityPlugin/ManualLogSource/Config, 0Harmony for the patches.
# Deliberately not the whole core directory: 0Harmony20.dll redefines the
# HarmonyLib types and makes every reference ambiguous (CS0433).
for name in BepInEx.dll 0Harmony.dll; do
    [[ -f $bepinex_core/$name ]] || { echo "missing $bepinex_core/$name" >&2; exit 1; }
    refs+=("-r:$bepinex_core/$name")
done

sources=()
for cs in "$here"/*.cs; do sources+=("$cs"); done

mcs -nostdlib -target:library -langversion:latest -nologo -optimize+ \
    -out:"$out" "${refs[@]}" "${sources[@]}"

[[ -f $out ]] || { echo "build produced no assembly" >&2; exit 1; }
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
