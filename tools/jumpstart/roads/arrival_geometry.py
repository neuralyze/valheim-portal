"""Shared native geometry for exterior road arrivals."""
from __future__ import annotations

import math

from crossing_contract import SIMPLIFY_TOL_M


def chord_slack_m(cell_m: float) -> float:
    """How far a station can stand from the corridor cell centres its search
    walked: half a cell diagonal plus the simplify tolerance."""
    return cell_m / math.sqrt(2.0) + SIMPLIFY_TOL_M


def fixed_landing_rejection(points, root, outward_bearing: float | None,
                            level_run_m: float) -> str | None:
    """Check root-first published road geometry against its owned landing.

    Native node coordinates are rounded to centimetres. That serialization
    error, not an attachment-search radius, is the allowed displacement.
    """
    tolerance = math.sqrt(2.0) * .005 + 1e-8
    iterator = iter(points)
    first = next(iterator, None)
    if first is None:
        return "owned landing has no constructed road"
    gap = math.dist(first, root)
    if gap > tolerance:
        return f"owned landing root moved {gap:.4f} m (rounding allowance {tolerance:.4f} m)"
    if outward_bearing is None:
        return None
    angle = math.radians(outward_bearing)
    ux, uz = math.sin(angle), math.cos(angle)
    distance = 0.0
    previous = first
    for point in iterator:
        distance += math.dist(previous, point)
        if distance > level_run_m + tolerance:
            break
        expected = (root[0] - distance * ux, root[1] - distance * uz)
        gap = math.dist(point, expected)
        if gap > tolerance:
            return (f"owned landing bends {gap:.4f} m from its declared bearing "
                    f"at {distance:.4f} m (rounding allowance {tolerance:.4f} m)")
        previous = point
    return None


def ray_into_rect(ex: float, ez: float, ux: float, uz: float, rx: float,
                  rz: float, hw: float, hd: float) -> float | None:
    """How far along the unit ray from (ex, ez) it first enters the rectangle
    centred (rx, rz) with half-extents (hw, hd): 0 if it starts inside, None
    if it never does."""
    t0, t1 = 0.0, math.inf
    for e, u, c, h in ((ex, ux, rx, hw), (ez, uz, rz, hd)):
        if abs(u) < 1e-12:
            if abs(e - c) > h:
                return None
            continue
        a, b = (c - h - e) / u, (c + h - e) / u
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0


def arrival_ring_cells(xz, base, corr, pads, level: float,
                       search_m: float = 200.0):
    """The existing arrival ring, in nearest-replacement order, uncapped."""
    ring = chord_slack_m(corr.cell_m) + corr.cell_m * math.sqrt(2.0)
    i0, j0 = corr.cell_of(float(xz[0]), float(xz[1]))
    r = int(search_m / corr.cell_m)
    candidates = []
    for i in range(max(0, i0 - r), min(corr.m, i0 + r + 1)):
        for j in range(max(0, j0 - r), min(corr.m, j0 + r + 1)):
            if corr.wet[i, j] or corr.blocked(i, j):
                continue
            x, z = corr.cell_world(i, j)
            dx, dz = float(xz[0]) - x, float(xz[1]) - z
            length = math.hypot(dx, dz)
            if length < 1e-9:
                continue
            gap = min((t for q in pads for t in (ray_into_rect(
                x, z, dx / length, dz / length, float(q["x"]), float(q["z"]),
                float(q["hw"]) + level, float(q["hd"]) + level),)
                if t is not None), default=None)
            if gap is not None and gap <= ring:
                candidates.append(((i - base[0]) ** 2 + (j - base[1]) ** 2,
                                   (i - i0) ** 2 + (j - j0) ** 2, i, j))
    return [(i, j) for _db, _dn, i, j in sorted(candidates)]
