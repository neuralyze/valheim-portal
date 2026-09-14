#!/usr/bin/env bash
# Host-only restore primitive. The restricted portal agent invokes this only after
# it has created a fresh backup and stopped the selected allowlisted world.
set -euo pipefail

world=${1:?world name is required}
backup_name=${2:?backup filename is required}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$script_dir/lib/common.sh"

[[ $world =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]] || { echo 'invalid world name' >&2; exit 2; }
[[ $backup_name =~ ^world-"$world"-[A-Za-z0-9._-]+\.tgz$ ]] || { echo 'invalid backup filename' >&2; exit 2; }

require_valheim_root
backup_root=$VALHEIM_BACKUP_ROOT
world_dir="$VALHEIM_ROOT/$world/config_merged/worlds_local"
backup=$(realpath -e -- "$backup_root/$backup_name")
[[ $backup == "$backup_root/"* && -f $backup ]] || { echo 'backup is outside inventory' >&2; exit 2; }
[[ -d $world_dir ]] || { echo 'world save directory unavailable' >&2; exit 2; }

mapfile -t entries < <(tar -tzf "$backup")
((${#entries[@]} > 0)) || { echo 'backup does not contain the selected world save' >&2; exit 2; }

# The archive decides the format, not the live directory. Restore has to work when
# worlds_local holds nothing yet (a world wiped before a rollback, or one recreated
# from a seed), and since 1.0.12 it also has to work when the live save is the
# other format from the archive's: Ulfsland is a 1.0 directory world while the four
# older worlds are still 0.220.x pairs, and rolling either one back to an older
# archive is the whole point of the inventory. Reading the live directory to decide
# what the archive "should" hold, which is what this script used to do, gets both of
# those wrong.
#
# Both shapes must still name this world. The stem is required to be the world's own
# casing or its lowercase form -- the two casings backup_valheim_world.sh writes --
# which is what stops a correctly named archive holding somebody else's save from
# being installed over this one.
format=''
stem=''
for candidate in "$world" "${world,,}"; do
	if ((${#entries[@]} == 2)) && [[ ${entries[0]} == "$candidate.db" && ${entries[1]} == "$candidate.fwl" ]]; then
		format=pair
		stem=$candidate
		break
	fi
	# A 1.0 archive is one top-level directory member plus its contents. Every entry
	# must sit under it, and at least one must be a *.fwl2: that is the same positive
	# test resolve_world_save applies to the live save, so an archive of a 0.220 pair
	# can never be mistaken for a 1.0 world or the reverse.
	contained=1
	metadata=0
	for entry in "${entries[@]}"; do
		if [[ $entry != "$candidate" && $entry != "$candidate/" && $entry != "$candidate/"* ]]; then
			contained=0
			break
		fi
		# tar already refuses to write outside -C, but an archive naming .. inside the
		# world directory would still let a crafted backup reach the rest of the world
		# tree once the directory is moved into place.
		if [[ $entry == *".."* ]]; then
			contained=0
			break
		fi
		if [[ $entry == *.fwl2 ]]; then
			metadata=1
		fi
	done
	if ((contained == 1 && metadata == 1)); then
		format=directory
		stem=$candidate
		break
	fi
done
[[ -n $format ]] || { echo 'backup does not contain the selected world save' >&2; exit 2; }

echo "restoring world=$world format=$format save=$stem"

stage=$(world_save_stage_dir "$world")
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "$backup" --no-same-owner --no-same-permissions -C "$stage"

if [[ $format == pair ]]; then
	[[ -f $stage/$stem.db && -f $stage/$stem.fwl ]] || { echo 'backup extraction failed validation' >&2; exit 2; }
	install -m 0640 -- "$stage/$stem.db" "$world_dir/$stem.db"
	install -m 0640 -- "$stage/$stem.fwl" "$world_dir/$stem.fwl"
else
	if ! [[ -d $stage/$stem ]] || ! compgen -G "$stage/$stem/*.fwl2" >/dev/null; then
		echo 'backup extraction failed validation' >&2
		exit 2
	fi
	# The pair only ever gets rewritten in place, so installing it root-owned 0640 has
	# always been survivable. A 1.0 world is different: the server creates the next
	# generation inside this directory on every save (_main.<N+1>.db2 plus a new
	# _main.<N+1>.ok), so a directory the container user cannot write is a world that
	# silently stops saving. Ownership and modes therefore come from the live
	# worlds_local rather than from the archive. 0775/0664 is what the game itself
	# writes, measured on Ulfsland 2026-09-12. chown needs privilege this script may
	# not have; without it we already own what we just extracted, so falling back to
	# the group alone and then to nothing is correct rather than fatal.
	chown -R --reference="$world_dir" -- "$stage/$stem" 2>/dev/null ||
		chgrp -R --reference="$world_dir" -- "$stage/$stem" 2>/dev/null || true
	chmod -R u=rwX,g=rwX,o=rX -- "$stage/$stem"
	# The outgoing save is moved OUT of worlds_local, never renamed aside inside it.
	# On 2026-09-12 a directory that was not a valid world, parked in worlds_local as
	# "Ulfsland.halfcreated-192409", left the 1.0 server hanging in the start scene
	# forever: Chainloader finished, the log repeated "Waiting for server to listen on
	# UDP query port 2470" and "NullReferenceException: The WorldGenerator instance
	# was null at Heightmap.OnEnable", and "Zonesystem Awake" never appeared. Moving
	# it out fixed it immediately. Both mv calls are renames within one filesystem,
	# which is why the staging directory is a sibling of worlds_local.
	if [[ -e "$world_dir/$stem" ]]; then
		mv -- "$world_dir/$stem" "$stage/.replaced"
	fi
	if ! mv -- "$stage/$stem" "$world_dir/$stem"; then
		echo 'installing the restored world failed' >&2
		if [[ -e "$stage/.replaced" ]]; then
			mv -- "$stage/.replaced" "$world_dir/$stem"
		fi
		exit 2
	fi
fi

# One world, one save in worlds_local. Rolling a world back ACROSS the 1.0 migration
# is the whole reason the inventory still holds pre-migration archives, and until now
# restore installed the archive and left the OTHER format's save sitting next to it.
# Measured in a sandbox under VALHEIM_ROOT on 2026-09-14, restoring a pre-migration
# pair over a migrated 1.0 world:
#
#   restoring world=TestWorld format=pair save=TestWorld
#   restored world=TestWorld backup=world-TestWorld-predn-2026-09-12_21-12-17.tgz
#   $ ls worlds_local -> TestWorld  TestWorld.db  TestWorld.fwl
#   resolve_world_save -> WORLD_SAVE_FORMAT=directory
#   backup_valheim_world.sh TestWorld -> archived TestWorld/
#
# Exit 0, "restored", and the rollback was invisible to every other script: the
# toolchain still resolved the 1.0 directory, and the next backup archived the very
# save the operator had just rolled back FROM, cementing it as the newest archive.
# Which save the SERVER loads when both are present was never established, and this
# is deliberately not the place to find out.
#
# Both casings, because resolve_world_save accepts either and the worlds created
# before the portal existed are lowercase. Retired saves go into the staging
# directory the EXIT trap removes, which is where the directory branch above already
# puts the save it replaces, and the caller is required to have taken a fresh backup
# before calling this at all.
#
# A retirement that cannot complete is fatal even though the restored save is already
# installed: "restored" while a second save of the same world is still present is the
# silent-no-op this block exists to prevent, so it has to be loud. It needs only write
# permission on worlds_local, which this run just used to install into it.
for other in "$world" "${world,,}"; do
	if [[ $format == directory ]]; then
		superseded=("$world_dir/$other.db" "$world_dir/$other.fwl")
	else
		superseded=("$world_dir/$other")
	fi
	for leftover in "${superseded[@]}"; do
		[[ -e $leftover ]] || continue
		name=${leftover##*/}
		if ! mv -- "$leftover" "$stage/.retired-$name"; then
			echo "the restored save is installed, but the superseded $name could not be" >&2
			echo "moved out of $world_dir. Two saves of $world are present and the next" >&2
			echo "server start would pick one of them unpredictably. Remove it and retry:" >&2
			echo "  rm -rf $leftover" >&2
			exit 2
		fi
		echo "retired superseded save $name"
	done
done
echo "restored world=$world backup=$backup_name"
