#!/usr/bin/env python3
"""Re-verify the two READING guards across BOTH branches of the forked chain.

The fork made them inert for a window: the one-compiler-per-zone clobber check
and the two-ends-per-tag portal check both decide by reading prior records, and
a reader that stops at the first branch cannot see claims made on the second.
This walks EVERY line in file order -- the only total order the artefact has --
and reports zone ownership, flatten=FORBIDDEN zones and portal tag ends, against
the 33 pads this package is about to write.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0]]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))
import build as B  # noqa: E402
import flatten as FL  # noqa: E402

LEDGER = HERE.parent / "ledger/runs/Ulfsland/ledger.jsonl"
ORDER = ("stenvik wt-spawn wt-south wt-town lh-south vestvik hognest-castle "
         "wt-peak wt-north wt-east lh-east lh-west tree-sth tree-west "
         "tree-near sudrberg-keep").split()


def zone_of(x: float, z: float) -> tuple[int, int]:
    return math.floor((float(x) + 32.0) / 64.0), math.floor((float(z) + 32.0) / 64.0)


def main() -> int:
    lines = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    owned: dict[tuple[int, int], list] = {}
    forbidden: dict[tuple[int, int], list] = {}
    tags: dict[str, list[int]] = {}
    for i, r in enumerate(lines):
        p = r["params"]
        if r["op"] == "terrain_write":
            for e in p["entries"]:
                owned.setdefault(tuple(e["zone"]), []).append((i, r["actor"]))
        if r["op"] == "observe" and p.get("what") == "zone_already_written":
            for z in (p.get("value") or {}).get("zones", []):
                owned.setdefault((int(z[0]), int(z[1])), []).append((i, r["actor"]))
        if r["op"] == "portal":
            tags.setdefault(p["tag"], []).append(i)
        if p.get("flatten") == "FORBIDDEN":
            pos = p.get("pos")
            xz = None
            if isinstance(pos, list) and len(pos) == 3:
                xz = (pos[0], pos[2])
            elif isinstance(pos, list) and len(pos) == 2:
                xz = (pos[0], pos[1])
            elif p.get("anchor"):
                xz = (p["anchor"].get("x"), p["anchor"].get("z"))
            if xz and xz[0] is not None and xz[1] is not None:
                forbidden.setdefault(zone_of(*xz), []).append(
                    (i, r["actor"], p.get("role")))

    print(f"ledger lines {len(lines)}")
    print(f"zones already carrying a compiler claim: {sorted(owned) or 'NONE'}")
    print(f"zones holding a flatten=FORBIDDEN structure: {sorted(forbidden)}")
    print(f"portal tags in the log (file lines): {tags}")

    plan = json.loads((HERE / "plan.json").read_text())
    mine: dict[tuple[int, int], list[str]] = {}
    for site in ORDER:
        doc = B.placements_doc(plan, site)
        for u in B.unit_records(plan, site):
            place = next(q for q in doc["placements"] if q["id"] == u["id"])
            w, d = FL.pad_extent(place)
            for z in FL.zones_for(u["x"], u["z"], w, d):
                mine.setdefault(z, []).append(u["id"])
    clash = {z: (owned[z], mine[z]) for z in mine if z in owned}
    fb = {z: (forbidden[z], mine[z]) for z in mine if z in forbidden}
    print(f"zones I will write: {len(mine)}")
    print(f"CLASH with an existing compiler claim: {clash or 'none'}")
    print(f"CLASH with flatten=FORBIDDEN: {fb or 'none'}")
    for site in ORDER:
        zs = sorted(z for z, v in mine.items()
                    if any(i.split('-')[0] == site.split('-')[0] and
                           i.startswith(site) for i in v))
        print(f"  {site:16s} {zs}")
    return 1 if clash or fb else 0


if __name__ == "__main__":
    raise SystemExit(main())
