#!/usr/bin/env bash
# Builds ServerCharacters 1.4.17 from UNMODIFIED upstream source and packages it the way
# a Thunderstore archive is packaged, so one artifact serves both sides of the fleet.
#
# TEMPORARY, OPERATOR-AUTHORISED. Read tools/servercharacters/README.md, section "What we
# ship, and why that is temporary", before changing anything here. The short version:
# Valheim 1.0 broke eight members this mod uses, upstream fixed all eight in
# blaxxun-boop/ServerCharacters@bb7d3cd6 ("fix for deep north", 2026-09-09) and bumped the
# version to 1.4.17, and that build has never been published to Thunderstore - its newest
# is 1.4.16 from 2025-05-02, which is fatal on 1.0.12. The operator decided on 2026-09-14
# to run our own compile of that commit on their own server and their own clients until
# Thunderstore carries 1.4.17.
#
# Output: a Thunderstore-shaped archive - ServerCharacters.dll and a manifest.json of our
# own at the root - written by default to the gitignored embed path that
# internal/servercharacters compiles into the Windows client and that tools/valheim_mods.py
# installs on the server. The archive is deliberately NOT in git: this repository is
# published, and the mod has no licence. See internal/servercharacters/embedded/README.md.
#
# Cross-compiled on Linux. The mod needs C# 10 and 12 features, so mcs cannot build it
# the way tools/everybodyshim and tools/vrfixes are built; this uses the .NET SDK in a
# throwaway container and leaves no toolchain on the host.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd -- "$here/../.." && pwd)"
out="${1:-$repo/internal/servercharacters/embedded/ServerCharacters.zip}"
work="${SERVERCHARACTERS_WORK:-${TMPDIR:-/tmp}/servercharacters-build}"
commit="${SERVERCHARACTERS_COMMIT:-bb7d3cd6}"
sdk_image="${DOTNET_SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:8.0}"

# Reference assemblies. Preference order, and the order matters because a stopped server is
# the normal state during a deploy: the world's own on-disk BepInEx tree first, then an
# explicit VALHEIM_MANAGED/BEPINEX_CORE or a previously lifted cache, then `docker cp` out
# of the running container as a last resort. The on-disk tree is the same assembly set the
# plugin will face and needs nothing running - hostops/export_valheim_map_sources.sh reads
# it the same way, and tools/worldseed/build.sh was changed to prefer it for this reason.
#
# VALHEIM_CONTAINER may be given either as a world name or as the container name; the
# valheim-server- prefix is stripped so the old invocation still resolves a world directory.
container="${VALHEIM_CONTAINER:-valheim-server-Ulfsland}"
world=${container#valheim-server-}
refroot="${SERVERCHARACTERS_REFS:-${TMPDIR:-/tmp}/everybodyshim-refs}"
ondisk="${VALHEIM_ROOT:-/media/big4/projects/game/valheim}/$world/data/bepinex"
managed="${VALHEIM_MANAGED:-}"
bepinex_core="${BEPINEX_CORE:-}"

# Both halves are checked for the assemblies this build actually reads, not merely for the
# directory: a half-populated tree fails later, inside the container, as a compile error.
if [[ -z $managed && -f $ondisk/valheim_server_Data/Managed/assembly_valheim.dll ]]; then
    managed=$ondisk/valheim_server_Data/Managed
fi
if [[ -z $bepinex_core && -f $ondisk/BepInEx/core/BepInEx.dll ]]; then
    bepinex_core=$ondisk/BepInEx/core
fi
[[ -n $managed || ! -f $refroot/managed/assembly_valheim.dll ]] || managed=$refroot/managed
[[ -n $bepinex_core || ! -f $refroot/core/BepInEx.dll ]] || bepinex_core=$refroot/core

if [[ -z $managed || -z $bepinex_core ]]; then
    docker inspect "$container" >/dev/null 2>&1 || {
        echo "no reference assemblies on disk under $ondisk and container $container is not present." >&2
        echo "set VALHEIM_MANAGED and BEPINEX_CORE, or VALHEIM_CONTAINER/VALHEIM_ROOT." >&2
        exit 1
    }
    mkdir -p "$refroot"
    [[ -n $managed ]] || {
        docker cp "$container:/opt/valheim/server/valheim_server_Data/Managed" "$refroot/managed"
        managed=$refroot/managed
    }
    [[ -n $bepinex_core ]] || {
        docker cp "$container:/opt/valheim/bepinex/BepInEx/core" "$refroot/core"
        bepinex_core=$refroot/core
    }
fi
printf 'references: managed=%s core=%s\n' "$managed" "$bepinex_core" >&2

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

# 4. Package it the way Thunderstore packages this mod: payload and metadata at the root of
#    the archive, which is the shape both install paths already understand -
#    extractPackageArchive in cmd/valheim-profile-sync maps a root-level file into
#    BepInEx/plugins/<namespace>-<name>/ and skips manifest.json, and extract_package in
#    tools/valheim_mods.py copies the tree into the profile's plugin cache, where
#    assert_cached_version reads manifest.json back.
#
#    manifest.json is OURS, not the author's: it exists to satisfy that version check, and
#    copying his metadata and icon would redistribute more of his package than the operator
#    decision calls for. Nothing reads its description, so it says what this build is.
pkg=$work/pkg
mkdir -p "$pkg"
cp "$dll" "$pkg/ServerCharacters.dll"
cat >"$pkg/manifest.json" <<EOF
{
  "name": "ServerCharacters",
  "version_number": "$built_version",
  "website_url": "https://github.com/blaxxun-boop/ServerCharacters/tree/$commit",
  "description": "Local compile of upstream $commit, built by tools/servercharacters/build.sh. Not an upstream release.",
  "dependencies": []
}
EOF
cat >"$pkg/README.md" <<EOF
ServerCharacters $built_version, compiled from unmodified upstream source at
blaxxun-boop/ServerCharacters@$commit ("fix for deep north"), the commit that ports the mod
to Valheim 1.0 and bumps the version to $built_version. Upstream has not released it:
Thunderstore's newest is 1.4.16, which is fatal on 1.0.12.

Server and clients are given THIS archive, so both sides run the same bytes and ServerSync's
MinimumRequiredVersion = $built_version / ModRequired = true cannot mismatch.

Temporary and operator-authorised; replaced by the Thunderstore package as soon as
$built_version is published there. Built by tools/servercharacters/build.sh in valheim-portal.
EOF

mkdir -p "$(dirname -- "$out")"
rm -f -- "$out"
# Reproducible on purpose: the SHA256 of this archive is published in every Ulfsland
# profile definition and is checked by the client against the copy compiled into it, so a
# rebuild that changes the hash without changing the payload would fail every install.
# MEASURED: the compiler is already deterministic here - two builds of bb7d3cd6 produced
# ServerCharacters.dll sha256 efe7d421... twice - but zip records each file's mtime in the
# DOS timestamp field, which -X does not drop, so the archive differed. Pinning the mtimes
# to the DOS epoch removes the only remaining source of variation.
touch -d '1980-01-01 00:00:00' "$pkg/ServerCharacters.dll" "$pkg/manifest.json" "$pkg/README.md"
(cd "$pkg" && zip -qrX "$out" ServerCharacters.dll manifest.json README.md)
printf 'built %s (%s bytes)\n' "$out" "$(stat -c %s "$out")"
printf 'archive sha256 %s\n' "$(sha256sum "$out" | cut -d' ' -f1)"
printf 'plugin sha256 %s\n' "$(sha256sum "$dll" | cut -d' ' -f1)"
