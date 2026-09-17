#!/usr/bin/env python3
"""HARBOURS, BOATHOUSES AND THE VESSELS IN THEM -- measure first, then build.

    waterfront.py measure                       # the candidate report, read-only
    waterfront.py audit   --site <id>           # what is standing there, live
    waterfront.py finish  --site <id> [--apply] # the missing fixtures + boat
    waterfront.py stair   --site <id> [--apply] # bridge a road/deck step
    waterfront.py build   --site <id> [--apply] # a new slip or jetty
    waterfront.py save                --apply   # ledger save + postcondition

WHY THIS FILE EXISTS RATHER THAN A RE-RUN OF `crossings/plan.py`.  `plan.py` is
the only thing that computes the six waterfront structures already standing, and
it REWRITES `crossings/out/ledger.jsonl` and `crossings/structures.yaml` for all
of them on every run.  Re-running it against a height field whose patches are
not byte-identical to the one it was first run with would silently move the
recorded geometry of things already built -- a published interchange file
disagreeing with the world, which is the defect class this project pays for most
often.  So `plan.py` and `assemble.py` are used HERE AS LIBRARIES, exactly the
way `flatten.py` is required to be, and nothing under `crossings/` is written.

THE THREE RULES THAT SHAPE EVERY NUMBER BELOW, each measured and each already
paid for once:

  1. A SLIP THAT CANNOT FLOAT A BOAT IS SCENERY.  So a berth is not "is there
     water": it is the deepest sample in the berth disc that is BOTH at or over
     the vessel's own draught plus a margin AND flood-fill connected to the open
     sea.  A 9 m hole behind a sandbar berths nothing.  `berth()` returns the
     connectivity, not just the depth.
  2. NEVER WRITE TERRAIN THAT LOWERS GROUND TOWARDS `c_WaterLevel` UNDER OR
     BESIDE A STRUCTURE.  Draining water out from under a pier is the early-dock
     defect and it is unrepairable.  Every geometry this module emits is a
     STRUCTURE.  It writes no terrain at all, which is why the road/deck step at
     harbour-temple-south is fixed with a stair and not with a cut.
  3. THE INSTRUMENT IS THE ORACLE.  The stair run is not arithmetic on an
     assumed slope: `stair_run()` asks the composed applied surface at each
     candidate tread and stops when the measurement says it has arrived, then
     REPORTS the residual it could not close.  A guard that narrows a request
     says so.

THE SURFACE A PLAYER STANDS ON is composed the only way that is true of the live
world: the GENERATED height from a lattice-aligned PatchScan field, plus the
union of the ledger's own `terrain_write` blobs in FILE ORDER, bilinearly
blended -- i.e. `roads/applied.py`, which is the instrument that found every
defect in this family.  A planned profile, a nearest sample, or this run's own
compilers each answer a different question and each has produced a false clear.

VESSELS.  `VESSELS` below is MEASURED, not read off the mod's README: every
figure comes from `crossings/PieceMaterial.cs` + `blueprints/PieceGeometry.cs`
run over ZNetScene in a sandbox copy of THIS server with the content mods
staged, so a name that is not in ZNetScene cannot appear here and
`spawn_object` cannot silently place nothing.  The hull filter (solid,
non-trigger colliders, `mesh_render` excluded) is CALIBRATED: it reproduces the
9.16 x 21.56 m recorded for `VikingShip` in ledger seq 33 to 0.01 m.  The
`mesh_render` bound is 11.64 x 35.43 m for the same prefab -- a water-ripple
plane, not a hull -- and using it would oversize every slip by 60 %.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "crossings", JUMPSTART / "roads",
           JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import assemble as A  # noqa: E402  crossings' piece geometry + support model
import data_entry as DE  # noqa: E402  the one ZDO-blob encoder in this repo
import fixtures as FX  # noqa: E402  portal tag validation
import plan as CP  # noqa: E402  crossings' aimer and POI gate, as a library
import water as W  # noqa: E402  VHPATCH1 -> numpy, water datums

ACTOR = "HarbourWork"

# The lattice-aligned field this module measures against.  Aligned means
# `cx - half + step/2` is an INTEGER, so every field sample sits exactly on a
# TerrainComp sample centre and the generated height under a written delta needs
# no interpolation.  Produced by blueprints/run_patchscan.sh (rivers included).
FIELD = "/tmp/harbour/h1m.bin"
WORLD_FIELD = "/tmp/harbour/world.bin"

# MEASURED in a sandbox copy of the Ulfsland server with Jotunn + JsonDotNET +
# OdinShip + RavenwoodVikingHouses + RavenwoodRestorations staged, dumping
# ZNetScene.m_prefabs after the scene came up.  4,872 prefabs; all 25 documented
# OdinShip prefabs present; every vessel ZNetView.m_persistent = 1, so a berthed
# ship is written to the database and survives a restart.
#   draught_m  = Ship.m_waterLevelOffset, how far the float collider centre sits
#                below the water plane.  A berth shallower than this grounds.
#   hull_w/l   = solid non-trigger collider AABB, the envelope a slip must clear.
VESSELS: dict[str, dict] = {
    # --- small: a slip for a short hop ---
    "DoubleRowingCanoe": dict(draught_m=0.8, hull_w=3.84, hull_l=8.20,
                              cls="small", mass=600),
    "RowingCanoe": dict(draught_m=1.0, hull_w=7.68, hull_l=6.59,
                        cls="small", mass=500),
    "LittleBoat": dict(draught_m=1.2, hull_w=4.23, hull_l=10.37,
                       cls="small", mass=1000),
    # --- large: a harbour berth ---
    "MercantShip": dict(draught_m=1.5, hull_w=9.16, hull_l=21.57,
                        cls="large", mass=2000),
    "BigCargoShip": dict(draught_m=1.5, hull_w=14.10, hull_l=20.05,
                         cls="large", mass=2000),
    "WarShip": dict(draught_m=1.7, hull_w=10.07, hull_l=20.08,
                    cls="large", mass=2000),
    "CargoShip": dict(draught_m=2.0, hull_w=9.16, hull_l=15.71,
                      cls="large", mass=2000),
    # vanilla, kept so an existing berth can be described in the same units
    "VikingShip": dict(draught_m=1.5, hull_w=9.16, hull_l=21.57,
                       cls="large", mass=2000),
    "Karve": dict(draught_m=1.2, hull_w=7.68, hull_l=10.12,
                  cls="small", mass=1000),
}

# Freeboard under the keel.  The project's own `MOOR_DEPTH_M` is 2.5 m for a
# 1.5 m draught, so the margin that number encodes is 1.0 m; it is spelled out
# here rather than re-derived per vessel.
KEEL_CLEARANCE_M = 1.0

# `wood_stair`, MEASURED from PieceGeometry: solid AABB 2.000 (x) x 2.029 (z) x
# 1.121 (y), and the TREADS DESCEND WITH +z -- the top tread box is
# y[0.86,1.05] at z[-1.02,-0.93] and the bottom one y[-0.07,0.10] at
# z[0.75,1.00].  So the piece rises towards -z and a run that climbs towards
# bearing B is placed at yaw = B + 180.
STAIR = dict(prefab="wood_stair", run_m=2.029, rise_m=1.05,
             foot_above_origin=0.10, head_above_origin=1.05)
# `wood_floor` top face sits +0.097 above its own origin, so a deck authored at
# y = 30.5 has a walking surface at 30.597 -- which IS the 30.6 deck datum.
FLOOR_TOP_OFFSET = 0.097
DECK_DATUM_Y = A.DECK_Y  # 30.6


# ---------------------------------------------------------------------------
# the surface
# ---------------------------------------------------------------------------

class Surface:
    """The applied surface a player stands on, plus the water under it.

    `generated` comes from a lattice-aligned PatchScan patch; `delta` from the
    ledger's own terrain blobs unioned in FILE ORDER (not `seq` -- the chain
    forked at file line 47 and carries duplicate seq labels, so `seq` is not a
    total order on this artefact).
    """

    def __init__(self, field: str = FIELD, actor: str = ACTOR):
        import applied as AP  # noqa: PLC0415  roads/, imported lazily
        self.fields = W.load(field)
        self.applied = AP.Applied(actor=actor)
        for fid, f in self.fields.items():
            off = (f.cx - f.half + f.step / 2.0)
            if abs(off - round(off)) > 1e-6:
                raise SystemExit(
                    f"patch {fid} is NOT lattice aligned (x0={off}); its samples "
                    f"sit half a metre off every TerrainComp sample centre and a "
                    f"delta composed onto it would be interpolated rather than "
                    f"read. Regenerate with half = k + 0.5 for an integer centre.")

    # -- patch selection ---------------------------------------------------

    def patch_for(self, x: float, z: float, margin: float = 2.0):
        """The SMALLEST patch that contains the point with margin.

        Smallest, because the overview patches are 8 m and 16 m and a 1 m
        question answered off a 16 m patch is a different question.
        """
        best = None
        for f in self.fields.values():
            if not f.contains(x, z, margin):
                continue
            if best is None or (f.step, f.half) < (best.step, best.half):
                best = f
        if best is None:
            raise SystemExit(
                f"({x}, {z}) is in no patch of {FIELD}; widen the request and "
                f"re-run run_patchscan.sh rather than guessing a height")
        return best

    def generated(self, x: float, z: float) -> float:
        return float(self.patch_for(x, z).height_at(x, z))

    def gen_sample(self, sx: int, sz: int) -> float:
        f = self.patch_for(float(sx), float(sz), margin=0.0)
        j = int(round(sx - f.x0))
        i = int(round(sz - f.z0))
        return float(f.heights[i, j])

    def at(self, x: float, z: float) -> tuple[float, float, float]:
        """`(applied, generated, delta)`.

        The generated half is the BILINEAR blend of the four INTEGER lattice
        samples around the point, which is how `Heightmap` renders it and what
        the delta is indexed by.  Blending the field's own half-metre grid
        instead would smooth a 1 m notch away.
        """
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        gen = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if w:
                gen += w * self.gen_sample(x0 + dx, z0 + dz)
        d = self.applied.delta_at(x, z)
        return gen + d, gen, d

    def author_at(self, sx: int, sz: int):
        """Which ledger record's ground this integer sample is, or None."""
        _d, w = self.applied.sample(int(sx), int(sz))
        return w


# ---------------------------------------------------------------------------
# water
# ---------------------------------------------------------------------------

def sea_mask(fld, min_depth_m: float) -> tuple[np.ndarray, np.ndarray]:
    """`(deep, open)` where `deep` is depth >= min_depth and `open` is the
    subset of `deep` that is 4-connected to the patch boundary.

    The boundary test is what makes this a NAVIGABILITY measurement rather than
    a depth measurement: a landlocked pocket of 9 m water berths nothing, and
    the 35.5 ha inland lake on this island would otherwise score as a harbour.
    """
    from scipy import ndimage  # noqa: PLC0415
    depth = W.WATER_LEVEL - fld.heights
    deep = depth >= min_depth_m
    lab, _ = ndimage.label(deep)
    edge = set(lab[0, :].tolist()) | set(lab[-1, :].tolist()) \
        | set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    edge.discard(0)
    return deep, np.isin(lab, list(edge))


def berth(surf: Surface, x: float, z: float, vessel: str,
          radius_m: float = 12.0) -> dict:
    """CAN THIS VESSEL FLOAT HERE, and where exactly.

    Returns the deepest sample inside the disc that clears the vessel's own
    draught plus `KEEL_CLEARANCE_M` AND is connected to the open sea, the area
    of such water, and the verdict.  `ok=False` with a real `max_depth_m` is the
    sandbar case and it is reported as such rather than as "shallow".
    """
    v = VESSELS[vessel]
    need = v["draught_m"] + KEEL_CLEARANCE_M
    fld = surf.patch_for(x, z, margin=radius_m + 2.0)
    deep, open_sea = sea_mask(fld, need)
    depth = W.WATER_LEVEL - fld.heights
    ci, cj = fld.index(x, z)
    r = int(math.ceil(radius_m / fld.step))
    ii, jj = np.mgrid[max(0, ci - r):min(fld.n, ci + r + 1),
                      max(0, cj - r):min(fld.n, cj + r + 1)]
    disc = np.hypot((ii - ci) * fld.step, (jj - cj) * fld.step) <= radius_m
    usable = disc & open_sea[ii, jj]
    sub = depth[ii, jj]
    out = dict(
        vessel=vessel, draught_m=v["draught_m"],
        required_depth_m=round(need, 2),
        hull_w_m=v["hull_w"], hull_l_m=v["hull_l"],
        centre=[round(x, 2), round(z, 2)], radius_m=radius_m,
        patch=fld.id, patch_step_m=fld.step,
        max_depth_in_disc_m=round(float(sub[disc].max()), 3),
        navigable_area_m2=round(float(usable.sum()) * fld.step ** 2, 1),
        connected_to_open_sea=bool(usable.any()),
    )
    if usable.any():
        k = int(np.argmax(np.where(usable, sub, -1e9)))
        bi, bj = np.unravel_index(k, usable.shape)
        out["deepest_navigable"] = [round(float(fld.world_x(jj[bi, bj])), 2),
                                    round(float(fld.world_z(ii[bi, bj])), 2),
                                    round(float(sub[bi, bj]), 3)]
    out["ok"] = bool(out["connected_to_open_sea"]
                     and out["max_depth_in_disc_m"] >= need)
    out["why"] = (
        "MEASURED: depth = c_WaterLevel 30.0 - generated height on a "
        f"{fld.step:g} m PatchScan patch (rivers included); the usable set is "
        f"depth >= draught {v['draught_m']} + keel clearance {KEEL_CLEARANCE_M} "
        "AND 4-connected to the patch boundary, so a landlocked pocket cannot "
        "score as a berth.")
    return out


def vessel_for(depth_m: float, *, want: str = "any",
               max_beam_m: float | None = None) -> str | None:
    """The LARGEST vessel this depth and beam will actually float.

    Largest by draught, because a large boathouse berthing a rowboat is the
    same defect as a slip that cannot float a boat.  Returns None rather than
    narrowing silently to a canoe when nothing fits -- a guard that shrinks a
    request has to say so, and the caller is the right place to say it.
    """
    best = None
    for name, v in VESSELS.items():
        if want != "any" and v["cls"] != want:
            continue
        if v["draught_m"] + KEEL_CLEARANCE_M > depth_m:
            continue
        if max_beam_m is not None and v["hull_w"] > max_beam_m:
            continue
        key = (v["draught_m"], v["hull_w"] * v["hull_l"])
        if best is None or key > best[0]:
            best = (key, name)
    return None if best is None else best[1]


# ---------------------------------------------------------------------------
# the stair that fixes a road/deck step
# ---------------------------------------------------------------------------

def stair_run(surf: Surface, foot_xz: tuple[float, float], bearing_deg: float,
              deck_top_y: float, *, max_steps: int = 6,
              close_tol_m: float = 0.35, probe_m: float = 12.0) -> dict:
    """Stair the bank between a deck top and the road above it, TOP DOWN.

    THE BOTTOM-UP VERSION WAS WRONG AND IT IS WORTH KEEPING THE CORPSE.  Built
    from the deck upwards at the stair's own 1.05 m rise per 2.03 m run -- a
    26 deg pitch -- the treads march out over ground that MEASURES 32.230 at
    (11,-256), only 1.6 m from a deck top of 30.597.  The road's levelled
    platform is effectively a 1.63 m retaining face AT the deck edge: no 26 deg
    run reaches it inside the space available, and the stairs it placed would
    have been buried 1.7 m in the bank.  Seating a structure on an ASSUMED
    slope is the same defect as walking a planned profile.

    So the run is anchored at the TOP, on the applied surface where the bank
    stops rising, and descends to the deck; the number that has to come out
    small is the residual at the BOTTOM.  Reported, never hidden:
      `residual_m`   deck top minus the lowest tread.  Positive means the deck
                     stands above the first tread -- a kerb, walkable either
                     way.  Negative means the tread is above the deck and the
                     step survives, which the caller must refuse.
      `max_burial_m` the worst amount by which the applied ground stands ABOVE
                     a tread's walking surface.  Stairs cut into a bank are
                     expected to be partly embedded; a tread buried deeper
                     than its own 0.26 m riser is not a stair, it is landfill.
    """
    ux, uz = A._unit(bearing_deg)          # up-slope, towards the road
    # 1. FIND THE TOP by measurement: walk up-slope until the applied surface
    #    stops climbing. The platform, not a guessed distance.
    top_t, top_y = 0.0, surf.at(*foot_xz)[0]
    t = 0.0
    while t < probe_m:
        t += 0.25
        y = surf.at(foot_xz[0] + ux * t, foot_xz[1] + uz * t)[0]
        if y > top_y + 1e-3:
            top_y, top_t = y, t
        elif t - top_t > 2.0:
            break
    top_xz = (foot_xz[0] + ux * top_t, foot_xz[1] + uz * top_t)
    rise_needed = top_y - deck_top_y
    n = max(0, min(max_steps, int(math.ceil(rise_needed / STAIR["rise_m"]))))
    # 2. LAY THE TREADS DOWNWARD from the platform.
    steps = []
    head_surface = top_y
    for i in range(n):
        origin_y = head_surface - STAIR["head_above_origin"]
        foot_surface = origin_y + STAIR["foot_above_origin"]
        d = top_t - (i + 0.5) * STAIR["run_m"]
        cx = foot_xz[0] + ux * d
        cz = foot_xz[1] + uz * d
        ground_here = surf.at(cx, cz)[0]
        tread_mid = (head_surface + foot_surface) / 2.0
        steps.append(dict(
            prefab=STAIR["prefab"], x=round(cx, 3), y=round(origin_y, 3),
            z=round(cz, 3), yaw=(bearing_deg + 180.0) % 360.0,
            head_surface_y=round(head_surface, 3),
            foot_surface_y=round(foot_surface, 3),
            ground_at_centre_y=round(ground_here, 3),
            burial_m=round(max(0.0, ground_here - tread_mid), 3),
            index=i))
        head_surface = foot_surface
    residual = deck_top_y - head_surface
    return dict(
        steps=steps, foot_xz=[round(foot_xz[0], 3), round(foot_xz[1], 3)],
        bearing_deg=bearing_deg, deck_top_y=round(deck_top_y, 3),
        top_xz=[round(top_xz[0], 3), round(top_xz[1], 3)],
        top_ground_y=round(top_y, 3), top_found_at_m=round(top_t, 2),
        rise_needed_m=round(rise_needed, 3), steps_laid=n,
        bottom_tread_surface_y=round(head_surface, 3),
        residual_m=round(residual, 3),
        max_burial_m=round(max([s["burial_m"] for s in steps], default=0.0), 3),
        closed=bool(n > 0 and residual >= -close_tol_m),
        why=("MEASURED per tread against the composed applied surface "
             "(lattice-aligned PatchScan generated height + the ledger's own "
             "terrain_write blobs unioned in FILE ORDER, bilinear). The top is "
             "found by walking up-slope until the surface stops rising, not by "
             "assuming a distance. residual_m >= 0 is a kerb at the deck; "
             "residual_m < 0 means the step survives."),
        tool="tools/jumpstart/settlements/waterfront.py::stair_run")


# ---------------------------------------------------------------------------
# live audit
# ---------------------------------------------------------------------------

CENSUS_CELL_M = 16.0
CENSUS_Y_LEVELS = (0.0, -16.0, 16.0, -32.0, 32.0)


def audit(srv, x0: float, x1: float, z0: float, z1: float, *,
          pad_m: float = 4.0, y_centre: float = 30.0) -> dict:
    """EVERY ZDO in a box, live, by prefab and WORLD POSITION.

    Disjoint 16 m cells on the global 16 m lattice, one UNSCOPED
    `findObjects -near cx y cz 8` per cell at FIVE y levels, because
    `findObjects -near` is a CUBE IN ALL THREE AXES and a box centred on the
    deck cannot contain a mast or a pile head.  No prefab list at all, so the
    census cannot be blind to a prefab nobody wrote down.  Deduplicated by
    prefab + world position, NOT by ZDO id: ids are reassigned on world load.
    """
    import clear as CL  # noqa: PLC0415  roads/, and the guard lives in it
    half = CENSUS_CELL_M / 2.0
    cells = []
    cx = math.floor((x0 - pad_m) / CENSUS_CELL_M) * CENSUS_CELL_M + half
    while cx - half < x1 + pad_m:
        cz = math.floor((z0 - pad_m) / CENSUS_CELL_M) * CENSUS_CELL_M + half
        while cz - half < z1 + pad_m:
            cells.append((cx, cz))
            cz += CENSUS_CELL_M
        cx += CENSUS_CELL_M
    by_pos: dict[tuple, dict] = {}
    engine: dict[tuple, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    for ccx, ccz in cells:
        for dy in CENSUS_Y_LEVELS:
            found, un, n, eng = CL.list_box(srv, ccx, y_centre + dy, ccz)
            calls += n
            unlisted += un
            for o in found.values():
                by_pos[(o["prefab"], round(o["x"], 2), round(o["y"], 2),
                        round(o["z"], 2))] = o
            for o in eng.values():
                engine[(o["prefab"], round(o["x"], 2), round(o["z"], 2))] = o
    per = {}
    for p, *_ in by_pos:
        per[p] = per.get(p, 0) + 1
    return dict(
        box=[x0, x1, z0, z1], pad_m=pad_m, cells=len(cells),
        y_levels=list(CENSUS_Y_LEVELS), socket_calls=calls,
        per_prefab=dict(sorted(per.items())),
        total=len(by_pos), objects=list(by_pos.values()),
        engine_per_prefab={p: sum(1 for k in engine if k[0] == p)
                           for p in {k[0] for k in engine}},
        unlisted_boxes=unlisted,
        method=("MEASURED live over the RCON SOCKET ONLY. Disjoint 16 m cells on "
                "the global 16 m lattice; one UNSCOPED `findObjects -near cx y cz "
                "8` per cell at five y levels (-32,-16,0,+16,+32); a reply over 18 "
                "rows re-asked as eight TILING sub-boxes so nothing approaches the "
                "client's 4096-byte buffer. Deduplicated by prefab + WORLD "
                "POSITION because ZDO ids are reassigned on world load."),
        tool="tools/jumpstart/settlements/waterfront.py::audit")


def read_back(srv, prefab: str, x: float, y: float, z: float,
              radius: float = 3.0) -> dict:
    """`findObjects -detailed` on one fixture, bounded, so a sign's text and a
    portal's tag are read off the ZDO instead of being assumed from the record
    that wrote them."""
    import replay as R  # noqa: PLC0415
    text, leaves, unlisted = R.bounded_detailed(srv, prefab, x, y, z, radius)
    return dict(prefab=prefab, at=[x, y, z], radius_m=radius,
                leaves=leaves, unlisted=unlisted, listing=text)


# ---------------------------------------------------------------------------
# ops
# ---------------------------------------------------------------------------

def sign_blob(text: str) -> str:
    e = DE.DataEntry()
    e.strings[DE.stable_hash("text")] = text
    return e.encode()


def portal_blob(tag: str) -> str:
    problem = FX.portal_tag_problem(tag)
    if problem:
        raise SystemExit(f"portal tag {tag!r} rejected: {problem}")
    e = DE.DataEntry()
    e.strings[DE.stable_hash("tag")] = tag
    return e.encode()


def _KNOWN_ROLES() -> frozenset:
    from ledger import schema as _s  # noqa: PLC0415
    return frozenset(_s.KNOWN_ROLES)


def _common(site: str, role: str, reason: str) -> dict:
    return dict(role=role, site_id=site, flatten="FORBIDDEN",
                flatten_reason=reason)


OVER_WATER = ("over-water structure; a terrain_write here would remove the "
              "water the piles are driven into, which is the early-dock defect")
ON_DECK = ("the piece stands on an existing deck at the 30.6 datum; there is no "
           "ground here to write and the bank beside it is 60 cm above the "
           "water plane, so a cut is refused")


def spawn_op(site: str, role: str, prefab: str, x: float, y: float, z: float,
             yaw: float, *, reason: str, blob: str | None = None,
             strings: dict | None = None, guard: float = 0.5,
             meta: dict | None = None) -> dict:
    cmd = (f"spawn_object {prefab} pos={A.fmt(z)},{A.fmt(x)},{A.fmt(y)}"
           f" rot={A.fmt(yaw)},0,0 from=0,0,0")
    # `params.role` MUST be one of `schema.KNOWN_ROLES`; the descriptive
    # per-piece name goes in `meta.part`, which is the convention the existing
    # records already use (seq 28 carries role `harbour_place` in params and
    # `harbour-temple-south/sign_post` in meta).
    part = None
    if "/" in role or role not in _KNOWN_ROLES():
        part, role = role, SITE_ROLE.get(site, PROBE_ROLE)
    params = dict(prefab=prefab, pos=[round(x, 4), round(y, 4), round(z, 4)],
                  yaw_deg=yaw, guard_radius_m=guard,
                  **_common(site, role, reason))
    if blob:
        cmd += f" data={blob}"
        params["data_b64"] = blob
        params["zdo_strings"] = strings or {}
    meta = dict(meta or {})
    if part:
        meta["part"] = part
    return dict(op="spawn", params=params, wire=[cmd],
                requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                              prefabs=[prefab], blobs=[]),
                expect=dict(prefab_count=[dict(
                    prefab=prefab, pos=[round(x, 2), round(z, 2)],
                    max=max(3.0, guard * 2.0), count=1, tolerance=0)]),
                meta=meta)


def boat_op(site: str, vessel: str, x: float, z: float, yaw: float,
            berth_report: dict) -> dict:
    """A berthed vessel.

    Spawn y is the water plane EXACTLY.  A boat spawned high falls, and
    `Ship.m_waterImpactDamage` is 10 per hard landing (MEASURED on every ship
    prefab in ZNetScene).  The guard is 6 m rather than the 0.5 m used for
    pieces because a hull is 9-14 m wide and WILL have drifted by the time a
    replay probes for it, so a tight guard spawns a second boat every replay.
    """
    v = VESSELS[vessel]
    cmd = (f"spawn_object {vessel} pos={A.fmt(z)},{A.fmt(x)},"
           f"{A.fmt(W.WATER_LEVEL)} rot={A.fmt(yaw)},0,0 from=0,0,0")
    return dict(
        op="spawn",
        params=dict(prefab=vessel,
                    pos=[round(x, 2), W.WATER_LEVEL, round(z, 2)],
                    yaw_deg=yaw, guard_radius_m=6.0,
                    role="boat_spawn_moor", site_id=site,
                    flatten="FORBIDDEN",
                    flatten_reason=("a boat floats on the water plane; there is "
                                    "no ground here to write")),
        wire=[cmd],
        requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                      prefabs=[vessel], blobs=[]),
        expect=dict(prefab_count=[dict(prefab=vessel,
                                       pos=[round(x, 2), round(z, 2)],
                                       max=12.0, count=1, tolerance=0)]),
        meta=dict(vessel_class=v["cls"], draught_m=v["draught_m"],
                  hull_w_m=v["hull_w"], hull_l_m=v["hull_l"],
                  berth=berth_report,
                  why=("MEASURED in a sandbox copy of this server with the content "
                       "mods staged: this prefab is in ZNetScene with "
                       "ZNetView.m_persistent=1, so the berthed vessel is written "
                       "to the database rather than being a prop. Draught is "
                       "Ship.m_waterLevelOffset and the berth clears it by at "
                       f"least {KEEL_CLEARANCE_M} m of keel clearance.")))


# ---------------------------------------------------------------------------
# planned-but-unbuilt structures: reuse the ops that were VERIFIED, not new ones
# ---------------------------------------------------------------------------

PLANNED = JUMPSTART / "crossings" / "out" / "ledger.jsonl"
LIVE_LEDGER = JUMPSTART / "ledger" / "runs" / "Ulfsland" / "ledger.jsonl"


def planned_ops(site: str) -> list[dict]:
    """The op list `crossings/plan.py` computed and `verify.json` passed.

    Read, never regenerated.  The geometry of a structure that was measured and
    verified is not re-derived off a different height field just because this
    module happens to hold one.
    """
    out = []
    for line in PLANNED.read_text().splitlines():
        if not line.strip():
            continue
        op = json.loads(line)
        if (op.get("params") or {}).get("site_id") == site:
            out.append(op)
    if not out:
        raise SystemExit(f"no planned ops for {site!r} in {PLANNED}")
    return out


def op_key(op: dict) -> tuple:
    """An op's identity for the already-emitted test.

    Keyed on (op, prefab, rounded position) rather than on `role`, because the
    live ledger's records carry the role of the op that SENT them and a
    re-emitted fixture would match on role while standing somewhere else.
    """
    p = op.get("params") or {}
    pos = p.get("pos") or p.get("centre") or p.get("anchor") or []
    if isinstance(pos, dict):
        pos = [pos.get("x"), pos.get("z")]
    xz = tuple(round(float(v), 1) for v in (pos[0], pos[-1])) if pos else ()
    return (op["op"], p.get("prefab") or p.get("plan_ref"), xz)


def already_emitted(site: str) -> set:
    keys = set()
    for line in LIVE_LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if (rec.get("params") or {}).get("site_id") != site:
            continue
        if rec["op"] in ("note", "observe"):
            continue
        keys.add(op_key(rec))
    return keys


# ---------------------------------------------------------------------------
# the sites this module owns work on
# ---------------------------------------------------------------------------
#
# `bearing` is the SEAWARD bearing of the pier, taken from the structure that is
# already standing (measured from its own deck tiles) or from the planned record
# for one that is not.  `stair` names the deck edge a road/deck step has to be
# bridged at, and it is only set where the step was MEASURED, never where it was
# assumed.
SITES: dict[str, dict] = {
    "harbour-temple-south": dict(
        kind="harbour", root=(11.5, -258.5), bearing=220.0, length_m=24.1,
        head=(-4.06, -276.88), deck_top_y=30.597, width_tiles=3,
        boat_offset_m=7.5,
        stair=dict(foot=(10.797, -256.383), bearing=45.0),
        rail_from="wood_pole",
        why=("The main harbour, 268 m from StartTemple and the first waterfront "
             "a player sees. Standing and complete as 72 pieces, but its road "
             "join steps and its signed Longship berth is empty."),
    ),
    "boathouse-temple-strait": dict(
        kind="slip", root=(-305.98, -110.4), bearing=190.0, length_m=24.0,
        inner_width_m=11.0, deck_top_y=30.597,
        why=("A slip in the strait lagoon 36 m south of bridge S1, shelter 0.96 "
             "-- the most enclosed water on either island. Built as a SLIP ONLY: "
             "its sign post, sign and boat were never emitted."),
    ),
    "dock-stationhub": dict(
        kind="jetty", root=(510.41, 1058.09), bearing=115.0, length_m=20.0,
        deck_top_y=30.597,
        why="Planned, verified and never built. Serves the station hub 160 m east.",
    ),
    "harbour-vestvik": dict(
        kind="jetty", root=(-1038.22, 1661.95), bearing=305.0, length_m=22.0,
        deck_top_y=30.597, width_tiles=3,
        # the curtain arc is centred on the SHORELINE root (-1031.5, 1656.5),
        # not on the spawn_plan anchor, so the wall stands behind the quay
        # rather than around its middle. Road arrives on bearing 125.
        port_centre=(-1031.5, 1656.5), gate_bearing_deg=125.0,
        why=("Planned, verified and never built. Vestvik's own shoreline is too "
             "shallow for a 22 m pier at any bearing, so this is the village's "
             "quay and the road joins the two."),
    ),
}


# EVERY `role` MUST BE IN `ledger/schema.py::KNOWN_ROLES` -- the schema refuses
# an unmodelled role, and correctly: a role nobody declared is a role no invariant
# can be written against. So these map onto roles that already exist rather than
# adding new ones, and a probe borrows `site_prop` because that is what a probe
# object is: a prop, load-bearing for nothing.
SITE_ROLE: dict[str, str] = {
    "harbour-temple-south": "harbour_place",
    "boathouse-temple-strait": "boathouse_place",
    "dock-stationhub": "dock_place",
    "harbour-vestvik": "harbour_place",
    "harbour-stenvik-port": "harbour_place",
    "dock-dockshore": "dock_place",
    "dock-northcape": "dock_place",
}
PROBE_ROLE = "site_prop"


# ---------------------------------------------------------------------------
# passes
# ---------------------------------------------------------------------------

def hull_depths(surf: Surface, bearing: float, bx: float, bz: float,
                vessel: str):
    """`(min, max, connected, samples)` water depth under a hull centred here.

    The footprint is an ELLIPSE of the vessel's solid-collider half-extents
    rotated onto the pier bearing, not a disc and not a point: a point check
    passes on a 3 m pothole inside a 2 m shoal and a 21 m hull does not fit in a
    pothole.  The binding number is the SHALLOWEST sample, because that is what
    the keel touches.
    """
    v = VESSELS[vessel]
    need = v["draught_m"] + KEEL_CLEARANCE_M
    ux, uz = A._unit(bearing)
    lx, lz = uz, -ux
    fld = surf.patch_for(bx, bz, margin=v["hull_l"])
    hl, hw = v["hull_l"] / 2.0, v["hull_w"] / 2.0
    ci, cj = fld.index(bx, bz)
    r = int(math.ceil(max(hl, hw) / fld.step)) + 1
    ii, jj = np.mgrid[max(0, ci - r):min(fld.n, ci + r + 1),
                      max(0, cj - r):min(fld.n, cj + r + 1)]
    dx = fld.world_x(jj) - bx
    dz = fld.world_z(ii) - bz
    hull = ((dx * ux + dz * uz) / hl) ** 2 + ((dx * lx + dz * lz) / hw) ** 2 <= 1.0
    if not hull.any():
        return None
    depth = W.WATER_LEVEL - fld.heights[ii, jj]
    _deep, open_sea = sea_mask(fld, need)
    # `open_sea[ii, jj][hull]`, NOT `(hull & open_sea).all()`: the second form
    # is False wherever the hull mask is False, i.e. everywhere outside the
    # ellipse, so it answers "is the whole neighbourhood navigable" and reported
    # a 4.669 m berth as unusable. MEASURED: it rejected every vessel at every
    # site, including a 6.1 m one.
    return (float(depth[hull].min()), float(depth[hull].max()),
            bool(open_sea[ii, jj][hull].all()), int(hull.sum()), fld)


def planned_pieces(site: str) -> list[tuple[str, float, float, float]]:
    """`(prefab, x, y, z)` for every piece in the site's planned `spawn_plan`.

    The wire's field order is `pos=<z>,<x>,<y>` -- three component orders live
    in one `spawn_object` command and this is the one that has been verified
    live against three structures whose `expect.prefab_count` positions are
    (x, z).  It is decoded here rather than assumed.
    """
    out = []
    for op in planned_ops(site):
        if op["op"] != "spawn_plan":
            continue
        for line in op["wire"]:
            parts = line.split()
            if len(parts) < 3 or parts[0] != "spawn_object":
                continue
            pos = parts[2].split("=", 1)[1].split(",")
            z, x, y = (float(v) for v in pos[:3])
            out.append((parts[1], x, y, z))
    return out


def berth_seed(surf: Surface, site: str) -> dict:
    """Where to start looking for the berth: the PIER HEAD, measured.

    Preference order, and each step is evidence rather than taste:
      1. the planned boat position, if `crossings/plan.py` computed one -- that
         geometry was verified and re-deriving it would risk moving it;
      2. otherwise the planned deck tile with the LARGEST PROJECTION ON THE
         SEAWARD BEARING among the tiles that stand over water, pushed 6 m
         further out.

    THE REJECTED RULE, and why, because it is the kind of check that answers a
    question it was not asked: "the deck tile with the deepest water under it".
    MEASURED at `dock-stationhub`, that tile is (508.36, 1060.87) at 3.418 m --
    which is MID-DECK, over a channel that runs under the jetty, while the
    actual head at (520.20, 1053.14) sits over 3.402 m. Seeding 6 m seaward of
    the wrong one put the hull on the isthmus at 4.12 m ABOVE the water plane
    and the whole site then reported "no vessel floats".
    """
    s = SITES[site]
    if s.get("berth_seed_xz"):
        # A site this run just PLANNED has no record to read; the seed comes
        # from the geometry that was computed, which is the same evidence.
        return dict(x=float(s["berth_seed_xz"][0]), z=float(s["berth_seed_xz"][1]),
                    source=s.get("berth_seed_why", "seeded from this run's own "
                                                   "planned geometry"),
                    planned_prefab=None)
    for op in planned_ops(site):
        p = op.get("params") or {}
        if op["op"] == "spawn" and p.get("role") == "boat_spawn_moor":
            return dict(x=float(p["pos"][0]), z=float(p["pos"][2]),
                        source=f"planned boat position for {site}",
                        planned_prefab=p.get("prefab"))
    ux, uz = A._unit(s["bearing"])
    wet = [(x, z) for pf, x, _y, z in planned_pieces(site)
           if pf == "wood_floor" and surf.generated(x, z) < W.WATER_LEVEL]
    if not wet:
        raise SystemExit(f"{site}: no planned deck tile stands over water, so it "
                         f"has no head and berths nothing")
    head = max(wet, key=lambda t: t[0] * ux + t[1] * uz)
    return dict(x=round(head[0] + ux * 6.0, 2), z=round(head[1] + uz * 6.0, 2),
                source=(f"planned deck tile farthest along bearing "
                        f"{s['bearing']:.0f} that stands over water "
                        f"({head[0]:.2f},{head[1]:.2f}), depth "
                        f"{W.WATER_LEVEL - surf.generated(*head):.3f} m, pushed "
                        "6 m to seaward"),
                planned_prefab=None)


def berth_spot(surf: Surface, site: str, vessel: str) -> dict:
    """SEARCH for where the hull goes, and prove the WHOLE hull floats.

    Seeded on the planned berth or the measured pier head, then swept.  Two
    things this deliberately does NOT do:

      * It does not take a fixed fraction along the pier.  `plan.py` used
        `length * 0.6` with a flat 7.5 m offset; MEASURED at
        harbour-temple-south that lands a hull over 2.33 m of water while
        4.67 m stands 12 m further out, so the heuristic would berth a rowboat
        in an 8.9 m harbour and the sign promising a Longship berth would still
        be lying.
      * It does not maximise depth.  Scored on depth alone the sweep put a
        `CargoShip` 14.6 m off the quay, which reads as a boat adrift rather
        than a boat berthed.  So the score is FLOATS FIRST, then NEAREST TO THE
        SEED -- the shallowest berth that still clears the keel, hard against
        the pier.
    """
    s = SITES[site]
    v = VESSELS[vessel]
    need = v["draught_m"] + KEEL_CLEARANCE_M
    seed = berth_seed(surf, site)
    ux, uz = A._unit(s["bearing"])
    lx, lz = uz, -ux
    if s["kind"] == "slip":
        offs = [0.0]                      # inside the fingers; that is the point
        alongs = [0.0, 2.0, 4.0, -2.0, 6.0, -4.0, 8.0]
    else:
        deck_half = s.get("width_tiles", 2) * 2.0 / 2.0
        fender = deck_half + v["hull_w"] / 2.0 + 1.0
        offs = [0.0]
        for d in (0.0, 2.0, 4.0, 6.0, 8.0):
            offs += [fender + d, -(fender + d)]
        alongs = [0.0, 3.0, 6.0, 9.0, 12.0, -3.0, -6.0, 15.0, 18.0]
    best = None
    swept = 0
    for along in alongs:
        for off in offs:
            bx = seed["x"] + ux * along + lx * off
            bz = seed["z"] + uz * along + lz * off
            got = hull_depths(surf, s["bearing"], bx, bz, vessel)
            if got is None:
                continue
            swept += 1
            mn, mx, conn, nsamp, _fld = got
            floats = bool(conn and mn >= need)
            # floats first, then NEAREST to the seed, then deepest as a
            # tie-break. A boat berthed is a boat against the quay.
            key = (floats, -math.hypot(along, off), round(mn, 3))
            if best is None or key > best[0]:
                best = (key, dict(x=round(float(bx), 2), z=round(float(bz), 2),
                                  along_from_seed_m=round(along, 2),
                                  offset_from_centreline_m=round(off, 2),
                                  min_depth_under_hull_m=round(mn, 3),
                                  max_depth_under_hull_m=round(mx, 3),
                                  hull_samples=nsamp,
                                  connected_to_open_sea=conn, floats=floats))
    if best is None:
        raise SystemExit(f"{site}: no berth candidate is inside a 1 m patch; "
                         f"widen the PatchScan request rather than guessing")
    out = dict(site=site, vessel=vessel, yaw=s["bearing"], seed=seed,
               required_depth_m=round(need, 2),
               draught_m=v["draught_m"], hull_w_m=v["hull_w"],
               hull_l_m=v["hull_l"], candidates_swept=swept, **best[1])
    out["disc"] = berth(surf, out["x"], out["z"], vessel,
                        radius_m=max(12.0, v["hull_l"] / 2.0))
    out["why"] = ("MEASURED: seeded on the planned berth or the deepest-water "
                  "planned deck tile, then swept along and across the pier. Each "
                  "candidate is scored on the SHALLOWEST sample inside the "
                  "hull's own elliptical footprint, with EVERY hull sample "
                  "required to lie in water that is flood-fill connected to the "
                  "open sea. Ranked floats-first then nearest-to-seed.")
    out["tool"] = "tools/jumpstart/settlements/waterfront.py::berth_spot"
    return out


def pick_vessel(surf: Surface, site: str) -> dict:
    """The largest vessel that this berth PROVES it can float, with the
    rejections listed so a narrowing is never silent."""
    s = SITES[site]
    beam_cap = s.get("inner_width_m")
    tried, chosen = [], None
    order = sorted(VESSELS.items(), key=lambda kv: -kv[1]["draught_m"])
    for name, v in order:
        if beam_cap is not None and v["hull_w"] > beam_cap - 1.0:
            tried.append(dict(vessel=name, rejected="beam",
                              hull_w_m=v["hull_w"], beam_cap_m=beam_cap))
            continue
        if beam_cap is not None and v["hull_l"] > s["length_m"]:
            tried.append(dict(vessel=name, rejected="length",
                              hull_l_m=v["hull_l"], slip_m=s["length_m"]))
            continue
        spot = berth_spot(surf, site, name)
        if spot["floats"]:
            chosen = spot
            break
        tried.append(dict(vessel=name, rejected="depth",
                          min_depth_under_hull_m=spot["min_depth_under_hull_m"],
                          required_depth_m=spot["required_depth_m"]))
    return dict(site=site, chosen=chosen, rejected=tried,
                rule=("largest by draught that the hull footprint proves it can "
                      "float, and that fits the slip's inner width less 1 m of "
                      "fender and its length. A large boathouse berthing a "
                      "rowboat is the same defect as a slip that cannot float a "
                      "boat."))


def emit(builder, ops: list[dict], *, label: str = "") -> list[dict]:
    """Send ops through the ledger, one at a time, printing as it goes.

    `spawn_plan` stores its plan text as a blob FIRST, built from the same wire
    lines that were hashed, because the ledger refuses an op whose blob it
    cannot read and will not guess a substitute for a replay.
    """
    out = []
    for op in ops:
        if op["op"] == "spawn_plan":
            text = "\n".join(op["wire"]) + "\n"
            sha = builder.blob(text.encode(),
                               note=f"{label} spawn_object plan, "
                                    f"{len(op['wire'])} commands")
            want = op["params"]["plan_sha256"]
            if sha != want:
                raise SystemExit(
                    f"{label}: stored blob {sha} != declared plan_sha256 {want}; "
                    f"the plan bytes and the digest disagree and a replay would "
                    f"build something else")
        res = builder.emit(op["op"], params=op["params"], wire=op.get("wire"),
                           requires=op.get("requires"), expect=op.get("expect"),
                           meta=op.get("meta"))
        print(f"    {op['op']:12s} {str(op.get('params', {}).get('prefab') or '') :22s}"
              f" seq={res.get('seq')} {res.get('status')}", flush=True)
        out.append(res)
    return out


def save_op(pos: tuple[float, float], prefab: str, count: int,
            radius: float, role: str) -> dict:
    """A `save` WITH a postcondition that is a real measurement of the pass.

    A bare `save` proves the command was accepted, not that the thing this pass
    built is in the world.  So the postcondition counts a prefab this pass
    placed, at the place it placed it.
    """
    return dict(op="save", params=dict(role=role),
                wire=["save"],
                requires=dict(mods=[], prefabs=[], blobs=[]),
                expect=dict(prefab_count=[dict(
                    prefab=prefab, pos=[round(pos[0], 2), round(pos[1], 2)],
                    max=radius, count=count, tolerance=0)]),
                meta=dict(why=("the world is saved WITH a postcondition that "
                               "re-measures this pass's own work, because a save "
                               "that returns cleanly proves the command ran and "
                               "not that the artefact exists")))


def substitute_boat(surf: Surface, site: str, ops: list[dict]) -> list[dict]:
    """Replace a planned vanilla boat with the largest vessel this berth PROVES
    it floats, and record the substitution.

    The planned records were written before `Marlthon-OdinShip` was installed,
    so they name `VikingShip` everywhere.  Berthing a Longship where a
    `CargoShip` fits wastes the berth; berthing a `CargoShip` where only 1.9 m
    of water exists is the defect this whole module is about.  So the berth
    decides and the log says which vessel it rejected and why.
    """
    pick = pick_vessel(surf, site)
    out = []
    for op in ops:
        p = op.get("params") or {}
        if op["op"] == "spawn" and p.get("role") == "boat_spawn_moor":
            if pick["chosen"] is None:
                print(f"    !! no vessel floats {site}'s berth; boat op DROPPED "
                      f"and the rejections are recorded", flush=True)
                continue
            c = pick["chosen"]
            nb = boat_op(site, c["vessel"], c["x"], c["z"], c["yaw"], c)
            nb["meta"]["replaces_planned"] = dict(
                prefab=p.get("prefab"), pos=p.get("pos"),
                why=("the planned record predates Marlthon-OdinShip; the vessel "
                     "is now chosen by the measured berth"))
            nb["meta"]["rejected"] = pick["rejected"]
            out.append(nb)
            continue
        out.append(op)
    return out


def cmd_complete(args) -> int:
    """Emit the planned ops for a site that are NOT already in the world.

    The already-built test is taken from the LIVE LEDGER and then CONFIRMED
    against a live census, because an existence check is never a completeness
    check: `boathouse-temple-strait` has three records and 97 pieces and is
    still missing its sign and its boat.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    surf = Surface()
    site = args.site
    ops = planned_ops(site)
    done = already_emitted(site)
    todo = [o for o in ops if op_key(o) not in done]
    print(f"=== {site}: {len(ops)} planned ops, {len(done)} already in the "
          f"ledger, {len(todo)} to emit")
    for o in ops:
        mark = "SKIP" if op_key(o) not in [op_key(t) for t in todo] else "EMIT"
        p = o.get("params") or {}
        print(f"  {mark} {o['op']:14s} {p.get('prefab') or p.get('plan_ref') or ''}")
    todo = substitute_boat(surf, site, todo)
    if not todo:
        print("nothing to do")
        return 0
    if not args.apply:
        print(json.dumps(dict(site=site, would_emit=[o["op"] for o in todo],
                              vessel=pick_vessel(surf, site)), indent=1,
                         default=float))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note(args.note or
               f"{site}: completing a structure that was planned and verified by "
               f"Crossings and left incomplete. Emitting ONLY the planned ops "
               f"whose (op, prefab, position) key is absent from the live "
               f"ledger; the deck itself is not re-sent, because a spawn_plan "
               f"re-emit would double every piece and its own postcondition "
               f"would then fail.", role=SITE_ROLE[site], site_id=site)
        emit(b, todo, label=site)
        last = [o for o in todo if o["op"] in ("spawn", "portal")][-1]
        lp = last["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1,
                         max(3.0, lp.get("guard_radius_m", 0.5) * 2.0),
                         SITE_ROLE[site])], label=f"{site} save")
        print(json.dumps(b.close(), indent=1))
    return 0


def cmd_stair(args) -> int:
    """Bridge a MEASURED road/deck step with a stair run, not a terrain cut."""
    from ledger.live import LiveBuilder  # noqa: PLC0415
    surf = Surface()
    site = args.site
    s = SITES[site]
    if not s.get("stair"):
        raise SystemExit(f"{site} has no MEASURED step recorded; measure one "
                         f"before inventing a stair for it")
    run = stair_run(surf, s["stair"]["foot"], s["stair"]["bearing"],
                    s["deck_top_y"])
    print(json.dumps(run, indent=1))
    if not run["steps"]:
        print("no step to bridge: the ground at the deck edge is already at or "
              "below the deck top")
        return 0
    if not run["closed"] or run["residual_m"] < -0.35:
        raise SystemExit(
            f"the run does not close: {run['residual_m']:.3f} m of ground still "
            f"stands ABOVE the top tread, so the step would remain. Widen "
            f"max_steps or move the foot; do NOT accept this.")
    ops = [spawn_op(site, f"{site}/stair{st['index']}", st["prefab"],
                    st["x"], st["y"], st["z"], st["yaw"],
                    reason=ON_DECK,
                    meta=dict(step=st, run_summary={
                        k: run[k] for k in (
                            "residual_m", "rise_needed_m", "steps_laid",
                            "top_ground_y", "top_xz", "max_burial_m",
                            "bottom_tread_surface_y", "closed")},
                        why=("the join between T13's carriageway and the 30.6 "
                             "deck datum steps up; a STRUCTURE fixes it because "
                             "a cut here works in 60 cm of headroom beside open "
                             "water and draining a pier is unrepairable")))
           for st in run["steps"]]
    if not args.apply:
        print(json.dumps(dict(would_emit=[o["wire"][0] for o in ops]), indent=1))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.observe(f"road_deck_step::{site}",
                  "MEASURED: composed applied surface (lattice-aligned PatchScan "
                  "generated height + the ledger's own terrain_write blobs "
                  "unioned in FILE ORDER, bilinear) sampled per tread",
                  "tools/jumpstart/settlements/waterfront.py::stair_run",
                  run, units="m")
        emit(b, ops, label=f"{site} stair")
        top = run["steps"][-1]
        emit(b, [save_op((top["x"], top["z"]), STAIR["prefab"],
                         len(run["steps"]),
                         round(STAIR["run_m"] * len(run["steps"]) + 1.0, 1),
                         SITE_ROLE[site])], label=f"{site} save")
        print(json.dumps(b.close(), indent=1))
    return 0


def cmd_audit(args) -> int:
    import replay as R  # noqa: PLC0415
    s = SITES[args.site]
    hx, hz = s.get("head") or (s["root"][0], s["root"][1])
    x0, x1 = sorted((s["root"][0], hx))
    z0, z1 = sorted((s["root"][1], hz))
    with R.Server(dry=False) as srv:
        srv.probe()
        rep = audit(srv, x0 - 8, x1 + 8, z0 - 8, z1 + 8)
    print(json.dumps({k: v for k, v in rep.items() if k != "objects"}, indent=1))
    return 0


# The fleet's ONE lamp convention, handed over by `Lampposts` and used verbatim
# rather than invented a second time -- two lighting conventions is how this
# project ended up with two clearance instruments.
#   pivot: the prefab's mesh AND collider span local y -0.65359..+0.82260, so
#   the pivot sits 0.654 m up the shaft and a torch seated at the surface floats.
#   data:  three ZDO INTs. ZNetView::Awake -> LoadFields() gates on "HasFields",
#   then "HasFields<TypeName>", then reads "<TypeName>.<fieldName>"; a bool
#   comes through ZDO.GetBool which is GetInt != 0, hence INTs and not a bool
#   section. Without it the prefab has m_infiniteFuel=false, m_startFuel=2 and
#   m_secPerFuel=20000 -- about 11 hours and then it wants resin, forever.
LAMP = dict(prefab="piece_groundtorch", pivot_above_surface=0.65359,
            ints={"HasFields": 1, "HasFieldsFireplace": 1,
                  "Fireplace.m_infiniteFuel": 1},
            measured_by="Lampposts, tools/jumpstart/roads/LampProbe.cs")


# MEASURED in the same ZNetScene dump, and it CORRECTS a warning I was given:
# `piece_groundtorch` has `Piece.m_notOnWood = 0`, NOT true. So placing it on a
# wood deck steps over no build-menu rule at all. Its solid collider is
# 0.233 x 0.237 m, so it fits inside one 2 x 2 m deck tile with room to spare,
# and it carries WearNTear material Wood -- which `AzuWearNTearPatches`
# (installed, weather damage off, server-synced and admin-locked) makes moot.
MATROWS_NOT_ON_WOOD = False


def lamp_blob() -> str:
    e = DE.DataEntry()
    for k, v in LAMP["ints"].items():
        e.ints[DE.stable_hash(k)] = v
    return e.encode()


# The portal hall's seat ring, FITTED to the seats already on its floor rather
# than read off a constant: least-squares circle over the 18 `SeatCheck` portal
# records gives centre (-279.415, 215.646) and R 13.04, every seat at y 37.1 and
# facing the centre with yaw = ring_angle - 180. MEASURED gap: 248.2 deg to
# 294.6 deg is empty, i.e. the 270 deg slot is free.
HUB_RING = dict(cx=-279.415, cz=215.646, r=13.375, y=37.1,
                free_slots_deg=[270.0])


def hub_seat(angle_deg: float) -> dict:
    a = math.radians(angle_deg)
    return dict(x=round(HUB_RING["cx"] + HUB_RING["r"] * math.sin(a), 2),
                y=HUB_RING["y"],
                z=round(HUB_RING["cz"] + HUB_RING["r"] * math.cos(a), 2),
                yaw=(angle_deg - 180.0) % 360.0, angle_deg=angle_deg)


def hub_portal_op(site: str, tag: str, angle_deg: float) -> dict:
    seat = hub_seat(angle_deg)
    blob = portal_blob(tag)
    cmd = (f"spawn_object portal_wood pos={A.fmt(seat['z'])},{A.fmt(seat['x'])},"
           f"{A.fmt(seat['y'])} rot={A.fmt(seat['yaw'])},0,0 from=0,0,0"
           f" data={blob}")
    return dict(
        op="portal",
        params=dict(prefab="portal_wood",
                    pos=[seat["x"], seat["y"], seat["z"]],
                    yaw_deg=seat["yaw"], tag=tag, pair_tag=tag,
                    guard_radius_m=0.5, zdo_strings=dict(tag=tag),
                    data_b64=blob,
                    role="spawn_portal", site_id=site,
                    flatten="FORBIDDEN",
                    flatten_reason=("the hall floor is a built pad at y 37.1; a "
                                    "terrain_write under it would drop the floor "
                                    "the ring stands on")),
        wire=[cmd],
        requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                      prefabs=["portal_wood"], blobs=[]),
        expect=dict(tag_readback=dict(tag=tag, pos=[seat["x"], seat["z"]],
                                      max=3.0)),
        meta=dict(end="hub", seat=seat, ring=HUB_RING,
                  why=("pairing is EXACT STRING EQUALITY with a uniform random "
                       "draw among equally-tagged unconnected portals, so a tag "
                       "with ONE end pairs with nothing and the site end would "
                       "be a one-way trip. The hub end is placed in the SAME "
                       "pass as the site end so the tag never exists with one "
                       "end. Seat angle 270 deg is the measured gap in the "
                       "ring.")))


def cmd_fabric(args) -> int:
    """Finish the fabric of a standing structure: rails, light, berthed vessel.

    Rails are derived from the posts that are ACTUALLY STANDING, censused live,
    not from the plan: 13 `wood_pole` rail posts at harbour-temple-south with
    nothing between them is a half-built object that reads as broken from 40 m,
    and the pairs are whichever posts measure 1.6-2.6 m apart at the same
    height, so a gap in the census cannot produce a rail spanning thin air.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    import replay as R  # noqa: PLC0415
    surf = Surface()
    site = args.site
    s = SITES[site]
    hx, hz = s.get("head") or s["root"]
    with R.Server(dry=False) as srv:
        srv.probe()
        rep = audit(srv, min(s["root"][0], hx) - 8, max(s["root"][0], hx) + 8,
                    min(s["root"][1], hz) - 8, max(s["root"][1], hz) + 8)
    posts = [o for o in rep["objects"] if o["prefab"] == s.get("rail_from")]
    print(f"live census: {rep['total']} objects, {len(posts)} "
          f"{s.get('rail_from')} rail posts, {rep['socket_calls']} socket calls")
    ops: list[dict] = []
    pairs = []
    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            a, b = posts[i], posts[j]
            d = math.hypot(a["x"] - b["x"], a["z"] - b["z"])
            if 1.6 <= d <= 2.6 and abs(a["y"] - b["y"]) < 0.1:
                pairs.append((a, b, d))
    for a, b, d in pairs:
        mx, mz = (a["x"] + b["x"]) / 2.0, (a["z"] + b["z"]) / 2.0
        # wood_beam is 2.0 m along its LOCAL x with the origin centred, so the
        # yaw that lays it along the pair is the pair's bearing minus 90.
        yaw = (math.degrees(math.atan2(b["x"] - a["x"], b["z"] - a["z"])) - 90.0) % 360.0
        # post is 1.0 m tall, origin centred, so its top is y + 0.5; the beam is
        # 0.4 m deep, origin centred, so y = top - 0.2 puts its top face flush.
        top = a["y"] + 0.5
        ops.append(spawn_op(site, f"{site}/rail", "wood_beam",
                            mx, top - 0.2, mz, yaw, reason=ON_DECK,
                            meta=dict(spans=[[a["x"], a["z"]], [b["x"], b["z"]]],
                                      span_m=round(d, 3), post_top_y=round(top, 3),
                                      why=("rails the posts that are standing; "
                                           "pairs taken from the LIVE census so "
                                           "no rail spans a post that is not "
                                           "there"))))
    # lights: on the deck, at the two ends of the rail line, on the fleet's lamp
    lamp_at = []
    if posts:
        ends = sorted(posts, key=lambda o: o["x"])
        lamp_at = [ends[0], ends[-1]]
    for o in lamp_at:
        # the post is 1.0 m tall with its origin centred, so its FOOT is the
        # surface it was seated on -- the deck top. Adding FLOOR_TOP_OFFSET here
        # double-counted the floor and floated the lamp 0.097 m.
        g = o["y"] - 0.5
        ops.append(spawn_op(
            site, f"{site}/lamp", LAMP["prefab"], o["x"], g + LAMP["pivot_above_surface"],
            o["z"], 0.0, reason=ON_DECK, blob=lamp_blob(),
            strings={},
            meta=dict(seated_on_deck_top_y=round(g, 3),
                      pivot_above_surface_m=LAMP["pivot_above_surface"],
                      infinite_fuel_ints=LAMP["ints"],
                      convention_from=LAMP["measured_by"],
                      why=("ONE lamp convention for the whole world; the ZDO int "
                           "payload is what makes it everburning in vanilla, and "
                           "without it the torch wants resin every 11 hours"))))
    pick = pick_vessel(surf, site)
    if pick["chosen"] and not args.no_boat:
        c = pick["chosen"]
        ops.append(boat_op(site, c["vessel"], c["x"], c["z"], c["yaw"], c))
        ops[-1]["meta"]["rejected"] = pick["rejected"]
    if not args.apply:
        print(json.dumps(dict(pairs=len(pairs), lamps=len(lamp_at),
                              vessel=(pick["chosen"] or {}).get("vessel"),
                              wires=[o["wire"][0][:110] for o in ops]), indent=1))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note(f"{site}: finishing the fabric. Rails between the "
               f"{len(posts)} rail posts that a LIVE census found standing, the "
               f"fleet's everburning lamp at both ends of the deck, and the "
               f"largest vessel the berth PROVES it floats. No terrain is "
               f"written: the deck datum is 30.6 against a water plane at 30.0, "
               f"so there are 60 cm of headroom beside open water here and a cut "
               f"is the early-dock defect.", role=SITE_ROLE[site], site_id=site)
        b.observe(f"berth::{site}",
                  "MEASURED: shallowest sample inside the hull's elliptical solid-"
                  "collider footprint, every hull sample required to be in water "
                  "flood-fill connected to the open sea",
                  "tools/jumpstart/settlements/waterfront.py::berth_spot",
                  pick, units="m")
        emit(b, ops, label=f"{site} fabric")
        lp = ops[-1]["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1, 12.0,
                         SITE_ROLE[site])], label=f"{site} save")
        print(json.dumps(b.close(), indent=1))
    return 0

# ---------------------------------------------------------------------------
# NEW waterfront structures
# ---------------------------------------------------------------------------
#
# The qualifying measurement is in `why` for every one of them, because the
# operator asked for boathouses "at the key map locations where it makes sense
# to travel to another area" and that is a MEASURABLE claim, not a taste one.
EXTRA_FIELD = "/tmp/harbour/extra.bin"

NEW_SITES: dict[str, dict] = {
    "dock-dockshore": dict(
        kind="jetty", role="dock_place",
        near=(675.4, -85.6), bearing_hint=130.0, length_m=40.0,
        width_tiles=3,
        sign_text="DOCK - Dockshore. Temple 762 m WNW, Wolf Town 826 m N.",
        portal_tag=None,
        why=("THE BEST-JUSTIFIED NEW WATERFRONT ON THIS WORLD, and the test is "
             "road access rather than water: TWO built trunk roads terminate "
             "here and there is nothing at the end of either. T5-temple-"
             "dockshore and T9-wttown-dockshore both end at the `dockshore` "
             "node (675.4, -85.6), whose own spec says 'early-dock / sandbox-"
             "harbour solved to water_dist_m 0.0. The ROAD stops on the "
             "shoreline; the pier is Crossings'' over-water structure' -- and "
             "Crossings never built one. MEASURED at 8 m: water fraction 0.820 "
             "in a 35 m disc, the HIGHEST of all 14 key places on this world; "
             "max depth 7.05 m; shelter 0.542. "
             "WHY 40 m AND NOT 24: crossings/plan.py::aim_seaward REFUSED a "
             "24 m pier here at EVERY one of 72 bearings and at every head "
             "depth from 2.2 to 3.0 m -- the shelf is shallow and a short pier "
             "cannot reach water a boat floats in. Swept by length, 40 m is the "
             "shortest that the aimer accepts: root (691,-94), bearing 130, wet "
             "fraction 0.512 under the deck, head depth 4.33 m. 48 m would "
             "reach 6.05 m and is not needed, because 4.33 m already clears the "
             "deepest-draught vessel in the world (CargoShip, 2.0 m) by 1.33 m. "
             "The refusal is the measurement: a 24 m boathouse here would have "
             "been scenery."),
    ),
    "harbour-stenvik-port": dict(
        kind="jetty", role="harbour_place",
        near=(470.0, 1045.0), bearing_hint=355.0, length_m=24.0,
        width_tiles=3,
        sign_text="STENVIK PORT - the town is 165 m SE. Deep berth, 5.0 m.",
        portal_tag="x-port",
        why=("THE LARGE HARBOUR PORT, and the operator's own test decided it: "
             "'is there a sheltered body of water big enough AND a town "
             "adjacent'. Two towns exist on this world. MEASURED over all 14 "
             "key places, this inlet has the HIGHEST SHELTER of any of them -- "
             "0.792 at (484,1072) on 24 rays at 70 m -- and it serves THREE "
             "installations at once: Stenvik town (496,904) at 144 m, the "
             "station hub (661.5,1088.5) at 177 m and Hognest castle "
             "(530,1348) at 276 m. Vestvik, the only other town, measures "
             "shelter 0.500, so it gets a quay and not a port. "
             "At 1 m the site is better than the 8 m scan suggested: root "
             "(476,1045), bearing 355, WET FRACTION 0.96 under a 24 m deck and "
             "5.03 m at the head -- the deepest-draught vessel in the world "
             "(CargoShip, 2.0 m) clears by 2.03 m. "
             "The 35.5 ha body at (1086,1110) was REJECTED as a port site by "
             "the same instrument: it is an INLAND LAKE, not 4-connected to "
             "the patch boundary, so nothing can sail out of it."),
    ),
    "dock-northcape": dict(
        kind="jetty", role="dock_place",
        near=(-186.0, 2582.0), bearing_hint=60.0, length_m=48.0,
        width_tiles=3, search_r=48.0,
        sign_text="NORTH DOCK - the northernmost quay on the continent.",
        portal_tag="x-north",
        why=("THE NORTH DOCK the operator asked for, at the northernmost point "
             "of the ROAD-CONNECTED continent that can actually take a pier. "
             "MEASURED: the absolute northernmost land of the two bridged "
             "halves is comp278's tip at (-292,2720), a ONE-CELL spit whose "
             "nearest sea-connected water at or over 2.5 m is 112 m away with "
             "shelter 0.417 -- exposed, and unreachable by any pier. The "
             "surveyed sector-0 extremity `w-north` (-186,2582) measures "
             "shelter 0.750 with sea at 53.7 m, and at 1 m the aimer accepts a "
             "48 m pier from root (-192,2612) at bearing 60: wet fraction "
             "0.816, head depth 3.25 m. 3.25 m clears a CargoShip's 2.0 m "
             "draught by 1.25 m, so the large OdinShip vessel the operator "
             "asked for at the north dock floats -- but only just, and the "
             "20/24/28/32/40 m lengths were ALL REFUSED, so this pier cannot "
             "be shortened. It is portal-only: the west isle's built road "
             "network reaches w-south and treenear, and w-north is ~2.8 km "
             "from either."),
    ),
}


def cmd_newbuild(args) -> int:
    """Plan and build a NEW slip or jetty, measuring every gate before it writes.

    Everything structural is `crossings/assemble.py` and every gate is
    `crossings/plan.py`'s, used as libraries: the aimer that measures which way
    is out to sea, the game's own WearNTear support algorithm, the per-type POI
    clearance instrument, and the clear-radius limit that is the only thing
    standing between an `objects_remove` and the one damage class this project
    records as unrepairable.  Nothing here re-implements a check that exists.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    import ledger_ops as LO  # noqa: PLC0415  crossings/
    spec = NEW_SITES[args.site]
    sid = args.site
    fields = W.load(EXTRA_FIELD) | W.load(FIELD)
    fld = None
    for f in fields.values():
        if f.step <= 1.0 and f.contains(spec["near"][0], spec["near"][1], 60.0):
            if fld is None or f.half < fld.half:
                fld = f
    if fld is None:
        raise SystemExit(f"{sid}: no 1 m patch covers {spec['near']} with 60 m "
                         f"of margin; extend the PatchScan request")
    # 1. WHICH WAY IS OUT TO SEA -- measured, swept at 5 deg, scored on what the
    #    deck would actually stand over. A hand-picked bearing once put a 20 m
    #    jetty with 0 of 22 tiles over water and its head 4.4 m up a hillside.
    aim = CP.aim_seaward(fld, spec["near"], spec["length_m"],
                         prefer_bearing=spec.get("bearing_hint"),
                         search_r=spec.get("search_r", 24.0))
    root = (aim["root"][0], aim["root"][1])
    bearing = aim["bearing"]
    ground, bed = CP.profiles(fld)
    print(json.dumps(dict(aim=aim), indent=1))
    # 2. GEOMETRY
    if spec["kind"] == "slip":
        pieces = A.slip(root, bearing, spec["length_m"], spec["inner_width_m"],
                        A.DECK_Y, bed, name=sid)
    else:
        pieces = A.jetty(root, bearing, spec["length_m"], A.DECK_Y, bed,
                         width_tiles=spec.get("width_tiles", 2), name=sid)
    verdict = A.verify(pieces, ground, note=sid)
    if not verdict["ok"]:
        raise SystemExit(f"{sid}: the game's own support algorithm REFUSES this "
                         f"assembly: {verdict['failures'][:4]}")
    fx_spec = dict(id=sid, root=root, bearing=bearing,
                   length_m=spec["length_m"], kind=spec["kind"],
                   sign_text=spec.get("sign_text"),
                   portal_tag=spec.get("portal_tag"),
                   width_tiles=spec.get("width_tiles", 2),
                   inner_width_m=spec.get("inner_width_m"))
    fixt = CP.fixtures_for(fld, fx_spec)
    fcmds = CP.fixture_commands(fld, fx_spec, fixt)
    poi = CP.poi_check(pieces + fixt, half_width_m=spec.get("width_tiles", 2))
    decks = [p for p in pieces if p.role.endswith("/deck")
             or p.role.endswith("/head")]
    wet = [p for p in decks if ground(p.x, p.z) < W.WATER_LEVEL]
    measured = dict(
        deck_tiles=len(decks), deck_tiles_over_water=len(wet),
        deck_over_water_fraction=round(len(wet) / max(1, len(decks)), 3),
        root_ground_y=round(ground(*root), 3),
        head_depth_m=aim["head_depth_m"], wet_fraction=aim["wet_fraction"],
        deck_top_y=round(A.DECK_Y, 3),
        deck_clears_water_by_m=round(A.DECK_Y - W.WATER_LEVEL, 3))
    print(json.dumps(dict(pieces=len(pieces), verdict_ok=verdict["ok"],
                          measured=measured,
                          poi=json.loads(json.dumps(poi, default=float))),
                     indent=1))
    if poi.get("verdict") not in (None, "CLEAR", "clear", True):
        print(f"    !! POI gate verdict {poi.get('verdict')!r}")
    rec = dict(
        id=sid, role=spec["role"], kind=spec["kind"], why=spec["why"],
        flatten_reason=OVER_WATER,
        prepare=CP.prepare_block(fld, pieces + fixt, sid),
        structural=verdict, measured=measured, continuity=None,
        poi_check=poi, boats=[],
        fixtures=dict(sign_text=spec.get("sign_text"),
                      portal_tag=spec.get("portal_tag"),
                      portal_pair_end_owner=None,
                      pieces=[dict(prefab=p.prefab, role=p.role,
                                   xz=[round(p.x, 2), round(p.z, 2)],
                                   y=round(p.y, 3), yaw=p.yaw) for p in fixt]),
        zdo_cost=len(pieces) + len(fixt))
    cmds = [A.command(p) for p in pieces] + fcmds
    outdir = HERE / "out"
    outdir.mkdir(exist_ok=True)
    plan_ref = f"settlements/out/{sid}.commands.txt"
    (outdir / f"{sid}.commands.txt").write_text("\n".join(cmds) + "\n")
    ops = LO.ops_for(rec, cmds,
                     [dict(xz=[p.x, p.z]) for p in pieces + fixt],
                     fixture_cmds={p.role: c for p, c in zip(fixt, fcmds)},
                     plan_ref=plan_ref, pair_ends={})
    # The vessel is chosen AFTER the geometry exists, from the geometry.
    ux0, uz0 = A._unit(bearing)
    wet_tiles = [(p.x, p.z) for p in decks if ground(p.x, p.z) < W.WATER_LEVEL]
    head = max(wet_tiles, key=lambda t: t[0] * ux0 + t[1] * uz0)
    SITES[sid] = dict(kind=spec["kind"], root=root, bearing=bearing,
                      length_m=spec["length_m"],
                      inner_width_m=spec.get("inner_width_m"),
                      width_tiles=spec.get("width_tiles", 2),
                      deck_top_y=A.DECK_Y, why=spec["why"],
                      berth_seed_xz=(round(head[0] + ux0 * 6.0, 2),
                                     round(head[1] + uz0 * 6.0, 2)),
                      berth_seed_why=(
                          f"deck tile farthest along bearing {bearing:.0f} that "
                          f"stands over water ({head[0]:.2f},{head[1]:.2f}), "
                          f"depth {W.WATER_LEVEL - ground(*head):.3f} m, pushed "
                          f"6 m to seaward"))
    surf = Surface()
    pick = None
    try:
        pick = pick_vessel(surf, sid)
    except SystemExit as exc:
        print(f"    berth not measurable on the working field: {exc}")
    if pick and pick["chosen"]:
        c = pick["chosen"]
        ops.append(boat_op(sid, c["vessel"], c["x"], c["z"], c["yaw"], c))
        ops[-1]["meta"]["rejected"] = pick["rejected"]
        print(f"    vessel: {c['vessel']} at ({c['x']},{c['z']}), "
              f"{c['min_depth_under_hull_m']} m under the hull "
              f"(needs {c['required_depth_m']})")
        for r in pick["rejected"]:
            print("      rejected:", r)
    elif pick:
        print("    NO VESSEL FLOATS THIS SLIP -- it would be scenery. "
              "Rejections:")
        for r in pick["rejected"]:
            print("      ", r)
    if not args.apply:
        print(json.dumps(dict(ops=[o["op"] for o in ops]), indent=1))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note(f"{sid}: a NEW waterfront structure. {spec['why']}",
               role=spec["role"], site_id=sid)
        b.observe(f"waterfront_site::{sid}",
                  "MEASURED on a 1 m PatchScan field (rivers included): bearing "
                  "swept at 5 deg by crossings/plan.py::aim_seaward and scored on "
                  "wet fraction under the deck and head depth; structure verified "
                  "with the game's own WearNTear support algorithm; POI clearance "
                  "by settlements/clearance.py per type",
                  "tools/jumpstart/settlements/waterfront.py::cmd_newbuild",
                  dict(aim=aim, measured=measured,
                       structural_ok=verdict["ok"],
                       poi=json.loads(json.dumps(poi, default=float)),
                       vessel=json.loads(json.dumps(pick, default=float))))
        emit(b, ops, label=sid)
        lp = ops[-1]["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1, 12.0,
                         spec["role"])], label=f"{sid} save")
        print(json.dumps(b.close(), indent=1))
    return 0


def cmd_probe(args) -> int:
    """DELIBERATE EXISTENCE PROBES against ZNetScene, through the ledger.

    A prefab that is not in `ZNetScene.m_prefabs` makes `spawn_object` place
    NOTHING and say nothing about it, so the only live oracle for "does this
    prefab exist" is an op with a `prefab_count` postcondition: it passes if the
    prefab exists and FAILS if it does not, and the failure is the measurement.
    Predicting the answer is not measuring it.

    Recorded as a probe so the next reader sees an experiment rather than a
    mistake, and each probe gets its OWN builder session because a failed
    postcondition raises and would abandon the rest.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    surf = Surface()
    x, z = args.probe_xz
    g = surf.at(x, z)[0]
    results = []
    for i, prefab in enumerate(args.prefabs):
        px = x + i * 24.0
        op = spawn_op("probe-znetscene", PROBE_ROLE, prefab,
                      px, g, z, 0.0,
                      reason=("a probe, not a structure; nothing here is load "
                              "bearing and no ground is written"),
                      guard=4.0,
                      meta=dict(probe=True,
                                question=("is this prefab in ZNetScene, i.e. "
                                          "will spawn_object actually place it"),
                                predicted=args.predict or "unstated",
                                why=("a prefab absent from ZNetScene makes "
                                     "spawn_object a silent no-op, so a "
                                     "prefab_count postcondition is the only "
                                     "live oracle. A FAILED postcondition here "
                                     "is the measurement and the record is "
                                     "deliberate.")))
        if not args.apply:
            print(f"would probe {prefab} at ({px:.1f},{g:.2f},{z:.1f})")
            continue
        try:
            with LiveBuilder(actor=ACTOR) as b:
                res = emit(b, [op], label=f"probe {prefab}")
                results.append(dict(prefab=prefab, exists=True,
                                    seq=res[0]["seq"],
                                    pos=[round(px, 2), round(g, 2), round(z, 2)]))
        except Exception as exc:  # noqa: BLE001 -- the failure IS the result
            results.append(dict(prefab=prefab, exists=False,
                                error=str(exc)[:400]))
            print(f"  PROBE NEGATIVE for {prefab}: {str(exc)[:200]}")
    if results:
        print(json.dumps(results, indent=1))
    return 0

# ---------------------------------------------------------------------------
# the fortified port: walls, gates, towers
# ---------------------------------------------------------------------------
#
# THE KIT, and it is not the one the brief named.  `JamesJonesTV-Ravenwood-
# VikingHouses` 7.7.8 ships the `mm_*` fortification set in two asset bundles
# whose registration method `RavenwoodPiecesPlugin::AddRWPieces()` has a body of
# literally `ret` -- so NONE of the 113 `mm_*` prefabs reach ZNetScene and
# `spawn_object mm_large_wall` places nothing.  MEASURED two ways: a ZNetScene
# dump of 4,872 prefabs in a sandbox copy of this server with the mod staged
# contains ZERO `mm_`, and the IL of the stub is 1 byte.  The mod's THIRD
# bundle, `villagestuff`, IS registered through Jotunn and gives 29 `fb_*`
# prefabs, which is a COMPLETE fortification vocabulary -- straights, corners,
# cross- and T-junctions, ends, gateways, portcullises, towers and stairs.
#
# MEASURED geometry (solid non-trigger colliders, `mesh_render` excluded):
#   fb_fortress_wall            20.009 x  6.800 x 11.866, ymin  0.000
#   fb_fortress_wall_gateway    20.007 x  6.801 x 11.871, ymin -0.004
#   fb_fortress_wall_tower      20.009 x  9.639 x 15.402, ymin  0.000
#   fb_fortress_wall_corner     13.404 x 13.404 x 11.866, ymin  0.000
# The long axis is LOCAL X and the thickness is LOCAL Z, so a curtain laid
# tangentially to an arc takes yaw = the arc angle.  `ymin ~ 0` means the pivot
# IS the foot: seat it at the applied surface, no offset.
# Every one has `wnt=0` -- NO WearNTear -- so there is no support chain to
# satisfy and no decay; `AzuWearNTearPatches` is installed with weather damage
# off as well.
PORT_KIT = dict(
    wall="fb_fortress_wall", gateway="fb_fortress_wall_gateway",
    tower="fb_fortress_wall_tower", corner="fb_fortress_wall_corner",
    wall_len_m=20.009, wall_depth_m=6.8, wall_h_m=11.866, tower_h_m=15.402,
    pivot_is_foot=True,
    # THE ROOF A BEACON STANDS ON IS NOT THE TOWER'S HEIGHT, and using the
    # height would have put three lamps 5.17 m in the air. MEASURED per
    # collider on fb_fortress_wall_tower: the 15.402 m figure is the whole
    # mesh AABB; the FIGHTING PLATFORM is the box stack topping out at
    # y 10.229 (three boxes, 2.00 x 0.55 and 0.52 x 2.00, the parapet ring),
    # with a small 2 x 2 cap at 13.734-14.269 and two 0.4 x 0.4 posts between.
    # So a lamp seated on the platform takes ground + 10.229, and a lamp
    # seated on the cap would take ground + 14.269.
    tower_platform_y=10.229, tower_cap_y=14.269,
)


def port_curtain(surf: Surface, centre: tuple[float, float], radius_m: float,
                 gate_bearing_deg: float, *, arc_deg: float = 180.0,
                 pitch_m: float | None = None) -> list[dict]:
    """A curtain wall on the LANDWARD arc behind a quay, seated on the surface.

    Laid tangentially at the kit's own 20.009 m pitch, with a GATEWAY on the
    bearing the road arrives from and a TOWER at each end of the arc.  Every
    segment is refused unless its own centre stands on land: a curtain wall
    whose footing is under the water plane is the early-dock defect wearing a
    battlement, and no terrain is written to make room for it.
    """
    pitch = pitch_m or PORT_KIT["wall_len_m"]
    span = 2.0 * math.pi * radius_m * (arc_deg / 360.0)
    n = max(3, int(round(span / pitch)))
    out = []
    for i in range(n):
        frac = (i + 0.5) / n - 0.5
        ang = (gate_bearing_deg + frac * arc_deg) % 360.0
        a = math.radians(ang)
        x = centre[0] + radius_m * math.sin(a)
        z = centre[1] + radius_m * math.cos(a)
        g = surf.at(x, z)[0]
        kind = PORT_KIT["wall"]
        if i == 0 or i == n - 1:
            kind = PORT_KIT["tower"]
        elif abs(frac) < (0.5 / n):
            kind = PORT_KIT["gateway"]
        out.append(dict(prefab=kind, x=round(x, 2), y=round(g, 3),
                        z=round(z, 2), yaw=round(ang, 1),
                        ground_y=round(g, 3), angle_deg=round(ang, 1),
                        on_land=bool(g > W.LAND_LEVEL),
                        freeboard_above_water_m=round(g - W.WATER_LEVEL, 3)))
    return out


def cmd_port(args) -> int:
    """Fortify a standing quay: curtain wall, gateway, towers, beacon lamps."""
    from ledger.live import LiveBuilder  # noqa: PLC0415
    surf = Surface()
    site = args.site
    s = SITES[site]
    centre = s.get("port_centre") or s["root"]
    segs = port_curtain(surf, centre, args.radius, args.gate_bearing,
                        arc_deg=args.arc)
    for sg in segs:
        print(f"  {sg['prefab']:28s} ({sg['x']:8.2f},{sg['z']:9.2f}) yaw={sg['yaw']:6.1f} "
              f"ground={sg['ground_y']:7.3f} land={sg['on_land']} "
              f"freeboard={sg['freeboard_above_water_m']:+.3f}")
    wet = [sg for sg in segs if not sg["on_land"]]
    if wet:
        print(f"  !! {len(wet)} segment(s) do NOT stand on land and are DROPPED "
              f"rather than propped: a footing under the water plane is the "
              f"early-dock defect and no terrain is written to make room.")
    segs = [sg for sg in segs if sg["on_land"]]
    ops = []
    for i, sg in enumerate(segs):
        ops.append(spawn_op(site, f"{site}/curtain{i}", sg["prefab"],
                            sg["x"], sg["y"], sg["z"], sg["yaw"],
                            reason=("a fortification standing on LAND behind "
                                    "the quay; no ground is written and the "
                                    "kit has no WearNTear to support"),
                            guard=6.0,
                            meta=dict(segment=sg, kit=PORT_KIT,
                                      why=("fb_* is the Ravenwood bundle that "
                                           "IS registered; mm_* is an upstream "
                                           "stub and cannot be spawned at all"))))
    # a beacon on each tower, the fleet's lamp
    for i, sg in enumerate(segs):
        if sg["prefab"] != PORT_KIT["tower"]:
            continue
        ops.append(spawn_op(
            site, f"{site}/beacon{i}", LAMP["prefab"], sg["x"], sg["y"]
            + PORT_KIT["tower_platform_y"] + LAMP["pivot_above_surface"],
            sg["z"], 0.0,
            reason="a beacon on a tower roof; nothing is written into the ground",
            blob=lamp_blob(), strings={}, guard=2.0,
            meta=dict(on=sg["prefab"],
                      seated_on_platform_y=PORT_KIT["tower_platform_y"],
                      tower_mesh_h_m=PORT_KIT["tower_h_m"],
                      not_on_wood_measured=MATROWS_NOT_ON_WOOD,
                      infinite_fuel_ints=LAMP["ints"],
                      convention_from=LAMP["measured_by"],
                      why=("the operator asked for a lighthouse at a large "
                           "port; a 15.4 m tower carrying the fleet's "
                           "everburning lamp is a beacon that can be built "
                           "today. A true lighthouse BODY (drake-lighthouse."
                           "blueprint, 1,255 pieces) is Settlements' artefact "
                           "and is the next step, not a substitute for this."))))
    if not args.apply:
        print(json.dumps(dict(segments=len(segs),
                              wires=[o["wire"][0][:100] for o in ops]), indent=1))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note(f"{site}: fortifying the quay with the Ravenwood fb_* kit -- "
               f"curtain wall, gateway on the road bearing, a tower at each end "
               f"of the arc and an everburning beacon on each tower. The mm_* "
               f"kit named in the brief CANNOT be used: its registration method "
               f"AddRWPieces() has a 1-byte `ret` body and none of its 113 "
               f"prefabs reach ZNetScene, MEASURED in a 4,872-prefab dump and "
               f"confirmed in IL. Every segment stands on land above the water "
               f"plane and no terrain is written.",
               role="harbour_place", site_id=site)
        b.observe(f"port_curtain::{site}",
                  "MEASURED: each segment's footing height taken from the "
                  "composed applied surface at its own XZ; segments not on land "
                  "are dropped, not propped",
                  "tools/jumpstart/settlements/waterfront.py::port_curtain",
                  dict(segments=segs, kit=PORT_KIT, dropped=len(wet)))
        emit(b, ops, label=f"{site} port")
        lp = ops[0]["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1, 12.0,
                         "harbour_place")], label=f"{site} save")
        print(json.dumps(b.close(), indent=1))
    return 0


def cmd_measure(args) -> int:
    """The candidate report: which waterfront installations the geography
    justifies, each with the measurement that qualified it.  READ-ONLY."""
    surf = Surface()
    out = dict(
        tool="tools/jumpstart/settlements/waterfront.py::cmd_measure",
        field=FIELD, world_field=WORLD_FIELD,
        water_level_y=W.WATER_LEVEL, land_level_y=W.LAND_LEVEL,
        deck_datum_y=DECK_DATUM_Y,
        vessels=VESSELS, keel_clearance_m=KEEL_CLEARANCE_M,
        sites={},
    )
    for site in SITES:
        s = SITES[site]
        rec = dict(kind=s["kind"], root=list(s["root"]),
                   bearing_deg=s["bearing"], why=s["why"])
        rec["root_applied"] = [round(v, 3) for v in surf.at(*s["root"])]
        rec["vessel"] = pick_vessel(surf, site)
        if s.get("stair"):
            rec["stair_run"] = stair_run(surf, s["stair"]["foot"],
                                         s["stair"]["bearing"], s["deck_top_y"])
        out["sites"][site] = rec
    print(json.dumps(out, indent=1, default=float))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("measure", cmd_measure), ("audit", cmd_audit),
                     ("complete", cmd_complete), ("stair", cmd_stair),
                     ("fabric", cmd_fabric), ("newbuild", cmd_newbuild),
                     ("probe", cmd_probe), ("port", cmd_port)):
        p = sub.add_parser(name)
        p.add_argument("--site", default=None)
        p.add_argument("--prefabs", nargs="*", default=[])
        p.add_argument("--predict", default=None)
        p.add_argument("--radius", type=float, default=34.0)
        p.add_argument("--arc", type=float, default=180.0)
        p.add_argument("--gate-bearing", type=float, default=0.0)
        p.add_argument("--probe-xz", nargs=2, type=float,
                       default=[-60.0, -300.0],
                       help="where to place an existence probe; default is open "
                            "meadow south of the temple, clear of every pad")
        p.add_argument("--no-boat", action="store_true",
                       help="skip the vessel (it is already berthed)")
        p.add_argument("--hub-slot", type=float, default=None,
                       help="ring angle in degrees for a portal's HUB end")
        p.add_argument("--apply", action="store_true",
                       help="actually append and send; without it nothing is "
                            "written and nothing is sent")
        p.add_argument("--note", default=None)
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    if args.cmd not in ("measure", "probe") and not args.site:
        raise SystemExit("--site is required")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

