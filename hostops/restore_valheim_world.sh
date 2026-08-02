#!/usr/bin/env bash
# Host-only restore primitive. The restricted portal agent invokes this only after
# it has created a fresh backup and stopped the selected allowlisted world.
set -euo pipefail

world=${1:?world name is required}
backup_name=${2:?backup filename is required}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$script_dir/lib/common.sh"

[[ $world =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo 'invalid world name' >&2; exit 2; }
[[ $backup_name =~ ^world-"$world"-[A-Za-z0-9._-]+\.tgz$ ]] || { echo 'invalid backup filename' >&2; exit 2; }

require_valheim_root
backup_root=$VALHEIM_BACKUP_ROOT
world_dir="$VALHEIM_ROOT/$world/config_merged/worlds_local"
backup=$(realpath -e -- "$backup_root/$backup_name")
[[ $backup == "$backup_root/"* && -f $backup ]] || { echo 'backup is outside inventory' >&2; exit 2; }
[[ -d $world_dir ]] || { echo 'world save directory unavailable' >&2; exit 2; }

# backup_valheim_world.sh archives whichever casing the live save pair uses, so
# the same detection has to pick the names this archive is expected to carry.
world_file=${world,,}
if [[ -f "$world_dir/$world.db" && -f "$world_dir/$world.fwl" ]]; then
  world_file=$world
fi

mapfile -t entries < <(tar -tzf "$backup")
[[ ${#entries[@]} -eq 2 && ${entries[0]} == "$world_file.db" && ${entries[1]} == "$world_file.fwl" ]] || {
  echo 'backup does not contain the selected world save pair' >&2
  exit 2
}
stage=$(mktemp -d "$world_dir/.portal-restore.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "$backup" --no-same-owner --no-same-permissions -C "$stage"
[[ -f $stage/$world_file.db && -f $stage/$world_file.fwl ]] || { echo 'backup extraction failed validation' >&2; exit 2; }
install -m 0640 -- "$stage/$world_file.db" "$world_dir/$world_file.db"
install -m 0640 -- "$stage/$world_file.fwl" "$world_dir/$world_file.fwl"
echo "restored world=$world backup=$backup_name"
