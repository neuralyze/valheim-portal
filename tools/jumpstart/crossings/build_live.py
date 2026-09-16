#!/usr/bin/env python3
"""Build planned crossing structures into the LIVE world, through the ledger.

    build_live.py --list
    build_live.py --structure bridge-s1-temple-strait [--dry-run]
    build_live.py --structure all

Every write goes through `tools/jumpstart/ledger/live.py`, which appends and
fsyncs the record BEFORE sending, sends through the same `send_wire` the replay
driver uses, and verifies the declared postcondition with the same
`check_expect` -- raising rather than returning green if it does not hold. That
is not belt-and-braces: two of the ledger's invariants (one compiler per zone,
and `flatten: "FORBIDDEN"`) can only see operations that went through it, so an
ad-hoc `spawn_object` here would leave the guard that exists to prevent the
early-dock defect sitting unfired while a road segment flattened a pier.

The ops themselves are NOT written here. They come from `plan.py`, which is the
only thing that computes them, so what gets built is what was verified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(HERE))

from ledger.live import LiveBuilder  # noqa: E402

LEDGER_OPS = HERE / "out" / "ledger.jsonl"
STRUCTURES = HERE / "structures.yaml"

# Measurements worth putting in the log as `observe`, because the next person
# siting a waterfront should use these instruments rather than re-derive a wrong
# one. Main asked for the shoreline test specifically.
OBSERVATIONS = [
    dict(what="shoreline test for siting a waterfront",
         method=("MEASURED. A waterfront site must be a sample that is LAND "
                 "(generated h > 30.5, i.e. 0.5 m clear of the measured "
                 "ZoneSystem::c_WaterLevel = 30.0) AND 4-connected to a wet "
                 "sample, and it must additionally admit a pier: sweep bearings "
                 "at 5 deg and require the outer half of the pier line to be "
                 "CONTIGUOUSLY wet with the head deeper than the hull draught. "
                 "Distance to the nearest WET CELL answers a different question "
                 "and is not usable: at Settlements' Vestvik (-906,1846) the "
                 "reported 94 m to water becomes 133.7 m to the nearest "
                 "shoreline sample, and that shoreline is too shallow for a 22 m "
                 "pier at all 72 bearings. The first shoreline that admits one is "
                 "(-1031.5,1656.5), 227.3 m from the village, berth 7.11 m."),
         tool="tools/jumpstart/crossings/survey.py + plan.py:aim_seaward",
         value=dict(land_threshold_y=30.5, water_level_y=30.0,
                    connectivity=4, bearing_sweep_deg=5,
                    vestvik_reported_m=94.0,
                    vestvik_nearest_shoreline_m=133.7,
                    vestvik_first_viable_quay=[-1031.5, 1656.5],
                    vestvik_first_viable_quay_m=227.3,
                    vestvik_first_viable_berth_m=7.11),
         units="m"),
    dict(what="structural support algorithm used to accept every assembly",
         method=("MEASURED from assembly_valheim.dll 1.0.12. "
                 "WearNTear::UpdateSupport: support(child) = max over touching "
                 "neighbours of support(n)*(1 - loss*(d+0.1)), d being "
                 "centre-of-mass distance, loss the child's horizontalLoss or a "
                 "Lerp to verticalLoss by the support direction's angle; a piece "
                 "whose collider overlaps the `terrain` layer is pinned to "
                 "maxSupport. WearNTear::HaveSupport: alive iff support >= "
                 "minSupport. WearNTear::GetMaterialProperties gives the table as "
                 "a pure function of m_materialType. The reason this had to run "
                 "offline: UpdateWear begins `if "
                 "(ZNetScene.OutsideActiveArea(pos)) { m_support = "
                 "GetMaxSupport(); return; }`, so support is only ever EVALUATED "
                 "when a player is nearby and an unsound bridge looks perfect in "
                 "a save until the operator walks onto it."),
         tool="tools/jumpstart/crossings/assemble.py:verify",
         value=dict(
             material_table={
                 "Wood": [100, 10, 0.2, 0.125],
                 "Stone": [1000, 100, 1.0, 0.125],
                 "Iron": [1500, 20, 0.076923, 0.076923],
                 "HardWood": [140, 10, 0.166667, 0.1],
                 "Marble": [1500, 100, 0.5, 0.125],
                 "Ashstone": [2000, 100, 0.333333, 0.1],
                 "Ancient": [5000, 100, 0.25, 0.066667],
                 "Ice": [1000, 100, 0.333333, 0.125],
                 "Timberwood": [200, 10, 0.2, 0.076923]},
             table_columns=["maxSupport", "minSupport", "horizontalLoss",
                            "verticalLoss"],
             safety_factor=2.0,
             approximation=("support distance and direction taken COM-to-COM "
                            "rather than from FindSupportPoint's contact point; "
                            "valid for the axis-aligned face-to-face geometry "
                            "generated here, NOT for oblique geometry"))),
    dict(what="boat persistence and the absence of a mooring mechanic",
         method=("MEASURED for persistence, INFERRED for drift. Persistence is a "
                 "complete chain: all five ship prefabs carry "
                 "ZNetView.m_persistent = 1 and m_type = 1 "
                 "(ZDO.ObjectType.Prioritized) (scanned off the loaded prefabs); "
                 "ZNetView.Awake copies that into the ZDO's DataFlags bit 4, "
                 "which is what ZDO.get_Persistent reads; and the save clone "
                 "builder ZDOMan::PrepareSave -> GetSaveClonePerChunk -> "
                 "AddObjectsPerChunk keeps a ZDO iff `zdo.Persistent` and its "
                 "prefab is not in Game.PortalPrefabHash. So a spawned Longship "
                 "is written to the chunked save. DRIFT IS NOT MEASURED AND "
                 "CANNOT BE FROM THIS HOST: Valheim has no mooring mechanic, and "
                 "Ship::CustomFixedUpdate returns unless IsOwner, forces "
                 "m_speed=Stop and m_rudderValue=0 when m_players.Count == 0, and "
                 "then still applies buoyancy/damping AddForceAtPosition plus "
                 "ApplyEdgeForce every tick -- so an unmanned boat is inert while "
                 "nobody is in its active area and subject to wave forces while "
                 "somebody is. THE OPERATOR IS THE FIRST REAL TEST. If a boat is "
                 "found far from its berth, the cause is here and not in the "
                 "terminal geometry."),
         tool="tools/jumpstart/crossings/run_piecematerial.sh + monodis on "
              "assembly_valheim.dll",
         value=dict(prefabs={"VikingShip": dict(persistent=True, ztype=1,
                                                hp=1000, mass=2000,
                                                hull_x_m=9.16, hull_z_m=21.56,
                                                water_level_offset=1.5),
                             "Karve": dict(persistent=True, hp=500, mass=1000,
                                           hull_x_m=7.68, hull_z_m=10.11,
                                           water_level_offset=1.2),
                             "Raft": dict(persistent=True, hp=300, mass=1000,
                                          hull_x_m=6.09, hull_z_m=6.22,
                                          water_level_offset=1.5)},
                    mooring_mechanic_exists=False,
                    drift_verified=False,
                    mitigation=("three-sided slip 11 m inner width sized from the "
                                "measured hull, plus a boat at BOTH ferry "
                                "terminals so a drifted boat cannot strand the "
                                "crossing"))),
]


def load_ops() -> dict[str, list[dict]]:
    if not LEDGER_OPS.exists():
        raise SystemExit(f"{LEDGER_OPS} missing; run plan.py first")
    by_site: dict[str, list[dict]] = {}
    for line in LEDGER_OPS.read_text().splitlines():
        if not line.strip():
            continue
        op = json.loads(line)
        sid = (op.get("params") or {}).get("site_id") or "_notes"
        by_site.setdefault(sid, []).append(op)
    return by_site


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--structure", default=None,
                    help="structure id, or 'all'")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--validate", action="store_true",
                    help="build the records and run schema.validate offline. No "
                         "append, no console. `LiveBuilder(dry=True)` is NOT a "
                         "dry run: MEASURED by RoadNet, it appends to the shared "
                         "ledger and runs check_expect against the LIVE server, "
                         "so it pollutes the log and then fails a postcondition "
                         "for a command it deliberately did not send.")
    ap.add_argument("--note", default=None,
                    help="a note to append before the ops (e.g. why a re-emit)")
    ap.add_argument("--notes", action="store_true",
                    help="emit the boundary notes and observations only")
    args = ap.parse_args(argv)

    by_site = load_ops()
    if args.list:
        for sid, ops in by_site.items():
            kinds = {}
            for o in ops:
                kinds[o["op"]] = kinds.get(o["op"], 0) + 1
            wires = sum(len(o.get("wire") or []) for o in ops)
            print(f"{sid:32s} ops={len(ops):3d} wire_lines={wires:4d} {kinds}")
        return 0

    if args.validate:
        from ledger import schema
        bad = 0
        for sid, ops in by_site.items():
            for i, op in enumerate(ops):
                rec = dict(seq=i + 1, ts="1970-01-01T00:00:00Z",
                           actor="Crossings", prev="0" * 64, **op)
                probs = schema.validate(rec)
                if probs:
                    bad += 1
                    print(f"{sid} {op['op']}: {probs}")
        print(f"validate: {sum(len(v) for v in by_site.values())} records, "
              f"{bad} invalid")
        return 1 if bad else 0

    targets: list[str]
    if args.notes:
        targets = []
    elif args.structure == "all":
        targets = [s for s in by_site if s != "_notes"]
    elif args.structure:
        if args.structure not in by_site:
            raise SystemExit(f"unknown structure {args.structure!r}; "
                             f"have {sorted(by_site)}")
        targets = [args.structure]
    else:
        raise SystemExit("pass --structure <id|all>, --notes, or --list")

    with LiveBuilder(actor="Crossings", dry=args.dry_run) as b:
        if args.notes or args.structure == "all":
            for note in by_site.get("_notes", []):
                b.note(note["params"]["text"])
            for obs in OBSERVATIONS:
                b.observe(obs["what"], obs["method"], obs["tool"], obs["value"],
                          **({"units": obs["units"]} if obs.get("units") else {}))
        if args.note:
            b.note(args.note)
        for sid in targets:
            print(f"=== {sid}", flush=True)
            for op in by_site[sid]:
                # A `spawn_plan` names its plan by digest in `requires.blobs`,
                # and the ledger refuses an op whose blob it cannot read -- the
                # replay will not guess at a substitute. So the plan text goes
                # into the blob store FIRST, built from the same wire lines that
                # were hashed, so the digest cannot disagree with the bytes.
                if op["op"] == "spawn_plan":
                    text = "\n".join(op["wire"]) + "\n"
                    sha = b.blob(text.encode(),
                                 note=f"{sid} spawn_object plan, "
                                      f"{len(op['wire'])} commands")
                    want = op["params"]["plan_sha256"]
                    if sha != want:
                        raise SystemExit(
                            f"{sid}: stored blob {sha} != declared plan_sha256 "
                            f"{want}; plan.py and build_live.py disagree about "
                            f"the plan bytes and the replay would be wrong")
                res = b.emit(op["op"], params=op["params"],
                             wire=op.get("wire"), requires=op.get("requires"),
                             expect=op.get("expect"), meta=op.get("meta"))
                print(f"    {op['op']:16s} "
                      f"wire={len(op.get('wire') or []):4d} ok", flush=True)
        print(json.dumps(b.close(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
