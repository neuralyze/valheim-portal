#!/usr/bin/env bash
# Round-trips a world save through backup_valheim_world.sh's archive naming and
# restore_valheim_world.sh, in BOTH save formats -- the 0.220 <World>.db/<World>.fwl
# pair and the 1.0 <World>/ directory -- and across the boundary between them, which
# is the rollback the backup inventory exists for.
# Run: bash hostops/tests/restore_valheim_world_roundtrip.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
WORLD=Midgard-Redesign

tmp=$(mktemp -d /tmp/restore-roundtrip.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

root="$tmp/valheim"
world_dir="$root/$WORLD/config_merged/worlds_local"
# The staging directory is a sibling of worlds_local under config_merged, never
# inside it. See world_save_stage_dir in hostops/lib/common.sh for why.
cm="$root/$WORLD/config_merged"
mkdir -p "$world_dir" "$root/world_backups"

reset_world_dir() {
  rm -rf -- "$world_dir"
  mkdir -p "$world_dir"
  rm -f -- "$root/world_backups"/*.tgz
}

assert_no_stage() {
  local label=$1 leftovers
  leftovers=("$cm"/.portal-restore.*)
  [[ ! -e ${leftovers[0]} ]] || fail "$label: staging directory left behind: ${leftovers[0]}"
}

# resolve_world_save is what every other hostops script uses to find a world's save,
# so it is the honest test of whether a restore actually took effect for the toolchain.
resolved_format() {
  # A separate PROCESS, not a subshell. Sourcing common.sh in a subshell makes the
  # linter treat this script's own world_dir and root as subshell-modified for the
  # rest of the file (SC2031, ten findings), and the sourced helpers set WORLD_SAVE_*
  # and VALHEIM_* which have no business leaking into the test's scope either.
  # (Phrased to avoid starting a comment line with the linter's own name, which is
  # how it recognises a directive - doing so made it fail to parse this file at all.)
  bash -c '
    source "$1/lib/common.sh"
    resolve_world_save "$2" "$3" || { echo none; exit 0; }
    echo "$WORLD_SAVE_FORMAT"
  ' _ "$HOSTOPS" "$world_dir" "$WORLD"
}

# restore_valheim_world.sh resolves its root from the environment, so the real
# script runs unmodified against the sandbox.
export VALHEIM_ROOT=$root
restore="$HOSTOPS/restore_valheim_world.sh"

for stem in "$WORLD" "${WORLD,,}"; do
  reset_world_dir

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

  assert_no_stage "stem $stem"
done

# The 1.0 directory branch of the restore script had no coverage at all, which is how
# the cross-format hole below survived. The archive shape is what
# backup_valheim_world.sh writes for a directory world: the single member "<stem>/"
# plus its contents, at their original basenames.
for stem in "$WORLD" "${WORLD,,}"; do
  reset_world_dir
  mkdir -p "$world_dir/$stem"
  printf 'FWL2-%s\n' "$stem" >"$world_dir/$stem/_main.7.fwl2"
  printf 'DB2-%s\n' "$stem" >"$world_dir/$stem/_main.7.db2"
  printf 'OK-%s\n' "$stem" >"$world_dir/$stem/_main.7.ok"
  printf 'CHUNK-%s\n' "$stem" >"$world_dir/$stem/00_00__0_1.chunk"

  archive="world-$WORLD-dir-$(date +%Y-%m-%d_%H-%M-%S).tgz"
  tar czf "$root/world_backups/$archive" -C "$world_dir" "$stem"

  # Clobber the live save the way the 2026-09-13 migration did on three worlds: the
  # directory is still there and still holds a *.fwl2, it just holds an empty world.
  # A restore that merged into it instead of replacing it would leave the sentinel.
  rm -f -- "$world_dir/$stem"/*
  printf 'CLOBBERED\n' >"$world_dir/$stem/_main.1.fwl2"
  printf 'CLOBBERED\n' >"$world_dir/$stem/SENTINEL"

  bash "$restore" "$WORLD" "$archive" >"$tmp/out" 2>"$tmp/err" ||
    fail "dir stem $stem: restore exited $? -- $(cat "$tmp/err")"

  [[ ! -e "$world_dir/$stem/SENTINEL" ]] ||
    fail "dir stem $stem: restore merged into the clobbered save instead of replacing it"
  [[ ! -e "$world_dir/$stem/_main.1.fwl2" ]] ||
    fail "dir stem $stem: the clobbered generation survived the restore"
  for member in _main.7.fwl2 _main.7.db2 _main.7.ok 00_00__0_1.chunk; do
    [[ -f "$world_dir/$stem/$member" ]] || fail "dir stem $stem: $member missing after restore"
  done
  [[ $(cat "$world_dir/$stem/_main.7.db2") == "DB2-$stem" ]] ||
    fail "dir stem $stem: .db2 content not restored: $(cat "$world_dir/$stem/_main.7.db2")"
  [[ $(resolved_format) == directory ]] ||
    fail "dir stem $stem: the toolchain resolves $(resolved_format) after a 1.0 restore"
  grep -qx "restored world=$WORLD backup=$archive" "$tmp/out" ||
    fail "dir stem $stem: missing success line, got: $(cat "$tmp/out")"
  assert_no_stage "dir stem $stem"
done

# A restore must leave exactly ONE save named this world in worlds_local. Before that
# was enforced, restoring the pre-migration pair over a migrated world exited 0 and
# printed "restored" while worlds_local still held <World>/ next to the restored
# <World>.db and <World>.fwl: resolve_world_save went on answering "directory", and
# the next backup_valheim_world.sh archived the save the operator had just rolled back
# FROM, making it the newest archive for the world.
reset_world_dir
mkdir -p "$world_dir/$WORLD"
printf 'MIGRATED\n' >"$world_dir/$WORLD/_main.4.fwl2"
printf 'MIGRATED\n' >"$world_dir/$WORLD/_main.4.db2"
printf 'ROLLBACK-DB\n' >"$tmp/$WORLD.db"
printf 'ROLLBACK-FWL\n' >"$tmp/$WORLD.fwl"
archive="world-$WORLD-predn-2026-01-01_00-00-00.tgz"
tar czf "$root/world_backups/$archive" -C "$tmp" "$WORLD.db" "$WORLD.fwl"
bash "$restore" "$WORLD" "$archive" >"$tmp/out" 2>"$tmp/err" ||
  fail "pair-over-directory: restore exited $? -- $(cat "$tmp/err")"
[[ ! -e "$world_dir/$WORLD" ]] ||
  fail "pair-over-directory: the migrated 1.0 save is still in worlds_local beside the restored pair"
[[ $(cat "$world_dir/$WORLD.db") == ROLLBACK-DB ]] ||
  fail "pair-over-directory: .db content not restored: $(cat "$world_dir/$WORLD.db")"
[[ $(resolved_format) == pair ]] ||
  fail "pair-over-directory: the toolchain still resolves $(resolved_format), so the rollback is invisible to it"
assert_no_stage "pair-over-directory"

# The same hole in the other direction: a 1.0 archive restored onto a world whose live
# save is still a 0.220 pair, which is every world in the inventory that has not been
# migrated yet.
reset_world_dir
printf 'LEGACY\n' >"$world_dir/$WORLD.db"
printf 'LEGACY\n' >"$world_dir/$WORLD.fwl"
mkdir -p "$tmp/dirarch/$WORLD"
printf 'FORWARD\n' >"$tmp/dirarch/$WORLD/_main.9.fwl2"
printf 'FORWARD\n' >"$tmp/dirarch/$WORLD/_main.9.db2"
archive="world-$WORLD-dirroll-2026-01-01_00-00-00.tgz"
tar czf "$root/world_backups/$archive" -C "$tmp/dirarch" "$WORLD"
bash "$restore" "$WORLD" "$archive" >"$tmp/out" 2>"$tmp/err" ||
  fail "directory-over-pair: restore exited $? -- $(cat "$tmp/err")"
[[ ! -e "$world_dir/$WORLD.db" && ! -e "$world_dir/$WORLD.fwl" ]] ||
  fail "directory-over-pair: the superseded 0.220 pair is still in worlds_local"
[[ $(resolved_format) == directory ]] ||
  fail "directory-over-pair: the toolchain resolves $(resolved_format)"
assert_no_stage "directory-over-pair"

# An archive whose members do not match the world is still refused.
reset_world_dir
printf 'x\n' >"$tmp/other.db"
printf 'x\n' >"$tmp/other.fwl"
(cd "$tmp" && tar czf "$root/world_backups/world-$WORLD-foreign-2026-01-01_00-00-00.tgz" other.db other.fwl)
if bash "$restore" "$WORLD" "world-$WORLD-foreign-2026-01-01_00-00-00.tgz" >"$tmp/out" 2>"$tmp/err"; then
  fail "restore accepted an archive holding a foreign save pair"
fi
# The refusal now covers both save formats, so it no longer says "pair"; what matters
# is that it is the members check that refused and not some earlier guard.
grep -q 'does not contain the selected world save' "$tmp/err" ||
  fail "expected save-members rejection, got: $(cat "$tmp/err")"

echo "PASS: restore_valheim_world.sh round-trip"
