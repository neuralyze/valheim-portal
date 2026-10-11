#!/usr/bin/env python3
"""Harbour B - the OPEN harbour village at (1190, 444) - and the SMALL
ENCLOSED boathouses at the existing waterfront sites.

WHY THIS MODULE EXISTS BESIDE `harbour_city.py`.  `harbour_city.design_b`
designs harbour B and has never been built.  Three of its numbers do not
survive being measured, and each of the three is the kind of defect this
project's own doctrine names:

  1. THE BAY MOUTH FACES INLAND.  `harbour_city.shed()` hard-codes the open
     gable at +z and `design_b` calls it with `z_back=430, z_front=456`.
     MEASURED here (`survey_b.py`, 2026-09-19): the open-sea component of the
     villB patch touches the SOUTH patch edge and not the north; the deepest
     sea-connected cell in the basin is (1267, 290) at 12.169 m; the span
     falls monotonically from 168 m at z=410 to 12 m at z=518.  So the mouth
     of this fjord is at -z and `design_b`'s boathouse opens up the valley and
     closes its gable against the sea.  A boat cannot enter it.

  2. THE BOATHOUSE STANDS IN OPEN WATER.  Its solved position (x_centre 1204,
     z 430..456) is 26-45 m from either bank at that latitude with no deck
     between, so nothing reaches it.  `solve_boathouse` scores on depth alone
     and never asks whether a player can walk to the thing.

  3. THE CENTRE PIER IS BUILT TWICE.  `boathouse()` gives bay 1 an east apron
     over x[1203,1205] and bay 2 a west apron over the SAME x[1203,1205].
     MEASURED on the emitted plan: 41 rows are exact (prefab,x,y,z)
     duplicates - 13 `wood_floor` and 28 `wood_pole_log_4`.  The same code
     built Ulfsgard's boathouse, so that structure very likely carries the
     same 41 doubled pieces; that is reported, not repaired, because the
     ledger is append-only and Ulfsgard is not this ticket.

  4. TWO OF THREE BERTHS ARE UNREACHABLE.  Solved on depth only, berth 3 at
     (1200, 462) is 8.06 m from the nearest planned deck tile.  A berth that
     floats a hull nobody can board is scenery with a depth measurement
     attached.

And one number that `design_b` never asked for at all: THE GROUND AT B IS NOT
FLAT.  MEASURED over 8,075 lattice centres in x 1110..1300, z 400..570, dry
with 0.8 m freeboard:

    pad     <=0.3 m   <=0.6 m   <=1.0 m   <=1.5 m   <=2.0 m   <=7.0 m
    16 m       0         0         6        269       940      2716
    20 m       0         0         0         38       435      2178
    24 m       0         0         0         10       162      1703

There is NOT ONE pad at harbour B flat to 0.6 m.  `fit_quarter` accepts a pad
whose worst deviation is 7.0 m and seats the body at the footprint MEDIAN, so
`design_b` would have stood 20 m halls with up to 4.704 m of hillside through
the floor.  Zero terrain writes is the better design where it is available and
it is NOT available here: every body on this site is TERRACED, per building,
through the canonical `flatten.py` encoder, and the terrace ceiling is 2.0 m.
That is the operator's "terrace, do not platform" with the measurement behind
it rather than the slogan.

NO CURTAIN WALL.  Operator's ruling, and the number behind it is already in
`design_b`: B's shortest verified closing line is 113.0 m of curtain for
4,326 m2 enclosed, and every line from z 450 to 498 fails to close the basin
at all.  `design_b`'s refusal record is carried forward verbatim.

THE SMALL BOATHOUSES.  Every structure called a boathouse on this world is
decking on piles - `wood_floor`, `wood_pole`, `wood_pole_log_4`, no roof, no
walls, no bay.  A boathouse here is walls on three sides, a gabled roof, an
apron you can walk, and a seaward gable that is DELIBERATELY OPEN because it
is the bay mouth.  A census counting four walls per bay would be counting a
defect.

The small bay is 10.0 x 14.0 m clear and it is sized by LENGTH, which is the
honest gate here:

    admits   DoubleRowingCanoe  3.84 x  8.20  draught 0.8  needs 1.8 m
             RowingCanoe        7.68 x  6.59  draught 1.0  needs 2.0 m
             Karve              7.68 x 10.12  draught 1.2  needs 2.2 m
             LittleBoat         4.23 x 10.37  draught 1.2  needs 2.2 m
    refuses  CargoShip          9.16 x 15.71  - 2.71 m too LONG
             BigCargoShip      14.10 x 20.05  - too long AND too wide
             WarShip           10.07 x 20.08  - too long
             VikingShip/MercantShip 9.16 x 21.57 - 8.57 m too long

so a small boathouse cannot silently become a large one, and the worst beam
fender is 0.42 m either side (Karve/RowingCanoe at 7.68 m in 10.0 m clear).
Bay geometry is on the 2 m lattice throughout: `wood_floor` is 2.00 m square
and a bay clear width that is not a multiple of 2 overlaps its own back apron
by the remainder, which is a 1 m double floor rather than a rounding error.

USAGE
    ./harbour_b.py design  [--out FILE] [--plans DIR]     READ-ONLY
    ./harbour_b.py sheds   [--out FILE]                   READ-ONLY, small sheds
    ./harbour_b.py build   [--stages ...] [--dry] [--expect-sha SHA]
    ./harbour_b.py shedbuild --site ID [--dry] [--expect-sha SHA]
    ./harbour_b.py verify  [--report FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "settlements", JUMPSTART / "crossings",
           JUMPSTART / "roads", JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints", JUMPSTART / "clearing"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import assemble as A        # noqa: E402  crossings/
import harbour_city as HC   # noqa: E402
import water as W           # noqa: E402
import terrace as TR        # noqa: E402  the landing and the shared terrace write
import applied as AP        # noqa: E402  roads/, whose ground is whose
import waterfront as WF     # noqa: E402

ACTOR = "HarbourBVillage"
SITE = HC.SITE_B                      # harbour-village-sudrvik

# ---------------------------------------------------------------------------
# the hand of the fjord.  MEASURED, not inherited.
# ---------------------------------------------------------------------------
HAND = dict(
    mouth="-z (south)",
    instrument=("`waterfront.sea_mask` over the villB 1 m patch at the "
                "CargoShip requirement 3.0 m, then which patch EDGE the "
                "open-sea component touches"),
    touches_patch_edges=dict(south=True, north=False, west=True, east=True),
    deepest_connected_cell=[1267.0, 290.0, 12.169],
    span_by_z={410: 168.0, 450: 105.0, 470: 66.0, 490: 44.0, 518: 12.0},
    consequence=("the bay mouth of every shed here opens toward -z; "
                 "`harbour_city.shed()` opens toward +z and is therefore not "
                 "reused"),
)

# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
APRON_W = 2.0                 # one `wood_floor` tile
WALL_COURSES = 2              # 2 x 2 m of `woodwall` above the deck
BIG_BAY = dict(clear=11.5, length=26.0, need_m=3.25,
               why=("11.5 x 26.0 m is Ulfsgard's own measured clear opening; "
                    "3.25 m is CargoShip's 3.0 m requirement plus the "
                    "project's 0.25 m BERTH_MARGIN_FLOOR, which is 9.1x the "
                    "0.0275 m field noise floor"))
SMALL_BAY = dict(clear=10.0, length=14.0, need_m=2.45,
                 why=("10.0 x 14.0 m admits every small hull and refuses "
                      "every large one ON LENGTH - the shortest large hull, "
                      "CargoShip, is 15.71 m against a 13.0 m limit. 2.45 m "
                      "is the deepest small draught (Karve/LittleBoat 1.2) "
                      "plus 1.0 m keel clearance plus the 0.25 m margin "
                      "floor"))

# A body is TERRACED, and this is the ceiling on that terrace.  MEASURED: no
# pad anywhere at harbour B is flat to 0.6 m, so the alternative to a terrace
# is not "no write", it is a hall with 4.7 m of hillside through its floor.
BODY_CUT_MAX_M = 2.0

# WHERE THE ROAD ARRIVES is NOT a number this module owns.  It used to be: a
# "street head" pinned to a ledger seq, a station and a datum (38.219 m) read
# off ANOTHER world's chain, so on any regeneration the village refused for a
# road that was never built to that number (`REFUSING sudrvik-roadhead:
# sudrvik-fjordhead-12 now levels to 34.103 m but R2 was profiled to 38.219 m
# (ledger seq 2916)` -- seq 2916 of a ledger that no longer exists).  What a
# declared site owes its roads is its LANDING: the rectangle
# `harbour_site.measure` publishes (`declared_sites[].landing_pad`), which the
# roads are routed to, built here by `settlements/terrace.py` as one more
# terrace and measured against the roads that actually arrive.

# The quarters, as rectangles of interest over the ground the pad census says
# is there: 24 m pads within 20 m of water cluster at (1136, 544-556),
# (1200, 536-552), (1216, 536-544), (1160, 520-532), (1144, 500-508),
# (1132, 448) and (1124-1128, 484).  The fitter decides the seats.
QUARTERS = [
    dict(id="vestrbakki", x=(1116.0, 1172.0), z=(436.0, 502.0), yaw=90.0,
         why="the west bank, addressing the water across the quay"),
    dict(id="austrbakki", x=(1210.0, 1272.0), z=(436.0, 506.0), yaw=270.0,
         why="the east bank, addressing the water across the quay"),
    dict(id="fjordhead", x=(1158.0, 1242.0), z=(504.0, 562.0), yaw=180.0,
         why="the head above the shallows, addressing the fjord to the south"),
    dict(id="vestrhlid", x=(1108.0, 1158.0), z=(502.0, 564.0), yaw=90.0,
         why=("the west shoulder above the head - where the 24 m pad census "
              "clusters (1136, 544-556), (1140, 520-528) and (1144, 500-508), "
              "all within 20 m of water")),
]

TORCH_PITCH_M = HC.TORCH_PITCH_M          # 12.0
TORCH_ABOVE_SURFACE = HC.TORCH_ABOVE_SURFACE   # 0.65359
GEOM = HC.GEOM
DECK_Y = HC.DECK_Y
DECK_AUTHOR_Y = HC.DECK_AUTHOR_Y


def surface():
    """The applied surface, with EVERY 1 m patch this module needs unioned in.

    `harbour_city.surface()` passes `extra=/tmp/harbour/city.bin`, and
    `waterfront.Surface` treats `extra` as a REPLACEMENT for its own
    `EXTRA_FIELD` default rather than an addition - so loading the harbour B
    field DISPLACES `/tmp/harbour/extra.bin`, which is where the `north`
    patch (centre -170,2570, half 150.5) lives.  MEASURED: with the
    `harbour_city` surface, `(-174, 2625)` - the north cape dock - answers
    "is in no patch", so a shed there would have been refused for want of a
    height rather than for want of water.  That is the same silent drop that
    once lost `dock-northcape`'s vessel.  `patch_for` still takes the
    SMALLEST containing patch, so a 1 m question is never answered off an
    8 m overview.
    """
    s = HC.surface()
    for extra in ("/tmp/harbour/extra.bin",):
        if Path(extra).exists():
            s.fields |= W.load(extra)
            if extra not in s.field_names:
                s.field_names.append(extra)
    return s


def field(s, x=1190.0, z=444.0):
    return s.patch_for(x, z, margin=2.0)


# ---------------------------------------------------------------------------
# one enclosed bay, mouth at -z
# ---------------------------------------------------------------------------

def shed(pl: HC.Plan, s, *, bay_x0: float, bay_x1: float, z_close: float,
         z_open: float, why: str, apron_w: float = APRON_W,
         courses: int = WALL_COURSES) -> dict:
    """One enclosed bay.  Aprons, piles, three walls, a gabled roof.

    `z_close` is the LANDWARD end and carries the back wall and the filled
    gable; `z_open` is the SEAWARD end and is left open, because that is the
    bay mouth.  `z_close > z_open` always: the mouth of this fjord is at -z
    (see HAND) and the +z-opening geometry in `harbour_city.shed` would close
    the gable against the sea.

    The ridge runs along z - the bay's long axis - so a hull enters under the
    ridge and the slopes shed to either side.  `wood_roof`'s own slope is
    along its local z, so a tile sloping in world x is placed at yaw 90 / 270.

    AND THE SENSE OF THOSE TWO IS THE DEFECT THIS FILE SHIPPED.  MEASURED
    from the prefab's own prism (`blueprints/data/piece_geometry.json`,
    game_version 1.0.12): `wood_roof`'s LOW edge is at local z=+1.0031 and
    its HIGH edge at local z=-0.9971, so it rises toward local -z.  Under the
    yaw convention this tree uses everywhere (`crossings/assemble.py::_unit`
    and `Placed.aabb`: 0=+Z, 90=+X, clockwise from above) a yaw-90 tile
    therefore puts its HIGH edge to the WEST and a yaw-270 tile puts it to
    the EAST.  The first version placed yaw 90 on the WEST half and yaw 270
    on the EAST half, so BOTH slopes fell 0.5654 m/m INTO the ridge: a
    butterfly, and because each course's pivot still climbed 1.131 m, a
    sawtooth with six 2.234 m vertical risers per bay.  The panel nearer the
    ridge must carry the yaw whose HIGH edge points AT the ridge, which is
    270 on the west half and 90 on the east half.

    Nothing geometric caught it and that is the lesson worth keeping: a 180
    degree yaw flip moves neither the footprint nor the AABB, so the count
    census, the position census at tolerance 0 AND the portal-hall
    plane-coverage test in `terminus_camp.py::cover` all return IDENTICAL
    numbers either way.  For an ORIENTED piece, verify the orientation as a
    SIGNED quantity; the volume it occupies cannot tell you which way it
    faces.  The operator saw it from the ground in one look.
    """
    if z_close <= z_open:
        raise SystemExit(f"shed: z_close {z_close} must exceed z_open "
                         f"{z_open}; the mouth of this fjord is at -z")
    aw = apron_w
    n0 = len(pl)
    west = HC.deck_strip(pl, s, bay_x0 - aw, bay_x0, z_open, z_close,
                         why=why + " apron W")
    east = HC.deck_strip(pl, s, bay_x1, bay_x1 + aw, z_open, z_close,
                         why=why + " apron E")
    back = HC.deck_strip(pl, s, bay_x0 - aw, bay_x1 + aw, z_close, z_close + 2.0,
                         why=why + " back apron")
    wall_y0 = DECK_Y + GEOM["wall"]["half"]
    zs = np.arange(z_open + 1.0, z_close + 2.0, 2.0)
    walls = 0
    for x in (bay_x0 - aw, bay_x1 + aw):
        for z in zs:
            for c in range(courses):
                pl.add(GEOM["wall"]["prefab"], x, wall_y0 + 2.0 * c, float(z),
                       90.0, why + " long wall")
                walls += 1
    for x in np.arange(bay_x0 - aw + 1.0, bay_x1 + aw, 2.0):
        for c in range(courses):
            pl.add(GEOM["wall"]["prefab"], float(x), wall_y0 + 2.0 * c,
                   z_close + 2.0, 0.0, why + " back wall")
            walls += 1
    span = (bay_x1 + aw) - (bay_x0 - aw)
    hw = span / 2.0
    xc = (bay_x0 + bay_x1) / 2.0
    eaves_y = DECK_Y + 2.0 * courses
    ncourse = int(math.ceil(hw / GEOM["roof"]["w"]))
    roofs = 0
    for c in range(ncourse):
        dx = hw - GEOM["roof"]["w"] * (c + 0.5)
        y = eaves_y + GEOM["roof"]["rise"] * c
        for z in zs:
            pl.add(GEOM["roof"]["prefab"], xc - dx, y, float(z), 270.0,
                   why + " roof W")
            pl.add(GEOM["roof"]["prefab"], xc + dx, y, float(z), 90.0,
                   why + " roof E")
            roofs += 2
    ridge_y = eaves_y + GEOM["roof"]["rise"] * ncourse
    for z in zs:
        pl.add(GEOM["beam"]["prefab"], xc, ridge_y, float(z), 90.0,
               why + " ridge")
    gable = 0
    for c in range(ncourse):
        y = eaves_y + GEOM["wall"]["half"] + 2.0 * c
        if y > ridge_y:
            break
        halfrun = max(0.0, hw - GEOM["roof"]["w"] * c)
        for x in np.arange(xc - halfrun + 1.0, xc + halfrun, 2.0):
            pl.add(GEOM["wall"]["prefab"], float(x), y, z_close + 2.0, 0.0,
                   why + " gable")
            gable += 1
    # the bay, MEASURED over its WHOLE footprint on a 0.5 x 1.0 m lattice
    clear = bay_x1 - bay_x0
    length = z_close - z_open
    ds = []
    for x in np.arange(bay_x0 + 0.5, bay_x1, 0.5):
        for z in np.arange(z_open, z_close, 1.0):
            ds.append(W.WATER_LEVEL - s.at(float(x), float(z))[0])
    ds = np.asarray(ds)
    fits, refused = [], {}
    for v, g in sorted(WF.VESSELS.items()):
        need = g["draught_m"] + WF.KEEL_CLEARANCE_M
        why_no = []
        if g["hull_w"] > clear - 0.5:
            why_no.append(f"beam {g['hull_w']} > {clear - 0.5:g} m clear less fender")
        if g["hull_l"] > length - 1.0:
            why_no.append(f"length {g['hull_l']} > {length - 1.0:g} m")
        if need > ds.min():
            why_no.append(f"needs {need:g} m, shallowest {ds.min():.3f} m")
        if why_no:
            refused[v] = "; ".join(why_no)
        else:
            fits.append(v)
    best = None
    for v in fits:
        g = WF.VESSELS[v]
        if best is None or (g["draught_m"], g["hull_w"] * g["hull_l"]) > (
                WF.VESSELS[best]["draught_m"],
                WF.VESSELS[best]["hull_w"] * WF.VESSELS[best]["hull_l"]):
            best = v
    return dict(
        bay=dict(clear_width_m=round(clear, 3), length_m=round(length, 3),
                 x=[bay_x0, bay_x1], z=[z_open, z_close],
                 mouth_at="-z", mouth_z=z_open,
                 depth_min_m=round(float(ds.min()), 3),
                 depth_med_m=round(float(np.median(ds)), 3),
                 depth_max_m=round(float(ds.max()), 3),
                 samples=int(ds.size),
                 hull_classes_that_fit=fits, largest=best,
                 hull_classes_refused=refused,
                 why=("the bay's depth is the MINIMUM over its whole "
                      "footprint on a 0.5 x 1.0 m lattice, because a hull "
                      "sits on the shallowest sample under it")),
        aprons=dict(west=west, east=east, back=back),
        walls=walls, gable=gable, roofs=roofs, span_m=round(span, 3),
        eaves_y=round(eaves_y, 3), ridge_y=round(ridge_y, 3),
        roof_courses=ncourse, pieces=len(pl) - n0,
        enclosed=dict(long_walls=2, back_wall=1, gable_filled=True,
                      seaward_gable="OPEN - this is the bay mouth",
                      roof=True))


def boathouse(pl: HC.Plan, s, *, x_centre: float, z_close: float,
              z_open: float, bay_clear: float, bays: int, name: str,
              apron_w: float = APRON_W) -> dict:
    """`bays` enclosed bays side by side, mouths at -z.

    Bays are pitched at `bay_clear + 2 * apron_w` so adjacent aprons are
    DISTINCT tile columns rather than the same one emitted twice.  The wall
    line where two bays meet is emitted by both and de-duplicated here, with
    the count recorded: the alternative, which is what `harbour_city` does, is
    41 pieces at identical (prefab, x, y, z).
    """
    pitch = bay_clear + 2 * apron_w
    x0 = x_centre - (bays * pitch) / 2.0
    before = len(pl.rows)
    out = []
    for k in range(bays):
        a = x0 + k * pitch + apron_w
        out.append(shed(pl, s, bay_x0=a, bay_x1=a + bay_clear,
                        z_close=z_close, z_open=z_open,
                        why=f"{name}/bay{k + 1}", apron_w=apron_w))
    # de-duplicate exact (prefab, x, y, z, yaw) rows produced at a shared edge
    seen, keep, dropped = set(), [], 0
    for r in pl.rows[:before]:
        keep.append(r)
    for r in pl.rows[before:]:
        key = (r[0], round(r[1], 3), round(r[2], 3), round(r[3], 3),
               round(r[4], 1))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        keep.append(r)
    pl.rows = keep
    return dict(name=name, bays=bays, bay_clear_m=bay_clear,
                pitch_m=pitch, x=[x0, x0 + bays * pitch],
                z=[z_open, z_close + 2.0],
                sheds=out, duplicate_rows_dropped=dropped,
                duplicate_why=("two neighbouring bays each emit the wall line "
                               "on the edge they share; the row is identical "
                               "in prefab, position and yaw, so it is emitted "
                               "once. `harbour_city.boathouse` emits the whole "
                               "shared APRON twice - 41 duplicate rows on the "
                               "B plan, 13 wood_floor and 28 wood_pole_log_4"),
                pieces=len(pl) - before)


def gangway(pl: HC.Plan, s, *, kind: str, axis_c: float, width: float,
            a0: float, a1: float, why: str) -> dict:
    """Decking on piles from a shed apron to the first fully dry row.

    `kind` is which way it runs, and it is SOLVED rather than assumed: at
    harbour B the banks run roughly north-south, so the shortest walk to dry
    ground from a shed's landward end is sideways, not up the valley. A
    +z-only gangway refused every one of 6,565 candidate shed seats
    (`no_shore` 86 after the depth gate) and the one it accepted before the
    metric was corrected ended over ground at 29.86 m -- 0.14 m UNDER the
    water plane.

      `z+`  runs from `a0` to `a1` in z, `width` wide in x about `axis_c`
      `x-` / `x+`  runs from `a0` to `a1` in x, `width` wide in z about
                   `axis_c`

    A structure over water that nothing reaches is the `dock-stationhub`
    failure in a different costume: structurally buildable, serves nobody.
    """
    lo, hi = (a0, a1) if a1 >= a0 else (a1, a0)
    if kind == "z+":
        st = HC.deck_strip(pl, s, axis_c - width / 2.0, axis_c + width / 2.0,
                           lo, hi, why=why)
    elif kind in ("x-", "x+"):
        st = HC.deck_strip(pl, s, lo, hi, axis_c - width / 2.0,
                           axis_c + width / 2.0, why=why)
    else:
        raise SystemExit(f"gangway: unknown kind {kind!r}")
    # TRIM THE TILES THAT END UP INSIDE THE BANK.  A gangway must REACH dry
    # ground, so its last row meets grade by construction - but `deck_strip`
    # lays a whole 2 m tile and the shore keeps rising under it, so on a
    # steep bank the row after the landing is buried.  MEASURED before this
    # trim: shed-dockshore 0.453 / 0.531 m, shed-vestvik 0.341 / 0.867 m,
    # shed-northcape 0.780 m of ground ABOVE the tile's top face.  A deck
    # tile under the ground is not a deck tile.  The threshold is the tile's
    # own thickness (`wood_floor` spans y[-0.0329, +0.0969] = 0.1298 m):
    # below that the tile is at grade and is the landing; above it the tile
    # is landfill and is dropped, with its pile stack, and the drop is
    # recorded rather than left to a later audit to find.
    thick = GEOM["floor"]["top"] - GEOM["floor"]["bottom"]
    deck_top = DECK_AUTHOR_Y + GEOM["floor"]["top"]
    buried_xz, trimmed = set(), []
    for r in pl.rows:
        if r[5].startswith(why) and r[0] == GEOM["floor"]["prefab"]:
            bed = s.at(r[1], r[3])[0]
            if bed - deck_top > thick:
                buried_xz.add((round(r[1], 2), round(r[3], 2)))
                trimmed.append(dict(x=r[1], z=r[3], bed=round(bed, 3),
                                    burial_m=round(bed - deck_top, 3)))
    if buried_xz:
        before = len(pl.rows)
        pl.rows = [r for r in pl.rows
                   if not (r[5].startswith(why)
                           and (round(r[1], 2), round(r[3], 2)) in buried_xz)]
        st["trimmed_pieces"] = before - len(pl.rows)
    st["trimmed_tiles"] = trimmed
    st["trim_threshold_m"] = round(thick, 4)
    st["kind"] = kind
    st["axis_c"] = axis_c
    st["width_m"] = width
    st["length_m"] = round(hi - lo, 2)
    st["why"] = ("a shed whose landward apron is over water needs a walkable "
                 "connection to the bank or it berths nothing anybody can "
                 "reach; the direction is the shortest MEASURED run to a "
                 "fully dry row, not the one the geometry suggests")
    return st


# ---------------------------------------------------------------------------
# solving a shed position
# ---------------------------------------------------------------------------

def trim_buried_deck(pl: HC.Plan, s) -> dict:
    """Drop every deck tile that ends up INSIDE the bank, and its piles.

    A deck tile under the ground is not a deck tile.  This applies to the
    WHOLE plan, not just a gangway: a shed whose landward apron sits against
    a steep shore buries its own back apron the same way.  MEASURED before
    this pass: shed-northcape kept one tile with 0.780 m of ground above its
    top face, on a shed that needed no gangway at all.

    The threshold is the tile's OWN thickness - `wood_floor` spans
    y[-0.0329, +0.0969] = 0.1298 m.  Ground above the top face by less than
    that is a tile at grade, which is what a landing is supposed to look
    like; more than that is landfill.
    """
    thick = GEOM["floor"]["top"] - GEOM["floor"]["bottom"]
    deck_top = DECK_AUTHOR_Y + GEOM["floor"]["top"]
    buried, rows = set(), []
    for r in pl.rows:
        if r[0] != GEOM["floor"]["prefab"]:
            continue
        bed = s.at(r[1], r[3])[0]
        if bed - deck_top > thick:
            buried.add((round(r[1], 2), round(r[3], 2)))
            rows.append(dict(x=r[1], z=r[3], bed=round(bed, 3),
                             burial_m=round(bed - deck_top, 3)))
    before = len(pl.rows)
    if buried:
        pl.rows = [r for r in pl.rows
                   if (round(r[1], 2), round(r[3], 2)) not in buried
                   or r[0] not in (GEOM["floor"]["prefab"],
                                   GEOM["pile"]["prefab"])]
    return dict(tiles_trimmed=len(rows), pieces_removed=before - len(pl.rows),
                threshold_m=round(thick, 4), deck_top_y=round(deck_top, 4),
                trimmed=rows,
                why=("a deck tile whose top face is under the ground by more "
                     "than the tile's own thickness is landfill, not decking; "
                     "the pile stack under it goes with it"))


def solve_shed(s, fld, *, bays: int, bay_clear: float, bay_len: float,
               need_m: float, x_range: tuple[float, float],
               z_range: tuple[float, float], apron_w: float = APRON_W,
               shore_max_m: float = 24.0, step: float = 1.0,
               gangway_w: float = 4.0,
               avoid: list[dict] | None = None) -> dict:
    """Where the shed goes, decided by depth AND by reach.

    Four gates, each counted separately so a refusal carries its number:
      bay_shallow    the shallowest sample under a bay footprint misses
                     `need_m`
      bay_not_open   a bay sample is not 4-connected to open sea at that depth
      apron_dry      an apron or pier column would be driven into dry ground -
                     a pier on a lawn
      no_shore       no dry ground within `shore_max_m` of the landward end,
                     so no gangway can reach it
    Scored on (shortest gangway, then deepest bay): a shed against the bank
    beats a deeper one in the middle of the fjord, because the second one is
    scenery.
    """
    H = np.asarray(fld.heights, dtype=np.float64)
    n, x0, z0 = fld.n, fld.x0, fld.z0
    _deep, openm = WF.sea_mask(fld, need_m)
    dry = H > W.WATER_LEVEL + 0.5

    def jx(x):
        return int(round(x - x0))

    def iz(z):
        return int(round(z - z0))

    pitch = bay_clear + 2 * apron_w
    span = bays * pitch
    best, tried = None, 0
    rej = dict(offpatch=0, bay_shallow=0, bay_not_open=0, apron_dry=0,
               no_shore=0, hits_existing=0)
    av = avoid or []
    for xc in np.arange(x_range[0], x_range[1] + 0.01, step):
        for zc in np.arange(z_range[0], z_range[1] + 0.01, step):
            tried += 1
            z_open = zc - bay_len
            xa = xc - span / 2.0
            ja, jb = jx(xa), jx(xa + span)
            ia, ib = iz(z_open), iz(zc + 2.0)
            if ja < 0 or ia < 0 or jb >= n or ib >= n:
                rej["offpatch"] += 1
                continue
            # WHAT IS ALREADY STANDING HERE.  `place_missing` matches on
            # prefab AND position, so an existing pier two metres away is
            # invisible to it and our tile lands on top.  The avoid capsules
            # are the recorded geometry of the structure this shed serves;
            # the LIVE footprint census before the write is the second,
            # independent instrument on the same question.
            if av and any(
                    abs(a["x"] - xc) < a["r"] + span / 2.0
                    and abs(a["z"] - (zc + z_open) / 2.0)
                    < a["r"] + (bay_len + 2.0) / 2.0 for a in av):
                rej["hits_existing"] += 1
                continue
            depths, opens, apron_ok = [], [], True
            for k in range(bays):
                a = xa + k * pitch + apron_w
                b = a + bay_clear
                blk = H[iz(z_open):iz(zc) + 1, jx(a):jx(b) + 1]
                depths.append(float((W.WATER_LEVEL - blk).min()))
                opens.append(bool(openm[iz(z_open):iz(zc) + 1,
                                        jx(a):jx(b) + 1].all()))
            if min(depths) < need_m:
                rej["bay_shallow"] += 1
                continue
            if not all(opens):
                rej["bay_not_open"] += 1
                continue
            for k in range(bays + 1):
                ax = xa + k * pitch if k < bays else xa + span - apron_w
                if k and k < bays:
                    ax = xa + k * pitch - apron_w
                blk = H[iz(z_open):iz(zc) + 1, jx(ax):jx(ax + apron_w) + 1]
                if float((W.WATER_LEVEL - blk).max()) < 0.4:
                    apron_ok = False
                    break
            if not apron_ok:
                rej["apron_dry"] += 1
                continue
            # THE GANGWAY, MEASURED AS THE RUN THAT WILL ACTUALLY BE BUILT,
            # IN EVERY DIRECTION THE BUILDER CAN BUILD IT.
            # A distance transform answers "how far is dry ground in ANY
            # direction" and the builder lays decking along ONE axis.
            # MEASURED on the first solve: the transform said 14.73 m, the
            # +z run on that axis is 19.73 m, and the 14.73 m gangway it
            # authorised ended over ground at 29.86 m -- 0.14 m UNDER the
            # water plane. Restricting the search to +z then refused all
            # 6,565 candidates, because the banks of this fjord run
            # north-south and the shore is SIDEWAYS from a shed, not up the
            # valley. So all three runs the builder can lay are measured and
            # the shortest wins.
            best_gw = None       # (run, kind, axis_c, a0, a1)
            for axc in np.arange(xa + gangway_w / 2.0,
                                 xa + span - gangway_w / 2.0 + 0.01, 2.0):
                ja2, jb2 = jx(axc - gangway_w / 2.0), jx(axc + gangway_w / 2.0)
                if ja2 < 0 or jb2 >= n:
                    continue
                for zz in np.arange(zc + 2.0, zc + 2.0 + shore_max_m + 0.01,
                                    1.0):
                    i = iz(zz)
                    if i >= n:
                        break
                    if dry[i, ja2:jb2 + 1].all():
                        run = float(zz - (zc + 2.0))
                        if best_gw is None or run < best_gw[0]:
                            best_gw = (run, "z+", float(axc),
                                       float(zc + 2.0), float(zz + 2.0))
                        break
            for azc in np.arange(z_open + gangway_w / 2.0,
                                 zc - gangway_w / 2.0 + 0.01, 2.0):
                ia2, ib2 = iz(azc - gangway_w / 2.0), iz(azc + gangway_w / 2.0)
                if ia2 < 0 or ib2 >= n:
                    continue
                for sign, edge in ((-1.0, xa), (1.0, xa + span)):
                    for dd in np.arange(1.0, shore_max_m + 0.01, 1.0):
                        xx = edge + sign * dd
                        j = jx(xx)
                        if j < 0 or j >= n:
                            break
                        if dry[ia2:ib2 + 1, j].all():
                            if best_gw is None or dd < best_gw[0]:
                                best_gw = (float(dd),
                                           "x-" if sign < 0 else "x+",
                                           float(azc), float(edge),
                                           float(xx + sign * 2.0))
                            break
            if best_gw is None:
                rej["no_shore"] += 1
                continue
            gw = best_gw[0]
            score = (-round(gw, 1), round(min(depths), 2))
            if best is None or score > best["score"]:
                best = dict(score=score, x_centre=float(xc),
                            z_close=float(zc), z_open=float(z_open),
                            span_m=float(span), pitch_m=float(pitch),
                            bay_min_depth_m=[round(d, 3) for d in depths],
                            gangway_m=round(gw, 2), gangway_w_m=gangway_w,
                            gangway_kind=best_gw[1], gangway_axis=best_gw[2],
                            gangway_a0=best_gw[3], gangway_a1=best_gw[4])
    return dict(best=best, tried=tried, refusals=rej, bays=bays,
                bay_clear_m=bay_clear, bay_len_m=bay_len, need_m=need_m,
                span_m=span,
                why=("scored on the SHORTEST gangway first and the deepest "
                     "bay second: `harbour_city.solve_boathouse` scores on "
                     "depth alone and put harbour B's boathouse 26-45 m from "
                     "either bank with nothing between"))


# ---------------------------------------------------------------------------
# berths, solved ALONGSIDE a real deck face
# ---------------------------------------------------------------------------

def solve_berths_alongside(s, deck_xz: list[tuple[float, float]], *,
                           x_range, z_range, want: str = "VikingShip",
                           bearing: float = 0.0, n: int = 3,
                           apart_m: float = 24.0, step: float = 2.0,
                           face_max_m: float = 3.0) -> dict:
    """Berths that float the hull AND are a stride from a planned deck tile.

    Two gates, because either alone passes a defect: `floats` is the
    shallowest sample under the hull's own rotated ellipse, flood-fill
    connected to open sea; `boardable` is a deck tile within `face_max_m` of
    the hull's own SIDE at that berth's latitude.  MEASURED on `design_b`'s
    own output: its third berth floats every hull in the table and is 8.06 m
    from the nearest deck tile.
    """
    g = WF.VESSELS[want]
    need = g["draught_m"] + WF.KEEL_CLEARANCE_M
    half_beam = g["hull_w"] / 2.0
    deck = np.asarray(deck_xz, dtype=np.float64) if deck_xz else None
    cands = []
    rej = dict(no_footprint=0, too_shallow=0, not_connected=0, not_boardable=0)
    for x in np.arange(x_range[0], x_range[1] + 0.01, step):
        for z in np.arange(z_range[0], z_range[1] + 0.01, step):
            hull = WF.hull_depths(s, bearing, float(x), float(z), want)
            if hull is None:
                rej["no_footprint"] += 1
                continue
            if not hull[2]:
                rej["not_connected"] += 1
                continue
            if hull[0] < need + WF.BERTH_MARGIN_FLOOR_M:
                rej["too_shallow"] += 1
                continue
            if deck is None:
                rej["not_boardable"] += 1
                continue
            # the hull's two sides at this berth's latitude
            dz = np.abs(deck[:, 1] - z)
            band = deck[dz <= g["hull_l"] / 2.0]
            if band.size == 0:
                rej["not_boardable"] += 1
                continue
            gap = float(np.min(np.minimum(
                np.abs(band[:, 0] - (x - half_beam)),
                np.abs(band[:, 0] - (x + half_beam)))))
            if gap > face_max_m:
                rej["not_boardable"] += 1
                continue
            cands.append((float(hull[0]), -gap, float(x), float(z),
                          int(hull[3]), gap))
    cands.sort(reverse=True)
    picked = []
    for depth, _ng, x, z, nsamp, gap in cands:
        if any(math.hypot(x - p["x"], z - p["z"]) < apart_m for p in picked):
            continue
        picked.append(dict(x=x, z=z, shallowest_under_hull_m=round(depth, 3),
                           hull_samples=nsamp, bearing_deg=bearing,
                           required_m=round(need, 2),
                           margin_m=round(depth - need, 3), sized_for=want,
                           gap_to_deck_face_m=round(gap, 2), boardable=True))
        if len(picked) >= n:
            break
    return dict(berths=picked, candidates=len(cands), refusals=rej,
                sized_for=want, required_m=round(need, 2),
                margin_floor_m=WF.BERTH_MARGIN_FLOOR_M,
                face_max_m=face_max_m)


# ---------------------------------------------------------------------------
# the village bodies, terraced
# ---------------------------------------------------------------------------

def fit_village(s, q: dict, mix: list[str], taken: list[dict], *,
                cut_max: float = BODY_CUT_MAX_M, lattice: float = 2.0,
                keepout: list[dict] | None = None,
                cap: dict[str, int] | None = None,
                gate=None,
                road_ground: set | None = None) -> tuple[list[dict], dict]:
    """`harbour_city.fit_quarter` with the terrace ceiling as the clamp.

    The only change from that function is the cut ceiling: it accepts
    `CUT_MAX_M` = 7.0, which is the TERRAIN CLAMP and not a buildability
    limit, and it writes no terrain, so a 7.0 m pad seats a hall with 4.7 m of
    ground through the floor.  Here the ceiling is the terrace we are willing
    to cut, and the cut is then actually written.
    """
    out: list[dict] = []
    why = dict(clamp=0, wet=0, collide=0, keepout=0, piece_reach=0,
               offpatch=0, road_ground=0, placed=0)
    grid = HC.Grid(s, (q["x"][0] + q["x"][1]) / 2.0,
                   (q["z"][0] + q["z"][1]) / 2.0)
    used: dict[str, int] = {}
    for prefab in mix:
        limit = (cap or {}).get(prefab, 99)
        cz = q["z"][0]
        while cz <= q["z"][1]:
            cx = q["x"][0]
            while cx <= q["x"][1]:
                if used.get(prefab, 0) >= limit:
                    cx += lattice
                    continue
                try:
                    seat = HC.house_seat(s, prefab, cx, cz, q["yaw"], grid=grid)
                except SystemExit:
                    why["offpatch"] += 1
                    cx += lattice
                    continue
                pad = seat["pad"]
                if pad["wet_samples"]:
                    why["wet"] += 1
                    cx += lattice
                    continue
                if pad["cut_needed_m"] > cut_max:
                    why["clamp"] += 1
                    cx += lattice
                    continue
                rw, rd = seat["rotated_w"], seat["rotated_d"]
                if any(abs(cx - o["cx"]) < (rw + o["rw"]) / 2 + HC.STREET_CLEAR_M
                       and abs(cz - o["cz"]) < (rd + o["rd"]) / 2 + HC.STREET_CLEAR_M
                       for o in taken + out):
                    why["collide"] += 1
                    cx += lattice
                    continue
                # THE STAND-OFF IS MEASURED AGAINST THE CLEARING CYLINDER,
                # NOT AGAINST THE BODY.  The irreversible op here is
                # `objects_remove` over a disc of the pad's own half-diagonal
                # plus `area.DEFAULT_MARGIN_M`, and the gate that fires at
                # write time compares THAT radius against each marker's
                # measured piece reach.  MEASURED on the first dry run:
                # `sudrvik-vestrbakki-2` cleared a 32 m disc test on its body
                # half-width and was then REFUSED at write time -- WoodFarm1
                # at 30.59 m with a 32.0 m reach against a 15.13 m cylinder,
                # room -2.41 m -- after its `zones_generate` had already run.
                # So the design uses the write-time geometry.
                # A PAD IS NOT WRITTEN OVER A CARRIAGEWAY.  `road_ground` is
                # every sample a road AUTHORED in this quarter's zones, read
                # off the chain (`terrace.road_ground_samples`); a seat whose
                # levelled rectangle (and the 1 m the engine's bilinear blend
                # spreads over) stands on one is not a seat.  MEASURED on the
                # first offline build of this village after the regeneration:
                # without it two terraces re-authored 165 samples of R2.
                if road_ground and TR.touches(road_ground, cx, cz,
                                              pad["w"], pad["d"]):
                    why["road_ground"] += 1
                    cx += lattice
                    continue
                clear_r = math.hypot(pad["w"], pad["d"]) / 2.0 + 2.0
                if keepout and any(
                        math.hypot(cx - k["x"], cz - k["z"]) < k["r"] + clear_r
                        for k in keepout):
                    why["keepout"] += 1
                    cx += lattice
                    continue
                # AND IT ASKS THE GATE ITSELF, per candidate, rather than
                # predicting its verdict: `clear_radius_room` reads the
                # mod-free dump's PER-TYPE measured reach, which the live
                # marker sweep above cannot supply.  Two marker sets, two
                # different questions; a seat has to satisfy both.
                if gate is not None:
                    room = gate(cx, cz)
                    if clear_r > room["max_radius_m"]:
                        why["piece_reach"] += 1
                        cx += lattice
                        continue
                out.append(dict(seat, quarter=q["id"], cx=cx, cz=cz,
                                rw=rw, rd=rd,
                                clear_radius_m=round(clear_r, 2)))
                why["placed"] += 1
                used[prefab] = used.get(prefab, 0) + 1
                cx += lattice
            cz += lattice
    return out, why


def terrace_units(seats: list[dict]) -> list[dict]:
    """One build unit per body, in the shape `build.py` levels a pad in.

    The pad is the one `house_seat` measured against: `flatten.
    pad_extent_at_yaw` of the body at its yaw plus `HC.SEAT_MARGIN_M` a side;
    `target_y` is that footprint's MEDIAN, which is
    the height that minimises the worst cut on a monotone slope.
    """
    out = []
    for i, st in enumerate(seats):
        pad = st["pad"]
        out.append(dict(
            id=f"sudrvik-{st['quarter']}-{i + 1}",
            prefab=st["prefab"], x=st["cx"], z=st["cz"], yaw=st["yaw"],
            body_x=st["x"], body_z=st["z"],
            pad_w=round(pad["w"], 1), pad_d=round(pad["d"], 1),
            foot_w=round(st["rw"], 2), foot_d=round(st["rd"], 2),
            target_y=pad["floor_y"], ground_y=pad["floor_y"],
            cut_needed_m=pad["cut_needed_m"],
            min_h=pad["min_h"], max_h=pad["max_h"], samples=pad["samples"]))
    return out


# ---------------------------------------------------------------------------
# the design
# ---------------------------------------------------------------------------

def design(s) -> dict:
    fld = field(s)
    pl = HC.Plan(SITE)
    out: dict = dict(site=SITE, hand=HAND, water_level_y=W.WATER_LEVEL,
                     deck_datum_y=DECK_Y,
                     curtain_wall=None,
                     curtain_refused=dict(
                         shortest_closing_line_z=440.0, span_m=113.0,
                         encloses_m2=4326, navigable_m2=2762,
                         lines_that_do_not_close="z 450..498",
                         why=("113.0 m of curtain to enclose 4,326 m2 is a "
                              "bad trade for a village, and the operator "
                              "ruled the wall is at A only. Refused with the "
                              "number rather than built small.")))

    # --- the quays, on the measured waterline -----------------------------
    out["quay_west"] = HC.quay(pl, s, z0=452.0, z1=490.0, side="west",
                               x_centre=1196.0, why="sudrvik/quay-west")
    out["quay_east"] = HC.quay(pl, s, z0=452.0, z1=490.0, side="east",
                               x_centre=1196.0, why="sudrvik/quay-east")

    # --- the boathouse, mouth to -z, against the bank ---------------------
    sol = solve_shed(s, fld, bays=2, bay_clear=BIG_BAY["clear"],
                     bay_len=BIG_BAY["length"], need_m=BIG_BAY["need_m"],
                     x_range=(1150.0, 1250.0), z_range=(436.0, 500.0),
                     shore_max_m=24.0)
    out["boathouse_solve"] = sol
    if not sol["best"]:
        raise SystemExit(
            f"REFUSING: no 2 x {BIG_BAY['clear']} x {BIG_BAY['length']} m "
            f"boathouse position at harbour B clears {BIG_BAY['need_m']} m "
            f"under both bays AND reaches dry ground within 16 m. "
            f"refusals {sol['refusals']} over {sol['tried']} candidates.")
    bb = sol["best"]
    out["boathouse"] = boathouse(pl, s, x_centre=bb["x_centre"],
                                 z_close=bb["z_close"], z_open=bb["z_open"],
                                 bay_clear=BIG_BAY["clear"], bays=2,
                                 name="sudrvik/boathouse")
    if bb["gangway_m"] > 1.5:
        out["gangway"] = gangway(pl, s, kind=bb["gangway_kind"],
                                 axis_c=bb["gangway_axis"],
                                 width=bb["gangway_w_m"],
                                 a0=bb["gangway_a0"], a1=bb["gangway_a1"],
                                 why="sudrvik/gangway")
    else:
        out["gangway"] = dict(
            tiles=0, pieces=0, kind=bb["gangway_kind"],
            why=(f"the apron is {bb['gangway_m']} m from dry ground, inside "
                 f"one deck tile; no gangway is needed and none is built"))

    # --- the bodies, terraced --------------------------------------------
    keep = HC.location_keepout()
    out["location_keepout"] = dict(markers=len(keep),
                                   standoff_m=HC.LOC_STANDOFF_M,
                                   dump=str(HC.LOC_MARKERS))
    taken: list[dict] = []
    quarters = []
    # THE WRITE-TIME GATE ITSELF, as a callable, so the design cannot propose
    # a seat the build will refuse after it has already generated the zone.
    import build as BB          # noqa: PLC0415
    import clearance           # noqa: PLC0415
    _loc = clearance.load(BB.locations_dump())
    out["piece_reach_gate"] = dict(
        dump=BB.locations_dump(),
        what=("`settlements/build.py::clear_radius_room`, the HARD gate on "
              "`objects_remove`, called per candidate seat rather than "
              "predicted"))

    def _gate(cx: float, cz: float) -> dict:
        return BB.clear_radius_room(_loc, cx, cz)

    # THE LIVE MARKER SWEEP IS A SCREEN, NOT THE GATE, and that distinction
    # is the project's own rule 7: a screen proposes, a second independent
    # instrument concludes.  An UNIDENTIFIED live LocationProxy can only be
    # given the world-wide WORST CASE reach (32.0 m), and 32.0 m plus a 15 m
    # clearing cylinder is a 47 m disc round a marker whose pieces may be
    # nowhere near it.  MEASURED here: run as a gate it refuses the whole
    # `vestrhlid` quarter - 2,027 candidate seats, 0 placed - on two markers,
    # WoodFarm1 at (1136.74, 528.93) and ShipSetting01 at (1144.52, 573.50),
    # neither of which is identified and neither of which has been asked what
    # it actually contains.  The same worst case once refused Stenvik's hall
    # on a marker 23.9 m away whose pieces were nowhere near the pad.
    #
    # So the gates that DECIDE are:
    #   * `clear_radius_room` on the mod-free dump, which carries a MEASURED
    #     per-type reach - kept, armed, and asked per candidate below;
    #   * the LIVE contents of the exact cylinder `objects_remove` will empty
    #     - `preflight_seats`, run read-only before the first write, which is
    #     a fact about the objects that would be deleted rather than an
    #     inference from a distance.
    # The live marker distances are still REPORTED per seat, because a
    # screen's output is evidence even when it is not a verdict.
    # THE LANDING IS THE FIRST THING THE VILLAGE RESERVES: the square its
    # roads arrive on (`harbour_site.measure` published it, the roads were
    # routed to it), so no body seat may share it, and the ground those roads
    # authored is read off the chain so no seat is written over a carriageway.
    landing = TR.landing_unit(TR.declared_record(SITE))
    reserved = [dict(cx=landing["x"], cz=landing["z"], rw=landing["pad_w"],
                     rd=landing["pad_d"])]
    qx0 = min(q["x"][0] for q in QUARTERS)
    qx1 = max(q["x"][1] for q in QUARTERS)
    qz0 = min(q["z"][0] for q in QUARTERS)
    qz1 = max(q["z"][1] for q in QUARTERS)
    import tcdata as _TC            # noqa: PLC0415  terraform/
    zx0, zz0 = _TC.zone_of(qx0 - 2.0, qz0 - 2.0)
    zx1, zz1 = _TC.zone_of(qx1 + 2.0, qz1 + 2.0)
    road_ground = TR.road_ground_samples(
        AP.Applied(actor=ACTOR),
        [(zx, zz) for zx in range(zx0, zx1 + 1) for zz in range(zz0, zz1 + 1)])
    out["road_ground"] = dict(
        samples=len(road_ground), zones=(zx1 - zx0 + 1) * (zz1 - zz0 + 1),
        why=("every sample a road AUTHORED (`roads/applied.py::Applied.zone` "
             "`author`) in the zones the quarters span, off the chain"))
    for q in QUARTERS:
        seats, why = fit_village(s, q, HC.MIX_VILLAGE, reserved + taken,
                                 cap=HC.CAP_VILLAGE, keepout=None, gate=_gate,
                                 road_ground=road_ground)
        for st in seats:
            near = min(((math.hypot(st["cx"] - k["x"], st["cz"] - k["z"]), k)
                        for k in keep), default=(None, None))
            st["nearest_live_marker_m"] = (None if near[0] is None
                                           else round(near[0], 2))
            st["nearest_live_marker"] = (None if near[1] is None
                                         else [near[1]["x"], near[1]["z"],
                                               near[1].get("identified")])
        taken += seats
        quarters.append(dict(q, seats=seats, refusals=why))
    out["quarters"] = quarters
    out["terrace_cut_max_m"] = BODY_CUT_MAX_M
    for qq in quarters:
        for st in qq["seats"]:
            pl.add(st["prefab"], st["x"], st["y"], st["z"], st["yaw"],
                   f"sudrvik/body/{qq['id']}")
    out["bodies"] = sum(len(q["seats"]) for q in quarters)
    units = [landing] + terrace_units(taken)
    out["landing"] = landing
    out["_units"] = units
    out["terraces"] = [dict(id=u["id"], x=u["x"], z=u["z"],
                            pad=[u["pad_w"], u["pad_d"]],
                            target_y=u["target_y"], cut_m=u["cut_needed_m"])
                       for u in units]

    # --- lighting: site_prop, NOT road_lighting ---------------------------
    # A quay is not a road.  `roads/lamps.py` sites against a road centreline
    # with biome minima (Meadows 55 m) tuned to a carriageway's sightline; a
    # 4 m quay deck lit at 55 m is a dark quay.  These are the structure's own
    # lamps at the dock pitch, seated on the DECK's walking surface with
    # `piece_groundtorch`'s own +0.65359 offset.
    out["lighting"] = quay_lighting(pl, out, why="sudrvik/lamp")

    # --- guideposts -------------------------------------------------------
    out["guideposts"] = [
        HC.guidepost(pl, s, 1216.0, 498.0, "Sudrvik  -  Harbour", 180.0,
                     "sudrvik/guidepost-harbour"),
        HC.guidepost(pl, s, 1172.0, 498.0, "Sudrvik  |  Ulfsgard", 0.0,
                     "sudrvik/guidepost-road"),
    ]

    # --- berths, alongside a real deck face -------------------------------
    deck = [(r[1], r[3]) for r in pl.rows if r[0] == GEOM["floor"]["prefab"]]
    out["berth_solve"] = solve_berths_alongside(
        s, deck, x_range=(1160.0, 1250.0), z_range=(424.0, 492.0),
        want="VikingShip", n=3, apart_m=24.0)
    out["berths"] = HC.berth_table(
        s, [(p["x"], p["z"], f"solved berth {i + 1}", 0.0)
            for i, p in enumerate(out["berth_solve"]["berths"])])

    out["deck_trim"] = trim_buried_deck(pl, s)

    # ONE GLOBAL DE-DUPLICATION, RECORDED.  Two builders can independently
    # emit the same piece at the same place -- a gangway tile abutting an
    # apron, two bays sharing a wall line.  An exact (prefab, x, y, z, yaw)
    # repeat is not a second piece, it is the same piece sent twice, and
    # `place_missing` would match the first against the world and then place
    # the second on top of it.  MEASURED on `harbour_city.design_b`: 41 such
    # rows, which is how 64 pieces once got placed twice on this world.
    seen, keep_rows, dropped = set(), [], 0
    for r in pl.rows:
        k = (r[0], round(r[1], 3), round(r[2], 3), round(r[3], 3),
             round(r[4], 1))
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        keep_rows.append(r)
    pl.rows = keep_rows
    out["duplicate_rows_dropped_total"] = dropped

    clash = TR.pieces_on_landing(pl.rows, landing)
    if clash:
        raise SystemExit(
            f"REFUSING {SITE}: {len(clash)} planned piece(s) stand on its "
            f"landing {landing['id']} ({landing['pad_w']:g} m square at "
            f"({landing['x']:g}, {landing['z']:g})), e.g. {clash[:3]}. The "
            f"landing is levelled before any piece stands; re-solve the "
            f"design off it.")
    out["_plan"] = pl
    out["plan_pieces"] = pl.counts()
    out["plan_total"] = len(pl)
    out["plan_extent"] = pl.extent()
    # THE KEEP-OUT THE ROAD ROUTER IS FED BEFORE IT ROUTES.  The union of the
    # ACTUAL pads and the actual waterfront footprint, never one district
    # disc: a 100 m `pad_radius_m` read as a footprint once made a road refuse
    # to write for 294 m, and a single disc round a 46 m ragged quay is that
    # same mistake at a smaller scale.  The deck is covered by a greedy
    # 10 m-separated cover of 7 m discs over its OWN tile positions.
    # The LANDING is deliberately NOT in the keep-out. It is levelled
    # ground with nothing standing on it, and it exists precisely so a road
    # can arrive there; a disc over it would tell the next router to avoid
    # the one square of this village that was built for it.
    ko = [dict(x=u["x"], z=u["z"],
               r=round(max(u["pad_w"], u["pad_d"]) / 2.0 + 4.0, 2),
               what=f"village body {u['id']} ({u['prefab']})")
          for u in units if not u.get("landing")]
    bhx = out["boathouse"]["x"]
    bhz = out["boathouse"]["z"]
    ko.append(dict(x=round((bhx[0] + bhx[1]) / 2.0, 2),
                   z=round((bhz[0] + bhz[1]) / 2.0, 2),
                   r=round(math.hypot(bhx[1] - bhx[0], bhz[1] - bhz[0]) / 2.0
                           + 4.0, 2),
                   what="boathouse (2 enclosed bays) and its aprons"))
    deck_ko: list[tuple[float, float]] = []
    for x, z in sorted({(round(r[1], 2), round(r[3], 2)) for r in pl.rows
                        if r[0] == GEOM["floor"]["prefab"]}):
        if any(math.hypot(x - a, z - b) < 10.0 for a, b in deck_ko):
            continue
        deck_ko.append((x, z))
    ko += [dict(x=x, z=z, r=7.0, what="quay / apron / gangway decking")
           for x, z in deck_ko]
    out["road_keepout"] = ko
    return out


def quay_lighting(pl: HC.Plan, out: dict, why: str) -> dict:
    """A torch every 12 m along each deck run, on the deck, at its own offset.

    `site_prop`, not `road_lighting`: the canonical road gate
    (`roads/lamps.py::site`) sites against a carriageway centreline with biome
    minimum spacings - Meadows 55 m - chosen for a road's sightline.  A 4 m
    quay deck lit every 55 m is an unlit quay, and widening a biome minimum to
    fix that would corrupt the road gate's own census.  Two different fixtures,
    two different gates; this one is the structure's own.
    """
    n0 = len(pl)
    deck = sorted({(round(r[1], 2), round(r[3], 2)) for r in pl.rows
                   if r[0] == GEOM["floor"]["prefab"]})
    seats: list[tuple[float, float]] = []
    for x, z in deck:
        if any(math.hypot(x - a, z - b) < TORCH_PITCH_M for a, b in seats):
            continue
        seats.append((x, z))
    for x, z in seats:
        pl.add(HC.TORCH, x, DECK_Y + TORCH_ABOVE_SURFACE, z, 0.0, why)
    return dict(torches=len(seats), pitch_m=TORCH_PITCH_M,
                seat_y=round(DECK_Y + TORCH_ABOVE_SURFACE, 5),
                role="site_prop",
                seated_on="the deck's 30.6 m walking surface",
                pivot_offset_m=TORCH_ABOVE_SURFACE,
                deck_tiles=len(deck), pieces=len(pl) - n0,
                why=("greedy 12 m minimum separation over the deck tile set, "
                     "so every run is lit at the dock pitch and no tile "
                     "carries two torches"))


# ---------------------------------------------------------------------------
# the SMALL enclosed boathouses at the existing waterfront sites
# ---------------------------------------------------------------------------
#
# Each entry names the STANDING site it serves, the search window, and the
# direction its own water lies in.  Nothing here is hand-placed: the window is
# the search space and `solve_shed` picks the seat.
# THE STANDING STRUCTURE AT EACH SITE, AS AN AVOID CAPSULE.  A new shed that
# lands on an existing pier is a collision `place_missing` cannot see: it
# matches on prefab AND position, so somebody else's `wood_floor` two metres
# away is invisible to it and our tile goes in on top.  MEASURED roots and
# bearings from `waterfront.SITES` / `NEW_SITES` and `LIGHTHOUSE`; the head is
# root + length along the bearing, with `assemble`'s own (sin, cos) sense.
# Capsule radius 6.0 m = a 3-tile pier's 3 m half-width plus 3 m of clearance.
SHED_SITES = [
    dict(id="shed-temple-strait", serves="boathouse-temple-strait",
         centre=(-306.0, -110.0), x_range=(-350.0, -262.0),
         z_range=(-160.0, -70.0),
         avoid=[dict(root=(-305.98, -110.4), bearing=190.0, length=24.0,
                     r=8.0, what="the 24 m slip and its two finger piers")],
         why="the strait crossing: 171 objects of decking and a LittleBoat"),
    dict(id="shed-dockshore", serves="dock-dockshore",
         centre=(706.0, -107.0), x_range=(630.0, 755.0),
         z_range=(-165.0, -55.0),
         avoid=[dict(root=(675.4, -85.6), bearing=130.0, length=40.0, r=6.0,
                     what="the 40 m jetty, root to head (706.0, -111.3)")],
         why="the south-east shore dock: 140 objects and a VikingShip"),
    dict(id="shed-vestvik", serves="harbour-vestvik",
         centre=(-1038.0, 1662.0), x_range=(-1080.0, -1000.0),
         z_range=(1620.0, 1705.0),
         avoid=[dict(root=(-1038.22, 1661.95), bearing=305.0, length=22.0,
                     r=6.0, what="the 22 m jetty and the berthed CargoShip")],
         why="the north-west village harbour: 184 objects and a CargoShip"),
    dict(id="shed-northcape", serves="dock-northcape",
         centre=(-174.0, 2625.0), x_range=(-230.0, -100.0),
         z_range=(2570.0, 2690.0),
         hold=("HELD, and the hold is a MEASUREMENT not a caution. Two "
              "independent faults, either of which alone is disqualifying. "
              "(1) BIOME SPLIT: zone (-2,42) has 3,414 of its 4,225 samples "
              "in the biome that is NOT in the majority at its four corners, "
              "so the immunity predicate - all four corner biomes equal AND "
              "every interior sample the same biome - fails, and "
              "HeightmapBuilder's corner blend is what the engine draws. "
              "(2) WRONG SURFACE: this site's shore end was solved against "
              "WorldGenerator.GetHeight, worst error 9.46 m under a real "
              "piece. Building on the old numbers would repeat wt-peak "
              "exactly. The fix is a PATCHSCAN_SURFACE=heightmap re-scan of "
              "the `north` patch and a re-solve, not a nudge."),
         avoid=[dict(root=(-192.0, 2612.0), bearing=60.0, length=48.0, r=6.0,
                     what="the 48 m pier, root to head (-150.4, 2636.0)")],
         why=("the north cape: a 127-piece pier, a lighthouse and a berthed "
              "CargoShip at 3.038 m, and 1.7 km from the nearest road - the "
              "one place on this world where a small hull for a short trip "
              "is the ONLY way to move")),
]


def avoid_discs(site: dict) -> list[dict]:
    """A capsule sampled every 4 m into discs the solver can test cheaply."""
    out = []
    for cap in site.get("avoid", []):
        ux, uz = A._unit(cap["bearing"])
        for dd in np.arange(0.0, cap["length"] + 0.01, 4.0):
            out.append(dict(x=cap["root"][0] + ux * dd,
                            z=cap["root"][1] + uz * dd, r=cap["r"],
                            what=cap["what"]))
    return out


def solve_small_sheds(s, *, bays: int = 1) -> dict:
    """Solve a small enclosed boathouse at every candidate waterfront site.

    A site that does not clear the depth floor is REFUSED WITH ITS NUMBERS
    rather than shrunk to fit: the small bay is already the smallest honest
    one, and a bay a boat cannot enter is the defect this whole exercise is
    correcting.
    """
    rows = []
    for site in SHED_SITES:
        fld = s.patch_for(site["centre"][0], site["centre"][1], margin=2.0)
        best = None
        for b in (2, 1):
            if b > bays and bays:
                pass
            sol = solve_shed(s, fld, bays=b, bay_clear=SMALL_BAY["clear"],
                             bay_len=SMALL_BAY["length"],
                             need_m=SMALL_BAY["need_m"],
                             x_range=site["x_range"], z_range=site["z_range"],
                             shore_max_m=16.0, avoid=avoid_discs(site))
            rows.append(dict(site=site["id"], serves=site["serves"], bays=b,
                             solve=sol))
            if sol["best"] and best is None:
                best = (b, sol)
        site["_best"] = best
    return dict(sites=SHED_SITES, solves=rows,
                bay=SMALL_BAY,
                why=("two bays first, one bay as the fallback; the first that "
                     "clears the depth floor AND reaches dry ground wins, and "
                     "every refusal keeps its counts"))


def design_small_shed(s, site: dict, sol: dict, bays: int) -> dict:
    pl = HC.Plan(site["id"])
    bb = sol["best"]
    out = dict(site=site["id"], serves=site["serves"], solve=sol,
               bay=SMALL_BAY, hand=dict(mouth="-z"))
    out["boathouse"] = boathouse(pl, s, x_centre=bb["x_centre"],
                                 z_close=bb["z_close"], z_open=bb["z_open"],
                                 bay_clear=SMALL_BAY["clear"], bays=bays,
                                 name=f"{site['id']}/shed")
    if bb["gangway_m"] > 1.5:
        out["gangway"] = gangway(pl, s, kind=bb["gangway_kind"],
                                 axis_c=bb["gangway_axis"],
                                 width=bb["gangway_w_m"],
                                 a0=bb["gangway_a0"], a1=bb["gangway_a1"],
                                 why=f"{site['id']}/gangway")
    else:
        out["gangway"] = dict(tiles=0, pieces=0, kind=bb["gangway_kind"],
                              why=f"dry ground is {bb['gangway_m']} m away")
    out["deck_trim"] = trim_buried_deck(pl, s)
    out["lighting"] = quay_lighting(pl, out, why=f"{site['id']}/lamp")
    out["_plan"] = pl
    out["plan_pieces"] = pl.counts()
    out["plan_total"] = len(pl)
    out["plan_extent"] = pl.extent()
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

B_STAGE_ORDER = ("terrace", "quays", "boathouse", "bodies", "lighting", "signs")
B_STAGE_MATCH = {
    "quays": ("sudrvik/quay-",),
    "boathouse": ("sudrvik/boathouse", "sudrvik/gangway"),
    "bodies": ("sudrvik/body/",),
    "lighting": ("sudrvik/lamp",),
    "signs": ("sudrvik/guidepost",),
}
B_STAGE_ROLE = {
    "terrace": "site_pad", "quays": "dock_place",
    # `town_body`, not an invented `village_body`: `ledger/schema.py`
    # registers the role vocabulary and REFUSES an unregistered one, "so the
    # operator reading the log sees one vocabulary". MEASURED by the dry run,
    # which is exactly what a dry run is for.
    "boathouse": "boathouse_place", "bodies": "town_body",
    # the small sheds' own stage, same role as the big one
    "shed": "boathouse_place",
    "lighting": "site_prop", "signs": "waypoint_sign",
}


def stage_rows(d: dict, stage: str) -> list[tuple]:
    pl: HC.Plan = d["_plan"]
    pref = B_STAGE_MATCH.get(stage, ())
    return [r for r in pl.sorted_rows()
            if any(r[5].startswith(q) for q in pref)]


def partition(d: dict) -> dict:
    """The partition is CHECKED TOTAL: a row that matches no stage is a row
    that never gets built and never gets missed."""
    pl: HC.Plan = d["_plan"]
    per = {st: len(stage_rows(d, st)) for st in B_STAGE_ORDER
           if st != "terrace"}
    orphans = [r for r in pl.sorted_rows()
               if not any(r[5].startswith(q)
                          for qs in B_STAGE_MATCH.values() for q in qs)]
    if orphans:
        raise SystemExit(
            f"REFUSING: {len(orphans)} planned pieces belong to no build "
            f"stage, e.g. {orphans[:3]}.")
    return dict(per_stage=per, assigned=sum(per.values()), planned=len(pl),
                total_ok=sum(per.values()) == len(pl))


def ledger_preflight(expect_sha: str | None) -> dict:
    import worldseed  # noqa: PLC0415
    led = worldseed.ledger_path()      # THIS run's world, at call time
    raw = led.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    rep = dict(path=str(led.resolve()), bytes=len(raw), sha256=got,
               records=sum(1 for q in raw.decode().splitlines() if q.strip()),
               expected=expect_sha)
    if expect_sha and got != expect_sha:
        raise SystemExit(
            f"REFUSING: the ledger moved between TAKING and record 1.\n"
            f"  expected {expect_sha}\n  found    {got}\n"
            f"Two writers on one live world is the hazard this check exists "
            f"for; settle who holds the token and re-run.")
    return rep


# `MOBILE` and `cylinder_verdict` live in `settlements/terrace.py` now: the
# village and the city's landing read ONE classification of what a cylinder
# may hold, not two copies that can disagree.


def footprint_gate(srv, d: dict, label: str) -> dict:
    """WHAT ALREADY STANDS INSIDE THIS STRUCTURE'S OWN FOOTPRINT, live.

    The avoid capsules in `SHED_SITES` are the RECORDED geometry of the
    structure each shed serves, and a recorded geometry is a model. This is
    the measurement: an unscoped, y-stacked `waterfront.audit` over the
    plan's own box, deduplicated by prefab + world position, with no prefab
    list at all - so the census cannot be blind to a prefab nobody wrote
    down. That is the exact hole that let 102 of our own pieces end up in a
    carriageway.

    A shed drops onto an existing pier as an INVISIBLE collision:
    `place_missing` matches on prefab AND position, so a `wood_floor` two
    metres from ours is not a match and ours goes in on top of it.
    """
    pl: HC.Plan = d["_plan"]
    rows = pl.sorted_rows()
    x0 = min(r[1] for r in rows)
    x1 = max(r[1] for r in rows)
    z0 = min(r[3] for r in rows)
    z1 = max(r[3] for r in rows)
    ylo = min(r[2] for r in rows)
    yhi = max(r[2] for r in rows)
    yc = (ylo + yhi) / 2.0
    cen = WF.audit(srv, x0, x1, z0, z1, y_centre=yc,
                   y_levels=HC.y_levels_for(ylo - 6.0, yhi + 6.0, yc))
    ours = {(r[0], round(r[1], 1), round(r[3], 1)) for r in rows}
    inside, blocking = [], {}
    for o in cen["objects"]:
        if not (x0 - 1.0 <= o["x"] <= x1 + 1.0
                and z0 - 1.0 <= o["z"] <= z1 + 1.0):
            continue
        inside.append(o)
        key = (o["prefab"], round(o["x"], 1), round(o["z"], 1))
        if key in ours:
            continue                     # our own piece, already standing
        if o["prefab"] in TR.MOBILE:
            continue
        import build as BB  # noqa: PLC0415
        if BB.NATURAL_RE.match(o["prefab"]):
            continue                     # a tree in the water is not a pier
        blocking[o["prefab"]] = blocking.get(o["prefab"], 0) + 1
    return dict(label=label, box=[x0, x1, z0, z1],
                y_window=cen["y_window"], cells=cen["cells"],
                socket_calls=cen["socket_calls"],
                objects_in_box=len(inside), per_prefab=cen["per_prefab"],
                blocking=blocking,
                verdict="clear" if not blocking else "VIOLATION",
                why=("an unscoped y-stacked census of the structure's own "
                     "box: the avoid capsule is a model of what stands "
                     "there, this is the measurement"))


def preflight_seats(srv, d: dict) -> dict:
    """ASK THE WORLD what is inside every clearing cylinder, BEFORE any write.

    This is the gate the live marker screen is not.  `objects_count id=*`
    lists every prefab present in the exact cylinder `objects_remove` will
    empty, one line per prefab, and `Server.count` permits the star only
    inside 40 m - which every seat cylinder here is.  A seat whose cylinder
    holds anything outside the MEASURED natural set is dropped now, with its
    contents recorded.

    Why it runs BEFORE the build and not inside it: MEASURED on this site's
    first dry run, `sudrvik-vestrbakki-2` was refused by the piece-reach gate
    AFTER its `zones_generate` record had already landed. A seat that fails
    mid-stage leaves a generated zone and a chain with no body in it. Drops
    are cheap here and expensive there.

    READ-ONLY: no record is appended and nothing is sent but `objects_count`.
    """
    import area as clear_area   # noqa: PLC0415
    import build as BB          # noqa: PLC0415

    units = d["_units"]
    doc = TR.placements_doc(units, SITE, "villB")
    keep, drop = [], []
    for u in units:
        place = next(p for p in doc["placements"] if p["id"] == u["id"])
        ar = clear_area.clearing_area(place, apron=BB.APRON_M)
        contents = TR.cylinder_verdict(srv, ar)
        row = dict(unit=u["id"], x=u["x"], z=u["z"],
                   clear_radius_m=round(ar.radius_m, 2),
                   total=contents["total"],
                   clearable=contents["clearable"],
                   mobile=contents["mobile"],
                   blocking=contents["blocking"],
                   verdict=contents["verdict"], probe=contents["probe"])
        if contents["verdict"] == "clear":
            keep.append(u)
            row["kept"] = True
        else:
            drop.append(row)
            row["kept"] = False
        print(f"  [preflight] {u['id']:26s} r {ar.radius_m:5.2f} m  "
              f"{contents['total']:4d} objects  {contents['verdict']}"
              + (f"  BLOCKED BY {contents['blocking']}"
                 if contents["verdict"] != "clear" else ""))
    return dict(kept=[u["id"] for u in keep], dropped=drop,
                kept_units=keep,
                why=("the live contents of the exact cylinder that would be "
                     "emptied, measured before the first write, so a seat "
                     "that cannot be cleared is dropped rather than half "
                     "built"))


def apply_preflight(d: dict, pf: dict) -> dict:
    """Drop the refused seats from BOTH the unit list and the plan rows.

    A SHRINK IS SILENT unless it is written down: what was dropped, how many
    pieces went with it, and why.
    """
    landing = next((u for u in d["_units"] if u.get("landing")), None)
    if landing is not None and any(r["unit"] == landing["id"]
                                   for r in pf["dropped"]):
        row = next(r for r in pf["dropped"] if r["unit"] == landing["id"])
        raise SystemExit(
            f"REFUSED {landing['id']}: the clearing cylinder of the landing "
            f"its roads arrive on holds {row['blocking']}, which is neither "
            f"in the measured natural set nor a creature. A body seat can be "
            f"dropped; the landing cannot -- it is the ground the roads were "
            f"routed to, and a village with no landing is a village its roads "
            f"do not reach. Not clipped, not moved: re-site the declared "
            f"landing (settlements/harbour_site.py) with this object as a "
            f"keep-out.")
    if not pf["dropped"]:
        return dict(dropped=0, units_before=len(d["_units"]),
                    units_after=len(d["_units"]), rows_removed=0)
    keep_ids = set(pf["kept"])
    gone = [u for u in d["_units"] if u["id"] not in keep_ids]
    before_rows = len(d["_plan"].rows)
    gone_xz = {(round(u["body_x"], 3), round(u["body_z"], 3)) for u in gone}
    d["_plan"].rows = [r for r in d["_plan"].rows
                       if not (r[5].startswith("sudrvik/body/")
                               and (round(r[1], 3), round(r[3], 3)) in gone_xz)]
    d["_units"] = [u for u in d["_units"] if u["id"] in keep_ids]
    # The landing is a terrace with no building, so it is a unit but it
    # is not a body; counting it would report 14 houses where 13 stand.
    d["bodies"] = sum(1 for u in d["_units"] if not u.get("landing"))
    d["road_keepout"] = [k for k in d["road_keepout"]
                         if not any(k.get("what", "").startswith(
                             f"village body {u['id']} ") for u in gone)]
    return dict(dropped=len(gone), dropped_ids=[u["id"] for u in gone],
                units_before=len(gone) + len(d["_units"]),
                units_after=len(d["_units"]),
                rows_removed=before_rows - len(d["_plan"].rows),
                why=("a seat whose clearing cylinder holds a non-natural "
                     "prefab is dropped; the keep-out disc goes with it so "
                     "the router is not kept out of ground nothing stands on"))


def terrace_stage(b, d: dict, report: dict) -> list[dict]:
    """Per unit -- the landing first, then the bodies -- zones_generate ->
    measured cylinder -> scoped clear -> levelled write, through
    `settlements/terrace.py` (build.py's order, build.py's encoders, build.py's
    gates, plus a refusal for a write over a road's ground).

    A pad is levelled PER BUILDING because MEASURED no district on this seed
    fits inside TerrainComp's +/-8 m clamp, and at harbour B specifically no
    pad of any size is flat to 0.6 m.
    """
    return TR.terrace_units(
        b, d["_units"], SITE, "villB", report, stage="terrace",
        cut_max_m=BODY_CUT_MAX_M,
        why_terraced=(
            "MEASURED over 8,075 lattice centres at harbour B: not one 16, 20 "
            "or 24 m pad is flat to 0.6 m, so seating a body on the applied "
            "surface at the footprint median would stand a 20 m hall with up "
            "to 4.7 m of hillside through its floor"))


def stage_save(b, d: dict, stage: str, site: str = SITE) -> dict:
    if stage == "terrace":
        import tcdata as TC  # noqa: PLC0415
        zs = sorted({z for u in d["_units"]
                     for z in TC.zones_for(u["x"], u["z"], u["pad_w"],
                                           u["pad_d"])}) \
            if hasattr(TC, "zones_for") else []
        checks = [dict(prefab="_TerrainCompiler",
                       pos=[float(c) for c in TC.zone_centre(zx, zz)],
                       max=31.0, count=1, tolerance=0) for zx, zz in zs]
        if not checks:
            import flatten as FL  # noqa: PLC0415
            zs = sorted({z for u in d["_units"]
                         for z in FL.zones_for(u["x"], u["z"], u["pad_w"],
                                               u["pad_d"])})
            checks = [dict(prefab="_TerrainCompiler",
                           pos=[float(c) for c in TC.zone_centre(zx, zz)],
                           max=31.0, count=1, tolerance=0) for zx, zz in zs]
    else:
        rows = stage_rows(d, stage)
        checks = [HC.sentinel(rows, d["_plan"].rows)] if rows else []
    if not checks:
        raise SystemExit(
            f"REFUSING to emit a save for stage {stage}: no measurable "
            f"postcondition could be derived from its own rows. "
            f"'I could not measure it' is not 'it is fine'.")
    return b.emit("save",
                  params={"role": B_STAGE_ROLE.get(stage, "harbour_place"),
                          "site_id": site,
                          "note": f"harbour B stage {stage} complete"},
                  wire=["save"], expect={"prefab_count": checks},
                  meta={"settlement": site, "stage": stage,
                        "why": ("a verified save at every stage boundary, so "
                                "any later boundary is a clean stopping "
                                "point rather than a chain tail with no save "
                                "after it")})


def build_stages(b, d: dict, want: list[str], site: str, report: dict,
                 *, dry: bool, from_batch: int = 1) -> None:
    for stage in want:
        if not dry:
            report.setdefault("players_per_stage", {})[stage] = str(
                b.srv.command("players"))[:200]
        if stage == "terrace":
            recs = terrace_stage(b, d, report)
            report["stages"].append({"stage": stage, "records": recs})
        elif stage == "signs":
            recs = []
            for g in d.get("guideposts", []):
                recs.append(HC._strip_op(WF.spawn_op(
                    site, "waypoint_sign", g["prefab"], g["x"], g["y"],
                    g["z"], g["yaw"], reason=HC.ON_LAND_REASON,
                    blob=g["blob"], strings={"text": g["text"]},
                    meta={"part": "sudrvik/guidepost", "stage": stage})))
                recs[-1] = b.emit(**recs[-1])
            report["stages"].append({"stage": stage, "records": recs})
        else:
            rows = stage_rows(d, stage)
            if not rows:
                report["stages"].append({"stage": stage, "records": [],
                                         "note": "no rows"})
                continue
            earlier = [q for st in B_STAGE_ORDER[:B_STAGE_ORDER.index(stage)]
                       for q in stage_rows(d, st)]
            fb = from_batch if stage == want[0] else 1
            ops = HC.stage_ops(site, stage, rows, d["_plan"].rows,
                               B_STAGE_ROLE[stage], placed_before=earlier,
                               from_batch=fb)
            recs = []
            for i, op in enumerate(ops):
                text = "\n".join(op["wire"][0].split(";")) + "\n"
                op["params"]["plan_sha256"] = b.blob(
                    text.encode(),
                    note=f"{site}/{stage} batch {op['meta']['batch']}"
                         f"/{op['meta']['batches']}")
                recs.append(b.emit(**HC._strip_op(op)))
                if not dry:
                    g = WF.sink_gate(f"{site}/{stage} batch {i + 1}")
                    if g.get("verdict_after") not in (None, "flowing", "ok"):
                        report.setdefault("sink", []).append(g)
            report["stages"].append({"stage": stage, "records": recs})
        report["stages"][-1].setdefault("saves", []).append(
            stage_save(b, d, stage, site))


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_counts(srv, d: dict, foreign: list[dict] | None = None) -> dict:
    """Per-prefab live counts at TOLERANCE 0, plus strays.

    Two instruments on orthogonal blindness, because each is blind where the
    other sees:
      * `objects_count id=<prefab> pos=x,z max=r` is a Y-BLIND XZ CYLINDER.
        It answers "is the NUMBER right" and it will happily count a piece
        15 m below the one you meant.
      * `waterfront.audit` is `findObjects -near x y z 8`, a CUBE in all
        three axes, stacked in y. It answers "is the HEIGHT right".
    A count that agrees in the cylinder and disagrees in the cube is a piece
    at the right xz and the wrong y, which is precisely the defect a cylinder
    cannot see.
    """
    rows = d["_plan"].sorted_rows()
    xs = [r[1] for r in rows]
    zs = [r[3] for r in rows]
    cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
    r = math.hypot(max(xs) - min(xs), max(zs) - min(zs)) / 2.0 + 4.0
    want: dict[str, int] = {}
    for q in rows:
        want[q[0]] = want.get(q[0], 0) + 1
    # THE CYLINDER DOES NOT KNOW WHOSE PIECES THEY ARE.  `objects_count`
    # counts a prefab, not an owner, and harbour B contains a GENERATED
    # village whose own pieces carry prefab names this build also uses.
    # MEASURED at (1218, 512) within 22 m: wood_floor 12, woodwall 1,
    # wood_wall_half 14, one LocationProxy and one goblin_bed.  Against a
    # 167 wood_floor / 144 woodwall plan the site cylinder answers 179 and
    # 145, and calling those 13 "strays" would report worldgen as a
    # double-placement.  `foreign` names the discs of world content the
    # build deliberately did not touch, so the surplus is EXPLAINED with
    # its own count rather than excused with a tolerance.
    out = []
    for prefab, n in sorted(want.items()):
        got, _per = srv.count(prefab, cx, cz, r)
        row = dict(prefab=prefab, want=n, got=got, delta=got - n,
                   exact=bool(got == n))
        if row["delta"] > 0 and foreign:
            seen = 0
            where = []
            for f in foreign:
                fn, _ = srv.count(prefab, f["x"], f["z"], f["r"])
                if fn:
                    seen += fn
                    where.append(dict(centre=[f["x"], f["z"]], r=f["r"],
                                      count=fn, what=f.get("what")))
            row["foreign_in_cylinder"] = seen
            row["foreign_where"] = where
            row["delta_after_foreign"] = row["delta"] - seen
            row["exact"] = bool(row["delta_after_foreign"] == 0)
        out.append(row)
    return dict(cylinder=dict(x=round(cx, 2), z=round(cz, 2),
                              radius_m=round(r, 2)),
                rows=out, tolerance=0,
                foreign_discs=foreign or [],
                all_exact=all(q["exact"] for q in out),
                why=("an XZ cylinder wide enough to contain the whole site; "
                     "`delta` above zero is a stray, a double-placement OR "
                     "world content sharing the prefab name, and the third "
                     "is separated by counting the same prefab inside the "
                     "discs of the locations this build refused to touch; "
                     "below zero is a piece that never landed"))


def verify_grounding(s, d: dict) -> dict:
    """Worst air gap and worst burial, BY COLUMN, against the applied surface.

    THE UNIT IS THE STACK, NOT THE PIECE. `piles()` builds a column of
    `wood_pole_log_4`: the lowest pole reaches the bed and every pole above
    it is carried by the one below. Measuring each pole against the seabed
    reports a correctly built column as two dozen floating piles.

    Classes have different datums and using one prefab's datum for another is
    the defect that put three beacons 5.173 m in the air:
      pile       pivot at CENTRE -> bottom = y - 2.000; burial is CORRECT
                 for a pile and is reported, not failed
      deck       `wood_floor` top +0.0969 -> burial = bed - top, and a deck
                 tile inside the bank IS a defect
      on-deck    torch / wall: datum 30.6, the question is whether a deck
                 tile is under them
      on-wall    roof and beam: carried at the EAVES, so a gable over the bay
                 void has nothing directly beneath by design - the test is
                 wall support at its own LATITUDE, not underneath
      body       `fb_` pivot at FOOT -> air gap = y - terrace floor
    """
    units = d.get("_units") or []
    rows = d["_plan"].sorted_rows()
    deck_xz = {(round(r[1], 2), round(r[3], 2)) for r in rows
               if r[0] == GEOM["floor"]["prefab"]}
    wall_z = {round(r[3], 2) for r in rows
              if r[0] == GEOM["wall"]["prefab"]}
    piles: dict[tuple, dict] = {}
    decks, ondeck, onwall, bodies = [], [], [], []
    for r in rows:
        pf, x, y, z, _yaw, why = r
        bed = s.at(x, z)[0]
        if pf == GEOM["pile"]["prefab"]:
            key = (round(x, 2), round(z, 2))
            col = piles.setdefault(key, dict(x=x, z=z, poles=0,
                                             bottom=y - GEOM["pile"]["half"],
                                             bed=bed))
            col["poles"] += 1
            col["bottom"] = min(col["bottom"], y - GEOM["pile"]["half"])
        elif pf == GEOM["floor"]["prefab"]:
            top = y + GEOM["floor"]["top"]
            decks.append(dict(x=x, z=z, top=round(top, 3), bed=round(bed, 3),
                              burial_m=round(bed - top, 3)))
            # A GANGWAY IS SUPPOSED TO REACH THE BANK, so its last tiles meet
            # grade by construction and reporting them as buried would be
            # reporting the feature.  The threshold is the tile's OWN
            # thickness, MEASURED: `wood_floor` spans y[-0.0329, +0.0969] =
            # 0.1298 m.  Ground above the tile's top by less than that is a
            # tile at grade; more than that is a tile inside the bank, which
            # is the real defect (two of harbour-temple-south's 39 deck tiles
            # stood +0.721 m and +0.147 m inside it).
        elif pf in (GEOM["roof"]["prefab"], GEOM["beam"]["prefab"]):
            onwall.append(dict(prefab=pf, x=x, z=z, y=round(y, 3),
                               wall_under=any(abs(z - wz) <= 2.05
                                              for wz in wall_z)))
        elif pf in (HC.TORCH, GEOM["wall"]["prefab"]):
            base = y - (TORCH_ABOVE_SURFACE if pf == HC.TORCH else 0.0)
            ondeck.append(dict(prefab=pf, x=x, z=z, base=round(base, 3),
                               above_deck_m=round(base - DECK_Y, 3),
                               deck_tile_under=any(
                                   abs(x - dx) <= 2.05 and abs(z - dz) <= 2.05
                                   for dx, dz in deck_xz)))
        elif pf.startswith("fb_"):
            # A BODY IS MEASURED AGAINST ITS OWN TERRACE, NOT AGAINST THE
            # HILLSIDE THE TERRACE REMOVES.  MEASURED before this fix: an
            # `fb_house_02` read -1.018 m "buried" against the PRE-terrace
            # surface at (1275.89, 459.23) while sitting exactly on the
            # 32.139 m pad its own `terrain_write` levels to.  Comparing a
            # seat against ground the build has already agreed to cut is the
            # same class as measuring a road profile against terrain it is
            # about to grade.
            tgt = None
            for u in (units or []):
                if (abs(u["body_x"] - x) < 0.01
                        and abs(u["body_z"] - z) < 0.01):
                    tgt = u["target_y"]
                    break
            bodies.append(dict(prefab=pf, x=x, z=z, y=round(y, 3),
                               pre_terrace_surface=round(bed, 3),
                               terrace_target_y=tgt,
                               air_gap_m=(None if tgt is None
                                          else round(y - tgt, 3)),
                               pre_terrace_gap_m=round(y - bed, 3)))
    pcols = [dict(x=c["x"], z=c["z"], poles=c["poles"],
                  bottom=round(c["bottom"], 3), bed=round(c["bed"], 3),
                  air_gap_m=round(c["bottom"] - c["bed"], 3))
             for c in piles.values()]

    def worst(rs, key, sign=1):
        return dict(max(rs, key=lambda q: sign * q[key])) if rs else None

    return dict(
        piles=dict(columns=len(pcols), poles=sum(c["poles"] for c in pcols),
                   worst_air_gap=worst(pcols, "air_gap_m"),
                   deepest_buried=worst(pcols, "air_gap_m", -1),
                   floating=[q for q in pcols if q["air_gap_m"] > 0.25],
                   note="burial is not a defect for a pile"),
        decks=dict(n=len(decks), worst_burial=worst(decks, "burial_m"),
                   tile_thickness_m=round(GEOM["floor"]["top"]
                                          - GEOM["floor"]["bottom"], 4),
                   at_grade=[q for q in decks
                             if 0.0 < q["burial_m"] <= 0.1298],
                   buried=[q for q in decks if q["burial_m"] > 0.1298]),
        on_deck=dict(n=len(ondeck),
                     no_deck_tile_under=[q for q in ondeck
                                         if not q["deck_tile_under"]],
                     worst_above_deck=worst(ondeck, "above_deck_m")),
        on_wall=dict(n=len(onwall),
                     no_wall_at_latitude=[q for q in onwall
                                          if not q["wall_under"]]),
        bodies=dict(n=len(bodies),
                    measured_against="its own terrace target_y",
                    without_terrace=[q for q in bodies
                                     if q["air_gap_m"] is None],
                    worst_air_gap=worst([q for q in bodies
                                         if q["air_gap_m"] is not None],
                                        "air_gap_m"),
                    deepest_buried=worst([q for q in bodies
                                          if q["air_gap_m"] is not None],
                                         "air_gap_m", -1)),
        thresholds=dict(visible_gap_m=0.25, field_noise_m=0.0275),
        surface=("the APPLIED surface: the seed-derived 1 m field unioned "
                 "with every terrain blob in the ledger IN FILE ORDER, which "
                 "includes this build's own terraces once they land"))


def verify_berths(s, d: dict) -> dict:
    """Per berth: depth under the hull's OWN ellipse at its OWN yaw, plus
    whether a player can board it from the dock face."""
    rows = d["_plan"].sorted_rows()
    deck = [(r[1], r[3]) for r in rows if r[0] == GEOM["floor"]["prefab"]]
    out = []
    for b in d.get("berth_solve", {}).get("berths", []):
        row = dict(x=b["x"], z=b["z"], bearing_deg=b["bearing_deg"], hulls={})
        for v in sorted(WF.VESSELS):
            g = WF.VESSELS[v]
            need = g["draught_m"] + WF.KEEL_CLEARANCE_M
            hull = WF.hull_depths(s, b["bearing_deg"], b["x"], b["z"], v)
            if hull is None:
                row["hulls"][v] = dict(measurable=False)
                continue
            side_a, side_b = (b["x"] - g["hull_w"] / 2.0,
                              b["x"] + g["hull_w"] / 2.0)
            band = [q for q in deck if abs(q[1] - b["z"]) <= g["hull_l"] / 2.0]
            gap = (min(min(abs(q[0] - side_a), abs(q[0] - side_b))
                       for q in band) if band else None)
            step = (W.WATER_LEVEL - HC.HULL_ORIGIN_BELOW_SURFACE_M
                    + (HC.HULL_STEP_ON_M.get(v) or 0.0)) - DECK_Y
            row["hulls"][v] = dict(
                measurable=True, required_m=round(need, 2),
                shallowest_under_hull_m=round(hull[0], 3),
                deepest_m=round(hull[1], 3),
                all_connected_to_open_sea=hull[2], samples=hull[3],
                margin_m=round(hull[0] - need, 3),
                floats=bool(hull[0] >= need and hull[2]),
                clears_margin_floor=bool(
                    hull[0] >= need + WF.BERTH_MARGIN_FLOOR_M and hull[2]),
                gap_to_deck_face_m=(None if gap is None else round(gap, 2)),
                boardable=bool(gap is not None and gap <= 3.0),
                step_onto_hull_m=(None if HC.HULL_STEP_ON_M.get(v) is None
                                  else round(step, 3)))
        out.append(row)
    return dict(berths=out, water_level_y=W.WATER_LEVEL,
                margin_floor_m=WF.BERTH_MARGIN_FLOOR_M,
                why=("the hull's own rotated ellipse at the berth's own "
                     "bearing, shallowest sample, every sample flood-fill "
                     "connected to open sea. A point reading of 4.21 m put a "
                     "Longship aground with -0.465 m under its bow."))


def verify_bays(d: dict) -> dict:
    """Bay count, clear opening, measured depth, and WHICH HULL EACH FITS."""
    bh = d.get("boathouse")
    if not bh:
        return {}
    out = []
    for i, sh in enumerate(bh["sheds"]):
        b = sh["bay"]
        out.append(dict(
            bay=i + 1, clear_width_m=b["clear_width_m"],
            length_m=b["length_m"], mouth_at=b["mouth_at"],
            depth_min_m=b["depth_min_m"], depth_med_m=b["depth_med_m"],
            samples=b["samples"], fits=b["hull_classes_that_fit"],
            largest=b["largest"], refused=b["hull_classes_refused"],
            eaves_y=sh["eaves_y"], ridge_y=sh["ridge_y"],
            enclosed=sh["enclosed"]))
    return dict(bays=len(out), rows=out,
                invariant=("the SEAWARD gable of every bay is DELIBERATELY "
                           "OPEN - it is the bay mouth. A census counting "
                           "four walls per bay would be counting a defect."))


def verify_landing(s, d: dict) -> dict:
    """Do the roads that ARRIVE at this village arrive on its landing?

    The instrument is the APPLIED surface -- the seed field unioned with every
    terrain blob in the ledger in file order -- so once the landing's own
    `terrain_write` is in the log this reads the ground the engine will draw.
    The roads are READ OFF THE CHAIN (`terrace.arrivals`: each road's own last
    station [x, z, y]), never off a constant, and the reference for each is
    that road's own datum, not the target this module chose: a check whose
    reference is the thing under test is not a check.

    Per arriving road, down the straight line from its last station to the
    landing's centre at 1 m: the applied height at the station, the worst 1 m
    step, and the BERM (how far the ground between stands above the higher of
    the road's end and the landing) -- the quantities the gate's
    `terminus_step` and `terminus_berm` judge, reported here before it does.
    """
    u = d.get("landing")
    if not u:
        return dict(built=False, why="this design has no landing")
    recs = AP.Applied(actor=ACTOR).led.records()
    arr = TR.arrival_numbers(
        TR.arrivals(recs, (u["x"], u["z"]), retired=TR._retired(recs)), u)
    centre = s.at(u["x"], u["z"])[0]
    rows = []
    for a in arr:
        n = max(int(round(a["dist_to_node_m"])), 1)
        prof = [s.at(a["x"] + (u["x"] - a["x"]) * k / n,
                     a["z"] + (u["z"] - a["z"]) * k / n)[0]
                for k in range(n + 1)]
        rows.append(dict(
            a, applied_y_at_station=round(prof[0], 3),
            worst_step_1m=round(max(abs(prof[k + 1] - prof[k])
                                    for k in range(n)), 3),
            berm_m=round(max(prof) - max(a["y"], centre), 3)))
    return dict(
        built=True, landing=u["id"], datum_y=u["target_y"],
        applied_y_at_centre=round(centre, 3),
        centre_error_m=round(centre - u["target_y"], 3),
        arrivals=rows,
        instrument=("waterfront.Surface.at over the applied field; each "
                    "road's datum is its own last station off the ledger"))


def chain_units() -> dict:
    """Which village units the LEDGER says were actually built.

    A body stands only if its terrace landed, so the `terrain_write` records
    with `role=site_pad` and a `sudrvik-` site id are the authority.  Reading
    the chain rather than the world keeps the reference independent of the
    thing being measured.
    """
    import writer as WR  # noqa: PLC0415
    import worldseed  # noqa: PLC0415
    path = worldseed.ledger_path()     # THIS run's world, at call time
    built: list[str] = []
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        p = rec.get("params") or {}
        sid = str(p.get("site_id") or "")
        if not sid.startswith("sudrvik-"):
            continue
        if rec.get("op") == "terrain_write" and p.get("role") == "site_pad":
            built.append(sid)
    return dict(
        built=sorted(set(built)),
        instrument=(f"every `terrain_write role=site_pad site_id=sudrvik-*` "
                    f"in {path.name}, sha256 {WR.sha256_file(path)[:16]}"),
        why=("a body stands only where its terrace landed, so the pads in "
             "the chain are exactly the units that were built"))


def shrink_to_chain(d: dict, chain: dict) -> dict:
    """Drop from the plan every unit the chain says was never built."""
    keep = set(chain["built"])
    gone = [u for u in d.get("_units", []) if u["id"] not in keep]
    if not gone:
        return dict(dropped=0, units_after=len(d.get("_units", [])),
                    rows_removed=0)
    before = len(d["_plan"].rows)
    gone_xz = {(round(u["body_x"], 3), round(u["body_z"], 3)) for u in gone}
    d["_plan"].rows = [r for r in d["_plan"].rows
                       if not (r[5].startswith("sudrvik/body/")
                               and (round(r[1], 3), round(r[3], 3)) in gone_xz)]
    d["_units"] = [u for u in d["_units"] if u["id"] in keep]
    d["bodies"] = sum(1 for u in d["_units"] if not u.get("landing"))
    return dict(dropped=len(gone), dropped_ids=[u["id"] for u in gone],
                units_after=len(d["_units"]),
                bodies_after=d["bodies"],
                rows_removed=before - len(d["_plan"].rows),
                # The refused seats' own cylinders ARE the discs of the
                # world content that refused them, so they are handed to the
                # census to explain any prefab surplus.
                refused_discs=[dict(x=u["x"], z=u["z"],
                                    r=round(max(u["pad_w"], u["pad_d"]) / 2.0
                                            + 4.0, 2),
                                    what=f"refused seat {u['id']} - the "
                                         f"generated content that blocked it")
                               for u in gone],
                why=("these units have no `terrain_write` in the chain, so "
                     "nothing was built on them and counting their pieces "
                     "would report a refusal as a shortfall"))


def cmd_verify(args) -> int:
    """Verify what stands, LIVE. READ-ONLY; no ledger handle is taken."""
    import replay as R  # noqa: PLC0415
    s = surface()
    if args.site and args.site != SITE:
        out = solve_small_sheds(s)
        site = next(q for q in out["sites"] if q["id"] == args.site)
        bays, sol = site["_best"]
        d = design_small_shed(s, site, sol, bays)
    else:
        d = design(s)
    rep: dict = dict(site=d.get("site", SITE), water_level_y=W.WATER_LEVEL,
                     planned=len(d["_plan"]))
    if not args.skip_live:
        with R.Server() as srv:
            srv.probe()
            rep["players"] = str(srv.command("players"))[:200]
            # VERIFY AGAINST WHAT THE BUILD AGREED TO BUILD - AND TAKE THAT
            # FROM THE CHAIN, NOT FROM THE WORLD UNDER TEST.
            #
            # `design()` lays out 13 bodies; the build's live seat
            # pre-flight refused two of them because their clearing
            # cylinders hold a generated village (`LocationProxy`,
            # `goblin_bed`, `Music_MeadowsVillageFarm` and 33 of its
            # pieces), and a deleted location piece is this project's one
            # recorded unrepairable damage class.  Counting the built world
            # against the pre-shrink design would report two houses missing
            # and call a correct refusal a defect.
            #
            # RE-RUNNING THE PRE-FLIGHT HERE WAS TRIED AND IT IS THE DEFECT
            # THIS PROJECT NAMES AS "a check whose reference is the thing
            # under test", inverted: after the build, every seat's cylinder
            # contains THE HOUSE THAT WAS JUST BUILT ON IT, so the gate read
            # `fb_viking_house_06` as blocking and refused all 13 of 13.
            # MEASURED: it dropped every seat and shrank the plan to 661.
            #
            # The authority for what was built is the LEDGER: a body stands
            # only where its terrace landed, so the units with a
            # `terrain_write` in the chain are exactly the units that were
            # built.
            if d.get("_units"):
                rep["built_from_chain"] = chain_units()
                rep["seat_shrink"] = shrink_to_chain(
                    d, rep["built_from_chain"])
                rep["planned_after_shrink"] = len(d["_plan"])
            rep["counts"] = verify_counts(
                srv, d,
                foreign=(rep.get("seat_shrink") or {}).get("refused_discs"))
            rep["footprint"] = footprint_gate(srv, d, rep["site"])
    rep["bays"] = verify_bays(d)
    rep["grounding"] = verify_grounding(s, d)
    rep["landing"] = verify_landing(s, d)
    if d.get("berth_solve"):
        rep["berths"] = verify_berths(s, d)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(rep, indent=1, default=float))
    print(json.dumps(dict(
        site=rep["site"], planned=rep["planned"],
        players=rep.get("players"),
        counts_all_exact=rep.get("counts", {}).get("all_exact"),
        count_rows=rep.get("counts", {}).get("rows"),
        bays=rep["bays"].get("bays"),
        worst_pile_gap=rep["grounding"]["piles"]["worst_air_gap"],
        floating_pile_columns=len(rep["grounding"]["piles"]["floating"]),
        buried_deck_tiles=len(rep["grounding"]["decks"]["buried"]),
        props_without_deck=len(
            rep["grounding"]["on_deck"]["no_deck_tile_under"]),
        landing_centre_error_m=rep["landing"].get("centre_error_m"),
        report=args.report), indent=1, default=float))
    return 0


def cmd_design(args) -> int:
    s = surface()
    d = design(s)
    pl = d.pop("_plan")
    if args.plans:
        p = Path(args.plans)
        p.mkdir(parents=True, exist_ok=True)
        pl.write(p / f"{SITE}.plan")
        print(f"plan written to {p / (SITE + '.plan')}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(d, indent=1, default=float))
        print(f"design written to {args.out}")
    print(summarise(d))
    return 0


def summarise(d: dict) -> str:
    L = [f"=== {d['site']}   {d['plan_total']} pieces",
         f"  counts {json.dumps(d['plan_pieces'])}",
         f"  NO CURTAIN WALL - {d['curtain_refused']['span_m']} m for "
         f"{d['curtain_refused']['encloses_m2']} m2, refused",
         f"  fjord mouth {d['hand']['mouth']} "
         f"(edges {d['hand']['touches_patch_edges']})"]
    for k in ("quay_west", "quay_east"):
        q = d[k]
        L.append(f"  {k}: {q['rows']} rows, {q['tiles']} tiles, "
                 f"{q['pieces']} pieces, deck {q['deck_walking_y']}")
    bh = d["boathouse"]
    L.append(f"  BOATHOUSE {bh['bays']} bays @ {bh['bay_clear_m']} m clear, "
             f"{bh['pieces']} pieces, {bh['duplicate_rows_dropped']} dup rows "
             f"dropped")
    for i, sh in enumerate(bh["sheds"]):
        b = sh["bay"]
        L.append(f"    bay{i + 1} {b['clear_width_m']} x {b['length_m']} m "
                 f"mouth {b['mouth_at']} depth min {b['depth_min_m']} "
                 f"({b['samples']} samples) fits {b['hull_classes_that_fit']} "
                 f"largest {b['largest']}")
        L.append(f"          eaves {sh['eaves_y']} ridge {sh['ridge_y']} "
                 f"walls {sh['walls']} gable {sh['gable']} roofs {sh['roofs']}")
    L.append(f"  gangway {d['gangway'].get('length_m', 0)} m, "
             f"{d['gangway'].get('pieces', 0)} pieces")
    L.append(f"  bodies {d['bodies']} terraced at cut <= "
             f"{d['terrace_cut_max_m']} m")
    for q in d["quarters"]:
        L.append(f"    {q['id']}: {len(q['seats'])} seats, "
                 f"refusals {q['refusals']}")
    L.append(f"  lighting {d['lighting']['torches']} torches "
             f"({d['lighting']['role']}) at {d['lighting']['pitch_m']} m")
    bs = d["berth_solve"]
    L.append(f"  berths {len(bs['berths'])} of {bs['candidates']} candidates, "
             f"refusals {bs['refusals']}")
    for p in bs["berths"]:
        L.append(f"    ({p['x']},{p['z']}) shallowest {p['shallowest_under_hull_m']} m "
                 f"need {p['required_m']} margin {p['margin_m']} "
                 f"deck face {p['gap_to_deck_face_m']} m")
    L.append(f"  road keep-out discs {len(d['road_keepout'])}")
    lu = d.get("landing")
    if lu:
        L.append(f"  LANDING {lu['id']} {lu['pad_w']:g} x {lu['pad_d']:g} m at "
                 f"({lu['x']},{lu['z']}) levelled to {lu['target_y']} m "
                 f"(the published datum) - cut {lu['cut_needed_m']} fill "
                 f"{lu['fill_needed_m']} over {lu['samples']} samples")
    return "\n".join(L)


def cmd_sheds(args) -> int:
    s = surface()
    out = solve_small_sheds(s)
    rows = []
    for r in out["solves"]:
        b = r["solve"]["best"]
        rows.append(f"  {r['site']:22s} {r['bays']} bay(s): "
                    + (f"BEST x {b['x_centre']} z {b['z_open']}..{b['z_close']} "
                       f"depth {b['bay_min_depth_m']} gangway {b['gangway_m']} m"
                       if b else
                       f"REFUSED {r['solve']['refusals']} over "
                       f"{r['solve']['tried']} candidates"))
    print(f"=== small enclosed boathouses, bay {SMALL_BAY['clear']} x "
          f"{SMALL_BAY['length']} m, need {SMALL_BAY['need_m']} m")
    print("\n".join(rows))
    designs = {}
    for site in out["sites"]:
        if not site.get("_best"):
            continue
        bays, sol = site["_best"]
        dd = design_small_shed(s, site, sol, bays)
        pl = dd.pop("_plan")
        designs[site["id"]] = dd
        if args.plans:
            p = Path(args.plans)
            p.mkdir(parents=True, exist_ok=True)
            pl.write(p / f"{site['id']}.plan")
        sh = dd["boathouse"]["sheds"][0]["bay"]
        print(f"  {site['id']}: {dd['plan_total']} pieces, bays "
              f"{dd['boathouse']['bays']}, depth min {sh['depth_min_m']} m, "
              f"fits {sh['hull_classes_that_fit']}, refused "
              f"{list(sh['hull_classes_refused'])}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(
            {k: v for k, v in designs.items()}, indent=1, default=float))
        print(f"written {args.out}")
    return 0


REQUIRED_VERBS = {
    "spawn_object": "World_Edit_Commands - THE ONLY CONSTRUCTIVE VERB",
    "objects_count": "Upgrade_World - every postcondition and every census",
    "objects_remove": "Upgrade_World - the terrace stage's clearing",
}
# MEASURED tonight: `World_Edit_Commands` was scoped client-only by a profile
# rebuild, the server lost `spawn_object`, and six `terrain_write` halves
# deleted a compiler and put nothing back while every RCON reply said
# "executed." -- `InvokeConsoleCommand.OnHandle` returns
# "Command '<x>' executed." UNCONDITIONALLY and never carries the console's
# output. A delivery receipt is not a result, so this gate reads the
# CONTAINER'S OWN STDOUT instead.


# NEUTER (selftest): False restores the shipped probe, which let the console's
# own `UnrecognizedVerb` escape and refused the whole body.
_UNRECOGNISED_VERB_IS_ABSENT = True


def verb_preflight(srv) -> dict:
    """Prove every required verb EXISTS before emitting record 1.

    PROVED BOTH DIRECTIONS BY CONSTRUCTION. A verb known to be owned by no
    installed plugin is probed alongside the required ones, and the gate
    REFUSES unless that control comes back absent. A probe that reports
    everything present has not shown it can detect an absence -- which is
    exactly the `grep -ci worldedit` failure that returned a confident zero
    against a directory named `World_Edit_Commands` for a whole session.
    """
    control = "terrain_reset"
    out, missing = {}, []
    for verb in list(REQUIRED_VERBS) + [control]:
        try:
            text = "\n".join(srv.console(verb))
        except Exception as e:                                 # noqa: BLE001
            # `console.run_console` RAISES `UnrecognizedVerb` for a verb the
            # game does not know (the right answer for a build step, the
            # answer THIS probe is asking for).  MEASURED on Ulfsland regen #4:
            # the control verb `terrain_reset`, expected absent, raised out of
            # this probe and refused the harbour-village-sudrvik body at step 5
            # before it began.  Any other exception is a real failure.
            if (type(e).__name__ != "UnrecognizedVerb"
                    or _UNRECOGNISED_VERB_IS_ABSENT is False):
                raise
            text = str(e)
        absent = ("Unknown command" in text and verb in text)
        out[verb] = dict(absent=absent,
                         owner=REQUIRED_VERBS.get(verb, "CONTROL - expected absent"),
                         console=text[:160])
        if absent and verb != control:
            missing.append(verb)
    if not out[control]["absent"]:
        raise SystemExit(
            f"REFUSING to build: this gate cannot detect an absence. Its "
            f"control `{control}` is owned by no installed plugin and the "
            f"probe did not report it missing, so a clean result here means "
            f"nothing. Console said: {out[control]['console']!r}")
    if missing:
        raise SystemExit(
            f"REFUSING to build: {missing} do not exist on this server.\n"
            + "\n".join(f"  {v}: {REQUIRED_VERBS[v]}" for v in missing)
            + "\nEvery spawn would record as placed and would not exist; a "
              "terrain_write would delete a compiler and put nothing back. "
              "The RCON reply says 'executed.' either way.")
    return dict(probed=out, control=control,
                verdict="all required verbs present, and the gate proved it "
                        "can detect an absence")


def cmd_preflight(args) -> int:
    """READ-ONLY. `replay.Server` directly, never `LiveBuilder`: this op
    appends no record, and taking a ledger handle for a read-only question
    would claim the write token for it."""
    import replay as R  # noqa: PLC0415
    s = surface()
    d = design(s)
    with R.Server() as srv:
        srv.probe()
        print(f"players: {str(srv.command('players'))[:200]}")
        pf = preflight_seats(srv, d)
    pf["planned_units"] = len(d["_units"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(pf, indent=1, default=float))
    print(f"\nkept {len(pf['kept'])} of {pf['planned_units']}; "
          f"dropped {len(pf['dropped'])}")
    for r in pf["dropped"]:
        print(f"  DROP {r['unit']} blocked by {r['blocking']}")
    print(f"written {args.out}")
    return 0


def cmd_build(args) -> int:
    from ledger.live import LiveBuilder  # noqa: PLC0415
    if args.site not in (None, SITE):
        raise SystemExit(
            f"REFUSING: asked to build {args.site!r}, and the design in "
            f"harbour_b.py is authored for {SITE!r} alone (its fjord, its "
            f"quays, its boathouse). A declared site of the same kind is not "
            f"that fjord; building this design at its node would stand "
            f"{SITE}'s village on another seed's water.")
    s = surface()
    d = design(s)
    want = [q for q in B_STAGE_ORDER if q in (args.stages or B_STAGE_ORDER)]
    report: dict = {"site": SITE, "stages": [], "want": want}
    report["ledger_preflight"] = ledger_preflight(args.expect_sha)
    print(f"LEDGER {report['ledger_preflight']['path']} "
          f"sha {report['ledger_preflight']['sha256']} "
          f"({report['ledger_preflight']['records']} records)")
    with LiveBuilder(actor=getattr(args, "actor", None) or ACTOR,
                     dry=args.dry) as b:
        if not args.dry:
            report["prefab_preflight"] = HC.prefab_preflight(b.srv)
        report["players"] = (str(b.srv.command("players"))[:200]
                             if not args.dry else "dry")
        print(f"players: {report['players']}")
        if not args.dry:
            report["verb_preflight"] = verb_preflight(b.srv)
            print("verb pre-flight: " + report["verb_preflight"]["verdict"])
        # THE SEAT PRE-FLIGHT, READ-ONLY AND BEFORE THE FIRST RECORD.
        # A seat refused after its `zones_generate` has landed leaves a
        # generated zone with nothing in it; refused here it costs one
        # `objects_count`.
        if d["_units"]:
            pf = preflight_seats(b.srv, d)
            report["seat_preflight"] = pf
            report["seat_shrink"] = apply_preflight(d, pf)
            print(f"seat pre-flight: {report['seat_shrink']}")
        report["partition"] = partition(d)
        build_stages(b, d, want, SITE, report, dry=args.dry,
                     from_batch=int(getattr(args, "from_batch", 1) or 1))
        report["close"] = b.close()
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=1, default=str))
    problems = [q for st in report["stages"] for r in st["records"]
                for q in (r.get("schema_problems") or [])]
    print(json.dumps({"want": want, "players": report.get("players"),
                      "per_stage": {st["stage"]: len(st["records"])
                                    for st in report["stages"]},
                      "schema_problem_count": len(problems),
                      "schema_problems": problems[:20],
                      "sink": report.get("sink"),
                      "report": args.report}, indent=1, default=str))
    return 0


SHED_STAGE_ORDER = ("shed", "lighting")


def cmd_shedbuild(args) -> int:
    from ledger.live import LiveBuilder  # noqa: PLC0415
    s = surface()
    out = solve_small_sheds(s)
    site = next((q for q in out["sites"] if q["id"] == args.site), None)
    if site is None:
        raise SystemExit(f"{args.site} is not one of "
                         f"{[q['id'] for q in out['sites']]}")
    # A HOLD IS A REFUSAL WITH A REASON, and it has to bite BEFORE the
    # solver's cheerful "BEST" line, because this site does solve: it
    # returns a 342-piece shed with 4.252 m under the bay. The numbers are
    # confident and they are computed on the wrong surface, which is the
    # combination that has cost this world the most.
    # It bites on a DRY run too. A dry run that "passes" for a held site is
    # an instrument telling a later reader the site is ready; the hold is
    # lifted by re-measuring and deleting it, not by a flag.
    if site.get("hold"):
        raise SystemExit(f"REFUSING {site['id']}: {site['hold']}")
    if not site.get("_best"):
        sol = next(r for r in out["solves"] if r["site"] == site["id"])
        raise SystemExit(
            f"REFUSING {site['id']}: no small bay clears "
            f"{SMALL_BAY['need_m']} m under its whole footprint AND reaches "
            f"dry ground. refusals {sol['solve']['refusals']} over "
            f"{sol['solve']['tried']} candidates. A bay a boat cannot enter "
            f"is the defect this build exists to correct.")
    bays, sol = site["_best"]
    d = design_small_shed(s, site, sol, bays)
    d["_units"] = []
    match = {"shed": (f"{site['id']}/shed", f"{site['id']}/gangway"),
             "lighting": (f"{site['id']}/lamp",)}
    global B_STAGE_MATCH, B_STAGE_ORDER
    saved_match, saved_order = B_STAGE_MATCH, B_STAGE_ORDER
    B_STAGE_MATCH, B_STAGE_ORDER = match, SHED_STAGE_ORDER
    try:
        partition(d)
        report: dict = {"site": site["id"], "stages": [],
                        "want": list(SHED_STAGE_ORDER)}
        report["ledger_preflight"] = ledger_preflight(args.expect_sha)
        print(f"LEDGER {report['ledger_preflight']['path']} "
              f"sha {report['ledger_preflight']['sha256']}")
        with LiveBuilder(actor=ACTOR, dry=args.dry) as b:
            report["players"] = (str(b.srv.command("players"))[:200]
                                 if not args.dry else "dry")
            print(f"players: {report['players']}")
            if not args.dry:
                report["verb_preflight"] = verb_preflight(b.srv)
                print("verb pre-flight: "
                      + report["verb_preflight"]["verdict"])
            build_stages(b, d, list(SHED_STAGE_ORDER), site["id"], report,
                         dry=args.dry,
                         from_batch=int(getattr(args, "from_batch", 1) or 1))
            report["close"] = b.close()
    finally:
        B_STAGE_MATCH, B_STAGE_ORDER = saved_match, saved_order
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=1, default=str))
    problems = [q for st in report["stages"] for r in st["records"]
                for q in (r.get("schema_problems") or [])]
    print(json.dumps({"site": site["id"], "players": report.get("players"),
                      "per_stage": {st["stage"]: len(st["records"])
                                    for st in report["stages"]},
                      "schema_problem_count": len(problems),
                      "report": args.report}, indent=1, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harbour_b")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("design", help="lay out harbour B; READ-ONLY")
    d.add_argument("--out", default="/tmp/hb/design.json")
    d.add_argument("--plans", default="/tmp/hb/plans")
    d.set_defaults(fn=cmd_design)
    sh = sub.add_parser("sheds", help="solve the small boathouses; READ-ONLY")
    sh.add_argument("--out", default="/tmp/hb/sheds.json")
    sh.add_argument("--plans", default="/tmp/hb/plans")
    sh.set_defaults(fn=cmd_sheds)
    pf = sub.add_parser("preflight",
                        help="live cylinder census over every village seat; "
                             "READ-ONLY, no ledger handle taken")
    pf.add_argument("--out", default="/tmp/hb/preflight.json")
    pf.set_defaults(fn=cmd_preflight)
    vv = sub.add_parser("verify",
                        help="verify what stands, LIVE; READ-ONLY")
    vv.add_argument("--site", default=None,
                    help="a shed site id; omit for harbour B itself")
    vv.add_argument("--report", default="/tmp/hb/verify.json")
    vv.add_argument("--skip-live", action="store_true")
    vv.set_defaults(fn=cmd_verify)
    bu = sub.add_parser("build", help="build harbour B on the LIVE world")
    bu.add_argument("--stages", nargs="*", choices=B_STAGE_ORDER)
    bu.add_argument("--dry", action="store_true")
    bu.add_argument("--site", default=None,
                    help="the declared site this run is asked to build; the "
                         "build stage passes the unit it is running. This "
                         "module's authored design is for ONE site and "
                         "refuses to build another under its name")
    bu.add_argument("--report", default="/tmp/hb/build_report.json")
    bu.add_argument("--actor", default=None,
                    help="who this run's records are stamped as; the build "
                         "stage passes the pipeline's actor, standalone use "
                         "keeps the module's own")
    bu.add_argument("--from-batch", type=int, default=1)
    bu.add_argument("--expect-sha", default=None)
    bu.set_defaults(fn=cmd_build)
    sb = sub.add_parser("shedbuild", help="build one small boathouse, LIVE")
    sb.add_argument("--site", required=True)
    sb.add_argument("--dry", action="store_true")
    sb.add_argument("--report", default="/tmp/hb/shed_report.json")
    sb.add_argument("--from-batch", type=int, default=1)
    sb.add_argument("--expect-sha", default=None)
    sb.set_defaults(fn=cmd_shedbuild)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
