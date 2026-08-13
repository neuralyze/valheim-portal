#!/usr/bin/env python3
"""Report the diagnostic switches a built profile archive would ship.

A release that quietly carries diagnostics costs every player frames for a measurement nobody is
reading. One session shipped a diagnostic mod bundled inside the VR runtime and left three profiling
switches on in the profile, and both went unnoticed until a log was read by hand - so the publisher
asks this question on every release rather than trusting anyone to remember.

Prints one comma-separated line naming what is on, or nothing at all when the archive is clean.
Exits 0 either way: the caller decides what to do about it.
"""

from __future__ import annotations

import re
import sys
import zipfile

# Every switch whose "on" position costs frames. Each one wraps methods that run every frame, or
# writes a log line per frame, or both.
SWITCHES = (
    "ProfileOurHooks",
    "ProfilePluginUpdateCost",
    "ProfileInventoryPanel",
    "ProfileGameMethods",
    "FrameAndVrReport",
    "SweepRenderScaleOnce",
    "MeasureCombatLatency",
    "LogDistinctHoverText",
    "SweepUILayerOnAdoptedCanvases",
)


def switches_on(archive_path: str) -> list[str]:
    found: set[str] = set()
    archive = zipfile.ZipFile(archive_path)
    for name in archive.namelist():
        if not name.endswith(".cfg"):
            continue
        text = archive.read(name).decode("utf-8", "replace")
        for switch in SWITCHES:
            if re.search(rf"(?m)^{switch}\s*=\s*true\s*$", text):
                found.add(switch)
        # Info-level disk logging is the same class of cost: every mod's per-frame chatter written to
        # disk. It is also what a profiling switch needs in order to be readable, so the two travel
        # together and are worth reporting together.
        if name.endswith("BepInEx.cfg"):
            disk = re.search(r"(?ms)^\[Logging\.Disk\].*?^LogLevels = ([^\n]+)", text)
            if disk and "Info" in disk.group(1):
                found.add("BepInEx disk logging at Info")
    return sorted(found)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: diagnostic_switches.py PROFILE_ARCHIVE", file=sys.stderr)
        return 2
    try:
        on = switches_on(argv[1])
    except Exception as error:
        # Unreadable is not clean. Saying so lets the publisher treat it as a finding rather than as
        # an all-clear, which is the mistake this whole file exists to prevent.
        print(f"unreadable ({error})")
        return 0
    if on:
        print(",".join(on))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
