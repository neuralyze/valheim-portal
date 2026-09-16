#!/usr/bin/env python3
"""Stand a settlement up in the live world, through the ledger and nothing else.

THE WRITE PATH, and why every step of it belongs to somebody else's module:
this file owns the SETTLEMENT -- which bodies, where, at what yaw, on what pad.
It owns none of the primitives:

    zone generation + clearing   tools/jumpstart/clearing/      (GroundTruth)
    the TCData terrain write     tools/jumpstart/terraform/     (GroundTruth)
    the blueprint spawn plan     tools/jumpstart/blueprints/    (shared)
    the wire, the log, the guards tools/jumpstart/ledger/       (BuildLedger)

So this is a DRIVER. It imports those as libraries to synthesise the exact wire
they would send, and then emits every one of those commands through
`ledger.live.LiveBuilder`, which appends and fsyncs the record BEFORE sending
and raises if the postcondition does not hold. Nothing here touches RCON
directly: an ad-hoc write is invisible to the ledger's one-compiler-per-zone
and `flatten: FORBIDDEN` guards, which would make them decoration.

A placements document is synthesised per settlement under `placements/` in THIS
directory, because `worlds/Ulfsland/*/placements.yaml` is not ours to write and
`clear.py` / `flatten.py` both accept `--placements` for exactly this.

ORDER, which is the proven one and is not negotiable:
    0 zones_generate   per pad -- MEASURED: paint alpha is NOT honoured in an
                       ungenerated zone; 1,681 cleared samples still grew
                       Bush01 x15, Beech1 x15 and eleven more prefabs, up to
                       7.68 m above the pad, as permanent ZDOs
    1 objects_remove   the vegetation, before the ground moves under it
    2 terrain_write    the pad, levelled to the footprint median
    3 spawn_plan       the body, bottom-up in Y so WearNTear support exists
    4 portal + sign    one per settlement, unique non-empty tag <= 10 chars

Usage:
    ./build.py placements                 # synthesise the documents only
    ./build.py plan   --site stenvik      # every command, nothing sent
    ./build.py apply  --site stenvik [--limit N] [--from N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "clearing"))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "blueprints"))
sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(REPO / "tools"))

import clearance  # noqa: E402
import terrain1m as T  # noqa: E402
import flatten as FL  # noqa: E402  terraform/flatten.py, driven as a LIBRARY
import place as PLACE  # noqa: E402  terraform/place.py, for its measured batching
import verify_placement as VP  # noqa: E402  blueprints/, for plan_rows / bottoming

PLACEMENTS = HERE / "placements"
SCRATCH = Path("/tmp/settle/build")
LOCATIONS = "/tmp/settle/loc3/f6fe167f4fcd.json"
# The dump `flatten.py`'s own location gate reads. It carries the per-type
# radii but not the measured ZNetView reach; ours carries both, and both are
# recorded so the two gates can be told apart in the log.
FLATTEN_LOCATIONS = "/tmp/settle/loc2/f6fe167f4fcd.json"

PAINT = "paved_cleared"
APRON_M = 0.0


# --- placements documents -------------------------------------------------

# Per-site cluster base angle, MEASURED by `Settlements` over a 5 deg sweep
# against all 12,301 location instances: the worst piece-reach room over the
# three members, at the stilt body's own 4.74 m half-diagonal.
#   tree-sth  105 deg -> min room 16.92 m   (0 deg put two members in Crypt2)
#   tree-near  65 deg -> min room 15.95 m   (0 deg: Crypt3 and StoneTowerRuins03)
#   tree-west  55 deg -> min room 30.96 m   (already clear at 0; this is best)
CLUSTER_BASE_YAW = {"tree-sth": 105.0, "tree-near": 65.0, "tree-west": 55.0}


def unit_records(plan: dict, site: str) -> list[dict]:
    """One build unit per body: the town's buildings, or an outlier's cluster."""
    if site in plan["towns"]:
        town = plan["towns"][site]
        out = []
        for b in town["buildings"]:
            out.append({
                "id": b["id"], "body": b["body"], "sha256": b["sha256"],
                "x": b["x"], "z": b["z"], "yaw": b["yaw_deg"],
                "pad_w": b["pad"]["width_m"], "pad_d": b["pad"]["depth_m"],
                "target_y": b["pad"]["target_y"], "ground_y": b["pad"]["target_y"],
                "pieces": b["pieces"], "role": b["role"], "patch": town["patch"],
            })
        return out
    for rec in plan["outliers"]:
        if rec["id"] != site:
            continue
        n = rec.get("cluster", 1)
        out = []
        for k in range(n):
            # A cluster spreads on the pad's own diagonal so the members do not
            # share a footprint; one member is the pad centre.
            #
            # THE BASE ANGLE IS PER SITE AND IT IS A MEASUREMENT. At base 0
            # four of the nine treehouse members refuse the piece-reach gate:
            # tree-sth-1 and -2 reach inside Crypt2's stand-off (MEASURED
            # znviewReachM 21.0 m against a much smaller declared radius),
            # tree-near-1 inside Crypt3's and tree-near-3 inside
            # StoneTowerRuins03's (11.47 m). Those sites were sited before
            # `clearance.py` gained the per-type measured reach, so the gate
            # got SHARPER under a finished plan -- which is the instrument
            # working, not a siting error. Settlements swept the base angle in
            # 5 deg steps against all 12,301 instances at the stilt body's own
            # 4.74 m half-diagonal; these are the angles at which all three
            # members clear, with the worst room over the three reported.
            # Rotating the cluster is the fix rather than shrinking the pad,
            # which would be the clipped-radius failure in a different costume.
            ang = math.radians(CLUSTER_BASE_YAW.get(rec["id"],
                                                    rec["pad"]["yaw_deg"])
                               + 120 * k)
            r = 0.0 if n == 1 else (rec["pad_m"] / 2 - 4.0)
            out.append({
                "id": rec["id"] if n == 1 else f"{rec['id']}-{k + 1}",
                "body": rec["body"], "sha256": rec["body_sha256"],
                "x": round(rec["x"] + r * math.cos(ang), 1),
                "z": round(rec["z"] + r * math.sin(ang), 1),
                "yaw": round(120 * k, 1),
                "pad_w": rec["pad"]["width_m"], "pad_d": rec["pad"]["depth_m"],
                "target_y": rec["pad"]["target_y"],
                "ground_y": rec["pad"]["target_y"],
                "pieces": rec["pieces"], "role": rec["kind"], "patch": rec["patch"],
            })
        return out
    raise ValueError(f"{site} is neither a town nor an outlier in plan.json")


def placements_doc(plan: dict, site: str) -> dict:
    """A placements document in the shape `clear.py` and `flatten.py` read.

    Only the fields those two actually consume are synthesised, and each is the
    number this package MEASURED rather than a restatement of a requirement:
    `solved.x/z` the pad centre, `footprint_orientation` the pad rectangle (it
    is the authority for `pad_extent`), `flatten_cost.target_y` the pad height,
    `rotation.yaw` the body's yaw so the clearing cylinder covers the rotated
    pad, and `requirement.footprint_m` for the diagnostics.
    """
    units = unit_records(plan, site)
    loc = clearance.load(LOCATIONS)
    places = []
    for u in units:
        near = loc.nearest(u["x"], u["z"])
        places.append({
            "id": u["id"],
            "blueprint": u["body"],
            "sha256": u["sha256"],
            "pieces": u["pieces"],
            "align": "floor-center",
            "rotation": {"yaw": u["yaw"]},
            "requirement": {
                "biome": "Meadows",
                "footprint_m": max(u["pad_w"], u["pad_d"]),
                "footprint_xz_m": [u["pad_w"], u["pad_d"]],
            },
            "solved": {
                "seed": "Pirate68",
                "x": u["x"], "z": u["z"], "y": u["ground_y"],
                "footprint_orientation": f"{u['pad_w']:g} m along x by {u['pad_d']:g} m along z",
                "flatten_cost": {"target_y": u["target_y"], "resolution_m": 1.0,
                                 "estimated": False},
                # `area.location_bound` reads this and REFUSES a clearing
                # cylinder that reaches it, so it is populated with the
                # MEASURED distance from our own per-type dump rather than
                # left absent -- an absent bound disarms their guard silently.
                "nearest_location_m": near["dist_m"],
                "nearest_location": near["prefab"],
                "nearest_location_standoff_m": near["standoff_m"],
            },
            "settlement": {"site": site, "role": u["role"], "patch": u["patch"]},
        })
    return {"world": "Ulfsland", "settlement": site, "placements": places}


def write_placements(plan: dict) -> list[Path]:
    PLACEMENTS.mkdir(parents=True, exist_ok=True)
    out = []
    for site in list(plan["towns"]) + [r["id"] for r in plan["outliers"]]:
        doc = placements_doc(plan, site)
        p = PLACEMENTS / f"{site}.yaml"
        p.write_text(yaml.safe_dump(doc, sort_keys=False, width=10 ** 9))
        out.append(p)
    return out


# --- the wire -------------------------------------------------------------

def clearing_ops(doc: dict, unit_id: str) -> list[dict]:
    """`zones_generate` + `objects_remove` for one pad, from clearing's own
    area derivation so the radius, the margin and the location bound are the
    ones that module decided rather than a re-implementation here."""
    import area as clear_area
    import clear as clearing

    place = next(p for p in doc["placements"] if p["id"] == unit_id)
    ar = clear_area.clearing_area(place, apron=APRON_M)
    cmds = clearing.commands(ar, clearing.DEFAULT_KEEP)
    gen, remove = cmds[0], cmds[1]
    zones = zone_list(ar.centre_x, ar.centre_z, ar.radius_m)
    return [
        {"op": "zones_generate",
         "params": {"pos": [ar.centre_x, ar.centre_z],
                    "max_m": clearing.generate_max(ar),
                    "zones": [list(z) for z in zones],
                    "role": "site_pad", "site_id": unit_id},
         "wire": [gen],
         "expect": {"zone_ctrl": len(zones)}},
        {"op": "objects_clear",
         "params": {"centre": [ar.centre_x, ar.centre_z],
                    "radius_m": round(ar.radius_m, 2), "ids": "*",
                    "ignore": list(clearing.DEFAULT_KEEP),
                    "role": "clearing", "site_id": unit_id},
         "wire": [remove],
         "expect": {"objects_count": {"ids": "*", "ignore": "_*",
                                      "pos": [ar.centre_x, ar.centre_z],
                                      "max": round(ar.radius_m / math.sqrt(2), 2),
                                      "total": 0, "tolerance": 0}}},
    ]


# WHAT MAY BE DELETED, and this list is a MEASUREMENT of this seed rather than
# a taxonomy: every prefab family `objects_count id=*` reported inside the 33
# settlement cylinders after generation. Vegetation, pickables, rocks and
# deadfall. A prefab that does NOT match is not assumed harmless -- it stops
# the build, because the one unrepairable damage class this project has
# recorded is deleting a location's pieces, and a mod POI's pieces are
# ordinary building prefabs (MEASURED at (444.2, 911.5): woodwall x10,
# wood_floor x3, wood_beam_45 x8, a Beehive and a Music_MeadowsVillageFarm;
# at (533.8, 910.6): woodwall x22, wood_roof x7 and a TreasureChest_meadows).
NATURAL_RE = re.compile(
    r"^(?:beech|birch|fir|pine|oak|swamptree|yggashoot|bush|"
    r"raspberrybush|blueberrybush|cloudberrybush|"
    r"rock|minerock|stubbe|shrub|vines|glowingmushroom|"
    r"pickable_|bh_pickable_|.*_log|.*_oldlog|greydwarf_root|"
    r"marker|spawner_)", re.IGNORECASE)


def cylinder_contents(srv, ar) -> dict:
    """WHAT IS ACTUALLY IN THE CYLINDER ABOUT TO BE EMPTIED.

    This is a different question from every stand-off check in this pipeline,
    and it is the only one that measures the hazard directly. The dump answers
    "how close is a location"; the live `LocationProxy` sweep answers "is there
    a marker the dump cannot see"; and for an UNIDENTIFIED marker the sweep
    can only assume a worst case -- MEASURED tonight, 58 m = a 32 m worst-case
    radius + 11 m INFERRED overshoot + a 15 m taste margin -- which refused
    Stenvik's hall on a marker 23.9 m away whose pieces are nowhere near the
    pad.

    So instead of arguing about radii: ask the world what is inside the
    cylinder. `objects_count id=*` lists EVERY prefab present with its count,
    the answer is one line per prefab, and `Server.count` permits `id=*` only
    inside 40 m, which every settlement cylinder is. The verdict is then a
    fact about the objects that would be deleted rather than an inference from
    a distance.
    """
    got, per = srv.count("*", ar.centre_x, ar.centre_z, ar.radius_m)
    natural = {p: n for p, n in per.items() if NATURAL_RE.match(p)}
    other = {p: n for p, n in per.items() if not NATURAL_RE.match(p)}
    return {
        "total": got, "per_prefab": per,
        "clearable": natural, "not_clearable": other,
        "verdict": "clear" if not other else "VIOLATION",
        "probe": f"objects_count id=* ignore=_* "
                 f"pos={ar.centre_x:.2f},{ar.centre_z:.2f} "
                 f"max={ar.radius_m:.2f}",
        "method": ("MEASURED: the live per-prefab contents of the exact "
                   "cylinder `objects_remove` will empty. Nothing here is "
                   "inferred from a radius -- a prefab that would be deleted "
                   "is either in the measured natural set or it stops the "
                   "build."),
        "tool": "tools/jumpstart/settlements/build.py::cylinder_contents",
    }


def scoped_clear(ar, unit_id: str, contents: dict) -> dict:
    """`objects_remove` scoped to the prefabs MEASURED in the cylinder.

    `id=*` would also delete anything that arrives between the measurement and
    the removal, and it is the command that deleted POI content in the
    pre-wipe world. Naming the prefabs makes the safety a property of the
    COMMAND rather than of a count taken a minute earlier. `ignore=_*` is kept
    so `_ZoneCtrl` and `_TerrainCompiler` survive: MEASURED, an empty
    `ignore=` is an empty id, not "no filter", and answers
    `Error: Entity id  not recognized.`
    """
    ids = sorted(contents["clearable"])
    wire = (f"objects_remove id={','.join(ids)} ignore=_* "
            f"pos={ar.centre_x:g},{ar.centre_z:g} max={ar.radius_m:.2f}")
    return {
        "op": "objects_clear",
        "params": {"centre": [ar.centre_x, ar.centre_z],
                   "radius_m": round(ar.radius_m, 2), "ids": ids,
                   "ignore": ["_*"], "role": "clearing", "site_id": unit_id},
        "wire": [wire],
        "expect": {"objects_count": {
            "ids": "*", "ignore": "_*",
            "pos": [ar.centre_x, ar.centre_z],
            "max": round(ar.radius_m / math.sqrt(2), 2),
            "total": 0, "tolerance": 0}},
    }



def union_prior(b, comps: dict, op_y: dict) -> dict:
    """Merge every EARLIER compiler claim on our zones into our own blobs.

    THIS IS NOT DEFENSIVE, IT IS ARITHMETIC THE TOWN REQUIRES. A zone holds
    exactly ONE `_TerrainCompiler` -- `Heightmap::GetAndCreateTerrainCompiler`
    returns the first it finds -- and the write path deletes before it spawns.
    Stenvik's 18 pads span 8 zones, so the second pad in a zone would ERASE
    the first one's levelling and leave a finished building standing on
    generated ground. The ledger's clobber guard refuses that, and the fix is
    to read the earlier blob back out of the blob store and union the samples:
    ours win where we wrote, theirs survive everywhere else.

    Whether the earlier record ever reached the world is measured separately
    (`compiler_present`), because a record is appended BEFORE it is sent and
    an append that failed to send leaves a claim with no compiler behind it.
    Unioning is right either way -- the samples describe a pad somebody
    intended -- but the log should say which case it was.
    """
    import tcdata
    prior: dict[tuple[int, int], list[dict]] = {}
    for line, rec in enumerate(b.led.records()):
        if rec["op"] != "terrain_write":
            continue
        for e in rec["params"]["entries"]:
            z = (int(e["zone"][0]), int(e["zone"][1]))
            if z in comps:
                prior.setdefault(z, []).append(
                    {"line": line, "seq": rec["seq"], "actor": rec["actor"],
                     "blob_sha256": e["blob_sha256"],
                     "data_entry": e["data_entry"]})
    merged: dict[tuple[int, int], dict] = {}
    for z, claims in prior.items():
        comp = comps[z]
        taken = 0
        for claim in claims:
            old = tcdata.parse(b.led.read_blob(claim["blob_sha256"]))
            for i, (level, smooth) in old["heights"].items():
                if not comp.modified_height[i]:
                    comp.modified_height[i] = True
                    comp.level_delta[i] = level
                    comp.smooth_delta[i] = smooth
                    taken += 1
            for i, colour in old.get("paints", {}).items():
                if not comp.modified_paint[i]:
                    comp.modified_paint[i] = True
                    comp.paint[i] = colour
        merged[z] = {
            "merged_from": [c["seq"] for c in claims],
            "merged_from_lines": [c["line"] for c in claims],
            "merge_policy": "union",
            "samples_inherited": taken,
            "why": ("a zone holds one compiler and this op deletes before it "
                    "spawns, so the earlier pad's samples are re-read from "
                    "the ledger's blob store and kept; ours win only where we "
                    "wrote. Last-writer-wins per SAMPLE, never per zone."),
        }
    return merged



def zone_compiler(srv, zx: int, zz: int) -> int:
    """How many `_TerrainCompiler` ZDOs actually stand in this zone.

    Counted at the zone centre inside 31 m -- under half a zone, so a
    neighbour cannot leak in. Two means one of them is dead weight and the
    terrain on screen is not the terrain written; zero against a ledger claim
    means that claim was appended and never sent.
    """
    import tcdata
    cx, cz = tcdata.zone_centre(zx, zz)
    got, _ = srv.count("_TerrainCompiler", cx, cz, 31.0, ignore="")
    return got

def zone_list(cx: float, cz: float, radius_m: float) -> list[tuple[int, int]]:
    import tcdata
    zs = set()
    r = radius_m
    for dx in (-r, 0.0, r):
        for dz in (-r, 0.0, r):
            zs.add(tcdata.zone_of(cx + dx, cz + dz))
    xs = sorted({z[0] for z in zs})
    ys = sorted({z[1] for z in zs})
    return [(x, y) for x in range(xs[0], xs[-1] + 1) for y in range(ys[0], ys[-1] + 1)]


def spawn_plan_file(unit: dict, pad_y: float, out_dir: Path) -> tuple[Path, dict]:
    """Run `to_rcon_plan.py --align floor-center` for one body."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    libmat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(libmat)
    body = libmat.locate_by_filename(unit["body"])
    if body is None:
        raise FileNotFoundError(f"no body resolves for {unit['body']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"{unit['id']}.plan"
    cmd = [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"), str(body),
           "--at", f"{unit['x']}", f"{pad_y}", f"{unit['z']}",
           "--rotate", f"{unit['yaw']}", "--align", "floor-center",
           "--out", str(plan_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not plan_path.exists():
        raise SystemExit(f"to_rcon_plan failed for {unit['id']}:\n"
                         f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    lines = [l for l in plan_path.read_text().splitlines() if l.strip()]
    prefabs: dict[str, int] = {}
    for l in lines:
        parts = l.split()
        if len(parts) >= 2 and parts[0] == "spawn_object":
            prefabs[parts[1]] = prefabs.get(parts[1], 0) + 1
    return plan_path, {"command_count": len(lines), "prefabs": prefabs,
                       "stdout": proc.stdout[-4000:]}


def location_check(loc: clearance.Locations, x: float, z: float,
                   half_diag_m: float) -> dict:
    v = loc.violations(x, z, half_diag_m)
    near = loc.nearest(x, z)
    return {
        "dump": LOCATIONS,
        "method": ("MEASURED per location TYPE from LocScan.cs: "
                   "max(exteriorRadius, interiorRadius, znviewReachM) plus a 12 m "
                   "margin and a 90 m extra for a location the generator treats as "
                   "load bearing (prioritized or centerFirst or quantity <= 5). "
                   "Largest declared radius in the world is 32 m; largest measured "
                   "ZNetView reach is 35.77 m (TrollCave02). MOD-FREE dump: it "
                   "cannot see More_World_Locations, so a live marker check after "
                   "zones_generate is still required."),
        "tool": "tools/jumpstart/settlements/clearance.py",
        "nearest": {"name": near["name"], "dist_m": near["dist_m"],
                    "clearance_m": near["standoff_m"]},
        "standoff_m": near["standoff_m"],
        "verdict": "clear" if not v else "violates",
        "violations": v,
    }


# --- step 2: the terrain write -------------------------------------------
#
# `flatten.py` is driven as a LIBRARY and never as `flatten.py apply`, which
# sends its own `deleteObjects` + `spawn_object _TerrainCompiler` straight to
# RCON. An unlogged compiler write is invisible to the ledger's
# one-compiler-per-zone clobber guard and to `flatten: "FORBIDDEN"`, and those
# two guards exist for the single most destructive operation in this build.
# So: its zone set, its patchscan, its sample loop, its self-check, its two
# preconditions -- and OUR emit.

SEED = "Pirate68"
# `place.py`'s measured budget. The binding direction is the REPLY,
# `Command '<joined>' executed.`, so the joined command is what is capped.
BATCH_BYTES = 3600
ROLE_BODY = {"town": "town_body", "castle": "castle", "watchtower": "watchtower",
             "lighthouse": "lighthouse", "treehouse": "treehouse"}
PORTAL_PREFAB = "portal_wood"
HUB_PRESET = "deepnorth-sandbox"
HUB_ID = "sandbox-portal-hub"
HUB_SPOTS = Path("/tmp/settle/hub_spots.json")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pad_lattice(unit: dict, step: float = 1.0) -> list[tuple[float, float]]:
    """The pad rectangle at 1 m: the samples a terrain write actually touches.

    Sample form rather than a disc, because `clearance.verdict_for_samples`
    compares the MINIMUM distance from written ground to each instance, and a
    26 m pad's 18.5 m half-diagonal would otherwise refuse ground the write
    never reaches.
    """
    hw, hd = unit["pad_w"] / 2 + APRON_M, unit["pad_d"] / 2 + APRON_M
    nx, nz = int(2 * hw / step), int(2 * hd / step)
    return [(unit["x"] - hw + i * step, unit["z"] - hd + j * step)
            for i in range(nx + 1) for j in range(nz + 1)]


def sample_verdict(loc: clearance.Locations, unit: dict, comps: dict | None = None,
                   gate: dict | None = None) -> dict:
    """`terrain_write.location_check`, in the canonical instrument's own words.

    `verdict_for_samples` already returns `method`, `tool` and the mod-free
    caveat IN THE RESULT, which is exactly what the schema demands, so the
    result is handed to the log unmodified apart from the dump path and the
    `clearance_m` alias the schema's reader expects.

    WITH `comps` this is the TERRAIN WRITE's own check and is asked under the
    non-destructive budget with the mandatory delta gate: the write moves
    ground and deletes nothing, so `MARGIN_M` and `PROTECTED_EXTRA_M` -- both
    taste, both calibrated for deletion -- are budgeting against a hazard this
    op does not have, and the hazard it DOES have (a location piece left
    floating because its ground moved) is measured directly by `delta_gate`.
    Main's ruling, and `clearance.verdict_for_samples` refuses the loosened
    budget outright if the delta gate is missing.

    WITHOUT `comps` it is the SITING question under the destructive budget,
    which is what the clearing cylinder's own gate (`clear_radius_room`) and
    the site solver answer against.
    """
    samples = pad_lattice(unit)
    if comps is None:
        v = loc.verdict_for_samples(samples)
    else:
        v = loc.verdict_for_samples(samples, destructive=False,
                                    delta_gate=gate or delta_gate(loc, comps,
                                                                  samples))
    near = dict(v.get("nearest") or {})
    near["clearance_m"] = near.get("standoff_m")
    v["nearest"] = near
    v["dump"] = LOCATIONS
    return v


def clear_radius_room(loc: clearance.Locations, cx: float, cz: float) -> dict:
    """How wide an `objects_remove` cylinder about (cx, cz) may be.

    A DIFFERENT QUESTION from the pad stand-off, and the distinction is
    Crossings' finding rather than mine: the pad gate asks whether WRITTEN
    GROUND enters a location's stand-off, while this asks whether the CLEARING
    CYLINDER can reach a location's PIECES. `objects_remove id=* ignore=_*` is
    the only irreversible operation in this build, and MEASURED by Crossings on
    `dock-stationhub`, a Runestone_Meadows marker 19.3 m away with an 8.0 m
    measured reach puts its pieces 11.3 m from the centre -- inside a 13.1 m
    clear radius that every other gate passed.

    The bound is a MINIMUM OVER ALL INSTANCES of (distance - that instance's
    own measured reach), not the nearest marker: a nearer marker with a small
    reach is not the constraint and a further one with a 35.77 m reach is.
    One metre of slack, and it REFUSES rather than clipping -- a silently
    clipped radius leaves standing forest against a wall, which is a decision
    for the caller and not for the arithmetic.
    """
    import numpy as np
    d = np.hypot(loc.xz[:, 0] - cx, loc.xz[:, 1] - cz)
    room = d - loc.reach
    i = int(np.argmin(room))
    return {"max_radius_m": round(float(room[i]) - 1.0, 2),
            "binding": {"name": loc.names[i], "prefab": loc.prefabs[i],
                        "dist_m": round(float(d[i]), 2),
                        "reach_m": round(float(loc.reach[i]), 2)},
            "method": ("MEASURED: min over all instances of (distance - "
                       "max(exteriorRadius, interiorRadius, znviewReachM)) "
                       "less 1 m of slack. The clearing cylinder must not "
                       "reach a location's PIECES, which is not the question "
                       "the pad stand-off asks."),
            "tool": "tools/jumpstart/settlements/build.py::clear_radius_room"}


def applied_delta(comps: dict, px: float, pz: float) -> float:
    """The height change this write applies at an arbitrary point.

    Bilinear over the four TerrainComp samples around the point, with
    UNWRITTEN samples contributing zero -- which is how `Heightmap` renders
    ground and is also where `clearance.TERRAIN_SPREAD_M` comes from:
    `ApplyToHeightmap` applies each sample's delta to that sample only, so the
    surface tilts out to the next unwritten sample and no further.
    """
    import tcdata
    total = 0.0
    for (zx, zz), comp in comps.items():
        cx, cz = tcdata.zone_centre(zx, zz)
        fx = (px - cx) / tcdata.SCALE + tcdata.PITCH // 2
        fz = (pz - cz) / tcdata.SCALE + tcdata.PITCH // 2
        x0, z0 = math.floor(fx), math.floor(fz)
        if not (0 <= x0 < tcdata.PITCH - 1 and 0 <= z0 < tcdata.PITCH - 1):
            continue
        tx, tz = fx - x0, fz - z0
        acc = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            i = (z0 + dz) * tcdata.PITCH + (x0 + dx)
            if comp.modified_height[i]:
                acc += w * comp.level_delta[i]
        total = acc if abs(acc) > abs(total) else total
    return total


def delta_gate(loc: clearance.Locations, comps: dict,
               samples: list[tuple[float, float]]) -> dict:
    """Does this write MOVE THE GROUND under any location's pieces?

    The hazard of a non-destructive write is not deletion, it is a location
    piece left FLOATING or BURIED because the ground under it moved -- the
    defect the operator condemned a world for. Distance does not measure that;
    the delta does. So every instance within 60 m of the written set is probed
    on a 1 m lattice over ITS OWN measured reach disc, because a location's
    pieces are spread over that reach and the marker can be metres from the
    piece that would float.

    `clearance.verdict_for_samples(destructive=False)` REFUSES without this,
    which is the point: the loosened distance budget was granted against this
    measurement.
    """
    probes = 0
    worst = 0.0
    violations = []
    instances = loc.instances_near(samples, 60.0)
    for inst in instances:
        ix, iz = inst["xz"]
        reach = max(1.0, inst["reach_m"])
        local = 0.0
        r = int(math.ceil(reach))
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if math.hypot(dx, dz) > reach:
                    continue
                probes += 1
                d = applied_delta(comps, ix + dx, iz + dz)
                if abs(d) > abs(local):
                    local = d
        if abs(local) > abs(worst):
            worst = local
        if abs(local) > clearance.DELTA_TOL_M:
            violations.append({
                "name": inst["name"], "prefab": inst["prefab"],
                "min_dist_m": inst["min_dist_m"], "reach_m": inst["reach_m"],
                "worst_delta_m": round(local, 3),
                "tolerance_m": clearance.DELTA_TOL_M,
                "reason": ("this write moves the ground under that location's "
                           "reach disc, which leaves its pieces floating or "
                           "buried")})
    return {
        "verdict": "clear" if not violations else "VIOLATION",
        "instances_probed": len(instances), "probe_points": probes,
        "worst_applied_delta_m": round(worst, 3),
        "tolerance_m": clearance.DELTA_TOL_M,
        "violations": violations,
        "method": ("MEASURED: for every instance within 60 m of the written "
                   "samples, the APPLIED height delta is evaluated on a 1 m "
                   "lattice over that instance's OWN measured reach disc by "
                   "bilinear blend of the four TerrainComp samples around "
                   "each probe, unwritten samples contributing zero -- which "
                   "is how Heightmap renders ground. Probing the DISC rather "
                   "than the marker matters: a location's pieces are spread "
                   "over its reach and the marker can be metres from the "
                   "piece that would float."),
        "tool": "tools/jumpstart/settlements/build.py::delta_gate",
    }


def zone_probe(srv, zx: int, zz: int) -> dict:
    """MEASURE that a zone is generated, and keep the command that measured it.

    `PlaceZoneCtrl` puts exactly one `_ZoneCtrl` at the zone CENTRE and
    `objects_count`'s filter is an XZ cylinder, so max=1 is exact and the
    answer is two lines. `zone_generated_before: True` without this command
    behind it is an assumption about what the pipeline did first.
    """
    import tcdata
    cx, cz = tcdata.zone_centre(zx, zz)
    cmd = f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1"
    got, _ = srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
    return {"cmd": cmd, "count": got, "centre": [cx, cz]}


def flatten_pad(place: dict, name: str) -> tuple[dict, dict, dict, dict]:
    """flatten.py's own pad stamp, for one placement. Nothing is sent."""
    solved = place["solved"]
    w, d = FL.pad_extent(place)
    zones = FL.zones_for(float(solved["x"]), float(solved["z"]),
                         w + 2 * APRON_M, d + 2 * APRON_M)
    patches = FL.run_patchscan(sorted(set(zones)), SEED,
                               FL.SCRATCH / f"patch_{name}.bin")
    comps: dict[tuple[int, int], object] = {}
    op_y: dict[tuple[int, int], float] = {}
    stats = FL.build(place, patches, zones, PAINT, APRON_M, comps, op_y)
    return stats, comps, op_y, patches


def generated_sha(patch) -> str:
    """The digest of the GENERATED heights this write was computed against.

    It is what detects a worldgen change: the deltas are only correct relative
    to these numbers, so a replay against different generated ground must stop
    rather than write a pad levelled to the wrong height.
    """
    return sha(struct.pack(f"<{patch.n * patch.n}f", *patch.heights))


def terrain_entries(b, unit: dict, comps: dict, op_y: dict, patches: dict,
                    probes: dict, merged: dict | None = None
                    ) -> tuple[list[dict], list[str], list[str]]:
    """One schema `entries[]` record per zone, plus the wire and the blob list.

    `write_entries` is flatten's, so the entry NAME and the bytes are its; the
    blob is stored in the ledger and the data-entry file is regenerated from
    that blob by `replay._materialise_data_entries`, so the world gets the
    bytes the log holds rather than whatever is left in the watched directory.
    """
    _path, entries = FL.write_entries(unit["id"], comps, op_y)
    out: list[dict] = []
    blobs: list[str] = []
    for e in entries:
        zx, zz = e["zone"]
        blob = comps[(zx, zz)].blob(op_y.get((zx, zz), 0.0))
        digest = b.blob(blob, note=f"TCData {e['entry']} zone {zx},{zz}")
        blobs.append(digest)
        sc = e["selfcheck"]
        out.append({
            "zone": [int(zx), int(zz)],
            "centre": [float(c) for c in e["centre"]],
            "data_entry": e["entry"],
            "blob_sha256": digest,
            "generated_heights_sha256": generated_sha(patches[f"z_{zx}_{zz}"]),
            "zone_generated_before": probes[(zx, zz)]["count"] >= 1,
            "zone_generated_probe": probes[(zx, zz)]["cmd"],
            "op_record": {
                "operations": int(sc["operations"]),
                "last_op_point": [round(float(v), 3) for v in sc["last_op_point"]],
                "last_op_radius": round(float(sc["last_op_radius"]), 3),
            },
            "samples": e["counts"]["height_modified"],
        })
        if merged and (zx, zz) in merged:
            m = merged[(zx, zz)]
            out[-1]["merged_from"] = m["merged_from"]
            out[-1]["merge_policy"] = m["merge_policy"]
    wire = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
            f"-prefab _TerrainCompiler -force" for e in entries]
    wire += [f"spawn_object _TerrainCompiler "
             f"from={e['centre'][0]:g},{e['centre'][1]:g},0 data={e['entry']}"
             for e in entries]
    return out, wire, blobs


def terrain_record(unit: dict, site: str, entries: list[dict], wire: list[str],
                   blobs: list[str], stats: dict, verdict: dict) -> dict:
    return {
        "op": "terrain_write",
        "params": {
            "name": unit["id"], "paint": PAINT,
            "datum": (f"pad target_y {unit['target_y']} m from plan.json, "
                      f"deltas against patchscan generated heights at 1 m "
                      f"(seed {SEED})"),
            "entries": [{k: v for k, v in e.items() if k != "samples"}
                        for e in entries],
            "location_check": verdict,
            "target_y": unit["target_y"], "apron_m": APRON_M,
            "pad_w": unit["pad_w"], "pad_d": unit["pad_d"],
            "max_cut_m": stats["max_cut"], "max_fill_m": stats["max_fill"],
            "over_clamp": stats["over_clamp"],
            "role": "site_pad", "site_id": unit["id"],
        },
        "wire": wire,
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": ["_TerrainCompiler"], "blobs": blobs},
        "expect": {"terrain_compiler": len(entries)},
        "meta": {"settlement": site, "unit": unit["id"],
                 "samples_written": sum(e["samples"] for e in entries),
                 "zone_checks": stats["checks"],
                 "why": ("one compiler per zone, deleted before spawn so the "
                         "op is idempotent; the pad is levelled per BUILDING "
                         "because MEASURED no 176 m district on this seed can "
                         "be written inside TerrainComp's +/-8 m clamp")},
    }


# --- step 3: the body ------------------------------------------------------

def check_radius(unit: dict, units: list[dict], clear_radius_m: float,
                 rows: list[tuple]) -> float:
    """The radius a prefab count can be EXACT inside.

    Two things bound it, and both are measurements rather than preferences:
    a sibling building's identical prefabs must not be able to answer for
    this one (so it stays inside the nearest neighbour's own reach), and
    uncleared vegetation must not be able to either (so it stays inside the
    radius `objects_clear` emptied). A wider radius with a tolerance would
    be a check that passes for the wrong reason.
    """
    reach = max((math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows),
                default=1.0)
    safe = [math.hypot(o["x"] - unit["x"], o["z"] - unit["z"])
            - math.hypot(o["pad_w"], o["pad_d"]) / 2 - 0.5
            for o in units if o["id"] != unit["id"]]
    return round(max(1.0, min([reach + 0.5, clear_radius_m - 0.1] + safe)), 2)


def spawn_plan_record(b, unit: dict, site: str, role: str, plan_path: Path,
                      info: dict, units: list[dict],
                      clear_radius_m: float) -> dict:
    rows = VP.plan_rows(plan_path)
    text = plan_path.read_text("utf-8")
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.startswith("#")]
    digest = b.blob(text.encode(), note=f"spawn plan {unit['id']}")
    groups = PLACE.batches(lines, PLACE.batch_budget(BATCH_BYTES))
    radius = check_radius(unit, units, clear_radius_m, rows)
    inside: dict[str, int] = {}
    for prefab, x, _y, z, _yaw in rows:
        if math.hypot(x - unit["x"], z - unit["z"]) <= radius:
            inside[prefab] = inside.get(prefab, 0) + 1
    dominant = max(inside.items(), key=lambda kv: kv[1])[0] if inside else None
    reach = max((math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows),
                default=1.0)
    expect: dict = {"objects_count": {
        "ids": "*", "ignore": "_*",
        "pos": [round(unit["x"], 2), round(unit["z"], 2)],
        "max": radius, "total": sum(inside.values()), "tolerance": 0}}
    if dominant:
        expect["prefab_count"] = [{
            "prefab": dominant,
            "pos": [round(unit["x"], 2), round(unit["z"], 2)],
            "max": radius, "count": inside[dominant], "tolerance": 0}]
    return {
        "op": "spawn_plan",
        "params": {
            "plan_sha256": digest,
            "plan_ref": str(plan_path),
            "anchor": {"x": unit["x"], "y": unit["target_y"], "z": unit["z"],
                       "yaw": unit["yaw"]},
            "command_count": len(lines), "prefabs": info["prefabs"],
            "body": {"filename": unit["body"], "sha256": unit["sha256"]},
            "align": "floor-center", "reach_m": round(reach, 2),
            "pad_height": unit["target_y"], "batch_bytes": BATCH_BYTES,
            "datum": f"pad top at target_y {unit['target_y']} m",
            "role": role, "site_id": unit["id"],
        },
        "wire": [";".join(g) for g in groups],
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": sorted(info["prefabs"]), "blobs": [digest]},
        "expect": expect,
        "meta": {"settlement": site, "batches": len(groups),
                 "count_radius_m": radius,
                 "pieces_inside_count_radius": sum(inside.values()),
                 "why": ("bottom-up in Y from to_rcon_plan so WearNTear "
                         "support exists when each piece lands; batched "
                         "because MEASURED round trips are the throughput "
                         "floor, and the count radius is bounded by the "
                         "nearest sibling pad so the postcondition cannot "
                         "pass on a neighbour's pieces")},
    }


# --- step 4: the portals, BOTH ends ---------------------------------------


def _waypoints():
    """WayFinding's emitter, loaded by PATH rather than by package import.

    `tools/jumpstart` is not a package on this checkout, and `network/
    waypoints.py` does `from . import DATA, JUMPSTART`, so it has to be given
    a package context explicitly. Loaded once and cached on the function, so
    the guidepost geometry and the sign validator are ONE implementation --
    the whole reason for calling it rather than rewriting it.
    """
    import importlib.util
    import types
    if getattr(_waypoints, "mod", None) is not None:
        return _waypoints.mod
    pkg = types.ModuleType("jsnet")
    pkg.__path__ = [str(JUMPSTART / "network")]
    sys.modules.setdefault("jsnet", pkg)
    init = importlib.util.spec_from_file_location(
        "jsnet.__init__", JUMPSTART / "network/__init__.py")
    base = importlib.util.module_from_spec(init)
    init.loader.exec_module(base)
    for name in ("DATA", "JUMPSTART", "HERE"):
        if hasattr(base, name):
            setattr(pkg, name, getattr(base, name))
    spec = importlib.util.spec_from_file_location(
        "jsnet.waypoints", JUMPSTART / "network/waypoints.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jsnet.waypoints"] = mod
    spec.loader.exec_module(mod)
    _waypoints.mod = mod
    return mod


def site_tag(plan: dict, site: str) -> str:
    if site in plan["towns"]:
        return plan["towns"][site]["tag"]
    return next(r["tag"] for r in plan["outliers"] if r["id"] == site)


def hub_placement() -> dict:
    doc = yaml.safe_load((JUMPSTART / "worlds/Ulfsland" / HUB_PRESET /
                          "placements.yaml").read_text())
    return next(p for p in doc["placements"] if p["id"] == HUB_ID)


def body_of(place_or_unit: dict, at: tuple[float, float, float], yaw: float):
    """A `fixtures.PlacedBody` for a body standing where it stands."""
    import importlib.util
    import fixtures
    from to_rcon_plan import read_objects
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    libmat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(libmat)
    name = place_or_unit.get("blueprint") or place_or_unit["body"]
    path = libmat.locate_by_filename(name)
    if path is None:
        raise FileNotFoundError(f"no body resolves for {name}")
    return fixtures, read_objects(path), fixtures.place_body(
        read_objects(path), at, yaw_deg=yaw)


def live_positions(srv, prefab: str, x: float, y: float, z: float,
                   radius: float) -> list[tuple[float, float, float]]:
    """Where a prefab already stands. Prefab-scoped AND radius-bounded:
    `findObjects` refuses without both, and an unbounded listing is the
    question whose ANSWER is long -- the one that has wedged this server."""
    import locations as LOCM
    reply = srv.command(f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} "
                        f"{z:.2f} {radius:.2f} -detailed")
    return [(px, py, pz) for pre, px, py, pz in
            [(m[0], float(m[1]), float(m[2]), float(m[3]))
             for m in LOCM.POSITION_RE.findall(reply)] if pre == prefab]


def hub_spots(srv, tags: list[str]) -> dict[str, dict]:
    """One legal hub-end position per tag, in the portal hall, solved against
    `fixtures` rather than declared.

    Cached: the search rasterises a 1,043-piece body and must give the SAME
    answer for every site, because a second solve with a different `taken`
    list would move the ends already standing.
    """
    if HUB_SPOTS.exists():
        cached = json.loads(HUB_SPOTS.read_text())
        if set(tags) <= set(cached):
            return cached
    place = hub_placement()
    solved = place["solved"]
    hx, hz = float(solved["x"]), float(solved["z"])
    pad_y = float(solved["flatten_cost"]["target_y"])
    yaw = float(place["rotation"]["yaw"])
    w, d = FL.pad_extent(place)
    fixtures, _objs, body = body_of(place, (hx, pad_y, hz), yaw)
    index = body.index()
    mask = body.interior()
    taken = [fixtures.fixture_box(PORTAL_PREFAB, px, py, pz, yaw)
             for px, py, pz in live_positions(srv, PORTAL_PREFAB, hx, pad_y,
                                              hz, min(30.0, max(w, d)))]
    standing = len(taken)
    out: dict[str, dict] = {}
    for tag in tags:
        spot = fixtures.free_spot(
            PORTAL_PREFAB, body, index, (hx, hz), pad_y,
            max(w, d) / 2, prefer=(hx, hz), yaw_deg=yaw, taken=taken,
            mask=mask)
        if spot is None:
            raise SystemExit(
                f"no legal hub-end position left for tag {tag!r} in the portal "
                f"hall: {standing} portal(s) already stand there and "
                f"{len(out)} were solved this run. That is a real refusal -- "
                f"the hall is full -- and it needs a second hall rather than "
                f"a portal inside a wall.")
        taken.append(fixtures.fixture_box(PORTAL_PREFAB, spot["x"], spot["y"],
                                          spot["z"], spot["yaw"]))
        out[tag] = spot
    # EVERY PIECE GETS ITS OWN GROUND HEIGHT. The ring stands on UNLEVELLED
    # ground -- there is no hall and there will be no terrain write here,
    # because the pad's zones include (-5,4) which holds Crossings' bridge
    # declared flatten=FORBIDDEN, and an `objects_remove` at this radius would
    # delete that bridge. Ground at a 17 m radius varies by metres, so a
    # single pad height would leave arches half-sunk or floating: the
    # operator's original complaint, sixteen times over, in the one place
    # where legibility is the entire point. So each XZ is sampled from the
    # patchscan field for its own zone -- the same generated heights the
    # flatten arithmetic uses, at 1 m.
    zones = sorted(set(zone_list(hx, hz, max(w, d) / 2 + 2.0)))
    patches = FL.run_patchscan(zones, SEED, FL.SCRATCH / "patch_portalring.bin")
    for tag, spot in out.items():
        import tcdata
        zx, zz = tcdata.zone_of(spot["x"], spot["z"])
        patch = patches[f"z_{zx}_{zz}"]
        spot["ground_y"] = round(patch.height_at_world(spot["x"], spot["z"]), 3)
        spot["pad_y_unused"] = spot["y"]
        spot["y"] = spot["ground_y"]
    out["_hub"] = {"x": hx, "z": hz, "pad_y": pad_y, "yaw": yaw,
                   "already_standing": standing,
                   "zones": [list(z) for z in zones],
                   "ground_probe": ("MEASURED per piece from the patchscan "
                                    "field at 1 m, bilinear at its own XZ. "
                                    "There is no hall and no terrain write "
                                    "here: the ring stands on generated "
                                    "ground."),
                   "no_flatten_reason": (
                       "zone (-5,4) holds Crossings' S2 bridge declared "
                       "flatten=FORBIDDEN, and objects_remove id=* at this "
                       "radius would delete 45 of its pieces")}
    HUB_SPOTS.parent.mkdir(parents=True, exist_ok=True)
    HUB_SPOTS.write_text(json.dumps(out, indent=1))
    return out


def portal_record(tag: str, x: float, y: float, z: float, yaw: float,
                  site: str, end: str, other: str, spot: dict) -> dict:
    import fixtures
    problem = fixtures.portal_tag_problem(tag)
    if problem:
        raise SystemExit(f"portal tag {tag!r} refused: {problem}")
    cmd = fixtures.portal_spawn_command(PORTAL_PREFAB, x, y, z, yaw, tag)
    return {
        "op": "portal",
        "params": {
            "prefab": PORTAL_PREFAB, "pos": [x, y, z], "yaw_deg": yaw,
            "tag": tag, "pair_tag": tag, "guard_radius_m": 0.5,
            "zdo_strings": {"tag": tag},
            "data_b64": cmd.split("data=")[1],
            "role": "spawn_portal", "site_id": site,
        },
        "wire": [cmd],
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": [PORTAL_PREFAB], "blobs": []},
        # `tag_readback` ONLY, and dropping the prefab count is a measurement
        # rather than a simplification. MEASURED by Crossings:
        # `ZDOMan::AddObjectsPerChunk` SKIPS any ZDO whose prefab is in
        # `Game.PortalPrefabHash`, so a chunk-scoped count reports zero
        # portals even when they are standing. A count that can answer zero
        # for a portal that exists is a check that fails for the wrong reason,
        # and the thing worth proving is the TAG off the ZDO, which
        # `findObjects -prefab ... -detailed` reads directly.
        "expect": {"tag_readback": True},
        "meta": {"end": end, "pair_other_end": other,
                 "clearance_m": spot.get("clearance_m"),
                 "wall_gap_m": spot.get("wall_gap_m"),
                 "free_above_m": spot.get("free_above_m"),
                 "why": ("pairing is EXACT STRING EQUALITY with a uniform "
                         "random draw among equally-tagged unconnected "
                         "portals, so a tag with one end pairs with nothing "
                         "and a blank tag pairs with the world's 26 blank-tag "
                         "mod-location portals at random")},
    }


def sign_records(pieces, site: str, tag: str) -> list[dict]:
    """WayFinding's guidepost, emitted through the ledger. Its emitter solves
    the board's offset against `fixtures.fixture_box`, so it cannot disagree
    with the gate that fails a build on an intersection."""
    out = []
    for p in pieces:
        params = {"prefab": p.prefab, "pos": [p.x, p.y, p.z], "yaw_deg": p.yaw,
                  "guard_radius_m": 0.5, "role": "waypoint_sign",
                  "site_id": site}
        if p.text is not None:
            import sys as _sys
            W = _waypoints()
            params["data_b64"] = W.sign_data(p.text)
            params["zdo_strings"] = {"text": p.text}
        out.append({
            "op": "spawn", "params": params, "wire": [p.spawn_command()],
            "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                         "prefabs": [p.prefab], "blobs": []},
            "expect": {"prefab_count": [{"prefab": p.prefab,
                                         "pos": [round(p.x, 2), round(p.z, 2)],
                                         "max": 1.0, "count": 1,
                                         "tolerance": 0}]},
            "meta": {"tag": tag,
                     "why": ("MEASURED: TeleportWorld::GetHoverText is the "
                             "only place a tag is shown and hover is gated at "
                             "5 m, so an unsigned portal is an anonymous arch")},
        })
    return out


def site_portal_spot(plan: dict, site: str, units: list[dict]) -> dict:
    """Where the site end stands: solved on the first unit's own pad, against
    that body's measured solids, outdoors beside it."""
    import fixtures
    unit = units[0]
    _fx, _objs, body = body_of(unit, (unit["x"], unit["target_y"], unit["z"]),
                               unit["yaw"])
    index = body.index()
    spot = fixtures.free_spot(
        PORTAL_PREFAB, body, index, (unit["x"], unit["z"]), unit["target_y"],
        max(unit["pad_w"], unit["pad_d"]) / 2, prefer=(unit["x"], unit["z"]),
        yaw_deg=unit["yaw"], want=fixtures.WANT_OUTDOOR)
    if spot is None:
        raise SystemExit(
            f"{site}: no legal outdoor portal position on {unit['id']}'s pad. "
            f"A refusal, not a fallback -- a portal inside a wall is the "
            f"defect this searches to avoid.")
    return spot


# --- the driver ------------------------------------------------------------

def unit_ops(b, plan: dict, doc: dict, unit: dict, site: str, role: str,
             units: list[dict], loc: clearance.Locations,
             srv=None, validate_only: bool = False) -> list[dict]:
    """Every record for one build unit, in the proven order.

    With `validate_only` nothing is measured live and nothing is sent: the
    records are built and handed back for `schema.validate`. MEASURED by
    RoadNet: `LiveBuilder(dry=True)` APPENDS to the shared ledger and runs
    `check_expect` against the LIVE server, so it is not a dry run at all.
    """
    place = next(p for p in doc["placements"] if p["id"] == unit["id"])
    import area as clear_area
    ar = clear_area.clearing_area(place, apron=APRON_M)
    verdict = sample_verdict(loc, unit)
    ops = clearing_ops(doc, unit["id"])
    recs = [dict(o, requires={"mods": ["UpgradeWorld"], "prefabs": [],
                              "blobs": []},
                 meta={"settlement": site, "unit": unit["id"],
                       "location_check": verdict})
            for o in ops]

    if validate_only:
        stats, comps, op_y, patches = flatten_pad(place, unit["id"])
        probes = {z: {"cmd": f"objects_count id=_ZoneCtrl pos={c.centre[0]:g},"
                             f"{c.centre[1]:g} max=1", "count": 1}
                  for z, c in comps.items()}
        entries, wire, blobs = terrain_entries(b, unit, comps, op_y, patches,
                                               probes)
        recs.append(terrain_record(unit, site, entries, wire, blobs, stats,
                                   sample_verdict(loc, unit, comps)))
        plan_path, info = spawn_plan_file(unit, unit["target_y"],
                                          SCRATCH / site)
        recs.append(spawn_plan_record(b, unit, site, role, plan_path, info,
                                      units, ar.radius_m))
        return recs
    return recs


def apply_unit(b, plan: dict, doc: dict, unit: dict, site: str, role: str,
               units: list[dict], loc: clearance.Locations) -> dict:
    """Generate -> clear -> live marker check -> flatten -> place, live."""
    place = next(p for p in doc["placements"] if p["id"] == unit["id"])
    import area as clear_area
    ar = clear_area.clearing_area(place, apron=APRON_M)
    half_diag = math.hypot(unit["pad_w"], unit["pad_d"]) / 2
    # THREE QUESTIONS, THREE OPERATIONS, and the whole point of Main's ruling
    # is that they are not interchangeable:
    #   siting      -- should we CHOOSE to build here (destructive budget,
    #                  MARGIN_M + PROTECTED_EXTRA_M, both taste). Recorded,
    #                  not gating: the sites were already solved under it.
    #   clearing    -- can `objects_remove` DELETE a POI object (piece-reach
    #                  room). HARD GATE; it is the irreversible op.
    #   terrain     -- can the write leave a POI piece floating (reach +
    #                  caller half-width + derived terrain spread, plus the
    #                  mandatory delta gate). HARD GATE, below.
    disc = location_check(loc, unit["x"], unit["z"], half_diag)
    siting = sample_verdict(loc, unit)
    room = clear_radius_room(loc, ar.centre_x, ar.centre_z)
    if ar.radius_m > room["max_radius_m"]:
        raise SystemExit(
            f"REFUSED {unit['id']}: the {ar.radius_m:.2f} m clearing cylinder "
            f"can reach {room['binding']['name']}'s PIECES -- it stands "
            f"{room['binding']['dist_m']} m away with a measured "
            f"{room['binding']['reach_m']} m reach, so the widest safe radius "
            f"here is {room['max_radius_m']} m. `objects_remove id=* "
            f"ignore=_*` is the only irreversible op in this build and a "
            f"deleted POI is the damage class this project recorded as "
            f"unrepairable. Not clipped: re-solve the pad or shrink the body.")
    out: dict = {"unit": unit["id"], "seqs": {},
                 "clear_radius_m": round(ar.radius_m, 2),
                 "clear_radius_room_m": room["max_radius_m"],
                 "clear_radius_binding": room["binding"],
                 "siting_budget": {
                     "disc_verdict": disc["verdict"],
                     "sample_verdict": siting["verdict"],
                     "violations": siting["violations"][:3],
                     "note": ("the DESTRUCTIVE budget (MARGIN_M 12 + "
                              "PROTECTED_EXTRA_M 90, both taste) reported "
                              "unsuppressed even where it does not gate this "
                              "operation -- a budget this op is not bound by "
                              "should still be visible to whoever reads the "
                              "record")}}

    ops = clearing_ops(doc, unit["id"])
    gen, clear = ops[0], ops[1]
    res = b.emit(gen["op"], params=gen["params"], wire=gen["wire"],
                 requires={"mods": ["UpgradeWorld"], "prefabs": [],
                           "blobs": []},
                 expect=gen["expect"],
                 meta={"settlement": site, "unit": unit["id"],
                       "location_check": siting})
    out["seqs"]["zones_generate"] = res["seq"]

    # THE MOD-FREE LIMIT, closed. The dump cannot see More_World_Locations'
    # POIs -- MEASURED, three of four live markers in GroundTruth's test zones
    # were invisible to it -- so the LIVE marker positions are read now that
    # the zone is generated, and typed against the dump's per-type radii.
    hits, report = FL.pad_location_check(b.srv.rc, place, APRON_M,
                                        Path(LOCATIONS))
    b.observe(f"live location markers around {unit['id']}",
              "MEASURED over RCON with `findObjects -prefab LocationProxy` "
              "after zones_generate: live positions for POSITION, the "
              "mod-free dump for per-type RADIUS. This is the check the "
              "dump alone cannot make.",
              "tools/jumpstart/terraform/locations.py via flatten."
              "pad_location_check",
              {"hits": len(hits), "report": report[:1200]},
              role="site_pad", site_id=unit["id"])
    # THE MARKER SWEEP IS A PROXIMITY MEASUREMENT, NOT THE HAZARD. For a
    # marker the mod-free dump cannot identify it can only assume a worst
    # case, and MEASURED on this very pad that worst case (58 m = 32 radius +
    # 11 INFERRED overshoot + 15 taste margin) refused Stenvik's hall on a
    # marker 23.9 m away whose pieces are nowhere near the pad. So when the
    # sweep reports hits, the question is escalated rather than answered by a
    # radius: ask the world WHAT IS IN THE CYLINDER that is about to be
    # emptied. Only a non-natural prefab inside it is a stop.
    contents = cylinder_contents(b.srv, ar)
    b.observe(f"contents of the clearing cylinder for {unit['id']}",
              contents["method"], contents["tool"],
              {"total": contents["total"], "clearable": contents["clearable"],
               "not_clearable": contents["not_clearable"],
               "probe": contents["probe"],
               "live_markers_within_standoff": len(hits)},
              role="clearing", site_id=unit["id"])
    if contents["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: the clearing cylinder holds "
            f"{contents['not_clearable']}, which are not in the measured "
            f"natural set. `objects_remove` would delete them and a deleted "
            f"location piece is the one damage class this project has "
            f"recorded as unrepairable.\nlive marker sweep:\n{report}")
    out["cylinder"] = {k: contents[k] for k in
                       ("total", "clearable", "not_clearable", "verdict")}
    out["live_markers_within_standoff"] = len(hits)

    # NOTHING TO REMOVE IS A REAL ANSWER, and it has to be handled rather
    # than sent: `objects_remove id=` with an empty id list is the malformed
    # command that answers "Error: Missing ids." and removes nothing
    # silently. An empty cylinder happens on a resume -- the pad was cleared
    # on a previous attempt -- and on a pad that generated bare.
    if not contents["clearable"]:
        b.observe(f"clearing skipped for {unit['id']}",
                  "MEASURED: the cylinder holds no clearable object, so there "
                  "is nothing for `objects_remove` to do. Sending it with an "
                  "empty id list would answer `Error: Missing ids.` and "
                  "remove nothing, which is a silent no-op dressed as a step.",
                  "tools/jumpstart/settlements/build.py::cylinder_contents",
                  {"total": contents["total"], "probe": contents["probe"]},
                  role="clearing", site_id=unit["id"])
        out["seqs"]["objects_clear"] = None
    else:
        clear = scoped_clear(ar, unit["id"], contents)
        res = b.emit(clear["op"], params=clear["params"], wire=clear["wire"],
                     requires={"mods": ["UpgradeWorld"], "prefabs": [],
                               "blobs": []},
                     expect=clear["expect"],
                     meta={"settlement": site, "unit": unit["id"],
                           "clear_radius_m": round(ar.radius_m, 2),
                           "piece_reach_gate": room,
                           "room_m": round(room["max_radius_m"]
                                           - ar.radius_m, 2),
                           "cylinder": {k: contents[k] for k in
                                        ("total", "clearable",
                                         "not_clearable")},
                           "live_marker_sweep": {
                               "hits": len(hits),
                               "probe": "findObjects -prefab LocationProxy, "
                                        "bounded",
                               "report": report[:800]},
                           "policy": ("REFUSE, never clip: a town pad needs "
                                      "its WHOLE footprint clear or the "
                                      "building stands in trees. Crossings "
                                      "clips because a smaller clear still "
                                      "admits an already-sized deck. Two "
                                      "operations, two policies, one reason "
                                      "each."),
                           "ids_scoped_why": (
                               "id=* would also delete anything that arrives "
                               "between the measurement and the removal, and "
                               "it is the command that deleted POI content in "
                               "the pre-wipe world. Naming the measured "
                               "prefabs makes the safety a property of the "
                               "command.")})
        out["seqs"]["objects_clear"] = res["seq"]
    stats, comps, op_y, patches = flatten_pad(place, unit["id"])
    missing = FL.ungenerated_zones(b.srv.rc, sorted(comps))
    if missing:
        raise SystemExit(
            f"REFUSED {unit['id']}: zones {missing} hold no _ZoneCtrl, so they "
            f"were never generated. Writing terrain there plants the zone's "
            f"whole vegetation set on the UNMODIFIED generated height -- "
            f"MEASURED to 7.68 m of floating grass. Step 0 did not cover the "
            f"flatten's zone set.")
    probes = {z: zone_probe(b.srv, *z) for z in sorted(comps)}
    # MERGE BEFORE WRITING, because a zone holds one compiler and 18 Stenvik
    # pads share 8 zones. The second pad in a zone would otherwise erase the
    # first one's levelling -- and the ledger's clobber guard refuses exactly
    # that, which is how this was caught rather than discovered by the
    # operator standing on a re-ungraded pad.
    merged = union_prior(b, comps, op_y)
    if merged:
        out["merged"] = {f"{z[0]},{z[1]}": {
            "merged_from": m["merged_from"],
            "merged_from_lines": m["merged_from_lines"],
            "samples_inherited": m["samples_inherited"],
            "compiler_present": zone_compiler(b.srv, *z)} for z, m in
            merged.items()}
        b.observe(f"compiler union for {unit['id']}",
                  "MEASURED: earlier per-zone TCData blobs re-read from the "
                  "ledger's blob store and unioned per SAMPLE INDEX, ours "
                  "winning only where we wrote. `compiler_present` is a live "
                  "`objects_count _TerrainCompiler` at the zone centre, "
                  "because a record is appended BEFORE it is sent and an "
                  "append whose send failed leaves a claim with no compiler "
                  "behind it -- which is the case for this build's own first "
                  "attempt at stenvik-hall-1.",
                  "tools/jumpstart/settlements/build.py::union_prior",
                  out["merged"], role="site_pad", site_id=unit["id"])
    entries, wire, blobs = terrain_entries(b, unit, comps, op_y, patches,
                                           probes, merged)
    write_gate = delta_gate(loc, comps, pad_lattice(unit))
    write_verdict = sample_verdict(loc, unit, comps, write_gate)
    if write_verdict["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: the terrain write is not clear under the "
            f"non-destructive budget (reach + half-width + the derived 1 m "
            f"terrain spread) or its delta gate: "
            f"{write_verdict['violations'][:3]} delta={write_gate}")
    out["delta_gate"] = {k: v for k, v in write_gate.items()
                         if k != "method"}
    rec = terrain_record(unit, site, entries, wire, blobs, stats,
                         write_verdict)
    res = b.emit("terrain_write", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["terrain_write"] = res["seq"]
    out["zones"] = [e["zone"] for e in entries]
    out["earthwork"] = {"max_cut_m": stats["max_cut"],
                        "max_fill_m": stats["max_fill"],
                        "samples": stats["samples"],
                        "over_clamp": stats["over_clamp"]}

    plan_path, info = spawn_plan_file(unit, unit["target_y"], SCRATCH / site)
    rec = spawn_plan_record(b, unit, site, role, plan_path, info, units,
                            ar.radius_m)
    res = b.emit("spawn_plan", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["spawn_plan"] = res["seq"]
    out["pieces"] = rec["params"]["command_count"]
    out["count_radius_m"] = rec["meta"]["count_radius_m"]
    out["counted"] = rec["meta"]["pieces_inside_count_radius"]
    out["checks"] = res["checks"]
    # `save` is issued at the console and NOT recorded as a ledger op: it is a
    # flush rather than a world mutation, and `check_expect` can measure no
    # postcondition of it. Attaching a check that measures something else --
    # a prefab count, say -- would be a check that confidently answers a
    # question it is not asking, which is the defect class of this build.
    b.srv.command("save")
    return out


def apply_portals(b, plan: dict, doc: dict, site: str, units: list[dict],
                  loc: clearance.Locations) -> dict:
    """BOTH ends of this site's tag, plus the board that names it."""
    W = _waypoints()
    tag = site_tag(plan, site)
    spot = site_portal_spot(plan, site, units)
    spots = hub_spots(b.srv, [site_tag(plan, s) for s in
                              ([*plan["towns"]] + [r["id"] for r in
                                                   plan["outliers"]])])
    hub = spots["_hub"]
    out: dict = {"tag": tag, "site_end": spot, "hub_end": spots[tag],
                 "seqs": {}}

    # STEP 0 APPLIES TO THE RING TOO. An ungenerated zone plants its whole
    # vegetation set the first time anybody walks there, against the collider
    # at that instant -- so a ring placed first would get a Beech1 growing
    # between two arches, in the one place where legibility is the whole
    # point. Generation is neither a write nor a deletion, so it is the one
    # step of the ordering that is safe beside Crossings' FORBIDDEN bridge.
    # Idempotent: SpawnZone skips a zone that IsZoneGenerated.
    ring_zones = [tuple(z) for z in hub["zones"]]
    res = b.emit("zones_generate", params={
        "pos": [hub["x"], hub["z"]],
        "max_m": round(math.hypot(*(32.0, 32.0)) + 34.1, 2),
        "zones": [list(z) for z in ring_zones],
        "role": "spawn_portal", "site_id": "portal-ring",
        "flatten": "FORBIDDEN",
        "flatten_reason": (
            "the ring shares zone (-5,4) with Crossings' S2 bridge, which is "
            "itself flatten=FORBIDDEN over water; and the ring is placed on "
            "generated ground with each piece's own measured height rather "
            "than on a levelled pad")},
        wire=[f"zones_generate pos={hub['x']:.1f},{hub['z']:.1f} "
              f"max={math.hypot(32.0, 32.0) + 34.1:.1f}"],
        requires={"mods": ["UpgradeWorld"], "prefabs": [], "blobs": []},
        expect={"zone_ctrl": len(ring_zones)},
        meta={"why": "the portal ring's own zones, generated before any arch "
                     "is placed", "ring": True})
    out["seqs"]["ring_zones_generate"] = res["seq"]

    rec = portal_record(tag, spot["x"], spot["y"], spot["z"], spot["yaw"],
                        site, "site", "portal hall (sandbox-portal-hub)", spot)
    res = b.emit("portal", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["site"] = res["seq"]
    out["site_readback"] = res["checks"]

    h = spots[tag]
    rec = portal_record(tag, h["x"], h["y"], h["z"], h["yaw"], site, "hub",
                        f"{site} site end", h)
    res = b.emit("portal", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["hub"] = res["seq"]
    out["hub_readback"] = res["checks"]

    name = site.replace("-", " ").title()
    pieces = W.portal_guidepost(spot["x"], spot["y"], spot["z"], spot["yaw"],
                                W.wrap_for_board(f"{name} {tag}"))
    out["seqs"]["site_sign"] = []
    for rec in sign_records(pieces, site, tag):
        res = b.emit(rec["op"], params=rec["params"], wire=rec["wire"],
                     requires=rec["requires"], expect=rec["expect"],
                     meta=rec["meta"])
        out["seqs"]["site_sign"].append(res["seq"])

    bearing, compass, metres = W.bearing_and_range((hub["x"], hub["z"]),
                                                   (spot["x"], spot["z"]))
    pieces = W.portal_guidepost(h["x"], h["y"], h["z"], h["yaw"],
                                W.pointer_text(name, compass, metres))
    out["seqs"]["hub_sign"] = []
    for rec in sign_records(pieces, site, tag):
        res = b.emit(rec["op"], params=rec["params"], wire=rec["wire"],
                     requires=rec["requires"], expect=rec["expect"],
                     meta=rec["meta"])
        out["seqs"]["hub_sign"].append(res["seq"])
    out["bearing_deg"] = round(bearing, 1)
    out["range_m"] = round(metres, 1)
    b.srv.command("save")
    return out


# --- verification: what the OPERATOR will see ----------------------------
#
# Not "did the plan say so". Every number here is measured off the plan file
# that was actually sent plus the world that actually answered.

GROUND_TOL_M = 0.10


def grounding(plan_path: Path, pad_y: float) -> dict:
    """Grounded fraction and worst air gap of a placed body, against ITS PAD.

    `bottoming` gives the LOWEST solid in each 1 m column, which is the right
    question: a column whose lowest solid stands 2 m up has nothing under it
    and that is the operator's floating building. A column over a roof is not
    counted as floating, because its lowest solid is the ground floor.
    """
    import base_geometry
    geom = base_geometry.geometry()
    objs = VP.plan_objects(plan_path)
    floor = VP.bottoming(objs, geom)
    gaps = sorted(y - pad_y for _n, y in floor.values())
    if not gaps:
        return {"columns": 0}
    grounded = sum(1 for g in gaps if g <= GROUND_TOL_M)
    return {
        "columns": len(gaps),
        "grounded_fraction": round(grounded / len(gaps), 3),
        "worst_air_gap_m": round(gaps[-1], 3),
        "p95_air_gap_m": round(gaps[int(0.95 * (len(gaps) - 1))], 3),
        "deepest_below_pad_m": round(gaps[0], 3),
        "tolerance_m": GROUND_TOL_M,
    }


def strays(plan_path: Path, unit: dict) -> dict:
    """Pieces beyond the body's own reach -- a misparsed rotation or a
    corner-anchored body shows up here and nowhere else."""
    rows = VP.plan_rows(plan_path)
    half_diag = math.hypot(unit["pad_w"], unit["pad_d"]) / 2
    d = [math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows]
    beyond = [r[0] for r, dist in zip(rows, d) if dist > half_diag]
    return {"pieces": len(rows), "max_reach_m": round(max(d), 2),
            "pad_half_diagonal_m": round(half_diag, 2),
            "beyond_pad_count": len(beyond),
            "beyond_pad_prefabs": sorted(set(beyond))}


def fixture_audit(unit: dict) -> dict:
    """`fixtures.audit()` over the fixtures the BODY CARRIES, at the yaw and
    height it was placed at. Beds, hearths and fire pits are the ones the
    operator named: a bed in the rain is the reported defect."""
    import fixtures
    from to_rcon_plan import read_objects
    fx, objects, body = body_of(unit, (unit["x"], unit["target_y"],
                                       unit["z"]), unit["yaw"])
    index = body.index()
    rows = [(p, x, y, z, yaw) for p, x, y, z, yaw in
            [(o["prefab"], *body.world_of(o)) for o in []]]
    # The carried fixtures, in world coordinates, straight off the placed body.
    rows = [(s[6], (s[0] + s[1]) / 2, s[2], (s[4] + s[5]) / 2, 0.0)
            for s in body.solids if fixtures.is_fixture(s[6])]
    if not rows:
        return {"carried_fixtures": 0,
                "note": "this body carries no bed, hearth, station or chest"}
    rep = fixtures.audit(rows, body, index)
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    indoor = [r for r in rep["fixtures"]
              if r["want"] == fixtures.WANT_INDOOR]
    return {
        "carried_fixtures": len(rows),
        "kinds": kinds,
        "failures": rep["failures"],
        "preference_failures": rep["preference_failures"],
        "portal_failures": rep["portal_failures"],
        "portal_warnings": rep["portal_warnings"],
        "min_clearance_m": rep["min_clearance_m"],
        "min_wall_clearance_m": rep["min_wall_clearance_m"],
        "indoor_wanting": len(indoor),
        "indoor_covered": sum(1 for r in indoor
                              if r["class"] == fixtures.INDOOR_COVERED),
        "indoor_covered_m2": rep["interior"].get("indoor_covered_area_m2"),
        "clean": not (rep["failures"] or rep["preference_failures"]
                      or rep["portal_failures"]),
    }


class PostBuildField:
    """The surface a player will walk: generated ground, plus every pad delta
    this build wrote. Passed to `terrain1m.walk_profile` so the gradient is
    computed by the SAME function that judged the plan -- a second copy of the
    baseline arithmetic is how a street gets two answers."""

    def __init__(self, field, units: list[dict]):
        self.f = field
        self.units = units

    def at(self, x: float, z: float) -> float:
        h = self.f.at(x, z)
        for u in self.units:
            if (abs(x - u["x"]) <= u["pad_w"] / 2 + APRON_M
                    and abs(z - u["z"]) <= u["pad_d"] / 2 + APRON_M):
                return u["target_y"]
        return h


def street_report(plan: dict, site: str, units: list[dict]) -> list[dict]:
    """Street gradients over an 8 m baseline, on the POST-BUILD surface.

    The hard limit is the MEASURED player slide angle, 38 deg = 0.781.
    Adjacent-1 m-sample gradient is reported too, because it is the number
    that produced a false NOT WALKABLE verdict earlier tonight -- it is a
    different quantity, dominated by terrain noise, and it is shown beside
    the 8 m figure rather than instead of it.
    """
    if site not in plan["towns"]:
        return []
    fields = T.load(plan["patch_file"])
    out = []
    for street in plan["towns"][site]["streets"]:
        nodes = [tuple(n) for n in street["nodes"]]
        f = T.field_for(fields, nodes[0][0], nodes[0][1])
        post = PostBuildField(f, units)
        prof = T.walk_profile(post, nodes)
        out.append({
            "street": street["name"],
            "length_m": prof["length_m"],
            "relief_m": prof["relief_m"],
            "grade_8m_max": prof["grade_8m_max"],
            "grade_1m_max": prof["grade_1m_max"],
            "max_step_m": prof["max_step_m"],
            "slide_angle_grade": prof["slide_angle_grade"],
            "walkable": prof["grade_8m_max"] <= prof["slide_angle_grade"],
            "planned_grade_8m_max": street["profile"]["grade_8m_max"],
        })
    return out


def live_verify(srv, plan: dict, site: str, units: list[dict],
                role: str) -> list[dict]:
    """What the world answers, prefab-scoped and radius-bounded throughout.

    `objects_count id=*` is allowed only inside 40 m by `Server.count`, and
    `findObjects` is only ever asked with both a prefab filter and a radius:
    every wedge on this project followed an unbounded listing.
    """
    import area as clear_area
    doc = placements_doc(plan, site)
    out = []
    for u in units:
        place = next(p for p in doc["placements"] if p["id"] == u["id"])
        ar = clear_area.clearing_area(place, apron=APRON_M)
        plan_path = SCRATCH / site / f"{u['id']}.plan"
        rows = VP.plan_rows(plan_path)
        radius = check_radius(u, units, ar.radius_m, rows)
        inside: dict[str, int] = {}
        for prefab, x, _y, z, _yaw in rows:
            if math.hypot(x - u["x"], z - u["z"]) <= radius:
                inside[prefab] = inside.get(prefab, 0) + 1
        got, per = srv.count("*", u["x"], u["z"], radius)
        dominant = max(inside.items(), key=lambda kv: kv[1])[0]
        out.append({
            "unit": u["id"], "plan_pieces": len(rows),
            "count_radius_m": radius,
            "expected_inside": sum(inside.values()), "world_inside": got,
            "matches": got == sum(inside.values()),
            "dominant_prefab": dominant,
            "dominant_expected": inside[dominant],
            "dominant_world": per.get(dominant),
            "grounding": grounding(plan_path, u["target_y"]),
            "strays": strays(plan_path, u),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["placements", "plan", "validate", "apply",
                                   "portals"])
    ap.add_argument("--site")
    ap.add_argument("--plan-json", default=str(HERE / "plan.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--no-portals", action="store_true")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan_json).read_text())
    if a.op == "placements":
        for p in write_placements(plan):
            print(p)
        return 0
    if not a.site:
        ap.error("--site is required for plan/validate/apply/portals")

    doc = placements_doc(plan, a.site)
    PLACEMENTS.mkdir(parents=True, exist_ok=True)
    doc_path = PLACEMENTS / f"{a.site}.yaml"
    doc_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=10 ** 9))
    all_units = unit_records(plan, a.site)
    units = all_units[a.start:]
    if a.limit:
        units = units[:a.limit]
    loc = clearance.load(LOCATIONS)
    kind = "town" if a.site in plan["towns"] else next(
        r["kind"] for r in plan["outliers"] if r["id"] == a.site)
    role = ROLE_BODY[kind]

    print(f"# {a.site}: {len(units)} of {len(all_units)} build unit(s), "
          f"role {role}, placements {doc_path}")
    if a.op == "plan":
        for u in units:
            half_diag = math.hypot(u["pad_w"], u["pad_d"]) / 2
            lc = location_check(loc, u["x"], u["z"], half_diag)
            sv = sample_verdict(loc, u)
            ops = clearing_ops(doc, u["id"])
            plan_path, info = spawn_plan_file(u, u["target_y"],
                                              SCRATCH / a.site)
            print(f"\n## {u['id']} {u['body']} at ({u['x']}, {u['z']}) yaw "
                  f"{u['yaw']} pad {u['pad_w']}x{u['pad_d']} target_y "
                  f"{u['target_y']}")
            print(f"   dump disc:    {lc['verdict']} (nearest "
                  f"{lc['nearest']['name']} at {lc['nearest']['dist_m']} m, "
                  f"stand-off {lc['standoff_m']} m)")
            print(f"   dump samples: {sv['verdict']} over "
                  f"{sv['samples_tested']} written samples")
            for o in ops:
                print(f"   {o['op']:16s} {o['wire']}")
            print(f"   spawn_plan       {plan_path} {info['command_count']} "
                  f"commands, {len(info['prefabs'])} distinct prefabs")
        return 0

    from live import LiveBuilder  # noqa: E402
    import schema  # noqa: E402

    if a.op == "validate":
        # No append, no console: build the records and validate them. The
        # ledger object is needed only for its blob store, which is additive
        # and content-addressed.
        from writer import Ledger
        led = Ledger(Path(str(JUMPSTART / "ledger/runs/Ulfsland")),
                     actor="SettleBuild")

        class Bag:
            def blob(self, data, note=""):
                return led.blob(data, note=note)

        bag = Bag()
        bad = 0
        for u in units:
            for rec in unit_ops(bag, plan, doc, u, a.site, role, all_units,
                                loc, validate_only=True):
                envelope = {"seq": 0, "ts": "1970-01-01T00:00:00Z",
                            "actor": "SettleBuild", "op": rec["op"],
                            "params": rec["params"], "wire": rec["wire"],
                            "requires": rec["requires"],
                            "expect": rec["expect"], "meta": rec["meta"],
                            "prev": "0" * 64}
                problems = schema.validate(envelope)
                print(f"  {u['id']:24s} {rec['op']:14s} "
                      f"{'OK' if not problems else 'REFUSED'} "
                      f"({len(rec['wire'])} wire line(s))")
                for p in problems:
                    bad += 1
                    print(f"      - {p}")
        print(f"validate: {'clean' if not bad else str(bad) + ' problem(s)'}")
        return 0 if not bad else 1

    built = []
    with LiveBuilder(actor="SettleBuild") as b:
        if a.op == "portals":
            print(json.dumps(apply_portals(b, plan, doc, a.site, all_units,
                                           loc), indent=1, default=str))
            print(json.dumps(b.close(), indent=1, default=str))
            return 0
        refused = []
        for u in units:
            # A REFUSAL IS A RESULT, NOT A CRASH. One pad that cannot be
            # cleared safely must not cost the operator the other seventeen
            # buildings, and the refusal is already in the ledger as an
            # `observe` with its measurement before the raise -- so it is
            # recorded, reported at the end, and the town continues.
            try:
                res = apply_unit(b, plan, doc, u, a.site, role, all_units,
                                 loc)
            except SystemExit as exc:
                refused.append({"unit": u["id"], "reason": str(exc)})
                b.note(f"REFUSED {u['id']}: {exc}", role="site_pad",
                       site_id=u["id"])
                print(f"REFUSED {u['id']} -- recorded, continuing\n{exc}",
                      flush=True)
                continue
            built.append(res)
            print(json.dumps(res, indent=1, default=str), flush=True)
        if refused:
            print(json.dumps({"refused": refused}, indent=1), flush=True)
        if not a.no_portals and not a.limit and not a.start:
            built.append(apply_portals(b, plan, doc, a.site, all_units, loc))
            print(json.dumps(built[-1], indent=1, default=str), flush=True)
        report = b.close()
    print(json.dumps({"site": a.site, "units": built, "close": report},
                     indent=1, default=str))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
