#!/usr/bin/env bash
set -euo pipefail

# Records - or shows - the game build one world's deployed mod set runs on.
#
#   anchor_game_build.sh WORLD [--reason TEXT] [--from-cache]
#   anchor_game_build.sh WORLD --show
#
# --from-cache anchors the build sitting in the Steam download cache rather than the one on the
# install. Use it for a pending update: the cache is what the container rsyncs onto the install on
# boot, so when a new build has landed there and not yet on the install, the cache is the build the
# mod set has to be validated against - and the install cannot become that build until a boot the
# gate is refusing.
#
# This is the human decision the start gate refuses without. `require_matching_game_build`
# (hostops/lib/common.sh) compares the installed game assembly against the anchor this
# writes, and when they differ it refuses to start the world and records why in
# <world>/mods/.game-build-hold. Recording a new anchor is what releases that hold, so
# run this only after the mod set has actually been re-validated against the new build -
# normally `./hostops/manage_mods.sh WORLD deploy --apply` first, this second.
#
# It does not stop, start or restart anything, and it does not look at a container.
#
# THIS COMMAND IS DELIBERATELY NOT REGISTERED IN policy.yaml, AND ITS ABSENCE THERE IS NOT AN
# OVERSIGHT. Operator and root only. Releasing a hold is the single human decision this whole
# gate exists to force, so if the portal agent could call it, the failure mode would come back
# wearing a new costume: an automated caller deciding at 3am that a game update is fine - which
# is exactly what valheim_hang_watchdog.sh's bare `docker restart` was doing until
# 2026-09-15. A gate whose release is automatable is a log line, not a gate. Do not add a verb
# for this; if an agent needs a world started after a Steam update, a human anchors it first.
#
# The deploy path is the one exception, and it is not an exception to the rule above: it calls
# this with --reason deploy at the moment a mod set has actually been re-staged against the
# installed build (tools/valheim_mods.py record_deploy_game_build_anchor), which is a human
# running a deploy, not a timer deciding on its own.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WORLD=${1:-}
[[ $WORLD =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || {
	echo "usage: anchor_game_build.sh WORLD [--reason TEXT] [--from-cache | --show]" >&2
	exit 2
}
shift

REASON=operator-approved
SHOW=0
FROM=install
while [[ $# -gt 0 ]]; do
	case "$1" in
	--reason)
		REASON=${2:?"--reason needs a value"}
		shift 2
		;;
	--from-cache)
		FROM=cache
		shift
		;;
	--show)
		SHOW=1
		shift
		;;
	*)
		echo "unknown argument: $1" >&2
		exit 2
		;;
	esac
done

require_valheim_root
[[ -d "$VALHEIM_ROOT/$WORLD" ]] || {
	echo "no such world: $VALHEIM_ROOT/$WORLD" >&2
	exit 2
}

ANCHOR=$(game_build_anchor_path "$WORLD")
HOLD=$(game_build_hold_path "$WORLD")

if ((SHOW)); then
	if [[ -f $ANCHOR ]]; then
		echo "anchor $ANCHOR"
		cat -- "$ANCHOR"
	else
		echo "anchor $ANCHOR (absent - the next start will record the installed build)"
	fi
	if [[ -f $HOLD ]]; then
		echo
		echo "HELD - this world is deliberately not running:"
		cat -- "$HOLD"
	fi
	# What is installed right now, so --show answers the comparison and not just half of it.
	DATA_DIR=$(world_env_dir "$WORLD" DATA_DIR data)
	CONFIG_DIR=$(world_env_dir "$WORLD" CONFIG_DIR config_merged)
	INSTALL="$DATA_DIR/server/$VALHEIM_GAME_ASSEMBLY"
	echo
	if [[ -f $INSTALL ]]; then
		echo "installed game_assembly_sha256=$(sha256sum -- "$INSTALL" | cut -d' ' -f1)"
	else
		echo "installed game_assembly_sha256=absent ($INSTALL)"
	fi
	CACHE="$DATA_DIR/dl/server/$VALHEIM_GAME_ASSEMBLY"
	if [[ -f $CACHE ]]; then
		echo "cache     game_assembly_sha256=$(sha256sum -- "$CACHE" | cut -d' ' -f1)"
	else
		echo "cache     game_assembly_sha256=absent ($CACHE)"
	fi
	echo "cache     steam_cache_buildid=$(game_cache_buildid "$DATA_DIR")"
	read -r FP COUNT <<<"$(mod_set_fingerprint "$CONFIG_DIR")"
	echo "deployed mod_set_fingerprint=$FP"
	echo "deployed mod_set_dll_count=$COUNT"
	exit 0
fi

record_game_build_anchor "$WORLD" "$REASON" "$FROM"
