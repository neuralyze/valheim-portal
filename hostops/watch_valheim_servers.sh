#!/usr/bin/env bash
# Detects a world server that is running but wedged, and restarts it.
#
# On 2026-08-06 Hrafnheim stopped accepting connections while its process stayed
# alive and its container reported healthy. Every surface an operator would
# check said the world was up: docker showed it running, the status endpoint
# answered on the query port, and the portal's release gate reported CLEAR. Only
# two things were actually wrong, and neither was visible without looking for
# them - the world had stopped autosaving, and the container had stopped
# producing output. Players discovered it at game time, by failing to connect.
#
# So the check is those two clocks, and it deliberately requires BOTH to be
# stale. A world with nobody on it still saves on its timer, and a quiet world
# still logs; either alone is normal, together they are not.
#
# Restarting is safe here because the world saves on shutdown and the server
# starts back in about four minutes. Losing four minutes beats losing an
# evening, which is what the undetected version cost.
set -euo pipefail

worlds_root=${VALHEIM_ROOT:-/media/big4/projects/game/valheim}
server_docker_dir=${VALHEIM_SERVER_DOCKER_DIR:-/media/big3/Projects/Game/valheim/server/ValheimConfig/valheim-server-docker}
hostops=${VALHEIM_HOSTOPS_DIR:-/srv/valheim-portal/hostops}
log_root=${VALHEIM_LOG_ROOT:-/var/log/valheim-worlds}
state_dir=${VALHEIM_WATCHDOG_STATE:-/var/lib/valheim-watchdog}
agent_user=${VALHEIM_AGENT_USER:-valheim-agent}

# A save older than this is suspicious: Valheim autosaves every 20 minutes.
save_stale=${VALHEIM_SAVE_STALE_SECONDS:-1500}
# Output older than this is suspicious: a healthy server prints routine lines
# every couple of minutes even with nobody connected.
log_stale=${VALHEIM_LOG_STALE_SECONDS:-420}
# Never restart the same world more often than this, so a world that is wedged
# for a structural reason is not restart-looped.
cooldown=${VALHEIM_RESTART_COOLDOWN_SECONDS:-1800}
dry_run=${VALHEIM_WATCHDOG_DRY_RUN:-0}

mkdir -p -- "$state_dir"

note() { printf '%s watchdog: %s\n' "$(date -Is)" "$*"; }

age_of() {  # seconds since mtime, or empty when absent
    local path=$1 mtime
    mtime=$(stat -c %Y -- "$path" 2>/dev/null) || return 0
    printf '%s' "$(( $(date +%s) - mtime ))"
}

restart_world() {
    local world=$1
    local marker="$state_dir/$world.last-restart" last now
    now=$(date +%s)
    last=$(cat -- "$marker" 2>/dev/null || echo 0)
    if (( now - last < cooldown )); then
        note "$world is wedged but was restarted $(( now - last ))s ago; leaving it alone"
        return
    fi
    if [[ $dry_run == 1 ]]; then
        note "$world WOULD BE RESTARTED (dry run)"
        return
    fi
    printf '%s' "$now" >"$marker"
    note "$world restarting"
    sudo -n -u "$agent_user" env VALHEIM_ROOT="$worlds_root" VALHEIM_SERVER_DOCKER_DIR="$server_docker_dir" \
        "$hostops/stop_valheim_server.sh" "$world" >/dev/null 2>&1 || note "$world stop reported failure"
    sleep 5
    sudo -n -u "$agent_user" env VALHEIM_ROOT="$worlds_root" VALHEIM_SERVER_DOCKER_DIR="$server_docker_dir" \
        "$hostops/start_valheim_server.sh" "$world" >/dev/null 2>&1 || note "$world start reported failure"
    note "$world restarted"
}

while read -r container; do
    [[ -n $container ]] || continue
    world=${container#valheim-server-}
    save="$worlds_root/$world/config_merged/worlds_local/$world.db"
    output="$log_root/$world.log"

    save_age=$(age_of "$save")
    log_age=$(age_of "$output")

    # An absent save or log is not evidence of a wedge - a freshly created world
    # has neither, and the collector may not have attached yet.
    [[ -n $save_age && -n $log_age ]] || { note "$world skipped (save or log not present yet)"; continue; }

    if (( save_age > save_stale && log_age > log_stale )); then
        note "$world WEDGED: no save for ${save_age}s, no output for ${log_age}s"
        restart_world "$world"
    fi
done < <(docker ps --filter 'name=valheim-server-' --format '{{.Names}}')
