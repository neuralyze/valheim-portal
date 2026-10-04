#!/usr/bin/env python3
"""Re-solve every jumpstart base placement for a world seed, in one command.

This is the point of the whole placement design.  Blueprint coordinates are
**seed-specific**: a flat coastal meadow in one seed is open ocean in the next.
So `placements.yaml` never hard-codes a site as its source of truth -- it
records a seed-independent *requirement* (biome, footprint, flatness, coastal or
not, which ZoneSystem anchor to sit near, what feature the spot must serve) and
this driver turns those requirements into concrete coordinates for whatever seed
the world is currently rolled onto.

How a site is chosen
--------------------
1. **Coarse search** over the whole `.biome` grid.  Every declared constraint is
   a hard full-grid mask -- biome, purity, flatness, freeboard, anchor distance,
   coastality, location clearance, and the footprints of placements already
   solved in the same preset.  Flatness is NOT among them: it is costed, not
   gated (see site_finder.HARD_CONSTRAINTS).
2. **Every instance of the anchor is tried.**  Valheim places five Bonemass
   altars, four GoblinKings, three Dragonqueens.  Measuring against whichever
   one happens to be nearest the world origin is how this tool used to report a
   site as 3629 m from "the" Bonemass when it was 484 m from another one.
3. **Spatial non-maximum suppression** turns a blob of adjacent qualifying cells
   into one site, so the shortlist is genuinely different options.
4. **1 m refinement** (`run_patchscan.sh`): each shortlisted candidate gets a
   real 1 m height/biome patch from the game's generator, with rivers, and the
   footprint is slid to the flattest whole-metre offset inside it.  The verdict
   is always this number, never the coarse one.
5. **Honest verdict.**  The chosen site is re-checked against the *declared*
   requirement and the result is written into the file: `satisfies_requirement`,
   plus a `violations:` list (constraint, required, actual, over_by) and a
   `relaxed:` record whenever a constraint had to be loosened to find anything
   at all.  A `solved` block that disagrees with its own `requirement` now says
   so in the file.

Typical run (the wrapper `resolve.sh` does all three steps for you):

    ./solve_placements.py --world tools/jumpstart/worlds/Ulfsland \\
        --seed Pirate68 \\
        --grid  /tmp/bp_p68_8/00000.biome \\
        --locations /tmp/seedscan/loc/f6fe167f4fcd.json \\
        --vh-src /media/big4/projects/game/valheim/Ulfsland/data/bepinex \\
        --patch /tmp/jumpstart_patch.bin --write

`--write` updates each file's `solved`/`shortlist` block in place and leaves
every `requirement` untouched.  `--label alternative` writes into
`solved_alternatives[<seed>]` instead, for recording a seed that is *not* live.
`--verify` re-checks what is already in the files against the grid and the
requirements and changes nothing.

Grids and location dumps come from the sibling `tools/seedscan` harness:

    printf 'Pirate68\\n' > /tmp/seed.txt
    VH_SRC=<install>/data/bepinex SEEDS=/tmp/seed.txt OUT=/tmp/g \\
      SANDBOX=/tmp/my_sandbox STEP=8 HEIGHT=1 tools/seedscan/run_scan.sh
    VH_SRC=... SEEDS=/tmp/seed.txt OUT=/tmp/loc tools/seedscan/run_locscan.sh
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import site_finder as SF  # noqa: E402

# The writer's own pad rule (`pad_extent`, `zones_for`), through the one module
# that bills pads, so there is no second copy of either here.
FL = SF.FL

# Chest capacity per placement. KitDesign's chest-manifest.yaml says how many
# inventory slots a preset's materials overflow into; this says how much the
# placed base can actually hold, so the two can be diffed instead of guessed.
# Counted from the blueprint's own object list, not estimated.
CONTAINER_RE = re.compile(
    r"^(piece_chest|piece_chest_wood|piece_chest_blackmetal|piece_chest_private"
    r"|piece_chest_barrel|dvergrprops_crate|piece_cartographytable|CargoCrate"
    r"|piece_MightyDrawer|RossDrawer)",
    re.I,
)

# Coarse-pass flatness widening. The coarse grid under-reports spread, so gate
# generously and let the 1 m pass decide. 2.0 was chosen against the measured
# distribution (p90 under-report at a 70 m footprint is 8.0 m on a 3.5 m budget).
COARSE_FLAT_SLACK = 2.0
# Candidates carried into the 1 m pass, per placement. MEASURED: 128 patches of
# ~7 k samples each cost 2.5 s of generator time inside one server boot, so the
# pool is cheap and recall is the only thing that matters here -- the coarse
# ranking is a proxy for the 1 m answer, and a proxy needs breadth.
#
# 64 is enough, and that is MEASURED rather than assumed: tripling this to 192
# (947 patches instead of 316) changed not one of the 13 chosen sites. So when a
# bill looks high the cause is the terrain, not the pool -- which is exactly the
# question `flatten_floor` exists to answer.
REFINE_POOL = 64
# Candidates per anchor instance out of the coarse pass, before merging. Equal
# to the pool so a single-instance anchor (StartTemple is unique) can still fill
# it; multi-instance anchors are thinned again by the merge.
COARSE_TOP = REFINE_POOL
# Two different separations. The refinement pool wants COVERAGE of the qualifying
# ground, so it packs candidates half a footprint apart; the shortlist an
# operator reads wants genuinely DIFFERENT options, so it is thinned again to a
# footprint and a half after the 1 m verdicts are in.
POOL_SEPARATION = 0.5
SHORTLIST_SEPARATION = 1.5

# When a requirement cannot be met as declared, the search widens ONCE into this
# window and then MINIMISES the actual miss against the declared requirement.
#
# This replaced a single-axis ladder that tried each knob at 1.25x, 1.5x, 2x, 3x,
# 5x in turn and stopped at the first hit. That shape is wrong whenever two
# constraints must both give, which is the normal case once the water datum is
# correct: no single-axis variant exists, so the ladder escalated until one axis
# was absurd. MEASURED on Pirate68 it answered `pre-queen` with a 26.7 m cliff
# rather than a 4 m slope 1.2 m under water, because a 5x flatness relaxation
# was the only single knob that produced anything. "Smallest multiplier" is not
# the goal; smallest departure from what was asked for is.
#
# `min_biome_purity` is the exception and barely moves: relaxing it is not
# loosening a requirement, it is abandoning the biome the preset is named after.
# MEASURED: unbounded, it returned a mountainside for a Swamp workshop.
COMPROMISE = {
    # metres of freeboard, downward through zero. A Swamp pad is under water
    # somewhere no matter where it goes -- MEASURED on Pirate68, the largest
    # swamp square entirely above y=30 is 16 m against a 70 m footprint -- so a
    # recorded negative freeboard is the honest answer, not "unsolvable".
    "min_height_m": lambda v: v - 10.0,
    "within_m": lambda v: v * 3.0,
    "coastal_within_m": lambda v: v * 3.0,
    "min_biome_purity": lambda v: v * 0.75,
}

# ---------------------------------------------------------------------------
# SEPARATION_SCOPE_DEPENDS_ON: WHICH PLACEMENTS STAND IN ONE SAVE.  Every
# solved placement under `worlds/<World>/*/placements.yaml`, because that is
# what the build lays into the one save: `buildstage._placement_units` makes a
# step-1 / step-2 / step-6 unit of EVERY preset's solved placement,
# `settlements/sites.py` keeps its towns off all of them ("installations kept
# off: 13 solved placement(s)"), and `terraform/sites.py` refuses two presets'
# copies of one body unless `duplicate_of` says which one is built -- a rule
# that exists only because the presets share a save.  `duplicate_of` is the
# one declared exception: the deferring placement is NOT a second building
# (terraform/sites.py skips it), so it neither claims ground nor is judged
# against its principal.
#
# THE SHIPPED RULE KEYED SEPARATION ON THE PRESET FILE -- "presets are
# alternative world states that never coexist" -- a key naming a SUBSET of
# what the build stands together.  MEASURED on Fresh42, stage 7, 2026-09-24
# 21:51, three DIFFERENT bodies solved onto one meadow ~467 m from the
# StartTemple:
#     deepnorth-sandbox#sandbox-portal-hub  (-220.5, -411.5) 34.1 x 34.1  y 38.54
#     pre-elder#early-dock                  (-209.5, -413.5) 42.8 x 35.8  y 37.14
#     pre-eikthyr#meadows-starter-hall      (-211.5, -417.5) 17.4 x 17.5  y 34.23
# overlapping 27.5 x 32.9 m, 16.8 x 17.5 m and 17.4 x 17.5 m (the hall's
# WHOLE pad), at datums up to 4.31 m apart -- three step-2 pads and three
# step-6 bodies of one build.  Nothing downstream refused it: the writer's pad
# guard declares PAD-VERSUS-PAD out of scope (settlement pads overlap by
# design), so the later pad wins per sample under the earlier body.  And
# pass 3's `pool = free or pool` accepted an overlap SILENTLY, even inside one
# preset, whenever no candidate was free.
#
# The neuters, both directions, are in `selftest`: `_SEPARATION_SCOPE =
# "preset"` restores the shipped key (the Fresh42 triple must come back
# ACCEPTED), and `_OVERLAP_DUPLICATES = "ignored"` stops honouring
# `duplicate_of` (Ulfsland's harbour/dock, one building, must come back
# REFUSED).  `_CROWDED = "silent"` is the shipped `free or pool`.
_SEPARATION_SCOPE = "world"
_OVERLAP_DUPLICATES = "honoured"
_CROWDED = "refused"


def separation_bucket(path) -> str:
    """The key placements exclude each other under: the WORLD, not the file."""
    return "world" if _SEPARATION_SCOPE == "world" else str(path)


def claims_ground(p: dict) -> bool:
    """A `duplicate_of` placement is its principal's building, not a second
    one: it takes the principal's coordinate in pass 4 and is never built, so
    its own solve must not push another building off real ground."""
    return _OVERLAP_DUPLICATES != "honoured" or not p.get("duplicate_of")


def _need(tr: float, fp: float) -> float:
    return max(tr, fp * 1.5)


def separation_free(pool: list[dict], taken: list[tuple[float, float, float]],
                    fp: float) -> list[dict]:
    """The candidates clear of every site already taken in this save.

    `taken` rows are `(x, z, r)`; a candidate is clear of one when its centre
    is further than `max(r, 1.5 * fp)`."""
    return [d for d in pool
            if all(math.hypot(d["x"] - tx, d["z"] - tz) > _need(tr, fp)
                   for tx, tz, tr in taken)]


def separation_breach(d: dict, taken: list[tuple[float, float, float]],
                      fp: float) -> dict | None:
    """The separation the CHOSEN site breaks, in metres, or None.

    When no candidate is clear the whole pool is still ranked -- a least-bad
    site is evidence -- but the breach is RECORDED as a violation, where the
    shipped `free or pool` dropped it."""
    if _CROWDED == "silent" or not taken:
        return None
    short, near = max(((_need(tr, fp) - math.hypot(d["x"] - tx, d["z"] - tz),
                        (tx, tz, tr)) for tx, tz, tr in taken),
                      key=lambda t: t[0])
    if short < 0.0:
        return None
    tx, tz, tr = near
    return {"constraint": "separation_m",
            "required": round(_need(tr, fp), 2),
            "actual": round(math.hypot(d["x"] - tx, d["z"] - tz), 2),
            "over_by": round(short, 2),
            "from_xz": [round(tx, 1), round(tz, 1)],
            "note": ("no candidate in the pool is clear of every site already "
                     "taken in this save (SEPARATION_SCOPE_DEPENDS_ON); the "
                     "chosen one is recorded and REFUSED")}


def placement_overlaps(rows: list[dict]) -> list[dict]:
    """Every pair of BUILT placements whose written pads overlap.

    `rows` carry `key`, `preset`, `x`, `z`, `w`, `d` (the pad `flatten.py`
    stamps, world axes), `y` and `duplicate_of`.  A deferring duplicate is not
    built, so it is not a participant.  Returned per pair with the overlap in
    metres and the datum step between the two pads."""
    live = [r for r in rows
            if _OVERLAP_DUPLICATES != "honoured" or not r.get("duplicate_of")]
    out = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if _SEPARATION_SCOPE != "world" and a["preset"] != b["preset"]:
                continue
            ox = (min(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2)
                  - max(a["x"] - a["w"] / 2, b["x"] - b["w"] / 2))
            oz = (min(a["z"] + a["d"] / 2, b["z"] + b["d"] / 2)
                  - max(a["z"] - a["d"] / 2, b["z"] - b["d"] / 2))
            if ox > 0.0 and oz > 0.0:
                dy = (None if a.get("y") is None or b.get("y") is None
                      else round(abs(float(a["y"]) - float(b["y"])), 2))
                out.append({"a": a["key"], "b": b["key"],
                            "overlap_m": [round(ox, 1), round(oz, 1)],
                            "datum_step_m": dy})
    return out


def overlap_rows(docs: dict) -> list[dict]:
    """`placement_overlaps` rows for every SOLVED placement of `docs`
    ({path: parsed placements.yaml}), each pad as `flatten.pad_extent` reads
    it back from the solved block."""
    rows = []
    for path, doc in docs.items():
        preset = doc.get("preset", Path(path).parent.name)
        for p in doc.get("placements") or []:
            sol = p.get("solved") or {}
            if "x" not in sol:
                continue
            w, d = FL.pad_extent(p)
            rows.append({"key": f"{preset}#{p['id']}", "preset": preset,
                         "x": float(sol["x"]), "z": float(sol["z"]),
                         "w": float(w), "d": float(d), "y": sol.get("y"),
                         "duplicate_of": p.get("duplicate_of")})
    return rows


def report_overlaps(docs: dict) -> int:
    """Print every overlapping pair of built pads; return how many."""
    bad = placement_overlaps(overlap_rows(docs))
    for o in bad:
        print(f"  !! OVERLAP {o['a']} x {o['b']}: pads share "
              f"{o['overlap_m'][0]} x {o['overlap_m'][1]} m of ground, datum "
              f"step {o['datum_step_m']} m -- both are built into this one "
              f"save (SEPARATION_SCOPE_DEPENDS_ON)", file=sys.stderr)
    return len(bad)


# ---------------------------------------------------------------------------
# NETWORK_LAND_DEPENDS_ON: A WATERFRONT THE ROADS SERVE HAS ITS LANDWARD APRON
# ON THE ROADS' LAND.  A dock is a shoreline you WALK to and sail from, so the
# rule has two ends: the landward end on land the network reaches, the water
# end in water.  `coastal` + the freeboard band only ever asked for the second.
#
#   router_labelling  land is what `roads/geography.island_of_point` labels it:
#                     `crossings/survey.label_land` on the seed's 1 m field
#                     (stage 4), h > 30.5, 4-connected -- NOT the 32 m
#                     `site_finder._land_graph`, which merges any two blocks
#                     holding a sample of land and so walks across channels.
#   network_root      the component is the one the ROOT stands on -- the
#                     placement whose `portal.role` is `hub`, i.e.
#                     `roads/network.ORPHAN_ROOT` (`portalhub`) -- on the
#                     window where that is the LARGEST component, the only
#                     land `island_of_point` lets a road node stand on.
#   landward_apron    the pad holds land samples OF THAT COMPONENT.  Not "the
#                     node snaps to it": MEASURED on Pirate68, Ulfsland's dock
#                     (675.4, -85.6), whose road the fixture world BUILT, holds
#                     168 samples of the network's component 7 and 318 of a
#                     sandbar the 4-connected labelling cuts off at a hairline
#                     (component 278, the nearest land to the node at 7.6 m
#                     against the network's 9.8 m).  Component equality at the
#                     snap would refuse the one dock known to work.
#   network_mask      the coarse search is masked to footprints whose window
#                     holds a sample of that component, BEFORE the pool is
#                     truncated -- a pass-3 filter alone found 0 of 64 refined
#                     Fresh42 candidates on it, because the ranking had filled
#                     the pool with open-water shelves.  Pass 1 runs before the
#                     root is solved, so its mask is the SPAWN's component: the
#                     window `roads/geography` calls trunk-primary is the one
#                     whose largest component holds the spawn, and
#                     `roads/spec.NODES["portalhub"]` stands on island "spawn".
#                     Pass 3 judges on the root as solved.
#
# SERVED means the anchor the candidate serves stands on that component: a
# dock `near: StartTemple` is a road node, a Deep North landing `near:
# bosslocation` is reached by longship by design and is not judged here.
#
# MEASURED on Fresh42, drive 5 (stage 7, 2026-09-27 00:32): pre-elder#early-
# dock solved to (-7.5, -416.5), pad 52.75 x 36.75 m, and ALL 2,014 of its 1 m
# samples are under the 30.0 plane (highest 29.34): no landward apron at all.
# The nearest land is a 2,383 m2 islet (component 153) 56.0 m off; the
# network's land, component 128 (2,065,437 m2, holding the portal hub at
# (-220.5, -411.5)), is 72.4 m off.  The solve recorded `overland_from_spawn:
# false` and scored it `reach 0.0` -- a 0.15 weight, outvoted -- while
# `route_from_spawn` said reachable over the 32 m graph.  The road plan then
# recorded T5-temple-dockshore "stopped 46.44 m short: water", 67.46 m from
# the pad, and T9 routed via 168 m of open water.  The neuter is
# `_WATERFRONT_REACH = "unchecked"`, the shipped rule, in `selftest`.
NETWORK_LAND_DEPENDS_ON = ("router_labelling", "network_root",
                           "landward_apron", "network_mask")
_WATERFRONT_REACH = "network_land"
# `route.Corridor.nearest_allowed`'s default `search_m`: how far the router
# looks for land from a node, so how far the recorded snap looks.
ROUTER_SNAP_M = 200.0


def _crossings_survey():
    """`crossings/survey.py` loaded by path under the name
    `roads/geography.crossings_survey` uses, so both read ONE module."""
    import importlib.util

    mod = sys.modules.get("crossings_survey")
    if mod is None:
        path = HERE.parent / "crossings" / "survey.py"
        spec = importlib.util.spec_from_file_location("crossings_survey", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["crossings_survey"] = mod
        spec.loader.exec_module(mod)
    return mod


def network_land(fields: dict | None, root_xz,
                 root: str = "the network root (the portal.role hub "
                             "placement)") -> dict:
    """The land component `root` stands on, in the router's labelling:
    `{"window", "fld", "lab", "component", "samples"}`, or `{"unread": why}`
    -- UNREAD IS NOT CLEAR."""
    if root_xz is None:
        return {"unread": f"{root} is not solved in this run"}
    if not fields:
        return {"unread": "no 1 m field (--field, stage 4's <seed>_1m.bin)"}
    cs = _crossings_survey()
    x, z = root_xz
    hits = []
    for pid, fld in fields.items():
        if not fld.contains(x, z, 1.0):
            continue
        lab, sizes = cs.label_land(fld)
        i, j = fld.index(x, z)
        cid = int(lab[i, j])
        if cid and cid == int(np.argmax(sizes[1:])) + 1:
            hits.append((int(sizes[cid]), pid, fld, lab, cid))
    if not hits:
        return {"unread": f"{root} at ({x}, {z}) is not on the largest land "
                          f"component of any window of the field"}
    samples, pid, fld, lab, cid = max(hits, key=lambda h: h[0])
    return {"window": pid, "fld": fld, "lab": lab, "component": cid,
            "samples": samples}


def _nearest_label(net: dict, x: float, z: float, want=None):
    """`(label, metres)` of the nearest land sample to (x, z) within
    ROUTER_SNAP_M -- of component `want` when given -- or `(None, None)`."""
    fld, lab = net["fld"], net["lab"]
    ci, cj = fld.index(x, z)
    r = int(ROUTER_SNAP_M / fld.step)
    i0, j0 = max(ci - r, 0), max(cj - r, 0)
    win = lab[i0:ci + r + 1, j0:cj + r + 1]
    ii, jj = np.nonzero(win == want if want is not None else win)
    if not ii.size:
        return None, None
    d2 = (ii + i0 - ci) ** 2 + (jj + j0 - cj) ** 2
    k = int(np.argmin(d2))
    return int(win[ii[k], jj[k]]), round(math.sqrt(float(d2[k])) * fld.step, 1)


def serves(net: dict, anchor_xz) -> bool:
    """Does the network serve a site anchored at `anchor_xz`: does the anchor
    stand on the network's land?  An unread network serves everything, so
    that an unread reading is REFUSED rather than waved through."""
    if "unread" in net:
        return True
    fld = net["fld"]
    if anchor_xz is None or not fld.contains(anchor_xz[0], anchor_xz[1], 1.0):
        return False
    return _nearest_label(net, anchor_xz[0], anchor_xz[1])[0] == net["component"]


def network_plane(g: dict, net: dict, req: dict) -> np.ndarray:
    """Coarse-grid HARD plane: does the footprint window centred on each cell
    hold a 1 m sample of the network's component?  A superset of the 1 m
    verdict (the window is the footprint rounded UP to odd cells), so it
    costs the coarse pass no recall; pass 3 judges at 1 m."""
    from scipy import ndimage

    step, n, extent = g["step"], g["n"], g["extent"]
    cells = net.get("_cells")
    if cells is None:
        fld = net["fld"]
        ii, jj = np.nonzero(net["lab"] == net["component"])
        c = np.floor((fld.x0 + jj * fld.step + extent) / step).astype(np.int64)
        r = np.floor((fld.z0 + ii * fld.step + extent) / step).astype(np.int64)
        keep = (r >= 0) & (r < n) & (c >= 0) & (c < n)
        cells = np.zeros((n, n), dtype=np.uint8)
        cells[r[keep], c[keep]] = 1
        net["_cells"] = cells
    k = SF.window_cells(SF.requirement_defaults(req), step)
    planes = net.setdefault("_planes", {})
    if k not in planes:
        planes[k] = ndimage.maximum_filter(cells, size=k, mode="constant",
                                           cval=0) > 0
    return planes[k]


def waterfront_gated(p: dict, req: dict) -> bool:
    """Is placement `p` judged under NETWORK_LAND_DEPENDS_ON: a waterfront
    (`coastal`) that is its own building (a `duplicate_of` is its principal's
    building and takes the principal's site)."""
    return (_WATERFRONT_REACH == "network_land" and bool(req.get("coastal"))
            and claims_ground(p))


def searched_pad(p: dict, d: dict) -> tuple[float, float]:
    """Candidate `d`'s searched rectangle, as `flatten.pad_extent` reads it;
    the written pad only ever grows it."""
    return FL.pad_extent(dict(p, solved={
        "x": round(d["x"], 1), "z": round(d["z"], 1),
        "footprint_orientation": d.get("footprint_orientation", "square")}))


def network_served(pool: list[dict], net: dict, p: dict,
                   pad_of=searched_pad) -> tuple[list[dict], dict[int, dict]]:
    """`(pool, verdicts by id)`: the candidates whose landward apron is on the
    network's land, or the whole pool when none is -- the chosen one is then
    recorded and REFUSED by `network_land_violation`."""
    reach_of: dict[int, dict] = {}
    for d in pool:
        v = waterfront_reach(net, d, pad_of(p, d))
        if v is not None:
            reach_of[id(d)] = v
    served = [d for d in pool if reach_of.get(id(d), {}).get(
        "verdict", "on_network") == "on_network"]
    return served or pool, reach_of


# ---------------------------------------------------------------------------
# WATERFRONT_YAW_DEPENDS_ON = (facing_measurement, per_candidate, written_yaw,
# duplicate_turn): A WATERFRONT'S YAW IS SOLVED, NOT INHERITED.  A yaw is the
# number the blueprint was captured at, and `site_finder.facing_check` has
# always MEASURED whether it opens onto water -- and only recorded the answer.
#   facing_measurement  the same 400 m rays over the four cardinal yaws, open
#                       water = coarse height <= 30.0; the inherited yaw stands
#                       while it is within `facing_check`'s own 0.1 of the best
#                       (its verdict "points at"), else the best is written,
#                       ties to the smallest turn; no yaw finding water is a
#                       `facing_water` violation, and ranks the site after any
#                       site that has one.
#   per_candidate       solved before the pad is sized, so `written_pad`
#                       (`flatten.placed_pad_at_yaw`), the fixture ring and the
#                       engine bill are all at the yaw that is written.
#   written_yaw         `rotation.yaw`, the one place every reader of the
#                       body's turn (flatten, stock, derive, clearing,
#                       waypoints) reads; `solved.yaw_solved` holds the evidence.
#   duplicate_turn      a `duplicate_of` placement takes its principal's yaw
#                       with its site (pass 4).
# MEASURED, Fresh42 scratch re-solve after NETWORK_LAND: early-dock at
# (-607.5, 178.5) inherited yaw 90, whose ray is 0.24 open water against 0.90
# at yaw 270 -- the jetty would have run 0.76 of its reach over land.  Drive 5
# (-7.5, -416.5) had it too: 0.32 at 90 against 1.0 at 180.  The neuter is
# `_WATERFRONT_YAW = "inherited"`, the shipped rule, in `selftest`.
_WATERFRONT_YAW = "solved"
# `site_finder.facing_check`'s verdict tolerance, restated because the verdict
# is a sentence: within this of the best cardinal, the yaw "points at" water.
FACING_TOLERANCE = 0.1


def waterfront_yaw_solved(req: dict) -> bool:
    return _WATERFRONT_YAW == "solved" and bool(req.get("coastal"))


def solve_yaw(g: dict, x: float, z: float, inherited: float) -> dict:
    """`{"yaw", ...}`: the yaw a waterfront at (x, z) is written at."""
    fc = SF.facing_check(g, x, z, inherited, water=True)
    opts = fc["by_cardinal_yaw"]
    best = max(opts.values())
    if best <= 0.0:
        return {"yaw": inherited, "refused": {
            "constraint": "facing_water", "required": "> 0.0",
            "actual": opts, "over_by": None,
            "why": (f"no cardinal yaw finds open water within "
                    f"{fc['reach_m']:.0f} m; the inherited yaw is kept")}}
    if fc["declared_yaw_biome_fraction"] >= best - FACING_TOLERANCE:
        return {"yaw": inherited}
    turn = lambda y: abs((y - inherited + 180.0) % 360.0 - 180.0)  # noqa: E731
    yaw = float(min((y for y, f in opts.items() if f == best), key=turn))
    return {"yaw": yaw, "inherited": inherited, "by_cardinal_yaw": opts,
            "why": (f"the inherited yaw's {fc['reach_m']:.0f} m ray is "
                    f"{fc['declared_yaw_biome_fraction']} open water against "
                    f"{best} at yaw {yaw:g} (WATERFRONT_YAW_DEPENDS_ON)")}


def candidate_yaw(g: dict, req: dict, x: float, z: float, inherited: float) -> dict:
    """The yaw candidate (x, z) is sized, billed and written at."""
    if not waterfront_yaw_solved(req):
        return {"yaw": inherited}
    return solve_yaw(g, round(x, 1), round(z, 1), inherited)


def at_yaw(p: dict, yaw: float) -> dict:
    """Placement `p` turned to `yaw`; `p` itself when it already stands there."""
    rot = p.get("rotation") or {}
    if float(rot.get("yaw", 0) or 0) == float(yaw):
        return p
    return dict(p, rotation=dict(rot, yaw=yaw_value(yaw)))


def yaw_value(yaw: float):
    """A yaw as `placements.yaml` writes one: an int when it is whole."""
    return int(yaw) if float(yaw).is_integer() else float(yaw)


def waterfront_reach(net: dict, d: dict, pad: tuple[float, float]) -> dict | None:
    """Candidate `d`'s verdict under NETWORK_LAND_DEPENDS_ON, on its searched
    `pad` (w, d) -- which the written pad only ever grows -- or None when the
    network does not serve it (its anchor stands on other land)."""
    if "unread" in net:
        return {"verdict": "unread", "why": net["unread"]}
    fld, lab, comp = net["fld"], net["lab"], net["component"]
    anchor = d.get("anchor_xz")
    if not serves(net, anchor):
        return None
    x, z, (w, dd) = round(d["x"], 1), round(d["z"], 1), pad
    out = {"network_component": comp, "window": net["window"]}
    if not (fld.contains(x - w / 2, z - dd / 2) and fld.contains(x + w / 2, z + dd / 2)):
        return dict(out, verdict="off_network",
                    why="the pad is outside the window the network's land "
                        "was labelled on, so no road reaches it")
    i0, j0 = fld.index(x - w / 2, z - dd / 2)
    i1, j1 = fld.index(x + w / 2, z + dd / 2)
    sub = lab[i0:i1 + 1, j0:j1 + 1]
    on = int((sub == comp).sum())
    land = int((sub != 0).sum())
    snap, snap_m = _nearest_label(net, x, z)
    _c, net_m = _nearest_label(net, x, z, comp)
    ok = on > 0
    return dict(out, verdict="on_network" if ok else "off_network",
                pad_samples=int(sub.size), apron_samples=land,
                on_network_samples=on, foreign_samples=land - on,
                wet_samples=int((fld.heights[i0:i1 + 1, j0:j1 + 1]
                                 < SF.WATER_LEVEL).sum()),
                node_snap_component=snap, node_snap_m=snap_m,
                network_land_m=net_m)


def network_land_violation(v: dict) -> dict:
    """The recorded refusal for a candidate whose verdict is not on_network."""
    if v["verdict"] == "unread":
        return {"constraint": "network_land", "required": "read",
                "actual": "unread", "over_by": None, "why": v["why"]}
    return {"constraint": "network_land",
            "required": (f"landward apron on land component "
                         f"{v['network_component']} of {v['window']} (the "
                         f"network root's)"),
            "actual": (f"{v.get('on_network_samples', 0)} of "
                       f"{v.get('pad_samples', 0)} pad samples on it "
                       f"({v.get('apron_samples', 0)} land, "
                       f"{v.get('wet_samples', 0)} under water); node snaps to "
                       f"component {v.get('node_snap_component')} at "
                       f"{v.get('node_snap_m')} m"),
            "over_by": v.get("network_land_m"),
            "why": v.get("why", "no candidate in the refined pool stands on "
                                "the network's land; the least-bad is "
                                "recorded and REFUSED")}


def container_census(source: Path) -> dict:
    """Count container objects in a blueprint, by prefab."""
    import to_rcon_plan as TP

    try:
        objs = TP.read_objects(source)
    except Exception as exc:  # a missing corpus file must not abort the solve
        return {"error": str(exc)}
    counts: dict[str, int] = {}
    for o in objs:
        if CONTAINER_RE.match(o.prefab):
            counts[o.prefab] = counts.get(o.prefab, 0) + 1
    total = sum(counts.values())
    return {
        "by_prefab": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "containers": total,
        # Valheim chests are 4x x slots; the deployed BiggerChests mod changes
        # this, so the figure is a floor, not a promise.
        "slots_floor": total * 20,
    }


def anchor_instances(anchors: dict, req: dict) -> list[dict]:
    """Every candidate anchor point for a requirement, richest form first."""
    near = req.get("near")
    if near:
        inst = anchors.get(near)
        if not inst:
            return []
        return [
            {
                "xz": (l["x"], l["z"]),
                "desc": f"{near} @ ({l['x']:.1f},{l['y']:.1f},{l['z']:.1f})",
            }
            for l in sorted(inst, key=lambda l: math.hypot(l["x"], l["z"]))
        ]
    if req.get("near_xz"):
        x, z = req["near_xz"][0], req["near_xz"][1]
        return [{"xz": (x, z), "desc": f"fixed ({x:.1f},{z:.1f})"}]
    return [{"xz": None, "desc": ""}]


def compromise_requirement(req: dict, min_height_floor: float | None = None) -> tuple[dict, dict]:
    """The widened requirement to search when the declared one yields nothing.

    Returns `(requirement, relaxed_record)`. Knobs are opened at their EFFECTIVE
    value, which for an undeclared one is the solver default -- `min_height_m` is
    never written in a requirement but still gates every search, so it still has
    to be openable and still has to be reported when it gives.
    """
    eff = SF.requirement_defaults(req)
    alt = dict(req)
    opened: dict[str, dict] = {}
    for knob, widen in COMPROMISE.items():
        if knob not in eff:
            continue
        if knob == "min_height_m":
            # Freeboard is only negotiable when the seed HAS no dry footprint of
            # this size under the rest of the requirement. Everything else on
            # this list is work -- a slope can be hoed, a walk can be walked --
            # but a pad under the water plane cannot be built on at all, and
            # trading metres of freeboard against metres of flatness as if they
            # were the same currency is how this solver put eight of thirteen
            # bases under water. `min_height_floor` is None when dry ground
            # exists, and the knob simply is not opened.
            if min_height_floor is None:
                continue
            alt[knob] = min_height_floor
            opened[knob] = {
                "declared": round(float(eff[knob]), 3),
                "applied": round(min_height_floor, 3),
                "why": "no dry footprint of this size exists in this seed",
            }
            continue
        if knob == "within_m" and not (req.get("near") or req.get("near_xz")):
            continue
        if knob == "coastal_within_m" and not req.get("coastal"):
            continue
        declared = float(eff[knob])
        applied = float(widen(declared))
        alt[knob] = applied
        opened[knob] = {"declared": round(declared, 3), "applied": round(applied, 3)}
    return alt, {
        "opened": opened,
        "why": (
            "no site in this seed satisfies the requirement as declared; the "
            "search was widened into these bounds and the site recorded is the "
            "one that misses the DECLARED requirement by the fewest metres. "
            "`violations` states the remaining miss exactly."
        ),
    }


# NETWORK_LAND_DEPENDS_ON `network_mask`, when even the compromise holds no
# shore on the network's land: reachability outranks the beach's SHAPE, so the
# grade band opens by this factor (and nothing else does) before the served
# waterfront is given up.  MEASURED on Fresh42 (drive-5 inputs): under the
# declared band the ONLY network-land shelf within 2,700 m of the temple is the
# portal hub's own meadow, (-204, -420), which the hub takes; with the grade
# open, 133 coarse footprints clear location clearance and every taken site,
# the flattest a 7.68 m spread at (-308, 364), 470 m from the temple, against
# the declared 4.28 m (10 % of 42.8 m).  x2 admits it; the band's freeboard
# (min/max_height_m) stays as declared, so it is still a SHORE.
NETWORK_GRADE_WIDEN = 2.0


def network_compromise(alt: dict, record: dict) -> tuple[dict, dict] | None:
    """`compromise_requirement`'s result with the grade band also opened, or
    None when the requirement declares no grade band to open."""
    eff = SF.requirement_defaults(alt)
    declared = float(eff["max_grade_pct"])
    if not math.isfinite(declared):
        return None
    applied = declared * NETWORK_GRADE_WIDEN
    opened = dict(record["opened"], max_grade_pct={
        "declared": round(declared, 3), "applied": round(applied, 3),
        "why": ("NETWORK_LAND_DEPENDS_ON: no shore on the network's land "
                "inside the declared band or the compromise")})
    return dict(alt, max_grade_pct=applied), dict(record, opened=opened)


def coarse_pool(
    g, anchors, avoid_xz, req: dict, exclude, spawn_xz, declared: dict | None = None,
    mask_for=None,
) -> list[dict]:
    """Coarse candidates across every anchor instance, merged and suppressed.

    `mask_for(variant, anchor_xz)` returns a caller's HARD plane for one
    anchor instance, or None (NETWORK_LAND_DEPENDS_ON `network_mask`)."""
    merged: list[dict] = []
    sep = max(SF.footprint_extent(SF.requirement_defaults(req))) * POOL_SEPARATION
    # A rectangular footprint has two grid-aligned orientations and they are not
    # interchangeable: MEASURED, sandbox-harbour's 42 x 29 m pad finds 2 gentle
    # beach shelves one way round and 0 the other, because a shoreline is a
    # line. Try both and let the ranking choose; the winner records which
    # orientation it assumed so the operator can set the yaw to match.
    orientations = [False, True] if req.get("footprint_xz_m") else [False]
    for swap in orientations:
        variant = dict(req, _swap=swap) if swap else req
        for inst in anchor_instances(anchors, variant):
            for d in SF.search(
                g, variant, inst["xz"], avoid_xz, COARSE_TOP, exclude,
                separation_m=sep, spawn_xz=spawn_xz, declared_req=declared,
                mask=mask_for(variant, inst["xz"]) if mask_for else None,
            ):
                d["anchor"] = inst["desc"]
                d["anchor_xz"] = inst["xz"]
                d["_swap"] = swap
                fx, fz = SF.footprint_extent(SF.requirement_defaults(variant))
                # The rectangle this candidate was SEARCHED for: the declared
                # floor `written_pad` grows at the body's yaw.
                d["footprint_orientation"] = pad_text(fx, fz)
                merged.append(d)
    if not merged:
        return []
    # Three orderings, interleaved, because the pool's job is RECALL for the 1 m
    # pass and the three things worth being sure about disagree:
    #   * blended score      -- the right way to CHOOSE a site,
    #   * coarse flatness    -- the only coarse signal correlated with the 1 m
    #                           flatness verdict, which the score happily trades
    #                           away for a shorter walk,
    #   * declared miss      -- when the search has been widened, the candidate
    #                           that breaks the ORIGINAL requirement least; it
    #                           can score badly on the widened one and still be
    #                           the honest answer.
    orders = [sorted(merged, key=lambda d: -d["score"]), sorted(merged, key=lambda d: d["flat"])]
    if declared is not None:
        orders.append(sorted(merged, key=lambda d: declared_miss(d, declared)))
    order: list[dict] = []
    seen: set[int] = set()
    for tup in zip(*orders):
        for d in tup:
            if id(d) not in seen:
                seen.add(id(d))
                order.append(d)
    out: list[dict] = []
    for d in order:
        if any((d["x"] - o["x"]) ** 2 + (d["z"] - o["z"]) ** 2 < sep * sep for o in out):
            continue
        out.append(d)
        if len(out) >= REFINE_POOL:
            break
    return out


def declared_miss(d: dict, req: dict) -> float:
    """Total metres by which a candidate breaks the declared requirement.

    Delegates to site_finder so the coarse search, the pool ordering and the
    final choice all minimise the SAME quantity. Summing `violations[].over_by`
    instead looks equivalent and is not: purity is a fraction, and adding it to
    a pile of metres silently makes a whole wrong biome cost 0.5 m.
    """
    return SF.miss(d, req)


def solve_coarse(
    g, anchors, avoid_xz, req: dict, exclude, refine: bool, spawn_xz,
    mask_for=None,
) -> tuple[list[dict], dict | None, str | None]:
    """Best coarse pool for a requirement: as declared, else the compromise.

    Returns (pool, relaxed_record, error).
    """
    if req.get("near") and not anchors.get(req["near"]):
        return [], None, f"seed has no location named {req['near']!r}"
    # `declared=req` even on the unrelaxed path: the coarse flatness gate is
    # `declared=req` on the unrelaxed path too: candidates in this pool can
    # still break the declared requirement, and ranking them by blended score
    # instead of by miss is how pre-moder ended up on a 6.17 m slope when a
    # 4.36 m one was available.
    pool = coarse_pool(g, anchors, avoid_xz, req, exclude, spawn_xz, declared=req,
                       mask_for=mask_for)
    if pool:
        return pool, None, None

    # Does a dry footprint exist at all? Measured, not assumed: it decides
    # whether freeboard is negotiable.
    ceiling = None
    dry = 0
    for inst in anchor_instances(anchors, req):
        got = SF.freeboard_ceiling(g, req, inst["xz"], avoid_xz)
        if got is None:
            continue
        fb, n = got
        dry += n
        ceiling = fb if ceiling is None or fb > ceiling else ceiling
    floor = None if dry > 0 or ceiling is None else ceiling - 0.5
    alt, record = compromise_requirement(req, floor)
    pool = coarse_pool(g, anchors, avoid_xz, alt, exclude, spawn_xz, declared=req,
                       mask_for=mask_for)
    if pool:
        # the 1 m pass has to gate on the requirement that was actually searched,
        # or its mask is empty and it cannot slide at all
        for d in pool:
            d["_req"] = alt
        return pool, record, None
    if mask_for is not None:
        wide = network_compromise(alt, record)
        if wide is not None:
            pool = coarse_pool(g, anchors, avoid_xz, wide[0], exclude, spawn_xz,
                               declared=req, mask_for=mask_for)
            if pool:
                for d in pool:
                    d["_req"] = wide[0]
                return pool, wide[1], None
        # No shore on the network's land at all: solve unmasked, and pass 3
        # records the chosen site REFUSED with its numbers -- the waterfront is
        # refused by this rule, never lost to it.
        return solve_coarse(g, anchors, avoid_xz, req, exclude, refine, spawn_xz)
    return [], None, (
        "no site found even inside the compromise bounds; the requirement is "
        "unreachable in this seed by a wide margin"
    )


RIVER_PROBES = 256
RIVER_TOLERANCE_M = 1.0
RIVER_MAX_BAD = 0.02


def probe_seed(world_hash: int) -> int:
    """The river probe's RNG seed, from a Valheim world hash of EITHER sign.

    `World..ctor` stores `seed.GetStableHashCode()`, which is a SIGNED int32,
    and the grid header carries it verbatim. `np.random.default_rng` rejects a
    negative seed outright, so the river check -- and with it every solve that
    passes `--vh-src` -- died on any seed whose hash happens to be negative.

    MEASURED, and it is a coin flip on the seed string: 50,098 of 100,000
    random alphanumeric seeds (length 4..12) hash negative. `Pirate68` hashes
    +147,627,509, which is the only reason this never fired; `Fresh42` hashes
    -1,894,280,788 and raised ValueError at solve_placements.py:373.

    Reinterpreting the same 32 bits as unsigned is the fix, not `abs()`: for
    every non-negative hash the mask is the identity, so a re-solve of an
    already-solved seed draws the IDENTICAL probe points.
    """
    return world_hash & 0xFFFFFFFF


def assert_grid_has_rivers(g, vh_src: str, seed: str, work: Path, sandbox: str | None) -> str | None:
    """Refuse a river-free grid, by measurement rather than by trust.

    `tools/seedscan/run_scan.sh` defaults `SEEDSCAN_PREGEN=0`, and
    `WorldGenerator.AddRivers` reads the dictionary `Pregenerate()` fills, so
    without it the height plane has no rivers or lakes AT ALL. Nothing in the
    `VHBIOME4` header records which it was, and the difference is not cosmetic:
    MEASURED on Pirate68, the river-free grid reports 33.97 m at (-275, 260)
    where the true height is 28.9 m, i.e. it calls a river bed dry land by 5 m.
    The solver approved sites on exactly that basis.

    So probe it. Sample points in the 25..60 m band, where rivers live, against
    1 m patches from `run_patchscan.sh` (which always pregenerates) and compare.
    MEASURED separation: a river-free grid fails 40 of 256 probes, a
    pregenerated one fails none.

    Returns None when the grid is sound, else a message explaining the refusal.
    """
    h = g["height"]
    band = np.argwhere((h > 25.0) & (h < 60.0))
    if len(band) < RIVER_PROBES:
        return None
    rng = np.random.default_rng(probe_seed(g["hash"]))
    pick = band[rng.choice(len(band), RIVER_PROBES, replace=False)]
    xs = SF.axis(g["n"], g["step"], g["extent"])
    req_path = work / "river_probe.tsv"
    req_path.write_text(
        "\n".join(
            f"p{i}\t{xs[c]:.1f}\t{xs[r]:.1f}\t1.0\t1.0" for i, (r, c) in enumerate(pick)
        )
        + "\n",
        "utf-8",
    )
    out_path = work / "river_probe.bin"
    run_patchscan(vh_src, seed, req_path, out_path, sandbox)
    probes = SF.load_patches(out_path)
    bad = 0
    for i, (r, c) in enumerate(pick):
        pa = probes.get(f"p{i}")
        if pa is None:
            continue
        if float(h[r, c]) - float(pa["height"].mean()) > RIVER_TOLERANCE_M:
            bad += 1
    frac = bad / float(RIVER_PROBES)
    print(
        f"river probe: {bad}/{RIVER_PROBES} points where the grid is more than "
        f"{RIVER_TOLERANCE_M} m above the 1 m river-inclusive truth",
        file=sys.stderr,
    )
    if frac > RIVER_MAX_BAD:
        return (
            f"this grid has no rivers: {bad}/{RIVER_PROBES} probe points sit more "
            f"than {RIVER_TOLERANCE_M} m above the 1 m river-inclusive height. "
            f"Re-scan with PREGEN=1:\n"
            f"  VH_SRC=... SEEDS=<seedfile> OUT=<dir> STEP=8 HEIGHT=1 PREGEN=1 "
            f"tools/seedscan/run_scan.sh\n"
            f"Solving against a river-free grid approves river beds as building "
            f"sites, which is exactly the bug this check exists to stop."
        )
    return None


def build_patch_request(jobs: list[dict], path: Path) -> int:
    """The 1 m windows to scan, sized by the module that consumes them.

    TWO NUMBERS DESCRIBE ONE FOOTPRINT AND THIS FILE READ THE WRONG ONE. The
    window was cut from `footprint_extent` -- the body's occupied RECTANGLE --
    while `site_finder.refine` slides `footprint_m`, the PAD SQUARE, inside
    whatever window it is handed (site_finder.py:1259) and raises `patch
    smaller than the footprint` when the square does not fit.

    MEASURED on Fresh42's coarse pool: 2 of 13 placements crashed the solve
    outright -- `pre-queen/mistlands-blackforge-base` slid an 80 m square in a
    50-sample window, `deepnorth-sandbox/complete-station-hub` a 56 m square in
    56 -- and 10 of 13 had LESS slide room than `PATCH_PAD` = 16 m declares.
    Seed-independent: a requirement carries no seed. Both crashing placements
    hold a `solved` block at `flat_resolution_m: 1.0`, so both refined once and
    the requirement widened afterwards with nothing re-running to notice.

    So the rule is not restated here. `site_finder.patch_request` is the SAME
    module's own window rule (`footprint_m * 0.5 + PATCH_PAD`), it was correct,
    and it had zero callers while this file kept a second copy that was wrong.
    Deleting the copy is the fix; a widened copy would only have agreed today.
    """
    rows = [
        f"{k}\t{x:.1f}\t{z:.1f}\t{half:.1f}\t{step:.1f}"
        for job in jobs
        for k, x, z, half, step in SF.patch_request(job["pool"], job["req"], job["key"])
    ]
    path.write_text("\n".join(rows) + "\n", "utf-8")
    return len(rows)


def patches_match(jobs: list[dict], patches: dict) -> bool:
    """Does this patch file actually cover the candidates we are about to refine?

    `--reuse-patch` is a convenience, and a convenience that silently pairs a
    candidate with somebody else's 1 m window is worse than no convenience at
    all. Patch ids are positional, so a pool that changed shape between runs
    reuses the right ids over the wrong coordinates.
    """
    for job in jobs:
        for i, d in enumerate(job["pool"]):
            pa = patches.get(f"{job['key']}#{i}")
            if pa is None:
                return False
            if math.hypot(pa["cx"] - d["x"], pa["cz"] - d["z"]) > 1.0:
                return False
    return True


def run_patchscan(vh_src: str, seed: str, req_path: Path, out_path: Path, sandbox: str | None) -> None:
    env = dict(os.environ)
    env.update({"VH_SRC": vh_src, "SEED": seed, "REQ": str(req_path), "OUT": str(out_path)})
    if sandbox:
        env["SANDBOX"] = sandbox
    subprocess.run([str(HERE / "run_patchscan.sh")], env=env, check=True)


def pad_text(w: float, d: float) -> str:
    """A pad as `flatten.pad_extent` reads it back from `footprint_orientation`."""
    return f"{w:.1f} m along x by {d:.1f} m along z"


def written_pad(p: dict, d: dict,
                depends_on: tuple = FL.PAD_EXTENT_DEPENDS_ON,
                ring: dict | None = None, shrink_m: float = 0.0) -> dict:
    """THE pad `terraform/flatten.py` will stamp for candidate `d` of
    placement `p`, centred on the 0.1 m centre `site_block` records: the
    rectangle the search assumed (`d["footprint_orientation"]`, a floor),
    grown wherever the body AT ITS YAW needs more -- `flatten.placed_pad_at_yaw`,
    i.e. `flatten.pad_extent_at_yaw` at the preset path's zero margin.  NOT
    the `footprint_m` square `site_finder.refine` slid (MEASURED on Fresh42:
    mistlands-blackforge-base recorded cut 13.65 m over an 80 m square and is
    written as a 17.5 x 10.9 m rectangle at cut 3.515 m), and NOT the searched
    rectangle alone: MEASURED 2026-09-25, the live Fresh42 build's step 2
    refused 8 of 13 installations on it -- mountain-outpost was searched on
    the swapped 30.8 x 20.3 m rectangle, stands at yaw 180 across 21.7 x
    33.5 m, and left 308 of 748 vertices under it unstamped (6.588 m).
    `ring` is the site's fixture ring (`stock.fixture_ring`, see
    `flatten.PAD_EXTENT_DEPENDS_ON` `fixture_ring`); None is no ring, never
    the one an earlier solve recorded.
    `shrink_m` takes that many metres off the extent of the searched
    rectangle before it is a floor, and a `depends_on` without `declared_pad`
    has no floor at all (LEVELLABLE_PAD_DEPENDS_ON); the result then names the
    requirement's own `declared` pad, the `floor` it stood on (None: none) and
    `grew` only where the pad is LARGER than the declared one.
    RAISES where the body cannot be measured; see PAD_EXTENT_DEPENDS_ON."""
    x, z = round(d["x"], 1), round(d["z"], 1)
    declared = FL.pad_extent(dict(p, solved={
        "x": x, "z": z,
        "footprint_orientation": d.get("footprint_orientation", "square")}))
    floor = (declared if not shrink_m else
             (max(declared[0] - shrink_m, 0.0), max(declared[1] - shrink_m, 0.0)))
    out = FL.placed_pad_at_yaw(p, floor, (x, z), 0.0, depends_on,
                               ring=ring or {})
    if shrink_m or "declared_pad" not in depends_on:
        out = {**out, "declared": [declared[0], declared[1]],
               "grew": out["w"] > declared[0] or out["d"] > declared[1],
               "floor": ([round(floor[0], 3), round(floor[1], 3)]
                         if "declared_pad" in depends_on else None)}
    return {**out, "x": x, "z": z}


def fixture_ring_of(world_name: str, job: dict, d: dict) -> dict:
    """`stock.fixture_ring` for placement `job["p"]`, solved at candidate
    `d` (the ring is offsets from the centre, so one candidate serves every
    one).  `{"unread": why}` where it cannot be solved: the pad is then the
    body's and step 6 refuses, by number, every fixture it does not reach --
    UNREAD IS NOT a ring, and it is not a reason to lose the site."""
    p = job["p"]
    if FL.declared_duplicate(p):
        return {"unread": f"declares duplicate_of {FL.declared_duplicate(p)}: "
                          f"its canonical's run levels and stocks the site"}
    terraform = str(HERE.parent / "terraform")
    if terraform not in sys.path:
        sys.path.insert(0, terraform)
    try:
        import stock  # noqa: PLC0415  terraform/stock.py
        place = dict(p, solved={"x": round(d["x"], 1), "z": round(d["z"], 1),
                                "y": 0.0, "flatten_cost": {"target_y": 0.0}})
        return stock.fixture_ring(world_name, job["preset"], place)
    except (SystemExit, Exception) as e:                   # noqa: BLE001
        return {"unread": f"{type(e).__name__}: {e}"}


_CLEARING_AREA = None
_CLEARANCE = None
# CLEARING_ROOM_DEPENDS_ON: the populations an installation's step-1
# clearing cylinder must not reach -- `settlements/clearance.py::
# Locations.cylinder_room`, the ONE rule the settlement siting asks
# (`settlements/plan.py::CLEARING_GATE_DEPENDS_ON`).  `interior_zone` is the
# whole 64 m zone of a location that declares an interior: MEASURED on
# Fresh42 drive 5, crypt interiors at y 5061 stood inside two settlement
# cylinders the marker stand-off had passed, and `objects_remove` is
# height-blind.  Neuter ("pieces",): the marker's own reach only.
CLEARING_ROOM_DEPENDS_ON = ("pieces", "interior_zone")
# The location dump the room is asked of; `main` binds `--locations`.
CLEARING_LOCATIONS: str | None = None


def _clearing_area_rule():
    global _CLEARING_AREA
    if _CLEARING_AREA is None:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location(
            "clearing_area_rule", HERE.parent / "clearing" / "area.py")
        _CLEARING_AREA = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _CLEARING_AREA     # its dataclass asks for it
        spec.loader.exec_module(_CLEARING_AREA)
    return _CLEARING_AREA


def clearing_locations():
    """`clearance.Locations` over CLEARING_LOCATIONS, read once per path;
    None when no dump is bound."""
    global _CLEARANCE
    if CLEARING_LOCATIONS is None:
        return None
    if _CLEARANCE is None or _CLEARANCE[0] != CLEARING_LOCATIONS:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location(
            "settlements_clearance_rule",
            HERE.parent / "settlements" / "clearance.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod                # its dataclass asks for it
        spec.loader.exec_module(mod)
        _CLEARANCE = (CLEARING_LOCATIONS, mod.load(CLEARING_LOCATIONS))
    return _CLEARANCE[1]


def clearing_room_refusal(p: dict, d: dict, pe: dict, bill: dict | None = None,
                          loc=None) -> dict | None:
    """The step-1 clearing cylinder over pad `pe` against the ONE clearing
    rule (CLEARING_ROOM_DEPENDS_ON), as a violation with its numbers and the
    location named, or None when it fits.  A pad `clearing_area` itself
    refuses is the neighbour rule's answer, not this one's."""
    loc = loc if loc is not None else clearing_locations()
    if loc is None or not CLEARING_ROOM_DEPENDS_ON:
        return None
    A = _clearing_area_rule()
    place = dict(p, solved={
        "x": pe["x"], "z": pe["z"], "y": float(d.get("y") or 0.0),
        "flatten_cost": bill or {},
        "footprint_orientation": pad_text(pe["w"], pe["d"]),
        "nearest_location_m": round(d["location_dist"], 0)})
    try:
        ar = A.clearing_area(place)
    except A.ClearAreaRefused:
        return None
    room = loc.cylinder_room(ar.centre_x, ar.centre_z, CLEARING_ROOM_DEPENDS_ON)
    if ar.radius_m <= room["max_radius_m"]:
        return None
    b = room["binding"]
    return {"constraint": "clearing_room_m",
            "required": round(ar.radius_m, 2),
            "actual": room["max_radius_m"],
            "over_by": round(ar.radius_m - room["max_radius_m"], 2),
            "location": b["name"], "population": b["population"],
            "why": (f"the {ar.radius_m:.2f} m step-1 clearing cylinder can "
                    f"reach {b['why']}; the widest safe radius here is "
                    f"{room['max_radius_m']} m")}


def ring_forbidden(p: dict, d: dict, pe: dict, bill: dict) -> dict | None:
    """Why candidate `d`'s ring pad `pe` may not be written, or None: the
    engine clamp (`bill`, the engine's own), or a NEIGHBOUR -- the step-1
    clearing cylinder over that pad reaching the nearest generated location,
    asked of `clearing/area.py::clearing_area`, the rule step 1 refuses by,
    or reaching a location's pieces or interior (`clearing_room_refusal`).
    Either would lose the site; the ring is dropped instead."""
    if bill["verdict"] != "clear":
        return {"forbidden_by": "the engine clamp", "verdict": bill["verdict"],
                "over_clamp_samples": bill.get("over_clamp"),
                "max_cut_m": bill.get("max_cut_m"),
                "max_fill_m": bill.get("max_fill_m")}
    A = _clearing_area_rule()
    place = dict(p, solved={
        "x": pe["x"], "z": pe["z"], "y": float(d.get("y") or 0.0),
        "flatten_cost": bill,
        "footprint_orientation": pad_text(pe["w"], pe["d"]),
        "nearest_location_m": round(d["location_dist"], 0)})
    try:
        A.clearing_area(place)
    except A.ClearAreaRefused as e:
        return {"forbidden_by": "a neighbour", "clearing": str(e)}
    room = clearing_room_refusal(p, d, pe, bill)
    if room is not None:
        return {"forbidden_by": "a neighbour", "clearing": room["why"],
                "clearing_room": room}
    return None


def written_bill(surface, pad: dict) -> dict:
    """The engine's bill for `pad` (`written_pad`), as it will be written."""
    return SF.engine_bill(surface, [(pad["x"], pad["z"])], pad["w"], pad["d"])


# ---------------------------------------------------------------------------
# LEVELLABLE_PAD_DEPENDS_ON: A SITE IS WRITTEN WITH A PAD THE ENGINE CAN LEVEL,
# OR REFUSED WITH THE NUMBERS.  NEVER WITH A PAD IT CANNOT.
#
#   declared_pad     the rectangle the requirement declares (`footprint_m`: the
#                    siting window and, written, the YARD an installation's
#                    body stands in), kept as the FLOOR of `flatten.PAD_EXTENT_
#                    DEPENDS_ON` `declared_pad`.  Every candidate is billed on
#                    it first, and where one levels, nothing below changes.
#   yard_steps       where NO candidate's declared pad is within the engine's
#                    +/-8 m apply clamp (`tcdata.CLAMP_M`), the floor gives way
#                    PAD_SHRINK_STEP_M at a time and the WHOLE pool is billed
#                    again at each step, so the pad written is the largest one
#                    ANY candidate can level.
#   body_and_ring    the last rung: no floor at all, the pad the body at its
#                    yaw and its fixture ring need (`flatten.PAD_EXTENT_
#                    DEPENDS_ON` less `declared_pad`).
#
# THE FLOOR WAS NEVER LET GO, AND THE CLAMP FALLBACK ASSUMED IT WAS.  The one
# thing the solver did with a pad beyond the clamp was drop the FIXTURE RING:
# "the body's pad is written and step 6 refuses, by number, each fixture it
# does not reach".  The body's pad carries the same declared floor, so on a
# site whose floor is the whole problem that pad IS the ring's pad, and the
# fallback re-billed the very rectangle it had just refused.  MEASURED on
# Fresh42 (Main's drive 11, re-solved offline): pre-bonemass#iron-era-workshop
# declares footprint_m 70 over a 22.3 x 23.9 m body.  All 13 candidates the
# search carries into the 1 m pass are beyond the clamp on the 70 m pad -- the
# one chosen, (-844.5, 4995.5), 442 of 5041 samples, cut 10.54 m / fill 9.569
# m, and its `fixture_ring.refused.pad` was [70.0, 70.0], the body's pad --
# while the same centre levels a 50 m pad (cut 6.06 / fill 7.34) and every
# smaller one.  The solve recorded the refusal in its block, step 1 refused the
# unit, and the whole installation and everything standing on it was lost for
# the 20 m of YARD round a body that stands on 24 x 25.  The other twelve
# installations had a candidate that levels its declared pad and are untouched.
#
# Where a pad is beyond the clamp on EVERY rung (every candidate, down to the
# body and its ring), the site is the candidate LEAST over the clamp
# (`rank_pool`), recorded REFUSED in its own block with every rung's numbers --
# never one chosen for a score and written over a pad nobody can level.
LEVELLABLE_PAD_DEPENDS_ON = ("declared_pad", "yard_steps", "body_and_ring")
PAD_SHRINK_STEP_M = 5.0
# `rank_pool`'s mode.  "excess": a pool with no levellable pad ranks by how far
# each candidate is past the clamp; "verdict": levellable first, then violations
# and score (the shipped key); "blind": the engine's verdict is not asked at
# all (the key before the engine bill).
_CLAMP_RANK = "excess"


def pad_rungs(declared_m: float) -> list[tuple[str, float | None]]:
    """The pad rules a pool is billed on, in the order tried: `(name,
    shrink)`, `shrink` the metres taken off the declared floor's extent (0.0
    is the declared pad) and None no floor at all (LEVELLABLE_PAD_DEPENDS_ON)."""
    out: list[tuple[str, float | None]] = []
    if "declared_pad" in LEVELLABLE_PAD_DEPENDS_ON:
        out.append(("declared_pad", 0.0))
    if "yard_steps" in LEVELLABLE_PAD_DEPENDS_ON:
        k = 1
        while PAD_SHRINK_STEP_M * k < declared_m:
            out.append((f"declared_pad-{PAD_SHRINK_STEP_M * k:g}m",
                        PAD_SHRINK_STEP_M * k))
            k += 1
    if "body_and_ring" in LEVELLABLE_PAD_DEPENDS_ON:
        out.append(("body_and_ring", None))
    return out


def clamp_excess(bill: dict) -> tuple[float, int]:
    """How far a bill is past the engine clamp: `(worst delta m, samples
    over)`; `(0.0, 0)` for a pad that levels, `(inf, 0)` for ground nobody
    read."""
    if bill["verdict"] == "clear":
        return (0.0, 0)
    if bill["verdict"] != "refused":
        return (math.inf, 0)
    return (round(max(bill["max_fill_m"], bill["max_cut_m"]), 3),
            int(bill["over_clamp"]))


def size_pool(pool: list[dict], p: dict, ring_at, shrink: float | None
              ) -> list[tuple[dict, dict, dict | None]]:
    """`[(pad, body pad, fixture ring)]` for every candidate on one rung of
    `pad_rungs`: the pad with the site's fixture ring, the pad without it
    (the ring's fallback), and the ring.  Pure: nothing on a candidate moves.
    RAISES where the body cannot be measured."""
    depends_on = (FL.PAD_EXTENT_DEPENDS_ON if shrink is not None else
                  tuple(t for t in FL.PAD_EXTENT_DEPENDS_ON
                        if t != "declared_pad"))
    sized = []
    for d in pool:
        r = ring_at(d)
        gr = r if "x" in r else {}
        q = at_yaw(p, d["_yaw"]["yaw"])
        pe = written_pad(q, d, depends_on, ring=gr, shrink_m=shrink or 0.0)
        bp = (written_pad(q, d, depends_on, shrink_m=shrink or 0.0) if gr
              else pe)
        sized.append((pe, bp, dict(r) if r else None))
    return sized


def judge_pool(pool: list[dict], sized: list, p: dict, req: dict, ring_at,
               surface) -> None:
    """Bill and judge every candidate on the pads `size_pool` sized."""
    for d, (pe, bp, fr) in zip(pool, sized):
        d["pad_extent"], d["_body_pad"], d["fixture_ring"] = pe, bp, fr
    surface.ensure({z for d in pool for pe in (d["pad_extent"],
                                               d["_body_pad"])
                    for z in FL.zones_for(pe["x"], pe["z"],
                                          pe["w"], pe["d"])})
    for d in pool:
        d["_violations"] = SF.violations(d, req)
        if d["_yaw"].get("refused"):
            d["_violations"].append(d["_yaw"]["refused"])
        # THE ONE CLEARING RULE over the body's own pad (the least
        # this site writes): a site whose step-1 cylinder reaches a
        # location's pieces or interior ranks with the violators and
        # is recorded refused with the location named.
        d["_clearing"] = clearing_room_refusal(
            at_yaw(p, d["_yaw"]["yaw"]), d, d["_body_pad"])
        if d["_clearing"] is not None:
            d["_violations"].append(d["_clearing"])
        d["flatten_cost"] = dict(written_bill(surface, d["pad_extent"]),
                                 resolution_m=1.0, estimated=False)
        r = ring_at(d)
        if "x" not in r:
            continue
        rc = d["flatten_cost"]
        forbid = ring_forbidden(at_yaw(p, d["_yaw"]["yaw"]), d,
                                d["pad_extent"], rc)
        if forbid is None:
            continue
        body = dict(written_bill(surface, d["_body_pad"]),
                    resolution_m=1.0, estimated=False)
        d["fixture_ring"] = dict(r, refused={
            "pad": [d["pad_extent"]["w"], d["pad_extent"]["d"]],
            **forbid,
            "why": (f"{forbid['forbidden_by']}: the body's pad is "
                    f"written and step 6 refuses, by number, each "
                    f"fixture it does not reach")})
        d["pad_extent"], d["flatten_cost"] = d["_body_pad"], body


def rung_row(name: str, shrink: float | None, pool: list[dict]) -> dict:
    """One rung of the ladder as the block records it: the pad of the
    candidate nearest the clamp's right side, how many levelled, and for a
    rung none levelled the numbers of the least bad."""
    least = min(pool, key=lambda d: clamp_excess(d["flatten_cost"]))
    eng, pe = least["flatten_cost"], least["pad_extent"]
    row = {"rung": name, "pad": [pe["w"], pe["d"]],
           "candidates": len(pool),
           "levellable": sum(d["flatten_cost"]["verdict"] == "clear"
                             for d in pool)}
    if eng["verdict"] == "refused":
        row["least_over_clamp"] = {"max_cut_m": eng["max_cut_m"],
                                   "max_fill_m": eng["max_fill_m"],
                                   "over_clamp_samples": eng["over_clamp"]}
    return row


def level_pool(pool: list[dict], p: dict, req: dict, ring_at, surface
               ) -> list[dict]:
    """Bill `pool` on the first rung of `pad_rungs` at which ANY candidate
    levels, leaving every candidate billed on that rung (the last one tried
    where none does); returns the rungs billed, `rung_row` each.  A rung that
    sizes every pad as the one before is not billed twice.  RAISES where the
    body cannot be measured."""
    rungs = pad_rungs(max(SF.footprint_extent(SF.requirement_defaults(req))))
    if not rungs:
        raise ValueError("LEVELLABLE_PAD_DEPENDS_ON names no pad rule")
    rows: list[dict] = []
    prev = None
    for name, shrink in rungs:
        sized = size_pool(pool, p, ring_at, shrink)
        shape = [(pe["w"], pe["d"], bp["w"], bp["d"]) for pe, bp, _r in sized]
        if shape == prev:
            continue
        prev = shape
        judge_pool(pool, sized, p, req, ring_at, surface)
        rows.append(rung_row(name, shrink, pool))
        verdicts = {d["flatten_cost"]["verdict"] for d in pool}
        if "clear" in verdicts or "refused" not in verdicts:
            break
    return rows


def rank_pool(pool: list[dict], req: dict) -> None:
    """Order `pool`, best first.  A pad the engine cannot level ranks after
    every pad it can; then satisfying sites first, and among them one whose
    fixture ring is levelled; among sites that cannot satisfy the requirement,
    the one that breaks it by the LEAST, and only then by score. Every miss is
    in metres, so summing them compares like with like, and it makes the
    recorded `violations` the thing being minimised rather than a footnote on
    a site chosen for other reasons.  A pool in which NO pad levels (every rung
    of `level_pool`) ranks by how far each candidate is past the clamp first:
    the site recorded REFUSED is the least bad, not the best scored."""
    levellable = any(d["flatten_cost"]["verdict"] == "clear" for d in pool)
    by_excess = _CLAMP_RANK == "excess" and not levellable
    pool.sort(
        key=lambda d: (
            (False if _CLAMP_RANK == "blind"
             else d["flatten_cost"]["verdict"] != "clear"),
            clamp_excess(d["flatten_cost"]) if by_excess else (0.0, 0),
            len(d["_violations"]) > 0,
            bool((d.get("fixture_ring") or {}).get("refused")),
            round(declared_miss(d, req), 1),
            -d["score"],
        )
    )


def pad_ladder_record(ladder: list[dict]) -> dict:
    """The block's account of a pad that is not the declared one: every rung
    billed, with its numbers, and where the pool ended."""
    last = ladder[-1]
    levelled = last["levellable"] > 0
    return {
        "depends_on": list(LEVELLABLE_PAD_DEPENDS_ON),
        "written": last["rung"], "levellable": levelled, "rungs": ladder,
        "why": ("no candidate's declared pad is within the engine's +/-"
                f"{SF.tcdata.CLAMP_M:g} m apply clamp, so the declared floor "
                "gave way until one was: the pad written is the largest the "
                "pool could level, and never smaller than the body at its "
                "yaw and its fixture ring need" if levelled else
                "no candidate could be levelled on any pad the ladder tried, "
                "down to the body at its yaw and its fixture ring; the site "
                "is the candidate least over the clamp and is REFUSED in "
                "this block with these numbers"),
    }


def recorded_clamp_violation(solved: dict) -> dict | None:
    """The `engine_clamp_m` row of a RECORDED block whose own bill
    (`flatten_cost`) is past the engine clamp, by the writer's own verdict
    (`tcdata.clamp_verdict`), or None when it levels or the block records no
    bill.  `--verify` asks it of every block: a pad past the clamp is never a
    site the solver recorded as written."""
    cost = solved.get("flatten_cost")
    if not cost:
        return None
    v = SF.tcdata.clamp_verdict(cost)
    if v["verdict"] != "refused":
        return None
    worst = max(v["max_fill_m"], v["max_cut_m"])
    return {"constraint": "engine_clamp_m", "required": SF.tcdata.CLAMP_M,
            "actual": round(worst, 2),
            "over_by": round(worst - SF.tcdata.CLAMP_M, 2)}


def ladder_line(key: str, ladder: list[dict]) -> str:
    first, last = ladder[0], ladder[-1]
    over = first.get("least_over_clamp")
    return (f"  .. {key}: {first['levellable']} of {first['candidates']} "
            f"candidates level on the declared {first['pad'][0]:g} x "
            f"{first['pad'][1]:g} m pad"
            + (f" (least over the clamp: cut {over['max_cut_m']} m, fill "
               f"{over['max_fill_m']} m, {over['over_clamp_samples']} samples)"
               if over else "")
            + f"; {last['levellable']} on the {last['pad'][0]:g} x "
            f"{last['pad'][1]:g} m pad ({last['rung']}, {len(ladder)} rungs "
            f"billed) (LEVELLABLE_PAD_DEPENDS_ON)")


def record(p: dict, target: dict, args) -> None:
    """Write one solved block into its placement -- under `--write` only."""
    if not args.write:
        return
    if args.label == "primary":
        p["solved"] = target
    else:
        p.setdefault("solved_alternatives", {})[args.seed] = target



def site_block(d: dict, req: dict, anchor_desc: str, routes: dict) -> dict:
    """The recorded form of one candidate site."""
    pe = d.get("pad_extent")
    v = SF.violations(d, req)
    block = {
        "x": round(d["x"], 1),
        "y": round(d["y"], 2),
        "z": round(d["z"], 1),
        # metres of the pad's LOWEST cell above Valheim's water plane (y = 30).
        # Negative means part of the footprint is under water. Measured on the
        # 1 m river-inclusive patch whenever the site was refined.
        "freeboard_m": round(d.get("freeboard_m", float("nan")), 2),
        "flat_spread_m": round(d["flat"], 2),
        "flat_resolution_m": d.get("flat_resolution_m", 1.0),
        # y is the footprint MAXIMUM (the blueprint's lowest piece is dropped
        # onto it), NOT the height at the centre. Read it with freeboard_m,
        # which is the footprint MINIMUM above the water plane.
        "y_centre_m": round(d.get("y_centre_m", float("nan")), 2),
        "biome_purity": round(d["biome_purity"], 3),
        # distance from the site CENTRE to the nearest cell at or below the
        # water plane. 0.0 means the centre cell is itself submerged -- which is
        # possible while `y` (the footprint maximum) is metres above water.
        "water_dist_m": round(d["water_dist"], 1),
        "anchor": anchor_desc,
        "anchor_dist_m": round(d["anchor_dist"], 0),
        # metres to the nearest cell of `near_biome`, when the requirement names
        # one. 0.0 means the site centre is itself in that biome.
        "near_biome_dist_m": round(d.get("near_biome_dist_m", 0.0), 1),
        # THE PAD `terraform/flatten.py` STAMPS, read back by
        # `flatten.pad_extent`: `written_pad`, i.e. the orientation the search
        # assumed (recorded under `pad_extent.declared`) grown wherever the body
        # at its yaw needs more.  A candidate never billed carries the searched
        # rectangle.
        "footprint_orientation": (pad_text(pe["w"], pe["d"]) if pe
                                  else d.get("footprint_orientation", "square")),
        "nearest_location_m": round(d["location_dist"], 0),
        "score": round(d["score"], 3),
        "scores": d.get("scores", {}),
        "overland_from_spawn": bool(d.get("overland_from_spawn", True)),
        "satisfies_requirement": not v,
    }
    if pe:
        block["pad_extent"] = {k: pe[k] for k in (
            "declared", "margin_m", "depends_on", "grew", "body_at_yaw",
            "wanted", "yaw_deg", "fixture_ring", "floor") if k in pe}
    if d.get("fixture_ring") is not None:
        block["fixture_ring"] = d["fixture_ring"]
    if d.get("flatten_cost"):
        block["flatten_cost"] = d["flatten_cost"]
    if d.get("refined"):
        block["flat_spread_coarse_m"] = d.get("flat_spread_coarse_m")
        block["flat_p5_p95_m"] = round(d.get("flat_p5_p95_m", float("nan")), 2)
        block["refined_moved_m"] = d.get("moved_m", 0.0)
    if v:
        block["violations"] = v
    block.update(routes)
    return block


def routes_for(g, d: dict, spawn_xz, anchor_xz) -> dict:
    out: dict = {}
    if spawn_xz is not None:
        out["route_from_spawn"] = SF.overland(g, spawn_xz, (d["x"], d["z"]))
    if anchor_xz is not None:
        out["route_to_anchor"] = SF.overland(g, (d["x"], d["z"]), anchor_xz)
    return out


FOOTPRINT_LADDER = (80, 70, 64, 60, 56, 50, 48, 40, 32, 24, 16)


def hard_feasible(g, anchors, avoid_xz, req: dict, spawn_xz=None) -> int:
    """How many grid-aligned footprints satisfy every HARD constraint.

    `spawn_xz` is not optional in practice: without it `reach_plane` returns
    None and `require_overland` is silently skipped, which made an earlier
    version of `remedy` advise "a 56 m footprint works here" for a placement
    where it does not. A remedy that is wrong is worse than no remedy.
    """
    total = 0
    p = SF.requirement_defaults(req)
    need = p["location_clearance_m"] + p["footprint_m"] * math.sqrt(2.0) * 0.5
    for inst in anchor_instances(anchors, req):
        pl = SF._planes(g, req, inst["xz"], avoid_xz, None, spawn_xz)
        idx = np.argwhere(pl["ok"])
        if not len(idx):
            continue
        if avoid_xz is None or not len(avoid_xz):
            total += len(idx)
            continue
        # The full-grid clearance mask is quantised to the grid and is therefore
        # up to half a cell diagonal (5.66 m at 8 m) LOOSER than the truth. Redo
        # it exactly, or the remedy advertises footprints that do not fit --
        # MEASURED: all 3 cells it offered at 56 m for pre-eikthyr failed the
        # exact check by 4 to 6 m.
        pts = np.stack(
            [pl["cx2d"][idx[:, 0], idx[:, 1]], pl["cz2d"][idx[:, 0], idx[:, 1]]], axis=1
        )
        ld, _ = SF._loc_tree(g, avoid_xz).query(pts, k=1)
        total += int((ld >= need).sum())
    return total


def remedy(g, anchors, avoid_xz, req: dict, spawn_xz=None) -> dict:
    """What WOULD satisfy the hard constraints, when nothing does.

    A violation record tells an operator the answer is bad. This tells them what
    to change. Two levers are searched, because they are the two the operator
    actually has: a smaller blueprint (the sibling `tools/jumpstart/library/`
    catalogues 176, many with small footprints) and a wider radius around the
    anchor. Biome is deliberately not offered -- changing it is changing which
    preset this is.
    """
    out: dict = {}
    fp = float(req.get("footprint_m", 56))
    for smaller in FOOTPRINT_LADDER:
        if smaller >= fp:
            continue
        if hard_feasible(g, anchors, avoid_xz, dict(req, footprint_m=smaller), spawn_xz) > 0:
            out["largest_workable_footprint_m"] = smaller
            out["blueprint_hint"] = (
                f"a blueprint whose footprint fits {smaller} m square satisfies "
                f"every hard constraint here; see tools/jumpstart/library/"
            )
            break
    if req.get("near") or req.get("near_xz"):
        for mult in (1.5, 2.0, 3.0, 5.0, 10.0):
            widened = float(req.get("within_m", 1500)) * mult
            if hard_feasible(g, anchors, avoid_xz, dict(req, within_m=widened), spawn_xz) > 0:
                out["within_m_needed"] = round(widened)
                out["within_m_factor"] = mult
                break
    # The last lever, and for a Swamp it is the only one: accept that the pad is
    # partly under water and build it on foundations.
    ceiling = None
    for inst in anchor_instances(anchors, req):
        got = SF.freeboard_ceiling(g, req, inst["xz"], avoid_xz)
        if got is not None:
            ceiling = got[0] if ceiling is None or got[0] > ceiling else ceiling
    if ceiling is not None and ceiling < float(
        req.get("min_height_m", SF.DEFAULT_FREEBOARD)
    ):
        out["min_height_m_needed"] = round(ceiling - 0.05, 2)
        out["foundations_note"] = (
            f"the driest footprint this seed offers here is {ceiling:.2f} m relative "
            f"to the water plane, so the pad is partly submerged wherever it goes: "
            f"declare min_height_m at or below that and build on foundations, or "
            f"pick a smaller footprint_m"
        )
    if not out:
        out["none"] = (
            "neither a smaller footprint, a wider radius nor accepting negative "
            "freeboard helps: the biome and purity combination has no solution "
            "in this seed"
        )
    return out


def flatness_floor(g, anchors, avoid_xz, req: dict) -> float | None:
    """Flattest grid-aligned footprint available anywhere the constraints allow.

    See site_finder.flat_floor: this is what the seed offers to within a
    fraction of a metre, not a formal bound over arbitrary 1 m offsets. When it
    sits well above `max_flat_m` the requirement is out of reach in this seed,
    and saying so is more use to an operator than another search.
    """
    best: float | None = None
    for inst in anchor_instances(anchors, req):
        fl = SF.flat_floor(g, req, inst["xz"], avoid_xz)
        if fl is not None and (best is None or fl < best):
            best = fl
    return best


def verify(world: Path, g, anchors, avoid_xz) -> int:
    """Audit every recorded `solved` block against its own requirement."""
    bad = 0
    print(f"{'preset/placement':46} {'constraint':22} {'required':>10} {'actual':>10} {'over':>8}")
    for path in sorted(world.glob("*/placements.yaml")):
        doc = yaml.safe_load(path.read_text("utf-8"))
        preset = doc.get("preset", path.parent.name)
        for p in doc.get("placements") or []:
            req = p.get("requirement")
            sol = p.get("solved")
            key = f"{preset}/{p['id']}"
            if not req:
                continue
            if not sol or "x" not in sol:
                print(f"{key:46} {'UNSOLVED':22} {(sol or {}).get('error','')}")
                bad += 1
                continue
            d = {
                "x": sol["x"],
                "z": sol["z"],
                "flat": sol.get("flat_spread_m", float("inf")),
                "biome_purity": sol.get("biome_purity", 0.0),
                "water_dist": sol.get("water_dist_m", float("inf")),
                "anchor_dist": sol.get("anchor_dist_m", float("inf")),
                "location_dist": sol.get("nearest_location_m", 0.0),
                # the recorded figure is the 1 m river-inclusive one; the coarse
                # grid is river-free and would only ever be more optimistic, so
                # re-deriving it here would weaken the audit, not strengthen it
                "freeboard_m": sol.get("freeboard_m"),
            }
            # re-derive the distances from the artefacts rather than trusting the
            # file: a stale solved block is exactly what this mode is hunting.
            if avoid_xz is not None and len(avoid_xz):
                tree = SF._loc_tree(g, avoid_xz)
                ld, _ = tree.query(np.array([[d["x"], d["z"]]]), k=1)
                d["location_dist"] = float(ld[0])
            inst = anchor_instances(anchors, req)
            if inst and inst[0]["xz"] is not None:
                d["anchor_dist"] = min(
                    math.hypot(d["x"] - i["xz"][0], d["z"] - i["xz"][1]) for i in inst
                )
            d["water_dist"] = SF._sample(g, SF._water_edt(g), d["x"], d["z"])
            vs = SF.violations(d, req)
            clamp = recorded_clamp_violation(sol)
            if clamp is not None:
                vs.append(clamp)
            if not vs:
                print(f"{key:46} {'OK':22}")
            for v in vs:
                bad += 1
                print(
                    f"{key:46} {v['constraint']:22} {v['required']:10.2f} "
                    f"{v['actual']:10.2f} {v['over_by']:8.2f}"
                )
    # and the pads against EACH OTHER, over the whole save
    # (SEPARATION_SCOPE_DEPENDS_ON): a set of blocks can each satisfy their
    # own requirement and still stand two buildings on one meadow.
    bad += report_overlaps({p: yaml.safe_load(p.read_text("utf-8"))
                            for p in sorted(world.glob("*/placements.yaml"))})
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world", required=True, help="tools/jumpstart/worlds/<World>")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--locations", required=True)
    ap.add_argument("--clearance", type=float, default=SF.DEFAULT_LOCATION_CLEARANCE)
    ap.add_argument(
        "--label",
        default="primary",
        choices=("primary", "alternative"),
        help="primary writes `solved`; alternative writes solved_alternatives[<seed>]",
    )
    ap.add_argument(
        "--manifest",
        default=str(HERE / "data" / "corpus_manifest.json"),
        help="used to locate each blueprint body for the container census",
    )
    ap.add_argument("--vh-src", help="server install dir; enables the 1 m refinement pass")
    ap.add_argument("--patch", help="patch dump path (written by run_patchscan.sh)")
    ap.add_argument(
        "--field",
        help="the seed's 1 m field (stage 4, <seed>_1m.bin): the router's land "
        "labelling, which NETWORK_LAND_DEPENDS_ON judges waterfronts on",
    )
    ap.add_argument("--patch-sandbox", help="sandbox dir for run_patchscan.sh")
    ap.add_argument(
        "--reuse-patch",
        action="store_true",
        help="reuse an existing --patch file instead of re-running the scan",
    )
    ap.add_argument(
        "--no-refine",
        action="store_true",
        help="skip the 1 m pass. The coarse grid MEASURABLY mis-calls flatness, "
        "so this is for debugging only and stamps flat_resolution_m accordingly.",
    )
    ap.add_argument("--shortlist", type=int, default=5, help="ranked alternatives recorded per site")
    ap.add_argument(
        "--skip-river-check",
        action="store_true",
        help="do not probe whether the grid includes rivers (debugging only)",
    )
    ap.add_argument("--verify", action="store_true", help="audit existing solved blocks, write nothing")
    ap.add_argument("--write", action="store_true", help="update the yaml files in place")
    args = ap.parse_args(argv)

    # ONE REFUSAL, not one per reader: this was a private copy that printed its
    # own wording and returned 2, while `worldseed.require_scope` said the same
    # thing for the height field, the location dump and the solved site set.
    # `SF.load_grid` now makes the call with the run's resolved seed; an
    # explicit `--seed` is compared here because an explicit expectation nobody
    # compared is exactly the unread declaration the guard exists for.
    g = SF.load_grid(Path(args.grid))
    SF._worldseed().require_scope(
        "coarse biome grid", args.grid, found_seed=g["seed"], seed_=args.seed,
        produced_by="tools/seedscan/run_scan.sh SEEDS=<this seed> HEIGHT=1")
    global CLEARING_LOCATIONS
    CLEARING_LOCATIONS = args.locations
    anchors = SF.load_anchors(Path(args.locations))
    avoid_xz = np.asarray(
        [(l["x"], l["z"]) for inst in anchors.values() for l in inst], dtype=np.float64
    )
    print(
        f"seed={g['seed']} hash={g['hash']} step={g['step']}m "
        f"locations={len(avoid_xz)} clearance={args.clearance}m",
        file=sys.stderr,
    )

    world = Path(args.world)
    if args.verify:
        bad = verify(world, g, anchors, avoid_xz)
        print(f"\n{bad} constraint violation(s)", file=sys.stderr)
        return 1 if bad else 0

    refine = not args.no_refine
    patch_path = Path(args.patch) if args.patch else Path("/tmp/jumpstart_patch.bin")
    if refine and not args.reuse_patch and not args.vh_src:
        print(
            "refusing to solve without the 1 m pass: pass --vh-src (to run "
            "run_patchscan.sh), or --reuse-patch with an existing --patch file, "
            "or --no-refine and accept coarse-grid flatness.",
            file=sys.stderr,
        )
        return 2

    if args.vh_src and not args.skip_river_check:
        problem = assert_grid_has_rivers(
            g, args.vh_src, args.seed, patch_path.parent, args.patch_sandbox
        )
        if problem:
            print(f"refusing to solve: {problem}", file=sys.stderr)
            return 2

    sources: dict[str, Path] = {}
    mpath = Path(args.manifest)
    if mpath.exists():
        best: dict[str, tuple[Path, int]] = {}
        for e in json.loads(mpath.read_text("utf-8"))["entries"]:
            if not e["sha256"]:
                continue
            prev = best.get(e["file"])
            if prev is None or e["bytes"] > prev[1]:
                best[e["file"]] = (Path(e["source"]), e["bytes"])
        sources = {k: v[0] for k, v in best.items()}

    files = sorted(world.glob("*/placements.yaml"))
    if not files:
        print(f"no placements.yaml under {world}", file=sys.stderr)
        return 1

    spawn_xz = None
    if anchors.get("StartTemple"):
        st = anchors["StartTemple"][0]
        spawn_xz = (st["x"], st["z"])

    # NETWORK_LAND_DEPENDS_ON: each network's land is labelled once, on first
    # use.  Pass 1 masks on the spawn's component (the root is not solved
    # yet); pass 3 judges on the root, the `portal.role: hub` placement, as it
    # writes it.
    nets: dict = {}
    fields = None

    def net_for(xz, root: str | None = None):
        nonlocal fields
        if xz not in nets:
            if fields is None and args.field and Path(args.field).is_file():
                fields = _crossings_survey().W.load(args.field)
            nets[xz] = (network_land(fields, xz, root) if root
                        else network_land(fields, xz))
        return nets[xz]

    def spawn_mask(variant: dict, anchor_xz):
        net = net_for(spawn_xz, "the spawn (StartTemple)")
        if "unread" in net or not serves(net, anchor_xz):
            return None
        return network_plane(g, net, variant)

    # ---- pass 1: coarse pools for every placement -------------------------
    docs: dict[Path, dict] = {}
    jobs: list[dict] = []
    failures = 0
    # ONE EXCLUSION SET PER SAVE, not per preset file: every preset's solved
    # placement is built into this one world (SEPARATION_SCOPE_DEPENDS_ON).
    excludes: dict[str, list[tuple[float, float, float]]] = {}
    for path in files:
        doc = yaml.safe_load(path.read_text("utf-8"))
        docs[path] = doc
        preset = doc.get("preset", path.parent.name)
        # Each solved site becomes an exclusion zone for the next, ACROSS
        # presets: they are built into one save, so two presets resolving to
        # the same meadow stacks two buildings on it.
        exclude = excludes.setdefault(separation_bucket(path), [])
        for p in doc.get("placements") or []:
            src = sources.get(p.get("blueprint", ""))
            if src is not None and src.exists():
                p["containers"] = container_census(src)
            req = p.get("requirement")
            if not req:
                continue
            pool, relaxed, err = solve_coarse(
                g, anchors, avoid_xz, req, exclude, refine, spawn_xz,
                mask_for=spawn_mask if waterfront_gated(p, req) else None,
            )
            if err:
                failures += 1
                print(f"  !! {preset}/{p['id']}: {err}")
                jobs.append(
                    {"path": path, "p": p, "req": req, "pool": [], "relaxed": None, "error": err,
                     "key": f"{preset}/{p['id']}", "preset": preset}
                )
                continue
            fp = max(SF.footprint_extent(SF.requirement_defaults(req)))
            if claims_ground(p):
                exclude.append((pool[0]["x"], pool[0]["z"], fp * 1.5))
            jobs.append(
                {"path": path, "p": p, "req": req, "pool": pool, "relaxed": relaxed,
                 "error": None, "key": f"{preset}/{p['id']}", "preset": preset}
            )
    print(
        f"coarse pass: {sum(len(j['pool']) for j in jobs)} candidates over "
        f"{len(jobs)} placements",
        file=sys.stderr,
    )

    # ---- pass 2: 1 m refinement ------------------------------------------
    patches: dict[str, dict] = {}
    if refine and any(j["pool"] for j in jobs):
        req_path = patch_path.with_suffix(".req.tsv")
        rows = build_patch_request(jobs, req_path)
        stale = True
        if args.reuse_patch and patch_path.exists():
            patches = SF.load_patches(patch_path)
            stale = not patches_match(jobs, patches)
            print(
                f"reusing {patch_path}"
                if not stale
                else f"{patch_path} does not match this coarse pool; re-scanning",
                file=sys.stderr,
            )
        if stale:
            if not args.vh_src:
                print(
                    f"{patch_path} does not cover the current coarse pool and "
                    f"--vh-src was not given, so it cannot be regenerated.",
                    file=sys.stderr,
                )
                return 2
            print(f"1 m patch scan: {rows} windows -> {patch_path}", file=sys.stderr)
            run_patchscan(args.vh_src, args.seed, req_path, patch_path, args.patch_sandbox)
            patches = SF.load_patches(patch_path)

    # ---- the engine surface: where every written pad is BILLED ------------
    # The 1 m patches above are WorldGenerator.GetHeight -- right for FINDING a
    # flat meadow, and not the surface `ApplyToHeightmap` adds the delta to.
    # `flatten_cost` is the datum every downstream step stands on (flatten,
    # to_rcon_plan --align floor-center, stock), so it is made by
    # `site_finder.engine_bill` on the heightmap surface over the rectangle
    # `flatten.pad_extent` will read back.  Cached beside the patch file, per
    # seed, each scan's sidecar declaring its surface.
    surface = SF.EngineSurface(args.seed, SF.SITE_BILL_SURFACE,
                               cache_dir=patch_path.parent / "engine_zones",
                               fetch=bool(args.vh_src), halo=0,
                               vh_src=args.vh_src)

    # ---- pass 3: verdict + write -----------------------------------------
    # Sites chosen so far IN THIS SAVE (separation_bucket: every preset is
    # built into it). The coarse pass already spread the pools apart, but the
    # 1 m pass moves sites by up to PATCH_PAD, so the final, authoritative
    # separation check happens here against the coordinates that actually get
    # written.
    taken: dict[str, list[tuple[float, float, float]]] = {}
    root_xz = None

    for job in jobs:
        p, req, path = job["p"], job["req"], job["path"]
        if job["error"]:
            target = {"seed": args.seed, "error": job["error"]}
        else:
            pool = job["pool"]
            if refine:
                refined = []
                for i, d in enumerate(pool):
                    patch = patches.get(f"{job['key']}#{i}")
                    if patch is None:
                        continue
                    refined.append(
                        SF.refine(
                            d,
                            patch,
                            dict(d.get("_req", req), _swap=d.get("_swap", False)),
                            d["anchor_xz"],
                            avoid_xz,
                            g,
                            spawn_xz,
                            declared_req=dict(req, _swap=d.get("_swap", False)),
                        )
                    )
                if refined:
                    pool = refined
            fp = float(req.get("footprint_m", 56))
            here = taken.setdefault(separation_bucket(path), [])
            pool = separation_free(pool, here, fp) or pool
            # NETWORK_LAND_DEPENDS_ON: a waterfront the network serves moves
            # to the next candidate whose landward apron is on the network's
            # land; none left is recorded and REFUSED below, with its numbers.
            reach_of: dict[int, dict] = {}
            if waterfront_gated(p, req):
                n_pool = len(pool)
                pool, reach_of = network_served(pool, net_for(root_xz), p)
                if reach_of:
                    served = sum(v["verdict"] == "on_network"
                                 for v in reach_of.values())
                    print(f"  .. {job['preset']}/{p['id']}: {served} of "
                          f"{n_pool} candidates on the network's land "
                          f"(NETWORK_LAND_DEPENDS_ON)", file=sys.stderr)
            # THE BILL IS THE WRITE (site_finder.SITE_BILL_SURFACE_DEPENDS_ON):
            # every candidate's `flatten_cost` is replaced by the engine's bill
            # of the pad as it will be written -- `written_pad`, the body at
            # its yaw (flatten.PAD_EXTENT_DEPENDS_ON) -- before anything ranks
            # on it.  So a pad that grows past the clamp ranks after every pad
            # that does not, and the solver MOVES the site; none left is a
            # refusal in the block, with its numbers.
            # THE PAD IS SIZED FOR WHAT STANDS ON IT (flatten.PAD_EXTENT_
            # DEPENDS_ON `fixture_ring`): the fixtures step 6 will seat, solved
            # once at the first candidate (the ring moves with the centre).
            # Where the ring pad is beyond the clamp and the body's pad is not,
            # the BODY's pad is written and the ring is recorded refused with
            # its numbers -- step 6 then refuses, by number, each fixture that
            # needed it.  A site is never lost to its fixtures.
            # THE PAD WRITTEN IS ONE THE ENGINE CAN LEVEL (LEVELLABLE_PAD_
            # DEPENDS_ON): where no candidate's DECLARED pad is within the
            # clamp the declared floor gives way a step at a time, the whole
            # pool re-billed at each, and the site is chosen on the largest pad
            # any candidate levels.  None levels on any rung: the candidate
            # least over the clamp is recorded REFUSED, with every rung's
            # numbers.
            # WATERFRONT_YAW_DEPENDS_ON: a waterfront's yaw is SOLVED per
            # candidate from the facing measurement, before its pad is sized,
            # so the pad, the ring and the bill are all at the yaw written.
            inherited = float((p.get("rotation") or {}).get("yaw", 0) or 0)
            for d in pool:
                d["_yaw"] = candidate_yaw(g, req, d["x"], d["z"], inherited)
            rings: dict[float, dict] = {}

            def ring_at(d):
                y = d["_yaw"]["yaw"]
                if y not in rings:
                    rings[y] = fixture_ring_of(world.name,
                                               dict(job, p=at_yaw(p, y)), d)
                return rings[y]

            ring = ring_at(pool[0]) if pool else {}
            if ring and "x" not in ring:
                print(f"  .. {job['preset']}/{p['id']}: fixture ring unread "
                      f"({ring['unread']}); pad sized for the body alone")
            try:
                ladder = level_pool(pool, p, req, ring_at, surface)
            except (ValueError, FileNotFoundError) as e:
                # UNREAD IS NOT CLEAR: a pad sized without the body is the
                # defect, so a body that cannot be measured gets no pad.
                failures += 1
                target = {"seed": args.seed, "error": (
                    f"the pad cannot be sized at the body's yaw "
                    f"(flatten.placed_pad_at_yaw): {type(e).__name__}: {e}")}
                print(f"  !! {job['preset']}/{p['id']}: {target['error']}")
                record(p, target, args)
                continue
            if len(ladder) > 1:
                print(ladder_line(f"{job['preset']}/{p['id']}", ladder))
            rank_pool(pool, req)
            best = pool[0]
            if best["flatten_cost"]["verdict"] == "unread":
                # UNREAD IS NOT CLEAR, and a block without a datum is worse
                # than none: `flatten.build` falls back to `y_centre_m`, the
                # finding surface, when `target_y` is absent.
                failures += 1
                target = {"seed": args.seed, "error": (
                    f"the {surface.surface} surface is unread for zones "
                    f"{best['flatten_cost']['unread_zones']} under every "
                    f"candidate, so no pad bill can be made; re-run with "
                    f"--vh-src so the zones can be scanned")}
                print(f"  !! {job['preset']}/{p['id']}: {target['error']}")
                record(p, target, args)
                continue
            breach = separation_breach(best, here, fp) if claims_ground(p) else None
            if claims_ground(p):
                here.append((best["x"], best["z"], fp * 1.5))
            anchor_desc = best.get("anchor", "")
            target = {"seed": args.seed}
            target.update(
                site_block(best, req, anchor_desc, routes_for(g, best, spawn_xz, best.get("anchor_xz")))
            )
            eng = best["flatten_cost"]
            if eng["verdict"] != "clear":
                # NO CANDIDATE is levellable on any pad the ladder tried
                # (LEVELLABLE_PAD_DEPENDS_ON), so this site is the one LEAST
                # over the clamp on the surface the engine levels
                # (`rank_pool`) and REFUSED in its own block, with the numbers.
                worst = max(eng["max_fill_m"], eng["max_cut_m"])
                target.setdefault("violations", []).append({
                    "constraint": "engine_clamp_m",
                    "required": SF.tcdata.CLAMP_M,
                    "actual": round(worst, 3),
                    "over_by": round(worst - SF.tcdata.CLAMP_M, 3),
                    "over_clamp_samples": eng["over_clamp"],
                    "surface": eng["surface"],
                    **({"rungs_tried": [r["rung"] for r in ladder]}
                       if len(ladder) > 1 else {})})
                target["satisfies_requirement"] = False
            if len(ladder) > 1:
                target["pad_ladder"] = pad_ladder_record(ladder)
            if breach is not None:
                target.setdefault("violations", []).append(breach)
                target["satisfies_requirement"] = False
            reach = reach_of.get(id(best))
            if reach is not None and reach["verdict"] != "on_network":
                target.setdefault("violations", []).append(
                    network_land_violation(reach))
                target["satisfies_requirement"] = False
            solved_yaw = best["_yaw"]
            if best.get("_clearing") is not None:
                target.setdefault("violations", []).append(best["_clearing"])
                target["satisfies_requirement"] = False
            if solved_yaw.get("refused"):
                target.setdefault("violations", []).append(solved_yaw["refused"])
                target["satisfies_requirement"] = False
            if solved_yaw["yaw"] != inherited:
                # The written yaw is the solved one: every reader of the body's
                # turn (flatten, stock, derive, clearing, waypoints) reads
                # `rotation.yaw`, so that is its one home.  The evidence is here.
                target["yaw_solved"] = {k: solved_yaw[k] for k in (
                    "inherited", "yaw", "by_cardinal_yaw", "why")}
                if args.label == "primary":
                    p.setdefault("rotation", {})["yaw"] = yaw_value(solved_yaw["yaw"])
            if job["relaxed"] and target.get("violations"):
                target["relaxed"] = job["relaxed"]
            elif job["relaxed"]:
                # the relaxed search found something that satisfies the declared
                # requirement after all (the 1 m pass moved it back inside)
                target["relaxed"] = dict(job["relaxed"], why="widened during search; final site satisfies the declared requirement")
            if any(v["constraint"] == "min_height_m" for v in target.get("violations", ())):
                best_fb = None
                dry_cells = 0
                for inst in anchor_instances(anchors, req):
                    got = SF.freeboard_ceiling(g, req, inst["xz"], avoid_xz)
                    if got is None:
                        continue
                    fb, n = got
                    dry_cells += n
                    best_fb = fb if best_fb is None or fb > best_fb else best_fb
                if best_fb is not None:
                    target["freeboard_ceiling_m"] = {
                        "best_available_m": round(best_fb, 2),
                        "dry_footprints_in_seed": dry_cells,
                        "note": (
                            "highest footprint-minimum freeboard this seed offers under "
                            "every other declared constraint, over every anchor instance, "
                            "measured on the coarse grid. dry_footprints_in_seed is how "
                            "many grid-aligned footprints clear min_height_m at all. Zero "
                            "means no dry pad of this size exists here and the negative "
                            "freeboard recorded above is forced, not chosen -- the base "
                            "needs foundations or a smaller footprint_m."
                        ),
                    }
            # Run this for EVERY placement, not just the one whose yaw was
            # caught wrong. A yaw is inherited from wherever the blueprint was
            # captured and means nothing at a re-solved site, so it has to be
            # re-checked against the world every time. Which mode applies is
            # read off the requirement: a served biome if one is named, open
            # water for anything waterside, otherwise the bearing to the anchor.
            yaw = float(solved_yaw["yaw"])
            # Is this bill near the biome's practical floor, or merely the best
            # we searched? That distinction is what tells an operator whether to
            # accept the earthwork or reconsider the building, so it is recorded
            # every time rather than argued about per placement.
            floor = None
            for inst in anchor_instances(anchors, req):
                for swap in ([False, True] if req.get("footprint_xz_m") else [False]):
                    fl = SF.flat_floor(
                        g, dict(req, _swap=swap), inst["xz"], avoid_xz
                    )
                    if fl is not None and (floor is None or fl < floor):
                        floor = fl
            if floor is not None:
                # Two reference points, because they answer different questions
                # and only one of them is a measurement of achievable ground:
                #   * the coarse floor is a LOWER BOUND on grid-aligned pads, and
                #     the 1 m pass usually cannot reach it -- the coarse grid
                #     under-reports spread (MEASURED: median 1.4 m, p90 8.0 m at
                #     a 70 m footprint), so judging the bill against it would
                #     report "flatter ground exists" for ground that does not;
                #   * the pool best is the flattest thing actually MEASURED at
                #     1 m across every candidate refined this run, which is the
                #     honest floor of what was searched.
                pool_best = min(d["flat"] for d in pool)
                slack = target["flat_spread_m"] - pool_best
                target["flatten_floor"] = {
                    "chosen_spread_m": target["flat_spread_m"],
                    "pool_best_1m_spread_m": round(pool_best, 2),
                    "candidates_measured_at_1m": len(pool),
                    "biome_coarse_floor_m": round(floor, 2),
                    "verdict": (
                        "at the floor of what was measured: the flattest of "
                        f"{len(pool)} candidates refined at 1 m, so the earthwork is "
                        "the character of this biome, footprint and radius rather "
                        "than a poor pick"
                        if slack <= 0.5
                        else f"{slack:.1f} m flatter ground was measured in this run's "
                        "own pool and lost on other criteria; widen the radius or "
                        "check the scoring before accepting the bill"
                    ),
                    "note": (
                        "biome_coarse_floor_m is a grid-aligned LOWER bound over every "
                        "anchor instance and both footprint orientations, not an "
                        "achievable spread; compare the bill against "
                        "pool_best_1m_spread_m."
                    ),
                }
            if req.get("near_biome"):
                # The ray has to be long enough to REACH what it is aiming at.
                # MEASURED: iron-era-workshop sits 437 m from its swamp, so the
                # 400 m default ray never entered it and the check reported a
                # correct yaw as wrong.
                target["facing_check"] = SF.facing_check(
                    g, target["x"], target["z"], yaw, str(req["near_biome"]),
                    reach_m=max(400.0, 2.0 * target["near_biome_dist_m"]),
                )
            elif req.get("coastal"):
                target["facing_check"] = SF.facing_check(
                    g, target["x"], target["z"], yaw, water=True
                )
            elif best.get("anchor_xz"):
                target["facing_check"] = SF.facing_check(
                    g, target["x"], target["z"], yaw, anchor_xz=best["anchor_xz"]
                )
            if target.get("violations"):
                # A violation record says the answer is bad; this says what to
                # change so it would not be.
                target["remedy"] = remedy(g, anchors, avoid_xz, req, spawn_xz)
            # The pool packs candidates half a footprint apart for coverage;
            # thin the recorded alternatives back out so each row an operator
            # reads is a different place, not the same meadow shifted 30 m.
            alts: list[dict] = [best]
            for d in pool[1:]:
                if len(alts) > args.shortlist:
                    break
                if any(
                    math.hypot(d["x"] - o["x"], d["z"] - o["z"]) < fp * SHORTLIST_SEPARATION
                    for o in alts
                ):
                    continue
                alts.append(d)
            shortlist = [site_block(d, req, d.get("anchor", ""), {}) for d in alts[1:]]
            if shortlist:
                target["shortlist"] = shortlist
            flag = "OK " if target["satisfies_requirement"] else "!! "
            print(
                f"  {flag}{job['preset']:18} {p['id']:28} -> "
                f"({target['x']:.1f}, {target['y']:.2f}, {target['z']:.1f}) "
                f"flat={target['flat_spread_m']}m@{target['flat_resolution_m']}m "
                f"anchor={target['anchor_dist_m']}m loc={target['nearest_location_m']}m "
                f"level={(target.get('flatten_cost') or {}).get('moved_m3', 0):.0f}m3 "
                f"score={target['score']} alts={len(shortlist)}"
            )
            for v in target.get("violations", []):
                print(
                    f"      violates {v['constraint']}: required {v['required']}, "
                    f"got {v['actual']} (over by {v['over_by']})"
                )
        record(p, target, args)
        if (p.get("portal") or {}).get("role") == "hub" and "x" in target:
            root_xz = (target["x"], target["z"])

    # ---- pass 4: ONE BUILDING, ONE COORDINATE ----------------------------
    # `duplicate_of: <preset>#<id>` DECLARES that two placements are the same
    # physical structure standing in one place.  `terraform/sites.py:136-146`
    # REFUSES the build outright when the two disagree, and
    # `network/waypoints.py:677` reads it for portal tags -- but nothing had
    # ever made them AGREE.  Passes 1-3 solve every placement that has a
    # `requirement`, independently, so the coordinate of one building was
    # written in two places, free to differ.
    #
    # MEASURED 2026-09-23.  On Ulfsland `deepnorth-sandbox#sandbox-harbour` and
    # `pre-elder#early-dock` both sit at (675.4, -85.6) -- identical, so the
    # two homes never disagreed and the defect was invisible for the whole life
    # of the fixture world.  On the first fresh seed they solved to
    # (-1391.5, -55.5) and (-209.5, -413.5): 1,258 m apart, and
    # `roads/network.py` refused stage 9 with "Two presets of the same world
    # disagree about where one building stands".  The requirements are NOT the
    # same (the harbour declares min_height_m -2.0, the dock max_grade_pct 5.0)
    # so there was never any reason for two independent solves to land together
    # -- Ulfsland's agreement was an accident of one world's terrain.
    #
    # THE DEFECT CLASS: a key naming a SUPERSET.  `solved` was keyed per
    # PLACEMENT when the coordinate depends on the BUILDING, and `duplicate_of`
    # is the declaration that says which placements are one building.  The
    # remedy is not a tolerance: the deferring placement gets NO solve of its
    # own, it gets the principal's, so there is one home for the number.
    dup_fixed, dup_broken = 0, 0
    if args.write and args.label == "primary":
        solved_by_qualified: dict[str, dict] = {}
        for path, doc in docs.items():
            preset = doc.get("preset", path.parent.name)
            for q in doc.get("placements") or []:
                if q.get("solved"):
                    solved_by_qualified[f"{preset}#{q['id']}"] = q
        for path, doc in docs.items():
            preset = doc.get("preset", path.parent.name)
            for q in doc.get("placements") or []:
                defers_to = q.get("duplicate_of")
                if not defers_to:
                    continue
                principal = solved_by_qualified.get(defers_to)
                if principal is None:
                    # NAMED, never silently dropped: the declaration points at
                    # a placement this world does not solve, so there is no
                    # coordinate to defer to and the build would refuse later
                    # with less context than here.
                    dup_broken += 1
                    print(f"  !! {preset}/{q['id']}: duplicate_of {defers_to} "
                          f"is not a solved placement in this world",
                          file=sys.stderr)
                    continue
                if principal.get("blueprint") != q.get("blueprint"):
                    dup_broken += 1
                    print(f"  !! {preset}/{q['id']}: duplicate_of {defers_to} "
                          f"names a DIFFERENT body "
                          f"({q.get('blueprint')} vs {principal.get('blueprint')}); "
                          f"two different buildings are not one structure",
                          file=sys.stderr)
                    continue
                own = q.get("solved") or {}
                src = dict(principal["solved"])
                src["deferred_from"] = {
                    "principal": defers_to,
                    "why": ("this placement declares duplicate_of, so it is the "
                            "SAME structure as the principal and shares its one "
                            "coordinate. Its own requirement was still solved "
                            "and is recorded under solved_independent for "
                            "review -- it is evidence, never a second home for "
                            "the number anything reads."),
                }
                if own and (round(float(own.get("x", 0)), 1),
                            round(float(own.get("z", 0)), 1)) != \
                           (round(float(src.get("x", 0)), 1),
                            round(float(src.get("z", 0)), 1)):
                    src["solved_independent"] = own
                    dup_fixed += 1
                    print(f"  == {preset}/{q['id']}: deferring to {defers_to} at "
                          f"({src['x']:.1f}, {src['z']:.1f}); its own solve at "
                          f"({own.get('x', 0):.1f}, {own.get('z', 0):.1f}) kept as "
                          f"solved_independent")
                q["solved"] = src
                # ...and its turn: one building stands at one yaw, and a solved
                # waterfront yaw (WATERFRONT_YAW_DEPENDS_ON) is the principal's.
                p_yaw = (principal.get("rotation") or {}).get("yaw")
                if p_yaw is not None and (q.get("rotation") or {}).get("yaw") != p_yaw:
                    q.setdefault("rotation", {})["yaw"] = p_yaw

    # ---- pass 5: NO TWO BUILT PADS SHARE GROUND --------------------------
    # Judged on the coordinates that get WRITTEN, after the deferral above,
    # over the whole save (SEPARATION_SCOPE_DEPENDS_ON).  The separation in
    # passes 1 and 3 is a centre distance; this is the rectangles the build
    # stamps, so a pad that slid inside another's reach is named here rather
    # than at step 2 of a live build, where the pad guard does not judge
    # pad-versus-pad at all.
    overlapping = report_overlaps(docs) if args.label == "primary" else 0

    if args.write:
        for path, doc in docs.items():
            if args.label == "primary":
                doc["seed"] = args.seed
                # NO `spawn_points` here any more. It used to be a naive
                # footprint-and-yaw offset with y = ceil(solved.y + 1), where
                # solved.y is the footprint MAXIMUM -- so it was ~7 m of airtime
                # above the ground at its own xz, and it was never terrain-tested.
                # tools/jumpstart/worlds/derive.py now owns spawn derivation and
                # probes the exact integer it writes at 1 m with rivers included.
                # A second, unverified answer in the tree is a trap for the next
                # reader, so this one is gone rather than merely deprecated.
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), "utf-8"
            )
            print(f"  wrote {path}")

    violating = sum(
        1
        for j in jobs
        if not j["error"]
        and (j["p"].get("solved") or {}).get("satisfies_requirement") is False
    )
    if failures:
        print(f"{failures} placement(s) unsolved", file=sys.stderr)
    if violating and args.write:
        print(
            f"{violating} placement(s) recorded WITH violations (see `violations:` "
            f"in the files)",
            file=sys.stderr,
        )
    if dup_fixed:
        print(f"{dup_fixed} duplicate placement(s) took their principal's "
              f"coordinate instead of a second solve of their own",
              file=sys.stderr)
    if dup_broken:
        # A `duplicate_of` that cannot be resolved is not a warning: every
        # downstream reader (terraform/sites.py, network/waypoints.py) exits on
        # it, so reporting 0 here would hand the failure to the build.
        print(f"{dup_broken} `duplicate_of` declaration(s) could not be "
              f"resolved", file=sys.stderr)
    if overlapping:
        print(f"{overlapping} pair(s) of built placements OVERLAP; refusing "
              f"(see `!! OVERLAP` above)", file=sys.stderr)
    return 1 if (failures or dup_broken or overlapping) else 0


# ---------------------------------------------------------------------------
# PAD_EXTENT_DEPENDS_ON ON THE PRESET PATH, driven through THIS emitter
# (`written_pad` -> `site_block` -> `flatten.pad_extent`) and judged by the
# writer's own check (`flatten.placed_body_cover`), on bodies registered in
# flatten's per-(body, yaw) cache under names no library body has - the way
# settlements/build.py's pad cases register theirs.  No library, no scan.
# ---------------------------------------------------------------------------
# pre-moder#mountain-outpost AS STAGE 7 WROTE IT ON FRESH42 (2026-09-24): the
# search assumed the SWAPPED rectangle, 30.8 along x by 20.3 along z, and the
# body stands at yaw 180, where `placed_body_extents` measures
# hs_swamp_vrolok_caba_a_2 at 21.697 x 33.477 m - made solid here.  The live
# build's step 2 refused it: 308 of 748 vertices unstamped, overhang z 6.588.
_FX_OUTPOST = {
    "place": {"id": "mountain-outpost", "blueprint": "fixture-outpost.blueprint",
              "rotation": {"yaw": 180}, "align": "floor-center",
              "footprint_xzy_m": [20.3, 30.8, 6.5]},
    "cand": {"x": -7591.5, "z": 289.5,
             "footprint_orientation": "30.8 m along x by 20.3 m along z"},
    "slab": (21.697, 33.477)}
# deepnorth-sandbox#sandbox-portal-hub as stage 7 wrote it: a 34.1 m square at
# yaw 135.  The body is not a slab, so its extents are the ones
# `placed_body_extents` MEASURES for salty-dick-portal-hub-final, verbatim.
# The live build's step 2 refused it: 140 of 1296 vertices unstamped.
_FX_HUB = {
    "place": {"id": "sandbox-portal-hub", "blueprint": "fixture-hub.blueprint",
              "rotation": {"yaw": 135}, "align": "floor-center",
              "footprint_xzy_m": [28.2, 28.1, 15.0]},
    "cand": {"x": -220.5, "z": -411.5,
             "footprint_orientation": "34.1 m along x by 34.1 m along z"},
    "extents": {"unturned": (28.906, 29.067), "at_yaw": (34.115, 34.083)}}
# A yaw-0 installation: complete-station-hub's 20.06 x 24.0 m body on its
# searched 20.1 x 24.0 m rectangle, centred on whole metres.
_FX_YAW0 = {
    "place": {"id": "yaw0", "blueprint": "fixture-yaw0.blueprint",
              "rotation": {"yaw": 0}, "align": "floor-center",
              "footprint_xzy_m": [16.3, 22.0, 15.0]},
    "cand": {"x": 104.0, "z": 362.0,
             "footprint_orientation": "20.1 m along x by 24.0 m along z"},
    "slab": (20.06, 24.0)}
_FX_REQ = {"biome": "Mountain", "max_flat_m": 3.0, "footprint_m": 40}


def _fx_without(term: str) -> tuple:
    return tuple(t for t in FL.PAD_EXTENT_DEPENDS_ON if t != term)


def _fx_block(fx: dict, depends_on: tuple = FL.PAD_EXTENT_DEPENDS_ON,
              surface=None) -> tuple[dict, dict, dict]:
    """`(candidate, solved block, body cover)`: the block this emitter records
    for fixture `fx` - billed on `surface` when one is given - and
    `flatten.placed_body_cover` of the body at its own yaw on the pad
    `flatten.build` reads back from that block."""
    place = fx["place"]
    yaw = float(place["rotation"]["yaw"])
    if "slab" in fx:
        objs, geom = FL._slab(*fx["slab"])
        ext = FL.body_extents(objs, yaw, geom)
    else:
        ext = {**fx["extents"], "yaw_deg": yaw, "pivot_only": 0}
    FL._BODY_EXTENTS[(place["blueprint"], yaw)] = {
        **ext, "blueprint": place["blueprint"]}
    d = dict(fx["cand"], y=0.0, flat=0.0, biome_purity=1.0, water_dist=1e3,
             anchor_dist=0.0, location_dist=1e3, score=0.0)
    d["pad_extent"] = written_pad(place, d, depends_on)
    if surface is not None:
        d["flatten_cost"] = written_bill(surface, d["pad_extent"])
    blk = site_block(d, _FX_REQ, "", {})
    return d, blk, FL.placed_body_cover(dict(place, solved=blk))


def _fx_bill_is_write(fx: dict) -> list[str]:
    """The solver bills `written_pad` on an engine surface of zeros; the
    writer stamps the block `site_block` recorded.  Every sample the bill
    names must be one `flatten.build` stamps, and every vertex the engine
    answers the body at its yaw with must be among them."""
    d, _b, _c = _fx_block(fx)
    pe = d["pad_extent"]
    zones = FL.zones_for(pe["x"], pe["z"], pe["w"], pe["d"])
    tc = SF.tcdata
    surface = SF.EngineSurface("fixture", zones={
        z: np.zeros((tc.PITCH, tc.PITCH)) for z in zones})
    d, blk, _c = _fx_block(fx, surface=surface)
    place = dict(fx["place"], solved=blk)
    w, dd = FL.pad_extent(place)
    zs = FL.zones_for(float(blk["x"]), float(blk["z"]), w, dd)
    comps: dict = {}
    st = FL.build(place, {f"z_{a}_{b}": surface.patch((a, b)) for a, b in zs},
                  zs, "paved_cleared", 0.0, comps, {})
    out = []
    if d["flatten_cost"]["samples"] != st["samples"]:
        out.append(f"the bill names {d['flatten_cost']['samples']} samples, "
                   f"the writer stamped {st['samples']}")
    written = {tc.sample_world(*comp.centre, i % tc.PITCH, i // tc.PITCH)
               for comp in comps.values()
               for i, on in enumerate(comp.modified_height) if on}
    bw, bd = FL._BODY_EXTENTS[(fx["place"]["blueprint"],
                               float(fx["place"]["rotation"]["yaw"]))]["at_yaw"]
    x, z = float(blk["x"]), float(blk["z"])
    under = {(float(a), float(b))
             for a in FL.answering_vertices(x - bw / 2, x + bw / 2)
             for b in FL.answering_vertices(z - bd / 2, z + bd / 2)}
    missing = under - {(float(a), float(b)) for a, b in written}
    if missing or not under:
        out.append(f"{len(missing)} of {len(under)} vertices under the body "
                   f"at its yaw are not stamped")
    return out


# ---------------------------------------------------------------------------
# LEVELLABLE_PAD_DEPENDS_ON on CONSTRUCTED GROUND: one body (`_FX_YAW0`, 20.06 x
# 24.0 m at yaw 0) whose requirement declares a 70 m pad, on four sites 300 m
# apart, 1 m lattice, billed by the engine's own bill (`written_bill`):
#   bowl    a 32 m flat floor inside walls that climb 1 m per metre.  The 70 m
#           pad is cut 10.0 / fill 9.0 m over 1,716 samples past the 8 m clamp
#           -- Fresh42's iron-era-workshop in miniature -- and the body's own
#           ground is flat.
#   gentle  0.02 m per metre: the 70 m pad levels.
#   cliff   a 20 m step through the body's centre: no pad that covers the
#           body levels.
#   ledge   a 17 m step through it: no pad levels, and it is the less bad.
# ---------------------------------------------------------------------------
_LP_SITES = {"bowl": (104.0, 362.0), "gentle": (404.0, 362.0),
             "cliff": (704.0, 362.0), "ledge": (1004.0, 362.0)}


def _lp_height(site: str, X, Z):
    cx, cz = _LP_SITES[site]
    if site == "bowl":
        return 50.0 + np.maximum(0.0, np.maximum(np.abs(X - cx),
                                                 np.abs(Z - cz)) - 16.0)
    if site == "gentle":
        return 50.0 + 0.02 * (X - cx)
    return 50.0 + (20.0 if site == "cliff" else 17.0) * (X >= cx)


def _lp_solve(sites: list[tuple[str, float]]):
    """`(pool ranked, ladder rows, place, requirement)` for candidates on
    `sites` (`(name, score)`), through the functions pass 3 calls:
    `level_pool` and `rank_pool`."""
    fx = _FX_YAW0
    _fx_block(fx)                       # registers the body's measured extents
    place, req = dict(fx["place"]), dict(_FX_REQ, footprint_m=70)
    tc = SF.tcdata
    off = (np.arange(tc.PITCH) - tc.PITCH // 2) * tc.SCALE
    zones = {}
    for site, (cx, cz) in _LP_SITES.items():
        for zx, zz in FL.zones_for(cx, cz, 76.0, 76.0):
            zcx, zcz = tc.zone_centre(zx, zz)
            zones[(zx, zz)] = _lp_height(
                site, zcx + off[None, :] + 0.0 * off[:, None],
                zcz + off[:, None] + 0.0 * off[None, :])
    pool = [dict(x=_LP_SITES[s][0], z=_LP_SITES[s][1], y=0.0, flat=0.0,
                 biome_purity=1.0, water_dist=1e3, anchor_dist=0.0,
                 location_dist=1e3, score=score,
                 footprint_orientation=pad_text(70.0, 70.0),
                 _yaw={"yaw": 0.0}) for s, score in sites]
    ladder = level_pool(pool, place, req, lambda d: {"unread": "no world"},
                        SF.EngineSurface("fixture", zones=zones))
    rank_pool(pool, req)
    return pool, ladder, place, req



def selftest() -> int:
    """The river probe seeds from a world hash of EITHER sign.

    This check exists because nothing here had ever been run on a seed whose
    `GetStableHashCode` is negative, and half of all seeds are. It is stated
    NEUTER FIRST: the shipped expression on the state that broke it, which must
    go red before the repaired one is allowed to go green.
    """
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    # data_entry.stable_hash of each seed string; the grid header carries the
    # same signed int32 the game computed.
    fresh42, pirate68 = -1894280788, 147627509

    print("case: a seed whose world hash is NEGATIVE (Fresh42)")
    try:
        np.random.default_rng(fresh42)
        neuter = "accepted"
    except ValueError as exc:
        neuter = f"ValueError: {exc}"
    want("NEUTER: the shipped seeding (must go red)",
         neuter, "ValueError: expected non-negative integer")
    print("   ^ solve_placements.py:373 raised exactly this on Fresh42, so the "
          "river check never ran and NO fresh seed with a negative hash could "
          "be solved at all. 50,098 of 100,000 random alphanumeric seeds hash "
          "negative; Pirate68 is one of the other half, which is the only "
          "reason 13 placements ever solved.")
    want("probe_seed draws the full probe set",
         len(np.random.default_rng(probe_seed(fresh42)).choice(
             50000, RIVER_PROBES, replace=False)), RIVER_PROBES)

    print("case: a seed whose world hash is POSITIVE (Pirate68) is UNCHANGED")
    want("probe_seed is the identity below 2^31", probe_seed(pirate68), pirate68)
    want("identical probe points",
         np.array_equal(
             np.random.default_rng(pirate68).choice(50000, RIVER_PROBES, replace=False),
             np.random.default_rng(probe_seed(pirate68)).choice(
                 50000, RIVER_PROBES, replace=False)),
         True)

    # The 1 m window must cover the square `refine` slides inside it. A patch
    # is `2*half` metres at 1 m, so it carries `int(2*half)` samples and
    # `site_finder.refine` needs `footprint_m < n`. `window_ok` asks the LIVE
    # builder, through the same call `build_patch_request` makes, so a future
    # re-divergence fails here instead of 127 seconds into a scan.
    def window_ok(req):
        (_k, _x, _z, half, _s), = SF.patch_request([{"x": 0.0, "z": 0.0}], req, "probe")
        return int(2.0 * half) > int(round(float(SF.requirement_defaults(req)["footprint_m"])))

    def shipped_window_ok(req):
        p = SF.requirement_defaults(req)
        half = max(SF.footprint_extent(p)) * 0.5 + SF.PATCH_PAD
        return int(2.0 * half) > int(round(float(p["footprint_m"])))

    # Verbatim from worlds/Ulfsland/*/placements.yaml, both with a `solved`
    # block at flat_resolution_m 1.0 -- i.e. both refined once, before the
    # requirement widened underneath them.
    widened = [
        ("pre-queen/mistlands-blackforge-base",
         {"biome": "Mistlands", "max_flat_m": 3.5,
          "footprint_m": 80, "footprint_xz_m": [17.5, 10.9]}),
        ("deepnorth-sandbox/complete-station-hub",
         {"biome": "DeepNorth", "max_flat_m": 3.0,
          "footprint_m": 56, "footprint_xz_m": [20.06, 24.0]}),
        # a portal: the rectangle is the SMALLER number here, which is the
        # shape the shipped expression was written for and still handles.
        ("pre-kall/deepnorth-landing-portal",
         {"biome": "DeepNorth", "max_flat_m": 2.5,
          "footprint_m": 32, "footprint_xz_m": [3.9, 6.5]}),
    ]
    print("case: the 1 m window must cover the square `refine` slides in it")
    want("NEUTER: the shipped half, rectangle only (must go red)",
         [k for k, r in widened if not shipped_window_ok(r)],
         ["pre-queen/mistlands-blackforge-base",
          "deepnorth-sandbox/complete-station-hub"])
    print("   ^ 2 of Fresh42's 13 placements: an 80 m square slid inside a "
          "50-sample window and a 56 m square inside 56. ValueError "
          "'patch smaller than the footprint', site_finder.py:1264, after a "
          "127 s scan -- the failure is seed-INDEPENDENT and only a re-solve "
          "can reach it.")
    want("site_finder.patch_request covers every one of them",
         [k for k, r in widened if not window_ok(r)], [])
    # ---- SEPARATION_SCOPE_DEPENDS_ON: which placements stand in one save --
    def neutered(name, value, fn):
        g_ = globals()
        was = g_[name]
        g_[name] = value
        try:
            return fn()
        finally:
            g_[name] = was

    def row(key, x, z, w, d, y, dup=None):
        return {"key": key, "preset": key.split("#")[0], "x": x, "z": z,
                "w": w, "d": d, "y": y, "duplicate_of": dup}

    # Verbatim: worlds/Fresh42/*/placements.yaml as stage 7 wrote them
    # 2026-09-24 21:51 (x, z, flatten.pad_extent, solved y).
    fresh42 = [
        row("deepnorth-sandbox#complete-station-hub", 104.5, 362.5, 20.1, 24.0, 81.14),
        row("deepnorth-sandbox#sandbox-portal-hub", -220.5, -411.5, 34.1, 34.1, 38.54),
        row("deepnorth-sandbox#sandbox-harbour", -209.5, -413.5, 42.8, 35.8, 37.14,
            dup="pre-elder#early-dock"),
        row("pre-eikthyr#meadows-starter-hall", -211.5, -417.5, 17.4, 17.5, 34.23),
        row("pre-elder#early-dock", -209.5, -413.5, 42.8, 35.8, 37.14),
    ]
    # Verbatim: worlds/Ulfsland/*/placements.yaml -- the harbour and the dock
    # are ONE building (duplicate_of), the hub and the hall stand apart.
    ulfsland = [
        row("deepnorth-sandbox#sandbox-portal-hub", -292.5, 213.5, 34.1, 34.1, 40.6),
        row("deepnorth-sandbox#sandbox-harbour", 675.4, -85.6, 29.0, 42.2, 32.63,
            dup="pre-elder#early-dock"),
        row("pre-eikthyr#meadows-starter-hall", 303.5, 385.5, 17.4, 17.5, 84.28),
        row("pre-elder#early-dock", 675.4, -85.6, 35.8, 42.8, 32.63),
    ]

    def pairs(rows):
        return sorted(tuple(sorted((o["a"].split("#")[1], o["b"].split("#")[1])))
                      for o in placement_overlaps(rows))

    bad3 = [("early-dock", "meadows-starter-hall"),
            ("early-dock", "sandbox-portal-hub"),
            ("meadows-starter-hall", "sandbox-portal-hub")]
    print("case: Fresh42's stage-7 solve stacks THREE bodies on one meadow "
          "(REQUIRED TO REFUSE)")
    want("the three built pads that share ground are named", pairs(fresh42), bad3)
    want("NEUTER scope=preset (the shipped key): the stack goes UNSEEN",
         neutered("_SEPARATION_SCOPE", "preset", lambda: pairs(fresh42)), [])
    print("   ^ every one of the three pairs crosses a preset boundary, so a "
          "check keyed on the preset file accepts the stack it exists to "
          "refuse.")
    print("case: Ulfsland's harbour and dock are ONE building (REQUIRED TO "
          "ACCEPT)")
    want("a declared duplicate is not a second pad", pairs(ulfsland), [])
    want("NEUTER duplicates ignored: the one building REFUSES itself",
         neutered("_OVERLAP_DUPLICATES", "ignored", lambda: pairs(ulfsland)),
         [("early-dock", "sandbox-harbour")])

    # The SOLVER'S own exclusion, as main() keys it: sites taken in file
    # order, the dock asked about after the hub was taken.
    hub_path = Path("worlds/Fresh42/deepnorth-sandbox/placements.yaml")
    dock_path = Path("worlds/Fresh42/pre-elder/placements.yaml")
    dock = {"x": -209.5, "z": -413.5}
    far = {"x": 104.5, "z": 362.5}

    def dock_free():
        taken_: dict = {}
        taken_.setdefault(separation_bucket(hub_path), []).append(
            (-220.5, -411.5, 50 * 1.5))
        here_ = taken_.get(separation_bucket(dock_path), [])
        return len(separation_free([dock], here_, 48.0))

    print("case: pass 3 asked for the dock after the hub was taken "
          "(REQUIRED TO REFUSE the hub's ground)")
    want("the dock's candidate on the hub is NOT free", dock_free(), 0)
    want("NEUTER scope=preset: the hub's ground reads free to the dock",
         neutered("_SEPARATION_SCOPE", "preset", dock_free), 1)
    hub_taken = [(-220.5, -411.5, 75.0)]
    got = separation_breach(dock, hub_taken, 48.0)
    print("case: no candidate clear -> the pick is RECORDED as a breach")
    want("the breach is recorded in metres",
         None if got is None else (got["constraint"], got["actual"],
                                   got["over_by"]),
         ("separation_m", 11.18, 63.82))
    want("NEUTER crowded=silent (the shipped `free or pool`): no record",
         neutered("_CROWDED", "silent",
                  lambda: separation_breach(dock, hub_taken, 48.0)), None)
    want("a site 1,040 m from the hub records no breach",
         separation_breach(far, hub_taken, 48.0), None)

    # ---- PAD_EXTENT_DEPENDS_ON (terraform/flatten.py), PRESET PATH -------
    print("case: an installation's block pad covers its body AT ITS YAW "
          "(REQUIRED TO ACCEPT)")
    _d, blk, cov = _fx_block(_FX_OUTPOST)
    want("mountain-outpost, yaw 180, searched on the SWAPPED rectangle",
         (cov["verdict"], cov["unstamped"]), ("covered", 0))
    _d, blk, cov = _fx_block(_FX_HUB)
    want("sandbox-portal-hub, yaw 135", (cov["verdict"], cov["unstamped"]),
         ("covered", 0))
    want("the bill names what the writer stamps, and that is every vertex "
         "under the body", _fx_bill_is_write(_FX_OUTPOST), [])
    print("case: NEUTER drop body_footprint -- the searched rectangle alone, "
          "the shipped key (REQUIRED TO REFUSE)")
    _d, _b, cov = _fx_block(_FX_OUTPOST, _fx_without("body_footprint"))
    want("mountain-outpost's live step-2 refusal RETURNS",
         (cov["verdict"], cov["unstamped"], cov["vertices_under_body"],
          cov["overhang_z_m"]), ("overhangs", 308, 748, 6.588))
    print("case: NEUTER drop body_yaw on the yaw-135 hub (REQUIRED TO REFUSE)")
    _d, _b, cov = _fx_block(_FX_HUB, _fx_without("body_yaw"))
    want("sandbox-portal-hub's live step-2 refusal RETURNS",
         (cov["verdict"], cov["unstamped"], cov["vertices_under_body"]),
         ("overhangs", 140, 1296))
    print("case: body_yaw dropped at yaw 180 SURVIVES -- the body turned half "
          "round spans what it spans unturned (a verdict)")
    _d, full, _c = _fx_block(_FX_OUTPOST)
    _d, bare, _c = _fx_block(_FX_OUTPOST, _fx_without("body_yaw"))
    want("mountain-outpost's pad with and without body_yaw",
         FL._identical(FL.pad_extent({"solved": bare}),
                       FL.pad_extent({"solved": full})), "identical")
    print("case: a yaw-0 installation SURVIVES byte-identical (a verdict)")
    for label, terms in (("every term", FL.PAD_EXTENT_DEPENDS_ON),
                         ("body_yaw dropped", _fx_without("body_yaw"))):
        _d, blk, _c = _fx_block(_FX_YAW0, terms)
        want(f"yaw 0, {label}: the block's footprint_orientation",
             "identical" if blk["footprint_orientation"]
             == _FX_YAW0["cand"]["footprint_orientation"] else
             f"differs: {blk['footprint_orientation']!r}", "identical")
    print("case: a body whose yaw is UNREAD gets no pad (REQUIRED TO REFUSE)")
    try:
        written_pad(dict(_FX_OUTPOST["place"], rotation={}),
                    dict(_FX_OUTPOST["cand"]))
        unread = "sized a pad"
    except ValueError:
        unread = "refused"
    want("mountain-outpost with no rotation.yaw", unread, "refused")

    # ---- fixture_ring (terraform/flatten.py): the pad is sized for what
    # stands on it, and a ring pad the clamp or a neighbour forbids is
    # DROPPED, never the site.  mountain-outpost's ring pad at its drive-2
    # target is 7 samples over the clamp (cut 8.73 m, MEASURED offline).
    print("case: a ring pad beyond the clamp or a neighbour is forbidden, "
          "not the site (REQUIRED TO REFUSE)")
    fx = _FX_OUTPOST
    pe = dict(written_pad(fx["place"], dict(fx["cand"])))
    cand = dict(fx["cand"], location_dist=500.0, y=0.0)
    want("a clear bill, no neighbour: the ring pad stands",
         ring_forbidden(fx["place"], cand, pe, {"verdict": "clear"}), None)
    got = ring_forbidden(fx["place"], cand, pe, {
        "verdict": "refused", "over_clamp": 7, "max_cut_m": 8.73,
        "max_fill_m": 2.65})
    want("the ring pad 7 samples over the clamp",
         got and got["forbidden_by"], "the engine clamp")
    got = ring_forbidden(fx["place"], dict(cand, location_dist=20.0), pe,
                         {"verdict": "clear"})
    want("the ring pad's clearing cylinder reaching a location 20 m out",
         got and got["forbidden_by"], "a neighbour")

    # ---- NETWORK_LAND_DEPENDS_ON: a waterfront the roads serve --------------
    # A constructed 1 m field in drive-5 Fresh42's geometry around pre-elder#
    # early-dock: sea 6 m deep (24.0) under the whole drive-5 pad (52.75 x
    # 36.75 m at (-7.5, -416.5)); the network's land -- holding the portal hub
    # (-220.5, -411.5) and the StartTemple (-6.1, 3.2) -- from x <= -80 and
    # from z >= -326.5; a 27 m islet whose near shore is 56 m east of the pad
    # centre.  Plus Ulfsland's shape: a sandbar one wet column off the
    # network's coast, which the 4-connected labelling makes its own component.
    W = _crossings_survey().W
    half, n = 480.0, 960
    fld = W.Field("fixture", -7.5, -216.5, half, 1.0, n,
                  np.full((n, n), 24.0, dtype=np.float32),
                  np.zeros((n, n), dtype=np.uint8))
    xs = fld.x0 + np.arange(n)
    zs = fld.z0 + np.arange(n)
    X, Z = np.meshgrid(xs, zs)
    fld.heights[(X <= -80.0) | (Z >= -326.5)] = 35.0
    fld.heights[np.hypot(X - 75.5, Z + 416.5) <= 27.0] = 35.0
    fld.heights[(X >= -78.0) & (X <= -62.0) & (Z >= -520.0) & (Z <= -480.0)] = 35.0
    temple, hub = (-6.1, 3.2), (-220.5, -411.5)
    dock = {"x": -7.5, "z": -416.5, "anchor_xz": temple}
    shore = {"x": -70.0, "z": -500.0, "anchor_xz": temple}
    place = {"id": "early-dock", "requirement": {"coastal": True}}
    drive5_pad = lambda _p, _d: (52.75, 36.75)  # noqa: E731
    net = network_land({"fixture": fld}, hub)

    def pick(pool):
        """main()'s pass-3 choice, first-ranked first: (x, z, refused)."""
        reach_of = {}
        if waterfront_gated(place, place["requirement"]):
            pool, reach_of = network_served(pool, net, place, drive5_pad)
        v = reach_of.get(id(pool[0]))
        return (pool[0]["x"], pool[0]["z"],
                v is not None and v["verdict"] != "on_network")

    print("case: early-dock's drive-5 spot, a pad over open water "
          "(REQUIRED TO REFUSE)")
    got = waterfront_reach(net, dock, (52.75, 36.75))
    want("the pad's samples on the network's land, the node's snap, the "
         "network's distance",
         (got["verdict"], got["on_network_samples"], got["apron_samples"],
          got["node_snap_component"] != net["component"], got["node_snap_m"],
          got["network_land_m"]),
         # drive 5 MEASURED 56.0 m to the islet and 72.4 m to component 128;
         # this lattice puts the network's first sample at 73.0.
         ("off_network", 0, 0, True, 56.0, 73.0))
    want("with a shore on the network's land ranked second, the shore is "
         "chosen", pick([dict(dock), dict(shore)]), (-70.0, -500.0, False))
    want("with none, the drive-5 spot is recorded REFUSED",
         pick([dict(dock)]), (-7.5, -416.5, True))
    want("the refusal is recorded in metres",
         network_land_violation(got)["over_by"], got["network_land_m"])
    want("NEUTER unchecked: the drive-5 spot is ACCEPTED over the shore",
         neutered("_WATERFRONT_REACH", "unchecked",
                  lambda: pick([dict(dock), dict(shore)])),
         (-7.5, -416.5, False))
    print("case: a shore whose node snaps to a sandbar off the network's "
          "coast, Ulfsland's dock (REQUIRED TO ACCEPT)")
    got = waterfront_reach(net, shore, (52.75, 36.75))
    want("its landward apron is on the network's land",
         (got["verdict"], got["node_snap_component"] != net["component"]),
         ("on_network", True))
    print("case: a waterfront anchored off the network's land, a Deep North "
          "landing (NOT JUDGED)")
    want("an islet anchor", waterfront_reach(
        net, dict(dock, anchor_xz=(75.5, -416.5)), (52.75, 36.75)), None)
    print("case: UNREAD IS NOT CLEAR (REQUIRED TO REFUSE)")
    got = waterfront_reach(network_land(None, hub), dock, (52.75, 36.75))
    want("no field", (got["verdict"],
                      network_land_violation(got)["actual"]),
         ("unread", "unread"))
    got = waterfront_reach(network_land({"fixture": fld}, None), dock,
                           (52.75, 36.75))
    want("no root solved", got["verdict"], "unread")
    print("case: the coarse mask holds the shore and not the drive-5 spot")
    g_fx = {"step": 8, "n": 256, "extent": 1024}
    dock_req = {"biome": "Meadows", "max_flat_m": 2.5, "footprint_m": 48,
                "coastal": True, "footprint_xz_m": [35.8, 42.8]}
    plane = network_plane(g_fx, net, dock_req)

    def at(x, z):
        return bool(plane[int((z + 1024) // 8), int((x + 1024) // 8)])

    want("(drive-5 spot, shore)", (at(-7.5, -416.5), at(-70.0, -500.0)),
         (False, True))

    # ---- WATERFRONT_YAW_DEPENDS_ON: a waterfront's yaw is solved ------------
    # A constructed 8 m grid: open water (25.0) west of x = -615, land (35.0)
    # east of it -- the shape of early-dock's scratch site (-607.5, 178.5),
    # whose sea is to the west (MEASURED there: 0.24 at the inherited 90,
    # 0.90 at 270).
    gy = {"step": 8, "n": 256, "extent": 1024,
          "biome": np.zeros((256, 256), dtype=np.uint8)}
    cx_ = SF.axis(256, 8, 1024)
    gy["height"] = np.where(cx_[None, :] < -615.0, 25.0, 35.0) * np.ones((256, 1))
    dock_req = dict(dock_req)
    print("case: early-dock at its inherited yaw 90 on the scratch site, the "
          "jetty over land (REQUIRED TO REFUSE the inherited yaw)")
    got = candidate_yaw(gy, dock_req, -607.5, 178.5, 90.0)
    want("the yaw written, and the facing it is chosen on",
         (got["yaw"], got["inherited"], got["by_cardinal_yaw"][90],
          got["by_cardinal_yaw"][270]), (270.0, 90.0, 0.0, 0.98))
    want("NEUTER inherited: yaw 90 is written over land",
         neutered("_WATERFRONT_YAW", "inherited",
                  lambda: candidate_yaw(gy, dock_req, -607.5, 178.5, 90.0)),
         {"yaw": 90.0})
    print("case: an inherited yaw that already faces water is KEPT "
          "(Pirate68's dock, 0.92 at 90 against 0.96 at 180)")
    want("yaw 270 on the scratch site",
         candidate_yaw(gy, dock_req, -607.5, 178.5, 270.0), {"yaw": 270.0})
    print("case: no cardinal yaw finds water (REQUIRED TO REFUSE)")
    got = candidate_yaw(gy, dock_req, 0.0, 0.0, 90.0)
    want("an inland site", (got["yaw"], got["refused"]["constraint"]),
         (90.0, "facing_water"))
    want("an inland NON-waterfront is never judged",
         candidate_yaw(gy, {"biome": "Meadows", "max_flat_m": 2.5}, 0.0, 0.0,
                       90.0), {"yaw": 90.0})
    print("case: the pad follows the solved yaw (flatten.placed_pad_at_yaw)")
    p180 = _fx_block(_FX_OUTPOST)[0]["pad_extent"]
    p90 = _fx_block(dict(_FX_OUTPOST, place=at_yaw(_FX_OUTPOST["place"],
                                                   90.0)))[0]["pad_extent"]
    want("mountain-outpost at yaw 180 and turned to 90: (yaw_deg, body at yaw)",
         ((p180["yaw_deg"], list(p180["body_at_yaw"])),
          (p90["yaw_deg"], list(p90["body_at_yaw"]))),
         ((180.0, list(p180["body_at_yaw"])),
          (90.0, list(reversed(p180["body_at_yaw"])))))

    # ---- CLEARING_ROOM_DEPENDS_ON: the ONE clearing rule --------------------
    # A CONSTRUCTED dump: Crypt4, at Fresh42's own measured type numbers, in
    # the zone east of mountain-outpost's drive-2 candidate -- 51.5 m away, so
    # its pieces (reach 21 m) leave the cylinder room, while its interior's
    # zone edge is 7.5 m from the pad centre.
    print("case: a step-1 cylinder reaching a crypt's interior zone "
          "(REQUIRED TO REFUSE)")
    import tempfile  # noqa: PLC0415
    g = globals()
    was_loc, was_dep = g["CLEARING_LOCATIONS"], g["CLEARING_ROOM_DEPENDS_ON"]
    with tempfile.TemporaryDirectory() as td:
        dump = Path(td) / "loc.json"
        dump.write_text(json.dumps({
            "seed": "fixture",
            "locations": [{"name": "Crypt4", "prefab": "Crypt4",
                           "x": -7540.0, "z": 289.5, "exteriorRadius": 18,
                           "interiorRadius": 21, "quantity": 200}],
            "locationTypes": [{"prefab": "Crypt4", "name": "Crypt4",
                               "exteriorRadius": 18, "interiorRadius": 21,
                               "znviewReachM": 18.44}]}))
        g["CLEARING_LOCATIONS"] = str(dump)
        try:
            got = clearing_room_refusal(fx["place"], cand, pe)
            want("the body pad's cylinder: refused on Crypt4's interior",
                 got and (got["constraint"], got["location"],
                          got["population"]),
                 ("clearing_room_m", "Crypt4", "interior_zone"))
            got = ring_forbidden(fx["place"], cand, pe, {"verdict": "clear"})
            want("and a ring pad there is dropped as a neighbour's",
                 got and got["forbidden_by"], "a neighbour")
            g["CLEARING_ROOM_DEPENDS_ON"] = ("pieces",)
            want("NEUTER pieces only (must go red: the interior is missed)",
                 clearing_room_refusal(fx["place"], cand, pe), None)
        finally:
            g["CLEARING_LOCATIONS"] = was_loc
            g["CLEARING_ROOM_DEPENDS_ON"] = was_dep
    want("discriminator restored: no dump bound, the rule is not asked",
         clearing_room_refusal(fx["place"], cand, pe), None)

    # ---- LEVELLABLE_PAD_DEPENDS_ON: a pad the engine cannot level ------------
    # Fresh42 drive 11: pre-bonemass#iron-era-workshop declared a 70 m pad, all
    # 13 candidates were past the +/-8 m clamp on it (the chosen one cut 10.54
    # m / fill 9.569 m over 442 samples), and the site was recorded REFUSED,
    # step 1 refused the unit.  The same centre levels a 50 m pad.
    def lp_at(sites):
        pool, ladder, place_, req_ = _lp_solve(sites)
        b = pool[0]
        return pool, ladder, place_, req_, {
            "site": next(k for k, v in _LP_SITES.items()
                         if (b["x"], b["z"]) == v),
            "verdict": b["flatten_cost"]["verdict"],
            "pad": [b["pad_extent"]["w"], b["pad_extent"]["d"]],
            "rungs": len(ladder)}

    print("case: no candidate's DECLARED 70 m pad is within the clamp and "
          "the body's own ground is flat (REQUIRED TO LEVEL A SMALLER PAD)")
    pool_, ladder_, place_, req_, got = lp_at([("bowl", 1.0)])
    want("the pad written: the largest the pool levels",
         got, {"site": "bowl", "verdict": "clear", "pad": [55.0, 55.0],
               "rungs": 4})
    want("the declared pad's own numbers are kept in the ladder",
         ladder_[0], {"rung": "declared_pad", "pad": [70.0, 70.0],
                      "candidates": 1, "levellable": 0,
                      "least_over_clamp": {"max_cut_m": 10.0,
                                           "max_fill_m": 9.0,
                                           "over_clamp_samples": 1716}})
    blk_ = site_block(pool_[0], req_, "", {})
    cov_ = FL.placed_body_cover(dict(place_, solved=blk_))
    want("the block's pad is the one the engine levels and it still stamps "
         "every vertex under the body at its yaw",
         (blk_["flatten_cost"]["verdict"], blk_["footprint_orientation"],
          blk_["pad_extent"]["floor"], blk_["pad_extent"]["grew"],
          cov_["verdict"], cov_["unstamped"]),
         ("clear", "55.0 m along x by 55.0 m along z", [55.0, 55.0], False,
          "covered", 0))
    want("NEUTER the ladder off (the shipped key): the drive-11 refusal "
         "RETURNS, the declared 70 m pad written beyond the clamp",
         neutered("LEVELLABLE_PAD_DEPENDS_ON", ("declared_pad",),
                  lambda: lp_at([("bowl", 1.0)])[4]),
         {"site": "bowl", "verdict": "refused", "pad": [70.0, 70.0],
          "rungs": 1})
    want("NEUTER the yard steps off: the yard is thrown away whole, the pad "
         "the body and its ring need and no larger",
         neutered("LEVELLABLE_PAD_DEPENDS_ON", ("declared_pad",
                                                "body_and_ring"),
                  lambda: lp_at([("bowl", 1.0)])[4]),
         {"site": "bowl", "verdict": "clear", "pad": [19.8, 23.8],
          "rungs": 2})

    print("case: a candidate whose declared pad levels is UNTOUCHED, and the "
          "next-best legal spot beats a better-scored one past the clamp "
          "(REQUIRED TO ACCEPT)")
    pool_, ladder_, place_, req_, got = lp_at([("bowl", 1.0),
                                               ("gentle", 0.0)])
    blk_ = site_block(pool_[0], req_, "", {})
    want("the lower-scored candidate that levels its 70 m pad is chosen, on "
         "the declared pad, with no ladder in its block",
         (got, "floor" in blk_["pad_extent"], blk_["pad_extent"]["grew"]),
         ({"site": "gentle", "verdict": "clear", "pad": [70.0, 70.0],
           "rungs": 1}, False, False))
    want("NEUTER the verdict ignored (blind): the better-scored bowl is "
         "chosen and its 70 m pad written beyond the clamp",
         neutered("_CLAMP_RANK", "blind",
                  lambda: lp_at([("bowl", 1.0), ("gentle", 0.0)])[4]),
         {"site": "bowl", "verdict": "refused", "pad": [70.0, 70.0],
          "rungs": 1})
    want("NEUTER the floor always dropped: the declared pad is thrown away "
         "where it levelled",
         neutered("LEVELLABLE_PAD_DEPENDS_ON", ("body_and_ring",),
                  lambda: lp_at([("gentle", 0.0)])[4]),
         {"site": "gentle", "verdict": "clear", "pad": [19.8, 23.8],
          "rungs": 1})

    print("case: no pad levels on any rung (REQUIRED TO REFUSE, with the "
          "numbers, on the candidate LEAST over the clamp)")
    pool_, ladder_, place_, req_, got = lp_at([("cliff", 1.0),
                                               ("ledge", 0.0)])
    want("the site is the ledge (17 m past the clamp's datum), not the "
         "better-scored cliff (20 m), refused on the body's own pad",
         (got["site"], got["verdict"], got["pad"]),
         ("ledge", "refused", [19.8, 23.8]))
    want("every rung is recorded with the numbers of its least bad, down to "
         "the pad the body needs (the last pad that sizes differently)",
         ([r["rung"] for r in ladder_][0], ladder_[-1]["rung"],
          all(r["levellable"] == 0 and "least_over_clamp" in r
              for r in ladder_), ladder_[-1]["least_over_clamp"]),
         ("declared_pad", "declared_pad-55m", True,
          {"max_cut_m": 0.0, "max_fill_m": 17.0,
           "over_clamp_samples": 260}))
    want("the record says no pad levelled",
         pad_ladder_record(ladder_)["levellable"], False)
    want("NEUTER the excess ranking off (the shipped key): the pad "
         "recorded is the better-scored cliff's, 20 m past where the "
         "ledge is 17 m",
         neutered("_CLAMP_RANK", "verdict",
                  lambda: lp_at([("cliff", 1.0), ("ledge", 0.0)])[4]["site"]),
         "cliff")

    print("case: a RECORDED block past the clamp is audited by --verify "
          "(REQUIRED TO REFUSE)")
    drive11 = {"flatten_cost": {"verdict": "refused", "over_clamp": 442,
                                "max_cut_m": 10.54, "max_fill_m": 9.569}}
    want("Fresh42 drive 11's iron-era-workshop block as stage 7 recorded it",
         recorded_clamp_violation(drive11),
         {"constraint": "engine_clamp_m", "required": 8.0, "actual": 10.54,
          "over_by": 2.54})
    want("the same site on the pad the ladder writes (cut 7.869, fill 7.58, "
         "0 over)",
         recorded_clamp_violation({"flatten_cost": {
             "verdict": "clear", "over_clamp": 0, "max_cut_m": 7.869,
             "max_fill_m": 7.58}}), None)
    want("a block that records no bill is not judged",
         recorded_clamp_violation({}), None)
    ironreq = {"biome": "BlackForest", "footprint_m": 70, "max_flat_m": 3.5,
               "min_biome_purity": 0.5, "location_clearance_m": 60,
               "near": "Bonemass", "within_m": 1400, "near_biome": "Swamp",
               "within_biome_m": 450}
    ironsite = dict(biome_purity=1.0, anchor_dist=830.0, water_dist=80.4,
                    location_dist=118.0, freeboard_m=21.65,
                    near_biome_dist_m=334.1, flat=20.42)
    want("NEUTER the shipped audit (the requirement alone): `--verify` found "
         "nothing wrong with the block it should have refused",
         SF.violations(ironsite, ironreq), [])

    print(f"\n{fails} case(s) failed")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(selftest())
    raise SystemExit(main())
