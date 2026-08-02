#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: publish-flat-release-plan.sh PLAN DATABASE ARTIFACT_ROOT [ACTOR]" >&2
  exit 2
}
(($# == 3 || $# == 4)) || usage
plan=$1
database=$2
artifact_root=$3
actor=${4:-flat-release-publisher}
[[ -f "$plan" && "$database" = /* && "$artifact_root" = /* ]] || usage
portal_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

mapfile -t values < <(python3 - "$plan" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    plan = json.load(handle)
if plan.get("schema") != 1 or not isinstance(plan.get("version"), str) or not isinstance(plan.get("notes"), str):
    raise SystemExit("invalid publication plan")
companion = plan.get("flat_companion")
targets = plan.get("targets")
if not isinstance(companion, str) or not os.path.isabs(companion) or not isinstance(targets, list) or not targets:
    raise SystemExit("invalid Flat publication targets")
print(plan["version"])
print(companion)
for target in targets:
    if not all(isinstance(target.get(key), str) and target[key] for key in ("world", "profile", "payload")) or not os.path.isabs(target["payload"]):
        raise SystemExit("invalid publication target")
    print("\t".join((target["world"], target["profile"], target["payload"])))
PY
)
version=${values[0]}
companion=${values[1]}
[[ -f "$companion" ]] || { echo "missing Flat companion: $companion" >&2; exit 1; }
for ((index = 2; index < ${#values[@]}; index++)); do
  IFS=$'\t' read -r world profile payload <<<"${values[index]}"
  [[ -f "$payload" ]] || { echo "missing payload: $payload" >&2; exit 1; }
  (
    cd "$portal_dir"
    go run ./cmd/seed-release \
      --database "$database" --artifact-root "$artifact_root" --actor "$actor" \
      --world "$world" --profile "$profile" --client-type flat --version "$version" \
      --profile-payload "$payload" --flat-companion "$companion"
  )
done
