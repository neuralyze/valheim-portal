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
