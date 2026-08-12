#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
out=$(realpath -m "${1:-"$root/dist/ValheimProfileSync.exe"}")
mkdir -p "$(dirname "$out")"

# Resolve the build identity here: the staging copy below carries no VCS data,
# and an unstamped binary cannot be matched to a release in a support report.
version=${PORTAL_VERSION:-$(git -C "$root" describe --tags --always --dirty 2>/dev/null || printf 'dev')}

staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
mkdir -p "$staging/source"
cp -a "$root/." "$staging/source/"
(
  cd "$staging/source"
  GOOS=windows GOARCH=amd64 go build -trimpath -buildvcs=false \
    -ldflags="-H=windowsgui -s -w -X github.com/neuralyze/valheim-portal/internal/version.Version=$version" \
    -o "$out" ./cmd/valheim-profile-sync
)
# Sign it if credentials are available. Unsigned is what Defender's heuristic keys on, and an
# unsigned build that gets quarantined mid-session is worse than a slower build.
"$root/scripts/sign-windows-client.sh" "$out" || true

printf 'built %s\n' "$version" >&2
printf '%s\n' "$out"
