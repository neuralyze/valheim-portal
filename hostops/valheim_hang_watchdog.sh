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
CONTAINER_NAME="valheim-server-$WORLD_NAME"
STATE_DIR=/var/log/valheim-worlds
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

# Restart only when the evidence says the server is actually wedged.
#
# Silence alone is not enough to justify kicking players: the capture can distinguish a stopped game
# loop from one that is pacing normally, and a server whose loop is alive should be left alone and
# investigated rather than bounced on a schedule.
if (( RESTART )); then
    if [[ "$VERDICT" == MAIN_LOOP_ALIVE* ]]; then
        note "$WORLD_NAME: NOT restarting - the main loop is alive despite the silence ($VERDICT)"
    else
        note "$WORLD_NAME: restarting after capture ($VERDICT)"
        if docker restart "$CONTAINER_NAME" >/dev/null 2>&1; then
            note "$WORLD_NAME: restart completed"
        else
            note "$WORLD_NAME: restart FAILED"
        fi
    fi
fi
