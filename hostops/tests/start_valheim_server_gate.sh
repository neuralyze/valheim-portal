#!/usr/bin/env bash
# Proves start_valheim_server.sh honours the client-release cutover gate.
# Run: bash hostops/tests/start_valheim_server_gate.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
WORLD=Midgard-Redesign

tmp=$(mktemp -d /tmp/start-gate.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# The sandbox mirrors the real layout: a hostops directory holding the script,
# its lib/common.sh and the stubbed gate, with the world root and the
# valheim-server-docker checkout supplied through the environment.
mkdir -p "$tmp/hostops/lib" "$tmp/bin" "$tmp/valheim-server-docker" "$tmp/valheim/$WORLD"
cp "$HOSTOPS/start_valheim_server.sh" "$tmp/hostops/"
cp "$HOSTOPS/lib/common.sh" "$tmp/hostops/lib/"
touch "$tmp/valheim/$WORLD/valheim.env" "$tmp/valheim-server-docker/docker-compose.yaml"

# Stub docker: records its argv instead of touching a real container.
cat >"$tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
EOF
chmod +x "$tmp/bin/docker"

# Stub gate: GATE_EXIT decides whether the cutover is still pending.
cat >"$tmp/hostops/manage_mods.sh" <<'EOF'
#!/usr/bin/env bash
echo "gate invoked: $*"
exit "${GATE_EXIT:-0}"
EOF
chmod +x "$tmp/hostops/manage_mods.sh"

export DOCKER_LOG="$tmp/docker.log"
export PATH="$tmp/bin:$PATH"
export VALHEIM_ROOT="$tmp/valheim"
export VALHEIM_SERVER_DOCKER_DIR="$tmp/valheim-server-docker"

run_start() {
  : >"$DOCKER_LOG"
  set +e
  GATE_EXIT=$1 bash "$tmp/hostops/start_valheim_server.sh" "${@:2}" >"$tmp/out" 2>"$tmp/err"
  rc=$?
  set -e
}

# 1. Pending cutover: abort before the container starts.
run_start 1 "$WORLD"
[[ $rc -ne 0 ]] || fail "pending cutover: expected non-zero exit, got 0"
[[ ! -s $DOCKER_LOG ]] || fail "pending cutover: docker was invoked: $(cat "$DOCKER_LOG")"
grep -q "Refusing to start $WORLD" "$tmp/err" ||
  fail "pending cutover: stderr does not name the world: $(cat "$tmp/err")"
grep -q 'release-confirm' "$tmp/err" ||
  fail "pending cutover: stderr does not tell the operator what to do: $(cat "$tmp/err")"

# 2. Cleared cutover, no service argument (how portal/internal/agent/agent.go calls it).
run_start 0 "$WORLD"
[[ $rc -eq 0 ]] || fail "cleared cutover: expected exit 0, got $rc -- $(cat "$tmp/err")"
want="compose --project-name ${WORLD,,} --env-file $tmp/valheim/$WORLD/valheim.env up -d"
got=$(cat "$DOCKER_LOG")
[[ $got == "$want" ]] || fail "cleared cutover: docker argv = '$got', want '$want'"

# 3. Cleared cutover with an explicit compose service.
run_start 0 "$WORLD" valheim-server
[[ $rc -eq 0 ]] || fail "service argument: expected exit 0, got $rc -- $(cat "$tmp/err")"
got=$(cat "$DOCKER_LOG")
[[ $got == "$want valheim-server" ]] || fail "service argument: docker argv = '$got'"

# 4. Missing world name is refused outright.
run_start 0
[[ $rc -ne 0 ]] || fail "missing world: expected non-zero exit"
[[ ! -s $DOCKER_LOG ]] || fail "missing world: docker was invoked: $(cat "$DOCKER_LOG")"

# 5. An unset VALHEIM_SERVER_DOCKER_DIR is a configuration error, not a docker
#    failure: the script must name the variable and exit 78 before the gate
#    runs, so nothing is started against a guessed compose project.
: >"$DOCKER_LOG"
rc=0
env -u VALHEIM_SERVER_DOCKER_DIR bash "$tmp/hostops/start_valheim_server.sh" "$WORLD" \
  >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -eq 78 ]] || fail "unset VALHEIM_SERVER_DOCKER_DIR: exit $rc, want 78"
grep -q 'VALHEIM_SERVER_DOCKER_DIR' "$tmp/err" ||
  fail "unset VALHEIM_SERVER_DOCKER_DIR: stderr does not name it: $(cat "$tmp/err")"
[[ ! -s $DOCKER_LOG ]] || fail "unset VALHEIM_SERVER_DOCKER_DIR: docker was invoked"

echo "PASS: start_valheim_server.sh release gate"
