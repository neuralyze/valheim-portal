#!/usr/bin/env python3
"""The northern trunk: how far north a road can be CONSTRUCTED, and the plan to build it.

WHY THIS TOOL EXISTS, AND WHAT IT CORRECTS.

A seed-acceptance scan of `Pirate68` measured the largest WALKABLE-AS-GENERATED
land component at 3,648 m north-south (z -936 .. +2712) and concluded the Deep
North inner edge at z = +8000 was unreachable by 5,288 m.  That number is
correct about the quantity it measures and WRONG ABOUT THE QUESTION.  It measures
terrain that is ALREADY walkable.  A road is a thing you BUILD, and three
construction techniques the walkable mask cannot see move the frontier:

  1. FORD / graded causeway -- terrain.  Water shallower than `FORD_DEPTH_M`
     below the plane is filled to road freeboard inside the +/-8 m apply clamp.
     This is what a road legitimately does (roads/ribbon.py's carriageway water
     rule is scoped to PROTECTED structures precisely so a causeway stays
     possible); the batter's water rule stays absolute.
  2. BOARDWALK on piles -- a STRUCTURE, no terrain write at all, so it can
     never drain a shoreline.  `wood_pole_log_4` piles carrying `wood_floor`,
     the same technique as the piers, deck at `DECK_DATUM_Y`.  Pile reach
     ~20 m MEASURED, so the bed must sit no deeper than DECK_DATUM_Y - 20.
  3. BRIDGE for a free span, ceiling 96 m MEASURED (72 m free span at a 3x
     safety factor on an iron stringer, 96 m absolute).  Beyond that, a FERRY
     PAIR.

THE MEASUREMENT THIS TOOL PRODUCES IS A LADDER, NOT A NUMBER, because "how far
north" has a different answer for every technique you allow, and a single figure
hides which constraint is binding.  MEASURED on seed Pirate68, 4 m cells over
x[-1450, 950] z[-900, 9400], multi-source from the three points where the
EXISTING built network touches the corridor (stathub, brg-s1-w, brg-s2-w):

    graded land only, no water at all        z = +2,698 m   <- the old figure
    + ford, depth <= 1.0 m                   z = +4,294 m
    + boardwalk on piles, depth <= 4 m       z = +7,758 m
    + boardwalk to the ~20 m pile reach      z = +7,814 m
    + bridge, free span <= 96 m              z = +7,814 m   <- bridges add NOTHING
    + a hypothetical 400 m span              z = +9,398 m   <- the raster edge

So construction buys +5,116 m of northward reach over the walkable mask, and the
operator -- who flew the route and said it looked possible -- was right.

Corroboration that this is not a resolution artefact: the same search at 16 m
cells over x[-5200, 5200] z[-1600, 8800] returns z = +7,816 m against the 4 m
answer of +7,814 m.  Two independent lattices, 2 m apart.

AND THE ONE PLACE THE 4 m SCREEN WAS OPTIMISTIC, recorded because it changes the
terminus: at 1 m the last 54 m of that +7,814 m figure stands over a bed at
h = 10.27, i.e. a 20.33 m pile against a MEASURED ~20 m reach.  It is 0.33 m
past the instrument, so the buildable walkable terminus is the northernmost LAND
on the main component, z = +7,739.5 m at x = -845 (Mistlands/Mountain edge).

WHERE IT STOPS, AND WHY -- MEASURED, NOT ASSERTED.  From that frontier the
nearest Deep North shore is 278.0 m away at 1 m resolution: south (-844, 7740)
h = 30.71 to north (-844, 8018) h = 30.51.  Fifty-five per cent of that chord
stands over a bed at the 0.00 ocean-floor clamp, i.e. a 30.6 m pile against ~20 m
of reach, and 278 m is 2.90x the 96 m span ceiling.  BOTH ceilings are exceeded
in the same chord, which is the east-isle crossing's signature exactly.  That is
a FERRY and there is no argument to have about it.

THE STRUCTURAL COUNT, which is the surprise and the reason this is buildable at
all.  Labelling (land | water shallower than the ford limit) at 1 m over the
whole corridor and testing component identity across the patch overlaps:

    west isle + the swamp chain     ONE component, z 2400 .. 4199  (2.82 km2)
    the northern landmass           ONE component, z 4075 .. 7744
    a 195 m2 islet                  z 7744.5 .. 7760.5

so the entire 8.5 km corridor needs exactly ONE structural crossing: an 11.2 m
channel at (-40.5, 4070.5) -> (-45.5, 4080.5), bed 27.33, i.e. 2.67 m of water
and a 3.27 m pile.  That is the S1 bridge's crossing to within 0.8 m of span and
0.15 m of bed.  Everything else the road fords in terrain.

The islet buys +21.0 m of northing for a 79.2 m span at an 18.77 m pile -- 94 %
of the measured reach for 0.3 % of the route.  REFUSED with its numbers rather
than forced; it is recorded here so nobody has to re-derive it.

Usage:
    north.py reach      --field4 ... --field16 ...   the ladder, to JSON
    north.py crossings  --field1 ...                 X1 and the ferry chord at 1 m
    north.py network    --field ... --grid ... --locations ... --provisional --out ...
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy import ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra as spdij

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
# ORDER MATTERS AND IT BIT TWICE.  `tools/jumpstart/network/` is a PACKAGE and
# `tools/jumpstart/roads/network.py` is a module; whichever directory comes
# FIRST on sys.path wins `import network`.
#
# So JUMPSTART is APPENDED, never inserted.  The obvious spelling --
# `insert(0, JUMPSTART)` guarded by `not in sys.path` -- puts the package
# ahead of this directory when run AS A SCRIPT, because Python has already
# put the script's own directory at sys.path[0] and the guard then skips it
# while still inserting JUMPSTART in front. MEASURED: `python3 north.py plan`
# died on `module 'network' has no attribute 'MAX_BRIDGE_SPAN_M'`, i.e. it had
# imported the package. JUMPSTART only has to supply `worldseed`, which no
# other directory defines, so the end of the path is the right place for it.
sys.path.insert(0, str(HERE))
if str(JUMPSTART) not in sys.path:
    sys.path.append(str(JUMPSTART))

import worldseed  # noqa: E402

# Publish --seed/--world before importing the original road/spec stack, which
# reads solved geography at import time. General generation never loads the
# historical fixture diagnostics below.
if __name__ == "__main__" and sys.argv[1:2] in (["network"], ["plan"], ["selftest"]):
    import north_itinerary
    raise SystemExit(north_itinerary.selftest() if sys.argv[1:2] == ["selftest"]
                     else north_itinerary.general_main(sys.argv[2:]))

import field as fieldmod  # noqa: E402
import network as netmod  # noqa: E402
import poi as poimod  # noqa: E402
import precensus as censusmod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402

# RESOLVED, not literal: `--seed`/`--world` beat `$VH_SEED`/`$VH_WORLD` beat
# the Ulfsland defaults. Historical diagnostic main() rebinds both.
SEED = worldseed.seed()
WORLD = worldseed.world()

# WHICH KEEP-OUT POPULATION THE ROUTER PLANS AGAINST.  `--poi-source
# {all,generator}` USED TO LIVE HERE, choosing between the mod-free generator
# dump and that dump plus `roads/mod_keepout.json`.  Both are gone as a CHOICE,
# because neither is a reading of the world this road will cross: the dump is
# mod-free by construction and the keepout file is a survey of ONE world's
# finished carriageways.  What the router plans against now is the dump (the
# [SEED] question) PLUS a CENSUS -- ground read live, after the zones were
# generated -- and `main()` rebinds `CENSUS` from `--census` or refuses.
CENSUS: censusmod.Census | None = None
POI_SOURCE = "generator+census"


def routing_pois() -> list:
    """The keep-out set a ROUTE is planned against.

    One function so the three call sites in this file cannot drift apart, and
    so a run with no census is something a caller has to ASK for by name
    (`--provisional`) rather than something it gets by forgetting.
    """
    return poimod.load() + ([] if CENSUS is None else CENSUS.pois())

# ---- the vertical frame, all MEASURED, none of it re-derived here ----------
WATER_LEVEL_M = fieldmod.WATER_LEVEL          # 30.0, ZoneSystem::c_WaterLevel
DECK_DATUM_Y = specmod.DECK_DATUM_Y           # 30.6, every deck in this world
PILE_REACH_M = 20.0                           # HardWood log piles, MEASURED
MAX_SPAN_M = netmod.MAX_BRIDGE_SPAN_M         # 96.0, MEASURED absolute ceiling
FORD_DEPTH_M = routemod.FORD_MAX_DEPTH_M if hasattr(routemod, "FORD_MAX_DEPTH_M") else 1.0
LAND_FREEBOARD_M = 0.5                        # survey.py's land test, shared

# A cell is land if h > 30.5.  Stated once and shared by every function in this
# file, because the one defect this project pays for over and over is two gates
# answering the same question against two different thresholds.
LAND_H = WATER_LEVEL_M + LAND_FREEBOARD_M


# ---------------------------------------------------------------------------
# the reach ladder
# ---------------------------------------------------------------------------

def mosaic(fields: dict, step: float, x0: float, x1: float, z0: float, z1: float):
    """Stitch PatchScan patches of one step onto a common lattice.

    Refuses on any uncovered cell rather than leaving a NaN: a NaN height reads
    as "not land" in every downstream test, so a hole in the mosaic would be
    silently reported as ocean and would shorten the answer.
    """
    nx = int(round((x1 - x0) / step))
    nz = int(round((z1 - z0) / step))
    h = np.full((nz, nx), np.nan, np.float32)
    b = np.zeros((nz, nx), np.uint8)
    for fld in fields.values():
        if abs(fld.step - step) > 1e-9:
            continue
        fx = fld.cx - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
        fz = fld.cz - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
        jj = np.round((fx - x0 - step / 2) / step).astype(int)
        ii = np.round((fz - z0 - step / 2) / step).astype(int)
        oj = (jj >= 0) & (jj < nx)
        oi = (ii >= 0) & (ii < nz)
        h[np.ix_(ii[oi], jj[oj])] = np.asarray(fld.h)[np.ix_(oi, oj)]
        b[np.ix_(ii[oi], jj[oj])] = np.asarray(fld.biome)[np.ix_(oi, oj)]
    if np.isnan(h).any():
        raise SystemExit(
            f"REFUSING: {int(np.isnan(h).sum())} of {h.size} mosaic cells are "
            f"uncovered by any {step:g} m patch. An uncovered cell reads as "
            f"water in every land test below, so the reach would come back "
            f"SHORT for a reason that is not the terrain.")
    xs = x0 + np.arange(nx) * step + step / 2
    zs = z0 + np.arange(nz) * step + step / 2
    return h, b, xs, zs


def buildable_graph(h: np.ndarray, step: float, *, pile_m: float, span_m: float,
                    grade_ceiling: float = routemod.GRADE_CEILING,
                    smooth_sigma_cells: float = 3.0,
                    boardwalk_penalty: float = 3.0, bridge_penalty: float = 6.0,
                    climb_penalty: float = 2.0):
    """The buildable-corridor graph: what a ROAD CREW can traverse, not what a
    player can walk today.

    Three cell kinds, and the kind is the technique:
      1 GRADED    dry ground.  An edge exists only where the SMOOTHED grade is
                  within the network's 25 % ceiling -- smoothed because the
                  binding quantity is the large-scale grade the profile has to
                  follow, not one lattice edge's noise, and because a cut or
                  fill deeper than the clamp cannot be produced by terrain data
                  at all.  A mountain pass is therefore FOUND by this search,
                  never carved.
      2 BOARDWALK water whose bed carries a pile no longer than `pile_m`.
      3 BRIDGE    water within half a `span_m` free span of land -- the same
                  geometric encoding route.py uses, and it bounds a crossing's
                  WIDTH, not its LENGTH, so a run measured along the route still
                  has to be checked afterwards.
    Set `pile_m` negative to forbid water outright.
    """
    hs = ndi.gaussian_filter(h, sigma=smooth_sigma_cells, mode="nearest")
    land = h > LAND_H
    wet = ~land
    kind = np.zeros(h.shape, np.uint8)
    kind[land] = 1
    if pile_m >= 0.0:
        kind[wet & ((DECK_DATUM_Y - h) <= pile_m)] = 2
    if span_m > 0.0:
        d_land = ndi.distance_transform_edt(wet, sampling=step)
        kind[wet & (kind == 0) & (2.0 * d_land <= span_m)] = 3
    nz, nx = h.shape
    ids = np.arange(nz * nx).reshape(nz, nx)
    rows, cols, vals = [], [], []
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
        run = step * math.hypot(di, dj)
        a = slice(max(0, di), nz + min(0, di))
        b = slice(max(0, dj), nx + min(0, dj))
        a2 = slice(max(0, -di), nz + min(0, -di))
        b2 = slice(max(0, -dj), nx + min(0, -dj))
        k_f, k_t = kind[a, b], kind[a2, b2]
        rise = hs[a2, b2] - hs[a, b]
        dry = (k_f == 1) & (k_t == 1)
        ok = (k_f > 0) & (k_t > 0) & ~(dry & (np.abs(rise) / run > grade_ceiling))
        w = np.full(k_f.shape, run, np.float64)
        w = np.where(k_t == 2, run * boardwalk_penalty, w)
        w = np.where(k_t == 3, run * bridge_penalty, w)
        w = w + climb_penalty * np.maximum(0.0, np.where(dry, rise, 0.0))
        rows.append(ids[a, b][ok])
        cols.append(ids[a2, b2][ok])
        vals.append(w[ok])
    g = coo_matrix((np.concatenate(vals),
                    (np.concatenate(rows), np.concatenate(cols))),
                   shape=(nz * nx, nz * nx)).tocsr()
    return g, kind, hs


# Where the EXISTING built network touches this corridor.  All three are
# [SAVE]-derived positions of built road ends, and all three are sources because
# the network is already one walkable thing across the two built bridges: a
# single-source search from stathub answers a question about the spawn island
# alone and under-reads by the width of the S1 channel.
NETWORK_TOUCHPOINTS = {
    # spawn island: T4's north terminus, the station hub.
    "stathub": (660.0, 1088.0),
    # west isle: the north end of W5-brgs1-wsouth / W10-brgs1-treenear, i.e.
    # the last BUILT road sample on that landmass.  It is here and not at the
    # abutment because a bridge abutment's 4 m raster sample reads WATER -- the
    # deck covers the last metre by design -- and a water cell cannot be a
    # source in the graded-only rung.  Using the abutments alone made the first
    # rung read +2138 m instead of +2698 m: the search never left the spawn
    # island, and the defect was in the SOURCE SET, not in the terrain.
    "w5-north-end": (-326.0, -66.0),
    "brg-s2-w": (-349.0, 246.0),
}

LADDER = [
    ("graded land only (no water at all)", -1.0, 0.0),
    (f"+ ford, depth <= {FORD_DEPTH_M:g} m", FORD_DEPTH_M, 0.0),
    ("+ boardwalk on piles, depth <= 4 m", 4.0, 0.0),
    (f"+ boardwalk to the ~{PILE_REACH_M:g} m pile reach", PILE_REACH_M, 0.0),
    (f"+ bridge, free span <= {MAX_SPAN_M:g} m", PILE_REACH_M, MAX_SPAN_M),
    ("+ a hypothetical 400 m span", PILE_REACH_M, 400.0),
]


def reach(field4: str, field16: str, out: Path) -> dict:
    f4 = fieldmod.load(field4)
    h4, b4, xs4, zs4 = mosaic(f4, 4.0, -1450.0, 950.0, -900.0, 9400.0)
    nz, nx = h4.shape

    def cell(x, z):
        return (int(round((z - zs4[0]) / 4.0)), int(round((x - xs4[0]) / 4.0)))

    # SNAP EACH SOURCE TO LAND, AND RECORD THE SNAP.  A source that lands on a
    # water cell is silently dropped from the graded-only rung, which is a
    # shrink, and a shrink is silent unless it is written down.
    land4 = h4 > LAND_H
    d_land, land_ind = ndi.distance_transform_edt(~land4, sampling=4.0,
                                                  return_indices=True)
    srcs, snaps = [], {}
    for name, (x, z) in NETWORK_TOUCHPOINTS.items():
        i, j = cell(x, z)
        si, sj = int(land_ind[0, i, j]), int(land_ind[1, i, j])
        srcs.append((si, sj))
        snaps[name] = {"asked_xz": [x, z],
                       "used_xz": [float(xs4[sj]), float(zs4[si])],
                       "snapped_m": round(float(d_land[i, j]), 1),
                       "asked_cell_was_land": bool(land4[i, j])}
        if d_land[i, j] > 0.0:
            print(f"  source {name}: snapped {d_land[i, j]:.1f} m to land at "
                  f"({xs4[sj]:.0f}, {zs4[si]:.0f})")
    src_ids = [i * nx + j for i, j in srcs]
    rungs = []
    for label, pile, span in LADDER:
        g, kind, _ = buildable_graph(h4, 4.0, pile_m=pile, span_m=span)
        d = np.asarray(spdij(g, indices=src_ids, min_only=True)).reshape(nz, nx)
        got = np.isfinite(d)
        rows = np.where(got.any(axis=1))[0]
        zmax = float(zs4[rows.max()])
        js = np.where(got[rows.max()])[0]
        rungs.append({
            "technique": label, "pile_reach_m": pile, "span_ceiling_m": span,
            "z_max_m": zmax,
            "x_at_z_max": [float(xs4[js.min()]), float(xs4[js.max()])],
            "cells_reachable": int(got.sum()),
        })
        print(f"{label:42s} z = {zmax:+9.1f} m   "
              f"x {xs4[js.min()]:.0f}..{xs4[js.max()]:.0f}")

    # The 16 m corroboration, on a different lattice over a 4.3x wider box, so a
    # reader can tell a terrain answer from a resolution answer.
    f16 = fieldmod.load(field16)
    h16, b16, xs16, zs16 = mosaic(f16, 16.0, -5200.0, 5200.0, -1600.0, 8800.0)
    nz16, nx16 = h16.shape
    g16, kind16, _ = buildable_graph(h16, 16.0, pile_m=PILE_REACH_M, span_m=MAX_SPAN_M)
    i0 = int(round((1088.0 - zs16[0]) / 16.0))
    j0 = int(round((660.0 - xs16[0]) / 16.0))
    d16 = np.asarray(spdij(g16, indices=i0 * nx16 + j0)).reshape(nz16, nx16)
    got16 = np.isfinite(d16)
    rows16 = np.where(got16.any(axis=1))[0]
    z16 = float(zs16[rows16.max()])
    print(f"{'16 m corroboration (x +/-5200)':42s} z = {z16:+9.1f} m")

    # The stop: the nearest land NORTH of the frontier, and its biome.
    dn = (h16 > LAND_H) & (b16 == fieldmod.BIOME_CODE["DeepNorth"])
    d_to_reach, inds = ndi.distance_transform_edt(~got16, sampling=16.0,
                                                 return_indices=True)
    ii, jj = np.where(dn)
    gaps = d_to_reach[ii, jj]
    k = int(np.argmin(gaps))
    a, b = int(ii[k]), int(jj[k])
    stop = {
        "frontier_z_m": z16,
        "nearest_deepnorth_shore_xz": [float(xs16[b]), float(zs16[a])],
        "gap_m_at_16m": round(float(gaps[k]), 1),
        "south_shore_xz": [float(xs16[int(inds[1, a, b])]),
                           float(zs16[int(inds[0, a, b])])],
        "span_ceiling_m": MAX_SPAN_M,
        "verdict": "FERRY -- both ceilings exceeded in the same chord",
    }
    print(f"stop: nearest DeepNorth land {stop['gap_m_at_16m']} m north of the "
          f"frontier at {stop['nearest_deepnorth_shore_xz']}")

    doc = {
        "owner": "TrunkNorth", "world": WORLD, "seed": SEED,
        "what": "BUILDABLE-CORRIDOR reach, which supersedes the walkable-mask "
                "figure of +2712 m. The walkable mask measures terrain that is "
                "ALREADY walkable; this measures terrain a road can be "
                "CONSTRUCTED to.",
        "method": "MEASURED. Dijkstra over PatchScan heights, multi-source from "
                  "the three points where the EXISTING built network touches "
                  "this corridor. Cell kinds are TECHNIQUES: graded dry ground "
                  "inside the 25% network grade ceiling on a 12 m-smoothed "
                  "field, boardwalk where the bed carries a pile within reach, "
                  "bridge where water is within half a free span of land.",
        "instrument": "tools/jumpstart/roads/north.py reach",
        "constants": {
            "water_level_y": WATER_LEVEL_M, "deck_datum_y": DECK_DATUM_Y,
            "pile_reach_m": PILE_REACH_M, "max_span_m": MAX_SPAN_M,
            "ford_depth_m": FORD_DEPTH_M, "land_test": f"h > {LAND_H}",
            "grade_ceiling": routemod.GRADE_CEILING,
            "clamp_m": routemod.CLAMP_M,
        },
        "sources": NETWORK_TOUCHPOINTS,
        "source_snaps": snaps,
        "ladder_4m": rungs,
        "corroboration_16m": {"z_max_m": z16, "box_x": [-5200, 5200],
                              "box_z": [-1600, 8800]},
        "superseded_claim": {
            "old_z_max_m": 2712.0,
            "old_method": "largest WALKABLE-AS-GENERATED land component",
            "reproduced_as": "the 'graded land only' rung",
            "why_it_was_wrong": "it measured whether a player can walk there "
                                "today, not whether a road can be built there",
        },
        "stop": stop,
    }
    Path(out).write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out}")
    return doc


# ---------------------------------------------------------------------------
# the crossings, at 1 m
# ---------------------------------------------------------------------------

def _patch_arrays(fld):
    px = fld.cx - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    pz = fld.cz - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    return px, pz, np.asarray(fld.h), np.asarray(fld.biome)


def ford_components(fld):
    """(land | water within the ford depth) at 1 m, 8-connected.

    This is the set a TERRAIN road can cross without a structure, so its
    components are exactly the segments' natural boundaries and the count of
    gaps between them is exactly the count of structures the route needs.
    """
    px, pz, ph, pb = _patch_arrays(fld)
    land = ph > LAND_H
    ford = (~land) & ((WATER_LEVEL_M - ph) <= FORD_DEPTH_M)
    lab, n = ndi.label(land | ford, structure=np.ones((3, 3), int))
    return px, pz, ph, pb, land, ford, lab, n


def chord(px, pz, ph, a_ij, b_ij):
    """Sample the bed along the straight line between two cells, at 1 m."""
    (ai, aj), (bi, bj) = a_ij, b_ij
    n = max(2, int(round(math.hypot(bi - ai, bj - aj))) + 1)
    tz = np.linspace(pz[ai], pz[bi], n)
    tx = np.linspace(px[aj], px[bj], n)
    bed = ph[np.round(tz - pz[0]).astype(int), np.round(tx - px[0]).astype(int)]
    return tx, tz, bed


def gap_between(fld, lab, ca: int, cb: int) -> dict:
    """The narrowest chord between two ford-components, with its bed profile."""
    px, pz, ph, _pb = _patch_arrays(fld)
    a = lab == ca
    d, inds = ndi.distance_transform_edt(~a, sampling=1.0, return_indices=True)
    ii, jj = np.where(lab == cb)
    dd = d[ii, jj]
    k = int(np.argmin(dd))
    bi, bj = int(ii[k]), int(jj[k])
    ai, aj = int(inds[0, bi, bj]), int(inds[1, bi, bj])
    _tx, _tz, bed = chord(px, pz, ph, (ai, aj), (bi, bj))
    pile = DECK_DATUM_Y - float(bed.min())
    over = float((bed < DECK_DATUM_Y - PILE_REACH_M).mean())
    return {
        "span_m": round(float(dd[k]), 1),
        "south_xz": [float(px[aj]), float(pz[ai])],
        "north_xz": [float(px[bj]), float(pz[bi])],
        "south_h": round(float(ph[ai, aj]), 2),
        "north_h": round(float(ph[bi, bj]), 2),
        "bed_min_h": round(float(bed.min()), 2),
        "max_pile_m": round(pile, 2),
        "deck_datum_y": DECK_DATUM_Y,
        "frac_chord_past_pile_reach": round(over, 3),
        "technique": ("boardwalk on piles" if pile <= PILE_REACH_M
                      else ("bridge" if dd[k] <= MAX_SPAN_M else "ferry")),
        "within_span_ceiling": bool(dd[k] <= MAX_SPAN_M),
        "within_pile_reach": bool(pile <= PILE_REACH_M),
    }




# ---------------------------------------------------------------------------
# the graded corridor: FIND the pass, never carve it
# ---------------------------------------------------------------------------

GRADED_CELL_M = 2.0
# 40 m each side: 2.8x the 14.5 m half-corridor, so the 4 m refinement can shift
# the line by a corridor width and no further.  It started at 120 m and that was
# too loose -- MEASURED on N2, where A* used the slack to leave the proven line
# and came back with a 149.3 m span the profile solver then proved unbuildable,
# 1.6x the 96 m ceiling, over DRY ground dropping 18 m in 180 m. The proof is
# only worth having if the router has to stay inside it.
GRADED_BAND_M = 110.0

# THE CORRIDOR IS 14.5 m PER SIDE, NOT THE CARRIAGEWAY HALF, and feeding the
# carriageway half to the keep-out is the defect the manual names in one line:
# "a tool that clears, checks or reserves 8 m and then grades 29 m has measured
# the wrong object."  MEASURED consequence here, from the rasteriser: with the
# routing masks built at half_width 3.0 m, N0b wrote a sample inside Dolmen01's
# hard radius and N1 one inside StoneTowerRuins05's, and `clearance.py::
# verdict_for_samples` refused both segments outright -- one sample each, out on
# the BATTER, 11.5 m beyond anything the router had been asked to protect.
#   trunk carriageway half  6.0/2 = 3.0 m
#   shoulder                        1.0 m  -> edge  4.0 m
#   batter reach cap               10.5 m  -> total 14.5 m per side
CORRIDOR_HALF_M = 3.0 + 1.0 + 10.5

# ...AND ONE CONSTANT WAS ANSWERING TWO QUESTIONS, WHICH IS THE THIRD LEVER AND
# THE ONLY ONE THAT WORKS.
#
# `CORRIDOR_HALF_M` is the width of the ground the write MOVES, and that is the
# right number for the RELIEF test below: a 29 m bench has to fit, and the
# rasteriser judges every one of those samples against the +/-8 m clamp.
#
# It is the WRONG number for a location keep-out, and the two failures the
# operator has been handed are the two halves of the same mistake:
#   * at 3.0 m the router reserved less than the write gate judges, so the
#     SHOULDER and the batter landed inside hard radii -- three legs refused
#     for one sample each;
#   * at 14.5 m the router reserved ground the batter never keeps, because
#     `stamp` CLIPS a batter sample at a location boundary and records it.
#     Reserving it anyway closed the only fordable neck on the shelf and
#     `N2-shelf-mid1` came back "no route found" even in a 110 m band.
#
# What the write gate actually judges, after the clip fix in `ribbon.py::
# location_check`, is the CARRIAGEWAY footprint against `reach +
# TERRAIN_SPREAD_M`.  The carriageway reaches `edge` = 4.0 m from the
# centreline, so a centreline that clears `reach + 1.0 + 4.0` clears the gate,
# and `poi.Poi.hard_m` already adds its own 3.0 m piece pad on top of the same
# reach: at half_width 4.0 the router reserves `reach + 7.0`, which is 2.0 m
# MORE than the gate requires, before `masks` adds its chord slack.  So the
# router now reserves the object the gate measures, plus margin, and nothing
# for the ground the batter gives back.
# The ROAD SURFACE's own half-extent: the carriageway half plus the shoulder,
# i.e. the outermost sample `stamp` puts in `road_written`.  Two questions in
# this file reduce to it and they are both about the road rather than the
# earthwork: what the POI gate judges, and what the apply clamp has to absorb
# across a level bench.
CARRIAGEWAY_HALF_M = 3.0 + 1.0
POI_HALF_M = CARRIAGEWAY_HALF_M
# The same statement as a number the gate can be compared against: how far the
# centreline must stand off an instance of reach r, i.e. r + this.
CENTRELINE_STANDOFF_OVER_REACH_M = 1.0 + POI_HALF_M

# WHAT NO PROFILE CAN ABSORB, and it is arithmetic rather than taste.
# `TerrainComp::ApplyToHeightmap` discards a sample past +/-8 m of generated
# height, so ONE profile value can satisfy a cross section only if that
# section's generated heights span at most 2 x 8 = 16 m.  A cell whose
# carriageway-width relief exceeds that cannot carry a road at any profile,
# at any gradient, by any ordering of cut and fill.
#
# MEASURED, and this is why it is a FORBID and not another penalty: the
# aligned re-plan routed N0-brgs2-wmid across ground whose relief over the
# +/-4 m carriageway window peaks at 17.50 m at (-434.4, 278.4) -- 31.81 m
# over the full 29 m bench -- and the rasteriser refused the leg with 37
# carriageway samples past the clamp, worst -10.02 m at (-426, 265) where
# generated ground is 56.26 m and the fitted profile 46.24 m.  The existing
# bench-relief term is a 30 m-per-metre PENALTY over the DESIGN margin, and a
# penalty is the right shape for "this bench will be expensive"; it is the
# wrong shape for "this bench cannot exist".
CARRIAGEWAY_RELIEF_LIMIT_M = 2.0 * routemod.CLAMP_M

# ---------------------------------------------------------------------------
# the write gate, asked at plan time in the write gate's own units
# ---------------------------------------------------------------------------

_CLEARANCE_CACHE: list = []


def clearance_locations():
    """The CANONICAL instrument, loaded once, for THIS RUN'S SEED.

    Imported rather than reimplemented: `roads/poi.py` and
    `settlements/clearance.py` already compute the same per-type reach two
    ways, and the one thing this session proved expensive is a second spelling
    of a boundary.  The PREDICTION must read the number the GATE reads or it
    predicts something else.

    AND IT MUST READ THE SAME WORLD.  This function used to pick the first of
    two hardcoded paths, both of them `f6fe167f4fcd.json` -- Pirate68's own
    content hash -- so a prediction of the write gate on any other seed was a
    prediction about Ulfsland.  MEASURED on Fresh42 through the identical
    fault in `ribbon.py`: S7-stathub-wtnorth was refused by a `ShipSetting01`
    at (124.2, 263.8) that exists in Pirate68's dump and NOT in Fresh42's,
    while the plan-time gate on the same run read 12,376 Fresh42 instances and
    said clear.  `poi.location_dump` is the one resolver, keyed on the seed
    (`poi.LOCATION_DUMP_DEPENDS_ON`).
    """
    if not _CLEARANCE_CACHE:
        sys.path.insert(0, str(HERE.parent / "settlements"))
        import clearance
        dump = str(poimod.location_dump())
        _CLEARANCE_CACHE.append((clearance.load(dump), dump))
    return _CLEARANCE_CACHE[0]


def write_gate_prediction(loaded, nodes, poi_half_m: float) -> dict:
    """Will `ribbon.py::location_check` refuse this centreline, and by how much?

    The gate's rule, restated on a centreline: every CARRIAGEWAY sample must
    stand at least `reach + TERRAIN_SPREAD_M` from every instance, and the
    carriageway reaches `poi_half_m` from the centreline.  So the quantity is
    `centreline_distance - poi_half_m - (reach + TERRAIN_SPREAD_M)` and a
    negative value is a refusal with its own magnitude.

    Reported with the BINDING instance whether or not it refuses, because
    "clears every location" is a claim and this is the measurement behind it.
    """
    L, dump = loaded
    sys.path.insert(0, str(HERE.parent / "settlements"))
    import clearance
    N = [(float(a), float(b)) for a, b in
         np.asarray(nodes, dtype=np.float64).reshape(-1, 2)]
    hard = L.hard_radius(0.0)
    worst = None
    breaches = []
    # THE DISTANCE IS TO THE POLYLINE, NOT TO THE NODES, and this line used to
    # take `np.min` over the nodes.  `roads/poi.py::polyline_dist` was written
    # because that exact measurement reported a clear route the writer then
    # refused: A* returns cell centres and `route.simplify` cuts chords to a
    # 1 m tolerance, so a straight leg is TWO nodes however long it is, and an
    # instance 1.5 m off the middle of a 200 m straight measures ~100 m to the
    # nearest node.  A key over the NODES is a key naming a SUBSET of the
    # ground the carriageway occupies.
    #
    # MEASURED on the only corpus that exists for this file, `north_segments.
    # yaml` (6 segments, 12,301 Pirate68 instances): the two forms agree here
    # -- 0 breaches missed on every segment, worst margins identical to 0.01 m
    # (N2-shelf-mid1 -2.51 node-only vs -2.52 polyline) -- because these
    # centrelines are stationed densely enough that a node is always near the
    # nearest point.  That is a property of THIS corpus, not of the rule, and
    # it is exactly the shape of silence a second spelling of a boundary
    # gives: correct by luck until a leg simplifies to two nodes.
    dists = poimod.polyline_dist(N, [float(v) for v in L.xz[:, 0]],
                                 [float(v) for v in L.xz[:, 1]])
    for i in range(len(L.names)):
        d = float(dists[i])
        if d - poi_half_m > float(hard[i]) + 60.0:
            continue
        margin = d - poi_half_m - float(hard[i])
        rec = {"name": L.names[i], "prefab": L.prefabs[i],
               "xz": [round(float(L.xz[i, 0]), 1), round(float(L.xz[i, 1]), 1)],
               "reach_m": round(float(L.reach[i]), 2),
               "hard_radius_m": round(float(hard[i]), 2),
               "centreline_dist_m": round(d, 2),
               "carriageway_edge_dist_m": round(d - poi_half_m, 2),
               "margin_m": round(margin, 2)}
        if worst is None or margin < worst["margin_m"]:
            worst = rec
        if margin < 0.0:
            breaches.append(rec)
    return {
        "verdict": "clear" if not breaches else "WOULD_REFUSE",
        "worst_margin_m": None if worst is None else worst["margin_m"],
        "binding_instance": worst,
        "breaches": sorted(breaches, key=lambda r: r["margin_m"]),
        "carriageway_half_m": poi_half_m,
        "terrain_spread_m": clearance.TERRAIN_SPREAD_M,
        "dump": dump,
        "method": ("INFERRED FROM A MEASURED BOUNDARY: the centreline's "
                   "minimum PERPENDICULAR distance to each instance "
                   "(roads/poi.py::polyline_dist, over the polyline and not "
                   "over its nodes), less the carriageway "
                   "half-extent, against that instance's own "
                   "clearance.Locations.hard_radius(0.0) = reach + "
                   "TERRAIN_SPREAD_M -- the identical array "
                   "ribbon.py::location_check compares written samples to. It "
                   "is a PREDICTION because a centreline is not a footprint: "
                   "the rasteriser judges every 1 m sample and remains the "
                   "gate."),
        "tool": "tools/jumpstart/roads/north.py::write_gate_prediction",
    }



def gate_forbid_mask(loaded, x0: float, z0: float, cell_m: float, m: int,
                     poi_half_m: float) -> tuple[np.ndarray, float]:
    """The cells a centreline may not occupy WITHOUT the write being refused.

    `poi.masks` builds a keep-out out of a MEASURED reach plus two TASTE picks
    (`ROAD_MARGIN_M` 6.0, `PIECE_PAD_M` 3.0).  This builds the part that is not
    taste: `clearance.Locations.hard_radius(0.0)` -- the identical array
    `ribbon.py::location_check` refuses against -- plus the carriageway
    half-extent, plus the same chord slack `poi.masks` uses, because A* returns
    cell centres and the simplified polyline can sit `cell/sqrt(2) + 1` inside
    a cell whose centre cleared.

    WHY IT HAS TO EXIST SEPARATELY FROM `poi.masks`.  The refinement LIFTS the
    POI forbid where the proof had no alternative, and that licence is right
    for a taste margin and WRONG for the gate: lifting a boundary the write
    gate will refuse does not buy a route, it buys a leg that cannot be
    written.  MEASURED on the aligned re-plan before this mask existed --
    N0b -2.07 m, N1 -0.41 m, N2 -0.06 m past the gate boundary, all three
    inside a lifted pinch, all three legs predicted refused with a routable
    corridor in hand.
    """
    L, _dump = loaded
    slack = cell_m / math.sqrt(2.0) + 1.0
    r = L.hard_radius(0.0) + float(poi_half_m) + slack
    out = np.zeros((m, m), bool)
    xlo, xhi = x0 - cell_m, x0 + m * cell_m
    zlo, zhi = z0 - cell_m, z0 + m * cell_m
    for i in range(len(L.names)):
        px, pz, rr = float(L.xz[i, 0]), float(L.xz[i, 1]), float(r[i])
        if not (xlo - rr <= px <= xhi + rr and zlo - rr <= pz <= zhi + rr):
            continue
        jc, ic = (px - x0) / cell_m, (pz - z0) / cell_m
        k = int(math.ceil(rr / cell_m))
        j0, j1 = max(0, int(jc) - k), min(m - 1, int(jc) + k + 1)
        i0, i1 = max(0, int(ic) - k), min(m - 1, int(ic) + k + 1)
        if j0 > j1 or i0 > i1:
            continue
        dx = (np.arange(j0, j1 + 1) - jc) * cell_m
        dz = (np.arange(i0, i1 + 1) - ic) * cell_m
        sub = np.hypot(dx[None, :], dz[:, None]) <= rr
        if sub.any():
            out[i0:i1 + 1, j0:j1 + 1] |= sub
    return out, slack


_CLAIM_ZONE_CACHE: list = []


def protected_claim_zones() -> dict:
    """Zones a `flatten: FORBIDDEN` site has already TERRAIN-WRITTEN.

    THE ROAD MUST NOT CLAIM ONE, AND THE REASON IS THE UNION RATHER THAN THE
    GROUND.  A zone holds exactly one `_TerrainCompiler`, so a road writing a
    zone somebody else wrote must carry every one of their samples forward
    verbatim -- `ribbon.py` does, per sample index, and the world is unchanged
    by it.  `writer.py::_flatten_forbidden_check` then decodes MY blob and
    measures the applied delta against GENERATED height at each protected
    piece, so the carried-forward samples read as MY write moving the ground.

    MEASURED, at append time, after the zones had already been generated:
    `N0b-wmid-wnorth` was refused for 38 pieces of `lh-north` (the North
    Lighthouse, over water, flatten=FORBIDDEN) moved by 0.020 to 0.751 m --
    every one of them ground the LIGHTHOUSE's own `terrain_write` at seq 2163
    had already moved, in the two zones (-3, 40) and (-3, 41) it claimed.  The
    rasteriser had refused nothing there and was right to: `skipped_protected`
    was 0 because no sample of ours comes within `PROTECT_CLEAR_M` of a piece.

    So this is not a clearance question and no keep-out radius answers it: it
    is ZONE GRANULAR, because the compiler is.  The corridor keeps out of the
    zones, and a leg boundary that sits inside one has to move -- which is why
    `w-northroad` exists.
    """
    if _CLAIM_ZONE_CACHE:
        return _CLAIM_ZONE_CACHE[0]
    import importlib
    sys.path.insert(0, str(HERE.parent / "ledger"))
    writer = importlib.import_module("writer")
    led = writer.Ledger.open(WORLD, actor="RoadNorthBuild")
    recs = led.records()
    retired = writer.retired_seqs(recs)
    sites = set()
    for i, r in enumerate(recs):
        p = r["params"]
        # BOTH KEEP-OUT KINDS.  This asks which SITES already terrain-wrote
        # a zone, because a compiler is per zone and sharing one means
        # carrying that site's samples forward under our name - and that is
        # true of a RESEAT site as much as a FORBIDDEN one.  Matching the
        # literal string would have dropped every lamp site out of this set
        # the moment the emitters started writing RESEAT.
        if writer.keep_out_kind(p) is not None and r["seq"] not in retired:
            k = writer._site_key(p)
            sites.add((k[1] if k else None) or p.get("role") or f"line{i}")
    zones: dict[tuple[int, int], str] = {}
    for r in recs:
        if r["op"] != "terrain_write":
            continue
        p = r["params"]
        k = writer._site_key(p)
        site = (k[1] if k else None) or p.get("role")
        if site not in sites:
            continue
        for e in p.get("entries", []):
            zones[(int(e["zone"][0]), int(e["zone"][1]))] = site
    out = {"zones": zones,
           "sites": sorted(sites),
           "method": ("MEASURED from the Ulfsland ledger: every zone in a "
                      "terrain_write belonging to a site that carries a live "
                      "flatten=FORBIDDEN record. Zone granular because "
                      "Heightmap::GetAndCreateTerrainCompiler returns ONE "
                      "compiler per zone, so sharing a zone means carrying "
                      "that site's samples forward under our name."),
           "tool": "tools/jumpstart/roads/north.py::protected_claim_zones"}
    _CLAIM_ZONE_CACHE.append(out)
    return out


def claim_zone_forbid(x0: float, z0: float, cell_m: float, m: int,
                      extra: set[tuple[int, int]] | None = None,
                      grow_m: float | None = None) -> np.ndarray:
    """`protected_claim_zones` rasterised onto a routing lattice, grown by the
    written half-corridor so the BATTER cannot reach into the zone either.

    `extra` FORBIDS ZONES FOR A REASON THAT IS NOT A CLAIM, and it exists for
    exactly one measured case: a zone the GAME will not spawn.  A road may not
    cross such a zone whatever the ground looks like, because
    `ZoneSystem::SpawnZone` returning false leaves no `_ZoneCtrl`, a
    `_TerrainCompiler` written into an ungenerated zone is ignored at
    generation time (MEASURED on Ulfsland 2026-09-15, zone (-18,28): 190
    objects planted on the UNMODIFIED generator height, standing up to 7.68 m
    above the finished pad, permanently), and paint alpha is not honoured
    either.  So it is a ROUTING constraint of exactly the same kind as a
    mountain pass that needs more than 8 m of cut: found, not forced.

    `grow_m` defaults to `CORRIDOR_HALF_M` (14.5 m) for BOTH cases, and the
    first version of this function got that wrong in a way worth recording,
    because the wrong answer LOOKED more careful.

    It grew an ungenerated zone by `CARRIAGEWAY_HALF_M` (4.0 m) instead,
    reasoning that "a batter sample in the bad zone is exactly as unwritable
    as a carriageway one" and that reserving 14.5 m would close necks the road
    can legitimately use.  The first clause is true and the CONCLUSION IS
    STILL WRONG, because the question is not which samples are unwritable --
    it is WHICH ZONES THE WRITE TOUCHES.  `ribbon.py` derives its zone list,
    its `zones_generate` batches and its `terrain_write` entries from where
    samples LAND, and one batter sample inside a zone puts that whole zone in
    the write.  MEASURED: with the 4.0 m grow, N2's centreline cleared
    (-9, 76) completely -- 0 of its densified centreline cells inside it --
    and the leg's stamped zone list STILL contained (-9, 76), because the
    batter reaches 10.5 m past the 4.0 m edge.  The re-route would have
    avoided the zone and then asked the game to generate it anyway, which is
    the original refusal with extra steps.

    So: a road AVOIDS a zone only when its whole 29 m bench avoids it.  Both
    numbers come from the one place that computes them, and the only reason
    `grow_m` remains a parameter is to make a different choice visible at the
    call site rather than buried here.
    """
    claims = dict(protected_claim_zones()["zones"])
    out = np.zeros((m, m), bool)
    xs = x0 + np.arange(m) * cell_m
    zs = z0 + np.arange(m) * cell_m

    def mark(zones, grow):
        for (zx, zz) in zones:
            xlo, xhi = zx * 64.0 - 32.0 - grow, zx * 64.0 + 32.0 + grow
            zlo, zhi = zz * 64.0 - 32.0 - grow, zz * 64.0 + 32.0 + grow
            jj = np.nonzero((xs >= xlo) & (xs <= xhi))[0]
            ii = np.nonzero((zs >= zlo) & (zs <= zhi))[0]
            if len(jj) and len(ii):
                out[np.ix_(ii, jj)] = True

    if claims:
        mark(claims, CORRIDOR_HALF_M)
    if extra:
        mark(extra, CORRIDOR_HALF_M if grow_m is None else grow_m)
    return out




def graded_corridor(fld, a_xz, b_xz, *, cell_m: float = GRADED_CELL_M,
                    grade_max: float = None, cut_fill_m: float = None,
                    band_m: float = GRADED_BAND_M, pois=None,
                    half_width_m: float = POI_HALF_M,
                    forbid_zones: set[tuple[int, int]] | None = None):
    """PROVE a slope-limited corridor exists between two points, and return it.

    THE PROBLEM THIS SOLVES, MEASURED.  route.Corridor's A* scores a step
    against a smoothed surface and prices gradient, but nothing in it forbids a
    corridor that contains a CLIFF: a 4 m cell is only as steep as its two
    neighbours' smoothed means. On N4 that produced a 3,022.6 m route with a
    187.4 m span the profile solver then PROVED unbuildable -- the ground drops
    28.5 m from (-994, 7610) to (-850, 7730), which is 15.2 % on the chord and
    therefore looks fine, while the actual descent contains a step no profile
    can absorb inside the +/-7 m usable clamp. The land-only retry cannot help,
    because the obstacle is DRY: it is a chasm, not water. And the cost of not
    solving it is a 187 m viaduct nobody asked for at the very end of a 8.4 km
    road.

    THE INSTRUMENT.  A 2 m lattice of the same field, the same clipped-smoothed
    surface route.py fits against (`clip(mean over CUT_FILL/grade window,
    h +/- CUT_FILL)`), and an edge ONLY where that surface's grade is within the
    ceiling. A path through this graph is a PROOF that a graded ribbon can
    follow it; the absence of one is a proof that it cannot, which is a complete
    answer and the right kind of refusal.

    MEASURED on N4: the proven graded path is 2,303.9 m against A*'s 3,022.6 m,
    it descends to the terminus via (-483, 7425) and (-713, 7679), and it
    contains no chasm at all. So the chasm was never the terrain's answer -- it
    was the search's.

    Returns (path_xz, band_mask_on_2m_lattice, x2, z2, stats).
    """
    grade_max = routemod.GRADE_CEILING if grade_max is None else grade_max
    cut_fill_m = routemod.CUT_FILL_MAX if cut_fill_m is None else cut_fill_m
    dec = max(1, int(round(cell_m / fld.step)))
    cell_m = dec * fld.step
    n = (fld.n // dec) * dec
    blk = np.asarray(fld.h)[:n, :n].reshape(n // dec, dec, n // dec, dec)
    h2 = blk.mean(axis=(1, 3)).astype(np.float64)
    hmax2 = blk.max(axis=(1, 3))
    hmin2 = blk.min(axis=(1, 3))
    px = fld.cx - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    pz = fld.cz - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    x2 = px[:n].reshape(-1, dec).mean(axis=1)
    z2 = pz[:n].reshape(-1, dec).mean(axis=1)
    win = max(1, int(round((cut_fill_m / grade_max) / cell_m)))
    surf = np.clip(ndi.uniform_filter(h2, size=2 * win + 1, mode="nearest"),
                   h2 - cut_fill_m, h2 + cut_fill_m)
    land = hmax2 > LAND_H
    ford = (~land) & ((WATER_LEVEL_M - h2) <= FORD_DEPTH_M)
    deckable = (~land) & (~ford) & ((DECK_DATUM_Y - h2) <= PILE_REACH_M)
    passable = land | ford | deckable
    # WHAT NO PROFILE CAN ABSORB, FORBIDDEN RATHER THAN PRICED.
    #
    # The quantity the rasteriser refuses on is per SAMPLE: |profile -
    # generated| > 8 m.  Two things drive it and only together:
    #   * how far the PROFILE has to sit from the ground under the centreline,
    #     which is `|surf - h2|` -- `surf` is the same clipped-smoothed surface
    #     `route.fit_profile` fits against, so this is the plan's own number
    #     and not a new model;
    #   * how far the CARRIAGEWAY's outermost sample sits from the centreline's
    #     ground, which on a cross slope is about half the relief across the
    #     road's own width.
    # Their sum is the predicted worst sample deviation, and where it exceeds
    # the clamp no profile value satisfies the cross section.
    #
    # MEASURED, and it is why neither term alone is the test.  N0-brgs2-wmid
    # failed on the FIRST term's ground: relief over +/-4 m reaches 17.50 m at
    # (-434.4, 278.4), past 2 x 8 m, so the section is infeasible outright.
    # N4-mid2-terminus failed on the SUM: at (-1382, 6987) the relief is only
    # 5.50 m and the rasteriser still refused 239 samples, worst -9.76 m at
    # (-1283, 7146) where relief is 11.50 m -- the profile was already ~7 m
    # into its cut budget and half the cross slope took it past the clamp.  A
    # relief-only forbid at 16 m sees N0 and is blind to N4.
    #
    # Measured on the TRUE 1 m extremes -- `maximum_filter` over the block
    # MAXIMA against `minimum_filter` over the block MINIMA -- because a 2 m
    # block MEAN hides exactly the 1 m cliff the rasteriser will refuse, which
    # is the same aliasing that made `stations_over_clamp: 0` a true statement
    # about stations and a false one about the road.
    #
    # Scoped to ground that gets a TERRAIN WRITE: a deckable cell carries a
    # boardwalk on piles, writes no terrain at all and therefore has no clamp
    # to exceed.  Forbidding relief under a deck would refuse the technique
    # that makes this road possible.
    cw_win = max(1, int(round(CARRIAGEWAY_HALF_M / cell_m)))
    cw_relief = (ndi.maximum_filter(hmax2, size=2 * cw_win + 1, mode="nearest")
                 - ndi.minimum_filter(hmin2, size=2 * cw_win + 1,
                                      mode="nearest"))
    worst_sample_dev = np.abs(surf - h2) + cw_relief / 2.0
    # FORBID ONLY WHAT IS ARITHMETICALLY IMPOSSIBLE, PRICE THE REST.  The two
    # terms are not the same kind of claim.  `cw_relief > 2 x CLAMP_M` is
    # arithmetic: one profile value cannot satisfy a section spanning more
    # than 16 m, at any gradient, in any order of cut and fill.  The SUM is a
    # PREDICTION -- it assumes the profile spends its whole cut budget at the
    # same cell where the cross slope is worst -- and MEASURED, forbidding a
    # prediction closed N4's entire dry corridor and sent it over water with
    # eight crossings and a 29.79 m breach of
    # `Mistlands_DvergrTownEntrance2`'s hard radius, which is a far worse road
    # than the one the prediction was protecting.  So the sum is priced at
    # 60 m per metre over the clamp: the corridor steers off ground that will
    # refuse, and where there is no alternative it comes back with a route the
    # rasteriser can judge instead of a false impossibility.
    clamp_infeasible = (cw_relief > CARRIAGEWAY_RELIEF_LIMIT_M) & ~deckable
    clamp_pen = np.where(
        deckable, 0.0,
        60.0 * np.clip(worst_sample_dev - routemod.CLAMP_M, 0.0, None))
    # AND THE ZONES ANOTHER CLAIM ALREADY OWNS UNDER A FORBIDDEN FLAG.  Not a
    # clearance radius and not a relief test: the compiler is per ZONE, so
    # sharing one means carrying that site's samples forward under our name,
    # and `writer.py::_flatten_forbidden_check` reads them as ours.  See
    # `protected_claim_zones`.
    claim_forbid = claim_zone_forbid(float(x2[0]), float(z2[0]), cell_m,
                                     h2.shape[0], extra=forbid_zones)
    passable = passable & ~clamp_infeasible & ~claim_forbid
    # AND THE RELIEF ACROSS THE WHOLE CORRIDOR, not just along the line.  The
    # proof, the 4 m search and the 2 m station profile all measure the
    # CENTRELINE; the rasteriser writes every 1 m sample out to 14.5 m per side
    # and checks each one against the clamp. MEASURED gap between those two
    # objects: N3 came back needing a 20.55 m cut and N4 an 11.81 m cut -- 64
    # and 193 samples past the +/-8 m clamp -- on routes whose centreline
    # profile reported `stations_over_clamp: 0`. Both instruments were right
    # about different things.
    #
    # A cut and a fill inside the usable clamp can absorb at most 2 x 7 m of
    # relief across the bench, so ground whose local relief over the corridor
    # width exceeds that cannot carry a level 29 m bench at all. Forbidding it
    # in the proof is cheap and it is the same quantity the rasteriser will
    # judge.
    relief_win = max(1, int(round(CORRIDOR_HALF_M / cell_m)))
    hi_ = ndi.maximum_filter(h2, size=2 * relief_win + 1, mode="nearest")
    lo_ = ndi.minimum_filter(h2, size=2 * relief_win + 1, mode="nearest")
    corridor_relief = hi_ - lo_
    # PENALTY, NOT FORBID -- measured, again.  As a hard forbid this closed
    # FIVE of six legs ("no graded corridor exists"), because the endpoints
    # themselves sit on shoreline and saddle ground whose 29 m relief is over
    # the limit: every road on this seed begins and ends somewhere a 29 m level
    # bench does not fit, and refusing that is refusing the road. As a heavy
    # penalty the corridor steers onto ground that can carry the bench and
    # still reaches a terminus that cannot.
    too_rough = corridor_relief > 2.0 * cut_fill_m
    relief_pen = 30.0 * np.clip(
        corridor_relief - 2.0 * cut_fill_m, 0.0, None)
    # THE POI KEEP-OUT BELONGS IN THE PROOF, not only in the refinement.  With
    # the keep-out applied only to the 4 m A* the proven line ran straight
    # through locations the router then had to dodge, and every dodge spent the
    # band's slack -- which is how N2 got outside the proof. MEASURED breaches
    # before this: SwampHut3 at 10.9 m against a 14.0 m hard radius, two
    # InfestedTree01 at 6.7 m and 9.7 m against 11.0 m, Mistlands_Statue1 at
    # 7.9 m against 12.0 m, Mistlands_Viaduct1 at 22.8 m against 30.3 m -- five
    # breaches across three legs, every one of them a location the road could
    # simply have gone round. Same numbers, one corridor.
    poi_pen = np.zeros(h2.shape, np.float64)
    if pois is not None:
        pforbid, psoft, _pt = poimod.masks(pois, float(x2[0]), float(z2[0]),
                                           cell_m, h2.shape[0], half_width_m)
        # PENALTY, NOT FORBID, and the difference decided whether N1 exists.
        # With the keep-out applied as impassable in the proof, N1 came back
        # "no graded corridor exists" -- the swamp archipelago's only fordable
        # neck runs past a location, so a hard forbid at 2 m closed the single
        # route through and the tool declared the operator's road impossible on
        # the strength of a stand-off. A refusal that is caused by the
        # instrument is the worst kind. So the proof pays 400 m per cell to pass
        # a hard keep-out and 40 m per unit of soft: it will walk a long way
        # round, and where there is no way round it comes back with a route and
        # a recorded breach instead of a false impossibility.
        poi_pen = 400.0 * pforbid.astype(np.float64) + 40.0 * psoft
    m = h2.shape[0]
    ids = np.arange(m * m).reshape(m, m)
    rows, cols, vals = [], [], []
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
        d = cell_m * math.hypot(di, dj)
        a1 = slice(max(0, di), m + min(0, di))
        b1 = slice(max(0, dj), m + min(0, dj))
        a2 = slice(max(0, -di), m + min(0, -di))
        b2 = slice(max(0, -dj), m + min(0, -dj))
        wet_to = deckable[a2, b2]
        g = np.abs(surf[a2, b2] - surf[a1, b1]) / d
        good = (passable[a1, b1] & passable[a2, b2]
                & (wet_to | (g <= grade_max)))
        # Gentle ground is preferred and deck is dear, so the corridor lands on
        # the flattest line and only decks where it has to.
        w = (d * (1.0 + 3.0 * g / max(grade_max, 1e-9))
             + np.where(wet_to, 60.0 * d, 0.0) + poi_pen[a2, b2]
             + relief_pen[a2, b2] + clamp_pen[a2, b2])
        rows.append(ids[a1, b1][good])
        cols.append(ids[a2, b2][good])
        vals.append(w[good])
    g_sp = coo_matrix((np.concatenate(vals),
                       (np.concatenate(rows), np.concatenate(cols))),
                      shape=(m * m, m * m)).tocsr()

    def c2(x, z):
        return (int(round((z - z2[0]) / cell_m)), int(round((x - x2[0]) / cell_m)))

    si, sj = c2(*a_xz)
    ti, tj = c2(*b_xz)
    dist, pred = spdij(g_sp, indices=si * m + sj, return_predecessors=True)
    if not np.isfinite(dist[ti * m + tj]):
        raise ValueError(
            f"no graded corridor exists between {a_xz} and {b_xz} at "
            f"{grade_max * 100:.0f}% inside +/-{cut_fill_m} m on a {cell_m:g} m "
            f"lattice. This is a PROOF, not a tuning failure.")
    order, k = [], ti * m + tj
    while k >= 0:
        order.append(k)
        k = int(pred[k])
    order.reverse()
    path = [(float(x2[k % m]), float(z2[k // m])) for k in order]
    seed = np.zeros((m, m), bool)
    for k in order:
        seed[k // m, k % m] = True
    d_path = ndi.distance_transform_edt(~seed, sampling=cell_m)
    # AND THE BAND CARRIES THE SAME FORBID, because the band is what the 4 m
    # refinement is allowed to move inside.  A cell no profile can satisfy is
    # not a place the refinement may bend into to save 40 m of length -- that
    # is precisely how N0 left clamp-clean ground for a 17.50 m spur.
    band = (d_path <= band_m) & ~clamp_infeasible & ~claim_forbid
    # Cells the PROOF itself had to pass through a hard keep-out on.  Recorded,
    # and handed to the refinement as a licence, because a refinement that
    # forbids what the proof had to use answers "no route found" for a corridor
    # that demonstrably has one -- MEASURED on N1, which proved a 2,012.6 m
    # graded corridor and then failed to route inside it.
    on_path_keepout = seed & (poi_pen >= 400.0)
    # THE LICENCE IS SCOPED TO THE PINCH, NOT TO THE LINE.  Dilating the whole
    # proven path by 15 m lifted the keep-out on 201-387 cells per leg and the
    # breach count went from 1 to 7 per leg, because the refinement then took
    # shortcuts past locations it had a perfectly good way round. Dilating only
    # the cells the proof itself HAD to spend 400 m to pass through scopes the
    # licence to the places where there is no alternative: MEASURED, 2 cells on
    # N1 and zero on the other three legs.
    near_path = (ndi.distance_transform_edt(~on_path_keepout, sampling=cell_m)
                 <= 30.0) if on_path_keepout.any() else np.zeros_like(seed)
    length = sum(math.hypot(path[i + 1][0] - path[i][0],
                            path[i + 1][1] - path[i][1])
                 for i in range(len(path) - 1))
    stats = {"proven_graded_length_m": round(length, 1),
             # BOTH WIDTHS, because they are different objects and the run
             # that conflated them cost two full re-plans.
             "bench_half_m": CORRIDOR_HALF_M,
             "poi_keepout_half_m": half_width_m,
             "cells_refused_corridor_relief": int(too_rough.sum()),
             "corridor_relief_limit_m": 2.0 * cut_fill_m,
             "carriageway_relief_limit_m": CARRIAGEWAY_RELIEF_LIMIT_M,
             "cells_forbidden_clamp_infeasible": int(clamp_infeasible.sum()),
             "path_cells_inside_a_hard_poi_keepout": int(on_path_keepout.sum()),
             "poi_keepout_is_a_penalty_in_the_proof_m": 400.0,
             "lattice_m": cell_m, "grade_ceiling": grade_max,
             "cut_fill_m": cut_fill_m, "band_m": band_m,
             "band_cells": int(band.sum()),
             "deck_cells_on_path": int(sum(1 for k in order
                                           if deckable[k // m, k % m]))}
    return path, band | np.zeros_like(band), x2, z2, stats, near_path





# ---------------------------------------------------------------------------
# placed objects: what the batter must not undermine
# ---------------------------------------------------------------------------

def placed_from_ledger(centreline, reach_m: float = 40.0) -> dict:
    """Every piece THIS PROJECT has placed within `reach_m` of a centreline.

    WHY THE LEDGER AND NOT A LIVE CENSUS, stated so the two cannot be confused.
    `ribbon.py` refuses to write a batter without a placed-object map, and the
    hazard it guards is real: the hub portal `u-lheast` floats 0.536 m because a
    ribbon cut under it after it had been seated. But the northern corridor runs
    through zones that have NEVER BEEN GENERATED -- there are no ZDOs there at
    all until `zones_generate` runs, and `zones_generate` happens inside the
    same `ribbon.py build`. A live census before the write therefore measures an
    empty world and a live census after the write is too late to gate it.
    What CAN be measured before the write is the thing the gate actually exists
    for: pieces WE have placed. The ledger is the authority for those -- the
    manual's own rule, learned when an agent derived its removal set from its
    own run output and misread 764 objects another agent had already cleared.
    Vegetation is a different hazard with a different remedy (clearance, after
    the write), and vanilla locations are covered by `poi_standoff_discs`, which
    ribbon.py computes itself.

    The method string says all of this, because a map labelled "placed objects"
    that a reader assumes is a live census is exactly how a caveat dies.
    """
    import importlib
    sys.path.insert(0, str(HERE.parent / "ledger"))
    writer = importlib.import_module("writer")
    led = writer.Ledger.open(WORLD, actor="TrunkNorth")
    pts = [(float(x), float(z)) for x, z in centreline]
    out, retired = [], 0
    for rec in led.records():
        if rec.get("op") not in ("spawn", "spawn_plan", "portal"):
            continue
        params = rec.get("params") or {}
        cands = []
        if isinstance(params.get("pos"), list) and len(params["pos"]) >= 3:
            cands.append(params["pos"])
        for key in ("pieces", "plan", "objects"):
            v = params.get(key)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and isinstance(it.get("pos"), list):
                        cands.append(it["pos"])
                    elif isinstance(it, list) and len(it) >= 3:
                        cands.append(it)
        for pos in cands:
            px, pz = float(pos[0]), float(pos[2])
            if any(abs(px - x) <= reach_m and abs(pz - z) <= reach_m
                   for x, z in pts):
                out.append([round(px, 3), round(pz, 3)])
    return {
        "objects": out,
        "method": "MEASURED from the Ulfsland ledger (op in spawn/spawn_plan/"
                  "portal), NOT a live census. Scope: pieces this project has "
                  "placed within "
                  f"{reach_m:g} m of the centreline. Vegetation is NOT in this "
                  "map -- the corridor's zones are ungenerated until "
                  "ribbon.py's own zones_generate runs, so no vegetation ZDO "
                  "exists to census before the write; it is handled by "
                  "clearance AFTER the write. Vanilla locations are handled by "
                  "ribbon.poi_standoff_discs.",
        "reach_m": reach_m,
        "retired_skipped": retired,
    }



# ---------------------------------------------------------------------------
# the per-leg report: technique, gradient, cut/fill, biome -- and the two
# techniques the walkable mask could not see, stated with their numbers
# ---------------------------------------------------------------------------

SLIDE_LIMIT = math.tan(math.radians(fieldmod.PLAYER_SLIDE_DEG))   # 0.781


def _biome_mix(fld, nodes):
    px = fld.cx - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    pz = fld.cz - fld.half + np.arange(fld.n) * fld.step + fld.step / 2
    b = np.asarray(fld.biome)
    counts: dict[str, int] = {}
    for x, z in nodes:
        j = int(round((x - px[0]) / fld.step))
        i = int(round((z - pz[0]) / fld.step))
        if 0 <= i < fld.n and 0 <= j < fld.n:
            name = fieldmod.BIOME_NAMES.get(int(b[i, j]), "Unknown")
            counts[name] = counts.get(name, 0) + 1
    tot = max(1, sum(counts.values()))
    return {k: round(100.0 * v / tot, 1)
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def _grade_over(nodes, profile_y, baseline_m: float):
    """Worst gradient over a `baseline_m` run, which is the quantity the verdict
    is taken on.  ribbon.VERDICT_BASELINE_M is 8.0 m and the 1 m figure is a
    different quantity: a 1 m rise between adjacent samples on a fitted 8 %
    profile is a rounding artefact of the integer lattice, not a wall, and
    measuring the verdict at 1 m produced a false NOT WALKABLE."""
    y = np.asarray(profile_y, dtype=np.float64)
    pts = np.asarray(nodes, dtype=np.float64)
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    worst, where = 0.0, None
    for i in range(len(s)):
        j = int(np.searchsorted(s, s[i] + baseline_m))
        if j >= len(s):
            break
        g = abs(y[j] - y[i]) / max(s[j] - s[i], 1e-9)
        if g > worst:
            worst, where = g, (float(pts[i, 0]), float(pts[i, 1]))
    return worst, where


def _north_name() -> str:
    """THE NORTHERN PLAN'S FILE NAME inside `--out` -- Pirate68's alone.

    This module routes Ulfsland's north shore.  On any other seed there is no
    northern network, and reading or writing the bare name would be reading
    or overwriting Pirate68's (worldseed.north_segments_path)."""
    p = worldseed.north_segments_path()
    if p is None:
        raise SystemExit(
            f"REFUSED: roads/north.py plans Pirate68's northern network and "
            f"seed {worldseed.seed()!r} has none; its roads are in "
            f"{worldseed.segments_path()}.")
    return p.name


def report(field1: str, out: Path) -> dict:
    flds = fieldmod.load(field1)
    north = yaml.safe_load((out / _north_name()).read_text())
    legs = []
    # W2/W3 come from the existing network file: they are ALREADY ROUTED and
    # unbuilt, and they are part of this road because without them the northern
    # trunk is an island of pavement. Read, never re-planned -- re-routing a
    # segment somebody else solved is how two plans for one road appear.
    existing = yaml.safe_load(worldseed.segments_path().read_text())
    joiners = [s for s in existing["segments"]
               if s["id"].split("-")[0] in ("W2", "W3")]
    for seg in joiners + north["segments"]:
        patch = seg.get("patch")
        fld = flds.get(patch) or flds.get("westisle")
        nodes = seg["nodes"]
        is_bridge = seg.get("is_bridge") or [False] * len(nodes)
        st = seg["station_m"]
        deck_m = float(sum(is_bridge)) * st
        graded_m = seg["length_m"] - deck_m
        g8, at8 = _grade_over(nodes, seg["profile_y"], 8.0)
        g1, at1 = _grade_over(nodes, seg["profile_y"], 1.0)
        piles = [round(DECK_DATUM_Y - float(c["gap_min_y"]), 2)
                 for c in seg.get("crossings", [])]
        legs.append({
            "leg": seg["id"],
            "length_m": seg["length_m"],
            "technique": ("graded terrain only" if not seg.get("crossings")
                          else f"graded terrain + {len(seg['crossings'])} "
                               f"piled boardwalk(s)"),
            "graded_m": round(graded_m, 1),
            "boardwalk_deck_m": round(deck_m, 1),
            "fords_terrain": seg["ford_count"],
            "bridges_free_span": 0,
            "ferries": 0,
            "grade_limit_designed": seg["grade_limit"],
            "worst_gradient_8m": round(g8, 4),
            "worst_gradient_8m_at": at8,
            "worst_gradient_1m": round(g1, 4),
            "slide_limit": round(SLIDE_LIMIT, 3),
            "headroom_to_slide_limit": round(SLIDE_LIMIT - g8, 4),
            "walkable_vs_slide_limit": bool(g8 < SLIDE_LIMIT),
            "cut_max_m": seg["cut_max_m"],
            "fill_max_m": seg["fill_max_m"],
            "clamp_m": routemod.CLAMP_M,
            "stations_over_clamp": seg["stations_over_clamp"],
            "biome_pct": _biome_mix(fld, nodes) if fld else {},
            "zones": seg["zdo_cost"],
            "max_pile_m": max(piles) if piles else 0.0,
            "pile_reach_m": PILE_REACH_M,
        })

    # --- the swamp legs, explicitly: where we skirt and where we deck -------
    swamp = []
    for seg in north["segments"]:
        for c in seg.get("crossings", []):
            depth = c["clamp_evidence"]["max_depth_below_plane_m"]
            pile = DECK_DATUM_Y - float(c["gap_min_y"])
            swamp.append({
                "leg": seg["id"], "crossing": c["crossing_id"],
                "span_m": c["span_m"],
                "bank_a_xz": c["bank_a"]["xz"], "bank_b_xz": c["bank_b"]["xz"],
                "water_depth_below_plane_m": depth,
                "pile_length_m": round(pile, 2),
                "pile_reach_m": PILE_REACH_M,
                "within_pile_reach": bool(pile <= PILE_REACH_M),
                "free_span_needed": False,
                "why_not_a_free_span": "the bed carries piles at "
                    f"{pile:.2f} m, which is {pile / PILE_REACH_M * 100:.0f}% "
                    "of the MEASURED ~20 m reach, so every bay is supported "
                    "and the 96 m FREE-SPAN ceiling does not apply. Quoting "
                    "that ceiling against a piled deck is comparing two "
                    "different structures.",
                "deck_datum_y": DECK_DATUM_Y,
                "approach_arrives_on": f"the deck datum {DECK_DATUM_Y} m, "
                    "pinned before grading, so the graded approach meets the "
                    "deck with no step -- the defect DECK_DATUM_Y exists to "
                    "prevent, measured once at +0.648 m on T13",
                "bay_spacing_m": 4.0,
            })
    skirted = {
        "what": "wet ground the road SKIRTS rather than decks",
        "how_measured": "1 m ford-components: (land | water within the 1.0 m "
                        "ford depth), 8-connected, per patch, with component "
                        "identity checked across the patch overlaps",
        "result": "the whole west isle plus the northern swamp archipelago is "
                  "ONE component z 2400..4199, and the northern landmass is "
                  "ONE component z 4075..7744, so the route needed exactly ONE "
                  "structural crossing between them. Every other wet run on "
                  "the four legs is either a ford in terrain or a short piled "
                  "deck across a pool the corridor could not walk round.",
        "fords_in_terrain": sum(s["ford_count"] for s in north["segments"]),
        "decked_crossings": len(swamp),
    }

    # --- the mountain legs: the passes, FOUND not carved --------------------
    passes = []
    for seg in north["segments"]:
        y = np.asarray(seg["profile_y"], dtype=np.float64)
        t = np.asarray(seg["terrain_y"], dtype=np.float64)
        pts = np.asarray(seg["nodes"], dtype=np.float64)
        seglen = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
        s = np.concatenate([[0.0], np.cumsum(seglen)])
        # A PASS is a local maximum of the road's own profile with real relief
        # on both sides: the road goes OVER something there, and the question is
        # what it cost.
        win = max(3, int(round(120.0 / max(seg["station_m"], 1e-9))))
        for k in range(win, len(y) - win):
            if y[k] != y[k - win:k + win + 1].max():
                continue
            if y[k] <= min(y[k - win], y[k + win]) + 3.0:
                continue
            lo = int(max(0, k - win)), int(min(len(y) - 1, k + win))
            cut = float((y[lo[0]:lo[1] + 1] - t[lo[0]:lo[1] + 1]).min())
            fill = float((y[lo[0]:lo[1] + 1] - t[lo[0]:lo[1] + 1]).max())
            up = abs(y[k] - y[lo[0]]) / max(s[k] - s[lo[0]], 1e-9)
            dn = abs(y[lo[1]] - y[k]) / max(s[lo[1]] - s[k], 1e-9)
            passes.append({
                "leg": seg["id"],
                "saddle_xz": [round(float(pts[k, 0]), 1),
                              round(float(pts[k, 1]), 1)],
                "crest_profile_y": round(float(y[k]), 2),
                "generated_y_at_crest": round(float(t[k]), 2),
                "generated_relief_over_pass_m": round(
                    float(t[lo[0]:lo[1] + 1].max()
                          - t[lo[0]:lo[1] + 1].min()), 2),
                "grade_achieved_up": round(float(up), 4),
                "grade_achieved_down": round(float(dn), 4),
                "needs": ("cut" if cut < -0.25 and fill <= 0.25 else
                          "fill" if fill > 0.25 and cut >= -0.25 else
                          "cut and fill" if (cut < -0.25 and fill > 0.25)
                          else "neither"),
                "cut_m": round(cut, 2), "fill_m": round(fill, 2),
                "inside_usable_clamp": bool(max(abs(cut), abs(fill))
                                            <= routemod.CUT_FILL_MAX),
            })
    # de-duplicate saddles that are the same crest seen from two stations
    dedup, seen = [], []
    for pz_ in sorted(passes, key=lambda r: -r["crest_profile_y"]):
        if any(math.hypot(pz_["saddle_xz"][0] - q[0],
                          pz_["saddle_xz"][1] - q[1]) < 150.0 for q in seen):
            continue
        seen.append(pz_["saddle_xz"])
        dedup.append(pz_)

    doc = {"owner": "TrunkNorth", "world": WORLD, "seed": SEED,
           "instrument": "tools/jumpstart/roads/north.py report",
           "slide_limit": round(SLIDE_LIMIT, 3),
           "design_grade_trunk": routemod.GRADE["trunk"],
           "design_grade_mountain": routemod.GRADE["mountain"],
           "legs": legs,
           "totals": {
               "length_m": round(sum(l["length_m"] for l in legs), 1),
               "graded_m": round(sum(l["graded_m"] for l in legs), 1),
               "boardwalk_deck_m": round(
                   sum(l["boardwalk_deck_m"] for l in legs), 1),
               "zones": sum(l["zones"] for l in legs),
               "worst_gradient_8m": max(l["worst_gradient_8m"] for l in legs),
               "worst_cut_m": min(l["cut_max_m"] for l in legs),
               "worst_fill_m": max(l["fill_max_m"] for l in legs),
               "stations_over_clamp": sum(l["stations_over_clamp"]
                                          for l in legs),
           },
           "swamp": {"skirted": skirted, "decked": swamp},
           "mountain_passes": dedup,
           "refusals": north.get("refusals", [])}
    (out / "north_report.json").write_text(json.dumps(doc, indent=2) + "\n")

    w = max(len(l["leg"]) for l in legs)
    print(f"{'leg':{w}s} {'len_m':>8s} {'graded':>8s} {'deck':>7s} "
          f"{'fords':>5s} {'g8':>7s} {'vs0.781':>8s} {'cut':>6s} {'fill':>6s} "
          f"{'clamp':>5s}  biome")
    for l in legs:
        bio = ", ".join(f"{k} {v:g}%" for k, v in list(l["biome_pct"].items())[:3])
        print(f"{l['leg']:{w}s} {l['length_m']:8.1f} {l['graded_m']:8.1f} "
              f"{l['boardwalk_deck_m']:7.1f} {l['fords_terrain']:5d} "
              f"{l['worst_gradient_8m']:7.4f} {'OK' if l['walkable_vs_slide_limit'] else 'FAIL':>8s} "
              f"{l['cut_max_m']:6.2f} {l['fill_max_m']:6.2f} "
              f"{l['stations_over_clamp']:5d}  {bio}")
    t = doc["totals"]
    print(f"{'TOTAL':{w}s} {t['length_m']:8.1f} {t['graded_m']:8.1f} "
          f"{t['boardwalk_deck_m']:7.1f} {'':5s} {t['worst_gradient_8m']:7.4f} "
          f"{'':8s} {t['worst_cut_m']:6.2f} {t['worst_fill_m']:6.2f} "
          f"{t['stations_over_clamp']:5d}  {t['zones']} zones")
    print()
    print("SWAMP -- decked spans (deck datum "
          f"{DECK_DATUM_Y} m, bay spacing 4.0 m):")
    for c in swamp:
        print(f"  {c['crossing']:26s} span {c['span_m']:6.1f} m  "
              f"water {c['water_depth_below_plane_m']:5.2f} m below plane  "
              f"pile {c['pile_length_m']:5.2f} m / {PILE_REACH_M:g} m reach  "
              f"{c['bank_a_xz']} -> {c['bank_b_xz']}")
    print(f"  fords kept as TERRAIN: {skirted['fords_in_terrain']}; "
          f"decked crossings: {skirted['decked_crossings']}")
    print()
    print("MOUNTAIN -- passes FOUND, not carved:")
    for r in dedup:
        print(f"  {r['leg']:18s} saddle {str(r['saddle_xz']):>18s} crest "
              f"{r['crest_profile_y']:6.2f} m  generated relief "
              f"{r['generated_relief_over_pass_m']:6.2f} m  grade up "
              f"{r['grade_achieved_up']:.3f} down {r['grade_achieved_down']:.3f}"
              f"  needs {r['needs']} (cut {r['cut_m']:.2f} / fill "
              f"{r['fill_m']:.2f}, inside clamp "
              f"{r['inside_usable_clamp']})")
    return doc



# ---------------------------------------------------------------------------
# the ferry across the final chord
# ---------------------------------------------------------------------------
#
# THE DECISION RULE IS TWO TESTS AND YOU MUST SAY WHICH ONE REFUSED.  Recording
# "it is a ferry" without both numbers is how a later session re-litigates the
# bridge, so both are here, MEASURED at 1 m on the `gapdn` patch:
#
#   shore gap                                     278.00 m  = 2.90x the ceiling
#   free-span ceiling (iron stringer, WearNTear)   96.00 m  -> SPAN REFUSED
#   depth on the sail line                   9.51 .. 30.00 m, reaching the
#                                                 0.00 ocean-floor clamp
#   HardWood log pile reach                       ~20.00 m  -> PILE REFUSED
#
# AND THE TEST THAT CLOSES THE ARGUMENT, because "put the piers further out"
# is the obvious objection: drive BOTH piers to the absolute limit of pile
# reach -- heads at z 7806 (pile 19.93 m) and z 7962 (pile 19.67 m), i.e. 99.7 %
# and 98.4 % of the instrument -- and the remaining head-to-head span is still
# 156.00 m, which is 1.63x the ceiling. There is no pier length that turns this
# into a bridge.
#
# So the design uses SANE piers instead: piles capped at 8 m, which costs
# nothing because sail length is free and a 20 m pile stack is five
# wood_pole_log_4 driven into a bed nobody can inspect.
FERRY = {
    "route_id": "ferry-north-deepnorth",
    "why": "the ONLY way north of z +7740 on this seed. Both ceilings are "
           "exceeded in the same chord and the pier-at-the-limit test above "
           "rules out the bridge a reader will ask for.",
    "decision": {
        "shore_gap_m": 278.00,
        "shore_gap_over_ceiling": round(278.00 / MAX_SPAN_M, 2),
        "free_span_ceiling_m": MAX_SPAN_M,
        "span_verdict": "REFUSED",
        "sail_depth_min_m": 9.51, "sail_depth_max_m": 30.00,
        "ocean_floor_clamp_m": 30.00,
        "pile_reach_m": PILE_REACH_M,
        "pile_verdict": "REFUSED",
        "piers_at_absolute_pile_limit": {
            "south_head_z": 7806.0, "south_head_pile_m": 19.93,
            "north_head_z": 7962.0, "north_head_pile_m": 19.67,
            "residual_head_to_head_m": 156.00,
            "residual_over_ceiling": round(156.00 / MAX_SPAN_M, 2),
            "conclusion": "no pier length makes this a bridge",
        },
    },
    # THREE DISTINCT DISTANCES, named, because they were being used
    # interchangeably at the east-isle ferry and the sign quoted the wrong one.
    "distances": {
        "shore_gap_m": 278.00,
        "head_to_head_m": 244.00,
        "sail_m": 228.00,
        "which_goes_on_the_guidepost": "sail_m -- it is what a passenger travels",
    },
    "ends": {
        "ferry-terminal-north-south": {
            "shore_xz": [-844.0, 7740.0], "shore_h": 30.18, "biome": "Mistlands",
            "head_xz": [-844.0, 7754.0], "head_pile_m": 6.10,
            "pier_length_m": 14.0, "berth_xz": [-844.0, 7762.0],
            "berth_depth_m": 9.51, "deck_top_y": DECK_DATUM_Y,
            "seaward_bearing_deg": 0.0,
            "guidepost": "FERRY NORTH - to the Deep North terminal, 228 m "
                         "across open water.",
        },
        "ferry-terminal-north-deepnorth": {
            "shore_xz": [-844.0, 8018.0], "shore_h": 30.86, "biome": "DeepNorth",
            "head_xz": [-844.0, 7998.0], "head_pile_m": 7.84,
            "pier_length_m": 20.0, "berth_xz": [-844.0, 7990.0],
            "berth_depth_m": 10.14, "deck_top_y": DECK_DATUM_Y,
            "seaward_bearing_deg": 180.0,
            "guidepost": "FERRY SOUTH - to the north road terminus, 228 m "
                         "across open water.",
        },
    },
    # The invariant, asserted rather than observed: identical at both ends, so a
    # graded approach arriving at either has no step.  ferry-terminal-east and
    # -eastisle both measured 30.597 for exactly this reason.
    "deck_datum_shared": True,
    "landing": {
        "biome": "DeepNorth",
        "north_shore_xz": [-844.0, 8018.0],
        "component_z_from": 8017.5, "component_z_to_at_least": 8209.5,
        "component_area_m2_in_window": 94194,
        "what_it_means": "the Deep North inner edge is z +8000 and this landing "
                         "is at z +8018, i.e. 18 m INSIDE it. From the north "
                         "terminal a player walks in the Deep North.",
    },
    "both_guideposts_name_the_other_end": True,
    "refused": "a bridge, and a one-ended terminal. A missing far terminal is a "
               "missing road as well as a missing dock: S14-eastlobe-ferryeast "
               "was validated and deliberately not built because its terminal "
               "had zero pieces in the world.",
}


# ---------------------------------------------------------------------------
# why a zone will not generate, answered OFFLINE
# ---------------------------------------------------------------------------
#
# THE POINT OF THIS TOOL IS THAT IT TOUCHES NO WIRE.  `zones_generate`'s two
# failure paths need opposite remedies, the wrong one is irreversible, and the
# live census that distinguishes them costs main-thread time on a world
# somebody else may be writing.  Everything here is `[SEED]` data plus the
# ledger plus the save FILE, so it can be run while another agent holds the
# write token and it cannot be a census of a different world.
#
# `ZoneSystem::SpawnZone` (IL of the deployed assembly_valheim.dll, method at
# RVA 0xbc404) has exactly TWO `return false` paths, MEASURED by `KeepBuild`
# at ledger seq 743:
#
#   IL_0030-003b   !HeightmapBuilder.IsTerrainReady        -- a RACE
#   IL_003c-0067   m_locationInstances.TryGetValue(zoneID, out li)
#                  && !li.m_placed
#                  && !PokeCanSpawnLocation(li.m_location, true)
#
# and `PokeCanSpawnLocation` returns `LocationPrefabLoadData.IsLoaded` for the
# location's SoftReference prefab -- **a per-location-TYPE async asset load,
# not a per-instance property.**  That is the whole leverage of this tool:
# ONE SUCCESS ANYWHERE REFUTES THE TYPE EVERYWHERE.  So a zone whose
# registration is of a type that has demonstrably generated elsewhere cannot
# be failing on IL_003c, and the remaining path is the race -- which is
# answered by waiting, not by `zones_restore` and not by a re-route.
#
# The sudrberg precedent is the other outcome: zone (37,-52) held an unplaced
# `MountainCave02` whose type never reported `IsLoaded` across ~600
# consecutive frames in two ten-second brackets, and the repair was
# `zones_generate ... empty` then `zones_restore` (seq 745/746), at the
# DECLARED cost of that zone's ungenerated content forever.

# NO FROZEN DUMP PATH.  `LOC_DUMP_DEFAULT` used to be Pirate68's own content
# hash, so `zonediag` on any other seed diagnosed a zone of Ulfsland's.  The
# resolution is `poi.location_dump()`, keyed on the run's seed
# (`poi.LOCATION_DUMP_DEPENDS_ON`); an explicit `--locations` still wins.
# AND NO FROZEN SAVE PATH, for the same reason: `SAVE_DEFAULT` was Ulfsland's
# save, so `zonediag` validated the ledger of whatever world it ran on against
# Ulfsland's generated zones.  `_save_default()` is THIS run's world's save.
def _save_default() -> str:
    from accept import instruments as AI                   # noqa: PLC0415
    return AI.world_src_default(str(WORLD))


ZONE_M = 64.0


def zone_of(x: float, z: float) -> tuple[int, int]:
    """MEASURED convention: `ZoneSystem::GetZone` is
    `Mathf.FloorToInt(pos/64 + 0.5)`, so zone (0,0) is CENTRED on the origin
    and zone (zx,zz)'s centre is (zx*64, zz*64).  Stated here rather than
    re-derived so this file and `crossings/plan.py::zone_of` cannot drift."""
    return (math.floor(x / ZONE_M + 0.5), math.floor(z / ZONE_M + 0.5))


def generated_zones_from_save(save_dir_or_db2: str) -> dict:
    """`ZoneSystem.m_generatedZones`, read off the world save FILE.

    A NEW OFFLINE INSTRUMENT, and the reason it is trustworthy is that it was
    validated in BOTH directions before it was believed -- see the
    `validation` key it returns.  An instrument that gets the PRESENCES right
    and says nothing about the absences has not been tested against the
    hypothesis it is about to be used on.

    FORMAT, MEASURED on Ulfsland's `_main.319.db2` (174,320 B): a 16-byte
    header, then a single gzip member (there are 40 trailing bytes after it,
    so `gzip.decompress` -- which insists on consuming every member --
    raises; use `zlib.decompressobj(31)` and keep `unused_data`).  Inside the
    inflated 248,787 B stream the zone list is a run of `(int16 x, int16 y)`
    pairs.  The run is LOCATED rather than assumed at a fixed offset, because
    an offset that happens to be right once is not a format.

    THE FILE IS ONLY AS CURRENT AS THE LAST `save`.  That is a feature here
    and it is stated in the return value: a zone generated after the last
    save is CORRECTLY absent, and reading the file again after the next save
    is how the question "did those attempts land?" gets answered with no wire
    read at all.
    """
    import gzip  # noqa: F401  (documented above: NOT usable, see below)
    import zlib
    p = Path(save_dir_or_db2)
    if p.is_dir():
        newest = worldseed.newest_save(p, "db2")
        if newest is None:
            raise SystemExit(f"no _main.*.db2 in {p}")
        p = newest
    blob = p.read_bytes()
    i = blob.find(b"\x1f\x8b\x08")
    if i < 0:
        raise SystemExit(f"{p}: no gzip member found")
    raw = zlib.decompressobj(31).decompress(blob[i:])

    def run_len(off: int) -> int:
        k = 0
        while off + 4 * k + 4 <= len(raw):
            x, y = struct.unpack_from("<hh", raw, off + 4 * k)
            if not (-400 <= x <= 400 and -400 <= y <= 400):
                break
            k += 1
        return k

    best_off, best_n = -1, 0
    off = 0
    while off < len(raw):
        k = run_len(off)
        if k > best_n:
            best_off, best_n = off, k
        off += 4 * k + 1 if k else 1
    if best_n < 100:
        raise SystemExit(f"{p}: no plausible zone run (longest {best_n})")
    zones = {struct.unpack_from("<hh", raw, best_off + 4 * k)
             for k in range(best_n)}
    return {
        "save": str(p),
        "save_mtime": datetime.datetime.utcfromtimestamp(
            p.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gzip_at": i, "inflated_bytes": len(raw),
        "run_offset": best_off, "entries": best_n,
        "zones": zones,
    }


def zonediag(zx: int, zz: int, save: str = "", loc_dump: str = "") -> dict:
    """Diagnose `zones_generate` refusing zone (zx, zz), with no wire read."""
    cx, cz = zx * ZONE_M, zz * ZONE_M
    dump = json.loads(Path(loc_dump or poimod.location_dump()).read_text())
    locs = dump["locations"]
    by_zone: dict[tuple[int, int], list] = {}
    per_type: dict[str, int] = {}
    for L in locs:
        by_zone.setdefault(zone_of(L["x"], L["z"]), []).append(L)
        per_type[L["prefab"]] = per_type.get(L["prefab"], 0) + 1
    here = by_zone.get((zx, zz)) or []

    # THE LEDGER IS THE AUTHORITY FOR "THIS ZONE DEMONSTRABLY GENERATED",
    # and the strongest record of it is a `terrain_write` entry, because
    # `zone_generated_before: true` there was established by that entry's own
    # `objects_count id=_ZoneCtrl pos=<centre> max=1` probe -- a 1 m disc at
    # the zone centre, which `PlaceZoneCtrl` plants exactly at, so it is an
    # EXACT generated/not-generated test rather than a proximity guess.
    led = worldseed.ledger_path(str(WORLD))
    proven: dict[tuple[int, int], int] = {}
    for line in led.read_text().splitlines():
        if '"terrain_write"' not in line:
            continue
        rec = json.loads(line)
        if rec.get("op") != "terrain_write":
            continue
        for e in rec["params"].get("entries") or []:
            if e.get("zone_generated_before"):
                proven[tuple(e["zone"])] = rec["seq"]

    types_here = sorted({L["prefab"] for L in here})
    refutations = []
    for tp in types_here:
        same = [z for z, ls in by_zone.items()
                if any(L["prefab"] == tp for L in ls) and z in proven]
        refutations.append({
            "prefab": tp,
            "registrations_world_wide": per_type[tp],
            "zones_of_this_type_PROVEN_generated": sorted(
                [list(z) for z in same])[:16],
            "proven_count": len(same),
            "verdict": ("REFUTED: the type's SoftReference prefab loads on "
                        "this world, so IL_003c cannot be the cause"
                        if same else
                        "NOT REFUTED: no zone holding this type is proven "
                        "generated, so IL_003c remains possible"),
            "instrument": ("terrain_write entries whose zone_generated_before "
                           "is true, each established by its own "
                           "objects_count id=_ZoneCtrl max=1 probe at the "
                           "zone centre"),
        })

    gz = generated_zones_from_save(save or _save_default())
    present = len(set(proven) & gz["zones"])
    validation = {
        "ledger_written_zones": len(proven),
        "of_those_present_in_the_save_set": present,
        "missing": sorted([list(z) for z in (set(proven) - gz["zones"])]),
        "why_this_is_the_test": (
            "every zone the ledger has terrain-written was generated before "
            "it was written, so all of them MUST be in m_generatedZones. A "
            "reader that gets fewer is reading the wrong bytes."),
    }

    return {
        "zone": [zx, zz], "centre": [cx, cz],
        "method": "OFFLINE ONLY: the LocScan location dump ([SEED]), the "
                  "canonical ledger, and ZoneSystem.m_generatedZones read out "
                  "of the save file. No RCON, no census, no main-thread cost.",
        "spawnzone_false_paths": {
            "IL_0030": "!HeightmapBuilder.IsTerrainReady -- a RACE, cured by "
                       "waiting and re-issuing",
            "IL_003c": "an unplaced LocationInstance whose TYPE's "
                       "SoftReference never reports IsLoaded -- permanent, "
                       "cured only by `zones_generate empty` + "
                       "`zones_restore` at the cost of the zone's content",
        },
        "registrations_in_this_zone": [
            {"prefab": L["prefab"], "name": L["name"], "biome": L["biome"],
             "xz": [L["x"], L["z"]], "y": L["y"],
             "exteriorRadius": L["exteriorRadius"],
             "quantity": L["quantity"],
             "placed_flag_is_not_a_placement_record": L["placed"]}
            for L in here],
        "il_003c_refutation": refutations,
        "generated_set": {
            "in_generated_set_at_last_save": [zx, zz] in
            [list(z) for z in gz["zones"]],
            **{k: v for k, v in gz.items() if k != "zones"},
            "set_size": len(gz["zones"]),
            "validation": validation,
            "caveat": "the save file is only as current as the last `save` "
                      "record; a zone generated after it is CORRECTLY absent.",
        },
    }


def main() -> int:
    # Regeneration is a seed-derived itinerary, never the historical coordinate
    # tables retained below for read-only fixture diagnostics.
    if sys.argv[1:2] in (["network"], ["plan"]):
        import north_itinerary
        return north_itinerary.general_main(sys.argv[2:])
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["reach", "crossings", "placed",
                                   "report", "ferry", "zonediag"])
    ap.add_argument("--zone", default="",
                    help="zonediag: 'zx,zz' of the zone to diagnose")
    ap.add_argument("--save", default="",
                    help="zonediag: path to the world's `_main.N.db2`; "
                         "defaults to the live Ulfsland save")
    ap.add_argument("--segment")
    ap.add_argument("--segments")
    ap.add_argument("--field4", default="/tmp/trunknorth/fine4.bin,"
                                        "/tmp/trunknorth/fine4s.bin")
    ap.add_argument("--field16", default="/tmp/trunknorth/north16.bin")
    ap.add_argument("--field1", default="/tmp/trunknorth/north1m.bin,"
                                        "/tmp/trunknorth/n1b.bin")
    ap.add_argument("--out", default=str(HERE))
    worldseed.add_arguments(ap)
    args = ap.parse_args()
    out = Path(args.out)

    global SEED, WORLD
    SEED, WORLD = worldseed.apply(args)
    if args.op in ("reach", "crossings", "report", "ferry") and not worldseed.is_default():
        raise SystemExit("Historical north diagnostics describe the Pirate68 fixture only; "
                         "use north.py network for seed-derived regeneration.")

    def load_many(spec: str) -> dict:
        merged: dict = {}
        for part in spec.split(","):
            # Refuse a field sampled on a different seed -- see
            # field.require_seed; this is the guard that makes --seed safe.
            fieldmod.require_seed(part.strip(), SEED)
            merged.update(fieldmod.load(part.strip()))
        return merged

    if args.op == "zonediag":
        if not args.zone:
            raise SystemExit("zonediag needs --zone zx,zz")
        zx, zz = (int(v) for v in args.zone.replace(" ", "").split(","))
        rep = zonediag(zx, zz, save=args.save)
        print(json.dumps(rep, indent=2))
        dest = out / f"zonediag_{zx}_{zz}.json"
        dest.write_text(json.dumps(rep, indent=2) + "\n")
        print(f"-> {dest}")
        return 0

    if args.op == "reach":
        # mosaic() needs one dict; load_many merges the 4 m patches.
        merged = load_many(args.field4)
        tmp = out / "north_reach.json"
        # reach() re-loads by path, so hand it a merged temp view instead.
        global _PRELOADED
        _PRELOADED = merged
        orig = fieldmod.load
        fieldmod.load = lambda p: (_PRELOADED if p == args.field4
                                   else orig(p))  # noqa: E731
        try:
            reach(args.field4, args.field16, tmp)
        finally:
            fieldmod.load = orig
        return 0

    if args.op == "crossings":
        flds = load_many(args.field1)
        rep = {"x1": None, "ferry_chord": None, "islet": None}
        px, pz, ph, pb, land, ford, lab, n = ford_components(flds["nnorth1"])
        west = int(lab[int(round(2582 - pz[0])), int(round(-186 - px[0]))])
        north = int(lab[int(round(4150 - pz[0])), int(round(-316 - px[0]))])
        rep["x1"] = gap_between(flds["nnorth1"], lab, west, north)
        px3, pz3, ph3, pb3, l3, f3, lab3, n3 = ford_components(flds["nnorth3"])
        main_c = int(lab3[int(round(7731 - pz3[0])), int(round(-847 - px3[0]))])
        islet = int(lab3[int(round(7754 - pz3[0])), int(round(-1152 - px3[0]))])
        rep["islet"] = gap_between(flds["nnorth3"], lab3, main_c, islet)
        print(json.dumps(rep, indent=2))
        (out / "north_crossings.json").write_text(json.dumps(rep, indent=2) + "\n")
        return 0

    if args.op == "ferry":
        (out / "north_ferry.json").write_text(json.dumps(FERRY, indent=2) + "\n")
        d = FERRY["decision"]
        print(f"shore gap {d['shore_gap_m']:.2f} m = "
              f"{d['shore_gap_over_ceiling']}x the {d['free_span_ceiling_m']:g} m "
              f"free-span ceiling -> {d['span_verdict']}")
        print(f"sail depth {d['sail_depth_min_m']:.2f}..{d['sail_depth_max_m']:.2f} m "
              f"against {d['pile_reach_m']:g} m of pile reach -> "
              f"{d['pile_verdict']}")
        pl = d["piers_at_absolute_pile_limit"]
        print(f"piers driven to the pile limit ({pl['south_head_pile_m']} m / "
              f"{pl['north_head_pile_m']} m) still leave "
              f"{pl['residual_head_to_head_m']:.2f} m = "
              f"{pl['residual_over_ceiling']}x the ceiling: "
              f"{pl['conclusion']}")
        for name, e in FERRY["ends"].items():
            print(f"  {name:34s} shore {e['shore_xz']} -> head {e['head_xz']} "
                  f"pile {e['head_pile_m']:.2f} m, berth depth "
                  f"{e['berth_depth_m']:.2f} m, deck {e['deck_top_y']}")
        print(f"  sail {FERRY['distances']['sail_m']:.2f} m; landing "
              f"{FERRY['landing']['north_shore_xz']} in "
              f"{FERRY['landing']['biome']}, {FERRY['landing']['what_it_means']}")
        return 0

    if args.op == "report":
        merged = load_many(args.field1)
        orig = fieldmod.load
        fieldmod.load = lambda p: (merged if p == args.field1 else orig(p))  # noqa: E731
        try:
            report(args.field1, out)
        finally:
            fieldmod.load = orig
        return 0

    if args.op == "placed":
        segfile = Path(args.segments or (out / _north_name()))
        doc = yaml.safe_load(segfile.read_text())
        segs = [x for x in doc["segments"] if x["id"] == args.segment]
        if not segs:
            raise SystemExit(f"no segment {args.segment} in {segfile}")
        rep = placed_from_ledger(segs[0]["nodes"])
        dest = out / f"placed_{args.segment}.json"
        dest.write_text(json.dumps(rep, indent=2) + "\n")
        print(f"{args.segment}: {len(rep['objects'])} placed pieces within "
              f"{rep['reach_m']:g} m -> {dest}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
