#!/usr/bin/env bash
set -euo pipefail

WORLD=${1:-}
[[ $# == 1 && $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_portal_tools
exec python3 "$PORTAL_TOOLS_DIR/valheim_profile_catalog.py" "$WORLD"
