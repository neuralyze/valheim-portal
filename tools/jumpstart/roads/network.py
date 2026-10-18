#!/usr/bin/env python3
"""Plan the Ulfsland road network: route, fit, cost in ZDOs, hand off crossings.

A ROAD IS TERRAIN, NOT PIECES.  `piece_pavedroad` has no persistent ZNetView and
zero road pieces exist in any fleet world save, so a spawned road piece leaves
nothing behind.  What persists is one `_TerrainCompiler` ZDO per zone carrying a
gzip'd `TCData` blob of per-sample height deltas and per-sample ground paint
(r dirt, g cultivated, b paved, a vegetation-still-standing) at 1 m over a 64 m
zone.  So a road is a levelled, paved ribbon of terrain.

THE ZDO COST IS PER ZONE, NOT PER METRE, AND THAT IS THE HEADLINE NUMBER.
MEASURED mechanism: `Heightmap::GetAndCreateTerrainCompiler` returns the FIRST
`_TerrainCompiler` it finds in a zone, so a zone holds exactly one and a second
is dead weight whose data is never applied (this is also why flatten.py deletes
before it spawns).  Therefore N metres of ribbon cost one ZDO per 64 m zone the
ribbon touches, NOT one per metre.  The "956 ZDOs per 957 m of paving" figure
from the earlier design was the PIECE-based road, which is the approach this
tool exists to replace; quoting it against a terrain ribbon overstates the cost
by roughly the zone length.

Usage:
    network.py plan                 # route everything, write segments/crossings
    network.py plan --pri 1         # just the spawn-island trunk
    network.py plan --only T1-...   # one segment
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

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

import field as fieldmod  # noqa: E402
import poi as poimod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402
import worldseed  # noqa: E402
import emitscope  # noqa: E402
from field import BIOME_NAMES, WATER_LEVEL  # noqa: E402
import precensus as censusmod  # noqa: E402
# THE COLLAPSE LIVES IN ONE PLACE.  `boardwalk` owns the corridor-area
# census - it derived the deck's own 3.000 m half-width off the solid and
# measured both rejected alternatives failing - and the PRODUCER uses the
# same instrument the consumer does rather than growing a second definition
# of "the same channel" that could disagree with it.
import boardwalk  # noqa: E402

# Seed and world are RESOLVED, not literal: `--seed`/`--world` beat `$VH_SEED`/
# `$VH_WORLD` beat the Ulfsland defaults.  `main()` rebinds both from the
# parsed arguments before anything reads them; the module-level binding is what
# an importer (`north.py` imports this module) sees.
SEED = worldseed.seed()
WORLD = worldseed.world()
FIELD_BIN = "/tmp/roads/h1m.bin"

# Ribbon widths, set by Crossings' MEASURED deck pitch (wood_floor is a
# 2.000 x 0.130 x 2.000 collider solid, so 2 m tiles): it builds a 3-tile 6 m
# deck on trunk crossings and a 2-tile 4 m deck on spurs.  A ribbon wider than
# the deck is a pinch the operator sees at every bridge.
from crossing_contract import WIDTH_M, BANK_LEVEL_M
# Crossings' MEASURED absolute span ceiling: 72 m free span at a 3x safety
# factor on an iron stringer under the deck, 96 m absolute, from its own
# implementation of WearNTear::UpdateSupport.  Beyond this a "bridge" is not a
# thing that can be built, so a route needing one is rejected rather than
# handed over.
MAX_BRIDGE_SPAN_M = 96.0
STATION_M = 2.0         # profile station spacing; also Crossings' snap
# The Ramer-Douglas-Peucker tolerance `build_segment` simplifies the A*
# staircase at.  With half the diagonal of a corridor cell it is how far a
# station can stand from every cell centre the search was allowed -- the
# chord slack each corridor mask is grown by (`poi.masks`, `pad_forbid_mask`)
# and the census confinement is shrunk by (precensus.CENSUS_CONFINE_DEPENDS_ON).
SIMPLIFY_TOL_M = 1.0


def chord_slack_m(cell_m: float) -> float:
    """How far a station can stand from the corridor cell centres its search
    walked: half a cell diagonal plus the simplify tolerance."""
    return cell_m / math.sqrt(2.0) + SIMPLIFY_TOL_M


def plain(o):
    """Coerce numpy scalars and arrays to YAML/JSON-representable Python.

    Needed because `yaml.safe_dump` raises RepresenterError on `numpy.float32`,
    and every height in this tool comes out of a float32 array.  Silent
    stringification would be worse than the exception, so this is explicit.
    """
    if isinstance(o, dict):
        return {k: plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [plain(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return [plain(v) for v in o.tolist()]
    return o


def straighten(pts, s, a: int, b: int):
    """Replace stations a..b with a straight line between their endpoints.

    `Crossings` cannot build a curve mid-span and asked for a constant bearing
    across the span plus 6 m each side.  Done on the STATION list, not the
    corridor nodes, so station count and spacing survive and the profile indices
    stay valid.
    """
    ax, az = pts[a]
    bx, bz = pts[b]
    span = s[b] - s[a]
    out = list(pts)
    for k in range(a + 1, b):
        f = (s[k] - s[a]) / span if span > 0 else 0.0
        out[k] = (ax + (bx - ax) * f, az + (bz - az) * f)
    return out


# ---------------------------------------------------------------------------
# THE BANKS ARE READ OFF THE LINE THAT IS WRITTEN -- CROSSING_BANKS_DEPENDS_ON
# ---------------------------------------------------------------------------
# A crossing record carries two lines.  `route.find_crossings` takes
# `bank_a`/`bank_b` from the polyline AS SEARCHED and snaps them to a 2 m
# world grid.  `build_segment` then STRAIGHTENS every crossing window
# (`straighten` replaces the run with its chord) and records
# `centreline_straight_from_to` from that chord.  Nothing re-read the banks,
# so the bank named one polyline and the line it has to lie on named
# another.  MEASURED on Fresh42 drive 3, roads/crossings.Fresh42.yaml, 12
# records: 6 of 12 put a bank further off their own centreline than the
# deck's 3.000 m half-width (W9-x03 4.98, S13-x01 4.88, T1-x02 4.44, T13-x01
# 3.39, T5-x01 3.33, W9-x04 3.25).  The builder stands every deck on the
# centreline, and it refused T1-x02 by `span_coverage` (29 of 49 declared
# metres covered), T5-x01 (13 of 15) and T13-x01 (8 of 9).
#
# A SECOND ROUTE TO THE SAME DEFECT IS OVERLAPPING WINDOWS.  Each window runs
# BANK_LEVEL_M past its free stations.  Two crossings whose runs were not
# merged (merging would have passed `max_merged_m`) can stand closer than two
# level runs.  Their windows were straightened one after the other, so the
# second chord MOVED the first window's last stations, and the first record's
# chord no longer passes through its own points.  MEASURED: W9-x01 (window
# 182..216) against W9-x02 (214..231) leaves the x01 polyline 1.38 m off its
# recorded chord; W9-x03 (1269..1315) against W9-x04 (1312..1337), 2.90 m.
#
# So:
#   the_straightened_line_the_ribbon_writes   each bank is the station at
#                                             its end of the FREE run (where
#                                             the ribbon's bench stops and
#                                             the deck begins), read off the
#                                             points AFTER `straighten`, with
#                                             no grid snap;
#   windows_that_share_no_station_but_an_end  overlapping windows are split
#                                             between their free runs, and
#                                             become ONE window where those
#                                             runs overlap;
#   a_bank_with_no_bench_beyond_it            a free run that reaches the
#                                             segment's own end has no bench
#                                             on that side, and the record
#                                             says so (`terminal_banks`).
CROSSING_BANKS_DEPENDS_ON = ("the_straightened_line_the_ribbon_writes",
                             "windows_that_share_no_station_but_an_end",
                             "a_bank_with_no_bench_beyond_it")
# Swapped out by the selftest neuters: "written" | "detected" (shipped: each
# bank keeps the searched polyline's grid-snapped water edge) | "overlapping"
# (shipped: windows straightened one over another) | "unflagged" (shipped: a
# bank at the segment's own end reads as an ordinary bank).
_CROSSING_BANKS_MODE = "written"


# BANK_CEILING_DEPENDS_ON: A DETECTED CROSSING'S LEVEL RUN TAKES THE
# WALKED-RAY CEILING BEFORE ITS ONE HEIGHT IS CHOSEN.
#
# `build_segment` pins both banks of a detected crossing to ONE height H
# (lo == hi over the window), and the walked-ray ceiling ran AFTER that pin,
# skipping every station whose bounds were already closed -- so a ceiling the
# writer asked for on a bank was dropped without a count.  And before that,
# `writer_water` recorded a drop only on a DRY station, so a refused ray
# whose foot fell on the deck's own stations asked no ceiling at all.
# MEASURED, Fresh42 drive 6 step 4 and the re-plan on the fixed tree:
# W9-vestvik-lhwest refused (rc 7), one caused rim step 0.9003 m at
# (-770,601), 6.35 m beside the deck end of crossing x01 -- foot on stations
# 212/213, both bridged, `_drops` empty, the ceiling round never ran, the
# level run held at the median-chosen 33.304 m (legal [32.5, 38.617]); the
# three W9 crossings then went unbuilt.
# THE RULE, two terms:
#   the_drop_at_the_decks_own_stations  a refused ray whose foot is on two
#       deck stations asks its drop of them (a foot with a dry station keeps
#       asking the dry one, as before);
#   the_ceiling_on_the_window_before_its_one_height  the ceiling narrows the
#       window's legal interval, never below the floor already in force
#       (deck clearance over water, the clamp), exactly as it narrows any
#       other station; H is then chosen inside it, so both banks and the
#       deck move together.
# A declared abutment's height stays Crossings'.
# NEUTER `_BANK_CEILING_MODE = "after"`: the shipped behaviour, both terms.
# Proven by `selftest_crossing_banks`.
# BANK_BED_DEPENDS_ON: see the comment in `build_segment`.  NEUTER
# `_BANK_BED_MODE = "free"`: H may sit below its banks.
BANK_BED_DEPENDS_ON = ("the_deck_at_or_above_its_banks",)
_BANK_BED_MODE = "floor"    # floor | free
BANK_CEILING_DEPENDS_ON = ("the_drop_at_the_decks_own_stations",
                           "the_ceiling_on_the_window_before_its_one_height")
_BANK_CEILING_MODE = "interval"     # interval | after (the shipped order)


# ABUTMENT_BENCH_DEPENDS_ON: FRESHWATER FREEBOARD ALONE DOES NOT MAKE A DECLARED
# ABUTMENT'S LEVEL RUN A CROSSING.
#
# `route.footprint_wet` calls a station wet when the lowest ground under its
# levelled bench stands within `ROAD_FREEBOARD_M` (1.0 m) of the plane, and a
# declared abutment is a bench at the crossing's own deck datum (30.6 m: 0.6 m
# over the plane, by Crossings' declaration) on a shore.  So every leg that
# starts at one reads wet at its first stations ON FREEBOARD ALONE, the
# detector joins them to the one shoulder sample that dips under the plane, and
# the whole run becomes a terminal crossing: `build_segment`'s pin of the
# abutment datum, which the profile solve skips at a free (deck) station, is
# lost and the leg climbs from the node on a deck-free ramp.  MEASURED on the
# Ulfsland regen, W2-brgs2-wmid and W8-brgs2-vestvik: stations 0-3 at brg-s2-w
# are bridged (a 6.0 m "crossing x01", `terminal_banks: bank_a`; footprint floor
# 29.84 / 30.2 / 30.5 / 31.33 m), deck H 32.817, profile 34.1 m AT THE NODE
# against the declared 30.6 m and the committed plan's 30.6 m for the same four
# stations (dry, a 1.9 m cut, then the 25 % climb); the writer's walked batter
# then leaves 18 `water_fill` faces of up to 2.04 m at rim [-347,250..253].
# THE RULE: inside a declared abutment's level run a footprint sample that
# stands ON OR ABOVE the plane is not wet for the detector -- the abutment pin
# (`lo = hi = level_y`) holds those stations at the declared datum, as it does
# at S1's and W5's.  A sample UNDER the plane stays wet: a bench cannot be
# filled over water (`ribbon.water_sign`), so the shoulder cell that dips under
# is still a deck at the node (W2: one station), and a run whose shore is
# mostly under water (S12 at brg-s1-e: 30 drained samples when the whole run
# is treated as dry) stays refused by `writer_water` with its number.
# NEUTER `_ABUTMENT_BENCH_MODE = "detected"`: the run is read like any other
# ground.  Proven by `selftest_abutment_bench`.
ABUTMENT_BENCH_DEPENDS_ON = ("the_declared_abutment_level_run",
                             "a_footprint_sample_on_or_above_the_plane")
_ABUTMENT_BENCH_MODE = "pinned"    # pinned | detected


# ABUTMENT_TRIMMED_END_DEPENDS_ON: A PIN THE CLAMP CANNOT HOLD, AT AN END THE
# TERMINAL TRIM HAS MOVED OFF ITS NODE, IS NOT AN ABUTMENT.
#
# `build_segment` pins the last `BANK_LEVEL_M` of a line at a declared node's
# `level_y`, and it did so at the END OF THE LINE IT WAS HANDED -- trimmed or
# not.  MEASURED, Ulfsland regen, T2-temple-brgs1 (probe: the trim candidate's
# own record): the terminal trim cut 88 stations (176 m, the whole 78.63 m
# crossing and the cliff above it) and the candidate's `abutments` still read
# `brg-s1-e level_y 30.6, ribbon_end_xz [-240.0, -92.79]` -- the hilltop,
# `worst_cut_fill_m` 22.57, `within_clamp` False, profile tail 38.6 -> 37.5 m
# over ground at 52 m, `writer_water` REFUSED 251 steps, worst 15.32 m at
# [-235, -89].  The trim was declined for the water score of a road dug 15 m
# into a hill to meet a deck 176 m away, and the crossing stayed: 78.63 m of
# deck at y 52.757 over ground at 27.7 m that no deck can land.
# THE RULE: where the trim moved an end off its node and the pin cannot be
# held there (the bench is more than `route.CUT_FILL_MAX` from the datum), the
# end is an ordinary road end and the pin is released -- recorded as
# `abutments_released`, never silently dropped.  A pin the clamp CAN hold at a
# trimmed end (W5, W10, S12 of the same plan: 2.98 / 2.98 / 3.79 m) is
# untouched, and so is every untrimmed end, infeasible or not: that conflict
# stays reported, because there the deck IS the abutment.
# NEUTER `_ABUTMENT_TRIMMED_END_MODE = "pinned"`: the shipped pin at whatever
# end the line has.  Proven by `selftest_abutment_released`.
ABUTMENT_TRIMMED_END_DEPENDS_ON = ("the_terminal_trim_per_end",
                                   "the_bench_against_the_clamp_at_that_end")
_ABUTMENT_TRIMMED_END_MODE = "released"    # released | pinned


def abutment_bench_dry(t_foot, abut_windows):
    """`t_foot` with, inside each declared abutment's level run, every sample
    that stands on or above the plane lifted out of the freeboard band
    (ABUTMENT_BENCH_DEPENDS_ON); a sample under the plane is left as read."""
    out = np.array(t_foot, dtype=np.float64)
    if _ABUTMENT_BENCH_MODE != "pinned":
        return out
    clear = WATER_LEVEL + routemod.ROAD_FREEBOARD_M
    for _which, _node, _level_y, lo_i, hi_i in abut_windows:
        run = out[lo_i:hi_i + 1]
        out[lo_i:hi_i + 1] = np.where((run >= WATER_LEVEL) & (run <= clear),
                                      clear + 1e-6, run)
    return out


def drop_stations(k: int, bridged: list[bool]) -> list[int]:
    """The stations a walked-ray drop at the foot run `k -> k+1` is asked of
    (BANK_CEILING_DEPENDS_ON): the dry ones, or the deck's own when both
    are bridged -- `bank_level_height` takes it into the level run."""
    dry = [q for q in (k, k + 1) if not bridged[q]]
    if dry or _BANK_CEILING_MODE == "after":
        return dry
    return [k, k + 1]


def bank_bed_floor(t, dry: list[int], wet: list[int], h_lo: float,
                   h_hi: float) -> float:
    """The lowest H a detected crossing may take (BANK_BED_DEPENDS_ON): at or
    above the dry ground at the two banks -- the dry stations either side of
    the water -- but never above the legal interval's top."""
    if _BANK_BED_MODE != "floor" or not dry or not wet:
        return h_lo
    a = [k for k in dry if k < min(wet)]
    b = [k for k in dry if k > max(wet)]
    bed = [float(t[k]) for k in (a[-1:] + b[:1])]
    return max(h_lo, min(max(bed), h_hi)) if bed else h_lo


# BANK_SPAN_BED_DEPENDS_ON: THE DECK IS CHOSEN AGAINST THE GROUND THE GATE
# READS ON THE DECK'S OWN METRES, NOT AGAINST THE ROUTING FIELD AT ITS BANKS.
#
# `bank_bed_floor` (BANK_BED_DEPENDS_ON) keeps H at or above the dry ground at
# the two stations BESIDE the free run, read from `t`, the routing field's
# bilinear height -- a field sampled on HALF-integers.  The acceptance gate
# (accept/sections/roads.py `crossing_premise`) reads something else: the
# engine's Heightmap at the integer lattice vertex nearest each 1 m sample of
# the straight line between the record's own `bank_a` and `bank_b` -- the
# free run's two END stations, which `bank_bed_floor` never looks at -- and
# refuses a deck whose bed stands more than 0.30 m above it on any of them
# ("IS NOT A CROSSING").  Two quantities, one name: the same shape as every
# field-vs-lattice defect here (`_writer_water_once`: "interpolating it back
# to integers smooths precisely the one-metre shore steps").
# MEASURED, Ulfsland regen, the writer lattice (`writer_patches`, nearest
# vertex) against the gate's own cached ground over the 1 m samples of the
# two refused crossings: 0.000 m difference at every sample that the road's
# own bench did not move (46 + 22 of 70), so the lattice IS the gate's
# quantity; and the routing field is NOT:
#   T9-wttown-dockshore-x01  deck 32.752; bank_b 32.61 m by the field, 33.234
#       m on the lattice (field - lattice up to 0.83 m on the bank's slope,
#       the bank sample 0.482 m over the deck) -> 3 of 22 metres over, worst
#       +0.506 m;
#   S4-meadhall-southeast-x01  deck 33.574 (its legal floor); the free run's
#       last-but-one station stands on a crest the field reads 33.722 and the
#       lattice 33.998 -> +0.424 m.
# In both the deck sat at its floor, `bank_bed_floor` was satisfied (the dry
# stations beyond the banks are lower than the crest), and the plan declared a
# deck its own bed rises above.  THE RULE:
#   the_gates_own_samples_of_the_span    the 1 m samples of the line between
#       the record's two banks (the free run's end stations, rounded as the
#       record rounds them), the gate's `crossing_spans` construction;
#   the_writers_integer_lattice          each read at the integer vertex(es)
#       the engine answers with (both on a half-lattice line, where its
#       reading can flip), from the writer's own patches -- the quantity the
#       gate reads -- never from the half-grid field;
#   a_margin_under_the_burial_limit      H is not chosen more than
#       CROSSING_BED_BURIAL_M (half of the gate's 0.30) under the highest of
#       them: an exact placement on a limit is the half-lattice ambiguity
#       again, and the margin is what survives the gate's own measurement.
# The floor is clipped at the legal interval's top (the clamp) like
# `bank_bed_floor`; a bed that still stands above is RECORDED on the crossing
# (`span_bed`, with the metres over) for Crossings, never forced, and a
# crossing the rule does not move carries no new key (Fresh42 byte-identical).
# NEUTER `_SPAN_BED_MODE = "banks"`: the shipped floor, the two beside-run
# stations of the routing field.  Proven by `selftest_crossing_banks`.
BANK_SPAN_BED_DEPENDS_ON = ("the_gates_own_samples_of_the_span",
                            "the_writers_integer_lattice",
                            "a_margin_under_the_burial_limit")
_SPAN_BED_MODE = "lattice"    # lattice | banks (the shipped floor)
# accept/sections/roads.py SAMPLE_M and half of its CROSSING_BURIAL_MAX_M.
CROSSING_SAMPLE_M = 1.0
CROSSING_BED_BURIAL_M = 0.15


def span_samples(a, b) -> list[tuple[float, float]]:
    """The gate's own samples of a crossing: `crossing_spans` cuts the line
    from `bank_a` to `bank_b` into `max(1, round(span / 1 m))` equal steps."""
    span = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(round(span / CROSSING_SAMPLE_M)))
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            for k in range(n + 1)]


def lattice_vertices(x: float, z: float) -> list[tuple[int, int]]:
    """The integer vertices the engine's Heightmap answers a point with: the
    nearest on each axis, and BOTH where the point sits on a half-lattice
    line (`accept/instruments.py::ground`: the reading there can flip by the
    local vertex difference)."""
    def axis(v: float) -> list[int]:
        lo = math.floor(v)
        if abs(v - lo - 0.5) < 1e-6:
            return [lo, lo + 1]
        return [int(math.floor(v + 0.5))]
    return [(i, j) for i in axis(x) for j in axis(z)]


def span_bed_floor(a, b, ground) -> dict:
    """The lowest H a detected crossing may take against the ground the gate
    reads on its own span (BANK_SPAN_BED_DEPENDS_ON): the highest lattice bed
    on the samples of the line `a -> b`, less CROSSING_BED_BURIAL_M.
    `ground(vx, vz)` is the writer's height at an integer vertex, None when
    unread.  `floor_y` is None when no sample was read."""
    best, at, read, unread = None, None, 0, 0
    pts = span_samples(a, b)
    for x, z in pts:
        hs = [g for g in (ground(i, j) for i, j in lattice_vertices(x, z))
              if g is not None]
        if not hs:
            unread += 1
            continue
        read += 1
        h = max(float(g) for g in hs)
        if best is None or h > best:
            best, at = h, (round(x, 3), round(z, 3))
    return {"bed_max_y": None if best is None else round(best, 3),
            "bed_max_at": at, "samples": len(pts), "unread": unread,
            "floor_y": (None if best is None
                        else round(best - CROSSING_BED_BURIAL_M, 3))}


def bank_level_height(ref: list[float], h_lo: float, h_hi: float,
                      caps: list[float]) -> dict:
    """The one height of a detected crossing's level run.

    `ref` is the ground the run is fitted to (median-chosen), `[h_lo, h_hi]`
    the legal interval, `caps` the walked-ray ceilings asked on stations of
    this window.  Returns `{H, h_hi, single, lowered, under_floor}`; `H` is
    None when the interval is empty (no single height exists)."""
    lowered = under_floor = 0
    if caps and _BANK_CEILING_MODE == "interval" and h_lo <= h_hi:
        cap = min(caps)
        if cap < h_lo:
            under_floor = 1
        new_hi = max(min(h_hi, cap), h_lo)
        if new_hi < h_hi:
            h_hi, lowered = new_hi, 1
    single = h_lo <= h_hi
    H = min(max(float(np.median(ref)), h_lo), h_hi) if single else None
    return {"H": H, "h_hi": h_hi, "single": single,
            "lowered": lowered, "under_floor": under_floor}


def crossing_windows(pts, s, detected: list[dict]):
    """The free stations and one window per detected crossing, as
    `(record, ia, ib, wa, wb)`: the free run `ia..ib` and BANK_LEVEL_M of
    level run beyond it on each side."""
    n = len(pts)
    free = np.zeros(n, dtype=bool)
    windows = []
    for c in detected:
        # Preserve the detector's actual run. Its banks are display/grid
        # coordinates: snapping may merge them, and a route can visit the
        # same coordinate more than once. Neither identifies a station.
        first, last = c["stations"]
        ia, ib = max(first-1, 0), min(last+1, n-1)
        wa = max(0, int(np.searchsorted(s, s[ia] - BANK_LEVEL_M)))
        wb = min(n - 1, int(np.searchsorted(s, s[ib] + BANK_LEVEL_M)))
        free[ia:ib + 1] = True
        windows.append((c, ia, ib, wa, wb))
    return free, windows


def disjoint_windows(wins: list[list[int]]) -> list[list[int]]:
    """Straightening ranges for windows given as `[lo, hi, core_lo, core_hi]`,
    one per window in input order.  Any two of them share no station except
    an end, or they are the SAME range (CROSSING_BANKS_DEPENDS_ON).

    The core is what a window must keep straight: a crossing's free run, or
    the whole of a declared-abutment window.  When windows overlap, they are
    split at the middle of the overlap, clamped so each keeps its core.  When
    two cores overlap by more than an end station, the windows become one."""
    if _CROSSING_BANKS_MODE == "overlapping" or len(wins) < 2:
        return [[w[0], w[1]] for w in wins]
    order = sorted(range(len(wins)), key=lambda k: (wins[k][2], wins[k][3]))
    groups: list[list] = []
    for k in order:
        lo, hi, clo, chi = wins[k]
        if groups and clo < groups[-1][3]:
            g = groups[-1]
            g[0], g[1], g[3] = min(g[0], lo), max(g[1], hi), max(g[3], chi)
            g[4].append(k)
        else:
            groups.append([lo, hi, clo, chi, [k]])
    for prev, cur in zip(groups, groups[1:]):
        if cur[0] < prev[1]:
            m = min(max((cur[0] + prev[1]) // 2, prev[3]), cur[2])
            prev[1], cur[0] = m, m
    out: list[list[int]] = [[0, 0]] * len(wins)
    for g in groups:
        for k in g[4]:
            out[k] = [g[0], g[1]]
    return out


def straighten_windows(pts, s, windows: list[tuple], abut_windows: list[tuple]):
    """Straighten every crossing window, then every declared-abutment window,
    over the ranges `disjoint_windows` keeps apart.  Returns `(pts, windows)`,
    with each crossing's `(wa, wb)` narrowed to the part of its window that
    lies on its own chord.  An abutment window keeps its own range for the
    height it pins; only the stretch that is straightened can change."""
    ranges = disjoint_windows(
        [[wa, wb, ia, ib] for _c, ia, ib, wa, wb in windows]
        + [[lo, hi, lo, hi] for *_w, lo, hi in abut_windows])
    nc = len(windows)
    for lo_i, hi_i in ranges[:nc]:
        pts = straighten(pts, s, lo_i, hi_i)
    for lo_i, hi_i in ranges[nc:]:
        if hi_i > lo_i:
            pts = straighten(pts, s, lo_i, hi_i)
    return pts, [(c, ia, ib, max(wa, r[0]), min(wb, r[1]))
                 for (c, ia, ib, wa, wb), r in zip(windows, ranges)]


def declare_banks(c: dict, pts, t, tw_lo, ia: int, ib: int, wa: int,
                  wb: int) -> None:
    """Write the crossing's centreline and its two banks from the line the
    ribbon writes (CROSSING_BANKS_DEPENDS_ON).  `pts`, `t` and `tw_lo` are
    the POST-straighten stations, their centreline and their footprint-floor
    generated heights.  The banks are the free run's end stations, so both
    lie on `centreline_straight_from_to` by construction.  The detector's own
    coordinate is kept as `detected_xz`, so the move can be audited."""
    ax, az = pts[wa]
    bx, bz = pts[wb]
    c["centreline_straight_from_to"] = [[round(ax, 2), round(az, 2)],
                                        [round(bx, 2), round(bz, 2)]]
    n = len(pts)
    if _CROSSING_BANKS_MODE != "detected":
        for end, k in (("bank_a", ia), ("bank_b", ib)):
            b = c[end]
            b["detected_xz"] = list(b["xz"])
            b["xz"] = [round(float(pts[k][0]), 2), round(float(pts[k][1]), 2)]
            b["ground_y"] = round(float(t[k]), 2)
            b["footprint_min_y"] = round(float(tw_lo[k]), 2)
            b["station"] = int(k)
        (pa, qa), (pb, qb) = c["bank_a"]["xz"], c["bank_b"]["xz"]
        c["span_m"] = round(math.hypot(pb - pa, qb - qa), 2)
    if _CROSSING_BANKS_MODE == "unflagged":
        return
    ends = [(end, k) for end, k, last in (("bank_a", ia, 0), ("bank_b", ib, n - 1))
            if k == last]
    if ends:
        c["terminal_banks"] = [end for end, _k in ends]
        c["terminal_note"] = "; ".join(
            f"{end} is station {k}, the segment's own "
            f"{'first' if k == 0 else 'last'}: the free run reaches the end "
            f"of the road, so no bench lies beyond this bank and a deck has "
            f"nothing to land on there (footprint floor "
            f"{float(tw_lo[k]):.2f} m against the {WATER_LEVEL:g} m plane). "
            f"Owner: this segment's terminus (terminal trim / pad arrival), "
            f"not the deck" for end, k in ends)


def transverse_window(fld, pts, width_m: float, lat_step_m: float = 0.5
                      ) -> tuple[np.ndarray, np.ndarray,
                                 list[tuple[int, float, float, float]]]:
    """Per-station MIN and MAX generated height across the LEVEL road surface.

    WHY THIS EXISTS, and it is the fourth instance of one defect: a quantity
    certified over one object and consumed as though it were about another.
    `fit_profile` was being handed `t`, the generated height ON THE
    CENTRELINE, and its `+/-cut_fill_max` box was therefore a statement about
    a LINE.  `TerrainComp::ApplyToHeightmap` clamps PER SAMPLE against that
    sample's own generated height, and the ribbon writes a LEVEL bench out to
    `route.footprint_half_m(width)` -- 4.0 m either side on a 6 m trunk.  On a
    cross slope the outermost shoulder sample therefore carries about half the
    transverse relief ON TOP OF whatever cut the profile already owes.

    MEASURED on N4-mid2-terminus, 2026-09-18, by `ribbon.py`'s rasteriser
    after the router had passed the leg: 112 carriageway samples past the
    +/-8 m clamp in the best of three routing variants (239 and 120 in the
    other two), of which 71 were in the carriageway band and **every single
    one sat at lat 3.99-4.12 m** -- the outermost shoulder sample line, not
    one of them on the paved +/-3.0 m.  First breach at arc 24.0 m of
    3,038.4 m and the last at 2,802.8 m, so there was no prefix worth
    terminating at either.  The leg's own routing record read
    `stations_over_clamp: 0`, truthfully, about stations.

    THE CONSTRAINT THIS MAKES EXPRESSIBLE.  For one profile value `y` to be
    writable across a station's whole footprint:

        y <= min(generated over footprint) + clamp
        y >= max(generated over footprint) - clamp

    which is a per-station [lo, hi] pair -- exactly the shape `fit_profile`
    already solves exactly by interval propagation.  Nothing new is modelled;
    the existing solver is finally given the right bounds.  It also makes the
    impossible case a PROOF rather than a surprise: where the footprint's own
    relief exceeds `2 * clamp`, lo > hi and the propagation reports an empty
    interval, which is the honest statement "no profile at any gradient can
    carry a level bench here".

    Sampled at `lat_step_m = 0.5` m -- HALF the 1 m lattice the ribbon is
    written on, so no written sample can hide between two probes.  The
    endpoints of the sweep are INCLUSIVE of `+/-e` because the sample at
    exactly `e` IS written: `ribbon.EDGE_EPS_M` exists because one float ULP
    at `lat = 4.000000000000001` once dropped that sample out of the
    carriageway and left a 1 m wide, 1.76 m deep trench under a batter.

    The normal is taken from the CENTRAL difference of the neighbouring
    stations, which is the tangent the ribbon itself rasterises against, and
    falls back to the one-sided difference at the two ends.
    """
    e = routemod.footprint_half_m(width_m)
    n = int(math.floor(e / lat_step_m + 1e-9))
    lats = np.concatenate([np.arange(-n, n + 1) * lat_step_m, [-e, e]])
    lats = np.unique(lats)
    P = np.asarray(pts, dtype=np.float64)
    tang = np.empty_like(P)
    tang[1:-1] = P[2:] - P[:-2]
    tang[0] = P[1] - P[0]
    tang[-1] = P[-1] - P[-2]
    norm = np.hypot(tang[:, 0], tang[:, 1])
    norm[norm < 1e-9] = 1.0
    # left normal of (dx, dz) is (dz, -dx)
    nx = tang[:, 1] / norm
    nz = -tang[:, 0] / norm
    lo = np.empty(len(P))
    hi = np.empty(len(P))
    # UNREAD IS NOT CLEAR, AND `Field.bilinear` CANNOT SAY SO: it CLAMPS its
    # sample indices to the patch, so a probe that walks off the window comes
    # back as the edge height instead of as a refusal.  On a footprint sweep
    # that is silent and load-bearing -- the outermost shoulder is the sample
    # this whole function exists for, and it is the first one to leave the
    # patch.  `field.py`'s own request manifest is the DENOMINATOR here: two
    # Fresh42 fields on this host carry the SAME seed name and the SAME seed
    # hash and cover 26 windows and 9, and `require_seed` accepts both.
    # Counted and returned rather than raised, so the caller can name the
    # station and the offset rather than the whole segment.
    off = []
    for i in range(len(P)):
        xs = P[i, 0] + nx[i] * lats
        zs = P[i, 1] + nz[i] * lats
        hs = np.array([fld.bilinear(float(x), float(z))
                       for x, z in zip(xs, zs)], dtype=np.float64)
        for x, z, lat in zip(xs, zs, lats):
            if not fld.contains(float(x), float(z)):
                off.append((i, float(lat), float(x), float(z)))
        lo[i] = hs.min()
        hi[i] = hs.max()
    return lo, hi, off


# ---------------------------------------------------------------------------
# THE PAD EDGE, ENFORCED RATHER THAN REPORTED.
#
# `spec.py` has stated the rule in words since it was written -- "my ribbon
# terminates at the pad edge and does not pave inside it, because inside it is
# its earthwork and a road levelling ground under a building is the defect
# class this build exists to avoid" -- and NOTHING APPLIED IT.  MEASURED:
# `pad_radius_m` never reached the search endpoints, and `endpoint_standoff_m`
# (below, in the returned segment) was COMPUTED AND REPORTED with no test
# against anything.  The ledger's own records show the result: T1 terminated
# 0.7 m from `portalhub`'s centre and T8 2.0 m from `stenvik`'s.
#
# WHAT THE REQUIRED STAND-OFF IS, AND WHY IT IS NOT `pad_radius_m` EVERYWHERE.
# `plan.py`'s own docstring says that number is "taken from the district or the
# pad plus its own margin" -- two quantities behind one name:
#   * for a watchtower, lighthouse, treehouse or castle it IS the pad, derived
#     by the solver as `pad_m * sqrt(2)/2 + 2.0`, so it is enforced directly;
#   * for a TOWN it is 100.0 m of DISTRICT, and reading that as a footprint is
#     a measured regression: it cost T4 293.9 m of unwritten centreline once
#     already.  A town's pads are its BUILDINGS, so the stand-off comes from
#     the pad rectangles the ledger actually holds (`writer._pad_claims`, the
#     same source the write-time pad guard judges against) and the district
#     figure is recorded as NOT a pad rather than silently used.
# A pad that exists only in the future -- a settlement not yet built when the
# road is planned -- cannot be avoided by the router at all, and that half of
# the rule is enforced on the SETTLEMENT side: `settlements/build.py` now reads
# the road before it drops a body.  The two halves are the two orderings the
# ledger measured: 42 of 48 obstructing bodies stood BEFORE their road, 6 came
# after.
# ---------------------------------------------------------------------------
_PAD_CLAIMS: list[dict] | None = None

# ---------------------------------------------------------------------------
# PAD_CLAIMS_DEPENDS_ON: EVERY PAD THIS BUILD STANDS A BODY ON -- the pads the
# ledger already holds AND the pads the same build lays after the road is
# planned: the solved installations (`worlds/<World>/*/placements.yaml`,
# stamped by `terraform/flatten.py` at build step 2 as `flatten.pad_extent`,
# apron 0) and the settlement units (`plan.<seed>.json`, stamped by
# `settlements/build.py` at step 5 as `unit_records` -> `written_pad`).  Both
# are fixed by pipeline stages 7 and 8, which run BEFORE either route stage.
#
# THE SHIPPED KEY WAS THE LEDGER ALONE -- the pads already WRITTEN -- a subset
# of the pads the road will meet.  Every route is planned at stages 9 and 11,
# and on a from-scratch world the ledger then holds zero pads, so the whole
# pad-edge rule below (terminal trim, interior refusal) was inert on every
# fresh drive.  MEASURED on Fresh42's stage-9 route of 2026-09-24 22:09
# (18 segments): 561 station hits inside a planned pad inflated by the level
# half-width, on 14 of 18 segments -- T1 ending 14 stations deep in the
# portal hub it serves, S4 crossing four stenvik buildings, T7 four more.
# The previous drive shows the same shape: "T1 arrived 0.8 m from the portal
# hub with no pad to stop at".
#
# A planned pad a written one already covers (same `site_id`) is NOT added:
# the written footprint is the truth once it exists.  The neuter, both
# directions, is `selftest`: `_PAD_SOURCE = "written"` restores the shipped
# key.  The pads are ALSO rasterised into the A* forbid mask
# (`pad_forbid_mask`), so a route bends round a building instead of being
# refused for crossing it.
_PAD_SOURCE = "written+planned"   # neuters: "written", "unmerged"


def merge_pad_claims(written: list[dict], planned: list[dict]) -> list[dict]:
    """Written pads, then every planned pad whose site has none written."""
    if _PAD_SOURCE == "written":
        return list(written)
    have = set() if _PAD_SOURCE == "unmerged" else \
        {str(q["site_id"]) for q in written}
    # A pad written to the ledger is the same pad it was when planned, so it
    # keeps the PLANNED pad's role (PAD_BATTER_DEPENDS_ON keys on it): the
    # ledger calls every pad a writer lays a `site_pad`, installations
    # included, and a re-plan after step 2 must not forget which were laid
    # first.
    role = {str(q["site_id"]): q.get("role") for q in planned}
    return ([{**q, "role": role.get(str(q["site_id"])) or q.get("role")}
             for q in written]
            + [q for q in planned if str(q["site_id"]) not in have])


def claims_holding(claims: list[dict], x: float, z: float,
                   level_half_m: float) -> list[str]:
    """The site ids whose pad (grown by the level half-width) holds (x, z)."""
    return [str(q["site_id"]) for q in claims
            if abs(x - float(q["x"])) <= float(q["hw"]) + level_half_m
            and abs(z - float(q["z"])) <= float(q["hd"]) + level_half_m]


def pad_claims() -> list[dict]:
    """Every pad rectangle a road must respect: the ledger's, one per site,
    plus the pads this build will lay (PAD_CLAIMS_DEPENDS_ON).

    The written half is ASKED OF `writer._pad_claims` rather than re-derived,
    for the reason the write-time guard exists: a pad's WRITTEN footprint is
    not its nominal radius, and a keep-out computed from a circle was MEASURED
    to miss a clobber that lay outside it.  An absent ledger contributes no
    written pads -- a fresh world has none -- and the planned half then
    carries every pad the build is about to lay.
    """
    global _PAD_CLAIMS
    if _PAD_CLAIMS is not None:
        return _PAD_CLAIMS
    written: list[dict] = []
    run = JUMPSTART / "ledger" / "runs" / str(WORLD) / "ledger.jsonl"
    if run.exists():
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        try:
            import writer as writermod  # noqa: PLC0415
            recs = [json.loads(l) for l in run.read_text().splitlines()
                    if l.strip()]
            written = writermod._pad_claims(recs)
        except Exception as exc:                      # noqa: BLE001
            print(f"  pad claims unavailable from {run}: {exc}")
            written = []
    # ONE READER OF THE PADS THE BUILD WILL LAY, shared with the writer's
    # keep-out (`ribbon.planned_pad_claims`), so router and writer cannot
    # hold two opinions about where a foundation will be.
    import ribbon as ribbonmod                             # noqa: PLC0415
    planned = ribbonmod.planned_pad_claims(str(WORLD), str(SEED))
    _PAD_CLAIMS = merge_pad_claims(written, planned)
    print(f"  pad claims: {len(written)} written (ledger) + "
          f"{len(_PAD_CLAIMS) - len(written)} planned (stages 7/8) "
          f"[PAD_CLAIMS_DEPENDS_ON, source={_PAD_SOURCE}]")
    return _PAD_CLAIMS


# PAD_BATTER_DEPENDS_ON: A ROAD'S BATTER STAYS OFF THE CLAIMED RING OF A PAD
# LAID BEFORE THE ROADS.  The mask below keeps the CENTRELINE a level
# half-width and a chord slack off a pad; the batter spreads past that, up to
# a cut's height over the slide grade, and an installation pad (laid at step
# 2) has already claimed a ring round its edge.  A margin on EVERY pad was
# measured and lost S4, T6 and W9 (12 legs, 5 crossings): the site pads they
# pass are laid after the roads and claim nothing yet.  MEASURED on Fresh42 drive 9,
# real writer, step 4: S5-temple-southcape and S11-southcape-lhsouth both
# refused 3 steps (1.3009 m / 1.2944 m) at [-249,-383], the north-west corner
# of sandbox-portal-hub, `rim ground owned by sandbox-portal-hub#680`, with
# the collar off and on alike.  So the mask grows by the batter margin too.
# NEUTER `_PAD_BATTER_MODE = "centreline"`: the shipped mask.  Proven by
# `selftest_plan_refusal`.
PAD_BATTER_DEPENDS_ON = ("the_batter_margin_past_the_level_footprint",)
PAD_BATTER_MARGIN_M = 6.0
_PAD_BATTER_MODE = "margin"    # margin | centreline


def pad_forbid_mask(claims: list[dict], x0: float, z0: float, cell_m: float,
                    m: int, level_half_m: float) -> np.ndarray:
    """Every corridor cell whose CENTRE lies within a pad rectangle grown by
    the level half-width plus the same chord slack `poi.masks` uses: A*
    returns cell centres and the line is simplified at 1 m, so a centreline
    can sit `cell_m/sqrt(2) + 1` inside a cell whose centre was clear."""
    base = level_half_m + cell_m / math.sqrt(2.0) + 1.0
    mask = np.zeros((m, m), dtype=bool)
    for q in claims:
        # A pad laid BEFORE the roads has claimed its ring already; one laid
        # after them has not, and shapes its own surroundings when it is.
        grow = base + (PAD_BATTER_MARGIN_M
                       if _PAD_BATTER_MODE == "margin"
                       and q.get("role") == "installation_pad" else 0.0)
        hw, hd = float(q["hw"]) + grow, float(q["hd"]) + grow
        j0 = max(0, int(math.ceil((float(q["x"]) - hw - x0) / cell_m)))
        j1 = min(m - 1, int(math.floor((float(q["x"]) + hw - x0) / cell_m)))
        i0 = max(0, int(math.ceil((float(q["z"]) - hd - z0) / cell_m)))
        i1 = min(m - 1, int(math.floor((float(q["z"]) + hd - z0) / cell_m)))
        if j0 <= j1 and i0 <= i1:
            mask[i0:i1 + 1, j0:j1 + 1] = True
    return mask


def pad_keepout(node_id: str, level_half_m: float) -> dict:
    """What the ribbon must stay outside at this node, and where it came from.

    Returns `rects` (x, z, hw, hd -- already inflated by the LEVEL half-width,
    because the earthwork writes level ground that far either side of the
    centreline), `disc_m` (a centreline radius, likewise inflated) and the
    provenance of whichever one binds.
    """
    node = specmod.NODES.get(node_id) or {}
    declared = node.get("pad_radius_m")
    kind = node.get("kind")
    nx, nz = node.get("xz", (0.0, 0.0))
    sid = node_id
    rects = []
    why = []
    for q in pad_claims():
        cid = str(q["site_id"])
        hw = float(q["hw"]) + level_half_m
        hd = float(q["hd"]) + level_half_m
        # TWO WAYS A PAD BELONGS TO THIS NODE, and the second is the one that
        # matters.
        #
        # BY NAME: pad claims are per BUILDING (`stenvik-hall-1`), site nodes
        # are per SITE (`stenvik`), and the solver's own id may carry hyphens
        # (`wt-spawn` for `wtspawn`), so both spellings are matched prefix-wise
        # and a town's buildings attach to the town node.
        #
        # BY CONTAINMENT: the node's own point standing INSIDE a pad rectangle
        # makes that pad this node's terminal pad whatever either is called.
        # MEASURED, and name matching alone would have missed it: `portalhub`
        # is a hand-written junction node with no `pad_radius_m` and no site
        # id, it sits inside the `portal-hall` pad (centre (-280.0, 216.0),
        # 37.4 x 37.4, datum 37.10), and T1 was written with 27 of its
        # stations -- 54 m of carriageway -- levelling ground inside that pad,
        # arriving 0.7 m from its centre.  A rule keyed on names cannot see
        # that; a rule keyed on geometry cannot miss it.
        flat = cid.replace("-", "")
        named = flat == sid or flat.startswith(sid) or cid.startswith(sid + "-")
        contains = abs(nx - float(q["x"])) <= hw and abs(nz - float(q["z"])) <= hd
        if named or contains:
            rects.append((float(q["x"]), float(q["z"]), hw, hd))
            why.append(f"{cid}{'' if not contains else ' (contains the node)'}")
    if rects:
        return {"rects": rects, "disc_m": 0.0,
                "required_source": f"{len(rects)} pad rectangle(s) from the "
                                   f"ledger (writer._pad_claims), inflated by "
                                   f"the {level_half_m:g} m level half-width: "
                                   + ", ".join(why[:6]),
                "declared_pad_radius_m": declared, "kind": kind}
    if declared is None:
        return {"rects": [], "disc_m": 0.0,
                "required_source": "not a settlement node: no pad to stop at",
                "declared_pad_radius_m": None, "kind": kind}
    if kind == "town":
        return {"rects": [], "disc_m": 0.0,
                "required_source":
                    f"NOT ENFORCED AS A PAD: {declared:g} m is this town's "
                    f"DISTRICT radius (plan.py writes 100.0 for a town), and "
                    f"reading a district as a footprint cost T4 293.9 m of "
                    f"unwritten centreline. The town's pads are its "
                    f"buildings; none is in the ledger yet, so the road is "
                    f"free to arrive and settlements/build.py is what keeps a "
                    f"body off it.",
                "declared_pad_radius_m": declared, "kind": kind}
    return {"rects": [], "disc_m": float(declared) + level_half_m,
            "required_source":
                f"sites.yaml pad_radius_m {declared:g} m -- the SOLVER's pad "
                f"figure (pad_m*sqrt(2)/2 + 2.0) for a {kind}, plus the "
                f"{level_half_m:g} m level half-width",
            "declared_pad_radius_m": declared, "kind": kind}


def _inside_pad(ko: dict, x: float, z: float, cx: float, cz: float) -> bool:
    for rx, rz, hw, hd in ko["rects"]:
        if abs(x - rx) <= hw and abs(z - rz) <= hd:
            return True
    return ko["disc_m"] > 0.0 and math.hypot(x - cx, z - cz) < ko["disc_m"]


def enforce_pad_edge(pts, edge: dict, level_half_m: float, *,
                     searched: bool = True) -> dict:
    """TRIM the line back to the pad edge, and refuse if trimming cannot do it.

    Trimming, not nudging: the two ends are where a road is allowed to stop, so
    a station inside a terminal pad is removed and the arrival recorded.  A
    station inside a pad in the MIDDLE of a run is a different fact -- the line
    crosses somebody's foundation -- and it is refused rather than trimmed,
    because deleting it would split the road in two.

    `searched=False` MAKES THIS REPORT-ONLY, and the distinction is the one
    `build_segment`'s own docstring already draws about `fixed_pts`: a line
    handed in is the object the rasteriser cleared and the blobs were computed
    against, so "re-fit this proven line" must not be answered with "route it
    again" -- nor with "refuse it for a pad it has already been paving for
    days".  MEASURED over the 39 built centrelines against the ledger's 58 pad
    rectangles: 9 lines terminate inside a pad (T1 27 stations, T10 31, T11 30,
    S8 22, S7 9, W10 8, S1/W3/T12 6 each) and 12 cross one mid-run (worst: T9,
    39 stations through wt-town's pad).  Every one of those is ALREADY ON THE
    GROUND.  Trimming or refusing them at re-fit time would block the repair
    passes that are the only way they get better, while changing nothing about
    the ground, so a re-fit records the conflict and a NEW route is bound by
    it.
    """
    out = {"trimmed": {}, "would_trim": {}, "required": {}, "achieved_m": {},
           "interior_violations": [], "enforced": bool(searched),
           "enforcement": ("TRIM AND REFUSE: this line was searched" if searched
                           else "REPORT ONLY: this line was handed in as "
                                "fixed_pts, i.e. it is a proven alignment "
                                "being re-fitted, and its ground is already "
                                "written")}
    pts = list(pts)
    for which, node_id in (("a", edge["a"]), ("b", edge["b"])):
        node = specmod.NODES.get(node_id) or {}
        cx, cz = node.get("xz", (0.0, 0.0))
        ko = pad_keepout(node_id, level_half_m)
        out["required"][node_id] = {
            "rects": len(ko["rects"]), "disc_m": round(ko["disc_m"], 2),
            "source": ko["required_source"],
            "declared_pad_radius_m": ko["declared_pad_radius_m"]}
        n = 0
        while len(pts) - n > 2:
            k = n if which == "a" else len(pts) - 1 - n
            if not _inside_pad(ko, pts[k][0], pts[k][1], cx, cz):
                break
            n += 1
        # The COUNT is recorded either way -- how far inside a pad a proven
        # line starts is a measurement worth having -- but only a SEARCHED
        # line is actually cut.
        if n and searched:
            pts = pts[n:] if which == "a" else pts[:len(pts) - n]
        out["trimmed"][node_id] = n
        out["would_trim"][node_id] = n
        end = pts[0] if which == "a" else pts[-1]
        out["achieved_m"][node_id] = round(
            math.hypot(end[0] - cx, end[1] - cz), 2)
        if searched and _inside_pad(ko, end[0], end[1], cx, cz):
            raise ValueError(
                f"pad edge unreachable at {node_id}: after trimming {n} "
                f"station(s) the terminal station {[round(v, 1) for v in end]} "
                f"is still inside the pad keep-out ({out['required'][node_id]}"
                f"). Re-solve the pad or re-site the node; a ribbon that "
                f"levels ground under a building is the defect class this "
                f"build exists to avoid.")
        if not searched:
            out["trimmed"][node_id] = 0
    # THE MIDDLE OF THE RUN, checked against EVERY site's pads rather than just
    # the two terminals: a road passing a third settlement has no business
    # paving its foundations either, and this is the only place that question
    # is asked before the write.
    ends = {edge["a"], edge["b"]}
    for q in pad_claims():
        rx, rz = float(q["x"]), float(q["z"])
        hw, hd = float(q["hw"]) + level_half_m, float(q["hd"]) + level_half_m
        hits = [k for k, (x, z) in enumerate(pts)
                if abs(x - rx) <= hw and abs(z - rz) <= hd]
        if hits:
            out["interior_violations"].append({
                "pad": q["site_id"], "name": q.get("name"),
                "xz": [round(rx, 1), round(rz, 1)],
                "half_extents_m": [round(hw, 2), round(hd, 2)],
                "stations": len(hits), "first_station": hits[0],
                "terminal_of_this_edge": bool(ends & {str(q["site_id"])}),
            })
    out["pts"] = pts
    return out


# ---------------------------------------------------------------------------
# ARRIVAL_DEPENDS_ON: A ROAD ARRIVES ON THE PAD IT SERVES.
#
# THE SEARCH CANNOT END THERE, BY CONSTRUCTION.  `pad_forbid_mask` grows every
# pad by the level half-width plus the chord slack (`cell/sqrt(2) + 1`, 3.83 m
# on the 4 m corridor), and `Corridor.nearest_allowed` snaps a node that stands
# inside it to the nearest cell OUTSIDE.  So every leg to a pad starts or ends
# 7-10 m from the rectangle, `enforce_pad_edge` trims nothing, and the metres
# between are paved by nobody: the ribbon's cap reaches `footprint_half_m` past
# the terminus and the batter rays are cast across the line, not along it.
# Nothing asks whether the road's datum reaches the pad's either --
# `junction.find_conflicts` reads a pad only within NEAR_MISS_M of a
# carriageway, and the pad the road is FOR is never that close.
#
# MEASURED, Fresh42 drive 3 (plan 2026-09-25 22:26, gate on save 05eb7c6c05de):
#   * stathub: T4, T6, S2, T7, S7 and S13 all end at (83.9, 361.2), 8.2 m
#     west of `complete-station-hub` (x >= 92.1, datum 77.67) at 79.43-79.49.
#     The caps end at x = 88; 89-91 is generated ground (78.97, 78.61,
#     78.58); the pad's own cut face is 0.907 m at (91,362)->(92,362).  The
#     gate refused it on 5 roads (terminus_step 1B).
#   * portalhub: T1 ends at (-184.1, -410.8), 8.0 m east of
#     `sandbox-portal-hub` at 34.438 against its 34.42 -- the datums AGREE --
#     and still a 1.045 m riser: the cap ends at -188, the ground dips to
#     33.375 at -191 and the pad's fill face stands at -192.
#   * wttown / wtsouth, PLANNED pads: T8 ends 10.0 m short of wt-town at
#     50.01 against 56.0, S8 8.0 m short of wt-south at 68.83 against 72.48;
#     step 4 refused both at the rim (7 steps 1.2759 m, 16 steps 1.5141 m).
#
# THE RULE: from the searched terminus, a straight spur toward the node's own
# point, ending `ARRIVAL_EPS_M` outside the pad grown by the level half-width
# -- where the cap's level ground meets the pad's edge -- and stopped short
# at any cell the search itself may not enter for a reason other than the
# pads (water, a POI keep-out, unread census ground, this leg's own water
# stand-off, another site's pad).  The pad is then a junction participant
# (`junction.PAD_GAP_DEPENDS_ON`) and the joint solve holds the terminus to
# its datum (`route.JUNCTION_DATUM_DEPENDS_ON`'s `pad_datums`).
#   the_pad_holding_the_node   the terminal pad is the one whose grown
#       rectangle holds the node's own point (`terminal_pads`), the same
#       containment `pad_keepout` reads.  A town's building pads, matched by
#       NAME there, are not destinations: a road arrives at the district.
#   the_level_footprint_at_its_edge   the spur stops where the cap meets the
#       pad, not at the pad's centre and not at the searched cell.
# NEUTER `_ARRIVAL_MODE = "off"`, the shipped rule (the line ends where the
# search could start), proven by `selftest_arrival`.
# ---------------------------------------------------------------------------
ARRIVAL_DEPENDS_ON = ("the_pad_holding_the_node",
                      "the_level_footprint_at_its_edge")
_ARRIVAL_MODE = "spur"       # spur | off (the shipped: no spur)
# Outside the grown rectangle by this much, because `enforce_pad_edge` trims
# on `<=`; the cap then stops this far short of the pad, inside one lattice
# edge and inside `junction.NEAR_MISS_M` of it.
ARRIVAL_EPS_M = 0.05
# The clearance of the spur is asked at this pitch: finer than the 4 m
# corridor cell, so a spur cannot step over a forbidden cell's corner.
ARRIVAL_PROBE_M = 0.5


def terminal_pads(node_id: str, level_half_m: float,
                  claims: list[dict] | None = None) -> list[dict]:
    """The rectangles holding this node (PAD_CLAIMS_DEPENDS_ON)."""
    node = specmod.NODES.get(node_id) or {}
    if "xz" not in node:
        return []
    nx, nz = map(float, node["xz"])
    return [q for q in (pad_claims() if claims is None else claims)
            if abs(nx - float(q["x"])) <= float(q["hw"]) + level_half_m
            and abs(nz - float(q["z"])) <= float(q["hd"]) + level_half_m]


def _ray_into_rect(ex: float, ez: float, ux: float, uz: float, rx: float,
                   rz: float, hw: float, hd: float) -> float | None:
    """How far along the unit ray from (ex, ez) it first enters the rectangle
    centred (rx, rz) with half-extents (hw, hd): 0 if it starts inside, None
    if it never does."""
    t0, t1 = 0.0, math.inf
    for e, u, c, h in ((ex, ux, rx, hw), (ez, uz, rz, hd)):
        if abs(u) < 1e-12:
            if abs(e - c) > h:
                return None
            continue
        a, b = (c - h - e) / u, (c + h - e) / u
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0


def arrival_spur(end_xz, node_xz, pads: list[dict], level_half_m: float,
                 clear=None, station_m: float = STATION_M) -> dict:
    """The stations that carry a terminus from where the search could start
    to the edge of the pad it serves (ARRIVAL_DEPENDS_ON), in order away from
    the terminus.  `clear(x, z)` returns None where a spur may pass and the
    reason where it may not; the spur stops at the last clear probe."""
    ex, ez = float(end_xz[0]), float(end_xz[1])
    rec: dict = {"mode": _ARRIVAL_MODE,
                 "pads": [str(q["site_id"]) for q in pads],
                 "stations": [], "gap_before_m": None, "gap_after_m": None,
                 "blocked_by": None}
    if not pads:
        rec["why"] = "no pad holds this node: nothing to arrive on"
        return rec
    dx, dz = float(node_xz[0]) - ex, float(node_xz[1]) - ez
    L = math.hypot(dx, dz)
    if L < 1e-9:
        rec["why"] = "the terminus stands on the node"
        return rec
    ux, uz = dx / L, dz / L
    hits = [t for t in (_ray_into_rect(ex, ez, ux, uz, float(q["x"]),
                                       float(q["z"]),
                                       float(q["hw"]) + level_half_m,
                                       float(q["hd"]) + level_half_m)
                        for q in pads) if t is not None]
    if not hits:
        rec["why"] = "the line from the terminus to the node meets no pad"
        return rec
    want = min(hits) - ARRIVAL_EPS_M
    rec["gap_before_m"] = round(min(hits), 2)
    rec["gap_after_m"] = round(min(hits), 2)
    if _ARRIVAL_MODE == "off":
        rec["why"] = "NEUTER: the line ends where the search could start"
        return rec
    if want <= ARRIVAL_EPS_M:
        rec["why"] = "the terminus already stands at the pad's edge"
        return rec
    reach = want
    if clear is not None:
        probes = [ARRIVAL_PROBE_M * k
                  for k in range(1, int(math.floor(want / ARRIVAL_PROBE_M))
                                 + 1)] + [want]
        prev = 0.0
        for d in probes:
            why = clear(ex + ux * d, ez + uz * d)
            if why:
                reach = prev
                rec["blocked_by"] = {"at_m": round(d, 2),
                                     "xz": [round(ex + ux * d, 2),
                                            round(ez + uz * d, 2)],
                                     "why": why}
                break
            prev = d
    ds = [station_m * k for k in range(1, int(math.floor(reach / station_m))
                                       + 1)]
    # The last station lands ON the reach; a remainder under half a pitch
    # moves the last whole station there rather than adding a sliver.
    if ds and reach - ds[-1] < station_m / 2.0:
        ds[-1] = reach
    elif reach > 0.1:
        ds.append(reach)
    rec["stations"] = [(ex + ux * d, ez + uz * d) for d in ds]
    rec["gap_after_m"] = round(min(hits) - (ds[-1] if ds else 0.0), 2)
    rec["why"] = ("arrived: the cap meets the pad's edge"
                  if rec["blocked_by"] is None else
                  f"stopped {rec['gap_after_m']} m short: "
                  f"{rec['blocked_by']['why']}")
    return rec


def _spur_clear(corr, others: list[dict], level_half_m: float,
                extra_forbid: np.ndarray | None = None):
    """`clear(x, z)` for `arrival_spur`: the search's own masks less the pads
    (`corr.arrival_forbid`), water, this leg's water stand-off and every
    OTHER site's pad grown by the level half-width."""
    def clear(x, z):
        for q in others:
            if (abs(x - float(q["x"])) <= float(q["hw"]) + level_half_m
                    and abs(z - float(q["z"])) <= float(q["hd"])
                    + level_half_m):
                return f"another site's pad ({q['site_id']})"
        try:
            i, j = corr.cell_of(x, z)
        except ValueError:
            return "outside the corridor grid"
        if corr.wet[i, j]:
            return "water"
        if extra_forbid is not None and extra_forbid[i, j]:
            return "this leg's water stand-off"
        f = getattr(corr, "arrival_forbid", None)
        if f is not None and f[i, j]:
            return "a POI keep-out or unread census ground"
        return None
    return clear


def arrive_on_pads(pts, edge: dict, level_half_m: float, corr,
                   extra_forbid: np.ndarray | None = None) -> dict:
    """`arrival_spur` at both ends of a searched line, against the search's
    own masks less the pads (`corr.arrival_forbid`, built beside
    `corr.forbid` in `main`), this leg's water stand-off and every OTHER
    site's pad grown by the level half-width."""
    pts = list(pts)
    claims = pad_claims()
    out: dict = {"depends_on": list(ARRIVAL_DEPENDS_ON), "ends": {}}
    for which, node_id in (("a", edge["a"]), ("b", edge["b"])):
        mine = terminal_pads(node_id, level_half_m, claims)
        ids = {id(q) for q in mine}
        others = [q for q in claims if id(q) not in ids]
        clear = _spur_clear(corr, others, level_half_m, extra_forbid)
        end = pts[0] if which == "a" else pts[-1]
        node = specmod.NODES.get(node_id) or {}
        r = arrival_spur(end, node.get("xz", end), mine, level_half_m, clear)
        add = [(float(x), float(z)) for x, z in r.pop("stations")]
        r["added_stations"] = len(add)
        if add:
            pts = (list(reversed(add)) + pts) if which == "a" else pts + add
        out["ends"][node_id] = r
    out["pts"] = pts
    return out


# ---------------------------------------------------------------------------
# ARRIVAL_REACH_DEPENDS_ON: A LEG TO A PAD STARTS WHERE THE PAD'S DATUM CAN BE
# MET FROM.
#
# The arrival spur (ARRIVAL_DEPENDS_ON) carries a terminus onto the pad's
# edge, and the joint solve then holds the terminus to the pad's datum
# (`route.JUNCTION_DATUM_DEPENDS_ON`, `pad_datums`).  But the search starts
# where `Corridor.nearest_allowed` puts it: the nearest cell outside the
# pad's mask, whatever ground that cell stands on.  If that ground is so far
# above or below the datum that no profile inside the clamp box can reach the
# datum over the spur, the pad reading cannot be satisfied and the joint
# solve refuses the leg.  MEASURED, Pirate68 provisional re-plan 2026-09-26
# (/tmp/rw2/pirate_final5.log): the nearest cell to stathub (661.5, 1088.5)
# is (640, 1090), on the flank of a knoll (generated 40.15; 40.32 at T4
# station 754), 7.47 m of spur from complete-station-hub (datum 31.85,
# x >= 651.45).  T4's clamp box holds station 754 at >= 35.238, and the pad
# reading 8.7 m on holds it at <= 34.794 at the 0.25 ceiling, so T4 was
# refused.  Every other stathub leg ended at the same knoll top.  With the
# joint solve's conflicts attributed (route.JUNCTION_CONFLICT_DEPENDS_ON),
# T4 and T6 were refused against the pad and S2 and S7 against S13's empty
# start on the knoll (32.831 over 32.621).  In the first ring round the pad,
# the east and south faces are water.  (664, 1110) on the north face and
# (640, 1082) on the west face meet the datum even at the trunk's 0.08.
#
# THE RULE: if the nearest cell's spur arrives at a pad whose datum no
# profile can meet from that cell, the search starts at the cell of the first
# ring outside the pad's mask (spur no longer than the chord slack plus one
# cell diagonal) NEAREST THE CELL IT REPLACES whose spur meets the datum:
# the spur's own stations, with their level-footprint clamp boxes, close at
# the ceiling rung to a last station within the pad reading's allowance of
# the datum.  The smallest move keeps the face of the pad, and the line, the
# router was already on.  MEASURED on the same plan, T4 alone: the start
# nearest the NODE that meets the datum at 0.08 is (664, 1110), and from
# there T4 routed 5,017.1 m round the east lobe; the nearest meeting it at
# 0.08 from the replaced cell, (640, 1082), JOINED but its water remedy
# fell from 4 steps to 26.  The smallest move, (640, 1086), JOINED with the
# pad and kept the 4 steps and the 1,436 m line.
# If the nearest cell can meet its datum, or its spur does not reach the
# pad, the start does not move.  If no ring cell can meet the datum, the
# start stays at the nearest cell and the joint solve names the leg.
#   the_pad_datum_the_spur_arrives_at   the datum of the pad the spur from
#       that cell meets first: the one the joint solve will read.
#   the_level_footprint_box_along_the_spur   `transverse_window` over the
#       spur, `+/- route.CUT_FILL_MAX`: the box `build_segment` fits in.
#   the_ceiling_rung   `route.GRADE_CEILING`, the top of every leg's ladder
#       and the steepest profile the joint solve may climb to.
#   the_smallest_move_off_the_nearest_cell   ring cells are taken nearest
#       the cell they replace first, then nearest the node.
# NEUTER `_ARRIVAL_REACH_MODE = "nearest"`: the shipped snap.  Proven by
# `selftest_arrival_reach`.
# ---------------------------------------------------------------------------
ARRIVAL_REACH_DEPENDS_ON = ("the_pad_datum_the_spur_arrives_at",
                            "the_level_footprint_box_along_the_spur",
                            "the_ceiling_rung",
                            "the_smallest_move_off_the_nearest_cell")
_ARRIVAL_REACH_MODE = "datum"    # datum | nearest (the shipped snap)
_PAD_DATUMS: dict[str, float] | None = None
# node id -> the start `arrival_start` moved off the nearest cell, with why.
_ARRIVAL_STARTS: dict[str, dict] = {}


def pad_datums() -> dict[str, float]:
    """site id -> the datum a road arriving on that pad is held to: the
    joint solve's own participants (`pad_participants`)."""
    global _PAD_DATUMS
    if _PAD_DATUMS is None:
        _PAD_DATUMS = {str(p["id"]): float(p["datum_y"])
                       for p in pad_participants()[0]}
    return _PAD_DATUMS


def arrival_reach(start_xz, node_xz, pads: list[dict], datums: dict,
                  fld, width_m: float, clear=None,
                  grade: float = routemod.GRADE_CEILING) -> dict:
    """Can a leg that starts at `start_xz` meet the datum of the pad its
    arrival spur reaches (ARRIVAL_REACH_DEPENDS_ON)?  `verdict` is MEETS,
    OUT OF REACH, NOT READ (the spur stops short of the pad, so no datum is
    read) or UNREAD (the footprint leaves the field)."""
    J = routemod._junction()
    level = routemod.footprint_half_m(width_m)
    spur = arrival_spur(start_xz, node_xz, pads, level, clear)
    rec: dict = {"verdict": "NOT READ", "start": [round(float(start_xz[0]), 2),
                                                  round(float(start_xz[1]), 2)],
                 "gap_before_m": spur["gap_before_m"],
                 "gap_after_m": spur["gap_after_m"]}
    if spur["gap_after_m"] is None or spur["gap_after_m"] > J.NEAR_MISS_M:
        return rec
    ex, ez = float(start_xz[0]), float(start_xz[1])
    dx, dz = float(node_xz[0]) - ex, float(node_xz[1]) - ez
    L = math.hypot(dx, dz)
    ux, uz = (dx / L, dz / L) if L > 1e-9 else (1.0, 0.0)
    first = min(((t, q) for q in pads for t in (_ray_into_rect(
        ex, ez, ux, uz, float(q["x"]), float(q["z"]), float(q["hw"]) + level,
        float(q["hd"]) + level),) if t is not None),
        key=lambda tq: tq[0], default=(None, None))[1]
    if first is None or str(first["site_id"]) not in datums:
        return rec
    datum = float(datums[str(first["site_id"])])
    P = [(ex, ez)] + [(float(x), float(z)) for x, z in spur["stations"]]
    probe = P if len(P) > 1 else P + [(ex + 0.5 * ux, ez + 0.5 * uz)]
    tw_lo, tw_hi, off = transverse_window(fld, probe, width_m)
    if off:
        rec["verdict"] = "UNREAD"
        return rec
    CF = routemod.CUT_FILL_MAX
    lo = np.maximum(tw_hi[:len(P)] - CF,
                    WATER_LEVEL + routemod.ROAD_FREEBOARD_M)
    hi = tw_lo[:len(P)] + CF
    a = J.slide_allow(spur["gap_after_m"]) - routemod.JUNCTION_DATUM_MARGIN_M
    box0 = [round(float(lo[0]), 3), round(float(hi[0]), 3)]
    lo[-1] = max(lo[-1], datum - a)
    hi[-1] = min(hi[-1], datum + a)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(
        np.diff([p[0] for p in P]), np.diff([p[1] for p in P])))])
    none = np.zeros(0, dtype=np.int64)
    _L, _U, ok = routemod._close_bounds(lo, hi, grade * np.diff(s), none,
                                        none, np.zeros(0))
    rec.update({"verdict": "MEETS" if ok else "OUT OF REACH",
                "pad": str(first["site_id"]), "datum_y": datum,
                "run_m": round(float(s[-1]), 2),
                "box_at_start": box0,
                "reach_at_start": [round(datum - a - grade * float(s[-1]), 3),
                                   round(datum + a + grade * float(s[-1]), 3)],
                "grade": grade})
    return rec


def arrival_start(node_id: str, xz, corr, width_m: float,
                  extra_forbid: np.ndarray | None = None, *,
                  claims: list[dict] | None = None,
                  datums: dict | None = None,
                  search_m: float = 200.0) -> tuple[tuple[int, int], dict | None]:
    """The cell a search at this node starts from (ARRIVAL_REACH_DEPENDS_ON)
    and the arrival record that chose it: `Corridor.nearest_allowed` unless
    its spur reaches a pad whose datum it cannot meet at the ceiling rung,
    then the cell of the first ring outside the pad's mask, nearest the one it
    replaces, whose spur meets it at the ceiling rung.  Raises what
    `nearest_allowed` raises."""
    base = corr.nearest_allowed(float(xz[0]), float(xz[1]), search_m)
    if _ARRIVAL_REACH_MODE != "datum":
        return base, None
    level = routemod.footprint_half_m(width_m)
    claims = pad_claims() if claims is None else claims
    mine = [q for q in claims
            if abs(xz[0] - q["x"]) <= q["hw"] + level
            and abs(xz[1] - q["z"]) <= q["hd"] + level]
    if not mine:
        return base, None
    datums = pad_datums() if datums is None else datums
    ids = {id(q) for q in mine}
    clear = _spur_clear(corr, [q for q in claims if id(q) not in ids], level,
                        extra_forbid)
    rec0 = arrival_reach(corr.cell_world(*base), xz, mine, datums, corr.fld,
                         width_m, clear)
    if rec0["verdict"] != "OUT OF REACH":
        return base, rec0
    ring = chord_slack_m(corr.cell_m) + corr.cell_m * math.sqrt(2.0)
    i0, j0 = corr.cell_of(float(xz[0]), float(xz[1]))
    r = int(search_m / corr.cell_m)
    cands = []
    for i in range(max(0, i0 - r), min(corr.m, i0 + r + 1)):
        for j in range(max(0, j0 - r), min(corr.m, j0 + r + 1)):
            if corr.wet[i, j] or corr.blocked(i, j):
                continue
            x, z = corr.cell_world(i, j)
            dx, dz = float(xz[0]) - x, float(xz[1]) - z
            L = math.hypot(dx, dz)
            if L < 1e-9:
                continue
            gap = min((t for q in mine for t in (_ray_into_rect(
                x, z, dx / L, dz / L, float(q["x"]), float(q["z"]),
                float(q["hw"]) + level, float(q["hd"]) + level),)
                if t is not None), default=None)
            if gap is not None and gap <= ring:
                cands.append(((i - base[0]) ** 2 + (j - base[1]) ** 2,
                              (i - i0) ** 2 + (j - j0) ** 2, i, j))
    for _db, _dn, i, j in sorted(cands):
        rec = arrival_reach(corr.cell_world(i, j), xz, mine, datums,
                            corr.fld, width_m, clear)
        if rec["verdict"] == "MEETS":
            break
    else:
        rec0["no_ring_cell_meets"] = len(cands)
        _ARRIVAL_STARTS[f"{node_id}@{width_m:g}"] = rec0
        return base, rec0
    rec["moved_from"] = rec0
    _ARRIVAL_STARTS[f"{node_id}@{width_m:g}"] = rec
    return (i, j), rec


def writer_batter() -> tuple[float, float, float]:
    """`(grade, batter reach, lattice pitch)` AS THE WRITER HOLDS THEM.

    Asked of `ribbon`, not restated: the plan-time self-adjacency key is the
    writer's `self_corridor` quantity, and a second spelling of its grade or
    its reach is how the two would drift apart.  Imported lazily for the
    reason `write_zones` gives."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    return (float(ribbonmod.BATTER_GRADE), float(ribbonmod.BATTER_MAX_M),
            float(ribbonmod.tcdata.SCALE))


# ---------------------------------------------------------------------------
# THE WRITER'S WATER, ASKED AT PLAN TIME -- route.WALKED_WET_DEPENDS_ON
# ---------------------------------------------------------------------------
# "walked" asks the writer's own stamp, rim gate and water sign over the
# generated field.  NEUTER "footprint" is the rule that shipped: the planner's
# only water question was the levelled footprint (`route.WET_DEPENDS_ON`), so
# a batter the writer then refused on water -- and the cap it paves past the
# last station -- was approved.  MEASURED on Fresh42 drive 2: 5 of 14
# corridors refused at step 4 on exactly that.
_WET_TEST = "walked"
# WHICH REMEDY IS ASKED FIRST.  "alignment" moves the line off the water the
# writer refused (route.WATER_STANDOFF_DEPENDS_ON) and only then ceilings the
# profile on the rays still refused.  NEUTER "profile" is the ceiling alone,
# the remedy that was tried and reverted: it cannot serve a ray whose ceiling
# is under the freeboard floor.
_WET_REMEDY = "alignment"
# Rounds of each remedy.  Each alignment round unions the stand-off of every
# seed-wet body the writer refused so far; each ceiling round lowers only the
# stations whose rays are still refused.  The loop keeps the best candidate
# it has seen and ends at the first clear one.
WATER_ALIGN_ROUNDS = 4
WATER_CEILING_ROUNDS = 3
# THE TERMINAL TRIMS TRIED, in stations (2 m each), shortest first: the
# first that leaves no refusal within batter reach of that end is kept.
# Bounded at 36 m -- past that the leg is not ending at its node any more,
# and the refusal is left standing with its number for the node's owner.
WATER_TRIM_STATIONS = (2, 4, 6, 9, 13, 18)
# THE CEILING AIMS THIS FAR UNDER THE WATER PLANE at the refused sample, for
# the same reason the self-adjacency join aims inside its allowance: the
# writer reads `profile_y` rounded to 1 mm and the ground on the integer
# lattice.
WATER_CEILING_MARGIN_M = 0.05
_GEN_PATCHES: dict = {}


# DROWNED_TRIM_DEPENDS_ON: A DROWNED FOOTPRINT AT A TERMINUS IS THE TERMINAL
# TRIM'S TO SERVE -- a refusal raised inside the as-routed build must not
# pre-empt the remedy that exists for it.
#
# `build_segment` refuses a station whose levelled footprint stands under the
# plane unbridged (`DrownedFootprint`), and it refuses it on the AS-ROUTED
# build -- before `water_route`, whose terminal trim (WATER_TRIM_STATIONS)
# exists for exactly the ground at a leg's end, is ever called.  MEASURED
# 2026-09-26 on Fresh42 drive 3: S13-stathub-dock's LAST station, 505 =
# (659.9, 861.2) at its dock terminus, has a footprint floor 1.20 m under
# the plane; stage 9 dropped the leg (so the census never read it) and stage
# 11 could not serve it.  Trimmed by 2 stations it builds 939.0 m and the
# writer lays it CLEAR (0 water steps, 0 drains, no plan refusal).  So:
#   the_drowned_stations_the_build_refused   carried on the refusal;
#   their_distance_from_each_end             every one within a trim of ONE
#                                            end, or no trim is offered;
#   the_terminal_trims_water_route_offers    WATER_TRIM_STATIONS, shortest
#                                            first; the build re-asks every
#                                            gate of the trimmed line itself.
# An interior drowned station is not a terminus's and stays refused.
DROWNED_TRIM_DEPENDS_ON = ("the_drowned_stations_the_build_refused",
                           "their_distance_from_each_end",
                           "the_terminal_trims_water_route_offers")
# Swapped out by the selftest neuters: "offered" | "off" (the shipped
# refusal: the leg is unroutable) | "always" (WRONG: every leg trimmed).
_DROWNED_TRIM_MODE = "offered"


class DrownedFootprint(ValueError):
    """`build_segment`'s drowned-footprint refusal, with the stations it
    refused (`stations`, indices into a line of `n` stations)."""

    def __init__(self, msg: str, stations, n: int):
        super().__init__(msg)
        self.stations = [int(k) for k in stations]
        self.n = int(n)


def drowned_trims(exc: DrownedFootprint) -> list[dict[str, int]]:
    """The terminal trims, shortest first, that take every drowned station
    off ONE end of the line (DROWNED_TRIM_DEPENDS_ON); [] when none can."""
    if _DROWNED_TRIM_MODE == "off" or not exc.stations:
        return []
    out: list[dict[str, int]] = []
    for n in WATER_TRIM_STATIONS:
        if min(exc.stations) >= exc.n - n:
            out.append({"b": n})
        if max(exc.stations) < n:
            out.append({"a": n})
    return out


# TERMINAL_CROSSING_TRIM_DEPENDS_ON: A CROSSING WHOSE FREE RUN REACHES THE
# SEGMENT'S OWN END HAS NO FAR BANK, SO THE ROAD ENDS ON ITS NEAR ONE.
#
# `declare_banks` flags such a bank (`terminal_banks`,
# CROSSING_BANKS_DEPENDS_ON) and step 6.5 refuses its abutment with the
# terminus as owner: no emission benches the ground beyond the last station,
# so no deck can land there.  MEASURED on the current tree's re-plan
# (`/tmp/deckfix/plan_after`, 2026-09-26): T5-temple-dockshore x01 free run
# 188..196 of 197 stations, T13-temple-harboursouth x01 131..136 of 137,
# S13-stathub-dock x01 483..510 of 511, W9-vestvik-lhwest x04 1315..1337 of
# 1338 -- every one ending ON the last station.  The terminal trims on offer
# were `WATER_TRIM_STATIONS`, a ladder capped at 18 stations for pulling a
# CAP off water; these runs need 9, 6, 28 and 23, and no constant is the
# right cap, because the number is the crossing's own.  So:
#   the_free_run_that_reaches_the_end   `terminal_banks` on the crossing the
#                                       build itself declared;
#   the_stations_from_its_near_bank     the trim: every station from the
#                                       free run's near bank to that end,
#                                       read off the record, never a ladder.
# The trimmed line is BUILT again, so every gate asks the line that is
# written, and asked again while the re-built line still ends in a free run.
#
# AND IT IS A CANDIDATE, NEVER A DECREE.  Applied unconditionally it cost a
# leg that lays: MEASURED on the current tree, `--only T5-temple-dockshore`,
# trimmed at every build the leg was REJECTED at plan time (3 water steps,
# worst 0.9838 m at [-83,-390], owner the dockshore terminus's approach);
# with the crossing kept it plans 383.9 m, 0 water steps, and laid at drive
# 3's step 4.  So the trim is asked of the WINNER of `water_route`, after
# every water remedy, and kept only where the trimmed line builds, passes
# the plan's own refusals and the writer lays it NO WORSE on water.  Where it
# is declined the crossing stands, with the reason on the record, and step
# 6.5's refusal names the terminus -- the true owner -- as before.
TERMINAL_CROSSING_TRIM_DEPENDS_ON = ("the_free_run_that_reaches_the_end",
                                     "the_stations_from_its_near_bank",
                                     "the_trimmed_line's_own_gates")
# Swapped out by the selftest neuters: "trimmed" | "kept" (the shipped plan:
# the leg keeps a crossing with no far bank) | "forced" (WRONG: the trim
# adopted whatever the trimmed line's gates say).
_TERMINAL_CROSSING_MODE = "trimmed"


def terminal_crossing_trim(rec: dict) -> dict[str, int]:
    """Stations to take off each end of `rec` so that no crossing's free run
    reaches it (TERMINAL_CROSSING_TRIM_DEPENDS_ON); {} when none does."""
    n = len(rec["nodes"])
    out: dict[str, int] = {}
    for c in rec.get("crossings") or ():
        ends = c.get("terminal_banks") or ()
        if "bank_b" in ends:
            out["b"] = max(out.get("b", 0), n - int(c["bank_a"]["station"]))
        if "bank_a" in ends:
            out["a"] = max(out.get("a", 0), int(c["bank_b"]["station"]) + 1)
    return out


def _built_trim(seg: dict, kw: dict) -> dict[str, int]:
    """The trim `seg` was actually built with: `build_segment` records it
    under the end nodes' ids (`water_trim`); a fixture has only `kw`."""
    wt = seg.get("water_trim")
    if wt:
        return {"a": int(wt.get(seg["from"], 0)),
                "b": int(wt.get(seg["to"], 0))}
    t = kw.get("trim") or {}
    return {"a": int(t.get("a", 0)), "b": int(t.get("b", 0))}


def trim_terminal_crossing(seg: dict, kw: dict, ww: dict, *, build, refusal,
                           water) -> tuple:
    """`seg` (built with `kw`, laid by the writer as `ww`) trimmed back to
    the bench before every free run that reaches its end, as `(record,
    joint, ww, kw, note)`; `(None, None, None, None, note)` where nothing
    reaches an end, or the trimmed line does not build, is refused by the
    plan (`refusal`), or is laid WORSE on water (`water(record)`).  See
    TERMINAL_CROSSING_TRIM_DEPENDS_ON."""
    rounds: list[dict] = []
    got = None
    cur, cur_kw, cur_ww = seg, kw, ww
    while _TERMINAL_CROSSING_MODE != "kept":
        cut = terminal_crossing_trim(cur)
        if not cut:
            break
        had = _built_trim(cur, cur_kw)
        nkw = {**cur_kw, "trim": {k: had[k] + int(cut.get(k, 0))
                                  for k in ("a", "b")}}
        if nkw.get("ceiling"):
            # a ceiling is keyed on the built line's stations: re-key it on
            # the stations the trim keeps.
            na, keep = int(cut.get("a", 0)), (len(cur["nodes"])
                                              - int(cut.get("b", 0)))
            nkw["ceiling"] = {int(k) - na: v for k, v in nkw["ceiling"].items()
                              if na <= int(k) < keep}
        row = {"cut": cut, "trim": nkw["trim"], "stations": len(cur["nodes"]),
               "terminal_spans_m": [round(float(c.get("span_m") or 0.0), 2)
                                    for c in cur.get("crossings") or ()
                                    if c.get("terminal_banks")]}
        rounds.append(row)
        try:
            cand, j = build(**nkw)
        except ValueError as exc:
            row["declined"] = f"unbuildable: {str(exc)[:160]}"
            break
        why = refusal(cand)
        if why is not None and _TERMINAL_CROSSING_MODE != "forced":
            row["declined"] = f"refused by the plan: {why}"
            break
        cw = water(cand)
        row["water_steps_and_drains"] = [_water_score(cur_ww),
                                         _water_score(cw)]
        if (_water_score(cw) > _water_score(cur_ww)
                and _TERMINAL_CROSSING_MODE != "forced"):
            row["declined"] = "the writer lays the trimmed line WORSE on water"
            break
        cur, cur_kw, cur_ww, got = cand, nkw, cw, (cand, j)
    note = ({"depends_on": list(TERMINAL_CROSSING_TRIM_DEPENDS_ON),
             "rounds": rounds, "adopted": got is not None} if rounds else None)
    if got is None:
        return None, None, None, None, note
    return got[0], got[1], cur_ww, cur_kw, note


# TERMINAL_LANDING_PLAN_DEPENDS_ON: A CROSSING WHOSE TERMINAL BANK NO DECK CAN
# LAND IS REFUSED AT PLAN TIME, WITH ITS NUMBERS.
#
# `trim_terminal_crossing` is a candidate; where it is declined the crossing
# stands, and step 6.5 is then the first to learn that the bank cannot be met:
# MEASURED, Ulfsland regen, T2-temple-brgs1-x01 -- a flat deck at y 52.757 over
# ground at 27.7 m, 25 m up, planned, built to the road's own bench and
# refused at the deck.  `boardwalk.terminal_landing_reach_m` is the ONE
# statement of how far below its datum a deck can land (a landing's rows, the
# bank step, and what the span itself gives back above the clearance floor);
# asked here of the GENERATED field at the landing's own rows, taking the
# HIGHEST ground they meet -- the most the builder could be offered -- so a
# refusal here is one the builder could not have answered.
# NEUTER `_TERMINAL_LANDING_PLAN_MODE = "kept"`: the leg is planned with the
# crossing and the refusal waits for step 6.5.  Proven by
# `selftest_terminal_landing_plan`.
TERMINAL_LANDING_PLAN_DEPENDS_ON = ("the_decks_own_landing_reach",
                                    "the_generated_ground_at_its_rows")
_TERMINAL_LANDING_PLAN_MODE = "refused"    # refused | kept


def terminal_landing_rejection(seg: dict, fld) -> dict | None:
    """The plan's refusal of `seg`, or None: a crossing recorded at a terminal
    bank (`terminal_banks`) whose deck stands further above the highest
    generated ground its landing can meet than a deck can land
    (TERMINAL_LANDING_PLAN_DEPENDS_ON)."""
    if _TERMINAL_LANDING_PLAN_MODE != "refused":
        return None
    rows = []
    for c in seg.get("crossings") or ():
        H = c.get("required_deck_y")
        if H is None:
            continue
        ax, az = (float(v) for v in c["bank_a"]["xz"])
        bx, bz = (float(v) for v in c["bank_b"]["xz"])
        length = math.hypot(bx - ax, bz - az)
        if length <= 0.0:
            continue
        ux, uz = (bx - ax) / length, (bz - az) / length
        for end in c.get("terminal_banks") or ():
            sign = 1.0 if end == "bank_b" else -1.0
            x0, z0 = (float(v) for v in c[end]["xz"])
            ground = [float(fld.bilinear(x0 + sign * ux * k,
                                         z0 + sign * uz * k))
                      for k in range(0, boardwalk.LAND_DESCENT_MAX_TILES + 2)]
            reach = boardwalk.terminal_landing_reach_m(c)
            gap = float(H) - max(ground)
            if gap > reach + 1e-6:
                rows.append(dict(crossing_id=c["crossing_id"], bank=end,
                                 deck_y=float(H), highest_ground_y=round(
                                     max(ground), 3),
                                 gap_m=round(gap, 3), reach_m=round(reach, 3),
                                 span_m=c.get("span_m")))
    if not rows:
        return None
    w = max(rows, key=lambda r: r["gap_m"])
    return {
        "why": f"{w['crossing_id']} {w['bank']} is its segment's own end and "
               f"its deck stands at y {w['deck_y']:.3f} m, {w['gap_m']:.3f} m "
               f"above the highest generated ground {w['highest_ground_y']:.3f}"
               f" m its landing can meet; a deck lands {w['reach_m']:.3f} m "
               f"at most (boardwalk.terminal_landing_reach_m: "
               f"{boardwalk.LAND_DESCENT_MAX_TILES} rows at "
               f"{boardwalk.LAND_TILE_STEP_M} m, the bank step, and what the "
               f"span gives back above the clearance floor). That is a slope "
               f"or open water, not a shore: re-route so the road reaches "
               f"it, or end the road on its near bank.",
        "terminal_landing": rows,
        "depends_on": list(TERMINAL_LANDING_PLAN_DEPENDS_ON),
    }


def build_past_drowned(build) -> tuple[dict, dict, dict | None]:
    """`build()`, or -- when it is refused for a drowned footprint within a
    terminal trim of one end -- the shortest trimmed build that stands:
    `(record, joint, the trim or None)`."""
    try:
        rec, j = build()
        if _DROWNED_TRIM_MODE == "always":
            rec, j = build(trim={"b": WATER_TRIM_STATIONS[0]})
            return rec, j, {"b": WATER_TRIM_STATIONS[0]}
        return rec, j, None
    except DrownedFootprint as exc:
        for t in drowned_trims(exc):
            try:
                rec, j = build(trim=t)
            except ValueError:
                continue
            rec["drowned_terminus"] = {
                "depends_on": list(DROWNED_TRIM_DEPENDS_ON),
                "stations_refused": len(exc.stations),
                "trim": t, "as_routed_refusal": str(exc)[:240]}
            return rec, j, t
        raise


def keep_trim(build, base: dict | None):
    """`build` with the terminus trim its as-routed line needed kept under
    every candidate's own trim (a water trim only lengthens it)."""
    if not base:
        return build

    def trimmed(*, trim=None, **kw):
        return build(trim={k: max(int(base.get(k, 0)),
                                  int((trim or {}).get(k, 0)))
                           for k in ("a", "b")}, **kw)
    return trimmed


# PLAN_REFUSAL_DEPENDS_ON: THE PLAN REFUSES ONLY WHAT THE STAMP EXPLAINS.
# A caused row the stamp itself explains -- `refused water_fill`, `refused
# pad`, `refused poi` -- is a refusal at any ground and drops the leg.  An
# unexplained `earthwork` row is not: the plan stamps each leg on VIRGIN
# ground, and the writer stamps them in order on the ground the earlier legs
# left, so the two disagree in both directions.  MEASURED on Fresh42 drive 8:
# S11-southcape-lhsouth stamps 4 steps at lh-south on virgin ground and the
# writer laid it with 0 (S4 had already graded that edge); T6-stathub-
# northcape stamps clean and the writer refused it with the collar.  A
# strict plan lost both (13 of 15 legs, S11 unroutable, T6 rejected), so the
# earthwork rows are left to the writer, whose refusal writes nothing and
# whose `road_ground` step retries the other collar arm (buildstage
# COLLAR_RETRY_DEPENDS_ON).  NEUTER `_PLAN_REFUSAL_MODE = "all"`: every caused
# row is a plan-time refusal.  Proven by `selftest_plan_refusal`.
PLAN_REFUSAL_DEPENDS_ON = ("explained_refusals_only",)
_PLAN_REFUSAL_MODE = "explained"    # explained | all


def plan_refusal_rows(labels: list[tuple[dict, str]]) -> list[dict]:
    """The caused rows that are a plan-time refusal: `labels` is `(row,
    rim_step_owner label)`.  POI and pad rows are counted on their own."""
    return [r for r, lab in labels
            if "poi" not in lab and "pad" not in lab
            and (_PLAN_REFUSAL_MODE == "all" or lab.startswith("refused"))]


# ARRIVAL_ADVISORY_DEPENDS_ON: A PAD APPROACH THE STAMP CANNOT GRADE IS
# REPORTED, NOT A REASON TO DELETE THE ROAD.  The stamp grades the walk to a
# pad as far as the height difference and the earth budget allow
# (`approach_samples`); a step it still leaves is recorded on the leg
# (`arrival_failures`) for the gate to judge.  Refusing the leg removes a road
# the network needs: S8-wtsouth-treesth (0.8557 m onto wt-south) and
# S11-southcape-lhsouth were laid in Fresh42 drive 6 and were lost from the
# drive-7 plan for it, disconnecting wt-south.  NEUTER `_ARRIVAL_REFUSAL_MODE
# = "refuse"`: every unfixed approach refuses the leg.  Proven by
# `selftest_plan_refusal`.
ARRIVAL_ADVISORY_DEPENDS_ON = ("an_approach_the_grading_could_not_fix",)
_ARRIVAL_REFUSAL_MODE = "advisory"    # advisory | refuse


def tracked_build(build, base: dict | None):
    """`build` for WATER_REREMEDY_DEPENDS_ON: every candidate is built ON TOP
    of `base`, the remedy a line already took (its stand-off OR'd in, its
    trim kept at least as long), and the kwargs each record was built with
    are kept in `.used` by the record's id.  `base=None` builds exactly as
    `build` does."""
    base = base or {}
    used: dict[int, dict] = {}

    def wrapped(*, extra_forbid=None, trim=None, ceiling=None):
        b_ef, b_tr = base.get("extra_forbid"), base.get("trim")
        ef = (extra_forbid if b_ef is None else b_ef if extra_forbid is None
              else b_ef | extra_forbid)
        tr = (trim if not b_tr else
              {k: max(int(b_tr.get(k, 0)), int((trim or {}).get(k, 0)))
               for k in ("a", "b")})
        rec, j = build(extra_forbid=ef, trim=tr, ceiling=ceiling)
        used[id(rec)] = {"extra_forbid": ef, "trim": tr}
        return rec, j
    wrapped.used = used
    return wrapped


_PLAN_PATCHES = None
_PLAN_PAD_KEEPOUTS = None


def writer_patches(_fld, zones) -> dict:
    """The writer's integer Heightmap lattice, never a half-grid re-sample."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    import hashlib                                         # noqa: PLC0415
    key = (str(SEED), "heightmap")
    cached = _GEN_PATCHES.setdefault(key, {})
    missing = sorted(z for z in zones if f"z_{z[0]}_{z[1]}" not in cached)
    if missing:
        digest = hashlib.sha256(repr(missing).encode()).hexdigest()[:16]
        cached.update(ribbonmod.zone_patches(
            missing, str(SEED), ribbonmod.SCRATCH / f"network_{digest}.bin"))
    return {f"z_{x}_{z}": cached[f"z_{x}_{z}"] for x, z in zones}


def _generated_patches(fld, zones) -> dict:
    """The zones' GENERATED heights from the routing field, in the writer's
    own in-memory patch shape (`ribbon._flat_patch`), cached per field."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    out = {}
    for zx, zz in zones:
        key = (fld.id, zx, zz)
        if key not in _GEN_PATCHES:
            _GEN_PATCHES[key] = ribbonmod._flat_patch(zx, zz, fld.bilinear)
        out[f"z_{zx}_{zz}"] = _GEN_PATCHES[key]
    return out


def lattice_ground(fld, a, b):
    """`ground(vx, vz)` for the 1 m samples of the line `a -> b`: the
    writer's generated height at an INTEGER vertex (BANK_SPAN_BED_DEPENDS_ON)
    -- `_PLAN_PATCHES` when the plan runs on the engine's patches, else the
    field re-sampled in the writer's patch shape (a fixture's ground).  Only
    the zones the samples' vertices stand in are asked for."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    zones = sorted({ribbonmod.tcdata.zone_of(i, j)
                    for x, z in span_samples(a, b)
                    for i, j in lattice_vertices(x, z)})
    patches = (_PLAN_PATCHES(fld, zones) if _PLAN_PATCHES is not None else
               _generated_patches(fld, zones))
    return lambda vx, vz: ribbonmod.appliedmod.generated_at(
        patches, float(vx), float(vz))


def _foot(nodes: np.ndarray, x: float, z: float) -> tuple[int, float, float]:
    """`(k, t, lat)`: the run `k -> k+1` nearest (x, z), the parameter of the
    foot on it and the distance to it."""
    a, b = nodes[:-1], nodes[1:]
    ab = b - a
    L2 = np.maximum((ab * ab).sum(axis=1), 1e-12)
    t = np.clip(((x - a[:, 0]) * ab[:, 0] + (z - a[:, 1]) * ab[:, 1]) / L2,
                0.0, 1.0)
    px, pz = a[:, 0] + t * ab[:, 0], a[:, 1] + t * ab[:, 1]
    d = np.hypot(px - x, pz - z)
    k = int(np.argmin(d))
    return k, float(t[k]), float(d[k])


# WALKED_KEEPOUT_DEPENDS_ON: THE WRITER'S BATTER IS REFUSED AT A POI KEEP-OUT
# DISC AS SURELY AS AT WATER, AND THE PLAN NEVER ASKED.
#
# `writer_water` stamped the leg with no keep-out beside it, so a batter the
# writer's own `ribbon.poi_standoff_discs` refuses -- and the wall that clip
# leaves at the disc -- was approved at plan time and refused at step 4.
# MEASURED on the current tree's re-plan of Fresh42, sequential step 4:
# S2-stathub-eastlobe (laid at drive 3) refused 1 step, 0.867 m at rim
# [634,297] from taken [634,296]: the rim sample stands 0.18 m inside the
# 22.0 m disc at (641.45, 317.51), and the batter plane (60.951, 60.170,
# 59.389 outward, each one slide limit apart) meets it 0.086 m too high, so
# no feather or closure can.  T8-stenvik-wttown: 51 such steps, worst
# 2.1394 m at [-321,595].  So the plan asks with the discs the writer gates
# on, through the one refusal population (`ribbon.refused_at_of`):
#   the_writers_own_poi_discs     `ribbon.poi_standoff_discs(zones)`;
#   a_caused_step_a_disc_refused  a caused row labelled `poi` is refused;
#   the_disc_the_face_stands_in   its remedy is the alignment's stand-off
#                                 from that disc (`keepout_standoff_forbid`,
#                                 grown by the half-width and the run the
#                                 batter needs to reach the disc's ground).
# NOT THE CEILING.  A ceiling of `step - limit + margin` at the face's foot
# was tried and MEASURED on the full re-plan: the lowered `hi` emptied
# T9-wttown-dockshore's interval at station 56 ([47.592, 46.97]) and the
# junction datum then REJECTED 6 legs (S5, T7, T13, T8, T9, W9), three of
# which lay.  A keep-out face the stand-off cannot clear stays REFUSED with
# its disc as owner (`water_owner`), at plan time.
WALKED_KEEPOUT_DEPENDS_ON = ("the_writers_own_poi_discs",
                             "a_caused_step_a_disc_refused",
                             "the_disc_the_face_stands_in")
# Swapped out by the selftest neuters: "poi" | "none" (the shipped plan: the
# leg stamped with no keep-out).
_WALKED_KEEPOUT_MODE = "poi"
# The planner's keep-out source, set by `main` to `writer_keepouts`; a
# fixture passes its own discs.  None: no disc is asked.
_PLAN_KEEPOUTS = None


def writer_keepouts(zones) -> list[tuple[float, float, float]]:
    """The POI discs `ribbon.main` gates the batter on, for these zones."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    return ribbonmod.poi_standoff_discs(list(zones))


def _writer_water_once(seg: dict, fld, keepouts=None) -> dict:
    """WOULD THE WRITER REFUSE THIS PLAN ON WATER OR A KEEP-OUT -- asked of
    the writer.

    `ribbon.stamp` rasterises the segment over the writer's integer
    Heightmap patches and pad keep-outs.  The routing field is sampled on
    half-integers: interpolating it back to integers smooths precisely the
    one-metre shore steps this check must predict.  Fixtures supply their
    own generated surface.  `junction.rim_transfer` and `water_sign` judge
    the finished surface, including foundations the build has yet to lay.

    Returns the verdict and its numbers, plus the private keys the remedies
    read: `_seeds` (every refused or drained wet sample with the road height
    its ray left from), `_keepout` (every refused disc, for the stand-off)
    and `_drops` (per station, how far the profile must fall for the plane
    to pass under the water plane, or inside the limit at a disc's face)."""
    if _WET_TEST == "footprint":
        return {"verdict": "CLEAR", "keyed_on": "levelled footprint only "
                "(route.WET_DEPENDS_ON) -- the NEUTER", "steps": 0,
                "drained": 0, "worst_m": 0.0, "worst_at": None,
                "keepout_steps": 0, "pad_steps": 0, "arrival_failures": [],
                "_seeds": [], "_drops": {}, "_feet": {}, "_keepout": []}
    import ribbon as ribbonmod                             # noqa: PLC0415
    J = routemod._junction()
    zones = ribbonmod.segment_zones(seg["nodes"], seg["is_bridge"],
                                    seg["width_m"])
    pads = (_PLAN_PAD_KEEPOUTS() if _PLAN_PAD_KEEPOUTS is not None else
            ribbonmod.PadKeepOut([], []))
    zones = sorted(set(zones) | pads.approach_zones(seg))
    patches = (_PLAN_PATCHES(fld, zones) if _PLAN_PATCHES is not None else
               _generated_patches(fld, zones))
    src = keepouts if keepouts is not None else _PLAN_KEEPOUTS
    discs = ([tuple(map(float, d)) for d in src(list(zones))]
             if src is not None and _WALKED_KEEPOUT_MODE == "poi" else [])
    comps, st = ribbonmod.stamp(seg, patches, list(zones), pads,
                                poi_keepouts=discs)
    def ground(x, z):
        return ribbonmod.appliedmod.generated_at(patches, float(x), float(z))

    def finished(sx, sz):
        pad_y = pads.finished_y(sx, sz)
        if pad_y is not None:
            return pad_y
        g = ground(sx, sz)
        return None if g is None else g + ribbonmod.delta_at(comps, sx, sz)
    tol = ribbonmod.appliedmod.AUTHOR_TOLERANCE_M
    taken_after: dict[tuple[int, int], float] = {}
    taken_gen: dict[tuple[int, int], float] = {}
    for (zx, zz), comp in comps.items():
        cx0, cz0 = ribbonmod.tcdata.zone_centre(zx, zz)
        for i, mod in enumerate(comp.modified_height):
            if not mod:
                continue
            gy, gx = divmod(i, ribbonmod.tcdata.PITCH)
            wx, wz = ribbonmod.tcdata.sample_world(cx0, cz0, gx, gy)
            k = (int(round(wx)), int(round(wz)))
            if k in taken_after:
                continue
            g = ground(float(k[0]), float(k[1]))
            after = g + ribbonmod.delta_at(comps, float(k[0]), float(k[1]))
            if abs(after - g) > tol:
                taken_after[k], taken_gen[k] = after, g
    rim = J.rim_transfer(
        taken_after, lambda sx, sz: ground(float(sx), float(sz)), None,
        top=1_000_000,
        height_after=finished)
    refused_at = ribbonmod.refused_at_of(st)
    plane_at: dict[tuple[int, int], float] = {}
    for wx, wz, cause, h in st["clip_at"] + st["closure_refused_at"]:
        if cause == "water_fill":
            k = (int(round(wx)), int(round(wz)))
            plane_at[k] = max(plane_at.get(k, -math.inf),
                              ground(float(k[0]), float(k[1])) + h)
    # THE WRITER'S OWN SPLIT (`ribbon.main`): a caused step standing in ground
    # a planned pad lays next, within one slide limit of that pad's datum, is
    # OWED to the pad, not refused (PLANNED_PAD_RIM_DEPENDS_ON).  The plan
    # counted every such row as a refusal the writer never made.
    # THE REMEDY SEARCH SCORES ON THE FIRST TERM OF THE OWED RULE ALONE; the
    # leg's FINAL verdict also takes the pad's own cliff
    # (PLANNED_PAD_CLIFF_DEPENDS_ON, `judged` below).
    rim_kept, _owed = ribbonmod.planned_pad_owed(rim["caused_rows"], pads,
                                                 J.SLIDE_PER_M, cliff=False)
    labels = [(r, ribbonmod.rim_step_owner(r, refused_at, None, J.SLIDE_PER_M))
              for r in rim_kept]
    keep_rows = [r for r, lab in labels if "poi" in lab]
    pad_rows = [r for r, lab in labels if "pad" in lab]
    rows = plan_refusal_rows(labels)      # PLAN_REFUSAL_DEPENDS_ON
    # WHAT THE STAMP REFUSED EACH ROW FOR, for `water_owner`
    # (OWNER_CAUSE_DEPENDS_ON): `refused a+b` -> {a, b}.
    row_causes: dict[tuple[int, int], set] = {}
    for r, lab in labels:
        if lab.startswith("refused "):
            row_causes.setdefault(
                (int(r["rim_xz"][0]), int(r["rim_xz"][1])), set()
            ).update(lab[len("refused "):].split("+"))
    sign = ribbonmod.water_sign(taken_gen, taken_after)
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    br = list(seg["is_bridge"])
    seeds: dict[tuple[int, int], float] = {}
    feet: dict[tuple[int, int], int] = {}
    drops: dict[int, float] = {}
    for r in rows:
        xz = (int(r["rim_xz"][0]), int(r["rim_xz"][1]))
        k, t, _lat = _foot(nodes, *xz)
        y_foot = float(prof[k] * (1.0 - t) + prof[k + 1] * t)
        seeds[xz] = max(seeds.get(xz, -math.inf), y_foot)
        feet[xz] = k if t < 0.5 else k + 1
        drop = (plane_at[xz] - WATER_LEVEL + WATER_CEILING_MARGIN_M
                if xz in plane_at else
                r["taken_height_after_m"] - r["rim_height_m"]
                - J.SLIDE_PER_M + WATER_CEILING_MARGIN_M)
        if drop > 0.0:
            for q in drop_stations(k, br):
                drops[q] = max(drops.get(q, 0.0), drop)
    for xz in sign["drained"]:
        xz = (int(xz[0]), int(xz[1]))
        seeds[xz] = max(seeds.get(xz, -math.inf), taken_after[xz])
        k, t, _lat = _foot(nodes, *xz)
        feet[xz] = k if t < 0.5 else k + 1
    # THE KEEP-OUT'S FACES (WALKED_KEEPOUT_DEPENDS_ON): the disc each rim
    # sample stands in, for the stand-off; a face standing over the limit
    # ABOVE the disc's ground lowers the stations at its foot by the excess.
    keepout: dict[tuple, tuple] = {}
    for r in keep_rows:
        xz = (int(r["rim_xz"][0]), int(r["rim_xz"][1]))
        k, t, _lat = _foot(nodes, *xz)
        y_foot = float(prof[k] * (1.0 - t) + prof[k + 1] * t)
        feet[xz] = k if t < 0.5 else k + 1
        if discs:
            cx, cz, rr = min(discs, key=lambda d: math.hypot(
                xz[0] - d[0], xz[1] - d[1]) - d[2])
            key = (round(float(cx), 2), round(float(cz), 2))
            g_rim = float(r["rim_height_before_m"])
            old = keepout.get(key)
            keepout[key] = (key[0], key[1], round(float(rr), 2),
                            max(y_foot, old[3]) if old else y_foot,
                            min(g_rim, old[4]) if old else g_rim)
    rows = rows + keep_rows + pad_rows
    steps = len(rows)
    arrivals = pads.arrivals(seg, finished)
    # THE LEG'S FINAL READING (PLANNED_PAD_CLIFF_DEPENDS_ON): the rows left once
    # the planned pads' own cliffs are owed to them.  Never fewer than the
    # search's reading would leave on a leg with no planned pad at its rim.
    j_kept, _j_owed = ribbonmod.planned_pad_owed(rim["caused_rows"], pads,
                                                 J.SLIDE_PER_M)
    j_labels = [(r, ribbonmod.rim_step_owner(r, refused_at, None,
                                             J.SLIDE_PER_M))
                for r in j_kept]
    j_steps = (len(plan_refusal_rows(j_labels))
               + sum(1 for _r, lab in j_labels if "poi" in lab)
               + sum(1 for _r, lab in j_labels if "pad" in lab))
    judged = {"steps": j_steps,
              "verdict": "REFUSED" if (j_steps or sign["drained_samples"] or (
                  arrivals and _ARRIVAL_REFUSAL_MODE == "refuse"))
              else "CLEAR"}
    return {
        "verdict": "REFUSED" if (steps or sign["drained_samples"] or (
            arrivals and _ARRIVAL_REFUSAL_MODE == "refuse")) else "CLEAR",
        "keyed_on": "route.WALKED_WET_DEPENDS_ON",
        "steps": steps,
        "worst_m": round(max((r["step_after_m"] for r in rows), default=0.0),
                         4),
        "worst_at": (max(rows, key=lambda r: r["step_after_m"])["rim_xz"]
                     if rows else None),
        "drained": int(sign["drained_samples"]),
        "drained_at": [list(k) for k in sign["drained"][:4]],
        "water_fill_clips": int(st.get("batter_clipped_water_fill", 0)),
        "keepout_steps": len(keep_rows),
        "pad_steps": len(pad_rows),
        "arrival_failures": arrivals,
        "keepout_discs": len(discs),
        "instrument": "ribbon.stamp over integer Heightmap patches with "
                      "writer pad/POI keep-outs, junction.rim_transfer on "
                      "the finished pad surface, ribbon.rim_step_owner, "
                      "ribbon.water_sign (network.writer_water)",
        "_seeds": [(x, z, y) for (x, z), y in sorted(seeds.items())],
        "_causes": {k: sorted(v) for k, v in row_causes.items()},
        "_judged": judged,
        "_drops": drops,
        "_feet": feet,
        "_keepout": sorted(keepout.values()),
    }


# COLLAR_CHOICE_DEPENDS_ON: A LEG TAKES THE COLLAR ONLY WHERE IT HELPS.  The
# pad collar (ribbon `_CONNECTION_COLLAR`) closes the fill onto a pad the road
# arrives at -- S7 and W9 lay clean only with it -- and on another leg it
# leaves a step the ground without it never had.  MEASURED on Fresh42 drive 8:
# T6-stathub-northcape 2 caused steps, worst 1.5626 m beside a second pad, with
# the collar, 0 without it (the arrival berm stays an advisory 0.267 m).  So
# the plan asks the writer's own stamp both ways for a leg the collar leaves
# refused, keeps the better, and records `connection_collar: false` on the
# leg, which `ribbon.stamp` reads.  NEUTER `_COLLAR_CHOICE_MODE = "on"`: the
# collar is always taken.  Proven by `selftest_plan_refusal`.
COLLAR_CHOICE_DEPENDS_ON = ("the_leg_with_and_without_its_collar",)
_COLLAR_CHOICE_MODE = "best"    # best | on


def writer_water(seg: dict, fld, keepouts=None) -> dict:
    """`_writer_water_once`, asked with the pad collar and -- for a leg the
    collar leaves refused -- without it (COLLAR_CHOICE_DEPENDS_ON).  The
    verdict carries `collar`: whether the arm it judged took the collar."""
    took = bool(seg.get("connection_collar", True))
    ww = _writer_water_once(seg, fld, keepouts)
    ww["collar"] = took
    if (_COLLAR_CHOICE_MODE != "best" or not took or _water_score(ww) == 0):
        return ww
    off = _writer_water_once({**seg, "connection_collar": False}, fld, keepouts)
    off["collar"] = False
    return off if _water_score(off) < _water_score(ww) else ww


def _water_score(ww: dict) -> int:
    return (int(ww["steps"]) + int(ww["drained"])
            + (len(ww.get("arrival_failures", []))
               if _ARRIVAL_REFUSAL_MODE == "refuse" else 0))


def judged_public(ww: dict) -> dict:
    """The leg's FINAL reading (PLANNED_PAD_CLIFF_DEPENDS_ON) as record keys,
    only where it differs from the search's: a leg whose two readings agree
    carries nothing new, so a plan that never needed the second is unchanged
    to the byte."""
    j = ww.get("_judged")
    if not j or (j["verdict"], j["steps"]) == (ww["verdict"], ww["steps"]):
        return {}
    return {"judged_verdict": j["verdict"], "judged_steps": j["steps"]}


def water_public(ww: dict) -> dict:
    """`writer_water`'s verdict without the remedies' private keys."""
    return {k: v for k, v in ww.items() if not k.startswith("_")}


# OWNER_CAUSE_DEPENDS_ON: A WATER REFUSAL IS NAMED FOR WHAT THE STAMP REFUSED IT
# FOR, AND FOR THE NEAREST END, NOT FOR THE FIRST ONE NAMED.  `water_owner`
# put every row that stood within batter reach of an end under "terminus <the
# first end>: its approach (deck/jetty)" and every other row under "the profile
# floor and the census band", whatever the stamp had refused it for.  MEASURED
# on the Ulfsland regen: T6-stathub-northcape's 4 rows are `refused
# shared_corridor` (the line's own second branch 5 m off, 2.4412 m) and
# W2/W3/W4/W8's [-77,1787] rows are `refused steeper_than_batter` (3.8962 m, a
# cliff beside the carriageway), all labelled "profile floor / census band" --
# an owner no census read or ceiling can serve -- and S12's rows read "terminus
# brg-s1-e" where the boathouse end was 12.9 m away and brg-s1-e 80 m.
# NEUTER `_OWNER_CAUSE_MODE = "position"`: position alone and the first end
# named, as before.  Proven by `selftest_owner_cause`.
OWNER_CAUSE_DEPENDS_ON = ("the_cause_the_stamp_refused_the_row_for",
                          "the_nearest_end")
_OWNER_CAUSE_MODE = "cause"    # cause | position


def water_owner(seg: dict, ww: dict, near_end_m: float) -> dict[str, int]:
    """WHO OWNS each water refusal no remedy here served, counted per owner.

    A refusal within batter reach of a terminal node is that node's approach
    (the dock or jetty a road cannot pave onto) -- the NEAREST end's, not the
    first end named: S12-brgs1-boathouse is 32 m long and every row of it
    stood within reach of both ends, so its rows read `terminus brg-s1-e`
    where the nearer end was the boathouse (OWNER_CAUSE_DEPENDS_ON); one whose
    foot is on a crossing's level bank run stands at the deck datum
    `Crossings` owns; one the stamp refused for the line's OWN SECOND BRANCH
    (`shared_corridor`), for ground steeper than the batter plane or for ground
    past the apply clamp names that, because no ceiling or census band is the
    remedy of a line that folds back on itself or runs under a cliff; any other
    has a ceiling under the floor in force and no alignment inside the ground
    this leg may be routed over (the census band) -- the census is what a wider
    move needs."""
    nodes = seg["nodes"]
    ends = ((seg["from"], nodes[0]), (seg["to"], nodes[-1]))
    bridged = [i for i, b in enumerate(seg["is_bridge"]) if b]
    causes = ww.get("_causes") or {}
    out: dict[str, int] = {}
    for x, z, _y in ww["_seeds"]:
        k = ww["_feet"].get((x, z))
        near = [(math.hypot(x - ex, z - ez), nid) for nid, (ex, ez) in ends
                if math.hypot(x - ex, z - ez) <= near_end_m]
        if _OWNER_CAUSE_MODE == "cause":
            near.sort()
        why = set(causes.get((x, z)) or ())
        if near:
            who = f"terminus {near[0][1]}: its approach (deck/jetty)"
        elif (k is not None and bridged and seg["crossings"]
              and min(abs(k - b) for b in bridged) * STATION_M
              <= BANK_LEVEL_M + STATION_M):
            c = min(seg["crossings"], key=lambda c: min(
                math.hypot(x - c[s]["xz"][0], z - c[s]["xz"][1])
                for s in ("bank_a", "bank_b")))
            who = (f"crossing {c['crossing_id']}: its bank level run at the "
                   f"deck datum {c.get('required_deck_y')}")
        elif "shared_corridor" in why and _OWNER_CAUSE_MODE == "cause":
            who = ("this leg's own second branch beside the line "
                   "(shared_corridor): the alignment folds back on itself")
        elif ("steeper_than_batter" in why or "over_clamp" in why) \
                and _OWNER_CAUSE_MODE == "cause":
            who = ("ground beside the line steeper than the batter plane or "
                   "past the apply clamp (steeper_than_batter / over_clamp): "
                   "the alignment")
        else:
            who = ("the profile floor (freeboard/clamp) and the census band "
                   "the alignment may move in")
        out[who] = out.get(who, 0) + 1
    for cx, cz, r, _y, _g in ww.get("_keepout") or ():
        who = (f"POI keep-out disc at ({cx:.1f}, {cz:.1f}) r {r:.1f} m: "
               f"the alignment and profile beside it")
        out[who] = out.get(who, 0) + 1
    return out


def keepout_standoff_forbid(corr: routemod.Corridor, keepout, *,
                            half_m: float, grade: float,
                            reach_m: float) -> np.ndarray:
    """Corridor cells a road may not take, from the POI discs whose face
    the writer refused (`keepout`: `(cx, cz, r, road_y, rim_ground)`): the
    disc grown by the footprint half-width and the run the batter needs to
    fall from the road to the ground at the disc's edge.  The keep-out form
    of `route.water_standoff_forbid` (WALKED_KEEPOUT_DEPENDS_ON)."""
    out = np.zeros((corr.m, corr.m), dtype=bool)
    slack = corr.cell_m * math.sqrt(0.5)
    for cx, cz, r, y_road, g_rim in keepout:
        reach = min(max(y_road - g_rim, 0.0) / grade, reach_m)
        R = r + half_m + reach + slack
        j0 = max(0, int(math.floor((cx - R - corr.x0) / corr.cell_m)))
        j1 = min(corr.m - 1, int(math.ceil((cx + R - corr.x0) / corr.cell_m)))
        i0 = max(0, int(math.floor((cz - R - corr.z0) / corr.cell_m)))
        i1 = min(corr.m - 1, int(math.ceil((cz + R - corr.z0) / corr.cell_m)))
        if j1 < j0 or i1 < i0:
            continue
        CI, CJ = np.mgrid[i0:i1 + 1, j0:j1 + 1]
        d = np.hypot(corr.x0 + CJ * corr.cell_m - cx,
                     corr.z0 + CI * corr.cell_m - cz)
        out[CI, CJ] |= d < R
    return out


def profile_stats(y, s, t, tw_lo, tw_hi, free) -> dict:
    """Every number the plan reports about a PROFILE, from one function.

    Split out of `build_segment` so a profile re-solved after the fit -- the
    junction datum, `route.junction_datums` -- is reported by the same
    arithmetic as the profile it replaced, rather than carrying the fitted
    profile's numbers beside a profile that no longer has them.  Keys in the
    order the segment record has always carried them."""
    CF = routemod.CUT_FILL_MAX
    y = np.asarray(y, dtype=np.float64)
    ds = np.diff(s)
    grade = np.abs(np.diff(y)) / np.maximum(ds, 1e-9)
    terr = ~np.asarray(free, dtype=bool)
    # THREE numbers now, and the third is the one that was missing.
    #
    # `over_margin` is against the 7 m design margin this tool fits to;
    # `over_clamp` is against the MEASURED 8 m limit in
    # TerrainComp::ApplyToHeightmap, which is the only one that decides
    # whether the ribbon lands where the plan says.  Collapsing those two
    # reported a buildable road as a failure and would have reported an
    # unbuildable one as fine if the margin were ever raised.
    #
    # BOTH ARE NOW MEASURED OVER THE FOOTPRINT, because the clamp is per
    # sample and the bench is 8 m wide.  The worst |delta| a station owes is
    # at one of the two extremes of its transverse window, so
    #     worst_abs = max(|y - tw_lo|, |y - tw_hi|)
    # is exact for a level bench -- no sampling, no estimate.
    # `*_centreline` is kept beside it, NOT instead of it: this change made
    # the counters STRICTER and a tightening recorded only in its effect is
    # as silent as a shrink.  On N4 the old pair read (0, 0) while the
    # rasteriser found 71 breaches; the two numbers side by side are what
    # makes that visible next time.
    worst_abs = np.maximum(np.abs(y - tw_lo), np.abs(y - tw_hi))[terr]
    cf_cl = (y - t)[terr]
    # Signed extremes over the footprint: the deepest cut is against the
    # HIGHEST sample under the bench, the largest fill against the lowest.
    cf_cut = (y - tw_hi)[terr]
    cf_fill = (y - tw_lo)[terr]
    cf = cf_cl
    return {
        "grade_max_measured": round(float(grade.max()), 4) if len(grade) else 0.0,
        "grade_p95": round(float(np.percentile(grade, 95)), 4) if len(grade) else 0.0,
        "grade_mean": round(float(grade.mean()), 4) if len(grade) else 0.0,
        "cut_max_m": round(float(cf_cut.min()), 2) if cf_cut.size else 0.0,
        "fill_max_m": round(float(cf_fill.max()), 2) if cf_fill.size else 0.0,
        "cut_max_centreline_m": round(float(cf.min()), 2) if cf.size else 0.0,
        "fill_max_centreline_m": round(float(cf.max()), 2) if cf.size else 0.0,
        "stations_over_clamp": int((worst_abs > routemod.CLAMP_M).sum()),
        "stations_over_design_margin": int((worst_abs > CF).sum()),
        # The old, weaker pair, kept beside the new one rather than replaced.
        # A tightening visible only in its effect is as silent as a shrink.
        "stations_over_clamp_centreline": int(
            (np.abs(cf_cl) > routemod.CLAMP_M).sum()),
        "stations_over_design_margin_centreline": int(
            (np.abs(cf_cl) > CF).sum()),
    }


def build_segment(fld, edge: dict, corr: routemod.Corridor, pois: list,
                  land_only: bool = False,
                  fixed_pts: list | None = None, *,
                  census: censusmod.Census | None,
                  joint: dict | None = None,
                  extra_forbid: np.ndarray | None = None,
                  ceiling: dict[int, float] | None = None,
                  trim: dict[str, int] | None = None,
                  line_out: list | None = None) -> dict:
    """Route and profile one leg.

    `fixed_pts` TAKES THE CENTRELINE AS AN INPUT instead of searching for one,
    and it exists so a leg's GEOMETRY can be held while its PROFILE is
    re-solved.  That is not a convenience: a centreline is the object the
    rasteriser cleared, the POI gate measured and (once written) the terrain
    blobs were computed against, while a profile is a solution over it.  When
    the profile solver is corrected -- as it was for the transverse footprint
    -- re-searching would silently hand back a DIFFERENT road, and the correct
    answer to "re-fit this proven line" must not be "route it again".

    MEASURED reason it is needed, on N2-shelf-mid1: the stored plan's line was
    routing variant A and cleared the rasteriser with 0 distance violations
    over 11,531 carriageway samples; re-searching it under the corrected
    solver returned a line 1.09 m closer to an `InfestedTree01`, taking the
    predicted POI margin from -0.06 m to -1.15 m -- i.e. a carriageway edge
    4.85 m from an instance whose gate boundary is 6.0 m, which
    `ribbon.location_check` refuses.  Two roads, both legitimate outputs of
    their own inputs; only one of them had been cleared.

    Everything downstream is unchanged and still runs: crossing detection,
    `straighten` over each crossing and abutment window (a no-op on a run
    already straight, so re-fitting a previously straightened line does not
    move it), the one constraint set and the grade ladder.

    `census` IS KEYWORD-ONLY AND HAS NO DEFAULT, so every call site has to say
    which world it is routing against and none can inherit the mod-blind
    answer by omission.  It is the reading of the ground taken AFTER the zones
    were generated; `None` is legal only for a PROVISIONAL line (the one
    `precensus.py survey` needs in order to know where to look), and a
    provisional segment carries `census.verdict == "PROVISIONAL"` so
    `ribbon.py` refuses to pave it.

    `joint`, when given, is FILLED with what `route.junction_datums` needs to
    re-solve this profile together with every other leg's: the leg's own
    bounds, arc, rung, ladder and self-adjacency lattice.  It is a side
    channel rather than a key of the returned record because the record is
    serialised as it stands by other callers (`north.py`).

    `line_out`, when given, is FILLED with the searched centreline the moment
    the search returns -- BEFORE any refusal below can raise -- so a caller
    whose leg is then refused still knows where the router went.  A
    provisional plan hands that line to the census (`census_lines`), because
    stage 11 routes the leg again and ground nobody read under it is a leg
    stage 11 can never serve (precensus.band_lines).
    """
    a_node = specmod.NODES[edge["a"]]
    b_node = specmod.NODES[edge["b"]]
    cls = edge["cls"]
    # Crossings' deck width wins over my road-class default where a segment
    # arrives at one of its terminals: a 6 m trunk ribbon meeting a 4 m jetty
    # IS the pinch that matching widths exists to avoid.
    width_m = float(edge.get("width_m") or WIDTH_M[cls])
    half_w = width_m / 2.0
    # A per-edge override exists for exactly one situation: a short ramp from
    # high ground down onto a bridge deck whose height Crossings owns.  The
    # portal hall stands 6.8 m above the S2 abutment 30 m away, so no 12 %
    # profile reaches it and the interval propagation PROVES that (2 stations
    # infeasible).  Lengthening a 30 m spur into a 60 m switchback to save 10 %
    # of grade on a bridge approach is worse for the operator than a short ramp.
    # Overrides are recorded per segment and are never silent.
    g_max = float(edge.get("grade_max") or routemod.GRADE[cls])

    terminal = None
    if fixed_pts is not None:
        # Arclength recomputed from the points themselves, never carried over
        # from the file: a station list and an `s` array that disagree is a
        # profile fitted at the wrong distances.
        pts = [(float(x), float(z)) for x, z in fixed_pts]
        s = np.concatenate([[0.0], np.cumsum(
            np.hypot(np.diff([p[0] for p in pts]),
                     np.diff([p[1] for p in pts])))])
    else:
        # `extra_forbid` is ONE LEG'S stand-off from the water its writer
        # refused (route.WATER_STANDOFF_DEPENDS_ON).  It is laid over the
        # shared corridor for this search only and taken off again, so no
        # other leg -- and no routing order -- ever sees it.
        corr.forbid_all_water = land_only
        shared_forbid = corr.forbid
        if extra_forbid is not None:
            corr.forbid = (extra_forbid if shared_forbid is None
                           else shared_forbid | extra_forbid)
        try:
            raw = corr.search(
                search_xz(edge["a"], corr, width_m=width_m,
                          extra_forbid=extra_forbid),
                search_xz(edge["b"], corr, width_m=width_m,
                          extra_forbid=extra_forbid))
        finally:
            corr.forbid_all_water = False
            corr.forbid = shared_forbid
        terminal = corr.last_terminal
        # Simplify tolerance is DELIBERATELY tight.  A* returns one node per
        # 4 m cell, i.e. a staircase, and the ribbon is rasterised from
        # segments, so a staircase shows as a scalloped edge from the first
        # step.  But every metre of chord-cutting also moves the centreline
        # off the cells the POI mask cleared, so the tolerance is 1 m and the
        # mask is dilated to cover it rather than the tolerance being
        # loosened and the breach being discovered afterwards.
        poly = routemod.simplify(raw, tol_m=SIMPLIFY_TOL_M)
        pts, s = routemod.resample(poly, STATION_M)
        s = np.asarray(s, dtype=np.float64)
        if line_out is not None:
            line_out[:] = [[round(float(x), 2), round(float(z), 2)]
                           for x, z in pts]

    # ---- THE ARRIVAL, before the pad edge and the fit (ARRIVAL_DEPENDS_ON) --
    # A searched line ends where the search could START, outside the pad's
    # grown A* mask; the spur carries it to the pad it serves.  A proven line
    # (`fixed_pts`) is re-fitted where it stands.
    arrival = None
    if fixed_pts is None:
        arrival = arrive_on_pads(pts, edge, routemod.footprint_half_m(width_m),
                                 corr, extra_forbid)
        if len(arrival["pts"]) != len(pts):
            pts = arrival["pts"]
            s = np.concatenate([[0.0], np.cumsum(
                np.hypot(np.diff([p[0] for p in pts]),
                         np.diff([p[1] for p in pts])))])
            if line_out is not None:
                line_out[:] = [[round(float(x), 2), round(float(z), 2)]
                               for x, z in pts]
        del arrival["pts"]

    # ---- THE PAD EDGE, before anything is fitted to this geometry ----------
    # Applied here rather than after profiling because the profile is a
    # solution OVER the geometry: trimming afterwards would leave a `y` array
    # fitted to stations that no longer exist.
    pad = enforce_pad_edge(pts, edge, routemod.footprint_half_m(width_m),
                           searched=fixed_pts is None)
    if len(pad["pts"]) != len(pts):
        pts = pad["pts"]
        s = np.concatenate([[0.0], np.cumsum(
            np.hypot(np.diff([p[0] for p in pts]),
                     np.diff([p[1] for p in pts])))])
    if pad["interior_violations"] and fixed_pts is None:
        raise ValueError(
            "centreline crosses a written pad footprint: "
            + "; ".join(f"{v['pad']} {v['xz']} for {v['stations']} station(s)"
                        for v in pad["interior_violations"][:4])
            + ". A road levelling ground under a building is the defect class "
              "this build exists to avoid; re-route the leg.")
    if pad["interior_violations"] and fixed_pts is not None:
        # A PROVEN LINE IS RECORDED, NOT REFUSED.  These 12 built legs already
        # cross a pad; the refusal belongs to whoever routes the NEXT one.
        print(f"  {edge['id']} re-fit: crosses "
              + "; ".join(f"{v['pad']} for {v['stations']} station(s)"
                          for v in pad["interior_violations"][:4])
              + " -- RECORDED, not refused (fixed_pts: proven alignment)")
    # ---- THE TERMINAL CAP, TRIMMED OFF THE WATER (WALKED_WET_DEPENDS_ON) ---
    # `trim` removes whole stations from an end AFTER the pad edge, so the
    # ribbon's cap -- clamped past the last station, where no station's
    # cross-section ever looks -- stops short of the seed-wet ground
    # `writer_water` found it paving.  `s` is sliced, never re-measured, so
    # every station kept keeps the arc its profile was fitted on.
    trim_rec = None
    if trim and any(trim.values()):
        na, nb = int(trim.get("a", 0)), int(trim.get("b", 0))
        if na + nb > len(pts) - 2:
            raise ValueError(f"{edge['id']}: a terminal trim of {na}+{nb} "
                             f"station(s) leaves no road")
        cut_m = float(s[na] - s[0]) + float(s[-1] - s[len(pts) - 1 - nb])
        pts = pts[na:len(pts) - nb]
        s = np.asarray(s[na:len(s) - nb], dtype=np.float64) - float(s[na])
        trim_rec = {edge["a"]: na, edge["b"]: nb, "trimmed_m": round(cut_m, 1)}
    t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)

    # Pass 1, terrain only: its infeasible spans plus the wet stations ARE the
    # definition of a crossing, and the infeasibility is a PROOF (interval
    # propagation is necessary and sufficient), not an opinion.
    #
    # THE WET STATIONS ARE THE FOOTPRINT'S, NOT THE CENTRELINE'S.  Same
    # correction the clamp box already carries below, and for the same reason:
    # the write benches a LEVEL surface out to `footprint_half_m`, and a
    # station whose centreline stands on the crest of a bank with its shoulder
    # over the bay is over water even though `t` says it is dry.  MEASURED on
    # Fresh42 T1-portalhub-temple with its fords already bridged: 179 of the
    # 274 samples `ribbon.water_sign` still refuses are CARRIAGEWAY-BAND, on
    # stations `t` called dry.  See `route.WET_DEPENDS_ON`.
    tw0_lo, tw0_hi, tw0_off = transverse_window(fld, pts, width_m)
    if tw0_off:
        i0, lat0, x0, z0 = tw0_off[0]
        raise ValueError(
            f"{edge['id']}: the levelled footprint leaves the height field at "
            f"{len(tw0_off)} probe(s), first at station {i0} lat {lat0:+.2f} m "
            f"= ({x0:.1f}, {z0:.1f}) in patch {fld.id}. "
            f"UNREAD IS NOT CLEAR: `Field.bilinear` clamps to the patch edge, "
            f"so those probes would come back as the edge height and this "
            f"segment would be judged for water on ground nobody measured. "
            f"The field must cover the footprint, not just the line -- run "
            f"`roads/field.py request` for this seed with a window that "
            f"reaches {routemod.footprint_half_m(width_m):g} m past the "
            f"centreline.")
    # THE ABUTMENT'S LEVEL RUN IS THE DECLARED BANK, NOT A SECOND CROSSING
    # (ABUTMENT_BENCH_DEPENDS_ON): stations inside a declared abutment's
    # level run are pinned at its datum below, so the detector is asked
    # about them with the freeboard-only wetness taken off.
    abut_windows = []
    abut_released = []
    for which, node_id in (("a", edge["a"]), ("b", edge["b"])):
        node = specmod.NODES[node_id]
        if node.get("level_y") is None:
            continue
        if which == "a":
            sel = np.nonzero(s <= s[0] + BANK_LEVEL_M)[0]
        else:
            sel = np.nonzero(s >= s[-1] - BANK_LEVEL_M)[0]
        # ABUTMENT_TRIMMED_END_DEPENDS_ON: an end the terminal trim moved off
        # its node, whose bench the clamp cannot hold at the datum, is an
        # ordinary road end.
        cut = int((trim or {}).get(which, 0))
        if cut > 0 and _ABUTMENT_TRIMMED_END_MODE == "released":
            held = float(np.max(np.maximum(
                np.abs(float(node["level_y"]) - tw0_lo[sel]),
                np.abs(float(node["level_y"]) - tw0_hi[sel]))))
            if held > routemod.CUT_FILL_MAX:
                abut_released.append({
                    "node": node_id, "end": which,
                    "level_y": float(node["level_y"]),
                    "stations_trimmed": cut,
                    "worst_cut_fill_m": round(held, 3),
                    "clamp_m": routemod.CUT_FILL_MAX,
                    "depends_on": list(ABUTMENT_TRIMMED_END_DEPENDS_ON)})
                continue
        abut_windows.append((which, node_id, float(node["level_y"]),
                             int(sel.min()), int(sel.max())))
    fit1 = routemod.fit_profile(t, s, g_max)
    detected, _flag, fords = routemod.find_crossings(
        edge["id"], pts, s, t, fit1["infeasible_spans"],
        t_foot=abutment_bench_dry(tw0_lo, abut_windows))

    # Geometry second: mark the deck stations, straighten each crossing window
    # and each declared-abutment window over ranges that never overlap
    # (CROSSING_BANKS_DEPENDS_ON), then re-sample the terrain because
    # straightening moved the stations.
    free, windows = crossing_windows(pts, s, detected)
    pts, windows = straighten_windows(pts, s, windows, abut_windows)
    if windows or abut_windows:
        t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)
        # ...AND `s` IS RE-MEASURED, because `straighten` MOVED the stations.
        #
        # MEASURED, and this is a defect the plan file has been carrying:
        # `straighten` replaces a curved run with its CHORD while keeping the
        # station indices, so the real spacing inside a straightened window
        # drops below `STATION_M` and the pre-straighten `s` overstates the
        # line.  Over the six northern legs the stored `length_m` overstates
        # the actual polyline by 184.8 m -- N2 by 78.3 m (4.6 %), N3 18.1 m
        # (2.3 %), N1 47.3 m (1.5 %) -- and the overstatement scales with
        # `bridged_stations`, which is exactly what a chord-replacement
        # explanation predicts (N2 has 221 of them, N0b has 0 and is out by
        # 1.9 m).
        #
        # Two consequences, and the second is the one that matters:
        #   * every quoted length was long;
        #   * the profile was fitted with `|dy| <= grade * ds` against a ds
        #     that no longer existed, so the achieved grade inside a
        #     straightened window is steeper than the limit that was applied
        #     to it -- by up to the same ratio, concentrated precisely at the
        #     bridge approaches where the chord replacement happened.
        #
        # The verdict the built legs were accepted on is unaffected and stays
        # the authority: `ribbon.py` walks the centreline at 0.5 m using the
        # real xz positions on the APPLIED surface, so it measured true
        # distances and its WALKABLE readings (0.2592 / 0.1836 / 0.1864
        # against the 0.781 slide limit) were never based on this `s`.  What
        # was wrong was the PLAN's arithmetic, and quoting a plan length as a
        # walked distance is the same class of error as everything else
        # tonight: a number certified about one object, consumed as though it
        # were about another.
        s = np.concatenate([[0.0], np.cumsum(
            np.hypot(np.diff([p[0] for p in pts]),
                     np.diff([p[1] for p in pts])))])

    # ONE constraint set.  Every special case -- bridge deck, level bank run,
    # abutment height owned by Crossings, ordinary cut and fill -- becomes a
    # per-station [lo, hi] pair, and the profile is fitted once inside it.
    #
    # This replaced a version that fitted first and then PATCHED the levelling
    # and the grade limit on afterwards.  That version left 8 stations outside
    # the measured +/-8 m clamp on the first trunk segment, because a flat
    # insert violates the grade limit and re-imposing the grade limit violates
    # the cut/fill box: each fix broke what the other had just fixed.  Stating
    # the constraints once and solving them once cannot do that.
    CF = routemod.CUT_FILL_MAX
    # THE BOX IS THE FOOTPRINT'S, NOT THE CENTRELINE'S.  `tw_lo`/`tw_hi` are
    # the min and max GENERATED height across the level road surface at each
    # station (`transverse_window`, which carries the measurement).  The apply
    # clamp is per sample, so one profile value is writable across a station
    # only if it is within the clamp of EVERY sample under the bench:
    #     y <= min(generated) + CF     and     y >= max(generated) - CF
    # Fitting against `t` alone is how N4 came back with `stations_over_clamp:
    # 0` and 71 real breaches, all of them at lat 3.99-4.12 m.
    tw_lo, tw_hi, tw_off = transverse_window(fld, pts, width_m)
    if tw_off:
        i1, lat1, x1, z1 = tw_off[0]
        raise ValueError(
            f"{edge['id']}: the straightened footprint leaves the height "
            f"field at {len(tw_off)} probe(s), first at station {i1} lat "
            f"{lat1:+.2f} m = ({x1:.1f}, {z1:.1f}) in patch {fld.id}. "
            f"Straightening a crossing window moves stations, so a line that "
            f"was covered before it can be off the patch after -- and "
            f"`Field.bilinear` would answer with the edge height rather than "
            f"refuse.")
    # THE DETECTOR READ THE LINE BEFORE `straighten` MOVED IT, AND NOTHING
    # COMPARED THE TWO.  `find_crossings` above was handed `tw0_lo`, the
    # footprint minimum on the ORIGINAL polyline; `straighten` then replaced
    # every crossing window and every declared-abutment window with its
    # CHORD, and `tw_lo` here is the footprint minimum on the line that will
    # actually be written.  A station the chord swung out over a bay is wet
    # on the second reading and was never offered to the first, so no run
    # covers it, `ford_rule` is never asked about it, `free` is False there,
    # and the floor three lines below -- `max(tw_hi - CF, WATER_LEVEL +
    # ROAD_FREEBOARD_M)` -- lifts it to freeboard.  That is the carriageway
    # filling the sea, arrived at by a route nobody re-measured.
    #
    # It is the same shape as every other instance this session: ONE QUANTITY
    # CERTIFIED ABOUT ONE OBJECT AND CONSUMED ABOUT ANOTHER.  `WET_DEPENDS_ON`
    # fixed WHICH SAMPLES the wet test reads; it did not fix WHICH LINE.
    #
    # MEASURED over all fourteen Fresh42 segments on the final geometry: 158
    # stations stand with their levelled footprint below the 30.0 m plane and
    # 0 OF 158 ARE UNBRIDGED, so this refusal fires on nothing today and the
    # hole is LATENT.  It is closed anyway, because a latent hole that no
    # instrument can see is how the last four instances of this class shipped
    # -- and UNREAD IS NOT CLEAR: the only reason that 0 is known is that it
    # was read.
    drowned = [k for k in range(len(pts))
               if not free[k] and tw_lo[k] < WATER_LEVEL]
    if drowned:
        k0 = drowned[0]
        raise DrownedFootprint(
            f"{edge['id']}: {len(drowned)} station(s) carry a LEVELLED "
            f"FOOTPRINT below the {WATER_LEVEL:g} m plane and are NOT "
            f"bridged, first at station {k0} = ({pts[k0][0]:.1f}, "
            f"{pts[k0][1]:.1f}), footprint floor {tw_lo[k0]:.2f} m "
            f"({WATER_LEVEL - tw_lo[k0]:.2f} m under it). The crossing "
            f"detector was asked about the line BEFORE `straighten` replaced "
            f"these windows with their chords; this is the line that will be "
            f"WRITTEN. The profile floor below would lift every one of them "
            f"to {WATER_LEVEL + routemod.ROAD_FREEBOARD_M:g} m, which is the "
            f"carriageway filling the sea and is exactly what "
            f"`ribbon.water_sign` refuses after the whole rasterisation. "
            f"Re-run `find_crossings` against the straightened line, or move "
            f"the leg.", drowned, len(pts))
    lo = np.maximum(tw_hi - CF, WATER_LEVEL + routemod.ROAD_FREEBOARD_M)
    hi = tw_lo + CF
    for _c, ia, ib, _wa, _wb in windows:
        # Footprint again, for the same reason as the detector: whether the
        # deck must clear water is a fact about the bench, not about the line.
        wet = bool(routemod.footprint_wet(tw_lo[ia:ib + 1]).any())
        lo[ia:ib + 1] = (np.maximum(t[ia:ib + 1], WATER_LEVEL + routemod.DECK_CLEARANCE_M)
                         if wet else t[ia:ib + 1] + 0.5)
        hi[ia:ib + 1] = 1e6

    # A level run needs ONE height H across the whole window.  The set of legal
    # H is an interval, computed exactly: H must be within the clamp of every
    # DRY station in the window, and above deck clearance if the gap holds
    # water.  An empty interval means a single H is impossible here, which is a
    # fact Crossings needs (its deck then has a grade) rather than something to
    # average away.
    bank_level = []
    bank_lowered = bank_under_floor = 0
    for c, ia, ib, wa, wb in windows:
        dry = [k for k in range(wa, wb + 1) if not free[k]]
        wet = bool(routemod.footprint_wet(tw_lo[ia:ib + 1]).any())
        if dry:
            # Same correction as the box above: the level run is a BENCH, so
            # its single H must clear the clamp against every sample under it,
            # not against the centreline sample.
            h_lo = float(max(tw_hi[k] for k in dry)) - CF
            h_hi = float(min(tw_lo[k] for k in dry)) + CF
        else:
            # Only reachable when the window is clipped by the end of the
            # segment.  Fall back to the window's own end stations rather than
            # to an unbounded interval: an unbounded interval's midpoint is
            # zero, and a level run at y=0 is 30 m below the sea.
            h_lo = float(max(tw_hi[wa], tw_hi[wb])) - CF
            h_hi = float(min(tw_lo[wa], tw_lo[wb])) + CF
        if wet:
            h_lo = max(h_lo, WATER_LEVEL + routemod.DECK_CLEARANCE_M)
        # THE DECK STANDS AT OR ABOVE ITS OWN BANKS (BANK_BED_DEPENDS_ON).  The
        # gate's crossing check refuses a "crossing" whose own bed stands above
        # its declared deck, so H may not be chosen below the dry ground at the
        # two banks -- the stations either side of the water.  The walked-ray
        # ceiling may not push it there either: MEASURED on Fresh42 drive 10,
        # W9-x01 was ceilinged to 32.797 against banks at 33.252 and the gate
        # refused it (+0.455 m, `IS NOT A CROSSING`); the deck builder could
        # only ramp the landfall.
        h_lo = bank_bed_floor(t, dry, [k for k in range(wa, wb + 1) if free[k]],
                              h_lo, h_hi)
        # The walked-ray ceiling narrows the interval BEFORE H is chosen
        # (BANK_CEILING_DEPENDS_ON): the pin below closes these stations'
        # bounds, and the ceiling pass further down skips closed bounds.
        caps = [float(ceiling[k]) for k in range(wa, wb + 1)
                if ceiling and k in ceiling]
        ref = [t[k] for k in dry] if dry else [t[wa], t[wb]]
        h_top = h_hi
        lvl = bank_level_height(ref, h_lo, h_hi, caps)
        # THE DECK STANDS AT OR ABOVE THE GROUND THE GATE READS ON ITS OWN
        # SPAN (BANK_SPAN_BED_DEPENDS_ON): the writer's lattice at the 1 m
        # samples between the two banks this record is about to declare, not
        # the routing field beside the free run.  It binds where the height
        # chosen above stands under that floor -- then the floor takes the
        # interval's lower end and H is chosen again, ceiling and all (the
        # floor wins, as the bank floor does) -- and the clamp's top stops it
        # like the bank floor; what it cannot reach is recorded, never forced.
        span_bed = None
        if _SPAN_BED_MODE == "lattice" and lvl["H"] is not None:
            a_xz = (round(float(pts[ia][0]), 2), round(float(pts[ia][1]), 2))
            b_xz = (round(float(pts[ib][0]), 2), round(float(pts[ib][1]), 2))
            sb = span_bed_floor(a_xz, b_xz, lattice_ground(fld, a_xz, b_xz))
            floor_y = sb["floor_y"]
            if floor_y is not None and floor_y > lvl["H"] + 1e-9:
                span_bed = {**sb, "depends_on": list(BANK_SPAN_BED_DEPENDS_ON),
                            "height_before_y": round(float(lvl["H"]), 3),
                            "clipped_at_interval_top": bool(floor_y > h_top)}
                h_lo = max(h_lo, min(floor_y, h_top))
                lvl = bank_level_height(ref, h_lo, h_top, caps)
        h_hi, single, H = lvl["h_hi"], lvl["single"], lvl["H"]
        bank_lowered += lvl["lowered"]
        bank_under_floor += lvl["under_floor"]
        if single:
            lo[wa:wb + 1] = H
            hi[wa:wb + 1] = H
        ax, az = pts[wa]
        bx, bz = pts[wb]
        brg = (math.degrees(math.atan2(bx - ax, bz - az)) + 360.0) % 360.0
        c.update({
            "status": "detected", "ribbon_width_m": width_m,
            "bank_level_run_m": BANK_LEVEL_M,
            "required_deck_y": round(H, 3) if H is not None else None,
            "single_H_both_banks": bool(single),
            "legal_deck_y_interval": [round(h_lo, 3), round(h_hi, 3)],
        })
        declare_banks(c, pts, t, tw_lo, ia, ib, wa, wb)
        c["bank_a"].update({"road_deck_y": round(H, 3) if H is not None else None,
                            "bearing_deg": round(brg, 1)})
        c["bank_b"].update({"road_deck_y": round(H, 3) if H is not None else None,
                            "bearing_deg": round((brg + 180.0) % 360.0, 1)})
        c["why_terrain_failed"] = c.pop("reason", c.get("why_terrain_failed"))
        if span_bed is not None:
            c["span_bed"] = span_bed
        bank_level.append({"crossing_id": c["crossing_id"],
                           "level_at_y": c["required_deck_y"],
                           "single_H_both_banks": bool(single),
                           "legal_interval": [round(h_lo, 3), round(h_hi, 3)]})

    # A declared abutment's height is NOT mine to choose: Crossings owns the
    # deck at exactly y = 30.6 (the same datum early-dock already declares), so
    # the last 6 m of ribbon is pinned there and any conflict with the clamp is
    # reported rather than resolved by moving the deck.
    abutments = []
    for which, node_id, H, lo_i, hi_i in abut_windows:
        sel = np.arange(lo_i, hi_i + 1)
        # Over the BENCH, not the centreline: the abutment's level run is the
        # full road surface and every sample of it is clamped independently.
        worst = float(np.max(np.maximum(np.abs(H - tw_lo[sel]),
                                        np.abs(H - tw_hi[sel]))))
        lo[sel] = H
        hi[sel] = H
        k_end = 0 if which == "a" else len(pts) - 1
        k_in = hi_i if which == "a" else lo_i
        bx, bz = pts[k_in]
        ax, az = pts[k_end]
        brg = (math.degrees(math.atan2(ax - bx, az - bz)) + 360.0) % 360.0
        abutments.append({
            "node": node_id, "end": which, "level_y": H,
            "level_run_m": BANK_LEVEL_M,
            "worst_cut_fill_m": round(worst, 3),
            "within_clamp": bool(worst <= CF),
            "ribbon_width_m": width_m,
            "outbound_bearing_deg": round(brg, 2),
            "ribbon_end_xz": [round(pts[k_end][0], 2), round(pts[k_end][1], 2)],
        })

    # THE WALKED-RAY CEILING (route.WALKED_WET_DEPENDS_ON), and ONLY on the
    # stations `writer_water` found a refused ray at: never below the floor
    # already in force (freeboard, clamp, a level run), because a ceiling
    # under the floor is a ray only the ALIGNMENT can serve, and it is
    # counted rather than forced.
    ceiling_rec = None
    if ceiling:
        applied_n, under_floor = 0, 0
        for k, cap in sorted(ceiling.items()):
            if not (0 <= k < len(pts)) or free[k] or lo[k] >= hi[k]:
                continue
            if cap < lo[k]:
                under_floor += 1
            new_hi = max(min(hi[k], cap), lo[k])
            if new_hi < hi[k]:
                hi[k] = new_hi
                applied_n += 1
        ceiling_rec = {"stations_asked": len(ceiling),
                       "stations_lowered": applied_n,
                       "stations_under_floor": under_floor,
                       "bank_runs_lowered": bank_lowered,
                       "bank_runs_under_floor": bank_under_floor}

    # GRADE RELAXATION LADDER.  If the one constraint set proves no profile
    # exists at the design grade, the honest options are: relax the grade, or
    # declare the segment unbuildable.  Silently leaving samples outside the
    # clamp is not an option -- those are metres of road that will not be where
    # the plan says, because ApplyToHeightmap discards the excess.
    #
    # The ladder stops at the 25 % network ceiling, which is still less than a
    # third of the 38 deg (78 %) angle at which Character::GetSlideAngle makes a
    # player slide, so no rung of it can produce a road that dumps the operator
    # downhill.  Every relaxation is recorded per segment.
    #
    # AND THE LINE'S OWN BRANCHES ARE IN THE CONSTRAINT SET.  A switchback
    # whose two legs' benches stand closer than the slide limit can span
    # across the height between them is a route no earthwork can build -- see
    # `route.SELF_ADJACENCY_DEPENDS_ON`, which is the writer's own
    # `self_corridor` quantity lifted to plan time.  The ladder answers it the
    # way it answers terrain: re-profile (a landing at the turn, the legs
    # carrying the climb it gave up) at the lowest rung that can, or return
    # the line REFUSED with its number for `main` to reject.
    ladder = [g_max] + [g for g in (routemod.GRADE["spur"],
                                    routemod.GRADE["mountain"],
                                    routemod.GRADE_CEILING) if g > g_max]
    design_rung = g_max
    sa_grade, sa_batter, sa_lattice = writer_batter()
    fl = routemod.fit_ladder(t, s, lo, hi, free, ladder, pts=pts,
                             width_m=width_m, grade=sa_grade,
                             batter_m=sa_batter, lattice_m=sa_lattice)
    fit, g_max = fl["fit"], fl["rung"]
    relaxed_to = (ladder[-1] if not fl["terrain_feasible"]
                  else None if g_max == design_rung else g_max)
    joined = fl["joined"]
    self_adjacency = {
        "verdict": joined["verdict"],
        "depends_on": routemod.SELF_ADJACENCY_DEPENDS_ON,
        "rule": "unabsorbable = |y(foot1) - y(foot2)| - grade * (run1 + "
                "lattice + run2), over every pair of 4-adjacent lattice "
                "samples within batter reach claimed by two different feet "
                "of this line (the writer's own ribbon.lateral_and_y_grid); "
                "refused where positive",
        "grade_per_m": round(sa_grade, 4),
        "bench_half_m": routemod.footprint_half_m(width_m),
        "lattice_m": sa_lattice,
        "batter_reach_m": sa_batter,
        "candidate_lattice_edges": joined.get("candidate_edges"),
        "candidate_station_pairs": joined["candidate_pairs"],
        # `pairs_*` COUNT LATTICE EDGES: two claims of this line one lattice
        # edge apart that no earthwork can join.
        "pairs_before": joined["pairs_before"],
        "worst_before": joined["worst_before"],
        "pairs_after": joined["pairs_after"],
        "worst_after": joined["worst_after"],
        "rung": joined["rung"],
        "design_rung": joined["design_rung"],
        "terrain_rung": fl["terrain_rung"],
        "terrain_feasible": fl["terrain_feasible"],
        "stations_moved": joined["stations_moved"],
        "max_move_m": joined["max_move_m"],
        "rungs_tried": joined["rungs_tried"],
        "proof": joined.get("proof"),
        "same_quantity_as": "roads/ribbon.py self_corridor "
                            "(BATTER_TARGET_DEPENDS_ON): |y1 - y2| - "
                            "BATTER_GRADE * (run1 + run2), minimised over "
                            "every cell between the two benches",
        "tool": "roads/route.py::fit_ladder, called from "
                "roads/network.py::build_segment",
    }
    y = fl["y"]
    terr = ~free
    stats = profile_stats(y, s, t, tw_lo, tw_hi, free)
    relief = (tw_hi - tw_lo)[terr]
    relief_infeasible = int((relief > 2.0 * routemod.CLAMP_M).sum())
    if joint is not None:
        joint.update({"s": np.asarray(s, dtype=np.float64), "t": t,
                      "lo": lo, "hi": hi, "free": free,
                      "tw_lo": tw_lo, "tw_hi": tw_hi,
                      "spans": [tuple(v) for v in fit["infeasible_spans"]],
                      "cand": fl["cand"], "rung": float(g_max),
                      "ladder": [float(g) for g in ladder],
                      "design_rung": float(design_rung),
                      "terrain_rung": fl["terrain_rung"],
                      "terrain_feasible": fl["terrain_feasible"],
                      "y0": np.asarray(y, dtype=np.float64)})

    # Second-pass infeasibility: a span the ONE constraint set could not satisfy
    # is terrain that cannot be built, and it goes to Crossings too.  Reported
    # separately so it is obvious these were found by the final solve rather
    # than by the wet test.
    residual = []
    for a, b in fit["infeasible_spans"]:
        if free[a:b + 1].all():
            continue
        residual.append({
            "segment_id": edge["id"], "stations": [int(a), int(b)],
            "xz_from": [round(pts[a][0], 2), round(pts[a][1], 2)],
            "xz_to": [round(pts[b][0], 2), round(pts[b][1], 2)],
            "length_m": round(float(s[b] - s[a]), 1),
            "why": "no slope-limited profile exists inside the +/-8 m apply clamp "
                   "here, PROVEN by interval propagation",
        })

    # Where did the route actually end relative to what was asked for?  The
    # corridor snaps endpoints out of water and out of POI keep-outs, and
    # `enforce_pad_edge` has just trimmed it back to the pad, so the stand-off
    # is a RESULT -- but it is no longer ONLY reported.  `endpoint_standoff_m`
    # is the raw distance, kept for continuity; `pad_edge` beside it carries
    # what was REQUIRED, what was ACHIEVED and how many stations were removed
    # to achieve it, and `enforce_pad_edge` has already raised rather than
    # returned when it could not be achieved.  A number with no test beside it
    # is how this rule sat unapplied in prose for the whole build.
    def standoff(node_id, p):
        nx, nz = specmod.NODES[node_id]["xz"]
        return round(math.hypot(p[0] - nx, p[1] - nz), 1)

    near = poimod.near_centreline(pois, pts, half_w)
    violations = [r for r in near if r["violates_hard"]]

    rec = {
        "id": edge["id"], "cls": cls, "pri": edge["pri"],
        "island": a_node["island"], "patch": a_node["patch"],
        "from": edge["a"], "to": edge["b"],
        "why": edge.get("why", ""),
        "width_m": width_m, "station_m": STATION_M,
        "length_m": round(float(s[-1]), 1),
        "endpoint_standoff_m": {edge["a"]: standoff(edge["a"], pts[0]),
                                edge["b"]: standoff(edge["b"], pts[-1])},
        "pad_edge": {
            "required": pad["required"],
            "achieved_m": pad["achieved_m"],
            "stations_trimmed": pad["trimmed"],
            "interior_violations": pad["interior_violations"],
            "enforced_by": "network.enforce_pad_edge, before the profile fit",
            # ARRIVAL_DEPENDS_ON: how far each end was carried onto its pad,
            # and what stopped it short if anything did.
            "arrival": arrival,
        },
        "census": ({"verdict": "PROVISIONAL",
                    "why": "routed with NO census: this line exists only so "
                           "`precensus.py survey` knows where to read. It "
                           "cannot be paved -- ribbon.py refuses a "
                           "PROVISIONAL segment.",
                    "instrument": None, "measured_utc": None}
                   if census is None else
                   {**censusmod.coverage_gate(pts, census),
                    "bodies_in_census": len(census.bodies),
                    "bodies_blocking": sum(1 for b in census.bodies
                                           if b.blocking),
                    "near_centreline": poimod.near_centreline(
                        census.pois(), pts, half_w)[:12]}),
        "grade_limit": g_max,
        "grade_limit_design": routemod.GRADE[cls],
        "grade_relaxed_to": relaxed_to,
        # WHICH CONSTRAINT CLIMBED THE LADDER.  A rung taken for the line's
        # own branches is a different fact from one taken for the ground, and
        # a reader of `grade_relaxed_to` alone cannot tell them apart.
        "grade_relaxed_by": (None if relaxed_to is None
                             else "terrain" if fl["terrain_rung"] in
                             (None, g_max)
                             else "self_adjacency"),
        "self_adjacency": self_adjacency,
        **stats,
        "clamp_measured_over": "the LEVEL road footprint, +/-"
                               f"{routemod.footprint_half_m(width_m):g} m of "
                               "the centreline, sampled at 0.5 m "
                               "(network.transverse_window)",
        "transverse_relief_max_m": round(float(relief.max()), 2) if relief.size else 0.0,
        "stations_relief_past_2x_clamp": relief_infeasible,
        "design_margin_m": CF,
        "grade_limit_is_override": bool(edge.get("grade_max")),
        "terrain_stations": int(terr.sum()),
        "fords": fords,
        "ford_count": len(fords),
        "bridged_stations": int(free.sum()),
        "abutments": abutments,
        "bank_level": bank_level,
        "crossings": detected,
        "residual_infeasible": residual,
        "poi_within_keepout": near[:12],
        "poi_hard_violations": violations,
        "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
        "profile_y": [round(float(v), 3) for v in y],
        "terrain_y": [round(float(v), 3) for v in t],
        "is_bridge": [bool(v) for v in free],
    }
    if ceiling_rec is not None:
        rec["water_ceiling"] = ceiling_rec
    if trim_rec is not None:
        rec["water_trim"] = trim_rec
    if abut_released:
        rec["abutments_released"] = abut_released
    if terminal is not None:
        # TERMINAL_REACH_DEPENDS_ON: the end the search re-snapped out of a
        # pocket only keep-outs close, and how far from its node it stands.
        rec["terminal_reach"] = {
            "node": edge["b"] if terminal["end"] == "goal" else edge["a"],
            **{k: v for k, v in terminal.items() if k != "cell"}}
    return rec


def write_gate(seg: dict, pois: list, seed: str) -> dict:
    """WOULD `ribbon.py::location_check` REFUSE THIS LEG -- asked at PLAN time.

    `poi_hard_violations` has been REPORTED since this file was written and
    has never refused anything: `main` printed a count and moved on.  So a leg
    whose carriageway stands inside a generated location's stand-off left the
    router looking planned, and the refusal arrived at `location_check` --
    after zone generation, a full corridor census, a clearance pass and a
    whole carriageway rasterisation.

    MEASURED cost of that ordering, Fresh42 2026-09-24: `S7-stathub-wtnorth`
    reached step 4 of the build and exited 3, its own earthwork CLEAN (rim
    transfer 0 caused, 0 pre-existing), refused on ONE carriageway sample
    18.2 m from a `ShipSetting01` needing 25.0 m.  The refusal's own remedy
    is "Re-route the leg", which is this module's job and nobody else's.  A
    guard that only speaks after hours of work is a guard nobody can afford
    to run -- which is why `buildstage.py` had to grow `--resume` in the same
    week.

    THE POPULATION IS THE GENERATOR DUMP FOR THIS SEED, ALONE, because that
    is the population `location_check` reads.  Census bodies and
    `mod_keepout.json` rows ride in `poi_within_keepout` and in the census
    block; the writer cannot see them, and a prediction that folded them in
    would refuse legs the writer accepts.  The dump is resolved through
    `poi.location_dump` -- see `poi.LOCATION_DUMP_DEPENDS_ON`, and the reason
    it is a named dependency rather than a path.

    `poi.gate_check` is the writer's own boundary restated on the centreline,
    NOT a second opinion: `declared_m + TERRAIN_SPREAD_M +
    level_half_width_m`, measured perpendicular to the POLYLINE.  It is
    conservative by at most one 1 m sample pitch and says so, so a violation
    inside that band is `marginal` and does not refuse: the lattice decides
    those and `location_check` remains the thing that decides them.

    Recorded whether or not it refuses, because "this leg clears every
    generated location" is a claim and this is the measurement behind it.
    """
    wg = poimod.gate_check([(n[0], n[1]) for n in seg["nodes"]],
                           seg["width_m"] / 2.0, pois)
    return {
        "verdict": wg["verdict"],
        "firm_violations": wg["firm_violations"],
        "marginal_violations": wg["marginal_violations"],
        "instances": wg["instances"],
        "level_half_width_m": wg["level_half_width_m"],
        "boundary": wg["boundary"],
        "same_gate_as": wg["same_gate_as"],
        "dump": str(poimod.location_dump(seed)),
        "dump_depends_on": poimod.LOCATION_DUMP_DEPENDS_ON,
        "population": "the generator dump for this seed ALONE -- the "
                      "population ribbon.py::location_check reads",
        "tool": "roads/poi.py::gate_check, called from "
                "roads/network.py::write_gate",
    }


def write_gate_rejection(seg: dict) -> dict | None:
    """The refusal a firm write-gate breach earns, WITH ITS NUMBER, or None.

    A refusal a builder cannot act on is how `FileNotFoundError [Errno 2]`
    made borrowing another world's field the cheapest way forward, so this
    one names the instance, where it stands, how far off the centreline is
    and how far off it had to be.
    """
    wg = seg.get("write_gate") or {}
    firm = wg.get("firm_violations") or []
    if not firm:
        return None
    worst = firm[0]
    return {
        "why": f"the centreline stands {worst['centreline_dist_m']} m from "
               f"{worst['name']} at {worst['xz']}, which needs "
               f"{worst['required_m']} m -- short by {worst['short_by_m']} m, "
               f"past the {poimod.LATTICE_PITCH_M} m sample pitch this "
               f"restatement is conservative by. {len(firm)} firm / "
               f"{wg.get('marginal_violations')} marginal over "
               f"{wg.get('instances')} generator instances. "
               f"ribbon.py::location_check WILL refuse this alignment after "
               f"the whole rasterisation, and its own remedy is to re-route "
               f"this leg. Do not widen the stand-off: moving a location's "
               f"ground floats its pieces.",
        "write_gate_short_by_m": worst["short_by_m"],
        "write_gate_binding": worst,
    }


def self_adjacency_rejection(seg: dict) -> dict | None:
    """The refusal a line whose own branches cannot be joined earns, WITH
    ITS NUMBER, or None.

    A leg refused here costs nothing; the same leg refused at step 4 costs a
    zone generation, a corridor census, a clearance pass and a whole
    rasterisation -- MEASURED on Fresh42, five of fourteen segments reached
    `ribbon.py`'s rim gate and were refused there for exactly this, after
    five earthwork reconciliations had each made it worse.  So the number
    names the two lattice samples, the two stations whose feet claim them,
    their design heights, how far past each bench the samples stand, the arc
    between the feet and the metres no earthwork can absorb, and the proof
    names the rung the ladder ran out at.
    """
    sa = seg.get("self_adjacency") or {}
    if sa.get("verdict") != "REFUSED":
        return None
    w = sa.get("worst_before") or {}
    pr = sa.get("proof") or {}
    tried = sa.get("rungs_tried") or []
    return {
        "why": f"{sa.get('pairs_before')} lattice edge(s) between two claims "
               f"of this line's own carriageway stand out of reach of each "
               f"other; worst between samples {w.get('cells')}, claimed from "
               f"stations {w.get('stations')} at {w.get('xz')} with "
               f"{w.get('arc_between_m')} m of line between the feet, design "
               f"heights {w.get('design_y')} ({w.get('height_difference_m')} "
               f"m apart), each sample {w.get('run_past_bench_m')} m past its "
               f"own bench: the slide limit spans {w.get('allowance_m')} m of "
               f"that, so {w.get('unabsorbable_m')} m is UNABSORBABLE by any "
               f"earthwork. No profile inside the +/-"
               f"{routemod.CUT_FILL_MAX:g} m design box joins them at any "
               f"rung tried ({[r['rung'] for r in tried]})"
               + (f"; at the last, station {pr.get('first_empty_station')}'s "
                  f"interval is empty {pr.get('interval_there')} across "
                  f"{pr.get('empty_stations')} station(s)" if pr else "")
               + ". ribbon.py's rim gate WILL refuse this line after the "
                 "whole rasterisation; re-route the leg so its switchback "
                 "legs stand apart.",
        "self_adjacency_unabsorbable_m": w.get("unabsorbable_m"),
        "self_adjacency_binding": w,
    }


def plan_refusal(seg: dict, gen_pois: list, seed: str, *, fld,
                 coverage: bool = True) -> str | None:
    """Which of `main`'s per-leg plan-time refusals this leg would earn, by
    name, or None -- so a remedy is never adopted that `main` would then
    refuse for something else.  `coverage=False` is the band-free probe's
    question (CENSUS_BAND_DEPENDS_ON): every refusal EXCEPT unread ground."""
    if max((c["span_m"] for c in seg["crossings"]), default=0.0) \
            > MAX_BRIDGE_SPAN_M:
        return "span"
    if terminal_landing_rejection(seg, fld) is not None:
        return "terminal_landing"
    if coverage and (seg.get("census") or {}).get("verdict") == "UNCENSUSED":
        return "uncensused"
    if write_gate_rejection({**seg, "write_gate": write_gate(
            seg, gen_pois, seed)}) is not None:
        return "write_gate"
    if self_adjacency_rejection(seg) is not None:
        return "self_adjacency"
    return None


# ---------------------------------------------------------------------------
# SPAN_REPRICE_DEPENDS_ON: A ROUTE THE SPAN CEILING REFUSES IS A ROUTE TO
# RE-PRICE, NOT PROOF THAT THE LEG HAS NO LAND ROUTE.
#
# THE DEFECT, MEASURED on the regenerated Ulfsland (seed Pirate68), W10-brgs1-
# treenear and W9-vestvik-lhwest, "UNROUTABLE: no route found" -- band-free
# too, so not the census's.  The committed plan routes both.
#
# THE CAUSE.  `water_forbidden` encodes the 96 m span ceiling as "no water
# cell further than 48 m from land", which is exact on the 4 m cell grid and
# loose on the 1 m field the crossing is MEASURED on: W10's first route bridges
# 84 m of cells and MEASURES 96.37 m (bank_a is the declared abutment
# brg-s1-w, station 0), 0.37 m past the ceiling.  The retry that follows the
# measurement forbids ALL water -- and a leg that needs even one honest bridge
# (this one needs 65.75 m; the committed plan's W10 has an 89.44 m one) has no
# land-only route at all, so the retry's "no route found" reads as a proof
# when it is a consequence of the retry.  The comment on it says as much ("if
# none exists the rejection is a proof rather than a tuning artefact"); it
# is a proof only of the land-only question.
#
# THE RULE.  When the land-only answer does not exist, ask the SAME search
# with water dearer, ladder SPAN_REPRICE_FACTORS times
# `Corridor.bridge_per_m_cost`, and take the first rung whose finished leg
# MEASURES inside the ceiling.  Water stays traversable at every rung, so a
# crossing the ceiling permits is never lost to the retry; the price rising
# without bound drives the search to the fewest bridged metres a route can
# have, so when the top rung still measures past the ceiling the leg is
# refused with that measured minimum -- a proof about bridges, which is the
# question the ceiling asks.
#   the_measured_span_past_the_ceiling  the trigger is the MEASURED span
#       (find_crossings on the 1 m field), not the cell run the search sees
#   water_dearer_not_forbidden          the price is the only thing that moves
#   the_land_only_answer_first          a leg that has a land route keeps it:
#       the shipped retry runs first and this runs only where it raised
# NEUTER `_SPAN_REPRICE_MODE = "land_only"`: the shipped retry, its "no route
# found" standing as the leg's refusal.  Proven by `selftest_span_reprice`.
# ---------------------------------------------------------------------------
SPAN_REPRICE_DEPENDS_ON = ("the_measured_span_past_the_ceiling",
                           "water_dearer_not_forbidden",
                           "the_land_only_answer_first")
_SPAN_REPRICE_MODE = "reprice"          # reprice | land_only (the shipped)
SPAN_REPRICE_FACTORS = (2.0, 4.0, 16.0, 64.0)


def span_worst(rec: dict) -> float:
    """The longest bridged run this leg MEASURES, in metres."""
    return max((c["span_m"] for c in rec["crossings"]), default=0.0)


def at_water_price(corr: routemod.Corridor, factor: float | None, fn):
    """`fn()` with the corridor's bridged-metre price times `factor` (None:
    as it stands), put back whatever `fn` does."""
    if factor is None:
        return fn()
    base = corr.bridge_per_m_cost
    corr.bridge_per_m_cost = base * factor
    try:
        return fn()
    finally:
        corr.bridge_per_m_cost = base


def span_reprice(corr: routemod.Corridor, attempt, *, first_worst: float,
                 land_exc: ValueError) -> tuple[tuple, dict]:
    """SPAN_REPRICE_DEPENDS_ON.  `attempt()` builds the leg on `corr` as it
    stands and returns a tuple whose first item is the leg's record; `land_
    exc` is what the land-only retry raised.  Returns that tuple for the
    first rung that measures inside the ceiling, with the record of the
    ladder; raises ValueError, naming the measured minimum, when none does."""
    if _SPAN_REPRICE_MODE == "land_only":
        raise land_exc
    rungs: list[dict] = []
    for f in SPAN_REPRICE_FACTORS:
        try:
            out = at_water_price(corr, f, attempt)
        except DrownedFootprint:
            raise       # the drowned-terminus trim's, not a refused rung
        except ValueError as exc:
            rungs.append({"factor": f, "refused": str(exc)[:200]})
            continue
        worst = span_worst(out[0])
        rungs.append({"factor": f, "worst_span_m": worst,
                      "length_m": out[0]["length_m"]})
        if worst <= MAX_BRIDGE_SPAN_M:
            return out, {
                "depends_on": list(SPAN_REPRICE_DEPENDS_ON), "factor": f,
                "first_attempt_worst_span_m": first_worst,
                "land_only": str(land_exc)[:160], "rungs": rungs,
                "why": "first route proposed a bridged run past Crossings' "
                       f"{MAX_BRIDGE_SPAN_M:g} m absolute span ceiling and no "
                       "land-only route exists; re-routed with bridged metres "
                       f"priced x{f:g}"}
    measured = [r["worst_span_m"] for r in rungs if "worst_span_m" in r]
    raise ValueError(
        f"no route found inside the {MAX_BRIDGE_SPAN_M:g} m span ceiling: the "
        f"first route bridges {first_worst:.1f} m, the land-only retry found "
        f"none ({str(land_exc)[:80]}), and bridged metres priced up to "
        f"x{SPAN_REPRICE_FACTORS[-1]:g} "
        + (f"still bridge {min(measured):.1f} m at the least"
           if measured else "are refused: "
           + "; ".join(r["refused"][:80] for r in rungs[-1:]))
        + "; key = network.SPAN_REPRICE_DEPENDS_ON")


# ---------------------------------------------------------------------------
# ADJACENCY_REROUTE_DEPENDS_ON: A LINE WHOSE OWN BRANCHES NO EARTHWORK CAN
# JOIN IS A LINE TO RE-ROUTE, AND `self_adjacency_rejection` SAYS SO.
#
# THE DEFECT, MEASURED on the regenerated Ulfsland (seed Pirate68),
# S14-eastlobe-ferryeast: the confined search found nothing, and the band-free
# probe's line (760.9 m, no bridge) was "REFUSED self_adjacency" -- 68 lattice
# edges between stations 362 and 370, 15.69 m of line and 2.82 m of design
# height apart, 6.2 m apart on the ground, 2.04 m UNABSORBABLE at every rung
# to the 25 % ceiling.  The refusal text ends "re-route the leg so its
# switchback legs stand apart" and NOTHING DID: A* prices a corridor cell, not
# the lattice two of its own legs share, so on a hillside it takes a hairpin
# whose branches stand closer than the earthwork can reconcile, the leg is
# refused, and the census, the joint solve and the report all read the leg as
# one with no route.  W1-brgs1-wmid (558 edges) and the rest of the self-
# adjacency refusals are the same shape.
#
# THE RULE.  A leg whose finished line is REFUSED by its own branches is
# searched again with a DISC THE SEARCH MAY NOT ENTER laid on the later foot of
# the worst pair the refusal names, radius ADJACENCY_DISC_M, one
# ADJACENCY_DISC_GROW_M wider per round, the discs of earlier rounds kept;
# the first round whose line is not refused is the leg.  A leg the first
# search leaves clear is not touched (the extra search is not made), and a leg
# no round joins -- or whose re-search raises -- is returned EXACTLY AS ROUTED,
# so the repair can only turn a refusal into a leg, never a leg into another
# refusal.  Every later gate (write gate, water, joint datum, census) judges
# the re-routed line as it would any other.
#   the_worst_pairs_later_foot       the station of the pair the refusal names
#   a_disc_the_search_may_not_enter  a mask over the corridor, not a penalty
#   the_leg_as_routed_when_no_round_joins  nothing is lost by asking
# NEUTER `_ADJACENCY_REROUTE_MODE = "off"`: the shipped plan, the refused line
# standing.  Proven by `selftest_adjacency_reroute`.
# ---------------------------------------------------------------------------
ADJACENCY_REROUTE_DEPENDS_ON = ("the_worst_pairs_later_foot",
                                "a_disc_the_search_may_not_enter",
                                "the_leg_as_routed_when_no_round_joins")
_ADJACENCY_REROUTE_MODE = "reroute"     # reroute | off (the shipped plan)
ADJACENCY_DISC_M = 8.0                  # two corridor cells: taste pick
ADJACENCY_DISC_GROW_M = 2.0             # one more half-cell per round
ADJACENCY_ROUNDS = 8


def adjacency_mask(corr: routemod.Corridor, discs) -> np.ndarray:
    """The corridor cells whose centre lies inside any `(x, z, r)` disc."""
    out = np.zeros((corr.m, corr.m), dtype=bool)
    for x, z, r in discs:
        j0 = max(0, int(math.floor((x - r - corr.x0) / corr.cell_m)))
        j1 = min(corr.m - 1, int(math.ceil((x + r - corr.x0) / corr.cell_m)))
        i0 = max(0, int(math.floor((z - r - corr.z0) / corr.cell_m)))
        i1 = min(corr.m - 1, int(math.ceil((z + r - corr.z0) / corr.cell_m)))
        if j0 > j1 or i0 > i1:
            continue
        ii, jj = np.mgrid[i0:i1 + 1, j0:j1 + 1]
        out[i0:i1 + 1, j0:j1 + 1] |= (
            np.hypot(corr.x0 + jj * corr.cell_m - x,
                     corr.z0 + ii * corr.cell_m - z) <= r)
    return out


def adjacency_reroute(corr: routemod.Corridor, one) -> tuple[dict, dict]:
    """ADJACENCY_REROUTE_DEPENDS_ON.  `one(extra_forbid)` builds the leg on
    `corr` with that mask laid over the search (None: none) and returns
    `(record, joint)`."""
    rec, joint = one(None)
    if (_ADJACENCY_REROUTE_MODE == "off"
            or self_adjacency_rejection(rec) is None):
        return rec, joint
    original, last, discs = (rec, joint), rec, []
    for k in range(ADJACENCY_ROUNDS):
        worst = (last["self_adjacency"].get("worst_before") or {}).get("xz")
        if not worst or len(worst) < 2:
            return original
        discs.append([float(worst[1][0]), float(worst[1][1]),
                      ADJACENCY_DISC_M + k * ADJACENCY_DISC_GROW_M])
        try:
            cand, cj = one(adjacency_mask(corr, discs))
        except ValueError:
            return original
        if self_adjacency_rejection(cand) is None:
            cand["adjacency_reroute"] = {
                "depends_on": list(ADJACENCY_REROUTE_DEPENDS_ON),
                "rounds": k + 1, "discs": [list(d) for d in discs],
                "as_routed_length_m": original[0]["length_m"],
                "why": "the line as first routed was refused for its own "
                       "branches; re-searched with a disc the search may not "
                       "enter on the later foot of the worst pair"}
            return cand, cj
        last = cand
    return original


def water_route(fld, corr: routemod.Corridor, seg: dict, joint: dict, *,
                build, refusal) -> tuple[dict, dict]:
    """Route this leg so the writer need not refuse its batter into water.

    ASKED OF THE WRITER FIRST (`writer_water`).  A leg the writer would lay
    is returned untouched, so no corridor that lays is moved to buy one that
    does not.  A refused one is offered, in this order and each on top of
    the best so far:

      1. the ALIGNMENT -- the stand-off `route.water_standoff_forbid` sizes
         from each refused ray's own reach, grown round by round over every
         body it refused;
      2. a TERMINAL TRIM where the refusal stands at an end -- the cap the
         ribbon clamps past the last station paves what no station samples;
      3. a profile CEILING on the stations whose walked rays are still
         refused, never under the floor already in force.

    Every candidate is re-asked of the writer and of `refusal`; the one with
    the fewest water-owned steps and drains wins, ties to the earlier (the
    as-routed line first).  Then, whether or not it was refused, the winner
    is offered 4. the TERMINAL CROSSING trim (`trim_terminal_crossing`),
    kept on a tie.  The verdict, what was tried and what won ride on the
    record as `writer_water`.

    `build(extra_forbid=, trim=, ceiling=)` returns `(record, joint)` for
    this leg (`build_segment`, in `main`) and raises ValueError where no line
    or profile exists.  Every candidate RE-SEARCHES with the same inputs plus
    its remedy, so a trim or a ceiling on the as-routed line is that line and
    its arcs to the station -- never a re-measured copy.  `refusal(record)`
    names the plan-time refusal a candidate would earn, or None
    (`plan_refusal`, in `main`)."""
    grade, reach, _lattice = writer_batter()
    half = routemod.footprint_half_m(float(seg["width_m"]))
    ww0 = writer_water(seg, fld)
    best = {"seg": seg, "joint": joint, "ww": ww0, "by": "as routed",
            "kw": {}}
    tried: list[dict] = []

    def attempt(kw: dict, by: str) -> tuple[dict, dict] | None:
        try:
            cand, j = build(**kw)
        except ValueError as exc:
            tried.append({"by": by, "unbuildable": str(exc)[:200]})
            return None
        why = refusal(cand)
        if why is not None:
            tried.append({"by": by, "refused_by_plan": why,
                          "length_m": cand["length_m"]})
            return None
        cw = writer_water(cand, fld)
        tried.append({"by": by, "steps": cw["steps"],
                      "drained": cw["drained"], "worst_m": cw["worst_m"],
                      "length_m": cand["length_m"]})
        if _water_score(cw) < _water_score(best["ww"]):
            best.update(seg=cand, joint=j, ww=cw, by=by, kw=kw)
        return cand, cw

    # 1. THE ALIGNMENT: off the water's refused bodies and the refused POI
    #    discs (WALKED_KEEPOUT_DEPENDS_ON) alike.
    if _water_score(ww0) and _WET_REMEDY == "alignment":
        extra, cur = None, ww0
        for rnd in range(1, WATER_ALIGN_ROUNDS + 1):
            m = routemod.water_standoff_forbid(corr, cur["_seeds"],
                                               half_m=half, grade=grade,
                                               reach_m=reach)
            m |= keepout_standoff_forbid(corr, cur.get("_keepout") or (),
                                         half_m=half, grade=grade,
                                         reach_m=reach)
            extra = m if extra is None else (extra | m)
            got = attempt({"extra_forbid": extra}, f"alignment {rnd}")
            if got is None or not _water_score(got[1]) or not (
                    got[1]["_seeds"] or got[1].get("_keepout")):
                break
            cur = got[1]

    def on_top(by: str) -> str:
        return by if base_by == "as routed" else f"{base_by} + {by}"

    # 2. THE TERMINAL TRIM, at an end the refusal stands beside.
    base_by = best["by"]
    if _water_score(best["ww"]):
        nodes = best["seg"]["nodes"]
        for end, (ex, ez) in (("a", nodes[0]), ("b", nodes[-1])):
            if not any(math.hypot(x - ex, z - ez) <= half + reach
                       for x, z, _y in best["ww"]["_seeds"]):
                continue
            for n in WATER_TRIM_STATIONS:
                kw = dict(best["kw"])
                kw["trim"] = {**(kw.get("trim") or {}), end: n}
                got = attempt(kw, on_top(f"trim {kw['trim']}"))
                if got is not None and not any(
                        math.hypot(x - ex, z - ez) <= half + reach
                        for x, z, _y in got[1]["_seeds"]):
                    break
    # 3. THE CEILING, on the walked rays still refused.
    if _water_score(best["ww"]):
        base_kw, base_by = dict(best["kw"]), best["by"]
        ceiling: dict[int, float] = {}
        cur_seg, cur_ww = best["seg"], best["ww"]
        for rnd in range(1, WATER_CEILING_ROUNDS + 1):
            if not cur_ww["_drops"]:
                break
            for k, d in cur_ww["_drops"].items():
                ceiling[k] = min(ceiling.get(k, math.inf),
                                 float(cur_seg["profile_y"][k]) - d)
            got = attempt({**base_kw, "ceiling": dict(ceiling)},
                          on_top(f"ceiling {rnd}"))
            if got is None or not _water_score(got[1]):
                break
            cur_seg, cur_ww = got
    # 4. THE TERMINAL CROSSING, on the winner: back to the bench before a
    #    free run that reaches its end, where the trimmed line is no worse
    #    (TERMINAL_CROSSING_TRIM_DEPENDS_ON).
    t_rec, t_j, t_ww, t_kw, t_note = trim_terminal_crossing(
        best["seg"], best["kw"], best["ww"], build=build, refusal=refusal,
        water=lambda cand: writer_water(cand, fld))
    if t_rec is not None:
        tried.append({"by": f"{best['by']} + terminal trim",
                      "steps": t_ww["steps"], "drained": t_ww["drained"],
                      "worst_m": t_ww["worst_m"],
                      "length_m": t_rec["length_m"]})
        best.update(seg=t_rec, joint=t_j, ww=t_ww, kw=t_kw,
                    by=f"{best['by']} + terminal trim")
    out = best["seg"]
    if best["ww"].get("collar") is False:
        out["connection_collar"] = False
    if t_note is not None:
        out["terminal_crossing_trim"] = t_note
    out["writer_water"] = {
        **water_public(best["ww"]),
        "depends_on": list(routemod.WALKED_WET_DEPENDS_ON),
        "as_routed": {k: ww0[k] for k in ("verdict", "steps", "drained",
                                          "worst_m", "worst_at")}
                     | {"length_m": seg["length_m"]},
        "remedy": best["by"],
        "remedy_order": ("alignment (route.WATER_STANDOFF_DEPENDS_ON), then "
                         "a terminal trim, then a ceiling on the walked rays "
                         "still refused" if _WET_REMEDY == "alignment"
                         else "terminal trim and ceiling only -- the NEUTER"),
        "tried": tried,
    }
    out["writer_water"].update(judged_public(best["ww"]))
    if _water_score(best["ww"]):
        out["writer_water"]["residual_owner"] = water_owner(
            out, best["ww"], half + reach)
    return out, best["joint"]


def final_verdict(fin: dict) -> str:
    """A leg's verdict for the plan's FINAL decision: the reading that owes the
    planned pads their own cliffs when the record carries one that differs
    (PLANNED_PAD_CLIFF_DEPENDS_ON), else the verdict the search scored."""
    return fin.get("judged_verdict", fin["verdict"])


def water_rejection(seg: dict, *, final: bool = False) -> dict | None:
    """The refusal a leg the writer would still refuse on water earns, WITH
    ITS NUMBER AND ITS OWNER, or None.  `final` asks the leg's final reading
    (`final_verdict`); the plan's earlier gates ask the search's."""
    w = seg.get("writer_water") or {}
    fin = w.get("after_junction") or w
    if (final_verdict(fin) if final else fin.get("verdict")) != "REFUSED":
        return None
    if final:
        fin = {**fin, "steps": fin.get("judged_steps", fin["steps"])}
    return {
        "why": f"the writer would refuse this leg's finished geometry: "
               f"{fin['steps']} rim step(s), including "
               f"{fin.get('keepout_steps', 0)} POI and "
               f"{fin.get('pad_steps', 0)} pad-boundary step(s); "
               f"approach defects {fin.get('arrival_failures', [])}; "
               f"{fin['drained']} seed-submerged sample(s) "
               f"lifted out of the water, worst {fin['worst_m']} m at "
               f"{fin['worst_at']}, after {w.get('remedy')}. It is refused "
               f"HERE, not at step 4 (route.WALKED_WET_DEPENDS_ON). Owner of "
               f"what is left: {w.get('residual_owner')}.",
        "writer_water_steps": fin["steps"],
        "writer_water_drained": fin["drained"],
        "writer_water_owner": w.get("residual_owner"),
        "writer_arrival_failures": fin.get("arrival_failures", []),
        "writer_water_tried": w.get("tried"),
    }


# ---------------------------------------------------------------------------
# CENSUS_BAND_DEPENDS_ON: WHERE THE CENSUS MUST HAVE READ, derived from the
# lines this plan WRITES -- never from the provisional line alone.
#
# `precensus.BAND_HALF_M` was derived as "batter 10.5 m + a 3.93 m remedy
# shift inside 16 m" about the line the band was drawn on, and the router's
# own displacement from that line was a second number chosen by nobody: the
# census-confined search may take ANY read cell.  Two independent numbers
# with no dependency between them, MEASURED on Fresh42 drive 3 (stage 11,
# 2026-09-26):
#   * written lines stood up to 22.6 m (S5) and 24.3 m (S8) off the lines
#     their bands were read about -- 20 cells within 16 m of five written
#     lines were never read;
#   * four legs the confined search could not serve at all (S2, S3, S9, W9:
#     "no route found", printed with a network-wide "re-survey at 32.0 m"
#     for a body on S3) route BAND-FREE over 22 / 19 / 54 / 89 unread band
#     cells, and nothing named those cells.
#
# THE DEPENDENCY, DECLARED.  The cells a census must hold are the band --
# at the half the census was read at -- about:
#   every_line_this_plan_writes              each routed leg's final line;
#   every_line_it_would_write_were_the_ground_read
#                                            each leg refused for want of
#                                            read ground (unroutable inside
#                                            the band, an uncensused station,
#                                            water the band would not let the
#                                            alignment leave), routed with the
#                                            confinement OFF and the bodies
#                                            the census found still kept out
#                                            (`band_free_probe`), when that
#                                            line passes every OTHER refusal.
# Their unread cells are the plan's `census_wanted`, per leg, and
# `precensus.py survey --wanted <this plan>` reads exactly them.  The
# pipeline re-routes after the reading (pipeline.CENSUS_LOOP_DEPENDS_ON); a
# leg still wanting ground when the rounds run out is left refused with the
# cells named.  A leg the probe cannot serve either is not the census's: its
# refusal stands with its own owner and nothing is read for it.
# ---------------------------------------------------------------------------
CENSUS_BAND_DEPENDS_ON = ("the_band_half_the_census_was_read_at",
                          "every_line_this_plan_writes",
                          "every_line_it_would_write_were_the_ground_read")
# Swapped out by the selftest neuters: "declared" | "off" (the shipped plan:
# nothing named, the band-limited leg refused with a network-wide width) |
# "unfiltered" (WRONG: the whole band wanted, read or not).
_WANTED_MODE = "declared"


def band_deficit(nodes, census: censusmod.Census) -> list[tuple[int, int]]:
    """The cells within the census's own band half of `nodes` that it did
    not read (CENSUS_BAND_DEPENDS_ON)."""
    if _WANTED_MODE == "off":
        return []
    band = censusmod.cells_for_band(nodes, census.band_half_m)
    if _WANTED_MODE == "unfiltered":
        return band
    return [c for c in band if not census.has_cell(c)]


# PROBE_PAD_DEPENDS_ON: A PAD-BOUNDARY STEP IS THE JOINT SOLVE'S TO CLOSE, NOT
# A REASON THE GROUND IS NOT THE CENSUS'S.  The band-free probe judges the
# leg BEFORE the joint solve, at the profile the terrain alone fits; the pad
# datums enter in the joint solve (JUNCTION_DATUM_DEPENDS_ON `pad_datums`).
# A probe refused ONLY on pad-boundary steps therefore said "not the
# census's" and named no cells, while the confined search found "no route"
# over unread ground: a fixed point that lost S8-wtsouth-treesth (stage-9
# plan CLEAR after alignment 1; stage 11 confined "no route found", probe
# 10 pad steps, 1.289 m, wt-south) and with it wt-south's connection, in
# Fresh42 drive 7.  A probe whose ONLY refusal is pad steps is SERVABLE;
# the joint solve and the after-joint water pass (WATER_REREMEDY) still
# refuse the leg if the pad steps survive them.  NEUTER
# `_PROBE_PAD_MODE = "refuse"`: any writer_water refusal ends the probe.
# Proven by `selftest_plan_refusal`.
PROBE_PAD_DEPENDS_ON = ("the_pad_steps_the_joint_solve_closes",)
_PROBE_PAD_MODE = "joint"    # joint | refuse


def _pad_pending_joint(rec: dict) -> bool:
    """Is every writer_water refusal on this leg a pad-boundary step?"""
    w = rec.get("writer_water") or {}
    fin = w.get("after_junction") or w
    return bool(fin.get("pad_steps")) and (
        int(fin.get("steps", 0)) == int(fin.get("pad_steps", 0))
        and not fin.get("drained") and not fin.get("keepout_steps"))


def band_free_probe(corr: routemod.Corridor, free_forbid, build, *, water,
                    refusal) -> dict:
    """Route one leg as if every cell were read: `corr.forbid` swapped for
    `free_forbid` (the same masks, the bodies the census found included,
    the census confinement left off) for exactly this call.

    `build(**kw)` returns `(record, joint)` for the leg on `corr` as it
    stands, and is asked exactly as the plan asks it: past a drowned
    terminus (`build_past_drowned`), then through `water(record, joint,
    build)` -- `water_route` over the same corridor, so a leg whose
    alignment needed more room than the band is probed WITH its remedy.
    `refusal(record)` is `plan_refusal(..., coverage=False)`.
    SERVABLE means the line passes every refusal but unread ground."""
    confined, unread = corr.forbid, corr.unread
    corr.forbid, corr.unread = free_forbid, None
    try:
        rec, joint, base = build_past_drowned(build)
        rec, joint = water(rec, joint, keep_trim(build, base))
    except ValueError as exc:
        return {"verdict": "UNROUTABLE", "why": str(exc)[:240]}
    finally:
        corr.forbid, corr.unread = confined, unread
    out = {"length_m": rec["length_m"],
           "stations": len(rec["nodes"]),
           "stations_unread": (rec.get("census") or {}).get(
               "stations_uncensused")}
    why = refusal(rec)
    if why is None and water_rejection(rec) is not None and not (
            _PROBE_PAD_MODE == "joint" and _pad_pending_joint(rec)):
        why = "writer_water"
    if why is not None:
        return {"verdict": "REFUSED", "by": why, **out}
    # `_rec`/`_joint` are the probe's own leg, kept so the plan can ADOPT it
    # (CENSUS_ADOPT_DEPENDS_ON); popped before the plan is serialised.
    return {"verdict": "SERVABLE", "nodes": rec["nodes"], **out,
            "_rec": rec, "_joint": joint}


# CENSUS_ADOPT_DEPENDS_ON: A LEG THE BAND-FREE PROBE SERVES OVER READ GROUND
# IS ROUTED -- by the probe's own line -- not left for a census that has
# nothing more to read.
#
# The confinement (`precensus.census_forbid`) is a constraint on the SEARCH:
# its snapped endpoints and every A* cell, eroded by the chord slack.  The
# census's claim is about the line that is WRITTEN: its stations
# (`coverage_gate`) and its band (`band_deficit`).  The two differ wherever
# a remedy re-shapes the searched line after the search -- a terminal trim,
# a drowned-terminus trim, an alignment whose re-search snaps elsewhere --
# because the search then stood on ground the written line does not.
# MEASURED 2026-09-26 on Fresh42 drive 4 (stage 11, fixed point round 2):
#   S13-stathub-dock  confined: "no allowed cell within 200.0 m of (502.5,
#       1062.5)" -- the free snap (659.9, 861.2) is on a cell nobody read;
#       band-free: SERVABLE 950.5 m ending (689.94, 835.24), 0 of 486
#       stations unread, band deficit 0;
#   W9-vestvik-lhwest confined: "no route found" -- the free snap
#       (-396.1, -738.8) unread; band-free: SERVABLE 2577.0 m, 0 of 1315
#       stations unread, band deficit 0;
# so `census_wanted` named 0 cells for both and the loop declared a fixed
# point with two servable legs unrouted (drive 3 had routed both).
#   the_probe_is_servable              every refusal but unread ground passed;
#   every_station_it_writes_is_read    `coverage_gate` COVERED;
#   its_whole_band_is_read             `band_deficit` empty.
# All three: the leg is ROUTED by the probe's line.  Servable with unread
# stations or band: its cells are `census_wanted`, and the next round adopts
# it.  A servable leg with neither is `servable_unrouted` -- a contradiction
# the pipeline refuses to call a fixed point (pipeline.CENSUS_LOOP_DEPENDS_ON).
# ---------------------------------------------------------------------------
CENSUS_ADOPT_DEPENDS_ON = ("the_probe_is_servable",
                           "every_station_it_writes_is_read",
                           "its_whole_band_is_read")
# Swapped out by the selftest neuters: "declared" | "off" (the shipped
# plan: a servable probe is never routed) | "blind" (WRONG: any servable
# probe adopted, unread stations and all).
_ADOPT_MODE = "declared"


def probe_adoptable(p: dict | None, census: censusmod.Census) -> bool:
    """May this leg be routed by its band-free probe's line?"""
    if not p or p.get("verdict") != "SERVABLE" or _ADOPT_MODE == "off":
        return False
    if _ADOPT_MODE == "blind":
        return True
    return (not p.get("stations_unread")
            and not band_deficit(p["nodes"], census))


def census_wanted(segments: list[dict], refused: list[dict],
                  census: censusmod.Census) -> dict:
    """The plan's `census_wanted`: per leg, the unread cells of the band
    about the line it writes, or -- for a refused leg whose band-free probe
    is SERVABLE -- about the line it would write (CENSUS_BAND_DEPENDS_ON)."""
    legs = []
    for sg in segments:
        d = band_deficit(sg["nodes"], census)
        if d:
            legs.append({"id": sg["id"], "patch": sg.get("patch"),
                         "kind": "written",
                         "cells": [list(c) for c in d],
                         "why": f"within {census.band_half_m:g} m of the "
                                f"line this plan WRITES"})
    unserved = []
    for r in refused:
        p = r.get("census_probe") or {}
        if p.get("verdict") != "SERVABLE":
            continue
        d = band_deficit(p["nodes"], census)
        if d:
            legs.append({"id": r["id"], "patch": p.get("patch"),
                         "kind": "band-free probe",
                         "cells": [list(c) for c in d],
                         "why": f"within {census.band_half_m:g} m of the "
                                f"line it would write were the ground read "
                                f"({p['length_m']} m band-free, "
                                f"{p['stations_unread']} of {p['stations']} "
                                f"station(s) on unread ground)"})
        else:
            # SERVABLE OVER READ GROUND AND STILL REFUSED: not the census's,
            # and never a fixed point (CENSUS_ADOPT_DEPENDS_ON).
            unserved.append({"id": r["id"], "length_m": p["length_m"],
                             "stations_unread": p["stations_unread"],
                             "owner": "network.CENSUS_ADOPT_DEPENDS_ON: the "
                                      "band-free line stands on read ground "
                                      "and was not routed"})
    cells = sorted({tuple(c) for lg in legs for c in lg["cells"]})
    return {
        "depends_on": list(CENSUS_BAND_DEPENDS_ON),
        "band_half_m": census.band_half_m,
        "cells": [list(c) for c in cells],
        "zones": len(censusmod.zones_for_cells(cells)),
        "legs": legs,
        "servable_unrouted": unserved,
        "method": "cells_for_band at the census's own band half about each "
                  "written line and each SERVABLE band-free probe line, less "
                  "every cell the census read",
        "remedy": ("" if not cells else
                   "precensus.py survey --plan <the provisional plan> "
                   "--wanted <this plan> reads exactly these cells; "
                   "pipeline.py run re-routes after it "
                   "(pipeline.CENSUS_LOOP_DEPENDS_ON)"),
    }


# ---------------------------------------------------------------------------
# A LEG TO A NODE MEASURED ON ANOTHER SEED (spec.MEASURED_SEED_DEPENDS_ON) is
# refused at plan time with THIS seed's own reading at the point, so the
# refusal is a number and not a guess about the other planet.
# ---------------------------------------------------------------------------
FOREIGN_LAND_FREEBOARD_M = 0.5     # roads/survey.py's land test, h > 30.5
FOREIGN_SEARCH_M = 300.0


def foreign_reading(fld, x: float, z: float,
                    search_m: float = FOREIGN_SEARCH_M) -> dict:
    """What this seed's field holds at a node measured on another seed: in
    its window or not, the generated height, land or water, and how far the
    nearest land sample is (within `search_m`)."""
    if not fld.contains(x, z):
        return {"window": fld.id, "in_window": False}
    i, j = fld.index(x, z)
    h = float(fld.h[i, j])
    line = WATER_LEVEL + FOREIGN_LAND_FREEBOARD_M
    r = int(search_m / fld.step)
    i0, j0 = max(0, i - r), max(0, j - r)
    ii, jj = np.nonzero(fld.h[i0:i + r + 1, j0:j + r + 1] > line)
    near = None
    if len(ii):
        near = round(float(np.min(np.hypot(ii + i0 - i, jj + j0 - j)))
                     * fld.step, 1)
    return {"window": fld.id, "in_window": True, "generated_y": round(h, 2),
            "land": h > line, "nearest_land_m": near,
            "searched_m": search_m}


def foreign_text(nid: str, xz, measured_on: str, here: dict) -> str:
    head = f"{nid} ({xz[0]}, {xz[1]}) measured on {measured_on}"
    if not here.get("in_window"):
        return f"{head}: outside this seed's {here.get('window')} window"
    land = ("land" if here["land"] else "open water")
    near = (f"nearest land {here['nearest_land_m']} m"
            if here["nearest_land_m"] is not None
            else f"no land within {here['searched_m']:g} m")
    return (f"{head}: here {land}, generated {here['generated_y']} m, "
            f"{near}")


# ---------------------------------------------------------------------------
# NODE_ANCHOR_DEPENDS_ON: LEGS THAT MEET AT ONE NODE START FROM ONE CELL.
#
# Every road class searches its OWN corridor, whose keep-out masks are grown
# by that class's half-width, so `Corridor.nearest_allowed` snaps one node to
# a different cell per class.  MEASURED, Fresh42 drive 4 (roads/segments.
# Fresh42.yaml of 2026-09-26, gate on save af96bfb62427): at the temple --
# StartTemple's protected ring, no pad to arrive on -- T1 B, T3 A, T5 A and
# T13 A all end at (-24.1, -30.8) on the trunk mask and S5-temple-southcape A
# at (-28.1, -26.8) on the spur mask, 5.66 m away.  One place, two ends; the
# gate's settlements section read S5 A as a terminus that meets nothing.
#
# THE RULE: a node served by more than one carriageway width is snapped ONCE,
# on the WIDEST class's corridor -- whose masks contain every narrower one's,
# so the cell is allowed for all of them -- and every leg there starts from
# that cell when its own corridor allows it.
#   every_class_that_meets_at_the_node   the widths of every routable leg at
#       the node, not the leg being searched.
#   the_widest_carriageways_own_mask      the anchor is that class's own snap.
# NEUTER `_NODE_ANCHOR_MODE = "per_class"`: the shipped rule, each corridor
# snaps alone.  Proven by `selftest_node_anchor`.
# ---------------------------------------------------------------------------
NODE_ANCHOR_DEPENDS_ON = ("every_class_that_meets_at_the_node",
                          "the_widest_carriageways_own_mask")
_NODE_ANCHOR_MODE = "shared"     # shared | per_class (the shipped rule)
_NODE_ANCHORS: dict[str, tuple[float, float]] = {}


def node_anchors(edges: list[dict], nodes: dict,
                 corridor_of) -> dict[str, tuple[float, float]]:
    """node id -> the cell every leg meeting there starts from, for each node
    served by more than one carriageway width.  `corridor_of(patch, cls)` is
    the corridor a leg of that class is searched in."""
    classes: dict[str, dict[str, str]] = {}
    for e in edges:
        patch = nodes[e["a"]]["patch"]
        for nid in (e["a"], e["b"]):
            classes.setdefault(nid, {}).setdefault(e["cls"], patch)
    out: dict[str, tuple[float, float]] = {}
    for nid, by_cls in classes.items():
        if len({WIDTH_M[c] for c in by_cls}) < 2:
            continue
        cls = max(sorted(by_cls), key=lambda c: WIDTH_M[c])
        corr = corridor_of(by_cls[cls], cls)
        try:
            (i, j), _rec = arrival_start(nid, nodes[nid]["xz"], corr,
                                         WIDTH_M[cls])
        except ValueError:
            continue
        x, z = corr.cell_world(i, j)
        out[nid] = (float(x), float(z))
    return out


def search_xz(node_id: str, corr: routemod.Corridor, nodes: dict | None = None,
              anchors: dict | None = None, *, width_m: float | None = None,
              extra_forbid: np.ndarray | None = None) -> tuple[float, float]:
    """Where a search at this node starts (NODE_ANCHOR_DEPENDS_ON): the
    node's anchor where it has one and this corridor allows it; else, given
    the carriageway width, the cell `arrival_start` picks when that is not
    the nearest (ARRIVAL_REACH_DEPENDS_ON); else the node's own point for
    the corridor to snap."""
    nodes = specmod.NODES if nodes is None else nodes
    anchors = _NODE_ANCHORS if anchors is None else anchors
    xz = tuple(nodes[node_id]["xz"])
    a = anchors.get(node_id)
    if _NODE_ANCHOR_MODE == "shared" and a is not None:
        try:
            i, j = corr.cell_of(*a)
        except ValueError:
            return xz
        if not (corr.wet[i, j] or corr.blocked(i, j)):
            return a
    if width_m is None:
        return xz
    try:
        (i, j), rec = arrival_start(node_id, xz, corr, width_m, extra_forbid)
    except ValueError:
        return xz
    return corr.cell_world(i, j) if rec and "moved_from" in rec else xz


# ---------------------------------------------------------------------------
# ORPHAN_JOIN_DEPENDS_ON: EVERY LEG THE PLAN LAYS IS WALKABLE FROM THE ROOT.
#
# A leg's authored topology can name a join this seed cannot build.  MEASURED,
# Fresh42 drive 4: S8-wtsouth-treesth (317.9 m) was routed, paved and lit,
# and the only authored legs that joined wtsouth to the network -- T10/T11
# wtspawn<->wtsouth, through T12 wtspawn->temple -- are UNROUTABLE, because
# the solved watchtower wtspawn (-974.1, -778.8) stands on land component 100
# of the trunk window (0.59 km2) and not on the spawn landmass (component
# 128, 2.07 km2).  The gate's connectivity then refused treesth and wtsouth
# twice, "1 missing link(s) ... gap at (-460.0,-658.0), 311.1 m ... nothing
# in this tree plans or paves a new segment to close a gap".  Nothing did:
# the planner never asked whether what it laid was connected.
#
# THE RULE: after every authored leg is routed, the legs that meet are one
# piece -- their paved footprints touch, they still serve the same node, or
# they serve the two banks of a DECLARED crossing (the bridge carries the
# walk). A released abutment, or a road ending outside its bank's level
# footprint, no longer serves that bank. Every piece the root's piece does
# not contain gets a JOIN leg from the nearest served node (including the
# far bank of a declared bridge actually reached from this piece) to one
# of its own served nodes, on the same island. An emitted candidate is not
# a repair until the original orphan belongs to the recomputed root piece.
# Failed candidates leave the requirement open and farther alternatives
# pass through the same landing, writer and census gates. Unread buildable
# approaches feed `census_wanted`; none are paved over unread ground.
#   the_root_the_gate_walks_from   `portalhub`, accept/sections/roads.py's
#       connectivity origin.
#   legs_that_touch_share_a_node_or_a_declared_bridge   what makes two legs
#       one piece: centrelines within both level half-widths, a common end
#       node, or the two banks of one of `spec.DECLARED_CROSSINGS`.
#   the_refused_legs_that_named_the_orphan   carried on the join as its why.
# NEUTER `_ORPHAN_JOIN_MODE = "off"`: the shipped plan, an orphan emitted as
# it is.  Proven by `selftest_orphan_join`.
# ---------------------------------------------------------------------------
ORPHAN_JOIN_DEPENDS_ON = ("the_root_the_gate_walks_from",
                          "legs_that_touch_share_a_node_or_a_declared_bridge",
                          "the_refused_legs_that_named_the_orphan")
_ORPHAN_JOIN_MODE = "join"       # join | off (the shipped plan)
ORPHAN_ROOT = "portalhub"
BRIDGE_APPROACH_DEPENDS_ON = ("the_declared_bridge_bank_identity",
                              "the_written_endpoint_and_level_footprint",
                              "the_bank_not_released_by_terminal_trimming")


def bridge_approach_gaps(seg: dict, *, nodes: dict | None = None,
                         crossings: list[dict] | None = None) -> list[dict]:
    """Unserved DECLARED banks, measured on this road's written endpoints.

    Other level nodes may stand on a pad edge; they are not bridge banks.
    A bank may be off the centreline, but must lie in its existing level
    footprint. A released pin is not a landing even if its xz still fits.
    """
    nodes = specmod.NODES if nodes is None else nodes
    crossings = specmod.DECLARED_CROSSINGS if crossings is None else crossings
    banks = {c[side]["node"] for c in crossings
             for side in ("bank_a", "bank_b")}
    released = {a["node"]: a for a in seg.get("abutments_released", [])}
    out = []
    for end, node, p in (("a", seg["from"], seg["nodes"][0]),
                         ("b", seg["to"], seg["nodes"][-1])):
        if node not in banks:
            continue
        x, z = nodes[node]["xz"]
        gap = math.hypot(float(p[0]) - x, float(p[1]) - z)
        reach = _level_half(seg)
        if node in released or gap > reach:
            out.append({"node": node, "end": end,
                        "endpoint_xz": list(p[:2]), "bank_xz": [x, z],
                        "gap_m": round(gap, 3), "level_half_m": reach,
                        "released": released.get(node),
                        "depends_on": list(BRIDGE_APPROACH_DEPENDS_ON)})
    return out


def _served_nodes(seg: dict, *, nodes: dict | None = None,
                  crossings: list[dict] | None = None) -> set[str]:
    """Named endpoints the written road still serves, not nominal topology."""
    released = {a["node"] for a in seg.get("abutments_released", [])}
    missing = {a["node"] for a in bridge_approach_gaps(
        seg, nodes=nodes, crossings=crossings)}
    return {seg["from"], seg["to"]} - released - missing


def bridge_approach_requirements(segments: list[dict], nodes: dict, *,
                                 crossings: list[dict] | None = None
                                 ) -> list[dict]:
    """Every declared bank remains a requirement, even with no road at all."""
    crossings = specmod.DECLARED_CROSSINGS if crossings is None else crossings
    served = {s["id"]: _served_nodes(s, nodes=nodes, crossings=crossings)
              for s in segments}
    gaps = [(s["id"], g) for s in segments
            for g in bridge_approach_gaps(s, nodes=nodes, crossings=crossings)]
    return [{"crossing_id": c["crossing_id"], "bank": side,
             "node": c[side]["node"], "bank_xz": list(nodes[c[side]["node"]]["xz"]),
             "verdict": ("SERVED" if any(c[side]["node"] in ns
                                        for ns in served.values()) else "MISSING"),
             "served_by": sorted(i for i, ns in served.items()
                                 if c[side]["node"] in ns),
             "disconnected": [{"segment_id": i, **g} for i, g in gaps
                              if g["node"] == c[side]["node"]]}
            for c in crossings for side in ("bank_a", "bank_b")]


def _bridge_frontier(served: set[str], crossings: list[dict]) -> set[str]:
    """Bank nodes reachable through declared bridges from a served bank."""
    out = set(served)
    while True:
        before = len(out)
        for c in crossings:
            banks = {c["bank_a"]["node"], c["bank_b"]["node"]}
            if banks & out:
                out |= banks
        if len(out) == before:
            return out


def _level_half(seg: dict) -> float:
    return routemod.footprint_half_m(float(seg.get("width_m")
                                           or WIDTH_M[seg.get("cls", "spur")]))


def plan_components(segments: list[dict],
                    bridges: list[tuple[str, str]] | None = None
                    ) -> list[list[str]]:
    """The legs of a plan as the pieces a walker can move between
    (ORPHAN_JOIN_DEPENDS_ON).  `bridges` defaults to the bank node pairs
    of `spec.DECLARED_CROSSINGS`."""
    if bridges is None:
        bridges = [(c["bank_a"]["node"], c["bank_b"]["node"])
                   for c in specmod.DECLARED_CROSSINGS]
    from scipy.spatial import cKDTree                  # noqa: PLC0415
    ids = [s["id"] for s in segments]
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    pts = {s["id"]: np.asarray([p[:2] for p in s["nodes"]], dtype=np.float64)
           for s in segments}
    trees = {i: cKDTree(p) for i, p in pts.items() if len(p)}
    half = {s["id"]: _level_half(s) for s in segments}
    for k, a in enumerate(ids):
        for b in ids[k + 1:]:
            if a not in trees or b not in trees or find(a) == find(b):
                continue
            # Stations are STATION_M apart, so half a pitch covers a line
            # that passes between two of them.
            reach = half[a] + half[b] + STATION_M / 2.0
            d, _ = trees[b].query(pts[a], k=1, distance_upper_bound=reach)
            if np.isfinite(d).any():
                parent[find(a)] = find(b)
    at_node: dict[str, list[str]] = {}
    for s in segments:
        for n in _served_nodes(s):
            at_node.setdefault(n, []).append(s["id"])
    for na, nb in bridges:
        at_node.setdefault(na, []).extend(at_node.get(nb, []))
    for legs in at_node.values():
        for other in legs[1:]:
            parent[find(other)] = find(legs[0])
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def orphan_components(segments: list[dict], root: str = ORPHAN_ROOT
                      ) -> tuple[list[str] | None, list[list[str]]]:
    """(the root's piece, every other piece); all pieces fail with no root."""
    by_id = {s["id"]: s for s in segments}
    comps = plan_components(segments)
    rooted = next((c for c in comps
                   if any(root in _served_nodes(by_id[i])
                          for i in c)), None)
    if rooted is None:
        return None, comps
    return rooted, [c for c in comps if c is not rooted]


def repair_connects(edge: dict, segments: list[dict], candidate: dict,
                    root: str = ORPHAN_ROOT) -> bool:
    """Would this written candidate satisfy the repair's actual obligation?"""
    trial = [s for s in segments if s["id"] != candidate["id"]] + [candidate]
    rooted, _left = orphan_components(trial, root)
    if edge.get("join"):
        return set(edge["join"]["orphan"]) <= set(rooted or [])
    if edge.get("bridge_approach"):
        return (candidate["id"] in (rooted or [])
                and edge["b"] in _served_nodes(candidate))
    return False


def join_candidates(orphan: list[str], rooted: list[str],
                    segments: list[dict], nodes: dict) -> list[dict]:
    """Nearest same-island pairs from served topology, not authored names.

    A served bank also exposes the far bank of that declared bridge. It
    does not expose unrelated nodes on the nominally reachable island.
    """
    by_id = {s["id"]: s for s in segments}
    o_nodes = sorted({n for i in orphan
                      for n in _served_nodes(by_id[i], nodes=nodes)})
    r_nodes = sorted(_bridge_frontier(
        {n for i in rooted for n in _served_nodes(by_id[i], nodes=nodes)},
        specmod.DECLARED_CROSSINGS))
    pairs = sorted(
        (math.hypot(nodes[o]["xz"][0] - nodes[r]["xz"][0],
                    nodes[o]["xz"][1] - nodes[r]["xz"][1]), r, o)
        for o in o_nodes for r in r_nodes
        if o != r and o in nodes and r in nodes
        and nodes[o]["island"] == nodes[r]["island"])
    pri = min(int(by_id[i]["pri"]) for i in orphan)
    return [{"id": f"J-{r.replace('-', '')}-{o.replace('-', '')}",
             "a": r, "b": o, "cls": "spur", "pri": pri,
             "join": {"orphan": sorted(orphan), "node_gap_m": round(d, 1)}}
            for d, r, o in pairs]


def rooted_islands(nodes: dict, root: str = ORPHAN_ROOT,
                   crossings: list[dict] | None = None) -> set[str]:
    """The root's island and every island a DECLARED crossing joins to it,
    transitively: the islands a walk from the root can reach at all."""
    crossings = specmod.DECLARED_CROSSINGS if crossings is None else crossings
    if root not in nodes:
        return set()
    out = {nodes[root]["island"]}
    grew = True
    while grew:
        grew = False
        for c in crossings:
            joins = set(c.get("joins") or ())
            if joins & out and not joins <= out:
                out |= joins
                grew = True
    return out


def join_orphans(segments: list[dict], refused: list[dict], nodes: dict,
                 edge_ends: dict[str, tuple[str, str]], route, servable,
                 root: str = ORPHAN_ROOT,
                 reachable_islands: set[str] | None = None) -> dict:
    """ORPHAN_JOIN_DEPENDS_ON over a routed plan.  `route(edge)` routes one
    leg through every plan-time gate and appends it to `segments` when it
    passes; `servable(edge_id)` is True when that leg's refusal is only the
    census's to lift.  `reachable_islands` (default: the islands the root's
    own piece stands on) are never UNROOTED: a piece there whose bridge leg
    was refused is reported, not rejected."""
    rooted, orphans = orphan_components(segments, root)
    out = {"depends_on": list(ORPHAN_JOIN_DEPENDS_ON), "root": root,
           "mode": _ORPHAN_JOIN_MODE,
           "pieces_cut_off_before": [list(c) for c in orphans],
           "joins": []}
    tried: set[str] = set()
    settled: set[tuple[frozenset, frozenset]] = set()
    if _ORPHAN_JOIN_MODE == "join" and rooted is not None:
        # Candidate ids are finite and never retried. Continue while the
        # rooted component grows, rather than abandoning the ninth group.
        while True:
            rooted, orphans = orphan_components(segments, root)
            moved = False
            for orphan in orphans:
                state = (frozenset(orphan), frozenset(rooted))
                if state in settled:
                    continue
                settled.add(state)
                by_id = {s["id"]: s for s in segments}
                o_nodes = {n for i in orphan
                           for n in _served_nodes(by_id[i], nodes=nodes)}
                named = sorted({r["id"] for r in refused
                                if set(edge_ends.get(r["id"], ())) & o_nodes})
                row = {"piece": list(orphan), "refused_legs_naming_it": named,
                       "verdict": "ORPHANED", "via": None, "tried": []}
                r_nodes = _bridge_frontier(
                    {n for i in rooted
                     for n in _served_nodes(by_id[i], nodes=nodes)},
                    specmod.DECLARED_CROSSINGS)
                r_islands = {nodes[n]["island"] for n in r_nodes if n in nodes}
                o_islands = {nodes[n]["island"] for n in o_nodes}
                if o_islands and not o_islands & (
                        r_islands | (reachable_islands or set())):
                    row["verdict"] = "UNROOTED_ISLAND"
                    row["why"] = (
                        f"{', '.join(orphan)} lie(s) on island "
                        f"{', '.join(sorted(o_islands))}, which holds no node "
                        f"the network walked from {root} serves (that is on "
                        f"{', '.join(sorted(r_islands))}); no leg can join "
                        f"them, so nothing they pave is walkable from "
                        f"{root}. Owner: spec.EDGES, authored for an island "
                        f"this seed reaches by portal only")
                    out["joins"].append(row)
                    continue
                cands = [c for c in join_candidates(orphan, rooted, segments,
                                                    nodes)
                         if c["id"] not in tried]
                for cand in cands:
                    tried.add(cand["id"])
                    cand["why"] = (
                        f"JOINS {', '.join(orphan)} to the network the gate "
                        f"walks from {root}: the authored leg(s) naming "
                        f"{', '.join(sorted(o_nodes))} are refused on this "
                        f"seed ({', '.join(named) or 'none named'}); "
                        f"{cand['b']} is {cand['join']['node_gap_m']} m from "
                        f"{cand['a']}, the nearest node the rooted network "
                        f"serves (ORPHAN_JOIN_DEPENDS_ON)")
                    edge_ends[cand["id"]] = (cand["a"], cand["b"])
                    route(cand)
                    if any(s["id"] == cand["id"] for s in segments):
                        after_rooted, _left = orphan_components(segments, root)
                        moved |= bool(set(after_rooted or ()) - set(rooted))
                        rooted = after_rooted or rooted
                        if set(orphan) <= set(rooted):
                            row["tried"].append([cand["id"], "ROUTED"])
                            row["verdict"], row["via"] = "JOINED", cand["id"]
                            break
                        row["tried"].append([cand["id"], "ROUTED_DISCONNECTED"])
                        # A trim can emit a perfectly buildable partial road
                        # without reaching either component. Try the next
                        # approach; emission alone proves no connectivity.
                        continue
                    if servable(cand["id"]):
                        row["tried"].append([cand["id"], "WAITS_FOR_CENSUS"])
                        row["verdict"] = "WAITS_FOR_CENSUS"
                        row["via"] = cand["id"]
                        break
                    row["tried"].append([cand["id"], "REFUSED"])
                out["joins"].append(row)
                if moved:
                    break
            if not moved:
                break
    _rooted, left = orphan_components(segments, root)
    out["pieces_cut_off_after"] = [list(c) for c in left]
    return out


def join_bridge_approaches(segments: list[dict], nodes: dict, route, servable,
                           *, root: str = ORPHAN_ROOT) -> list[dict]:
    """Try buildable alternatives to each missing declared bank approach.

    Targets come from crossing obligations, not an authored road's lost
    endpoint. Sources are nodes actually served by the rooted road graph,
    plus far banks of declared bridges reached from that graph. Routing
    uses the same callback as orphan repair, including its census probe.
    """
    out = []
    tried = {s["id"] for s in segments}
    for pending in bridge_approach_requirements(segments, nodes):
        req = next(r for r in bridge_approach_requirements(segments, nodes)
                   if r["crossing_id"] == pending["crossing_id"]
                   and r["bank"] == pending["bank"])
        if req["verdict"] == "SERVED":
            continue
        target = req["node"]
        rooted, _orphans = orphan_components(segments, root)
        by_id = {s["id"]: s for s in segments}
        sources = _bridge_frontier(
            {n for i in rooted or [] for n in _served_nodes(by_id[i], nodes=nodes)},
            specmod.DECLARED_CROSSINGS)
        pairs = sorted(
            (math.dist(nodes[n]["xz"], nodes[target]["xz"]), n)
            for n in sources if n != target and n in nodes
            and nodes[n]["island"] == nodes[target]["island"])
        row = {**req, "verdict": "MISSING", "via": None, "tried": []}
        for gap, source in pairs:
            eid = f"A-{source.replace('-', '')}-{target.replace('-', '')}"
            if eid in tried:
                continue
            tried.add(eid)
            edge = {"id": eid, "a": source, "b": target, "cls": "spur",
                    "pri": min((int(s["pri"]) for s in segments
                                if target in (s["from"], s["to"])), default=1),
                    "bridge_approach": {"crossing_id": req["crossing_id"],
                                        "bank": req["bank"],
                                        "node_gap_m": round(gap, 1)},
                    "why": f"Restore missing approach to declared "
                           f"{req['crossing_id']} {req['bank']} ({target}); "
                           f"the previous endpoint is not a served bank"}
            route(edge)
            rooted_now, _left = orphan_components(segments, root)
            attached = next((r for r in bridge_approach_requirements(segments, nodes)
                             if r["node"] == target), req)
            if set(attached["served_by"]) & set(rooted_now or []):
                row.update(verdict="JOINED", via=eid)
                row["tried"].append([eid, "ROUTED"])
                break
            if servable(eid):
                row.update(verdict="WAITS_FOR_CENSUS", via=eid)
                row["tried"].append([eid, "WAITS_FOR_CENSUS"])
                break
            row["tried"].append([eid, "ROUTED_DISCONNECTED"
                                 if any(s["id"] == eid for s in segments)
                                 else "REFUSED"])
        out.append(row)
    return out

# ---------------------------------------------------------------------------
# LEGS THAT MEET AGREE AT PLAN TIME.  See `route.JUNCTION_DATUM_DEPENDS_ON`
# for the key, the rule and the measurement; this is where the planner hands
# every routed leg to the joint solve and writes its answer back.
#
# The neuter switch, like `_PAD_SOURCE`: `selftest` rebinds it to drop a term
# and show the known-bad state return.  `()` is "no joint solve at all" --
# every leg keeps the profile it was fitted with alone, which is the plan
# the Fresh42 build was handed.
# ---------------------------------------------------------------------------
_JUNCTION_DATUM_DEPENDS_ON = routemod.JUNCTION_DATUM_DEPENDS_ON


def pad_participants() -> tuple[list[dict], list[str]]:
    """Every pad a road must meet at its datum, junction-shaped, from the
    SAME population the router keeps out of (`pad_claims`, PAD_CLAIMS_DEPENDS_
    ON): written pads carry the datum the ledger wrote, planned ones the datum
    their producer solved.  A pad with no datum cannot be held, and is NAMED
    rather than silently dropped."""
    # `pad_claims` FIRST: it is what puts ledger/ on the path and imports
    # the writer `junction.pads_from_ledger` borrows, and a plan that routed
    # no corridor has not called it yet.
    claims = pad_claims()
    written: dict[str, float] = {}
    run = JUMPSTART / "ledger" / "runs" / str(WORLD) / "ledger.jsonl"
    if run.exists():
        for p in routemod._junction().pads_from_ledger(run):
            written[str(p["id"])] = float(p["datum_y"])
    out, missing = [], []
    for q in claims:
        sid = str(q["site_id"])
        y = q.get("datum_y") if q.get("planned") else written.get(sid)
        if y is None:
            missing.append(sid)
            continue
        out.append({"kind": "pad", "id": sid, "label": sid,
                    "role": q.get("role", "site_pad"), "seq": q.get("seq"),
                    "x": float(q["x"]), "z": float(q["z"]),
                    "hw": float(q["hw"]), "hd": float(q["hd"]),
                    "datum_y": float(y),
                    "source": "planned" if q.get("planned") else str(run)})
    return out, missing


def junction_legs(segments: list[dict], joints: dict[str, dict]) -> list[dict]:
    """Each routed leg in the shape `route.junction_datums` takes."""
    return [{"id": sg["id"], "cls": sg["cls"], "pri": sg["pri"],
             "width_m": float(sg["width_m"]), "nodes": sg["nodes"],
             "is_bridge": sg["is_bridge"], "y0": joints[sg["id"]]["y0"],
             "s": joints[sg["id"]]["s"], "lo": joints[sg["id"]]["lo"],
             "hi": joints[sg["id"]]["hi"],
             "spans": joints[sg["id"]]["spans"],
             "cand": joints[sg["id"]]["cand"],
             "rung": joints[sg["id"]]["rung"],
             "ladder": joints[sg["id"]]["ladder"]}
            for sg in segments]


def junction_population(segments: list[dict], pads: list[dict]) -> dict:
    """The shared-ground population AS THE BUILD'S GATE READS IT, from the
    profiles the plan will emit: `junction.gate`, its pairs and its places."""
    J = routemod._junction()
    ribs = J.ribbons_from_segments({"segments": segments}, "plan")
    rep = J.gate(ribs, pads)
    s = rep["survey"]
    over = [j for j in rep["junctions"] if j["impassable_pairs"]]
    w = rep["refusals"][0] if rep["refusals"] else None
    return {
        "pairs_sharing_ground": s["pairs_sharing_ground"],
        "places": s["junctions"],
        "places_over_allowance": len(over),
        "impassable_pairs": s["impassable_pairs"],
        "road_vs_pad_pairs": s["road_vs_pad_pairs"],
        # ONE DATUM, AS THE GATE COUNTS IT: pairs flush to 1 cm, and pairs
        # over the design budget (its tier 2, report-only).
        "flush_within_1cm": s["flush_within_1cm"],
        "over_design_budget_pairs": s["over_budget_pairs"],
        "worst": (None if w is None else
                  {"pair": [w["a"], w["b"]], "xz": w["xz"],
                   "design_y": [w["a_y"], w["b_y"]],
                   "spread_m": w["spread_m"],
                   "allowance_m": w["slide_allow_m"]}),
        "places_over": [{"at": j["at"], "ways": j["ways"],
                         "design_heights": j["design_heights"],
                         "spread_m": j["spread_m"],
                         "worst_pair": j["worst_pair"],
                         "worst_pair_spread_m": j["worst_pair_spread_m"],
                         "impassable_pairs": j["impassable_pairs"]}
                        for j in over],
        "instrument": "roads/junction.py::gate over the in-memory plan -- "
                      "the same find_conflicts the build's junction gate "
                      "runs at step 4, run here on the profiles this plan "
                      "will emit",
    }


def apply_junction_datums(segments: list[dict], joints: dict[str, dict],
                          pads: list[dict],
                          depends_on: tuple | None = None) -> dict:
    """Solve every leg's profile jointly and write the answer back into the
    segment records IN PLACE; return the solve.

    A leg that meets nothing is not touched at all -- not a key, not a byte
    -- so its record is the one it had before this existed.  A leg that
    shares ground carries a `junction_datum` record either way, and one the
    solve moved has every profile number recomputed by `profile_stats`, the
    function that produced them in the first place."""
    grade, batter, lattice = writer_batter()
    res = routemod.junction_datums(
        junction_legs(segments, joints), pads, grade=grade,
        batter_m=batter, lattice_m=lattice,
        depends_on=(_JUNCTION_DATUM_DEPENDS_ON if depends_on is None
                    else depends_on))
    refused = {r["id"] for r in res["refused"]}
    for sg in segments:
        sid = sg["id"]
        if sid in refused or sid not in res["legs"]:
            continue
        v = res["legs"][sid]
        j = joints[sid]
        if v["verdict"] == "UNSHARED":
            continue
        if v["stations_moved"] or v["rung"] != j["rung"]:
            y = np.asarray(res["y"][sid], dtype=np.float64)
            sg.update(profile_stats(y, j["s"], j["t"], j["tw_lo"],
                                    j["tw_hi"], j["free"]))
            sg["profile_y"] = [round(float(q), 3) for q in y]
        if v["rung"] != j["rung"]:
            sg["grade_limit"] = v["rung"]
            sg["grade_relaxed_to"] = v["rung"]
            sg["grade_relaxed_by"] = "junction"
        sg["junction_datum"] = {
            **v,
            "depends_on": list(depends_on if depends_on is not None
                               else _JUNCTION_DATUM_DEPENDS_ON),
            "rule": routemod.JUNCTION_DATUM_RULE,
            "margin_m": routemod.JUNCTION_DATUM_MARGIN_M,
            "tool": "roads/route.py::junction_datums, called from "
                    "roads/network.py::apply_junction_datums",
        }
    return res


# THE JOINT SOLVE MAY LIFT A LEG BACK INTO THE WATER IT WAS ROUTED OUT OF.
# It moves each leg inside that leg's own bounds, and a leg the writer lays
# clear as routed carries no walked-ray ceiling in them.  MEASURED on the
# Fresh42 re-plan: S5-temple-southcape aligned off the pond at
# (-141,-264) with 0 water steps, and the solve raised it to 2 (0.9117 m).
# So a leg the solve refuses gets its ceiling IN ITS OWN BOUNDS and the
# network is solved again -- the same place the self-adjacency join and the
# pad datums live, never a patch on the answer.
WATER_JOINT_ROUNDS = 3

# WATER_CAP_DEPENDS_ON: A WALKED-RAY CEILING ON ONE LEG MUST NOT REFUSE A LEG
# THE UNCAPPED SYSTEM JOINED.
#
# The ceiling is written into ONE leg's bounds, but the leg may share its
# corridor with others, and the joint solve holds all of them to one datum
# there.  A ceiling those neighbours cannot follow makes the system
# infeasible, and `_refuse_order` then refuses whichever member of the
# conflict ranks lowest, which need not be the capped leg.  MEASURED,
# Pirate68 provisional re-plan 2026-09-26 (/tmp/pj/fix3): round 1 of the
# solve refused nothing.  Round 2 capped S13-stathub-dock's stations 87-96
# at its dock end (hi down 6.4 m, to 32.2) and refused S7-stathub-wtnorth.
# The conflict was {S13, S7, T6} at (487, 1122), where T6 stands at 40.1 and
# S7 at 35.7 in the corridor all three share west of stathub.  S7 has no
# water refusal of its own.
#
# THE RULE: after a round's ceilings, a refusal the previous round did not
# make is the ceiling's.  Every ceiling on a leg in that refusal's conflict
# is withheld for the rest of the plan, and the round is solved again.  The
# capped leg then keeps its writer-water REFUSAL, with its own number and
# owner, as it had before the joint solve existed.  The leg it would have
# cost the plan is not refused.
#   the_refusals_the_uncapped_system_did_not_make   read off the solve;
#   the_legs_in_each_such_refusals_conflict        the refusal's own conflict
#       (`route.JUNCTION_CONFLICT_DEPENDS_ON`, its `rungs_tried` members).
# NEUTER `_WATER_CAP_MODE = "always"`: the shipped rule, every ceiling kept.
# Proven by `selftest_water_cap`.
WATER_CAP_DEPENDS_ON = ("the_refusals_the_uncapped_system_did_not_make",
                        "the_legs_in_each_such_refusals_conflict")
_WATER_CAP_MODE = "joinable"     # joinable | always (the shipped rule)


def junction_with_caps(segments: list[dict], joints: dict[str, dict],
                       pads: list[dict], caps: dict[str, dict[int, float]],
                       before: dict[str, dict], refused_before: set) -> dict:
    """Write `caps` (leg id -> {station: ceiling}) into the legs' own upper
    bounds and solve again from the records in `before`.  Under
    WATER_CAP_DEPENDS_ON, a ceiling on any leg in the conflict of a refusal
    that `refused_before` lacks is taken back out and the system solved once
    more.  The legs whose ceilings were withheld are returned as
    `water_caps_withheld`."""
    import copy                                             # noqa: PLC0415
    saved = {sid: np.array(joints[sid]["hi"], dtype=np.float64, copy=True)
             for sid in caps}
    for sid, ks in caps.items():
        for k, v in ks.items():
            joints[sid]["hi"][k] = v

    def resolve() -> dict:
        for sg in segments:
            sid = sg["id"]
            sg.clear()
            sg.update(copy.deepcopy(before[sid]))
        return apply_junction_datums(segments, joints, pads)
    jd = resolve()
    withheld: list[str] = []
    if _WATER_CAP_MODE == "joinable":
        undo: set[str] = set()
        for r in jd["refused"]:
            if r["id"] in refused_before:
                continue
            members = set(r["binding"].get("rungs_tried") or ()) | {r["id"]}
            undo |= members & set(saved)
        if undo:
            for sid in sorted(undo):
                joints[sid]["hi"] = saved[sid]
                withheld.append(sid)
            jd = resolve()
    jd["water_caps_withheld"] = withheld
    return jd


def water_after_joint(segments: list[dict], joints: dict[str, dict],
                      pads: list[dict], flds, judged: dict[str, list],
                      cache: dict) -> dict:
    """`apply_junction_datums`, re-run with a walked-ray ceiling in the
    bounds of every leg the solve lifted into a water refusal, less any
    ceiling that would refuse a leg the uncapped system joined
    (WATER_CAP_DEPENDS_ON).  `judged` is the profile each leg's water was
    already judged CLEAR or REFUSED on; `cache` keeps each `writer_water`
    answer by (leg, profile) for the pass that records the final verdict."""
    import copy                                             # noqa: PLC0415
    before = {sg["id"]: copy.deepcopy(sg) for sg in segments}
    jd = apply_junction_datums(segments, joints, pads)
    withheld: set[str] = set()
    for _rnd in range(WATER_JOINT_ROUNDS):
        refused = {r["id"] for r in jd["refused"]}
        caps: dict[str, dict[int, float]] = {}
        for sg in segments:
            sid = sg["id"]
            if (sid in refused or sid in withheld
                    or "writer_water" not in sg
                    or sg["profile_y"] == judged.get(sid)):
                continue
            key = (sid, tuple(sg["profile_y"]))
            if key not in cache:
                cache[key] = writer_water(sg, worldseed.resolve(flds,
                                                                sg["patch"]))
            fw = cache[key]
            if fw["verdict"] != "REFUSED":
                continue
            j = joints[sid]
            for k, d in fw["_drops"].items():
                cap = max(float(sg["profile_y"][k]) - d,
                          min(float(j["y0"][k]), float(j["hi"][k])))
                if j["lo"][k] < j["hi"][k] and cap < j["hi"][k] - 1e-9:
                    caps.setdefault(sid, {})[k] = max(cap, float(j["lo"][k]))
        if not caps:
            break
        jd = junction_with_caps(segments, joints, pads, caps, before, refused)
        withheld |= set(jd["water_caps_withheld"])
    if withheld:
        print(f"water ceilings WITHHELD at PLAN time: {sorted(withheld)} -- "
              f"each would have refused a leg the uncapped joint solve "
              f"joined; the leg keeps its writer-water refusal; mode "
              f"{_WATER_CAP_MODE}; key = network.WATER_CAP_DEPENDS_ON")
    jd["water_caps_withheld"] = sorted(withheld)
    return jd


# WATER_REREMEDY_DEPENDS_ON: A LEG THE JOINT SOLVE MOVES INTO A WRITER
# REFUSAL IS OFFERED ITS OWN REMEDIES AGAIN, NOT DROPPED.
#
# The writer's check (`writer_water`) is asked of each leg ALONE and its
# remedies -- alignment, terminal trim, ceiling -- are chosen there.  The
# joint solve then moves the profile, and WATER_CAP only knows the ceiling:
# a refusal with no drop to cap (a POI disc the lifted batter now reaches)
# goes straight to the final pass and is dropped.  MEASURED, Fresh42 drive 6
# stage 11: T5-temple-dockshore routed CLEAR as routed + terminal trim
# (1236 m, joined, moved 407 st); on the joint-solved profile the writer
# refuses 1 step, 0.892 m at [-419, -45], a keep-out step of the r 9.0 m
# disc at (-427.62, -44.46) with no `_drops`, and the leg was REFUSED.
#
# THE RULE: after the joint solve, a leg the writer refuses on the profile
# the solve gave it is handed to `water_route` again ON THAT PROFILE -- its
# own remedies, in their order, the alignment stand-off sized from the
# refusal the solve made, accumulated over rounds -- and the network is
# solved again from every leg's solo record.  At most WATER_REREMEDY_ROUNDS
# rounds; what is still refused after them is refused with its number and
# owner by the final pass, exactly as before.
#   the_writer_refusal_on_the_joint_solved_profile
#   the_legs_own_remedies_in_their_order      `water_route`
#   a_bound_on_rounds                         WATER_REREMEDY_ROUNDS
# NEUTER `_WATER_REREMEDY_MODE = "drop"`: the shipped rule.  Proven by
# `selftest_water_reremedy`.
WATER_REREMEDY_DEPENDS_ON = ("the_writer_refusal_on_the_joint_solved_profile",
                             "the_legs_own_remedies_in_their_order",
                             "a_bound_on_rounds")
_WATER_REREMEDY_MODE = "reremedy"   # reremedy | drop (the shipped rule)
WATER_REREMEDY_ROUNDS = 2


def remedied_after_joint(segments: list[dict], joints: dict[str, dict],
                         judged: dict[str, list], *, solve, water, remedy,
                         rounds: int = WATER_REREMEDY_ROUNDS
                         ) -> tuple[dict, list[dict]]:
    """`solve()` (`water_after_joint` over `segments`/`joints`), then
    WATER_REREMEDY_DEPENDS_ON: every leg the solve moved into a refusal
    `water(record)` names is offered `remedy(joint_solved_copy, refusal)` ->
    `(solo record, joint)` or None, and the network is solved again from
    every leg's solo record.  `judged` takes each new solo profile.  Returns
    the last solve and the re-remedies made, in order."""
    import copy                                             # noqa: PLC0415
    solo = {sg["id"]: copy.deepcopy(sg) for sg in segments}
    solo_j = copy.deepcopy(joints)
    made: list[dict] = []
    jd = solve()
    for rnd in range(1, rounds + 1):
        if _WATER_REREMEDY_MODE != "reremedy":
            break
        refused = {r["id"] for r in jd["refused"]}
        new: dict[str, tuple[dict, dict]] = {}
        for sg in segments:
            sid = sg["id"]
            if (sid in refused or "writer_water" not in sg
                    or sg["profile_y"] == judged.get(sid)):
                continue
            fw = water(sg)
            if fw["verdict"] != "REFUSED":
                continue
            got = remedy(copy.deepcopy(sg), fw)
            if got is None:
                continue
            rec, _j = got
            note = {"depends_on": list(WATER_REREMEDY_DEPENDS_ON),
                    "round": rnd,
                    "joint_refusal": {k: fw.get(k) for k in (
                        "steps", "drained", "worst_m", "worst_at",
                        "keepout_steps", "pad_steps", "arrival_failures")},
                    "remedy": rec["writer_water"]["remedy"],
                    "length_m": rec.get("length_m")}
            rec["writer_water"]["reremedied_after_joint"] = note
            made.append({"id": sid, **note})
            new[sid] = got
        if not new:
            break
        for sid, (rec, j) in new.items():
            solo[sid], solo_j[sid] = rec, j
            judged[sid] = list(rec["profile_y"])
        for sg in segments:
            sid = sg["id"]
            sg.clear()
            sg.update(copy.deepcopy(solo[sid]))
        joints.clear()
        joints.update(copy.deepcopy(solo_j))
        jd = solve()
    return jd, made


def _rr_writer(limit: float):
    """A writer that refuses a profile over `limit` anywhere: the POI disc
    the joint solve's lift reaches, in miniature."""
    def water(sg):
        over = [k for k, v in enumerate(sg["profile_y"]) if v > limit + 1e-9]
        return {"verdict": "REFUSED" if over else "CLEAR",
                "steps": len(over), "drained": 0,
                "worst_m": round(max((sg["profile_y"][k] - limit
                                      for k in over), default=0.0), 3),
                "worst_at": None, "keepout_steps": len(over)}
    return water


def selftest_water_reremedy() -> int:
    """WATER_REREMEDY_DEPENDS_ON, both ways, on T5 at the temple in
    miniature: T1 comes in at 40.0 and T5 leaves the same ground at 35.0,
    CLEAR alone; the joint solve lifts T5 past the writer's 36.0 (the disc at
    (-427.62, -44.46)), and T5's own alignment moves it 30 m off T1."""
    import copy                                             # noqa: PLC0415
    global _WATER_REREMEDY_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fx = {"T1-portalhub-temple": _jd_fixture(
              "T1-portalhub-temple", [(2.0 * k, 0.0) for k in range(21)],
              [40.0] * 21, pin=(0, 20, 40.0)),
          "T5-temple-dockshore": _jd_fixture(
              "T5-temple-dockshore", [(2.0 * k, 0.0) for k in range(21)],
              [35.0] * 21)}
    moved = _jd_fixture("T5-temple-dockshore",
                        [(2.0 * k, 30.0) for k in range(21)], [35.0] * 21)
    water = _rr_writer(36.0)

    def verdict(remedies: int) -> str:
        segs = [copy.deepcopy(fx[i][0]) for i in fx]
        joints = {i: copy.deepcopy(fx[i][1]) for i in fx}
        for sg in segs:
            sg["writer_water"] = {"verdict": "CLEAR", "remedy": "as routed"}
        judged = {sg["id"]: list(sg["profile_y"]) for sg in segs}
        left = [remedies]

        def remedy(sg, _fw):
            if not left[0]:
                return None
            left[0] -= 1
            rec, j = copy.deepcopy(moved)
            rec["writer_water"] = {"verdict": "CLEAR", "remedy": "alignment 1"}
            return rec, j
        jd, made = remedied_after_joint(
            segs, joints, judged,
            solve=lambda: apply_junction_datums(segs, joints, []),
            water=water, remedy=remedy)
        gone = {r["id"] for r in jd["refused"]}
        still = [sg["id"] for sg in segs if sg["id"] not in gone
                 and sg["profile_y"] != judged[sg["id"]]
                 and water(sg)["verdict"] == "REFUSED"]
        return (f"re-remedied {[m['id'] for m in made]}, refused after the "
                f"joint solve {still}")

    def moded(mode, fn):
        global _WATER_REREMEDY_MODE
        was = _WATER_REREMEDY_MODE
        _WATER_REREMEDY_MODE = mode
        try:
            return fn()
        finally:
            _WATER_REREMEDY_MODE = was

    print("case: T5 lifted by the joint solve into the writer's refusal "
          "(REQUIRED TO RE-REMEDY)")
    want("T5's alignment is taken and nothing is refused", verdict(1),
         "re-remedied ['T5-temple-dockshore'], refused after the joint "
         "solve []")
    want("NEUTER drop (the shipped rule): T5 is refused",
         moded("drop", lambda: verdict(1)),
         "re-remedied [], refused after the joint solve "
         "['T5-temple-dockshore']")
    print("case: a leg whose remedies are spent (REQUIRED TO REFUSE)")
    want("no remedy: refused, the loop ends", verdict(0),
         "re-remedied [], refused after the joint solve "
         "['T5-temple-dockshore']")
    print(f"water re-remedy selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# WATER_CAP_DEPENDS_ON, both ways, on the Pirate68 stathub corridor of
# 2026-09-26 in miniature.  S7 and S13 leave stathub on one line.  T6 crosses
# S7 10 m past S13's end, held at 40.0 by its own bounds.  S13's dock end then
# takes a walked-ray ceiling at 32.0: S13's water, not S7's.
# ---------------------------------------------------------------------------
def selftest_water_cap() -> int:
    """WATER_CAP_DEPENDS_ON, both ways."""
    global _WATER_CAP_MODE
    import copy                                             # noqa: PLC0415
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fx = {"T6-stathub-northcape": _jd_fixture(
              "T6-stathub-northcape", [(50.0, -20.0 + 2.0 * k)
                                       for k in range(21)],
              [40.0] * 21, pri=2, pin=(0, 20, 40.0)),
          "S7-stathub-wtnorth": _jd_fixture(
              "S7-stathub-wtnorth", [(2.0 * k, 0.0) for k in range(31)],
              [35.0] * 31, width=4.0, pri=2, rung=0.12),
          "S13-stathub-dock": _jd_fixture(
              "S13-stathub-dock", [(2.0 * k, 0.0) for k in range(21)],
              [35.0] * 21, width=4.0, pri=2, rung=0.12)}

    def verdict() -> str:
        segs = [copy.deepcopy(fx[i][0]) for i in fx]
        joints = {i: copy.deepcopy(fx[i][1]) for i in fx}
        before = {sg["id"]: copy.deepcopy(sg) for sg in segs}
        uncapped = apply_junction_datums(segs, joints, [])
        jd = junction_with_caps(
            segs, joints, [],
            {"S13-stathub-dock": {k: 32.0 for k in range(15, 21)}}, before,
            {r["id"] for r in uncapped["refused"]})
        return (f"uncapped refuses {[r['id'] for r in uncapped['refused']]}; "
                f"capped refuses {[r['id'] for r in jd['refused']]}, "
                f"ceilings withheld {jd['water_caps_withheld']}")

    def moded(mode):
        global _WATER_CAP_MODE
        was = _WATER_CAP_MODE
        _WATER_CAP_MODE = mode
        try:
            return verdict()
        finally:
            _WATER_CAP_MODE = was

    print("case: S13's water ceiling in the corridor it shares with S7 and T6 "
          "(REQUIRED: S7 is not refused for S13's water)")
    want("the ceiling is withheld; nothing is refused", verdict(),
         "uncapped refuses []; capped refuses [], ceilings withheld "
         "['S13-stathub-dock']")
    want("NEUTER cap=always (the shipped rule): the bystander S7 is refused",
         moded("always"),
         "uncapped refuses []; capped refuses ['S7-stathub-wtnorth'], "
         "ceilings withheld []")
    print(f"water cap selftest: {fails} failed")
    return fails



def junction_rejection(rec: dict) -> dict:
    """The refusal a leg that can agree with no joint profile earns, WITH
    ITS NUMBER: the two legs, the place, the spread and the rungs tried."""
    b = rec["binding"]
    return {
        "why": f"{b['segments'][0]} and {b['segments'][1] if len(b['segments']) > 1 else '-'} "
               f"share ground at {b.get('xz')} at design heights "
               f"{b.get('design_y')} -- a {b.get('spread_m')} m spread "
               f"against a {b.get('allowance_m')} m allowance "
               f"({b.get('reading')}) -- and NO joint profile satisfies both "
               f"legs' own bounds and grade at any rung tried "
               f"({b.get('rungs_tried')}); station {b.get('empty_station')}'s "
               f"interval is empty {b.get('interval_there')}. The junction "
               f"gate WILL refuse whichever of the two is written second; "
               f"this leg is refused here instead, by "
               f"route._refuse_order (lower priority, then narrower, then "
               f"id) -- never by the order it was planned in.",
        "junction_spread_m": b.get("spread_m"),
        "junction_binding": b,
    }



def _station_of(pts, xz) -> int:
    x, z = xz
    d = [(px - x) ** 2 + (pz - z) ** 2 for px, pz in pts]
    return int(np.argmin(d))


def zones_of_segment(seg: dict) -> set[tuple[int, int]]:
    """Every zone whose sample lattice a TERRAIN station of this segment touches.

    `ZoneSystem::GetZone` is floor((c + 32) / 64) per axis, and a zone's 65x65
    lattice includes both edges, so a sample within half a metre of a zone line
    belongs to two zones and both compilers must be written.  Bridged stations
    are excluded: those are Crossings' deck, not my terrain.
    """
    zones: set[tuple[int, int]] = set()
    half = seg["width_m"] / 2.0
    prev = None
    for (x, z), bridged in zip(seg["nodes"], seg["is_bridge"]):
        if bridged:
            prev = None
            continue
        pts = [(x, z)]
        if prev is not None:
            # Fill between stations at 1 m so a 2 m station spacing cannot skip a
            # zone whose corner the ribbon clips.
            px, pz = prev
            n = max(1, int(math.hypot(x - px, z - pz)))
            pts = [(px + (x - px) * k / n, pz + (z - pz) * k / n) for k in range(n + 1)]
        for cx, cz in pts:
            for dx in (-half, 0.0, half):
                for dz in (-half, 0.0, half):
                    zones.add((math.floor((cx + dx + 32.0) / 64.0),
                               math.floor((cz + dz + 32.0) / 64.0)))
        prev = (x, z)
    return zones


# WHERE A PLAN GOES WHEN THE CALLER DID NOT SAY.  `--out` used to default to
# `str(HERE)` -- the repo's own `roads/` directory -- so a bare
# `network.py plan` OVERWROTE the 1.1 MB `segments.yaml` that the live
# Ulfsland build was routed, profiled, stamped and ledgered against.  There was
# no dry mode and no confirmation.  The default is now INERT: a scratch
# directory outside the repo.  Writing into the repo is still possible and
# still one flag -- `--out roads/` -- it is just no longer what happens when
# you forget.
DEFAULT_OUT = Path("/tmp/roads/plan")
LIVE_OUT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# WHERE EVERY COORDINATE THIS PLAN TERMINATES AT CAME FROM.
#
# MEASURED 2026-09-22 on seed Fresh42: 8 of 8 authored installation and
# shoreline nodes carried ULFSLAND'S COORDINATES, VERBATIM, TO THE DECIMAL; 11
# of 20 segments terminated at one; 12,849 of 21,938 m -- 59% of the paved
# metreage -- was fitted to another planet's buildings, inside a run that
# stamped `seed: Fresh42 / world: Fresh42`, reported POI violations 0 and
# exited 0.  NOTHING READ THE OUTPUT BACK, and the header alone cannot: the
# header was correct.
#
# So the document now SAYS where each endpoint came from, and `emit` checks it.
# A guard whose coverage depends on the artefact having VOLUNTEERED a field is
# a guard the artefact can opt out of, which is why this block is emitted for
# every segment rather than for the nodes that happen to have a source.
#
#   source    the artefact ACTUALLY OPENED for THIS seed -- a template where
#             the reader's own resolver takes the seed or the world, never a
#             transcribed path.
#   verbatim  the xz was COPIED out of `source`.  Only this class can be
#             decided by equality, and equality is the whole test: eight
#             coordinates identical to the decimal across two planets is not a
#             distribution, it is a transcription.
#   cites     PROSE.  Never parsed for file names -- see emitscope's note.
# ---------------------------------------------------------------------------
def node_provenance(segments, nodes=None) -> dict:
    """`{node_id: {xz, kind, verbatim, source, cites}}` for every endpoint."""
    nodes = specmod.NODES if nodes is None else nodes
    geo = str(specmod.geography.geography_path(SEED))
    sites = str(worldseed.sites_path(SEED))
    out: dict[str, dict] = {}
    for sg in segments:
        for nid in (sg["from"], sg["to"]):
            if nid in out or nid not in nodes:
                continue
            n = nodes[nid]
            if nid in specmod.DERIVED_GEOGRAPHY:
                src, verbatim = geo, True
            elif nid in specmod.PLACEMENT_OF:
                # The reader resolves `<world>` for the world being built, so
                # no seed can inherit another's directory by transcription.
                src, verbatim = "worlds/<world>/*/placements.yaml", True
            elif nid in specmod.SETTLEMENT_SITES:
                # A road node is the site's APPROACH, derived from the solved
                # centre and not equal to it: wtspawn stands at (110,-104)
                # against sites.yaml's (114,-108).
                src, verbatim = sites, False
            elif nid in specmod.DECLARED_SITES:
                # A person's request (`raw_xz`) sited by stage 8's own module:
                # the node is the measured landing, never the request.
                src, verbatim = sites, False
            else:
                src, verbatim = None, False
            rec: dict = {"xz": [float(n["xz"][0]), float(n["xz"][1])],
                         "kind": n.get("kind"), "verbatim": verbatim,
                         "cites": n.get("provenance") or ""}
            if src:
                rec["source"] = src
            raw = n.get("raw_xz")
            if raw:
                rec["raw_xz"] = [float(raw[0]), float(raw[1])]
            out[nid] = rec
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# THE ZONES THIS PLAN WILL ACTUALLY WRITE, AS OPPOSED TO THE ONES IT DRIVES ON.
#
# `sg["zones"]` is the CARRIAGEWAY set.  `ribbon.main()` builds its write set
# from `segment_zones(..., batter_m=BATTER_MAX_M)`, which is wider, because a
# batter sample lands outside the carriageway by construction.  Every agent who
# took a zone lock off the record column tonight under-declared it:
#
#   MEASURED 2026-09-22 over all 39 segments of roads/segments.yaml at
#   BATTER_MAX_M = 10.5, using ribbon's OWN segment_zones:
#     record-column zones, network-wide   702
#     derived at full batter reach        950
#     segments under-declaring             37 OF 39
#     zones missing from the record       248  -- a 26% under-declaration
#   Three agents hand-derived this on three segments (55->75, 60->82, 6->10)
#   and each read it as a quirk of their own closure.  It cost one real overlap
#   at zone (-5,4), found only because an agent re-derived rather than trusting
#   the column.
#
# THIS IS THE SAME SENTENCE AS THE INSTALLATION NODES ONE COLUMN OVER: an
# emitted artefact DECLARING A SET THAT IS NOT WHAT THE WRITER WRITES.  READ THE
# DECLARATION OFF THE THING THAT WAS USED, NEVER OFF THE THING THAT WAS MEANT.
# And the producer already existed, already took `batter_m`, and nothing at
# emission called it.
#
# `ribbon` is imported LAZILY: it pulls numpy, the census and the applied
# surface, and `network.py` is imported by tools that need none of them.  There
# is no cycle -- `ribbon` does not import `network` -- but there is a cost, and
# it is paid once per emission rather than once per import.
#
# THE DEFERRAL ABOVE WAS WRONG AND IT WAS WRONG IN THE SAME WAY THE COLUMN IS.
# It said `zones` -> `zones_carriageway` "touches structures.py, ribbon.py,
# junction.py and every lock-taker".  MEASURED 2026-09-22 over 137 .py files
# under tools/jumpstart -- 540 bare `zones` tokens, of which 68 are the string
# KEY "zones" -- ALL THREE OF THOSE FILES READ THE COLUMN ZERO TIMES:
#   structures.py  0 key sites (11 prose)
#   ribbon.py      4 key sites, all its OWN write-report fields
#   junction.py    4 key sites, all its OWN report counters
# The only two readers of the column in the tree are `all_zones` below and
# `deviate.py`'s splice print, and neither takes a lock.  A blast radius
# ESTIMATED instead of counted, written into the file as the reason not to act.
#
# THE PARTITION OF THE 68, so the next reader does not re-derive it:
#   carriageway column key sites    5   (3 writes, 2 reads, 3 files)
#   carriageway prose/comment       4
#   carriageway-derived count       1
#   LEDGER zone-control op param   32   -- `zones_generate`/`zones_restore`
#   census artefact root key        2
#   module-local report field      24
# The ledger param is a ZONE-CONTROL set and NOT this column: 557 committed
# ledger lines carry it (549 of ledger/runs/Ulfsland/ledger.jsonl's 5,421 and
# 8 of crossings/out/ledger.jsonl's 47).  A blanket rename would have rewritten
# all of them and meant something different in every one.
#
# THE ON-DISK RENAME IS REFUSED, WITH ITS NUMBER: the column lives in 3
# artefacts, 62 segment records, 1,129 zone entries -- roads/segments.yaml
# (git-TRACKED, 39 segments, 702), roads/preflight.json (git-TRACKED, 17
# segments, 191) and roads/north_segments.yaml (untracked, 6 segments, 236).
# Renaming a key across committed artefacts is a DATA MIGRATION, and a forked
# on-disk format is worse than an ambiguous name.
#
# SO THE RENAME LANDS AT THE BOUNDARY INSTEAD: on disk `zones`, IN MEMORY
# `zones_carriageway`, converted by `structures.carriageway_from_disk` on the
# way in and `structures.carriageway_to_disk` immediately before every dump.
# Past those two functions `zones` DOES NOT EXIST, so a reader reaching for it
# gets a KeyError at its own line rather than a 26%-narrow lock set.  ZERO
# bytes of any committed artefact change.
# ---------------------------------------------------------------------------
ZONES_SCOPE = (
    "`zones_carriageway` per segment (`zones` on disk) is the CARRIAGEWAY set "
    "and IS NOT THE WRITE SET: ribbon.main() writes to segment_zones(..., "
    "batter_m=BATTER_MAX_M), which is wider. MEASURED over this network: 702 "
    "carriageway zones against 950 at batter reach, 37 of 39 segments short, "
    "248 zones missing. TAKE A ZONE LOCK FROM `zones_write` VIA "
    "structures.segment_lock_zones(), NEVER FROM the carriageway column."
)

ZONES_COLUMN_ON_DISK = "zones"
ZONES_COLUMN_IN_MEMORY = "zones_carriageway"


def _each_segment(doc):
    """`doc` as a list of segment records, whether it is a doc or one record."""
    if isinstance(doc, dict) and isinstance(doc.get("segments"), list):
        return [s for s in doc["segments"] if isinstance(s, dict)]
    return [doc] if isinstance(doc, dict) else []


def carriageway_from_disk(doc):
    """Disk name -> memory name, in place.  `zones` STOPS EXISTING.

    Called by `structures.load_segment_doc`, which is the sanctioned parse for
    a plan file.  There is deliberately NO fallback and NO alias: an alias is
    the ambiguity with extra steps, and the whole point is that a reader who
    reaches for `zones` gets a KeyError at its own line instead of a set that
    is 26% narrow on 37 of 39 segments.
    """
    for s in _each_segment(doc):
        if ZONES_COLUMN_ON_DISK in s:
            s[ZONES_COLUMN_IN_MEMORY] = s.pop(ZONES_COLUMN_ON_DISK)
    return doc


def carriageway_to_disk(doc):
    """Memory name -> disk name, in place, AT THE SERIALISATION BOUNDARY ONLY.

    Every emitter of a segments document calls this immediately before its
    `yaml.safe_dump`, so no artefact ever carries `zones_carriageway` on disk
    and the 1,129 zone entries already committed across segments.yaml,
    preflight.json and north_segments.yaml stay readable by every tool that
    predates this boundary.  A forked on-disk format would be worse than the
    ambiguous name this replaces.
    """
    for s in _each_segment(doc):
        if ZONES_COLUMN_IN_MEMORY in s:
            s[ZONES_COLUMN_ON_DISK] = s.pop(ZONES_COLUMN_IN_MEMORY)
    return doc


def write_zones(segments) -> list[list[int]]:
    """Every zone this plan will TOUCH, at full batter reach, sorted.

    Derived through `ribbon.segment_zones` -- the function `ribbon.main()`
    itself writes from -- so this cannot drift away from the writer the way a
    second definition of "which zones" would.
    """
    import ribbon as ribbonmod                             # noqa: PLC0415
    out: set[tuple[int, int]] = set()
    for sg in segments:
        out.update(ribbonmod.segment_zones(
            sg["nodes"], sg["is_bridge"], sg["width_m"],
            batter_m=ribbonmod.BATTER_MAX_M))
    return [list(z) for z in sorted(out)]


def require_write_zones_declared(name: str, doc, *, act: str = "WRITE") -> None:
    """REFUSE a plan document that does not declare the zones it will write.

    The refusal is the load-bearing half: without it the block is a courtesy a
    later edit can drop, and the declaration goes stale exactly the way the
    carriageway column did -- silently, and only visible to whoever re-derives.
    A DECLARED WRITE SET NARROWER THAN THE DERIVED ONE IS THE DEFECT, so both
    the absence and the narrowing are refused.

    `act` names WHICH SIDE is refusing, because this runs on BOTH now.  It ran
    at emission only until 2026-09-22, and that is precisely why
    `roads/segments.yaml` -- the artefact every tool in `roads/` opens -- sat
    with no `zones_write` key at all while the class was reported closed: a
    check pointed at the output while the input goes unguarded.
    `structures.load_segment_doc` calls it with act="READ".
    """
    if not isinstance(doc, dict) or "segments" not in doc:
        return
    declared = doc.get("zones_write")
    if not isinstance(declared, list):
        raise SystemExit(
            f"REFUSING TO {act} {name}: it declares no `zones_write`, so the "
            f"only zone set in it is the CARRIAGEWAY column -- which is "
            f"narrower than what ribbon writes on 37 of 39 segments of this "
            f"network, by 248 zones. A consumer taking a lock from it "
            f"under-declares, and that is how zone (-5,4) was written by two "
            f"claims. {ZONES_SCOPE}")
    got = {tuple(z) for z in declared}
    want = {tuple(z) for z in write_zones(doc["segments"])}
    if not want <= got:
        missing = sorted(want - got)
        raise SystemExit(
            f"REFUSING TO {act} {name}: its declared `zones_write` is NARROWER "
            f"than the set ribbon will actually write -- {len(missing)} zones "
            f"missing, e.g. {missing[:8]}. A declaration narrower than the act "
            f"it describes is the defect this block exists to close.")


# The key each emitted document's seed-varying coordinates live under, or None
# where this stage emits none.  A document not named here is still scope- and
# citation-checked; naming it only adds rung C.
#
# KEYED BY STEM, NOT BY FILE NAME.  The on-disk name now carries the seed for
# every seed but the default (`roads/spec.py::road_plan_names`), so a table
# keyed by `segments.yaml` would silently stop adding rung C the moment the
# file became `segments.Fresh42.yaml` -- a guard whose coverage depends on a
# file name that has started varying is a guard that turns itself off.
EMIT_COORDINATES = {"segments": "node_provenance", "crossings": None}


def emit(out_dir: Path, docs: dict[str, str], *, dry_run: bool) -> list[str]:
    """Write the plan documents, or under `--dry-run` write NOTHING and say so.

    `docs` is keyed by STEM (`segments`, `crossings`) and the FILE NAME is
    asked of `roads/spec.py::road_plan_names` for THIS seed.  The two names
    used to be literals here, so both ends of the road plan were keyed by
    nothing: `--out roads/` on any seed overwrote the 1.1 MB plan the live
    Ulfsland build was routed, profiled, stamped and ledgered against, and
    `pipeline.py` could only report PRODUCER_SLOT_MISSING and refuse to run
    stage 11 at all.  The resolver is the same one the readers call, so the
    producer and the reader cannot disagree about the name.

    A dry mode whose report is "I would write segments.yaml" is worth very
    little -- the question a caller has is whether the plan it just computed
    DIFFERS from the file on disk.  So the report is the sha256 of each
    document beside the sha256 of the file that is there now, which answers
    that without touching a byte.
    """
    names = worldseed.road_plan_names(SEED)
    lines: list[str] = []
    for stem, text in docs.items():
        name = names[stem]
        dst = out_dir / name
        # READ THE OUTPUT BACK, PARSED, BEFORE IT IS A FILE.  `require_scope`
        # refuses a foreign artefact on the way in; this is the same refusal
        # pointed the other way, which until now existed in exactly one place
        # in this tree (`field.py::request` re-checking PatchScan's own output).
        # It runs under --dry-run too: a dry run that reports the sha256 of a
        # document fitted to another planet has answered the wrong question.
        parsed = yaml.safe_load(text)
        emitscope.require_emission_scope(
            name, dst, parsed, seed=SEED, world=WORLD,
            coordinates=EMIT_COORDINATES[stem], root=JUMPSTART)
        # AND THE SECOND THING THIS DOCUMENT MUST NOT LIE ABOUT: the zones it
        # will write.  Same rung as the header -- a declaration checked against
        # what the writer actually does, not against what this module meant.
        require_write_zones_declared(name, parsed)
        new = hashlib.sha256(text.encode()).hexdigest()
        old = (hashlib.sha256(dst.read_bytes()).hexdigest()
               if dst.exists() else None)
        if dry_run:
            state = ("would CREATE" if old is None else
                     "IDENTICAL, no write needed" if old == new else
                     "would OVERWRITE with different content")
            lines.append(f"DRY RUN {state}: {dst}\n"
                         f"        planned sha256 {new}\n"
                         f"        on disk sha256 {old or '-'}")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            dst.write_text(text)
            lines.append(f"-> {dst}  sha256 {new}"
                         + ("" if old is None or old == new
                            else f"  (replaced {old})"))
    return lines


def selftest() -> int:
    """PAD_CLAIMS_DEPENDS_ON, both directions, on the measured shapes.

    Offline: the claims are built in code, verbatim from what the tools
    wrote, so the check neither reads nor writes a world."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def neutered(value, fn):
        global _PAD_SOURCE
        was = _PAD_SOURCE
        _PAD_SOURCE = value
        try:
            return fn()
        finally:
            _PAD_SOURCE = was

    half = routemod.footprint_half_m(WIDTH_M["trunk"])
    # Verbatim: Fresh42 deepnorth-sandbox#sandbox-portal-hub, solved
    # (-220.5, -411.5), flatten.pad_extent 34.1 x 34.1; T1's first station
    # (-220.1, -410.8) on the stage-9 route of 2026-09-24 22:09.
    hub = {"seq": None, "name": "sandbox-portal-hub",
           "site_id": "sandbox-portal-hub", "planned": True,
           "x": -220.5, "z": -411.5, "hw": 17.05, "hd": 17.05}
    t1 = (-220.1, -410.8)
    print("case: a FRESH ledger (0 written pads) and T1's first station "
          "(REQUIRED TO REFUSE)")
    want("the planned portal-hub pad holds T1's first station",
         claims_holding(merge_pad_claims([], [hub]), *t1, half),
         ["sandbox-portal-hub"])
    want("NEUTER source=written (the shipped key): nothing holds it",
         neutered("written", lambda: claims_holding(
             merge_pad_claims([], [hub]), *t1, half)), [])
    x0, z0, cell = -300.0, -500.0, 4.0
    i, j = int(round((t1[1] - z0) / cell)), int(round((t1[0] - x0) / cell))
    want("the A* cell under it is forbidden",
         bool(pad_forbid_mask(merge_pad_claims([], [hub]), x0, z0, cell, 64,
                              half)[i, j]), True)
    want("NEUTER source=written: the A* cell under it is open",
         neutered("written", lambda: bool(pad_forbid_mask(
             merge_pad_claims([], [hub]), x0, z0, cell, 64, half)[i, j])),
         False)
    # Verbatim from ledger/writer.py::_pad_footprint's measurement: stenvik-
    # cottage-2 reserved (484.7, 901.7) and was then NUDGED to (477.7, 902.7),
    # where its pad was written.  The written pad is the truth; the planned
    # rectangle at the abandoned spot is ground nobody will build on.
    planned_c2 = {"seq": None, "name": "stenvik-cottage-2",
                  "site_id": "stenvik-cottage-2", "planned": True,
                  "x": 484.7, "z": 901.7, "hw": 5.0, "hd": 5.0}
    written_c2 = dict(planned_c2, seq=265, planned=False, x=477.7, z=902.7)
    probe = (489.0, 901.7)
    print("case: a pad already WRITTEN elsewhere supersedes its plan "
          "(REQUIRED TO ACCEPT the abandoned spot)")
    want("a station on the abandoned planned spot is clear",
         claims_holding(merge_pad_claims([written_c2], [planned_c2]),
                        *probe, half), [])
    want("NEUTER source=unmerged: the abandoned plan still refuses it",
         neutered("unmerged", lambda: claims_holding(
             merge_pad_claims([written_c2], [planned_c2]), *probe, half)),
         ["stenvik-cottage-2"])
    fails += selftest_junction_datum()
    fails += selftest_junction_conflict()
    fails += selftest_water_cap()
    fails += selftest_water_reremedy()
    fails += selftest_co_paved()
    fails += selftest_writer_water()
    fails += selftest_plan_pad_face()
    fails += selftest_plan_refusal()
    fails += selftest_walked_keepout()
    fails += selftest_census_band()
    fails += selftest_drowned_terminus()
    fails += selftest_census_adopt()
    fails += selftest_terminal_crossing()
    fails += selftest_crossing_banks()
    fails += selftest_abutment_bench()
    fails += selftest_abutment_released()
    fails += selftest_terminal_landing_plan()
    fails += selftest_span_bed()
    fails += selftest_owner_cause()
    fails += selftest_pad_cliff_verdict()
    fails += selftest_arrival()
    fails += selftest_arrival_reach()
    fails += selftest_foreign_nodes()
    fails += selftest_node_anchor()
    fails += selftest_orphan_join()
    fails += selftest_bridge_approaches()
    fails += selftest_terminal_reach()
    fails += selftest_span_reprice()
    fails += selftest_adjacency_reroute()
    print(f"\n{fails} case(s) failed")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# JUNCTION_DATUM_DEPENDS_ON, EACH TERM BOTH WAYS, ON IN-MEMORY FIXTURES.
#
# The temple is VERBATIM from the Fresh42 plan the build was handed
# (`roads/segments.Fresh42.yaml`, route of 2026-09-25 01:44): T1 stations
# 286-296, T3 0-15, T5 and T13 0-11, S5 0-10 -- nodes, profile_y and
# terrain_y to the digit.  It is the ground where T3, T5 and T13 were refused
# at step 4, (-27.29, -37.17), 1.629 / 1.862 / 1.862 m against T1.  The
# other fixtures are synthetic and carry nothing of any world: each isolates
# ONE term, so dropping it brings back exactly the state it exists to prevent
# while a fixture it does not govern survives.
# ---------------------------------------------------------------------------
_TEMPLE = {
    "T1-portalhub-temple": dict(pri=1, width=6.0, rung=0.08, pts=[
        (-37.54, -44.24), (-36.13, -42.83), (-34.71, -41.41), (-33.3, -40.0),
        (-31.88, -38.58), (-30.47, -37.17), (-29.06, -35.76),
        (-27.64, -34.34), (-26.23, -32.93), (-24.81, -31.51), (-24.1, -30.8)],
        y=[41.884, 41.789, 41.636, 41.627, 41.627, 41.627, 41.613, 41.613,
           41.613, 41.541, 41.541],
        t=[39.833, 39.803, 39.657, 39.466, 39.752, 40.118, 40.094, 40.14,
           40.571, 40.454, 40.666]),
    "T3-temple-meadhall": dict(pri=1, width=6.0, rung=0.08, pts=[
        (-24.1, -30.8), (-24.1, -32.8), (-24.1, -34.8), (-24.1, -36.8),
        (-24.1, -38.8), (-22.69, -37.39), (-21.27, -35.97), (-20.1, -35.14),
        (-20.1, -37.14), (-20.1, -39.14), (-20.1, -41.14), (-19.86, -42.56),
        (-18.44, -41.14), (-17.03, -39.73), (-15.61, -38.31), (-14.2, -36.9)],
        y=[40.399, 40.255, 40.174, 40.014, 39.854, 39.713, 39.603, 39.565,
           39.44, 39.303, 39.187, 39.072, 39.23, 39.07, 39.202, 39.211],
        t=[40.666, 40.185, 40.115, 39.89, 39.682, 39.56, 39.482, 39.348,
           39.328, 39.261, 39.203, 39.056, 39.3, 39.051, 39.202, 39.211]),
    "T5-temple-dockshore": dict(pri=1, width=6.0, rung=0.08, pts=[
        (-24.1, -30.8), (-24.1, -32.8), (-24.1, -34.8), (-24.1, -36.8),
        (-24.1, -38.8), (-24.1, -40.8), (-24.1, -42.8), (-24.1, -44.8),
        (-24.1, -46.8), (-25.51, -48.21), (-26.93, -49.63), (-28.34, -51.04)],
        y=[40.261, 40.101, 39.941, 39.781, 39.621, 39.461, 39.301, 39.273,
           39.273, 39.273, 39.273, 39.273],
        t=[40.666, 40.185, 40.115, 39.89, 39.682, 39.31, 38.896, 38.342,
           37.868, 37.92, 38.093, 38.593]),
    "S5-temple-southcape": dict(pri=2, width=4.0, rung=0.12, pts=[
        (-28.1, -26.8), (-28.1, -28.8), (-28.1, -30.8), (-29.51, -32.21),
        (-30.93, -33.63), (-32.34, -35.04), (-33.76, -36.46),
        (-35.17, -37.87), (-36.59, -39.29), (-38.0, -40.7), (-39.41, -42.11)],
        y=[41.89, 41.65, 41.628, 41.628, 41.628, 41.628, 41.628, 41.674,
           41.831, 41.961, 42.113],
        t=[42.038, 41.501, 40.67, 40.511, 40.406, 40.094, 40.386, 40.45,
           40.525, 40.543, 40.607]),
}
# T13 shares its first twelve stations with T5 on the plan, to the digit.
_TEMPLE["T13-temple-harboursouth"] = dict(_TEMPLE["T5-temple-dockshore"])


def _jd_fixture(sid: str, pts, y, *, width: float = 6.0, pri: int = 1,
                rung: float = 0.08, t=None, pin=None) -> tuple[dict, dict]:
    """One leg as `build_segment` hands it on: its record and its joint side
    channel.  `t` is the ground (the clamp box is +/- CUT_FILL_MAX about it);
    `pin = (k0, k1, H)` holds stations k0..k1 at H, as an abutment's level
    run is held."""
    pts = [(float(x), float(z)) for x, z in pts]
    n = len(pts)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(
        np.diff([p[0] for p in pts]), np.diff([p[1] for p in pts])))])
    y = np.asarray(y, dtype=np.float64)
    t = y.copy() if t is None else np.asarray(t, dtype=np.float64)
    lo = t - routemod.CUT_FILL_MAX
    hi = t + routemod.CUT_FILL_MAX
    if pin:
        lo[pin[0]:pin[1] + 1] = pin[2]
        hi[pin[0]:pin[1] + 1] = pin[2]
    free = np.zeros(n, dtype=bool)
    cls = "trunk" if width >= 6.0 else "spur"
    seg = {"id": sid, "cls": cls, "pri": pri, "width_m": width,
           "grade_limit": rung, "grade_relaxed_to": None,
           "grade_relaxed_by": None,
           **profile_stats(y, s, t, t, t, free),
           "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
           "profile_y": [round(float(v), 3) for v in y],
           "terrain_y": [round(float(v), 3) for v in t],
           "is_bridge": [False] * n}
    joint = {"s": s, "t": t, "lo": lo, "hi": hi, "free": free,
             "tw_lo": t, "tw_hi": t, "spans": [], "cand": None,
             "rung": rung, "y0": y,
             "ladder": [g for g in (0.08, 0.12, 0.18, 0.25) if g >= rung]}
    return seg, joint


def _jd_net(fx: dict, *, pads=(), depends_on=None, order=None):
    """Solve a fixture network; return the records after and the solve."""
    import copy                                             # noqa: PLC0415
    ids = list(order or fx)
    segs = [copy.deepcopy(fx[i][0]) for i in ids]
    joints = {i: copy.deepcopy(fx[i][1]) for i in ids}
    res = apply_junction_datums(segs, joints, list(pads),
                                depends_on=depends_on)
    gone = {r["id"] for r in res["refused"]}
    return [sg for sg in segs if sg["id"] not in gone], res


def _jd_gate_at(segs, x: float, z: float, pads=()) -> str:
    """The BUILD'S junction gate at one point: every road/road reading
    there, and its verdict."""
    J = routemod._junction()
    rep = J.gate(J.ribbons_from_segments({"segments": segs}, "fixture"),
                 list(pads))
    rows = sorted((r for r in rep["pairs"]
                   if abs(r["xz"][0] - x) <= 0.01
                   and abs(r["xz"][1] - z) <= 0.01 and r["b_kind"] == "ribbon"),
                  key=lambda r: (r["a"], r["b"]))
    if not rows:
        return "NO READING"
    bad = any(r["impassable"] for r in rows)
    return (("REFUSED " if bad else "AGREED ")
            + ", ".join(f"{r['a'].split('-')[0]}/{r['b'].split('-')[0]} "
                        f"{r['spread_m']:.3f}" for r in rows))


def _jd_gate(segs, pads=()) -> str:
    """The build's junction gate over the whole fixture."""
    J = routemod._junction()
    rep = J.gate(J.ribbons_from_segments({"segments": segs}, "fixture"),
                 list(pads))
    if not rep["refusals"]:
        return "AGREED"
    w = rep["refusals"][0]
    return (f"REFUSED {len(rep['refusals'])} pair(s), worst {w['a']} vs "
            f"{w['b']} {w['spread_m']:.3f} m")


def _jd_boundary(segs) -> str:
    """The writer's claim boundary between legs over the whole fixture."""
    grade, batter, lattice = writer_batter()
    legs = sorted(({"id": sg["id"], "nodes": sg["nodes"],
                    "is_bridge": sg["is_bridge"], "width_m": sg["width_m"],
                    "y0": np.asarray(sg["profile_y"], dtype=np.float64),
                    "rung": sg["grade_limit"]} for sg in segs),
                  key=lambda lg: lg["id"])
    rd = routemod._joint_readings(
        legs, [], ("the_writers_claim_boundary_between_legs",),
        grade=grade, batter_m=batter, lattice_m=lattice)
    ea, _eu = routemod._excess([lg["y0"] for lg in legs], rd)
    over = ea > routemod.JUNCTION_DATUM_EPS_M
    if not over.any():
        return "JOINABLE"
    return (f"UNJOINABLE {int(over.sum())} edge(s), worst "
            f"{float(ea.max()):.3f} m past the slide limit")


# ---------------------------------------------------------------------------
# route.CO_PAVED_DEPENDS_ON, both ways, with Fresh42 drive 4's S2 / S3 heights
# at (872,349) / (873,349) in miniature: leg A (S2) along z = 0 steps its own
# profile 39.7834 -> 39.0489 between x = 20 and 21; leg B (S3) runs 2.9 m
# off it at 38.9572 from x = 21, so B's bench covers (21,0) -- co-paved,
# 0.092 m under A -- and not (20,0).  B written after A re-paves (21,0).
# ---------------------------------------------------------------------------
def _cp_legs():
    a = {"id": "S2-fixture", "nodes": [[float(x), 0.0] for x in range(0, 41, 2)],
         "is_bridge": [False] * 21, "width_m": 4.0,
         "y": [39.7834 if x <= 20 else 39.0489 for x in range(0, 41, 2)]}
    b = {"id": "S3-fixture", "nodes": [[float(x), 2.9] for x in range(21, 61, 2)],
         "is_bridge": [False] * 20, "width_m": 4.0, "y": [38.9572] * 20}
    return [a, b]


def selftest_co_paved() -> int:
    """route.CO_PAVED_DEPENDS_ON, both ways."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def worst(mode) -> float:
        """The worst plan step over its allowance on the edge (20,0)-(21,0)
        among the cross-leg pairs the datum reads, 0.0 when none is read."""
        was = routemod._CROSS_CLAIM_MODE
        routemod._CROSS_CLAIM_MODE = mode
        try:
            legs = _cp_legs()
            e = routemod.cross_claim_lattice(
                legs, grade=ribbonmod.BATTER_GRADE,
                batter_m=ribbonmod.BATTER_MAX_M, lattice_m=1.0)
        finally:
            routemod._CROSS_CLAIM_MODE = was
        arcs = [np.asarray(ribbonmod.node_arcs(lg["nodes"], lg["is_bridge"]))
                for lg in legs]
        out = 0.0
        for k in range(len(e["s1"])):
            ends = {(round(float(e["x1"][k])), round(float(e["z1"][k]))),
                    (round(float(e["x2"][k])), round(float(e["z2"][k])))}
            if ends != {(20, 0), (21, 0)}:
                continue
            ys = []
            for li, s in ((int(e["la"][k]), e["s1"][k]),
                          (int(e["lb"][k]), e["s2"][k])):
                k0, k1, t = routemod._feet(arcs[li], s)
                y = legs[li]["y"]
                ys.append(float(y[int(k0)] * (1 - t) + y[int(k1)] * t))
            allow = ribbonmod.BATTER_GRADE * (e["run1"][k] + e["run2"][k] + 1.0)
            out = max(out, abs(ys[0] - ys[1]) - allow)
        return round(out, 4)

    print("case: S3's bench re-paves a sample S2 paves (REQUIRED: the datum "
          "reads the step the later write leaves)")
    want("every paver: the 0.8262 m step is read, 0.0449 over the allowance",
         worst("every_paver"), 0.0449)
    want("NEUTER claim=winner (the shipped key): the datum reads nothing "
         "there", worst("winner"), 0.0)
    print(f"co-paved selftest: {fails} failed")
    return fails


def selftest_junction_datum() -> int:
    """JUNCTION_DATUM_DEPENDS_ON, every term in each direction."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    full = routemod.JUNCTION_DATUM_DEPENDS_ON

    def drop(term):
        return tuple(q for q in full if q != term)

    temple = {i: _jd_fixture(i, v["pts"], v["y"], width=v["width"],
                             pri=v["pri"], rung=v["rung"], t=v["t"])
              for i, v in _TEMPLE.items()}
    lone = _jd_fixture("Z-lone", [(500.0 + 2 * k, 500.0) for k in range(21)],
                       [60.0 + 0.05 * k for k in range(21)])
    temple_net = dict(temple, **{"Z-lone": lone})
    at = (-27.29, -37.17)
    design = [temple[i][0] for i in temple]

    print("case: the temple AS EACH LEG WAS FITTED ALONE, read by the "
          "build's own junction gate (REQUIRED TO REFUSE)")
    want("at (-27.29, -37.17)", _jd_gate_at(design, *at),
         "REFUSED T1/T13 1.862, T1/T3 1.629, T1/T5 1.862")

    print("case: the temple SOLVED JOINTLY at plan time (REQUIRED TO AGREE)")
    segs, res = _jd_net(temple_net)
    want("at (-27.29, -37.17)", _jd_gate_at(segs, *at).split(" ")[0],
         "AGREED")
    want("the build's gate over the whole fixture", _jd_gate(segs), "AGREED")
    want("the writer's claim boundary", _jd_boundary(segs), "JOINABLE")
    want("every leg that meets was joined",
         sorted({v["verdict"] for i, v in res["legs"].items()
                 if i != "Z-lone"}), ["JOINED"])

    def node(segs):
        """The temple node (-24.1, -30.8): T1's last station, T3's, T5's
        and T13's first -- the four legs that MEET there."""
        by = {sg["id"].split("-")[0]: sg["profile_y"] for sg in segs}
        h = [by["T1"][-1], by["T3"][0], by["T5"][0], by["T13"][0]]
        return f"spread {max(h) - min(h):.3f} m, heights {h}"
    want("as fitted alone, the four legs at their shared node",
         node(design).split(",")[0], "spread 1.280 m")
    want("ONE DATUM at the shared node", node(segs).split(",")[0],
         "spread 0.000 m")
    want("the datum each meeting leg was held to",
         sorted({v["datum"].split(":")[0] for i, v in res["legs"].items()
                 if i != "Z-lone"}), ["ONE DATUM (lambda 0)"])
    lone_after = next(sg for sg in segs if sg["id"] == "Z-lone")
    same = (yaml.safe_dump(plain(lone_after), sort_keys=False)
            == yaml.safe_dump(plain(lone[0]), sort_keys=False))
    want("SURVIVOR: a leg meeting nothing",
         f"{res['legs']['Z-lone']['verdict']}, record "
         f"{'IDENTICAL' if same else 'CHANGED'}", "UNSHARED, record IDENTICAL")

    print("case: NEUTER -- no joint solve (the plan the build was handed)")
    segs0, res0 = _jd_net(temple_net, depends_on=())
    want("the disagreement RETURNS at (-27.29, -37.17)",
         _jd_gate_at(segs0, *at),
         "REFUSED T1/T13 1.862, T1/T3 1.629, T1/T5 1.862")
    lone0 = next(sg for sg in segs0 if sg["id"] == "Z-lone")
    want("SURVIVOR under the neuter: the leg meeting nothing",
         "IDENTICAL" if yaml.safe_dump(plain(lone0), sort_keys=False)
         == yaml.safe_dump(plain(lone[0]), sort_keys=False) else "CHANGED",
         "IDENTICAL")

    print("case: ORDER -- the same network handed in two orders")
    fwd = list(temple_net)
    rev = fwd[::-1]

    def profiles(order, dep=None):
        s, _r = _jd_net(temple_net, order=order, depends_on=dep)
        return {sg["id"]: sg["profile_y"] for sg in s}
    want("every_participants_own_design_height: two orders",
         "IDENTICAL" if profiles(fwd) == profiles(rev) else "DIFFERENT",
         "IDENTICAL")
    nf = profiles(fwd, drop("every_participants_own_design_height"))
    nr = profiles(rev, drop("every_participants_own_design_height"))
    differ = sorted(i for i in nf if nf[i] != nr.get(i))
    want("NEUTER (each leg pinned to the legs before it -- the build's "
         "rule): the answer depends on who went first",
         f"DIFFERENT on {differ}" if differ else "IDENTICAL",
         "DIFFERENT on ['S5-temple-southcape', 'T1-portalhub-temple', "
         "'T13-temple-harboursouth', 'T3-temple-meadhall', "
         "'T5-temple-dockshore']")

    print("case: the_writers_claim_boundary_between_legs -- two trunks 8.0 m "
          "apart, shoulders touching, 1.5 m apart in height; the gate's "
          "carriageway band misses them by 2.0 m")
    sh = {"SH-a": _jd_fixture("SH-a", [(2.0 * k, 0.0) for k in range(21)],
                              [50.0] * 21),
          "SH-b": _jd_fixture("SH-b", [(2.0 * k, 8.0) for k in range(21)],
                              [51.5] * 21)}
    # The edge COUNT is the lattice population the claim model reads
    # (route.CO_PAVED_DEPENDS_ON grows it); the verdict and the worst step
    # are the measurement.
    def uncounted(v: str) -> str:
        head, _sep, tail = v.partition(" edge(s)")
        return (head.split(" ")[0] + " edge(s)" + tail) if _sep else v
    want("as fitted alone",
         uncounted(_jd_boundary([v[0] for v in sh.values()])),
         "UNJOINABLE edge(s), worst 0.719 m past the slide limit")
    want("solved jointly", _jd_boundary(_jd_net(sh)[0]), "JOINABLE")
    want("NEUTER: the shoulder step RETURNS",
         uncounted(_jd_boundary(_jd_net(sh, depends_on=drop(
             "the_writers_claim_boundary_between_legs"))[0])),
         "UNJOINABLE edge(s), worst 0.719 m past the slide limit")
    want("SURVIVOR under the same neuter: the temple, gate",
         _jd_gate(_jd_net(temple, depends_on=drop(
             "the_writers_claim_boundary_between_legs"))[0]), "AGREED")

    print("case: the_junction_gates_own_pairs -- two trunks sharing one "
          "centreline for 20 m, 1.5 m apart in height; the nearest-claim "
          "lattice gives every shared sample to one of them")
    co = {"CO-a": _jd_fixture("CO-a", [(2.0 * k, 0.0) for k in range(21)],
                              [50.0] * 21),
          "CO-b": _jd_fixture("CO-b", [(2.0 * k, 0.0) for k in range(11)]
                              + [(20.0, 2.0 * k) for k in range(1, 11)],
                              [51.5] * 21)}
    want("as fitted alone", _jd_gate([v[0] for v in co.values()])
         .split(",")[0], "REFUSED 14 pair(s)")
    want("solved jointly", _jd_gate(_jd_net(co)[0]), "AGREED")
    # Every paver on the claim lattice (route.CO_PAVED_DEPENDS_ON) ALSO reads
    # this co-paved centreline, so the gate term is isolated with the
    # lattice at its winner-only key.
    was_cc = routemod._CROSS_CLAIM_MODE
    routemod._CROSS_CLAIM_MODE = "winner"
    try:
        gate_neut = _jd_gate(_jd_net(co, depends_on=drop(
            "the_junction_gates_own_pairs"))[0]).split(" ")[0]
    finally:
        routemod._CROSS_CLAIM_MODE = was_cc
    want("NEUTER: the gate's refusal RETURNS", gate_neut, "REFUSED")
    want("SURVIVOR under the same neuter: the shoulders, claim boundary",
         _jd_boundary(_jd_net(sh, depends_on=drop(
             "the_junction_gates_own_pairs"))[0]), "JOINABLE")

    print("case: pad_datums -- a road leaving a levelled pad (datum 50.0) "
          "is crossed 5 m out by a road 4.0 m higher")
    pad = {"kind": "pad", "id": "PAD", "label": "PAD", "role": "site_pad",
           "seq": None, "x": -10.0, "z": 0.0, "hw": 10.0, "hd": 10.0,
           "datum_y": 50.0, "source": "fixture"}
    pd = {"PD-a": _jd_fixture("PD-a", [(1.0 + 2.0 * k, 0.0)
                                       for k in range(21)], [50.0] * 21),
          "PD-b": _jd_fixture("PD-b", [(5.0, -20.0 + 2.0 * k)
                                       for k in range(21)], [54.0] * 21)}
    want("solved jointly: the build's gate WITH the pad",
         _jd_gate(_jd_net(pd, pads=[pad])[0], [pad]), "AGREED")
    # THE PAIR, NOT THE COUNT: PD-b's level shoulder passes 1.0 m from the
    # pad 4.0 m above it, which `junction.PAD_GAP_DEPENDS_ON` reads too, so
    # the neutered solve refuses more than PD-a; the arm is PD-a's own pair.
    J = routemod._junction()
    nd = _jd_net(pd, pads=[pad], depends_on=drop("pad_datums"))[0]
    want("NEUTER: the road is dragged off the pad's datum",
         sorted({(r["a"], r["b"]) for r in J.gate(
             J.ribbons_from_segments({"segments": nd}, "fixture"),
             [pad])["refusals"]}),
         [("PD-a", "PAD"), ("PD-b", "PAD")])
    want("SURVIVOR under the same neuter: the temple, gate",
         _jd_gate(_jd_net(temple, depends_on=drop("pad_datums"))[0]),
         "AGREED")

    print("case: each_legs_own_bounds_and_branches -- a road crossed inside "
          "the 6 m level run Crossings owns at 50.0 by a road 1.5 m higher")
    ob = {"OB-a": _jd_fixture("OB-a", [(2.0 * k, 0.0) for k in range(21)],
                              [50.0] * 21, pin=(0, 3, 50.0)),
          "OB-b": _jd_fixture("OB-b", [(2.0, -20.0 + 2.0 * k)
                                       for k in range(21)], [51.5] * 21)}

    def level_run(segs):
        a = next(sg for sg in segs if sg["id"] == "OB-a")
        v = sorted(set(a["profile_y"][:4]))
        return "HELD at 50.000" if v == [50.0] else f"MOVED to {v}"
    s_ob = _jd_net(ob)[0]
    want("solved jointly: the level run", level_run(s_ob), "HELD at 50.000")
    want("solved jointly: the build's gate", _jd_gate(s_ob), "AGREED")
    want("NEUTER: the level run is bought", level_run(_jd_net(
        ob, depends_on=drop("each_legs_own_bounds_and_branches"))[0])
        .split(" ")[0], "MOVED")
    want("SURVIVOR under the same neuter: the temple, gate",
         _jd_gate(_jd_net(temple, depends_on=drop(
             "each_legs_own_bounds_and_branches"))[0]), "AGREED")

    print("case: NO JOINT PROFILE -- two level runs, 2.0 m apart, meeting "
          "(REQUIRED TO REFUSE, WITH ITS NUMBER)")
    rx = {"RX-a": _jd_fixture("RX-a", [(2.0 * k, 0.0) for k in range(21)],
                              [50.0] * 21, pin=(0, 3, 50.0)),
          "RX-b": _jd_fixture("RX-b", [(2.0, 2.0 * k) for k in range(21)],
                              [52.0] * 21, width=4.0, pri=2, rung=0.12,
                              pin=(0, 3, 52.0))}
    s_rx, r_rx = _jd_net(rx)
    b = r_rx["refused"][0]["binding"] if r_rx["refused"] else {}
    want("the refusal", (f"REFUSED {[r['id'] for r in r_rx['refused']]}: "
                         f"{b.get('segments')} spread {b.get('spread_m')} m "
                         f"vs {b.get('allowance_m')} m, rungs "
                         f"{b.get('rungs_tried')}"),
         "REFUSED ['RX-b']: ['RX-a', 'RX-b'] spread 2.0 m vs 0.7813 m, "
         "rungs {'RX-a': [0.08, 0.12, 0.18, 0.25], 'RX-b': [0.12, 0.18, "
         "0.25]}")
    want("SURVIVOR: the leg it was refused against",
         [sg["id"] for sg in s_rx], ["RX-a"])
    return fails

# ---------------------------------------------------------------------------
# route.JUNCTION_CONFLICT_DEPENDS_ON, both ways, on the Pirate68 cascade of
# 2026-09-26 in miniature.  T4 arrives on complete-station-hub (datum 31.85)
# at 36.28, and its own clamp box holds its end at >= 35.238, so no profile
# meets the pad's datum.  S13 crosses T4 10 m before its end, 5.903 m higher
# (the spread S13 was refused for at (505,1129)).  That crossing is a
# disagreement the two legs CAN settle between their own boxes.  Removing S13
# leaves T4's end exactly as empty as before.
# ---------------------------------------------------------------------------
_JC_PAD = {"kind": "pad", "id": "complete-station-hub",
           "label": "complete-station-hub", "role": "installation_pad",
           "seq": None, "x": 54.05, "z": 0.0, "hw": 10.05, "hd": 12.0,
           "datum_y": 31.85, "source": "fixture"}


def selftest_junction_conflict() -> int:
    """route.JUNCTION_CONFLICT_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fx = {"T4-meadhall-stathub": _jd_fixture(
              "T4-meadhall-stathub", [(2.0 * k, 0.0) for k in range(21)],
              [36.28] * 21, t=[42.238] * 21),
          "S13-stathub-dock": _jd_fixture(
              "S13-stathub-dock", [(30.0, -30.0 + 2.0 * k) for k in range(31)],
              [42.183] * 31, width=4.0, pri=2, rung=0.12)}

    def verdict() -> str:
        segs, res = _jd_net(fx, pads=[_JC_PAD])
        b = res["refused"][0]["binding"] if res["refused"] else {}
        return (f"REFUSED {[r['id'] for r in res['refused']]}, first for "
                f"{b.get('segments')} spread {b.get('spread_m')} m "
                f"({b.get('reading')}); kept {[sg['id'] for sg in segs]}")

    def moded(mode):
        was = routemod._CONFLICT_SCOPE
        routemod._CONFLICT_SCOPE = mode
        try:
            return verdict()
        finally:
            routemod._CONFLICT_SCOPE = was

    print("case: T4 cannot meet its pad's datum; S13 crosses it 5.903 m "
          "higher and CAN meet it (REQUIRED: only T4 is refused)")
    want("the conflict: T4 against its pad, S13 kept", verdict(),
         "REFUSED ['T4-meadhall-stathub'], first for ['T4-meadhall-stathub', "
         "'complete-station-hub'] spread 4.43 m (pad datum); kept "
         "['S13-stathub-dock']")
    want("NEUTER scope=component (the shipped rule): S13 is refused for the "
         "crossing first, and T4 after it", moded("component"),
         "REFUSED ['S13-stathub-dock', 'T4-meadhall-stathub'], first for "
         "['T4-meadhall-stathub', 'S13-stathub-dock'] spread 5.903 m "
         "(claim boundary); kept []")
    print(f"junction conflict selftest: {fails} failed")
    return fails



# ---------------------------------------------------------------------------
# WALKED_WET_DEPENDS_ON AND WATER_STANDOFF_DEPENDS_ON, BOTH WAYS, ON IN-MEMORY
# GROUND.  Synthetic, carrying nothing of any world; each is the SHAPE a
# Fresh42 drive-2 refusal had, and each arm's verdict is the WRITER'S --
# `writer_water` run with the walked rule whatever mode the plan was in.
#
#   BANK   the pond at (-141,-264): a bank falling 0.7 m/m (within 0.08 of
#          the 0.7813 batter) to a pond 3 m past the carriageway edge, the
#          levelled footprint dry.  A fill plane there still stands above the
#          water at the shore; the profile could be cut ~1.9 m to pass it
#          under, and a plateau 8 m back needs no cut at all.
#   SHORE  the dock terminus (-60,-413): a 31.5 m shore ending in the sea,
#          the leg's last station on the last dry cell, the ribbon's cap
#          clamped 4 m past it onto the sea bed.
# ---------------------------------------------------------------------------
_WW_POND = (8.0, 25.0)                  # the shore's x, the pond's |z| bound


def _ww_bank(X, Z):
    bank = 38.4 - 0.7 * np.maximum(X + 4.0, 0.0)
    pond = np.abs(Z) <= _WW_POND[1]
    return np.where(pond, np.maximum(bank, 29.6), np.maximum(bank, 30.4))


def _ww_shore(X, Z):
    return np.where(X <= 4.0, 31.5, 28.0)


def _ww_field(ground, half: float = 120.0) -> "fieldmod.Field":
    n = int(2 * half)
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)
    return fieldmod.Field(f"ww-{ground.__name__}", 0.0, 0.0, half, 1.0, n,
                          ground(X, Z).astype(np.float32),
                          np.ones((n, n), dtype=np.uint8))


def _ww_corridor(fld) -> "routemod.Corridor":
    """A trunk corridor over a fixture, with roughness unpriced: every line
    along the bank costs the same, so the as-routed line is the straight one
    the drive-2 plans took along their banks."""
    return routemod.Corridor(fld, cell_m=4.0, grade_max=0.08,
                             roughness_weight=0.0,
                             bridge_entry_cost_m=300.0, bridge_per_m_cost=15.0)


def _ww_leg(fld, corr, a, b, width: float = 6.0, grade: float = 0.08):
    """A `build` for `water_route` over a fixture: the corridor search, the
    footprint box and the fit -- `build_segment`'s path with no census, pad
    or crossing to read."""
    def build(*, extra_forbid=None, trim=None, ceiling=None):
        shared = corr.forbid
        if extra_forbid is not None:
            corr.forbid = (extra_forbid if shared is None
                           else shared | extra_forbid)
        try:
            raw = corr.search(a, b)
        finally:
            corr.forbid = shared
        pts, s = routemod.resample(routemod.simplify(raw, tol_m=SIMPLIFY_TOL_M),
                                   STATION_M)
        s = np.asarray(s, dtype=np.float64)
        na, nb = (trim or {}).get("a", 0), (trim or {}).get("b", 0)
        if na + nb > len(pts) - 2:
            raise ValueError("fixture trim leaves no road")
        pts, s = pts[na:len(pts) - nb], s[na:len(s) - nb] - s[na]
        t = np.array([fld.bilinear(x, z) for x, z in pts])
        tw_lo, tw_hi, _off = transverse_window(fld, pts, width)
        lo = np.maximum(tw_hi - routemod.CUT_FILL_MAX,
                        WATER_LEVEL + routemod.ROAD_FREEBOARD_M)
        hi = tw_lo + routemod.CUT_FILL_MAX
        for k, cap in (ceiling or {}).items():
            hi[k] = max(min(hi[k], cap), lo[k])
        y = routemod.fit_profile(t, s, grade, lo=lo, hi=hi)["y"]
        return ({"id": "fixture", "from": "a", "to": "b", "width_m": width,
                 "length_m": round(float(s[-1]), 1),
                 "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
                 "profile_y": [round(float(v), 3) for v in y],
                 "is_bridge": [False] * len(pts), "crossings": [],
                 "_footprint_min": tw_lo}, {})
    return build


def selftest_writer_water() -> int:
    """WALKED_WET_DEPENDS_ON and WATER_STANDOFF_DEPENDS_ON, each dependency
    both ways, verdicts from the writer's own instruments."""
    global _PLAN_PATCHES
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(test, remedy, fn):
        global _WET_TEST, _WET_REMEDY
        was = _WET_TEST, _WET_REMEDY
        _WET_TEST, _WET_REMEDY = test, remedy
        try:
            return fn()
        finally:
            _WET_TEST, _WET_REMEDY = was

    def truth(fld, seg):
        """What the WRITER does with it, whatever the plan was keyed on."""
        return moded("walked", _WET_REMEDY, lambda: writer_water(seg, fld))

    def plan(fld, a, b, test, remedy):
        corr = _ww_corridor(fld)
        build = _ww_leg(fld, corr, a, b)
        seg, joint = build()
        return moded(test, remedy, lambda: water_route(
            fld, corr, seg, joint, build=build, refusal=lambda c: None))[0]

    bank = _ww_field(_ww_bank)
    a, b = (0.0, -80.0), (0.0, 80.0)
    print("case: BANK -- the planner approves a batter the writer refuses "
          "(REQUIRED TO REFUSE, then to MOVE THE LINE)")
    routed, _j = _ww_leg(bank, _ww_corridor(bank), a, b)()
    want("the levelled footprint calls every station dry (WET_DEPENDS_ON)",
         int(routemod.footprint_wet(routed["_footprint_min"]).sum()), 0)
    got = truth(bank, routed)
    want("the writer refuses the as-routed line", got["verdict"], "REFUSED")
    print(f"       {got['steps']} water-owned rim step(s), worst "
          f"{got['worst_m']} m at {got['worst_at']}")

    neut = plan(bank, a, b, "footprint", "alignment")
    want("NEUTER test=footprint (the shipped key): the plan approves it",
         neut["writer_water"]["verdict"], "CLEAR")
    want("NEUTER test=footprint: ...and the writer refuses what it approved",
         truth(bank, neut)["verdict"], "REFUSED")

    def cut(fld, seg) -> float:
        """The deepest the profile stands under its own centreline ground
        beside the pond -- the earth a remedy moves THERE."""
        return round(max(fld.bilinear(x, z) - y for (x, z), y in
                         zip(seg["nodes"], seg["profile_y"])
                         if abs(z) <= _WW_POND[1]), 2)

    def pond_gap(seg) -> float:
        return min(_WW_POND[0] - x for x, z in seg["nodes"]
                   if abs(z) <= _WW_POND[1])

    fixed = plan(bank, a, b, "walked", "alignment")
    want("walked + alignment: the writer lays the planned line",
         truth(bank, fixed)["verdict"], "CLEAR")
    want("walked + alignment: the remedy that served is the ALIGNMENT",
         fixed["writer_water"]["remedy"].split(" ")[0], "alignment")
    prof = plan(bank, a, b, "walked", "profile")
    want("NEUTER remedy=profile (the ceiling alone): it serves only by "
         "CUTTING the road into the bank",
         (truth(bank, prof)["verdict"],
          prof["writer_water"]["remedy"].split(" ")[0]), ("CLEAR", "ceiling"))
    want("...a metre and more deeper than the line the alignment moved",
         cut(bank, prof) - cut(bank, fixed) >= 1.0, True)
    print(f"       shore gap {pond_gap(routed):.1f} m as routed, "
          f"{pond_gap(fixed):.1f} m aligned; deepest cut "
          f"{cut(bank, routed)} / {cut(bank, fixed)} m aligned / "
          f"{cut(bank, prof)} m by the ceiling alone; length "
          f"{routed['length_m']} -> {fixed['length_m']} m")

    shore = _ww_field(_ww_shore)
    a, b = (-80.0, 0.0), (10.0, 0.0)
    print("case: SHORE -- the terminal cap paves the sea (REQUIRED TO REFUSE, "
          "then to STOP SHORT)")
    routed, _j = _ww_leg(shore, _ww_corridor(shore), a, b)()
    want("the levelled footprint calls every station dry (WET_DEPENDS_ON)",
         int((routed["_footprint_min"] < WATER_LEVEL).sum()), 0)
    got = truth(shore, routed)
    want("the writer's water sign refuses the cap past the last station",
         got["drained"] > 0, True)
    print(f"       {got['drained']} seed-submerged sample(s) lifted out, "
          f"e.g. {got['drained_at'][:2]}; last station "
          f"{routed['nodes'][-1]}")
    neut = plan(shore, a, b, "footprint", "alignment")
    want("NEUTER test=footprint: the plan keeps the cap on the sea",
         (neut["writer_water"]["verdict"], truth(shore, neut)["verdict"]),
         ("CLEAR", "REFUSED"))
    fixed = plan(shore, a, b, "walked", "alignment")
    want("walked: the writer lays the planned line",
         truth(shore, fixed)["verdict"], "CLEAR")
    print(f"       remedy {fixed['writer_water']['remedy']}; last station "
          f"{routed['nodes'][-1]} -> {fixed['nodes'][-1]}, length "
          f"{routed['length_m']} -> {fixed['length_m']} m")
    # A sub-grid shore can disappear when half-grid routing samples are
    # averaged back onto the writer lattice.  Judge emitted terrain, not the
    # smooth field that selected the centreline.
    import ribbon as ribbonmod                             # noqa: PLC0415
    flat = _ww_field(lambda X, Z: np.full_like(X + Z, 40.0))
    island = {"id": "lattice-shore", "nodes": [[-8.0, 0.0], [8.0, 0.0]],
              "profile_y": [40.0, 40.0], "is_bridge": [False, False],
              "width_m": 3.0, "length_m": 16.0}
    old_patches = _PLAN_PATCHES
    try:
        _PLAN_PATCHES = None
        want("smooth field hides the one-metre inlet",
             writer_water(island, flat)["verdict"], "CLEAR")
        def exact_patches(_fld, zones):
            def ground(x, z):
                return 29.9 if 0 <= x <= 1 and 1 <= z <= 2 else 40.0
            return {f"z_{x}_{z}": ribbonmod._flat_patch(x, z, ground)
                    for x, z in zones}
        _PLAN_PATCHES = exact_patches
        got = writer_water(island, flat)
        want("integer writer lattice forbids draining the inlet",
             (got["verdict"], got["drained"] > 0), ("REFUSED", True))
    finally:
        _PLAN_PATCHES = old_patches
    print(f"writer-water selftest: {fails} failed")
    return fails


def selftest_plan_pad_face() -> int:
    """THE PLAN ASKS THE WRITER WITH THE PADS IT GATES ON, both ways.

    The S7-stathub-wtnorth shape of Fresh42 drive 6 step 4: a leg on fill
    beside a planned pad whose ground the writer's batter may not take, so
    the batter is clipped at the pad's face and leaves a step the earthwork
    cannot close (rc 7, 4 steps, worst 1.3868 m at (-830, 4), `refused pad`).
    The plan stamped with NO pad keep-out -- `_PLAN_PAD_KEEPOUTS = None`, the
    shipped plan -- graded that batter freely and called the leg CLEAR."""
    global _PLAN_PATCHES, _PLAN_PAD_KEEPOUTS
    import ribbon as ribbonmod                             # noqa: PLC0415
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    pad = {"site_id": "wt-north", "name": "wt-north",
           "x": 0.0, "z": 7.0, "hw": 6.0, "hd": 3.0, "datum_y": 40.0}
    pads = ribbonmod.pad_keepouts_from_chain([], [], planned=[pad])
    leg = {"id": "pad-face", "nodes": [[-20.0, 0.0], [20.0, 0.0]],
           "profile_y": [43.0, 43.0], "is_bridge": [False, False],
           "width_m": 3.0, "length_m": 40.0}
    old = _PLAN_PATCHES, _PLAN_PAD_KEEPOUTS
    try:
        _PLAN_PATCHES = lambda _f, zones: {
            f"z_{x}_{z}": ribbonmod._flat_patch(x, z, lambda a, b: 40.0)
            for x, z in zones}
        print("case: a leg on 3 m of fill beside a planned pad (REQUIRED: "
              "the plan refuses the face the pad clips, with the pad as owner)")
        _PLAN_PAD_KEEPOUTS = lambda: pads
        got = writer_water(leg, None)
        want("the pad's clipped batter is a plan-time refusal",
             (got["verdict"], got["pad_steps"] > 0), ("REFUSED", True))
        _PLAN_PAD_KEEPOUTS = None
        got = writer_water(leg, None)
        want("NEUTER pads=none (the shipped plan): the same leg reads CLEAR",
             (got["verdict"], got["pad_steps"]), ("CLEAR", 0))
    finally:
        _PLAN_PATCHES, _PLAN_PAD_KEEPOUTS = old
    print(f"plan pad-face selftest: {fails} failed")
    return fails


def selftest_plan_refusal() -> int:
    """PLAN_REFUSAL, ribbon.ARRIVAL_PADS, ARRIVAL_ADVISORY, PROBE_PAD and COLLAR_CHOICE, both ways."""
    import ribbon as ribbonmod                             # noqa: PLC0415
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    global _PLAN_REFUSAL_MODE
    rows = [({"id": "w"}, "refused water_fill"), ({"id": "p"}, "refused pad"),
            ({"id": "e"}, "earthwork"), ({"id": "c"}, "claim road_x#7")]
    print("case: the drive-8 mix -- W9's `refused water_fill`, S7's `refused "
          "pad`, and the earthwork rows the writer resolves in build order "
          "(REQUIRED: only the explained water row drops a leg here; the pad "
          "row is counted on its own)")
    want("explained rows only", [r["id"] for r in plan_refusal_rows(rows)],
         ["w"])
    _PLAN_REFUSAL_MODE = "all"
    try:
        want("NEUTER refusal=all (the strict plan): earthwork rows drop the "
             "leg, as S11 and T6 were dropped",
             [r["id"] for r in plan_refusal_rows(rows)], ["w", "e", "c"])
    finally:
        _PLAN_REFUSAL_MODE = "explained"
    print("case: pad pins (REQUIRED: a road is pinned to the pad it ARRIVES "
          "at, never to one it passes)")
    pads = ribbonmod.pad_keepouts_from_chain([], [], planned=[
        {"site_id": "arrives", "name": "arrives", "x": 0.0, "z": 0.0,
         "hw": 4.0, "hd": 4.0, "datum_y": 40.0},
        {"site_id": "passes", "name": "passes", "x": 100.0, "z": 0.0,
         "hw": 4.0, "hd": 4.0, "datum_y": 45.0}])
    want("both pads pin when unrestricted",
         (pads.finished_y(0, 0), pads.finished_y(100, 0)), (40.0, 45.0))
    want("only the arrival pad pins when restricted",
         (pads.finished_y(0, 0, only={"arrives"}),
          pads.finished_y(100, 0, only={"arrives"})), (40.0, None))
    print("case: a probe refused ONLY on pad-boundary steps -- S8 onto "
          "wt-south, 10 steps (REQUIRED: not the census's refusal; the joint "
          "solve closes it)")
    global _PROBE_PAD_MODE
    pad_only = {"writer_water": {"steps": 10, "pad_steps": 10, "drained": 0,
                                 "keepout_steps": 0}}
    mixed = {"writer_water": {"steps": 12, "pad_steps": 10, "drained": 0,
                              "keepout_steps": 0}}
    wet = {"writer_water": {"steps": 3, "pad_steps": 0, "drained": 2,
                            "keepout_steps": 0}}
    want("pad-only steps are pending the joint solve",
         (_pad_pending_joint(pad_only), _pad_pending_joint(mixed),
          _pad_pending_joint(wet)), (True, False, False))
    print("case: an approach the grading could not fix -- S8 onto wt-south, "
          "0.8557 m (REQUIRED: reported on the leg, not a refusal that "
          "deletes the road)")
    left = {"steps": 0, "drained": 0, "arrival_failures": [
        {"check": "terminus_step", "destination": "wt-south",
         "measured": 0.8557}]}
    global _ARRIVAL_REFUSAL_MODE
    want("advisory: the leg keeps its road", _water_score(left), 0)
    want("a water step still refuses beside it",
         _water_score({**left, "steps": 2}), 2)
    _ARRIVAL_REFUSAL_MODE = "refuse"
    try:
        want("NEUTER arrival=refuse: the unfixed approach deletes the road",
             _water_score(left), 1)
    finally:
        _ARRIVAL_REFUSAL_MODE = "advisory"
    print("case: a leg the collar leaves refused -- T6 beside a second pad, "
          "2 steps with the collar, 0 without (REQUIRED: the leg drops the "
          "collar and says so; a leg the collar leaves clean keeps it)")
    global _COLLAR_CHOICE_MODE, _writer_water_once
    real_once, calls = _writer_water_once, []

    def fake(collar_costs):
        def once(seg, fld, keepouts=None):
            took = seg.get("connection_collar", True)
            calls.append(took)
            return {"steps": collar_costs if took else 0, "drained": 0,
                    "arrival_failures": []}
        return once
    try:
        _writer_water_once = fake(2)
        got = writer_water({"id": "T6"}, None)
        want("refused with the collar, clean without: the off arm wins",
             (got["collar"], got["steps"], calls), (False, 0, [True, False]))
        calls.clear()
        _COLLAR_CHOICE_MODE = "on"
        got = writer_water({"id": "T6"}, None)
        want("NEUTER choice=on (the collar always): the refused arm stays",
             (got["collar"], got["steps"], calls), (True, 2, [True]))
        calls.clear()
        _COLLAR_CHOICE_MODE = "best"
        _writer_water_once = fake(0)
        got = writer_water({"id": "S7"}, None)
        want("clean with the collar: kept, and asked only once",
             (got["collar"], got["steps"], calls), (True, 0, [True]))
    finally:
        _writer_water_once, _COLLAR_CHOICE_MODE = real_once, "best"
    print("case: a pad laid before the roads -- the portal hub (REQUIRED: its "
          "keep-out is wider than a site pad's by the batter margin, and a "
          "written pad keeps the PLANNED role)")
    global _PAD_BATTER_MODE
    hub = {"site_id": "hub", "x": 100.0, "z": 100.0, "hw": 10.0, "hd": 10.0,
           "role": "installation_pad"}
    site = {**hub, "site_id": "site", "x": 300.0, "role": "site_pad"}

    def reach(claim):
        mask = pad_forbid_mask([claim], 0.0, 0.0, 1.0, 500, 4.0)
        row = mask[int(claim["z"])]
        return int(row.sum() // 2 - claim["hw"])
    want("an installation pad is kept further off than a site pad",
         reach(hub) - reach(site), int(PAD_BATTER_MARGIN_M))
    _PAD_BATTER_MODE = "centreline"
    try:
        want("NEUTER batter=centreline (the shipped mask): no difference",
             reach(hub) - reach(site), 0)
    finally:
        _PAD_BATTER_MODE = "margin"
    merged = merge_pad_claims(
        [{"site_id": "hub", "x": 100.0, "z": 100.0, "hw": 10.0, "hd": 10.0,
          "role": "site_pad"}], [hub])
    want("the ledger's `site_pad` is replaced by the planned role",
         merged[0]["role"], "installation_pad")
    print(f"plan refusal selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# WALKED_KEEPOUT_DEPENDS_ON, both ways.  S2-stathub-eastlobe at station 358
# in miniature: a leg on a 0.3 cross slope, its downhill shoulder on a 1.2 m
# fill whose batter runs into a 10 m POI disc 1.5 m past the footprint --
# the plane meets the disc's ground over the limit, as S2's met its 22.0 m
# disc 0.086 m too high.
# ---------------------------------------------------------------------------
def _wk_slope(X, Z):
    return 40.0 - 0.3 * X


_WK_DISC = [(15.5, 0.0, 10.0)]


def selftest_walked_keepout() -> int:
    """WALKED_KEEPOUT_DEPENDS_ON, both ways."""
    global _WALKED_KEEPOUT_MODE, _PLAN_KEEPOUTS, _WET_REMEDY
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fld = _ww_field(_wk_slope)
    corr = _ww_corridor(fld)
    build = _ww_leg(fld, corr, (0.0, -80.0), (0.0, 64.0))
    seg, joint = build()
    was = (_WALKED_KEEPOUT_MODE, _PLAN_KEEPOUTS, _WET_REMEDY)
    try:
        _PLAN_KEEPOUTS = lambda zones: _WK_DISC
        _WALKED_KEEPOUT_MODE = "none"
        shipped = writer_water(seg, fld)
        _WALKED_KEEPOUT_MODE = "poi"
        asked = writer_water(seg, fld)
        routed, _j = water_route(fld, corr, seg, joint, build=build,
                                 refusal=lambda c: None)
        _WET_REMEDY = "profile"
        ceiled, _j = water_route(fld, corr, seg, joint, build=build,
                                 refusal=lambda c: None)
    finally:
        _WALKED_KEEPOUT_MODE, _PLAN_KEEPOUTS, _WET_REMEDY = was
    print("case: a batter into a POI disc (REQUIRED: the plan asks the "
          "writer's own discs and refuses what the writer would)")
    want("the writer's discs: refused, every step the disc's",
         (asked["verdict"], asked["steps"] == asked["keepout_steps"] > 0),
         ("REFUSED", True))
    print(f"       {asked['steps']} step(s), worst {asked['worst_m']} m at "
          f"{asked['worst_at']}, discs {asked['_keepout']}")
    want("NEUTER keepout=none (the shipped plan): approved, and step 4 "
         "refuses it", (shipped["verdict"], shipped["steps"]), ("CLEAR", 0))
    print("case: the remedy (REQUIRED: the alignment's stand-off clears it; "
          "no ceiling is cut for a keep-out face)")
    ww = routed["writer_water"]
    want("the alignment stands off the disc", (ww["verdict"], ww["remedy"]),
         ("CLEAR", "alignment 1"))
    cw = ceiled["writer_water"]
    want("without the alignment the face stands, refused with its disc as "
         "owner", (cw["verdict"], cw["steps"] == asked["steps"],
                   any("POI keep-out disc" in k
                       for k in cw.get("residual_owner") or {})),
         ("REFUSED", True, True))
    print(f"walked-keepout selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# CENSUS_CONFINE_DEPENDS_ON AND CENSUS_BAND_DEPENDS_ON, EACH BOTH WAYS, on
# synthetic flat ground.  Nothing here reads a world: the census, its cells
# and its one body are built in code, so no neuter can reach a denominator.
#
#   STAIR  the S5 shape: a band whose read cells meet only at their corners
#          (a 2-cell staircase at slope 1/2), and a corridor lattice offset
#          from the census lattice, as every real patch's is.
#   BODY   the S2/S3/S9 shape: a straight provisional line, its 16 m band
#          read, and a body the census FOUND on it whose keep-out no line
#          inside the band can get round.
# ---------------------------------------------------------------------------
def _cb_field() -> "fieldmod.Field":
    """Flat dry ground whose corridor lattice sits 2 m off the census
    lattice in z (corridor centres at x = 2 mod 4, z = 0 mod 4)."""
    half, n = 160.0, 320
    return fieldmod.Field("cb-flat", 0.0, 2.0, half, 1.0, n,
                          np.full((n, n), 40.0, dtype=np.float32),
                          np.ones((n, n), dtype=np.uint8))


def _cb_census(cells, bodies=()) -> censusmod.Census:
    return censusmod.Census("FIXTURE", "FIXTURE", "2026-01-01T00:00:00Z",
                            "FIXTURE: cells and bodies built in code",
                            censusmod.BAND_HALF_M, sorted(cells), list(bodies),
                            [])


def _cb_corridor(fld, census, *, confine: bool = True):
    """`(corridor, the same masks unconfined)` exactly as `main` builds them:
    the census bodies kept out, then the census confinement over them."""
    corr = _ww_corridor(fld)
    forbid, soft, _t = poimod.masks(census.pois(), corr.x0, corr.z0,
                                    corr.cell_m, corr.m, WIDTH_M["spur"] / 2.0)
    free = forbid
    if confine:
        cf, _st = censusmod.census_forbid(
            census, corr.x0, corr.z0, corr.cell_m, corr.m,
            chord_slack_m=chord_slack_m(corr.cell_m))
        forbid = forbid | cf
        corr.unread = cf
    corr.forbid, corr.soft = forbid, soft
    return corr, free


def _cb_build(corr, census, a, b):
    """`build_segment`'s geometry and its census gate, nothing else."""
    def build(**_kw):
        raw = corr.search(a, b)
        pts, s = routemod.resample(routemod.simplify(raw, tol_m=SIMPLIFY_TOL_M),
                                   STATION_M)
        return ({"id": "fixture", "length_m": round(float(s[-1]), 1),
                 "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
                 "census": censusmod.coverage_gate(pts, census),
                 "crossings": []}, {})
    return build


def selftest_census_band() -> int:
    """CENSUS_CONFINE_DEPENDS_ON and CENSUS_BAND_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(mod, attr, value, fn):
        was = getattr(mod, attr)
        setattr(mod, attr, value)
        try:
            return fn()
        finally:
            setattr(mod, attr, was)

    def confined(cells, a, b) -> str:
        """What the census-confined search hands the coverage gate."""
        cen = _cb_census(cells)
        corr, _free = _cb_corridor(fld, cen)
        try:
            rec, _j = _cb_build(corr, cen, a, b)()
        except ValueError:
            return "NO ROUTE"
        return (f"{rec['census']['verdict']} "
                f"{rec['census']['stations_uncensused']}")

    fld = _cb_field()
    stair2 = {(i, k) for k in range(-4, 4) for i in range(2 * k, 2 * k + 2)}
    stair3 = {(i, k) for k in range(-4, 4) for i in range(2 * k, 2 * k + 3)}
    a, b = (-120.0, -56.0), (120.0, 56.0)
    print("case: STAIR -- read cells that meet only at corners (REQUIRED TO "
          "REFUSE: the confinement must never hand the gate an uncensused "
          "line)")
    want("the confined search returns no line it cannot stand on",
         confined(stair2, a, b), "NO ROUTE")
    want("NEUTER slack=centre (the shipped test): it hands the gate a line "
         "on unread ground",
         moded(censusmod, "_CONFINE_SLACK_MODE", "centre",
               lambda: confined(stair2, a, b)).split()[0], "UNCENSUSED")
    print("case: STAIR, one cell wider (REQUIRED TO ACCEPT)")
    want("the confined search routes it, every station read",
         confined(stair3, a, b), "COVERED 0")
    want("NEUTER slack=band (WRONG: the slack stretched to the band half): "
         "nothing left to route in",
         moded(censusmod, "_CONFINE_SLACK_MODE", "band",
               lambda: confined(stair3, a, b)), "NO ROUTE")

    body = censusmod.Body("fixture-body-001", 0.0, 0.0, 40.0, 18.0, 40, 40,
                          {"stone_wall_4x2": 40}, "FIXTURE",
                          "2026-01-01T00:00:00Z")
    a, b = (-120.0, 0.0), (120.0, 0.0)
    line = [[float(x), 0.0] for x in range(-120, 121, 2)]
    band = set(censusmod.cells_for_band(line, censusmod.BAND_HALF_M))

    def after_one_round() -> str:
        """Stage 11, the census reading what it WANTS, stage 11 again."""
        cen = _cb_census(band, [body])
        corr, free = _cb_corridor(fld, cen)
        try:
            _cb_build(corr, cen, a, b)()
            return "ROUTED FIRST TIME"
        except ValueError:
            pass
        p = band_free_probe(corr, free, _cb_build(corr, cen, a, b),
                            water=lambda rec, j, _b: (rec, j),
                            refusal=lambda rec: None)
        cw = census_wanted([], [{"id": "fixture",
                                 "census_probe": {**p, "patch": "fx"}}], cen)
        cen2 = _cb_census(band | {tuple(c) for c in cw["cells"]}, [body])
        corr2, _f = _cb_corridor(fld, cen2)
        try:
            rec, _j = _cb_build(corr2, cen2, a, b)()
        except ValueError:
            return f"STILL REFUSED after reading {len(cw['cells'])} cell(s)"
        dev = max(abs(z) for _x, z in rec["nodes"])
        return (f"{rec['census']['verdict']} after reading "
                f"{'some' if cw['cells'] else 'no'} wanted cells, "
                f"{'beyond' if dev > censusmod.BAND_HALF_M else 'inside'} "
                f"the provisional band")
    print("case: BODY -- the census finds a body on the provisional line "
          "that the band cannot route round (REQUIRED TO REFUSE the band-"
          "limited plan, then to READ WHERE THE LINE WILL STAND)")
    want("one round: the wanted cells read, the leg routes beyond the band",
         after_one_round(),
         "COVERED after reading some wanted cells, beyond the provisional "
         "band")
    want("NEUTER wanted=off (the shipped plan): nothing named, still refused",
         moded(sys.modules[__name__], "_WANTED_MODE", "off",
               after_one_round).split(" after")[0], "STILL REFUSED")
    print("case: a written line whose whole band was read (REQUIRED TO "
          "ACCEPT: nothing wanted)")
    read_line = _cb_census(band)
    want("it wants no cell", len(band_deficit(line, read_line)), 0)
    want("NEUTER wanted=unfiltered (WRONG): it wants its whole band again",
         moded(sys.modules[__name__], "_WANTED_MODE", "unfiltered",
               lambda: len(band_deficit(line, read_line))), len(band))
    print(f"census-band selftest: {fails} failed")
    return fails

# ---------------------------------------------------------------------------
# DROWNED_TRIM_DEPENDS_ON, both ways.  Flat 31.5 m land along a line whose
# last 8 m (or middle 12 m) have the sea 1.5 m to one side -- the S13 dock
# terminus, and a notch no terminus owns.  The fixture build refuses a
# drowned footprint by `build_segment`'s own rule (every unbridged station
# whose levelled-footprint floor is under the plane).
# ---------------------------------------------------------------------------
def _dt_leg(sea):
    fld = _ww_field(sea)
    inner = _ww_leg(fld, _ww_corridor(fld), (0.0, -80.0), (0.0, 64.0))

    def build(**kw):
        rec, j = inner(**kw)
        ks = [k for k, v in enumerate(rec["_footprint_min"]) if v < WATER_LEVEL]
        if ks:
            raise DrownedFootprint(f"fixture: {len(ks)} station(s) drowned",
                                   ks, len(rec["nodes"]))
        return rec, j
    return build


def _dt_terminus(X, Z):
    return np.where((X > 1.5) & (Z > 56.0), 28.0, 31.5)


def _dt_notch(X, Z):
    return np.where((X > 1.5) & (np.abs(Z) < 6.0), 28.0, 31.5)


def _dt_dry(X, Z):
    return np.full_like(X, 31.5)


def selftest_drowned_terminus() -> int:
    """DROWNED_TRIM_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(value, fn):
        global _DROWNED_TRIM_MODE
        was = _DROWNED_TRIM_MODE
        _DROWNED_TRIM_MODE = value
        try:
            return fn()
        finally:
            _DROWNED_TRIM_MODE = was

    def verdict(sea) -> str:
        try:
            rec, _j, t = build_past_drowned(_dt_leg(sea))
        except DrownedFootprint as exc:
            return f"REFUSED {len(exc.stations)} drowned"
        return (f"BUILT {rec['length_m']} m" + (f" trim {t}" if t else ""))

    print("case: a drowned footprint at the TERMINUS (REQUIRED TO REFUSE the "
          "unroutable leg, then to TRIM it)")
    got = verdict(_dt_terminus)
    want("the terminal trim serves it", got.split(" m")[0].split()[0], "BUILT")
    print(f"       {got}")
    want("NEUTER trim=off (the shipped refusal): the leg is unroutable",
         moded("off", lambda: verdict(_dt_terminus)).split()[0], "REFUSED")
    print("case: a drowned notch mid-line (REQUIRED TO REFUSE: no terminus "
          "owns it)")
    want("it stays refused", verdict(_dt_notch).split()[0], "REFUSED")
    print("case: a leg with nothing drowned (REQUIRED TO ACCEPT as routed)")
    want("it is built untrimmed", verdict(_dt_dry), "BUILT 144.0 m")
    want("NEUTER trim=always (WRONG): it is cut short",
         moded("always", lambda: verdict(_dt_dry)).endswith("trim {'b': 2}"),
         True)
    print(f"drowned-terminus selftest: {fails} failed")
    return fails

# ---------------------------------------------------------------------------
# CENSUS_ADOPT_DEPENDS_ON, both ways -- the drive-4 S13/W9 shape: the
# confined search refused, the band-free probe SERVABLE with every written
# station and its whole band read.  The probes are REAL (`band_free_probe`
# over the census-band fixture); the confined refusal is the plan's input.
# ---------------------------------------------------------------------------
def selftest_census_adopt() -> int:
    """CENSUS_ADOPT_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(value, fn):
        global _ADOPT_MODE
        was = _ADOPT_MODE
        _ADOPT_MODE = value
        try:
            return fn()
        finally:
            _ADOPT_MODE = was

    fld = _cb_field()
    a, b = (-120.0, 0.0), (120.0, 0.0)
    body = censusmod.Body("fixture-body-001", 0.0, 0.0, 40.0, 18.0, 40, 40,
                          {"stone_wall_4x2": 40}, "FIXTURE",
                          "2026-01-01T00:00:00Z")
    line = [[float(x), 0.0] for x in range(-120, 121, 2)]
    band = set(censusmod.cells_for_band(line, censusmod.BAND_HALF_M))

    def probed(cells):
        cen = _cb_census(cells, [body])
        corr, free = _cb_corridor(fld, cen)
        p = band_free_probe(corr, free, _cb_build(corr, cen, a, b),
                            water=lambda rec, j, _b: (rec, j),
                            refusal=lambda rec: None)
        return cen, {**p, "patch": "fx"}

    def disposition(cen, p) -> str:
        """What the plan does with a leg the confined search refused."""
        if probe_adoptable(p, cen):
            return "ROUTED"
        cw = census_wanted([], [{"id": "S13", "census_probe": p}], cen)
        return (f"WANTED {len(cw['cells'])}" if cw["cells"] else
                f"UNROUTED, 0 cells wanted, servable_unrouted "
                f"{[u['id'] for u in cw['servable_unrouted']]}")

    # The probe's line around the body, then that line's band read too.
    cen0, p0 = probed(band)
    cen1, p1 = probed(band | set(band_deficit(p0["nodes"], cen0)))
    print("case: DRIVE-4 S13 -- confined refused, band-free SERVABLE with "
          f"{p1['stations_unread']} of {p1['stations']} stations unread and "
          f"{len(band_deficit(p1['nodes'], cen1))} band cells unread "
          "(REQUIRED TO ROUTE)")
    want("the plan routes it by the probe's line", disposition(cen1, p1),
         "ROUTED")
    want("NEUTER adopt=off (the shipped plan): unrouted with 0 cells wanted, "
         "and named", moded("off", lambda: disposition(cen1, p1)),
         "UNROUTED, 0 cells wanted, servable_unrouted ['S13']")
    print("case: the same leg before its line's band was read (REQUIRED TO "
          "WANT CELLS, never to adopt a line on unread ground)")
    want("its unread cells are wanted", disposition(cen0, p0).split()[0],
         "WANTED")
    want("NEUTER adopt=blind (WRONG): a line over unread ground is routed",
         moded("blind", lambda: disposition(cen0, p0)), "ROUTED")
    print(f"census-adopt selftest: {fails} failed")
    return fails



# ---------------------------------------------------------------------------
# TERMINAL_CROSSING_TRIM_DEPENDS_ON, both ways.  A straight 31-station line
# (2 m) whose water is the LAST 7 stations (T5 / T13 / S13 / W9-x04 in
# miniature: the free run reaches the terminus), the FIRST 6, the middle 6
# (an ordinary crossing, two banks), or all of it.  Detection, windows,
# straighten and banks are `build_segment`'s own (`_cb_plan`); the trim is
# the build's own `trim=`.  The writer's water verdict is a stand-in: clear
# everywhere, or -- T5-temple-dockshore's shape, measured -- 3 water steps
# on any line shorter than the as-routed one, whose new end faces the water.
# ---------------------------------------------------------------------------
def _tc_leg(wet: set[int], n: int = 31):
    def build(*, trim=None, **_kw):
        pts, s = _cb_line(n, 20.0, straight=n)
        na, nb = (trim or {}).get("a", 0), (trim or {}).get("b", 0)
        if na + nb > len(pts) - 2:
            raise ValueError("fixture trim leaves no road")
        pts, s = pts[na:len(pts) - nb], s[na:len(s) - nb] - s[na]
        keep = {k - na for k in wet if na <= k < n - nb}
        plan = _cb_plan(pts, s, keep)
        return ({"id": "fixture", "length_m": round(float(s[-1]), 1),
                 "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
                 "crossings": [c for c, _w in plan]}, {})
    return build


def selftest_terminal_crossing() -> int:
    """TERMINAL_CROSSING_TRIM_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(value, fn):
        global _TERMINAL_CROSSING_MODE
        was = _TERMINAL_CROSSING_MODE
        _TERMINAL_CROSSING_MODE = value
        try:
            return fn()
        finally:
            _TERMINAL_CROSSING_MODE = was

    def plan(wet, wet_end_steps: int = 0) -> str:
        build = _tc_leg(wet)
        seg, _j = build()
        n0 = len(seg["nodes"])

        def water(cand):
            k = wet_end_steps if len(cand["nodes"]) < n0 else 0
            return {"steps": k, "drained": 0}
        got, _j2, _ww, kw, note = trim_terminal_crossing(
            seg, {}, water(seg), build=build, refusal=lambda c: None,
            water=water)
        rec = got if got is not None else seg
        xs = [f"{c['bank_a']['station']}..{c['bank_b']['station']}"
              + ("*" if c.get("terminal_banks") else "")
              for c in rec["crossings"]]
        return (f"{len(rec['nodes'])} stations, crossings {xs or 'none'}"
                + (f", trim {kw['trim']}" if kw else "")
                + (", declined" if note and not note["adopted"] else ""))

    tail, head = set(range(24, 31)), set(range(0, 6))
    print("case: the free run reaches the LAST station (REQUIRED: the road "
          "ends on its near bank, trimmed by the run's own length)")
    want("the wet tail is trimmed to the bench before it", plan(tail),
         "23 stations, crossings none, trim {'a': 0, 'b': 8}")
    want("NEUTER crossing=kept (the shipped plan): a deck with no far bank",
         moded("kept", lambda: plan(tail)), "31 stations, crossings "
         "['23..30*']")
    print("case: the free run reaches the FIRST station (REQUIRED: the same, "
          "at the other end)")
    want("the wet head is trimmed", plan(head),
         "24 stations, crossings none, trim {'a': 7, 'b': 0}")
    print("case: the trimmed line's new end faces the water (T5-temple-"
          "dockshore, measured; REQUIRED: a leg that lays is not traded)")
    want("the trim is declined, the crossing and its terminus owner stand",
         plan(tail, wet_end_steps=3),
         "31 stations, crossings ['23..30*'], declined")
    want("NEUTER crossing=forced (WRONG): the leg is traded for 3 water steps",
         moded("forced", lambda: plan(tail, wet_end_steps=3)),
         "23 stations, crossings none, trim {'a': 0, 'b': 8}")
    print("case: an ordinary crossing with two banks (REQUIRED TO ACCEPT as "
          "routed)")
    want("it is left alone", plan(set(range(12, 18))),
         "31 stations, crossings ['11..18']")
    print("case: the whole line is the free run (REQUIRED: no trim leaves a "
          "road, so the crossing and its terminus owner stand)")
    want("it is kept, flagged", plan(set(range(0, 31))),
         "31 stations, crossings ['0..30*'], declined")
    print(f"terminal-crossing selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# ARRIVAL_DEPENDS_ON and junction.PAD_GAP_DEPENDS_ON, each both ways, on the
# stathub hub VERBATIM from Fresh42 drive 3: T4-meadhall-stathub stations
# 790-801 (nodes, profile_y, terrain_y of `segments.Fresh42.yaml`, route of
# 2026-09-25 22:26), `complete-station-hub` as step 2 wrote it (seq 631:
# centre (104.5, 362.5), 24.8 x 42.8 m, target_y 77.67) and the node on its
# centre.  The spur's ground is `Fresh42_1m.bin` (trunk-primary) sampled on
# the line from the terminus to the node.  On drive 3 that terminus stood
# 8.2 m from the pad at 79.447 and the gate read a 0.907 m riser onto it.
# Neither reads nor writes a world.
# ---------------------------------------------------------------------------
_AR_PAD = {"kind": "pad", "id": "complete-station-hub",
           "label": "complete-station-hub",
           "site_id": "complete-station-hub", "role": "site_pad", "seq": 631,
           "x": 104.5, "z": 362.5, "hw": 12.4, "hd": 21.4, "datum_y": 77.67}
_AR_NODE = (104.5, 362.5)
_AR_T4 = dict(
    pts=[(83.9, 340.72), (83.9, 342.72), (83.9, 344.72), (83.9, 346.72),
         (83.9, 348.72), (83.9, 350.72), (83.9, 352.72), (83.9, 354.72),
         (83.9, 356.72), (83.9, 358.72), (83.9, 360.72), (83.9, 361.2)],
    y=[77.432, 77.68, 77.931, 78.15, 78.411, 78.651, 78.891, 79.131, 79.371,
       79.433, 79.433, 79.447],
    t=[76.59, 76.741, 77.211, 77.396, 78.026, 78.633, 79.404, 79.597, 79.947,
       79.832, 79.894, 79.954])
# Distance along the spur -> generated height, Fresh42_1m.bin trunk-primary.
_AR_SPUR_T = ((0.0, 79.954), (2.0, 79.882), (4.0, 79.469), (4.159, 79.398))


def _ar_leg(spur: dict) -> tuple[dict, dict]:
    """T4's last 12 stations plus whatever the spur added, as a joint leg."""
    end = _AR_T4["pts"][-1]
    add = list(spur["stations"])
    ds = [math.hypot(x - end[0], z - end[1]) for x, z in add]
    dt, tt = zip(*_AR_SPUR_T)
    return _jd_fixture("T4-meadhall-stathub", _AR_T4["pts"] + add,
                       _AR_T4["y"] + [_AR_T4["y"][-1]] * len(add),
                       width=6.0, rung=0.18,
                       t=_AR_T4["t"] + [float(np.interp(d, dt, tt))
                                        for d in ds])


def selftest_arrival() -> int:
    """ARRIVAL_DEPENDS_ON and junction.PAD_GAP_DEPENDS_ON, both ways."""
    global _ARRIVAL_MODE
    fails = 0
    J = routemod._junction()

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(arrival, pad_gap, fn):
        global _ARRIVAL_MODE
        was = (_ARRIVAL_MODE, J._PAD_GAP_EDGE)
        _ARRIVAL_MODE, J._PAD_GAP_EDGE = arrival, pad_gap
        try:
            return fn()
        finally:
            _ARRIVAL_MODE, J._PAD_GAP_EDGE = was

    level = routemod.footprint_half_m(6.0)

    def verdict() -> str:
        """Whether the hub pad is read and where the solve puts the end."""
        spur = arrival_spur(_AR_T4["pts"][-1], _AR_NODE, [_AR_PAD], level)
        seg, joint = _ar_leg(spur)
        pairs = [r for r in J.find_conflicts(
            J.ribbons_from_segments({"segments": [seg]}, "fixture"),
            [_AR_PAD])["pairs"] if r["b_kind"] == "pad"]
        segs, _res = _jd_net({seg["id"]: (seg, joint)}, pads=[_AR_PAD])
        y_end = segs[0]["profile_y"][-1] if segs else None
        if not pairs:
            return (f"PAD UNREAD, gap {spur['gap_after_m']} m, end "
                    f"{y_end} vs datum {_AR_PAD['datum_y']}")
        allow = J.budget_allow(min(r["gap_m"] for r in pairs), 0.18)
        met = y_end is not None and abs(y_end - _AR_PAD["datum_y"]) <= (
            allow + routemod.JUNCTION_DATUM_MARGIN_M + 1e-6)
        return (("ARRIVES" if met else "PAD READ, END OFF ITS DATUM")
                + f", gap {spur['gap_after_m']} m")

    print("case: T4 arrives at complete-station-hub (REQUIRED TO ARRIVE)")
    want("the spur carries the hub terminus onto the pad at its datum",
         verdict(), "ARRIVES, gap 0.05 m")
    want("NEUTER arrival=off (the shipped rule): the pad is never read",
         moded("off", "level", verdict),
         "PAD UNREAD, gap 4.21 m, end 79.447 vs datum 77.67")
    want("NEUTER pad gap=carriageway (the shipped key): the pad the spur "
         "reaches is still outside the population",
         moded("spur", "carriageway", verdict).startswith("PAD UNREAD"),
         True)

    print("case: a spur never crosses a cell the search may not enter "
          "(REQUIRED TO STOP SHORT)")

    def wet_past_2m(x, _z):
        return "water" if x > 86.4 else None

    def stopped() -> str:
        r = arrival_spur(_AR_T4["pts"][-1], _AR_NODE, [_AR_PAD], level,
                         wet_past_2m)
        return (f"{len(r['stations'])} station(s), all dry: "
                f"{all(x <= 86.4 for x, _z in r['stations'])}")
    want("water 2.5 m out: one station, and it is dry", stopped(),
         "1 station(s), all dry: True")
    want("the same under NEUTER arrival=off: nothing is paved",
         moded("off", "level", stopped), "0 station(s), all dry: True")
    print(f"arrival selftest: {fails} failed")
    return fails

# ---------------------------------------------------------------------------
# ARRIVAL_REACH_DEPENDS_ON, both ways, on Pirate68's stathub in miniature.
# complete-station-hub (hw 10.05, hd 12.0, datum 31.85) is centred on the
# node.  A knoll at 42 m stands round its west, north and east faces: the
# ground T4's box at station 754 was clamped to (floor 35.238 there).  South
# of it is ground at 33 m.  The pad is narrower than it is deep, so the nearest cell outside its
# mask is on the knoll, as (640, 1090) was.  The verdict is the joint solve's: a trunk that
# comes in 30 m straight through the chosen start and runs its spur to the pad.
# ---------------------------------------------------------------------------
_RCH_PAD = {"site_id": "complete-station-hub", "name": "complete-station-hub",
            "role": "installation_pad", "planned": True, "seq": None,
            "x": 0.0, "z": 0.0, "hw": 10.05, "hd": 12.0, "datum_y": 31.85}


def _rch_ground(X, Z):
    return np.where(Z > -14.0, 42.0, 33.0)


def selftest_arrival_reach() -> int:
    """ARRIVAL_REACH_DEPENDS_ON, both ways."""
    global _ARRIVAL_REACH_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fld = _ww_field(_rch_ground, half=80.0)
    corr = _ww_corridor(fld)
    level = routemod.footprint_half_m(6.0)
    # This case is about WHERE an arrival starts, at the nearest legal cell to
    # a pad it meets: the 6 m batter margin (PAD_BATTER_DEPENDS_ON) keeps a
    # PASSING leg off an installation pad and is asked here as a pass-by of
    # none, so the mask is the centreline one.
    global _PAD_BATTER_MODE
    _was_batter, _PAD_BATTER_MODE = _PAD_BATTER_MODE, "centreline"
    try:
        corr.forbid = pad_forbid_mask([_RCH_PAD], corr.x0, corr.z0,
                                      corr.cell_m, corr.m, level)
    finally:
        _PAD_BATTER_MODE = _was_batter
    node = (0.0, 0.0)
    datums = {"complete-station-hub": 31.85}
    pad = {"kind": "pad", "id": "complete-station-hub",
           "label": "complete-station-hub", "role": "installation_pad",
           "seq": None, "x": 0.0, "z": 0.0, "hw": 10.05, "hd": 12.0,
           "datum_y": 31.85, "source": "fixture"}

    def verdict() -> str:
        (i, j), _rec = arrival_start("stathub", node, corr, 6.0,
                                     claims=[_RCH_PAD], datums=datums)
        sx, sz = corr.cell_world(i, j)
        spur = arrival_spur((sx, sz), node, [_RCH_PAD], level)
        L = math.hypot(sx, sz)
        ux, uz = -sx / L, -sz / L
        pts = ([(sx - ux * 2.0 * k, sz - uz * 2.0 * k)
                for k in range(15, 0, -1)] + [(sx, sz)]
               + [tuple(p) for p in spur["stations"]])
        t = [fld.bilinear(x, z) for x, z in pts]
        tw_lo, tw_hi, _off = transverse_window(fld, pts, 6.0)
        seg, joint = _jd_fixture("T4-meadhall-stathub", pts, t, t=t)
        joint["lo"] = np.maximum(tw_hi - routemod.CUT_FILL_MAX,
                                 WATER_LEVEL + routemod.ROAD_FREEBOARD_M)
        joint["hi"] = tw_lo + routemod.CUT_FILL_MAX
        joint["y0"] = np.clip(np.asarray(t), joint["lo"], joint["hi"])
        seg["profile_y"] = [round(float(v), 3) for v in joint["y0"]]
        _segs, res = _jd_net({seg["id"]: (seg, joint)}, pads=[pad])
        side = "south of" if -sz > abs(sx) else "beside"
        return (f"starts {side} the pad on {t[15]:.1f} m ground: "
                + ("REFUSED against the pad" if res["refused"] else
                   f"{res['legs'][seg['id']]['verdict']} with the pad"))

    def moded(mode):
        global _ARRIVAL_REACH_MODE
        was = _ARRIVAL_REACH_MODE
        _ARRIVAL_REACH_MODE = mode
        try:
            return verdict()
        finally:
            _ARRIVAL_REACH_MODE = was

    print("case: the nearest cell to the hub is on a knoll 10 m above the "
          "pad (REQUIRED: the leg starts where it can meet the datum)")
    want("the start moves to the pad's south face, and the leg arrives",
         verdict(), "starts south of the pad on 33.0 m ground: JOINED with the pad")
    want("NEUTER reach=nearest (the shipped snap): the knoll, and the joint "
         "solve refuses the leg", moded("nearest"),
         "starts beside the pad on 42.0 m ground: REFUSED against the pad")
    print(f"arrival reach selftest: {fails} failed")
    return fails




# ---------------------------------------------------------------------------
# CROSSING_BANKS_DEPENDS_ON, each term both ways.  Stations 2 m apart along a
# bend, on synthetic heights: dry at 31.5 m, a channel at 28.0 m.  Detection
# is `route.find_crossings` itself, grid snap included, and the windows,
# straightening and record are `build_segment`'s own helpers.  The verdict is
# the gate's own reading (accept/sections/roads.py::
# declaration_off_centreline): each bank's lateral offset from the record's
# own `centreline_straight_from_to`.  Neither reads nor writes a world.
#   BEND     one channel on a 20 m radius: the shape of T1-x02, whose bank_a
#            stood 4.44 m off its recorded chord on drive 3.
#   PAIR     a 52 m channel on a straight, then a 10 m one on a 14 m bend, 8 m
#            apart and not merged (70 m > max_merged_m): the shape of W9-x01/x02
#            and W9-x03/x04, whose windows overlapped on drive 3.
#   TERMINUS a channel that runs to the last station: T5-x01, T13-x01,
#            S13-x01 and W9-x04 on drive 3.
# ---------------------------------------------------------------------------
_CB_ON_LINE_M = 0.05        # two 0.01 m roundings of a point already on it


def _cb_line(n: int, radius: float, straight: int = 0):
    pts = []
    for k in range(n):
        if k <= straight:
            pts.append((2.0 * k, 0.0))
            continue
        th = 2.0 * (k - straight) / radius
        pts.append((2.0 * straight + radius * math.sin(th),
                    radius * (1.0 - math.cos(th))))
    s = np.concatenate([[0.0], np.cumsum(np.hypot(
        np.diff([p[0] for p in pts]), np.diff([p[1] for p in pts])))])
    return pts, s


def _cb_plan(pts, s, wet: set[int]):
    """Detection, windows, straighten and record, as `build_segment` runs
    them.  Returns the records and each record's own straightened window."""
    t = np.array([28.0 if k in wet else 31.5 for k in range(len(pts))])
    detected, _flag, _fords = routemod.find_crossings("fx", pts, s, t, [],
                                                      t_foot=t)
    _free, windows = crossing_windows(pts, s, detected)
    pts, windows = straighten_windows(pts, s, windows, [])
    for c, ia, ib, wa, wb in windows:
        declare_banks(c, pts, t, t, ia, ib, wa, wb)
    return [(c, [pts[k] for k in range(wa, wb + 1)])
            for c, _ia, _ib, wa, wb in windows]


def _cb_off(xz, line) -> float:
    (ax, az), (bx, bz) = line[0], line[-1]
    L = math.hypot(bx - ax, bz - az)
    return abs((xz[0] - ax) * (bz - az) / L - (xz[1] - az) * (bx - ax) / L)


def _cb_worst(plan) -> float:
    """Worst of: a bank off its record's centreline, and a station of the
    record's own window off that same line."""
    worst = 0.0
    for c, window in plan:
        line = c["centreline_straight_from_to"]
        worst = max([worst] + [_cb_off(c[e]["xz"], line)
                               for e in ("bank_a", "bank_b")]
                    + [_cb_off(p, line) for p in window])
    return round(worst, 3)


def selftest_crossing_banks() -> int:
    """CROSSING_BANKS_DEPENDS_ON, each term both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(value, fn):
        global _CROSSING_BANKS_MODE
        was = _CROSSING_BANKS_MODE
        _CROSSING_BANKS_MODE = value
        try:
            return fn()
        finally:
            _CROSSING_BANKS_MODE = was

    def on_line(fn) -> str:
        w = _cb_worst(fn())
        return "ON THE LINE" if w <= _CB_ON_LINE_M else f"OFF {w} m"

    def bend():
        return _cb_plan(*_cb_line(31, 20.0), set(range(12, 18)))

    def pair():
        return _cb_plan(*_cb_line(60, 14.0, straight=36),
                        set(range(10, 36)) | set(range(40, 45)))

    def terminal(wet):
        return [c.get("terminal_banks")
                for c, _w in _cb_plan(*_cb_line(31, 20.0), wet)]

    half = boardwalk.DECK_HALF_WIDTH_M
    def station_provenance(pts, wet, terminal_end):
        s = np.r_[0.0, np.cumsum([
            math.hypot(b[0]-a[0], b[1]-a[1])
            for a, b in zip(pts, pts[1:])])]
        t = np.array([20.0 if k in wet else 40.0 for k in range(len(pts))])
        detected, flagged, _ = routemod.find_crossings(
            "station-provenance", pts, s, t, [], t_foot=t)
        free, wins = crossing_windows(pts, s, detected)
        c, ia, ib, wa, wb = wins[0]
        declare_banks(c, pts, t, t, ia, ib, wa, wb)
        return (bool(free[flagged].all()) and ia < ib
                and c["span_m"] > 0
                and ia == max(c["stations"][0]-1, 0)
                and ib == min(c["stations"][1]+1, len(pts)-1)
                and c.get("terminal_banks") == terminal_end)

    want("a short terminal crossing survives coincident snapped banks",
         station_provenance([(-2.0, 0.0), (0.1, 0.0), (0.8, 0.0)],
                            {2}, ["bank_b"]), True)
    want("repeated coordinates do not select an earlier bank occurrence",
         station_provenance([(0., 0.), (2., 2.), (4., 2.), (5., 0.),
                             (4., -2.), (0., 0.), (1., 1.), (2., 2.),
                             (4., 3.)], {6}, None), True)

    print("case: a channel on a BEND (REQUIRED: both banks on the recorded "
          "centreline)")
    want("the banks are read off the straightened line", on_line(bend),
         "ON THE LINE")
    shipped = moded("detected", lambda: _cb_worst(bend()))
    want(f"NEUTER banks=detected (the shipped record): a bank further off "
         f"than the deck's {half} m half-width", shipped > half, True)
    print(f"       shipped worst {shipped} m")
    print("case: two channels whose windows OVERLAP on a bend (REQUIRED: "
          "every record's banks and window on its own chord)")
    want("the windows are kept apart", on_line(pair), "ON THE LINE")
    want("NEUTER windows=overlapping (the shipped straighten): the first "
         "chord is moved by the second",
         moded("overlapping", lambda: on_line(pair)).split()[0], "OFF")
    print("case: a channel that runs to the segment's LAST station "
          "(REQUIRED: bank_b is flagged as having no bench beyond it)")
    want("the terminal bank is named", terminal(set(range(24, 31))),
         [["bank_b"]])
    want("NEUTER terminal=unflagged (the shipped record): it reads as a "
         "bank", moded("unflagged", lambda: terminal(set(range(24, 31)))),
         [None])
    print("case: a channel with dry road beyond both banks (REQUIRED: no "
          "terminal flag)")
    want("an interior crossing is not terminal", terminal(set(range(12, 18))),
         [None])
    print("case: the deck stands at or above its banks -- W9 x01, banks at "
          "33.252 m, ceiling 32.797 (REQUIRED: H is not chosen under the bed)")
    global _BANK_BED_MODE
    ground = [33.2, 33.25, 29.0, 29.0, 29.0, 33.25, 33.3]
    args = (ground, [0, 1, 5, 6], [2, 3, 4], 32.5, 38.6)
    want("the floor is the higher bank", bank_bed_floor(*args), 33.25)
    want("a bed above the interval's top stops at the top",
         bank_bed_floor(ground, [0, 1, 5, 6], [2, 3, 4], 32.5, 33.0), 33.0)
    _BANK_BED_MODE = "free"
    try:
        want("NEUTER bed=free: the interval's own floor",
             bank_bed_floor(*args), 32.5)
    finally:
        _BANK_BED_MODE = "floor"
    print("case: a ceiling asked on a detected crossing's bank run -- the W9 "
          "x01 shape, ground median 33.304, legal [30.9, 34.1] (REQUIRED: "
          "the one height obeys it; both banks move together)")

    def bank(caps, mode="interval"):
        global _BANK_CEILING_MODE
        was = _BANK_CEILING_MODE
        _BANK_CEILING_MODE = mode
        try:
            got = bank_level_height([33.1, 33.304, 33.5], 30.9, 34.1, caps)
        finally:
            _BANK_CEILING_MODE = was
        return (round(got["H"], 3), got["lowered"], got["under_floor"])

    want("the ceiling sets the run's height", bank([32.4]), (32.4, 1, 0))
    want("NEUTER bank_ceiling=after (the shipped order): the median holds "
         "and the ceiling is dropped uncounted",
         bank([32.4], "after"), (33.304, 0, 0))
    want("a ceiling under the deck-clearance floor stops AT the floor and is "
         "counted, never forced", bank([29.0]), (30.9, 1, 1))
    want("no ceiling asked: the median-chosen height is unchanged",
         bank([]), (33.304, 0, 0))
    print("case: a refused ray whose foot is on the deck's own stations -- "
          "W9 x01, foot 212/213 both bridged (REQUIRED: the drop is asked of "
          "the level run, and a dry neighbour still takes it first)")

    def drops(bridged, mode="interval"):
        global _BANK_CEILING_MODE
        was = _BANK_CEILING_MODE
        _BANK_CEILING_MODE = mode
        try:
            return drop_stations(1, bridged)
        finally:
            _BANK_CEILING_MODE = was

    want("both deck stations take the drop", drops([True, True, True]),
         [1, 2])
    want("NEUTER bank_ceiling=after (the shipped writer_water): nothing is "
         "asked, so no ceiling round runs", drops([True, True, True], "after"),
         [])
    want("a dry neighbour keeps taking it alone", drops([True, True, False]),
         [2])
    print(f"crossing-banks selftest: {fails} failed")
    return fails



def selftest_owner_cause() -> int:
    """OWNER_CAUSE_DEPENDS_ON, both ways, on the Ulfsland regen's own shapes:
    S12 (a 32 m leg, both ends within batter reach of every row) and T6/W3's
    rows the stamp refused for the line's own second branch and for a cliff."""
    global _OWNER_CAUSE_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    # a 400 m straight leg: ends (0, 0) and (400, 0)
    nodes = [[2.0 * k, 0.0] for k in range(201)]
    seg = {"from": "brg-s1-e", "to": "boathouse-strait", "nodes": nodes,
           "is_bridge": [False] * len(nodes), "crossings": []}
    short = {"from": "brg-s1-e", "to": "boathouse-strait",
             "nodes": [[2.0 * k, 0.0] for k in range(17)],
             "is_bridge": [False] * 17, "crossings": []}
    seeds = [(200, 3, 40.0), (210, 4, 40.0), (212, 4, 40.0), (216, 4, 40.0)]
    feet = {(x, z): x // 2 for x, z, _y in seeds}
    causes = {(200, 3): ["shared_corridor"], (210, 4): ["steeper_than_batter"],
              (212, 4): ["over_clamp", "water_fill"], (216, 4): ["water_fill"]}
    ww = {"_seeds": seeds, "_feet": feet, "_causes": causes}
    floor = ("the profile floor (freeboard/clamp) and the census band the "
             "alignment may move in")
    cliff = ("ground beside the line steeper than the batter plane or past "
             "the apply clamp (steeper_than_batter / over_clamp): the "
             "alignment")
    branch = ("this leg's own second branch beside the line (shared_corridor)"
              ": the alignment folds back on itself")
    # a short leg: rows 12.9 m from the boathouse end and 19 m from brg-s1-e
    near_b = {"_seeds": [(19, 0, 31.0)], "_feet": {(19, 0): 9}, "_causes": {}}

    def moded(mode, fn):
        global _OWNER_CAUSE_MODE
        was = _OWNER_CAUSE_MODE
        _OWNER_CAUSE_MODE = mode
        try:
            return fn()
        finally:
            _OWNER_CAUSE_MODE = was
    print("case: rows the stamp refused for the line's own branch and a cliff "
          "(REQUIRED: named for that, not for the floor or the census band)")
    want("by cause", moded("cause", lambda: water_owner(seg, ww, 25.0)),
         {branch: 1, cliff: 2, floor: 1})
    want("NEUTER owner_cause=position: all four are the floor and the band",
         moded("position", lambda: water_owner(seg, ww, 25.0)), {floor: 4})
    print("case: a 32 m leg (REQUIRED: a row 12.9 m from its far end is that "
          "end's approach)")
    want("the nearer end", moded("cause", lambda: water_owner(short, near_b,
                                                              25.0)),
         {"terminus boathouse-strait: its approach (deck/jetty)": 1})
    want("NEUTER owner_cause=position: the first end named",
         moded("position", lambda: water_owner(short, near_b, 25.0)),
         {"terminus brg-s1-e: its approach (deck/jetty)": 1})
    print(f"owner-cause selftest: {fails} failed")
    return fails


def selftest_pad_cliff_verdict() -> int:
    """PLANNED_PAD_CLIFF_DEPENDS_ON at the plan's FINAL decision: the search's
    verdict and numbers are untouched (a plan that never needed the second
    reading is unchanged to the byte); a leg REFUSED only on the planned pads'
    own cliffs -- Ulfsland S6 (5 `refused pad` rows, 1.8808 m at [533,1326])
    and S8 (3 rows, 0.9689 m at [533,50]) -- is judged CLEAR at the end and
    rejected only when rows survive the owed rule."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def leg(extra):
        return {"writer_water": {
            "verdict": "REFUSED", "steps": 5, "drained": 0, "worst_m": 1.8808,
            "worst_at": [533, 1326], "pad_steps": 5, "keepout_steps": 0,
            "arrival_failures": [], "remedy": "as routed", **extra}}
    ww = {"verdict": "REFUSED", "steps": 5}
    print("case: the record carries the final reading only where it differs")
    want("agreeing readings add no key (Fresh42 stays byte-identical)",
         judged_public({**ww, "_judged": {"verdict": "REFUSED", "steps": 5}}),
         {})
    want("a leg the cliff rule clears carries it", judged_public(
        {**ww, "_judged": {"verdict": "CLEAR", "steps": 0}}),
        {"judged_verdict": "CLEAR", "judged_steps": 0})
    print("case: S6's shape (REQUIRED: refused by the search's gates, judged "
          "CLEAR by the final one)")
    cleared = leg({"judged_verdict": "CLEAR", "judged_steps": 0})
    want("the search's reading still refuses", water_rejection(cleared) is None,
         False)
    want("the final reading admits it",
         (final_verdict(cleared["writer_water"]),
          water_rejection(cleared, final=True)), ("CLEAR", None))
    print("case: rows survive the owed rule (REQUIRED TO REFUSE, with the "
          "judged number)")
    left = leg({"judged_verdict": "REFUSED", "judged_steps": 2})
    want("refused, with the rows left", (
        water_rejection(left, final=True) or {}).get("writer_water_steps"), 2)
    print("case: a record with no second reading (REQUIRED: the old verdict)")
    plain = leg({})
    want("final reading is the search's", (
        final_verdict(plain["writer_water"]),
        (water_rejection(plain, final=True) or {}).get("writer_water_steps")),
        ("REFUSED", 5))
    print(f"pad-cliff-verdict selftest: {fails} failed")
    return fails


def _ab_ground(X, Z):
    """A shore: sea west of x = -1; land from there rising 0.1 m/m; a low band
    either side of the line inside the freeboard (30.4 m, over the plane) and
    one shoulder pit UNDER the plane (29.9 m) at the first station -- the
    Ulfsland brg-s2-w shape -- and, 30 m on, a real 10 m channel (28.5 m)."""
    h = np.where(X < -1, 29.0, 31.4 + 0.10 * np.maximum(X, 0.0))
    band = (np.abs(Z) > 2.0) & (np.abs(Z) <= 4.0) & (X >= -1) & (X < 8.0)
    h = np.where(band, 30.4, h)
    pit = (np.abs(X - 0.5) <= 1.0) & (np.abs(Z - 3.0) <= 0.6)
    h = np.where(pit, 29.9, h)
    return np.where((X >= 30.0) & (X < 40.0) & (np.abs(Z) <= 30.0), 28.5, h)


def selftest_abutment_bench() -> int:
    """ABUTMENT_BENCH_DEPENDS_ON, both ways, through `build_segment` on a
    shore built in code.  Two declared nodes are added to `spec.NODES` for the
    call and removed after."""
    global _ABUTMENT_BENCH_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    half, n = 120.0, 240
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)
    fld = fieldmod.Field("ab-shore", 0.0, 0.0, half, 1.0, n,
                         _ab_ground(X, Z).astype(np.float32),
                         np.ones((n, n), dtype=np.uint8))
    corr = _ww_corridor(fld)
    line = [[float(x), 0.0] for x in range(0, 82, 2)]
    edge = {"id": "AB", "a": "ab-abutment", "b": "ab-far", "cls": "trunk",
            "pri": 3}
    specmod.NODES["ab-abutment"] = {
        "island": "ab", "patch": "ab", "kind": "abutment", "level_y": 30.6,
        "xz": (0.0, 0.0), "ground_y": 31.4}
    specmod.NODES["ab-far"] = {"island": "ab", "patch": "ab",
                               "kind": "extremity", "xz": (80.0, 0.0)}

    def built(mode):
        global _ABUTMENT_BENCH_MODE
        was = _ABUTMENT_BENCH_MODE
        _ABUTMENT_BENCH_MODE = mode
        try:
            rec = build_segment(fld, edge, corr, [], fixed_pts=line,
                                census=None, joint={})
        finally:
            _ABUTMENT_BENCH_MODE = was
        return rec
    try:
        keep, drop = built("pinned"), built("detected")
    finally:
        del specmod.NODES["ab-abutment"], specmod.NODES["ab-far"]
    print("case: a declared abutment on a shore (REQUIRED: the freeboard band "
          "inside its level run is not a crossing; the shoulder sample UNDER "
          "the plane still is, and so is the channel 30 m on)")
    want("the run's crossing is the one station that dips under the plane",
         keep["crossings"][0]["stations"], [0, 0])
    want("NEUTER abutment_bench=detected: the freeboard band is joined to it "
         "(stations 0-3, the run)", drop["crossings"][0]["stations"], [0, 3])
    want("both read the channel past the run as a crossing, not at the leg's "
         "end", [(c["stations"][0] > 3, not c.get("terminal_banks"))
                 for c in keep["crossings"][1:] + drop["crossings"][1:]],
         [(True, True), (True, True)])
    want("the pinned stations 2-3 are no longer deck", (
        keep["is_bridge"][2:4], drop["is_bridge"][2:4]),
        ([False, False], [True, True]))
    print(f"abutment-bench selftest: {fails} failed")
    return fails


def _ar_field(top: float) -> "fieldmod.Field":
    """A hilltop at `top` m to x = 60, a 1 m/m fall to a 28 m channel at
    x = 72 -- the T2-temple-brgs1 shape (a cliff above the shore a declared
    abutment node stands on)."""
    half, n = 120.0, 240
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)
    h = np.where(X < 60.0, top,
                 np.where(X < 72.0, top - (X - 60.0) * (top - 28.0) / 12.0,
                          28.0))
    return fieldmod.Field("ar-hill", 0.0, 0.0, half, 1.0, n,
                          h.astype(np.float32),
                          np.ones((n, n), dtype=np.uint8))


def selftest_abutment_released() -> int:
    """ABUTMENT_TRIMMED_END_DEPENDS_ON, both named dependencies both ways,
    through `build_segment` on a hill built in code.  Two declared nodes are
    added to `spec.NODES` for the call and removed after."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    edge = {"id": "AR", "a": "ar-far", "b": "ar-abutment", "cls": "trunk",
            "pri": 3}
    specmod.NODES["ar-abutment"] = {
        "island": "ab", "patch": "ab", "kind": "abutment", "level_y": 30.6,
        "xz": (80.0, 0.0), "ground_y": 28.0}
    specmod.NODES["ar-far"] = {"island": "ab", "patch": "ab",
                               "kind": "extremity", "xz": (0.0, 0.0)}
    line = [[float(x), 0.0] for x in range(0, 82, 2)]

    def built(top, trim, mode):
        global _ABUTMENT_TRIMMED_END_MODE
        was = _ABUTMENT_TRIMMED_END_MODE
        _ABUTMENT_TRIMMED_END_MODE = mode
        try:
            fld = _ar_field(top)
            return build_segment(fld, edge, _ww_corridor(fld), [],
                                 fixed_pts=line, census=None, joint={},
                                 trim=trim)
        finally:
            _ABUTMENT_TRIMMED_END_MODE = was

    def read(rec):
        return dict(
            pinned=[(a["end"], a["within_clamp"]) for a in rec["abutments"]],
            released=[(a["end"], a["worst_cut_fill_m"])
                      for a in rec.get("abutments_released", [])],
            end_y=rec["profile_y"][-1])
    try:
        kept = built(40.0, None, "released")
        rel = built(40.0, {"b": 16}, "released")
        pin = built(40.0, {"b": 16}, "pinned")
        low_rel = built(33.0, {"b": 16}, "released")
        low_pin = built(33.0, {"b": 16}, "pinned")
    finally:
        del specmod.NODES["ar-abutment"], specmod.NODES["ar-far"]
    print("case: the terminal trim moved the end onto a 40 m hilltop, 9.4 m "
          "from the node's 30.6 m datum (REQUIRED: the pin the clamp cannot "
          "hold is released, recorded, and the road ends at the ground)")
    want("released and recorded, the end follows the hill",
         read(rel), dict(pinned=[], released=[("b", 9.4)], end_y=40.0))
    want("NEUTER trimmed_end=pinned (the shipped pin): 9.4 m outside the "
         "clamp, the hilltop dug to the datum",
         (read(pin)["pinned"], read(pin)["released"],
          read(pin)["end_y"] < 33.0),
         ([("b", False)], [], True))
    print("case: the trimmed end is only 2.4 m from the datum (REQUIRED: a "
          "pin the clamp CAN hold is never released)")
    want("held in both modes", (read(low_rel), read(low_pin)),
         (dict(pinned=[("b", True)], released=[], end_y=30.6),
          dict(pinned=[("b", True)], released=[], end_y=30.6)))
    print("case: no trim -- the line still ends AT its node (REQUIRED: the "
          "pin stays, however hard)")
    want("pinned, within the clamp over the water", read(kept)["pinned"],
         [("b", True)])
    print(f"abutment-released selftest: {fails} failed")
    return fails


def selftest_terminal_landing_plan() -> int:
    """TERMINAL_LANDING_PLAN_DEPENDS_ON, both named dependencies both ways,
    on a crossing recorded in code: 30 m from (20, 0) to (50, 0) at deck y
    36.0 over a field whose ground beyond the far bank is set per case."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    half, n = 120.0, 240
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)

    def verdict(beyond: float, terminal=("bank_b",), mode="refused",
                descent="span") -> str:
        global _TERMINAL_LANDING_PLAN_MODE
        fld = fieldmod.Field("tl", 0.0, 0.0, half, 1.0, n,
                             np.where(X < 49.0, 36.0, beyond).astype(
                                 np.float32),
                             np.ones((n, n), dtype=np.uint8))
        seg = {"crossings": [{
            "crossing_id": "FIX-tl-x01", "span_m": 30.0,
            "required_deck_y": 36.0, "terminal_banks": list(terminal),
            "bank_a": {"xz": [20.0, 0.0]}, "bank_b": {"xz": [50.0, 0.0]}}]}
        was, was_d = (_TERMINAL_LANDING_PLAN_MODE,
                      boardwalk._TERMINAL_DESCENT_MODE)
        _TERMINAL_LANDING_PLAN_MODE = mode
        boardwalk._TERMINAL_DESCENT_MODE = descent
        try:
            got = terminal_landing_rejection(seg, fld)
        finally:
            _TERMINAL_LANDING_PLAN_MODE = was
            boardwalk._TERMINAL_DESCENT_MODE = was_d
        return ("kept" if got is None
                else f"REJECTED gap {got['terminal_landing'][0]['gap_m']} "
                     f"> reach {got['terminal_landing'][0]['reach_m']}")

    print("case: a shore 1.2 m under the deck (REQUIRED: a landing meets it)")
    want("kept", verdict(34.8), "kept")
    print("case: ground 5.0 m under the deck -- past a landing alone (3.05 "
          "m), inside what the span gives back above the clearance floor "
          "(REQUIRED: kept, because the builder descends in the span too)")
    want("kept", verdict(31.0), "kept")
    want("NEUTER descent=landing: the plan asks a shorter reach than the "
         "builder has", verdict(31.0, descent="landing"),
         "REJECTED gap 5.0 > reach 3.05")
    print("case: ground 24 m under the deck -- a hillside no deck lands "
          "(T2-temple-brgs1: 52.757 over 27.7; REQUIRED: refused with its "
          "numbers)")
    want("rejected", verdict(12.0), "REJECTED gap 24.0 > reach 6.55")
    want("NEUTER terminal_landing=kept (the shipped plan): the leg is "
         "planned and 6.5 learns it", verdict(12.0, mode="kept"), "kept")
    print("case: the same hillside at a bank the road is meant to bench "
          "(REQUIRED: not this check's -- the road's own refusal)")
    want("kept", verdict(12.0, terminal=()), "kept")
    print("case: an unlandable band-free repair is REFUSED, not SERVABLE")
    fld = _ww_field(lambda X, Z: np.full_like(X, 30.366))
    corr = _ww_corridor(fld)
    rec = {"length_m": 30.0, "nodes": [[20.0, 0.0], [50.0, 0.0]],
           "census": {"stations_uncensused": 2},
           "crossings": [{
               "crossing_id": "FIX-unlandable", "span_m": 30.0,
               "required_deck_y": 57.344, "terminal_banks": ["bank_b"],
               "bank_a": {"xz": [20.0, 0.0]},
               "bank_b": {"xz": [50.0, 0.0]}}]}
    probed = band_free_probe(
        corr, corr.forbid, lambda **_kw: (rec, {}),
        water=lambda seg, joint, _build: (seg, joint),
        refusal=lambda seg: plan_refusal(seg, [], "FIXTURE", fld=fld,
                                         coverage=False))
    want("terminal landing rejection survives the band-free census probe",
         (probed["verdict"], probed.get("by")), ("REFUSED", "terminal_landing"))
    print(f"terminal-landing-plan selftest: {fails} failed")
    return fails


def _sb_ground(X, Z):
    """A 20 m channel (28.5 m, under the plane) between two 33.0 m banks."""
    return np.where((X >= 30.0) & (X < 50.0), 28.5, 33.0)


def selftest_span_bed() -> int:
    """BANK_SPAN_BED_DEPENDS_ON, each term both ways, through `build_segment`
    on a channel built in code.  The routing field is flat 33.0 m banks; the
    writer's LATTICE (what the gate reads) carries a crest over the free
    run's far end station -- the Ulfsland S4 / T9 shape, where the field read
    33.722 / 32.61 m and the lattice 33.998 / 33.234 m under a deck chosen
    from the field.  Two nodes are added to `spec.NODES` for the call and
    removed after."""
    global _PLAN_PATCHES, _SPAN_BED_MODE
    import ribbon as ribbonmod                             # noqa: PLC0415
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    print("case: the gate's own samples and the vertices its engine answers "
          "with (REQUIRED: the samples are the chord cut at 1 m, a "
          "half-lattice point asks BOTH vertices)")
    want("a 3 m chord is four samples", span_samples((0.0, 0.0), (3.0, 0.0)),
         [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])
    want("an off-line point asks its nearest vertex",
         lattice_vertices(10.4, 3.6), [(10, 4)])
    want("a half-lattice point asks both",
         lattice_vertices(10.5, 3.2), [(10, 3), (11, 3)])
    bed = {(0, 0): 31.0, (1, 0): 33.0, (2, 0): 32.0, (3, 0): 31.5}
    got = span_bed_floor((0.0, 0.0), (3.0, 0.0), lambda i, j: bed.get((i, j)))
    want("the floor is the highest vertex under the burial margin",
         (got["bed_max_y"], got["floor_y"]),
         (33.0, round(33.0 - CROSSING_BED_BURIAL_M, 3)))
    got = span_bed_floor((0.5, 0.0), (0.5, 0.0), lambda i, j: bed.get((i, j)))
    want("a tie takes the HIGHER of its two vertices (the reading can flip)",
         got["bed_max_y"], 33.0)
    want("a span nothing was read on has no floor",
         span_bed_floor((0.0, 0.0), (3.0, 0.0), lambda i, j: None)["floor_y"],
         None)

    fld = _ww_field(_sb_ground)
    corr = _ww_corridor(fld)
    line = [[float(x), 0.0] for x in range(0, 82, 2)]
    edge = {"id": "SB", "a": "sb-a", "b": "sb-b", "cls": "trunk", "pri": 3}
    specmod.NODES["sb-a"] = {"island": "sb", "patch": "sb",
                             "kind": "extremity", "xz": (0.0, 0.0)}
    specmod.NODES["sb-b"] = {"island": "sb", "patch": "sb",
                             "kind": "extremity", "xz": (80.0, 0.0)}

    def built(crest, mode):
        """`(H, over, record)`: the deck's height, how far the lattice's
        highest bed on the gate's own samples stands over it, and the
        crossing's record."""
        def lat(x, z):
            if 30.0 <= x < 50.0:
                return 28.5
            if 50.0 <= x <= 51.0 and abs(z) <= 2.0:
                return crest
            return 33.0
        global _PLAN_PATCHES, _SPAN_BED_MODE
        was = _PLAN_PATCHES, _SPAN_BED_MODE
        _PLAN_PATCHES = lambda _f, zones: {
            f"z_{x}_{z}": ribbonmod._flat_patch(x, z, lat) for x, z in zones}
        _SPAN_BED_MODE = mode
        try:
            rec = build_segment(fld, edge, corr, [], fixed_pts=line,
                                census=None, joint={})
        finally:
            _PLAN_PATCHES, _SPAN_BED_MODE = was
        c = rec["crossings"][0]
        beds = [max(lat(i, j) for i, j in lattice_vertices(x, z))
                for x, z in span_samples(tuple(c["bank_a"]["xz"]),
                                         tuple(c["bank_b"]["xz"]))]
        return c["required_deck_y"], round(max(beds) - c["required_deck_y"],
                                           3), c
    try:
        keep, drop = built(34.2, "lattice"), built(34.2, "banks")
        flat = built(33.0, "lattice")
        top = built(45.0, "lattice")
    finally:
        del specmod.NODES["sb-a"], specmod.NODES["sb-b"]
    gate_limit = 0.30      # accept/sections/roads.py CROSSING_BURIAL_MAX_M
    print("case: a crest 1.2 m over the field's bank on the lattice, over "
          "the free run's end station (REQUIRED: the deck clears it by the "
          "gate's own measure)")
    want("the deck stands at the crest less the margin",
         (keep[0], keep[1] <= gate_limit), (34.05, True))
    want("NEUTER span_bed=banks (the shipped floor): the deck stays at the "
         "field's bank and the gate would refuse it ('IS NOT A CROSSING')",
         (drop[0], drop[1] > gate_limit), (33.0, True))
    want("the record says what moved it",
         (keep[2]["span_bed"]["height_before_y"],
          keep[2]["span_bed"]["clipped_at_interval_top"]), (33.0, False))
    want("the legal interval's floor is the one it was chosen from",
         keep[2]["legal_deck_y_interval"][0], 34.05)
    print("case: a bed no higher than the deck (REQUIRED: nothing moves, "
          "no key is added -- Fresh42 stays byte-identical)")
    want("the height the field chose stands and the record is unchanged",
         (flat[0], "span_bed" in flat[2]), (33.0, False))
    print("case: a crest past the clamp's top (REQUIRED: stopped at the "
          "top, never forced, and the metres still over are on the record)")
    want("the deck stops at the interval's top",
         (top[0], top[2]["span_bed"]["clipped_at_interval_top"]), (40.0, True))
    want("what stands over it is measurable from the record", top[1] > gate_limit,
         True)
    print(f"span-bed selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# spec.MEASURED_SEED_DEPENDS_ON, NODE_ANCHOR_DEPENDS_ON and
# ORPHAN_JOIN_DEPENDS_ON, each both ways, on the Fresh42 drive-4 shapes.
# Neither reads nor writes a world: the terminal is spec's own table, the
# fields are synthetic, the plans are built in code.
# ---------------------------------------------------------------------------
def selftest_foreign_nodes() -> int:
    """T13-temple-harboursouth planned on Fresh42 to harbour-south's Pirate68
    point (11.5, -258.5): REFUSED at plan on Fresh42, ROUTED on Pirate68."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(mode, fn):
        was = specmod._MEASURED_SEED_MODE
        specmod._MEASURED_SEED_MODE = mode
        try:
            return fn()
        finally:
            specmod._MEASURED_SEED_MODE = was

    hs = specmod.CROSSING_TERMINALS["harbour-south"]
    table = {"harbour-south": {"xz": hs["xz"], "island": hs["island"],
                               "patch": hs["patch"],
                               "kind": "waterfront_terminal"}}

    def verdict(seed) -> str:
        kept, withheld = specmod.seed_scoped(
            table, measured_on=worldseed.DEFAULT_SEED, owner="Crossings",
            seed_=seed)
        nodes = {"temple": {"xz": (-6.14, 3.18)}, **kept}
        edges = [{"id": "T13-temple-harboursouth", "a": "temple",
                  "b": "harbour-south", "cls": "trunk", "pri": 1}]
        gone = specmod.split_foreign(edges, nodes, withheld)
        if gone:
            n = gone[0]["nodes"][0]
            return f"REFUSED at plan: {n} measured on " \
                   f"{withheld[n]['measured_on']}"
        return f"ROUTED to {tuple(nodes['harbour-south']['xz'])}"

    print("case: Fresh42 plans T13 to harbour-south (REQUIRED TO REFUSE)")
    want("Fresh42", verdict("Fresh42"),
         "REFUSED at plan: harbour-south measured on Pirate68")
    want("NEUTER seed=unscoped (the shipped read): Fresh42 routes to "
         "Pirate68's point", moded("unscoped", lambda: verdict("Fresh42")),
         "ROUTED to (11.5, -258.5)")
    print("case: the default seed keeps its own terminal "
          "(REQUIRED TO ROUTE)")
    want("Pirate68", verdict(worldseed.DEFAULT_SEED),
         "ROUTED to (11.5, -258.5)")
    print("case: the refusal carries THIS seed's reading at the point")
    fld = _ww_field(_ww_shore)
    r = foreign_reading(fld, 20.0, 0.0)
    want("a point 16.5 m off a synthetic shore",
         (r["land"], r["nearest_land_m"]), (False, 17.0))
    print(f"foreign nodes selftest: {fails} failed")
    return fails


def _na_flat(X, Z):
    return np.full_like(X, 40.0)


def selftest_node_anchor() -> int:
    """Two classes leave one protected node: the temple of Fresh42 drive 4,
    T3/T5/T13 on the trunk mask and S5 on the spur mask, 5.66 m apart."""
    global _NODE_ANCHOR_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fld = _ww_field(_na_flat, half=80.0)

    def corridor(cls, ring_m):
        c = routemod.Corridor(fld, cell_m=4.0, grade_max=routemod.GRADE[cls],
                              roughness_weight=0.0, bridge_entry_cost_m=300.0,
                              bridge_per_m_cost=15.0)
        ii, jj = np.mgrid[0:c.m, 0:c.m]
        c.forbid = np.hypot(c.x0 + jj * c.cell_m, c.z0 + ii * c.cell_m) <= ring_m
        return c
    # A protected ring grown by each class's own half-width: the trunk's mask
    # holds the spur's, as `poi.masks` makes it.
    trunk, spur = corridor("trunk", 29.0), corridor("spur", 27.0)
    nodes = {"temple": {"xz": (0.0, 0.0), "patch": "fx", "island": "i"},
             "meadhall": {"xz": (60.0, 0.0), "patch": "fx", "island": "i"},
             "southcape": {"xz": (-60.0, -60.0), "patch": "fx", "island": "i"}}
    edges = [{"a": "temple", "b": "meadhall", "cls": "trunk"},
             {"a": "temple", "b": "southcape", "cls": "spur"}]

    def verdict() -> str:
        anchors = node_anchors(edges, nodes,
                               lambda _p, c: {"trunk": trunk, "spur": spur}[c])
        starts = [corr.search(search_xz("temple", corr, nodes, anchors),
                              nodes[e["b"]]["xz"])[0]
                  for corr, e in ((trunk, edges[0]), (spur, edges[1]))]
        d = math.hypot(starts[0][0] - starts[1][0],
                       starts[0][1] - starts[1][1])
        return "ONE CELL" if d < 1e-9 else f"{d:.1f} m apart"

    def moded(mode):
        global _NODE_ANCHOR_MODE
        was = _NODE_ANCHOR_MODE
        _NODE_ANCHOR_MODE = mode
        try:
            return verdict()
        finally:
            _NODE_ANCHOR_MODE = was

    print("case: a trunk and a spur leave the temple (REQUIRED TO MEET)")
    want("both start on the trunk's snap", verdict(), "ONE CELL")
    want("NEUTER anchor=per_class (the shipped rule): each class snaps "
         "alone", moded("per_class"), "4.0 m apart")
    print(f"node anchor selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# route.TERMINAL_REACH_DEPENDS_ON, both ways, on S4's southeast in miniature.
# The node stands on flat ground in a 2 x 3-cell pocket (drive 6: 6 cells)
# that a one-cell ring of keep-out closes -- the tree-near and lh-south pads
# and the census bodies east and south of (388, -614).  The same ring read as
# UNREAD ground, and as a cliff, must still refuse: the census and the land
# own those, not the snap.
# ---------------------------------------------------------------------------
def _tr_flat(X, Z):
    return np.full_like(X, 40.0)


def _tr_mesa(X, Z):
    return np.where((np.abs(X - 20.0) < 14.0) & (np.abs(Z + 20.0) < 14.0),
                    80.0, 40.0)


def selftest_terminal_reach() -> int:
    """route.TERMINAL_REACH_DEPENDS_ON, both ways."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    def moded(mode, fn):
        was = routemod._TERMINAL_REACH_MODE
        routemod._TERMINAL_REACH_MODE = mode
        try:
            return fn()
        finally:
            routemod._TERMINAL_REACH_MODE = was

    node, far = (20.0, -20.0), (-60.0, 60.0)

    def corridor(ground, *, ring: bool, unread: bool | str):
        corr = _ww_corridor(_ww_field(ground, half=80.0))
        gi, gj = corr.cell_of(*node)
        forbid = np.zeros((corr.m, corr.m), dtype=bool)
        if ring:
            forbid[gi - 2:gi + 3, gj - 2:gj + 2] = True
            forbid[gi - 1:gi + 2, gj - 1:gj + 1] = False
        corr.forbid = forbid
        corr.unread = None
        if unread == "rim":
            corr.unread = np.zeros_like(forbid)
            corr.unread[gi - 2:gi + 3, gj + 1] = True
        elif unread:
            corr.unread = forbid.copy()
        return corr

    def verdict(corr, a, b) -> str:
        try:
            corr.search(a, b)
        except ValueError as exc:
            return f"REFUSED: {exc}"
        t = corr.last_terminal
        return ("ROUTED as snapped" if t is None else
                f"ROUTED, {t['end']} moved {t['moved_m']} m out of a "
                f"{t['pocket_cells']}-cell pocket")

    print("case: S4's southeast node in a pocket keep-outs close "
          "(REQUIRED TO ROUTE)")
    c = corridor(_tr_flat, ring=True, unread=False)
    want("the goal is re-snapped to the nearest cell the start reaches",
         verdict(c, far, node),
         "ROUTED, goal moved 6.32 m out of a 6-cell pocket")
    want("the same pocket at the START end", verdict(c, node, far),
         "ROUTED, start moved 6.32 m out of a 6-cell pocket")
    want("NEUTER nearest (the shipped snap): no route",
         moded("nearest", lambda: verdict(c, far, node)),
         "REFUSED: no route found")
    want("no pocket: the snap is untouched",
         verdict(corridor(_tr_flat, ring=False, unread=False), far, node),
         "ROUTED as snapped")
    print("case: the ring is UNREAD ground, the census's (REQUIRED TO REFUSE)")
    want("unread ground never moves an end",
         verdict(corridor(_tr_flat, ring=True, unread=True), far, node),
         "REFUSED: no route found")
    want("one side of the ring unread: the census's, not the snap's",
         verdict(corridor(_tr_flat, ring=True, unread="rim"), far, node),
         "REFUSED: no route found")
    print("case: the node on a mesa no grade reaches (REQUIRED TO REFUSE)")
    want("land that separates the ends never moves an end",
         verdict(corridor(_tr_mesa, ring=False, unread=False), far, node),
         "REFUSED: no route found")
    print(f"terminal reach selftest: {fails} failed")
    return fails


def _oj_leg(sid, a, b, nodes, width=4.0, pri=2, start=None):
    (x0, z0), (x1, z1) = start or nodes[a]["xz"], nodes[b]["xz"]
    n = max(2, int(math.hypot(x1 - x0, z1 - z0) / STATION_M) + 1)
    return {"id": sid, "from": a, "to": b, "width_m": width, "pri": pri,
            "nodes": [[x0 + (x1 - x0) * k / (n - 1),
                       z0 + (z1 - z0) * k / (n - 1)] for k in range(n)]}


def selftest_orphan_join() -> int:
    """The Fresh42 drive-4 shape: S8 wtsouth-treesth routed, the only legs
    that joined wtsouth (T10/T11 through wtspawn) refused, and S5's start
    5.66 m off the trunk's end at the temple."""
    global _ORPHAN_JOIN_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    nodes = {n: {"xz": xz, "island": "spawn"} for n, xz in (
        ("portalhub", (0.0, 0.0)), ("temple", (200.0, 0.0)),
        ("southcape", (200.0, -200.0)), ("wtsouth", (-150.0, -120.0)),
        ("treesth", (-150.0, -60.0)))}
    refused = [{"id": "T11-wtsouth-wtspawn", "from": "wtsouth",
                "to": "wtspawn"}]

    def plan():
        return [_oj_leg("T1-portalhub-temple", "portalhub", "temple", nodes,
                        width=6.0, pri=1),
                _oj_leg("S5-temple-southcape", "temple", "southcape", nodes,
                        start=(204.0, 4.0)),
                _oj_leg("S8-wtsouth-treesth", "wtsouth", "treesth", nodes)]

    def verdict(routes: bool, census_reads: bool = False) -> str:
        segs = plan()

        def route(edge):
            if routes:
                segs.append(_oj_leg(edge["id"], edge["a"], edge["b"], nodes,
                                    pri=edge["pri"]))
        rep = join_orphans(segs, refused, nodes,
                           {"T11-wtsouth-wtspawn": ("wtsouth", "wtspawn")},
                           route, lambda _eid: census_reads)
        left = rep["pieces_cut_off_after"]
        if left:
            waits = [j["via"] for j in rep["joins"]
                     if j["verdict"] == "WAITS_FOR_CENSUS"]
            return (f"{len(left)} piece(s) cut off: {left}"
                    + (f", waiting on the census for {waits}" if waits else "")
                    + f"; named {[j['refused_legs_naming_it'] for j in rep['joins']]}")
        return (f"walkable from portalhub via "
                f"{[j['via'] for j in rep['joins']]}")

    def moded(mode, fn):
        global _ORPHAN_JOIN_MODE
        was = _ORPHAN_JOIN_MODE
        _ORPHAN_JOIN_MODE = mode
        try:
            return fn()
        finally:
            _ORPHAN_JOIN_MODE = was

    print("case: S8 cut off, T11 refused (REQUIRED TO JOIN)")
    want("joined from the nearest served node",
         verdict(True), "walkable from portalhub via ['J-portalhub-treesth']")
    want("NEUTER join=off (the shipped plan): S8 emitted cut off",
         moded("off", lambda: verdict(True)),
         "1 piece(s) cut off: [['S8-wtsouth-treesth']]; named []")
    print("case: the join's ground is unread (REQUIRED TO WAIT, not to try "
          "a farther join)")
    want("the census is asked for the nearest join",
         verdict(False, census_reads=True),
         "1 piece(s) cut off: [['S8-wtsouth-treesth']], waiting on the "
         "census for ['J-portalhub-treesth']; named "
         "[['T11-wtsouth-wtspawn']]")
    print("case: a piece on a portal-only island (REQUIRED TO REJECT, "
          "not to emit)")
    west = dict(nodes, **{n: {"xz": xz, "island": "west"} for n, xz in (
        ("w-mid", (-5000.0, 0.0)), ("w-north", (-5000.0, 300.0)))})

    def island_verdict():
        segs = plan() + [_oj_leg("W3-wmid-wnorth", "w-mid", "w-north", west,
                                 pri=3)]
        rep = join_orphans(segs, [], west, {}, lambda _e: None,
                           lambda _e: False)
        return sorted((tuple(j["piece"]), j["verdict"]) for j in rep["joins"])
    want("the west-isle piece is refused, the spawn-island one reported",
         island_verdict(), [(("S8-wtsouth-treesth",), "ORPHANED"),
                            (("W3-wmid-wnorth",), "UNROOTED_ISLAND")])
    print("case: which islands a walk from the root reaches (REQUIRED: a "
          "declared crossing joins, an unbuildable one does not)")
    s1 = {"crossing_id": "S1", "joins": ["spawn", "west"]}
    two = {"portalhub": {"island": "spawn"}}
    want("Pirate68 shape, S1 declared", sorted(rooted_islands(two, "portalhub",
                                                              [s1])),
         ["spawn", "west"])
    want("Fresh42 shape, S1/S2 measured portal-only (none declared)",
         sorted(rooted_islands(two, "portalhub", [])), ["spawn"])
    want("NEUTER join=off: both emitted with no verdict",
         moded("off", island_verdict), [])
    print("case: a trimmed road no longer reaches its declared bridge bank")
    banks = dict(nodes, **{
        "east": {"xz": (300.0, 0.0), "island": "spawn"},
        "west": {"xz": (320.0, 0.0), "island": "west"},
        "shore": {"xz": (350.0, 0.0), "island": "west"},
        "town": {"xz": (600.0, 0.0), "island": "west"}})
    approach = _oj_leg("approach", "portalhub", "east", banks)
    departure = _oj_leg("departure", "west", "town", banks,
                        start=(420.0, 0.0))
    departure["abutments_released"] = [{"node": "west", "end": "a"}]
    want("a released bank cannot connect across a declared bridge",
         plan_components([approach, departure], [("east", "west")]),
         [["approach"], ["departure"]])
    shared = _oj_leg("shared", "west", "shore", banks)
    want("a released bank cannot connect through its old node name",
         plan_components([shared, departure], []),
         [["departure"], ["shared"]])
    candidates = join_candidates(["departure"], ["shared"],
                                 [shared, departure], banks)
    want("repair targets a node the orphan still serves",
         [(c["a"], c["b"]) for c in candidates],
         [("shore", "town"), ("west", "town")])
    want("a released root does not establish a served network",
         orphan_components([departure], root="west"), (None, [["departure"]]))
    held = _oj_leg("held", "west", "town", banks)
    want("a held bank still connects across the declared bridge",
         plan_components([approach, held], [("east", "west")]),
         [["approach", "held"]])
    touching = dict(shared, nodes=[[420.0, 0.0], [450.0, 0.0]])
    want("released metadata does not prevent a physical road junction",
         plan_components([touching, departure], []),
         [["departure", "shared"]])

    def feedback(*, alternate: bool, first_emitted: bool):
        segs = plan()
        attempts = []

        def route(edge):
            attempts.append(edge["id"])
            if len(attempts) == 1 and first_emitted:
                partial = _oj_leg(edge["id"], edge["a"], edge["b"], nodes)
                partial["nodes"] = [[1000.0, 1000.0], [1010.0, 1000.0]]
                partial["abutments_released"] = [
                    {"node": edge["a"], "end": "a"},
                    {"node": edge["b"], "end": "b"}]
                segs.append(partial)
            elif alternate and len(attempts) == (2 if first_emitted else 4):
                segs.append(_oj_leg(edge["id"], edge["a"], edge["b"], nodes))
        rep = join_orphans(segs, refused, nodes, {}, route, lambda _eid: False)
        rooted, _left = orphan_components(segs)
        return rep, rooted, attempts

    print("case: emitting a repair does not prove the old orphan was joined")
    rep, rooted, attempts = feedback(alternate=True, first_emitted=True)
    want("a disconnected emitted candidate is followed by a connecting repair",
         [verdict for _eid, verdict in rep["joins"][0]["tried"][:2]],
         ["ROUTED_DISCONNECTED", "ROUTED"])
    want("a farther actual approach roots the original orphan",
         "S8-wtsouth-treesth" in (rooted or []), True)
    rep, rooted, attempts = feedback(alternate=False, first_emitted=True)
    want("with no working alternative the original requirement stays failed",
         ("S8-wtsouth-treesth" in (rooted or []),
          any(j["verdict"] == "JOINED" for j in rep["joins"])), (False, False))
    rep, rooted, attempts = feedback(alternate=True, first_emitted=False)
    want("a fourth buildable candidate is not hidden by a nearest-three cap",
         ("S8-wtsouth-treesth" in (rooted or []), len(attempts)),
         (True, 4))
    many_nodes = {
        "portalhub": {"xz": (0.0, 0.0), "island": "spawn"},
        "root-end": {"xz": (10.0, 0.0), "island": "spawn"}}
    many_segments = [_oj_leg("root", "portalhub", "root-end", many_nodes)]
    for k in range(9):
        angle = 2.0 * math.pi * k / 9
        for prefix, radius in (("p", 1000.0), ("q", 1005.0)):
            many_nodes[f"{prefix}{k}"] = {
                "xz": (radius * math.cos(angle), radius * math.sin(angle)),
                "island": "spawn"}
        many_segments.append(_oj_leg(f"piece{k}", f"p{k}", f"q{k}",
                                     many_nodes))

    def connect_many(edge):
        many_segments.append(_oj_leg(edge["id"], edge["a"], edge["b"],
                                     many_nodes))

    many = join_orphans(many_segments, [], many_nodes, {}, connect_many,
                        lambda _id: False)
    want("all nine repairable groups are connected, not only the first eight",
         many["pieces_cut_off_after"], [])
    print(f"orphan join selftest: {fails} failed")
    return fails


def selftest_bridge_approaches() -> int:
    """Released/offset banks, real repair transitions and the census handoff."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    nodes = {n: {"xz": xz, "island": island} for n, xz, island in (
        ("portalhub", (0.0, 0.0), "spawn"),
        ("east", (40.0, 0.0), "spawn"), ("east2", (40.0, 100.0), "spawn"),
        ("west", (60.0, 0.0), "west"), ("west2", (60.0, 100.0), "west"),
        ("town", (260.0, 0.0), "west"),
        ("level-pad", (0.0, -100.0), "spawn"))}
    nodes["level-pad"]["level_y"] = 35.0
    crossings = [{"crossing_id": cid, "bank_a": {"node": a},
                  "bank_b": {"node": b}} for cid, a, b in (
                      ("FIX1", "east", "west"), ("FIX2", "east2", "west2"))]
    saved = specmod.NODES, specmod.DECLARED_CROSSINGS
    specmod.NODES, specmod.DECLARED_CROSSINGS = nodes, crossings
    try:
        root = _oj_leg("root", "portalhub", "east", nodes)
        root2 = _oj_leg("root2", "east", "east2", nodes)
        partial = _oj_leg("partial", "west", "town", nodes, start=(160.0, 0.0))
        partial["abutments_released"] = [{
            "node": "west", "end": "a", "stations_trimmed": 77,
            "worst_cut_fill_m": 25.386, "clamp_m": 7.0}]
        segs = [root, root2, partial]
        want("the 100 m bank gap remains measured, not a phantom bridge edge",
             (bridge_approach_gaps(partial)[0]["gap_m"],
              plan_components([root, partial])), (100.0, [["partial"], ["root"]]))
        unmarked = {k: v for k, v in partial.items() if k != "abutments_released"}
        want("forgetting release metadata cannot restore a distant bank name",
             "west" in _served_nodes(unmarked), False)
        nearby = _oj_leg("nearby", "west", "town", nodes, start=(62.0, 0.0))
        want("an existing 2 m footprint offset remains a served approach",
             bridge_approach_gaps(nearby), [])
        pad = _oj_leg("pad", "level-pad", "portalhub", nodes, start=(7.28, -100.0))
        want("a level pad is not mistaken for a declared bridge bank",
             (bridge_approach_gaps(pad), "level-pad" in _served_nodes(pad)),
             ([], True))
        reqs = bridge_approach_requirements(segs, nodes)
        want("declared obligations survive roads that released their banks",
             [(r["node"], r["verdict"]) for r in reqs],
             [("east", "SERVED"), ("west", "MISSING"),
              ("east2", "SERVED"), ("west2", "MISSING")])
        candidates = join_candidates(["partial"], ["root", "root2"], segs, nodes)
        want("only reached declared bridge banks extend the repair frontier",
             [(e["a"], e["b"]) for e in candidates],
             [("west", "town"), ("west2", "town")])
        edge = candidates[0]
        bad_probe = {**partial, "id": edge["id"]}
        want("a SERVABLE partial probe does not discharge an orphan",
             repair_connects(edge, segs, bad_probe), False)
        good_probe = _oj_leg(edge["id"], edge["a"], edge["b"], nodes)
        census = _cb_census([])
        p = {"verdict": "SERVABLE", "nodes": good_probe["nodes"],
             "length_m": 200.0, "stations": len(good_probe["nodes"]),
             "stations_unread": len(good_probe["nodes"])}
        wanted = census_wanted([], [{"id": edge["id"], "census_probe": p}], census)
        want("the attached unread alternative requests real census cells",
             (len(wanted["cells"]) > 0, wanted["zones"] > 0,
              probe_adoptable(p, census)), (True, True, False))
        want("the actual attached probe does discharge the orphan",
             repair_connects(edge, segs, good_probe), True)

        def census_route(_edge):
            pass  # A refused confined route emits nothing.
        waits = join_bridge_approaches(segs, nodes, census_route, lambda _id: True)
        want("a missing bank approach waits for the existing census loop",
             [(r["node"], r["verdict"]) for r in waits],
             [("west", "WAITS_FOR_CENSUS"), ("west2", "WAITS_FOR_CENSUS")])
        want("waiting does not pave a provisional connector", len(segs), 3)

        def route(edge):
            # A searched alternative may bend through the destination road;
            # its endpoints still reach the two declared banks.
            via = _oj_leg(edge["id"], edge["a"], "town", nodes)
            tail = _oj_leg(edge["id"], "town", edge["b"], nodes)
            via.update(to=edge["b"], nodes=via["nodes"] + tail["nodes"][1:])
            segs.append(via)
        repairs = join_bridge_approaches(segs, nodes, route, lambda _id: False)
        want("a general bank-to-bank approach can discharge both obligations",
             [(r["node"], r["verdict"]) for r in repairs], [("west", "JOINED")])
        want("the road graph, not candidate emission, confirms connectivity",
             orphan_components(segs)[1], [])
        want("no stale missing-bank requirement survives the real repair",
             [r["node"] for r in bridge_approach_requirements(segs, nodes)
              if r["verdict"] != "SERVED"], [])
    finally:
        specmod.NODES, specmod.DECLARED_CROSSINGS = saved
    print(f"bridge-approach selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# SPAN_REPRICE_DEPENDS_ON, both ways, on a channel in miniature.  A 60 m
# channel runs the width of the field and narrows to 16 m at a neck 620 m
# east of the direct line.  At the shipped price the direct crossing is the
# cheaper (W10-brgs1-treenear: 96.37 m MEASURED against a 96 m ceiling, the
# detour to a narrower neck dearer than the 0.37 m it saves), and the channel
# is water end to end so no land route exists for the land-only retry to
# find.  The ceiling is taken down to 40 m so the fixture stays small.
# ---------------------------------------------------------------------------
def _sr_ground(X, Z):
    chan = np.abs(Z) <= 30.0
    neck = (X >= 300.0) & (X <= 340.0) & (np.abs(Z) > 8.0)
    return np.where(chan & ~neck, 28.0, 32.0)


def selftest_span_reprice() -> int:
    """SPAN_REPRICE_DEPENDS_ON, both ways."""
    global MAX_BRIDGE_SPAN_M, _SPAN_REPRICE_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    corr = _ww_corridor(_ww_field(_sr_ground, half=400.0))
    a, b = (-300.0, 100.0), (-300.0, -100.0)

    def attempt():
        path = corr.search(a, b)
        run = best = 0
        for x, z in path:
            i, j = corr.cell_of(x, z)
            run = run + 1 if corr.water[i, j] else 0
            best = max(best, run)
        return ({"crossings": [{"span_m": best * corr.cell_m}],
                 "length_m": round(sum(math.hypot(p[0] - q[0], p[1] - q[1])
                                       for p, q in zip(path, path[1:])), 1),
                 "via_neck": any(x > 250.0 for x, _z in path)},)

    def raised(fn):
        try:
            fn()
        except ValueError as exc:
            return str(exc)
        return None

    ceiling, mode, price = MAX_BRIDGE_SPAN_M, _SPAN_REPRICE_MODE, \
        corr.bridge_per_m_cost
    land_exc = ValueError("no route found")
    try:
        MAX_BRIDGE_SPAN_M = 40.0
        first = attempt()[0]
        print("case: the direct crossing is 60 m against a 40 m ceiling and "
              "no land route exists (REQUIRED TO REPRICE)")
        want("the first route takes the direct crossing and breaks the "
             "ceiling", (first["via_neck"], first["crossings"][0]["span_m"]
                         > MAX_BRIDGE_SPAN_M), (False, True))
        (rec,), rep = span_reprice(corr, attempt, first_worst=60.0,
                                   land_exc=land_exc)
        want("the first rung that measures inside the ceiling is taken",
             (rep["factor"], rec["via_neck"],
              rec["crossings"][0]["span_m"] <= MAX_BRIDGE_SPAN_M),
             (2.0, True, True))
        want("the corridor's price is put back", corr.bridge_per_m_cost,
             price)
        _SPAN_REPRICE_MODE = "land_only"
        want("NEUTER mode=land_only (the shipped retry): the land-only "
             "'no route found' stands", raised(lambda: span_reprice(
                 corr, attempt, first_worst=60.0, land_exc=land_exc)),
             "no route found")
        _SPAN_REPRICE_MODE = mode
        print("case: the narrowest neck is 16 m against an 8 m ceiling "
              "(REQUIRED TO REFUSE, with the measured minimum)")
        MAX_BRIDGE_SPAN_M = 8.0
        msg = raised(lambda: span_reprice(corr, attempt, first_worst=60.0,
                                          land_exc=land_exc)) or ""
        want("refused, naming the least span any price reaches",
             ("still bridge 16.0 m at the least" in msg,
              "SPAN_REPRICE_DEPENDS_ON" in msg), (True, True))
        want("the corridor's price is put back", corr.bridge_per_m_cost,
             price)
    finally:
        MAX_BRIDGE_SPAN_M, _SPAN_REPRICE_MODE = ceiling, mode
    print(f"span reprice selftest: {fails} failed")
    return fails


# ---------------------------------------------------------------------------
# ADJACENCY_REROUTE_DEPENDS_ON, both ways, on a stub builder.  The builder
# stands in for `build_segment`: a leg has hairpin feet at fixed places, and
# a search that may not enter the disc round a foot no longer takes that
# hairpin -- S14-eastlobe-ferryeast joined on its fourth search, after discs
# on the later feet of three refused lines (68, 63 and 76 refused pairs).
# ---------------------------------------------------------------------------
def selftest_adjacency_reroute() -> int:
    """ADJACENCY_REROUTE_DEPENDS_ON, both ways."""
    global _ADJACENCY_REROUTE_MODE
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    corr = _ww_corridor(_ww_field(_tr_flat, half=400.0))

    def builder(feet, calls, *, fail_after=None):
        def one(mask):
            calls.append(mask)
            if fail_after is not None and len(calls) > fail_after:
                raise ValueError("no route found")
            for x, z in feet:
                i, j = corr.cell_of(x, z)
                if mask is None or not mask[i, j]:
                    return ({"length_m": 100.0 + len(calls),
                             "self_adjacency": {
                                 "verdict": "REFUSED", "pairs_before": 60,
                                 "worst_before": {"xz": [[x - 5.0, z],
                                                         [x, z]]}}}, {})
            return ({"length_m": 100.0 + len(calls),
                     "self_adjacency": {"verdict": "CLEAR"}}, {})
        return one

    feet3 = [(-200.0, 0.0), (-100.0, 0.0), (0.0, 0.0)]
    calls: list = []
    print("case: three hairpins, each cleared by the disc on its later foot "
          "(REQUIRED TO RE-ROUTE)")
    rec, _j = adjacency_reroute(corr, builder(feet3, calls))
    ar = rec.get("adjacency_reroute") or {}
    want("the fourth search is the clean one",
         (len(calls), ar.get("rounds")), (4, 3))
    want("the discs stand on the later feet, 2 m wider each round",
         [d[:2] + [d[2]] for d in ar.get("discs", [])],
         [[-200.0, 0.0, 8.0], [-100.0, 0.0, 10.0], [0.0, 0.0, 12.0]])
    want("the clean line is the leg", rec["self_adjacency"]["verdict"],
         "CLEAR")
    calls = []
    rec, _j = adjacency_reroute(corr, builder([], calls))
    want("a line the first search leaves clear is searched once and "
         "carries no record", (len(calls), "adjacency_reroute" in rec),
         (1, False))
    _ADJACENCY_REROUTE_MODE = "off"
    try:
        calls = []
        rec, _j = adjacency_reroute(corr, builder(feet3, calls))
        want("NEUTER mode=off (the shipped plan): the refused line stands, "
             "searched once", (rec["self_adjacency"]["verdict"], len(calls)),
             ("REFUSED", 1))
    finally:
        _ADJACENCY_REROUTE_MODE = "reroute"
    print("case: more hairpins than rounds, and a re-search that raises "
          "(REQUIRED TO RETURN THE LEG AS ROUTED)")
    many = [(-350.0 + 60.0 * k, 0.0) for k in range(ADJACENCY_ROUNDS + 2)]
    calls = []
    rec, _j = adjacency_reroute(corr, builder(many, calls))
    want("no round joins: the first line is returned, after every round",
         (rec["self_adjacency"]["verdict"], "adjacency_reroute" in rec,
          len(calls), rec["length_m"]),
         ("REFUSED", False, ADJACENCY_ROUNDS + 1, 101.0))
    calls = []
    rec, _j = adjacency_reroute(corr, builder(feet3, calls, fail_after=1))
    want("a re-search that raises: the first line is returned",
         (rec["self_adjacency"]["verdict"], "adjacency_reroute" in rec,
          rec["length_m"]), ("REFUSED", False, 101.0))
    print(f"adjacency reroute selftest: {fails} failed")
    return fails


def main(*, plan_state: dict | None = None) -> int:
    if sys.argv[1:2] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan"])
    ap.add_argument("--field", default=FIELD_BIN)
    ap.add_argument("--only", action="append")
    ap.add_argument("--pri", type=int, action="append")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"directory for segments.yaml / crossings.yaml "
                         f"(default: {DEFAULT_OUT}; pass {LIVE_OUT} to "
                         f"overwrite the live plan)")
    ap.add_argument("--dry-run", action="store_true",
                    help="route and report, write nothing; prints the sha256 "
                         "of each document it would have written so a caller "
                         "can diff against the file on disk without touching it")
    worldseed.add_arguments(ap)
    # CENSUS-BEFORE-ROUTE IS THE ORDER, NOT AN OPTION.  `--poi-source
    # {all,generator}` USED TO LIVE HERE and it is gone: it selected between
    # the mod-free generator dump and that dump plus a SURVEY FILE, and
    # neither of those is a reading of the world the road will cross.  The
    # dump cannot see More_World_Locations at all (it is not part of world
    # generation) and `mod_keepout.json` was measured after the roads were
    # built, on one world.  A future world gets neither.
    #
    # So exactly one of these two is required, and the two are not
    # interchangeable:
    ap.add_argument("--census",
                    help="the census artefact `precensus.py survey` wrote "
                         "AFTER generating the zones this road crosses. "
                         "Required for a plan that will be paved.")
    ap.add_argument("--provisional", action="store_true",
                    help="route with NO census, to give `precensus.py survey` "
                         "a line to read along. Every segment is stamped "
                         "PROVISIONAL and ribbon.py refuses to pave it.")
    ap.add_argument("--order", choices=["spec", "reverse"], default="spec",
                    help="the order edges are ROUTED in. A plan must not "
                         "depend on it -- every leg is profiled alone and the "
                         "legs that meet are solved jointly, and the document "
                         "is emitted in the spec's order either way -- and "
                         "`reverse` exists to prove exactly that")
    ap.add_argument("--census-band-free", action="store_true",
                    help="do not confine the search to the censused band. "
                         "Recorded per segment; the coverage gate still "
                         "refuses an uncensused station, so this trades a "
                         "constraint for a refusal.")
    args = ap.parse_args()

    global SEED, WORLD, _PLAN_KEEPOUTS, _PLAN_PATCHES, _PLAN_PAD_KEEPOUTS
    SEED, WORLD = worldseed.apply(args)
    # A SITE A PERSON DECLARED IS A NODE THIS PLAN MUST ROUTE TO.  A published
    # site set that does not carry it is not "a plan with one node fewer": it is
    # a road network that has forgotten a city.  `spec` imported anyway (so the
    # tools that only want a helper keep working); the one consumer that would
    # route without the node stops here, with the refusal spec recorded.
    if specmod.DECLARED_STALE:
        raise SystemExit(specmod.DECLARED_STALE)
    # THE WRITER'S OWN POI DISCS, for every plan-time writer check
    # (WALKED_KEEPOUT_DEPENDS_ON).
    _PLAN_KEEPOUTS = writer_keepouts
    _PLAN_PATCHES = writer_patches
    import ribbon as ribbonmod                             # noqa: PLC0415
    plan_pads = ribbonmod.pad_keepouts_from_chain(
        [], [], planned=ribbonmod.planned_pad_claims(str(WORLD), str(SEED)))
    _PLAN_PAD_KEEPOUTS = lambda: plan_pads

    # THE FIELD MUST BE THIS SEED'S FIELD.  PatchScan stamps the seed name
    # into the header and `load` used to discard it; with --seed a live
    # parameter, planning one seed against another's heightmap succeeds
    # silently and returns a road fitted to ground the world does not have.
    field_header = fieldmod.require_seed(args.field, SEED)
    flds = fieldmod.load(args.field)
    # THE ROUTER'S KEEP-OUT SET IS NOW TWO POPULATIONS, COUNTED SEPARATELY.
    # `poi.load()` is the world GENERATOR's location dump -- a [SEED] fact,
    # mod-free by construction, and correct as far as it goes.  The census is
    # everything else that is standing: read live, after the zones were
    # generated, from the world this road is actually being built in.
    # `poi.load_all()` is NOT used here any more: it folds
    # `roads/mod_keepout.json` into the dump, and a survey of one world's
    # finished carriageways is not a reading of this one's corridor.  That
    # file still has a job -- `precensus.py replay` turns it into a census
    # artefact with its coverage and its date attached, which is the form in
    # which it can be checked instead of trusted.
    if args.provisional and args.census:
        raise SystemExit("--provisional and --census are the two ends of the "
                         "same pipeline; pass one")
    census = (None if args.provisional
              else censusmod.require(Path(args.census) if args.census else None,
                                     world=WORLD, seed=SEED))
    pois = poimod.load()
    census_pois = [] if census is None else census.pois()
    if census is not None:
        fresh = censusmod.freshness_gate(
            census, JUMPSTART / "ledger" / "runs" / str(WORLD))
        print(f"census {args.census}: {len(census.bodies)} bodies over "
              f"{len(census.cells)} cells, band half "
              f"{census.band_half_m} m, measured {census.measured_utc}")
        print(f"  freshness vs the ledger: {fresh['verdict']} "
              f"({fresh['stale_count']}/{fresh['zones_checked']} censused "
              f"zones generated after the census)")
        for st in fresh["stale"][:4]:
            print(f"    zone {st['zone']} generated {st['generated_utc']} "
                  f"(seq {st['generated_seq']}, {st['actor']}) > censused "
                  f"{st['censused_utc']}")
    else:
        fresh = {"verdict": "NOT_ASKED",
                 "why": "provisional run: there is no census to date-check"}
    out_dir = Path(args.out)
    corridors: dict[tuple[str, str], routemod.Corridor] = {}
    free_forbids: dict[tuple[str, str], np.ndarray] = {}
    segments = []
    unroutable: list[dict] = []
    rejected: list[dict] = []
    poi_masked: dict[str, list] = {}
    # A PROVISIONAL PLAN KEEPS EVERY LINE ITS SEARCH FOUND for the census to
    # read (precensus.band_lines), refused or not: stage 11 routes the leg
    # again, and ground nobody read under a line the router found is a leg
    # stage 11 can never serve.  MEASURED on Fresh42 drive 3: S13 refused
    # here with its line in hand, never read, "no allowed cell within 200 m"
    # at stage 11.
    census_lines: list[dict] = []

    def keep_for_census(eid: str, patch: str, nodes, why: str) -> None:
        if census is None and nodes:
            census_lines.append({"id": eid, "patch": patch,
                                 "nodes": [list(p)[:2] for p in nodes],
                                 "why": f"kept for the census to read: {why}"})

    def probe(edge: dict, key: tuple[str, str], patch: str) -> dict | None:
        """This leg band-free (CENSUS_BAND_DEPENDS_ON), or None when there
        is no confinement to lift."""
        if census is None or args.census_band_free:
            return None
        fld_p, corr = worldseed.resolve(flds, patch), corridors[key]

        def build(**kw):
            ef = kw.pop("extra_forbid", None)

            def one(adj=None):
                jo: dict = {}
                m = ef if adj is None else (adj if ef is None else ef | adj)
                return (build_segment(fld_p, edge, corr, pois + census_pois,
                                      census=census, joint=jo,
                                      extra_forbid=m, **kw), jo)
            rec, j = adjacency_reroute(corr, one)
            if span_worst(rec) > MAX_BRIDGE_SPAN_M:
                first_worst = span_worst(rec)
                j = {}
                try:
                    rec = build_segment(fld_p, edge, corr, pois + census_pois,
                                        land_only=True, census=census,
                                        joint=j, extra_forbid=ef, **kw)
                except DrownedFootprint:
                    raise
                except ValueError as land_exc:
                    # SPAN_REPRICE_DEPENDS_ON: no land-only route is not
                    # proof there is none inside the ceiling.
                    def again():
                        jj: dict = {}
                        return (build_segment(fld_p, edge, corr,
                                              pois + census_pois,
                                              census=census, joint=jj, extra_forbid=ef, **kw),
                                jj)
                    (rec, j), rep = span_reprice(
                        corr, again, first_worst=first_worst,
                        land_exc=land_exc)
                    rec["span_reprice"] = rep
            return rec, j
        got = band_free_probe(
            corr, free_forbids[key], build,
            water=lambda rec, j, b: water_route(
                fld_p, corr, rec, j, build=b,
                refusal=lambda c: plan_refusal(c, pois, SEED, fld=fld_p,
                                               coverage=False)),
            refusal=lambda c: plan_refusal(c, pois, SEED, fld=fld_p,
                                            coverage=False))
        return {**got, "patch": patch}

    def probe_note(p: dict | None) -> str:
        if not p:
            return ""
        if p["verdict"] == "SERVABLE":
            return (f"  [band-free: SERVABLE {p['length_m']} m, "
                    f"{p['stations_unread']} of {p['stations']} station(s) on "
                    f"unread ground -> "
                    + ("census_wanted]" if census is not None and band_deficit(
                        p["nodes"], census) else
                       "NOT ROUTED: servable_unrouted]"))
        return (f"  [band-free: {p['verdict']} "
                f"{p.get('by') or p.get('why', '')[:120]} -- not the census's]")

    def adopt(p: dict | None, eid: str, confined_why: str) -> bool:
        """Route this leg by its band-free probe's line when every station
        it writes and its whole band are read (CENSUS_ADOPT_DEPENDS_ON)."""
        if census is None or not probe_adoptable(p, census):
            return False
        seg, joint = p.pop("_rec"), p.pop("_joint")
        seg["census_adopted"] = {
            "depends_on": list(CENSUS_ADOPT_DEPENDS_ON),
            "confined": confined_why[:240],
            "why": "the confined search refused a line the band-free probe "
                   "serves with every written station and its whole band on "
                   "read ground"}
        print(f"{eid:22s} CONFINED REFUSED ({confined_why[:100]}) -> ADOPTED "
              f"band-free {seg['length_m']} m, {len(seg['nodes'])} station(s) "
              f"and band read")
        admit(seg, joint)
        return True

    def admit(seg: dict, joint: dict) -> None:
        """The leg passes every plan-time gate: into the plan."""
        if "write_gate" not in seg:
            seg["write_gate"] = write_gate(seg, pois, SEED)
        water_judged[seg["id"]] = list(seg["profile_y"])
        zs = zones_of_segment(seg)
        seg["zones_carriageway"] = sorted([list(z) for z in zs])
        seg["zdo_cost"] = len(zs)
        segments.append(seg)
        joints[seg["id"]] = joint
        print(f"{seg['id']:22s} p{seg['pri']} {seg['cls']:5s} {seg['length_m']:7.1f} m  "
              f"grade max {seg['grade_max_measured']*100:5.2f}%/{seg['grade_limit']*100:.0f}%  "
              f"cut {seg['cut_max_m']:6.2f} fill {seg['fill_max_m']:5.2f}  "
              f"clamp8 {seg['stations_over_clamp']:3d} margin7 {seg['stations_over_design_margin']:3d}  "
              f"brg {seg['bridged_stations']:3d}st/{len(seg['crossings'])}x  "
              f"zones {seg['zdo_cost']:3d}  poi-viol {len(seg['poi_hard_violations'])}  "
              f"write-gate {seg['write_gate']['verdict']}"
              f"/{seg['write_gate']['marginal_violations']}m  "
              f"census {seg['census'].get('verdict')}  "
              f"pad-trim {sum(seg['pad_edge']['stations_trimmed'].values())}  "
              f"self-adj {seg['self_adjacency']['verdict']} "
              f"{seg['self_adjacency']['pairs_before']}->"
              f"{seg['self_adjacency']['pairs_after']}"
              + (f" moved {seg['self_adjacency']['stations_moved']}st "
                 f"<={seg['self_adjacency']['max_move_m']}m"
                 if seg['self_adjacency']['stations_moved'] else ""))

    edges = [e for e in specmod.EDGES
             if (not args.only or e["id"] in args.only)
             and (not args.pri or e["pri"] in args.pri)]
    if args.order == "reverse":
        edges = edges[::-1]
    # WHAT THE JOINT SOLVE NEEDS OF EACH LEG, kept beside the record rather
    # than in it (see `build_segment`'s `joint`).
    joints: dict[str, dict] = {}
    # EACH LEG'S OWN WATER REMEDIES, for WATER_REREMEDY_DEPENDS_ON:
    # (field, corridor, build, the remedy its line took, refusal).
    remedies: dict[str, tuple] = {}
    # THE PROFILE EACH LEG'S WATER WAS JUDGED ON, so the pass after the joint
    # solve re-asks exactly the legs the solve moved.
    water_judged: dict[str, list] = {}
    def corridor_for(patch: str, cls: str) -> tuple[str, str]:
        """The corridor a leg of class `cls` is searched in on `patch`,
        built once; returns its key into `corridors`."""
        edge = {"cls": cls}     # the block below names the class through it
        key = (patch, cls)
        if key not in corridors:
            fld = worldseed.resolve(flds, patch)
            corr = routemod.Corridor(
                fld, cell_m=4.0, grade_max=routemod.GRADE[edge["cls"]],
                # Crossings MEASURED that SPAN LENGTH is nearly free to it (a
                # wood deck is any length in 8 m bays), and asked me not to
                # detour to find a narrow neck.  That is about WHERE to cross a
                # channel the route has to cross -- it is not licence to
                # substitute bridge for road.  With water at 3 road-metres per
                # metre the router MEASURABLY did exactly that: it planned a
                # 136 m bridge on the 442 m temple->S1 segment to avoid roughly
                # 400 m of walking, which is terrible value for a structure
                # nobody had measured.  At 10 per metre plus a 250 m entry, that
                # same crossing costs 1,610 road-metres and the router only
                # bridges when the land detour is genuinely long -- while still
                # choosing the flattest place to cross rather than the narrowest,
                # because the per-metre term is small next to the gradient term.
                # RETUNED AGAIN, and the measurement that forced it: at 10
                # road-metres per water metre the router took SHORELINE
                # SHORTCUTS over open water in preference to going inland, and
                # six segments came out with bridged runs of 103 m, 111 m,
                # 175 m, 184 m, 318 m and 418 m -- every one of them past
                # Crossings' MEASURED 96 m absolute span ceiling, i.e. six
                # bridges that cannot be built. At 60 per metre plus a 400 m
                # entry, a 100 m span costs 6,400 road-metres and is only taken
                # when the land detour is longer than that, while the 12 m
                # channel Crossings measured still costs only 1,120 -- so real
                # short crossings survive and shoreline shortcuts do not.
                # Water is priced MODERATELY now that the span ceiling is a
                # geometric constraint rather than a price (see
                # Corridor.water_forbidden).  A crossing the search can still
                # propose is one a bridge can physically reach across, so the
                # price only has to express "prefer land, and cross at the
                # flattest place rather than the narrowest" -- which is exactly
                # what Crossings asked for.
                bridge_entry_cost_m=300.0, bridge_per_m_cost=15.0)
            # THE MASK IS BUILT FROM BOTH POPULATIONS.  The generator dump
            # answers the [SEED] question and the census answers the
            # "what is standing here now" question; a router given only the
            # first has MEASURED 38 mod bodies inside built carriageways.
            forbid, soft, touched = poimod.masks(
                pois + census_pois, corr.x0, corr.z0, corr.cell_m, corr.m,
                WIDTH_M[edge["cls"]] / 2.0)
            # AND EVERY PAD THE BUILD STANDS A BODY ON, written or planned
            # (PAD_CLAIMS_DEPENDS_ON): hard-forbidden, so the search bends
            # round a building instead of `enforce_pad_edge` refusing the leg
            # for crossing it after the fact.
            pad_forbid = pad_forbid_mask(
                pad_claims(), corr.x0, corr.z0, corr.cell_m, corr.m,
                routemod.footprint_half_m(WIDTH_M[edge["cls"]]))
            # THE ARRIVAL SPUR'S MASK (ARRIVAL_DEPENDS_ON): everything the
            # search may not enter EXCEPT the pads, which the spur reads by
            # rectangle so it can stop at the one it serves.
            arrival_forbid = forbid
            forbid = forbid | pad_forbid
            # THE SAME MASKS WITH THE CONFINEMENT OFF, kept for the band-free
            # probe (CENSUS_BAND_DEPENDS_ON): the bodies the census FOUND
            # stay out, the ground it has not read is let in.
            free_forbids[key] = forbid
            cen_stats = {"confined": False,
                         "why": "provisional run: nothing has been read yet"}
            if census is not None and not args.census_band_free:
                # CONFINED TO GROUND THAT HAS BEEN READ.  Without this the
                # search is free to leave the censused cells and the coverage
                # gate can only refuse afterwards; with it the router finds the
                # in-band answer, and the gate still proves it -- because the
                # confinement is shrunk by the chord slack THIS line carries
                # (precensus.CENSUS_CONFINE_DEPENDS_ON).
                cen_forbid, cen_stats = censusmod.census_forbid(
                    census, corr.x0, corr.z0, corr.cell_m, corr.m,
                    chord_slack_m=chord_slack_m(corr.cell_m))
                forbid = forbid | cen_forbid
                arrival_forbid = arrival_forbid | cen_forbid
                cen_stats["confined"] = True
                corr.unread = cen_forbid
            elif census is not None:
                cen_stats = {"confined": False,
                             "why": "--census-band-free: the search may leave "
                                    "the censused cells and the coverage gate "
                                    "refuses the result instead"}
            corr.forbid, corr.soft = forbid, soft
            corr.arrival_forbid = arrival_forbid
            poi_masked[f"{patch}:{edge['cls']}"] = {
                "instances_overlapping_grid": len(touched),
                "cells_hard_forbidden": int(forbid.sum()),
                "cells_soft_penalised": int((soft > 0).sum()),
                "cells_pad_forbidden": int(pad_forbid.sum()),
                "pads_masked": len(pad_claims()),
                "grid_cells": int(corr.m * corr.m),
                "protected_overlapping": sum(1 for r in touched if r["protected"]),
                "census_confinement": cen_stats,
            }
            corridors[key] = corr
        return key

    # THE LEGS TO A NODE MEASURED ON ANOTHER SEED (spec.MEASURED_SEED_
    # DEPENDS_ON): not routed, refused with THIS seed's reading at the node.
    for fe in specmod.FOREIGN_EDGES:
        fedge = fe["edge"]
        if (args.only and fedge["id"] not in args.only) or (
                args.pri and fedge["pri"] not in args.pri):
            continue
        readings = []
        for nid in fe["nodes"]:
            fn = specmod.FOREIGN_NODES[nid]
            try:
                here = foreign_reading(worldseed.resolve(flds, fn["patch"]),
                                       *fn["xz"])
            except KeyError:
                here = {"window": fn["patch"], "in_window": False}
            readings.append({"node": nid, "xz": list(fn["xz"]),
                             "measured_on": fn["measured_on"], "here": here})
        owner = specmod.FOREIGN_NODES[fe["nodes"][0]]["owner"]
        text = "; ".join(foreign_text(r["node"], r["xz"], r["measured_on"],
                                      r["here"]) for r in readings)
        unroutable.append({
            "id": fedge["id"], "from": fedge["a"], "to": fedge["b"],
            "cls": fedge["cls"], "pri": fedge["pri"],
            "error": f"FOREIGN NODE: {text}",
            "depends_on": list(specmod.MEASURED_SEED_DEPENDS_ON),
            "foreign_nodes": readings, "owner": owner,
            "why": "a node measured on another seed is not a node of this "
                   "one; a road routed to it ends where that seed had "
                   "something and this one has nothing"})
        print(f"{fedge['id']:22s} p{fedge['pri']} FOREIGN NODE: {text} -- "
              f"owner {owner}")

    # THE ISLANDS A WALK FROM THE ROOT CAN REACH AT ALL (ORPHAN_JOIN_
    # DEPENDS_ON): the root's own and every island a DECLARED crossing joins
    # to it.  A leg anywhere else is not routed -- nothing it paves is
    # walkable from the root, and routing it would only send the census to
    # read ground for a road the plan must refuse.  A partial run is not
    # asked.
    reach_isl = (rooted_islands(specmod.NODES)
                 if not args.only and not args.pri
                 and _ORPHAN_JOIN_MODE == "join" else set())

    # ONE CELL PER NODE FOR EVERY CLASS THAT MEETS THERE
    # (NODE_ANCHOR_DEPENDS_ON), over the legs this run can route.
    routable = [e for e in edges if specmod.NODES[e["a"]]["island"]
                == specmod.NODES[e["b"]]["island"]
                and (not reach_isl
                     or specmod.NODES[e["a"]]["island"] in reach_isl)]
    _NODE_ANCHORS.clear()
    _ARRIVAL_STARTS.clear()
    _NODE_ANCHORS.update(node_anchors(
        routable, specmod.NODES, lambda p, c: corridors[corridor_for(p, c)]))
    print(f"node anchors at PLAN time: {len(_NODE_ANCHORS)} node(s) served "
          f"by more than one carriageway width start every leg from one cell"
          + (f" ({', '.join(f'{n} {list(v)}' for n, v in sorted(_NODE_ANCHORS.items()))})"
             if _NODE_ANCHORS else "")
          + f"; mode {_NODE_ANCHOR_MODE}; key = network.NODE_ANCHOR_DEPENDS_ON")

    def route_edge(edge: dict) -> None:
        """One leg through every plan-time gate: admitted into the plan, or
        recorded as refused with its number."""
        a_node = specmod.NODES[edge["a"]]
        if specmod.NODES[edge["b"]]["island"] != a_node["island"]:
            # An edge between two islands is a finding, not a crash: it means a
            # destination was assigned to the wrong landmass, and the honest
            # output is a record saying so rather than a dead run.
            unroutable.append({
                "id": edge["id"], "from": edge["a"], "to": edge["b"],
                "cls": edge["cls"], "pri": edge["pri"],
                "error": "endpoints are on different landmasses",
                "from_island": a_node["island"],
                "to_island": specmod.NODES[edge["b"]]["island"],
                "why": "a road cannot span two landmasses; this needs a bridge or "
                       "a ferry from Crossings, or the destination is portal-only",
            })
            print(f"{edge['id']:22s} p{edge['pri']} CROSS-ISLAND: "
                  f"{a_node['island']} -> {specmod.NODES[edge['b']]['island']}")
            return
        if (_ORPHAN_JOIN_MODE == "join" and reach_isl
                and a_node["island"] not in reach_isl):
            why = (f"island {a_node['island']!r} holds no node a walk from "
                   f"{ORPHAN_ROOT} reaches (reachable: "
                   f"{', '.join(sorted(reach_isl))}) and no declared crossing "
                   f"joins it to them on this seed; a road there is walkable "
                   f"from nowhere the gate walks from. Owner: spec.EDGES, "
                   f"authored for an island this seed reaches by portal only")
            unroutable.append({
                "id": edge["id"], "from": edge["a"], "to": edge["b"],
                "cls": edge["cls"], "pri": edge["pri"],
                "error": f"UNROOTED ISLAND: {why}",
                "depends_on": list(ORPHAN_JOIN_DEPENDS_ON), "why": why})
            print(f"{edge['id']:22s} p{edge['pri']} UNROOTED ISLAND: "
                  f"{a_node['island']} (reachable from {ORPHAN_ROOT}: "
                  f"{', '.join(sorted(reach_isl))})")
            return
        patch = a_node["patch"]
        key = corridor_for(patch, edge["cls"])
        searched: list = []
        base_trim: dict | None = None
        try:
            def first(*, trim=None, _p=patch, _k=key, _e=edge):
                def one(adj=None):
                    jo: dict = {}
                    return (build_segment(worldseed.resolve(flds, _p), _e,
                                          corridors[_k], pois + census_pois,
                                          census=census, joint=jo, trim=trim,
                                          line_out=searched,
                                          extra_forbid=adj), jo)
                return adjacency_reroute(corridors[_k], one)
            # A drowned footprint at a terminus is the terminal trim's to
            # serve, not a refusal ahead of it (DROWNED_TRIM_DEPENDS_ON).
            seg, joint, base_trim = build_past_drowned(first)
            if seg.get("terminal_reach"):
                tr = seg["terminal_reach"]
                print(f"{edge['id']:22s} TERMINAL IN A POCKET: {tr['node']} "
                      f"snapped {tr['snapped_xz']} in {tr['pocket_cells']} "
                      f"cell(s) only keep-outs close -> ends at "
                      f"{tr['moved_to_xz']}, {tr['moved_m']} m from "
                      f"{tr['requested_xz']}; key = "
                      f"route.TERMINAL_REACH_DEPENDS_ON")
            if base_trim is not None:
                print(f"{edge['id']:22s} DROWNED TERMINUS as routed: "
                      f"{seg['drowned_terminus']['stations_refused']} "
                      f"station(s) -> trim {base_trim}, length "
                      f"{seg['length_m']} m")
            worst = span_worst(seg)
            if worst > MAX_BRIDGE_SPAN_M:
                # LAND-ONLY RETRY.  The span ceiling is already encoded
                # geometrically (water further than half the maximum span from
                # land is not traversable), but that bounds a crossing's WIDTH,
                # not its LENGTH: a route running 200 m parallel to the coast
                # 20 m offshore is inside the width bound at every step and is
                # still a 200 m "bridge".  MEASURED on three segments (114.8 m,
                # 174.7 m, 200.6 m).  Rather than add water-run length to the
                # search state -- which would multiply a 640,000-cell grid by
                # the run bound -- re-route the offending segment with water
                # forbidden outright.  If a land route exists it is the right
                # answer anyway.  If none exists that is a proof about LAND and
                # no more: the leg is then re-priced (SPAN_REPRICE_DEPENDS_ON)
                # rather than refused with the land-only "no route found".
                joint = {}
                try:
                    land = build_segment(worldseed.resolve(flds, patch), edge,
                                         corridors[key], pois + census_pois,
                                         land_only=True, census=census,
                                         joint=joint, trim=base_trim,
                                         line_out=searched)
                except DrownedFootprint:
                    raise
                except ValueError as land_exc:
                    (seg, joint, base_trim), rep = span_reprice(
                        corridors[key], lambda: build_past_drowned(first),
                        first_worst=worst, land_exc=land_exc)
                    seg["span_reprice"] = rep
                    print(f"{edge['id']:22s} SPAN REPRICED: first route "
                          f"bridged {worst} m, no land route; water x"
                          f"{rep['factor']:g} -> longest bridge "
                          f"{span_worst(seg)} m, {seg['length_m']} m; key = "
                          f"network.SPAN_REPRICE_DEPENDS_ON")
                else:
                    land["land_only_retry"] = {
                        "first_attempt_worst_span_m": worst,
                        "why": "first route proposed a bridged run past "
                               "Crossings' 96 m absolute span ceiling; "
                               "re-routed with water forbidden",
                        "length_penalty_m": round(
                            land["length_m"] - seg["length_m"], 1),
                    }
                    seg = land
        except ValueError as exc:
            # An unroutable edge is a FINDING, not a crash.  A destination on a
            # different landmass, or one whose every approach is inside a
            # protected POI's keep-out, has no road and the plan has to say so:
            # a segment silently dropped from a network file reads as an
            # oversight to the next reader, which is exactly how "why is there
            # no road to the watchtower" becomes unanswerable.
            p0 = probe(edge, key, patch)
            if adopt(p0, edge["id"], str(exc)):
                return
            unroutable.append({
                "id": edge["id"], "from": edge["a"], "to": edge["b"],
                "cls": edge["cls"], "pri": edge["pri"], "patch": patch,
                "error": str(exc),
                "from_xz": list(a_node["xz"]),
                "to_xz": list(specmod.NODES[edge["b"]]["xz"]),
                "why": "A* found no admissible path, or the line it found was "
                       "refused. Either the endpoints are on different land "
                       "components (this seed has thousands), or every "
                       "corridor into the target is inside a protected "
                       "location's keep-out, or -- when a census confines the "
                       "search -- the ground the leg needs was never read. "
                       "`census_probe` distinguishes the last case from the "
                       "rest: it routes THIS leg with the confinement off, "
                       "and only a SERVABLE probe names cells for the census "
                       "(CENSUS_BAND_DEPENDS_ON).",
                "census_probe": p0,
                "census_confined": (None if census is None else
                                    not args.census_band_free),
            })
            keep_for_census(edge["id"], patch, searched, str(exc)[:200])
            print(f"{edge['id']:22s} p{edge['pri']} UNROUTABLE: {exc}"
                  + probe_note(unroutable[-1]["census_probe"])
                  + (f"  [line kept for the census: {len(searched)} "
                     f"stations]" if census is None and searched else ""))
            return
        # THE WRITER'S WATER, ASKED BEFORE ANY OTHER GATE READS THE LINE --
        # see `water_route`.  A leg the writer would lay comes back untouched.
        fld_p = worldseed.resolve(flds, patch)

        def build_leg(*, extra_forbid=None, trim=None, ceiling=None,
                      _edge=edge, _fld=fld_p, _corr=corridors[key],
                      _retry=seg.get("land_only_retry"),
                      _reprice=seg.get("span_reprice"),
                      _adj=seg.get("adjacency_reroute"),
                      _drowned=seg.get("drowned_terminus")):
            j: dict = {}
            ef = extra_forbid
            if _adj is not None:
                # ADJACENCY_REROUTE_DEPENDS_ON: a remedy rebuild keeps the
                # discs that took the leg off its refused hairpin.
                am = adjacency_mask(_corr, _adj["discs"])
                ef = am if ef is None else ef | am
            rec = at_water_price(
                _corr, None if _reprice is None else _reprice["factor"],
                lambda: build_segment(_fld, _edge, _corr, pois + census_pois,
                                      land_only=_retry is not None,
                                      census=census, joint=j,
                                      extra_forbid=ef,
                                      trim=trim, ceiling=ceiling))
            if _retry is not None:
                rec["land_only_retry"] = _retry
            if _reprice is not None:
                rec["span_reprice"] = _reprice
            if _adj is not None:
                rec["adjacency_reroute"] = _adj
            if _drowned is not None:
                rec["drowned_terminus"] = _drowned
            return rec, j
        leg_build = tracked_build(keep_trim(build_leg, base_trim), None)
        seg, joint = water_route(
            fld_p, corridors[key], seg, joint, build=leg_build,
            refusal=lambda cand: plan_refusal(cand, pois, SEED, fld=fld_p))
        # WATER_REREMEDY_DEPENDS_ON: the leg's own remedies, kept with the
        # remedy its solo line took, for the pass after the joint solve.
        remedies[seg["id"]] = (fld_p, corridors[key],
                               keep_trim(build_leg, base_trim),
                               leg_build.used.get(id(seg)),
                               lambda cand: plan_refusal(cand, pois, SEED,
                                                         fld=fld_p))
        wwr = seg["writer_water"]
        if wwr["as_routed"]["verdict"] != "CLEAR":
            print(f"{seg['id']:22s} WRITER WATER as routed: "
                  f"{wwr['as_routed']['steps']} step(s) / "
                  f"{wwr['as_routed']['drained']} drain(s), worst "
                  f"{wwr['as_routed']['worst_m']} m at "
                  f"{wwr['as_routed']['worst_at']} -> {wwr['remedy']}: "
                  f"{wwr['steps']} / {wwr['drained']}, length "
                  f"{wwr['as_routed']['length_m']} -> {seg['length_m']} m")
        water_judged[seg["id"]] = list(seg["profile_y"])
        # A BRIDGED RUN LONGER THAN CROSSINGS CAN BUILD IS A REJECTED SEGMENT,
        # not a crossing to hand over.  MEASURED ceiling from Crossings' own
        # WearNTear implementation: 72 m free span at a 3x safety factor on an
        # iron stringer, 96 m absolute.  Handing over a 330 m "bridge" would be
        # the mirror image of the defect this project already paid for in the
        # other direction (a 12 m bridge planned over a 40 m river).
        worst_span = max((c["span_m"] for c in seg["crossings"]), default=0.0)
        if worst_span > MAX_BRIDGE_SPAN_M:
            seg["rejected"] = {
                "why": f"longest bridged run is {worst_span:.1f} m, past Crossings' "
                       f"MEASURED {MAX_BRIDGE_SPAN_M} m absolute span ceiling "
                       f"(96 m iron stringer). Re-route inland or make it a ferry.",
                "worst_span_m": worst_span,
            }
            rejected.append({"id": seg["id"], **seg["rejected"]})
            keep_for_census(seg["id"], patch, seg["nodes"], "span")
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {seg['rejected']['why']}")
            return
        # A TERMINAL CROSSING NO LANDING CAN MEET IS A REJECTED SEGMENT, with
        # its number -- see `terminal_landing_rejection`.
        rej = terminal_landing_rejection(seg, fld_p)
        if rej is not None:
            seg["rejected"] = rej
            rejected.append({"id": seg["id"], **rej})
            keep_for_census(seg["id"], patch, seg["nodes"], "terminal landing")
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {rej['why']}")
            return
        # AN UNCENSUSED STATION IS A REJECTED SEGMENT.  This is the check that
        # would have caught the ordering defect: it compares the FINISHED line
        # against the cells a socket census actually answered for, which is a
        # different artefact from the PatchScan field the line was planned on.
        # The walkability check that declared 84 impassable locations WALKABLE
        # composed its reference from the same field as the thing under test
        # and therefore could not fail; this one can, and does -- see
        # `precensus.selftest`.
        cg = seg["census"]
        if cg.get("verdict") == "UNCENSUSED":
            p0 = probe(edge, key, patch)
            if adopt(p0, seg["id"], f"{cg['stations_uncensused']} station(s) "
                                    f"on unread ground"):
                return
            seg["rejected"] = {
                "why": f"{cg['stations_uncensused']} of {cg['stations']} "
                       f"stations stand on ground the census never read "
                       f"(first at {cg['first_uncensused'][:3]}). A route over "
                       f"unread ground is exactly how 42 standing bodies "
                       f"ended up under a carriageway. `census_probe` routes "
                       f"it band-free; a SERVABLE probe's unread cells are in "
                       f"this plan's `census_wanted` (band half "
                       f"{cg['band_half_m']} m).",
                "stations_uncensused": cg["stations_uncensused"],
                "census_probe": p0,
            }
            rejected.append({"id": seg["id"], **seg["rejected"]})
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: "
                  f"{seg['rejected']['why']}"
                  + probe_note(seg["rejected"]["census_probe"]))
            return
        # A LEG THE WRITE GATE WILL REFUSE IS A REJECTED SEGMENT, and the
        # router is where that sentence belongs -- see `write_gate`.
        seg["write_gate"] = write_gate(seg, pois, SEED)
        rej = write_gate_rejection(seg)
        if rej is not None:
            seg["rejected"] = rej
            rejected.append({"id": seg["id"], **rej})
            keep_for_census(seg["id"], patch, seg["nodes"], "write gate")
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {rej['why']}")
            return
        # A LINE WHOSE OWN BRANCHES NO EARTHWORK CAN JOIN IS A REJECTED
        # SEGMENT, with its number -- see `self_adjacency_rejection`.  The
        # re-profile that joins the rest already happened in `build_segment`.
        rej = self_adjacency_rejection(seg)
        if rej is not None:
            seg["rejected"] = rej
            rejected.append({"id": seg["id"], **rej})
            keep_for_census(seg["id"], patch, seg["nodes"], "self-adjacency")
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {rej['why']}")
            return
        # A LEG THE WRITER WOULD STILL REFUSE ON WATER IS REFUSED HERE, with
        # its number and its owner (`water_rejection`) -- not at step 4.  A
        # PROVISIONAL plan keeps it, refusal on it, for the census to read.
        rej = water_rejection(seg)
        if rej is not None and census is not None:
            # Water the band would not let the alignment leave is the
            # census's to answer, and only the probe can tell it from water
            # no alignment leaves: it routes the remedy band-free.
            rej["census_probe"] = probe(edge, key, patch)
            if adopt(rej["census_probe"], seg["id"],
                     f"writer water {rej['writer_water_steps']} step(s)"):
                return
            seg["rejected"] = rej
            rejected.append({"id": seg["id"], **rej})
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {rej['why']}"
                  + probe_note(rej.get("census_probe")))
            return
        admit(seg, joint)

    for edge in edges:
        route_edge(edge)

    # ---- EVERY LEG THE PLAN LAYS IS WALKABLE FROM THE ROOT ----------------
    # (ORPHAN_JOIN_DEPENDS_ON).  A partial run (--only / --pri) has no
    # network to be connected to and is not asked.
    join_edges: list[dict] = []
    edge_ends = {e["id"]: (e["a"], e["b"]) for e in specmod.EDGES}
    edge_ends.update({fe["edge"]["id"]: (fe["edge"]["a"], fe["edge"]["b"])
                      for fe in specmod.FOREIGN_EDGES})

    def route_join(edge: dict) -> None:
        join_edges.append(edge)
        route_edge(edge)

    def join_servable(eid: str) -> bool:
        rec = next((r for r in unroutable + rejected if r["id"] == eid), None)
        p = (rec or {}).get("census_probe") or {}
        if census is None or p.get("verdict") != "SERVABLE":
            return False
        edge = next(e for e in join_edges if e["id"] == eid)
        candidate = p.get("_rec")
        if candidate is None or not repair_connects(edge, segments, candidate):
            p.update(verdict="REFUSED", by="repair_connectivity")
            if candidate is not None:
                p["bridge_approach_gaps"] = bridge_approach_gaps(candidate)
            return False
        return bool(band_deficit(p.get("nodes") or [], census))

    orphan_join = None
    if not args.only and not args.pri:
        orphan_join = join_orphans(segments, unroutable + rejected,
                                   specmod.NODES, edge_ends, route_join,
                                   join_servable,
                                   reachable_islands=reach_isl)
        orphan_join["reachable_islands"] = sorted(reach_isl)
        print(f"orphan join at PLAN time: "
              f"{len(orphan_join['pieces_cut_off_before'])} piece(s) of the "
              f"plan cut off from {orphan_join['root']} as routed, "
              f"{sum(1 for j in orphan_join['joins'] if j['verdict'] == 'JOINED')}"
              f" JOINED, "
              f"{sum(1 for j in orphan_join['joins'] if j['verdict'] == 'WAITS_FOR_CENSUS')}"
              f" waiting on the census, "
              f"{len(orphan_join['pieces_cut_off_after'])} still cut off; "
              f"mode {_ORPHAN_JOIN_MODE}; key = network.ORPHAN_JOIN_DEPENDS_ON")
        for j in orphan_join["joins"]:
            print(f"   {', '.join(j['piece'])}: {j['verdict']} "
                  f"{j['via'] or ''}  refused legs naming it: "
                  f"{', '.join(j['refused_legs_naming_it']) or 'none'}; "
                  f"tried {j['tried']}")
        # A PIECE NO LEG CAN JOIN, ON AN ISLAND THE ROOT DOES NOT SERVE, is
        # REJECTED with its number: nothing it paves is walkable from the
        # root (ORPHAN_JOIN_DEPENDS_ON).
        for j in orphan_join["joins"]:
            if j["verdict"] != "UNROOTED_ISLAND":
                continue
            for lid in j["piece"]:
                sg = next(s for s in segments if s["id"] == lid)
                sg["rejected"] = {"why": j["why"],
                                  "orphan_join": "UNROOTED_ISLAND",
                                  "depends_on": list(ORPHAN_JOIN_DEPENDS_ON)}
                rejected.append({"id": lid, **sg["rejected"]})
                print(f"{lid:22s} REJECTED: {j['why']}")
        _gone = {lid for j in orphan_join["joins"]
                 if j["verdict"] == "UNROOTED_ISLAND" for lid in j["piece"]}
        segments[:] = [s for s in segments if s["id"] not in _gone]
        orphan_join["bridge_approach_repairs"] = (
            join_bridge_approaches(segments, specmod.NODES, route_join,
                                   join_servable)
            if _ORPHAN_JOIN_MODE == "join" else [])

    # THE DOCUMENT IS IN THE SPEC'S ORDER, NOT THE ORDER THE EDGES WERE
    # ROUTED IN.  Identical to what this emitted before for the default
    # order, and it is what lets two routing orders be compared byte for
    # byte instead of record by record.  A foreign leg and a join are ranked
    # after the spec's own, in the order they were refused or derived.
    rank = {e["id"]: i for i, e in enumerate(
        list(specmod.EDGES) + [fe["edge"] for fe in specmod.FOREIGN_EDGES]
        + join_edges)}
    segments.sort(key=lambda sg: rank[sg["id"]])
    unroutable.sort(key=lambda r: rank[r["id"]])
    rejected.sort(key=lambda r: rank[r["id"]])
    corr_rank = {}
    for e in specmod.EDGES:
        k = f"{specmod.NODES[e['a']]['patch']}:{e['cls']}"
        corr_rank.setdefault(k, len(corr_rank))
    poi_masked = {k: poi_masked[k]
                  for k in sorted(poi_masked, key=lambda k: corr_rank.get(
                      k, len(corr_rank)))}

    # ---- LEGS THAT MEET AGREE, AT PLAN TIME ------------------------------
    # Every leg above was profiled ALONE.  Here the legs that share ground
    # are solved as one system (route.JUNCTION_DATUM_DEPENDS_ON); a leg no
    # joint profile can satisfy is REJECTED with its number, exactly as the
    # write gate and the self-adjacency rejections above are.
    jd_pads, jd_pads_missing = pad_participants()
    water_cache: dict = {}
    gate_before: dict = {}

    def solve_network() -> dict:
        gate_before["v"] = junction_population(segments, jd_pads)
        return water_after_joint(segments, joints, jd_pads, flds,
                                 water_judged, water_cache)

    def water_now(sg: dict) -> dict:
        key = (sg["id"], tuple(sg["profile_y"]))
        if key not in water_cache:
            water_cache[key] = writer_water(
                sg, worldseed.resolve(flds, sg["patch"]))
        return water_cache[key]

    def remedy_leg(sg: dict, _fw: dict) -> tuple[dict, dict] | None:
        """WATER_REREMEDY_DEPENDS_ON: `water_route` on the joint-solved
        profile, on top of the remedy the leg's line already took."""
        r = remedies.get(sg["id"])
        if r is None:
            return None
        fld_p, corr, build, base, refusal = r
        b = tracked_build(build, base)
        out, j = water_route(fld_p, corr, sg, joints[sg["id"]], build=b,
                             refusal=refusal)
        if out is sg:
            return None
        remedies[sg["id"]] = (fld_p, corr, build, b.used.get(id(out), base),
                              refusal)
        out["write_gate"] = write_gate(out, pois, SEED)
        zs = zones_of_segment(out)
        out["zones_carriageway"] = sorted([list(z) for z in zs])
        out["zdo_cost"] = len(zs)
        for k in [k for k in water_cache if k[0] == sg["id"]]:
            del water_cache[k]
        return out, j

    jd, reremedied = remedied_after_joint(
        segments, joints, water_judged, solve=solve_network, water=water_now,
        remedy=remedy_leg)
    jd_before = gate_before["v"]
    for m in reremedied:
        jr = m["joint_refusal"]
        print(f"{m['id']:22s} WATER AFTER JOINT round {m['round']}: "
              f"{jr['steps']} step(s) ({jr['keepout_steps']} poi) / "
              f"{jr['drained']} drain(s), worst {jr['worst_m']} m at "
              f"{jr['worst_at']} on the joint-solved profile -> "
              f"{m['remedy']}, {m['length_m']} m; key = "
              f"network.WATER_REREMEDY_DEPENDS_ON")
    # A PROVISIONAL PLAN KEEPS A REFUSED LEG, WITH ITS REFUSAL ON IT.  That
    # line exists only so `precensus.py survey` knows where to read, and
    # ribbon.py refuses to pave it anyway; dropping it here would leave its
    # corridor unread, and the census-confined route at stage 11 -- which
    # MEASURED on Fresh42 routes T9 differently and joins it -- would then be
    # refused as UNCENSUSED for a disagreement on a line nobody builds.
    kept_refused = set()
    for rec in jd["refused"]:
        rej = junction_rejection(rec)
        if census is None:
            kept_refused.add(rec["id"])
            sg = next(s for s in segments if s["id"] == rec["id"])
            sg["junction_datum"] = {"verdict": "REFUSED", **rej}
            print(f"{rec['id']:22s} REFUSED (kept: provisional line, for "
                  f"the census to read): {rej['why']}")
            continue
        rejected.append({"id": rec["id"], **rej})
        print(f"{rec['id']:22s} REJECTED: {rej['why']}")
    gone = {r["id"] for r in jd["refused"]} - kept_refused
    if gone:
        segments = [sg for sg in segments if sg["id"] not in gone]
        rejected.sort(key=lambda r: rank[r["id"]])
    jd_after = junction_population(segments, jd_pads)
    junction_datum = {
        "depends_on": list(_JUNCTION_DATUM_DEPENDS_ON),
        "rule": routemod.JUNCTION_DATUM_RULE,
        "tool": "roads/route.py::junction_datums",
        "readings": jd["readings"],
        "population": jd.get("population"),
        "places": jd.get("places"),
        "pads": {"participants": len(jd_pads),
                 "without_a_datum": sorted(jd_pads_missing)},
        "gate_before": jd_before,
        "gate_after": jd_after,
        "legs": {sid: f"{v['verdict']} -- {v['datum']}"
                 for sid, v in sorted(jd["legs"].items())},
        "refused": [{"id": r["id"], **r["binding"]} for r in jd["refused"]],
    }
    print(f"junction datum at PLAN time: gate before -- "
          f"{jd_before['pairs_sharing_ground']} pairs in "
          f"{jd_before['places']} shared places, "
          f"{jd_before['places_over_allowance']} place(s) / "
          f"{jd_before['impassable_pairs']} pair(s) over the allowance; "
          f"after -- {jd_after['pairs_sharing_ground']} pairs in "
          f"{jd_after['places']} places, "
          f"{jd_after['places_over_allowance']} / "
          f"{jd_after['impassable_pairs']} over; "
          f"{sum(1 for v in jd['legs'].values() if v['verdict'] == 'JOINED')}"
          f" leg(s) joined, "
          f"{sum(1 for v in jd['legs'].values() if v['verdict'] == 'AGREED')}"
          f" agreed as designed, "
          f"{sum(1 for v in jd['legs'].values() if v['verdict'] == 'UNSHARED')}"
          f" share no ground, {len(jd['refused'])} REJECTED; places where "
          f"legs meet: {(jd.get('places') or {}).get('count')}, at ONE "
          f"DATUM {(jd.get('places') or {}).get('at_one_datum')}; key = "
          f"route.JUNCTION_DATUM_DEPENDS_ON {_JUNCTION_DATUM_DEPENDS_ON}")
    for sid, v in sorted(jd["legs"].items()):
        if v["verdict"] == "UNSHARED":
            continue
        wb, wa = v["worst_before"] or {}, v["worst_after"] or {}
        print(f"   {sid:24s} {v['verdict']:7s} with {len(v['shares_ground_with'])}"
              f" participant(s), {v['over_allowance_before']} -> "
              f"{v['over_allowance_after']} reading(s) over the allowance, "
              f"{v['over_design_budget_before']} -> "
              f"{v['over_design_budget_after']} over the design budget; worst "
              f"{wb.get('spread_m')} m at {wb.get('xz')} ({wb.get('reading')})"
              f" -> {wa.get('spread_m')} m; moved {v['stations_moved']} st "
              f"<= {v['max_move_m']} m, rung {v['rung_design']} -> {v['rung']};"
              f" {v['datum'].split(':')[0]}")

    # ---- THE WRITER'S WATER AGAIN, ON THE PROFILE THAT WILL BE EMITTED -----
    # The joint solve moves profiles inside each leg's own bounds, and a
    # walked-ray ceiling is in those bounds -- but a leg the solve RAISED
    # beside water is re-asked, because the verdict is about the plan that
    # is written, not the one that was routed.
    for sg in segments:
        rec = sg.get("writer_water")
        if rec is None:
            continue
        if sg["profile_y"] != water_judged.get(sg["id"]):
            key = (sg["id"], tuple(sg["profile_y"]))
            if key not in water_cache:
                sg.pop("connection_collar", None)
                water_cache[key] = writer_water(
                    sg, worldseed.resolve(flds, sg["patch"]))
            fw = water_cache[key]
            # The collar is re-decided on the profile that will be EMITTED,
            # and the leg records it for ribbon.stamp (COLLAR_CHOICE).
            if fw.get("collar") is False:
                sg["connection_collar"] = False
            else:
                sg.pop("connection_collar", None)
            # keepout_steps rides along: the rejection line prints it, and a
            # dropped key printed "(0 poi)" on a leg refused BY a POI disc.
            rec["after_junction"] = {k: fw[k] for k in (
                "verdict", "steps", "drained", "worst_m", "worst_at",
                "keepout_steps", "pad_steps", "arrival_failures")}
            rec["after_junction"].update(judged_public(fw))
            if fw["verdict"] == "REFUSED":
                rec["residual_owner"] = water_owner(
                    sg, fw, routemod.footprint_half_m(float(sg["width_m"]))
                    + writer_batter()[1])
        final = rec.get("after_junction") or rec
        rec["final_verdict"] = final_verdict(final)
    water_refused = [sg for sg in segments
                     if (sg.get("writer_water") or {}).get("final_verdict")
                     == "REFUSED"]
    print(f"writer water at PLAN time: "
          f"{sum(1 for sg in segments if 'writer_water' in sg) - len(water_refused)}"
          f" leg(s) the writer lays clear of water, "
          f"{sum(1 for sg in segments if (sg.get('writer_water') or {}).get('remedy', 'as routed') != 'as routed')}"
          f" moved or re-profiled to get there, {len(water_refused)} still "
          f"REFUSED; key = route.WALKED_WET_DEPENDS_ON")
    for sg in water_refused:
        rec = sg["writer_water"]
        fin = rec.get("after_junction") or rec
        print(f"   REFUSED {sg['id']} {fin['steps']} water step(s), "
              f"{fin['drained']} drain(s), worst {fin['worst_m']} m at "
              f"{fin['worst_at']} after {rec['remedy']} (the joint solve "
              f"moved it)")
    # REFUSED AFTER THE JOINT SOLVE IS REFUSED, exactly as a junction
    # refusal is -- and a PROVISIONAL plan keeps it for the census.
    if census is not None and water_refused:
        gone_w = {sg["id"] for sg in water_refused}
        for sg in water_refused:
            rej = water_rejection(sg, final=True)
            sg["rejected"] = rej
            rejected.append({"id": sg["id"], **rej})
        segments = [sg for sg in segments if sg["id"] not in gone_w]
        rejected.sort(key=lambda r: rank[r["id"]])

    # ---- WHERE THE CENSUS MUST STILL READ (CENSUS_BAND_DEPENDS_ON) ---------
    wanted = None
    if census is not None:
        wanted = census_wanted(segments, unroutable + rejected, census)
        for sg in segments:
            n = next((len(lg["cells"]) for lg in wanted["legs"]
                      if lg["id"] == sg["id"]), 0)
            sg["census"]["band_unread_cells"] = n
        for r in unroutable + rejected:
            for k in ("nodes", "_rec", "_joint"):
                (r.get("census_probe") or {}).pop(k, None)
        by_kind = [lg for lg in wanted["legs"] if lg["kind"] == "written"]
        print(f"census band at PLAN time: {len(segments) - len(by_kind)} "
              f"written line(s) with their whole {census.band_half_m:g} m "
              f"band read, {len(by_kind)} with "
              f"{sum(len(lg['cells']) for lg in by_kind)} band cell(s) unread, "
              f"{len(wanted['legs']) - len(by_kind)} refused leg(s) the router "
              f"would serve over unread ground; WANTED "
              f"{len(wanted['cells'])} cell(s) in {wanted['zones']} zone(s), "
              f"about {len(wanted['cells']) * censusmod.SURVEY_S_PER_CELL / 60:.0f}"
              f" min of survey; key = network.CENSUS_BAND_DEPENDS_ON "
              f"{CENSUS_BAND_DEPENDS_ON}")
        for lg in wanted["legs"]:
            print(f"   {lg['id']:24s} {len(lg['cells']):4d} cell(s)  {lg['why']}")
        for u in wanted["servable_unrouted"]:
            print(f"   {u['id']:24s} SERVABLE {u['length_m']} m over read "
                  f"ground and NOT ROUTED -- owner {u['owner']}")

    all_zones = sorted({tuple(z) for sg in segments
                        for z in sg["zones_carriageway"]})
    total_len = sum(sg["length_m"] for sg in segments)
    paved_m2 = sum(sg["terrain_stations"] * sg["station_m"] * sg["width_m"] for sg in segments)
    worst_grade = max((sg["grade_max_measured"] for sg in segments), default=0.0)
    over = sum(sg["stations_over_clamp"] for sg in segments)
    over_m = sum(sg["stations_over_design_margin"] for sg in segments)
    # ONE PHYSICAL CHANNEL, ONE DECLARATION - and this line is where the
    # duplicates came from.  `find_crossings` runs PER SEGMENT, so a channel
    # that TWO routed segments both have to get across is emitted TWICE,
    # once under each segment's name, and a plain concatenation ships both.
    #
    # MEASURED on Ulfsland: 14 detected records describing 10 channels, plus
    # 3 declared - 17 declarations for 13 channels, 714.33 m of declared
    # span for 539.38 m of water.  W10-brgs1-treenear-x01 and
    # W5-brgs1-wsouth-x01 are the SAME BANK PAIR to the metre; so are
    # W7-wwest-wnorthwest-x01 and W9-vestvik-lhwest-x01; T4-x01/T7-x02 and
    # T4-x02/T7-x01 are the same water approached from opposite ends.
    #
    # EVERY CONSUMER PAID FOR IT.  `accept` counted 17 obligations and
    # credited ONE BUILT DECK to EVERY declaration sharing its corridor - so
    # "3 of 17 declared crossings carry a walker" had TWO OF ITS THREE
    # PASSES BEING ONE DECK - and a builder taking the file at face value
    # puts a second deck in the same cubic metres as the first.
    # `boardwalk.collapse_duplicate_declarations` was already collapsing
    # them on READ, which is the tell: the consumer was compensating for a
    # producer defect, one file downstream, where nothing else could see it.
    #
    # The survivors keep their own records; the redundant ones are recorded
    # as `superseded_duplicates` rather than deleted, because a declaration
    # that vanished without a number beside it is indistinguishable from one
    # that was never detected.
    detected_raw = [c for sg in segments for c in sg["crossings"]]
    detected, duplicate_census = boardwalk.collapse_duplicate_declarations(
        detected_raw)

    doc = {
        "schema_version": 2, "owner": "RoadNet", "world": WORLD, "seed": SEED,
        "connection_requirements": specmod.connection_requirements(),
        # A MEASUREMENT OF ONE SEED'S LAND, AND IT WAS EMITTED FOR EVERY SEED.
        # MEASURED 2026-09-22: a Fresh42 plan carried this sentence verbatim --
        # "spawn island + west isle joined by two bridges", 5.4559 km2, bbox
        # x[-1520,1904] z[-928,2720] -- into a document stamped `seed: Fresh42`,
        # in the same run whose own geography had just measured the two trunk
        # landmasses 4,926.7 m apart with NO bridge this project can build. The
        # three literals two keys below were deleted for exactly this reason and
        # this one was left. It is not derived here because the measurement
        # belongs to roads/archipelago.py and a second definition of "largest
        # land component" in this file would disagree with it; what is fixed is
        # that a seed nobody has run it for gets the ABSENCE, named, instead of
        # another planet's answer.
        "archipelago_note": (
            "The main continent is spawn island + west isle joined by "
            "two bridges. Largest land component in the world is "
            "5.4559 km2 at 16 m cells, bbox x[-1520,1904] z[-928,2720], "
            "which is exactly the union of the two. MEASURED, "
            "tools/jumpstart/roads/archipelago.py."
            if SEED == worldseed.DEFAULT_SEED else
            f"NOT MEASURED ON SEED {SEED!r}. The archipelago note is a reading "
            f"of ONE seed's land and there is no frozen copy to fall back on; "
            f"produce it with tools/jumpstart/roads/archipelago.py for this "
            f"seed. What this run DID measure about the same question is in "
            f"roads/geography.{SEED}.yaml::no_crossing and in this document's "
            f"crossings.yaml companion."),
        "height_source": {
            "tool": "tools/jumpstart/blueprints/run_patchscan.sh",
            "field": args.field, "resolution_m": 1.0, "rivers_included": True,
            # MEASURED FROM THE FILE, not typed in.  These three were literals
            # (46112400 / 230562258 / 147627509) describing Ulfsland's field;
            # on any other seed they would have been three confident lies in a
            # document that reads as evidence. Verified equal to the literals
            # for the default field before the change.
            "samples": sum(f.n * f.n for f in flds.values()),
            "bytes": Path(args.field).stat().st_size,
            "seed_hash": field_header["seed_hash"],
            "field_seed": field_header["seed"],
            "patch_windows": sorted(flds),
            "why": "PatchScan initialises WorldGenerator with full pregeneration, so "
                   "WorldGenerator.AddRivers has the dictionary Pregenerate() fills. "
                   "The coarse *.biome grid on this host was made with "
                   "SEEDSCAN_PREGEN=0 and is RIVER-FREE; no number here comes from it.",
        },
        "grade_policy": {
            **routemod.GRADE, "ceiling": routemod.GRADE_CEILING,
            "status": "DESIGN, not measured",
            "measured_player_slide_deg": fieldmod.PLAYER_SLIDE_DEG,
            "measured_slide_source": "Character::GetSlideAngle returns literal 38 for "
                                     "IsPlayer (45 mounted, 90 monster)",
            "rationale": "38 deg is a 78 % grade -- the angle above which a player "
                         "SLIDES, not a walkable target. Vagon is a plain physics body "
                         "(m_baseMass, m_itemWeightMassFactor, m_breakForce; no slope "
                         "logic at all), so cart behaviour on a grade is momentum, not "
                         "a rule, and the limit has to come from engineering rather "
                         "than from the game. Historic cart roads held to <= 8 %.",
        },
        "clamp": {"measured_m": routemod.CLAMP_M, "margin_m": routemod.CLAMP_MARGIN_M,
                  "source": "TerrainComp::ApplyToHeightmap clamps the applied height to "
                            "the GENERATED height +/- 8 m",
                  "checked": "per sample, not per segment"},
        "crossing_contract_with_Crossings": {
            "bank_snap_m": 2.0, "stand_off_m": 0.0,
            "bank_level_run_m": BANK_LEVEL_M,
            "deck_datum_y": specmod.DECK_DATUM_Y,
            "centreline_straight_across_window": True,
            "ribbon_width_m": WIDTH_M,
        },
        "poi_keepout": {
            # TWO POPULATIONS, NEVER ONE NUMBER.  "instances: N" over a mixed
            # list hides which half a route was planned against, and that is
            # how "184 objects left standing" came to answer a question about
            # structures.
            #
            # AND THE PATH IS THE RESOLVED ONE, NOT A MODULE CONSTANT.  This
            # line read `str(poimod.DUMP)` -- a frozen content hash -- while
            # `poimod.load()` five hundred lines up resolved the dump by SEED,
            # so `roads/segments.Fresh42.yaml:71` records
            # `generator_dump: /tmp/settle/loc3/f6fe167f4fcd.json` (Pirate68's
            # file) as the provenance of a plan plotted against Fresh42's
            # 12,376 instances.  A provenance field that names a file the run
            # did not read is worse than none: it is the evidence a reader
            # would use to rule out exactly this fault.
            "generator_dump": str(poimod.location_dump(SEED)),
            "generator_dump_depends_on": poimod.LOCATION_DUMP_DEPENDS_ON,
            "generator_instances": len(pois),
            "generator_status": "[SEED] fact: the worldgen location list, "
                                "MOD-FREE by construction",
            "census_bodies": len(census_pois),
            "census_file": args.census,
            "census_instrument": (None if census is None
                                  else census.instrument),
            "census_measured_utc": (None if census is None
                                    else census.measured_utc),
            "census_band_half_m": (None if census is None
                                   else census.band_half_m),
            "census_cells_read": (0 if census is None else len(census.cells)),
            "census_freshness": fresh,
            "census_band_gate": (None if census is None
                                 else censusmod.band_gate(census, 3.0)),
            "order": "ZONES GENERATED, THEN CENSUSED, THEN ROUTED, THEN "
                     "PAVED. ribbon.py's own STEP 0 generated the zones AFTER "
                     "the route was planned -- creating the mod structures the "
                     "route was about to cross -- and asked only whether a "
                     "_ZoneCtrl had appeared. MEASURED consequence: 42 of the "
                     "48 bodies that block a walker were ALREADY STANDING "
                     "when their road was written.",
            "mod_keepout_file_status":
                "NOT read directly any more. roads/mod_keepout.json is a "
                "survey of ONE world's finished carriageways; "
                "`precensus.py replay` converts it into a census artefact so "
                "its coverage and its date travel with it and can be checked "
                "(`coverage_gate`, `freshness_gate`) instead of trusted.",
            "declared_radius": "MEASURED per instance (exteriorRadius/interiorRadius)",
            "overshoot_m": poimod.OVERSHOOT_M,
            "overshoot_status": "INFERRED from ONE observation (pieces reaching 43 m "
                                "against a 32 m declared radius in the old world)",
            "road_margin_m": poimod.ROAD_MARGIN_M, "road_margin_status": "taste pick",
            "piece_pad_m": poimod.PIECE_PAD_M, "piece_pad_status": "taste pick",
            "protected_test": "prioritized or centerFirst or quantity <= 5 "
                              "(Settlements; there is no sacredness flag on "
                              "ZoneLocation and `unique` is FALSE even on bosslocation)",
            "residual_risk":
                "the census is COMPLETE ONLY INSIDE `census_cells_read`. "
                "Outside them this plan's keep-out set is the mod-free dump "
                "alone, which is why an uncensused station is a REJECTED "
                "segment rather than a caveat.",
            "per_corridor": poi_masked,
        },
        "totals": {
            "segments": len(segments),
            "length_m": round(total_len, 1),
            "paved_m2": round(paved_m2, 1),
            "zones_touched": len(all_zones),
            "zdo_cost": len(all_zones),
            "zdo_cost_basis": "ONE _TerrainCompiler ZDO per zone. "
                              "Heightmap::GetAndCreateTerrainCompiler returns the first "
                              "compiler in a zone, so a second is dead weight. Cost is "
                              "per ZONE, not per metre: the 956-ZDO-per-957-m figure was "
                              "the PIECE-based road this tool replaces.",
            "worst_grade": worst_grade,
            "stations_over_clamp": over,
            "stations_over_design_margin": over_m,
            "detected_crossings": len(detected),
            "declared_crossings": len(specmod.DECLARED_CROSSINGS),
            "unroutable_edges": len(unroutable),
            "rejected_segments": len(rejected),
            # THE LINE BESIDE ITSELF, at plan time: how many lines the ladder
            # re-profiled to join their own branches, and how many it could
            # not and refused -- see `route.SELF_ADJACENCY_DEPENDS_ON`.
            "self_adjacency_joined": sum(
                1 for sg in segments
                if sg["self_adjacency"]["verdict"] == "JOINED"),
            "self_adjacency_rejected": sum(
                1 for r in rejected if "self_adjacency_binding" in r),
            # LEGS THAT MEET, at plan time: how many the joint solve moved to
            # agree, and how many no joint profile could satisfy -- see
            # `route.JUNCTION_DATUM_DEPENDS_ON`.
            "junction_datum_joined": sum(
                1 for v in jd["legs"].values() if v["verdict"] == "JOINED"),
            "junction_datum_rejected": len(jd["refused"]),
            # THE WRITER'S WATER, at plan time: how many legs had to move or
            # re-profile so the writer need not refuse their batter into
            # water, and how many it still would -- route.WALKED_WET_DEPENDS_ON.
            "writer_water_remedied": sum(
                1 for sg in segments
                if (sg.get("writer_water") or {}).get("remedy", "as routed")
                != "as routed"),
            "writer_water_refused": (
                sum(1 for r in rejected if "writer_water_steps" in r)
                + (len(water_refused) if census is None else 0)),
        },
        "unroutable": unroutable,
        "rejected": rejected,
        "no_road": specmod.NO_ROAD,
        "segments": segments,
        "node_provenance": node_provenance(segments),
        # THE SET THIS PLAN WILL WRITE, beside the carriageway set it drives
        # on.  37 of 39 segments of this network declare the second where a
        # consumer needs the first; see ZONES_SCOPE.
        "zones_scope": ZONES_SCOPE,
        "zones_write": write_zones(segments),
        # THE GROUND LEGS SHARE, AND THE ONE HEIGHT THEY AGREE ON THERE.
        "junction_datum": junction_datum,
    }
    # ONE CELL PER NODE (NODE_ANCHOR_DEPENDS_ON), and WHAT THE ROOT REACHES
    # (ORPHAN_JOIN_DEPENDS_ON) -- re-asked of the legs this plan EMITS, since
    # the joint solve and the water pass above may have refused one.
    doc["node_anchors"] = {"depends_on": list(NODE_ANCHOR_DEPENDS_ON),
                           "mode": _NODE_ANCHOR_MODE,
                           "anchors": {k: list(v) for k, v
                                       in sorted(_NODE_ANCHORS.items())}}
    # WHERE A LEG TO A PAD STARTS WHEN THE NEAREST CELL CANNOT MEET THE
    # PAD'S DATUM (ARRIVAL_REACH_DEPENDS_ON).
    doc["arrival_reach"] = {"depends_on": list(ARRIVAL_REACH_DEPENDS_ON),
                            "mode": _ARRIVAL_REACH_MODE,
                            "starts": {k: plain(v) for k, v
                                       in sorted(_ARRIVAL_STARTS.items())}}
    moved = {k: v for k, v in _ARRIVAL_STARTS.items() if "moved_from" in v}
    print(f"arrival reach at PLAN time: {len(_ARRIVAL_STARTS)} start(s) whose "
          f"nearest cell cannot meet its pad's datum, {len(moved)} moved to "
          f"the nearest ring cell that can"
          + "".join(f"; {k} {v['moved_from']['start']} "
                    f"(box {v['moved_from'].get('box_at_start')} vs reach "
                    f"{v['moved_from'].get('reach_at_start')}) -> {v['start']} "
                    f"on {v['pad']} at {v['datum_y']}"
                    if "moved_from" in v else
                    f"; {k} {v['start']} STAYS: no ring cell of "
                    f"{v.get('no_ring_cell_meets')} meets {v.get('pad')} "
                    f"at {v.get('datum_y')}"
                    for k, v in sorted(_ARRIVAL_STARTS.items()))
          + f"; mode {_ARRIVAL_REACH_MODE}; key = "
            f"network.ARRIVAL_REACH_DEPENDS_ON")
    if orphan_join is not None:
        _rooted, _left = orphan_components(segments)
        orphan_join["pieces_cut_off_emitted"] = [list(c) for c in _left]
        doc["orphan_join"] = orphan_join
        print(f"orphan join, as EMITTED: {len(_left)} piece(s) cut off from "
              f"{ORPHAN_ROOT}"
              + (f": {_left}" if _left else ""))
        # Re-read after the joint solve: a pre-solve JOINED is not a claim
        # about a repair the joint/water gates subsequently removed.
        for row in orphan_join["joins"]:
            if row["verdict"] == "JOINED" and not (
                    set(row["piece"]) <= set(_rooted or [])):
                row["verdict"] = "ORPHANED_AFTER_JOINT"
        approaches = bridge_approach_requirements(segments, specmod.NODES)
        missing = [a for a in approaches if a["verdict"] != "SERVED"]
        doc["bridge_approaches"] = {
            "depends_on": list(BRIDGE_APPROACH_DEPENDS_ON),
            "requirements": approaches,
            "verdict": "REFUSED" if missing else "SERVED",
            "missing": len(missing)}
        for sg in segments:
            gaps = bridge_approach_gaps(sg)
            if gaps:
                sg["bridge_approach_gaps"] = gaps
        for req in missing:
            print(f"MISSING BRIDGE APPROACH: {req['crossing_id']} "
                  f"{req['bank']} {req['node']} at {req['bank_xz']}; "
                  f"disconnected endpoints {req['disconnected']}")
        for req in missing:
            rejected.append({
                "id": f"approach-{req['crossing_id']}-{req['bank']}",
                "owner": "RoadNet", "requirement": "bridge_approach",
                "why": f"No written road reaches declared {req['crossing_id']} "
                       f"{req['bank']} ({req['node']}); "
                       f"disconnected endpoints: {req['disconnected']}",
                "bridge_approach": req,
                "depends_on": list(BRIDGE_APPROACH_DEPENDS_ON)})
        doc["totals"]["rejected_segments"] = len(rejected)
        for row in orphan_join["bridge_approach_repairs"]:
            if row["verdict"] == "JOINED" and not any(
                    a["node"] == row["node"]
                    and set(a["served_by"]) & set(_rooted or [])
                    for a in approaches):
                row["verdict"] = "MISSING_AFTER_JOINT"
    # WHERE THE CENSUS READS (precensus.band_lines) and WHAT IT HAS NOT READ
    # YET (CENSUS_BAND_DEPENDS_ON) -- one per end of the pipeline.
    if census is None:
        doc["census_lines"] = census_lines
    else:
        doc["census_wanted"] = wanted
    # THE SERIALISATION BOUNDARY, and the ONLY place the on-disk name is
    # re-instated.  `emit()` below re-parses these bytes and runs
    # `require_write_zones_declared` on them; that refusal derives the write
    # set from `nodes`/`is_bridge`/`width_m` and never looks at the column, so
    # it is unaffected by which name the column is carrying here.
    carriageway_to_disk(doc)
    seg_text = yaml.safe_dump(plain(doc), default_flow_style=False,
                              sort_keys=False, width=200)

    xdoc = {
        "schema_version": 2, "owner": "RoadNet", "consumer": "Crossings",
        "world": WORLD, "seed": SEED,
        # THE CITATION THIS DOCUMENT NEVER CARRIED.  Every bank coordinate in
        # `detected` below is read off the SAME 1 m field `segments.yaml`
        # cites, and this document said so only in prose: `measurement` is a
        # sentence, and a sentence is not a field any reader can resolve.
        # MEASURED 2026-09-23 on Fresh42: `emitscope.require_emission_scope`
        # refused the write with "names no artefact in any citation field ...
        # It claims nothing, so nothing about it can be checked -- and absent
        # is not permission", and the refusal was CORRECT.  The companion
        # document had always carried `height_source`; this one had not, so the
        # pair were checkable to different depths while claiming to describe
        # one plan.  Same key, same block, one field.
        "height_source": dict(doc["height_source"]),
        "measurement": "generated heights from PatchScan at 1 m WITH rivers; bank "
                       "coordinates snapped to 2 m per Crossings' MEASURED deck pitch",
        "precedence": "DECLARED crossings carry Crossings' own measurements and are "
                      "authoritative. DETECTED crossings are mine, from routing over "
                      "the 1 m field. Where they describe the same channel, Crossings' "
                      "numbers win and mine are marked superseded.",
        "contract": {"ribbon_stops_at_bank": True, "stand_off_m": 0.0,
                     "bank_level_run_m": BANK_LEVEL_M,
                     "deck_datum_y": specmod.DECK_DATUM_Y,
                     "centreline_straight_across_window": True},
        "declared": specmod.DECLARED_CROSSINGS,
        # NEVER AN EMPTY `declared` BY OMISSION.  A crossing this seed cannot
        # build is a MEASUREMENT, not a deletion: spec.py moves it here with the
        # reason geography.absent_nodes() recorded, so the Crossings consumer
        # reads "S1 does not exist on this seed, 4,926.7 m against a 96 m
        # ceiling" rather than a shorter list it cannot tell from a shorter
        # world.
        "unbuildable": specmod.UNBUILDABLE_CROSSINGS,
        "detected": detected,
        # BOTH NUMBERS, ALWAYS.  How many channels the router met and how
        # many records describe them, so a consumer can tell "this seed has
        # ten channels" from "somebody shortened the list".  A redundant
        # record is NAMED against the record that supersedes it with the
        # measured corridor fraction beside it, never silently dropped.
        "duplicate_declarations": {
            "instrument": duplicate_census["instrument"],
            "records_before_collapse": duplicate_census["declarations"],
            "distinct_channels": duplicate_census["distinct_channels"],
            "threshold": duplicate_census["threshold"],
            "span_m_before_collapse": duplicate_census["declared_m"],
            "span_m_distinct": duplicate_census["distinct_m"],
            "superseded_duplicates": duplicate_census["collapsed"],
            "pairs_over_threshold":
                duplicate_census["pairs_over_threshold"],
            "why": "route.find_crossings runs PER SEGMENT, so a channel two "
                   "segments both have to get across is detected twice "
                   "under two names. Concatenating the per-segment lists "
                   "shipped both, which charged every consumer's "
                   "denominator twice and credited one built deck to every "
                   "declaration sharing its corridor.",
        },
        "abutment_handoffs": [a | {"segment_id": sg["id"], "cls": sg["cls"]}
                              for sg in segments for a in sg["abutments"]
                              if a["node"] in _served_nodes(sg)],
        "bridge_approaches": doc.get("bridge_approaches"),
    }
    x_text = yaml.safe_dump(plain(xdoc), default_flow_style=False,
                            sort_keys=False, width=200)
    written = emit(out_dir, {"segments": seg_text, "crossings": x_text},
                   dry_run=args.dry_run)

    print(f"\nTOTAL {len(segments)} segments  {total_len:.0f} m  {paved_m2:.0f} m2 paved  "
          f"{len(all_zones)} zones = {len(all_zones)} _TerrainCompiler ZDOs")
    print(f"worst grade {worst_grade*100:.2f}%  over 8 m clamp {over}  over 7 m margin {over_m}  "
          f"detected crossings {len(detected)} of {len(detected_raw)} records "
          f"({len(duplicate_census['collapsed'])} redundant declaration(s) of a "
          f"channel already detected)  declared {len(specmod.DECLARED_CROSSINGS)}")
    viol = [(sg["id"], r) for sg in segments for r in sg["poi_hard_violations"]]
    print(f"POI hard-radius violations: {len(viol)}")
    for sid, r in viol[:10]:
        print(f"   {sid} {r['name']} at {r['xz']} dist {r['centreline_dist_m']} m "
              f"hard {r['hard_m']} m protected={r['protected']}")
    # THE WRITE GATE OVER THE WHOLE PLAN, AND THE MARGINALS NAMED.  A leg
    # with a FIRM violation never reaches `segments` -- it is in `rejected`
    # with its own number -- so a non-zero count here is the thing the 1 m
    # sample pitch leaves for the lattice to decide, and it is printed rather
    # than swallowed because "no firm violations" is not the same claim as
    # "clear of every generated location".
    near_gate = sum(sg["write_gate"]["marginal_violations"] for sg in segments)
    gate_rej = [r for r in rejected if "write_gate_short_by_m" in r]
    print(f"write gate at PLAN time: {len(segments)} segment(s) clear, "
          f"{len(gate_rej)} REJECTED for a firm breach, {near_gate} marginal "
          f"violation(s) inside the {poimod.LATTICE_PITCH_M} m sample pitch "
          f"left to the lattice; population = the generator dump for "
          f"{SEED!r} ({poimod.location_dump(SEED)})")
    for r in gate_rej:
        b = r["write_gate_binding"]
        print(f"   REJECTED {r['id']} {b['name']} at {b['xz']} centreline "
              f"{b['centreline_dist_m']} m, needs {b['required_m']} m, "
              f"short by {b['short_by_m']} m")
    sa_rej = [r for r in rejected if "self_adjacency_binding" in r]
    print(f"self-adjacency at PLAN time: "
          f"{sum(1 for sg in segments if sg['self_adjacency']['verdict'] == 'CLEAR')}"
          f" segment(s) clear as routed, "
          f"{sum(1 for sg in segments if sg['self_adjacency']['verdict'] == 'JOINED')}"
          f" re-profiled to join their own branches, {len(sa_rej)} REJECTED "
          f"with no joinable profile; key = route.SELF_ADJACENCY_DEPENDS_ON "
          f"{routemod.SELF_ADJACENCY_DEPENDS_ON}")
    for r in sa_rej:
        b = r["self_adjacency_binding"]
        print(f"   REJECTED {r['id']} samples {b['cells']} stations "
              f"{b['stations']} design {b['design_y']}, "
              f"{b['unabsorbable_m']} m unabsorbable")
    for line in written:
        print(line)
    if plan_state is not None:
        # The north continuation shares the existing joint/profile and writer
        # gates with this network; exposing measured state avoids a second
        # independent profile solve where their actual footprints meet.
        plan_state.update(joints=joints, pads=jd_pads,
                          pads_missing=jd_pads_missing, fields=flds,
                          water_cache=water_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
