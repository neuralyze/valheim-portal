#!/usr/bin/env bash
set -euo pipefail

# Rebuild and republish every configured profile from the current managed mod lists.
#
# This exists because doing it by hand is where the mistakes come from. Republishing ten
# profiles on 2026-08-06 meant ten invocations of seed-release, each needing the published
# profile name (not the source profile name), the right client type, a bumped version, the
# world's own VR runtime filename, and the Flat companion. Getting one flag wrong produced a
# release that published cleanly and served 404 to every client, and getting the profile name
# wrong produced "profile manifest does not match its release" ten times over. None of that is
# judgement; it is bookkeeping, so it belongs in a script.
#
# What it does per target in release-targets.json:
#   1. builds the profile definition from <WORLD>/mods/profiles/<SOURCE>/profile-manifest.json,
#      naming it with the PUBLISHED profile, which is what the release scope requires
#   2. publishes it with seed-release, which now refuses a non-deployed artifact root and reads
#      every artifact back through its recorded path before reporting success
#   3. bumps the patch version from whatever is currently published for that scope
#
# Refuses to touch a world whose server is running, because deploying a new plugin set needs it
# stopped and a half-applied world is worse than an untouched one.
#
# usage: republish-profiles.sh NOTES [TARGETS_JSON]
#
# Environment:
#   VALHEIM_PROFILE_SOURCE_ROOT  required  world root holding <WORLD>/mods/profiles/<PROFILE>
#   VALHEIM_FLAT_COMPANION       required for flat targets  reviewed Flat companion ZIP
#   PORTAL_DATABASE              optional  defaults to the deployed database
#   PORTAL_ARTIFACT_ROOT         optional  defaults to the deployed artifact root
#   DRY_RUN=1                    optional  build and report, publish nothing

usage() {
  sed -n '3,31p' "${BASH_SOURCE[0]}" >&2
  exit 2
}

(($# == 1 || $# == 2)) || usage
notes=$1
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
targets=${2:-$repo_root/release-targets.json}
source_root=${VALHEIM_PROFILE_SOURCE_ROOT:-}
companion=${VALHEIM_FLAT_COMPANION:-}
database=${PORTAL_DATABASE:-/var/lib/valheim-portal/portal.sqlite}
artifact_root=${PORTAL_ARTIFACT_ROOT:-/var/lib/valheim-portal/artifacts}
dry_run=${DRY_RUN:-0}

[[ -n $source_root && -d $source_root ]] ||
  { echo "VALHEIM_PROFILE_SOURCE_ROOT is not set to a directory" >&2; exit 78; }
[[ -f $targets ]] || { echo "no release targets file: $targets" >&2; exit 78; }

build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT

# One row per target: world, source profile, published profile, client type.
plan=$(python3 - "$targets" <<'PY'
import json, sys
catalog = json.load(open(sys.argv[1]))
for client_type in ("flat", "vr"):
    for entry in catalog.get(client_type) or []:
        print(entry["world"], entry["source_profile"], entry["published_profile"], client_type)
PY
)
[[ -n $plan ]] || { echo "no targets in $targets" >&2; exit 1; }

# A running world cannot take a new plugin set, and finding that out halfway through leaves some
# worlds updated and others not. Check them all before publishing anything.
running=()
while read -r world _ _ _; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "valheim-server-$world"; then
    running+=("$world")
  fi
done <<<"$plan"
if ((${#running[@]})); then
  printf 'refusing to start: these worlds are running, stop them first: %s\n' "$(printf '%s ' "${running[@]}")" >&2
  exit 1
fi

next_version() {
  python3 - "$database" "$1" "$2" "$3" <<'PY'
import sqlite3, sys
db, world, profile, client_type = sys.argv[1:5]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = c.execute(
    "select version from releases where world=? and profile=? and client_type=? "
    "order by created_at desc limit 1", (world, profile, client_type)).fetchone()
if not row:
    print("1.0.0"); raise SystemExit
parts = (row[0].split(".") + ["0", "0"])[:3]
try:
    parts[2] = str(int(parts[2]) + 1)
except ValueError:
    parts[2] = "1"
print(".".join(parts))
PY
}

failures=0
while read -r world source published client_type; do
  profile_dir=$source_root/$world/mods/profiles/$source
  version=$(next_version "$world" "$published" "$client_type")
  payload=$build_dir/$published-profile.zip
  printf '%-20s %-4s %-8s ' "$published" "$client_type" "$version"

  # Merge the shared client config with the type overlay, exactly as build-profile-definition.sh
  # does, so the shipped config matches what a hand build would produce.
  merged=$build_dir/config-$published
  mkdir -p "$merged"
  cp -a -- "$profile_dir/client-config/." "$merged/"
  [[ -d "$profile_dir/client-config-$client_type" ]] &&
    cp -a -- "$profile_dir/client-config-$client_type/." "$merged/"

  build_args=(-world "$world" -profile "$published" -client-type "$client_type"
              -source-manifest "$profile_dir/profile-manifest.json"
              -config-dir "$merged" -output "$payload")
  publish_args=(-world "$world" -profile "$published" -client-type "$client_type"
                -version "$version" -profile-payload "$payload"
                -notes "$notes" -actor republish-profiles
                -database "$database" -artifact-root "$artifact_root")

  case "$client_type" in
    vr)
      # Each world publishes its own runtime under its own filename; reuse the one attached to
      # the newest release for this scope rather than guessing a name.
      runtime=$(python3 - "$database" "$published" <<'PY'
import sqlite3, sys
db, profile = sys.argv[1:3]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = c.execute(
    "select a.path from artifacts a join releases r on a.release_id=r.id "
    "where r.profile=? and a.kind='vr_runtime' order by a.created_at desc limit 1",
    (profile,)).fetchone()
print(row[0] if row else "")
PY
)
      [[ -n $runtime && -f $runtime ]] || { echo "no VR runtime found for $published"; ((failures++)); continue; }
      publish_args+=(-vr-runtime "$runtime")
      ;;
    flat)
      # A true non-VR profile ships neither ValheimVR nor a companion; every other Flat one needs
      # the reviewed companion.
      if [[ $published == *nonvr* ]]; then
        build_args+=(-true-nonvr)
      else
        [[ -n $companion && -f $companion ]] ||
          { echo "VALHEIM_FLAT_COMPANION is required for $published"; ((failures++)); continue; }
        build_args+=(-flat-companion "$companion")
        publish_args+=(-flat-companion "$companion")
      fi
      ;;
  esac

  if ! (cd "$repo_root" && go run ./cmd/profile-definition-builder "${build_args[@]}") >"$build_dir/$published.log" 2>&1; then
    echo "BUILD FAILED: $(tail -1 "$build_dir/$published.log")"; ((failures++)); continue
  fi
  if [[ $dry_run == 1 ]]; then
    echo "built (dry run, not published)"; continue
  fi
  if ! (cd "$repo_root" && go run ./cmd/seed-release "${publish_args[@]}") >>"$build_dir/$published.log" 2>&1; then
    echo "PUBLISH FAILED: $(tail -1 "$build_dir/$published.log")"; ((failures++)); continue
  fi
  echo "published and verified"
done <<<"$plan"

if ((failures)); then
  printf '\n%d target(s) failed; logs in %s were removed on exit, rerun a single target to see them\n' "$failures" "$build_dir" >&2
  exit 1
fi
echo
echo "all targets published. Each world still needs: manage_mods.sh <WORLD> deploy --apply, then start."
