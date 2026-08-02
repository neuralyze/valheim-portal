#!/usr/bin/env bash
set -euo pipefail

WORLD_NAME=${1:-}
[[ $WORLD_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world name" >&2; exit 2; }
shift
WORLD_NOTE="$*"
[[ -n $WORLD_NOTE ]] || { echo "usage: add_note_valheim_world.sh WORLD NOTE..." >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Notes are operator data, so they live beside the worlds they describe rather
# than inside this repository. The old unchecked `cd` into the directory
# appended the note to the caller's working directory instead of failing, so
# create it rather than guess where the note went.
require_valheim_root
NOTES_DIR="$VALHEIM_ROOT/world_notes"
mkdir -p "$NOTES_DIR"

echo "Adding note to Valheim world $WORLD_NAME"

printf '%s - %s\n' "$(date +"%Y-%m-%d_%H-%M-%S")" "$WORLD_NOTE" >> "$NOTES_DIR/notes_$WORLD_NAME.txt"
