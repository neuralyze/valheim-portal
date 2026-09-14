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


def coarse_pool(
    g, anchors, avoid_xz, req: dict, exclude, spawn_xz, declared: dict | None = None
) -> list[dict]:
    """Coarse candidates across every anchor instance, merged and suppressed."""
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
            ):
                d["anchor"] = inst["desc"]
                d["anchor_xz"] = inst["xz"]
                d["_swap"] = swap
                fx, fz = SF.footprint_extent(SF.requirement_defaults(variant))
                d["footprint_orientation"] = f"{fx:.1f} m along x by {fz:.1f} m along z"
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
    g, anchors, avoid_xz, req: dict, exclude, refine: bool, spawn_xz
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
    pool = coarse_pool(g, anchors, avoid_xz, req, exclude, spawn_xz, declared=req)
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
    pool = coarse_pool(g, anchors, avoid_xz, alt, exclude, spawn_xz, declared=req)
    if pool:
        # the 1 m pass has to gate on the requirement that was actually searched,
        # or its mask is empty and it cannot slide at all
        for d in pool:
            d["_req"] = alt
        return pool, record, None
    return [], None, (
        "no site found even inside the compromise bounds; the requirement is "
        "unreachable in this seed by a wide margin"
    )


RIVER_PROBES = 256
RIVER_TOLERANCE_M = 1.0
RIVER_MAX_BAD = 0.02


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
    rng = np.random.default_rng(g["hash"])
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
    rows: list[str] = []
    for job in jobs:
        for i, d in enumerate(job["pool"]):
            half = max(SF.footprint_extent(SF.requirement_defaults(job["req"]))) * 0.5 + SF.PATCH_PAD
            rows.append(f"{job['key']}#{i}\t{d['x']:.1f}\t{d['z']:.1f}\t{half:.1f}\t1.0")
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


def site_block(d: dict, req: dict, anchor_desc: str, routes: dict) -> dict:
    """The recorded form of one candidate site."""
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
        # which grid-aligned orientation of a rectangular footprint was assumed;
        # set the rotation yaw to match it
        "footprint_orientation": d.get("footprint_orientation", "square"),
        "nearest_location_m": round(d["location_dist"], 0),
        "score": round(d["score"], 3),
        "scores": d.get("scores", {}),
        "overland_from_spawn": bool(d.get("overland_from_spawn", True)),
        "satisfies_requirement": not v,
    }
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
            if not vs:
                print(f"{key:46} {'OK':22}")
            for v in vs:
                bad += 1
                print(
                    f"{key:46} {v['constraint']:22} {v['required']:10.2f} "
                    f"{v['actual']:10.2f} {v['over_by']:8.2f}"
                )
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

    g = SF.load_grid(Path(args.grid))
    if g["seed"] != args.seed:
        print(
            f"refusing to solve: --seed {args.seed!r} but the grid was generated for "
            f"{g['seed']!r}. Mismatched coordinates are worse than none.",
            file=sys.stderr,
        )
        return 2
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

    # ---- pass 1: coarse pools for every placement -------------------------
    docs: dict[Path, dict] = {}
    jobs: list[dict] = []
    failures = 0
    for path in files:
        doc = yaml.safe_load(path.read_text("utf-8"))
        docs[path] = doc
        preset = doc.get("preset", path.parent.name)
        # Placements within one preset coexist in the same world, so each
        # solved site becomes an exclusion zone for the next. Presets do NOT
        # exclude each other: they are alternative world states, never live
        # together, so two presets resolving to the same flat meadow is correct.
        exclude: list[tuple[float, float, float]] = []
        for p in doc.get("placements") or []:
            src = sources.get(p.get("blueprint", ""))
            if src is not None and src.exists():
                p["containers"] = container_census(src)
            req = p.get("requirement")
            if not req:
                continue
            pool, relaxed, err = solve_coarse(
                g, anchors, avoid_xz, req, exclude, refine, spawn_xz
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

    # ---- pass 3: verdict + write -----------------------------------------
    # Sites chosen so far within each preset file. The coarse pass already
    # spread the pools apart, but the 1 m pass moves sites by up to PATCH_PAD,
    # so the final, authoritative separation check happens here against the
    # coordinates that actually get written.
    taken: dict[Path, list[tuple[float, float, float]]] = {}
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
            free = [
                d
                for d in pool
                if all(
                    math.hypot(d["x"] - tx, d["z"] - tz) > max(tr, fp * 1.5)
                    for tx, tz, tr in taken.get(path, ())
                )
            ]
            pool = free or pool
            # rank: satisfying sites first, then by score
            for d in pool:
                d["_violations"] = SF.violations(d, req)
            # Satisfying sites first; among sites that cannot satisfy the
            # requirement, the one that breaks it by the LEAST, and only then by
            # score. Every miss is in metres, so summing them compares like with
            # like, and it makes the recorded `violations` the thing being
            # minimised rather than a footnote on a site chosen for other reasons.
            pool.sort(
                key=lambda d: (
                    len(d["_violations"]) > 0,
                    round(declared_miss(d, req), 1),
                    -d["score"],
                )
            )
            best = pool[0]
            taken.setdefault(path, []).append((best["x"], best["z"], fp * 1.5))
            anchor_desc = best.get("anchor", "")
            target = {"seed": args.seed}
            target.update(
                site_block(best, req, anchor_desc, routes_for(g, best, spawn_xz, best.get("anchor_xz")))
            )
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
            yaw = float((p.get("rotation") or {}).get("yaw", 0))
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
        if args.write:
            if args.label == "primary":
                p["solved"] = target
            else:
                p.setdefault("solved_alternatives", {})[args.seed] = target

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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
