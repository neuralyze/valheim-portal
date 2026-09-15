#!/usr/bin/env bash
# Proves `portal_mod_admin.sh <world> <profile> deploy` refuses a cached server package
# whose files are not where its own archive puts them, instead of quietly deploying a
# plugin tree with a DLL in a different directory.
#
# Found 2026-09-15 while fixing the CharacterTemplate.yml loss on Ulfsland.
# profiles/ulfsland-dn (the profile .active-mod-profile names, so the one an ordinary
# deploy of that world uses) held ImpactfulSkills with its DLL at
# ImpactfulSkills/plugins/ImpactfulSkills.dll, while profiles/ulfsland-admin held the
# IDENTICAL archive extracted flat. Re-extracting that archive with the current extractor
# produces the flat layout, so the entry is stale - written before 'plugins/' joined the
# strip list - and the next ulfsland-dn deploy would have moved the mod's DLL.
#
# The check compares a cache entry against the archive the manifest pins, NOT against a
# directory-name rule, because `<Package>/plugins/` is a legitimate author subfolder in
# some packages and an unflattened prefix in others, and only the archive says which.
# Case 3 below is that distinction, and a name-based check fails it.
#
# Everything here runs against a throwaway fleet under /tmp with no container and no live
# world.
# Run: bash hostops/tests/portal_mod_admin_stale_cache.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ADMIN="$SCRIPT_DIR/../portal_mod_admin.sh"
WORLD=Fixtureheim
PROFILE=fixture-dn
IDENTIFIER=Owner-TestMod
PACKAGE=TestMod
VERSION=1.2.3

tmp=$(mktemp -d /tmp/mod-admin-stale.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

fleet="$tmp/fleet"
world="$fleet/$WORLD"
profile="$fleet/profiles/$PROFILE"
cached="$profile/manager-cache/server/BepInEx/plugins/$PACKAGE"
archive="$profile/manager-cache/packages/$PACKAGE-$VERSION.zip"
plugins="$world/config_merged/bepinex/plugins"

# One pinned package, its archive, and a cache entry whose layout each case decides.
# $1 is the path the archive stores the DLL at, relative to the archive root.
reset_fixture() {
  rm -rf -- "$fleet"
  mkdir -p "$cached" "$profile/manual-mods" "$profile/manager-cache/packages" \
    "$world/mods" "$plugins/$PACKAGE"
  printf '{"packages": [{"identifier": "%s", "version": "%s", "scope": "shared"}], "client_only_packages": []}\n' \
    "$IDENTIFIER" "$VERSION" >"$profile/profile-manifest.json"
  printf '{"name": "%s", "version_number": "%s"}\n' "$PACKAGE" "$VERSION" >"$cached/manifest.json"
  python3 - "$archive" "$1" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr('manifest.json', '{"name": "TestMod", "version_number": "1.2.3"}\n')
    z.writestr(sys.argv[2], 'DLL\n')
PY
}

run_admin() {
  env VALHEIM_ROOT="$fleet" VALHEIM_SETTINGS_HISTORY="$tmp/history" \
    bash "$ADMIN" "$WORLD" "$PROFILE" "$@" >"$tmp/out" 2>"$tmp/err"
}

# 1. The Ulfsland shape: the archive stores plugins/TestMod.dll, so the extractor would
#    write TestMod/TestMod.dll, but the cache has TestMod/plugins/TestMod.dll. Refuse,
#    name the file, name the repair, and leave the live tree alone.
reset_fixture "plugins/$PACKAGE.dll"
mkdir -p "$cached/plugins"
printf 'DLL\n' >"$cached/plugins/$PACKAGE.dll"
printf 'LIVE\n' >"$plugins/$PACKAGE/$PACKAGE.dll"
rc=0
run_admin deploy || rc=$?
[[ $rc -ne 0 ]] || fail "a stale cache entry was deployed instead of refused: $(cat "$tmp/out")"
grep -q "does not match its own archive" "$tmp/err" ||
  fail "the refusal does not say the cache disagrees with its archive: $(cat "$tmp/err")"
grep -q "$IDENTIFIER is missing $PACKAGE.dll" "$tmp/err" ||
  fail "the refusal does not name the missing file: $(cat "$tmp/err")"
grep -q "sync $IDENTIFIER" "$tmp/err" ||
  fail "the refusal does not name a repair command: $(cat "$tmp/err")"
[[ $(cat "$plugins/$PACKAGE/$PACKAGE.dll") == LIVE ]] ||
  fail "a refused deploy still rewrote the live plugin tree"

# 2. The same archive, correctly extracted. Must deploy: a guard that refuses the healthy
#    fleet is worse than the bug. All 92-94 packages of every other profile are this case.
reset_fixture "plugins/$PACKAGE.dll"
printf 'DLL\n' >"$cached/$PACKAGE.dll"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "a correctly flattened cache entry was refused: $(cat "$tmp/err")"
[[ -f "$plugins/$PACKAGE/$PACKAGE.dll" ]] || fail "the flattened package did not deploy"

# 3. An author who really does ship plugins/plugins/<dll>: after the prefix is stripped the
#    cache legitimately holds TestMod/plugins/TestMod.dll - byte-identical on disk to case 1
#    - and this must deploy. This is why the check reads the archive instead of refusing any
#    directory called `plugins`, and it is the case a name rule gets wrong.
reset_fixture "plugins/plugins/$PACKAGE.dll"
mkdir -p "$cached/plugins"
printf 'DLL\n' >"$cached/plugins/$PACKAGE.dll"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "a legitimate nested author subfolder was refused: $(cat "$tmp/err")"
[[ -f "$plugins/$PACKAGE/plugins/$PACKAGE.dll" ]] ||
  fail "the nested package did not deploy at its own layout"

# 4. deploy-plan reports the stale entry without touching anything, which is the only
#    check available before a maintenance window stops the world on it.
reset_fixture "plugins/$PACKAGE.dll"
mkdir -p "$cached/plugins"
printf 'DLL\n' >"$cached/plugins/$PACKAGE.dll"
printf 'LIVE\n' >"$plugins/$PACKAGE/$PACKAGE.dll"
rc=0
run_admin deploy-plan || rc=$?
[[ $rc -eq 0 ]] || fail "deploy-plan failed on a stale cache entry: $(cat "$tmp/err")"
grep -q "^cache_stale=$IDENTIFIER missing=$PACKAGE.dll\$" "$tmp/out" ||
  fail "deploy-plan does not report the stale cache entry: $(cat "$tmp/out")"
[[ $(cat "$plugins/$PACKAGE/$PACKAGE.dll") == LIVE ]] || fail "deploy-plan wrote to the plugin tree"

[[ $failures -eq 0 ]] || { echo "$failures stale-cache check(s) failed" >&2; exit 1; }
echo "PASS: portal_mod_admin.sh deploy refuses a cache entry that disagrees with its archive"
