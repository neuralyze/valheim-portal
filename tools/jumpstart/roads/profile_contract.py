"""Lossless native solver inputs for fixed-geometry total-profile feedback.

This is a constraint transport, not write authorization: the writer still owns
all physical stamp, water, clamp, arrival, body and rim acceptance gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

import numpy as np

SCHEMA = "native_profile_model"
VERSION = 1
BOUND_ENCODING = "null_lower_is_negative_infinity_null_upper_is_positive_infinity"


class NativeProfileModelError(ValueError):
    """A profile cannot be modified without a new complete native solve."""


def _fail(reason):
    raise NativeProfileModelError(
        f"{reason}; replan this exact physical segment with complete native "
        "profile inputs before profile modification or authoritative write")


def _plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        _fail(f"native profile model is not finite JSON: {exc}")
    return hashlib.sha256(raw).hexdigest()


def _binding(seg):
    keys = ("id", "cls", "width_m", "nodes", "profile_y", "is_bridge",
            "terminal_caps", "terminal_owners", "grade_limit")
    if any(k not in seg for k in keys):
        _fail("physical segment lacks exact native binding fields")
    return _plain({k: seg[k] for k in keys})


def _encode_bounds(model):
    """JSON null is unbounded only in these two explicitly inventoried arrays."""
    model["bound_encoding"] = BOUND_ENCODING
    for key, direction, inventory in (
            ("lo", -math.inf, "unbounded_lower"),
            ("hi", math.inf, "unbounded_upper")):
        values = model["inputs"][key]
        unbounded = []
        encoded = []
        for k, value in enumerate(values):
            if value is None or float(value) == direction:
                unbounded.append(k)
                encoded.append(None)
            elif not math.isfinite(float(value)):
                _fail(f"native {key} contains NaN or wrong-sign infinity")
            else:
                encoded.append(float(value))
        model["inputs"][key] = encoded
        model["inputs"][inventory] = unbounded
    return model


def _normalise(raw):
    model = copy.deepcopy(raw)
    if (model.get("schema") != SCHEMA or type(model.get("version")) is not int
            or model["version"] != VERSION):
        _fail("missing or unsupported complete native_profile_model")
    if model.get("bound_encoding") != BOUND_ENCODING:
        _fail("native profile bound encoding is missing or unsupported")
    for key in ("binding", "inputs", "self_lattice", "readings", "physical_basis"):
        if key not in model:
            _fail(f"native profile model lacks {key}")
    binding = model["binding"]
    try:
        nodes = np.asarray(binding["nodes"], dtype=np.float64)
        n = len(nodes)
        if n < 2 or nodes.shape != (n, 2) or not np.isfinite(nodes).all():
            _fail("native profile geometry is not a finite station chain")
        width = float(binding["width_m"])
        if not math.isfinite(width) or width <= 0 or not binding["cls"]:
            _fail("native profile width/class is invalid")
        inputs = model["inputs"]
        for key in ("s", "t", "tw_lo", "tw_hi"):
            a = np.asarray(inputs[key], dtype=np.float64)
            if a.shape != (n,) or not np.isfinite(a).all():
                _fail(f"native profile {key} is not a finite full inventory")
            inputs[key] = a
        for key, direction, inventory in (
                ("lo", -math.inf, "unbounded_lower"),
                ("hi", math.inf, "unbounded_upper")):
            values = inputs[key]
            if [k for k, value in enumerate(values) if value is None] != inputs[inventory]:
                _fail(f"native {key} unbounded station inventory differs")
            a = np.asarray([direction if value is None else value for value in values],
                           dtype=np.float64)
            if (a.shape != (n,) or np.isnan(a).any()
                    or np.any(np.isinf(a) & (a != direction))):
                _fail(f"native profile {key} has invalid bounds")
            inputs[key] = a
        for key in ("free", "held"):
            a = np.asarray(inputs[key])
            if a.shape != (n,) or a.dtype.kind != "b":
                _fail(f"native profile {key} is not a full boolean inventory")
            inputs[key] = a
        if not np.array_equal(inputs["free"], binding["is_bridge"]):
            _fail("native free stations differ from emitted bridge ownership")
        s = inputs["s"]
        if s[0] != 0 or np.any(np.diff(s) <= 0):
            _fail("native station arcs are not increasing from zero")
        # Use the writer's exact arc law, the same one used for self feet.
        import route
        arcs = np.asarray(route._writer().node_arcs(nodes, inputs["free"]))
        if not np.array_equal(arcs, model["physical_basis"]["geometry_arcs"]):
            _fail("native physical basis differs from emitted geometry")
        if not np.array_equal(s, arcs):
            _fail("native chain distances differ from emitted geometry")
        rung = float(inputs["rung"])
        ladder = np.asarray(inputs["ladder"], dtype=np.float64)
        if (ladder.ndim != 1 or not len(ladder) or not np.isfinite(ladder).all()
                or np.any(ladder <= 0) or rung not in ladder
                or rung != float(binding["grade_limit"])):
            _fail("native grade/rung inventory differs from emitted profile")
        if not math.isfinite(float(inputs["design_rung"])):
            _fail("native design rung is missing")
        if inputs["terminal_owners"] != binding["terminal_owners"]:
            _fail("native terminal ownership differs from emitted ownership")
        for first, last in inputs["spans"]:
            if not 0 <= first <= last < n:
                _fail("native residual span inventory is invalid")
        for arrival in inputs["arrival_readings"]:
            if (arrival["station"] not in (0, n - 1)
                    or [arrival["x"], arrival["z"]] != binding["nodes"][arrival["station"]]
                    or not all(math.isfinite(float(arrival[k]))
                               for k in ("datum", "allow", "x", "z"))
                    or arrival["allow"] < 0):
                _fail("native arrival owner reading is invalid")
        for key in ("held_abutment_inputs", "held_detected_bank_inputs",
                    "free_crossing_windows", "geometry_preparation",
                    "walked_water_ceiling_inputs", "measurement_field", "producer"):
            if key not in model["physical_basis"]:
                _fail(f"native physical basis lacks original {key}")
        basis = model["physical_basis"]
        expected_free = np.zeros(n, dtype=bool)
        expected_held = np.zeros(n, dtype=bool)
        for window in basis["free_crossing_windows"]:
            first, last = window["first_free"], window["last_free"]
            if not 0 <= first <= last < n or not window["crossing_id"]:
                _fail("native free crossing owner inventory is invalid")
            expected_free[first:last + 1] = True
        for *_owner, first, last in basis["held_abutment_inputs"]:
            if not 0 <= first <= last < n:
                _fail("native abutment owner inventory is invalid")
            expected_held[first:last + 1] = True
        for window in basis["held_detected_bank_inputs"]:
            first, last = window["first"], window["last"]
            if not 0 <= first <= last < n or not window["crossing_id"]:
                _fail("native detected-bank owner inventory is invalid")
            expected_held[first:last + 1] = True
        if (not np.array_equal(expected_free, inputs["free"])
                or not np.array_equal(expected_held, inputs["held"])):
            _fail("native free/held stations lack matching physical owner inputs")
        for key in ("water_level", "road_freeboard_m", "deck_clearance_m",
                    "cut_fill_max_m", "clamp_m", "self_join_margin_m"):
            if not math.isfinite(float(model["physical_basis"][key])):
                _fail(f"native physical basis lacks {key}")
        cand = model["self_lattice"]
        for key in ("grade", "lattice_m", "batter_m", "dy_cap", "bench_half_m"):
            if not math.isfinite(float(cand[key])) or float(cand[key]) <= 0:
                _fail(f"native self lattice lacks its original {key}")
        for key in ("arcs", "pts", "near1", "near2", "dsum", "allow_e"):
            cand[key] = np.asarray(cand[key], dtype=(np.int64 if key in
                                   ("near1", "near2") else np.float64))
        if not np.array_equal(cand["pts"], nodes):
            _fail("native self lattice belongs to a different geometry")
        if tuple(cand["terminal_caps"]) != tuple(binding["terminal_caps"]):
            _fail("native self lattice has different terminal ownership")
        if cand["bench_half_m"] != route.footprint_half_m(width):
            _fail("native self lattice has different physical width")
        if cand["dy_cap"] < rung:
            _fail("native self lattice does not cover the declared grade")
        edges = cand["edges"]
        for key in ("s1", "s2", "run1", "run2", "x1", "z1", "x2", "z2"):
            edges[key] = np.asarray(edges[key], dtype=np.float64)
        m = len(edges["s1"])
        if any(a.shape != (m,) or not np.isfinite(a).all()
               for a in edges.values()):
            _fail("native self lattice edge inventory is incomplete")
        if any(cand[k].shape != (m,) for k in
               ("near1", "near2", "dsum", "allow_e")):
            _fail("native self lattice restrictions are incomplete")
        if cand["arcs"].shape != (n,) or not np.array_equal(cand["arcs"], arcs):
            _fail("native self lattice arc differs from emitted geometry")
        for row in model["readings"]:
            columns, weights = row["columns"], row["weights"]
            if (not columns or len(columns) != len(weights)
                    or any(isinstance(k, bool) or not isinstance(k, int)
                           or not 0 <= k < n for k in columns)
                    or not all(math.isfinite(float(v)) for v in weights)
                    or not math.isfinite(float(row["lower_y"]))
                    or not math.isfinite(float(row["upper_y"]))
                    or row["lower_y"] > row["upper_y"]
                    or not row["kind"] or "owner" not in row):
                _fail("native interpolated reading is invalid")
        model["cand"] = cand
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, NativeProfileModelError):
            raise
        _fail(f"native profile input inventory is incomplete: {exc}")
    return model


def native_profile_readings(model):
    """Return the native exact self/owner sparse rows and original limits."""
    import route
    from scipy.sparse import csr_matrix, vstack

    cand = model["self_lattice"]
    edges = cand["edges"]
    a0, a1, ta = route._feet(cand["arcs"], edges["s1"])
    b0, b1, tb = route._feet(cand["arcs"], edges["s2"])
    n = len(model["inputs"]["s"])
    matrix = route._reading_matrix(
        np.column_stack((a0, a1, b0, b1)),
        np.column_stack((1. - ta, ta, -(1. - tb), -tb)), n)
    allowance = (np.asarray(cand["allow_e"])
                 - model["physical_basis"]["self_join_margin_m"])
    lower, upper = -allowance, allowance
    rows = model["readings"]
    if rows:
        ii, jj, values = [], [], []
        for i, row in enumerate(rows):
            ii.extend([i] * len(row["columns"]))
            jj.extend(row["columns"])
            values.extend(row["weights"])
        extra = csr_matrix((values, (ii, jj)), shape=(len(rows), n))
        matrix = vstack((matrix, extra), format="csr")
        lower = np.r_[lower, [row["lower_y"] for row in rows]]
        upper = np.r_[upper, [row["upper_y"] for row in rows]]
    return matrix, lower, upper


def _check_profile(model):
    import route
    y = np.asarray(model["binding"]["profile_y"], dtype=np.float64)
    inputs = model["inputs"]
    if y.shape != inputs["s"].shape or not np.isfinite(y).all():
        _fail("emitted native profile is incomplete")
    if (np.any(y < inputs["lo"] - 1e-9)
            or np.any(y > inputs["hi"] + 1e-9)
            or np.any(np.abs(np.diff(y)) > inputs["rung"]
                      * np.diff(inputs["s"]) + 1e-9)):
        _fail("emitted total profile violates original native bounds/grade")
    matrix, lower, upper = native_profile_readings(model)
    values = matrix @ y
    if (np.any(values < lower - 1e-9) or np.any(values > upper + 1e-9)
            or route.self_adjacency_violations(y, model["self_lattice"])):
        _fail("emitted total profile violates original native self/owner readings")


def build_native_profile_model(seg, joint, *, published_readings=()):
    """Capture actual FINAL native solver inputs, never infer missing inputs."""
    needed = ("s", "t", "lo", "hi", "tw_lo", "tw_hi", "free", "held",
              "ladder", "design_rung", "cand", "terminal_owners", "native_basis", "spans")
    if any(k not in joint for k in needed):
        _fail("native solve did not export its complete physical input basis")
    inputs = {k: joint[k] for k in needed if k not in ("cand", "native_basis")}
    inputs["rung"] = seg["grade_limit"]
    inputs["arrival_readings"] = joint.get("arrival_readings", [])
    readings = list(published_readings)
    # Free bridge intervals describe design freedom BEFORE a deck is
    # published, not permission for single-road earthwork feedback to move
    # that structure afterwards. Keep the original bounds AND its agreed
    # emitted reading. Held ground-bank owners receive the same explicit lock.
    for k in np.flatnonzero(np.asarray(joint["free"]) | np.asarray(joint["held"])):
        k = int(k)
        datum = float(seg["profile_y"][k])
        readings.append({
            "columns": [k], "weights": [1.], "lower_y": datum, "upper_y": datum,
            "kind": "published_deck_datum" if joint["free"][k]
                    else "published_owned_station",
            "owner": seg["id"], "source": {
                "original_bounds_station": k,
                "owner_basis": "physical_basis.held_abutment_inputs/"
                               "held_detected_bank_inputs/free_crossing_windows"}})
    # Even before a joint solve, retain explicit native arrival restrictions.
    import route
    for arrival in inputs["arrival_readings"]:
        allowance = max(float(arrival["allow"]) - route.JUNCTION_DATUM_MARGIN_M, 0.)
        datum = float(arrival["datum"])
        readings.append({"columns": [int(arrival["station"])], "weights": [1.],
                         "lower_y": datum - allowance, "upper_y": datum + allowance,
                         "kind": "arrival", "owner": arrival["pad"],
                         "source": _plain(arrival)})
    model = _plain({"schema": SCHEMA, "version": VERSION,
                    "binding": _binding(seg), "inputs": inputs,
                    "physical_basis": joint["native_basis"],
                    "self_lattice": joint["cand"], "readings": readings})
    model = _encode_bounds(model)
    _check_profile(_normalise(model))
    model["sha256"] = _digest(model)
    return model


def refresh_native_profile_model(seg, joint, *, published_readings=()):
    """Replace every old variant; incomplete/native-infeasible remains unproven."""
    seg.pop("native_profile_model", None)
    try:
        model = build_native_profile_model(seg, joint,
                                           published_readings=published_readings)
    except NativeProfileModelError as exc:
        seg["native_profile_status"] = {"verdict": "UNPROVEN", "why": str(exc)}
        return None
    seg["native_profile_model"] = model
    seg["native_profile_status"] = {"verdict": "COMPLETE", "version": VERSION}
    return model


def read_native_profile_model(seg):
    """Require full exact physical binding and all original native constraints."""
    raw = seg.get("native_profile_model")
    if not isinstance(raw, dict):
        _fail("segment has no complete native_profile_model")
    content = {k: v for k, v in raw.items() if k != "sha256"}
    if raw.get("sha256") != _digest(content):
        _fail("native profile model integrity mismatch")
    if content.get("binding") != _binding(seg):
        _fail("native profile model geometry/profile/ownership binding mismatch")
    model = _normalise(content)
    # Validate the complete inventory at the common read boundary, including
    # planner/shore consumers, not only when a terrain writer later refits it.
    import route
    cand = model["self_lattice"]
    actual = route.self_adjacency_lattice(
        np.asarray(seg["nodes"], dtype=np.float64),
        np.asarray(seg["is_bridge"], dtype=bool), float(seg["width_m"]),
        grade=float(cand["grade"]), batter_m=float(cand["batter_m"]),
        lattice_m=float(cand["lattice_m"]), dy_cap=float(cand["dy_cap"]),
        terminal_caps=tuple(seg["terminal_caps"]))
    if (any(not np.array_equal(cand[key], actual[key]) for key in
            ("arcs", "pts", "near1", "near2", "dsum", "allow_e"))
            or set(cand["edges"]) != set(actual["edges"])
            or any(not np.array_equal(cand["edges"][key], actual["edges"][key])
                   for key in actual["edges"])):
        _fail("complete native self-lattice inventory differs from emitted geometry")
    _check_profile(model)
    return model


def bind_native_profile_model(seg, model):
    """Rebind only a legal total profile; no geometry or constraints may move."""
    raw = _plain({k: v for k, v in model.items() if k not in ("cand", "sha256")})
    before, after = raw["binding"], _binding(seg)
    raw = _encode_bounds(raw)
    original = seg.get("native_profile_model")
    if not isinstance(original, dict):
        _fail("profile feedback lost its original complete native model")
    content = {k: v for k, v in original.items() if k != "sha256"}
    if original.get("sha256") != _digest(content):
        _fail("original native profile model integrity mismatch")
    if (_digest({k: v for k, v in content.items() if k != "binding"})
            != _digest({k: v for k, v in raw.items() if k != "binding"})):
        _fail("profile-only feedback changed original native constraints")
    if any(before[k] != after[k] for k in before if k != "profile_y"):
        _fail("profile-only feedback changed native geometry or ownership")
    raw["binding"] = after
    _check_profile(_normalise(raw))
    raw["sha256"] = _digest(raw)
    seg["native_profile_model"] = raw
    seg["native_profile_status"] = {"verdict": "COMPLETE", "version": VERSION}
    return raw
