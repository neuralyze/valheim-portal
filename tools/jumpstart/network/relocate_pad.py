#!/usr/bin/env python3
"""Move a solved pad the SHORTEST distance that clears the LIVE generated
locations, and re-check every hard constraint at the new centre.

WHY THIS EXISTS, AND WHY THE SOLVER CANNOT DO IT. `blueprints/solve_placements.py`
scores candidate sites against the ZoneSystem location dump produced by
`tools/seedscan/run_locscan.sh`, and that dump is MOD-FREE BY CONSTRUCTION:
`run_locscan.sh` rsyncs the server tree with `--exclude 'BepInEx/'`, then
`rm -rf $SANDBOX/BepInEx`, then copies back only `BepInEx/core` and builds its
own scanner into `plugins`. So `More_World_Locations_AIO`'s 190 POI locations
cannot register in it, and `solved.nearest_location_m` describes a world that is
not the one running. MEASURED at `pre-eikthyr#meadows-starter-hall`: recorded
77.0 m, live markers at 11.08, 25.41 and 77.44 m -- the solver found exactly the
vanilla one. Re-running the solver cannot fix that, and a mod's own location
quantity switches only apply at WORLD GENERATION, so no config change retracts a
marker from a zone this world has already generated either.

So the live world is the authority on locations, and it is asked over RCON with
`findObjects -prefab LocationProxy` -- one ZDO per generated location, prefab
filtered and radius bounded, which is the only shape of `findObjects` that is
safe (see `network/verify_site.py` for why an unfiltered one wedges the server).

WHAT IS AND IS NOT RE-CHECKED, and the difference matters:

  MEASURED HERE, per candidate, from the same 8 m biome+height grid the solver
  used (`tools/seedscan/run_scan.sh`, HEIGHT=1) --
      biome at the centre, biome purity over the pad rectangle, freeboard of
      the LOWEST cell of the rectangle over the y=30 water plane, height spread,
      anchor distance against `requirement.within_m`, and the distance to the
      nearest LIVE LocationProxy.
  MEASURED HERE, at 1 m, for the chosen candidate only --
      the earthwork bill, by delegating to `network/recost_pad.py`'s own `cost()`
      over PatchScan heights, which is the height source that agrees with the
      game.
  NOT re-checked --
      overland reachability from spawn and the route detour. Those are graph
      searches over the whole world and a local nudge of a few tens of metres
      cannot change which landmass a site is on. The tool refuses to move
      further than `--radius` precisely so that claim stays true, and prints the
      displacement so it can be judged.

THE SELF-CHECK IS NOT OPTIONAL. Before searching, the grid reader is asked for
the biome purity and freeboard AT THE SOLVED CENTRE with the solved rectangle,
and compared against `solved.biome_purity` and `solved.freeboard_m`. If the
reader disagrees with the solver on the site the solver actually measured, then
every candidate figure it produces is unfounded, so it refuses rather than
reporting. This module exists because a check that confidently answers a
question it is not measuring is the defect that cost this world its evening.

THE RADIUS THAT MATTERS IS THE ONE THAT GETS CLEARED, not the pad. Three
numbers have to fit between the building and the nearest marker:

    clear radius   = hypot(pad_w + 2*apron, pad_d + 2*apron)/2 + margin
                     -- `clearing/area.py`, the cylinder `objects_remove` takes
    verify radius  = collider reach after yaw + 2.0 m
                     -- `network/verify_site.py`, the circle whose object count
                        must equal the plan's command count. Anything the
                        clearing left standing inside it is counted as a stray,
                        so the clear radius MUST cover it.
    marker distance
                     -- must exceed the clear radius, or `clearing/clear.py`
                        refuses (correctly: half a deleted location is worse
                        than a refusal).

So this tool solves for the apron that makes the clear radius cover the verify
radius, and then requires `marker > clear + --safety`. It prints the apron to
pass to `network/build_site.py`; passing a different one invalidates the search.

Usage:
    network/relocate_pad.py --preset pre-eikthyr --id meadows-starter-hall
    network/relocate_pad.py --preset pre-eikthyr --id meadows-starter-hall --write
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "clearing"))
sys.path.insert(0, str(JUMPSTART / "blueprints"))

import site_finder as SF  # noqa: E402
import tcdata  # noqa: E402
import flatten as flattenmod  # noqa: E402
import recost_pad  # noqa: E402
from rcon import Rcon, container_ip, rcon_credentials  # noqa: E402
import clear as clearing  # noqa: E402

GRID = Path("/tmp/fb/grid/00000.biome")
ANCHOR_RE = re.compile(r"@\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
FOOTPRINT_RE = re.compile(r"footprint@yaw[-\d.]+=([\d.]+)x([\d.]+)")


def load_place(world: str, preset: str, pid: str) -> tuple[Path, dict, dict]:
    path = JUMPSTART / "worlds" / world / preset / "placements.yaml"
    doc = yaml.safe_load(path.read_text())
    place = next((p for p in doc["placements"] if p["id"] == pid), None)
    if place is None:
        raise SystemExit(f"no placement {pid} in {path}")
    return path, doc, place


def pad_rect(place: dict) -> tuple[float, float, str]:
    """The rectangle to level, in WORLD axes, and where the shape came from.

    `requirement.footprint_xz_m` is the declared world-axis rectangle and wins
    when present -- that is how a pad is shrunk from the requirement's square to
    the body's own shape. Without it the pad is the square `footprint_m`, which
    is what nine of this world's thirteen placements still ask for.
    """
    req = place["requirement"]
    rect = req.get("footprint_xz_m")
    if rect:
        return float(rect[0]), float(rect[1]), "requirement.footprint_xz_m"
    side = float(req["footprint_m"])
    return side, side, "square requirement.footprint_m"


def collider_reach(index: Path, preset: str, pid: str) -> tuple[float, str]:
    """Half-diagonal of the body's COLLIDER box after its yaw, from the plan index.

    The manifest's pivot box understates it and a rotated pivot box overstates
    it: MEASURED, `salty-dick-portal-hub-final` at yaw 135 is 28.2 x 28.1 m by
    pivot, whose rotated axis-aligned envelope is 39.8 x 39.8 m, while the
    measured collider corners after the same yaw are 34.11 x 34.08 m. Only the
    measured one describes the building.
    """
    rows = {f"{r['preset']}#{r['id']}": r for r in json.loads(index.read_text())}
    row = rows.get(f"{preset}#{pid}")
    if row is None or row.get("status") != "ok":
        raise SystemExit(f"{preset}#{pid} has no ok plan in {index}; run terraform/sites.py first")
    m = FOOTPRINT_RE.search(row.get("notes", ""))
    if not m:
        raise SystemExit(f"{preset}#{pid}'s plan notes carry no footprint@yaw; cannot size the "
                         f"verification radius from the pivot box without overstating it")
    w, d = float(m.group(1)), float(m.group(2))
    return math.hypot(w / 2, d / 2), f"collider box after yaw {w}x{d} m"


def apron_for(pad_w: float, pad_d: float, margin: float, verify_r: float,
              floor: float, ceiling: float = 24.0) -> tuple[float, float]:
    """Smallest apron >= `floor` whose clearing radius covers `verify_r`.

    Stepped at 0.05 m rather than solved in closed form so the returned apron is
    a number that can be typed into `build_site.py --apron` and reproduce this
    radius exactly, instead of a float that rounds to a different cylinder.
    """
    a = floor
    while a <= ceiling:
        r = math.hypot(pad_w + 2 * a, pad_d + 2 * a) / 2 + margin
        if r >= verify_r:
            return round(a, 2), r
        a = round(a + 0.05, 2)
    raise SystemExit(f"no apron up to {ceiling} m makes the clearing cover the {verify_r:.2f} m "
                     f"verification radius on a {pad_w} x {pad_d} m pad")


class Grid:
    """The 8 m biome + height grid, read the same way `site_finder` reads it."""

    def __init__(self, path: Path):
        if not path.exists():
            raise SystemExit(f"no grid at {path}; regenerate with tools/seedscan/run_scan.sh "
                             f"STEP=8 HEIGHT=1")
        self.g = SF.load_grid(path)
        self.step = float(self.g["step"])
        self.extent = float(self.g["extent"])
        self.n = int(self.g["n"])
        self.biome = self.g["biome"]
        self.height = self.g["height"]

    def index(self, v: float) -> int:
        return int(round((v + self.extent - self.step * 0.5) / self.step))

    def window(self, cx: float, cz: float, w: float, d: float):
        """The cells `site_finder` would score this rectangle over, clipped.

        THE CELL COUNT IS THE SOLVER'S, NOT A FRESH DERIVATION.
        `SF.footprint_cells` rounds the side UP to an ODD number of cells and
        floors it at 3, and the window is then CENTRED on the cell holding the
        site, which is what makes the reported coordinate the true centre of the
        window it was scored on. Slicing index(c - w/2) .. index(c + w/2)
        instead gives an EVEN count for a side that is a whole multiple of the
        step -- one extra ring of cells -- and MEASURED at
        `deepnorth-sandbox#complete-station-hub` that extra ring read 5.47 m of
        freeboard where the solver recorded 2.84 m, a 2.63 m disagreement that
        tripped this module's own self-check. The self-check was right and the
        reader was wrong.
        """
        kx = SF.footprint_cells(w, self.step)
        kz = SF.footprint_cells(d, self.step)
        cxi, czi = self.index(cx), self.index(cz)
        ix0, ix1 = cxi - kx // 2, cxi + kx // 2
        iz0, iz1 = czi - kz // 2, czi + kz // 2
        ix0, iz0 = max(0, ix0), max(0, iz0)
        ix1, iz1 = min(self.n - 1, ix1), min(self.n - 1, iz1)
        if ix1 < ix0 or iz1 < iz0:
            raise SystemExit("rectangle falls outside the grid")
        return self.biome[iz0:iz1 + 1, ix0:ix1 + 1], self.height[iz0:iz1 + 1, ix0:ix1 + 1]

    def measure(self, cx: float, cz: float, w: float, d: float, bid: int) -> dict:
        bio, hgt = self.window(cx, cz, w, d)
        return {
            "biome_at_centre": int(self.biome[self.index(cz), self.index(cx)]),
            "biome_purity": float((bio == bid).mean()),
            "freeboard_m": float(hgt.min() - SF.WATER_LEVEL),
            "flat_spread_m": float(hgt.max() - hgt.min()),
            "cells": int(bio.size),
        }


def _markers(rc, cx: float, cy: float, cz: float,
             radius: float) -> list[tuple[float, float, float]]:
    out = rc.command(f"findObjects -near {cx:.2f} {cy:.2f} {cz:.2f} {radius:.2f} "
                     f"-prefab {clearing.LOCATION_MARKER}")
    hits = []
    for _name, x, _y, z in clearing.POSITION_RE.findall(out):
        x, z = float(x), float(z)
        hits.append((x, z, math.hypot(x - cx, z - cz)))
    return sorted(hits, key=lambda h: h[2])


def live_markers(cx: float, cy: float, cz: float, radius: float) -> list[tuple[float, float, float]]:
    """(x, z, distance) of every LocationProxy within `radius` of the pad centre.

    READ THE LIMIT OF THIS, because it cost a refused build to learn: a
    LocationProxy exists only in a zone that has been GENERATED. MEASURED at
    `pre-yagluth#plains-farm-base` -- this function saw 2 markers within 296 m
    and proposed a centre 72.11 m away with 59 m of stand-off; `clearing/clear.py`
    then ran `zones_generate` over that cylinder and immediately found a marker
    25.83 m from it. The marker was not missed, it did not yet EXIST.
    `ZoneSystem::SpawnZone` runs PlaceLocations when the zone is first generated,
    and on a dedicated server with no peers most of the world has never been
    generated at all. So this answers "what markers are already in the world
    here", which is a strictly smaller set than "what markers will be here", and
    it must be paired with `confirm_centre()` before any centre is trusted.
    """
    port, password = rcon_credentials()
    with Rcon(container_ip(), port, password) as rc:
        rc.command("players")  # liveness, 8 bytes, before anything that matters
        return _markers(rc, cx, cy, cz, radius)


def confirm_centre(cx: float, cy: float, cz: float, clear_r: float,
                   look: float, timeout: float = 240.0) -> list[tuple[float, float, float]]:
    """GENERATE the zones the clearing cylinder touches, then measure markers.

    This is the only measurement of a candidate centre that can be believed,
    and it is deliberately the same order `clearing/clear.py` uses: generate,
    then ask. Generating early does not change WHAT gets generated --
    `PlaceVegetation` and `PlaceLocations` seed from
    `worldSeed + zoneX*4271 + zoneZ*9187`, so the zone is identical whenever it
    is first built -- and generated zones are saved, so this is work the world
    would have done the first time a player walked here.

    READINESS IS MEASURED, not slept on. `zones_generate` is a staged Upgrade
    World operation that runs across frames, so the answer immediately after
    `start` is not the answer. `PlaceZoneCtrl` puts exactly one `_ZoneCtrl` in
    each generated zone, and the set of zones `zones_generate pos=P max=M`
    selects is exactly those whose CENTRE is within M of P (MEASURED in
    clear.py's own survey), so the expected `_ZoneCtrl` count is computable and
    polling it is a real completion signal rather than a guess. One ZDO per
    zone keeps the reply a few tens of lines, well clear of the wedge.
    """
    reach = clearing.ZONE_REACH_M
    gen_max = math.ceil((clear_r + reach) * 100) / 100
    want = set()
    span = int((gen_max + 64.0) // 64.0) + 2
    here = tcdata.zone_of(cx, cz)
    for zx in range(here[0] - span, here[0] + span + 1):
        for zz in range(here[1] - span, here[1] + span + 1):
            zcx, zcz = tcdata.zone_centre(zx, zz)
            if math.hypot(zcx - cx, zcz - cz) <= gen_max:
                want.add((zx, zz))
    port, password = rcon_credentials()
    with Rcon(container_ip(), port, password) as rc:
        rc.command("players")
        rc.console("stop")
        rc.console(f"zones_generate pos={cx:.2f},{cz:.2f} max={gen_max:g}")
        rc.console("start")
        deadline = time.time() + timeout
        seen = -1
        while True:
            out = rc.command(f"findObjects -near {cx:.2f} {cy:.2f} {cz:.2f} "
                             f"{gen_max + reach:.2f} -prefab _ZoneCtrl")
            got = set()
            for _n, x, _y, z in clearing.POSITION_RE.findall(out):
                got.add(tcdata.zone_of(float(x), float(z)))
            seen = len(want & got)
            if seen >= len(want) or time.time() >= deadline:
                break
            time.sleep(2.0)
        print(f"    zones_generate max={gen_max:g}: {seen} of {len(want)} zones carry a "
              f"_ZoneCtrl" + ("" if seen >= len(want) else "  <-- TIMED OUT, treat as incomplete"))
        return _markers(rc, cx, cy, cz, clear_r + look)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--seed", default="Pirate68")
    ap.add_argument("--grid", default=str(GRID))
    ap.add_argument("--index", default="/tmp/terraform/plans_wb/index.json")
    ap.add_argument("--radius", type=float, default=140.0,
                    help="furthest the pad may move, metres (default %(default)s)")
    ap.add_argument("--step", type=float, default=2.0, help="candidate spacing, metres")
    ap.add_argument("--margin", type=float, default=2.0,
                    help="clearing margin beyond the pad half-diagonal; matches clearing/area.py")
    ap.add_argument("--apron-floor", type=float, default=0.0,
                    help="minimum apron to consider")
    ap.add_argument("--safety", type=float,
                    help="metres the nearest live marker must exceed the clearing radius by. "
                         "DEFAULT: requirement.location_clearance_m minus --margin, which makes "
                         "the live test reproduce the solver's own gate EXACTLY -- site_finder "
                         "requires marker >= location_clearance_m + half_diag, and the clearing "
                         "radius at apron 0 is half_diag + margin, so that subtraction is an "
                         "identity rather than a taste pick. Lower it only deliberately, and only "
                         "after seeing what clearance is actually achievable: on a world carrying "
                         "More_World_Locations_AIO's 190 POIs the declared 60 m is not always "
                         "available anywhere nearby, and the refusal below says how close it got.")
    ap.add_argument("--no-generate", action="store_true",
                    help="skip the zones_generate confirmation of the chosen centre. Only "
                         "meaningful where the surrounding zones are ALREADY generated; "
                         "otherwise the marker measurement is measuring a world that does not "
                         "exist yet and `clearing/clear.py` will refuse the build.")
    ap.add_argument("--write", action="store_true",
                    help="move solved.x/z and re-cost the pad in placements.yaml")
    args = ap.parse_args()

    path, doc, place = load_place(args.world, args.preset, args.id)
    solved = place["solved"]
    cx0, cz0 = float(solved["x"]), float(solved["z"])
    pad_y = float((solved.get("flatten_cost") or {})["target_y"])
    pad_w, pad_d, rect_src = pad_rect(place)
    verify_r, reach_src = collider_reach(Path(args.index), args.preset, args.id)
    verify_r += 2.0
    apron, clear_r = apron_for(pad_w, pad_d, args.margin, verify_r, args.apron_floor)
    # The identity: site_finder gates on `marker >= location_clearance_m +
    # half_diag`, and `clear_r == half_diag + margin` at apron 0, so subtracting
    # the margin makes the live test the same test the solver ran -- against the
    # location set the solver could not see.
    declared_clearance = float((place.get("requirement") or {}).get(
        "location_clearance_m", SF.DEFAULT_LOCATION_CLEARANCE))
    safety = args.safety if args.safety is not None else max(
        0.5, declared_clearance - args.margin)
    need = clear_r + safety

    req = place["requirement"]
    bid = SF.BIOME_ID[str(req["biome"]).lower()]
    p = SF.requirement_defaults(req)
    grid = Grid(Path(args.grid))

    print(f"{args.preset}#{args.id}  {place['blueprint']}")
    print(f"  pad {pad_w} x {pad_d} m ({rect_src}) at ({cx0}, {cz0}) y {pad_y}")
    print(f"  {reach_src} -> verification radius {verify_r:.2f} m")
    print(f"  apron {apron:g} m + margin {args.margin:g} m -> clearing radius {clear_r:.2f} m; "
          f"a marker must be further than {need:.2f} m (stand-off {safety:g} m, "
          + ("declared: requirement.location_clearance_m "
             f"{declared_clearance:g} minus the margin" if args.safety is None
             else "given on the command line") + ")")

    # SELF-CHECK, and it checks only what the GRID is the source of. The solver
    # measured this site with the rectangle it recorded in
    # `footprint_orientation`, so the reader is checked against THAT rather than
    # a possibly-shrunken pad.
    #
    # `biome_purity` is a grid quantity: `site_finder._purity` counts matching
    # cells in the same odd centred window this reader builds, so a disagreement
    # means the reader is wrong and nothing it says about a candidate is
    # founded. That is a refusal.
    #
    # `freeboard_m` is NOT. The solver records the figure from its 1 m
    # refinement pass, and the 1 m PatchScan sampling INCLUDES RIVERS while the
    # 8 m seedscan grid does not. MEASURED at
    # `deepnorth-sandbox#complete-station-hub`: the grid's lowest cell over the
    # solved 56 m square is 39.93 (9.93 m of freeboard) while the recorded
    # figure is 2.84 m, i.e. a lowest cell at 32.84 -- and the recorded
    # flatten_cost independently agrees with the 1 m reading, max_fill 10.3 from
    # target_y 43.15 down to 32.85 and max_cut 15.27 up to 58.42. A river cuts
    # that footprint and the river-free grid cannot see it. So gating on this
    # comparison would refuse a site over a difference between two height
    # sources rather than over anything about the ground. It is printed with
    # both sources named, and the AUTHORITATIVE freeboard test stays where it
    # belongs: the 1 m bill below, on the LEVELLED pad height.
    sw, sd = flattenmod.pad_extent(place)
    here = grid.measure(cx0, cz0, sw, sd, bid)
    want_pure = solved.get("biome_purity")
    want_free = solved.get("freeboard_m")
    print(f"  grid self-check over the solver's own {sw} x {sd} m rectangle: "
          f"purity {here['biome_purity']:.3f} vs recorded {want_pure} (both 8 m grid, GATED); "
          f"freeboard {here['freeboard_m']:.2f} m at 8 m river-free vs recorded {want_free} m "
          f"at 1 m river-inclusive (not gated, different height sources)")
    if want_pure is not None and abs(here["biome_purity"] - float(want_pure)) > 0.05:
        print(f"  REFUSED: the grid reader disagrees with the solver on biome purity at the site "
              f"the solver measured -- {here['biome_purity']:.3f} against a recorded "
              f"{want_pure} -- and both are the same 8 m grid quantity, so the reader is wrong "
              f"and nothing it says about a candidate is founded.", file=sys.stderr)
        return 3

    anchor_xz = None
    if req.get("near") or req.get("near_xz"):
        m = ANCHOR_RE.search(str(solved.get("anchor", "")))
        if not m:
            raise SystemExit("requirement declares an anchor but solved.anchor has no coordinates")
        anchor_xz = (float(m.group(1)), float(m.group(3)))

    markers = live_markers(cx0, pad_y, cz0, args.radius + need + 64.0)
    print(f"  LIVE LocationProxy markers within {args.radius + need + 64.0:.0f} m: {len(markers)}")
    for x, z, dist in markers[:8]:
        print(f"    {dist:8.2f} m at ({x:.1f}, {z:.1f})")
    if not markers:
        print("  nothing to avoid: no marker is anywhere near this pad", file=sys.stderr)
        return 0
    mx = np.array([m[0] for m in markers])
    mz = np.array([m[1] for m in markers])

    # Candidate centres on a grid, nearest first. Nearest-first is the whole
    # objective: the site was chosen for its surroundings, so the correct move is
    # the smallest one that clears the marker, not the best-scoring one somewhere
    # else.
    k = int(args.radius / args.step)
    # Plain Python floats, not numpy scalars: a numpy float64 reaches
    # `yaml.safe_dump` as an unrepresentable object and the write fails after
    # every measurement has been taken.
    offs = [round(-k * args.step + i * args.step, 3) for i in range(2 * k + 1)]
    cand = [(math.hypot(dx, dz), cx0 + dx, cz0 + dz)
            for dx in offs for dz in offs if math.hypot(dx, dz) <= args.radius]
    cand.sort()

    def search(threshold: float, skip: list | None = None):
        """Nearest qualifying centre, or (None, why-nothing-qualified).

        Split out from `main` for two reasons. The refusal path re-runs it with
        the threshold lowered to the bare minimum and reports what clearance the
        ground actually offers -- a refusal that only says "no" leaves the next
        reader to re-derive the achievable number by hand, and that is how a site
        ends up quietly built at 0.5 m of stand-off. And `skip` lets the caller
        reject a centre the 1 m PatchScan bill disqualified and ask for the next
        one: the 8 m grid is a coarse pre-filter, and MEASURED at
        `pre-yagluth#plains-farm-base` it read 1.21 m of freeboard where the 1 m
        sampling found a cell at 0.64 m, under the 1.0 m floor. Taking the grid's
        word there would be exactly the defect this module is written against.
        """
        rejects: dict[str, int] = {}
        tried = 0
        skip = skip or []
        for disp, x, z in cand:
            tried += 1
            if any(math.hypot(x - sx, z - sz) <= sr for sx, sz, sr in skip):
                rejects["bill"] = rejects.get("bill", 0) + 1
                continue
            d = float(np.hypot(mx - x, mz - z).min())
            if d <= threshold:
                rejects["marker"] = rejects.get("marker", 0) + 1
                continue
            got = grid.measure(x, z, pad_w, pad_d, bid)
            if got["biome_at_centre"] != bid:
                rejects["biome"] = rejects.get("biome", 0) + 1
                continue
            if got["biome_purity"] < p["min_biome_purity"] - 1e-6:
                rejects["purity"] = rejects.get("purity", 0) + 1
                continue
            if got["freeboard_m"] < p["min_height_m"] - 1e-6:
                rejects["freeboard"] = rejects.get("freeboard", 0) + 1
                continue
            if math.isfinite(p["max_height_m"]) and got["freeboard_m"] > p["max_height_m"] + 1e-6:
                rejects["max_height"] = rejects.get("max_height", 0) + 1
                continue
            if math.isfinite(p["max_grade_pct"]) and got["flat_spread_m"] > SF.grade_cap(p) + 1e-6:
                rejects["grade"] = rejects.get("grade", 0) + 1
                continue
            if anchor_xz is not None:
                adist = math.hypot(x - anchor_xz[0], z - anchor_xz[1])
                if adist > p["within_m"] + 1e-6:
                    rejects["within_m"] = rejects.get("within_m", 0) + 1
                    continue
            else:
                adist = None
            return {"displacement_m": round(disp, 2), "x": round(x, 1), "z": round(z, 1),
                    "marker_dist_m": round(d, 2),
                    "anchor_dist_m": None if adist is None else round(adist, 1),
                    **got}, rejects, tried
        return None, rejects, tried

    if p["coastal"]:
        raise SystemExit("coastal placements are not handled: water distance is not measured "
                         "here, and guessing it would be the defect this file warns about")

    # The 8 m grid proposes; the 1 m PatchScan bill DECIDES. A centre the grid
    # likes can still be uncuttable (past the +/-8 m apply clamp), median below
    # the water plane, or hold a 1 m cell under the freeboard floor the 8 m
    # average hid. Each rejection feeds back into `skip` and the search resumes
    # at the next-nearest centre, so the answer stays "the smallest move that
    # actually works" rather than "the smallest move the coarse grid approved".
    # Exclusion DISCS, not points. A 1 m bill failure is a property of the
    # GROUND under the pad -- a cell below the water plane, or a cut past the
    # clamp -- and that cell stays inside the pad for any shift smaller than the
    # pad's half-short-side. MEASURED: without discs, a river crossing the
    # Plains pad rejected twelve consecutive centres 2 m apart, all for the same
    # cell at -1.75 m, and the search exhausted its attempts without leaving the
    # riverbank. The disc radius is that half-short-side, so one failure retires
    # the whole neighbourhood it describes.
    bill_exclusion_r = min(pad_w, pad_d) / 2
    skip: list = []
    chosen = bill = None
    for attempt in range(1, 13):
        chosen, rejects, tried = search(need, skip)
        print(f"  attempt {attempt}: candidates examined {tried} of {len(cand)}; "
              f"rejected {rejects}")
        if chosen is None:
            break
        print(f"    candidate {chosen['displacement_m']} m out at "
              f"({chosen['x']}, {chosen['z']}): marker {chosen['marker_dist_m']} m "
              f"({chosen['marker_dist_m'] - clear_r:.2f} m clear of the cylinder), "
              f"purity {chosen['biome_purity']:.3f}, 8 m freeboard "
              f"{chosen['freeboard_m']:.2f} m, 8 m spread {chosen['flat_spread_m']:.2f} m"
              + (f", anchor {chosen['anchor_dist_m']} m of {p['within_m']:g} m"
                 if chosen["anchor_dist_m"] is not None else ""))
        zones = flattenmod.zones_for(chosen["x"], chosen["z"], pad_w, pad_d)
        out = Path("/tmp/terraform") / f"relocate_{args.preset}_{args.id}.bin"
        patches = flattenmod.run_patchscan(sorted(zones), args.seed, out)
        bill = recost_pad.cost(patches, chosen["x"], chosen["z"], pad_w, pad_d)
        print(f"    1 m bill: target_y {bill['target_y']} (freeboard "
              f"{bill['target_freeboard_m']} m), moved {bill['moved_m3']} m3, "
              f"max cut {bill['max_cut_m']} m, max fill {bill['max_fill_m']} m, "
              f"{bill['samples']} samples, {bill['over_clamp_samples']} over the +/-8 m clamp, "
              f"lowest generated cell {bill['min_freeboard_m']} m over the water plane")
        why = None
        if bill["over_clamp_samples"]:
            why = (f"{bill['over_clamp_samples']} sample(s) past the +/-8 m "
                   f"TerrainComp::ApplyToHeightmap clamp, so the pad cannot be cut to target_y")
        elif bill["target_freeboard_m"] < p["min_height_m"] - 1e-6:
            # The test is the LEVELLED surface, not the lowest generated cell.
            # Every placement here sets `flatten_required: true`, so a river
            # crossing the footprint gets filled -- and MEASURED across the
            # Plains north of this site, gating on the pre-flatten low cell
            # rejected every candidate for 260 m in every direction because the
            # 8 m seedscan grid is RIVER-FREE while the 1 m PatchScan sampling
            # includes rivers, so the two disagree by up to 4.8 m there. What
            # actually decides whether a base is on land is the height of the
            # pad it ends up standing on, and `min_height_m` applied to that is
            # the same question the requirement is asking.
            why = (f"levelled pad height {bill['target_y']} is only "
                   f"{bill['target_freeboard_m']} m over the y={SF.WATER_LEVEL:g} water plane, "
                   f"under the {p['min_height_m']:g} m min_height_m floor "
                   f"(lowest generated cell {bill['min_freeboard_m']} m)")
        if why is None and not args.no_generate:
            # LAST GATE, and the only authoritative one: generate the zones the
            # cylinder touches and ask again. Whatever this turns up joins the
            # marker set permanently, so the next attempt is searched against a
            # strictly better map of the world rather than the same blind one.
            fresh = confirm_centre(chosen["x"], bill["target_y"], chosen["z"], clear_r,
                                   look=args.radius)
            known = {(round(x, 1), round(z, 1)) for x, z, _ in
                     ((mx[i], mz[i], 0.0) for i in range(len(mx)))}
            added = 0
            for x, z, _dist in fresh:
                if (round(x, 1), round(z, 1)) not in known:
                    mx = np.append(mx, x)
                    mz = np.append(mz, z)
                    known.add((round(x, 1), round(z, 1)))
                    added += 1
            inside = [h for h in fresh if h[2] <= need]
            print(f"    after generating: {len(fresh)} marker(s) within "
                  f"{clear_r + args.radius:.0f} m, {added} of them NEW; nearest "
                  + (f"{fresh[0][2]:.2f} m" if fresh else "none"))
            if inside:
                why = (f"generating the zones revealed {len(inside)} marker(s) inside the "
                       f"{need:.2f} m stand-off, nearest {inside[0][2]:.2f} m -- they did not "
                       f"exist before the zone was generated")
        if why is None:
            break
        print(f"    rejected at 1 m: {why}" if "generating" not in why
              else f"    rejected after generating: {why}")
        skip.append((chosen["x"], chosen["z"], bill_exclusion_r))
        chosen = bill = None

    if chosen is None or bill is None:
        floor_pick, _, _ = search(clear_r + 0.5)
        if floor_pick is None:
            print(f"  REFUSED: no centre within {args.radius:g} m satisfies the requirement even "
                  f"with the stand-off cut to 0.5 m past the {clear_r:.2f} m cylinder; the "
                  f"blocker is not the stand-off", file=sys.stderr)
        else:
            print(f"  REFUSED: no centre within {args.radius:g} m both keeps a marker beyond "
                  f"{need:.2f} m (the declared {declared_clearance:g} m standard) and passes the "
                  f"1 m bill. The BEST stand-off this ground offers is "
                  f"{floor_pick['marker_dist_m']} m at {floor_pick['displacement_m']} m "
                  f"displacement, i.e. {floor_pick['marker_dist_m'] - clear_r:.2f} m past the "
                  f"cylinder. Re-run with "
                  f"--safety {max(0.5, floor_pick['marker_dist_m'] - clear_r - 0.01):.2f} to take "
                  f"it, deliberately.", file=sys.stderr)
        return 4

    print(f"  MOVE {chosen['displacement_m']} m to ({chosen['x']}, {chosen['z']}), "
          f"pad_y {bill['target_y']}")
    print(f"  build with: network/build_site.py --preset {args.preset} --id {args.id} "
          f"--apron {apron:g}")
    if not args.write:
        return 0

    solved["x"], solved["z"] = chosen["x"], chosen["z"]
    solved["y"] = round(float(bill["max_generated_m"]), 2)
    solved["biome_purity"] = round(chosen["biome_purity"], 3)
    solved["freeboard_m"] = round(chosen["freeboard_m"], 2)
    solved["footprint_orientation"] = f"{pad_w:.1f} m along x by {pad_d:.1f} m along z"
    solved["flatten_cost"] = {k2: v for k2, v in bill.items()
                              if k2 not in ("samples", "over_clamp_samples", "min_generated_m",
                                            "max_generated_m", "min_freeboard_m",
                                            "target_freeboard_m")}
    if chosen["anchor_dist_m"] is not None:
        solved["anchor_dist_m"] = chosen["anchor_dist_m"]
    # A second relocation of the same pad must not erase the first: `moved_from`
    # would then read as the solver's centre when it is actually the previous
    # move's destination, and the displacement would understate how far the site
    # has travelled from what the solver chose.
    previous = solved.get("relocation")
    solved["relocation"] = {
        "by": "network/relocate_pad.py",
        "reason": "a LIVE generated location marker stood inside the clearing cylinder at the "
                  "solved centre; solved.nearest_location_m is a vanilla-only figure and could "
                  "not see it (see this preset's location_clearance_note)",
        "moved_from": [cx0, cz0],
        "displacement_m": chosen["displacement_m"],
        "search_radius_m": args.radius,
        "search_step_m": args.step,
        "pad_rect_m": [pad_w, pad_d],
        "pad_rect_source": rect_src,
        "clearing_radius_m": round(clear_r, 2),
        "clearing_apron_m": apron,
        "clearing_margin_m": args.margin,
        "verification_radius_m": round(verify_r, 2),
        "verification_radius_source": reach_src + " plus verify_site.py's 2.0 m",
        "live_marker_dist_m": chosen["marker_dist_m"],
        "live_marker_clearance_over_cylinder_m": round(chosen["marker_dist_m"] - clear_r, 2),
        "live_markers_seen": len(markers),
        "marker_source": "findObjects -prefab LocationProxy over RCON, 2026-09-15",
        "marker_standoff_required_m": round(safety, 2),
        "marker_standoff_basis": ("requirement.location_clearance_m minus the clearing margin, "
                                  "which reproduces site_finder's own gate exactly"
                                  if args.safety is None else "declared on the command line"),
        "satisfies_declared_location_clearance": bool(
            chosen["marker_dist_m"] >= declared_clearance + (clear_r - args.margin) - 1e-6),
        "rechecked": ["biome", "min_biome_purity", "min_height_m", "max_height_m",
                      "max_grade_pct", "within_m", "flatten_cost", "clamp"],
        "not_rechecked": ["overland_from_spawn", "route_from_spawn", "route_to_anchor",
                          "facing_check"],
        "not_rechecked_basis": "whole-world graph searches; a move of "
                               f"{chosen['displacement_m']} m cannot change landmass, and the "
                               "search radius is bounded so that stays true",
        "grid_source": "tools/seedscan/run_scan.sh STEP=8 HEIGHT=1, seed " + args.seed,
        "height_source": "blueprints/run_patchscan.sh, step 1 m, rivers included",
    }
    if previous:
        solved["relocation"]["supersedes"] = previous
        origin = previous.get("moved_from")
        if origin:
            solved["relocation"]["moved_from_solved"] = origin
            solved["relocation"]["displacement_from_solved_m"] = round(
                math.hypot(chosen["x"] - float(origin[0]), chosen["z"] - float(origin[1])), 2)
    # Two decimals, not one: the rectangle is a MEASURED collider box and
    # rounding 34.11 to 34.1 silently shrinks the pad by 1 cm per side, which
    # then disagrees with the number this run sized the clearing cylinder from.
    req["footprint_xz_m"] = [round(pad_w, 2), round(pad_d, 2)]
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, width=100, sort_keys=False))
    print(f"  written -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
