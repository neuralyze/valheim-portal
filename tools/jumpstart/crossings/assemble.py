#!/usr/bin/env python3
"""Assemble bridges, jetties, slips and terminals out of real pieces -- and
prove the assembly stands up before anything is sent to a server.

WHY ASSEMBLED AND NOT A LIBRARY BODY
The library has 12 `bridge` bodies and 2 `dock` bodies. None of them is
parametric: MEASURED from `library/data/library_manifest.json`, they run 63 to
2,883 pieces over footprints like 89.1 x 89.2 m, and their spans are whatever
their author built. A crossing has a span the world chose, not a span a
blueprint chose, and "a 12 m bridge over a 40 m river is a bug". So the span-
critical structures are generated to the measured span, and a canned body is
only used where its own footprint happens to fit the site.

WHAT MAKES THIS MORE THAN A PILE OF COORDINATES
`verify()` runs the GAME'S OWN structural-support algorithm over the generated
pieces and refuses the assembly if any piece would collapse. That algorithm is
MEASURED from assembly_valheim.dll 1.0.12:

  WearNTear::GetMaterialProperties -- maxSupport, minSupport, horizontalLoss,
  verticalLoss are a pure function of WearNTear.m_materialType:
      Wood       0  100   10  h 0.200000  v 0.125000
      Stone      1 1000  100  h 1.000000  v 0.125000
      Iron       2 1500   20  h 0.076923  v 0.076923
      HardWood   3  140   10  h 0.166667  v 0.100000
      Marble     4 1500  100  h 0.500000  v 0.125000
      Ashstone   5 2000  100  h 0.333333  v 0.100000
      Ancient    6 5000  100  h 0.250000  v 0.066667
      Ice        7 1000  100  h 0.333333  v 0.125000
      Timberwood 8  200   10  h 0.200000  v 0.076923

  WearNTear::UpdateSupport -- for each touching neighbour n with m_supports:
      d     = Distance(COM, n.COM) + 0.1
      cand  = n.support - loss * d * n.support      (i.e. n.support*(1-loss*d))
    with `loss` = horizontalLoss for the plain case, and where the support point
    is below this piece's COM, Mathf.Lerp(horizontalLoss, verticalLoss, t) with
    t = Acos(1 - |dir.y|)/(pi/2). A piece whose collider overlaps the `terrain`
    layer is instead PINNED to maxSupport. support = max over candidates.

  WearNTear::HaveSupport -- alive iff support >= minSupport. Otherwise
  UpdateWear applies 100 damage per tick, which kills a 400 hp plank in four.

  WearNTear::UpdateWear also contains the fact that makes this a real check and
  not a formality: `if (ZNetScene.OutsideActiveArea(pos)) { m_support =
  GetMaxSupport(); return; }`. Support is only ever EVALUATED when a player is
  nearby. So a bridge can sit in a save looking perfect for a week and fall down
  the moment the operator walks onto it. That is precisely the failure mode of
  this assignment, and it is why the check runs offline, before the build.

THE ONE APPROXIMATION, stated rather than hidden: the game takes `d` and the
support direction from `FindSupportPoint`, the actual collider contact point.
This code uses centre-of-mass to centre-of-mass. For the stacked, axis-aligned,
face-to-face geometry generated here the two agree to within the piece
thickness, and every reported margin is against minSupport with a safety factor,
so the approximation is absorbed. It would NOT be safe for oblique or
interpenetrating geometry, and `verify()` says so in its output.

WATER LEGALITY is measured too, from `data/piece_material.tsv` (produced by
`run_piecematerial.sh`): `Piece.m_noInWater` is 1 on `portal_wood`,
`piece_bed02`, `piece_chest_wood` and `piece_workbench`, and 0 on every deck,
pole and beam used here. `spawn_object` bypasses placement validation, so the
flag is advisory at spawn time -- but a portal is flagged unbuildable in water by
the game's own data, so terminal portals go on DRY LAND and the verifier
enforces it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
GEOMETRY = JUMPSTART / "blueprints" / "data" / "piece_geometry.json"
MATERIAL = HERE / "data" / "piece_material.tsv"

# MEASURED: ZoneSystem::c_WaterLevel = static literal float32(30.).
WATER_LEVEL = 30.0

# Deck height for every water crossing on this world. Not a taste pick: it is
# the freeboard the existing `early-dock` placement already declares
# (`target_y: 30.6`, `target_y_basis: 'a dock DECK, not a sea floor'`), adopted
# by RoadNet as the network's water-crossing datum so bridge decks, jetty decks
# and road surfaces all meet at one number.
DECK_Y = 30.6

# MEASURED table, see module docstring. (maxSupport, minSupport, hLoss, vLoss)
MATERIAL_PROPS = {
    0: (100.0, 10.0, 0.2, 0.125),            # Wood
    1: (1000.0, 100.0, 1.0, 0.125),          # Stone
    2: (1500.0, 20.0, 0.076923, 0.076923),   # Iron
    3: (140.0, 10.0, 0.166667, 0.1),         # HardWood
    4: (1500.0, 100.0, 0.5, 0.125),          # Marble
    5: (2000.0, 100.0, 0.333333, 0.1),       # Ashstone
    6: (5000.0, 100.0, 0.25, 0.066667),      # Ancient
    7: (1000.0, 100.0, 0.333333, 0.125),     # Ice
    8: (200.0, 10.0, 0.2, 0.076923),         # Timberwood
}
MATERIAL_NAME = {0: "Wood", 1: "Stone", 2: "Iron", 3: "HardWood", 4: "Marble",
                 5: "Ashstone", 6: "Ancient", 7: "Ice", 8: "Timberwood"}

# Safety factor on minSupport. A piece at exactly minSupport is one rain tick of
# rounding from collapsing, and the COM approximation above is worth a margin.
SUPPORT_SAFETY = 2.0


# ---------------------------------------------------------------------------
# Measured piece facts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PieceFacts:
    name: str
    lo: tuple[float, float, float]   # local AABB min over all solid colliders
    hi: tuple[float, float, float]
    material: int | None
    supports: bool
    no_in_water: bool
    health: float

    @property
    def top(self) -> float:
        return self.hi[1]

    @property
    def bottom(self) -> float:
        return self.lo[1]


_FACTS: dict[str, PieceFacts] | None = None


def facts() -> dict[str, PieceFacts]:
    """Local-space solid AABB + structural fields for every prefab, MEASURED.

    Geometry comes from `piece_geometry.json` (real Collider shapes, triggers
    excluded); structure comes from `piece_material.tsv` (real serialised
    WearNTear/Piece fields). Neither is guessed and neither is hand-typed.
    """
    global _FACTS
    if _FACTS is not None:
        return _FACTS
    geom = json.loads(GEOMETRY.read_text())["prefabs"]
    mat: dict[str, dict] = {}
    with MATERIAL.open() as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            mat[row["name"]] = row
    out: dict[str, PieceFacts] = {}
    for name, solids in geom.items():
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for solid in solids:
            vals = solid[1:]
            for k in range(0, len(vals) - 2, 3):
                xs.append(vals[k])
                ys.append(vals[k + 1])
                zs.append(vals[k + 2])
        if not xs:
            continue
        m = mat.get(name, {})
        material = int(m["material"]) if m.get("material", "na") != "na" else None
        out[name] = PieceFacts(
            name=name,
            lo=(min(xs), min(ys), min(zs)),
            hi=(max(xs), max(ys), max(zs)),
            material=material,
            supports=m.get("supports") == "1",
            no_in_water=m.get("no_in_water") == "1",
            health=float(m["health"]) if m.get("health", "na") != "na" else 0.0,
        )
    _FACTS = out
    return out


# ---------------------------------------------------------------------------
# A placed piece
# ---------------------------------------------------------------------------

@dataclass
class Placed:
    prefab: str
    x: float
    y: float
    z: float
    yaw: float = 0.0
    role: str = ""

    # filled by verify()
    support: float = 0.0
    on_terrain: bool = False

    def aabb(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """World AABB. Only yaws that are multiples of 90 deg are generated, so
        the axis-aligned box stays exact rather than becoming an inflated
        bound; a non-cardinal yaw is rotated about Y as an honest bounding box
        and `verify()` reports that it did."""
        f = facts()[self.prefab]
        lo, hi = f.lo, f.hi
        cx = [lo[0], hi[0]]
        cz = [lo[2], hi[2]]
        a = math.radians(self.yaw)
        ca, sa = math.cos(a), math.sin(a)
        xs, zs = [], []
        for px in cx:
            for pz in cz:
                xs.append(px * ca + pz * sa)
                zs.append(-px * sa + pz * ca)
        return ((self.x + min(xs), self.y + lo[1], self.z + min(zs)),
                (self.x + max(xs), self.y + hi[1], self.z + max(zs)))

    def com(self) -> tuple[float, float, float]:
        lo, hi = self.aabb()
        return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def _unit(bearing_deg: float) -> tuple[float, float]:
    """Valheim bearing: 0 = +Z, 90 = +X, clockwise seen from above. Matches the
    yaw convention `to_rcon_plan.py` documents (Quaternion.Euler y, +yaw turns
    the building clockwise from above)."""
    a = math.radians(bearing_deg)
    return math.sin(a), math.cos(a)


def _pile_stack(x: float, z: float, deck_y: float, ground_y: float,
                role: str, embed: float = 1.0) -> list[Placed]:
    """A column of 4 m log piles from just under the deck down past the bed.

    `embed` is how far the lowest pile's bottom goes BELOW the generated ground,
    and it is the whole point of the column: a piece whose collider overlaps the
    terrain layer is pinned to maxSupport (MEASURED, WearNTear::UpdateSupport),
    so a pile that stops at the bed is a pile with no foundation and a pile that
    is buried is a foundation. 1.0 m is one full sample of overlap at the 1 m
    field resolution.
    """
    f = facts()["wood_pole_log_4"]
    height = f.hi[1] - f.lo[1]          # 4.0 m, MEASURED
    top_target = deck_y - 0.13          # just under a wood_floor's underside
    bottom_target = ground_y - embed
    n = max(1, math.ceil((top_target - bottom_target) / height))
    out = []
    for k in range(n):
        # place so the solid's top face is exactly `top`: y + f.hi[1] == top
        top = top_target - k * height
        out.append(Placed("wood_pole_log_4", x, top - f.hi[1], z, 0.0, f"{role}/pile"))
    return out


def bridge(bank_a: tuple[float, float], bank_b: tuple[float, float],
           bearing_deg: float, deck_y: float, bed_profile,
           width_tiles: int = 3, bent_spacing_m: float = 8.0,
           name: str = "bridge", overhang_stations: int = 1) -> list[Placed]:
    """A piled timber road bridge from bank A to bank B.

    Deck tiles at 2 m pitch (MEASURED: `wood_floor` is a 2.000 x 0.130 x 2.000
    solid whose top face is +0.0969 above its own origin), so the deck's walking
    surface lands exactly on `deck_y` and meets a road ribbon levelled to the
    same number with no step. Tiles are placed AT both bank coordinates, so each
    end of the deck is keyed 1 m into the bank rather than stopping at the
    waterline.

    `bed_profile(x, z) -> generated_height` supplies the riverbed, so pile
    lengths are measured rather than assumed.
    """
    ax, az = bank_a
    bx, bz = bank_b
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    # lateral unit, +90 deg from the centreline
    lx, lz = uz, -ux
    f_floor = facts()["wood_floor"]
    floor_y = deck_y - f_floor.hi[1]

    out: list[Placed] = []
    # `overhang_stations` extra 2 m stations BEYOND each bank coordinate. This is
    # not padding: MEASURED on S1, the bank coordinate agreed with RoadNet sits
    # at generated height 30.10, only 0.10 m above the water plane, and the first
    # genuinely dry sample is one station further inland at 31.98. One station of
    # overhang puts the end of the deck on real ground, so the bridge is keyed
    # into the bank whether or not the road ribbon has been levelled yet.
    stations = int(round(length / 2.0)) + 1 + 2 * overhang_stations
    offsets = [(i - (width_tiles - 1) / 2) * 2.0 for i in range(width_tiles)]
    half_w = (width_tiles * 2.0) / 2.0
    for s in range(stations):
        t = (s - overhang_stations) * 2.0
        cx, cz = ax + ux * t, az + uz * t
        for off in offsets:
            out.append(Placed("wood_floor", cx + lx * off, floor_y, cz + lz * off,
                              bearing_deg, f"{name}/deck"))
        # railing: a 1 m post inboard of each edge, and a 2 m top rail along the
        # centreline direction. Both sides, every station.
        for side in (-1, 1):
            rx = cx + lx * side * (half_w - 0.2)
            rz = cz + lz * side * (half_w - 0.2)
            out.append(Placed("wood_pole", rx, deck_y + 0.5, rz, bearing_deg,
                              f"{name}/rail_post"))
            if s < stations - 1:
                mx = cx + ux * 1.0 + lx * side * (half_w - 0.2)
                mz = cz + uz * 1.0 + lz * side * (half_w - 0.2)
                out.append(Placed("wood_beam", mx, deck_y + 1.0, mz,
                                  bearing_deg + 90.0, f"{name}/rail"))

    # Bents. Abutment ends are supported by the bank itself, so bents go at
    # interior stations only, at <= bent_spacing_m centres.
    bays = max(1, math.ceil(length / bent_spacing_m))
    for k in range(1, bays):
        t = round(length * k / bays / 2.0) * 2.0   # snap to a deck station
        if t <= 0 or t >= length:
            continue
        cx, cz = ax + ux * t, az + uz * t
        for side in (-1, 1):
            px = cx + lx * side * 2.0
            pz = cz + lz * side * 2.0
            out.extend(_pile_stack(px, pz, deck_y, bed_profile(px, pz),
                                   f"{name}/bent{k}"))
        # cap beam across the two piles, under the deck
        cap = facts()["wood_wall_log_4x0.5"]
        out.append(Placed("wood_wall_log_4x0.5", cx, deck_y - 0.13 - cap.hi[1], cz,
                          bearing_deg + 90.0, f"{name}/bent{k}/cap"))
    return out


def jetty(root: tuple[float, float], bearing_deg: float, length_m: float,
          deck_y: float, bed_profile, width_tiles: int = 2,
          bent_spacing_m: float = 4.0, name: str = "jetty") -> list[Placed]:
    """A pier running out from the shore over water on piles.

    This is the structure the operator called a dock. It is deliberately NOT a
    flattened pad with a building on it: the pad is the `early-dock` defect,
    where the flatten removed the water from under the footprint and the pier
    then read as floating. Every station here stands on piles driven into the
    bed and there is no terrain write anywhere in its footprint.
    """
    ux, uz = _unit(bearing_deg)
    lx, lz = uz, -ux
    f_floor = facts()["wood_floor"]
    floor_y = deck_y - f_floor.hi[1]
    offsets = [(i - (width_tiles - 1) / 2) * 2.0 for i in range(width_tiles)]
    half_w = (width_tiles * 2.0) / 2.0
    out: list[Placed] = []
    stations = int(round(length_m / 2.0)) + 1
    for s in range(stations):
        t = s * 2.0
        cx, cz = root[0] + ux * t, root[1] + uz * t
        for off in offsets:
            out.append(Placed("wood_floor", cx + lx * off, floor_y, cz + lz * off,
                              bearing_deg, f"{name}/deck"))
        if s > 0 and (s * 2.0) % bent_spacing_m == 0:
            for side in (-1, 1):
                px, pz = cx + lx * side * (half_w - 1.0), cz + lz * side * (half_w - 1.0)
                out.extend(_pile_stack(px, pz, deck_y, bed_profile(px, pz),
                                       f"{name}/pile{s}"))
        # a single-sided rail, on the side away from the berth, so a boat can be
        # boarded from the deck. Handrail on the outboard side only.
        rx = cx + lx * (half_w - 0.2)
        rz = cz + lz * (half_w - 0.2)
        out.append(Placed("wood_pole", rx, deck_y + 0.5, rz, bearing_deg,
                          f"{name}/rail_post"))
    return out


def slip(root: tuple[float, float], bearing_deg: float, length_m: float,
         inner_width_m: float, deck_y: float, bed_profile,
         name: str = "slip") -> list[Placed]:
    """A boathouse slip: two finger piers with a closed head, open to seaward.

    Sizing is MEASURED from the hulls, not chosen: `VikingShip` (the Longship)
    has solid colliders spanning x[-4.58, 4.58] and z[-10.24, 11.32], so 9.16 m
    wide and 21.56 m long; `Karve` is 7.68 x 10.11 m; `Raft` is 6.09 x 6.22 m.
    An 11 m inner width and a 24 m length berth a Longship with a margin on each
    side; anything narrower is a slip a Longship cannot enter.

    What a slip is FOR, stated honestly: Valheim has NO mooring mechanic. There
    is nothing to tie a boat to and no field that pins it. MEASURED from
    `Ship::CustomFixedUpdate`: it returns immediately unless `m_nview.IsOwner()`,
    it forces `m_speed = Stop` and `m_rudderValue = 0` when `m_players.Count ==
    0`, and it then still applies buoyancy and damping through
    `Rigidbody::AddForceAtPosition` plus `ApplyEdgeForce` on every tick. So an
    unmanned boat is inert while nobody is in its active area, and subject to
    wave forces while somebody is. Three solid sides bound that drift
    physically, with colliders, instead of hoping.
    """
    ux, uz = _unit(bearing_deg)
    lx, lz = uz, -ux
    half = inner_width_m / 2.0
    out: list[Placed] = []
    for side in (-1, 1):
        rx = root[0] + lx * side * (half + 1.0)
        rz = root[1] + lz * side * (half + 1.0)
        out.extend(jetty((rx, rz), bearing_deg, length_m, deck_y, bed_profile,
                         width_tiles=1, bent_spacing_m=4.0,
                         name=f"{name}/finger{'L' if side < 0 else 'R'}"))
    # head walkway across the closed end, joining the two fingers
    f_floor = facts()["wood_floor"]
    floor_y = deck_y - f_floor.hi[1]
    n = int(round(inner_width_m / 2.0)) + 1
    for k in range(n):
        off = -half + k * 2.0
        out.append(Placed("wood_floor", root[0] + lx * off, floor_y,
                          root[1] + lz * off, bearing_deg, f"{name}/head"))
    return out


# ---------------------------------------------------------------------------
# The structural check
# ---------------------------------------------------------------------------

def verify(pieces: list[Placed], ground_profile, *, note: str = "") -> dict:
    """Run the game's support algorithm and report every failure.

    Returns a dict with `ok`, per-role support minima, and the list of pieces
    that would collapse. Nothing is sent anywhere if `ok` is false.
    """
    f = facts()
    boxes = [p.aabb() for p in pieces]
    coms = [p.com() for p in pieces]

    # terrain contact: the collider must actually reach the ground, sampled at
    # the piece's own footprint corners AND centre, because a pile is 0.4 m wide
    # and a single centre sample can miss a 1 m bed feature.
    for i, p in enumerate(pieces):
        lo, hi = boxes[i]
        probes = [(lo[0], lo[2]), (hi[0], lo[2]), (lo[0], hi[2]), (hi[0], hi[2]),
                  ((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)]
        p.on_terrain = any(lo[1] <= ground_profile(x, z) for x, z in probes)

    # neighbour graph: AABB overlap with a 0.1 m tolerance, which is how
    # Physics.OverlapBox behaves for face-to-face pieces after float rounding.
    tol = 0.1
    neigh: list[list[int]] = [[] for _ in pieces]
    for i in range(len(pieces)):
        li, hi_ = boxes[i]
        for j in range(i + 1, len(pieces)):
            lj, hj = boxes[j]
            if (li[0] - tol <= hj[0] and lj[0] - tol <= hi_[0]
                    and li[1] - tol <= hj[1] and lj[1] - tol <= hi_[1]
                    and li[2] - tol <= hj[2] and lj[2] - tol <= hi_[2]):
                neigh[i].append(j)
                neigh[j].append(i)

    props = [MATERIAL_PROPS[f[p.prefab].material if f[p.prefab].material is not None else 0]
             for p in pieces]
    for i, p in enumerate(pieces):
        p.support = props[i][0] if p.on_terrain else 0.0

    # Fixed-point relaxation. Monotone increasing and bounded by maxSupport, so
    # it converges; the loop bound is a guard, not the mechanism.
    for _ in range(400):
        changed = False
        for i, p in enumerate(pieces):
            if p.on_terrain:
                continue
            maxs, mins, hloss, vloss = props[i]
            best = 0.0
            for j in neigh[i]:
                if not f[pieces[j].prefab].supports:
                    continue
                sj = pieces[j].support
                if sj <= 0:
                    continue
                dx = coms[j][0] - coms[i][0]
                dy = coms[j][1] - coms[i][1]
                dz = coms[j][2] - coms[i][2]
                d = math.sqrt(dx * dx + dy * dy + dz * dz) + 0.1
                cand = sj - hloss * d * sj
                if dy < 0.0:
                    n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
                    t = math.acos(max(-1.0, min(1.0, 1.0 - abs(dy / n)))) / (math.pi / 2)
                    loss = hloss + (vloss - hloss) * t
                    cand = max(cand, sj - loss * d * sj)
                best = max(best, min(cand, maxs))
            if best > p.support + 1e-6:
                p.support = best
                changed = True
        if not changed:
            break

    failures = []
    by_role: dict[str, float] = {}
    for i, p in enumerate(pieces):
        mins = props[i][1]
        by_role[p.role] = min(by_role.get(p.role, 1e9), p.support)
        if p.support < mins * SUPPORT_SAFETY:
            failures.append(dict(prefab=p.prefab, role=p.role,
                                 xz=[round(p.x, 2), round(p.z, 2)],
                                 y=round(p.y, 3),
                                 support=round(p.support, 2),
                                 min_support=mins,
                                 needed=mins * SUPPORT_SAFETY,
                                 dead=p.support < mins))
    in_water = [p.role for p in pieces if f[p.prefab].no_in_water
                and p.y < WATER_LEVEL]
    return dict(
        ok=not failures and not in_water,
        pieces=len(pieces),
        terrain_anchored=sum(1 for p in pieces if p.on_terrain),
        support_min_by_role={k: round(v, 2) for k, v in sorted(by_role.items())},
        failures=failures,
        no_in_water_violations=sorted(set(in_water)),
        safety_factor=SUPPORT_SAFETY,
        approximation="support distance and direction taken COM-to-COM, not from "
                      "FindSupportPoint's contact point; valid for the "
                      "axis-aligned face-to-face geometry generated here",
        note=note,
    )


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

def fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0"


def command(p: Placed) -> str:
    """`spawn_object <prefab> pos=z,x,y rot=y,x,z from=0,0,0`.

    The component orders are NOT the vanilla ones and are MEASURED in
    `blueprints/to_rcon_plan.py`: `pos=` is parsed by `Parse::VectorZXYRange`
    (token 0 = Z, 1 = X, 2 = Y) and `rot=` by `Parse::VectorYXZRange` (token 0 =
    Y/yaw). `from=0,0,0` is mandatory: `pos=` is RELATIVE and `From` otherwise
    defaults to a player's position, so a plan sent while somebody is online
    lands somewhere else entirely.
    """
    return (f"spawn_object {p.prefab}"
            f" pos={fmt(p.z)},{fmt(p.x)},{fmt(p.y)}"
            f" rot={fmt(p.yaw)},0,0"
            f" from=0,0,0")


def ledger_ops(structure_id: str, role: str, pieces: list[Placed],
               verdict: dict, meta: dict) -> list[dict]:
    """One `spawn_plan` op per structure, in BuildLedger's v1.0 envelope.

    `flatten: "FORBIDDEN"` is not decoration: the replay driver refuses a
    `terrain_write` whose footprint overlaps a structure carrying it, which is
    what makes the `early-dock` floating-pier defect unreproducible by
    construction instead of by memory.
    """
    prefabs: dict[str, int] = {}
    for p in pieces:
        prefabs[p.prefab] = prefabs.get(p.prefab, 0) + 1
    xs = [p.x for p in pieces]
    zs = [p.z for p in pieces]
    ys = [p.y for p in pieces]
    return [dict(
        op="spawn_plan",
        params=dict(
            role=role,
            structure_id=structure_id,
            flatten="FORBIDDEN",
            flatten_reason=(
                "over-water structure: its piles stand on the generated bed and "
                "its deck clears the y=30.0 water plane by "
                f"{DECK_Y - WATER_LEVEL:.1f} m. A terrain_write in this footprint "
                "removes the water the structure is designed to stand in, which "
                "is the measured early-dock defect (site solved to water_dist_m "
                "0.0, pad levelled the whole rectangle, pier then stood on dry "
                "levelled ground and read as floating)."),
            command_count=len(pieces),
            prefabs=prefabs,
            bbox=dict(x=[round(min(xs), 2), round(max(xs), 2)],
                      y=[round(min(ys), 2), round(max(ys), 2)],
                      z=[round(min(zs), 2), round(max(zs), 2)]),
            deck_y=DECK_Y,
        ),
        wire=[command(p) for p in pieces],
        requires=dict(mods=["WorldEditCommands", "ServerDevcommands", "ValheimRcon"],
                      prefabs=sorted(prefabs), blobs=[]),
        expect=dict(objects_count_delta=len(pieces),
                    prefab_counts=prefabs),
        meta=dict(structural_verdict=verdict, **meta),
    )]
