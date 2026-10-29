"""Deterministic continuous-clearance fixtures, never measured-world evidence."""
from __future__ import annotations

import math

import numpy as np

import field as fieldmod
import route
from crossing_bank_clearance import wet_bank_certificate
from crossing_physical import CoverageError, PhysicalFields


def _field(name, surface, extent=8, *, dtype=np.float32):
    z, x = np.mgrid[-extent:extent+1, -extent:extent+1]
    n = 2*extent+1
    heights = np.broadcast_to(surface(x, z), (n, n)).astype(dtype).copy()
    return fieldmod.Field(name, 0.0, 0.0, n/2, 1.0, n, heights,
                          np.ones((n, n), dtype=np.uint8))


def _physical(*fields):
    return PhysicalFields({f.id: f for f in fields},
        {"constructed": True, "surface": "heightmap",
         "scope": "explicit synthetic lattice, not measured-world evidence"},
        keepout_discs=())


def _island(name="small-island", extent=8):
    return _field(name, lambda x, z: np.where(
        (abs(x) <= 1) & (abs(z) <= 1), route.WATER_LEVEL+4,
        route.WATER_LEVEL-4), extent)


def _scalar_chord(physical, bank, radius, angle):
    # This independent witness uses production SCALAR generated(), not the
    # certificate's snapshot, trigonometric intervals or interpolation helper.
    x, z = bank["xz"]
    nx, nz = math.cos(angle), math.sin(angle)
    heights = [physical.generated(x+radius*i/32*nx, z+radius*i/32*nz)
               for i in range(-32, 33)]
    return dict(angle_rad=angle, endpoint_heights_m=[heights[0], heights[-1]],
                sampled_chord_min_m=min(heights),
                endpoints_dry=bool(min(heights[0], heights[-1]) >= route.WATER_LEVEL),
                sampled_chord_dry=bool(min(heights) >= route.WATER_LEVEL))


def _scalar_wet(physical, bank, radius):
    x, z = bank["xz"]
    pair_minima = []
    for k in range(129):
        theta = math.pi*k/128
        dx, dz = radius*math.cos(theta), radius*math.sin(theta)
        pair_minima.append(min(physical.generated(x+dx, z+dz),
                               physical.generated(x-dx, z-dz)))
    # This is only a regression witness, NEVER the all-orientation proof.
    return dict(independent_scalar_angles=129,
                largest_endpoint_pair_min_m=max(pair_minima),
                every_sampled_pair_has_wet_endpoint=bool(
                    max(pair_minima) < route.WATER_LEVEL))


def _audit(physical, certificate):
    if certificate is None:
        return False
    end = 0.0
    water = route.WATER_LEVEL
    for interval in certificate["covered_angle_intervals"]:
        low, high = interval["angle_rad"]
        if low != end or not low < high or interval["endpoint_sign"] not in (-1, 1):
            return False
        axes = []
        for lower, upper in interval["endpoint_box_xz"]:
            axes.append([lower, *range(math.floor(lower)+1, math.ceil(upper)), upper])
        # Independent scalar extrema on every grid-clipped rectangle, including
        # fractional corners and grid-line crossings. No proof-module private
        # helpers or cached heights are used to check its reported upper bound.
        maximum = max(physical.generated(x, z) for x in axes[0] for z in axes[1])
        if not maximum <= interval["generated_upper_m"] < water:
            return False
        end = high
    return end >= math.pi and end == certificate["angle_domain_rad"][1]


def _certified_island():
    physical, bank, radius = _physical(_island()), {"xz": [0.0, 0.0]}, 4.0
    certificate = wet_bank_certificate(physical, bank, radius)
    witness = _scalar_wet(physical, bank, radius)
    return dict(case="too_small_dry_island_certifies_every_orientation",
        matches_expected=bool(_audit(physical, certificate)
            and witness["every_sampled_pair_has_wet_endpoint"]),
        certificate_returned=certificate is not None, scalar_witness=witness,
        certified_intervals=0 if certificate is None else
            len(certificate["covered_angle_intervals"]))


def _dry_case(name, surface, radius, angle, *, extent=8):
    physical = _physical(_field(name, surface, extent))
    bank = {"xz": [0.0, 0.0]}
    witness = _scalar_chord(physical, bank, radius, angle)
    certificate = wet_bank_certificate(physical, bank, radius)
    return dict(case=name, matches_expected=bool(certificate is None
        and witness["endpoints_dry"] and witness["sampled_chord_dry"]),
        certificate_returned=certificate is not None, scalar_witness=witness)


def _bank_changes():
    field = _field("separate-banks", lambda x, z: np.where(
        ((abs(x) <= 1) & (abs(z) <= 1)) | (x >= 3),
        route.WATER_LEVEL+4, route.WATER_LEVEL-4), 16)
    physical = _physical(field)
    bank = {"xz": [0.0, 0.0]}
    initial = wet_bank_certificate(physical, bank, 4.0)
    bank["xz"][:] = [8.0, 0.0]
    changed = wet_bank_certificate(physical, bank, 4.0)
    witness = _scalar_chord(physical, bank, 4.0, 0.0)
    return dict(case="changed_actual_bank_is_not_cached",
        matches_expected=bool(initial is not None and changed is None
            and witness["sampled_chord_dry"]),
        certificate_sequence=[initial is not None, changed is not None],
        scalar_witness=witness)


def _height_changes():
    field = _island()
    physical, bank = _physical(field), {"xz": [0.0, 0.0]}
    initial = wet_bank_certificate(physical, bank, 4.0)
    field.h[:] = route.WATER_LEVEL+4
    changed = wet_bank_certificate(physical, bank, 4.0)
    witness = _scalar_chord(physical, bank, 4.0, 0.0)
    return dict(case="changed_heights_are_not_cached",
        matches_expected=bool(initial is not None and changed is None
            and witness["sampled_chord_dry"]),
        certificate_sequence=[initial is not None, changed is not None],
        scalar_witness=witness)


def _scalar_unread(physical, point):
    try:
        physical.generated(*point)
    except CoverageError:
        return True
    return False


def _hole_changes():
    field = _island()
    physical, bank = _physical(field), {"xz": [0.0, 0.0]}
    initial = wet_bank_certificate(physical, bank, 4.0)
    old = float(field.h[8, 12])  # native endpoint (4,0)
    field.h[8, 12] = np.nan
    changed = wet_bank_certificate(physical, bank, 4.0)
    unread = _scalar_unread(physical, (4.0, 0.0))
    field.h[8, 12] = old
    repaired = wet_bank_certificate(physical, bank, 4.0)
    return dict(case="hole_addition_and_repair_are_not_cached",
        matches_expected=bool(initial is not None and changed is None
            and unread and _audit(physical, repaired)),
        certificate_sequence=[initial is not None, changed is not None,
                              repaired is not None],
        independent_scalar_observed_hole=unread)


def _overlap_changes():
    a, b = _island("a"), _island("b")
    physical, bank = _physical(a, b), {"xz": [0.0, 0.0]}
    initial = wet_bank_certificate(physical, bank, 4.0)
    old = float(b.h[8, 12])
    b.h[8, 12] = old+1
    changed = wet_bank_certificate(physical, bank, 4.0)
    disagreement = _scalar_unread(physical, (4.0, 0.0))
    b.h[8, 12] = old
    repaired = wet_bank_certificate(physical, bank, 4.0)
    return dict(case="overlap_disagreement_and_repair_are_not_cached",
        matches_expected=bool(initial is not None and changed is None
            and disagreement and _audit(physical, repaired)),
        certificate_sequence=[initial is not None, changed is not None,
                              repaired is not None],
        independent_scalar_observed_disagreement=disagreement)


def _unread_extra_domain():
    # The complete actual radius-4 circle is measured, but outward coordinate
    # inflation asks for adjacent vertices beyond this optional snapshot. The
    # optimization must remain unknown, not convert extra unread into refusal.
    physical, bank = _physical(_island(extent=4)), {"xz": [0.0, 0.0]}
    certificate = wet_bank_certificate(physical, bank, 4.0)
    witness = _scalar_wet(physical, bank, 4.0)
    return dict(case="unread_extra_snapshot_domain_returns_unknown",
        matches_expected=bool(certificate is None
            and witness["every_sampled_pair_has_wet_endpoint"]),
        certificate_returned=certificate is not None, scalar_witness=witness)


def _invalid_extra_vertex(value):
    field = _island()
    field.h[3, 3] = value  # (-5,-5), outside every actual radius-4 endpoint
    physical, bank = _physical(field), {"xz": [0.0, 0.0]}
    certificate = wet_bank_certificate(physical, bank, 4.0)
    witness = _scalar_wet(physical, bank, 4.0)
    unread = _scalar_unread(physical, (-5.0, -5.0))
    return dict(case="nonfinite_extra_vertex_returns_unknown_"+str(value),
        matches_expected=bool(certificate is None and unread
            and witness["every_sampled_pair_has_wet_endpoint"]),
        certificate_returned=certificate is not None,
        independent_scalar_observed_unread_extra=unread, scalar_witness=witness)


def _disagreeing_extra_vertex():
    a, b = _island("a"), _island("b")
    b.h[3, 3] += 1  # also outside the actual endpoint circle
    physical, bank = _physical(a, b), {"xz": [0.0, 0.0]}
    certificate = wet_bank_certificate(physical, bank, 4.0)
    witness = _scalar_wet(physical, bank, 4.0)
    disagreement = _scalar_unread(physical, (-5.0, -5.0))
    return dict(case="disagreeing_extra_vertex_returns_unknown",
        matches_expected=bool(certificate is None and disagreement
            and witness["every_sampled_pair_has_wet_endpoint"]),
        certificate_returned=certificate is not None,
        independent_scalar_observed_disagreement=disagreement,
        scalar_witness=witness)


def _narrow_between_angles():
    # A 4-by-3 half-rectangle has its longest chord at atan2(3,4), not at a
    # regular angular probe. Only ~1e-6 radians around that angle survive: dry
    # boundary vertices stand just 1e-4 m above water; adjacent vertices are
    # 10 m below it. Its entire exact diagonal chord is nevertheless dry.
    field = _field("narrow-diagonal", lambda x, z: np.where(
        (abs(x) <= 4) & (abs(z) <= 3), route.WATER_LEVEL+0.0001,
        route.WATER_LEVEL-10))
    physical, bank, radius = _physical(field), {"xz": [0.0, 0.0]}, 5.0
    angle = math.atan2(3.0, 4.0)
    witness = _scalar_chord(physical, bank, radius, angle)
    coarse = _scalar_wet(physical, bank, radius)
    certificate = wet_bank_certificate(physical, bank, radius)
    return dict(case="between_angle_narrow_viable_chord_is_not_erased",
        matches_expected=bool(certificate is None and witness["sampled_chord_dry"]
            and coarse["every_sampled_pair_has_wet_endpoint"]),
        certificate_returned=certificate is not None, scalar_witness=witness,
        misleading_regular_angle_witness=coarse)


def _computed_transverse_normal():
    field = _island("computed-normal")
    # Synthetic translation stresses coordinate-addition rounding independently
    # of the normal's hypot/division rounding; this is not a world coordinate.
    field.cx, field.cz = 100000000.0, -100000000.0
    bank, radius = {"xz": [field.cx+0.25, field.cz+0.125]}, 4.0
    physical = _physical(field)
    tangent = np.asarray([7.0, 3.0], dtype=np.float64)
    norm = np.hypot(tangent[0], tangent[1])
    normal = np.asarray([tangent[1]/norm, -tangent[0]/norm])
    # The endpoint pair is unchanged by flipping the normal into [0,pi].
    if normal[1] < 0:
        normal = -normal
    angle = math.atan2(float(normal[1]), float(normal[0]))
    certificate = wet_bank_certificate(physical, bank, radius)
    witnesses, contained = [], False
    if certificate is not None:
        interval = next(record for record in certificate["covered_angle_intervals"]
                        if record["angle_rad"][0] <= angle <= record["angle_rad"][1])
        sign = interval["endpoint_sign"]
        point = np.asarray(bank["xz"]) + normal*(sign*radius)
        contained = all(low <= coordinate <= high
            for coordinate, (low, high) in zip(point, interval["endpoint_box_xz"]))
        witnesses = [physical.generated(*(np.asarray(bank["xz"])+normal*offset))
                     for offset in (-radius, radius)]
        contained &= physical.generated(*point) <= interval["generated_upper_m"]
    return dict(case="computed_hypot_normal_and_coordinate_rounding_are_enclosed",
        matches_expected=bool(contained and witnesses
            and min(witnesses) < route.WATER_LEVEL and _audit(physical, certificate)),
        certificate_returned=certificate is not None,
        independent_scalar_endpoint_heights_m=witnesses,
        computed_normal_norm=float(np.hypot(*normal)))


def _shifted_origin():
    water = route.WATER_LEVEL
    physical = _physical(_field("offset-dry-square", lambda x, z: np.where(
        (x >= -2) & (x <= 4) & (abs(z) <= 3), water+1, water-100)))
    bank = {"xz": [0.0, 0.0]}
    exact = wet_bank_certificate(physical, bank, 4.0)
    uncertain = wet_bank_certificate(physical, bank, 4.0, origin_tolerance_m=1.0)
    witness = _scalar_chord(physical, {"xz": [1.0, 0.0]}, 4.0, math.pi/4)
    return dict(case="nearby_valid_origin_invalidates_bank_certificate",
                matches_expected=bool(exact is not None and uncertain is None
                                      and witness["sampled_chord_dry"]))


def _duplicate_prefix():
    from types import SimpleNamespace
    from crossing_contract import transverse_window
    physical = _physical(_island())
    bank = {"xz": [0.0, 0.0]}
    points = [(0.0, 0.0)]*4+[(2.0, 0.0), (8.0, 0.0)]
    wet = []
    transverse_window(SimpleNamespace(bilinear=physical.generated,
                                     contains=physical.contains),
                      points, 6.0, wet_at=wet)
    proof = wet_bank_certificate(physical, bank, 4.0, origin_tolerance_m=1e-5)
    return dict(case="duplicate_first_tangent_still_has_bank_near_full_width",
                matches_expected=bool(proof is not None
                    and not any(k == 0 for _x, _z, k in wet)
                    and any(k == 3 for _x, _z, k in wet)))


def _remote_unread():
    from crossing_reachability_cases import fixture
    coarse, graph, bank = fixture(n=257)
    native = coarse.fields[0]
    z, x = np.mgrid[-128:129, -128:129]
    # The coarse search graph is only a hint. Fine terrain proves this bank's
    # width impossible, independently of an unread remote optional handoff.
    native.h[:] = np.where((abs(x) <= 1) & (abs(z) <= 1), 34.0, 26.0)
    native.h[240, 240] = np.nan
    physical = _physical(native)
    physical.bind_approach_graphs(graph, graph)
    remote_unread = False
    try:
        physical.generated(112.0, 112.0)
    except CoverageError:
        remote_unread = True
    proofs = [physical.candidate({"id": "constructed-local-necessity"}, bank,
                                 {"xz": [-16.0, 0.0]}, 30.6)
              for _ in range(2)]
    native.h[128, 128] = np.nan
    unknown_local = physical._bank_clearance_certificate(graph, bank) is None
    return dict(case="unread_remote_goal_cannot_rescue_locally_impossible_bank",
                matches_expected=bool(remote_unread and unknown_local
                    and all(p["status"] == "refused" for p in proofs)))


def bank_clearance_cases():
    water = route.WATER_LEVEL
    bank = {"xz": [0.0, 0.0]}
    degenerate = _physical(_island())
    near_water = _physical(_field("wet-inside-roundoff-margin",
        lambda x, z: water-1e-10, dtype=np.float64))
    return [
        _certified_island(),
        _dry_case("dry_square_valid_diagonal_chord", lambda x, z: np.where(
            (abs(x) <= 3) & (abs(z) <= 3), water+4, water-4),
            4.0, math.pi/4),
        _dry_case("dry_infinite_strip_has_legal_tangent", lambda x, z: np.where(
            abs(z) <= 1, water+4, water-4), 4.0, 0.0),
        _dry_case("entirely_dry_bank_stays_unknown", lambda x, z: water+4,
                  4.0, 0.73),
        _bank_changes(), _height_changes(), _hole_changes(), _overlap_changes(),
        _unread_extra_domain(), _invalid_extra_vertex(np.nan),
        _invalid_extra_vertex(np.inf), _disagreeing_extra_vertex(),
        _narrow_between_angles(), _computed_transverse_normal(),
        _shifted_origin(), _duplicate_prefix(),
        _remote_unread(),
        dict(case="degenerate_radius_cannot_certify", matches_expected=bool(
            all(wet_bank_certificate(degenerate, bank, radius) is None
                for radius in (0.0, -1.0, math.nan, math.inf)))),
        dict(case="wet_inside_strict_roundoff_margin_stays_unknown",
            matches_expected=bool(
                wet_bank_certificate(near_water, bank, 4.0) is None
                and near_water.generated(4.0, 0.0) < water)),
    ]
