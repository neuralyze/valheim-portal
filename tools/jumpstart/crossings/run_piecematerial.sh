#!/usr/bin/env bash
# Build and run PieceMaterial in a SANDBOX COPY of the Valheim dedicated server.
#
# Same shape as tools/jumpstart/blueprints/run_piecegeometry.sh: mirror the
# server tree without live BepInEx, then stage its core and this plugin plus,
# with MODS=ALL, the complete content-mod profile.
# Run on a throwaway world and port, then kill it. No live mod, config, world
# or profile is written, and the live server is never touched.
#
# Needs a LOADED WORLD, because ZNetScene only publishes `m_prefabs` once the
# game scene is up. Expect ~30-60 s.
#
# Usage:
#   VH_SRC=/path/to/server/install OUT=/tmp/piece_material.tsv \
#   [VH_CONFIG=/path/to/host/config] \
#   [SANDBOX=/tmp/piecemat/vh] [PORT=3461] [MODS=ALL] \
#   tools/jumpstart/crossings/run_piecematerial.sh
#
# VH_CONFIG defaults to $VH_SRC/BepInEx/config. Use the readable host directory
# when that path is a container-only symlink. A modded measurement requires it.
# Configuration symlinks are materialized into private sandbox files.
# Operational org.tristan.rcon.cfg is excluded: its empty default password
# disables RCON rather than reusing production listener credentials.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
VH_CONFIG="${VH_CONFIG:-$VH_SRC/BepInEx/config}"
OUT="${OUT:?set OUT to the output TSV path}"
SANDBOX="${SANDBOX:-/tmp/piecemat/vh}"
PORT="${PORT:-3461}"
MODS="${MODS:-}"
case "$MODS" in
  ""|ALL) ;;
  *) echo "MODS must be ALL or empty; partial profiles cannot measure this world's ships" >&2; exit 1 ;;
esac

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -f "$VH_SRC/BepInEx/core/BepInEx.Preloader.dll" ] || { echo "no BepInEx core in $VH_SRC" >&2; exit 1; }
if [ -n "$MODS" ] && { [ ! -d "$VH_CONFIG" ] || [ ! -r "$VH_CONFIG" ] || [ ! -x "$VH_CONFIG" ]; }; then
  echo "MODS is nonempty but VH_CONFIG is not a readable, searchable directory: $VH_CONFIG; set VH_CONFIG to the host config directory (container-only symlinks are not usable); refusing before sandbox staging" >&2
  exit 1
fi

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  echo "staging sandbox copy in $SANDBOX (mirror of $VH_SRC, live BepInEx excluded)" >&2
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
fi

rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
chmod 700 "$SANDBOX/BepInEx"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"

# Match the geometry export's actual prefab registry. A vanilla-only dump
# cannot establish the draught or persistence of OdinShip prefabs.
if [ "$MODS" = "ALL" ]; then
  [ -d "$VH_SRC/BepInEx/plugins" ] || { echo "MODS=ALL but plugins directory is missing" >&2; exit 1; }
  cp -a "$VH_SRC/BepInEx/plugins/." "$SANDBOX/BepInEx/plugins/"
  [ ! -d "$VH_SRC/BepInEx/patchers" ] || cp -a "$VH_SRC/BepInEx/patchers/." "$SANDBOX/BepInEx/patchers/"
  # Plugin IDs need not match package names. Never write through config links.
  rsync -aL --exclude 'org.tristan.rcon.cfg' "$VH_CONFIG/" "$SANDBOX/BepInEx/config/"
  echo "staged complete content-mod profile (operational RCON config excluded)" >&2
fi

MANAGED="$SANDBOX/valheim_server_Data/Managed"
for dll in "$MANAGED/assembly_valheim.dll" "$MANAGED/UnityEngine.CoreModule.dll" \
           "$MANAGED/UnityEngine.PhysicsModule.dll" "$MANAGED/netstandard.dll" \
           "$SANDBOX/BepInEx/core/BepInEx.dll"; do
  [ -f "$dll" ] || { echo "missing $dll" >&2; exit 1; }
done

mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" \
  -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.PhysicsModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$MANAGED/MagicaClothV2.dll" \
  -r:"$MANAGED/SoftReferenceableAssets.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/PieceMaterial.dll" \
  "$HERE/PieceMaterial.cs"

rm -f "$OUT"
mkdir -p "$(dirname "$OUT")" "$SANDBOX/scratch-save"

cd "$SANDBOX"
set +e
env \
  DOORSTOP_ENABLED=1 \
  DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
  LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
  LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
  SteamAppId=892970 \
  PIECEMAT_OUT="$OUT" \
  PIECEMAT_TIMEOUT="${PIECEMAT_TIMEOUT:-600}" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name piecemat -port "$PORT" -world PieceMatScratch -password piecemat1 \
    -savedir "$SANDBOX/scratch-save" >&2
rc=$?
set -e

# The plugin SIGKILLs the process once the output is flushed, so exit status is
# always 137 on success. Validate by output, like run_piecegeometry.sh does.
[ -s "$OUT" ] || { echo "FAILED: no material output at $OUT (raw_exit=$rc)" >&2; exit 1; }
{
  printf '# staged plugins for %s\nMODS=%s\n' "$OUT" "$MODS"
  printf 'VH_CONFIG=%s\n' "$VH_CONFIG"
  if [ -n "$MODS" ]; then
    printf 'config_policy\twhole directory, symlinks dereferenced\n'
    printf 'config_excluded\torg.tristan.rcon.cfg\toperational RCON isolated; default empty password disables listener\n'
  else
    printf 'config_policy\tnot staged (MODS empty)\n'
  fi
  for entry in "$SANDBOX"/BepInEx/plugins/*; do
    [ ! -e "$entry" ] || printf 'plugin\t%s\n' "${entry##*/}"
  done
} >"$OUT.mods"
echo "piecemat ok: $OUT ($(wc -l <"$OUT") rows, raw_exit=$rc); mod manifest $OUT.mods" >&2
