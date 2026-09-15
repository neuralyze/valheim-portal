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

# The shared gate lives with the scripts this one drives, so both paths refuse on the same
# evidence. VALHEIM_ROOT is what lib/common.sh calls the world root internally.
# shellcheck source=hostops/lib/common.sh
source "$hostops/lib/common.sh"
export VALHEIM_ROOT="$worlds_root"

note() { printf '%s watchdog: %s\n' "$(date -Is)" "$*"; }

age_of() {  # seconds since mtime, or empty when absent
    local path=$1 mtime
    mtime=$(stat -c %Y -- "$path" 2>/dev/null) || return 0
    printf '%s' "$(( $(date +%s) - mtime ))"
}

# newest_save_age answers the same question age_of does, for a Valheim 1.0 world.
#
# 1.0.12 stores the save as the directory worlds_local/<World>/ and has no <World>.db at
# all, so the pair lookup below returned nothing and this loop SKIPPED the world as "save
# not present yet" - the wedge check that exists because Hrafnheim went quiet on
# 2026-08-06 had silently stopped covering upgraded worlds. Measured on Ulfsland
# 2026-09-12: each autosave writes a whole new generation into that directory and drops
# the previous one (generation 1 at 20:03, generation 2 at 20:33), so the directory's own
# mtime moves with every save.
#
# The directory itself is included in the scan rather than only its files: the game left
# it mode drw-rw-r-- at 20:33, and without the execute bit nothing inside a directory can
# be stat'ed, so `find -type f` in there silently matches nothing. On that same directory
# the directory mtime read 1789263187.49 against a newest file of 1789263187.46, so it is
# never staler than its contents either.
newest_save_age() {
    local dir=$1 newest
    [[ -d $dir ]] || return 0
    newest=$(find "$dir" -maxdepth 1 -printf '%T@\n' 2>/dev/null | sort -rn | head -n 1)
    [[ -n $newest ]] || return 0
    printf '%s' "$(( $(date +%s) - ${newest%.*} ))"
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
    start_output=""
    if ! start_output=$(sudo -n -u "$agent_user" env VALHEIM_ROOT="$worlds_root" VALHEIM_SERVER_DOCKER_DIR="$server_docker_dir" \
        "$hostops/start_valheim_server.sh" "$world" 2>&1); then
        # A refused start and a failed start leave the world in the same place and must not read
        # the same way. The gate records why in <world>/mods/.game-build-hold, so the distinction
        # is a file an operator can read, not a shell script they have to reverse-engineer.
        if [[ -f $(game_build_hold_path "$world") ]]; then
            note "$world start REFUSED by the game-build gate - deliberately DOWN, not broken; see $(game_build_hold_path "$world")"
            return
        fi
        note "$world start reported failure: $(printf '%s' "$start_output" | tr '\n' ' ')"
    fi
    note "$world restarted"
}

while read -r container; do
    [[ -n $container ]] || continue
    world=${container#valheim-server-}
    worlds_local="$worlds_root/$world/config_merged/worlds_local"
    output="$log_root/$world.log"
    # Does this world still run the build its mod set was anchored to? The gate answers, records a
    # hold when it does not, and its answer decides whether this world may be restarted at all - a
    # restart is a start, and a start applies whatever the Steam cache holds.
    #
    # A held world is left exactly as it is. It is not stopped here: a running world whose binary
    # changed under it may already have loaded a fresh empty world, and both a graceful stop and
    # the next autosave write that over the real save, so there is no automatic action that is
    # safe without first measuring which world is loaded. What this does guarantee is that the
    # watchdog will not restart it into the mismatch, and that the reason is on disk.
    gate_output=""
    if ! gate_output=$(require_matching_game_build "$world" 2>&1); then
        note "$world BUILD HOLD: $(printf '%s' "$gate_output" | tr '\n' ' ')"
        note "$world left untouched - deliberately not restarted; see $(game_build_hold_path "$world")"
        continue
    fi
    if [[ -n $gate_output ]]; then
        note "$world game-build gate: $(printf '%s' "$gate_output" | tr '\n' ' ')"
    fi

    save_age=$(age_of "$worlds_local/$world.db")
    # The pair first, so nothing changes for the four worlds still on 0.220.x, then the
    # 1.0 directory. Both are live on this host at once.
    [[ -n $save_age ]] || save_age=$(newest_save_age "$worlds_local/$world")
    log_age=$(age_of "$output")

    # An absent save or log is not evidence of a wedge - a freshly created world
    # has neither, and the collector may not have attached yet.
    [[ -n $save_age && -n $log_age ]] || { note "$world skipped (save or log not present yet)"; continue; }

    if (( save_age > save_stale && log_age > log_stale )); then
        note "$world WEDGED: no save for ${save_age}s, no output for ${log_age}s"
        restart_world "$world"
    fi
done < <(docker ps --filter 'name=valheim-server-' --format '{{.Names}}')
