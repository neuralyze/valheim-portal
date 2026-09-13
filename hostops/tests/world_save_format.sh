#!/usr/bin/env bash
# Backup and restore across both Valheim world save formats, against a fixture world
# root so the live worlds are never touched.
# Run: bash hostops/tests/world_save_format.sh
#
# Valheim 1.0.12 stores a world as the directory worlds_local/<World>/ holding
# _main.<N>.fwl2, _main.<N>.db2, _main.<N>.chunks, _main.<N>.ok and one .chunk per
# zone; 0.220.x stored the pair worlds_local/<World>.db + <World>.fwl. Both are live
# on the original host (Ulfsland is 1.0.12, the other four worlds are not), so every
# case here is paired with its control: the 1.0 detection must not fire on an old
# world, and the pair detection must not fire on a 1.0 world.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
backup="$HOSTOPS/backup_valheim_world.sh"
restore="$HOSTOPS/restore_valheim_world.sh"

tmp=$(mktemp -d /tmp/world-save-format.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

root="$tmp/valheim"
mkdir -p "$root/world_backups"
export VALHEIM_ROOT=$root

# The first four bytes of a real _main.0.fwl2 read from Ulfsland on 2026-09-12:
# int32 declared length 49, int32 world version 41, then the 7-bit-prefixed world
# name. Only the *.fwl2 suffix decides the format, but a plausible body keeps the
# fixture honest about what is being archived.
fwl2_bytes() {
	printf '1\x00\x00\x00)\x00\x00\x00\x08%s' "$1"
}

# make_directory_world builds a 1.0-format world, complete with the noise the game
# itself leaves in worlds_local: an auto-backup DIRECTORY (measured on Ulfsland as
# Ulfsland_backup_auto-20260912-200308/, holding only a 53-byte _main.0.fwl2).
make_directory_world() {
	# Separate declarations: bash expands every word of one `local` before assigning
	# any of them, so `dir="$root/$world/..."` beside `world=$1` reads an unset
	# variable and set -u aborts.
	local world=$1 stem=$2
	local dir="$root/$world/config_merged/worlds_local"
	mkdir -p "$dir/$stem" "$dir/${stem}_backup_auto-20260912-200308"
	fwl2_bytes "$stem" >"$dir/$stem/_main.1.fwl2"
	printf 'DB2-%s\n' "$stem" >"$dir/$stem/_main.1.db2"
	printf 'CHUNKS-%s\n' "$stem" >"$dir/$stem/_main.1.chunks"
	printf ')\x00\x00\x00' >"$dir/$stem/_main.1.ok"
	printf 'ZONE-%s\n' "$stem" >"$dir/$stem/00_00__0_1.chunk"
	fwl2_bytes "$stem" >"$dir/${stem}_backup_auto-20260912-200308/_main.0.fwl2"
}

# make_pair_world builds a 0.220.x world, with the game's own rolling backups beside
# it; the four worlds still on that format carry dozens of them.
make_pair_world() {
	local world=$1 stem=$2
	local dir="$root/$world/config_merged/worlds_local"
	mkdir -p "$dir"
	printf 'DB-%s\n' "$stem" >"$dir/$stem.db"
	printf 'FWL-%s\n' "$stem" >"$dir/$stem.fwl"
	printf 'OLD-DB\n' >"$dir/${stem}_backup_auto-20260730213536.db"
	printf 'OLD-FWL\n' >"$dir/${stem}_backup_auto-20260730213536.fwl"
}

members() {
	tar -tzf "$1" | sed 's:/$::' | sort
}

run_backup() {
	local world=$1 name=$2
	bash "$backup" "$world" "$name" >"$tmp/out" 2>"$tmp/err" ||
		fail "$world: backup exited $? -- $(cat "$tmp/err")"
	tail -n 1 "$tmp/out"
}

# --- 1.0 directory world -----------------------------------------------------------
make_directory_world Ulfsland Ulfsland
archive=$(run_backup Ulfsland dirfmt)
[[ -f $archive ]] || fail "directory world: backup printed no archive path: $archive"
grep -q 'directory format' "$tmp/out" ||
	fail "directory world: backup did not report the 1.0 format: $(cat "$tmp/out")"
got=$(members "$archive")
want=$(printf '%s\n' Ulfsland Ulfsland/00_00__0_1.chunk Ulfsland/_main.1.chunks \
	Ulfsland/_main.1.db2 Ulfsland/_main.1.fwl2 Ulfsland/_main.1.ok | sort)
[[ $got == "$want" ]] || fail "directory world: archive members wrong:
$got"

# --- control: an old-format world is NOT treated as 1.0 ----------------------------
make_pair_world Vangard Vangard
pair_archive=$(run_backup Vangard pairfmt)
grep -q 'pair format' "$tmp/out" ||
	fail "pair world: backup did not report the 0.220 format: $(cat "$tmp/out")"
got=$(tar -tzf "$pair_archive")
[[ $got == "Vangard.db
Vangard.fwl" ]] || fail "pair world: archive members or order changed: $got"

# --- control: a lowercase pair still wins over the world's own casing --------------
make_pair_world Doggerland doggerland
lower_archive=$(run_backup Doggerland lowerfmt)
got=$(tar -tzf "$lower_archive")
[[ $got == "doggerland.db
doggerland.fwl" ]] || fail "lowercase pair: archive members wrong: $got"

# --- control: a directory with no *.fwl2 must not flip an old world to 1.0 ---------
# "is a directory" is not the test. worlds_local can hold directories that are not
# worlds, and an old-format world beside one must still back up as a pair.
mkdir -p "$root/Storgard/config_merged/worlds_local"
make_pair_world Storgard Storgard
mkdir -p "$root/Storgard/config_merged/worlds_local/Storgard"
printf 'not a world\n' >"$root/Storgard/config_merged/worlds_local/Storgard/README"
stray_archive=$(run_backup Storgard strayfmt)
got=$(tar -tzf "$stray_archive")
[[ $got == "Storgard.db
Storgard.fwl" ]] || fail "stray directory: old-format world was not archived as a pair: $got"

# --- a world with no save at all is named, not handed to tar -----------------------
mkdir -p "$root/Empty/config_merged/worlds_local"
if bash "$backup" Empty emptyfmt >"$tmp/out" 2>"$tmp/err"; then
	fail "empty world: backup succeeded with no save present"
fi
grep -q 'no Valheim world save found for Empty' "$tmp/err" ||
	fail "empty world: expected a named refusal, got: $(cat "$tmp/err")"
[[ -z $(find "$root/world_backups" -name 'world-Empty-*' -print -quit) ]] ||
	fail "empty world: backup left an archive behind"

# --- restore round-trips the 1.0 directory -----------------------------------------
world_dir="$root/Ulfsland/config_merged/worlds_local"
# Clobber every generation so a no-op restore cannot pass, and leave a stale
# generation that only a whole-directory replacement removes.
printf 'CLOBBERED\n' >"$world_dir/Ulfsland/_main.1.db2"
printf 'STALE\n' >"$world_dir/Ulfsland/_main.9.db2"
bash "$restore" Ulfsland "$(basename "$archive")" >"$tmp/out" 2>"$tmp/err" ||
	fail "directory restore exited $? -- $(cat "$tmp/err")"
grep -qx "restored world=Ulfsland backup=$(basename "$archive")" "$tmp/out" ||
	fail "directory restore: missing success line, got: $(cat "$tmp/out")"
[[ $(cat "$world_dir/Ulfsland/_main.1.db2") == "DB2-Ulfsland" ]] ||
	fail "directory restore: _main.1.db2 not restored: $(cat "$world_dir/Ulfsland/_main.1.db2")"
[[ -f "$world_dir/Ulfsland/_main.1.ok" ]] ||
	fail "directory restore: the .ok commit marker was dropped"
[[ ! -e "$world_dir/Ulfsland/_main.9.db2" ]] ||
	fail "directory restore: a stale generation survived the replacement"
# The game's own auto-backup directory is not part of the world and must be left alone.
[[ -f "$world_dir/Ulfsland_backup_auto-20260912-200308/_main.0.fwl2" ]] ||
	fail "directory restore: the game's auto-backup directory was destroyed"
# Nothing but worlds may be left inside worlds_local: a directory in there that is not
# a valid world hung the 1.0 server in the start scene on 2026-09-12.
got=$(find "$world_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
[[ $got == "Ulfsland
Ulfsland_backup_auto-20260912-200308" ]] ||
	fail "directory restore: worlds_local gained an entry: $got"

# --- restore refuses the other format's archive for the same world -----------------
# A 1.0 archive must not be unpacked over a pair world or the reverse. Vangard's pair
# archive is renamed to Ulfsland's naming, which is the only thing the filename guard
# checks, so the members test is what has to refuse it.
cp -- "$pair_archive" "$root/world_backups/world-Ulfsland-crossfmt-2026-01-01_00-00-00.tgz"
if bash "$restore" Ulfsland world-Ulfsland-crossfmt-2026-01-01_00-00-00.tgz \
	>"$tmp/out" 2>"$tmp/err"; then
	fail "restore accepted another world's pair archive under an Ulfsland name"
fi
grep -q 'does not contain the selected world save' "$tmp/err" ||
	fail "cross-format restore: expected a members refusal, got: $(cat "$tmp/err")"

# --- restore into an empty worlds_local, which is the rollback case -----------------
rm -rf -- "$world_dir/Ulfsland"
bash "$restore" Ulfsland "$(basename "$archive")" >"$tmp/out" 2>"$tmp/err" ||
	fail "restore into an empty directory exited $? -- $(cat "$tmp/err")"
[[ -s "$world_dir/Ulfsland/_main.1.fwl2" ]] ||
	fail "restore into an empty directory produced no metadata file"

# --- the staging directory is never created inside worlds_local --------------------
# Asserted on the interface rather than by racing the script: a tar shim records every
# -C directory the restore extracts into, which is the staging path it chose. This is
# the hazard from 2026-09-12 -- a non-world directory inside worlds_local hung the 1.0
# server's start scene with a null WorldGenerator and no log line naming the cause.
shim="$tmp/bin"
mkdir -p "$shim"
cat >"$shim/tar" <<'SHIM'
#!/usr/bin/env bash
previous=''
for argument in "$@"; do
	if [[ $previous == -C ]]; then
		printf '%s\n' "$argument" >>"$TAR_C_LOG"
	fi
	previous=$argument
done
exec /usr/bin/env -u PATH /usr/bin/tar "$@"
SHIM
chmod 0755 "$shim/tar"
export TAR_C_LOG="$tmp/tar-c.log"
: >"$TAR_C_LOG"
PATH="$shim:$PATH" bash "$restore" Ulfsland "$(basename "$archive")" \
	>"$tmp/out" 2>"$tmp/err" || fail "shimmed restore exited $? -- $(cat "$tmp/err")"
[[ -s $TAR_C_LOG ]] || fail "the tar shim recorded no -C directory; the probe is broken"
while read -r directory; do
	[[ $directory != "$world_dir"* ]] ||
		fail "restore staged inside worlds_local: $directory"
done <"$TAR_C_LOG"

echo "PASS: backup and restore handle both Valheim world save formats"
