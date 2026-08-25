#!/usr/bin/env bash
# Proves the host scripts still agree with the callers that build their argv.
#
# Two shipped breaks on 2026-08-17, both invisible because nothing tested this seam:
#
#   * provision_valheim_server.sh declared fifteen positionals ending TEMPLATE_WORLD
#     TEMPLATE_PROFILE. The agent had already collapsed that pair into one COPY_FROM and
#     sent fourteen, so every agent-driven provision exited 2.
#   * portal_publish_profile.sh resolved its catalog target by source_profile. Once a
#     world published two Flat editions from the same primary, that matched twice and
#     every Flat publish exited 2.
#
# Both are the same failure: a producer changed shape and a separately-edited consumer
# kept asserting the old one. This test compares the scripts against the OTHER side's
# source rather than against a copy of the same assumption.
#
# Run: bash hostops/tests/agent_argv_contract.sh
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOSTOPS="$SCRIPT_DIR/.."
REPO=$(cd -- "$HOSTOPS/.." && pwd)

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- provisioning: the script's positional count against the agent's argv construction
declared=$(python3 - "$HOSTOPS/provision_valheim_server.sh" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"POSITIONALS=\((.*?)\)", text, re.S)
if not block:
    raise SystemExit("no POSITIONALS array in provision_valheim_server.sh")
print(len(block.group(1).split()))
PY
)

# The agent appends World first, then the provision case's list. Counting the identifiers
# in that case is what keeps this honest: it reads agent.go, not a second copy of the
# number.
sent=$(python3 - "$REPO/internal/agent/agent.go" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
case = re.search(r'case operation == "provision":\s*\n\s*args = append\(args,(.*?)\)\n', text, re.S)
if not case:
    raise SystemExit('no provision argv construction in agent.go')
# \b and the capital both matter: without them "provision_valheim_server.sh" in a
# comment inside this block counts as a field, because it contains "r.sh".
fields = re.findall(r"\br\.[A-Z][A-Za-z]*", case.group(1))
print(len(fields) + 1)  # +1 for the world seeded before the switch
PY
)

[[ $declared == "$sent" ]] ||
	fail "provision_valheim_server.sh expects $declared positionals; agent.go sends $sent"

grep -q "TEMPLATE_WORLD\|TEMPLATE_PROFILE" "$HOSTOPS/provision_valheim_server.sh" &&
	fail "provision_valheim_server.sh still names the template pair the copy_from rename removed"

# --- publishing: every catalog target must be resolvable by what the verb actually sends
python3 - "$HOSTOPS/portal_publish_profile.sh" <<'PY' || fail "portal_publish_profile.sh cannot resolve one target per published edition"
import json, re, subprocess, sys, tempfile, os

script = sys.argv[1]
# Two Flat editions from one primary, which is the shape that broke it, plus one vr.
catalog = {
    "schema": 1,
    "flat": [
        {"world": "Midgard", "source_profile": "flat", "published_profile": "midgard-vr-flat",
         "valheim_vr": True, "audience": "player"},
        {"world": "Midgard", "source_profile": "flat", "published_profile": "midgard-non-vr",
         "valheim_vr": False, "audience": "player"},
    ],
    "vr": [
        {"world": "Midgard", "source_profile": "vr", "published_profile": "midgard-vr",
         "valheim_vr": True, "audience": "player"},
    ],
}
selector = re.search(r"<<'PY'[^\n]*\n(import json, sys\n.*?)\nPY\n", open(script, encoding="utf-8").read(), re.S)
if not selector:
    raise SystemExit("no catalog selector in portal_publish_profile.sh")

with tempfile.TemporaryDirectory() as tmp:
    catalog_path = os.path.join(tmp, "release-targets.json")
    with open(catalog_path, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle)
    for published, client_type in (("midgard-vr-flat", "flat"), ("midgard-non-vr", "flat"), ("midgard-vr", "vr")):
        out = os.path.join(tmp, published + ".json")
        result = subprocess.run([sys.executable, "-", catalog_path, "Midgard", published, client_type, out],
                                input=selector.group(1), capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"{published}: exit {result.returncode}: {result.stderr.strip()}")
        chosen = json.load(open(out, encoding="utf-8"))[client_type]
        if len(chosen) != 1 or chosen[0]["published_profile"] != published:
            raise SystemExit(f"{published}: selector chose {chosen}")
print("ok")
PY

# The test has to fail on the shapes that shipped, or it proves nothing. Re-run the
# selector against a catalog where source_profile is the only key it could match on: two
# Flat editions from one primary is exactly the state that broke publishing.
python3 - "$HOSTOPS/portal_publish_profile.sh" <<'PY' || fail "the selector still resolves by source_profile"
import json, re, subprocess, sys, tempfile, os

script = sys.argv[1]
selector = re.search(r"<<'PY'[^\n]*\n(import json, sys\n.*?)\nPY\n", open(script, encoding="utf-8").read(), re.S)
catalog = {"schema": 1, "vr": [], "flat": [
    {"world": "Midgard", "source_profile": "flat", "published_profile": "midgard-vr-flat",
     "valheim_vr": True, "audience": "player"},
    {"world": "Midgard", "source_profile": "flat", "published_profile": "midgard-non-vr",
     "valheim_vr": False, "audience": "player"},
]}
with tempfile.TemporaryDirectory() as tmp:
    catalog_path = os.path.join(tmp, "c.json")
    with open(catalog_path, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle)
    # Asking by the PRIMARY name must now be refused: it names two editions.
    result = subprocess.run([sys.executable, "-", catalog_path, "Midgard", "flat", "flat",
                             os.path.join(tmp, "out.json")],
                            input=selector.group(1), capture_output=True, text=True)
    if result.returncode == 0:
        raise SystemExit("selecting by the source primary succeeded; it matches two editions")
print("ok")
PY

echo "PASS: host script argv and catalog contracts hold"
