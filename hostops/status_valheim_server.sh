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

cd "$VALHEIM_SERVER_DOCKER_DIR"
docker compose --project-name "$VALHEIM_PROJECT_NAME" --env-file "$VALHEIM_ENV_FILE" ps
