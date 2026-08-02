#!/usr/bin/env bash
# Host-only deletion primitive. The restricted portal agent invokes this only
# after a final world backup succeeds and the selected allowlisted server stops.
set -euo pipefail

world=${1:-}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$script_dir/lib/common.sh"

[[ $# == 1 && $world =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || {
  echo 'invalid world name' >&2
  exit 2
}
require_valheim_root
root=$(realpath -e -- "$VALHEIM_ROOT")
world_entry="$root/$world"
[[ -d $world_entry && ! -L $world_entry ]] || {
  echo 'world directory unavailable or unsafe' >&2
  exit 2
}
world_path=$(realpath -e -- "$world_entry")
[[ $world_path == "$root/$world" ]] || {
  echo 'world directory escapes configured root' >&2
  exit 2
}
[[ -f $world_path/valheim.env ]] || {
  echo 'world server environment is missing' >&2
  exit 2
}

rm -rf --one-file-system -- "$world_path"
[[ ! -e $world_path ]] || {
  echo 'world directory deletion incomplete' >&2
  exit 1
}
printf 'deleted server directory for world=%s; external backups retained\n' "$world"
