"""Constructed transverse reduction/dispatch parity; not measured-world evidence."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import route as routemod

from crossing_contract import footprint_offsets, transverse_window


_POINTS = [(-2.125, -1.75), (-0.25, 0.375), (1.625, 2.875), (4.0, 3.125)]


def _height(x, z):
    return routemod.WATER_LEVEL + 0.375*x - 0.625*z


def _contains(x, z):
    return (-4.0 <= x <= 5.0 and -3.0 <= z <= 4.0
            and not (0.0 < x < 2.0 and z > 1.0))


def _same(left, right):
    # Numeric equality alone would hide changed signed zeros or NaN payloads.
    return (np.array_equal(left[0].view(np.uint64), right[0].view(np.uint64))
            and np.array_equal(left[1].view(np.uint64), right[1].view(np.uint64))
            and left[2:] == right[2:])


def _typed(off, wet):
    return (all(type(i) is int and all(type(v) is float for v in (lat, x, z))
                for i, lat, x, z in off)
            and all(type(i) is int and type(x) is float and type(z) is float
                    for x, z, i in wet))


def _geometry(name, points, *, all_dry=False, all_wet=False, collect=True):
    calls = [0, 0, 0]

    def height(x, z):
        if all_dry or all_wet:
            return np.zeros_like(x) + routemod.WATER_LEVEL + (-1 if all_wet else 0)
        return _height(x, z)

    def scalar(x, z):
        calls[0] += 1
        return float(height(x, z))

    def contains(x, z):
        calls[1] += 1
        return _contains(x, z)

    def bulk(xs, zs):
        calls[2] += 1
        covered = ((xs >= -4) & (xs <= 5) & (zs >= -3) & (zs <= 4)
                   & ~((xs > 0) & (xs < 2) & (zs > 1)))
        return np.asarray(height(xs, zs), dtype=np.float64), covered

    wet_left, wet_right = ([] if collect else None for _ in range(2))
    left = (*transverse_window(SimpleNamespace(bilinear=scalar, contains=contains),
                               points, 6.0, wet_at=wet_left), wet_left)
    right = (*transverse_window(SimpleNamespace(sample_many=bulk), points, 6.0,
                                wet_at=wet_right), wet_right)
    probes = len(points)*len(footprint_offsets(6.0))
    ordering = (left[2] == sorted(left[2], key=lambda item: (item[0], item[1])))
    wet_ok = (not collect or (not all_dry or not wet_right)
              and (not all_wet or len(wet_right) == probes))
    return {"case": name, "probes": probes, "uncovered": len(right[2]),
            "wet": None if wet_right is None else len(wet_right),
            "matches_expected": bool(_same(left, right) and ordering and wet_ok
                                     and _typed(right[2], wet_right or [])
                                     and calls == [probes, probes, 1])}


def _legacy_rows(xs, zs, heights, covered, lats, wet):
    """The pre-optimization bulk consumer, including dispatch/exception order."""
    lo, hi = np.empty(len(xs)), np.empty(len(xs))
    off = []
    for i in range(len(xs)):
        row_xs, row_zs, hs = xs[i], zs[i], heights[i]
        off.extend((i, float(lats[j]), float(row_xs[j]), float(row_zs[j]))
                   for j in np.flatnonzero(~covered[i]))
        if wet is not None:
            wet.extend((float(x), float(z), i)
                       for x, z, h in zip(row_xs, row_zs, hs)
                       if h < routemod.WATER_LEVEL)
        lo[i] = hs.min()
        hi[i] = hs.max()
    return lo, hi, off


class _ObservedArray(np.ndarray):
    def __new__(cls, values, events):
        out = np.asarray(values).view(cls)
        out.events = events
        return out

    def __array_finalize__(self, parent):
        self.events = getattr(parent, "events", None)

    def __getitem__(self, key):
        self.events.append(("getitem", repr(key)))
        return super().__getitem__(key)

    def min(self, *args, **kwargs):
        self.events.append(("min", self.shape))
        return super().min(*args, **kwargs)

    def max(self, *args, **kwargs):
        self.events.append(("max", self.shape))
        return super().max(*args, **kwargs)


class _WetObserver(list):
    def __init__(self, heights, events, *, mutate=False):
        super().__init__()
        self.heights, self.events, self.mutate = heights, events, mutate
        self.batches = 0

    def extend(self, values):
        batch = list(values)
        self.events.append(("extend", tuple(batch)))
        super().extend(batch)
        if self.mutate:
            self.heights[self.batches] = routemod.WATER_LEVEL + 100 + self.batches
        self.batches += 1


def _outcome(fn):
    try:
        return "value", fn()
    except Exception as exc:
        return type(exc).__name__, str(exc)


def _rows(name, build, *, collect=True, observer=False, mutate=False,
          lat_step=0.5):
    results, events, calls = [], [], [0]
    for legacy in (True, False):
        trace = []
        holder = {}
        wet = (_WetObserver(None, trace, mutate=mutate) if observer else []) if collect else None

        class Captured(Exception):
            pass

        def sample(xs, zs):
            calls[0] += 1
            heights, covered = build(xs, zs, trace)
            holder.update(xs=xs, zs=zs, heights=heights, covered=covered)
            if observer:
                wet.heights = heights
            if legacy:
                raise Captured
            return heights, covered

        def actual():
            return transverse_window(SimpleNamespace(sample_many=sample), _POINTS,
                                     6.0, lat_step, wet_at=wet)

        if legacy:
            # Capture the geometry without consuming the returned arrays, so
            # malformed shapes and custom row dispatch have a real old-loop oracle.
            try:
                actual()
            except Captured:
                pass
            result = _outcome(lambda: _legacy_rows(
                holder["xs"], holder["zs"], holder["heights"], holder["covered"],
                footprint_offsets(6.0, lat_step), wet))
        else:
            result = _outcome(actual)
        results.append((result, None if wet is None else list(wet)))
        events.append(trace)
    left, right = results
    same = left[0][0] == right[0][0] and left[1] == right[1]
    if left[0][0] == "value":
        same = same and _same((*left[0][1], left[1]), (*right[0][1], right[1]))
    else:
        same = same and left[0][1] == right[0][1]
    return {"case": name, "matches_expected": bool(same and events[0] == events[1]
                                                  and calls == [2])}


def _arrays(xs, zs, trace):
    return np.asarray(_height(xs, zs), dtype=np.float64), xs + zs < 3


def _variant(kind):
    def build(xs, zs, trace):
        heights, covered = _arrays(xs, zs, trace)
        if kind == "float32":
            heights = heights.astype(np.float32)
        elif kind == "swapped":
            heights = heights.astype(np.dtype(np.float64).newbyteorder("S"))
        elif kind == "fortran":
            heights = np.asfortranarray(heights)
        elif kind == "height_subclass":
            heights = _ObservedArray(heights, trace)
        elif kind == "coverage_subclass":
            covered = _ObservedArray(covered, trace)
        elif kind == "coverage_integer":
            covered = covered.astype(np.int64)
        elif kind == "height_collection":
            heights = list(heights)
        elif kind == "coverage_collection":
            covered = list(covered)
        elif kind == "extra_axis":
            heights = heights[..., None]
        elif kind == "empty_offsets":
            heights, covered = heights[:, :0], covered[:, :0]
        elif kind == "short_coverage":
            covered = covered[:-1]
        elif kind == "scalar_heights":
            heights = heights[:, 0]
        return heights, covered
    return build


def _special(xs, zs, trace):
    heights = np.zeros(xs.shape, dtype=np.float64)
    heights[0, 1::2] = -0.0
    heights[1] = -0.0
    heights[1, 1::2] = 0.0
    heights[2, 3] = np.nan
    heights[3, 0], heights[3, -1] = -np.inf, np.inf
    return heights, np.ones(xs.shape, dtype=np.bool_)


def _refusal():
    calls = []
    refusal = ValueError("nonfinite generated sample")

    def sample(xs, zs):
        calls.append(xs.shape)
        raise refusal

    try:
        transverse_window(SimpleNamespace(sample_many=sample), _POINTS, 6.0)
    except ValueError as exc:
        same = exc is refusal
    else:
        same = False
    return {"case": "transverse_bulk_sampler_refusal_unchanged",
            "matches_expected": bool(same and len(calls) == 1)}


def transverse_cases():
    """Return deterministic case records for the parent's acceptance runner."""
    cases = [
        _geometry("transverse_diagonal_holes_mixed_wet_order", _POINTS),
        _geometry("transverse_degenerate_tangents", [(0, 0)]*4),
        _geometry("transverse_water_level_is_dry", _POINTS, all_dry=True),
        _geometry("transverse_all_wet", _POINTS, all_wet=True),
        _geometry("transverse_without_wet_collection", _POINTS, collect=False),
        _rows("transverse_nan_infinity_signed_zero_bits", _special, lat_step=0.125),
        _rows("transverse_custom_extend_batches", _arrays, observer=True),
        _rows("transverse_custom_extend_mutation_order", _arrays,
              observer=True, mutate=True),
        _refusal(),
    ]
    cases.extend(_rows("transverse_fallback_"+kind, _variant(kind),
                       collect=kind not in ("empty_offsets", "scalar_heights",
                                            "extra_axis"))
                 for kind in ("float32", "swapped", "fortran", "height_subclass",
                              "coverage_subclass", "coverage_integer",
                              "height_collection", "coverage_collection",
                              "extra_axis", "empty_offsets",
                              "short_coverage", "scalar_heights"))
    return cases
