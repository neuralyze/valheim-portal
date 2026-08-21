#!/usr/bin/env bash
# Proves portal_world_mod_catalog.sh refuses what it cannot serve and names every refusal.
#
# This script reaches a player-facing page, so a bad argument must not arrive at the controller as
# a traceback: the portal renders stderr as the failure, and "invalid world" is an answer while a
# Python stack trace is not. The `state` mode is also the one the portal calls on every world page
# view, so a silent misspelling of it would turn a cheap check into the expensive build.
#
# Run: bash hostops/tests/portal_world_mod_catalog_messages.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CATALOG="$SCRIPT_DIR/../portal_world_mod_catalog.sh"

tmp=$(mktemp -d /tmp/mod-catalog-msg.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

mkdir -p "$tmp/worlds/Hrafnheim/config_merged/bepinex/plugins"
for edition in vr flat admin; do
	mkdir -p "$tmp/worlds/profiles/$edition"
done
# Two player editions and an admin one. PlantEasily is installed everywhere; ValheimRcon only in
# admin, so a correct derivation drops it without naming it.
cat >"$tmp/worlds/profiles/vr/profile-manifest.json" <<'JSON'
{"profile_name":"vr","schema_version":3,
 "packages":[{"identifier":"Advize-PlantEasily","version":"2.1.1","scope":"shared"}],
 "client_only_packages":[],"disabled_packages":[],"custom_packages":[],"excluded_packages":[]}
JSON
cp "$tmp/worlds/profiles/vr/profile-manifest.json" "$tmp/worlds/profiles/flat/profile-manifest.json"
cat >"$tmp/worlds/profiles/admin/profile-manifest.json" <<'JSON'
{"profile_name":"admin","schema_version":3,
 "packages":[{"identifier":"Advize-PlantEasily","version":"2.1.1","scope":"shared"},
             {"identifier":"Tristan-ValheimRcon","version":"1.4.0","scope":"shared"}],
 "client_only_packages":[],"disabled_packages":[],"custom_packages":[],"excluded_packages":[]}
JSON

export VALHEIM_ROOT="$tmp/worlds"
export PORTAL_TOOLS_DIR="$SCRIPT_DIR/../../tools"

failures=0

expect_reject() { # <expected stderr substring> <args...>
	local want=$1
	shift
	local rc=0
	bash "$CATALOG" "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
	if [[ $rc -eq 0 ]]; then
		echo "FAIL: [$*] exit 0, want non-zero" >&2
		failures=$((failures + 1))
	elif ! grep -qF -- "$want" "$tmp/err"; then
		echo "FAIL: [$*] stderr = '$(cat "$tmp/err")', want substring '$want'" >&2
		failures=$((failures + 1))
	fi
}

# A world is a path component on the host, so traversal and separators are refused before any
# Python runs.
expect_reject "invalid world" ../etc
expect_reject "invalid world" "Hrafn/heim"
expect_reject "invalid world" ""

# The mode is a closed set. A typo must be refused rather than silently treated as the full build,
# which is the several-megabyte Thunderstore fetch the cache exists to avoid.
expect_reject "unsupported mode 'statte'" Hrafnheim statte
expect_reject "usage: portal_world_mod_catalog.sh" Hrafnheim state extra

# The cheap mode answers with a fingerprint and reads no network.
if ! bash "$CATALOG" Hrafnheim state >"$tmp/state" 2>"$tmp/err"; then
	echo "FAIL: state mode exited non-zero: $(cat "$tmp/err")" >&2
	failures=$((failures + 1))
elif ! grep -qE '^\{"fingerprint":"[0-9a-f]{64}"\}$' "$tmp/state"; then
	echo "FAIL: state mode printed '$(cat "$tmp/state")', want a 64-hex fingerprint" >&2
	failures=$((failures + 1))
fi

# The same fingerprint twice: it is a staleness check, so it must be stable when nothing moved.
if [[ $(bash "$CATALOG" Hrafnheim state) != $(bash "$CATALOG" Hrafnheim state) ]]; then
	echo "FAIL: the fingerprint changed between two reads of an unchanged profile set" >&2
	failures=$((failures + 1))
fi

# Adding to the admin edition alone must not move it: it cannot change what a player sees.
before=$(bash "$CATALOG" Hrafnheim state)
python3 - "$tmp/worlds/profiles/admin/profile-manifest.json" <<'PY'
import json, sys
path = sys.argv[1]
manifest = json.load(open(path, encoding="utf-8"))
manifest["packages"].append({"identifier": "sighsorry-AdminQoL", "version": "1.0.0", "scope": "shared"})
json.dump(manifest, open(path, "w", encoding="utf-8"))
PY
if [[ $(bash "$CATALOG" Hrafnheim state) != "$before" ]]; then
	echo "FAIL: an admin-only addition moved the player fingerprint" >&2
	failures=$((failures + 1))
fi

# Adding to a player edition must move it, or the check would never fire.
python3 - "$tmp/worlds/profiles/vr/profile-manifest.json" <<'PY'
import json, sys
path = sys.argv[1]
manifest = json.load(open(path, encoding="utf-8"))
manifest["packages"].append({"identifier": "Azumatt-FastLink", "version": "1.0.4", "scope": "shared"})
json.dump(manifest, open(path, "w", encoding="utf-8"))
PY
if [[ $(bash "$CATALOG" Hrafnheim state) == "$before" ]]; then
	echo "FAIL: an addition to a player edition did not move the fingerprint" >&2
	failures=$((failures + 1))
fi

if [[ $failures -ne 0 ]]; then
	echo "FAIL: $failures refusal(s) or read(s) misreported" >&2
	exit 1
fi
echo "PASS: portal_world_mod_catalog.sh refuses and fingerprints as documented"
