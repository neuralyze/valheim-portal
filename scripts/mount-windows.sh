#!/usr/bin/env bash
set -euo pipefail

# Mounts the Windows gaming host's Valheim installations over CIFS.
#
# These mounts are how this machine reaches a real Valheim client install: the
# ValheimVR artifacts and the profile-sync client are assembled here on Linux
# against files that live on the Windows box, so nothing is built on Windows.
#
# Every value is host-specific, so nothing is defaulted to the original author's
# machine. Set them in the environment or a private wrapper.

usage() {
  cat >&2 <<'USAGE'
usage: VALHEIM_WINDOWS_HOST=<host> VALHEIM_WINDOWS_USER=<user> mount-windows.sh [SHARE...]

Mounts each SHARE from //$VALHEIM_WINDOWS_HOST under $VALHEIM_WINDOWS_MOUNT_ROOT.
Defaults to the four shares this project uses when no SHARE is given.

Environment:
  VALHEIM_WINDOWS_HOST        required  CIFS server, e.g. gaming-pc
  VALHEIM_WINDOWS_USER        required  account on that server
  VALHEIM_WINDOWS_MOUNT_ROOT  optional  default /mnt/valheim-windows
  VALHEIM_WINDOWS_CREDENTIALS optional  path to a credentials file; preferred
                                        over an interactive password prompt
  VALHEIM_WINDOWS_CIFS_VERS   optional  default 3.0

Requires sudo for mkdir and mount.
USAGE
  exit 2
}

[[ ${1:-} == -h || ${1:-} == --help ]] && usage

host=${VALHEIM_WINDOWS_HOST:?set VALHEIM_WINDOWS_HOST to the Windows machine hosting the Valheim installs}
user=${VALHEIM_WINDOWS_USER:?set VALHEIM_WINDOWS_USER to the account on that machine}
root=${VALHEIM_WINDOWS_MOUNT_ROOT:-/mnt/valheim-windows}
vers=${VALHEIM_WINDOWS_CIFS_VERS:-3.0}

shares=("$@")
if ((${#shares[@]} == 0)); then
  shares=(Valheim Valheim2 Valheim3 ValheimProfileSync)
fi

# A credentials file keeps the password out of the process list, which `-o
# password=` would expose to every user on the host.
options="uid=$(id -u),gid=$(id -g),vers=$vers"
if [[ -n ${VALHEIM_WINDOWS_CREDENTIALS:-} ]]; then
  [[ -r $VALHEIM_WINDOWS_CREDENTIALS ]] || { echo "credentials file is unreadable: $VALHEIM_WINDOWS_CREDENTIALS" >&2; exit 1; }
  options="credentials=$VALHEIM_WINDOWS_CREDENTIALS,$options"
else
  options="username=$user,$options"
fi

for share in "${shares[@]}"; do
  target="$root/$share"
  if mountpoint -q "$target" 2>/dev/null; then
    echo "already mounted: $target"
    continue
  fi
  sudo mkdir -p "$target"
  sudo mount -t cifs "//$host/$share" "$target" -o "$options"
  echo "mounted //$host/$share -> $target"
done
