#!/usr/bin/env bash
# Builds ExplorationReporter.dll - the client-side BepInEx plugin that writes down what this player has
# uncovered on their own map.
#
# It has to be a client plugin: the revealed map is a grid held by the minimap in the player's own
# process and saved into their character file, and a dedicated server never sees it. Cross-compiled on
# Linux with Mono against the client's own assemblies, like tools/vrfixes.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/ExplorationReporter.dll}"

managed="${VALHEIM_MANAGED:-/mnt/valheim-windows/Valheim3/valheim_Data/Managed}"
# Any synchronized profile supplies BepInEx; the plugin uses no VR types, so a flat profile is fine.
bepinex="${VALHEIM_BEPINEX:-}"
if [[ -z $bepinex ]]; then
    for candidate in /mnt/valheim-windows/ValheimProfileSync/profiles/*/active/BepInEx/core; do
        [[ -f $candidate/BepInEx.dll ]] && bepinex="$candidate" && break
    done
fi

[[ -d $managed ]] || { echo "missing game assemblies: $managed" >&2; exit 1; }
[[ -n $bepinex && -d $bepinex ]] || { echo "missing BepInEx core; set VALHEIM_BEPINEX" >&2; exit 1; }

refs=()
for dll in "$managed"/*.dll; do refs+=("-r:$dll"); done
# Only these two: referencing all of BepInEx/core pulls in 0Harmony20.dll, which redefines
# HarmonyLib.Harmony and makes every patch ambiguous (CS0433).
for name in 0Harmony.dll BepInEx.dll; do
    [[ -f "$bepinex/$name" ]] && refs+=("-r:$bepinex/$name")
done

# -nostdlib: the game ships its own mscorlib/System; letting Mono's corlib in as well makes every core
# type ambiguous. Same reason as the other plugins here.
mcs -nostdlib -target:library -langversion:latest -optimize+ -nologo -out:"$out" "${refs[@]}" "$here/ExplorationReporter.cs" 2>&1 |
    grep -viE "warning CS(0168|0219|0414|0618|0649)" || true

[[ -f $out ]] || { echo "build produced no assembly" >&2; exit 1; }
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
