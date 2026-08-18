#!/usr/bin/env bash
# Builds a ValheimVR artifact for players: compile the mod, swap it into an archive of the
# right shape, and re-zip.
#
# This replaces tools/build-valheimvr-flat.ps1 (MSBuild.exe on a Windows host we can no
# longer build on) and the Windows-only make-release.cmd staging that produced the VR
# release zip. Both artifacts carry the same locally built DLL and differ only in shape, so
# they are one script with --client-type rather than two that drift apart.
#
#   --client-type flat  the Flat companion: BepInEx payload only, nonVrPlayer = true.
#   --client-type vr    a VHVR release zip: BepInEx/ and Valheim_Data/, feeding
#                       scripts/build-vr-runtime-artifact.sh, which makes the portal
#                       artifact VR players install.
#
# The guards are not ceremony. The dodge guard is the single reason a locally built mod is
# shipped at all (docs/valheimvr-packaging.md); a Flat template that does not set
# nonVrPlayer = true would run VR input handling for desktop players, and a VR template
# that does set it would ship a headset build that thinks it is on a monitor.
set -euo pipefail

usage() {
    cat >&2 <<'USAGE'
Usage: build-valheimvr-artifact.sh --source-root DIR --template ZIP --output ZIP
                                  --client-type flat|vr [options]

  --source-root DIR   ValheimVR checkout (the directory holding ValheimVRMod/)
  --template ZIP      an existing artifact of the same client type to rebuild from
  --output ZIP        artifact to write
  --client-type T     flat (companion) or vr (VHVR release zip)
  --configuration C   Release (default) or SyncOnlyRelease
  --refs DIR          reference assemblies, passed through to build-valheimvr.sh
  --bepinex DIR       BepInEx core directory, passed through

Prints one JSON object describing the artifact.
USAGE
    exit 64
}

source_root=""
template=""
output=""
configuration="Release"
client_type=""
passthrough=()

while [ $# -gt 0 ]; do
    case "$1" in
        --source-root) source_root="${2:-}"; shift 2 ;;
        --template) template="${2:-}"; shift 2 ;;
        --output) output="${2:-}"; shift 2 ;;
        --configuration) configuration="${2:-}"; shift 2 ;;
        --client-type) client_type="${2:-}"; shift 2 ;;
        --refs) passthrough+=(--refs "${2:-}"); shift 2 ;;
        --bepinex) passthrough+=(--bepinex "${2:-}"); shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

if [ -z "$source_root" ] || [ -z "$template" ] || [ -z "$output" ] || [ -z "$client_type" ]; then
    usage
fi

case "$client_type" in
    flat|vr) ;;
    *) echo "client type must be flat or vr" >&2; usage ;;
esac

case "$configuration" in
    Release|SyncOnlyRelease) ;;
    *) echo "configuration must be Release or SyncOnlyRelease" >&2; usage ;;
esac

for tool in unzip zip; do
    command -v "$tool" >/dev/null 2>&1 || { echo "required command is unavailable: $tool" >&2; exit 78; }
done

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
control_patches="$source_root/ValheimVRMod/Patches/ControlPatches.cs"

for required in "$control_patches" "$template"; do
    [ -f "$required" ] || { echo "required file is missing: $required" >&2; exit 66; }
done

if ! grep -q '\[HarmonyPrepare\]' "$control_patches" ||
   ! grep -Eq 'return[[:space:]]+!VHVRConfig\.NonVrPlayer\(\)' "$control_patches"; then
    echo "Flat dodge guard is absent from ControlPatches.cs." >&2
    exit 65
fi

work="$(mktemp -d -t valheimvr-artifact-XXXXXXXX)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

staged="$work/staged"
mkdir -p "$staged"
unzip -q "$template" -d "$staged"

# Everything about the template is checked before the compile: a wrong template is the
# likely mistake, and finding out after a build wastes the build and buries the message
# under compiler output.
config="$staged/BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg"
case "$client_type" in
    flat)
        # A companion carries the config, because the whole point of it is that
        # nonVrPlayer is set for a desktop player.
        [ -f "$config" ] || { echo "template is not a ValheimVR companion: $config is missing" >&2; exit 65; }
        grep -Eq 'nonVrPlayer[[:space:]]*=[[:space:]]*true' "$config" || {
            echo "Template is not a Flat ValheimVR companion: nonVrPlayer must be true." >&2
            exit 65
        }
        ;;
    vr)
        # A fresh VR release ships no config at all - BepInEx writes one on first run - so
        # its absence is normal here and only a present-and-wrong one is a defect.
        if [ -f "$config" ] && grep -Eq 'nonVrPlayer[[:space:]]*=[[:space:]]*true' "$config"; then
            echo "Template is a Flat companion, not a VR release: nonVrPlayer must not be true." >&2
            exit 65
        fi
        # The runtime builder expects exactly these two roots and rejects anything else.
        unexpected="$(find "$staged" -mindepth 1 -maxdepth 1 -printf '%P\n' |
            grep -vxE 'BepInEx|Valheim_Data' || true)"
        if [ -n "$unexpected" ]; then
            echo "VR release template has unexpected top-level entries:" >&2
            printf '  %s\n' "$unexpected" >&2
            exit 65
        fi
        ;;
esac

plugins="$staged/BepInEx/plugins"
[ -d "$plugins" ] || { echo "template has no BepInEx/plugins directory" >&2; exit 65; }

dll="$work/ValheimVRMod.dll"
build_report="$("$here/build-valheimvr.sh" --source-root "$source_root" --output "$dll" \
    --configuration "$configuration" "${passthrough[@]+"${passthrough[@]}"}")"

cp -- "$dll" "$plugins/ValheimVRMod.dll"
# The standalone dodge fix is superseded by the in-mod guard; a companion carrying both
# would unpatch what was never patched. release-format.md accepts it only in archives
# published before the cutover.
rm -f -- "$plugins/ValheimVRFlatDodgePatchFix.dll"

output_path="$(cd -- "$(dirname -- "$output")" && pwd)/$(basename -- "$output")"
rm -f -- "$output_path"
# Reproducible: identical content has to produce identical bytes, or release review cannot
# compare two artifacts by hash. -X drops uid/gid and extra fields; the timestamps still
# vary because the freshly built DLL carries this minute, so every staged entry is pinned to
# the earliest instant a ZIP can hold.
find "$staged" -exec touch -h -t 198001010000 {} +
( cd "$staged" && find . -mindepth 1 -printf '%P\n' | sort | zip -qX9 "$output_path" -@ )

python3 - "$output_path" "$configuration" "$build_report" "$client_type" <<'PY'
import hashlib, json, sys
artifact, configuration, report, client_type = sys.argv[1:5]
with open(artifact, "rb") as handle:
    digest = hashlib.sha256(handle.read()).hexdigest()
built = json.loads(report)
print(json.dumps({
    "artifact": artifact,
    "sha256": digest,
    "valheimvr_dll_sha256": built["valheimvr_dll_sha256"],
    "configuration": configuration,
    "client_type": client_type,
}, separators=(",", ":")))
PY
