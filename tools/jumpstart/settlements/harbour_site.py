#!/usr/bin/env python3
"""SITING A HUMAN-DECLARED HARBOUR: the request is the owner's, the node is
MEASURED.

`worldseed.HUMAN_SITES` holds the two sites a person chose by looking at the
map -- Ulfsgard, the walled harbour city "near (1050, 720)", and Sudrvik, the
harbour village "near (1190, 444)".  A coordinate read off a map is a REQUEST:
the owner was right about the harbour and the number is a harbour's, not a
building's.  MEASURED on Pirate68's own 1 m field, the sea (the water component
that reaches the window edge, i.e. navigable water and not a pond) stands 3.19 m
over (1050, 720) and 3.80 m over (1190, 444).  Nothing can be built, and no road
can end, on either.

So this module does what the owner's eye did and the number could not: it finds
the inlet and the LANDING -- the dry ground a road can end on at that inlet --
and that landing is the road network's node.  It is called by
`settlements/plan.py` at stage 8, with the same field, spawn and keep-outs the
solver sites are judged against, and its record is published in `sites.yaml`
(`declared_sites`) and `plan.json` for `roads/spec.py` (nodes, legs) and
`buildstage.py` (the build unit).

THE LANDING IS A RULE, NOT A COORDINATE, and every term of it is a measurement:

  * DRY WITH FREEBOARD   a disc of `landing_r_m` that stands `FREEBOARD_M` above
                         the water plane at EVERY sample.  A single dry cell is
                         a spit; a disc is ground a road can end on.
  * ON THE ROADS' LAND   the landmass the spawn stands on (`T.land_components`).
                         A landing on an islet is a node no road can reach.
  * ON THE SHORE         within `shore_max_m` of the sea, so the road arrives at
                         the harbour and not at some meadow near it.
  * WALKABLE             mean slope under the disc no steeper than the network's
                         25 % grade ceiling (`roads/spec.py` grade policy).
  * CLEAR                of every generator location and recorded installation,
                         asked of the caller's `clear` -- the solver's own test.

The nearest such cell to the request wins; a request with no such cell inside
`search_m`, or farther than `inlet_max_m` from the sea at all, is REFUSED with
the numbers.  Nothing is moved quietly and nothing is forced.

`district_m` is the radius of ground the settlement reserves round the request
(authored design, like `OUTLIERS`' `pad_m`: a city is bigger than a village).
`plan.py` claims it first, so no solver site is planted inside it.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import terrain1m as T  # noqa: E402

# A dry sample must stand this far above the water plane to count as land: the
# same freeboard `harbour_city.PAD_FREEBOARD_M` seats a building on.
FREEBOARD_M = 0.8
# The network's grade ceiling (roads/spec.py grade policy, 25 %).  A landing
# whose mean slope is steeper cannot be reached by any road of the network.
LANDING_SLOPE_MAX = 0.25

# AUTHORED DESIGN PER KIND (what to build); nothing here is a place.
KINDS: dict[str, dict] = {
    "harbour_city": dict(
        landing_r_m=12.0, shore_max_m=60.0, search_m=250.0, inlet_max_m=150.0,
        district_m=160.0, road_priority="trunk"),
    "harbour_village": dict(
        landing_r_m=10.0, shore_max_m=60.0, search_m=200.0, inlet_max_m=150.0,
        district_m=90.0, road_priority="trunk"),
}

# WHO BUILDS EACH KIND: `(module under settlements/, argv tail)`.  The step that
# builds a declared site (`buildstage.py` step 5, unit = the site id) appends
# `--actor`, `--report` and, for its dry form, `--dry`; the module owns the
# design and the writes, exactly as `settlements/build.py` owns a town's.
BUILDERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "harbour_city": ("harbour_city.py", ("ubuild",)),
    "harbour_village": ("harbour_b.py", ("build",)),
}

# THE NEUTERS (selftest): each restores the defect its term exists to stop.
_LANDMASS_MODE = "spawn"      # spawn | any    (any: the landing may be an islet)
_SLOPE_MODE = "walkable"      # walkable | any (any: a cliff face is a landing)
_DISC_MODE = "disc"           # disc | cell    (cell: one dry sample is enough)


def _sea(f: T.Field) -> tuple[np.ndarray, int]:
    """`(sea mask, number of sea components)`: the wet components that reach
    the window edge, i.e. navigable water.  Refuses a window with none -- a
    harbour is a statement about the sea, and an inland lake is not it."""
    from scipy import ndimage as ndi
    lab, cnt = ndi.label(f.h <= T.WATER_LEVEL_M)
    edge = set(np.unique(np.concatenate(
        [lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))) - {0}
    if not edge:
        raise ValueError(
            f"window {f.id} holds {cnt} wet component(s) and none reaches its "
            f"edge, so it holds no navigable water; a harbour needs the sea")
    return np.isin(lab, list(edge)), len(edge)


def _disc(r_cells: int) -> np.ndarray:
    yy, xx = np.mgrid[-r_cells:r_cells + 1, -r_cells:r_cells + 1]
    return (yy ** 2 + xx ** 2) <= r_cells * r_cells


def measure(fields: dict[str, T.Field], spawn_xz, site_id: str, rec: dict,
            clear=None) -> dict:
    """The measured record of one declared harbour (`rec` is its
    `worldseed.HUMAN_SITES` entry).  `clear(x, z, half_diag_m) -> bool` is the
    caller's keep-out test (generator locations, recorded installations); None
    means nothing else claims ground here."""
    from scipy import ndimage as ndi
    kind = rec["kind"]
    if kind not in KINDS:
        raise ValueError(f"{site_id}: no siting rule for kind {kind!r}; "
                         f"this module sites {sorted(KINDS)}")
    P = KINDS[kind]
    rx, rz = float(rec["xz"][0]), float(rec["xz"][1])
    f = T.field_for(fields, rx, rz, pad_m=P["shore_max_m"])
    if f is None:
        raise ValueError(
            f"{site_id} is requested at ({rx:g}, {rz:g}) and no scanned window "
            f"holds it with {P['shore_max_m']:g} m (the shore band) to spare "
            f"({sorted(fields)}); "
            f"its ground has never been measured. Scan a window that covers "
            f"it (roads/field.py request) or re-declare the site.")
    sea, n_sea = _sea(f)
    iz, ix = f.idx(rx, rz)
    ground = float(f.h[iz, ix])
    ds = ndi.distance_transform_edt(~sea, sampling=f.step).astype(np.float32)
    inlet_m = float(ds[iz, ix])
    if inlet_m > P["inlet_max_m"]:
        raise ValueError(
            f"{site_id} is requested at ({rx:g}, {rz:g}), ground {ground:.2f} m, "
            f"and the nearest navigable water is {inlet_m:.1f} m away "
            f"(limit {P['inlet_max_m']:g} m for a {kind}); there is no harbour "
            f"there to site. Re-declare the request on the map's real inlet.")

    # --- candidates: dry-with-freeboard discs, on the roads' land, on the shore
    r = int(math.ceil(P["landing_r_m"] / f.step)) if _DISC_MODE == "disc" else 0
    dry = f.h > T.WATER_LEVEL_M + FREEBOARD_M
    ok = ndi.binary_erosion(dry, structure=_disc(r), border_value=0) \
        if r else dry.copy()
    land, _ = T.land_components(f, min_area_m2=1.0)
    spawn_label = None
    if f.contains(spawn_xz[0], spawn_xz[1]):
        sz, sx = f.idx(spawn_xz[0], spawn_xz[1])
        spawn_label = int(land[sz, sx]) or None
    near = _window_mask(f, rx, rz, P["search_m"])
    if spawn_label is None:
        # No spawn in this window: the roads' land is the biggest piece of dry
        # ground the search reaches, and the record says so.
        sizes = np.bincount(land[near & (land > 0)].ravel())
        spawn_label = int(np.argmax(sizes)) if sizes.size > 1 else None
    if _LANDMASS_MODE == "spawn":
        ok &= (land == spawn_label)
    gz, gx = np.gradient(f.h, f.step)
    mean_slope = ndi.uniform_filter(np.hypot(gz, gx), size=2 * r + 1)
    if _SLOPE_MODE == "walkable":
        ok &= mean_slope <= LANDING_SLOPE_MAX
    ok &= (ds <= P["shore_max_m"]) & near
    zz, xx = np.mgrid[0:f.n, 0:f.n]
    X = f.x0 + xx * f.step
    Z = f.z0 + zz * f.step
    dist = np.hypot(X - rx, Z - rz)
    order = np.argsort(np.where(ok, dist, np.inf), axis=None)
    n_ok = int(ok.sum())
    refused = 0
    chosen = None
    for flat in order[:min(n_ok, 20000)]:
        cz, cx = divmod(int(flat), f.n)
        px, pz = float(X[cz, cx]), float(Z[cz, cx])
        if clear is not None and not clear(px, pz, P["landing_r_m"]):
            refused += 1
            continue
        chosen = (cz, cx, px, pz)
        break
    if chosen is None:
        raise ValueError(
            f"{site_id} is requested at ({rx:g}, {rz:g}) and has no landing "
            f"inside {P['search_m']:g} m: {n_ok} sample(s) are a "
            f"{P['landing_r_m']:g} m disc standing {FREEBOARD_M:g} m above the "
            f"water on the spawn's land within {P['shore_max_m']:g} m of the "
            f"sea at a mean slope of at most {LANDING_SLOPE_MAX:g}, and "
            f"{refused} of those were refused by a generator location or an "
            f"installation. Re-declare the request, or widen "
            f"settlements/harbour_site.py::KINDS[{kind!r}] -- its terms are "
            f"arguments, not constants of the world.")
    cz, cx, px, pz = chosen
    gap = float(dist[cz, cx])
    window = f.h[max(cz - r, 0):cz + r + 1, max(cx - r, 0):cx + r + 1]
    relief = float(window.max() - window.min())
    deepest = float(T.WATER_LEVEL_M - f.h[iz, ix]) if ground < T.WATER_LEVEL_M \
        else 0.0
    measured = {
        "request_ground_y": round(ground, 2),
        "request_water_depth_m": round(max(deepest, 0.0), 2),
        "request_to_sea_m": round(inlet_m, 1),
        "sea_components_in_window": n_sea,
        "landing_gap_m": round(gap, 1),
        "landing_ground_y": round(float(f.h[cz, cx]), 2),
        "landing_to_sea_m": round(float(ds[cz, cx]), 1),
        "landing_relief_m": round(relief, 2),
        "landing_mean_slope": round(float(mean_slope[cz, cx]), 3),
        "landing_candidates": n_ok,
        "landing_candidates_refused_by_keepout": refused,
        "roads_land_label": spawn_label,
    }
    return {
        "id": site_id, "kind": kind, "node": rec["node"],
        "requested_xz": [rx, rz],
        "xz": [round(px, 1), round(pz, 1)],
        "patch": f.id,
        "pad_radius_m": P["landing_r_m"],
        "district_m": P["district_m"],
        "road_priority": P["road_priority"],
        "pad_y": round(float(f.h[cz, cx]), 2),
        "road_to": list(rec.get("road_to", ())),
        "measured": measured,
        "why": (f"{rec['why']} MEASURED on this seed's field ({f.id}): the "
                f"request ({rx:g}, {rz:g}) stands at {ground:.2f} m, "
                f"{max(deepest, 0.0):.2f} m under the sea, so the node is the "
                f"landing -- ({px:g}, {pz:g}), {gap:.1f} m away, {measured['landing_to_sea_m']} m "
                f"from the sea, a {P['landing_r_m']:g} m disc {relief:.2f} m "
                f"of relief, mean slope {measured['landing_mean_slope']}."),
    }


def _window_mask(f: T.Field, x: float, z: float, r_m: float) -> np.ndarray:
    zz, xx = np.mgrid[0:f.n, 0:f.n]
    return np.hypot(f.x0 + xx * f.step - x, f.z0 + zz * f.step - z) <= r_m


def claim_unit(rec: dict) -> dict:
    """The one `settlements/build.py::unit_records`-shaped unit whose stamped
    ground `plan.SiteClaims` reserves for this site: the square of side
    `2 * district_m` about the REQUEST (the district, not the landing)."""
    side = 2.0 * float(rec["district_m"])
    x, z = float(rec["requested_xz"][0]), float(rec["requested_xz"][1])
    return {"id": rec["id"], "x": x, "z": z, "yaw": 0.0, "pad_w": side,
            "pad_d": side, "foot_w": side, "foot_d": side,
            "target_y": float(rec["pad_y"])}


# ---------------------------------------------------------------------------
# SELFTEST.  CONSTRUCTED FIELDS, never the 230 MB Pirate68 scan: a test of
# "the landing is a rule" that needed Pirate68's ground would be testing it on
# Pirate68.  Each term has a known-bad case it must refuse and a neuter that
# restores the defect, so a term that stops doing anything is caught.
# ---------------------------------------------------------------------------
def _fixture(*, islet: bool = False, cliff: bool = False, lake: bool = False,
             n: int = 400) -> dict[str, T.Field]:
    """A 400 m square, 1 m step, window id `trunk-primary`, centre (200, 200).
    The sea is the northern band z < 150 (it reaches the window edge); land is
    z >= 150 at 35 m.  An inlet notches the shore at x 180..220, z 100..150.
    `islet` puts a dry disc IN the sea nearer the request than the mainland
    shore; `cliff` raises the shore into a 60 % face; `lake` removes the sea
    (an enclosed pond only)."""
    h = np.full((n, n), 35.0, np.float32)
    h[:150, :] = 20.0
    if lake:
        h[:, :] = 35.0
        h[100:140, 180:220] = 20.0
    else:
        h[150:, :] = 35.0
        h[:150, :] = 20.0
    if cliff:
        for z in range(150, 200):
            h[z, :] = 30.0 + (z - 150) * 0.6
    if islet:
        yy, xx = np.mgrid[0:n, 0:n]
        h[(yy - 120) ** 2 + (xx - 200) ** 2 <= 20 ** 2] = 36.0
    f = T.Field("trunk-primary", 200.0, 200.0, 200.0, 1.0, n, h,
                np.ones((n, n), np.uint8))
    return {"trunk-primary": f}


def _site(kind: str = "harbour_village", xz=(200, 130)) -> dict:
    return {"kind": kind, "xz": xz, "node": "x", "road_to": (),
            "why": "the owner, looking at the map."}


_SPAWN = (200.0, 300.0)       # on the mainland, so the islet is another landmass


def _case_the_node_is_the_dry_landing_not_the_request() -> list[str]:
    got = measure(_fixture(), _SPAWN, "harbour-village-x", _site())
    nx, nz = got["xz"]
    f = _fixture()["trunk-primary"]
    p = KINDS["harbour_village"]
    bad = []
    if got["requested_xz"] != [200.0, 130.0]:
        bad.append(f"the request was altered: {got['requested_xz']}")
    if not f.h[f.idx(nx, nz)] > T.WATER_LEVEL_M + FREEBOARD_M:
        bad.append(f"the node {got['xz']} is not dry with freeboard")
    # the whole landing disc is dry with freeboard
    r = p["landing_r_m"]
    cz, cx = f.idx(nx, nz)
    ri = int(math.ceil(r))
    if not (f.h[cz - ri:cz + ri + 1, cx - ri:cx + ri + 1][_disc(ri)]
            > T.WATER_LEVEL_M + FREEBOARD_M).all():
        bad.append("part of the landing disc is not dry")
    if got["measured"]["request_water_depth_m"] != 10.0:
        bad.append(f"depth under the request: {got['measured']}")
    if got["pad_radius_m"] != r:
        bad.append(f"keep-out radius {got['pad_radius_m']} != landing disc {r}")
    return bad


def _case_a_request_with_no_sea_nearby_is_REFUSED() -> list[str]:
    try:
        measure(_fixture(), _SPAWN, "harbour-village-x",
                _site(xz=(200, 330)), clear=None)
    except ValueError as exc:
        return [] if "navigable water" in str(exc) and "m away" in str(exc) \
            else [f"refused without the numbers: {exc}"]
    return ["a request 180 m from the sea was sited"]


def _case_an_inland_pond_is_not_a_harbour() -> list[str]:
    try:
        measure(_fixture(lake=True), _SPAWN, "harbour-village-x", _site())
    except ValueError as exc:
        return [] if "no navigable water" in str(exc) else [str(exc)]
    return ["an enclosed pond was taken for the sea"]


def _case_an_islet_is_not_the_roads_land() -> list[str]:
    got = measure(_fixture(islet=True), _SPAWN, "harbour-village-x", _site())
    f = _fixture(islet=True)["trunk-primary"]
    if got["xz"][1] < 150:
        return [f"the landing {got['xz']} is on the islet, which no road reaches"]
    return [] if f.h[f.idx(*got["xz"])] > 30 else ["landing not dry"]


def _case_NEUTER_any_landmass_the_islet_is_the_landing() -> list[str]:
    global _LANDMASS_MODE
    _LANDMASS_MODE = "any"
    try:
        got = measure(_fixture(islet=True), _SPAWN, "harbour-village-x", _site())
    finally:
        _LANDMASS_MODE = "spawn"
    return [] if got["xz"][1] < 150 else [
        f"the neuter did not restore the islet landing: {got['xz']}"]


def _case_a_cliff_shore_is_not_a_landing() -> list[str]:
    try:
        got = measure(_fixture(cliff=True), _SPAWN, "harbour-village-x", _site())
    except ValueError as exc:
        return [] if "mean slope" in str(exc) else [f"refused without the "
                                                    f"numbers: {exc}"]
    return [f"a 60 % face was sited at {got['xz']}, mean slope "
            f"{got['measured']['landing_mean_slope']}"]


def _case_NEUTER_any_slope_the_cliff_is_the_landing() -> list[str]:
    global _SLOPE_MODE
    _SLOPE_MODE = "any"
    try:
        got = measure(_fixture(cliff=True), _SPAWN, "harbour-village-x", _site())
    finally:
        _SLOPE_MODE = "walkable"
    return [] if got["measured"]["landing_mean_slope"] > LANDING_SLOPE_MAX else [
        "the neuter did not restore the steep landing"]


def _case_a_keepout_refusal_moves_the_landing_to_the_next_ground() -> list[str]:
    first = measure(_fixture(), _SPAWN, "harbour-village-x", _site())
    banned = first["xz"]
    got = measure(_fixture(), _SPAWN, "harbour-village-x", _site(),
                  clear=lambda x, z, r: math.hypot(x - banned[0],
                                                   z - banned[1]) > 15.0)
    if math.hypot(got["xz"][0] - banned[0], got["xz"][1] - banned[1]) <= 15.0:
        return [f"the keep-out was ignored: {got['xz']} vs banned {banned}"]
    if got["measured"]["landing_candidates_refused_by_keepout"] < 1:
        return ["the refusal was not counted"]
    return []


def _case_a_keepout_that_refuses_everything_REFUSES_by_number() -> list[str]:
    try:
        measure(_fixture(), _SPAWN, "harbour-village-x", _site(),
                clear=lambda x, z, r: False)
    except ValueError as exc:
        return [] if "refused by a generator location" in str(exc) else [str(exc)]
    return ["a site was placed on ground the keep-out refused everywhere"]


def _case_a_single_dry_sample_is_not_a_landing() -> list[str]:
    """A spit one sample wide is dry and is not ground a road can end on."""
    fx = _fixture()
    f = fx["trunk-primary"]
    f.h[140:150, 200] = 36.0          # a 1 m wide spit, 10 m long, into the sea
    got = measure(fx, _SPAWN, "harbour-village-x", _site())
    if got["xz"][1] < 150 - KINDS["harbour_village"]["landing_r_m"]:
        return [f"a 1 m spit was sited at {got['xz']}"]
    return []


def _case_NEUTER_one_cell_the_spit_is_the_landing() -> list[str]:
    global _DISC_MODE
    _DISC_MODE = "cell"
    try:
        fx = _fixture()
        fx["trunk-primary"].h[140:150, 200] = 36.0
        got = measure(fx, _SPAWN, "harbour-village-x", _site())
    finally:
        _DISC_MODE = "disc"
    return [] if got["xz"][1] < 150 else [
        f"the neuter did not restore the spit landing: {got['xz']}"]


def _case_an_unknown_kind_is_refused() -> list[str]:
    try:
        measure(_fixture(), _SPAWN, "x", _site(kind="harbour_metropolis"))
    except ValueError as exc:
        return [] if "no siting rule" in str(exc) else [str(exc)]
    return ["an unknown kind was sited"]


CASES = (
    ("the_node_is_the_dry_landing_not_the_request",
     _case_the_node_is_the_dry_landing_not_the_request),
    ("a_request_with_no_sea_nearby_is_REFUSED",
     _case_a_request_with_no_sea_nearby_is_REFUSED),
    ("an_inland_pond_is_not_a_harbour", _case_an_inland_pond_is_not_a_harbour),
    ("an_islet_is_not_the_roads_land", _case_an_islet_is_not_the_roads_land),
    ("NEUTER_any_landmass_the_islet_is_the_landing",
     _case_NEUTER_any_landmass_the_islet_is_the_landing),
    ("a_cliff_shore_is_not_a_landing", _case_a_cliff_shore_is_not_a_landing),
    ("NEUTER_any_slope_the_cliff_is_the_landing",
     _case_NEUTER_any_slope_the_cliff_is_the_landing),
    ("a_keepout_refusal_moves_the_landing_to_the_next_ground",
     _case_a_keepout_refusal_moves_the_landing_to_the_next_ground),
    ("a_keepout_that_refuses_everything_REFUSES_by_number",
     _case_a_keepout_that_refuses_everything_REFUSES_by_number),
    ("a_single_dry_sample_is_not_a_landing",
     _case_a_single_dry_sample_is_not_a_landing),
    ("NEUTER_one_cell_the_spit_is_the_landing",
     _case_NEUTER_one_cell_the_spit_is_the_landing),
    ("an_unknown_kind_is_refused", _case_an_unknown_kind_is_refused),
)


def selftest() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            problems = fn()
        except BaseException as e:                          # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  harbour site selftest {len(CASES) - bad}/{len(CASES)}  "
          f"{'PASS' if not bad else 'FAIL'}\n")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv[1:] else
                     print(__doc__) or 0)
