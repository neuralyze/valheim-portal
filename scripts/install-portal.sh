#!/usr/bin/env bash
# Install, verify, or remove a Valheim Portal deployment.
#
# The portal is two deployables that share one HMAC secret: an unprivileged
# loopback-only container and a privileged host agent that runs a fixed set of
# world operations. This installer provisions both, generates the shared
# secrets once, and refuses configurations that would expose administration to
# the public internet.
#
# Read docs/installation.md before first use.
set -euo pipefail

portal_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

action=install
dry_run=false
purge=false
skip_build=false
config_file=""
allow_broad_proxy_cidr=false
allow_public_bind=false
allow_insecure_base_url=false
allow_bridge_gateway_cidr=false

# Staging prefix. Tests and packagers set this; a real install leaves it empty.
install_root=${PORTAL_INSTALL_ROOT:-}
install_root=${install_root%/}

# Host layout. Overridable so the same script works under a staging prefix.
etc_dir=${PORTAL_ETC_DIR:-/etc/valheim-portal}
bin_dir=${PORTAL_BIN_DIR:-/usr/local/bin}
unit_dir=${PORTAL_UNIT_DIR:-/etc/systemd/system}

# Deployment configuration. Every value without a safe default is required.
PORTAL_PUBLIC_BASE_URL=${PORTAL_PUBLIC_BASE_URL:-}
PORTAL_TRUSTED_PROXY_CIDR=${PORTAL_TRUSTED_PROXY_CIDR:-}
VALHEIM_WORLD_ROOT=${VALHEIM_WORLD_ROOT:-}
# The world operation scripts ship in this repository. Defaulting to them means
# a clone is a complete deployment; an override still points the agent at a
# copy installed elsewhere. hostops/ resolves its Python helpers as a sibling
# tools/ directory, so an override must name a hostops directory that has one.
AGENT_SCRIPT_DIR=${AGENT_SCRIPT_DIR:-$portal_dir/hostops}
# Checkout of the modified valheim-server-docker fork. It is a separate
# Apache-2.0 project, is not vendored here, and every lifecycle script drives
# its compose project, so there is no default worth guessing.
VALHEIM_SERVER_DOCKER_DIR=${VALHEIM_SERVER_DOCKER_DIR:-}
AGENT_ALLOWED_WORLDS=${AGENT_ALLOWED_WORLDS:-}
PORTAL_BIND_ADDR=${PORTAL_BIND_ADDR:-127.0.0.1}
PORTAL_BIND_PORT=${PORTAL_BIND_PORT:-18080}
PORTAL_AUTH_HEADER=${PORTAL_AUTH_HEADER:-X-Forwarded-User}
PORTAL_AGENT_SOCKET_DIR=${PORTAL_AGENT_SOCKET_DIR:-/run/valheim-portal-agent}
PORTAL_STEAM_API_KEY=${PORTAL_STEAM_API_KEY:-}
PORTAL_ADMIN_STEAM_IDS=${PORTAL_ADMIN_STEAM_IDS:-}
PORTAL_REQUIRE_DEVICE_CODE=${PORTAL_REQUIRE_DEVICE_CODE:-true}
PORTAL_SOURCE_URL=${PORTAL_SOURCE_URL:-}
# The agent bridge lets a local agent process read the operator conversation and
# request verbs through /api/agent/*. Off unless asked for: a deployment that has
# not opted in cannot be driven by an agent at all, which is the safe default for
# a portal that can stop servers and delete worlds.
PORTAL_ENABLE_AGENT_BRIDGE=${PORTAL_ENABLE_AGENT_BRIDGE:-false}
# The runner is the process that reads the operator conversation and asks a model
# what to do. It only exists when the bridge does. Two ways to run it, and the
# installer sets up both: on demand
#
#   sudo systemctl start valheim-agent-runner-once
#
# and as a poller, which is what this switch enables. On demand is the default
# because a poller holds a model session open against a portal nobody is watching.
AGENT_RUNNER_SERVICE=${AGENT_RUNNER_SERVICE:-false}
# The account whose omp credentials the runner uses. It cannot be the agent
# account: omp keeps its login in a home directory, and the agent runs with
# ProtectHome and no model credentials of its own. Defaults to whoever installs.
AGENT_RUNNER_USER=${AGENT_RUNNER_USER:-}
# Absolute path is required for a unit, whose PATH does not include a user's
# ~/.local/bin. Resolved from the installing user's PATH when left empty.
AGENT_RUNNER_OMP=${AGENT_RUNNER_OMP:-}
# Model for omp to use; omp's own default when empty.
AGENT_RUNNER_MODEL=${AGENT_RUNNER_MODEL:-}
# How often the poller reads the inbox. Ignored on demand.
AGENT_RUNNER_POLL=${AGENT_RUNNER_POLL:-3s}
AGENT_USER=${AGENT_USER:-valheim-agent}
AGENT_GROUP=${AGENT_GROUP:-valheim-agent}
AGENT_EXTRA_GROUPS=${AGENT_EXTRA_GROUPS:-docker}
# The uid the Valheim container runs as (its PUID). It owns every file the
# container writes, and an operator normally shares it, so both it and the
# agent need durable write access to a world regardless of which of them
# created a given file.
WORLD_OWNER_UID=${WORLD_OWNER_UID:-1000}
PORTAL_DEFAULT_JOIN_HOST=${PORTAL_DEFAULT_JOIN_HOST:-}
PORTAL_DEFAULT_GAME_PORT=${PORTAL_DEFAULT_GAME_PORT:-2456}
PORTAL_DEFAULT_PLAYER_LIMIT=${PORTAL_DEFAULT_PLAYER_LIMIT:-10}
PORTAL_DEFAULT_BACKUP_INTERVAL=${PORTAL_DEFAULT_BACKUP_INTERVAL:-1h}
PORTAL_DEFAULT_BACKUP_AGE_DAYS=${PORTAL_DEFAULT_BACKUP_AGE_DAYS:-7}
PORTAL_DEFAULT_BACKUP_COUNT=${PORTAL_DEFAULT_BACKUP_COUNT:-168}

usage() {
  cat <<'USAGE'
usage: install-portal.sh [ACTION] [options]

Actions:
  install            Provision the agent and portal, then verify (default)
  verify             Re-run only the health and header-spoofing checks
  uninstall          Stop and remove the deployment, retaining data
  print-config       Show the resolved configuration and exit

Options:
  --config FILE      Read KEY=VALUE deployment configuration from FILE
  --dry-run          Report every action without changing the host
  --skip-build       Reuse an installed agent binary instead of rebuilding
  --purge            With uninstall, also delete secrets, config, and volumes
  --allow-broad-proxy-cidr
                     Permit a trusted-proxy CIDR wider than a single host
  --allow-public-bind
                     Permit binding the portal off the loopback interface
  --allow-insecure-base-url
                     Permit an http:// public base URL
  --allow-bridge-gateway-proxy-cidr
                     Permit trusting the container bridge gateway itself
  -h, --help         Show this message

Required configuration (flag, --config file, or environment):
  PORTAL_PUBLIC_BASE_URL      Public HTTPS origin serving the portal
  PORTAL_TRUSTED_PROXY_CIDR   Exact source range of the terminating proxy
  VALHEIM_WORLD_ROOT          Directory holding Valheim world data
  VALHEIM_SERVER_DOCKER_DIR   Checkout of the valheim-server-docker fork
  AGENT_ALLOWED_WORLDS        Comma-separated worlds the agent may control

Generated here, not supplied:
  admin token                 Written to <etc>/admin-token. The proxy must send
                              its contents as X-Portal-Admin-Token on every
                              admin route, or administration is unreachable.

Optional configuration worth knowing about:
  PORTAL_STEAM_API_KEY        Steam Web API key for resolving persona names in
                              /admin. Empty (the default) falls back to the
                              public community profile XML endpoint, which
                              resolves public Steam profiles only.
  PORTAL_SOURCE_URL           Source-code link on the player pages, the AGPL
                              section 13 offer. Defaults to the upstream
                              project; set it if you deploy modified code.
  PORTAL_REQUIRE_DEVICE_CODE  "Confirm this sign-in": the player retypes a code only
                              the desktop application shows, so a stranger's browser
                              cannot authorize someone else's app. Set false ONLY on a
                              single-operator install, where there is no second party to
                              check and the step is a login tax on the only user.
                              Anything but exactly "false" keeps it on. Default true.
  PORTAL_ADMIN_STEAM_IDS      Comma-separated SteamID64s that may administer
                              the portal with their signed-in Steam identity.
                              Empty (the default) leaves administration to the
                              trusted proxy alone. Not the in-game admin role.
  AGENT_SCRIPT_DIR            Directory holding the world operation scripts.
                              Defaults to this repository's hostops/.

See deploy/install.conf.example for a documented template.
USAGE
}

log() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Collected preflight failures so one run reports every problem.
problems=()
problem() { problems+=("$1"); }

run() {
  if $dry_run; then
    printf '    [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

# Write stdin to a file with an explicit owner and mode, honouring --dry-run.
install_file() {
  local dest=$1 mode=$2 owner=$3 content
  content=$(cat)
  if $dry_run; then
    printf '    [dry-run] write %s (mode %s, owner %s, %s bytes)\n' \
      "$dest" "$mode" "$owner" "${#content}"
    return 0
  fi
  mkdir -p -- "$(dirname -- "$dest")"
  printf '%s\n' "$content" >"$dest.tmp"
  chmod "$mode" "$dest.tmp"
  # Ownership is best effort: a staging prefix has no such users.
  chown "$owner" "$dest.tmp" 2>/dev/null || true
  mv -f -- "$dest.tmp" "$dest"
  note "wrote $dest (mode $mode)"
}

while (($# > 0)); do
  case $1 in
    install | verify | uninstall | print-config) action=$1; shift ;;
    --config) config_file=${2:?--config needs a file}; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --skip-build) skip_build=true; shift ;;
    --purge) purge=true; shift ;;
    --allow-broad-proxy-cidr) allow_broad_proxy_cidr=true; shift ;;
    --allow-public-bind) allow_public_bind=true; shift ;;
    --allow-insecure-base-url) allow_insecure_base_url=true; shift ;;
    --allow-bridge-gateway-proxy-cidr) allow_bridge_gateway_cidr=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_config() {
  [[ -n $config_file ]] || return 0
  [[ -f $config_file ]] || die "missing config file: $config_file"
  local line key value
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [[ -n $line ]] || continue
    [[ $line == *=* ]] || die "invalid config line in $config_file: $line"
    key=${line%%=*}
    value=${line#*=}
    key=${key%"${key##*[![:space:]]}"}
    value=${value#"${value%%[![:space:]]*}"}
    value=${value#[\"\']}
    value=${value%[\"\']}
    [[ $key == [A-Z_]* ]] || die "invalid config key in $config_file: $key"
    printf -v "$key" '%s' "$value" 2>/dev/null || die "cannot set $key"
  done <"$config_file"
}

# Absolute path of a host location under the staging prefix.
staged() { printf '%s%s' "$install_root" "$1"; }

resolved_etc() { staged "$etc_dir"; }
resolved_bin() { staged "$bin_dir"; }
resolved_unit() { staged "$unit_dir"; }

csrf_secret_file() { printf '%s/csrf-secret' "$(resolved_etc)"; }
admin_token_file() { printf '%s/admin-token' "$(resolved_etc)"; }
agent_token_file() { printf '%s/agent-token' "$(resolved_etc)"; }
agent_bridge_token_file() { printf '%s/agent-bridge-token' "$(resolved_etc)"; }
agent_env_file() { printf '%s/agent.env' "$(resolved_etc)"; }
runner_env_file() { printf '%s/agent-runner.env' "$(resolved_etc)"; }

# Compose reads its variables from .env beside compose.yaml. A staging prefix
# redirects it so tests never touch a live deployment's file. Both the writer
# and the uninstaller resolve the path here so they can never disagree.
compose_env_file() {
  if [[ -n $install_root ]]; then
    printf '%s/compose.env' "$install_root"
  else
    printf '%s/.env' "$portal_dir"
  fi
}

# The portal reads these as container paths; compose maps them from the host.
host_csrf_secret_file() { printf '%s/csrf-secret' "$etc_dir"; }
host_admin_token_file() { printf '%s/admin-token' "$etc_dir"; }
host_agent_token_file() { printf '%s/agent-token' "$etc_dir"; }
host_agent_bridge_token_file() { printf '%s/agent-bridge-token' "$etc_dir"; }
host_runner_env_file() { printf '%s/agent-runner.env' "$etc_dir"; }

# The container path the portal reads, or empty to leave the bridge off. Compose
# mounts the host file either way, so the switch is this single value rather than
# a conditional mount that would make compose.yaml unusable in one of the states.
container_agent_bridge_token_path() {
  [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} == true ]] && printf '/run/secrets/agent-bridge-token'
}

# Release identity stamped into both binaries. Go's own VCS stamping is
# unusable when the checkout sits inside an unrelated repository, so derive it
# from tags and fall back to "dev" for an untagged tree.
resolve_version() {
  if [[ -n ${PORTAL_VERSION:-} ]]; then
    printf '%s' "$PORTAL_VERSION"
    return 0
  fi
  git -C "$portal_dir" describe --tags --always --dirty 2>/dev/null || printf 'dev'
}

print_config() {
  cat <<CONFIG
PORTAL_PUBLIC_BASE_URL=$PORTAL_PUBLIC_BASE_URL
PORTAL_TRUSTED_PROXY_CIDR=$PORTAL_TRUSTED_PROXY_CIDR
PORTAL_BIND_ADDR=$PORTAL_BIND_ADDR
PORTAL_BIND_PORT=$PORTAL_BIND_PORT
PORTAL_AUTH_HEADER=$PORTAL_AUTH_HEADER
PORTAL_ADMIN_STEAM_IDS=$PORTAL_ADMIN_STEAM_IDS
PORTAL_REQUIRE_DEVICE_CODE=$PORTAL_REQUIRE_DEVICE_CODE
PORTAL_SOURCE_URL=$PORTAL_SOURCE_URL
VALHEIM_WORLD_ROOT=$VALHEIM_WORLD_ROOT
VALHEIM_SERVER_DOCKER_DIR=$VALHEIM_SERVER_DOCKER_DIR
AGENT_SCRIPT_DIR=$AGENT_SCRIPT_DIR
AGENT_ALLOWED_WORLDS=$AGENT_ALLOWED_WORLDS
AGENT_USER=$AGENT_USER
AGENT_GROUP=$AGENT_GROUP
AGENT_EXTRA_GROUPS=$AGENT_EXTRA_GROUPS
PORTAL_AGENT_SOCKET_DIR=$PORTAL_AGENT_SOCKET_DIR
PORTAL_STEAM_API_KEY=${PORTAL_STEAM_API_KEY:+<set>}
PORTAL_DEFAULT_JOIN_HOST=${PORTAL_DEFAULT_JOIN_HOST:-<public base host>}
PORTAL_DEFAULT_GAME_PORT=$PORTAL_DEFAULT_GAME_PORT
PORTAL_DEFAULT_PLAYER_LIMIT=$PORTAL_DEFAULT_PLAYER_LIMIT
PORTAL_DEFAULT_BACKUP_INTERVAL=$PORTAL_DEFAULT_BACKUP_INTERVAL
PORTAL_DEFAULT_BACKUP_AGE_DAYS=$PORTAL_DEFAULT_BACKUP_AGE_DAYS
PORTAL_DEFAULT_BACKUP_COUNT=$PORTAL_DEFAULT_BACKUP_COUNT
etc_dir=$etc_dir
bin_dir=$bin_dir
unit_dir=$unit_dir
install_root=${install_root:-/}
CONFIG
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || problem "missing required command: $1${2:+ ($2)}"
}

check_tools() {
  need_cmd python3 "configuration validation"
  need_cmd curl "health verification"
  $skip_build || need_cmd go "building the agent binary"
  if [[ -z $install_root ]]; then
    need_cmd systemctl "managing the agent service"
    need_cmd docker "running the portal container"
    need_cmd getent "resolving the agent group"
    need_cmd useradd "creating the agent user"
    if command -v docker >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then
      problem "docker compose v2 is required (docker compose version failed)"
    fi
  fi
  command -v openssl >/dev/null 2>&1 || [[ -r /dev/urandom ]] ||
    problem "need openssl or /dev/urandom to generate secrets"
  return 0
}

# Validate the trusted-proxy range. This is the portal's entire administrative
# trust boundary: any source inside it that sets the identity header is treated
# as an authenticated administrator.
check_proxy_cidr() {
  [[ -n $PORTAL_TRUSTED_PROXY_CIDR ]] || {
    problem "PORTAL_TRUSTED_PROXY_CIDR is required"
    return 0
  }
  local verdict
  verdict=$(
    PORTAL_TRUSTED_PROXY_CIDR=$PORTAL_TRUSTED_PROXY_CIDR python3 - <<'PY'
import ipaddress, os, sys

raw = os.environ["PORTAL_TRUSTED_PROXY_CIDR"]
try:
    net = ipaddress.ip_network(raw, strict=False)
except ValueError as exc:
    print(f"fail:not a valid CIDR ({exc})")
    sys.exit(0)

tight = 32 if net.version == 4 else 112
if net.prefixlen == 0:
    print("fail:matches every possible source address")
elif not (net.is_private or net.is_loopback or net.is_link_local):
    print("broad:covers publicly routable addresses")
elif net.num_addresses > 1 and net.prefixlen < (24 if net.version == 4 else 120):
    print(f"broad:covers {net.num_addresses} addresses")
elif net.prefixlen != tight:
    print(f"warn:covers {net.num_addresses} addresses; a single proxy host is safer")
else:
    print("ok:single host")
PY
  )
  case $verdict in
    ok:*) note "trusted proxy CIDR: ${verdict#ok:}" ;;
    warn:*) warn "PORTAL_TRUSTED_PROXY_CIDR ${verdict#warn:}" ;;
    broad:*)
      if $allow_broad_proxy_cidr; then
        warn "PORTAL_TRUSTED_PROXY_CIDR ${verdict#broad:}; permitted by --allow-broad-proxy-cidr"
      else
        problem "PORTAL_TRUSTED_PROXY_CIDR ${verdict#broad:}. Any source in this range that sets $PORTAL_AUTH_HEADER becomes an administrator. Narrow it to the proxy host, or pass --allow-broad-proxy-cidr if this is deliberate."
      fi
      ;;
    fail:*) problem "PORTAL_TRUSTED_PROXY_CIDR ${verdict#fail:}" ;;
    *) problem "could not validate PORTAL_TRUSTED_PROXY_CIDR" ;;
  esac
  return 0
}

# Every bridge gateway this host currently has, plus docker0's default. A
# container reached through a published port sees the gateway as the source of
# every request, so these addresses match all traffic rather than a proxy.
bridge_gateways() {
  printf '172.17.0.1\n'
  command -v docker >/dev/null 2>&1 || return 0
  local id
  while IFS= read -r id; do
    [[ -n $id ]] || continue
    docker network inspect "$id" \
      --format '{{range .IPAM.Config}}{{println .Gateway}}{{end}}' 2>/dev/null
  done < <(docker network ls --filter driver=bridge --format '{{.ID}}' 2>/dev/null)
}

check_proxy_not_bridge_gateway() {
  [[ -n $PORTAL_TRUSTED_PROXY_CIDR ]] || return 0
  local address=${PORTAL_TRUSTED_PROXY_CIDR%%/*} gateway
  while IFS= read -r gateway; do
    [[ -n $gateway && $address == "$gateway" ]] || continue
    if $allow_bridge_gateway_cidr; then
      warn "PORTAL_TRUSTED_PROXY_CIDR is the bridge gateway $gateway; permitted by --allow-bridge-gateway-proxy-cidr. The admin token is then the only barrier in front of administration."
    else
      problem "PORTAL_TRUSTED_PROXY_CIDR is the Docker bridge gateway $gateway. Docker rewrites the source address of every request that reaches a published port, so this range matches all of them: the portal's network check can never fail and it cannot tell the proxy apart from any other process on the host. Attach the proxy to the portal's compose network and use its container address, or pass --allow-bridge-gateway-proxy-cidr to accept an admin-token-only boundary."
    fi
    return 0
  done < <(bridge_gateways)
  note "trusted proxy CIDR is not a bridge gateway"
  return 0
}

check_bind() {
  case $PORTAL_BIND_ADDR in
    127.* | ::1 | localhost) note "portal bind: $PORTAL_BIND_ADDR (loopback)" ;;
    *)
      if $allow_public_bind; then
        warn "portal binds $PORTAL_BIND_ADDR; the terminating proxy must enforce identity"
      else
        problem "PORTAL_BIND_ADDR=$PORTAL_BIND_ADDR exposes the portal beyond loopback. Administration is only protected by the proxy, so bind 127.0.0.1 or pass --allow-public-bind."
      fi
      ;;
  esac
  if ! [[ $PORTAL_BIND_PORT =~ ^[0-9]+$ ]] || ((PORTAL_BIND_PORT < 1 || PORTAL_BIND_PORT > 65535)); then
    problem "PORTAL_BIND_PORT must be a TCP port: $PORTAL_BIND_PORT"
  fi
  return 0
}

check_base_url() {
  [[ -n $PORTAL_PUBLIC_BASE_URL ]] || {
    problem "PORTAL_PUBLIC_BASE_URL is required"
    return 0
  }
  case $PORTAL_PUBLIC_BASE_URL in
    https://*) : ;;
    http://*)
      if $allow_insecure_base_url; then
        warn "PORTAL_PUBLIC_BASE_URL is http://; session cookies are marked secure and will not be sent"
      else
        problem "PORTAL_PUBLIC_BASE_URL must be https:// because the portal issues secure cookies. Pass --allow-insecure-base-url only for local testing."
      fi
      ;;
    *) problem "PORTAL_PUBLIC_BASE_URL must be an absolute http(s) URL: $PORTAL_PUBLIC_BASE_URL" ;;
  esac
  if [[ $PORTAL_PUBLIC_BASE_URL == */ ]]; then
    warn "PORTAL_PUBLIC_BASE_URL has a trailing slash; the portal joins paths itself"
  fi
  return 0
}

check_auth_header() {
  [[ -n $PORTAL_AUTH_HEADER ]] || problem "PORTAL_AUTH_HEADER must not be empty"
  [[ $PORTAL_AUTH_HEADER =~ ^[A-Za-z0-9-]+$ ]] ||
    problem "PORTAL_AUTH_HEADER must be a single header name: $PORTAL_AUTH_HEADER"
  return 0
}

# A bridge that quietly stays off because the switch says "yes" instead of "true"
# looks exactly like a portal the agent cannot reach, and the page can only report
# the symptom. Refuse the value instead of interpreting it.
check_agent_bridge_switch() {
  case ${PORTAL_ENABLE_AGENT_BRIDGE,,} in
  true) note "agent bridge: enabled; /api/agent/* accepts the bridge token" ;;
  false) note "agent bridge: disabled; the agent page will say so" ;;
  *) problem "PORTAL_ENABLE_AGENT_BRIDGE must be true or false, got '$PORTAL_ENABLE_AGENT_BRIDGE'" ;;
  esac
  return 0
}

# The runner's two failure modes are both silent, so they are resolved here rather
# than discovered from a unit that starts and does nothing: an omp that a unit's
# PATH cannot find, and an account with no model credentials.
check_agent_runner() {
  case ${AGENT_RUNNER_SERVICE,,} in
  true | false) ;;
  *)
    problem "AGENT_RUNNER_SERVICE must be true or false, got '$AGENT_RUNNER_SERVICE'"
    return 0
    ;;
  esac
  if [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} != true ]]; then
    [[ ${AGENT_RUNNER_SERVICE,,} == true ]] &&
      problem "AGENT_RUNNER_SERVICE is true but PORTAL_ENABLE_AGENT_BRIDGE is not: the runner would poll a portal that answers 503"
    return 0
  fi
  [[ -n $AGENT_RUNNER_USER ]] || AGENT_RUNNER_USER=$(logname 2>/dev/null || printf '%s' "${SUDO_USER:-$(id -un)}")
  if [[ -z $install_root ]] && ! id -u "$AGENT_RUNNER_USER" >/dev/null 2>&1; then
    problem "AGENT_RUNNER_USER does not exist: $AGENT_RUNNER_USER"
    return 0
  fi
  if [[ -z $AGENT_RUNNER_OMP ]]; then
    # Probed directly rather than through the runner user's shell: omp normally lives
    # in their ~/.local/bin, which root's PATH does not include, and a login shell is
    # not a reliable way to ask - this host's profile is bash-specific and fails under
    # sh, which silently produced "omp not found" on a host where omp was installed.
    local runner_home candidate
    runner_home=$(getent passwd "$AGENT_RUNNER_USER" 2>/dev/null | cut -d: -f6)
    [[ -n $runner_home ]] || runner_home=/home/$AGENT_RUNNER_USER
    for candidate in "$runner_home/.local/bin/omp" "$runner_home/bin/omp" /usr/local/bin/omp /usr/bin/omp; do
      [[ -x $candidate ]] || continue
      AGENT_RUNNER_OMP=$candidate
      break
    done
    [[ -n $AGENT_RUNNER_OMP ]] || AGENT_RUNNER_OMP=$(command -v omp 2>/dev/null || true)
  fi
  if [[ -z $AGENT_RUNNER_OMP ]]; then
    note "agent runner: omp not found; set AGENT_RUNNER_OMP to its absolute path before running the runner"
  elif [[ $AGENT_RUNNER_OMP != /* ]]; then
    problem "AGENT_RUNNER_OMP must be an absolute path for a systemd unit: $AGENT_RUNNER_OMP"
  fi
  if [[ ${AGENT_RUNNER_SERVICE,,} == true ]]; then
    note "agent runner: polling service enabled for $AGENT_RUNNER_USER every $AGENT_RUNNER_POLL"
  else
    note "agent runner: on demand only; run it with systemctl start valheim-agent-runner-once"
  fi
  return 0
}

check_world_root() {
  [[ -n $VALHEIM_WORLD_ROOT ]] || {
    problem "VALHEIM_WORLD_ROOT is required"
    return 0
  }
  [[ $VALHEIM_WORLD_ROOT == /* ]] || problem "VALHEIM_WORLD_ROOT must be absolute: $VALHEIM_WORLD_ROOT"
  [[ -d $VALHEIM_WORLD_ROOT ]] || problem "VALHEIM_WORLD_ROOT is not a directory: $VALHEIM_WORLD_ROOT"
  return 0
}

# The lifecycle scripts drive the compose project in this checkout, and
# provisioning reads its default.env for the PGID the container chowns its
# mounts to. It is a separate Apache-2.0 project and is deliberately not
# vendored here, so it is configuration with no default.
check_server_docker_dir() {
  [[ -n $VALHEIM_SERVER_DOCKER_DIR ]] || {
    problem "VALHEIM_SERVER_DOCKER_DIR is required; it points at a checkout of the modified valheim-server-docker fork"
    return 0
  }
  [[ $VALHEIM_SERVER_DOCKER_DIR == /* ]] ||
    problem "VALHEIM_SERVER_DOCKER_DIR must be absolute: $VALHEIM_SERVER_DOCKER_DIR"
  [[ -d $VALHEIM_SERVER_DOCKER_DIR ]] || {
    problem "VALHEIM_SERVER_DOCKER_DIR is not a directory: $VALHEIM_SERVER_DOCKER_DIR"
    return 0
  }
  local compose=""
  local candidate
  for candidate in docker-compose.yaml docker-compose.yml compose.yaml compose.yml; do
    [[ -f $VALHEIM_SERVER_DOCKER_DIR/$candidate ]] && { compose=$candidate; break; }
  done
  [[ -n $compose ]] ||
    problem "VALHEIM_SERVER_DOCKER_DIR holds no compose file: $VALHEIM_SERVER_DOCKER_DIR"
  [[ -f $VALHEIM_SERVER_DOCKER_DIR/default.env ]] ||
    problem "VALHEIM_SERVER_DOCKER_DIR holds no default.env: $VALHEIM_SERVER_DOCKER_DIR. Provisioning reads the container PGID from it."
  return 0
}

# The agent executes a fixed script per operation. The authoritative list lives
# in internal/agent/agent.go, so derive it instead of duplicating it here.
required_scripts() {
  local source=$portal_dir/internal/agent/agent.go
  [[ -r $source ]] || return 0
  grep -ohE '"[a-z0-9_]+\.sh"' "$source" | tr -d '"' | sort -u
}

check_script_dir() {
  [[ -n $AGENT_SCRIPT_DIR ]] || {
    problem "AGENT_SCRIPT_DIR is required"
    return 0
  }
  [[ $AGENT_SCRIPT_DIR == /* ]] || problem "AGENT_SCRIPT_DIR must be absolute: $AGENT_SCRIPT_DIR"
  [[ -d $AGENT_SCRIPT_DIR ]] || {
    problem "AGENT_SCRIPT_DIR is not a directory: $AGENT_SCRIPT_DIR"
    return 0
  }
  local missing=() not_exec=() name
  while IFS= read -r name; do
    [[ -n $name ]] || continue
    if [[ ! -f $AGENT_SCRIPT_DIR/$name ]]; then
      missing+=("$name")
    elif [[ ! -x $AGENT_SCRIPT_DIR/$name ]]; then
      not_exec+=("$name")
    fi
  done < <(required_scripts)
  ((${#missing[@]} == 0)) ||
    problem "AGENT_SCRIPT_DIR is missing ${#missing[@]} world operation scripts: ${missing[*]}. They ship in this repository's hostops/ directory; unset AGENT_SCRIPT_DIR to use it."
  ((${#not_exec[@]} == 0)) ||
    problem "not executable in AGENT_SCRIPT_DIR: ${not_exec[*]}"
  # The scripts resolve their Python helpers as a sibling tools/ directory and
  # their shared helpers as lib/common.sh. A copy that took only the .sh files
  # passes the check above and then fails at the first mod or world operation.
  [[ -f $AGENT_SCRIPT_DIR/lib/common.sh ]] ||
    problem "AGENT_SCRIPT_DIR has no lib/common.sh: $AGENT_SCRIPT_DIR. Copy the whole hostops/ directory, not just the scripts."
  [[ -f $AGENT_SCRIPT_DIR/../tools/valheim_mods.py ]] ||
    problem "AGENT_SCRIPT_DIR has no sibling tools/ directory: $(dirname -- "$AGENT_SCRIPT_DIR")/tools. hostops/ and tools/ ship together and must stay siblings."
  ((${#missing[@]} || ${#not_exec[@]})) ||
    note "world operation scripts: all $(required_scripts | grep -c . ) present and executable in $AGENT_SCRIPT_DIR"
  return 0
}

check_allowed_worlds() {
  [[ -n $AGENT_ALLOWED_WORLDS ]] || {
    problem "AGENT_ALLOWED_WORLDS is required; the agent controls nothing without it"
    return 0
  }
  local world
  IFS=',' read -ra worlds <<<"$AGENT_ALLOWED_WORLDS"
  for world in "${worlds[@]}"; do
    world=${world//[[:space:]]/}
    [[ -n $world ]] || problem "AGENT_ALLOWED_WORLDS contains an empty entry"
    [[ $world =~ ^[A-Za-z0-9_-]+$ ]] ||
      problem "invalid world name in AGENT_ALLOWED_WORLDS: $world"
    if [[ -n $VALHEIM_WORLD_ROOT && -d $VALHEIM_WORLD_ROOT && ! -d $VALHEIM_WORLD_ROOT/$world ]]; then
      warn "world '$world' has no directory under $VALHEIM_WORLD_ROOT yet"
    fi
  done
  return 0
}

# Steam identities allowed to administer the portal. This is portal
# authorisation and is unrelated to a world's in-game admin role, which grants
# nothing here. A malformed entry fails silently at runtime -- the operator
# simply never sees the administration link, with nothing to distinguish that
# from a deliberately empty list -- so reject anything that is not a SteamID64
# here, where the value can still be corrected.
check_admin_steam_ids() {
  [[ -n $PORTAL_ADMIN_STEAM_IDS ]] || {
    note "no Steam portal operators; administration stays proxy-only"
    return 0
  }
  local entry valid=0
  local -a ids
  IFS=',' read -ra ids <<<"$PORTAL_ADMIN_STEAM_IDS"
  for entry in "${ids[@]}"; do
    entry=${entry//[[:space:]]/}
    if [[ -z $entry ]]; then
      problem "PORTAL_ADMIN_STEAM_IDS contains an empty entry; remove the stray comma"
    elif [[ $entry =~ ^7[0-9]{16}$ ]]; then
      valid=$((valid + 1))
    else
      problem "invalid SteamID64 in PORTAL_ADMIN_STEAM_IDS: $entry. A SteamID64 is 17 digits beginning with 7, the trailing number in a profile URL such as https://steamcommunity.com/profiles/76561198000000001."
    fi
  done
  ((valid == 0)) ||
    note "Steam portal operators: $valid; the proxy must not challenge /admin with auth_basic"
  return 0
}

preflight() {
  step "Preflight"
  [[ $(uname -s) == Linux ]] || problem "this installer supports Linux hosts only"
  check_tools
  check_base_url
  check_proxy_cidr
  check_proxy_not_bridge_gateway
  check_bind
  check_auth_header
  check_agent_bridge_switch
  check_agent_runner
  check_world_root
  check_server_docker_dir
  check_script_dir
  check_allowed_worlds
  check_admin_steam_ids
  if [[ -z $install_root && $EUID -ne 0 ]] && ! $dry_run; then
    problem "installing to $etc_dir and $unit_dir requires root; re-run with sudo"
  fi
  if ((${#problems[@]} > 0)); then
    printf '\n%s\n' "Preflight found ${#problems[@]} problem(s):" >&2
    local p
    for p in "${problems[@]}"; do printf '  - %s\n' "$p" >&2; done
    exit 1
  fi
  note "preflight passed"
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

ensure_agent_identity() {
  step "Agent identity"
  if [[ -n $install_root ]]; then
    note "staging prefix set; skipping user and group creation"
    return 0
  fi
  if getent group "$AGENT_GROUP" >/dev/null; then
    note "group $AGENT_GROUP exists"
  else
    run groupadd --system "$AGENT_GROUP"
    note "created group $AGENT_GROUP"
  fi
  if getent passwd "$AGENT_USER" >/dev/null; then
    note "user $AGENT_USER exists"
  else
    run useradd --system --gid "$AGENT_GROUP" \
      --home-dir "$PORTAL_AGENT_SOCKET_DIR" --no-create-home \
      --shell /usr/sbin/nologin "$AGENT_USER"
    note "created user $AGENT_USER"
  fi
  local group
  local -a requested_groups
  IFS=',' read -ra requested_groups <<<"$AGENT_EXTRA_GROUPS"
  for group in "${requested_groups[@]}"; do
    group=${group//[[:space:]]/}
    [[ -n $group ]] || continue
    if getent group "$group" >/dev/null; then
      run usermod --append --groups "$group" "$AGENT_USER"
      note "added $AGENT_USER to $group"
    else
      warn "supplementary group '$group' does not exist; skipped"
    fi
  done
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
    printf '\n'
  fi
}

# Secrets are generated once. Regenerating the CSRF secret invalidates live
# sessions and regenerating the agent token breaks the portal/agent pair until
# both restart, so an existing file is always preserved.
ensure_secret() {
  local path=$1 label=$2 dir
  dir=$(dirname -- "$path")
  # Without search permission on the directory a missing file and an
  # unreadable one are indistinguishable. Reporting "generated" there would
  # understate what a real root run does, so say what is actually known.
  if [[ ! -f $path && -d $dir && ! -x $dir ]]; then
    warn "cannot determine whether $label exists at $path without root; a real install preserves any existing secret"
    return 0
  fi
  if [[ -f $path ]]; then
    local length
    length=$(tr -d '[:space:]' <"$path" | wc -c)
    if ((length < 32)); then
      die "$label at $path holds only $length bytes; it must contain at least 32. Remove it deliberately to regenerate."
    fi
    note "$label already present, preserved"
    return 0
  fi
  generate_secret | install_file "$path" 0640 "root:$AGENT_GROUP"
  note "generated $label"
}

ensure_secrets() {
  step "Shared secrets"
  run mkdir -p -- "$(resolved_etc)"
  $dry_run || chmod 0750 "$(resolved_etc)" 2>/dev/null || true
  ensure_secret "$(csrf_secret_file)" "CSRF secret"
  # Same mode and ownership as the CSRF secret: the portal reads it through a
  # read-only bind mount, and rotating it only logs every operator out.
  ensure_secret "$(admin_token_file)" "admin token"
  ensure_secret "$(agent_token_file)" "agent token"
  # Generated even when the bridge is off, so compose has a file to mount in both
  # states and enabling it later is one config line and a restart rather than a
  # secret ceremony. An unread 0640 token costs nothing; a conditional mount would
  # cost a compose file that is only valid in one configuration.
  ensure_secret "$(agent_bridge_token_file)" "agent bridge token"
}

build_agent_binary() {
  step "Agent binary"
  local dest
  dest=$(resolved_bin)/valheim-portal
  if $skip_build; then
    [[ -x $dest ]] || die "--skip-build was given but $dest is not an executable"
    note "reusing $dest"
    return 0
  fi
  local staged=$portal_dir/dist/valheim-portal
  run mkdir -p -- "$portal_dir/dist"
  # -buildvcs=false keeps the build reproducible when the checkout sits inside
  # an unrelated version control tree, so the identity is stamped explicitly.
  run env CGO_ENABLED=0 go build -trimpath -buildvcs=false \
    -ldflags="-s -w -X github.com/neuralyze/valheim-portal/internal/version.Version=$(resolve_version)" \
    -o "$staged" "$portal_dir/cmd/valheim-portal"
  run mkdir -p -- "$(resolved_bin)"
  run install -m 0755 "$staged" "$dest"
  note "installed $dest"
}

# Built whenever the bridge is on, because both ways of running the runner need
# the same executable: the poller and the on-demand oneshot are two units over one
# binary, not two programs.
build_runner_binary() {
  [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} == true ]] || return 0
  step "Agent runner binary"
  local dest
  dest=$(resolved_bin)/valheim-agent-runner
  if $skip_build; then
    [[ -x $dest ]] || die "--skip-build was given but $dest is not an executable"
    note "reusing $dest"
    return 0
  fi
  local staged=$portal_dir/dist/valheim-agent-runner
  run mkdir -p -- "$portal_dir/dist"
  run env CGO_ENABLED=0 go build -trimpath -buildvcs=false \
    -ldflags="-s -w -X github.com/neuralyze/valheim-portal/internal/version.Version=$(resolve_version)" \
    -o "$staged" "$portal_dir/cmd/agent-runner"
  run mkdir -p -- "$(resolved_bin)"
  run install -m 0755 "$staged" "$dest"
  note "installed $dest"
}

# One environment file for both units, so an on-demand run and the poller cannot
# disagree about which portal they drive or which token they present.
write_runner_env() {
  [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} == true ]] || return 0
  step "Agent runner environment"
  local group=$AGENT_GROUP
  [[ -n $install_root ]] || group=$(id -gn "$AGENT_RUNNER_USER" 2>/dev/null || printf '%s' "$AGENT_GROUP")
  install_file "$(runner_env_file)" 0640 "root:$group" <<ENV
# Generated by scripts/install-portal.sh. Read by both valheim-agent-runner.service
# (polling) and valheim-agent-runner-once.service (on demand).
PORTAL_BASE_URL=http://$PORTAL_BIND_ADDR:$PORTAL_BIND_PORT
PORTAL_AGENT_BRIDGE_TOKEN_FILE=$(host_agent_bridge_token_file)
AGENT_RUNNER_STATE=/var/lib/valheim-agent-runner/cursor
AGENT_RUNNER_OMP=${AGENT_RUNNER_OMP:-omp}
AGENT_RUNNER_MODEL=$AGENT_RUNNER_MODEL
ENV
  note "wrote $(runner_env_file)"
}

write_runner_units() {
  [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} == true ]] || return 0
  step "Agent runner services"
  local home
  home=$(getent passwd "$AGENT_RUNNER_USER" 2>/dev/null | cut -d: -f6)
  [[ -n $home ]] || home=/home/$AGENT_RUNNER_USER
  # ProtectHome cannot be on: omp keeps the model login in this user's home, and
  # the runner holds no credentials of its own by design. The account is the
  # boundary instead - it is not the agent account, and it cannot reach the world
  # tree or the docker socket.
  local common group
  group=$(id -gn "$AGENT_RUNNER_USER" 2>/dev/null || printf '%s' "$AGENT_RUNNER_USER")
  common="User=$AGENT_RUNNER_USER
Group=$group
# Needed to read the bridge token, which is mode 0640 root:$AGENT_GROUP.
SupplementaryGroups=$AGENT_GROUP
Environment=HOME=$home
EnvironmentFile=$(host_runner_env_file)
StateDirectory=valheim-agent-runner
StateDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/var/lib/valheim-agent-runner"

  install_file "$(resolved_unit)/valheim-agent-runner.service" 0644 root:root <<UNIT
# Generated by scripts/install-portal.sh. Do not edit; re-run the installer.
#
# The polling half: reads the operator conversation every $AGENT_RUNNER_POLL and asks
# a model what to do. Enabled only when AGENT_RUNNER_SERVICE=true. For a portal
# nobody is watching, prefer valheim-agent-runner-once.service.
[Unit]
Description=Valheim Portal agent runner (polling)
After=network.target docker.service
Wants=valheim-portal-agent.service

[Service]
Type=simple
$common
ExecStart=$bin_dir/valheim-agent-runner -poll $AGENT_RUNNER_POLL
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

  install_file "$(resolved_unit)/valheim-agent-runner-once.service" 0644 root:root <<UNIT
# Generated by scripts/install-portal.sh. Do not edit; re-run the installer.
#
# The on-demand half. Never enabled; start it when you want one pass:
#
#   sudo systemctl start valheim-agent-runner-once
#   journalctl -u valheim-agent-runner-once -n 20 --no-pager
#
# It shares its configuration with the polling unit, so what you test on demand is
# what the poller would do.
[Unit]
Description=Valheim Portal agent runner (one pass)
After=network.target

[Service]
Type=oneshot
$common
ExecStart=$bin_dir/valheim-agent-runner -once
UNIT
  note "installed both runner units; polling is $([[ ${AGENT_RUNNER_SERVICE,,} == true ]] && printf enabled || printf 'available but not enabled')"
}

start_runner() {
  [[ ${PORTAL_ENABLE_AGENT_BRIDGE,,} == true ]] || return 0
  step "Agent runner"
  if [[ -n $install_root ]]; then
    note "staging prefix set; not touching systemd"
    return 0
  fi
  run systemctl daemon-reload
  if [[ ${AGENT_RUNNER_SERVICE,,} == true ]]; then
    run systemctl enable valheim-agent-runner
    run systemctl restart valheim-agent-runner
    note "polling runner started; journalctl -u valheim-agent-runner -f"
  else
    # Disabled rather than left as found, so turning the switch off actually stops
    # a poller a previous install started.
    run systemctl disable --now valheim-agent-runner
    note "on demand only; sudo systemctl start valheim-agent-runner-once"
  fi
}

write_agent_env() {
  step "Agent environment"
  install_file "$(agent_env_file)" 0640 "root:$AGENT_GROUP" <<ENV
# Generated by scripts/install-portal.sh. Managed file; edit and restart
# valheim-portal-agent to change the agent's fixed operating parameters.
AGENT_SOCKET=$PORTAL_AGENT_SOCKET_DIR/agent.sock
AGENT_TOKEN_FILE=$(host_agent_token_file)
AGENT_SCRIPT_DIR=$AGENT_SCRIPT_DIR
AGENT_WORLD_ROOT=$VALHEIM_WORLD_ROOT
# Read by hostops/lib/common.sh and tools/portal_paths.py. The operation
# scripts inherit this environment, so it is what they drive compose against.
VALHEIM_SERVER_DOCKER_DIR=$VALHEIM_SERVER_DOCKER_DIR
# The agent refuses any world outside this allowlist. Keep it synchronized
# with the servers this host actually controls, then restart the agent.
AGENT_ALLOWED_WORLDS=$AGENT_ALLOWED_WORLDS
ENV
}

# The agent writes generated access lists into each world's config_merged and
# valheim.env, and stages new world directories under the world root. Its unit
# is sandboxed to exactly this tree, so grant the agent user an ACL here rather
# than loosening the group bits on directories the operator owns. The default
# ACL makes every world created later inherit the same access.
ensure_world_acls() {
  step "World directory access"
  if ! command -v setfacl >/dev/null 2>&1; then
    warn "setfacl is unavailable: install acl, or the agent cannot write access lists"
    return 0
  fi
  local root=$VALHEIM_WORLD_ROOT
  if [[ ! -d $root ]]; then
    warn "world root $root does not exist yet; re-run the installer after creating it"
    return 0
  fi
  # Grant both writers. A world is written by the agent (portal operations) and
  # by uid WORLD_OWNER_UID (the container's PUID, which an operator shares), and
  # either one can be the creator of a given file: the agent when the portal
  # provisions a world, the container when it chowns its own mounts on first
  # start. Naming both, plus a default entry so anything created later inherits
  # them, makes access independent of who happened to create the file.
  local acl="u:$AGENT_USER:rwx,u:$WORLD_OWNER_UID:rwx"
  run setfacl -m "$acl" "$root"
  run setfacl -d -m "$acl" "$root"
  # Only allowlisted worlds get an ACL: the world root also holds archives and
  # backups the agent has no business writing.
  local world path
  while IFS= read -r world; do
    [[ -n $world ]] || continue
    path=$root/$world
    if [[ ! -d $path ]]; then
      warn "allowlisted world $world has no directory under $root"
      continue
    fi
    # Recursive, because the container and the mod tooling create files inside
    # config_merged long after the world was provisioned.
    run setfacl -R -m "$acl" "$path"
    run setfacl -R -d -m "$acl" "$path"
  done < <(tr ',' '\n' <<<"$AGENT_ALLOWED_WORLDS" | tr -d ' ')
  note "granted $AGENT_USER and uid $WORLD_OWNER_UID write access to the world root and $AGENT_ALLOWED_WORLDS"
}

write_agent_unit() {
  step "Agent service"
  local supplementary_groups=${AGENT_EXTRA_GROUPS//,/ }
  # Resolved once here rather than inside the heredoc so a bad script directory
  # is reported by preflight, not by a stray cd error in the generated unit.
  local tools_dir
  tools_dir=$(cd -- "$AGENT_SCRIPT_DIR/.." && pwd)/tools
  install_file "$(resolved_unit)/valheim-portal-agent.service" 0644 root:root <<UNIT
# Generated by scripts/install-portal.sh. Do not edit; re-run the installer.
[Unit]
Description=Valheim Portal restricted operations agent
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=$AGENT_USER
Group=$AGENT_GROUP
SupplementaryGroups=$supplementary_groups
EnvironmentFile=$etc_dir/agent.env
Environment=HOME=$PORTAL_AGENT_SOCKET_DIR
ExecStart=$bin_dir/valheim-portal --mode=agent
Restart=on-failure
RestartSec=5
RuntimeDirectory=${PORTAL_AGENT_SOCKET_DIR#/run/}
RuntimeDirectoryMode=0750
# The portal container bind-mounts this directory. Without preservation systemd
# deletes and recreates it on every agent restart, and the running container keeps
# its mount on the deleted inode: the socket exists on the host, /run/agent is empty
# inside the container, and every verb fails with "no such file or directory" until
# the container is recreated. Measured, not theorised - it broke a live portal.
RuntimeDirectoryPreserve=yes
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$PORTAL_AGENT_SOCKET_DIR $VALHEIM_WORLD_ROOT
# ProtectSystem=strict already makes the filesystem read-only, but ProtectHome
# makes /home and /root inaccessible outright, so every directory the agent
# reads is named here or a checkout under a home directory disappears from the
# unit's view. The scripts read their Python helpers from the sibling tools/
# directory and drive the compose project in the valheim-server-docker tree.
ReadOnlyPaths=$AGENT_SCRIPT_DIR $tools_dir $VALHEIM_SERVER_DOCKER_DIR

[Install]
WantedBy=multi-user.target
UNIT
}

# Compose reads this file. It carries paths, the resolved agent group ID the
# container joins to reach the socket, and nothing secret beyond an optional
# Steam Web API key; the HMAC secret and CSRF secret stay in mounted files.
write_compose_env() {
  step "Compose environment"
  local gid=${PORTAL_AGENT_GID:-}
  if [[ -z $gid ]]; then
    if [[ -n $install_root ]]; then
      gid=0
    else
      gid=$(getent group "$AGENT_GROUP" | cut -d: -f3)
      [[ -n $gid ]] || die "cannot resolve GID for group $AGENT_GROUP"
    fi
  fi
  local target
  target=$(compose_env_file)
  # The previous file may hold hand-tuned values, so never discard it silently.
  if [[ -f $target ]] && ! $dry_run; then
    cp -a -- "$target" "$target.replaced"
    note "kept the previous file as $(basename -- "$target").replaced"
  fi
  install_file "$target" 0640 "$(id -un):$(id -gn)" <<ENV
# Generated by scripts/install-portal.sh for docker compose. The only credential
# it can carry is PORTAL_STEAM_API_KEY; the portal reads the CSRF secret, the
# admin token, and the agent token from mounted files.
# Stamped into the container image so GET /version identifies the deployment.
PORTAL_VERSION=$(resolve_version)
PORTAL_BIND_ADDR=$PORTAL_BIND_ADDR
PORTAL_BIND_PORT=$PORTAL_BIND_PORT
PORTAL_PUBLIC_BASE_URL=$PORTAL_PUBLIC_BASE_URL
PORTAL_TRUSTED_PROXY_CIDR=$PORTAL_TRUSTED_PROXY_CIDR
PORTAL_AUTH_HEADER=$PORTAL_AUTH_HEADER
PORTAL_ADMIN_STEAM_IDS=$PORTAL_ADMIN_STEAM_IDS
PORTAL_REQUIRE_DEVICE_CODE=$PORTAL_REQUIRE_DEVICE_CODE
PORTAL_SOURCE_URL=$PORTAL_SOURCE_URL
PORTAL_AGENT_GID=$gid
PORTAL_AGENT_SOCKET_DIR=$PORTAL_AGENT_SOCKET_DIR
PORTAL_STEAM_API_KEY=$PORTAL_STEAM_API_KEY
PORTAL_CSRF_SECRET_FILE=$(host_csrf_secret_file)
PORTAL_ADMIN_TOKEN_FILE=$(host_admin_token_file)
PORTAL_AGENT_TOKEN_FILE=$(host_agent_token_file)
PORTAL_AGENT_BRIDGE_TOKEN_FILE=$(host_agent_bridge_token_file)
# Empty leaves the bridge disabled: the portal reads this path, and compose mounts
# the host file regardless.
PORTAL_AGENT_BRIDGE_TOKEN_PATH=$(container_agent_bridge_token_path)
VALHEIM_WORLD_ROOT=$VALHEIM_WORLD_ROOT
PORTAL_DEFAULT_JOIN_HOST=$PORTAL_DEFAULT_JOIN_HOST
PORTAL_DEFAULT_GAME_PORT=$PORTAL_DEFAULT_GAME_PORT
PORTAL_DEFAULT_PLAYER_LIMIT=$PORTAL_DEFAULT_PLAYER_LIMIT
PORTAL_DEFAULT_BACKUP_INTERVAL=$PORTAL_DEFAULT_BACKUP_INTERVAL
PORTAL_DEFAULT_BACKUP_AGE_DAYS=$PORTAL_DEFAULT_BACKUP_AGE_DAYS
PORTAL_DEFAULT_BACKUP_COUNT=$PORTAL_DEFAULT_BACKUP_COUNT
ENV
}

start_agent() {
  step "Starting agent"
  if [[ -n $install_root ]]; then
    note "staging prefix set; not touching systemd"
    return 0
  fi
  run systemctl daemon-reload
  run systemctl enable valheim-portal-agent
  run systemctl restart valheim-portal-agent
  $dry_run && return 0
  local socket=$PORTAL_AGENT_SOCKET_DIR/agent.sock
  for _ in $(seq 1 50); do
    [[ -S $socket ]] && break
    sleep 0.2
  done
  [[ -S $socket ]] || die "agent socket $socket did not appear; check journalctl -u valheim-portal-agent"
  note "agent socket ready at $socket"
}

start_portal() {
  step "Starting portal"
  if [[ -n $install_root ]]; then
    note "staging prefix set; not starting containers"
    return 0
  fi
  if [[ ! -x $portal_dir/dist/ValheimProfileSync.exe && ! -f $portal_dir/dist/ValheimProfileSync.exe ]]; then
    warn "dist/ValheimProfileSync.exe is absent; the portal serves the client download from it. Build it with scripts/build-windows-client.sh."
    run mkdir -p -- "$portal_dir/dist"
  fi
  run env -C "$portal_dir" docker compose up -d --build
  $dry_run && return 0
  # A container that predates the agent's last restart holds its bind mount on a
  # RuntimeDirectory systemd has since deleted: the socket is on the host and
  # /run/agent is empty inside. `up -d` will not recreate it, because nothing about
  # the image or the config changed. Repair it here rather than leaving an operator
  # to discover it as "no such file or directory" from a chat message.
  local socket=$PORTAL_AGENT_SOCKET_DIR/agent.sock
  if [[ -S $socket ]] && ! docker compose -f "$portal_dir/compose.yaml" exec -T portal test -S /run/agent/agent.sock 2>/dev/null; then
    warn "the running container cannot see $socket; recreating it"
    run env -C "$portal_dir" docker compose up -d --force-recreate portal
  fi
}

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

# The deployed agent environment file is the one place a live host can still
# point at a pre-relocation script directory. The installer never rewrites an
# existing file's values behind an operator's back, and the running agent reads
# that file rather than this repository, so an upgraded checkout keeps working
# while the agent silently answers "operation unavailable" to everything.
# Detect it here and print the exact line to change.
check_deployed_script_dir() {
  local env_file deployed missing=() name
  env_file=$(agent_env_file)
  [[ -f $env_file ]] || {
    note "no deployed agent environment at $env_file yet"
    return 0
  }
  deployed=$(sed -nE 's/^[[:space:]]*AGENT_SCRIPT_DIR=(.*)$/\1/p' "$env_file" | tail -n 1)
  deployed=${deployed%\"}
  deployed=${deployed#\"}
  if [[ -z $deployed ]]; then
    warn "$env_file sets no AGENT_SCRIPT_DIR; the agent refuses to start without one."
    warn "Add exactly one line:  AGENT_SCRIPT_DIR=$AGENT_SCRIPT_DIR"
    return 1
  fi
  while IFS= read -r name; do
    [[ -n $name ]] || continue
    [[ -f $deployed/$name ]] || missing+=("$name")
  done < <(required_scripts)
  if ((${#missing[@]} > 0)); then
    warn "the running agent's AGENT_SCRIPT_DIR is stale: $deployed is missing ${#missing[@]} of the world operation scripts (${missing[0]} among them)."
    warn "They now ship in this repository. Change exactly one line in $env_file:"
    warn "  AGENT_SCRIPT_DIR=$AGENT_SCRIPT_DIR"
    warn "then run: systemctl restart valheim-portal-agent"
    return 1
  fi
  if [[ ! -f $deployed/lib/common.sh || ! -f $deployed/../tools/valheim_mods.py ]]; then
    warn "the running agent's AGENT_SCRIPT_DIR has the scripts but not their helpers: $deployed lacks lib/common.sh or a sibling tools/ directory."
    warn "Point it at a complete hostops/ tree. Change exactly one line in $env_file:"
    warn "  AGENT_SCRIPT_DIR=$AGENT_SCRIPT_DIR"
    warn "then run: systemctl restart valheim-portal-agent"
    return 1
  fi
  # The same migration adds a variable the file predating it cannot have. An
  # agent.env whose script directory is correct but whose compose checkout is
  # unset fails every lifecycle operation with exit 78 instead.
  local docker_dir
  docker_dir=$(sed -nE 's/^[[:space:]]*VALHEIM_SERVER_DOCKER_DIR=(.*)$/\1/p' "$env_file" | tail -n 1)
  if [[ -z $docker_dir ]]; then
    warn "$env_file sets no VALHEIM_SERVER_DOCKER_DIR; every start, stop and build exits 78."
    warn "Add exactly one line:  VALHEIM_SERVER_DOCKER_DIR=${VALHEIM_SERVER_DOCKER_DIR:-/srv/valheim-server-docker}"
    warn "then run: systemctl restart valheim-portal-agent"
    return 1
  fi
  note "deployed agent environment is current: AGENT_SCRIPT_DIR=$deployed"
  return 0
}

verify() {
  step "Verification"
  local failures=0
  # Runs even under a staging prefix or a dry run: it only reads a file, and a
  # stale script directory is precisely what an operator runs `verify` to find.
  check_deployed_script_dir || failures=$((failures + 1))
  if [[ -n $install_root ]] || $dry_run; then
    note "staging or dry run; skipping live checks"
    return 0
  fi
  local base=http://$PORTAL_BIND_ADDR:$PORTAL_BIND_PORT
  for _ in $(seq 1 30); do
    curl -fsS --max-time 3 "$base/healthz" >/dev/null 2>&1 && break
    sleep 1
  done
  if curl -fsS --max-time 5 "$base/healthz" >/dev/null; then
    note "healthz responded on $base"
  else
    warn "healthz did not respond on $base"
    failures=$((failures + 1))
  fi
  if curl -fsS --max-time 5 "$base/readyz" >/dev/null; then
    note "readyz responded: the database answered and the agent socket is present inside the container"
  else
    warn "readyz failed: $(curl -sS --max-time 5 "$base/readyz" 2>/dev/null | head -1)"
    failures=$((failures + 1))
  fi

  # Report what is actually deployed. "dev" means the running image was built
  # from an untagged tree and cannot be matched to a release.
  local deployed
  deployed=$(curl -fsS --max-time 5 "$base/version" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || printf '')
  if [[ -n $deployed ]]; then
    note "deployed version: $deployed"
    if [[ $deployed == dev ]]; then
      warn "the running portal reports 'dev'; tag a release and re-run the installer to stamp a version"
    fi
  else
    warn "could not read $base/version; the running portal predates version reporting"
  fi

  # The bind address is where NAT delivers every request, so it is the one
  # place an attacker on this host reaches the portal directly. A request that
  # carries only the identity header must be refused there: if it is not, the
  # running portal has no admin token and administration is a header away.
  local direct
  direct=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -H "$PORTAL_AUTH_HEADER: installer-token-probe" \
    "$base/admin" 2>/dev/null || printf '000')
  case $direct in
    401 | 403) note "header-only administration refused on $base (HTTP $direct)" ;;
    000) warn "could not reach $base/admin to test header-only administration" ;;
    *)
      printf '\n' >&2
      warn "CRITICAL: $base/admin returned HTTP $direct for a request carrying only $PORTAL_AUTH_HEADER."
      warn "The running portal is not enforcing X-Portal-Admin-Token. Recreate the container so it picks up PORTAL_ADMIN_TOKEN_FILE."
      failures=$((failures + 1))
      ;;
  esac

  # Administration additionally requires the admin token, which only the proxy
  # can supply. Confirm the public edge cannot supply an identity either, since
  # that is the factor a misconfigured location leaks. A 2xx or 3xx here means
  # the proxy forwards a browser-controlled identity.
  if [[ $PORTAL_PUBLIC_BASE_URL == https://* ]]; then
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
      -H "$PORTAL_AUTH_HEADER: installer-spoof-probe" \
      "$PORTAL_PUBLIC_BASE_URL/admin" 2>/dev/null || printf '000')
    case $code in
      000)
        warn "could not reach $PORTAL_PUBLIC_BASE_URL/admin to test identity spoofing; run this check once the proxy and DNS are live"
        ;;
      401 | 403 | 404)
        note "identity spoofing refused at the public edge (HTTP $code)"
        ;;
      2?? | 3??)
        printf '\n' >&2
        warn "CRITICAL: $PORTAL_PUBLIC_BASE_URL/admin returned HTTP $code for a request that supplied its own $PORTAL_AUTH_HEADER."
        warn "The proxy is forwarding a browser-controlled administrative identity. Set '$PORTAL_AUTH_HEADER' explicitly from the verified user on every proxied route before serving traffic."
        failures=$((failures + 1))
        ;;
      *) warn "unexpected HTTP $code from the public admin spoofing probe" ;;
    esac
  fi
  ((failures == 0)) || die "$failures verification check(s) failed"
  note "verification passed"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
  step "Removing portal"
  if [[ -z $install_root ]]; then
    if command -v docker >/dev/null 2>&1; then
      if $purge; then
        run env -C "$portal_dir" docker compose down --volumes
      else
        run env -C "$portal_dir" docker compose down
      fi
    fi
    run systemctl disable --now valheim-portal-agent || true
    # A poller left running would keep driving a portal that no longer exists.
    run systemctl disable --now valheim-agent-runner || true
  fi
  run rm -f -- "$(resolved_unit)/valheim-portal-agent.service" \
    "$(resolved_unit)/valheim-agent-runner.service" \
    "$(resolved_unit)/valheim-agent-runner-once.service"
  if [[ -z $install_root ]]; then
    run systemctl daemon-reload
  fi
  run rm -f -- "$(resolved_bin)/valheim-portal" "$(resolved_bin)/valheim-agent-runner"
  if $purge; then
    warn "purging installer-managed secrets and configuration in $(resolved_etc)"
    # Remove only what this installer creates. Operators keep unrelated
    # material here (proxy passwords, for example) that must survive.
    run rm -f -- "$(csrf_secret_file)" "$(admin_token_file)" "$(agent_token_file)" \
      "$(agent_bridge_token_file)" "$(agent_env_file)" "$(runner_env_file)"
    if $dry_run; then
      printf '    [dry-run] rmdir %s if empty\n' "$(resolved_etc)"
    elif rmdir -- "$(resolved_etc)" 2>/dev/null; then
      note "removed the now-empty $(resolved_etc)"
    else
      note "kept $(resolved_etc); it still holds files this installer did not create"
    fi
    run rm -f -- "$(compose_env_file)" "$(compose_env_file).replaced"
    note "world data under ${VALHEIM_WORLD_ROOT:-the world root} was not touched"
  else
    note "retained secrets, configuration, and the portal data volume"
    note "re-run with --purge to remove them"
  fi
}

summary() {
  cat <<SUMMARY

Installed. Before serving public traffic:

 1. Terminate TLS for $PORTAL_PUBLIC_BASE_URL at a proxy that reaches
    $PORTAL_BIND_ADDR:$PORTAL_BIND_PORT from $PORTAL_TRUSTED_PROXY_CIDR.
 2. On EVERY proxied route, set '$PORTAL_AUTH_HEADER' explicitly from the
    verified user, and to an empty value on unauthenticated routes. nginx
    forwards unrecognised client headers unchanged, so omitting it is a
    bypass rather than a safe default. deploy/Caddyfile and
    deploy/nginx-*.conf show both patterns.
 3. On the admin routes only, also set:
      X-Portal-Admin-Token: <contents of $(host_admin_token_file)>
    The portal compares it in constant time and refuses administration
    without it, so the identity header alone can no longer grant access.
    A browser never sends this header; only the proxy does.
 4. Build the client with scripts/build-windows-client.sh so the portal can
    serve dist/ValheimProfileSync.exe.
 5. Re-run 'scripts/install-portal.sh verify' once DNS and the proxy are
    live; it probes the public edge for identity spoofing and confirms the
    portal refuses header-only administration.

Agent logs:  journalctl -u valheim-portal-agent -f
Portal logs: docker compose -f $portal_dir/compose.yaml logs -f
SUMMARY
}

main() {
  load_config
  [[ -n $PORTAL_DEFAULT_JOIN_HOST ]] || PORTAL_DEFAULT_JOIN_HOST=${PORTAL_PUBLIC_BASE_URL#*://}
  PORTAL_DEFAULT_JOIN_HOST=${PORTAL_DEFAULT_JOIN_HOST%%/*}

  case $action in
    print-config)
      print_config
      ;;
    verify)
      check_base_url
      ((${#problems[@]} == 0)) || die "${problems[0]}"
      verify
      ;;
    uninstall)
      uninstall
      ;;
    install)
      $dry_run && log "Dry run: no host changes will be made."
      preflight
      ensure_agent_identity
      ensure_secrets
      build_agent_binary
      write_agent_env
      write_agent_unit
      build_runner_binary
      write_runner_env
      write_runner_units
      ensure_world_acls
      write_compose_env
      start_agent
      start_portal
      start_runner
      verify
      $dry_run || summary
      ;;
  esac
}

main
