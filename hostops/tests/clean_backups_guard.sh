#!/usr/bin/env bash
# Proves clean_backups.sh validates its argument and does not delete archives
# behind an operator's back. It used to accept anything, including 0 -- which
# find reads as "older than 24 hours", i.e. wipe nearly the whole inventory --
# with no validation, no confirmation, no dry run and no output.
# Run: bash hostops/tests/clean_backups_guard.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CLEAN="$SCRIPT_DIR/../clean_backups.sh"

tmp=$(mktemp -d /tmp/clean-backups.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

root="$tmp/valheim"
backups="$root/world_backups"
mkdir -p "$backups"
export VALHEIM_ROOT=$root

seed_backups() {
  rm -f -- "$backups"/*.tgz
  : >"$backups/world-Midgard-old-2020-01-01_00-00-00.tgz"
  : >"$backups/world-Midgard-older-2019-01-01_00-00-00.tgz"
  touch -d '400 days ago' "$backups"/*.tgz
  : >"$backups/world-Midgard-fresh-2026-08-01_00-00-00.tgz"
}

count_backups() { local files=("$backups"/*.tgz); echo "${#files[@]}"; }
shopt -s nullglob

# 1. A non-integer argument is refused, and nothing is deleted.
seed_backups
for bad in abc 3.5 -1 '' 1e3 '30 '; do
  rc=0
  bash "$CLEAN" "$bad" >"$tmp/out" 2>"$tmp/err" || rc=$?
  [[ $rc -eq 2 ]] || fail "argument '$bad': exit $rc, want 2"
  grep -q 'positive integer' "$tmp/err" ||
    fail "argument '$bad': stderr does not explain the rule: $(cat "$tmp/err")"
done
[[ $(count_backups) -eq 3 ]] || fail "a rejected argument still deleted backups"

# 2. Zero is refused specifically: `clean_backups.sh 0` used to wipe everything
#    older than 24 hours.
rc=0
bash "$CLEAN" 0 >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -eq 2 ]] || fail "argument '0': exit $rc, want 2"
[[ $(count_backups) -eq 3 ]] || fail "clean_backups.sh 0 deleted backups"

# 3. On a terminal the default is a dry run: it reports what it would delete and
#    deletes nothing.
seed_backups
rc=0
script -qec "bash '$CLEAN' 30" /dev/null >"$tmp/tty" 2>&1 || rc=$?
[[ $rc -eq 0 ]] || fail "interactive default exited $rc: $(cat "$tmp/tty")"
grep -q 'would delete 2 backup(s)' "$tmp/tty" ||
  fail "interactive default did not report a dry run: $(cat "$tmp/tty")"
grep -q -- '--delete' "$tmp/tty" ||
  fail "interactive dry run did not say how to actually delete: $(cat "$tmp/tty")"
[[ $(count_backups) -eq 3 ]] || fail "interactive default deleted backups"

# 4. --delete on a terminal does delete, and reports the count.
rc=0
script -qec "bash '$CLEAN' --delete 30" /dev/null >"$tmp/tty" 2>&1 || rc=$?
[[ $rc -eq 0 ]] || fail "--delete exited $rc: $(cat "$tmp/tty")"
grep -q 'deleted 2 backup(s)' "$tmp/tty" ||
  fail "--delete did not report the count: $(cat "$tmp/tty")"
[[ $(count_backups) -eq 1 ]] || fail "--delete removed $(count_backups) of 3 backups, want 1 left"

# 5. Without a terminal the default deletes, so cron and the agent still prune.
seed_backups
bash "$CLEAN" 30 >"$tmp/out" 2>"$tmp/err" || fail "non-interactive run failed: $(cat "$tmp/err")"
grep -q 'deleted 2 backup(s)' "$tmp/out" ||
  fail "non-interactive run did not report the count: $(cat "$tmp/out")"
[[ $(count_backups) -eq 1 ]] || fail "non-interactive default did not prune"

# 6. --dry-run is honoured without a terminal too.
seed_backups
bash "$CLEAN" --dry-run 30 >"$tmp/out" 2>"$tmp/err" || fail "--dry-run failed: $(cat "$tmp/err")"
grep -q 'would delete 2 backup(s)' "$tmp/out" || fail "--dry-run did not report: $(cat "$tmp/out")"
[[ $(count_backups) -eq 3 ]] || fail "--dry-run deleted backups"

[[ $failures -eq 0 ]] || { echo "$failures clean_backups.sh check(s) failed" >&2; exit 1; }
echo "PASS: clean_backups.sh argument and dry-run guards"
