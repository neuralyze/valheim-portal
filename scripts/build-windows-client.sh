#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
out=$(realpath -m "${1:-"$root/dist/ValheimProfileSync.exe"}")
mkdir -p "$(dirname "$out")"

# Refuse to build a client that cannot work. The ServerCharacters archive is embedded
# with `//go:embed embedded`, which points at a DIRECTORY holding a committed README.md -
# so the pattern still matches when the archive is absent and `go build` succeeds
# silently, producing an installer that fails at the user's first from-scratch install,
# after they have already completed the Steam sign-in. That shipped on 2026-09-14: this
# script was run inside the deployment checkout, where the archive is gitignored and
# therefore missing, and the operator hit "ServerCharacters archive is missing" three
# times before the cause was found. A build-time failure costs seconds; that cost an hour.
embedded_archive="$root/internal/servercharacters/embedded/ServerCharacters.zip"
if [ ! -s "$embedded_archive" ]; then
	printf 'refusing to build: %s is missing or empty.\n' "$embedded_archive" >&2
	printf 'run tools/servercharacters/build.sh, or copy the archive from a tree that has it.\n' >&2
	printf 'it is gitignored deliberately, so a fresh checkout never has it.\n' >&2
	exit 1
fi

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
