#!/usr/bin/env bash
set -euo pipefail

# The tail of one world's collected server log, for the admin site.
#
# Two log sources exist and they are not equivalent. logs_valheim_server_snapshot.sh reads the live
# container, so it dies with the container. This reads the file hostops/collect_valheim_server_logs.sh
# appends outside the container, which survives a restart, a `compose down`, and the container being
# removed - and is therefore the only one that can answer what happened before a crash.
#
# It never prints the whole file. Hrafnheim's is over 11 MB and only grows; a page that renders that
# is a browser hang, and an operator asking for "the log" means the end of it.
#
# usage: portal_world_log.sh <World> [lines] [filter]
#        portal_world_log.sh <World> info
#
# Exit 0 with a message when no log has been collected: three worlds here have none, because they
# stopped the day before the collector started, and "empty box" is a worse answer than saying so.

WORLD=${1:-}
SECOND=${2:-200}
FILTER=${3:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

reject() { echo "$*" >&2; exit 2; }

[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || reject "invalid world"

LOG_ROOT=${VALHEIM_LOG_ROOT:-/var/log/valheim-worlds}
LOG="$LOG_ROOT/$WORLD.log"

if [[ $SECOND == info ]]; then
  if [[ -f $LOG ]]; then
    printf 'path=%s\nbytes=%s\nmodified=%s\n' "$LOG" "$(stat -c '%s' "$LOG")" "$(stat -c '%y' "$LOG")"
  else
    printf 'path=%s\nbytes=0\nmodified=never\n' "$LOG"
  fi
  exit 0
fi

# Bounded because the answer is rendered into a page and passed back through the agent socket. The
# ceiling is lines rather than bytes so a filter that matches nothing still costs a bounded read.
[[ $SECOND =~ ^[0-9]{1,5}$ ]] || reject "line count must be a number between 1 and 5000, got '$SECOND'"
((10#$SECOND >= 1 && 10#$SECOND <= 5000)) || reject "line count must be between 1 and 5000, got '$SECOND'"

if [[ -n $FILTER ]]; then
  # A filter reaches grep, so it is a fixed string, not a pattern: an operator looking for
  # "ZDOS:2084" should not have to think about regex, and a pathological pattern should not be able
  # to spend the agent's time.
  [[ ${#FILTER} -le 120 ]] || reject "filter must be 120 characters or fewer"
  # No NUL check: bash cannot hold one in an argument, and $'\0' is an empty pattern, so testing
  # for it matches every string - which rejected every filter until this was measured.
  [[ $FILTER != *$'\n'* && $FILTER != *$'\r'* ]] || reject "filter must be a single line"
fi

if [[ ! -f $LOG ]]; then
  echo "no collected log for $WORLD at $LOG"
  echo "hostops/collect_valheim_server_logs.sh writes one per running world; a world that has not run since the collector started has none."
  exit 0
fi

if [[ -n $FILTER ]]; then
  # Filter across the whole file, then take the tail of what matched, so a term that last appeared
  # an hour ago is still findable. grep -F: fixed string. The head guard bounds a filter that
  # matches nearly every line.
  grep -aF -- "$FILTER" "$LOG" | tail -n "$SECOND" || echo "no lines matching '$FILTER' in $WORLD.log"
else
  tail -n "$SECOND" "$LOG"
fi
