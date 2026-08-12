#!/usr/bin/env bash
set -euo pipefail

# What the server is stuck IN, captured while it is still stuck.
#
# capture_valheim_diagnostics.sh takes an inventory - logs, plugin hashes, world files - which
# answers "what was installed" but never "what is this process doing right now". A hang leaves no
# trace in any of those: the 2026-08-12 hang produced a container that was up, listening, burning
# 2% CPU, with its last log line 32 minutes old and a client that timed out connecting. Nothing in
# the bundle could distinguish a deadlock from a blocked write, so the only remedy was a restart,
# which destroys the evidence.
#
# Everything here is read from /proc on the HOST against the container's PID. Deliberately so: if
# the process is blocked writing to stdout, anything that needs the container's stdout - including
# Mono's own SIGQUIT thread dump - is exactly what cannot be relied on.
#
#   capture_valheim_hang.sh WORLD_NAME [OUTPUT_DIR]

WORLD_NAME=${1:?"usage: capture_valheim_hang.sh WORLD_NAME [OUTPUT_DIR]"}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_valheim_root

CONTAINER_NAME="valheim-server-$WORLD_NAME"
WORLD_DIR="$VALHEIM_ROOT/$WORLD_NAME"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUTPUT_DIR=${2:-"$WORLD_DIR/diagnostics/hang-$TIMESTAMP"}

if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "container not found: $CONTAINER_NAME" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

CONTAINER_PID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER_NAME")
if [[ -z "$CONTAINER_PID" || "$CONTAINER_PID" == "0" ]]; then
    echo "container is not running: $CONTAINER_NAME" >&2
    exit 1
fi

# The game itself, not supervisord: the container's init spawns valheim_server.x86_64 and that is
# the process whose threads matter.
GAME_PID=$(pgrep --parent "$CONTAINER_PID" --full 'valheim_server' 2>/dev/null | head -1 || true)
if [[ -z "$GAME_PID" ]]; then
    GAME_PID=$(pgrep --full 'valheim_server\.x86_64' 2>/dev/null | head -1 || true)
fi

{
    echo "captured_at=$(date --iso-8601=seconds)"
    echo "world=$WORLD_NAME"
    echo "container=$CONTAINER_NAME"
    echo "container_pid=$CONTAINER_PID"
    echo "game_pid=${GAME_PID:-NOT_FOUND}"
    docker inspect --format 'status={{.State.Status}} running={{.State.Running}} started_at={{.State.StartedAt}} restarts={{.RestartCount}}' "$CONTAINER_NAME"
} > "$OUTPUT_DIR/hang-context.txt"

# How stale is the output? This is the symptom that defines the hang, so record it as a number.
LAST_LOG_LINE=$(docker logs --timestamps --tail 1 "$CONTAINER_NAME" 2>&1 | head -1 || true)
LAST_LOG_EPOCH=$(date -d "$(printf '%s' "$LAST_LOG_LINE" | awk '{print $1}')" +%s 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
{
    echo "last_log_line=$LAST_LOG_LINE"
    if [[ "$LAST_LOG_EPOCH" != "0" ]]; then
        echo "log_silence_seconds=$(( NOW_EPOCH - LAST_LOG_EPOCH ))"
    else
        echo "log_silence_seconds=UNKNOWN"
    fi
} >> "$OUTPUT_DIR/hang-context.txt"

if [[ -n "${GAME_PID:-}" ]]; then
    # Is the process still executing at all? This is the discriminator, and the only one that does
    # not require interpreting a wchan: a game loop that is running burns CPU between two samples, a
    # wedged one does not. Measured over two seconds against the main thread and the whole process.
    read -r _ _ _ _ _ _ _ _ _ _ _ _ _ UT0 ST0 _ < "/proc/$GAME_PID/stat"
    MAIN_UT0=$(awk '{print $14}' "/proc/$GAME_PID/task/$GAME_PID/stat" 2>/dev/null || echo 0)
    sleep 2
    read -r _ _ _ _ _ _ _ _ _ _ _ _ _ UT1 ST1 _ < "/proc/$GAME_PID/stat"
    MAIN_UT1=$(awk '{print $14}' "/proc/$GAME_PID/task/$GAME_PID/stat" 2>/dev/null || echo 0)
    CPU_TICKS=$(( (UT1 + ST1) - (UT0 + ST0) ))
    MAIN_TICKS=$(( MAIN_UT1 - MAIN_UT0 ))
    {
        echo "process_cpu_ticks_per_2s=$CPU_TICKS"
        echo "main_thread_cpu_ticks_per_2s=$MAIN_TICKS"
    } >> "$OUTPUT_DIR/hang-context.txt"

    # Per-thread kernel state. wchan and syscall together name the blocking call: a thread parked in
    # pipe_write is a blocked log pipe, futex is a lock, ep_poll and hrtimer_nanosleep are ordinary
    # idling. Thread names contain spaces ("Job.Worker 0"), so they are printed with spaces squashed
    # rather than breaking every column to their right.
    {
        printf '%-8s %-22s %-6s %-28s %s\n' TID COMM STATE WCHAN SYSCALL
        for task in /proc/"$GAME_PID"/task/*; do
            tid=$(basename "$task")
            comm=$(tr ' ' '_' < "$task/comm" 2>/dev/null | tr -d '\n' || echo '?')
            state=$(awk '{print $3}' "$task/stat" 2>/dev/null || echo '?')
            wchan=$(tr -d '\n' < "$task/wchan" 2>/dev/null || echo '?')
            syscall=$(awk '{print $1}' "$task/syscall" 2>/dev/null || echo '?')
            printf '%-8s %-22s %-6s %-28s %s\n' "$tid" "${comm:-?}" "$state" "${wchan:-?}" "$syscall"
        done
    } > "$OUTPUT_DIR/threads.txt" 2>&1

    # Kernel stacks need root and an unrestricted kptr; capture whatever is permitted rather than
    # failing the whole bundle.
    for task in /proc/"$GAME_PID"/task/*; do
        tid=$(basename "$task")
        if [[ -r "$task/stack" ]]; then
            echo "=== tid $tid ($(cat "$task/comm" 2>/dev/null))"
            cat "$task/stack" 2>/dev/null || true
        fi
    done > "$OUTPUT_DIR/thread-stacks.txt" 2>&1

    cp "/proc/$GAME_PID/status" "$OUTPUT_DIR/proc-status.txt" 2>/dev/null || true

    # Where stdout actually goes, and whether it is backed up. A pipe whose reader has stopped is
    # the difference between "the game deadlocked" and "the game is waiting to print".
    {
        echo "=== fd targets"
        ls -l "/proc/$GAME_PID/fd/1" "/proc/$GAME_PID/fd/2" 2>/dev/null || true
        echo "=== fdinfo for stdout"
        cat "/proc/$GAME_PID/fdinfo/1" 2>/dev/null || true
        echo "=== pipe buffer sizes in use"
        for fd in /proc/"$GAME_PID"/fd/*; do
            target=$(readlink "$fd" 2>/dev/null || true)
            [[ "$target" == pipe:* ]] || continue
            echo "$fd -> $target"
        done
    } > "$OUTPUT_DIR/stdout-state.txt" 2>&1
fi

{
    echo "=== docker stats"
    timeout 20 docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} pids={{.PIDs}}' "$CONTAINER_NAME" 2>&1 || true
    echo "=== processes inside the container"
    timeout 20 docker exec "$CONTAINER_NAME" ps -eo pid,stat,etime,pcpu,comm 2>&1 | head -25 || echo "docker exec failed - the container is not answering, which is itself a finding"
    echo "=== sockets on the host for the game ports"
    ss -uanp 2>/dev/null | grep -E ':(2456|2457|2458)' | head -10 || true
} > "$OUTPUT_DIR/runtime-state.txt" 2>&1

# Mono dumps every managed thread on SIGQUIT - OPT IN with HANG_CAPTURE_SIGQUIT=1.
#
# Unity's embedded runtime is not guaranteed to install that handler, and the default action for
# SIGQUIT is terminate-with-core. Killing a server to find out why it is stuck is not a decision
# this script makes unattended. It writes to the process's stdout, so it is captured
# LAST and treated as a bonus: if stdout is the thing that is blocked, this produces nothing, and
# that silence is consistent with the kernel state above rather than a contradiction of it.
if [[ -n "${GAME_PID:-}" && "${HANG_CAPTURE_SIGQUIT:-0}" == "1" ]]; then
    SIGQUIT_AT=$(date --iso-8601=seconds)
    kill -QUIT "$GAME_PID" 2>/dev/null || true
    sleep 3
    docker logs --timestamps --since "$SIGQUIT_AT" "$CONTAINER_NAME" 2>&1 | head -400 > "$OUTPUT_DIR/mono-thread-dump.txt" || true
fi

docker logs --timestamps --tail 400 "$CONTAINER_NAME" 2>&1 \
    | sed -E 's#https://discord[^[:space:]"]*#[REDACTED_DISCORD_WEBHOOK]#g' \
    > "$OUTPUT_DIR/docker-tail.log" || true

# A one-line verdict, so the bundle answers the question without being read in full.
# A one-line verdict, so the bundle answers the question without being read in full.
#
# Judged on the MAIN thread plus whether the process burned any CPU, because a healthy server has
# dozens of pool threads parked on futex and wait_woken - reading those as contention produced a
# false "LOCK_CONTENTION" on a perfectly good server the first time this ran.
VERDICT="inconclusive"
if [[ -f "$OUTPUT_DIR/threads.txt" && -n "${GAME_PID:-}" ]]; then
    MAIN_ROW=$(awk -v p="$GAME_PID" '$1==p' "$OUTPUT_DIR/threads.txt" || true)
    MAIN_WCHAN=$(printf '%s' "$MAIN_ROW" | awk '{print $4}')
    CPU_TICKS=$(grep -oP '(?<=^process_cpu_ticks_per_2s=)-?\d+' "$OUTPUT_DIR/hang-context.txt" 2>/dev/null || echo -1)

    if printf '%s' "$MAIN_ROW" | grep -qiE 'pipe_write'; then
        VERDICT="BLOCKED_WRITING_TO_STDOUT - the log pipe is not being drained; the game loop is stopped waiting to print"
    elif grep -qiE 'pipe_write' "$OUTPUT_DIR/threads.txt"; then
        VERDICT="A_THREAD_IS_BLOCKED_WRITING_TO_STDOUT - not the main thread, but the log pipe is backing up"
    elif [[ "$CPU_TICKS" == "0" ]]; then
        VERDICT="NO_CPU_PROGRESS - the process burned zero CPU over two seconds; main thread wchan=${MAIN_WCHAN:-unknown}"
    elif printf '%s' "$MAIN_ROW" | grep -qiE 'futex'; then
        VERDICT="MAIN_THREAD_ON_FUTEX - waiting on a lock while the process still runs"
    elif printf '%s' "$MAIN_ROW" | grep -qiE 'hrtimer_nanosleep|ep_poll|poll_schedule'; then
        VERDICT="MAIN_LOOP_ALIVE - the game loop is pacing normally; the silence is not a wedged main thread"
    fi
fi
echo "verdict=$VERDICT" >> "$OUTPUT_DIR/hang-context.txt"

echo "Captured hang bundle: $OUTPUT_DIR"
echo "verdict: $VERDICT"
