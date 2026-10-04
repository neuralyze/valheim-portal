#!/usr/bin/env python3
"""THE WALKER PROBE - can a player STAND here, and can a player GET here.

WHY A SEPARATE FILE.  Four sections ask the same two questions in four
different settings:

  portals      can a walker reach the arch's mouth, or is a board on the ray
  spawn        can a walker stand where the world puts them on death
  settlements  can a walker step from the carriageway onto the pad it meets
  lighting     is the flame in view from eye height over the applied surface

`instruments.py` owns the readings - the census, the ground, the geometry,
the Solid, the Grid.  What it does not own is this: the walker's own capsule
swept through those readings.  Rather than four copies of a swept capsule (and
this project has already paid twice for a second implementation of a shared
helper) there is one, here, imported by all four.  If a FIFTH section needs it,
it belongs in `instruments.py` and `WorldGateScope` owns that move.

EVERY LENGTH IN THIS FILE COMES FROM SOMEWHERE ELSE.  The capsule is
`ctx.walker`, measured off the Player prefab this run - `census()` raises
rather than substituting a guess, so asking for it is what makes the refusal
reachable.  The step limit is `instruments.STEP_LIMIT_PER_M`,
tan(38 deg) = 0.7813 m per 1 m lattice edge, from
`Character::c_MinSlideDegreesPlayer`.  There is no step-height constant in
Valheim and none is invented here.

THE SIGN CONVENTION MATTERS.  `hull_distance` is NEGATIVE inside the
footprint.  A probe that returned 0.0 for "touching" and 0.0 for "buried
1 m deep" is how a 212-piece watchtower stood in 13.5 m of air while four
censuses reported zero floating: the quantity has to keep its sign.
"""
from __future__ import annotations

import math


def instruments():
    """The one authority for readings.  Imported lazily so that a section
    module can be imported (and its selftest cases listed) by a tool that has
    no world, no cache and no harness."""
    from .. import instruments as I          # noqa: PLC0415
    return I


# ---------------------------------------------------------------------------
# footprint arithmetic
# ---------------------------------------------------------------------------
def hull_distance(solid, px: float, pz: float) -> float:
    """Distance from (px, pz) to the piece's own rotated FOOTPRINT, SIGNED:
    negative inside.

    The footprint and not the axis-aligned box: a wall placed at 45 degrees
    has a bounding box 41 % wider than the wall, and a probe that used it
    would manufacture obstructions that are not there - the mirror image of
    the coverage test that could not see a flip because it only ever looked
    at bounding boxes.
    """
    h = solid.hull()
    if not h:
        return math.inf
    if len(h) < 3:
        return min(math.hypot(px - a, pz - b) for a, b in h)
    inside = True
    best = math.inf
    n = len(h)
    for i in range(n):
        ax, az = h[i]
        bx, bz = h[(i + 1) % n]
        if (bx - ax) * (pz - az) - (bz - az) * (px - ax) < -1e-9:
            inside = False
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx
                                                   + (pz - az) * vz) / ll))
        best = min(best, math.hypot(px - (ax + t * vx), pz - (az + t * vz)))
    return -best if inside else best


def blocks_at_height(solid, surf: float, walker_h: float,
                     underfoot_tol: float) -> bool:
    """Does this piece occupy the band a standing walker occupies?

    Three exclusions, each with the same justification the roads section
    uses:
      * no measured solid at all (`_ZoneCtrl`, `LocationProxy`, mist) - it is
        MEASURED to have no collider, which is not the same as unmeasured;
      * its top is within a step of the surface - a kerb the walker walks
        over, and calling it a wall would invent a step-height rule the game
        does not have;
      * its underside is above the walker's head - a gantry, walked under.
    """
    if not solid.verts:
        return False
    if solid.ymax <= surf + underfoot_tol:
        return False
    if solid.ymin >= surf + walker_h:
        return False
    return True


def free_radius(x: float, z: float, surf: float, grid, walker_h: float,
                cap: float = 3.0, underfoot_tol: float | None = None,
                skip=()) -> tuple[float, object | None]:
    """How much room a standing walker has at (x, z), and what limits it.

    Returns (radius, nearest piece).  NEGATIVE means the walker's own centre
    line is INSIDE a piece: that is penetration, not a tight fit, and the
    number says how deep.  `cap` bounds the search, and a capped answer is
    reported as capped rather than as "plenty".
    """
    I = instruments()
    if underfoot_tol is None:
        underfoot_tol = I.STEP_LIMIT_PER_M
    best, who = cap, None
    for s in grid.near(x, z, cap + 2.0):
        if s in skip:
            continue
        if not blocks_at_height(s, surf, walker_h, underfoot_tol):
            continue
        d = hull_distance(s, x, z)
        if d < best:
            best, who = d, s
    return best, who


def headroom(x: float, z: float, surf: float, grid, walker_r: float,
             cap: float = 6.0, skip=()) -> tuple[float, object | None]:
    """Clear height above the surface at (x, z), and the piece that caps it.

    A roof 4 m up is not a defect and a beam 1.2 m up is; the walker's own
    measured height decides which, in the caller.
    """
    best, who = cap, None
    for s in grid.near(x, z, walker_r + 1.0):
        if s in skip or not s.verts:
            continue
        if s.ymin <= surf + 0.05:
            continue
        if hull_distance(s, x, z) > walker_r:
            continue
        if s.ymin - surf < best:
            best, who = s.ymin - surf, s
    return best, who


def standing_surface(x: float, z: float, grid, bed: float | None,
                     ceiling: float | None = None,
                     skip=()) -> tuple[float, object | None]:
    """The highest thing a walker's feet can rest on at (x, z) at or below
    `ceiling`, and what it is.  `bed` is the applied ground; None means the
    ground was not read here and only pieces are offered.

    THIS IS HOW A FLOOR STOPS BEING A HOLE.  A hall deck 3 m above its own
    bed is two surfaces at one xz; the walk uses the one it can reach.

    `skip` excludes named solids BY IDENTITY.  Its one caller is the spawn
    section's seat probe, which must not let a bed offer ITSELF as the
    surface the player who wakes in it is standing on.
    """
    best, who = (bed, None) if bed is not None else (None, None)
    for s in grid.near(x, z, 0.0):
        if s in skip or not s.verts:
            continue
        top = s.level_top()
        if top is None:
            continue
        if ceiling is not None and top > ceiling + 1e-6:
            continue
        if bed is not None and top < bed - 0.01:
            continue
        if not s.covers(x, z):
            continue
        if best is None or top > best:
            best, who = top, s
    return best, who


# ---------------------------------------------------------------------------
# the approach - a walker walking IN, not a box test
# ---------------------------------------------------------------------------
def yaw_deg(quat) -> float:
    """World yaw of a ZDO's own quaternion, in degrees."""
    x, y, _z, w = quat
    return math.degrees(math.atan2(2.0 * (w * y + x * quat[2]),
                                   1.0 - 2.0 * (y * y + x * x)))


def approach_reach(target, grid, surf: float, walker_r: float, walker_h: float,
                   facing_sign: int, span: float, start: float = 0.75,
                   step: float = 0.25, skip=(),
                   hits: list | None = None) -> tuple[float, tuple | None]:
    """How far out along the target's own facing axis a walker can get, and
    the first thing that stops them.

    THE RAY, NOT THE VOLUME, AND THIS IS THE WHOLE POINT OF THE FUNCTION.  A
    box test round the mouth of an arch clears anything outside the box: the
    four 45-degree bays in the portal hall stood their boards 4.395 m out
    along the facing axis and cleared a 3.000 m corridor volume by 0.150 m,
    and a walker still had to walk through them.  A clearance smaller than
    the uncertainty in the volume's own definition is not clearance.  So the
    walk is swept: the walker's capsule is stepped along the ray and the
    first overlap ends the reach, with its depth and its distance.

    Returns (reach_m, (gap_m, piece, at_m) or None).  `reach_m == span` means
    the whole ray is clear.  `hits`, when given, collects EVERY piece that
    overlaps the walker anywhere on the ray, once each: clearing only the
    worst one does not clear the ray, so a caller asking whose blockers
    they are must be able to ask of all of them.
    """
    fx = facing_sign * math.sin(math.radians(yaw_deg(target.quat)))
    fz = facing_sign * math.cos(math.radians(yaw_deg(target.quat)))
    reach, worst = span, None
    d = start
    while d <= span + 1e-9:
        px, pz = target.pos[0] + fx * d, target.pos[2] + fz * d
        for s in grid.near(px, pz, walker_r + 2.0):
            if s is target or s in skip:
                continue
            if not blocks_at_height(s, surf, walker_h,
                                    instruments().STEP_LIMIT_PER_M):
                continue
            gap = hull_distance(s, px, pz) - walker_r
            if gap < 0.0:
                if worst is None or gap < worst[0]:
                    worst = (gap, s, d)
                if hits is not None and not any(h is s for h in hits):
                    hits.append(s)
                reach = min(reach, d)
        d += step
    return reach, worst


# ---------------------------------------------------------------------------
# sightlines, over the surface the player actually walks on
# ---------------------------------------------------------------------------
def visible(profile: list[float | None], i: int, j: int, eye: float,
            target_y: float) -> bool:
    """Is `target_y` over station j visible from `eye` over station i, given
    the surface profile between them?

    The occluder is the APPLIED surface at the 1 m lattice, which is the
    lattice the engine's own heightmap uses.  A coarser step steps over the
    ridge that does the blocking - the same mistake as an 8 m gradient
    baseline over a 1 m cliff.
    """
    if i == j:
        return True
    lo, hi = (i, j) if i < j else (j, i)
    for k in range(lo + 1, hi):
        yk = profile[k]
        if yk is None:
            continue
        t = (k - i) / (j - i)
        if yk > eye + (target_y - eye) * t + 1e-6:
            return False
    return True
