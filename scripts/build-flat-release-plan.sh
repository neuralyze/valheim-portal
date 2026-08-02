#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: build-flat-release-plan.sh VERSION NOTES FLAT_COMPANION OUTPUT_DIR [TARGETS_JSON]

Builds one checksum-bound Flat profile definition per configured target and
writes OUTPUT_DIR/publication-plan.json for valheim-flat-release-publish.

VALHEIM_PROFILE_SOURCE_ROOT is required: the world root holding
<WORLD>/mods/profiles/<PROFILE>. There is no default.
USAGE
  exit 2
}

(($# == 4 || $# == 5)) || usage
version=$1
notes=$2
companion=$3
output_dir=$4
targets=${5:-"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/release-targets.json"}
# No default: the previous absolute default was the original author's disk, so
# every other host silently built plans against profiles that were not there.
source_root=${VALHEIM_PROFILE_SOURCE_ROOT:-}
[[ -n $source_root ]] || {
  echo "VALHEIM_PROFILE_SOURCE_ROOT is not set: point it at the world root holding <WORLD>/mods/profiles" >&2
  exit 78
}
[[ -d $source_root ]] || { echo "VALHEIM_PROFILE_SOURCE_ROOT is not a directory: $source_root" >&2; exit 78; }
portal_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

[[ -f "$companion" ]] || { echo "missing Flat companion: $companion" >&2; exit 1; }
[[ -f "$targets" ]] || { echo "missing release target catalog: $targets" >&2; exit 1; }
mkdir -p "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
companion=$(cd -- "$(dirname -- "$companion")" && pwd)/$(basename -- "$companion")

mapfile -t targets_list < <(python3 - "$targets" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    catalog = json.load(handle)
if catalog.get("schema") != 1 or not isinstance(catalog.get("flat"), list):
    raise SystemExit("invalid Flat release target catalog")
seen = set()
for entry in catalog["flat"]:
    world = entry.get("world")
    source = entry.get("source_profile")
    published = entry.get("published_profile")
    if not all(isinstance(value, str) and value for value in (world, source, published)):
        raise SystemExit("invalid Flat release target")
    if (world, published) in seen:
        raise SystemExit("duplicate Flat release target")
    seen.add((world, published))
    print("\t".join((world, source, published)))
PY
)

payloads=()
for entry in "${targets_list[@]}"; do
  IFS=$'\t' read -r world source_profile published_profile <<<"$entry"
  source_manifest="$source_root/$world/mods/profiles/$source_profile/profile-manifest.json"
  base_config="$source_root/$world/mods/profiles/$source_profile/client-config"
  flat_config="$source_root/$world/mods/profiles/$source_profile/client-config-flat"
  [[ -f "$source_manifest" && -d "$base_config" && -d "$flat_config" ]] || { echo "incomplete source profile for $world/$source_profile" >&2; exit 1; }
  merged=$(mktemp -d)
  trap 'rm -rf -- "$merged"' EXIT
  cp -a -- "$base_config/." "$merged/"
  cp -a -- "$flat_config/." "$merged/"
  payload="$output_dir/${published_profile}-profile-${version}.zip"
  (
    cd "$portal_dir"
    go run ./cmd/profile-definition-builder \
      -source-manifest "$source_manifest" -world "$world" -profile "$published_profile" \
      -client-type flat -config-dir "$merged" -output "$payload" \
      -flat-companion "$companion"
  )
  rm -rf -- "$merged"
  trap - EXIT
  payloads+=("$world"$'\t'"$published_profile"$'\t'"$payload")
done

python3 - "$version" "$notes" "$companion" "$output_dir/publication-plan.json" "${payloads[@]}" <<'PY'
import json, sys
version, notes, companion, output, *targets = sys.argv[1:]
entries = []
for target in targets:
    world, profile, payload = target.split("\t")
    entries.append({"world": world, "profile": profile, "payload": payload})
with open(output, "w", encoding="utf-8") as handle:
    json.dump({"schema": 1, "version": version, "notes": notes, "flat_companion": companion, "targets": entries}, handle, indent=2)
    handle.write("\n")
print(output)
PY
