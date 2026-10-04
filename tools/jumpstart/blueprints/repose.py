#!/usr/bin/env python3
"""REPOSE AND DEDUPLICATE PLACED PIECES, BY ZDO ID, THROUGH THE LEDGER.

WHY THIS TOOL EXISTS AND WHAT IT REFUSES TO DO.

Two defect classes on this world could not be repaired by any command the
build path already had, and both for the same reason: `deleteObjects -near`
and `spawn_object` address a POSITION, and the defects are two pieces at ONE
position and one piece at the RIGHT position with the WRONG pose.

  INVERTED PANELS.  A roof panel yawed 180 degrees rises AWAY from its own
  ridge.  The only modelled remedy was retire + spawn, which is two
  mutations, a window in which the roof has a hole, a new ZDO id and the
  loss of whatever the old ZDO carried.  `modifyObject <id> -rotation x y z`
  changes the pose of the EXISTING object -- MEASURED in ValheimRcon IL,
  `RotationModification::Apply` is `Quaternion.Euler(v)` then
  `ZDO.SetRotation`, then `ZdoUtils.SetZdoModified`.

  CO-LOCATED COPIES.  Two pieces of one prefab at one pivot cannot be told
  apart by a radius: `deleteObjects -prefab P -near x y z 0.05` takes BOTH.
  `deleteObjects -id <id:userid>` takes exactly one.  That is why an
  orientation flip iterated on such a pair converges and stays: flipping
  both members reproduces the pair.

WHAT IT REFUSES.  It will not remove a piece that fills space nothing else
fills.  The membership test is `accept.sections.structures.buried_fraction`
-- the same instrument the gate refuses with -- so this tool and the gate
cannot disagree about what a duplicate is.  MEASURED on Ulfsland: of 2,874
co-located extras, 2,457 are buried in a sibling and 417 are architecture
(two roofs meeting in a valley, two half-arches making one arch) and are
left standing.

THE ID IS EVIDENCE, NOT AN ADDRESS.  ZDO ids are NOT durable: measured
across a container restart, 414 objects came back at byte-identical
coordinates with DIFFERENT ids.  So every record is keyed by prefab and FULL
3D position, `keeps` says how many same-prefab pieces remain inside that
sphere, and the id appears only in `meta` as the evidence of which one went.

TRUNCATION IS CHECKED, NEVER ASSUMED.  `findObjects -detailed` silently
truncates near 4050 bytes; MEASURED, a disc that declared "Found 35 objects"
returned 31 rows.  For a duplicate hunt that is the worst possible fault --
a short reply makes a PAIR read as a single piece and the duplicate
disappears from the census.  Every lookup here asserts
declared == prefab rows parsed, and refuses the pivot otherwise.

    repose.py plan   --out /tmp/repose.json      # offline, no wire
    repose.py probe  --plan ... --index N        # ONE piece, both ends
    repose.py apply  --plan ... [--what flip|dedup] [--limit N]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "terraform"))

from accept import instruments as I                          # noqa: E402
from accept.sections import structures as S                  # noqa: E402

ID_RE = re.compile(r"Id:\s*(-?\d+:-?\d+)")
FOUND_RE = re.compile(r"Found\s+(\d+)\s+objects")
ROT_RE = re.compile(r"Rotation:\s*\(([-\d.eE ]+)\)")
POS_RE = re.compile(r"Position:\s*\(([-\d.eE ]+)\)")
PREFAB_RE = re.compile(r"-Prefab:\s*(\S+)")

# How wide a disc to ask `findObjects` for around a pivot.  Larger than the
# 0.05 m co-location cube so a float round-trip cannot hide a member, and
# small enough that the reply is a handful of rows.
LOOKUP_R_M = 0.30

# How many pivots of a batch carry a measured postcondition.  Every delete is
# sent and every one is recorded; this is how many of them are PROVEN by a
# second, independent query.  `srv.count` runs through the console bridge
# with a 2 s settle, so one per pivot would cost over two hours of wall clock
# on this campaign for an answer that a sample already gives.
EXPECT_SAMPLE = 12


# ---------------------------------------------------------------------------
# the offline plan
# ---------------------------------------------------------------------------
def load_world(census_tsv: Path):
    geom, build = I.load_geometry()
    rows = []
    with census_tsv.open() as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9:
                continue
            rows.append((f[1] or ("hash:" + f[0]), float(f[2]), float(f[3]),
                         float(f[4]), float(f[5]), float(f[6]), float(f[7]),
                         float(f[8])))

    class C:
        pass
    c = C()
    c.rows = rows
    return geom, build, I.solids_from_census(c, geom)


def yaw_of(q) -> float:
    return math.degrees(math.atan2(2.0 * (q[3] * q[1] + q[0] * q[2]),
                                   1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2]))) % 360.0


def inverted_panels(solids, geom, anchors: list[dict]) -> list[dict]:
    """Every roof panel the `structures` gate counts as inverted.

    IT IS THE GATE'S OWN ROW LIST, NOT A SECOND WALK.  This used to re-type
    the instrument's body partition, ridge rule and skips, with a comment
    saying the verdict would be "re-measured against the gate at the end of
    every run" - which is a claim that the copy and the original agree,
    checked once, afterwards, on whatever the campaign had already done.
    They stopped agreeing the moment `check_orientation` stopped picking one
    ridge point by a distance that ties on a symmetric roof, and this copy
    would then have sent `modifyObject` at 14 panels the gate calls CORRECT
    - a 180 degree flip of sound roof, by the tool whose whole reason for
    existing is that such a flip leaves count, position and AABB unchanged
    and so cannot be seen afterwards.  So the population is read straight
    off the gate, keyed by prefab and FULL 3D POSITION, and the only thing
    added here is the yaw the wire needs, taken from the very solid the row
    names.

    AND THE GATE JUDGES ONE BUILDING AT A TIME.  `check_orientation` alone
    pools every roof piece within 1.2 m into one body, so a house beside a
    taller neighbour is judged against the NEIGHBOUR'S ridge: MEASURED on
    Fresh42 save a521b70356e7, that pool names 12 panels (11 of
    stenvik-house-2 against stenvik-manor-1, 1 of tree-sth-2 against
    tree-sth-3) and every one of them is correct - 0 of 1297 with each
    building judged alone.  Asking the pool here would flip twelve sound
    panels.  `anchors` is `structures.ledger_anchors`, the same provenance
    `our_pieces` scopes by.
    """
    rows = S.orientation_by_building(solids, anchors, geom)["inverted_at"]
    at: dict[tuple, list] = {}
    for s in solids:
        at.setdefault((s.name, round(s.pos[0], 4), round(s.pos[1], 4),
                       round(s.pos[2], 4)), []).append(s)
    out = []
    for r in rows:
        key = (r["prefab"], round(r["pos"][0], 4), round(r["pos"][1], 4),
               round(r["pos"][2], 4))
        hit = at.get(key)
        if not hit:
            raise RuntimeError(
                f"check_orientation named {key} and no solid of this census "
                "carries that prefab at that position: the repair and the "
                "check are looking at two different worlds")
        for s in hit:
            out.append({"prefab": s.name,
                        "pos": list(r["pos"]),
                        "yaw_deg": round(yaw_of(s.quat), 4),
                        "toward": r["margin_m"],
                        "upside_down": r["upside_down"],
                        "body": r["body"], "panels": r["panels"]})
    return out


def colocated(pieces, tol: float = S.DUP_TOL_M) -> list[list]:
    cells = collections.defaultdict(list)
    for s in pieces:
        cells[(s.name, int(math.floor(s.pos[0] / tol)),
               int(math.floor(s.pos[1] / tol)),
               int(math.floor(s.pos[2] / tol)))].append(s)
    idx = {id(s): i for i, s in enumerate(pieces)}
    parent = list(range(len(pieces)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for (nm, cx, cy, cz), lst in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    other = cells.get((nm, cx + dx, cy + dy, cz + dz))
                    if not other:
                        continue
                    for s in lst:
                        for t in other:
                            if s is t:
                                continue
                            if (abs(s.pos[0] - t.pos[0]) <= tol
                                    and abs(s.pos[1] - t.pos[1]) <= tol
                                    and abs(s.pos[2] - t.pos[2]) <= tol):
                                ra, rb = find(idx[id(s)]), find(idx[id(t)])
                                if ra != rb:
                                    parent[rb] = ra
    groups = collections.defaultdict(list)
    for s in pieces:
        groups[find(idx[id(s)])].append(s)
    return [g for g in groups.values() if len(g) >= 2]


def redundant_members(g, geom) -> tuple[int, float]:
    """How many of this group are buried in their siblings, and the worst
    fraction.  Greedy and one at a time: removing a member changes what is
    left, so three identical pieces yield TWO removals, not three."""
    def occ(s):
        return tuple(sorted((round(v[0], 2), round(v[1], 2), round(v[2], 2))
                            for v in s.verts))
    alive = list(range(len(g)))
    worst = 0.0
    dropped = 0
    changed = True
    while changed and len(alive) > 1:
        changed = False
        for i in list(alive):
            rest = [g[j] for j in alive if j != i]
            if not rest:
                break
            if occ(g[i]) in {occ(t) for t in rest}:
                frac = 1.0
            else:
                frac = S.buried_fraction(g[i], rest, geom)
            if frac >= S.DUP_BURIED_FRACTION:
                alive.remove(i)
                dropped += 1
                worst = max(worst, frac)
                changed = True
                break
    return dropped, round(worst, 4)


def column_index(pieces, r: float = LOOKUP_R_M):
    """How many same-prefab pieces share each piece's XZ COLUMN.

    `srv.count` -- and therefore every `prefab_count` and `absent`
    postcondition -- is an XZ CYLINDER WITH NO HEIGHT BOUND, while
    `deleteObjects -near` is a 3D sphere and `deleteObjects -id` is one
    object.  So the number a postcondition will READ after a removal is not
    the number left at the pivot, it is the number left in the whole column,
    and a record that asserts the pivot count fails on a correct delete.
    MEASURED: the first batch of this campaign asserted 1 and read 2 on four
    `wood_pole_log_4`, each of which has a same-prefab sibling stacked above
    it.  This index is what makes the assertion true."""
    cell = collections.defaultdict(list)
    step = max(r * 2.0, 1.0)
    for s in pieces:
        cell[(s.name, int(s.pos[0] // step), int(s.pos[2] // step))].append(s)
    return cell, step


def column_count(cell, step, prefab, x, z, r: float = LOOKUP_R_M) -> int:
    n = 0
    cx, cz = int(x // step), int(z // step)
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            for s in cell.get((prefab, cx + dx, cz + dz), ()):
                if math.hypot(s.pos[0] - x, s.pos[2] - z) <= r + 1e-9:
                    n += 1
    return n


def our_pieces(pieces, ledger: Path) -> tuple[list, dict]:
    """The build pieces in a body matched to a ledger anchor OF THIS RUN.

    THE POPULATION OF A REPAIR IS THE POPULATION OF THE VERDICT, AND FOR
    THIS TOOL IT WAS THE WHOLE SAVE.  `build_plan` used to plan over every
    placed build piece, while `accept.sections.structures` judges
    `our_bodies()`.  MEASURED on Ulfsland save 5385652e with both
    instruments in their fixed form:

      world-wide   168 inverted panels, 46 co-located pieces at 34 pivots
      our bodies    52 inverted panels,  0 co-located pieces

    so an unscoped run of this tool sends `modifyObject` at 116 roof panels
    and `deleteObjects -id` at 46 pieces STANDING INSIDE VALHEIM'S OWN
    GENERATED LOCATIONS - village houses, ruins, dvergr halls - which this
    run did not place and cannot put back.  The nearest position at which
    this run ever declared any of those 46 prefabs is 246.8 m away; the
    farthest is 6,075.5 m.  A retire is the only remedy a duplicate finding
    has, and a wrong retire is the one mutation in this whole tree that
    nothing can undo.

    So the ledger is REQUIRED, not optional: a repair with no notion of what
    this run owns is a machine for demolishing somebody else's building.
    """
    anchors = S.ledger_anchors(ledger)
    bods = [b for b in I.bodies(pieces, slack=0.35)
            if len(b) >= S.MIN_BODY_PIECES]
    matched, _o, _f, _s, _r = S.our_bodies(bods, anchors)
    ours = [s for body, _a in matched for s in body]
    return ours, {"anchors": len(anchors), "bodies": len(bods),
                  "bodies_matched": len(matched),
                  "pieces_in_save": len(pieces), "pieces_ours": len(ours)}


def build_plan(census_tsv: Path, ledger: Path) -> dict:
    geom, build, solids = load_world(census_tsv)
    pieces = [s for s in solids if s.name in build]
    ours, scope = our_pieces(pieces, ledger)
    # The column count a postcondition will read is a fact about THE WORLD,
    # not about our half of it: `srv.count` is an XZ cylinder over every ZDO
    # there is, so it is indexed over the whole save even though only our
    # pieces may be touched.
    cell, step = column_index(pieces)
    flips = inverted_panels(ours, geom, S.ledger_anchors(ledger))
    dedup = []
    for g in colocated(ours):
        n_drop, worst = redundant_members(g, geom)
        a = g[0]
        entry = {"prefab": a.name,
                 "pos": [round(a.pos[0], 4), round(a.pos[1], 4),
                         round(a.pos[2], 4)],
                 "group": len(g), "remove": n_drop,
                 "keeps": len(g) - n_drop,
                 "buried_fraction": worst,
                 "column_before": column_count(cell, step, a.name,
                                               a.pos[0], a.pos[2]),
                 "yaws": sorted(round(yaw_of(s.quat), 2) for s in g)}
        entry["column_after"] = entry["column_before"] - n_drop
        if n_drop:
            dedup.append(entry)
    return {"census": str(census_tsv), "ledger": str(ledger),
            "scope": scope, "flips": flips, "dedup": dedup,
            "totals": {"inverted_panels": len(flips),
                       "dedup_sites": len(dedup),
                       "pieces_to_remove": sum(d["remove"] for d in dedup),
                       **scope}}


# ---------------------------------------------------------------------------
# the live side
# ---------------------------------------------------------------------------
def look(srv, prefab: str, pos, r: float = LOOKUP_R_M,
         *, plan_yaw: float | None = None) -> list[dict]:
    """Every object of `prefab` inside a disc, WITH ITS ID AND ROTATION, or a
    refusal.  Raises when the reply is short: a truncated listing makes a
    co-located pair read as a single piece.

    A DRY RUN THAT CANNOT FORM A BATCH VALIDATES NOTHING.  `R.Server(dry=1)`
    answers `(dry run)` to every command, so this function used to raise
    "findObjects gave no count" at EVERY pivot and `--dry` reported 52
    SKIPS -- the exact same output a total refusal would print, from a run
    whose whole purpose was to prove the record and the wire before the
    console is taken.  MEASURED on this campaign: `apply --what flip --dry`
    over a 52-panel plan built ZERO records and schema-validated ZERO of
    them.  So under `dry` the row is served FROM THE PLAN, with an id that
    says `dry:` in it so no reader can mistake the output for a reading of
    the world: what a dry run is allowed to prove is that the record BUILDS
    and VALIDATES and that the wire string is well formed, and that is now
    what it proves.
    """
    x, y, z = pos
    if getattr(srv, "dry", False):
        return [{"id": "dry:0", "prefab": prefab, "pos": [x, y, z],
                 "rot": [0.0, float(plan_yaw or 0.0), 0.0]}]
    reply = srv.command(f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} "
                        f"{z:.2f} {r:.2f} -detailed")
    if "Found 0 objects" in reply or "No objects" in reply:
        return []
    m = FOUND_RE.search(reply)
    rows = [ln for ln in reply.splitlines() if "-Prefab:" in ln]
    if m is None:
        raise RuntimeError(f"findObjects gave no count for {prefab} at "
                           f"{pos}: {reply[:200]!r}")
    if int(m.group(1)) != len(rows):
        raise RuntimeError(
            f"TRUNCATED reply for {prefab} at {pos}: declared "
            f"{m.group(1)} objects, parsed {len(rows)} rows.  Refusing to "
            f"treat a short answer as a census.")
    out = []
    for ln in rows:
        i, p, rot = ID_RE.search(ln), POS_RE.search(ln), ROT_RE.search(ln)
        pf = PREFAB_RE.search(ln)
        if not (i and p and rot and pf):
            raise RuntimeError(f"unparsed findObjects row: {ln[:200]!r}")
        out.append({"id": i.group(1), "prefab": pf.group(1),
                    "pos": [float(t) for t in p.group(1).split()],
                    "rot": [float(t) for t in rot.group(1).split()]})
    return [o for o in out if o["prefab"] == prefab]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("plan", "probe", "apply"))
    ap.add_argument("--census", type=Path)
    ap.add_argument("--world", default=I.WORLD_DEFAULT)
    ap.add_argument("--ledger", type=Path, default=None,
                    help="the ledger whose anchors say what THIS RUN built; "
                        "defaults to ledger/runs/<world>/ledger.jsonl.  A "
                        "plan is REFUSED without one - see `our_pieces`")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--what", default="both",
                    choices=("flip", "dedup", "both"))
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--actor", default="FixOrientDupes")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if args.mode == "plan":
        ledger = args.ledger or (JUMPSTART / "ledger" / "runs" / args.world
                                 / "ledger.jsonl")
        if not Path(ledger).exists():
            print(f"no ledger at {ledger}: this tool DELETES and ROTATES "
                  f"placed objects, and without the ledger it has no way to "
                  f"tell what this run built from what the seed generated. "
                  f"MEASURED on Ulfsland: unscoped, it plans 116 flips and "
                  f"46 retires inside Valheim's own village houses, ruins "
                  f"and dvergr halls.  Refusing to plan.", file=sys.stderr)
            return 2
        plan = build_plan(args.census, Path(ledger))
        args.out.write_text(json.dumps(plan, indent=1))
        print(json.dumps(plan["totals"], indent=1))
        return 0

    plan = json.loads(args.plan.read_text())
    from live import LiveBuilder                             # noqa: PLC0415

    with LiveBuilder(actor=args.actor, dry=args.dry) as b:
        if args.mode == "probe":
            return _probe(b, plan, args)
        return _apply(b, plan, args)


def _probe(b, plan, args) -> int:
    """ONE piece of each kind, both ends, with the wire state printed."""
    out = {}
    f = plan["flips"][args.index]
    got = look(b.srv, f["prefab"], f["pos"])
    print(f"BEFORE flip: {f['prefab']} at {f['pos']} -> {got}")
    tgt = [0.0, round((f["yaw_deg"] + 180.0) % 360.0, 3), 0.0]
    if len(got) != 1:
        print(f"REFUSED: {len(got)} objects share this pivot; the probe wants "
              f"an unambiguous one")
        return 2
    src = [0.0, round(got[0]["rot"][1], 3), 0.0]
    r = b.emit("modify", params={
        "prefab": f["prefab"], "pos": f["pos"],
        "rotation_deg": tgt, "from_rotation_deg": src,
        "role": "town_body",
        "reason": (f"ORIENTATION one-piece round trip: this {f['prefab']} at "
                   f"({f['pos'][0]:.3f}, {f['pos'][1]:.3f}, {f['pos'][2]:.3f}) "
                   f"rises AWAY from its own ridge, dot(rise, to-ridge) = "
                   f"{f['toward']:+.4f} against a limit of 0, measured on its "
                   f"own rotated geometry in a {f['panels']}-panel roof at "
                   f"({f['body'][0]}, {f['body'][1]}).  A 180 degree yaw "
                   f"moves neither its pivot nor its AABB, so the "
                   f"postcondition is the rotation itself, read back off the "
                   f"object.")},
        wire=[f"modifyObject {got[0]['id']} -rotation "
              f"{tgt[0]:g} {tgt[1]:g} {tgt[2]:g}"],
        expect={"rotation": [{"prefab": f["prefab"], "pos": f["pos"],
                              "rotation_deg": tgt, "count": 1}]},
        meta={"zdo_id": got[0]["id"], "stage": "one_piece_round_trip",
              "players_probe_ms": round(b.srv.probe(), 1)})
    print("modify ->", r["status"], r.get("checks"))
    out["flip"] = {"seq": r["seq"], "id": got[0]["id"], "from": src,
                   "to": tgt, "pos": f["pos"], "prefab": f["prefab"]}
    after = look(b.srv, f["prefab"], f["pos"])
    print(f"AFTER flip (live): {after}")

    d = plan["dedup"][args.index]
    got = look(b.srv, d["prefab"], d["pos"])
    print(f"BEFORE dedup: {d['prefab']} at {d['pos']} -> {got}")
    if len(got) != d["group"]:
        print(f"REFUSED: plan says {d['group']} at this pivot, the world says "
              f"{len(got)}")
        return 2
    victim = got[-1]
    keep = [o for o in got if o["id"] != victim["id"]]
    r = b.emit("retire", params={
        "prefab": d["prefab"], "pos": d["pos"], "radius_m": LOOKUP_R_M,
        "retires": [0], "role": "town_body",
        "pieces": [{"prefab": d["prefab"], "pos": d["pos"],
                    "radius_m": LOOKUP_R_M, "keeps": d["keeps"],
                    "zdo_id": victim["id"]}],
        "reason": (f"DUPLICATE one-piece round trip: {d['group']} "
                   f"{d['prefab']} stand at one pivot "
                   f"({d['pos'][0]:.3f}, {d['pos'][1]:.3f}, "
                   f"{d['pos'][2]:.3f}) within {S.DUP_TOL_M:.2f} m on all "
                   f"three axes.  {d['remove']} of them are BURIED: "
                   f"{d['buried_fraction']:.3f} of the piece's own interior "
                   f"probe grid lies inside a sibling, against a limit of "
                   f"{S.DUP_BURIED_FRACTION}.  Removing it moves nothing and "
                   f"opens nothing.  {d['keeps']} remain standing and that "
                   f"is the postcondition, because `expect.absent` is an XZ "
                   f"CYLINDER with no height bound and the survivor answers "
                   f"on the victim's behalf.")},
        wire=[f"deleteObjects -id {victim['id']} -force"],
        expect={"prefab_count": [{"prefab": d["prefab"], "pos": [d["pos"][0],
                                                                 d["pos"][2]],
                                  "max": LOOKUP_R_M, "count": d["keeps"]}]},
        meta={"zdo_id_removed": victim["id"],
              "zdo_ids_kept": [o["id"] for o in keep],
              "stage": "one_piece_round_trip",
              "players_probe_ms": round(b.srv.probe(), 1)})
    print("retire ->", r["status"], r.get("checks"))
    after = look(b.srv, d["prefab"], d["pos"])
    print(f"AFTER dedup (live): {after}")
    out["dedup"] = {"seq": r["seq"], "removed": victim["id"],
                    "kept": [o["id"] for o in keep], "pos": d["pos"],
                    "prefab": d["prefab"]}
    r = b.emit("save", params={"role": "town_body"}, wire=["save"],
               expect={"rotation": [{"prefab": f["prefab"], "pos": f["pos"],
                                     "rotation_deg": tgt, "count": 1}]},
               meta={"why": "the one-piece round trip must survive a SAVE: "
                            "SetRotation on a live ZDO is not the same claim "
                            "as a rotation that survives serialisation, so "
                            "the save's own postcondition re-reads the pose "
                            "it was asked to flush"})
    print("save ->", r["status"])
    out["save_seq"] = r["seq"]
    print(json.dumps(out, indent=1))
    (args.out or Path("/tmp/repose_probe.json")).write_text(
        json.dumps(out, indent=1))
    return 0


def _apply(b, plan, args) -> int:
    done = {"flip": 0, "dedup": 0, "records": [], "skipped": []}
    if args.what in ("dedup", "both"):
        done.update(_apply_dedup(b, plan, args, done))
    if args.what in ("flip", "both"):
        done.update(_apply_flip(b, plan, args, done))
    anchor = (plan["dedup"] or plan["flips"])[0]
    r = b.emit("save", params={"role": "town_body"}, wire=["save"],
               expect={"prefab_count": [
                   {"prefab": anchor["prefab"],
                    "pos": [anchor["pos"][0], anchor["pos"][2]],
                    "max": LOOKUP_R_M,
                    "count": anchor.get("column_after",
                                        anchor.get("keeps", 1))}]},
               meta={"why": "flush the campaign to disk before the gate "
                            "re-reads the save"})
    done["save_seq"] = r["seq"]
    print(json.dumps({k: v for k, v in done.items() if k != "records"},
                     indent=1)[:4000])
    Path("/tmp/repose_apply.json").write_text(json.dumps(done, indent=1))
    return 0


def _apply_dedup(b, plan, args, done) -> dict:
    todo = plan["dedup"][:args.limit or None]
    batch: list[dict] = []
    for d in todo:
        try:
            got = look(b.srv, d["prefab"], d["pos"])
        except RuntimeError as exc:
            done["skipped"].append({"why": str(exc)[:200], **d})
            continue
        if len(got) == d["keeps"]:
            done["already"] = done.get("already", 0) + d["remove"]
            continue
        if len(got) != d["group"]:
            done["skipped"].append({"why": f"world has {len(got)}, plan says "
                                           f"{d['group']}", **d})
            continue
        victims = got[d["keeps"]:]
        batch.append({"d": d, "victims": victims,
                      "kept": [o["id"] for o in got[:d["keeps"]]]})
        if sum(len(e["victims"]) for e in batch) >= args.batch:
            done["dedup"] += _emit_dedup(b, batch, done)
            batch = []
    if batch:
        done["dedup"] += _emit_dedup(b, batch, done)
    return done


def _emit_dedup(b, batch, done) -> int:
    # TWO PIVOTS IN ONE BATCH CAN SHARE A COLUMN.  Each entry's `col_before`
    # was measured through an XZ CYLINDER before ANY of this batch's deletes,
    # so the count a postcondition will read afterwards is `col_before` minus
    # EVERY victim in the batch that stands in that cylinder -- not just this
    # pivot's own.  MEASURED: seq 5051 asserted 3 and read 2 on two
    # `wood_pole_log_4` stacked in one column, both in the same batch.  The
    # subtraction is over the victims' OWN positions and the SAME radius the
    # cylinder uses, because any other radius counts a piece the query will
    # not.
    # `srv.count` goes through the console bridge with a 2 s settle, so
    # asking it once per pivot costs 2,404 x 2 s on this campaign -- more
    # than two hours of wall clock to answer a question only the SAMPLED
    # pivots need.  It is asked only for the entries that carry an `expect`.
    vic = [(o["prefab"], o["pos"][0], o["pos"][2])
           for e in batch for o in e["victims"]]
    for e in batch[:EXPECT_SAMPLE]:
        d = e["d"]
        col_before, _ = b.srv.count(d["prefab"], d["pos"][0], d["pos"][2],
                                    LOOKUP_R_M)
        gone = sum(1 for pf, vx, vz in vic
                   if pf == d["prefab"]
                   and math.hypot(vx - d["pos"][0], vz - d["pos"][2])
                   <= LOOKUP_R_M + 1e-9)
        e["column_after"] = col_before - gone
    pieces, wire, ids = [], [], []
    for e in batch:
        d = e["d"]
        pieces.append({"prefab": d["prefab"], "pos": d["pos"],
                       "radius_m": LOOKUP_R_M, "keeps": d["keeps"],
                       "zdo_id": ",".join(o["id"] for o in e["victims"])})
        for o in e["victims"]:
            wire.append(f"deleteObjects -id {o['id']} -force")
            ids.append(o["id"])
    first = pieces[0]
    n = len(wire)
    r = b.emit("retire", params={
        "prefab": first["prefab"], "pos": first["pos"],
        "radius_m": LOOKUP_R_M, "retires": [0], "role": "town_body",
        "pieces": pieces,
        "reason": (f"DUPLICATES: {n} pieces across {len(pieces)} pivots are "
                   f"BURIED COPIES -- at least "
                   f"{S.DUP_BURIED_FRACTION} of each one's own interior "
                   f"probe grid lies inside a sibling of the same prefab at "
                   f"the same pivot, so removing it moves nothing, opens "
                   f"nothing and no building loses a piece.  Each entry's "
                   f"`keeps` is the number STILL STANDING there afterwards, "
                   f"which is the postcondition: `expect.absent` is an XZ "
                   f"cylinder with no height bound and a surviving twin "
                   f"answers on the removed piece's behalf.  Removed by ZDO "
                   f"id because a radius cannot tell two pieces at one pivot "
                   f"apart; the ids are in `meta` as evidence and are NOT "
                   f"the key -- they do not survive a container restart.")},
        wire=wire,
        expect={"prefab_count": [
            {"prefab": e["d"]["prefab"],
             "pos": [e["d"]["pos"][0], e["d"]["pos"][2]],
             "max": LOOKUP_R_M, "count": e["column_after"]}
            for e in batch[:EXPECT_SAMPLE]]},
        meta={"zdo_ids_removed": ids, "pivots": len(pieces),
              "stage": "duplicate_campaign",
              "players_probe_ms": round(b.srv.probe(), 1)})
    done["records"].append({"seq": r["seq"], "op": "retire", "pieces": n})
    print(f"  retire seq {r['seq']}: {n} pieces at {len(pieces)} pivots -> "
          f"{r['status']}")
    return n


def _apply_flip(b, plan, args, done) -> dict:
    todo = plan["flips"][:args.limit or None]
    batch = []
    for f in todo:
        try:
            got = look(b.srv, f["prefab"], f["pos"],
                       plan_yaw=f["yaw_deg"])
        except RuntimeError as exc:
            done["skipped"].append({"why": str(exc)[:200], **f})
            continue
        want_src = round(f["yaw_deg"], 1)
        hit = [o for o in got if abs(((o["rot"][1] - want_src + 180) % 360)
                                     - 180) <= 1.0]
        if len(hit) != 1:
            done["skipped"].append({"why": f"{len(got)} at pivot, "
                                           f"{len(hit)} at yaw {want_src}",
                                    **f})
            continue
        o = hit[0]
        src = [round(v, 3) for v in o["rot"]]
        tgt = [src[0], round((src[1] + 180.0) % 360.0, 3), src[2]]
        batch.append({"f": f, "o": o, "src": src, "tgt": tgt,
                      "at_pivot": len(got)})
        if len(batch) >= args.batch:
            done["flip"] += _emit_flip(b, batch, done)
            batch = []
    if batch:
        done["flip"] += _emit_flip(b, batch, done)
    return done


def _emit_flip(b, batch, done) -> int:
    pieces, wire = [], []
    for e in batch:
        f = e["f"]
        pieces.append({"prefab": f["prefab"], "pos": f["pos"],
                       "rotation_deg": e["tgt"],
                       "from_rotation_deg": e["src"],
                       "toward_before": f["toward"],
                       "zdo_id": e["o"]["id"]})
        wire.append(f"modifyObject {e['o']['id']} -rotation "
                    f"{e['tgt'][0]:g} {e['tgt'][1]:g} {e['tgt'][2]:g}")
    first = pieces[0]
    r = b.emit("modify", params={
        "prefab": first["prefab"], "pos": first["pos"],
        "rotation_deg": first["rotation_deg"],
        "from_rotation_deg": first["from_rotation_deg"],
        "role": "town_body", "pieces": pieces,
        "reason": (f"ORIENTATION: {len(pieces)} roof panels rise AWAY from "
                   f"their own ridge -- dot(own rise direction, direction to "
                   f"the nearest point of their own body's ridge) is "
                   f"NEGATIVE, measured on each panel's own rotated geometry "
                   f"from piece_geometry.json.  Yawed 180 degrees in place. "
                   f"The pose is the whole change: the pivot, the footprint "
                   f"and the AABB are all invariant under it, which is how "
                   f"349 inverted panels were once certified '349/349 at "
                   f"tolerance 0'.  So the postcondition is the rotation "
                   f"itself, read back off each object.")},
        wire=wire,
        # EVERY PANEL, NOT A SAMPLE.  `EXPECT_SAMPLE` exists because a
        # DEDUP postcondition is `srv.count`, which goes through the console
        # bridge with a 2 s settle -- one per pivot is over two hours of
        # wall clock on a 2,404-pivot campaign.  A ROTATION postcondition is
        # a bare `findObjects -detailed` at the pivot with NO settle, the
        # same call `_apply_flip` already makes once per panel to find the
        # id, so there was never a cost reason to sample it -- the 8 was
        # borrowed from a constraint that does not apply here.  And a flip
        # is the one mutation whose whole point is that it is INVISIBLE to
        # count, position and AABB: a sampled postcondition over 52 panels
        # certifies 8 and asserts nothing whatever about the other 44,
        # which is the same shape as "349/349 at tolerance 0" over 349
        # inverted panels.  A repair is only ever as wide as the check that
        # names its population, so the check is the population.
        expect={"rotation": [{"prefab": e["f"]["prefab"], "pos": e["f"]["pos"],
                              "rotation_deg": e["tgt"],
                              "count": e["at_pivot"],
                              "matching": 1}
                             for e in batch]},
        meta={"zdo_ids": [e["o"]["id"] for e in batch],
              "stage": "orientation_campaign",
              "players_probe_ms": round(b.srv.probe(), 1)})
    done["records"].append({"seq": r["seq"], "op": "modify",
                            "pieces": len(pieces),
                            "schema_problems": r.get("schema_problems")})
    print(f"  modify seq {r['seq']}: {len(pieces)} panels -> {r['status']}")
    # THE ONLY THING A DRY RUN CAN PROVE, PRINTED.  `emit(dry=True)` builds
    # the record, runs `schema.validate` and hands the problems back in
    # `schema_problems` -- and this caller used to print `status` alone, so
    # "dry-run: validated" appeared on screen whether the list was empty or
    # named six faults.  A dry run whose verdict is invisible is a dry run
    # nobody can act on.
    if r.get("schema_problems"):
        print(f"    SCHEMA PROBLEMS ({len(r['schema_problems'])}): "
              f"{r['schema_problems']}")
    elif r["seq"] is None:
        print(f"    schema clean: 0 problems over {len(pieces)} piece(s), "
              f"{len(wire)} wire line(s)")
    return len(pieces)


if __name__ == "__main__":
    raise SystemExit(main())
