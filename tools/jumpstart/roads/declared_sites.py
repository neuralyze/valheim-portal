#!/usr/bin/env python3
"""THE SITES A PERSON DECLARED, AS ROAD-NETWORK NODES AND THE LEGS THEY IMPLY.

`worldseed.HUMAN_SITES` holds the owner's requests (Pirate68: Ulfsgard and
Sudrvik, "near (1050, 720)" and "near (1190, 444)").  A request is not a node:
both stand under 3-4 m of sea.  `settlements/plan.py` (stage 8) sites each
through the module that owns its kind (`settlements/harbour_site.py`: the real
inlet, the real landing) and publishes the result under `declared_sites` in
`sites.yaml`.  This module reads that back (`load`) and derives the legs the
declaration implies (`legs`); `roads/spec.py` merges both into NODES and EDGES.

It is a module of its own, and pure in its inputs, so the mechanism can be
tested over CONSTRUCTED documents and node tables instead of the host's
`sites.yaml` -- and so importing it does not load the road network's node table,
which needs the very file this reads.

THE DECLARATION IS THE AUTHORITY.  `load` REFUSES a published file that does not
carry exactly the declared set at exactly the declared request: a node list that
quietly lacks a declared site is the road network forgetting a city, and a stale
node is a road to where nobody asked for one.  A seed that declares nothing gets
`{}` and no legs -- unchanged to the byte.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.append(str(HERE.parent))

import worldseed  # noqa: E402

# Nodes that are not places a road can JOIN a network: a cape end, a bridge
# abutment, a quay, a shoreline, a bare junction, an arrival point.
NOT_A_JUNCTION = frozenset({"extremity", "abutment", "waterfront_terminal",
                            "shoreline", "junction", "settlement_arrival"})

# THE NEUTER (selftest): "dangling" restores the defect -- the declared node is
# in the table and no leg reaches it.
_LEGS_MODE = "derived"       # derived | dangling


def load(path: Path | None, seed_: str | None, island_of) -> dict[str, dict]:
    """`{node id: record}` for every site `worldseed.human_sites(seed_)`
    declares, as stage 8 measured it; `{}` for a seed that declares none.

    `island_of(routing_window)` is the network's window-to-landmass table.
    `patch` records the routing field; `measurement_patch` retains the survey."""
    want = worldseed.human_sites(seed_)
    if not want:
        return {}
    path = Path(path) if path is not None else worldseed.sites_path(seed_)
    if not path.exists():
        raise SystemExit(
            f"{path} is missing, and seed {seed_ or worldseed.seed()!r} "
            f"declares {sorted(want)}: their nodes are measured by "
            f"settlements/plan.py (stage 8), which publishes them in this file.")
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope(
        "solved site set", path, found_seed=doc.get("seed"),
        found_world=doc.get("world"), seed_=seed_,
        produced_by="settlements/plan.py, which writes this file for the seed "
                    "it solved; run the settlements planner for this seed")
    have = {d["id"]: d for d in doc.get("declared_sites") or []}
    if set(have) != set(want):
        raise SystemExit(
            f"REFUSING: {path} carries declared site(s) {sorted(have)} and "
            f"worldseed.HUMAN_SITES declares {sorted(want)} for this seed. "
            f"The declaration is the authority and this file predates it (or "
            f"was solved against another one): re-run settlements/plan.py "
            f"(pipeline stage 8) so the missing node is MEASURED, not typed.")
    out: dict[str, dict] = {}
    for sid, rec in want.items():
        d = have[sid]
        if ([float(v) for v in d["requested_xz"]]
                != [float(v) for v in rec["xz"]]
                or d["type"] != rec["kind"] or d["node"] != rec["node"]):
            raise SystemExit(
                f"REFUSING: {path} sited {sid} for request "
                f"{d['requested_xz']} as {d['type']!r}/{d['node']!r}, and "
                f"worldseed.HUMAN_SITES now says {list(rec['xz'])} as "
                f"{rec['kind']!r}/{rec['node']!r}. A node measured for a "
                f"request nobody is making is stale: re-run stage 8.")
        routing_window = d.get("routing_window")
        if not routing_window:
            raise SystemExit(
                f"REFUSING: {path} declared site {sid!r} lacks required "
                f"routing_window. Re-run settlements/plan.py (pipeline "
                f"stage 8) for this seed to regenerate its covering rooted "
                f"routing field; measurement patch {d['patch']!r} is not "
                f"evidence of a separate landmass.")
        out[d["node"]] = {
            "site_id": sid, "type": d["type"],
            "xz": (float(d["xz"][0]), float(d["xz"][1])),
            "requested_xz": (float(d["requested_xz"][0]),
                             float(d["requested_xz"][1])),
            "island": island_of(routing_window), "patch": routing_window,
            "measurement_patch": d["patch"],
            "pad_radius_m": float(d["pad_radius_m"]),
            "district_m": float(d["district_m"]),
            "road_priority": d["road_priority"],
            "road_to": list(d.get("road_to") or ()),
            "pad_y": d.get("pad_y"), "why": d["why"],
            "measured": d.get("measured") or {},
        }
    return out


def trunk_junctions(nodes: dict[str, dict], edges: list[dict],
                    exclude=()) -> list[str]:
    """The nodes a new leg may JOIN: an end of a `trunk` edge, present in
    `nodes`, not in `exclude`, and a place a road can join (`NOT_A_JUNCTION`)."""
    trunk_ends = {n for e in edges if e["cls"] == "trunk"
                  for n in (e["a"], e["b"])}
    return sorted(n for n in trunk_ends if n in nodes and n not in exclude
                  and nodes[n].get("kind") not in NOT_A_JUNCTION)


def nearest_join(members, targets, nodes: dict[str, dict]):
    """`(metres, member, target)` of the closest member-to-target pair ON ONE
    LANDMASS, by straight line, or None.  Nearest by straight line is a choice
    of ENDPOINT only; the router still finds the route, bridges included.
    THE ONE JOIN RULE: a declared site and a solved site are joined the same
    way."""
    best = None
    for m in sorted(members):
        mx, mz = nodes[m]["xz"]
        for c in sorted(targets):
            if nodes[c]["island"] != nodes[m]["island"]:
                continue
            d_m = math.hypot(nodes[c]["xz"][0] - mx, nodes[c]["xz"][1] - mz)
            if best is None or d_m < best[0]:
                best = (d_m, m, c)
    return best


def legs(declared: dict[str, dict], nodes: dict[str, dict],
         edges: list[dict]) -> list[dict]:
    """THE LEGS A DECLARATION IMPLIES -- DERIVED, NEVER TYPED.

    Two kinds, both from the declaration and the nodes alone:

      * the roads the owner ASKED for (`road_to`: Sudrvik "a road connecting
        through the meadows" to Ulfsgard), one leg per named pair;
      * the JOIN TO THE NETWORK: every group of declared sites that no leg
        connects to a non-declared node gets ONE leg, from its member nearest
        to the nearest TRUNK node of that member's own landmass (an end of a
        `trunk` edge in `edges`, not itself declared, and a place a road can
        join -- `NOT_A_JUNCTION`).  Nearest by straight line is a choice of
        ENDPOINT only; the router still finds the route, bridges included.
        A group with no such node on its landmass is REFUSED by name: a city
        no road can reach is a portal-only city, and that is the owner's call.

    The join leg is numbered first (R1 reaches the network, R2 the village
    behind it), skipping any `R<n>` id already taken.
    """
    if not declared:
        return []
    by_site = {d["site_id"]: nid for nid, d in declared.items()}
    used = {e["id"] for e in edges}

    def edge_id(a: str, b: str) -> str:
        n = 1
        while any(i.startswith(f"R{n}-") for i in used):
            n += 1
        i = f"R{n}-{a}-{b}"
        used.add(i)
        return i

    graph: dict[str, set[str]] = {nid: set() for nid in declared}
    asked: list[tuple[str, str]] = []
    for nid, d in declared.items():
        for tgt in d["road_to"]:
            t = by_site[tgt]
            if t not in graph[nid]:
                graph[nid].add(t)
                graph[t].add(nid)
                asked.append((t, nid))
    network = trunk_junctions(nodes, edges, exclude=declared)
    joins: list[dict] = []
    seen: set[str] = set()
    for root in declared:
        if root in seen:
            continue
        comp, todo = set(), [root]
        while todo:
            m = todo.pop()
            if m not in comp:
                comp.add(m)
                todo.extend(graph[m])
        seen |= comp
        best = nearest_join(comp, network, nodes)
        if best is None:
            raise SystemExit(
                f"REFUSING: declared site(s) {sorted(comp)} have no trunk "
                f"node on their own landmass to be joined to "
                f"({len(network)} candidate node(s) on other landmasses). A "
                f"declared site no road can reach is portal-only; say so in "
                f"worldseed.HUMAN_SITES rather than leaving a dangling node.")
        d_m, m, c = best
        if _LEGS_MODE == "dangling":
            continue
        joins.append({
            "id": edge_id(m, c), "a": m, "b": c,
            "cls": declared[m]["road_priority"], "pri": 2,
            "why": f"{declared[m]['site_id']} is a declared site and no road "
                   f"of the declaration reaches the network, so by rule it "
                   f"joins the nearest trunk node of its landmass: {c}, "
                   f"{d_m:.1f} m by straight line."})
    out = []
    for a, b in asked:
        out.append({
            "id": edge_id(a, b), "a": a, "b": b,
            "cls": declared[b]["road_priority"], "pri": 2,
            "why": f"the owner asked for a road between "
                   f"{declared[a]['site_id']} and {declared[b]['site_id']} "
                   f"(worldseed.HUMAN_SITES road_to)."})
    return joins + out


# THE NEUTER (selftest): "none" restores the shipped network -- a solved site
# is a node with whatever legs were typed for it, and no leg is derived.
_SITE_LEGS_MODE = "derived"      # derived | none (the shipped)
ORPHAN_ROOT = "portalhub"        # the node a walk starts from (network.py)


def site_legs(sites: dict[str, dict], nodes: dict[str, dict],
              edges: list[dict], crossings: list[dict] | None = None,
              root: str = ORPHAN_ROOT) -> tuple[list[dict], dict[str, str]]:
    """THE LEGS A ROADED SOLVED SITE STILL NEEDS -- DERIVED, THE SAME RULE AS
    A DECLARED SITE'S JOIN.

    `sites` are the solver's nodes whose published `road` verdict is `roaded`
    (`settlements/road_reach.py`: a road CAN reach them, in the router's own
    graph).  Whatever legs `spec.py` carries for them are authored topology --
    "this site hangs from that node" -- and it is not trusted to still reach
    anything: a leg is dropped when its other end is a portal-only site, and
    a leg to a node on another landmass is unroutable by construction.  So
    the roads the declaration promises are checked against what is LEFT:

      * walk from `root` over the legs whose ends stand on ONE landmass, and
        over each declared crossing's two banks;
      * every group of roaded sites that walk never reaches gets ONE leg, from
        the member nearest the nearest REACHED trunk node of its own landmass
        (`nearest_join` -- the declared sites' rule), in the site's own
        declared class (`priority`), numbered `J1`, `J2`, ...

    Returns `(legs, unjoined)`: a group with no such node on its landmass
    cannot be joined, and is returned by name with the numbers rather than
    raised -- a solved site no road can be joined to is portal-only, and every
    tool that imports the node table must still import.  A seed whose
    publication carries no verdicts passes no `sites` and gets nothing.
    """
    if not sites or _SITE_LEGS_MODE == "none":
        return [], {}
    adj: dict[str, set[str]] = {n: set() for n in nodes}

    def link(a: str, b: str) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for e in edges:
        a, b = e["a"], e["b"]
        if a in nodes and b in nodes and nodes[a]["island"] == nodes[b]["island"]:
            link(a, b)
    for c in crossings or ():
        banks = [c["bank_a"]["node"], c["bank_b"]["node"]]
        if all(n in nodes for n in banks):
            link(*banks)

    def walk(start: str, within=None) -> set[str]:
        seen, todo = set(), [start]
        while todo:
            n = todo.pop()
            if n in seen or (within is not None and n not in within):
                continue
            seen.add(n)
            todo.extend(adj[n])
        return seen

    rooted = walk(root) if root in nodes else set()
    cut_off = {n for n in nodes if n not in rooted}
    junctions = [n for n in trunk_junctions(nodes, edges) if n in rooted]
    used = {e["id"] for e in edges}
    out: list[dict] = []
    unjoined: dict[str, str] = {}
    seen: set[str] = set()
    for first in sorted(n for n in sites if n in cut_off):
        if first in seen:
            continue
        comp = walk(first, within=cut_off)
        seen |= comp
        members = sorted(n for n in comp if n in sites)
        best = nearest_join(members, junctions, nodes)
        if best is None:
            why = (f"no trunk node a walk from {root} reaches stands on its "
                   f"landmass ({len(junctions)} candidate node(s), none on "
                   f"island(s) {sorted({nodes[m]['island'] for m in members})})")
            for m in members:
                unjoined[m] = why
            continue
        d_m, m, c = best
        n = 1
        while any(i.startswith(f"J{n}-") for i in used):
            n += 1
        leg_id = f"J{n}-{m}-{c}"
        used.add(leg_id)
        out.append({
            "id": leg_id, "a": m, "b": c, "cls": sites[m]["priority"],
            "pri": 2,
            "why": f"{m} is a roaded site (a road reaches it: "
                   f"settlements/road_reach.py) and no leg of the network "
                   f"connects it to {root}, so by rule it joins the nearest "
                   f"trunk node its landmass reaches: {c}, {d_m:.1f} m by "
                   f"straight line."})
    return out, unjoined


# ---------------------------------------------------------------------------
# SELFTEST.  A seed that is not Pirate68 with its own declaration, constructed
# here, so none of it is a reading of the host's sites.yaml -- and a real seed
# that declares nothing, to prove it is untouched.
# ---------------------------------------------------------------------------
_SEED = "Testseed"


def _declare(table: dict | None = None):
    """Install a fixture declaration for `_SEED`; return the undo."""
    saved = worldseed.HUMAN_SITES.get(_SEED)
    worldseed.HUMAN_SITES[_SEED] = table if table is not None else {
        "harbour-city-a": dict(kind="harbour_city", xz=(500, 500), node="cityA",
                               road_to=(), why="the owner: a city at 500, 500"),
        "harbour-village-b": dict(kind="harbour_village", xz=(900, 100),
                                  node="villB", road_to=("harbour-city-a",),
                                  why="the owner: a village at 900, 100"),
    }

    def undo():
        if saved is None:
            worldseed.HUMAN_SITES.pop(_SEED, None)
        else:
            worldseed.HUMAN_SITES[_SEED] = saved
    return undo


def _published(sites=None, *, request_a=(500.0, 500.0), seed=_SEED) -> dict:
    sites = sites if sites is not None else ("harbour-city-a",
                                             "harbour-village-b")
    rec = {
        "harbour-city-a": dict(
            id="harbour-city-a", type="harbour_city", node="cityA",
            requested_xz=list(request_a), xz=[470.5, 520.5], pad_radius_m=12.0,
            district_m=160.0, road_priority="trunk", road_to=[],
            patch="trunk-primary", routing_window="trunk-primary",
            pad_y=33.0, why="w", measured={}),
        "harbour-village-b": dict(
            id="harbour-village-b", type="harbour_village", node="villB",
            requested_xz=[900.0, 100.0], xz=[880.5, 120.5], pad_radius_m=10.0,
            district_m=90.0, road_priority="trunk", road_to=["harbour-city-a"],
            patch="trunk-primary", routing_window="trunk-primary",
            pad_y=33.0, why="w", measured={}),
    }
    return {"world": None, "seed": seed, "seed_hash": 1, "sites": [],
            "declared_sites": [rec[s] for s in sites]}


def _load(doc: dict):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / worldseed.sites_name(_SEED)
        p.write_text(yaml.safe_dump(doc))
        return load(p, _SEED,
                    lambda patch: {"trunk-primary": "spawn"}.get(patch, patch))


def _refused(fn) -> str | None:
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    return None


_NETWORK = {
    "wtnear": {"xz": (300.0, 480.0), "island": "spawn", "kind": "watchtower"},
    "town": {"xz": (100.0, 100.0), "island": "spawn", "kind": "town"},
    "farisle": {"xz": (480.0, 520.0), "island": "other", "kind": "town"},
    "cape": {"xz": (470.0, 530.0), "island": "spawn", "kind": "extremity"},
    "quay": {"xz": (471.0, 521.0), "island": "spawn",
             "kind": "waterfront_terminal"},
}
_EDGES = [{"id": "T1-wtnear-town", "a": "wtnear", "b": "town", "cls": "trunk",
           "pri": 1},
          {"id": "R3-quay-cape", "a": "quay", "b": "cape", "cls": "trunk",
           "pri": 3},
          {"id": "T9-farisle-cape", "a": "farisle", "b": "cape", "cls": "trunk",
           "pri": 2}]


def _nodes_with(declared: dict) -> dict:
    return {**_NETWORK, **{nid: {"xz": d["xz"], "island": d["island"],
                                 "kind": d["type"]}
                           for nid, d in declared.items()}}


def _case_the_declared_set_is_loaded_at_the_measured_node() -> list[str]:
    undo = _declare()
    try:
        got = _load(_published())
    finally:
        undo()
    bad = []
    if sorted(got) != ["cityA", "villB"]:
        bad.append(f"nodes {sorted(got)}")
    elif got["cityA"]["xz"] != (470.5, 520.5) or \
            got["cityA"]["requested_xz"] != (500.0, 500.0):
        bad.append(f"the node is not the measured landing: {got['cityA']}")
    return bad


def _case_a_survey_measurement_joins_the_published_routing_landmass():
    undo = _declare()
    try:
        doc = _published()
        doc["declared_sites"][0]["patch"] = "north-survey"
        declared = _load(doc)
    finally:
        undo()
    city = declared["cityA"]
    bad = []
    if (city["measurement_patch"], city["patch"], city["island"]) != (
            "north-survey", "trunk-primary", "spawn"):
        bad.append(f"survey patch replaced the published routing role: {city}")
    got = legs(declared, _nodes_with(declared), list(_EDGES))
    pairs = [(e["a"], e["b"]) for e in got]
    if pairs != [("cityA", "wtnear"), ("cityA", "villB")]:
        bad.append(f"survey-measured city did not join spawn trunk: {pairs}")
    return bad


def _case_a_missing_routing_window_is_REFUSED():
    undo = _declare()
    try:
        doc = _published()
        doc["declared_sites"][0]["patch"] = "north-survey"
        del doc["declared_sites"][0]["routing_window"]
        msg = _refused(lambda: _load(doc))
    finally:
        undo()
    return [] if msg and all(s in msg for s in (
        "harbour-city-a", "routing_window", "stage 8")) else [
        f"a survey-measured site without routing_window was not refused "
        f"with the stage 8 remedy: {msg}"]


def _case_a_publication_missing_a_declared_site_is_REFUSED() -> list[str]:
    undo = _declare()
    try:
        msg = _refused(lambda: _load(_published(("harbour-city-a",))))
    finally:
        undo()
    return [] if msg and "harbour-village-b" in msg and "stage 8" in msg else [
        f"a file lacking a declared site was accepted or refused without "
        f"naming it: {msg}"]


def _case_a_publication_carrying_an_undeclared_site_is_REFUSED() -> list[str]:
    undo = _declare({"harbour-city-a": dict(
        kind="harbour_city", xz=(500, 500), node="cityA", road_to=(),
        why="the owner")})
    try:
        msg = _refused(lambda: _load(_published()))
    finally:
        undo()
    return [] if msg and "harbour-village-b" in msg else [
        f"a node nobody declares was adopted: {msg}"]


def _case_a_stale_request_is_REFUSED() -> list[str]:
    undo = _declare()
    try:
        msg = _refused(lambda: _load(_published(request_a=(510.0, 500.0))))
    finally:
        undo()
    return [] if msg and "stale" in msg else [
        f"a node measured for another request was adopted: {msg}"]


def _case_a_foreign_seeds_publication_is_REFUSED() -> list[str]:
    undo = _declare()
    try:
        msg = _refused(lambda: _load(_published(seed="Pirate68")))
    finally:
        undo()
    return [] if msg and "Pirate68" in msg else [
        f"another seed's declared nodes were adopted: {msg}"]


def _case_a_seed_that_declares_nothing_is_untouched() -> list[str]:
    bad = []
    if worldseed.human_sites("Fresh42"):
        bad.append(f"Fresh42 declares {sorted(worldseed.human_sites('Fresh42'))}")
    if load(Path("/nonexistent") / worldseed.sites_name("Fresh42"), "Fresh42", lambda p: "x") != {}:
        bad.append("a seed with no declaration read a file")
    if legs({}, dict(_NETWORK), list(_EDGES)) != []:
        bad.append("a seed with no declaration got legs")
    return bad


def _case_the_default_seed_declares_the_two_owner_sites_and_nothing_else():
    got = worldseed.human_sites("Pirate68")
    bad = []
    if list(got) != ["harbour-city-ulfsgard", "harbour-village-sudrvik"]:
        bad.append(f"declared {list(got)}")
        return bad
    if tuple(got["harbour-city-ulfsgard"]["xz"]) != (1050, 720):
        bad.append(f"Ulfsgard requested at {got['harbour-city-ulfsgard']['xz']}")
    if tuple(got["harbour-village-sudrvik"]["xz"]) != (1190, 444):
        bad.append(f"Sudrvik requested at {got['harbour-village-sudrvik']['xz']}")
    for sid, rec in got.items():
        if '"' not in rec["why"] or "the owner" not in rec["why"]:
            bad.append(f"{sid}: `why` is not the owner's own words")
    return bad


def _case_legs_join_the_network_and_follow_the_owners_road() -> list[str]:
    undo = _declare()
    try:
        declared = _load(_published())
    finally:
        undo()
    nodes = _nodes_with(declared)
    got = legs(declared, nodes, list(_EDGES))
    bad = []
    pairs = [(e["a"], e["b"]) for e in got]
    # nearest trunk node on cityA's landmass: wtnear (300,480), 170 m away.
    # `farisle` is closer and a trunk node (a different landmass), `cape` and
    # `quay` are not
    # places a road joins, `town` is farther.
    if pairs != [("cityA", "wtnear"), ("cityA", "villB")]:
        bad.append(f"legs {pairs}")
    ids = [e["id"] for e in got]
    if ids != ["R1-cityA-wtnear", "R2-cityA-villB"]:
        bad.append(f"ids {ids}")
    clash = {e["id"] for e in _EDGES} & set(ids)
    if clash:
        bad.append(f"a derived id collides with a typed one: {clash}")
    return bad


def _case_the_numbering_skips_the_ids_already_taken() -> list[str]:
    undo = _declare()
    try:
        declared = _load(_published())
    finally:
        undo()
    taken = _EDGES + [{"id": "R1-x-y", "a": "x", "b": "y", "cls": "spur",
                       "pri": 3},
                      {"id": "R2-x-z", "a": "x", "b": "z", "cls": "spur",
                       "pri": 3}]
    ids = [e["id"] for e in legs(declared, _nodes_with(declared), taken)]
    return [] if ids == ["R4-cityA-wtnear", "R5-cityA-villB"] else [
        f"ids {ids} (R1, R2 and R3 are taken)"]


def _case_a_landmass_with_no_trunk_node_is_REFUSED() -> list[str]:
    undo = _declare()
    try:
        declared = _load(_published())
    finally:
        undo()
    nodes = _nodes_with(declared)
    for n in ("wtnear", "town"):
        nodes[n] = dict(nodes[n], island="elsewhere")
    msg = _refused(lambda: legs(declared, nodes, list(_EDGES)))
    return [] if msg and "cityA" in msg and "portal-only" in msg else [
        f"a city no road can reach got a leg or an unnamed refusal: {msg}"]


def _case_NEUTER_dangling_the_city_reaches_no_road() -> list[str]:
    global _LEGS_MODE
    _LEGS_MODE = "dangling"
    try:
        problems = _case_legs_join_the_network_and_follow_the_owners_road()
    finally:
        _LEGS_MODE = "derived"
    return [] if problems else [
        "the neuter did not restore a dangling declared node"]


# ---------------------------------------------------------------------------
# THE LEGS A ROADED SOLVED SITE STILL NEEDS (`site_legs`).  CONSTRUCTED: a
# spawn island and a west isle joined by one crossing, four roaded sites, and
# the authored legs typed for them -- one connected, one whose partner is gone,
# one never typed, one hung from a node on the other island.
# ---------------------------------------------------------------------------
def _site_world(*, west_junction: bool = True, link_b_c: bool = False):
    nodes = {
        "portalhub": {"xz": (0.0, 0.0), "island": "spawn",
                      "kind": "installation"},
        "stathub": {"xz": (400.0, 0.0), "island": "spawn",
                    "kind": "installation"},
        "northcape": {"xz": (400.0, 900.0), "island": "spawn",
                      "kind": "extremity"},
        "brgE": {"xz": (-250.0, 0.0), "island": "spawn", "kind": "abutment"},
        "brgW": {"xz": (-300.0, 0.0), "island": "west", "kind": "abutment"},
        "wm": {"xz": (-800.0, 50.0), "island": "west", "kind": "extremity"},
        "wtA": {"xz": (900.0, 100.0), "island": "spawn", "kind": "watchtower"},
        "wtB": {"xz": (1500.0, 100.0), "island": "spawn",
                "kind": "watchtower"},
        "wtC": {"xz": (1600.0, 800.0), "island": "spawn",
                "kind": "watchtower"},
        "lhW": {"xz": (-900.0, 0.0), "island": "west", "kind": "lighthouse"},
    }
    edges = [
        {"id": "T1-portalhub-stathub", "a": "portalhub", "b": "stathub",
         "cls": "trunk", "pri": 1},
        {"id": "T2-stathub-wtA", "a": "stathub", "b": "wtA", "cls": "trunk",
         "pri": 1},
        {"id": "T3-portalhub-brgE", "a": "portalhub", "b": "brgE",
         "cls": "trunk", "pri": 1},
        {"id": "W1-brgW-wm", "a": "brgW", "b": "wm", "cls": "trunk", "pri": 3},
        # hung from a node on the OTHER island: unroutable by construction
        {"id": "W9-stathub-lhW", "a": "stathub", "b": "lhW", "cls": "spur",
         "pri": 3},
    ]
    if west_junction:
        nodes["vil"] = {"xz": (-700.0, 0.0), "island": "west",
                        "kind": "village"}
        edges.append({"id": "W2-brgW-vil", "a": "brgW", "b": "vil",
                      "cls": "trunk", "pri": 3})
    if link_b_c:
        edges.append({"id": "S7-wtB-wtC", "a": "wtB", "b": "wtC",
                      "cls": "spur", "pri": 2})
    crossings = [{"bank_a": {"node": "brgE"}, "bank_b": {"node": "brgW"}}]
    sites = {n: {"priority": "spur"} for n in ("wtA", "wtB", "wtC", "lhW")}
    sites["wtA"]["priority"] = "trunk"
    return sites, nodes, edges, crossings


def _case_a_roaded_site_no_leg_connects_is_joined_to_its_own_landmass():
    sites, nodes, edges, crossings = _site_world()
    got, unjoined = site_legs(sites, nodes, edges, crossings)
    pairs = [(e["id"], e["a"], e["b"], e["cls"]) for e in got]
    want = [("J1-lhW-vil", "lhW", "vil", "spur"),
            ("J2-wtB-wtA", "wtB", "wtA", "spur"),
            ("J3-wtC-wtA", "wtC", "wtA", "spur")]
    bad = []
    if pairs != want:
        bad.append(f"legs {pairs}, wanted {want} (wtA is connected and gets "
                   f"none; the west lighthouse, hung from a spawn-island node, "
                   f"joins the WEST isle; the rest join the nearest REACHED "
                   f"trunk node of their own landmass)")
    if unjoined:
        bad.append(f"unjoined {unjoined}")
    return bad


def _case_sites_connected_to_each_other_get_one_leg() -> list[str]:
    sites, nodes, edges, crossings = _site_world(link_b_c=True)
    got, _u = site_legs(sites, nodes, edges, crossings)
    pairs = sorted((e["a"], e["b"]) for e in got
                   if e["a"] in ("wtB", "wtC"))
    return [] if pairs == [("wtB", "wtA")] else [
        f"two sites joined by an authored leg got {pairs}: ONE leg, from the "
        f"member nearest the network (wtB, 600 m from wtA)"]


def _case_a_landmass_with_no_trunk_node_is_returned_not_raised() -> list[str]:
    sites, nodes, edges, crossings = _site_world(west_junction=False)
    try:
        got, unjoined = site_legs(sites, nodes, edges, crossings)
    except SystemExit as exc:
        return [f"a roaded site with no node to join raised and would stop "
                f"every importer of the node table: {exc}"]
    bad = []
    if "lhW" not in unjoined or "portalhub" not in unjoined["lhW"]:
        bad.append(f"the west lighthouse was not returned by name with its "
                   f"reason: {unjoined}")
    if any(e["a"] == "lhW" for e in got):
        bad.append("a leg was derived to a node on another landmass")
    return bad


def _case_an_empty_solved_site_set_derives_nothing() -> list[str]:
    _s, nodes, edges, crossings = _site_world()
    got, unjoined = site_legs({}, nodes, edges, crossings)
    return [] if (got, unjoined) == ([], {}) else [
        f"an empty solved site set got {got} {unjoined}"]


def _case_the_numbering_skips_the_site_ids_already_taken() -> list[str]:
    sites, nodes, edges, crossings = _site_world()
    edges = edges + [{"id": "J1-x-y", "a": "wtA", "b": "stathub",
                      "cls": "spur", "pri": 2}]
    ids = [e["id"] for e in site_legs(sites, nodes, edges, crossings)[0]]
    return [] if ids == ["J2-lhW-vil", "J3-wtB-wtA", "J4-wtC-wtA"] else [
        f"ids {ids} (J1 is taken)"]


def _case_NEUTER_derived_site_legs_off_leaves_the_sites_dangling():
    global _SITE_LEGS_MODE
    _SITE_LEGS_MODE = "none"
    try:
        problems = _case_a_roaded_site_no_leg_connects_is_joined_to_its_own_landmass()
    finally:
        _SITE_LEGS_MODE = "derived"
    return [] if problems else [
        "the neuter did not restore sites left with no road"]


CASES = (
    ("the_declared_set_is_loaded_at_the_measured_node",
     _case_the_declared_set_is_loaded_at_the_measured_node),
    ("a_survey_measurement_joins_the_published_routing_landmass",
     _case_a_survey_measurement_joins_the_published_routing_landmass),
    ("a_missing_routing_window_is_REFUSED",
     _case_a_missing_routing_window_is_REFUSED),
    ("a_publication_missing_a_declared_site_is_REFUSED",
     _case_a_publication_missing_a_declared_site_is_REFUSED),
    ("a_publication_carrying_an_undeclared_site_is_REFUSED",
     _case_a_publication_carrying_an_undeclared_site_is_REFUSED),
    ("a_stale_request_is_REFUSED", _case_a_stale_request_is_REFUSED),
    ("a_foreign_seeds_publication_is_REFUSED",
     _case_a_foreign_seeds_publication_is_REFUSED),
    ("a_seed_that_declares_nothing_is_untouched",
     _case_a_seed_that_declares_nothing_is_untouched),
    ("the_default_seed_declares_the_two_owner_sites_and_nothing_else",
     _case_the_default_seed_declares_the_two_owner_sites_and_nothing_else),
    ("legs_join_the_network_and_follow_the_owners_road",
     _case_legs_join_the_network_and_follow_the_owners_road),
    ("the_numbering_skips_the_ids_already_taken",
     _case_the_numbering_skips_the_ids_already_taken),
    ("a_landmass_with_no_trunk_node_is_REFUSED",
     _case_a_landmass_with_no_trunk_node_is_REFUSED),
    ("NEUTER_dangling_the_city_reaches_no_road",
     _case_NEUTER_dangling_the_city_reaches_no_road),
    ("a_roaded_site_no_leg_connects_is_joined_to_its_own_landmass",
     _case_a_roaded_site_no_leg_connects_is_joined_to_its_own_landmass),
    ("sites_connected_to_each_other_get_one_leg",
     _case_sites_connected_to_each_other_get_one_leg),
    ("a_landmass_with_no_trunk_node_is_returned_not_raised",
     _case_a_landmass_with_no_trunk_node_is_returned_not_raised),
    ("an_empty_solved_site_set_derives_nothing",
     _case_an_empty_solved_site_set_derives_nothing),
    ("the_numbering_skips_the_site_ids_already_taken",
     _case_the_numbering_skips_the_site_ids_already_taken),
    ("NEUTER_derived_site_legs_off_leaves_the_sites_dangling",
     _case_NEUTER_derived_site_legs_off_leaves_the_sites_dangling),
)


def selftest() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            problems = fn()
        except BaseException as e:                          # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  declared sites selftest {len(CASES) - bad}/{len(CASES)}  "
          f"{'PASS' if not bad else 'FAIL'}\n")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else
                     print(__doc__) or 0)
