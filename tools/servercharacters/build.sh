#!/usr/bin/env bash
# Builds ServerCharacters 1.4.17 from upstream master and packages it as a custom
# override for the pinned Smoothbrain-ServerCharacters 1.4.16 Thunderstore package.
#
# Why this exists instead of a binary patch: Valheim 1.0 changed six things this mod
# depends on, not one. Upstream fixed all six in blaxxun-boop/ServerCharacters@bb7d3cd6
# ("fix for deep north", 2026-09-09) and bumped the version to 1.4.17, but has never
# published that build - Thunderstore's newest is still 1.4.16 from 2025-05-02. So the
# fix exists; only the release does not. Building it is strictly better than patching
# the released binary, which would repair one call site and leave five live faults.
# The full accounting is in tools/modpatches/README.md.
#
# Output: ServerCharacters.zip, laid out the way tools/valheim_mods.py `custom-add`
# expects (BepInEx/plugins/<files>), whose install key is derived from the archive stem
# and therefore lands on top of the stock plugin directory rather than beside it.
#
# Cross-compiled on Linux. The mod needs C# 10 and 12 features, so mcs cannot build it
# the way tools/everybodyshim and tools/vrfixes are built; this uses the .NET SDK in a
# throwaway container and leaves no toolchain on the host.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/ServerCharacters.zip}"
work="${SERVERCHARACTERS_WORK:-${TMPDIR:-/tmp}/servercharacters-build}"
commit="${SERVERCHARACTERS_COMMIT:-bb7d3cd6}"
sdk_image="${DOTNET_SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:8.0}"

# Reference assemblies, same contract as tools/everybodyshim/build.sh: with neither
# VALHEIM_MANAGED nor BEPINEX_CORE set they are lifted out of the named running server
# container, because that is the assembly set the plugin will actually face.
container="${VALHEIM_CONTAINER:-valheim-server-Ulfsland}"
refroot="${SERVERCHARACTERS_REFS:-${TMPDIR:-/tmp}/everybodyshim-refs}"
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

rm -rf -- "$work"
mkdir -p "$work"

# 1. Upstream source at the fix commit. Only ServerCharacters/*.cs, Properties/ and Libs/
#    are used; the checked-in .csproj wants a NuGet packages/ directory and a protogen
#    run this build deliberately replaces.
git clone -q https://github.com/blaxxun-boop/ServerCharacters.git "$work/src"
git -C "$work/src" checkout -q "$commit"
built_version=$(sed -n 's/.*ModVersion = "\([^"]*\)".*/\1/p' "$work/src/ServerCharacters/ServerCharacters.cs")
[[ -n $built_version ]] || { echo "could not read ModVersion from upstream source" >&2; exit 1; }
echo "upstream $commit declares ModVersion $built_version"

proj=$work/proj
mkdir -p "$proj/Properties" "$proj/Libs" "$proj/refs"
cp "$work"/src/ServerCharacters/*.cs "$proj/"
cp "$work"/src/ServerCharacters/Properties/*.cs "$proj/Properties/"
cp "$work"/src/ServerCharacters/Libs/*.dll "$proj/Libs/"
rm -f "$proj/ServerCharacters.csproj"
cp "$here/ServerCharacters.csproj" "$here/ILRepack.targets" "$here/Request.cs" "$proj/"

# 2. Publicized game assemblies. The mod reads private Valheim state throughout, and
#    upstream's csproj expects a publicized set that only exists on a modder's machine.
mcs -out:"$work/publicize.exe" -r:"$bepinex_core/Mono.Cecil.dll" "$here/publicize.cs"
MONO_PATH="$bepinex_core" mono "$work/publicize.exe" "$proj/refs" \
    "$managed/assembly_valheim.dll" "$managed/assembly_utils.dll" \
    "$managed/assembly_guiutils.dll" "$managed/com.rlabrecque.steamworks.net.dll" \
    "$managed/SoftReferenceableAssets.dll"

for name in Splatform Unity.TextMeshPro UnityEngine UnityEngine.CoreModule UnityEngine.UI \
            UnityEngine.IMGUIModule UnityEngine.InputLegacyModule UnityEngine.PhysicsModule \
            UnityEngine.TextRenderingModule UnityEngine.UIModule UnityEngine.AnimationModule \
            UnityEngine.AudioModule UnityEngine.ImageConversionModule UnityEngine.ParticleSystemModule; do
    [[ -f $managed/$name.dll ]] || { echo "missing $managed/$name.dll" >&2; exit 1; }
    cp "$managed/$name.dll" "$proj/refs/"
done
cp "$bepinex_core/0Harmony.dll" "$bepinex_core/BepInEx.dll" "$proj/refs/"

# 3. Compile and ILRepack. Runs as the invoking user so the outputs are not root-owned.
mkdir -p "$work/nuget"
docker run --rm -u "$(id -u):$(id -g)" \
    -e HOME=/tmp -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e NUGET_PACKAGES=/nuget \
    -v "$work/nuget:/nuget" -v "$proj:/src" -w /src \
    "$sdk_image" dotnet build -c Release -v minimal

dll=$proj/bin/Release/ServerCharacters.dll
[[ -f $dll ]] || { echo "build produced no assembly" >&2; exit 1; }

# 4. Package over the pinned Thunderstore payload. manifest.json is carried across from
#    the stock package unchanged, so the profile's 1.4.16 pin and the deploy-time cache
#    check in tools/valheim_mods.py stay true; only the DLL is ours.
stock=${SERVERCHARACTERS_STOCK:-}
if [[ -z $stock ]]; then
    for candidate in "${VALHEIM_ROOT:-/media/big4/projects/game/valheim}"/profiles/*/manager-cache/server/BepInEx/plugins/ServerCharacters; do
        [[ -f $candidate/manifest.json ]] && { stock=$candidate; break; }
    done
fi
[[ -n $stock && -f $stock/manifest.json ]] ||
    { echo "no stock ServerCharacters package found; set SERVERCHARACTERS_STOCK" >&2; exit 1; }

pkg=$work/pkg
mkdir -p "$pkg/BepInEx/plugins"
cp "$dll" "$pkg/BepInEx/plugins/"
cp "$stock/manifest.json" "$stock/icon.png" "$stock/README.md" "$pkg/BepInEx/plugins/"
cat >"$pkg/README.txt" <<EOF
ServerCharacters $built_version - built from upstream master
(blaxxun-boop/ServerCharacters@$commit, "fix for deep north"), the commit that ports the
mod to Valheim 1.0 but has never been released to Thunderstore. Replaces the payload of
the pinned Smoothbrain-ServerCharacters package in place; manifest.json still reads the
pinned version so the profile's version pin stays honest.

Shipped to server AND clients from one archive because ServerSync enforces
MinimumRequiredVersion = $built_version with ModRequired = true.

Built by tools/servercharacters/build.sh in valheim-portal.
EOF

rm -f -- "$out"
(cd "$pkg" && zip -qrX "$out" BepInEx README.txt)
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
printf 'plugin sha256 %s\n' "$(sha256sum "$dll" | cut -d' ' -f1)"
