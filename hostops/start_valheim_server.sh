#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VALHEIM_WORLD=${1:-}
[[ $VALHEIM_WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world name" >&2; exit 2; }
require_valheim_root
require_server_docker_dir
VALHEIM_ENV_FILE="$VALHEIM_ROOT/$VALHEIM_WORLD/valheim.env"
typeset -l VALHEIM_PROJECT_NAME
VALHEIM_PROJECT_NAME=$VALHEIM_WORLD
# The portal agent starts the whole stack and passes no second argument; an
# operator may name a single compose service to bring up instead.
VALHEIM_SERVICE=()
if [[ -n ${2:-} ]]; then
	VALHEIM_SERVICE=("$2")
fi

echo "Starting Valheim Server: $VALHEIM_WORLD"
if ! "$SCRIPT_DIR/manage_mods.sh" "$VALHEIM_WORLD" release-status --require-complete; then
	echo "Refusing to start $VALHEIM_WORLD: a client-release cutover is still pending." >&2
	echo "Republish every pending target and record it with './hostops/manage_mods.sh $VALHEIM_WORLD release-confirm <published-profile> <client-type> <release-id> <profile-zip>', then start again." >&2
	exit 1
fi

cd "$VALHEIM_SERVER_DOCKER_DIR"

docker compose \
	--project-name "$VALHEIM_PROJECT_NAME" \
	--env-file "$VALHEIM_ENV_FILE" \
	up -d "${VALHEIM_SERVICE[@]}"
