#!/usr/bin/env bash
# Builds NeuralyzeWorldSeed.dll against the Valheim build it will actually run on.
#
# Usage:
#   ./build.sh WORLD              references from $VALHEIM_ROOT/WORLD/data/bepinex
#   ./build.sh /path/to/bepinex   references from an install directory
#   ./build.sh --refs DIR         references from DIR, laid out or flat
#   ./build.sh CONTAINER          last resort: docker cp out of a RUNNING container
#
# On-disk references come FIRST deliberately, and the container is only the fallback.
# tools/valheim_worldgen.py stops the server before it needs this plugin, and this
# script could previously only read a running container - so the one moment a re-roll
# needed the DLL was exactly the moment the build could not produce it, which on
# 2026-09-14 meant a re-roll refused on a fresh deployment. The same six assemblies sit
# under <world>/data/bepinex on disk whether the container runs or not, and that is the
# copy that executes: the process is /opt/valheim/bepinex/valheim_server.x86_64, which
# is that directory mounted into the container.
#
# A container name is still accepted, and `valheim-server-<World>` is resolved on disk
# first, so the original invocation now builds without a container too.
set -euo pipefail

OUT=$(cd "$(dirname "$0")" && pwd)

usage() {
	sed -n '4,8p' "$0" | sed 's/^# \{0,1\}//'
}

REFS_DIR=""
TARGET=""
while (($#)); do
	case $1 in
	--refs)
		REFS_DIR=${2:-}
		[[ -n $REFS_DIR ]] || { echo "--refs needs a directory" >&2; exit 2; }
		shift 2
		;;
	--refs=*) REFS_DIR=${1#*=}; shift ;;
	-h | --help) usage; exit 0 ;;
	-*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	*)
		[[ -z $TARGET ]] || { echo "one target only: $TARGET and $1" >&2; exit 2; }
		TARGET=$1
		shift
		;;
	esac
done

# The six assemblies WorldSeed.cs compiles against, at their paths inside a BepInEx
# install. A flat directory is accepted too, so a hand-assembled --refs works.
ASSEMBLIES=(
	valheim_server_Data/Managed/assembly_valheim.dll
	valheim_server_Data/Managed/UnityEngine.dll
	valheim_server_Data/Managed/UnityEngine.CoreModule.dll
	valheim_server_Data/Managed/netstandard.dll
	BepInEx/core/0Harmony.dll
	BepInEx/core/BepInEx.dll
)

# collect_refs ROOT prints one absolute path per assembly and returns 0 only when ALL
# six were found. All or nothing: a partial reference set fails in mcs with a type error
# per missing assembly instead of naming the directory that was wrong.
collect_refs() {
	local root=$1 rel path found=()
	[[ -d $root ]] || return 1
	for rel in "${ASSEMBLIES[@]}"; do
		if [[ -f "$root/$rel" ]]; then
			path="$root/$rel"
		elif [[ -f "$root/${rel##*/}" ]]; then
			path="$root/${rel##*/}"
		else
			return 1
		fi
		found+=("$path")
	done
	printf '%s\n' "${found[@]}"
}

CANDIDATES=()
if [[ -n $REFS_DIR ]]; then
	CANDIDATES=("$REFS_DIR" "$REFS_DIR/data/bepinex" "$REFS_DIR/bepinex")
elif [[ -n $TARGET ]]; then
	# A container name carries the world: valheim-server-<World>.
	WORLD=${TARGET#valheim-server-}
	CANDIDATES=("$TARGET" "$TARGET/data/bepinex" "$TARGET/bepinex")
	# The same three variable names tools/portal_paths.py and hostops/lib/common.sh
	# accept for the world root, so one configured value serves every caller.
	for base in "${VALHEIM_ROOT:-}" "${AGENT_WORLD_ROOT:-}" "${VALHEIM_WORLD_ROOT:-}"; do
		[[ -n $base ]] || continue
		CANDIDATES+=("$base/$WORLD/data/bepinex" "$base/$TARGET/data/bepinex")
	done
else
	echo "a target is required" >&2
	usage >&2
	exit 2
fi

REFS=()
SOURCE=""
for candidate in "${CANDIDATES[@]}"; do
	mapfile -t found < <(collect_refs "$candidate") || true
	if ((${#found[@]} == ${#ASSEMBLIES[@]})); then
		SOURCE=$candidate
		for path in "${found[@]}"; do REFS+=("-r:$path"); done
		break
	fi
done

if ((${#REFS[@]} == 0)); then
	if [[ -n $REFS_DIR ]]; then
		echo "no complete assembly set under $REFS_DIR" >&2
		printf '  expected %s\n' "${ASSEMBLIES[@]}" >&2
		exit 1
	fi
	# Fallback only: this needs the container to be RUNNING, which a re-roll has just
	# made false. It stays because a world whose install is not on this host has no
	# other source for its own assemblies.
	echo "no on-disk assemblies for $TARGET; trying container $TARGET" >&2
	REF=$(mktemp -d)
	trap 'rm -rf "$REF"' EXIT
	for rel in "${ASSEMBLIES[@]}"; do
		docker cp "$TARGET:/opt/valheim/bepinex/$rel" "$REF/" 2>/dev/null || {
			echo "missing $rel in container $TARGET, and no on-disk install was found" >&2
			echo "searched: ${CANDIDATES[*]}" >&2
			echo "set VALHEIM_ROOT, or pass --refs DIR" >&2
			exit 1
		}
	done
	SOURCE="container $TARGET"
	for path in "$REF"/*.dll; do REFS+=("-r:$path"); done
fi

echo "references from $SOURCE"
mcs -target:library -langversion:latest -nologo \
	-out:"$OUT/NeuralyzeWorldSeed.dll" "${REFS[@]}" "$OUT/WorldSeed.cs"
echo "built $OUT/NeuralyzeWorldSeed.dll ($(stat -c%s "$OUT/NeuralyzeWorldSeed.dll") bytes)"
echo "deploy to <world>/config_merged/bepinex/plugins/NeuralyzeWorldSeed/ and set ForcedSeedName"
