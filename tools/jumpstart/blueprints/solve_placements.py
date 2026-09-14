#!/usr/bin/env python3
"""Re-solve every jumpstart base placement for a world seed, in one command.

This is the point of the whole placement design.  Blueprint coordinates are
**seed-specific**: a flat coastal meadow in one seed is open ocean in the next.
So `placements.yaml` never hard-codes a site as its source of truth -- it
records a seed-independent *requirement* (biome, footprint, flatness, coastal or
not, which ZoneSystem anchor to sit near, what feature the spot must serve) and
this driver turns those requirements into concrete coordinates for whatever seed
the world is currently rolled onto.

Re-rolling the world is therefore not a manual re-survey:

    ./solve_placements.py --world tools/jumpstart/worlds/Ulfsland \\
        --seed Pirate68 \\
        --grid  /tmp/bp_p68_8/00000.biome \\
        --locations /tmp/seedscan/loc/f6fe167f4fcd.json --write

`--write` updates each file's `solved` block in place and leaves every
`requirement` untouched.  `--label alternative` writes into
`solved_alternatives[<seed>]` instead, for recording a seed that is *not* live.

Grids and location dumps come from the sibling `tools/seedscan` harness:

    printf 'Pirate68\\n' > /tmp/seed.txt
    VH_SRC=<install>/data/bepinex SEEDS=/tmp/seed.txt OUT=/tmp/g \\
      SANDBOX=/tmp/my_sandbox STEP=8 HEIGHT=1 tools/seedscan/run_scan.sh
    VH_SRC=... SEEDS=/tmp/seed.txt OUT=/tmp/loc tools/seedscan/run_locscan.sh
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import site_finder as SF  # noqa: E402

# Chest capacity per placement. KitDesign's chest-manifest.yaml says how many
# inventory slots a preset's materials overflow into; this says how much the
# placed base can actually hold, so the two can be diffed instead of guessed.
# Counted from the blueprint's own object list, not estimated.
CONTAINER_RE = re.compile(
    r"^(piece_chest|piece_chest_wood|piece_chest_blackmetal|piece_chest_private"
    r"|piece_chest_barrel|dvergrprops_crate|piece_cartographytable|CargoCrate"
    r"|piece_MightyDrawer|RossDrawer)",
    re.I,
)


def container_census(source: Path) -> dict:
    """Count container objects in a blueprint, by prefab."""
    import to_rcon_plan as TP

    try:
        objs = TP.read_objects(source)
    except Exception as exc:  # a missing corpus file must not abort the solve
        return {"error": str(exc)}
    counts: dict[str, int] = {}
    for o in objs:
        if CONTAINER_RE.match(o.prefab):
            counts[o.prefab] = counts.get(o.prefab, 0) + 1
    total = sum(counts.values())
    return {
        "by_prefab": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "containers": total,
        # Valheim chests are 4x x slots; the deployed BiggerChests mod changes
        # this, so the figure is a floor, not a promise.
        "slots_floor": total * 20,
    }


def solve_one(
    g, anchors, avoid_xz, req: dict, clearance_default: float, exclude: list | None = None
) -> dict | None:
    anchor_xz = None
    anchor_desc = ""
    near = req.get("near")
    if near:
        inst = anchors.get(near)
        if not inst:
            return {"error": f"seed has no location named {near!r}"}
        pick = min(inst, key=lambda l: math.hypot(l["x"], l["z"]))
        anchor_xz = (pick["x"], pick["z"])
        anchor_desc = f"{near} @ ({pick['x']:.1f},{pick['y']:.1f},{pick['z']:.1f})"
    elif req.get("near_xz"):
        anchor_xz = tuple(req["near_xz"])  # type: ignore[assignment]
        anchor_desc = f"fixed {anchor_xz}"

    clearance = float(req.get("location_clearance_m", clearance_default))
    # Widen the search until it yields something rather than failing outright:
    # a requirement that is unsatisfiable at its stated tolerance is still
    # better served by the closest honest match plus a recorded relaxation.
    relaxations = [
        (1.0, 1.0),
        (1.5, 1.5),
        (2.0, 2.0),
        (3.0, 3.0),
        (4.0, 6.0),
    ]
    for flat_mul, within_mul in relaxations:
        cands = SF.solve(
            g,
            req["biome"],
            float(req.get("footprint_m", 56)),
            float(req["max_flat_m"]) * flat_mul,
            bool(req.get("coastal", False)),
            anchor_xz,
            float(req.get("within_m", 1500)) * within_mul,
            float(req.get("min_biome_purity", 0.85)),
            1,
            avoid_xz,
            clearance,
            float(req.get("coastal_within_m", 120)),
            float(req.get("min_height_m", 2.0)),
            exclude,
        )
        if cands:
            d = cands[0]
            return {
                "x": round(d["x"], 1),
                "y": round(d["y"], 2),
                "z": round(d["z"], 1),
                "flat_spread_m": round(d["flat"], 2),
                "biome_purity": round(d["biome_purity"], 3),
                "water_dist_m": round(d["water_dist"], 1),
                "anchor": anchor_desc,
                "anchor_dist_m": round(d["anchor_dist"], 0),
                "nearest_location_m": round(d.get("location_dist", float("nan")), 0),
                "relaxation": (
                    "exact"
                    if (flat_mul, within_mul) == (1.0, 1.0)
                    else f"max_flat x{flat_mul}, within x{within_mul}"
                ),
            }
    return {"error": "no site found even after relaxing tolerances 4x"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world", required=True, help="tools/jumpstart/worlds/<World>")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--locations", required=True)
    ap.add_argument("--clearance", type=float, default=SF.DEFAULT_LOCATION_CLEARANCE)
    ap.add_argument(
        "--label",
        default="primary",
        choices=("primary", "alternative"),
        help="primary writes `solved`; alternative writes solved_alternatives[<seed>]",
    )
    ap.add_argument(
        "--manifest",
        default=str(HERE / "data" / "corpus_manifest.json"),
        help="used to locate each blueprint body for the container census",
    )
    ap.add_argument("--write", action="store_true", help="update the yaml files in place")
    args = ap.parse_args(argv)

    sources: dict[str, Path] = {}
    mpath = Path(args.manifest)
    if mpath.exists():
        for e in json.loads(mpath.read_text("utf-8"))["entries"]:
            if not e["sha256"]:
                continue
            prev = sources.get(e["file"])
            if prev is None or e["bytes"] > prev[1]:
                sources[e["file"]] = (Path(e["source"]), e["bytes"])  # type: ignore[assignment]
        sources = {k: v[0] for k, v in sources.items()}  # type: ignore[index]

    g = SF.load_grid(Path(args.grid))
    if g["seed"] != args.seed:
        print(
            f"refusing to solve: --seed {args.seed!r} but the grid was generated for "
            f"{g['seed']!r}. Mismatched coordinates are worse than none.",
            file=sys.stderr,
        )
        return 2
    anchors = SF.load_anchors(Path(args.locations))
    avoid_xz = np.asarray(
        [(l["x"], l["z"]) for inst in anchors.values() for l in inst], dtype=np.float64
    )
    print(
        f"seed={g['seed']} hash={g['hash']} step={g['step']}m "
        f"locations={len(avoid_xz)} clearance={args.clearance}m",
        file=sys.stderr,
    )

    files = sorted(Path(args.world).glob("*/placements.yaml"))
    if not files:
        print(f"no placements.yaml under {args.world}", file=sys.stderr)
        return 1

    failures = 0
    for path in files:
        doc = yaml.safe_load(path.read_text("utf-8"))
        preset = doc.get("preset", path.parent.name)
        # Placements within one preset coexist in the same world, so each
        # solved site becomes an exclusion zone for the next. Presets do NOT
        # exclude each other: they are alternative world states, never live
        # together, so two presets resolving to the same flat meadow is correct.
        exclude: list[tuple[float, float, float]] = []
        spawn_points: list[list[int]] = []
        for p in doc.get("placements") or []:
            src = sources.get(p.get("blueprint", ""))
            if src is not None and src.exists():
                p["containers"] = container_census(src)
            req = p.get("requirement")
            if not req:
                continue
            res = solve_one(g, anchors, avoid_xz, req, args.clearance, exclude)
            if res is None or "error" in (res or {}):
                failures += 1
                msg = (res or {}).get("error", "unknown")
                print(f"  !! {preset}/{p['id']}: {msg}")
                target = {"seed": args.seed, "error": msg} if args.write else None
            else:
                print(
                    f"  {preset:18} {p['id']:28} -> "
                    f"({res['x']:.1f}, {res['y']:.2f}, {res['z']:.1f}) "
                    f"flat={res['flat_spread_m']}m water={res['water_dist_m']}m "
                    f"anchor={res['anchor_dist_m']}m [{res['relaxation']}]"
                )
                target = {"seed": args.seed, **res}
                fp = float(req.get("footprint_m", 56))
                # keep the next building a clear footprint away, edge to edge
                exclude.append((res["x"], res["z"], fp * 1.5))
                if args.label == "primary":
                    # Wake a new character just outside the footprint, on the
                    # side the building faces, and round y UP so the spawn can
                    # never start inside terrain. ServerCharacters wants ints.
                    yaw = math.radians(float((p.get("rotation") or {}).get("yaw", 0)))
                    off = fp * 0.5 + 6.0
                    sx = res["x"] + off * math.sin(yaw)
                    sz = res["z"] + off * math.cos(yaw)
                    spawn_points.append(
                        [int(round(sx)), int(math.ceil(res["y"] + 1.0)), int(round(sz))]
                    )
            if args.write and target is not None:
                if args.label == "primary":
                    p["solved"] = target
                else:
                    p.setdefault("solved_alternatives", {})[args.seed] = target
        if args.write and args.label == "primary" and spawn_points:
            doc["spawn_points"] = spawn_points
        if args.write:
            if args.label == "primary":
                doc["seed"] = args.seed
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), "utf-8"
            )
            print(f"  wrote {path}")
    if failures:
        print(f"{failures} placement(s) unsolved", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
