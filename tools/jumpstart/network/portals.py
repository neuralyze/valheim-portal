#!/usr/bin/env python3
"""The portal GRAPH: nodes, tagged pairwise edges, and honest piece accounting.

Vanilla portals pair BY TAG. Two portals sharing a tag are a working pair; a third
portal on the same tag makes the destination a coin toss. So an edge is not a
convenience -- it is literally two portal pieces and one string, and an N-spoke hub
needs N portal pieces in one room. That is where the cost lands, and it is the part
a "hub and spoke" diagram hides.

Topology is `tiered_hub` (see data/road_policy.yaml for why that beat a flat hub and
a ring). Hubs come from installations with role `portal_hub`; at tiers that have no
hub yet, the tier's home base is the implicit hub, because something has to be.

The interesting output is the CAPACITY CHECK. Each hub blueprint's portal count is
MEASURED, from tools/jumpstart/library/data/{corpus,web}_roles.json. Two of the
already-selected "portal" placements contain zero portal pieces:
  god-portaaoo.blueprint  (sandbox-portal-hub)     portals = 0
  Portal13.vbuild         (deepnorth-landing-portal) portals = 0
so their portals have to be added by hand. This module reports that shortfall rather
than assuming a building called a portal hub has portals in it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import DATA, JUMPSTART
from .catalogue import Catalogue, Installation

ROLE_FILES = (
    JUMPSTART / "library" / "data" / "corpus_roles.json",
    JUMPSTART / "library" / "data" / "web_roles.json",
)


def _measured_portal_capacity() -> dict[str, int]:
    """Portal pieces each blueprint actually contains, by manifest file name."""
    out: dict[str, int] = {}
    for path in ROLE_FILES:
        if not path.exists():
            continue
        for e in json.loads(path.read_text()).get("entries", []):
            name = e.get("file")
            if name and e.get("portals") is not None:
                # A file may appear in both dumps; take the larger, since a
                # zero from a partial parse should not mask a measured count.
                out[name] = max(out.get(name, 0), int(e["portals"]))
    return out


@dataclass
class PortalNode:
    id: str
    role: str
    kind: str          # "hub" | "spoke"
    first_tier: int
    blueprint: str | None
    # Portal pieces the blueprint already contains (MEASURED), and pieces the graph
    # needs it to have. shortfall = needed - contained, and is what an admin places.
    contained: int = 0
    needed: int = 0

    @property
    def shortfall(self) -> int:
        return max(0, self.needed - self.contained)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "first_tier": self.first_tier,
            "blueprint": self.blueprint,
            "portal_pieces_needed": self.needed,
            "portal_pieces_in_blueprint": self.contained,
            "portal_pieces_to_place": self.shortfall,
        }


@dataclass
class PortalEdge:
    a: str
    b: str
    tag: str
    kind: str          # "spoke" | "trunk"
    first_tier: int

    def as_dict(self) -> dict[str, Any]:
        return {"a": self.a, "b": self.b, "tag": self.tag, "kind": self.kind, "first_tier": self.first_tier}


@dataclass
class PortalGraph:
    tier: int
    preset: str
    topology: str
    assignment_mode: str
    hub_mesh: str
    nodes: dict[str, PortalNode]
    edges: list[PortalEdge]
    warnings: list[str] = field(default_factory=list)
    # Portal pieces an admin must add because the chosen blueprint does not contain
    # them. Not a warning: it is a normal, expected work item, and the whole reason
    # a hub-and-spoke topology has to be costed rather than drawn.
    shortfalls: list[str] = field(default_factory=list)

    def hubs(self) -> list[PortalNode]:
        return [n for n in self.nodes.values() if n.kind == "hub"]

    def degree(self, node_id: str) -> int:
        return sum(1 for e in self.edges if node_id in (e.a, e.b))

    def max_hops(self) -> int:
        """Worst-case hop count between any two nodes, by BFS over the edge set."""
        adj: dict[str, list[str]] = {k: [] for k in self.nodes}
        for e in self.edges:
            adj[e.a].append(e.b)
            adj[e.b].append(e.a)
        worst = 0
        for src in self.nodes:
            seen = {src: 0}
            queue = [src]
            while queue:
                cur = queue.pop(0)
                for nxt in adj[cur]:
                    if nxt not in seen:
                        seen[nxt] = seen[cur] + 1
                        queue.append(nxt)
            if len(seen) < len(self.nodes):
                return -1  # disconnected: reported as a warning, not silently averaged away
            worst = max(worst, max(seen.values()))
        return worst

    def piece_total(self) -> int:
        return sum(n.needed for n in self.nodes.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "preset": self.preset,
            "topology": self.topology,
            "assignment_mode": self.assignment_mode,
            "hub_mesh": self.hub_mesh,
            "node_count": len(self.nodes),
            "hub_count": len(self.hubs()),
            "edge_count": len(self.edges),
            "max_hops": self.max_hops(),
            "portal_pieces_total": self.piece_total(),
            "portal_pieces_to_place": sum(n.shortfall for n in self.nodes.values()),
            "zdo_cost": self.piece_total(),
            "nodes": [n.as_dict() for n in sorted(self.nodes.values(), key=lambda n: (n.kind != "hub", n.id))],
            "edges": [e.as_dict() for e in self.edges],
            "warnings": self.warnings,
            "shortfalls": self.shortfalls,
        }


def _short(node_id: str) -> str:
    """A portal tag has to be typed in game, so keep it short and unambiguous."""
    parts = [p for p in node_id.split("-") if p]
    if len(parts) == 1:
        return parts[0][:10]
    return (parts[0][:4] + parts[-1][:6]).lower()


def build(
    cat: Catalogue,
    tier: int,
    policy: dict[str, Any] | None = None,
    coords: dict[str, tuple[float, float]] | None = None,
) -> PortalGraph:
    """Build the portal graph for one tier's accumulated installation set.

    `coords` is optional. When it is supplied (x, z per installation id), spokes are
    assigned to their nearest hub, which is what you want once placements are solved.
    Without it, a spoke is assigned to the latest hub that already existed when the
    spoke was built -- geography-free, deterministic, and honest about being so. The
    mode used is recorded in `assignment_mode`.
    """
    policy = policy or load_policy()
    pol = policy["portals"]
    cap = _measured_portal_capacity()
    warnings: list[str] = []

    members = [i for i in cat.set_for_tier(tier) if i.portal]
    hubs = [i for i in members if i.role == "portal_hub"]
    spokes = [i for i in members if i.role != "portal_hub"]

    if not members:
        # portal_wood first appears in pre-elder's station ladder, so tier 1 has no
        # portal graph at all. That is a correct empty answer, not a failure.
        return PortalGraph(
            tier=tier,
            preset=cat.tiers[tier],
            topology=pol.get("topology", "tiered_hub"),
            assignment_mode="none",
            hub_mesh="none",
            nodes={},
            edges=[],
            warnings=[f"tier {tier} has no portal-bearing installation: portal_wood is not unlocked yet"],
        )

    if not hubs:
        # No hub blueprint exists yet at this tier. The home base becomes the hub:
        # a star centred on the base is the only shape that works with one building.
        home = cat.home_base_for_tier(tier)
        hubs = [home]
        spokes = [i for i in spokes if i.id != home.id]
        warnings.append(
            f"tier {tier} has no portal_hub installation; {home.id} acts as the hub, "
            f"so it needs one portal per spoke rather than the {home.portal_pieces} its entry declares"
        )

    hubs = sorted(hubs, key=lambda i: (i.first_tier, i.id))

    nodes: dict[str, PortalNode] = {}

    def add(inst: Installation, kind: str) -> PortalNode:
        bp = inst.blueprint.name if inst.blueprint else None
        n = PortalNode(
            id=inst.id,
            role=inst.role,
            kind=kind,
            first_tier=inst.first_tier,
            blueprint=bp,
            contained=cap.get(bp, 0) if bp else 0,
        )
        nodes[inst.id] = n
        return n

    for h in hubs:
        add(h, "hub")
    for s in spokes:
        add(s, "spoke")

    # ---- trunk FIRST, because trunk portals eat hub capacity and a spoke cannot be
    # assigned to a hub whose remaining slots are unknown.
    #
    # The trunk shape is ADAPTIVE, because neither shape wins at every size:
    #
    #   CLIQUE (H <= clique_max_hubs): every hub pairs with every other. Worst case
    #     3 hops for any pair in the world (spoke -> hub -> hub -> spoke). Costs H-1
    #     trunk portals per hub, so at H=4 that is 3 of an 8-portal hub and 5 spoke
    #     slots remain -- 4 x 5 = 20 spokes, which covers tier 6.
    #
    #   HUB-STAR (H > clique_max_hubs): the earliest hub is primary; every other hub
    #     pairs only with it. Worst case 4 hops. Costs H-1 trunk portals at the
    #     primary and exactly 1 at each secondary, so secondaries keep 7 spoke slots
    #     and the shape keeps working as hubs are added. A clique at H=6 would be 15
    #     trunk edges and 5 trunk portals per hub, leaving 3 spoke slots each --
    #     18 slots for tier 9's 20 spokes, i.e. it stops fitting.
    #
    # A CHAIN was rejected outright: H+1 worst-case hops, which at tier 9 is 5, and
    # every journey between the ends drags through every hub in between.
    edges: list[PortalEdge] = []
    prefix = pol.get("tag_prefix", "uls")
    trunk_degree: dict[str, int] = {h.id: 0 for h in hubs}
    clique_max = int(pol.get("clique_max_hubs", 4))
    hub_mesh = "clique" if len(hubs) <= clique_max else "hub-star"

    def link(h1: Installation, h2: Installation) -> None:
        edges.append(
            PortalEdge(
                a=h1.id,
                b=h2.id,
                tag=f"{prefix}-trunk-{_short(h1.id)}-{_short(h2.id)}",
                kind="trunk",
                first_tier=max(h1.first_tier, h2.first_tier),
            )
        )
        trunk_degree[h1.id] += 1
        trunk_degree[h2.id] += 1

    if hub_mesh == "clique":
        for idx, h1 in enumerate(hubs):
            for h2 in hubs[idx + 1 :]:
                link(h1, h2)
    else:
        primary, *secondaries = hubs
        for h in secondaries:
            link(primary, h)

    # ---- spoke -> hub assignment
    max_spokes = int(pol.get("max_spokes_per_hub", 8))
    mode = "nearest-hub-by-distance" if coords else "hub-era-by-tier"
    load: dict[str, int] = {h.id: 0 for h in hubs}
    capacity = {h.id: max_spokes - trunk_degree[h.id] for h in hubs}

    def rank_hubs(s: Installation) -> list[Installation]:
        if coords and s.id in coords:
            known = [h for h in hubs if h.id in coords]
            if known:
                sx, sz = coords[s.id]
                return sorted(known, key=lambda h: math.hypot(coords[h.id][0] - sx, coords[h.id][1] - sz))
        # Era: the latest hub that existed when this spoke was built, then the rest.
        earlier = [h for h in hubs if h.first_tier <= s.first_tier]
        later = [h for h in hubs if h.first_tier > s.first_tier]
        return list(reversed(earlier)) + later

    for s in spokes:
        chosen = None
        for h in rank_hubs(s):
            if load[h.id] < capacity[h.id]:
                chosen = h
                break
        if chosen is None:
            # Every hub is full. Say so; do not silently overload a tag.
            chosen = min(hubs, key=lambda h: load[h.id] - capacity[h.id])
            warnings.append(
                f"{s.id}: every hub is full (hub capacity is {max_spokes} portals, "
                f"minus {trunk_degree[chosen.id]} trunk portals); attached to {chosen.id} "
                f"anyway -- add a hub or drop a spoke"
            )
        load[chosen.id] += 1
        edges.append(
            PortalEdge(
                a=chosen.id,
                b=s.id,
                tag=f"{prefix}-{_short(s.id)}",
                kind="spoke",
                first_tier=max(chosen.first_tier, s.first_tier),
            )
        )

    # ---- piece accounting: one portal piece per edge END.
    for e in edges:
        nodes[e.a].needed += 1
        nodes[e.b].needed += 1

    graph = PortalGraph(
        tier=tier,
        preset=cat.tiers[tier],
        topology=pol.get("topology", "tiered_hub"),
        assignment_mode=mode,
        hub_mesh=hub_mesh,
        nodes=nodes,
        edges=edges,
        warnings=warnings,
    )

    # ---- capacity and sanity warnings
    tags = [e.tag for e in edges]
    if len(set(tags)) != len(tags):
        dupes = sorted({t for t in tags if tags.count(t) > 1})
        graph.warnings.append(f"duplicate portal tags {dupes}: a third portal on a tag makes the pair non-deterministic")
    for n in sorted(nodes.values(), key=lambda n: (-n.shortfall, n.id)):
        if n.shortfall:
            graph.shortfalls.append(
                f"{n.id}: needs {n.needed} portal piece(s), blueprint "
                f"{n.blueprint or '(prefab-composed)'} contains {n.contained} "
                f"(MEASURED) -- place {n.shortfall} more {pol['portal_prefab']} by hand"
            )
    if graph.max_hops() < 0:
        graph.warnings.append("graph is disconnected: some installation cannot be reached by portal")
    return graph


def load_policy(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or (DATA / "road_policy.yaml")).read_text())


def declared_vs_graph(cat: Catalogue, graph: PortalGraph) -> list[str]:
    """Compare data/installations.yaml's `portal_pieces` against what the graph needs.

    `portal_pieces` in the YAML is the count at the installation's OWN first_tier,
    which is the only tier-independent thing it can be: a hub needs more portals at
    tier 9 than at tier 4, because the set it serves has grown. So only installations
    introduced at this graph's tier are compared; for the rest the graph is the answer
    and the YAML is not wrong, merely earlier.
    """
    out = []
    by_id = {i.id: i for i in cat.installations}
    for n in graph.nodes.values():
        inst = by_id[n.id]
        if inst.first_tier != graph.tier:
            continue
        if inst.portal_pieces != n.needed:
            out.append(
                f"{n.id}: installations.yaml declares {inst.portal_pieces} portal piece(s) "
                f"at its own tier {inst.first_tier}, graph needs {n.needed}"
            )
    return out
