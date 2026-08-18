#!/usr/bin/env bash
# Proves the ValheimVR artifact builder refuses the mistakes that would ship a broken client.
#
# Every case here fails before the compile, which is why this test needs no game assemblies
# and no Mono: the template and guard checks were deliberately put ahead of the build so a
# wrong template costs 0.2s instead of a build, and so this test could exist at all.
#
# The success path is not covered here because it needs the game's managed assemblies, which
# are not distributable. It is exercised by an operator build; docs/valheimvr-packaging.md
# records how that was verified.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/build-valheimvr-artifact.sh"
work="$(mktemp -d -t valheimvr-artifact-test-XXXXXXXX)"
trap 'rm -rf -- "$work"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# A source tree carrying the Flat dodge guard, and one without it.
make_source() {
    local dir=$1 guarded=$2
    mkdir -p "$dir/ValheimVRMod/Patches"
    if [ "$guarded" = guarded ]; then
        cat > "$dir/ValheimVRMod/Patches/ControlPatches.cs" <<'CS'
class Player_UpdateDodge_Patch {
    [HarmonyPrepare]
    static bool Prepare()
    {
        return !VHVRConfig.NonVrPlayer();
    }
}
CS
    else
        echo 'class Player_UpdateDodge_Patch { }' > "$dir/ValheimVRMod/Patches/ControlPatches.cs"
    fi
}

# $1 archive, $2 nonVrPlayer value or "none", $3.. extra top-level directories
make_template() {
    local archive=$1 nonvr=$2; shift 2
    local staging="$work/staging-$RANDOM"
    mkdir -p "$staging/BepInEx/plugins" "$staging/BepInEx/config"
    : > "$staging/BepInEx/plugins/ValheimVRMod.dll"
    if [ "$nonvr" != none ]; then
        printf '[VRTweaks]\nnonVrPlayer = %s\n' "$nonvr" \
            > "$staging/BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg"
    fi
    for extra in "$@"; do mkdir -p "$staging/$extra"; done
    ( cd "$staging" && zip -qXr9 "$archive" . )
}

expect_exit() {
    local wanted=$1 pattern=$2; shift 2
    local output status
    set +e
    output="$("$@" 2>&1)"
    status=$?
    set -e
    [ "$status" = "$wanted" ] || fail "expected exit $wanted, got $status from: $* :: $output"
    grep -q -- "$pattern" <<<"$output" || fail "expected message $pattern, got: $output"
}

make_source "$work/guarded" guarded
make_source "$work/unguarded" plain
make_template "$work/flat.zip" true
make_template "$work/flat-false.zip" false
make_template "$work/noconfig.zip" none
make_template "$work/vr.zip" none Valheim_Data
make_template "$work/vr-extra.zip" none Valheim_Data Steamworks

out="$work/out.zip"

# A client type is required: building "an artifact" without saying which one is how a Flat
# player ends up with a headset build.
expect_exit 64 "client-type" "$script" --source-root "$work/guarded" --template "$work/flat.zip" --output "$out"
expect_exit 64 "client type must be" "$script" --source-root "$work/guarded" --template "$work/flat.zip" \
    --output "$out" --client-type desktop

# The guard is the only reason we build the mod ourselves; shipping without it silently
# restores VR dodging for desktop players.
expect_exit 65 "Flat dodge guard is absent" "$script" --source-root "$work/unguarded" \
    --template "$work/flat.zip" --output "$out" --client-type flat

# Flat needs the config, and it has to say true.
expect_exit 65 "is missing" "$script" --source-root "$work/guarded" --template "$work/noconfig.zip" \
    --output "$out" --client-type flat
expect_exit 65 "nonVrPlayer must be true" "$script" --source-root "$work/guarded" \
    --template "$work/flat-false.zip" --output "$out" --client-type flat

# VR must not be handed a companion: same DLL, opposite meaning.
expect_exit 65 "nonVrPlayer must not be true" "$script" --source-root "$work/guarded" \
    --template "$work/flat.zip" --output "$out" --client-type vr

# The runtime builder accepts exactly BepInEx/ and Valheim_Data/.
expect_exit 65 "unexpected top-level entries" "$script" --source-root "$work/guarded" \
    --template "$work/vr-extra.zip" --output "$out" --client-type vr

# A VR template of the right shape gets past every template check and stops at the compile,
# which is the boundary this test does not cross. Reaching the compiler is the pass
# condition: it proves nothing above it rejected a valid VR release.
set +e
output="$("$script" --source-root "$work/guarded" --template "$work/vr.zip" --output "$out" \
    --client-type vr --refs "$work/absent-refs" 2>&1)"
status=$?
set -e
[ "$status" = 78 ] || fail "a valid VR template should reach the compile and stop on missing references, got $status: $output"
grep -q "No reference assemblies" <<<"$output" || fail "expected the reference-assembly message, got: $output"

echo "PASS: build-valheimvr-artifact.sh refuses wrong templates and missing guards"
