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
SAVE=$(realpath -e "$WORLD_ROOT/$WORLD/config_merged/worlds_local/$WORLD.fwl")
[[ $SAVE == "$WORLD_ROOT/$WORLD/config_merged/worlds_local/"* ]] || { echo "save escapes world" >&2; exit 2; }
exec python3 "$PORTAL_TOOLS_DIR/valheim_world.py" inspect "$SAVE"
