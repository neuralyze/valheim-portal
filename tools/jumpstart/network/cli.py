#!/usr/bin/env python3
"""Command line for the world-network tooling.

    # the per-tier installation set, validated
    tools/jumpstart/network/cli.py tiers
    tools/jumpstart/network/cli.py installations --tier 4

    # the portal graph for a tier
    tools/jumpstart/network/cli.py portals --tier 9

    # solve endpoints and route roads between them on a real seed
    tools/jumpstart/network/cli.py route --world Ulfsland \
        --chain meadows-farm,early-dock,mountain-furnace-camp

    # everything, as one JSON document
    tools/jumpstart/network/cli.py network --world Ulfsland --tier 4 --write

Nothing here places anything in a live world. `route` boots a SANDBOX COPY of the
dedicated server to sample terrain and kills it; see terrain.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow running the file directly
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "network"

from . import DATA, HERE, JUMPSTART  # noqa: E402
from . import catalogue, plan, portals, router, sites, terrain  # noqa: E402

SEEDSCAN_LOC_DEFAULT = Path("/tmp/seedscan/loc")

# Endpoint specs for the installations this tool solves itself. The durable
# requirement lives in data/installations.yaml; these are the extra, corridor-local
# constraints the site solver needs and placements.yaml's schema does not carry.
ENDPOINT_SPECS: dict[str, dict] = {
    "meadows-farm": dict(
        biome="Meadows", pad_m=40, min_freeboard_m=3.0, max_grade_p90=0.30,
        min_purity=0.90, avoid_water_m=12,
    ),
    "early-dock": dict(
        biome="any", pad_m=28, min_freeboard_m=1.5, max_grade_p90=0.62, min_purity=0.0,
        coastal=True, max_ocean_dist_m=60, min_land_fraction=0.50,
    ),
    "sandbox-harbour": dict(
        biome="any", pad_m=52, min_freeboard_m=1.5, max_grade_p90=0.62, min_purity=0.0,
        coastal=True, max_ocean_dist_m=60, min_land_fraction=0.50,
    ),
    "mountain-furnace-camp": dict(
        biome="Mountain", pad_m=32, min_freeboard_m=30.0, max_grade_p90=0.45,
        min_purity=0.75, min_altitude_m=60,
    ),
    "mountain-outpost": dict(
        biome="Mountain", pad_m=40, min_freeboard_m=20.0, max_grade_p90=0.45,
        min_purity=0.80, min_altitude_m=45,
    ),
    "mountain-portal-shrine": dict(
        biome="Mountain", pad_m=12, min_freeboard_m=15.0, max_grade_p90=0.45,
        min_purity=0.75, min_altitude_m=45,
    ),
    "wolf-pen": dict(
        biome="Mountain", pad_m=28, min_freeboard_m=15.0, max_grade_p90=0.40,
        min_purity=0.80, min_altitude_m=45,
    ),
    "kiln-yard": dict(
        biome="BlackForest", pad_m=32, min_freeboard_m=3.0, max_grade_p90=0.30,
        min_purity=0.80, avoid_water_m=8,
    ),
    "meadows-starter-hall": dict(
        biome="Meadows", pad_m=60, min_freeboard_m=3.0, max_grade_p90=0.35,
        min_purity=0.85, avoid_water_m=8,
    ),
}


def _locations(path: Path, seed: str) -> list[dict]:
    """ZoneSystem location instances for a seed, from the tools/seedscan LocScan dump."""
    if not path.exists():
        return []
    for f in sorted(path.glob("*.json")):
        try:
            blob = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        if blob.get("seed") == seed or blob.get("seedNameFromWorld") == seed:
            return blob.get("locations") or []
    return []


def _world(name: str) -> dict:
    import yaml

    path = JUMPSTART / "worlds" / name / "world.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def cmd_tiers(args) -> int:
    cat = catalogue.load()
    problems = cat.validate()
    pol = portals.load_policy()
    rows = []
    for tier, preset in sorted(cat.tiers.items()):
        s = cat.set_for_tier(tier)
        g = portals.build(cat, tier, pol)
        rows.append(
            {
                "tier": tier,
                "preset": preset,
                "installations": len(s),
                "new_this_tier": [i.id for i in s if i.first_tier == tier],
                "pieces": sum(i.piece_estimate() for i in s),
                "home_base": cat.home_base_for_tier(tier).id,
                "portal_nodes": len(g.nodes),
                "portal_hubs": len(g.hubs()),
                "portal_edges": len(g.edges),
                "portal_max_hops": g.max_hops(),
                "portal_pieces": g.piece_total(),
                "portal_pieces_to_place_by_hand": sum(n.shortfall for n in g.nodes.values()),
            }
        )
    text = json.dumps(
        {
            "schema_version": 1,
            "world": cat.world,
            "seed": cat.seed,
            "owner": "WorldNetwork",
            "generated_by": "tools/jumpstart/network/cli.py tiers",
            "problems": problems,
            "tiers": rows,
        },
        indent=1,
    )
    if getattr(args, "write", False):
        out = DATA / cat.world / "network-summary.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 1 if problems else 0


def cmd_installations(args) -> int:
    cat = catalogue.load()
    out = [i.as_dict() for i in cat.set_for_tier(args.tier)]
    print(json.dumps({"tier": args.tier, "preset": cat.tiers[args.tier], "installations": out}, indent=1))
    return 0


def cmd_portals(args) -> int:
    cat = catalogue.load()
    g = portals.build(cat, args.tier)
    doc = g.as_dict()
    doc["declared_vs_graph"] = portals.declared_vs_graph(cat, g)
    print(json.dumps(doc, indent=1))
    return 0


def _solve_chain(args, cat, pol):
    """Sample the corridor, solve every stop, route the chain, emit the plans."""
    world = _world(args.world)
    seed = args.seed or world.get("seed") or cat.seed
    ids = [s.strip() for s in args.chain.split(",") if s.strip()]
    by_id = {i.id: i for i in cat.installations}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise SystemExit(f"unknown installation id(s): {missing}")
    unspecced = [i for i in ids if i not in ENDPOINT_SPECS]
    if unspecced:
        raise SystemExit(
            f"no endpoint spec for {unspecced}: add one to cli.ENDPOINT_SPECS, or pass "
            f"--at id=x,z to supply a coordinate this tool should not be choosing"
        )

    fixed: dict[str, tuple[int, int]] = {}
    for pair in args.at or []:
        k, _, v = pair.partition("=")
        x, _, z = v.partition(",")
        fixed[k.strip()] = (int(x), int(z))

    hint: dict[str, tuple[int, int]] = {}
    for pair in args.hint or []:
        k, _, v = pair.partition("=")
        x, _, z = v.partition(",")
        hint[k.strip()] = (int(x), int(z))
    box = [int(v) for v in args.box.split(",")] if args.box else None
    if box is None:
        if not fixed:
            raise SystemExit("--box x0,x1,z0,z1 is required unless every stop has --at")
        xs = [p[0] for p in fixed.values()]
        zs = [p[1] for p in fixed.values()]
        box = [min(xs) - 300, max(xs) + 300, min(zs) - 300, max(zs) + 300]

    work = Path(args.work)
    windows = terrain.tile(box[0], box[1], box[2], box[3], half=args.half, prefix="cor")
    print(
        f"# sampling {len(windows)} window(s), {sum(w.samples for w in windows):,} samples, "
        f"seed {seed}, river-inclusive, step 1.0 m",
        file=sys.stderr,
    )
    binpath = terrain.run(windows, seed, work, vh_src=args.vh_src)
    cor = terrain.load(binpath, seed, only_prefix="cor")
    fields = sites._fields(cor)
    locs = _locations(Path(args.locations), seed)

    solved: dict[str, sites.Site] = {}
    exclude: list[tuple[int, int, float]] = []
    for iid in ids:
        if iid in fixed:
            x, z = fixed[iid]
            y, bid = cor.at(x, z)
            solved[iid] = sites.Site(
                id=iid, x=x, z=z, y=y, biome=terrain.BIOME_NAME.get(bid, "?"),
                freeboard_m=y - terrain.WATER_LEVEL, pad_m=0, grade_p90=0.0, grade_max=0.0,
                biome_purity=1.0, water_dist_m=float(fields["dist_water"][z - cor.z0, x - cor.x0]),
                ocean_dist_m=float(fields["dist_ocean"][z - cor.z0, x - cor.x0]), score=0.0,
                evidence={"source": "--at, supplied by the caller and only height-verified here"},
            )
        else:
            spec = dict(ENDPOINT_SPECS[iid], id=iid)
            if iid in hint:
                spec["near"] = hint[iid]
                spec["within_m"] = args.within
            solved[iid] = sites.solve(cor, spec, fields, exclude=exclude)
        st = solved[iid]
        exclude.append((st.x, st.z, max(16.0, st.pad_m * 0.75)))

    stops = [(iid, (solved[iid].x, solved[iid].z)) for iid in ids]
    limits = {}
    for (na, _), (nb, _) in zip(stops, stops[1:]):
        if "mountain" in nb or "mountain" in na:
            limits[f"{na}->{nb}"] = float(pol["grade"]["max_spur"])
    routes = router.chain(cor, stops, pol, locs, grade_limits=limits)
    plans = [plan.build(r, pol, cor) for r in routes]

    doorsteps = {}
    for iid in ids:
        toward = None
        for r in routes:
            if r.a == iid:
                toward = (r.nodes[1].x, r.nodes[1].z)
                break
            if r.b == iid:
                toward = (r.nodes[-2].x, r.nodes[-2].z)
        if toward is None:
            continue
        inst = by_id[iid]
        fp = tuple(inst.blueprint.footprint_xzy_m[:2]) if inst.blueprint else None
        try:
            doorsteps[iid] = sites.doorstep(
                cor, solved[iid], toward, fields,
                preset=cat.tiers.get(inst.first_tier),
                footprint_xz_m=fp,
            )
        except ValueError as exc:
            doorsteps[iid] = {
                "error": str(exc),
                "installation": iid,
                "preset": cat.tiers.get(inst.first_tier),
            }

    return {
        "world": args.world,
        "seed": seed,
        "corridor": {
            "x0": cor.x0,
            "z0": cor.z0,
            "shape": list(cor.shape),
            "coverage": round(cor.coverage(), 4),
            "sampler": "tools/jumpstart/blueprints/run_patchscan.sh (river-inclusive, Pregenerate on)",
            "water_datum_m": terrain.WATER_LEVEL,
            "samples": sum(w.samples for w in windows),
        },
        "zonesystem_locations_loaded": len(locs),
        "sites": {k: v.as_dict() for k, v in solved.items()},
        "doorsteps": doorsteps,
        "routes": [r.as_dict() for r in routes],
        "plans": [p.as_dict() for p in plans],
        "paved_alternative": [plan.paved_alternative(r, pol) for r in routes],
        "totals": plan.combine(plans),
    }


def cmd_route(args) -> int:
    cat = catalogue.load()
    pol = portals.load_policy()
    doc = _solve_chain(args, cat, pol)
    if args.rcon:
        # MEASURED: `spawn <prefab> <x> <y> <z> -rotation <x> <y> <z>` resolves the
        # prefab from ZNetScene, instantiates it, calls ZNetView.FinishGhostInit and
        # destroys the GameObject, leaving a persistent ZDO. Works with no players
        # online. It cannot place terrain and it cannot stamp a blueprint.
        for p in doc["plans"]:
            print(f"# {p['from']} -> {p['to']}: {p['rcon_placeable']} spawnable item(s)")
            for item in p["items"]:
                if item["channel"] != "piece":
                    continue
                for _ in range(item["count"]):
                    print(
                        f"spawn {item['prefab']} {item['x']:.1f} {item['y']:.2f} "
                        f"{item['z']:.1f} -rotation 0 {item['yaw']:.1f} 0"
                    )
        return 0
    print(json.dumps(doc, indent=1))
    bad = [b for p in doc["plans"] for b in p["blockers"]] + [
        a for r in doc["routes"] for a in r["audit"]
    ]
    return 1 if bad else 0


def cmd_network(args) -> int:
    cat = catalogue.load()
    pol = portals.load_policy()
    problems = cat.validate()
    g = portals.build(cat, args.tier)
    doc = {
        "schema_version": 1,
        "world": args.world,
        "seed": args.seed or cat.seed,
        "owner": "WorldNetwork",
        "generated_by": "tools/jumpstart/network/cli.py network",
        "tier": args.tier,
        "preset": cat.tiers[args.tier],
        "catalogue_problems": problems,
        "installations": [i.as_dict() for i in cat.set_for_tier(args.tier)],
        "portal_graph": g.as_dict(),
        "portal_declared_vs_graph": portals.declared_vs_graph(cat, g),
        "policy": {
            "water": pol["water"],
            "grade": pol["grade"],
            "roadway": {k: v for k, v in pol["roadway"].items()},
            "portals": pol["portals"],
        },
    }
    if args.chain:
        doc["chain"] = _solve_chain(args, cat, pol)
    text = json.dumps(doc, indent=1)
    if args.write:
        out = DATA / args.world / f"network-tier{args.tier}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="network", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tiers", help="per-tier installation set and portal summary")
    p.add_argument("--write", action="store_true", help="write to data/<world>/network-summary.json")
    p.set_defaults(fn=cmd_tiers)

    p = sub.add_parser("installations", help="the accumulated set for one tier")
    p.add_argument("--tier", type=int, required=True)
    p.set_defaults(fn=cmd_installations)

    p = sub.add_parser("portals", help="the portal graph for one tier")
    p.add_argument("--tier", type=int, required=True)
    p.set_defaults(fn=cmd_portals)

    def route_args(p):
        p.add_argument("--world", default="Ulfsland")
        p.add_argument("--seed", default=None)
        p.add_argument("--chain", default=None, help="comma-separated installation ids")
        p.add_argument("--at", action="append", help="id=x,z to fix a stop instead of solving it")
        p.add_argument("--box", default=None, help="x0,x1,z0,z1 corridor to sample")
        p.add_argument("--half", type=int, default=200, help="window half-extent in metres")
        p.add_argument("--within", type=float, default=600.0, help="radius around a --hint")
        p.add_argument("--work", default="/tmp/worldnetwork", help="scratch dir for the sandbox and patch")
        p.add_argument("--vh-src", default=None)
        p.add_argument("--locations", default=str(SEEDSCAN_LOC_DEFAULT), help="tools/seedscan LocScan dump dir")
        p.add_argument("--hint", action="append", help="id=x,z to bias the site search near a point")
        p.add_argument("--rcon", action="store_true", help="print the RCON spawn lines instead of JSON")

    p = sub.add_parser("route", help="solve endpoints and route roads between them")
    route_args(p)
    p.set_defaults(fn=cmd_route)

    p = sub.add_parser("network", help="the whole model for one tier as one JSON document")
    route_args(p)
    p.add_argument("--tier", type=int, required=True)
    p.add_argument("--write", action="store_true", help="write to data/<world>/network-tier<N>.json")
    p.set_defaults(fn=cmd_network)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
