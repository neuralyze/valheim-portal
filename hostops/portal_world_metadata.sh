#!/usr/bin/env bash
set -euo pipefail

WORLD=${1:-}
[[ $# == 1 && $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root
require_portal_tools
WORLD_ROOT=$(realpath "$VALHEIM_ROOT")
WORLDS_LOCAL="$WORLD_ROOT/$WORLD/config_merged/worlds_local"
# Valheim 1.0.12 moved the metadata: a 0.220.x world is worlds_local/<World>.fwl, a 1.0
# world is worlds_local/<World>/_main.<N>.fwl2. Measured on Ulfsland 2026-09-12, the two
# are the same container format -- valheim_world.py inspect reads an fwl2 unchanged and
# reports world version 41 -- so only the path has to be found. Without this, the world
# metadata verb and the portal's seed display fail outright on a 1.0 world: realpath -e
# on a .fwl that does not exist exits non-zero with nothing to read.
if resolve_world_save "$WORLDS_LOCAL" "$WORLD" && [[ $WORLD_SAVE_FORMAT == directory ]]; then
	# Highest generation, version-sorted so _main.10 beats _main.9. Which generation is
	# read barely matters for these fields: Ulfsland's _main.0.fwl2 and _main.1.fwl2
	# carry the same name, seed, seed value, uid and generator version, and differ only
	# in the trailer. The newest is still the right one to report.
	SAVE=$(find "$WORLDS_LOCAL/$WORLD_SAVE_STEM" -maxdepth 1 -type f -name '*.fwl2' |
		sort -V | tail -n 1)
	[[ -n $SAVE ]] || { echo "world metadata unavailable" >&2; exit 2; }
	SAVE=$(realpath -e "$SAVE")
else
	SAVE=$(realpath -e "$WORLDS_LOCAL/$WORLD.fwl")
fi
[[ $SAVE == "$WORLDS_LOCAL/"* ]] || { echo "save escapes world" >&2; exit 2; }
exec python3 "$PORTAL_TOOLS_DIR/valheim_world.py" inspect "$SAVE"
