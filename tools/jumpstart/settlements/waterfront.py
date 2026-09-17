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
import hashlib
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

# WHO THE LEDGER SAYS DID IT.  `HarbourWork` MEASURED all of this and wrote
# nothing -- it never held the token and yielded with zero world writes --
# and `WaterfrontRun` is the agent that actually appends.  Crediting the
# measurer for the writer's records would put the wrong name in the only log
# that survives the session, which is the reason `build.py` grew `--actor`.
# The FIRST applied pass (boathouse-temple-strait, seq 2055-2059) went in under
# the old constant and is left alone: a ledger record is NEVER edited, and this
# comment plus the later records are the annotation.
ACTOR = "WaterfrontRun"
MEASURED_BY = "HarbourWork"

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

    def __init__(self, field: str = FIELD, actor: str = ACTOR,
                 extra: str | None = None):
        import applied as AP  # noqa: PLC0415  roads/, imported lazily
        # THE UNION IS NOT OPTIONAL, and this was MEASURED as a real refusal:
        # `newbuild --site dock-northcape` aimed its pier off `extra.bin` and
        # then could not measure its own berth, because the `north` patch
        # (centre -170,2570 half 150.5) lives in `extra.bin` while this class
        # loaded `h1m.bin` alone -- so `(-148.91, 2644.61) is in no patch`, and
        # the vessel was silently dropped for want of a height. Unioned in the
        # same order `cmd_newbuild` already uses, so both read one surface.
        # `patch_for` still takes the SMALLEST containing patch, so a 1 m
        # question is never answered off a coarser overview patch.
        self.field_names = [p for p in (extra or EXTRA_FIELD, field)
                            if p and Path(p).exists()]
        self.fields = {}
        for p in self.field_names:
            self.fields |= W.load(p)
        if not self.fields:
            raise SystemExit(f"no PatchScan field loaded from {self.field_names}")
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
                f"({x}, {z}) is in no patch of {self.field_names}; widen the "
                f"request and re-run run_patchscan.sh rather than guessing a "
                f"height")
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
# `roads/clear.py`, loaded BY PATH under an unambiguous module name.
#
# MEASURED LIVE TONIGHT, and it aborted a pass two records in: `import clear`
# inside `audit` resolved to `jumpstart/clearing/clear.py` and raised
# `module 'clear' has no attribute 'list_box'`.  The cause is that
# `settlements/build.py` -- which this module imports for its grounding
# instrument -- does `sys.path.insert(0, JUMPSTART / "clearing")`, so whether
# `clear` means the census module or the clearing-area module depends on
# WHICH OTHER MODULE WAS IMPORTED FIRST.  Two files of the same name on one
# path is a coin flip, and `build.py` loads `network/waypoints.py` by path for
# exactly this class of reason.
_ROADS_CLEAR = None


def roads_clear():
    global _ROADS_CLEAR  # noqa: PLW0603  one module object, loaded once
    if _ROADS_CLEAR is None:
        import importlib.util  # noqa: PLC0415
        path = JUMPSTART / "roads" / "clear.py"
        spec = importlib.util.spec_from_file_location("roads_clear", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["roads_clear"] = mod
        spec.loader.exec_module(mod)
        if not hasattr(mod, "list_box"):
            raise SystemExit(f"{path} carries no `list_box`; the census "
                             f"instrument is not where this module thinks")
        _ROADS_CLEAR = mod
    return _ROADS_CLEAR



CENSUS_CELL_M = 16.0
CENSUS_Y_LEVELS = (0.0, -16.0, 16.0, -32.0, 32.0)


def audit(srv, x0: float, x1: float, z0: float, z1: float, *,
          pad_m: float = 4.0, y_centre: float = 30.0,
          y_levels: tuple[float, ...] = CENSUS_Y_LEVELS) -> dict:
    """EVERY ZDO in a box, live, by prefab and WORLD POSITION.

    Disjoint 16 m cells on the global 16 m lattice, one UNSCOPED
    `findObjects -near cx y cz 8` per cell at FIVE y levels, because
    `findObjects -near` is a CUBE IN ALL THREE AXES and a box centred on the
    deck cannot contain a mast or a pile head.  No prefab list at all, so the
    census cannot be blind to a prefab nobody wrote down.  Deduplicated by
    prefab + world position, NOT by ZDO id: ids are reassigned on world load.

    `y_levels` are the OFFSETS from `y_centre`, and the default pair of
    (0, -16, 16, -32, 32) leaves an 8 m gap between +16 and +32 -- harmless
    for a deck, and NOT harmless for a 48 m tower, so a caller censusing one
    passes a contiguous stack instead.  MEASURED: at half 8 each box spans
    y +/- 8 about its own centre, so offsets 16 m apart tile exactly.
    """
    CL = roads_clear()
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


def site_anchor(ops: list[dict], site: str) -> dict:
    """The op a pass's `save` postcondition should be measured against.

    NEVER the hub end.  MEASURED the hard way at `dock-northcape`: the save was
    anchored on `ops[-1]`, which `pair_ops` had just made the HUB portal, and
    the check asked for `prefab_count[portal_wood] == 1` within 12 m of a bay in
    the portal hall -- where NINE portals stand inside 12 m.  It answered 9 and
    correctly refused, while the world was entirely right.  A postcondition that
    measures a different place than the pass built is a question about somebody
    else's work.

    So the anchor is the last op belonging to THIS site, and a portal is only
    taken if nothing else is available.
    """
    mine = [o for o in ops
            if o["op"] in ("spawn", "portal")
            and (o["params"] or {}).get("site_id") == site]
    if not mine:
        raise SystemExit(f"no spawn/portal op belongs to {site!r}; a save cannot "
                         f"be anchored on another site's piece")
    solid = [o for o in mine if o["op"] == "spawn"]
    return (solid or mine)[-1]




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
    # THE PORTAL GAP, CLOSED HERE.  Any tag whose SITE end this pass opens gets
    # its HUB end appended to the SAME op list, so one pass emits both ends or
    # neither.  Emitting the site end alone leaves a tag that pairs with
    # nothing, which is the operator's original reported defect.
    todo, pairing = pair_ops(site, todo)
    if pairing:
        print(json.dumps(dict(pairing=pairing), indent=1, default=float))
    if not todo:
        print("nothing to do")
        return 0
    if not args.apply:
        print(json.dumps(dict(site=site, would_emit=[o["op"] for o in todo],
                              pairing=pairing,
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
        if pairing:
            for row in pairing:
                if row.get("bay"):
                    row["bay"]["live_confirmed"] = confirm_bay_clear(
                        b.srv, row["bay"])
            b.observe(f"portal_pair_bays::{site}",
                      "MEASURED by portal_hall's own bay gate (fixtures.probe "
                      "against the placed hall body, interior mask class, 4.4 m "
                      "separation from every standing seat) and then CONFIRMED "
                      "EMPTY LIVE with a scoped objects_count. NOT a "
                      "least-squares fit over the seat records: that fit put "
                      "this arch 0.198 m into blackmarble_column_2 on a "
                      "covered_unenclosed cell",
                      "tools/jumpstart/settlements/waterfront.py::hub_bay",
                      pairing)
        emit(b, todo, label=site)
        last = site_anchor(todo, site)
        lp = last["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1,
                         max(3.0, lp.get("guard_radius_m", 0.5) * 2.0),
                         SITE_ROLE[site])], label=f"{site} save")
        # BOTH ENDS, off the ZDO, before this pass is called done.
        pairs = []
        for row in pairing:
            ends = [dict(e, end=("hub" if e.get("site_id") == "portal-hall"
                                 else "site")) for e in tag_ends(row["tag"])]
            emitted = [o["params"] for o in todo
                       if o["op"] == "portal"
                       and (o["params"] or {}).get("tag") == row["tag"]]
            for p in emitted:
                if not any(abs(e["x"] - p["pos"][0]) < 0.5
                           and abs(e["z"] - p["pos"][2]) < 0.5 for e in ends):
                    ends.append(dict(x=p["pos"][0], y=p["pos"][1],
                                     z=p["pos"][2],
                                     site_id=p.get("site_id"),
                                     end=("hub" if p.get("site_id")
                                          == "portal-hall" else "site")))
            pairs.append(verify_pair(b.srv, row["tag"], ends))
        if pairs:
            b.observe(f"portal_pair_readback::{site}",
                      "MEASURED: findObjects -detailed bounded to 3 m at EVERY "
                      "end of the tag, tag string required in the ZDO listing "
                      "at both, because the record that wrote a tag is not "
                      "evidence the tag is on the ZDO",
                      "tools/jumpstart/settlements/waterfront.py::verify_pair",
                      pairs)
            broken = [p["tag"] for p in pairs if not p["paired"]]
            if broken:
                raise SystemExit(
                    f"the tag(s) {broken} did NOT read back off both ZDOs. The "
                    f"pass is NOT complete and the ledger says so; do not "
                    f"annotate this away, fix the end that is missing.")
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
    # A RUN OF ONE PREFAB CANNOT USE THE SINGLE-SPAWN GUARD, and this is
    # MEASURED rather than reasoned: on the first live run tread 2's
    # postcondition asked for `prefab_count[wood_stair] == 1` inside the
    # `max(3.0, guard*2)` default radius and the live answer was 2, because the
    # treads are `STAIR["run_m"]` = 2.029 m apart and BOTH sit inside 3 m. The
    # record correctly refused. The WORLD was right -- 2 treads at the intended
    # positions, `Support: 1` on both, read back off the ZDOs -- so the defect
    # was in the question, not the answer.
    #
    # The honest postcondition for a run is CUMULATIVE: after tread i lands
    # there are exactly i+1 of them inside a radius that spans the whole run.
    # That is monotone, exact at every step, and it cannot pass on a neighbour
    # because the neighbour it would count is one this pass placed on purpose.
    run_r = round(STAIR["run_m"] * max(1, len(run["steps"])) + 1.0, 1)
    anchor = run["steps"][0]
    for i, op in enumerate(ops, start=1):
        op["expect"] = dict(prefab_count=[dict(
            prefab=STAIR["prefab"],
            pos=[round(anchor["x"], 2), round(anchor["z"], 2)],
            max=run_r, count=i, tolerance=0)])
        op["meta"]["guard"] = dict(
            kind="cumulative run count", index=i, of=len(ops),
            radius_m=run_r, tread_pitch_m=STAIR["run_m"],
            why=("the single-spawn guard counts 1 inside 3 m and the treads are "
                 "2.029 m apart, so it reads a sibling tread as a duplicate"))
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


# ---------------------------------------------------------------------------
# the HUB end of a portal pair
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT A RING FIT ANY MORE.  The first version of this section fitted
# a circle by least squares over the 18 `portal-hall` portal RECORDS in the
# ledger -- centre (-279.415, 215.646), R 13.375 -- read the 248.2..294.6 deg
# span as empty, and seated `x-vestvik` at 270 deg, i.e. (-292.79, 215.65).
# Two things were wrong with that, and the second one would have stood an arch
# inside a wall:
#
#   1. THE FIT WAS POLLUTED.  Three of those 18 seats are not on that ring at
#      all: `u-treenear`, `x-harbour` and `x-ferry-e` sit at R 8.47, 7.26 and
#      8.26 on the hall's INNER ring, against 12.87..14.65 m for the other 15.
#      A least-squares circle over two concentric rings describes neither, and
#      the residual is what let a 46.4 deg "gap" look like one free slot.
#   2. THE SEAT FAILED THE HALL'S OWN GATE.  MEASURED by `fixtures.probe`
#      against the placed hall body at (-292.79, 37.10, 215.65) yaw 90: worst
#      penetration 0.198 m -- OVER `portal_hall.BAY_PEN_TOL_M` 0.15 -- into
#      `blackmarble_column_2` and `blackmarble_1x1`, and the interior mask
#      calls the cell `covered_unenclosed`, not `indoor_covered`.  That seat is
#      in the colonnade wall, in a door axis.  Geometry read off a log is not
#      geometry read off the body.
#
# So the seat is ALLOCATED BY THE HALL'S OWN BAY SOLVER.  `portal_hall` already
# owns that instrument -- the body's 15 designer arch transforms plus solved
# inner-ring bays, each gated on nothing penetrating deeper than the 0.15 m the
# floor beams themselves do, on the interior mask calling the cell
# `indoor_covered`, and on 4.4 m (one `portal_wood` width) from every other bay.
# `portal_hall.bays(need=18)` reproduces all three live solved seats to 0.000 m,
# which is the check that says the instrument and the world agree.
#
# `bays(need=20)` CANNOT be used to get two more, and this is a trap worth
# naming: its sweep step is `360/short`, so raising `need` MOVES the earlier
# solved bays.  MEASURED at need=20 the bay that should be `x-harbour`'s drifts
# 1.100 m and `x-ferry-e`'s 2.911 m, so the 4.4 m separation gate would then be
# measured against seats that are not the ones standing in the world.  The
# sweep is therefore re-run here with the LIVE seats as the occupied set.
HUB_BAY_CLEAR_M = 4.4


def _hall() -> tuple:
    """The hall's interior mask and collider index, built from the SAME body
    placement `portal_hall.bays` uses, so a bay solved here is a bay by that
    module's definition and not by a second one."""
    import base_geometry as BG  # noqa: PLC0415
    import portal_hall as PH  # noqa: PLC0415
    g = BG.geometry()
    _objs, keep = PH.body_objects()
    shell = FX.place_body(keep, (PH.HALL_X, PH.TARGET_Y, PH.HALL_Z),
                          yaw_deg=PH.BODY_YAW)
    return PH, g, shell.interior(), shell.index()


def hub_seats_taken() -> list[dict]:
    """Every `portal_wood` seat the hall already holds, per the ledger.

    This is the CANDIDATE list, not the oracle: `retire` records are honoured
    (a retired seat frees its bay) and the survivors are confirmed live with
    `findObjects` before anything is emitted, because a portal placed outside
    the ledger is invisible to this function and would be collided with.
    """
    seats: dict[tuple, dict] = {}
    for line in LIVE_LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        p = rec.get("params") or {}
        if p.get("prefab") != "portal_wood":
            continue
        pos = p.get("pos") or []
        if len(pos) < 3:
            continue
        key = (round(pos[0], 1), round(pos[2], 1))
        if rec["op"] == "retire":
            seats.pop(key, None)
        elif rec["op"] == "portal":
            seats[key] = dict(tag=p.get("tag"), x=pos[0], y=pos[1], z=pos[2],
                              yaw=p.get("yaw_deg") or 0.0,
                              site_id=p.get("site_id"))
    return list(seats.values())


def hub_bay(taken: list[dict], *, bearing_step: float = 1.0) -> dict:
    """Solve ONE free bay in the hall, under `portal_hall`'s own gate.

    Sweeps the bearings and the radius ladder the hall's solver uses and returns
    the first candidate that (a) drives nothing deeper than `BAY_PEN_TOL_M` into
    the body, (b) lands on an `indoor_covered` cell, and (c) clears every seat
    in `taken` by `HUB_BAY_CLEAR_M`.  It RAISES rather than relaxing any of the
    three, because a bay that fails the gate is an arch in a wall -- and because
    a guard that quietly widens its own tolerance is the silent shrink this
    project has now been bitten by four times.

    The cheap tests (interior class, separation) run BEFORE the collider probe,
    which is the expensive one; that is an ordering choice, not a different gate.
    """
    PH, g, mask, index = _hall()
    ref = PH.bays(need=18)
    dsg = [b for b in ref if b["source"] == "designer"]
    rx0 = sum(b["x"] for b in dsg) / len(dsg)
    rz0 = sum(b["z"] for b in dsg) / len(dsg)
    occupied = [FX.fixture_box("portal_wood", s["x"], s.get("y", PH.TARGET_Y),
                               s["z"], s.get("yaw") or 0.0, g) for s in taken]
    rejected: list[dict] = []
    steps = int(round(360.0 / bearing_step))
    for k in range(steps):
        bearing = round(k * bearing_step, 1)
        for radius in (8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 8.5):
            x = rx0 + radius * math.sin(math.radians(bearing))
            z = rz0 + radius * math.cos(math.radians(bearing))
            cell = mask.cell(x, z)
            klass = mask.klass.get(cell, "outdoor")
            near = min([math.hypot(x - s["x"], z - s["z"]) for s in taken],
                       default=99.9)
            if klass != "indoor_covered":
                continue
            if near < HUB_BAY_CLEAR_M:
                continue
            stand = mask.stand_y(*cell) if cell in mask.cover_m else PH.TARGET_Y
            yaw = round((bearing + 180.0) % 360.0, 1)
            box = FX.fixture_box("portal_wood", x, stand, z, yaw, g)
            hits, _sup = FX.probe(box, index, stand_y=stand, geom=g)
            worst = max([h["penetration_m"] for h in hits], default=0.0)
            deep = sorted({h["prefab"] for h in hits
                           if h["penetration_m"] > PH.BAY_PEN_TOL_M})
            clash = any(all(o > 1e-6 for o in FX.overlap(box, t))
                        for t in occupied)
            row = dict(x=round(x, 3), y=round(stand, 3), z=round(z, 3),
                       yaw=yaw, bearing=bearing, r_m=radius, source="solved",
                       worst_pen_m=round(worst, 3), deep_pen_prefabs=deep,
                       klass=klass, cover_m=(None if cell not in mask.cover_m
                                             else round(mask.cover_m[cell], 2)),
                       nearest_seat_m=round(near, 3), overlaps=clash,
                       seats_considered=len(taken),
                       centre=[round(rx0, 3), round(rz0, 3)])
            if deep or clash:
                rejected.append(row)
                continue
            row["ok"] = True
            row["gate"] = (
                f"MEASURED by portal_hall's own bay gate via fixtures.probe "
                f"against the placed hall body: worst penetration "
                f"{row['worst_pen_m']} m <= BAY_PEN_TOL_M "
                f"{PH.BAY_PEN_TOL_M}, interior mask class {klass!r}, nearest of "
                f"{len(taken)} standing seats {row['nearest_seat_m']} m >= "
                f"HUB_BAY_CLEAR_M {HUB_BAY_CLEAR_M}. Sweep centred on the mean "
                f"of the body's 15 designer arch transforms "
                f"({rx0:.3f},{rz0:.3f}), radius ladder and tolerances taken "
                f"from portal_hall.bays, not restated.")
            row["rejected_sample"] = rejected[:6]
            return row
    raise SystemExit(
        f"no free bay in the hall clears portal_hall's gate against "
        f"{len(taken)} standing seats: {len(rejected)} candidates were "
        f"indoor_covered and {HUB_BAY_CLEAR_M} m clear but penetrate the body. "
        f"Do NOT relax the gate -- the hall is full and needs another bay row.")


def hub_portal_op(site: str, tag: str, bay: dict) -> dict:
    """The HUB end of `tag`, on a bay solved by the hall's own instrument.

    Pairing is EXACT STRING EQUALITY with a uniform random draw among equally
    tagged unconnected portals, so a tag with ONE end pairs with nothing and the
    site end is a dead arch.  This op is ALWAYS emitted in the same pass as the
    site end -- see `pair_ops` -- so the tag never exists one-ended.
    """
    blob = portal_blob(tag)
    cmd = (f"spawn_object portal_wood pos={A.fmt(bay['z'])},{A.fmt(bay['x'])},"
           f"{A.fmt(bay['y'])} rot={A.fmt(bay['yaw'])},0,0 from=0,0,0"
           f" data={blob}")
    return dict(
        op="portal",
        params=dict(prefab="portal_wood",
                    pos=[round(bay["x"], 4), round(bay["y"], 4),
                         round(bay["z"], 4)],
                    yaw_deg=bay["yaw"], tag=tag, pair_tag=tag,
                    guard_radius_m=0.5, zdo_strings=dict(tag=tag),
                    data_b64=blob,
                    role="spawn_portal", site_id="portal-hall",
                    datum=(f"bay solved at bearing {bay['bearing']} radius "
                           f"{bay['r_m']} m on the hall floor at y {bay['y']}"),
                    flatten="FORBIDDEN",
                    flatten_reason=("the hall floor is a built pad at y 37.1; a "
                                    "terrain_write under it would drop the floor "
                                    "the bay stands on")),
        wire=[cmd],
        requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                      prefabs=["portal_wood"], blobs=[]),
        expect=dict(prefab_count=[dict(
            prefab="portal_wood", pos=[round(bay["x"], 2), round(bay["z"], 2)],
            max=3.0, count=1, tolerance=0)]),
        meta=dict(end="hub", pair_site=site, bay=bay,
                  why=("pairing is EXACT STRING EQUALITY with a uniform random "
                       "draw among equally-tagged unconnected portals, so a tag "
                       "with ONE end pairs with nothing and the site end would "
                       "be a dead arch. Emitted in the SAME pass as the site "
                       "end so the tag is never one-ended. The bay is ALLOCATED "
                       "by portal_hall's own gate, not fitted to the ledger: a "
                       "least-squares ring fit over the 18 seat records put "
                       "this arch at (-292.79,215.65), which MEASURES 0.198 m "
                       "into blackmarble_column_2 against a 0.15 m tolerance "
                       "and lands on a covered_unenclosed cell.")))


def tag_ends(tag: str) -> list[dict]:
    """Every standing end of `tag` per the ledger, retires honoured."""
    ends: dict[tuple, dict] = {}
    for line in LIVE_LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        p = rec.get("params") or {}
        if p.get("prefab") != "portal_wood":
            continue
        pos = p.get("pos") or []
        if len(pos) < 3:
            continue
        key = (round(pos[0], 1), round(pos[2], 1))
        if rec["op"] == "retire":
            ends.pop(key, None)
        elif rec["op"] == "portal" and p.get("tag") == tag:
            ends[key] = dict(tag=tag, x=pos[0], y=pos[1], z=pos[2],
                             site_id=p.get("site_id"), seq=rec.get("seq"))
    return list(ends.values())


def confirm_bay_clear(srv, bay: dict, *, radius: float = HUB_BAY_CLEAR_M) -> dict:
    """MEASURE the bay empty before seating an arch in it.

    `hub_seats_taken` reads the LEDGER, and the ledger is the candidate list,
    not the oracle: a portal placed outside it is invisible to that function,
    and a seat retired outside it would read as occupied.  So the solved bay is
    confirmed live with a SCOPED `objects_count` -- scoped rather than the
    unscoped `findObjects` this project caps at 8 m, because the question is
    "is there a portal here", which names its prefab.

    Refuses rather than nudging.  A guard that slides the bay a metre to make
    room is a silent shrink; if the world disagrees with the ledger, the right
    answer is to re-solve against the world, which is a decision, not a fixup.
    """
    n, per = srv.count("portal_wood", bay["x"], bay["z"], radius)
    out = dict(at=[round(bay["x"], 2), round(bay["z"], 2)], radius_m=radius,
               portal_wood=n, per_prefab=per,
               method=("MEASURED live: scoped `objects_count id=portal_wood` "
                       "over the bay's own separation radius. The ledger is the "
                       "candidate list; this is the oracle."))
    if n:
        raise SystemExit(
            f"the solved bay at ({bay['x']}, {bay['z']}) is NOT empty: "
            f"{n} portal_wood stand within {radius} m, which the ledger does "
            f"not know about. Re-solve against the live seats; do NOT nudge "
            f"this bay to fit.")
    print(f"    bay ({bay['x']}, {bay['z']}) confirmed EMPTY live: "
          f"0 portal_wood within {radius} m", flush=True)
    return out


def pair_ops(site: str, todo: list[dict]) -> tuple[list[dict], list[dict]]:
    """Append a HUB end for every tag this pass is about to open at a SITE.

    THE GAP THIS CLOSES.  `complete --site harbour-vestvik` emits the site end
    of `x-vestvik` and nothing else: the hub end was a separate function with no
    caller.  Run alone it leaves a tag with ONE end, which pairs with nothing --
    the operator's original reported defect, reintroduced by a known gap.

    A tag must have EXACTLY two ends.  So for each site-end portal op in `todo`:
      * count the tag's standing ends in the ledger (retires honoured);
      * 0 standing and we are emitting the site end -> solve a bay, append the
        hub end to the SAME op list, so one pass emits both or neither;
      * 1 standing already -> the missing end is appended, whichever it is;
      * 2 standing -> nothing to do;
      * 3+ -> raise.  A third end makes the destination a coin flip, and
        silently picking one to keep is not this function's call to make.

    Returns (ops, report).  Bays are solved against a taken-set that includes
    every bay solved earlier in the SAME call, so two tags in one pass cannot be
    given the same bay.
    """
    site_ends = [o for o in todo
                 if o["op"] == "portal" and (o["params"] or {}).get("tag")]
    if not site_ends:
        return todo, []
    taken = hub_seats_taken()
    out = list(todo)
    report = []
    for op in site_ends:
        tag = op["params"]["tag"]
        standing = tag_ends(tag)
        hub = [e for e in standing if e.get("site_id") == "portal-hall"]
        row = dict(tag=tag, standing_ends=len(standing),
                   hub_ends=len(hub), site_end_emitted_now=True)
        if len(standing) >= 3:
            raise SystemExit(
                f"tag {tag!r} already has {len(standing)} standing ends "
                f"{[(e['x'], e['z']) for e in standing]}; a third end makes the "
                f"destination a coin flip. Retire the extras deliberately "
                f"before adding to this tag.")
        if hub:
            row["action"] = "hub end already standing; not duplicated"
            report.append(row)
            continue
        bay = hub_bay(taken)
        hop = hub_portal_op(site, tag, bay)
        out.append(hop)
        taken.append(dict(tag=tag, x=bay["x"], y=bay["y"], z=bay["z"],
                          yaw=bay["yaw"], site_id="portal-hall"))
        row["action"] = "hub end SOLVED and appended to this pass"
        row["bay"] = bay
        report.append(row)
    return out, report


def verify_pair(srv, tag: str, ends: list[dict]) -> dict:
    """Read `tag` back off EVERY end's ZDO.

    The record that wrote a tag is not evidence the tag is on the ZDO: the blob
    could encode the wrong hash, the spawn could have been swallowed, or the
    guard could have matched a different portal.  So both ends are read with
    `findObjects -detailed` and the tag STRING is required at both.  A pair that
    reads back at one end only is reported as BROKEN, loudly, rather than being
    counted as a success because the emit returned cleanly.
    """
    rows = []
    for e in ends:
        rb = read_back(srv, "portal_wood", e["x"], e["y"], e["z"], radius=3.0)
        listing = rb.get("listing") or ""
        rows.append(dict(end=e.get("end") or e.get("site_id"),
                         at=[round(e["x"], 2), round(e["y"], 2),
                             round(e["z"], 2)],
                         # `bounded_detailed` returns the LEAF COUNT as an int,
                         # not a list of leaves -- MEASURED by this function
                         # raising `object of type 'int' has no len()` on the
                         # first live pair. The ZDO count is the number of
                         # `-Prefab:` rows in the listing, which is the thing
                         # actually being counted.
                         leaves=rb.get("leaves"),
                         zdos=sum(1 for ln in listing.splitlines()
                                  if ln.strip().startswith("-Prefab:")),
                         tag_on_zdo=(tag in listing),
                         listing=listing))
    ok = len(rows) == 2 and all(r["tag_on_zdo"] for r in rows)
    out = dict(tag=tag, ends=rows, paired=ok,
               method=("MEASURED: findObjects -detailed bounded to 3 m at each "
                       "end, and the tag STRING required in the ZDO listing at "
                       "BOTH. Pairing is exact string equality among unconnected "
                       "portals, so one end reading back is not half a portal, "
                       "it is a dead arch."))
    if not ok:
        print(f"    !! PAIR {tag} IS NOT PROVEN: "
              f"{[(r['end'], r['tag_on_zdo']) for r in rows]}", flush=True)
    else:
        print(f"    pair {tag}: tag read back off BOTH ZDOs", flush=True)
    return out


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
    # THE RAILS ARE A RUN TOO, and the same guard that refused the stair would
    # refuse them: 12 `wood_beam` at 1.4-2.0 m spacing all sit inside the
    # single-spawn guard's 3.0 m radius, so each one after the first would read
    # as a duplicate of its neighbour. Same fix as `cmd_stair` -- a CUMULATIVE
    # count over a radius spanning the whole rail line -- with one addition
    # that the stair did not need: the radius is wide enough to catch
    # `wood_beam` this pass did NOT place, so the baseline is MEASURED LIVE
    # first and the expected count is baseline + i. A guard that assumed a
    # baseline of zero would pass on somebody else's beams.
    rails = [o for o in ops if o["params"]["prefab"] == "wood_beam"]
    if rails:
        rx = [o["params"]["pos"][0] for o in rails]
        rz = [o["params"]["pos"][2] for o in rails]
        cx, cz = (min(rx) + max(rx)) / 2.0, (min(rz) + max(rz)) / 2.0
        rail_r = round(max(math.hypot(x - cx, z - cz)
                           for x, z in zip(rx, rz)) + 2.0, 1)
        # The baseline comes out of the census THIS PASS already took over the
        # same box, rather than a second socket call: the rails all lie on the
        # deck and the census box is root+/-8 to head+/-8, so every candidate
        # `wood_beam` inside the rail radius is already in `rep["objects"]`.
        base = sum(1 for o in rep["objects"]
                   if o["prefab"] == "wood_beam"
                   and math.hypot(o["x"] - cx, o["z"] - cz) <= rail_r)
        print(f"    rail guard: {len(rails)} wood_beam, radius {rail_r} m about "
              f"({cx:.2f},{cz:.2f}), LIVE baseline {base} already standing")
        for i, op in enumerate(rails, start=1):
            op["expect"] = dict(prefab_count=[dict(
                prefab="wood_beam", pos=[round(cx, 2), round(cz, 2)],
                max=rail_r, count=base + i, tolerance=0)])
            op["meta"]["guard"] = dict(
                kind="cumulative run count over a live baseline",
                index=i, of=len(rails), radius_m=rail_r, live_baseline=base,
                why=("12 beams at 1.4-2.0 m spacing all fall inside the "
                     "single-spawn guard's 3.0 m radius, so it reads a sibling "
                     "as a duplicate -- MEASURED on the stair at this same "
                     "site. The radius spans the run and the baseline is "
                     "measured live so the count cannot pass on beams this "
                     "pass did not place."))
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
    # THE PORTAL GAP, CLOSED HERE TOO.  `ledger_ops.ops_for` is handed
    # `pair_ends={}` and emits the SITE end only, so a new site with a
    # `portal_tag` would stand with a tag that pairs with nothing.  At
    # `dock-northcape` that is not cosmetic: the site is ~2.8 km from the
    # nearest built road and the portal IS the access, so a one-ended tag
    # strands the operator at the top of the map.
    ops, pairing = pair_ops(sid, ops)
    if pairing:
        print(json.dumps(dict(pairing=pairing), indent=1, default=float))
    if not args.apply:
        print(json.dumps(dict(ops=[o["op"] for o in ops],
                              pairing=pairing), indent=1, default=float))
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
        if pairing:
            for row in pairing:
                if row.get("bay"):
                    row["bay"]["live_confirmed"] = confirm_bay_clear(
                        b.srv, row["bay"])
            b.observe(f"portal_pair_bays::{sid}",
                      "MEASURED by portal_hall's own bay gate (fixtures.probe "
                      "against the placed hall body, interior mask class, 4.4 m "
                      "separation from every standing seat) and then CONFIRMED "
                      "EMPTY LIVE with a scoped objects_count",
                      "tools/jumpstart/settlements/waterfront.py::hub_bay",
                      pairing)
        emit(b, ops, label=sid)
        lp = site_anchor(ops, sid)["params"]
        emit(b, [save_op((lp["pos"][0], lp["pos"][2]), lp["prefab"], 1, 12.0,
                         spec["role"])], label=f"{sid} save")
        pairs = []
        for row in pairing:
            ends = [dict(e, end=("hub" if e.get("site_id") == "portal-hall"
                                 else "site")) for e in tag_ends(row["tag"])]
            for o in ops:
                if o["op"] != "portal":
                    continue
                p = o["params"]
                if p.get("tag") != row["tag"]:
                    continue
                if not any(abs(e["x"] - p["pos"][0]) < 0.5
                           and abs(e["z"] - p["pos"][2]) < 0.5 for e in ends):
                    ends.append(dict(x=p["pos"][0], y=p["pos"][1],
                                     z=p["pos"][2], site_id=p.get("site_id"),
                                     end=("hub" if p.get("site_id")
                                          == "portal-hall" else "site")))
            pairs.append(verify_pair(b.srv, row["tag"], ends))
        if pairs:
            b.observe(f"portal_pair_readback::{sid}",
                      "MEASURED: findObjects -detailed bounded to 3 m at EVERY "
                      "end of the tag, tag string required in the ZDO listing "
                      "at both. At dock-northcape the portal IS the access, so "
                      "an unproven pair is a stranded operator",
                      "tools/jumpstart/settlements/waterfront.py::verify_pair",
                      pairs)
            broken = [p["tag"] for p in pairs if not p["paired"]]
            if broken:
                raise SystemExit(
                    f"the tag(s) {broken} did NOT read back off both ZDOs; "
                    f"{sid} is portal-only and this pass is NOT complete.")
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
    results = []
    for i, prefab in enumerate(args.prefabs):
        px = x + i * 24.0
        # EACH SLOT ON ITS OWN GROUND. Sampling once at the first slot and
        # reusing it seats the others wherever that happens to land: MEASURED on
        # the (56,-288) line the three slots are 35.00 / 34.90 / 35.97 m, so a
        # shared datum would have buried the third probe 0.97 m and read as a
        # negative result for a prefab that exists.
        g = surf.at(px, z)[0]
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


# The deck tiles that MEASURE buried, per site, with the `spawn_plan` seq that
# placed them.  Positions are the LIVE census positions, not the reconstructed
# ones: the assembly recomputed off the field puts these two at (13.03,-259.79)
# and (11.75,-261.32), which is 0.17 m and 0.25 m from where they actually
# stand, and a 1 m delete aimed at a reconstruction can miss.
BURIED = {
    "harbour-temple-south": dict(
        placed_by_seq=26, prefab="wood_floor",
        tiles=[(12.91, -259.91), (11.50, -261.33)],
        why=("the pier's landward corner is inside the bank. MEASURED against "
             "the composed applied surface, these two tiles carry 0.724 m and "
             "0.150 m of NATURAL ground over their walking surface -- the "
             "ledger's own delta at both is 0.000, so this is not a clobber by "
             "any road record and rewinding one cannot help. They are "
             "invisible. A CUT is refused: the neighbouring deck tile at "
             "(11.50,-258.50) stands at 30.582, i.e. 0.582 m above "
             "c_WaterLevel, terrain spread reaches 1 m past the request, and "
             "that is the same sub-0.6 m headroom beside open water that "
             "refused a cut at the stair 4 m away. Draining a pier is the "
             "unrepairable class, so 0.15 m of cosmetics does not buy a "
             "terrain write here."))
}


def cmd_retire(args) -> int:
    """Retire deck tiles that MEASURE buried, instead of cutting the bank.

    Three things are re-measured before anything is deleted, because every one
    of them is a way this pass could be wrong:

      1. THE BURIAL, off the composed applied surface at each tile's own XZ.
         A tile that is not actually buried is not retired.
      2. THE SUPPORT, with the game's own WearNTear algorithm on the assembly
         MINUS these tiles.  If the deck stops standing without them they are
         load-bearing and this pass refuses -- buried is not the same as free.
      3. THE NEIGHBOURS, live.  The delete is `deleteObjects -prefab wood_floor
         -near x y z r`, so the radius must hold exactly ONE tile.  MEASURED on
         this deck the pitch is 2 m, and the radius is checked against the
         nearest OTHER tile rather than assumed from the pitch.

    No terrain is written, so the "every written sample stays above
    c_WaterLevel" requirement is satisfied by there being no written sample.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    import replay as R  # noqa: PLC0415
    site = args.site
    spec = BURIED.get(site)
    if not spec:
        raise SystemExit(f"{site} has no MEASURED buried tiles recorded; "
                         f"measure them before retiring anything")
    surf = Surface()
    s = SITES[site]
    deck_top = s["deck_top_y"]
    # 1. THE BURIAL
    rows = []
    for tx, tz in spec["tiles"]:
        app, gen, delta = surf.at(tx, tz)
        rows.append(dict(xz=[tx, tz], applied_y=round(app, 3),
                         generated_y=round(gen, 3), ledger_delta_m=round(delta, 3),
                         deck_top_y=deck_top,
                         buried_by_m=round(app - deck_top, 3),
                         author=surf.author_at(int(round(tx)), int(round(tz)))))
    print(json.dumps(dict(burial=rows), indent=1, default=str))
    shallow = [r for r in rows if r["buried_by_m"] <= 0.0]
    if shallow:
        raise SystemExit(
            f"{[r['xz'] for r in shallow]} MEASURE at or below the deck top and "
            f"are therefore NOT buried; retiring a visible deck tile would take "
            f"a hole out of the pier. Re-measure before running this.")
    # 2. THE SUPPORT, asked of the game's own algorithm
    fields = W.load(EXTRA_FIELD) | W.load(FIELD)
    fld = None
    for f in fields.values():
        if f.step <= 1.0 and f.contains(s["root"][0], s["root"][1], 40.0):
            if fld is None or f.half < fld.half:
                fld = f
    ground, bed = CP.profiles(fld)
    pieces = A.jetty(s["root"], s["bearing"], s["length_m"], A.DECK_Y, bed,
                     width_tiles=s.get("width_tiles", 2), name=site)
    keep = [p for p in pieces
            if not any(math.hypot(p.x - tx, p.z - tz) < 0.6
                       for tx, tz in spec["tiles"])]
    full = A.verify(pieces, ground, note=f"{site} full")
    less = A.verify(keep, ground, note=f"{site} minus buried")
    support = dict(pieces_full=len(pieces), pieces_kept=len(keep),
                   full_ok=full["ok"], without_ok=less["ok"],
                   without_failures=(less.get("failures") or [])[:6],
                   method=("MEASURED with the game's own WearNTear support "
                           "algorithm via crossings/assemble.py::verify, on the "
                           "assembly MINUS these tiles"))
    print(json.dumps(support, indent=1, default=float))
    if not less["ok"]:
        raise SystemExit(
            f"the deck does NOT stand without these tiles: "
            f"{less.get('failures')[:3]}. They are load-bearing, buried or not, "
            f"and retiring them would collapse the pier. Leave them.")
    # 3. THE NEIGHBOURS, live -- the radius must hold exactly one tile
    radius = 1.0
    with R.Server(dry=False) as srv:
        srv.probe()
        box = audit(srv, min(t[0] for t in spec["tiles"]) - 10,
                    max(t[0] for t in spec["tiles"]) + 10,
                    min(t[1] for t in spec["tiles"]) - 10,
                    max(t[1] for t in spec["tiles"]) + 10)
    live = [o for o in (box.get("objects") or [])
            if o["prefab"] == spec["prefab"]]
    print(f"    live {spec['prefab']} in the census box: {len(live)}")
    aim = []
    for tx, tz in spec["tiles"]:
        d = sorted(((math.hypot(o["x"] - tx, o["z"] - tz), o) for o in live),
                   key=lambda t: t[0])
        if not d:
            raise SystemExit(
                f"no live {spec['prefab']} anywhere near ({tx}, {tz}); an empty "
                f"answer is not a zero, so this stops rather than aiming a "
                f"delete at nothing")
        inside = [o for dd, o in d if dd <= radius]
        nearest_other = next((dd for dd, _o in d if dd > radius), None)
        row = dict(target=[tx, tz], inside_radius=len(inside), radius_m=radius,
                   nearest_m=round(d[0][0], 3),
                   nearest_other_m=(None if nearest_other is None
                                    else round(nearest_other, 3)),
                   hit=dict(x=d[0][1]["x"], y=d[0][1]["y"], z=d[0][1]["z"]))
        aim.append(row)
        if len(inside) != 1:
            raise SystemExit(
                f"({tx}, {tz}) holds {len(inside)} {spec['prefab']} inside "
                f"{radius} m, not exactly 1; a delete here would take a "
                f"neighbour with it. Tighten the radius against the measured "
                f"nearest-other of {row['nearest_other_m']} m.")
    print(json.dumps(dict(aim=aim), indent=1, default=float))
    ops = []
    for row in aim:
        h = row["hit"]
        ops.append(dict(
            op="retire",
            params=dict(prefab=spec["prefab"],
                        pos=[round(h["x"], 3), round(h["y"], 3),
                             round(h["z"], 3)],
                        radius_m=radius, retires=[spec["placed_by_seq"]],
                        reason=spec["why"], role=SITE_ROLE[site], site_id=site),
            wire=[f"deleteObjects -prefab {spec['prefab']} -near "
                  f"{h['x']:.2f} {h['y']:.2f} {h['z']:.2f} {radius:.2f} -force"],
            requires=dict(mods=["WorldEditCommands"], prefabs=[spec["prefab"]],
                          blobs=[]),
            expect=dict(absent=[dict(prefab=spec["prefab"],
                                     pos=[round(h["x"], 2), round(h["z"], 2)],
                                     max=radius)]),
            meta=dict(counted_before=row["inside_radius"], aim=row,
                      burial=next(r for r in rows
                                  if abs(r["xz"][0] - row["target"][0]) < 0.01),
                      support=support,
                      deck_count_after=(
                          f"the deck's spawn_plan (seq {spec['placed_by_seq']}) "
                          f"has a prefab_count expect of 39 {spec['prefab']} at "
                          f"tolerance 0; after this retire the deck is 37 BY "
                          f"RECORD. That record is NOT edited and the plan is "
                          f"NOT re-emitted -- a re-emit would double every "
                          f"piece and its own postcondition would then fail. "
                          f"This append is the annotation."),
                      why_not_a_cut=spec["why"],
                      terrain_written="NONE -- this pass writes no terrain at "
                                      "all, so no sample can fall below "
                                      "c_WaterLevel 30.0")))
    if not args.apply:
        print(json.dumps(dict(would_emit=[o["wire"][0] for o in ops]), indent=1))
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note(f"{site}: retiring {len(ops)} deck tile(s) that MEASURE buried in "
               f"natural ground, rather than cutting the bank to expose them. "
               f"{spec['why']}", role=SITE_ROLE[site], site_id=site)
        b.observe(f"buried_deck_tiles::{site}",
                  "MEASURED three ways before deleting anything: burial off the "
                  "composed applied surface at each tile's own XZ; support from "
                  "the game's own WearNTear algorithm on the assembly MINUS "
                  "these tiles; and the delete radius against the LIVE nearest "
                  "other tile, so a 1 m delete cannot take a neighbour",
                  "tools/jumpstart/settlements/waterfront.py::cmd_retire",
                  dict(burial=rows, support=support, aim=aim))
        emit(b, ops, label=f"{site} retire")
        h = ops[-1]["params"]["pos"]
        emit(b, [save_op((h[0], h[2]), spec["prefab"], 0, radius,
                         SITE_ROLE[site])], label=f"{site} save")
        print(json.dumps(b.close(), indent=1))
    return 0


# ---------------------------------------------------------------------------
# THE NORTH LIGHTHOUSE: retire a HALF-BUILT body, then re-place it WHOLE
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT A RESUME, AND MUST NEVER BECOME ONE.  `lh-north`'s body went
# in as ONE `spawn_plan` of 1,255 `spawn_object` commands (seq 2164, 28
# batches of 3,600 bytes) and raised TimeoutError part way through: the game's
# stdout pipe backed up, pid 120208 sat in `pipe_write` on pipe:[62736727],
# and the main thread stopped answering RCON at all.  272 pieces stood when
# the sink was drained FROM THE HOST (supervisord held the read end on fd 21;
# `timeout 75 cat /proc/113676/fd/21 >/dev/null`, after which 120208 moved to
# `hrtimer_nanosleep` and RCON answered in 34 ms).  No SIGKILL was used and
# none may be: a SIGKILL mid-write loses a terrain compiler.
#
# That record's `expect` is a FULL-BODY `objects_count` of 1,255 at tolerance
# 0.  Re-emitting it re-sends all 1,255 commands, so it would DOUBLE the 272
# that stand and then fail its own postcondition on 1,527.  The precedent is
# ledger seq 73 -- RETIRE THE PARTIAL, RE-PLACE WHOLE -- and the retirement is
# recorded with counts and positions so this reads as a repair and not as a
# mystery.
#
# FOUR THINGS THIS SECTION MEASURES RATHER THAN ASSUMES:
#
#   1. WHAT IS STANDING.  Not seq 2166's per-prefab numbers and NOT the plan's
#      first N lines -- MEASURED, the 272 standing pieces are NOT a prefix of
#      the plan (its first 272 lines hold 58 `stone_wall_1x1` and the live
#      census of the same moment held none), so a demolition reconstructed
#      from plan order would aim at the wrong set.  The retire set comes from
#      an UNSCOPED live census, deduplicated by prefab + WORLD POSITION
#      because ZDO ids are reassigned on world load.
#
#   2. WHERE THE DELETE CUBE MAY REACH.  `deleteObjects -near x y z r` is a
#      CUBE of half-extent r in ALL THREE AXES (MEASURED, roads/clear.py), and
#      this body shares three prefabs -- `wood_floor`, `wood_pole`,
#      `wood_pole_log_4` -- with `dock-northcape`'s 127-piece pier, whose
#      nearest piece MEASURED 19.x m from this pad.  So the cube is sized from
#      the standing pieces' own extent and then CHECKED against every foreign
#      object of the same prefab the census found; it refuses rather than
#      guessing.
#
#   3. THE SINK, BEFORE EVERY BATCH.  A 1,255-command record is what wedged
#      it, so the body goes in as `SPAWN_BATCH_PIECES`-piece records with a
#      host-side sink measurement and an RCON liveness probe between them.
#      See `SPAWN_BATCH_PIECES` for the byte arithmetic.
#
#   4. THE HEIGHT AND THE BEACON'S SEAT, off the body's own collider solids
#      rather than off a mesh bounding box.  See `lh_height`.
#
# NO TERRAIN IS WRITTEN BY ANY OF THIS.  The pad (seq 2163) stands with both
# its compilers verified; a second write to those zones would DESTROY the
# first one's terrain, and the pad sits 2.98 m above c_WaterLevel 30.0 beside
# a pier whose piles are driven into water.

# The console sink's capacity and the cost of one echoed line, both MEASURED
# elsewhere in this project and used here as arithmetic rather than as a feel:
#   - a Linux pipe holds 65,536 bytes before a writer blocks in `pipe_write`,
#     which is the state pid 120208 was found in;
#   - `deleteObjects` echoes ONE LINE PER DELETED OBJECT at 155 bytes
#     (MEASURED by SeatCheck, quoted in ledger/schema.py), and 34 consecutive
#     removal records averaging 20 objects -- about 105 KB -- wedged the main
#     thread even though every individual reply was small.
# 64 spawn commands therefore cost at most 64 x 155 = 9,920 bytes, 15 % of one
# pipeful, and the gate below runs between records.  The old 1,255-command
# record was ~194 KB with no gate anywhere inside it: three pipefuls.
SINK_PIPE_BYTES = 65536
SINK_ECHO_BYTES = 155
SPAWN_BATCH_PIECES = 64
# The joined-command budget for ONE round trip.  `place.batch_budget` clamps
# against the 4,096-byte RCON buffer (the reply is `Command '<joined>'
# executed.`, the larger of the two directions); 1,800 halves the 3,600 the
# wedged record used, so a trip's echo sits near 45 % of the buffer rather
# than 88 %, and a desynchronised stream poisons the NEXT caller's reply.
SPAWN_WIRE_BYTES = 1800
# Margin added to the measured extent of the pieces being retired, and the
# clearance a FOREIGN object of the same prefab must keep outside the cube.
RETIRE_MARGIN_M = 0.5
RETIRE_CLEAR_M = 1.0
# The row cap a `findObjects` reply must stay under so nothing approaches the
# client's 4,096-byte buffer -- the same 18 rows roads/clear.py uses.
LIST_ROW_CAP = 18
# A contiguous census stack for a TOWER: offsets from the pad datum, 16 m
# apart so the 8 m half-boxes tile exactly, covering pad-16 m to pad+64 m.
# The default (0,-16,+16,-32,+32) leaves a gap between +16 and +32, which a
# 48 m body would fall into.
TOWER_Y_LEVELS = (-8.0, 8.0, 24.0, 40.0, 56.0)

LIGHTHOUSE: dict[str, dict] = {
    "lh-north": dict(
        pad=(-182.0, 2592.0), pad_y=32.98, yaw=0.0, role="lighthouse",
        plan="/tmp/settle/build/lh-north/lh-north.plan",
        plan_sha256=("7ca8e38f1ccd546d191db589d155d98b647039a962be61e90ebdf"
                     "a0a79b071ad"),
        body=dict(filename="drake-lighthouse.blueprint",
                  sha256=("8f531ce29c330c7bc8d367047c2bfcde31bfb5afa42f499cd"
                          "edc3f4c3eee2fac")),
        # From seq 2164, and both are `build.check_radius`'s own output: the
        # body reaches 7.028 m from the anchor and 7.28 m is the midpoint of
        # the gap above its outermost ring, so every piece is unambiguously
        # inside and no ring sits ON the boundary.
        count_radius_m=7.28, reach_m=7.028, partial_seq=2164,
        stray_radius_m=14.0,
        # `dock-northcape`, built and verified at seq 2127-2143: the 48 m pier
        # (20/24/28/32/40 m were ALL refused by the aimer), the berthed
        # CargoShip, and the `x-north` portal whose SITE end was re-seated
        # onto the deck because its first seat stood 0.675 m UNDER
        # c_WaterLevel 30.0 -- an arch standing in the surf, on the only
        # access to a site 2.8 km from the nearest built road.
        dock=dict(site="dock-northcape", tag="x-north",
                  plan="settlements/out/dock-northcape.commands.txt",
                  pier_xz=(-174.4, 2624.58), pier_radius_m=32.6,
                  pier_prefabs={"wood_floor": 75, "wood_pole": 25,
                                "wood_pole_log_4": 27},
                  root=(-192.0, 2612.0), bearing_deg=60.0, length_m=48.0,
                  boat=dict(prefab="CargoShip", xz=(-136.62, 2653.21),
                            yaw_deg=55.0, radius_m=12.0),
                  portal_site=(-184.3, 30.597, 2614.95),
                  portal_hub=(-277.62, 37.1, 208.02)),
        # The same body stands at three other sites.  `stone_wall_1x1` is the
        # prefab seq 2166's census found NONE of while the plan's first 272
        # lines hold 58, so it is probed at a FINISHED sibling: 81 standing
        # there proves the prefab spawns, and lh-north's missing ones are
        # simply commands the wedged wire never sent.
        sibling=dict(site="lh-south", pad=(-106.0, -900.0), radius_m=7.28,
                     probe_prefab="stone_wall_1x1", probe_count=81,
                     total=1255),
    ),
}


def lh_spec(site: str) -> dict:
    """The site's spec, with the plan file's digest CHECKED against the one
    the partial record placed.  Re-placing a different body under the same
    name would turn a repair into a second defect."""
    spec = LIGHTHOUSE.get(site)
    if not spec:
        raise SystemExit(f"{site!r} is not a lighthouse this module owns; "
                         f"known: {sorted(LIGHTHOUSE)}")
    path = Path(spec["plan"])
    if not path.exists():
        raise SystemExit(
            f"the spawn plan {path} is GONE. It is the body this pass places "
            f"and its digest is recorded in seq {spec['partial_seq']}; "
            f"rebuild it with settlements/build.py rather than placing "
            f"something else under the same name.")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != spec["plan_sha256"]:
        raise SystemExit(
            f"{path} digests {got} and seq {spec['partial_seq']} placed "
            f"{spec['plan_sha256']}. These are different bodies.")
    return dict(spec, site_id=site)


def plan_lines(spec: dict) -> list[str]:
    return [ln.strip() for ln in
            Path(spec["plan"]).read_text("utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def plan_prefabs(spec: dict) -> dict[str, int]:
    import verify_placement as VP  # noqa: PLC0415
    out: dict[str, int] = {}
    for prefab, *_rest in VP.plan_rows(Path(spec["plan"])):
        out[prefab] = out.get(prefab, 0) + 1
    return dict(sorted(out.items()))


def sink_gate(note: str) -> dict:
    """MEASURE the console sink from the HOST, drain it if it is drainable,
    and return the evidence.

    Host-side on purpose: `docker exec` is exactly the call that blocks while
    the sink is stalled, and MEASURED twice tonight the in-container probe
    then reports `fds_lost` -- whose named remedy is a 4-7 minute restart --
    for a wait that a two-second read of supervisord's own read end releases
    with the game process untouched.  `keep_sink_clear` raises only for the
    shape a drain cannot fix, so this is a loop's safety valve and not a stop.
    """
    import rcon as RC  # noqa: PLC0415
    before = RC.host_sink_state()
    action = RC.keep_sink_clear(note=note)
    out = dict(note=note, verdict_before=before["verdict"],
               wchans=list(before.get("wchans") or []),
               pids=before.get("pids"), read_fds=before.get("read_fds"),
               action=action.get("action"),
               verdict_after=action.get("verdict"),
               drain_seconds=action.get("seconds"),
               method=before.get("method"), tool=before.get("tool"))
    if action.get("action") != "none":
        print(f"    [sink] {note}: {before['verdict']} -> "
              f"{action.get('verdict')} by {action.get('action')} in "
              f"{action.get('seconds')} s", flush=True)
    return out


def cube_offset(o: dict, cx: float, cy: float, cz: float) -> float:
    """The half-extent a `deleteObjects -near` cube needs to contain `o`.

    A CUBE in all three axes, so it is the max of the three axis offsets and
    not a distance -- MEASURED in roads/clear.py, where a 20 m box answered
    with a trunk 21.9 m away in XZ whose every axis offset was under 20.
    """
    return max(abs(o["x"] - cx), abs(o["y"] - cy), abs(o["z"] - cz))


def lh_census(srv, spec: dict, *, box_m: float = 18.0) -> dict:
    """WHAT STANDS at the pad, live, split into this body's pieces and
    everything else, with the distance to the nearest foreign object.

    UNSCOPED, so it cannot be blind to a prefab nobody wrote down, over a
    CONTIGUOUS y stack because the body is 48 m tall, and deduplicated by
    prefab + WORLD POSITION.
    """
    px, pz = spec["pad"]
    body = set(plan_prefabs(spec))
    rep = audit(srv, px - box_m, px + box_m, pz - box_m, pz + box_m,
                y_centre=spec["pad_y"], y_levels=TOWER_Y_LEVELS)
    mine, foreign = [], []
    for o in rep["objects"]:
        d = math.hypot(o["x"] - px, o["z"] - pz)
        row = dict(prefab=o["prefab"], x=round(o["x"], 3), y=round(o["y"], 3),
                   z=round(o["z"], 3), dist_xz_m=round(d, 3))
        # A body piece is one of the plan's prefabs standing INSIDE the body's
        # own measured reach. The pier 19 m away uses three of the same
        # prefabs, so the prefab name alone cannot decide this.
        (mine if (o["prefab"] in body and d <= spec["reach_m"] + 0.5)
         else foreign).append(row)
    per: dict[str, int] = {}
    for r in mine:
        per[r["prefab"]] = per.get(r["prefab"], 0) + 1
    return dict(
        pad=[px, pz], box_m=box_m, y_levels=list(TOWER_Y_LEVELS),
        standing_total=len(mine), standing_per_prefab=dict(sorted(per.items())),
        standing=mine, foreign=foreign,
        foreign_per_prefab={p: sum(1 for r in foreign if r["prefab"] == p)
                            for p in sorted({r["prefab"] for r in foreign})},
        nearest_foreign_xz_m=min((r["dist_xz_m"] for r in foreign),
                                 default=None),
        engine_per_prefab=rep["engine_per_prefab"],
        unlisted_boxes=rep["unlisted_boxes"], socket_calls=rep["socket_calls"],
        census_method=rep["method"],
        tool="tools/jumpstart/settlements/waterfront.py::lh_census")


def retire_geometry(spec: dict, census: dict) -> dict:
    """The ONE cube every retire in this pass aims at, sized from the pieces
    that ARE standing and checked against everything that is not ours.

    One shared cube rather than one per prefab: it must already cover the
    body's 7.03 m reach in XZ, so its vertical half-extent covers the whole
    standing stack anyway, and one geometry makes the emptiness check
    afterwards a single question with a single possible answer.
    """
    px, pz = spec["pad"]
    mine = census["standing"]
    if not mine:
        raise SystemExit(
            "the live census found NO body piece at the pad. An empty answer "
            "is not a zero here -- it is either an already-clean site or a "
            "blind census -- so this stops rather than emitting a demolition "
            "aimed at nothing. Check `unlisted_boxes` and the sink verdict.")
    ys = [r["y"] for r in mine]
    yc = round((min(ys) + max(ys)) / 2.0, 3)
    need = max(cube_offset(r, px, yc, pz) for r in mine)
    r_cube = round(math.ceil((need + RETIRE_MARGIN_M) * 10.0) / 10.0, 2)
    if r_cube > 20.0:
        raise SystemExit(
            f"the standing pieces need a {r_cube:g} m cube and the schema caps "
            f"a retire at 20 m, because `deleteObjects` echoes 155 bytes per "
            f"deleted object. Retire in sub-cubes instead.")
    # THE FOREIGN CHECK, in the cube's geometry for the delete and in the
    # cylinder's for the count that proves the site empty afterwards.
    intruders = [r for r in census["foreign"]
                 if r["prefab"] in census["standing_per_prefab"]
                 and cube_offset(r, px, yc, pz) <= r_cube + RETIRE_CLEAR_M]
    if intruders:
        raise SystemExit(
            f"REFUSED: {len(intruders)} object(s) that are NOT this body's "
            f"stand inside the {r_cube:g} m delete cube plus its "
            f"{RETIRE_CLEAR_M:g} m clearance and share a prefab with it: "
            f"{intruders[:4]}. A demolition that takes a neighbour's piece is "
            f"not a repair.")
    # THE EMPTINESS CYLINDER IS NOT THE CUBE, and conflating them was a real
    # bug in this function caught on a synthetic census: a piece at dx=3,
    # dz=3 needs a cube half-extent of only 3.0 but sits 4.243 m away
    # RADIALLY, so a 3.5 m cylinder would have verified "gone" over a disc
    # that never contained it. `objects_count` measures an XZ cylinder, so
    # the radius has to cover the pieces' radial spread, not their axis
    # offsets.
    spread = max(math.hypot(r["x"] - px, r["z"] - pz) for r in mine)
    r_empty = round(max(r_cube, spread + 0.2), 2)
    in_cyl = [r for r in census["foreign"] if r["dist_xz_m"] <= r_empty + 0.5]
    if in_cyl:
        nearest = min(r["dist_xz_m"] for r in in_cyl)
        r_empty = round(nearest - 0.5, 2)
        if r_empty <= spread + 0.1:
            raise SystemExit(
                f"REFUSED: a foreign object stands {nearest:g} m from the pad "
                f"in XZ while this body's pieces spread to {spread:.3f} m, so "
                f"no cylinder can read EMPTY without either missing a piece "
                f"or counting somebody else's: {in_cyl[:4]}")
    return dict(centre=[px, yc, pz], cube_half_m=r_cube,
                empty_radius_m=r_empty, piece_spread_xz_m=round(spread, 3),
                y_range=[round(min(ys), 3), round(max(ys), 3)],
                needed_half_m=round(need, 3),
                foreign_inside_empty_cylinder=len(in_cyl),
                nearest_foreign_xz_m=census["nearest_foreign_xz_m"],
                method=("MEASURED: the cube half-extent is the largest of the "
                        "three axis offsets over every standing piece plus "
                        f"{RETIRE_MARGIN_M:g} m, because `deleteObjects "
                        "-near` is a cube in all three axes; the emptiness "
                        "radius is an XZ cylinder, which is what "
                        "`objects_count` actually measures."))


def retire_ops(spec: dict, census: dict, geom: dict) -> list[dict]:
    """One `retire` per standing prefab, per the seq 73 precedent, aimed at the
    measured cube and verified by counting that prefab to ZERO.

    Ascending by count: the cheapest reply goes first, so the procedure is
    proven on a one-object echo before the 168-object one is sent.
    """
    px, yc, pz = geom["centre"]
    r = geom["cube_half_m"]
    ops = []
    for prefab, n in sorted(census["standing_per_prefab"].items(),
                            key=lambda kv: (kv[1], kv[0])):
        rows = [q for q in census["standing"] if q["prefab"] == prefab]
        ops.append(dict(
            op="retire",
            params=dict(
                prefab=prefab, pos=[px, yc, pz], radius_m=r,
                retires=[spec["partial_seq"]],
                reason=(
                    f"{n} {prefab} of drake-lighthouse.blueprint stand at the "
                    f"lh-north pad from spawn_plan seq {spec['partial_seq']}, "
                    f"which raised TimeoutError part way through its 28 "
                    f"batches when the game's stdout pipe backed up and the "
                    f"main thread stopped answering RCON. That record's "
                    f"expect is a full-body count of 1255 at tolerance 0, so "
                    f"re-emitting it would DOUBLE these pieces and then fail "
                    f"its own postcondition on 1527. The body is retired "
                    f"whole and re-placed whole in this one token hold, which "
                    f"is ledger seq 73's precedent. A half-built lighthouse "
                    f"is the one state the operator should never find."),
                role=spec["role"], site_id=spec["site_id"]),
            wire=[f"deleteObjects -prefab {prefab} -near {px:.2f} {yc:.2f} "
                  f"{pz:.2f} {r:.2f} -force"],
            requires=dict(mods=["WorldEditCommands"], prefabs=[prefab],
                          blobs=[]),
            expect=dict(absent=[dict(prefab=prefab,
                                     pos=[round(px, 2), round(pz, 2)],
                                     max=geom["empty_radius_m"])]),
            meta=dict(
                counted_before=n, cube=geom,
                echo_bytes_estimate=n * SINK_ECHO_BYTES,
                positions=[[q["x"], q["y"], q["z"]] for q in rows],
                position_key=("prefab + WORLD POSITION: ZDO ids are "
                              "REASSIGNED on world load, so the id in a "
                              "census is not a durable key and the positions "
                              "are what make this demolition auditable"),
                terrain_written=("NONE -- this pass writes no terrain at all, "
                                 "so no sample can fall below c_WaterLevel "
                                 "30.0; the pad from seq 2163 stands and a "
                                 "second write to its zones would destroy the "
                                 "first one's terrain"),
                why=("radius is the MEASURED cube half-extent, not a "
                     "convention: `deleteObjects -near` is a cube in all "
                     "three axes, and the nearest object at this pad that is "
                     "NOT this body measures "
                     f"{census['nearest_foreign_xz_m']} m away in XZ "
                     "(dock-northcape's pier), so this cannot take a "
                     "neighbour's piece"))))
    return ops


def spawn_batches(spec: dict, lines: list[str], *,
                  pieces: int = SPAWN_BATCH_PIECES, baseline: int = 0,
                  rows: list | None = None, tag: str = "batch") -> list[dict]:
    """The body as `ceil(len(lines)/pieces)` `spawn_plan` records, IN PLAN
    ORDER.

    Plan order is bottom-up in Y (`to_rcon_plan`), so WearNTear support exists
    under every piece as it lands; batching must not reorder it.  Each
    record's postcondition is the CUMULATIVE count inside the exact radius --
    exact because the site is proven EMPTY first and the nearest foreign
    object is 17.6 m away.  1,255 pieces at 1-2 m spacing cannot each be given
    a disjoint disc, so this is the documented case where a cumulative
    run-count is the only exact instrument available.

    `baseline` is the count ALREADY STANDING inside the count radius, and it
    is what makes a resume honest rather than optimistic: MEASURED, this pass
    was killed by its harness after two batches had landed, and a resume that
    assumed a baseline of zero would have asserted 64 where 192 was correct
    and failed every remaining record. It must be a live measurement, never
    the arithmetic of which records were appended: an appended record whose
    wire may or may not have been sent is exactly the state a resume exists to
    resolve.  `rows` accompanies a filtered `lines` list so the per-batch
    prefab histogram still matches the commands being sent.
    """
    import place as PLACE  # noqa: PLC0415
    import verify_placement as VP  # noqa: PLC0415
    px, pz = spec["pad"]
    rows = list(rows) if rows is not None else VP.plan_rows(Path(spec["plan"]))
    if len(rows) != len(lines):
        raise SystemExit(f"the plan parses {len(rows)} rows for {len(lines)} "
                         f"command lines; refusing to batch a plan whose "
                         f"parse and text disagree")
    budget = PLACE.batch_budget(SPAWN_WIRE_BYTES)
    chunks = [(lines[i:i + pieces], rows[i:i + pieces])
              for i in range(0, len(lines), pieces)]
    ops, done = [], baseline
    for i, (chunk, chunk_rows) in enumerate(chunks, start=1):
        wire = [";".join(g) for g in PLACE.batches(chunk, budget)]
        digest = hashlib.sha256(("\n".join(wire) + "\n").encode()).hexdigest()
        per: dict[str, int] = {}
        for prefab, *_r in chunk_rows:
            per[prefab] = per.get(prefab, 0) + 1
        done += len(chunk)
        ops.append(dict(
            op="spawn_plan",
            params=dict(
                plan_sha256=digest, plan_ref=f"{spec['plan']}#{tag}{i}",
                anchor=dict(x=px, y=spec["pad_y"], z=pz, yaw=spec["yaw"]),
                command_count=len(chunk), prefabs=dict(sorted(per.items())),
                body=spec["body"], align="floor-center",
                reach_m=spec["reach_m"], pad_height=spec["pad_y"],
                batch_bytes=SPAWN_WIRE_BYTES,
                datum=f"pad top at target_y {spec['pad_y']} m",
                role=spec["role"], site_id=spec["site_id"]),
            wire=wire,
            requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                          prefabs=sorted(per), blobs=[digest]),
            expect=dict(objects_count=dict(
                ids="*", ignore="_*,sfx_*,vfx_*",
                pos=[round(px, 2), round(pz, 2)], max=spec["count_radius_m"],
                total=done, tolerance=0)),
            meta=dict(
                batch=i, of=len(chunks), pieces=len(chunk), tag=tag,
                baseline_pieces=baseline,
                cumulative_pieces=done, round_trips=len(wire),
                full_body=dict(plan_sha256=spec["plan_sha256"],
                               commands=len(lines),
                               body=spec["body"]["filename"]),
                sink_budget=dict(
                    pipe_bytes=SINK_PIPE_BYTES,
                    echo_bytes_per_line=SINK_ECHO_BYTES,
                    worst_case_bytes=len(chunk) * SINK_ECHO_BYTES,
                    fraction_of_pipe=round(len(chunk) * SINK_ECHO_BYTES
                                           / SINK_PIPE_BYTES, 3)),
                why=(f"batch {i} of {len(chunks)}. The body is placed in "
                     f"{pieces}-piece records because ONE 1,255-command "
                     f"record (seq {spec['partial_seq']}) filled the game's "
                     f"stdout pipe and stopped the main thread answering "
                     f"RCON: at 155 echoed bytes per line that is ~194 KB "
                     f"against a 65,536-byte pipe with no drain window "
                     f"anywhere inside it. This record is at most "
                     f"{len(chunk) * SINK_ECHO_BYTES} B, and the sink is "
                     f"MEASURED from the host and the main thread probed "
                     f"BEFORE it is sent. The postcondition is cumulative "
                     f"because 1,255 pieces at 1-2 m spacing cannot each hold "
                     f"a disjoint disc, and it is exact because the site was "
                     f"proven empty first."))))
    return ops


def lh_remaining(spec: dict, census: dict, *, tol_m: float = 0.15) -> dict:
    """WHICH PLAN LINES ARE NOT YET STANDING, matched PER PIECE by prefab +
    world position.

    THIS IS WHY THE PASS IS RESUMABLE WITHOUT GUESSING.  MEASURED tonight:
    the placement run was killed by its own harness after two of twenty
    records had landed, and the ledger then held an appended `spawn_plan`
    whose wire may or may not have been sent -- which is precisely the
    question a record cannot answer about itself.  A per-piece diff answers
    it from the world: every one of the 1,255 plan lines has a distinct
    target position, so a line is either occupied by a piece of its own
    prefab within `tol_m` or it is not, and only the ones that are not get
    re-sent.  That makes a resume neither a double-place nor a gap.

    A STANDING BODY PIECE THAT NO PLAN LINE CLAIMS IS A REFUSAL, not a
    rounding difference: it is either a duplicate from a re-sent command or
    something else standing inside the body's footprint, and both change what
    the final count means.
    """
    import verify_placement as VP  # noqa: PLC0415
    rows = VP.plan_rows(Path(spec["plan"]))
    lines = plan_lines(spec)
    live = list(census["standing"])
    taken = [False] * len(live)
    todo_lines, todo_rows, matched = [], [], 0
    for line, row in zip(lines, rows):
        prefab, x, y, z, _yaw = row
        best, at = None, None
        for k, o in enumerate(live):
            if taken[k] or o["prefab"] != prefab:
                continue
            d = max(abs(o["x"] - x), abs(o["y"] - y), abs(o["z"] - z))
            if d <= tol_m and (best is None or d < best):
                best, at = d, k
        if at is None:
            todo_lines.append(line)
            todo_rows.append(row)
        else:
            taken[at] = True
            matched += 1
    orphans = [live[k] for k in range(len(live)) if not taken[k]]
    per: dict[str, int] = {}
    for prefab, *_r in todo_rows:
        per[prefab] = per.get(prefab, 0) + 1
    return dict(
        plan_total=len(lines), standing=len(live), matched=matched,
        remaining=len(todo_lines), remaining_per_prefab=dict(sorted(per.items())),
        orphan_standing=len(orphans), orphans=orphans[:12],
        tolerance_m=tol_m, lines=todo_lines, rows=todo_rows,
        method=("MEASURED per piece: every plan line's target position "
                "matched against the live census by prefab and by max axis "
                "offset within "
                f"{tol_m} m, each live piece claimable once. The remainder is "
                "the set of lines with no piece standing at them, so a resume "
                "can neither duplicate a placed piece nor skip a missing "
                "one. ZDO ids are not used: they are reassigned on world "
                "load."),
        tool="tools/jumpstart/settlements/waterfront.py::lh_remaining")



def cube_rows(srv, prefab: str, x: float, y: float, z: float,
              half: float) -> dict:
    """One `findObjects -prefab P -near x y z h` CUBE, halved until the
    reply's OWN HEADER is under the row cap, parsed for positions.

    A cube rather than `bounded_detailed`'s disc, on purpose: the question
    here is how high the finished tower stands, and a disc counts the whole
    48-piece roof column while a cube centred on the cap holds only the cap.
    The HEADER decides, never the rows parsed -- a truncated reply must never
    read as a short list.
    """
    CL = roads_clear()
    hh = half
    while True:
        reply = CL.ask(srv, f"findObjects -prefab {prefab} -near {x:.2f} "
                            f"{y:.2f} {z:.2f} {hh:.2f} -detailed")
        m = CL.FOUND_RE.search(reply)
        n = int(m.group(1)) if m else 0
        if n <= LIST_ROW_CAP or hh <= 0.4:
            break
        hh = round(hh / 2.0, 3)
    rows = [dict(prefab=mm.group("prefab"), x=float(mm.group("x")),
                 y=float(mm.group("y")), z=float(mm.group("z")))
            for mm in CL.POS_RE.finditer(reply)]
    return dict(prefab=prefab, at=[round(x, 2), round(y, 2), round(z, 2)],
                cube_half_m=hh, header_rows=n, listed=len(rows), rows=rows,
                truncated=bool(n > LIST_ROW_CAP),
                y_min=(None if not rows else round(min(r["y"] for r in rows), 3)),
                y_max=(None if not rows else round(max(r["y"] for r in rows), 3)),
                probe=(f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} "
                       f"{z:.2f} {hh:.2f} -detailed"))


def lh_height(spec: dict) -> dict:
    """THE HEIGHT, off the body's own measured colliders -- and the beacon's
    seat, which is the error this project has already paid for once.

    BEWARE THE MESH AABB.  `bonfire` carries THREE solids: the pyre's log ring
    at local y -0.496..+0.531, a 4.8 m box at -2.130..+2.670, and a 1.66 m
    flame column to +4.149.  The UNION says the beacon's bottom is 2.130 m
    below its pivot, which reads as a beacon floating over its plinth; the
    STRUCTURAL solid is the log ring.  `fire_pit` has the identical shape -- a
    0.2 m hearth pan plus a 2 m fire volume -- so this is the family's
    convention and not a one-off.  Beacons were once placed 5.173 m in the air
    because 15.402 m was a tower's mesh bounding box while its parapet
    platform sat at 10.229 m; the datum is the surface a player stands on plus
    the piece's own measured offset.
    """
    import base_geometry as BG  # noqa: PLC0415
    import verify_placement as VP  # noqa: PLC0415
    geom = BG.geometry()
    objs = VP.plan_objects(Path(spec["plan"]))
    pad_y = spec["pad_y"]
    sea = float(getattr(W, "WATER_LEVEL", 30.0))

    def span(o, solids=None) -> tuple[float, float]:
        s = solids if solids is not None else geom._prefabs.get(o.prefab)
        q = BG._normalised(tuple(o.rot))
        ys = [BG._qrot(q, c)[1] for so in s for c in BG._solid_corners(so)]
        return o.pos[1] + min(ys), o.pos[1] + max(ys)

    solid = [o for o in objs if geom._prefabs.get(o.prefab)]
    missing = sorted({o.prefab for o in objs} - {o.prefab for o in solid})
    cap_y, cap = max(((span(o)[1], o) for o in solid), key=lambda t: t[0])
    floors = [(span(o)[1], o) for o in objs
              if o.prefab in ("wood_floor", "stone_floor_2x2")]
    gallery_y, gallery = max(floors, key=lambda t: t[0])
    entry_y = min(y for y, o in floors if o.prefab == "stone_floor_2x2")
    beacon_rows = []
    for b in [o for o in objs if o.prefab in ("bonfire", "fire_pit")]:
        # The structural solid is the SHORTEST of the prefab's solids in y:
        # the pyre ring / hearth pan. The tall ones are the fire and light
        # volumes, and seating on their union is the mesh-AABB error.
        ring = min(geom._prefabs[b.prefab],
                   key=lambda s: (max(p[1] for p in BG._solid_corners(s))
                                  - min(p[1] for p in BG._solid_corners(s))))
        base_union = span(b)[0]
        base_struct = span(b, [ring])[0]
        under = []
        for o in objs:
            if o is b or not geom._prefabs.get(o.prefab):
                continue
            if math.hypot(o.pos[0] - b.pos[0], o.pos[2] - b.pos[2]) > 2.4:
                continue
            hi = span(o)[1]
            if hi <= base_struct + 0.25:
                under.append((hi, o.prefab))
        under.sort(reverse=True)
        beacon_rows.append(dict(
            prefab=b.prefab, at=[round(b.pos[0], 3), round(b.pos[1], 3),
                                 round(b.pos[2], 3)],
            structural_base_y=round(base_struct, 3),
            union_aabb_base_y=round(base_union, 3),
            plinth_top_y=None if not under else round(under[0][0], 3),
            plinth_prefabs=sorted({p for _h, p in under[:8]}),
            engagement_m=(None if not under
                          else round(under[0][0] - base_struct, 3)),
            false_air_gap_if_union_used_m=(
                None if not under else round(under[0][0] - base_union, 3)),
            above_gallery_platform_m=round(base_struct - gallery_y, 3),
            above_sea_m=round(base_struct - sea, 3),
            seated=bool(under and abs(under[0][0] - base_struct) <= 0.25)))
    return dict(
        pad_target_y=pad_y, sea_level_m=sea,
        entry_floor_top_y=round(entry_y, 3),
        gallery_platform_top_y=round(gallery_y, 3),
        gallery_platform_prefab=gallery.prefab,
        gallery_platform_origin_y=round(gallery.pos[1], 3),
        roof_cap_top_y=round(cap_y, 3), roof_cap_prefab=cap.prefab,
        roof_cap_origin_y=round(cap.pos[1], 3),
        roof_cap_xz=[round(cap.pos[0], 3), round(cap.pos[2], 3)],
        height_above_entry_floor_m=round(cap_y - entry_y, 3),
        height_above_pad_m=round(cap_y - pad_y, 3),
        height_above_sea_m=round(cap_y - sea, 3),
        gallery_above_sea_m=round(gallery_y - sea, 3),
        prefabs_without_geometry=missing, beacons=beacon_rows,
        method=("MEASURED off the blueprint's own collider solids "
                "(blueprints/base_geometry.py, from the ZNetScene dump) "
                "placed at this pad's datum. The cap is the union AABB's top; "
                "the beacon's seat is its STRUCTURAL solid, never the union, "
                "because a bonfire's union reaches 2.130 m below its pivot on "
                "account of its fire volume."),
        tool="tools/jumpstart/settlements/waterfront.py::lh_height")


def lh_grounding(spec: dict) -> dict:
    """The grounded fraction with the SUNK / OVERHANG split, against the pad.

    `build.grounding` answers the fraction and the extremes; the split is the
    part an operator can act on, because the two cases look nothing alike. A
    column whose lowest solid is BELOW the pad is masonry embedded in the pad;
    one whose lowest solid is ABOVE it is a cantilever with air under it. A
    bare "grounded fraction 0.03" on this body would read as a floating
    building when what it describes is a foundation ring seated 2.00 m into
    its own pad, and the number that would be a real defect is a column whose
    lowest piece hangs just above the ground with nothing beneath it.
    """
    import base_geometry as BG  # noqa: PLC0415
    import build as B  # noqa: PLC0415
    import verify_placement as VP  # noqa: PLC0415
    path = Path(spec["plan"])
    pad_y = spec["pad_y"]
    floor = VP.bottoming(VP.plan_objects(path), BG.geometry())
    tol = B.GROUND_TOL_M
    gaps = sorted(y - pad_y for _n, y in floor.values())
    sunk = [g for g in gaps if g < -tol]
    over = [g for g in gaps if g > tol]
    flush = [g for g in gaps if -tol <= g <= tol]
    return dict(
        columns=len(gaps), tolerance_m=tol,
        # ONE convention, and it is `build.grounding`'s: a column is grounded
        # when its lowest solid sits at or BELOW pad + tol, i.e. on the pad or
        # embedded in it. Defining a second, stricter "flush only" fraction
        # here would give this project two grounding instruments that disagree
        # by 0.73 on the same body, which is how two clearance instruments
        # happened. The flush count is reported as a count, not as a rate.
        grounded_fraction=round((len(flush) + len(sunk)) / len(gaps), 3),
        flush_columns=len(flush),
        sunk_columns=len(sunk), deepest_below_pad_m=round(gaps[0], 3),
        overhang_columns=len(over),
        lowest_overhang_above_pad_m=(None if not over else round(min(over), 3)),
        worst_air_gap_m=round(gaps[-1], 3),
        floating_near_ground_columns=sum(1 for g in over if g <= 4.0),
        build_py=B.grounding(path, pad_y),
        method=("MEASURED with verify_placement.bottoming over 1 m columns: "
                "the LOWEST solid in each column against the pad datum. Sunk "
                "is below pad-tol (foundation embedded in the pad), overhang "
                "is above pad+tol (a cantilever, and this body's are the "
                "lantern gallery and the eaves)."),
        tool="tools/jumpstart/settlements/waterfront.py::lh_grounding")


def lh_live_verify(srv, spec: dict, census: dict) -> dict:
    """The finished body, counted live: EVERY prefab against the plan inside
    the radius the count can be EXACT in, plus the strays outside it.

    The stray figure subtracts the foreign objects the pre-build census
    already measured in the annulus, so a neighbour's pile head cannot be
    reported as a piece this body flung out of its own footprint.
    """
    px, pz = spec["pad"]
    want = plan_prefabs(spec)
    r = spec["count_radius_m"]
    wide = spec["stray_radius_m"]
    total, per = srv.count("*", px, pz, r, ignore="_*,sfx_*,vfx_*")
    rows = []
    for prefab, n in want.items():
        got, _ = srv.count(prefab, px, pz, r)
        rows.append(dict(prefab=prefab, want=n, got=got, ok=(got == n)))
    wide_total, wide_per = srv.count("*", px, pz, wide,
                                     ignore="_*,sfx_*,vfx_*")
    known_foreign = [f for f in census["foreign"]
                     if r < f["dist_xz_m"] <= wide]
    strays = wide_total - total - len(known_foreign)
    bad = [q for q in rows if not q["ok"]]
    return dict(
        count_radius_m=r, total_want=sum(want.values()), total_got=total,
        total_ok=(total == sum(want.values())),
        per_prefab=rows, mismatched=bad, prefabs_ok=all(q["ok"] for q in rows),
        live_per_prefab=per,
        stray_radius_m=wide, stray_total_in_wide=wide_total,
        known_foreign_in_annulus=len(known_foreign),
        known_foreign=known_foreign, strays=strays, strays_ok=(strays == 0),
        method=(f"MEASURED live: `objects_count id=* ignore=_*,sfx_*,vfx_*` "
                f"at {r} m -- the gap-midpoint radius build.check_radius "
                f"computed, so every piece is unambiguously inside and no "
                f"ring sits ON the boundary where the filter's comparison "
                f"MEASURED 2 of 4 pieces at exactly 1.000 m -- then one "
                f"scoped count per prefab at the same radius, and the same "
                f"star count at {wide} m to expose anything placed outside "
                f"the body's {spec['reach_m']} m reach, minus the foreign "
                f"objects the pre-build census had already measured there. "
                f"sfx_/vfx_ are excluded because a fuelled piece spawns its "
                f"own one-shot effects and an exact count taken immediately "
                f"after placement would race them."),
        tool="tools/jumpstart/settlements/waterfront.py::lh_live_verify")


def pier_head(spec: dict) -> dict:
    """The pier's HEAD, taken from the deck tiles the pier plan actually
    places rather than from root + length: the aimer trims a deck to the wet
    fraction it accepted, so the authored head is a measurement."""
    d = spec["dock"]
    path = JUMPSTART / d["plan"]
    if not path.exists():
        path = HERE / Path(d["plan"]).name
    rx, rz = d["root"]
    best = None
    for line in path.read_text("utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "spawn_object":
            continue
        pos = next((p for p in parts if p.startswith("pos=")), None)
        if pos is None or parts[1] != "wood_floor":
            continue
        z, x, y = (float(v) for v in pos[4:].split(","))
        dd = math.hypot(x - rx, z - rz)
        if best is None or dd > best[0]:
            best = (dd, x, y, z)
    if best is None:
        raise SystemExit(f"no wood_floor deck tile in {path}; the pier head "
                         f"cannot be measured off its own plan")
    return dict(plan=str(path), along_m=round(best[0], 3),
                xz=[round(best[1], 3), round(best[3], 3)],
                deck_y=round(best[2], 3))


def dock_confirm(srv, spec: dict) -> dict:
    """THE DOCK, THE BERTH AND THE PORTAL, re-measured rather than cited.

    The pier, the vessel and both ends of `x-north` were placed at seq
    2127-2143; this pass owns the lighthouse above them and confirms the
    access, because the site is portal-only and ~2.8 km from the nearest built
    road -- a one-ended tag strands the operator at the top of the map, and
    that is the operator's original reported defect.

    THE PIER COUNT IS A COMPOSITE ONE AND IT HAS TO BE, which is a defect this
    function shipped with and MEASURED live the moment the tower was finished:
    a scoped count of `wood_floor` in the pier's own 32.6 m cylinder answered
    103 for 75, and `wood_pole` 63 for 25.  Nothing was wrong with the pier.
    The lighthouse pad is 33.455 m from the pier's anchor, the body reaches
    7.028 m, so 423 of its 1,255 pieces -- including 28 `wood_floor`, 38
    `wood_pole` and 6 `wood_pole_log_4` -- stand INSIDE that cylinder, and no
    single cylinder can hold the whole 48 m pier while excluding the tower.
    So the expected count is the pier's own plan PLUS the lighthouse rows that
    fall inside the same disc, computed from the plan this pass has just
    verified standing piece for piece, and the pier-only figure is the
    difference.  A check that answers a question it is not measuring is the
    most expensive thing in this project; this one names both structures.
    """
    d = spec["dock"]
    import verify_placement as VP  # noqa: PLC0415
    overlap: dict[str, int] = {}
    for prefab, x, _y, z, _yaw in VP.plan_rows(Path(spec["plan"])):
        if math.hypot(x - d["pier_xz"][0], z - d["pier_xz"][1]) <= d["pier_radius_m"]:
            overlap[prefab] = overlap.get(prefab, 0) + 1
    rows = []
    for prefab, n in sorted(d["pier_prefabs"].items()):
        got, _ = srv.count(prefab, d["pier_xz"][0], d["pier_xz"][1],
                           d["pier_radius_m"])
        extra = overlap.get(prefab, 0)
        rows.append(dict(prefab=prefab, pier_want=n,
                         lighthouse_in_disc=extra, want=n + extra, got=got,
                         pier_only=got - extra, ok=(got == n + extra)))
    boat = d["boat"]
    bgot, _ = srv.count(boat["prefab"], boat["xz"][0], boat["xz"][1],
                        boat["radius_m"])
    head = pier_head(spec)
    surf = Surface()
    sea = float(getattr(W, "WATER_LEVEL", 30.0))
    hx, hz = head["xz"]
    happ, hgen, hdel = surf.at(hx, hz)
    hull = hull_depths(surf, boat["yaw_deg"], boat["xz"][0], boat["xz"][1],
                       boat["prefab"])
    disc = berth(surf, boat["xz"][0], boat["xz"][1], boat["prefab"],
                 radius_m=boat["radius_m"])
    v = VESSELS[boat["prefab"]]
    need = v["draught_m"] + KEEL_CLEARANCE_M
    ends = tag_ends(d["tag"])
    for e in ends:
        e["end"] = ("site" if math.hypot(e["x"] - d["portal_site"][0],
                                         e["z"] - d["portal_site"][2]) < 2.0
                    else "hub")
    pair = verify_pair(srv, d["tag"], ends)
    return dict(
        pier=dict(centre=list(d["pier_xz"]), radius_m=d["pier_radius_m"],
                  length_m=d["length_m"], root=list(d["root"]),
                  bearing_deg=d["bearing_deg"], head=head,
                  head_depth_m=round(sea - happ, 3),
                  head_generated_y=round(hgen, 3),
                  head_terrain_delta_m=round(hdel, 3),
                  per_prefab=rows, ok=all(q["ok"] for q in rows),
                  pieces_want=sum(d["pier_prefabs"].values()),
                  pieces_in_disc=sum(q["got"] for q in rows),
                  lighthouse_pieces_in_disc=sum(overlap.values()),
                  pieces_got=sum(q["pier_only"] for q in rows)),
        berth=dict(prefab=boat["prefab"], at=list(boat["xz"]),
                   yaw_deg=boat["yaw_deg"], radius_m=boat["radius_m"],
                   count=bgot, ok=(bgot == 1),
                   draught_m=v["draught_m"],
                   keel_clearance_m=KEEL_CLEARANCE_M, required_depth_m=need,
                   hull_min_depth_m=(None if hull is None
                                     else round(hull[0], 3)),
                   hull_max_depth_m=(None if hull is None
                                     else round(hull[1], 3)),
                   hull_open_sea=(None if hull is None else hull[2]),
                   hull_samples=(None if hull is None else hull[3]),
                   floats=(None if hull is None else bool(hull[0] >= need)),
                   disc=disc),
        portal=dict(tag=d["tag"], ends=len(ends), paired=pair["paired"],
                    readback=pair),
        method=("MEASURED live: one scoped `objects_count` per pier prefab "
                "inside the pier's own 32.6 m reach, expected as the pier's "
                "127 planned pieces PLUS the 423 lighthouse pieces whose own "
                "verified positions fall inside that same disc -- the pad is "
                "33.455 m from the pier anchor and the body reaches 7.028 m, "
                "so no cylinder holds the pier alone; `pier_only` is the "
                "difference and it is the number that says the pier is "
                "intact. The vessel counted in its 12 m berth disc; the depth "
                "recomputed as c_WaterLevel minus the APPLIED surface off the "
                "1 m PatchScan `north` patch, at the pier head taken from the "
                "pier's own plan and under the hull's elliptical collider "
                "footprint; and the portal tag read back off BOTH ZDOs with "
                "`findObjects -detailed`, because the record that wrote a tag "
                "is not evidence the tag is on the ZDO."),
        tool="tools/jumpstart/settlements/waterfront.py::dock_confirm")


def cmd_lighthouse(args) -> int:
    """Retire a half-built lighthouse and re-place it WHOLE, in batches that
    cannot wedge the console sink, then confirm its dock, berth and portal.

    The order is not negotiable and each step is a measurement the next one
    depends on:

        players online -> sink -> live census -> retire per prefab ->
        site proven EMPTY -> save -> batched placement with a sink gate and a
        liveness probe before every record -> full per-prefab verify ->
        height off the ZDOs -> dock, berth and both portal ends -> save

    `--resume` replaces the retire half with a PER-PIECE DIFF (`lh_remaining`)
    and re-sends only the plan lines with nothing standing at them, against a
    baseline MEASURED live.  It exists because this pass was killed mid
    placement once: two of twenty records had landed, the ledger held an
    appended record whose wire may or may not have gone out, and neither
    retiring 128 good pieces nor re-sending 1,255 commands is the right
    answer to that.  Resuming is only correct BECAUSE the diff is per piece;
    a resume from "which records were appended" would be a guess.

    Without `--apply` nothing is appended and nothing is sent: the geometry,
    the batch plan and the height are all computable offline and are printed.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    site = args.site or "lh-north"
    spec = lh_spec(site)
    lines = plan_lines(spec)
    want = plan_prefabs(spec)
    pieces = int(args.batch_pieces or SPAWN_BATCH_PIECES)
    batches = spawn_batches(spec, lines, pieces=pieces)
    height = lh_height(spec)
    ground = lh_grounding(spec)
    print(json.dumps(dict(
        site=site, body=spec["body"], commands=len(lines), prefabs=want,
        batches=len(batches), pieces_per_batch=pieces,
        round_trips=sum(len(o["wire"]) for o in batches),
        worst_batch_echo_bytes=max(o["meta"]["sink_budget"]["worst_case_bytes"]
                                   for o in batches),
        pipe_bytes=SINK_PIPE_BYTES, height=height, grounding=ground),
        indent=1, default=str))
    if not args.apply:
        print("    dry: nothing appended, nothing sent")
        return 0

    px, pz = spec["pad"]
    drains: list[dict] = []
    with LiveBuilder(actor=args.actor) as b:
        srv = b.srv
        players = srv.rc.players_online()
        gate = sink_gate("pass start")
        drains.append(gate)
        print(f"    players online {players}; sink {gate['verdict_before']} -> "
              f"{gate['verdict_after']}", flush=True)
        if players:
            raise SystemExit(
                f"{players} player(s) online. This pass deletes 272 standing "
                f"pieces and then places 1,255, and doing that around a "
                f"player is how somebody falls through a floor that stopped "
                f"existing. Stop, or wait for an empty server.")
        b.note(
            f"{site}: RETIRING THE HALF-BUILT BODY AND RE-PLACING IT WHOLE, "
            f"in one token hold. spawn_plan seq {spec['partial_seq']} timed "
            f"out part way through its 28 batches -- the game's stdout pipe "
            f"backed up, pid 120208 sat in pipe_write, the main thread "
            f"stopped answering RCON, and 272 of 1255 pieces were standing "
            f"when the sink was drained FROM THE HOST with no SIGKILL. That "
            f"record's expect is a full-body count at tolerance 0, so it is "
            f"NOT re-emitted: re-sending it would double the 272 and fail its "
            f"own postcondition on 1527. Instead: an unscoped live census, a "
            f"retire per standing prefab aimed at a MEASURED cube, the site "
            f"counted EMPTY, and then the body placed as {len(batches)} "
            f"records of {pieces} pieces with the sink measured from the host "
            f"and the main thread probed before every one. No terrain is "
            f"written -- the pad from seq 2163 stands, and a second write to "
            f"its zones would destroy the first one's terrain. Precedent: "
            f"ledger seq 73.", role=spec["role"], site_id=site)
        # ANNOTATION BY APPEND, never by edit: a `--note` carries whatever
        # the caller has to say about an EARLIER attempt in the same chain --
        # an abort, a refusal, a correction -- into the log next to this
        # pass's own records, which is the only sanctioned way to amend the
        # ledger.
        if getattr(args, "note", None):
            b.note(args.note, role=spec["role"], site_id=site)
        b.observe(f"sink_state::{site} pass start",
                  "MEASURED from the HOST's /proc: wchan of the container's "
                  "supervisord and syslogd plus supervisord's read ends of "
                  "syslogd's stdout pipes, matched by pipe inode. No docker "
                  "exec, which is the call that blocks while the sink is "
                  "stalled and made the in-container probe report fds_lost "
                  "for a drainable wait.",
                  "tools/jumpstart/settlements/waterfront.py::sink_gate",
                  dict(gate=gate, players_online=players),
                  role=spec["role"], site_id=site)

        # THE SIBLING PROBE: does `stone_wall_1x1` spawn at all? seq 2166's
        # census found none of it while the plan's first 272 lines hold 58.
        sib = spec["sibling"]
        sgot, _ = srv.count(sib["probe_prefab"], sib["pad"][0], sib["pad"][1],
                            sib["radius_m"])
        stot, _ = srv.count("*", sib["pad"][0], sib["pad"][1],
                            sib["radius_m"], ignore="_*,sfx_*,vfx_*")
        b.observe(f"sibling_body::{sib['site']}",
                  f"MEASURED live at the FINISHED sibling that carries the "
                  f"same blueprint: a scoped count of {sib['probe_prefab']} "
                  f"and the star count inside the same {sib['radius_m']} m "
                  f"radius. This decides whether lh-north's missing "
                  f"{sib['probe_prefab']} are a prefab that does not spawn or "
                  f"commands the wedged wire never sent.",
                  "tools/jumpstart/settlements/waterfront.py::cmd_lighthouse",
                  dict(site=sib["site"], prefab=sib["probe_prefab"],
                       want=sib["probe_count"], got=sgot,
                       body_total_want=sib["total"], body_total_got=stot),
                  role=spec["role"], site_id=site)
        print(f"    sibling {sib['site']}: {sib['probe_prefab']} "
              f"{sgot}/{sib['probe_count']}, body {stot}/{sib['total']}",
              flush=True)

        # 1. WHAT IS STANDING
        census = lh_census(srv, spec)
        print(f"    census: {census['standing_total']} body pieces, "
              f"{len(census['foreign'])} foreign, nearest foreign "
              f"{census['nearest_foreign_xz_m']} m, "
              f"{census['socket_calls']} socket calls", flush=True)
        if census["unlisted_boxes"]:
            raise SystemExit(
                f"{len(census['unlisted_boxes'])} census box(es) could not be "
                f"listed safely: {census['unlisted_boxes'][:3]}. 'I could not "
                f"measure it' is not 'it is fine', and a demolition aimed at "
                f"an incomplete census is how a piece is left standing inside "
                f"the new body.")
        b.observe(f"partial_standing::{site}",
                  census["census_method"], census["tool"],
                  dict(standing_total=census["standing_total"],
                       standing_per_prefab=census["standing_per_prefab"],
                       standing=census["standing"],
                       foreign_per_prefab=census["foreign_per_prefab"],
                       nearest_foreign_xz_m=census["nearest_foreign_xz_m"],
                       engine_per_prefab=census["engine_per_prefab"],
                       plan_total=sum(want.values()),
                       recorded_at_seq_2166=272,
                       durable_key=("prefab + WORLD POSITION; ZDO ids are "
                                    "reassigned on world load")),
                  role=spec["role"], site_id=site)

        if args.resume:
            # 2R. THE PER-PIECE DIFF, instead of a demolition. Nothing is
            # retired: the pieces standing here now are THIS pass's own,
            # placed after the site was counted empty, and re-sending a
            # command whose piece already stands is what doubles a body.
            rest = lh_remaining(spec, census)
            print(f"    resume: {rest['matched']} of {rest['plan_total']} "
                  f"plan lines already standing, {rest['remaining']} to send, "
                  f"{rest['orphan_standing']} orphan(s)", flush=True)
            b.observe(f"resume_diff::{site}", rest["method"], rest["tool"],
                      {k: v for k, v in rest.items()
                       if k not in ("lines", "rows")},
                      role=spec["role"], site_id=site)
            if rest["orphan_standing"]:
                raise SystemExit(
                    f"{rest['orphan_standing']} standing piece(s) inside the "
                    f"body's reach match NO plan line: {rest['orphans'][:4]}. "
                    f"That is either a duplicate from a re-sent command or "
                    f"something else in the footprint, and both change what "
                    f"the final count of 1255 would mean. Stop and identify "
                    f"them before sending anything.")
            if not rest["remaining"]:
                print("    resume: nothing left to place", flush=True)
            batches = spawn_batches(spec, rest["lines"], pieces=pieces,
                                    baseline=rest["matched"],
                                    rows=rest["rows"], tag="resume")
        else:
            # 2. RETIRE, one record per prefab, per seq 73
            geom = retire_geometry(spec, census)
            print(f"    delete cube: half {geom['cube_half_m']} m about "
                  f"{geom['centre']}, empty radius {geom['empty_radius_m']} m, "
                  f"pieces spread {geom['piece_spread_xz_m']} m", flush=True)
            b.observe(f"retire_geometry::{site}", geom["method"],
                      "tools/jumpstart/settlements/waterfront.py"
                      "::retire_geometry",
                      geom, role=spec["role"], site_id=site)
            ops = retire_ops(spec, census, geom)
            for i, op in enumerate(ops, start=1):
                g = sink_gate(f"before retire {i}/{len(ops)} "
                              f"{op['params']['prefab']}")
                drains.append(g)
                rtt = srv.probe()
                op["meta"]["sink_gate"] = g
                op["meta"]["probe_rtt_s"] = round(rtt, 3)
                emit(b, [op], label=f"{site} retire {i}/{len(ops)}")

            # 3. THE SITE READS EMPTY -- one question, one possible answer
            sink_gate("after retires")
            empty_total, empty_per = srv.count("*", px, pz,
                                               geom["empty_radius_m"],
                                               ignore="_*,sfx_*,vfx_*")
            b.observe(f"site_empty::{site}",
                      f"MEASURED live: `objects_count id=* "
                      f"ignore=_*,sfx_*,vfx_* pos={px:.2f},{pz:.2f} "
                      f"max={geom['empty_radius_m']:g}` -- an XZ cylinder "
                      f"that contains every one of the retired pieces (they "
                      f"spread to {geom['piece_spread_xz_m']} m) and no "
                      f"foreign object (the nearest measures "
                      f"{census['nearest_foreign_xz_m']} m). A star count to "
                      f"zero is the only honest proof of a demolition: "
                      f"deleteObjects echoes one line per object, so a reply "
                      f"that looks empty is indistinguishable from one that "
                      f"was truncated.",
                      "tools/jumpstart/settlements/waterfront.py"
                      "::cmd_lighthouse",
                      dict(radius_m=geom["empty_radius_m"], total=empty_total,
                           per_prefab=empty_per,
                           retired_total=census["standing_total"],
                           retire_records=len(ops)),
                      role=spec["role"], site_id=site)
            print(f"    site after retires: {empty_total} objects inside "
                  f"{geom['empty_radius_m']} m {empty_per}", flush=True)
            if empty_total != 0:
                raise SystemExit(
                    f"the site still holds {empty_total} object(s) "
                    f"{empty_per} after {len(ops)} retires. Placing 1,255 "
                    f"pieces on top of a leftover would make the new body's "
                    f"own count wrong and leave the operator a building with "
                    f"a ghost in it.")
            big = max(census["standing_per_prefab"].items(),
                      key=lambda kv: kv[1])[0]
            emit(b, [save_op((px, pz), big, 0, geom["empty_radius_m"],
                             spec["role"])], label=f"{site} save (retired)")

        # 4. THE BODY, WHOLE, IN BATCHES
        for i, op in enumerate(batches, start=1):
            g = sink_gate(f"before batch {i}/{len(batches)}")
            drains.append(g)
            rtt = srv.probe()
            op["meta"]["sink_gate"] = g
            op["meta"]["probe_rtt_s"] = round(rtt, 3)
            print(f"    batch {i}/{len(batches)}: {op['params']['command_count']}"
                  f" pieces, {len(op['wire'])} trips, cumulative "
                  f"{op['meta']['cumulative_pieces']}, probe {rtt * 1000:.0f} "
                  f"ms", flush=True)
            emit(b, [op], label=f"{site} batch {i}/{len(batches)}")

        # 5. EVERY PREFAB, COUNTED
        sink_gate("before verify")
        ver = lh_live_verify(srv, spec, census)
        print(f"    verify: total {ver['total_got']}/{ver['total_want']}, "
              f"prefabs_ok {ver['prefabs_ok']}, strays {ver['strays']}",
              flush=True)
        b.observe(f"body_verified::{site}", ver["method"], ver["tool"],
                  dict(ver, grounding=ground), role=spec["role"],
                  site_id=site)
        if not (ver["total_ok"] and ver["prefabs_ok"]):
            raise SystemExit(
                f"the placed body does NOT match the plan: total "
                f"{ver['total_got']}/{ver['total_want']}, mismatched "
                f"{ver['mismatched']}. The records stand in the ledger; fix "
                f"the cause rather than re-emitting a batch, because a "
                f"re-emit duplicates whatever DID land.")

        # 6. THE HEIGHT, off the ZDOs
        sink_gate("before height read-back")
        cap = cube_rows(srv, height["roof_cap_prefab"],
                        height["roof_cap_xz"][0], height["roof_cap_origin_y"],
                        height["roof_cap_xz"][1], 1.2)
        gal = cube_rows(srv, height["gallery_platform_prefab"], px,
                        height["gallery_platform_origin_y"], pz, 1.2)
        bea = [cube_rows(srv, r["prefab"], r["at"][0], r["at"][1], r["at"][2],
                         1.5) for r in height["beacons"]]
        live_h = dict(
            cap=cap, gallery=gal, beacons=bea,
            cap_origin_y_live=cap["y_max"],
            cap_top_y_live=(None if cap["y_max"] is None else
                            round(cap["y_max"] + (height["roof_cap_top_y"]
                                                  - height["roof_cap_origin_y"]),
                                  3)),
            agrees_with_plan_m=(None if cap["y_max"] is None else
                                round(cap["y_max"]
                                      - height["roof_cap_origin_y"], 3)))
        b.observe(f"height::{site}",
                  "MEASURED two ways and they must agree: the plan's collider "
                  "geometry at this pad's datum, and the LIVE y of the "
                  "topmost pieces read off their ZDOs with a bounded "
                  "`findObjects -detailed` cube whose header is required "
                  "under 18 rows. The beacon's seat is measured against its "
                  "plinth from the piece's STRUCTURAL solid: a bonfire's union "
                  "AABB reaches 2.130 m below its pivot because of the fire "
                  "volume, and seating on a union is exactly how beacons "
                  "ended up 5.173 m in the air earlier today.",
                  "tools/jumpstart/settlements/waterfront.py::lh_height",
                  dict(plan=height, live=live_h), units="m",
                  role=spec["role"], site_id=site)
        print(f"    height: cap top {height['roof_cap_top_y']} "
              f"({height['height_above_sea_m']} m above sea, "
              f"{height['height_above_entry_floor_m']} m above the entry "
              f"floor); live cap origin {cap['y_max']} vs plan "
              f"{height['roof_cap_origin_y']}", flush=True)

        # 7. THE DOCK, THE BERTH AND BOTH PORTAL ENDS
        sink_gate("before dock confirm")
        dock = dock_confirm(srv, spec)
        b.observe(f"access_confirmed::{spec['dock']['site']}", dock["method"],
                  dock["tool"], dock, role=spec["role"], site_id=site)
        print(f"    dock: pier {dock['pier']['pieces_got']}/"
              f"{dock['pier']['pieces_want']} head depth "
              f"{dock['pier']['head_depth_m']} m; {dock['berth']['prefab']} "
              f"x{dock['berth']['count']} min depth "
              f"{dock['berth']['hull_min_depth_m']} m; portal "
              f"{dock['portal']['tag']} paired {dock['portal']['paired']}",
              flush=True)
        if not dock["portal"]["paired"]:
            raise SystemExit(
                f"the {spec['dock']['tag']} tag does NOT read back off both "
                f"ZDOs: {[(e['end'], e['tag_on_zdo']) for e in dock['portal']['readback']['ends']]}. "
                f"This site is portal-only and 2.8 km from the nearest built "
                f"road, so a one-ended tag strands the operator at the top of "
                f"the map. That is the operator's original reported defect.")
        if not (dock["pier"]["ok"] and dock["berth"]["ok"]):
            raise SystemExit(f"the pier or the berth does not measure as "
                             f"recorded: {dock['pier']['per_prefab']} "
                             f"{dock['berth']['count']}")

        # 8. THE SAVE, with a postcondition that re-measures THIS pass
        dominant = max(want.items(), key=lambda kv: kv[1])
        emit(b, [save_op((px, pz), dominant[0], dominant[1],
                         spec["count_radius_m"], spec["role"])],
             label=f"{site} save")
        b.note(
            f"{site} IS COMPLETE. {sum(want.values())} pieces of "
            f"{spec['body']['filename']} placed WHOLE in {len(batches)} "
            f"spawn_plan records of {pieces} pieces each after the "
            f"{census['standing_total']}-piece partial was retired in "
            f"{len(ops)} records and the site counted EMPTY. Batch size is "
            f"arithmetic, not taste: at 155 echoed bytes per placed object a "
            f"{pieces}-piece record costs at most "
            f"{pieces * SINK_ECHO_BYTES} B of a 65,536-byte pipe, while the "
            f"single 1,255-command record that wedged the sink cost ~194 KB "
            f"with no drain window inside it. The sink was MEASURED from the "
            f"host and the main thread probed before every record: "
            f"{sum(1 for g in drains if g['action'] != 'none')} drain(s) were "
            f"needed across {len(drains)} gates. MEASURED height: the roof cap "
            f"tops at {height['roof_cap_top_y']} m, "
            f"{height['height_above_sea_m']} m above c_WaterLevel 30.0 and "
            f"{height['height_above_entry_floor_m']} m above its own entry "
            f"floor, with the lantern gallery platform at "
            f"{height['gallery_platform_top_y']} m "
            f"({height['gallery_above_sea_m']} m above sea). The beacon sits "
            f"on its plinth, not in the air: pyre base "
            f"{height['beacons'][0]['structural_base_y']} m against a plinth "
            f"top of {height['beacons'][0]['plinth_top_y']} m. Access is "
            f"confirmed at both ends of {spec['dock']['tag']}.",
            role=spec["role"], site_id=site)
        print(json.dumps(b.close(), indent=1, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("measure", cmd_measure), ("audit", cmd_audit),
                     ("complete", cmd_complete), ("stair", cmd_stair),
                     ("fabric", cmd_fabric), ("newbuild", cmd_newbuild),
                     ("probe", cmd_probe), ("port", cmd_port),
                     ("retire", cmd_retire), ("lighthouse", cmd_lighthouse)):
        p = sub.add_parser(name)
        p.add_argument("--site", default=None)
        p.add_argument("--prefabs", nargs="*", default=[])
        p.add_argument("--predict", default=None)
        p.add_argument("--radius", type=float, default=34.0)
        p.add_argument("--arc", type=float, default=180.0)
        p.add_argument("--gate-bearing", type=float, default=0.0)
        p.add_argument("--probe-xz", nargs=2, type=float,
                       default=[56.0, -288.0],
                       help="where to place an existence probe. MEASURED "
                            "default: the previous default (-60,-300) was "
                            "described as open meadow and is the SEABED -- the "
                            "composed applied surface there is 15.241 m, i.e. "
                            "14.759 m BELOW c_WaterLevel 30.0, and all three "
                            "24 m-spaced slots were underwater. (56,-288) puts "
                            "the slots at 35.00 / 34.90 / 35.97 m, ~5 m of "
                            "freeboard, 54 m clear of harbour-temple-south")
        p.add_argument("--no-boat", action="store_true",
                       help="skip the vessel (it is already berthed)")
        p.add_argument("--hub-slot", type=float, default=None,
                       help="ring angle in degrees for a portal's HUB end")
        p.add_argument("--apply", action="store_true",
                       help="actually append and send; without it nothing is "
                            "written and nothing is sent")
        p.add_argument("--note", default=None)
        # THE ACTOR IS THE CALLER'S.  `ACTOR` above is the agent that wrote
        # the earlier waterfront records, and a module constant is a default
        # in disguise: `clear.py`'s said `RoadClear` while `RoadEmit` was
        # running it, and the ledger then recorded work under the name of an
        # agent that had yielded an hour earlier. Provenance that is wrong is
        # worse than provenance that is missing, because it reads as evidence.
        p.add_argument("--actor", default=ACTOR,
                       help="the agent id the ledger records this pass under")
        p.add_argument("--batch-pieces", type=int, default=SPAWN_BATCH_PIECES,
                       help="pieces per spawn_plan record; the sink gate runs "
                            "between records, so this is the size of the "
                            "largest un-gated burst of console echo")
        p.add_argument("--resume", action="store_true",
                       help="skip the retire half and re-send only the plan "
                            "lines with nothing standing at them, matched "
                            "PER PIECE against a live census. For a run that "
                            "died mid-placement: neither retiring good "
                            "pieces nor re-sending placed commands is right.")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    if args.cmd not in ("measure", "probe") and not args.site:
        raise SystemExit("--site is required")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

