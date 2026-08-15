#!/usr/bin/env bash
# Proves portal_world_log.sh bounds what reaches the host and names every refusal.
# A log view is a read, but an unbounded one is a browser hang or a host disk read of 12 MB, so
# the bounds are the behaviour under test. Reads run against a fixture log, never a real one.
# Run: bash hostops/tests/portal_world_log_messages.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LOGVIEW="$SCRIPT_DIR/../portal_world_log.sh"

tmp=$(mktemp -d /tmp/world-log-msg.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

mkdir -p "$tmp/worlds/Hrafnheim" "$tmp/logs"
{
  for i in $(seq 1 400); do
    printf '2026-08-15T07:00:00Z supervisord: valheim-server line %s Connections 0\n' "$i"
  done
  printf '2026-08-15T07:01:00Z supervisord: valheim-server [Message:   BepInEx] Chainloader startup complete\n'
} >"$tmp/logs/Hrafnheim.log"

export VALHEIM_ROOT="$tmp/worlds"
export VALHEIM_LOG_ROOT="$tmp/logs"

failures=0

expect_reject() { # <expected stderr substring> <args...>
  local want=$1; shift
  local rc=0
  bash "$LOGVIEW" "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
  if [[ $rc -eq 0 ]]; then
    echo "FAIL: [$*] exit 0, want non-zero" >&2
    failures=$((failures + 1))
  elif ! grep -qF -- "$want" "$tmp/err"; then
    echo "FAIL: [$*] stderr = '$(cat "$tmp/err")', want substring '$want'" >&2
    failures=$((failures + 1))
  fi
}

expect_output() { # <expected stdout substring> <args...>
  local want=$1; shift
  local rc=0
  bash "$LOGVIEW" "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "FAIL: [$*] exit $rc, want 0 (stderr: $(cat "$tmp/err"))" >&2
    failures=$((failures + 1))
  elif ! grep -qF -- "$want" "$tmp/out"; then
    echo "FAIL: [$*] stdout lacks '$want'" >&2
    failures=$((failures + 1))
  fi
}

expect_lines() { # <expected line count> <args...>
  local want=$1; shift
  local got
  got=$(bash "$LOGVIEW" "$@" | wc -l)
  if [[ $got -ne $want ]]; then
    echo "FAIL: [$*] printed $got lines, want $want" >&2
    failures=$((failures + 1))
  fi
}

# The line count is the browser's protection and the host's: neither an empty view nor the whole
# file is a useful answer, and both are one keystroke away in a URL.
expect_reject "line count must be between 1 and 5000, got '0'" Hrafnheim 0
expect_reject "line count must be between 1 and 5000, got '99999'" Hrafnheim 99999
expect_reject "line count must be a number between 1 and 5000, got 'abc'" Hrafnheim abc
expect_reject "line count must be a number between 1 and 5000, got '-5'" Hrafnheim -5

# A filter reaches grep, so a newline in it would be a second pattern the operator never typed.
expect_reject "filter must be a single line" Hrafnheim 5 "$(printf 'a\nb')"

# A world is a filename component here, and a log path is not a place to accept traversal.
expect_reject "invalid world" ../etc 5
expect_reject "invalid world" "Hrafn/heim" 5

# Reads. The tail is bounded by the count, the filter narrows it, and both absences say so.
expect_lines 3 Hrafnheim 3
expect_output "Chainloader startup complete" Hrafnheim 5 Chainloader
expect_output "no lines matching 'zzz-absent'" Hrafnheim 50 zzz-absent
expect_output "no collected log for Vangard" Vangard 5
expect_output "hostops/collect_valheim_server_logs.sh writes one per running world" Vangard 5

# The info form takes the place of the line count and reads none of the log, so it answers for a
# world that has never run - which is how the page shows a size beside an empty view.
expect_output "bytes=" Hrafnheim info
expect_output "modified=never" Vangard info

if [[ $failures -ne 0 ]]; then
  echo "FAIL: $failures refusal(s) or read(s) misreported" >&2
  exit 1
fi
echo "PASS: portal_world_log.sh bounds and explains every path"
