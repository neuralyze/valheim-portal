#!/usr/bin/env bash
# Builds NeuralyzeVRFixes.dll - the BepInEx plugin carrying our VR fixes.
#
# This source was recovered from /tmp on 2026-08-06 after a session built it
# there and nothing committed it. It is tracked here, beside the other two C#
# tools, so the fixes can be rebuilt without archaeology.
#
# Cross-compiled on Linux with Mono's compiler; no Windows host is involved.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/NeuralyzeVRFixes.dll}"

# Reference assemblies. The game's Managed directory supplies Unity and Valheim;
# a synchronized VR profile supplies BepInEx and ValheimVRMod. Override either
# when building against a different install.
managed="${VALHEIM_MANAGED:-/mnt/valheim-windows/Valheim3/valheim_Data/Managed}"
profile="${VR_PROFILE_ROOT:-/mnt/valheim-windows/ValheimProfileSync/profiles/Vangard--vangard-vr--vr/active}"

[[ -d $managed ]] || { echo "missing game assemblies: $managed" >&2; exit 1; }
[[ -d $profile ]] || { echo "missing VR profile: $profile" >&2; exit 1; }

refs=()
for dll in "$managed"/*.dll; do refs+=("-r:$dll"); done
# Only the two core assemblies the plugin compiles against. Referencing all of
# BepInEx/core pulls in 0Harmony20.dll, which redefines HarmonyLib.Harmony and
# makes every patch ambiguous (CS0433).
for name in 0Harmony.dll BepInEx.dll; do
    [[ -f $profile/BepInEx/core/$name ]] && refs+=("-r:$profile/BepInEx/core/$name")
done
for dll in "$profile"/BepInEx/plugins/ValheimVRMod.dll; do
    [[ -f $dll ]] && refs+=("-r:$dll")
done
# Older installs ship no UnityEngine.UIModule; the facade beside this script
# satisfies the reference then. Referencing both is an error, so it is a
# fallback rather than an addition.
if [[ ! -f $managed/UnityEngine.UIModule.dll && -f $here/UnityEngine.UIModule.dll ]]; then
    refs+=("-r:$here/UnityEngine.UIModule.dll")
fi

sources=()
for cs in "$here"/*.cs; do sources+=("$cs"); done

# -nowarn:CS0618 etc: the plugin deliberately uses deprecated Unity input APIs,
# which is what the game itself uses.
# -nostdlib: the game ships its own mscorlib/System, and letting Mono's corlib
# in as well makes every core type ambiguous (CS0433/CS1685). The same flag is
# why hostops/export_valheim_map_sources.sh compiles cleanly.
mcs -nostdlib -target:library -langversion:latest -nologo -optimize+ \
    -out:"$out" "${refs[@]}" "${sources[@]}" 2>&1 |
    grep -viE "warning CS(0168|0219|0414|0618|0649)" || true

[[ -f $out ]] || { echo "build produced no assembly" >&2; exit 1; }
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
