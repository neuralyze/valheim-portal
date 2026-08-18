#!/usr/bin/env bash
# Compiles ValheimVRMod.dll from the ValheimVR source with Mono's compiler, on this host.
#
# The Windows build host and its Visual Studio are not available, so MSBuild is not the
# build path any more. Nothing about the mod needs Windows: it is a BepInEx plugin compiled
# against the game's own managed assemblies, which is exactly what tools/vrfixes/build.sh
# has been doing for our own plugin all along.
#
# Two things cost a session to learn, so they are encoded here rather than rediscovered:
#
#   * `-nostdlib -noconfig` makes mcs sit forever instead of erroring. Let it use its own
#     default configuration; it compiles the whole mod in about a second.
#   * The default language version rejects this source (CS1644, CS1738 - named arguments
#     before positional ones). `-langversion:latest` is required, not cosmetic.
#
# Reference assemblies are NOT vendored: they are the game's, published under its own
# licence. Point --refs at a directory holding them. A completed build leaves exactly such
# a directory at <source-root>/build/latest, which is the default when it exists.
set -euo pipefail

usage() {
    cat >&2 <<'USAGE'
Usage: build-valheimvr.sh --source-root DIR --output DLL [options]

  --source-root DIR   ValheimVR checkout (the directory holding ValheimVRMod/)
  --output DLL        where to write ValheimVRMod.dll
  --configuration C   Release (default), Debug, SyncOnlyRelease, SyncOnlyDebug
  --refs DIR          reference assemblies; default <source-root>/build/latest
  --bepinex DIR       directory holding BepInEx.dll and 0Harmony.dll

Prints one JSON object describing what it built.
USAGE
    exit 64
}

source_root=""
output=""
configuration="Release"
refs_dir=""
bepinex_dir=""

while [ $# -gt 0 ]; do
    case "$1" in
        --source-root) source_root="${2:-}"; shift 2 ;;
        --output) output="${2:-}"; shift 2 ;;
        --configuration) configuration="${2:-}"; shift 2 ;;
        --refs) refs_dir="${2:-}"; shift 2 ;;
        --bepinex) bepinex_dir="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

if [ -z "$source_root" ] || [ -z "$output" ]; then
    usage
fi

command -v mcs >/dev/null 2>&1 || {
    echo "mcs is unavailable. Install Mono's C# compiler (Debian/Ubuntu: mono-mcs)." >&2
    exit 78
}

project_dir="$source_root/ValheimVRMod"
[ -d "$project_dir" ] || { echo "not a ValheimVR checkout: $source_root" >&2; exit 66; }

# The defines are the ones ValheimVRMod.csproj sets per configuration. NONVRMODE only adds
# a log line, but the companion is built with it, so the parity is kept rather than assumed
# away.
optimize="-optimize+"
defines="TRACE"
case "$configuration" in
    Release) ;;
    Debug) optimize="-debug"; defines="TRACE;DEBUG" ;;
    SyncOnlyRelease) defines="TRACE;NONVRMODE" ;;
    SyncOnlyDebug) optimize="-debug"; defines="TRACE;DEBUG;NONVRMODE" ;;
    *) echo "unknown configuration: $configuration" >&2; usage ;;
esac

if [ -z "$refs_dir" ]; then
    refs_dir="$source_root/build/latest"
fi
[ -d "$refs_dir" ] || {
    cat >&2 <<EOF
No reference assemblies at $refs_dir.

They are the game's managed assemblies (UnityEngine*, assembly_valheim, SteamVR, final_ik,
and the rest) and are not distributed with this repository. Pass --refs DIR, or set up
<source-root>/build/latest. A synchronized VR profile's Valheim_Data/Managed serves.
EOF
    exit 78
}

if [ -z "$bepinex_dir" ]; then
    for candidate in "$refs_dir" "$refs_dir/BepInEx/core" "$source_root/build/bepinex"; do
        if [ -f "$candidate/BepInEx.dll" ] && [ -f "$candidate/0Harmony.dll" ]; then
            bepinex_dir="$candidate"
            break
        fi
    done
fi
if [ -z "$bepinex_dir" ]; then
    cat >&2 <<'EOF'
BepInEx.dll and 0Harmony.dll were not found.

Pass --bepinex DIR pointing at a BepInEx core directory. Copy it out of a synchronized
profile first if that profile lives on a network share: reading references across one makes
the build appear to hang.
EOF
    exit 78
fi

refs=()
for dll in "$refs_dir"/*.dll; do
    [ -e "$dll" ] || continue
    # The mod's own previous output is in there after a build; referencing it would shadow
    # the types being compiled.
    case "$(basename "$dll")" in
        ValheimVRMod.dll|BepInEx.dll|0Harmony.dll) continue ;;
    esac
    refs+=("-r:$dll")
done
refs+=("-r:$bepinex_dir/BepInEx.dll" "-r:$bepinex_dir/0Harmony.dll")

sources=()
while IFS= read -r file; do
    sources+=("$file")
done < <(find "$project_dir" -name '*.cs' -not -path '*/obj/*' -not -path '*/bin/*' | sort)

[ "${#sources[@]}" -gt 0 ] || { echo "no sources under $project_dir" >&2; exit 66; }

mkdir -p "$(dirname -- "$output")"
mcs -target:library -platform:x64 -langversion:latest -unsafe \
    "$optimize" "-define:$defines" -out:"$output" \
    "${refs[@]}" "${sources[@]}" >&2

python3 - "$output" "$configuration" "${#sources[@]}" "${#refs[@]}" <<'PY'
import hashlib, json, sys
path, configuration, sources, references = sys.argv[1:5]
with open(path, "rb") as handle:
    digest = hashlib.sha256(handle.read()).hexdigest()
print(json.dumps({
    "valheimvr_dll": path,
    "valheimvr_dll_sha256": digest,
    "configuration": configuration,
    "sources": int(sources),
    "references": int(references),
}, separators=(",", ":")))
PY
