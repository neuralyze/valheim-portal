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

# --- an archive that stores the world directory untraversable is installed usable ---
# tar records and restores a directory's mode faithfully, and --no-same-permissions
# cannot help: a umask only clears bits, it can never add the missing execute bit. Every
# 1.0 archive taken on this host before valheim-server-docker 3f73689 therefore carries
# its world directory at 0664, because ensure_permissions applied the FILE mode to what
# are now directories. Restoring one unchanged would install a world the SERVER itself
# cannot read: measured 2026-09-12 inside the live container as the actual server user,
# `docker exec -u valheim ... head -c 4 .../Ulfsland/_main.2.fwl2` was denied on a
# drw-rw-r-- directory owned by that very user, while `ls` of it succeeded and the same
# probe as root succeeded. That is the start-scene hang: the game enumerates the world,
# cannot open it, and WorldGenerator stays null with no log line naming the cause.
if [[ $(id -u) -ne 0 ]]; then
	make_directory_world Jotunheim Jotunheim
	jotun_dir="$root/Jotunheim/config_merged/worlds_local"
	jotun_archive=$(run_backup Jotunheim traversable)
	# Rebuild the archive with the directory member stored 0664, which is what every 1.0
	# archive taken on this host before the fork fix actually holds. --mode applies the
	# stored mode without touching the tree, which matters: tar itself could not read the
	# save if the live directory were chmod'ed untraversable first.
	(cd "$jotun_dir" && tar czf "$root/world_backups/world-Jotunheim-untraversable-2026-01-01_00-00-00.tgz" \
		--mode=0664 Jotunheim)
	tar -tvzf "$root/world_backups/world-Jotunheim-untraversable-2026-01-01_00-00-00.tgz" |
		grep -q '^drw-rw-r-- .*Jotunheim/$' ||
		fail "untraversable archive: the fixture does not store the directory at 0664"
	rm -rf -- "$jotun_dir/Jotunheim"

	bash "$restore" Jotunheim world-Jotunheim-untraversable-2026-01-01_00-00-00.tgz \
		>"$tmp/out" 2>"$tmp/err" ||
		fail "untraversable archive: restore exited $? -- $(cat "$tmp/err")"
	# Asserted by actually OPENING a file inside the directory, which is the only probe
	# that tells the truth. A 0664 directory still lists, because enumerating names needs
	# only read, and every check run with privilege succeeds regardless - that pair of
	# facts is why this hid from everyone until it was probed as the server's own user.
	head -c 4 -- "$jotun_dir/Jotunheim/_main.1.fwl2" >/dev/null 2>&1 ||
		fail "untraversable archive: the restored world cannot be read, mode $(stat -c %A "$jotun_dir/Jotunheim")"
	[[ $(stat -c %A "$jotun_dir/Jotunheim") == drwx* ]] ||
		fail "untraversable archive: restored world is not traversable: $(stat -c %A "$jotun_dir/Jotunheim")"
	# And the good archive of the same world still restores, so the normalisation is not
	# hiding a detection failure.
	bash "$restore" Jotunheim "$(basename "$jotun_archive")" >"$tmp/out" 2>"$tmp/err" ||
		fail "untraversable archive: the traversable archive stopped restoring -- $(cat "$tmp/err")"
fi

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

# --- a save this user cannot open is refused, and leaves no archive ------------------
# Valheim 1.0 leaves the world directory without an execute bit (measured on Ulfsland
# 2026-09-12: drw-rw-r--, and `sudo -u valheim-agent head -c 8 .../_main.2.fwl2` denied
# while `ls` of the same directory succeeded). tar streams as it goes, so before the
# readability gate this produced a 122-byte archive holding only the directory entry and
# left it in the inventory as the newest backup for that world.
#
# Root is exempt from the permission bits entirely, so these two cases can only be
# observed as an unprivileged user.
if [[ $(id -u) -ne 0 ]]; then
	make_directory_world Bifrost Bifrost
	bifrost_dir="$root/Bifrost/config_merged/worlds_local"
	chmod 0664 "$bifrost_dir/Bifrost"

	# Nobody but the owner or root may repair a mode, and in production the owner is the
	# container user while the caller is the agent. A chmod shim that fails is how that
	# asymmetry is reproduced without a second account.
	deny="$tmp/deny"
	mkdir -p "$deny"
	printf '#!/usr/bin/env bash\nexit 1\n' >"$deny/chmod"
	chmod 0755 "$deny/chmod"
	if PATH="$deny:$PATH" bash "$backup" Bifrost denied >"$tmp/out" 2>"$tmp/err"; then
		fail "unreadable save: backup succeeded"
	fi
	grep -q 'cannot read the world save' "$tmp/err" ||
		fail "unreadable save: expected a named refusal, got: $(cat "$tmp/err")"
	grep -q 'chmod a+X' "$tmp/err" ||
		fail "unreadable save: the refusal does not name the repair: $(cat "$tmp/err")"
	[[ -z $(find "$root/world_backups" -name 'world-Bifrost-denied-*' -print -quit) ]] ||
		fail "unreadable save: a stub archive was left in the inventory"

	# The same world, with the repair permitted: it is applied and the backup completes.
	archive=$(run_backup Bifrost repaired)
	grep -q 'repaired traversal' "$tmp/err" ||
		fail "repairable save: the repair was not reported: $(cat "$tmp/err")"
	[[ $(stat -c %A "$bifrost_dir/Bifrost") == drwx* ]] ||
		fail "repairable save: the execute bit was not restored: $(stat -c %A "$bifrost_dir/Bifrost")"
	members "$archive" | grep -qx 'Bifrost/_main.1.fwl2' ||
		fail "repairable save: the archive is missing the world metadata"
fi

# --- a tar that fails mid-stream leaves no archive behind ----------------------------
# The readability gate catches the known cause; this covers every other one (a save being
# rewritten underneath us, a full disk). A truncated archive in the inventory is worse
# than no archive, because list, analysis and restore would all treat it as a backup.
broken="$tmp/broken"
mkdir -p "$broken"
cat >"$broken/tar" <<'SHIM'
#!/usr/bin/env bash
# Write a stub at whatever -f names, then fail, which is what GNU tar does when it
# cannot read a member it has already opened the archive for.
previous=''
for argument in "$@"; do
	if [[ $previous == -f ]]; then
		printf 'PARTIAL' >"$argument"
	fi
	previous=$argument
done
exit 2
SHIM
chmod 0755 "$broken/tar"
if PATH="$broken:$PATH" bash "$backup" Vangard torn >"$tmp/out" 2>"$tmp/err"; then
	fail "torn archive: backup reported success"
fi
grep -q 'partial archive was removed' "$tmp/err" ||
	fail "torn archive: expected the cleanup message, got: $(cat "$tmp/err")"
[[ -z $(find "$root/world_backups" -name 'world-Vangard-torn-*' -print -quit) ]] ||
	fail "torn archive: the partial archive was left in the inventory"

echo "PASS: backup and restore handle both Valheim world save formats"
