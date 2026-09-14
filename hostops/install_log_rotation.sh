#!/usr/bin/env bash
# Installs hourly rotation for the host-side world logs. Idempotent: safe to
# re-run after every deploy, and re-running is how a config change reaches the
# host.
#
# Without this, collect_valheim_server_logs.sh appends forever. Docker's own
# json-file driver is capped (50m x 5) so the container side is bounded, but the
# host copy is not: Hrafnheim.log reached 1,704,742,436 bytes on 2026-09-13.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

source_config="$SCRIPT_DIR/logrotate/valheim-worlds.conf"
installed_config=/etc/valheim-worlds-logrotate.conf
state_file=/var/lib/logrotate/valheim-worlds.status
cron_job=/etc/cron.hourly/valheim-worlds-logrotate

[[ -f $source_config ]] || { echo "missing $source_config" >&2; exit 2; }
command -v logrotate >/dev/null || { echo "logrotate is not installed" >&2; exit 2; }

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (writes $installed_config and $cron_job)" >&2
    exit 2
fi

install -m 0644 -o root -g root -- "$source_config" "$installed_config"
install -d -m 0755 -- "$(dirname "$state_file")"

# The job is what makes rotation hourly. logrotate's own systemd timer only
# covers /etc/logrotate.conf, which is why this config lives outside
# /etc/logrotate.d - two schedules sharing one config means two state files
# disagreeing about when a file was last rotated.
cat >"$cron_job" <<EOF
#!/bin/sh
# Installed by hostops/install_log_rotation.sh - edit that, not this.
exec /usr/sbin/logrotate --state $state_file $installed_config
EOF
chmod 0755 "$cron_job"

# Refuse to leave a config behind that logrotate cannot parse: a broken file
# here fails silently every hour.
if ! logrotate --debug --state "$state_file" "$installed_config" >/dev/null 2>/tmp/valheim-logrotate-check.$$; then
    echo "logrotate rejected $installed_config:" >&2
    cat /tmp/valheim-logrotate-check.$$ >&2
    rm -f /tmp/valheim-logrotate-check.$$
    exit 1
fi
rm -f /tmp/valheim-logrotate-check.$$

echo "installed $installed_config"
echo "installed $cron_job (hourly)"
echo "state     $state_file"
