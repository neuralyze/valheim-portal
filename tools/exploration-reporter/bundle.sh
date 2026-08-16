#!/usr/bin/env bash
# Builds the client plugin bundle that profiles install, adding the exploration reporter to whatever is
# already published.
#
# Why additive rather than assembled from source: the published bundle carries NeuralyzeVRFixes.dll,
# and rebuilding that here would ship every VR change made since it was published as a side effect of
# adding a map reporter. The existing entries are copied byte for byte; the only new thing players
# receive is the reporter.
#
# usage: bundle.sh SOURCE_ZIP OUTPUT_ZIP [REPORTER_DLL]
#
# SOURCE_ZIP is the currently published diag_plugin artifact - scripts/republish-profiles.sh finds it
# with the same query, or read it out of the database by hand. OUTPUT_ZIP is what to pass to that script
# as VALHEIM_CLIENT_PLUGIN.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_zip=${1:-}
output_zip=${2:-}
reporter=${3:-}

[[ -f ${source_zip:-} ]] || { echo "usage: bundle.sh SOURCE_ZIP OUTPUT_ZIP [REPORTER_DLL]" >&2; exit 2; }
[[ -n ${output_zip:-} ]] || { echo "usage: bundle.sh SOURCE_ZIP OUTPUT_ZIP [REPORTER_DLL]" >&2; exit 2; }

if [[ -z $reporter ]]; then
    reporter=$here/ExplorationReporter.dll
    "$here/build.sh" "$reporter" >/dev/null
fi
[[ -f $reporter ]] || { echo "no reporter assembly: $reporter" >&2; exit 78; }

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

# Unpack, add, repack. A zip written in place would rewrite the existing entries' metadata; this keeps
# the file list explicit so the diff against the old bundle is one added path.
python3 - "$source_zip" "$reporter" "$output_zip" <<'PY'
import shutil
import sys
import zipfile

source, reporter, output = sys.argv[1:4]
added = "BepInEx/plugins/NeuralyzeExplorationReporter/ExplorationReporter.dll"

with zipfile.ZipFile(source) as existing:
    names = existing.namelist()
    if added in names:
        print(f"  {added} is already in the bundle; copying it unchanged")
        shutil.copyfile(source, output)
        raise SystemExit(0)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for info in existing.infolist():
            # Entry metadata is carried over with the bytes, so the only difference between the two
            # bundles is the new file.
            bundle.writestr(info, existing.read(info.filename))
        bundle.write(reporter, added)

with zipfile.ZipFile(output) as check:
    if check.testzip() is not None:
        raise SystemExit("the bundle this produced is corrupt")
    for name in check.namelist():
        print(f"  {name} ({check.getinfo(name).file_size} bytes)")
PY

printf 'wrote %s (%s bytes)\n' "$output_zip" "$(stat -c %s "$output_zip")"
