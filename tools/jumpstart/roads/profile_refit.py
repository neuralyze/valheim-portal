"""Read-only proposals inside a segment's complete native profile contract.

This is not an earthwork/write authorization: accepted proposals must still pass
all of the writer's stamp, codec, ownership, water, arrival and rim judges.
"""
from __future__ import annotations

import copy
import math

import numpy as np

import profile_contract as contract
import route


def _readings(model: dict, claims: list[dict]):
    """Append exact station-foot readings, without station-envelope padding."""
    from scipy.sparse import vstack

    matrix, lower, upper = contract.native_profile_readings(model)
    arcs = model["inputs"]["s"]
    columns, weights, lows, highs = [], [], [], []
    for claim in claims:
        s, h, slack = (float(claim[k]) for k in ("s", "h", "slack"))
        if (not all(math.isfinite(v) for v in (s, h, slack)) or slack < 0
                or s < arcs[0] or s > arcs[-1]):
            raise contract.NativeProfileModelError(
                "measured profile claim is outside the exact station geometry")
        k0, k1, t = route._feet(arcs, np.asarray([s]))
        columns.append([int(k0[0]), int(k1[0])])
        weights.append([float(1. - t[0]), float(t[0])])
        lows.append(h - slack)
        highs.append(h + slack)
    if columns:
        matrix = vstack((matrix, route._reading_matrix(
            columns, weights, len(arcs))), format="csr")
        lower = np.concatenate((lower, lows))
        upper = np.concatenate((upper, highs))
    return matrix, np.asarray(lower), np.asarray(upper)


def _validate(seg: dict, model: dict, profile, claims: list[dict]) -> None:
    """The native inequalities and the actual emitted self-lattice, unchanged."""
    y = np.asarray(profile, dtype=np.float64)
    inputs = model["inputs"]
    arcs = inputs["s"]
    matrix, lower, upper = _readings(model, claims)
    values = matrix @ y
    if (y.shape != np.asarray(inputs["lo"]).shape or not np.all(np.isfinite(y))
            or np.any(y < inputs["lo"] - 1e-9)
            or np.any(y > inputs["hi"] + 1e-9)
            or np.any(np.abs(np.diff(y))
                      > float(inputs["rung"]) * np.diff(arcs) + 1e-9)
            or np.any(values < lower - 1e-9)
            or np.any(values > upper + 1e-9)):
        raise contract.NativeProfileModelError(
            "total profile violates original native station/grade/reading constraints")
    # The shared contract reader already proved the complete emitted lattice.
    # A proposal changes only its heights, not that verified inventory.
    if route.self_adjacency_violations(y, model["self_lattice"]):
        raise contract.NativeProfileModelError(
            "total profile violates the actual emitted native self-lattice")


def require_native_profile(seg: dict) -> dict:
    """Refuse missing/stale/incomplete context; never infer it from profile_y."""
    model = contract.read_native_profile_model(seg)
    _validate(seg, model, seg["profile_y"], [])
    return model


def record_native_profile_refusal(seg: dict, proposal: dict) -> None:
    """Do not leave an old CLEAR certificate on missing/mismatched context."""
    if proposal.get("refusal_kind") != "NATIVE_PROFILE_CONTEXT_REFUSED":
        return
    why = proposal["why"]
    seg["native_profile_status"] = {"verdict": "UNPROVEN", "why": why}
    seg["profile_constraints"] = {"verdict": "REFUSED", "why": why,
                                  "bindings": [], "complete_native_model": False}
    seg["self_adjacency"] = {"verdict": "UNPROVEN", "why": why}


def accept_native_profile(seg: dict, proposal: dict) -> None:
    """Publish a legal proposal and refresh current native verdict metadata."""
    if not proposal["feasible"]:
        raise contract.NativeProfileModelError("cannot accept a refused native proposal")
    candidate = {**seg, "profile_y": proposal["profile"],
                 "native_profile_model": proposal["native_profile_model"]}
    model = require_native_profile(candidate)
    inputs = model["inputs"]
    moved = np.abs(np.asarray(candidate["profile_y"]) - seg["profile_y"])
    grades = np.abs(np.diff(candidate["profile_y"])) / np.diff(inputs["s"])
    seg.update(
        profile_y=list(candidate["profile_y"]),
        native_profile_model=copy.deepcopy(candidate["native_profile_model"]),
        native_profile_status={"verdict": "COMPLETE", "version": contract.VERSION},
        grade_max_measured=round(float(grades.max()), 4),
        grade_p95=round(float(np.percentile(grades, 95)), 4),
        grade_mean=round(float(grades.mean()), 4),
        profile_constraints={
            "verdict": "CLEAR", "held_stations": int(np.count_nonzero(inputs["held"])),
            "bindings": [], "complete_native_model": True,
            "tool": "roads/profile_refit.py::require_native_profile"},
        self_adjacency={
            "verdict": "CLEAR", "pairs_after": 0, "worst_after": None,
            "candidate_edges": len(model["self_lattice"]["edges"]["s1"]),
            "rung": float(inputs["rung"]),
            "design_rung": float(inputs["design_rung"]),
            "stations_moved": int(np.count_nonzero(moved > 1e-9)),
            "max_move_m": float(moved.max(initial=0.0)),
            "tool": "roads/profile_refit.py::require_native_profile"})


def refit_native_profile(seg: dict, claims: list[dict]) -> dict:
    """Minimum-displacement TOTAL profile, at the original native rung.

    `unpin` on a legacy claim grants no authority to release a held station,
    a bank/arrival bound, or a published shared reading. Infeasibility means
    only that this exact fixed-geometry remedy is unavailable.
    """
    profile = [float(v) for v in seg["profile_y"]]
    base = {"feasible": False, "profile": profile, "nodes_regraded": 0,
            "max_move_m": 0.0, "claims_active": 0}
    try:
        model = require_native_profile(seg)
        matrix, lower, upper = _readings(model, claims)
        inputs = model["inputs"]
        arcs = inputs["s"]
        for claim in claims:
            value = float(np.interp(float(claim["s"]), arcs, profile))
            base["claims_active"] += int(
                abs(value - float(claim["h"])) > float(claim["slack"]) + 1e-9)
        if not base["claims_active"]:
            return {**base, "feasible": True,
                    "native_profile_model": copy.deepcopy(seg["native_profile_model"]),
                    "why": "every measured claim already holds; no profile change"}
        try:
            proposal = route._exact_reading_profile(
                profile, inputs["lo"], inputs["hi"],
                [(0, len(arcs), float(inputs["rung"]) * arcs)],
                matrix, lower, upper)
        except ValueError as exc:
            return {**base, "why": "native exact profile solver failed: " + str(exc)
                    + "; no physical infeasibility has been proved",
                    "refusal_kind": "NATIVE_PROFILE_SOLVER_FAILED"}
        if proposal is None:
            return {**base, "why": "measured claims are infeasible with this complete native profile model; replan the alignment or remedy bounds",
                    "refusal_kind": "NATIVE_PROFILE_CLAIMS_INFEASIBLE"}
        _validate(seg, model, proposal, claims)
        candidate = copy.deepcopy(seg)
        candidate["profile_y"] = proposal.tolist()
        contract.bind_native_profile_model(candidate, model)
        # Re-read the newly bound JSON, not a private unconstrained solver array.
        require_native_profile(candidate)
    except (contract.NativeProfileModelError, ValueError, KeyError, TypeError) as exc:
        return {**base, "why": "native profile repair refused: " + str(exc)
                + "; replan with a complete geometry-bound native_profile_model",
                "refusal_kind": "NATIVE_PROFILE_CONTEXT_REFUSED"}
    moves = np.abs(proposal - np.asarray(profile))
    return {"feasible": True, "profile": candidate["profile_y"],
            "native_profile_model": candidate["native_profile_model"],
            "nodes_regraded": int(np.count_nonzero(moves > 1e-9)),
            "max_move_m": float(moves.max(initial=0.0)),
            "claims_active": base["claims_active"],
            "why": "total profile satisfies complete native constraints and measured claims"}
