#!/usr/bin/env bash
set -euo pipefail

WORLD_NAME=${1:-}
[[ $WORLD_NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo "invalid world" >&2; exit 2; }
CONTAINER="valheim-server-$WORLD_NAME"
DEADLINE=$((SECONDS + 600))
STARTED_AT=$(date --iso-8601=seconds)

while ((SECONDS < DEADLINE)); do
  running=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)
  [[ $running == true ]] || {
    echo "container exited before readiness" >&2
    docker logs --tail 80 "$CONTAINER" 2>&1 || true
    exit 1
  }
  if docker logs --since "$STARTED_AT" "$CONTAINER" 2>&1 | grep -q 'Game server connected'; then
    echo "ready=$WORLD_NAME"
    exit 0
  fi
  sleep 5
done

echo "server did not become ready within 600 seconds" >&2
docker logs --tail 80 "$CONTAINER" 2>&1 || true
exit 1
