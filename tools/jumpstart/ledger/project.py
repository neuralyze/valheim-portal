#!/usr/bin/env python3
"""Fold the append-only ledger down to FINAL STATE, then measure that
projection against a world.

WHY A PROJECTION AND NOT A HISTORY REPLAY
=========================================
`replay.py` replays the ledger as a HISTORY: record by record, in file order,
postcondition by postcondition.  That is the right instrument for resuming an
interrupted build and the wrong one for rebuilding a world, because the
history contains its own mistakes:

  * a lighthouse built to 272 of 1255 pieces and then retired whole,
  * 18 portal arches placed, retired, and re-placed inside a hall,
  * 764 objects cleared and then cleared again,
  * a road graded, re-graded and re-battered four times,
  * 64 duplicate pieces found and de-duplicated,
  * a Longship berthed and then retired as aground.

Replaying that literally repeats twenty hours of defects.  So this module
FOLDS it: for each (prefab, world position) it decides what EXISTS NOW after
every retire, clear and re-place, and emits only that.

THE KEY IS `prefab` + WORLD POSITION, NOT ZDO ID
================================================
Deliberately, and the ledger says why: ZDO ids are reassigned on load.  A
de-duplication pass MEASURED that after one restart "every single id
different -- 23163:1 -> 22958:1, 76580:1 -> 76288:1, and the offset isn't
even constant".  An id-keyed fold would therefore treat one object as two
across any restart boundary, and the ledger spans many.  Position survives.

WORLD POSITION MEANS ALL THREE AXES, and for a long time this module said
so while keying on two.  `qkey` was `(prefab, u, v)`: every piece standing
on another piece collided with it and only the last one survived.  MEASURED
on this ledger: of 12,550 key collisions, 689 were true duplicates and
**11,861 were stacked pieces the fold destroyed** -- 36% of everything ever
placed, worst case 38.00 m apart vertically.  Every multi-storey structure
folded down to a single course.  The key is now 3D; see `qkey`.

AXIS ORDER: MEASURED, AND THE TWO CARRIERS DISAGREE
===================================================
`params["pos"]` and the `spawn_object` wire string do NOT list the axes in
the same order.  MEASURED on seq 3 (`spawn`, wood_pole2):

    params pos = [13.62, 32.316, -256.38]
    wire       = spawn_object wood_pole2 pos=-256.3787,13.6213,32.3161

so wire[0] == params[2], wire[1] == params[0], wire[2] == params[1], and the
same relation holds on `portal` and on every `spawn_plan` command checked
against its own `anchor` (bridge-s1-temple-strait: anchor x=-318.0 z=-64.0,
wire pos=-66,-310,30.5 -> wire[1]=-310~=anchor.x, wire[0]=-66~=anchor.z).
One of the two carriers has its axes transposed; the world cannot tell us
which label is "x" because the world was built BY the wire.

This module therefore takes the WIRE as authoritative -- it is the byte
sequence the game actually accepted -- and reports positions in the ledger's
`params` frame (u = params[0] = wire[1], v = params[2] = wire[0]).  A rebuild
that re-derives commands from `params["pos"]` instead of replaying the wire
would mirror the entire world across u=v.  That is a finding, not a detail.
"""

from __future__ import annotations

import argparse
import base64
import collections
import fnmatch
import hashlib
import json
import math
import os
import re
import sys
import struct
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "terraform"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
if str(HERE.parent) not in sys.path:
    sys.path.append(str(HERE.parent))
import worldseed  # noqa: E402

# `spawn_object <prefab> pos=z,x,y rot=a,b,c from=... [data=...]`
SPAWN_RE = re.compile(
    r"^spawn_object\s+(?P<prefab>\S+)"
    r"(?=.*\bpos=(?P<pos>-?[\d.]+,-?[\d.]+,-?[\d.]+))"
    r"(?:.*?\brot=(?P<rot>-?[\d.]+,-?[\d.]+,-?[\d.]+))?"
    r"(?:.*?\bdata=(?P<data>\S+))?")

# Position quantum for the durable key.  The wire carries 4 decimals; two
# distinct pieces are never closer than centimetres, and 1 mm is finer than
# any float round-trip in the chain.
QUANT = 1000.0

# The vessel prefabs THIS LEDGER ACTUALLY USES, not the ones a Valheim
# glossary would list.  MEASURED from the fold's own prefab histogram:
# CargoShip x5 and LittleBoat x1 survive; the Longship the ledger mentions
# was retired as aground and is correctly absent.  A hardcoded
# Raft/Karve/VikingShip set reported ZERO vessels against a world holding
# six, which is the failure mode where a category is defined from memory
# instead of from the data.
VESSEL_PREFABS = {"Raft", "Karve", "VikingShip", "Longship", "Cart",
                  "CargoShip", "LittleBoat"}

# THE KEY IS THREE-DIMENSIONAL, AND THE QUANTUM IS MEASURED, NOT CHOSEN
# ---------------------------------------------------------------------
# `qkey` once keyed on (prefab, u, v) only, so every piece standing on
# another piece collided with it and only the last one survived.  The
# quantum for y comes from measuring the two populations the old key was
# conflating, over the whole 32,946-command stream of `runs/Ulfsland`:
#
#   distinct 3D identities .... 30,499     repeated commands ....  2,447
#   distinct 2D identities .... 19,925     repeated commands .... 13,021
#                                          -> 10,574 stacked pieces were
#                                             being called duplicates
#
# THE POPULATIONS DO NOT OVERLAP, and the gap is wide:
#
#   true-duplicate dy   : exactly 0.0 m, for every one of them.  Not
#                         "small" -- ZERO.  Both sides are parsed from the
#                         same 4-decimal wire text, so a re-emitted command
#                         reproduces the float bit-for-bit.  There is no
#                         float noise for a tolerant quantum to absorb.
#   stacked-piece dy    : minimum 0.0497 m, p50 2.00 m, max 38.00 m.
#                         Exactly 1 pair of 11,861 is under 0.05 m, 19 under
#                         0.5 m; the mass sits on the 0.5/1/2/4 m course
#                         pitches the builders actually use.
#
# So any quantum below 49.7 mm separates every stacked pair while still
# collapsing every true duplicate.  QUANT = 1000.0 (1 mm) is used for y for
# the same reason it is used for u and v -- 49.7x finer than the closest
# real separation, and an isotropic key is one fact rather than three.  A
# coarser "tolerant" y would buy nothing: the noise population is empty.
#
# A WARNING ABOUT MEASURING A BUG WITH THE BUG STILL IN.  Instrumenting the
# OLD fold reported only 689 same-y collisions against 11,861 different-y
# ones, which reads as "689 true duplicates".  It was wrong, and wrong
# because of the defect being measured: by the time a re-emitted command
# arrived, the 2D key had already replaced the original with a DIFFERENT
# piece from the same column, so the fold compared the duplicate against a
# stranger and scored it different-y.  1,302 of the 11,861 were true
# duplicates mislabelled that way.  Count identities in the command stream,
# which no fold order can distort, rather than collisions inside the fold.


def qkey(prefab: str, u: float, v: float, y: float) -> tuple:
    return (prefab, round(u * QUANT), round(v * QUANT), round(y * QUANT))


def strings_in(blob: bytes) -> list[str]:
    """Printable runs out of a ZDO `data=` payload.

    The payload is a ZPackage: length-prefixed UTF-8 among binary fields.  A
    full parser would need the ZDO field layout for every prefab; the strings
    are what a human needs (a portal tag, a sign's text) and a printable-run
    scan gets them without pretending to understand the rest.  INFERRED
    structure, MEASURED result: seq 4's portal payload yields "x-harbour",
    which matches that record's own `params["tag"]`.
    """
    out, cur = [], bytearray()
    for b in blob:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= 3:
                out.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= 3:
        out.append(cur.decode("ascii"))
    return out


class Projection:
    """The fold.  One pass, in FILE ORDER, which is the only total order the
    artefact has (seq labels duplicate across a documented concurrent-append
    fork -- see `replay.chain_report`)."""

    def __init__(self, ledger: Path):
        self.ledger = Path(ledger)
        self.records: list[dict] = []
        for raw in self.ledger.read_text("utf-8").splitlines():
            raw = raw.strip()
            if raw:
                self.records.append(json.loads(raw))

        self.placements: dict[tuple, dict] = {}
        self.terrain: dict[tuple, dict] = {}
        self.clears: dict[tuple, dict] = {}
        self.zones_generated: dict[tuple, dict] = {}
        self.zones_restored: list[dict] = []
        self.generate_wires: dict[str, dict] = {}
        self.events: list[dict] = []
        self.stats = collections.Counter()
        self.unparsed: list[dict] = []

    # -- helpers -----------------------------------------------------------

    def _place(self, line: int, rec: dict, prefab: str, u: float, v: float,
               y: float, rot: str | None, data: str | None, wire: str) -> None:
        key = qkey(prefab, u, v, y)
        prior = self.placements.get(key)
        if prior is not None:
            # A collision on the 3D key is a genuinely re-emitted command:
            # same prefab, same millimetre, same height.  It is NOT a piece
            # standing on another one -- that case now has its own key.
            self.stats["collapsed_duplicate_commands"] += 1
            self.events.append({"kind": "collapse", "line": line,
                                "prefab": prefab, "pos": [u, v], "y": y,
                                "first_line": prior["line"]})
        p = {"prefab": prefab, "u": u, "v": v, "y": y, "rot": rot,
             "data_b64": data, "line": line, "seq": rec["seq"],
             "op": rec["op"], "actor": rec["actor"],
             "site_id": rec["params"].get("site_id"),
             "role": rec["params"].get("role"), "wire": wire}
        if rec["op"] == "portal":
            p["portal_tag"] = rec["params"].get("tag")
            p["pair_tag"] = rec["params"].get("pair_tag")
        self.placements[key] = p
        self.stats["placements_applied"] += 1

    def _remove(self, pred, why: str, line: int, rec: dict) -> list[dict]:
        gone = [k for k, p in self.placements.items() if pred(p)]
        for k in gone:
            p = self.placements.pop(k)
            self.events.append({"kind": "remove", "why": why, "line": line,
                                "op": rec["op"], "prefab": p["prefab"],
                                "pos": [p["u"], p["v"]], "y": p["y"],
                                "placed_at_line": p["line"]})
        return gone

    # -- the fold ----------------------------------------------------------

    def fold(self) -> None:
        for line, rec in enumerate(self.records):
            op = rec["op"]
            p = rec.get("params") or {}
            handler = getattr(self, f"_op_{op}", None)
            if handler is None:
                self.stats[f"ignored_{op}"] += 1
                continue
            handler(line, rec, p)

    def _op_spawn(self, line, rec, p) -> None:
        for cmd in self._commands(rec):
            m = SPAWN_RE.match(cmd)
            if not m:
                self.unparsed.append({"line": line, "cmd": cmd[:160]})
                continue
            a, b, c = (float(t) for t in m.group("pos").split(","))
            self._place(line, rec, m.group("prefab"), b, a, c,
                        m.group("rot"), m.group("data"), cmd)

    _op_portal = _op_spawn
    _op_spawn_plan = _op_spawn

    def _commands(self, rec) -> list[str]:
        out = []
        for w in rec.get("wire") or []:
            for cmd in str(w).split(";"):
                cmd = cmd.strip()
                if cmd.startswith("spawn_object"):
                    out.append(cmd)
        return out

    # How far a retire may reach beyond its own radius when its radius finds
    # nothing.  MEASURED, and it is not slop: a retire's centre is a LIVE
    # MEASUREMENT (`findObjects` told the builder where the object actually
    # was) while a spawn's position is a REQUEST, and the two differ.  Seq 854
    # retires `wood_pole_log` at [-275.3433, 37.2630, 224.6567] with r=0.6,
    # while seq 927 spawned that pole at [-275.34, 37.95, 224.007] -- 0.95 m
    # apart in 3D, so the strict sphere misses the very object the record
    # names.  The live save PROVES the retire won: there is no wood_pole_log
    # within 0.05 m of the spawned position, and the nearest one is 5.78 m
    # away.  Widening is therefore what agrees with the world; refusing to
    # widen leaves a retired object standing in the projection.
    #
    # RETIRE AND PLACEMENT NOW AGREE ABOUT WHAT "THE SAME OBJECT" IS.  They
    # did not before: the retire sphere below has always compared (u, y, v)
    # in 3D while `qkey` compared (u, v) only, so a retire could distinguish
    # two pieces the key had already merged into one.  The disagreement was
    # not symmetric either -- the key merged FIRST, at place time, so by the
    # time a retire ran, the piece it might have spared was already gone.
    # With `qkey` 3D both sides mean the same thing, and the slack below is
    # the one remaining deliberate difference: identity is exact to the
    # millimetre, while a retire is allowed to reach 1.5 m because its
    # centre is a live measurement of where an object IS and the key's
    # position is a record of where one was ASKED FOR.  That difference is
    # between two different kinds of number, not between two dimensions.
    RETIRE_MEASUREMENT_SLACK_M = 1.5

    def _op_retire(self, line, rec, p) -> None:
        # ONE RECORD MAY RETIRE MANY PIECES.  This method used to model
        # EXACTLY ONE sphere out of `params` and ignore both the wire and
        # any batch, so a record carrying 50 `deleteObjects` lines removed
        # one placement here and left 49 standing in the projection -- the
        # ledger asserting something true about the log and false about the
        # world, which is the one failure a retire record exists to
        # prevent.  `params.pieces` is the batch and `params.prefab`/`pos`/
        # `radius_m` mirror `pieces[0]` (enforced in schema), so folding
        # `pieces` when present and the single sphere otherwise covers both
        # forms with no double-count.
        for e in (p.get("pieces") or [dict(prefab=p["prefab"], pos=p["pos"],
                                           radius_m=p["radius_m"])]):
            self._retire_one(line, rec, e["prefab"], e["pos"],
                             float(e.get("radius_m", p["radius_m"])))

    def _retire_one(self, line, rec, prefab, pos, r: float) -> None:
        x, y, z = pos
        # The wire is `deleteObjects -prefab X -near x y z r`, a 3D sphere in
        # the SAME frame as params["pos"] -- MEASURED: seq 6's wire reads
        # `-near 481.10 47.03 920.40 20.00` against params pos
        # [481.1, 47.03, 920.4], i.e. straight through with no transposition.
        # So a retire is compared in the params frame (u, y, v).
        #
        # A CO-LOCATED PAIR IS RETIRED BY ID ON THE WIRE AND BY POSITION
        # HERE, AND THE TWO DO NOT MEAN THE SAME THING.  `deleteObjects -id`
        # removes exactly ONE of two pieces at one pivot; this predicate
        # removes every placement inside the sphere, which at a shared pivot
        # is both.  That is why a piece that must SURVIVE is counted in the
        # entry's `keeps`, and spared below.
        keeps = 0
        for e in (rec.get("params", {}).get("pieces") or []):
            if e.get("prefab") == prefab \
                    and list(e.get("pos") or []) == list(pos):
                keeps = int(e.get("keeps") or 0)
                break

        inside = sorted(
            (k for k, q in self.placements.items()
             if q["prefab"] == prefab
             and math.dist((q["u"], q["y"], q["v"]), (x, y, z)) <= r + 1e-6),
            key=lambda k: self.placements[k]["line"])
        if inside:
            # The sphere cannot tell two pieces at ONE pivot apart, but
            # `deleteObjects -id` on the wire removed exactly one of them.
            # `keeps` is how many same-prefab placements inside this sphere
            # the record says are STILL STANDING; the oldest survive, so a
            # replay's projection keeps the piece the world kept instead of
            # claiming a hole that is not there.
            for k in inside[keeps:]:
                q = self.placements.pop(k)
                self.stats["retire_removed"] += 1
                self.events.append({"kind": "remove", "why": "retire",
                                    "line": line, "op": rec["op"],
                                    "prefab": q["prefab"],
                                    "pos": [q["u"], q["v"]], "y": q["y"],
                                    "placed_at_line": q["line"]})
            self.stats["retire_kept_colocated_twin"] += min(keeps, len(inside))
            return
        # Nothing inside the sphere: take the NEAREST same-prefab placement
        # inside the slack, and say so in the events so the number is never
        # mistaken for an exact match.
        best, bestd = None, None
        for k, q in self.placements.items():
            if q["prefab"] != prefab:
                continue
            d = math.dist((q["u"], q["y"], q["v"]), (x, y, z))
            if d <= r + self.RETIRE_MEASUREMENT_SLACK_M and (bestd is None
                                                             or d < bestd):
                best, bestd = k, d
        if best is not None:
            q = self.placements.pop(best)
            self.stats["retire_removed_fuzzy"] += 1
            self.events.append({"kind": "remove", "why": "retire-fuzzy",
                                "line": line, "op": "retire",
                                "prefab": prefab, "pos": [q["u"], q["v"]],
                                "y": q["y"],
                                "retire_centre": [x, y, z],
                                "offset_m": round(bestd, 3),
                                "placed_at_line": q["line"]})
            return
        self.stats["retire_matched_nothing"] += 1
        self.events.append({"kind": "retire-empty", "line": line,
                            "prefab": prefab, "pos": [x, z], "radius": r})

    # A POSE CORRECTION THE PROJECTION HAS TO LEARN, OR THE DEFECT COMES
    # BACK.  `modify` changes a placed piece's rotation in the world; if the
    # fold ignored it, a replay would rebuild every corrected panel at the
    # OLD yaw and the 373-panel inversion would return on the next
    # regeneration -- which is the whole point of the artefact.
    #
    # The stored `rot` is the string a `spawn_object` wire carries, and that
    # is euler `y,x,z` while `modifyObject -rotation` is `x,y,z`.  The two
    # commands disagree; the conversion happens HERE, once, rather than at
    # every reader.
    def _op_modify(self, line, rec, p) -> None:
        for e in (p.get("pieces") or [p]):
            x, y, z = e["pos"]
            prefab = e["prefab"]
            rx, ry, rz = e["rotation_deg"]
            best, bestd = None, None
            for k, q in self.placements.items():
                if q["prefab"] != prefab:
                    continue
                d = math.dist((q["u"], q["y"], q["v"]), (x, y, z))
                if d <= 0.25 and (bestd is None or d < bestd):
                    best, bestd = k, d
            if best is None:
                self.stats["modify_matched_nothing"] += 1
                self.events.append({"kind": "modify-empty", "line": line,
                                    "prefab": prefab, "pos": [x, z], "y": y})
                continue
            q = self.placements[best]
            q["rot"] = f"{ry:g},{rx:g},{rz:g}"
            q["rot_source"] = f"modify line {line}"
            self.stats["modify_rotated"] += 1
            self.events.append({"kind": "rotate", "line": line, "op": "modify",
                                "prefab": prefab, "pos": [q["u"], q["v"]],
                                "y": q["y"],
                                "from_rotation_deg": e.get("from_rotation_deg"),
                                "rotation_deg": [rx, ry, rz],
                                "offset_m": round(bestd, 3),
                                "placed_at_line": q["line"]})

    def _op_objects_clear(self, line, rec, p) -> None:
        # AUDITED against the other keys in this module and this one is 2D
        # ON PURPOSE, not by omission.  `params["centre"]` is a TWO-element
        # list on all 854 clear records and the wire it carries is
        # `objects_remove id=* ignore=_* pos=-318.0,-64.0 max=12.5` -- a
        # vertical cylinder with no y term at all.  Giving this key or this
        # predicate a y would describe a command the game never received.
        cu, cv = (float(t) for t in p["centre"][:2])
        r = float(p["radius_m"])
        ids = p["ids"]
        ids = [ids] if isinstance(ids, str) else list(ids)
        ign = p["ignore"]
        ign = [ign] if isinstance(ign, str) else list(ign)

        def hit(q):
            if math.dist((q["u"], q["v"]), (cu, cv)) > r + 1e-6:
                return False
            if any(fnmatch.fnmatchcase(q["prefab"], g) for g in ign if g):
                return False
            return any(fnmatch.fnmatchcase(q["prefab"], g) for g in ids if g)

        gone = self._remove(hit, "objects_clear", line, rec)
        self.stats["clear_removed_placements"] += len(gone)
        key = (round(cu * QUANT), round(cv * QUANT), round(r * QUANT))
        prior = self.clears.get(key)
        if prior:
            self.stats["clears_superseded"] += 1
        self.clears[key] = {"centre": [cu, cv], "radius_m": r,
                            "ids": ids, "ignore": ign, "line": line,
                            "seq": rec["seq"], "actor": rec["actor"],
                            "site_id": p.get("site_id"),
                            "repeats": (prior or {}).get("repeats", 0) + 1,
                            "wire": list(rec.get("wire") or [])}

    def _op_terrain_write(self, line, rec, p) -> None:
        for e in p["entries"]:
            zone = tuple(e["zone"])
            prior = self.terrain.get(zone)
            if prior:
                self.stats["terrain_superseded"] += 1
            self.terrain[zone] = {
                "zone": list(zone), "centre": e["centre"],
                "blob_sha256": e["blob_sha256"],
                "generated_heights_sha256": e.get("generated_heights_sha256"),
                "data_entry": e["data_entry"],
                "op_record": e.get("op_record"),
                "name": p.get("name"), "paint": p.get("paint"),
                "target_y": p.get("target_y"), "datum": p.get("datum"),
                "line": line, "seq": rec["seq"], "actor": rec["actor"],
                "site_id": p.get("site_id"), "role": p.get("role"),
                "writes_to_this_zone": (prior or {}).get(
                    "writes_to_this_zone", 0) + 1,
            }

    def _op_zones_generate(self, line, rec, p) -> None:
        for z in p.get("zones") or []:
            self.zones_generated.setdefault(tuple(z), {
                "zone": list(z), "line": line, "seq": rec["seq"],
                "site_id": p.get("site_id")})
        # Keep the LITERAL wire too, deduplicated by its own text.  A
        # replay must generate with the same disc the build used: the
        # ledger's own ordering proof is generate -> clear -> flatten ->
        # place, and `flatten.py` REFUSES a compiler write into a zone with
        # no `_ZoneCtrl` at its centre, so a terrain write inherits this
        # step as a hard precondition.  Re-deriving a disc from the zone
        # list would send a different command than the one that worked.
        for w in rec.get("wire") or []:
            w = str(w).strip()
            if w.startswith("zones_generate"):
                self.generate_wires.setdefault(w, {
                    "wire": w, "line": line, "seq": rec["seq"],
                    "site_id": p.get("site_id"),
                    "zones": [list(z) for z in (p.get("zones") or [])]})

    def _op_zones_restore(self, line, rec, p) -> None:
        # A restore puts a zone back to its GENERATED state: every placement
        # inside its 64 m square is gone and so is any terrain write.
        pos = None
        for w in rec.get("wire") or []:
            m = re.search(r"zones_restore\s+pos=(-?[\d.]+),(-?[\d.]+)", str(w))
            if m:
                pos = (float(m.group(1)), float(m.group(2)))
        if pos is None:
            self.stats["zones_restore_unparsed"] += 1
            return
        zx, zz = int(round(pos[0] / 64.0)), int(round(pos[1] / 64.0))
        lo_u, hi_u = zx * 64.0 - 32.0, zx * 64.0 + 32.0
        lo_v, hi_v = zz * 64.0 - 32.0, zz * 64.0 + 32.0
        gone = self._remove(
            lambda q: lo_u <= q["u"] < hi_u and lo_v <= q["v"] < hi_v,
            "zones_restore", line, rec)
        self.terrain.pop((zx, zz), None)
        self.zones_restored.append({"zone": [zx, zz], "pos": list(pos),
                                    "line": line, "seq": rec["seq"],
                                    "removed_placements": len(gone),
                                    "fault": (p.get("fault") or "")[:400]})

    # -- manifest ----------------------------------------------------------

    def manifest(self) -> dict:
        # y is part of the sort for the same reason it is part of the key:
        # without it the order of a stacked column is dict-insertion order,
        # i.e. not reproducible across runs.  Bottom-up within a column.
        placements = sorted(self.placements.values(),
                            key=lambda q: (q["prefab"], q["u"], q["v"],
                                           q["y"]))
        by_prefab = collections.Counter(q["prefab"] for q in placements)

        portals = collections.defaultdict(list)
        for q in placements:
            if not q["prefab"].startswith("portal"):
                continue
            tag = q.get("portal_tag")
            if tag is None and q.get("data_b64"):
                try:
                    got = strings_in(base64.b64decode(q["data_b64"]))
                    tag = got[-1] if got else None
                except Exception:  # noqa: BLE001
                    tag = None
            portals[tag or "<untagged>"].append(
                {"pos": [q["u"], q["v"]], "y": q["y"], "site_id": q["site_id"],
                 "line": q["line"], "prefab": q["prefab"]})

        texts = []
        for q in placements:
            if not q.get("data_b64"):
                continue
            if q["prefab"].startswith("portal"):
                continue
            try:
                got = strings_in(base64.b64decode(q["data_b64"]))
            except Exception:  # noqa: BLE001
                continue
            if got:
                texts.append({"prefab": q["prefab"], "pos": [q["u"], q["v"]],
                              "strings": got, "site_id": q["site_id"],
                              "line": q["line"]})

        vessels = [{"prefab": q["prefab"], "pos": [q["u"], q["v"]],
                    "y": q["y"], "site_id": q["site_id"], "line": q["line"]}
                   for q in placements if q["prefab"] in VESSEL_PREFABS]

        head = None
        try:
            import schema  # noqa: PLC0415
            h = schema.GENESIS_PREV
            for rec in self.records:
                h = schema.digest(rec)
                head = h
        except Exception as exc:  # noqa: BLE001
            head = f"<unavailable: {type(exc).__name__}>"

        return {
            "source": {
                "ledger": str(self.ledger),
                "ledger_sha256": hashlib.sha256(
                    self.ledger.read_bytes()).hexdigest(),
                "records": len(self.records),
                "head": head,
                "folded_at": time.strftime("%FT%TZ", time.gmtime()),
                "frame": "u = params_pos[0] = wire[1]; v = params_pos[2] = "
                         "wire[0]; y = params_pos[1] = wire[2]",
            },
            "counts": {
                "placements": len(placements),
                "distinct_prefabs": len(by_prefab),
                "terrain_zones": len(self.terrain),
                "clears": len(self.clears),
                "zones_generated": len(self.zones_generated),
                "zones_restored": len(self.zones_restored),
                "portal_tags": len(portals),
                "vessels": len(vessels),
                "texts": len(texts),
                **{k: v for k, v in sorted(self.stats.items())},
            },
            "by_prefab": dict(by_prefab.most_common()),
            "placements": placements,
            "terrain": sorted(self.terrain.values(),
                              key=lambda e: (e["zone"][0], e["zone"][1])),
            "clears": sorted(self.clears.values(),
                             key=lambda c: (c["centre"][0], c["centre"][1])),
            "zones_generated": sorted(self.zones_generated.values(),
                                      key=lambda z: tuple(z["zone"])),
            "zones_restored": self.zones_restored,
            "generate_wires": list(self.generate_wires.values()),
            "portals": {k: v for k, v in sorted(portals.items())},
            "vessels": vessels,
            "texts": texts,
            "unparsed_commands": self.unparsed,
        }


# --------------------------------------------------------------------------
# Reconciliation against a LIVE world
# --------------------------------------------------------------------------

def site_clusters(placements: list[dict], reach: float = 24.0) -> list[dict]:
    """Group the projection into bounded census discs.

    Bounded ON PURPOSE.  An `objects_count id=*` over a generated zone answers
    `Total: 6149` followed by one line per prefab, straight into the console
    sink, and that is what wedged the server's stdout.  So the census asks one
    NAMED prefab at a time inside a disc no larger than `reach`.
    """
    buckets: dict[tuple, dict] = {}
    for q in placements:
        key = (round(q["u"] / reach), round(q["v"] / reach))
        b = buckets.setdefault(key, {"u": [], "v": [], "prefabs":
                                     collections.Counter(), "sites": set()})
        b["u"].append(q["u"])
        b["v"].append(q["v"])
        b["prefabs"][q["prefab"]] += 1
        if q["site_id"]:
            b["sites"].add(q["site_id"])
    out = []
    for key, b in buckets.items():
        cu = (min(b["u"]) + max(b["u"])) / 2.0
        cv = (min(b["v"]) + max(b["v"])) / 2.0
        span = max(max(b["u"]) - min(b["u"]), max(b["v"]) - min(b["v"]))
        out.append({"centre": [round(cu, 2), round(cv, 2)],
                    "radius_m": round(span / 2.0 + 1.5, 2),
                    "prefabs": dict(b["prefabs"]),
                    "sites": sorted(b["sites"])})
    return sorted(out, key=lambda c: (-sum(c["prefabs"].values()),
                                      c["centre"]))


# --------------------------------------------------------------------------
# The save file as a census instrument
# --------------------------------------------------------------------------

def stable_hash(s: str) -> int:
    """Valheim's `string.GetStableHashCode`, the prefab id in every ZDO.

    Needed because the save stores the hash, not the name.  Verified against
    the world: hash("CargoShip") = 870910752 sits at offset 54527 of
    `24_1e__1_7.chunk`, exactly 12 bytes after the float triple that equals
    that CargoShip's recorded spawn position.
    """
    a = b = 5381
    i, n = 0, len(s)
    while i < n:
        a = (((a << 5) + a) & 0xFFFFFFFF) ^ ord(s[i])
        if i == n - 1:
            break
        b = (((b << 5) + b) & 0xFFFFFFFF) ^ ord(s[i + 1])
        i += 2
    return struct.unpack("<i", struct.pack(
        "<I", (a + b * 1566083941) & 0xFFFFFFFF))[0]


def scan_save(world_dir: Path, prefabs) -> list[tuple]:
    """Every (prefab, x, y, z) in a world save, for the named prefabs.

    THE LAYOUT IS MEASURED, NOT ASSUMED: a ZDO's `Vector3` position is
    immediately followed by its prefab's stable hash, so a scan for
    "plausible position + known hash at +12" finds every object of a named
    prefab without parsing the variable-length remainder of the record.

    It can over-report (a coincidental 16 bytes) and that is the direction
    that matters least here: the question asked of it is "is the object the
    projection claims at this exact position actually there", and for that a
    false positive would have to land on a projected position with the right
    hash.  CORROBORATED against the live server on Ulfsland: 7 bounded
    `objects_count` probes, 7 agreements, including three zeros, a
    20-versus-20 disc census and a 0-versus-0 one -- i.e. instruments that
    were capable of disagreeing.
    """
    h2n = {}
    for name in prefabs:
        h2n[stable_hash(name)] = name
    rows = []
    for f in sorted(Path(world_dir).glob("*.chunk")):
        b = f.read_bytes()
        # `len(b) - 15`, not `len(b) - 16`.  The window is 16 bytes read at
        # `i`, so the last legal start is `len(b) - 16` and `range` must be
        # given one more than that.  The old bound never tested that offset,
        # so an object whose record ended a chunk file was invisible to the
        # census -- and the census only ever UNDER-reports the world, which
        # is the direction that flatters a fidelity figure.
        for i in range(len(b) - 15):
            name = h2n.get(struct.unpack_from("<i", b, i + 12)[0])
            if name is None:
                continue
            x, y, z = struct.unpack_from("<fff", b, i)
            if not (-12000.0 < x < 12000.0 and -600.0 < y < 1200.0
                    and -12000.0 < z < 12000.0):
                continue
            if abs(x) < 0.001 and abs(z) < 0.001:
                continue
            rows.append((name, x, y, z))
    # One ZDO is one row; identical (prefab, position) triples are the same
    # object seen through two overlapping windows.
    return sorted(set((n, round(x, 3), round(y, 3), round(z, 3))
                      for n, x, y, z in rows))


def reconcile(manifest: dict, world_dir: Path, tol_m: float = 0.05) -> dict:
    """Per-prefab placed-versus-projected, at a stated tolerance.

    THE CENSUS IS 3D AND ONE-TO-ONE, and both halves of that had to be
    fixed together with `qkey`.  It previously asked only "is there a
    same-prefab object within `tol_m` in x and z", which is the SAME dropped
    dimension the placement key had: a projected row was satisfied by any
    object anywhere in its vertical column.  A y-blind fold reconciled by a
    y-blind census agrees with itself by construction -- the two errors
    cancel and the fidelity reads ~100% while a five-course wall is being
    compared against one course.  It also matched WITHOUT CONSUMING, so one
    live object could satisfy every projected row in its column.

    It is still only one direction -- `projection is a subset of world`.
    `live_rows_unclaimed` is the other direction, reported rather than
    scored, because `scan_save` deliberately over-reports and the world also
    legitimately holds objects no ledger record claims.
    """
    prefabs = set(manifest["by_prefab"])
    live = scan_save(world_dir, prefabs)
    grid: dict[tuple, list] = {}
    for n, x, y, z in live:
        grid.setdefault((n, round(x / 1.0), round(z / 1.0)),
                        []).append([x, y, z, False])

    def present(prefab, u, v, w):
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                for row in grid.get((prefab, round(u) + du,
                                     round(v) + dv), ()):
                    x, y, z, used = row
                    if used:
                        continue
                    if (abs(x - u) <= tol_m and abs(z - v) <= tol_m
                            and abs(y - w) <= tol_m):
                        row[3] = True
                        return True
        return False

    hit = collections.Counter()
    missing: list[dict] = []
    for q in manifest["placements"]:
        if present(q["prefab"], q["u"], q["v"], q["y"]):
            hit[q["prefab"]] += 1
        else:
            missing.append({"prefab": q["prefab"], "pos": [q["u"], q["v"]],
                            "y": q["y"], "site_id": q["site_id"],
                            "seq": q["seq"], "actor": q["actor"]})
    unclaimed = sum(1 for rows in grid.values() for r in rows if not r[3])
    projected = manifest["by_prefab"]
    per_prefab = {p: {"projected": projected[p], "placed": hit.get(p, 0)}
                  for p in sorted(projected)}
    total_p = sum(projected.values())
    total_h = sum(hit.values())
    return {
        "tolerance_m": tol_m,
        "matched": "3D, one-to-one: a live object satisfies at most one "
                   "projected placement",
        "world_dir": str(world_dir),
        "live_rows_scanned": len(live),
        "live_rows_unclaimed": unclaimed,
        "placements": {"projected": total_p, "placed": total_h,
                       "fidelity_pct": round(100.0 * total_h / total_p, 3)
                       if total_p else None},
        "per_prefab_short": {p: v for p, v in per_prefab.items()
                             if v["placed"] != v["projected"]},
        "per_prefab": per_prefab,
        "missing": missing,
    }


def verify_terrain(manifest: dict, blobs: Path, world_dir: Path) -> dict:
    """Did the terrain land byte-for-byte?

    The recorded blob IS the `TCData` byte array the compiler carries, so the
    honest test is whether those exact bytes are in the save.  No tolerance,
    no proxy, no compiler count standing in for a heightfield.
    """
    chunks = [f.read_bytes() for f in sorted(Path(world_dir).glob("*.chunk"))]
    ok, missing, absent_blob = 0, [], []
    for e in manifest["terrain"]:
        path = Path(blobs) / e["blob_sha256"]
        if not path.is_file():
            absent_blob.append(e["blob_sha256"])
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != e["blob_sha256"]:
            absent_blob.append(e["blob_sha256"] + " (corrupt)")
            continue
        if any(data in c for c in chunks):
            ok += 1
        else:
            missing.append({"zone": e["zone"], "name": e["name"],
                            "site_id": e["site_id"], "bytes": len(data),
                            "blob": e["blob_sha256"][:12]})
    total = len(manifest["terrain"])
    return {"zones": total, "byte_exact": ok,
            "agreement_pct": round(100.0 * ok / total, 3) if total else None,
            "missing": missing, "blob_problems": absent_blob}


# --------------------------------------------------------------------------
# Replay of the projection
# --------------------------------------------------------------------------

# A batch is capped by BYTES first and commands second.  `ValheimRcon`'s
# parser rejects a request over 4050 bytes and the reply is the request plus
# `Command '' executed.`, so the reply is the binding side.  48 commands is
# the ceiling the build path uses; at ~95 B per `spawn_object` that is ~4.6 KB,
# so the byte cap is what actually bites and it is applied first.
BATCH_BYTES = 3600
BATCH_COMMANDS = 48


def batched(commands: list[str]) -> list[list[str]]:
    out, cur, size = [], [], 0
    for cmd in commands:
        add = len(cmd) + (1 if cur else 0)
        if cur and (size + add > BATCH_BYTES or len(cur) >= BATCH_COMMANDS):
            out.append(cur)
            cur, size = [], 0
            add = len(cmd)
        cur.append(cmd)
        size += add
    if cur:
        out.append(cur)
    return out


def apply_projection(man: dict, *, blobs: Path, phases: set[str],
                     data_dir: Path, limit: int | None = None) -> dict:
    """Send the projection to whatever world `VALHEIM_WORLD` names.

    ORDER IS THE LEDGER'S OWN PROVEN ORDER: generate, then terrain, then
    place.  `flatten.py` refuses a compiler write into a zone with no
    `_ZoneCtrl`, so generation is a precondition and not a courtesy.
    """
    import replay as R  # noqa: PLC0415
    import rcon as RC  # noqa: PLC0415

    report: dict = {"world": RC.WORLD, "container": RC.CONTAINER,
                    "phases": sorted(phases), "began": time.strftime(
                        "%FT%TZ", time.gmtime()), "steps": []}
    t0 = time.time()
    with R.Server(dry=False) as srv:
        report["probe_rtt_s"] = round(srv.probe(), 3)

        if "generate" in phases:
            wires = [w["wire"] for w in man["generate_wires"]]
            done, failed = 0, []
            for i, w in enumerate(wires[:limit] if limit else wires):
                try:
                    R._staged(srv, [w])
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append({"wire": w,
                                   "error": f"{type(exc).__name__}: {exc}"[:200]})
                if i % 10 == 9:
                    report.setdefault("generate_probes", []).append(
                        round(srv.probe(), 3))
            report["steps"].append({"phase": "generate", "sent": done,
                                    "of": len(wires), "failed": failed[:10],
                                    "failed_n": len(failed),
                                    "elapsed_s": round(time.time() - t0, 1)})

        if "clear" in phases:
            # SECOND, and the order is the ledger's own proof: generate ->
            # clear -> flatten -> place.  Clearing before generation removes
            # nothing, and clearing AFTER placement would delete the build --
            # an `objects_remove id=*` keeps only `_`-prefixed engine objects.
            t = time.time()
            cs = man["clears"][:limit] if limit else man["clears"]
            done, failed = 0, []
            for i, c in enumerate(cs):
                wire = [w for w in c["wire"] if not w.strip() == "start"]
                try:
                    R._staged(srv, wire)
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append({"centre": c["centre"],
                                   "error": f"{type(exc).__name__}: {exc}"[:180]})
                if i % 25 == 24:
                    srv.probe()
            report["steps"].append({"phase": "clear", "sent": done,
                                    "of": len(cs), "failed": failed[:10],
                                    "failed_n": len(failed),
                                    "elapsed_s": round(time.time() - t, 1)})

        if "terrain" in phases:
            t = time.time()
            entries = man["terrain"][:limit] if limit else man["terrain"]
            _write_data_entries(entries, blobs, data_dir)
            cmds = []
            for e in entries:
                zx, zz = e["zone"]
                cu, cv = e["centre"]
                cmds.append(f"deleteObjects -zone {zx} {zz} "
                            f"-prefab _TerrainCompiler -force")
                cmds.append(f"spawn_object _TerrainCompiler "
                            f"from={cu:g},{cv:g},0 data={e['data_entry']}")
            sent, failed = 0, []
            for batch in batched(cmds):
                for cmd in batch:
                    try:
                        if cmd.startswith("deleteObjects"):
                            srv.command(cmd)
                        else:
                            srv.console_echo(cmd)
                        sent += 1
                    except Exception as exc:  # noqa: BLE001
                        failed.append({"cmd": cmd[:110],
                                       "error": f"{type(exc).__name__}: "
                                                f"{exc}"[:200]})
                srv.probe()
            report["steps"].append({"phase": "terrain", "zones": len(entries),
                                    "commands_sent": sent,
                                    "failed": failed[:10],
                                    "failed_n": len(failed),
                                    "elapsed_s": round(time.time() - t, 1)})

        if "place" in phases:
            t = time.time()
            places = man["placements"][:limit] if limit else man["placements"]
            cmds = [q["wire"] for q in places]
            groups = batched(cmds)
            sent, failed, stalls = 0, [], []
            for n, batch in enumerate(groups):
                joined = ";".join(batch)
                try:
                    srv.console_echo(joined)
                    sent += len(batch)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"batch": n, "size": len(batch),
                                   "error": f"{type(exc).__name__}: {exc}"[:220]})
                # THE SINK CHECK.  A `players` reply proves the Unity main
                # thread ran a frame since the batch was queued, which is the
                # one thing a wedged stdout pipe makes impossible.  The
                # container log cannot answer this inside a build loop: its
                # heartbeat is a 600 s signal.
                try:
                    rtt = srv.probe()
                    if rtt > 5.0:
                        stalls.append({"batch": n, "rtt_s": round(rtt, 2)})
                except Exception as exc:  # noqa: BLE001
                    stalls.append({"batch": n,
                                   "error": f"{type(exc).__name__}: {exc}"[:160]})
                    try:
                        RC.recover_log_sink()
                    except Exception:  # noqa: BLE001, S110
                        pass
            report["steps"].append({"phase": "place", "pieces": len(places),
                                    "batches": len(groups), "sent": sent,
                                    "failed": failed[:10],
                                    "failed_n": len(failed),
                                    "stalls": stalls[:10],
                                    "elapsed_s": round(time.time() - t, 1)})

        # THE SAVE IS THE POSTCONDITION CARRIER.  Everything measured
        # afterwards is measured from the file the server wrote, so the save
        # has to be confirmed rather than assumed.
        before = _save_generation(RC.WORLD)
        report["save_reply"] = str(srv.command("save"))[:200]
        report["save_before"] = before
        after = before
        deadline = time.time() + 180.0
        while time.time() < deadline:
            time.sleep(5.0)
            after = _save_generation(RC.WORLD)
            if after and after != before:
                break
        report["save_after"] = after
        report["save_advanced"] = bool(after and after != before)
        report["commands_sent_total"] = len(srv.sent)
    report["wall_s"] = round(time.time() - t0, 1)
    return report


def _save_generation(world: str) -> str | None:
    """The world's newest `_main.N.fwl2` name.  A save writes a new
    generation, so the name advancing is evidence the save happened; the
    reply alone is not, because `save` answers before the write completes."""
    import rcon as RC  # noqa: PLC0415
    d = RC.WORLD_ROOT / world / "config_merged" / "worlds_local" / world
    found = worldseed.newest_save(d, "fwl2")
    return found.name if found else None


def _write_data_entries(entries: list[dict], blobs: Path,
                        data_dir: Path) -> None:
    """One World Edit Commands data file holding every compiler payload.

    The blob IS the `TCData` byte array; the YAML is only its delivery
    vehicle, so it is regenerated from the blob rather than stored twice.
    The directory is watched (`ServerDevcommands.Yaml::SetupWatcher`), hence
    the pause.
    """
    import yaml  # noqa: PLC0415
    doc = []
    for e in entries:
        data = (Path(blobs) / e["blob_sha256"]).read_bytes()
        doc.append({"name": e["data_entry"],
                    "bytes": ["TCData, " + base64.b64encode(data).decode()]})
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "projection_terrain.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, width=10 ** 9,
                       sort_keys=False))
    time.sleep(8)


def probe_prefabs(prefabs: list[str]) -> dict:
    """Which of the projection's prefabs does the TARGET server register?

    `objects_count` on an unknown id answers `Error: Entity id X not
    recognized.` and prints no Total, so the absence of a Total IS the
    signal.  This is the measurement that decides whether a piece could ever
    replay, as distinct from whether it did.
    """
    import replay as R  # noqa: PLC0415
    known, unknown, errors = [], [], {}
    with R.Server(dry=False) as srv:
        srv.probe()
        for prefab in prefabs:
            try:
                srv.count(prefab, 0.0, 0.0, 1.0)
                known.append(prefab)
            except Exception as exc:  # noqa: BLE001
                unknown.append(prefab)
                errors[prefab] = str(exc)[:120]
    return {"registered": known, "unregistered": unknown,
            "registered_n": len(known), "unregistered_n": len(unknown),
            "errors": errors}


def repair(man: dict, world_dir: Path, tol_m: float = 0.05) -> dict:
    """THE READ-BEFORE-WRITE CENSUS GATE, as a pass rather than a promise.

    Censuses the world FIRST and re-sends only the placements that are not
    in it.  This is the gate the idempotency audit found missing from ten of
    the nineteen mutating entrypoints, and it is the reason a projection
    replay can be run twice: a second run of this against a complete world
    sends nothing at all.

    MEASURED on the throwaway world: the first replay pass landed 15,680 of
    15,683 pieces, this pass re-sent the 2 that were merely lost (the third
    is `mm_large_wall`, whose prefab the server does not register, so no
    number of passes can place it), and the save then held exactly 174 of
    174 `wood_pole_log_4` -- ONE per position, no duplicate.  That last part
    is the point: an ungated re-run would have made 176.
    """
    import replay as R  # noqa: PLC0415
    before = reconcile(man, world_dir, tol_m)
    # 3D, like the fold's own key.  A 2D lookup here silently dropped every
    # stacked piece but one: N missing rows in a column all resolved to the
    # same `q`, so the repair pass re-sent one piece and reported success
    # for the column.  `round(..., 3)` is the same millimetre QUANT uses.
    bykey = {(q["prefab"], round(q["u"], 3), round(q["v"], 3),
              round(q["y"], 3)): q for q in man["placements"]}
    todo = []
    for miss in before["missing"]:
        q = bykey.get((miss["prefab"], round(miss["pos"][0], 3),
                       round(miss["pos"][1], 3), round(miss["y"], 3)))
        if q:
            todo.append(q)
    sent, failed = 0, []
    if todo:
        with R.Server(dry=False) as srv:
            srv.probe()
            for batch in batched([q["wire"] for q in todo]):
                try:
                    srv.console_echo(";".join(batch))
                    sent += len(batch)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"size": len(batch),
                                   "error": f"{type(exc).__name__}: {exc}"[:200]})
                srv.probe()
            srv.command("save")
    return {"missing_before": len(before["missing"]),
            "resent": sent, "failed": failed,
            "not_resendable": [m for m in before["missing"]
                               if (m["prefab"], round(m["pos"][0], 3),
                                   round(m["pos"][1], 3),
                                   round(m["y"], 3)) not in bykey],
            "before": {k: v for k, v in before.items()
                       if k not in ("per_prefab", "missing")},
            "missing_detail": before["missing"]}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["fold", "census-plan", "reconcile",
                                   "prefab-probe", "apply", "repair"])
    ap.add_argument("--root", required=True,
                    help="ledger run directory (holds ledger.jsonl and blobs/)")
    ap.add_argument("--ledger", help="override the ledger file (e.g. a snapshot)")
    ap.add_argument("--out", help="write the report/manifest here")
    ap.add_argument("--reach", type=float, default=24.0)
    ap.add_argument("--world-dir", help="a world's worlds_local/<World> "
                                        "directory, for reconcile")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--phases", default="generate,terrain,place")
    ap.add_argument("--data-dir", help="World Edit Commands data directory of "
                                       "the TARGET world")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    root = Path(args.root)
    ledger = Path(args.ledger) if args.ledger else root / "ledger.jsonl"
    pr = Projection(ledger)
    pr.fold()
    man = pr.manifest()
    out: dict

    if args.op == "fold":
        out = man
    elif args.op == "census-plan":
        out = {"source": man["source"], "counts": man["counts"],
               "clusters": site_clusters(man["placements"], args.reach)}
    elif args.op == "reconcile":
        if not args.world_dir:
            ap.error("reconcile needs --world-dir")
        out = {"source": man["source"], "counts": man["counts"],
               "placements": reconcile(man, Path(args.world_dir),
                                       args.tolerance),
               "terrain": verify_terrain(man, root / "blobs",
                                         Path(args.world_dir))}
    elif args.op == "prefab-probe":
        out = probe_prefabs(sorted(man["by_prefab"]))
    elif args.op == "repair":
        if not args.world_dir:
            ap.error("repair needs --world-dir: the census reads the world's "
                     "own save, and re-sending without reading first is the "
                     "defect this pass exists to remove")
        out = repair(man, Path(args.world_dir), args.tolerance)
    else:
        out = apply_projection(
            man, blobs=root / "blobs",
            phases={p.strip() for p in args.phases.split(",") if p.strip()},
            data_dir=Path(args.data_dir) if args.data_dir else Path("."),
            limit=args.limit)

    text = json.dumps(out, indent=1)
    if args.out:
        Path(args.out).write_text(text)
        brief = {k: v for k, v in out.items()
                 if k in ("counts", "placements", "terrain", "steps",
                          "wall_s", "registered_n", "unregistered_n",
                          "unregistered", "save_advanced", "save_before",
                          "save_after")}
        if "placements" in brief and isinstance(brief["placements"], dict):
            brief["placements"] = {k: v for k, v in brief["placements"].items()
                                   if k != "per_prefab" and k != "missing"}
        print(json.dumps({"wrote": args.out, "bytes": len(text),
                          "summary": brief}, indent=1))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
