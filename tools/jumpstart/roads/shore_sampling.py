"""Exact spatial dispatch for one solve's immutable generated Heightmap fields.

This is not an applied/live-terrain cache. The caller must first validate the
source bytes, seed, lattice and coverage, and must construct a fresh sampler
when the field collection or its geometry changes. Field objects and their
height arrays stay owned by that solve; no arrays are copied or smoothed.
"""
from __future__ import annotations

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
