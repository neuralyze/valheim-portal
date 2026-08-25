#!/usr/bin/env bash
# Arm, disarm or report one world's admin-mode plugin overlay.
#
# Staging only, deliberately: this script never stops, deploys or starts anything. The
# ordering that makes a maintenance window safe - back up, stop, arm, deploy, start, wait
# for ready - is composed by the portal agent from the scripts that already do each step,
# so there is no second copy of it here to drift out of agreement with the first.
set -euo pipefail

WORLD=${1:-}
PROFILE=${2:-}
ACTION=${3:-}
shift 3 || true
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_portal_tools
CONTROLLER="$PORTAL_TOOLS_DIR/valheim_mods.py"

[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }
[[ $PROFILE =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid profile" >&2; exit 2; }
(($# == 0)) || { echo "admin mode action '$ACTION' expects no further arguments, got $#" >&2; exit 2; }

case "$ACTION" in
  on|off|state)
    exec python3 "$CONTROLLER" --world "$WORLD" --profile "$PROFILE" admin-mode "$ACTION"
    ;;
  *)
    # The portal renders this stderr as the admin-UI error text, so it has to name the
    # actions rather than just refusing.
    echo "unsupported admin mode action '$ACTION'; expected on, off or state" >&2
    exit 2
    ;;
esac
