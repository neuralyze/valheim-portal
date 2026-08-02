#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WORLD_NAME=${1:-}
[[ $# == 1 && $WORLD_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }
require_valheim_root
shopt -s nullglob
backups=("$VALHEIM_BACKUP_ROOT"/world-"$WORLD_NAME"-*.tgz)
for backup in "${backups[@]}"; do
	printf '%s\n' "${backup##*/}"
done | sort -r
