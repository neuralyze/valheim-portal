#!/usr/bin/env bash
set -euo pipefail

WORLD=${1:-}
ADMIN_ARG=${2:-}
PERMITTED_ARG=${3:-}
(($# == 3)) || { echo "usage: portal_access_lists.sh <world> <admin_ids> <permitted_ids>" >&2; exit 2; }
[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root
WORLD_ROOT=$(realpath -- "$VALHEIM_ROOT")
ADMIN_HEADER='// List admin players ID  ONE per line'
PERMITTED_HEADER='// List permitted players ID ONE per line'
TEMPORARIES=()

cleanup() { (( ${#TEMPORARIES[@]} == 0 )) || rm -f -- "${TEMPORARIES[@]}"; }

parse_ids() {
  local label=$1 argument=$2 entry
  local -n ids=$3
  local -A seen=()
  ids=()
  [[ $argument != - ]] || return 0
  [[ -n $argument ]] || { echo "invalid $label list: use - for an empty list" >&2; exit 2; }
  mapfile -t ids < <(printf '%s\n' "$argument" | tr ',' '\n')
  (( ${#ids[@]} <= 200 )) || { echo "invalid $label list: more than 200 entries" >&2; exit 2; }
  for entry in "${ids[@]}"; do
    [[ $entry =~ ^7[0-9]{16}$ ]] || { echo "invalid $label id: $entry" >&2; exit 2; }
    [[ -z ${seen[$entry]:-} ]] || { echo "duplicate $label id: $entry" >&2; exit 2; }
    seen[$entry]=1
  done
}

# Refuses anything but a missing path or a plain file, so no write ever follows a
# symlink out of the world and no list is applied before every target is sane.
require_plain() {
  local path=$1 label
  label=$(basename -- "$path")
  [[ ! -L $path ]] || { echo "$label is a symlink" >&2; exit 2; }
  [[ ! -e $path || -f $path ]] || { echo "$label is not a regular file" >&2; exit 2; }
}

# Staging and renaming replaces the inode, so the generated file inherits the
# caller's ownership. The agent is unprivileged and cannot chown it back, which
# silently left these files owned by the agent and read-only for the operator.
# Restore what the caller is allowed to: root restores owner and group, and
# anyone else keeps the shared group and grants the previous owner explicit rw
# through an ACL, so a generated file never stops being editable or recoverable.
preserve_ownership() {
  local reference=$1 staged=$2 owner
  [[ -e $reference ]] || return 0
  chown --reference="$reference" "$staged" 2>/dev/null && return 0
  chgrp --reference="$reference" "$staged" 2>/dev/null || true
  owner=$(stat -c '%U' -- "$reference" 2>/dev/null) || return 0
  [[ -n $owner && $owner != "$(id -un)" ]] || return 0
  command -v setfacl >/dev/null 2>&1 || return 0
  setfacl -m "u:$owner:rw" "$staged" 2>/dev/null || true
}

# Stages the intended list beside its destination and moves it into place only
# when the bytes differ, so untouched lists keep their modification time.
#
# $3 names the caller's ID array and $4 the caller's status variable. Both are
# namerefs, which shellcheck reads as plain locals in this scope: it sees an
# array used as a string, and a status nobody consumes.
# shellcheck disable=SC2178,SC2034  # namerefs into the caller's scope
apply_list() {
  local path=$1 header=$2 label
  local -n ids=$3
  local -n state=$4
  local temporary
  label=$(basename -- "$path")
  temporary=$(mktemp --tmpdir="$CONFIG_DIR" ".$label.XXXXXX")
  TEMPORARIES+=("$temporary")
  { printf '%s\n' "$header"; (( ${#ids[@]} == 0 )) || printf '%s\n' "${ids[@]}"; } > "$temporary"
  if [[ -f $path ]] && cmp -s -- "$temporary" "$path"; then
    rm -f -- "$temporary"
    state=unchanged
    return 0
  fi
  if [[ -e $path ]]; then chmod --reference="$path" "$temporary"; else chmod 644 "$temporary"; fi
  preserve_ownership "$path" "$temporary"
  mv -- "$temporary" "$path"
  state=written
}

parse_ids admin "$ADMIN_ARG" ADMIN_IDS
parse_ids permitted "$PERMITTED_ARG" PERMITTED_IDS

WORLD_DIR=$(realpath -- "$WORLD_ROOT/$WORLD")
[[ $WORLD_DIR == "$WORLD_ROOT/"* && -d $WORLD_DIR && ! -L "$WORLD_ROOT/$WORLD" ]] || { echo "World is unavailable" >&2; exit 2; }
CONFIG_DIR="$WORLD_DIR/config_merged"
[[ -d $CONFIG_DIR && ! -L $CONFIG_DIR ]] || { echo "World configuration directory is unavailable" >&2; exit 2; }
ENV_FILE="$WORLD_DIR/valheim.env"

# The lock lives in the world directory: that is the only tree the hardened
# agent unit may write, and the lock is per world anyway.
exec 9>"$WORLD_DIR/.access-lists.lock"
flock -x 9
trap cleanup EXIT
require_plain "$CONFIG_DIR/adminlist.txt"
require_plain "$CONFIG_DIR/permittedlist.txt"
require_plain "$ENV_FILE"

apply_list "$CONFIG_DIR/adminlist.txt" "$ADMIN_HEADER" ADMIN_IDS ADMIN_STATE
apply_list "$CONFIG_DIR/permittedlist.txt" "$PERMITTED_HEADER" PERMITTED_IDS PERMITTED_STATE

ENV_STATE=absent
if [[ -f $ENV_FILE ]]; then
  temporary=$(mktemp --tmpdir="$WORLD_DIR" .valheim.env.XXXXXX)
  TEMPORARIES+=("$temporary")
  awk -v admin="${ADMIN_IDS[*]-}" -v permitted="${PERMITTED_IDS[*]-}" '
    /^ADMINLIST_IDS=/ { print "ADMINLIST_IDS=\"" admin "\""; seen_admin = 1; next }
    /^PERMITTEDLIST_IDS=/ { print "PERMITTEDLIST_IDS=\"" permitted "\""; seen_permitted = 1; next }
    { print }
    END {
      if (!seen_admin) print "ADMINLIST_IDS=\"" admin "\""
      if (!seen_permitted) print "PERMITTEDLIST_IDS=\"" permitted "\""
    }
  ' "$ENV_FILE" > "$temporary"
  if cmp -s -- "$temporary" "$ENV_FILE"; then
    rm -f -- "$temporary"
    ENV_STATE=unchanged
  else
    chmod --reference="$ENV_FILE" "$temporary"
    preserve_ownership "$ENV_FILE" "$temporary"
    mv -- "$temporary" "$ENV_FILE"
    ENV_STATE=updated
  fi
fi

printf 'Applied %s access lists: %s admin, %s permitted\n' "$WORLD" "${#ADMIN_IDS[@]}" "${#PERMITTED_IDS[@]}"
printf 'adminlist.txt %s\n' "$ADMIN_STATE"
printf 'permittedlist.txt %s\n' "$PERMITTED_STATE"
printf 'valheim.env %s\n' "$ENV_STATE"
(( ${#PERMITTED_IDS[@]} > 0 )) || echo 'permittedlist empty: every player with the password may join'
