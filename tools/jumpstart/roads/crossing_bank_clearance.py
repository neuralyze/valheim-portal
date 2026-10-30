"""Optional continuous bank-near mandatory-probe proofs on native Heightmaps.

A returned certificate covers every normal orientation, not a set of sampled
routes or angles. Failure to prove anything (including optional measurement
reads) returns None and leaves the ordinary physical search unchanged.
"""
from __future__ import annotations

import math
from fractions import Fraction

import route


# The adjacent binary64 numbers bracket mathematical pi. Using the upper one
# also covers pi itself, rather than silently omitting its final fraction of an
# ulp. Subdivision endpoints are shared closed boundaries, so rounding a midpoint
# cannot leave an angular gap.
_PI_LO = float.fromhex("0x1.921fb54442d18p+1")
_PI_HI = math.nextafter(_PI_LO, math.inf)
_MAX_GRID_VERTICES = 4096
_MAX_ANGLE_INTERVALS = 4096
_MAX_ANGLE_DEPTH = 24
_MAX_BOUND_POINTS = 131072
# Separate the error in hypot/division's computed normal from trigonometric
# enclosure error. This is deliberately much wider than binary64's per-step
# rounding for a finite, nondegenerate two-component normalized tangent.
_NORMAL_COMPONENT_ERROR = 64*math.ulp(1.0)


def _down(value):
    return math.nextafter(value, -math.inf)


def _up(value):
    return math.nextafter(value, math.inf)


def _add(a, b):
    return _down(a[0] + b[0]), _up(a[1] + b[1])


def _mul(a, b):
    products = (a[0]*b[0], a[0]*b[1], a[1]*b[0], a[1]*b[1])
    return _down(min(products)), _up(max(products))


def _rational_interval(numerator, denominator):
    value = float(Fraction(numerator, denominator))
    return _down(value), _up(value)


# Directed interval polynomial evaluation avoids depending on a libm sin/cos
# accuracy claim. Taylor's theorem bounds the omitted terms uniformly for
# 0 <= theta <= pi_hi < 4; zero odd/even coefficients give the stated orders.
_COS_COEFFICIENTS = tuple(_rational_interval((-1)**k, math.factorial(2*k))
                          for k in range(17))
_SIN_COEFFICIENTS = tuple(_rational_interval((-1)**k, math.factorial(2*k+1))
                          for k in range(17))
_COS_REMAINDER = _rational_interval(4**34, math.factorial(34))[1]
_SIN_REMAINDER = _rational_interval(4**35, math.factorial(35))[1]


def _polynomial(coefficients, square):
    value = coefficients[-1]
    for coefficient in reversed(coefficients[:-1]):
        value = _add(_mul(value, square), coefficient)
    return value


def _trig_at(angle):
    argument = (angle, angle)
    square = _mul(argument, argument)
    cosine = _add(_polynomial(_COS_COEFFICIENTS, square),
                  (-_COS_REMAINDER, _COS_REMAINDER))
    sine = _add(_mul(argument, _polynomial(_SIN_COEFFICIENTS, square)),
                (-_SIN_REMAINDER, _SIN_REMAINDER))
    return ((max(-1.0, cosine[0]), min(1.0, cosine[1])),
            (max(-1.0, sine[0]), min(1.0, sine[1])))


def _trig_ranges(low, high, cache):
    if low not in cache:
        cache[low] = _trig_at(low)
    if high not in cache:
        cache[high] = _trig_at(high)
    (cl, sl), (ch, sh) = cache[low], cache[high]
    # Cosine's only possible interior extremum is its minimum at pi;
    # sine's is its maximum at pi/2. The enclosing pi bounds also handle the
    # deliberately included sliver beyond mathematical pi.
    cosine = (-1.0 if high >= _PI_LO else ch[0], max(cl[1], ch[1]))
    sine = (min(sl[0], sh[0]),
            1.0 if low <= _PI_HI/2 and high >= _PI_LO/2 else max(sl[1], sh[1]))
    return cosine, sine


def _endpoint_box(bank, radius, sign, ranges, padding):
    offset = (sign*radius, sign*radius)
    return tuple(_add(_add((coordinate, coordinate), _mul(offset, direction)),
                      (-padding, padding))
                 for coordinate, direction in zip(bank, ranges))


def _mix(a, b, fraction):
    inverse = _add((1.0, 1.0), (-fraction[1], -fraction[0]))
    return _add(_mul(a, inverse), _mul(b, fraction))


def _fraction(value, origin):
    low, high = _add((value, value), (-origin, -origin))
    # The point belongs to this closed cell. Intersection with [0,1] loses no
    # possible exact weight and keeps zero-weight adjacent cells unnecessary.
    return max(0.0, low), min(1.0, high)


def _height_bounds(vertices, x, z):
    ix, iz = math.floor(x), math.floor(z)
    a = vertices[ix, iz]
    if x == ix and z == iz:
        return a, a
    if x == ix:
        return _mix((a, a), (vertices[ix, iz+1],)*2, _fraction(z, iz))
    b = vertices[ix+1, iz]
    lower = _mix((a, a), (b, b), _fraction(x, ix))
    if z == iz:
        return lower
    upper = _mix((vertices[ix, iz+1],)*2, (vertices[ix+1, iz+1],)*2,
                 _fraction(x, ix))
    return _mix(lower, upper, _fraction(z, iz))


def _cuts(interval):
    low, high = interval
    return [low, *range(math.floor(low)+1, math.ceil(high)), high]


def _box_bounds(vertices, box, work):
    xs, zs = map(_cuts, box)
    work[0] += len(xs)*len(zs)
    if work[0] > _MAX_BOUND_POINTS:
        return None
    low, high = math.inf, -math.inf
    # Splitting at EVERY integer crossing partitions a box into bilinear
    # rectangles. A bilinear function's extrema on each rectangle occur at its
    # corners. These are scalar generated-height bounds, never a raster mask or
    # an ellipse approximation, and include all grid-line intersections.
    for z in zs:
        for x in xs:
            lo, hi = _height_bounds(vertices, x, z)
            low, high = min(low, lo), max(high, hi)
    if not math.isfinite(low) or not math.isfinite(high):
        return None
    return low, high


def wet_bank_certificate(physical, bank, half_width, *, origin_tolerance_m=0.0,
                         mandatory_offsets_m=None):
    """Prove every normal has a wet required probe; None means unknown.

    Retains the wet-only API and endpoint evidence by default. ``half_width``
    is the complete mandatory footprint radius, not half the paved width.
    Optional offsets must be actual mandatory, finite, sign-symmetric lateral
    probes within that radius. The caller must independently bound the first
    nondegenerate station's displacement from ``bank`` by
    ``origin_tolerance_m``. No heights/results are cached.
    """
    try:
        x, z = map(float, bank["xz"])
        radius, water = float(half_width), float(route.WATER_LEVEL)
        origin_tolerance = float(origin_tolerance_m)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (not all(math.isfinite(v) for v in (x, z, radius, water, origin_tolerance))
            or radius <= 0 or origin_tolerance < 0
            or max(abs(x), abs(z), radius, origin_tolerance) >= 2**52-2):
        return None
    if mandatory_offsets_m is None:
        offsets = (radius, -radius)
    else:
        try:
            offsets = tuple(dict.fromkeys(float(v) for v in mandatory_offsets_m))
        except (TypeError, ValueError, OverflowError):
            return None
        if (not offsets or any(not math.isfinite(v) or abs(v) > radius
                               for v in offsets)
                or set(offsets) != {-v for v in offsets}):
            return None

    # A computed normal need not have length exactly one: enclose its component
    # error from hypot normalization/division BEFORE multiplying by the radius.
    # Separately cover radius multiplication and bank-coordinate addition. The
    # directed trig bounds above do not replace either numerical allowance.
    # Inflating outward also at lattice boundaries cannot hide an unread
    # neighbour or narrow dry direction. Effort limits only return unknown.
    normal_padding = _up(radius*_NORMAL_COMPONENT_ERROR)
    coordinate_padding = 128*math.ulp(max(1.0, abs(x), abs(z), radius))
    padding = _up(origin_tolerance
                  + max(1e-10, _up(normal_padding+coordinate_padding)))
    extent = _endpoint_box((x, z), radius, 1, ((-1.0, 1.0), (-1.0, 1.0)),
                           padding)
    if not all(math.isfinite(v) for axis in extent for v in axis):
        return None
    x0, x1 = math.floor(extent[0][0]), math.ceil(extent[0][1])
    z0, z1 = math.floor(extent[1][0]), math.ceil(extent[1][1])
    nx, nz = x1-x0+1, z1-z0+1
    if nx*nz > _MAX_GRID_VERTICES:
        return None

    vertices, rows = {}, []
    try:
        for iz in range(z0, z1+1):
            row = []
            for ix in range(x0, x1+1):
                # vertex validates every overlapping source and finite value.
                # Even unused corners of this optional local snapshot must
                # remain unknown when validation fails, not become a refusal.
                height = float(physical.vertex(ix, iz))
                if not math.isfinite(height):
                    return None
                vertices[ix, iz] = height
                row.append(height)
            rows.append(row)
    except Exception:
        # Only optional measurement acquisition is guarded. In particular a
        # CoverageError here must never abort the ordinary physical search.
        return None

    scale = max(1.0, abs(water), *(abs(v) for v in vertices.values()))
    # Interval evaluation bounds the mathematical bilinear surface. This extra
    # allowance dominates scalar generated()'s four weight/product computations
    # and their ordinary OR compensated sum (CPython 3.12+); it is added to the
    # reported generated upper bound, then required again as a strict wet gap.
    margin = max(1e-9, 256*math.ulp(scale))
    threshold = _down(water-margin)
    intervals, stack, cache, work = [], [(0.0, _PI_HI, 0)], {}, [0]
    examined = 0
    while stack:
        low, high, depth = stack.pop()
        examined += 1
        if examined > _MAX_ANGLE_INTERVALS:
            return None
        ranges = _trig_ranges(low, high, cache)
        bounds = []
        for offset in offsets:
            sign = -1 if offset < 0 else 1
            box = _endpoint_box((x, z), abs(offset), sign, ranges, padding)
            bound = _box_bounds(vertices, box, work)
            if bound is None:
                return None
            bounds.append(bound)
            generated_upper = _up(bound[1]+margin)
            if generated_upper < threshold:
                if mandatory_offsets_m is None:
                    evidence = dict(endpoint_sign=sign,
                        endpoint_box_xz=[list(axis) for axis in box],
                        bilinear_upper_m=bound[1], generated_upper_m=generated_upper)
                else:
                    evidence = dict(lateral_offset_m=offset,
                        probe_role=("endpoint" if abs(offset) == radius else
                                    "center" if offset == 0 else "interior"),
                        probe_box_xz=[list(axis) for axis in box],
                        bilinear_upper_m=bound[1], generated_upper_m=generated_upper)
                intervals.append(dict(angle_rad=[low, high], **evidence))
                break
        else:
            if all(bound[0] >= water for bound in bounds):
                return None
            midpoint = (low+high)/2
            if depth >= _MAX_ANGLE_DEPTH or not low < midpoint < high:
                return None
            stack.append((midpoint, high, depth+1))
            stack.append((low, midpoint, depth+1))

    certificate = dict(
        bank_xz=[x, z], half_width_m=radius, water_level_m=water,
        angle_domain_rad=[0.0, _PI_HI], normal_orientation_mod_pi=True,
        coordinate_inflation_m=padding, strict_wet_margin_m=margin,
        station_origin_tolerance_m=origin_tolerance,
        normalized_direction_component_error=_NORMAL_COMPONENT_ERROR,
        normalization_inflation_m=normal_padding,
        coordinate_arithmetic_inflation_m=coordinate_padding,
        generated_arithmetic_allowance_m=margin,
        wet_upper_threshold_m=threshold, covered_angle_intervals=intervals,
        coverage_method=("closed interval subdivision; outward-rounded Taylor "
            "sin/cos boxes; piecewise-bilinear rectangle extrema at box corners, "
            "integer grid crossings and intersections; validated native vertices"),
        native_grid=dict(x0=x0, z0=z0, nx=nx, nz=nz, heights_zx=rows,
                         vertex_reads=nx*nz),
        angle_intervals_examined=examined, bound_points_evaluated=work[0])
    if mandatory_offsets_m is None:
        certificate["kind"] = "all_orientations_wet_bank_near_station_endpoint"
    else:
        certificate.update(kind="all_orientations_wet_bank_near_station_probe",
                           mandatory_offsets_m=list(offsets))
    return certificate
