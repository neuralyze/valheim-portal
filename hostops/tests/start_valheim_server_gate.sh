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
cp "$HOSTOPS/start_valheim_server.sh" "$HOSTOPS/anchor_game_build.sh" "$tmp/hostops/"
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
# --build is load-bearing, not cosmetic: each world has its own `<world>-valheim`
# image and a bare `up -d` reuses a stale one, which on 2026-09-13 booted Hrafnheim
# with an empty BepInEx/patchers and 5,925 MissingMethodException. Keep it asserted.
want="compose --project-name ${WORLD,,} --env-file $tmp/valheim/$WORLD/valheim.env up -d --build"
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

# 6. The game-build gate. Three copies of the game live under a world's
#    DATA_DIR and only bepinex/ executes; on 2026-09-13 a stale cache was
#    rsynced over a good install, the overlay was never re-merged, and three
#    worlds booted an old binary against a new mod set and saved fresh empty
#    worlds over the real ones. These cases are that failure, in miniature.
managed=valheim_server_Data/Managed/assembly_valheim.dll
data="$tmp/valheim/$WORLD/data"
build_world() {
  # $1 install bytes, $2 overlay bytes, $3 cache bytes
  rm -rf -- "$data"
  local p
  for p in "server/$managed" "bepinex/$managed" "dl/server/$managed"; do
    mkdir -p -- "$(dirname "$data/$p")"
  done
  printf '%s' "$1" >"$data/server/$managed"
  printf '%s' "$2" >"$data/bepinex/$managed"
  printf '%s' "$3" >"$data/dl/server/$managed"
  printf "DATA_DIR='%s'\n" "$data" >"$tmp/valheim/$WORLD/valheim.env"
  # Each case here is about the three copies of the game agreeing with each other, so each one
  # starts from a world with no game-build anchor yet: the gate records the installed build on
  # first observation, and these cases are not about what happens on the second start.
  # hostops/tests/game_build_anchor_gate.sh is where the anchor itself is tested.
  rm -f -- "$tmp/valheim/$WORLD/mods/.game-build-anchor" "$tmp/valheim/$WORLD/mods/.game-build-hold"
}

# 6a. All three agree: start normally, and do not signal a re-merge.
build_world same same same
run_start 0 "$WORLD"
[[ $rc -eq 0 ]] || fail "agreeing build: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "agreeing build: docker was not invoked"
[[ ! -e "$data/dl/bepinex/merge" ]] ||
  fail "agreeing build: signalled a re-merge that was not needed"

# 6b. Overlay is not the install. Repairable, so the world still starts - but
#     only because the re-merge signal the container consumes was written.
build_world newbuild oldbuild newbuild
run_start 0 "$WORLD"
[[ $rc -eq 0 ]] || fail "stale overlay: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "stale overlay: docker was not invoked"
[[ -e "$data/dl/bepinex/merge" ]] ||
  fail "stale overlay: no re-merge signal written, so the old binary would run"

# 6c. Cache older than the install. This is the downgrade setup: the updater
#     rsyncs the cache onto the install with --delete. Refuse, and start nothing.
build_world newbuild newbuild oldbuild
touch -d '2020-01-01' "$data/dl/server/$managed"
run_start 0 "$WORLD"
[[ $rc -ne 0 ]] || fail "older cache: expected non-zero exit, got 0"
[[ ! -s $DOCKER_LOG ]] || fail "older cache: docker was invoked: $(cat "$DOCKER_LOG")"
grep -q 'REFUSING TO START' "$tmp/err" ||
  fail "older cache: stderr does not refuse clearly: $(cat "$tmp/err")"

# 6d. Cache differs from the install and is NEWER: an update Steam has downloaded and the
#     container will rsync onto the install on boot. That start IS the update, and the mod set
#     was anchored to the build the install still carries, so it is refused - this is the hole
#     the anchor exists to close, and until 2026-09-15 this case started the world.
build_world oldbuild oldbuild newbuild
run_start 0 "$WORLD"
[[ $rc -ne 0 ]] || fail "newer cache: expected non-zero exit, got 0"
[[ ! -s $DOCKER_LOG ]] || fail "newer cache: docker was invoked: $(cat "$DOCKER_LOG")"
grep -q '^held=pending_game_update$' "$tmp/valheim/$WORLD/mods/.game-build-hold" ||
  fail "newer cache: hold does not name a pending update: $(cat "$tmp/valheim/$WORLD/mods/.game-build-hold")"

# 6e. The mirror image: the cache holds the ANCHORED build and the install does not. The boot
#     rsyncs the cache onto the install, so the anchored build is what ends up executing, and
#     refusing here would make a pending update unreleasable - the install only changes on boot.
build_world oldbuild oldbuild newbuild
bash "$tmp/hostops/anchor_game_build.sh" "$WORLD" --from-cache --reason operator-approved \
  >/dev/null 2>&1 || fail "cache anchor: anchor_game_build.sh --from-cache failed"
run_start 0 "$WORLD"
[[ $rc -eq 0 ]] || fail "cache anchor: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "cache anchor: docker was not invoked"

rm -rf -- "$data" "$tmp/valheim/$WORLD/mods"

rm -f -- "$tmp/valheim/$WORLD/valheim.env"
touch "$tmp/valheim/$WORLD/valheim.env"

echo "PASS: start_valheim_server.sh release gate"
