"""Constructed water-feedback masks; sparse geometric witnesses, not world evidence."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

import route


def _corridor(n=385, *, step=1.0, dec=1, x0=-200.25, z0=-300.25,
              wet=()):
    heights = np.full((n, n), route.WATER_LEVEL + 20.0)
    for i, j in wet:
        heights[i, j] = route.WATER_LEVEL - 1.0
    field = SimpleNamespace(n=n, step=step, x0=x0, z0=z0, h=heights)
    return SimpleNamespace(fld=field, m=n // dec, cell_m=dec * step,
                           x0=x0 + (dec - 1) * step / 2,
                           z0=z0 + (dec - 1) * step / 2)


def _seeds(corr, heights):
    f = corr.fld
    return [(f.x0 + j * f.step, f.z0 + i * f.step, y)
            for (i, j), y in heights.items()]


def _hazards(points, seed_heights, radius, step=1.0):
    """Independent sparse witness for one explicitly connected wet body."""
    ordered = sorted(seed_heights, key=lambda p: (p[1], p[0]))
    result = dict(seed_heights)
    for i, j in points:
        nearest = min(ordered, key=lambda p: (i - p[0]) ** 2 + (j - p[1]) ** 2)
        if math.sqrt((i - nearest[0]) ** 2 + (j - nearest[1]) ** 2) * step <= radius:
            result[i, j] = seed_heights[nearest]
    return result


def _expected(corr, hazards, *, half_m, grade, reach_m):
    """Brute-force point geometry: no EDT, tiles, labels, or production helpers.

    Hazards are explicit fixture facts. Sorting by (column,row) expresses the
    pinned SciPy 1.11.4 nearest-feature tie rule, independently of its EDT.
    """
    expected = np.zeros((corr.m, corr.m), dtype=bool)
    if not hazards:
        return expected
    f = corr.fld
    ci, cj = np.mgrid[:corr.m, :corr.m]
    fi = np.rint((corr.z0 + ci * corr.cell_m - f.z0) / f.step).astype(int)
    fj = np.rint((corr.x0 + cj * corr.cell_m - f.x0) / f.step).astype(int)
    ordered = sorted(hazards, key=lambda p: (p[1], p[0]))
    hi = np.array([p[0] for p in ordered])
    hj = np.array([p[1] for p in ordered])
    yy = np.array([hazards[p] for p in ordered])
    # A row at a time bounds witness allocation even for long connected bodies.
    slack = corr.cell_m * math.sqrt(0.5) + route.WATER_STANDOFF_SIMPLIFY_M
    for i in range(corr.m):
        distances = ((fi[i, :, None] - hi) ** 2
                     + (fj[i, :, None] - hj) ** 2)
        nearest = distances.argmin(axis=1)
        dist = np.sqrt(distances[np.arange(corr.m), nearest]) * f.step
        reach = np.minimum(np.maximum(yy[nearest] - route.WATER_LEVEL, 0.0)
                           / grade, reach_m)
        expected[i] = ((fi[i] >= 0) & (fi[i] < f.n)
                       & (fj[i] >= 0) & (fj[i] < f.n)
                       & (dist < half_m + reach + slack))
    return expected


def _case(name, corr, seeds, hazards, parameters, *, probes=()):
    actual = route.water_standoff_forbid(corr, seeds, **parameters)
    expected = _expected(corr, hazards, **parameters)
    return dict(case=name, matches_expected=bool(
        np.array_equal(actual, expected)
        and all(bool(actual[i, j]) == value for i, j, value in probes)),
        forbidden_cells=int(actual.sum()),
        expected_forbidden_cells=int(expected.sum()),
        probes=[dict(row=i, column=j, expected=value, actual=bool(actual[i, j]))
                for i, j, value in probes])


def _sparse_transform_work_case(parameters):
    """Separating two hazard clusters must not transform their entire dry gap."""
    work = []
    correct = True
    original = route.ndimage.distance_transform_edt
    for n in (513, 2049):
        corr = _corridor(n)
        heights = {(0, 0): route.WATER_LEVEL + 12,
                   (n - 1, n - 1): route.WATER_LEVEL + 12}
        processed = 0

        def measured(array, *args, **kwargs):
            nonlocal processed
            processed += array.size
            return original(array, *args, **kwargs)

        route.ndimage.distance_transform_edt = measured
        try:
            actual = route.water_standoff_forbid(
                corr, _seeds(corr, heights), **parameters)
        finally:
            route.ndimage.distance_transform_edt = original
        correct &= np.array_equal(actual, _expected(corr, heights, **parameters))
        work.append(processed)
    return dict(case="water_sparse_hazards_do_not_transform_quadratic_dry_gap",
                matches_expected=bool(correct and work[1] <= 4 * work[0]),
                transformed_samples=work, complete_masks_correct=bool(correct))


def water_feedback_cases():
    """Adversarial full output masks for bounded exact water feedback."""
    water = route.WATER_LEVEL
    parameters = dict(half_m=0.0, grade=1.0, reach_m=12.0)
    anchors = {(0, 0): water, (384, 384): water}
    rows = []

    # Anchors retain the entire original crop, placing row/column 128 exactly
    # on tile boundaries. Smaller column beats smaller row AND greater height.
    corr = _corridor()
    heights = {**anchors, (126, 130): water + 12, (130, 126): water}
    rows.append(_case("water_seed_tie_across_tile_boundary_uses_column_then_row",
        corr, _seeds(corr, heights), heights, parameters,
        probes=((128, 128, False),)))
    heights = {**anchors, (126, 128): water + 12, (130, 128): water}
    rows.append(_case("water_same_column_seed_tie_uses_smaller_row",
        corr, _seeds(corr, heights), heights, parameters,
        probes=((128, 128, True),)))

    heights = {(0, 384): water + 12, (384, 0): water + 12,
               (150, 150): water + 12}
    rows.append(_case("water_query_core_halo_without_any_feature_stays_empty",
        corr, _seeds(corr, heights), heights, parameters,
        probes=((0, 0, False), (127, 127, False))))

    wet = {(127, 129), (128, 128), (129, 127)}
    corr = _corridor(wet=wet)
    heights = {**anchors, (126, 130): water + 12, (130, 126): water}
    radius = parameters["reach_m"] + math.sqrt(0.5) + 1.0
    hazards = _hazards(wet, heights, radius)
    rows.append(_case("water_hazard_height_tie_survives_two_tile_transforms",
        corr, _seeds(corr, heights), hazards, parameters,
        probes=((130, 130, False),)))

    corr = _corridor()
    heights = {**anchors, (128, 128): water + 8}
    repeated = _seeds(corr, anchors) + [
        (corr.fld.x0 + 128.1, corr.fld.z0 + 128.1, water + 8),
        (corr.fld.x0 + 128.2, corr.fld.z0 + 128.2, water)]
    rows.append(_case("water_repeated_snapped_seed_keeps_max_height",
        corr, repeated, heights, parameters, probes=((128, 132, True),)))
    rows.append(_case("water_repeated_snapped_seed_max_height_is_order_independent",
        corr, list(reversed(repeated)), heights, parameters,
        probes=((128, 132, True),)))

    # The two arms connect at row105, outside row128 core's 15-cell halo.
    # The right arm is nevertheless in the SAME full-crop wet component.
    wet = ({(i, 145) for i in range(105, 129)}
           | {(i, 155) for i in range(105, 129)}
           | {(105, j) for j in range(145, 156)})
    corr = _corridor(wet=wet)
    heights = {**anchors, (128, 145): water + 12}
    rows.append(_case("water_body_connects_only_outside_tile_halo",
        corr, _seeds(corr, heights), _hazards(wet, heights, radius), parameters,
        probes=((128, 160, True),)))

    # Fractional origins, negative world coordinates, field-edge sources and
    # genuine pooled-corridor half-sample round-to-even coordinates.
    corr = _corridor(33, step=0.5, dec=4, x0=-19.75, z0=-29.75)
    heights = {(0, 0): water + 2, (32, 32): water + 3}
    edge_seeds = _seeds(corr, heights)
    edge_seeds[0] = (corr.fld.x0 - 0.1, corr.fld.z0 - 0.1, water + 2)
    rows.append(_case("water_negative_fractional_crop_edges_and_coarse_rounding",
        corr, edge_seeds, heights,
        dict(half_m=0.5, grade=1.0, reach_m=3.0)))

    corr = _corridor(65, dec=2, x0=-40.0, z0=-60.0)
    slack = corr.cell_m * math.sqrt(0.5) + 1.0
    strict = dict(half_m=4.0 - slack, grade=1.0, reach_m=0.0)
    heights = {(16, 16): water}
    rows.append(_case("water_coarse_half_even_snapping_and_strict_reach_boundary",
        corr, _seeds(corr, heights), heights, strict,
        probes=((8, 9, True), (8, 10, False))))

    wet = {(32, j) for j in range(32, 37)}
    corr = _corridor(65, wet=wet)
    inclusive = dict(half_m=0.0, grade=1.0,
                     reach_m=4.0 - math.sqrt(0.5) - 1.0)
    heights = {(32, 32): water + 12}
    hazards = {point: water + 12 for point in wet}
    rows.append(_case("water_hazard_radius_inclusive_final_radius_strict",
        corr, _seeds(corr, heights), hazards, inclusive,
        probes=((32, 39, True), (32, 40, False))))

    corr = _corridor(129, dec=2)
    heights = {(i, j): water + (12 if (i + j) % 32 else 0)
               for i in range(0, 129, 16) for j in range(0, 129, 16)}
    rows.append(_case("water_dense_seed_large_halo_uses_exact_full_domain",
        corr, _seeds(corr, heights), heights,
        dict(half_m=0.0, grade=1.0, reach_m=40.0)))

    corr = _corridor(33)
    for name, seeds in (
            ("water_empty_seeds", []),
            ("water_no_seed_in_positive_crop", [(1e6, 1e6, water + 12)]),
            ("water_no_seed_in_negative_crop", [(-1e6, -1e6, water + 12)])):
        rows.append(_case(name, corr, seeds, {}, parameters))
    rows.append(_sparse_transform_work_case(parameters))
    return rows
