"""Installation service land comes from its canonical writer footprint.

A waterfront centre may be wet and its nearest router cell may hit a sandbar.
The placement producer instead requires its written apron to touch the solved
portal hub's land. Measure that same contact; never substitute an authored
island name or enlarge the pad by combining duplicate installations.
"""
from __future__ import annotations

import math

import numpy as np


def installation_footprints(installations: dict, *, placements=None) -> dict:
    from terraform import sites
    import installation_sites

    records = sites.placements() if placements is None else placements
    by_name = {record['_qualified']: record for record in records}
    result = {}
    for node in ('stathub', 'dockshore'):
        if node not in installations:
            continue
        owners = {}
        for record in records:
            if record['id'] not in installation_sites.PLACEMENT_OF[node]:
                continue
            visited = set()
            while record.get('_duplicate_of'):
                name = record['_qualified']
                target = record['_duplicate_of']
                if name in visited or target not in by_name:
                    raise SystemExit(f'REFUSING {node}: unresolved canonical installation owner')
                visited.add(name)
                record = by_name[target]
            owners[record['_qualified']] = record
        if len(owners) != 1:
            raise SystemExit(f'REFUSING {node}: expected one canonical installation owner, found {sorted(owners)}')
        owner, record = next(iter(owners.items()))
        solved = record['solved']
        centre = float(solved['x']), float(solved['z'])
        if centre != tuple(installations[node]):
            raise SystemExit(f'REFUSING {node}: canonical installation centre disagrees with semantic anchor')
        width, depth = sites.flatten.pad_extent(record)
        if not all(math.isfinite(v) and v > 0 for v in (width, depth)):
            raise SystemExit(f'REFUSING {node}: unread canonical installation footprint')
        result[node] = dict(owner=owner, xz=list(centre), width_m=width,
                            depth_m=depth,
                            network_root_xz=list(installations['portalhub']))
    return result


def footprint_contact(complete, footprint: dict) -> dict:
    """Positive contact with the actual network root, on unique writer samples."""
    import field

    component = complete.at(*footprint['network_root_xz'])
    if not component:
        raise SystemExit('REFUSING installation source: network root has no measured dry component')
    finder = field._site_finder()
    tcdata = finder.tcdata
    offsets = (np.arange(tcdata.PITCH) - tcdata.PITCH // 2) * tcdata.SCALE
    points = set()
    for (zx, zz), rows, cols in finder.written_samples(
            *footprint['xz'], footprint['width_m'], footprint['depth_m'], 0.0):
        cx, cz = tcdata.zone_centre(zx, zz)
        points.update((int(round(cx + offsets[col])), int(round(cz + offsets[row])))
                      for row in rows for col in cols)
    if any(not complete.field.contains(x, z) for x, z in points):
        raise SystemExit('REFUSING installation source: unread canonical footprint contact')
    rooted = sorted((x, z) for x, z in points if complete.at(x, z) == component)
    if not rooted:
        raise SystemExit('REFUSING installation source: canonical pad has no contact with its measured network component')
    return dict(owner=footprint['owner'], component=int(component), samples=len(rooted),
                rooted_samples_xz=[list(point) for point in rooted],
                sample_step_m=float(tcdata.SCALE))


def footprint_approaches(graph, footprint: dict, contact: dict, width_m: float, *, complete):
    """Native exterior ring on the canonical pad's measured rooted contact side.

    The returned graph adds only the pad's level-footprint/chord-clearance mask.
    Its inherited connectivity labels remain a necessary over-approximation;
    the full-width router must still search every proposed approach it needs.
    No applied pad height or arrival-datum feasibility is inferred here.
    """
    import copy
    from scipy.spatial import cKDTree
    import arrival_geometry
    import route

    corr = copy.copy(graph.corr)
    level = route.footprint_half_m(width_m)
    x, z = map(float, footprint['xz'])
    hw, hd = float(footprint['width_m']) / 2, float(footprint['depth_m']) / 2
    pad = dict(x=x, z=z, hw=hw, hd=hd)
    grow = level + arrival_geometry.chord_slack_m(corr.cell_m)
    axis = np.arange(corr.m) * corr.cell_m
    masked = ((np.abs(corr.x0 + axis[None, :] - x) <= hw + grow)
              & (np.abs(corr.z0 + axis[:, None] - z) <= hd + grow))
    routed_graph = copy.copy(graph)
    routed_graph.corr = corr
    routed_graph.ok = graph.ok & ~masked
    corr.forbid = ~routed_graph.ok
    if graph.corr.forbid is not None:
        corr.forbid = corr.forbid | graph.corr.forbid
        routed_graph.ok = ~corr.forbid
    try:
        base = corr.nearest_allowed(x, z)
    except ValueError:
        return routed_graph, []
    rooted = cKDTree(np.asarray(contact['rooted_samples_xz'], dtype=float))
    # The point must belong to an actual rooted writer sample's lattice
    # square, not its circumscribed circle or a neighboring component.
    witness_reach = float(contact['sample_step_m']) / 2.0
    approaches = []
    for cell in arrival_geometry.arrival_ring_cells(
            (x, z), base, corr, [pad], level):
        ex, ez = corr.cell_world(*cell)
        dx, dz = x - ex, z - ez
        length = math.hypot(dx, dz)
        ux, uz = dx / length, dz / length
        gap = arrival_geometry.ray_into_rect(ex, ez, ux, uz, x, z, hw, hd)
        if gap is None:
            continue
        point = (ex + ux * gap, ez + uz * gap)
        if complete.at(*point) != contact['component']:
            continue
        distance, index = rooted.query(point, p=np.inf)
        if distance > witness_reach:
            continue
        approaches.append(dict(owner=contact['owner'], handoff_xz=[ex, ez],
                               contact_xz=list(point),
                               rooted_sample_xz=rooted.data[index].tolist(),
                               semantic_xz=[x, z],
                               purpose='necessary dry approach preflight only'))
    return routed_graph, approaches
