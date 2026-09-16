#!/usr/bin/env python3
"""Turn a planned crossing structure into BuildLedger v1.1 ops, in order.

The ledger is a REPLAY ARTEFACT, not documentation: the operator asked for "a
log of everything you build and where and how so that you can repeat it all
during the next regeneration". So every structure here is emitted as the FULL
sequence that produces it from nothing, in the order GroundTruth proved:

    zones_generate -> objects_clear -> spawn_plan -> spawn/portal -> save

and NOT `flatten`, which is the one step every structure here forbids. That
refusal is carried as data (`flatten: "FORBIDDEN"` plus a reason) rather than as
a convention, because BuildLedger's replay driver REFUSES a `terrain_write`
whose footprint overlaps a structure carrying it. That is what makes the
`early-dock` floating-pier defect unreproducible by construction: at that site
the pad levelled the whole rectangle, the water under the footprint went away,
and an over-water pier body then stood on dry levelled ground.

Every `expect` is a cheap real measurement, never a sleep:
  * `zone_ctrl` -- PlaceZoneCtrl puts exactly one `_ZoneCtrl` per generated zone,
    at the zone centre, so counting them is a completion signal for a staged
    operation.
  * `prefab_count` -- how many of each prefab should stand in the footprint
    afterwards, per prefab, with a radius sized to the structure's own bbox.
"""

from __future__ import annotations

import hashlib
import json
import math

import assemble as A

ZONE_SIZE = 64.0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _bbox(entries: list[dict]) -> tuple[float, float, float]:
    xs = [e["xz"][0] for e in entries]
    zs = [e["xz"][1] for e in entries]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    r = max(math.hypot(x - cx, z - cz) for x, z in zip(xs, zs))
    return cx, cz, r


def plain(obj):
    """numpy scalars out of the POI gate are not JSON-serialisable and the
    ledger writer canonicalises with json. Coerce once, here."""
    return json.loads(json.dumps(obj, default=float))


def ops_for(rec: dict, commands: list[str], piece_entries: list[dict],
            *, fixture_cmds: dict[str, str], plan_ref: str,
            pair_ends: dict[str, str]) -> list[dict]:
    """Ops for one structure. `rec` is a record from `plan.build`.

    `pair_ends` maps a portal tag to the id of the OTHER end's owner, so the
    two-ends-per-tag invariant has something to check against rather than
    silently accepting a one-ended pair -- the defect that gave the operator a
    one-way trip, and the one BuildLedger's own round-trip proof reproduced with
    an over-wide presence guard.
    """
    sid = rec["id"]
    prep = rec["prepare"]
    zones = [tuple(z) for z in prep["zones"]]
    cx, cz, radius = _bbox(piece_entries)
    ops: list[dict] = []

    common = dict(role=rec["role"], site_id=sid,
                  flatten="FORBIDDEN", flatten_reason=rec["flatten_reason"])

    # 0. GENERATE. Required even for an over-water structure: MEASURED by
    #    GroundTruth, a server with no peers instantiates no _TerrainCompiler, so
    #    Heightmap::Generate -> ApplyModifiers finds nothing and SpawnZone runs
    #    PlaceVegetation against the unmodified collider in the same call.
    #    Vegetation planted AFTER the deck exists stands through the landward end
    #    of the pier.
    pos = prep["generate_pos"]
    span_m = prep["generate_max_m"]
    ops.append(dict(
        op="zones_generate",
        params=dict(pos=pos, max_m=span_m,
                    zones=[list(z) for z in zones], **common),
        wire=[f"zones_generate pos={pos[0]:.1f},{pos[1]:.1f} max={span_m:.1f}",
              "start"],
        requires=dict(mods=["UpgradeWorld"], prefabs=[], blobs=[]),
        expect=dict(zone_ctrl=len(zones)),
        meta=dict(why="generate before clear before place; the alpha answer is NO, "
                      "so an ungenerated zone plants vegetation regardless of paint"),
    ))

    # 1. CLEAR, landward end only. Over water there is nothing to remove, and a
    #    wide radius near a POI is how the old world lost mod content.
    clear = prep.get("clear")
    if clear:
        ops.append(dict(
            op="objects_clear",
            params=dict(centre=clear["centre"], radius_m=clear["radius_m"],
                        ids="*", ignore="_*", **common),
            wire=[f"objects_remove id=* ignore=_* "
                  f"pos={clear['centre'][0]:.1f},{clear['centre'][1]:.1f} "
                  f"max={clear['radius_m']:.1f}", "start"],
            requires=dict(mods=["UpgradeWorld"], prefabs=[], blobs=[]),
            expect=dict(objects_count=dict(ids="*", ignore="_*",
                                           pos=clear["centre"],
                                           max=clear["radius_m"], total=0,
                                           tolerance=0)),
            meta=dict(why=clear["reason"]),
        ))

    # 2. THE STRUCTURE.
    struct_cmds = [c for c in commands if not c.split()[1] in
                   ("sign", "wood_pole2", "portal_wood") and
                   " data=" not in c and not _is_boat(c)]
    plan_text = "\n".join(struct_cmds) + "\n"
    prefabs: dict[str, int] = {}
    for c in struct_cmds:
        p = c.split()[1]
        prefabs[p] = prefabs.get(p, 0) + 1
    ops.append(dict(
        op="spawn_plan",
        params=dict(plan_sha256=_sha(plan_text), plan_ref=plan_ref,
                    anchor=dict(x=round(cx, 2), y=A.DECK_Y, z=round(cz, 2), yaw=0.0),
                    command_count=len(struct_cmds), prefabs=prefabs,
                    datum="deck_y=30.6, the network water-crossing datum",
                    reach_m=round(radius + 4.0, 1), **common),
        wire=struct_cmds,
        requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                      prefabs=sorted(prefabs), blobs=[_sha(plan_text)]),
        expect=dict(prefab_count=[
            dict(prefab=p, pos=[round(cx, 1), round(cz, 1)],
                 max=round(radius + 4.0, 1), count=n, tolerance=0)
            for p, n in sorted(prefabs.items())]),
        meta=dict(poi_check=plain(rec.get("poi_check")),
                  structural_verdict=rec["structural"],
                  measured=rec.get("measured"),
                  continuity=rec.get("continuity"),
                  why=rec["why"]),
    ))

    # 3. SIGN, and its post. A sign is the only thing that makes a structure
    #    legible beyond 5 m: MEASURED by WayFinding, TeleportWorld::GetHoverText
    #    is the ONLY place a portal tag is ever shown and hover is gated at
    #    m_maxInteractDistance 5.0, so an unsigned portal is an anonymous arch.
    for role, cmd in fixture_cmds.items():
        prefab = cmd.split()[1]
        entry = next(e for e in rec["fixtures"]["pieces"] if e["role"] == role)
        if prefab == "portal_wood":
            tag = rec["fixtures"]["portal_tag"]
            ops.append(dict(
                op="portal",
                params=dict(prefab=prefab, pos=[entry["xz"][0], entry["y"],
                                                entry["xz"][1]],
                            yaw_deg=entry["yaw"], tag=tag,
                            pair_tag=tag, guard_radius_m=0.5,
                            zdo_strings=dict(tag=tag),
                            data_b64=cmd.split("data=")[1],
                            **common),
                wire=[cmd],
                requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                              prefabs=[prefab], blobs=[]),
                expect=dict(prefab_count=[dict(prefab=prefab,
                                               pos=entry["xz"], max=3.0,
                                               count=1, tolerance=0)]),
                meta=dict(pair_other_end=pair_ends.get(tag, "UNASSIGNED"),
                          why=("this end is at the structure; the other end is "
                               "owned by WayFinding in the portal hall, because a "
                               "tag must have EXACTLY two ends and a ferry whose "
                               "two terminals portal to each other is a ferry "
                               "nobody rides")),
            ))
        else:
            strings = {}
            if "data=" in cmd:
                strings = dict(text=rec["fixtures"]["sign_text"])
            ops.append(dict(
                op="spawn",
                params=dict(prefab=prefab,
                            pos=[entry["xz"][0], entry["y"], entry["xz"][1]],
                            yaw_deg=entry["yaw"], guard_radius_m=0.5,
                            **({"zdo_strings": strings,
                                "data_b64": cmd.split("data=")[1]} if strings else {}),
                            **common),
                wire=[cmd],
                requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                              prefabs=[prefab], blobs=[]),
                expect=dict(prefab_count=[dict(prefab=prefab, pos=entry["xz"],
                                               max=3.0, count=1, tolerance=0)]),
                meta=dict(role=role),
            ))

    # 4. BOATS. A ferry is only a ferry if the boat is still there next week.
    for b in rec.get("boats", []):
        ops.append(dict(
            op="spawn",
            params=dict(prefab=b["prefab"], pos=[b["x"], b["y"], b["z"]],
                        yaw_deg=b["yaw"], guard_radius_m=6.0,
                        role="boat_spawn_moor", site_id=sid,
                        flatten="FORBIDDEN",
                        flatten_reason="a boat floats on the water plane; there is "
                                       "no ground here to write"),
            wire=[f"spawn_object {b['prefab']}"
                  f" pos={A.fmt(b['z'])},{A.fmt(b['x'])},{A.fmt(b['y'])}"
                  f" rot={A.fmt(b['yaw'])},0,0 from=0,0,0"],
            requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                          prefabs=[b["prefab"]], blobs=[]),
            expect=dict(prefab_count=[dict(prefab=b["prefab"],
                                           pos=[b["x"], b["z"]], max=12.0,
                                           count=1, tolerance=0)]),
            meta=dict(berth_depth_m=b["berth_depth_m"], bed_y=b["bed_y"],
                      guard_radius_reason=(
                          "6 m, not the 0.5 m epsilon used for pieces: a hull is "
                          "9.16 m wide and WILL have moved by the time a replay "
                          "probes for it, so a tight guard would spawn a second "
                          "boat every replay. It is still far below the 12 m "
                          "spacing to anything else, so it cannot swallow a "
                          "neighbour."),
                      why=("MEASURED: all five ship prefabs carry "
                           "ZNetView.m_persistent=1 and m_type=1 "
                           "(ZDO.ObjectType.Prioritized), a Rigidbody, a Piece and "
                           "a WearNTear of material Wood, so a spawned Longship is "
                           "written to the database. Valheim has NO mooring "
                           "mechanic, so drift is bounded by geometry, not by a "
                           "field.")),
        ))
    return ops


def _is_boat(cmd: str) -> bool:
    return cmd.split()[1] in ("VikingShip", "Karve", "Raft", "Trailership",
                              "VikingShip_Ashlands")


def boundary_notes() -> list[dict]:
    """Decisions that will be re-litigated by whoever reads this in three months.
    They belong in the log, not in a hub transcript nobody keeps."""
    texts = [
        "Fords are TERRAIN and belong to RoadNet. Crossings owns no ford and "
        "writes no terrain. Agreed with RoadNet over hub.",
        "Bridge S1 at (-312,-64)->(-324,-64) is a STRUCTURE, not a causeway. "
        "RoadNet could have filled the 12 m channel with 2.5 m of terrain and "
        "agreed not to: the operator asked for bridges over streams and this is "
        "the one stream between the temple and the west isle.",
        "Crossing coordinates are owned by Crossings and quoted by RoadNet; the "
        "snapped values in this ledger are the agreed ones, not either side's raw "
        "measurement, because a bridge and its road must meet and there is only "
        "one way to guarantee that.",
        "Lighthouses belong to Settlements. Crossings measured two over-water "
        "lighthouse bodies for them and placed neither.",
        "Every Crossings structure carries flatten: FORBIDDEN. This is the "
        "exception to the proven generate/clear/flatten/place ordering, and it is "
        "the operator's own rule: uneven bases are a DEFECT on land and EXPECTED "
        "over water.",
    ]
    return [dict(op="note", params=dict(text=t), wire=[], requires={},
                 expect={}, meta={}) for t in texts]
