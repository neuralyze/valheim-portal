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
	SOURCE_WORLD COPY_FROM WORLD_UPLOAD
)
(($# == ${#POSITIONALS[@]})) || {
	echo "invalid provisioning request: expected ${#POSITIONALS[@]} arguments, got $#" >&2
	echo "usage: provision_valheim_server.sh ${POSITIONALS[*]}" >&2
	echo "PUBLIC and CROSSPLAY are 'true' or 'false'; pass an empty string for any optional value." >&2
	echo "WORLD_UPLOAD is a 32-character hex staging id under the world upload root, resolved to a directory here." >&2
	echo "Run 'python3 tools/valheim_provision.py --help' from the repository root for what each one means." >&2
	exit 2
}
[[ -n ${PORTAL_SERVER_PASSWORD:-} ]] || { echo "server password was not supplied" >&2; exit 2; }

# The agent sends a staging id, never a path, so this is where the id becomes one.
# valheim_provision.py receives the resolved directory, exactly as it receives every
# other already-resolved path, and never learns the id or the root.
upload_dir=""
if [[ -n ${15} ]]; then
	require_world_upload_root "${15}"
	upload_dir=$VALHEIM_WORLD_UPLOAD_DIR
fi

require_portal_tools
exec python3 "$PORTAL_TOOLS_DIR/valheim_provision.py" "${@:1:14}" "$upload_dir"
