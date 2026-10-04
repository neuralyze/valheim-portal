#!/usr/bin/env python3
"""Pick the sites: a town in the Meadows, watchtowers, mountaintop castles,
lighthouses and tree houses -- decided at 1 m, with the earthwork bill.

Search coarse, decide fine, and never let the coarse pass have the last word.
The coarse pass here is NOT the 8 m `.biome` grid (which is river-free and so
cannot be trusted about ground at all); it is the SAME 1 m field decimated 8x
into per-block minima and maxima. Because those blocks are exact, a window
relief taken on the decimated plane is an exact bound on a slightly larger
window -- it can only OVER-state relief, so it discards some usable sites and
admits no unusable ones. Every survivor is then re-measured at 1 m, with the
pad slid inside a search box to the cheapest offset, and the verdict that
matters -- does the pad fit inside `TerrainComp`'s +/-8 m clamp -- is taken
there and nowhere else.

CRITERIA, and why each one is a criterion
-----------------------------------------
town        Meadows purity at 1 m over the whole district; low relief so the
            STREETS need no earthwork (only the building pads get flattened);
            on a landmass the operator can reach on foot from spawn; coastal so
            `Crossings` can put the harbour on the town's own waterfront.
watchtower  local RISE over a 160 m radius -- height above the lowest ground it
            overlooks. A tower on a local maximum that is 2 m above its
            surroundings is a shed; this is the number that says it commands
            something. Small pad, so the bill stays trivial.
castle      Mountain biome, high, and with a summit BROAD enough that a level
            pad stays inside the clamp. That is the binding constraint and it
            disqualifies most peaks: a 48 m pad on a cone needs tens of metres
            of cut.
lighthouse  water within a few metres, standing well above it, and at a
            HEADLAND -- measured as the fraction of a 160 m window that is
            below the water plane, so a cape reads high and a bay shore low.
treehouse   BlackForest, canopy country, on ground flat enough for a stilted
            body to reach the pad on its posts.

Every candidate additionally clears `clearance.py`'s per-type stand-off and is
reported with its distance from spawn, because the operator inspects on foot.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field as dcfield
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import clearance  # noqa: E402
import terrain1m as T  # noqa: E402

DECIMATE = 8


@dataclass
class Block:
    """One patch decimated to `DECIMATE` m blocks, with exact per-block extremes."""
    f: T.Field
    step: float
    x0: float
    z0: float
    hmin: np.ndarray
    hmax: np.ndarray
    hmean: np.ndarray
    biome_frac: dict[int, np.ndarray] = dcfield(default_factory=dict)

    def world(self, iz: int, ix: int) -> tuple[float, float]:
        return (self.x0 + ix * self.step, self.z0 + iz * self.step)


def decimate(f: T.Field, k: int = DECIMATE) -> Block:
    n = (f.n // k) * k
    h = f.h[:n, :n].reshape(n // k, k, n // k, k)
    b = f.biome[:n, :n].reshape(n // k, k, n // k, k)
    blk = Block(
        f=f, step=k * f.step,
        x0=f.x0 + (k - 1) * f.step / 2, z0=f.z0 + (k - 1) * f.step / 2,
        hmin=h.min(axis=(1, 3)), hmax=h.max(axis=(1, 3)),
        hmean=h.mean(axis=(1, 3)),
    )
    for code in (T.BIOME_CODE["Meadows"], T.BIOME_CODE["Mountain"],
                 T.BIOME_CODE["BlackForest"]):
        blk.biome_frac[code] = (b == code).mean(axis=(1, 3)).astype(np.float32)
    return blk


# --- window reductions on the decimated plane -----------------------------

def _win_mean(a: np.ndarray, k: int) -> np.ndarray:
    r = k // 2
    p = np.pad(a.astype(np.float64), r, mode="edge")
    c = np.zeros((p.shape[0] + 1, p.shape[1] + 1), np.float64)
    c[1:, 1:] = p.cumsum(0).cumsum(1)
    n0, n1 = a.shape
    tot = (c[k:k + n0, k:k + n1] - c[0:n0, k:k + n1]
           - c[k:k + n0, 0:n1] + c[0:n0, 0:n1])
    return (tot / (k * k)).astype(np.float32)


def _slide(a: np.ndarray, k: int, fn) -> np.ndarray:
    r = k // 2
    p = np.pad(a.astype(np.float32), r, mode="edge")

    def run(x: np.ndarray, axis: int) -> np.ndarray:
        v = np.moveaxis(x, axis, -1)
        w = v.shape[-1] - k + 1
        acc = v[..., 0:w].copy()
        for s in range(1, k):
            fn(acc, v[..., s:s + w], out=acc)
        return np.moveaxis(acc, -1, axis)

    return run(run(p, 0), 1)


def win_relief(blk: Block, metres: float) -> np.ndarray:
    k = max(1, int(round(metres / blk.step)))
    k = k + 1 if k % 2 == 0 else k
    return _slide(blk.hmax, k, np.maximum) - _slide(blk.hmin, k, np.minimum)


def win_rise(blk: Block, radius_m: float) -> np.ndarray:
    k = max(1, int(round(2 * radius_m / blk.step)))
    k = k + 1 if k % 2 == 0 else k
    return blk.hmean - _slide(blk.hmin, k, np.minimum)


def win_biome(blk: Block, code: int, metres: float) -> np.ndarray:
    k = max(1, int(round(metres / blk.step)))
    k = k + 1 if k % 2 == 0 else k
    return _win_mean(blk.biome_frac[code], k)


def win_wet(blk: Block, metres: float) -> np.ndarray:
    k = max(1, int(round(metres / blk.step)))
    k = k + 1 if k % 2 == 0 else k
    return _win_mean((blk.hmin <= T.WATER_LEVEL_M).astype(np.float32), k)


def peaks(mask: np.ndarray, value: np.ndarray, sep_cells: int, limit: int
          ) -> list[tuple[int, int]]:
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return []
    order = np.argsort(-value[ys, xs])
    out: list[tuple[int, int]] = []
    for t in order:
        iz, ix = int(ys[t]), int(xs[t])
        if all((iz - a) ** 2 + (ix - b) ** 2 >= sep_cells ** 2 for a, b in out):
            out.append((iz, ix))
            if len(out) >= limit:
                break
    return out


# --- 1 m refinement -------------------------------------------------------

def best_pad(f: T.Field, x: float, z: float, width_m: float, depth_m: float,
             search_m: float = 12.0, stride_m: float = 2.0, yaw_deg: float = 0.0,
             must_clear=None, bill=None) -> dict:
    """Slide the pad inside a +/-`search_m` box and keep the cheapest offset
    that is BUILDABLE and, where `must_clear` is given, LEGAL.

    The ordering is a real design decision, not an implementation detail:
      1. clears the POI stand-off, if a test was supplied;
      2. fits inside `TerrainComp`'s +/-8 m clamp;
      3. moves the least material.
    A pad that moves less material but cannot be built, or that would delete a
    location's pieces, is not a better pad. MEASURED reason `must_clear` exists
    at all: the best summit pads on this seed are ALL occupied -- (-238, 1634)
    at 136.9 m has `Runestone_Mountains` 13.4 m from its centre, i.e. inside
    the footprint, and a +/-16 m slide cannot escape it. Escaping needs a
    search box wider than the obstacle, and the search has to know about the
    obstacle to aim.

    `cleared_offsets` and `offsets_tried` are reported so a zero result can be
    told apart from a narrow one.

    `bill(px, pz)` prices one offset.  Absent, it is `terrain1m.pad_bill` on
    this FINDING field, which is right for ranking where to look; a pad that
    is going to be WRITTEN is billed by `settlements/plan.py::site_bill` on
    the surface the engine levels, over the samples the writer stamps.
    """
    best = None
    reach = math.hypot(width_m, depth_m) / 2 + search_m + 2
    steps = int(search_m / stride_m)
    tried = cleared = 0
    halfdiag = math.hypot(width_m, depth_m) / 2
    for dz in range(-steps, steps + 1):
        for dx in range(-steps, steps + 1):
            px, pz = x + dx * stride_m, z + dz * stride_m
            if not f.contains(px, pz, reach):
                continue
            tried += 1
            legal = True if must_clear is None else bool(must_clear(px, pz, halfdiag))
            if legal:
                cleared += 1
            elif must_clear is not None:
                continue
            try:
                got = (T.pad_bill(f, px, pz, width_m, depth_m, yaw_deg)
                       if bill is None else bill(px, pz))
            except ValueError:
                continue
            got["x"], got["z"] = px, pz
            key = (not got["clamp_ok"], got["moved_m3"])
            if best is None or key < (not best["clamp_ok"], best["moved_m3"]):
                best = got
    if best is None:
        raise ValueError(
            f"no pad offset inside patch {f.id} at ({x}, {z}): {tried} offsets tried, "
            f"{cleared} cleared the stand-off")
    best["offsets_tried"] = tried
    best["cleared_offsets"] = cleared
    return best


@dataclass
class Site:
    kind: str
    x: float
    z: float
    patch: str
    ground_y: float
    biome: str
    pad: dict
    rise_m: float
    dist_water_m: float
    dist_spawn_m: float
    nearest_location: dict
    notes: dict = dcfield(default_factory=dict)

    def line(self) -> str:
        p = self.pad
        return (f"{self.kind:11s} ({self.x:7.1f}, {self.z:7.1f}) {self.patch:12s} "
                f"y={p['target_y']:7.2f} {self.biome:11s} "
                f"pad={p['width_m']:.0f}x{p['depth_m']:.0f} "
                f"moved={p['moved_m3']:8.0f}m3 cut={p['max_cut_m']:5.2f} "
                f"fill={p['max_fill_m']:5.2f} clamp={'OK ' if p['clamp_ok'] else 'FAIL'} "
                f"head={p['clamp_headroom_m']:5.2f} rise={self.rise_m:6.1f} "
                f"wet={p['wet_samples']:4d} water={self.dist_water_m:6.0f} "
                f"spawn={self.dist_spawn_m:6.0f} poi={self.nearest_location['prefab'][:18]:18s}"
                f"@{self.nearest_location['dist_m']:.0f}")


def water_distance(f: T.Field) -> np.ndarray:
    """Distance to the nearest WET sample of any kind, including a puddle."""
    from scipy import ndimage as ndi
    return ndi.distance_transform_edt(f.h > T.WATER_LEVEL_M,
                                      sampling=f.step).astype(np.float32)


def ocean_distance(f: T.Field) -> np.ndarray:
    """Distance to NAVIGABLE water: the wet component that reaches the patch
    edge, i.e. the open sea rather than an inland pond.

    MEASURED correction, and `Crossings` found it rather than me: Vestvik's
    "94 m to water" was a distance to a wet CELL. The nearest shoreline there
    that is 4-connected to the sea is 133.7 m away, and the nearest one deep
    enough to take a 22 m pier is 227.3 m. A settlement's waterfront claim has
    to be about water a boat can be in, so `dist_water_m` alone is not a
    waterfront test and both numbers are now reported.
    """
    from scipy import ndimage as ndi
    wet = f.h <= T.WATER_LEVEL_M
    lab, cnt = ndi.label(wet)
    if cnt == 0:
        return np.full(f.h.shape, np.inf, np.float32)
    edge = set(np.unique(np.concatenate([lab[0, :], lab[-1, :],
                                         lab[:, 0], lab[:, -1]])))
    edge.discard(0)
    if not edge:
        # No wet component reaches the edge: fall back to the largest, and say
        # so rather than silently answering a different question.
        sizes = ndi.sum(np.ones_like(lab, np.float32), lab,
                        index=np.arange(1, cnt + 1))
        edge = {int(np.argmax(sizes)) + 1}
    sea = np.isin(lab, list(edge))
    return ndi.distance_transform_edt(~sea, sampling=f.step).astype(np.float32)


# SITE_INSTALLATIONS_DEPENDS_ON: WHICH WORLD'S BUILDINGS A SETTLEMENT KEEPS OFF,
# and it is THIS world's SOLVED placements -- nothing else and nothing less.
#
# THE KEY NAMED ANOTHER WORLD.  `Picker.__init__` defaulted `world_dir` to
# `worlds/Ulfsland` and `plan.py` never passed one, so EVERY seed's site solve
# kept its settlements 60 m off ULFSLAND's 13 solved installations (seed
# Pirate68) and could not see one of its own.  MEASURED 2026-09-24 on Fresh42:
# the certified plan.Fresh42.json was solved against Ulfsland's 13 discs, and
# Fresh42's own 13 (portal hub, harbour, starter hall, station hub, ...) were
# never read.  Nothing refused, because this reader never asked which seed a
# placement set declares.  On THIS seed it moved nothing -- 0 of the plan's 45
# settlement points stand inside either set of discs -- which is why it was
# silent, and on the next seed it is a town on top of a portal hall.
#
# AND THE ORDER WAS INVERTED WITH IT.  `pipeline.py` ran `sites` BEFORE
# `placements` and declared the reverse dependency; the site solve only ever
# ran because a previous drive had left a `worlds/<World>/` behind.  From
# scratch it refused at the end of a 137.5 s solve (roads/spec.py's import-time
# `installation_xz`).  `pipeline.audit_reads` now checks the table against
# `worldseed.installations_read`, the one declaration of where a world's solved
# installations live, which this reader globs too.
def installations_dir(world: str) -> Path:
    """The directory holding `world`'s placement sets -- the one place a
    settlement solve learns where this world's own buildings stand."""
    if not world:
        raise SystemExit(
            "REFUSING: a site solve needs the WORLD whose buildings it keeps "
            "off, and none was given. A settlement plan is a function of the "
            "seed AND of the installations already solved for this world; "
            "set $VH_WORLD.")
    return _worldseed().installations_read(world)[0]


def _worldseed():
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    return worldseed



def existing_installations(world_dir: str | Path, *, seed_: str | None = None,
                           world_: str | None = None) -> list[dict]:
    """The installations already recorded for this world, as exclusion discs.

    MEASURED reason this is here rather than assumed away: the castle search's
    best mountain-isle summit came out at (-10, 3924), and the recorded
    `pre-moder/mountain-outpost` stands at (-7.5, 3939.5) -- 15.7 m away, i.e.
    the same summit. The POI clearance check cannot see it, because our own
    buildings are not world locations. A site list that ignores them proposes
    building on top of the operator's existing base.

    The disc radius is the recorded `requirement.footprint_m` half-diagonal
    plus `KEEP_CLEAR_M`, which is a TASTE PICK (a settlement should not crowd
    an installation) and not a measurement.

    EVERY placement set is scope-checked against the seed/world being planned
    (`worldseed.require_scope`), and UNREAD IS NOT CLEAR: no placement set, or
    a placement without its `solved` block, is REFUSED -- an empty list here
    would read as "this world has no buildings to keep off".
    """
    import yaml
    worldseed = _worldseed()
    glob, key = worldseed.INSTALLATIONS_GLOB, worldseed.INSTALLATIONS_KEY
    files = sorted(Path(world_dir).glob(glob))
    if not files:
        raise SystemExit(
            f"REFUSING: {world_dir}/{glob} matches nothing, so "
            f"this site solve cannot know where the world's own installations "
            f"stand. Solve them first: pipeline stage `placements` "
            f"(blueprints/solve_placements.py).")
    out, unsolved = [], []
    for f in files:
        doc = yaml.safe_load(f.read_text()) or {}
        worldseed.require_scope(
            "installation placement set", f, found_seed=doc.get("seed"),
            found_world=doc.get("world"), seed_=seed_, world_=world_,
            produced_by="blueprints/solve_placements.py for the world being "
                        "planned")
        for pl in (doc.get("placements") or []):
            s = pl.get(key) or {}
            if s.get("x") is None:
                unsolved.append(f"{f.parent.name}/{pl.get('id')}")
                continue
            fp = float((pl.get("requirement") or {}).get("footprint_m") or 32.0)
            out.append({"id": f"{f.parent.name}/{pl.get('id')}",
                        "x": float(s["x"]), "z": float(s["z"]),
                        "footprint_m": fp,
                        "radius_m": fp * math.sqrt(2) / 2 + KEEP_CLEAR_M})
    if unsolved or not out:
        raise SystemExit(
            f"REFUSING: {len(unsolved)} placement(s) under {world_dir} carry "
            f"no {key!r} block ({', '.join(unsolved[:6])}) and "
            f"{len(out)} do. A settlement kept off a subset of this world's "
            f"buildings is not kept off its buildings.")
    return out


# TASTE PICK, not measured: how far a new settlement keeps off an installation
# that is already recorded for this world.
KEEP_CLEAR_M = 60.0


def trunk_patches(fields: dict) -> tuple[str, ...]:
    """The windows a TOWN may stand in, named BY ROLE and resolved per field.

    `mainland` and `westisle` are Ulfsland's landmass names, and they were the
    default of `Picker.town` -- so a fresh seed's field, whose windows are
    named for what they DO (`trunk-primary` holds the spawn and the priority-1
    trunk, `trunk-secondary` is the next landmass), returned ZERO town
    candidates and the planner refused on ground it had never looked at.
    `worldseed.aliases` already carries both names for each role, so Ulfsland's
    binary resolves to exactly the same two patches in exactly the same order.

    REFUSES rather than searching whatever is left: a town is defined as
    standing on a TRUNK landmass, and quietly demoting that to "any window in
    the file" would plant a town on an outlier island the roads cannot reach.
    """
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    out: list[str] = []
    for role in (worldseed.TRUNK_PRIMARY, worldseed.TRUNK_SECONDARY):
        hit = next((a for a in worldseed.aliases(role) if a in fields), None)
        if hit is not None and hit not in out:
            out.append(hit)
    if not out:
        raise ValueError(
            f"this field holds {sorted(fields)} and none of them is a trunk "
            f"window ({worldseed.TRUNK_PRIMARY} / "
            f"{worldseed.TRUNK_SECONDARY}, or their Ulfsland names "
            f"{worldseed.LEGACY_OF.get(worldseed.TRUNK_PRIMARY)!r} / "
            f"{worldseed.LEGACY_OF.get(worldseed.TRUNK_SECONDARY)!r}), so "
            f"there is nowhere a town is defined to stand. Scan this seed's "
            f"trunk landmasses first: roads/field.py request derives the "
            f"window set from the seed's own land.")
    return tuple(out)


def all_patches(fields: dict) -> tuple[str, ...]:
    """EVERY window in this field, trunk windows first, then the rest by name.

    For the kinds that are not confined to a trunk landmass -- a castle stands
    on the highest ground there is, wherever that is.  `Picker.castle`'s
    default was `("mtn7114", "mainland", "westisle", "south33")`: four of
    Ulfsland's seven window ids, hand-ordered, inside what reads as a general
    default.  On a derived field it names nothing at all.
    """
    trunks = trunk_patches(fields)
    return trunks + tuple(sorted(p for p in fields if p not in trunks))


class Picker:
    def __init__(self, patch_path: str, loc_path: str, world_dir: str | Path,
                 *, seed_: str | None = None, world_: str | None = None):
        # The inputs are KEPT, not just consumed: `plan.py` stamps the seed it
        # actually ran on into `sites.yaml`, and it can only do that if the
        # artefacts it read are still nameable.  Before 2026-09-21 that stamp
        # was the literal `Pirate68`, so a plan solved on ANY seed declared
        # itself Pirate68 and every downstream scope check read a constant.
        #
        # `world_dir` HAS NO DEFAULT: it was `worlds/Ulfsland`, which made
        # every seed's settlements keep off Ulfsland's buildings.  See
        # SITE_INSTALLATIONS_DEPENDS_ON above.
        self.patch_path = str(patch_path)
        self.loc_path = str(loc_path)
        self.fields = T.load(patch_path)
        self.loc = clearance.load(loc_path)
        self.blocks = {k: decimate(v) for k, v in self.fields.items()}
        self.wdist: dict[str, np.ndarray] = {}
        self.world_dir = Path(world_dir)
        self.installations = existing_installations(
            world_dir, seed_=seed_, world_=world_)

    def installation_conflict(self, x: float, z: float, half_diag_m: float) -> dict | None:
        for inst in self.installations:
            need = inst["radius_m"] + half_diag_m
            d = math.hypot(x - inst["x"], z - inst["z"])
            if d < need:
                return dict(inst, dist_m=round(d, 1), required_m=round(need, 1))
        return None

    def water(self, pid: str) -> np.ndarray:
        if pid not in self.wdist:
            self.wdist[pid] = water_distance(self.fields[pid])
        return self.wdist[pid]

    def _site(self, kind: str, blk: Block, iz: int, ix: int, pad_w: float,
              pad_d: float, search_m: float, rise_radius: float,
              yaw_deg: float = 0.0, clearance_half_diag_m: float | None = None,
              protected_only: bool = False, stride_m: float = 2.0) -> Site | None:
        f = blk.f
        x, z = blk.world(iz, ix)
        fixed_hd = clearance_half_diag_m

        def ok(px: float, pz: float, halfdiag: float) -> bool:
            hd = halfdiag if fixed_hd is None else fixed_hd
            if self.installation_conflict(px, pz, hd) is not None:
                return False
            return self.loc.clear(px, pz, hd, protected_only=protected_only)

        try:
            pad = best_pad(f, x, z, pad_w, pad_d, search_m=search_m, stride_m=stride_m,
                           yaw_deg=yaw_deg, must_clear=ok)
        except ValueError:
            return None
        px, pz = pad["x"], pad["z"]
        halfdiag = (math.hypot(pad_w, pad_d) / 2 if fixed_hd is None else fixed_hd)
        iz1, ix1 = f.idx(px, pz)
        rise = float(win_rise(blk, rise_radius)[iz, ix])
        return Site(
            kind=kind, x=round(px, 1), z=round(pz, 1), patch=f.id,
            ground_y=round(float(f.h[iz1, ix1]), 2), biome=f.biome_at(px, pz),
            pad=pad, rise_m=round(rise, 1),
            dist_water_m=round(float(self.water(f.id)[iz1, ix1]), 1),
            dist_spawn_m=round(math.hypot(px - self.loc.spawn[0], pz - self.loc.spawn[1]), 1),
            nearest_location=self.loc.nearest(px, pz),
            notes={"clearance_half_diagonal_m": round(halfdiag, 1),
                   "clearance_scope": "protected-only" if protected_only else "all instances",
                   "pad_offsets_tried": pad.get("offsets_tried"),
                   "pad_offsets_clearing_standoff": pad.get("cleared_offsets")},
        )

    # --- kinds ------------------------------------------------------------
    def town(self, district_m: float = 176.0, square_m: float = 24.0,
             purity: float = 0.90, max_relief: float = 20.0, limit: int = 10,
             patches: tuple[str, ...] | None = None) -> list[Site]:
        """A town DISTRICT, not one pad.

        MEASURED, and it is the finding that set the whole layout: a 176 m
        district of >=92% Meadows with <=12 m of relief exists in 21 blocks on
        the spawn island and 6 on the west isle, and NONE of them is dry and
        coastal. Meadows on this seed is rolling, not flat. So the town is not
        levelled as a district; it FOLLOWS the ground -- individual building
        pads, each inside the +/-8 m clamp on its own, joined by streets routed
        along the contours. `max_relief` here is therefore a limit on how much
        the street network has to climb, not a flatness demand.

        The pad priced here is the TOWN SQUARE (`square_m`), the one piece of
        deliberately level ground the layout needs; every other pad is priced
        by `layout.py` where the buildings are.
        """
        out: list[Site] = []
        mead = T.BIOME_CODE["Meadows"]
        # BY ROLE, not by Ulfsland's landmass names -- see `trunk_patches`.
        for pid in (patches if patches is not None
                    else trunk_patches(self.fields)):
            blk = self.blocks[pid]
            rel = win_relief(blk, district_m)
            pure = win_biome(blk, mead, district_m)
            wet = win_wet(blk, district_m)
            coast = win_wet(blk, 2.2 * district_m)
            ok = ((pure >= purity) & (rel <= max_relief) & (wet <= 0.0)
                  & (coast > 0.02) & (blk.hmin > T.WATER_LEVEL_M + 1.0))
            sx, sz = self.loc.spawn
            zz, xx = np.meshgrid(blk.z0 + np.arange(blk.hmin.shape[0]) * blk.step,
                                 blk.x0 + np.arange(blk.hmin.shape[1]) * blk.step,
                                 indexing="ij")
            dsp = np.hypot(xx - sx, zz - sz)
            value = pure * 100 - rel * 3 - dsp / 300.0 + np.minimum(coast, 0.25) * 40
            for iz, ix in peaks(ok, value, sep_cells=int(500 / blk.step), limit=limit * 4):
                s = self._site("town", blk, iz, ix, square_m, square_m, search_m=24.0,
                               rise_radius=200.0,
                               clearance_half_diag_m=district_m * math.sqrt(2) / 2,
                               protected_only=True)
                if s:
                    s.notes["district_m"] = district_m
                    s.notes["district_relief_m"] = round(float(rel[iz, ix]), 1)
                    s.notes["district_meadows_fraction"] = round(float(pure[iz, ix]), 3)
                    s.notes["coast_fraction_400m"] = round(float(coast[iz, ix]), 3)
                    s.notes["square_m"] = square_m
                    out.append(s)
                if len(out) >= limit:
                    break
        return out

    def watchtower(self, pad: float = 16.0, min_rise: float = 12.0,
                   max_relief: float = 7.0, limit: int = 14,
                   patches: tuple[str, ...] | None = None) -> list[Site]:
        out: list[Site] = []
        for pid in (patches if patches is not None
                    else trunk_patches(self.fields)):
            blk = self.blocks[pid]
            rel = win_relief(blk, pad + 8)
            rise = win_rise(blk, 160.0)
            ok = ((rise >= min_rise) & (rel <= max_relief)
                  & (blk.hmin > T.WATER_LEVEL_M + 2.0))
            value = rise - rel * 2
            for iz, ix in peaks(ok, value, sep_cells=int(420 / blk.step), limit=limit * 3):
                s = self._site("watchtower", blk, iz, ix, pad, pad, search_m=10.0,
                               rise_radius=160.0)
                if s:
                    out.append(s)
                if len(out) >= limit:
                    break
        return out

    def castle(self, pad: float = 44.0, min_h: float = 100.0,
               max_relief: float = 22.0, limit: int = 10, search_m: float = 40.0,
               patches: tuple[str, ...] | None = None) -> list[Site]:
        """A castle on high ground, priced against the clamp rather than hoped at.

        MEASURED, and it is the finding that shapes this: the best-scoring
        summit pads on this seed are OCCUPIED, and escaping the occupant costs
        the clamp. At (-238, 1634) -- 136.9 m, 100% Mountain, on the SPAWN
        ISLAND -- a 44 m pad is clamp-legal at 3.74 m of headroom but has
        `Runestone_Mountains` 13.4 m from its centre, inside the footprint.
        Sliding out to a clear offset (+/-60 m, 264 of 961 offsets clear) puts
        the pad on the flank instead of the crown and it needs 24.15 m of fill:
        clamp headroom -16.16 m, i.e. UNBUILDABLE as terrain.

        So the search slides with the stand-off test in the loop rather than
        filtering afterwards, and a candidate that cannot be levelled is
        DROPPED rather than reported with a quiet clamp failure.
        """
        out: list[Site] = []
        mtn = T.BIOME_CODE["Mountain"]
        # EVERY window: a castle stands on the highest ground there is, which
        # is not confined to a trunk landmass.  This default was four of
        # Ulfsland's seven window ids, hand-ordered.
        for pid in (patches if patches is not None
                    else all_patches(self.fields)):
            blk = self.blocks[pid]
            rel = win_relief(blk, pad + 8)
            mfrac = win_biome(blk, mtn, pad + 8)
            rise = win_rise(blk, 300.0)
            ok = (blk.hmean >= min_h) & (rel <= max_relief) & (mfrac >= 0.6)
            value = blk.hmean + rise - rel * 5
            for iz, ix in peaks(ok, value, sep_cells=int(500 / blk.step), limit=limit * 4):
                s = self._site("castle", blk, iz, ix, pad, pad, search_m=search_m,
                               stride_m=4.0, rise_radius=300.0)
                if s and s.pad["clamp_ok"]:
                    s.notes["mountain_fraction_1m"] = round(
                        s.pad["biome_counts"].get("Mountain", 0) / max(s.pad["samples"], 1), 3)
                    out.append(s)
                if len(out) >= limit:
                    break
        return out

    def lighthouse(self, pad: float = 16.0, max_water_m: float = 30.0,
                   min_rise: float = 6.0, limit: int = 10,
                   patches: tuple[str, ...] | None = None) -> list[Site]:
        out: list[Site] = []
        for pid in (patches if patches is not None
                    else trunk_patches(self.fields)):
            blk = self.blocks[pid]
            f = self.fields[pid]
            rel = win_relief(blk, pad + 8)
            rise = win_rise(blk, 140.0)
            openness = win_wet(blk, 320.0)
            wd = self.water(pid)
            # Nearest-water distance at block resolution: take the block minimum
            # so a block that touches the shore reads as coastal.
            n = (f.n // DECIMATE) * DECIMATE
            wdb = wd[:n, :n].reshape(n // DECIMATE, DECIMATE,
                                     n // DECIMATE, DECIMATE).min(axis=(1, 3))
            ok = ((wdb <= max_water_m) & (blk.hmin > T.WATER_LEVEL_M + 1.0)
                  & (rel <= 7.0) & (rise >= min_rise) & (openness >= 0.30))
            value = blk.hmean + openness * 60 - rel * 2
            for iz, ix in peaks(ok, value, sep_cells=int(700 / blk.step), limit=limit * 3):
                s = self._site("lighthouse", blk, iz, ix, pad, pad, search_m=8.0,
                               rise_radius=140.0)
                if s:
                    s.notes["sea_fraction_640m"] = round(float(openness[iz, ix]), 3)
                    out.append(s)
                if len(out) >= limit:
                    break
        return out

    def treehouse(self, pad: float = 14.0, purity: float = 0.85,
                  max_relief: float = 6.0, limit: int = 10,
                  patches: tuple[str, ...] | None = None) -> list[Site]:
        out: list[Site] = []
        bf = T.BIOME_CODE["BlackForest"]
        for pid in (patches if patches is not None
                    else trunk_patches(self.fields)):
            blk = self.blocks[pid]
            rel = win_relief(blk, pad + 8)
            pure = win_biome(blk, bf, 96.0)
            ok = (pure >= purity) & (rel <= max_relief) & (blk.hmin > T.WATER_LEVEL_M + 1.0)
            sx, sz = self.loc.spawn
            zz, xx = np.meshgrid(blk.z0 + np.arange(blk.hmin.shape[0]) * blk.step,
                                 blk.x0 + np.arange(blk.hmin.shape[1]) * blk.step,
                                 indexing="ij")
            value = pure * 60 - rel * 3 - np.hypot(xx - sx, zz - sz) / 400.0
            for iz, ix in peaks(ok, value, sep_cells=int(500 / blk.step), limit=limit * 3):
                s = self._site("treehouse", blk, iz, ix, pad, pad, search_m=8.0,
                               rise_radius=160.0)
                if s:
                    s.notes["blackforest_fraction_1m"] = round(
                        s.pad["biome_counts"].get("BlackForest", 0) / max(s.pad["samples"], 1), 3)
                    out.append(s)
                if len(out) >= limit:
                    break
        return out


def dedupe(p: "Picker", sites: list[Site], within_m: float = 40.0) -> list[Site]:
    """Collapse the same place found twice.

    The published patches OVERLAP -- `mainland` covers x[-802, 2398] and
    `westisle` covers x[-2360, 840], so (496, 904) is inside both and the
    search reports it once per patch with slightly different block statistics.
    Keeping both would put two towns on one hill. The survivor is the one whose
    patch centre is nearest, which is `terrain1m.field_for`'s rule, so a site's
    patch is decided the same way wherever it is asked.

    THIS IS NOT THE OVERLAP CHECK.  It compares candidates of ONE kind, found
    by one search, and nothing else -- it never saw a treehouse 5.7 m from a
    lighthouse.  Whose ground a site may stand on is decided over every site
    of every kind by `plan.py::SiteClaims` (SITE_CLAIM_DEPENDS_ON).
    """
    out: list[Site] = []
    for s in sites:
        f = T.field_for(p.fields, s.x, s.z)
        if f is not None and f.id != s.patch:
            continue
        if any(math.hypot(s.x - t.x, s.z - t.z) < within_m for t in out):
            continue
        out.append(s)
    return out


def run_picker(patch_path: str, loc_path: str) -> Picker:
    """A `Picker` keeping off THIS run's world's installations, resolved the
    way every tool here resolves its scope (`$VH_WORLD` / `$VH_SEED`)."""
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    return Picker(patch_path, loc_path, installations_dir(worldseed.world()),
                  seed_=worldseed.seed(), world_=worldseed.world())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default="/tmp/settle/h1m.bin")
    ap.add_argument("--locations", default=None,
                    help="default: THIS seed's dump (clearance.location_dump)")
    ap.add_argument("--kind", default="all")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    p = run_picker(a.patches, a.locations or clearance.location_dump())
    print(f"# patches {sorted(p.fields)} locations {len(p.loc)} spawn {p.loc.spawn}")
    kinds = (["town", "watchtower", "castle", "lighthouse", "treehouse"]
             if a.kind == "all" else a.kind.split(","))
    allsites: list[Site] = []
    for k in kinds:
        sites = dedupe(p, getattr(p, k)())
        print(f"\n## {k}: {len(sites)}")
        for s in sites:
            print("  " + s.line())
        allsites += sites
    if a.json:
        Path(a.json).write_text(json.dumps([s.__dict__ for s in allsites], indent=1))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
