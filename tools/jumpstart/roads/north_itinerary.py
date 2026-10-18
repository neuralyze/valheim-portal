#!/usr/bin/env python3
"""Seed-derived north itinerary, shared by field production and the route driver.

The coarse plane orders reconstructible corridors, never authorises a write.
Every candidate's road legs pass network.build_segment and the ribbon writer.
An inter-continent leg is explicitly transport, not walking connectivity.
"""
from __future__ import annotations

import math
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy import ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(JUMPSTART) not in sys.path:
    sys.path.append(str(JUMPSTART))
import worldseed

# Resolution and overlap are survey parameters, not seed-specific coordinates.
SEARCH_STEP_M = 32.0
SURVEY_MARGIN_M = 256.0
WAYPOINT_REFINE_RADIUS_M = 48.0
TERMINAL_ROOT_RADIUS_M = 96.0
# tools/seedscan/README.md: playable world disc radius, not a seed landmark.
WORLD_RADIUS_M = 10000.0


def phase_policy(name: str | None = None) -> dict:
    """Use the shipped preset chain, not a second tier/distance table.

A pre-boss phase builds in its current biome as well as earlier ones. Pre-Kall
is the explicit exception requested by the owner: stop before Deep North.
"""
    name = name or os.environ.get("VH_NORTH_PRESET", "pre-kall")
    presets = [yaml.safe_load(p.read_text()) for p in
               sorted((JUMPSTART / "presets").glob("*.yaml"))]
    chosen = next((p for p in presets if p.get("name") == name), None)
    if chosen is None:
        raise ValueError(f"unknown north phase {name!r}; use a shipped preset")
    normalize = lambda s: str(s).replace(" ", "").lower()
    allowed = {normalize(p["biome"]) for p in presets
               if int(p["tier"]) <= int(chosen["tier"])}
    if chosen["name"] == "pre-kall":
        if "defeated_fader" not in chosen.get("global_keys", []):
            raise ValueError("pre-kall preset does not declare defeated_fader")
        allowed.discard("deepnorth")
    return {"preset": name, "tier": int(chosen["tier"]),
            "global_keys": list(chosen.get("global_keys", [])),
            "allowed_biomes": sorted(allowed), "next_boss": chosen.get("boss")}


def coarse_route(grid: dict, spawn_xz, *, preset: str | None = None) -> dict:
    """Dijkstra over this seed's terrain, with explicit expensive sea travel.

Dry edges obey the existing network's construction-grade ceiling (25%).
Water can be selected only as a transport corridor: fine routing determines
whether a short gap is structural and the dock/hull machinery judges ferries.
The search is north of spawn, apart from one search-cell overlap. No crop in X.
"""
    policy = phase_policy(preset)
    stride = max(1, int(round(SEARCH_STEP_M / float(grid["step"]))))
    h = np.asarray(grid["height"])[::stride, ::stride]
    b = np.asarray(grid["biome"])[::stride, ::stride]
    step = float(grid["step"]) * stride
    if abs(step - SEARCH_STEP_M) > 1e-6:
        raise ValueError(f"north frontier requires an explicit {SEARCH_STEP_M:g}m coarse search; got {step:g}m")
    # Keep the scanner's ORIGINAL half-cell offset when decimating.
    half_cell = float(grid["step"]) / 2.0
    xs = -float(grid["extent"]) + np.arange(h.shape[1]) * step + half_cell
    zs = -float(grid["extent"]) + np.arange(h.shape[0]) * step + half_cell
    # Biome codes are the seed scanner/PatchScan wire enum, not Unity flags.
    names = {1: "meadows", 2: "swamp", 3: "mountain", 4: "blackforest",
             5: "plains", 6: "ashlands", 7: "deepnorth", 9: "mistlands"}
    permitted = np.isin(b, [k for k, v in names.items()
                            if v in policy["allowed_biomes"]])
    dry = h > 30.5
    water = ~dry
    allowed = (permitted | water) & np.isfinite(h) & (b != 255) & (b != 0)
    allowed &= zs[:, None] >= float(spawn_xz[1]) - step
    # Even water bearing a forbidden biome is not a licence to enter it.
    allowed &= (permitted | (b == 8))
    allowed &= xs[None, :]**2 + zs[:, None]**2 <= WORLD_RADIUS_M**2
    si = int(np.argmin(abs(zs - spawn_xz[1])))
    sj = int(np.argmin(abs(xs - spawn_xz[0])))
    if not allowed[si, sj] or not dry[si, sj]:
        raise ValueError(f"north search spawn cell {xs[sj], zs[si]} is not dry "
                         f"permitted terrain (height={h[si, sj]}, biome={b[si, sj]})")
    ids = np.arange(h.size).reshape(h.shape)
    rows, cols, costs = [], [], []
    smooth = ndi.gaussian_filter(h.astype(np.float64), sigma=1.0)
    # Imported lazily: field production must not import spec/site installations.
    import route as road_route
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
        a = (slice(max(0, di), h.shape[0] + min(0, di)),
             slice(max(0, dj), h.shape[1] + min(0, dj)))
        c = (slice(max(0, -di), h.shape[0] + min(0, -di)),
             slice(max(0, -dj), h.shape[1] + min(0, -dj)))
        length = step * math.hypot(di, dj)
        ground = dry[a] & dry[c]
        ok = allowed[a] & allowed[c]
        ok &= ~(ground & (abs(smooth[a] - smooth[c]) >
                          length * road_route.GRADE_CEILING))
        # No diagonal jumping over a forbidden biome or a cliff corner.
        if di and dj:
            ai, aj = np.indices(h[a].shape)
            ai += max(0, di)
            aj += max(0, dj)
            ok &= allowed[ai - di, aj] & allowed[ai, aj - dj]
        price = np.where(ground, length, length * 15.0)
        price += np.where(dry[a] != dry[c], 300.0, 0.0)
        rows.append(ids[a][ok]); cols.append(ids[c][ok]); costs.append(price[ok])
    graph = coo_matrix((np.concatenate(costs),
                        (np.concatenate(rows), np.concatenate(cols))),
                       shape=(h.size, h.size)).tocsr()
    dist, pred = dijkstra(graph, directed=True, indices=int(ids[si, sj]),
                          return_predecessors=True)
    coastal = dry & ndi.binary_dilation(water) & permitted & np.isfinite(dist.reshape(h.shape))
    coastal &= zs[:, None] > spawn_xz[1]
    if not coastal.any():
        raise ValueError("north search: 0 reachable permitted northern shore cells; "
                         f"phase={policy['preset']}, dry_cells={int(dry.sum())}")
    ci, cj = np.nonzero(coastal)
    order = np.lexsort((xs[cj], dist[ids[ci, cj]], -zs[ci]))
    ordered = [int(ids[ci[k], cj[k]]) for k in order]
    # A predecessor tree stores shared corridors once, not one duplicate path
    # per shore. Every eligible shore remains reconstructible and ordered.
    tree = {}
    origin = int(ids[si, sj])
    for shore in ordered:
        target = shore
        while target not in tree:
            i, j = divmod(target, h.shape[1])
            parent = None if target == origin else int(pred[target])
            if parent == -9999:
                raise ValueError("north search predecessor chain does not reach spawn")
            tree[target] = [float(xs[j]), float(zs[i]), bool(not dry[i, j]), parent]
            if parent is None:
                break
            target = parent
    result = {"schema_version": 1, "seed": grid["seed"], "phase": policy,
              "search_step_m": step, "corridor_tree": tree,
              "candidate_order": ordered,
              "frontier": {"coarse_cell_m": step, "reachable_shore_cells": len(ordered),
                           "selection": "first fully buildable shore in descending northing, construction cost, x order",
                           "not_a_fine_maximum_proof": True}}
    return {**result, **route_for_candidate(result, ordered[0])}


def route_for_candidate(coarse, candidate_id):
    """Reconstruct one corridor from the shared, seed-derived search tree."""
    tree = coarse["corridor_tree"]
    if candidate_id not in coarse["candidate_order"]:
        raise ValueError(f"north candidate {candidate_id} is outside the eligible coarse universe")
    chain, seen, target = [], set(), candidate_id
    while target is not None:
        if target in seen:
            raise ValueError("north corridor predecessor cycle")
        seen.add(target)
        row = tree[target]
        chain.append(row[:3])
        target = row[3]
    chain.reverse()
    return {"path": chain, "frontier": {**coarse["frontier"],
            "candidate_id": candidate_id, "coarse_northing_m": chain[-1][1]}}


def route_windows(route: dict) -> tuple:
    """Deterministic union tiles of ALL eligible predecessor corridors.

512m tiles with 256m overlap retain the landing/refinement and terminal survey
envelope, even at tile boundaries. The actual field producer charges the union
to its explicit budget; no stage-9 scan or unread-ground fallback is permitted.
"""
    points = (route["corridor_tree"].values() if "corridor_tree" in route
              else route["path"])
    tiles = sorted({(int(math.floor((p[0] + 256.0) / 512.0)),
                     int(math.floor((p[1] + 256.0) / 512.0))) for p in points})
    return tuple(worldseed.Window(
        f"north-search-{ix:+05d}-{iz:+05d}", ix*512.0 + 0.5,
        iz*512.0 + 0.5, 512, 1.0,
        "union coverage of every eligible 32m north frontier corridor")
        for ix, iz in tiles)


def require_search_coverage(coarse, fields):
    """Missing alternative coverage invalidates the search, not the candidate."""
    unaligned = [key for key, f in fields.items() if abs(f.step-1.0) < 1e-9
                 and (abs(f.cx-f.half+0.5-round(f.cx-f.half+0.5)) > 1e-6
                      or abs(f.cz-f.half+0.5-round(f.cz-f.half+0.5)) > 1e-6)]
    if unaligned:
        raise ValueError(f"north writer preview refuses old half-grid fields {unaligned}; "
                         "request a REAL lattice-aligned stage-4 rescan, never relabel existing bytes")
    for w in route_windows(coarse):
        f = fields.get(w.fid)
        if (f is None or abs(f.cx-w.cx) > 1e-6 or abs(f.cz-w.cz) > 1e-6
                or abs(f.half-w.half) > 1e-6 or abs(f.step-1.0) > 1e-9):
            raise ValueError(f"north alternative coverage absent/foreign: {w.fid}; "
                             "rerun the scoped stage-4 field producer under its declared budget")


def _field_for(fields: dict, a, b, *, margin=32.0):
    candidates = [f for f in fields.values()
                  if abs(f.step - 1.0) < 1e-9
                  and abs(f.x0 - round(f.x0)) < 1e-6
                  and abs(f.z0 - round(f.z0)) < 1e-6
                  and f.contains(*a, margin=margin)
                  and f.contains(*b, margin=margin)]
    if not candidates:
        raise ValueError(f"north aligned 1m coverage missing for {a} -> {b} plus {margin}m; "
                         "rerun pipeline field with the coarse-derived north windows")
    return min(candidates, key=lambda f: (f.half, f.id))


def _dry_point(fields: dict, p, allowed_biomes: set[str], *, radius=WAYPOINT_REFINE_RADIUS_M):
    f = _field_for(fields, p, p, margin=radius + 2.0)
    i, j = f.index(*p); r = int(math.ceil(radius / f.step))
    ii, jj = np.mgrid[max(0, i-r):min(f.n, i+r+1), max(0, j-r):min(f.n, j+r+1)]
    import field as fieldmod
    good_b = np.isin(f.biome[ii, jj], [k for k, v in fieldmod.BIOME_NAMES.items()
                                      if v.replace(" ", "").lower() in allowed_biomes])
    distance = (f.world(0, 0)[0] + jj*f.step - p[0])**2 + (f.world(0, 0)[1] + ii*f.step - p[1])**2
    valid = (f.h[ii, jj] > 30.5) & good_b & (distance <= radius**2)
    if not valid.any():
        raise ValueError(f"north endpoint {p}: 0 dry permitted 1m cells inside {radius}m")
    best = np.argmin(np.where(valid, distance, np.inf))
    return [float(v) for v in f.world(int(ii.flat[best]), int(jj.flat[best]))]


def _landing_point(fields, p, sea, permitted):
    """A real shallow bank within the surveyed envelope; no manufactured coast."""
    import field as fieldmod
    f = _field_for(fields, p, p, margin=TERMINAL_ROOT_RADIUS_M + 2.0)
    i, j = f.index(*p)
    r = int(TERMINAL_ROOT_RADIUS_M)
    ii, jj = np.mgrid[max(0, i-r):min(f.n, i+r+1),
                      max(0, j-r):min(f.n, j+r+1)]
    xx, zz = f.x0 + jj*f.step, f.z0 + ii*f.step
    dist = np.hypot(xx-p[0], zz-p[1])
    codes = [k for k, v in fieldmod.BIOME_NAMES.items()
             if v.replace(" ", "").lower() in permitted]
    good = ((dist <= TERMINAL_ROOT_RADIUS_M) & (f.h[ii, jj] > 30.5)
            & (f.h[ii, jj] <= 31.0) & np.isin(f.biome[ii, jj], codes))
    if not good.any():
        raise ValueError(f"north dock {p}: 0 natural shallow-bank cells "
                         f"(30.5 < h <= 31.0) inside {TERMINAL_ROOT_RADIUS_M:g}m; no forced terrain")
    score = dist + np.hypot(xx-sea[0], zz-sea[1])
    k = int(np.argmin(np.where(good, score, np.inf)))
    return [float(xx.flat[k]), float(zz.flat[k])]


def derive_itinerary(coarse: dict, fields: dict, base: dict, net) -> dict:
    """Attach to an actual generated ROOTED station, never a standing N0b."""
    rooted, _ = net.orphan_components(base.get("segments", []))
    if not rooted:
        raise ValueError("north itinerary: generated network has no connected root component")
    path = coarse["path"]
    tree = cKDTree(np.asarray([p[:2] for p in path]))
    joins = []
    for sg in base["segments"]:
        if sg["id"] not in rooted:
            continue
        xy = np.asarray([p[:2] for p in sg["nodes"]])
        distances, indices = tree.query(xy)
        for station, (distance, k) in enumerate(zip(distances, indices)):
            if sg.get("is_bridge") and sg["is_bridge"][station]:
                continue
            if distance <= SURVEY_MARGIN_M / 2.0 and not path[int(k)][2]:
                try:
                    _field_for(fields, xy[station], path[int(k)][:2], margin=50.0)
                except ValueError:
                    continue
                joins.append((int(k), -float(distance), sg["id"], sg, station))
    if not joins:
        raise ValueError("north itinerary: 0 physical rooted stations within the surveyed corridor")
    import poi
    source_pois = poi.load()
    source_width = net.WIDTH_M["trunk"]
    for k, _, _, root_sg, station in sorted(joins, key=lambda r: (r[0], r[1], r[2]), reverse=True):
        root_xz = [float(round(v)) for v in root_sg["nodes"][station][:2]]
        forbidden, _, _ = poi.masks(source_pois, *root_xz, 1.0, 1, source_width/2.0)
        pads = net.pad_forbid_mask(net.pad_claims(), *root_xz, 1.0, 1,
                                   net.routemod.footprint_half_m(source_width))
        if not forbidden[0, 0] and not pads[0, 0]:
            break
    else:
        raise ValueError(f"north attachment: all {len(joins)} rooted station candidates "
                         "are inside generator/pad keep-outs at the north road's width")
    root = "north-root-junction"
    points = [[*root_xz, False]] + [list(p) for p in path[k:]]
    allowed = set(coarse["phase"]["allowed_biomes"])
    # Refine road/sea transition points against the real fine plane.
    for n, p in enumerate(points):
        if not p[2]:
            p[:2] = _dry_point(fields, p[:2], allowed)
    points[0][:2] = root_xz
    # Short wet runs remain potential structures, judged by the existing span gate.
    n = 0
    while n < len(points):
        if not points[n][2]:
            n += 1; continue
        end = n
        while end + 1 < len(points) and points[end+1][2]:
            end += 1
        if n > 0 and end+1 < len(points) and math.dist(points[n-1][:2], points[end+1][:2]) <= net.MAX_BRIDGE_SPAN_M:
            del points[n:end+1]
            continue
        n = end+1
    nodes = {root: {"xz": root_xz, "kind": "junction", "island": "north-generated",
                    "provenance": "a station of this run's generated rooted road network",
                    "level_y": float(root_sg["profile_y"][station])}}
    edges = []; terminals = {}; transport = []; last = root; pending = None
    def add_node(p):
        nid = f"north-node-{len(nodes):04d}"
        nodes[nid] = {"xz": list(p[:2]), "kind": "waypoint", "island": "north-generated",
                      "provenance": "coarse itinerary refined against this seed's 1m field"}
        return nid
    def add_road(a, b):
        f = _field_for(fields, nodes[a]["xz"], nodes[b]["xz"])
        sid = f"north-road-{len(edges):04d}"
        for nid in (a, b):
            nodes[nid]["patch"] = f.id
        edges.append({"id": sid, "a": a, "b": b, "cls": "trunk", "pri": 1,
                      "patch": f.id, "why": "generated north itinerary"})
        return sid
    def terminal(nid, sea, sid):
        nodes[nid]["xz"] = _landing_point(fields, nodes[nid]["xz"], sea, allowed)
        nodes[nid]["level_y"] = float(net.specmod.DECK_DATUM_Y)
        tid = f"north-dock-{len(terminals):04d}"
        terminals[tid] = {"xz": list(nodes[nid]["xz"]), "sea_xz": list(sea),
                          "node_id": nid, "segment_id": sid, "final_departure": False}
        return tid
    i = 1
    while i < len(points):
        if points[i][2]:
            end = i
            while end+1 < len(points) and points[end+1][2]:
                end += 1
            if end+1 >= len(points):
                raise ValueError("north itinerary ends at sea, not a constructible shore")
            if not edges or edges[-1]["b"] != last:
                raise ValueError("north ferry departure has no physical road-ground approach")
            a = terminal(last, points[i][:2], edges[-1]["id"])
            arrival = add_node(points[end+1])
            b = terminal(arrival, points[end][:2], "")
            transport.append({"id": f"north-ferry-{len(transport):04d}", "a": a, "b": b,
                              "sea_route": [p[:2] for p in points[i:end+1]]})
            last = arrival; pending = b; i = end+2; continue
        p = points[i]
        if math.dist(nodes[last]["xz"], p[:2]) < 4.0:
            i += 1; continue
        # One search edge per fine route keeps the survey/corridor bounds honest.
        nid = add_node(p); sid = add_road(last, nid)
        if pending:
            terminals[pending]["segment_id"] = sid; pending = None
        last = nid; i += 1
    if pending:
        raise ValueError(f"north arrival {pending} has 0m of constructible road beyond its dock")
    if not edges:
        raise ValueError("north itinerary has 0 generated road-ground legs")
    # The final departure sails towards the actual next biome's nearest coast.
    last_p = points[-1][:2]
    f = _field_for(fields, last_p, last_p, margin=64.0)
    ii, jj = np.nonzero((f.h < 27.0) & (f.biome != 255))
    if not len(ii):
        raise ValueError(f"north frontier {last_p}: 0 navigable departure cells in fine patch")
    xy = np.column_stack((f.x0 + jj*f.step, f.z0 + ii*f.step))
    dist = np.linalg.norm(xy-np.asarray(last_p), axis=1)
    # Prefer north-facing navigable water, but do not fabricate it if absent.
    candidates = np.nonzero((xy[:, 1] >= last_p[1]) & (dist >= 12.0))[0]
    if not len(candidates):
        raise ValueError(f"north frontier {last_p}: 0 north-facing navigable departure cells")
    sea = xy[candidates[np.argmin(dist[candidates])]].tolist()
    final = terminal(last, sea, edges[-1]["id"])
    terminals[final]["final_departure"] = True
    for edge in edges:
        edge["patch"] = _field_for(fields, nodes[edge["a"]]["xz"],
                                   nodes[edge["b"]]["xz"]).id
    return {"schema_version": 1, "seed": worldseed.seed(), "world": worldseed.world(),
            "provisional": True, "phase": coarse["phase"], "nodes": nodes,
            "required_connections": base["connection_requirements"],
            "road_edges": edges, "terminals": terminals, "transport": transport,
            "final_departure": final, "frontier": {**coarse["frontier"],
                "road_endpoint_xz": list(nodes[last]["xz"])},
            "attachment": {"node": root, "segment_id": root_sg["id"],
                           "station": station, "height_y": nodes[root]["level_y"]}}


def load_itinerary(path, *, provisional_ok=False) -> dict:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    worldseed.require_scope("north itinerary", path, found_seed=doc.get("seed"),
                            found_world=doc.get("world"))
    if doc.get("schema_version") != 1 or not doc.get("road_edges") or not doc.get("terminals"):
        raise ValueError(f"{path}: north itinerary has no complete road/transport population")
    if not isinstance(doc.get("provisional"), bool):
        raise ValueError(f"{path}: provisional state is unread")
    if doc["provisional"] and not provisional_ok:
        raise ValueError(f"{path}: north itinerary is PROVISIONAL, not buildable")
    final = doc.get("final_departure")
    if final not in doc["terminals"] or not doc["terminals"][final].get("final_departure"):
        raise ValueError(f"{path}: final departure terminal is absent")
    edges = {e["id"]: e for e in doc["road_edges"]}
    if len(edges) != len(doc["road_edges"]):
        raise ValueError(f"{path}: duplicate north road ids")
    paired = set()
    for pair in doc.get("transport", []):
        if pair.get("a") == pair.get("b") or any(pair.get(k) not in doc["terminals"] for k in ("a", "b")):
            raise ValueError(f"{path}: ferry {pair.get('id')} is not a pair of distinct docks")
        for tid in (pair["a"], pair["b"]):
            if tid in paired:
                raise ValueError(f"{path}: terminal {tid} belongs to multiple voyages")
            paired.add(tid)
    for tid, terminal in doc["terminals"].items():
        if tid != final and tid not in paired:
            raise ValueError(f"{path}: terminal {tid} is a lone boat prop, not paired transport")
        if bool(terminal.get("final_departure")) != (tid == final):
            raise ValueError(f"{path}: final departure marker is not unique")
        edge = edges.get(terminal.get("segment_id"))
        nid = terminal.get("node_id")
        if edge is None or nid not in (edge["a"], edge["b"]):
            raise ValueError(f"{path}: terminal {tid} has no serving north road endpoint")
        node = (doc.get("nodes") or {}).get(nid)
        if node is None or list(node["xz"]) != list(terminal["xz"]):
            raise ValueError(f"{path}: terminal {tid} is not on its road node")
    if not doc["provisional"] and (
            doc.get("route_refusals") or doc.get("base_route_refusals")
            or set(doc.get("routed_road_ids") or []) != set(edges)):
        raise ValueError(f"{path}: final itinerary omits or refuses required road-ground")
    return doc


def _joint_routed_profiles(base, segments, joints, fields, net, plan_state,
                           *, provisional):
    """Keep provisional base survey debt; never authorize it for construction."""
    participants = plan_state["pads"]
    base_ids = {s["id"] for s in base["segments"]}
    combined = base["segments"] + segments
    all_joints = {**plan_state["joints"], **joints}
    judged = {s["id"]: list(s["profile_y"]) for s in combined}
    jd = net.water_after_joint(
        combined, all_joints, participants, fields, judged,
        plan_state["water_cache"])
    refused, base_refused, rejected_ids = [], [], set()

    def record(sid, kind, diagnostic):
        rec = {"id": sid, "kind": kind, "error": str(diagnostic),
               "diagnostic": diagnostic}
        if provisional and sid in base_ids:
            base_refused.append(rec)
        else:
            refused.append(rec)
            rejected_ids.add(sid)

    joint_refusals = {r["id"]: net.junction_rejection(r)
                      for r in jd["refused"]}
    for sg in combined:
        sid = sg["id"]
        if sid in joint_refusals:
            sg["junction_datum"] = {"verdict": "REFUSED", **joint_refusals[sid]}
            record(sid, "joint", joint_refusals[sid])
        else:
            sg.setdefault("junction_datum", {})["verdict"] = "JOINED"
        key = (sid, tuple(sg["profile_y"]))
        reading = plan_state["water_cache"].get(key)
        if reading is None:
            reading = net.writer_water(sg, worldseed.resolve(fields, sg["patch"]))
            plan_state["water_cache"][key] = reading
        public = {**net.water_public(reading), **net.judged_public(reading)}
        water = sg.setdefault("writer_water", {})
        water.update(public)
        water["after_junction"] = public
        water["final_verdict"] = net.final_verdict(public)
        if reading.get("collar") is False:
            sg["connection_collar"] = False
        else:
            sg.pop("connection_collar", None)
        if water["final_verdict"] == "REFUSED":
            record(sid, "writer", public)
    base["segments"] = [s for s in base["segments"]
                        if s["id"] not in rejected_ids]
    base["north_junction_datum"] = {
        "refused": jd["refused"], "pads_missing": plan_state["pads_missing"],
        "participants": len(participants)}
    return ([s for s in segments if s["id"] not in rejected_ids],
            refused, base_refused)


def append_routes(doc: dict, fields: dict, base: dict, crossings: dict, net, census,
                  *, plan_state: dict) -> tuple[dict, dict]:
    """Measure every north road by the existing router/writer gates, then merge.

No ferry is inserted into the walking component graph as a pretend bridge.
"""
    import poi
    import route
    pois = poi.load()
    obstacles = pois + ([] if census is None else census.pois())
    net.carriageway_from_disk(base)
    pinned = {doc["attachment"]["node"]} | {t["node_id"] for t in doc["terminals"].values()}
    for nid, node in doc["nodes"].items():
        if nid not in pinned:
            node.pop("level_y", None)
    net.specmod.NODES.update(doc["nodes"])
    segments = []; refused = []; lines = []
    joints = {}
    for edge in doc["road_edges"]:
        f = fields[edge["patch"]]
        for nid in (edge["a"], edge["b"]):
            net.specmod.NODES[nid]["patch"] = f.id
        corr = route.Corridor(f, cell_m=1.0, grade_max=route.GRADE_CEILING,
                              bridge_entry_cost_m=300.0, bridge_per_m_cost=15.0)
        forbid, soft, _ = poi.masks(obstacles, corr.x0, corr.z0, corr.cell_m,
                                    corr.m, net.WIDTH_M[edge["cls"]]/2.0)
        pads = net.pad_forbid_mask(net.pad_claims(), corr.x0, corr.z0, corr.cell_m,
                                   corr.m, route.footprint_half_m(net.WIDTH_M[edge["cls"]]))
        import field as fieldmod
        permitted = [k for k, v in fieldmod.BIOME_NAMES.items()
                     if v.replace(" ", "").lower() in doc["phase"]["allowed_biomes"]]
        phase_forbid = ~np.isin(corr.Bi, permitted + [8])
        corr.forbid = forbid | pads | phase_forbid
        corr.soft = soft
        searched = []
        try:
            def build_north(**kw):
                j = {}
                rec = net.build_segment(f, edge, corr, obstacles, census=census,
                                         joint=j, line_out=searched, **kw)
                rec["patch"] = f.id
                return rec, j
            sg, joint = build_north()
            if net.span_worst(sg) > net.MAX_BRIDGE_SPAN_M:
                worst = net.span_worst(sg)
                try:
                    sg, joint = build_north(land_only=True)
                except ValueError as exc:
                    (sg, joint), ladder = net.span_reprice(
                        corr, build_north, first_worst=worst, land_exc=exc)
                    sg["span_reprice"] = ladder
            sg, joint = net.water_route(
                f, corr, sg, joint, build=build_north,
                refusal=lambda rec: net.plan_refusal(
                    rec, pois, worldseed.seed(), fld=f, coverage=False))
            sg["patch"] = f.id
            sg["write_gate"] = net.write_gate(sg, pois, worldseed.seed())
            why = net.plan_refusal(sg, pois, worldseed.seed(), fld=f, coverage=False)
            water = net.writer_water(sg, f)
            public_water = {**net.water_public(water), **net.judged_public(water)}
            sg["writer_water"] = {**sg.get("writer_water", {}), **public_water,
                                   "final_verdict": net.final_verdict(public_water)}
            if water.get("collar") is False:
                sg["connection_collar"] = False
            released = set((edge["a"], edge["b"])) - net._served_nodes(sg)
            if why or released:
                raise ValueError(f"north road {edge['id']} refused by "
                                 f"{why or ('released endpoints ' + str(sorted(released)) if released else 'writer_water')}: "
                                 f"{public_water}")
            actual_start, actual_end = list(sg["nodes"][0][:2]), list(sg["nodes"][-1][:2])
            if actual_start != list(doc["nodes"][edge["a"]]["xz"]):
                raise ValueError(f"north road {edge['id']} would lose its physical "
                                 f"source junction: {doc['nodes'][edge['a']]['xz']} -> {actual_start}")
            # Endpoints are solver output, not frozen coarse wishes. Carry the
            # measured legal arrival into the next road and every dock contract.
            doc["nodes"][edge["b"]]["xz"] = actual_end
            for terminal in doc["terminals"].values():
                if terminal["node_id"] == edge["b"]:
                    terminal["xz"] = list(actual_end)
            sg["zones_carriageway"] = sorted([list(z) for z in net.zones_of_segment(sg)])
            sg["zdo_cost"] = len(sg["zones_carriageway"])
            segments.append(sg)
            joints[sg["id"]] = joint
        except ValueError as exc:
            refused.append({"id": edge["id"], "error": str(exc)})
        if searched:
            lines.append({"id": edge["id"], "patch": f.id, "nodes": searched})
    base_refused = []
    if segments:
        segments, joint_refused, base_refused = _joint_routed_profiles(
            base, segments, joints, fields, net, plan_state,
            provisional=census is None)
        refused.extend(joint_refused)
    doc["base_route_refusals"] = base_refused
    if census is None:
        doc["provisional"] = True
    base["segments"].extend(segments)
    base.setdefault("unroutable", []).extend(refused)
    base.setdefault("census_lines", []).extend(lines)
    base["zones_write"] = net.write_zones(base["segments"])
    all_segments = base["segments"]
    base.setdefault("totals", {}).update({
        "segments": len(all_segments),
        "length_m": round(sum(s["length_m"] for s in all_segments), 1),
        "paved_m2": round(sum(s["terrain_stations"] * s["station_m"] * s["width_m"]
                               for s in all_segments), 1),
        "zones_touched": len({tuple(z) for s in all_segments for z in s["zones_carriageway"]}),
        "worst_grade": max((s["grade_max_measured"] for s in all_segments), default=0.0),
        "unroutable_edges": len(base.get("unroutable") or [])})
    base.setdefault("node_provenance", {}).update({nid: {
        "xz": n["xz"], "kind": n.get("kind"), "source": str(worldseed.north_path()),
        "verbatim": True, "cites": n.get("provenance", "generated north itinerary")}
        for nid, n in doc["nodes"].items()})
    survey_lines = transport_survey_lines(doc, fields)
    base["census_lines"].extend(survey_lines)
    if census is not None:
        import precensus
        wanted = net.census_wanted(segments, [], census)
        for line in survey_lines:
            cells = [list(c) for c in precensus.cells_for_band(line["nodes"], precensus.BAND_HALF_M)
                     if not census.has_cell(c)]
            wanted["legs"].append({"id": line["id"], "patch": line["patch"], "cells": cells})
            wanted["cells"].extend(cells)
        old = base.get("census_wanted") or {}
        base["census_wanted"] = {**old, "legs": (old.get("legs") or []) + wanted["legs"],
            "cells": sorted({tuple(c) for c in (old.get("cells") or []) + wanted["cells"]}),
            "servable_unrouted": (old.get("servable_unrouted") or []) + wanted.get("servable_unrouted", [])}
        # Unread ground keeps the entire itinerary unbuildable until the
        # existing census fixed point measures it; no partial ferry build.
        doc["provisional"] = bool(base["census_wanted"]["cells"] or refused)
    doc["route_refusals"] = refused
    doc["routed_road_ids"] = [s["id"] for s in segments]
    doc["frontier"]["road_endpoint_xz"] = list(doc["terminals"][doc["final_departure"]]["xz"])
    base["north_itinerary"] = str(worldseed.north_path())
    crossings["detected"], collapsed = net.boardwalk.collapse_duplicate_declarations(
        [c for s in base["segments"] for c in s["crossings"]])
    crossings["north_crossing_collapse"] = collapsed
    base["totals"]["detected_crossings"] = len(crossings["detected"])
    crossings.setdefault("abutment_handoffs", []).extend(
        a | {"segment_id": sg["id"], "cls": sg["cls"]}
        for sg in segments for a in sg["abutments"])
    net.carriageway_to_disk(base)
    return base, crossings


def transport_survey_lines(doc, fields):
    """Generate/census terminal search boxes AND real sea lanes before building."""
    out = []
    for tid, terminal in doc["terminals"].items():
        x, z = terminal["xz"]
        f = _field_for(fields, [x, z], [x, z], margin=98.0)
        nodes = []
        for row, dz in enumerate(range(-96, 97, 16)):
            ends = (-96.0, 96.0) if row % 2 == 0 else (96.0, -96.0)
            nodes.extend([[x + dx, z + dz] for dx in ends])
        out.append({"id": f"north-terminal-survey-{tid}", "patch": f.id, "nodes": nodes})
    for pair in doc["transport"]:
        for k, (a, b) in enumerate(zip(pair["sea_route"], pair["sea_route"][1:])):
            f = _field_for(fields, a, b)
            out.append({"id": f"north-sea-survey-{pair['id']}-{k:04d}",
                        "patch": f.id, "nodes": [a, b]})
    return out




def file_identity(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest}


def planned_surface(fields, segments, net):
    """Writer raster preview over scoped integer fields; never a deck echo."""
    import ribbon
    import north_transport as NT
    from dataclasses import replace
    from types import SimpleNamespace
    if segments and net is None:
        raise ValueError("planned road terrain requires the production writer providers")
    transport_fields = {key: NT.W.Field(f.id, f.cx, f.cz, f.half, f.step, f.n, f.h, f.biome)
                        for key, f in fields.items() if abs(f.step-1.0) < 1e-9
                        and abs(f.x0-round(f.x0)) < 1e-6
                        and abs(f.z0-round(f.z0)) < 1e-6}
    scoped = NT.ScopedFields(transport_fields, worldseed.seed(), source="scoped stage-4 field")
    compiled = []
    pads = (net._PLAN_PAD_KEEPOUTS() if net is not None and net._PLAN_PAD_KEEPOUTS is not None
            else ribbon.PadKeepOut([], []))
    for sg in segments:
        zones = sorted(set(ribbon.segment_zones(sg["nodes"], sg["is_bridge"], sg["width_m"]))
                       | pads.approach_zones(sg))
        if not zones:
            continue
        patches = scoped_writer_patches(fields, zones)
        comps, _ = ribbon.stamp(sg, patches, zones, pads,
                                poi_keepouts=net.writer_keepouts(zones))
        compiled.append(comps)

    class Planned(NT.WF.Surface):
        def __init__(self):
            self.fields = scoped
            self.field_names = [scoped.source]
            self._patches = {}
            self._masks = {}

        def at(self, x, z):
            f = _field_for(fields, [x, z], [x, z], margin=1.0)
            generated = f.bilinear(x, z)
            # Later stamps replace claimed samples; road profiles are not added.
            def vertex(sx, sz):
                y = pads.finished_y(sx, sz)
                if y is not None:
                    return y
                value = f.at(sx, sz)
                for comps in compiled:
                    comp = comps.get(ribbon.tcdata.zone_of(sx, sz))
                    if comp is None:
                        continue
                    gx, gy = ribbon.tcdata.vertex_mask_index(*comp.centre, sx, sz)
                    if comp.modified_height[gy*ribbon.tcdata.PITCH + gx]:
                        value = f.at(sx, sz) + ribbon.delta_at(comps, sx, sz)
                return value
            x0, z0 = math.floor(x), math.floor(z)
            tx, tz = x-x0, z-z0
            y = sum(w*vertex(x0+dx, z0+dz) for dx, dz, w in (
                (0, 0, (1-tx)*(1-tz)), (1, 0, tx*(1-tz)),
                (0, 1, (1-tx)*tz), (1, 1, tx*tz)) if w)
            return y, generated, y-generated

        def hull_depths(self, bearing, x, z, vessel):
            f = self.patch_for(x, z, margin=NT.WF.VESSELS[vessel]["hull_l"])
            if f.id not in self._patches:
                heights = f.heights.copy()
                for comps in compiled:
                    for comp in comps.values():
                        for k, modified in enumerate(comp.modified_height):
                            if not modified:
                                continue
                            gy, gx = divmod(k, ribbon.tcdata.PITCH)
                            sx, sz = ribbon.tcdata.sample_world(*comp.centre, gx, gy)
                            if f.contains(sx, sz, margin=1.0):
                                i, j = f.index(sx, sz)
                                heights[i, j] = self.at(sx, sz)[0]
                # Planned foundations also change water connectivity, including
                # cells no road claims. Use the pad writer's own stamp predicate.
                for _, cx, cz, hw, hd in pads.unlaid:
                    for sz in range(max(math.floor(cz-hd)-1, int(f.z0)),
                                    min(math.ceil(cz+hd)+1, int(f.z0)+f.n-1)+1):
                        for sx in range(max(math.floor(cx-hw)-1, int(f.x0)),
                                        min(math.ceil(cx+hw)+1, int(f.x0)+f.n-1)+1):
                            y = pads.finished_y(sx, sz)
                            if y is not None:
                                i, j = f.index(sx, sz)
                                heights[i, j] = y
                self._patches[f.id] = replace(f, heights=heights)
            patch = self._patches[f.id]
            key = (f.id, vessel)
            if key not in self._masks:
                self._masks[key] = NT.WF.sea_mask(
                    patch, NT.WF.VESSELS[vessel]["draught_m"] + NT.WF.KEEL_CLEARANCE_M)[1]
            return NT.WF.hull_depths(SimpleNamespace(patch_for=lambda *a, **kw: patch),
                                    bearing, x, z, vessel, open_sea=self._masks[key])
    return Planned(), scoped


def scoped_writer_patches(fields, zones):
    """Writer-shaped patches read ONLY measured integer lattice vertices."""
    import ribbon
    def generated(x, z):
        f = _field_for(fields, [x, z], [x, z], margin=0.0)
        return f.at(x, z)
    return {f"z_{x}_{z}": ribbon._flat_patch(x, z, generated) for x, z in zones}


class CandidateRefusal(ValueError):
    def __init__(self, stage, reason):
        super().__init__(str(reason))
        self.stage = stage


def select_voyage_vessel(doc, pair, fields, *, surface):
    """Choose one measured hull for BOTH berths and the continuous sea lane.

    Roads and their finished raster are already fixed. A failed hull retries
    only this voyage, never reroutes every road or rejects a higher frontier
    before the other measured hulls have been tried.
    """
    import north_transport as NT
    failures = []
    for vessel in NT.vessel_candidates(False):
        trial = {**doc, "terminals": dict(doc["terminals"])}
        for tid in (pair["a"], pair["b"]):
            trial["terminals"][tid] = {**doc["terminals"][tid], "vessel": vessel}
        try:
            plans = [NT.plan_terminal(trial, tid, fields, surface=surface)
                     for tid in (pair["a"], pair["b"])]
            report = NT.measure_passage(pair, plans, fields, surface=surface)
        except (ValueError, SystemExit) as exc:
            failures.append({"vessel": vessel, "reason": str(exc)})
            continue
        for tid in (pair["a"], pair["b"]):
            doc["terminals"][tid]["vessel"] = vessel
        return plans, report
    raise ValueError(f"{pair['id']}: no measured vessel supports both berths and "
                     f"the swept sea passage: {json.dumps(failures, sort_keys=True)}")


def select_departure_vessel(doc, fields, *, surface):
    """A LARGE Odin ship, with the actual road root, dock and lighthouse."""
    import north_transport as NT
    tid = doc["final_departure"]
    failures = []
    for vessel in NT.vessel_candidates(True):
        trial = {**doc, "terminals": {**doc["terminals"],
                 tid: {**doc["terminals"][tid], "vessel": vessel}}}
        try:
            plan = NT.plan_terminal(trial, tid, fields, surface=surface)
        except (ValueError, SystemExit) as exc:
            failures.append({"vessel": vessel, "reason": str(exc)})
            continue
        doc["terminals"][tid]["vessel"] = vessel
        return plan
    raise ValueError(f"{tid}: no measured LARGE Odin ship supports the berth, "
                     f"dock and lighthouse: {json.dumps(failures, sort_keys=True)}")


def select_transport_vessels(doc, fields, *, surface):
    """Independent voyage choices, followed by the whole-itinerary predicate."""
    import north_transport as NT
    plans = {}
    for pair in doc["transport"]:
        selected, _ = select_voyage_vessel(doc, pair, fields, surface=surface)
        plans.update((p["terminal_id"], p) for p in selected)
    final = select_departure_vessel(doc, fields, surface=surface)
    plans[final["terminal_id"]] = final
    ordered = [plans[tid] for tid in doc["terminals"]]
    passages = NT.measure_passages(doc, ordered, fields, surface=surface)
    return ordered, passages


def _evaluate_candidate(coarse, candidate_id, fields, base, crossings, net, census, state):
    """Candidate transaction: rejected roads never enter the selected plan."""
    try:
        candidate = {**coarse, **route_for_candidate(coarse, candidate_id)}
        doc = derive_itinerary(candidate, fields, base, net)
    except ValueError as exc:
        raise CandidateRefusal("landing", exc) from exc
    try:
        base, crossings = append_routes(doc, fields, base, crossings, net, census,
                                        plan_state=state)
        if doc["route_refusals"]:
            raise ValueError(json.dumps(doc["route_refusals"], sort_keys=True))
    except ValueError as exc:
        raise CandidateRefusal("road", exc) from exc
    import north_transport as NT
    try:
        surface, scoped = planned_surface(fields, base["segments"], net)
        plans, passages = select_transport_vessels(doc, scoped, surface=surface)
    except (ValueError, SystemExit) as exc:
        raise CandidateRefusal("transport", exc) from exc
    doc["construction_feasibility"] = {
        "surface": "ribbon.stamp over scoped integer stage-4 field; planned, not saved-world proof",
        "terminals": [p["terminal_id"] for p in plans], "passages": passages,
        "lighthouse": any(p["lighthouse"] for p in plans)}
    return doc, base, crossings


def evaluate_candidate(coarse, candidate_id, fields, base, crossings, net, census, state):
    """Keep production and acceptance replay on identical read-only providers."""
    import ribbon
    saved = (net.SEED, net.WORLD, net._PLAN_PATCHES, net._PLAN_KEEPOUTS,
             net._PLAN_PAD_KEEPOUTS)
    pads = ribbon.pad_keepouts_from_chain(
        [], [], planned=ribbon.planned_pad_claims(worldseed.world(), worldseed.seed()))
    try:
        net.SEED, net.WORLD = worldseed.seed(), worldseed.world()
        net._PLAN_PATCHES = lambda _fld, zones: scoped_writer_patches(fields, zones)
        net._PLAN_KEEPOUTS = net.writer_keepouts
        net._PLAN_PAD_KEEPOUTS = lambda: pads
        return _evaluate_candidate(coarse, candidate_id, fields, base, crossings, net, census, state)
    finally:
        (net.SEED, net.WORLD, net._PLAN_PATCHES, net._PLAN_KEEPOUTS,
         net._PLAN_PAD_KEEPOUTS) = saved


def select_frontier(coarse, evaluate):
    """No top-N cap: exhaust eligible shores or return the first full success."""
    rejected = []
    for candidate_id in coarse["candidate_order"]:
        try:
            result = evaluate(candidate_id)
        except CandidateRefusal as exc:
            rejected.append({"candidate_id": candidate_id, "stage": exc.stage, "reason": str(exc)})
            continue
        return result, rejected
    raise CandidateRefusal("exhausted", json.dumps({
        "eligible_candidates": len(coarse["candidate_order"]), "rejected": rejected,
        "search_step_m": coarse["search_step_m"],
        "not_a_fine_maximum_proof": True}, sort_keys=True))


def validate_frontier_evidence(doc, coarse):
    """Structural contract; acceptance must also independently replay failures."""
    search = doc.get("frontier_search") or {}
    selected = doc.get("frontier", {}).get("candidate_id")
    order = coarse["candidate_order"]
    if selected not in order:
        raise ValueError("selected frontier is outside the eligible 32m coarse search")
    rejected = search.get("rejected") or []
    if [r.get("candidate_id") for r in rejected] != order[:order.index(selected)]:
        raise ValueError("north rejection evidence is not the complete ordered higher-candidate prefix")
    if any(r.get("stage") not in ("landing", "road", "transport") or not r.get("reason")
           for r in rejected):
        raise ValueError("north higher-candidate rejection has no measured failure")
    expected = route_for_candidate(coarse, selected)["frontier"]
    if any(doc["frontier"].get(k) != expected[k] for k in
           ("coarse_northing_m", "coarse_cell_m", "candidate_id")):
        raise ValueError("north selected frontier differs from reconstructed coarse corridor")
    for row in search.get("inputs", {}).values():
        if file_identity(row["path"]) != row:
            raise ValueError(f"north search input changed: {row['path']}")
    required = {"field", "grid", "locations", "planner", "transport", "writer",
                "network", "hull_geometry", "waterfront", "dock_geometry"}
    if not doc.get("provisional"):
        required |= {"census", "lighthouse_body", "lighthouse_manifest"}
    if not required <= set(search.get("inputs", {})):
        raise ValueError("north rejection evidence lacks scoped source/input identities")
    import north_transport as NT
    expected_sources = {"planner": __file__, "transport": HERE / "north_transport.py",
                        "writer": HERE / "ribbon.py", "network": HERE / "network.py",
                        "hull_geometry": JUMPSTART / "accept" / "sections" / "harbours.py",
                        "waterfront": NT.WF.__file__, "dock_geometry": NT.A.__file__}
    for key, path in expected_sources.items():
        if search["inputs"][key] != file_identity(path):
            raise ValueError(f"north rejection source identity is not the active {key}")
    if doc.get("height_source", {}).get("identity") != search["inputs"]["field"]:
        raise ValueError("north frontier and road/dock plans cite different raw terrain fields")
    if (search.get("eligible_candidates") != len(order)
            or search.get("search_step_m") != SEARCH_STEP_M
            or coarse["search_step_m"] != SEARCH_STEP_M
            or search.get("not_a_fine_maximum_proof") is not True):
        raise ValueError("north frontier evidence misstates its bounded 32m search universe")
    return True


def replay_frontier_rejection(record, coarse, fields, net, census, search):
    """Re-run a claimed rejection, not merely its serialized failed flag."""
    initial = search["replay"]
    state = copy.deepcopy(initial["state"])
    state.update(fields=fields, water_cache={})
    nodes = copy.deepcopy(net.specmod.NODES)
    try:
        evaluate_candidate(coarse, record["candidate_id"], fields,
                           copy.deepcopy(initial["base"]), copy.deepcopy(initial["crossings"]),
                           net, census, state)
    except CandidateRefusal as exc:
        if exc.stage != record["stage"] or str(exc) != record["reason"]:
            raise ValueError("higher-candidate rejection does not reproduce its measured stage/reason") from exc
        return True
    finally:
        net.specmod.NODES.clear()
        net.specmod.NODES.update(nodes)
    raise ValueError("claimed rejected higher frontier is buildable on independent replay")


def search_survey_lines(coarse, fields):
    """Census the union once; final fallback must not borrow unread objects."""
    out = []
    for w in route_windows(coarse):
        f = fields[w.fid]
        nodes = []
        # Census bands whole 16m cells. Retain an extra cell before the
        # field boundary so ground_of never clamps an unread outer cell.
        # Coarse stations retain >192m of terminal/landing envelope.
        for k, z in enumerate(range(int(f.z0)+32, int(f.z0)+f.n-32, 16)):
            xx = (f.x0+32, f.x0+f.n-33)
            nodes.extend([[x, float(z)] for x in (xx if k % 2 == 0 else xx[::-1])])
        out.append({"id": f"north-search-survey-{w.fid}", "patch": f.id, "nodes": nodes})
    return out


def general_main(argv=None):
    """Pipeline front door: base network, derived itinerary, measured north."""
    import argparse
    import json
    import field as fieldmod

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", required=True)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--locations", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--itinerary", help="stage 9 provisional itinerary, required at final routing")
    ap.add_argument("--preset", default=os.environ.get("VH_NORTH_PRESET", "pre-kall"))
    ap.add_argument("--provisional", action="store_true")
    ap.add_argument("--census")
    worldseed.add_arguments(ap)
    args = ap.parse_args(argv)
    seed, world = worldseed.apply(args)
    import network as net
    import poi
    import precensus
    fieldmod.require_field(args.field, seed)
    fieldmod.require_seed(args.field, seed)
    sf = fieldmod._site_finder()
    grid = sf.load_grid(Path(args.grid))
    worldseed.require_scope("north coarse grid", args.grid, found_seed=grid.get("seed"))
    loc = json.loads(Path(args.locations).read_text())
    worldseed.require_scope("north location dump", args.locations, found_seed=poi.dump_seed(loc))
    if args.provisional == bool(args.census):
        ap.error("choose exactly one of --provisional and --census")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    old_argv = sys.argv
    plan_state = {}
    try:
        sys.argv = [str(HERE / "network.py"), "plan", "--field", args.field,
                    "--out", str(out), "--seed", seed, "--world", world]
        sys.argv += (["--provisional"] if args.provisional else ["--census", args.census])
        rc = net.main(plan_state=plan_state)
    finally:
        sys.argv = old_argv
    if rc:
        return rc
    names = worldseed.road_plan_names(seed)
    base = yaml.safe_load((out / names["segments"]).read_text())
    crossings = yaml.safe_load((out / names["crossings"]).read_text())
    fields = plan_state["fields"]
    census = None if args.provisional else precensus.require(Path(args.census), world=world, seed=seed)
    spawn = next((p for p in loc.get("locations", []) if p.get("name") == "StartTemple"), None)
    if spawn is None:
        raise ValueError("north itinerary: location dump has no StartTemple")
    coarse = coarse_route(grid, (float(spawn["x"]), float(spawn["z"])), preset=args.preset)
    require_search_coverage(coarse, fields)
    survey = search_survey_lines(coarse, fields)
    survey_cells = sorted({c for line in survey
                           for c in precensus.cells_for_band(line["nodes"], precensus.BAND_HALF_M)})
    if not args.provisional:
        if not args.itinerary:
            ap.error("final routing requires --itinerary from the pre-census stage")
        previous = load_itinerary(args.itinerary, provisional_ok=True)
        if previous["phase"] != coarse["phase"]:
            raise ValueError("north itinerary phase differs from the field/provisional route phase")
        if previous.get("height_source", {}).get("identity") != file_identity(args.field):
            raise ValueError("north alternative coverage field differs from the scoped provisional survey")
        unread = [list(c) for c in survey_cells if not census.has_cell(c)]
        if unread:
            raise ValueError(f"north alternative coverage census unread: {len(unread)} cells; "
                             "complete the stage-10 scoped union survey before selecting a frontier")
    initial_state = {k: copy.deepcopy(v) for k, v in plan_state.items()
                     if k not in ("fields", "water_cache")}
    replay = {"base": copy.deepcopy(base), "crossings": copy.deepcopy(crossings),
              "state": initial_state}
    original_nodes = copy.deepcopy(net.specmod.NODES)
    def evaluate(candidate_id):
        net.specmod.NODES.clear()
        net.specmod.NODES.update(copy.deepcopy(original_nodes))
        state = copy.deepcopy(initial_state)
        state.update(fields=fields, water_cache={})
        return evaluate_candidate(coarse, candidate_id, fields, copy.deepcopy(replay["base"]),
                                  copy.deepcopy(replay["crossings"]), net, census, state)
    try:
        (doc, base, crossings), rejected = select_frontier(coarse, evaluate)
    except CandidateRefusal as exc:
        refusal_path = out / f"north-frontier-refusal.{seed}.yaml"
        refusal_path.write_text(yaml.safe_dump({"seed": seed, "world": world,
            "phase": coarse["phase"], "stage": exc.stage,
            "measurements": json.loads(str(exc))}, sort_keys=False))
        print(f"REFUSED north frontier: {exc}", file=sys.stderr)
        return 4
    finally:
        net.specmod.NODES.clear()
        net.specmod.NODES.update(original_nodes)
    itinerary_path = (out / worldseed.north_path(seed).name if args.provisional
                      else worldseed.north_path(seed))
    inputs = {"field": args.field, "grid": args.grid, "locations": args.locations,
              "planner": __file__, "transport": HERE / "north_transport.py",
              "writer": HERE / "ribbon.py", "network": HERE / "network.py",
              "hull_geometry": JUMPSTART / "accept" / "sections" / "harbours.py"}
    if args.census:
        # Later build/census fixed points may refresh the live census path.
        # Replay must retain the exact reading that judged these candidates.
        import shutil
        identity = file_identity(args.census)
        snapshot = out / "north-frontier-inputs" / f"census-{identity['sha256']}.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            shutil.copyfile(args.census, snapshot)
        if file_identity(snapshot)["sha256"] != identity["sha256"]:
            raise ValueError("north frontier census changed while freezing replay inputs")
        inputs["census"] = snapshot
    from library import materialise
    import north_transport as NT
    body = materialise.locate_by_filename(NT.LIGHTHOUSE_BODY)
    if body is not None:
        inputs["lighthouse_body"] = body
    inputs["lighthouse_manifest"] = materialise.DATA / "library_manifest.json"
    inputs["waterfront"] = NT.WF.__file__
    inputs["dock_geometry"] = NT.A.__file__
    inputs["settlement_plan"] = worldseed.plan_path(seed)
    directory, pattern, _ = worldseed.installations_read(world)
    for index, installation in enumerate(sorted(directory.glob(pattern))):
        inputs[f"installation_{index:04d}"] = installation
    doc["frontier_search"] = {"search_step_m": SEARCH_STEP_M,
        "eligible_candidates": len(coarse["candidate_order"]), "rejected": rejected,
        "inputs": {k: file_identity(p) for k, p in inputs.items()},
        "replay": replay, "not_a_fine_maximum_proof": True}
    base.setdefault("census_lines", []).extend(survey)
    if args.provisional:
        wanted = base.setdefault("census_wanted", {})
        wanted.setdefault("legs", []).extend(
            {"id": line["id"], "patch": line["patch"],
             "cells": [list(c) for c in precensus.cells_for_band(line["nodes"], precensus.BAND_HALF_M)]}
            for line in survey)
        wanted["cells"] = sorted(set(survey_cells) | {tuple(c) for c in wanted.get("cells", [])})
        doc["provisional"] = True
    doc["height_source"] = {"field": str(Path(args.field).resolve()),
                            "identity": file_identity(args.field), "resolution_m": 1.0}
    doc["required_connections"] = base["connection_requirements"]
    doc["coarse_grid"] = str(Path(args.grid).resolve())
    doc["location_dump"] = str(Path(args.locations).resolve())
    base["north_itinerary"] = str(itinerary_path)
    for line in net.emit(out, {"segments": yaml.safe_dump(net.plain(base), sort_keys=False),
                               "crossings": yaml.safe_dump(net.plain(crossings), sort_keys=False)},
                         dry_run=False):
        print(line)
    itinerary_path.parent.mkdir(parents=True, exist_ok=True)
    itinerary_path.write_text(yaml.safe_dump(net.plain(doc), sort_keys=False))
    print(f"north: {len(doc['road_edges'])} required roads, {len(doc['routed_road_ids'])} routed; "
          f"{len(doc['transport'])} paired ferries, final departure {doc['final_departure']}; "
          f"{len(doc['route_refusals'])} refused")
    if doc.get("base_route_refusals"):
        print(f"north: {len(doc['base_route_refusals'])} base joint/writer "
              f"refusals retained for census; provisional survey only, "
              f"NOT construction authorization")
    if doc["route_refusals"] and not args.provisional:
        for rec in doc["route_refusals"]:
            print(f"REFUSED {rec['id']}: {rec['error']}", file=sys.stderr)
        return 4
    return 0


def _measured_vessel_regression(kind):
    """Real berths, dock supports and continuous voyages over a numeric bed."""
    import field as F
    import north_transport as NT
    from unittest.mock import patch
    n = 385
    heights = np.full((n, n), 26.0, np.float32)
    xx, zz = np.meshgrid(np.arange(n)-n//2, np.arange(n)-n//2)
    if kind in ("depth", "retry"):
        # Both endpoint centres float for a canoe; a deeper/wider hull does
        # not get to replace its own measured draught with that canoe's.
        heights[xx > 8] = 27.85
    elif kind == "beam":
        # The east-west lane is deep, but a wide beam clips the north bank.
        heights[(abs(xx) <= 8) & (zz == -13)] = NT.W.WATER_LEVEL
    elif kind in ("journey", "blocked"):
        # Endpoint berths remain deep. Only the actual sea journey sees this
        # shoal, which cannot be evaded by checking the two spawn poses.
        heights[abs(xx) <= 1] = 27.85 if kind == "journey" else 28.5
    nodes, edges, terminals = {}, [], {}
    for index, x in enumerate((-48.0, 48.0, 0.0)):
        z = -48.0 if index < 2 else 100.0
        a, b = f"approach-{index}", f"root-{index}"
        sid, tid = f"road-{index}", f"north-dock-{index:04d}"
        nodes[a], nodes[b] = {"xz": [x, z-16]}, {"xz": [x, z]}
        edges.append({"id": sid, "a": a, "b": b})
        terminals[tid] = {"xz": [x, z], "sea_xz": [x, z+24],
                          "node_id": b, "segment_id": sid,
                          "final_departure": index == 2}
        # A real six-metre approach bench, not a single artificially raised
        # vertex under the root. The dock's root rails must stand on land.
        bench = (abs(xx-x) <= 3) & (zz >= z-16) & (zz <= z)
        heights[bench] = NT.A.DECK_Y
    pair = {"id": "measured-ferry", "a": "north-dock-0000",
            "b": "north-dock-0001",
            "sea_route": [[-48.0, -16.0], [48.0, -16.0]]}
    doc = {"schema_version": 1, "seed": "Contract", "world": "Contract",
           "provisional": False, "nodes": nodes, "road_edges": edges,
           "terminals": terminals, "transport": [pair],
           "final_departure": "north-dock-0002"}
    raw = F.Field("measured-vessels", 0.0, 0.0, n/2, 1.0, n,
                  heights, np.ones((n, n), np.uint8))
    with worldseed.scope("Contract", "Contract"):
        surface, fields = planned_surface({raw.id: raw}, [], None)
        roots = {tid: list(t["xz"]) for tid, t in terminals.items()}
        def fixed(vessel, tid):
            trial = copy.deepcopy(doc)
            trial["terminals"][tid]["vessel"] = vessel
            return NT.plan_terminal(trial, tid, fields, surface=surface)
        if kind == "depth":
            # One deep endpoint is not permission to put the same deeper
            # vessel at its shallow peer. The first endpoint really fits.
            fixed("Karve", pair["a"])
            try:
                fixed("Karve", pair["b"])
            except (ValueError, SystemExit):
                pass
            else:
                return False
        elif kind in ("beam", "journey"):
            broad = [fixed("Karve", pair[end]) for end in ("a", "b")]
            try:
                NT.measure_passage(pair, broad, fields, surface=surface)
            except (ValueError, SystemExit):
                pass
            else:
                return False
        elif kind == "mismatched":
            unlike = [fixed("DoubleRowingCanoe", pair["a"]),
                      fixed("Karve", pair["b"])]
            try:
                NT.measure_passage(pair, unlike, fields, surface=surface)
            except (ValueError, SystemExit):
                return True
            return False
        if kind == "retry":
            # Change only enumeration order, not any physics/result provider:
            # every registered hull is attempted using its actual dimensions.
            # This catches a first-boat failure incorrectly rejecting a shore.
            order = list(reversed(NT.vessel_candidates(False)))
            visited = []
            def evaluate(candidate_id):
                visited.append(candidate_id)
                return select_voyage_vessel(doc, pair, fields, surface=surface)
            with patch.object(NT, "vessel_candidates", return_value=order):
                (plans, report), rejected = select_frontier(
                    {"candidate_order": [11, 12], "search_step_m": SEARCH_STEP_M},
                    evaluate)
            if visited != [11] or rejected:
                return False
        else:
            try:
                plans, report = select_voyage_vessel(doc, pair, fields, surface=surface)
            except ValueError as exc:
                return kind == "blocked" and "swept sea passage" in str(exc) and all(
                    "vessel" not in t for t in terminals.values())
            if kind == "blocked":
                return False
        vessel = plans[0]["boats"][0]["prefab"]
        return (vessel in NT.WF.VESSELS
                and all(p["boats"][0]["prefab"] == vessel for p in plans)
                and all(doc["terminals"][pair[end]]["vessel"] == vessel
                        for end in ("a", "b"))
                and report["vessel"] == vessel and report["swept_vertices"] > 0
                and report["min_depth_m"] >= report["required_depth_m"]
                and all(t["xz"] == roots[tid] for tid, t in terminals.items()))


def _joint_field_role_regression():
    """A real joint/writer pass joins canonical north fields to a role-named base."""
    from unittest.mock import patch
    import network as net
    import poi
    f = net.fieldmod.Field(
        "trunk-primary", 0.0, 0.0, 160.5, 1.0, 321,
        np.full((321, 321), 40.0, np.float32),
        np.ones((321, 321), np.uint8))
    nodes = {nid: {"xz": xz, "island": "spawn", "patch": f.id,
                   "kind": "road_junction"}
             for nid, xz in (("numeric-a", [0.0, 0.0]),
                             ("numeric-b", [32.0, 0.0]))}
    saved = (copy.deepcopy(net.specmod.NODES), net._PLAN_PATCHES,
             net._PLAN_KEEPOUTS, net._PLAN_PAD_KEEPOUTS)
    try:
        net._PLAN_PATCHES = net._PLAN_KEEPOUTS = net._PLAN_PAD_KEEPOUTS = None
        net.specmod.NODES.update(nodes)
        doc = {
            "attachment": {"node": "numeric-a"}, "nodes": copy.deepcopy(nodes),
            "terminals": {"end": {"node_id": "numeric-b", "xz": [32.0, 0.0]}},
            "road_edges": [{"id": "numeric-north", "a": "numeric-a",
                            "b": "numeric-b", "patch": f.id,
                            "cls": "trunk", "pri": 1}],
            "phase": {"allowed_biomes": ["meadows"]}, "frontier": {},
            "final_departure": "end", "transport": []}
        base, _ = net._ww_leg(
            f, net._ww_corridor(f), (-32.0, 0.0), (0.0, 0.0))()
        base.update(id="numeric-base", patch="mainland", cls="trunk", pri=1,
                    station_m=1.0, terrain_stations=len(base["nodes"]),
                    grade_max_measured=0.0, zones_carriageway=[],
                    abutments=[], **{"from": "numeric-source", "to": "numeric-a"})
        _, joint = net._jd_fixture(
            base["id"], base["nodes"], base["profile_y"])
        state = {"pads": [], "pads_missing": [],
                 "joints": {base["id"]: joint}, "water_cache": {}}
        # Empty environmental populations; routing, joint fitting and the
        # terrain writer remain the real implementations over the numeric bed.
        with patch.object(poi, "load", return_value=[]), \
                patch.object(net, "pad_claims", return_value=[]):
            result, _ = append_routes(
                doc, {f.id: f}, {"segments": [base]}, {}, net, None,
                plan_state=state)
        roads = {s["id"]: s for s in result["segments"]}
        return (set(roads) == {"numeric-base", "numeric-north"}
                and net.plan_components(list(roads.values()), bridges=[])
                == [["numeric-base", "numeric-north"]]
                and all(s["writer_water"]["final_verdict"] == "CLEAR"
                        for s in roads.values())
                and doc["route_refusals"] == [])
    finally:
        net.specmod.NODES.clear()
        net.specmod.NODES.update(saved[0])
        net._PLAN_PATCHES, net._PLAN_KEEPOUTS, net._PLAN_PAD_KEEPOUTS = saved[1:]


def _joint_phase_regression(kind, provisional):
    """Real protected-disc and incompatible-datum failures across both phases."""
    import network as net
    f = net.fieldmod.Field(
        "trunk-primary", 0.0, 0.0, 64.5, 1.0, 129,
        np.full((129, 129), 40.0, np.float32),
        np.ones((129, 129), np.uint8))
    base_height = 42.0 if kind == "base_writer" else 40.0
    north_height = 42.0 if kind in ("north_writer", "base_joint") else 40.0
    base, bj = net._jd_fixture(
        "numeric-base", [(float(x), 0.0) for x in range(-32, 1)],
        [base_height] * 33, t=[40.0] * 33, pri=3,
        pin=(32, 32, base_height) if kind == "base_joint" else None)
    north, nj = net._jd_fixture(
        "numeric-north", [(float(x), 0.0) for x in range(33)],
        [north_height] * 33, t=[40.0] * 33, pri=1,
        pin=(0, 0, north_height) if kind == "base_joint" else None)
    base.update(patch="mainland", **{"from": "numeric-source", "to": "numeric-split"})
    north.update(patch=f.id, **{"from": "numeric-split", "to": "numeric-end"})
    state = {"pads": [], "pads_missing": [], "joints": {base["id"]: bj},
             "water_cache": {}}
    saved = net._PLAN_PATCHES, net._PLAN_KEEPOUTS, net._PLAN_PAD_KEEPOUTS
    try:
        net._PLAN_PATCHES = net._PLAN_PAD_KEEPOUTS = None
        discs = ([(-16.0, 6.0, 3.0)] if kind == "base_writer" else
                 [(16.0, 6.0, 3.0)] if kind == "north_writer" else [])
        net._PLAN_KEEPOUTS = lambda zones: discs
        doc = {"segments": [base]}
        kept, refused, debt = _joint_routed_profiles(
            doc, [north], {north["id"]: nj}, {f.id: f}, net, state,
            provisional=provisional)
        north_fails = kind == "north_writer"
        expected_id = "numeric-north" if north_fails else "numeric-base"
        diagnostic_kind = "joint" if kind == "base_joint" else "writer"
        records = debt if provisional and not north_fails else refused
        matching = [r for r in records
                    if r["id"] == expected_id and r["kind"] == diagnostic_kind]
        if not matching:
            return False
        diagnostic = matching[0]["diagnostic"]
        measured_failure = (diagnostic["junction_spread_m"] > 1.0
                            if diagnostic_kind == "joint"
                            else diagnostic["steps"] > 0
                            and diagnostic["keepout_steps"] > 0)
        base_retained = any(s["id"] == "numeric-base" for s in doc["segments"])
        north_retained = any(s["id"] == "numeric-north" for s in kept)
        return (measured_failure
                and base_retained == (provisional or north_fails)
                and north_retained == (not north_fails)
                and (not refused if provisional and not north_fails else not debt)
                and (base["junction_datum"]["verdict"] == "REFUSED"
                     if kind == "base_joint" else
                     (north if north_fails else base)["writer_water"]["final_verdict"]
                     == "REFUSED"))
    finally:
        net._PLAN_PATCHES, net._PLAN_KEEPOUTS, net._PLAN_PAD_KEEPOUTS = saved


def selftest():
    """Deterministic known-bad cases; no scans, server, ledger or world writes."""
    import copy
    import tempfile
    cases = []
    def case(name, fn):
        try:
            ok = bool(fn())
        except Exception as exc:
            print(f"BAD {name}: {exc}")
            cases.append(False)
            return
        print(f"{'OK' if ok else 'BAD'} {name}")
        cases.append(ok)
    case("joint north writer resolves the base road's field role",
         _joint_field_role_regression)
    for kind in ("base_writer", "base_joint", "north_writer"):
        for provisional in (True, False):
            case(f"{kind} keeps measured refusal with provisional={provisional}",
                 lambda kind=kind, provisional=provisional:
                 _joint_phase_regression(kind, provisional))
    case("pre-kall uses actual preset Fader key and excludes Deep North",
         lambda: "defeated_fader" in phase_policy("pre-kall")["global_keys"]
         and "deepnorth" not in phase_policy("pre-kall")["allowed_biomes"])
    case("pre-eikthyr does not inherit later biomes",
         lambda: phase_policy("pre-eikthyr")["allowed_biomes"] == ["meadows"])
    def frontier_case():
        h = np.zeros((17, 17), np.float32)
        b = np.full((17, 17), 8, np.uint8)
        h[7:16, 6:11] = 35.0
        b[7:14, 6:11] = 1
        b[14:16, 6:11] = 7
        grid = dict(seed="Contract", step=32, extent=256, height=h, biome=b)
        r = coarse_route(grid, (0.0, 0.0), preset="pre-kall")
        return r["frontier"]["coarse_northing_m"] == 176.0 and all(
            p[1] < 208.0 for p in r["path"])
    case("higher forbidden Deep North shore cannot win the frontier search", frontier_case)
    def windows_case():
        r = {"path": [[0.0, float(z), False] for z in range(0, 1025, 32)]}
        windows = route_windows(r)
        return len(windows) > 1 and all(any(
            abs(x-w.cx) + 96 <= w.half and abs(z-w.cz) + 96 <= w.half
            for w in windows) for x, z, _ in r["path"])
    case("north corridor is not truncated to the old spawn-window extent", windows_case)
    def measured_fallback(kind):
        import field as F
        import route as R
        import north_transport as NT
        search = {"candidate_order": [11, 12], "search_step_m": SEARCH_STEP_M}
        def raster(height, n=257):
            return F.Field("constructed", 0.0, 0.0, n/2.0, 1.0, n,
                           np.full((n, n), height, np.float32),
                           np.ones((n, n), np.uint8))
        def evaluate(candidate_id):
            first = candidate_id == 11
            try:
                if kind == "lighthouse":
                    from types import SimpleNamespace
                    f = raster(50.0)
                    if first:
                        f.h[:] += np.arange(f.n, dtype=np.float32)[None, :] * 0.5
                    surface, _ = planned_surface({f.id: f}, [], None)
                    seat = NT.lighthouse_seat(surface, [0.0, 0.0], 0.0,
                                              SimpleNamespace(size_x=8.0, size_z=8.0))
                    if seat is None:
                        raise ValueError("real lighthouse footprint has no dry level seat")
                    return seat
                if kind == "landing":
                    f = raster(35.0 if first else 30.75)
                    return _landing_point({f.id: f}, [0.0, 0.0], [0.0, 24.0], {"meadows"})
                if kind == "road":
                    corr = R.Corridor(raster(35.0, n=65), cell_m=1.0)
                    corr.forbid = np.zeros((corr.m, corr.m), bool)
                    if first:
                        corr.forbid[:, 32] = True
                    path = corr._astar((32, 2), (32, 62))
                    if path is None:
                        raise ValueError("measured A* has no path through the protected wall")
                    return path
                if kind == "dock":
                    f = raster(NT.A.DECK_Y-1.0 if first else NT.A.DECK_Y)
                    NT._root_access(f.at(0.0, 0.0), NT.A.DECK_Y, "constructed dock")
                    return candidate_id
                f = raster(29.0 if first else 20.0)
                wf = NT.W.Field(f.id, f.cx, f.cz, f.half, f.step, f.n, f.h, f.biome)
                surface = NT.WF.Surface.__new__(NT.WF.Surface)
                surface.fields, surface.field_names = {f.id: wf}, ["constructed measured raster"]
                depths = NT.WF.hull_depths(surface, 0.0, 0.0, 0.0, "VikingShip")
                if not NT._hull_usable(depths, "VikingShip"):
                    raise ValueError(f"measured full-hull depths fail: {depths[:4]}")
                return depths[:4]
            except (ValueError, SystemExit) as exc:
                raise CandidateRefusal("road" if kind == "road" else
                                       "landing" if kind == "landing" else "transport", exc) from exc
        selected, failures = select_frontier(search, evaluate)
        return bool(selected) and [r["candidate_id"] for r in failures] == [11]
    for kind in ("landing", "road", "dock", "hull", "lighthouse"):
        case(f"real {kind} predicate rejects the highest shore and permits the later shore",
             lambda kind=kind: measured_fallback(kind))
    for kind, label in (
            ("depth", "a measured shallower Odin hull must fit BOTH ferry endpoints"),
            ("beam", "a measured narrower hull survives a real sea-lane beam obstruction"),
            ("journey", "deep endpoint berths cannot excuse a shallower sea journey"),
            ("blocked", "all measured hulls refuse a shoal on the actual voyage"),
            ("mismatched", "different feasible boats at each endpoint are not one usable ferry"),
            ("retry", "failed earlier hulls cannot skip a physically feasible higher shore")):
        case(label, lambda kind=kind: _measured_vessel_regression(kind))
    def exhausted():
        search = {"candidate_order": list(range(65)), "search_step_m": SEARCH_STEP_M}
        visited = []
        def evaluate(candidate_id):
            visited.append(candidate_id)
            raise CandidateRefusal("road", f"measured refusal {candidate_id}")
        try:
            select_frontier(search, evaluate)
        except CandidateRefusal as exc:
            evidence = json.loads(str(exc))
            return (exc.stage == "exhausted" and visited == search["candidate_order"]
                    and [r["candidate_id"] for r in evidence["rejected"]] == visited
                    and evidence["not_a_fine_maximum_proof"])
        return False
    case("all candidates fail with explicit reasons, without a top-N retry cap", exhausted)
    def alternative_coverage():
        from types import SimpleNamespace
        search = {"path": [[0.0, 0.0, False], [1536.0, 2048.0, False]]}
        windows = route_windows(search)
        fields = {w.fid: SimpleNamespace(cx=w.cx, cz=w.cz, half=w.half, step=w.step)
                  for w in windows}
        require_search_coverage(search, fields)
        for foreign in ("absent", "foreign", "old-half-grid"):
            bad = copy.deepcopy(fields)
            if foreign == "foreign":
                bad[windows[-1].fid].cz += 512.0
            elif foreign == "old-half-grid":
                bad[windows[-1].fid].cx += 0.5
            else:
                bad.pop(windows[-1].fid)
            try:
                require_search_coverage(search, bad)
            except ValueError:
                continue
            return False
        return True
    case("absent or foreign alternative fine coverage refuses the entire search", alternative_coverage)
    def reconstructible():
        search = {"candidate_order": [2, 3], "frontier": {"coarse_cell_m": 32.0},
                  "corridor_tree": {1: [0.0, 0.0, False, None],
                                    2: [0.0, 64.0, False, 1],
                                    3: [512.0, 32.0, False, 1]}}
        return (route_for_candidate(search, 3)["path"] == [
            [0.0, 0.0, False], [512.0, 32.0, False]]
            and any(w.cx > 500.0 for w in route_windows(search)))
    case("shared predecessor tree preserves and covers an alternative corridor", reconstructible)
    valid = {"schema_version": 1, "seed": "Contract", "world": "Contract",
             "provisional": False, "nodes": {"a": {"xz": [0, 0]}, "b": {"xz": [0, 10]}},
             "road_edges": [{"id": "r", "a": "a", "b": "b", "patch": "fine"}],
             "routed_road_ids": ["r"], "route_refusals": [], "transport": [],
             "terminals": {"final": {"xz": [0, 10], "node_id": "b", "segment_id": "r",
                                      "sea_xz": [0, 20], "final_departure": True}},
             "final_departure": "final"}
    def rejected(change):
        doc = copy.deepcopy(valid); change(doc)
        with tempfile.TemporaryDirectory() as tmp, worldseed.scope("Contract", "Contract"):
            path = Path(tmp) / "itinerary.yaml"
            path.write_text(yaml.safe_dump(doc))
            try:
                load_itinerary(path)
            except (ValueError, SystemExit):
                return True
        return False
    case("foreign itinerary refuses instead of borrowing fixture geography",
         lambda: rejected(lambda d: d.update(seed="Foreign")))
    case("provisional itinerary cannot be built",
         lambda: rejected(lambda d: d.update(provisional=True)))
    case("unresolved base survey debt cannot become a final itinerary",
         lambda: rejected(lambda d: d.update(
             base_route_refusals=[{"id": "base", "kind": "writer"}])))
    case("unread provisional declaration fails closed",
         lambda: rejected(lambda d: d.pop("provisional")))
    case("missing final departure is not accepted as an empty transport job",
         lambda: rejected(lambda d: d.update(final_departure="missing")))
    case("lone nonfinal dock is not a ferry",
         lambda: rejected(lambda d: d["terminals"].update(other={
             "xz": [0, 0], "node_id": "a", "segment_id": "r", "final_departure": False})))
    case("missing required road-ground cannot shrink the population",
         lambda: rejected(lambda d: d.update(routed_road_ids=[])))
    case("road-to-dock intent away from its endpoint refuses",
         lambda: rejected(lambda d: d["terminals"]["final"].update(xz=[20, 10])))
    case("self-paired ferry refuses",
         lambda: rejected(lambda d: d.update(transport=[{"id": "bad", "a": "final", "b": "final"}])))
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if sys.argv[1:] == ["selftest"] else general_main())
