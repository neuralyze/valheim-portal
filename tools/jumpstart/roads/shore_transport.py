"""Paired coast/boat search over explicitly requested seed-derived fine fields."""
from __future__ import annotations

import heapq
import math

import numpy as np
from scipy import ndimage


def _source_reach(graph, anchor, xs, zs):
    """RoadGraph's exact allowed/snap/strong-component rules, rooted at anchor.

    The largest component is not necessarily the destination island. Reusing its
    label as a ferry test would mark every smaller legitimate destination absent.
    """
    corr = graph.corr
    ai, aj = corr.cell_of(*anchor)
    source = int(graph.si[ai, aj]), int(graph.sj[ai, aj])
    if (graph.dist[ai, aj] * corr.cell_m > graph.snap_m
            or not graph.ok[source]):
        return np.zeros(xs.shape, dtype=bool)
    target_label = graph.lab[source]
    i = np.rint((zs - corr.z0) / corr.cell_m).astype(int)
    j = np.rint((xs - corr.x0) / corr.cell_m).astype(int)
    inside = (i >= 0) & (j >= 0) & (i < corr.m) & (j < corr.m)
    reached = np.zeros(xs.shape, dtype=bool)
    ii, jj = i[inside], j[inside]
    pi, pj = graph.si[ii, jj], graph.sj[ii, jj]
    reached[inside] = ((graph.dist[ii, jj] * corr.cell_m <= graph.snap_m)
        & graph.ok[pi, pj] & (graph.lab[pi, pj] == target_label))
    return reached


def _water_route(field, start, goal, vessel, *, radius_m=None):
    """Measured-water A* proposal; the shared swept-hull gate is final authority.

    Search has the requested fine rectangle's actual bounds. The proposal is
    conservative across 4m blocks and never interpolates missing survey space.
    Turn/connector feasibility is measured afterward, not inferred from this mask.
    """
    from settlements import waterfront as WF
    need = WF.VESSELS[vessel]["draught_m"] + WF.KEEL_CLEARANCE_M + WF.BERTH_MARGIN_FLOOR_M
    wet = np.isfinite(field.heights) & (30.0 - field.heights >= need)
    if radius_m is not None:
        xs2 = field.world_x(np.arange(field.n)) ** 2
        for first in range(0, field.n, 256):
            last = min(first+256, field.n)
            zs2 = field.world_z(np.arange(first, last)) ** 2
            wet[first:last] &= xs2[None, :] + zs2[:, None] <= radius_m**2
    clearance = ndimage.distance_transform_edt(wet)
    beam = WF.VESSELS[vessel]["hull_w"] / 2 + 0.75
    ok = ndimage.minimum_filter(clearance, size=5, mode="constant", cval=0)[::4, ::4] >= beam
    step = 4.0
    n, m = ok.shape

    def snap(point):
        i = int(round((point[1] - field.z0) / step))
        j = int(round((point[0] - field.x0) / step))
        r = 24  # the existing 96m terminal/hull survey envelope
        rows, cols = np.nonzero(ok[max(0, i-r):min(n, i+r+1),
                                  max(0, j-r):min(m, j+r+1)])
        if not len(rows):
            raise ValueError(f"no measured navigable full-beam route cell within 96m of boat {point}")
        rows += max(0, i-r); cols += max(0, j-r)
        k = int(np.argmin((rows-i)**2 + (cols-j)**2))
        return int(rows[k]), int(cols[k])

    source, target = snap(start), snap(goal)
    q = [(0.0, 0.0, source)]
    distances = {source: 0.0}; previous = {}
    while q:
        _, distance, node = heapq.heappop(q)
        if distance != distances.get(node):
            continue
        if node == target:
            path = [node]
            while path[-1] != source:
                path.append(previous[path[-1]])
            return [[field.x0 + j*step, field.z0 + i*step]
                    for i, j in reversed(path)]
        i, j = node
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ni, nj = i+di, j+dj
            if not (0 <= ni < n and 0 <= nj < m and ok[ni, nj]):
                continue
            if di and dj and not (ok[i, nj] and ok[ni, j]):
                continue
            candidate = distance + step * (math.sqrt(2) if di and dj else 1)
            nxt = ni, nj
            if candidate < distances.get(nxt, math.inf):
                distances[nxt] = candidate; previous[nxt] = node
                heuristic = step * math.hypot(ni-target[0], nj-target[1])
                heapq.heappush(q, (candidate+heuristic, candidate, nxt))
    raise ValueError(f"no measured water route between {start} and {goal} inside complete requested {field.id}")


def _endpoint_plan(site, seed, world):
    boat = site["boat"]
    return dict(terminal_id=site["id"], seed=seed, world=world,
        final_departure=False, boats=[dict(prefab=boat["prefab"],
            pos=[boat["x"], boat["y"], boat["z"]], yaw_deg=boat["yaw"])])


def solve_pair(nodes, fields, search, complete, *, occupied=()):
    """Both endpoint/road populations use component-complete rendered identity.

    Local labels never certify separation; every destination's identity and
    major-landmass area come from the complete measured playable world.
    """
    import shores as S
    import north_transport as NT
    import worldseed
    source_label = complete.at(*nodes["eastlobe"]["xz"])
    if source_label == 0:
        raise SystemExit("REFUSING ferry: generated east-lobe anchor has no rendered component")
    if not search:
        raise SystemExit("REFUSING ferry-east-isle: this seed's whole-world component survey has no eastern disconnected landmass meeting the existing major-landmass area floor; required east travel remains physically unmet (no tiny-rock or portal substitute)")
    source_req = dict(next(r for r in S.REQUIREMENTS if r["node"] == "ferry-east"))
    failures = []
    for requirement in search:
        fid = requirement["search_patch"]
        if fid not in fields:
            raise SystemExit(f"REFUSING ferry-east-isle: missing required fine alternative {fid}; rerun field.request_for_seed, never omit this destination")
        field = fields[fid]
        probe = tuple(requirement["probe_xz"])
        S._coverage(field, probe)
        fine_label = int(requirement["rendered_component"])
        if fine_label == source_label:
            failures.append(dict(component=fine_label,
                reason="complete rendered world connects the two shores outside the local search; no disconnected ferry destination"))
            continue
        if (fine_label >= len(complete.area_m2)
                or complete.area_m2[fine_label] < requirement["min_area_m2"]):
            failures.append(dict(component=fine_label, reason="not an actual major rendered landmass"))
            continue
        projected = complete.labels_for(field)
        destination = projected == fine_label
        inland = ndimage.distance_transform_edt(destination)
        ii, jj = np.nonzero(inland >= 25.0)
        xs, zs = field.world_x(jj), field.world_z(ii)
        valid = np.hypot(xs-probe[0], zs-probe[1]) <= S.SEARCH_RADIUS_M
        indices = np.nonzero(valid)[0]
        if not len(indices):
            failures.append(dict(component=fine_label,
                reason="complete rendered destination has no 25m inland road seat in its requested facing-shore envelope"))
            continue
        order = indices[np.argsort(np.hypot(xs[indices]-probe[0], zs[indices]-probe[1]), kind="stable")]
        far_graph = S.geography.RoadGraph(S.F.Field(field.id, field.cx, field.cz,
            field.half, field.step, field.n, field.heights, field.biomes), cls="spur")
        anchors, seen = [], set()
        for k in order:
            ci, cj = far_graph.corr.cell_of(float(xs[k]), float(zs[k]))
            gi, gj = far_graph.si[ci, cj], far_graph.sj[ci, cj]
            if (far_graph.dist[ci, cj]*far_graph.corr.cell_m > far_graph.snap_m
                    or not far_graph.ok[gi, gj]):
                continue
            road_component = int(far_graph.lab[gi, gj])
            if road_component not in seen:
                seen.add(road_component)
                anchors.append((fine_label, (float(xs[k]), float(zs[k]))))
        for fine_label, inland_anchor in anchors:
            far_id = "ferry-eastisle-inland"
            far_nodes = dict(nodes, **{far_id: dict(xz=inland_anchor,
                patch=fid, island=f"ferry-component-{fine_label}",
                kind="extremity", level_y=field.height_at(*inland_anchor))})
            far_req = dict(node="ferry-terminal-eastisle", id="ferry-terminal-eastisle",
                anchor=far_id, kind="jetty", role="ferry_terminal_place",
                cls="spur", width_tiles=2, survey_patch=fid,
                search_center=list(probe))
            src_req = dict(source_req)
            selected = {}
            last = []

            def accept_source(source):
                if not S.clears_sites(source, occupied):
                    return False
                def accept_far(far):
                    if not S.clears_sites(far, [*occupied, source]):
                        return False
                    start = [source["boat"]["x"], source["boat"]["z"]]
                    goal = [far["boat"]["x"], far["boat"]["z"]]
                    plans = {s["id"]: _endpoint_plan(s, worldseed.seed(), worldseed.world())
                             for s in (source, far)}
                    # Try the shortest chord first; if blocked, search measured
                    # water around the obstacle, then judge its complete sweep.
                    proposals = [[start, goal]]
                    for pass_index in (0, 1):
                        try:
                            route = proposals[0] if pass_index == 0 else water_route(
                                complete.field, start, goal, S.VESSEL, radius_m=complete.radius_m)
                            pair = dict(id="ferry-east-isle", a=source["id"], b=far["id"],
                                        sea_route=route)
                            proof = NT.measure_passage(pair, plans, fields,
                                                      surface=S.GeneratedSurface(fields))
                        except (SystemExit, ValueError) as exc:
                            last[:] = [str(exc)]
                            continue
                        source["pair_requirement"] = dict(pair, status="measured", measurement=proof)
                        far["access_anchor"] = dict(node=far_id, xz=list(inland_anchor),
                            patch=fid, island=far_nodes[far_id]["island"], kind="extremity",
                            level_y=far_nodes[far_id]["level_y"])
                        far["destination_component"] = dict(requirement,
                            fine_label=fine_label, inland_anchor_xz=list(inland_anchor))
                        selected.update(source=source, far=far)
                        return True
                    return False
                try:
                    S.solve_site(far_req, far_nodes, fields, component=fine_label,
                                 accept=accept_far, complete=complete,
                                 graph_factory=lambda _field, cls: far_graph)
                    return True
                except SystemExit as exc:
                    last[:] = [str(exc)]
                    return False
            try:
                S.solve_site(src_req, nodes, fields, accept=accept_source, complete=complete)
                return [selected["source"], selected["far"]]
            except SystemExit as exc:
                failures.append(dict(component=fine_label, fine_component=fine_label,
                                     reason=str(exc), last_passage_refusal=last))
    raise SystemExit(f"REFUSING ferry-east-isle: every measured major-landmass/shore/boat/passage alternative failed: {failures}; paired transport requirement retained, not replaced by portal")


def selftest():
    """Constructed physical berth/passage arms using the production hull gate."""
    import shores as S
    import north_transport as NT
    n = 401
    ax = np.arange(n) - 200
    xx, zz = np.meshgrid(ax, ax)
    heights = np.where(abs(xx) >= 70, 34.0, 20.0).astype(np.float32)
    field = S.W.Field("paired-constructed", 0, 0, 200.5, 1, n, heights,
                     np.ones((n, n), dtype=np.uint8))
    surface = S.GeneratedSurface({field.id: field})
    def site(tid, x, yaw):
        return dict(id=tid, boat=dict(prefab=S.VESSEL, x=x, y=30.0, z=0.0, yaw=yaw))
    a, b = site("ferry-terminal-east", -30, 90), site("ferry-terminal-eastisle", 30, 270)
    plans = {s["id"]: _endpoint_plan(s, "constructed", "constructed") for s in (a, b)}
    pair = dict(id="ferry-east-isle", a=a["id"], b=b["id"],
                sea_route=[[-30.0, 0.0], [30.0, 0.0]])
    proof = NT.measure_passage(pair, plans, {field.id: field}, surface=surface)
    assert proof["min_depth_m"] >= proof["required_depth_m"]
    assert proof["hull_stations"] and proof["swept_vertices"]
    from dataclasses import replace
    shoal = heights.copy()
    shoal[(abs(xx) < 8) & (abs(zz) < 25)] = 29.0
    blocked = replace(field, heights=shoal)
    try:
        NT.measure_passage(pair, plans, {field.id: blocked},
                           surface=S.GeneratedSurface({field.id: blocked}))
    except SystemExit as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("paired boats hid a grounded full-hull voyage")
    proposal = water_route(blocked, (-30, 0), (30, 0), S.VESSEL)
    assert proposal and any(abs(p[1]) >= 25 for p in proposal)
    # Both boats without a measured connected route never satisfy the contract.
    try:
        S.require_pairs([dict(a, pair_requirement=dict(pair, status="required")), b])
    except SystemExit:
        pass
    else:
        raise AssertionError("unmeasured ferry pair authorized")
    print("paired shore physical geometry regressions passed")
    return 0


def water_route(field, start, goal, vessel, *, radius_m=None):
    """Grow the water search on already COMPLETE measured coverage as needed.

    Crops are zero-copy integer views of the same measured world, not newly
    asserted patches or boundaries capable of certifying land disconnection.
    """
    from dataclasses import replace
    padding = 128.0
    while True:
        j0 = max(0, int(math.floor((min(start[0], goal[0])-padding-field.x0))))
        i0 = max(0, int(math.floor((min(start[1], goal[1])-padding-field.z0))))
        j1 = min(field.n-1, int(math.ceil((max(start[0], goal[0])+padding-field.x0))))
        i1 = min(field.n-1, int(math.ceil((max(start[1], goal[1])+padding-field.z0))))
        n = min(field.n, max(j1-j0+1, i1-i0+1))
        j0, i0 = min(j0, field.n-n), min(i0, field.n-n)
        view = replace(field, cx=field.x0+j0+(n-1)/2,
            cz=field.z0+i0+(n-1)/2, half=n/2, n=n,
            heights=field.heights[i0:i0+n, j0:j0+n],
            biomes=field.biomes[i0:i0+n, j0:j0+n])
        try:
            return _water_route(view, start, goal, vessel, radius_m=radius_m)
        except ValueError:
            if n == field.n:
                raise
            padding *= 2
