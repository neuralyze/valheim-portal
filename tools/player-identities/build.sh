#!/usr/bin/env bash
# Builds PlayerIdentities.dll - the server-side BepInEx plugin that writes down which player id
# belongs to which character name.
#
# It runs on the dedicated server, not the client: the pairing it records only exists in a running
# game, and only the server sees every player. Cross-compiled on Linux with Mono; no Windows host is
# involved, same as the other plugins here.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/PlayerIdentities.dll}"

# The server's own assemblies, so the plugin compiles against exactly what it will run inside.
managed="${VALHEIM_SERVER_MANAGED:-/media/big4/projects/game/valheim/Vangard/data/server/valheim_server_Data/Managed}"
bepinex="${VALHEIM_SERVER_BEPINEX:-/media/big4/projects/game/valheim/Vangard/data/bepinex/BepInEx/core}"

[[ -d $managed ]] || { echo "missing server assemblies: $managed" >&2; exit 1; }
[[ -d $bepinex ]] || { echo "missing server BepInEx core: $bepinex" >&2; exit 1; }

refs=()
for dll in "$managed"/*.dll; do refs+=("-r:$dll"); done
# Only these two from BepInEx: referencing all of core pulls in 0Harmony20.dll, which redefines
# HarmonyLib.Harmony and makes every patch ambiguous (CS0433). Learned on the VR plugin.
for name in 0Harmony.dll BepInEx.dll; do
    [[ -f "$bepinex/$name" ]] && refs+=("-r:$bepinex/$name")
done

# -nostdlib: the game ships its own mscorlib/System, and letting Mono's corlib in as well makes every
# core type ambiguous (CS0433/CS1685). Same flag, same reason, as tools/vrfixes/build.sh.
mcs -nostdlib -target:library -langversion:latest -optimize+ -nologo -out:"$out" "${refs[@]}" "$here/PlayerIdentities.cs" 2>&1 |
    grep -viE "warning CS(0168|0219|0414|0618|0649)" || true

[[ -f $out ]] || { echo "build produced no assembly" >&2; exit 1; }
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
