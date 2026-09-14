#!/usr/bin/env bash
# Run LocScan inside a SANDBOX COPY of the Valheim dedicated server, once per
# seed, and dump ZoneSystem's location placement as JSON.
#
# Unlike run_scan.sh this actually boots a world per seed (locations need the
# game's prefabs and a live coroutine), so budget ~1 minute per seed rather than
# ~1 second. It still never writes to the live install: the sandbox is an rsync
# mirror, the world is a throwaway in $SANDBOX/scratch-save, and the port is 3456.
#
# Usage:
#   VH_SRC=/path/to/server/install \
#   SEEDS=/tmp/seeds.txt \
#   OUT=/tmp/seedscan/loc \
#   [SANDBOX=/tmp/seedscan/vh] [LOC_TIMEOUT=600] [SKIP_EXISTING=1] \
#   tools/seedscan/run_locscan.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VH_SRC="${VH_SRC:?set VH_SRC to the server install dir}"
SEEDS="${SEEDS:?set SEEDS to a file of seed strings, one per line}"
OUT="${OUT:?set OUT to an output directory}"
SANDBOX="${SANDBOX:-/tmp/seedscan/vh}"
LOC_TIMEOUT="${LOC_TIMEOUT:-600}"
SKIP_EXISTING="${SKIP_EXISTING:-1}"

[ -x "$VH_SRC/valheim_server.x86_64" ] || { echo "no valheim_server.x86_64 in $VH_SRC" >&2; exit 1; }
[ -f "$VH_SRC/BepInEx/core/BepInEx.Preloader.dll" ] || { echo "no BepInEx core in $VH_SRC" >&2; exit 1; }

if [ ! -x "$SANDBOX/valheim_server.x86_64" ]; then
  echo "staging sandbox copy in $SANDBOX (mirror of $VH_SRC, live BepInEx excluded)"
  mkdir -p "$SANDBOX"
  rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
fi

rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"
MANAGED="$SANDBOX/valheim_server_Data/Managed" \
BEPINEX_CORE="$SANDBOX/BepInEx/core" \
  "$HERE/build.sh" "$SANDBOX/BepInEx/plugins" >/dev/null

mkdir -p "$OUT"
n=0; ok=0; fail=0
while IFS= read -r seed; do
  seed="$(printf '%s' "$seed" | tr -d '\r')"
  case "$seed" in ''|'#'*) continue;; esac
  n=$((n+1))
  # Seed strings can contain anything a player can type, so never use one as a
  # filename directly - index the outputs and keep the mapping in index.tsv.
  safe="$(printf '%s' "$seed" | md5sum | cut -c1-12)"
  target="$OUT/$safe.json"
  printf '%s\t%s\n' "$seed" "$safe.json" >> "$OUT/index.tsv"
  if [ "$SKIP_EXISTING" = "1" ] && [ -s "$target" ]; then
    echo "[$n] $seed -> cached"; ok=$((ok+1)); continue
  fi

  rm -rf "$SANDBOX/scratch-save"; mkdir -p "$SANDBOX/scratch-save"
  start=$(date +%s)
  ( cd "$SANDBOX" && env \
      DOORSTOP_ENABLED=1 \
      DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll \
      LD_LIBRARY_PATH="./doorstop_libs:./linux64:${LD_LIBRARY_PATH:-}" \
      LD_PRELOAD="libdoorstop_x64.so:${LD_PRELOAD:-}" \
      SteamAppId=892970 \
      LOCSCAN_SEED="$seed" \
      LOCSCAN_OUT="$target" \
      LOCSCAN_TIMEOUT="$LOC_TIMEOUT" \
      timeout $((LOC_TIMEOUT + 120)) \
      ./valheim_server.x86_64 -nographics -batchmode \
        -name locscan -port 3456 -world LocScanScratch -password locscan1 \
        -public 0 -savedir "$SANDBOX/scratch-save" ) >"$OUT/$safe.log" 2>&1 || true
  dur=$(( $(date +%s) - start ))

  if [ -s "$target" ]; then
    echo "[$n] $seed -> ok (${dur}s, $(grep -c '"name"' "$target" || true) entries)"
    ok=$((ok+1))
  else
    echo "[$n] $seed -> FAILED (${dur}s); see $OUT/$safe.log" >&2
    fail=$((fail+1))
  fi
done < "$SEEDS"

sort -u -o "$OUT/index.tsv" "$OUT/index.tsv"
echo "locscan: $ok/$n ok, $fail failed"
[ "$fail" -eq 0 ]
