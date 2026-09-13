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
