#!/usr/bin/env bash
set -euo pipefail

# The mods a player of one world has, as JSON, for the world page.
#
# Two modes, because the portal caches the list and must still never serve a stale one. Mods are
# changed through tools/valheim_mods.py directly on this host as well as through the portal, and the
# portal sees nothing when that happens - so it asks for the cheap fingerprint on every page view
# and rebuilds only when that moved. `state` is two manifest reads and a hash; the full build
# fetches the Thunderstore index, which is several megabytes and is what the cache exists to avoid.
#
# usage: portal_world_mod_catalog.sh <World>
#        portal_world_mod_catalog.sh <World> state
#
# The world is required: the full build reads that world's installed plugin manifests as the
# fallback description source.

WORLD=${1:-}
MODE=${2:-full}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_portal_tools

reject() { echo "$*" >&2; exit 2; }

[[ $# -ge 1 && $# -le 2 ]] || reject "usage: portal_world_mod_catalog.sh <World> [state]"
[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || reject "invalid world"

base=(python3 "$PORTAL_TOOLS_DIR/valheim_mods.py" --world "$WORLD" player-catalog)
case "$MODE" in
  full) exec "${base[@]}" ;;
  state) exec "${base[@]}" --state ;;
  *) reject "unsupported mode '$MODE'; expected 'state' or nothing" ;;
esac
