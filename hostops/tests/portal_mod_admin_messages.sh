#!/usr/bin/env bash
# Proves portal_mod_admin.sh explains every rejection instead of exiting silently.
# Only rejection paths are exercised, so the mod controller is never executed.
# Run: bash hostops/tests/portal_mod_admin_messages.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ADMIN="$SCRIPT_DIR/../portal_mod_admin.sh"

tmp=$(mktemp -d /tmp/mod-admin-msg.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
expect_reject() { # <expected stderr substring> <args...>
  local want=$1; shift
  local rc=0
  bash "$ADMIN" "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
  if [[ $rc -ne 2 ]]; then
    echo "FAIL: [$*] exit $rc, want 2" >&2
    failures=$((failures + 1))
  elif ! grep -qF -- "$want" "$tmp/err"; then
    echo "FAIL: [$*] stderr = '$(cat "$tmp/err")', want substring '$want'" >&2
    failures=$((failures + 1))
  fi
}

W=Midgard
P=redesign-alpha

expect_reject "mod action 'inventory' expects 0 argument(s), got 1" "$W" "$P" inventory extra
expect_reject "mod action 'search' expects 1 argument(s), got 0" "$W" "$P" search
expect_reject "mod action 'custom-list' expects 0 argument(s), got 2" "$W" "$P" custom-list a b
expect_reject "mod action 'add' expects 3 argument(s), got 2" "$W" "$P" add pkg 1.0
expect_reject "mod action 'add' expects scope 'shared' or 'client-only', got 'bogus'" "$W" "$P" add pkg 1.0 bogus
expect_reject "mod action 'remove' expects 2 argument(s), got 1" "$W" "$P" remove pkg
expect_reject "mod action 'enable' expects 1 argument(s), got 0" "$W" "$P" enable
expect_reject "mod action 'disable' expects 1 argument(s), got 2" "$W" "$P" disable a b
expect_reject "mod action 'custom-add' expects 2 argument(s), got 1" "$W" "$P" custom-add pkg
expect_reject "mod action 'custom-add' expects scope 'shared' or 'client-only', got 'nope'" "$W" "$P" custom-add pkg nope
expect_reject "mod action 'custom-remove' expects 1 argument(s), got 0" "$W" "$P" custom-remove
expect_reject "mod action 'custom-enable' expects 1 argument(s), got 3" "$W" "$P" custom-enable a b c
expect_reject "mod action 'custom-disable' expects 1 argument(s), got 0" "$W" "$P" custom-disable
expect_reject "mod action 'deploy' expects 0 argument(s), got 1" "$W" "$P" deploy now

# Pre-existing guards must keep their own wording.
expect_reject "invalid world" "bad world" "$P" inventory
expect_reject "invalid profile" "$W" "bad profile" inventory
expect_reject "unsupported mod action" "$W" "$P" nonsense

[[ $failures -eq 0 ]] || { echo "$failures rejection(s) misreported" >&2; exit 1; }
echo "PASS: portal_mod_admin.sh rejection messages"
