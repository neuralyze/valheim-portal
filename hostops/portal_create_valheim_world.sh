#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
[[ $# == 2 ]] || { echo "usage: portal_create_valheim_world.sh <world> <seed>" >&2; exit 2; }

require_portal_tools
exec python3 "$PORTAL_TOOLS_DIR/valheim_worldgen.py" "$@"
