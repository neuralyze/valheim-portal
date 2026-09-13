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
# resolve_world_save, not a hardcoded pair: Ulfsland runs 1.0.12 and stores its
# save as the directory worlds_local/Ulfsland/, while the other four worlds are
# still 0.220.x <World>.db/<World>.fwl pairs. See hostops/lib/common.sh.
if ! resolve_world_save "$WORLD_DIR" "$WORLD_NAME"; then
	echo "no Valheim world save found for $WORLD_NAME in $WORLD_DIR" >&2
	echo "Expected either a 1.0 world directory $WORLD_NAME/ holding a *.fwl2 file," >&2
	echo "or a 0.220 pair $WORLD_NAME.db plus $WORLD_NAME.fwl (either casing)." >&2
	exit 2
fi
# Proven readable BEFORE an archive file exists. tar streams as it goes, so a save
# this user cannot open leaves a stub in the inventory -- measured as the real
# service user on 2026-09-12, 122 bytes holding only the directory entry -- and
# that stub is then the NEWEST archive for the world.
require_readable_world_save "$WORLD_DIR" || exit 2

echo "Backing up Valheim world $WORLD_NAME ($WORLD_SAVE_FORMAT format, save name $WORLD_SAVE_STEM)"

mkdir -p "$VALHEIM_BACKUP_ROOT"
ARCHIVE="$VALHEIM_BACKUP_ROOT/world-$WORLD_NAME-$BACKUP_NAME-$(date +%Y-%m-%d_%H-%M-%S).tgz"
# -C instead of the cd this script used to do, so the members stay relative to
# worlds_local either way. The pair archive is therefore byte-shape identical to
# every archive already in the inventory -- exactly two members, "<stem>.db"
# then "<stem>.fwl" -- which restore_valheim_world.sh and the world-analysis
# reader both still depend on. A 1.0 archive is the single directory member
# "<stem>/" plus its contents at their original basenames: the generation number
# in _main.<N>.db2 is what picks the newest save set, so nothing is renamed or
# flattened. Naming only the world directory also leaves the game's own
# <World>_backup_auto-* copies out of the archive, matching the pair behaviour.
if ! tar czf "$ARCHIVE" -C "$WORLD_DIR" "${WORLD_SAVE_MEMBERS[@]}"; then
	# Belt and braces behind the readability gate: anything else tar trips over
	# mid-stream (a save being rewritten under us, a full disk) must not leave a
	# truncated archive that list, analysis and restore would treat as a backup.
	rm -f -- "$ARCHIVE"
	echo "archiving $WORLD_NAME failed; the partial archive was removed" >&2
	exit 2
fi
echo "$ARCHIVE"
