#!/usr/bin/env python3
"""Turn a Route into a piece-by-piece plan, and cost it honestly.

THE COST MODEL, and why the recommendation is terrain rather than paving.

MEASURED 2026-09-14 with internal/worldintel.ParseDB over read-only copies of three
real fleet-world backups:

  world       objects  construction  zones  TerrainModifier  _TerrainCompiler  piece_pavedroad
  Vangard      34,871        32,704  3,880                0                 0                0
  Storgard     75,547        72,326  5,852                0                 0                0
  Hrafnheim     1,366           794    798                0                 0                0

111,784 persistent objects and 105,824 construction pieces across 10,530 generated
zones, and NOT ONE terrain object. Storgard's 732 distinct prefabs contain nothing
matching terrain / paved / path / road / dirt / cultivate.

So: hoe work -- levelling, paving, pathing, cultivating -- costs ZERO persistent
ZDOs. The deltas live in the zone's own heightmap and paint arrays, which is a fixed
per-zone cost whether you edit one square metre or all 4,096 of them. Pieces cost
one ZDO each.

That inverts the naive plan. A 3 km paved road at stone_floor_2x2, two pieces wide,
is 3,000 ZDOs -- more construction than the whole of Hrafnheim, for a road. The same
3 km hoed as a paved-road ribbon is ZERO, and the only ZDOs are the marker posts
that make it read as a road from a distance, the decking at bridges and junctions,
and the bridges themselves.

CONSEQUENCE, inferred from the same measurement: piece_pavedroad has no persistent
ZNetView, which is why it never appears in a save. So `spawn piece_pavedroad x y z`
over RCON cannot pave -- there is nothing for the spawn to leave behind. Road terrain
work is ADMIN-CLIENT-SEAT ONLY. That happens to be the operator's hard rule anyway,
but it is now a technical fact rather than a policy: the RCON route can place the
markers, the decks and the shelters, and it cannot place the road.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import JUMPSTART
from .router import Route
from .terrain import WATER_LEVEL

_LIBRARY = JUMPSTART / "library" / "data" / "library_manifest.json"
_PIECES: dict[str, int] | None = None


def blueprint_pieces(name: str) -> int:
    """Pieces in a library blueprint, i.e. the ZDOs one stamp costs.

    A blueprint stamp is not one object. speeds-bridge2 is 63 pieces and
    PiNoKi_SmallHut is 48 (MEASURED, library_manifest.json), so costing a stamp as
    1 understates a road with four bridges by two hundred ZDOs.
    """
    global _PIECES
    if _PIECES is None:
        import json

        _PIECES = {e["name"]: int(e["pieces"]) for e in json.loads(_LIBRARY.read_text())["entries"]}
    return _PIECES.get(name, 0)

#: Emission channels, in the order an admin would actually work.
TERRAIN = "terrain"     # hoe work: zero ZDOs, admin seat only
PIECE = "piece"         # a placeable prefab: one ZDO, RCON-spawnable
BLUEPRINT = "blueprint"  # a library stamp: many ZDOs, admin seat (PlanBuild/Infinity Hammer)


@dataclass
class Item:
    channel: str
    kind: str
    prefab: str | None
    x: float
    y: float
    z: float
    yaw: float = 0.0
    count: int = 1
    radius_m: float | None = None
    note: str = ""

    @property
    def zdo(self) -> int:
        if self.channel == TERRAIN:
            return 0
        if self.channel == BLUEPRINT:
            return self.count * blueprint_pieces(self.prefab or "")
        return self.count

    def rcon(self) -> str | None:
        """The ValheimRcon `spawn` line, or None when RCON cannot place this.

        MEASURED behaviour of the spawn route: it resolves the prefab from ZNetScene,
        instantiates it, calls ZNetView.FinishGhostInit and destroys the GameObject,
        leaving a persistent ZDO. That works for anything with a ZNetView. It does
        nothing useful for a TerrainModifier, and it cannot stamp a blueprint.
        """
        if self.channel != PIECE or not self.prefab:
            return None
        return (
            f"spawn {self.prefab} {self.x:.1f} {self.y:.2f} {self.z:.1f} "
            f"-rotation 0 {self.yaw:.1f} 0"
        )

    def as_dict(self) -> dict[str, Any]:
        out = {
            "channel": self.channel,
            "kind": self.kind,
            "prefab": self.prefab,
            "x": round(self.x, 1),
            "y": round(self.y, 2),
            "z": round(self.z, 1),
            "yaw": round(self.yaw, 1),
            "count": self.count,
            "zdo": self.zdo,
        }
        if self.radius_m is not None:
            out["radius_m"] = self.radius_m
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class Plan:
    route_from: str
    route_to: str
    items: list[Item]
    surface: str
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def zdo_cost(self) -> int:
        return sum(i.zdo for i in self.items)

    def by_channel(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for i in self.items:
            slot = out.setdefault(i.channel, {"items": 0, "zdo": 0})
            slot["items"] += i.count
            slot["zdo"] += i.zdo
        return out

    def rcon_lines(self) -> list[str]:
        return [line for i in self.items if (line := i.rcon())]

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.route_from,
            "to": self.route_to,
            "surface": self.surface,
            "zdo_cost": self.zdo_cost(),
            "by_channel": self.by_channel(),
            "rcon_placeable": len(self.rcon_lines()),
            "blockers": self.blockers,
            "notes": self.notes,
            "items": [i.as_dict() for i in self.items],
        }


def _yaw(ax: float, az: float, bx: float, bz: float) -> float:
    """Euler Y in degrees, matching placements.yaml's rotation convention.

    MEASURED there: ValheimRcon `spawn -rotation <x> <y> <z>` calls Quaternion.Euler
    on that vector, so yaw is the Y component and +yaw turns clockwise from above.
    A road piece faces along the segment, so yaw is the segment's bearing from +Z.
    """
    return math.degrees(math.atan2(bx - ax, bz - az)) % 360.0


def _walk(nodes, spacing: float):
    """Yield (x, y, z, yaw, distance_along) every `spacing` metres along a polyline."""
    if len(nodes) < 2:
        return
    carry = 0.0
    travelled = 0.0
    for p, q in zip(nodes, nodes[1:]):
        seg = math.hypot(q.x - p.x, q.z - p.z)
        if seg <= 0:
            continue
        yaw = _yaw(p.x, p.z, q.x, q.z)
        t = carry
        while t <= seg:
            f = t / seg
            yield (
                p.x + (q.x - p.x) * f,
                p.y + (q.y - p.y) * f,
                p.z + (q.z - p.z) * f,
                yaw,
                travelled + t,
            )
            t += spacing
        carry = t - seg
        travelled += seg


def build(route: Route, policy: dict[str, Any] | None = None, cor=None) -> Plan:
    """Emit the plan for one route.

    `cor` is the sampled Corridor. Pass it: a marker post sits offset from the
    centreline, and placing it at the CENTRELINE's height leaves it floating or
    buried by however much the ground falls away in those few metres. With the
    corridor available every piece gets the height at its own coordinate.
    """
    policy = policy or route.policy

    def ground(x: float, z: float, fallback: float) -> float:
        if cor is None or not cor.contains(x, z):
            return fallback
        return cor.at(x, z)[0]
    rp = policy["roadway"]
    bp = policy["bridge"]
    items: list[Item] = []
    blockers: list[str] = []
    notes: list[str] = []

    surface = str(rp["surface"])
    width = float(rp["width_m"])
    pave_step = float(rp["pave_spacing_m"])
    marker_step = float(rp["marker_spacing_m"])
    light_every = int(rp["marker_light_every"])
    deck_area = float(rp["deck_piece_area_m2"])
    junction_r = float(rp["junction_deck_radius_m"])
    wp_step = float(rp["waypoint_spacing_m"])

    # ---- 1. the carriageway itself. On land only: the water runs are bridges.
    land_runs: list[list] = []
    cur: list = []
    for n in route.nodes:
        if n.water:
            if len(cur) > 1:
                land_runs.append(cur)
            cur = []
        else:
            cur.append(n)
    if len(cur) > 1:
        land_runs.append(cur)

    paved = 0
    for run in land_runs:
        for x, y, z, yaw, _ in _walk(run, pave_step):
            items.append(
                Item(
                    channel=TERRAIN,
                    kind="pave",
                    prefab=rp["pave_prefab"],
                    x=x,
                    y=y,
                    z=z,
                    yaw=yaw,
                    radius_m=width / 2.0,
                    note="hoe stamp: level to the centreline, then paved road. 0 ZDOs (MEASURED).",
                )
            )
            paved += 1

    # ---- 2. markers, which are the entire visual budget and the only per-metre ZDOs
    marked = 0
    for run in land_runs:
        for x, y, z, yaw, along in _walk(run, marker_step):
            marked += 1
            lit = light_every > 0 and marked % light_every == 0
            prefab = rp["marker_light_prefab"] if lit else rp["marker_prefab"]
            # Alternate sides so the corridor reads as a road rather than a fence.
            side = 1.0 if marked % 2 else -1.0
            off = (width / 2.0 + 1.0) * side
            ox = x + math.cos(math.radians(yaw)) * off
            oz = z - math.sin(math.radians(yaw)) * off
            items.append(
                Item(
                    channel=PIECE,
                    kind="marker_light" if lit else "marker",
                    prefab=prefab,
                    x=ox,
                    y=ground(ox, oz, y),
                    z=oz,
                    yaw=yaw,
                    note="roadside marker; RCON-spawnable",
                )
            )

    # ---- 3. bridges, with the span check that makes the whole exercise honest
    for c in route.crossings:
        if c.problem:
            blockers.append(f"crossing {c.start} -> {c.end}: {c.problem}")
            continue
        ax, az = c.start
        bx, bz = c.end
        yaw = _yaw(ax, az, bx, bz)
        items.append(
            Item(
                channel=BLUEPRINT,
                kind="bridge",
                prefab=c.blueprint,
                x=(ax + bx) / 2.0,
                y=WATER_LEVEL + max(c.freeboard_start_m, c.freeboard_end_m),
                z=(az + bz) / 2.0,
                yaw=yaw,
                count=max(1, c.spans),
                note=(
                    f"span {c.span_m:.1f} m over {c.max_depth_m:.1f} m of water; "
                    f"blueprint reaches {c.blueprint_span_m} m x {max(1, c.spans)}; "
                    f"abutment freeboard {c.freeboard_start_m:.2f} / {c.freeboard_end_m:.2f} m"
                ),
            )
        )
        for px, py, pz in c.pier_positions:
            items.append(
                Item(
                    channel=PIECE,
                    kind="pier",
                    prefab="stone_pillar",
                    x=px,
                    y=py,
                    z=pz,
                    yaw=yaw,
                    note="multi-span pier; water depth is within the pier limit",
                )
            )
        # Decking at both abutments: the one place paving is worth its ZDOs, because
        # a hoed ramp onto a bridge deck does not join cleanly.
        for ex, ez in (c.start, c.end):
            n = max(1, int(round(math.pi * junction_r**2 / deck_area)))
            items.append(
                Item(
                    channel=PIECE,
                    kind="abutment_deck",
                    prefab=rp["deck_prefab"],
                    x=ex,
                    y=ground(ex, ez, WATER_LEVEL + max(c.freeboard_start_m, c.freeboard_end_m)),
                    z=ez,
                    yaw=yaw,
                    count=n,
                    radius_m=junction_r,
                    note=f"{n} x {rp['deck_prefab']} paving the abutment apron",
                )
            )

    # ---- 4. waypoint shelters, the repeatable module the library exists for
    #
    # Distance is cumulative along the WHOLE route, water included, because a
    # traveller's fatigue does not reset at a bridge. Measuring per land run put the
    # 800 m shelter inside the 16 m run after a bridge and then reported that there
    # was nowhere dry to put it.
    cumulative: list[tuple[float, Any]] = []
    acc = 0.0
    for p, q in zip(route.nodes, route.nodes[1:]):
        acc += math.hypot(q.x - p.x, q.z - p.z)
        cumulative.append((acc, q))
    total = route.length_m
    if total > wp_step:
        for k in range(1, int(total // wp_step) + 1):
            target = k * wp_step
            dry = [(abs(a - target), n) for a, n in cumulative if not n.water]
            if not dry:
                notes.append(f"no dry node anywhere for the {target:.0f} m waypoint shelter; skipped")
                continue
            gap, node = min(dry, key=lambda t: t[0])
            if gap > wp_step / 2:
                notes.append(
                    f"nearest dry ground to {target:.0f} m is {gap:.0f} m away; waypoint shelter skipped"
                )
                continue
            # Face the shelter back at the road it serves.
            idx = next(i for i, (_, n) in enumerate(cumulative) if n is node)
            nxt = cumulative[min(idx + 1, len(cumulative) - 1)][1]
            yaw = _yaw(node.x, node.z, nxt.x, nxt.z)
            x, y, z = node.x, node.y, node.z
            items.append(
                Item(
                    channel=BLUEPRINT,
                    kind="waypoint",
                    prefab="PiNoKi_SmallHut.blueprint",
                    x=(wx := x + math.cos(math.radians(yaw)) * (width / 2.0 + 8.0)),
                    y=ground(wx, (wz := z - math.sin(math.radians(yaw)) * (width / 2.0 + 8.0)), y),
                    z=wz,
                    yaw=yaw,
                    count=1,
                    note="48 pieces; the only redistributable stamp in the corpus",
                )
            )

    notes.append(
        f"carriageway {width:.0f} m wide, {route.land_m:.0f} m of it on land: "
        f"{paved} hoe stamps at {pave_step:.0f} m, 0 ZDOs (MEASURED across 3 worlds)"
    )
    notes.append(
        f"earthworks: {route.over_budget_m:.0f} m of carriageway is steeper than the "
        f"{route.grade_limit} budget, needing about {route.cut_fill_m:.0f} vertical "
        f"metres of cut and fill"
    )
    if surface == "terrain":
        notes.append(
            "TERRAIN WORK IS ADMIN-CLIENT-SEAT ONLY. piece_pavedroad leaves no ZDO "
            "(0 occurrences in 111,784 objects across three worlds), so RCON `spawn` "
            "cannot place it. The RCON plan below covers markers, decks and piers only."
        )
    return Plan(
        route_from=route.a,
        route_to=route.b,
        items=items,
        surface=surface,
        blockers=blockers,
        notes=notes,
    )


def paved_alternative(route: Route, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a fully PAVED carriageway would cost, for comparison.

    This is the number that decides the recommendation, so it is computed rather than
    asserted. A 4 m carriageway decked in 2x2 m pieces is two pieces per 2 m of
    length, i.e. one ZDO per metre.
    """
    policy = policy or route.policy
    rp = policy["roadway"]
    width = float(rp["width_m"])
    area = float(rp["deck_piece_area_m2"])
    across = max(1, int(round(width / math.sqrt(area))))
    along = int(round(route.land_m / math.sqrt(area)))
    pieces = across * along
    return {
        "prefab": rp["deck_prefab"],
        "pieces": pieces,
        "zdo_cost": pieces,
        "pieces_per_m": round(pieces / max(1.0, route.land_m), 2),
        "verdict": (
            f"{pieces} ZDOs for {route.land_m:.0f} m of road. For comparison the whole "
            f"of Hrafnheim is 1,366 persistent objects and Vangard is 34,871 (MEASURED), "
            f"so paving this one road outright is a real fraction of a world's object "
            f"budget and buys nothing a hoed ribbon does not."
        ),
    }


def combine(plans: Iterable[Plan]) -> dict[str, Any]:
    plans = list(plans)
    by_channel: dict[str, dict[str, int]] = {}
    for p in plans:
        for ch, slot in p.by_channel().items():
            agg = by_channel.setdefault(ch, {"items": 0, "zdo": 0})
            agg["items"] += slot["items"]
            agg["zdo"] += slot["zdo"]
    return {
        "legs": [p.route_from + " -> " + p.route_to for p in plans],
        "zdo_cost": sum(p.zdo_cost() for p in plans),
        "by_channel": by_channel,
        "rcon_placeable": sum(len(p.rcon_lines()) for p in plans),
        "blockers": [b for p in plans for b in p.blockers],
    }
