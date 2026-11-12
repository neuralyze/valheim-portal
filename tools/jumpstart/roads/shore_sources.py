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
    samples = sum(complete.at(x, z) == component for x, z in points)
    if not samples:
        raise SystemExit('REFUSING installation source: canonical pad has no contact with its measured network component')
    return dict(owner=footprint['owner'], component=int(component), samples=samples)
