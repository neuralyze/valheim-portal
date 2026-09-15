#!/usr/bin/env bash
set -euo pipefail

# Detect a silent server and capture it before anyone restarts it.
#
# The server prints a connection summary every ten minutes, unprompted, whether or not anybody is
# playing. That cadence is the health signal: on 2026-08-12 it stopped at 02:43:15 while the
# container stayed up and a player kept playing for another seventeen minutes, after which the world
# froze and new connections timed out. By the time a human noticed, the only available action was a
# restart, which erased the evidence.
#
# So this runs on a timer, notices the silence, and captures the process state while it is still
# stuck. Restarting is opt-in and always happens AFTER the capture.
#
#   valheim_hang_watchdog.sh WORLD_NAME [--silence-minutes N] [--restart]

WORLD_NAME=${1:?"usage: valheim_hang_watchdog.sh WORLD_NAME [--silence-minutes N] [--restart]"}
shift || true

SILENCE_MINUTES=15   # heartbeat is every 10; 15 tolerates one missed print without crying wolf
RESTART=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --silence-minutes) SILENCE_MINUTES=${2:?"--silence-minutes needs a value"}; shift 2 ;;
        --restart) RESTART=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
CONTAINER_NAME="valheim-server-$WORLD_NAME"
# The default is the host's log directory, which only root can write. The override exists so the
# regression test can exercise the restart decision without root and without a real world.
STATE_DIR=${VALHEIM_HANG_STATE_DIR:-/var/log/valheim-worlds}
LOG_FILE="$STATE_DIR/hang-watchdog.log"
STAMP_FILE="$STATE_DIR/.hang-watchdog-$WORLD_NAME.last"

mkdir -p "$STATE_DIR"

note() { printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >> "$LOG_FILE"; }

docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 || exit 0
[[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]] || exit 0

# A container that started moments ago is loading a world, not hanging.
STARTED_AT=$(date -d "$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER_NAME")" +%s 2>/dev/null || echo 0)
NOW=$(date +%s)
if (( STARTED_AT > 0 && NOW - STARTED_AT < SILENCE_MINUTES * 60 )); then
    exit 0
fi

LAST_LINE=$(docker logs --timestamps --tail 1 "$CONTAINER_NAME" 2>&1 | head -1 || true)
LAST_EPOCH=$(date -d "$(printf '%s' "$LAST_LINE" | awk '{print $1}')" +%s 2>/dev/null || echo 0)
if (( LAST_EPOCH == 0 )); then
    note "$WORLD_NAME: could not parse a timestamp from the last log line; skipping"
    exit 0
fi

SILENCE=$(( NOW - LAST_EPOCH ))
if (( SILENCE < SILENCE_MINUTES * 60 )); then
    exit 0
fi

# One capture per silent episode. Without this the timer would produce a bundle every couple of
# minutes for as long as the server stays wedged.
if [[ -f "$STAMP_FILE" ]] && [[ "$(cat "$STAMP_FILE" 2>/dev/null)" == "$LAST_EPOCH" ]]; then
    exit 0
fi
printf '%s' "$LAST_EPOCH" > "$STAMP_FILE"

note "$WORLD_NAME: silent for ${SILENCE}s (threshold $(( SILENCE_MINUTES * 60 ))s) - capturing"
BUNDLE=""
if OUTPUT=$("$SCRIPT_DIR/capture_valheim_hang.sh" "$WORLD_NAME" 2>&1); then
    BUNDLE=$(printf '%s' "$OUTPUT" | sed -n 's/^Captured hang bundle: //p' | head -1)
    note "$WORLD_NAME: $(printf '%s' "$OUTPUT" | tr '\n' ' ')"
else
    note "$WORLD_NAME: capture FAILED: $(printf '%s' "$OUTPUT" | tr '\n' ' ')"
fi

VERDICT="unknown"
if [[ -n "$BUNDLE" && -f "$BUNDLE/hang-context.txt" ]]; then
    VERDICT=$(sed -n 's/^verdict=//p' "$BUNDLE/hang-context.txt" | head -1)
fi

# restart_is_allowed decides whether this world may be started again, and prints the gate's own
# explanation either way.
#
# A container start is not the cheap action it looks like. Starting one runs the in-container
# valheim-updater, which does `steamcmd +app_update 896660` and then rsyncs the download cache onto
# the install with --delete (valheim-updater:86,162), re-merges the BepInEx overlay and starts the
# server. So `docker restart` on this timer is an unattended GAME UPDATE, on a two-minute cycle,
# with no human present - and on 2026-09-13 a binary that did not match its mod set generated a
# fresh world and saved it over the real one.
#
# The gate is applied HERE rather than by routing this through stop_valheim_server.sh /
# start_valheim_server.sh the way watch_valheim_servers.sh does, for two measured reasons:
#
#   1. Those scripts require VALHEIM_SERVER_DOCKER_DIR and this unit does not set it -
#      /etc/systemd/system/valheim-hang-watchdog@.service carries only
#      Environment=VALHEIM_ROOT=... - so require_server_docker_dir would exit 78 and a hung world
#      would stop being recoverable at all. Trading a hazard for a broken recovery is worse than
#      the hazard.
#   2. `docker compose down` removes the container and `up -d --build` rebuilds the image; this
#      script exists to recover a wedge in seconds while keeping the evidence it just captured.
#
# The gate itself is the shared one, so the hang path and the five-minute watchdog path refuse on
# exactly the same evidence. It needs the world root: without it nothing can be measured, and an
# unmeasured restart is the hazard, so a missing root refuses rather than restarting blind.
restart_is_allowed() {
    if [[ -z ${VALHEIM_ROOT:-}${AGENT_WORLD_ROOT:-}${VALHEIM_WORLD_ROOT:-} ]]; then
        echo "no world root configured (VALHEIM_ROOT) - cannot check the game build"
        return 1
    fi
    require_valheim_root
    require_matching_game_build "$WORLD_NAME"
}

# Restart only when the evidence says the server is actually wedged.
#
# Silence alone is not enough to justify kicking players: the capture can distinguish a stopped game
# loop from one that is pacing normally, and a server whose loop is alive should be left alone and
# investigated rather than bounced on a schedule.
if (( RESTART )); then
    if [[ "$VERDICT" == MAIN_LOOP_ALIVE* ]]; then
        note "$WORLD_NAME: NOT restarting - the main loop is alive despite the silence ($VERDICT)"
    else
        GATE_OUTPUT=""
        if ! GATE_OUTPUT=$(restart_is_allowed 2>&1); then
            note "$WORLD_NAME: NOT restarting - the game-build gate refused: $(printf '%s' "$GATE_OUTPUT" | tr '\n' ' ')"
            note "$WORLD_NAME: the container was left as it is, hung and capturable. Nothing was updated."
            exit 1
        fi
        if [[ -n $GATE_OUTPUT ]]; then
            note "$WORLD_NAME: game-build gate: $(printf '%s' "$GATE_OUTPUT" | tr '\n' ' ')"
        fi
        note "$WORLD_NAME: restarting after capture ($VERDICT)"
        if docker restart "$CONTAINER_NAME" >/dev/null 2>&1; then
            note "$WORLD_NAME: restart completed"
        else
            note "$WORLD_NAME: restart FAILED"
        fi
    fi
fi
