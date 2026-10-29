"""Constructed bulk sampler parity and query-work cases; not world evidence."""
from __future__ import annotations

import numpy as np

import field as fieldmod
from crossing_physical import CoverageError, PhysicalFields
from crossing_sampling import _QUERY_CHUNK, generated_many


def _field(name, x0, z0, n, *, step=1.0):
    i, j = np.mgrid[:n, :n]
    return fieldmod.Field(name, x0+n/2-step/2, z0+n/2-step/2,
                          n/2, step, n,
                          (17.0+0.125*j*j+0.75*i-0.25*i*j).astype(np.float32),
                          np.ones((n, n), dtype=np.uint8))


def _physical(*fields):
    return PhysicalFields({f.id: f for f in fields},
                          {"constructed": True}, keepout_discs=())


def _bulk(physical, xs, zs):
    return generated_many(physical.fields, physical._tiles, xs, zs, CoverageError)


def _scalar(physical, xs, zs):
    xs, zs = np.broadcast_arrays(xs, zs)
    values = [physical.generated(float(x), float(z))
              for x, z in zip(xs.flat, zs.flat)]
    return np.asarray(values, dtype=np.float64).reshape(xs.shape)


def _outcome(sample):
    try:
        return "values", sample()
    except (ValueError, OverflowError) as exc:
        return type(exc).__name__, str(exc)


def _parity(name, physical, xs, zs):
    expected = _outcome(lambda: _scalar(physical, xs, zs))
    actual = _outcome(lambda: _bulk(physical, xs, zs))
    matches = (expected[0] == actual[0]
               and (np.array_equal(expected[1], actual[1])
                    and actual[1].dtype == np.dtype(np.float64)
                    if expected[0] == "values" else expected[1] == actual[1]))
    return {"case": name, "matches_expected": bool(matches),
            "expected_kind": expected[0], "actual_kind": actual[0]}


def _adjacent():
    a, b = _field("a", -66, -3, 4), _field("b", -63, -3, 4)
    # A common world-coordinate surface, with asymmetric x/z contributions.
    for f in (a, b):
        i, j = np.mgrid[:f.n, :f.n]
        x, z = f.x0+j, f.z0+i
        f.h[:] = 30+0.125*x+0.75*z+0.0625*x*z
    physical = _physical(a, b)
    return _parity("bulk_adjacent_negative_tile_boundary_and_overlap", physical,
                   np.array([-65, -64.5, -64, -63.5, -63, -62.5]),
                   np.array([-3, -2.5, -2, -1.5, -1, -0.5]))


def _changed(value):
    a, b = _field("a", -2, -2, 5), _field("b", -2, -2, 5)
    physical = _physical(a, b)
    initial = _bulk(physical, [0.25], [0.5])
    expected_initial = _scalar(physical, [0.25], [0.5])
    b.h[2, 2] = value
    result = _parity(f"bulk_changed_overlap_{value}", physical, [0.25], [0.5])
    result["matches_expected"] &= (np.array_equal(initial, expected_initial)
                                   and result["expected_kind"] == "CoverageError")
    return result


def _first_agreeing():
    a, b = _field("a", 0, 0, 3), _field("b", 0, 0, 3)
    b.h[:] += np.float32(0.00005)
    result = _parity("bulk_returns_first_agreeing_source_not_average_or_last",
                     _physical(b, a), [0, 0.5, 1], [1, 0.5, 0])
    return result


def _first_error(across_chunks):
    a, b = _field("a", -66, -2, 4), _field("b", 62, -2, 4)
    a.h[:] = np.nan
    b.h[1, 1] = np.inf
    physical = _physical(a, b)
    count = _QUERY_CHUNK+2 if across_chunks else 3
    xs, zs = np.full(count, 64.0), np.zeros(count)
    first = _QUERY_CHUNK-1 if across_chunks else 0
    # The positive tile fails first in query order, though tile sorting visits
    # the negative tile first; its 00 corner is valid, its 10 corner is unread.
    xs[first], zs[first] = 62.5, -1
    xs[first+1], zs[first+1] = -65.5, -1.5
    return _parity("bulk_first_query_and_corner_error_"+
                   ("across_chunks" if across_chunks else "within_chunk"),
                   physical, xs, zs)


class _CountedHeights(np.ndarray):
    def __new__(cls, values, reads):
        result = np.asarray(values).view(cls)
        result.reads = reads
        return result

    def __array_finalize__(self, source):
        self.reads = getattr(source, "reads", None)

    def __getitem__(self, key):
        if self.reads is not None:
            self.reads[0] += 1
        return super().__getitem__(key)


def _query_work():
    def work(count, unrelated):
        local = _field("local", -2, -2, 5)
        remote = [_field(f"remote-{k}", 1024+64*k, 1024, 3)
                  for k in range(unrelated)]
        physical = _physical(local, *remote)
        expected = physical.generated(0.25, 0.5)
        reads = [0]
        local.h = _CountedHeights(local.h, reads)
        actual = _bulk(physical, np.full(count, 0.25), np.full(count, 0.5))
        return reads[0], bool(np.all(actual == expected))

    small, large, remote = work(1, 0), work(4096, 0), work(4096, 128)
    return {"case": "bulk_array_reads_scale_by_tiles_not_scalar_queries",
            "small_array_reads": small[0], "large_array_reads": large[0],
            "remote_array_reads": remote[0],
            "matches_expected": (small[1] and large[1] and remote[1]
                                 and 0 < large[0] <= small[0]+4
                                 and remote[0] <= large[0]+4)}


def _footprint(custom=False):
    from types import SimpleNamespace
    from crossing_contract import transverse_window

    class OffsetPhysical(PhysicalFields):
        def vertex(self, x, z):
            return super().vertex(x, z)+5.1

    f = _field("sweep", -7, -5, 13)
    reads = [0]
    f.h = _CountedHeights(f.h, reads)
    cls = OffsetPhysical if custom else PhysicalFields
    physical = cls({f.id: f}, {"constructed": True}, keepout_discs=())
    points = [(-1.1, -0.1), (0.13, 0.37), (1.9, 1.1)]
    outcomes, work = [], []
    for batched in (False, True):
        sampler = SimpleNamespace(bilinear=physical.generated,
                                  contains=physical.contains)
        if batched:
            sampler.sample_many = physical.sample_many
        reads[0] = 0
        wet = []
        lo, hi, off = transverse_window(sampler, points, 6.0, wet_at=wet)
        outcomes.append((lo, hi, off, wet))
        work.append(reads[0])
    left, right = outcomes
    same = (np.array_equal(left[0], right[0])
            and np.array_equal(left[1], right[1]) and left[2:] == right[2:])
    return {"case": "bulk_full_footprint_"+("custom_dispatch" if custom else "native"),
            "scalar_reads": work[0], "batch_reads": work[1],
            "matches_expected": bool(same and (custom or work[1] < work[0]))}


def bulk_cases():
    physical = _physical(_field("asymmetric", -7, -5, 13))
    integer_only = _physical(_field("single", -64, -64, 1))
    unread = _field("unread", -2, -2, 3)
    unread.h[0, 0] = np.nan
    holes = _physical(_field("corner-a", 0, 0, 1),
                      _field("corner-b", 1, 1, 1))
    fractional = _field("fractional", -7, -5, 13)
    rng = np.random.default_rng(8123)
    fractional.h[:] = rng.uniform(20, 40, fractional.h.shape)
    return [
        _parity("bulk_asymmetric_broadcast_integer_half_and_fractional", physical,
                np.array([[-6], [-2.5], [0], [3.125]]),
                np.array([[-4, -2.5, 0.25, 5]])),
        _parity("bulk_scalar_shape", physical, -1.5, 0.5),
        _parity("bulk_empty_broadcast", physical, np.empty((0, 1)), np.zeros((1, 3))),
        _adjacent(), _first_agreeing(),
        _parity("bulk_compensated_float_sum_on_nondyadic_queries",
                _physical(fractional), rng.uniform(-6, 4, 4096),
                rng.uniform(-4, 6, 4096)),
        _parity("bulk_actual_step_and_tolerated_origin", _physical(
            _field("nearly-native", -3+0.5e-6, -2-0.5e-6, 7,
                   step=1+0.5e-9)), [-3, -2.5, 0, 2.5], [-2, -1.5, 0, 2.5]),
        _changed(99.0), _changed(np.nan), _changed(np.inf), _changed(-np.inf),
        _parity("bulk_zero_weight_unread_corners_are_not_sampled",
                integer_only, [-64], [-64]),
        _parity("bulk_nonoverlap_nan_refuses", _physical(unread), [-2], [-2]),
        _parity("bulk_holes_are_not_filled_by_tile_membership", holes, [0.5], [0.5]),
        _first_error(False), _first_error(True),
        _parity("bulk_coverage_error_precedes_later_nonfinite_query", holes,
                [0.5, np.nan], [0.5, 0]),
        _parity("bulk_nonfinite_query_scalar_floor_error", physical,
                [0, np.inf], [0, 0]),
        _query_work(),
        _footprint(), _footprint(custom=True),
    ]
