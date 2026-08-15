#!/usr/bin/env bash
# Every gate CI runs, in one command, failing at the first broken one and naming it.
#
# This exists because "run everything" used to mean reading .github/workflows/ci.yml and copying
# eleven commands by hand, and nobody did: shellcheck stayed red on scripts/republish-profiles.sh
# for weeks without being noticed.
#
# usage: scripts/check.sh [--list] [--only <name>] [--skip <name>]...
#
# Exit codes: 0 every gate passed, 1 a gate failed, 2 the invocation was wrong.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root" || exit 2

# name|description|command. Ordered cheapest first, so a formatting mistake is reported in seconds
# rather than after the race suite.
gates=(
  "gofmt|every Go file is formatted|test -z \"\$(gofmt -l .)\" || { gofmt -l . >&2; false; }"
  "vet|go vet finds nothing|go vet ./..."
  "tidy|go.mod and go.sum match the imports|go mod tidy && git diff --exit-code go.mod go.sum"
  "build|the module builds|go build ./..."
  "windows|the profile-sync client builds for Windows|GOOS=windows go build ./..."
  "shellcheck-scripts|scripts/*.sh are clean|shellcheck scripts/*.sh scripts/tests/*.sh"
  "shellcheck-hostops|host operation scripts are clean at style level|shellcheck -S style hostops/lib/common.sh hostops/*.sh hostops/tests/*.sh"
  "policy|policy.yaml, the docs and the Go verb table agree|python3 tools/check_agent_policy.py"
  "perframe|no unbounded scene searches on a frame path|python3 tools/check_perframe_work.py"
  "beads|the task tracker belongs to this project|python3 tools/check_beads_workspace.py"
  "pytest|the Python tool tests pass|cd tools && python3 -m pytest -q"
  "hostops|the host script regression tests pass|for t in hostops/tests/*.sh; do bash \"\$t\" >/dev/null || exit 1; done"
  "installer|install.conf reaches the compose environment unchanged|for t in scripts/tests/*.sh; do bash \"\$t\" >/dev/null || exit 1; done"
  "gotest|the Go tests pass under the race detector|go test -race ./..."
)

list() {
  printf '%-20s %s\n' "GATE" "CHECKS"
  local entry name description
  for entry in "${gates[@]}"; do
    IFS='|' read -r name description _ <<<"$entry"
    printf '%-20s %s\n' "$name" "$description"
  done
}

only=""
skips=()
while (($# > 0)); do
  case "$1" in
    --list) list; exit 0 ;;
    --only) only=${2:-}; [[ -n $only ]] || { echo "--only needs a gate name" >&2; exit 2; }; shift 2 ;;
    --skip) [[ -n ${2:-} ]] || { echo "--skip needs a gate name" >&2; exit 2; }; skips+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

skipped() {
  local candidate=$1 skip
  for skip in ${skips[@]+"${skips[@]}"}; do
    [[ $skip == "$candidate" ]] && return 0
  done
  return 1
}

known=0
for entry in "${gates[@]}"; do
  IFS='|' read -r name _ _ <<<"$entry"
  [[ -n $only && $only == "$name" ]] && known=1
done
if [[ -n $only && $known -eq 0 ]]; then
  echo "no such gate: $only" >&2
  list >&2
  exit 2
fi

started=$SECONDS
for entry in "${gates[@]}"; do
  IFS='|' read -r name description command <<<"$entry"
  if [[ -n $only && $only != "$name" ]]; then
    continue
  fi
  if skipped "$name"; then
    printf '  skip  %-20s %s\n' "$name" "$description"
    continue
  fi
  printf '  ....  %-20s %s\n' "$name" "$description"
  gate_started=$SECONDS
  if output=$(eval "$command" 2>&1); then
    printf '\033[1A  ok    %-20s %s (%ds)\n' "$name" "$description" "$((SECONDS - gate_started))"
  else
    printf '\033[1A  FAIL  %-20s %s\n' "$name" "$description"
    echo "$output" | tail -40 | sed 's/^/        /'
    echo
    echo "gate '$name' failed. Reproduce it with: scripts/check.sh --only $name" >&2
    exit 1
  fi
done
echo "  all gates passed in $((SECONDS - started))s"
