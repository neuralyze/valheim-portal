#!/usr/bin/env bash
# Proves `portal_mod_admin.sh <world> <profile> deploy` carries generated per-world
# server config, and says so out loud when a deploy removes a file nothing replaces.
#
# The incident this covers, measured 2026-09-15 on Ulfsland: an admin-mode window
# deployed profile ulfsland-admin, the rebuilt plugins/ServerCharacters/ came back
# without CharacterTemplate.yml, and every character created afterwards got no kit and
# no spawn point. The file is generated per world AND per preset by tools/jumpstart, so
# it is in no Thunderstore package and - the actual cause - its durable home was the
# manual-mods of a DIFFERENT profile (ulfsland-dn, the one the world links to), which
# the admin-profile deploy never reads. cmd_deploy replaces the plugin tree wholesale,
# so the loss was total and silent.
#
# Everything here runs against a throwaway fleet under /tmp with no container, no live
# world and its own settings-history store.
# Run: bash hostops/tests/portal_mod_admin_generated_config.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ADMIN="$SCRIPT_DIR/../portal_mod_admin.sh"
WORLD=Fixtureheim
PROFILE=fixture-admin
TEMPLATE=ServerCharacters/CharacterTemplate.yml

tmp=$(mktemp -d /tmp/mod-admin-generated.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

fleet="$tmp/fleet"
world="$fleet/$WORLD"
profile="$fleet/profiles/$PROFILE"
plugins="$world/config_merged/bepinex/plugins"

# A minimal but real fixture: one package in the profile's server cache, an empty
# manual-mods (the deploy refuses without it), and no admin-mode overlay.
reset_fixture() {
  rm -rf -- "$fleet"
  mkdir -p "$profile/manager-cache/server/BepInEx/plugins/ServerCharacters"
  mkdir -p "$profile/manual-mods" "$world/mods" "$plugins/ServerCharacters"
  printf '{"packages": [], "client_only_packages": []}\n' >"$profile/profile-manifest.json"
  printf 'DLL\n' >"$profile/manager-cache/server/BepInEx/plugins/ServerCharacters/ServerCharacters.dll"
  printf 'DLL\n' >"$plugins/ServerCharacters/ServerCharacters.dll"
}

# The deploy is a HISTORY command, so it snapshots fleet settings; point that store at
# the sandbox too, or the test would commit into the operator's real history.
run_admin() {
  env VALHEIM_ROOT="$fleet" VALHEIM_SETTINGS_HISTORY="$tmp/history" \
    bash "$ADMIN" "$WORLD" "$PROFILE" "$@" >"$tmp/out" 2>"$tmp/err"
}

# 1. A generated file declared in the world's own mods/generated/ must land in the
#    deployed plugin tree. This is the fix: the file becomes a deploy SOURCE, so no
#    choice of profile can leave it out.
reset_fixture
mkdir -p "$world/mods/generated/ServerCharacters"
printf 'spawn:\n  - {x: -4673, y: 73, z: -350}\n' >"$world/mods/generated/$TEMPLATE"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy failed: rc=$rc stderr=$(cat "$tmp/err")"
if [[ -f "$plugins/$TEMPLATE" ]]; then
  diff -q "$world/mods/generated/$TEMPLATE" "$plugins/$TEMPLATE" >/dev/null ||
    fail "deployed $TEMPLATE differs from the generated source"
else
  fail "deploy dropped the generated $TEMPLATE; mods/generated is not a deploy source"
fi
grep -q "^generated_placed=$TEMPLATE\$" "$tmp/out" ||
  fail "deploy did not report placing $TEMPLATE: $(cat "$tmp/out")"

# 2. The generated file survives repeat deploys, including the second one whose backup
#    holds the first deploy's tree. A fix that works once and loses the file on the
#    next window is the same defect with a delay.
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "second deploy failed: rc=$rc stderr=$(cat "$tmp/err")"
[[ -f "$plugins/$TEMPLATE" ]] || fail "second deploy dropped the generated $TEMPLATE"

# 3. A file present only in the live tree - what the hand-restored template was, and
#    what any hand-placed config is - still gets removed, because a deploy is a rebuild
#    from sources. It must now SAY so, naming the file and where to recover it. The
#    hour this cost was spent on a deletion nobody was told about.
reset_fixture
printf 'HAND PLACED\n' >"$plugins/$TEMPLATE"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy over an unmanaged file failed: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^deploy_dropped=$TEMPLATE\$" "$tmp/out" ||
  fail "deploy removed $TEMPLATE without reporting it: $(cat "$tmp/out")"
grep -q '^deploy_dropped_files=1$' "$tmp/out" ||
  fail "deploy did not count the files it removed: $(cat "$tmp/out")"
grep -q 'server-plugins.previous' "$tmp/out" ||
  fail "deploy did not name the backup holding the removed file: $(cat "$tmp/out")"
grep -q "mods/generated" "$tmp/err" ||
  fail "the removal warning does not point at the layer that would have carried it: $(cat "$tmp/err")"

# 4. A package the sources no longer carry is a removal the operator asked for, so it
#    reports once by name instead of once per file. Otherwise removing one large mod
#    buries the line that matters in hundreds of expected ones.
reset_fixture
mkdir -p "$plugins/RemovedMod/sub"
printf 'A\n' >"$plugins/RemovedMod/a.dll"
printf 'B\n' >"$plugins/RemovedMod/sub/b.dll"
rc=0
run_admin deploy || rc=$?
[[ $rc -eq 0 ]] || fail "deploy over a removed package failed: rc=$rc stderr=$(cat "$tmp/err")"
grep -q '^deploy_removed=RemovedMod$' "$tmp/out" ||
  fail "deploy did not name the package it removed: $(cat "$tmp/out")"
# `grep && fail` would abort the run under set -e on the passing case, so the negative
# assertions are written as conditions.
if grep -q '^deploy_dropped=' "$tmp/out"; then
  fail "a removed package was reported per file instead of once: $(cat "$tmp/out")"
fi
if grep -q 'warning:' "$tmp/err"; then
  fail "an expected package removal warned as if it were a loss: $(cat "$tmp/err")"
fi

# 5. deploy-plan answers "will my generated config be carried?" without touching
#    anything, which is the only check available while a world is being played.
reset_fixture
mkdir -p "$world/mods/generated/ServerCharacters"
printf 'spawn:\n' >"$world/mods/generated/$TEMPLATE"
rm -f "$plugins/$TEMPLATE"
rc=0
run_admin deploy-plan || rc=$?
[[ $rc -eq 0 ]] || fail "deploy-plan failed: rc=$rc stderr=$(cat "$tmp/err")"
grep -q "^generated_file=$TEMPLATE\$" "$tmp/out" ||
  fail "deploy-plan does not name the generated files a deploy would carry: $(cat "$tmp/out")"
[[ ! -f "$plugins/$TEMPLATE" ]] || fail "deploy-plan wrote to the plugin tree"

[[ $failures -eq 0 ]] || { echo "$failures generated-config check(s) failed" >&2; exit 1; }
echo "PASS: portal_mod_admin.sh deploy carries generated per-world config"
