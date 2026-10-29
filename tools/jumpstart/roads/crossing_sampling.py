"""Bounded, uncached native Heightmap sampling for crossing geometry."""
from __future__ import annotations

import math
import sys

import numpy as np

import field as fieldmod


_QUERY_CHUNK = 8192


def _raise_vertex(tiles, x, z, error_type):
    """Replay only a refused vertex to retain scalar diagnostics and precedence."""
    x, z = int(x), int(z)
    pitch = fieldmod.HEIGHTMAP_ZONE_M
    values = [f.at(x, z)
              for f, x0, x1, z0, z1 in
              tiles.get((math.floor(x/pitch), math.floor(z/pitch)), ())
              if x0-1e-6 <= x <= x1+1e-6 and z0-1e-6 <= z <= z1+1e-6]
    if not values or not all(math.isfinite(v) for v in values):
        raise error_type(f"unread rendered Heightmap vertex ({x}, {z})")
    if max(values)-min(values) > 1e-4:
        raise error_type(f"overlapping Heightmaps disagree at ({x}, {z}): {values}")
    raise error_type(f"rendered Heightmap changed during sampling at ({x}, {z})")


def _vertices(tiles, xs, zs, error_type):
    """Read each covering source, grouping work by tile rather than query."""
    count = xs.size
    first = np.empty(count, dtype=np.float64)
    low = np.full(count, np.inf)
    high = np.full(count, -np.inf)
    seen = np.zeros(count, dtype=bool)
    bad = np.zeros(count, dtype=bool)
    pitch = fieldmod.HEIGHTMAP_ZONE_M
    tile_keys, inverse = np.unique(
        np.column_stack((np.floor(xs/pitch), np.floor(zs/pitch))),
        axis=0, return_inverse=True)
    for group, (tile_x, tile_z) in enumerate(tile_keys):
        positions = np.flatnonzero(inverse == group)
        vx, vz = xs[positions], zs[positions]
        for f, x0, x1, z0, z1 in tiles.get((int(tile_x), int(tile_z)), ()):
            covered = ((x0-1e-6 <= vx) & (vx <= x1+1e-6)
                       & (z0-1e-6 <= vz) & (vz <= z1+1e-6))
            selected = positions[covered]
            if not selected.size:
                continue
            # np.rint, like Python round in Field.index, uses ties-to-even.
            # Do not assume exactly unit step or exactly integral origins.
            cols = np.rint((xs[selected]-f.x0)/f.step)
            rows = np.rint((zs[selected]-f.z0)/f.step)
            valid = ((rows >= 0) & (rows < f.n)
                     & (cols >= 0) & (cols < f.n))
            bad[selected[~valid]] = True
            selected = selected[valid]
            values = np.asarray(f.h[rows[valid].astype(np.intp),
                                      cols[valid].astype(np.intp)], dtype=np.float64)
            fresh = ~seen[selected]
            first[selected[fresh]] = values[fresh]
            seen[selected] = True
            bad[selected] |= ~np.isfinite(values)
            low[selected] = np.minimum(low[selected], values)
            high[selected] = np.maximum(high[selected], values)
    spread = np.zeros(count, dtype=np.float64)
    np.subtract(high, low, out=spread, where=np.isfinite(high) & np.isfinite(low))
    bad |= ~seen | (spread > 1e-4)
    refused = np.flatnonzero(bad)
    if refused.size:
        index = refused[0]
        _raise_vertex(tiles, xs[index], zs[index], error_type)
    return first


def generated_many(fields, tiles, xs, zs, error_type):
    """Match PhysicalFields.generated on broadcast coordinates, in C order.

    ``fields`` must be its sorted tuple of exact native Field instances and
    ``tiles`` its immutable-frame index (including every overlapping source).
    Heights are read anew on every call; no sampled data is retained. The
    caller retains scalar dispatch for subclasses with custom ``at`` behavior.
    """
    xs, zs = np.broadcast_arrays(np.asarray(xs, dtype=np.float64),
                                 np.asarray(zs, dtype=np.float64))
    result = np.empty(xs.shape, dtype=np.float64)
    for start in range(0, xs.size, _QUERY_CHUNK):
        stop = min(start+_QUERY_CHUNK, xs.size)
        x, z = xs.flat[start:stop], zs.flat[start:stop]
        nonfinite = np.flatnonzero(~np.isfinite(x) | ~np.isfinite(z))
        # Scalar floor errors must not jump ahead of an earlier missing corner.
        end = int(nonfinite[0]) if nonfinite.size else x.size
        bx, bz = np.floor(x[:end]), np.floor(z[:end])
        tx, tz = x[:end]-bx, z[:end]-bz
        weights = np.column_stack(((1-tx)*(1-tz), tx*(1-tz),
                                   (1-tx)*tz, tx*tz))
        cx = bx[:, None] + np.array((0, 1, 0, 1))
        cz = bz[:, None] + np.array((0, 0, 1, 1))
        active = weights != 0
        heights = np.zeros(weights.shape, dtype=np.float64)
        if np.any(active):
            heights[active] = _vertices(tiles, cx[active], cz[active], error_type)
        total = np.zeros(end, dtype=np.float64)
        # CPython 3.12+ sum(float...) compensates rounding error. Preserve it:
        # a one-ulp difference can change whether a footprint touches water.
        if sys.version_info >= (3, 12):
            correction = np.zeros(end, dtype=np.float64)
            with np.errstate(over="ignore", invalid="ignore"):
                for corner in range(4):
                    term = weights[:, corner]*heights[:, corner]
                    updated = total+term
                    correction += np.where(
                        np.abs(total) >= np.abs(term),
                        (total-updated)+term, (term-updated)+total)
                    total = updated
                corrected = (correction != 0) & np.isfinite(correction)
                total[corrected] += correction[corrected]
        else:
            for corner in range(4):
                total += weights[:, corner]*heights[:, corner]
        result.flat[start:start+end] = total
        if nonfinite.size:
            math.floor(float(x[end]))
            math.floor(float(z[end]))
    return result
