#!/usr/bin/env bash
# One world's mod-set operations, delegated to tools/valheim_mods.py.
#
# `deploy` rebuilds <world>/config_merged/bepinex/plugins from four layered sources -
# the profile's manager-cache/server, its manual-mods/, <world>/mods/admin-mode/ and
# <world>/mods/generated/ - and renames the result over the live tree. Anything that is
# only in the live tree is therefore deleted, which is why generated per-world config
# (ServerCharacters' CharacterTemplate.yml) belongs in mods/generated/ and nowhere else;
# see "Generated per-world server config" in docs/script-reference.md. A removal the
# sources do not explain is reported as deploy_dropped=<file>, never silently.
#
# `deploy` also refuses a cached package whose files are not where its own pinned archive
# puts them, because deploying it would move a plugin's DLL without saying so; the refusal
# names the missing file and the `sync` that repairs the cache. `deploy-plan` reports the
# same as cache_stale=<identifier> and writes nothing, so it is safe while a world is live.
#
# A hand-patched mod assembly (tools/modpatches/, detected by its .stock-<version> sidecar)
# is replaced by package bytes like anything else, so `deploy` names that too:
# patch_reverted= when the stock build it was made against comes back, patch_stale= when
# the package moved and the patcher needs revalidating, patch_applied= when a source
# carries the patched bytes. Reported and not refused - see docs/script-reference.md.
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
  check-updates)
    require_argc 0 $#
    exec "${base[@]}" check-updates
    ;;
  notes)
    # Bounded because the changelog of every crossed version is fetched and printed, and an
    # unbounded line count is how one request turns into a several-megabyte reply.
    require_argc 1 $#
    if [[ ! $1 =~ ^[0-9]{1,3}$ ]] || ((10#$1 < 1 || 10#$1 > 200)); then
      reject "mod action 'notes' expects a line count between 1 and 200, got '$1'"
    fi
    exec "${base[@]}" notes --lines "$1"
    ;;
  release-status)
    require_argc 0 $#
    exec "${base[@]}" release-status
    ;;
  deploy-plan)
    # deploy without --apply: the diff an operator has to see before confirming a deploy that
    # stops the world. Read-only by construction, which is why it is a separate action.
    require_argc 0 $#
    exec "${base[@]}" deploy
    ;;
  update)
    require_argc 1 $#
    exec "${base[@]}" update "$1" --apply
    ;;
  release-confirm)
    require_argc 4 $#
    [[ $2 == flat || $2 == vr ]] || reject "mod action 'release-confirm' expects client type 'flat' or 'vr', got '$2'"
    exec "${base[@]}" release-confirm "$1" "$2" "$3" "$4"
    ;;
  *)
    echo "unsupported mod action" >&2
    exit 2
    ;;
esac
