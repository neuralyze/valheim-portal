#!/usr/bin/env bash
# Measure a `Door` prefab's OPEN pose -- the geometry no other instrument here
# can see.
#
# `blueprints/PieceGeometry.cs` dumps a prefab's colliders AS AUTHORED, which
# for a door is the CLOSED pose.  `Door.SetState` (assembly_valheim IL, class
# `Door`) only calls `m_animator.SetInteger("state", s)` and
# `m_openEnable.SetActive(...)`, so the open geometry lives in an animation
# clip and no static dump contains it.  That gap cost this project a wrong
# design: `fb_fortress_portcullis` was chosen as a ship gate on its 5.704 m
# closed envelope, and MEASURED here, only its leaf moves -- the two frame
# posts never do, so the real open aperture is 4.956 m and tiling the panels
# makes it NARROWER, not wider.  See settlements/sea_gate_fix.py.
#
# Sandbox discipline is run_piecegeometry.sh's: a copy of the live tree with a
# fresh BepInEx, a throwaway world/port/savedir, the live install untouched.
# The plugin set matches run_modded_scan.sh, which is what puts the modded
# prefabs in ZNetScene -- with only the vanilla set loaded every `fb_` prefab
# answers MISSING, which reads exactly like "not installed".
#
# Output: blueprints/data/door_open_pose.txt (NODE / DOOR / ANIMATOR / CLIP /
# POSE / COL rows; COL carries each collider's 8 corners in the prefab root's
# local frame, per animator state).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VALHEIM_ROOT:-/media/big4/projects/game/valheim}/Ulfsland/data/bepinex"
SANDBOX="${DOORPROBE_SANDBOX:-/tmp/doorprobe/vh}"
LIVE_PLUGINS="$VH_SRC/BepInEx/plugins"
OUT="${DOORPROBE_OUT:-$HERE/data/door_open_pose.txt}"
LOG="${DOORPROBE_LOG:-$(dirname "$SANDBOX")/doorprobe.log}"
PORT="${DOORPROBE_PORT:-3471}"
PREFABS="${DOORPROBE_PREFABS:-fb_fortress_portcullis,fb_fortress_gates,GB_Ship_Gate,fb_fortress_wall_portcullis,wood_door}"
mkdir -p "$(dirname "$LOG")"

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude 'scratch-save/' --exclude '*.pdf' \
    "$VH_SRC/" "$SANDBOX/"
fi
rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"

MANAGED="$SANDBOX/valheim_server_Data/Managed"
mcs -target:library -nologo -optimize+ \
  -r:"$MANAGED/assembly_valheim.dll" -r:"$MANAGED/assembly_utils.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" -r:"$MANAGED/UnityEngine.PhysicsModule.dll" \
  -r:"$MANAGED/UnityEngine.AnimationModule.dll" \
  -r:"$MANAGED/UnityEngine.dll" -r:"$MANAGED/netstandard.dll" \
  -r:"$SANDBOX/BepInEx/core/BepInEx.dll" \
  -out:"$SANDBOX/BepInEx/plugins/DoorProbe.dll" \
  "$HERE/DoorProbe.cs"

for m in Jotunn JsonDotNET OdinShip RavenwoodVikingHouses RavenwoodRestorations; do
  [ -e "$LIVE_PLUGINS/$m" ] && cp -a "$LIVE_PLUGINS/$m" "$SANDBOX/BepInEx/plugins/$m" || true
done

rm -f "$OUT"
mkdir -p "$SANDBOX/scratch-save"
cd "$SANDBOX"
set +e
env DOORSTOP_ENABLED=1 \
  DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
  LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
  LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
  SteamAppId=892970 \
  DOORPROBE_OUT="$OUT" DOORPROBE_TIMEOUT=420 \
  DOORPROBE_PREFABS="$PREFABS" \
  ./valheim_server.x86_64 -nographics -batchmode \
    -name doorprobe -port "$PORT" -world DoorProbeScratch -password doorprobe11 \
    -savedir "$SANDBOX/scratch-save" >"$LOG" 2>&1
rc=$?
set -e
echo "raw_exit=$rc"
if [ -s "$OUT" ]; then echo "ok: $OUT $(stat -c%s "$OUT") bytes"; else echo "MISSING: $OUT"; tail -40 "$LOG"; fi
