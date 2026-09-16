#!/usr/bin/env python3
"""Lay out a town on ground that is not flat, and prove it is walkable.

WHY THE TOWN IS NOT ONE PAD
---------------------------
MEASURED on this seed: a 176 m district of >=92% Meadows with <=12 m of relief
exists in 21 decimated blocks on the spawn island and 6 on the west isle, and
NOT ONE of them is both dry and coastal. Meadows on `Pirate68` is rolling.
Levelling a whole district is also not available even where it looks tempting:
`TerrainComp::ApplyToHeightmap` clamps each sample to +/-8 m of generated
height, so a 176 m pad across 18 m of relief is simply not writable.

So the town FOLLOWS the ground:

  * a MAIN STREET chosen by measurement, not by taste: of 18 bearings through
    the town centre, the one whose 1 m walk profile has the lowest maximum
    gradient. That is a contour road, which is what a hill town has.
  * a CROSS STREET, the flattest bearing at least 50 degrees off the main one,
    so the town has a junction and a square rather than one ribbon.
  * BUILDING PLOTS along both sides of each street, each on its OWN small pad,
    yawed to face the street it stands on. Small pads are what keep the
    earthwork inside the clamp, and facing the street is what makes a doorway
    mean something once villagers exist.

WHAT IS PROVEN HERE, per building and per town
----------------------------------------------
  grounded      the body's own `flat_pad_verdict` is `grounds` or
                `grounds_on_posts` (gate in `bodies.py`), and its footprint
                fits inside its pad with a margin
  no overlap    every placed footprint is tested against every other with an
                exact oriented-rectangle separating-axis test, at the yaw it
                will be built at, and must keep `MIN_BUILDING_GAP_M`
  buildable     each pad's worst sample stays inside the +/-8 m clamp
  walkable      each street's 1 m profile: max gradient, worst step between
                consecutive metres, and whether any sample is below the water
                plane
  legal         each pad clears the per-type POI stand-off from `clearance.py`
                and the recorded installations

A street is not walkable because it is drawn; it is walkable because the
profile says so. `MAX_STREET_GRADE` is a TASTE PICK at 0.25, chosen well under
the MEASURED player slide angle of 38 degrees (tan 38 = 0.78) so a street is
comfortable rather than merely possible.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field as dcfield
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bodies as BD  # noqa: E402
import clearance  # noqa: E402
import sites as S  # noqa: E402
import terrain1m as T  # noqa: E402

# TASTE PICKS, all of them, and each one is a number the operator could argue
# with rather than a measurement:
MIN_BUILDING_GAP_M = 2.5      # daylight between two buildings
STREET_HALF_WIDTH_M = 3.0     # 6 m carriageway, matches RoadNet's trunk ribbon
VERGE_M = 1.5                 # between kerb and wall
PLOT_PITCH_M = 4.0            # granularity at which a plot may slide along a street
# Comfort, over an 8 m player-scale baseline. The HARD limit is the MEASURED
# player slide angle of 38 degrees (gradient 0.781, `Character::GetSlideAngle`);
# a street at a quarter of that is a street rather than a scramble.
MAX_STREET_GRADE = 0.25
# The one number that can hide a WALL rather than a slope. Measured between
# adjacent 1 m samples, so it is a step and not a gradient.
MAX_STREET_STEP_M = 0.7


def obb_corners(cx: float, cz: float, w: float, d: float, yaw_deg: float
                ) -> list[tuple[float, float]]:
    a = math.radians(yaw_deg)
    ca, sa = math.cos(a), math.sin(a)
    hw, hd = w / 2, d / 2
    out = []
    for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, lz = sx * hw, sz * hd
        out.append((cx + lx * ca - lz * sa, cz + lx * sa + lz * ca))
    return out


def obb_gap(a: tuple, b: tuple) -> float:
    """Separation between two oriented rectangles `(cx, cz, w, d, yaw)`.

    Positive is a gap, negative is penetration depth. Exact separating-axis on
    the four face normals -- the same reason `fixtures.py` stopped using AABBs:
    MEASURED there, a 4.0 x 1.0 m wall turned 59 degrees has a 2.45 x 4.08 m
    AABB and claims 10 m2 of floor for 4 m2 of wall. A town laid out on AABBs
    either wastes the street or invents collisions.
    """
    ca = obb_corners(*a)
    cb = obb_corners(*b)
    best = -1e9
    for (rect, other) in ((a, cb), (b, ca)):
        yaw = rect[4]
        for ang in (yaw, yaw + 90):
            r = math.radians(ang)
            ax, az = math.cos(r), math.sin(r)
            own = [p[0] * ax + p[1] * az for p in obb_corners(*rect)]
            oth = [p[0] * ax + p[1] * az for p in other]
            gap = max(min(oth) - max(own), min(own) - max(oth))
            best = max(best, gap)
    return best


@dataclass
class Building:
    id: str
    role: str
    body: str
    sha256: str
    x: float
    z: float
    yaw_deg: float
    footprint: tuple[float, float]
    pad: dict
    street: str
    side: str
    pieces: int
    grounded: str
    floating_fraction: float
    stations: int
    beds: int
    indoor_covered_m2: float | None = None
    npc_ok: bool | None = None
    poi_clearance: dict = dcfield(default_factory=dict)

    def rect(self, pad: bool = False) -> tuple:
        w, d = self.footprint
        if pad:
            w, d = self.pad["width_m"], self.pad["depth_m"]
        return (self.x, self.z, w, d, self.yaw_deg)


@dataclass
class Street:
    name: str
    bearing_deg: float
    nodes: list[tuple[float, float]]
    profile: dict

    @property
    def walkable(self) -> bool:
        return (self.profile["max_grade"] <= MAX_STREET_GRADE
                and self.profile["max_step_m"] <= MAX_STREET_STEP_M
                and self.profile["wet_samples"] == 0)


def street_through(f: T.Field, cx: float, cz: float, bearing_deg: float,
                   length_m: float) -> list[tuple[float, float]]:
    a = math.radians(bearing_deg)
    dx, dz = math.cos(a), math.sin(a)
    h = length_m / 2
    return [(cx - dx * h, cz - dz * h), (cx + dx * h, cz + dz * h)]


def flattest_bearing(f: T.Field, cx: float, cz: float, length_m: float,
                     exclude_deg: float | None = None, min_sep_deg: float = 50.0
                     ) -> tuple[float, dict]:
    """The bearing through (cx, cz) whose 1 m walk profile climbs least.

    Scored on max gradient first and mean gradient as the tiebreak: a street
    with one 30% pinch is worse than one that is 8% throughout, even if the
    second climbs more in total.
    """
    best = None
    for b in range(0, 180, 10):
        if exclude_deg is not None:
            d = abs((b - exclude_deg + 90) % 180 - 90)
            if d < min_sep_deg:
                continue
        nodes = street_through(f, cx, cz, b, length_m)
        if not all(f.contains(x, z, 4) for x, z in nodes):
            continue
        prof = T.walk_profile(f, nodes)
        key = (prof["max_grade"], prof["mean_grade"])
        if best is None or key < (best[1]["max_grade"], best[1]["mean_grade"]):
            best = (float(b), prof)
    if best is None:
        raise ValueError(f"no street fits inside patch {f.id} at ({cx}, {cz})")
    return best


class TownPlanner:
    def __init__(self, picker: S.Picker, audit_rows: list[dict] | None = None):
        self.p = picker
        self.rows = audit_rows if audit_rows is not None else BD.load_audit()
        self.by_name = {r["name"]: r for r in self.rows}

    # --- plots ---------------------------------------------------------
    def place(self, out: list[Building], f: T.Field, bid: str, role: str,
              row: dict, street: Street, side: int, along_m: float,
              pad_margin_m: float = BD.PAD_MARGIN_M) -> Building | None:
        """Try to stand one body on one plot, sliding along the street until a
        position is buildable, legal and clear of its neighbours."""
        w = float(row["footprint_x_m"])
        d = float(row["footprint_z_m"])
        pad_w, pad_d = w + 2 * pad_margin_m, d + 2 * pad_margin_m
        yaw = street.bearing_deg          # the long axis lies along the street
        a = math.radians(yaw)
        dx, dz = math.cos(a), math.sin(a)
        nx, nz = -dz, dx                  # street normal
        offset = STREET_HALF_WIDTH_M + VERGE_M + pad_d / 2
        cx0, cz0 = street.nodes[0]
        for slide in range(0, 13):
            for direction in (1, -1):
                s = along_m + direction * slide * PLOT_PITCH_M
                px = cx0 + dx * s + nx * offset * side
                pz = cz0 + dz * s + nz * offset * side
                if not f.contains(px, pz, max(pad_w, pad_d)):
                    continue
                halfdiag = math.hypot(pad_w, pad_d) / 2
                viol = self.p.loc.violations(px, pz, halfdiag)
                if viol:
                    continue
                if self.p.installation_conflict(px, pz, halfdiag) is not None:
                    continue
                try:
                    pad = T.pad_bill(f, px, pz, pad_w, pad_d, yaw)
                except ValueError:
                    continue
                if not pad["clamp_ok"] or pad["wet_samples"]:
                    continue
                cand = (px, pz, w, d, yaw)
                if any(obb_gap(cand, b.rect()) < MIN_BUILDING_GAP_M for b in out):
                    continue
                pad["x"], pad["z"] = px, pz
                return Building(
                    id=bid, role=role, body=row["name"], sha256=row["sha256"],
                    x=round(px, 1), z=round(pz, 1), yaw_deg=round(yaw, 1),
                    footprint=(w, d), pad=pad, street=street.name,
                    side=("north" if side > 0 else "south"),
                    pieces=int(row["pieces"]), grounded=row["flat_pad_verdict"],
                    floating_fraction=float(row["floating_fraction"]),
                    stations=int(row["stations"]), beds=int(row["beds"]),
                    poi_clearance=self.p.loc.nearest(px, pz),
                )
        return None

    # --- the town ------------------------------------------------------
    def plan(self, site: S.Site, name: str, plan_spec: list[tuple[str, str, int]],
             street_len_m: float = 150.0) -> dict:
        f = self.p.fields[site.patch]
        cx, cz = site.x, site.z
        b1, p1 = flattest_bearing(f, cx, cz, street_len_m)
        main = Street(f"{name}-main", b1,
                      street_through(f, cx, cz, b1, street_len_m), p1)
        b2, p2 = flattest_bearing(f, cx, cz, street_len_m * 0.6, exclude_deg=b1)
        cross = Street(f"{name}-cross", b2,
                       street_through(f, cx, cz, street_len_m * 0.6, b2), p2)
        streets = [main, cross]

        buildings: list[Building] = []
        # Four frontages: both sides of both streets. Plots are dealt ROUND
        # ROBIN from a rotating start rather than "first slot that fits",
        # because first-fit put 17 of 18 Stenvik buildings on one side of one
        # street -- a terrace, not a town. Along each frontage they alternate
        # outward from the junction so the town thickens around its square.
        slots = [(main, +1), (main, -1), (cross, +1), (cross, -1)]
        along = {id(main): street_len_m / 2, id(cross): street_len_m * 0.3}
        step = {id(s): 0 for s, _ in slots}
        dealt = 0
        for role, body_name, count in plan_spec:
            row = self.by_name.get(body_name)
            if row is None:
                raise ValueError(f"{body_name} is not in the audit")
            for k in range(count):
                bid = f"{name}-{role}-{k + 1}"
                placed = None
                order = slots[dealt % len(slots):] + slots[:dealt % len(slots)]
                dealt += 1
                for street, side in order:
                    key = (id(street), side)
                    n = step.setdefault(key, 0)
                    pitch = max(float(row["footprint_x_m"]),
                                float(row["footprint_z_m"])) + MIN_BUILDING_GAP_M + 3
                    a_m = along[id(street)] + ((n // 2) + 1) * pitch * (1 if n % 2 == 0 else -1)
                    placed = self.place(buildings, f, bid, role, row, street, side, a_m)
                    step[key] = n + 1
                    if placed:
                        buildings.append(placed)
                        break
                if placed is None:
                    print(f"  ! no plot for {bid} ({body_name})")
        square = T.pad_bill(f, cx, cz, site.notes.get("square_m", 24.0),
                            site.notes.get("square_m", 24.0))
        square["x"], square["z"] = cx, cz
        return {
            "name": name, "centre": [cx, cz], "patch": site.patch,
            "pad_y": square["target_y"],
            "square": square,
            "streets": [s.__dict__ | {"walkable": s.walkable} for s in streets],
            "buildings": [b.__dict__ for b in buildings],
            "totals": self.totals(buildings, square),
        }

    @staticmethod
    def totals(buildings: list[Building], square: dict) -> dict:
        moved = sum(b.pad["moved_m3"] for b in buildings) + square["moved_m3"]
        pieces = sum(b.pieces for b in buildings)
        worst = max((b.pad["clamp_headroom_m"] for b in buildings), default=0.0)
        tight = min((b.pad["clamp_headroom_m"] for b in buildings), default=0.0)
        return {
            "buildings": len(buildings),
            "pieces_total": pieces,
            "zdo_estimate": pieces + len(buildings),   # +1 portal/sign allowance each
            "earthwork_m3": round(moved, 1),
            "pad_area_m2": round(sum(b.pad["width_m"] * b.pad["depth_m"] for b in buildings)
                                 + square["width_m"] * square["depth_m"], 1),
            "clamp_headroom_min_m": round(tight, 2),
            "clamp_headroom_max_m": round(worst, 2),
            "beds": sum(b.beds for b in buildings),
            "stations": sum(b.stations for b in buildings),
            "all_clamp_ok": all(b.pad["clamp_ok"] for b in buildings),
        }


def check_overlaps(town: dict) -> list[dict]:
    """Every pair of footprints, exactly, at the yaw they will be built at."""
    bs = town["buildings"]
    bad = []
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a = (bs[i]["x"], bs[i]["z"], *bs[i]["footprint"], bs[i]["yaw_deg"])
            b = (bs[j]["x"], bs[j]["z"], *bs[j]["footprint"], bs[j]["yaw_deg"])
            g = obb_gap(a, b)
            if g < MIN_BUILDING_GAP_M:
                bad.append({"a": bs[i]["id"], "b": bs[j]["id"], "gap_m": round(g, 2)})
    return bad


# EVERY body here passed `spawnable.py`, which runs the real `to_rcon_plan.py`
# rather than trusting the audit's `missing_prefabs` field. MEASURED, the two
# disagree: the audit reports `missing_prefabs: []` for
# `hs_blackforest_crimsonchaostownhall.blueprint` and the plan emitter REFUSES
# it on `Placeable_HardRock: not in the evidence file`. Four of the eighteen
# bodies originally chosen here failed that way and were replaced rather than
# forced through with --drop-prefab.
STENVIK_SPEC = [
    # was hs_blackforest_crimsonchaostownhall (Placeable_HardRock unspawnable)
    ("hall", "hs_meadows_madzobuild_modernstarterhouse.blueprint", 1),
    # was hs_blackforest_mister_basestart_simple (FeastMeadows unspawnable)
    ("workshop", "creator-looney-swamp-starter-home.blueprint", 1),
    ("longhouse", "hs_blackforest_tester_meadows_log_cabin.blueprint", 2),
    ("house", "hs_blackforest_kin_01_woodhouse.blueprint", 3),
    ("cottage", "hs_meadows_cottage.blueprint", 4),
    ("stonehouse", "hs_meadows_stone_house.blueprint", 2),
    ("manor", "hs_meadows_stone_manor.blueprint", 1),
    ("beehive", "hs_meadows_aodi_the_hive_1_0.blueprint", 1),
    ("hut", "hs_meadows_hut.blueprint", 3),
]

# Vestvik's first draft used only the `hs_meadows_*` decorative kit and came
# out with 0 beds and 5 stations across 14 buildings -- a film set. MEASURED
# per body from `library/data/base_audit.json`, these carry beds and stations
# and still ground on a flat pad, so the village is somewhere a villager could
# actually live once the mod exists.
VESTVIK_SPEC = [
    ("hall", "hs_blackforest_tester_meadows_log_cabin.blueprint", 1),
    # was hs_blackforest_mister_basestart_simple (FeastMeadows unspawnable)
    ("workshop", "hs_meadows_the_neck_nook.blueprint", 1),
    ("house", "hs_blackforest_kin_01_woodhouse.blueprint", 4),
    # was Rocket Raccoon_Quick_House (thin_wood_pole_2 unspawnable)
    ("villa", "hs_meadows_villa.blueprint", 3),
    ("stonehouse", "hs_meadows_stone_cottage.blueprint", 2),
    ("hut", "hs_meadows_hut.blueprint", 2),
]
# `hs_blackforest_aodi_quickrest_sleep_module.blueprint` was in this list and is
# OUT: it carries a bed, but MEASURED through `fixtures.interior()` it has
# 0.0 m2 of covered, enclosed, reachable floor -- the bed is under an open
# shelter. It is exactly the body the NPC gate exists to refuse, and it took
# the gate to notice, because the audit's `beds: 1` says nothing about whether
# anything can stand next to the bed.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default="/tmp/settle/h1m.bin")
    ap.add_argument("--locations", default="/tmp/settle/loc3/f6fe167f4fcd.json")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    p = S.Picker(a.patches, a.locations)
    planner = TownPlanner(p)
    towns = S.dedupe(p, p.town())
    out = {}
    for site, name, spec in ((towns[0], "stenvik", STENVIK_SPEC),
                             (towns[1], "vestvik", VESTVIK_SPEC)):
        print(f"\n== {name} at ({site.x}, {site.z}) {site.patch}")
        town = planner.plan(site, name, spec)
        for s in town["streets"]:
            pr = s["profile"]
            print(f"  street {s['name']:16s} bearing {s['bearing_deg']:5.1f} "
                  f"len {pr['length_m']:6.1f} relief {pr['relief_m']:5.2f} "
                  f"max_grade {pr['max_grade']:.3f} max_step {pr['max_step_m']:.2f} "
                  f"wet {pr['wet_samples']} -> {'WALKABLE' if s['walkable'] else 'NOT WALKABLE'}")
        for b in town["buildings"]:
            print(f"  {b['id']:26s} {b['body'][:40]:42s} ({b['x']:7.1f},{b['z']:7.1f}) "
                  f"yaw {b['yaw_deg']:5.1f} {b['footprint'][0]:5.1f}x{b['footprint'][1]:5.1f} "
                  f"pad {b['pad']['width_m']:5.1f}x{b['pad']['depth_m']:5.1f} "
                  f"y={b['pad']['target_y']:7.2f} moved={b['pad']['moved_m3']:7.1f} "
                  f"head={b['pad']['clamp_headroom_m']:5.2f} {b['grounded']}")
        ov = check_overlaps(town)
        print(f"  overlaps under {MIN_BUILDING_GAP_M} m: {len(ov)} {ov[:4]}")
        print(f"  totals: {town['totals']}")
        town["overlaps"] = ov
        out[name] = town
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
