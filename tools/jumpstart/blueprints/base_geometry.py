#!/usr/bin/env python3
"""Where does a blueprint's floor meet the ground?

THE BUG THIS REPLACES
---------------------
`to_rcon_plan.py --align ground-center` used to compute `dy = -min(pivot Y)`
over every row and call that "drop the lowest piece onto the pad". Two things
are wrong with it, both MEASURED:

  1. A pivot is not a surface. `stone_floor_2x2`'s solid collider runs from
     -0.5 to +0.5 in its own local frame, i.e. the pivot sits at MID-THICKNESS
     of a 1 m slab. `stone_wall_1x1` is the same. So `min(pivot Y)` is neither
     the bottom of the structure nor the top of its floor -- it is a number with
     no geometric meaning, off by half a piece in an unknown direction.
  2. On 99 of the 157 non-empty `.blueprint` files in this corpus the writer had
     already normalised the body so that `min(pivot Y) == 0` exactly. On those
     files `dy` was ALWAYS 0.0 and the shift never did anything at all. It was a
     computation that confidently answered a question it was not measuring. The
     remaining 58 files are not normalised and span min pivot Y from -5064.42 to
     +4.49 m, so the old rule was not even consistent across the corpus.

Result in the live world, MEASURED: `pre-bonemass/iron-era-workshop`
(`BjOrN_blueprint001.blueprint`) was placed with its local Y=0 plane on a pad
flattened to 70.91 m. That put its lowest walkable floor 1.50 m up in the air,
its main floor (124 m2 of `stone_floor_2x2` at local 1.834) 1.83 m up, and the
median air gap under the structure 2.05 m -- the lowest solid in each 2 m column
over its 337 occupied columns, mode 2.1 m, with the rear terrace's underside at
6.02 m. The operator reported the building "floating about 10' high"; that was an
eyeball estimate from a player walking past a two-terrace building, and no plane
in this body's geometry sits 3.05 m up, so it is corroboration of the sign and
rough size of the error and not an input to the datum. Loose props placed by
`terraform/stock.py` at the same pad height sat correctly, which is the tell: the
props used the pad datum and the blueprint did not.

THE DATUM CHOSEN, AND WHAT IT DOES TO THE ALTERNATIVES
------------------------------------------------------
Three definitions of "the structure's bottom" were computed for every catalogued
blueprint (see `survey_datum.py`). They differ by up to metres.

  A. `bottom_solid`   lowest solid-collider Y over ALL pieces.
  B. `walkable`       top of the lowest FLOOR-ROLE piece.  <-- CHOSEN
  C. `support_bottom` lowest solid-collider Y over support/foundation pieces.

B is chosen. The reasons, in order:

  * B is the plane a player stands on, which is what "the floor rests on the
    pad" means, and it is the SAME plane the loose-object placement already
    uses. MEASURED from the collider dump: the ground-resting furnishings all
    have their solid starting at their own pivot -- piece_workbench +0.025,
    forge +0.035, hearth -0.017, piece_chest_wood +0.0003, smelter 0.000,
    charcoal_kiln -0.023, portal_wood -0.007 m. `terraform/stock.py` spawns
    those at the pad height and the operator confirmed they sit right. Putting
    the blueprint's lowest walkable surface at the pad height therefore makes
    the blueprint's own floor agree with the props placed beside it.
  * A lifts every deliberately-buried part clear of the ground. On
    iron-era-workshop the lowest solid is the underside of five stone_wall_1x1
    cubes at local -0.5, which is 2.00 m BELOW the lowest walkable surface;
    using A would float the ground floor by that 2.00 m, on top of the 1.50 m
    the old rule already floated it by. Across the 162 placeable bodies in the
    corpus A differs from B by a median of 2.23 m. That is the reported bug,
    restated.
  * C needs a list of "prefabs that are meant to be sunk into terrain" that the
    game does not publish, and it is A's failure in a milder form: a blueprint
    whose poles are sunk 0.5 m gets lifted 0.5 m. B leaves those poles sunk,
    which is correct -- a corner pole below the pad is a pole doing its job.

What B costs, stated rather than hidden: on a blueprint captured across a slope,
only ONE floor level can be flush with a flat pad. B makes the LOWEST one flush,
so higher terraces stand proud by their own step height. On iron-era-workshop
the lowest floor level (7 `stone_floor_2x2` tiles, top at local 1.500) lands
flush and the main floor (31 tiles, top at 1.834) stands 0.334 m proud. That
residual is reported per placement as `terraces_m`; it is geometry, not error.

REFUSAL
-------
`floor_datum` refuses rather than guesses. It returns a `FloorDatum` whose
`base_y` is None and whose `violations` say why, in the same spirit as
`solve_placements.py`'s `satisfies_requirement` / `violations` reporting:

  unmeasured_floor_prefab  FATAL. A floor-role piece whose prefab is absent
                           from the collider dump, so its surface cannot be
                           located. Re-run `run_piecegeometry.sh` against the
                           deployed game version.
  no_measurable_solid      FATAL. Not one piece in the body has a measurable
                           solid collider, so neither a floor nor a bottom
                           exists to align to.

and three non-fatal violations, each of which names the rule that answered so
the decision is auditable rather than silent:

  no_floor_used_lowest_solid  no floor-role piece at all -- a portal arch, a
                           bridge span, a palisade module. The datum falls back
                           to the bottom of the lowest solid, which is right for
                           a floorless body: the thing a builder sinks into
                           terrain on purpose is the foundation UNDER a floor,
                           and there is no floor here.
  suspect_below_grade_floor  the lowest plane holds under
                           `BELOW_GRADE_AREA_FRACTION` of the blueprint's floor
                           area AND the next plane up is at least
                           `BELOW_GRADE_STEP_M` higher. That is the signature of
                           a cellar or a sunken pit, and honouring it would bury
                           the house by a storey. The datum falls back to the
                           next plane up.
  props_below_floor        a pivot-at-base piece sits more than `FLOOR_TOL_M`
                           below the chosen plane. MEASURED across the corpus
                           this is normally an outdoor prop that stood on ground
                           lower than the building -- three `piece_chest_barrel`
                           0.6-0.8 m under `PuP_house10`'s floor, a
                           `piece_chest_wood` on bare terrain beside
                           `instairtower`. Floors define the datum anyway,
                           because burying a floor makes a building unenterable
                           and a barrel standing 0.7 m proud of a flat pad is
                           cosmetic. The depth is reported as
                           `props_below_floor_m`.

A blueprint whose body spans several storeys on a captured slope has NO single
correct datum, and this module does not pretend otherwise: it reports every
walkable level and the residual of each (`terraces_m`). Where an operator judges
a different plane correct for a specific site -- a raised cart deck over a camp
that stands on bare ground, a dock whose lower jetty belongs at the waterline --
`placements.yaml` carries an explicit `blueprint_datum.override_base_y` with a
written `basis`, in the same declared-requirement-plus-measured-verdict shape
the rest of that file already uses. An override is a DECLARATION, recorded and
reviewable; it is not this module guessing.

Geometry comes from `data/piece_geometry.json`, built by
`build_piece_geometry.py` from a sandbox dedicated-server run of
`PieceGeometry.cs`. Nothing in this module assumes a dimension.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEOMETRY_PATH = HERE / "data" / "piece_geometry.json"

# ---------------------------------------------------------------------------
# tolerances, and where each number comes from
# ---------------------------------------------------------------------------

# How far below a floor surface a ground-resting object may legitimately sit
# before the floor plane is considered unestablished.
#
# MEASURED, `survey_datum.py --offsets` over the whole catalogued corpus: for
# every pivot-at-base piece, the highest floor surface whose slab contains it
# horizontally, and the gap. 9243 samples. The NEGATIVE tail -- a piece sunk
# into the surface it stands on -- is bounded: min -0.300 m (a `hearth` sunk
# into a `stone_floor_2x2`), p01 -0.218, p05 -0.058, median +0.043. 0.35 m
# covers the measured worst case with margin and still sits under the 0.500 m
# half-thickness of `stone_floor_2x2`, the thickest floor slab in the corpus, so
# it cannot swallow a genuine second floor.
#
# (The POSITIVE tail of that distribution runs to +39 m and is not a tolerance
# candidate: it is pieces used as cladding on walls and upper storeys, which are
# not standing on the floor beneath them at all. Worth stating, because reading
# a symmetric tolerance out of that distribution is exactly the kind of number
# that looks measured and is not.)
FLOOR_TOL_M = 0.35

# How close a prefab's solid must start to its own pivot for the prefab to count
# as pivot-at-base, i.e. for its Y to be a reading of the surface it stands on.
#
# MEASURED over the 99 furniture/station/rug-named prefabs present in this
# corpus: 76 have their solid starting within 0.080 m of their pivot. The next
# ones out are 0.106 m and beyond and are all things that are NOT floor-standing
# -- wall-mounted station extensions, ceiling braziers, and ground torches whose
# stake is modelled 0.65 m below the pivot -- plus one `piece_artisanstation`
# whose solid starts 100 m below its pivot. 0.10 m keeps the 76 and rejects
# every one of those, which is the whole reason the check is measured and not a
# name list.
PIVOT_AT_BASE_TOL_M = 0.10

# The cellar / sunken-pit signature. A floor level holding under 2% of the
# blueprint's floor area, with the next level up at least 1.5 m higher, is not
# the ground floor -- 1.5 m is above the tallest single build step in the game
# (a 1 m wall course plus a 0.5 m slab) so two levels that far apart cannot be
# adjacent terrace steps.
BELOW_GRADE_AREA_FRACTION = 0.02
BELOW_GRADE_STEP_M = 1.5

# A STOREY's worth of floor area. The datum is the lowest walkable level holding
# at least this share of the blueprint's floor area; smaller levels below it are
# steps, porches, daises, cart decks and causeway courses, not the floor a player
# stands on.
#
# MEASURED case this exists for: `BjOrN_blueprint001` (pre-bonemass/
# iron-era-workshop). Its lowest walkable level is 7 `stone_floor_2x2` tiles,
# 28 m2 (2.5% of its 1142 m2 of floor), laid as a single diagonal line of slabs
# that carries the SECOND course of the same diagonal 1.0 m directly above it --
# a wall built out of floor pieces, not a floor. Its main hall floor is 31 tiles,
# 124 m2, 0.334 m higher. Resting the 28 m2 line on the pad lifts the hall and
# everything above it by that 0.334 m.
#
# 0.10 is the threshold, and it is chosen against the corpus rather than picked:
# sweeping 0.05 - 0.20 over the 174 parseable bodies moves 10-16 of them, and
# every fraction in that range moves the same core set. At 0.10 the 15 bodies
# that move all go from a 1-148 m2 sliver to a 36-1969 m2 storey, which is the
# shape the rule is claiming to fix.
MAJOR_LEVEL_AREA_FRACTION = 0.10

# How far the datum may CLIMB to reach that major level. One wall course.
#
# A level less than a course below the major floor cannot be a separate storey --
# there is no room for one -- so burying it is burying a step. A level a full
# course or more below IS a storey, and burying a storey is the failure this
# module exists to avoid, so the climb stops and the lowest level stands with the
# discrepancy reported. MEASURED over the corpus: 26 of the 124 eligible bodies
# hit this bound and keep their lowest level, including `citadel` (6.0 m down to
# its lowest plane) and `Пипкин_full_castle` (2.0 m); without the bound those
# would be buried by that much.
MAJOR_LEVEL_CLIMB_M = 1.0

# The base PROFILE raster. The question "is the bottom of this blueprint flat?"
# is not answerable from a floor level: it is the lowest SOLID in each column of
# the body's footprint, and on a slope-captured body it is a staircase.
#
# 2 m cells because that is the module size of the pieces being measured
# (`stone_floor_2x2`, `stone_wall_2x1`), so a finer raster reports sub-piece
# sampling noise as relief.
BASE_PROFILE_CELL_M = 2.0

# How far above the pad a column's lowest solid has to stand before a player
# reads daylight under it. 0.5 m, the half-thickness of `stone_floor_2x2`, the
# thickest slab in the corpus: less than that and the gap is inside the slab the
# body is built from.
BASE_PROFILE_FLAT_TOL_M = 0.5

# Vertical extent above which a BUILD PIECE's stored solid is a trigger volume
# rather than a collider. See `Geometry._drop_trigger_volumes` for the
# measurement; the short version is 15.53 m of real maximum against one 200 m
# outlier.
TRIGGER_VOLUME_HEIGHT_M = 16.0

# How much of the footprint may stand in air before the placement is reported as
# not sitting on its pad.
#
# The verdict is the AIR, not the relief, and that distinction is measured. Raw
# base relief conflates two opposite things: `PuP_black_house_full` has 68.9 m of
# relief and ZERO floating columns, because all of its spread is foundation
# BELOW the datum, which is a foundation doing its job. Judging by relief would
# have flagged it and missed the difference that matters.
#
# 0.10 comes from the corpus distribution of floating area at each body's own
# chosen datum over the 164 placeable bodies: p25 0.000, median 0.045, p75 0.292,
# max 0.713. A tenth of the footprint is above the median and below the upper
# quartile, so an ordinary body clears it and a terraced or spanning one does
# not. It fires on 62 of the 164, and reading the list back is the check on the
# number: hillside castles (`Пипкин_full_castle` 0.61), bridges by design
# (`brokkr-broken-bridge-1` 0.58, `salty-dick-bridge-curved-final` 0.71), docks
# (`dock` 0.70) and ruins. Those bodies genuinely cannot rest on a flat pad.
BASE_PROFILE_AIR_AREA_FRACTION = 0.10

# Floor levels are quantised before they are compared, because a captured
# building's floor pieces land on fractional Y values that differ in the fourth
# decimal. 0.02 m is finer than any real build step and coarser than that noise.
LEVEL_QUANTUM_M = 0.02

# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

# A floor-role prefab is one whose NAME says floor/paving AND whose MEASURED
# collider is slab-shaped. The name test alone admits `piece_brazierfloor01`,
# which is a 4 x 4 x 4 m brazier; the geometry test alone admits
# `blackmarble_2x2x2`, a cube block used as much for walls as for floors. Both
# gates together is what makes the set defensible.
#
# `(?:^|_)` before the word is load-bearing: it keeps `wood_floor`,
# `Ashlands_Ruins_Floor_6x6` and `ashwood_deco_floor` and rejects
# `piece_brazierfloor01`, in which `floor` is glued to `brazier`.
#
# Rugs and carpets are deliberately NOT floors. A rug lies ON a floor, so it
# reports a surface a couple of centimetres too high and, worse, a rug thrown
# over a beam deck would invent a floor plane where there is no slab. They are
# ground-resting instead, which is what they are.
_FLOOR_NAME = re.compile(r"(?:^|_)(?:floor|paving|paved)", re.IGNORECASE)

# Prefabs that physically cannot float: they are put down ON a surface. Used
# only as a GUARD on the chosen plane, never as the source of it. The list is
# name-pattern based so a kit variant (`ashwood_bed`, `piece_chest_blackmetal`)
# is caught, and every match is then required to have its solid collider start
# within FLOOR_TOL_M of its own pivot -- prefabs failing that measured check are
# dropped from the guard set rather than trusted, because a name is not
# evidence.
_GROUND_RESTING_NAME = re.compile(
    r"(?:^|_)(?:chest|bed|bench|chair|throne|stool|table|preptable|cauldron|hearth"
    r"|fireplace|workbench|forge|blackforge|smelter|kiln|blastfurnace|windmill"
    r"|spinningwheel|stonecutter|artisanstation|magetable|cookingstation|oven"
    r"|eitrrefinery|fermenter|beehive|sapcollector|barber|bathtub|cartographytable"
    r"|rug|carpet|groundtorch|brazier|cart|sled)",
    re.IGNORECASE,
)

# Crafting stations and station extensions -- the subset of the above whose
# burial is a FUNCTIONAL failure rather than a cosmetic one. A workbench under
# the pad cannot be crafted at; a barrel under the pad is just a barrel nobody
# sees. So when the floor plane would bury a station, the station wins and the
# datum drops to it, with the decision reported.
#
# MEASURED case this exists for: `wagon-camp.blueprint`, the body behind
# `pre-kall/deepnorth-landing-camp`. Its only floor-role pieces are a 2 m2 and a
# 19 m2 cart deck at local 1.560 and 2.460, while every station in the body --
# `piece_workbench_ext2` 0.04, `piece_workbench_ext1` 0.13, `piece_cauldron` and
# `piece_cookingstation` 0.39 -- stands on bare ground 1.2-1.5 m BELOW the lower
# deck. The camp's ground plane is 0.04, not 1.560, and the floor-only rule gets
# that wrong by 1.52 m.
_STATION_NAME = re.compile(
    r"(?:^|_)(?:workbench|forge|blackforge|smelter|kiln|blastfurnace|windmill"
    r"|spinningwheel|stonecutter|artisanstation|artisan|magetable|cookingstation"
    r"|oven|preptable|cauldron|eitrrefinery|fermenter|beehive|sapcollector|barber"
    r"|bathtub|cartographytable)",
    re.IGNORECASE,
)

# Support/foundation prefabs, for the `support_bottom` figure reported alongside
# the chosen datum. Never used to place anything -- it exists so the report can
# say how deep the chosen datum buries the things designed to be buried.
_SUPPORT_NAME = re.compile(
    r"(?:^|_)(?:pole|pillar|pillarbase|stake|sharpstakes|foundation|base)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

_CORNER_SIGNS = tuple(
    (-1 if not (i & 1) else 1, -1 if not (i & 2) else 1, -1 if not (i & 4) else 1)
    for i in range(8)
)


def _solid_corners(entry: list) -> list[tuple[float, float, float]]:
    """The 8 (or 8-per-collider) corner points of one stored solid."""
    if entry[0] == "b":
        x0, y0, z0, x1, y1, z1 = entry[1:7]
        cx, cy, cz = (x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5
        ex, ey, ez = (x1 - x0) * 0.5, (y1 - y0) * 0.5, (z1 - z0) * 0.5
        return [(cx + sx * ex, cy + sy * ey, cz + sz * ez) for sx, sy, sz in _CORNER_SIGNS]
    v = entry[1:]
    return [(v[i], v[i + 1], v[i + 2]) for i in range(0, 24, 3)]


def _qrot(q, v):
    """Rotate v by quaternion q (x, y, z, w), Unity convention."""
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def _normalised(q) -> tuple[float, float, float, float]:
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-6:
        # A `.vbuild` row with every rotation field blank parses to (0,0,0,0).
        # Identity is the only defensible reading, and it is the reading
        # `to_rcon_plan.py` already emits for those rows.
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)  # type: ignore[return-value]


class Geometry:
    """The measured collider solids, keyed by prefab name."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else GEOMETRY_PATH
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.game_version = doc.get("game_version", "")
        self.generated_utc = doc.get("generated_utc", "")
        self._prefabs: dict[str, list] = doc["prefabs"]
        self._build_pieces: frozenset[str] = frozenset(doc.get("build_pieces", ()))
        self._span_cache: dict[tuple, tuple[float, float] | None] = {}
        self.trigger_volumes: dict[str, list[float]] = {}
        self._drop_trigger_volumes()

    def _drop_trigger_volumes(self) -> None:
        """Discard the dump's non-physical volumes from BUILD PIECES.

        MEASURED, and the reason this exists: `piece_artisanstation` carries
        three solids -- a 2.65 x 1.03 x 1.10 m bench, a 2.35 x 1.56 x 0.23 m
        rack, and a 40 x 200 x 40 m box centred on its pivot. The third is a
        trigger volume, not a collider, and unioned in it makes the prefab's
        AABB reach 100 m below its own pivot. Left in, it eats a 40 x 40 m
        square of any pad the piece stands on: MEASURED on
        `deepnorth-sandbox/complete-station-hub`, only 113 of the pad's 3,249
        one-metre cells survived the fixture-clearance test, and the four
        chests that would not fit were reported as "nowhere legal to stand".

        The bound is 16 m of VERTICAL extent and it applies to build pieces
        only, both halves measured. Over the dump's 2,028 build-piece solids the
        tallest genuine one is a 15.53 m `VikingShip_Ashlands` hull box, then
        14.90 and 14.64; the next value up is 200.0, so the threshold sits in a
        13x gap rather than in a distribution. And it is build pieces only
        because world decor is legitimately that big -- in this corpus
        `cliff_mistlands1` is 72.65 m tall across 625 rows, `Beech1` 30.46,
        `rock1_mountain` 29.14, all real geometry a fixture must not be placed
        inside.
        """
        for prefab in self._build_pieces:
            solids = self._prefabs.get(prefab)
            if not solids or len(solids) < 2:
                # A prefab whose ONLY solid is huge is not a trigger volume with
                # a piece attached; it is whatever it is, and guessing it away
                # would be inventing geometry rather than measuring it.
                continue
            kept, dropped = [], []
            for s in solids:
                pts = _solid_corners(s)
                height = max(p[1] for p in pts) - min(p[1] for p in pts)
                (dropped if height > TRIGGER_VOLUME_HEIGHT_M else kept).append(
                    round(height, 2) if height > TRIGGER_VOLUME_HEIGHT_M else s)
            if dropped and kept:
                self._prefabs[prefab] = kept
                self.trigger_volumes[prefab] = dropped

    def known(self, prefab: str) -> bool:
        return prefab in self._prefabs

    def has_solid(self, prefab: str) -> bool:
        return bool(self._prefabs.get(prefab))

    def local_aabb(self, prefab: str) -> tuple[float, float, float, float, float, float] | None:
        """Unrotated prefab-local AABB of the solids, for shape tests."""
        solids = self._prefabs.get(prefab)
        if not solids:
            return None
        pts = [p for s in solids for p in _solid_corners(s)]
        return (
            min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts),
        )

    def y_span(self, prefab: str, rot, scale) -> tuple[float, float] | None:
        """(min Y, max Y) of the prefab's solids in BLUEPRINT-LOCAL space,
        relative to the piece's own pivot, with the row's rotation and scale
        applied. `None` means the prefab has no measured solid."""
        solids = self._prefabs.get(prefab)
        if not solids:
            return None
        q = _normalised(tuple(rot))
        sx, sy, sz = scale
        key = (prefab, round(q[0], 6), round(q[1], 6), round(q[2], 6), round(q[3], 6),
               round(sx, 6), round(sy, 6), round(sz, 6))
        hit = self._span_cache.get(key, 0)
        if hit != 0:
            return hit
        lo = math.inf
        hi = -math.inf
        for s in solids:
            for cx, cy, cz in _solid_corners(s):
                y = _qrot(q, (cx * sx, cy * sy, cz * sz))[1]
                if y < lo:
                    lo = y
                if y > hi:
                    hi = y
        out = (lo, hi)
        self._span_cache[key] = out
        return out

    def is_floor(self, prefab: str) -> bool:
        """Name says floor AND the measured solid is slab-shaped: at least 0.9 m
        across in both horizontal axes, and no taller than it is narrow."""
        if not _FLOOR_NAME.search(prefab):
            return False
        box = self.local_aabb(prefab)
        if box is None:
            return False
        dx, dy, dz = box[3] - box[0], box[4] - box[1], box[5] - box[2]
        return dx >= 0.9 and dz >= 0.9 and dy <= min(dx, dz) + 1e-6

    def is_ground_resting(self, prefab: str) -> bool:
        """Name says furniture/station/rug AND its solid MEASURABLY starts at its
        own pivot, which is what makes its Y a reading of the surface it stands
        on rather than of nothing in particular."""
        if not _GROUND_RESTING_NAME.search(prefab):
            return False
        box = self.local_aabb(prefab)
        return box is not None and abs(box[1]) <= PIVOT_AT_BASE_TOL_M

    def is_build_piece(self, prefab: str) -> bool:
        """True when the prefab carries a `Piece` component, i.e. a player built
        it. False for world decor -- rocks, trees, vegetation -- which blueprint
        capture happily picks up and whose colliders reach metres below their
        pivots because they are modelled to sit IN the ground."""
        return prefab in self._build_pieces

    def is_station(self, prefab: str) -> bool:
        """A crafting station or station extension, gated on pivot-at-base.

        Separated from the rest of the ground-resting set because a buried
        station is a FUNCTIONAL failure -- a player cannot craft at a workbench
        under the pad -- whereas a buried barrel is cosmetic. That difference is
        what decides the datum when the two disagree."""
        if not _STATION_NAME.search(prefab):
            return False
        box = self.local_aabb(prefab)
        return box is not None and abs(box[1]) <= PIVOT_AT_BASE_TOL_M

    def is_support(self, prefab: str) -> bool:
        return bool(_SUPPORT_NAME.search(prefab)) and self.has_solid(prefab)


_GEOMETRY: Geometry | None = None

def geometry() -> Geometry:
    global _GEOMETRY
    if _GEOMETRY is None:
        _GEOMETRY = Geometry()
    return _GEOMETRY


# ---------------------------------------------------------------------------
# the footprint
# ---------------------------------------------------------------------------


@dataclass
class Footprint:
    """The XZ rectangle a blueprint body actually OCCUPIES, after a yaw.

    Two things separate this from `min(pos.x)..max(pos.x)` over the rows, and
    both were MEASURED wrong on `BjOrN_blueprint001` before it existed:

    1. A pivot is not a solid. That body's pivot box is
       `0..65.467` x `0..65.984`; its colliders reach `-0.63..65.74` x
       `-0.63..66.55`, so the pivot box misplaces the centre by
       `(+0.17, +0.03)` m and understates the footprint by ~1.2 m on both axes.
    2. A footprint is not rotation-equivariant, so it MUST be measured after
       the yaw rather than rotated with the body. Centring on a pre-rotation
       centre and then rotating about it leaves the real centre offset by
       `R(yaw) * d - d`, where `d` is the pivot-box-to-solid-box offset: zero
       at 0 degrees, and `-2d` at 180, which is a sign FLIP rather than an
       error that stays put. That is what "centred at 0, off at 180" looks
       like, and on this body it is 0.35 m of X.
    """

    min_x: float
    max_x: float
    min_z: float
    max_z: float
    yaw_deg: float = 0.0
    pieces: int = 0
    pivot_only: list[str] = field(default_factory=list)

    @property
    def center_x(self) -> float:
        return (self.min_x + self.max_x) * 0.5

    @property
    def center_z(self) -> float:
        return (self.min_z + self.max_z) * 0.5

    @property
    def size_x(self) -> float:
        return self.max_x - self.min_x

    @property
    def size_z(self) -> float:
        return self.max_z - self.min_z


def xz_footprint(objects, yaw_deg: float = 0.0, geom: Geometry | None = None) -> Footprint:
    """The occupied XZ rectangle of `objects` after rotating the body `yaw_deg`
    about the blueprint's own origin.

    `objects` is the same shape `floor_datum` takes: `.prefab`, `.pos`, `.rot`,
    `.scale`. Every measured collider corner of every row is transformed by the
    row's own rotation and scale, translated to the row's pivot, then yawed.
    Rows whose prefab has no measured solid contribute their pivot alone and are
    named in `pivot_only` -- a point is an honest under-statement, and the
    caller can see how many rows it applies to.
    """
    g = geom or geometry()
    half = math.radians(yaw_deg) * 0.5
    sin_h, cos_h = math.sin(half), math.cos(half)
    lo_x = lo_z = math.inf
    hi_x = hi_z = -math.inf
    pivot_only: list[str] = []
    pieces = 0
    for obj in objects:
        pieces += 1
        solids = g._prefabs.get(obj.prefab)
        if solids:
            q = _normalised(tuple(obj.rot))
            sx, sy, sz = obj.scale
            offsets = [
                _qrot(q, (cx * sx, cy * sy, cz * sz))
                for s in solids
                for cx, cy, cz in _solid_corners(s)
            ]
        else:
            pivot_only.append(obj.prefab)
            offsets = [(0.0, 0.0, 0.0)]
        for ox, _oy, oz in offsets:
            px = obj.pos[0] + ox
            pz = obj.pos[2] + oz
            # yaw about the local origin, from the same quaternion the emitter
            # uses: (0, sin, 0, cos) applied to (x, y, z).
            rx = px * (1.0 - 2.0 * sin_h * sin_h) + pz * (2.0 * sin_h * cos_h)
            rz = pz * (1.0 - 2.0 * sin_h * sin_h) - px * (2.0 * sin_h * cos_h)
            if rx < lo_x:
                lo_x = rx
            if rx > hi_x:
                hi_x = rx
            if rz < lo_z:
                lo_z = rz
            if rz > hi_z:
                hi_z = rz
    if pieces == 0:
        raise ValueError("no objects: a footprint of nothing has no centre")
    return Footprint(lo_x, hi_x, lo_z, hi_z, yaw_deg, pieces, pivot_only)

# ---------------------------------------------------------------------------
# the base profile -- "is the bottom of this blueprint flat?"
# ---------------------------------------------------------------------------


@dataclass
class BaseProfile:
    """The lowest SOLID in each cell of the body's footprint, in blueprint-local
    metres. This is the shape that would have to be cut into the pad for the
    whole body to touch ground, and it is the only honest answer to the
    operator's question -- a datum is one number and a base is a surface.

    `bottom` is keyed by (cell x, cell z) index at `cell_m` resolution.
    """

    cell_m: float
    bottom: dict[tuple[int, int], float] = field(default_factory=dict)

    @property
    def columns(self) -> int:
        return len(self.bottom)

    @property
    def area_m2(self) -> float:
        return self.columns * self.cell_m * self.cell_m

    def percentile(self, q: float) -> float:
        vals = sorted(self.bottom.values())
        if not vals:
            return float("nan")
        return vals[min(len(vals) - 1, max(0, int(q / 100.0 * (len(vals) - 1))))]

    @property
    def relief_m(self) -> float:
        """Peak-to-peak spread of the base. Zero for a body with a flat bottom."""
        if not self.bottom:
            return 0.0
        return max(self.bottom.values()) - min(self.bottom.values())

    def grounded(self, base_y: float) -> bool:
        """Does the body actually SIT on a flat pad at this datum?

        Asked of the air under the footprint rather than of the relief, because
        relief cannot tell a deep foundation from a floating wing: MEASURED,
        `PuP_black_house_full` has 68.9 m of base relief and not one floating
        column, all of it foundation below the datum.
        """
        air = self.air(base_y)
        return (not air) or air["floating_fraction"] <= BASE_PROFILE_AIR_AREA_FRACTION

    def air(self, base_y: float, tol: float = BASE_PROFILE_FLAT_TOL_M) -> dict:
        """What a player would see under the body once `base_y` is put on the pad.

        A column whose lowest solid stands more than `tol` above the pad has
        VISIBLE AIR under it: nothing in that column reaches the ground. A column
        below the pad is buried, which is what a foundation is for.
        """
        if not self.bottom:
            return {}
        air = [v - base_y for v in self.bottom.values() if v - base_y > tol]
        cell_area = self.cell_m * self.cell_m
        return {
            "columns": self.columns,
            "columns_floating": len(air),
            "floating_fraction": round(len(air) / self.columns, 3),
            "floating_area_m2": round(len(air) * cell_area, 1),
            "max_air_m": round(max(air), 3) if air else 0.0,
            "mean_air_m": round(sum(air) / len(air), 3) if air else 0.0,
            "air_volume_m3": round(sum(air) * cell_area, 1),
            "max_buried_m": round(
                max((base_y - v for v in self.bottom.values()), default=0.0), 3),
            "grounded": len(air) / self.columns <= BASE_PROFILE_AIR_AREA_FRACTION,
        }

    def steps(self, bin_m: float = 0.5, top: int = 8) -> list[dict]:
        """The base's own terraces: how much area bottoms out at each height.
        This is the profile a human reads, rather than 400 raw cell values."""
        cell_area = self.cell_m * self.cell_m
        hist: dict[float, int] = {}
        for v in self.bottom.values():
            key = round(round(v / bin_m) * bin_m, 3)
            hist[key] = hist.get(key, 0) + 1
        out = [{"bottom_y": y, "area_m2": round(n * cell_area, 1),
                "share": round(n / self.columns, 3)}
               for y, n in sorted(hist.items(), key=lambda kv: -kv[1])]
        return out[:top]


def base_profile(objects, geom: Geometry | None = None,
                 cell_m: float = BASE_PROFILE_CELL_M) -> BaseProfile:
    """Rasterise the lowest solid in each column of the body's footprint.

    Measured in the body's OWN frame: a yaw turns the raster but cannot change
    its relief, and the relief is the whole point.
    """
    g = geom or geometry()
    prof = BaseProfile(cell_m=cell_m)
    bottom = prof.bottom
    for o in objects:
        solids = g._prefabs.get(o.prefab)
        if not solids:
            continue
        q = _normalised(tuple(o.rot))
        sx, sy, sz = o.scale
        lo_x = lo_y = lo_z = math.inf
        hi_x = hi_z = -math.inf
        for s in solids:
            for cx, cy, cz in _solid_corners(s):
                px, py, pz = _qrot(q, (cx * sx, cy * sy, cz * sz))
                if px < lo_x:
                    lo_x = px
                if px > hi_x:
                    hi_x = px
                if pz < lo_z:
                    lo_z = pz
                if pz > hi_z:
                    hi_z = pz
                if py < lo_y:
                    lo_y = py
        y0 = o.pos[1] + lo_y
        ix0 = math.floor((o.pos[0] + lo_x) / cell_m)
        ix1 = math.ceil((o.pos[0] + hi_x) / cell_m)
        iz0 = math.floor((o.pos[2] + lo_z) / cell_m)
        iz1 = math.ceil((o.pos[2] + hi_z) / cell_m)
        for ix in range(ix0, max(ix0 + 1, ix1)):
            for iz in range(iz0, max(iz0 + 1, iz1)):
                key = (ix, iz)
                if key not in bottom or y0 < bottom[key]:
                    bottom[key] = y0
    return prof


# ---------------------------------------------------------------------------
# the datum
# ---------------------------------------------------------------------------

FATAL_VIOLATIONS = frozenset(("unmeasured_floor_prefab", "no_measurable_solid"))


@dataclass
class FloorLevel:
    """One walkable surface plane in blueprint-local coordinates."""

    y: float
    pieces: int
    area_m2: float


@dataclass
class FloorDatum:
    """The answer to "given this blueprint, what Y should the placement origin
    be at so the floor rests on terrain height H?"

    `place_y(H) == H - base_y`. When `base_y` is None the blueprint is
    UNPLACEABLE and `violations` says why.
    """

    base_y: float | None
    method: str
    violations: list[dict] = field(default_factory=list)
    levels: list[FloorLevel] = field(default_factory=list)
    bottom_solid: float | None = None
    support_bottom: float | None = None
    lowest_ground_resting: float | None = None
    lowest_station: float | None = None
    pieces: int = 0
    floor_pieces: int = 0
    unmeasured_prefabs: list[str] = field(default_factory=list)
    props_below_floor_m: float | None = None
    profile: BaseProfile | None = None

    def base_dict(self) -> dict | None:
        """The base PROFILE, reported beside the datum because a datum is one
        number and a base is a surface. Absent only when nothing in the body has
        a measurable solid."""
        if self.profile is None or not self.profile.bottom:
            return None
        p = self.profile
        out = {
            "cell_m": p.cell_m,
            "columns": p.columns,
            "footprint_area_m2": round(p.area_m2, 1),
            "bottom_y_min": round(p.percentile(0), 3),
            "bottom_y_p25": round(p.percentile(25), 3),
            "bottom_y_median": round(p.percentile(50), 3),
            "bottom_y_p75": round(p.percentile(75), 3),
            "bottom_y_max": round(p.percentile(100), 3),
            "relief_m": round(p.relief_m, 3),
            "bottom_is_flat": p.relief_m <= BASE_PROFILE_FLAT_TOL_M,
            "steps": p.steps(),
        }
        if self.base_y is not None:
            out["on_a_flat_pad"] = p.air(self.base_y)
        return out

    @property
    def placeable(self) -> bool:
        return self.base_y is not None

    def place_y(self, terrain_height: float) -> float:
        if self.base_y is None:
            raise ValueError(f"unplaceable blueprint: {self.violations}")
        return terrain_height - self.base_y

    def terraces_m(self) -> list[float]:
        """How far each walkable level ends up above the pad. The first entry is
        0.0 for the chosen level; the rest are the unavoidable cost of laying a
        slope-captured building on a flat pad."""
        if self.base_y is None:
            return []
        return [round(lv.y - self.base_y, 3) for lv in self.levels]

    def as_dict(self) -> dict:
        return {
            "base_y": None if self.base_y is None else round(self.base_y, 3),
            "method": self.method,
            "placeable": self.placeable,
            "bottom_solid_y": None if self.bottom_solid is None else round(self.bottom_solid, 3),
            "support_bottom_y": (
                None if self.support_bottom is None else round(self.support_bottom, 3)
            ),
            "lowest_station_y": (
                None if self.lowest_station is None else round(self.lowest_station, 3)
            ),
            "lowest_ground_resting_y": (
                None if self.lowest_ground_resting is None
                else round(self.lowest_ground_resting, 3)
            ),
            "walkable_levels_y": [round(lv.y, 3) for lv in self.levels],
            "walkable_level_pieces": [lv.pieces for lv in self.levels],
            "walkable_level_area_m2": [lv.area_m2 for lv in self.levels],
            "props_below_floor_m": self.props_below_floor_m,
            "base_profile": self.base_dict(),
            "floor_pieces": self.floor_pieces,
            "pieces": self.pieces,
            "violations": self.violations,
        }

    def summary_dict(self, max_levels: int = 12) -> dict:
        """The compact form recorded in `placements.yaml`. Same numbers as
        `as_dict`, with the walkable levels folded into one list of mappings and
        capped -- `BjOrN_blueprint001` alone has 27 of them, and four parallel
        27-element lists in a placement file is noise, not evidence."""
        levels = [
            {"y": round(lv.y, 3),
             "above_pad_m": None if self.base_y is None else round(lv.y - self.base_y, 3),
             "pieces": lv.pieces,
             "area_m2": lv.area_m2}
            for lv in self.levels[:max_levels]
        ]
        out = {
            "base_y": None if self.base_y is None else round(self.base_y, 3),
            "method": self.method,
            "placeable": self.placeable,
            "bottom_solid_y": None if self.bottom_solid is None else round(self.bottom_solid, 3),
            "support_bottom_y": (
                None if self.support_bottom is None else round(self.support_bottom, 3)
            ),
            "lowest_station_y": (
                None if self.lowest_station is None else round(self.lowest_station, 3)
            ),
            "lowest_ground_resting_y": (
                None if self.lowest_ground_resting is None
                else round(self.lowest_ground_resting, 3)
            ),
            "floor_pieces": self.floor_pieces,
            "pieces": self.pieces,
            "walkable_levels": levels,
            "walkable_levels_total": len(self.levels),
        }
        base = self.base_dict()
        if base is not None:
            out["base_profile"] = base
        if self.props_below_floor_m is not None:
            out["props_below_floor_m"] = self.props_below_floor_m
        out["violations"] = self.violations
        return out


def _quantise(y: float) -> float:
    return round(y / LEVEL_QUANTUM_M) * LEVEL_QUANTUM_M


def _not_grounded_violation(prof: BaseProfile | None, base_y: float) -> dict | None:
    """The violation nobody had, and the one the operator raised by eye.

    A datum is a single number and a body's BASE is a surface. When that surface
    is not flat, no datum can put all of it on a flat pad: part of the body is
    buried and part of it stands in the air, and the only question is how much.
    That is a property of the BLUEPRINT and the PAD, not an error in the datum,
    so it is reported rather than refused -- but it is REPORTED, because
    `violations: []` on a body with 13.8 m of base relief is how a hillside
    castle got signed off as correctly placed and the operator found the truth by
    walking up to it.

    Gated on the AIR under the footprint, not on the relief: see
    BASE_PROFILE_AIR_AREA_FRACTION for why relief cannot tell a deep foundation
    from a floating wing.

    MEASURED on `BjOrN_blueprint001` (pre-bonemass/iron-era-workshop): 508
    occupied 2 m columns, base from -0.500 to +13.342, and at the chosen datum
    173 of those columns -- 692 m2, a third of the footprint -- stand more than
    0.5 m clear of the pad, up to 11.5 m of it. The operator's words were "now
    it's floating in air... it looks like the bottom of the blueprint isnt
    flat.. correct?". Correct.
    """
    if prof is None or not prof.bottom or prof.grounded(base_y):
        return None
    air = prof.air(base_y)
    steps = ", ".join(f"{s['area_m2']:.0f} m2 at y={s['bottom_y']:+.1f}"
                      for s in prof.steps(top=4))
    return {
        "code": "base_not_grounded_on_flat_pad",
        "detail": f"the body's BASE is not flat: over {prof.columns} occupied "
                  f"{prof.cell_m:g} m columns its lowest solid runs from "
                  f"{prof.percentile(0):.3f} to {prof.percentile(100):.3f} "
                  f"({prof.relief_m:.3f} m of relief), so it was captured across a "
                  f"slope or on terraced ground. Largest steps: {steps}. Resting "
                  f"y={base_y:.3f} on a FLAT pad therefore buries up to "
                  f"{air['max_buried_m']:.2f} m of it and leaves "
                  f"{air['floating_area_m2']:.0f} m2 "
                  f"({air['floating_fraction']:.1%} of the footprint, over the "
                  f"{BASE_PROFILE_AIR_AREA_FRACTION:.0%} allowed) standing in air, "
                  f"mean {air['mean_air_m']:.2f} m and up to {air['max_air_m']:.2f} m. "
                  f"No single datum removes that; only cutting the pad to this "
                  f"profile, or choosing a body whose base IS flat, does.",
    }


def floor_datum(objects, geom: Geometry | None = None) -> FloorDatum:
    """Locate the blueprint's floor plane in blueprint-local coordinates.

    `objects` is any iterable of items exposing `.prefab`, `.pos` (x, y, z),
    `.rot` (quaternion x, y, z, w) and `.scale` (x, y, z) -- the shape
    `to_rcon_plan.read_objects` already produces, so the parser is not
    duplicated here.
    """
    g = geom or geometry()
    # Materialised because the body is walked TWICE -- once for the levels, once
    # for the base profile -- and a caller passing a generator would otherwise
    # get a silently empty profile.
    objects = list(objects)
    violations: list[dict] = []
    levels: dict[float, list[tuple[float, float]]] = {}
    bottom_solid = math.inf
    bottom_build = math.inf
    support_bottom = math.inf
    lowest_resting: float | None = None
    lowest_station: float | None = None
    resting_levels: list[float] = []
    unmeasured: set[str] = set()
    n = 0
    floor_pieces = 0

    for o in objects:
        n += 1
        span = g.y_span(o.prefab, o.rot, o.scale)
        if span is None:
            if _FLOOR_NAME.search(o.prefab):
                unmeasured.add(o.prefab)
            continue
        lo, hi = o.pos[1] + span[0], o.pos[1] + span[1]
        if lo < bottom_solid:
            bottom_solid = lo
        if g.is_build_piece(o.prefab) and lo < bottom_build:
            bottom_build = lo
        if g.is_support(o.prefab) and lo < support_bottom:
            support_bottom = lo
        if g.is_ground_resting(o.prefab):
            resting_levels.append(o.pos[1])
            if lowest_resting is None or o.pos[1] < lowest_resting:
                lowest_resting = o.pos[1]
        if g.is_station(o.prefab):
            if lowest_station is None or o.pos[1] < lowest_station:
                lowest_station = o.pos[1]
        if g.is_floor(o.prefab):
            floor_pieces += 1
            box = g.local_aabb(o.prefab)
            # Slab area from the MEASURED footprint, so a 4x4 stone_floor counts
            # four times a 2x2 one when the area share is judged.
            area = (box[3] - box[0]) * (box[5] - box[2])
            # Grouped by the quantised surface, but the level's reported Y is the
            # TRUE lowest slab top in the group. Reporting the quantised value
            # instead would put up to half a quantum of invented error into the
            # datum itself, which is not a thing to do to the number the whole
            # placement hangs off.
            levels.setdefault(_quantise(hi), []).append((area, hi))

    out = FloorDatum(
        base_y=None,
        method="",
        pieces=n,
        floor_pieces=floor_pieces,
        bottom_solid=None if bottom_solid is math.inf else bottom_solid,
        support_bottom=None if support_bottom is math.inf else support_bottom,
        lowest_ground_resting=lowest_resting,
        lowest_station=lowest_station,
        unmeasured_prefabs=sorted(unmeasured),
        profile=base_profile(objects, g),
    )
    out.levels = [
        FloorLevel(y=min(t[1] for t in a), pieces=len(a),
                   area_m2=round(sum(t[0] for t in a), 2))
        for y, a in sorted(levels.items())
    ]

    if unmeasured:
        violations.append({
            "code": "unmeasured_floor_prefab",
            "prefabs": sorted(unmeasured),
            "detail": "floor-role prefab absent from data/piece_geometry.json, so its "
                      "walkable surface cannot be located; re-run "
                      "run_piecegeometry.sh against the deployed game version",
        })
        out.violations = violations
        out.method = "refused"
        return out

    if not out.levels:
        # No floor-role piece at all: a portal arch, a bridge span, a palisade
        # module, a pile of poles. There is no floor to rest, so the only
        # defensible plane left is the bottom of the lowest BUILD piece. Build
        # pieces only, because blueprint capture picks up world decor whose
        # colliders are modelled to sit IN the ground -- MEASURED on
        # `Portal13.vbuild`, whose `Rock_4` bottoms out at -3.024 while every
        # built piece in the body bottoms out at 0.066. Using the raw lowest
        # solid there would lift the shrine 3.02 m, i.e. reintroduce the exact
        # bug this module exists to fix.
        bottom = bottom_build if bottom_build is not math.inf else bottom_solid
        if bottom is math.inf:
            violations.append({
                "code": "no_measurable_solid",
                "detail": f"none of {n} pieces has a measurable solid collider, so "
                          f"neither a floor nor a bottom can be located",
            })
            out.violations = violations
            out.method = "refused"
            return out
        violations.append({
            "code": "no_floor_used_lowest_solid",
            "detail": f"no floor-role piece among {n} pieces; datum falls back to the "
                      f"lowest build-piece solid y={bottom:.3f}"
                      + ("" if bottom_build is not math.inf else
                         " (no build piece either, so world decor was used)"),
        })
        out.base_y = bottom
        out.method = "lowest_build_solid_no_floor"
        flat = _not_grounded_violation(out.profile, bottom)
        if flat:
            violations.append(flat)
        out.violations = violations
        return out

    # A level is INHABITED when something pivot-at-base stands on it.
    def inhabited(level_y: float) -> bool:
        return any(abs(r - level_y) <= FLOOR_TOL_M for r in resting_levels)

    total_area = sum(lv.area_m2 for lv in out.levels)
    idx = 0
    # Skip AT MOST ONE below-grade level. A level is read as below grade when it
    # holds a negligible share of the blueprint's floor area AND nothing stands
    # on it -- a sump, a pit, a sub-cellar slab.
    #
    # MEASURED case this exists for: `PuP_black_house_full.blueprint`
    # (pre-queen/mistlands-blackforge-base) has 80 m2 at local -7.000 with no
    # furniture on it at all, under 1216 m2 at -6.000 carrying 22 furnishings and
    # every one of its 13 crafting stations.
    #
    # ONE level, not a loop, and the bound is load-bearing. An unbounded version
    # was written first and MEASURED to walk `Пипкин_full_castle.blueprint` up
    # through 24 successive levels, from local -36.333 to +5.760 -- 42 m of
    # "skipping", which is not a sump, it is the rule losing its nerve one small
    # level at a time. A sump is one level under the floor by construction, so
    # one is the honest limit and anything deeper is left visible in `levels`
    # for a human rather than quietly climbed.
    if (
        len(out.levels) > 1
        and total_area > 0
        and out.levels[0].area_m2 / total_area < BELOW_GRADE_AREA_FRACTION
        and not inhabited(out.levels[0].y)
    ):
        violations.append({
            "code": "suspect_below_grade_floor",
            "detail": f"walkable level y={out.levels[0].y:.3f} holds "
                      f"{out.levels[0].area_m2:.1f} m2 of {total_area:.1f} m2 "
                      f"({out.levels[0].area_m2 / total_area:.1%}) and nothing stands "
                      f"on it; read as a sump or sub-cellar and skipped in favour of "
                      f"y={out.levels[1].y:.3f}",
        })
        idx = 1

    # THE LOWEST LEVEL IS NOT NECESSARILY A FLOOR. Having skipped a sump, climb
    # to the lowest level that is big enough to BE a storey -- at least
    # MAJOR_LEVEL_AREA_FRACTION of the body's floor area -- provided the climb
    # stays inside one wall course, MAJOR_LEVEL_CLIMB_M. Every level stepped over
    # is by construction smaller than that fraction and less than a course down,
    # so what gets buried is a step, a porch, a dais or a cart deck, never a
    # storey.
    #
    # MEASURED case this exists for, and the defect it fixes: `BjOrN_blueprint001`
    # (pre-bonemass/iron-era-workshop). Its lowest walkable level is 7
    # `stone_floor_2x2` on a single 12 m diagonal -- 28 m2, 2.5% of 1142 m2 --
    # carrying a SECOND identical course of slabs 1.0 m directly above it at the
    # same XZ. That is a wall built out of floor pieces, and the old rule rested
    # the whole castle on it, lifting the 124 m2 hall floor and all 26 levels
    # above it by 0.334 m. The cost is not the 0.334 m: MEASURED from the base
    # profile, resting the hall floor instead grounds 540 m2 of footprint that
    # otherwise stands 0.55 m clear of the pad, i.e. a quarter of the plan with
    # daylight under it.
    #
    # The area fraction is NOT the inhabited test the sump rule uses, on purpose:
    # FLOOR_TOL_M is 0.35 m and these two levels are 0.334 m apart, so a prop on
    # the hall floor reads as standing on the diagonal too. A tolerance cannot
    # separate planes closer together than itself, and area can.
    climbed: list[FloorLevel] = []
    if total_area > 0 and out.levels[idx].area_m2 / total_area < MAJOR_LEVEL_AREA_FRACTION:
        for j in range(idx + 1, len(out.levels)):
            if out.levels[j].y - out.levels[idx].y > MAJOR_LEVEL_CLIMB_M:
                break
            if out.levels[j].area_m2 / total_area >= MAJOR_LEVEL_AREA_FRACTION:
                climbed = out.levels[idx:j]
                idx = j
                break
    chosen = out.levels[idx]
    if climbed:
        violations.append({
            "code": "minor_levels_below_datum",
            "detail": ", ".join(
                f"y={lv.y:.3f} holding {lv.area_m2:.1f} m2 "
                f"({lv.area_m2 / total_area:.1%})" for lv in climbed)
            + f" {'is' if len(climbed) == 1 else 'are'} below the chosen datum "
              f"y={chosen.y:.3f} ({chosen.area_m2:.1f} m2, "
              f"{chosen.area_m2 / total_area:.1%} of {total_area:.1f} m2) and will be "
              f"buried by up to {chosen.y - climbed[0].y:.3f} m. Each holds under "
              f"{MAJOR_LEVEL_AREA_FRACTION:.0%} of the floor area and sits less than a "
              f"{MAJOR_LEVEL_CLIMB_M:g} m wall course down, so it is a step or a "
              f"terrace course rather than a storey; the storey wins.",
        })
        method = "lowest_major_walkable_surface"
    else:
        method = ("lowest_walkable_surface" if idx == 0
                  else "lowest_walkable_surface_above_cellar")

    # A CRAFTING STATION below the chosen plane overrides it. A buried station is
    # a functional failure (nobody can craft at a workbench under the pad); a
    # floor edge standing proud is cosmetic. MEASURED case: `wagon-camp.blueprint`
    # (pre-kall/deepnorth-landing-camp), whose only floor-role pieces are a 2 m2
    # and a 19 m2 cart deck at 1.560/2.460 while all four of its stations stand
    # on bare ground at 0.04-0.39. The floor-only rule buries that camp 1.52 m.
    if lowest_station is not None and lowest_station < chosen.y - FLOOR_TOL_M:
        violations.append({
            "code": "station_below_floor",
            "detail": f"a crafting station stands at y={lowest_station:.3f}, "
                      f"{chosen.y - lowest_station:.2f} m below the lowest walkable "
                      f"surface y={chosen.y:.3f}. A buried station cannot be used, so "
                      f"the station plane wins; the floor above it stands proud by that "
                      f"much, which is cosmetic.",
        })
        out.base_y = lowest_station
        out.method = "lowest_station_below_floor"
        flat = _not_grounded_violation(out.profile, lowest_station)
        if flat:
            violations.append(flat)
        out.violations = violations
        return out

    if lowest_resting is not None and lowest_resting < chosen.y - FLOOR_TOL_M:
        # A pivot-at-base piece below the chosen floor that is NOT a station.
        # Not fatal, and not a reason to lower the datum. MEASURED across the
        # corpus this is normally an outdoor prop that stood on ground lower than
        # the building's floor -- three `piece_chest_barrel` 0.6-0.8 m below the
        # floor of `PuP_house10`, a `piece_chest_wood` on bare terrain beside
        # `instairtower`. Honouring those would bury the building's door sill,
        # which makes it unenterable; leaving them proud of a flat pad is
        # cosmetic. Floors win, and the cost is reported rather than hidden.
        out.props_below_floor_m = round(chosen.y - lowest_resting, 3)
        violations.append({
            "code": "props_below_floor",
            "detail": f"a ground-resting piece sits at y={lowest_resting:.3f}, "
                      f"{chosen.y - lowest_resting:.2f} m below the chosen floor plane "
                      f"y={chosen.y:.3f}; on a flat pad it will stand proud of the "
                      f"ground by that much. Floors define the datum because burying a "
                      f"floor makes a building unenterable and a floating barrel does "
                      f"not.",
        })

    out.base_y = chosen.y
    out.method = method
    flat = _not_grounded_violation(out.profile, chosen.y)
    if flat:
        violations.append(flat)
    out.violations = violations
    return out
