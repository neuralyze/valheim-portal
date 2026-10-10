#!/usr/bin/env python3
"""THE LEVELLED GROUND OF A HUMAN-DECLARED SITE -- ONE ROUTINE FOR BOTH BUILDERS.

`harbour_city.py` (the walled city) and `harbour_b.py` (the village) each had
the same obligation and each carried its own half of it: stand a levelled
square where a road ARRIVES, and terrace the ground a body stands on.  The
village did it (per body, through `settlements/build.py`'s own encoders, and a
"street head" pinned to a ledger seq of ANOTHER world); the city did not do it
at all, so the three roads that arrive at Ulfsgard ended 16-17 m from the node
on natural ground and the gate read every one of them as meeting nothing.

WHAT A DECLARED SITE OWES ITS ROADS.  `harbour_site.measure` publishes the
LANDING a road is routed to -- `declared_sites[].landing_pad`, a square at the
node whose circumscribed disc is the site's `pad_radius_m` less the 2 m margin
every solved pad carries, levelled to one datum (the footprint's median).  This
module BUILDS that landing, as a unit `<node>-landing`, with the same
generate -> clear -> levelled-write chain a town's pad runs, so the chain holds
a pad claim (`writer._pad_claims`) the gate can resolve a terminus against.

WHAT IT REFUSES, with the numbers, instead of forcing:
  * a cylinder holding anything that is neither the measured natural set nor a
    creature (`cylinder_verdict`);
  * a pad write that would MOVE GROUND A ROAD AUTHORED (`road_overwrites`): a
    pad written over a carriageway re-authors it, and the rim step that makes
    is the road tool's refusal to carry for a site tool's write.

The functions here are pure where they can be (`landing_unit`, `arrivals`,
`road_overwrites`) so a selftest proves them over constructed documents on ANY
corpus; `terrace_units` is the one that writes.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, HERE, JUMPSTART / "roads", JUMPSTART / "ledger",
           JUMPSTART / "terraform", JUMPSTART / "clearing"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import structures as _ST    # noqa: E402  roads/, for CREATURE
import tcdata               # noqa: E402  terraform/

# A pad may move this much of a road's ground before it has re-authored it: the
# writer's own FLATTEN_TOLERANCE_M (`accept/sections/settlements.py`), the
# smallest movement the one sanctioned write path admits happened at all.
ROAD_GROUND_TOL_M = 0.01
# How far from a site's node a road's last station is still a road ARRIVING at
# that site: `accept/sections/settlements.py::ARRIVAL_REACH_M`, the distance the
# gate itself looks for the destination within.
ARRIVAL_REACH_M = 30.0
# A terrace's cut ceiling, and the landing's: the terrain clamp is 8 m and is
# not a buildability limit (see harbour_b.BODY_CUT_MAX_M for the measurement).
TERRACE_CUT_MAX_M = 2.0

# THE NEUTER (selftest): "blind" writes a pad over a road without asking.
_ROAD_GROUND_MODE = "guarded"        # guarded | blind


# ---------------------------------------------------------------------------
# what a clearing cylinder may hold
# ---------------------------------------------------------------------------
# CREATURES ARE NOT BLOCKERS AND THEY ARE NOT CLEARABLE EITHER.
#
# `build.py::cylinder_contents` splits a cylinder's contents into the MEASURED
# natural set (`NATURAL_RE`, which is vegetation, rock, pickables and
# deadfall) and everything else, and stops the build on "everything else".
# That is exactly right for its hazard - `objects_remove` deleting a POI's
# pieces is the one damage class this project calls unrepairable - and it is
# wrong about ANIMALS, which are the third thing a cylinder can hold.
#
# MEASURED on harbour B's first live pre-flight, 13 seats: five were refused
# by `{'Deer': 2, 'Neck': 1, 'Seagal': 1}`, `{'Boar': 1}`, `{'Seagal': 1}` and
# two `{'Greyling': 1}`. A seagull is not a location piece. It is not deleted
# either - `scoped_clear` names only the prefabs in the clearable set, so a
# creature is never in the removal command - and it will have walked away
# before the first `wood_floor` lands. Refusing a permanent seat on a bird's
# position at census time is a check answering a question it is not measuring.
#
# The list is EXPLICIT and exact-match rather than a prefix rule, for one
# measured reason: `goblin_bed` is a PIECE and a prefix rule on "goblin"
# would have waved through the mod farmhouse at `sudrvik-austrbakki-9`,
# which is a real blocker holding 6 `wood_floor`, 7 `wood_roof_45`, 7
# `wood_wall_half`, a `goblin_bed`, a `Music_MeadowsVillageFarm` and a
# `LocationProxy`. A wrong entry here does not delete anything; it builds on
# top of something. So it is kept tight.
# The canonical set lives in `roads/structures.py::CREATURE` and is extended
# here rather than re-invented, because a second list of the same thing is a
# second thing that can disagree with the first. That module's index is
# deliberately exhaustive-or-error: `classify()` RAISES on an unknown prefab
# rather than defaulting it, "because defaulting it would put a building in
# NATURE or a tree in STRUCTURE". Its creature set was built from a road
# corridor census, so it holds the Meadows/BlackForest wanderers and not the
# ones a shoreline census turns up - `Seagal` and `Boar_piggy` came out of
# MINE and are added with that provenance.
MOBILE = frozenset(_ST.CREATURE) | frozenset({
    "Seagal", "Boar_piggy", "Hare", "Greydwarf_Elite", "Greydwarf_Shaman",
    "Skeleton_NoArcher", "Ghost", "Blob", "BlobElite", "Leech", "Troll",
    "Wolf", "Wolf_cub", "Fenring", "Draugr", "Draugr_Elite", "Draugr_Ranged",
    "Surtling", "Serpent", "Lox", "Lox_Calf", "Hen", "Chicken", "Goblin",
    "GoblinBrute", "GoblinShaman", "Eikthyr",
    "Fish1", "Fish2", "Fish3", "Fish4", "Fish5",
})


def cylinder_verdict(srv, ar) -> dict:
    """`build.py::cylinder_contents` with the THIRD category it does not have.

    Three kinds of thing can be in a cylinder and they want three different
    answers:
      clearable  the measured natural set - named in `objects_remove`
      mobile     creatures - neither removed nor a reason to refuse
      blocking   anything else - a STOP, because it is a built piece
    Reported separately, so a refusal says which.
    """
    import build as BB  # noqa: PLC0415
    c = BB.cylinder_contents(srv, ar)
    mobile = {p: n for p, n in c["not_clearable"].items() if p in MOBILE}
    blocking = {p: n for p, n in c["not_clearable"].items()
                if p not in MOBILE}
    c["mobile"] = mobile
    c["blocking"] = blocking
    c["verdict"] = "clear" if not blocking else "VIOLATION"
    c["method"] = (c["method"] + " Creatures are separated out: they are "
                   "never named in the removal command and they move, so "
                   "they are reported and not counted as blockers.")
    return c


# ---------------------------------------------------------------------------
# the landing: what the site publishes, as the unit that builds it
# ---------------------------------------------------------------------------
def landing_unit(rec: dict) -> dict:
    """The build unit for a declared site's published landing pad.

    `rec` is a `declared_sites` record (plan.json / sites.yaml) carrying
    `landing_pad`.  A record without one was solved before the landing was
    published and is REFUSED rather than guessed: a landing sized here would be
    a second source of truth for the rectangle the roads were routed to.
    """
    lp = rec.get("landing_pad")
    if not lp:
        raise SystemExit(
            f"REFUSING {rec.get('id')}: the published declared site carries no "
            f"`landing_pad`, so this solve predates the landing (stage 8, "
            f"settlements/plan.py, publishes it). The landing is the "
            f"rectangle the roads are routed to; a builder that sized its own "
            f"would level ground no road was planned against.")
    return dict(
        id=lp["id"], prefab=None, landing=True,
        x=float(lp["x"]), z=float(lp["z"]), yaw=float(lp.get("yaw", 0.0)),
        body_x=float(lp["x"]), body_z=float(lp["z"]),
        pad_w=float(lp["pad_w"]), pad_d=float(lp["pad_d"]),
        foot_w=float(lp["pad_w"]), foot_d=float(lp["pad_d"]),
        target_y=float(lp["target_y"]), ground_y=float(lp["target_y"]),
        cut_needed_m=float(lp["cut_m"]), fill_needed_m=float(lp["fill_m"]),
        min_h=float(lp["min_h"]), max_h=float(lp["max_h"]),
        samples=int(lp["samples"]), site=rec["id"], node=rec["node"],
        why=(f"the landing of declared site {rec['id']}: the square "
             f"harbour_site.measure published at its node, levelled to its "
             f"median so every road routed to it has ground to arrive on"))


def declared_record(site_id: str, seed: str | None = None) -> dict:
    """The published `declared_sites` record of `site_id` for this seed."""
    import worldseed  # noqa: PLC0415
    plan = worldseed.plan_path(seed)
    if not plan.exists():
        raise SystemExit(f"REFUSING {site_id}: {plan} is missing; the "
                         f"declared site is measured by stage 8 "
                         f"(settlements/plan.py), which writes it")
    doc = json.loads(plan.read_text())
    for d in doc.get("declared_sites") or ():
        if d["id"] == site_id:
            return d
    raise SystemExit(
        f"REFUSING {site_id}: {plan} publishes declared site(s) "
        f"{[d['id'] for d in doc.get('declared_sites') or ()]} and not this "
        f"one. The declaration (worldseed.HUMAN_SITES) is the authority; "
        f"re-run stage 8 for this seed.")


def pieces_on_landing(rows, unit: dict, margin_m: float = 1.0) -> list[dict]:
    """The planned pieces (`(prefab, x, y, z, yaw, why)` rows) that stand inside
    the landing's rectangle plus `margin_m`.  The landing is levelled before any
    piece of the site stands, so a piece planned there would be buried or left
    floating by that levelling: refused by the caller, with these numbers."""
    hw, hd = unit["pad_w"] / 2.0 + margin_m, unit["pad_d"] / 2.0 + margin_m
    return [{"prefab": r[0], "x": round(r[1], 2), "z": round(r[3], 2),
             "why": r[5]} for r in rows
            if abs(r[1] - unit["x"]) <= hw and abs(r[3] - unit["z"]) <= hd]


def planned_claims(plan_doc: dict, apron_m: float = 0.0) -> list[dict]:
    """The landing pads of every declared site in a plan document, in
    `writer._pad_claims` shape -- the shape `roads/ribbon.py::planned_pad_claims`
    returns for a town's or an outlier's pad -- so a road is routed to, graded
    to and ARRIVES AT the rectangle this module will level, instead of a
    nominal disc round a node with nothing standing on it.  Pure."""
    out = []
    for rec in plan_doc.get("declared_sites") or ():
        lp = rec.get("landing_pad")
        if not lp:
            continue
        out.append({"seq": None, "name": lp["id"], "site_id": lp["id"],
                    "role": "site_pad", "planned": True,
                    "x": float(lp["x"]), "z": float(lp["z"]),
                    "hw": float(lp["pad_w"]) / 2.0 + apron_m,
                    "hd": float(lp["pad_d"]) / 2.0 + apron_m,
                    "datum_y": float(lp["target_y"])})
    return out


def arrivals(records: list[dict], node_xz, reach_m: float = ARRIVAL_REACH_M,
             retired: set | None = None) -> list[dict]:
    """Every road END in the chain within `reach_m` of the node: the roads that
    ARRIVE at this site, read off what they wrote (`params.profile.nodes`, the
    [x, z, y] stations of the fitted profile), never off a plan or a constant.

    A road with both ends in reach contributes both.  Pure: records in, rows
    out, so a selftest can hand it a constructed chain.
    """
    gone = retired or set()
    out = []
    for r in records:
        if r.get("op") != "terrain_write" or r.get("seq") in gone:
            continue
        p = r.get("params") or {}
        if p.get("role") != "road_segment":
            continue
        nodes = (p.get("profile") or {}).get("nodes") or []
        if len(nodes) < 2:
            continue
        for end, st in (("A", nodes[0]), ("B", nodes[-1])):
            d = math.hypot(float(st[0]) - node_xz[0], float(st[1]) - node_xz[1])
            if d <= reach_m:
                out.append({"segment": p.get("name"), "seq": r.get("seq"),
                            "end": end, "x": float(st[0]), "z": float(st[1]),
                            "y": float(st[2]), "dist_to_node_m": round(d, 2)})
    return sorted(out, key=lambda a: (a["dist_to_node_m"], a["segment"]))


def arrival_numbers(arr: list[dict], unit: dict) -> list[dict]:
    """Per arriving road: how far its last station stands from the landing's
    edge and how far its datum is from the landing's.  MEASURED, recorded for
    the gate (`terminus_step` / `terminus_berm`) to judge -- this tool levels
    the landing to the datum the plan published and does not re-grade a road
    it did not write."""
    hw, hd = unit["pad_w"] / 2.0, unit["pad_d"] / 2.0
    rows = []
    for a in arr:
        dx = max(abs(a["x"] - unit["x"]) - hw, 0.0)
        dz = max(abs(a["z"] - unit["z"]) - hd, 0.0)
        gap = math.hypot(dx, dz)
        rows.append(dict(a, gap_to_pad_edge_m=round(gap, 2),
                         datum_error_m=round(a["y"] - unit["target_y"], 3),
                         rise_per_m=round(abs(a["y"] - unit["target_y"])
                                          / max(gap, 1.0), 3)))
    return rows


# ---------------------------------------------------------------------------
# ground a road authored
# ---------------------------------------------------------------------------
def road_overwrites(ap, comps: dict) -> list[dict]:
    """The samples `comps` writes that a ROAD authored and that this write
    would move by more than `ROAD_GROUND_TOL_M`.

    `ap` is a `roads/applied.py::Applied` over the chain; `author` is the last
    claim that CHANGED a sample (not the last that merely carried it forward),
    which is what "whose ground is this" means once every claim unions its
    neighbours.  Edge samples shared by two zones are reported once.
    """
    if _ROAD_GROUND_MODE == "blind":
        return []
    hits: dict[tuple[int, int], dict] = {}
    for (zx, zz), comp in sorted(comps.items()):
        zc = ap.zone(zx, zz)
        cx, cz = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i] or not zc["modified"][i]:
                continue
            w = int(zc["author"][i])
            if w < 0 or ap.writes[w].get("role") != "road_segment":
                continue
            ours = float(comp.level_delta[i] + comp.smooth_delta[i])
            prior = float(zc["delta"][i])
            if abs(ours - prior) <= ROAD_GROUND_TOL_M:
                continue
            x, z = tcdata.sample_world(cx, cz, i % tcdata.PITCH,
                                       i // tcdata.PITCH)
            hits[(round(x), round(z))] = {
                "x": round(x), "z": round(z), "road": ap.writes[w]["name"],
                "seq": ap.writes[w]["seq"], "prior_m": round(prior, 3),
                "ours_m": round(ours, 3),
                "moved_m": round(abs(ours - prior), 3)}
    return sorted(hits.values(), key=lambda h: -h["moved_m"])


def road_ground_samples(ap, zones) -> set[tuple[int, int]]:
    """Every integer world sample in `zones` whose last AUTHOR is a road: the
    ground a seat's pad may not touch.  Used by a fitter to keep a body off a
    carriageway BEFORE it is chosen; `road_overwrites` is the same question
    asked of a finished write."""
    out: set[tuple[int, int]] = set()
    for zx, zz in sorted({(int(a), int(b)) for a, b in zones}):
        zc = ap.zone(zx, zz)
        cx, cz = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not zc["modified"][i]:
                continue
            w = int(zc["author"][i])
            if w >= 0 and ap.writes[w].get("role") == "road_segment":
                x, z = tcdata.sample_world(cx, cz, i % tcdata.PITCH,
                                           i // tcdata.PITCH)
                out.add((round(x), round(z)))
    return out


def pad_samples(x: float, z: float, w: float, d: float,
                margin_m: float = 1.0):
    """The integer world samples a pad of `w` x `d` about (x, z) writes, its
    own rectangle plus the lattice pitch the engine's bilinear blend spreads
    over (`roads/clear.py::TERRAIN_SPREAD_M`)."""
    x0, x1 = x - w / 2.0 - margin_m, x + w / 2.0 + margin_m
    z0, z1 = z - d / 2.0 - margin_m, z + d / 2.0 + margin_m
    for ix in range(math.ceil(x0), math.floor(x1) + 1):
        for iz in range(math.ceil(z0), math.floor(z1) + 1):
            yield ix, iz


def touches(road_set: set, x: float, z: float, w: float, d: float,
            margin_m: float = 1.0) -> int:
    """How many of a pad's samples stand on road-authored ground."""
    if _ROAD_GROUND_MODE == "blind":
        return 0
    return sum(1 for p in pad_samples(x, z, w, d, margin_m) if p in road_set)


# ---------------------------------------------------------------------------
# the placements document `clearing/area.py` and `terraform/flatten.py` read
# ---------------------------------------------------------------------------
def placements_doc(units: list[dict], site: str, patch: str,
                   world: str | None = None) -> dict:
    """Only the fields those two consume are synthesised, and each is this
    module's own measurement rather than a restatement of a requirement."""
    import build as BB  # noqa: PLC0415
    import clearance  # noqa: PLC0415
    import worldseed  # noqa: PLC0415
    loc = clearance.load(BB.locations_dump())
    places = []
    for u in units:
        near = loc.nearest(u["x"], u["z"])
        places.append({
            "id": u["id"],
            # A levelled-ground unit has no blueprint because nothing is built
            # on it; `flatten.pad_extent` and `clearing.area` read the pad from
            # `footprint_orientation` and never look at the blueprint, so
            # naming one that does not exist would be the fiction.
            "blueprint": u["prefab"] or "none - levelled ground only",
            "pieces": 0 if u.get("landing") else 1,
            "sha256": "", "align": "floor-center",
            "rotation": {"yaw": u["yaw"]},
            "requirement": {"biome": "Meadows",
                            "footprint_m": max(u["pad_w"], u["pad_d"]),
                            "footprint_xz_m": [u["pad_w"], u["pad_d"]]},
            "solved": {
                "seed": worldseed.seed(), "x": u["x"], "z": u["z"],
                "y": u["ground_y"],
                "footprint_orientation":
                    f"{u['pad_w']:g} m along x by {u['pad_d']:g} m along z",
                "flatten_cost": {"target_y": u["target_y"],
                                 "resolution_m": 1.0, "estimated": False},
                "nearest_location_m": near["dist_m"],
                "nearest_location": near["prefab"],
                "nearest_location_standoff_m": near["standoff_m"]},
            "settlement": {"site": site,
                           "role": ("landing" if u.get("landing")
                                    else "town_body"),
                           "patch": patch},
        })
    return {"world": world or worldseed.world(), "settlement": site,
            "placements": places}


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------
def terrace_units(b, units: list[dict], site: str, patch: str, report: dict,
                  *, stage: str = "terrace",
                  cut_max_m: float = TERRACE_CUT_MAX_M,
                  why_terraced: str = "") -> list[dict]:
    """Per unit: zones_generate -> measured cylinder -> scoped clear ->
    levelled write.  `build.py`'s order, encoders and gates, plus the two this
    module owns (a cylinder with a blocker refuses; a write over a road's
    ground refuses).

    A pad is levelled PER UNIT because MEASURED no district on this seed fits
    inside TerrainComp's +/-8 m clamp.  The landing is one more unit.
    """
    import area as clear_area   # noqa: PLC0415
    import build as BB          # noqa: PLC0415
    import clearance            # noqa: PLC0415
    import yaml                 # noqa: PLC0415
    from applied import Applied  # noqa: PLC0415

    doc = placements_doc(units, site, patch)
    BB.PLACEMENTS.mkdir(parents=True, exist_ok=True)
    (BB.PLACEMENTS / f"{site}.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, width=10 ** 9))
    loc = clearance.load(BB.locations_dump())
    recs = []
    for u in units:
        if u["cut_needed_m"] > cut_max_m or u.get("fill_needed_m", 0) > cut_max_m:
            raise SystemExit(
                f"REFUSED {u['id']}: levelling it to {u['target_y']} m is a "
                f"{u['cut_needed_m']} m cut / {u.get('fill_needed_m', 0)} m "
                f"fill against a {cut_max_m} m ceiling. Not clipped: re-solve "
                f"the seat.")
        place = next(p for p in doc["placements"] if p["id"] == u["id"])
        ar = clear_area.clearing_area(place, apron=BB.APRON_M)
        room = BB.clear_radius_room(loc, ar.centre_x, ar.centre_z)
        if ar.radius_m > room["max_radius_m"]:
            raise SystemExit(
                f"REFUSED {u['id']}: the {ar.radius_m:.2f} m clearing "
                f"cylinder can reach {room['binding']['why']}; the widest "
                f"safe radius here is {room['max_radius_m']} m. Not clipped: "
                f"re-solve the pad.")
        ops = BB.clearing_ops(doc, u["id"])
        gen = ops[0]
        recs.append(b.emit(gen["op"], params=gen["params"], wire=gen["wire"],
                           requires={"mods": ["UpgradeWorld"], "prefabs": [],
                                     "blobs": []},
                           expect=gen["expect"],
                           meta={"settlement": site, "unit": u["id"],
                                 "stage": stage}))
        contents = cylinder_verdict(b.srv, ar)
        b.observe(f"contents of the clearing cylinder for {u['id']}",
                  contents["method"], contents["tool"],
                  {"total": contents["total"],
                   "clearable": contents["clearable"],
                   "mobile": contents["mobile"],
                   "blocking": contents["blocking"],
                   "probe": contents["probe"]},
                  role="clearing", site_id=u["id"])
        if contents["verdict"] != "clear":
            raise SystemExit(
                f"REFUSED {u['id']}: the clearing cylinder holds "
                f"{contents['blocking']}, which is neither in the measured "
                f"natural set nor a creature. A deleted location piece is "
                f"the one damage class this project has recorded as "
                f"unrepairable.")
        if contents["clearable"]:
            cl = BB.scoped_clear(ar, u["id"], contents)
            recs.append(b.emit(cl["op"], params=cl["params"], wire=cl["wire"],
                               requires={"mods": ["UpgradeWorld"],
                                         "prefabs": [], "blobs": []},
                               expect=cl["expect"],
                               meta={"settlement": site, "unit": u["id"],
                                     "stage": stage,
                                     "piece_reach_gate": room}))
        else:
            b.observe(f"clearing skipped for {u['id']}",
                      "MEASURED: the cylinder holds no clearable object, so "
                      "`objects_remove` with an empty id list would answer "
                      "`Error: Missing ids.` and remove nothing.",
                      "settlements/build.py::cylinder_contents",
                      {"total": contents["total"], "probe": contents["probe"]},
                      role="clearing", site_id=u["id"])
        stats, comps, op_y, patches = BB.flatten_pad(place, u["id"])
        # GROUND A ROAD AUTHORED IS NOT THIS UNIT'S TO MOVE.  Asked of OUR OWN
        # samples, before the union carries the road's forward: a pad written
        # over a carriageway re-authors it, and the rim step that makes is a
        # defect no road tool can close.
        over = road_overwrites(Applied(world=BB.WORLD, led=b.led), comps)
        if over:
            raise SystemExit(
                f"REFUSED {u['id']}: this write would move {len(over)} "
                f"sample(s) of ground a road authored, worst {over[0]['moved_m']}"
                f" m ({over[0]['road']} seq {over[0]['seq']} at "
                f"({over[0]['x']}, {over[0]['z']}): {over[0]['prior_m']:+} -> "
                f"{over[0]['ours_m']:+} m). Not forced: re-seat the unit off "
                f"the road's ground.")
        ready = BB.ground_ready(b, comps, op_y, unit_id=u["id"], site=site,
                                role="site_pad")
        entries, wire, blobs = BB.terrain_entries(
            b, dict(u), comps, op_y, patches, ready["probes"], ready["merged"])
        verdict = BB.sample_verdict(loc, dict(u), comps)
        rec = BB.terrain_record(dict(u), site, entries, wire, blobs, stats,
                                verdict)
        rec["meta"]["stage"] = stage
        rec["meta"]["terrace_cut_max_m"] = cut_max_m
        if why_terraced:
            rec["meta"]["why_terraced"] = why_terraced
        recs.append(b.emit(rec["op"], params=rec["params"], wire=rec["wire"],
                           requires=rec["requires"], expect=rec["expect"],
                           meta=rec["meta"]))
        if u.get("landing"):
            arr = arrival_numbers(arrivals(
                b.led.records(), (u["x"], u["z"]),
                retired=_retired(b.led.records())), u)
            b.observe(f"roads arriving at {u['id']}",
                      "MEASURED off each road's own terrain_write profile (the "
                      "last station's [x, z, y]) against the landing this "
                      "write levelled; the gate's terminus_step / terminus_berm "
                      "judge the approach, this records what they will read.",
                      "settlements/terrace.py::arrivals",
                      {"landing": u["id"], "target_y": u["target_y"],
                       "arrivals": arr},
                      role="site_pad", site_id=u["id"])
            report.setdefault("arrivals", {})[u["id"]] = arr
        report.setdefault("terraces", []).append(
            dict(unit=u["id"], max_cut=stats["max_cut"],
                 max_fill=stats["max_fill"], over_clamp=stats["over_clamp"],
                 zones=len(entries)))
    return recs


def _retired(records: list[dict]) -> set:
    import writer as WR  # noqa: PLC0415
    return set(WR.retired_seqs(records))


def save_after(b, units: list[dict], site: str, role: str, note: str) -> dict:
    """A verified save after the terraces: one `_TerrainCompiler` per zone any
    unit's pad touches, at tolerance 0."""
    import flatten as FL  # noqa: PLC0415
    zs = sorted({tuple(z) for u in units
                 for z in FL.zones_for(u["x"], u["z"], u["pad_w"], u["pad_d"])})
    if not zs:
        raise SystemExit(
            f"REFUSING to emit a save after {note}: no measurable "
            f"postcondition could be derived from its own units. 'I could "
            f"not measure it' is not 'it is fine'.")
    checks = [dict(prefab="_TerrainCompiler",
                   pos=[float(c) for c in tcdata.zone_centre(zx, zz)],
                   max=31.0, count=1, tolerance=0) for zx, zz in zs]
    return b.emit("save", params={"role": role, "site_id": site, "note": note},
                  wire=["save"], expect={"prefab_count": checks},
                  meta={"settlement": site, "stage": "terrace",
                        "why": ("a verified save at every stage boundary, so "
                                "any later boundary is a clean stopping point "
                                "rather than a chain tail with no save "
                                "after it")})


# ---------------------------------------------------------------------------
# SELFTEST: constructed documents, no world, no server, any corpus
# ---------------------------------------------------------------------------
_REC = {"id": "fx-harbour", "node": "fxnode", "kind": "harbour_city",
        "landing_pad": {"id": "fxnode-landing", "x": 100.0, "z": 200.0,
                        "pad_w": 14.1, "pad_d": 14.1, "yaw": 0.0,
                        "target_y": 32.5, "cut_m": 1.2, "fill_m": 0.9,
                        "min_h": 31.6, "max_h": 33.7, "samples": 225}}


def _case_a_piece_planned_on_the_landing_is_found_with_its_numbers() -> list[str]:
    u = landing_unit(_REC)
    rows = [("wood_floor", 103.0, 32.0, 205.0, 0.0, "U/arm 1"),
            ("wood_floor", 140.0, 32.0, 205.0, 0.0, "U/arm 2"),
            ("wood_pole", 107.5, 30.0, 200.0, 0.0, "U/arm 3")]
    got = pieces_on_landing(rows, u)
    bad = []
    if [g["why"] for g in got] != ["U/arm 1", "U/arm 3"]:
        bad.append(f"expected the pieces on the 14.1 m square (the margin "
                   f"included), got {got}")
    if pieces_on_landing([rows[1]], u):
        bad.append("a piece 40 m off the landing was reported on it")
    return bad


def _case_a_landing_is_a_planned_pad_claim_the_roads_can_arrive_at() -> list[str]:
    got = planned_claims({"declared_sites": [_REC, {"id": "no-landing"}]})
    if len(got) != 1:
        return [f"expected exactly the declared site that carries a landing, "
                f"got {got}"]
    c = got[0]
    want = dict(name="fxnode-landing", role="site_pad", planned=True, x=100.0,
                z=200.0, hw=7.05, hd=7.05, datum_y=32.5)
    off = {k: (c.get(k), v) for k, v in want.items()
           if (abs(c.get(k) - v) > 1e-9 if isinstance(v, float)
               else c.get(k) != v)}
    return [] if not off else [f"claim differs from the landing: {off}"]


def _road(seq, name, nodes, role="road_segment"):
    return {"op": "terrain_write", "seq": seq,
            "params": {"name": name, "role": role,
                       "profile": {"nodes": nodes}}}


def _case_the_landing_is_the_published_rectangle() -> list[str]:
    u = landing_unit(_REC)
    bad = []
    if (u["id"], u["x"], u["z"], u["pad_w"], u["target_y"]) != (
            "fxnode-landing", 100.0, 200.0, 14.1, 32.5):
        bad.append(f"the unit is not the published landing: {u}")
    if u["prefab"] is not None or not u["landing"]:
        bad.append("a landing must carry no body")
    return bad


def _case_a_site_with_no_published_landing_is_REFUSED() -> list[str]:
    try:
        landing_unit({k: v for k, v in _REC.items() if k != "landing_pad"})
    except SystemExit as e:
        return [] if "landing_pad" in str(e) else [f"wrong refusal: {e}"]
    return ["a record with no landing_pad was built: the builder would size "
            "its own rectangle, a second source of truth for the roads'"]


def _case_arrivals_are_read_off_the_chain_within_reach() -> list[str]:
    chain = [
        _road(1, "road_A", [[100.0, 170.0, 33.0], [100.0, 150.0, 34.0]]),
        _road(2, "road_B", [[300.0, 300.0, 40.0], [100.0, 217.0, 32.9]]),
        _road(3, "road_far", [[900.0, 900.0, 40.0], [950.0, 950.0, 41.0]]),
        _road(4, "pad_not_a_road", [[100.0, 200.0, 32.0], [101.0, 201.0, 32.0]],
              role="site_pad"),
    ]
    got = arrivals(chain, (100.0, 200.0))
    want = [("road_B", "B"), ("road_A", "A")]
    seen = [(a["segment"], a["end"]) for a in got]
    bad = [] if seen == want else [
        f"expected the two roads ending within {ARRIVAL_REACH_M:g} m, nearest "
        f"first, {want}; got {seen}"]
    gone = arrivals(chain, (100.0, 200.0), retired={2})
    if [a["segment"] for a in gone] != ["road_A"]:
        bad.append(f"a retired road still arrives: {gone}")
    num = arrival_numbers(got, landing_unit(_REC))
    b_row = next(r for r in num if r["segment"] == "road_B")
    if abs(b_row["gap_to_pad_edge_m"] - 9.95) > 0.01 \
            or abs(b_row["datum_error_m"] - 0.4) > 1e-6:
        bad.append(f"road_B: gap {b_row['gap_to_pad_edge_m']} (want 9.95) "
                   f"datum error {b_row['datum_error_m']} (want +0.4)")
    return bad


def _road_world():
    """`(applied, comps_over_road, comps_clear)`: a road authored four samples
    of zone (0,0); one pad stamps over them, one stands clear."""
    import build as BB  # noqa: PLC0415
    from applied import Applied  # noqa: PLC0415
    b, _ = BB._chain((0, 0), [(10, "road_fx", "road_segment",
                               {2100: 1.5, 2101: 1.5, 2102: 1.5, 2103: 1.5})])
    ap = Applied(world="selftest", led=b.led)
    over = BB._comp_with((0, 0), {2101: -0.4, 500: 0.2})
    clear = BB._comp_with((0, 0), {500: 0.2, 501: 0.2})
    return ap, {(0, 0): over}, {(0, 0): clear}


def _case_a_pad_over_a_road_is_REFUSED_with_the_numbers() -> list[str]:
    ap, over, _clear = _road_world()
    hits = road_overwrites(ap, over)
    if len(hits) != 1:
        return [f"expected the one road sample the pad moves, got {hits}"]
    h = hits[0]
    return [] if (h["road"] == "road_fx" and h["seq"] == 10
                  and abs(h["moved_m"] - 1.9) < 1e-6) else [
        f"the refusal does not carry the road, seq and 1.9 m: {h}"]


def _case_a_pad_clear_of_the_road_is_not_refused() -> list[str]:
    ap, _over, clear = _road_world()
    return [] if not road_overwrites(ap, clear) else [
        "a pad standing clear of the carriageway was refused"]


def _case_NEUTER_blind_the_pad_over_the_road_is_WRITTEN() -> list[str]:
    global _ROAD_GROUND_MODE
    ap, over, _clear = _road_world()
    prior, _ROAD_GROUND_MODE = _ROAD_GROUND_MODE, "blind"
    try:
        got = road_overwrites(ap, over)
    finally:
        _ROAD_GROUND_MODE = prior
    return [] if not got else [
        f"the neuter did not restore the unguarded write: {got}"]


def _case_a_seat_is_kept_off_road_ground() -> list[str]:
    ap, _o, _c = _road_world()
    road = road_ground_samples(ap, [(0, 0)])
    bad = []
    # the road's samples 2100..2103 are row 32, columns 35..38 of zone (0, 0)
    x0, z0 = tcdata.sample_world(0.0, 0.0, 2100 % tcdata.PITCH,
                                 2100 // tcdata.PITCH)
    if touches(road, x0 + 2.0, z0, 4.0, 4.0) == 0:
        bad.append("a pad on the carriageway touches no road ground")
    if touches(road, x0 + 2.0, z0 + 20.0, 4.0, 4.0) != 0:
        bad.append("a pad 20 m off the carriageway touches road ground")
    return bad


def _case_NEUTER_blind_the_seat_on_the_road_is_CHOSEN() -> list[str]:
    global _ROAD_GROUND_MODE
    ap, _o, _c = _road_world()
    road = road_ground_samples(ap, [(0, 0)])
    x0, z0 = tcdata.sample_world(0.0, 0.0, 2100 % tcdata.PITCH,
                                 2100 // tcdata.PITCH)
    prior, _ROAD_GROUND_MODE = _ROAD_GROUND_MODE, "blind"
    try:
        n = touches(road, x0 + 2.0, z0, 4.0, 4.0)
    finally:
        _ROAD_GROUND_MODE = prior
    return [] if n == 0 else [f"the neuter still sees the road ({n})"]


CASES = (
    ("the_landing_is_the_published_rectangle",
     _case_the_landing_is_the_published_rectangle),
    ("a_site_with_no_published_landing_is_REFUSED",
     _case_a_site_with_no_published_landing_is_REFUSED),
    ("a_piece_planned_on_the_landing_is_found_with_its_numbers",
     _case_a_piece_planned_on_the_landing_is_found_with_its_numbers),
    ("a_landing_is_a_planned_pad_claim_the_roads_can_arrive_at",
     _case_a_landing_is_a_planned_pad_claim_the_roads_can_arrive_at),
    ("arrivals_are_read_off_the_chain_within_reach",
     _case_arrivals_are_read_off_the_chain_within_reach),
    ("a_pad_over_a_road_is_REFUSED_with_the_numbers",
     _case_a_pad_over_a_road_is_REFUSED_with_the_numbers),
    ("a_pad_clear_of_the_road_is_not_refused",
     _case_a_pad_clear_of_the_road_is_not_refused),
    ("NEUTER_blind_the_pad_over_the_road_is_WRITTEN",
     _case_NEUTER_blind_the_pad_over_the_road_is_WRITTEN),
    ("a_seat_is_kept_off_road_ground", _case_a_seat_is_kept_off_road_ground),
    ("NEUTER_blind_the_seat_on_the_road_is_CHOSEN",
     _case_NEUTER_blind_the_seat_on_the_road_is_CHOSEN),
)


def selftest() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  terrace selftest {len(CASES) - bad}/{len(CASES)}  "
          f"{'PASS' if not bad else 'FAIL'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else
                     print(__doc__) or 0)
