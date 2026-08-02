#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WORLD_NAME=${1:-}
BACKUP_NAME=${2:-backup}
[[ $WORLD_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world name" >&2; exit 2; }
# The name lands in the archive filename, which restore_valheim_world.sh parses
# back out as ^world-<WORLD>-[A-Za-z0-9._-]+\.tgz$. Anything outside that shape
# would produce a backup nothing can restore.
[[ $BACKUP_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid backup name" >&2; exit 2; }

require_valheim_root
WORLD_DIR="$VALHEIM_ROOT/$WORLD_NAME/config_merged/worlds_local"
SAVE_STEM="${WORLD_NAME,,}"
if [[ -f "$WORLD_DIR/$WORLD_NAME.db" && -f "$WORLD_DIR/$WORLD_NAME.fwl" ]]; then
	SAVE_STEM="$WORLD_NAME"
fi

echo "Backing up Valheim world $WORLD_NAME"

mkdir -p "$VALHEIM_BACKUP_ROOT"
cd "$WORLD_DIR"
tar czf \
	"$VALHEIM_BACKUP_ROOT/world-$WORLD_NAME-$BACKUP_NAME-$(date +%Y-%m-%d_%H-%M-%S).tgz" \
	"$SAVE_STEM.db" "$SAVE_STEM.fwl"
