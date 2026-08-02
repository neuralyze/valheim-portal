#!/usr/bin/env bash
# Prunes world backup archives older than DAYS_OLD days.
#
# This deletes the archives restore_valheim_world.sh restores from, so it
# refuses anything it cannot understand and, on a terminal, shows what it would
# delete rather than deleting it. Pass --delete to actually remove the files.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

usage() {
	cat >&2 <<'USAGE'
usage: clean_backups.sh [--dry-run|--delete] [DAYS_OLD]

DAYS_OLD is a positive integer number of days; archives whose mtime is older
than that are pruned. It defaults to 30. There is no zero: "older than 0 days"
means every archive taken more than 24 hours ago, which is never what an
operator prunes deliberately.

On a terminal --dry-run is the default and only lists what would be deleted.
Without a terminal (cron, the portal agent) --delete is the default, because a
scheduled prune that silently does nothing is worse than no prune at all.
USAGE
	exit 2
}

# Default depends on where the output goes: interactive runs must not destroy
# archives on a bare invocation, scheduled runs must not turn into no-ops.
if [[ -t 1 ]]; then
	dry_run=true
else
	dry_run=false
fi
# Tracked separately from its value: an explicitly empty argument is a caller
# bug (an unset shell variable expanded into argv), not a request for the
# default, and silently pruning at 30 days would hide it.
days_old=30
days_old_given=false

while (($# > 0)); do
	case "$1" in
		--dry-run) dry_run=true ;;
		--delete) dry_run=false ;;
		-h|--help) usage ;;
		-*) echo "unknown option: $1" >&2; usage ;;
		*)
			! $days_old_given || { echo "unexpected argument: $1" >&2; usage; }
			days_old=$1
			days_old_given=true
			;;
	esac
	shift
done

[[ $days_old =~ ^[1-9][0-9]*$ ]] || {
	echo "DAYS_OLD must be a positive integer, got: $days_old" >&2
	exit 2
}

require_valheim_root
[[ -d $VALHEIM_BACKUP_ROOT ]] || {
	echo "no backup directory at $VALHEIM_BACKUP_ROOT; nothing to prune"
	exit 0
}

mapfile -t -d '' stale < <(find "$VALHEIM_BACKUP_ROOT" -type f -mtime +"$days_old" -print0)

if ((${#stale[@]} == 0)); then
	echo "no backups older than $days_old days in $VALHEIM_BACKUP_ROOT"
	exit 0
fi

if $dry_run; then
	printf '%s\n' "${stale[@]}"
	echo "would delete ${#stale[@]} backup(s) older than $days_old days from $VALHEIM_BACKUP_ROOT"
	echo "re-run with --delete to remove them"
	exit 0
fi

rm -f -- "${stale[@]}"
echo "deleted ${#stale[@]} backup(s) older than $days_old days from $VALHEIM_BACKUP_ROOT"
