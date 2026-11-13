"""Exact spatial dispatch for one solve's immutable generated Heightmap fields.

This is not an applied/live-terrain cache. The caller must first validate the
source bytes, seed, lattice and coverage, and must construct a fresh sampler
when the field collection or its geometry changes. Field objects and their
height arrays stay owned by that solve; no arrays are copied or smoothed.
"""
from __future__ import annotations

import math

import numpy as np

from roads.field_index import FieldEntry, FieldIndex


class GeneratedSampler:
    """Select exactly as waterfront.Surface, without an all-field point scan.

    ``fields`` is the insertion-ordered mapping of crossings.water.Field
    objects from the validated generated source, not roads.field.Field (whose
    containment frame differs). Cached endpoints use precisely water.Field's
    float64 expressions. Positive/zero/negative margins are evaluated per
    query, with inclusive boundaries. Equal (step, half) priorities retain
    mapping insertion order. There is no last-patch shortcut that could hide
    a better inset patch, and no coverage or out-of-bounds sample fallback.

    The hierarchy stores O(number of fields) metadata and existing field
    references. Disjoint local queries visit a small spatial subset; truly
    overlapping boxes may require more work to preserve the exact policy.
    """

    def __init__(self, fields, field_names):
        self.field_names = list(field_names)
        ordered = sorted(fields.values(), key=lambda f: (f.step, f.half))
        entries = []
        for rank, field in enumerate(ordered):
            x0, z0 = field.x0, field.z0
            entries.append(FieldEntry(field, rank, x0, z0,
                                      x0 + (field.n - 1) * field.step,
                                      z0 + (field.n - 1) * field.step))
        self._entries = tuple(entries)
        self._index = FieldIndex(entries)

    def _entry_for(self, x: float, z: float, margin: float) -> FieldEntry:
        best = self._index.find(x, z, margin)
        if best is None:
            raise SystemExit(
                f"({x}, {z}) is in no patch of {self.field_names}; widen the "
                f"request and re-run run_patchscan.sh rather than guessing a "
                f"height")
        return best

    def patch_for(self, x: float, z: float, margin: float = 2.0):
        return self._entry_for(x, z, margin).field

    def gen_sample(self, sx: int, sz: int) -> float:
        entry = self._entry_for(float(sx), float(sz), margin=0.0)
        # Deliberately no /step, clipping, or interpolation: these are exactly
        # Surface.gen_sample's integer-lattice index expressions and errors.
        j = int(round(sx - entry.x0))
        i = int(round(sz - entry.z0))
        return float(entry.field.heights[i, j])

    def _scalar_points(self, points: np.ndarray) -> np.ndarray:
        """Replay Surface.at's generated arithmetic and first-error order."""
        result = np.empty(len(points), dtype=np.float64)
        for index, (x, z) in enumerate(points):
            x, z = float(x), float(z)
            x0, z0 = math.floor(x), math.floor(z)
            tx, tz = x - x0, z - z0
            gen = 0.0
            for dx, dz, w in (
                    (0, 0, (1 - tx) * (1 - tz)),
                    (1, 0, tx * (1 - tz)),
                    (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
                if w:
                    gen += w * self.gen_sample(x0 + dx, z0 + dz)
            result[index] = gen + 0.0
        return result

    def _corner_ranks(self, corners: np.ndarray) -> np.ndarray:
        """Dispatch independent corners using the existing spatial metadata."""
        ranks = np.full(len(corners), len(self._entries), dtype=np.intp)
        root = self._index._root
        stack = [(root, np.arange(len(corners)))] if root is not None else []
        while stack:
            node, indices = stack.pop()
            x, z = corners[indices].T
            inside = ((ranks[indices] > node.rank)
                      & (node.xmin + 0.0 <= x) & (x <= node.xmax - 0.0)
                      & (node.zmin + 0.0 <= z) & (z <= node.zmax - 0.0))
            indices = indices[inside]
            if not len(indices):
                continue
            if node.entries:
                for entry in node.entries:
                    x, z = corners[indices].T
                    inside = ((ranks[indices] > entry.rank)
                              & (entry.x0 + 0.0 <= x)
                              & (x <= entry.xmax - 0.0)
                              & (entry.z0 + 0.0 <= z)
                              & (z <= entry.zmax - 0.0))
                    ranks[indices[inside]] = entry.rank
            else:
                stack.append((node.right, indices))
                stack.append((node.left, indices))
        return ranks

    def sample_points(self, points) -> np.ndarray:
        """Return immutable generated ``Surface.at(x, z)[0]`` for Nx2 points.

        Each nonzero-weight integer corner selects its own field. Only those
        lattice samples are read, once per call; field matrices remain shared.
        The float64 products and four ordered additions match the scalar
        instrument, rather than a field's own potentially nonunit grid blend.
        Unsafe integer conversion and any dispatch/index failure are replayed
        through the scalar path so grouping cannot change the first error.
        """
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError("points must have shape (N, 2)")
        if not len(points):
            return np.empty(0, dtype=np.float64)
        # Beyond this range, adding one to a float lattice corner need not
        # equal Python's unbounded integer addition followed by float lookup.
        if not np.all(np.isfinite(points) & (np.abs(points) < 2**52)):
            return self._scalar_points(points)

        lower = np.floor(points)
        tx, tz = (points - lower).T
        weights = np.empty((len(points), 4), dtype=np.float64)
        with np.errstate(under="ignore"):
            weights[:, 0] = (1 - tx) * (1 - tz)
            weights[:, 1] = tx * (1 - tz)
            weights[:, 2] = (1 - tx) * tz
            weights[:, 3] = tx * tz
        corners = np.empty((len(points), 4, 2), dtype=np.float64)
        corners[:, 0] = lower
        corners[:, 1] = lower + (1, 0)
        corners[:, 2] = lower + (0, 1)
        corners[:, 3] = lower + (1, 1)
        active = np.flatnonzero(weights.ravel())
        unique, inverse = np.unique(corners.reshape(-1, 2)[active], axis=0,
                                    return_inverse=True)
        ranks = self._corner_ranks(unique)
        if np.any(ranks == len(self._entries)):
            return self._scalar_points(points)

        # Group actual raster reads, not an N-points scan for every field.
        order = np.argsort(ranks, kind="stable")
        breaks = np.flatnonzero(np.diff(ranks[order])) + 1
        values = np.empty(len(unique), dtype=np.float64)
        for indices in np.split(order, breaks):
            entry = self._entries[ranks[indices[0]]]
            x, z = unique[indices].T
            # Python round(float) and rint(float64) both use ties-to-even.
            # Deliberately retain gen_sample's missing /step and negative-index
            # behavior; invalid indices must raise, never clip or rescale.
            with np.errstate(over="ignore", invalid="ignore"):
                j = np.rint(x - entry.x0)
                i = np.rint(z - entry.z0)
            height, width = entry.field.heights.shape
            if not np.all((i >= -height) & (i < height)
                          & (j >= -width) & (j < width)):
                return self._scalar_points(points)
            values[indices] = entry.field.heights[
                i.astype(np.intp), j.astype(np.intp)]

        samples = np.zeros((len(points), 4), dtype=np.float64)
        samples.ravel()[active] = values[inverse]
        result = np.zeros(len(points), dtype=np.float64)
        # Do not reduce/reassociate or evaluate zero-weight corners. Python
        # float arithmetic does not honor NumPy's process-wide seterr policy.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            for corner in range(4):
                used = weights[:, corner] != 0
                result[used] += weights[used, corner] * samples[used, corner]
            result += 0.0
        return result
