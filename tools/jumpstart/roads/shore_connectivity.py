"""Component-complete identity on the measured rendered playable world.

Coarse labels and clipped local labels NEVER certify disconnection. Production
requires the entire playable domain at 1m, requested by shore_scope before any
scan. Local graphs receive slices of these actual rendered component labels.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

PATCH_ID = "shore-connectivity-world"


class CompleteLand:
    def __init__(self, field, radius_m):
        self.field = field
        self.radius_m = float(radius_m)
        if (abs(field.step-1.0) > 1e-9
                or any(abs(v-round(v)) > 1e-6 for v in (field.x0, field.z0))
                or not field.contains(-self.radius_m, -self.radius_m)
                or not field.contains(self.radius_m, self.radius_m)):
            raise SystemExit("REFUSING rendered component identity: incomplete playable-domain 1m coverage; rerun the canonical field request")
        # Chunking avoids materializing a whole-world float64 coordinate mesh.
        land = np.empty(field.heights.shape, dtype=bool)
        xs2 = field.world_x(np.arange(field.n)) ** 2
        for first in range(0, field.n, 256):
            last = min(first+256, field.n)
            zs2 = field.world_z(np.arange(first, last)) ** 2
            playable = xs2[None, :] + zs2[:, None] <= self.radius_m ** 2
            heights = field.heights[first:last]
            if not np.isfinite(heights[playable]).all():
                raise SystemExit("REFUSING rendered component identity: unread playable-domain heights")
            land[first:last] = playable & (heights > 30.0)
        # Conservative 8-connectivity never calls diagonally touching rendered
        # land two islands; actual road/capsule feasibility is measured separately.
        self.labels, _ = ndimage.label(land, structure=np.ones((3, 3), dtype=bool))
        self.area_m2 = np.bincount(self.labels.ravel()).astype(np.float64)
        self.area_m2[0] = 0.0

    @classmethod
    def from_fields(cls, fields, receipt):
        from north_itinerary import WORLD_RADIUS_M
        contract = receipt.get("rendered_connectivity") or {}
        if (contract.get("schema_version") != 1
                or contract.get("patch") != PATCH_ID
                or contract.get("radius_m") != WORLD_RADIUS_M
                or contract.get("connectivity") != 8
                or PATCH_ID not in fields):
            raise SystemExit("REFUSING ferry identity: missing complete rendered-world connectivity request; rerun roads/field.py request, never promote cropped/coarse labels")
        import hashlib
        digest = hashlib.sha256(memoryview(fields[PATCH_ID].heights).cast("B")).hexdigest()
        if digest != contract.get("height_sha256"):
            raise SystemExit("REFUSING ferry identity: rendered world differs from the world that nominated the shore request")
        return cls(fields[PATCH_ID], WORLD_RADIUS_M)

    def at(self, x, z):
        if x*x + z*z > self.radius_m*self.radius_m:
            return 0
        return int(self.labels[self.field.index(x, z)])

    def labels_for(self, field):
        if abs(field.step-1.0) > 1e-9:
            raise SystemExit("REFUSING component projection onto a non-1m field")
        dx, dz = field.x0-self.field.x0, field.z0-self.field.z0
        if abs(dx-round(dx)) > 1e-6 or abs(dz-round(dz)) > 1e-6:
            raise SystemExit("REFUSING component projection: different Heightmap lattice")
        dx, dz = int(round(dx)), int(round(dz))
        out = np.zeros((field.n, field.n), dtype=self.labels.dtype)
        i0, j0 = max(0, -dz), max(0, -dx)
        i1, j1 = min(field.n, self.field.n-dz), min(field.n, self.field.n-dx)
        if i0 < i1 and j0 < j1:
            out[i0:i1, j0:j1] = self.labels[dz+i0:dz+i1, dx+j0:dx+j1]
        # Samples outside the complete square are necessarily outside the
        # playable disc, not unsurveyed holes inside its component population.
        return out


def selftest():
    import water as W
    n = 201
    ax = np.arange(n)-100
    xx, zz = np.meshgrid(ax, ax)
    # Two arms look disconnected in a cropped central ferry rectangle; a real
    # dry connection passes outside that rectangle across the top of the world.
    h = np.where(((abs(xx) >= 35) & (abs(xx) <= 50) & (abs(zz) <= 70))
                 | ((zz >= 55) & (zz <= 70) & (abs(xx) <= 50)), 35.0, 20.0).astype(np.float32)
    field = W.Field("constructed-complete", 0, 0, 100.5, 1, n, h,
                    np.ones((n, n), dtype=np.uint8))
    complete = CompleteLand(field, 95)
    assert complete.at(-40, 0) == complete.at(40, 0) != 0
    cropped = h[75:126, 40:161]
    local, _ = ndimage.label(cropped > 30.0)
    assert local[25, 20] != local[25, 100]
    disconnected = h.copy()
    disconnected[(zz >= 55) & (abs(xx) < 35)] = 20.0
    from dataclasses import replace
    separate = CompleteLand(replace(field, heights=disconnected), 95)
    assert separate.at(-40, 0) != separate.at(40, 0)
    try:
        CompleteLand(replace(field, half=50.5, n=101, heights=h[50:151, 50:151],
                             biomes=np.ones((101, 101), dtype=np.uint8)), 95)
    except SystemExit:
        pass
    else:
        raise AssertionError("cropped field certified global disconnection")
    return 0


def candidate_landings(complete, source_xz, min_area_m2):
    """Nominate every actual major eastern rendered component, not coarse IDs."""
    from scipy.spatial import cKDTree
    labels, field = complete.labels, complete.field
    source_label = complete.at(*source_xz)
    if source_label == 0:
        raise SystemExit("REFUSING ferry topology request: actual spawn has no rendered land component")
    objects = ndimage.find_objects(labels)

    def shoreline(label):
        sl = objects[label-1]
        mask = labels[sl] == label
        ii, jj = np.nonzero(mask & ~ndimage.binary_erosion(mask,
            structure=np.ones((3, 3), dtype=bool), border_value=0))
        ii += sl[0].start
        jj += sl[1].start
        return np.column_stack((field.world_x(jj), field.world_z(ii)))

    source_points = shoreline(source_label)
    source_cx = float(source_points[:, 0].mean())
    east = source_points[source_points[:, 0] >= source_cx]
    tree = cKDTree(east)
    out = []
    for label in np.flatnonzero(complete.area_m2 >= min_area_m2):
        label = int(label)
        if label == source_label:
            continue
        points = shoreline(label)
        if float(points[:, 0].mean()) <= source_cx:
            continue
        distances, indices = tree.query(points)
        k = int(np.argmin(distances))
        out.append(dict(rendered_component=label, rendered_source_component=source_label,
            area_m2=float(complete.area_m2[label]), min_area_m2=float(min_area_m2),
            probe_xz=points[k].tolist(), source_shore_xz=east[int(indices[k])].tolist(),
            measured_gap_m=float(distances[k])))
    out.sort(key=lambda c: (c["measured_gap_m"], -c["area_m2"], c["rendered_component"]))
    return out
