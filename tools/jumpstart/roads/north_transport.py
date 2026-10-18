#!/usr/bin/env python3
"""Seed-scoped, measured north docks; no terrain edits and no portal substitute.

plan is geometry, not evidence of a built crossing. measure reads the live world;
accept/sections/north_transport independently judges the saved-world census.
Every build preflights BOTH ends before acquiring the normal LiveBuilder token.
Resume measures each source pose one-to-one. Completed pieces are preserved;
missing commands get their own truthful subset blob/record, bound to the
original full source digest. Unplanned pieces refuse; nothing is demolished.
The ledger's ordinary terrain/structure ownership guards are never overridden.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for directory in (ROOT, ROOT / "ledger", ROOT / "blueprints", ROOT / "crossings",
                  ROOT / "settlements", HERE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
import worldseed
import field as F
import water as W
import assemble as A
from settlements import waterfront as WF

ROLE = "north_transport"
LARGE_VESSELS = frozenset(k for k, v in WF.VESSELS.items()
                         if k in WF.ODIN_SHIPS and v["cls"] == "large")
LIGHTHOUSE_BODY = "drake-lighthouse.blueprint"
MIN_LIGHTHOUSE_HEIGHT_M = 15.0


def field_path(path=None) -> Path:
    value = path or os.environ.get("VH_NORTH_FIELD")
    if not value:
        raise SystemExit("north transport needs --field or VH_NORTH_FIELD: a seed-scoped, lattice-aligned 1m PatchScan covering every terminal and hull")
    return Path(value)


class ScopedFields(dict):
    """Loaded patches carrying their checked seed; shared across terminals."""
    def __init__(self, patches, seed, *, source=None, ignored_unaligned=()):
        super().__init__(patches)
        self.seed = seed
        self.source = str(source or "<loaded north field>")
        self.ignored_unaligned = tuple(ignored_unaligned)


def load_field(path, seed) -> dict:
    if isinstance(path, ScopedFields):
        if path.seed != seed:
            raise SystemExit("loaded north field belongs to another seed")
        return path
    path = field_path(path)
    F.require_seed(path, seed)
    fields = W.load(path)
    fine = {key: f for key, f in fields.items() if abs(f.step - 1.0) < 1e-6}
    if not fine:
        raise SystemExit(f"{path}: no measured 1m transport patches")
    aligned = {key: f for key, f in fine.items()
               if all(abs(v - round(v)) <= 1e-6 for v in (f.x0, f.z0))}
    ignored = sorted(set(fine) - set(aligned))
    if not aligned:
        raise SystemExit(f"{path}: no lattice-aligned 1m transport patches; ignored unaligned patches {ignored}; supply aligned coverage without resampling")
    return ScopedFields(aligned, seed, source=path, ignored_unaligned=ignored)


def _xz(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SystemExit(f"{label}: expected finite [x,z]")
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value):
        raise SystemExit(f"{label}: expected finite [x,z]")
    return tuple(float(v) for v in value)


def vessel_candidates(final):
    eligible = LARGE_VESSELS if final else WF.VESSELS
    return sorted(eligible, key=lambda v: (
        WF.VESSELS[v]["draught_m"],
        WF.VESSELS[v]["hull_w"] * WF.VESSELS[v]["hull_l"], v))


def _terminal_vessels(terminal):
    """Declared measured type, or the eligible registry choices for this berth."""
    declared = terminal.get("vessel")
    eligible = vessel_candidates(terminal["final_departure"])
    if declared is not None:
        if not isinstance(declared, str) or declared not in WF.VESSELS:
            raise SystemExit(f"unmeasured north vessel {declared!r}")
        if declared not in eligible:
            raise SystemExit(f"final departure requires a measured large Odin ship, not {declared}")
        return [declared]
    return eligible


def validate_itinerary(doc, *, seed, world, for_write=False, path="<itinerary>"):
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise SystemExit(f"{path}: north itinerary schema_version must be 1")
    if not isinstance(doc.get("world"), str) or not doc["world"]:
        raise SystemExit(f"{path}: north itinerary must declare its world")
    worldseed.require_scope("north itinerary", path, found_seed=doc.get("seed"),
                            found_world=doc.get("world"), seed_=seed, world_=world)
    if not isinstance(doc.get("provisional"), bool):
        raise SystemExit(f"{path}: provisional must be explicitly boolean")
    if for_write and (doc["provisional"] or doc.get("refusal")
                      or doc.get("route_refusals") or doc.get("base_route_refusals")):
        raise SystemExit(f"{path}: provisional/refused itinerary cannot authorize transport writes")
    nodes, terminals = doc.get("nodes"), doc.get("terminals")
    edges, pairs = doc.get("road_edges"), doc.get("transport")
    if not isinstance(nodes, dict) or not isinstance(terminals, dict) or not terminals or not isinstance(edges, list) or not isinstance(pairs, list):
        raise SystemExit(f"{path}: missing nodes, road_edges, terminals or transport population")
    by_edge = {}
    for edge in edges:
        if not isinstance(edge, dict) or not edge.get("id") or edge["id"] in by_edge:
            raise SystemExit(f"{path}: missing/duplicate road edge id")
        if edge.get("a") not in nodes or edge.get("b") not in nodes:
            raise SystemExit(f"{path}: road edge has missing node")
        by_edge[edge["id"]] = edge
    final = doc.get("final_departure")
    if final not in terminals:
        raise SystemExit(f"{path}: missing final departure terminal")
    marked = []
    for tid, terminal in terminals.items():
        if not isinstance(tid, str) or not tid.startswith("north-dock-") or not isinstance(terminal, dict):
            raise SystemExit(f"{path}: invalid stable terminal id {tid!r}")
        root, sea = _xz(terminal.get("xz"), tid), _xz(terminal.get("sea_xz"), tid + " sea")
        if math.dist(root, sea) < 2.0:
            raise SystemExit(f"{tid}: sea target must define a nonzero seaward bearing")
        edge = by_edge.get(terminal.get("segment_id"))
        if edge is None or root not in [_xz(nodes[n].get("xz"), n) for n in (edge["a"], edge["b"])]:
            raise SystemExit(f"{tid}: dock root must exactly equal its serving north road endpoint")
        if not isinstance(terminal.get("final_departure"), bool):
            raise SystemExit(f"{tid}: final_departure must be boolean")
        if terminal["final_departure"]:
            marked.append(tid)
        _terminal_vessels(terminal)
    if marked != [final]:
        raise SystemExit(f"{path}: final departure markers disagree")
    seen, paired = set(), set()
    for pair in pairs:
        if not isinstance(pair, dict) or not pair.get("id") or pair["id"] in seen:
            raise SystemExit(f"{path}: missing/duplicate transport id")
        seen.add(pair["id"])
        if pair.get("a") not in terminals or pair.get("b") not in terminals or pair["a"] == pair["b"]:
            raise SystemExit(f"{path}: transport requires two distinct declared terminals")
        route = pair.get("sea_route")
        if not isinstance(route, list) or len(route) < 2:
            raise SystemExit(f"{pair['id']}: a measured sea_route is required, not merely two berths")
        for point in route:
            _xz(point, pair["id"] + " sea route")
        paired.update((pair["a"], pair["b"]))
    if set(terminals) - paired - {final}:
        raise SystemExit(f"{path}: unpaired transport terminals {sorted(set(terminals) - paired - {final})}")
    return doc


def load_itinerary(path, seed, world, for_write=False):
    import yaml
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"north itinerary missing: {path}; run the seed's north planner, never borrow another world's plan")
    return validate_itinerary(yaml.safe_load(path.read_text()), seed=seed, world=world,
                              for_write=for_write, path=path)


class LedgerView:
    """Read existing ledger bytes/blobs without creating a manifest or store."""
    def __init__(self, world, seed):
        from ledger import schema
        self.root = ROOT / "ledger" / "runs" / world
        path = self.root / "ledger.jsonl"
        if not path.is_file():
            raise SystemExit(f"{world}: no existing build ledger; transport requires the normal road-ground build first")
        lines = path.read_text().splitlines()
        _seq, self.head = schema.chain_head(lines)
        self._records = [json.loads(line) for line in lines if line.strip()]
        manifest = self._records[0].get("params", {}) if self._records else {}
        worldseed.require_scope("transport ledger", path,
                                found_seed=manifest.get("seed_name"),
                                found_world=manifest.get("world"),
                                seed_=seed, world_=world)

    def records(self):
        return self._records

    def read_blob(self, sha):
        data = (self.root / "blobs" / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit(f"transport ledger blob {sha} digest mismatch")
        return data


class Surface(WF.Surface):
    """Use waterfront's sampler/hull machinery, never its fixture field union."""
    def __init__(self, fields, *, world, actor, led=None):
        import applied
        self.fields = fields
        self.field_names = [f"{fields.source} (aligned 1m only; ignored unaligned patches: {list(fields.ignored_unaligned)})"]
        self.applied = applied.Applied(world=world, actor=actor,
                                       led=led or LedgerView(world, fields.seed))
        self._hull_fields = {}
        self._hull_masks = {}

    def applied_patch_for(self, x, z, margin=2.0):
        from dataclasses import replace
        original = self.patch_for(x, z, margin)
        if original.id not in self._hull_fields:
            heights = original.heights.copy()
            # Only authored zones can differ. Connectivity must see their
            # applied deltas too, not a generated-only sandbar or escape route.
            for zx, zz in {w["zone"] for w in self.applied.writes}:
                x0, z0 = zx * 64 - 32, zz * 64 - 32
                for sz in range(max(z0, int(original.z0)),
                                min(z0 + 64, int(original.z0) + original.n - 1) + 1):
                    for sx in range(max(x0, int(original.x0)),
                                    min(x0 + 64, int(original.x0) + original.n - 1) + 1):
                        i, j = original.index(sx, sz)
                        heights[i, j] = original.heights[i, j] + self.applied.delta_at(sx, sz)
            self._hull_fields[original.id] = replace(original, heights=heights)
        return self._hull_fields[original.id]

    def hull_depths(self, bearing, x, z, vessel):
        from types import SimpleNamespace
        patch = self.applied_patch_for(x, z, margin=WF.VESSELS[vessel]["hull_l"])
        key = (patch.id, vessel)
        if key not in self._hull_masks:
            self._hull_masks[key] = WF.sea_mask(
                patch, WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M)[1]
        return WF.hull_depths(SimpleNamespace(patch_for=lambda *a, **k: patch),
                              bearing, x, z, vessel, open_sea=self._hull_masks[key])


def _piece_rows(commands):
    rows = []
    for command in commands:
        bits = shlex.split(command)
        if len(bits) < 2 or bits[0] != "spawn_object":
            raise SystemExit("north plan contains a non-spawn command")
        values = dict(part.split("=", 1) for part in bits[2:] if "=" in part)
        z, x, y = map(float, values["pos"].split(","))
        ry, rx, rz = map(float, values["rot"].split(","))
        rows.append(dict(prefab=bits[1], pos=[x, y, z], rot=[rx, ry, rz]))
    return rows


def _plan_op(tid, commands, anchor, segment, *, body=None):
    pieces = _piece_rows(commands)
    text = "\n".join(commands) + "\n"
    sha = hashlib.sha256(text.encode()).hexdigest()
    prefabs = dict(Counter(p["prefab"] for p in pieces))
    reach = max(math.hypot(p["pos"][0] - anchor[0], p["pos"][2] - anchor[2]) for p in pieces) + 3.0
    params = dict(role=ROLE, site_id=tid, plan_sha256=sha,
                  anchor=dict(x=anchor[0], y=anchor[1], z=anchor[2], yaw=0.0),
                  command_count=len(commands), prefabs=prefabs, reach_m=reach,
                  flatten="FORBIDDEN", flatten_reason="North transport stands on the measured applied bed; moving its ground or draining its berth is forbidden")
    if body:
        params["body"] = body
    return dict(op="spawn_plan", params=params, wire=commands,
                requires=dict(mods=["WorldEditCommands", "ServerDevcommands"], prefabs=sorted(prefabs), blobs=[sha]),
                expect=dict(prefab_count=[dict(prefab=p, pos=[anchor[0], anchor[2]], max=reach, count=n, tolerance=0) for p, n in sorted(prefabs.items())]),
                meta=dict(stands_on=[segment], north_terminal=tid))


def lighthouse_seat(surf, root, bearing, foot):
    """Pure measured siting shared by real body planning and terrain regressions."""
    ux, uz = A._unit(bearing)
    lx, lz = uz, -ux
    # Deterministic shore-side candidates, all outside the road/dock entrance.
    for back in (12.0, 20.0, 28.0, 36.0):
        for side in (16.0, -16.0, 24.0, -24.0):
            x, z = root[0] - ux * back + lx * side, root[1] - uz * back + lz * side
            heights = []
            for dx in range(math.floor(-foot.size_x / 2), math.ceil(foot.size_x / 2) + 1):
                for dz in range(math.floor(-foot.size_z / 2), math.ceil(foot.size_z / 2) + 1):
                    heights.append(surf.at(x + dx, z + dz)[0])
            if min(heights) > W.WATER_LEVEL + 0.5 and max(heights) - min(heights) <= 0.20:
                return [x, max(heights), z]
    return None


def _lighthouse(surf, root, bearing, tid, segment):
    """A real checksum-bound body via the production converter, no beacon prop.

    We do not create a pad or claim terrain. If no existing dry, near-flat seat
    fits, planning refuses and the ground owner must supply an honest solution.
    """
    from library import materialise
    import to_rcon_plan as TRP
    import base_geometry as BG
    body = materialise.locate_by_filename(LIGHTHOUSE_BODY)
    if body is None:
        raise SystemExit(f"{tid}: missing real lighthouse body {LIGHTHOUSE_BODY}; materialise the verified blueprint library")
    manifest = [row for row in materialise.load_rows()
                if LIGHTHOUSE_BODY in (row["name"], row.get("source_name"))]
    digest = hashlib.sha256(body.read_bytes()).hexdigest()
    # locate() deliberately returns a mismatched candidate for diagnostics;
    # that is not permission to build it under a newly invented digest.
    if len(manifest) != 1 or digest != manifest[0].get("sha256"):
        raise SystemExit(f"{tid}: lighthouse source does not match the verified library manifest")
    objects = TRP.read_objects(body)
    foot = BG.xz_footprint(objects, bearing)
    if foot.pivot_only:
        raise SystemExit(f"{tid}: lighthouse footprint lacks measured solid geometry")
    seat = lighthouse_seat(surf, root, bearing, foot)
    if seat is None:
        raise SystemExit(f"{tid}: no measured dry level lighthouse seat; unresolved ground ownership must be solved before building (no transport terrain bypass)")
    with tempfile.TemporaryDirectory(prefix="north-lighthouse-") as scratch:
        output = Path(scratch) / "lighthouse.plan"
        rc = TRP.main([str(body), "--at", *map(str, seat), "--rotate", str(bearing), "--strip-identity", "--out", str(output)])
        if rc:
            raise SystemExit(f"{tid}: real lighthouse blueprint preflight refused ({rc}); no override permitted")
        commands = output.read_text().splitlines()
        repairs = json.loads(Path(str(output) + ".repairs.json").read_text())
    pieces = _piece_rows(commands)
    height = max(p["pos"][1] for p in pieces) - min(p["pos"][1] for p in pieces)
    if height < MIN_LIGHTHOUSE_HEIGHT_M or len(pieces) < 100:
        raise SystemExit(f"{tid}: lighthouse is not a tall real blueprint body")
    if hashlib.sha256(body.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"{tid}: lighthouse body changed while converting; refusing inconsistent provenance")
    identity = dict(filename=body.name, sha256=digest)
    op = _plan_op(tid, commands, seat, segment, body=identity)
    op["meta"].update(lighthouse=True, converter_repairs=repairs)
    return dict(body=body.name, sha256=digest, anchor=seat, height_m=height, pieces=pieces), op


def _root_access(ground_y, deck_y, terminal):
    if not math.isfinite(ground_y) or abs(ground_y - deck_y) > 0.35:
        raise SystemExit(f"{terminal}: road root to dock step exceeds 0.35m; road ground owner must resolve it before transport")


def _hull_usable(depths, vessel):
    need = WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M
    return bool(depths and depths[2] and depths[3] > 0
                and math.isfinite(depths[0]) and depths[0] >= need)


def plan_terminal(itinerary, terminal_id, field, *, actor="NorthTransport", surface=None):
    validate_itinerary(itinerary, seed=itinerary.get("seed"), world=itinerary.get("world"))
    if terminal_id not in itinerary["terminals"]:
        raise SystemExit(f"unknown north terminal {terminal_id!r}")
    fields = load_field(field, itinerary["seed"])
    surf = surface or Surface(fields, world=itinerary["world"], actor=actor,
                              led=LedgerView(itinerary["world"], itinerary["seed"]))
    terminal = itinerary["terminals"][terminal_id]
    root = _xz(terminal["xz"], terminal_id)
    sea = _xz(terminal["sea_xz"], terminal_id)
    segment = terminal["segment_id"]
    bearing = math.degrees(math.atan2(sea[0] - root[0], sea[1] - root[1])) % 360
    ux, uz = A._unit(bearing)
    lx, lz = uz, -ux
    deck_y = A.DECK_Y
    ground = lambda x, z: surf.at(x, z)[0]
    _root_access(ground(*root), deck_y, terminal_id)
    bed = A.Bed.applied(ground, "explicit north field plus this world's ledger in file order")
    chosen = None
    attempts = []
    # Each registry hull needs its own measured along-dock seat and draught.
    for vessel in _terminal_vessels(terminal):
        vf = WF.VESSELS[vessel]
        for length in range(max(12, int(math.ceil(math.dist(root, sea) / 2)) * 2), 98, 2):
            bx = root[0] + ux * (length - vf["hull_l"] / 2) - lx * (2 + vf["hull_w"] / 2 + 0.75)
            bz = root[1] + uz * (length - vf["hull_l"] / 2) - lz * (2 + vf["hull_w"] / 2 + 0.75)
            if abs(bx - root[0]) + vf["hull_l"] / 2 + vf["hull_w"] / 2 > 96 or abs(bz - root[1]) + vf["hull_l"] / 2 + vf["hull_w"] / 2 > 96:
                continue
            depths = surf.hull_depths(bearing, bx, bz, vessel)
            attempts.append(dict(vessel=vessel, length_m=length,
                                 min_depth_m=None if depths is None else depths[0],
                                 connected=False if depths is None else depths[2],
                                 required_depth_m=vf["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M))
            if _hull_usable(depths, vessel):
                chosen = (length, bx, bz, depths)
                break
        if chosen is not None:
            break
    if chosen is None:
        requirement = "large Odin ship" if terminal["final_departure"] else "measured vessel"
        raise SystemExit(f"{terminal_id}: no connected full-hull berth alongside the dock for an eligible {requirement}; measured candidates={attempts}; no boat may be silently omitted")
    length, bx, bz, depths = chosen
    pieces = A.jetty(root, bearing, length, deck_y, bed, width_tiles=2, name=terminal_id)
    verdict = A.verify(pieces, bed, note=terminal_id)
    if not verdict["ok"]:
        raise SystemExit(f"{terminal_id}: dock support refused: {verdict['failures'][:4]}")
    commands = [A.command(p) for p in pieces]
    dock_op = _plan_op(terminal_id, commands, [root[0], deck_y, root[1]], segment)
    dock_op["meta"]["structural_verdict"] = verdict
    hull = dict(min_depth_m=depths[0], max_depth_m=depths[1], connected=depths[2], samples=depths[3], required_depth_m=vf["draught_m"] + WF.KEEL_CLEARANCE_M)
    boat = WF.boat_op(terminal_id, vessel, bx, bz, bearing, hull)
    boat["params"]["role"] = ROLE
    boat["meta"].update(stands_on=[segment], north_terminal=terminal_id)
    walk_length = length - vf["hull_l"] / 2
    walk = [[root[0] + ux * t, root[1] + uz * t] for t in range(int(walk_length) + 1)]
    walk.append([root[0] + ux * walk_length - lx * 1.5, root[1] + uz * walk_length - lz * 1.5])
    lighthouse, ops = None, [dock_op, boat]
    if terminal["final_departure"]:
        lighthouse, op = _lighthouse(surf, root, bearing, terminal_id, segment)
        ops.append(op)
    all_rows = _piece_rows(commands) + (lighthouse["pieces"] if lighthouse else [])
    if any(max(abs(p["pos"][0] - root[0]), abs(p["pos"][2] - root[1])) > 94.0 for p in all_rows):
        raise SystemExit(f"{terminal_id}: structure extends beyond the terminal's measured ±96m census footprint")
    return dict(terminal_id=terminal_id, seed=itinerary["seed"], world=itinerary["world"],
                root_xz=list(root), segment_id=segment,
                walk_xz=walk, deck_y=deck_y, bearing_deg=bearing,
                ops=ops, pieces=_piece_rows(commands),
                boats=[dict(prefab=vessel, pos=boat["params"]["pos"], yaw_deg=bearing, hull=hull)],
                lighthouse=lighthouse, final_departure=terminal["final_departure"])


def _route_stations(points, start_yaw, end_yaw):
    previous = start_yaw
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length < 1e-8:
            continue
        yaw = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
        turn = (yaw - previous + 180) % 360 - 180
        turns = max(1, math.ceil(abs(turn) / 10))
        for k in range(turns + 1):
            yield a, previous + turn * k / turns
        count = max(1, math.ceil(length))
        for k in range(1, count + 1):
            yield [a[0] + (b[0] - a[0]) * k / count,
                   a[1] + (b[1] - a[1]) * k / count], yaw
        previous = yaw
    turn = (end_yaw - previous + 180) % 360 - 180
    turns = max(1, math.ceil(abs(turn) / 10))
    for k in range(turns + 1):
        yield points[-1], previous + turn * k / turns


def measure_passage(pair, terminals, fields, *, surface=None):
    """Measure ONE declared pair with the same full-hull predicate as writing."""
    from accept.sections import harbours as H
    by_id = (terminals if isinstance(terminals, dict) else
             {p["terminal_id"]: p for p in terminals})
    if pair.get("a") not in by_id or pair.get("b") not in by_id or pair["a"] == pair["b"]:
        raise SystemExit(f"{pair.get('id')}: passage needs two distinct measured endpoint plans")
    route = pair.get("sea_route")
    if not isinstance(route, list) or len(route) < 2:
        raise SystemExit(f"{pair.get('id')}: measured sea_route required")
    for point in route:
        _xz(point, pair["id"] + " sea route")
    endpoint_plans = [by_id[pair[t]] for t in ("a", "b")]
    boats = [p.get("boats", []) for p in endpoint_plans]
    if any(len(rows) != 1 for rows in boats):
        raise SystemExit(f"{pair['id']}: each measured endpoint must declare one boat")
    a, b = [rows[0] for rows in boats]
    vessel = a["prefab"]
    if vessel not in WF.VESSELS or b["prefab"] != vessel:
        raise SystemExit(f"{pair['id']}: both endpoint boats must match one declared measured vessel; got {vessel!r} and {b['prefab']!r}")
    for plan, boat in zip(endpoint_plans, (a, b)):
        if plan.get("final_departure") and boat["prefab"] not in LARGE_VESSELS:
            raise SystemExit(f"{pair['id']}: final departure requires a measured large Odin ship")
    if surface is None:
        seed, world = endpoint_plans[0]["seed"], endpoint_plans[0]["world"]
        if any((p["seed"], p["world"]) != (seed, world) for p in endpoint_plans):
            raise SystemExit(f"{pair['id']}: endpoint seed/world scopes disagree")
        fields = load_field(fields, seed)
        surface = Surface(fields, world=world, actor="NorthTransport")
    points = [[a["pos"][0], a["pos"][2]], *route, [b["pos"][0], b["pos"][2]]]
    minimum, count = math.inf, 0
    poses = []
    for xz, yaw in _route_stations(points, a["yaw_deg"], b["yaw_deg"]):
        depths = surface.hull_depths(yaw, *xz, vessel)
        poses.append((*xz, yaw))
        count += 1
        if not _hull_usable(depths, vessel):
            measured = None if depths is None else depths[0]
            connected = False if depths is None else depths[2]
            raise SystemExit(f"{pair['id']}: blocked full-hull passage at {xz}, heading {yaw:.2f}; min depth {measured}, connected={connected}, needs {WF.VESSELS[vessel]['draught_m'] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M:.3f}m")
        minimum = min(minimum, depths[0])
    spec = WF.VESSELS[vessel]
    hull = H.hull_table()[vessel]
    if not hull.profile:
        raise SystemExit(f"{pair['id']}: measured full-hull geometry is absent for {vessel}")
    vertices = H.swept_footprint(hull, poses)
    required = spec["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M
    for x, z in vertices:
        y = surface.at(x, z)[0]
        if not math.isfinite(y) or H.WATER_LEVEL-y < required:
            raise SystemExit(
                f"{pair['id']}: blocked swept hull at {(x, z)}; "
                f"depth {H.WATER_LEVEL-y}, needs {required:.3f}m")
        minimum = min(minimum, H.WATER_LEVEL-y)
    return dict(id=pair["id"], a=pair["a"], b=pair["b"], vessel=vessel,
                min_depth_m=minimum, required_depth_m=required,
                hull_stations=count, swept_vertices=len(vertices), waypoints=points)


def measure_passages(itinerary, plans, fields, *, surface=None, actor="NorthTransport"):
    validate_itinerary(itinerary, seed=itinerary.get("seed"), world=itinerary.get("world"))
    fields = load_field(fields, itinerary["seed"])
    surf = surface or Surface(fields, world=itinerary["world"], actor=actor)
    by_id = plans if isinstance(plans, dict) else {p["terminal_id"]: p for p in plans}
    for tid, terminal in itinerary["terminals"].items():
        plan = by_id.get(tid)
        boats = plan.get("boats", []) if plan else []
        if not boats or any(b["prefab"] not in _terminal_vessels(terminal) for b in boats):
            raise SystemExit(f"{tid}: missing boat or vessel differs from the declared measured type")
    return [measure_passage(pair, by_id, fields, surface=surf)
            for pair in itinerary["transport"]]


def _require_road_ground(itinerary, led):
    import yaml
    path = worldseed.segments_path(itinerary["seed"])
    if not path.is_file():
        raise SystemExit(f"missing merged north road contract {path}")
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope("north road segments", path, found_seed=doc.get("seed"), found_world=doc.get("world"), seed_=itinerary["seed"], world_=itinerary["world"])
    rows = {r["id"]: r for r in doc.get("segments", [])}
    records = led.records()
    from ledger.writer import retired_seqs
    retired = retired_seqs(records)
    laid = {r.get("meta", {}).get("segment") for r in records if r.get("op") == "terrain_write" and r.get("seq") not in retired and r.get("params", {}).get("role") == "road_segment"}
    for tid, terminal in itinerary["terminals"].items():
        sid = terminal["segment_id"]
        row = rows.get(sid)
        if row is None or sid not in laid:
            raise SystemExit(f"{tid}: serving road {sid} missing/ungraded; transport may not build around refused road ground")
        endpoints = row.get("nodes", [])
        if not endpoints:
            raise SystemExit(f"{tid}: serving road has no solved nodes")
        ends = [_xz([n["x"], n["z"]] if isinstance(n, dict) else n[:2], sid) for n in (endpoints[0], endpoints[-1])]
        if _xz(terminal["xz"], tid) not in ends:
            raise SystemExit(f"{tid}: merged road endpoint differs from the itinerary's exact root")


def measure(plans, *, builder):
    """Actual bounded live per-position reads, not an intended-count report."""
    result = []
    for plan in plans:
        rows = plan["pieces"] + (plan["lighthouse"]["pieces"] if plan["lighthouse"] else []) + plan["boats"]
        checks = []
        for row in rows:
            x, y, z = row["pos"]
            read = WF.read_back(builder.srv, row["prefab"], x, y, z, radius=0.5 if row not in plan["boats"] else 12.0)
            checks.append(dict(prefab=row["prefab"], pos=row["pos"], readback=read))
        result.append(dict(terminal_id=plan["terminal_id"], objects=checks,
                           acceptance="Run saved-world north_transport acceptance for physical walk/support/hull verdict; live readback alone is not acceptance"))
    return result


def reconciled_operations(builder, plan):
    """Preflight ALL terminal ops before any write, retaining source identity."""
    import copy
    from ledger import replay as R
    geometry = []
    for source in plan["ops"]:
        rows = R.north_spawn_rows(source["wire"])
        if source["op"] == "spawn":
            for row in rows:
                row["floating"] = True
        geometry.extend(rows)
    selected = []
    for source in plan["ops"]:
        op = copy.deepcopy(source)
        op.setdefault("meta", {})["north_geometry"] = geometry
        pending, probes = R.reconcile_north_spawns(builder.srv, op)
        if not pending:
            continue
        if source["op"] == "spawn_plan":
            text = "\n".join(source["wire"]) + "\n"
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest != source["params"]["plan_sha256"]:
                raise SystemExit("North source geometry digest changed")
            # The immutable source is durable even when execution is a subset.
            stored = builder.blob(text.encode(), note=plan["terminal_id"]+" full source geometry")
            if stored != digest:
                raise SystemExit("North source geometry blob identity changed")
            op["meta"]["north_source_plan"] = dict(
                sha256=digest, command_count=source["params"]["command_count"],
                prefabs=source["params"]["prefabs"])
            op["wire"] = pending
            op["params"]["command_count"] = len(pending)
            op["params"]["prefabs"] = dict(Counter(r["prefab"] for r in R.north_spawn_rows(pending)))
            subset = hashlib.sha256(("\n".join(pending)+"\n").encode()).hexdigest()
            op["params"]["plan_sha256"] = subset
            op["requires"]["blobs"] = sorted({digest, subset})
        op["meta"]["north_reconciliation"] = dict(
            source_commands=len(source["wire"]), planned_send_commands=len(pending),
            census=probes)
        selected.append(op)
    return selected


def _resume_regressions():
    """Exercise the real reconciliation/parser/sender on a mutable pose world."""
    from types import SimpleNamespace
    from ledger import replay as R
    class PoseWorld:
        dry = False
        rc = None
        def __init__(self, rows):
            self.rows = list(rows)
            self.sent = []
        def count(self, prefab, x, z, radius):
            return sum(r["prefab"] == prefab and math.hypot(
                r["pos"][0]-x, r["pos"][2]-z) <= radius
                       for r in self.rows), ""
        def command(self, command):
            bits = shlex.split(command)
            prefab = bits[bits.index("-prefab")+1]
            k = bits.index("-near")
            x, y, z, radius = map(float, bits[k+1:k+5])
            rows = [(i, r) for i, r in enumerate(self.rows)
                    if r["prefab"] == prefab and math.hypot(
                        r["pos"][0]-x, r["pos"][2]-z) <= radius]
            return f"Found {len(rows)} objects\n" + "\n".join(
                f"-Prefab: {r['prefab']} Id: {i}:1 Position: "
                f"({' '.join(map(str, r['pos']))}) Rotation: "
                f"({' '.join(map(str, r['rot']))})" for i, r in rows)
        def console_echo(self, command):
            self.sent.append(command)
            self.rows.extend(R.north_spawn_rows([command]))
            return "spawn accepted"
    commands = [
        "spawn_object wood_floor pos=0,0,31 rot=0,0,0 from=0,0,0",
        "spawn_object wood_floor pos=2,0,31 rot=0,0,0 from=0,0,0",
        "spawn_object wood_floor pos=4,0,31 rot=0,0,0 from=0,0,0"]
    op = _plan_op("fixture", commands, [0, 31, 0], "road")
    op["status"] = "applied"  # Must NOT suppress live evidence of the missing middle.
    source = R.north_spawn_rows(commands)
    world = PoseWorld([source[0], source[2]])
    blobs = {}
    def blob(data, **kwargs):
        digest = hashlib.sha256(data).hexdigest()
        blobs[digest] = data
        return digest
    builder = SimpleNamespace(srv=world, blob=blob)
    ops = reconciled_operations(builder, dict(terminal_id="fixture", ops=[op]))
    record = ops[0]
    _, checks, _ = R.send_and_verify(world, record)
    pending = reconciled_operations(builder, dict(terminal_id="fixture", ops=[op]))
    cases = [dict(
        name="partial terminal sends only missing piece and second resume sends nothing",
        ok=(len(ops) == 1 and world.sent == [commands[1]] and len(world.rows) == 3
            and world.rows[0] is source[0] and world.rows[1] is source[2]
            and not pending and all(c["ok"] for c in checks)
            and record["params"]["command_count"] == 1
            and record["meta"]["north_source_plan"]["sha256"] == op["params"]["plan_sha256"]
            and blobs[op["params"]["plan_sha256"]] == ("\n".join(commands)+"\n").encode()))]
    for label, extra in (
            ("duplicate completed pose refuses", source[0]),
            ("undesired extra pose refuses", dict(prefab="wood_floor", pos=[1, 31, 1], rot=[0, 0, 0])),
            ("wrong rotation at unchanged position refuses", dict(prefab="wood_floor", pos=[0, 31, 0], rot=[180, 0, 0]))):
        rows = source if label != "wrong rotation at unchanged position refuses" else source[1:]
        bad_world = PoseWorld([*rows, extra])
        try:
            reconciled_operations(SimpleNamespace(srv=bad_world, blob=blob),
                                  dict(terminal_id="fixture", ops=[op]))
            refused = False
        except R.Drift:
            refused = True
        cases.append(dict(name=label, ok=refused and not bad_world.sent))
    class TruncatedWorld(PoseWorld):
        def command(self, command):
            return super().command(command).rsplit("\n", 1)[0]
    truncated = TruncatedWorld(source)
    try:
        reconciled_operations(SimpleNamespace(srv=truncated, blob=blob),
                              dict(terminal_id="fixture", ops=[op]))
        refused = False
    except R.Drift:
        refused = True
    cases.append(dict(name="truncated live pose evidence refuses before send",
                      ok=refused and not truncated.sent))
    return cases


def _departure_preflight_regression(doc):
    """Real raster: a small Odin hull floats but every large Odin hull grounds."""
    import numpy as np
    from types import SimpleNamespace
    heights = np.full((513, 513), W.WATER_LEVEL-2.4, np.float32)
    field = W.Field("departure-preflight", 0.0, 0.0, 256.0, 1.0, 513,
                    heights, np.ones_like(heights, dtype=np.uint8))
    root = doc["terminals"][doc["final_departure"]]["xz"]
    heights[field.index(*root)] = A.DECK_Y
    fields = ScopedFields({field.id: field}, doc["seed"], source="constructed 1m raster")
    class RasterSurface(WF.Surface):
        def __init__(self):
            self.fields, self.field_names = fields, [fields.source]
            self.applied = SimpleNamespace(delta_at=lambda x, z: 0.0)
            self.masks = {}
        def hull_depths(self, yaw, x, z, vessel):
            if vessel not in self.masks:
                self.masks[vessel] = WF.sea_mask(
                    field, WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M)[1]
            return WF.hull_depths(self, yaw, x, z, vessel,
                                  open_sea=self.masks[vessel])
    surface = RasterSurface()
    small = surface.hull_depths(0, root[0]+15, root[1]+20, "DoubleRowingCanoe")
    try:
        plan_terminal(doc, doc["final_departure"], fields, surface=surface)
        refused, error = False, ""
    except SystemExit as exc:
        refused, error = True, str(exc)
    return dict(name="writer preflight refuses no eligible large Odin berth despite floating small Odin",
                ok=refused and _hull_usable(small, "DoubleRowingCanoe")
                   and "large Odin ship" in error and "measured candidates=" in error,
                small_hull_min_depth_m=small[0], refusal=error)


def selftest():
    """Pure known-bad contract regressions; never opens a ledger/server."""
    import copy
    doc = dict(schema_version=1, seed="Selftest", world="Selftest", provisional=False,
               nodes={"a": dict(xz=[0, 0]), "b": dict(xz=[0, 20]), "c": dict(xz=[100, 0]), "d": dict(xz=[100, 20])},
               road_edges=[dict(id="r0", a="a", b="b"), dict(id="r1", a="c", b="d")],
               terminals={"north-dock-000": dict(xz=[0, 20], sea_xz=[0, 40], segment_id="r0", final_departure=False), "north-dock-001": dict(xz=[100, 20], sea_xz=[100, 40], segment_id="r1", final_departure=True)},
               transport=[dict(id="crossing", a="north-dock-000", b="north-dock-001", sea_route=[[0,40], [100,40]])], final_departure="north-dock-001")
    cases = []
    def expect(label, mutation, refused):
        state = copy.deepcopy(doc)
        mutation(state)
        try:
            validate_itinerary(state, seed="Selftest", world="Selftest", for_write=True)
            got = False
        except SystemExit:
            got = True
        cases.append(dict(name=label, want_refused=refused, refused=got, ok=got == refused))
    expect("foreign seed", lambda d: d.update(seed="Foreign"), True)
    expect("foreign world", lambda d: d.update(world="Foreign"), True)
    expect("undeclared world", lambda d: d.pop("world"), True)
    expect("provisional transport writes", lambda d: d.update(provisional=True), True)
    expect("unresolved base roads cannot authorize final transport",
           lambda d: d.update(base_route_refusals=[{"id": "base", "kind": "writer"}]), True)
    expect("unresolved north roads cannot authorize final transport",
           lambda d: d.update(route_refusals=[{"id": "r0", "kind": "joint"}]), True)
    expect("unpaired crossing", lambda d: d.update(transport=[]), True)
    expect("nonendpoint dock", lambda d: d["terminals"]["north-dock-000"].update(xz=[1, 20]), True)
    expect("missing final ship lighthouse obligation", lambda d: d.update(final_departure=None), True)
    expect("missing peer terminal", lambda d: d["transport"][0].update(b="absent"), True)
    expect("missing measured sailing path", lambda d: d["transport"][0].pop("sea_route"), True)
    expect("valid paired contract", lambda d: None, False)
    expect("vanilla large ship cannot authorize final writer",
           lambda d: d["terminals"][d["final_departure"]].update(vessel="VikingShip"), True)
    expect("small Odin ship cannot authorize final writer",
           lambda d: d["terminals"][d["final_departure"]].update(vessel="LittleBoat"), True)
    expect("measured large Odin ship authorizes final contract",
           lambda d: d["terminals"][d["final_departure"]].update(vessel="CargoShip"), False)
    expect("invented boat cannot authorize writer",
           lambda d: d["terminals"][d["final_departure"]].update(vessel="ImaginaryOdinShip"), True)
    def standalone_final(state):
        state["terminals"].pop("north-dock-000")
        state["transport"] = []
    expect("standalone final departure before Deep North", standalone_final, False)
    for name, depths, want in (
            ("deep centre but shallow full hull", (1.0, 8.0, True, 50), False),
            ("deep landlocked pocket", (8.0, 8.0, False, 50), False),
            ("no measured hull samples", (8.0, 8.0, True, 0), False),
            ("margin inside measurement noise", (2.51, 8.0, True, 50), False),
            ("connected full hull with clearance", (3.0, 8.0, True, 50), True)):
        got = _hull_usable(depths, "MercantShip")
        cases.append(dict(name=name, ok=got == want, usable=got, want=want))
    for name, height, want in (("ungraded road root", 32.0, True),
                               ("unmeasured road root", float("nan"), True),
                               ("applied road meets deck", 30.6, False)):
        try:
            _root_access(height, 30.6, "north-dock-000")
            got = False
        except SystemExit:
            got = True
        cases.append(dict(name=name, refused=got, want_refused=want, ok=got == want))
    cases.extend(_resume_regressions())
    cases.append(_departure_preflight_regression(doc))
    print(json.dumps(cases, indent=2))
    return 0 if all(c["ok"] for c in cases) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("plan", "build", "measure", "selftest"))
    ap.add_argument("--itinerary")
    ap.add_argument("--field")
    ap.add_argument("--terminal")
    ap.add_argument("--actor")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="store_true")
    worldseed.add_arguments(ap)
    args = ap.parse_args(argv)
    if args.command == "selftest":
        return selftest()
    seed, world = worldseed.apply(args)
    if args.apply and args.command != "build":
        raise SystemExit("--apply is valid only for build")
    if args.command in ("build", "measure") and not args.actor:
        raise SystemExit("build/measure require the actual --actor")
    itinerary = load_itinerary(args.itinerary or worldseed.north_path(seed), seed, world,
                               for_write=args.command == "build")
    field = field_path(args.field)
    # All terminals are planned even for a single build unit: an unbuildable
    # peer never silently degenerates into a one-ended crossing.
    fields = load_field(field, seed)
    view = LedgerView(world, seed)
    surface = Surface(fields, world=world, actor=args.actor or "NorthTransport", led=view)
    plans = [plan_terminal(itinerary, tid, fields, actor=args.actor or "NorthTransport", surface=surface) for tid in itinerary["terminals"]]
    passages = measure_passages(itinerary, plans, fields, surface=surface,
                                 actor=args.actor or "NorthTransport")
    selected = plans if not args.terminal else [p for p in plans if p["terminal_id"] == args.terminal]
    if not selected:
        raise SystemExit(f"unknown terminal {args.terminal!r}")
    report = dict(schema_version=1, seed=seed, world=world, provisional=itinerary["provisional"], terminals=selected, passages=passages, applied=False)
    report["field"] = dict(path=fields.source, aligned_patches=sorted(fields),
                           ignored_unaligned_patches=list(fields.ignored_unaligned))
    if args.command == "measure" or (args.command == "build" and args.apply):
        from ledger.live import LiveBuilder
        with LiveBuilder(actor=args.actor, world=world) as builder:
            if builder.led.head != view.head:
                raise SystemExit("transport ground ledger changed after planning; reacquire measured plans before writing")
            if args.command == "build":
                _require_road_ground(itinerary, builder.led)
                pending = [(plan, reconciled_operations(builder, plan)) for plan in selected]
                for plan, ops in pending:
                    WF.emit(builder, ops, label=plan["terminal_id"])
                # Save has real physical postconditions for every ship endpoint.
                boat_checks = [op["expect"]["prefab_count"][0] for p in selected for op in p["ops"] if op["op"] == "spawn"]
                builder.emit("save", params=dict(role=ROLE), wire=["save"], requires=dict(mods=[], prefabs=[], blobs=[]), expect=dict(prefab_count=boat_checks))
                report["applied"] = True
            report["physical"] = measure(selected, builder=builder)
    text = json.dumps(report, indent=2, default=float) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
