"""Check a generated dock's actual collider parts against its measured boat."""
from __future__ import annotations

import math

from accept.instruments import rotate
from accept.sections import harbours


def boat_clearance(pieces, boat, *, hulls=None, geometry=None):
    """Conservative full-hull clearance at the exact emitted berth pose.

    Callers may share the operation's measured hull/geometry dictionaries. No
    process-global snapshot survives a new generation/build operation. Mesh
    bounds can prove separation, never prove an intersecting bound is clear.
    Ground depth, sea connectivity and structural support remain separate gates.
    """
    hulls = harbours.hull_table() if hulls is None else hulls
    geometry = harbours._corpus()[0] if geometry is None else geometry
    vessel = boat['prefab']
    hull = hulls.get(vessel)
    if (hull is None or not hull.separating_axes or hull.air_m is None
            or not math.isfinite(hull.air_m) or hull.air_m < 0):
        return dict(ok=False, blocked=[], missing_geometry=[vessel],
                    reason='complete measured hull and air draft required')
    height = float(boat['y'])
    band = (height - hull.draught_m, height + hull.air_m)
    pose = (float(boat['x']), float(boat['z']), float(boat['yaw']))
    blocked, missing = [], set()
    for index, piece in enumerate(pieces):
        parts = geometry.get(piece.prefab)
        if not parts:
            missing.add(piece.prefab)
            continue
        half_yaw = math.radians(piece.yaw) / 2.0
        quaternion = (0.0, math.sin(half_yaw), 0.0, math.cos(half_yaw))
        barriers, source_pose = harbours.barriers_for_placement(
            piece.prefab, (piece.x, piece.y, piece.z), quaternion,
            parts, rotate, {})
        if not barriers:
            missing.add(piece.prefab)
            continue
        for barrier in barriers:
            # The shared ledger helper forgives 1cm vertical overlap. Native
            # emitted coordinates do not: any positive overlap must reach SAT.
            overlap = min(barrier.y_hi, band[1]) - max(barrier.y_lo, band[0])
            if (overlap > 0.0 and harbours.swept_hull_near_polygon(
                    hull, pose, pose, barrier.poly)):
                blocked.append(dict(piece=index, prefab=piece.prefab,
                    role=piece.role, part=barrier.part,
                    tight=barrier.tight, collider_pose=source_pose,
                    y_lo=barrier.y_lo, y_hi=barrier.y_hi))
    return dict(ok=not blocked and not missing, blocked=blocked,
                missing_geometry=sorted(missing), vertical_band=list(band),
                boat_pose=list(pose), vessel=vessel)
