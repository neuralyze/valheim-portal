#!/usr/bin/env bash
# Proves the deployed mod set is anchored to a game build, and that every host path which can
# start a world honours that anchor.
#
# Three things are asserted, because three separate routes could change the game binary under a
# deployed mod set with no human present:
#
#   1. valheim_hang_watchdog.sh --restart. A bare `docker restart` runs the in-container
#      valheim-updater, which does `steamcmd +app_update 896660` and rsyncs the download cache
#      onto the install with --delete, so on a two-minute timer it is an unattended game update.
#      It must consult the gate, and it must still restart a hung world when the build matches.
#   2. start_valheim_server.sh. An installed build that is not the anchored one must be refused,
#      and a matching build and mod set must start normally.
#   3. watch_valheim_servers.sh. A world already running a drifted build must not be restarted
#      into the mismatch, and the operator must be able to tell that from a crash.
#
# Run: bash hostops/tests/game_build_anchor_gate.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# The override exists so this file can be run against a pre-change copy of hostops/ to show it
# fails there. Nothing in production sets it.
HOSTOPS=${VALHEIM_HOSTOPS_SOURCE:-$SCRIPT_DIR/..}
WORLD=Anchorholm

tmp=$(mktemp -d /tmp/game-build-anchor.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$tmp/hostops/lib" "$tmp/bin" "$tmp/state" "$tmp/logs" \
	"$tmp/valheim-server-docker" "$tmp/valheim/$WORLD"
for script in start_valheim_server.sh valheim_hang_watchdog.sh watch_valheim_servers.sh \
	anchor_game_build.sh; do
	cp "$HOSTOPS/$script" "$tmp/hostops/" 2>/dev/null ||
		fail "$script is missing from $HOSTOPS - nothing to test"
done
cp "$HOSTOPS/lib/common.sh" "$tmp/hostops/lib/"
touch "$tmp/valheim-server-docker/docker-compose.yaml"
printf "DATA_DIR='%s'\nCONFIG_DIR='%s'\n" \
	"$tmp/valheim/$WORLD/data" "$tmp/valheim/$WORLD/config_merged" \
	>"$tmp/valheim/$WORLD/valheim.env"

# Stub docker: records its argv and answers the few queries these scripts make.
cat >"$tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
case "$1" in
inspect)
	case "$*" in
	*State.Running*) echo true ;;
	*State.StartedAt*) echo "$DOCKER_STARTED_AT" ;;
	esac
	;;
logs) printf '%s a routine line\n' "$DOCKER_LAST_LOG_TS" ;;
ps) printf '%s\n' "${DOCKER_PS_OUTPUT:-}" ;;
esac
exit 0
EOF
chmod +x "$tmp/bin/docker"

# Stub sudo: watch_valheim_servers.sh restarts through it, so recording its argv is how we prove
# a held world was not stopped or started.
cat >"$tmp/bin/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SUDO_LOG"
exit 0
EOF
chmod +x "$tmp/bin/sudo"

# Stub release gate: always clear, so the only thing under test here is the build anchor.
cat >"$tmp/hostops/manage_mods.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$tmp/hostops/manage_mods.sh"

# Stub hang capture: reports a wedged main loop, which is the verdict that permits a restart.
cat >"$tmp/hostops/capture_valheim_hang.sh" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$CAPTURE_BUNDLE_DIR"
printf 'verdict=MAIN_LOOP_STOPPED\n' >"$CAPTURE_BUNDLE_DIR/hang-context.txt"
echo "Captured hang bundle: $CAPTURE_BUNDLE_DIR"
EOF
chmod +x "$tmp/hostops/capture_valheim_hang.sh"

export PATH="$tmp/bin:$PATH"
export DOCKER_LOG="$tmp/docker.log"
export SUDO_LOG="$tmp/sudo.log"
export VALHEIM_ROOT="$tmp/valheim"
export VALHEIM_SERVER_DOCKER_DIR="$tmp/valheim-server-docker"
export VALHEIM_HANG_STATE_DIR="$tmp/state"
export CAPTURE_BUNDLE_DIR="$tmp/bundle"
: >"$DOCKER_LOG"
: >"$SUDO_LOG"

managed=valheim_server_Data/Managed/assembly_valheim.dll
data="$tmp/valheim/$WORLD/data"
config="$tmp/valheim/$WORLD/config_merged"
mods="$tmp/valheim/$WORLD/mods"
anchor="$mods/.game-build-anchor"
hold="$mods/.game-build-hold"

# build_world INSTALL OVERLAY CACHE [BUILDID] - the three copies of the game, by content, plus the
# Steam manifest that only ever exists under the download cache (measured: the updater's rsync
# runs with --exclude steamapps, so the install has no manifest at all).
build_world() {
	rm -rf -- "$data"
	local spec dir body
	for spec in "server:$1" "bepinex:$2" "dl/server:$3"; do
		dir=${spec%%:*}
		body=${spec#*:}
		mkdir -p -- "$data/$dir/${managed%/*}"
		printf '%s' "$body" >"$data/$dir/$managed"
	done
	mkdir -p -- "$data/dl/server/steamapps"
	printf '\t"buildid"\t\t"%s"\n' "${4:-25253791}" \
		>"$data/dl/server/steamapps/appmanifest_896660.acf"
}

# deploy_mods NAME... - the deployed mod set, as cmd_deploy leaves it.
deploy_mods() {
	rm -rf -- "$config/bepinex/plugins"
	mkdir -p -- "$config/bepinex/plugins"
	local name
	for name in "$@"; do
		printf 'plugin %s' "$name" >"$config/bepinex/plugins/$name.dll"
	done
}

run_start() {
	: >"$DOCKER_LOG"
	rc=0
	bash "$tmp/hostops/start_valheim_server.sh" "$WORLD" >"$tmp/out" 2>"$tmp/err" || rc=$?
}

# run_hang SILENCE_AGE - the hang watchdog on a container that has been silent that long. The
# timestamp differs per call because the watchdog captures once per silent episode.
run_hang() {
	: >"$DOCKER_LOG"
	rm -rf -- "$CAPTURE_BUNDLE_DIR"
	DOCKER_STARTED_AT=$(date -d "-$1" --iso-8601=seconds) \
		DOCKER_LAST_LOG_TS=$(date -d "-$1" --iso-8601=seconds)
	export DOCKER_STARTED_AT DOCKER_LAST_LOG_TS
	rc=0
	bash "$tmp/hostops/valheim_hang_watchdog.sh" "$WORLD" --restart \
		>"$tmp/out" 2>"$tmp/err" || rc=$?
	hanglog=$(cat "$tmp/state/hang-watchdog.log" 2>/dev/null || true)
}

# 1. First observation. Five worlds are deployed today with no anchor at all, so the gate records
#    what is installed rather than refusing the fleet. It must record a fact and say so.
build_world same same same
deploy_mods AzuAntiArthriticCrafting ServersideQoL
run_start
[[ $rc -eq 0 ]] || fail "first observation: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -f $anchor ]] || fail "first observation: no anchor written at $anchor"
[[ -s $DOCKER_LOG ]] || fail "first observation: docker was not invoked"
want_sum=$(printf 'same' | sha256sum | cut -d' ' -f1)
got_sum=$(sed -n 's/^game_assembly_sha256=//p' "$anchor")
[[ $got_sum == "$want_sum" ]] || fail "first observation: anchored $got_sum, want $want_sum"
grep -q '^game_buildid=25253791$' "$anchor" ||
	fail "first observation: buildid not recorded: $(cat "$anchor")"
grep -q '^mod_set_dll_count=2$' "$anchor" ||
	fail "first observation: mod set not recorded: $(cat "$anchor")"
grep -q 'does not' "$tmp/err" ||
	fail "first observation: stderr does not say the record is not a certification: $(cat "$tmp/err")"

# 2. Matching build and matching mod set: start normally, no hold, no noise about the mod set.
run_start
[[ $rc -eq 0 ]] || fail "matching build: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "matching build: docker was not invoked"
[[ ! -e $hold ]] || fail "matching build: a hold was recorded: $(cat "$hold")"
grep -q 'not the one the game-build anchor describes' "$tmp/err" &&
	fail "matching build: reported mod-set drift that does not exist"

# 3. The installed build is not the anchored one - a Steam update landed. All three copies agree
#    with each other, which is exactly why the old three-copy comparison passed this case.
build_world newbuild newbuild newbuild 25260001
run_start
[[ $rc -ne 0 ]] || fail "changed build: expected non-zero exit, got 0"
[[ ! -s $DOCKER_LOG ]] || fail "changed build: docker was invoked: $(cat "$DOCKER_LOG")"
grep -q 'REFUSING TO START' "$tmp/err" ||
	fail "changed build: stderr does not refuse clearly: $(cat "$tmp/err")"
[[ -f $hold ]] || fail "changed build: no hold recorded at $hold"
grep -q '^held=game_build_changed$' "$hold" || fail "changed build: hold reason wrong: $(cat "$hold")"
grep -q '^release=' "$hold" || fail "changed build: hold does not say how to release it"
grep -q 'deliberately DOWN, not broken' "$tmp/err" ||
	fail "changed build: refusal does not distinguish itself from a crash: $(cat "$tmp/err")"
grep -q "^anchored_game_assembly_sha256=$want_sum$" "$hold" ||
	fail "changed build: hold does not name the anchored build: $(cat "$hold")"

# 4. The install still matches, but the Steam cache holds a different build. The container rsyncs
#    the cache onto the install on boot, so this start IS the update - refuse it.
build_world same same newbuild 25260001
touch "$data/server/$managed"
touch -d '+1 minute' "$data/dl/server/$managed" 2>/dev/null ||
	touch "$data/dl/server/$managed"
run_start
[[ $rc -ne 0 ]] || fail "pending update: expected non-zero exit, got 0"
[[ ! -s $DOCKER_LOG ]] || fail "pending update: docker was invoked: $(cat "$DOCKER_LOG")"
grep -q '^held=pending_game_update$' "$hold" || fail "pending update: hold reason wrong: $(cat "$hold")"

# 4b. The hold must be releasable. Anchoring reads the install, and the install only changes on
#     boot - the very thing being refused - so a pending update can only be approved by anchoring
#     the build sitting in the cache. After that the same world starts, and it starts because the
#     boot will rsync the anchored build onto the install.
bash "$tmp/hostops/anchor_game_build.sh" "$WORLD" --from-cache --reason operator-approved \
	>"$tmp/out" 2>"$tmp/err" || fail "release from cache: anchoring failed: $(cat "$tmp/err")"
[[ ! -e $hold ]] || fail "release from cache: hold not released: $(cat "$hold")"
grep -q '^game_assembly_source=cache$' "$anchor" ||
	fail "release from cache: anchor does not record which copy it read: $(cat "$anchor")"
run_start
[[ $rc -eq 0 ]] || fail "release from cache: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "release from cache: docker was not invoked"
grep -q 'the Steam cache is' "$tmp/err" ||
	fail "release from cache: stderr does not explain why a differing install is allowed: $(cat "$tmp/err")"

# 5. The update the operator approved in 4b lands: the boot rsynced the cache onto the install,
#    so the install is now the anchored build. The world starts and the hold stays gone, so a
#    hold always means "held now" rather than "was held once".
build_world newbuild newbuild newbuild 25260001
run_start
[[ $rc -eq 0 ]] || fail "update landed: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ ! -e $hold ]] || fail "update landed: hold not cleared: $(cat "$hold")"

# 6. The mod set changed but the binary did not. Reported, never refused: a changed plugin does
#    not overwrite a world.
deploy_mods AzuAntiArthriticCrafting ServersideQoL CreatureLevelControl
run_start
[[ $rc -eq 0 ]] || fail "mod-set drift: expected exit 0, got $rc -- $(cat "$tmp/err")"
[[ -s $DOCKER_LOG ]] || fail "mod-set drift: docker was not invoked"
[[ ! -e $hold ]] || fail "mod-set drift: refused a mod-set change: $(cat "$hold")"
grep -q 'not the one the game-build anchor describes' "$tmp/err" ||
	fail "mod-set drift: stderr does not report it: $(cat "$tmp/err")"

# 7. An anchor that cannot be read certifies nothing, and a human wrote it. Refuse.
printf 'anchor_version=1\nworld=%s\n' "$WORLD" >"$anchor"
run_start
[[ $rc -ne 0 ]] || fail "unreadable anchor: expected non-zero exit, got 0"
grep -q '^held=anchor_unreadable$' "$hold" || fail "unreadable anchor: hold reason wrong: $(cat "$hold")"

# Re-anchor through the operator command, which is also what releases a hold.
bash "$tmp/hostops/anchor_game_build.sh" "$WORLD" --reason operator-approved >"$tmp/out" 2>"$tmp/err" ||
	fail "anchor_game_build.sh failed: $(cat "$tmp/err")"
[[ ! -e $hold ]] || fail "anchor_game_build.sh did not release the hold"
want_new=$(printf 'newbuild' | sha256sum | cut -d' ' -f1)
grep -q "^game_assembly_sha256=$want_new$" "$anchor" ||
	fail "anchor_game_build.sh recorded the wrong build: $(cat "$anchor")"
grep -q '^recorded_reason=operator-approved$' "$anchor" ||
	fail "anchor_game_build.sh did not record the reason: $(cat "$anchor")"

# 8. The bare-restart route. The hang watchdog must not restart a container when doing so would
#    change the game build, because a container start applies whatever the Steam cache holds.
build_world nextbuild nextbuild nextbuild 25270002
run_hang '2 hours'
[[ $rc -ne 0 ]] || fail "hang restart with changed build: expected non-zero exit, got 0"
grep -q '^restart ' "$DOCKER_LOG" &&
	fail "hang restart with changed build: docker restart ran anyway: $(cat "$DOCKER_LOG")"
printf '%s' "$hanglog" | grep -q 'NOT restarting' ||
	fail "hang restart with changed build: watchdog log does not say it refused: $hanglog"
[[ -f $hold ]] || fail "hang restart with changed build: no hold recorded"
grep -q '^held=game_build_changed$' "$hold" ||
	fail "hang restart with changed build: hold reason wrong: $(cat "$hold")"

# 9. Recovery still works. The whole point of this watchdog is that a hung world can be recovered,
#    and tonight produced four main-thread freezes that needed exactly that. With the anchored
#    build installed, the restart must happen.
build_world newbuild newbuild newbuild 25260001
run_hang '3 hours'
[[ $rc -eq 0 ]] || fail "hang restart with matching build: expected exit 0, got $rc -- $(cat "$tmp/err")"
grep -q "^restart valheim-server-$WORLD$" "$DOCKER_LOG" ||
	fail "hang restart with matching build: no restart issued: $(cat "$DOCKER_LOG")"
[[ ! -e $hold ]] || fail "hang restart with matching build: recorded a hold: $(cat "$hold")"

# 10. The five-minute watchdog must not restart a world whose build drifted under it, and the
#     operator has to be able to tell that from a crash.
build_world nextbuild nextbuild nextbuild 25270002
: >"$DOCKER_LOG"
: >"$SUDO_LOG"
rc=0
VALHEIM_HOSTOPS_DIR="$tmp/hostops" \
	VALHEIM_LOG_ROOT="$tmp/logs" \
	VALHEIM_WATCHDOG_STATE="$tmp/state" \
	DOCKER_PS_OUTPUT="valheim-server-$WORLD" \
	bash "$tmp/hostops/watch_valheim_servers.sh" >"$tmp/out" 2>"$tmp/err" || rc=$?
[[ $rc -eq 0 ]] || fail "watchdog with drifted build: exit $rc -- $(cat "$tmp/err")"
grep -q 'BUILD HOLD' "$tmp/out" ||
	fail "watchdog with drifted build: output does not report the hold: $(cat "$tmp/out")"
[[ ! -s $SUDO_LOG ]] ||
	fail "watchdog with drifted build: it stopped or started the world: $(cat "$SUDO_LOG")"
grep -q '^held=game_build_changed$' "$hold" ||
	fail "watchdog with drifted build: hold reason wrong: $(cat "$hold")"

echo "PASS: game build anchor gate"
