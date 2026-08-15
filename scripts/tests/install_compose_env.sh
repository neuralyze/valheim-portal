#!/usr/bin/env bash
# Proves the installer carries deploy/install.conf into the compose environment unchanged, and that
# the agent bridge switch is the only thing that turns the bridge on.
#
# This exists because of a real incident: install.conf on the live host had drifted from a .env
# somebody had hand-corrected, and reinstalling silently reset PORTAL_TRUSTED_PROXY_CIDR to a stale
# value. The portal then ignored the proxy's identity header and refused every admin request, which
# reads as a broken sign-in rather than a configuration fault. Nothing checked that a configured
# value survived the trip into .env, so nothing could catch it.
#
# Runs entirely under a staging prefix: no users, no systemd, no containers, no live deployment.
# Run: bash scripts/tests/install_compose_env.sh
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
INSTALLER="$REPO/scripts/install-portal.sh"

tmp=$(mktemp -d /tmp/install-env.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

failures=0

# A CIDR that is deliberately not a Docker bridge gateway, so preflight needs no allowance flag.
write_config() { # <path> [extra lines...]
  local path=$1; shift
  mkdir -p -- "$(dirname -- "$path")"
  cat >"$path" <<EOF
PORTAL_PUBLIC_BASE_URL=https://portal.example.test
PORTAL_TRUSTED_PROXY_CIDR=10.9.9.9/32
PORTAL_AUTH_HEADER=X-Forwarded-User
PORTAL_BIND_PORT=18099
PORTAL_ADMIN_STEAM_IDS=76561190000000000
PORTAL_REQUIRE_DEVICE_CODE=false
VALHEIM_WORLD_ROOT=$tmp/worlds
VALHEIM_SERVER_DOCKER_DIR=$tmp/server-docker
AGENT_SCRIPT_DIR=$REPO/hostops
AGENT_ALLOWED_WORLDS=Testworld
EOF
  local line
  for line in "$@"; do printf '%s\n' "$line" >>"$path"; done
}

stage() { # <root> <config> -> installs into the staging prefix
  local root=$1 config=$2
  mkdir -p -- "$root" "$tmp/worlds/Testworld" "$tmp/server-docker"
  : >"$tmp/server-docker/docker-compose.yaml"
  # Provisioning reads the container PGID from this file, so preflight requires it.
  printf 'PGID=1000\nPUID=1000\n' >"$tmp/server-docker/default.env"
  PORTAL_INSTALL_ROOT="$root" bash "$INSTALLER" install --config "$config" >"$root/install.log" 2>&1 || {
    echo "FAIL: staged install failed; log follows" >&2
    tail -20 "$root/install.log" >&2
    failures=$((failures + 1))
    return 1
  }
}

value_of() { # <env file> <key>
  local file=$1 key=$2
  sed -n "s/^$key=//p" "$file" | head -1
}

expect_value() { # <env file> <key> <expected>
  local file=$1 key=$2 want=$3 got
  got=$(value_of "$file" "$key")
  if [[ $got != "$want" ]]; then
    echo "FAIL: $key = '$got', want '$want'" >&2
    failures=$((failures + 1))
  fi
}

# --- the bridge is off unless asked for ------------------------------------------------------
off=$tmp/off
write_config "$tmp/off.conf"
if stage "$off" "$tmp/off.conf"; then
  env_file=$off/compose.env
  expect_value "$env_file" PORTAL_AGENT_BRIDGE_TOKEN_PATH ""
  expect_value "$env_file" PORTAL_AGENT_BRIDGE_TOKEN_FILE /etc/valheim-portal/agent-bridge-token

  # The token exists even when the bridge is off, so compose has a file to mount and enabling it
  # later is a config line and a restart.
  token=$off/etc/valheim-portal/agent-bridge-token
  if [[ ! -f $token ]]; then
    echo "FAIL: no bridge token generated at $token" >&2
    failures=$((failures + 1))
  elif (($(tr -d '[:space:]' <"$token" | wc -c) < 32)); then
    echo "FAIL: bridge token shorter than the 32 characters the portal requires" >&2
    failures=$((failures + 1))
  fi
fi

# --- and on when it is --------------------------------------------------------------------------
on=$tmp/on
write_config "$tmp/on.conf" "PORTAL_ENABLE_AGENT_BRIDGE=true"
if stage "$on" "$tmp/on.conf"; then
  expect_value "$on/compose.env" PORTAL_AGENT_BRIDGE_TOKEN_PATH /run/secrets/agent-bridge-token
fi

# --- every configured value survives the trip into .env unchanged -------------------------------
# The incident above was one of these lines silently disagreeing with its source.
if [[ -f $on/compose.env ]]; then
  expect_value "$on/compose.env" PORTAL_TRUSTED_PROXY_CIDR 10.9.9.9/32
  expect_value "$on/compose.env" PORTAL_REQUIRE_DEVICE_CODE false
  expect_value "$on/compose.env" PORTAL_PUBLIC_BASE_URL https://portal.example.test
  expect_value "$on/compose.env" PORTAL_AUTH_HEADER X-Forwarded-User
  expect_value "$on/compose.env" PORTAL_ADMIN_STEAM_IDS 76561190000000000
  expect_value "$on/compose.env" PORTAL_BIND_PORT 18099
  expect_value "$on/compose.env" VALHEIM_WORLD_ROOT "$tmp/worlds"
fi

# --- the runner: one binary, two units, and the poller off unless asked --------------------------
# Both ways of running it must be set up by an install, because an operator who has to hand-write a
# unit will hand-write a different one than the poller uses, and then on-demand testing proves
# nothing about what the poller does.
if [[ -d $on ]]; then
  for artefact in \
    usr/local/bin/valheim-agent-runner \
    etc/valheim-portal/agent-runner.env \
    etc/systemd/system/valheim-agent-runner.service \
    etc/systemd/system/valheim-agent-runner-once.service; do
    if [[ ! -f $on/$artefact ]]; then
      echo "FAIL: enabling the bridge did not install $artefact" >&2
      failures=$((failures + 1))
    fi
  done

  # The two units must agree on configuration, or on demand is not a rehearsal.
  for unit in valheim-agent-runner valheim-agent-runner-once; do
    unit_file=$on/etc/systemd/system/$unit.service
    [[ -f $unit_file ]] || continue
    if ! grep -qF "EnvironmentFile=/etc/valheim-portal/agent-runner.env" "$unit_file"; then
      echo "FAIL: $unit does not read the shared runner environment" >&2
      failures=$((failures + 1))
    fi
    # omp keeps the model login in a home directory, so the runner cannot run as the
    # agent account and must not have ProtectHome on.
    if grep -qE "^User=(root|valheim-agent)$" "$unit_file"; then
      echo "FAIL: $unit runs as an account with no model credentials" >&2
      failures=$((failures + 1))
    fi
    if grep -qF "ProtectHome=true" "$unit_file"; then
      echo "FAIL: $unit hides the home directory omp reads its login from" >&2
      failures=$((failures + 1))
    fi
  done

  # The oneshot exists so an operator can run one pass; a Type=simple oneshot would
  # report success the moment it started.
  if ! grep -qF "Type=oneshot" "$on/etc/systemd/system/valheim-agent-runner-once.service"; then
    echo "FAIL: the on-demand unit is not a oneshot" >&2
    failures=$((failures + 1))
  fi
  if ! grep -qE "ExecStart=.*valheim-agent-runner -once" "$on/etc/systemd/system/valheim-agent-runner-once.service"; then
    echo "FAIL: the on-demand unit does not run a single pass" >&2
    failures=$((failures + 1))
  fi
  if ! grep -qE "ExecStart=.*valheim-agent-runner -poll" "$on/etc/systemd/system/valheim-agent-runner.service"; then
    echo "FAIL: the polling unit does not poll" >&2
    failures=$((failures + 1))
  fi
fi

# A bridge that is off installs no runner at all: there is nothing for it to talk to.
if [[ -d $off ]]; then
  for artefact in usr/local/bin/valheim-agent-runner etc/systemd/system/valheim-agent-runner.service; do
    if [[ -e $off/$artefact ]]; then
      echo "FAIL: the disabled bridge still installed $artefact" >&2
      failures=$((failures + 1))
    fi
  done
fi

# --- a poller without a bridge is refused rather than left to poll a 503 ------------------------
orphan=$tmp/orphan
write_config "$tmp/orphan.conf" "AGENT_RUNNER_SERVICE=true"
mkdir -p -- "$orphan"
if PORTAL_INSTALL_ROOT="$orphan" bash "$INSTALLER" install --config "$tmp/orphan.conf" >"$tmp/orphan.log" 2>&1; then
  echo "FAIL: a polling runner was accepted with the bridge disabled" >&2
  failures=$((failures + 1))
elif ! grep -qF "answers 503" "$tmp/orphan.log"; then
  echo "FAIL: the refusal did not explain why: $(tail -3 "$tmp/orphan.log")" >&2
  failures=$((failures + 1))
fi

# --- a mistyped switch is refused, not read as off ----------------------------------------------
bad=$tmp/bad
write_config "$tmp/bad.conf" "PORTAL_ENABLE_AGENT_BRIDGE=yes"
mkdir -p -- "$bad"
if PORTAL_INSTALL_ROOT="$bad" bash "$INSTALLER" install --config "$tmp/bad.conf" >"$tmp/bad.log" 2>&1; then
  echo "FAIL: 'yes' was accepted as a bridge switch value" >&2
  failures=$((failures + 1))
elif ! grep -qF "must be true or false" "$tmp/bad.log"; then
  echo "FAIL: refusal did not name the problem: $(tail -3 "$tmp/bad.log")" >&2
  failures=$((failures + 1))
fi

# --- compose.yaml is valid in BOTH states -------------------------------------------------------
# The empty switch must leave a usable compose file: an interpolation error here would take the
# portal down on the next restart rather than leaving the bridge off.
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  for state in off on; do
    root=$tmp/$state
    [[ -f $root/compose.env ]] || continue
    if ! docker compose --env-file "$root/compose.env" -f "$REPO/compose.yaml" config >"$tmp/$state.config" 2>"$tmp/$state.err"; then
      echo "FAIL: compose.yaml is invalid with the bridge $state: $(head -2 "$tmp/$state.err")" >&2
      failures=$((failures + 1))
    fi
  done
  # And the switch must actually reach the container's environment.
  if [[ -f $tmp/on.config ]] && ! grep -qE "PORTAL_AGENT_BRIDGE_TOKEN_FILE: /run/secrets/agent-bridge-token" "$tmp/on.config"; then
    echo "FAIL: the enabled bridge did not reach the container environment" >&2
    failures=$((failures + 1))
  fi
else
  echo "note: docker compose unavailable; skipped the compose validity checks" >&2
fi

if ((failures != 0)); then
  echo "FAIL: $failures assertion(s) failed" >&2
  exit 1
fi
echo "PASS: install.conf reaches compose unchanged and the bridge switch is the only opt-in"
