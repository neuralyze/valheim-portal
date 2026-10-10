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
# THE BED A PILE IS COUNTED AGAINST, AND ITS PROVENANCE
#
# THE DEFECT THIS TYPE EXISTS TO MAKE UNSPELLABLE.  Every generator below
# takes a `bed_profile(x, z) -> height` callable, and a callable cannot be
# asked where its numbers came from.  So three pile implementations grew in
# this tree and TWO of them counted their poles against the SEED bed:
#
#   crossings/plan.py::profiles      returned `fld.height_at` -- a raw
#                                    PatchScan field with NO ledger delta --
#                                    as BOTH `ground` and `bed`, and
#                                    waterfront.py::cmd_newbuild handed it
#                                    straight to `jetty`/`slip`
#   harbour_city.py::DredgedSurface  set `self.applied = None` and composed
#                                    the dredge onto GENERATED heights, while
#                                    its own docstring said "the applied
#                                    heights PLUS this dredge"
#   roads/boardwalk.py::surface_fn   ribbon.zone_patches + applied.Applied --
#                                    the correct one, and the only one
#
# A pile counted against the seed bed is correct exactly until somebody
# writes terrain there.  The instant a later flatten, ribbon or dredge RAISES
# the bed, the poles that were driven to the old bed are under the new one,
# and a pole under the ground is a `spawn_object`, a ZDO and an acceptance
# refusal nobody can see.  MEASURED on Ulfsland: 24 of the gate's 45 `buried`
# bodies are over-water pile stacks and fixed-datum decks, worst
# boathouse-temple-strait 33 of 104 pieces at 2.870 m and
# N1-wnorth-shelf-x03 11 of 212 at 2.294 m on `wood_pole_log_4`.
#
# So the bed stops being a bare lambda and becomes a value that CARRIES ITS
# OWN PROVENANCE, and every generator here REFUSES one that is not applied.
# The seed bed is still expressible - `Bed.generated()` - because the aimer
# and the berth table legitimately ask what the generator produced; what is
# no longer expressible is driving a pile into it by accident.
# ---------------------------------------------------------------------------

# Every pile column's lowest piece must overlap the terrain layer: that
# overlap is what pins it to maxSupport (MEASURED, WearNTear::UpdateSupport).
# Less than this and the column is a post standing on nothing.
PILE_EMBED_MIN_M = 0.10

# A deck tile over a bank may be buried by the bench it keys into, but a
# buried WALKING surface is a hole in the road.  Same number the gate's
# `buried` check uses in spirit and boardwalk has used since N1-x04.
DECK_BURIAL_MAX_M = 0.30

# Half-width of the TRAVELLED WAY.  A 6 m deck arriving at a bank with any
# cross-slope puts its OUTER tile row into the bank - that is the deck keying
# in, and refusing it would refuse a correctly keyed abutment.  MEASURED on
# N1-x04 and N4-x01: the outer row at the abutment is buried 0.610-2.703 m
# while the centre row stands clear at H.  So burial REFUSES on the centre
# 2 m tile and is COUNTED AND REPORTED outboard of it, never silently
# dropped.
TRAVELLED_WAY_HALF_M = 1.0

# A PIECE whose own TOP is under the bed is a piece nobody can see.  This is
# the acceptance gate's `buried` tolerance, read from the gate rather than
# retyped, so a piece this emitter passes is not one the gate refuses
# afterwards.  0.05 m: a tolerance large enough to swallow a whole pole
# would hide a metre of real burial, and the gate says so at its own
# definition.
PIECE_TOP_BURIED_TOL_M = 0.05

# ---------------------------------------------------------------------------
# WHAT THIS POSTCONDITION IS A FUNCTION OF - DECLARED, AND NEUTERED BOTH WAYS
# ---------------------------------------------------------------------------
# THE BUILDER CERTIFIED 319 OF 319 AND THE GATE DENIED 4.  MEASURED on
# Ulfsland save 7632b3a5, W10-brgs1-treenear-x01, ledger seqs 5421/5426/5427 -
# every one of the four is a piece THIS EMITTER PLACED, and
# `refuse_on_seating` passed all of them on the way out:
#
#   wood_pole       (-327.774, 33.000, -66.302)  0.380 m under   role rail_post
#   wood_pole_log_4 (-322.687, 26.581, -73.551)  0.335 m under   role bent2/pile
#   wood_pole_log_4 (-316.650, 27.163, -109.041) 0.293 m under   role bent7/pile
#   wood_beam       (-327.942, 33.500, -65.316)  0.180 m under   role rail
#
# TWO KEYS, AND EACH NAMES SOMETHING OTHER THAN WHAT BURIAL DEPENDS ON.
#
#   THE POPULATION was keyed on ROLE SUFFIX - `/pile`, `/deck`, `/head`.
#     `bridge` emits FIVE roles: `/deck`, `/rail_post`, `/rail`,
#     `/bent<k>/pile` and `/bent<k>/cap`.  A rail post and a rail were
#     therefore asked BY NOTHING, which is how the worst of the four - a
#     0.380 m burial on the piece a walker's hand is meant to be on - left
#     this function certified.  Burial is a property of a PLACED SOLID, not
#     of the name its emitter gave it, so the population is every piece and
#     the role decides only WHICH ARM judges it.  A role no arm claims is
#     now COUNTED AND NAMED (`pieces_unjudged`), never silently dropped.
#
#   THE SURFACE was keyed on ONE BILINEAR SAMPLE AT THE PIECE PIVOT.
#     `roads/applied.py::Applied.surface_at` interpolates the four corners of
#     the lattice cell; `Heightmap.GetHeight` is NEAREST-VERTEX, which is the
#     surface the player stands on and the surface the gate reads.  MEASURED
#     at the three poles `_pile_stack` seats with their top EXACTLY on its own
#     bed reading - zero margin, by construction:
#
#       pivot                      placed top   BILINEAR   NEAREST VERTEX
#       (-322.687, -73.551)           28.581     28.581     28.9163  +0.335
#       (-320.340, -87.350)           28.396     28.397     28.2086  -0.187
#       (-316.650, -109.041)          29.163     29.164     29.4559  +0.293
#
#     The bilinear reading reproduces the placed top to a millimetre on all
#     three - so the emitter's own bed IS what it seated against - and the
#     vertex the engine answers with stands up to 0.335 m higher.  The sign
#     flips with where in the cell the piece happens to stand, which is why
#     one of the three is proud and two are buried.
SEATING_POPULATION_DEPENDS_ON = (
    "EVERY PLACED PIECE this assembly emits, judged against the LOWEST "
    "applied height at any 1 m LATTICE VERTEX under the piece's own rotated "
    "footprint. NOT the role suffixes /pile, /deck and /head: `bridge` emits "
    "five roles and a piece is buried or not whatever its emitter called it. "
    "NOT one BILINEAR sample at the pivot: `Heightmap.GetHeight` is "
    "nearest-vertex, so an interpolated reading is a height nothing in the "
    "engine ever returns, and a piece seated exactly on it stands under the "
    "surface by the cell's own gradient.")


@dataclass(frozen=True)
class Bed:
    """A ground/bed sampler that knows whether it is the APPLIED surface.

    `kind` is "applied" only when the sampler composes the ledger's terrain
    deltas onto the generated field, so it answers "what will a player stand
    on", not "what did the generator produce".  `note` names the composition
    in words, so a refusal can say which of the two it was handed.
    """
    fn: object
    kind: str
    note: str = ""

    def __call__(self, x: float, z: float) -> float:
        return float(self.fn(x, z))

    @property
    def is_applied(self) -> bool:
        return self.kind == "applied"

    @classmethod
    def applied(cls, fn, note: str) -> "Bed":
        return cls(fn, "applied", note)

    @classmethod
    def generated(cls, fn, note: str) -> "Bed":
        return cls(fn, "generated", note)


def require_applied_bed(bed, who: str) -> Bed:
    """Refuse to drive a pile or lay a deck against anything but the applied
    surface.  A bare callable is refused too: it is exactly the shape the two
    seed-bed implementations had, and accepting it would leave the hole open."""
    if isinstance(bed, Bed) and bed.is_applied:
        return bed
    if isinstance(bed, Bed):
        raise SystemExit(
            f"{who}: REFUSED a {bed.kind!r} bed ({bed.note}). A pile is "
            f"driven to the bed a player will stand on, not the one the "
            f"generator produced: the moment any later write RAISES the "
            f"ground there, every pole counted against the seed is UNDER it "
            f"and invisible. Compose the ledger delta "
            f"(roads/applied.py::Applied) onto the generated field and pass "
            f"Bed.applied(...).")
    raise SystemExit(
        f"{who}: REFUSED a bare {type(bed).__name__} as a bed profile. A "
        f"callable cannot say whether its heights carry the ledger's terrain "
        f"deltas, and two of this tree's three pile implementations did not. "
        f"Wrap it: Bed.applied(fn, 'what it composes') or Bed.generated(fn, "
        f"...) if it really is the seed and you are not driving piles.")


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


_GEOMETRY: dict | None = None


def geometry() -> dict:
    """`piece_geometry.json`'s `prefabs` - prefab -> measured solid parts -
    parsed ONCE.  `facts()` reduces it to boxes; `colocated_pieces` hands the
    same parts to the acceptance gate's own duplicate census, so the emitter
    and the gate read one corpus and cannot disagree about a piece's solid."""
    global _GEOMETRY
    if _GEOMETRY is None:
        _GEOMETRY = json.loads(GEOMETRY.read_text())["prefabs"]
    return _GEOMETRY


def facts() -> dict[str, PieceFacts]:
    """Local-space solid AABB + structural fields for every prefab, MEASURED.

    Geometry comes from `piece_geometry.json` (real Collider shapes, triggers
    excluded); structure comes from `piece_material.tsv` (real serialised
    WearNTear/Piece fields). Neither is guessed and neither is hand-typed.
    """
    global _FACTS
    if _FACTS is not None:
        return _FACTS
    geom = geometry()
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

    # Signed distance from the structure's own centreline, in metres, RECORDED
    # AT PLACEMENT.  The generator is the only place that knows it for free -
    # it is the `off` it just used - and recovering it downstream needs the
    # span geometry, which is why `roads/boardwalk.py` had to carry a
    # `lateral_of(span, p)` of its own and no other caller had one.  It is what
    # separates a deck tile keyed into a bank from a hole in the road.
    lateral: float = 0.0

    # A tile BEYOND the structure's own end - a bridge's overhang station, a
    # jetty's shore station - laid deliberately INTO the bank so the deck
    # keys into real ground instead of stopping at the waterline.  Being
    # swallowed is what keying in MEANS, so the seating re-test reports these
    # and never refuses them; the caller that levelled the abutment is the
    # one that decides whether to trim (`boardwalk.trim_buried_overhang`).
    overhang: bool = False

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


def _pile_stack(x: float, z: float, deck_y: float, bed, role: str,
                embed: float = 1.0, lateral: float = 0.0) -> list[Placed]:
    """A column of 4 m log piles from just under the deck down past the bed.

    `embed` is how far the lowest pile's bottom goes BELOW the APPLIED ground,
    and it is the whole point of the column: a piece whose collider overlaps the
    terrain layer is pinned to maxSupport (MEASURED, WearNTear::UpdateSupport),
    so a pile that stops at the bed is a pile with no foundation and a pile that
    is buried is a foundation. 1.0 m is one full sample of overlap at the 1 m
    field resolution.

    THE BED IS SAMPLED HERE, NOT BY THE CALLER, and that is the fix. This
    function used to take a `ground_y` float, so the one decision that matters
    - WHICH surface the column is counted against - was made at a call site
    this function could not see, and two of the three call sites made it
    wrong. Taking the `Bed` itself moves the choice inside the chokepoint,
    where `require_applied_bed` can refuse it.

    THE COLUMN LENGTH IS NOW SOLVED, NOT ROUNDED UP, AND THAT IS THE SECOND
    FIX.  This function used to take `n = ceil((top_target - (ground - embed))
    / 4)` poles and stack every one of them on a rigid 4 m pitch from the
    deck downwards, so the LOWEST pole's top landed wherever that arithmetic
    put it -- which is under the bed for every column whose deck-to-bed
    distance is not within `embed` of a multiple of 4 m.  `check_seating`
    then refused the pole as "an invisible pole, not a foundation" and took
    the whole span with it.  MEASURED on Ulfsland 2026-09-22: that is 15 of
    15 pile columns across W10-x01/W5-x01, T4-x02 and T5-x01 -- 165.8 m of
    distinct declared deck on BUILT road, refused by the generator's own
    rounding rather than by the ground.  The refusal was right about the
    pole and wrong about the cause.

    So the count is chosen as the SMALLEST stack whose lowest pole still
    breaks the surface, `floor(D/4) + 1` for `D = top_target - ground`, which
    leaves that pole's top `D mod 4` ABOVE the bed -- never below it, for any
    D.  That stack's natural foot is `4 - (D mod 4)` under the bed; when it
    is shallower than `embed`, ONE extra pole is added with its top seated
    EXACTLY ON the bed, which is a full 4 m of overlap and still zero
    burial.  The two poles overlap by `4 - (D mod 4)` > 0, so the column
    stays one connected run for `verify`'s neighbour graph.

    AND THE TWO READINGS ARE DIFFERENT SURFACES, WHICH IS THE THIRD FIX.
    "EXACTLY ON the bed" is zero margin, so it is only true of the bed it
    was measured on -- and the bed `Bed.applied` hands back is BILINEAR
    while `Heightmap.GetHeight` is NEAREST-VERTEX.  MEASURED on Ulfsland
    save 7632b3a5, the three columns on W10-brgs1-treenear-x01 that needed
    an extra pole: the bilinear reading reproduces the placed top to
    +0.000/+0.001/+0.001 m, and the vertex the engine answers with stands
    +0.335/-0.187/+0.293 m off it.  Two of the three poles were therefore
    emitted wholly under the surface a player walks on, and the gate refused
    them afterwards.  So:

      the TOP is solved against `ground_top`, the LOWEST height at any
        lattice vertex under the column - the same reduction the gate's
        `buried` check makes, so no pole this function emits can be under
        the surface the engine draws;
      the FOOT is judged against `ground_foot`, the LOWER of that and the
        bilinear reading at the column's own xz - because embed asks
        whether the collider overlaps the terrain MESH, which is the
        interpolated one, and a foot that clears the lower of the two
        clears both.

    D < 0 is ground standing above the deck's own underside.  That is a
    premise failure, not a quantisation one, and it is left to
    `refuse_on_seating` to name.
    """
    bed = require_applied_bed(bed, f"_pile_stack({role})")
    f = facts()["wood_pole_log_4"]
    height = f.hi[1] - f.lo[1]          # 4.0 m, MEASURED
    ground_top = bed_under(
        Placed("wood_pole_log_4", x, 0.0, z, 0.0, f"{role}/pile"), bed)
    ground_foot = min(bed(x, z), ground_top)
    top_target = deck_y - 0.13          # just under a wood_floor's underside
    drop = top_target - ground_top
    n = max(1, math.floor(drop / height) + 1)
    out = []
    for k in range(n):
        # place so the solid's top face is exactly `top`: y + f.hi[1] == top
        top = top_target - k * height
        out.append(Placed("wood_pole_log_4", x, top - f.hi[1], z, 0.0,
                          f"{role}/pile", lateral=lateral))
    # The foot this stack reaches on its own, and the one extra pole that
    # buys the asked-for overlap without burying a pole to get it.
    natural_embed = ground_foot - (top_target - n * height)
    if drop >= 0.0 and natural_embed < embed:
        out.append(Placed("wood_pole_log_4", x, ground_top - f.hi[1], z, 0.0,
                          f"{role}/pile", lateral=lateral))
    return out


# ---------------------------------------------------------------------------
# WHAT THE TILE-ROW STATIONS ARE A FUNCTION OF - DECLARED, AND NEUTERED
# ---------------------------------------------------------------------------
# A BRIDGE LAID A ROW 0.041 m BEHIND ITS LAST ROW, AND THE GATE READ THE TWO
# AS ONE PLACE.  MEASURED on Ulfsland, S7-stathub-wtnorth-x01 (ledger seq
# 4885-4889, frozen save c4f653104b10), bank_a (450.413, 1025.902) to bank_b
# (426.077, 1062.608), length 44.041 m:
#
#   bays = ceil(44.041 / 2) = 23     rows at 0, 2, ... 44.000 (s < bays)
#   and then `length` = 44.041 for the LAST bay, 0.041 m behind the row
#   before it.
#
# Two rows 0.041 m apart are 3 `wood_floor`, 2 `wood_pole` rail posts and a
# 2 m `wood_beam` laid on one place: the gate's duplicate cube is 0.05 m on
# all three axes (`structures.DUP_TOL_M`), so 3 floors + 1 pole (the other
# pole pair was opened for the S6 road) were REFUSED at (400, 1050) and
# (450, 1050) as buried copies - 0 same-solid, 4 buried, each 0.04 m off its
# sibling - while the 9 other bridges of that world, final bays 0.514 -
# 2.000 m, were clean.  `zones_generate` was a bystander: the ledger's
# 8 / 12 covering discs are 406 of 489 zones, and a repeat generate answers
# "0 zones generated".  The second row is not a second emission; it is this
# lattice putting two rows in the cube.
#
# THE FINAL BAY IS (0, 2], and the tile is exactly 2 m: a row is only ever
# redundant, never wrong, until it is so close to its neighbour that the gate
# cannot tell them apart.  That is not a quirk of one span: every span whose
# length lies within MIN_BAY_M above an even number does it, 1 in 20 at
# MIN_BAY_M = 0.1 m.  It can be repaired in exactly one way that keeps both
# bank coordinates tiled and every seam closed: the span is divided into the
# SAME number of bays EVENLY.  The alternatives each lose a stated invariant:
#   drop the row at `length`   the deck stops 0.04 m short of its bank
#                              coordinate and `bank_endpoint_arms` fails;
#   drop the row before it     the pitch to `length` becomes 2.041 m, a 4 cm
#                              crack between 2.000 m tiles, and a tile that
#                              touches nothing carries no support
#                              (WearNTear::UpdateSupport needs contact);
#   put a row between them     still a 98 % buried row, one metre off.
# An even division is a 1.915 m pitch for S7: 24 rows, as many as before, none
# redundant, every seam overlapping by 0.085 m.
#
# EVERY SPAN WHOSE FINAL BAY IS NOT A SLIVER IS UNTOUCHED - the lattice below
# is byte for byte the one this emitter shipped, so no measured bridge moves
# by a centimetre unless it was in the cube.
BRIDGE_STATIONS_DEPENDS_ON = (
    "the ACTUAL bank-to-bank length and nothing rounded: a row AT each bank "
    "coordinate and a pitch of at most 2 m, the tile's own width, so no seam",
    "MIN_BAY_M = 2 x structures.DUP_TOL_M, READ FROM THE GATE: the shortest "
    "final bay that leaves the two rows' pivots outside the gate's cube on "
    "at least one axis at EVERY bearing (a diagonal bridge puts 0.707 of the "
    "bay on each axis, so 0.0707 m is the corner) with the wire's 4 dp "
    "rounding to spare.  A shorter final bay is absorbed by dividing the "
    "span evenly, never by dropping a bank row",
)

# THE FOUR SHIPPED STATES, off, and reachable so the arms below can put each
# back.  `_BRIDGE_STATIONS_KEEP_SLIVER_BAY` restores the lattice that laid a
# row 0.041 m behind its neighbour; `_COLOCATED_REFUSAL_OFF` restores the
# emitter that returned whatever it laid, which is what let the gate be the
# first to read it.  The other two are the same class, found by running
# `colocated_pieces` over `slip()`: `_JETTY_STANDS_A_ONE_TILE_COLUMN_TWICE`
# (both "sides" of a one-tile pier are its centreline) and
# `_SLIP_HEAD_TILE_ON_A_FINGER_ROOT` (a head tile on the finger's own tile).
_BRIDGE_STATIONS_KEEP_SLIVER_BAY = False
_COLOCATED_REFUSAL_OFF = False
_JETTY_STANDS_A_ONE_TILE_COLUMN_TWICE = False
_SLIP_HEAD_TILE_ON_A_FINGER_ROOT = False
# `bridge` laid a bent under a RAISED land row only; a LOWERED terminal
# landing row then stood on nothing (see `bridge`'s `end_bents`).
_BENT_ONLY_UNDER_RAISED_ROWS = False


def _gate():
    """The acceptance gate's duplicate section, imported where it is used.

    Read rather than retyped: `PIECE_TOP_BURIED_TOL_M` above says it is read
    from the gate and is a literal, and a tolerance retyped here is a tolerance
    that drifts the first time the gate moves.  Imported lazily, because
    `accept.sections.structures` imports THIS module from its own selftest."""
    if str(JUMPSTART) not in sys.path:
        sys.path.insert(0, str(JUMPSTART))
    from accept.sections import structures        # noqa: PLC0415
    return structures


def min_bay_m() -> float:
    """Shortest final bay the lattice will lay; see BRIDGE_STATIONS_DEPENDS_ON."""
    return 2.0 * _gate().DUP_TOL_M


def _bridge_station_distances(length: float, overhang_stations: int) -> list[float]:
    """Tile both actual banks, with at most 2m pitch and true outboard rows,
    and never two rows inside the gate's duplicate cube.

    The lattice is a row every 2 m from bank A with the last bay short.  When
    that last bay is a sliver (under `min_bay_m()`) the SAME number of bays is
    divided evenly instead; every other span keeps the lattice unchanged.  A
    span shorter than one `min_bay_m()` has no bay to divide and is left for
    `refuse_on_colocated` to refuse with its numbers rather than guessed at."""
    bays = max(1, math.ceil((length - 1e-9) / 2.0))
    if (bays > 1 and length - 2.0 * (bays - 1) < min_bay_m()
            and not _BRIDGE_STATIONS_KEEP_SLIVER_BAY):
        along = [s * (length / bays) for s in range(bays)] + [length]
    else:
        along = [s * 2.0 if s < bays else length for s in range(bays + 1)]
    return ([s * 2.0 for s in range(-overhang_stations, 0)] + along
            + [length + s * 2.0 for s in range(1, overhang_stations + 1)])


def bridge(bank_a: tuple[float, float], bank_b: tuple[float, float],
           bearing_deg: float, deck_y: float, bed_profile,
           width_tiles: int = 3, bent_spacing_m: float = 8.0,
           name: str = "bridge", overhang_stations: int = 1,
           report: dict | None = None,
           bents_on_ground_at_deck_level: bool = False,
           carriageway_half_m: float | None = None,
           shoulder_on_swallowed_ground: bool = False,
           deck_stations: list[tuple[float, float]] | None = None,
           end_bents: tuple[str, ...] = ()) -> list[Placed]:
    """A piled timber road bridge from bank A to bank B.

    Deck tiles at 2 m pitch (MEASURED: `wood_floor` is a 2.000 x 0.130 x 2.000
    solid whose top face is +0.0969 above its own origin), so the deck's walking
    surface lands exactly on `deck_y` and meets a road ribbon levelled to the
    same number with no step. Tiles are placed AT both bank coordinates, so each
    end of the deck is keyed 1 m into the bank rather than stopping at the
    waterline.

    `bed_profile` is a `Bed` and MUST be `Bed.applied(...)`: pile lengths and
    the deck's own burial are measured against the surface a player will
    stand on, never against what the generator produced. See the `Bed`
    comment block above for the two implementations that got this wrong.

    THE DECK IS RE-TESTED AFTER IT IS TILED. `deck_y` arrives as a FIXED
    DATUM solved somewhere else and nothing here used to ask whether the
    ground had since come up through it. `check_seating` below asks, on the
    travelled way, before a single piece leaves this function.

    `carriageway_half_m` names the travelled way when the deck is wider than
    it: a tile row reaching outside it is SHOULDER, and a shoulder tile or a
    rail the applied ground has already swallowed WHOLE is not emitted (see
    the comment above the tiling loop).  None means every tile row is
    carriageway, which is how every caller before the bench-width deck
    tiled, and so is a deck exactly as wide as its carriageway.

    `deck_stations`, when supplied, is the caller's bounded land-seating
    profile (distance along the span, walking-surface height). Tiles and
    furniture follow it, and each raised row receives support at its own
    height unless measured terrain already carries it. The same seating
    refusal still judges every emitted piece; a ramp is not an exemption.

    `end_bents` names the ends ("a", "b") whose OUTERMOST row carries a bent
    of its own: a bank with no bench beyond it (a segment's own terminus,
    network.CROSSING_BANKS_DEPENDS_ON) has no road ground to hold its end
    tiles, and "the abutment end is supported by the bank itself" is only
    true where the bank touches the tile.  The grounded-cap test below still
    drops a bent the terrain already carries, so an end that stands on ground
    gains nothing.  Every row OFF the datum (raised or lowered) carries a
    bent at its own height for the same reason.
    """
    bed = require_applied_bed(bed_profile, f"bridge({name})")
    ax, az = bank_a
    bx, bz = bank_b
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    # lateral unit, +90 deg from the centreline
    lx, lz = uz, -ux
    f_floor = facts()["wood_floor"]

    out: list[Placed] = []
    omitted_shoulder: list[dict] = []
    # `overhang_stations` extra 2 m stations BEYOND each bank coordinate. This is
    # not padding: MEASURED on S1, the bank coordinate agreed with RoadNet sits
    # at generated height 30.10, only 0.10 m above the water plane, and the first
    # genuinely dry sample is one station further inland at 31.98. One station of
    # overhang puts the end of the deck on real ground, so the bridge is keyed
    # into the bank whether or not the road ribbon has been levelled yet.
    # End at the actual bank, not round(length / 2) * 2. Rounding placed
    # W9's final "required" tile 0.705 m inland and called its swallowed bank
    # overhang a buried travelled-way tile. A short final bay overlaps its
    # predecessor; it cannot leave the half-metre gap that rounding down can.
    station_ts = _bridge_station_distances(length, overhang_stations)
    if deck_stations is None:
        deck_stations = [(t, deck_y) for t in station_ts]
    else:
        station_ts = [t for t, _h in deck_stations]
    station_heights = dict(deck_stations)
    offsets = [(i - (width_tiles - 1) / 2) * 2.0 for i in range(width_tiles)]
    half_w = (width_tiles * 2.0) / 2.0
    # THE SHOULDER THE BANK HAS SWALLOWED IS THE BANK'S, AND EMITTING IT
    # REFUSES THE WHOLE SPAN.  Two kinds of piece are not the travelled way:
    # the rails, and every tile row that reaches outside `carriageway_half_m`
    # (the outer rows of a deck as wide as its road's levelled bench; see
    # roads/boardwalk.py DECK_WIDTH_DEPENDS_ON).  Such a row is dropped only
    # where EVERY lattice vertex under it, its carriageway part included,
    # already stands at or above its top: the bank has taken that strip of
    # the road, and a deck as wide as the carriageway would only have keyed
    # its edge tile into the same bank.
    # A rail is dropped the same way when the ground under it stands at or
    # above its own top: the bank is the edge there, and the rail is a solid
    # nobody can see.  MEASURED on Fresh42 drive 3, S4-meadhall-southeast-x01,
    # a 4 m spur whose channel runs along a hillside.  At its bench width of
    # 6 m, the west-side rail pieces at x 393.1 were under the applied ground
    # by 0.075-0.669 m, and the seating re-test refused the deck.  Once those
    # were dropped, the west outer tiles at x 393.9 were under it by up to
    # 0.472 m, and the burial guard refused it.  That is a 101-piece deck the
    # build had already laid at its carriageway width.  The rule and the
    # reading are the same as for the bent legs below: a piece is dropped
    # exactly where the ground has made it unnecessary (`bed_under`, the
    # gate's lattice reduction), and every drop is counted and named through
    # `report`.  A tile row wholly inside the carriageway is never dropped: a
    # buried travelled way is a refusal.  `shoulder_on_swallowed_ground=True`
    # restores the unconditional emission.
    def keep(p: Placed) -> bool:
        top = p.y + facts()[p.prefab].hi[1]
        g = bed_under(p, bed)
        if g < top or shoulder_on_swallowed_ground:
            return True
        omitted_shoulder.append(dict(
            piece=p.prefab, role=p.role, xz=[round(p.x, 2), round(p.z, 2)],
            lateral=round(p.lateral, 2), top_y=round(top, 3),
            applied_ground_y=round(g, 3), under_by_m=round(g - top, 3)))
        return False

    for s, t in enumerate(station_ts):
        station_y = station_heights[t]
        floor_y = station_y - f_floor.hi[1]
        cx, cz = ax + ux * t, az + uz * t
        beyond = t < 0.0 or t > length
        for off in offsets:
            tile = Placed("wood_floor", cx + lx * off, floor_y, cz + lz * off,
                          bearing_deg, f"{name}/deck", lateral=off,
                          overhang=beyond)
            shoulder = (carriageway_half_m is not None
                        and abs(off) + 1.0 > carriageway_half_m + 1e-9)
            if not shoulder or keep(tile):
                out.append(tile)
        # railing: a 1 m post inboard of each edge, and a 2 m top rail along the
        # centreline direction. Both sides, every station.
        for side in (-1, 1):
            rx = cx + lx * side * (half_w - 0.2)
            rz = cz + lz * side * (half_w - 0.2)
            rail = [Placed("wood_pole", rx, station_y + 0.5, rz, bearing_deg,
                           f"{name}/rail_post")]
            if s < len(station_ts) - 1:
                half_bay = (station_ts[s + 1] - t) / 2.0
                mx = cx + ux * half_bay + lx * side * (half_w - 0.2)
                mz = cz + uz * half_bay + lz * side * (half_w - 0.2)
                rail_y = (station_y + station_heights[station_ts[s + 1]]) / 2.0
                rail.append(Placed("wood_beam", mx, rail_y + 1.0, mz,
                                   bearing_deg + 90.0, f"{name}/rail"))
            out.extend(p for p in rail if keep(p))

    # Bents. Abutment ends are supported by the bank itself, so bents go at
    # interior stations only, at <= bent_spacing_m centres.
    #
    # A BENT LEG STANDING ON GROUND THAT HAS ALREADY REACHED THE DECK'S
    # UNDERSIDE CARRIES NOTHING, AND EMITTING IT REFUSES THE WHOLE SPAN.
    # MEASURED on Ulfsland 2026-09-22, W5-x01/W10-x01 (-326,-66)->(-310,-154):
    # the two bents nearest the banks stand at applied ground 32.582 m and
    # 32.648 m against a deck underside of 32.370 m, so every pole in those
    # columns is under the bed by construction -- no column length can fix
    # it, because the ground is ABOVE the thing the column is holding up.
    # `refuse_on_seating` then refused 89.44 m of deck on a BUILT road for
    # two poles at its own abutment shelves.
    #
    # The condition is exact rather than tolerated, and it is the SAME
    # condition that makes the leg unnecessary: `wood_floor`'s underside sits
    # at deck_y - 0.13, so `bed >= deck_y - 0.13` at a leg's own xz means the
    # OUTER DECK TILE AT THAT SAME xz is in contact with the terrain layer
    # and is pinned to maxSupport by the ground itself.  The leg is dropped
    # exactly where the deck no longer needs it, never merely where it is
    # inconvenient, and `verify` proves the anchorage afterwards.
    #
    # COUNTED AND NAMED, NEVER SILENTLY DROPPED: every omitted leg is
    # reported through `report`, and `bents_on_ground_at_deck_level=True`
    # restores the old unconditional emission so a fixture can show this
    # geometry refused without the rule.
    bays = max(1, math.ceil(length / bent_spacing_m))
    bent_ts = {round(length * k / bays / 2.0) * 2.0 for k in range(1, bays)}
    bent_ts = {t for t in bent_ts if 0.0 < t < length}
    # A bent stands under a ROW.  On the 2 m lattice every even t IS a row, so
    # this moves nothing; on a span `_bridge_station_distances` divided evenly
    # the rows are off that grid and the bent goes to the nearest row instead
    # of standing between two.
    rows = sorted(t for t in station_ts if 0.0 < t < length)
    bent_ts = ({min(rows, key=lambda r: (abs(r - t), r)) for t in bent_ts}
               if rows else set())
    # Every row OFF the datum -- a raised land tile or a lowered terminal
    # landing -- gets a bent at its own datum unless applied terrain already
    # supports it. Never lift a floor off its old supports.
    bent_ts.update(t for t, h in deck_stations
                   if (h > deck_y + 1e-6 if _BENT_ONLY_UNDER_RAISED_ROWS
                       else abs(h - deck_y) > 1e-6))
    # A bank with no bench beyond it holds its end row on its own bent
    # (`end_bents`); the grounded-cap test below drops it where the terrain
    # already carries the tile.
    if "a" in end_bents:
        bent_ts.add(min(station_ts))
    if "b" in end_bents:
        bent_ts.add(max(station_ts))
    omitted_legs, omitted_bents = [], []
    grounded_caps = []
    cap = facts()["wood_wall_log_4x0.5"]
    for k, t in enumerate(sorted(bent_ts), 1):
        bent_y = station_heights.get(t, deck_y)
        deck_underside_y = bent_y - 0.13
        cx, cz = ax + ux * t, az + uz * t
        cap_piece = Placed("wood_wall_log_4x0.5", cx,
                           deck_underside_y - cap.hi[1], cz,
                           bearing_deg + 90.0, f"{name}/bent{k}/cap")
        cap_ground = bed_under(cap_piece, bed)
        if cap_ground >= deck_underside_y and not bents_on_ground_at_deck_level:
            # A terrain-supported floor needs no invisible cap, even if a
            # narrow pole footprint just beyond its edge reaches lower soil.
            # verify() must still prove that every emitted tile is supported.
            omitted_bents.append(f"{name}/bent{k}")
            grounded_caps.append(dict(
                bent=f"{name}/bent{k}", xz=[round(cx, 3), round(cz, 3)],
                applied_ground_y=round(cap_ground, 6),
                deck_underside_y=round(deck_underside_y, 6)))
            continue
        legs = 0
        for side in (-1, 1):
            px = cx + lx * side * 2.0
            pz = cz + lz * side * 2.0
            # THE SAME READING THE BURIAL ARM WILL USE, or the two disagree
            # and the disagreement is a refusal.  A leg emitted because the
            # BILINEAR bed is under the deck's underside, then judged buried
            # against the LATTICE vertex that is above it, refuses the whole
            # span for a premise this loop already had the number to decline.
            g = bed_under(Placed("wood_pole_log_4", px, 0.0, pz, 0.0,
                                 f"{name}/bent{k}/pile"), bed)
            if g >= deck_underside_y and not bents_on_ground_at_deck_level:
                omitted_legs.append(dict(
                    bent=f"{name}/bent{k}", xz=[round(px, 2), round(pz, 2)],
                    applied_ground_y=round(g, 3),
                    deck_underside_y=round(deck_underside_y, 3),
                    above_underside_m=round(g - deck_underside_y, 3)))
                continue
            legs += 1
            out.extend(_pile_stack(px, pz, bent_y, bed, f"{name}/bent{k}",
                                   lateral=side * 2.0))
        if legs == 0:
            omitted_bents.append(f"{name}/bent{k}")
            continue
        out.append(cap_piece)
    if report is not None:
        report["bent_legs_omitted_on_ground_at_deck_level"] = omitted_legs
        report["bents_omitted_entirely"] = omitted_bents
        report["bents_omitted_under_grounded_deck"] = grounded_caps
        report["bents_planned"] = len(bent_ts)
        report["shoulder_omitted_swallowed_by_the_bank"] = omitted_shoulder
    refuse_on_seating(out, bed, name)
    refuse_on_colocated(out, name)
    return out


# THE TWO SHIPPED KEYS, off, and reachable so the arms below can put each
# back.  Both are states this emitter was measurably in on Ulfsland save
# 7632b3a5 - see `SEATING_POPULATION_DEPENDS_ON`.
#
# `_SEATING_POPULATION_BY_ROLE_SUFFIX`  the burial population keyed on
#     `/pile`, `/deck` and `/head`, with every other role SILENTLY DROPPED -
#     the UNDER-KEYED direction, which is silent and FALSE.
# `_SEATING_POPULATION_JUDGES_NOTHING`  no piece judged at all - the
#     DEGENERATE OVER-CORRECTION, and the reason the arms below need a
#     survivor: a population that returned nothing would satisfy "the neuter
#     flipped" while naming no obligation whatever.
# `_SEATING_BED_AT_THE_PIVOT`           one BILINEAR sample at the piece
#     pivot instead of the lowest lattice vertex under its footprint.
_SEATING_POPULATION_BY_ROLE_SUFFIX = False
_SEATING_POPULATION_JUDGES_NOTHING = False
_SEATING_BED_AT_THE_PIVOT = False


def footprint_vertices(p: Placed) -> list[tuple[float, float]]:
    """Every 1 m LATTICE VERTEX under this piece's own rotated footprint.

    These are the columns `Heightmap` actually has heights for; asking
    anywhere else returns one of them anyway, interpolated, and the
    interpolation is a number the engine never gives a player back.  The
    construction is the acceptance gate's `structures.piece_columns` - the
    integer lattice inside the piece's own rotated rectangle, falling back to
    the vertex nearest the pivot for a piece too small to cover one - so the
    emitter and the gate ask their shared question of the same columns.

    The rotation is the INVERSE of the one `Placed.aabb` places with: that
    maps a local corner to the world, and this maps a world vertex back into
    the piece's own frame.  A forward rotation here would test a rectangle
    2*yaw off the one it names, which is invisible on an axis-aligned piece.
    """
    f = facts()[p.prefab]
    (lo, hi) = (p.aabb())
    a = math.radians(p.yaw)
    ca, sa = math.cos(a), math.sin(a)
    out: list[tuple[float, float]] = []
    for vx in range(int(math.floor(lo[0])), int(math.ceil(hi[0])) + 1):
        for vz in range(int(math.floor(lo[2])), int(math.ceil(hi[2])) + 1):
            dx, dz = vx - p.x, vz - p.z
            px = dx * ca - dz * sa
            pz = dx * sa + dz * ca
            if (f.lo[0] - 1e-9 <= px <= f.hi[0] + 1e-9
                    and f.lo[2] - 1e-9 <= pz <= f.hi[2] + 1e-9):
                out.append((float(vx), float(vz)))
    if not out:
        out = [(float(round(p.x)), float(round(p.z)))]
    return out


def bed_under(p: Placed, bed) -> float:
    """The LOWEST applied height at any lattice vertex under this piece.

    LOWEST, because "buried" is the claim that EVERY point of the solid is
    under the surface: if the piece's own top is below even the lowest ground
    it stands over, nobody can see any part of it.  The same reduction the
    gate's `buried` check makes, so the two cannot disagree by construction.

    `bed` is sampled AT the vertex, where a bilinear sampler's three other
    corners carry weight exactly zero and the reading is the vertex's own
    height - so this needs no second ground instrument.
    """
    if _SEATING_BED_AT_THE_PIVOT:
        # THE SHIPPED READING, restored: one bilinear sample at the pivot -
        # a height `Heightmap.GetHeight` never returns to anybody.
        return bed(p.x, p.z)
    return min(bed(vx, vz) for (vx, vz) in footprint_vertices(p))


def seating_arm(p: Placed) -> str:
    """WHICH ARM JUDGES THIS PIECE - `"deck"`, `"pile"` or `"structure"`.

    THE ROLE DECIDES THE ARM, NEVER THE POPULATION.  See
    `SEATING_POPULATION_DEPENDS_ON`: every piece is judged, and this only
    picks which of the three questions it is judged by.  Anything the two
    named roles do not claim - a rail, a rail post, a bent cap - is a
    STRUCTURE piece, and a structure piece wholly under the bed is invisible
    exactly as a pole is.
    """
    if p.role.endswith("/deck") or p.role.endswith("/head"):
        return "deck"
    if p.role.endswith("/pile"):
        return "pile"
    return "structure"


def check_seating(pieces: list[Placed], bed) -> dict:
    """Do the piles reach the applied bed, and is the travelled way clear of it?

    THE CHECK THAT WOULD HAVE CAUGHT THE WHOLE OVER-WATER HALF OF THE BURIED
    CLASS.  A pile stack is solved ONCE, against whatever bed the caller
    sampled, and a deck is emitted at a FIXED DATUM and never asked about
    again.  Neither is re-read after a later write moves the ground.  So the
    same two questions the world acceptance gate asks afterwards - is this
    piece under the surface, does this column reach it - are asked HERE,
    against the applied surface, before anything is emitted.

    THREE ARMS, ONE POPULATION, AND THE POPULATION IS EVERY PIECE.  A deck
    arriving at a bank with any cross-slope puts its OUTER tile row into the
    bank: that is the deck KEYING IN, which is what `bridge`'s overhang
    comment describes as putting "the end of the deck on real ground".
    MEASURED on N1-x04 and N4-x01, the outer row at the abutment is buried
    0.610-2.703 m while the centre row stands clear at H.  So DECK burial
    REFUSES only on the travelled way (|lateral| <= TRAVELLED_WAY_HALF_M) and
    the edge burials are COUNTED AND REPORTED, never silently dropped.  A
    PILE and a STRUCTURE piece have no travelled way and no keying-in to do,
    so for them the question is the gate's own: is the piece's own top under
    the LOWEST ground it stands over.

    THE ROLE PICKS THE ARM, IT DOES NOT PICK THE POPULATION - see
    `SEATING_POPULATION_DEPENDS_ON`, and see `seating_arm`, which has no
    fall-through.  A piece no arm claims would be reported in
    `pieces_unjudged`, and that number is the one a reader checks first.

    `lateral` is read off the piece, where the generator recorded it, rather
    than recovered from span geometry the caller may not have: that is what
    lets this run for a jetty, a slip head and a boardwalk span alike.

    Returns the measurement.  `refuse_on_seating` is the half that raises.
    """
    bed = require_applied_bed(bed, "check_seating")
    columns: dict[tuple[float, float], list[Placed]] = {}
    for p in pieces:
        if seating_arm(p) == "pile":
            columns.setdefault((round(p.x, 3), round(p.z, 3)), []).append(p)
    unfounded, embeds = [], []
    for (x, z), col in sorted(columns.items()):
        f = facts()[col[0].prefab]
        foot = min(pc.y + f.lo[1] for pc in col)
        # THE EMBED IS THE ONE READING THAT WANTS THE PIVOT AND NOT THE
        # LOWEST VERTEX.  It asks whether this column's foot overlaps the
        # terrain layer where the column stands; the lowest vertex under a
        # 0.3 m pole on a slope is a different place, and reading the embed
        # there would call a founded column unfounded.
        ground = bed(x, z)
        embed = ground - foot
        embeds.append(embed)
        if embed < PILE_EMBED_MIN_M:
            unfounded.append(dict(xz=[round(x, 2), round(z, 2)],
                                  foot_y=round(foot, 3),
                                  applied_ground_y=round(ground, 3),
                                  embed_m=round(embed, 3),
                                  poles=len(col)))
    # A PIECE THAT IS ITSELF WHOLLY UNDER THE BED.  A column counted against a
    # bed that later ROSE keeps its top under the new one - the `buried`
    # refusal - and the embed test above cannot see it, because a column
    # driven far too deep has a LARGE embed.  The two failures are opposite
    # ends of the same measurement and both have to be asked.
    sunk, structure_sunk, unjudged = [], [], []
    buried_way, buried_edge, buried_overhang = [], [], []
    for p in pieces:
        if _SEATING_POPULATION_JUDGES_NOTHING:
            # THE DEGENERATE OVER-CORRECTION, restored: a population so
            # narrowly keyed it names no obligation at all, which reads as
            # "nothing to refuse" and is the quietest possible failure.
            break
        arm = seating_arm(p)
        if arm == "structure" and _SEATING_POPULATION_BY_ROLE_SUFFIX:
            # THE SHIPPED DROP, restored VERBATIM: no arm, no record, no
            # refusal - not even a count.  `/rail_post` and `/rail` left
            # `bridge` certified this way.
            continue
        f = facts()[p.prefab]
        top = p.y + f.hi[1]
        if arm in ("pile", "structure"):
            ground = bed_under(p, bed)
            depth = ground - top
            # Same tolerance the acceptance gate's `buried` check uses
            # (accept/sections/structures.py::BURIED_TOL_M), so a piece this
            # emitter passes is not one the gate then refuses, and a piece
            # the gate would refuse cannot be emitted.
            if depth > PIECE_TOP_BURIED_TOL_M:
                rec = dict(xz=[round(p.x, 2), round(p.z, 2)],
                           top_y=round(top, 3),
                           applied_ground_y=round(ground, 3),
                           depth_m=round(depth, 3), role=p.role,
                           prefab=p.prefab)
                (sunk if arm == "pile" else structure_sunk).append(rec)
            continue
        if arm != "deck":
            unjudged.append(dict(xz=[round(p.x, 2), round(p.z, 2)],
                                 role=p.role, prefab=p.prefab))
            continue
        ground = bed_under(p, bed)
        depth = ground - top
        if depth <= DECK_BURIAL_MAX_M:
            continue
        rec = dict(xz=[round(p.x, 2), round(p.z, 2)],
                   walking_surface_y=round(top, 3),
                   applied_ground_y=round(ground, 3),
                   depth_m=round(depth, 3), lateral_m=round(p.lateral, 2))
        if p.overhang:
            buried_overhang.append(rec)
        elif abs(p.lateral) <= TRAVELLED_WAY_HALF_M:
            buried_way.append(rec)
        else:
            buried_edge.append(rec)
    for lst in (sunk, structure_sunk, buried_way, buried_edge,
                buried_overhang):
        lst.sort(key=lambda r: -r["depth_m"])
    n_deck = sum(1 for p in pieces if seating_arm(p) == "deck")
    n_struct = sum(1 for p in pieces if seating_arm(p) == "structure")
    return dict(
        pile_columns=len(columns),
        pile_embed_min_m=round(min(embeds), 3) if embeds else None,
        pile_embed_max_m=round(max(embeds), 3) if embeds else None,
        unfounded_columns=unfounded,
        piles_under_the_bed=sunk,
        # EVERY OTHER PIECE THIS ASSEMBLY EMITS - a rail, a rail post, a bent
        # cap - asked the same question.  This list was the hole: four
        # buried pieces left `bridge` certified because two of them wore a
        # role no arm claimed.  See `SEATING_POPULATION_DEPENDS_ON`.
        structure_under_the_bed=structure_sunk,
        # A ROLE NO ARM CLAIMS.  Empty by construction while `seating_arm`
        # has no fall-through, and reported anyway: this is the number that
        # would have named the defect the day it was introduced.
        pieces_unjudged=unjudged,
        travelled_way_buried=buried_way,
        edge_tiles_keyed_into_bank=len(buried_edge),
        worst_edge_tile=buried_edge[0] if buried_edge else None,
        # REPORTED, NEVER REFUSED, AND NEVER DROPPED HERE.  A swallowed
        # overhang station is the caller's decision: the road that levelled
        # the abutment is the only party that knows whether the bench now
        # stands at H, which is why `boardwalk.trim_buried_overhang` solves
        # it per end and writes down every station it drops.
        overhang_tiles_swallowed_by_the_bank=len(buried_overhang),
        worst_overhang_tile=buried_overhang[0] if buried_overhang else None,
        bed=dict(kind=bed.kind, note=bed.note),
        limits=dict(pile_embed_min_m=PILE_EMBED_MIN_M,
                    deck_burial_max_m=DECK_BURIAL_MAX_M,
                    travelled_way_half_m=TRAVELLED_WAY_HALF_M,
                    piece_top_buried_tol_m=PIECE_TOP_BURIED_TOL_M),
        depends_on=SEATING_POPULATION_DEPENDS_ON,
        population=(f"ALL {len(pieces)} placed piece(s) of this assembly - "
                    f"{len(columns)} pile column(s) of "
                    f"{sum(1 for p in pieces if seating_arm(p) == 'pile')} "
                    f"pole(s), {n_deck} deck tile(s) and {n_struct} other "
                    f"piece(s) - against the APPLIED surface at the 1 m "
                    f"lattice vertices under each piece's own footprint"))


def refuse_on_seating(pieces: list[Placed], bed, name: str) -> dict:
    """`check_seating`, raising on anything a player would meet as a defect.

    Called by every generator in this module BEFORE it returns, so a caller
    cannot forget it and no emitter downstream has to remember.  Nothing is
    written, nothing is dropped: a wrong assembly does not become a smaller
    wrong assembly, it becomes a refusal naming the pieces and the metres.
    """
    m = check_seating(pieces, bed)
    bad = []
    for r in m["piles_under_the_bed"][:8]:
        bad.append(f"pile at {r['xz']} has its TOP {r['depth_m']:.3f} m under "
                   f"applied ground {r['applied_ground_y']:.3f} m - an "
                   f"invisible pole, not a foundation")
    for r in m["structure_under_the_bed"][:8]:
        bad.append(f"{r['prefab']} ({r['role']}) at {r['xz']} has its TOP "
                   f"{r['depth_m']:.3f} m under applied ground "
                   f"{r['applied_ground_y']:.3f} m - a piece nobody can see "
                   f"or use, and no arm of this check used to ask")
    for r in m["pieces_unjudged"][:8]:
        bad.append(f"{r['prefab']} ({r['role']}) at {r['xz']} was JUDGED BY "
                   f"NO ARM of this check - `seating_arm` must claim every "
                   f"role this module emits, or the population is a subset "
                   f"of the obligation and the silence reads as a pass")
    for r in m["unfounded_columns"][:8]:
        bad.append(f"pile column at {r['xz']} has its foot at "
                   f"{r['foot_y']:.3f} m against applied ground "
                   f"{r['applied_ground_y']:.3f} m - overlap "
                   f"{r['embed_m']:+.3f} m, under the {PILE_EMBED_MIN_M} m "
                   f"needed to pin it to maxSupport")
    for r in m["travelled_way_buried"][:8]:
        bad.append(f"TRAVELLED-WAY deck tile at {r['xz']} has its walking "
                   f"surface {r['depth_m']:.3f} m BELOW applied ground "
                   f"{r['applied_ground_y']:.3f} m - a buried centre tile is "
                   f"a hole in the road")
    if bad:
        raise SystemExit(
            f"{name}: REFUSED by the seating re-test against the APPLIED "
            f"surface ({m['bed']['note']}). "
            + f"{len(m['piles_under_the_bed'])} pile(s) under the bed, "
            + f"{len(m['structure_under_the_bed'])} other piece(s) under it, "
            + f"{len(m['pieces_unjudged'])} piece(s) no arm judged, "
            + f"{len(m['unfounded_columns'])} unfounded column(s), "
            + f"{len(m['travelled_way_buried'])} buried travelled-way tile(s); "
            + f"{m['edge_tiles_keyed_into_bank']} edge tile(s) keyed into a "
            + f"bank and {m['overhang_tiles_swallowed_by_the_bank']} overhang "
            + "tile(s) swallowed by one are NOT defects and are not counted "
            + "here.\n  "
            + "\n  ".join(bad))
    return m


def colocated_pieces(pieces: list[Placed]) -> dict:
    """The acceptance gate's OWN duplicate census, asked of an emitter's list.

    `structures.check_duplicates` is called, not imitated: the same cube
    (`DUP_TOL_M`, Chebyshev, all three axes), the same occupied-space grading
    (SAME SOLID at 1 cm, BURIED COPY at `DUP_BURIED_FRACTION` of the piece's
    own interior), over the same measured parts (`geometry()`), with each
    piece's yaw as the quaternion the engine will build from `rot=yaw,0,0`.
    A pair that SHARES A PIVOT and fills space nothing else fills is the
    gate's advisory, not a duplicate, and is counted apart.

    `groups` names what the counts cannot: which roles, how far apart, where,
    so a refusal can be acted on without opening the gate's finding."""
    ST = _gate()
    tol = ST.DUP_TOL_M
    geom = geometry()
    solids = [ST.I.mk_solid(p.prefab, (p.x, p.y, p.z), ST.I.yaw(p.yaw),
                            geom.get(p.prefab)) for p in pieces]
    _findings, counts = ST.check_duplicates(solids, geom, tol=tol)
    cells: dict[tuple, list[int]] = {}
    for i, p in enumerate(pieces):
        cells.setdefault((p.prefab, int(math.floor(p.x / tol)),
                          int(math.floor(p.y / tol)),
                          int(math.floor(p.z / tol))), []).append(i)
    parent = list(range(len(pieces)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (nm, cx, cy, cz), members in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cells.get((nm, cx + dx, cy + dy, cz + dz), ()):
                        for i in members:
                            a, b = pieces[i], pieces[j]
                            if i != j and (abs(a.x - b.x) <= tol
                                           and abs(a.y - b.y) <= tol
                                           and abs(a.z - b.z) <= tol):
                                parent[find(i)] = find(j)
    grouped: dict[int, list[int]] = {}
    for i in range(len(pieces)):
        grouped.setdefault(find(i), []).append(i)
    groups = []
    for g in grouped.values():
        if len(g) < 2:
            continue
        a = pieces[g[0]]
        groups.append(dict(
            prefab=a.prefab, n=len(g),
            roles=sorted({pieces[i].role for i in g}),
            at=[round(a.x, 3), round(a.y, 3), round(a.z, 3)],
            apart_m=round(max(math.dist((a.x, a.y, a.z),
                                        (pieces[i].x, pieces[i].y,
                                         pieces[i].z)) for i in g[1:]), 4)))
    groups.sort(key=lambda r: (r["at"][0], r["at"][2], r["prefab"]))
    return dict(counts=counts, groups=groups, cube_tol_m=tol)


def refuse_on_colocated(pieces: list[Placed], name: str) -> dict:
    """`colocated_pieces`, raising on anything the gate would refuse.

    Called by every generator in this module BEFORE it returns, next to
    `refuse_on_seating`.  Nothing is dropped and nothing is moved: a
    duplicate that is not literally the same solid is a lattice that put two
    rows in one place, and quietly deleting one would hide the arithmetic
    that did it - and the next span with the same arithmetic.  The refusal
    carries the metres.  What it counts is the gate's own `same_solid`,
    `buried_copy` and `unmeasured`; `shared_pivot_not_a_duplicate` is
    architecture (two half-arches on one pivot) and is never counted here.
    """
    if _COLOCATED_REFUSAL_OFF:
        return {}
    m = colocated_pieces(pieces)
    n = m["counts"]
    bad = n["same_solid"] + n["buried_copy"] + n["unmeasured"]
    if not bad:
        return m
    raise SystemExit(
        f"{name}: REFUSED by the co-location re-test: {bad} piece(s) stand "
        f"within {m['cube_tol_m']:.2f} m on all three axes of a sibling of "
        f"the same prefab - {n['same_solid']} filling identical space, "
        f"{n['buried_copy']} buried inside the sibling, {n['unmeasured']} with "
        f"no measured solid - which is what the acceptance gate refuses as "
        f"`duplicates`.  Nothing was dropped.\n  "
        + "\n  ".join(
            f"{g['n']} x {g['prefab']} {g['roles']} at {g['at']}, "
            f"{g['apart_m']:.4f} m apart" for g in m["groups"][:8]))


def jetty(root: tuple[float, float], bearing_deg: float, length_m: float,
          deck_y: float, bed_profile, width_tiles: int = 2,
          bent_spacing_m: float = 4.0, name: str = "jetty") -> list[Placed]:
    """A pier running out from the shore over water on piles.

    This is the structure the operator called a dock. It is deliberately NOT a
    flattened pad with a building on it: the pad is the `early-dock` defect,
    where the flatten removed the water from under the footprint and the pier
    then read as floating. Every station here stands on piles driven into the
    bed and there is no terrain write anywhere in its footprint.

    `bed_profile` MUST be `Bed.applied(...)`; every deck tile is re-tested
    against it before this function returns.
    """
    bed = require_applied_bed(bed_profile, f"jetty({name})")
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
            # Station 0 sits AT the root, and `plan.aim_seaward` snaps the
            # root to a genuine shoreline sample - LAND, h > 30.5 - on
            # purpose, so the landward end keys into the bank instead of
            # starting in the water. A 30.6 m deck over a 31 m bank is that
            # keying, not a hole in a pier, so it carries the same
            # `overhang` mark a bridge's beyond-the-bank station does.
            out.append(Placed("wood_floor", cx + lx * off, floor_y, cz + lz * off,
                              bearing_deg, f"{name}/deck", lateral=off,
                              overhang=(s == 0)))
        if s > 0 and (s * 2.0) % bent_spacing_m == 0:
            # ONE COLUMN PER DISTINCT LATERAL.  A one-tile pier has
            # `half_w - 1.0 == 0`: both "sides" are its own centreline, and
            # the loop over (-1, 1) stood the SAME column twice - 24
            # same-solid `wood_pole_log_4` in a 24 m slip's two fingers,
            # MEASURED by `colocated_pieces` on `slip()` at 9 - 12 m inner
            # width.  Order and positions for every wider pier are unchanged.
            col = half_w - 1.0
            for lat in ((0.0,) if col < 1e-9
                        and not _JETTY_STANDS_A_ONE_TILE_COLUMN_TWICE
                        else (-col, col)):
                px, pz = cx + lx * lat, cz + lz * lat
                out.extend(_pile_stack(px, pz, deck_y, bed, f"{name}/pile{s}",
                                       lateral=lat))
        # a single-sided rail, on the side away from the berth, so a boat can be
        # boarded from the deck. Handrail on the outboard side only.
        rx = cx + lx * (half_w - 0.2)
        rz = cz + lz * (half_w - 0.2)
        out.append(Placed("wood_pole", rx, deck_y + 0.5, rz, bearing_deg,
                          f"{name}/rail_post"))
    refuse_on_seating(out, bed, name)
    refuse_on_colocated(out, name)
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

    `bed_profile` MUST be `Bed.applied(...)`. The head walkway's tiles carry
    `lateral=0`: the head runs ACROSS the slip, so its own centreline is the
    walkway, and every tile of it is travelled way.
    """
    bed = require_applied_bed(bed_profile, f"slip({name})")
    ux, uz = _unit(bearing_deg)
    lx, lz = uz, -ux
    half = inner_width_m / 2.0
    out: list[Placed] = []
    for side in (-1, 1):
        rx = root[0] + lx * side * (half + 1.0)
        rz = root[1] + lz * side * (half + 1.0)
        out.extend(jetty((rx, rz), bearing_deg, length_m, deck_y, bed,
                         width_tiles=1, bent_spacing_m=4.0,
                         name=f"{name}/finger{'L' if side < 0 else 'R'}"))
    # head walkway across the closed end, joining the two fingers
    f_floor = facts()["wood_floor"]
    floor_y = deck_y - f_floor.hi[1]
    n = int(round(inner_width_m / 2.0)) + 1
    # A head tile that lands on a finger's own root tile is that tile twice:
    # at 11 m inner width the 7th of 7 sits at +6.5 m, exactly where finger R
    # starts, and the finger's tile already covers it.  Skipped where it is
    # closer than the shortest bay the bridge lattice will lay
    # (`min_bay_m`), so the head still reaches both fingers and no row
    # stands within the gate's cube of another.
    roots = (-(half + 1.0), half + 1.0)
    for k in range(n):
        off = -half + k * 2.0
        if not _SLIP_HEAD_TILE_ON_A_FINGER_ROOT and any(
                abs(off - r) < min_bay_m() for r in roots):
            continue
        out.append(Placed("wood_floor", root[0] + lx * off, floor_y,
                          root[1] + lz * off, bearing_deg, f"{name}/head",
                          lateral=0.0))
    refuse_on_seating(out, bed, name)
    refuse_on_colocated(out, name)
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


# ---------------------------------------------------------------------------
# THE PROOF.  A check that has never been shown to refuse is decoration, and
# this project has paid for that four times: "WALKABLE, worst 8 m gradient
# 0.0801" over 84 impassable locations, "349/349 at tolerance 0" over 480
# inverted roofs.  So every case below names a state and the direction it is
# REQUIRED to answer in, and the KNOWN-BAD ones run first.
#
# No world, no server, no save: the whole of Class B is a question about a
# number the emitter already holds, which is exactly why it could have been
# caught before a single `spawn_object` was sent.
# ---------------------------------------------------------------------------

# The measured shape of the defect, from the world it was found in.
# `boathouse-temple-strait` was emitted over a bed sampled BEFORE the temple
# strait was written, and the gate later read 33 of its 104 pieces
# COMPLETELY under the applied surface, worst `wood_floor` 2.870 m.
# The fixture below is that history in four numbers: a bed at 26.0 that a
# later write raised to 33.0, under a deck pinned to the 30.6 datum.
SEED_BED_Y = 26.0
RAISED_BED_Y = 33.0


def _flat(y: float):
    return lambda _x, _z: y


def _case_pile_stack_against_a_seed_bed_is_refused() -> tuple[bool, str]:
    """THE KNOWN-BAD PILE STACK.  Solve a jetty's piles against the seed bed
    at 26.0, then measure the SAME pieces against the applied surface a later
    write left at 33.0.  Every pole is under the bed and the deck with it.
    `check_seating` must refuse."""
    seed = Bed.generated(_flat(SEED_BED_Y), "the seed bed, before the strait "
                                            "was written")
    applied = Bed.applied(_flat(RAISED_BED_Y),
                          "the applied surface after the write that raised "
                          "this bed 7.0 m")
    # Solved the OLD way: piles driven to whatever the caller sampled.
    pieces = _pile_stack(0.0, 0.0, DECK_Y,
                         Bed.applied(seed.fn, "seed heights, MISLABELLED "
                                              "applied to build the fixture"),
                         "known-bad")
    m = check_seating(pieces, applied)
    if not m["piles_under_the_bed"]:
        return False, ("check_seating passed a pile stack solved against a "
                       f"bed {RAISED_BED_Y - SEED_BED_Y:.1f} m below the "
                       "applied surface: THE CHECK IS NOT A CHECK")
    worst = m["piles_under_the_bed"][0]["depth_m"]
    return True, (f"{len(m['piles_under_the_bed'])} of {len(pieces)} pole(s) "
                  f"refused, worst top {worst:.3f} m under applied ground "
                  f"{RAISED_BED_Y:.1f} m")


def _case_seed_bed_cannot_reach_a_generator() -> tuple[bool, str]:
    """A `Bed.generated` handed to a generator must be REFUSED, not used.
    This is the one that makes the defect unspellable rather than merely
    detectable."""
    seed = Bed.generated(_flat(SEED_BED_Y), "crossings/plan.py::profiles, the "
                                            "raw PatchScan field")
    try:
        jetty((0.0, 0.0), 90.0, 20.0, DECK_Y, seed, name="known-bad")
    except SystemExit as e:
        if "REFUSED a 'generated' bed" not in str(e):
            return False, f"refused, but for the wrong reason: {e}"
        return True, "jetty() refused a generated bed by name"
    return False, ("jetty() ACCEPTED a generated bed -- the two seed-bed "
                   "implementations would still be writable")


def _case_bare_callable_is_refused() -> tuple[bool, str]:
    """The exact shape both defective implementations had: a lambda whose
    provenance nobody can ask about."""
    try:
        jetty((0.0, 0.0), 90.0, 20.0, DECK_Y, _flat(SEED_BED_Y), name="known-bad")
    except SystemExit as e:
        if "bare function" not in str(e):
            return False, f"refused, but for the wrong reason: {e}"
        return True, "jetty() refused a bare callable"
    return False, "jetty() ACCEPTED a bare callable bed profile"


def _case_buried_travelled_way_is_refused() -> tuple[bool, str]:
    """A deck at a FIXED DATUM under ground that came up through it. Scoped to
    the travelled way, so this is the tile a player falls into."""
    applied = Bed.applied(_flat(RAISED_BED_Y), "applied surface, raised")
    deck = [Placed("wood_floor", 0.0, DECK_Y - facts()["wood_floor"].hi[1],
                   0.0, 0.0, "known-bad/deck", lateral=0.0)]
    m = check_seating(deck, applied)
    if not m["travelled_way_buried"]:
        return False, "a centre deck tile 2.4 m under the ground passed"
    return True, (f"centre tile refused at "
                  f"{m['travelled_way_buried'][0]['depth_m']:.3f} m under")


def _case_keyed_in_edge_tile_is_not_refused() -> tuple[bool, str]:
    """THE OTHER DIRECTION, and the one that keeps this check usable. An OUTER
    tile row buried by the bank it keys into is a correct abutment: MEASURED
    0.610-2.703 m on N1-x04 and N4-x01 while the centre row stood clear. A
    guard that refuses it refuses every correctly keyed bridge."""
    applied = Bed.applied(_flat(RAISED_BED_Y), "applied surface, raised")
    f = facts()["wood_floor"]
    edge = [Placed("wood_floor", 0.0, DECK_Y - f.hi[1], 0.0, 0.0,
                   "known-bad/deck", lateral=2.0)]
    m = check_seating(edge, applied)
    if m["travelled_way_buried"]:
        return False, "an edge tile keyed into the bank was refused"
    if m["edge_tiles_keyed_into_bank"] != 1:
        return False, ("the edge tile was neither refused NOR counted -- a "
                       "silent drop is the thing this project keeps paying "
                       "for")
    return True, "edge tile counted and reported, not refused"


def _case_overhang_is_reported_not_refused() -> tuple[bool, str]:
    """A bridge's beyond-the-bank station swallowed by the bench the road
    levelled. The caller trims it (`boardwalk.trim_buried_overhang`); the
    generator must not refuse the whole crossing for it."""
    applied = Bed.applied(_flat(RAISED_BED_Y), "applied surface, raised")
    f = facts()["wood_floor"]
    tile = [Placed("wood_floor", 0.0, DECK_Y - f.hi[1], 0.0, 0.0,
                   "known-bad/deck", lateral=0.0, overhang=True)]
    m = check_seating(tile, applied)
    if m["travelled_way_buried"]:
        return False, "a deliberate overhang station was refused"
    if m["overhang_tiles_swallowed_by_the_bank"] != 1:
        return False, "the overhang tile was not counted"
    return True, "overhang tile counted and reported, not refused"


def _case_applied_bed_passes() -> tuple[bool, str]:
    """THE OTHER DIRECTION for the whole fix: solved against the applied
    surface, the same jetty passes every part of the re-test."""
    applied = Bed.applied(_flat(SEED_BED_Y),
                          "applied surface = seed, nothing written here yet")
    pieces = jetty((0.0, 0.0), 90.0, 20.0, DECK_Y, applied, name="good")
    m = check_seating(pieces, applied)
    if m["piles_under_the_bed"] or m["unfounded_columns"] \
            or m["travelled_way_buried"]:
        return False, (f"a correctly solved jetty was refused: {m['unfounded_columns'][:2]} "
                       f"{m['piles_under_the_bed'][:2]} "
                       f"{m['travelled_way_buried'][:2]}")
    return True, (f"{len(pieces)} pieces, {m['pile_columns']} column(s), embed "
                  f"{m['pile_embed_min_m']}..{m['pile_embed_max_m']} m")


def _old_pile_stack_rounded_up(deck_y: float, ground_y: float,
                               embed: float = 1.0) -> list[Placed]:
    """THE ARITHMETIC `_pile_stack` REPLACED, kept ONLY as the known-bad
    fixture the case below neuters against.  Verbatim: round the column up to
    whole 4 m poles measured from `ground - embed`, then stack every one of
    them on a rigid pitch downwards from the deck."""
    f = facts()["wood_pole_log_4"]
    height = f.hi[1] - f.lo[1]
    top_target = deck_y - 0.13
    n = max(1, math.ceil((top_target - (ground_y - embed)) / height))
    return [Placed("wood_pole_log_4", 0.0,
                   (top_target - k * height) - f.hi[1], 0.0, 0.0,
                   "known-bad/pile") for k in range(n)]


def _case_pile_stack_quantisation_never_buries_its_own_foot() -> tuple[bool, str]:
    """THE CHECK THAT WOULD HAVE CAUGHT 165.8 m OF UNBUILDABLE DECK.

    A column is solved against a FLAT applied bed at every deck-to-bed
    distance from 0 to 16 m in 1 cm steps -- 1601 columns, the whole domain a
    4 m pole quantises over, four times -- and EVERY ONE of them must come
    back with no pole under the bed AND a founded foot.  The defect this
    neuters is not a wrong bed: the bed is right, flat and applied.  It is
    the generator rounding its own column UP to a whole pole from
    `ground - embed` and then stacking on a rigid pitch from the deck, which
    puts the lowest pole's top `(4 - (D mod 4))` metres UNDER the bed for
    every D whose remainder exceeds `embed`.  `check_seating` then refuses it
    as an invisible pole and takes the span with it.

    Both directions, because a sweep that only passes proves nothing: the
    SAME sweep is run through the arithmetic this replaced, and that one is
    REQUIRED to bury poles.  If it ever stops, the case has stopped being a
    case.
    """
    lo_m, hi_m, step_m = 0.0, 16.0, 0.01
    n_pts = int(round((hi_m - lo_m) / step_m)) + 1
    sunk_new, unfounded_new = [], []
    sunk_old = 0
    worst_old = 0.0
    for i in range(n_pts):
        d = lo_m + i * step_m
        ground = DECK_Y - 0.13 - d
        applied = Bed.applied(_flat(ground),
                              f"flat applied bed {d:.2f} m under the deck "
                              f"underside")
        col = _pile_stack(0.0, 0.0, DECK_Y, applied, "sweep")
        m = check_seating(col, applied)
        if m["piles_under_the_bed"]:
            sunk_new.append((round(d, 2),
                             m["piles_under_the_bed"][0]["depth_m"]))
        if m["unfounded_columns"]:
            unfounded_new.append((round(d, 2),
                                  m["unfounded_columns"][0]["embed_m"]))
        om = check_seating(_old_pile_stack_rounded_up(DECK_Y, ground), applied)
        if om["piles_under_the_bed"]:
            sunk_old += 1
            worst_old = max(worst_old, om["piles_under_the_bed"][0]["depth_m"])
    if not sunk_old:
        return False, (f"THE NEUTER DID NOT FLIP: the arithmetic this "
                       f"replaced buried no pole over {n_pts} column(s), so "
                       f"this case proves nothing about the new one")
    if sunk_new or unfounded_new:
        return False, (f"{len(sunk_new)} of {n_pts} column(s) still bury "
                       f"their own foot (first {sunk_new[:3]}), "
                       f"{len(unfounded_new)} unfounded (first "
                       f"{unfounded_new[:3]})")
    return True, (f"0 of {n_pts} column(s) bury a pole or lose their footing "
                  f"over 0..{hi_m:g} m at {step_m:g} m; the arithmetic this "
                  f"replaced buries one in {sunk_old} of the same {n_pts}, "
                  f"worst {worst_old:.3f} m under the bed")


BANK_SHELF_Y = DECK_Y - 0.05          # a benched abutment shelf, 0.05 m
                                      # proud of the deck's own underside


def _shelved_channel_bed(length_m: float, shelf_m: float = 12.0):
    """A channel benched to the deck at both ends and deep in the middle --
    the shape EVERY road crossing has once its ribbon has levelled the
    abutments, and the shape that refused W5-x01/W10-x01."""
    def f(x: float, _z: float) -> float:
        return (BANK_SHELF_Y if (x < shelf_m or x > length_m - shelf_m)
                else SEED_BED_Y)
    return f


def _case_bent_on_ground_at_deck_level_is_omitted() -> tuple[bool, str]:
    """THE CHECK THAT WOULD HAVE CAUGHT 89.44 m OF DECK REFUSED FOR TWO POLES.

    A bent leg whose own applied ground has already reached the deck's
    underside cannot be built: every pole in it is under the bed by
    construction, because the ground is ABOVE the thing the column holds up.
    No column length fixes that, so `bridge` must not emit the leg -- and it
    must not need to, because the SAME inequality puts the outer deck tile at
    that xz in contact with the terrain layer.

    THE NEUTER IS THE RULE SWITCHED OFF, NOT THE GEOMETRY CHANGED: the same
    span, the same bed, `bents_on_ground_at_deck_level=True`, MUST be refused
    by the seating re-test. If it ever stops being refused, the rule is not
    load-bearing and this case is decoration.
    """
    length = 60.0
    # 4 m bents: the fixture is about WHICH legs are emitted, so its spacing
    # is the one the real driver tightens to, never the widest.  At 8 m this
    # 60 m channel is genuinely under-supported mid-span and the case would
    # be reporting its own geometry rather than the rule.
    spacing = 4.0
    bed = Bed.applied(_shelved_channel_bed(length),
                      "a channel benched to the deck at both ends")
    try:
        legacy = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed,
                        name="known-bad", bent_spacing_m=spacing,
                        bents_on_ground_at_deck_level=True)
    except SystemExit as e:
        if "invisible pole" not in str(e):
            return False, f"the neuter refused for the wrong reason: {e}"
        legacy = None
    if legacy is not None:
        return False, ("THE NEUTER DID NOT FLIP: emitting every bent leg "
                       "unconditionally over a benched abutment produced a "
                       f"clean {len(legacy)}-piece span, so omitting them "
                       "proves nothing")
    pieces = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed,
                    name="good", bent_spacing_m=spacing)
    m = check_seating(pieces, bed)
    if (m["piles_under_the_bed"] or m["structure_under_the_bed"]
            or m["unfounded_columns"]):
        return False, f"unusable support geometry: {m}"
    v = verify(pieces, bed, note="bent-omission case")
    if not v["ok"]:
        return False, f"the terrain-supported span fails support: {v['failures'][:2]}"
    return True, (f"{len(pieces)} pieces remain visible, founded and supported; "
                  f"unconditional buried supports are REFUSED by the same test")


def _hillside_channel_bed(length_m: float, hill_z: float, hill_y: float,
                          shelf_m: float = 6.0):
    """A channel benched to the deck at both ends, with a hillside standing at
    `hill_y` on the -z side of the whole run: the shape of
    S4-meadhall-southeast-x01, a spur whose channel runs along a slope."""
    def f(x: float, z: float) -> float:
        if z <= hill_z:
            return hill_y
        return (BANK_SHELF_Y if (x < shelf_m or x > length_m - shelf_m)
                else SEED_BED_Y)
    return f


def _case_rail_swallowed_by_the_bank_is_omitted() -> tuple[bool, str]:
    """A RAIL THE BANK HAS SWALLOWED IS OMITTED, NOT REFUSED.

    With `shoulder_on_swallowed_ground=True`, the same span on the same bed
    MUST be refused by the seating re-test.  The rail on the OTHER side,
    over open channel, must survive in full: omitting every rail would also
    pass the re-test, and it would leave a deck with no railing.
    """
    length = 20.0
    bed = Bed.applied(_hillside_channel_bed(length, -2.5, DECK_Y + 1.5),
                      "a channel along a hillside 1.5 m above the deck")
    try:
        legacy = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed,
                        name="known-bad", bent_spacing_m=4.0,
                        shoulder_on_swallowed_ground=True)
    except SystemExit:
        legacy = None
    if legacy is not None:
        return False, ("THE NEUTER DID NOT FLIP: emitting every rail beside a "
                       f"hillside produced a clean {len(legacy)}-piece span, "
                       "so omitting rails proves nothing")
    rep: dict = {}
    pieces = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed, name="good",
                    bent_spacing_m=4.0, report=rep)
    gone = rep["shoulder_omitted_swallowed_by_the_bank"]
    if not gone:
        return False, "nothing was omitted, so the span that passes is not " \
                      "the span the neuter refused"
    hill = [d for d in gone if d["xz"][1] < 0.0]
    if len(hill) != len(gone):
        return False, (f"{len(gone) - len(hill)} piece(s) omitted over OPEN "
                       f"CHANNEL, where no bank stands above them")
    open_side = [p for p in pieces if p.role.endswith(("/rail_post", "/rail"))
                 and p.z > 0.0]
    stations = int(round(length / 2.0)) + 1 + 2
    if len(open_side) != 2 * stations - 1:
        return False, (f"the open side carries {len(open_side)} rail piece(s) "
                       f"against {2 * stations - 1} for {stations} stations")
    v = verify(pieces, bed, note="swallowed-rail case")
    if not v["ok"]:
        return False, f"the span fails support: {v['failures'][:2]}"
    return True, (f"{len(gone)} rail piece(s) under a hillside omitted "
                  f"(worst {max(d['under_by_m'] for d in gone):.3f} m under), "
                  f"{len(open_side)} on the open side kept, {len(pieces)} "
                  f"pieces verify ok; the same span with the rule off is "
                  f"REFUSED by the seating re-test")


def _case_shoulder_tile_swallowed_by_the_bank_is_omitted() -> tuple[bool, str]:
    """A SHOULDER TILE THE BANK HAS SWALLOWED IS OMITTED; A CARRIAGEWAY TILE
    NEVER IS.

    A 4-tile (8 m) deck over a 6 m carriageway beside a hillside 0.5 m above
    the deck: the hill swallows the -z shoulder row whole, while the rail
    above it still stands proud.  The seating re-test accepts an edge tile
    keyed into a bank, so the neuter's verdict is the burial question the
    gate asks (`structures/buried`): is any emitted piece's own top under
    the lowest lattice vertex beneath it?  With
    `shoulder_on_swallowed_ground=True` the answer MUST be yes.  With the
    rule on it must be no, and both carriageway rows must be whole.
    """
    length = 20.0
    bed = Bed.applied(_hillside_channel_bed(length, -1.5, DECK_Y + 0.5),
                      "a channel along a hillside 0.5 m above the deck")

    def swallowed(ps: list[Placed]) -> list[Placed]:
        return [p for p in ps
                if bed_under(p, bed) > p.y + facts()[p.prefab].hi[1]]

    legacy = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed,
                    name="known-bad", width_tiles=4, bent_spacing_m=4.0,
                    carriageway_half_m=3.0, shoulder_on_swallowed_ground=True)
    if not swallowed(legacy):
        return False, ("THE NEUTER DID NOT FLIP: every shoulder tile beside "
                       "a hillside stood proud, so omitting them proves "
                       "nothing")
    rep: dict = {}
    pieces = bridge((0.0, 0.0), (length, 0.0), 90.0, DECK_Y, bed, name="good",
                    width_tiles=4, bent_spacing_m=4.0, carriageway_half_m=3.0,
                    report=rep)
    still = swallowed(pieces)
    if still:
        return False, (f"{len(still)} emitted piece(s) still swallowed, first "
                       f"{still[0].role} at ({still[0].x:.1f},{still[0].z:.1f})")
    stations = int(round(length / 2.0)) + 1 + 2
    carriage = [p for p in pieces if p.role.endswith("/deck")
                and abs(p.lateral) + 1.0 <= 3.0 + 1e-9]
    if len(carriage) != 2 * stations:
        return False, (f"the carriageway carries {len(carriage)} tile(s) "
                       f"against {2 * stations} for {stations} stations")
    gone = rep["shoulder_omitted_swallowed_by_the_bank"]
    v = verify(pieces, bed, note="swallowed-shoulder case")
    if not v["ok"]:
        return False, f"the span fails support: {v['failures'][:2]}"
    return True, (f"{len(gone)} shoulder tile(s) under a hillside omitted "
                  f"(worst {max(d['under_by_m'] for d in gone):.3f} m under), "
                  f"{len(carriage)} carriageway tile(s) kept whole, "
                  f"{len(pieces)} pieces verify ok; the same span with the "
                  f"rule off emits {len(swallowed(legacy))} swallowed "
                  f"piece(s)")


# ---------------------------------------------------------------------------
# THE DECLARED DEPENDENCY, CHECKED IN EACH DIRECTION
# ---------------------------------------------------------------------------
# Over-keying is silent and EXPENSIVE; under-keying is silent and FALSE.  The
# obvious repair for one is the other, so `SEATING_POPULATION_DEPENDS_ON` is
# checked by a neuter in EACH DIRECTION and by an arm that must SURVIVE each
# one - so that "the neuter flipped" can never be satisfied by a check that
# simply stopped looking.
#
# THE SURVIVOR IS A VERDICT, NEVER AN ASSERTION.  `accept/registry.py`'s
# `run_cases` reads an AssertionError as a flip, so a survivor expressed as
# an assert lets a degenerate instrument be stamped PROVEN LOAD-BEARING.
# Every arm below answers with a BOOLEAN VERDICT and the driver compares
# them arm by arm.


def _bilinear_cell_bed(peak: float = 31.0, dip: float = 1.0):
    """An applied surface whose LATTICE VERTICES are all at `peak` and whose
    interior reads LOWER, the way a bilinear sampler reads it.

    `g(x, z) = peak - dip * frac(x) * frac(z)`.  Every integer vertex is
    exactly `peak` - which is what `Heightmap.GetHeight` returns anywhere in
    the cell - and the interior dips, which is what `Applied.surface_at`
    returns and what `_pile_stack` used to seat a pole on.  Built here in
    code from one formula: the fixture constructs the state it tests and
    borrows nothing from a save, a corpus or a placement.
    """
    def f(x: float, z: float) -> float:
        return peak - dip * (x - math.floor(x)) * (z - math.floor(z))
    return f


# The four states the declaration is checked on.  Each names the role it
# wears and the surface it is judged against, because those are the two keys.
_ARM_FLAT_BED_Y = 33.0
_ARM_PEAK_Y = 31.0


def seating_arms() -> list[dict]:
    """Five states, and the direction `check_seating` must answer in each.

    ROLE_STRUCTURE  a `/rail_post` wholly under a flat bed MUST be refused.
                    This is the arm the role-keyed population loses, and
                    losing it is losing 2 of the 4 pieces the gate refused -
                    including the worst, 0.380 m.
    ROLE_PILE       a `/pile` wholly under the SAME bed MUST be refused --
                    under the role neuter as well.  Without this arm a
                    population that judged nothing at all would score as
                    "the neuter flipped".
    BED_VERTEX      a piece seated EXACTLY on the BILINEAR reading at its own
                    pivot, inside a cell whose every vertex stands higher,
                    MUST be refused.  This is the arm the pivot sample loses,
                    and it is the state `_pile_stack` emitted three times on
                    W10-brgs1-treenear-x01.
    BED_PIVOT       a piece under the bed AT THE PIVOT TOO MUST be refused --
                    under the bed neuter as well, so that flip is a LOST
                    DEPENDENCY and not a blinded sampler.
    PROUD           a piece standing clear of every vertex under it MUST be
                    accepted, because a check that refuses everything is as
                    useless as one that refuses nothing.
    """
    flat = Bed.applied(_flat(_ARM_FLAT_BED_Y), "a flat applied surface")
    cell = Bed.applied(_bilinear_cell_bed(_ARM_PEAK_Y),
                       "one lattice cell, vertices at "
                       f"{_ARM_PEAK_Y:.1f} m, interior read BILINEARLY")
    fp = facts()["wood_pole"]
    # inside the cell, off the vertex: bilinear 30.84, every vertex 31.00
    px, pz = 0.4, 0.4
    interior = _ARM_PEAK_Y - 1.0 * px * pz

    def one(name: str, piece: Placed, bed, want_refuse: bool,
            why: str) -> dict:
        m = check_seating([piece], bed)
        refused = bool(m["piles_under_the_bed"] or m["structure_under_the_bed"]
                       or m["travelled_way_buried"] or m["pieces_unjudged"])
        return dict(name=name, role=piece.role, got=refused, want=want_refuse,
                    ok=refused == want_refuse, why=why,
                    depth_m=round(
                        (m["piles_under_the_bed"] + m["structure_under_the_bed"]
                         or [{"depth_m": 0.0}])[0]["depth_m"], 3))

    return [
        one("a_rail_post_under_the_bed_is_refused",
            Placed("wood_pole", 0.0, _ARM_FLAT_BED_Y - 1.0 - fp.hi[1], 0.0,
                   0.0, "arm/rail_post"), flat, True,
            "a role NO arm claimed, 1.000 m under a flat bed"),
        one("a_pile_under_the_bed_is_refused",
            Placed("wood_pole", 0.0, _ARM_FLAT_BED_Y - 1.0 - fp.hi[1], 0.0,
                   0.0, "arm/bent1/pile"), flat, True,
            "the SURVIVOR of the role neuter: same piece, a role the "
            "shipped population did claim"),
        one("a_piece_buried_only_at_the_governing_vertex_is_refused",
            Placed("wood_pole", px, interior - fp.hi[1], pz, 0.0,
                   "arm/bent1/pile"), cell, True,
            f"top seated EXACTLY on the bilinear reading {interior:.3f} m; "
            f"every vertex under it stands at {_ARM_PEAK_Y:.3f} m"),
        one("a_piece_buried_at_the_pivot_too_is_refused",
            Placed("wood_pole", px, _ARM_PEAK_Y - 1.0 - fp.hi[1], pz, 0.0,
                   "arm/bent1/pile"), cell, True,
            "the SURVIVOR of the bed neuter: 1.000 m under the bed by "
            "either reading"),
        one("a_piece_standing_proud_is_accepted",
            Placed("wood_pole", px, _ARM_PEAK_Y + 1.0 - fp.hi[1], pz, 0.0,
                   "arm/rail_post"), cell, False,
            "1.000 m PROUD of every vertex under it"),
    ]


def _flag(name: str):
    """Set a module flag True and hand back the undo."""
    g = globals()
    old = g[name]
    g[name] = True

    def undo():
        g[name] = old
    return undo


def _neuter_seating_population_to_role_suffixes():
    """RESTORE THE DEFECT: the burial population keyed on the role suffixes
    `/pile`, `/deck` and `/head`, every other role silently dropped.

    Drops exactly the pieces a role-keyed population cannot reach and nothing
    else, so the PILE arm that must SURVIVE is untouched and the flip cannot
    be passed by an empty answer."""
    return _flag("_SEATING_POPULATION_BY_ROLE_SUFFIX")


def _neuter_seating_population_to_nothing():
    """RESTORE THE DEGENERATE OVER-CORRECTION: judge no piece at all."""
    return _flag("_SEATING_POPULATION_JUDGES_NOTHING")


def _neuter_seating_bed_to_the_pivot_sample():
    """RESTORE THE DEFECT: one BILINEAR sample at the piece pivot instead of
    the lowest lattice vertex under its own footprint."""
    return _flag("_SEATING_BED_AT_THE_PIVOT")


def seating_population_report() -> dict:
    """Every arm, plus what each neuter does to it - THE VERDICT ITSELF.

    `ok` is false unless ALL of:
      every arm answers the direction it declares;
      the role neuter FLIPS at least one arm - so the population really does
        depend on judging a role no suffix claims;
      at least one arm SURVIVES the role neuter - so the flip is a LOST
        DEPENDENCY and not a population that emptied;
      the bed neuter FLIPS at least one arm;
      at least one arm SURVIVES the bed neuter;
      the degenerate neuter loses EVERY refusing arm - so a check that stops
        looking is distinguishable from one that looks in the right place.
    """
    real = seating_arms()

    def under(neuter) -> list[dict]:
        undo = neuter()
        try:
            return seating_arms()
        finally:
            undo()

    out = dict(arms=real, ok=True, why=[])
    for key, neuter, what in (
            ("role_neuter", _neuter_seating_population_to_role_suffixes,
             "the burial population keyed on the role suffixes"),
            ("bed_neuter", _neuter_seating_bed_to_the_pivot_sample,
             "one bilinear sample at the piece pivot"),
            ("empty_neuter", _neuter_seating_population_to_nothing,
             "a population that judges nothing")):
        got = under(neuter)
        flipped = [a["name"] for a, b in zip(real, got) if a["got"] != b["got"]]
        survived = [b["name"] for a, b in zip(real, got)
                    if a["want"] and b["got"]]
        out[key] = dict(restores=what, flipped=flipped, survived=survived)
    bad = [a["name"] for a in real if not a["ok"]]
    if bad:
        out["ok"] = False
        out["why"].append(f"{len(bad)} arm(s) answered the wrong way: {bad}")
    for key in ("role_neuter", "bed_neuter"):
        r = out[key]
        if not r["flipped"]:
            out["ok"] = False
            out["why"].append(
                f"THE {key.upper()} DID NOT FLIP: restoring {r['restores']} "
                f"answers exactly as the declared key does, so "
                f"SEATING_POPULATION_DEPENDS_ON is not load-bearing there")
        elif not r["survived"]:
            out["ok"] = False
            out["why"].append(
                f"THE {key.upper()} EMPTIED THE CHECK: no arm survived it, "
                f"so the flip proves nothing about the key - a check that "
                f"stopped looking passes a one-direction neuter")
    if out["empty_neuter"]["survived"]:
        out["ok"] = False
        out["why"].append(
            "a population that judges NOTHING still refused "
            f"{out['empty_neuter']['survived']}, so these arms are not "
            "measuring the population at all")
    return out


def _case_the_seating_population_is_every_piece() -> tuple[bool, str]:
    """THE CHECK THAT WOULD HAVE REFUSED W10-brgs1-treenear-x01 BEFORE IT
    WAS WRITTEN.

    `bridge` emitted 319 pieces, `refuse_on_seating` certified all 319, and
    the acceptance gate then refused FOUR of them as wholly under the applied
    surface - two wearing roles no arm of this check claimed, two seated
    exactly on a bilinear reading the engine never returns.  Both are the
    same defect: A KEY THAT NAMES A SUBSET OF WHAT THE QUANTITY DEPENDS ON.
    """
    r = seating_population_report()
    n = len(r["arms"])
    if not r["ok"]:
        return False, "; ".join(r["why"])
    return True, (
        f"{n} arm(s) answer as declared; the ROLE-keyed population loses "
        f"{r['role_neuter']['flipped']} while "
        f"{r['role_neuter']['survived']} survives; the PIVOT bed sample "
        f"loses {r['bed_neuter']['flipped']} while "
        f"{r['bed_neuter']['survived']} survives; a population judging "
        f"nothing loses all {len(r['empty_neuter']['flipped'])} refusing "
        f"arm(s)")

def bank_endpoint_arms() -> list[dict]:
    """Fractional-length spans retain a full tile at each actual bank."""
    arms = []
    for length in (20.0, 20.7, 21.3):
        for yaw in (0.0, 54.0, 90.0):
            a = math.radians(yaw)
            ux, uz = math.sin(a), math.cos(a)
            ax, az = 0.2, 0.3

            def along(x, z):
                return (x - ax) * ux + (z - az) * uz

            bed = Bed.applied(
                lambda x, z: 32.5 if (along(x, z) < 1.0 or
                                      along(x, z) > length - 1.0) else 29.0,
                "bank shelves at the actual fractional endpoints")
            pieces = bridge((ax, az), (ax + length * ux, az + length * uz),
                            yaw, 32.5, bed, width_tiles=3, bent_spacing_m=4.0)
            centre = [p for p in pieces if p.role.endswith("/deck")
                      and abs(p.lateral) < 1e-6 and not p.overhang]
            positions = sorted(along(p.x, p.z) for p in centre)
            # An overhang cannot substitute for the bank's own tile: callers
            # may trim it when the land swallows it.
            keyed = all(any(abs(t - bank) < 1e-6 for t in positions)
                        for bank in (0.0, length))
            continuous = all(b - a <= 2.0 + 1e-6
                             for a, b in zip(positions, positions[1:]))
            seated = not check_seating(pieces, bed)["travelled_way_buried"]
            supported = verify(pieces, bed)["ok"]
            arms.append(dict(
                name=f"length_{length:g}_yaw_{yaw:g}",
                ok=keyed and continuous and seated and supported,
                banks_keyed=keyed, covered_without_gaps=continuous,
                seating=seated, structural=supported))
    return arms


def _case_actual_banks_not_rounded_stations() -> tuple[bool, str]:
    real = bank_endpoint_arms()
    station_fn = globals()["_bridge_station_distances"]
    globals()["_bridge_station_distances"] = lambda length, overhang: [
        (s - overhang) * 2.0
        for s in range(int(round(length / 2.0)) + 1 + 2 * overhang)]
    try:
        old = bank_endpoint_arms()
    finally:
        globals()["_bridge_station_distances"] = station_fn
    flipped = [r["name"] for r in old if not r["ok"]]
    survived = [r["name"] for r in old if r["ok"]]
    return (all(r["ok"] for r in real) and bool(flipped and survived),
            f"actual-bank arms {real}; rounded-station neuter loses "
            f"{flipped}; exact-length survivors {survived}")


# ---------------------------------------------------------------------------
# TWO PIECES IN ONE PLACE - the arms, and what each neuter does to them
# ---------------------------------------------------------------------------
# THE KNOWN-BAD STATE IS MEASURED, NOT INVENTED.  S7-stathub-wtnorth-x01 laid
# a row 0.041 m behind its last (see `BRIDGE_STATIONS_DEPENDS_ON`).  The
# spans below are that one, three that put the final bay at the cube's edge
# (0.020 m on an axis, 0.049 m on an axis, 0.0707 m at the 45 degree corner
# where each axis carries 0.0500 m) and three the lattice never touched, two
# of them measured on the same world (S6 final bay 1.586 m, S2-x02 0.514 m).
#
# EVERY ARM ANSWERS A BOOLEAN VERDICT, never an assertion, and the driver
# compares them arm by arm - `accept/registry.py`'s `run_cases` reads an
# AssertionError as a flip, so an assert would let a check that stopped
# looking be stamped PROVEN.  The spans that must SURVIVE each neuter are
# what separate "the check lost this dependency" from "the check refuses
# everything".
_ROW_ARMS = (
    # (name, length_m, bearing_deg, laid_a_row_in_the_cube_before, why)
    ("S7_stathub_wtnorth_as_built", 44.041, 326.46, True,
     "MEASURED: 23 bays, final bay 0.041 m"),
    ("final_bay_0.020_on_an_axis", 30.020, 0.0, True,
     "0.020 m on z, 0.000 on x"),
    ("final_bay_0.049_on_an_axis", 30.049, 90.0, True,
     "0.049 m on x: inside the edge of the cube (0.050 itself is a float "
     "coin-flip: 30.05 - 30 reads 0.0500000000000007)"),
    ("final_bay_0.0707_at_the_cube_corner", 30.0707, 45.0, True,
     "0.0500 m on EACH axis: the longest bay the cube still reads as one "
     "place, which is why MIN_BAY_M is 2 x the cube, not the cube"),
    ("S6_stenvik_hognest_as_built", 43.586, 305.54, False,
     "MEASURED: final bay 1.586 m - the lattice must not move"),
    ("S2_stathub_eastlobe_x02_as_built", 46.514, 90.0, False,
     "MEASURED: final bay 0.514 m, the smallest on the world after S7"),
    ("exact_even_span", 20.0, 54.0, False, "final bay 2.000 m"),
)


def _row_arm(name: str, length: float, yaw: float, width_tiles: int = 3) -> dict:
    """One span, emitted through `bridge()`: did it come back clean?"""
    a = math.radians(yaw)
    ux, uz = math.sin(a), math.cos(a)
    ax, az = 0.2, 0.3

    def along(x, z):
        return (x - ax) * ux + (z - az) * uz

    bed = Bed.applied(
        lambda x, z: 32.5 if (along(x, z) < 1.0 or
                              along(x, z) > length - 1.0) else 29.0,
        "bank shelves at the actual fractional endpoints")
    try:
        pieces = bridge((ax, az), (ax + length * ux, az + length * uz), yaw,
                        32.5, bed, width_tiles=width_tiles,
                        bent_spacing_m=4.0)
    except SystemExit as e:
        return dict(name=name, got=False, refused=str(e), co_located=None)
    n = colocated_pieces(pieces)["counts"]
    co = n["same_solid"] + n["buried_copy"] + n["unmeasured"]
    centre = sorted(along(p.x, p.z) for p in pieces
                    if p.role.endswith("/deck") and abs(p.lateral) < 1e-6
                    and not p.overhang)
    keyed = all(any(abs(t - bank) < 1e-6 for t in centre)
                for bank in (0.0, length))
    continuous = all(b - a_ <= 2.0 + 1e-6
                     for a_, b in zip(centre, centre[1:]))
    return dict(name=name, got=co == 0 and keyed and continuous, refused=None,
                co_located=co, banks_keyed=keyed,
                covered_without_gaps=continuous, pieces=len(pieces))


def row_arms() -> list[dict]:
    return [_row_arm(n, length, yaw) for n, length, yaw, _bad, _why in _ROW_ARMS]


def _legacy_station_distances(length: float, overhang_stations: int) -> list[float]:
    """The lattice this emitter shipped before the final bay was guarded."""
    bays = max(1, math.ceil((length - 1e-9) / 2.0))
    return ([s * 2.0 for s in range(-overhang_stations, 0)]
            + [s * 2.0 if s < bays else length for s in range(bays + 1)]
            + [length + s * 2.0 for s in range(1, overhang_stations + 1)])


def lattice_report() -> dict:
    """The lattice itself, over a dense sweep: no span that was not a sliver
    moves, and no span at all lays two rows closer than `min_bay_m()`, leaves
    a seam over 2 m, or misses a bank coordinate."""
    floor = min_bay_m()
    moved, closer, seam, unkeyed, slivers, swept = [], [], [], [], 0, 0
    length = 4.0
    while length < 64.0:
        swept += 1
        bays = max(1, math.ceil((length - 1e-9) / 2.0))
        sliver = bays > 1 and length - 2.0 * (bays - 1) < floor
        slivers += sliver
        st = _bridge_station_distances(length, 1)
        if not sliver and st != _legacy_station_distances(length, 1):
            moved.append(round(length, 4))
        gaps = [b - a for a, b in zip(st, st[1:])]
        if min(gaps) < floor - 1e-9:
            closer.append(round(length, 4))
        if max(gaps) > 2.0 + 1e-9:
            seam.append(round(length, 4))
        on_span = [t for t in st if -1e-9 <= t <= length + 1e-9]
        if abs(on_span[0]) > 1e-9 or abs(on_span[-1] - length) > 1e-9:
            unkeyed.append(round(length, 4))
        length += 0.0137
    return dict(swept=swept, slivers=slivers, moved_without_a_sliver=moved[:5],
                rows_inside_the_floor=closer[:5], seams_over_2m=seam[:5],
                banks_missed=unkeyed[:5], min_bay_m=floor,
                ok=not (moved or closer or seam or unkeyed) and slivers > 0)


def _neuter_station_lattice_to_the_sliver_bay():
    """RESTORE THE DEFECT: the lattice that laid a row 0.041 m behind its
    neighbour.  Changes the stations and nothing else - the refusal stays."""
    return _flag("_BRIDGE_STATIONS_KEEP_SLIVER_BAY")


def _neuter_colocated_refusal_off():
    """RESTORE THE DEFECT: an emitter that returns whatever it laid."""
    return _flag("_COLOCATED_REFUSAL_OFF")


def _neuter_both(*neuters):
    undos = [n() for n in neuters]

    def undo():
        for u in reversed(undos):
            u()
    return undo


def colocation_arms() -> list[dict]:
    """Plans handed straight to `refuse_on_colocated`, no lattice in the way.

    want_refuse  the answer the gate itself would give.  A SHARED PIVOT that
    fills space nothing else fills, a stack, and a different prefab on one
    pivot are architecture and MUST pass - a refusal that fires on all of
    them is as useless as one that fires on none."""
    P = Placed
    a = math.radians(45.0)
    sx, sz = math.sin(a), math.cos(a)
    states = (
        ("a_second_copy_of_the_same_solid_is_refused", True,
         [P("wood_floor", 1.0, 2.0, 3.0, 45.0),
          P("wood_floor", 1.0, 2.0, 3.0, 45.0)]),
        ("a_row_0.041_m_behind_its_neighbour_is_refused", True,
         [P("wood_floor", 1.0, 2.0, 3.0, 45.0),
          P("wood_floor", 1.0 + 0.041 * sx, 2.0, 3.0 + 0.041 * sz, 45.0)]),
        ("a_row_0.5_m_behind_its_neighbour_is_accepted", False,
         [P("wood_floor", 1.0, 2.0, 3.0, 45.0),
          P("wood_floor", 1.5, 2.0, 3.0, 45.0)]),
        ("two_crossed_beams_on_one_pivot_are_architecture", False,
         [P("wood_beam", 1.0, 2.0, 3.0, 0.0),
          P("wood_beam", 1.0, 2.0, 3.0, 90.0)]),
        ("a_pole_stacked_over_a_pole_is_accepted", False,
         [P("wood_pole", 1.0, 2.0, 3.0, 0.0),
          P("wood_pole", 1.0, 6.0, 3.0, 0.0)]),
    )
    out = []
    for name, want, pieces in states:
        try:
            refuse_on_colocated(pieces, name)
            refused, said = False, ""
        except SystemExit as e:
            refused, said = True, str(e)
        out.append(dict(name=name, want=want, got=refused, ok=refused == want,
                        said=said.splitlines()[-1].strip() if said else ""))
    return out


def colocation_report() -> dict:
    """Every arm, plus what each neuter does to it - THE VERDICT ITSELF.

    `ok` is false unless ALL of:
      every span answers clean, and the lattice sweep is clean;
      every co-location arm answers the direction it declares;
      the STATION neuter flips EXACTLY the spans declared known-bad, and each
        of them is REFUSED BY NAME with metres - the refusal catches what the
        lattice used to put in the cube - while the declared-clean spans
        SURVIVE it;
      the REFUSAL neuter flips every refusing co-location arm and leaves the
        accepting ones accepted;
      with BOTH neuters - the emitter as it shipped - each known-bad span
        comes back WITH co-located pieces, counted, and the clean ones with
        none: the gate's finding, reproduced offline.
    """
    out = dict(ok=True, why=[])
    real = row_arms()
    bad_spans = [n for n, _l, _y, bad, _w in _ROW_ARMS if bad]
    good_spans = [n for n, _l, _y, bad, _w in _ROW_ARMS if not bad]
    out["arms"] = real
    out["lattice"] = lattice_report()
    if not out["lattice"]["ok"]:
        out["ok"] = False
        out["why"].append(f"the lattice sweep failed: {out['lattice']}")
    wrong = [a["name"] for a in real if not a["got"]]
    if wrong:
        out["ok"] = False
        out["why"].append(f"{len(wrong)} span(s) did not come back clean: "
                          f"{wrong}")

    undo = _neuter_station_lattice_to_the_sliver_bay()
    try:
        old = row_arms()
    finally:
        undo()
    flipped = [a["name"] for a in old if not a["got"]]
    survived = [a["name"] for a in old if a["got"]]
    named = {a["name"]: a["refused"] for a in old if a["refused"]}
    out["station_neuter"] = dict(flipped=flipped, survived=survived,
                                 refusal_with_metres={
                                     k: v.splitlines()[-1].strip()
                                     for k, v in named.items()})
    if sorted(flipped) != sorted(bad_spans):
        out["ok"] = False
        out["why"].append(
            f"THE STATION NEUTER FLIPPED {flipped}, declared known-bad "
            f"{bad_spans}: the lattice is not what separates them")
    if sorted(survived) != sorted(good_spans):
        out["ok"] = False
        out["why"].append(f"the station neuter EMPTIED THE CHECK or lost the "
                          f"wrong spans: survivors {survived}, declared "
                          f"clean {good_spans}")
    unnamed = [k for k in flipped
               if k not in named or " m apart" not in named[k]]
    if unnamed:
        out["ok"] = False
        out["why"].append(f"{unnamed} fell without a refusal that carries "
                          f"the metres")

    undo = _neuter_both(_neuter_station_lattice_to_the_sliver_bay,
                        _neuter_colocated_refusal_off)
    try:
        shipped = row_arms()
    finally:
        undo()
    out["as_shipped"] = {a["name"]: a["co_located"] for a in shipped}
    for a in shipped:
        if (a["name"] in bad_spans) != bool(a["co_located"]):
            out["ok"] = False
            out["why"].append(
                f"the shipped emitter laid {a['co_located']} co-located "
                f"piece(s) on {a['name']}; declared "
                f"{'known-bad' if a['name'] in bad_spans else 'clean'}")

    cart = colocation_arms()
    out["co_location_arms"] = cart
    out["why"].extend(f"arm {a['name']} answered the wrong way"
                      for a in cart if not a["ok"])
    undo = _neuter_colocated_refusal_off()
    try:
        off = colocation_arms()
    finally:
        undo()
    flipped_c = [a["name"] for a, b in zip(cart, off) if a["got"] != b["got"]]
    survived_c = [b["name"] for b in off if not b["got"] and not b["want"]]
    out["refusal_neuter"] = dict(flipped=flipped_c, survived=survived_c)
    if sorted(flipped_c) != sorted(a["name"] for a in cart if a["want"]):
        out["ok"] = False
        out["why"].append(f"THE REFUSAL NEUTER FLIPPED {flipped_c}: the "
                          f"refusal is not load-bearing on every refusing arm")
    if not survived_c:
        out["ok"] = False
        out["why"].append("no accepting arm survived the refusal neuter")
    if any(not a["ok"] for a in cart):
        out["ok"] = False
    return out


def _case_sliver_final_bay_lays_no_co_located_row() -> tuple[bool, str]:
    """THE CHECK THAT WOULD HAVE REFUSED S7-stathub-wtnorth-x01 BEFORE ITS
    FIRST `spawn_object`.  `bridge` emitted 195 pieces that the gate then
    read as 4 buried copies at (400, 1050) and (450, 1050); here the same
    span is asked through the emitter, with the lattice and with the refusal
    put back one at a time and together."""
    r = colocation_report()
    if not r["ok"]:
        return False, "; ".join(r["why"])
    sn, rn = r["station_neuter"], r["refusal_neuter"]
    return True, (
        f"{len(r['arms'])} span(s) come back clean over a "
        f"{r['lattice']['swept']}-length sweep ({r['lattice']['slivers']} "
        f"sliver final bays absorbed, none other moved); the legacy lattice "
        f"loses {sn['flipped']} to a named refusal while {sn['survived']} "
        f"survive; with the refusal ALSO off the shipped emitter lays "
        f"{ {k: v for k, v in r['as_shipped'].items() if v} } co-located "
        f"piece(s); the refusal neuter flips {rn['flipped']} and leaves "
        f"{rn['survived']} accepted")


def _case_co_located_plan_is_refused_with_its_numbers() -> tuple[bool, str]:
    """The span the gate refused, put through the emitter on the lattice that
    laid it.  It must come back as a refusal that names the metres - not a
    smaller assembly, not a returned list."""
    undo = _neuter_station_lattice_to_the_sliver_bay()
    try:
        arm = _row_arm("S7", 44.041, 326.46)
    finally:
        undo()
    said = arm["refused"] or ""
    ok = (not arm["got"] and "co-location re-test" in said
          and "buried inside the sibling" in said
          and " m apart" in said)
    return ok, ("refused: " + " | ".join(
        s.strip() for s in said.splitlines()[:5])) if said else (
        f"NOT REFUSED: returned {arm.get('pieces')} pieces with "
        f"{arm.get('co_located')} co-located")


def slip_arms() -> list[dict]:
    """`slip()` at four inner widths plus one plain two-tile jetty."""
    flat = Bed.applied(_flat(26.0), "a flat applied surface under the water")
    out = []
    for w in (9.0, 10.0, 11.0, 12.0):
        name = f"slip_{w:g}_m_inner_width"
        try:
            ps = slip((0.0, 0.0), 0.0, 24.0, w, DECK_Y, flat, name=name)
            n = colocated_pieces(ps)["counts"]
            out.append(dict(name=name, got=not (n["same_solid"]
                                                + n["buried_copy"]),
                            refused=None, pieces=len(ps),
                            co_located=n["same_solid"] + n["buried_copy"]))
        except SystemExit as e:
            out.append(dict(name=name, got=False, refused=str(e),
                            pieces=None, co_located=None))
    ps = jetty((0.0, 0.0), 90.0, 24.0, DECK_Y, flat, name="jetty_2_tiles")
    n = colocated_pieces(ps)["counts"]
    out.append(dict(name="jetty_2_tiles", got=not (n["same_solid"]
                                                   + n["buried_copy"]),
                    refused=None, pieces=len(ps),
                    co_located=n["same_solid"] + n["buried_copy"]))
    return out


def _case_slip_stands_each_column_and_tile_once() -> tuple[bool, str]:
    """`slip()` laid the same pile twice per bent - 24 same-solid
    `wood_pole_log_4` in a 24 m slip, 25 at 11 m inner width where its 7th
    head tile also lands on finger R's root tile - MEASURED by running the
    gate's census over it.  Nobody had built one on this world, so the gate
    never saw it; the next world's waterfront would have."""
    real = slip_arms()
    undo = _neuter_both(
        lambda: _flag("_JETTY_STANDS_A_ONE_TILE_COLUMN_TWICE"),
        lambda: _flag("_SLIP_HEAD_TILE_ON_A_FINGER_ROOT"))
    try:
        refused = slip_arms()
    finally:
        undo()
    undo = _neuter_both(
        lambda: _flag("_JETTY_STANDS_A_ONE_TILE_COLUMN_TWICE"),
        lambda: _flag("_SLIP_HEAD_TILE_ON_A_FINGER_ROOT"),
        _neuter_colocated_refusal_off)
    try:
        shipped = slip_arms()
    finally:
        undo()
    flipped = [a["name"] for a in refused if not a["got"]]
    survived = [a["name"] for a in refused if a["got"]]
    counts = {a["name"]: a["co_located"] for a in shipped if a["co_located"]}
    ok = (all(a["got"] for a in real)
          and flipped == [a["name"] for a in real if a["name"] != "jetty_2_tiles"]
          and survived == ["jetty_2_tiles"]
          and all(a["refused"] and " m apart" in a["refused"]
                  for a in refused if not a["got"])
          and sorted(counts) == sorted(flipped))
    return ok, (f"{len(real)} arm(s) clean; the shipped one-tile column and "
                f"head tile are REFUSED by name on {flipped} while "
                f"{survived} survives; with the refusal off they lay "
                f"{counts} same-solid pieces")


def _case_terminal_end_and_landing_rows_carry_their_own_bents() -> tuple[bool, str]:
    """A bank with no bench beyond it holds its OWN end -- both halves, both ways.

    The Ulfsland regen's T9-x03 and T13-x01 each ended on a terminal bank (the
    segment's own last station) and were refused for support 17.90 and 19.76
    against the 20.00 the safety factor needs: "the abutment end is carried by
    the bank" is only true where the bank touches the tile, and a shelf 0.30 m
    under the deck touches nothing.  Constructed here, no world:

      END ROW     a 20 m span whose far shelf lies 0.30 m under the deck.
                  Without `end_bents` the deck FAILS `verify`; with
                  `end_bents=("b",)` it holds.
      LOWERED     two landing rows 0.30 and 0.60 m under the datum over a
                  shore 3.5 m under it each stand on a bent of their own
                  (columns at both rows); the rule restored to raised rows
                  only (`_BENT_ONLY_UNDER_RAISED_ROWS`) leaves them on
                  nothing and the deck FAILS `verify`.
    """
    deck, length = 32.5, 20.0

    def shore(drop):
        return Bed.applied(
            lambda x, _z: deck if x < 2.0 else (26.0 if x < 16.0
                                                 else deck - drop),
            f"terminal fixture, far shore {drop:g} m under the deck")

    def span(bed, **kw):
        return bridge((0.0, 0.0), (length, 0.0), 90.0, deck, bed,
                      width_tiles=3, name="terminal", bent_spacing_m=4.0, **kw)

    shelf = shore(0.30)
    bare = verify(span(shelf), shelf)
    held = verify(span(shelf, end_bents=("b",)), shelf)
    landing = [(float(t), deck) for t in range(-2, 21, 2)] + [
        (21.0, deck - 0.30), (22.0, deck - 0.60)]
    low = shore(3.5)

    def columns(ps):
        return sorted({round(p.x, 2) for p in ps if p.role.endswith("/pile")})

    laid = span(low, deck_stations=landing)
    got = verify(laid, low)
    undo = _flag("_BENT_ONLY_UNDER_RAISED_ROWS")
    try:
        old = span(low, deck_stations=landing)
        old_v = verify(old, low)
    finally:
        undo()
    ok = (not bare["ok"] and held["ok"] and got["ok"] and not old_v["ok"]
          and {21.0, 22.0} <= set(columns(laid))
          and not {21.0, 22.0} & set(columns(old)))
    worst = min((f["support"] for f in bare["failures"]), default=None)
    return ok, (f"end row: {len(bare['failures'])} failure(s) without its "
                f"bent (worst support {worst} < 20.00), none with it; "
                f"landing rows 21 and 22 m stand on columns "
                f"{columns(laid)[-2:]}, and {len(old_v['failures'])} "
                f"piece(s) fail verify with the bent-under-raised-rows-only "
                f"rule")


# (name, must_refuse, fn) -- `must_refuse` is what the case is REQUIRED to
# answer, so a case that stops firing is a failure, not a pass.
SEATING_CASES = (
    ("pile_stack_solved_against_a_seed_bed", True,
     _case_pile_stack_against_a_seed_bed_is_refused),
    ("generated_bed_reaching_a_generator", True,
     _case_seed_bed_cannot_reach_a_generator),
    ("bare_callable_as_a_bed_profile", True, _case_bare_callable_is_refused),
    ("buried_travelled_way_tile", True, _case_buried_travelled_way_is_refused),
    ("edge_tile_keyed_into_its_bank", False,
     _case_keyed_in_edge_tile_is_not_refused),
    ("overhang_station_swallowed_by_the_bank", False,
     _case_overhang_is_reported_not_refused),
    ("jetty_solved_against_the_applied_surface", False,
     _case_applied_bed_passes),
    ("pile_column_quantisation_over_0..16_m", False,
     _case_pile_stack_quantisation_never_buries_its_own_foot),
    ("bent_leg_on_ground_at_the_deck_underside", False,
     _case_bent_on_ground_at_deck_level_is_omitted),
    ("rail_swallowed_by_the_bank_beside_the_deck", False,
     _case_rail_swallowed_by_the_bank_is_omitted),
    ("shoulder_tile_swallowed_by_the_bank_beside_the_deck", False,
     _case_shoulder_tile_swallowed_by_the_bank_is_omitted),
    ("seating_population_is_every_piece_on_the_engines_lattice", False,
     _case_the_seating_population_is_every_piece),
    ("fractional_span_tiles_both_actual_bank_coordinates", False,
     _case_actual_banks_not_rounded_stations),
    ("sliver_final_bay_lays_no_co_located_row", False,
     _case_sliver_final_bay_lays_no_co_located_row),
    ("co_located_plan_is_refused_with_its_numbers", True,
     _case_co_located_plan_is_refused_with_its_numbers),
    ("slip_stands_each_column_and_head_tile_once", False,
     _case_slip_stands_each_column_and_tile_once),
    ("terminal_end_and_landing_rows_carry_their_own_bents", False,
     _case_terminal_end_and_landing_rows_carry_their_own_bents),
)


def selftest() -> int:
    must_refuse = sum(1 for _n, r, _f in SEATING_CASES if r)
    print(f"crossings/assemble.py seating re-test: {len(SEATING_CASES)} "
          f"case(s), {must_refuse} of them states it MUST REFUSE\n")
    bad = 0
    for name, refuses, fn in SEATING_CASES:
        ok, detail = fn()
        verdict = "REFUSES" if refuses else "PASSES "
        print(f"  [{'ok  ' if ok else 'FAIL'}] {verdict} {name}\n"
              f"           {detail}")
        bad += not ok
    if bad:
        print(f"\n  {bad} case(s) did not answer as required.")
        return 1
    print(f"\n  all {len(SEATING_CASES)} cases answer as required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
