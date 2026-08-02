#!/usr/bin/env bash
# Proves the host scripts resolve every deployment path outside this repository
# from the environment and refuse to guess one. Before hostops/lib/common.sh
# they hardcoded an absolute path, so on any other machine they silently
# operated on a directory that did not exist -- or, worse, on someone else's.
# Two paths are outside the repository: the world root and the
# valheim-server-docker checkout. Both resolve here, both exit 78 when unset.
# Run: bash hostops/tests/valheim_root_resolution.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
WORLD=Midgard-Redesign

tmp=$(mktemp -d /tmp/valheim-root.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

root="$tmp/valheim"
world_dir="$root/$WORLD/config_merged/worlds_local"
mkdir -p "$world_dir" "$root/world_backups"
printf 'DB\n' >"$world_dir/${WORLD,,}.db"
printf 'FWL\n' >"$world_dir/${WORLD,,}.fwl"
: >"$root/world_backups/world-$WORLD-known-good-2026-01-01_00-00-00.tgz"

# Run without inheriting any root the operator happens to have exported, so the
# unset case is genuinely unset.
clean() { env -u VALHEIM_ROOT -u AGENT_WORLD_ROOT -u VALHEIM_WORLD_ROOT "$@"; }

# 1. Every root-consuming script must refuse to run with no root configured,
#    must name the variable the operator has to set, and must exit 78
#    (sysexits.h EX_CONFIG) so a caller can tell "misconfigured" from "failed".
for script in \
  list_valheim_world_backups.sh \
  backup_valheim_world.sh \
  restore_valheim_world.sh \
  portal_delete_valheim_server.sh \
  clean_backups.sh
do
  rc=0
  case $script in
    restore_valheim_world.sh)
      clean bash "$HOSTOPS/$script" "$WORLD" "world-$WORLD-known-good-2026-01-01_00-00-00.tgz" \
        >"$tmp/out" 2>"$tmp/err" || rc=$?
      ;;
    clean_backups.sh)
      clean bash "$HOSTOPS/$script" --dry-run 30 >"$tmp/out" 2>"$tmp/err" || rc=$?
      ;;
    *)
      clean bash "$HOSTOPS/$script" "$WORLD" >"$tmp/out" 2>"$tmp/err" || rc=$?
      ;;
  esac
  [[ $rc -eq 78 ]] || fail "$script with no world root configured: exit $rc, want 78"
  grep -q 'VALHEIM_ROOT' "$tmp/err" ||
    fail "$script did not name VALHEIM_ROOT when unset: $(cat "$tmp/err")"
  # A silent fallback to a path nobody configured is the exact bug this guards.
  ! grep -qE '/media/|/home/' "$tmp/err" "$tmp/out" ||
    fail "$script still refers to a hardcoded absolute root"
done

# 2. VALHEIM_ROOT is honoured: the listing comes from the sandbox, not elsewhere.
got=$(clean env VALHEIM_ROOT="$root" bash "$HOSTOPS/list_valheim_world_backups.sh" "$WORLD")
[[ $got == "world-$WORLD-known-good-2026-01-01_00-00-00.tgz" ]] ||
  fail "VALHEIM_ROOT not honoured by list_valheim_world_backups.sh: got '$got'"

# 3. A backup lands under the configured root, nowhere else.
clean env VALHEIM_ROOT="$root" bash "$HOSTOPS/backup_valheim_world.sh" "$WORLD" roottest \
  >"$tmp/out" 2>"$tmp/err" || fail "backup failed under VALHEIM_ROOT: $(cat "$tmp/err")"
shopt -s nullglob
written=("$root/world_backups/world-$WORLD-roottest-"*.tgz)
((${#written[@]} == 1)) || fail "backup did not write into VALHEIM_ROOT/world_backups"

# 4. The portal agent exports AGENT_WORLD_ROOT and the installer documents
#    VALHEIM_WORLD_ROOT. Both must resolve, or agent-invoked scripts break.
for alias_name in AGENT_WORLD_ROOT VALHEIM_WORLD_ROOT; do
  got=$(clean env "$alias_name=$root" bash "$HOSTOPS/list_valheim_world_backups.sh" "$WORLD" | head -1)
  [[ $got == world-"$WORLD"-* ]] ||
    fail "$alias_name was ignored: got '$got'"
done

# 5. A relative or missing root is rejected rather than half-resolved.
rc=0
clean env VALHEIM_ROOT=relative/path bash "$HOSTOPS/list_valheim_world_backups.sh" "$WORLD" \
  >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -ne 0 ]] || fail "a relative VALHEIM_ROOT was accepted"
grep -q 'absolute' "$tmp/err" || fail "relative root rejection does not explain itself: $(cat "$tmp/err")"

rc=0
clean env VALHEIM_ROOT="$tmp/does-not-exist" bash "$HOSTOPS/list_valheim_world_backups.sh" "$WORLD" \
  >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -ne 0 ]] || fail "a nonexistent VALHEIM_ROOT was accepted"
grep -q 'not a directory' "$tmp/err" || fail "missing root rejection does not explain itself: $(cat "$tmp/err")"

# 6. The valheim-server-docker checkout is resolved the same way and refuses to
#    guess for the same reason: the lifecycle scripts run `docker compose down`
#    and `rm -v` against whatever project lives there.
docker_dir="$tmp/valheim-server-docker"
mkdir -p "$docker_dir"
for case_name in unset relative missing no-compose-file; do
  rc=0
  case $case_name in
    unset)            value=() ;;
    relative)         value=(VALHEIM_SERVER_DOCKER_DIR=relative/path) ;;
    missing)          value=(VALHEIM_SERVER_DOCKER_DIR="$tmp/does-not-exist") ;;
    no-compose-file)  value=(VALHEIM_SERVER_DOCKER_DIR="$docker_dir") ;;
  esac
  clean env -u VALHEIM_SERVER_DOCKER_DIR VALHEIM_ROOT="$root" "${value[@]}" \
    bash "$HOSTOPS/status_valheim_server.sh" "$WORLD" >"$tmp/out" 2>"$tmp/err" || rc=$?
  [[ $rc -eq 78 ]] || fail "VALHEIM_SERVER_DOCKER_DIR $case_name: exit $rc, want 78"
  grep -q 'VALHEIM_SERVER_DOCKER_DIR' "$tmp/err" ||
    fail "VALHEIM_SERVER_DOCKER_DIR $case_name: stderr does not name it: $(cat "$tmp/err")"
done

# A real checkout is accepted: only the compose invocation itself is missing
# here, so the resolver must not be what fails.
touch "$docker_dir/docker-compose.yaml"
rc=0
clean env VALHEIM_ROOT="$root" VALHEIM_SERVER_DOCKER_DIR="$docker_dir" \
  bash "$HOSTOPS/status_valheim_server.sh" "$WORLD" >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -ne 78 ]] || fail "a valid VALHEIM_SERVER_DOCKER_DIR was rejected: $(cat "$tmp/err")"

[[ $failures -eq 0 ]] || { echo "$failures deployment-path check(s) failed" >&2; exit 1; }
echo "PASS: VALHEIM_ROOT and VALHEIM_SERVER_DOCKER_DIR resolution"
