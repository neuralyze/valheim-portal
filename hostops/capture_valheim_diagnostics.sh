#!/usr/bin/env bash
set -euo pipefail

WORLD_NAME=${1:?"usage: capture_valheim_diagnostics.sh WORLD_NAME [OUTPUT_DIR]"}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root
WORLD_DIR="$VALHEIM_ROOT/$WORLD_NAME"
CONTAINER_NAME="valheim-server-$WORLD_NAME"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUTPUT_DIR=${2:-"$WORLD_DIR/diagnostics/$TIMESTAMP"}
CONFIG_DIR="$WORLD_DIR/config_merged/bepinex"
DATA_DIR="$WORLD_DIR/data/bepinex/BepInEx"

if [[ ! -d "$WORLD_DIR" ]]; then
    echo "world directory not found: $WORLD_DIR" >&2
    exit 1
fi

if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "container not found: $CONTAINER_NAME" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

{
    echo "captured_at=$(date --iso-8601=seconds)"
    echo "world=$WORLD_NAME"
    echo "container=$CONTAINER_NAME"
    docker inspect --format 'id={{.Id}}' "$CONTAINER_NAME"
    docker inspect --format 'image={{.Config.Image}}' "$CONTAINER_NAME"
    docker inspect --format 'created={{.Created}}' "$CONTAINER_NAME"
    docker inspect --format 'status={{.State.Status}}' "$CONTAINER_NAME"
    docker inspect --format 'running={{.State.Running}}' "$CONTAINER_NAME"
    docker inspect --format 'started_at={{.State.StartedAt}}' "$CONTAINER_NAME"
    docker inspect --format 'finished_at={{.State.FinishedAt}}' "$CONTAINER_NAME"
    docker inspect --format 'restart_count={{.RestartCount}}' "$CONTAINER_NAME"
    docker image inspect --format 'image_id={{.Id}}' "$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
    docker image inspect --format 'image_created={{.Created}}' "$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
    docker port "$CONTAINER_NAME" || true
} > "$OUTPUT_DIR/runtime.txt"

docker logs --timestamps "$CONTAINER_NAME" 2>&1 \
    | sed -E 's#https://discord[^[:space:]"]*#[REDACTED_DISCORD_WEBHOOK]#g' \
    > "$OUTPUT_DIR/docker.log"

if [[ -f "$DATA_DIR/LogOutput.log" ]]; then
    cp -- "$DATA_DIR/LogOutput.log" "$OUTPUT_DIR/BepInEx-LogOutput.log"
fi

if [[ -d "$CONFIG_DIR/LoadTimeProfiler" ]]; then
    cp -a -- "$CONFIG_DIR/LoadTimeProfiler" "$OUTPUT_DIR/LoadTimeProfiler"
fi

if [[ -d "$CONFIG_DIR/plugins" ]]; then
    while IFS= read -r -d '' file; do
        sha256sum "$file"
    done < <(find "$CONFIG_DIR/plugins" -type f \( -name '*.dll' -o -name 'manifest.json' \) -print0 | sort -z) \
        > "$OUTPUT_DIR/plugin-inventory.sha256"
fi

if [[ -d "$CONFIG_DIR" ]]; then
    while IFS= read -r -d '' file; do
        sha256sum "$file"
    done < <(find "$CONFIG_DIR" -maxdepth 1 -type f -print0 | sort -z) \
        > "$OUTPUT_DIR/config-inventory.sha256"
fi

if [[ -d "$WORLD_DIR/config_merged/worlds_local" ]]; then
    find "$WORLD_DIR/config_merged/worlds_local" -maxdepth 1 -type f \( -name '*.db' -o -name '*.fwl' \) \
        -printf '%f\t%s bytes\t%TY-%Tm-%TdT%TH:%TM:%TS%Tz\n' \
        | sort > "$OUTPUT_DIR/world-metadata.txt"
fi

echo "Captured diagnostic bundle: $OUTPUT_DIR"
