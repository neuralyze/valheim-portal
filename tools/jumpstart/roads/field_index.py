"""Exact spatial dispatch over scope-owned, immutable field geometry.

Callers supply containment bounds and priority ranks; this index retains only
metadata and field references, never sampled heights or applied terrain.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldEntry:
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
    entries: tuple[FieldEntry, ...] = ()
    left: _Node | None = None
    right: _Node | None = None


def _tree(entries: list[FieldEntry]) -> _Node:
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


class FieldIndex:
    """Select the lowest-ranked entry containing every supplied endpoint.

    Bounds are inclusive, with the same margin applied to each endpoint.
    Construct a fresh index whenever the field collection or geometry changes.
    Assign distinct ranks to encode caller ordering, including priority ties.
    Storage is O(number of fields); overlaps may require additional traversal
    to preserve exact priority rather than accepting the first spatial hit.
    """

    def __init__(self, entries):
        entries = list(entries)
        self._root = _tree(entries) if entries else None

    def find(self, x: float, z: float, margin: float = 0.0,
             x2: float | None = None, z2: float | None = None) -> FieldEntry | None:
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
            if x2 is not None and not node.xmin + margin <= x2 <= node.xmax - margin:
                continue
            if z2 is not None and not node.zmin + margin <= z2 <= node.zmax - margin:
                continue
            if node.entries:
                for entry in node.entries:
                    if best is not None and entry.rank >= best.rank:
                        break
                    # Keep containment's operation order: regrouping these
                    # expressions or narrowing to float32 changes edge cases.
                    if (entry.x0 + margin <= x <= entry.xmax - margin
                            and entry.z0 + margin <= z <= entry.zmax - margin
                            and (x2 is None or entry.x0 + margin <= x2 <= entry.xmax - margin)
                            and (z2 is None or entry.z0 + margin <= z2 <= entry.zmax - margin)):
                        best = entry
                        break
            else:
                # Children are ordered by best possible priority, not by an
                # assumed non-overlap. Lower-priority branches are pruned only
                # after an actual containing field has won.
                stack.append(node.right)
                stack.append(node.left)
        return best
