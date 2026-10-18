#!/usr/bin/env python3
"""Seed-derived waterfront requirements, sharing road and full-hull predicates.

Install as roads/shores.py. No coordinates, fixture fields, applied-world ledger
or default-seed exception enter this planner. A failed requirement is a refusal,
not a portal substitution or permission to reuse a historical terminal.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
for directory in (HERE.parent, HERE.parent / "blueprints",
                  HERE.parent / "crossings", HERE.parent / "settlements", HERE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
import worldseed
import geography
import field as F
import water as W
import assemble as A
from settlements import waterfront as WF

from shore_scope import SEARCH_RADIUS_M, FOOTPRINT_MARGIN_M, request_windows
DECK_Y = geography.DECK_DATUM_Y
VESSEL = "VikingShip"
# Authored identities and travel needs, never authored positions.
REQUIREMENTS = (
    dict(node="harbour-south", id="harbour-temple-south", anchor="temple",
         kind="harbour", role="harbour_place", cls="trunk", width_tiles=3,
         south=True),
    dict(node="boathouse-strait", id="boathouse-temple-strait", anchor="brg-s1-e",
         kind="slip", role="boathouse_place", cls="spur", width_tiles=2),
    dict(node="dock-stathub", id="dock-stationhub", anchor="stathub",
         kind="jetty", role="dock_place", cls="spur", width_tiles=2),
    dict(node="ferry-east", id="ferry-terminal-east", anchor="eastlobe",
         kind="jetty", role="ferry_terminal_place", cls="spur", width_tiles=2),
    dict(node="harbour-vestvik", id="harbour-vestvik", anchor="vestvik",
         kind="jetty", role="harbour_place", cls="trunk", width_tiles=3),
    dict(node="dock-dockshore", id="dock-dockshore", anchor="dockshore",
         kind="jetty", role="dock_place", cls="trunk", width_tiles=3),
    dict(node="harbour-stenvik-port", id="harbour-stenvik-port", anchor="stenvik",
         kind="jetty", role="harbour_place", cls="trunk", width_tiles=3),
    dict(node="dock-northcape", id="dock-northcape", anchor="northcape",
         kind="jetty", role="dock_place", cls="trunk", width_tiles=3,
         large_ship=True),
)


FERRY_REQUIREMENT = dict(id="ferry-east-isle", a="ferry-terminal-east",
    b="ferry-terminal-eastisle", status="required",
    reason="paired dock+boat travel to a measured disconnected major eastern landcomponent")

_PREPARED_SCOPE = None
_PREPARED_SITES = ()



class GeneratedSurface(WF.Surface):
    """Only the explicitly scoped field, with the shared rendered-height sampler."""
    def __init__(self, fields):
        from types import SimpleNamespace
        self.fields = fields
        self.field_names = ["this seed's explicit measured shore field"]
        self.applied = SimpleNamespace(delta_at=lambda x, z: 0.0)
        self._masks = {}

    def hull_depths(self, bearing, x, z, vessel):
        from types import SimpleNamespace
        patch = self.patch_for(x, z, margin=WF.VESSELS[vessel]["hull_l"])
        key = (patch.id, vessel)
        if key not in self._masks:
            self._masks[key] = WF.sea_mask(
                patch, WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M)[1]
        return WF.hull_depths(SimpleNamespace(patch_for=lambda *a, **k: patch),
                              bearing, x, z, vessel, open_sea=self._masks[key])


def load_fields(path=None):
    import yaml
    if path is None:
        source = geography.geography_path()
        doc = yaml.safe_load(source.read_text()) or {}
        worldseed.require_scope("shore geography", source, found_seed=doc.get("seed"),
                                found_world=doc.get("world"))
        path = (doc.get("provenance") or {}).get("height_field")
    if not path:
        raise SystemExit("REFUSING shore solve: geography must name its measured height_field; re-emit geography from this seed's requested PatchScan")
    F.require_seed(path, worldseed.seed())
    import hashlib
    import json
    source = Path(path).with_suffix(Path(path).suffix + ".source.json")
    if not source.is_file():
        raise SystemExit(f"REFUSING shore field: missing {source}; rerun roads.field.request for rendered Heightmap measurements")
    manifest = json.loads(source.read_text())
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if (manifest.get("schema_version") != 1 or manifest.get("seed") != worldseed.seed()
            or manifest.get("surface") != "heightmap"
            or manifest.get("lattice") != "PatchScan"
            or manifest.get("sha256") != digest):
        raise SystemExit(f"REFUSING shore field: {source} does not bind this seed's measured rendered Heightmap bytes; raw coarse/unaligned geometry is not authority")
    fields = W.load(path)
    bad = [fid for fid, f in fields.items() if abs(f.step - 1.0) > 1e-6
           or any(abs(v - round(v)) > 1e-6 for v in (f.x0, f.z0))]
    # Never select an overview as final geometry, even when its bbox contains us.
    fine = {fid: f for fid, f in fields.items() if fid not in bad}
    if not fine:
        raise SystemExit(f"REFUSING shore solve: {path} has no aligned 1m patches; rejected {bad}")
    return fine


def publication_path():
    return worldseed.waterfront_path()


def semantic_nodes():
    """Read existing producer publications without importing the road spec."""
    import yaml
    nodes = geography.load()
    import installation_sites
    installations = installation_sites.installation_xz(
        seed=worldseed.seed(), world=worldseed.world())
    for nid in ("stathub", "dockshore"):
        nodes[nid] = dict(xz=installations[nid],
                         patch=worldseed.TRUNK_PRIMARY, island="spawn")
    path = worldseed.sites_path()
    doc = yaml.safe_load(path.read_text()) or {}
    worldseed.require_scope("shore settlement sites", path,
        found_seed=doc.get("seed"), found_world=doc.get("world"))
    for site in doc.get("sites") or []:
        nid = site.get("id", "").replace("-", "")
        if nid not in ("vestvik", "stenvik"):
            continue
        road = site.get("road") or {}
        if not road.get("roaded") or not road.get("topology_window"):
            raise SystemExit(f"REFUSING generated harbour: {nid} village has no road topology; its waterfront requirement remains unmet")
        nodes[nid] = dict(xz=tuple(site["xz"]),
            patch=road["topology_window"], island=road["evidence"]["island"])
    return nodes


def produce(field_path):
    """Stage-9 pre-import producer, after actual stage-7/8 semantic placements."""
    global _PREPARED_SCOPE, _PREPARED_SITES
    import hashlib
    import yaml
    import json
    fields = load_fields(field_path)
    nodes = semantic_nodes()
    manifest = json.loads(Path(field_path).with_suffix(Path(field_path).suffix + ".source.json").read_text())
    if manifest.get("ferry_search_policy") != "east-major-components-rendered-complete-v2" or not manifest.get("coarse_grid"):
        raise SystemExit("REFUSING shore production: field request lacks its complete major-east-component ferry search scope; rerun the canonical field request")
    grid_path = Path(manifest["coarse_grid"])
    with grid_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest.get("coarse_grid_sha256"):
            raise SystemExit("REFUSING shore production: coarse request grid changed since this fine scan; rerun field.request_for_seed so every alternative is measured")
    grid = F._site_finder().load_grid(grid_path)
    worldseed.require_scope("shore coarse request grid", grid_path, found_seed=grid.get("seed"))
    from shore_connectivity import CompleteLand
    complete = CompleteLand.from_fields(fields, manifest)
    sites = derive(nodes, fields, ferry_search=manifest.get("ferry_search"), complete=complete)
    with Path(field_path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    doc = dict(schema_version=1, seed=worldseed.seed(), world=worldseed.world(),
        derived_by="roads.shores.solve_site", field=str(Path(field_path).resolve()),
        field_sha256=digest, semantic_anchors={k: dict(xz=list(v["xz"]),
            patch=v["patch"], island=v["island"]) for k, v in nodes.items()
            if k in {r["anchor"] for r in REQUIREMENTS}}, sites=sites)
    path = publication_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    _PREPARED_SCOPE = worldseed.seed(), worldseed.world()
    _PREPARED_SITES = tuple(sites)
    return doc


def load(*, required=True):
    """Hydrate only; no terrain scan/solve and no default-seed authority."""
    global _PREPARED_SCOPE, _PREPARED_SITES
    if not required:
        # Initial numeric imports acquire no new publication prerequisites and
        # cannot trip on an obsolete prior output before the producer can run.
        return list(_PREPARED_SITES) if _PREPARED_SCOPE == (
            worldseed.seed(), worldseed.world()) else []
    import hashlib
    import yaml
    path = publication_path()
    if not path.is_file():
        raise SystemExit(f"REFUSING: missing {path}; run roads/north.py network with its ordinary --field after placements/sites; no frozen waterfront fallback")
    doc = yaml.safe_load(path.read_text()) or {}
    worldseed.require_scope("derived waterfront", path,
        found_seed=doc.get("seed"), found_world=doc.get("world"))
    if doc.get("schema_version") != 1 or doc.get("derived_by") != "roads.shores.solve_site":
        raise SystemExit(f"REFUSING {path}: unsupported waterfront producer/schema")
    source = Path(doc.get("field") or "")
    F.require_seed(source, worldseed.seed())
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != doc.get("field_sha256"):
        raise SystemExit(f"REFUSING {path}: measured field changed; rerun the shore producer")
    nodes = semantic_nodes()
    expected = {k: dict(xz=list(v["xz"]), patch=v["patch"], island=v["island"])
        for k, v in nodes.items() if k in {r["anchor"] for r in REQUIREMENTS}}
    if doc.get("semantic_anchors") != expected:
        raise SystemExit(f"REFUSING {path}: generated semantic anchors changed; rerun shore producer")
    sites = doc.get("sites") or []
    if len(sites) != len(REQUIREMENTS) + 1 or {s.get("node") for s in sites} != ({r["node"] for r in REQUIREMENTS} | {"ferry-terminal-eastisle"}):
        raise SystemExit(f"REFUSING {path}: missing, duplicate or extra named waterfront requirements")
    require_pairs(sites)
    if any(not site.get("rail_from") or site.get("rail_from") not in site.get("rail_post_prefabs", [])
           for site in sites):
        raise SystemExit(f"REFUSING {path}: missing actual physical-plan railpost prefab; rerun shore producer")
    _PREPARED_SCOPE = worldseed.seed(), worldseed.world()
    _PREPARED_SITES = tuple(sites)
    return sites


def approach(surf, root, bearing, width_m, *, length_m=6.0):
    """The existing straight reciprocal road approach, on measured dry ground.

    The first metre meets the deck within the player's 0.35m step limit; the
    following cross-sections must admit the road's 25% construction grade.
    Final road planning still runs its full profile, ownership and write gates.
    """
    ux, uz = A._unit(bearing)
    lx, lz = uz, -ux
    previous = DECK_Y
    for along in np.arange(0.0, length_m + 0.5, 1.0):
        hs = [surf.at(root[0] - ux * along + lx * across,
                      root[1] - uz * along + lz * across)[0]
              for across in np.arange(-width_m / 2, width_m / 2 + 0.5, 1.0)]
        if not all(math.isfinite(h) and h > W.WATER_LEVEL for h in hs):
            return False
        # A level ribbon must meet every sample, not only its centreline.
        if max(hs) - min(hs) > 0.7:
            return False
        height = sum(hs) / len(hs)
        if abs(height - previous) > (0.35 if along == 0 else 0.25):
            return False
        previous = height
    return True


def _coverage(fld, anchor, radius=SEARCH_RADIUS_M):
    if not fld.contains(*anchor, margin=radius + FOOTPRINT_MARGIN_M):
        raise SystemExit(f"REFUSING shore search at {anchor}: {fld.id} does not cover the complete {radius:g}m search plus {FOOTPRINT_MARGIN_M:g}m hull/approach envelope; rerun field.request_for_seed with shores.request_windows, not a clipped search")


def _shore_candidates(fld, anchor, *, south=False, component=None, component_mask=None):
    _coverage(fld, anchor)
    if component_mask is None:
        land = fld.heights > W.WATER_LEVEL
        labels, _ = ndimage.label(land, structure=ndimage.generate_binary_structure(2, 1))
        if component is None:
            i, j = fld.index(*anchor)
            component = int(labels[i, j])
            if not component:
                raise SystemExit(f"REFUSING shore solve: semantic anchor {anchor} has no dry land component on {fld.id}")
        mask = labels == component
    else:
        if component_mask.shape != fld.heights.shape:
            raise SystemExit("REFUSING shore component mask on a different measured lattice")
        labels = mask = component_mask
    shore = mask & ndimage.binary_dilation(
        fld.heights < W.WATER_LEVEL, structure=ndimage.generate_binary_structure(2, 1))
    ii, jj = np.nonzero(shore)
    xs, zs = fld.world_x(jj), fld.world_z(ii)
    distances = np.hypot(xs - anchor[0], zs - anchor[1])
    keep = distances <= SEARCH_RADIUS_M
    if south:
        keep &= zs < anchor[1]
    order = np.lexsort((xs[keep], zs[keep], distances[keep]))
    return [(float(xs[keep][k]), float(zs[keep][k])) for k in order], labels


def _boat_pose(root, bearing, length, kind, width_m, vessel=VESSEL):
    vf = WF.VESSELS[vessel]
    ux, uz = A._unit(bearing)
    lx, lz = uz, -ux
    along = length * 0.6
    off = 0.0 if kind == "slip" else -(width_m / 2 + vf["hull_w"] / 2 + 0.75)
    return (float(A.fmt(root[0] + ux * along + lx * off)),
            float(A.fmt(root[1] + uz * along + lz * off)))


def emitted_pieces(pieces):
    """Support is evaluated on the exact native command coordinates, not floats
    the command formatter will subsequently move."""
    from dataclasses import replace
    return [replace(p, x=float(A.fmt(p.x)), y=float(A.fmt(p.y)),
                    z=float(A.fmt(p.z)), yaw=float(A.fmt(p.yaw)))
            for p in pieces]


def occupied_bounds(pieces, boat):
    """Measured body, boarding-space and rotated full-hull service envelope."""
    boxes = [piece.aabb() for piece in pieces]
    xs = [v[axis][0] for v in boxes for axis in (0, 1)]
    zs = [v[axis][2] for v in boxes for axis in (0, 1)]
    vf = WF.VESSELS[boat["prefab"]]
    ux, uz = A._unit(boat["yaw"])
    lx, lz = uz, -ux
    for along in (-vf["hull_l"]/2, vf["hull_l"]/2):
        for across in (-vf["hull_w"]/2, vf["hull_w"]/2):
            xs.append(boat["x"] + ux*along + lx*across)
            zs.append(boat["z"] + uz*along + lz*across)
    return [min(xs), max(xs), min(zs), max(zs)]


def rail_metadata(pieces):
    prefabs = sorted({p.prefab for p in pieces if p.role.endswith("/rail_post")})
    if len(prefabs) != 1:
        raise SystemExit(f"REFUSING generated rail contract: expected one actual physical railpost prefab, found {prefabs}")
    return dict(rail_from=prefabs[0], rail_post_prefabs=prefabs)


def clears_sites(candidate, sites):
    a = candidate["occupied_bounds"]
    for site in sites:
        b = site["occupied_bounds"]
        if min(a[1], b[1]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[2], b[2]):
            return False
    return True


def road_preview(graph, surf, node, root, bearing):
    """Use the actual router and exact interval profile before accepting a shore.

    End runs are pinned, including an S1 source's real level_y. Overlapping or
    too-short ramps cannot hide a drop by changing the last vertex afterward.
    This is planning feasibility, not permission to skip the final writer gates.
    """
    import route
    ux, uz = A._unit(bearing)
    entry = (root[0] - ux * 6.0, root[1] - uz * 6.0)
    graph.corr.forbid = ~graph.ok
    try:
        path = graph.corr.search(tuple(node["xz"]), entry)
        if not path:
            return None
        raw = [tuple(node["xz"]), *path, entry, tuple(root)]
        clean = [raw[0]]
        for point in raw[1:]:
            if math.dist(point, clean[-1]) > 1e-6:
                clean.append(point)
        points, arcs = route.resample(clean, spacing_m=1.0)
        terrain = np.array([surf.at(*p)[0] for p in points])
        s = np.asarray(arcs)
        lo, hi = terrain - route.CUT_FILL_MAX, terrain + route.CUT_FILL_MAX
        target = s >= s[-1] - 6.0 - 1e-6
        source = s <= 6.0 + 1e-6
        level = node.get("level_y")
        if level is not None and np.any(source & target) and abs(level-DECK_Y) > 1e-6:
            return None
        if np.any((DECK_Y < lo[target]) | (DECK_Y > hi[target])):
            return None
        if level is not None and np.any((level < lo[source]) | (level > hi[source])):
            return None
        lo[target] = hi[target] = DECK_Y
        if level is not None:
            lo[source] = hi[source] = float(level)
        fit = route.fit_profile(terrain, s, grade_max=route.GRADE_CEILING, lo=lo, hi=hi)
        if fit["infeasible_spans"]:
            return None
        return dict(length_m=float(s[-1]), grade_max=fit["grade_max"],
                    source_level_y=level, arrival_level_y=DECK_Y,
                    stations=len(points))
    except (ValueError, IndexError):
        return None


def solve_site(requirement, nodes, fields, *, graph_factory=None, surface=None,
               component=None, anchor_override=None, accept=None, complete=None):
    """Search ALL measured same-component shoreline samples, not a pinned seed."""
    if requirement.get("large_ship") and not requirement.get("vessel"):
        import north_transport
        failures = []
        for vessel in north_transport.vessel_candidates(True):
            try:
                return solve_site(dict(requirement, vessel=vessel), nodes, fields,
                    graph_factory=graph_factory, surface=surface, component=component,
                    anchor_override=anchor_override, accept=accept, complete=complete)
            except SystemExit as exc:
                failures.append(dict(vessel=vessel, reason=str(exc)))
        raise SystemExit(f"REFUSING {requirement['id']}: every measured large Odin vessel/shore alternative failed: {failures}")
    anchor_id = requirement["anchor"]
    if anchor_override is None and anchor_id not in nodes:
        raise SystemExit(f"REFUSING {requirement['id']}: required generated semantic anchor {anchor_id} is absent; its travel/site requirement remains unmet")
    node = nodes[anchor_id]
    anchor = tuple(anchor_override or node["xz"])
    core = worldseed.resolve(fields, node["patch"])
    survey_id = requirement.get("survey_patch") or "shore-" + worldseed.FUNCTION_OF.get(core.id, core.id)
    if survey_id not in fields:
        raise SystemExit(f"REFUSING {requirement['id']}: missing measured {survey_id}; rerun field.request_for_seed with shore_scope.request_windows")
    fld = fields[survey_id]
    search_center = tuple(requirement.get("search_center", anchor))
    component_mask = None
    if complete is not None:
        component = complete.at(*anchor) if component is None else component
        if component == 0:
            raise SystemExit(f"REFUSING {requirement['id']}: semantic anchor has no component on complete rendered world")
        component_mask = complete.labels_for(fld) == component
    candidates, _ = _shore_candidates(fld, search_center,
                                      south=requirement.get("south", False),
                                      component=component, component_mask=component_mask)
    if not candidates:
        raise SystemExit(f"REFUSING {requirement['id']}: no measured shoreline on the required land component within {SEARCH_RADIUS_M:g}m of {anchor_id}; site requirement retained")
    # RoadGraph is the production corridor/POI/grade instrument, not Euclidean
    # proximity pretending that a cliff or a protected location can be paved.
    factory = graph_factory or geography.RoadGraph
    graph = factory(F.Field(fld.id, fld.cx, fld.cz, fld.half, fld.step, fld.n,
                            fld.heights, fld.biomes), cls=requirement["cls"])
    from shore_transport import _source_reach
    surf = surface or GeneratedSurface(fields)
    xs, zs = np.asarray(candidates).T
    reachable = _source_reach(graph, anchor, xs, zs)
    width = requirement["width_tiles"] * 2.0
    vessel = requirement.get("vessel", VESSEL)
    need = WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M
    bed = A.Bed.applied(lambda x, z: surf.at(x, z)[0], "seed-scoped measured generated Heightmap")
    rejected = {"road": 0, "approach": 0, "water": 0, "hull": 0, "support": 0}
    road_cache = {}
    for root, reached in zip(candidates, reachable):
        if not reached:
            rejected["road"] += 1
            continue
        for length in range(24 if requirement["kind"] in ("slip", "harbour") else 20, 98, 2):
            aims = []
            for bearing in range(0, 360, 5):
                ux, uz = A._unit(bearing)
                hs = [surf.at(root[0] + ux * t, root[1] + uz * t)[0]
                      for t in range(length + 1)]
                wet = [h < W.WATER_LEVEL for h in hs]
                if not all(wet[len(wet) // 2:]) or W.WATER_LEVEL - hs[-1] < need:
                    rejected["water"] += 1
                    continue
                # Same wet-fraction/depth score as crossings.plan.aim_seaward,
                # but each shore is tested rather than fixing the nearest one.
                score = sum(wet) / len(wet) * 10 + min(W.WATER_LEVEL - hs[-1], 8)
                aims.append((-score, bearing))
            for _, bearing in sorted(aims):
                if not approach(surf, root, bearing, width):
                    rejected["approach"] += 1
                    continue
                key = root, bearing
                if key not in road_cache:
                    road_cache[key] = road_preview(graph, surf, node, root, bearing)
                if road_cache[key] is None:
                    rejected["road"] += 1
                    continue
                bx, bz = _boat_pose(root, bearing, length, requirement["kind"], width, vessel)
                depths = surf.hull_depths(bearing, bx, bz, vessel)
                if not (depths and depths[2] and depths[3] and depths[0] >= need):
                    rejected["hull"] += 1
                    continue
                inner = WF.VESSELS[vessel]["hull_w"] + 2 * 0.75
                pieces = (A.slip(root, bearing, length, inner, DECK_Y, bed,
                                 name=requirement["id"]) if requirement["kind"] == "slip"
                          else A.jetty(root, bearing, length, DECK_Y, bed,
                                       width_tiles=requirement["width_tiles"], name=requirement["id"]))
                pieces = emitted_pieces(pieces)
                verdict = A.verify(pieces, bed, note=requirement["id"])
                if not verdict["ok"]:
                    rejected["support"] += 1
                    continue
                candidate = dict(requirement, root=list(root), bearing=float(bearing),
                            length_m=float(length), inner_width_m=inner,
                            island=node["island"], patch=fld.id,
                            measurement_patch=fld.id,
                            arrive_bearing_deg=(bearing + 180) % 360,
                            width_m=width, boat=dict(prefab=vessel, x=bx, z=bz,
                                y=W.WATER_LEVEL, yaw=float(bearing),
                                berth_depth_m=depths[0], deep_enough=True,
                                hull_samples=depths[3], connected=True,
                                bed_y=surf.at(bx, bz)[0]),
                            structural=verdict, anchor_xz=list(anchor),
                            why=f"seed-derived {requirement['kind']} serving generated {anchor_id}; measured component, reciprocal road approach, full hull and structural support",
                            sign_text=f"{requirement['id']} - {anchor_id}",
                            aim=dict(root=list(root), bearing=float(bearing),
                                     root_ground_y=surf.at(*root)[0]))
                candidate["road_preview"] = road_cache[key]
                candidate["occupied_bounds"] = occupied_bounds(pieces, candidate["boat"])
                candidate.update(rail_metadata(pieces))
                if complete is not None:
                    candidate["rendered_component"] = int(component)
                    candidate["rendered_identity_patch"] = complete.field.id
                if accept is not None and not accept(candidate):
                    continue
                return candidate
    raise SystemExit(f"REFUSING {requirement['id']}: all {len(candidates)} measured shore candidates fail production road/approach/water/hull/support criteria: {rejected}; required site remains unmet")


def derive(nodes, fields, *, ferry_search, complete):
    from shore_transport import solve_pair
    sites = []
    for requirement in REQUIREMENTS:
        if requirement["node"] == "ferry-east":
            continue
        sites.append(solve_site(requirement, nodes, fields, complete=complete,
            accept=lambda candidate: clears_sites(candidate, sites)))
    sites.extend(solve_pair(nodes, fields, ferry_search, complete, occupied=sites))
    require_pairs(sites)
    return sites


def road_nodes(sites):
    out = {s["node"]: dict(xz=tuple(s["root"]), island=s["island"], patch=s["patch"],
        measurement_patch=s["measurement_patch"],
        kind="waterfront_terminal", owner="Crossings", level_y=DECK_Y,
        arrive_bearing_deg=s["arrive_bearing_deg"], jetty_bearing_deg=s["bearing"],
        ribbon_width_m=s["width_m"], keep_clear=[], provenance=s["why"], why=s["why"])
        for s in sites}
    for site in sites:
        if site.get("access_anchor"):
            anchor = dict(site["access_anchor"])
            nid = anchor.pop("node")
            anchor["xz"] = tuple(anchor["xz"])
            anchor["provenance"] = "measured inland road seat of the paired destination's actual landcomponent"
            out[nid] = anchor
    return out


def require_pairs(sites):
    by_id = {s.get("id"): s for s in sites}
    pair = (by_id.get("ferry-terminal-east") or {}).get("pair_requirement") or {}
    if (pair.get("status") != "measured" or pair.get("a") not in by_id
            or pair.get("b") not in by_id or pair.get("a") == pair.get("b")
            or not pair.get("sea_route") or not pair.get("measurement")):
        raise SystemExit(f"REFUSING waterfront construction: required paired dock+boat transport remains unmet: {pair or FERRY_REQUIREMENT}; no portal-only substitution or one-ended ferry is authorized")


def _resume_regressions(plan):
    """Exercise the REAL native matcher on emitted geometry; fake only live I/O."""
    import copy
    import hashlib
    from types import SimpleNamespace
    import north_transport as NT
    from ledger import replay as R
    rows = [row for op in plan["ops"] for row in R.north_spawn_rows(op["wire"])]
    class Census:
        def __init__(self, rows):
            self.rows = copy.deepcopy(rows)
        def within(self, prefab, x, z, radius):
            return [(i, row) for i, row in enumerate(self.rows)
                    if row["prefab"] == prefab and math.hypot(row["pos"][0]-x, row["pos"][2]-z) <= radius+0.01]
        def count(self, prefab, x, z, radius):
            return len(self.within(prefab, x, z, radius)), "constructed census"
    def detailed(server, prefab, x, y, z, radius, *, strict=False):
        lines = [f"Id: 0:{i+1} -Prefab: {row['prefab']} Position: ({' '.join(map(str, row['pos']))}) Rotation: ({' '.join(map(str, row['rot']))})"
                 for i, row in server.within(prefab, x, z, radius)]
        return "\n".join(lines), 1, []
    def present(server, rec):
        p = rec["params"]
        return bool(server.within(p["prefab"], p["pos"][0], p["pos"][2],
                                  p["guard_radius_m"])), {}
    old = R.bounded_detailed, R.already_present
    R.bounded_detailed, R.already_present = detailed, present
    try:
        server = Census(rows)
        builder = SimpleNamespace(srv=server,
            blob=lambda data, **kwargs: hashlib.sha256(data).hexdigest())
        assert all(not op["params"].get("plan_ref") for op in plan["ops"])
        assert NT.reconciled_operations(builder, plan) == [], "modern standing deck was resent without a legacy op_key"
        server.rows.pop(0)
        pending = NT.reconciled_operations(builder, plan)
        assert len(pending) == 1 and pending[0]["wire"] == [plan["ops"][0]["wire"][0]]
    finally:
        R.bounded_detailed, R.already_present = old


def selftest():
    """Constructed geometry regressions; no save, field file or world execution."""
    class Plane:
        def __init__(self, fn):
            self.fn = fn
        def at(self, x, z):
            h = self.fn(x, z)
            return h, h, 0.0
    assert approach(Plane(lambda x, z: DECK_Y + max(0, -z) * 0.1), (0, 0), 0, 4)
    assert not approach(Plane(lambda x, z: DECK_Y + max(0, -z) * 0.4), (0, 0), 0, 4)
    assert not approach(Plane(lambda x, z: 29 if abs(x) > 1 else DECK_Y), (0, 0), 0, 4)
    assert not approach(Plane(lambda x, z: DECK_Y + 1.336), (0, 0), 0, 4)
    # A complete component-labelled field: the closest opposite-bank shore is
    # not a candidate of the source component, and south means actual south.
    n = 1400
    ax = np.arange(n) - n / 2
    xx, zz = np.meshgrid(ax, ax)
    heights = np.where((xx >= -40) & (xx <= 40) & (abs(zz) < 100), DECK_Y, 20.0)
    heights[(xx >= 50) & (xx <= 60) & (abs(zz) < 100)] = DECK_Y
    fld = W.Field("constructed", -0.5, -0.5, n / 2, 1, n, heights,
                  np.ones((n, n), dtype=np.uint8))
    candidates, _ = _shore_candidates(fld, (0, 0), south=True)
    assert candidates and all(x <= 40 and z < 0 for x, z in candidates)
    try:
        _coverage(fld, (600, 0))
    except SystemExit as exc:
        assert "complete" in str(exc)
    else:
        raise AssertionError("clipped survey accepted")
    try:
        require_pairs([dict(pair_requirement=dict(id="ferry-east-isle", status="unmet"))])
    except SystemExit as exc:
        assert "paired dock+boat" in str(exc)
    else:
        raise AssertionError("unmet named ferry silently removed")
    # Real router, rendered sampler, hull footprint and assembled pile/deck
    # support on a constructed coast. Only the fixture's absent POIs are
    # injected; no fake road/hull/support verdict participates.
    import route
    def fixture_graph(field, cls):
        corr = route.Corridor(field, cell_m=4.0, grade_max=route.GRADE[cls])
        return geography.RoadGraph(field, cls=cls,
            forbid=np.zeros((corr.m, corr.m), dtype=bool))

    def coast(fid, half):
        n = int(half * 2)
        ax = -half + 0.5 + np.arange(n)
        _, zz = np.meshgrid(ax, ax)
        h = np.where(zz <= 0, DECK_Y - zz * 0.1,
                     np.maximum(20.0, DECK_Y - zz * 0.8))
        return W.Field(fid, 0, 0, half, 1, n, h.astype(np.float32),
                       np.ones((n, n), dtype=np.uint8))
    fields = {"trunk-primary": coast("trunk-primary", 200.5),
              "shore-trunk-primary": coast("shore-trunk-primary", 800.5)}
    requirement = dict(next(r for r in REQUIREMENTS if r["node"] == "dock-stathub"))
    nodes = {"stathub": dict(xz=(0, -40), patch="trunk-primary", island="spawn")}
    first = solve_site(requirement, nodes, fields, graph_factory=fixture_graph)
    nodes["stathub"]["xz"] = (100, -40)
    second = solve_site(requirement, nodes, fields, graph_factory=fixture_graph)
    assert first["root"] != second["root"]
    assert first["root"] == [0.0, 0.0] and second["root"] == [100.0, 0.0]
    assert first["structural"]["ok"] and first["boat"]["connected"]
    assert first["boat"]["berth_depth_m"] >= (
        WF.VESSELS[VESSEL]["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M)
    assert not clears_sites(first, [first]) and clears_sites(second, [first])
    from shore_build import plan_site
    planned = plan_site(first, GeneratedSurface(fields))
    assert [op["op"] for op in planned["ops"]] == ["spawn_plan", "spawn"]
    assert planned["pieces"] and planned["walk_xz"]
    assert planned["boats"][0]["prefab"] == first["boat"]["prefab"]
    assert not any(op["op"] == "terrain_write" for op in planned["ops"])
    # Coarse values may nominate survey scope; they cannot become final geometry.
    bad = {"trunk-primary": fields["trunk-primary"]}
    try:
        solve_site(requirement, nodes, bad, graph_factory=fixture_graph)
    except SystemExit as exc:
        assert "missing measured shore-" in str(exc)
    else:
        raise AssertionError("missing fine shore survey accepted")
    original = worldseed.Window("trunk-primary", 0, 0, 200.5, 1, "constructed")
    requests = request_windows((original,))
    assert requests[0] == original and requests[1].fid == "shore-trunk-primary"
    assert requests[1].half - original.half == SEARCH_RADIUS_M + FOOTPRINT_MARGIN_M
    preview_graph = fixture_graph(F.Field(
        fields["trunk-primary"].id, 0, 0, 200.5, 1, 401,
        fields["trunk-primary"].heights, fields["trunk-primary"].biomes), "spur")
    too_short = dict(xz=(0.0, -9.0), level_y=DECK_Y+3.625)
    assert road_preview(preview_graph, GeneratedSurface(fields), too_short,
                        (0.0, 0.0), 0.0) is None
    from shore_transport import selftest as transport_selftest
    assert transport_selftest() == 0
    from shore_connectivity import selftest as connectivity_selftest
    assert connectivity_selftest() == 0
    _resume_regressions(planned)
    assert first["rail_from"] in first["rail_post_prefabs"]
    assert WF.fabric_save_anchor([]) is None
    assert WF.fabric_save_anchor(planned["ops"])["params"]["prefab"] == first["boat"]["prefab"]
    # Source lies in the original core; its only genuine shore lies OUTSIDE it
    # but INSIDE the promised measured shore envelope. No graph stitching.
    from dataclasses import replace
    wide = fields["shore-trunk-primary"]
    zrows = wide.world_z(np.arange(wide.n))[:, None]
    shifted_h = np.broadcast_to(np.where(zrows <= 300,
        DECK_Y+(300-zrows)*0.1, np.maximum(20.0, DECK_Y-(zrows-300)*0.8)).astype(np.float32),
        wide.heights.shape).copy()
    wide = replace(wide, heights=shifted_h)
    clipped = replace(fields["trunk-primary"], heights=shifted_h[600:1001, 600:1001])
    outside = solve_site(requirement,
        {"stathub": dict(xz=(0, 140), patch="trunk-primary", island="spawn")},
        {"trunk-primary": clipped, "shore-trunk-primary": wide},
        graph_factory=fixture_graph)
    assert not clipped.contains(*outside["root"])
    assert outside["patch"] == outside["measurement_patch"] == wide.id
    assert wide.contains(*outside["anchor_xz"]) and wide.contains(*outside["root"])
    print("shore geometry regressions passed")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("op", choices=("produce", "selftest"))
    ap.add_argument("--field")
    worldseed.add_arguments(ap)
    args = ap.parse_args(argv)
    if args.op == "selftest":
        return selftest()
    worldseed.apply(args)
    if not args.field:
        ap.error("produce requires the existing pipeline --field")
    produce(args.field)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
