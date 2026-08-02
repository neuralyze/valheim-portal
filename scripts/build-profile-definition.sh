#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  build-profile-definition.sh WORLD PROFILE CLIENT_TYPE
  build-profile-definition.sh WORLD PROFILE CLIENT_TYPE SOURCE_MANIFEST CONFIG_DIR OUTPUT [CONFIG_OVERLAY_DIR]

The three-argument form reads the managed profile manifest and client-config
layout from VALHEIM_PROFILE_SOURCE_ROOT, which is required and has no default:
it is the world root holding <WORLD>/mods/profiles/<PROFILE>. The explicit form
accepts an absolute managed profile manifest path and does not need it. Set
VALHEIM_PACKAGE_BASE_URL to use an approved Thunderstore archive mirror instead
of the public package endpoint.
USAGE
  exit 2
}

(($# == 3 || $# == 6 || $# == 7)) || usage

world=$1
profile=$2
client_type=$3
case "$client_type" in
  flat|vr) ;;
  *) echo "client type must be flat or vr" >&2; exit 2 ;;
esac

if (($# == 3)); then
  # No default: the previous absolute default was the original author's disk,
  # so every other host silently built against profiles that were not there.
  source_root=${VALHEIM_PROFILE_SOURCE_ROOT:-}
  [[ -n $source_root ]] || {
    echo "VALHEIM_PROFILE_SOURCE_ROOT is not set: point it at the world root holding <WORLD>/mods/profiles" >&2
    exit 78
  }
  [[ -d $source_root ]] || { echo "VALHEIM_PROFILE_SOURCE_ROOT is not a directory: $source_root" >&2; exit 78; }
  source_manifest="$source_root/$world/mods/profiles/$profile/profile-manifest.json"
  config_dir="$source_root/$world/mods/profiles/$profile/client-config"
  config_overlay_dir="$source_root/$world/mods/profiles/$profile/client-config-$client_type"
  output="$source_root/$world/mods/manager/exports/$world-($profile)-$client_type-profile.zip"
  artifact_map="$source_root/$world/mods/profiles/$profile/valheimvr-artifacts.json"
  if [[ ! -f "$artifact_map" ]]; then
    shared_mod_root=${VALHEIM_SHARED_MODS_ROOT:-"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../boilerplate/mods/valheimvr" && pwd)"}
    artifact_map="$shared_mod_root/valheimvr-artifacts.json"
  fi
  flat_companion=
  mapped_artifact=
  if [[ -f "$artifact_map" ]]; then
    mapped_artifact=$(python3 - "$artifact_map" "$(dirname "$artifact_map")" "$client_type" <<'PY'
import json
import os
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    mapping = json.load(handle)
entry = mapping.get(sys.argv[3], {})
path = entry.get("path", "")
if path:
    print(os.path.normpath(os.path.join(sys.argv[2], path)))
PY
)
  fi
  if [[ "$client_type" == "flat" ]]; then
    flat_companion=$mapped_artifact
  fi
else
  source_manifest=$4
  config_dir=$5
  output=$6
  config_overlay_dir=${7:-}
  flat_companion=
  mapped_artifact=
fi

[[ -f "$source_manifest" ]] || { echo "missing managed profile manifest: $source_manifest" >&2; exit 1; }
[[ -d "$config_dir" ]] || { echo "missing client configuration directory: $config_dir" >&2; exit 1; }
[[ -z "${config_overlay_dir:-}" || -d "$config_overlay_dir" ]] || { echo "missing client configuration overlay directory: $config_overlay_dir" >&2; exit 1; }
[[ -z "$flat_companion" || -f "$flat_companion" ]] || { echo "missing Flat companion archive: $flat_companion" >&2; exit 1; }
[[ -z "$mapped_artifact" || -f "$mapped_artifact" ]] || { echo "missing mapped ValheimVR artifact: $mapped_artifact" >&2; exit 1; }

merged_config=
if [[ -n "${config_overlay_dir:-}" && -d "$config_overlay_dir" ]]; then
  merged_config=$(mktemp -d)
  trap 'rm -rf -- "$merged_config"' EXIT
  cp -a -- "$config_dir/." "$merged_config/"
  cp -a -- "$config_overlay_dir/." "$merged_config/"
  config_dir=$merged_config
fi

portal_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$portal_dir"
args=(
  -source-manifest "$source_manifest"
  -world "$world"
  -profile "$profile"
  -client-type "$client_type"
  -config-dir "$config_dir"
  -output "$output"
)
if [[ -n "${VALHEIM_PACKAGE_BASE_URL:-}" ]]; then
  args+=(-package-base-url "$VALHEIM_PACKAGE_BASE_URL")
fi
if [[ "${VALHEIM_DEBUG_LOGGING:-}" == "1" ]]; then
  args+=(-debug-logging)
fi
if [[ -n "$flat_companion" ]]; then
  args+=(-flat-companion "$flat_companion")
fi
go run ./cmd/profile-definition-builder "${args[@]}"
