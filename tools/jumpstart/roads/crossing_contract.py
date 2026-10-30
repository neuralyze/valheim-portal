"""Import-safe road/abutment design policy shared with network construction."""

import math

import numpy as np
import route as routemod

# Moved verbatim from roads/network.py, not another set of eligibility defaults.
WIDTH_M = {"trunk": 6.0, "spur": 4.0, "mountain": 4.0}
BANK_LEVEL_M = 6.0
STATION_M = 2.0
SIMPLIFY_TOL_M = 1.0
TANGENT_MIN_NORM_M = 1e-9


def footprint_offsets(width_m: float, lat_step_m: float = 0.5) -> np.ndarray:
    """Exact mandatory lateral probes shared by sweeps and necessary proofs."""
    extent = routemod.footprint_half_m(width_m)
    count = int(math.floor(extent / lat_step_m + 1e-9))
    return np.unique(np.concatenate(
        [np.arange(-count, count+1)*lat_step_m, [-extent, extent]]))

def straighten(pts, s, a: int, b: int):
    """Replace stations a..b with a straight line between their endpoints.

    `Crossings` cannot build a curve mid-span and asked for a constant bearing
    across the span plus 6 m each side.  Done on the STATION list, not the
    corridor nodes, so station count and spacing survive and the profile indices
    stay valid.
    """
    ax, az = pts[a]
    bx, bz = pts[b]
    span = s[b] - s[a]
    out = list(pts)
    for k in range(a + 1, b):
        f = (s[k] - s[a]) / span if span > 0 else 0.0
        out[k] = (ax + (bx - ax) * f, az + (bz - az) * f)
    return out
def transverse_window(fld, pts, width_m: float, lat_step_m: float = 0.5,
                      *, wet_at: list | None = None
                      ) -> tuple[np.ndarray, np.ndarray,
                                 list[tuple[int, float, float, float]]]:
    """Per-station MIN and MAX generated height across the LEVEL road surface.

    WHY THIS EXISTS, and it is the fourth instance of one defect: a quantity
    certified over one object and consumed as though it were about another.
    `fit_profile` was being handed `t`, the generated height ON THE
    CENTRELINE, and its `+/-cut_fill_max` box was therefore a statement about
    a LINE.  `TerrainComp::ApplyToHeightmap` clamps PER SAMPLE against that
    sample's own generated height, and the ribbon writes a LEVEL bench out to
    `route.footprint_half_m(width)` -- 4.0 m either side on a 6 m trunk.  On a
    cross slope the outermost shoulder sample therefore carries about half the
    transverse relief ON TOP OF whatever cut the profile already owes.

    MEASURED on N4-mid2-terminus, 2026-09-18, by `ribbon.py`'s rasteriser
    after the router had passed the leg: 112 carriageway samples past the
    +/-8 m clamp in the best of three routing variants (239 and 120 in the
    other two), of which 71 were in the carriageway band and **every single
    one sat at lat 3.99-4.12 m** -- the outermost shoulder sample line, not
    one of them on the paved +/-3.0 m.  First breach at arc 24.0 m of
    3,038.4 m and the last at 2,802.8 m, so there was no prefix worth
    terminating at either.  The leg's own routing record read
    `stations_over_clamp: 0`, truthfully, about stations.

    THE CONSTRAINT THIS MAKES EXPRESSIBLE.  For one profile value `y` to be
    writable across a station's whole footprint:

        y <= min(generated over footprint) + clamp
        y >= max(generated over footprint) - clamp

    which is a per-station [lo, hi] pair -- exactly the shape `fit_profile`
    already solves exactly by interval propagation.  Nothing new is modelled;
    the existing solver is finally given the right bounds.  It also makes the
    impossible case a PROOF rather than a surprise: where the footprint's own
    relief exceeds `2 * clamp`, lo > hi and the propagation reports an empty
    interval, which is the honest statement "no profile at any gradient can
    carry a level bench here".

    Sampled at `lat_step_m = 0.5` m -- HALF the 1 m lattice the ribbon is
    written on, so no written sample can hide between two probes.  The
    endpoints of the sweep are INCLUSIVE of `+/-e` because the sample at
    exactly `e` IS written: `ribbon.EDGE_EPS_M` exists because one float ULP
    at `lat = 4.000000000000001` once dropped that sample out of the
    carriageway and left a 1 m wide, 1.76 m deep trench under a batter.

    The normal is taken from the CENTRAL difference of the neighbouring
    stations, which is the tangent the ribbon itself rasterises against, and
    falls back to the one-sided difference at the two ends.
    """
    lats = footprint_offsets(width_m, lat_step_m)
    P = np.asarray(pts, dtype=np.float64)
    tang = np.empty_like(P)
    tang[1:-1] = P[2:] - P[:-2]
    tang[0] = P[1] - P[0]
    tang[-1] = P[-1] - P[-2]
    norm = np.hypot(tang[:, 0], tang[:, 1])
    norm[norm < TANGENT_MIN_NORM_M] = 1.0
    # left normal of (dx, dz) is (dz, -dx)
    nx = tang[:, 1] / norm
    nz = -tang[:, 0] / norm
    lo = np.empty(len(P))
    hi = np.empty(len(P))
    # UNREAD IS NOT CLEAR, AND `Field.bilinear` CANNOT SAY SO: it CLAMPS its
    # sample indices to the patch, so a probe that walks off the window comes
    # back as the edge height instead of as a refusal.  On a footprint sweep
    # that is silent and load-bearing -- the outermost shoulder is the sample
    # this whole function exists for, and it is the first one to leave the
    # patch.  `field.py`'s own request manifest is the DENOMINATOR here: two
    # Fresh42 fields on this host carry the SAME seed name and the SAME seed
    # hash and cover 26 windows and 9, and `require_seed` accepts both.
    # Counted and returned rather than raised, so the caller can name the
    # station and the offset rather than the whole segment.
    off = []
    bulk = getattr(fld, "sample_many", None)
    if bulk is not None:
        all_xs = P[:, 0, None] + nx[:, None]*lats
        all_zs = P[:, 1, None] + nz[:, None]*lats
        all_hs, covered = bulk(all_xs, all_zs)
    for i in range(len(P)):
        if bulk is not None:
            xs, zs, hs = all_xs[i], all_zs[i], all_hs[i]
            off.extend((i, float(lats[j]), float(xs[j]), float(zs[j]))
                       for j in np.flatnonzero(~covered[i]))
        else:
            xs = P[i, 0] + nx[i] * lats
            zs = P[i, 1] + nz[i] * lats
            hs = np.array([fld.bilinear(float(x), float(z))
                           for x, z in zip(xs, zs)], dtype=np.float64)
            for x, z, lat in zip(xs, zs, lats):
                if not fld.contains(float(x), float(z)):
                    off.append((i, float(lat), float(x), float(z)))
        if wet_at is not None:
            wet_at.extend((float(x), float(z), i) for x, z, h in zip(xs, zs, hs)
                          if h < routemod.WATER_LEVEL)
        lo[i] = hs.min()
        hi[i] = hs.max()
    return lo, hi, off
