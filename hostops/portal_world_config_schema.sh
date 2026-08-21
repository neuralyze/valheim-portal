#!/usr/bin/env bash
set -euo pipefail

# The typed schema of one world's BepInEx settings, as JSON, for the settings manager page.
#
# Two modes, for the same reason portal_world_config_schema's sibling
# portal_world_mod_catalog.sh has two: the portal caches this payload and must still never
# serve a stale one. Configs are edited on this host by scripts and by hand as well as
# through the portal, and the portal sees nothing when that happens - so it asks for the
# cheap fingerprint on every page view and rebuilds only when that moved.
#
# The asymmetry is worth the split here. `state` stats 113 files and hashes the result;
# `full` parses 19,866 setting blocks out of 4.5 MB of config text and emits a 4.5 MB
# payload, which is what the cache exists to avoid doing per page view.
#
# usage: portal_world_config_schema.sh <World>
#        portal_world_config_schema.sh <World> state

WORLD=${1:-}
MODE=${2:-full}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_portal_tools

reject() { echo "$*" >&2; exit 2; }

[[ $# -ge 1 && $# -le 2 ]] || reject "usage: portal_world_config_schema.sh <World> [state]"
[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || reject "invalid world"

base=(python3 "$PORTAL_TOOLS_DIR/valheim_config_schema.py" extract --world "$WORLD")
case "$MODE" in
  full) exec "${base[@]}" ;;
  state) exec "${base[@]}" --state ;;
  *) reject "unsupported mode '$MODE'; expected 'state' or nothing" ;;
esac
