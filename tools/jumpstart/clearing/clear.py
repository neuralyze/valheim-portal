#!/usr/bin/env python3
"""Clear a building site headlessly: pre-generate its zones, then delete the
scenery standing in the pad.

ORDER.  This is step 1 of the site-preparation order described in
tools/jumpstart/SITE_PREPARATION_ORDER.md: generate -> clear -> flatten ->
place structures -> place loose objects.  Two corrections to what this header
used to claim, both MEASURED on 2026-09-15:

  * `zones_generate` (step 0, below) is a HARD PRECONDITION of the flatten,
     not a convenience.  A compiler written into an ungenerated zone is
     ignored by `Heightmap::ApplyModifiers` -- it finds compilers by scanning
     the INSTANTIATED `TerrainComp::s_instances`, and a server with no peers
     instantiates none -- so the zone then plants its entire vegetation set on
     the unmodified generated height, standing it up to 7.68 m in the air over
     the finished pad, permanently, and ignoring the paint's cleared alpha.
     `terraform/flatten.py` now refuses to write into a zone with no
     `_ZoneCtrl` at its centre.
  * clearing BEFORE the flatten is not required.  `objects_remove`'s
     `pos`/`max` filter is a vertical cylinder on `Utils.DistanceXZ` with no
     height bound, so an object 7 m above a cut pad is exactly as removable as
     one resting on it.  MEASURED: pads prepared as generate->clear->flatten
     and as generate->flatten->clear both ended with the same two survivors,
     `_TerrainCompiler` and `_ZoneCtrl`.  Clearing stays first only so that the
     step carrying `zones_generate` runs immediately before the step that
     requires it.

ROUTE, and why it is this one.  Every claim below was MEASURED tonight against
a throwaway server in /tmp running the same build and the same plugin set as
Ulfsland (Valheim l-1.0.12, network version 40):

  1. THE TREES DO NOT EXIST YET.  A dedicated server with zero peers generates
     zones only around `ZNet.GetReferencePosition()`, and on a dedicated server
     that position is never set by anything: MEASURED, a fresh sandbox world
     had ghost-generated 81 zones (9x9) whose `_ZoneCtrl` objects all sat at
     (1000000, 0, 1000000) -- outside the 10500 m world -- and contained ZERO
     vegetation objects anywhere in the world.  `findObjects -prefab Beech1`
     answered "No objects found".  So a clearing step that only DELETES would
     have nothing to delete at pipeline time, and the trees the operator walked
     into were created later, when a player first loaded the zone.  That is the
     defect: it is a generation-order problem, not a deletion problem.

  2. `zones_generate` FIXES THAT, HEADLESSLY.  Upgrade World (already installed
     on Ulfsland, `BepInEx/plugins/Upgrade_World/UpgradeWorld.dll`, v1.82)
     pre-generates zones from the server console with no player connected.
     MEASURED: `zones_generate pos=200,200 max=100` + `start` reported "8 zones
     generated" and created 1365 objects -- 142 Beech1, 14 Birch1, 1 Oak1,
     200 Rock_4, 129 Bush01, 23 RaspberryBush, 11 Pickable_Mushroom, 8
     _ZoneCtrl -- where seconds earlier there were none.
     Generating early does not change WHAT gets generated: `PlaceVegetation`
     seeds its RNG with `worldSeed + zoneX*4271 + zoneZ*9187 + prefabHash`
     (MEASURED in assembly_valheim IL), so the same trees appear whether the
     zone is generated now or on first visit.

  3. IT STAYS CLEARED.  `ZoneSystem::SpawnZone` skips PlaceLocations,
     PlaceVegetation and PlaceZoneCtrl entirely when `IsZoneGenerated(zone)`
     (MEASURED in IL), and generated zones are saved in the world file.  A
     removed tree therefore never regenerates -- and `ZDOMan::HandleDestroyedZDO`
     also records the dead id in `m_deadZDOs`, which is saved too, so a client
     that still remembers the object cannot resurrect it.

  4. THE REMOVAL PRIMITIVE.  `objects_remove` works on the ZDO table
     (`RemoveObjects::ProcessZDO` -> `Helper::RemoveZDO`), which is fully
     loaded on a server with no players, and its `pos=`/`max=` filter is a
     vertical cylinder on `Utils.DistanceXZ` -- measured per OBJECT, not per
     zone: `objects_count Beech1 pos=200,200 max=20` answered 5 and `max=100`
     answered 129.  `ignore=` accepts wildcards: `ignore=_*` dropped `_ZoneCtrl`
     from the same count (72 -> 71).
     Two rejected alternatives, for the record.  `vegetation_remove` reads the
     game's own vegetation table (`ZoneSystem.m_vegetation`, 170-odd prefabs --
     it prints the list) but is ZONE-granular: `RemoveVegetation::ExecuteZone`
     removes every matching ZDO in a 64 m zone, so a 70 m pad would strip
     128-192 m of forest.  World Edit Commands' `object remove` operates on
     `ZNetView` instances, and a server with no peers instantiates none.

  5. WHAT SURVIVES ON PURPOSE.  Everything whose prefab name starts with `_`:
     `_ZoneCtrl` (the zone's creature spawner and its saved spawn state) and
     `_TerrainCompiler` (the terrain the flatten step writes).  That matches the
     game's own convention and ValheimRcon's `ZdoUtils::CanModifyZdo`, which
     refuses `_`-prefixed prefabs without -force.

VERIFICATION.  Counting is done with Upgrade World's `objects_count`, read out
of the SERVER CONSOLE LOG, and both halves of that sentence are measured
corrections to what this file used to do:

  * `findObjects -near` could not survive a dense site.  MEASURED against the
    live Ulfsland workshop pad holding this 1,907-piece building: a
    `findObjects -detailed` query wide enough to see the body WEDGED the
    server.  `RconCommandReceiver` passes the command's result to
    `Log.Message` -- the container's stdout, through supervisord -- BEFORE
    `ValidatePayloadLength` truncates it, so a 1,907-line result is a ~290 KB
    blocking write on the Unity main thread.  The log stopped mid-object and
    the server answered nothing further until the container was restarted.  A
    sustained ~100 line/s stream of smaller replies wedged it the same way
    after about 1,000 lines.  So the rule is not "keep replies under 4050
    bytes", it is "never ask a question whose ANSWER is long".
  * `objects_count` is NOT a staged operation.  MEASURED: staging it answers
    `routine is null`, and its per-prefab table plus `Total:` line goes to the
    server console, not into the RCON reply -- the reply is only
    `Command '<cmd>' executed.`.  So it is run directly and read back from the
    container log with `console.run_console`, the same way `flatten.py` reads
    a console command's output.  A few hundred bytes for a site with thousands
    of objects, and the reply size no longer scales with the thing being
    measured.

Note the SHAPE difference from the old box: `objects_count`'s `pos=`/`max=` is
Upgrade World's vertical cylinder (`Utils.DistanceXZ`, y ignored), so `half` is
read as a radius.  For the clearing decision that is the correct shape anyway,
because the removal is the same cylinder.

WRITE PATH.  Both operations go through the LEDGER (`flatten.install_emit` ->
`ledger/live.py` `LiveBuilder.emit`): appended first, then sent over the
session's own transport, then verified by the record's own postcondition.
This step used to drive raw RCON and append nothing -- MEASURED on the previous
Fresh42 drive, 13 unit-runs at rc 0 and zero records naming any installation
-- so the zones it generated and the ground it cleared were invisible to every
later write and every replay.  See `terraform/flatten.py::
INSTALLATION_WRITE_PATH_DEPENDS_ON`.  The socket is the session's, resolved from
the environment and recorded on every record; there is no per-tool override.

Usage:
    clear.py plan  --world Ulfsland --preset pre-bonemass --id iron-era-workshop
    clear.py apply --world Ulfsland --preset pre-bonemass --id iron-era-workshop --actor <you>
    clear.py apply --placements /tmp/p.yaml --id sandbox --actor <you>
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.append(str(JUMPSTART))

from area import (ClearArea, ClearAreaRefused, DEFAULT_MARGIN_M,  # noqa: E402
                  clearing_area, levelling_refusal, placement)
from rcon import Rcon  # noqa: E402
from console import run_console  # noqa: E402
import flatten as FL  # noqa: E402  THE installation write path lives there
import worldseed  # noqa: E402

ZONE_HALF_M = 32.0
# A zone is selected by the distance from `pos` to its CENTRE (MEASURED:
# max=100 at (200,200) selected exactly the 8 zones whose centres lie within
# 100 m), so covering every zone the cylinder touches needs the cylinder radius
# plus a zone's half-diagonal.
ZONE_REACH_M = ZONE_HALF_M * math.sqrt(2.0)
DEFAULT_KEEP = ("_*",)
# `Id:` is required on the line: ValheimRcon truncates a response at ~4050
# bytes mid-line, and a bare `^-Prefab: (\S+)` happily reads "Pic" out of a
# severed "Pickable_Stone" and reports it as a location marker.
POSITION_RE = re.compile(
    r"^-Prefab: (\S+) Id: \S+ Position: \((-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)", re.M)
# Every generated location plants one of these at its centre, so it is the
# cheapest ground truth for "this pad contains world content, not scenery".
LOCATION_MARKER = "LocationProxy"
SETTLE_POLL_S = 2.0

TOTAL_RE = re.compile(r"^Total:?\s*(\d+)\s*$", re.M)
# `objects_count`'s table is one `<prefab>: <count>` line per prefab. MEASURED
# on the live workshop pad: `Total: 1907` first, then `stone_wall_2x1: 854` and
# 59 more. The name is on the LEFT, which is the opposite way round from
# `findObjects`, and reading it the other way silently produces a list of
# numbers. The `Total:` line has the same shape as a prefab row, so it is
# excluded explicitly -- without that, "Total" is reported as a prefab that
# survived the removal.
COUNT_LINE_RE = re.compile(r"^(?!Total:)(\S+):\s*(\d+)\s*$", re.M)


def box_query(rc: Rcon, x: float, y: float, z: float, half: float,
              ident: str = "*") -> tuple[int, list[str]]:
    """Objects inside the cylinder of radius `half`, as (count, prefab names).

    `y` is accepted and ignored: Upgrade World's filter is a vertical cylinder
    on `Utils.DistanceXZ`. It stays in the signature because every caller has
    the surface height to hand and dropping it would make the two coordinate
    conventions in this file look interchangeable.

    `ident` narrows the count to one prefab. The default `*` answers "how much
    is here", which is what clearing needs; a named prefab answers "where did
    MY pieces go", which is what verification needs and which a total cannot
    answer -- a wider circle drawn after `zones_generate` is full of trees, so
    comparing two totals at two radii reports the forest as strays.

    See this module's VERIFICATION note for why this counts on the console
    instead of listing over RCON, and why `objects_count` is NOT staged.
    """
    del y
    lines = run_console(
        rc, f"objects_count id={ident} ignore=_* pos={x:.2f},{z:.2f} max={half:.2f}")
    text = "\n".join(lines)
    total = TOTAL_RE.search(text)
    prefabs = [name for name, _count in COUNT_LINE_RE.findall(text)]
    if total:
        return int(total.group(1)), prefabs
    # No total line at all means the query did not run, which must NOT read as
    # "the pad is empty" - that is how a removal gets aimed at nothing.
    raise RuntimeError(f"objects_count printed no Total line; console said {lines!r}")


def locations_inside(rc: Rcon, area: ClearArea) -> list[tuple[str, float]]:
    """Generated locations whose marker stands inside the clearing cylinder.

    `area.py` already refuses when the radius reaches the clearance the solver
    RECORDED; this asks the world instead.  MEASURED on the sandbox: a site
    picked blind at (-500, 400) turned out to hold a Dolmen -- LocationProxy,
    RockDolmen_2, stone_wall_2x1, Pickable_DolmenTreasure and a skeleton
    spawner -- and `id=* ignore=_*` would have deleted all of it.  Half a
    deleted location is worse than a refusal.
    """
    out = rc.command(f"findObjects -near {area.centre_x:.2f} {area.surface_y:.2f} "
                     f"{area.centre_z:.2f} {area.radius_m:.2f} -prefab {LOCATION_MARKER}")
    hits = []
    for name, x, _y, z in POSITION_RE.findall(out):
        distance = math.hypot(float(x) - area.centre_x, float(z) - area.centre_z)
        if distance <= area.radius_m:
            hits.append((name, round(distance, 2)))
    return hits


def generate_max(area: ClearArea) -> float:
    """Rounded UP: `round()` shaved 2 mm off the required reach for the 70 m
    workshop pad (96.7523 -> 96.75), and a corner zone whose centre lands in
    that sliver would be left ungenerated -- which is the whole defect, just
    2 mm wide."""
    return math.ceil((area.radius_m + ZONE_REACH_M) * 100) / 100


def commands(area: ClearArea, keep: tuple[str, ...]) -> list[str]:
    """The two staged operations, as they go over the wire.

    `id=*` is REQUIRED: `objects_remove` with only `ignore=` answers
    "Error: Missing ids." and removes nothing -- MEASURED, and it is a silent
    failure as far as the RCON reply goes, which is why this step verifies by
    counting rather than by reading a reply.  `id=*` is every prefab
    ZNetScene knows; `ignore=` subtracts, and wildcards work on both sides
    (`objects_count *` answered 14 including `_ZoneCtrl: 1`, and
    `objects_count id=* ignore=_*` answered 13 without it).
    """
    pos = f"pos={area.centre_x:g},{area.centre_z:g}"
    parts = ["objects_remove", "id=*"]
    if keep:
        parts.append(f"ignore={','.join(keep)}")
    parts += [pos, f"max={area.radius_m:.2f}"]
    return [
        f"zones_generate {pos} max={generate_max(area):g}",
        " ".join(parts),
    ]


def records(area: ClearArea, keep: tuple[str, ...], preset: str) -> list[dict]:
    """Step 1 as LEDGER RECORDS: the two commands above, byte for byte, each
    with the postcondition this file already verified by hand.

    `zones` is the set the clearing CYLINDER intersects, the same
    `settlements/build.py::zone_list` a settlement pad's own generate carries
    -- `generate_max` reaches every one of them by construction, so one
    `_ZoneCtrl` per zone centre is the real completion signal.  The removal's
    count is taken over `verify_half_side_m`, the region this tool has always
    verified; that region is NARROWER than the cylinder it removes over, and
    that is stated in `meta` rather than hidden (the exact disc `r` is refused
    by `replay.Server.count` above 40 m for `id=*`, and 2 of the 13 Fresh42
    installations clear wider than that: iron-era-workshop 51.50 m,
    blackforest-walled-base 41.60 m)."""
    gen, remove = commands(area, keep)
    pid = area.placement_id
    zones = FL._settlement_emitter().zone_list(area.centre_x, area.centre_z,
                                               area.radius_m)
    where = [area.centre_x, area.centre_z]
    meta = {"installation": f"{preset}/{pid}", "tool": "clearing/clear.py apply"}
    return [
        {"op": "zones_generate",
         "params": {"pos": where, "max_m": generate_max(area),
                    "zones": [list(z) for z in zones],
                    "role": "site_pad", "site_id": pid},
         "wire": [gen],
         "requires": {"mods": ["UpgradeWorld"], "prefabs": [], "blobs": []},
         "expect": {"zone_ctrl": len(zones)},
         "meta": dict(meta, why=(
             "a dedicated server with no peers has generated none of these "
             "zones, so a delete-only step has nothing to delete and a "
             "flatten into them plants vegetation on unmodified ground"))},
        {"op": "objects_clear",
         "params": {"centre": where, "radius_m": round(area.radius_m, 2),
                    "ids": "*", "ignore": list(keep),
                    "role": "clearing", "site_id": pid},
         "wire": [remove],
         "requires": {"mods": ["UpgradeWorld"], "prefabs": [], "blobs": []},
         "expect": {"objects_count": {
             "ids": "*", "ignore": ",".join(keep), "pos": where,
             "max": round(area.verify_half_side_m, 2),
             "total": 0, "tolerance": 0}},
         "meta": dict(meta, verify_half_side_m=round(area.verify_half_side_m, 2),
                      unverified_annulus_m=[round(area.verify_half_side_m, 2),
                                            round(area.radius_m, 2)],
                      why=("objects_remove's pos/max filter is a vertical "
                           "cylinder on Utils.DistanceXZ; the count is the "
                           "verification box this tool has always read"))},
    ]


def settle(rc: Rcon, area: ClearArea, timeout: float = 180.0,
           stable_polls: int = 3) -> tuple[int, list[str]]:
    """Poll the verification box until the count stops changing.  Upgrade World
    runs a staged operation across frames -- `zones_generate` reports progress
    as a percentage -- so the answer immediately after `start` is not the
    answer, and MEASURED, querying too early made a 69-object pad look empty
    and the removal ran against nothing."""
    half = area.verify_half_side_m
    last, stable = None, 0
    deadline = time.time() + timeout
    count, prefabs = 0, []
    while True:
        count, prefabs = box_query(rc, area.centre_x, area.surface_y, area.centre_z, half)
        if count == last:
            stable += 1
            if stable >= stable_polls:
                break
        else:
            stable, last = 0, count
        if time.time() >= deadline:
            break
        time.sleep(SETTLE_POLL_S)
    return count, prefabs


def protected(name: str, keep: tuple[str, ...]) -> bool:
    for pattern in keep:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True
    return False


def apply(b, area: ClearArea, keep: tuple[str, ...], preset: str,
          generate: bool = True) -> dict:
    """Generate, check for locations, remove -- every write THROUGH THE
    LEDGER (`flatten.install_emit`), every read over the session's socket."""
    rc = b.srv.rc
    half = area.verify_half_side_m
    gen, remove = records(area, keep, preset)
    seqs: dict[str, int | None] = {}
    before, _ = settle(rc, area, timeout=20.0)
    generated = None
    if generate:
        # Generation has to FINISH before the removal is staged: it creates the
        # objects the removal is supposed to take.  `install_emit` waits for
        # the record's own `zone_ctrl` postcondition; `settle` is the report.
        seqs["zones_generate"] = FL.install_emit(b, gen).get("seq")
        generated, _ = settle(rc, area)
    found = locations_inside(rc, area)
    if found:
        return {
            "before": before, "after_generate": generated, "after_clear": None,
            "kept": [], "leftovers": [], "seqs": seqs,
            "locations": found, "verify_half_side_m": half,
            "verify_y_window_m": [round(area.surface_y - half, 2),
                                  round(area.surface_y + half, 2)],
        }
    seqs["objects_clear"] = FL.install_emit(b, remove).get("seq")
    after, prefabs = settle(rc, area)
    leftovers = sorted({p for p in prefabs if not protected(p, keep)})
    b.srv.command("save")
    return {
        "before": before, "after_generate": generated, "after_clear": after,
        "kept": sorted({p for p in prefabs if protected(p, keep)}),
        "leftovers": leftovers, "seqs": seqs, "locations": [],
        "verify_half_side_m": half,
        "verify_y_window_m": [round(area.surface_y - half, 2), round(area.surface_y + half, 2)],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "apply"])
    worldseed.add_arguments(ap)
    ap.add_argument("--preset")
    ap.add_argument("--placements", help="placements.yaml path; overrides --world/--preset")
    ap.add_argument("--id", action="append", required=True,
                    help="placement id; repeat to clear several pads in one pass")
    ap.add_argument("--apron", type=float, default=0.0,
                    help="extra metres cleared outside the costed footprint; "
                         "match flatten.py's --apron so levelled ground is also cleared")
    ap.add_argument("--margin", type=float, default=DEFAULT_MARGIN_M,
                    help="metres beyond the pad's half-diagonal (default %(default)s)")
    ap.add_argument("--keep", action="append", default=None,
                    help="prefab id or wildcard never removed (default: _*)")
    ap.add_argument("--actor",
                    help="the agent running this. REQUIRED for apply: the generate "
                         "and the removal are appended to the world's ledger before "
                         "they are sent (terraform/flatten.py::"
                         "INSTALLATION_WRITE_PATH_DEPENDS_ON)")
    ap.add_argument("--no-generate", action="store_true",
                    help="skip zones_generate; only meaningful where the zones are already generated")
    args = ap.parse_args()
    _seed, world = worldseed.apply(args)
    if args.op == "apply" and not args.actor:
        ap.error("apply writes through the ledger (ledger/live.py LiveBuilder) and "
                 "the record carries who ran it: pass --actor")

    if args.placements:
        path = Path(args.placements)
    else:
        if not args.preset:
            raise SystemExit("--preset is required without --placements")
        path = JUMPSTART / "worlds" / world / args.preset / "placements.yaml"
    preset = args.preset or path.parent.name
    keep = tuple(args.keep) if args.keep else DEFAULT_KEEP

    areas = []
    for pid in args.id:
        try:
            place = placement(path, pid)
            # A DECLARED DUPLICATE IS THE CANONICAL'S CYLINDER, CLEARED BY IT.
            # MEASURED on Fresh42 drive 2: deepnorth-sandbox/sandbox-harbour
            # (`duplicate_of: pre-elder#early-dock`) generated and cleared at
            # seqs 541/542, and early-dock then cleared the identical
            # cylinder -- centre (-7.5, -416.5), radius 30.22 m -- at 549/550.
            # Two writes, one bought.
            # `flatten.declared_duplicate`: the one predicate clear, flatten
            # and stock ask.
            canon = FL.declared_duplicate(place)
            if canon:
                print(f"{pid}: SKIPPED -- declares duplicate_of {canon}, whose "
                      f"own run clears this same cylinder; nothing generated "
                      f"or removed")
                continue
            # THE UNIT'S REFUSAL IS READ BY ITS FIRST WRITER, not its second:
            # see area.LEVELLING_REFUSAL_DEPENDS_ON.
            refusal = levelling_refusal(place)
            if refusal is not None:
                raise ClearAreaRefused(
                    f"{pid}: the solve recorded this pad as one the engine "
                    f"cannot level -- {refusal['over_clamp_samples']} samples "
                    f"beyond the +/-{refusal['clamp_m']:g} m apply clamp, cut "
                    f"{refusal['max_cut_m']} m, fill {refusal['max_fill_m']} m "
                    f"({refusal['clamp_source']}). Step 2 refuses it on the "
                    f"same numbers, so clearing it first is a write that buys "
                    f"nothing. Nothing was generated or removed; re-solve the "
                    f"placement.")
            areas.append(clearing_area(place, args.apron, args.margin))
        except ClearAreaRefused as exc:
            print(f"REFUSED {exc}")
            return 1

    for area in areas:
        print(area.describe())
        for cmd in commands(area, keep):
            if args.no_generate and cmd.startswith("zones_generate"):
                print(f"  (skipped) {cmd}")
                continue
            print(f"  {cmd}")
            print("  start")
    if args.op == "plan":
        return 0

    failed = False
    PostconditionFailed = FL._ledger("live").PostconditionFailed
    with FL.live_builder()(actor=args.actor, world=world) as b:
        for area in areas:
            try:
                result = apply(b, area, keep, preset, generate=not args.no_generate)
            except PostconditionFailed as exc:
                print(f"{area.placement_id}: NOT CLEARED -- {exc}")
                failed = True
                continue
            print(f"{area.placement_id}: ledger seqs {result['seqs']}")
            if result.get("locations"):
                print(f"{area.placement_id}: REFUSED -- generated location markers inside the "
                      f"clearing cylinder: {result['locations']}. Nothing was removed. The "
                      f"solver recorded {area.location_bound_m} m of clearance "
                      f"({area.bound_source}); the world disagrees, so re-solve this placement.")
                failed = True
                continue
            print(f"{area.placement_id}: verification box half-side "
                  f"{result['verify_half_side_m']} m, y window {result['verify_y_window_m']}")
            print(f"  objects in box: {result['before']} before -> "
                  f"{result['after_generate']} after zones_generate -> "
                  f"{result['after_clear']} after objects_remove")
            print(f"  kept by design: {result['kept'] or 'none'}")
            if result["leftovers"]:
                print(f"  NOT CLEARED: {result['leftovers']}")
                failed = True
            else:
                print("  pad is clear")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
