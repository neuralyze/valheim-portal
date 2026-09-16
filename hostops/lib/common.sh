# shellcheck shell=bash
# Shared host-script helpers. Source it, never execute it:
#
#   SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
#   source "$SCRIPT_DIR/lib/common.sh"
#   require_valheim_root
#
# Sourcing only defines functions. Each script calls the ones it needs, so a
# script that never touches the world root is not forced to configure one.

# require_valheim_root sets VALHEIM_ROOT to the directory holding one
# subdirectory per world plus world_backups, and VALHEIM_BACKUP_ROOT to the
# backup inventory inside it.
#
# There is deliberately no default. Every one of these scripts used to hardcode
# the original author's absolute path, which meant the installer's documented
# VALHEIM_WORLD_ROOT was silently ignored and backup, list, delete and restore
# operated on a directory that does not exist on anyone else's machine. A loud
# failure naming the variable is the only safe behaviour for scripts that
# delete and overwrite world data.
require_valheim_root() {
  local root=${VALHEIM_ROOT:-}
  # The portal agent's systemd unit exports AGENT_WORLD_ROOT (written by
  # portal/scripts/install-portal.sh), and the installer and compose call the
  # same directory VALHEIM_WORLD_ROOT. Accepting both means an agent-invoked
  # script needs no extra unit configuration to find the root.
  [[ -n $root ]] || root=${AGENT_WORLD_ROOT:-}
  [[ -n $root ]] || root=${VALHEIM_WORLD_ROOT:-}
  if [[ -z $root ]]; then
    cat >&2 <<'MESSAGE'
Cannot find your Valheim world directory.

Set VALHEIM_WORLD_ROOT to the directory that holds one subdirectory per world:

  /srv/valheim/
    MyWorld/
    AnotherWorld/
    world_backups/

Run a script directly:

  VALHEIM_WORLD_ROOT=/srv/valheim ./hostops/backup_valheim_world.sh MyWorld

Or set it once for a whole shell:

  export VALHEIM_WORLD_ROOT=/srv/valheim

If you installed with scripts/install-portal.sh, this is the VALHEIM_WORLD_ROOT
from deploy/install.conf and the agent already has it; you only see this message
when running a script by hand.

One directory, three accepted names, and you never need more than one of them:
VALHEIM_WORLD_ROOT is what you set, AGENT_WORLD_ROOT is what the installer hands
the agent, and VALHEIM_ROOT is what these scripts call it internally.

There is no default on purpose. These scripts delete and overwrite world data,
so guessing a path could destroy the wrong one.
MESSAGE
    exit 78
  fi
  [[ $root == /* ]] || { echo "VALHEIM_ROOT must be an absolute path: $root" >&2; exit 78; }
  [[ -d $root ]] || { echo "VALHEIM_ROOT is not a directory: $root" >&2; exit 78; }
  VALHEIM_ROOT=$root
  # shellcheck disable=SC2034  # read by the sourcing script, not here
  # Fixed, not configurable: portal/internal/agent/agent.go resolves the backup
  # inventory as <world root>/world_backups and refuses anything outside it, so
  # a second knob here could only ever disagree with the agent.
  VALHEIM_BACKUP_ROOT="$VALHEIM_ROOT/world_backups"
}

# require_worlds_file sets VALHEIM_WORLDS_FILE to <script dir>/worlds.txt, which
# is untracked operator data. A fresh clone does not have it, and the bare "no
# such file" the bulk scripts used to emit told the operator nothing about how
# to fix that.
require_worlds_file() {
  local dir=$1
  # shellcheck disable=SC2034  # read by the sourcing script, not here
  VALHEIM_WORLDS_FILE="$dir/worlds.txt"
  [[ -f $VALHEIM_WORLDS_FILE ]] || {
    cat >&2 <<MESSAGE
$VALHEIM_WORLDS_FILE does not exist.

It is operator data and is not tracked. Create it from the example and list one
world name per line:

  cp $dir/worlds.txt.example $VALHEIM_WORLDS_FILE
  \$EDITOR $VALHEIM_WORLDS_FILE
MESSAGE
    exit 78
  }
}

# require_portal_tools sets PORTAL_TOOLS_DIR to the repository's tools/
# directory, resolved from this file's own location rather than the caller's
# working directory. The agent runs these scripts with cmd.Dir set to the
# hostops directory, but an operator runs them from anywhere, so a relative
# literal would resolve differently for the two callers.
#
# There is no environment override: hostops/ and tools/ ship together in one
# repository and a version skew between them is a bug, not a configuration.
require_portal_tools() {
  local dir
  dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/tools
  [[ -d $dir ]] || {
    echo "portal tools directory is missing: $dir" >&2
    echo "hostops/ and tools/ are siblings in one repository; copy or clone the whole tree." >&2
    exit 78
  }
  # shellcheck disable=SC2034  # read by the sourcing script, not here
  PORTAL_TOOLS_DIR=$dir
}

# require_server_docker_dir sets VALHEIM_SERVER_DOCKER_DIR to the checkout of
# the modified valheim-server-docker fork whose compose project every lifecycle
# script drives.
#
# Like the world root there is deliberately no default. That tree is a separate
# 29 MB Apache-2.0 fork, is not vendored here, and every path an operator could
# plausibly have it at is wrong for someone else. These scripts run
# `docker compose down` and `rm -v`, so guessing would stop or destroy whatever
# stack happened to live at the guessed path.
require_server_docker_dir() {
  local dir=${VALHEIM_SERVER_DOCKER_DIR:-}
  if [[ -z $dir ]]; then
    cat >&2 <<'MESSAGE'
VALHEIM_SERVER_DOCKER_DIR is not set.

Set it to a checkout of the modified valheim-server-docker fork -- the
directory holding docker-compose.yaml and default.env -- for example:

  VALHEIM_SERVER_DOCKER_DIR=/srv/valheim-server-docker ./hostops/start_valheim_server.sh MyWorld

The portal installer writes it into the agent's environment file. There is no
default: these scripts run `docker compose down` and `docker compose rm -v`
against whatever project lives there, so guessing a path would tear down
someone else's stack.
MESSAGE
    exit 78
  fi
  [[ $dir == /* ]] || { echo "VALHEIM_SERVER_DOCKER_DIR must be an absolute path: $dir" >&2; exit 78; }
  [[ -d $dir ]] || { echo "VALHEIM_SERVER_DOCKER_DIR is not a directory: $dir" >&2; exit 78; }
  # A compose file is the one thing every caller needs from this directory, so
  # checking for it turns "pointed at the wrong tree" into a message about the
  # variable instead of an opaque docker compose error.
  [[ -f $dir/docker-compose.yaml || -f $dir/docker-compose.yml || -f $dir/compose.yaml || -f $dir/compose.yml ]] || {
    echo "VALHEIM_SERVER_DOCKER_DIR holds no compose file: $dir" >&2
    echo "It must point at a checkout of the valheim-server-docker fork." >&2
    exit 78
  }
  VALHEIM_SERVER_DOCKER_DIR=$dir
}

# require_world_upload_root sets VALHEIM_WORLD_UPLOAD_ROOT to the spool the portal
# stages an uploaded world save in, and resolves one staging id inside it into
# VALHEIM_WORLD_UPLOAD_DIR.
#
# The bytes travel on disk rather than through the agent because the agent caps a
# JSON operation payload at 32 MiB and a Valheim database is routinely far larger:
# the four worlds on the original host range up to four megabytes today and grow
# without bound. Only the id crosses the socket, so this is the one place that
# turns it into a path, and the id is checked against the portal's randomID()
# alphabet first: a caller must not be able to name a directory.
require_world_upload_root() {
  local id=$1
  [[ $id =~ ^[a-f0-9]{32}$ ]] || {
    echo "world upload id is not a 32-character hex staging id: $id" >&2
    exit 2
  }
  local root=${VALHEIM_WORLD_UPLOAD_ROOT:-}
  [[ -n $root ]] || root=${AGENT_WORLD_UPLOAD_ROOT:-}
  [[ -n $root ]] || root=${PORTAL_WORLD_UPLOAD_ROOT:-}
  if [[ -z $root ]]; then
    cat >&2 <<'MESSAGE'
VALHEIM_WORLD_UPLOAD_ROOT is not set, so an uploaded world save cannot be found.

It is the directory the portal writes a staged save pair into, and it must be the
same directory on both sides: bind-mounted read-write into the portal container
and listed in the agent unit's ReadWritePaths. The portal calls it
PORTAL_WORLD_UPLOAD_ROOT and defaults it to /var/lib/valheim-world-uploads.

There is no default here on purpose. This path is copied into a new world's save
directory, so guessing it would populate a server from whatever happened to be
at the guessed location.
MESSAGE
    exit 78
  fi
  [[ $root == /* ]] || { echo "VALHEIM_WORLD_UPLOAD_ROOT must be an absolute path: $root" >&2; exit 78; }
  [[ -d $root ]] || { echo "VALHEIM_WORLD_UPLOAD_ROOT is not a directory: $root" >&2; exit 78; }
  VALHEIM_WORLD_UPLOAD_ROOT=$root
  # shellcheck disable=SC2034  # read by the sourcing script, not here
  VALHEIM_WORLD_UPLOAD_DIR="$root/$id"
  [[ -d $VALHEIM_WORLD_UPLOAD_DIR ]] || {
    echo "staged world upload does not exist: $VALHEIM_WORLD_UPLOAD_DIR" >&2
    echo "A staging directory is swept after two hours; upload the archive again." >&2
    exit 2
  }
}

# resolve_world_save detects which of the two save layouts one world uses and
# sets WORLD_SAVE_FORMAT ("directory" or "pair"), WORLD_SAVE_STEM (the name the
# save carries on disk, which is not always the world's own casing) and
# WORLD_SAVE_MEMBERS (the tar members, relative to worlds_local, that are the
# save). It returns 1 when the world has no save at all.
#
# Valheim 1.0.12 (network version 40, released 2026-09-09) replaced the
# worlds_local/<World>.db + worlds_local/<World>.fwl pair with a DIRECTORY
# worlds_local/<World>/ holding one generation of files per save. Measured on
# Ulfsland 2026-09-12 while it ran 1.0.12:
#
#   Ulfsland/_main.1.fwl2    144 bytes   world metadata
#   Ulfsland/_main.1.db2     145402      the world database
#   Ulfsland/_main.1.chunks  21
#   Ulfsland/_main.1.ok      4           int32 41, the world version
#   Ulfsland/00_00__0_1.chunk 1504       one file per saved zone
#
# Before this, backup_valheim_world.sh handed tar the pair names unconditionally
# and died with "tar: ulfsland.db: Cannot stat: No such file or directory",
# which is what made POST /admin/worlds/Ulfsland/analysis answer 409 with job
# detail "initial backup failed". The other four worlds on this host are still
# 0.220.x pairs and are the rollback path, so the layout is detected per world
# and never assumed, and the pair branch is left exactly as it was.
#
# Both sides of the test are positive, deliberately. "Not a pair" must not imply
# 1.0, and "is a directory" must not imply 1.0 either: worlds_local also holds
# the game's own rolling backups, which under 1.0 are directories
# (Ulfsland_backup_auto-20260912-200308/, measured) and under 0.220.x are
# <World>_backup_auto-*.db files. So 1.0 requires <stem>/ to exist AND to hold a
# *.fwl2, and the pair requires both files to exist. A world matching neither is
# reported to the caller rather than left to surface as a raw tar failure.
# shellcheck disable=SC2034  # the three WORLD_SAVE_* results are read by the sourcing script
resolve_world_save() {
  local world_dir=$1 world=$2 stem
  for stem in "$world" "${world,,}"; do
    if [[ -d "$world_dir/$stem" ]] && compgen -G "$world_dir/$stem/*.fwl2" >/dev/null; then
      WORLD_SAVE_FORMAT=directory
      WORLD_SAVE_STEM=$stem
      WORLD_SAVE_MEMBERS=("$stem")
      return 0
    fi
  done
  # The world's own casing wins over the lowercase form, which is the order
  # backup_valheim_world.sh has used since it was written: the server writes the
  # save under whichever casing WORLD_NAME carried, and the worlds created
  # before the portal existed are lowercase.
  for stem in "$world" "${world,,}"; do
    if [[ -f "$world_dir/$stem.db" && -f "$world_dir/$stem.fwl" ]]; then
      WORLD_SAVE_FORMAT=pair
      WORLD_SAVE_STEM=$stem
      WORLD_SAVE_MEMBERS=("$stem.db" "$stem.fwl")
      return 0
    fi
  done
  return 1
}

# world_save_stage_dir prints a private staging directory for one world, created
# under <world>/config_merged and never under config_merged/worlds_local.
#
# It is a separate helper only so the reason survives: on 2026-09-12 a directory
# that was not a valid world, parked inside worlds_local as
# "Ulfsland.halfcreated-192409", left the 1.0 server hanging in the start scene
# forever. Chainloader finished, the log repeated "Waiting for server to listen
# on UDP query port 2470" and "NullReferenceException: The WorldGenerator
# instance was null at Heightmap.OnEnable", and "Zonesystem Awake" never
# appeared; moving the directory out of worlds_local fixed it immediately. No
# log line named the cause, and the exact trigger is still unproven -- the game
# writes its own Ulfsland_backup_auto-*/ directories in there with the same
# incomplete contents -- so the rule here is the cheap one that holds under
# every candidate explanation: the portal puts nothing in worlds_local except a
# world. config_merged is the container's CONFIG_DIR, is the same filesystem as
# worlds_local so a rename into place stays atomic, and is not scanned for
# worlds.
world_save_stage_dir() {
  local world=$1
  mktemp -d "$VALHEIM_ROOT/$world/config_merged/.portal-restore.XXXXXX"
}

# require_readable_world_save proves this user can actually OPEN the save
# resolve_world_save just found, repairs the one condition that is repairable,
# and otherwise explains the refusal. Call it with the worlds_local path, after
# resolve_world_save.
#
# Measured on Ulfsland 2026-09-12, as the real service user:
#
#   sudo -u valheim-agent ls worlds_local/Ulfsland        -> lists five files
#   sudo -u valheim-agent head -c 8 .../_main.2.fwl2      -> Permission denied
#
# The world directory is mode drw-rw-r--. It has no execute bit, and without
# execute nothing inside a directory can be opened or even stat'ed, though
# readdir still works -- which is why the names list and every open fails. The
# ACL makes it worse in a way that is invisible without getfacl: worlds_local
# carries a correct default ACL, so the world directory really does inherit
# "user:valheim-agent:rwx", but the mode also sets "mask::rw-" and the mask
# clamps every named entry. getfacl prints it outright:
#
#   user:valheim-agent:rwx  #effective:rw-
#
# So the inherited ACL was never the problem. Without this gate the failure is
# far worse than a refusal: tar streams as it goes, so it created the archive,
# failed on all five members and left a 122-byte tgz holding nothing but the
# directory entry sitting in the backup inventory, where it is the NEWEST
# archive for that world and would be picked as the restore source.
#
# The repair (any execute bit, which also lifts the ACL mask) is attempted, not
# assumed: only the owner or root may change a mode, the world directory is
# owned by the container user, and the agent is not it. So an operator or root
# run heals the world and proceeds, and an agent run refuses and says exactly
# what to run. A chmod here that could only ever fail for the caller that
# matters would be a fix-shaped change, not a fix.
require_readable_world_save() {
  local world_dir=$1
  local target="$world_dir/${WORLD_SAVE_MEMBERS[0]}"
  if world_save_is_readable "$world_dir"; then
    return 0
  fi
  if chmod a+X -- "$target" 2>/dev/null && world_save_is_readable "$world_dir"; then
    echo "repaired traversal on $target, which Valheim 1.0 left without an execute bit" >&2
    return 0
  fi
  # Two different faults reach here and they need different repairs: the world
  # directory not opening (the 1.0 + ACL-mask case the chmod above targets), or
  # the directory opening fine while the .db2 holding the world will not. Naming
  # the execute bit for the second one sends the operator to a chmod that cannot
  # help, so say which one it actually is.
  local unreadable_db2='' probe
  if [[ $WORLD_SAVE_FORMAT == directory && -x $target ]]; then
    for probe in "$target"/*.db2; do
      if [[ -f $probe && ! -r $probe ]]; then
        unreadable_db2=$probe
        break
      fi
    done
  fi
  if [[ -n $unreadable_db2 ]]; then
    echo "cannot read the world data at $unreadable_db2" >&2
    echo "It is mode $(stat -c %A -- "$unreadable_db2" 2>/dev/null || echo unknown), owned by" >&2
    echo "$(stat -c '%U:%G' -- "$unreadable_db2" 2>/dev/null || echo unknown), and this user is $(id -un)." >&2
    echo "The directory opens; the save file itself does not, so archiving it would stream a" >&2
    echo "backup that cannot be restored. Repair it as the owner or as root, then retry:" >&2
    echo "  chmod u+r $unreadable_db2" >&2
    return 1
  fi
  echo "cannot read the world save at $target" >&2
  echo "It is mode $(stat -c %A -- "$target" 2>/dev/null || echo unknown) and this user is $(id -un)." >&2
  echo "A directory with no execute bit cannot be opened into, and its mode also sets an" >&2
  echo "ACL mask that clamps the inherited user:valheim-agent:rwx entry to rw-." >&2
  echo "Repair it as the owner or as root, then retry:  chmod a+X $target" >&2
  return 1
}

# world_save_is_readable is the probe behind require_readable_world_save: it
# opens what would actually be archived rather than trusting the mode bits,
# because the mode alone does not tell you what an ACL mask did to it.
world_save_is_readable() {
  local world_dir=$1 member probe found=1
  if [[ $WORLD_SAVE_FORMAT == directory ]]; then
    # The *.fwl2 files are the same ones resolve_world_save detected the format
    # by, so a readable one means the directory is traversable and the save can
    # be streamed. The glob itself proves nothing: matching names needs only
    # read on the directory, which is exactly the permission that is present.
    for probe in "$world_dir/${WORLD_SAVE_MEMBERS[0]}"/*.fwl2; do
      if [[ -f $probe && -r $probe ]]; then
        found=0
      fi
    done
    [[ $found == 0 ]] || return "$found"
    # A readable .fwl2 proves traversal, NOT that the world data itself opens:
    # measured 2026-09-13 with _main.1.db2 at mode 000 and the .fwl2 readable,
    # this probe passed and only tar's mid-stream failure stopped the stub. The
    # .db2 holds the world; if any exist, at least one must open, so the gate
    # fails before an archive file exists rather than relying on the cleanup.
    local db2 saw_db2=0
    for db2 in "$world_dir/${WORLD_SAVE_MEMBERS[0]}"/*.db2; do
      [[ -f $db2 ]] || continue
      saw_db2=1
      if [[ -r $db2 ]]; then
        return 0
      fi
    done
    # A .db2 that exists and will not open is the fault. No .db2 at all is not:
    # a world can be mid creation, and traversal is already proven above.
    [[ $saw_db2 == 1 ]] && return 1
    return 0
  fi
  for member in "${WORLD_SAVE_MEMBERS[@]}"; do
    if ! [[ -f "$world_dir/$member" && -r "$world_dir/$member" ]]; then
      return 1
    fi
  done
  return 0
}

# require_matching_game_build proves that the game binary a world is ABOUT TO RUN
# is the one that was installed, and repairs the one divergence that is
# repairable. Call it with the world name, before starting the container.
#
# There are three copies of the game under a world's DATA_DIR, and only one of
# them is the one that executes:
#
#   dl/server/      the Steam download cache. valheim-updater rsyncs this ONTO
#                   the install, with --delete.
#   server/         the install.
#   bepinex/        the BepInEx overlay. THIS is what runs - the process is
#                   /opt/valheim/bepinex/valheim_server.x86_64 - and it is
#                   re-merged from the install only when the updater's rsync
#                   reports a change.
#
# Nothing used to compare them, and on 2026-09-13 that cost three worlds. Steam
# refused to update Doggerland, Storgard and Vangard ("Error! App 896660 state
# is 0x6"); the updater fell through to its rsync and restored the stale cache
# over a correct install; the overlay was never re-merged because the rsync
# reported "no change". Each world then booted Valheim 0.221.12 against a 1.0
# mod set, logged 10,000-12,000 MissingMethodException for Character.Message,
# generated a fresh empty world and SAVED IT over the real one - Doggerland.db
# went from 20,164,038 bytes to 70. Recovery took the pre-migration archives.
#
# The updater's fall-through is fixed upstream of us now (valheim-server-docker
# f000f97f), but that fix lives in a separate checkout that this repository's
# gates cannot see, and a stale image can still carry the old script. So this
# check does not trust it: it measures the bytes.
#
# Divergence is treated by which copy is authoritative:
#   overlay != install  -> repairable, and repaired. Touching dl/bepinex/merge
#                          is the signal the container's own bepinex-updater
#                          consumes to re-merge on boot, so the fix rides the
#                          normal path rather than this script copying binaries.
#   cache   != install  -> reported, not repaired. Mid-update this is normal and
#                          transient. It is only dangerous when the cache is
#                          OLDER, because then the next update rsyncs a
#                          downgrade over a good install - exactly what happened
#                          above - so that one case refuses to start.
#
# A world with no install yet (a first-ever boot, where the updater downloads
# everything) has nothing to compare and passes.
#
# Since 2026-09-15 it also enforces the mod set's game-build ANCHOR, which is
# the check this function was believed to be and was not: three copies of a NEW
# build agree with each other perfectly. See the anchor section at the bottom of
# this file for what is recorded, why the anchor is the assembly hash and not the
# Steam buildid, and what a refusal does.
require_matching_game_build() {
  local world=$1
  local data_dir config_dir
  data_dir=$(world_env_dir "$world" DATA_DIR data)
  config_dir=$(world_env_dir "$world" CONFIG_DIR config_merged)

  local install="$data_dir/server/$VALHEIM_GAME_ASSEMBLY"
  local overlay="$data_dir/bepinex/$VALHEIM_GAME_ASSEMBLY"
  local cache="$data_dir/dl/server/$VALHEIM_GAME_ASSEMBLY"

  if [[ ! -f $install ]]; then
    echo "$world: no game install yet at $install - letting the updater fetch it" >&2
    return 0
  fi

  local install_sum overlay_sum cache_sum
  install_sum=$(sha256sum -- "$install" | cut -d' ' -f1)

  if [[ -f $overlay ]]; then
    overlay_sum=$(sha256sum -- "$overlay" | cut -d' ' -f1)
    if [[ $overlay_sum != "$install_sum" ]]; then
      echo "$world: the BepInEx overlay is not the installed game build." >&2
      echo "  overlay $overlay" >&2
      echo "  install $install" >&2
      echo "  Signalling a re-merge; the container will rebuild the overlay on boot." >&2
      mkdir -p -- "$data_dir/dl/bepinex"
      touch -- "$data_dir/dl/bepinex/merge"
    fi
  fi

  if [[ -f $cache ]]; then
    cache_sum=$(sha256sum -- "$cache" | cut -d' ' -f1)
    if [[ $cache_sum != "$install_sum" ]]; then
      if [[ $cache -ot $install ]]; then
        echo "$world: REFUSING TO START - the Steam download cache is older than the install." >&2
        echo "  cache   $cache" >&2
        echo "  install $install" >&2
        echo "  valheim-updater rsyncs the cache onto the install with --delete, so" >&2
        echo "  starting now risks downgrading the game under this world's mod set." >&2
        echo "  Fix the cache first, e.g. let Steam refresh it:" >&2
        echo "    docker exec -u valheim valheim-server-$world bash -lc \\" >&2
        echo "      'cd /opt/steamcmd && ./steamcmd.sh +force_install_dir /opt/valheim/dl/server \\" >&2
        echo "       +login anonymous +app_update 896660 -validate +quit'" >&2
        echo "  This world is deliberately DOWN, not broken; the reason is recorded in" >&2
        echo "  $(game_build_hold_path "$world")." >&2
        # Every refusal on this path writes a hold, so "is this world down on purpose?" is
        # one file to read rather than three scripts to reverse-engineer.
        write_game_build_hold "$world" \
          "held=cache_older_than_install" \
          "detail=the Steam download cache is older than the install, and the boot rsyncs it onto the install with --delete" \
          "installed_game_assembly_sha256=$install_sum" \
          "cache_game_assembly_sha256=$cache_sum" \
          "release=refresh the Steam download cache, then start the world normally" || true
        return 1
      fi
      echo "$world: the Steam download cache differs from the install but is newer -" >&2
      echo "  an update is pending and will be applied on boot." >&2
    fi
  fi

  require_anchored_game_build "$world" "$data_dir" "$config_dir" "$install_sum" "${cache_sum:-}"
}

# ---------------------------------------------------------------------------
# The game-build anchor: what the deployed mod set was validated against.
# ---------------------------------------------------------------------------
#
# require_matching_game_build above compares the three copies of the game to
# each other. That catches a real failure, but it answers a question nobody
# asked - "do these three files agree?" - and the question that matters is "is
# this the build the plugins in this world were built against?". Three copies of
# a NEW build agree with each other perfectly, so a legitimate Steam update
# passes that check trivially, which is the case it most needs to catch. The
# limitation was written down at the time: docs/proposed/2026-09-13-mod-set-
# provenance.md:46-49.
#
# So the mod set gets a record of its own, beside the profile link it already
# has (tools/profile_store.py:45 writes <world>/mods/.active-mod-profile):
#
#   <world>/mods/.game-build-anchor   key=value, written by record_game_build_anchor
#   <world>/mods/.game-build-hold     key=value, written when a start is refused
#
# WHY THE ASSEMBLY HASH AND NOT THE STEAM BUILDID. Measured on this host
# 2026-09-15:
#
#   Ulfsland/data/server/steamapps/appmanifest_896660.acf      ABSENT
#   Ulfsland/data/dl/server/steamapps/appmanifest_896660.acf   buildid 25253791
#   Hrafnheim: identical - absent under the install, 25253791 under the cache
#
# There is no appmanifest under the install at all, because the updater rsyncs
# the cache onto the install with --exclude steamapps (valheim-updater:86). The
# buildid is therefore a property of the DOWNLOAD CACHE - the one copy that is
# allowed to be ahead of the install - so a gate keyed on it would be reading
# one file to answer a question about a different one. The assembly hash is the
# opposite: it is measurable on all three copies, and it is the surface the
# plugins are patched against (assembly_valheim.dll, 2,560,000 bytes, sha256
# 1231fc2f... on all 15 copies across the five worlds, measured 2026-09-15). So
# the hash is what is compared; the buildid is recorded beside it because
# "25253791 -> something else" is what a human can act on, and it is never
# compared.
#
# The mod set's own fingerprint is recorded too, so the anchor cannot silently
# outlive the mod set it claims to describe. It is structural - relative path
# plus size over *.dll under bepinex/plugins and bepinex/patchers - not content:
# the deployed set is 887,174,428 bytes on Ulfsland, and hashing that on every
# start and every five-minute watchdog pass is I/O this gate does not need. A
# mod-set difference is REPORTED and never refused: a changed plugin does not
# overwrite a world, a changed binary does. What it means is "this anchor is
# stale", and it is said in those words.
VALHEIM_GAME_ASSEMBLY=valheim_server_Data/Managed/assembly_valheim.dll

# world_env_dir reads one directory out of a world's valheim.env, falling back to
# the conventional path under the world root. The values are quoted in that file
# (CONFIG_DIR='...'), so one layer of either quote is stripped.
world_env_dir() {
  local world=$1 key=$2 fallback=$3
  local env_file="$VALHEIM_ROOT/$world/valheim.env" value=""
  if [[ -f $env_file ]]; then
    value=$(sed -n "s/^$key=//p" "$env_file" | tail -1 | sed "s/^[\"']//; s/[\"']\$//")
  fi
  [[ -n $value ]] || value="$VALHEIM_ROOT/$world/$fallback"
  printf '%s' "$value"
}

game_build_anchor_path() { printf '%s' "$VALHEIM_ROOT/$1/mods/.game-build-anchor"; }
game_build_hold_path() { printf '%s' "$VALHEIM_ROOT/$1/mods/.game-build-hold"; }

# rcon_answers succeeds when the WORLD ITSELF replies, and it deliberately does
# not read a log, a status file or a save timestamp. Those are all downstream of
# the container's log sink, and this host has a measured failure mode that stops
# the sink dead while the game keeps running: a burst of console replies makes
# the container's syslogd lose its stdout pipe, after which not one further line
# is written. MEASURED 2026-09-16: a build's object census killed the sink, the
# watchdog read a frozen log plus a stale save and SIGTERMed a perfectly healthy
# world in the middle of a terrain write. The server answered `players` in 0.03 s
# the entire time.
#
# An RCON round trip is 8 bytes and ~35 ms, it touches nothing this fault can
# reach, and a reply proves a frame actually ran - which is the only question a
# wedge test is really asking. Absent config, an unreachable container or a
# missing python3 all return non-zero: the caller must treat "cannot ask" as
# "no answer" rather than as proof of health.
rcon_answers() {
  local world=$1 cfg pass ip
  cfg="$VALHEIM_ROOT/$world/config_merged/bepinex/org.tristan.rcon.cfg"
  [[ -r $cfg ]] || return 1
  pass=$(sed -n 's/^Password *= *//p' "$cfg" | tail -1)
  [[ -n $pass ]] || return 1
  ip=$(docker inspect "valheim-server-$world" \
        --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
  [[ -n $ip ]] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  RCON_HOST="$ip" RCON_PASS="$pass" timeout 20 python3 - <<'PY' >/dev/null 2>&1
import os, socket, struct, sys

def pkt(pid, ptype, body):
    payload = struct.pack("<ii", pid, ptype) + body.encode() + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload

def read(sock):
    head = b""
    while len(head) < 4:
        chunk = sock.recv(4 - len(head))
        if not chunk:
            raise EOFError
        head += chunk
    (length,) = struct.unpack("<i", head)
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise EOFError
        body += chunk
    return body

try:
    with socket.create_connection((os.environ["RCON_HOST"], 2458), timeout=8) as sock:
        sock.settimeout(8)
        sock.sendall(pkt(1, 3, os.environ["RCON_PASS"]))
        read(sock)
        # `players` is the smallest reply the server can give and it is produced on
        # the game thread, so a reply is evidence a frame ran - not merely that the
        # socket is bound.
        sock.sendall(pkt(2, 2, "players"))
        read(sock)
except Exception:
    sys.exit(1)
sys.exit(0)
PY
}

# anchor_field prints one value out of a key=value file, or nothing. A missing
# file is not an error here: the caller decides what absence means, and the two
# callers mean different things by it.
anchor_field() {
  local file=$1 key=$2
  [[ -f $file ]] || return 0
  sed -n "s/^$key=//p" "$file" | tail -1
}

# game_cache_buildid prints the Steam buildid of the DOWNLOAD CACHE, or
# "unknown". It is recorded for a human to read and is never compared - see the
# section header for why the install has no manifest to read.
game_cache_buildid() {
  local data_dir=$1 id=""
  local manifest="$data_dir/dl/server/steamapps/appmanifest_896660.acf"
  if [[ -r $manifest ]]; then
    id=$(sed -n 's/^[[:space:]]*"buildid"[[:space:]]*"\([0-9]*\)".*/\1/p' "$manifest" | head -1)
  fi
  printf '%s' "${id:-unknown}"
}

# mod_set_fingerprint prints "<sha256> <dll count>" for one world's deployed mod
# set, or "none 0" when nothing is deployed there yet.
mod_set_fingerprint() {
  local config_dir=$1 listing="" sum="" count=0
  local plugins="$config_dir/bepinex/plugins" patchers="$config_dir/bepinex/patchers"
  listing=$(
    {
      if [[ -d $plugins ]]; then
        find "$plugins" -type f -name '*.dll' -printf 'plugins/%P %s\n' 2>/dev/null
      fi
      if [[ -d $patchers ]]; then
        find "$patchers" -type f -name '*.dll' -printf 'patchers/%P %s\n' 2>/dev/null
      fi
    } | LC_ALL=C sort
  ) || true
  if [[ -n $listing ]]; then
    count=$(printf '%s\n' "$listing" | wc -l | tr -d ' ')
    sum=$(printf '%s\n' "$listing" | sha256sum | cut -d' ' -f1)
  fi
  printf '%s %s' "${sum:-none}" "$count"
}

# record_game_build_anchor states that this world's deployed mod set runs on one
# particular game build. It is the human decision, made explicit: the deploy path
# calls it after staging a mod set, and an operator calls it through
# hostops/anchor_game_build.sh after re-validating one against a new build.
# Recording releases any hold, because the hold is the absence of this decision.
#
#   record_game_build_anchor WORLD REASON [install|cache]
#
# The third argument names which copy of the game to read the build from, and
# defaults to the install. "cache" exists for the one case the install cannot
# answer: a Steam update that has landed in the download cache and not yet been
# rsynced onto the install. Without it a pending update would be unreleasable,
# because the install only changes on boot and the boot is what gets refused.
record_game_build_anchor() {
  local world=$1 reason=$2 source=${3:-install}
  local data_dir config_dir copy copy_sum copy_bytes buildid anchor tmp fp count
  data_dir=$(world_env_dir "$world" DATA_DIR data)
  config_dir=$(world_env_dir "$world" CONFIG_DIR config_merged)
  case "$source" in
  install) copy="$data_dir/server/$VALHEIM_GAME_ASSEMBLY" ;;
  cache) copy="$data_dir/dl/server/$VALHEIM_GAME_ASSEMBLY" ;;
  *)
    echo "$world: cannot anchor - unknown source '$source' (want install or cache)" >&2
    return 1
    ;;
  esac
  if [[ ! -f $copy ]]; then
    echo "$world: cannot anchor - there is no game $source at $copy" >&2
    return 1
  fi
  copy_sum=$(sha256sum -- "$copy" | cut -d' ' -f1)
  copy_bytes=$(stat -c %s -- "$copy")
  buildid=$(game_cache_buildid "$data_dir")
  read -r fp count <<<"$(mod_set_fingerprint "$config_dir")"
  anchor=$(game_build_anchor_path "$world")
  mkdir -p -- "${anchor%/*}" 2>/dev/null || true
  tmp="$anchor.new.$$"
  if ! {
    printf 'anchor_version=1\n'
    printf 'world=%s\n' "$world"
    printf 'game_assembly_sha256=%s\n' "$copy_sum"
    printf 'game_assembly_bytes=%s\n' "$copy_bytes"
    printf 'game_assembly_source=%s\n' "$source"
    printf 'game_buildid=%s\n' "$buildid"
    printf 'mod_set_fingerprint=%s\n' "$fp"
    printf 'mod_set_dll_count=%s\n' "$count"
    printf 'recorded_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'recorded_by=%s\n' "${SUDO_USER:-${USER:-unknown}}"
    printf 'recorded_reason=%s\n' "$reason"
  } >"$tmp" 2>/dev/null; then
    rm -f -- "$tmp" 2>/dev/null || true
    echo "$world: could not write the game-build anchor at $anchor" >&2
    return 1
  fi
  if ! mv -f -- "$tmp" "$anchor" 2>/dev/null; then
    rm -f -- "$tmp" 2>/dev/null || true
    echo "$world: could not install the game-build anchor at $anchor" >&2
    return 1
  fi
  echo "$world: game-build anchor recorded ($reason)" >&2
  echo "  assembly $copy_sum ($copy_bytes bytes) from the $source, Steam cache buildid $buildid" >&2
  echo "  mod set  $fp ($count dll)" >&2
  clear_game_build_hold "$world"
  return 0
}

# write_game_build_hold records WHY a world is deliberately not running. Extra
# arguments are key=value lines appended verbatim.
#
# This file is the answer to "is this world down because Steam moved, or because
# it is broken?". A crash leaves no hold file; this is only ever written by a
# refusal, and it is removed the moment a start passes the gate, so its presence
# means "held now", not "was held once".
write_game_build_hold() {
  local world=$1
  shift
  local hold tmp
  hold=$(game_build_hold_path "$world")
  mkdir -p -- "${hold%/*}" 2>/dev/null || true
  tmp="$hold.new.$$"
  if ! {
    printf 'hold_version=1\n'
    printf 'world=%s\n' "$world"
    printf 'detected_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'detected_by=%s\n' "${0##*/}"
    printf '%s\n' "$@"
  } >"$tmp" 2>/dev/null; then
    rm -f -- "$tmp" 2>/dev/null || true
    return 1
  fi
  mv -f -- "$tmp" "$hold" 2>/dev/null || { rm -f -- "$tmp" 2>/dev/null || true; return 1; }
  return 0
}

clear_game_build_hold() {
  local hold
  hold=$(game_build_hold_path "$1")
  [[ -e $hold ]] || return 0
  if rm -f -- "$hold" 2>/dev/null; then
    echo "$1: cleared the game-build hold at $hold" >&2
  fi
  return 0
}

# require_anchored_game_build refuses a start when the game build is not the one
# this world's mod set was anchored to. Call it from require_matching_game_build,
# never directly: the three-copy comparison has to run first, because the copy
# this compares is the install and the overlay repair is what makes the install
# the copy that will execute.
#
# The comparison is against the build that will be EXECUTING after this start,
# which is the cache's when a cache exists and the install's otherwise:
#
#   cache != anchor          REFUSE. The boot rsyncs the cache onto the install
#                            (valheim-updater:86, with --delete) and re-merges the
#                            overlay, unconditionally and with no host-side file
#                            able to stop it, so the start IS the update and the
#                            start is the only place it can be stopped. Reported
#                            as pending_game_update when the install still matches
#                            the anchor, game_build_changed when neither does.
#   no cache, install
#   != anchor                REFUSE. The game changed under the mod set.
#   cache == anchor but
#   install != anchor        PERMIT, and say so: the boot installs the anchored
#                            build from the cache, so the anchored build is what
#                            ends up executing.
#   anchor present but has
#   no assembly hash         REFUSE. A safety record that cannot be read
#                            certifies nothing, and a human wrote it.
#   mod set != anchor        REPORT. The anchor is stale; the binary is still the
#                            one the anchor names.
#
# No anchor at all records the installed build and then compares against it. That
# is trust-on-first-use, and it is the only honest option here: five worlds are
# deployed today with no anchor, refusing them all would take the fleet down to
# install a safety feature, and there is nothing on disk that says what those mod
# sets were validated against. What it records is a fact - "this is what is
# installed right now" - not a certification, and it says so.
#
# A first observation is still COMPARED, not waved through. Recording the install
# and then returning would let a pending update ride in on the first pass, which
# is the one route this function exists to close.
require_anchored_game_build() {
  local world=$1 data_dir=$2 config_dir=$3 install_sum=$4 cache_sum=${5:-}
  local anchor expected expected_buildid recorded_at recorded_reason buildid fp count anchor_fp

  anchor=$(game_build_anchor_path "$world")
  buildid=$(game_cache_buildid "$data_dir")
  read -r fp count <<<"$(mod_set_fingerprint "$config_dir")"

  if [[ ! -f $anchor ]]; then
    echo "$world: no game-build anchor yet - recording the installed build as the" >&2
    echo "  one this mod set runs on. This records what is installed now; it does not" >&2
    echo "  certify that the $count deployed dll were validated against it." >&2
    if ! record_game_build_anchor "$world" first-observation; then
      echo "$world: WARNING - no anchor was written, so a game-build change cannot be detected." >&2
      return 0
    fi
  fi

  expected=$(anchor_field "$anchor" game_assembly_sha256)
  expected_buildid=$(anchor_field "$anchor" game_buildid)
  recorded_at=$(anchor_field "$anchor" recorded_at)
  recorded_reason=$(anchor_field "$anchor" recorded_reason)

  if [[ -z $expected ]]; then
    echo "$world: REFUSING TO START - the game-build anchor is unreadable." >&2
    echo "  anchor $anchor has no game_assembly_sha256 line." >&2
    echo "  A human wrote that file to say which build this mod set runs on. Re-record it" >&2
    echo "  once you know the answer:  ./hostops/anchor_game_build.sh $world --reason operator-approved" >&2
    write_game_build_hold "$world" \
      "held=anchor_unreadable" \
      "anchor=$anchor" \
      "detail=the game-build anchor exists but carries no game_assembly_sha256, so nothing can be compared" \
      "release=./hostops/anchor_game_build.sh $world --reason operator-approved" || true
    return 1
  fi

  # What matters is the build that will be EXECUTING after this start, not the one on the
  # install right now: the container rsyncs the download cache onto the install on boot
  # (valheim-updater:86, with --delete) and re-merges the overlay when that rsync reports a
  # change. So when a cache is present it is the cache that decides, and an install which
  # differs from the anchor while the cache holds the anchored build is not a refusal - it is
  # the boot repairing itself.
  #
  # This is also what keeps the gate releasable. Anchoring reads the install, so if a refusal
  # required install == anchor there would be no way out of a pending update: the install only
  # changes on boot, and the boot is what is refused. --from-cache is the human's way to say
  # "the mod set was validated against the build that is about to land".
  local held="" detail=""
  if [[ -n $cache_sum && $cache_sum != "$expected" ]]; then
    if [[ $install_sum == "$expected" ]]; then
      held=pending_game_update
      detail="the Steam download cache holds a different game build, and the container rsyncs it onto the install on boot"
    else
      held=game_build_changed
      detail="neither the installed game assembly nor the Steam download cache is the build this mod set was anchored to"
    fi
  elif [[ $install_sum != "$expected" ]]; then
    if [[ -n $cache_sum ]]; then
      echo "$world: the install is not the anchored build, but the Steam cache is - the boot" >&2
      echo "  rsyncs the cache onto the install, so the anchored build is what will execute." >&2
    else
      held=game_build_changed
      detail="the installed game assembly is not the one this mod set was anchored to"
    fi
  fi

  if [[ -n $held ]]; then
    echo "$world: REFUSING TO START - $detail." >&2
    echo "  anchored  $expected  (Steam cache buildid $expected_buildid, recorded $recorded_at, $recorded_reason)" >&2
    echo "  installed $install_sum" >&2
    if [[ -n $cache_sum ]]; then
      echo "  cache     $cache_sum  (Steam cache buildid $buildid)" >&2
    fi
    echo "  This world's $count deployed dll were validated against the anchored build." >&2
    echo "  Starting anyway is the 2026-09-13 failure: a mismatched binary generated a" >&2
    echo "  fresh world and saved it over the real one (Doggerland.db 20,164,038 -> 70 bytes)." >&2
    echo "  This world is deliberately DOWN, not broken. The reason is recorded in:" >&2
    echo "    $(game_build_hold_path "$world")" >&2
    echo "  Re-validate the mod set against the new build, then release the hold - from the" >&2
    echo "  install, or from the Steam cache when the new build has not landed on it yet:" >&2
    echo "    ./hostops/anchor_game_build.sh $world --reason operator-approved" >&2
    echo "    ./hostops/anchor_game_build.sh $world --from-cache --reason operator-approved" >&2
    write_game_build_hold "$world" \
      "held=$held" \
      "detail=$detail" \
      "anchored_game_assembly_sha256=$expected" \
      "installed_game_assembly_sha256=$install_sum" \
      "cache_game_assembly_sha256=${cache_sum:-absent}" \
      "anchored_steam_cache_buildid=$expected_buildid" \
      "steam_cache_buildid=$buildid" \
      "anchor_recorded_at=$recorded_at" \
      "anchor_recorded_reason=$recorded_reason" \
      "mod_set_dll_count=$count" \
      "release=./hostops/anchor_game_build.sh $world --reason operator-approved" || true
    return 1
  fi

  anchor_fp=$(anchor_field "$anchor" mod_set_fingerprint)
  if [[ -n $anchor_fp && $anchor_fp != "$fp" ]]; then
    echo "$world: the deployed mod set is not the one the game-build anchor describes." >&2
    echo "  anchored mod set $anchor_fp ($(anchor_field "$anchor" mod_set_dll_count) dll, recorded $recorded_at)" >&2
    echo "  deployed mod set $fp ($count dll)" >&2
    echo "  The binary is still the anchored build, so this world starts. What is stale is" >&2
    echo "  the claim that these plugins were validated against it; re-record it with" >&2
    echo "  ./hostops/anchor_game_build.sh $world --reason mod-set-changed when that is true." >&2
  fi

  clear_game_build_hold "$world"
  return 0
}
