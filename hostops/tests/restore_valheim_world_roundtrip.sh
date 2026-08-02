#!/usr/bin/env bash
# Round-trips a world save pair through backup_valheim_world.sh's archive naming
# and restore_valheim_world.sh. Run: bash hostops/tests/restore_valheim_world_roundtrip.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
WORLD=Midgard-Redesign

tmp=$(mktemp -d /tmp/restore-roundtrip.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

root="$tmp/valheim"
world_dir="$root/$WORLD/config_merged/worlds_local"
mkdir -p "$world_dir" "$root/world_backups"

# restore_valheim_world.sh resolves its root from the environment, so the real
# script runs unmodified against the sandbox.
export VALHEIM_ROOT=$root
restore="$HOSTOPS/restore_valheim_world.sh"

for stem in "$WORLD" "${WORLD,,}"; do
  rm -f -- "$world_dir"/*.db "$world_dir"/*.fwl "$root/world_backups"/*.tgz

  printf 'DB-CONTENT-%s\n' "$stem" >"$world_dir/$stem.db"
  printf 'FWL-CONTENT-%s\n' "$stem" >"$world_dir/$stem.fwl"

  # Archive exactly as backup_valheim_world.sh does: world-<WORLD>-<name>-<stamp>.tgz
  # holding "<save stem>.db" then "<save stem>.fwl", relative to the world dir.
  archive="world-$WORLD-known-good-$(date +%Y-%m-%d_%H-%M-%S).tgz"
  (cd "$world_dir" && tar czf "$root/world_backups/$archive" "$stem.db" "$stem.fwl")

  # Clobber the live saves so a no-op restore cannot pass.
  printf 'CLOBBERED\n' >"$world_dir/$stem.db"
  printf 'CLOBBERED\n' >"$world_dir/$stem.fwl"

  bash "$restore" "$WORLD" "$archive" >"$tmp/out" 2>"$tmp/err" ||
    fail "stem $stem: restore exited $? -- $(cat "$tmp/err")"

  [[ -f "$world_dir/$stem.db" ]] || fail "stem $stem: $stem.db missing after restore"
  [[ -f "$world_dir/$stem.fwl" ]] || fail "stem $stem: $stem.fwl missing after restore"
  [[ $(cat "$world_dir/$stem.db") == "DB-CONTENT-$stem" ]] ||
    fail "stem $stem: .db content not restored: $(cat "$world_dir/$stem.db")"
  [[ $(cat "$world_dir/$stem.fwl") == "FWL-CONTENT-$stem" ]] ||
    fail "stem $stem: .fwl content not restored: $(cat "$world_dir/$stem.fwl")"
  grep -qx "restored world=$WORLD backup=$archive" "$tmp/out" ||
    fail "stem $stem: missing success line, got: $(cat "$tmp/out")"

  # The staging directory must not survive the run.
  leftovers=("$world_dir"/.portal-restore.*)
  [[ ! -e ${leftovers[0]} ]] || fail "stem $stem: staging directory left behind: ${leftovers[0]}"
done

# An archive whose members do not match the world is still refused.
rm -f -- "$root/world_backups"/*.tgz
printf 'x\n' >"$tmp/other.db"
printf 'x\n' >"$tmp/other.fwl"
(cd "$tmp" && tar czf "$root/world_backups/world-$WORLD-foreign-2026-01-01_00-00-00.tgz" other.db other.fwl)
if bash "$restore" "$WORLD" "world-$WORLD-foreign-2026-01-01_00-00-00.tgz" >"$tmp/out" 2>"$tmp/err"; then
  fail "restore accepted an archive holding a foreign save pair"
fi
grep -q 'does not contain the selected world save pair' "$tmp/err" ||
  fail "expected save-pair rejection, got: $(cat "$tmp/err")"

echo "PASS: restore_valheim_world.sh round-trip"
