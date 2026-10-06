#!/usr/bin/env python3
"""Stand a settlement up in the live world, through the ledger and nothing else.

THE WRITE PATH, and why every step of it belongs to somebody else's module:
this file owns the SETTLEMENT -- which bodies, where, at what yaw, on what pad.
It owns none of the primitives:

    zone generation + clearing   tools/jumpstart/clearing/      (GroundTruth)
    the TCData terrain write     tools/jumpstart/terraform/     (GroundTruth)
    the blueprint spawn plan     tools/jumpstart/blueprints/    (shared)
    the wire, the log, the guards tools/jumpstart/ledger/       (BuildLedger)

So this is a DRIVER. It imports those as libraries to synthesise the exact wire
they would send, and then emits every one of those commands through
`ledger.live.LiveBuilder`, which appends and fsyncs the record BEFORE sending
and raises if the postcondition does not hold. Nothing here touches RCON
directly: an ad-hoc write is invisible to the ledger's one-compiler-per-zone
and `flatten: FORBIDDEN` guards, which would make them decoration.

A placements document is synthesised per settlement under `placements/` in THIS
directory, because `worlds/Ulfsland/*/placements.yaml` is not ours to write and
`clear.py` / `flatten.py` both accept `--placements` for exactly this.

ORDER, which is the proven one and is not negotiable:
    0 zones_generate   per pad -- MEASURED: paint alpha is NOT honoured in an
                       ungenerated zone; 1,681 cleared samples still grew
                       Bush01 x15, Beech1 x15 and eleven more prefabs, up to
                       7.68 m above the pad, as permanent ZDOs
    1 objects_remove   the vegetation, before the ground moves under it
    2 terrain_write    the pad, levelled to the footprint median
    3 spawn_plan       the body, bottom-up in Y so WearNTear support exists
    4 portal + sign    one per settlement, unique non-empty tag <= 10 chars

Usage:
    ./build.py placements                 # synthesise the documents only
    ./build.py plan   --site stenvik      # every command, nothing sent
    ./build.py apply  --site stenvik [--limit N] [--from N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "clearing"))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "blueprints"))
sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(REPO / "tools"))

import worldseed  # noqa: E402
import clearance  # noqa: E402
import terrain1m as T  # noqa: E402
import tcdata  # noqa: E402  terraform/tcdata.py, the TCData codec
import flatten as FL  # noqa: E402  terraform/flatten.py, driven as a LIBRARY
import place as PLACE  # noqa: E402  terraform/place.py, for its measured batching
import verify_placement as VP  # noqa: E402  blueprints/, for plan_rows / bottoming
import bodies as BD  # noqa: E402  settlements/bodies.py, for PAD_MARGIN_M

PLACEMENTS = HERE / "placements"
# PER WORLD.  A plan file here is read back by `live_verify` and
# `retire_partial.py` by SITE name, and site names recur across worlds
# (`wt-north` is a unit of Ulfsland and of Fresh42).  MEASURED on Fresh42
# drive 2: `/tmp/settle/build/wt-north/wt-north.plan` was Ulfsland's, from
# 2026-09-16, pieces at x 1,182 -- 1,050 m from Fresh42's wt-north.  `main`
# rebinds this after `worldseed.apply`.
SCRATCH_ROOT = Path("/tmp/settle/build")
SCRATCH = SCRATCH_ROOT / worldseed.world()
# The generator location dump, which EVERY location gate in this file reads:
# `clear_radius_room` (the hard gate on the irreversible `objects_remove`),
# `placements_doc`'s `nearest_location_m` (clearing's own ClearAreaRefused
# bound), `sample_verdict` and `pad_location_check`.
#
# IT IS ONE SEED'S MEASUREMENT AND IT WAS READ FROM A PATH THAT NAMES NO
# SEED.  The default was `/tmp/settle/loc3/f6fe167f4fcd.json`, whose own
# header reads `"seed": "Pirate68"`.  MEASURED on Fresh42 drive 2
# (`/tmp/regen2/d2_build8.log`): 13 settlement units were REFUSED on
# Pirate68's locations -- stenvik 6 (ShipSetting01, StoneCircle), vestvik 3
# plus villa-1's ClearAreaRefused, wt-north (ShipSetting01), wt-east
# (StoneTowerRuins03) -- and, offline over the 49 units of
# `plan.Fresh42.json`, 14 refuse under that dump and 0 under Fresh42's own
# (`ledger/runs/Fresh42/pipeline/loc/74cb38be48da.json`, the file the plan
# records as `locations_dump`).  The other face of the same read is worse: no
# Fresh42 location guarded a Fresh42 clearing cylinder.
#
# So the dump is THE PLAN'S OWN `locations_dump` -- the plan is refused unless
# it declares this run's seed -- and the dump is refused unless ITS header
# declares this run's seed, through `worldseed.require_scope`.
#
# `SETTLE_LOCATIONS_DUMP` still overrides, because a live sweep can correct a
# mod-free dump's POSITIONS: MEASURED at lh-north, the dump put a
# ShipSetting01 17.9 m from the only LocationProxy a live census found, and
# refusing a build on a demonstrably wrong coordinate is a wrong answer with a
# guard's authority.  The override is scope-checked exactly like the default.
LOCATIONS_ENV = "SETTLE_LOCATIONS_DUMP"
# LOCATIONS_SCOPE_DEPENDS_ON, each term neutered in `selftest`:
#   plan_locations_dump -- the dump is the one the plan was solved against;
#                          without it the shipped path returns.
#   dump_declared_seed  -- the dump's own `seed` header is compared with the
#                          run's; without it a foreign dump is accepted.
LOCATIONS_SCOPE_DEPENDS_ON = ("plan_locations_dump", "dump_declared_seed")
# seed -> the plan-declared dump `main` bound for the plan it is building, so
# a reader handed no plan answers with the SAME file (`--plan-json` may name a
# plan other than this seed's slot).
_BOUND_LOCATIONS: dict[str, str] = {}
_DUMP_SEED: dict[str, object] = {}


def locations_dump(plan: dict | None = None, *, seed_: str | None = None,
                   depends_on: tuple = LOCATIONS_SCOPE_DEPENDS_ON) -> str:
    """The generator location dump for THIS seed, or a refusal.

    `plan` is the settlement plan this run builds; absent, the dump `main`
    bound for this seed, else this seed's plan slot."""
    s = seed_ if seed_ is not None else worldseed.seed()
    path = os.environ.get(LOCATIONS_ENV)
    if not path and "plan_locations_dump" not in depends_on:
        # The shipped rule, for the neuter: the DEFAULT seed's dump whatever
        # the run's seed, asked of the one resolver at call time.
        path = clearance.location_dump(worldseed.DEFAULT_SEED)
    if not path and plan is None:
        path = _BOUND_LOCATIONS.get(s)
        if not path:
            plan = json.loads(worldseed.plan_path(s).read_text())
    if not path:
        path = plan.get("locations_dump")
        if not path:
            raise SystemExit(
                f"REFUSING: the settlement plan for seed {s!r} records no "
                f"`locations_dump`, so there is no dump its units were sited "
                f"against and no location gate here can be armed. Re-solve it "
                f"with settlements/plan.py.")
    path = str(path)
    if path not in _DUMP_SEED:
        _DUMP_SEED[path] = json.loads(Path(path).read_text()).get("seed")
    found = _DUMP_SEED[path] if "dump_declared_seed" in depends_on else s
    worldseed.require_scope(
        "generator location dump", path, found_seed=found, seed_=s,
        world_=None, produced_by="tools/seedscan/run_locscan.sh (pipeline "
                                 "stage `loc`), recorded by settlements/"
                                 "plan.py as the plan's `locations_dump`")
    if plan is not None and depends_on == LOCATIONS_SCOPE_DEPENDS_ON:
        _BOUND_LOCATIONS[s] = path
    return path


PAINT = "paved_cleared"
APRON_M = 0.0


# ---------------------------------------------------------------------------
# PLAN_SCOPE_DEPENDS_ON -- THE SEED THE PLAN WAS SOLVED ON, AND THE SEED THIS
# RUN IS BUILDING.  NOT THE FILE PATH.
#
# `--plan-json` defaulted to `HERE/plan.json`: ONE document, no world in its
# name, and its contents never compared to anything.  That document declares
# `seed: "Pirate68"`, `seed_hash: 147627509`, `patch_file:
# /tmp/settle/h1m.bin`, and Pirate68 IS Ulfsland's seed
# (`worldseed.DEFAULT_SEED`).  So `--seed Fresh42 --world Fresh42` read
# ULFSLAND'S SOLVED SITES and wrote them into Fresh42's ground, and no gate in
# the tree contradicted it.  The key named a superset -- "the settlement plan"
# -- of what x, z and target_y actually depend on: the world they were solved
# against.
#
# MEASURED 2026-09-24 off the two recorded ledgers.  This one reached the
# GROUND rather than staying latent:
#
#   * every one of Fresh42's 45 site pads carries Ulfsland's `target_y` BYTE
#     FOR BYTE -- wt-spawn 76.04 in both worlds, hognest-castle 110.95 in
#     both, stenvik-hall-1 47.03 in both.
#   * Fresh42's own generated ground at wt-spawn's pad centre (114, -108) is
#     33.785 m (field `Fresh42_1m.bin`, 7x7 neighbours 32.03-34.63, so it is
#     READ and not the clamp's 0.0).  That pad commanded `max_fill_m` 45.527
#     with `over_clamp` 441.  On Ulfsland the SAME datum cost 0.886 m of fill
#     and `over_clamp` 0.
#   * of the 30 pad centres whose Fresh42 ground is readable, 30 disagree with
#     their own datum by more than 3.9 m -- worst +43.355 m (stenvik-hall-1)
#     and -37.895 m (tree-near-1) -- and 22 of the 30 sit BELOW the 30 m water
#     plane.
#   * Ulfsland: 73 pad writes, `over_clamp > 0` in ZERO of them.
#     Fresh42:  45 pad writes, `over_clamp > 0` in FORTY-THREE.
#
# `roads/junction.py` was the only gate in the tree that caught any of it, and
# only because one road happened to run through one pad rectangle:
# S11-southcape-lhsouth#2 against wt-spawn, gap 0.0 m, spread 44.955 m against
# a 0.7813 m allowance.  A gate that sees a class only when a road crosses it
# is not the check for that class.  These two are.
#
# AND THE CORRECT DOCUMENT WAS ON DISK THE WHOLE TIME, WHICH IS WHAT MAKES
# THIS A KEY DEFECT RATHER THAN A MISSING MEASUREMENT.
# `settlements/plan.Fresh42.json`, written 2026-09-23 11:04:49, declares
# `seed: "Fresh42"`, `world: "Fresh42"`, `seed_hash: -1894280788` (the
# field's own hash) and its own `patch_file`, and it holds a Fresh42 solution:
# stenvik-hall-1 at (350.1, -208.8) target_y 31.62, not (481.1, 920.4)
# target_y 47.03 -- 1,136 m away.  wt-spawn at (-986.1, -772.8) target_y
# 82.31, not (114, -108) target_y 76.04.  43 of its 43 units carry
# `over_clamp` 0, worst fill 6.53 m and worst cut 5.06 m, all inside the
# clamp.  The build read the 2026-09-16 Pirate68 file because the default
# named a FILE and not a SEED, so `worldseed.plan_path` derives the name and
# `require_plan_scope` refuses whatever is handed over anyway.
#
# THE PADS CANNOT BE RE-SEATED IN PLACE.  The sites MOVE, not just their
# datums, so step 5 must re-run against `plan.Fresh42.json`; every placed
# object, pad rectangle and keep-out on that world moves with them.
PLAN_SCOPE_DEPENDS_ON = ("plan_declared_seed", "run_seed",
                         "plan_declared_world", "run_world")


def plan_scope(doc: dict) -> tuple[str | None, str | None]:
    """What a settlement plan says about ITSELF.

    `None` is not `clear`: `worldseed.require_scope` refuses an artefact that
    declares no seed, because an undeclared document is exactly the one that
    gets adopted by whichever run opens it.
    """
    return doc.get("seed"), doc.get("world")


def require_plan_scope(path, doc: dict, *, seed_: str | None = None,
                       world_: str | None = None,
                       depends_on: tuple = PLAN_SCOPE_DEPENDS_ON) -> dict:
    """REFUSE a settlement plan solved on ground this run does not have.

    The refusal itself is `worldseed.require_scope`, imported rather than
    restated, so this gate and `field.require_seed` cannot disagree about what
    a scope violation is or what it says.

    `depends_on` exists to be NEUTERED, one named term at a time, and the
    arms run in `selftest`:
      * without `plan_declared_seed` the plan is taken at its word and the
        recorded Pirate68-into-Fresh42 pair is ACCEPTED -- the defect returns;
      * without `plan_declared_world` the same pair is still REFUSED, because
        the seed alone carries it;
      * without `run_seed` a plan naming another WORLD is still REFUSED.
    """
    found_seed, found_world = plan_scope(doc)
    want_s = seed_ if seed_ is not None else worldseed.seed()
    want_w = world_ if world_ is not None else worldseed.world()
    if "plan_declared_seed" not in depends_on:
        found_seed = want_s
    if "plan_declared_world" not in depends_on:
        found_world = None
    if "run_seed" not in depends_on:
        want_s = found_seed
    if "run_world" not in depends_on:
        want_w = None
    return worldseed.require_scope(
        "settlement plan", path, found_seed=found_seed,
        found_world=found_world, seed_=want_s, world_=want_w,
        produced_by=("the settlement siting solver, whose answer is a "
                     "reading of ONE world's ground: re-solve the sites "
                     "against this seed and pass the result as "
                     "`--plan-json settlements/plan.<World>.json`. Building "
                     "another world's solved sites is how 45 pads came to "
                     "command up to 110.95 m of fill"))


# ---------------------------------------------------------------------------
# GROUND_FREEZE_ACROSS_INVOCATIONS_DEPENDS_ON -- THE ZONES THIS WRITE MOVES
# AND THE ZONES A PIECE ALREADY STANDS IN, READ FROM THE LEDGER.  NOT FROM
# THIS PROCESS'S MEMORY.
#
# `main`'s two passes -- whole ground pass, then whole body pass -- make "a
# settlement's ground is finished before any of its bodies stand" true FOR ONE
# INVOCATION.  The unit set of an invocation is `--site` narrowed by `--from`,
# `--limit` and `--only`, and a resume is the NORMAL path: `buildstage
# --resume` exists and has saved 3,258 s of recorded RCON.  A resumed run does
# its own two passes over the eight units it was handed while the other ten
# bodies are already standing, so the guarantee's key was THE PROCESS and the
# declared quantity depends on THE SETTLEMENT'S WHOLE LIFETIME.  Same class,
# one scope narrower.
#
# MEASURED 2026-09-24 on Fresh42's recorded ledger, which is what the 18
# surviving `TERRAIN_AFTER_PIECE` rows are: every one of the 18 is a
# `role=site_pad` write, ten in stenvik and eight in vestvik, and every one is
# blocked by a piece of ANOTHER UNIT OF ITS OWN SETTLEMENT -- zone (8,14) held
# since seq 1486, (9,14) since 1479, (9,15) since 1503, (-14,29) since 1584,
# (-15,28) since 1597, (-14,28) since 1611.  Not one of them is a foreign
# claim and not one is elsewhere in the world.  THE EMITTER IS THE WRONG SIDE,
# not the declaration, and the count is not reclassified: it is refused.
#
# The rule is the SAME one `buildstage.replay` reports, and both halves are
# IMPORTED from it (`piece_zone`, `PIECE_OPS`) rather than restated, so a
# write-time refusal and a replay finding cannot drift into disagreement.
# UNREAD IS NOT CLEAR: a standing piece whose position cannot be read poisons
# every zone, exactly as it does in the replay.
GROUND_FREEZE_ACROSS_INVOCATIONS_DEPENDS_ON = (
    "zones_this_write_moves", "zones_a_standing_piece_occupies")


def occupied_zones(records) -> tuple[dict, bool]:
    """`{zone: seq of the first piece in it}`, and whether any piece is unread.

    Read through `buildstage`'s own `piece_zone` and `PIECE_OPS`: the emitter
    must refuse precisely the population the replay reports.
    """
    import buildstage as BS                                # noqa: PLC0415
    out: dict[tuple[int, int], int] = {}
    unreadable = False
    for rec in records:
        if (rec.get("op") or "") not in BS.PIECE_OPS:
            continue
        z = BS.piece_zone(rec)
        if z is None:
            unreadable = True
        else:
            out.setdefault(z, rec.get("seq"))
    return out, unreadable


def frozen_ground(zones, occupied: dict, unreadable: bool = False,
                  depends_on: tuple = GROUND_FREEZE_ACROSS_INVOCATIONS_DEPENDS_ON
                  ) -> dict:
    """Would this write move ground a body already stands on?  A VERDICT.

    Neuter surface:
      * without `zones_a_standing_piece_occupies` the gate cannot see the
        standing bodies and the recorded stenvik resume is ACCEPTED -- the 18
        rows come straight back;
      * without `zones_this_write_moves` it cannot see which ground moves, and
        must then fail CLOSED on every write after a piece rather than open.
    """
    occ = dict(occupied) if (
        "zones_a_standing_piece_occupies" in depends_on) else {}
    if "zones_this_write_moves" not in depends_on:
        # UNREAD IS NOT CLEAR.  Losing the write's own zones means every
        # occupied zone is a candidate, not none of them.
        shared = sorted(occ)
        return {"verdict": "refused" if (shared or unreadable) else "clear",
                "zones": None, "shared": shared,
                "held_since_seq": occ.get(shared[0]) if shared else None,
                "unreadable_piece": unreadable,
                "depends_on": list(depends_on)}
    zs = sorted({tuple(z) for z in (zones or ())})
    if not zs or unreadable:
        return {"verdict": "refused", "zones": zs, "shared": sorted(occ),
                "held_since_seq": None, "unreadable_piece": unreadable,
                "depends_on": list(depends_on)}
    shared = sorted(set(zs) & set(occ))
    return {"verdict": "refused" if shared else "clear",
            "zones": zs, "shared": shared,
            "held_since_seq": occ.get(shared[0]) if shared else None,
            "unreadable_piece": unreadable,
            "depends_on": list(depends_on)}


# --- placements documents -------------------------------------------------

# Per-site cluster base angle, MEASURED by `Settlements` over a 5 deg sweep
# against all 12,301 location instances: the worst piece-reach room over the
# three members, at the stilt body's own 4.74 m half-diagonal.
#   tree-sth  105 deg -> min room 16.92 m   (0 deg put two members in Crypt2)
#   tree-near  65 deg -> min room 15.95 m   (0 deg: Crypt3 and StoneTowerRuins03)
#   tree-west  55 deg -> min room 30.96 m   (already clear at 0; this is best)
CLUSTER_BASE_YAW = {"tree-sth": 105.0, "tree-near": 65.0, "tree-west": 55.0}


def written_pad(u: dict,
                depends_on: tuple = FL.PAD_EXTENT_DEPENDS_ON) -> dict:
    """THE pad the writer stamps for unit `u`: the plan's declared pad, grown
    wherever the body AT ITS YAW needs more - `flatten.pad_extent_at_yaw`
    over the emitter's own measure of the body, at the settlement margin.
    See PAD_EXTENT_DEPENDS_ON in terraform/flatten.py.  The plan's pad was
    sized on the yaw-0 footprint and written axis-aligned under a body turned
    to the street's bearing; MEASURED, that put vestvik-hall-1 1.740 m off its
    pad on Fresh42 and three standing Ulfsland bodies off theirs."""
    ext = FL.placed_body_extents(u["body"], float(u["yaw"]))
    return FL.pad_extent_at_yaw((u["pad_w"], u["pad_d"]),
                                (u["foot_w"], u["foot_d"]), ext,
                                (u["x"], u["z"]), BD.PAD_MARGIN_M, APRON_M,
                                depends_on)


def _with_written_pad(u: dict, depends_on: tuple) -> dict:
    pe = written_pad(u, depends_on)
    return {**u, "pad_declared": [u["pad_w"], u["pad_d"]],
            "pad_w": pe["w"], "pad_d": pe["d"], "pad_extent": pe}


def unit_records(plan: dict, site: str,
                 depends_on: tuple = FL.PAD_EXTENT_DEPENDS_ON) -> list[dict]:
    """One build unit per body: the town's buildings, or an outlier's cluster.

    `pad_w` x `pad_d` is the pad THE WRITER STAMPS (`written_pad`), not the
    plan's number: every consumer of a unit - `placements_doc`, the clamp
    certification in `plan.py`, the ledger record - reads this one pad."""
    if site in plan["towns"]:
        town = plan["towns"][site]
        out = []
        for b in town["buildings"]:
            out.append({
                "id": b["id"], "body": b["body"], "sha256": b["sha256"],
                "x": b["x"], "z": b["z"], "yaw": b["yaw_deg"],
                "pad_w": b["pad"]["width_m"], "pad_d": b["pad"]["depth_m"],
                # The BODY's own extent, distinct from its pad. `check_radius`
                # needs the extent that can hold PREFABS: a pad is cleared
                # ground and a 20 m pad around a 4 m hut contains nothing.
                "foot_w": b["footprint"][0], "foot_d": b["footprint"][1],
                "target_y": b["pad"]["target_y"], "ground_y": b["pad"]["target_y"],
                "pieces": b["pieces"], "role": b["role"], "patch": town["patch"],
            })
        return [_with_written_pad(u, depends_on) for u in out]
    for rec in plan["outliers"]:
        if rec["id"] != site:
            continue
        n = rec.get("cluster", 1)
        out = []
        for k in range(n):
            # A cluster spreads on the pad's own diagonal so the members do not
            # share a footprint; one member is the pad centre.
            #
            # THE BASE ANGLE IS PER SITE AND IT IS A MEASUREMENT. At base 0
            # four of the nine treehouse members refuse the piece-reach gate:
            # tree-sth-1 and -2 reach inside Crypt2's stand-off (MEASURED
            # znviewReachM 21.0 m against a much smaller declared radius),
            # tree-near-1 inside Crypt3's and tree-near-3 inside
            # StoneTowerRuins03's (11.47 m). Those sites were sited before
            # `clearance.py` gained the per-type measured reach, so the gate
            # got SHARPER under a finished plan -- which is the instrument
            # working, not a siting error. Settlements swept the base angle in
            # 5 deg steps against all 12,301 instances at the stilt body's own
            # 4.74 m half-diagonal; these are the angles at which all three
            # members clear, with the worst room over the three reported.
            # Rotating the cluster is the fix rather than shrinking the pad,
            # which would be the clipped-radius failure in a different costume.
            ang = math.radians(CLUSTER_BASE_YAW.get(rec["id"],
                                                    rec["pad"]["yaw_deg"])
                               + 120 * k)
            r = 0.0 if n == 1 else (rec["pad_m"] / 2 - 4.0)
            out.append({
                "id": rec["id"] if n == 1 else f"{rec['id']}-{k + 1}",
                "body": rec["body"], "sha256": rec["body_sha256"],
                "x": round(rec["x"] + r * math.cos(ang), 1),
                "z": round(rec["z"] + r * math.sin(ang), 1),
                "yaw": round(120 * k, 1),
                "pad_w": rec["pad"]["width_m"], "pad_d": rec["pad"]["depth_m"],
                # THE BODY'S EXTENT, AND OUTLIERS SPELL IT `body_footprint`.
                # `footprint` is the towns' key and no outlier record has it,
                # so this fell through to the PAD on every outlier -- which is
                # exactly the substitution `check_radius` documents as fatal.
                # MEASURED on the treehouses: the stilt body is 4.4 x 8.4 but
                # the pad is 16 x 16, so the pad's 11.31 m half-diagonal ate
                # the 6.93 m spacing between cluster members and every one of
                # the nine refused with "nearest sibling room -4.88 m". With
                # the body's own 4.74 m half-diagonal the room is +1.69 m and
                # the count is solvable.
                "foot_w": (rec.get("footprint") or rec.get("body_footprint")
                           or [rec["pad"]["width_m"],
                               rec["pad"]["depth_m"]])[0],
                "foot_d": (rec.get("footprint") or rec.get("body_footprint")
                           or [rec["pad"]["width_m"],
                               rec["pad"]["depth_m"]])[1],
                "target_y": rec["pad"]["target_y"],
                "ground_y": rec["pad"]["target_y"],
                "pieces": rec["pieces"], "role": rec["kind"], "patch": rec["patch"],
            })
            # A NUDGED OUTLIER PAD, and it has to be an OVERRIDE rather than a
            # rewrite of `x`/`z`. A cluster member's position is DERIVED --
            # centre plus `pad_m/2 - 4` on a per-site base angle -- so moving
            # one treehouse by editing the site's x/z would move all three,
            # and editing the derivation would move the ones already standing.
            # `nudged` is keyed by UNIT id and carries the re-solved pad, so a
            # nudge names exactly the body it moved and the plan still reads
            # as the cluster it is.
            nud = (rec.get("nudged") or {}).get(out[-1]["id"])
            if nud:
                out[-1]["x"], out[-1]["z"] = nud["x"], nud["z"]
                out[-1]["target_y"] = nud["pad"]["target_y"]
                out[-1]["ground_y"] = nud["pad"]["target_y"]
                out[-1]["nudged_from_m"] = nud.get("move_m")
        return [_with_written_pad(u, depends_on) for u in out]
    raise ValueError(f"{site} is neither a town nor an outlier in plan.json")


def pad_text(u: dict) -> str:
    """The unit's written pad as `flatten.pad_extent` reads it back."""
    return f"{u['pad_w']:g} m along x by {u['pad_d']:g} m along z"


def placements_doc(plan: dict, site: str) -> dict:
    """A placements document in the shape `clear.py` and `flatten.py` read.

    Only the fields those two actually consume are synthesised, and each is the
    number this package MEASURED rather than a restatement of a requirement:
    `solved.x/z` the pad centre, `footprint_orientation` the pad rectangle (it
    is the authority for `pad_extent`), `flatten_cost.target_y` the pad height,
    `rotation.yaw` the body's yaw so the clearing cylinder covers the rotated
    pad, and `requirement.footprint_m` for the diagnostics.
    """
    units = unit_records(plan, site)
    loc = clearance.load(locations_dump(plan))
    return {"world": WORLD, "settlement": site,
            "placements": [unit_placement(u, loc, site) for u in units]}


def unit_placement(u: dict, loc: clearance.Locations, site: str) -> dict:
    """ONE unit's placement record, as `placements_doc` writes it and
    `apply_unit_ground` reads it back -- and as `settlements/plan.py` asks
    `clearing_gate` of a candidate pad before it sites one."""
    near = loc.nearest(u["x"], u["z"])
    return {
        "id": u["id"],
        "blueprint": u["body"],
        "sha256": u["sha256"],
        "pieces": u["pieces"],
        "align": "floor-center",
        "rotation": {"yaw": u["yaw"]},
        "requirement": {
            "biome": "Meadows",
            "footprint_m": max(u["pad_w"], u["pad_d"]),
            "footprint_xz_m": [u["pad_w"], u["pad_d"]],
        },
        "solved": {
            # THE RUN'S SEED, NOT A LITERAL.  A synthesised placements
            # document that states one world's seed while carrying
            # another's coordinates is an artefact that lies about its
            # own scope, and every downstream `require_scope` reads this
            # field. `require_plan_scope` has already proved the plan
            # belongs to this seed by the time this is written.
            "seed": SEED,
            "x": u["x"], "z": u["z"], "y": u["ground_y"],
            "footprint_orientation": pad_text(u),
            "flatten_cost": {"target_y": u["target_y"], "resolution_m": 1.0,
                             "estimated": False},
            # `area.location_bound` reads this and REFUSES a clearing
            # cylinder that reaches it, so it is populated with the
            # MEASURED distance from our own per-type dump rather than
            # left absent -- an absent bound disarms their guard silently.
            "nearest_location_m": near["dist_m"],
            "nearest_location": near["prefab"],
            "nearest_location_standoff_m": near["standoff_m"],
        },
        "settlement": {"site": site, "role": u["role"], "patch": u["patch"]},
    }


def write_placements(plan: dict) -> list[Path]:
    PLACEMENTS.mkdir(parents=True, exist_ok=True)
    out = []
    for site in list(plan["towns"]) + [r["id"] for r in plan["outliers"]]:
        doc = placements_doc(plan, site)
        p = PLACEMENTS / f"{site}.yaml"
        p.write_text(yaml.safe_dump(doc, sort_keys=False, width=10 ** 9))
        out.append(p)
    return out


# --- the wire -------------------------------------------------------------

def clearing_ops(doc: dict, unit_id: str) -> list[dict]:
    """`zones_generate` + `objects_remove` for one pad, from clearing's own
    area derivation so the radius, the margin and the location bound are the
    ones that module decided rather than a re-implementation here."""
    import area as clear_area
    import clear as clearing

    place = next(p for p in doc["placements"] if p["id"] == unit_id)
    ar = clear_area.clearing_area(place, apron=APRON_M)
    cmds = clearing.commands(ar, clearing.DEFAULT_KEEP)
    gen, remove = cmds[0], cmds[1]
    zones = zone_list(ar.centre_x, ar.centre_z, ar.radius_m)
    return [
        {"op": "zones_generate",
         "params": {"pos": [ar.centre_x, ar.centre_z],
                    "max_m": clearing.generate_max(ar),
                    "zones": [list(z) for z in zones],
                    "role": "site_pad", "site_id": unit_id},
         "wire": [gen],
         "expect": {"zone_ctrl": len(zones)}},
        {"op": "objects_clear",
         "params": {"centre": [ar.centre_x, ar.centre_z],
                    "radius_m": round(ar.radius_m, 2), "ids": "*",
                    "ignore": list(clearing.DEFAULT_KEEP),
                    "role": "clearing", "site_id": unit_id},
         "wire": [remove],
         # THE VERIFYING REGION MUST CONTAIN THE ACTING REGION.
         #
         # This checked `max = radius_m / sqrt(2)` -- the disc inscribed in a
         # SQUARE of half-side r -- while the removal above acts over `max =
         # radius_m`. Upgrade World's `pos=/max=` filter is a DISC, not a
         # Chebyshev box: MEASURED at the binary, UpgradeWorld.dll references
         # `Utils.DistanceXZ` and `get_sqrMagnitude` and carries no rect or
         # Chebyshev test; corroborated by `objects_count` answering Total 212
         # at max=7.00 and 215 at max=7.70 for three pieces 7.168 m out --
         # inside a 7.0 m square, outside a 7.0 m disc.
         #
         # So the annulus between 0.707r and r -- EXACTLY 50% of the area --
         # was emptied and never verified, and a survivor there read as a
         # clean clearing. A false PASS, which is the dangerous direction.
         "expect": {"objects_count": {"ids": "*", "ignore": "_*",
                                      "pos": [ar.centre_x, ar.centre_z],
                                      "max": round(ar.radius_m, 2),
                                      "total": 0, "tolerance": 0}}},
    ]


# WHAT MAY BE DELETED, and this list is a MEASUREMENT of this seed rather than
# a taxonomy: every prefab family `objects_count id=*` reported inside the 33
# settlement cylinders after generation. Vegetation, pickables, rocks and
# deadfall. A prefab that does NOT match is not assumed harmless -- it stops
# the build, because the one unrepairable damage class this project has
# recorded is deleting a location's pieces, and a mod POI's pieces are
# ordinary building prefabs (MEASURED at (444.2, 911.5): woodwall x10,
# wood_floor x3, wood_beam_45 x8, a Beehive and a Music_MeadowsVillageFarm;
# at (533.8, 910.6): woodwall x22, wood_roof x7 and a TreasureChest_meadows).
NATURAL_RE = re.compile(
    r"^(?:beech|birch|fir|pine|oak|swamptree|yggashoot|bush|"
    r"raspberrybush|blueberrybush|cloudberrybush|"
    r"rock|minerock|stubbe|shrub|vines|glowingmushroom|"
    r"pickable_|bh_pickable_|.*_log|.*_oldlog|greydwarf_root|"
    r"marker|spawner_)", re.IGNORECASE)


def cylinder_contents(srv, ar) -> dict:
    """WHAT IS ACTUALLY IN THE CYLINDER ABOUT TO BE EMPTIED.

    This is a different question from every stand-off check in this pipeline,
    and it is the only one that measures the hazard directly. The dump answers
    "how close is a location"; the live `LocationProxy` sweep answers "is there
    a marker the dump cannot see"; and for an UNIDENTIFIED marker the sweep
    can only assume a worst case -- MEASURED tonight, 58 m = a 32 m worst-case
    radius + 11 m INFERRED overshoot + a 15 m taste margin -- which refused
    Stenvik's hall on a marker 23.9 m away whose pieces are nowhere near the
    pad.

    So instead of arguing about radii: ask the world what is inside the
    cylinder. `objects_count id=*` lists EVERY prefab present with its count,
    the answer is one line per prefab, and `Server.count` permits `id=*` only
    inside 40 m, which every settlement cylinder is. The verdict is then a
    fact about the objects that would be deleted rather than an inference from
    a distance.
    """
    got, per = srv.count("*", ar.centre_x, ar.centre_z, ar.radius_m)
    natural = {p: n for p, n in per.items() if NATURAL_RE.match(p)}
    other = {p: n for p, n in per.items() if not NATURAL_RE.match(p)}
    return {
        "total": got, "per_prefab": per,
        "clearable": natural, "not_clearable": other,
        "verdict": "clear" if not other else "VIOLATION",
        "probe": f"objects_count id=* ignore=_* "
                 f"pos={ar.centre_x:.2f},{ar.centre_z:.2f} "
                 f"max={ar.radius_m:.2f}",
        "method": ("MEASURED: the live per-prefab contents of the exact "
                   "cylinder `objects_remove` will empty. Nothing here is "
                   "inferred from a radius -- a prefab that would be deleted "
                   "is either in the measured natural set or it stops the "
                   "build."),
        "tool": "tools/jumpstart/settlements/build.py::cylinder_contents",
    }


def scoped_clear(ar, unit_id: str, contents: dict) -> dict:
    """`objects_remove` scoped to the prefabs MEASURED in the cylinder.

    `id=*` would also delete anything that arrives between the measurement and
    the removal, and it is the command that deleted POI content in the
    pre-wipe world. Naming the prefabs makes the safety a property of the
    COMMAND rather than of a count taken a minute earlier. `ignore=_*` is kept
    so `_ZoneCtrl` and `_TerrainCompiler` survive: MEASURED, an empty
    `ignore=` is an empty id, not "no filter", and answers
    `Error: Entity id  not recognized.`
    """
    ids = sorted(contents["clearable"])
    wire = (f"objects_remove id={','.join(ids)} ignore=_* "
            f"pos={ar.centre_x:g},{ar.centre_z:g} max={ar.radius_m:.2f}")
    return {
        "op": "objects_clear",
        "params": {"centre": [ar.centre_x, ar.centre_z],
                   "radius_m": round(ar.radius_m, 2), "ids": ids,
                   "ignore": ["_*"], "role": "clearing", "site_id": unit_id},
        "wire": [wire],
        # THE CHECK MUST ASK THE QUESTION THE COMMAND ANSWERED.
        #
        # `ids="*"` asked "is this cylinder empty of everything?" while the
        # command only ever promised "these named prefabs are gone".  The
        # difference is not theoretical: MEASURED at
        # sudrvik-austrbakki-6 (seq 3002), the removal ran correctly and the
        # postcondition failed with got 2, per_prefab {'Deer': 1,
        # 'Seagal': 1} -- a deer and a gull that WALKED AND FLEW IN between
        # the removal and the count.  No `objects_remove` can make a
        # cylinder stay empty of wildlife, so a check that demands it is a
        # gate with no open position, and it stopped a live build one record
        # into a 14-terrace stage.
        #
        # `cylinder_verdict` already classifies creatures as MOBILE and
        # explicitly does not treat them as blocking; this expectation was
        # the one place that forgot.  Scoped to the same id list the wire
        # carries, the check is also STRICTLY SHARPER for what the command
        # did: it can no longer pass just because the cylinder happens to be
        # empty of something nobody removed.
        #
        # THE VERIFYING REGION MUST CONTAIN THE ACTING REGION.
        #
        # The wire above removes over `max=ar.radius_m`; this checked
        # `max = radius_m / sqrt(2)`, the disc inscribed in a SQUARE of
        # half-side r.  Upgrade World's `pos=/max=` filter is a DISC:
        # UpgradeWorld.dll references `Utils.DistanceXZ` and
        # `get_sqrMagnitude` and carries no rect or Chebyshev symbol, and
        # `objects_count` answered Total 212 at max=7.00 against 215 at
        # max=7.70 for three pieces 7.168 m out -- inside a 7.0 m square,
        # outside a 7.0 m disc.  [The filter's own call site is INFERRED,
        # not disassembled; see the single-object test on record.]
        #
        # So the annulus between 0.707r and r -- EXACTLY 50% of the area --
        # was emptied and never verified, and a survivor there read as a
        # clean clearing.  A false PASS, the dangerous direction.
        #
        # The fix does not depend on the parser question, because
        # disc(r) is contained in square(r) unconditionally: verifying over
        # r is exact under the disc reading and merely conservative under
        # the square one, and false-fails under neither.  This is the site
        # that actually ran -- `terrace_stage` calls `scoped_clear`, while
        # `clearing_ops` is the planning form.
        "expect": {"objects_count": {
            "ids": ",".join(ids), "ignore": "_*",
            "pos": [ar.centre_x, ar.centre_z],
            "max": round(ar.radius_m, 2),
            "total": 0, "tolerance": 0}},
    }



def union_prior(b, comps: dict, op_y: dict) -> dict:
    """Restore, into our own blobs, WHICH CLAIM'S BYTES ARE ON THE COMPILER.

    THIS IS NOT DEFENSIVE, IT IS ARITHMETIC THE TOWN REQUIRES. A zone holds
    exactly ONE `_TerrainCompiler` -- `Heightmap::GetAndCreateTerrainCompiler`
    returns the first it finds -- and the write path deletes before it spawns.
    Stenvik's 18 pads span 8 zones, so the second pad in a zone would ERASE
    the first one's levelling and leave a finished building standing on
    generated ground. The ledger's clobber guard refuses that, and the fix is
    to read the prior bytes back out of the blob store: ours win where we
    wrote, the compiler's current state survives everywhere else.

    THE PREDICATE IS A FACT WITH ONE ANSWER, NOT A SCAN DIRECTION.  This
    function used to walk the prior claims in chain order and take a sample
    `if not comp.modified_height[i]`, so for every sample the new write did
    not itself touch, THE OLDEST CLAIM THAT EVER TOUCHED IT WON.  Every write
    on a chain has already unioned its predecessors, so a zone's CURRENT state
    is not its first claim; oldest-wins handed back a state the world has not
    been in since, and a pad re-emit therefore REVERTED every road that had
    later crossed its zones.

    MEASURED with no world, over the 11 zones of five pad re-emits on
    Ulfsland: oldest-wins disagrees with the zone's live state on 7 of the
    11 -- zone (8,0) on 1,315 samples, (9,15) on 630, (8,14) on 208, (9,1) on
    60, (9,14) on 48 -- and the worst single sample, (598, 967) in zone
    (9,15), is delta -3.916 m under oldest-wins against +2.591 m as the world
    stands: 6.507 m of `road_T4_meadhall_stathub`'s bed thrown away by a write
    that never mentioned it.  It is silent in both directions, which is why
    nobody had caught it: the reverted samples lie OUTSIDE the re-emitting
    pad's own rectangle, so its delta gate, its sample verdict and its own
    `expect` postcondition all pass over a zone whose road has just been
    un-graded.

    "Reverse the scan" would have been the wrong shape of fix -- the same
    shape that moved the portal hall's footprint 8.9 m when `_pad_footprint`
    swapped its first sibling for its last.  The question has a name and one
    answer: WHICH CLAIM'S BYTES ARE CURRENTLY ON THE COMPILER.
    `roads/applied.py::Applied.zone` already answers exactly that, per sample,
    in FILE ORDER -- which is the only total order this artefact has, because
    the chain forked at file line 47 and carries duplicate `seq` labels 43-46,
    so `max(seq)` picks one of a forked pair arbitrarily.  It separates
    `owner` (the last claim whose blob HOLDS a sample - what is on the
    compiler) from `author` (the last claim that CHANGED it), and `owner` is
    the one this function needs.  So this is a CALLER of that instrument
    rather than a fifth implementation of it.

    `Applied.restore_into` then hands every restored sample to
    `Applied.assert_restores_current`, which re-compares it against that same
    instrument's composed delta and RAISES on the first disagreement.  A
    restore that cannot prove it preserved the world refuses to be written.
    The check lives there rather than here because `roads/ribbon.py` does this
    restore too -- correctly today, but with no check to stop it going
    backwards a second time, which it already did once.

    Whether the earlier record ever reached the world is measured separately
    (`compiler_present`), because a record is appended BEFORE it is sent and
    an append that failed to send leaves a claim with no compiler behind it.
    Restoring is right either way -- the samples describe a pad somebody
    intended -- but the log should say which case it was.
    """
    sys.path.insert(0, str(JUMPSTART / "roads"))
    from applied import Applied  # noqa: PLC0415

    # OUR OWN LEDGER HANDLE, not a second one: this runs inside a write.
    ap = Applied(world=WORLD, led=b.led)
    prior: dict[tuple[int, int], list[dict]] = {}
    for w in ap.writes:
        if w["zone"] in comps:
            prior.setdefault(w["zone"], []).append(w)
    merged: dict[tuple[int, int], dict] = {}
    for z, claims in prior.items():
        got = ap.restore_into(z[0], z[1], comps[z])
        merged[z] = dict(
            got,
            merged_from=[c["seq"] for c in claims],
            merged_from_file_lines_1based=[c["file_line"] + 1
                                           for c in claims],
            merged_from_record_index_0based=[c["file_line"] for c in claims],
            merged_from_actors=[c["actor"] for c in claims],
            why=("a zone holds one compiler and this op deletes before it "
                 "spawns, so the prior bytes are re-read from the ledger's "
                 "blob store and kept; ours win only where we wrote. Which "
                 "prior bytes is not a scan direction but a fact -- "
                 "`Applied.zone`'s `owner` array, in FILE ORDER, because "
                 "`seq` is not a total order on this chain (it forked at "
                 "file line 47 and carries duplicate labels 43-46). Taking "
                 "the OLDEST claim instead, which this did until it was "
                 "measured, threw away 6.507 m of a later road bed, and "
                 "`samples_checked_against_applied` is the number of "
                 "restored samples re-compared against the live surface "
                 "before the blob was allowed to exist."))
    return merged


# ---------------------------------------------------------------------------
# THE SELFTEST FOR THE COMPILER RESTORE - both directions, no world, no server
# ---------------------------------------------------------------------------
# A union that can silently discard a sample is the single most destructive
# thing in this build, and it WAS silently discarding samples.  So it is
# pinned the way the acceptance gate pins a section: each case is a state
# built here in code and a direction the code must come out, and one of them
# is the state THIS WORLD WAS ACTUALLY IN.
#
# `_FakeLedger` is a ledger-shaped object over records built in this file.
# It is not a mock of the answer - `Applied` does its own file-order walk and
# its own blob decode over these records, so what is being proved is the real
# composition, on a chain small enough to state by hand.
# ---------------------------------------------------------------------------
class _FakeLedger:
    def __init__(self, recs: list[dict], blobs: dict[str, bytes]):
        self._recs, self._blobs = recs, blobs

    def records(self):
        return list(self._recs)

    def read_blob(self, sha: str) -> bytes:
        return self._blobs[sha]


class _FakeBuilder:
    def __init__(self, led):
        self.led = led


def _rec(seq: int, name: str, role: str, zone, sha: str) -> dict:
    return {"op": "terrain_write", "seq": seq, "actor": "selftest",
            "params": {"name": name, "role": role,
                       "entries": [{"zone": [zone[0], zone[1]],
                                    "blob_sha256": sha,
                                    "data_entry": f"{name}_z"}]}}


def _comp_with(zone, samples: dict[int, float]):
    c = tcdata.Compiler(zone_x=zone[0], zone_z=zone[1])
    for i, v in samples.items():
        c.modified_height[i] = True
        c.level_delta[i] = v
    return c


def _chain(zone, claims: list[tuple[int, str, str, dict[int, float]]]):
    """`(fake builder, {sha: blob})` for a list of (seq, name, role, samples).

    Each claim's blob is CUMULATIVE, exactly as a real one is: a write unions
    its predecessors' samples before it serialises, so claim k's blob holds
    everything claims 0..k-1 held with its own values on top.  Getting this
    wrong in the fixture would prove the fix against a chain this world never
    produces.
    """
    blobs: dict[str, bytes] = {}
    recs: list[dict] = []
    state: dict[int, float] = {}
    for seq, name, role, samples in claims:
        state = dict(state)
        state.update(samples)
        blob = _comp_with(zone, state).blob(0.0)
        sha = sha256_hex(blob)
        blobs[sha] = blob
        recs.append(_rec(seq, name, role, zone, sha))
    return _FakeBuilder(_FakeLedger(recs, blobs)), blobs


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# The state this world was in, reduced to two samples and stated in metres.
# `_PAD_ONLY` is a sample only the pad ever wrote; `_CONTESTED` is one the pad
# wrote and a road later re-cut - the shape of (598, 967) in zone (9, 15),
# where the pad's delta is -3.916 m and the road's is +2.591 m.
_Z = (9, 15)
_PAD_ONLY = 100
_CONTESTED = 2589
_OURS = 500


def _case_a_later_road_is_not_reverted() -> list[str]:
    """THE DEFECT.  A pad re-emit in a zone a road later cut must restore the
    ROAD's delta at the contested sample, not the pad's."""
    b, _ = _chain(_Z, [
        (121, "stenvik-house-3", "site_pad",
         {_PAD_ONLY: -1.250, _CONTESTED: -3.916}),
        (1197, "road_T8_stenvik_wttown", "road_segment",
         {_CONTESTED: 2.591}),
    ])
    comp = _comp_with(_Z, {_OURS: 0.75})
    union_prior(b, {_Z: comp}, {_Z: 47.15})
    bad = []
    if abs(comp.level_delta[_CONTESTED] - 2.591) > 1e-6:
        bad.append(f"contested sample restored to "
                   f"{comp.level_delta[_CONTESTED]:+.3f} m, not the road's "
                   f"+2.591 m - THE ROAD HAS BEEN REVERTED")
    if abs(comp.level_delta[_PAD_ONLY] + 1.250) > 1e-6:
        bad.append(f"the pad's own uncontested sample came back as "
                   f"{comp.level_delta[_PAD_ONLY]:+.3f} m, not -1.250 m")
    if abs(comp.level_delta[_OURS] - 0.75) > 1e-6:
        bad.append("our own sample was overwritten by a prior claim")
    return bad


def _case_the_known_bad_order_is_REFUSED() -> list[str]:
    """AND THE SAME STATE MUST FAIL WHEN THE OLD ORDER IS USED.

    The oldest-wins loop is reproduced here verbatim and handed to the
    postcondition.  If `Applied.assert_restores_current` does not raise on it,
    the check is a decoration: this is the exact state that threw away
    6.507 m, and the message must name the metres and the road.
    """
    sys.path.insert(0, str(JUMPSTART / "roads"))
    from applied import Applied  # noqa: PLC0415
    b, _ = _chain(_Z, [
        (121, "stenvik-house-3", "site_pad",
         {_PAD_ONLY: -1.250, _CONTESTED: -3.916}),
        (1197, "road_T8_stenvik_wttown", "road_segment",
         {_CONTESTED: 2.591}),
    ])
    comp = _comp_with(_Z, {_OURS: 0.75})
    ours = {i for i in range(tcdata.SAMPLES) if comp.modified_height[i]}
    ap = Applied(world=WORLD, led=b.led)
    for w in sorted((w for w in ap.writes if w["zone"] == _Z),
                    key=lambda w: w["file_line"]):          # OLDEST FIRST
        old = tcdata.parse(b.led.read_blob(w["sha"]))
        for i, (level, smooth) in old["heights"].items():
            if not comp.modified_height[i]:
                comp.modified_height[i] = True
                comp.level_delta[i] = level
                comp.smooth_delta[i] = smooth
    try:
        ap.assert_restores_current(_Z[0], _Z[1], comp, ours)
    except SystemExit as e:
        msg = str(e)
        missing = [w for w in ("REVERT", "6.507", "road_T8_stenvik_wttown")
                   if w not in msg]
        if missing:
            return [f"refused, but the message does not name {missing}: "
                    f"{msg}"]
        return []
    return ["THE OLDEST-WINS UNION WAS ACCEPTED - the postcondition cannot "
            "see a 6.507 m reversion and is a decoration"]


def _case_an_untouched_zone_restores_byte_identically() -> list[str]:
    """THE REFUSAL THE FIX MUST NOT COST.  A pad re-emit in a zone NO later
    write touched must come back exactly and must NOT refuse - otherwise a
    silent revert has been traded for a gate nobody can get through."""
    b, blobs = _chain(_Z, [
        (121, "stenvik-house-3", "site_pad",
         {_PAD_ONLY: -1.250, _CONTESTED: -3.916, 42: 0.125}),
    ])
    comp = _comp_with(_Z, {_OURS: 0.75})
    union_prior(b, {_Z: comp}, {_Z: 47.15})
    want = {_PAD_ONLY: -1.250, _CONTESTED: -3.916, 42: 0.125}
    bad = [f"sample {i} came back {comp.level_delta[i]:+.4f} m, not "
           f"{v:+.4f} m" for i, v in want.items()
           if not comp.modified_height[i]
           or abs(comp.level_delta[i] - v) > 1e-6]
    return bad


def _case_seq_is_not_the_order() -> list[str]:
    """`seq` IS NOT A TOTAL ORDER ON THIS ARTEFACT and must not be the key.

    MEASURED on Ulfsland's chain: 5,298 records, 5,294 distinct seqs, four
    DUPLICATE labels (43-46 appear at two file lines each) and one position
    where seq DECREASES - at line 48 it goes 46 -> 43, because three
    concurrent appends raced inside the writer's read-prev-write window.

    So the fixture puts the LATER claim at the LOWER seq.  Anything keyed on
    `seq` restores the wrong bytes; file order restores the right ones.
    """
    b, _ = _chain(_Z, [
        (46, "stenvik-house-3", "site_pad", {_CONTESTED: -3.916}),
        (43, "road_T8_stenvik_wttown", "road_segment", {_CONTESTED: 2.591}),
    ])
    comp = _comp_with(_Z, {_OURS: 0.75})
    union_prior(b, {_Z: comp}, {_Z: 47.15})
    if abs(comp.level_delta[_CONTESTED] - 2.591) > 1e-6:
        return [f"restored {comp.level_delta[_CONTESTED]:+.3f} m: the claim "
                f"at the HIGHER seq won, but it is EARLIER in the file"]
    return []

def _case_the_union_is_APPENDABLE() -> list[str]:
    """A UNION THE LEDGER WILL NOT ACCEPT IS NOT A UNION, IT IS A REFUSAL.

    The other three cases prove the BYTES are right and stop there, so for as
    long as `union_prior` produced correct bytes under a `merge_policy` the
    schema rejects, all four passed and every merged write still failed at
    `Ledger.append`.  MEASURED: the first `build.py pad` emit ever attempted
    (wt-south, seq 5358) was refused on entries 0, 1 and 3 -- exactly the
    three of its four zones that HAD a prior claim -- because
    `applied.py::restore_into` returned a SENTENCE in the enum slot and
    `schema.py` only checks `merge_policy` where `merged_from` is present.
    The union's one reason to exist is writing a zone somebody already wrote,
    and that was the only case that could not be appended.

    So this case asks the question the byte cases cannot: run the entry
    `terrain_entries` would build from a merged zone through the REAL
    `schema.validate`, and fail unless the ledger would take it.
    """
    import schema  # noqa: PLC0415

    b, _ = _chain(_Z, [
        (121, "stenvik-house-3", "site_pad", {_CONTESTED: -3.916}),
    ])
    comp = _comp_with(_Z, {_OURS: 0.75})
    merged = union_prior(b, {_Z: comp}, {_Z: 47.15})
    m = merged.get(_Z)
    if not m:
        return [f"union_prior reported no merge for zone {_Z}, which holds a "
                f"prior claim -- the fixture no longer exercises the path"]
    # The two fields `terrain_entries` copies onto the entry, and nothing
    # else: if either is unacceptable the append dies, whatever the bytes say.
    # The whole validator is driven and only the merge complaints are kept,
    # so this cannot be satisfied by a fixture that is invalid for unrelated
    # reasons, nor pass because some other field silently went missing.
    env = {"seq": 0, "ts": "1970-01-01T00:00:00Z", "actor": "selftest",
           "op": "terrain_write", "wire": [], "requires": {}, "expect": {},
           "meta": {}, "prev": "0" * 64,
           "params": {"entries": [{"zone": [_Z[0], _Z[1]],
                                   "merged_from": m["merged_from"],
                                   "merge_policy": m["merge_policy"]}]}}
    bad = [p for p in schema.validate(env) if "merge_policy" in p]
    if bad:
        return [f"the merge_policy union_prior produced "
                f"({m['merge_policy']!r}) is refused by the ledger schema: "
                f"{bad[0]}"]
    return []


_UNION_CASES = (
    ("a_later_road_is_not_reverted", _case_a_later_road_is_not_reverted),
    ("the_known_bad_order_is_REFUSED", _case_the_known_bad_order_is_REFUSED),
    ("an_untouched_zone_restores_byte_identically",
     _case_an_untouched_zone_restores_byte_identically),
    ("seq_is_not_the_order", _case_seq_is_not_the_order),
    ("the_union_is_APPENDABLE", _case_the_union_is_APPENDABLE),
)


# ---------------------------------------------------------------------------
# THE SELFTEST FOR THE REGRADE GATE - both directions, no world, no server
# ---------------------------------------------------------------------------
# `accept/instruments.py::regrade_census` is proven where it lives.  What is
# proven HERE is the thing that did not exist until tonight: that the WRITE
# PATH asks it, over the pad's own snapped lattice, and REFUSES.  A census
# nobody calls refuses nothing, which is exactly how seventeen refusals stayed
# open while the rule that names them was already written and green.
#
# Every case drives `regrade_gate` itself through its two instrument seams.
_RG_SLAB = [["b", -1.0, -0.5, -1.0, 1.0, 0.5, 1.0]]        # stone_floor_2x2
_RG_POST = [["b", -0.15, 0.0, -0.15, 0.15, 4.0, 0.15]]     # wood_pole_log_4
_RG_GEOM = {"stone_floor_2x2": _RG_SLAB, "wood_pole_log_4": _RG_POST}
_RG_UNIT = {"id": "selftest-pad", "x": 0.0, "z": 0.0,
            "pad_w": 4.0, "pad_d": 4.0, "target_y": 40.0}


def _rg_run(unit, ground, rows, hole=()):
    """`regrade_gate` over a constructed surface and a constructed census."""
    import accept.instruments as AI                # noqa: PLC0415
    hole = {(float(x), float(z)) for x, z in hole}

    def surface_at(x, z):
        return None if (x, z) in hole else ground.get((x, z), 40.0)

    def census():
        return SimpleNamespace(rows=list(rows)), "selftest"

    real = AI.load_geometry
    AI.load_geometry = lambda: (_RG_GEOM, set(_RG_GEOM))
    try:
        return regrade_gate(None, unit, surface_at=surface_at,
                            census=census)
    finally:
        AI.load_geometry = real


def _rg_low_column(y: float = 38.5) -> dict:
    """The pad edge that is LOW - the state every measured fill came from."""
    return {(-2.0, float(z)): y for z in range(-2, 3)}


_RG_SLAB_ROW = ("stone_floor_2x2", -2.0, 39.0, 0.0, 0.0, 0.0, 0.0, 1.0)
_RG_POST_ROW = ("wood_pole_log_4", 0.0, 40.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _case_a_fill_over_a_standing_piece_is_REFUSED():
    """THE STATE THE WORLD IS IN.  A pad edge 1.5 m low, a slab standing on
    that low ground, and a re-emit to the pad's own datum that puts the
    ground over its top.  ParBuried retired 385 pieces for exactly this."""
    r = _rg_run(_RG_UNIT, _rg_low_column(), [_RG_SLAB_ROW])
    if r["verdict"] == "clear":
        return ["NOT REFUSED: the fill buries a standing stone_floor_2x2 and "
                f"the gate returned clear: {r}"]
    if not r.get("buries"):
        return [f"refused without naming what it buries: {r}"]
    return []


def _case_the_same_fill_with_nothing_standing_is_clear():
    """AND THE REFUSAL MUST COST NOTHING ELSE.  Identical ground, identical
    target, empty census: a gate that refuses this refuses every pad and is
    worth nothing."""
    r = _rg_run(_RG_UNIT, _rg_low_column(), [])
    if r["verdict"] != "clear":
        return [f"a fill with nothing standing on it was refused: {r}"]
    if r["fills"] < 1:
        return [f"the fixture moves no ground, so it proves nothing: {r}"]
    return []


def _case_a_cut_that_undermines_a_post_is_REFUSED():
    """A cut cannot bury anything, and that is true and insufficient: it
    takes the ground out from under a post whose foot is on it."""
    unit = dict(_RG_UNIT, target_y=38.0)
    r = _rg_run(unit, {}, [_RG_POST_ROW])
    if r["verdict"] == "clear":
        return [f"NOT REFUSED: a 2 m cut under a standing post: {r}"]
    if not r.get("undermines"):
        return [f"refused without naming what it undermines: {r}"]
    return []


def _case_a_pad_that_does_not_move_is_clear():
    """RULE 1, THROUGH THE WRITE PATH.  The ground already reads the target,
    so the write moves nothing and nothing standing on it can be affected -
    even with a piece sitting exactly at the datum."""
    r = _rg_run(_RG_UNIT, {}, [_RG_SLAB_ROW])
    if r["verdict"] != "clear":
        return [f"a write that moves no ground was refused: {r}"]
    if r["moving"]:
        return [f"the fixture moves {r['moving']} samples, so rule 1 is not "
                f"what made it clear: {r}"]
    return []


def _case_an_unread_sample_is_UNREAD_not_clear():
    """A RECTANGLE JUDGED OVER THE SAMPLES THAT HAPPENED TO BE COVERED IS
    THE DEFECT.  One vertex the surface cannot answer for, and the verdict
    must name the hole rather than report the other 24 as clean."""
    r = _rg_run(_RG_UNIT, _rg_low_column(), [_RG_SLAB_ROW],
                hole=[(2.0, 2.0)])
    if r["verdict"] != "UNREAD":
        return [f"an unread pad vertex did not stop the census: {r}"]
    if r["unread_count"] != 1:
        return [f"one vertex was withheld and {r['unread_count']} reported"]
    return []


def _case_a_pad_that_moves_nothing_REPAIRS_nothing():
    """THE SECOND QUESTION, AND THE CASE ABOVE IS WHY IT HAD TO EXIST.

    `a_pad_that_does_not_move_is_clear` asserts the census says CLEAR over an
    empty moving-set -- correct, and for a whole session that was the ONLY
    thing asked, so the write path would have taken the write.  A pad emit is
    irreversible ground under a standing building; "would not break anything"
    is not a reason to do it.

    MEASURED on Ulfsland: `sudrberg-keep` came back CLEAR with 0 of 2025
    lattice samples moving -- the engine reads its own target_y 121.840000 at
    every one -- while still carrying a `buried` refusal, because the beam is
    under the pad's DATUM and not under unwritten ground.  Both directions
    are asserted off ONE census each so the predicate cannot be satisfied by
    a fixture that simply never moves anything.
    """
    still = _rg_run(_RG_UNIT, {}, [_RG_SLAB_ROW])
    if not pad_repairs_nothing(still):
        return [f"a census with moving={still.get('moving')} was treated as a "
                f"repair: the emitter would re-level ground already at target"]
    moves = _rg_run(_RG_UNIT, _rg_low_column(), [])
    if not moves.get("moving"):
        return [f"the moving fixture moved nothing ({moves}), so the negative "
                f"direction proves nothing"]
    if pad_repairs_nothing(moves):
        return [f"a census with moving={moves['moving']} was called a no-op: "
                f"the emitter would refuse a write that DOES repair ground"]
    return []


_REGRADE_CASES = (
    ("a_fill_over_a_standing_piece_is_REFUSED",
     _case_a_fill_over_a_standing_piece_is_REFUSED),
    ("the_same_fill_with_nothing_standing_is_clear",
     _case_the_same_fill_with_nothing_standing_is_clear),
    ("a_cut_that_undermines_a_post_is_REFUSED",
     _case_a_cut_that_undermines_a_post_is_REFUSED),
    ("a_pad_that_does_not_move_is_clear",
     _case_a_pad_that_does_not_move_is_clear),
    ("an_unread_sample_is_UNREAD_not_clear",
     _case_an_unread_sample_is_UNREAD_not_clear),
    ("a_pad_that_moves_nothing_REPAIRS_nothing",
     _case_a_pad_that_moves_nothing_REPAIRS_nothing),
)


# ---------------------------------------------------------------------------
# THE SELFTEST FOR THE THREE GATES THAT WOULD HAVE STOPPED THE 45 PADS
# ---------------------------------------------------------------------------
# No world, no server, no ledger write.  Two of the cases read the REAL
# `settlements/plan.json` off disk, because that document IS the known-bad
# state and a fixture copy of it could be quietly corrected; the rest are
# built in memory from numbers MEASURED off the two recorded ledgers, because
# the denominator (45 Fresh42 pad writes, 73 Ulfsland ones, 18 flagged
# orderings) is not patchable from here.
#
# EVERY CASE IS A VERDICT.  Each returns the list of things it found wrong,
# quoting the number it judged on, so a failure says what it measured rather
# than that an assertion was false.

# MEASURED, ledger/runs/Fresh42/ledger.jsonl seq 1680, `terrain_write`
# role=site_pad site_id=wt-spawn: the write that put Ulfsland's 76.04 m datum
# into ground the same field reads at 33.785 m.
_WT_SPAWN_WRITE = {"over_clamp": 441, "max_fill": 45.527, "max_cut": 0.0}
# MEASURED, ledger/runs/Ulfsland/ledger.jsonl: the same five sites, on the
# world they were solved for.  All 73 of Ulfsland's pad writes read
# over_clamp 0; these are the five this session named.
_ULFSLAND_WRITES = {
    "wt-spawn": {"over_clamp": 0, "max_fill": 0.886, "max_cut": 0.0},
    "stenvik-hall-1": {"over_clamp": 0, "max_fill": 2.121, "max_cut": 0.0},
    "lh-south": {"over_clamp": 0, "max_fill": 3.705, "max_cut": 0.0},
    "hognest-castle": {"over_clamp": 0, "max_fill": 3.897, "max_cut": 0.0},
    "wt-peak": {"over_clamp": 0, "max_fill": 2.568, "max_cut": 0.0},
}


def _piece(seq: int, zone: tuple[int, int]) -> dict:
    """A standing body, in the shape `buildstage.piece_zone` reads."""
    return {"seq": seq, "op": "spawn_plan",
            "params": {"role": "town_body", "site_id": f"u{seq}",
                       "anchor": {"x": zone[0] * 64.0, "y": 0.0,
                                  "z": zone[1] * 64.0, "yaw": 0.0}}}


def _piece_without_position(seq: int) -> dict:
    return {"seq": seq, "op": "spawn_plan",
            "params": {"role": "town_body", "site_id": f"u{seq}"}}


# MEASURED on Fresh42: stenvik-house-1's pad (seq 1492) moves zones (7,14) and
# (8,14); (8,14) has held a body since seq 1486.  Ten of the eighteen name
# that zone.
_STENVIK_RESUME_PIECES = [_piece(1479, (9, 14)), _piece(1486, (8, 14)),
                          _piece(1503, (9, 15))]
_STENVIK_HOUSE1_ZONES = [(7, 14), (8, 14)]


def _raises(fn) -> str | None:
    try:
        fn()
    except SystemExit as e:                                # noqa: PERF203
        return str(e)
    return None


def _real_plan() -> tuple[Path, dict]:
    p = worldseed.plan_path(worldseed.DEFAULT_SEED)
    return p, json.loads(p.read_text())


def _case_the_recorded_plan_is_REFUSED_for_a_foreign_seed() -> list[str]:
    """THE KNOWN-BAD STATE, ON DISK.  `settlements/plan.json` declares seed
    Pirate68; a Fresh42 run read it and wrote 45 pads."""
    path, doc = _real_plan()
    declared, _w = plan_scope(doc)
    msg = _raises(lambda: require_plan_scope(path, doc, seed_="Fresh42",
                                             world_="Fresh42"))
    if msg is None:
        return [f"{path} declares seed {declared!r} and a Fresh42 run was "
                f"ACCEPTED -- this is the state that wrote 45 pads, worst "
                f"max_fill_m 110.95 with over_clamp 2070"]
    if declared not in msg:
        return [f"refused, but the message does not name the plan's own seed "
                f"{declared!r}: {msg[:160]}"]
    return []


def _case_the_recorded_plan_is_ACCEPTED_for_its_own_world() -> list[str]:
    """THE PASS DIRECTION.  A gate demonstrated only to fail refuses good
    work: the same document, for the world it was solved on, must go
    through."""
    path, doc = _real_plan()
    declared, _w = plan_scope(doc)
    msg = _raises(lambda: require_plan_scope(path, doc, seed_=declared,
                                             world_="Ulfsland"))
    return [] if msg is None else [
        f"{path} was REFUSED for its own seed {declared!r}: {msg[:200]}"]


def _case_NEUTER_drop_plan_declared_seed_the_defect_RETURNS() -> list[str]:
    """LOSS ARM.  Without the plan's own declaration the gate is the state
    that shipped: the document is taken at its word and the Pirate68 sites
    enter Fresh42 unopposed."""
    path, doc = _real_plan()
    msg = _raises(lambda: require_plan_scope(
        path, doc, seed_="Fresh42", world_="Fresh42",
        depends_on=("run_seed", "plan_declared_world", "run_world")))
    return [] if msg is None else [
        f"the neutered gate still refused, so `plan_declared_seed` is not "
        f"the term the refusal rests on: {msg[:160]}"]


def _case_NEUTER_drop_plan_declared_world_the_seed_still_REFUSES() -> list[str]:
    """SURVIVOR.  The world term is dropped and the seed alone must carry the
    known-bad pair."""
    path, doc = _real_plan()
    msg = _raises(lambda: require_plan_scope(
        path, doc, seed_="Fresh42", world_="Fresh42",
        depends_on=("plan_declared_seed", "run_seed")))
    return [] if msg is not None else [
        "dropping `plan_declared_world` let the Pirate68 plan into a Fresh42 "
        "run: the seed comparison is not doing the work it claims"]


def _case_NEUTER_drop_run_seed_a_foreign_WORLD_still_REFUSES() -> list[str]:
    """SURVIVOR, the other arm.  With the seed comparison disarmed, a plan
    that names another WORLD must still refuse -- `world-defaults.yaml` emits
    several worlds from ONE seed, so the world term is not decoration."""
    doc = {"seed": "Pirate68", "world": "Ulfsland", "towns": {},
           "outliers": []}
    msg = _raises(lambda: require_plan_scope(
        "<in-memory>", doc, seed_="Pirate68", world_="Fresh42",
        depends_on=("plan_declared_world", "run_world")))
    return [] if msg is not None else [
        "a plan declaring world 'Ulfsland' was accepted by a run building "
        "'Fresh42' once the seed term was dropped"]


def _case_a_plan_that_declares_no_seed_is_REFUSED() -> list[str]:
    """UNREAD IS NOT CLEAR.  An undeclared document is the one every run
    adopts."""
    msg = _raises(lambda: require_plan_scope(
        "<in-memory>", {"towns": {}, "outliers": []},
        seed_="Fresh42", world_="Fresh42"))
    return [] if msg is not None else [
        "a plan with no `seed` key was accepted"]


def _case_the_recorded_wt_spawn_write_is_REFUSED() -> list[str]:
    """THE CHECK THAT NEEDS NO DECLARATION.  441 of wt-spawn's samples cannot
    reach 76.04 m through a +/-8 m clamp, and the number was already in the
    record."""
    v = tcdata.clamp_verdict(_WT_SPAWN_WRITE)
    if v["verdict"] == "clear":
        return [f"over_clamp {v['over_clamp_samples']} / max_fill "
                f"{v['max_fill_m']} m read as clear against a "
                f"{v['clamp_m']} m clamp"]
    return []


def _case_every_recorded_Ulfsland_pad_is_clear() -> list[str]:
    """THE PASS DIRECTION, ON THE WORLD THESE DATUMS BELONG TO.  All 73 of
    Ulfsland's pad writes carry over_clamp 0; refusing any of them would make
    this gate worse than none."""
    bad = []
    for site, st in _ULFSLAND_WRITES.items():
        v = tcdata.clamp_verdict(st)
        if v["verdict"] != "clear":
            bad.append(f"{site} over_clamp {v['over_clamp_samples']} fill "
                       f"{v['max_fill_m']} m was REFUSED on its own world")
    return bad


def _case_NEUTER_drop_over_clamp_the_defect_RETURNS() -> list[str]:
    """LOSS ARM.  Without the sample count the gate sees only extremes, and
    the pre-fix emitter's answer comes back: clear."""
    v = tcdata.clamp_verdict(_WT_SPAWN_WRITE,
                      depends_on=("max_fill_m", "max_cut_m"))
    # The fill extreme alone is 45.527 m, which still exceeds the clamp, so
    # the honest loss arm drops BOTH derived terms and keeps only what the
    # shipped emitter actually consulted: nothing.
    v2 = tcdata.clamp_verdict(_WT_SPAWN_WRITE, depends_on=())
    if v2["verdict"] != "clear":
        return [f"with every term dropped the gate still refused: {v2}"]
    if v["verdict"] == "clear":
        return [f"dropping only `over_clamp_samples` also cleared the write "
                f"({v['max_fill_m']} m of fill against a {v['clamp_m']} m "
                f"clamp), so the fill term is not carrying anything"]
    return []


def _case_NEUTER_drop_the_extremes_over_clamp_still_REFUSES() -> list[str]:
    """SURVIVOR.  The sample count alone must carry the refusal."""
    v = tcdata.clamp_verdict(_WT_SPAWN_WRITE, depends_on=("over_clamp_samples",))
    return [] if v["verdict"] != "clear" else [
        f"over_clamp {v['over_clamp_samples']} alone did not refuse"]


def _case_the_recorded_stenvik_resume_is_REFUSED() -> list[str]:
    """THE 18, AT THE EMITTER.  stenvik-house-1's pad moves (7,14) and (8,14);
    (8,14) has held a body since seq 1486."""
    occ, unread = occupied_zones(_STENVIK_RESUME_PIECES)
    fz = frozen_ground(_STENVIK_HOUSE1_ZONES, occ, unread)
    if fz["verdict"] == "clear":
        return [f"a pad moving {_STENVIK_HOUSE1_ZONES} read as clear while "
                f"zone (8, 14) has held a piece since seq 1486"]
    if fz["held_since_seq"] != 1486:
        return [f"refused, but named seq {fz['held_since_seq']} rather than "
                f"the piece at 1486 in zone {fz['shared']}"]
    return []


def _case_the_same_pad_before_any_body_is_clear() -> list[str]:
    """THE PASS DIRECTION.  Identical zones, nothing standing: the declared
    order, which must not be refused."""
    fz = frozen_ground(_STENVIK_HOUSE1_ZONES, {}, False)
    return [] if fz["verdict"] == "clear" else [
        f"a pad on empty ground was refused: {fz}"]


def _case_a_pad_far_from_every_body_is_clear() -> list[str]:
    """GROUND_ELSEWHERE, at the emitter.  24 of Fresh42's 42 flagged writes
    were this, and a gate that refuses them is measuring the wrong thing."""
    occ, unread = occupied_zones(_STENVIK_RESUME_PIECES)
    fz = frozen_ground([(-14, 29)], occ, unread)
    return [] if fz["verdict"] == "clear" else [
        f"a pad in vestvik was refused by a body standing in stenvik: {fz}"]


def _case_NEUTER_drop_occupied_zones_the_18_RETURN() -> list[str]:
    """LOSS ARM.  Blind to the standing bodies, the emitter is the one that
    wrote the 18."""
    occ, unread = occupied_zones(_STENVIK_RESUME_PIECES)
    fz = frozen_ground(_STENVIK_HOUSE1_ZONES, occ, unread,
                       depends_on=("zones_this_write_moves",))
    return [] if fz["verdict"] == "clear" else [
        f"the neutered gate still refused, so the occupancy term is not what "
        f"the refusal rests on: {fz}"]


def _case_NEUTER_drop_write_zones_FAILS_CLOSED() -> list[str]:
    """SURVIVOR, and in the safe direction.  Losing the write's own zones is
    'I cannot measure which ground moves', which is not 'none of it'."""
    occ, unread = occupied_zones(_STENVIK_RESUME_PIECES)
    fz = frozen_ground(_STENVIK_HOUSE1_ZONES, occ, unread,
                       depends_on=("zones_a_standing_piece_occupies",))
    return [] if fz["verdict"] != "clear" else [
        f"dropping `zones_this_write_moves` opened the gate instead of "
        f"closing it: {fz}"]


def _case_a_body_with_no_position_is_not_clear() -> list[str]:
    """UNREAD IS NOT CLEAR, the same reading `buildstage.replay` makes."""
    occ, unread = occupied_zones([_piece_without_position(1486)])
    fz = frozen_ground([(-14, 29)], occ, unread)
    if not unread:
        return ["a piece carrying no anchor and no pos was not reported "
                "unreadable"]
    return [] if fz["verdict"] != "clear" else [
        f"a write was cleared while a standing piece names no position: {fz}"]


def _case_a_write_naming_no_zones_is_not_clear() -> list[str]:
    """The other closed direction: a write that cannot say what it moves."""
    occ, unread = occupied_zones(_STENVIK_RESUME_PIECES)
    fz = frozen_ground([], occ, unread)
    return [] if fz["verdict"] != "clear" else [
        f"a terrain write naming no zones was cleared: {fz}"]


def _case_plan_path_is_DERIVED_from_the_seed() -> list[str]:
    """THE DEFAULT IS THE DEFECT.  `plan.Fresh42.json` has sat beside the
    default seed's plan since 2026-09-23 11:04:49 -- solved on seed Fresh42,
    seed_hash -1894280788, with stenvik-hall-1 at (350.1, -208.8) target_y
    31.62 instead of (481.1, 920.4) target_y 47.03 -- and the build read the
    2026-09-16 Pirate68 document because `--plan-json` defaulted to a name
    with no seed in it.  The plan is a function of the SEED, so the one the
    build reads (`worldseed.plan_path`) is the one its own header says was
    solved on that seed."""
    bad = []
    for seed_ in ("Fresh42", worldseed.DEFAULT_SEED):
        p = worldseed.plan_path(seed_)
        declared, _w = plan_scope(json.loads(p.read_text()))
        if declared != seed_:
            bad.append(f"seed {seed_!r} reads {p.name}, solved on {declared!r}")
    return bad


def _case_the_Fresh42_plan_is_ACCEPTED_for_Fresh42_and_REFUSED_here() -> list[str]:
    """THE CORRECTED ARTEFACT, BOTH DIRECTIONS.  A gate that only refuses the
    bad document has not shown it can admit the good one."""
    p = worldseed.plan_path("Fresh42")
    if p.name != "plan.Fresh42.json":
        return [f"no Fresh42-solved plan to test; resolved {p.name!r}"]
    doc = json.loads(p.read_text())
    bad = []
    msg = _raises(lambda: require_plan_scope(p, doc, seed_="Fresh42",
                                             world_="Fresh42"))
    if msg is not None:
        bad.append(f"the Fresh42-solved plan was REFUSED for Fresh42: "
                   f"{msg[:160]}")
    msg = _raises(lambda: require_plan_scope(p, doc, seed_="Pirate68",
                                             world_="Ulfsland"))
    if msg is None:
        bad.append("the Fresh42-solved plan was ACCEPTED for Ulfsland -- the "
                   "leak runs in both directions and only one end is guarded")
    return bad


def _case_every_unit_of_the_Fresh42_plan_clears_the_clamp() -> list[str]:
    """THE CLAMP GATE ADMITS THE CORRECT PLAN.  MEASURED: 43 of 43 units in
    `plan.Fresh42.json` carry over_clamp 0, worst fill 6.53 m, worst cut
    5.06 m -- inside the +/-8 m clamp -- while 43 of the 45 writes the build
    actually made carry over_clamp 110..2070.  Same gate, opposite answers,
    on the two documents."""
    p = worldseed.plan_path("Fresh42")
    if p.name != "plan.Fresh42.json":
        return [f"no Fresh42-solved plan to test; resolved {p.name!r}"]
    doc = json.loads(p.read_text())
    units = [(b["id"], b["pad"]) for t in doc["towns"].values()
             for b in t["buildings"]]
    units += [(r["id"], r["pad"]) for r in doc["outliers"]]
    bad = []
    for uid, pad in units:
        v = tcdata.clamp_verdict({"over_clamp": pad.get("over_clamp") or 0,
                           "max_fill": pad.get("max_fill_m") or 0.0,
                           "max_cut": pad.get("max_cut_m") or 0.0})
        if v["verdict"] != "clear":
            bad.append(f"{uid} refused: {v}")
    if not units:
        bad.append("the Fresh42-solved plan carries no units to judge")
    return bad


_SCOPE_CASES = (
    ("the_recorded_plan_is_REFUSED_for_a_foreign_seed",
     _case_the_recorded_plan_is_REFUSED_for_a_foreign_seed),
    ("the_recorded_plan_is_ACCEPTED_for_its_own_world",
     _case_the_recorded_plan_is_ACCEPTED_for_its_own_world),
    ("NEUTER_drop_plan_declared_seed_the_defect_RETURNS",
     _case_NEUTER_drop_plan_declared_seed_the_defect_RETURNS),
    ("NEUTER_drop_plan_declared_world_the_seed_still_REFUSES",
     _case_NEUTER_drop_plan_declared_world_the_seed_still_REFUSES),
    ("NEUTER_drop_run_seed_a_foreign_WORLD_still_REFUSES",
     _case_NEUTER_drop_run_seed_a_foreign_WORLD_still_REFUSES),
    ("a_plan_that_declares_no_seed_is_REFUSED",
     _case_a_plan_that_declares_no_seed_is_REFUSED),
    ("the_recorded_wt_spawn_write_is_REFUSED",
     _case_the_recorded_wt_spawn_write_is_REFUSED),
    ("every_recorded_Ulfsland_pad_is_clear",
     _case_every_recorded_Ulfsland_pad_is_clear),
    ("NEUTER_drop_over_clamp_the_defect_RETURNS",
     _case_NEUTER_drop_over_clamp_the_defect_RETURNS),
    ("NEUTER_drop_the_extremes_over_clamp_still_REFUSES",
     _case_NEUTER_drop_the_extremes_over_clamp_still_REFUSES),
    ("the_recorded_stenvik_resume_is_REFUSED",
     _case_the_recorded_stenvik_resume_is_REFUSED),
    ("the_same_pad_before_any_body_is_clear",
     _case_the_same_pad_before_any_body_is_clear),
    ("a_pad_far_from_every_body_is_clear",
     _case_a_pad_far_from_every_body_is_clear),
    ("NEUTER_drop_occupied_zones_the_18_RETURN",
     _case_NEUTER_drop_occupied_zones_the_18_RETURN),
    ("NEUTER_drop_write_zones_FAILS_CLOSED",
     _case_NEUTER_drop_write_zones_FAILS_CLOSED),
    ("a_body_with_no_position_is_not_clear",
     _case_a_body_with_no_position_is_not_clear),
    ("a_write_naming_no_zones_is_not_clear",
     _case_a_write_naming_no_zones_is_not_clear),
    ("plan_path_is_DERIVED_from_the_seed",
     _case_plan_path_is_DERIVED_from_the_seed),
    ("the_Fresh42_plan_is_ACCEPTED_for_Fresh42_and_REFUSED_for_Ulfsland",
     _case_the_Fresh42_plan_is_ACCEPTED_for_Fresh42_and_REFUSED_here),
    ("every_unit_of_the_Fresh42_plan_clears_the_clamp",
     _case_every_unit_of_the_Fresh42_plan_clears_the_clamp),
)


# ---------------------------------------------------------------------------
# THE PAD A UNIT IS WRITTEN ON COVERS ITS BODY AT ITS YAW.  See
# PAD_EXTENT_DEPENDS_ON in terraform/flatten.py, whose own selftest neuters
# every term on geometry built in memory.  These drive the SAME terms through
# `unit_records`, on a stub plan in the shape `plan.py`/`layout.py` hand it at
# placement time, with the body measured in memory by the emitter's own
# `xz_footprint` and registered under a fixture name no library body has.
# ---------------------------------------------------------------------------
_PAD_FIXTURE_BODY = "fixture-square-20.1.blueprint"


def _pad_stub(yaw: float) -> dict:
    objs, geom = FL._slab(20.1, 20.1)
    FL._BODY_EXTENTS[(_PAD_FIXTURE_BODY, float(yaw))] = {
        **FL.body_extents(objs, yaw, geom), "blueprint": _PAD_FIXTURE_BODY}
    b = {"id": "fx-hall", "body": _PAD_FIXTURE_BODY, "sha256": "fixture",
         "x": 350.1, "z": -208.8, "yaw_deg": yaw, "footprint": [20.1, 20.1],
         "pad": {"width_m": 26.1, "depth_m": 26.1, "target_y": 0.0},
         "pieces": 1, "role": "hall"}
    return {"towns": {"fx": {"buildings": [b], "patch": "fixture"}},
            "outliers": []}


def _stub_cover(yaw: float, depends_on: tuple = FL.PAD_EXTENT_DEPENDS_ON
                ) -> tuple[dict, dict]:
    u = unit_records(_pad_stub(yaw), "fx", depends_on)[0]
    w, d = FL.pad_extent({"solved": {"footprint_orientation": pad_text(u)}})
    bw, bd = FL._BODY_EXTENTS[(_PAD_FIXTURE_BODY, float(yaw))]["at_yaw"]
    return u, FL.body_cover((u["x"], u["z"]), w, d, bw, bd, APRON_M)


def _case_a_yawed_unit_is_written_on_a_pad_under_all_of_its_body(
        ) -> list[str]:
    u, cover = _stub_cover(30.0)
    return [] if cover["verdict"] == "covered" else [
        f"unit_records wrote {u['pad_w']} x {u['pad_d']} for a body "
        f"{cover['body_at_yaw']} at yaw 30: {cover['verdict']}, "
        f"{cover['unstamped']} vertices unstamped"]


def _case_NEUTER_drop_body_yaw_the_unit_overhang_RETURNS() -> list[str]:
    """LOSS ARM through `unit_records`: measured unturned, the unit is
    written on its declared 26.1 m pad and the body at yaw 30 overhangs."""
    u, cover = _stub_cover(30.0, tuple(t for t in FL.PAD_EXTENT_DEPENDS_ON
                                       if t != "body_yaw"))
    return [] if cover["verdict"] == "overhangs" else [
        f"without body_yaw the unit's {u['pad_w']} x {u['pad_d']} pad still "
        f"covers its body ({cover['verdict']})"]


def _case_a_yaw_0_unit_SURVIVES_byte_identical() -> list[str]:
    """SURVIVOR: an unturned unit is written on the plan's own pad, byte for
    byte, through the placements text and back (a verdict)."""
    u, _cover = _stub_cover(0.0)
    back = FL.pad_extent({"solved": {"footprint_orientation": pad_text(u)}})
    v = FL._identical(back, (26.1, 26.1))
    return [] if v == "identical" else [
        f"a yaw-0 unit is written on {back!r}, {v} from the declared 26.1"]


_PAD_CASES = (
    ("a_yawed_unit_is_written_on_a_pad_under_all_of_its_body",
     _case_a_yawed_unit_is_written_on_a_pad_under_all_of_its_body),
    ("NEUTER_drop_body_yaw_the_unit_overhang_RETURNS",
     _case_NEUTER_drop_body_yaw_the_unit_overhang_RETURNS),
    ("a_yaw_0_unit_SURVIVES_byte_identical",
     _case_a_yaw_0_unit_SURVIVES_byte_identical),
)


# ---------------------------------------------------------------------------
# THE ROOM A SIBLING LEAVES (SIBLING_ROOM_DEPENDS_ON).  CONSTRUCTED fixtures in
# the shape of the measured state: `vestvik-hut-1`'s rings (1.0 m x4, 1.414 m
# x4, 2.236 m x2) beside a long hall (8.4 x 17.0 m footprint, 11.04 m away,
# circle room 1.06 m) whose nearest piece is 2.01 m from the hut's anchor.
# ---------------------------------------------------------------------------
_HUT = {"id": "fx-hut", "x": 0.0, "z": 0.0, "pad_w": 12.4, "pad_d": 12.4}
_HUT_ROWS = ([("p", x, 0.0, z, 0.0) for x, z in
              ((1, 0), (-1, 0), (0, 1), (0, -1),
               (1, 1), (1, -1), (-1, 1), (-1, -1), (2, 1), (-2, -1))])


def _hall(nearest_m: float) -> dict:
    return {"id": "fx-hall", "x": 11.04, "z": 0.0, "pad_w": 14.0,
            "pad_d": 22.0, "foot_w": 8.4, "foot_d": 17.0,
            "_nearest_m": nearest_m}


def _radius_of(nearest_m: float) -> tuple[float | None, int]:
    """check_radius for the hut beside the hall, plus how many sibling plans
    it asked for."""
    asked: list[str] = []

    def pieces(o: dict) -> list[tuple[float, float]]:
        asked.append(o["id"])
        return [(0.0, o["_nearest_m"]), (14.0, 6.0), (9.0, -7.0)]

    try:
        r = check_radius(_HUT, [_HUT, _hall(nearest_m)], 12.0, _HUT_ROWS,
                         piece_source=pieces)
    except SystemExit:
        return None, len(asked)
    return r, len(asked)


def _case_a_tall_sibling_whose_circle_is_tight_leaves_the_hut_a_radius(
        ) -> list[str]:
    """MEASURED on vestvik-hut-1: the hall's circle leaves 1.06 m, its nearest
    piece 2.01 m, and the hut's first gap is 1.207 m."""
    r, asked = _radius_of(2.01)
    return [] if r == 1.21 and asked == 1 else [
        f"vestvik-hut-1's shape was given radius {r} after asking for "
        f"{asked} sibling plan(s); the hall's nearest piece is 2.01 m away, "
        f"so 1.21 m is exact"]


def _case_NEUTER_the_circle_alone_the_hut_REFUSES_again() -> list[str]:
    prior = globals()["_SIBLING_ROOM_MODE"]
    globals()["_SIBLING_ROOM_MODE"] = "circle"
    try:
        r, _ = _radius_of(2.01)
    finally:
        globals()["_SIBLING_ROOM_MODE"] = prior
    return [] if r is None else [
        f"with the circle bound alone the hut was still given {r}"]


def _case_a_sibling_whose_pieces_truly_crowd_the_hut_still_refuses(
        ) -> list[str]:
    """A refusal is a real answer when the pieces confirm it: a piece 1.2 m
    away leaves 0.7 m, under the first gap."""
    r, _ = _radius_of(1.2)
    return [] if r is None else [
        f"a sibling piece 1.2 m from the hut's anchor still left radius {r}"]


def _case_NEUTER_ignore_the_siblings_the_crowded_hut_is_ACCEPTED() -> list[str]:
    prior = globals()["_SIBLING_ROOM_MODE"]
    globals()["_SIBLING_ROOM_MODE"] = "blind"
    try:
        r, _ = _radius_of(1.2)
    finally:
        globals()["_SIBLING_ROOM_MODE"] = prior
    return [] if r is not None else ["a blind re-measure still refused"]


def _case_a_unit_the_circle_solves_is_untouched_and_asks_for_no_plan(
        ) -> list[str]:
    """SURVIVOR (a verdict): a unit with room to spare is answered by the
    circle alone -- same radius as before, no sibling plan made."""
    far = dict(_hall(2.01), x=60.0)
    asked: list[str] = []
    r = check_radius(_HUT, [_HUT, far], 12.0, _HUT_ROWS,
                     piece_source=lambda o: asked.append(o["id"]) or [])
    return [] if r == 2.49 and not asked else [
        f"an unpressed unit was given {r} after {len(asked)} plan request(s); "
        f"the circle alone answers 2.49"]


_RADIUS_CASES = (
    ("a_tall_sibling_whose_circle_is_tight_leaves_the_hut_a_radius",
     _case_a_tall_sibling_whose_circle_is_tight_leaves_the_hut_a_radius),
    ("NEUTER_the_circle_alone_the_hut_REFUSES_again",
     _case_NEUTER_the_circle_alone_the_hut_REFUSES_again),
    ("a_sibling_whose_pieces_truly_crowd_the_hut_still_refuses",
     _case_a_sibling_whose_pieces_truly_crowd_the_hut_still_refuses),
    ("NEUTER_ignore_the_siblings_the_crowded_hut_is_ACCEPTED",
     _case_NEUTER_ignore_the_siblings_the_crowded_hut_is_ACCEPTED),
    ("a_unit_the_circle_solves_is_untouched_and_asks_for_no_plan",
     _case_a_unit_the_circle_solves_is_untouched_and_asks_for_no_plan),
)


# ---------------------------------------------------------------------------
# A ZONE UPGRADE WORLD CANNOT GENERATE (ZONE_REPAIR_DEPENDS_ON).  An in-memory
# Upgrade World with ONE staged queue and the three behaviours measured on
# hognest-castle and sudrberg-keep: a zone SpawnZone refuses stays ungenerated
# under any number of asks; `empty` marks a zone generated and spawns nothing;
# `zones_restore` puts the control object into a generated zone.  A zone that
# is merely a race generates on its second ask.
# ---------------------------------------------------------------------------
_HOG_ZONES = [[8, 21], [8, 22], [9, 21], [9, 22]]
_HOG_GEN = {"op": "zones_generate",
            "params": {"pos": [530.0, 1348.0], "max_m": 78.37,
                       "zones": _HOG_ZONES, "role": "site_pad",
                       "site_id": "fx-castle"},
            "wire": ["zones_generate pos=530,1348 max=78.37"],
            "expect": {"zone_ctrl": 4}}


def _zone_world(srv_cls, stuck: tuple[int, int], asks_to_cure: int | None):
    """`srv_cls` instance modelling the queue; `asks_to_cure` is how many
    ordinary asks a stuck zone refuses before it generates (None: never)."""
    state = {"ctrl": set(), "marked": set(), "queue": None, "asks": 0}

    class Queue(srv_cls):
        def __init__(self):
            super().__init__(dry=False)

        def probe(self) -> float:
            return 0.0

        def command(self, cmd: str) -> str:
            self.sent.append(cmd)
            if cmd == "consoleCommand stop":
                state["queue"] = None
            elif cmd == "consoleCommand start" and state["queue"]:
                kind, zones = state["queue"]
                state["queue"] = None
                for z in zones:
                    if kind == "generate":
                        if z == stuck:
                            state["asks"] += 1
                            if asks_to_cure is None or state["asks"] <= \
                                    asks_to_cure:
                                continue
                        state["ctrl"].add(z)
                    elif kind == "empty":
                        state["marked"].add(z)
                    elif kind == "restore" and z in state["marked"]:
                        state["ctrl"].add(z)
            elif cmd.split()[:1] == ["players"]:
                return "Online 0"
            return f"Command '{cmd}' executed."

        def console(self, cmd: str, settle: float = 2.0) -> list[str]:
            self.sent.append(cmd)
            t = cmd.split()
            if t[:1] == ["stop"]:
                state["queue"] = None
                return []
            if t[:1] == ["uw_check"]:
                return ["No operations queued."] if state["queue"] is None \
                    else ["1 operation queued."]
            if not t:
                return []
            kv = dict(a.split("=", 1) for a in t[1:] if "=" in a)
            px, pz = (float(v) for v in kv["pos"].split(","))
            r = float(kv["max"])
            near = [(zx, zz) for zx in range(-40, 60) for zz in range(-60, 60)
                    if math.hypot(zx * 64 - px, zz * 64 - pz) <= r]
            if t[0] == "zones_generate":
                done = state["ctrl"] | state["marked"]
                pick = [z for z in near if z not in done]
                state["queue"] = ("empty" if "empty" in t[1:] else "generate",
                                  pick)
                return [f"Generate zones less than {r:g} meters away. "
                        f"{len(pick)} zones: 84595 skipped by the command."]
            if t[0] == "zones_restore":
                pick = [z for z in near if z in state["marked"]
                        and z not in state["ctrl"]]
                state["queue"] = ("restore", pick)
                return [f"{len(pick)} zones: skipped by the command."]
            if t[0] == "objects_count":
                n = sum(1 for z in state["ctrl"]
                        if math.hypot(z[0] * 64 - px, z[1] * 64 - pz) <= r)
                return [f"Total: {n}"] + ([f"_ZoneCtrl: {n}"] if n else [])
            return []

    return Queue(), state


def _zone_run(stuck: tuple[int, int], registered: bool,
              asks_to_cure: int | None) -> dict:
    """`generate_zones` over the model: whether it raised, the ops it wrote,
    and the zones that ended with a control object."""
    import tempfile
    with FL.memory_session("ZoneRepairSelftest") as b:
        R = sys.modules[type(b).__module__].R
        saved = (R.STAGED_SETTLE_S, R.STAGED_POLL_S)
        R.STAGED_SETTLE_S, R.STAGED_POLL_S = 0.05, 0.01
        b.srv, state = _zone_world(R.Server, stuck, asks_to_cure)
        with tempfile.TemporaryDirectory(prefix="zone-dump-") as td:
            dump = Path(td) / "locations.json"
            dump.write_text(json.dumps({"locations": (
                [{"prefab": "MountainCave02", "x": stuck[0] * 64.0,
                  "z": stuck[1] * 64.0}] if registered else [])}))
            raised = None
            try:
                generate_zones(b, _HOG_GEN, "fx-castle", "fx", {},
                               dump_path=str(dump))
            except RuntimeError as exc:
                raised = type(exc).__name__
            finally:
                R.STAGED_SETTLE_S, R.STAGED_POLL_S = saved
        recs = [r for r in b.led.records() if r["op"] in
                ("zones_generate", "zones_restore")]
    return {"raised": raised,
            "ops": [("empty" if r["params"].get("empty") else r["op"],
                     r["meta"].get("forfeits") is not None) for r in recs],
            "ctrl": sorted(state["ctrl"]),
            "all4": all(tuple(z) in state["ctrl"] for z in _HOG_ZONES)}


def _case_a_zone_spawnzone_refuses_and_the_dump_registers_is_repaired(
        ) -> list[str]:
    """hognest-castle's shape: zone (9,22) holds a registered MountainCave02
    and no ask generates it."""
    r = _zone_run((9, 22), True, None)
    want = [("zones_generate", False), ("zones_generate", False),
            ("empty", True), ("zones_restore", False)]
    return [] if (r["raised"] is None and r["all4"] and r["ops"] == want) \
        else [f"expected the site-wide ask, one scoped re-issue, the empty "
              f"mark (forfeit declared) and the restore, every zone "
              f"controlled; got raised={r['raised']} ops={r['ops']} "
              f"controlled={r['ctrl']}"]


def _case_NEUTER_no_repair_the_castle_REFUSES_again() -> list[str]:
    prior = globals()["_ZONE_REPAIR_MODE"]
    globals()["_ZONE_REPAIR_MODE"] = "none"
    try:
        r = _zone_run((9, 22), True, None)
    finally:
        globals()["_ZONE_REPAIR_MODE"] = prior
    return [] if r["raised"] == "PostconditionFailed" else [
        f"with no repair the unit did not refuse: {r}"]


def _case_a_zone_nothing_explains_is_still_refused() -> list[str]:
    """No registration in the zone: an unexplained fault is not forfeited."""
    r = _zone_run((9, 22), False, None)
    return [] if (r["raised"] == "PostconditionFailed"
                  and ("empty", True) not in r["ops"]) else [
        f"an unexplained ungenerated zone was not refused: {r}"]


def _case_NEUTER_forfeit_blind_the_unexplained_zone_is_FORFEITED() -> list[str]:
    prior = globals()["_ZONE_REPAIR_MODE"]
    globals()["_ZONE_REPAIR_MODE"] = "blind"
    try:
        r = _zone_run((9, 22), False, None)
    finally:
        globals()["_ZONE_REPAIR_MODE"] = prior
    return [] if ("empty", True) in r["ops"] else [
        f"a blind repair did not forfeit the unexplained zone: {r}"]


def _case_a_race_is_cured_by_one_reissue_and_nothing_is_forfeited() -> list[str]:
    """SURVIVOR (a verdict): a zone that generates on the second ask is cured
    by the scoped re-issue; no empty mark, no restore, no forfeit."""
    r = _zone_run((9, 22), True, 1)
    want = [("zones_generate", False), ("zones_generate", False)]
    return [] if (r["raised"] is None and r["all4"] and r["ops"] == want) \
        else [f"a race was not cured by one re-issue: {r}"]


_ZONE_CASES = (
    ("a_zone_spawnzone_refuses_and_the_dump_registers_is_repaired",
     _case_a_zone_spawnzone_refuses_and_the_dump_registers_is_repaired),
    ("NEUTER_no_repair_the_castle_REFUSES_again",
     _case_NEUTER_no_repair_the_castle_REFUSES_again),
    ("a_zone_nothing_explains_is_still_refused",
     _case_a_zone_nothing_explains_is_still_refused),
    ("NEUTER_forfeit_blind_the_unexplained_zone_is_FORFEITED",
     _case_NEUTER_forfeit_blind_the_unexplained_zone_is_FORFEITED),
    ("a_race_is_cured_by_one_reissue_and_nothing_is_forfeited",
     _case_a_race_is_cured_by_one_reissue_and_nothing_is_forfeited),
)


# ---------------------------------------------------------------------------
# PORTALS AND WORLD SCOPE.  Each class is shown on its KNOWN-BAD state from
# Fresh42 drive 2, and each NEUTER restores the shipped rule and must bring
# the measured defect back.
# ---------------------------------------------------------------------------
_CARRIER = "hs_ashlands_neletit_portalhubtower.blueprint"   # 15 portal_wood


def _emitted_portals() -> int:
    import importlib.util  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import to_rcon_plan as TRP  # noqa: PLC0415
    from fixtures import PORTAL_PREFABS  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    body = lib.locate_by_filename(_CARRIER)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "plan.txt"
        rc = TRP.main([str(body), "--at", "0", "30", "0", "--align",
                       "floor-center", "--out", str(out)])
        if rc != 0:
            raise AssertionError(f"the emitter refused {_CARRIER} rc {rc}")
        return sum(1 for r in VP.plan_rows(out) if r[0] in PORTAL_PREFABS)


def _case_a_body_carrying_portals_emits_none() -> list[str]:
    """MEASURED drive 2: spawn_plan seq 1623 (hognest-castle) carried 15
    portal_wood and seq 1636 (sudrberg-keep) 1 -- 16 BLANK tags."""
    n = _emitted_portals()
    return [] if n == 0 else [f"{_CARRIER} still emits {n} portal row(s)"]


def _case_NEUTER_emit_carried_portals_the_15_RETURN() -> list[str]:
    import to_rcon_plan as TRP  # noqa: PLC0415
    TRP.CARRIED_PORTALS = "emit"
    try:
        n = _emitted_portals()
    finally:
        TRP.CARRIED_PORTALS = "drop"
    return [] if n == 15 else [f"the shipped emitter carried {n}, not 15"]


def _foreign_dump(fn) -> str | None:
    import tempfile  # noqa: PLC0415
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"seed": "Pirate68", "locations": []}, f)
    old = os.environ.get(LOCATIONS_ENV)
    os.environ[LOCATIONS_ENV] = f.name
    try:
        return _raises(fn)
    finally:
        if old is None:
            os.environ.pop(LOCATIONS_ENV, None)
        else:
            os.environ[LOCATIONS_ENV] = old
        Path(f.name).unlink()


def _case_a_Pirate68_dump_is_REFUSED_for_a_Fresh42_run() -> list[str]:
    """MEASURED drive 2: 13 settlement units refused on Pirate68's locations;
    offline 14 of 49 Fresh42 units refuse under that dump, 0 under their own."""
    msg = _foreign_dump(lambda: locations_dump(seed_="Fresh42"))
    return [] if msg and "Pirate68" in msg else [
        f"a Pirate68 dump was accepted for Fresh42 ({msg!r})"]


def _case_NEUTER_drop_dump_declared_seed_the_foreign_dump_is_ACCEPTED(
        ) -> list[str]:
    msg = _foreign_dump(lambda: locations_dump(
        seed_="Fresh42", depends_on=("plan_locations_dump",)))
    return [] if msg is None else [f"still refused without the term: {msg}"]


def _case_the_Fresh42_plan_names_its_own_dump() -> list[str]:
    p = worldseed.plan_path("Fresh42")
    doc = json.loads(p.read_text())
    path = locations_dump(doc, seed_="Fresh42")
    seed_ = json.loads(Path(path).read_text()).get("seed")
    return [] if seed_ == "Fresh42" else [f"{path} declares seed {seed_!r}"]


def _case_an_anchor_is_THIS_worlds_ledger_body() -> list[str]:
    """MEASURED drive 2: wt-north's tower was refused and its pair still stood,
    because `/tmp/settle/build/wt-north/wt-north.plan` was Ulfsland's."""
    global SCRATCH_ROOT, ANCHOR_SOURCE  # noqa: PLW0603
    import tempfile  # noqa: PLC0415
    keep = SCRATCH_ROOT
    bad = []
    with tempfile.TemporaryDirectory() as d:
        SCRATCH_ROOT = Path(d)
        (Path(d) / "wt-north").mkdir()
        (Path(d) / "wt-north" / "wt-north.plan").write_text(
            "spawn_object stone_floor_2x2 pos=31.92,144.78,1182 rot=0,0,0 "
            "from=0,0,0\n")
        try:
            rows, _why = anchor_plan([], lambda s: b"", "wt-north", "wt-north")
            if rows is not None:
                bad.append("a ledger with no spawn_plan was read as a body")
            ANCHOR_SOURCE = "scratch"
            rows, _why = anchor_plan([], lambda s: b"", "wt-north", "wt-north")
            if rows is None:
                bad.append("NEUTER: the shipped scratch read found no plan -- "
                           "the known-bad state is not reproduced")
        finally:
            ANCHOR_SOURCE = "ledger"
            SCRATCH_ROOT = keep
    live = [{"seq": 7, "op": "spawn_plan",
             "params": {"site_id": "wt-north", "plan_sha256": "x"}}]
    rows, _why = anchor_plan(live, lambda s: (
        b"spawn_object stone_floor_2x2 pos=1,2,3 rot=0,0,0 from=0,0,0\n"),
        "wt-north")
    if not rows:
        bad.append("a live ledger spawn_plan was not read as the body")
    dead = live + [{"seq": 8, "op": "retire", "params": {"retires": [7]}}]
    rows, _why = anchor_plan(dead, lambda s: b"", "wt-north")
    if rows is not None:
        bad.append("a RETIRED spawn_plan was read as a standing body")
    return bad


def _case_the_hub_is_THIS_worlds_and_NEUTER_reads_Ulfsland() -> list[str]:
    """MEASURED drive 2: all 14 settlement hub ends stood in Ulfsland's ring,
    629 m from Fresh42's hall."""
    global HUB_SOURCE  # noqa: PLW0603
    # The case asserts about Fresh42's hub, so it runs AS Fresh42; the
    # placement reader's scope check is right to refuse a cross-world read.
    with worldseed.scope("Fresh42", "Fresh42"):
        s = hub_placement("Fresh42")["solved"]
        bad = [] if (s["x"], s["z"]) == (-220.5, -411.5) else [
            f"Fresh42's hub resolved to ({s['x']}, {s['z']})"]
        HUB_SOURCE = "ulfsland"
        try:
            u = hub_placement("Fresh42")["solved"]
        finally:
            HUB_SOURCE = "own_world"
    if (u["x"], u["z"]) != (-292.5, 213.5):
        bad.append(f"NEUTER did not restore Ulfsland's ring ({u['x']}, "
                   f"{u['z']})")
    return bad


# The drive-2 settlement ring, verbatim from ledger/runs/Fresh42 (portal
# records with meta.end == "hub", seqs 1594..1823), all at yaw 135.
_D2_RING = {
    "u-stenvik": (-282.5, 35.611, 225.5), "u-castle": (-288.5, 34.125, 229.5),
    "u-sudrberg": (-276.5, 39.067, 209.5), "u-wtspawn": (-302.5, 33.724, 227.5),
    "u-wttown": (-282.5, 40.257, 199.5), "u-wtpeak": (-295.5, 38.575, 196.5),
    "u-wtnorth": (-309.5, 33.309, 217.5), "u-wteast": (-277.5, 36.413, 222.5),
    "u-lhsouth": (-277.5, 39.707, 204.5), "u-lheast": (-307.5, 32.632, 223.5),
    "u-lhwest": (-283.5, 34.181, 230.5), "u-treesth": (-277.5, 35.09, 227.5),
    "u-treewest": (-306.5, 35.806, 198.5),
    "u-treenear": (-277.5, 39.995, 199.5)}


def _ring_boards(depends_on: tuple) -> tuple[set, set]:
    """(tags whose board is nearer another arch, tags with no board)."""
    W = _waypoints()
    wrong, none = set(), set()
    for tag, at in _D2_RING.items():
        others = [(*p, 135.0) for t, p in _D2_RING.items() if t != tag]
        pieces = None
        for side in ("left", "right"):
            try:
                pieces = W.portal_guidepost(*at, 135.0, "board", side=side,
                                            neighbours=others,
                                            depends_on=depends_on)
                break
            except W.WaypointError:
                continue
        if pieces is None:
            none.add(tag)
            continue
        bd = next(p for p in pieces if p.prefab == W.BOARD_PREFAB)
        near = min(_D2_RING, key=lambda t: math.dist((bd.x, bd.y, bd.z),
                                                      _D2_RING[t]))
        if near != tag:
            wrong.add(tag)
    return wrong, none


def _case_every_ring_board_names_its_own_door() -> list[str]:
    """MEASURED drive 2, gate `portals` guidepost_bind: 4 boards nearer
    another arch.  The solver now refuses a side that would be; u-lheast has
    no side that is not, so its pair is refused rather than misnamed."""
    wrong, none = _ring_boards(("neighbour_arches",))
    return [] if not wrong and none == {"u-lheast"} else [
        f"wrong door {sorted(wrong)}, no board {sorted(none)}"]


def _case_NEUTER_drop_neighbour_arches_the_4_RETURN() -> list[str]:
    wrong, _none = _ring_boards(())
    want = {"u-wttown", "u-lheast", "u-stenvik", "u-castle"}
    return [] if wrong == want else [f"shipped solver: {sorted(wrong)}"]


def _pair_rows(records: list[dict]) -> int:
    import stock  # noqa: PLC0415
    own = {"prefab": "portal_wood", "tag": "u-stathub", "x": 0, "y": 0, "z": 0}
    far = dict(own, site_id="sandbox-portal-hub")
    spawn = [("station", own), ("pair_hub_end", far),
             ("chest", {"prefab": "piece_chest"})]
    why = stock.pair_verdict(records, set(), "sandbox-portal-hub", own, far)
    got = stock.pair_spawn_list(spawn, {"tag": "u-stathub"}, why)
    return sum(1 for _r, p in got if p["prefab"] == "portal_wood")


def _case_a_spoke_pair_lands_whole_or_not_at_all() -> list[str]:
    """MEASURED drive 2: 9 installation tags one-ended -- the hub body never
    stood and nothing ever placed a hub end."""
    hub = [{"seq": 1, "op": "spawn_plan",
            "params": {"site_id": "sandbox-portal-hub"}}]
    bad = []
    if _pair_rows([]) != 0:
        bad.append("hub absent: a spoke end was still spawned")
    if _pair_rows(hub) != 2:
        bad.append("hub standing: the pair did not land whole")
    return bad


def _case_NEUTER_spoke_alone_the_one_ended_tag_RETURNS() -> list[str]:
    import stock  # noqa: PLC0415
    stock.PAIR_MODE = "spoke_alone"
    try:
        n = _pair_rows([])
    finally:
        stock.PAIR_MODE = "both_or_neither"
    return [] if n == 1 else [f"shipped rule spawned {n} end(s), not 1"]


# THE HALL'S ARCHES AND BOARDS STAND ON THE HALL'S PAD.  MEASURED on Fresh42's
# hall, offline: 13 of 16 settlement arches and 27 of 32 board pieces off its
# 34.8 m pad, u-lhwest's 3.29 m arch under 3.46 m of hillside.  Driven here
# through `pack_settlement_arches` -- `stock.hub_arch` and
# `waypoints.portal_guidepost`, the build's own -- on the hall's centre, yaw
# and pad height with a one-slab body built in memory, on a pad sized for
# the body alone (12 m) inside the hall's 50 m site square.
_HALL_SCENE = {"cx": -220.5, "cz": -411.5, "base_y": 33.89, "yaw": 135.0,
               "pad": (12.0, 12.0), "pad_half": 25.0}
_HALL_TAGS = ["u-stenvik", "u-vestvik", "u-castle", "u-sudrberg",
              "u-wtspawn", "u-wtsouth"]


def _hall_packing() -> tuple[dict, dict]:
    import stock  # noqa: PLC0415
    import fixtures  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415
    h = _HALL_SCENE
    slab = SimpleNamespace(prefab="stone_floor_2x2", pos=(0.0, 0.0, 0.0),
                           rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))
    body = fixtures.place_body([slab], at=(h["cx"], h["base_y"], h["cz"]))
    si = {"place": {"id": "selftest-hall"}, **h}
    seat = stock.site_seat(si)
    plan = stock.solve_placement({"id": "selftest-hall"}, {"stations": []},
                                 0, h["cx"], h["cz"], h["base_y"], h["yaw"],
                                 h["pad_half"], props=[], body=body, seat=seat)
    packing = stock.packing_of(si, "selftest", body, plan, seat)
    return packing, pack_settlement_arches(packing, _HALL_TAGS)


def _hall_off_pad(packed: dict) -> tuple[list[str], int]:
    """(arches and board pieces standing off the scene's pad, how many
    stand at all), judged by the writer's own stamp."""
    import stock  # noqa: PLC0415
    h = _HALL_SCENE
    judge = stock.PadSeat(h["cx"], h["cz"], *h["pad"], h["base_y"])
    off, n = [], 0
    for tag in _HALL_TAGS:
        got = packed[tag]
        pieces = ([got["arch"]] if got["arch"] else []) + [
            {"prefab": pf, "x": x, "y": y, "z": z, "yaw": yw}
            for pf, x, y, z, yw in got["board"] or ()]
        for p in pieces:
            n += 1
            if judge(stock.foot_of(dict(p, prefab=p.get("prefab",
                                                        PORTAL_PREFAB)))) is None:
                off.append(f"{tag} {p.get('prefab', PORTAL_PREFAB)}")
    return off, n


def _case_every_hall_arch_and_board_stands_on_the_halls_pad() -> list[str]:
    _packing, packed = _hall_packing()
    off, n = _hall_off_pad(packed)
    out = [f"off the pad: {o}" for o in off]
    if not n:
        out.append("no arch stood at all: the case measures nothing")
    for tag in _HALL_TAGS:
        got = packed[tag]
        if not got["arch"] and "unseated" not in got and "tried" not in got \
                and "walkin" not in got \
                and "arch spot(s) tried" not in (got["why"] or ""):
            out.append(f"{tag} refused without a number: {got['why']}")
    return out


def _case_NEUTER_site_square_hall_arches_stand_OFF_the_pad() -> list[str]:
    """LOSS ARM: the shipped rule stands arches and boards anywhere in the
    site square at the pad height -- off the 12 m pad."""
    import stock  # noqa: PLC0415
    stock.SEAT_MODE = "site_square"
    try:
        _packing, packed = _hall_packing()
    finally:
        stock.SEAT_MODE = "written_pad"
    off, _n = _hall_off_pad(packed)
    return [] if off else ["the shipped rule stood every arch on the pad"]


# THE DRIVE-3 HALL RING, verbatim from ledger/runs/Fresh42 -- every portal
# record in the hall and each settlement arch's guidepost post and board
# (seq, prefab, tag, x, y, z, yaw) -- in the order the packing DECIDED it:
# `stock.hub_ends`' installation arches first, then each settlement arch
# with its board (`pack_settlement_arches`).  MEASURED, drive-3 gate
# `portals` approach_ray on save 05eb7c6c05de: u-sudrberg +Z stopped at
# 3.75 m by a board, -Z at 3.75 m by an arch; u-wtspawn +Z at 4.25 m and
# -Z at 5.25 m, both by arches -- 10 of the hall's 25 arches, every blocker
# ours.  The scene it is re-seated on is the hall's own centre, yaw, datum
# and ring pad (placements.yaml `sandbox-portal-hub`), one slab for a body.
_D3_RING = [
    (2428, "portal_wood", "u-stathub", -225.5, 35.017, -404.5, 270.0),
    (2503, "portal_wood", "u-meadhall", -229.5, 35.017, -405.5, 90.0),
    (2512, "portal_wood", "u-blacbase", -227.5, 35.017, -404.5, 90.0),
    (2526, "portal_wood", "u-dock", -211.87, 34.42, -398.392, 135.0),
    (2550, "portal_wood", "u-ashland", -227.87, 34.42, -425.392, 135.0),
    (2566, "portal_wood", "u-dnshrine", -206.87, 34.42, -401.392, 135.0),
    (2597, "portal_wood", "u-dncamp", -203.87, 34.42, -414.392, 135.0),
    (2617, "portal_wood", "u-mtnpost", -232.87, 34.42, -422.392, 135.0),
    (2649, "portal_wood", "u-mistfrge", -217.87, 34.42, -394.392, 135.0),
    (2671, "portal_wood", "u-plainsfm", -235.87, 34.42, -402.392, 135.0),
    (2112, "portal_wood", "u-stenvik", -200.87, 34.42, -408.392, 135.0),
    (2115, "wood_pole_log", "u-stenvik", -198.713, 35.27, -406.235, 135.0),
    (2116, "sign", "u-stenvik", -197.975, 35.87, -406.974, 135.0),
    (2206, "portal_wood", "u-vestvik", -212.87, 34.42, -393.392, 135.0),
    (2209, "wood_pole_log", "u-vestvik", -210.713, 35.27, -391.235, 135.0),
    (2210, "sign", "u-vestvik", -209.975, 35.87, -391.974, 135.0),
    (2218, "portal_wood", "u-castle", -237.87, 34.42, -419.392, 135.0),
    (2221, "wood_pole_log", "u-castle", -235.713, 35.27, -417.235, 135.0),
    (2222, "sign", "u-castle", -234.975, 35.87, -417.974, 135.0),
    (2230, "portal_wood", "u-sudrberg", -201.87, 34.42, -403.392, 135.0),
    (2233, "wood_pole_log", "u-sudrberg", -199.713, 35.27, -401.235, 135.0),
    (2234, "sign", "u-sudrberg", -198.975, 35.87, -401.974, 135.0),
    (2242, "portal_wood", "u-wtspawn", -206.87, 34.42, -396.392, 135.0),
    (2245, "wood_pole_log", "u-wtspawn", -204.713, 35.27, -394.235, 135.0),
    (2246, "sign", "u-wtspawn", -203.975, 35.87, -394.974, 135.0),
    (2254, "portal_wood", "u-wtsouth", -222.87, 34.42, -391.392, 135.0),
    (2257, "wood_pole_log", "u-wtsouth", -220.713, 35.27, -389.235, 135.0),
    (2258, "sign", "u-wtsouth", -219.975, 35.87, -389.974, 135.0),
    (2266, "portal_wood", "u-wttown", -232.87, 34.42, -427.392, 135.0),
    (2269, "wood_pole_log", "u-wttown", -235.027, 35.27, -429.549, 135.0),
    (2270, "sign", "u-wttown", -234.288, 35.87, -430.287, 135.0),
    (2278, "portal_wood", "u-wtpeak", -240.87, 34.42, -412.392, 135.0),
    (2281, "wood_pole_log", "u-wtpeak", -243.027, 35.27, -414.549, 135.0),
    (2282, "sign", "u-wtpeak", -242.288, 35.87, -415.287, 135.0),
    (2291, "portal_wood", "u-wtnorth", -235.87, 34.42, -397.392, 135.0),
    (2294, "wood_pole_log", "u-wtnorth", -232.193, 35.27, -393.715, 135.0),
    (2295, "sign", "u-wtnorth", -231.454, 35.87, -394.454, 135.0),
    (2309, "portal_wood", "u-lhsouth", -201.87, 34.42, -398.392, 135.0),
    (2312, "wood_pole_log", "u-lhsouth", -199.713, 35.27, -396.235, 135.0),
    (2313, "sign", "u-lhsouth", -198.975, 35.87, -396.974, 135.0),
    (2321, "portal_wood", "u-lheast", -240.87, 34.42, -402.392, 135.0),
    (2324, "wood_pole_log", "u-lheast", -243.027, 35.27, -404.549, 135.0),
    (2325, "sign", "u-lheast", -242.288, 35.87, -405.287, 135.0),
    (2334, "portal_wood", "u-lhwest", -211.87, 34.42, -388.392, 135.0),
    (2337, "wood_pole_log", "u-lhwest", -209.713, 35.27, -386.235, 135.0),
    (2338, "sign", "u-lhwest", -208.975, 35.87, -386.974, 135.0),
    (2360, "portal_wood", "u-treesth", -221.87, 34.42, -386.392, 135.0),
    (2363, "wood_pole_log", "u-treesth", -219.713, 35.27, -384.235, 135.0),
    (2364, "sign", "u-treesth", -218.975, 35.87, -384.974, 135.0),
    (2387, "portal_wood", "u-treewest", -227.87, 34.42, -387.392, 135.0),
    (2390, "wood_pole_log", "u-treewest", -230.027, 35.27, -389.549, 135.0),
    (2391, "sign", "u-treewest", -229.288, 35.87, -390.287, 135.0),
    (2414, "portal_wood", "u-treenear", -242.87, 34.42, -423.392, 135.0),
    (2417, "wood_pole_log", "u-treenear", -245.027, 35.27, -425.549, 135.0),
    (2418, "sign", "u-treenear", -244.288, 35.87, -426.287, 135.0),
]
# `hub_ends`' own arches: step 6 built them after step 5's settlement arches
_D3_HUB_ENDS_FROM = 2428
_D3_HALL = {"cx": -220.5, "cz": -411.5, "base_y": 34.42, "yaw": 135.0,
            "pad": (56.8, 56.8), "pad_half": 25.0}


def _d3_gate(rows) -> dict[str, list[str]]:
    """`{tag: blockers}` for every arch in `rows` the GATE itself --
    `accept/sections/portals.py::check_approach`, over its own solids and
    the Player capsule the corpus measures -- finds with no walk-in."""
    import fixtures  # noqa: PLC0415
    from accept import instruments as I  # noqa: PLC0415
    from accept.sections import portals as GP  # noqa: PLC0415
    geom, build = I.load_geometry()
    ss = [I.Solid(pf, (x, y, z), I.yaw(yw), geom.get(pf) or [], pf in geom)
          for _s, pf, _t, x, y, z, yw in rows]
    tags = {(round(x, 2), round(z, 2)): t
            for _s, pf, t, x, _y, z, _yw in rows if pf == GP.PORTAL_PREFAB}
    r, hgt = fixtures.walker_capsule()
    fs = GP.check_approach([s for s in ss if s.name == GP.PORTAL_PREFAB],
                           I.Grid(ss), r, hgt, tags, build)
    return {f.numbers["tag"]: f.numbers["blockers"] for f in fs}


def _d3_reseat() -> tuple[list[tuple], dict[str, str]]:
    """The drive-3 ring through the solver's walk-in book, decision by
    decision (an arch with its board is ONE decision, as the packing makes
    it): what the book admits stands where drive 3 put it, what it refuses
    is searched again by the solver's own arch search -- `stock.hub_arch`
    for the hall's installation arches, `pack_settlement_arches` for the
    rest -- on `_D3_HALL`.  Returns (rows standing, {tag: why} refused).
    Under WALKIN_MODE "none" there is no book: the ring stands as built."""
    import itertools  # noqa: PLC0415
    import fixtures  # noqa: PLC0415
    import stock  # noqa: PLC0415
    book = stock.book_for(None)
    stood, again = [], []
    for tag, grp in itertools.groupby(_D3_RING, key=lambda r: r[2]):
        grp = list(grp)
        rows = [(r[1], *r[3:]) for r in grp]
        if book is None or book.admit(rows):
            if book is not None:
                book.commit(rows)
            stood += grp
        else:
            again.append((tag, grp[0][0]))
    if not again:
        return stood, {}
    h = _D3_HALL
    slab = SimpleNamespace(prefab="stone_floor_2x2", pos=(0.0, 0.0, 0.0),
                           rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))
    body = fixtures.place_body([slab], at=(h["cx"], h["base_y"], h["cz"]))
    seat = stock.site_seat({"place": {"id": "d3-hall"}, **h})
    index, mask = body.index(), body.interior()
    taken = [fixtures.fixture_box(r[1], *r[3:]) for r in stood]
    refused: dict[str, str] = {}
    for tag, seq in again:
        if seq < _D3_HUB_ENDS_FROM:
            continue
        arch, unseated = stock.hub_arch(
            body, index, mask, h["cx"], h["cz"], h["base_y"], h["pad_half"],
            h["yaw"], taken, None, seat=seat, walkins=book)
        if arch is None:
            walk = book.refusal()
            refused[tag] = (stock.unseated_reason(unseated) if unseated
                            else stock.walkin_reason(walk) if walk
                            else "no legal arch spot left")
            continue
        book.commit([stock.row_of(arch, PORTAL_PREFAB)])
        taken.append(tuple(arch["box"]))
        stood.append((0, PORTAL_PREFAB, tag, arch["x"], arch["y"], arch["z"],
                      arch["yaw"]))
    sett = [t for t, seq in again if seq < _D3_HUB_ENDS_FROM]
    packing = {"hub": {"x": h["cx"], "z": h["cz"], "pad_y": h["base_y"],
                       "yaw": h["yaw"], "pad_half": h["pad_half"],
                       "pad": h["pad"]},
               "ends": {r[2]: {"x": r[3], "y": r[4], "z": r[5], "yaw": r[6]}
                        for r in stood if r[1] == PORTAL_PREFAB},
               "boxes": taken, "body": body, "index": index, "mask": mask,
               "seat": seat, "indoor_centre": None, "walkins": book}
    packed = pack_settlement_arches(packing, sett)
    for tag in sett:
        got = packed[tag]
        if not got["arch"]:
            refused[tag] = got["why"]
            continue
        a = got["arch"]
        stood.append((0, PORTAL_PREFAB, tag, a["x"], a["y"], a["z"], a["yaw"]))
        stood += [(0, pf, tag, x, y, z, yw) for pf, x, y, z, yw in got["board"]]
    return stood, refused


def _case_the_book_reads_the_drive3_ring_as_the_gate_does() -> list[str]:
    """KNOWN-BAD, AS A VERDICT: drive 3's ring as built leaves u-sudrberg
    and u-wtspawn with no walk-in, and the solver's walk-in book refuses
    the SAME arches the gate's `check_approach` does, arch for arch -- one
    corridor rule, not two."""
    import stock  # noqa: PLC0415
    book = stock.Walkins(None)
    for r in _D3_RING:
        book.commit([(r[1], *r[3:])])
    tag = {(round(r[3], 2), round(r[5], 2)): r[2]
           for r in _D3_RING if r[1] == PORTAL_PREFAB}
    solver = {tag[(round(x, 2), round(z, 2))] for x, z in book.blocked()}
    gate = set(_d3_gate(_D3_RING))
    out = []
    if solver != gate:
        out.append(f"book refuses {sorted(solver)}, gate {sorted(gate)}")
    if not {"u-sudrberg", "u-wtspawn"} <= gate:
        out.append(f"the gate no longer refuses the drive-3 pair: "
                   f"{sorted(gate)}")
    return out


def _case_the_drive3_ring_reseated_keeps_every_walk_in() -> list[str]:
    """SURVIVOR (monotone): re-seated through the book, all 25 drive-3
    arches stand and the gate finds a walk-in at every one of them."""
    stood, refused = _d3_reseat()
    out = [f"{t}: no walk-in, blocked by {b}"
           for t, b in _d3_gate(stood).items()]
    standing = {r[2] for r in stood if r[1] == PORTAL_PREFAB}
    out += [f"{t} refused: {why}" for t, why in refused.items()]
    lost = {r[2] for r in _D3_RING} - standing - set(refused)
    if lost:
        out.append(f"lost with no refusal: {sorted(lost)}")
    return out


def _case_NEUTER_no_walkins_u_sudrberg_u_wtspawn_BLOCKED_again(
        ) -> list[str]:
    """LOSS ARM: drop `reserved_walkins` and the ring stands as drive 3
    built it -- the gate refuses u-sudrberg and u-wtspawn, blocked by our
    own arches and boards."""
    import stock  # noqa: PLC0415
    stock.WALKIN_MODE = "none"
    try:
        stood, _refused = _d3_reseat()
    finally:
        stock.WALKIN_MODE = "reserved"
    rows = _d3_gate(stood)
    out = []
    for t in ("u-sudrberg", "u-wtspawn"):
        if t not in rows:
            out.append(f"the shipped packing left {t} a walk-in")
        elif not set(rows[t]) <= {PORTAL_PREFAB, "sign", "wood_pole_log"}:
            out.append(f"{t} blocked by {rows[t]}, not by our own pieces")
    return out


# DRIVE 4's u-wtnorth, verbatim.  The unit as `unit_records` gave it, and
# every seed object on the frozen save af96bfb62427 whose own solid, as the
# gate books it, reaches the site end's `site_reach` and stands in the
# walker's band at the pad -- `(prefab, x, y, z, qx, qy, qz, qw)`.  Drive 4
# stood the arch at (149.9, 80.27, 238.2) yaw 0, and the gate refused it:
# +Z by this Birch1, -Z by the tower's own stone_pillar.
_D4_WTNORTH = {"id": "wt-north", "body": "outposttower.blueprint",
               "x": 149.9, "z": 233.2, "yaw": 0, "pad_w": 20.0,
               "pad_d": 20.0, "target_y": 80.27}
_D4_WTNORTH_ARCH = (149.9, 80.27, 238.2, 0.0)
_D4_WTNORTH_SEED = (
    ('Beech1', 129.3313, 80.1496, 239.3344, -0.008235, 0.857107, 0.005233, 0.515046),
    ('Beech1', 130.8336, 79.7751, 242.6366, 0.010991, -0.078767, -0.027121, -0.996463),
    ('Beech1', 135.3895, 80.5649, 253.6542, 0.009750, -0.909902, 0.000351, 0.414708),
    ('Beech1', 136.5439, 80.7050, 243.9120, 0.012153, 0.819046, -0.002143, 0.573595),
    ('Beech1', 140.7878, 71.1680, 206.9245, 0.002248, -0.342098, 0.031679, -0.939127),
    ('Beech1', 142.3218, 80.5187, 262.6616, 0.001080, -0.522630, 0.035149, -0.851834),
    ('Beech1', 143.6867, 80.2650, 271.4103, 0.164758, 0.204326, 0.081700, -0.961473),
    ('Beech1', 153.2134, 77.7191, 217.3375, 0.027849, 0.826053, -0.002147, 0.562900),
    ('Beech1', 161.2238, 79.4137, 220.9037, 0.083836, -0.546214, 0.203819, -0.808134),
    ('Beech1', 165.9816, 78.2761, 217.3295, 0.032223, -0.283986, -0.014093, -0.958183),
    ('Beech1', 167.2632, 78.4421, 216.0442, -0.011884, 0.587910, -0.035586, -0.808056),
    ('Beech1', 167.7360, 78.4619, 244.0039, 0.019709, 0.622206, 0.017771, 0.782404),
    ('Beech1', 168.4640, 78.3798, 222.0941, 0.025402, 0.828610, 0.001348, 0.559249),
    ('Beech1', 168.7944, 77.7383, 250.0752, 0.029815, -0.984029, 0.031822, -0.172583),
    ('Beech1', 172.3954, 77.8221, 250.4244, -0.001741, -0.913098, -0.038972, -0.405871),
    ('Beech1', 173.9327, 77.9185, 249.8860, 0.015019, -0.776978, 0.004681, 0.629331),
    ('Beech1', 177.5161, 78.0934, 246.3583, 0.008989, 0.514960, -0.014960, -0.857037),
    ('Beech_small2', 133.8178, 80.8178, 239.4117, -0.050649, 0.807299, -0.004473, 0.587948),
    ('Birch1', 159.7963, 78.0667, 246.8937, -0.007673, 0.731281, 0.042926, -0.680681),
    ('Birch2', 158.4611, 78.5904, 250.0690, 0.004930, 0.998951, -0.012887, 0.043672),
    ('Bush01', 137.1219, 80.8809, 245.1487, 0.000000, 0.913546, 0.000000, 0.406737),
    ('Pickable_Branch', 136.9199, 81.2976, 250.4093, -0.040533, 0.194281, -0.087678, -0.976179),
)


def _unity_euler(qx: float, qy: float, qz: float, qw: float) -> tuple:
    """`Quaternion.eulerAngles` (z, then x, then y), in degrees: the
    rotation `findObjects -detailed` prints and `euler_quat` reads back."""
    sx = max(-1.0, min(1.0, 2 * (qw * qx - qy * qz)))
    return (math.degrees(math.asin(sx)),
            math.degrees(math.atan2(2 * (qw * qy + qx * qz),
                                    1 - 2 * (qx * qx + qy * qy))),
            math.degrees(math.atan2(2 * (qw * qz + qx * qy),
                                    1 - 2 * (qx * qx + qz * qz))))


class _SeedWorld:
    """A world's natural objects served as the live server serves them to
    `seed_census`: `objects_count` (a vertical cylinder, y-blind, `id=*`
    refused past 40 m) and `findObjects -near ... -detailed` (a CUBE in all
    three axes, positions to the centimetre), from rows `(prefab, x, y, z,
    qx, qy, qz, qw)`.  `terraform/stock.py`'s selftest reads its seed through
    this too."""

    def __init__(self, rows):
        self.rows = list(rows)

    def count(self, ident, x, z, r, ignore="_*"):
        if ident == "*" and r > _STAR_COUNT_MAX_M:
            raise AssertionError(f"`objects_count id=*` over {r:g} m")
        hit = [o for o in self.rows if ident in ("*", o[0])
               and math.hypot(o[1] - x, o[3] - z) <= r]
        per: dict = {}
        for o in hit:
            per[o[0]] = per.get(o[0], 0) + 1
        return len(hit), per

    def command(self, cmd):
        m = re.fullmatch(r"findObjects -prefab (\S+) -near (\S+) (\S+) (\S+) "
                         r"(\S+) -detailed", cmd)
        if not m:
            raise AssertionError(f"the seed world was asked {cmd!r}")
        prefab = m.group(1)
        x, y, z, r = (float(v) for v in m.groups()[1:])
        return "\n".join(
            f"-Prefab: {o[0]} Id: {i}:{i} Position: ({o[1]:.2f} {o[2]:.2f} "
            f"{o[3]:.2f}) Creator: 0 Rotation: "
            "(%.2f %.2f %.2f)" % _unity_euler(*o[4:8])
            for i, o in enumerate(self.rows)
            if o[0] == prefab and abs(o[1] - x) <= r and abs(o[2] - y) <= r
            and abs(o[3] - z) <= r)


def _d4_wtnorth_site_end(read=None) -> tuple[dict, list, list]:
    """u-wtnorth's site end and its board as `apply_portals` solves them
    over the seed -- `_D4_WTNORTH_SEED` verbatim, or `read`, what `site_seed`
    read of that world -- and the gate's rows for that arch over the tower,
    the REAL seed, the arch and the board: `(spot, board, rows)`."""
    import fixtures  # noqa: PLC0415
    import stock  # noqa: PLC0415
    from accept import instruments as I  # noqa: PLC0415
    from accept.sections import portals as GP  # noqa: PLC0415
    W = _waypoints()
    u = _D4_WTNORTH
    seed = [stock.gate_solid(r[0], *r[1:4], r[4:8]) for r in _D4_WTNORTH_SEED]
    spot, book = site_portal_spot(None, "wt-north", [u],
                                  seed if read is None else read)
    board = W.portal_guidepost(spot["x"], spot["y"], spot["z"], spot["yaw"],
                               W.wrap_for_board("Wt North u-wtnorth"),
                               side="left", seat=unit_seat(u), walkins=book)
    _fx, _o, body = body_of(u, (u["x"], u["target_y"], u["z"]), u["yaw"])
    arch = stock.gate_solid(PORTAL_PREFAB, spot["x"], spot["y"], spot["z"],
                            I.yaw(spot["yaw"]))
    ss = (stock.gate_solids(body) + seed + [arch]
          + [stock.gate_solid(p.prefab, p.x, p.y, p.z, I.yaw(p.yaw))
             for p in board])
    r, h = fixtures.walker_capsule()
    rows = GP.check_approach([arch], I.Grid(ss), r, h,
                             {(round(spot["x"], 2), round(spot["z"], 2)):
                              "u-wtnorth"}, I.load_geometry()[1])
    return spot, board, rows


def _case_the_u_wtnorth_site_end_keeps_its_walk_in() -> list[str]:
    """SURVIVOR: drive 4's u-wtnorth, re-solved through the site end's book
    with the seed read, stands with a face the gate walks the walker into,
    its board beside it and on the tower's pad."""
    spot, board, rows = _d4_wtnorth_site_end()
    out = [f"gate refuses the re-solved arch: {f.message}" for f in rows]
    seat = unit_seat(_D4_WTNORTH)
    if seat is not None:
        import stock  # noqa: PLC0415
        for p in [dict(spot, prefab=PORTAL_PREFAB)] + [
                {"prefab": q.prefab, "x": q.x, "y": q.y, "z": q.z,
                 "yaw": q.yaw} for q in board]:
            if seat(stock.foot_of(p)) is None:
                out.append(f"{p['prefab']} at ({p['x']}, {p['z']}) stands off "
                           f"the tower's pad")
    return out


def _case_NEUTER_seed_unread_u_wtnorth_BLOCKED_as_drive4_stood_it(
        ) -> list[str]:
    """LOSS ARM: drop `seed_objects` and the book holds the tower alone --
    u-wtnorth stands where drive 4 stood it and the gate refuses it, +Z by
    the Birch1 past the clearing and -Z by the tower's own stone."""
    global SITE_SEED_MODE  # noqa: PLW0603
    SITE_SEED_MODE = "none"
    try:
        spot, _board, rows = _d4_wtnorth_site_end()
    finally:
        SITE_SEED_MODE = "read"
    out = []
    at = (spot["x"], spot["y"], spot["z"], float(spot["yaw"]))
    if at != _D4_WTNORTH_ARCH:
        out.append(f"the neutered solve stood u-wtnorth at {at}, not drive "
                   f"4's {_D4_WTNORTH_ARCH}")
    if not rows:
        out.append("the gate found a walk-in at the neutered u-wtnorth")
    elif not ("Birch1" in rows[0].numbers["blockers"]
              and rows[0].numbers["build_piece_blocker"]):
        out.append(f"the neutered u-wtnorth is blocked by "
                   f"{rows[0].numbers['blockers']}, not by the Birch1 and "
                   f"our own stone")
    return out


def _d4_wtnorth_world() -> "_SeedWorld":
    """Drive 4's u-wtnorth world as the live server serves it: its seed
    objects and one Beech1 70 m east that is nobody's walk-in."""
    return _SeedWorld(list(_D4_WTNORTH_SEED)
                      + [("Beech1", 219.9, 80.0, 233.2, 0.0, 0.0, 0.0, 1.0)])


def _case_the_u_wtnorth_seed_read_off_the_world_keeps_its_walk_in(
        ) -> list[str]:
    """SURVIVOR: `site_seed` reads drive 4's world and returns exactly the
    seed the frozen rows hold -- not the Beech1 70 m off -- whole, and the
    site end solved over what it read keeps its walk-in."""
    import collections
    seed, seen = site_seed(_d4_wtnorth_world(), _D4_WTNORTH)
    want = dict(collections.Counter(r[0] for r in _D4_WTNORTH_SEED))
    out = []
    if seen["within_reach"] != want or seen["unlisted"]:
        out.append(f"read {seen['within_reach']} (unlisted "
                   f"{seen['unlisted']}), not the frozen {want}")
    _spot, _board, rows = _d4_wtnorth_site_end(seed)
    out += [f"gate refuses the arch solved over the read seed: {f.message}"
            for f in rows]
    return out


def _case_NEUTER_a_zero_reach_reads_no_seed_u_wtnorth_BLOCKED() -> list[str]:
    """LOSS ARM: the reach is what puts the trees in the book.  With none the
    same world reads as empty and u-wtnorth stands where drive 4 stood it,
    refused +Z by the Birch1 and -Z by the tower's own stone."""
    import stock  # noqa: PLC0415
    real = stock.walk_reach
    stock.walk_reach = lambda _half_diagonal: 0.0
    try:
        seed, seen = site_seed(_d4_wtnorth_world(), _D4_WTNORTH)
    finally:
        stock.walk_reach = real
    spot, _board, rows = _d4_wtnorth_site_end(seed)
    out = []
    if seed:
        out.append(f"a zero reach still read {seen['within_reach']}")
    at = (spot["x"], spot["y"], spot["z"], float(spot["yaw"]))
    if at != _D4_WTNORTH_ARCH:
        out.append(f"the unread solve stood u-wtnorth at {at}, not drive "
                   f"4's {_D4_WTNORTH_ARCH}")
    if not rows:
        out.append("the gate found a walk-in at the unread u-wtnorth")
    return out



_PORTAL_CASES = (
    ("a_body_carrying_portals_emits_none",
     _case_a_body_carrying_portals_emits_none),
    ("NEUTER_emit_carried_portals_the_15_RETURN",
     _case_NEUTER_emit_carried_portals_the_15_RETURN),
    ("a_Pirate68_dump_is_REFUSED_for_a_Fresh42_run",
     _case_a_Pirate68_dump_is_REFUSED_for_a_Fresh42_run),
    ("NEUTER_drop_dump_declared_seed_the_foreign_dump_is_ACCEPTED",
     _case_NEUTER_drop_dump_declared_seed_the_foreign_dump_is_ACCEPTED),
    ("the_Fresh42_plan_names_its_own_dump",
     _case_the_Fresh42_plan_names_its_own_dump),
    ("an_anchor_is_THIS_worlds_ledger_body",
     _case_an_anchor_is_THIS_worlds_ledger_body),
    ("the_hub_is_THIS_worlds_and_NEUTER_reads_Ulfsland",
     _case_the_hub_is_THIS_worlds_and_NEUTER_reads_Ulfsland),
    ("every_ring_board_names_its_own_door",
     _case_every_ring_board_names_its_own_door),
    ("NEUTER_drop_neighbour_arches_the_4_RETURN",
     _case_NEUTER_drop_neighbour_arches_the_4_RETURN),
    ("a_spoke_pair_lands_whole_or_not_at_all",
     _case_a_spoke_pair_lands_whole_or_not_at_all),
    ("NEUTER_spoke_alone_the_one_ended_tag_RETURNS",
     _case_NEUTER_spoke_alone_the_one_ended_tag_RETURNS),
    ("every_hall_arch_and_board_stands_on_the_halls_pad",
     _case_every_hall_arch_and_board_stands_on_the_halls_pad),
    ("NEUTER_site_square_hall_arches_stand_OFF_the_pad",
     _case_NEUTER_site_square_hall_arches_stand_OFF_the_pad),
    ("the_book_reads_the_drive3_ring_as_the_gate_does",
     _case_the_book_reads_the_drive3_ring_as_the_gate_does),
    ("the_drive3_ring_reseated_keeps_every_walk_in",
     _case_the_drive3_ring_reseated_keeps_every_walk_in),
    ("NEUTER_no_walkins_u_sudrberg_u_wtspawn_BLOCKED_again",
     _case_NEUTER_no_walkins_u_sudrberg_u_wtspawn_BLOCKED_again),
    ("the_u_wtnorth_site_end_keeps_its_walk_in",
     _case_the_u_wtnorth_site_end_keeps_its_walk_in),
    ("NEUTER_seed_unread_u_wtnorth_BLOCKED_as_drive4_stood_it",
     _case_NEUTER_seed_unread_u_wtnorth_BLOCKED_as_drive4_stood_it),
    ("the_u_wtnorth_seed_read_off_the_world_keeps_its_walk_in",
     _case_the_u_wtnorth_seed_read_off_the_world_keeps_its_walk_in),
    ("NEUTER_a_zero_reach_reads_no_seed_u_wtnorth_BLOCKED",
     _case_NEUTER_a_zero_reach_reads_no_seed_u_wtnorth_BLOCKED),
)


def selftest() -> int:
    bad = 0
    for name, fn in _UNION_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  union_prior selftest {len(_UNION_CASES) - bad}/"
          f"{len(_UNION_CASES)}  {'PASS' if not bad else 'FAIL'}")
    rbad = 0
    for name, fn in _REGRADE_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        rbad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  regrade_gate selftest {len(_REGRADE_CASES) - rbad}/"
          f"{len(_REGRADE_CASES)}  {'PASS' if not rbad else 'FAIL'}")
    sbad = 0
    for name, fn in _SCOPE_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        sbad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  plan scope / pad clamp / ground freeze selftest "
          f"{len(_SCOPE_CASES) - sbad}/{len(_SCOPE_CASES)}  "
          f"{'PASS' if not sbad else 'FAIL'}")
    pbad = 0
    for name, fn in _PAD_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        pbad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  written pad selftest {len(_PAD_CASES) - pbad}/"
          f"{len(_PAD_CASES)}  {'PASS' if not pbad else 'FAIL'}")
    obad = 0
    for name, fn in _PORTAL_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        obad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  portals / world scope selftest {len(_PORTAL_CASES) - obad}/"
          f"{len(_PORTAL_CASES)}  {'PASS' if not obad else 'FAIL'}")
    kbad = 0
    for name, fn in _RADIUS_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        kbad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  sibling room selftest {len(_RADIUS_CASES) - kbad}/"
          f"{len(_RADIUS_CASES)}  {'PASS' if not kbad else 'FAIL'}")
    zbad = 0
    for name, fn in _ZONE_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        zbad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  zone repair selftest {len(_ZONE_CASES) - zbad}/"
          f"{len(_ZONE_CASES)}  {'PASS' if not zbad else 'FAIL'}")
    return 0 if not (bad or rbad or sbad or pbad or obad or kbad or zbad) \
        else 1



def zone_compiler(srv, zx: int, zz: int) -> int:
    """How many `_TerrainCompiler` ZDOs actually stand in this zone.

    Counted at the zone centre inside 31 m -- under half a zone, so a
    neighbour cannot leak in. Two means one of them is dead weight and the
    terrain on screen is not the terrain written; zero against a ledger claim
    means that claim was appended and never sent.
    """
    import tcdata
    cx, cz = tcdata.zone_centre(zx, zz)
    got, _ = srv.count("_TerrainCompiler", cx, cz, 31.0, ignore="")
    return got

def zone_list(cx: float, cz: float, radius_m: float) -> list[tuple[int, int]]:
    """The zones the clearing CYLINDER actually intersects.

    THE BOUNDING SQUARE IS THE WRONG SHAPE AND IT COSTS A BUILD. This used
    to sample the nine points of the bounding square, which names every zone
    the SQUARE touches -- but `clear.generate_max` reaches
    `radius + 45.25` (a zone's half-diagonal), which is exactly the set the
    DISC touches. So a zone clipped by a corner of the square but not by the
    disc was listed in the postcondition and never generated by the command,
    and `zone_ctrl want 4 got 3` aborted the run.

    MEASURED on vestvik-stonehouse-2 at (-918.40, 1765.50) r 10.51: the
    square's corner (-928.91, 1755.00) lands in zone (-15, 27), whose centre
    is 56.00 m away against a `generate_max` of 55.77 -- so UpgradeWorld
    correctly generated three zones and the gate correctly refused four.
    The disc's own extent at that zone's z bound (1760.0) is
    x in [-927.36, -909.44], which never crosses the zone edge at -928.
    """
    import tcdata
    r = radius_m
    zx0, zz0 = tcdata.zone_of(cx - r, cz - r)
    zx1, zz1 = tcdata.zone_of(cx + r, cz + r)
    out: list[tuple[int, int]] = []
    for zx in range(zx0, zx1 + 1):
        for zz in range(zz0, zz1 + 1):
            zcx, zcz = tcdata.zone_centre(zx, zz)
            # Closest point of the zone's 64 m square to the disc centre.
            half = tcdata.ZONE_SIZE / 2.0
            nx = min(max(cx, zcx - half), zcx + half)
            nz = min(max(cz, zcz - half), zcz + half)
            if math.hypot(cx - nx, cz - nz) <= r:
                out.append((zx, zz))
    return out


def spawn_plan_file(unit: dict, pad_y: float, out_dir: Path) -> tuple[Path, dict]:
    """Run `to_rcon_plan.py --align floor-center` for one body."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    libmat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(libmat)
    body = libmat.locate_by_filename(unit["body"])
    if body is None:
        raise FileNotFoundError(f"no body resolves for {unit['body']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"{unit['id']}.plan"
    cmd = [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"), str(body),
           "--at", f"{unit['x']}", f"{pad_y}", f"{unit['z']}",
           "--rotate", f"{unit['yaw']}", "--align", "floor-center",
           "--out", str(plan_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not plan_path.exists():
        raise SystemExit(f"to_rcon_plan failed for {unit['id']}:\n"
                         f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    lines = [l for l in plan_path.read_text().splitlines() if l.strip()]
    prefabs: dict[str, int] = {}
    for l in lines:
        parts = l.split()
        if len(parts) >= 2 and parts[0] == "spawn_object":
            prefabs[parts[1]] = prefabs.get(parts[1], 0) + 1
    return plan_path, {"command_count": len(lines), "prefabs": prefabs,
                       "stdout": proc.stdout[-4000:]}


# ---------------------------------------------------------------------------
# THE OTHER HALF OF THE PAD-EDGE RULE: A BODY MUST READ THE ROAD.
#
# `roads/spec.py` states that a ribbon "terminates at the pad edge and does not
# pave inside it".  That rule has two ends and only one of them belongs to the
# router.  MEASURED from this ledger, over OUR OWN spawn_plan bodies against
# the paved width plus its 1 m shoulder, ordering decided by each road's FIRST
# `terrain_write` seq:
#
#   13 (body, road) pairs have our pieces standing inside a carriageway.
#    7 of them, over 5 distinct bodies, are ROAD_FIRST -- the road was already
#      written when the body was dropped on it: wt-spawn (158 of 212 pieces in
#      T10's band, 0.16 m from the centreline), wt-south (110 in T10, 84 in
#      S8), wt-town (160 in T9, 0.00 m), tree-near-1 (50 of 68 in W10),
#      tree-sth-1 (20 of 68 in S8).
#    6 are BODY_FIRST and are the ROUTER's half; `roads/precensus.py` is that
#      half.
#
# Nothing here read a road.  `apply_unit_ground` asked three questions -- siting,
# clearing, terrain -- all of them about generator LOCATIONS, none about a
# carriageway.  So the body went down on the centreline and the operator found
# a watchtower in the road.
#
# This gate is the missing question, and it reads the ROADS THAT EXIST: the
# ledger's `terrain_write` records with role `road_segment`, whose
# `params.profile` carries the written centreline, half width and shoulder.
# `upto_seq` exists so the gate can be evaluated at any point in the chain --
# which is what makes it testable in both directions on the history that
# produced the defect.
# ---------------------------------------------------------------------------
ROAD_SEGMENT_ROLE = "road_segment"


def built_roads(world: str | None = None,
                upto_seq: int | None = None) -> list[dict]:
    """Every road ON THE GROUND, from the ledger, with its own written band.

    One entry per road NAME carrying its FIRST write, because that is the seq
    at which its ground became a carriageway; later re-emits of the same road
    do not change the question "was there a road here when this body was
    placed".  Widths come from the record rather than from a constant: a trunk
    writes 3.0 m of paved half and a spur 2.0 m, and the shoulder is in the
    record too.
    """
    run = JUMPSTART / "ledger" / "runs" / str(world or WORLD) / "ledger.jsonl"
    if not run.exists():
        return []
    out: dict[str, dict] = {}
    for line in run.read_text().splitlines():
        if not line.strip() or '"terrain_write"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("op") != "terrain_write":
            continue
        p = rec.get("params") or {}
        if p.get("role") != ROAD_SEGMENT_ROLE:
            continue
        if upto_seq is not None and int(rec.get("seq") or 0) >= upto_seq:
            continue
        prof = p.get("profile") or {}
        nodes = prof.get("nodes") or []
        if len(nodes) < 2:
            continue
        name = str(p.get("name"))
        seq = int(rec.get("seq") or 0)
        if name in out and out[name]["seq"] <= seq:
            continue
        out[name] = {
            "name": name, "seq": seq, "ts": rec.get("ts"),
            "nodes": [(float(n[0]), float(n[1])) for n in nodes],
            "half_width_m": float(prof.get("half_width_m") or 3.0),
            "shoulder_m": float(prof.get("shoulder_m") or 1.0),
        }
    return sorted(out.values(), key=lambda r: r["seq"])


def _min_dist_to_polyline(nodes, pts) -> list[float]:
    """Perpendicular distance from each point to the polyline, per point.

    Segment-wise rather than node-wise: a 2 m station spacing over a bend
    leaves a node-only test blind by up to a metre, and a metre is the whole
    question at a 3 m paved half-width.
    """
    best = [float("inf")] * len(pts)
    for k in range(len(nodes) - 1):
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 < 1e-12:
            continue
        for i, (px, pz) in enumerate(pts):
            t = ((px - ax) * dx + (pz - az) * dz) / L2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            d = math.hypot(px - (ax + t * dx), pz - (az + t * dz))
            if d < best[i]:
                best[i] = d
    return best


def plan_piece_xz(plan_path: Path) -> list[tuple[float, float]]:
    """Every piece position in a `to_rcon_plan` file, as world (x, z).

    THE WIRE ORDER IS (z, x, y) AND IT IS NOT A TYPO: `spawn_object ... pos=`
    takes the coordinates in that order -- verified against this ledger's own
    pairs, where a record `pos: [-302.98, 33.887, -96.55]` is sent as
    `pos=-96.5456,-302.9791,33.8873`.  Reading it as (x, z) silently mirrors
    every body about the diagonal, which would make this gate answer about
    somewhere else entirely.
    """
    out = []
    for line in Path(plan_path).read_text().splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] != "spawn_object":
            continue
        for tok in parts[2:]:
            if not tok.startswith("pos="):
                continue
            try:
                pz, px, _py = (float(v) for v in tok[4:].split(",")[:3])
            except ValueError:
                break
            out.append((px, pz))
            break
    return out


def road_conflict(unit: dict, pieces_xz: list[tuple[float, float]] | None,
                  upto_seq: int | None = None) -> dict:
    """DOES THIS BODY STAND IN A ROAD THAT IS ALREADY WRITTEN?

    Measured against the body's OWN PIECES when a plan exists -- exact, and
    invariant under yaw, which a rectangle is not -- and against the pad
    lattice as well, which is the conservative direction because the pad
    contains the body.

    The pad-versus-road overlap is REPORTED and never refuses: shared ground
    between a pad and a road is the junction-height question, which the
    junction reconciler owns, and refusing on it here would block arrivals
    the design wants.  A PIECE inside the paved band is a different fact --
    the operator walks into it -- and that is what refuses.
    """
    roads = built_roads(upto_seq=upto_seq)
    pieces = pieces_xz or []
    pad = pad_lattice(unit)
    hits, pad_hits = [], []
    for r in roads:
        band = r["half_width_m"] + r["shoulder_m"]
        if pieces:
            d = _min_dist_to_polyline(r["nodes"], pieces)
            inside = [i for i, v in enumerate(d) if v <= band]
            if inside:
                hits.append({
                    "road": r["name"], "road_seq": r["seq"],
                    "road_written_utc": r["ts"],
                    "band_m": round(band, 2),
                    "pieces_in_band": len(inside),
                    "pieces_total": len(pieces),
                    "nearest_piece_m": round(min(d), 2),
                    "measured_on": "the body's own plan pieces",
                })
        dpad = _min_dist_to_polyline(r["nodes"], pad)
        if dpad and min(dpad) <= band:
            pad_hits.append({"road": r["name"], "road_seq": r["seq"],
                             "band_m": round(band, 2),
                             "pad_samples_in_band":
                                 sum(1 for v in dpad if v <= band),
                             "nearest_pad_sample_m": round(min(dpad), 2)})
    return {
        "verdict": "clear" if not hits else "in_a_road",
        "roads_read": len(roads),
        "roads_read_upto_seq": upto_seq,
        "pieces_measured": len(pieces),
        "piece_conflicts": hits,
        "pad_overlaps_reported": pad_hits,
        "method": ("every piece of this body against every road already in "
                   "the ledger, distance to the written centreline POLYLINE, "
                   "refused inside the record's own paved half-width plus its "
                   "recorded shoulder"),
        "tool": "tools/jumpstart/settlements/build.py::road_conflict",
    }


def location_check(loc: clearance.Locations, x: float, z: float,
                   half_diag_m: float) -> dict:
    v = loc.violations(x, z, half_diag_m)
    near = loc.nearest(x, z)
    return {
        "dump": locations_dump(),
        "method": ("MEASURED per location TYPE from LocScan.cs: "
                   "max(exteriorRadius, interiorRadius, znviewReachM) plus a 12 m "
                   "margin and a 90 m extra for a location the generator treats as "
                   "load bearing (prioritized or centerFirst or quantity <= 5). "
                   "Largest declared radius in the world is 32 m; largest measured "
                   "ZNetView reach is 35.77 m (TrollCave02). MOD-FREE dump: it "
                   "cannot see More_World_Locations, so a live marker check after "
                   "zones_generate is still required."),
        "tool": "tools/jumpstart/settlements/clearance.py",
        "nearest": {"name": near["name"], "dist_m": near["dist_m"],
                    "clearance_m": near["standoff_m"]},
        "standoff_m": near["standoff_m"],
        "verdict": "clear" if not v else "violates",
        "violations": v,
    }


# --- step 2: the terrain write -------------------------------------------
#
# `flatten.py` is driven as a LIBRARY and never as `flatten.py apply`, which
# sends its own `deleteObjects` + `spawn_object _TerrainCompiler` straight to
# RCON. An unlogged compiler write is invisible to the ledger's
# one-compiler-per-zone clobber guard and to `flatten: "FORBIDDEN"`, and those
# two guards exist for the single most destructive operation in this build.
# So: its zone set, its patchscan, its sample loop, its self-check, its two
# preconditions -- and OUR emit.

# RESOLVED, not literal: `--seed`/`--world` beat `$VH_SEED`/`$VH_WORLD` beat
# the Ulfsland defaults.  `main()` rebinds both before any patchscan runs.
SEED = worldseed.seed()
WORLD = worldseed.world()
# `place.py`'s measured budget. The binding direction is the REPLY,
# `Command '<joined>' executed.`, so the joined command is what is capped.
BATCH_BYTES = 3600
ROLE_BODY = {"town": "town_body", "castle": "castle", "watchtower": "watchtower",
             "lighthouse": "lighthouse", "treehouse": "treehouse"}
PORTAL_PREFAB = "portal_wood"
# HUB_SOURCE_DEPENDS_ON: "own_world" -- the hub is the one placement of THIS
# world that declares `portal.role: hub`.  "ulfsland" restores the shipped
# read of `worlds/Ulfsland/deepnorth-sandbox/placements.yaml#sandbox-portal-hub`
# for every world, and is set only by `selftest`'s neuter.
HUB_SOURCE = "own_world"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pad_lattice(unit: dict, step: float = 1.0) -> list[tuple[float, float]]:
    """The pad rectangle at 1 m: the samples a terrain write actually touches.

    Sample form rather than a disc, because `clearance.verdict_for_samples`
    compares the MINIMUM distance from written ground to each instance, and a
    26 m pad's 18.5 m half-diagonal would otherwise refuse ground the write
    never reaches.
    """
    hw, hd = unit["pad_w"] / 2 + APRON_M, unit["pad_d"] / 2 + APRON_M
    nx, nz = int(2 * hw / step), int(2 * hd / step)
    return [(unit["x"] - hw + i * step, unit["z"] - hd + j * step)
            for i in range(nx + 1) for j in range(nz + 1)]


def sample_verdict(loc: clearance.Locations, unit: dict, comps: dict | None = None,
                   gate: dict | None = None) -> dict:
    """`terrain_write.location_check`, in the canonical instrument's own words.

    `verdict_for_samples` already returns `method`, `tool` and the mod-free
    caveat IN THE RESULT, which is exactly what the schema demands, so the
    result is handed to the log unmodified apart from the dump path and the
    `clearance_m` alias the schema's reader expects.

    WITH `comps` this is the TERRAIN WRITE's own check and is asked under the
    non-destructive budget with the mandatory delta gate: the write moves
    ground and deletes nothing, so `MARGIN_M` and `PROTECTED_EXTRA_M` -- both
    taste, both calibrated for deletion -- are budgeting against a hazard this
    op does not have, and the hazard it DOES have (a location piece left
    floating because its ground moved) is measured directly by `delta_gate`.
    Main's ruling, and `clearance.verdict_for_samples` refuses the loosened
    budget outright if the delta gate is missing.

    WITHOUT `comps` it is the SITING question under the destructive budget,
    which is what the clearing cylinder's own gate (`clear_radius_room`) and
    the site solver answer against.
    """
    samples = pad_lattice(unit)
    if comps is None:
        v = loc.verdict_for_samples(samples)
    else:
        v = loc.verdict_for_samples(samples, destructive=False,
                                    delta_gate=gate or delta_gate(loc, comps,
                                                                  samples))
    near = dict(v.get("nearest") or {})
    near["clearance_m"] = near.get("standoff_m")
    v["nearest"] = near
    v["dump"] = locations_dump()
    return v


# BUILD_ROOM_DEPENDS_ON: the populations the build's PRE-GENERATION gate
# refuses by, a subset of `clearance.CLEARING_POPULATION_DEPENDS_ON`.
#
# `pieces` only, and the reason is the instrument that follows it: after
# `zones_generate`, `cylinder_contents` counts the EXACT objects in the
# cylinder, sky interiors included (MEASURED: it refused wt-south and wt-east
# on Crypt4's and Crypt3's doors at y 5061).  The `interior_zone` term is the
# whole 64 m zone -- an upper bound -- and MEASURED against Ulfsland's save
# (census bc484171e303) it would refuse six standing Pirate68 units
# (hognest-castle, tree-sth-1..3, tree-near-1, tree-near-3) whose cylinders
# hold no non-natural interior object.  So the build RECORDS the
# full-population room (`clear_radius_room_siting`) and lets the exact
# census decide; the SITING, which has no census to ask, refuses by the
# full population (`settlements/plan.py::CLEARING_GATE_DEPENDS_ON`) so it
# never places a pad where the census could refuse.
BUILD_ROOM_DEPENDS_ON = ("pieces",)


def clear_radius_room(loc: clearance.Locations, cx: float, cz: float,
                      depends_on: tuple = BUILD_ROOM_DEPENDS_ON) -> dict:
    """How wide an `objects_remove` cylinder about (cx, cz) may be.

    A DIFFERENT QUESTION from the pad stand-off, and the distinction is
    Crossings' finding rather than mine: the pad gate asks whether WRITTEN
    GROUND enters a location's stand-off, while this asks whether the CLEARING
    CYLINDER can reach a location's PIECES. `objects_remove id=* ignore=_*` is
    the only irreversible operation in this build, and MEASURED by Crossings on
    `dock-stationhub`, a Runestone_Meadows marker 19.3 m away with an 8.0 m
    measured reach puts its pieces 11.3 m from the centre -- inside a 13.1 m
    clear radius that every other gate passed.

    THE RULE IS `clearance.Locations.cylinder_room`, over the populations in
    `depends_on`: BUILD_ROOM_DEPENDS_ON for the build's own pre-generation
    gate, the full `clearance.CLEARING_POPULATION_DEPENDS_ON` for the siting.
    It REFUSES rather than clipping -- a silently clipped radius leaves
    standing forest against a wall, which is a decision for the caller and not
    for the arithmetic.
    """
    room = loc.cylinder_room(cx, cz, depends_on)
    room["tool"] = ("tools/jumpstart/settlements/build.py::clear_radius_room "
                    "-> " + room["tool"])
    return room


def clearing_gate(loc: clearance.Locations, place: dict,
                  depends_on: tuple = BUILD_ROOM_DEPENDS_ON) -> dict:
    """THE CLEARING GATE'S OFFLINE HALF for one placement, exactly as
    `apply_unit_ground` asks it: the cylinder `clearing/area.py::
    clearing_area` derives at `APRON_M`, against `clear_radius_room`.

    `settlements/plan.py` asks the SAME function of every pad it sites
    (`unit_placement` over the unit the writer derives) with the FULL
    population, so a pad is only placed where neither this gate nor the live
    cylinder census after it can refuse -- or it is refused at stage 8 with
    the location named and the numbers."""
    import area as clear_area                               # noqa: PLC0415
    try:
        ar = clear_area.clearing_area(place, apron=APRON_M)
    except clear_area.ClearAreaRefused as exc:
        return {"verdict": "REFUSED", "radius_m": None, "room": None,
                "why": str(exc), "ar": None}
    room = clear_radius_room(loc, ar.centre_x, ar.centre_z, depends_on)
    if ar.radius_m > room["max_radius_m"]:
        return {"verdict": "REFUSED", "radius_m": round(ar.radius_m, 2),
                "room": room, "ar": ar,
                "why": (f"the {ar.radius_m:.2f} m clearing cylinder at "
                        f"({ar.centre_x:g}, {ar.centre_z:g}) can reach "
                        f"{room['binding']['why']}, so the widest safe "
                        f"radius here is {room['max_radius_m']} m")}
    return {"verdict": "clear", "radius_m": round(ar.radius_m, 2),
            "room": room, "ar": ar, "why": ""}


def applied_delta(comps: dict, px: float, pz: float) -> float:
    """The height change this write applies at an arbitrary point.

    Bilinear over the four TerrainComp samples around the point, with
    UNWRITTEN samples contributing zero -- which is how `Heightmap` renders
    ground and is also where `clearance.TERRAIN_SPREAD_M` comes from:
    `ApplyToHeightmap` applies each sample's delta to that sample only, so the
    surface tilts out to the next unwritten sample and no further.
    """
    import tcdata
    total = 0.0
    for (zx, zz), comp in comps.items():
        cx, cz = tcdata.zone_centre(zx, zz)
        fx = (px - cx) / tcdata.SCALE + tcdata.PITCH // 2
        fz = (pz - cz) / tcdata.SCALE + tcdata.PITCH // 2
        x0, z0 = math.floor(fx), math.floor(fz)
        if not (0 <= x0 < tcdata.PITCH - 1 and 0 <= z0 < tcdata.PITCH - 1):
            continue
        tx, tz = fx - x0, fz - z0
        acc = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            i = (z0 + dz) * tcdata.PITCH + (x0 + dx)
            if comp.modified_height[i]:
                acc += w * comp.level_delta[i]
        total = acc if abs(acc) > abs(total) else total
    return total


def delta_gate(loc: clearance.Locations, comps: dict,
               samples: list[tuple[float, float]]) -> dict:
    """Does this write MOVE THE GROUND under any location's pieces?

    The hazard of a non-destructive write is not deletion, it is a location
    piece left FLOATING or BURIED because the ground under it moved -- the
    defect the operator condemned a world for. Distance does not measure that;
    the delta does. So every instance within 60 m of the written set is probed
    on a 1 m lattice over ITS OWN measured reach disc, because a location's
    pieces are spread over that reach and the marker can be metres from the
    piece that would float.

    `clearance.verdict_for_samples(destructive=False)` REFUSES without this,
    which is the point: the loosened distance budget was granted against this
    measurement.
    """
    probes = 0
    worst = 0.0
    violations = []
    instances = loc.instances_near(samples, 60.0)
    for inst in instances:
        ix, iz = inst["xz"]
        reach = max(1.0, inst["reach_m"])
        local = 0.0
        r = int(math.ceil(reach))
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if math.hypot(dx, dz) > reach:
                    continue
                probes += 1
                d = applied_delta(comps, ix + dx, iz + dz)
                if abs(d) > abs(local):
                    local = d
        if abs(local) > abs(worst):
            worst = local
        if abs(local) > clearance.DELTA_TOL_M:
            violations.append({
                "name": inst["name"], "prefab": inst["prefab"],
                "min_dist_m": inst["min_dist_m"], "reach_m": inst["reach_m"],
                "worst_delta_m": round(local, 3),
                "tolerance_m": clearance.DELTA_TOL_M,
                "reason": ("this write moves the ground under that location's "
                           "reach disc, which leaves its pieces floating or "
                           "buried")})
    return {
        "verdict": "clear" if not violations else "VIOLATION",
        "instances_probed": len(instances), "probe_points": probes,
        "worst_applied_delta_m": round(worst, 3),
        "tolerance_m": clearance.DELTA_TOL_M,
        "violations": violations,
        "method": ("MEASURED: for every instance within 60 m of the written "
                   "samples, the APPLIED height delta is evaluated on a 1 m "
                   "lattice over that instance's OWN measured reach disc by "
                   "bilinear blend of the four TerrainComp samples around "
                   "each probe, unwritten samples contributing zero -- which "
                   "is how Heightmap renders ground. Probing the DISC rather "
                   "than the marker matters: a location's pieces are spread "
                   "over its reach and the marker can be metres from the "
                   "piece that would float."),
        "tool": "tools/jumpstart/settlements/build.py::delta_gate",
    }


def zone_probe(srv, zx: int, zz: int) -> dict:
    """MEASURE that a zone is generated, and keep the command that measured it.

    `PlaceZoneCtrl` puts exactly one `_ZoneCtrl` at the zone CENTRE and
    `objects_count`'s filter is an XZ cylinder, so max=1 is exact and the
    answer is two lines. `zone_generated_before: True` without this command
    behind it is an assumption about what the pipeline did first.
    """
    import tcdata
    cx, cz = tcdata.zone_centre(zx, zz)
    cmd = f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1"
    got, _ = srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
    return {"cmd": cmd, "count": got, "centre": [cx, cz]}


def flatten_pad(place: dict, name: str) -> tuple[dict, dict, dict, dict]:
    """flatten.py's own pad stamp, for one placement. Nothing is sent."""
    solved = place["solved"]
    w, d = FL.pad_extent(place)
    zones = FL.zones_for(float(solved["x"]), float(solved["z"]),
                         w + 2 * APRON_M, d + 2 * APRON_M)
    patches = FL.run_patchscan(sorted(set(zones)), SEED,
                               FL.SCRATCH / f"patch_{name}.bin")
    comps: dict[tuple[int, int], object] = {}
    op_y: dict[tuple[int, int], float] = {}
    stats = FL.build(place, patches, zones, PAINT, APRON_M, comps, op_y)
    return stats, comps, op_y, patches


def generated_sha(patch) -> str:
    """The digest of the GENERATED heights this write was computed against.

    It is what detects a worldgen change: the deltas are only correct relative
    to these numbers, so a replay against different generated ground must stop
    rather than write a pad levelled to the wrong height.
    """
    return sha(struct.pack(f"<{patch.n * patch.n}f", *patch.heights))


def terrain_entries(b, unit: dict, comps: dict, op_y: dict, patches: dict,
                    probes: dict, merged: dict | None = None
                    ) -> tuple[list[dict], list[str], list[str]]:
    """One schema `entries[]` record per zone, plus the wire and the blob list.

    `write_entries` is flatten's, so the entry NAME and the bytes are its; the
    blob is stored in the ledger and the data-entry file is regenerated from
    that blob by `replay._materialise_data_entries`, so the world gets the
    bytes the log holds rather than whatever is left in the watched directory.
    """
    _path, entries = FL.write_entries(unit["id"], comps, op_y)
    out: list[dict] = []
    blobs: list[str] = []
    for e in entries:
        zx, zz = e["zone"]
        blob = comps[(zx, zz)].blob(op_y.get((zx, zz), 0.0))
        digest = b.blob(blob, note=f"TCData {e['entry']} zone {zx},{zz}")
        blobs.append(digest)
        sc = e["selfcheck"]
        out.append({
            "zone": [int(zx), int(zz)],
            "centre": [float(c) for c in e["centre"]],
            "data_entry": e["entry"],
            "blob_sha256": digest,
            "generated_heights_sha256": generated_sha(patches[f"z_{zx}_{zz}"]),
            # WHICH SURFACE THAT DIGEST IS OF, and it is load-bearing for the
            # replay.  A TCData delta is relative to a generated height, and
            # there are TWO generated heights: `WorldGenerator.GetHeight` (the
            # point's own biome) and `HeightmapBuilder::Build`'s per-zone
            # corner-biome blend, which is what the engine adds the delta to.
            # They differ by up to 8.807 m on this seed -- see
            # `terraform/flatten.py::DATUM_SURFACE` for the IL and the
            # measurement.  A digest without its surface is as useless as a
            # digest without its byte order: the same deltas on the other
            # surface are a different pad.  The 107 terrain_write records
            # written before this field existed are all `worldgen` by
            # construction, which is exactly why wt-peak floats.
            "generated_surface": FL.DATUM_SURFACE,
            "zone_generated_before": probes[(zx, zz)]["count"] >= 1,
            "zone_generated_probe": probes[(zx, zz)]["cmd"],
            "op_record": {
                "operations": int(sc["operations"]),
                "last_op_point": [round(float(v), 3) for v in sc["last_op_point"]],
                "last_op_radius": round(float(sc["last_op_radius"]), 3),
            },
            "samples": e["counts"]["height_modified"],
        })
        if merged and (zx, zz) in merged:
            m = merged[(zx, zz)]
            out[-1]["merged_from"] = m["merged_from"]
            out[-1]["merge_policy"] = m["merge_policy"]
    wire = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
            f"-prefab _TerrainCompiler -force" for e in entries]
    wire += [f"spawn_object _TerrainCompiler "
             f"from={e['centre'][0]:g},{e['centre'][1]:g},0 data={e['entry']}"
             for e in entries]
    return out, wire, blobs


def terrain_record(unit: dict, site: str, entries: list[dict], wire: list[str],
                   blobs: list[str], stats: dict, verdict: dict) -> dict:
    return {
        "op": "terrain_write",
        "params": {
            "name": unit["id"], "paint": PAINT,
            # THE RECTANGLE'S POSITION, not only its size.  `pad_w` and
            # `pad_d` without this are a size with no position, and MEASURED
            # on this chain the centre is not recoverable from the blobs:
            # four of the 52 pads share a target height with an adjacent pad,
            # their sample components merge, and the recovered centre is out
            # by up to 6.0 m.  `schema.py` now refuses a pad write without it.
            "centre": [float(unit["x"]), float(unit["z"])],
            "datum": (f"pad target_y {unit['target_y']} m; deltas against "
                      f"the {FL.DATUM_SURFACE} surface at 1 m (seed {SEED}). "
                      f"`heightmap` is HeightmapBuilder's per-zone "
                      f"corner-biome blend -- the ground the engine draws and "
                      f"the one ApplyToHeightmap adds the delta to -- NOT "
                      f"WorldGenerator.GetHeight, which differs by up to "
                      f"8.807 m on this seed and is what left wt-peak's "
                      f"watchtower in the air"),
            "entries": [{k: v for k, v in e.items() if k != "samples"}
                        for e in entries],
            "location_check": verdict,
            "target_y": unit["target_y"], "apron_m": APRON_M,
            "pad_w": unit["pad_w"], "pad_d": unit["pad_d"],
            "max_cut_m": stats["max_cut"], "max_fill_m": stats["max_fill"],
            "over_clamp": stats["over_clamp"],
            "role": "site_pad", "site_id": unit["id"],
        },
        "wire": wire,
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": ["_TerrainCompiler"], "blobs": blobs},
        "expect": {"terrain_compiler": len(entries)},
        "meta": {"settlement": site, "unit": unit["id"],
                 "samples_written": sum(e["samples"] for e in entries),
                 "zone_checks": stats["checks"],
                 "why": ("one compiler per zone, deleted before spawn so the "
                         "op is idempotent; the pad is levelled per BUILDING "
                         "because MEASURED no 176 m district on this seed can "
                         "be written inside TerrainComp's +/-8 m clamp")},
    }


# --- step 3: the body ------------------------------------------------------

# SIBLING_ROOM_DEPENDS_ON: THE ROOM A SIBLING LEAVES IS MEASURED TO ITS PIECES
# WHEN ITS CIRCUMSCRIBED CIRCLE LEAVES NONE.  The bound below treats every
# neighbour as the circle through its footprint's corners, which is the
# cheapest bound that is never optimistic and is the wrong shape for a long
# body: MEASURED on the Ulfsland regeneration, `vestvik-hut-1` stands 11.04 m
# from `vestvik-hall-1`, a 8.4 x 17.0 m body whose circle (half-diagonal 9.48
# m) leaves 1.06 m of room -- below the hut's first usable gap (1.207 m, the
# midpoint of its 1.0 m and 1.414 m rings) -- so step 5 REFUSED the hut,
# "no radius can make this count exact", while the hall's nearest PIECE is
# 2.01 m from the hut's anchor (a 1.51 m room, solvable at 1.21 m).  The same
# 4.2 x 17 m footprint's circle overstates its reach toward a point abeam of
# its long side by 5.0 m.  Offline sweep of the 49 units of the Pirate68 plan:
# this is the only one the circle refuses.
#
# So when, and ONLY when, the circle bound leaves no usable radius, each
# sibling whose circle room is under the largest candidate gap is re-measured
# against its own PIECES (its plan, made in a throwaway directory, never read
# from a shared scratch path that another world may have written).  A sibling
# whose plan cannot be made keeps its circle room, and a sibling whose pieces
# are TRULY close still refuses the unit -- a refusal is a real answer only
# when the pieces confirm it.  A unit the circle already solved is untouched,
# byte for byte, and no plan is made for it.
#
# NEUTERS: `circle` is the shipped rule (the hut refuses); `blind` lets the
# re-measure ignore every sibling, which accepts a unit whose neighbour's
# piece is 1.2 m away.  Proven by `_RADIUS_CASES`.
SIBLING_ROOM_DEPENDS_ON = ("the_nearest_sibling_piece_when_the_circle_leaves_"
                           "no_radius",)
_SIBLING_ROOM_MODE = "pieces"       # pieces | circle | blind


def sibling_piece_xz(o: dict) -> list[tuple[float, float]] | None:
    """The XZ of every piece sibling `o` stands, from its own plan, or None
    when that plan cannot be made (a unit with no body, a body that does not
    resolve).  Made in a throwaway directory each time."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="sibling-room-") as td:
            plan_path, _info = spawn_plan_file(o, o["target_y"], Path(td))
            return [(r[1], r[3]) for r in VP.plan_rows(plan_path)]
    except (Exception, SystemExit):                 # noqa: BLE001
        return None


def check_radius(unit: dict, units: list[dict], clear_radius_m: float,
                 rows: list[tuple], piece_source=None) -> float:
    """The radius a prefab count can be EXACT inside.

    Three things bound it, and all three are measurements rather than
    preferences.

    (1) A SIBLING'S IDENTICAL PREFABS MUST NOT ANSWER FOR THIS BODY, so the
    radius stays clear of the nearest neighbour's own pieces. Measured
    against that neighbour's BODY FOOTPRINT and not its PAD: a pad is
    levelled, cleared ground and contains no prefabs, and using the pad was
    MEASURED to collapse this radius to its 1 m floor on `stenvik-hut-1`
    (cottage-1's body is 4.4 x 6.4 but its pad is 10.4 x 12.4, so the pad's
    8.09 m half-diagonal ate the whole 8.25 m separation).

    (2) UNCLEARED VEGETATION MUST NOT ANSWER EITHER, so it stays inside the
    radius `objects_clear` emptied.

    (3) THE RADIUS MUST NOT LAND ON A RING OF PIECES. `objects_count`'s
    filter is a boundary comparison, and a body's pieces sit on exact rings:
    `stenvik-hut-1`'s 28 pieces are at 1.000 m (x4), 1.414 (x8), 2.003 (x1)
    and 2.236 (x15) from its anchor. At a radius of exactly 1.000 the check
    MEASURED `want 4 got 2` -- four pieces on the boundary, two of them
    counted -- while the same body re-counted at 3 m answers 28 for 28 with
    the plan's exact histogram. That is a coin flip, not a postcondition. So
    the radius is snapped to the MIDPOINT of a gap between rings, which
    leaves every piece unambiguously inside or outside.

    A refusal is a real answer here: if no gap-midpoint that contains at
    least one piece fits under the bound, a count inside that bound proves
    nothing and the caller is told rather than handed a number.
    """
    reach = max((math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows),
                default=1.0)
    safe = []
    sibs = []
    for o in units:
        if o["id"] == unit["id"]:
            continue
        foot = math.hypot(o.get("foot_w", o["pad_w"]),
                          o.get("foot_d", o["pad_d"])) / 2
        room = math.hypot(o["x"] - unit["x"], o["z"] - unit["z"]) - foot - 0.5
        safe.append(room)
        sibs.append((o, room))
    cap = min([reach + 0.5, clear_radius_m - 0.1] + safe)
    rings = sorted({round(math.hypot(r[1] - unit["x"], r[3] - unit["z"]), 3)
                    for r in rows})
    if not rings:
        return round(max(1.0, cap), 2)
    gaps = [(rings[i] + rings[i + 1]) / 2 for i in range(len(rings) - 1)]
    gaps.append(rings[-1] + 0.25)
    usable = [g for g in gaps if g <= cap]
    if not usable and _SIBLING_ROOM_MODE != "circle":
        # SIBLING_ROOM_DEPENDS_ON: re-measure the siblings the circle made
        # tight against their own pieces, and only those.
        source = piece_source or sibling_piece_xz
        rooms = []
        for o, circle_room in sibs:
            room = circle_room
            if circle_room < max(gaps):
                if _SIBLING_ROOM_MODE == "blind":
                    room = math.inf
                else:
                    pts = source(o)
                    if pts:
                        room = min(math.hypot(px - unit["x"], pz - unit["z"])
                                   for px, pz in pts) - 0.5
            rooms.append(room)
        cap = min([reach + 0.5, clear_radius_m - 0.1] + rooms)
        usable = [g for g in gaps if g <= cap]
        safe = rooms
    if not usable:
        raise SystemExit(
            f"REFUSED {unit['id']}: no radius can make this count exact. The "
            f"bound is {cap:.2f} m (reach+0.5 {reach + 0.5:.2f}, clear-0.1 "
            f"{clear_radius_m - 0.1:.2f}, nearest sibling room "
            f"{min(safe) if safe else float('inf'):.2f}) and the body's "
            f"pieces sit on rings {rings[:6]}, so every candidate radius "
            f"either contains no piece or lands ON a ring -- where "
            f"`objects_count`'s boundary comparison MEASURED 2 of 4 pieces "
            f"at exactly 1.000 m. A count that can be wrong by a boundary is "
            f"not a postcondition.")
    return round(max(usable), 2)


def spawn_plan_record(b, unit: dict, site: str, role: str, plan_path: Path,
                      info: dict, units: list[dict],
                      clear_radius_m: float) -> dict:
    rows = VP.plan_rows(plan_path)
    text = plan_path.read_text("utf-8")
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.startswith("#")]
    digest = b.blob(text.encode(), note=f"spawn plan {unit['id']}")
    groups = PLACE.batches(lines, PLACE.batch_budget(BATCH_BYTES))
    radius = check_radius(unit, units, clear_radius_m, rows)
    inside: dict[str, int] = {}
    for prefab, x, _y, z, _yaw in rows:
        if math.hypot(x - unit["x"], z - unit["z"]) <= radius:
            inside[prefab] = inside.get(prefab, 0) + 1
    dominant = max(inside.items(), key=lambda kv: kv[1])[0] if inside else None
    reach = max((math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows),
                default=1.0)
    # THE COUNT EXCLUDES THE EFFECTS A PLACED PIECE SPAWNS FOR ITSELF, and
    # this is MEASURED rather than tidied away. `stenvik-beehive-1` placed
    # 126 pieces and the check read 130: the four extras were
    # `sfx_FireAddFuel` x2 and `vfx_groundtorch_addFuel` x2, the one-shot
    # effects the body's two `piece_groundtorch_wood` spawn when they are
    # fuelled. They are TRANSIENT -- the same cylinder re-counted minutes
    # later answers 126 with the identical command -- so an exact count taken
    # immediately after placement RACES them, and the race is invisible on a
    # body with no torch. Excluding them makes the postcondition deterministic
    # and it still counts every piece the plan placed, because no spawn plan
    # in this corpus contains an `sfx_`/`vfx_` prefab: they only ever appear
    # as a piece's own runtime children.
    expect: dict = {"objects_count": {
        "ids": "*", "ignore": "_*,sfx_*,vfx_*",
        "pos": [round(unit["x"], 2), round(unit["z"], 2)],
        "max": radius, "total": sum(inside.values()), "tolerance": 0}}
    if dominant:
        expect["prefab_count"] = [{
            "prefab": dominant,
            "pos": [round(unit["x"], 2), round(unit["z"], 2)],
            "max": radius, "count": inside[dominant], "tolerance": 0}]
    return {
        "op": "spawn_plan",
        "params": {
            "plan_sha256": digest,
            "plan_ref": str(plan_path),
            "anchor": {"x": unit["x"], "y": unit["target_y"], "z": unit["z"],
                       "yaw": unit["yaw"]},
            "command_count": len(lines), "prefabs": info["prefabs"],
            "body": {"filename": unit["body"], "sha256": unit["sha256"]},
            "align": "floor-center", "reach_m": round(reach, 2),
            "pad_height": unit["target_y"], "batch_bytes": BATCH_BYTES,
            "datum": f"pad top at target_y {unit['target_y']} m",
            "role": role, "site_id": unit["id"],
        },
        "wire": [";".join(g) for g in groups],
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": sorted(info["prefabs"]), "blobs": [digest]},
        "expect": expect,
        "meta": {"settlement": site, "batches": len(groups),
                 "count_radius_m": radius,
                 "pieces_inside_count_radius": sum(inside.values()),
                 "why": ("bottom-up in Y from to_rcon_plan so WearNTear "
                         "support exists when each piece lands; batched "
                         "because MEASURED round trips are the throughput "
                         "floor, and the count radius is bounded by the "
                         "nearest sibling pad so the postcondition cannot "
                         "pass on a neighbour's pieces")},
    }


# --- step 4: the portals, BOTH ends ---------------------------------------


def _waypoints():
    """WayFinding's emitter, loaded by PATH rather than by package import.

    `tools/jumpstart` is not a package on this checkout, and `network/
    waypoints.py` does `from . import DATA, JUMPSTART`, so it has to be given
    a package context explicitly. Loaded once and cached on the function, so
    the guidepost geometry and the sign validator are ONE implementation --
    the whole reason for calling it rather than rewriting it.
    """
    import importlib.util
    import types
    if getattr(_waypoints, "mod", None) is not None:
        return _waypoints.mod
    pkg = types.ModuleType("jsnet")
    pkg.__path__ = [str(JUMPSTART / "network")]
    sys.modules.setdefault("jsnet", pkg)
    init = importlib.util.spec_from_file_location(
        "jsnet.__init__", JUMPSTART / "network/__init__.py")
    base = importlib.util.module_from_spec(init)
    init.loader.exec_module(base)
    for name in ("DATA", "JUMPSTART", "HERE"):
        if hasattr(base, name):
            setattr(pkg, name, getattr(base, name))
    spec = importlib.util.spec_from_file_location(
        "jsnet.waypoints", JUMPSTART / "network/waypoints.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jsnet.waypoints"] = mod
    spec.loader.exec_module(mod)
    _waypoints.mod = mod
    return mod


def site_tag(plan: dict, site: str) -> str:
    if site in plan["towns"]:
        return plan["towns"][site]["tag"]
    return next(r["tag"] for r in plan["outliers"] if r["id"] == site)


def hub_placement(world: str | None = None) -> dict:
    """THIS world's portal hub: the one placement declaring `portal.role: hub`.

    The shipped rule read `worlds/Ulfsland/...` for EVERY world and cached its
    solve in `/tmp/settle/hub_spots.json`, a file keyed by nothing.  MEASURED
    on Fresh42 drive 2: all 14 settlement hub ends stood in Ulfsland's ring --
    x -309.5..-276.5, z 196.5..230.5, at a 2026-09-15 cache's Pirate68 ground
    heights -- 629 m from Fresh42's own hall at (-220.5, -411.5), in a Fresh42
    beech wood (the gate's approach findings name Beech1 at five of them)."""
    import stock  # noqa: PLC0415  terraform/stock.py: the portal graph's reader
    w = world or WORLD
    if HUB_SOURCE == "ulfsland":
        doc = yaml.safe_load((JUMPSTART / "worlds/Ulfsland/deepnorth-sandbox"
                              / "placements.yaml").read_text())
        return next(p for p in doc["placements"]
                    if p["id"] == "sandbox-portal-hub")
    hub = stock.portal_graph(w)["hub"]
    if hub is None:
        raise SystemExit(
            f"{w} declares no placement with `portal.role: hub`, so a "
            f"settlement tag has no hall for its far end")
    return stock.load_placement(w, hub["preset"], hub["id"])


def body_of(place_or_unit: dict, at: tuple[float, float, float], yaw: float):
    """A `fixtures.PlacedBody` for a body standing where it stands."""
    import importlib.util
    import fixtures
    from to_rcon_plan import read_objects
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    libmat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(libmat)
    name = place_or_unit.get("blueprint") or place_or_unit["body"]
    path = libmat.locate_by_filename(name)
    if path is None:
        raise FileNotFoundError(f"no body resolves for {name}")
    return fixtures, read_objects(path), fixtures.place_body(
        read_objects(path), at, yaw_deg=yaw)


def live_positions(srv, prefab: str, x: float, y: float, z: float,
                   radius: float) -> list[tuple[float, float, float]]:
    """Where a prefab already stands. Prefab-scoped AND radius-bounded:
    `findObjects` refuses without both, and an unbounded listing is the
    question whose ANSWER is long -- the one that has wedged this server."""
    import locations as LOCM
    reply = srv.command(f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} "
                        f"{z:.2f} {radius:.2f} -detailed")
    return [(px, py, pz) for pre, px, py, pz in
            [(m[0], float(m[1]), float(m[2]), float(m[3]))
             for m in LOCM.POSITION_RE.findall(reply)] if pre == prefab]


_HUB_SPOTS: dict[tuple, dict] = {}
# How many arch spots one tag may try before its pair is refused.  Each
# refused spot's box joins `taken`, so the next search starts elsewhere.
HUB_ARCH_ATTEMPTS = 40


def hub_spots(tags: list[str]) -> dict[str, dict]:
    """Every settlement tag's arch in THIS world's hall AND the board that
    names it, solved together: `{tag: {"arch", "board", "why"}, "_hub": ...}`.

    THE PACKING CONTINUES `terraform/stock.py::hub_ends` -- the hall's own
    stations, props and chests, then its declared installation arches -- with
    `stock.hub_arch`, the one arch search, in this plan's tag order.  Every
    settlement run and every installation spoke therefore asks the same
    packing: nothing is solved onto anything else, and nothing depends on
    what already stands (the shipped solve took LIVE portals as `taken`, which
    is why it had to be cached in a file keyed by nothing).

    AN ARCH IS ACCEPTED ONLY WITH ITS BOARD.  MEASURED on Fresh42's hall,
    offline: packing the 16 settlement arches first and fitting boards after
    left 6 of 16 with no board that names its own door -- the arches stood
    4-5 m apart at yaw 135 with no lane for a sign.  So each arch spot is
    tried with its board (`waypoints.portal_guidepost`: clear of every arch
    and fixture, nearer its own arch than any other) outside the hall's
    solids (`fixtures.audit`), and must not stand nearer an EARLIER board
    than that board's own arch; a spot that fails joins `taken` and the next
    is tried.  `board` None is a refusal with `why`, and `apply_portals` then
    places neither end of that tag.

    ON THE HALL'S WRITTEN PAD (`stock.SEAT_DEPENDS_ON`, the packing's
    `seat`): an arch or a board the pad does not reach is refused with its
    number, never stood at the pad height over ground nobody levelled.
    MEASURED on Fresh42's hall, offline: 13 of 16 settlement arches stood
    off its 34.8 m pad, u-lhwest's 3.29 m arch under 3.46 m of hillside.
    """
    key = (WORLD, tuple(tags))
    if key in _HUB_SPOTS:
        return _HUB_SPOTS[key]
    import stock  # noqa: PLC0415
    packing = stock.hub_ends(WORLD)
    if packing["hub"] is None:
        raise SystemExit(f"{WORLD} declares no portal hub: no settlement tag "
                         f"has a hall for its far end")
    out = pack_settlement_arches(packing, tags)
    _HUB_SPOTS[key] = out
    return out


def pack_settlement_arches(packing: dict, tags: list[str]) -> dict[str, dict]:
    """`hub_spots`' packing over any hub packing in `stock.hub_ends`' shape
    (the hall at its recorded site, or `stock.fixture_ring`'s stage-7
    candidate with no seat): `{tag: {"arch", "board", "why"}, "_hub"}`.

    EVERY ARCH KEEPS ITS WALK-IN (`stock.WALKIN_DEPENDS_ON`): the arch search
    and the board walk ask the packing's walk-in book -- a `fork()`, so a
    memoised packing is continued, never written -- and an arch or board is
    stood only where every arch in the hall still has a face the gate's own
    `approach_ray` walks a walker 6 m into.  MEASURED on Fresh42 drive 3: 10
    of the hall's 25 arches had none, every blocker a neighbouring arch, a
    board or the hall's brazier."""
    import fixtures  # noqa: PLC0415
    import stock  # noqa: PLC0415
    hub = packing["hub"]
    seat = packing.get("seat")
    book = packing.get("walkins")
    book = None if book is None else book.fork()
    W = _waypoints()
    taken = list(packing["boxes"])
    arches = [(p["x"], p["y"], p["z"], p["yaw"])
              for p in packing["ends"].values() if p]
    boards: list[tuple[tuple, tuple]] = []     # (board xyz, its own arch xyz)
    out: dict[str, dict] = {"_hub": dict(hub)}
    for tag in tags:
        tried: list[dict] = []
        got = None
        unseated = None
        walkin = None
        for _ in range(HUB_ARCH_ATTEMPTS):
            arch, unseated = stock.hub_arch(
                packing["body"], packing["index"], packing["mask"], hub["x"],
                hub["z"], hub["pad_y"], hub["pad_half"], hub["yaw"],
                taken + [t["box"] for t in tried], packing["indoor_centre"],
                seat=seat, walkins=book)
            if arch is None:
                walkin = None if book is None else book.refusal()
                break
            at = (arch["x"], arch["y"], arch["z"])
            robbed = [b for b in boards
                      if math.dist(b[0], at)
                      < math.dist(b[0], b[1]) + W.BIND_MARGIN_M]
            why: list[str] = []
            board = None
            if robbed:
                why.append(f"it would stand nearer the board at "
                           f"{tuple(round(v, 2) for v in robbed[0][0])} than "
                           f"that board's own arch")
            else:
                for side in ("left", "right"):
                    try:
                        pieces = W.portal_guidepost(
                            arch["x"], arch["y"], arch["z"], arch["yaw"],
                            "board", side=side, neighbours=arches,
                            obstacles=taken, seat=seat, walkins=book)
                    except W.WaypointError as exc:
                        why.append(f"{side}: {exc}")
                        continue
                    inside = fixtures.audit([p.audit_row() for p in pieces],
                                            packing["body"])["failures"]
                    if inside:
                        why.append(f"{side}: inside the hall -- {inside[0]}")
                        continue
                    board = pieces
                    break
            if board is None:
                tried.append({"box": tuple(arch["box"]), "at": at,
                              "why": "; ".join(why)})
                continue
            got = (arch, board)
            break
        if got is None:
            out[tag] = {"arch": None, "board": None, "why": (
                (f"no arch spot on the hall's levelled ground: "
                 f"{stock.unseated_reason(unseated)}"
                 if unseated is not None and not tried else
                 f"no arch spot in the hall keeps a walk-in: "
                 f"{stock.walkin_reason(walkin)}"
                 if walkin is not None and not tried else
                 f"{len(tried)} arch spot(s) tried and none takes a board "
                 f"that names its own door"
                 + (f"; the last: {tried[-1]['why']}" if tried else
                    "; the hall has no legal arch spot left"))),
                **({"unseated": unseated} if unseated is not None else {}),
                **({"walkin": walkin} if walkin is not None else {})}
            continue
        arch, board = got
        arch = dict(arch, tag=tag)
        if book is not None:
            book.commit([stock.row_of(arch, PORTAL_PREFAB)]
                        + [(p.prefab, p.x, p.y, p.z, p.yaw) for p in board])
        taken.append(tuple(arch["box"]))
        taken += W._boxes(board)
        arches.append((arch["x"], arch["y"], arch["z"], arch["yaw"]))
        bd = next(p for p in board if p.prefab == W.BOARD_PREFAB)
        boards.append(((bd.x, bd.y, bd.z), (arch["x"], arch["y"], arch["z"])))
        out[tag] = {"arch": arch, "why": None, "tried": len(tried),
                    "board": [(p.prefab, p.x, p.y, p.z, p.yaw) for p in board]}
    return out


def portal_record(tag: str, x: float, y: float, z: float, yaw: float,
                  site: str, end: str, other: str, spot: dict) -> dict:
    import fixtures
    problem = fixtures.portal_tag_problem(tag)
    if problem:
        raise SystemExit(f"portal tag {tag!r} refused: {problem}")
    cmd = fixtures.portal_spawn_command(PORTAL_PREFAB, x, y, z, yaw, tag)
    return {
        "op": "portal",
        "params": {
            "prefab": PORTAL_PREFAB, "pos": [x, y, z], "yaw_deg": yaw,
            "tag": tag, "pair_tag": tag, "guard_radius_m": 0.5,
            "zdo_strings": {"tag": tag},
            "data_b64": cmd.split("data=")[1],
            "role": "spawn_portal", "site_id": site,
        },
        "wire": [cmd],
        "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                     "prefabs": [PORTAL_PREFAB], "blobs": []},
        # `tag_readback` ONLY, and dropping the prefab count is a measurement
        # rather than a simplification. MEASURED by Crossings:
        # `ZDOMan::AddObjectsPerChunk` SKIPS any ZDO whose prefab is in
        # `Game.PortalPrefabHash`, so a chunk-scoped count reports zero
        # portals even when they are standing. A count that can answer zero
        # for a portal that exists is a check that fails for the wrong reason,
        # and the thing worth proving is the TAG off the ZDO, which
        # `findObjects -prefab ... -detailed` reads directly.
        "expect": {"tag_readback": True},
        "meta": {"end": end, "pair_other_end": other,
                 "clearance_m": spot.get("clearance_m"),
                 "wall_gap_m": spot.get("wall_gap_m"),
                 "free_above_m": spot.get("free_above_m"),
                 "why": ("pairing is EXACT STRING EQUALITY with a uniform "
                         "random draw among equally-tagged unconnected "
                         "portals, so a tag with one end pairs with nothing "
                         "and a blank tag pairs with the world's 26 blank-tag "
                         "mod-location portals at random")},
    }


def sign_records(pieces, site: str, tag: str) -> list[dict]:
    """WayFinding's guidepost, emitted through the ledger. Its emitter solves
    the board's offset against `fixtures.fixture_box`, so it cannot disagree
    with the gate that fails a build on an intersection."""
    out = []
    for p in pieces:
        params = {"prefab": p.prefab, "pos": [p.x, p.y, p.z], "yaw_deg": p.yaw,
                  "guard_radius_m": 0.5, "role": "waypoint_sign",
                  "site_id": site}
        if p.text is not None:
            import sys as _sys
            W = _waypoints()
            params["data_b64"] = W.sign_data(p.text)
            params["zdo_strings"] = {"text": p.text}
        out.append({
            "op": "spawn", "params": params, "wire": [p.spawn_command()],
            "requires": {"mods": ["WorldEditCommands", "ServerDevcommands"],
                         "prefabs": [p.prefab], "blobs": []},
            "expect": {"prefab_count": [{"prefab": p.prefab,
                                         "pos": [round(p.x, 2), round(p.z, 2)],
                                         "max": 1.0, "count": 1,
                                         "tolerance": 0}]},
            "meta": {"tag": tag,
                     "why": ("MEASURED: TeleportWorld::GetHoverText is the "
                             "only place a tag is shown and hover is gated at "
                             "5 m, so an unsigned portal is an anonymous arch")},
        })
    return out


def unit_seat(unit: dict):
    """The ground levelled for a unit's fixtures: its written pad
    (`stock.PadSeat`, `stock.SEAT_DEPENDS_ON`), or None under the shipped
    rule."""
    import stock  # noqa: PLC0415
    if stock.SEAT_MODE != "written_pad":
        return None
    return stock.PadSeat(unit["x"], unit["z"], unit["pad_w"], unit["pad_d"],
                         unit["target_y"], APRON_M)


# ---------------------------------------------------------------------------
# THE SITE END KEEPS ITS WALK-IN -- AGAINST THE WHOLE SETTLEMENT AND THE SEED.
#
# MEASURED on Fresh42 drive 4, gate `portals` approach_ray on the frozen save
# af96bfb62427: 5 of the 14 settlement-end arches had no clear walk-in on
# either face, and each had OUR piece on one face and a SEED tree or rock on
# the other.  u-wtnorth (149.90, 238.20): +Z stopped by a Birch1 whose pivot
# stands 16.9 m from the tower, past its 16.14 m clearing, its box reaching
# the corridor; -Z by the tower's own stone_pillar 1.5 m behind the arch.
# u-treenear: -Z by tree-near-3's wood_door -- a SIBLING body the solve never
# saw, because this searched against `units[0]` alone.  The hall's arches
# kept their walk-ins (`stock.Walkins`, WALKIN_DEPENDS_ON); the settlement
# ends had no book at all.
#
# So the site end is solved through the same book -- the gate's own
# approach_ray over its own `instruments.Solid`s and the Player capsule --
# holding every sibling body of the settlement whose pad can reach it (which
# the arch must also clear) and the seed's own objects round the pad, read
# off the live world (`seed_census`); and the site board walks with it.
# SITED, NOT CLEARED, by measurement: over the frozen save the book finds a
# spot with a clear face at 14 of 14 sites, moving 5 arches 1.0-3.6 m on
# their pads and deleting nothing, where a corridor clear of each arch's
# body-clear face would delete 11 seed ZDOs -- among them a rock4_forest and
# four rock4_coast at two spots, 22.9-26.9 m from their pads -- on mesh
# bounding boxes the gate itself books as upper bounds.  A pad with no such
# spot turns the arch a quarter and is then REFUSED with its number
# (`stock.walkin_reason`).
#
# SITE_SEED_DEPENDS_ON = ("seed_objects",): SITE_SEED_MODE "none" is the book
# over the bodies alone -- the neuter in this module's selftest, which stands
# u-wtnorth where drive 4 stood it.  `stock.WALKIN_MODE` "none" drops the
# book altogether, as it does at the hall.
# ---------------------------------------------------------------------------
SITE_SEED_DEPENDS_ON = ("seed_objects",)
SITE_SEED_MODE = "read"

# `objects_count id=*` answers inside 40 m only (`replay.Server.count`).
_STAR_COUNT_MAX_M = 40.0


def site_reach(unit: dict) -> float:
    """How far from a unit's centre its site end and the walker walking into
    it reach: the search square's half-diagonal, the gate's span, the
    walker's radius (`stock.walk_reach`, which the installations' own book
    asks the same way)."""
    import stock  # noqa: PLC0415
    return stock.walk_reach(max(unit["pad_w"], unit["pad_d"]) / 2
                            * math.sqrt(2))


def site_seed(srv, unit: dict) -> tuple[list, dict]:
    """The seed round a unit's site end, read off the live world
    (`seed_census`) over the site end's `site_reach`."""
    return seed_census(srv, unit["x"], unit["z"], unit["target_y"],
                       site_reach(unit))


def site_siblings(units: list[dict]) -> list:
    """The settlement's OTHER bodies whose pad reaches the anchor's
    `site_reach`, placed as they stand (`body_of`)."""
    u = units[0]
    reach = site_reach(u)
    return [body_of(o, (o["x"], o["target_y"], o["z"]), o["yaw"])[2]
            for o in units[1:]
            if math.hypot(o["x"] - u["x"], o["z"] - u["z"])
            - math.hypot(o["pad_w"], o["pad_d"]) / 2 <= reach]


def site_book(body, siblings=(), seed=()):
    """The site end's walk-in book: the anchor `body`, its `siblings` and
    `seed` (gate Solids, under SITE_SEED_MODE "read") -- or None under
    `stock.WALKIN_MODE` "none"."""
    import stock  # noqa: PLC0415
    if stock.WALKIN_MODE != "reserved":
        return None
    extra = [s for sb in siblings for s in stock.gate_solids(sb)]
    if SITE_SEED_MODE == "read":
        extra += list(seed)
    return stock.Walkins(body, extra)


def site_portal_spot(plan: dict, site: str, units: list[dict],
                     seed=()) -> tuple[dict, object]:
    """Where the site end stands: solved on the first unit's own pad, against
    that body's measured solids, outdoors beside it.  ON the pad
    (`unit_seat`): the search square is `max(pad_w, pad_d) / 2`, which on a
    rectangular pad reaches past its short sides.

    WITH ITS WALK-IN (see SITE_SEED_DEPENDS_ON): a spot is taken only where
    the arch clears the settlement's sibling bodies as well as its own, and
    has a face the gate's walker walks in on through all of them and `seed`
    -- the body's own yaw first, then a quarter turn.  Returns `(spot,
    book)`, the arch committed to the book so the site board walks against
    it."""
    import fixtures
    import stock  # noqa: PLC0415
    unit = units[0]
    _fx, _objs, body = body_of(unit, (unit["x"], unit["target_y"], unit["z"]),
                               unit["yaw"])
    siblings = site_siblings(units) if stock.WALKIN_MODE == "reserved" else []
    index = fixtures.SolidIndex(
        body.solids + [s for sb in siblings for s in sb.solids])
    book = site_book(body, siblings, seed)
    walk = None
    for yaw in (unit["yaw"], unit["yaw"] + 90.0):
        if book is not None:
            book.begin()
        spot, unseated = stock.seated_spot(
            unit_seat(unit), PORTAL_PREFAB, body, index, (unit["x"], unit["z"]),
            unit["target_y"], max(unit["pad_w"], unit["pad_d"]) / 2,
            prefer=(unit["x"], unit["z"]), yaws=(yaw,),
            want=fixtures.WANT_OUTDOOR,
            accept=None if book is None else book.accept)
        walk = walk or (None if book is None else book.refusal())
        if spot is not None or book is None:
            break
    if spot is None and unseated is not None:
        raise SystemExit(
            f"{site}: no outdoor portal position on {unit['id']}'s levelled "
            f"pad -- {stock.unseated_reason(unseated)}")
    if spot is None and walk is not None:
        raise SystemExit(
            f"{site}: no outdoor portal position on {unit['id']}'s pad keeps "
            f"a walk-in, at its yaw or a quarter turned -- "
            f"{stock.walkin_reason(walk)}")
    if spot is None:
        raise SystemExit(
            f"{site}: no legal outdoor portal position on {unit['id']}'s pad. "
            f"A refusal, not a fallback -- a portal inside a wall is the "
            f"defect this searches to avoid.")
    if book is not None:
        book.commit([stock.row_of(spot, PORTAL_PREFAB)])
    return spot, book


def euler_quat(x: float, y: float, z: float) -> tuple[float, ...]:
    """Unity's `Quaternion.Euler` -- z, then x, then y -- as (x, y, z, w):
    the rotation `findObjects -detailed` prints in degrees."""
    def axis(i: int, deg: float) -> tuple[float, ...]:
        q = [0.0, 0.0, 0.0, math.cos(math.radians(deg) / 2)]
        q[i] = math.sin(math.radians(deg) / 2)
        return tuple(q)

    def mul(a, b):
        return (a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
                a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
                a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
                a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2])
    return mul(mul(axis(1, y), axis(0, x)), axis(2, z))


_DETAIL_RE = re.compile(
    r"-Prefab: (\S+) Id: (\S+) Position: \(([-\d.eE+]+) ([-\d.eE+]+) "
    r"([-\d.eE+]+)\).*?Rotation: \(([-\d.eE+]+) ([-\d.eE+]+) ([-\d.eE+]+)\)")

# How far above and below the pad a seed object is looked for.  `findObjects
# -near` is a CUBE in all three axes (docs/manuals/build-harness-and-protocol.md
# 8.3, MEASURED 2026-09-16), so a leaf quartered down to r metres sees only
# +/- r of height; the listing steps its cube up and down until it has every
# object the y-blind `objects_count` cylinder holds, out to this span.
SEED_Y_SPAN_M = 64.0
# `findObjects` prints positions to about a centimetre (`Position: (347.1
# 31.57 -201.8)`, ledger seq 2014's read-back).
_PRINT_ROUND_M = 0.05


def _seed_listing(srv, prefab: str, x: float, y: float, z: float,
                  radius: float) -> tuple[dict, list, int]:
    """Every `prefab` in the XZ disc, listed `-detailed`: `({id: match},
    unlisted, queries)`.  `replay.bounded_detailed`'s bound -- at most
    `MAX_DETAILED_ROWS` per reply, quartering the disc until it holds --
    counted over the disc round the listing's whole CUBE, because the cube's
    corners answer too and the y-blind cylinder count bounds every row it can
    return; the cube is then stepped in height until the leaf has every
    object its own disc's count holds.  A leaf that cannot be completed is
    `unlisted`."""
    import replay  # noqa: PLC0415
    found: dict = {}
    unlisted: list = []
    queries = 0
    stack = [(x, z, radius)]
    while stack:
        cx, cz, r = stack.pop()
        cap, _ = srv.count(prefab, cx, cz, r * math.sqrt(2))
        if cap == 0:
            continue
        if cap > replay.MAX_DETAILED_ROWS:
            if r <= replay.MIN_DETAILED_RADIUS_M:
                unlisted.append({"centre": [cx, cz], "radius_m": r,
                                 "rows": cap})
                continue
            h = r / 2.0
            for dx, dz in ((-h, -h), (-h, h), (h, -h), (h, h)):
                stack.append((cx + dx, cz + dz, h * 1.4143))
            continue
        n, _ = srv.count(prefab, cx, cz, r)
        # The listing prints rounded positions; an object on the disc's edge
        # must not read as missing for the rounding of its own coordinates.
        edge = r + _PRINT_ROUND_M
        leaf: dict = {}
        k = 0
        while len(leaf) < n and 2 * k * r <= SEED_Y_SPAN_M:
            for yy in ((y,) if k == 0 else (y - 2 * k * r, y + 2 * k * r)):
                queries += 1
                reply = srv.command(
                    f"findObjects -prefab {prefab} -near {cx:.2f} {yy:.2f} "
                    f"{cz:.2f} {r:.2f} -detailed")
                for m in _DETAIL_RE.finditer(reply):
                    if m.group(1) == prefab and math.hypot(
                            float(m.group(3)) - cx,
                            float(m.group(5)) - cz) <= edge:
                        leaf[m.group(2)] = m
            k += 1
        if len(leaf) < n:
            unlisted.append({"centre": [cx, cz], "radius_m": r, "rows": n,
                             "listed": len(leaf)})
        found.update(leaf)
    return found, unlisted, queries


def seed_census(srv, cx: float, cz: float, y: float,
                reach: float) -> tuple[list, dict]:
    """The seed's own objects whose solid, as the gate books it, comes within
    `reach` of (cx, cz) -- the walk-in's reach of a site (`stock.walk_reach`:
    the site end's `site_reach`, or an installation's `stock.seed_reach`) --
    read off the LIVE world: `(solids, report)`.  `y` is the pad height the
    listing's height-stepped cubes are centred on.

    Names from `objects_count id=*` over 40 m tiles covering the reach plus
    the largest natural hull; each NATURAL prefab (`NATURAL_RE`, never a
    build piece) the gate has geometry for is then listed (`_seed_listing`)
    over the reach plus ITS OWN hull reach, and stood as `stock.gate_solid`
    at its own position and rotation.  A disc the listing could not complete
    is `report["unlisted"]`: unread is not clear."""
    import stock  # noqa: PLC0415
    I, A, _GP = stock._gate()
    geom, build_pieces = I.load_geometry()

    def hull_reach(parts) -> float:
        # 3-D: a tilted 24 m trunk leans its top metres sideways, so only
        # the distance from the pivot bounds the footprint at ANY rotation.
        s = I.Solid("", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), parts, True)
        return max((math.dist((0.0, 0.0, 0.0), v) for v in s.verts),
                   default=0.0)

    reaches = {p: hull_reach(parts) for p, parts in geom.items()
               if NATURAL_RE.match(p) and p not in build_pieces}
    outer = reach + max(reaches.values(), default=0.0)
    tile = _STAR_COUNT_MAX_M * math.sqrt(2)
    n = max(1, math.ceil(2 * outer / tile)) if outer > _STAR_COUNT_MAX_M else 1
    names: set[str] = set()
    for i in range(n):
        for j in range(n):
            tx = cx if n == 1 else cx - outer + tile * (i + 0.5)
            tz = cz if n == 1 else cz - outer + tile * (j + 0.5)
            names |= set(srv.count("*", tx, tz, min(outer,
                                                     _STAR_COUNT_MAX_M))[1])
    solids, per, unlisted, queries = [], {}, [], 0
    for p in sorted(names):
        if p not in reaches:
            continue
        found, miss, q = _seed_listing(srv, p, cx, y, cz, reach + reaches[p])
        queries += q
        unlisted += [dict(m, prefab=p) for m in miss]
        for m in found.values():
            px, py, pz, rx, ry, rz = (float(v) for v in m.groups()[2:])
            s = stock.gate_solid(p, px, py, pz, euler_quat(rx, ry, rz))
            if A.hull_distance(s, cx, cz) <= reach:
                solids.append(s)
                per[p] = per.get(p, 0) + 1
    return solids, {
        "reach_m": round(reach, 2), "read_m": round(outer, 2),
        "count_tiles": n * n, "names": sorted(names),
        "within_reach": per, "listing_queries": queries,
        "unlisted": unlisted,
        "not_in_geometry": sorted(p for p in names if NATURAL_RE.match(p)
                                  and p not in geom)}


# --- the driver ------------------------------------------------------------

def unit_ops(b, plan: dict, doc: dict, unit: dict, site: str, role: str,
             units: list[dict], loc: clearance.Locations,
             srv=None, validate_only: bool = False) -> list[dict]:
    """Every record for one build unit, in the proven order.

    With `validate_only` nothing is measured live and nothing is sent: the
    records are built and handed back for `schema.validate`. MEASURED by
    RoadNet: `LiveBuilder(dry=True)` APPENDS to the shared ledger and runs
    `check_expect` against the LIVE server, so it is not a dry run at all.
    """
    place = next(p for p in doc["placements"] if p["id"] == unit["id"])
    import area as clear_area
    ar = clear_area.clearing_area(place, apron=APRON_M)
    verdict = sample_verdict(loc, unit)
    ops = clearing_ops(doc, unit["id"])
    recs = [dict(o, requires={"mods": ["UpgradeWorld"], "prefabs": [],
                              "blobs": []},
                 meta={"settlement": site, "unit": unit["id"],
                       "location_check": verdict})
            for o in ops]

    if validate_only:
        stats, comps, op_y, patches = flatten_pad(place, unit["id"])
        probes = {z: {"cmd": f"objects_count id=_ZoneCtrl pos={c.centre[0]:g},"
                             f"{c.centre[1]:g} max=1", "count": 1}
                  for z, c in comps.items()}
        entries, wire, blobs = terrain_entries(b, unit, comps, op_y, patches,
                                               probes)
        recs.append(terrain_record(unit, site, entries, wire, blobs, stats,
                                   sample_verdict(loc, unit, comps)))
        plan_path, info = spawn_plan_file(unit, unit["target_y"],
                                          SCRATCH / site)
        # THE SAME QUESTION ON THE OFFLINE PATH, so a unit can be checked
        # against the roads already on the ground without sending anything.
        # Printed rather than raised here: `validate_only` builds records for
        # `schema.validate` and its job is to report, but a caller that sees
        # `in_a_road` here will see `apply_unit_body` REFUSE.
        road = road_conflict(unit, plan_piece_xz(plan_path))
        if road["verdict"] != "clear":
            print(f"  {unit['id']} road_conflict {road['verdict']}: "
                  f"{road['piece_conflicts']}")
        recs.append(spawn_plan_record(b, unit, site, role, plan_path, info,
                                      units, ar.radius_m))
        return recs
    return recs


# ---------------------------------------------------------------------------
# THE PAD, ON ITS OWN
# ---------------------------------------------------------------------------
# TWO HOLES MET HERE, and until they did, seventeen refusals on this world
# were "closable" and stayed open all night.
#
#   A SPEC WITH NO EMITTER.  `accept/instruments.py::regrade_census` answers
#   "would levelling this rectangle to this datum BURY or UNDERMINE a piece
#   that is already standing", over the samples that actually MOVE, through
#   the same `Columns.span` and `piece_columns` the grounding check uses.  It
#   is proven in five directions by `accept/sections/structures.py` and, until
#   this function existed, IT HAD NO CALLER OUTSIDE THAT SELFTEST.  The gate
#   is a read by contract, so the one thing that can decide a regrade lived
#   where nothing may write.
#
#   AN EMITTER WITH NO ENTRY POINT.  The ledgered `terrain_write role=site_pad`
#   emitter is the block that used to sit inline in `apply_unit` - flatten_pad
#   -> ungenerated_zones -> zone_probe -> union_prior -> terrain_entries ->
#   delta_gate -> terrain_record -> emit.  It had no name and no `op`:
#   `main()` offered placements/plan/validate/apply/portals/verify/selftest,
#   and `apply` also runs `objects_remove` (THE irreversible op) and re-emits
#   `spawn_plan` for the whole body.  So re-levelling the ground under a
#   STANDING building meant clearing it and re-placing its 216 pieces, which
#   nobody was going to do, so the pad stayed wrong.
#
# The two are joined here: one named pad emitter, gated by the census that
# decides whether a regrade is safe, reachable as `build.py pad --only <ids>`
# without touching a piece.  `apply_unit_ground` calls THIS, so there is one pad
# emitter and not two.
def _world_src() -> Path:
    """Where THIS world's save is, for the instruments that read it back.

    REFUSES rather than guesses.  `accept/instruments.py` carries a hardcoded
    Ulfsland path as a DEFAULT, and a default silently reused on another world
    is the join-of-two-worlds defect the gate itself had to close: one world's
    ledger over another world's ground, with nothing downstream able to tell.
    So the path is either declared through the environment or it is this
    world's own default, and `require_world_save` re-reads the name off the
    directory either way.
    """
    import accept.instruments as AI                # noqa: PLC0415
    # `AI.world_save`: `$JUMPSTART_WORLD_SRC`, else THIS world's own save
    # under the install root, resolved at call time and REFUSED by
    # `require_world_save` unless the directory is that world's.  It was
    # `AI.WORLD_SRC_DEFAULT` -- Ulfsland's save -- whenever WORLD was Ulfsland.
    src = AI.world_save(WORLD)
    # The save is flushed by the SERVER this run drives (`_save_census`'s
    # `save`) and then read off disk here, so the two must be the same world:
    # the name check alone passes for a same-named world on another install.
    AI.require_save_is_the_ledgers(src, WORLD)
    return src


def _save_census(b):
    """The whole-save ZDO census, off a FROZEN COPY, with its fingerprint.

    THE SAVE IS FLUSHED FIRST.  The census answers "what is standing HERE
    NOW", and the dedicated server holds placements in memory between its own
    periodic writes, so a census taken off an unflushed save is a reading of
    some earlier world.  `save` is a flush, not a mutation, which is why it is
    issued and not recorded.

    The copy is content-hashed and cached exactly as the acceptance gate's
    `Ctx` does it, so a second question in the same session costs nothing and
    CANNOT be answered off a world that has moved in between.
    """
    import accept.instruments as AI                # noqa: PLC0415
    b.srv.command("save")
    src = _world_src()
    fp = AI.save_fingerprint(src)
    cache = Path(AI.CACHE_DEFAULT) / WORLD
    (cache / fp).mkdir(parents=True, exist_ok=True)
    snap = AI.snapshot(src, cache / fp / "world")
    args = SimpleNamespace(vh_src=AI.VH_SRC_DEFAULT, world_src=str(snap),
                           world=WORLD, sandbox=AI.SANDBOX_DEFAULT)
    return AI.census(cache, fp, args), fp


def pad_repairs_nothing(rg: dict) -> bool:
    """Does this regrade census describe a write that would change NO height?

    Named, and separate from `regrade_gate`, because it is a DIFFERENT
    QUESTION asked of the same measurement.  `regrade_gate` asks "would this
    BREAK something" and over an empty moving-set its honest answer is CLEAR.
    This asks "would it FIX anything", and over the same empty set the answer
    is no.  A gate that only ever asks the first question will wave through a
    write that is pure risk -- the shape `CloseFive` found in `clean_order`,
    where `clean: true` was satisfied by a write that repaired nothing.

    `moving` is the count of lattice samples whose ENGINE ground differs from
    the pad's own target_y by more than `REGRADE_MOVE_M`.  Zero of them means
    the ground is already, to the instrument's own tolerance, the number the
    write would set it to.
    """
    return not rg.get("moving")


def regrade_gate(b, unit: dict, surface_at=None, census=None) -> dict:
    """Would re-levelling this pad to its own target_y bury or undermine
    something that is already standing on it?

    THE REFERENCE IS NOT THE ARTEFACT UNDER TEST, AND THAT COST ONE REWRITE.
    `ground` is the APPLIED BED READ BACK THROUGH THE ENGINE off a frozen
    copy of the save (`accept/instruments.py::ground` -> run_waterproof.sh
    FROM_SAVE=1 -> the save's own _TerrainCompiler ZDOs instantiated, then
    Heightmap.GetHeight), and `solids` are the save's own ZDO census through
    the same instruments.  Never the plan, never the record's `target_y`.

    IT WAS `roads/applied.py::Applied.surface_at` FOR AN HOUR, AND THAT IS
    THE LEDGER'S OWN BLOBS - i.e. the artefact under test.  MEASURED on
    Ulfsland b608a61b, driving both: over pad `wt-south`'s 441-sample
    rectangle the ledger composition reports 0 SAMPLES MOVING - every vertex
    already at target_y 82.660 - while the engine reads a spread of 1.454 m
    over 438 of them.  The two disagree for the exact reason this repair
    exists: the z = 32.0 row is row 0 of zones (8,1)/(9,1), which the pad
    wrote, AND row 64 of (8,0)/(9,0), which nothing wrote, and the engine
    answers from the unwritten one.  A regrade census taken off the ledger is
    blind to precisely the defect it is being asked about, and would have
    called a pad with 21 off-target samples "nothing to do".

    WHY IT IS NOT `delta_gate`.  That gate measures the hazard for GENERATED
    content: every location instance within 60 m, over its own reach disc.  A
    building WE planted is in neither population, so a re-emit that fills a
    pad edge 1.589 m and puts the ground over a standing wood_stair passes it
    cleanly.  Two populations, two gates, one reason each.
    `surface_at` and `census` exist so the SELFTEST drives THIS function
    rather than a copy of it: `surface_at(x, z) -> applied_y | None` and
    `census() -> (Census, fingerprint)`.  Left at None they are the live
    instruments, which is the only way the shipping path can reach them.
    """
    import accept.instruments as AI            # noqa: PLC0415
    import accept.sections.structures as AS    # noqa: PLC0415

    target = float(unit["target_y"])
    cen, fp = _save_census(b) if census is None else census()
    # ONE LABEL IS ONE QUESTION.  The points are built by the SAME `gpoint`
    # whose label `regrade_census` will look the ground up by, so a query and
    # its key cannot disagree; and they are snapped, because
    # `Heightmap.GetHeight` quantises to the 1 m vertex and a pad edge on the
    # half-lattice line is an ambiguous question.
    pts, seen = [], set()
    for (x, z) in pad_lattice(unit):
        k, sx, sz = AS.gpoint(x, z)
        if k in seen:
            continue
        seen.add(k)
        pts.append((k, sx, sz))
    if surface_at is None:
        cache = Path(AI.CACHE_DEFAULT) / WORLD
        args = SimpleNamespace(vh_src=AI.VH_SRC_DEFAULT,
                               world_src=str(cache / fp / "world"),
                               world=WORLD, sandbox=AI.SANDBOX_DEFAULT,
                               zone_batch=110)
        # The ground cache is keyed on the TERRAIN EPOCH, not on `fp`: a bed
        # height is a function of the terrain alone, so this read shares its
        # directory with every other save of the same terrain state.
        epoch = AI.terrain_epoch(
            AI.JUMPSTART / "ledger" / "runs" / WORLD / "ledger.jsonl",
            save_fp=fp, world_src=cache / fp / "world")
        read = AI.ground(cache, epoch, pts, args)

        def surface_at(x, z, _r=read):
            return _r.get(AS.gkey(x, z))
    rect, ground, unread = [], {}, []
    for (k, sx, sz) in pts:
        got = surface_at(sx, sz)
        if got is None:
            unread.append([sx, sz])
            continue
        rect.append((sx, sz))
        ground[k] = got
    # AN UNREAD SAMPLE IS NOT A CLEAN ONE.  A rectangle judged over the
    # samples that happened to come back is the population-too-small defect
    # this project keeps producing, so a vertex the instrument did not answer
    # for stops the census instead of shrinking it.
    if unread:
        return {"verdict": "UNREAD", "unread": unread[:8],
                "unread_count": len(unread),
                "rect_samples": len(rect) + len(unread),
                "save_fingerprint": fp,
                "why": ("the applied-bed instrument returned no reading for "
                        "these vertices of the pad's own rectangle, so the "
                        "regrade census cannot be taken over the whole of it")}

    geom, build_pieces = AI.load_geometry()
    xs = [p[0] for p in rect]
    zs = [p[1] for p in rect]
    x0, x1 = min(xs) - 8.0, max(xs) + 8.0
    z0, z1 = min(zs) - 8.0, max(zs) + 8.0
    solids = [s for s in AI.solids_from_census(cen, geom)
              if s.name in build_pieces and s.verts
              and s.xmax >= x0 and s.xmin <= x1
              and s.zmax >= z0 and s.zmin <= z1]
    out = AI.regrade_census(rect, target, ground, solids, geom,
                            AS.Columns(geom), AS.gkey, AS.piece_columns)
    out["verdict"] = "clear" if out["verdict"] == "CLEAR" else out["verdict"]
    out["build_pieces_in_window"] = len(solids)
    out["objects_in_save"] = len(cen.rows)
    out["save_fingerprint"] = fp
    out["tool"] = ("accept/instruments.py::regrade_census over "
                   "roads/applied.py::Applied.surface_at and the save's own "
                   "ZDO census")
    return out


def emit_pad(b, place: dict, unit: dict, site: str,
             loc: clearance.Locations, regrade: bool = True) -> dict:
    """THE pad emitter: one `terrain_write role=site_pad`, nothing else.

    Called by `apply_unit_ground` in its own sequence, and by `build.py pad` on a
    building that is ALREADY STANDING - which is the case that had no path.
    `regrade` is the difference between the two and it is not a knob: on a
    fresh pad the ground carries nothing of ours, so `regrade_census` has an
    empty population and says so; on a standing one it is the gate.
    """
    out: dict = {"seqs": {}}
    stats, comps, op_y, patches = flatten_pad(place, unit["id"])
    missing = FL.ungenerated_zones(b.srv.rc, sorted(comps))
    if missing:
        raise SystemExit(
            f"REFUSED {unit['id']}: zones {missing} hold no _ZoneCtrl, so they "
            f"were never generated. Writing terrain there plants the zone's "
            f"whole vegetation set on the UNMODIFIED generated height -- "
            f"MEASURED to 7.68 m of floating grass. Step 0 did not cover the "
            f"flatten's zone set.")
    # CAN THIS WORLD'S GROUND REACH THE DATUM AT ALL?  See
    # PAD_CLAMP_DEPENDS_ON.  Before anything else that costs a round trip,
    # because this one is already in hand: `flatten_pad` measured it against
    # THIS seed's generated heights and the answer has been written into the
    # ledger, unrefused, 45 times.
    cv = tcdata.clamp_verdict(stats)
    out["clamp"] = cv
    if cv["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: {cv['over_clamp_samples']} of this pad's "
            f"samples need more than TerrainComp's +/-{cv['clamp_m']:g} m "
            f"apply clamp to reach target_y {unit['target_y']} "
            f"(max_fill {cv['max_fill_m']} m, max_cut {cv['max_cut_m']} m). "
            f"The engine clamps the delta, the ground stops short and the "
            f"body stands on a datum the terrain never reached -- that is "
            f"wt-peak's floating watchtower. MEASURED: Ulfsland's 73 pad "
            f"writes have over_clamp 0; 43 of Fresh42's 45 do not, because "
            f"they carry another world's target_y. RE-SOLVE THE PAD against "
            f"this seed's own ground; do not widen the clamp and do not "
            f"delete the building.")
    # DOES THE PAD STAND UNDER THE WHOLE BODY?  See PAD_EXTENT_DEPENDS_ON in
    # terraform/flatten.py.  Offline and already in hand, like the clamp: the
    # body at the yaw it will be emitted at, against the pad `flatten.build`
    # just stamped.  Nothing downstream can see this - the gate's pad checks
    # are populated by the pad's rectangle and the pad's writes, never by the
    # ground under the body - and three standing Ulfsland bodies prove it.
    cover = FL.placed_body_cover(place, APRON_M)
    out["body_cover"] = cover
    if cover["verdict"] != "covered":
        raise SystemExit(
            f"REFUSED {unit['id']}: body cover {cover['verdict']} - "
            + (f"{cover['unstamped']} of the {cover['vertices_under_body']} "
               f"lattice vertices under its body at yaw {cover['yaw']} "
               f"({cover['body_at_yaw'][0]} x {cover['body_at_yaw'][1]} m) "
               f"are outside the {cover['pad'][0]} x {cover['pad'][1]} m pad, "
               f"overhang x {cover['overhang_x_m']} z {cover['overhang_z_m']} m"
               if "unstamped" in cover else cover.get("why", ""))
            + ". The pad comes from `unit_records` -> `written_pad`; a pad that "
              "does not cover its body was not derived there. Do not delete "
              "the building.")
    # IS A BODY ALREADY STANDING ON THE GROUND THIS WRITE MOVES?  See
    # GROUND_FREEZE_ACROSS_INVOCATIONS_DEPENDS_ON.  Only on the FRESH-pad
    # path: `regrade=True` is `build.py pad`, whose whole purpose is to
    # re-level under a standing body, and its guard is the regrade census --
    # footprint-granular, strictly sharper than this zone-granular one.
    if not regrade:
        occ, unread = occupied_zones(b.led.records())
        fz = frozen_ground(sorted(comps), occ, unread)
        out["ground_freeze"] = fz
        if fz["verdict"] != "clear":
            raise SystemExit(
                f"REFUSED {unit['id']}: this pad moves ground in zone(s) "
                f"{fz['shared'] or fz['zones']} where a piece has stood since "
                f"seq {fz['held_since_seq']} "
                f"(unreadable_piece={fz['unreadable_piece']}). A settlement's "
                f"ground is finished before ANY of its bodies stand, and that "
                f"holds across invocations, not merely within one: `--from` / "
                f"`--limit` / `--only` hand this process a subset while the "
                f"rest of the town is already up. MEASURED on Fresh42: 18 "
                f"site_pad writes landed in a zone held by another unit of "
                f"their OWN settlement. Build this unit's ground in the same "
                f"pass as the bodies it shares a zone with, or re-level it "
                f"through `build.py pad`, which carries the regrade census.")
    if regrade:
        rg = regrade_gate(b, unit)
        out["regrade_census"] = {k: v for k, v in rg.items() if k != "tool"}
        b.observe(f"regrade census for {unit['id']}",
                  "MEASURED: every build-piece solid whose own rotated "
                  "footprint covers a lattice sample THIS WRITE MOVES, "
                  "judged by the sign of the move against its own span - a "
                  "fill that reaches a piece's top BURIES it, a cut that "
                  "leaves its foot more than one walker step in the air "
                  "UNDERMINES it. This is the population `delta_gate` does "
                  "not have: that one measures GENERATED content only.",
                  rg.get("tool", ""), out["regrade_census"],
                  role="site_pad", site_id=unit["id"])
        if rg["verdict"] != "clear":
            raise SystemExit(
                f"REFUSED {unit['id']}: re-levelling this pad to "
                f"{unit['target_y']} is {rg['verdict']} under the regrade "
                f"census -- buries={rg.get('buries')} "
                f"undermines={rg.get('undermines')} "
                f"unread={rg.get('unread')}. ParBuried retired 385 pieces for "
                f"exactly the first of those and a fill puts them back; "
                f"NEVER delete the building. Re-solve the pad's target_y, or "
                f"retire and re-spawn the piece the ground has come up over.")
        # A WRITE THAT MOVES NO GROUND CANNOT CLOSE ANYTHING, SO IT IS ALL
        # RISK AND NO REPAIR.  `regrade_gate` answers "would re-levelling
        # BREAK something", and over an empty moving-set the honest answer is
        # CLEAR -- which is exactly the shape CloseFive found in
        # `clean_order`: a gate that accepts a write that repairs NOTHING.
        # Here it is the same hole in the pad path.
        #
        # MEASURED on Ulfsland, and it is why this exists: `sudrberg-keep`
        # carried a `buried` refusal (woodiron_beam_45 0.495 m under its own
        # top) that looked identical to five pad refusals this emitter DID
        # close, and its regrade census came back CLEAR.  But the engine
        # reads exactly 121.840000 -- the pad's own target_y -- at ALL 2025
        # of its lattice samples, so 0 move.  The beam is below the pad's own
        # DATUM; the ground is not the defect and re-levelling it to the same
        # number repairs nothing.  Writing it would have re-spawned the
        # compilers of four zones under a 3,532-piece keep to change no
        # height at all.
        #
        # Only on the STANDING-pad path (`regrade=True`).  A fresh pad in
        # `apply_unit_ground` is levelling ground nobody has written, where a zero
        # moving-set would mean the terrain already matches the plan.
        if pad_repairs_nothing(rg):
            raise SystemExit(
                f"REFUSED {unit['id']}: the engine reads this pad's own "
                f"target_y {unit['target_y']} at every one of its "
                f"{rg.get('rect_samples')} lattice samples -- 0 MOVE. A "
                f"re-emit would rewrite {len(comps)} zone compiler(s) and "
                f"change no height, so it cannot close a single refusal. If "
                f"this pad is still refused, the defect is the PIECE's "
                f"placement below the pad datum, not the ground: retire and "
                f"re-spawn it, or re-solve target_y. Do not re-level ground "
                f"that is already at the number you would write.")
    probes = {z: zone_probe(b.srv, *z) for z in sorted(comps)}
    # MERGE BEFORE WRITING, because a zone holds one compiler and 18 Stenvik
    # pads share 8 zones. The second pad in a zone would otherwise erase the
    # first one's levelling -- and the ledger's clobber guard refuses exactly
    # that, which is how this was caught rather than discovered by the
    # operator standing on a re-ungraded pad.
    merged = union_prior(b, comps, op_y)
    if merged:
        out["merged"] = {f"{z[0]},{z[1]}": {
            "merged_from": m["merged_from"],
            "merged_from_file_lines_1based": m["merged_from_file_lines_1based"],
            "merged_from_record_index_0based":
                m["merged_from_record_index_0based"],
            "merged_from_actors": m["merged_from_actors"],
            "samples_inherited": m["samples_inherited"],
            "compiler_present": zone_compiler(b.srv, *z)} for z, m in
            merged.items()}
        b.observe(f"compiler union for {unit['id']}",
                  "MEASURED: earlier per-zone TCData blobs re-read from the "
                  "ledger's blob store and unioned per SAMPLE INDEX, ours "
                  "winning only where we wrote. `compiler_present` is a live "
                  "`objects_count _TerrainCompiler` at the zone centre, "
                  "because a record is appended BEFORE it is sent and an "
                  "append whose send failed leaves a claim with no compiler "
                  "behind it -- which is the case for this build's own first "
                  "attempt at stenvik-hall-1.",
                  "tools/jumpstart/settlements/build.py::union_prior",
                  out["merged"], role="site_pad", site_id=unit["id"])
    entries, wire, blobs = terrain_entries(b, unit, comps, op_y, patches,
                                           probes, merged)
    write_gate = delta_gate(loc, comps, pad_lattice(unit))
    write_verdict = sample_verdict(loc, unit, comps, write_gate)
    if write_verdict["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: the terrain write is not clear under the "
            f"non-destructive budget (reach + half-width + the derived 1 m "
            f"terrain spread) or its delta gate: "
            f"{write_verdict['violations'][:3]} delta={write_gate}")
    out["delta_gate"] = {k: v for k, v in write_gate.items()
                         if k != "method"}
    rec = terrain_record(unit, site, entries, wire, blobs, stats,
                         write_verdict)
    res = b.emit("terrain_write", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["terrain_write"] = res["seq"]
    out["zones"] = [e["zone"] for e in entries]
    out["earthwork"] = {"max_cut_m": stats["max_cut"],
                        "max_fill_m": stats["max_fill"],
                        "samples": stats["samples"],
                        "over_clamp": stats["over_clamp"]}
    return out


# ---------------------------------------------------------------------------
# SETTLEMENT_ORDER_DEPENDS_ON: the ground a write moves and the ground a piece
# stands on -- NOT the order the units happen to be listed in.
#
# THIS FUNCTION USED TO BE ONE PASS PER UNIT: generate -> clear -> flatten ->
# PLACE THE BODY, then the next unit's generate -> ... -> flatten.  So every
# unit after the first levelled ground while the previous unit's body was
# already standing.  MEASURED 2026-09-24 on Fresh42's ledger -- the FIRST
# correctly-ordered build step 5 has ever had on a fresh seed -- 42
# `terrain_write role=site_pad` records were appended after the first piece
# stood (first_piece_seq 1479), and EIGHTEEN of those 42 wrote into a 64 m
# zone that already held a standing body.  Eighteen, not forty-two, is the
# number that matters and it is not zero: buildstage's step 5 declares
# "EVERY TERRAIN WRITE PRECEDES EVERY PIECE" and eighteen writes moved ground
# in an occupied zone.
#
# The remedy is the one the declaration already implies: A SETTLEMENT'S
# GROUND IS FINISHED BEFORE ANY OF ITS BODIES STAND.  `main` now runs the
# ground pass over every unit, then the body pass over the units that
# survived it, so the last pad write precedes the first piece for the whole
# settlement rather than only for the first building.
# ZONE_REPAIR_DEPENDS_ON: A ZONE UPGRADE WORLD CANNOT GENERATE IS RE-ASKED
# ONCE, AND FORFEITED ONLY WHEN IT HOLDS A REGISTRATION.  `zones_generate`
# can leave a zone without its `_ZoneCtrl` when `SpawnZone` returns false for
# it: IL_0030 (terrain not ready, a RACE cured by asking again) or IL_003c (an
# unplaced location whose type never loads, permanent -- see
# `roads/north.py::zonediag`).  MEASURED on the Ulfsland regeneration:
# hognest-castle's ground generate refused, `zone_ctrl want 4 got 3`, zone
# (9,22) ungenerated, in BOTH concurrent builds and again 105 minutes later;
# the server's own lines were "1 zones generated. 1 errors." and then "0 zones
# generated. 1 errors.", and the dump registers a MountainCave02 at the zone
# centre -- the sudrberg-keep precedent (live ledger seq 745/746), repaired by
# hand there with `zones_generate ... empty` then `zones_restore`.  A from-
# scratch regeneration cannot wait for that hand.
#
# So `generate_zones` repairs the ONE failure it can name, in the documented
# order, every step a ledger record carrying its evidence:
#   1. the ordinary bracket (as before);
#   2. if exactly the `zone_ctrl` check failed: ONE scoped re-issue per
#      ungenerated zone (`zones_generate pos=<centre> max=1`), the race cure;
#   3. if the zone is STILL ungenerated and the location dump registers a
#      location in it: `zones_generate ... empty` (marks it generated, spawns
#      nothing, deletes nothing, FORFEITS the zone's ungenerated content --
#      declared in `meta.forfeits`) and `zones_restore` (puts its one
#      `_ZoneCtrl` there).
# A zone that fails with NO registration to explain it, or a failure of any
# other check, is refused exactly as before -- an unexplained fault is not
# repaired on a hunch.  NEUTERS: `none` is the shipped behaviour (the unit
# refuses); `blind` forfeits a zone with no registration.  Proven by
# `_ZONE_CASES`.
ZONE_REPAIR_DEPENDS_ON = ("a_scoped_reissue_before_a_forfeit",
                          "only_a_zone_holding_a_registration_is_forfeited")
_ZONE_REPAIR_MODE = "repair"        # repair | none | blind


def zone_registrations(zone: tuple[int, int],
                       dump_path: str | None = None) -> list[dict]:
    """Every location the mod-free dump registers inside `zone`."""
    dump = json.loads(Path(dump_path or locations_dump()).read_text())
    return [L for L in dump["locations"]
            if tcdata.zone_of(L["x"], L["z"]) == (int(zone[0]), int(zone[1]))]


def _ungenerated_after(exc: BaseException) -> list[tuple[int, int]] | None:
    """The zones a failed `zones_generate` left without a `_ZoneCtrl`, when
    that is the ONLY thing that failed; None for any other failure."""
    failed = [c for c in (getattr(exc, "checks", None) or []) if not c["ok"]]
    if len(failed) != 1 or failed[0].get("check") != "zone_ctrl":
        return None
    return [(int(z[0]), int(z[1])) for z in failed[0].get("ungenerated") or []]


def generate_zones(b, gen: dict, unit_id: str, site: str, siting: dict,
                   dump_path: str | None = None) -> dict:
    """`gen` is the unit's `zones_generate` op; returns `{"seq": ...}` of the
    record that left its zones generated.  See ZONE_REPAIR_DEPENDS_ON."""
    requires = {"mods": ["UpgradeWorld"], "prefabs": [], "blobs": []}
    try:
        return b.emit(gen["op"], params=gen["params"], wire=gen["wire"],
                      requires=requires, expect=gen["expect"],
                      meta={"settlement": site, "unit": unit_id,
                            "location_check": siting})
    except RuntimeError as exc:
        zones = _ungenerated_after(exc)
        if not zones or _ZONE_REPAIR_MODE == "none":
            raise
        first = exc
    last: dict = {}
    for zone in zones:
        cx, cz = (float(v) for v in tcdata.zone_centre(*zone))
        where = f"pos={cx:g},{cz:g}"
        base = {"pos": [cx, cz], "max_m": 1.0, "zones": [list(zone)],
                "role": "site_pad", "site_id": unit_id}
        try:
            last = b.emit(
                "zones_generate", params=dict(base),
                wire=[f"zones_generate {where} max=1"], requires=requires,
                expect={"zone_ctrl": 1},
                meta={"settlement": site, "unit": unit_id,
                      "why": ("ONE scoped re-issue for a zone the site-wide "
                              "bracket left without a _ZoneCtrl: the cure for "
                              "SpawnZone's terrain-not-ready race, asked "
                              "before anything is forfeited")})
            continue
        except RuntimeError:
            pass
        regs = zone_registrations(zone, dump_path)
        if not regs and _ZONE_REPAIR_MODE != "blind":
            raise first
        prefabs = sorted({L["prefab"] for L in regs}) or ["(none)"]
        was = re.match(r"seq \d+", str(first))
        fault = (f"MEASURED: zone {tuple(zone)} was left without a _ZoneCtrl "
                 f"by the site-wide `zones_generate` "
                 f"({was.group(0) if was else 'record unknown'}) and by a "
                 f"scoped re-issue at its centre, "
                 f"two Upgrade World brackets in which SpawnZone refused it; "
                 f"the location dump registers {prefabs} in this zone, whose "
                 f"unplaced instance SpawnZone refuses when its type never "
                 f"loads (roads/north.py::zonediag IL_003c). Ordinary "
                 f"generation cannot generate it and `zones_restore` cannot "
                 f"see an ungenerated zone, so it is marked generated "
                 f"(nothing spawned, nothing deleted) and then given its "
                 f"control object, as at sudrberg-keep (live ledger seq "
                 f"745/746).")[:1500]
        last = b.emit(
            "zones_generate", params=dict(base, empty=True, fault=fault),
            wire=[f"zones_generate {where} max=1 empty"], requires=requires,
            expect={"zone_marked_generated": {"selected": 0}},
            meta={"settlement": site, "unit": unit_id,
                  "forfeits": (f"zone {tuple(zone)}'s vegetation and its "
                               f"unplaceable {prefabs} registration, "
                               f"permanently"),
                  "why": ("the zone cannot be spawned, so it is marked "
                          "generated instead; nothing is deleted and "
                          "nothing is spawned")})
        last = b.emit(
            "zones_restore", params=dict(base, fault=fault),
            wire=[f"zones_restore {where} max=1"], requires=requires,
            expect={"zone_ctrl_exact": {"count": 1, "probe_radius_m": 15.0}},
            meta={"settlement": site, "unit": unit_id,
                  "why": ("the empty mark leaves the zone generated with no "
                          "control object; this puts exactly one there")})
    return last


def apply_unit_ground(b, doc: dict, unit: dict, site: str,
                      units: list[dict], loc: clearance.Locations):
    """Generate -> clear -> live marker check -> flatten.  NO PIECE STANDS.

    Returns `(out, ar)`: the per-unit report so far, and the clearing area,
    which `apply_unit_body` needs at FULL precision -- `out["clear_radius_m"]`
    is rounded for the reader and is not the same number.
    """
    place = next(p for p in doc["placements"] if p["id"] == unit["id"])
    # CLEARING'S OWN REFUSAL IS A UNIT REFUSAL, like every gate below it.
    # `main` records a `SystemExit` and continues with the next unit; a
    # `ClearAreaRefused` escaped it as a traceback.  MEASURED on Fresh42 drive
    # 2, both passes: vestvik-villa-1's refusal killed the whole ground pass,
    # so villa-2, villa-3, stonehouse-1, stonehouse-2, hut-1 and hut-2 were
    # never asked, NO vestvik body stood, vestvik's portal pair was never
    # attempted, and the resume re-wrote the pads the first pass had already
    # levelled (terrain_write 2035 and 2042 over 1603 and 1609).
    #
    # `clearing_gate` is the function `settlements/plan.py` sited this pad
    # by: the same cylinder and room, over BUILD_ROOM_DEPENDS_ON here and the
    # full population there; the full room is recorded below.
    gate = clearing_gate(loc, place)
    if gate["ar"] is None:
        raise SystemExit(f"REFUSED {unit['id']}: {gate['why']}")
    ar, room = gate["ar"], gate["room"]
    full = clear_radius_room(loc, ar.centre_x, ar.centre_z,
                             clearance.CLEARING_POPULATION_DEPENDS_ON)
    half_diag = math.hypot(unit["pad_w"], unit["pad_d"]) / 2
    # THREE QUESTIONS, THREE OPERATIONS, and the whole point of Main's ruling
    # is that they are not interchangeable:
    #   siting      -- should we CHOOSE to build here (destructive budget,
    #                  MARGIN_M + PROTECTED_EXTRA_M, both taste). Recorded,
    #                  not gating: the sites were already solved under it.
    #   clearing    -- can `objects_remove` DELETE a POI object (piece-reach
    #                  room, `clearing_gate`; then the exact live census).
    #                  HARD GATE; it is the irreversible op.
    #   terrain     -- can the write leave a POI piece floating (reach +
    #                  caller half-width + derived terrain spread, plus the
    #                  mandatory delta gate). HARD GATE, below.
    disc = location_check(loc, unit["x"], unit["z"], half_diag)
    siting = sample_verdict(loc, unit)
    if gate["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: {gate['why']}. `objects_remove id=* "
            f"ignore=_*` is the only irreversible op in this build and a "
            f"deleted POI is the damage class this project recorded as "
            f"unrepairable. Not clipped: re-solve the pad or shrink the body.")
    out: dict = {"unit": unit["id"], "seqs": {},
                 "clear_radius_m": round(ar.radius_m, 2),
                 "clear_radius_room_m": room["max_radius_m"],
                 "clear_radius_binding": room["binding"],
                 # THE SITING'S population (pieces + interior_zone), recorded
                 # not gating: the live cylinder census below is exact.
                 "clear_radius_room_siting": {
                     "max_radius_m": full["max_radius_m"],
                     "binding": full["binding"],
                     "depends_on": full["depends_on"]},
                 "siting_budget": {
                     "disc_verdict": disc["verdict"],
                     "sample_verdict": siting["verdict"],
                     "violations": siting["violations"][:3],
                     "note": ("the DESTRUCTIVE budget (MARGIN_M 12 + "
                              "PROTECTED_EXTRA_M 90, both taste) reported "
                              "unsuppressed even where it does not gate this "
                              "operation -- a budget this op is not bound by "
                              "should still be visible to whoever reads the "
                              "record")}}

    ops = clearing_ops(doc, unit["id"])
    gen, clear = ops[0], ops[1]
    res = generate_zones(b, gen, unit["id"], site, siting)
    out["seqs"]["zones_generate"] = res["seq"]

    # THE MOD-FREE LIMIT, closed. The dump cannot see More_World_Locations'
    # POIs -- MEASURED, three of four live markers in GroundTruth's test zones
    # were invisible to it -- so the LIVE marker positions are read now that
    # the zone is generated, and typed against the dump's per-type radii.
    hits, report = FL.pad_location_check(b.srv.rc, place, APRON_M,
                                        Path(locations_dump()))
    b.observe(f"live location markers around {unit['id']}",
              "MEASURED over RCON with `findObjects -prefab LocationProxy` "
              "after zones_generate: live positions for POSITION, the "
              "mod-free dump for per-type RADIUS. This is the check the "
              "dump alone cannot make.",
              "tools/jumpstart/terraform/locations.py via flatten."
              "pad_location_check",
              {"hits": len(hits), "report": report[:1200]},
              role="site_pad", site_id=unit["id"])
    # THE MARKER SWEEP IS A PROXIMITY MEASUREMENT, NOT THE HAZARD. For a
    # marker the mod-free dump cannot identify it can only assume a worst
    # case, and MEASURED on this very pad that worst case (58 m = 32 radius +
    # 11 INFERRED overshoot + 15 taste margin) refused Stenvik's hall on a
    # marker 23.9 m away whose pieces are nowhere near the pad. So when the
    # sweep reports hits, the question is escalated rather than answered by a
    # radius: ask the world WHAT IS IN THE CYLINDER that is about to be
    # emptied. Only a non-natural prefab inside it is a stop.
    contents = cylinder_contents(b.srv, ar)
    b.observe(f"contents of the clearing cylinder for {unit['id']}",
              contents["method"], contents["tool"],
              {"total": contents["total"], "clearable": contents["clearable"],
               "not_clearable": contents["not_clearable"],
               "probe": contents["probe"],
               "live_markers_within_standoff": len(hits)},
              role="clearing", site_id=unit["id"])
    if contents["verdict"] != "clear":
        raise SystemExit(
            f"REFUSED {unit['id']}: the clearing cylinder holds "
            f"{contents['not_clearable']}, which are not in the measured "
            f"natural set. `objects_remove` would delete them and a deleted "
            f"location piece is the one damage class this project has "
            f"recorded as unrepairable.\nlive marker sweep:\n{report}")
    out["cylinder"] = {k: contents[k] for k in
                       ("total", "clearable", "not_clearable", "verdict")}
    out["live_markers_within_standoff"] = len(hits)

    # NOTHING TO REMOVE IS A REAL ANSWER, and it has to be handled rather
    # than sent: `objects_remove id=` with an empty id list is the malformed
    # command that answers "Error: Missing ids." and removes nothing
    # silently. An empty cylinder happens on a resume -- the pad was cleared
    # on a previous attempt -- and on a pad that generated bare.
    if not contents["clearable"]:
        b.observe(f"clearing skipped for {unit['id']}",
                  "MEASURED: the cylinder holds no clearable object, so there "
                  "is nothing for `objects_remove` to do. Sending it with an "
                  "empty id list would answer `Error: Missing ids.` and "
                  "remove nothing, which is a silent no-op dressed as a step.",
                  "tools/jumpstart/settlements/build.py::cylinder_contents",
                  {"total": contents["total"], "probe": contents["probe"]},
                  role="clearing", site_id=unit["id"])
        out["seqs"]["objects_clear"] = None
    else:
        clear = scoped_clear(ar, unit["id"], contents)
        res = b.emit(clear["op"], params=clear["params"], wire=clear["wire"],
                     requires={"mods": ["UpgradeWorld"], "prefabs": [],
                               "blobs": []},
                     expect=clear["expect"],
                     meta={"settlement": site, "unit": unit["id"],
                           "clear_radius_m": round(ar.radius_m, 2),
                           "piece_reach_gate": room,
                           "room_m": round(room["max_radius_m"]
                                           - ar.radius_m, 2),
                           "cylinder": {k: contents[k] for k in
                                        ("total", "clearable",
                                         "not_clearable")},
                           "live_marker_sweep": {
                               "hits": len(hits),
                               "probe": "findObjects -prefab LocationProxy, "
                                        "bounded",
                               "report": report[:800]},
                           "policy": ("REFUSE, never clip: a town pad needs "
                                      "its WHOLE footprint clear or the "
                                      "building stands in trees. Crossings "
                                      "clips because a smaller clear still "
                                      "admits an already-sized deck. Two "
                                      "operations, two policies, one reason "
                                      "each."),
                           "ids_scoped_why": (
                               "id=* would also delete anything that arrives "
                               "between the measurement and the removal, and "
                               "it is the command that deleted POI content in "
                               "the pre-wipe world. Naming the measured "
                               "prefabs makes the safety a property of the "
                               "command.")})
        out["seqs"]["objects_clear"] = res["seq"]
    # THE PAD, THROUGH THE ONE EMITTER.  `regrade=False` because the ground
    # here has just been cleared and nothing of ours is standing on it yet:
    # the regrade census would have an empty population, and a gate whose
    # population is empty by construction is a green tick with nothing behind
    # it.  The standing case is `build.py pad`, which passes True.
    padout = emit_pad(b, place, unit, site, loc, regrade=False)
    out["seqs"].update(padout.pop("seqs"))
    out.update(padout)
    # NO FLUSH HERE, AND THAT IS MEASURED RATHER THAN SAVED EFFORT.  The old
    # single pass ended each unit with `save` because the unit ENDED with a
    # body, and `emit_pad` with `regrade=False` re-reads nothing off the save.
    # `main` flushes ONCE when the whole ground pass is done, which is the
    # point the body pass needs on disk; a save per unit here would double
    # every settlement's flush count for a postcondition nobody asks.
    return out, ar


def apply_unit_body(b, unit: dict, site: str, role: str,
                    units: list[dict], out: dict, ar) -> dict:
    """Place the body on ground the WHOLE SETTLEMENT has already finished.

    Split out of `apply_unit` -- see SETTLEMENT_ORDER_DEPENDS_ON above.  Takes
    the ground pass's `out` and extends it, so one unit still yields one
    report.
    """
    plan_path, info = spawn_plan_file(unit, unit["target_y"], SCRATCH / site)
    # ---- READ THE ROAD, BEFORE THE BODY GOES DOWN ------------------------
    # A HARD GATE, and it is asked AFTER the plan file exists so it can be
    # asked about the pieces themselves rather than about a rectangle: the
    # plan is a file on disk and nothing has been sent yet, so a refusal here
    # costs one scratch file and leaves the world untouched.
    road = road_conflict(unit, plan_piece_xz(plan_path))
    out["road_conflict"] = road
    if road["verdict"] != "clear":
        worst = max(road["piece_conflicts"], key=lambda h: h["pieces_in_band"])
        raise SystemExit(
            f"REFUSED {unit['id']}: {worst['pieces_in_band']} of "
            f"{worst['pieces_total']} pieces of this body stand inside "
            f"{worst['road']}'s paved band ({worst['band_m']} m), nearest "
            f"{worst['nearest_piece_m']} m from its centreline. That road was "
            f"written at seq {worst['road_seq']} ({worst['road_written_utc']}) "
            f"-- it is already on the ground and the operator walks it. "
            f"MEASURED on this ledger: 5 bodies were dropped onto a finished "
            f"road this way (wt-spawn, wt-south, wt-town, tree-near-1, "
            f"tree-sth-1), worst 160 of 212 pieces in T9's band at 0.00 m. "
            f"Re-site the unit clear of the band, or re-terminate the road "
            f"first -- NEVER delete either. Full report: "
            f"{road['piece_conflicts']}")
    rec = spawn_plan_record(b, unit, site, role, plan_path, info, units,
                            ar.radius_m)
    res = b.emit("spawn_plan", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["spawn_plan"] = res["seq"]
    out["pieces"] = rec["params"]["command_count"]
    out["count_radius_m"] = rec["meta"]["count_radius_m"]
    out["counted"] = rec["meta"]["pieces_inside_count_radius"]
    out["checks"] = res["checks"]
    # `save` is issued at the console and NOT recorded as a ledger op: it is a
    # flush rather than a world mutation, and `check_expect` can measure no
    # postcondition of it. Attaching a check that measures something else --
    # a prefab count, say -- would be a check that confidently answers a
    # question it is not asking, which is the defect class of this build.
    b.srv.command("save")
    return out


# ANCHOR_DEPENDS_ON: the body a portal is solved against is the LIVE
# `spawn_plan` whose `site_id` is that unit in THIS world's ledger, its plan
# read from the ledger's own blob.  "scratch" restores the shipped read of
# `SCRATCH/<site>/<unit>.plan`, set only by `selftest`'s neuter.
ANCHOR_SOURCE = "ledger"


def anchor_plan(records: list[dict], read_blob, unit_id: str,
                site: str | None = None
                ) -> tuple[list | None, str]:
    """The plan rows of the body that stood `unit_id`, or (None, why).

    A site name is not a world: `wt-north` is a unit of Ulfsland and of
    Fresh42.  The ledger is the only record that a body was built HERE."""
    if ANCHOR_SOURCE == "scratch":
        path = SCRATCH_ROOT / (site or "") / f"{unit_id}.plan"
        if not path.exists():
            return None, f"no spawn plan at {path}"
        return VP.plan_rows(path), f"scratch plan {path}"
    import writer as ledger_writer  # noqa: PLC0415
    dead = ledger_writer.retired_seqs(records)
    live = [r for r in records if r.get("op") == "spawn_plan"
            and (r.get("params") or {}).get("site_id") == unit_id
            and r.get("seq") not in dead]
    if not live:
        return None, (f"no live spawn_plan with site_id={unit_id} in the "
                      f"{WORLD} ledger: its body was never built in this "
                      f"world")
    rec = live[-1]
    text = read_blob(rec["params"]["plan_sha256"]).decode("utf-8")
    return VP.plan_text_rows(text), f"ledger spawn_plan seq {rec['seq']}"


def anchor_stands(b, unit: dict, units: list[dict], site: str) -> dict:
    """Does the body the site end is solved against ACTUALLY STAND?

    WHY THIS EXISTS. `apply_portals` used to run whenever a site run was not
    a partial, INCLUDING when every body of that site had been refused -- and
    MEASURED tonight on `wt-north`, whose tower was refused on a live census
    of a stone ruin, that stood a paired portal beside an empty field. The
    tag then reads GREEN under every portal check in this file, because those
    check "is this tag paired", and the question that matters is "does this
    tag lead anywhere". A destination that does not exist is strictly worse
    than a missing tag: a missing tag is visible, a working portal to nothing
    is not.

    Measured LIVE rather than taken from the run's own success list, because
    `build.py portals --site X` is a legitimate standalone invocation with no
    such list, and because "the spawn returned" is a weaker claim than "the
    pieces are there now".

    WHAT IS COUNTED IS THIS WORLD'S LEDGER'S BODY, not a scratch file.  See
    `anchor_plan`: the shipped rule read `/tmp/settle/build/<site>/<unit>.plan`
    and MEASURED on Fresh42 drive 2 that was Ulfsland's wt-north, 1,050 m away,
    so a refused tower's portal pair stood beside an empty field again.
    """
    rows, why = anchor_plan(b.led.records(), b.led.read_blob, unit["id"],
                            site)
    if rows is None:
        return {"stands": False, "anchor": unit["id"], "why": why}
    # EXACTNESS IS A BONUS HERE, PRESENCE IS THE QUESTION. `check_radius`
    # refuses when no radius can make a count EXACT, which is the right
    # answer for a piece-count postcondition and the wrong one for "is this
    # body there at all" -- a portal must not be blocked, or a run aborted,
    # because a postcondition radius is unsolvable. Fall back to the body's
    # own reach and keep the `>=` test, which a partially overlapping
    # sibling cannot satisfy.
    exact = True
    try:
        r = check_radius(unit, units, 1e9, rows)
    except SystemExit:
        exact = False
        r = max((math.hypot(row[1] - unit["x"], row[3] - unit["z"])
                 for row in rows), default=1.0) + 0.5
    want: dict[str, int] = {}
    for row in rows:
        d = math.hypot(row[1] - unit["x"], row[3] - unit["z"])
        if d <= r:
            want[row[0]] = want.get(row[0], 0) + 1
    prefab = max(want, key=want.get)
    got, _per = b.srv.count(prefab, unit["x"], unit["z"], r)
    return {"stands": got >= want[prefab], "anchor": unit["id"],
            "prefab": prefab, "want": want[prefab], "got": got,
            "radius_m": round(r, 2), "radius_is_exact": exact,
            "probe": f"objects_count id={prefab} pos={unit['x']:g},"
                     f"{unit['z']:g} max={r:.2f}"}


def apply_portals(b, plan: dict, doc: dict, site: str, units: list[dict],
                  loc: clearance.Locations) -> dict:
    """BOTH ends of this site's tag and BOTH boards that name it, or nothing.

    Every piece of the pair is SOLVED before the first is sent, after every
    gate that can refuse it: the site end's anchor body and THIS world's hall
    both stand in THIS world's ledger, and the site end, the hall arch and
    both boards have legal spots.  A refusal is noted in the ledger and
    returned; it places neither end, because one end of a tag is a door that
    pairs at random with a blank-tag mod portal.  The same rule as
    `terraform/stock.py`'s installation spokes."""
    W = _waypoints()
    tag = site_tag(plan, site)

    def refuse(why: str, **extra) -> dict:
        b.note(f"NO PORTAL PAIR for {site} ({tag}): {why}. Neither end was "
               f"placed; re-run `build.py portals --site {site}` once the "
               f"cause is gone.", role="site_pad", site_id=site)
        return {"tag": tag, "placed": False, "why": why, **extra}

    # THE GATE: no body, no portal. `site_portal_spot` solves the site end
    # against `units[0]`, so that is the body whose absence makes the tag a
    # lie, and it is the one measured.
    standing = anchor_stands(b, units[0], units, site)
    if not standing["stands"]:
        return refuse(f"its anchor body {units[0]['id']} is not standing",
                      anchor_check=standing)
    import stock  # noqa: PLC0415
    import writer as ledger_writer  # noqa: PLC0415
    hall = hub_placement()
    recs = b.led.records()
    if not stock.standing_body_plans(recs, hall["id"],
                                     ledger_writer.retired_seqs(recs)):
        return refuse(f"the hall {hall['id']} has no live spawn_plan in the "
                      f"{WORLD} ledger: its body was never built, so its end "
                      f"has nothing to stand in")
    spots = hub_spots([site_tag(plan, s) for s in
                       ([*plan["towns"]] + [r["id"] for r in plan["outliers"]])])
    hub, h = spots["_hub"], spots[tag]
    if h["board"] is None:
        return refuse(f"the hall has no arch and board for this tag: {h['why']}")
    arch = h["arch"]
    # THE SEED ROUND THE PAD, READ BEFORE THE SITE END IS SOLVED (see
    # SITE_SEED_DEPENDS_ON): the trees and rocks the gate will walk the
    # walker through, off the live world, recorded with how they were read.
    seed = ()
    if SITE_SEED_MODE == "read" and stock.WALKIN_MODE == "reserved":
        seed, seen = site_seed(b.srv, units[0])
        b.observe(f"seed objects round {site}'s site end",
                  "MEASURED: `objects_count id=*` tiles for the names, then "
                  "a bounded, height-stepped `findObjects -detailed` per "
                  "natural prefab for position "
                  "and rotation; kept where the gate's own solid for it "
                  "reaches the site end's walk-in",
                  "tools/jumpstart/settlements/build.py::seed_census", seen,
                  role="spawn_portal", site_id=site)
        if seen["unlisted"]:
            return refuse(f"the seed round the site end was not read whole: "
                          f"{len(seen['unlisted'])} disc(s) held more than "
                          f"one bounded listing can carry, and unread is not "
                          f"clear", seed_census=seen)
    try:
        spot, book = site_portal_spot(plan, site, units, seed)
    except SystemExit as exc:
        return refuse(str(exc))
    name = site.replace("-", " ").title()
    # ON THE UNIT'S LEVELLED PAD (`unit_seat`), left side first as it always
    # was, then the right: a side the pad does not reach refuses by number.
    site_board, board_why = None, []
    for side in ("left", "right"):
        try:
            site_board = W.portal_guidepost(spot["x"], spot["y"], spot["z"],
                                            spot["yaw"],
                                            W.wrap_for_board(f"{name} {tag}"),
                                            side=side, seat=unit_seat(units[0]),
                                            walkins=book)
            break
        except W.WaypointError as exc:
            board_why.append(f"{side}: {exc}")
    if site_board is None:
        return refuse(f"the site end's board has nowhere to stand: "
                      f"{'; '.join(board_why)}")
    bearing, compass, metres = W.bearing_and_range((hub["x"], hub["z"]),
                                                   (spot["x"], spot["z"]))
    hub_text = W.pointer_text(name, compass, metres)
    hub_board = [W.Piece(prefab, x, y, z, yaw,
                         hub_text if prefab == W.BOARD_PREFAB else None)
                 for prefab, x, y, z, yaw in h["board"]]

    out: dict = {"tag": tag, "placed": True, "site_end": spot,
                 "hub_end": arch, "hall": hub, "seqs": {}}
    rec = portal_record(tag, spot["x"], spot["y"], spot["z"], spot["yaw"],
                        site, "site", f"portal hall ({hub['id']})", spot)
    res = b.emit("portal", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["site"] = res["seq"]
    out["site_readback"] = res["checks"]

    rec = portal_record(tag, arch["x"], arch["y"], arch["z"], arch["yaw"],
                        site, "hub", f"{site} site end", arch)
    res = b.emit("portal", params=rec["params"], wire=rec["wire"],
                 requires=rec["requires"], expect=rec["expect"],
                 meta=rec["meta"])
    out["seqs"]["hub"] = res["seq"]
    out["hub_readback"] = res["checks"]

    for label, pieces in (("site_sign", site_board), ("hub_sign", hub_board)):
        out["seqs"][label] = []
        for rec in sign_records(pieces, site, tag):
            res = b.emit(rec["op"], params=rec["params"], wire=rec["wire"],
                         requires=rec["requires"], expect=rec["expect"],
                         meta=rec["meta"])
            out["seqs"][label].append(res["seq"])
    out["bearing_deg"] = round(bearing, 1)
    out["range_m"] = round(metres, 1)
    b.srv.command("save")
    return out


# --- verification: what the OPERATOR will see ----------------------------
#
# Not "did the plan say so". Every number here is measured off the plan file
# that was actually sent plus the world that actually answered.

GROUND_TOL_M = 0.10
# `waterfront.lh_grounding`'s own discriminator, restated so this module does
# not have to import it: MEASURED over 528 resolvable bodies, structural air
# (docks, bridges, stilt towns, portal arcades) runs 2.00-6.00 m while slope
# and cliff captures run 6.32-100.58 m.  An overhang within this of the ground
# is a building that missed it; above it, it is a gallery or an eave.
NEAR_GROUND_TOL_M = 4.0


def applied_surface(zones, seed: str, out: Path, actor: str = "SettleVerify"):
    """`(x, z) -> applied_y or None`: the surface a player actually stands on.

        applied = HeightmapBuilder surface  +  live TerrainComp delta

    Both halves are the engine's, not the builder's.  The first comes from
    `PatchScan --surface heightmap` (see `flatten.DATUM_SURFACE`); the second
    from `roads/applied.Applied`, which unions every `terrain_write` blob in
    the ledger in FILE ORDER so a later record's overwrite of this pad's
    samples is included.  Off-lattice points are the bilinear blend of the four
    surrounding samples, because that is how `Heightmap` renders ground.

    This is the reference `grounding` needs.  It is a separate function so the
    expensive halves -- a PatchScan boot and a ledger decode -- are paid once
    per site rather than once per column.
    """
    import applied as APPLIED
    patches = FL.run_patchscan(sorted(set(zones)), seed, out)
    live = APPLIED.Applied(actor=actor)

    def at(x: float, z: float):
        got = live.surface_at(patches, float(x), float(z))
        return None if got is None else got[0]

    return at


def grounding(plan_path: Path, pad_y: float,
              surface=None) -> dict:
    """Grounded fraction and worst air gap of a placed body.

    `bottoming` gives the LOWEST solid in each column, which is the right
    question: a column whose lowest solid stands 2 m up has nothing under it
    and that is the operator's floating building. A column over a roof is not
    counted as floating, because its lowest solid is the ground floor.

    `surface` IS THE POINT OF THIS FUNCTION AND OMITTING IT IS A DEGRADED MODE.
    Pass a callable `(x, z) -> applied_y or None`: the height the ENGINE
    RENDERS plus the live TCData delta.  Without it the body is compared
    against `pad_y`, the scalar the pad was levelled TO -- and a body seated on
    that number is grounded against it BY CONSTRUCTION, whatever the ground is
    doing.  MEASURED, and this is why the field is not optional in spirit: four
    consecutive grounding censuses reported ZERO floating columns at wt-peak
    while its 212-piece watchtower stood in a median 4.92 m of air, because
    `pad_y` was 102.310 and the surface the engine draws under that pad is
    93.885..99.427.  The check's reference was the thing under test, so it
    could only pass.

    The returned `reference` field says which mode ran, so a clean verdict from
    the degraded mode cannot be quoted as a grounding result.
    """
    import base_geometry
    geom = base_geometry.geometry()
    objs = VP.plan_objects(plan_path)
    floor = VP.bottoming(objs, geom)
    cell = base_geometry.BASE_PROFILE_CELL_M
    gaps, uncovered = [], 0
    for (ix, iz), (_n, y) in floor.items():
        if surface is None:
            gaps.append(y - pad_y)
            continue
        ref = surface((ix + 0.5) * cell, (iz + 0.5) * cell)
        if ref is None:
            uncovered += 1
            continue
        gaps.append(y - ref)
    gaps.sort()
    if not gaps:
        return {"columns": 0, "uncovered_columns": uncovered,
                "reference": "applied" if surface else "pad_y_scalar"}
    grounded = sum(1 for g in gaps if g <= GROUND_TOL_M)
    # The discriminator `waterfront.lh_grounding` already uses: an overhang
    # 34.8 m up is a lantern gallery, an overhang 0.4 m up is a building that
    # missed the ground.  Reported here too, because the bare fraction reads as
    # a floating building on any body with eaves.
    near = [g for g in gaps if GROUND_TOL_M < g <= NEAR_GROUND_TOL_M]
    return {
        "columns": len(gaps),
        "uncovered_columns": uncovered,
        "reference": "applied" if surface else "pad_y_scalar",
        "grounded_fraction": round(grounded / len(gaps), 3),
        "worst_air_gap_m": round(gaps[-1], 3),
        "p95_air_gap_m": round(gaps[int(0.95 * (len(gaps) - 1))], 3),
        "deepest_below_pad_m": round(gaps[0], 3),
        "floating_near_ground_columns": len(near),
        "worst_near_ground_air_m": round(max(near), 3) if near else 0.0,
        "near_ground_tolerance_m": NEAR_GROUND_TOL_M,
        "tolerance_m": GROUND_TOL_M,
    }


def strays(plan_path: Path, unit: dict) -> dict:
    """Pieces beyond the body's own reach -- a misparsed rotation or a
    corner-anchored body shows up here and nowhere else."""
    rows = VP.plan_rows(plan_path)
    half_diag = math.hypot(unit["pad_w"], unit["pad_d"]) / 2
    d = [math.hypot(r[1] - unit["x"], r[3] - unit["z"]) for r in rows]
    beyond = [r[0] for r, dist in zip(rows, d) if dist > half_diag]
    return {"pieces": len(rows), "max_reach_m": round(max(d), 2),
            "pad_half_diagonal_m": round(half_diag, 2),
            "beyond_pad_count": len(beyond),
            "beyond_pad_prefabs": sorted(set(beyond))}


def fixture_audit(unit: dict) -> dict:
    """`fixtures.audit()` over the fixtures the BODY CARRIES, at the yaw and
    height it was placed at. Beds, hearths and fire pits are the ones the
    operator named: a bed in the rain is the reported defect."""
    import fixtures
    fx, _objects, body = body_of(unit, (unit["x"], unit["target_y"],
                                        unit["z"]), unit["yaw"])
    # The carried fixtures, in world coordinates, straight off the placed body.
    rows = [(s[6], (s[0] + s[1]) / 2, s[2], (s[4] + s[5]) / 2, 0.0)
            for s in body.solids if fixtures.is_fixture(s[6])]
    if not rows:
        return {"carried_fixtures": 0,
                "note": "this body carries no bed, hearth, station or chest"}
    # THE STRUCTURE MUST BE MEASURED WITHOUT THE FIXTURES IN IT, and getting
    # this wrong is why every carried fixture in Stenvik read as a failure.
    # `audit` does two things with the body it is given: it pierces each
    # fixture's box against the body's solid index, and it classifies the
    # fixture's cell against the body's interior mask. The fixtures here are
    # solids OF THIS BODY, so both saw them --
    #   * the index contained the fire pit, so the fire pit overlapped
    #     itself. MEASURED on `stenvik-hall-1`: `fire_pit at (480.01, 45.20,
    #     915.25) overlaps fire_pit by 2.00 x 1.27 x 2.00 m`, its own
    #     dimensions to the centimetre.
    #   * the mask counted the fire pit as an obstruction, so its own cell
    #     classified `blocked` rather than `indoor_covered` and the
    #     indoor-and-covered preference failed on a hearth that is demonstrably
    #     under a roof -- the same message reported `0 of 4 cell(s) open to
    #     the sky`, which is the answer to the question that was actually
    #     asked.
    # Six of fifteen bodies "failed" on those two artefacts. The frame is
    # rebuilt with the same datum, footprint and origin so the world
    # coordinates are unchanged, carrying only the structure's solids --
    # re-running `place_body` on a filtered object list would re-derive the
    # datum and the footprint centre and move the building.
    structure = [s for s in body.solids if not fixtures.is_fixture(s[6])]
    sbody = fixtures.PlacedBody(
        solids=structure, carried={}, datum=body.datum,
        footprint=body.footprint, origin=body.origin,
        pivot_only=body.pivot_only, centre=body.centre, pad_y=body.pad_y)
    rep = fixtures.audit(rows, sbody, sbody.index())
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    indoor = [r for r in rep["fixtures"]
              if r["want"] == fixtures.WANT_INDOOR]
    return {
        "carried_fixtures": len(rows),
        "kinds": kinds,
        "failures": rep["failures"],
        "preference_failures": rep["preference_failures"],
        "portal_failures": rep["portal_failures"],
        "portal_warnings": rep["portal_warnings"],
        "min_clearance_m": rep["min_clearance_m"],
        "min_wall_clearance_m": rep["min_wall_clearance_m"],
        "indoor_wanting": len(indoor),
        "indoor_covered": sum(1 for r in indoor
                              if r["class"] == fixtures.INDOOR_COVERED),
        "indoor_covered_m2": rep["interior"].get("indoor_covered_area_m2"),
        "clean": not (rep["failures"] or rep["preference_failures"]
                      or rep["portal_failures"]),
    }


class PostBuildField:
    """The surface a player will walk: generated ground, plus every pad delta
    this build wrote. Passed to `terrain1m.walk_profile` so the gradient is
    computed by the SAME function that judged the plan -- a second copy of the
    baseline arithmetic is how a street gets two answers."""

    def __init__(self, field, units: list[dict]):
        self.f = field
        self.units = units

    def at(self, x: float, z: float) -> float:
        h = self.f.at(x, z)
        for u in self.units:
            if (abs(x - u["x"]) <= u["pad_w"] / 2 + APRON_M
                    and abs(z - u["z"]) <= u["pad_d"] / 2 + APRON_M):
                return u["target_y"]
        return h


def street_report(plan: dict, site: str, units: list[dict]) -> list[dict]:
    """Street gradients over an 8 m baseline, on the POST-BUILD surface.

    The hard limit is the MEASURED player slide angle, 38 deg = 0.781.
    Adjacent-1 m-sample gradient is reported too, because it is the number
    that produced a false NOT WALKABLE verdict earlier tonight -- it is a
    different quantity, dominated by terrain noise, and it is shown beside
    the 8 m figure rather than instead of it.
    """
    if site not in plan["towns"]:
        return []
    fields = T.load(plan["patch_file"])
    out = []
    for street in plan["towns"][site]["streets"]:
        nodes = [tuple(n) for n in street["nodes"]]
        f = T.field_for(fields, nodes[0][0], nodes[0][1])
        post = PostBuildField(f, units)
        prof = T.walk_profile(post, nodes)
        out.append({
            "street": street["name"],
            "length_m": prof["length_m"],
            "relief_m": prof["relief_m"],
            "grade_8m_max": prof["grade_8m_max"],
            "grade_1m_max": prof["grade_1m_max"],
            "max_step_m": prof["max_step_m"],
            "slide_angle_grade": prof["slide_angle_grade"],
            "walkable": prof["grade_8m_max"] <= prof["slide_angle_grade"],
            "planned_grade_8m_max": street["profile"]["grade_8m_max"],
        })
    return out


def live_verify(srv, plan: dict, site: str, units: list[dict],
                role: str) -> list[dict]:
    """What the world answers, prefab-scoped and radius-bounded throughout.

    `objects_count id=*` is allowed only inside 40 m by `Server.count`, and
    `findObjects` is only ever asked with both a prefab filter and a radius:
    every wedge on this project followed an unbounded listing.
    """
    import area as clear_area
    doc = placements_doc(plan, site)
    out = []
    # ONE applied surface for the whole site: a PatchScan boot and a ledger
    # decode each cost ~20 s, and the zones overlap heavily between units.
    zones: list[tuple[int, int]] = []
    for u in units:
        zones += FL.zones_for(u["x"], u["z"], u["pad_w"], u["pad_d"])
    surface = applied_surface(zones, SEED,
                              FL.SCRATCH / f"patch_verify_{site}.bin")
    for u in units:
        place = next(p for p in doc["placements"] if p["id"] == u["id"])
        ar = clear_area.clearing_area(place, apron=APRON_M)
        plan_path = SCRATCH / site / f"{u['id']}.plan"
        rows = VP.plan_rows(plan_path)
        radius = check_radius(u, units, ar.radius_m, rows)
        inside: dict[str, int] = {}
        for prefab, x, _y, z, _yaw in rows:
            if math.hypot(x - u["x"], z - u["z"]) <= radius:
                inside[prefab] = inside.get(prefab, 0) + 1
        # Same filter as the postcondition, for the same measured reason: a
        # torch's one-shot `sfx_`/`vfx_` children are transient and would
        # make this count disagree with itself between two runs.
        got, per = srv.count("*", u["x"], u["z"], radius,
                             ignore="_*,sfx_*,vfx_*")
        dominant = max(inside.items(), key=lambda kv: kv[1])[0]
        out.append({
            "unit": u["id"], "plan_pieces": len(rows),
            "count_radius_m": radius,
            "expected_inside": sum(inside.values()), "world_inside": got,
            "matches": got == sum(inside.values()),
            "dominant_prefab": dominant,
            "dominant_expected": inside[dominant],
            "dominant_world": per.get(dominant),
            "grounding": grounding(plan_path, u["target_y"], surface=surface),
            "strays": strays(plan_path, u),
        })
    return out


def portal_pairing(srv, plan: dict, site: str) -> dict:
    """EVERY END OF THIS SITE'S TAG, read off the ZDO.

    The defect the operator reported by name is a one-way trip, and its cause
    is a tag with one end: pairing in `TeleportWorld` is exact string equality
    against a uniform random draw among equally-tagged UNCONNECTED portals, so
    one end pairs with nothing and a BLANK tag pairs at random with this
    world's blank-tag mod-location portals.

    The ends come from the ledger's own `portal` records for this tag -- the
    ledger is where both ends are declared -- and each is then read back
    live at its recorded position, prefab-scoped inside 4 m.
    """
    import re as _re
    tag = site_tag(plan, site)
    led = (JUMPSTART / "ledger/runs" / WORLD / "ledger.jsonl").read_text()
    declared = [json.loads(line) for line in led.splitlines()]
    ends = [r for r in declared
            if r["op"] == "portal" and r["params"].get("tag") == tag]
    out = []
    for rec in ends:
        x, y, z = rec["params"]["pos"]
        reply = srv.command(f"findObjects -prefab {PORTAL_PREFAB} -near "
                            f"{x:.2f} {y:.2f} {z:.2f} 4 -detailed")
        tags = _re.findall(r"Portal tag: (\S*)", reply)
        out.append({"pos": [x, y, z], "seq": rec["seq"],
                    "end": (rec.get("meta") or {}).get("end"),
                    "tags_found": tags,
                    "ok": tags.count(tag) == 1})
    return {
        "tag": tag, "tag_length": len(tag),
        "declared_ends": len(ends), "ends": out,
        "paired": len(ends) == 2 and all(e["ok"] for e in out),
        "why": ("a tag needs EXACTLY two standing ends: one end is a "
                "one-way trip and three ends pair at random among them"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["placements", "plan", "validate", "apply",
                                   "pad", "portals", "verify", "selftest"],
                    help="`pad` re-emits ONLY the site_pad terrain_write for "
                         "the units named by --only, under the regrade "
                         "census, and touches no piece: it is the path a "
                         "STANDING building's ground had none of")
    ap.add_argument("--site")
    # DEFAULT None, RESOLVED AFTER `worldseed.apply`: a default computed at
    # parser-construction time would be fixed before `--world` is known, and
    # that is precisely how one document came to serve every world.
    ap.add_argument("--plan-json", default=None,
                    help="the solved settlement plan; default "
                         "settlements/plan.<World>.json when it exists, else "
                         "settlements/plan.json -- either way REFUSED unless "
                         "the document declares this run's seed")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--no-portals", action="store_true")
    # WHY A NAME SELECTOR EXISTS BESIDE --from/--limit. Those two are a
    # RESUME: a contiguous tail of the unit list, which is what a run that
    # stopped needs. The eight pads Stenvik refused on the mod village are
    # NOT contiguous -- they are indices 1, 5, 7, 8, 11, 12, 14 and 15 of
    # eighteen -- so building the ones that a nudge cleared needs a set, and
    # `--from 8 --limit 8` would rebuild six standing buildings to reach two.
    ap.add_argument("--only", default="",
                    help="comma-separated unit ids to build, in the plan's "
                         "own order; refuses an id that is not a unit of "
                         "this site")
    # WHO THE LEDGER SAYS DID IT. Siblings share one ledger and the chain
    # records an actor per record; `SettleBuild` stood the first eight
    # Stenvik buildings and yielded, and the rest of this world is built by
    # `SiteFinish`. Hard-coding the first agent's name would credit one
    # agent's work to another in the only log that survives the session.
    ap.add_argument("--actor", default="SiteFinish")
    worldseed.add_arguments(ap)
    a = ap.parse_args()

    global SEED, WORLD, SCRATCH  # noqa: PLW0603
    SEED, WORLD = worldseed.apply(a)
    SCRATCH = SCRATCH_ROOT / WORLD

    # BEFORE THE PLAN IS READ.  The compiler-restore selftest needs no plan,
    # no world and no server, so it must not be reachable only from a machine
    # that has one.
    if a.op == "selftest":
        return selftest()
    plan_json = Path(a.plan_json) if a.plan_json else worldseed.plan_path(SEED)
    plan = json.loads(plan_json.read_text())
    # THE PLAN IS AN ARTEFACT OF ONE WORLD'S GROUND.  Refused here, before a
    # unit is read, a placements document is synthesised or the write token is
    # taken -- see PLAN_SCOPE_DEPENDS_ON for the 45 pads that proved it.
    plan_scope_ok = require_plan_scope(plan_json, plan)
    print(f"# plan {plan_json} seed {plan_scope_ok['seed']!r} "
          f"world {plan_scope_ok['world']!r} -> building {WORLD!r} "
          f"on seed {SEED!r}; location dump {locations_dump(plan)}")
    if a.op == "placements":
        for p in write_placements(plan):
            print(p)
        return 0
    if not a.site:
        ap.error("--site is required for plan/validate/apply/pad/portals")
    # BEFORE THE WRITE TOKEN, not inside it.  `pad` re-emits a terrain_write
    # per named unit and there is no undo, so a bare `pad --site vestvik`
    # would re-level every standing pad in the town.  Refusing here costs
    # nobody the ledger lock.
    if a.op == "pad" and not a.only:
        ap.error("`pad` requires --only: name the standing units whose "
                 "ground is to be re-levelled. It re-emits a terrain_write "
                 "per unit and there is no undo.")

    doc = placements_doc(plan, a.site)
    PLACEMENTS.mkdir(parents=True, exist_ok=True)
    doc_path = PLACEMENTS / f"{a.site}.yaml"
    doc_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=10 ** 9))
    all_units = unit_records(plan, a.site)
    units = all_units[a.start:]
    if a.limit:
        units = units[:a.limit]
    if a.only:
        want = [u for u in a.only.split(",") if u]
        known = {u["id"] for u in all_units}
        unknown = [u for u in want if u not in known]
        if unknown:
            ap.error(f"--only names {unknown} which are not build units of "
                     f"{a.site}; its units are {sorted(known)}")
        units = [u for u in all_units if u["id"] in want]
    loc = clearance.load(locations_dump(plan))
    kind = "town" if a.site in plan["towns"] else next(
        r["kind"] for r in plan["outliers"] if r["id"] == a.site)
    role = ROLE_BODY[kind]

    print(f"# {a.site}: {len(units)} of {len(all_units)} build unit(s), "
          f"role {role}, placements {doc_path}")
    if a.op == "plan":
        for u in units:
            half_diag = math.hypot(u["pad_w"], u["pad_d"]) / 2
            lc = location_check(loc, u["x"], u["z"], half_diag)
            sv = sample_verdict(loc, u)
            ops = clearing_ops(doc, u["id"])
            plan_path, info = spawn_plan_file(u, u["target_y"],
                                              SCRATCH / a.site)
            print(f"\n## {u['id']} {u['body']} at ({u['x']}, {u['z']}) yaw "
                  f"{u['yaw']} pad {u['pad_w']}x{u['pad_d']} target_y "
                  f"{u['target_y']}")
            print(f"   dump disc:    {lc['verdict']} (nearest "
                  f"{lc['nearest']['name']} at {lc['nearest']['dist_m']} m, "
                  f"stand-off {lc['standoff_m']} m)")
            print(f"   dump samples: {sv['verdict']} over "
                  f"{sv['samples_tested']} written samples")
            for o in ops:
                print(f"   {o['op']:16s} {o['wire']}")
            print(f"   spawn_plan       {plan_path} {info['command_count']} "
                  f"commands, {len(info['prefabs'])} distinct prefabs")
        return 0

    from live import LiveBuilder  # noqa: E402
    import schema  # noqa: E402

    if a.op == "verify":
        # WHAT THE OPERATOR WILL SEE, and nothing that writes. `Server` is
        # used directly rather than `LiveBuilder`: this op appends no record,
        # so taking a ledger handle would claim the write token for a
        # read-only question.
        import replay as R  # noqa: E402
        report: dict = {"site": a.site, "units": [], "streets": [],
                        "portals": None}
        with R.Server() as srv:
            srv.probe()
            live = live_verify(srv, plan, a.site, units, role)
            for row, u in zip(live, units):
                row["fixtures"] = fixture_audit(u)
                report["units"].append(row)
            report["streets"] = street_report(plan, a.site, units)
            report["portals"] = portal_pairing(srv, plan, a.site)
        report["summary"] = {
            "units": len(report["units"]),
            "counts_exact": sum(1 for r in report["units"] if r["matches"]),
            "with_strays": sum(1 for r in report["units"]
                               if r["strays"]["beyond_pad_count"]),
            "fixtures_clean": sum(1 for r in report["units"]
                                  if r["fixtures"].get("clean", True)),
            "worst_grounded_fraction": min(
                (r["grounding"]["grounded_fraction"]
                 for r in report["units"]), default=None),
            "streets_walkable": all(s["walkable"]
                                    for s in report["streets"]),
            "portals_paired": report["portals"]["paired"],
        }
        print(json.dumps(report, indent=1, default=str))
        ok = (report["summary"]["counts_exact"] == len(report["units"])
              and report["summary"]["portals_paired"])
        return 0 if ok else 1

    if a.op == "validate":
        # No append, no console: build the records and validate them. The
        # ledger object is needed only for its blob store, which is additive
        # and content-addressed.
        from writer import Ledger
        led = Ledger(Path(str(JUMPSTART / "ledger/runs" / WORLD)),
                     actor=a.actor)

        class Bag:
            def blob(self, data, note=""):
                return led.blob(data, note=note)

        bag = Bag()
        bad = 0
        for u in units:
            for rec in unit_ops(bag, plan, doc, u, a.site, role, all_units,
                                loc, validate_only=True):
                envelope = {"seq": 0, "ts": "1970-01-01T00:00:00Z",
                            "actor": a.actor, "op": rec["op"],
                            "params": rec["params"], "wire": rec["wire"],
                            "requires": rec["requires"],
                            "expect": rec["expect"], "meta": rec["meta"],
                            "prev": "0" * 64}
                problems = schema.validate(envelope)
                print(f"  {u['id']:24s} {rec['op']:14s} "
                      f"{'OK' if not problems else 'REFUSED'} "
                      f"({len(rec['wire'])} wire line(s))")
                for p in problems:
                    bad += 1
                    print(f"      - {p}")
        print(f"validate: {'clean' if not bad else str(bad) + ' problem(s)'}")
        return 0 if not bad else 1

    built = []
    with LiveBuilder(actor=a.actor) as b:
        if a.op == "portals":
            print(json.dumps(apply_portals(b, plan, doc, a.site, all_units,
                                           loc), indent=1, default=str))
            print(json.dumps(b.close(), indent=1, default=str))
            return 0
        if a.op == "pad":
            # The unit set is named, never defaulted; refused above, before
            # the write token was taken.
            padded, refused_pads = [], []
            for u in units:
                try:
                    place = next(p for p in doc["placements"]
                                 if p["id"] == u["id"])
                    res = emit_pad(b, place, u, a.site, loc, regrade=True)
                except SystemExit as exc:
                    refused_pads.append({"unit": u["id"], "reason": str(exc)})
                    b.note(f"PAD REFUSED {u['id']}: {exc}", role="site_pad",
                           site_id=u["id"])
                    print(f"REFUSED {u['id']} -- recorded, continuing\n{exc}",
                          flush=True)
                    continue
                res["unit"] = u["id"]
                padded.append(res)
                print(json.dumps(res, indent=1, default=str), flush=True)
            report = b.close()
            print(json.dumps({"site": a.site, "pads": padded,
                              "refused": refused_pads, "close": report},
                             indent=1, default=str))
            return 0 if (report["status"] == "complete"
                         and not refused_pads) else 1
        # TWO PASSES OVER THE SAME UNITS, AND THE ORDER IS THE POINT.  See
        # SETTLEMENT_ORDER_DEPENDS_ON at `apply_unit_ground`: one pass per
        # unit put every pad write after the previous unit's body, and on
        # Fresh42 that was 42 writes after the first piece, 18 of them into a
        # zone that already held one.  A settlement's ground is finished
        # before any of its bodies stand.
        refused = []
        ground: list[tuple[dict, dict, object]] = []
        for u in units:
            # A REFUSAL IS A RESULT, NOT A CRASH. One pad that cannot be
            # cleared safely must not cost the operator the other seventeen
            # buildings, and the refusal is already in the ledger as an
            # `observe` with its measurement before the raise -- so it is
            # recorded, reported at the end, and the town continues.
            try:
                res, ar = apply_unit_ground(b, doc, u, a.site, all_units, loc)
            except SystemExit as exc:
                refused.append({"unit": u["id"], "stage": "ground",
                                "reason": str(exc)})
                b.note(f"REFUSED {u['id']}: {exc}", role="site_pad",
                       site_id=u["id"])
                print(f"REFUSED {u['id']} -- recorded, continuing\n{exc}",
                      flush=True)
                continue
            ground.append((u, res, ar))
            print(json.dumps(res, indent=1, default=str), flush=True)
        # THE GROUND IS FINISHED AND ON DISK BEFORE THE FIRST PIECE STANDS.
        if ground:
            b.srv.command("save")
        # A UNIT WHOSE GROUND WAS REFUSED HAS NO PAD, so it gets no body; it
        # is dropped here rather than re-tried, which is what the single pass
        # did too.
        for u, res, ar in ground:
            try:
                res = apply_unit_body(b, u, a.site, role, all_units, res, ar)
            except SystemExit as exc:
                refused.append({"unit": u["id"], "stage": "body",
                                "reason": str(exc)})
                b.note(f"REFUSED {u['id']}: {exc}", role=role,
                       site_id=u["id"])
                print(f"REFUSED {u['id']} -- recorded, continuing\n{exc}",
                      flush=True)
                continue
            built.append(res)
            print(json.dumps(res, indent=1, default=str), flush=True)
        if refused:
            print(json.dumps({"refused": refused}, indent=1), flush=True)
        # A PARTIAL BUILD MUST NOT RE-EMIT THE PORTAL PAIR. `--only` joins
        # `--limit` and `--from` here: the site end is solved on the FIRST
        # unit's pad, and with a subset that is a different pad -- so a
        # partial run would stand a second arch with the same tag, and a tag
        # with three ends pairs at random among them.
        if not a.no_portals and not a.limit and not a.start and not a.only:
            built.append(apply_portals(b, plan, doc, a.site, all_units, loc))
            print(json.dumps(built[-1], indent=1, default=str), flush=True)
        report = b.close()
    print(json.dumps({"site": a.site, "units": built, "close": report},
                     indent=1, default=str))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
