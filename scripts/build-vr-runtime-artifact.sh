#!/usr/bin/env bash
set -euo pipefail

if (($# != 2)); then
  echo "usage: $0 <validated-vhvr-release.zip> <portal-vr-runtime.zip>" >&2
  exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
go run ./cmd/vr-runtime-builder --input "$1" --output "$2"
