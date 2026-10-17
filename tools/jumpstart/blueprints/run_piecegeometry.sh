#!/usr/bin/env bash
# Build and run PieceGeometry in a SANDBOX COPY of the Valheim dedicated server.
#
# Same shape as run_patchscan.sh: rsync the server tree (minus the live BepInEx
# directory) into scratch, drop in a BepInEx core plus this plugin (and, when
# MODS is set, the named CONTENT MODS), run it on a throwaway world and port,
# kill it. No live mod, config, world or profile is written, and the live
# server is never touched.
#
# Unlike PatchScan this needs a LOADED WORLD, because ZNetScene only publishes
# `m_prefabs` once the game scene is up. Expect ~30-60 s.
#
# MODS IS NOT OPTIONAL WHEN THE WORLD IS MODDED, and leaving it empty was a
# real defect with a live verdict on it. A vanilla-only sandbox publishes a
# vanilla-only `m_prefabs`, so the corpus built from it silently contained
# 4,644 prefabs and NONE of the 7 modded hull classes this world berths. The
# harbour gate then could not judge the air draft of a 9.16 m CargoShip under
# a pier deck and REFUSED instead - correctly, but for want of a measurement
# that was one rsync away. An absent prefab is indistinguishable from a prefab
# with no collider unless the dump says which mods were loaded, so MODS is
# echoed into the output header and carried into the corpus metadata.
#
# Usage:
#   VH_SRC=/path/to/server/install OUT=/tmp/piece_geometry.tsv \
#   [VH_CONFIG=/path/to/host/config] \
#   [MODS=Jotunn,JsonDotNET,OdinShip] [SANDBOX=/tmp/piecegeom/vh] [PORT=3459] \
#   tools/jumpstart/blueprints/run_piecegeometry.sh
#
# MODS is a comma list of entries under $VH_SRC/BepInEx/plugins - a directory
# name or a loose .dll name. Each is copied VERBATIM into the sandbox's own
# plugins directory; a named entry that does not exist is a hard failure, never
# a silent omission, because a dump missing a mod looks exactly like a mod with
# nothing in it.
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
SANDBOX="${SANDBOX:-/tmp/piecegeom/vh}"
PORT="${PORT:-3459}"
MODS="${MODS:-}"

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

# --- CONTENT MODS, staged by name, verified one by one ----------------------
# MODS=ALL stages the whole live plugins directory and effective configuration,
# except operational RCON. A hand-picked subset answers a different question:
# OdinShip alone would leave other mods' standing corridor objects unmeasured.
staged=""
if [ "$MODS" = "ALL" ]; then
  [ -d "$VH_SRC/BepInEx/plugins" ] || { echo "MODS=ALL but no $VH_SRC/BepInEx/plugins" >&2; exit 1; }
  cp -a "$VH_SRC/BepInEx/plugins/." "$SANDBOX/BepInEx/plugins/"
  [ -d "$VH_SRC/BepInEx/patchers" ] && cp -a "$VH_SRC/BepInEx/patchers/." "$SANDBOX/BepInEx/patchers/"
  staged=" ALL ($(ls "$VH_SRC/BepInEx/plugins" | wc -l) entries)"
  echo "staged content mods:$staged" >&2
elif [ -n "$MODS" ]; then
  IFS=',' read -r -a _mods <<<"$MODS"
  for m in "${_mods[@]}"; do
    m="$(echo "$m" | xargs)"
    [ -n "$m" ] || continue
    src="$VH_SRC/BepInEx/plugins/$m"
    if [ -d "$src" ]; then
      cp -a "$src" "$SANDBOX/BepInEx/plugins/$m"
    elif [ -f "$src" ]; then
      cp -a "$src" "$SANDBOX/BepInEx/plugins/"
    else
      echo "MODS names '$m' but there is no $src - refusing to run a dump that would be missing a mod without saying so" >&2
      exit 1
    fi
    staged="$staged $m"
  done
  echo "staged content mods:$staged" >&2
fi
if [ -n "$MODS" ]; then
  # Plugin IDs need not match package names. Never write through config links.
  rsync -aL --exclude 'org.tristan.rcon.cfg' "$VH_CONFIG/" "$SANDBOX/BepInEx/config/"
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
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/PieceGeometry.dll" \
  "$HERE/PieceGeometry.cs"

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
  PIECEGEOM_OUT="$OUT" \
  PIECEGEOM_TIMEOUT="${PIECEGEOM_TIMEOUT:-600}" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name piecegeom -port "$PORT" -world PieceGeomScratch -password piecegeom1 \
    -savedir "$SANDBOX/scratch-save" >&2
rc=$?
set -e

# The plugin SIGKILLs the process once the output is flushed, so exit status is
# always 137 on success. Validate by output, like run_patchscan.sh does.
[ -s "$OUT" ] || { echo "FAILED: no geometry output at $OUT (raw_exit=$rc)" >&2; exit 1; }
# WHICH REGISTRY THIS DUMP IS OF, written beside it. A corpus that does not say
# which mods were loaded cannot tell "this prefab has no collider" from "this
# prefab was never in the scene", and that is the exact ambiguity that let a
# vanilla-only corpus be read as a complete one.
{
  echo "# staged plugins for $OUT"
  echo "MODS=$MODS"
  printf 'VH_CONFIG=%s\n' "$VH_CONFIG"
  if [ -n "$MODS" ]; then
    printf 'config_policy\twhole directory, symlinks dereferenced\n'
    printf 'config_excluded\torg.tristan.rcon.cfg\toperational RCON isolated; default empty password disables listener\n'
  else
    printf 'config_policy\tnot staged (MODS empty)\n'
  fi
  ls -1 "$SANDBOX/BepInEx/plugins" | sed 's/^/plugin\t/'
} >"$OUT.mods"
echo "piecegeom ok: $OUT ($(wc -l <"$OUT") rows, raw_exit=$rc); mod manifest $OUT.mods" >&2
