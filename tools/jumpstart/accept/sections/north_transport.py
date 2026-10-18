#!/usr/bin/env python3
"""North acceptance: required itinerary, saved docks, paired boats and departure.

The itinerary supplies the REQUIRED population, never the verdict. Positions,
rotations, walkable faces, obstructions, hull depth and tower grounding all come
from the gate's one frozen save and its existing instruments. A ledger saying
that a dock/ship/tower was spawned is not evidence that it still exists.

Run with --section roads,north_transport, --north-itinerary PATH and
--north-field PATH (or VH_NORTH_ITINERARY / VH_NORTH_FIELD). If no explicit
field is supplied, itinerary.height_source.field names the measured input.
Its real seed header and 1 m lattice are still required; no fixture is selected.
Both saved boats must clear every hull footprint along pair.sea_route, including
turns and overhead solids. Voyage edges never enter the physical walking graph.
"""
from __future__ import annotations

import math
import os
from types import SimpleNamespace

from .. import instruments as I
from ..registry import Case, Finding, Section, SectionResult, register
from . import _approach as A

POSITION_TOL_M = 0.35
ROTATION_TOL_DEG = 2.0
BOARDING_GAP_M = 2.0
MIN_LIGHTHOUSE_HEIGHT_M = 15.0

TERMINATION_FAMILIES = {
    "north_artifact": ("PRODUCER_NOT_RUN", "produce the complete, non-provisional north itinerary for this seed and world"),
    "north_routes": ("UPSTREAM_STAGE_NOT_RUN", "build every itinerary road through the ordinary road network writer and run roads acceptance"),
    "instrument": ("CENSUS_NOT_TAKEN", "provide the seed-checked 1 m north field, frozen-save ZDO census, measured prefab geometry and applied ground readings"),
    "corpus_is_about_this_world": ("PRODUCER_NOT_RUN", "re-run the cited corpus producer for this seed and world; a foreign corpus cannot authorize north construction"),
    "dock_pieces": ("UPSTREAM_STAGE_NOT_RUN", "build the terminal's real dock pieces through roads/north_transport.py build after its serving road"),
    "dock_support": ("SOURCE_CHANGE", "re-plan dock foundations against frozen-save terrain through the ownership-gated transport builder; unchanged piece poses cannot certify missing ground"),
    "dock_access": ("SOURCE_CHANGE", "repair the north dock placement or its serving road through their existing ownership-gated builders; missing walking surfaces cannot be certified by a ledger"),
    "boat": ("UPSTREAM_STAGE_NOT_RUN", "build a real measured vessel at this terminal with roads/north_transport.py build; both endpoints need their own boat"),
    "boat_clearance": ("SOURCE_CHANGE", "re-plan this terminal against measured water and actual hull obstructions; never bypass terrain ownership or replace the crossing with a portal"),
    "paired_endpoints": ("UPSTREAM_STAGE_NOT_RUN", "build and physically accept BOTH docks and BOTH boats of every declared transport edge"),
    "lighthouse": ("UPSTREAM_STAGE_NOT_RUN", "build the final terminal's full lighthouse body, connected, upright and grounded, with roads/north_transport.py build"),
    "departure_ship": ("UPSTREAM_STAGE_NOT_RUN", "build and physically accept a measured large OdinShip vessel at the final departure terminal; no vanilla substitute"),
    "voyage": ("SOURCE_CHANGE", "re-plan the paired sea passage against the frozen save's full-hull terrain and solid obstacles; a boat at each shore is not a navigable crossing"),
    "north_extent": ("SOURCE_CHANGE", "regenerate the north itinerary from this seed's actual coarse terrain and selected preset; a short route with complete dock props is not the required northern frontier"),
    # The termination enumerator follows the shared grounding predicate even
    # though its findings are summarized into our lighthouse evidence.
    "grounding": ("SOURCE_CHANGE", "repair lighthouse support against applied terrain through the ownership-gated transport builder"),
    "buried": ("SOURCE_CHANGE", "re-plan the real lighthouse body above the applied ground without bypassing terrain ownership"),
}


def _finding(ok, message, where=None, *, check, **numbers):
    return Finding(check=check, ok=bool(ok), message=message, where=where,
                   numbers=numbers)


def extent_verdict(road_northing, coarse_northing, cell_m, *, relocation_m):
    """A terminal may use only the solver's finite shore-siting envelope."""
    values = (road_northing, coarse_northing, cell_m, relocation_m)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return False, {"unread_extent": True}
    minimum = coarse_northing - cell_m - relocation_m
    return road_northing >= minimum, {
        "road_northing_m": road_northing, "coarse_northing_m": coarse_northing,
        "coarse_cell_m": cell_m, "terminal_siting_envelope_m": relocation_m,
        "minimum_northing_m": minimum, "short_by_m": max(0.0, minimum-road_northing)}


def measure_extent(ctx, doc):
    """Re-derive the northern requirement, never trust the itinerary's number."""
    import json
    from pathlib import Path
    from roads import field as F, north_itinerary as NI, poi
    seed, world = I.run_scope(ctx)
    ws = I._worldseed()
    expected = NI.phase_policy(os.environ.get("VH_NORTH_PRESET", "pre-kall"))
    if doc.get("phase") != expected:
        raise ValueError("north phase differs from the selected shipped preset")
    grid_path, locations_path = doc.get("coarse_grid"), doc.get("location_dump")
    if not grid_path or not locations_path:
        raise ValueError("north extent has no readable coarse-grid/location citation")
    grid = F._site_finder().load_grid(Path(grid_path))
    ws.require_scope("north extent grid", grid_path, found_seed=grid.get("seed"),
                     seed_=seed, world_=world)
    locations = json.loads(Path(locations_path).read_text())
    ws.require_scope("north extent locations", locations_path,
                     found_seed=poi.dump_seed(locations), seed_=seed, world_=world)
    spawn = next((p for p in locations.get("locations", [])
                  if p.get("name") == "StartTemple"), None)
    if spawn is None:
        raise ValueError("north extent location dump has no StartTemple")
    measured = NI.coarse_route(grid, (float(spawn["x"]), float(spawn["z"])),
                               preset=expected["preset"])
    NI.validate_frontier_evidence(doc, measured)
    search = doc["frontier_search"]
    inputs = search["inputs"]
    if (NI.file_identity(grid_path) != inputs["grid"]
            or NI.file_identity(locations_path) != inputs["locations"]):
        raise ValueError("north frontier citations differ from measured search inputs")
    fields_path = inputs["field"]["path"]
    F.require_field(fields_path, seed)
    F.require_seed(fields_path, seed)
    fields = F.load(fields_path)
    NI.require_search_coverage(measured, fields)
    import network as net
    import precensus
    if "census" not in inputs:
        raise ValueError("north frontier has no scoped measured census for rejection replay")
    census = precensus.require(Path(inputs["census"]["path"]), world=world, seed=seed)
    with ws.scope(seed, world):
        for rejected in search.get("rejected", []):
            NI.replay_frontier_rejection(rejected, measured, fields, net, census, search)
    selected = NI.route_for_candidate(measured, doc["frontier"]["candidate_id"])
    root = doc["terminals"][doc["final_departure"]]["xz"]
    ok, numbers = extent_verdict(
        root[1], selected["frontier"]["coarse_northing_m"],
        measured["search_step_m"],
        relocation_m=NI.WAYPOINT_REFINE_RADIUS_M + NI.TERMINAL_ROOT_RADIUS_M)
    numbers["independently_rejected_higher_candidates"] = len(search.get("rejected", []))
    return _finding(ok, "Final road/dock must reach the furthest independently "
                    "buildable frontier after replaying every higher refusal",
                    tuple(root), check="north_extent", **numbers)


def _transforms():
    I._worldseed()  # expose the repository's namespace packages, lazily
    from blueprints.to_rcon_plan import euler_to_quat, quat_angle_between
    return euler_to_quat, quat_angle_between


def match_pieces(required, solids, *, check="dock_pieces", label="dock"):
    """One-to-one saved-piece census; the same object cannot fill two slots."""
    euler, angle = _transforms()
    grid, used, matched = I.Grid(solids), set(), []
    for piece in required:
        pos = tuple(float(v) for v in piece["pos"])
        quat = euler(*piece["rot"])
        choices = [s for s in grid.near(pos[0], pos[2], POSITION_TOL_M)
                   if id(s) not in used and s.name == piece["prefab"]
                   and s.known and math.dist(s.pos, pos) <= POSITION_TOL_M
                   and angle(s.quat, quat) <= ROTATION_TOL_DEG]
        actual = min(choices, key=lambda s: math.dist(s.pos, pos), default=None)
        if actual is not None:
            used.add(id(actual))
            matched.append(actual)
    return matched, [_finding(bool(required) and len(matched) == len(required), f"{label}: {len(matched)}/{len(required)} required pieces actually "
    f"present at their measured poses (tolerance {POSITION_TOL_M:g} m, "
    f"{ROTATION_TOL_DEG:g} degrees)", tuple(required[0]["pos"]) if required else None,
    required=len(required), saved=len(matched), position_tol_m=POSITION_TOL_M,
    rotation_tol_deg=ROTATION_TOL_DEG, check=check)]


def _walk_points(path):
    """Quarter-metre capsule sweep plus EVERY ground-mesh corner crossed.

    Fine pose samples alone cannot see a diagonal ridge between two equal
    terrain vertices. The shared snap convention identifies both possible
    corners; they are read and judged on the same one-metre allowance.
    """
    points = [(x, z) for _, x, z in I.resample(path, 0.25)]
    queries = set(points)
    for a, b in zip(points, points[1:]):
        ax, az = I.snap(*a)
        bx, bz = I.snap(*b)
        if ax != bx and az != bz:
            queries.update(((ax, bz), (bx, az)))
    return points, queries


def walk_access(path, root, dock, solids, geom, ground, walker):
    """Follow reachable saved surfaces from road root to the dock's head.

    Reachability propagates actual heights; a roof cannot become a bridge by
    picking the highest surface independently at each sample. Unknown nearby
    colliders are unread, not transparent. The sea floor is never a walkway.
    """
    if not path or tuple(path[0]) != tuple(root) or len(path) < 2:
        return False, {"reason": "walk must start at the exact road endpoint", "samples": 0}
    points, _ = _walk_points(path)
    grid = I.Grid(solids)
    radius, height = walker
    reachable = []
    last = None

    def surfaces(x, z):
        bed = ground.get(I.gpoint(x, z)[0])
        if bed is None or not math.isfinite(bed):
            return []
        candidates = [(bed, None)] if bed > I.WATER_LEVEL else []
        nearby = grid.near(x, z, radius + 2.0)
        if any(not s.known and math.hypot(s.pos[0]-x, s.pos[2]-z)
               < radius + 1.0 for s in nearby):
            return []
        for s in nearby:
            for hull, _bottom, plane in s.part_faces(geom):
                if plane is not None and I._hull_covers(hull, x, z):
                    y = plane[0]*x + plane[1]*z + plane[2]
                    if y > I.WATER_LEVEL and y >= bed - 0.01:
                        candidates.append((y, s))
        return [(y, s) for y, s in candidates
                if A.free_radius(x, z, y, grid, height, cap=radius+0.1,
                                 underfoot_tol=0.05)[0] >= radius
                and A.headroom(x, z, y, grid, radius, cap=height+0.1)[0] >= height]

    for index, (x, z) in enumerate(points):
        candidates = surfaces(x, z)
        if last is None:
            bed = ground.get(I.gpoint(x, z)[0])
            reachable = [(y, s) for y, s in candidates
                         if bed is not None and abs(y-bed) <= I.STEP_LIMIT_PER_M]
        else:
            distance = math.dist(last, (x, z))
            previous = reachable
            ax, az = I.snap(*last)
            bx, bz = I.snap(x, z)
            corners = ([(ax, bz), (bx, az)]
                       if ax != bx and az != bz else [])
            corner_surfaces = [surfaces(*c) for c in corners]
            reachable = []
            for y, s in candidates:
                for py, ps in previous:
                    # Solid faces are swept at 0.25 m; terrain is resolved on
                    # one-metre vertices, not four fictitious smaller steps.
                    allowance = I.STEP_LIMIT_PER_M * (
                        max(distance, 1.0) if s is None or ps is None else distance)
                    if I.exceeds_step_limit(abs(y-py), allowance):
                        continue
                    if corners and s is None and ps is None and not any(
                            any(not I.exceeds_step_limit(max(abs(cy-py), abs(y-cy)),
                                                        I.STEP_LIMIT_PER_M)
                                for cy, _ in cs) for cs in corner_surfaces):
                        continue
                    reachable.append((y, s))
                    break
        if not reachable:
            return False, {"reason": "unread/wet/blocked/unreachable walking surface",
                           "samples": len(points), "reached_samples": index,
                           "at": [x, z]}
        last = (x, z)
    dock_ids = {id(s) for s in dock}
    on_dock = any(s is not None and id(s) in dock_ids for _, s in reachable)
    return on_dock, {"samples": len(points), "reached_samples": len(points),
                     "on_saved_dock": on_dock, "at": list(last)}


def standing_lighthouse(required, actual, ground, geom):
    """A complete tall connected BODY supported by measured ground, not a prop."""
    from . import structures as S
    solid = [s for s in actual if s.verts]
    groups = I.bodies(solid)
    minimum = max(MIN_LIGHTHOUSE_HEIGHT_M, float(required["height_m"]) * 0.95)
    towers = [g for g in groups if len(g) >= 8
              and max(s.ymax for s in g)-min(s.ymin for s in g) >= minimum]
    support = S.check_grounding([(g, [], "north lighthouse") for g in groups],
                                ground, S.Columns(geom, categories={}), geom)
    bad = [f for f in support if not f.ok and not f.advisory]
    tallest = max((max(s.ymax for s in g)-min(s.ymin for s in g)
                   for g in groups), default=0.0)
    complete = len(actual) == len(required["pieces"]) and bool(actual)
    return complete and bool(towers) and not bad, {
        "required_pieces": len(required["pieces"]), "saved_pieces": len(actual),
        "connected_bodies": len(groups), "height_m": round(tallest, 4),
        "minimum_height_m": minimum,
        "grounding_refusals": [f.message for f in bad]}


def dock_support(dock, ground, geom):
    """Measure actual saved foundations, never the planner's bed or verdict."""
    from . import structures as S
    I._worldseed()
    from crossings import assemble
    cols = S.Columns(geom, categories={})
    queries = {p for s in dock for p in S.piece_columns(s)}
    missing = [p for p in queries if ground.get(I.gpoint(*p)[0]) is None
               or not math.isfinite(ground[I.gpoint(*p)[0]])]
    converted = {S.gkey(x, z): ground[I.gpoint(x, z)[0]]
                 for x, z in queries if (x, z) not in missing}
    groups = I.bodies([s for s in dock if s.verts])
    support = S.check_grounding([(g, [], "north dock") for g in groups],
                                converted, cols, geom)
    bad = [f.message for f in support if not f.ok and not f.advisory]
    # A shore tile touching ground does not found an outboard pile column.
    piles = {}
    for solid in dock:
        if solid.name == "wood_pole_log_4":
            piles.setdefault((round(solid.pos[0], 2), round(solid.pos[2], 2)), []).append(solid)
    unfounded = []
    for xz, column in piles.items():
        foot = min(column, key=lambda s: s.ymin)
        embeds = []
        for x, z in S.piece_columns(foot):
            y = ground.get(I.gpoint(x, z)[0])
            span = cols.span(foot, x, z)
            if y is not None and math.isfinite(y):
                embeds.append(y-(span[0] if span is not None else foot.ymin))
        embed = max(embeds, default=-math.inf)
        if embed < assemble.PILE_EMBED_MIN_M:
            unfounded.append(dict(xz=xz, embed_m=embed if math.isfinite(embed) else None))
    return bool(dock) and bool(groups) and not missing and not bad and not unfounded, {
        "saved_pieces": len(dock), "foundation_columns": len(piles),
        "unread_vertices": len(missing), "grounding_refusals": bad,
        "unfounded_columns": unfounded, "minimum_embed_m": assemble.PILE_EMBED_MIN_M}


def _saved_key(solid):
    # Local solids are rebuilt for each terminal. Python identity would let
    # one saved ship satisfy two endpoints in overlapping observation masks.
    return (solid.name, *solid.pos, *solid.quat)


def is_large_departure(ship, clear):
    I._worldseed()
    from settlements import waterfront as WF
    spec = WF.VESSELS.get(ship.name) if ship is not None else None
    return bool(clear and spec and ship.name in WF.ODIN_SHIPS and spec["cls"] == "large")


def _ship(spec, solids, used):
    """A float may drift, but not into another terminal or out of the water."""
    pos = spec["pos"]
    possible = [s for s in solids if s.name == spec["prefab"]
                and _saved_key(s) not in used and s.known and s.verts
                and math.hypot(s.pos[0]-pos[0], s.pos[2]-pos[2]) <= BOARDING_GAP_M
                and abs(s.pos[1]-pos[1]) <= 2.0
                and I.rotate(s.quat, (0.0, 1.0, 0.0))[1] >= math.cos(math.radians(15))]
    ship = min(possible, key=lambda s: math.dist(s.pos, pos), default=None)
    if ship is not None:
        used.add(_saved_key(ship))
    return ship


def ship_clearance(ship, hull, ground, solids, geom, dock_head):
    from . import harbours as H
    pose = (ship.pos[0], ship.pos[2], H.yaw_of(ship.quat))
    footprint = H.footprint(hull, *pose)
    readings = [ground.get(I.gpoint(x, z)[0]) for x, z in footprint]
    complete = bool(readings) and all(y is not None and math.isfinite(y) for y in readings)
    depth = I.WATER_LEVEL - max(readings) if complete else None
    required_depth = max(
        hull.required_depth_m, I.WATER_LEVEL-ship.ymin+hull.keel_clearance_m)
    blockers, unread = [], []
    if hull.air_m is None:
        unread.append(f"{hull.name}: air draft unmeasured")
    for other in solids:
        if other is ship:
            continue
        if not other.known:
            if A.hull_distance(ship, other.pos[0], other.pos[2]) <= 1.0:
                unread.append(other.name)
            continue
        for part in geom.get(other.name, ()):
            barrier = H.barrier_from_part(other.name, other.pos, other.quat,
                                          part, I.rotate)
            if H.swept_hull_intersects_barrier(
                    hull, pose, pose, barrier, vertical_band=(ship.ymin, ship.ymax)):
                (blockers if barrier.tight else unread).append(other.name)
    gap = max(0.0, A.hull_distance(ship, *dock_head))
    ok = (complete and depth >= required_depth and not blockers and not unread
          and gap <= BOARDING_GAP_M)
    return ok, {"depth_m": depth, "required_depth_m": required_depth,
                "air_draft_m": hull.air_m,
                "hull_vertices": len(footprint), "read_vertices": sum(y is not None for y in readings),
                "blocking_parts": blockers, "unmeasured_parts": unread,
                "boarding_gap_m": gap, "boarding_limit_m": BOARDING_GAP_M}


def paired_endpoints(doc, terminals):
    return [_finding(terminals.get(pair["a"]) is True and terminals.get(pair["b"]) is True, f"{pair['id']}: BOTH physical docks and boats required: "
    f"{pair['a']}={terminals.get(pair['a'], False)}, "
    f"{pair['b']}={terminals.get(pair['b'], False)}", tuple(doc["terminals"][pair["a"]]["xz"]),
    transport=pair["id"], a=pair["a"], b=pair["b"],
    endpoints_accepted=sum(terminals.get(k) is True for k in (pair["a"], pair["b"])),
    required=2, check="paired_endpoints") for pair in doc["transport"]]


def _passage_poses(route, start_yaw, end_yaw, radius):
    """One-metre translation and <=1 m tip travel while turning a full hull."""
    from . import harbours as H
    poses = []
    previous = start_yaw
    angle_step = min(5.0, math.degrees(1.0 / max(radius, 1.0)))
    for index, (a, b) in enumerate(zip(route, route[1:])):
        if math.dist(a, b) < 1e-9:
            continue
        heading = math.degrees(math.atan2(b[0]-a[0], b[1]-a[1]))
        delta = (heading-previous+180) % 360-180
        turns = max(1, math.ceil(abs(delta)/angle_step))
        poses.extend((a[0], a[1], previous+delta*k/turns)
                     for k in range(turns+1))
        poses.extend((x, z, heading) for x, z in H.densify([a, b], 1.0))
        previous = heading
    delta = (end_yaw-previous+180) % 360-180
    turns = max(1, math.ceil(abs(delta)/angle_step))
    poses.extend((*route[-1], previous+delta*k/turns)
                 for k in range(turns+1))
    return poses


def measure_saved_passages(ctx, doc, observed, terminals, hulls):
    """Independently measure each sea route on SAVED terrain and colliders.

    Never use the planner's connected/depth booleans or the ledger's applied
    field as evidence. Both endpoint vessel sizes, and rotations at all bends,
    must clear. No straight-line fallback replaces an unread sea_route.
    """
    from . import harbours as H
    from . import roads as R
    findings, verified = [], []
    for pair in doc["transport"]:
        a, b = pair["a"], pair["b"]
        route = pair.get("sea_route")
        boats = observed.get(a, []) + observed.get(b, [])
        valid_route = (isinstance(route, list) and len(route) >= 2
                       and all(isinstance(p, (list, tuple)) and len(p) == 2
                               and all(isinstance(v, (int, float)) and math.isfinite(v)
                                       for v in p) for p in route))
        if (not valid_route or not terminals.get(a) or not terminals.get(b)
                or not observed.get(a) or not observed.get(b)):
            findings.append(_finding(
                False, f"{pair['id']}: no complete physical endpoints/sea_route",
                tuple(doc["terminals"][a]["xz"]), check="voyage",
                endpoints=sum(bool(terminals.get(t)) for t in (a, b)),
                route_points=len(route) if isinstance(route, list) else 0))
            continue
        first, last = observed[a][0], observed[b][0]
        I._worldseed()
        from settlements import waterfront as WF
        declared = [doc["terminals"][t].get("vessel") for t in (a, b)]
        same_type = (first.name == last.name and first.name in WF.VESSELS
                     and all(v is None or v == first.name for v in declared))
        if not same_type:
            findings.append(_finding(
                False, f"{pair['id']}: physical endpoint boats must match the declared measured vessel",
                tuple(doc["terminals"][a]["xz"]), check="voyage",
                vessels=[first.name, last.name], declared_vessels=declared))
            continue
        route = [(first.pos[0], first.pos[2]), *[tuple(p) for p in route],
                 (last.pos[0], last.pos[2])]
        vessels = {ship.name: hulls[ship.name] for ship in boats}
        radius = max(h.radius_m for h in vessels.values())
        vertical_band = min(s.ymin for s in boats), max(s.ymax for s in boats)
        required_depths = {
            name: max(h.required_depth_m,
                      I.WATER_LEVEL-min(s.ymin for s in boats if s.name == name)
                      + h.keel_clearance_m)
            for name, h in vessels.items()}
        poses = _passage_poses(route, H.yaw_of(first.quat), H.yaw_of(last.quat), radius)
        footprints = {hull.name: H.swept_footprint(hull, poses)
                      for hull in vessels.values()}
        queries = set().union(*footprints.values())
        ground = ctx.ground([I.gpoint(x, z) for x, z in sorted(queries)])
        mask = R.corridor_mask([(x, z) for x, z, _ in poses],
                               reach_m=radius+I.CORRIDOR_MASK_M)
        rows = [row for row in ctx.census.rows
                if (int(row[1]//4), int(row[3]//4)) in mask]
        solids = I.solids_from_census(I.Census(rows, *ctx.walker, 0), ctx.geom)
        grid = I.Grid(solids)
        endpoint_ships = {_saved_key(s) for s in boats}
        barriers, blockers, unread = {}, set(), set()
        min_depth, missing, at = math.inf, 0, None
        for hull in vessels.values():
            if hull.air_m is None:
                unread.add(f"{hull.name}: air draft unmeasured")
            for px, pz in footprints[hull.name]:
                y = ground.get(I.gpoint(px, pz)[0])
                if y is None or not math.isfinite(y):
                    missing += 1
                    at = at or (px, pz)
                else:
                    depth = I.WATER_LEVEL-y
                    min_depth = min(min_depth, depth)
                    if depth < required_depths[hull.name]:
                        at = at or (px, pz)
            for start, end in zip(poses, poses[1:]):
                x, z = (start[0]+end[0])/2.0, (start[1]+end[1])/2.0
                reach = radius+math.hypot(end[0]-start[0], end[1]-start[1])/2.0
                for solid in grid.near(x, z, reach):
                    if _saved_key(solid) in endpoint_ships:
                        continue
                    key = _saved_key(solid)
                    if not solid.known:
                        if math.hypot(solid.pos[0]-x, solid.pos[2]-z) <= reach:
                            unread.add(solid.name)
                        continue
                    if key not in barriers:
                        barriers[key] = [
                            H.barrier_from_part(solid.name, solid.pos, solid.quat,
                                                part, I.rotate)
                            for part in ctx.geom.get(solid.name, ())]
                    for barrier in barriers[key]:
                        if H.swept_hull_intersects_barrier(
                                hull, start, end, barrier, vertical_band=vertical_band):
                            (blockers if barrier.tight else unread).add(
                                f"{solid.name}@{solid.pos}")
                            at = at or (x, z)
        required = max(required_depths.values())
        ok = bool(poses) and not missing and not blockers and not unread and at is None
        evidence = dict(transport=pair["id"], a=a, b=b,
                        hull_stations=len(poses), vertices=len(queries),
                        unread_vertices=missing,
                        min_depth_m=min_depth if math.isfinite(min_depth) else None,
                        required_depth_m=required, vessels=sorted(vessels),
                        air_draft_m={h.name: h.air_m for h in vessels.values()},
                        blocking_parts=sorted(blockers), unmeasured_parts=sorted(unread))
        findings.append(_finding(ok, f"{pair['id']}: full saved-hull sea passage "
                                 f"{'clear' if ok else 'REFUSED'}",
                                 at or route[0], check="voyage", **evidence))
        if ok:
            verified.append(dict(id=pair["id"], a=a, b=b,
                                 sailing_m=sum(math.dist(p, q)
                                               for p, q in zip(route, route[1:])),
                                 **{k: evidence[k] for k in
                                    ("min_depth_m", "required_depth_m", "hull_stations")}))
    return findings, verified


def run(ctx):
    """Acquire the physical north reading once, shared with roads acceptance."""
    result = getattr(ctx, "_north_transport_gate_result", None)
    if result is None:
        result = _measure(ctx)
        ctx._north_transport_gate_result = result
    return result


def road_transport_links(ctx, positions, segment_of):
    """Explicit voyage edges, never inserted into the walking graph."""
    result = run(ctx)
    doc, _ = I.north_itinerary(ctx)
    terminals = getattr(ctx, "_transport_terminal_contract",
                        doc["terminals"] if doc is not None else {})
    links, missing = [], []
    for pair in result.report.get("verified_pairs", []):
        keys = []
        endpoint_positions = {}
        natural_endpoints = {}
        for tid in (pair["a"], pair["b"]):
            terminal = terminals[tid]
            vertex = I.snap(*terminal["xz"])
            candidates = [key for key, pos in positions.items()
                          if tuple(pos) == vertex
                          and segment_of.get(key) == terminal["segment_id"]]
            key = min(candidates) if candidates else None
            if key is None and terminal.get("natural_ground") is True:
                # Only a saved-accepted dock/access/boat/voyage can create this
                # vertex, and ONLY in the multimodal journey graph.
                key = "shore:" + tid
                endpoint_positions[key] = vertex
                natural_endpoints[key] = tid
            keys.append(key)
        if None in keys:
            missing.append(pair["id"])
            continue
        links.append({**pair, "ka": keys[0], "kb": keys[1],
                      "endpoint_positions": endpoint_positions,
                      "natural_endpoints": natural_endpoints,
                      "walking_m": sum(result.report["terminals"][tid]["dock_walk_m"]
                                       for tid in (pair["a"], pair["b"]))})
    hard = [f for f in result.findings if not f.ok and not f.advisory]
    finding = Finding(
        check="north_transport", ok=not hard and not missing,
        message=f"north transport: {len(links)} physical voyage edge(s), "
                f"{len(hard)} physical refusal(s), {len(missing)} missing road roots",
        numbers={"verified_voyages": len(links), "missing_road_roots": missing,
                 "physical_refusals": len(hard),
                 "failures": [{"check": f.check, "where": f.where,
                               "message": f.message, "numbers": f.numbers}
                              for f in hard]})
    return links, [finding], result.report


def shore_road_population(sites, planned, built):
    """Every road-backed shore requires its actual planned AND built root.

    Source centres can legitimately be trimmed at pads; compare the serving
    road's real final endpoints, not an invented centre-to-dock segment.
    """
    I._worldseed()
    from roads.shore_build import ROAD_OF
    rows = {row["id"]: row for row in planned.get("segments", [])}
    findings = []
    def xz(point):
        return (float(point["x"]), float(point["z"])) if isinstance(point, dict) else tuple(map(float, point[:2]))
    for site in sites:
        sid = ROAD_OF.get(site["node"])
        if sid is None:
            findings.append(_finding(False, f"{site['id']}: no declared serving-road obligation",
                tuple(site["root"]), check="north_routes", terminal=site["id"]))
            continue
        row = rows.get(sid)
        record = built.get("road_"+sid.replace("-", "_"))
        expected = None if not row or not row.get("nodes") else (xz(row["nodes"][0]), xz(row["nodes"][-1]))
        actual = None if not record or not record.get("nodes") else (xz(record["nodes"][0]), xz(record["nodes"][-1]))
        root = tuple(map(float, site["root"]))
        ok = (not planned.get("provisional") and expected is not None
              and root in expected and actual in (expected, tuple(reversed(expected))))
        findings.append(_finding(ok,
            f"{site['id']}: required serving road {sid} {'present at exact shore root' if ok else 'MISSING/misrouted/provisional'}",
            root, check="north_routes", segment=sid, terminal=site["id"],
            required_root=root, planned_ends=expected, built_ends=actual))
    return findings


def _measure(ctx):
    doc, findings = I.north_itinerary(ctx)
    findings = list(findings)
    if doc is None:
        return SectionResult(findings=findings)
    # Population closure is also required for --section north_transport alone.
    # The roads section owns the full route/crossing physical verdict.
    from .roads import built_segments
    ordinary_roads = built_segments(ctx.ledger)
    findings += I.north_road_population(ctx, ordinary_roads)
    seed, world = I.run_scope(ctx)
    try:
        I._worldseed()
        from roads import north_transport as N
        findings.append(measure_extent(ctx, doc))
        from . import harbours as H
        field_path = N.field_path(
            getattr(ctx.args, "north_field", None)
            or os.environ.get("VH_NORTH_FIELD")
            or (doc.get("height_source") or {}).get("field"))
        field = N.load_field(field_path, seed)
        hulls = H.hull_table()
        plans = {tid: N.plan_terminal(doc, tid, field, actor="NorthAcceptance")
                 for tid in doc["terminals"]}
        # Semantic waterfronts share this physical instrument, not a second
        # acceptance path trusting planning booleans or a differently sized hull.
        import yaml
        import worldseed
        from roads import shores, shore_build
        with worldseed.scope(seed, world):
            sites = shores.load()
            shore_road_path = worldseed.segments_path(seed)
            shore_roads = yaml.safe_load(shore_road_path.read_text()) or {}
            worldseed.require_scope("shore serving-road population", shore_road_path,
                found_seed=shore_roads.get("seed"), found_world=shore_roads.get("world"))
            shore_population = shore_road_population(sites, shore_roads, ordinary_roads)
            findings += shore_population
            shore_road_verdicts = {f.numbers["terminal"]: f.ok for f in shore_population}
            publication = yaml.safe_load(worldseed.waterfront_path(seed, world).read_text())
            shore_fields = N.ScopedFields(shores.load_fields(publication["field"]),
                                           seed, source=publication["field"])
            shore_surface = N.Surface(shore_fields, world=world,
                actor="ShoreAcceptance", led=N.LedgerView(world, seed))
            shore_plans, _ = shore_build.plan(sites, shore_fields, shore_surface)
        doc = dict(doc, terminals=dict(doc["terminals"]),
                   transport=list(doc["transport"]))
        for site in sites:
            doc["terminals"][site["id"]] = dict(xz=site["root"],
                segment_id=shore_build.ROAD_OF.get(site["node"]),
                final_departure=False, shore=True,
                natural_ground=site["node"] not in shore_build.ROAD_OF,
                requires_large_ship=bool(site.get("large_ship")))
            if site.get("pair_requirement"):
                doc["transport"].append(site["pair_requirement"])
        plans.update(shore_plans)
        ctx._transport_terminal_contract = doc["terminals"]
    except (Exception, SystemExit) as exc:
        findings.append(_finding(False, f"north physical plan cannot be measured: {exc}", terminals=len(doc["terminals"]), check="instrument"))
        return SectionResult(findings=findings)
    verdicts, report, used_boats, observed = {}, {}, set(), {}
    for tid, plan in plans.items():
        root = doc["terminals"][tid]["xz"]
        start = len(findings)
        lighthouse = plan.get("lighthouse")
        pieces = plan["pieces"] + (lighthouse["pieces"] if lighthouse else [])
        positions = [p["pos"] for p in pieces + plan["boats"]]
        if not positions:
            findings.append(_finding(False, f"{tid}: empty terminal plan", tuple(root), pieces=0, check="dock_pieces"))
            verdicts[tid] = False
            continue
        # Mask BEFORE rotating solids, as the roads instrument does. Include
        # the complete body and every hull footprint, not just the road root.
        margin = max((h.length_m for h in hulls.values()), default=30.0) + 4.0
        xmin, xmax = min(p[0] for p in positions)-margin, max(p[0] for p in positions)+margin
        zmin, zmax = min(p[2] for p in positions)-margin, max(p[2] for p in positions)+margin
        rows = [r for r in ctx.census.rows if xmin <= r[1] <= xmax and zmin <= r[3] <= zmax]
        local = I.Census(rows, *ctx.walker, 0)
        solids = I.solids_from_census(local, ctx.geom)
        dock, rows_found = match_pieces(plan["pieces"], solids, label=tid)
        findings += rows_found
        tower, tower_findings = (match_pieces(lighthouse["pieces"], solids, check="lighthouse", label=tid+" lighthouse")
                                 if lighthouse else ([], []))
        findings += tower_findings
        boats = [(spec, _ship(spec, solids, used_boats)) for spec in plan["boats"]]
        points, queries = _walk_points(plan["walk_xz"])
        from . import structures as S
        for s in dock + tower:
            queries.update(S.piece_columns(s))
        for spec, ship in boats:
            hull = hulls.get(spec["prefab"])
            if ship is not None and hull is not None:
                queries.update(H.footprint(hull, ship.pos[0], ship.pos[2], H.yaw_of(ship.quat)))
        ground = ctx.ground([I.gpoint(x, z) for x, z in sorted(queries)])
        supported, support_evidence = dock_support(dock, ground, ctx.geom)
        findings.append(_finding(supported, f"{tid}: saved dock foundations "
                                 f"{'supported' if supported else 'REFUSED'}",
                                 tuple(root), check="dock_support", **support_evidence))
        # A tree that can be cleared is still a physical obstruction until
        # it IS cleared. No ownership/clearability category makes it vanish.
        access, evidence = walk_access(plan["walk_xz"], root, dock, solids,
                                       ctx.geom, ground, ctx.walker)
        findings.append(_finding(access, f"{tid}: actual road-to-dock walk {'reachable' if access else 'REFUSED'}", tuple(root), **evidence, check="dock_access"))
        findings.append(_finding(bool(boats), f"{tid}: at least one physical boat required", tuple(root), required=1, planned=len(boats), check="boat"))
        large = False
        for spec, ship in boats:
            findings.append(_finding(ship is not None, f"{tid}: saved upright {spec['prefab']} {'present' if ship else 'MISSING/unmeasured'}", tuple(spec["pos"]), saved=int(ship is not None), required=1, check="boat"))
            hull = hulls.get(spec["prefab"])
            if ship is None:
                continue
            if hull is None:
                findings.append(_finding(False, f"{tid}: hull {spec['prefab']} has no measured dimensions", ship.pos, hulls=0, check="instrument"))
                continue
            clear, ship_evidence = ship_clearance(ship, hull, ground, solids, ctx.geom, points[-1])
            findings.append(_finding(clear, f"{tid}: actual hull depth, obstruction and boarding clearance {'accepted' if clear else 'REFUSED'}", ship.pos, **ship_evidence, check="boat_clearance"))
            if clear:
                observed.setdefault(tid, []).append(ship)
            large |= is_large_departure(ship, clear)
        if doc["terminals"][tid].get("requires_large_ship") and not doc["terminals"][tid]["final_departure"]:
            findings.append(_finding(large,
                f"{tid}: required physical large Odin vessel {'present' if large else 'MISSING/wrong provider or class'}",
                tuple(root), accepted_large_ships=int(large), required=1, check="departure_ship"))
        if doc["terminals"][tid]["final_departure"]:
            standing, tower_evidence = (standing_lighthouse(lighthouse, tower, {
                S.gkey(x, z): ground[I.gpoint(x, z)[0]] for x, z in queries
                if I.gpoint(x, z)[0] in ground}, ctx.geom) if lighthouse else
                (False, {"saved_pieces": 0, "height_m": 0}))
            findings.append(_finding(standing, f"{tid}: tall connected lighthouse physically {'standing' if standing else 'MISSING/unsupported/incomplete'}", tuple(root), **tower_evidence, check="lighthouse"))
            findings.append(_finding(large, f"{tid}: physically usable measured large Odin departure ship {'present' if large else 'MISSING/wrong provider or class'}", tuple(root), accepted_large_ships=int(large), required=1, check="departure_ship"))
        verdicts[tid] = (all(f.ok or f.advisory for f in findings[start:])
                         and (shore_road_verdicts.get(tid, True)))
        report[tid] = {"accepted": verdicts[tid], "saved_dock_pieces": len(dock),
                       "saved_lighthouse_pieces": len(tower), "saved_boats": sum(s is not None for _, s in boats),
                       "road_access": evidence,
                       "dock_walk_m": sum(math.dist(p, q) for p, q in
                                          zip(plan["walk_xz"], plan["walk_xz"][1:]))}
    findings += paired_endpoints(doc, verdicts)
    passage_findings, verified = measure_saved_passages(
        ctx, doc, observed, verdicts, hulls)
    findings += passage_findings
    return SectionResult(findings=findings, report={"terminals": report,
                         "verified_pairs": verified,
                         "pairs": len(doc["transport"]), "final_departure": doc["final_departure"],
                         "save_fingerprint": ctx.fp, "field": str(field_path)})


# Deterministic regression states. No saved evidence, terrain scan, server or
# corpus is borrowed by these fixtures, and none is executed at import time.
def _itinerary():
    return {"schema_version": 1, "seed": "ExampleSeed", "world": "ExampleWorld",
            "provisional": False,
            "nodes": {"a": {"xz": [0, 0]}, "b": {"xz": [0, 2]},
                      "c": {"xz": [20, 0]}, "d": {"xz": [20, 2]}},
            "road_edges": [{"id": "N0-a-b", "a": "a", "b": "b", "cls": "trunk"},
                           {"id": "N1-c-d", "a": "c", "b": "d", "cls": "trunk"}],
            "terminals": {"north-dock-000": {"xz": [0, 2], "sea_xz": [0, 8], "segment_id": "N0-a-b", "final_departure": False, "vessel": "MercantShip"},
                          "north-dock-001": {"xz": [20, 2], "sea_xz": [20, 8], "segment_id": "N1-c-d", "final_departure": True, "vessel": "MercantShip"}},
            "transport": [{"id": "sail-0", "a": "north-dock-000", "b": "north-dock-001",
                           "sea_route": [[0, 4], [20, 4]]}],
            "final_departure": "north-dock-001"}


def _schema_case(change=None):
    doc = _itinerary()
    if change:
        change(doc)
    errors = I.north_itinerary_errors(doc)
    return _finding(not errors, "; ".join(errors) or "complete scoped population", errors=len(errors), check="north_artifact")


def _pair_case(both):
    return paired_endpoints(_itinerary(), {"north-dock-000": True, "north-dock-001": both})


_FIX_GEOM = {"deck": [["b", -1.1, -0.1, -1.1, 1.1, 0, 1.1]],
             "wall": [["b", -2, 0, -0.1, 2, 3, 0.1]],
             "tower": [["b", -0.3, 0, -0.3, 0.3, 2, 0.3]]}


def _walk_case(hole=False, wall=False):
    dock = [I.mk_solid("deck", (0, 31, z), I.yaw(0), _FIX_GEOM["deck"])
            for z in (2, 4, 6) if not (hole and z == 4)]
    solids = list(dock)
    if wall:
        solids.append(I.mk_solid("wall", (0, 31, 3), I.yaw(0), _FIX_GEOM["wall"]))
    path = [(0, 0), (0, 6)]
    _, queries = _walk_points(path)
    ground = {I.gpoint(x, z)[0]: (31 if z < 1 else 27) for x, z in queries}
    ok, evidence = walk_access(path, path[0], dock, solids, _FIX_GEOM, ground, (0.4, 1.8))
    return _finding(ok, "synthetic applied dock walk", (0, 0), **evidence, check="dock_access")


def _pieces_case(missing=False, inverted=False, duplicate=False):
    piece = {"prefab": "deck", "pos": [0, 31, 0], "rot": [0, 0, 0]}
    actual = [] if missing else [I.mk_solid("deck", piece["pos"],
                 (1, 0, 0, 0) if inverted else I.yaw(0), _FIX_GEOM["deck"])]
    return match_pieces([piece, piece] if duplicate else [piece], actual)[1]


def _tower_case(floating=False, short=False):
    count = 3 if short else 8
    base = 35 if floating else 31
    tower = [I.mk_solid("tower", (10, base+2*i, 10), I.yaw(0), _FIX_GEOM["tower"])
             for i in range(count)]
    from . import structures as S
    ground = {S.gkey(x, z): 31 for s in tower for x, z in S.piece_columns(s)}
    ok, evidence = standing_lighthouse({"height_m": 16, "pieces": [{}]*count}, tower, ground, _FIX_GEOM)
    return _finding(ok, "synthetic connected lighthouse body", (10, 10), **evidence, check="lighthouse")


def _routes_case(missing=False):
    doc = _itinerary()
    ctx = SimpleNamespace(_north_itinerary_read=(doc, []))
    segments = {"road_"+e["id"].replace("-", "_"): {"nodes": [doc["nodes"][e[k]]["xz"] for k in ("a", "b")]}
                for e in doc["road_edges"]}
    if missing:
        segments.pop("road_N1_c_d")
    return I.north_road_population(ctx, segments)


def _artifact_case(kind):
    import json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="north-gate-case-") as directory:
        path = Path(directory) / "itinerary.yaml"
        doc = _itinerary()
        if kind == "foreign":
            doc.update(seed="OtherSeed", world="OtherWorld")
        if kind != "missing":
            path.write_text("nodes: [unclosed" if kind == "unreadable"
                            else json.dumps(doc))
        ctx = SimpleNamespace(args=SimpleNamespace(
            seed="ExampleSeed", world="ExampleWorld",
            north_itinerary=str(path)))
        _doc, findings = I.north_itinerary(ctx)
        return findings


def _ship_case(missing=False, inverted=False, reuse=False):
    geom = [["b", -1, -1, -2, 1, 1, 2, "box"]]
    ship = I.mk_solid("fixture-hull", (0, 30, 0),
                      (1, 0, 0, 0) if inverted else I.yaw(0), geom)
    spec = {"prefab": ship.name, "pos": ship.pos}
    used = set()
    if reuse:
        _ship(spec, [ship], used)
        # A second terminal's own Solid object represents the SAME save row.
        ship = I.mk_solid(ship.name, ship.pos, ship.quat, geom)
    actual = _ship(spec, [] if missing else [ship], used)
    return _finding(actual is not None, "saved upright unique hull", (0, 0), saved=int(actual is not None), check="boat")


def _clearance_case(shallow=False, unread=False, blocked=False, thin=False):
    from . import harbours as H
    hull = H.Hull("fixture-hull", 2, 4, 1, 2, 0.2, "synthetic fixture",
                  ((-1, -2), (1, -2), (1, 2), (-1, 2)))
    geom = {hull.name: [["b", -1, -1, -2, 1, 1, 2, "box"]],
            "pile": [["b", -0.2, -1, -0.2, 0.2, 1, 0.2, "box"]]}
    ship = I.mk_solid(hull.name, (0, 30, 0), I.yaw(0), geom[hull.name])
    solids = [ship]
    if blocked:
        solids.append(I.mk_solid("pile", (0, 29.5, 0), I.yaw(0), geom["pile"]))
    if thin:
        solids.append(I.mk_solid("pile", (0.5, 29.5, 0.5), I.yaw(37), geom["pile"]))
    ground = {} if unread else {
        I.gpoint(x, z)[0]: 29.5 if shallow else 27
        for x, z in H.footprint(hull, 0, 0, 0)}
    ok, evidence = ship_clearance(ship, hull, ground, solids, geom, (2, 0))
    return _finding(ok, "actual hull footprint and boarding", (0, 0), **evidence, check="boat_clearance")


def _placed_height_case(sunk=False, above=False):
    from . import harbours as H
    hull = H.Hull("placed-hull", 2, 4, 1, 2.5, 0.2, "numeric collider fixture",
                  ((-1, -2), (1, -2), (1, 2), (-1, 2)))
    geom = {
        hull.name: [["b", -1, -0.5, -2, 1, 3, 2, "box"]],
        "needle": [["b", -0.01, -0.01, -0.01, 0.01, 0.01, 0.01, "box"]]}
    ship = I.mk_solid(hull.name, (0, 27 if sunk else 30, 0), I.yaw(0),
                      geom[hull.name])
    obstacle = I.mk_solid("needle", (0, 34 if above or sunk else 32.75, 0),
                          I.yaw(0), geom["needle"])
    ground = {I.gpoint(x, z)[0]: 27.0 for x, z in H.footprint(hull, 0, 0, 0)}
    ok, evidence = ship_clearance(ship, hull, ground, [ship, obstacle], geom, (0, 0))
    return _finding(ok, "placed hull vertical clearance", (0, 0),
                    check="boat_clearance", **evidence)


def _departure_case(small=False, missing=False, vanilla=False):
    from . import harbours as H
    I._worldseed()
    from settlements import waterfront as WF
    name = "LittleBoat" if small else "VikingShip" if vanilla else "MercantShip"
    spec = WF.VESSELS[name]
    hull = H.hull_table()[name]
    geom = {name: [["b", -spec["hull_w"]/2, -0.5, -spec["hull_l"]/2,
                    spec["hull_w"]/2, 0.5, spec["hull_l"]/2, "box"]]}
    ship = I.mk_solid(name, (0, 30, 0), I.yaw(0), geom[name])
    ground = {I.gpoint(x, z)[0]: 20 for x, z in H.footprint(hull, 0, 0, 0)}
    depth = min(I.WATER_LEVEL-y for y in ground.values())
    upright = _ship({"prefab": name, "pos": ship.pos}, [ship], set())
    floats = upright is not None and depth >= hull.required_depth_m
    # Provider/class guard in isolation: do not invent an Odin air-draft
    # measurement merely to make the separate full clearance predicate pass.
    assert floats
    ok = is_large_departure(None if missing else upright, floats)
    return _finding(ok, "upright floating ship's Odin provider and large-class guard", (0, 0),
                    accepted_large_ships=int(ok), vessel=name, depth_m=depth,
                    required_depth_m=hull.required_depth_m, check="departure_ship")


def _contiguous_case():
    def contiguous(doc):
        doc["transport"] = []
        doc["terminals"].pop("north-dock-000")
    return _schema_case(contiguous)


def _passage_case(shallow=False, unread=False, blocked=False, missing_route=False,
                  thin=False, between_poses=False, swept_shoal=False,
                  turning_between_poses=False, wrong_declared_type=False):
    from . import harbours as H
    hull = H.hull_table()["Karve"]
    hw, hl = hull.beam_m/2, hull.length_m/2
    geom = {hull.name: [["b", -hw, -1, -hl, hw, 1, hl, "box"]],
            "pile": [["b", -0.2, -1, -0.2, 0.2, 1, 0.2, "box"]]}
    geom["needle"] = [["b", -0.001, -1, -0.001, 0.001, 1, 0.001, "box"]]
    a = I.mk_solid(hull.name, (0, 30, 4), I.yaw(0), geom[hull.name])
    b = I.mk_solid(hull.name, (20, 30, 4), I.yaw(0), geom[hull.name])
    actual = [a, b]
    if blocked:
        actual.append(I.mk_solid("pile", (10, 29.5, 4), I.yaw(0), geom["pile"]))
    if thin:
        actual.append(I.mk_solid("pile", (10.5, 29.5, 4.5), I.yaw(37), geom["pile"]))
    if between_poses:
        actual.append(I.mk_solid("needle", (10.5, 30, 4+hw-0.001), I.yaw(0),
                                  geom["needle"]))
    if turning_between_poses:
        angle = math.radians(2.5)
        # The farthest MEASURED corner sweeps an arc outside the endpoint
        # polygons. An ellipse's algebraic tip may not belong to this hull.
        x, z = max(hull.profile, key=lambda p: math.hypot(*p))
        x, z = x*(1.0-1e-7), z*(1.0-1e-7)
        actual.append(I.mk_solid(
            "needle", (x*math.cos(angle)+z*math.sin(angle), 30,
                       4-x*math.sin(angle)+z*math.cos(angle)),
            I.yaw(0), geom["needle"]))
    if swept_shoal:
        swept_z = 5-hw+0.001
        a = I.mk_solid(hull.name, (-0.5, 30, swept_z), I.yaw(0), geom[hull.name])
        b = I.mk_solid(hull.name, (19.5, 30, swept_z), I.yaw(0), geom[hull.name])
        actual = [a, b]
    rows = [(s.name, *s.pos, *s.quat) for s in actual]
    ctx = SimpleNamespace(
        geom=geom, walker=(0.4, 1.8), census=I.Census(rows, 0.4, 1.8, 0),
        ground=lambda points: {} if unread else {
            label: 29.5 if shallow or (swept_shoal and _x == 10 and _z == 5)
            else 27 for label, _x, _z in points})
    doc = _itinerary()
    for terminal in doc["terminals"].values():
        terminal["vessel"] = hull.name
    if wrong_declared_type:
        doc["terminals"]["north-dock-001"]["vessel"] = "LittleBoat"
    if missing_route:
        doc["transport"][0].pop("sea_route")
    if not missing_route:
        doc["transport"][0]["sea_route"] = (
            [[0.5, swept_z], [18.5, swept_z]] if swept_shoal
            else [[1, 4], [19, 4]])
    findings, _verified = measure_saved_passages(
        ctx, doc, {"north-dock-000": [a], "north-dock-001": [b]},
        {"north-dock-000": True, "north-dock-001": True}, {hull.name: hull})
    return findings


def _journey_link_case(missing_root=False, rejected=False):
    doc = _itinerary()
    result = SectionResult(
        findings=[] if not rejected else [Finding(
            check="voyage", ok=False, message="synthetic shallow passage",
            numbers={"depth_m": 0.5})],
        report={"verified_pairs": [] if rejected else [
            {"id": "sail-0", "a": "north-dock-000", "b": "north-dock-001",
             "sailing_m": 20}],
            "terminals": {tid: {"dock_walk_m": 2} for tid in doc["terminals"]}})
    ctx = SimpleNamespace(_north_itinerary_read=(doc, []),
                          _north_transport_gate_result=result)
    positions = {"a": (0.0, 2.0), "b": (20.0, 2.0)}
    segment_of = {"a": "N0-a-b", "b": "N1-c-d"}
    if missing_root:
        positions["b"] = (20.0, 3.0)
    links, findings, _report = road_transport_links(ctx, positions, segment_of)
    # The voyage must not manufacture missing road roots or appear after a
    # rejected physical reading. This is the exact helper roads.run consumes.
    if not missing_root and not rejected:
        assert len(links) == 1 and links[0]["sailing_m"] == 20
    else:
        assert not links
    return findings


def _natural_shore_link_case(rejected=False, missing_source=False):
    doc = _itinerary()
    far = doc["terminals"]["north-dock-001"]
    far.update(segment_id=None, natural_ground=True)
    result = SectionResult(findings=[] if not rejected else [
        Finding(check="voyage", ok=False, message="constructed rejected physical pair")],
        report={"verified_pairs": [] if rejected else [
            dict(id="sail-0", a="north-dock-000", b="north-dock-001", sailing_m=20)],
            "terminals": {tid: {"dock_walk_m": 2} for tid in doc["terminals"]}})
    ctx = SimpleNamespace(_north_itinerary_read=(doc, []),
        _north_transport_gate_result=result, _transport_terminal_contract=doc["terminals"])
    positions = {} if missing_source else {"a": (0.0, 2.0)}
    original = dict(positions)
    links, findings, _ = road_transport_links(ctx, positions, {"a": "N0-a-b"})
    assert positions == original, "journey extension mutated the walking graph"
    if rejected or missing_source:
        assert not links
    else:
        assert len(links) == 1 and links[0]["ka"] == "a"
        assert links[0]["kb"] == "shore:north-dock-001"
        assert links[0]["endpoint_positions"] == {"shore:north-dock-001": I.snap(*far["xz"])}
    return findings


def _connection_case(unbuilt_parent=False, missing_harbour=False):
    from . import roads
    doc = {"required_connections": [
        {"id": "vestvik_to_network", "a": "portalhub", "b": "vestvik"},
        {"id": "vestvik_to_harbour", "a": "vestvik", "b": "dockshore"}]}
    keys = {"portalhub": "root", "vestvik": "town", "dockshore": "harbour"}
    reached = {"root": 0.0, "harbour": 30.0}
    if not unbuilt_parent:
        reached["town"] = 20.0
    if missing_harbour:
        keys.pop("dockshore")
    return roads.required_connection_findings(doc, keys, reached)


def _dock_support_case(lowered=False, unread=False):
    from . import structures as S
    geom = {"wood_pole_log_4": [["b", -0.2, 0, -0.2, 0.2, 4, 0.2, "box"]],
            "deck": [["b", -1, -0.1, -1, 1, 0, 1, "box"]]}
    # Same saved poses in both states; ONLY the frozen foundation changes.
    dock = [I.mk_solid("wood_pole_log_4", (0, 26, 0), I.yaw(0), geom["wood_pole_log_4"]),
            I.mk_solid("deck", (0, 30, 0), I.yaw(0), geom["deck"])]
    ground = {I.gpoint(x, z)[0]: 24.0 if lowered else 27.0
              for s in dock for x, z in S.piece_columns(s)}
    if unread:
        ground.pop(I.gpoint(0, 0)[0])
    ok, evidence = dock_support(dock, ground, geom)
    return _finding(ok, "saved dock foundation with unchanged piece poses",
                    (0, 0), check="dock_support", **evidence)


def _shore_road_case(missing=None, wrong_root=False, provisional=False):
    sites = [dict(node="dock-stathub", id="dock-stationhub", root=[10, 0]),
             dict(node="ferry-terminal-eastisle", id="ferry-terminal-eastisle", root=[110, 0])]
    rows = [dict(id="S13-stathub-dock", nodes=[[0, 0], [10, 0]]),
            dict(id="F1-ferry-eastisle-inland", nodes=[[100, 0], [110, 0]])]
    built = {"road_"+row["id"].replace("-", "_"): dict(nodes=row["nodes"])
             for row in rows}
    if missing is not None:
        built.pop("road_"+rows[missing]["id"].replace("-", "_"))
    if wrong_root:
        built["road_F1_ferry_eastisle_inland"] = dict(nodes=[[100, 0], [109, 0]])
    return shore_road_population(sites, dict(segments=rows, provisional=provisional), built)


CASES = [
    Case("ordinary shore requires its actually built serving road", True, lambda: _shore_road_case(missing=0)),
    Case("far ferry requires its generated inward road", True, lambda: _shore_road_case(missing=1)),
    Case("far inward road must meet the exact published dock root", True, lambda: _shore_road_case(wrong_root=True)),
    Case("provisional shore roads cannot close saved acceptance", True, lambda: _shore_road_case(provisional=True)),
    Case("both exact actual shore serving roads close population", False, _shore_road_case),
    Case("saved mast above the water-relative estimate still collides", True,
         _placed_height_case),
    Case("obstacle above the actual saved mast remains clear", False,
         lambda: _placed_height_case(above=True)),
    Case("saved keel below the measured bed is not floating", True,
         lambda: _placed_height_case(sunk=True)),
    Case("actual saved dock has grounded pile foundations", False, _dock_support_case),
    Case("lowered frozen foundation refuses unchanged dock poses", True,
         lambda: _dock_support_case(lowered=True)),
    Case("unread dock support footprint fails closed", True,
         lambda: _dock_support_case(unread=True)),
    Case("thin rotated pile between integer vertices blocks berth", True,
         lambda: _clearance_case(thin=True)),
    Case("thin rotated pile between integer vertices blocks voyage", True,
         lambda: _passage_case(thin=True)),
    Case("thin pile struck between clear sampled poses blocks voyage", True,
         lambda: _passage_case(between_poses=True)),
    Case("integer shoal crossed only between sampled poses blocks voyage", True,
         lambda: _passage_case(swept_shoal=True)),
    Case("thin pile struck between clear sampled turn angles blocks voyage", True,
         lambda: _passage_case(turning_between_poses=True)),
    Case("physical ferry boats must match their declared measured type", True,
         lambda: _passage_case(wrong_declared_type=True)),
    Case("generated town and real harbour retain physical network duties", False, _connection_case),
    Case("named junction on an unbuilt parent is not a generated connection", True,
         lambda: _connection_case(unbuilt_parent=True),
         "A standing N0b coordinate/name could previously claim a junction when its parent road never stood."),
    Case("removing standing R4 cannot erase the real harbour obligation", True,
         lambda: _connection_case(missing_harbour=True)),
    Case("full north extent reaches measured terrain frontier", False,
         lambda: _finding(extent_verdict(990, 1000, 32, relocation_m=144)[0],
                          "physical endpoint near actual frontier", check="north_extent")),
    Case("complete dock props on truncated north road refuse", True,
         lambda: _finding(extent_verdict(500, 1000, 32, relocation_m=144)[0],
                          "short route cannot redefine required frontier", check="north_extent")),
    Case("unread frontier never becomes zero required northing", True,
         lambda: _finding(extent_verdict(990, float("nan"), 32, relocation_m=144)[0],
                          "unread actual frontier", check="north_extent")),
    Case("complete arbitrary-seed itinerary", False, _schema_case),
    Case("full saved sea passage connects physical endpoints", False, _passage_case),
    Case("shallow sea between deep berths refuses passage", True, lambda: _passage_case(shallow=True),
         "Independent good berths are not evidence of a navigable route between them."),
    Case("unread sea passage is never a voyage edge", True, lambda: _passage_case(unread=True)),
    Case("saved underwater pile blocks an otherwise deep passage", True, lambda: _passage_case(blocked=True)),
    Case("missing sea route has no straight-line fallback", True, lambda: _passage_case(missing_route=True)),
    Case("accepted voyage binds exact physical road roots", False, _journey_link_case),
    Case("voyage cannot manufacture a missing road root", True, lambda: _journey_link_case(missing_root=True)),
    Case("rejected physical passage never becomes a road journey edge", True, lambda: _journey_link_case(rejected=True)),
    Case("saved accepted far natural shoreline is a journey-only destination", False, _natural_shore_link_case),
    Case("unaccepted natural shoreline creates no voyage destination", True, lambda: _natural_shore_link_case(rejected=True)),
    Case("natural far bank cannot manufacture its source road", True, lambda: _natural_shore_link_case(missing_source=True)),
    Case("contiguous land route needs only final departure dock", False, _contiguous_case),
    Case("missing north artifact is a hard refusal", True, lambda: _artifact_case("missing")),
    Case("unreadable north artifact is a hard refusal", True, lambda: _artifact_case("unreadable")),
    Case("foreign seed and world artifact is a hard refusal", True, lambda: _artifact_case("foreign")),
    Case("physical upright ship is observed", False, _ship_case),
    Case("ship spawn intent with no saved ship fails", True, lambda: _ship_case(missing=True)),
    Case("capsized saved ship fails", True, lambda: _ship_case(inverted=True)),
    Case("one saved ship cannot serve both endpoints", True, lambda: _ship_case(reuse=True)),
    Case("actual hull floats beside boarding head", False, _clearance_case),
    Case("actual saved hull grounded on shallow bed fails", True, lambda: _clearance_case(shallow=True)),
    Case("unread applied hull footprint fails", True, lambda: _clearance_case(unread=True)),
    Case("actual pile through saved hull fails", True, lambda: _clearance_case(blocked=True)),
    Case("final actual measured large Odin vessel accepted", False, _departure_case),
    Case("floating upright vanilla large vessel cannot satisfy Odin departure", True,
         lambda: _departure_case(vanilla=True)),
    Case("floating upright small Odin boat cannot satisfy final departure", True,
         lambda: _departure_case(small=True)),
    Case("final departure ledger without physical ship fails", True, lambda: _departure_case(missing=True)),
    Case("provisional north plan is not accepted", True, lambda: _schema_case(lambda d: d.update(provisional=True))),
    Case("absent provisional declaration fails closed", True, lambda: _schema_case(lambda d: d.pop("provisional"))),
    Case("unresolved base survey debt is not final-world acceptance", True,
         lambda: _schema_case(lambda d: d.update(
             base_route_refusals=[{"id": "base", "kind": "writer"}]))),
    Case("unresolved north road debt is not final-world acceptance", True,
         lambda: _schema_case(lambda d: d.update(
             route_refusals=[{"id": "N0-a-b", "kind": "joint"}]))),
    Case("empty north cannot hide behind core roads", True, lambda: _schema_case(lambda d: d.update(road_edges=[]))),
    Case("terminal must be exact serving road endpoint", True, lambda: _schema_case(lambda d: d["terminals"]["north-dock-000"].update(xz=[0, 1]))),
    Case("unpaired endpoint is not a crossing", True, lambda: _schema_case(lambda d: d.update(transport=[]))),
    Case("departure flag cannot disappear", True, lambda: _schema_case(lambda d: d["terminals"]["north-dock-001"].update(final_departure=False))),
    Case("both actual endpoints are present", False, lambda: _pair_case(True)),
    Case("one real dock and boat do not make a pair", True, lambda: _pair_case(False), "A one-sided ferry declaration cannot substitute for paired physical docks and boats."),
    Case("all north roads join ordinary gate population", False, _routes_case),
    Case("unbuilt north road cannot vanish from ledger population", True, lambda: _routes_case(True)),
    Case("saved dock pieces at required poses", False, _pieces_case),
    Case("intent with no saved dock pieces", True, lambda: _pieces_case(missing=True)),
    Case("upside-down saved dock is not its planned pose", True, lambda: _pieces_case(inverted=True)),
    Case("one saved tile cannot satisfy two required tiles", True, lambda: _pieces_case(duplicate=True)),
    Case("saved deck reaches the boarding head", False, _walk_case),
    Case("sea floor under a dock hole is not walking access", True, lambda: _walk_case(hole=True), "A declared dock with a missing middle deck must not pass on its sea floor."),
    Case("saved wall blocks road-to-dock walking access", True, lambda: _walk_case(wall=True)),
    Case("connected grounded tall body stands", False, _tower_case),
    Case("complete tower floating over applied ground", True, lambda: _tower_case(floating=True), "The existing gate's watchtower defect: intended datum agrees while every actual support stands in air."),
    Case("short prop cannot satisfy tall lighthouse", True, lambda: _tower_case(short=True)),
]

register(Section(
    name="north_transport",
    measures="physical walking access, both saved docks and boats, full-hull sea passages and multimodal north reachability, with a grounded tall lighthouse and measured large Odin departure ship",
    instrument="the frozen-save ZDO census, measured collider geometry, applied ground and shared capsule/harbour/structure predicates",
    population="every road and terminal in the mandatory seed/world north itinerary, including both endpoints and the unique final departure",
    run=run, cases=CASES))
