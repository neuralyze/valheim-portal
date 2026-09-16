#!/usr/bin/env python3
"""Move a refused town pad the SMALLEST distance that clears a mod village.

WHAT WAS REFUSED AND WHY IT IS NOT A SITING ERROR. `Settlements` sited Stenvik
against a location dump that is MOD-FREE: it carries the 12,301 vanilla
instances of this seed and none of More_World_Locations' POIs. Eight of
Stenvik's eighteen pads landed on top of a mod hamlet, and `build.py`'s
`cylinder_contents` caught every one of them live -- `objects_count id=*` over
the exact cylinder `objects_remove` would empty, which is the only check that
measures the hazard directly. What it found is FURNISHED HOUSES, e.g. for
`stenvik-cottage-1`: a bed, 2 chests, 4 ground torches, 2 deer rugs, a
portal_wood, 16 woodwall, 12 wood_floor, 12 wood_roof. Deleting those is the
one damage class this project records as unrepairable.

THE DECISION, and it is not re-litigated here: the hamlet stays, the town
centre and both streets stay, and each refused pad MOVES the smallest distance
that clears. A town grown around an existing hamlet is a better world than
either a deleted hamlet or a re-sited town, and the operator walks through
both.

WHAT "CLEARS" MEANS, in the order the checks are cheap:

  1. STREET FIDELITY, offline. The building keeps the SAME SIDE of the same
     street and a perpendicular offset within a window of its planned one, so
     the street frontage the plan designed still exists. A nudge that crosses
     the street is a different town.
  2. PAD SOLVENCY, offline, and it is re-solved rather than carried: a pad's
     `target_y` is the MEDIAN of the generated heights under its own rotated
     footprint, so a pad that moves 6 m has a different median. Reusing the
     old one is how a building ends up 1.5 m into a hillside. `terrain1m.
     pad_bill` at the new centre answers target_y, cut, fill, the +/-8 m clamp
     and wet samples.
  3. NO OVERLAP with a standing sibling, offline: body footprint against body
     footprint at BUILD YAW plus a margin, AND the standing sibling must be
     outside the candidate's own `objects_count` verification disc -- because
     that check asserts `total: 0` and a neighbour's wall inside it fails a
     correct clear.
  4. THE PIECE-REACH GATE, offline, `clearance.clear_radius_room`: the
     irreversible op may not reach any location's pieces. REFUSE, never clip.
  5. THE TERRAIN GATE, offline but through the REAL code path: `flatten.py`
     driven as a library for the candidate's own pad, then `delta_gate` over
     the written samples and `sample_verdict` under the non-destructive
     budget. Computed here so a nudge is not accepted and then refused at the
     wire.
  6. THE LIVE CENSUS, last because it costs a round trip: `objects_count id=*`
     over the candidate's exact clearing cylinder. THE DUMP CANNOT ANSWER
     THIS. It is the same call that produced the eight refusals, and a
     candidate is only accepted when it comes back with nothing but the
     measured natural set.

A pad that cannot clear inside `MAX_NUDGE_M` is DROPPED and the reason
recorded. Sixteen buildings around a mod hamlet beats seventeen with a
deleted one.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build as B  # noqa: E402
import clearance  # noqa: E402
import terrain1m as T  # noqa: E402

# How far a building may move. TASTE with a reason: the mod houses measured in
# these cylinders are 8-14 m across and the cylinders are 10-14 m in radius, so
# clearing one takes single-digit metres of offset; and beyond ~14 m a
# "nudged" building is on somebody else's frontage and the plan's street
# composition stops meaning anything. Searched at 1 m, nearest first, so the
# accepted move is the smallest that works rather than the first that is
# comfortable.
MAX_NUDGE_M = 14.0

# How much the perpendicular distance to the building's own street may change,
# and it is asymmetric on purpose: moving AWAY from the street (+) keeps the
# frontage and only widens the verge, while moving TOWARD it (-) eats the
# carriageway RoadNet terminates its ribbons against.
STREET_NEARER_M = 2.0
STREET_FURTHER_M = 8.0

# Air between two bodies' footprints. Two buildings 0.5 m apart read as one
# building with a crack in it, and the clearing cylinders would overlap enough
# that the second clear's verification disc sees the first building.
SIBLING_MARGIN_M = 2.0


def ledger_records() -> list[dict]:
    path = B.JUMPSTART / "ledger/runs/Ulfsland/ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def built_units(records: list[dict]) -> list[str]:
    """Which units STAND, measured from the ledger's own spawn_plan records.

    `spawn_plan` is the record that places a body, and `emit` raises unless its
    postcondition held -- so a `spawn_plan` in the chain is a building that was
    counted in the world, not one that was intended.
    """
    out = []
    for rec in records:
        if rec["op"] == "spawn_plan":
            sid = rec["params"].get("site_id")
            if sid and sid not in out:
                out.append(sid)
    return out


def refused_units(records: list[dict]) -> list[str]:
    out = []
    for rec in records:
        if rec["op"] == "note" and str(rec["params"].get("text", "")
                                       ).startswith("REFUSED "):
            sid = rec["params"].get("site_id")
            if sid and sid not in out:
                out.append(sid)
    return out


def rect_corners(x: float, z: float, w: float, d: float, yaw_deg: float
                 ) -> list[tuple[float, float]]:
    a = math.radians(yaw_deg)
    ca, sa = math.cos(a), math.sin(a)
    hw, hd = w / 2, d / 2
    return [(x + lx * ca - lz * sa, z + lx * sa + lz * ca)
            for lx, lz in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]


def rects_overlap(a: tuple, b: tuple, margin_m: float = 0.0) -> bool:
    """Separating-axis test on two oriented rectangles, each grown by margin/2.

    Exact rather than a circle test: Stenvik's bodies are 4.4 x 4.4 up to
    20.1 x 20.1 at yaws of 30 and 80 degrees, and a bounding circle on the
    20 m hall would veto legal cells 7 m away from anything.
    """
    (ax, az, aw, ad, ayaw) = a
    (bx, bz, bw, bd, byaw) = b
    ca = rect_corners(ax, az, aw + margin_m, ad + margin_m, ayaw)
    cb = rect_corners(bx, bz, bw + margin_m, bd + margin_m, byaw)
    for poly, other in ((ca, cb), (cb, ca)):
        for i in range(4):
            x0, z0 = poly[i]
            x1, z1 = poly[(i + 1) % 4]
            nx, nz = -(z1 - z0), (x1 - x0)
            pa = [nx * px + nz * pz for px, pz in poly]
            pb = [nx * px + nz * pz for px, pz in other]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def rect_disc_overlap(rect: tuple, cx: float, cz: float, r: float) -> bool:
    """Does an oriented rectangle reach inside a disc? Used for the clear's
    own verification circle, which asserts `total: 0`."""
    x, z, w, d, yaw = rect
    a = math.radians(-yaw)
    ca, sa = math.cos(a), math.sin(a)
    dx, dz = cx - x, cz - z
    lx, lz = dx * ca - dz * sa, dx * sa + dz * ca
    qx = max(-w / 2, min(lx, w / 2))
    qz = max(-d / 2, min(lz, d / 2))
    return math.hypot(lx - qx, lz - qz) <= r


def street_of(town: dict, building: dict) -> dict | None:
    for s in town["streets"]:
        if s["name"] == building.get("street"):
            return s
    return None


def signed_offset(street: dict, x: float, z: float) -> float:
    (x0, z0), (x1, z1) = street["nodes"][0], street["nodes"][-1]
    dx, dz = x1 - x0, z1 - z0
    length = math.hypot(dx, dz)
    return ((x - x0) * dz - (z - z0) * dx) / length


def candidate_offsets(max_m: float) -> list[tuple[float, float]]:
    span = int(max_m)
    out = [(dx * 1.0, dz * 1.0)
           for dx in range(-span, span + 1)
           for dz in range(-span, span + 1)
           if 0 < math.hypot(dx, dz) <= max_m]
    out.sort(key=lambda p: (round(math.hypot(*p), 3), p))
    return out


def pad_at(fields, building: dict, x: float, z: float) -> dict:
    f = T.field_for(fields, x, z, pad_m=max(building["pad"]["width_m"],
                                            building["pad"]["depth_m"]))
    if f is None:
        return {}
    return T.pad_bill(f, x, z, building["pad"]["width_m"],
                      building["pad"]["depth_m"],
                      yaw_deg=building["yaw_deg"], apron_m=0.0)


def clearing_area_for(plan: dict, site: str, unit_id: str):
    import area as clear_area
    doc = B.placements_doc(plan, site)
    place = next(p for p in doc["placements"] if p["id"] == unit_id)
    return clear_area.clearing_area(place, apron=B.APRON_M), place, doc


def building_of(plan: dict, site: str, unit_id: str) -> dict:
    """The candidate's geometry in ONE shape, town or outlier.

    A town building carries `x`, `z`, `footprint`, `yaw_deg`, `pad` and a
    `street`; an outlier carries `body_footprint` and `pad.yaw_deg` and has
    no street, and a treehouse member's x/z/yaw are DERIVED from the cluster
    rather than stored. Rather than branch at every use, the derived unit
    record supplies position and yaw and the site record supplies the rest.
    """
    if site in plan["towns"]:
        return next(b for b in plan["towns"][site]["buildings"]
                    if b["id"] == unit_id)
    rec = next(r for r in plan["outliers"] if r["id"] == site)
    unit = next(u for u in B.unit_records(plan, site) if u["id"] == unit_id)
    return {"id": unit_id, "x": unit["x"], "z": unit["z"],
            "yaw_deg": unit["yaw"],
            "footprint": [unit["foot_w"], unit["foot_d"]],
            "pad": rec["pad"], "street": None}


def patched_plan(plan: dict, site: str, unit_id: str, x: float, z: float,
                 pad: dict) -> dict:
    out = json.loads(json.dumps(plan))
    if site in out["towns"]:
        for b in out["towns"][site]["buildings"]:
            if b["id"] == unit_id:
                b["x"], b["z"] = round(x, 1), round(z, 1)
                b["pad"] = {**b["pad"], **pad, "x": x, "z": z}
        return out
    # OUTLIERS ARE OVERRIDDEN, NOT REWRITTEN -- see `build.unit_records`: a
    # cluster member's position is derived from the site centre, so writing
    # the site's x/z would move every member including ones already standing.
    for rec in out["outliers"]:
        if rec["id"] != site:
            continue
        base = next(r for r in plan["outliers"] if r["id"] == site)
        rec.setdefault("nudged", {})[unit_id] = {
            "x": round(x, 1), "z": round(z, 1),
            "pad": {**base["pad"], **pad, "x": x, "z": z},
            "move_m": round(math.hypot(
                x - building_of(plan, site, unit_id)["x"],
                z - building_of(plan, site, unit_id)["z"]), 2)}
    return out


def terrain_gate(plan: dict, site: str, unit_id: str, loc) -> dict:
    """The REAL terrain gate for a candidate, offline.

    `flatten.py` as a library -- its zone set, its patchscan, its sample loop
    -- then `delta_gate` over the written samples and `sample_verdict` under
    the non-destructive budget (reach + caller half-width + the derived 1 m
    terrain spread). Nothing is sent, and no compiler union is applied here:
    the union changes which samples this write OWNS, never where the pad is,
    and the gate asks whether the pad's own deltas move a POI's ground.
    """
    _ar, place, _doc = clearing_area_for(plan, site, unit_id)
    unit = next(u for u in B.unit_records(plan, site) if u["id"] == unit_id)
    stats, comps, op_y, _patches = B.flatten_pad(place, f"nudge_{unit_id}")
    gate = B.delta_gate(loc, comps, B.pad_lattice(unit))
    verdict = B.sample_verdict(loc, unit, comps, gate)
    return {"verdict": verdict["verdict"],
            "violations": verdict["violations"][:3],
            "delta_gate": {k: v for k, v in gate.items() if k != "method"},
            "earthwork": {"max_cut_m": stats["max_cut"],
                          "max_fill_m": stats["max_fill"],
                          "samples": stats["samples"],
                          "over_clamp": stats["over_clamp"]},
            "zones": [list(z) for z in sorted(comps)]}


def solve_one(plan: dict, site: str, unit_id: str, fields, loc,
              standing: list[dict], shortlist: int = 6) -> dict:
    """Ranked candidate offsets for one refused pad, offline filters only."""
    town = plan["towns"].get(site)
    building = building_of(plan, site, unit_id)
    street = street_of(town, building) if town else None
    base_off = signed_offset(street, building["x"], building["z"]) if street \
        else 0.0
    # THE BIOME GATE IS "DO NOT CHANGE BIOME", NOT "BE MEADOWS". Hard-coding
    # Meadows is right for the two towns and wrong for every outlier that was
    # deliberately sited elsewhere: MEASURED, tree-sth's planned pad is
    # BlackForest on all 256 samples, so a Meadows test rejects all 288
    # candidate cells and the pad reads as unmovable when nothing is wrong
    # with it. The planned pad's own dominant biome is the datum.
    want_biome = max(building["pad"]["biome_counts"],
                     key=building["pad"]["biome_counts"].get)
    rejected = {"street_side": 0, "street_window": 0, "pad_unsolvable": 0,
                "clamp": 0, "wet": 0, "biome": 0, "sibling_overlap": 0,
                "sibling_in_count_disc": 0, "piece_reach": 0}
    kept: list[dict] = []
    for dx, dz in candidate_offsets(MAX_NUDGE_M):
        x, z = building["x"] + dx, building["z"] + dz
        if street is not None:
            off = signed_offset(street, x, z)
            if base_off != 0.0 and (off < 0) != (base_off < 0):
                rejected["street_side"] += 1
                continue
            if not (abs(base_off) - STREET_NEARER_M <= abs(off)
                    <= abs(base_off) + STREET_FURTHER_M):
                rejected["street_window"] += 1
                continue
        pad = pad_at(fields, building, x, z)
        if not pad:
            rejected["pad_unsolvable"] += 1
            continue
        if not pad["clamp_ok"] or pad["over_clamp"]:
            rejected["clamp"] += 1
            continue
        if pad["wet_samples"]:
            rejected["wet"] += 1
            continue
        if max(pad["biome_counts"], key=pad["biome_counts"].get) != want_biome:
            rejected["biome"] += 1
            continue
        mine = (x, z, building["footprint"][0], building["footprint"][1],
                building["yaw_deg"])
        if any(rects_overlap(mine, s["rect"], SIBLING_MARGIN_M)
               for s in standing):
            rejected["sibling_overlap"] += 1
            continue
        cand_plan = patched_plan(plan, site, unit_id, x, z, pad)
        ar, _place, _doc = clearing_area_for(cand_plan, site, unit_id)
        count_r = ar.radius_m / math.sqrt(2)
        if any(rect_disc_overlap(s["rect"], ar.centre_x, ar.centre_z, count_r)
               for s in standing):
            rejected["sibling_in_count_disc"] += 1
            continue
        room = B.clear_radius_room(loc, ar.centre_x, ar.centre_z)
        if ar.radius_m > room["max_radius_m"]:
            rejected["piece_reach"] += 1
            continue
        kept.append({"dx": dx, "dz": dz, "move_m": round(math.hypot(dx, dz), 2),
                     "x": round(x, 1), "z": round(z, 1),
                     "target_y": pad["target_y"],
                     "street_offset_m": round(signed_offset(street, x, z), 2)
                     if street else None,
                     "planned_street_offset_m": round(base_off, 2),
                     "pad": pad,
                     "clear_radius_m": round(ar.radius_m, 2),
                     "clear_radius_room_m": room["max_radius_m"],
                     "count_radius_m": round(count_r, 2),
                     "clear_centre": [ar.centre_x, ar.centre_z]})
        if len(kept) >= shortlist:
            # The offline list is generated GENEROUSLY and the live census is
            # what actually decides, because the mod hamlet is invisible to
            # every offline check. MEASURED on `stenvik-cottage-2`: the first
            # six offline-legal cells are all inside the same mod farm
            # (`wood_floor_1x1` x18, `wood_beam` x8), so a shortlist of six
            # drops a pad that a wider search clears.
            break
    return {"unit": unit_id, "planned": [building["x"], building["z"]],
            "candidates": kept, "rejected": rejected,
            "searched_to_m": MAX_NUDGE_M}


def standing_rects(plan: dict, site: str, built: list[str]) -> list[dict]:
    """The footprints already on the ground at this site, at BUILD YAW.

    A treehouse hamlet is three bodies 12 m apart, so an outlier site needs
    this every bit as much as a town does -- nudging member 3 into member 1
    is the same defect as nudging a cottage into a hall.
    """
    units = B.unit_records(plan, site)
    return [{"id": u["id"],
             "rect": (u["x"], u["z"], u["foot_w"], u["foot_d"], u["yaw"])}
            for u in units if u["id"] in built]


def census(srv, cx: float, cz: float, radius_m: float) -> dict:
    """`objects_count id=*` over the exact cylinder, classified by the same
    `NATURAL_RE` the build gate uses. One implementation, one verdict."""
    total, per = srv.count("*", cx, cz, radius_m)
    natural = {p: n for p, n in per.items() if B.NATURAL_RE.match(p)}
    other = {p: n for p, n in per.items() if not B.NATURAL_RE.match(p)}
    return {"total": total, "clearable": natural, "not_clearable": other,
            "verdict": "clear" if not other else "VIOLATION",
            "probe": f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} "
                     f"max={radius_m:.2f}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["solve", "accept"])
    ap.add_argument("--site", default="stenvik")
    ap.add_argument("--units", default="")
    ap.add_argument("--plan-json", default=str(HERE / "plan.json"))
    ap.add_argument("--shortlist", type=int, default=120,
                    help="offline-legal cells collected per pad before the "
                         "live census starts choosing")
    ap.add_argument("--probes", type=int, default=40,
                    help="live censuses per pad; each is one round trip")
    # WHO THE LEDGER SAYS DID IT. `SiteFinish` solved Stenvik's five nudges
    # and yielded; the outlying sites are nudged by `OutlierBuild`. Crediting
    # one agent's measurements to another in the only log that survives the
    # session is the same defect `build.py --actor` exists to prevent.
    ap.add_argument("--actor", default="SiteFinish")
    a = ap.parse_args()

    plan_path = Path(a.plan_json)
    plan = json.loads(plan_path.read_text())
    records = ledger_records()
    built = built_units(records)
    units = [u for u in (a.units.split(",") if a.units
                         else refused_units(records)) if u]
    units = [u for u in units if u.startswith(a.site)]
    fields = T.load(plan["patch_file"])
    loc = clearance.load(B.LOCATIONS)
    standing = standing_rects(plan, a.site, built)

    report = {"site": a.site, "standing": [s["id"] for s in standing],
              "refused": units, "solved": {}, "dropped": {}}

    from live import LiveBuilder  # noqa: E402
    with LiveBuilder(actor=a.actor) as b:
        b.srv.probe()
        for unit_id in units:
            sol = solve_one(plan, a.site, unit_id, fields, loc, standing,
                            shortlist=a.shortlist)
            chosen = None
            tried = []

            # THE PLANNED POSITION IS TRIED FIRST, because zero is the
            # smallest nudge and one of the eight refusals does not reproduce:
            # `stenvik-beehive-1` was refused on `{'stubbe': 1}`, and `stubbe`
            # IS in the natural set this build gates on. Nudging a pad that
            # does not need nudging would move a building for nothing.
            ar0, _p0, _d0 = clearing_area_for(plan, a.site, unit_id)
            here = census(b.srv, ar0.centre_x, ar0.centre_z, ar0.radius_m)
            tried.append({"move_m": 0.0, "xz": [ar0.centre_x, ar0.centre_z],
                          "census": here, "terrain_gate": None,
                          "note": "the PLANNED position, re-measured"})
            if here["verdict"] == "clear":
                gate0 = terrain_gate(plan, a.site, unit_id, loc)
                tried[-1]["terrain_gate"] = gate0
                if gate0["verdict"] == "clear":
                    report["solved"][unit_id] = {
                        "move_m": 0.0, "x": None, "z": None,
                        "no_nudge_needed": True, "census": here,
                        "terrain_gate": gate0, "tried": 1,
                        "why": ("re-measured live: the planned cylinder holds "
                                "nothing outside the natural set, so the "
                                "earlier refusal does not reproduce")}
                    continue

            # Then the ranked nudges. The CENSUS COMES FIRST and the terrain
            # gate only runs on a cell the census cleared: the census is one
            # round trip of two lines, the gate is a patchscan plus a sample
            # loop, and MEASURED they cost about 2 s and 7 s. Cheap question
            # first, expensive question only where it can change the answer.
            for cand in sol["candidates"][:a.probes]:
                cx, cz = cand["clear_centre"]
                cen = census(b.srv, cx, cz, cand["clear_radius_m"])
                row = {"move_m": cand["move_m"],
                       "xz": [cand["x"], cand["z"]], "census": cen,
                       "terrain_gate": None}
                tried.append(row)
                if cen["verdict"] != "clear":
                    continue
                cand_plan = patched_plan(plan, a.site, unit_id, cand["x"],
                                         cand["z"], cand["pad"])
                gate = terrain_gate(cand_plan, a.site, unit_id, loc)
                row["terrain_gate"] = gate
                if gate["verdict"] == "clear":
                    chosen = {**cand, "census": cen, "terrain_gate": gate}
                    break
            if chosen is None:
                report["dropped"][unit_id] = {
                    "reason": (f"no candidate within {MAX_NUDGE_M:g} m passed "
                               f"every gate in {len(tried)} live probe(s)"),
                    "planned_census": here,
                    "offline_legal_cells": len(sol["candidates"]),
                    "offline_rejections": sol["rejected"],
                    "candidates_tried": tried}
                continue
            report["solved"][unit_id] = {k: v for k, v in chosen.items()
                                         if k != "pad"}
            report["solved"][unit_id]["pad_target_y"] = chosen["pad"]["target_y"]
            report["solved"][unit_id]["tried"] = len(tried)
            if a.op == "accept":
                plan = patched_plan(plan, a.site, unit_id, chosen["x"],
                                    chosen["z"], chosen["pad"])
                # The standing set grows as pads are accepted, so two nudged
                # pads cannot be given the same ground.
                acc = building_of(plan, a.site, unit_id)
                standing.append({
                    "id": unit_id,
                    "rect": (chosen["x"], chosen["z"],
                             acc["footprint"][0], acc["footprint"][1],
                             acc["yaw_deg"])})

    if a.op == "accept":
        plan_path.write_text(json.dumps(plan, indent=1))
        report["plan_written"] = str(plan_path)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
