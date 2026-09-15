#!/usr/bin/env bash
# Proves a deploy that replaces a hand-patched mod assembly with package bytes says so.
#
# tools/modpatches/ holds Cecil patchers for defects no config switch can avoid - today
# exactly one, blacksmithing_expanded_null_key.cs against OdinPlus-BlacksmithingExpanded
# 1.1.7, live on Vangard only. A patcher refuses to write when the IL shape it expects has
# changed, which is a safety feature with a silent failure mode: the deploy rebuilds the
# plugin tree from package sources, so the world gets stock bytes back and nothing says the
# repair is gone. That is the CharacterTemplate.yml failure shape again - invisible at
# deploy time, visible later in a player's game, here as the crash the patch prevents.
#
# MEASURED read-only 2026-09-15 on the live fleet: Vangard's plugin tree carries
# BlacksmithingExpanded.dll md5 79133afef773 beside BlacksmithingExpanded.dll.stock-1.1.7
# md5 9bf9b3435f76, and every profile's server cache carries exactly those stock bytes - so
# the next Vangard deploy reverts the patch today. Ulfsland, Hrafnheim, Doggerland and
# Storgard have no sidecar and produce no patch lines.
#
# The `.stock-<version>` sidecar is the detection signal, and it is the convention
# tools/modpatches/README.md already requires ("Keep the stock DLL beside the patched one
# ... so the change is reversible and visible") rather than a second declaration file that
# could drift out of agreement with the patchers.
#
# Reported, not refused, deliberately: a deploy is the documented way this patch is
# reverted, reapplying it is a manual mcs/mono build rather than a one-command repair, and
# refusing would turn a maintenance window into a stop no operator could clear.
#
# Run: bash hostops/tests/portal_mod_admin_modpatch.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ADMIN="$SCRIPT_DIR/../portal_mod_admin.sh"
WORLD=Fixtureheim
PROFILE=fixture-admin
PACKAGE=PatchedMod
DLL=$PACKAGE/$PACKAGE.dll
SIDECAR=$DLL.stock-1.1.7

tmp=$(mktemp -d /tmp/mod-admin-patch.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

fleet="$tmp/fleet"
world="$fleet/$WORLD"
profile="$fleet/profiles/$PROFILE"
cache="$profile/manager-cache/server/BepInEx/plugins/$PACKAGE"
plugins="$world/config_merged/bepinex/plugins"

# $1 = bytes the profile cache ships for the DLL. The live tree always holds PATCHED bytes
# beside a .stock-1.1.7 sidecar holding STOCK, which is Vangard's shape.
reset_fixture() {
  rm -rf -- "$fleet"
  mkdir -p "$cache" "$profile/manual-mods" "$world/mods" "$plugins/$PACKAGE"
  printf '{"packages": [], "client_only_packages": []}\n' >"$profile/profile-manifest.json"
  printf '%s\n' "$1" >"$cache/$PACKAGE.dll"
  printf 'PATCHED\n' >"$plugins/$DLL"
  printf 'STOCK\n' >"$plugins/$SIDECAR"
}

run_admin() {
  env VALHEIM_ROOT="$fleet" VALHEIM_SETTINGS_HISTORY="$tmp/history" \
    bash "$ADMIN" "$WORLD" "$PROFILE" "$@" >"$tmp/out" 2>"$tmp/err"
}

# 1. Vangard today: the cache ships the same stock build the patch was made against, so the
#    deploy hands back the bytes the patch repaired. Name it, and say the repair is gone.
reset_fixture STOCK
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy failed: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^patch_reverted=$DLL version=1.1.7\$" "$tmp/out" ||
  fail "the deploy reverted a hand-patched assembly silently: $(cat "$tmp/out")"
grep -q 'hand-patched mod' "$tmp/err" ||
  fail "no warning named the reverted patch: $(cat "$tmp/err")"
grep -q 'tools/modpatches/README.md' "$tmp/err" ||
  fail "the warning does not say where to rebuild the patch from: $(cat "$tmp/err")"

# 2. The future bump: 1.1.8 ships, so the incoming bytes are neither the patched ones nor
#    the stock build the patcher was verified against. Distinct outcome, because this one
#    cannot be fixed by repeating the documented command - the patcher needs revalidating.
reset_fixture STOCK-1.1.8
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy failed on a bumped package: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^patch_stale=$DLL stock_version=1.1.7\$" "$tmp/out" ||
  fail "a patch invalidated by a version bump was not reported: $(cat "$tmp/out")"
grep -q 'revalidate the patcher' "$tmp/err" ||
  fail "the bumped-package warning does not say the patcher must be revalidated: $(cat "$tmp/err")"

# 3. A patch carried by a deploy source is a success line, not a warning: the deployed tree
#    ends up patched. This is the shape an operator gets by putting the patched assembly
#    and its sidecar in <world>/mods/generated/.
reset_fixture STOCK
mkdir -p "$world/mods/generated/$PACKAGE"
printf 'PATCHED\n' >"$world/mods/generated/$DLL"
printf 'STOCK\n' >"$world/mods/generated/$SIDECAR"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy failed with a carried patch: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^patch_applied=$DLL version=1.1.7\$" "$tmp/out" ||
  fail "a patch the deploy carried was not reported: $(cat "$tmp/out")"
[[ $(cat "$plugins/$DLL") == PATCHED ]] || fail "the carried patch did not reach the plugin tree"
if grep -q '^patch_reverted=' "$tmp/out"; then
  fail "a carried patch was reported as reverted: $(cat "$tmp/out")"
fi
if grep -q 'hand-patched mod' "$tmp/err"; then
  fail "a carried patch produced a loss warning: $(cat "$tmp/err")"
fi

# 4. No sidecar anywhere - the state of the other four worlds - must produce no patch lines
#    at all. A guard that cries wolf on every world is one nobody reads.
reset_fixture STOCK
rm -f "$plugins/$SIDECAR"
printf 'STOCK\n' >"$plugins/$DLL"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy failed on an unpatched world: rc=$rc stderr=$(cat "$tmp/err")"
if grep -q '^patch_' "$tmp/out"; then
  fail "an unpatched world reported a patch outcome: $(cat "$tmp/out")"
fi

# 5. deploy-plan predicts the same outcome read-only, which is the only check available
#    while the world is being played - and it is how Vangard's pending revert was measured.
reset_fixture STOCK
rc=0
run_admin deploy-plan || rc=$?
[[ $rc -eq 0 ]] || fail "deploy-plan failed: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^patch_would_be_reverted=$DLL version=1.1.7\$" "$tmp/out" ||
  fail "deploy-plan does not predict the patch revert: $(cat "$tmp/out")"
[[ $(cat "$plugins/$DLL") == PATCHED ]] || fail "deploy-plan wrote to the plugin tree"

[[ $failures -eq 0 ]] || { echo "$failures mod-patch check(s) failed" >&2; exit 1; }
echo "PASS: portal_mod_admin.sh deploy names what it does to a hand-patched mod assembly"
