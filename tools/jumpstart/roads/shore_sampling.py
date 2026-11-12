"""Exact spatial dispatch for one solve's immutable generated Heightmap fields.

This is not an applied/live-terrain cache. The caller must first validate the
source bytes, seed, lattice and coverage, and must construct a fresh sampler
when the field collection or its geometry changes. Field objects and their
height arrays stay owned by that solve; no arrays are copied or smoothed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Entry:
    field: object
    rank: int
    x0: float
    z0: float
    xmax: float
    zmax: float


@dataclass(frozen=True, slots=True)
class _Node:
    xmin: float
    zmin: float
    xmax: float
    zmax: float
    rank: int
    entries: tuple[_Entry, ...] = ()
    left: _Node | None = None
    right: _Node | None = None


def _tree(entries: list[_Entry]) -> _Node:
    xmin = min(e.x0 for e in entries)
    zmin = min(e.z0 for e in entries)
    xmax = max(e.xmax for e in entries)
    zmax = max(e.zmax for e in entries)
    rank = min(e.rank for e in entries)
    if len(entries) <= 8:
        return _Node(xmin, zmin, xmax, zmax, rank,
                     tuple(sorted(entries, key=lambda e: e.rank)))
    # Median splitting keeps storage linear even for broad, nested or identical
    # patches. Unlike a per-metre/bin raster, world extent cannot grow memory.
    if xmax - xmin >= zmax - zmin:
        entries.sort(key=lambda e: (e.x0, e.xmax, e.rank))
    else:
        entries.sort(key=lambda e: (e.z0, e.zmax, e.rank))
    mid = len(entries) // 2
    left, right = _tree(entries[:mid]), _tree(entries[mid:])
    if right.rank < left.rank:
        left, right = right, left
    return _Node(xmin, zmin, xmax, zmax, rank, left=left, right=right)


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
            entries.append(_Entry(field, rank, x0, z0,
                                  x0 + (field.n - 1) * field.step,
                                  z0 + (field.n - 1) * field.step))
        self._root = _tree(entries) if entries else None

    def _entry_for(self, x: float, z: float, margin: float) -> _Entry:
        best = None
        stack = [self._root] if self._root is not None else []
        while stack:
            node = stack.pop()
            if best is not None and node.rank >= best.rank:
                continue
            # A union bbox, expanded/shrunk by this same margin, is a
            # conservative dispatch bound even when the margin is negative.
            if not (node.xmin + margin <= x <= node.xmax - margin
                    and node.zmin + margin <= z <= node.zmax - margin):
                continue
            if node.entries:
                for entry in node.entries:
                    if best is not None and entry.rank >= best.rank:
                        break
                    # Keep Field.contains' operation order: regrouping these
                    # expressions or narrowing to float32 changes edge cases.
                    if (entry.x0 + margin <= x <= entry.xmax - margin
                            and entry.z0 + margin <= z <= entry.zmax - margin):
                        best = entry
                        break
            else:
                # Children are ordered by best possible priority, not by an
                # assumed non-overlap. Lower-priority branches are pruned only
                # after an actual containing field has won.
                stack.append(node.right)
                stack.append(node.left)
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
