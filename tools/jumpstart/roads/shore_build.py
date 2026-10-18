#!/usr/bin/env python3
"""Construct generated waterfronts through the existing guarded transport writer.

All endpoints and the entire paired voyage are measured before any selected unit
writes. Uses the established transport reconciliation, LiveBuilder, immutable
spawn plans, real boats, save postconditions and readback; never terrain edits.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE.parent) not in sys.path:
    sys.path.append(str(HERE.parent))

import shores as S
import north_transport as NT

ROAD_OF = {
    "harbour-south": "T13-temple-harboursouth",
    "boathouse-strait": "S12-brgs1-boathouse",
    "dock-stathub": "S13-stathub-dock",
    "ferry-east": "S14-eastlobe-ferryeast",
    "harbour-vestvik": "S15-vestvik-harbour",
    "dock-dockshore": "S16-dockshore-dock",
    "harbour-stenvik-port": "S17-stenvik-port",
    "dock-northcape": "S18-northcape-dock",
    "ferry-terminal-eastisle": "F1-ferry-eastisle-inland",
}


def plan_site(site, surface):
    root, bearing = site["root"], site["bearing"]
    ground = lambda x, z: surface.at(x, z)[0]
    NT._root_access(ground(*root), S.DECK_Y, site["id"])
    bed = S.A.Bed.applied(ground, "this seed's rendered field plus current world's ordered applied terrain")
    pieces = (S.A.slip(root, bearing, site["length_m"], site["inner_width_m"],
                      S.DECK_Y, bed, name=site["id"])
              if site["kind"] == "slip" else
              S.A.jetty(root, bearing, site["length_m"], S.DECK_Y, bed,
                        width_tiles=site["width_tiles"], name=site["id"]))
    pieces = S.emitted_pieces(pieces)
    verdict = S.A.verify(pieces, bed, note=site["id"])
    if not verdict["ok"]:
        raise SystemExit(f"{site['id']}: actual applied-ground support refuses: {verdict['failures']}")
    boat = site["boat"]
    if site.get("large_ship") and boat["prefab"] not in NT.LARGE_VESSELS:
        raise SystemExit(f"{site['id']}: this named north dock requires a measured large Odin vessel")
    depths = surface.hull_depths(boat["yaw"], boat["x"], boat["z"], boat["prefab"])
    if not NT._hull_usable(depths, boat["prefab"]):
        raise SystemExit(f"{site['id']}: generated boat pose no longer has its measured full-hull clearance on applied ground: {depths}")
    segment = ROAD_OF.get(site["node"])
    commands = [S.A.command(piece) for piece in pieces]
    dock_op = NT._plan_op(site["id"], commands,
                          [root[0], S.DECK_Y, root[1]], segment or "ferry-east-isle")
    dock_op["meta"].update(shore_terminal=site["id"], structural_verdict=verdict)
    if segment is None:
        dock_op["meta"].update(stands_on=[], shore_ground_source="measured natural destination shoreline",
                               transport_edge="ferry-east-isle")
    hull = dict(min_depth_m=depths[0], max_depth_m=depths[1], connected=depths[2],
                samples=depths[3], required_depth_m=S.WF.VESSELS[boat["prefab"]]["draught_m"] + S.WF.KEEL_CLEARANCE_M)
    boat_op = S.WF.boat_op(site["id"], boat["prefab"], boat["x"], boat["z"], boat["yaw"], hull)
    # Keep the evidence/model at the four-decimal pose the native wire emits,
    # rather than the boat factory's display-only two-decimal params.
    boat_op["params"]["pos"] = [boat["x"], S.W.WATER_LEVEL, boat["z"]]
    boat_op["expect"]["prefab_count"][0]["pos"] = [boat["x"], boat["z"]]
    # Reuse the existing transport role's pose reconciliation at replay, rather
    # than the advisory-radius guard that can mistake a neighbouring boat for us.
    boat_op["params"]["role"] = NT.ROLE
    boat_op["meta"].update(shore_terminal=site["id"], north_terminal=site["id"],
                            stands_on=[segment] if segment else [])
    ux, uz = S.A._unit(bearing)
    lx, lz = uz, -ux
    along = site["length_m"] * 0.6
    walk = [list(root)]
    if site["kind"] == "slip":
        offset = site["inner_width_m"] / 2 + 0.5
        walk.append([root[0] + lx*offset, root[1] + lz*offset])
        walk.append([root[0] + lx*offset + ux*along,
                     root[1] + lz*offset + uz*along])
    else:
        offset = -(site["width_m"] / 2 - 0.5)
        walk.append([root[0] + ux*along, root[1] + uz*along])
        walk.append([root[0] + ux*along + lx*offset,
                     root[1] + uz*along + lz*offset])
    return dict(terminal_id=site["id"], seed=S.worldseed.seed(), world=S.worldseed.world(),
        root_xz=list(root), segment_id=segment, final_departure=False, lighthouse=None,
        walk_xz=walk,
        ops=[dock_op, boat_op], pieces=NT._piece_rows(commands),
        boats=[dict(prefab=boat["prefab"], pos=boat_op["params"]["pos"],
                    yaw_deg=boat["yaw"], hull=hull)])


def plan(sites, fields, surface):
    S.require_pairs(sites)
    plans = {site["id"]: plan_site(site, surface) for site in sites}
    passages = [NT.measure_passage(site["pair_requirement"], plans, fields, surface=surface)
                for site in sites if site.get("pair_requirement")]
    return plans, passages


def road_contract(sites):
    return dict(seed=S.worldseed.seed(), world=S.worldseed.world(),
        terminals={site["id"]: dict(segment_id=ROAD_OF[site["node"]], xz=site["root"])
                   for site in sites if site["node"] in ROAD_OF})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("plan", "build", "measure"))
    ap.add_argument("--field", required=True)
    ap.add_argument("--terminal")
    ap.add_argument("--actor")
    ap.add_argument("--note")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out")
    S.worldseed.add_arguments(ap)
    args = ap.parse_args(argv)
    seed, world = S.worldseed.apply(args)
    if args.apply and args.command != "build":
        ap.error("--apply is only valid with build")
    if args.command in ("build", "measure") and not args.actor:
        ap.error("build/measure require the actual --actor")
    sites = S.load()
    raw_fields = S.load_fields(args.field)
    # The producer binds a specific source. An arbitrary same-seed rescan cannot
    # be substituted after terminal siting without re-running that producer.
    import hashlib
    import yaml
    publication = yaml.safe_load(S.publication_path().read_text())
    with Path(args.field).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != publication["field_sha256"]:
        raise SystemExit("shore construction field differs from the solved publication")
    fields = NT.ScopedFields(raw_fields, seed, source=args.field)
    view = NT.LedgerView(world, seed)
    surface = NT.Surface(fields, world=world, actor=args.actor or "ShoreTransport", led=view)
    plans, passages = plan(sites, fields, surface)
    selected = list(plans.values()) if args.terminal is None else [plans[args.terminal]] if args.terminal in plans else []
    if not selected:
        raise SystemExit(f"unknown generated shoreline terminal {args.terminal!r}")
    report = dict(schema_version=1, seed=seed, world=world, terminals=selected,
                  passages=passages, applied=False)
    if args.command == "measure" or (args.command == "build" and args.apply):
        from ledger.live import LiveBuilder
        with LiveBuilder(actor=args.actor, world=world) as builder:
            if builder.led.head != view.head:
                raise SystemExit("shore ground ledger changed during physical preflight; reacquire the plans")
            if args.command == "build":
                NT._require_road_ground(road_contract(sites), builder.led)
                pending = [(terminal, NT.reconciled_operations(builder, terminal))
                           for terminal in selected]
                if args.note:
                    builder.note(args.note, role=NT.ROLE, site_id=args.terminal or "generated-shorelines")
                for terminal, operations in pending:
                    S.WF.emit(builder, operations, label=terminal["terminal_id"])
                boats = [op["expect"]["prefab_count"][0] for p in selected
                         for op in p["ops"] if op["op"] == "spawn"]
                builder.emit("save", params=dict(role=NT.ROLE), wire=["save"],
                    requires=dict(mods=[], prefabs=[], blobs=[]), expect=dict(prefab_count=boats))
                report["applied"] = True
            report["physical"] = NT.measure(selected, builder=builder)
    text = json.dumps(report, indent=2, default=float) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
