#!/usr/bin/env bash
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

# The portal renders this script's stderr as the admin-UI error text, so every
# rejection has to say which action was refused and what it wanted instead.
reject() { echo "$*" >&2; exit 2; }
require_argc() {
  local expected=$1 actual=$2
  ((actual == expected)) || reject "mod action '$ACTION' expects $expected argument(s), got $actual"
}
require_scope() {
  [[ $1 == shared || $1 == client-only ]] || reject "mod action '$ACTION' expects scope 'shared' or 'client-only', got '$1'"
}

base=(python3 "$CONTROLLER" --world "$WORLD" --profile "$PROFILE")
case "$ACTION" in
  inventory)
    require_argc 0 $#
    exec "${base[@]}" list --json
    ;;
  search)
    require_argc 1 $#
    exec "${base[@]}" search "$1" --json
    ;;
  custom-list)
    require_argc 0 $#
    exec "${base[@]}" custom-list
    ;;
  add)
    require_argc 3 $#
    require_scope "$3"
    if [[ $3 == client-only ]]; then
      exec "${base[@]}" add "$1" "$2" --client-only
    fi
    exec "${base[@]}" add "$1" "$2"
    ;;
  remove)
    require_argc 2 $#
    exec "${base[@]}" remove "$1" --reason "$2"
    ;;
  enable|disable)
    require_argc 1 $#
    exec "${base[@]}" "$ACTION" "$1"
    ;;
  custom-add)
    require_argc 2 $#
    require_scope "$2"
    exec "${base[@]}" custom-add "$1" --scope "$2"
    ;;
  custom-remove|custom-enable|custom-disable)
    require_argc 1 $#
    exec "${base[@]}" "$ACTION" "$1"
    ;;
  deploy)
    require_argc 0 $#
    exec "${base[@]}" deploy --apply
    ;;
  *)
    echo "unsupported mod action" >&2
    exit 2
    ;;
esac
