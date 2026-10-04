#!/usr/bin/env python3
"""CUT-ONLY REGRADE OVER AN EXPLICIT LATTICE SET - the write shape this tree
had no emitter for.

WHY IT EXISTS, AND IT IS NOT A CONVENIENCE.

`ribbon.py` writes a SEGMENT: a fitted longitudinal profile, a level
carriageway held across the full width, shoulders and a batter.  That is the
right instrument for a road and the WRONG one for two defects this world
actually has, and in both cases the ribbon is REFUSED BY MEASUREMENT rather
than merely clumsy:

  R4-vestvik-harbour's B STAND-OFF.  161 of 161 lattice vertices read inside
  the 6 m travelled way, 550 eight-connected edges judged, 10 over the
  tan(38 deg) slide limit -- the worst +0.1348 m on the diagonal
  (-1034,1642) -> (-1033,1643).  ELEVEN of those 161 vertices have BOTH the
  seed AND the applied surface under the 30.0 m water plane, two of them by
  18 mm and 24 mm.  A level carriageway at any datum at or above the plane
  across that corridor is a FIRST DRAIN and `ribbon.water_sign` returns 8,
  correctly.  The repair the ground will accept is 0.5256 m3 of CUT at nine
  vertices.

  T9-wttown-dockshore's BANK A.  "Lower the bench to the 30.600 deck datum"
  is a cut at the centreline and a FILL of up to +1.374 m at the south
  shoulder, because the shoulder is ALREADY at 29.226: a single datum held
  across a width is a cut at one edge and a fill at the other, and only the
  per-sample sign can tell which.  Ten of those eighteen samples have seed
  under the plane.

So the shape that is missing is: MOVE THESE NAMED VERTICES, DOWNWARD ONLY, BY
THESE NAMED AMOUNTS.  Not a datum, not a profile, not a width - a set.

WHAT MAKES IT A WRITE AND NOT A HOLE IN THE GATE STACK.  A new emitter that
skips the guards is worse than no emitter, so every guard here is the
SHIPPING function imported from where it lives, never a copy:

  cut-only          asserted on every proposed delta, and it is what makes
                    the water sign unable to refuse this write
  water sign        `ribbon.water_sign(gen, after, now)` - THE DELTA FORM
  rim transfer      `junction.rim_transfer` over THIS write's own taken set
  regrade census    `accept.instruments.regrade_census` over a PER-SAMPLE
                    SURFACE, never a collapsed datum
  seating           nothing standing may be left in the air by a cut under it
  zone union        `applied.Applied.restore_into`, which proves it
                    reproduces the live surface or raises
  clamp             `tcdata.CLAMP_M` against the GENERATED height

IT NEVER FILLS, IT NEVER DELETES AND IT NEVER PLACES.  There is no code path
here that raises a sample, removes an object or spawns one.

    regrade.py plan  --cuts <json> --name <id> --out <plan>
    regrade.py build --plan <plan> --actor <you> [--validate|--dry]
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
for p in (str(HERE), str(JUMPSTART), str(JUMPSTART / "ledger"),
          str(JUMPSTART / "terraform")):
    if p not in sys.path:
        sys.path.insert(0, p)

import tcdata                                              # noqa: E402
import ribbon as ribbonmod                                 # noqa: E402
import junction as junctionmod                             # noqa: E402
import applied as appliedmod                               # noqa: E402
from accept import instruments as AI                       # noqa: E402
import worldseed                                           # noqa: E402

# REGRADE_WORLD_DEPENDS_ON: the world this invocation was pointed at --
# `--world`, else `$VH_WORLD`, else the default.  It read `"Ulfsland"` from a
# LITERAL at three places (the `--validate` probe ledger, the schema probe
# ledger, and `_census_for`'s save scope), so RUNNING regrade.py ON ANY WORLD
# OPENED THE QUARANTINED LEDGER and measured the fresh world's cuts against
# Ulfsland's save.  `Ledger.open` CREATES the run directory and appends a
# `world_manifest` when the chain is empty, so this was a WRITE path into
# `ledger/runs/Ulfsland/`, not a read.  Same class as
# `LiveBuilder(world="Ulfsland")` and `applied.Applied(world="Ulfsland")`.
def _world(args) -> str:
    """ARGUMENT > $VH_WORLD > default, through the one resolver."""
    return getattr(args, "world", None) or worldseed.world()


WATER_LEVEL_M = ribbonmod.WATER_LEVEL_M
SLIDE_PER_M = junctionmod.SLIDE_PER_M

# A proposed delta smaller than this is not a cut, it is noise in the
# reading.  `applied.AUTHOR_TOLERANCE_M` is the same number the composition
# uses to decide whether a write AUTHORS a sample: below it the two claims
# agree to the centimetre and the ground does not move, so writing it would
# take ownership of a sample without changing it.
MIN_CUT_M = appliedmod.AUTHOR_TOLERANCE_M


# ---------------------------------------------------------------------------
def load_cuts(path: Path) -> list[dict]:
    """`{"cuts": [{"xz": [x, z], "applied_after": y}, ...]}` on the 1 m
    lattice.  `applied_after` is an ABSOLUTE height, not a delta: a delta is
    only meaningful against a stated prior, and the prior is re-read here."""
    doc = json.loads(path.read_text())
    rows = doc["cuts"] if isinstance(doc, dict) else doc
    out = []
    for r in rows:
        x, z = (float(v) for v in r["xz"])
        if abs(x - round(x)) > 1e-6 or abs(z - round(z)) > 1e-6:
            raise SystemExit(
                f"REFUSED: cut site {r['xz']} is not on the 1 m lattice. "
                "Heightmap quantises to the nearest vertex, so an off-lattice "
                "request is an ambiguous question with two answers.")
        out.append({"xz": (float(round(x)), float(round(z))),
                    "applied_after": float(r["applied_after"])})
    return out


def zones_of(sites, margin: int = 1) -> list[tuple[int, int]]:
    """Every zone holding a cut site, PLUS the zones its rim falls in.

    A sample on a zone boundary belongs to two compilers, and the rim of the
    taken set is by construction outside the taken set.
    """
    zs = set()
    for s in sites:
        x, z = s["xz"]
        for dx in range(-margin, margin + 1):
            for dz in range(-margin, margin + 1):
                zs.add(tcdata.zone_of(x + dx, z + dz))
    return sorted(zs)


# ---------------------------------------------------------------------------
def plan(args) -> int:
    sites = load_cuts(Path(args.cuts))
    zones = zones_of(sites)
    scratch = ribbonmod.SCRATCH
    scratch.mkdir(parents=True, exist_ok=True)
    patches = ribbonmod.zone_patches(zones, args.seed,
                                     scratch / f"regrade_{args.name}.bin")
    live = appliedmod.Applied(actor=args.actor)

    rows, refused = [], []
    for s in sites:
        x, z = s["xz"]
        got = live.surface_at(patches, x, z)
        gen = appliedmod.generated_at(patches, x, z)
        if got is None or gen is None:
            refused.append({**s, "why": "no applied or generated reading"})
            continue
        now = float(got[0])
        after = s["applied_after"]
        delta = after - now
        if delta > -MIN_CUT_M:
            refused.append({**s, "now": round(now, 4),
                            "after": round(after, 4),
                            "why": (f"delta {delta:+.4f} m is not a cut of at "
                                    f"least {MIN_CUT_M} m")})
            continue
        rows.append({"xz": [x, z], "now": round(now, 4),
                     "after": round(after, 4), "delta_m": round(delta, 4),
                     "generated": round(float(gen), 4),
                     "delta_from_generated_m": round(after - float(gen), 4)})

    if not rows:
        print(f"REFUSED: {len(refused)} of {len(sites)} named site(s) are not "
              f"cuts of at least {MIN_CUT_M} m against the live applied "
              f"surface, and this tool writes nothing else.")
        for r in refused[:8]:
            print(f"   {r['xz']}: {r['why']}")
        return 2

    # ---- CUT-ONLY, asserted -------------------------------------------
    fills = [r for r in rows if r["delta_m"] > 0]
    if fills:
        raise SystemExit(f"REFUSED: {len(fills)} proposed FILL(s) in a "
                         f"cut-only write: {fills[:4]}")

    # ---- THE CLAMP, against the GENERATED height ----------------------
    over = [r for r in rows
            if abs(r["delta_from_generated_m"]) > tcdata.CLAMP_M]
    if over:
        print(f"REFUSED: {len(over)} of {len(rows)} cut(s) would leave the "
              f"applied surface more than {tcdata.CLAMP_M} m from the "
              f"GENERATED height, which `TerrainComp::ApplyToHeightmap` "
              f"silently clamps -- the write would not land where it says.")
        for r in over[:6]:
            print(f"   {r['xz']}: {r['delta_from_generated_m']:+.3f} m from "
                  f"generated {r['generated']}")
        return 3

    out = {
        "name": args.name, "seed": args.seed,
        "zones": [list(z) for z in zones],
        "sites_named": len(sites), "sites_written": len(rows),
        "sites_refused": len(refused), "refused": refused,
        "cut_worst_m": min(r["delta_m"] for r in rows),
        "cut_total_m3": round(sum(-r["delta_m"] for r in rows), 4),
        "clamp_m": tcdata.CLAMP_M,
        "min_cut_m": MIN_CUT_M,
        "population": (f"the {len(rows)} of {len(sites)} named lattice "
                       f"vertex(es) that are a CUT of at least {MIN_CUT_M} m "
                       f"against the live applied surface, in "
                       f"{len(zones)} zone(s)"),
        "rows": rows,
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("rows", "refused")}, indent=1))
    return 0


# ---------------------------------------------------------------------------
def build(args) -> int:
    doc = json.loads(Path(args.plan).read_text())
    rows = doc["rows"]
    zones = [tuple(z) for z in doc["zones"]]
    scratch = ribbonmod.SCRATCH
    patches = ribbonmod.zone_patches(zones, doc["seed"],
                                     scratch / f"regrade_{doc['name']}.bin")

    from live import LiveBuilder                           # noqa: PLC0415
    import schema                                          # noqa: PLC0415

    want = {(float(r["xz"][0]), float(r["xz"][1])): float(r["after"])
            for r in rows}

    # ---- RE-READ THE PRIOR STATE.  A plan is not evidence about a world
    # it was not written against, and the applied surface moves whenever
    # anybody writes.
    live = appliedmod.Applied(actor=args.actor)
    taken_now, taken_gen, taken_after = {}, {}, {}
    moved = 0
    stale = []
    for (x, z), after in sorted(want.items()):
        got = live.surface_at(patches, x, z)
        gen = appliedmod.generated_at(patches, x, z)
        if got is None or gen is None:
            stale.append({"xz": [x, z], "why": "unreadable now"})
            continue
        now = float(got[0])
        if after > now - MIN_CUT_M:
            stale.append({"xz": [x, z], "now": round(now, 4),
                          "after": round(after, 4),
                          "why": "no longer a cut against the live surface"})
            continue
        taken_now[(int(x), int(z))] = now
        taken_gen[(int(x), int(z))] = float(gen)
        taken_after[(int(x), int(z))] = after
        moved += 1
    print(f"plan freshness: {moved} of {len(want)} named vertex(es) are still "
          f"a cut against the live applied surface; {len(stale)} are not")
    for s in stale[:8]:
        print(f"   STALE {s['xz']}: {s['why']}")
    if not moved:
        print("REFUSED: nothing in this plan is still a cut. Re-plan.")
        return 2

    # ---- THE WATER SIGN, as a DELTA ------------------------------------
    wsign = ribbonmod.water_sign(taken_gen, taken_after, taken_now)
    print(f"water sign: {wsign['verdict']} -- {wsign['submerged_samples']} of "
          f"{wsign['moved_samples']} moved sample(s) sit on ground the SEED "
          f"drew below {wsign['plane_m']} m; {wsign['drained_samples']} would "
          f"be RAISED to or above it, "
          f"{wsign['already_drained_not_deepened']} were already above it and "
          f"are not deepened; judged as a {wsign['judged_as']}")
    if wsign["verdict"] == "REFUSED":
        print(f"REFUSING: {wsign['population']}")
        return 8

    # ---- THE RIM.  A restitution transfers its discontinuity to the rim of
    # the reclaimed set, so the rim is judged over THIS write's taken set and
    # as a DELTA -- caused, not pre-existing.
    def _height_now(sx: int, sz: int):
        got = live.surface_at(patches, float(sx), float(sz))
        return None if got is None else float(got[0])

    def _author_now(sx: int, sz: int):
        _d, w = live.sample(int(sx), int(sz), by="author")
        return None if w is None else w.get("name")

    # A RESTITUTION WRITES EXACTLY `taken_after` -- the plan's still-cut
    # vertices -- so nothing outside the taken set moves and the rim's after
    # surface IS its current one.  Stated, not defaulted: see
    # junction.RIM_STEP_DEPENDS_ON, where `ribbon.py`'s answer differs.
    rim = junctionmod.rim_transfer(taken_after, _height_now, _author_now,
                                   height_after=_height_now)
    print(f"rim transfer: {rim['verdict']} -- this write MOVES "
          f"{rim['taken_samples']} sample(s); their rim is "
          f"{rim['rim_samples']} sample(s), {rim['rim_pairs_judged']} pair(s) "
          f"judged, {rim['rim_samples_unreadable']} unreadable; "
          f"{rim['caused']} step(s) CAUSED, {rim['pre_existing']} "
          f"pre-existing")
    for r in rim["caused_rows"][:8]:
        print(f"   CAUSED {r['step_after_m']} m at rim {r['rim_xz']} from "
              f"taken {r['taken_xz']}, was {r['step_before_m']} m, rim "
              f"ground owned by {r['rim_authored_by']}")
    if rim["verdict"] == "REFUSED":
        print(f"REFUSING: this write would leave {rim['caused']} step(s) a "
              f"walker cannot take AT THE RIM of the ground it takes, worst "
              f"{rim['worst_caused_m']} m at {rim['worst_caused_at']} against "
              f"{SLIDE_PER_M:.4f} m. Nothing has been sent.")
        return 7

    # ---- THE REGRADE CENSUS, OVER A PER-SAMPLE SURFACE -----------------
    #
    # `target` is a float OR A SURFACE.  DO NOT COLLAPSE THIS ONE TO A
    # DATUM: the whole reason this emitter exists is that a single datum
    # held across a width is a cut at one edge and a fill at the other, and
    # a census asked about the mean of a cut set answers CLEAR over a write
    # that undermines a piece by the worst of them.
    #
    # BOTH SIGNS, and for a cut-only write the dangerous one is UNDERMINES:
    # "a cut cannot bury anything, so it is safe" is true and insufficient.
    # A cut that leaves a standing piece's foot more than one walker step in
    # the air is how a 212-piece watchtower ended up in 13.5 m of air.
    # AND IT RUNS ON EVERY PATH.  It used to be SKIPPED under --validate and
    # --dry, which left the two halves of the proof permanently unavailable
    # at once: the only path that could say the RECORD was well formed was
    # also the only path that could not say whether the CUT undermines a
    # standing piece, and the path that answered the second never asked the
    # first.  A rehearsal that cannot fail is not a rehearsal.
    #
    # The objection it was skipped for is real and is answered by STATING
    # THE PROVENANCE rather than by declining to measure: the census is
    # taken off the last FLUSHED save, so under --validate/--dry it cannot
    # see a write that has not been saved yet.  That is a named limit on a
    # real reading.  "SKIPPED" is not a limit, it is an absence, and an
    # absence reads as fine.
    if args.validate or args.dry:
        print("regrade census: taken off the LAST FLUSHED SAVE -- "
              "--validate/--dry opens no server, so a terrain write that has "
              "not been saved yet is invisible to it. The reading is real "
              "and its limit is stated; the live path below re-takes it.")
    import accept.sections.structures as AS                # noqa: PLC0415
    cen = _census_for(args)
    geom, build_pieces = AI.load_geometry()
    rect = [(float(x), float(z)) for (x, z) in sorted(taken_after)]
    tgt = {(float(x), float(z)): v for (x, z), v in taken_after.items()}
    gnd = {AS.gkey(float(x), float(z)): v
           for (x, z), v in taken_now.items()}
    xs = [p[0] for p in rect]
    zs = [p[1] for p in rect]
    x0, x1 = min(xs) - 8.0, max(xs) + 8.0
    z0, z1 = min(zs) - 8.0, max(zs) + 8.0
    solids = [s for s in AI.solids_from_census(cen, geom)
              if s.name in build_pieces and s.verts
              and s.xmax >= x0 and s.xmin <= x1
              and s.zmax >= z0 and s.zmin <= z1]
    rg = AI.regrade_census(rect, tgt, gnd, solids, geom,
                           AS.Columns(geom), AS.gkey, AS.piece_columns)
    print(f"regrade census: {rg['verdict']} over {rg['rect_samples']} "
          f"sample(s) this write authors, of which {rg['moving']} MOVE "
          f"({rg['fills']} fill / {rg['cuts']} cut); target is a "
          f"{rg['target_kind']}; {rg['samples_without_a_target']} "
          f"sample(s) with no target; "
          f"{rg['solids_over_a_moving_sample']} build-piece solid(s) "
          f"cover a moving sample, {rg['ground_bearing']} "
          f"ground-bearing, {rg['aloft_and_not_in_the_question']} "
          f"carried by something else; {len(rg['buries'])} buries / "
          f"{len(rg['undermines'])} undermines; census {len(cen.rows)} "
          f"ZDOs, {len(solids)} build pieces in the window")
    for r in rg["buries"][:6]:
        print(f"   BURIES {r['prefab']} at {r['at']} span {r['span']} "
              f"ground {r['ground']} -> {r['target']}")
    for r in rg["undermines"][:6]:
        print(f"   UNDERMINES {r['prefab']} at {r['at']} span "
              f"{r['span']} ground {r['ground']} -> {r['target']} "
              f"({r['foot_in_air_m']:.3f} m of foot in the air)")
    if rg["verdict"] != "CLEAR":
        print(f"REFUSING: this cut would bury {len(rg['buries'])} and "
              f"undermine {len(rg['undermines'])} standing build "
              f"piece(s). Nothing has been sent. Retire and re-spawn the "
              f"pieces, or move the cut off them; do not delete a "
              f"building.")
        return 9
    if rg["fills"]:
        print(f"REFUSING: the census counts {rg['fills']} FILL(s) in a "
              f"write that declares itself cut-only. Nothing sent.")
        return 9

    # ---- THE COMPILERS -------------------------------------------------
    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    for (x, z), after in sorted(taken_after.items()):
        zx, zz = tcdata.zone_of(float(x), float(z))
        comp = comps.get((zx, zz))
        if comp is None:
            comp = comps[(zx, zz)] = tcdata.Compiler(zone_x=zx, zone_z=zz)
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, float(x), float(z))
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            raise SystemExit(f"vertex {(x, z)} is outside zone {(zx, zz)}")
        comp.set_height(gx, gy, after - taken_gen[(x, z)])

    entries = []
    union_evidence: list = []
    for (zx, zz), comp in sorted(comps.items()):
        # THE UNION, through the instrument that PROVES it reproduces the
        # live surface.  A terrain write deletes the compiler before it
        # spawns, so a blob that does not carry the zone's prior claims
        # forward DESTROYS them.
        restore = live.restore_into(zx, zz, comp)
        cx, cz = comp.centre
        patch = patches[f"z_{zx}_{zz}"]
        op_y = float(np.median([comp.level_delta[i] + patch.heights[i]
                                for i in range(tcdata.SAMPLES)
                                if comp.modified_height[i]]))
        blob = comp.blob(op_y)
        opx, opy, opz, opr = comp.op_record(op_y)
        # THE UNION, DECLARED AS WELL AS PERFORMED.  `restore_into` walks the
        # zone's per-sample `owner`, reads each prior claim's blob and
        # inherits its samples - and `_zone_clobber_check` refuses the append
        # unless the record NAMES those priors, because a terrain write
        # DELETES the compiler before it spawns and from the chain's side an
        # undeclared union is indistinguishable from a clobber.  MEASURED
        # 2026-09-22: two correct writes refused at `Ledger.append` for this
        # alone - seq 5393 zone [0,0], 12 priors named by the guard over
        # 1,494 samples inherited and all 1,494 checked against the live
        # applied surface, and zone [-14,30], 5 priors over 1,061.  The
        # function knew the answer and the return value threw it away.
        #
        # THE KEY IS OMITTED ON A VIRGIN ZONE, never sent empty: `[]` is a
        # claim that this write merged nothing, and "there was nothing to
        # merge" is a different statement.
        entry = {
            "zone": [zx, zz], "centre": [cx, cz],
            "data_entry": f"regrade_{doc['name']}_z{zx}_{zz}".replace("-", "_"),
            "blob": blob,
            "blob_sha256": hashlib.sha256(blob).hexdigest(),
            "generated_heights_sha256": ribbonmod.generated_digest(patch),
            "op_record": {"operations": comp.operations,
                          "last_op_point": [opx, opy, opz],
                          "last_op_radius": opr},
            "counts": comp.counts(),
            "merge_policy": restore["merge_policy"],
            "samples_inherited": restore["samples_inherited"],
            "samples_written_by_us": restore["samples_written_by_us"],
        }
        if restore["merged_from"]:
            entry["merged_from"] = restore["merged_from"]
        entries.append(entry)
        union_evidence.append(((zx, zz), {
            "samples_inherited": restore["samples_inherited"],
            "samples_written_by_us": restore["samples_written_by_us"],
            "samples_checked_against_applied":
                restore["samples_checked_against_applied"],
            "merged_from": restore["merged_from"],
            "merged_sample_counts": restore["merged_sample_counts"],
            "priors_with_no_sample_on_the_compiler":
                restore["priors_with_no_sample_on_the_compiler"],
        }))
        print(f"  zone {(zx, zz)}: wrote "
              f"{restore['samples_written_by_us']} sample(s), inherited "
              f"{restore['samples_inherited']}, "
              f"{restore['samples_checked_against_applied']} checked against "
              f"the live applied surface"
              + (f", merged_from {len(restore['merged_from'])} prior claim(s) "
                 f"in this zone of which "
                 f"{len(restore['priors_with_no_sample_on_the_compiler'])} own "
                 f"NO sample on the compiler and are named because this op "
                 f"deletes them, not because it inherits them"
                 if restore["merged_from"] else ", no prior claim in this zone"))

    # ---- THE LOCATION CHECK.  A terrain write states its check or it is not
    # written: `flatten.py` had none and cut 6.65 m of ground out from under
    # a LocationProxy holding a buried treasure chest.  This is the SHIPPING
    # instrument, `ribbon.location_check`, over THIS write's own taken set.
    #
    # `road_written` IS the taken set and `road_half_width_m` is 0.0, and
    # both of those are the truth here rather than a convenience: a cut-only
    # lattice set has no carriageway and no batter, so the tested samples
    # ARE the footprint and `violations_for_samples` measures the minimum
    # distance from them.  A half-extent about those samples would claim the
    # write reaches past its own outermost vertex.  `clipped` is empty
    # because nothing here is rasterised and nothing is clipped.
    written = [(float(x), float(z)) for (x, z) in sorted(taken_after)]
    loc = ribbonmod.location_check(comps, written, road_written=written,
                                   clipped={}, road_half_width_m=0.0)
    print(f"location check: {loc.get('verdict')} -- "
          f"{loc.get('samples_tested_carriageway')} sample(s) tested against "
          f"the location dump {loc.get('dump')}")
    if loc.get("verdict") != "clear":
        print(f"REFUSING: this cut is inside a location's stand-off. "
              f"{loc.get('method')}. Nothing has been sent.")
        return 6

    params = {
        "name": f"regrade_{doc['name']}".replace("-", "_"),
        "role": "regrade_cut",
        "paint": None,
        "datum": ("an explicit CUT-ONLY lattice set: each named vertex is "
                  "lowered to its own target height. NOT a datum and NOT a "
                  "fitted profile -- a single datum held across a width is a "
                  "cut at one edge and a fill at the other, which is why "
                  "this write shape exists. The per-vertex targets are in "
                  "`profile.nodes`"),
        # THE FOOTPRINT, IN THE ONE SHAPE A REPLAY CAN RE-DERIVE FROM.
        #
        # `target_y` XOR `profile` is required, and this write is neither a
        # pad nor a fitted road: it has one absolute target PER VERTEX.
        # `profile.nodes` is `[[x, z, y], ...]` - the ledger's node order,
        # never the wire's - and it is exactly that set.  `half_width_m` and
        # `shoulder_m` are 0.0 because a named vertex has no width: the
        # nodes ARE the footprint, not a centreline to be swept.  `interp`
        # is "none" for the same reason - there is nothing between two
        # named vertices to interpolate, and a replay that interpolated
        # would invent samples this write never claimed.
        "profile": {"nodes": [[float(x), float(z),
                               round(taken_after[(x, z)], 4)]
                              for (x, z) in sorted(taken_after)],
                    "half_width_m": 0.0, "shoulder_m": 0.0,
                    "interp": "none"},
        "max_cut_m": round(min(r["delta_m"] for r in rows), 4),
        "max_fill_m": 0.0,
        "over_clamp": 0,
        "location_check": loc,
        "entries": entries,
    }
    # THE EVIDENCE GOES IN `meta`, WHICH IS WHERE THE SCHEMA SAYS IT GOES.
    #
    # `cut_sites`, `cut_total_m3`, `water_sign` and `rim_transfer` were in
    # `params`, and `params` is the half of the record the schema POLICES:
    # every one of them came back as "is not in the schema (... Put
    # unmodelled data in `meta`, which is free-form and unverified)".  So
    # the water sign and the rim transfer - the two guards that make this
    # write legal - were being filed in the one place that rejects them,
    # and the record carrying them could not validate.
    evidence = {
        "cut_sites": moved,
        "cut_total_m3": round(sum(taken_now[k] - taken_after[k]
                                  for k in taken_after), 4),
        "water_sign": {k: v for k, v in wsign.items()
                       if k not in ("drained", "already_drained_xz")},
        "rim_transfer": {k: v for k, v in rim.items()
                         if not isinstance(v, list)},
        # THE UNION'S TWO POPULATIONS, BOTH CARRIED.  `merged_from` in
        # `params` is the guard's question - what this op DELETES - and the
        # schema validates its shape.  These are the other half: how many
        # samples of each prior this restore actually still carries, and
        # which priors carry NONE.  A 0 is the interesting row: a prior this
        # write wholly supersedes, visible here instead of being the
        # difference between two counts.  Evidence, so it goes in `meta`.
        "zone_union": {f"{zx},{zz}": u for (zx, zz), u in union_evidence},
    }
    wire = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
            f"-prefab _TerrainCompiler -force" for e in entries]
    wire += [f"spawn_object _TerrainCompiler "
             f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
             f"data={e['data_entry']}" for e in entries]

    REQUIRES = {"mods": ["WorldEditCommands", "UpgradeWorld",
                         "ServerDevcommands", "ValheimRcon"],
                "prefabs": ["_TerrainCompiler"]}

    if args.validate:
        from writer import Ledger                          # noqa: PLC0415
        led = Ledger.open(_world(args), actor=args.actor)
        shas = [hashlib.sha256(e["blob"]).hexdigest() for e in entries]
        probe = json.loads(json.dumps(
            params, default=lambda o: None))   # blobs are bytes; drop them
        for e in probe["entries"]:
            e.pop("blob", None)
            # THE ZONE PROBE, STOOD IN FOR AND SAID SO.  `--validate` opens
            # no server, so it cannot OBSERVE a `_ZoneCtrl`; the live path
            # below runs the real `objects_count` and refuses on it.  Named
            # here so a reader cannot mistake the stand-in for evidence.
            cx, cz = e["centre"]
            e["zone_generated_before"] = True
            e["zone_generated_probe"] = (
                f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1")
        rec = led.build("terrain_write", params=probe, wire=wire,
                        requires={**REQUIRES, "blobs": shas},
                        expect={"terrain_compiler": True},
                        meta={"validate_only": True,
                              "zone_generated_probe_is_a_stand_in": True,
                              **evidence})
        bad = schema.validate(rec)
        print(f"schema.validate(terrain_write): "
              f"{len(bad)} problem(s)" if bad else
              "schema.validate(terrain_write): 0 problems")
        for b_ in bad:
            print(f"   {b_}")
        print("VALIDATED, nothing appended and nothing sent.")
        return 0 if not bad else 4

    with LiveBuilder(actor=args.actor, dry=args.dry) as b:
        players = b.srv.command("players")
        print(f"players immediately before the record:\n{players}")
        # ---- STEP 0, AND IT IS A HARD PRECONDITION.  A terrain write into a
        # zone that was never generated plants that zone's whole vegetation
        # set on the UNMODIFIED generated height.  Each zone is probed
        # INDIVIDUALLY with the exact command that observes it, because
        # PlaceZoneCtrl puts one `_ZoneCtrl` at the zone CENTRE and
        # `objects_count`'s filter is an XZ cylinder.
        ungenerated = []
        for e in entries:
            cx, cz = e["centre"]
            probe = f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1"
            got, _ = b.srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
            e["zone_generated_before"] = bool(got >= 1)
            e["zone_generated_probe"] = probe
            e["zone_ctrl_observed"] = int(got)
            if got < 1:
                ungenerated.append(e)
        if ungenerated:
            xs = [e["centre"][0] for e in ungenerated]
            zs = [e["centre"][1] for e in ungenerated]
            px, pz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
            reach = max(math.hypot(e["centre"][0] - px, e["centre"][1] - pz)
                        for e in ungenerated) + 1.0
            g = b.emit("zones_generate",
                       params={"pos": [px, pz], "max_m": round(reach, 1),
                               "zones": [e["zone"] for e in ungenerated],
                               "role": "regrade_cut"},
                       wire=[f"zones_generate pos={px:g},{pz:g} "
                             f"max={reach:g}"],
                       expect={"zone_ctrl": len(ungenerated)},
                       meta={"why": "paint alpha is NOT honoured on a zone "
                                    "that has never been generated, and the "
                                    "zone's whole vegetation set is planted "
                                    "on the UNMODIFIED generated height"})
            print(f"  zones_generate {len(ungenerated)} zone(s) -> "
                  f"{g['status']}")
            for e in ungenerated:
                cx, cz = e["centre"]
                got, _ = b.srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
                e["zone_generated_before"] = bool(got >= 1)
                e["zone_ctrl_observed"] = int(got)
                if got < 1:
                    raise SystemExit(
                        f"zone {e['zone']} shows {got} _ZoneCtrl after "
                        f"zones_generate: refusing to write terrain into a "
                        f"zone that is not generated. Probe: "
                        f"{e['zone_generated_probe']}")
        # ---- THE RECORD IS VALIDATED BEFORE IT IS SENT, NOT INSTEAD OF
        # BEING SENT.  `--validate` proved the record and wrote nothing;
        # this path wrote and proved nothing, so the only route that could
        # tell you the record was malformed was the one that did not write
        # it.  MEASURED 2026-09-22: this emitter's record failed
        # `schema.validate` on 24 counts - a missing location check, a
        # missing footprint, four evidence fields in the policed half of the
        # record, an unregistered role, and no zone-generation evidence for
        # any zone - and eleven terrain writes had already gone out through
        # a path that never asked.
        from writer import Ledger                          # noqa: PLC0415
        _led = Ledger.open(_world(args), actor=args.actor)
        _shas = [hashlib.sha256(e["blob"]).hexdigest() for e in entries]
        _probe_rec = json.loads(json.dumps(params, default=lambda o: None))
        for e in _probe_rec["entries"]:
            e.pop("blob", None)
        _bad = schema.validate(_led.build(
            "terrain_write", params=_probe_rec, wire=wire,
            requires={**REQUIRES, "blobs": _shas},
            expect={"terrain_compiler": True}, meta=evidence))
        if _bad:
            print(f"REFUSING: the record this write would append fails "
                  f"schema.validate on {len(_bad)} count(s). Nothing has "
                  f"been sent.")
            for b_ in _bad:
                print(f"   {b_}")
            return 5
        print(f"schema.validate(terrain_write): 0 problems over "
              f"{len(entries)} zone entry(ies)")
        shas = [b.blob(e.pop("blob"),
                       note=f"TCData regrade {doc['name']} zone {e['zone']}")
                for e in entries]
        r = b.emit("terrain_write", params=params, wire=wire,
                   requires={**REQUIRES, "blobs": shas},
                   expect={"terrain_compiler": True},
                   meta={"players_before": players.strip()[:400],
                         "instrument": ("roads/regrade.py -- cut-only "
                                        "regrade over an explicit lattice "
                                        "set"),
                         "why": doc.get("why", ""),
                         **evidence})
        print(f"terrain_write seq {r.get('seq')} -> {r['status']}")
        # THE FLUSH'S POSTCONDITION IS THE COMPILERS, COUNTED WHERE THEY
        # STAND - not `terrain_compiler: True`, which `replay.check_expect`
        # answers by walking `params["entries"]` AND A `save` RECORD HAS
        # NONE.  MEASURED 2026-09-22: it raised KeyError 'entries' AFTER the
        # save had already appended, so the flush went in unverified and the
        # emitter died on its own postcondition.  This is `ribbon.py:5431`'s
        # shape: one `_TerrainCompiler` within 31 m of each zone centre this
        # write touched, which is a claim about the world and is checkable.
        s = b.emit("save", params={"role": "regrade_cut"}, wire=["save"],
                   expect={"prefab_count": [
                       {"prefab": "_TerrainCompiler",
                        "pos": [e["centre"][0], e["centre"][1]],
                        "max": 31, "count": 1} for e in entries]},
                   meta={"why": "flush before the gate re-reads the save"})
        print(f"save seq {s.get('seq')} -> {s['status']}")
    return 0


def _census_for(args):
    """The whole-save census `regrade_census` needs, taken FRESH off the save
    through the harness on a frozen copy -- never the ledger's own blobs."""
    class A:
        pass
    a = A()
    a.world = _world(args)
    a.seed = args.seed
    a.vh_src = AI.VH_SRC_DEFAULT
    a.world_src = args.world_src or AI.world_src_default(a.world)
    a.sandbox = "/tmp/waterproof/regrade"
    a.zone_batch = 110
    live = Path(a.world_src)
    AI.require_world_save(live, a.world)
    fp = AI.save_fingerprint(live)
    cache = Path(AI.CACHE_DEFAULT) / a.world
    cache.mkdir(parents=True, exist_ok=True)
    a.world_src = str(AI.snapshot(live, cache / fp / "world"))
    return AI.census(cache, fp, a)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["plan", "build"])
    ap.add_argument("--cuts")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--name", default="cut")
    ap.add_argument("--seed", default=ribbonmod.SEED)
    ap.add_argument("--world-src", default=None)
    # `--seed` already exists above with its own default; only the WORLD was
    # unaskable.  `default=None` on purpose so argparse cannot shadow
    # `$VH_WORLD` -- the precedence lives in `_world()`, in one place.
    ap.add_argument("--world", default=None,
                    help="world / ledger name (default: $VH_WORLD or "
                         f"{worldseed.DEFAULT_WORLD})")
    ap.add_argument("--actor", required=True)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    return plan(args) if args.op == "plan" else build(args)


if __name__ == "__main__":
    raise SystemExit(main())
