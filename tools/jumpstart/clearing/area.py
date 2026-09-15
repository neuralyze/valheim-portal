#!/usr/bin/env python3
"""Turn a solved placement into the ground area that has to be cleared.

WHY A DISC, AND WHY THE HALF-DIAGONAL.  The only headless removal primitive
that exists (see clear.py for the survey) selects objects by
`Utils.DistanceXZ(zdo.GetPosition(), centre)` -- MEASURED in
UpgradeWorld.dll IL, `FiltererParameters::FilterZdos`, which compares
`DistanceXZ` against `MinDistance`/`MaxDistance`.  DistanceXZ ignores y, so
the selected region is a vertical CYLINDER of radius `max`, not a sphere and
not a box: height never excludes anything.

The pad, though, is a RECTANGLE -- the same one the flatten step levels.  A
cylinder of radius (half the shorter side) misses the corners, and a cylinder
of radius (half the longer side) still misses them: the only radius that
covers a w x d rectangle is its CIRCUMRADIUS, hypot(w, d) / 2.  On the
70 x 70 m `iron-era-workshop` pad that is 49.50 m against a half-side of
35.00 m, so costing the clearing by half-side would leave 14.5 m of standing
forest reaching into every corner of the building -- which is exactly the
shape of defect this file exists to avoid.

ROTATION.  The circumscribed circle of a rectangle does not move or change
size when the rectangle spins about its centre, so the clearing radius is
independent of the placement's yaw.  That is not an excuse to ignore yaw: it
is the reason the radius is safe at every yaw, and `covers_rotated_pad()`
states it as a checkable property rather than a claim.

MARGIN.  Valheim's own way of keeping vegetation out of a location is
`ZoneSystem::InsideClearArea`, MEASURED as an axis-aligned square test
(|dx| < r AND |dz| < r) against the vegetation's POSITION, with no allowance
for trunk thickness.  So margin 0 is vanilla-equivalent.  The 2 m default here
is a deliberate over-clear, NOT a measurement: a trunk centred half a metre
outside a pad corner would otherwise be left standing against the wall, and
the collider radii that would let this be derived live in Unity prefab assets
rather than in any assembly we can read.

BOUND.  Clearing must never reach a generated location: those are world
content, not scenery.  The solver already measured how far the nearest one is
(`solved.nearest_location_m`) and what the requirement demanded
(`requirement.location_clearance_m`); the smaller of the two is a hard ceiling
on the radius, and exceeding it is refused rather than clipped.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "terraform"))

from flatten import pad_extent  # noqa: E402  the flatten step's own pad, not a second opinion

# Not derived from game data -- see MARGIN above.
DEFAULT_MARGIN_M = 2.0
SQRT2 = math.sqrt(2.0)


class ClearAreaRefused(Exception):
    """The derived area cannot be cleared safely, and guessing is worse."""


@dataclass(frozen=True)
class ClearArea:
    placement_id: str
    centre_x: float
    centre_z: float
    surface_y: float
    pad_w_m: float
    pad_d_m: float
    apron_m: float
    margin_m: float
    radius_m: float
    yaw_deg: float
    location_bound_m: float | None
    bound_source: str
    relief_m: float

    @property
    def verify_half_side_m(self) -> float:
        """Half-side of the largest axis-aligned square wholly INSIDE the
        cleared cylinder.  Used for verification: a box query over this square
        can only see objects the removal was obliged to take, so anything left
        in it is a real miss.  A square of half-side s fits in a circle of
        radius R iff s * sqrt(2) <= R."""
        return math.floor(self.radius_m / SQRT2 * 10) / 10

    def covers_rotated_pad(self) -> bool:
        """Every corner of the pad, at this placement's yaw, is inside the
        cleared cylinder.  The pad extent is already expressed in world axes by
        the solver, so this re-spins it about its centre and checks the worst
        case rather than trusting that hypot() was the right formula."""
        half_w = self.pad_w_m / 2 + self.apron_m
        half_d = self.pad_d_m / 2 + self.apron_m
        rad = math.radians(self.yaw_deg)
        cos, sin = math.cos(rad), math.sin(rad)
        for sx in (-1, 1):
            for sz in (-1, 1):
                x, z = sx * half_w, sz * half_d
                wx = x * cos + z * sin
                wz = -x * sin + z * cos
                if math.hypot(wx, wz) > self.radius_m + 1e-9:
                    return False
        return True

    def verify_relief_headroom_m(self, relief_m: float, tree_m: float = 25.0) -> float:
        """Spare metres the verification box has over the pad's own relief.

        `findObjects -near x y z r` is a CUBE, so the same r constrains HEIGHT
        (MEASURED in `NearCriteria::IsMatch`).  A tree rooted at the low corner
        of a sloped pad has to fall inside the y window or verification would
        answer "clear" about ground it never looked at.  `relief_m` is the
        solver's own worst cut/fill; 25 m is a generous standing-tree height, so
        a positive result means the window cannot hide one.
        """
        return self.verify_half_side_m - (relief_m + tree_m)

    def describe(self) -> str:
        bound = ("unbounded" if self.location_bound_m is None
                 else f"{self.location_bound_m:.1f} m ({self.bound_source})")
        return (f"{self.placement_id}: pad {self.pad_w_m:g} x {self.pad_d_m:g} m"
                f" + {self.apron_m:g} m apron at ({self.centre_x:g}, {self.centre_z:g}),"
                f" yaw {self.yaw_deg:g} deg -> clear a cylinder of radius"
                f" {self.radius_m:.2f} m (half-diagonal {self.radius_m - self.margin_m:.2f} m"
                f" + {self.margin_m:g} m margin); nearest location bound {bound};"
                f" verification box half-side {self.verify_half_side_m:g} m"
                f" (pad relief {self.relief_m:.2f} m, height headroom"
                f" {self.verify_relief_headroom_m(self.relief_m):.2f} m)")


def surface_y(place: dict) -> float:
    """The height the pad ends up at.  The flatten step levels to
    `flatten_cost.target_y`, so that -- not the placement origin -- is the
    ground a tree would be standing on."""
    solved = place["solved"]
    cost = solved.get("flatten_cost") or {}
    for key, src in ((cost, "target_y"), (solved, "y_centre_m"), (solved, "y")):
        value = key.get(src)
        if value is not None:
            return float(value)
    raise ClearAreaRefused(f"{place.get('id')}: solved block has no height to clear at")


def location_bound(place: dict) -> tuple[float | None, str]:
    """How close the clearing may come to generated world content, and where
    that number came from.  Measured truth beats the requirement when both
    exist, because `nearest_location_m` is what the solver actually found."""
    solved = place.get("solved") or {}
    measured = solved.get("nearest_location_m")
    required = (place.get("requirement") or {}).get("location_clearance_m")
    candidates = [(float(v), s) for v, s in
                  ((measured, "solved.nearest_location_m"),
                   (required, "requirement.location_clearance_m")) if v is not None]
    if not candidates:
        return None, "none recorded"
    return min(candidates)


def pad_relief_m(place: dict) -> float:
    """The worst height the pad's own ground departs from the levelled
    surface, taken from the flatten bill the solver already computed.  Used
    only to prove the verification box's height window is wide enough."""
    solved = place.get("solved") or {}
    cost = solved.get("flatten_cost") or {}
    candidates = [abs(float(cost[k])) for k in ("max_cut_m", "max_fill_m") if cost.get(k) is not None]
    if candidates:
        return max(candidates)
    spread = solved.get("flat_spread_m")
    return float(spread) if spread is not None else 0.0


def clearing_area(place: dict, apron: float = 0.0,
                  margin: float = DEFAULT_MARGIN_M) -> ClearArea:
    """Derive the clearing cylinder from what the solver already solved.

    `apron` mirrors flatten.py's flag of the same name: ground that gets
    levelled has to get cleared too, or the pipeline produces a bald terrace
    with trees standing on it.
    """
    solved = place.get("solved")
    if not solved:
        raise ClearAreaRefused(f"{place.get('id')}: no solved block; run the solver first")
    pad_w, pad_d = pad_extent(place)
    if pad_w <= 0 or pad_d <= 0:
        raise ClearAreaRefused(f"{place.get('id')}: pad extent {pad_w} x {pad_d} is not an area")
    if margin < 0:
        raise ClearAreaRefused(f"{place.get('id')}: negative margin {margin}")
    half_diagonal = math.hypot(pad_w + 2 * apron, pad_d + 2 * apron) / 2
    radius = half_diagonal + margin
    bound, source = location_bound(place)
    if bound is not None and radius >= bound:
        raise ClearAreaRefused(
            f"{place['id']}: a {radius:.2f} m clearing radius reaches the nearest generated "
            f"location at {bound:.1f} m ({source}). Clearing world content is not scenery work: "
            f"re-solve the placement further from it, or shrink the apron/margin.")
    area = ClearArea(
        placement_id=str(place.get("id", "?")),
        centre_x=float(solved["x"]), centre_z=float(solved["z"]),
        surface_y=surface_y(place),
        pad_w_m=pad_w, pad_d_m=pad_d, apron_m=float(apron), margin_m=float(margin),
        radius_m=radius, yaw_deg=float((place.get("rotation") or {}).get("yaw", 0.0)),
        location_bound_m=bound, bound_source=source, relief_m=pad_relief_m(place),
    )
    if not area.covers_rotated_pad():
        raise ClearAreaRefused(
            f"{area.placement_id}: radius {radius:.2f} m does not cover the pad at yaw "
            f"{area.yaw_deg:g}; the derivation is wrong, not the input")
    return area


def load_placements(path: Path) -> list[dict]:
    doc = yaml.safe_load(path.read_text())
    return list(doc.get("placements") or [])


def placement(path: Path, pid: str) -> dict:
    for p in load_placements(path):
        if p.get("id") == pid:
            return p
    raise ClearAreaRefused(f"no placement {pid} in {path}")
