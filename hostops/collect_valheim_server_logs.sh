#!/usr/bin/env bash
# Keeps every Valheim world server's output on the host, outside the container.
#
# On 2026-08-06 Hrafnheim stopped accepting connections while its process stayed
# alive. Diagnosing it afterwards was impossible: the container's stdout is the
# only copy of the server log, `docker compose` recreates the container on
# restart, and recreation deletes that log with it. BepInEx truncates its own
# LogOutput.log on every start, so the server-side mod log is gone too. The
# incident left a save-file timestamp and nothing else.
#
# This follows each world container's output and appends it to a host file that
# survives container recreation, so the next occurrence leaves evidence. It
# reconnects when a container restarts and picks up new worlds automatically.
#
# Deliberately append-only and rotation-aware rather than clever: the value is
# that the bytes still exist tomorrow.
set -euo pipefail

log_root=${VALHEIM_LOG_ROOT:-/var/log/valheim-worlds}
poll_seconds=${VALHEIM_LOG_POLL_SECONDS:-10}
name_filter=${VALHEIM_LOG_NAME_FILTER:-valheim-server-}

mkdir -p -- "$log_root"

declare -A following=()

# Follow one container until it exits. --since 1s avoids replaying the whole
# backlog on every reconnect; timestamps are kept because the server's own lines
# carry game time, not wall-clock date.
follow_container() {
    local name=$1 destination="$log_root/${1#"$name_filter"}.log"
    {
        printf '=== %s: attached to %s ===\n' "$(date -Is)" "$name"
        docker logs --timestamps --follow --since 1s "$name" 2>&1 || true
        printf '=== %s: detached from %s ===\n' "$(date -Is)" "$name"
    } >>"$destination" &
    following["$name"]=$!
}

reap_finished() {
    local name pid
    for name in "${!following[@]}"; do
        pid=${following[$name]}
        kill -0 "$pid" 2>/dev/null && continue
        wait "$pid" 2>/dev/null || true
        unset 'following[$name]'
    done
}

terminate() {
    local pid
    for pid in "${following[@]}"; do kill "$pid" 2>/dev/null || true; done
    exit 0
}
trap terminate INT TERM

while :; do
    reap_finished
    while read -r name; do
        [[ -n $name ]] || continue
        [[ -n ${following[$name]:-} ]] && continue
        follow_container "$name"
    done < <(docker ps --filter "name=$name_filter" --format '{{.Names}}')
    sleep "$poll_seconds"
done
