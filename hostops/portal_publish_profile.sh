#!/usr/bin/env bash
set -euo pipefail

# Publish one profile for one world, for the agent surface.
#
# Deliberately narrower than scripts/republish-profiles.sh, which publishes a whole catalog: this
# takes a world, its source profile and a client type, resolves the single matching target out of
# release-targets.json, and publishes that. Two things a caller may NOT supply:
#
#   * artifact paths. The publish script carries the newest plugin and VR runtime forward from the
#     profile's own last release when the environment is unset, so an agent can never point a
#     release at an arbitrary file on this host.
#   * a target list. It is derived from the catalog, so a request can only publish something an
#     operator already declared.
#
# Notes are mandatory and become the release note. A publish without a reason is exactly what
# nobody could review afterwards.

WORLD=${1:-}
PROFILE=${2:-}
CLIENT_TYPE=${3:-}
NOTES=${4:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root

reject() { echo "$*" >&2; exit 2; }

[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || reject "invalid world"
[[ $PROFILE =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || reject "invalid profile"
[[ $CLIENT_TYPE == vr || $CLIENT_TYPE == flat ]] || reject "client type must be vr or flat, got '$CLIENT_TYPE'"
[[ ${#NOTES} -ge 8 && ${#NOTES} -le 500 ]] || reject "notes must be 8-500 characters, so the release says why it exists"
[[ $NOTES != *$'\n'* && $NOTES != *$'\r'* ]] || reject "notes must be a single line"

REPO_ROOT=${PORTAL_REPO_ROOT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}
CATALOG=${VALHEIM_RELEASE_TARGETS:-$REPO_ROOT/release-targets.json}
[[ -f $CATALOG ]] || reject "no release targets catalog: $CATALOG"

# One target, taken from the catalog rather than from the request.
TARGET_FILE=$(mktemp)
trap 'rm -f -- "$TARGET_FILE"' EXIT
python3 - "$CATALOG" "$WORLD" "$PROFILE" "$CLIENT_TYPE" "$TARGET_FILE" <<'PY' || exit $?
import json, sys

catalog_path, world, profile, client_type, out_path = sys.argv[1:6]
catalog = json.load(open(catalog_path))
# Matched on the PUBLISHED profile, which is what the verb names and what a release is
# scoped to. Matching the source profile stopped resolving the moment a world published
# more than one edition from the same primary: "flat" now feeds both <world>-vr-flat and
# <world>-non-vr, so every Flat publish saw two targets and refused.
matches = [
    entry for entry in (catalog.get(client_type) or [])
    if entry.get("world") == world and entry.get("published_profile") == profile
]
if len(matches) != 1:
    names = [f'{e.get("world")}/{e.get("published_profile")}' for e in (catalog.get(client_type) or [])]
    print(
        f"catalog has {len(matches)} {client_type} targets for {world}/{profile}; "
        f"declared: {', '.join(names) or 'none'}",
        file=sys.stderr,
    )
    raise SystemExit(2)
json.dump({"schema": 1, "flat": [], "vr": [], client_type: matches}, open(out_path, "w"))
print(f"publishing {matches[0]['published_profile']} ({client_type}) for {world} from {matches[0]['source_profile']}")
PY

exec env VALHEIM_PROFILE_SOURCE_ROOT="${VALHEIM_PROFILE_SOURCE_ROOT:-$VALHEIM_ROOT}" \
  "$REPO_ROOT/scripts/republish-profiles.sh" "$NOTES" "$TARGET_FILE"
