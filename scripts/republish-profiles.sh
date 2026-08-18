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
#   1. builds the profile definition from profiles/<SOURCE>/profile-manifest.json in the shared
#      profile store, naming it with the PUBLISHED profile, which is what the release scope requires
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
#   VALHEIM_PROFILE_SOURCE_ROOT  required  root holding profiles/<PROFILE> and one dir per world
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

# One row per target: world, source profile, published profile, client type, ValheimVR.
#
# valheim_vr is declared per target and has no default. It used to be inferred from the
# published name matching *nonvr*, which is a trap: rename a profile to "non-vr" and the
# match silently fails, shipping ValheimVR to the players who asked not to have it.
plan=$(python3 - "$targets" <<'PY'
import json, sys
catalog = json.load(open(sys.argv[1]))
for client_type in ("flat", "vr"):
    for entry in catalog.get(client_type) or []:
        if not isinstance(entry.get("valheim_vr"), bool):
            raise SystemExit(f'target {entry.get("published_profile")!r} must declare valheim_vr')
        if entry.get("audience") not in ("player", "admin"):
            raise SystemExit(f'target {entry.get("published_profile")!r} must declare audience player or admin')
        print(entry["world"], entry["source_profile"], entry["published_profile"],
              client_type, str(entry["valheim_vr"]).lower(), entry["audience"])
PY
)
[[ -n $plan ]] || { echo "no targets in $targets" >&2; exit 1; }

# Publishing does not touch a server; deploying its plugin folder does, and that needs the world
# down. The two used to be welded together here, so a client-side config change - a logging flag -
# cost every player a world restart and several minutes of mod loading. So the guard now asks the
# question it actually cares about: would deploying this profile change the server's plugins? If the
# set already on disk matches the one the profile would install, nothing needs to stop.
# A checksum of the NUL-separated, sorted entry names directly inside a directory, or of nothing
# when it does not exist. `ls -1` read more naturally and tripped SC2012, but simply swapping in
# `find -printf '%f\n'` would have silenced the linter while keeping the flaw: plugin folders are
# named by mod authors, and with newline-separated output one directory called "A<newline>B"
# renders exactly like two called "A" and "B", so a real difference compares equal and a pending
# deploy is missed. Measured both ways - newline-separated misses it, NUL-separated catches it.
# Bash cannot hold a NUL in a variable, so the comparison is over checksums rather than lists.
# Dotfiles stay excluded, as `ls` excluded them, so a stray editor swap file is not a mod change.
plugin_entries() {
  [[ -d $1 ]] || return 0
  find "$1" -mindepth 1 -maxdepth 1 ! -name '.*' -printf '%f\0'
}

server_plugin_change() {
  local world=$1 profile=$2
  local staged="$source_root/profiles/$profile/manager-cache/server/BepInEx/plugins"
  local manual="$source_root/profiles/$profile/manual-mods"
  local deployed="$source_root/$world/config_merged/bepinex/plugins"
  [[ -d $staged && -d $deployed ]] || return 0
  local want have
  want=$( { plugin_entries "$staged"; plugin_entries "$manual"; } | sort -zu | md5sum)
  have=$(plugin_entries "$deployed" | sort -zu | md5sum)
  [[ $want != "$have" ]]
}

# The profile a world's SERVER runs, which is not the profile a client edition is built
# from: a world publishes editions sourced from several primaries, and only the linked one
# says anything about the plugins on that server. Comparing a target's source profile here
# refused every publish, because a flat or vr edition legitimately stages a different set
# than the admin profile the servers run.
linked_profile() {
  local link="$source_root/$1/mods/.active-mod-profile"
  [[ -f $link ]] || return 0
  tr -d '[:space:]' <"$link"
}

# Checked for every world before publishing anything, so a refusal never leaves some worlds updated
# and others not. One check per world rather than per target, so a world is named once.
running=()
while read -r world; do
  linked=$(linked_profile "$world")
  [[ -n $linked ]] || continue
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "valheim-server-$world" && server_plugin_change "$world" "$linked"; then
    running+=("$world")
  fi
done <<<"$(cut -d' ' -f1 <<<"$plan" | sort -u)"
if ((${#running[@]})); then
  printf 'refusing to start: these worlds are running and their server plugins would change, stop them first: %s\n' "$(printf '%s ' "${running[@]}")" >&2
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
while read -r world source published client_type valheim_vr audience; do
  profile_dir=$source_root/profiles/$source
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

  # This server's own setting overrides, applied per KEY over the profile's values. The
  # profile is shared, so a value only one server needs - the address a link mod
  # advertises - lives here rather than being edited into the shared config. A whole-file
  # override would freeze every other setting in that file at today's value, which is the
  # drift that left four worlds with four different mod sets.
  overrides=$source_root/$world/mods/overrides/client
  if [[ -d $overrides ]]; then
    layered=$build_dir/config-$published-layered
    while read -r touched; do
      [[ -n $touched ]] && printf 'override %s ' "$touched"
    done < <(python3 "$repo_root/tools/config_merge.py" tree "$merged" "$overrides" "$layered")
    rm -rf -- "$merged"
    mv -- "$layered" "$merged"
  fi

  build_args=(-world "$world" -profile "$published" -client-type "$client_type" -audience "$audience"
              -source-manifest "$profile_dir/profile-manifest.json"
              -config-dir "$merged" -output "$payload")
  publish_args=(-world "$world" -profile "$published" -client-type "$client_type"
                -version "$version" -profile-payload "$payload" -audience "$audience"
                -notes "$notes" -actor republish-profiles
                -database "$database" -artifact-root "$artifact_root")

  # Carry the client plugin forward. A republish rebuilds the mod list, not the plugin, so a
  # release that omits it silently uninstalls the current one from every client on next sync -
  # which is exactly what a routine "remove one mod" republish did on 2026-08-13.
  plugin=$(python3 - "$database" "$published" <<'PY'
import sqlite3, sys
db, profile = sys.argv[1:3]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = c.execute(
    "select a.path from artifacts a join releases r on a.release_id=r.id "
    "where r.profile=? and a.kind='diag_plugin' order by a.created_at desc limit 1",
    (profile,)).fetchone()
print(row[0] if row else "")
PY
)
  # An explicit build wins over the carried-forward one, so shipping a new plugin does not require
  # publishing it by hand first and then republishing to pick it up.
  [[ -n ${VALHEIM_CLIENT_PLUGIN:-} ]] && plugin=$VALHEIM_CLIENT_PLUGIN
  if [[ -n $plugin && -f $plugin ]]; then
    publish_args+=(-diag-plugin "$plugin")
  elif [[ -n ${VALHEIM_CLIENT_PLUGIN:-} ]]; then
    echo "VALHEIM_CLIENT_PLUGIN is not a file: $VALHEIM_CLIENT_PLUGIN"; ((failures++)); continue
  fi

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
      # An explicit build wins, so a runtime fix - removing a diagnostic mod bundled inside it -
      # ships without hand-publishing first and republishing to pick it up.
      [[ -n ${VALHEIM_VR_RUNTIME:-} ]] && runtime=$VALHEIM_VR_RUNTIME
      [[ -n $runtime && -f $runtime ]] || { echo "no VR runtime found for $published"; ((failures++)); continue; }
      publish_args+=(-vr-runtime "$runtime")
      ;;
    flat)
      # A true non-VR profile ships neither ValheimVR nor a companion; every other Flat one
      # needs the reviewed companion. The catalog says which this is.
      if [[ $valheim_vr == false ]]; then
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
    kept="${TMPDIR:-/tmp}/republish-$published-$(date -u +%Y%m%dT%H%M%SZ).log"
    cp "$build_dir/$published.log" "$kept" 2>/dev/null || true
    echo "PUBLISH FAILED: $(tail -1 "$build_dir/$published.log") (full log: $kept)"; ((failures++)); continue
  fi
  echo "published and verified"
done <<<"$plan"

if ((failures)); then
  printf '\n%d target(s) failed; logs in %s were removed on exit, rerun a single target to see them\n' "$failures" "$build_dir" >&2
  exit 1
fi
echo
echo "all targets published. Each world still needs: manage_mods.sh <WORLD> deploy --apply, then start."
