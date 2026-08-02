#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# The portal agent builds this argv positionally (see the "provision" case in
# portal/internal/agent/agent.go), so a mismatch is a programming error the
# operator has to be able to diagnose. Print the order rather than a bare count.
POSITIONALS=(
	WORLD SERVER_NAME PORT PUBLIC CROSSPLAY PLAYER_LIMIT PRESET
	BACKUP_INTERVAL BACKUP_AGE BACKUP_COUNT PROFILE SEED
	SOURCE_WORLD TEMPLATE_WORLD TEMPLATE_PROFILE
)
(($# == ${#POSITIONALS[@]})) || {
	echo "invalid provisioning request: expected ${#POSITIONALS[@]} arguments, got $#" >&2
	echo "usage: provision_valheim_server.sh ${POSITIONALS[*]}" >&2
	echo "PUBLIC and CROSSPLAY are 'true' or 'false'; pass an empty string for any optional value." >&2
	echo "Run 'python3 tools/valheim_provision.py --help' from the repository root for what each one means." >&2
	exit 2
}
[[ -n ${PORTAL_SERVER_PASSWORD:-} ]] || { echo "server password was not supplied" >&2; exit 2; }

require_portal_tools
exec python3 "$PORTAL_TOOLS_DIR/valheim_provision.py" "$@"
