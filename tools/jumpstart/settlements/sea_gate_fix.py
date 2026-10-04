#!/usr/bin/env python3
"""Open Ulfsgard's sea gate: retire the six `fb_fortress_portcullis` panels.

THE OPERATOR'S COMPLAINT
    "there are four gates and you cant sail a ship through the harbour walls.
     two of the gates are in the middle."

WHAT HE IS LOOKING AT.  The six panels went in at yaw 90 while every module of
the 132 m curtain is at yaw 0 (MEASURED live, `findObjects -prefab ...
-detailed`: six rows `Rotation: (0 90 0)`, two wall rows `Rotation: (0 0 0)`,
each reply's declared count equal to the rows parsed).  A panel's solid is
5.704 m in LOCAL X and 0.203 m in LOCAL Z, so at yaw 90 the long face lies
along world Z and the three panels stand edge-on in the throat as three fins,
splitting an 11.951 m structural gap into four slots -- the operator's "four
gates", the two middle ones 3.777 m each.

WHY THIS SCRIPT DOES NOT ROTATE THEM TO YAW 0, WHICH IS WHAT IT WAS WRITTEN TO
DO.  `Door.SetState` (assembly_valheim IL, class `Door` at .il line 434912)
does exactly two things: `m_animator.SetInteger("state", s)` and
`m_openEnable.SetActive(s != 0)`, and on this prefab `m_openEnable` is null.
So the OPEN geometry is an animation clip and is INVISIBLE to a closed-pose
collider dump -- which is the only dump this project had.  MEASURED by
instantiating the prefab inactive in a sandbox (own port, own throwaway world,
live install untouched), stripping its MonoBehaviours and driving the Animator
by hand through state 0 -> 1 -> -1 -> 0:

    fb_fortress_portcullis has THREE solid boxes.  ONE of them moves.
      leaf  `door`            x [-2.47495, 2.64786]   lifts +3.7431 m, returns
                              y [ 0.24422, 5.75114] closed
                              y [ 3.98732, 9.49424] open
      post  `collider`        x [ 2.53573, 2.91000]   0.374 m wide, 5.834 m
      post  `collider (1)`    x [-2.79427,-2.42000]   tall, NEVER MOVES, in any
                                                      state, in either direction

    So ONE panel's open aperture is 4.956 m wide by 3.751 m tall, and the
    raised leaf never leaves the throat -- it goes up 3.743 m and stops.

Three panels at yaw 0 on the built 3.982 m pitch therefore put SIX immovable
post columns inside the 11.951 m gap, and the widest post-free interval with
every panel FULLY OPEN is 3.181 m.  That is NARROWER than the 3.777 m the
operator is complaining about.  Rotating to yaw 0 would have sealed the wall,
passed a close/reopen cycle, and made his actual problem measurably worse while
looking like a fix.

AND IT CANNOT BE FIXED BY MOVING THEM, proved in both directions rather than
one.  Everything in the prefab is on one side of the pivot, and the posts are
static, so for a hull to pass, the 5.834 m post column must sit ENTIRELY below
the keel or ENTIRELY above the ship:
  * posts below the keel (top <= 28.0 on a 30.0 water plane) puts the closed
    leaf top at 27.99 -- the "closed" gate blocks nothing above water;
  * posts above the ship puts the closed leaf's BOTTOM above the ship too --
    same result.
Flipping the panel (euler x 180) mirrors both cases and moves the open leaf
DOWN into the channel.  There is no placement of this prefab that is both
closable and sailable.

NOR IS THERE ANOTHER PREFAB.  MEASURED in the same sandbox run:
`fb_fortress_gates` is a double door whose jambs are static at x [-1.53298,
1.52502] -- 3.058 m clear.  `fb_fortress_wall_portcullis` has no `Door` and no
`Animator` at all: it is a wall with a 6.851 m hole.  `GB_Ship_Gate`, the widest
keyless leaf the earlier 7,262-prefab ZNetScene sweep found, is 7.090 m against
a 9.16 m CargoShip beam.  There is no closable ship gate in this game build.

WHAT THE OPERATOR LOSES BY REMOVING THEM, stated because it decides his answer.
MEASURED from IL: `SpawnSystem::FindBaseSpawnPoint(SpawnData, List<Player>, out
spawnCenter, out targetPlayer)` derives a raid's spawn centre from a PLAYER, at
`m_spawnDistanceMin = 40` to `m_spawnDistanceMax = 80` (static literals,
`SpawnSystem`), and `RandomEvent` carries `m_pos` + `m_eventRange` around the
same point.  Raid creatures MATERIALISE inside the base radius; they do not
path in from open sea and there is no ship-borne attacker in the vanilla set.
So a barrier on a sea throat is decorative against the threats this world
generates.  NOT MEASURED, and said plainly rather than reassured away: the
`ZenRaids` plugin is installed on this server and its spawn placement was not
read.

Everything here is reversible by position: six retires, each keyed by prefab AND
full 3D position -- the two rows share all three xz and differ only in y
(23.774 and 29.481), so a 2D key would merge them -- and one `spawn_object` per
panel puts the wall back exactly as it stood.

Usage:
    VALHEIM_ROOT=/media/big4/projects/game/valheim ./sea_gate_fix.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "terraform"))

PREFAB = "fb_fortress_portcullis"
WALL_Z = 752.0
SEQ_PLACED = 2690                      # the spawn_plan that put the six in

# The six, as PLANNED in seq 2690's wire and as STANDING in the world.  The
# wire's `pos=` is z,x,y; these are world order.
COLUMNS = (1062.518, 1066.5, 1070.482)
ROWS = (23.774, 29.481)                # panel pivot y, lower and upper

# `deleteObjects -near` is a CUBE in all three axes, so 1.0 m of half-extent
# isolates one panel: the sibling row is 5.707 m away in y and the neighbouring
# column is 3.982 m away in x.  It is also the radius that keeps the reply --
# one line per deleted object, 155 B MEASURED -- to a single line.
RETIRE_R_M = 1.0

# A scratch position for the constructive-half proof: 30 m above the gate,
# inside the same zone, with nothing else within 20 m in any axis.
PROOF_POS = (1066.5, 60.0, WALL_Z)
PROOF_R_M = 2.0

# MEASURED collider geometry of the standing wall module, from
# /tmp/harbour/piece_geometry_modded.json: the solid box is x [-10.00448,
# 10.00448] in local frame, and both flanking modules stand at yaw 0.
WALL_HALF_M = 10.00448

FOUND_RE = re.compile(r"Found (\d+) objects")
ROW_RE = re.compile(
    r"-Prefab:\s+(\S+)\s+Id:\s+(\S+)\s+Position:\s+\(([-\d.]+) ([-\d.]+) "
    r"([-\d.]+)\)\s+Zone:\s+\((-?\d+) (-?\d+)\)\s+Rotation:\s+"
    r"\(([-\d.]+) ([-\d.]+) ([-\d.]+)\)")


def find_objects(srv, cmd: str) -> list[dict]:
    """A `findObjects` read WITH THE HEADER CHECK ARMED.

    The reply declares its own count.  A count that disagrees with the rows
    actually parsed is a FAILED READ, not a short answer -- the CLI truncated
    replies at `--max-print` for most of this project's life and a header that
    said 41 over a list of 6 read as a confident, wrong census.  This raises
    instead of returning the short list.
    """
    reply = srv.command(cmd)
    if srv.dry:
        return []
    m = FOUND_RE.search(reply)
    if not m:
        # MEASURED live 2026-09-19: `findObjects` does NOT answer "Found 0
        # objects" when nothing matches -- it answers "No objects found
        # matching the provided criteria.", a sentence with no count in it at
        # all.  A parser that only knows the counted form reads the ONE reply
        # it most needs to understand -- the empty one that proves a
        # demolition -- as a protocol error.  Both forms are accepted here and
        # both were checked against the live server.
        if ("No objects found" in reply or "Found 0 objects" in reply
                or reply.strip() == ""):
            return []
        raise RuntimeError(f"findObjects reply has no count header: {reply[:300]!r}")
    declared = int(m.group(1))
    rows = [dict(prefab=g[0], id=g[1],
                 pos=(float(g[2]), float(g[3]), float(g[4])),
                 zone=(int(g[5]), int(g[6])),
                 rot=(float(g[7]), float(g[8]), float(g[9])))
            for g in ROW_RE.findall(reply)]
    if len(rows) != declared:
        raise RuntimeError(
            f"FAILED READ: reply declares {declared} objects and {len(rows)} "
            f"rows parsed. A count that disagrees with its own list is not a "
            f"short answer. Command: {cmd!r}")
    return rows


def gap_from_standing_walls(srv) -> dict:
    """The clear opening, measured from what is STANDING, never from plan.json.

    The two flanking `fb_fortress_wall` modules are read live and the gap is
    their facing solid faces -- pivot +/- the module's own MEASURED 10.00448 m
    box half-extent -- not the design's 11.946 m.
    """
    rows = find_objects(
        srv, f"findObjects -prefab fb_fortress_wall -near 1066.5 26 {WALL_Z:g} "
             f"25 -detailed")
    if srv.dry:
        return {"clear_m": None}
    if len(rows) != 2:
        raise RuntimeError(f"expected the 2 modules flanking the gate, got "
                           f"{len(rows)}: {[r['pos'] for r in rows]}")
    for r in rows:
        if abs(r["rot"][1]) > 0.5:
            raise RuntimeError(f"wall module at {r['pos']} is at yaw "
                               f"{r['rot'][1]}, not 0; the half-extent below "
                               f"is only valid along world x at yaw 0")
    west, east = sorted(rows, key=lambda r: r["pos"][0])
    x0 = west["pos"][0] + WALL_HALF_M
    x1 = east["pos"][0] - WALL_HALF_M
    return {"west_module_x": west["pos"][0], "east_module_x": east["pos"][0],
            "gap_x": [round(x0, 3), round(x1, 3)],
            "clear_m": round(x1 - x0, 3),
            "method": ("MEASURED live: both flanking fb_fortress_wall read by "
                       "findObjects -prefab -detailed, yaw checked = 0, minus "
                       "the module's own MEASURED solid half-extent 10.00448 m")}


def record_outcome(b, srv, dry: bool) -> None:
    """Census what the retires left standing, and record it.

    SEPARATE FROM THE RETIRES ON PURPOSE.  The first live run of this script
    retired all six panels cleanly and then died HERE, because `findObjects`
    answers an empty query with "No objects found matching the provided
    criteria." rather than "Found 0 objects" and the parser did not know that
    sentence.  The world was right and the instrument was wrong -- again --
    but the outcome record had nowhere to go, so the run had to be finished
    with `--finish` rather than repeated.  A destructive phase and the phase
    that proves it must be separately re-runnable.
    """
    left = find_objects(
        srv, f"findObjects -prefab {PREFAB} -near 1066.5 30 {WALL_Z:g} 25 "
             f"-detailed")
    throat = find_objects(
        srv, f"findObjects -near 1066.5 30 {WALL_Z:g} 8 -detailed")
    after = gap_from_standing_walls(srv)
    if not dry:
        if left:
            raise SystemExit(f"{len(left)} {PREFAB} still standing within "
                             f"25 m of the gate: {[r['pos'] for r in left]}")
        print(f"census: 0 {PREFAB} within a 50 m cube of the gate centre; "
              f"throat holds {len(throat)} objects "
              f"{sorted({r['prefab'] for r in throat})}")
        print(f"clear opening now: {after['clear_m']} m")

    beam = 9.16
    b.observe(
        "Ulfsgard sea gate clear opening, after the six panels were retired",
        "MEASURED live: `findObjects -prefab fb_fortress_portcullis -near "
        "1066.5 30 752 25 -detailed` answers `No objects found matching the "
        "provided criteria.`; `findObjects -near 1066.5 30 752 8 -detailed` "
        "censuses the whole throat with its declared count checked against "
        "the rows parsed; and the opening is the facing solid faces of the "
        "two flanking fb_fortress_wall modules read live, minus the module's "
        "own MEASURED 10.00448 m box half-extent. Nothing is taken from "
        "plan.json.",
        "tools/jumpstart/settlements/sea_gate_fix.py::gap_from_standing_walls",
        {"clear_opening_m": after.get("clear_m"),
         "gap_x": after.get("gap_x"),
         "cargoship_beam_m": beam,
         "margin_each_side_m": (round((after["clear_m"] - beam) / 2.0, 3)
                                if after.get("clear_m") else None),
         "portcullis_within_25m_cube": len(left),
         "objects_left_in_throat": sorted({r["prefab"] for r in throat}),
         "throat_object_count": len(throat),
         "not_binding": ("the Rock_4 in the throat sit at pivot y 24.0-24.94 "
                         "and the prefab's mesh is 0.689 m tall at unit "
                         "scale; their world scale was NOT read, so this is "
                         "recorded rather than cleared. The dredged channel "
                         "was separately measured at 6.000 m under a full "
                         "CargoShip hull footprint over 114 samples."),
         "raid_exposure": ("MEASURED from IL: SpawnSystem::FindBaseSpawnPoint "
                           "derives a raid's spawn centre from a PLAYER at "
                           "m_spawnDistanceMin 40 to m_spawnDistanceMax 80, "
                           "and RandomEvent carries m_pos + m_eventRange "
                           "about the same point, so raid creatures "
                           "materialise inside the base radius rather than "
                           "pathing in from open sea. UNMEASURED: the "
                           "ZenRaids plugin is installed and its placement "
                           "was not read."),
         "reversal": ("six `spawn_object fb_fortress_portcullis "
                      "pos=752,<x>,<y> rot=90,0,0 from=0,0,0` with "
                      "x in (1062.518, 1066.5, 1070.482) and y in "
                      "(23.774, 29.481) restores the wall exactly")},
        units="m", at=f"[1066.5, {WALL_Z}]",
        role="castle", site_id="harbour-city-ulfsgard")

    b.emit("save", params={"note": "Ulfsgard sea gate opened",
                           "role": "castle",
                           "site_id": "harbour-city-ulfsgard"},
           wire=["save"],
           expect={"absent": [{"prefab": PREFAB, "pos": [1066.5, WALL_Z],
                               "max": 20.0}]},
           meta={"why": "a verified save at the stage boundary, with the "
                        "gate's own emptiness as its postcondition"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--finish", action="store_true",
                    help="skip the retires and record ONLY the outcome "
                         "census: for a run whose destructive phase landed "
                         "and whose proof phase did not")
    a = ap.parse_args()

    from live import LiveBuilder  # noqa: E402

    with LiveBuilder(actor="GateFix", dry=a.dry) as b:
        srv = b.srv

        # A DRY RUN THAT DOES NOT REPORT ITS SCHEMA PROBLEMS IS NOT A DRY RUN.
        # `emit` returns `schema_problems` instead of raising when dry, so a
        # record with an unmodelled param sails through the rehearsal and
        # fails on the live write with the token held. Rebinding on the
        # INSTANCE also covers `observe`/`note`, which call `self.emit`.
        _raw_emit = b.emit

        def _checked(op, **kw):
            res = _raw_emit(op, **kw)
            if res.get("schema_problems"):
                raise SystemExit(f"{op} FAILS SCHEMA: {res['schema_problems']}")
            return res

        b.emit = _checked

        if a.finish:
            record_outcome(b, srv, a.dry)
            print(json.dumps(b.close(), indent=1, default=str))
            return 0

        # ---- 1. PRECONDITION: the six are standing, at yaw 90 ------------
        standing = find_objects(
            srv, f"findObjects -prefab {PREFAB} -near 1066.5 26.6 {WALL_Z:g} "
                 f"15 -detailed")
        if not a.dry:
            if len(standing) != 6:
                raise SystemExit(f"expected 6 {PREFAB}, found {len(standing)}")
            yaws = sorted({r["rot"][1] for r in standing})
            print(f"precondition: 6 {PREFAB} standing, yaws {yaws}")
            if yaws != [90.0]:
                raise SystemExit(
                    f"the six are not all at yaw 90 ({yaws}); the diagnosis "
                    f"this script acts on does not describe the world")
        gap = gap_from_standing_walls(srv)
        print(f"structural gap: {json.dumps(gap)}")

        # ---- 2. PROVE THE CONSTRUCTIVE HALF BEFORE THE DESTRUCTIVE ONE ---
        # `terrain_write` is delete-then-respawn, clearing is remove-then-place,
        # and a retire whose spawn cannot be replayed is not reversible -- it is
        # a deletion. So `spawn_object` is proved to work, at THIS prefab and
        # THIS yaw, before a single panel is removed. One panel, 30 m above the
        # gate, then taken straight back out.
        px, py, pz = PROOF_POS
        b.emit("spawn", params={
            "prefab": PREFAB, "pos": [px, py, pz], "yaw_deg": 0.0,
            "rot_sent": "0,0,0", "from": "0,0,0",
            "role": "castle", "site_id": "harbour-city-ulfsgard",
            "guard_radius_m": PROOF_R_M},
            wire=[f"spawn_object {PREFAB} pos={pz:g},{px:g},{py:g} "
                  f"rot=0,0,0 from=0,0,0"],
            requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                      "prefabs": [PREFAB], "blobs": []},
            # y-BLIND on purpose: `objects_count` is an XZ cylinder with no
            # height bound, so the honest prediction at this xz is the scratch
            # panel PLUS the two real ones stacked below it. Predicting 1 here
            # is the exact error that failed seq 2690's postcondition on a
            # correct world.
            expect={"prefab_count": [{"prefab": PREFAB, "pos": [px, pz],
                                      "count": 3, "max": PROOF_R_M,
                                      "tolerance": 0}]},
            meta={"stage": "constructive-half proof",
                  "why": ("prove spawn_object is available, and that rot=0,0,0 "
                          "lands as Rotation (0 0 0), BEFORE deleting anything "
                          "-- a retire whose constructive half is unavailable "
                          "is a deletion, not a retire-and-replace")})
        proof = find_objects(
            srv, f"findObjects -prefab {PREFAB} -near {px:g} {py:g} {pz:g} "
                 f"{PROOF_R_M:g} -detailed")
        if not a.dry:
            if len(proof) != 1:
                raise SystemExit(f"constructive proof: expected 1 scratch "
                                 f"panel, found {len(proof)}")
            if proof[0]["rot"] != (0.0, 0.0, 0.0):
                raise SystemExit(f"constructive proof: scratch panel came back "
                                 f"at rotation {proof[0]['rot']}, not (0 0 0)")
            print(f"constructive half PROVED: spawn_object placed {PREFAB} at "
                  f"{proof[0]['pos']} rotation {proof[0]['rot']}")

        b.emit("retire", params={
            "prefab": PREFAB, "pos": [px, py, pz], "radius_m": PROOF_R_M,
            "retires": [SEQ_PLACED],
            "reason": ("the constructive-half proof panel, removed "
                       "immediately. It existed only to show that "
                       "`spawn_object` can put this prefab back at this yaw "
                       "before the six real ones were retired."),
            "role": "castle", "site_id": "harbour-city-ulfsgard"},
            wire=[f"deleteObjects -prefab {PREFAB} -near {px:.3f} {py:.3f} "
                  f"{pz:.3f} {PROOF_R_M:.2f} -force"],
            requires={"mods": ["ValheimRcon"], "prefabs": [PREFAB],
                      "blobs": []},
            expect={"prefab_count": [{"prefab": PREFAB, "pos": [px, pz],
                                      "count": 2, "max": PROOF_R_M,
                                      "tolerance": 0}]},
            meta={"stage": "constructive-half proof",
                  "why": "back to 2 at this xz: the two real panels, y-blind"})

        # ---- 3. THE MEASUREMENT THAT JUSTIFIES NOT RE-PLACING ------------
        b.observe(
            "fb_fortress_portcullis OPEN pose: only the leaf moves, and the "
            "two frame posts never do, so the prefab cannot clear a ship "
            "channel in any yaw or at any height",
            "MEASURED in a sandbox copy of this server's own tree (own port "
            "3471, own throwaway world, live install untouched): the prefab "
            "was instantiated INACTIVE so no ZNetView.Awake ran without a "
            "ZDO, every MonoBehaviour stripped, then the Animator driven by "
            "hand through state 0 -> 1 -> -1 -> 0 with collider corners read "
            "in the prefab root's local frame at each state. Door.SetState "
            "(assembly_valheim IL, class Door) only calls "
            "m_animator.SetInteger(\"state\", s) and m_openEnable.SetActive, "
            "and m_openEnable is null here -- so the open geometry is an "
            "animation clip and a closed-pose collider dump cannot see it.",
            "tools/jumpstart/settlements/sea_gate_fix.py + throwaway "
            "DoorProbe.cs (sandbox BepInEx plugin, same method as "
            "blueprints/PieceGeometry.cs)",
            {
                "solid_boxes": 3,
                "moving_boxes": 1,
                "leaf_child": "door",
                "leaf_x_local_m": [-2.47495, 2.64786],
                "leaf_y_closed_m": [0.24422, 5.75114],
                "leaf_y_open_m": [3.98732, 9.49424],
                "leaf_lift_m": 3.7431,
                "post_a_x_local_m": [2.53573, 2.91],
                "post_b_x_local_m": [-2.79427, -2.42],
                "post_y_local_m": [0.23615, 6.07021],
                "posts_move_on_open": False,
                "cycle_proven": "state 0 -> 1 -> -1 -> 0, leaf localPosition y "
                                "0 -> 3.7431 -> 3.7431 -> 0, exact restore",
                "one_panel_open_aperture_w_m": 4.95573,
                "one_panel_open_aperture_h_m": 3.75117,
                "three_panels_yaw0_widest_post_free_interval_m": 3.181,
                "today_yaw90_widest_slot_m": 3.777,
                "cargoship_beam_m": 9.16,
                "alternatives_closed": {
                    "fb_fortress_gates": "double door, static jambs, 3.058 m "
                                         "clear when open",
                    "fb_fortress_wall_portcullis": "no Door and no Animator: a "
                                                   "wall with a 6.851 m hole",
                    "GB_Ship_Gate": "7.090 m leaf, widest keyless door found "
                                    "by the 7,262-prefab ZNetScene sweep"},
                "conclusion": "there is no closable ship gate in this game "
                              "build, and rotating the six to yaw 0 would have "
                              "narrowed the operator's channel from 3.777 m to "
                              "3.181 m while passing every close/reopen check"},
            units="m", at=f"[1066.5, {WALL_Z}]",
            role="castle", site_id="harbour-city-ulfsgard")

        # ---- 4. THE SIX RETIRES, EACH KEYED IN THREE AXES ----------------
        # Lower row first within each column, so the y-blind XZ cylinder the
        # checker uses has an honest prediction at every step: 2 -> 1 -> 0.
        for x in COLUMNS:
            for i, y in enumerate(ROWS):
                remaining = len(ROWS) - i - 1
                row = "lower" if i == 0 else "upper"
                srv.probe()
                res = b.emit("retire", params={
                    "prefab": PREFAB, "pos": [x, y, WALL_Z],
                    "radius_m": RETIRE_R_M, "retires": [SEQ_PLACED],
                    "reason": (
                        f"Ulfsgard sea gate, {row} row, panel pivot "
                        f"({x:g}, {y:g}, {WALL_Z:g}). Removed so a ship can "
                        f"enter the harbour, which is what the operator asked "
                        f"for and what the wall does not allow. MEASURED: this "
                        f"prefab's two frame posts (local x [2.53573, 2.91] "
                        f"and [-2.79427, -2.42], 0.374 m wide, 5.834 m tall) "
                        f"do NOT move when the Door opens -- only the leaf "
                        f"lifts, +3.7431 m -- so three panels leave six "
                        f"immovable columns in an 11.951 m throat and the "
                        f"widest post-free interval when fully open is "
                        f"3.181 m against a 9.16 m CargoShip beam. Rotating "
                        f"them to yaw 0 would have sealed the wall and made "
                        f"the channel NARROWER than the 3.777 m it is today. "
                        f"REVERSIBLE EXACTLY: `spawn_object {PREFAB} "
                        f"pos={WALL_Z:g},{x:g},{y:g} rot=90,0,0 from=0,0,0` "
                        f"restores this panel as it stood; the y is part of "
                        f"the key because the two rows share all three xz and "
                        f"differ only in y ({ROWS[0]:g} and {ROWS[1]:g})."),
                    "role": "castle", "site_id": "harbour-city-ulfsgard"},
                    wire=[f"deleteObjects -prefab {PREFAB} -near {x:.3f} "
                          f"{y:.3f} {WALL_Z:.3f} {RETIRE_R_M:.2f} -force"],
                    requires={"mods": ["ValheimRcon"], "prefabs": [PREFAB],
                              "blobs": []},
                    expect=({"prefab_count": [
                                {"prefab": PREFAB, "pos": [x, WALL_Z],
                                 "count": remaining, "max": RETIRE_R_M,
                                 "tolerance": 0,
                                 "why": "predicted with the SAME y-blind XZ "
                                        "cylinder objects_count measures with: "
                                        "the sibling row stands at the same xz "
                                        "5.707 m above, so a cube-derived "
                                        "expectation of 0 here would fail on a "
                                        "correct world -- exactly how seq "
                                        "2690's postcondition failed"}]}
                            if remaining else
                            {"absent": [{"prefab": PREFAB, "pos": [x, WALL_Z],
                                         "max": RETIRE_R_M}]}),
                    meta={"settlement": "harbour-city-ulfsgard", "stage": "gate",
                          "row": row, "pivot_y": y, "column_x": x,
                          "why": ("keyed in THREE axes: `deleteObjects -near` "
                                  "is a cube, and 1.0 m of half-extent "
                                  "isolates this panel from its sibling row "
                                  "5.707 m away in y and its neighbour column "
                                  "3.982 m away in x")})
                print(f"  retired {row} panel at ({x:g}, {y:g}, {WALL_Z:g}) -> "
                      f"{res['status']} seq {res['seq']} "
                      f"{res.get('checks', res.get('schema_problems'))}")
                time.sleep(0.15)

        # ---- 5. CENSUS THE RESULT, DO NOT SUBTRACT IT --------------------
        record_outcome(b, srv, a.dry)

        print(json.dumps(b.close(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
