#!/usr/bin/env bash
set -euo pipefail

WORLD_NAME=${1:-}
BASE_PORT=${2:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root
WORLD_ROOT=$(realpath -- "$VALHEIM_ROOT")

[[ $WORLD_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "Invalid world name" >&2; exit 2; }
[[ $BASE_PORT =~ ^[0-9]+$ ]] || { echo "Invalid game port" >&2; exit 2; }
(( BASE_PORT >= 1024 && BASE_PORT <= 65533 )) || { echo "Game port must be between 1024 and 65533" >&2; exit 2; }

WORLD_DIR=$(realpath -- "$WORLD_ROOT/$WORLD_NAME")
[[ $WORLD_DIR == "$WORLD_ROOT/"* && -d $WORLD_DIR ]] || { echo "World is unavailable" >&2; exit 2; }
ENV_FILE="$WORLD_DIR/valheim.env"
[[ -f $ENV_FILE && ! -L $ENV_FILE ]] || { echo "World environment file is unavailable" >&2; exit 2; }

# The port range is shared across worlds, so this lock must be shared too. The
# hardened agent unit mounts /run/lock read-only and the world root is not
# agent-writable, so prefer the agent's own runtime directory and fall back to
# TMPDIR for hand runs outside the unit.
LOCK_DIR=/run/valheim-portal-agent
[[ -d $LOCK_DIR && -w $LOCK_DIR ]] || LOCK_DIR=${TMPDIR:-/tmp}
exec 9>"$LOCK_DIR/valheim-ports.lock"
flock -x 9
# The compose service maps the host range onto the container's 2456-2457/udp
# pair (game plus Steam query), so the host range must also be two ports.
# A three-port range makes docker compose reject the service outright.
REQUESTED_END=$((BASE_PORT + 1))
shopt -s nullglob
for candidate in "$WORLD_ROOT"/*/valheim.env; do
  [[ $(realpath -- "$candidate") == "$ENV_FILE" ]] && continue
  value=$(sed -nE 's/^CONTAINER_VALHEIM_PORT="?([0-9]+)(-[0-9]+)?"?$/\1\2/p' "$candidate" | tail -n 1)
  [[ -n $value ]] || continue
  other_start=${value%%-*}
  if [[ $value == *-* ]]; then other_end=${value##*-}; else other_end=$((other_start + 1)); fi
  if (( BASE_PORT <= other_end && REQUESTED_END >= other_start )); then
    echo "Port range $BASE_PORT-$REQUESTED_END conflicts with $(basename -- "$(dirname -- "$candidate")") range $other_start-$other_end" >&2
    exit 3
  fi
done

temporary=$(mktemp --tmpdir="$WORLD_DIR" .valheim.env.XXXXXX)
trap 'rm -f -- "$temporary"' EXIT
if grep -q '^CONTAINER_VALHEIM_PORT=' "$ENV_FILE"; then
  sed -E "s/^CONTAINER_VALHEIM_PORT=.*/CONTAINER_VALHEIM_PORT=\"$BASE_PORT-$REQUESTED_END\"/" "$ENV_FILE" > "$temporary"
else
  cat -- "$ENV_FILE" > "$temporary"
  printf '\nCONTAINER_VALHEIM_PORT="%s-%s"\n' "$BASE_PORT" "$REQUESTED_END" >> "$temporary"
fi
chmod --reference="$ENV_FILE" "$temporary"
# Restoring the exact owner needs CAP_CHOWN, which the unprivileged agent does
# not have, so a hard chown here failed the whole port change after the server
# had already been stopped. Preserve as much as the caller is allowed to: root
# keeps owner and group, the agent keeps the group it shares with the operator
# (so the reference mode's group write still applies), and neither aborts.
chown --reference="$ENV_FILE" "$temporary" 2>/dev/null \
  || chgrp --reference="$ENV_FILE" "$temporary" 2>/dev/null \
  || true
mv -- "$temporary" "$ENV_FILE"
trap - EXIT
printf 'Configured %s game ports %s-%s\n' "$WORLD_NAME" "$BASE_PORT" "$REQUESTED_END"
