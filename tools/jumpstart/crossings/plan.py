#!/usr/bin/env python3
"""Plan every crossing structure on Ulfsland, verify it, and emit the artefacts.

Outputs, all under `tools/jumpstart/crossings/`:
  structures.yaml          the interchange file Main assigned to Crossings:
                           one record per structure, read-only to everyone else
  out/<id>.commands.txt    the exact `spawn_object` lines, in order
  out/ledger.jsonl         the ops in BuildLedger's v1.0 envelope, ready to
                           append through its writer
  out/verify.json          the structural verdict for every structure

Nothing here contacts a server. The whole point is that a bridge is proved to
stand, to clear the water, and to meet the road at both ends BEFORE a single
command is sent -- because MEASURED from `WearNTear::UpdateWear`, support is
only evaluated when a player is inside the active area, so an unsound bridge
looks perfect in a save and collapses under the operator.

Every height comes from a 1 m `VHPATCH1` field with rivers
(`run_patchscan.sh` -> WorldGenerator with full pregeneration). Nothing comes
from the 8 m overview grid, which is river-free.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

sys.path.insert(0, str(JUMPSTART / "blueprints"))

import data_entry as DE  # noqa: E402
import fixtures as FX  # noqa: E402

import assemble as A  # noqa: E402
import ledger_ops as LO  # noqa: E402
import water as W  # noqa: E402

FIELD = "/tmp/roads/h1m.bin"
PATCH = "mainland"


# ---------------------------------------------------------------------------
# The declared structures.
#
# Coordinates are MEASURED, and where they were agreed with RoadNet the agreed
# snapped value is used rather than my raw measurement, because a bridge and its
# road have to meet and there is only one way to guarantee that: one number,
# owned by one side, quoted by both.
# ---------------------------------------------------------------------------

BRIDGES = [
    dict(
        id="bridge-s1-temple-strait",
        role="bridge_assembly",
        bank_a=(-312.0, -64.0),      # spawn island, snapped with RoadNet
        bank_b=(-324.0, -64.0),      # west isle
        bearing=270.0,
        width_tiles=3,               # 6 m deck, matches RoadNet's trunk ribbon
        why=("The join between the two halves of the largest landmass in the "
             "world. MEASURED: spawn island 2.7754 km2 and west isle 2.6343 km2 "
             "are separated here by 12.0 m of water 2.52 m deep, and the next "
             "nearest approach between them is 197 m. 260 m from StartTemple, so "
             "this is on the operator's first walk out of the spawn."),
    ),
    dict(
        id="bridge-s2-portalhall-strait",
        role="bridge_assembly",
        bank_a=(-318.0, 230.0),      # spawn island, snapped with RoadNet
        bank_b=(-348.0, 246.0),      # west isle
        bearing=298.07,
        width_tiles=3,
        why=("The second crossing of the same channel, 300 m north of S1, which "
             "turns an out-and-back into a walkable loop: RoadNet's W1 segment "
             "runs S1-west-bank to S2-west-bank. MEASURED span 31.9 m over water "
             "4.86 m deep. Its spawn-island bank is ~30 m from the portal hall "
             "centre, so the spur to it is nearly free."),
    ),
]

# Jetties, harbours and slips. `bearing` points OUT to sea from the root.
# Sites come from `survey.py bays`, which measures shelter (fraction of 24 rays
# at 70 m ending on land), water fraction in a 35 m disc, and the deepest berth
# in that disc against the 2.5 m a Longship needs
# (MEASURED Ship.m_waterLevelOffset = 1.5 on VikingShip).
WATERFRONT = [
    dict(
        id="harbour-temple-south",
        role="harbour_place",
        kind="harbour",
        root=(11.0, -258.0),
        bearing=180.0,
        length_m=24.0,
        width_tiles=3,
        portal_tag="x-harbour",
        sign_text="HARBOUR - Longship berth, 8.9 m water. Temple 268 m north.",
        why=("The main harbour. MEASURED shelter 0.62, water fraction 0.45, "
             "berth depth 8.95 m -- the deepest sheltered berth on the spawn "
             "island's south shore and the only one over 8 m within 300 m of "
             "the spawn. 268 m from StartTemple (-64.68, 3.31)."),
    ),
    dict(
        id="boathouse-temple-strait",
        role="boathouse_place",
        kind="slip",
        root=(-305.0, -100.0),
        bearing=170.0,
        length_m=24.0,
        inner_width_m=11.0,
        sign_text="BOATHOUSE - Longship slip. Bridge to West Isle 36 m north.",
        why=("A Longship slip in the strait lagoon 36 m south of bridge S1, on "
             "the spawn-island bank. MEASURED shelter 0.96 -- the most enclosed "
             "water on either island -- with a 5.15 m berth. Sized from the hull: "
             "VikingShip colliders span 9.16 m x 21.56 m, so 11 m inner width and "
             "24 m length berth it with a margin. It is SOUTH of the bridge so a "
             "boat reaches open water without passing under a 0.6 m deck."),
    ),
    dict(
        id="dock-stationhub",
        role="dock_place",
        kind="jetty",
        root=(502.0, 1062.0),
        bearing=0.0,
        length_m=20.0,
        width_tiles=2,
        sign_text="DOCK - Station Hub 160 m east.",
        why=("Serves the station hub installation at (661.5, 1088.5), 160 m away. "
             "MEASURED shelter 0.79, water fraction 0.50, berth 5.47 m: the best "
             "sheltered water on the island's north-west coast."),
    ),
    dict(
        id="ferry-terminal-east",
        role="ferry_terminal_place",
        kind="jetty",
        root=(1751.0, 660.0),
        bearing=335.0,
        length_m=20.0,
        width_tiles=2,
        prefer_bearing=116.0,
        sign_text="FERRY EAST - 223 m to the East Isle. Longship moored here.",
        why=("Spawn-island end of the only ferry on this world that a bridge "
             "cannot replace. MEASURED: the gap from the spawn island to the "
             "east isle (0.396 km2, 99% BlackForest) is 223.2 m over water 30.0 m "
             "deep -- 2.3x my measured 96 m absolute iron-stringer span ceiling "
             "and 30 m of depth where a wood pile reaches ~20 m."),
    ),
    dict(
        id="harbour-vestvik",
        role="harbour_place",
        kind="jetty",
        patch="westisle",
        root=(-1031.5, 1656.5),
        bearing=0.0,
        length_m=22.0,
        width_tiles=3,
        portal_tag="x-vestvik",
        sign_text="VESTVIK HARBOUR - the village is 94 m inland to the south.",
        why=("The waterfront of Settlements' west-isle village Vestvik at "
             "(-906, 1846), which it reported as 94 m from water. I MEASURE the "
             "nearest SHORELINE sample (land h>30.5 4-connected to water) at "
             "(-1029.5, 1896.5), 133.7 m from the village centre -- and that "
             "shoreline is too shallow for a 22 m pier at any bearing (my aimer "
             "refused it). The nearest shoreline that takes one is "
             "(-1031.5, 1656.5), 227.3 m from the village, MEASURED berth 7.11 m "
             "with 0.61 water fraction in a 35 m disc. One waterfront, "
             "one owner each side of the shoreline: Settlements owns the village, "
             "this owns the quay. Bearing and root are MEASURED by aim_seaward, "
             "not assumed from the village bearing."),
    ),
    dict(
        id="ferry-terminal-eastisle",
        role="ferry_terminal_place",
        kind="jetty",
        root=(1953.0, 566.0),
        bearing=155.0,
        length_m=20.0,
        width_tiles=2,
        prefer_bearing=296.0,
        portal_tag="x-ferry-e",
        sign_text="FERRY WEST - 223 m to the main island. Longship moored here.",
        why=("East-isle end of the same ferry. Paired with ferry-terminal-east "
             "across 223.2 m of open water; both ends get a jetty, a sign naming "
             "the far shore, and a Longship."),
    ),
]

# Boats. One per ferry terminal, so a boat that has drifted at one end does not
# strand the crossing.
# Which hub-side portal each of my tags pairs with. A tag must have EXACTLY two
# ends (MEASURED: TeleportWorld pairing is exact string equality with a uniform
# random draw among equals, so a third end makes the destination a coin flip and
# one end makes a one-way trip). The hub end is WayFinding's to place; these
# names are what I have asked it for and the ledger records them as UNASSIGNED
# until it confirms.
PAIR_ENDS = {
    "x-harbour": "WayFinding: portal hall hub end",
    "x-vestvik": "WayFinding: portal hall hub end",
    "x-ferry-e": "WayFinding: portal hall hub end",
}

BOATS = [
    dict(id="ferry-boat-east", terminal="ferry-terminal-east", prefab="VikingShip"),
    dict(id="ferry-boat-eastisle", terminal="ferry-terminal-eastisle",
         prefab="VikingShip"),
    dict(id="boathouse-longship", terminal="boathouse-temple-strait",
         prefab="VikingShip"),
]



FERRY_ROUTES = [
    dict(id="ferry-east-isle", a="ferry-terminal-east", b="ferry-terminal-eastisle",
         why=("The only crossing on this world a bridge cannot replace. MEASURED: "
              "the spawn island and the east isle come no closer than 223.2 m and "
              "the water between them reaches the y=0 ocean floor clamp, 30.0 m "
              "down. My measured ceilings are a 96 m absolute free span (iron "
              "stringer, 1500 max support / 20 min at 0.076923 per metre) and "
              "~20 m of pile reach (HardWood log piles, 140/10 at 0.1 vertical). "
              "This crossing is 2.3x the first and 1.5x the second, so it is a "
              "ferry and not a bridge I declined to build.")),
]


def ferry_routes(fields: dict, records: dict) -> list[dict]:
    """Measure the water between two terminal heads, because a ferry route with
    a sandbar in it is not a route.

    The test is not 'is there water': it is one CONTIGUOUS wet run at or above
    the draught a Longship needs. MEASURED Ship.m_waterLevelOffset = 1.5 on
    VikingShip, so the shallowest sample on the line must leave more than that.
    """
    out = []
    for spec in FERRY_ROUTES:
        ra, rb = records[spec["a"]], records[spec["b"]]
        fld = fields[ra["patch"]]
        ha, hb = ra["head_xz"], rb["head_xz"]
        p = W.profile(fld, ha[0], ha[1], hb[0], hb[1])
        shallowest = float(max(p.heights))
        out.append(dict(
            id=spec["id"], a=spec["a"], b=spec["b"],
            head_a=ha, head_b=hb,
            water_route_m=round(p.length_m, 1),
            wet_fraction=round(p.wet_fraction, 3),
            wet_runs=p.wet_runs,
            max_depth_m=round(p.max_depth_m, 2),
            min_depth_m=round(A.WATER_LEVEL - shallowest, 2),
            navigable=(p.wet_fraction == 1.0 and p.wet_runs == 1
                       and A.WATER_LEVEL - shallowest > 1.5),
            draught_required_m=1.5,
            bearing_deg=round(p.bearing_deg, 2),
            why=spec["why"],
        ))
    return out


def profiles(fld: W.Field):
    """Ground and bed samplers. Same function for both: the generated height IS
    the bed under water and the ground on land, and conflating them is the point
    -- a pile is embedded relative to whatever the generator produced."""

    def ground(x: float, z: float) -> float:
        if not fld.contains(x, z, 1.0):
            raise SystemExit(f"({x}, {z}) is outside patch {fld.id}; "
                             "widen the patch rather than guessing a height")
        return fld.height_at(x, z)

    return ground, ground


def aim_seaward(fld: W.Field, near: tuple[float, float], length_m: float,
                *, min_head_depth: float = 2.5, prefer_bearing: float | None = None,
                search_r: float = 24.0) -> dict:
    """Find where the shore is and which way is out to sea, by measuring.

    This function exists because I got it wrong by hand first, and the failure
    was silent in exactly the way this project keeps paying for: a hand-picked
    bearing put a 20 m ferry jetty with 0 of 22 deck tiles over water and its
    head 4.4 m UP a hillside. A jetty's bearing is not a taste decision, it is
    a measurement, so it is measured here:

      * the root is snapped to a genuine shoreline sample -- land (h > 30.5)
        with water 4-connected to it -- within `search_r` of the requested point,
        so the landward end keys into the bank instead of starting in the water;
      * the bearing is swept at 5 deg and scored on what the deck would actually
        stand over: the fraction of 1 m samples along the line that are wet, the
        depth at the head against the 2.5 m a Longship needs (MEASURED
        Ship.m_waterLevelOffset = 1.5 on VikingShip), and a requirement that the
        outer half of the line is CONTIGUOUSLY wet so the pier does not cross a
        sandbar and come back;
      * `prefer_bearing` adds a mild preference for pointing at a known target
        (the far ferry terminal), never enough to override the water test.
    """
    from scipy import ndimage
    ci, cj = fld.index(*near)
    r = int(search_r)
    sub_h = fld.heights[ci - r:ci + r + 1, cj - r:cj + r + 1]
    sub_land = sub_h > W.LAND_LEVEL
    sub_wet = sub_h < W.WATER_LEVEL
    k = ndimage.generate_binary_structure(2, 1)
    shore = sub_land & ndimage.binary_dilation(sub_wet, k)
    cands = np.argwhere(shore)
    if not len(cands):
        raise SystemExit(f"no shoreline sample within {search_r} m of {near}")
    # nearest shoreline sample to the requested point
    d2 = (cands[:, 0] - r) ** 2 + (cands[:, 1] - r) ** 2
    si, sj = cands[int(np.argmin(d2))]
    root = (float(fld.world_x(cj - r + sj)), float(fld.world_z(ci - r + si)))

    best = None
    n = int(length_m) + 1
    for bearing in range(0, 360, 5):
        ux, uz = A._unit(float(bearing))
        hs = []
        ok = True
        for t in range(n):
            x, z = root[0] + ux * t, root[1] + uz * t
            if not fld.contains(x, z, 1.0):
                ok = False
                break
            hs.append(fld.height_at(x, z))
        if not ok:
            continue
        wet = [h < W.WATER_LEVEL for h in hs]
        head_depth = W.WATER_LEVEL - hs[-1]
        outer = wet[len(wet) // 2:]
        if head_depth < min_head_depth or not all(outer):
            continue
        score = (sum(wet) / len(wet)) * 10.0 + min(head_depth, 8.0)
        if prefer_bearing is not None:
            delta = abs((bearing - prefer_bearing + 180) % 360 - 180)
            score -= delta / 180.0 * 2.0
        if best is None or score > best["score"]:
            best = dict(score=score, bearing=float(bearing), root=list(root),
                        wet_fraction=round(sum(wet) / len(wet), 3),
                        head_depth_m=round(head_depth, 2),
                        root_ground_y=round(hs[0], 2))
    if best is None:
        raise SystemExit(
            f"no bearing from {root} keeps a {length_m:.0f} m pier over "
            f"contiguous water at least {min_head_depth} m deep; the site is "
            "too shallow or too open for this length")
    return best




ZONE_SIZE = 64.0  # MEASURED: ZoneSystem.m_zoneSize = 64 m, 1 m TCData samples.


def zone_of(x: float, z: float) -> tuple[int, int]:
    """Zone index. MEASURED convention: ZoneSystem::GetZone is
    Mathf.FloorToInt(pos/64 + 0.5), so zone (0,0) is CENTRED on the origin and
    its centre is (zx*64, zz*64)."""
    return (math.floor(x / ZONE_SIZE + 0.5), math.floor(z / ZONE_SIZE + 0.5))


def zones_within(pos: tuple[float, float], reach: float) -> list[tuple[int, int]]:
    """Every zone whose CENTRE is within `reach` of `pos` -- the selection
    `zones_generate` actually makes."""
    span = int(reach // ZONE_SIZE) + 2
    zx0, zz0 = zone_of(pos[0], pos[1])
    out = []
    for zx in range(zx0 - span, zx0 + span + 1):
        for zz in range(zz0 - span, zz0 + span + 1):
            if math.hypot(zx * ZONE_SIZE - pos[0], zz * ZONE_SIZE - pos[1]) <= reach:
                out.append((zx, zz))
    return sorted(out)


def prepare_block(fld: W.Field, pieces: list[A.Placed], sid: str) -> dict:
    """What has to happen to this site BEFORE the pieces go in, and what must
    never happen to it.

    The ordering is GroundTruth's, proven: generate -> clear -> flatten -> place.
    My structures take the first two steps and REFUSE the third, and both halves
    of that are load-bearing:

      * GENERATE is required even here. MEASURED by GroundTruth: on a server with
        no peers, `Heightmap::Generate` -> `ApplyModifiers` finds its compiler by
        scanning `TerrainComp::s_instances` for INSTANTIATED components, finds
        none, and `SpawnZone` then runs `PlaceVegetation` against the unmodified
        collider in the same call. Vegetation planted after my deck exists would
        stand through the landward end of the pier at generated height. So the
        site's zones are generated and cleared first, with the structure placed
        after -- ordinary ordering, no exception.
      * FLATTEN IS FORBIDDEN. This is the exception, and it is the operator's own
        rule: uneven bases are expected over water. At `early-dock` the site
        solved to `water_dist_m 0.0`, the pad levelled the whole rectangle, and
        after flattening there was no water left under the footprint -- so an
        over-water pier body stood on dry levelled ground and read as the
        floating defect. The clear radius below is deliberately sized to the
        LANDWARD end only, because clearing over water has nothing to remove and
        a wide radius near a POI is how the old world lost mod content.
    """
    touched = sorted({zone_of(p.x, p.z) for p in pieces})
    land = [p for p in pieces if fld.height_at(p.x, p.z) > W.LAND_LEVEL]
    if land:
        cx = sum(p.x for p in land) / len(land)
        cz = sum(p.z for p in land) / len(land)
        radius = max(6.0, max(math.hypot(p.x - cx, p.z - cz) for p in land) + 4.0)
    else:
        cx = cz = 0.0
        radius = 0.0
    # `zones_generate pos=X,Z max=M` selects zones by the distance from `pos` to
    # the ZONE CENTRE, not by a bounding square. MEASURED live: pos=(-318,-64)
    # max=82 answered "5 zones generated", and exactly five zone centres lie
    # within 82 m of that point ((-320,-64) at 2 m, then (-384,-64) at 66,
    # (-256,-64) at 62, (-320,-128) and (-320,0) at 64.03) while a square of side
    # 82 would have covered nine. So the generated set is computed the same way
    # here, and the ledger declares every zone the command will actually touch
    # rather than only the ones the structure stands in -- a declaration that
    # undercounts is a postcondition that passes for the wrong reason.
    pos = (round(cx, 1), round(cz, 1))
    reach = max(ZONE_SIZE / 2,
                max(math.hypot(z[0] * ZONE_SIZE - pos[0],
                               z[1] * ZONE_SIZE - pos[1]) for z in touched))
    generated = zones_within(pos, reach)
    return dict(
        order=["zones_generate", "objects_clear", "spawn_plan"],
        generate_pos=list(pos),
        generate_max_m=round(reach, 1),
        zones_touched_by_structure=[list(z) for z in touched],
        zones=[list(z) for z in generated],
        zone_centres=[[z[0] * ZONE_SIZE, z[1] * ZONE_SIZE] for z in generated],
        zone_generated_before_required=True,
        clear=(dict(centre=[round(cx, 1), round(cz, 1)], radius_m=round(radius, 1),
                    reason="landward end of an over-water structure; the wet part "
                           "has no vegetation to remove")
               if land else None),
        flatten="FORBIDDEN",
    )


def boat_command(b: dict) -> str:
    """A boat is a real, persistent ZDO. MEASURED from
    `data/piece_material.tsv`: all five ship prefabs carry
    `ZNetView.m_persistent = 1` and `m_type = 1` (ZDO.ObjectType.Prioritized),
    a `Rigidbody` (mass 1000 on Karve/Raft/Trailership, 2000 on VikingShip, 3000
    on VikingShip_Ashlands), a `Piece`, and a `WearNTear` of material Wood with
    300-3000 hp. So a spawned Longship is saved to the database, not a prop."""
    return (f"spawn_object {b['prefab']}"
            f" pos={A.fmt(b['z'])},{A.fmt(b['x'])},{A.fmt(b['y'])}"
            f" rot={A.fmt(b['yaw'])},0,0"
            f" from=0,0,0")


def fixtures_for(fld: W.Field, spec: dict) -> list[A.Placed]:
    """The sign, its post, and the portal -- all on DRY LAND, behind the root.

    Why dry land and not out on the pier, which would look better: MEASURED from
    `data/piece_material.tsv`, `Piece.m_noInWater` is 1 on `portal_wood` (and on
    `piece_bed02`, `piece_chest_wood`, `piece_workbench`). `spawn_object`
    bypasses placement validation so a portal over water WOULD be created, but
    the game's own data says that prefab is not meant to stand in water, and
    `assemble.verify()` fails the assembly if one does. So the portal goes
    inland of the shoreline and the pier stays a pier.
    """
    ux, uz = A._unit(spec["bearing"])
    root = spec["root"]
    out: list[A.Placed] = []
    sid = spec["id"]

    def inland(d: float) -> tuple[float, float]:
        return root[0] - ux * d, root[1] - uz * d

    if spec.get("sign_text"):
        sx, sz = inland(3.0)
        g = fld.height_at(sx, sz)
        post = A.facts()["wood_pole2"]
        # pole2 is 2 m tall with its origin at mid-height, so origin = g + 1.0
        # puts its foot exactly on the ground.
        out.append(A.Placed("wood_pole2", sx, g + 1.0, sz,
                            spec["bearing"], f"{sid}/sign_post"))
        out.append(A.Placed("sign", sx, g + 1.7, sz,
                            spec["bearing"] + 180.0, f"{sid}/sign"))
    if spec.get("portal_tag"):
        px, pz = inland(7.0)
        g = fld.height_at(px, pz)
        # portal_wood's solid starts at y = -0.007 and rises 3.29 m, so origin at
        # ground - 0.05 keys its base into the ground rather than hovering.
        out.append(A.Placed("portal_wood", px, g - 0.05, pz,
                            spec["bearing"] + 180.0, f"{sid}/portal"))
    return out


def fixture_commands(fld: W.Field, spec: dict, pieces: list[A.Placed]) -> list[str]:
    """Commands for the fixtures, with the ZDO strings attached.

    The blobs are built with `blueprints/data_entry.py`, the one decoder/encoder
    in this repo, and the tag is validated with `blueprints/fixtures.py`'s
    `portal_tag_problem` rather than a second copy of the rule. A sign's text has
    no 10-character limit -- that cap is `TeleportWorld::Interact`'s retype box,
    `Sign` just reads the ZDO string "text".
    """
    cmds = []
    for p in pieces:
        blob = ""
        if p.role.endswith("/sign"):
            e = DE.DataEntry()
            e.strings[DE.stable_hash("text")] = spec["sign_text"]
            blob = e.encode()
        elif p.role.endswith("/portal"):
            tag = spec["portal_tag"]
            problem = FX.portal_tag_problem(tag)
            if problem:
                raise SystemExit(f"{spec['id']}: portal tag {tag!r} rejected: {problem}")
            e = DE.DataEntry()
            e.strings[DE.stable_hash("tag")] = tag
            blob = e.encode()
        cmd = A.command(p)
        if blob:
            cmd += f" data={blob}"
        cmds.append(cmd)
    return cmds


def boat_for(fld: W.Field, spec: dict, prefab: str) -> dict:
    """Place a boat at the berth and MEASURE that it can float there.

    Offset from the pier centreline is sized off the hull, not guessed: MEASURED
    solid colliders give `VikingShip` a half-width of 4.58 m, so a 4 m-wide pier
    (half-width 2 m) needs at least 6.58 m of offset before the hull stops
    intersecting the deck. 7.5 m is used. A slip instead berths the boat on its
    own centreline, between fingers 11 m apart.

    Spawn Y is the water plane exactly. The hull's solid spans y[-0.73, 10.91],
    so the keel starts 0.73 m submerged and buoyancy settles it from there; a
    boat spawned high FALLS, and `Ship.m_waterImpactDamage` is 10 per hard
    landing (MEASURED on all five ship prefabs).
    """
    ux, uz = A._unit(spec["bearing"])
    lx, lz = uz, -ux
    along = spec["length_m"] * 0.6
    off = 0.0 if spec["kind"] == "slip" else 7.5
    bx = spec["root"][0] + ux * along + lx * off
    bz = spec["root"][1] + uz * along + lz * off
    ground = fld.height_at(bx, bz)
    depth = A.WATER_LEVEL - ground
    return dict(prefab=prefab, x=round(bx, 2), y=A.WATER_LEVEL, z=round(bz, 2),
                yaw=spec["bearing"], berth_depth_m=round(depth, 2),
                bed_y=round(ground, 2),
                deep_enough=depth >= 2.5,
                offset_from_centreline_m=off)


def deck_continuity(fld: W.Field, pieces, bank_a, bank_b, deck_y: float) -> dict:
    """The two acceptance criteria the operator will physically walk into:
    no step at either end, and no deck under water."""
    f = A.facts()
    decks = [p for p in pieces if p.role.endswith("/deck") or p.role.endswith("/head")]
    tops = [p.y + f[p.prefab].hi[1] for p in decks]
    ends = []
    for bx, bz in (bank_a, bank_b):
        ground = fld.height_at(bx, bz)
        ends.append(dict(xz=[bx, bz], generated_ground_y=round(ground, 3),
                         deck_y=deck_y,
                         step_vs_generated_m=round(deck_y - ground, 3),
                         step_vs_levelled_ribbon_m=0.0,
                         ribbon_level_y_required=deck_y))
    return dict(
        deck_top_min=round(min(tops), 4),
        deck_top_max=round(max(tops), 4),
        deck_is_level=abs(max(tops) - min(tops)) < 1e-3,
        deck_clears_water_by_m=round(min(tops) - A.WATER_LEVEL, 3),
        deck_under_water=min(tops) <= A.WATER_LEVEL,
        ends=ends,
    )


def build(fields: dict, default_patch: str) -> dict:
    out = {"structures": [], "ledger": [], "verify": {}, "commands": {}}

    for spec in BRIDGES:
        fld = fields[spec.get("patch", default_patch)]
        ground, bed = profiles(fld)
        p = W.profile(fld, *spec["bank_a"], *spec["bank_b"])
        pieces = A.bridge(spec["bank_a"], spec["bank_b"], spec["bearing"],
                          A.DECK_Y, bed, width_tiles=spec["width_tiles"],
                          name=spec["id"])
        verdict = A.verify(pieces, ground, note=spec["id"])
        cont = deck_continuity(fld, pieces, spec["bank_a"], spec["bank_b"], A.DECK_Y)
        measured = dict(
            span_m=round(p.span_m, 1),
            wet_runs=p.wet_runs,
            bank_a_y=round(p.bank_a_y, 2),
            bank_b_y=round(p.bank_b_y, 2),
            max_depth_m=round(p.max_depth_m, 2),
            bed_min_y=round(p.bed_min_y, 2),
            bearing_deg=round(p.bearing_deg, 2),
        )
        rec = dict(
            id=spec["id"], role=spec["role"], kind="bridge",
            provisional=False,
            bank_a=list(spec["bank_a"]), bank_b=list(spec["bank_b"]),
            bearing_deg=spec["bearing"], deck_y=A.DECK_Y,
            deck_width_m=spec["width_tiles"] * 2.0,
            measured=measured, continuity=cont,
            zdo_cost=len(pieces),
            flatten="FORBIDDEN",
            flatten_reason=("piles stand on the generated bed; a terrain_write "
                            "here removes the water the piles are driven into"),
            road_contract=dict(
                owner_of_coordinates="Crossings",
                agreed_with="RoadNet",
                ribbon_level_last_m=6.0,
                ribbon_level_y=A.DECK_Y,
                ribbon_width_m=spec["width_tiles"] * 2.0,
                straight_bearing_required_m=6.0),
            why=spec["why"],
            structural=dict(ok=verdict["ok"],
                            min_support_by_role=verdict["support_min_by_role"],
                            failures=verdict["failures"]),
            prepare=prepare_block(fld, pieces, spec["id"]),
            patch=spec.get("patch", default_patch),
        )
        out["structures"].append(rec)
        out["verify"][spec["id"]] = verdict
        cmds = [A.command(x) for x in pieces]
        rec["fixtures"] = dict(sign_text=None, portal_tag=None, pieces=[])
        out["ledger"] += LO.ops_for(
            rec, cmds,
            [dict(xz=[p.x, p.z]) for p in pieces],
            fixture_cmds={}, plan_ref=f"out/{spec['id']}.commands.txt",
            pair_ends={})
        out["commands"][spec["id"]] = cmds

    for spec in WATERFRONT:
        fld = fields[spec.get("patch", default_patch)]
        ground, bed = profiles(fld)
        aim = aim_seaward(fld, tuple(spec["root"]), spec["length_m"],
                          prefer_bearing=spec.get("prefer_bearing"))
        spec = dict(spec)
        spec["root"] = tuple(aim["root"])
        spec["bearing"] = aim["bearing"]
        spec["aim"] = aim
        if spec["kind"] == "slip":
            pieces = A.slip(spec["root"], spec["bearing"], spec["length_m"],
                            spec["inner_width_m"], A.DECK_Y, bed, name=spec["id"])
        else:
            pieces = A.jetty(spec["root"], spec["bearing"], spec["length_m"],
                             A.DECK_Y, bed,
                             width_tiles=spec.get("width_tiles", 2),
                             name=spec["id"])
        fixt = fixtures_for(fld, spec)
        verdict = A.verify(pieces + fixt, ground, note=spec["id"])
        f = A.facts()
        decks = [p for p in pieces if p.role.endswith("/deck") or p.role.endswith("/head")]
        tops = [p.y + f[p.prefab].hi[1] for p in decks]
        # A waterfront structure's whole justification is that it stands OVER
        # WATER. Measure it: how much of the deck footprint has water under it.
        wet = sum(1 for p in decks if fld.height_at(p.x, p.z) < A.WATER_LEVEL)
        head_x, head_z = (spec["root"][0] + A._unit(spec["bearing"])[0] * spec["length_m"],
                          spec["root"][1] + A._unit(spec["bearing"])[1] * spec["length_m"])
        rec = dict(
            id=spec["id"], role=spec["role"], kind=spec["kind"],
            provisional=False,
            root=list(spec["root"]), bearing_deg=spec["bearing"],
            length_m=spec["length_m"], deck_y=A.DECK_Y,
            head_xz=[round(head_x, 1), round(head_z, 1)],
            aim=spec["aim"],
            measured=dict(
                root_ground_y=round(fld.height_at(*spec["root"]), 2),
                head_ground_y=round(fld.height_at(head_x, head_z), 2),
                head_depth_m=round(max(0.0, A.WATER_LEVEL
                                       - fld.height_at(head_x, head_z)), 2),
                deck_tiles=len(decks),
                deck_tiles_over_water=wet,
                deck_over_water_fraction=round(wet / max(len(decks), 1), 3),
            ),
            zdo_cost=len(pieces),
            flatten="FORBIDDEN",
            flatten_reason=(
                "over-water structure. MEASURED "
                f"{wet}/{len(decks)} deck tiles have water under them; levelling "
                "the footprint removes that water and the pier reads as floating, "
                "which is the early-dock defect verbatim."),
            why=spec["why"],
            structural=dict(ok=verdict["ok"],
                            min_support_by_role=verdict["support_min_by_role"],
                            failures=verdict["failures"]),
            deck_top_min=round(min(tops), 4) if tops else None,
            deck_clears_water_by_m=round(min(tops) - A.WATER_LEVEL, 3) if tops else None,
            fixtures=dict(
                sign_text=spec.get("sign_text"),
                portal_tag=spec.get("portal_tag"),
                portal_pair_end_owner=("WayFinding (hub end)"
                                       if spec.get("portal_tag") else None),
                pieces=[dict(prefab=p.prefab, role=p.role,
                             xz=[round(p.x, 2), round(p.z, 2)], y=round(p.y, 3),
                             yaw=p.yaw) for p in fixt],
            ),
            boats=[boat_for(fld, spec, b["prefab"]) for b in BOATS
                   if b["terminal"] == spec["id"]],
            prepare=prepare_block(fld, pieces + fixt, spec["id"]),
            patch=spec.get("patch", default_patch),
        )
        out["structures"].append(rec)
        out["verify"][spec["id"]] = verdict
        fcmds = fixture_commands(fld, spec, fixt)
        cmds = ([A.command(x) for x in pieces] + fcmds
                + [boat_command(b) for b in rec["boats"]])
        out["ledger"] += LO.ops_for(
            rec, cmds,
            [dict(xz=[p.x, p.z]) for p in pieces + fixt],
            fixture_cmds={p.role: c for p, c in zip(fixt, fcmds)},
            plan_ref=f"out/{spec['id']}.commands.txt",
            pair_ends=PAIR_ENDS)
        out["commands"][spec["id"]] = cmds

    out["ledger"] = LO.boundary_notes() + out["ledger"]
    by_id = {r["id"]: r for r in out["structures"]}
    out["ferry_routes"] = ferry_routes(fields, by_id)
    return out



def write_structures_yaml(path: Path, res: dict) -> None:
    """The interchange file Main assigned to Crossings: one record per
    structure, one file, one producer, read-only to every consumer.

    It is emitted rather than hand-maintained so it cannot drift from the
    measurements: every number in it came out of this run, and re-running
    `plan.py` is the only way to change it.
    """
    try:
        import yaml
    except ImportError:
        path.with_suffix(".json").write_text(json.dumps(res, indent=1))
        return
    doc = dict(
        owner="Crossings",
        generated_by="tools/jumpstart/crossings/plan.py",
        world="Ulfsland",
        seed="Pirate68",
        seed_hash=147627509,
        height_field=dict(
            path=FIELD, format="VHPATCH1", step_m=1.0, rivers=True,
            provenance=("produced by tools/jumpstart/blueprints/run_patchscan.sh, "
                        "which runs WorldGenerator.Initialize with full "
                        "pregeneration. NOT the 8 m .biome overview grid, which is "
                        "written with SEEDSCAN_PREGEN=0 and is river-free.")),
        datums=dict(
            water_level_y=A.WATER_LEVEL,
            water_level_source="MEASURED: ZoneSystem::c_WaterLevel, static literal "
                               "float32(30.) in assembly_valheim.dll 1.0.12",
            deck_y=A.DECK_Y,
            deck_y_source="early-dock's declared dock-DECK freeboard (target_y 30.6), "
                          "adopted by RoadNet as the network water-crossing datum",
            land_level_y=W.LAND_LEVEL,
            deck_pitch_m=2.0,
            deck_pitch_source="MEASURED: wood_floor is a 2.000 x 0.130 x 2.000 solid, "
                              "top face +0.0969 above its own origin"),
        span_limits=dict(
            wood_bent_spacing_m=8.0,
            wood_free_span_absolute_m=12.0,
            iron_free_span_safe_m=72.0,
            iron_free_span_absolute_m=96.0,
            pile_reach_hardwood_m=20.0,
            source=("MEASURED WearNTear::GetMaterialProperties + UpdateSupport + "
                    "HaveSupport in assembly_valheim.dll 1.0.12; support decays "
                    "multiplicatively as support*(1-loss*(d+0.1)) and the piece "
                    "dies below minSupport")),
        totals=dict(structures=len(res["structures"]),
                    zdo_total=sum(r["zdo_cost"] for r in res["structures"])),
        structures=res["structures"],
        ferry_routes=res["ferry_routes"],
        boundaries=[
            "Fords are TERRAIN and belong to RoadNet. Crossings owns no ford.",
            "Road ribbons belong to RoadNet. Crossings writes no terrain at all.",
            "Lighthouses belong to Settlements. Crossings noted two over-water "
            "lighthouse bodies for them: drake-lighthouse.blueprint (1255 pieces, "
            "14.5 x 14.5 m, grounds on posts) and "
            "hs_plains_ulf_the_builder_lighthouse.blueprint (1267 pieces).",
            "Settlements owns the village of Vestvik; Crossings owns its quay.",
            "Every structure here carries flatten: FORBIDDEN. A terrain_write "
            "overlapping one is refused by the ledger replay driver.",
        ],
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default=FIELD)
    ap.add_argument("--patch", default=PATCH)
    ap.add_argument("--out", default=str(HERE / "out"))
    args = ap.parse_args(argv)

    wanted = {args.patch} | {s.get("patch", args.patch)
                             for s in list(BRIDGES) + list(WATERFRONT)}
    fields = W.load(args.field, only=wanted)
    missing = sorted(wanted - set(fields))
    if missing:
        raise SystemExit(f"patches {missing} are not in {args.field}")
    res = build(fields, args.patch)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "verify.json").write_text(json.dumps(res["verify"], indent=1))
    with (outdir / "ledger.jsonl").open("w") as fh:
        for op in res["ledger"]:
            fh.write(json.dumps(op) + "\n")
    for sid, cmds in res["commands"].items():
        (outdir / f"{sid}.commands.txt").write_text("\n".join(cmds) + "\n")

    write_structures_yaml(HERE / "structures.yaml", res)
    total = sum(r["zdo_cost"] for r in res["structures"])
    bad = [r["id"] for r in res["structures"] if not r["structural"]["ok"]]
    summary = dict(structures=len(res["structures"]), zdo_total=total,
                   failed=bad,
                   ferry_routes=[dict(id=r["id"], m=r["water_route_m"],
                                      navigable=r["navigable"])
                                 for r in res["ferry_routes"]])
    print(json.dumps(dict(summary=summary, structures=res["structures"],
                          ferry_routes=res["ferry_routes"]), indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
