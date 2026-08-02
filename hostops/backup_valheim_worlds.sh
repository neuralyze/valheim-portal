#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_worlds_file "$SCRIPT_DIR"

echo "Backing up all Valheim worlds"

mapfile -t WORLD_NAMES < "$VALHEIM_WORLDS_FILE"

pids=()
worlds=()
for world in "${WORLD_NAMES[@]}"; do
	[[ -n $world ]] || continue
	"$SCRIPT_DIR/backup_valheim_world.sh" "$world" &
	pids+=($!)
	worlds+=("$world")
done

((${#pids[@]} > 0)) || { echo "$VALHEIM_WORLDS_FILE lists no worlds" >&2; exit 78; }

echo "${#pids[@]} world(s) backing up. Waiting for them to finish..."

# `wait` reports the job's exit status, so under set -e an unguarded call would
# abort this loop on the first failure and leave the remaining jobs unreaped.
# The old `if [ $? -ne 0 ]` check could never fire for the same reason.
failures=0
for index in "${!pids[@]}"; do
	status=0
	wait "${pids[index]}" || status=$?
	if ((status != 0)); then
		echo "backup of ${worlds[index]} failed with status $status" >&2
		failures=$((failures + 1))
	fi
done

((failures == 0)) || { echo "$failures of ${#pids[@]} world(s) failed" >&2; exit 1; }
echo "Backed up ${#pids[@]} world(s)."
