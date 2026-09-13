#!/usr/bin/env bash
set -euo pipefail

WORLD_NAME=${1:?world name required}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if [[ ! "$WORLD_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]]; then
    echo "invalid world name" >&2
    exit 2
fi

require_valheim_root
require_portal_tools
WORLD_DIR="$VALHEIM_ROOT/$WORLD_NAME"
OUTPUT_ROOT=${PORTAL_MAP_SOURCE_ROOT:-$WORLD_DIR/map_sources}
SERVER_ROOT="$WORLD_DIR/data/bepinex"
SOURCE_FILE="$PORTAL_TOOLS_DIR/map-source-exporter/MapSourceExporter.cs"

if [[ ! -f "$SERVER_ROOT/valheim_server.x86_64" || ! -f "$SOURCE_FILE" ]]; then
    echo "Valheim runtime or map exporter source is unavailable" >&2
    exit 1
fi

shopt -s nullglob
backups=("$VALHEIM_BACKUP_ROOT/world-$WORLD_NAME-"*.tgz)
if ((${#backups[@]} == 0)); then
    echo "no immutable backup is available for $WORLD_NAME" >&2
    exit 1
fi
backup=${backups[0]}
for candidate in "${backups[@]:1}"; do
    if [[ "$candidate" -nt "$backup" ]]; then
        backup=$candidate
    fi
done

work=$(mktemp -d "/tmp/valheim-map-export-$WORLD_NAME-XXXXXXXX")
server_pid=
cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
        kill -TERM -- "-$server_pid" 2>/dev/null || true
        for _ in {1..10}; do
            kill -0 "$server_pid" 2>/dev/null || break
            sleep 1
        done
        kill -KILL -- "-$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
runtime="$work/runtime"
saves="$work/saves"
exported="$work/exported"
mkdir -p "$runtime/BepInEx/plugins" "$runtime/BepInEx/config" "$runtime/BepInEx/cache" "$saves/worlds_local" "$exported"

managed="$SERVER_ROOT/valheim_server_Data/Managed"
mcs -nostdlib -target:library -out:"$runtime/BepInEx/plugins/MapSourceExporter.dll" \
    -r:"$managed/mscorlib.dll" \
    -r:"$managed/netstandard.dll" \
    -r:"$managed/System.dll" \
    -r:"$managed/System.Core.dll" \
    -r:"$managed/System.IO.Compression.dll" \
    -r:"$managed/UnityEngine.dll" \
    -r:"$SERVER_ROOT/BepInEx/core/BepInEx.dll" \
    -r:"$SERVER_ROOT/BepInEx/core/0Harmony.dll" \
    -r:"$managed/assembly_valheim.dll" \
    -r:"$managed/assembly_utils.dll" \
    -r:"$managed/UnityEngine.CoreModule.dll" \
    -r:"$managed/UnityEngine.ImageConversionModule.dll" \
    "$SOURCE_FILE"

ln -s "$SERVER_ROOT/BepInEx/core" "$runtime/BepInEx/core"
if [[ -d "$SERVER_ROOT/BepInEx/patchers" ]]; then
    ln -s "$SERVER_ROOT/BepInEx/patchers" "$runtime/BepInEx/patchers"
fi
for entry in valheim_server.x86_64 valheim_server_Data UnityPlayer.so linux64 doorstop_libs steamclient.so libsteamwebrtc.so steam_appid.txt; do
    if [[ -e "$SERVER_ROOT/$entry" ]]; then
        ln -s "$SERVER_ROOT/$entry" "$runtime/$entry"
    fi
done

# Stage the world the exporter will render. Valheim 1.0.12 replaced worlds_local/<World>.db plus
# <World>.fwl with a worlds_local/<World>/ directory, so a 1.0 backup carries no .db and no .fwl at
# all and the old two-member extraction failed with "backup does not contain one <World> world
# save". Both shapes are staged here, and the shape is decided by what the archive holds rather than
# by a version guess: only 1.0 has a .fwl2. The whole archive is extracted because a world backup
# holds exactly one world and a 1.0 directory has no fixed member list - the save generation number
# and the per-chunk file names both vary.
archive="$work/archive"
mkdir -p "$archive"
tar -xzf "$backup" -C "$archive"
# Real 1.0 world directories on this host carry mode 0664 - rw-rw-r-- on a DIRECTORY, with no
# execute bit - because valheim-server-docker's ensure_permissions applied a file mode to them on
# every container start. tar restores that mode faithfully, and the result is a directory its own
# owner cannot traverse: extracting the Ulfsland backup and copying it out of the archive failed
# with "cp: cannot stat .../_main.2.db2: Permission denied". The staging tree is this script's own
# throwaway, so the execute bit is put back before anything reads it; u+rwX only adds it to
# directories.
chmod -R u+rwX -- "$archive"
shopt -s globstar
metadata=("$archive"/**/*.fwl2)
if ((${#metadata[@]} > 0)); then
    # The directory holding the .fwl2 IS the world. It is staged under $WORLD_NAME because that is
    # the name the server is launched with, and the backup's own stem may differ in case.
    #
    # Every .fwl2 has to share one parent: a world directory holds one save generation per world,
    # so two parents means two worlds and there is no right answer about which to render. The
    # extraction deliberately lands outside worlds_local and only the finished directory is placed
    # in it, because a 1.0 server hangs in the start scene on a directory there it cannot read.
    world_dir=$(dirname "${metadata[0]}")
    for candidate in "${metadata[@]:1}"; do
        if [[ $(dirname "$candidate") != "$world_dir" ]]; then
            echo "backup holds more than one world directory" >&2
            exit 1
        fi
    done
    cp -r -- "$world_dir" "$saves/worlds_local/$WORLD_NAME"
else
    fwl=("$archive"/**/"$WORLD_NAME.fwl")
    db=("$archive"/**/"$WORLD_NAME.db")
    if ((${#fwl[@]} != 1 || ${#db[@]} != 1)); then
        echo "backup does not contain one $WORLD_NAME world save" >&2
        exit 1
    fi
    cp -- "${fwl[0]}" "$saves/worlds_local/$WORLD_NAME.fwl"
    cp -- "${db[0]}" "$saves/worlds_local/$WORLD_NAME.db"
fi

port=$((30000 + RANDOM % 25000))
log="$work/server.log"
set +e
(
    cd "$runtime"
    export SteamAppId=892970
    export DOORSTOP_ENABLED=1
    export DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll
    export LD_LIBRARY_PATH="$runtime/doorstop_libs:$runtime/linux64"
    export VALHEIM_MAP_EXPORT_DIR="$exported"
    exec setsid timeout --signal=TERM --kill-after=30s 30m env LD_PRELOAD=libdoorstop_x64.so \
        ./valheim_server.x86_64 -nographics -batchmode -name "Map export $WORLD_NAME" \
        -port "$port" -world "$WORLD_NAME" -savedir "$saves" -public 0 -password map-export-only
) >"$log" 2>&1 &
server_pid=$!
status=124
for _ in {1..1740}; do
    if [[ -f "$exported/complete" && -s "$exported/biome.png" && -s "$exported/height.png" ]]; then
        status=0
        break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
        wait "$server_pid"
        status=$?
        server_pid=
        break
    fi
    sleep 1
done
if [[ -n "$server_pid" ]]; then
    kill -TERM -- "-$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=
fi
set -e
if ((status != 0)) || [[ ! -f "$exported/complete" || ! -s "$exported/biome.png" || ! -s "$exported/height.png" ]]; then
    cp "$log" "/tmp/valheim-map-export-$WORLD_NAME-failed.log" 2>/dev/null || true
    tail -n 80 "$log" >&2
    echo "isolated map export failed for $WORLD_NAME" >&2
    exit 1
fi

mkdir -p "$OUTPUT_ROOT/objects"
identity=$(sha256sum "$exported/biome.png" "$exported/height.png" | sha256sum)
identity=${identity%% *}
object="$OUTPUT_ROOT/objects/$identity"
if [[ ! -d "$object" ]]; then
    mv "$exported" "$object"
fi
link="$OUTPUT_ROOT/current.new"
ln -sfn "objects/$identity" "$link"
mv -Tf "$link" "$OUTPUT_ROOT/current"
echo "map_source=$OUTPUT_ROOT/current"
echo "backup=$(basename "$backup")"
