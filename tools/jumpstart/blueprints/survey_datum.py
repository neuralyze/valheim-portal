#!/usr/bin/env python3
"""Survey the catalogued blueprint corpus against every candidate floor datum.

This is the evidence behind `base_geometry.py`'s choice. It resolves every row
of `library/data/library_manifest.json` to a body on disk, parses it with
`to_rcon_plan.read_objects` (the same full-fidelity reader the converter uses,
so rotation and scale are honoured), and reports for each blueprint:

    bottom_solid    lowest solid-collider Y over all pieces
    walkable        top of the lowest floor-role piece      <- the chosen datum
    support_bottom  lowest solid-collider Y over support pieces
    min_pivot       the OLD rule: min pivot Y, for comparison

plus the distribution: how many resolve, how many are refused and why, how many
would be mis-placed by each candidate and by how much.

Usage:
    tools/jumpstart/blueprints/survey_datum.py            # table + distribution
    tools/jumpstart/blueprints/survey_datum.py --json OUT # machine-readable
    tools/jumpstart/blueprints/survey_datum.py --offsets  # the FLOOR_TOL_M
                                                          # evidence
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))

import base_geometry as bg  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

sys.path.insert(0, str(JUMPSTART / "library"))
from materialise import load_rows, locate  # noqa: E402


def resolve_bodies() -> dict[str, Path]:
    """Every RESOLVABLE manifest row -> the body on disk, keyed by library handle.

    There used to be a second resolver here, walking a hard-coded root list and
    matching on file name. It is gone, and both reasons are worth recording
    because each one produced a confident wrong answer:

    * **Name is not identity.** `brokkr-the-cathedral.blueprint` exists in two
      sources and is two DIFFERENT buildings (8269 and 8240 pieces, MEASURED).
      Keying on the name surveyed whichever root came first and never reported
      the other. The manifest now carries a unique `name` handle and the
      `source_name` to look for on disk, and `library/materialise.locate`
      requires the body to HASH to what the row claims -- so a survey row is
      about a known body rather than about a file that happened to match.
    * **A zero-byte candidate must never win.** The `old_Storgard` root holds
      0-byte stubs for `s-ren-dockhouse.blueprint` and
      `salty-dick-cottage-final.blueprint` while another root holds the real
      101,856 and 83,966-byte bodies (MEASURED). First-root-wins resolved both
      to nothing and this survey then reported two real buildings as FLOORLESS.

    Rows marked `resolvable: false` -- the ten 0-byte corpus names, which have
    exactly one copy each and it is empty -- are SKIPPED and returned separately
    rather than raised on. A body that does not exist is not a survey failure;
    claiming to have surveyed it would be.
    """
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for entry in load_rows():
        if not entry.get("resolvable", bool(entry.get("sha256"))):
            continue
        path = locate(entry)
        if path is None:
            missing.append(entry["name"])
            continue
        resolved[entry["name"]] = path
    if missing:
        raise SystemExit(f"unresolved bodies: {missing}")
    return resolved


def survey(geom: bg.Geometry) -> list[dict]:
    rows = []
    for name, path in sorted(resolve_bodies().items()):
        try:
            objs = read_objects(path)
        except Exception as exc:  # a legacy-terrain refusal is a real answer
            rows.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        datum = bg.floor_datum(objs, geom)
        pivots = [o.pos[1] for o in objs]
        rows.append({
            "name": name,
            "fmt": path.suffix.lower().lstrip("."),
            "pieces": len(objs),
            "min_pivot_y": round(min(pivots), 3) if pivots else None,
            **datum.as_dict(),
            "terraces_m": datum.terraces_m()[:8],
        })
    return rows


def offset_evidence(geom: bg.Geometry) -> dict:
    """Distribution of (ground-resting pivot Y - the floor surface it stands on),
    which is where FLOOR_TOL_M comes from. For every ground-resting piece, find
    the floor-role surface nearest below it whose slab the piece stands within
    horizontally, and record the gap."""
    gaps: list[tuple[float, str, str]] = []
    for name, path in sorted(resolve_bodies().items()):
        try:
            objs = read_objects(path)
        except Exception:
            continue
        floors = []
        for o in objs:
            if not geom.is_floor(o.prefab):
                continue
            span = geom.y_span(o.prefab, o.rot, o.scale)
            box = geom.local_aabb(o.prefab)
            # Per-axis half extents from the UNROTATED slab. Every floor prefab
            # measured in this corpus is square in XZ except `jute_carpet`,
            # which is not a floor here, so yaw does not change containment.
            floors.append((o.pos[0], o.pos[1] + span[1], o.pos[2],
                           (box[3] - box[0]) * 0.5, (box[5] - box[2]) * 0.5, o.prefab))
        if not floors:
            continue
        for o in objs:
            if not geom.is_ground_resting(o.prefab):
                continue
            # The surface this piece STANDS ON is the HIGHEST floor surface at or
            # below it whose slab contains it horizontally. Taking the nearest
            # surface by absolute distance instead -- the first thing tried here
            # -- matches floors ABOVE the piece and in neighbouring columns, and
            # produces a flat +/-1 m spread that says nothing. That is the
            # measurement bug this file exists to avoid, so it is named.
            best = None
            for fx, fy, fz, hx, hz, fprefab in floors:
                if abs(o.pos[0] - fx) > hx + 1e-3 or abs(o.pos[2] - fz) > hz + 1e-3:
                    continue
                if fy > o.pos[1] + 0.30:
                    continue
                if best is None or fy > best[0]:
                    best = (fy, fprefab)
            if best is not None:
                gaps.append((o.pos[1] - best[0], o.prefab, best[1]))
    gaps.sort(key=lambda t: t[0])
    vals = [g[0] for g in gaps]
    n = len(vals)

    def pct(p):
        return round(vals[min(n - 1, int(p * n))], 4) if n else None

    return {
        "samples": n,
        "min": round(vals[0], 4) if n else None,
        "p01": pct(0.01), "p05": pct(0.05), "p50": pct(0.50),
        "p95": pct(0.95), "p99": pct(0.99),
        "max": round(vals[-1], 4) if n else None,
        "within_0.10": round(sum(1 for v in vals if abs(v) <= 0.10) / n, 4) if n else None,
        "within_0.25": round(sum(1 for v in vals if abs(v) <= 0.25) / n, 4) if n else None,
        "deepest_below": gaps[:6],
        "highest_above": gaps[-6:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json")
    ap.add_argument("--offsets", action="store_true")
    ap.add_argument("--geometry")
    args = ap.parse_args(argv)

    geom = bg.Geometry(Path(args.geometry)) if args.geometry else bg.geometry()

    if args.offsets:
        print(json.dumps(offset_evidence(geom), indent=1))
        return 0

    rows = survey(geom)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))

    ok = [r for r in rows if r.get("placeable")]
    refused = [r for r in rows if r.get("placeable") is False]
    errored = [r for r in rows if "error" in r]

    print(f"{'blueprint':44s} {'pcs':>5s} {'pivot':>8s} {'walk':>8s} {'solid':>8s} "
          f"{'supp':>8s} {'old->new':>9s} method")
    for r in rows:
        if "error" in r:
            print(f"{r['name'][:44]:44s} {'':>5s} {'':>8s} ERROR {r['error'][:60]}")
            continue
        def f(v):
            return "  --  " if v is None else f"{v:8.3f}"
        shift = (None if r["base_y"] is None else r["base_y"] - (r["min_pivot_y"] or 0.0))
        print(f"{r['name'][:44]:44s} {r['pieces']:5d} {f(r['min_pivot_y'])} "
              f"{f(r['base_y'])} {f(r['bottom_solid_y'])} {f(r['support_bottom_y'])} "
              f"{'  --  ' if shift is None else f'{-shift:+9.3f}'} {r['method']}"
              + ("  " + ",".join(v["code"] for v in r["violations"]) if r["violations"] else ""))

    print()
    print(f"catalogued bodies surveyed : {len(rows)}")
    print(f"  placeable                : {len(ok)}")
    print(f"  refused                  : {len(refused)}")
    print(f"  parser errors            : {len(errored)}")
    codes: dict[str, int] = {}
    for r in rows:
        for v in r.get("violations", []):
            codes[v["code"]] = codes.get(v["code"], 0) + 1
    for code, k in sorted(codes.items(), key=lambda t: -t[1]):
        print(f"  violation {code:26s} {k}")

    # How wrong each candidate is, against the chosen datum, over the placeable set.
    def stats(key, label):
        d = [r["base_y"] - r[key] for r in ok if r.get(key) is not None]
        if not d:
            print(f"  {label}: no data")
            return
        d.sort()
        nz = sum(1 for v in d if abs(v) > 0.01)
        print(f"  {label:22s} n={len(d):3d} differs>1cm={nz:3d} "
              f"median={d[len(d)//2]:+7.3f} p95={d[int(0.95*len(d))]:+7.3f} "
              f"max={d[-1]:+7.3f} m")

    print()
    print("chosen datum minus each alternative (metres the alternative would float by):")
    stats("min_pivot_y", "old rule min pivot Y")
    stats("bottom_solid_y", "lowest solid")
    stats("support_bottom_y", "support bottom")
    buried = [r for r in ok if r.get("support_bottom_y") is not None
              and r["support_bottom_y"] < r["base_y"] - 0.01]
    print(f"  blueprints whose supports sit below the chosen plane (correctly buried): "
          f"{len(buried)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
