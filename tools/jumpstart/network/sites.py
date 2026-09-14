#!/usr/bin/env python3
"""Pick a concrete, verified point for an installation inside a sampled corridor.

This is deliberately NOT a replacement for tools/jumpstart/blueprints/solve_placements.py,
which searches the whole world for a base. This solves the narrow problem the router
has: given a corridor already sampled river-inclusive and point-exact, where exactly
does this installation stand? Endpoints have to be correct before a road between
them means anything, and three of the installations the operator named (farm,
boathouse, mountain furnace camp) do not exist in placements.yaml at all.

Every candidate is scored on the actual sampled lattice and every accepted site
reports its own evidence: freeboard at the pad's LOWEST cell, the p90 1 m grade
across the pad, biome purity, and distance to water. Nothing is asserted that was
not measured on the lattice.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import JUMPSTART

from .terrain import BIOME_NAME, WATER_LEVEL, Corridor

OCEAN_ID = next(k for k, v in BIOME_NAME.items() if v == "Ocean")


def _derive():
    """Import worlds/derive.py for its spawn constants, by path, read-only.

    The THRESHOLDS are not duplicated here. The walk below is a second
    implementation -- it has to be, because a Corridor mosaic is not a VHPATCH1
    patch -- but if 0.5 and 1.5 were typed in as literals then the day someone
    raises the freeboard floor in derive.py these doorsteps would keep passing at
    the old bar and start disagreeing with its verdict silently. That is a smaller
    copy of the WATER_LEVEL incident, so the numbers come from one definition.

    derive.py guards its CLI behind __main__ and has no import side effects beyond
    loading jumpstart.py, which does the same.
    """
    spec = importlib.util.spec_from_file_location(
        "jumpstart_worlds_derive", JUMPSTART / "worlds" / "derive.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging failure
        raise RuntimeError("cannot load worlds/derive.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


DERIVE = _derive()

# Asserted, not assumed: if derive.py's datum ever diverges from site_finder's, the
# two halves of the spawn contract are measuring different worlds.
assert DERIVE.SEA_LEVEL_M == WATER_LEVEL, (
    f"datum disagreement: derive.SEA_LEVEL_M={DERIVE.SEA_LEVEL_M} vs site_finder.WATER_LEVEL={WATER_LEVEL}"
)


@dataclass
class Site:
    id: str
    x: int
    z: int
    y: float
    biome: str
    freeboard_m: float
    pad_m: int
    grade_p90: float
    grade_max: float
    biome_purity: float
    water_dist_m: float
    ocean_dist_m: float
    score: float
    doorstep: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def xz(self) -> tuple[int, int]:
        return (self.x, self.z)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "x": self.x,
            "y": round(self.y, 2),
            "z": self.z,
            "biome": self.biome,
            "freeboard_m": round(self.freeboard_m, 2),
            "pad_m": self.pad_m,
            "grade_p90": round(self.grade_p90, 3),
            "grade_max": round(self.grade_max, 3),
            "biome_purity": round(self.biome_purity, 3),
            "water_dist_m": round(self.water_dist_m, 1),
            "ocean_dist_m": round(self.ocean_dist_m, 1),
            "score": round(self.score, 4),
            "evidence": self.evidence,
        }
        if self.doorstep:
            out["doorstep"] = self.doorstep
        return out


def _fields(cor: Corridor):
    """Per-cell derived fields: water mask, distance transforms, local grade."""
    from scipy.ndimage import distance_transform_edt, maximum_filter, minimum_filter

    h = cor.height
    finite = np.isfinite(h)
    hf = np.where(finite, h, WATER_LEVEL - 1000.0)
    water = hf <= WATER_LEVEL
    # Ocean is the BIOME, not "anything wet" and emphatically not "anything
    # unsampled": folding ~finite into the ocean mask makes the corridor's own edge
    # look like a coastline, and then every cell within 48 m of the edge scores as
    # coastal. Unsampled cells are simply absent from the distance transform.
    ocean = cor.biome == OCEAN_ID
    # 1 m absolute grade, forward difference in x and z, padded to full shape.
    gx = np.zeros_like(hf)
    gz = np.zeros_like(hf)
    gx[:, :-1] = np.abs(np.diff(hf, axis=1))
    gz[:-1, :] = np.abs(np.diff(hf, axis=0))
    grade = np.maximum(gx, gz)
    grade[~finite] = np.inf
    return {
        "finite": finite,
        "water": water,
        "dist_water": distance_transform_edt(~water).astype(np.float32),
        "dist_ocean": distance_transform_edt(~ocean).astype(np.float32),
        "grade": grade,
        "hmin": minimum_filter(np.where(finite, h, np.inf), size=3),
        "hmax": maximum_filter(np.where(finite, h, -np.inf), size=3),
    }


def solve(
    cor: Corridor,
    spec: dict[str, Any],
    fields: dict[str, np.ndarray] | None = None,
    exclude: list[tuple[int, int, float]] = (),
) -> Site:
    """Best point in `cor` for one installation spec.

    spec keys (all optional except id):
      biome           required biome name, or "any"
      pad_m           side of the square pad the installation needs
      min_freeboard_m freeboard required at the pad's LOWEST cell
      max_grade_p90   p90 of the 1 m grade across the pad
      min_purity      fraction of pad cells in the required biome
      coastal         True -> require ocean within max_ocean_dist_m
      max_ocean_dist_m
      min_altitude_m  absolute height floor, e.g. real snow rather than a foothill
      near            (x, z) to stay close to
      within_m        hard radius around `near`
      avoid_water_m   minimum distance to any water (keeps a farm out of a marsh)
    """
    f = fields or _fields(cor)
    pad = int(spec.get("pad_m", 24))
    half = max(1, pad // 2)
    want_biome = spec.get("biome", "any")
    want_id = None if want_biome in (None, "any") else next(
        k for k, v in BIOME_NAME.items() if v.lower() == str(want_biome).lower()
    )
    min_fb = float(spec.get("min_freeboard_m", 2.0))
    max_g90 = float(spec.get("max_grade_p90", 0.45))
    min_purity = float(spec.get("min_purity", 0.8))
    coastal = bool(spec.get("coastal", False))
    max_ocean = float(spec.get("max_ocean_dist_m", 40.0))
    min_alt = spec.get("min_altitude_m")
    # A harbour pad MUST overlap water -- that is the whole point of a harbour -- so
    # freeboard and grade are evaluated over the pad's LAND cells only, and the pad
    # must be at least this fraction dry. For an inland site the default 1.0 means
    # "every cell dry", which is the stricter and correct reading there.
    min_land = float(spec.get("min_land_fraction", 1.0))
    avoid_water = float(spec.get("avoid_water_m", 0.0))
    near = spec.get("near")
    within = float(spec.get("within_m", 1e9))

    H, W = cor.height.shape
    # Candidate mask on the coarse side first: cheap per-cell tests only.
    ok = f["finite"] & (cor.height > WATER_LEVEL + min_fb)
    if want_id is not None:
        ok &= cor.biome == want_id
    if min_alt is not None:
        ok &= cor.height >= float(min_alt)
    if avoid_water > 0:
        ok &= f["dist_water"] >= avoid_water
    if coastal:
        ok &= f["dist_ocean"] <= max_ocean
    ok[:half, :] = ok[-half:, :] = False
    ok[:, :half] = ok[:, -half:] = False

    zz, xx = np.mgrid[0:H, 0:W]
    wx = xx + cor.x0
    wz = zz + cor.z0
    if near:
        ok &= np.hypot(wx - float(near[0]), wz - float(near[1])) <= within
    for ex, ez, er in exclude:
        ok &= np.hypot(wx - ex, wz - ez) > er

    idx = np.argwhere(ok)
    if not len(idx):
        raise ValueError(f"{spec.get('id')}: no candidate cell satisfies the spec")

    # Rank by the PAD's mean grade, not the centre cell's. A single flat cell in
    # broken ground ranks first on a per-cell grade and then fails every pad test,
    # which is how a solver reports "4000 candidates, none passed" while standing
    # next to a usable meadow. The box filter costs one convolution.
    from scipy.ndimage import uniform_filter

    gfin = np.where(np.isfinite(f["grade"]), f["grade"], 4.0).astype(np.float32)
    pad_grade = uniform_filter(gfin, size=2 * half + 1, mode="nearest")
    proxy = pad_grade[ok]
    if near:
        proxy = proxy + 0.0005 * np.hypot(wx[ok] - float(near[0]), wz[ok] - float(near[1]))
    order = np.argsort(proxy)[:4000]

    best: Site | None = None
    rejects: dict[str, int] = {"nan": 0, "freeboard": 0, "grade": 0, "purity": 0, "land": 0}
    for o in order:
        i, j = idx[o]
        sl = (slice(i - half, i + half + 1), slice(j - half, j + half + 1))
        hpad = cor.height[sl]
        if not np.isfinite(hpad).all():
            rejects["nan"] += 1
            continue
        # ONE threshold, used consistently: a cell is "buildable" when it clears the
        # water datum by min_freeboard_m. Testing "above 30" separately from
        # "freeboard >= 1.5" is what made every coastal pad fail: a beach always
        # contains cells at 30.1 m, which are above water and not buildable, so the
        # pad minimum was always below the threshold no matter where you stood.
        buildable = hpad > WATER_LEVEL + min_fb
        land_frac = float(buildable.mean())
        if land_frac < min_land:
            rejects["land"] += 1
            continue
        if not buildable.any():
            rejects["freeboard"] += 1
            continue
        dry = buildable
        fb = float(cor.height[i, j]) - WATER_LEVEL
        gpad = f["grade"][sl][dry]
        if not gpad.size or not np.isfinite(gpad).all():
            rejects["nan"] += 1
            continue
        g90 = float(np.percentile(gpad, 90))
        if g90 > max_g90:
            rejects["grade"] += 1
            continue
        bpad = cor.biome[sl]
        purity = 1.0 if want_id is None else float((bpad[dry] == want_id).mean())
        if purity < min_purity:
            rejects["purity"] += 1
            continue
        # Lower is better: flat, tall freeboard, pure biome, close to `near`.
        score = g90 + 0.05 * max(0.0, min_fb + 4.0 - fb) + 2.0 * (1.0 - purity)
        if near:
            score += 0.0008 * math.hypot(float(wx[i, j]) - float(near[0]), float(wz[i, j]) - float(near[1]))
        if best is None or score < best.score:
            best = Site(
                id=str(spec.get("id", "site")),
                x=int(wx[i, j]),
                z=int(wz[i, j]),
                y=float(cor.height[i, j]),
                biome=BIOME_NAME.get(int(cor.biome[i, j]), "?"),
                freeboard_m=fb,
                pad_m=pad,
                grade_p90=g90,
                grade_max=float(gpad.max()),
                biome_purity=purity,
                water_dist_m=float(f["dist_water"][i, j]),
                ocean_dist_m=float(f["dist_ocean"][i, j]),
                score=score,
                evidence={
                    "sampler": "run_patchscan.sh, river-inclusive, step 1.0 m, integer-exact",
                    "water_datum_m": WATER_LEVEL,
                    "pad_buildable_fraction": round(land_frac, 3),
                    "pad_lowest_buildable_height_m": round(float(hpad[dry].min()), 2),
                    "pad_lowest_height_m": round(float(hpad.min()), 2),
                    "pad_highest_height_m": round(float(hpad.max()), 2),
                    "candidates_considered": int(len(idx)),
                },
            )
    if best is None:
        raise ValueError(
            f"{spec.get('id')}: {len(idx)} cells passed the per-cell tests, {len(order)} pads "
            f"were tested and none passed (freeboard >= {min_fb}, grade p90 <= {max_g90}, "
            f"purity >= {min_purity}, land fraction >= {min_land}); rejections {rejects}"
        )
    return best


def dry_approach(
    cor: Corridor,
    sx: int,
    sz: int,
    tx: float,
    tz: float,
    path_freeboard_m: float | None = None,
    path_step_m: float | None = None,
) -> dict[str, Any]:
    """Walk 1 m at a time from a point to a target and report whether it stays dry.

    The same rule as derive.py's dry_approach, applied to a Corridor mosaic instead
    of a single probe patch: ANY water fails, and a rise of more than path_step_m in
    one metre fails. The thresholds are IMPORTED from derive.SPAWN_PATH_FREEBOARD_M
    and derive.SPAWN_PATH_STEP_M, so raising the bar there raises it here.

    This is the one part of the spawn verdict a route cannot imply. The route being
    dry says nothing about the last few metres from the doorstep to the building's
    near edge: if that edge sits across a ditch from the road, the corridor is dry
    and the walk still fails.
    """
    if path_freeboard_m is None:
        path_freeboard_m = float(DERIVE.SPAWN_PATH_FREEBOARD_M)
    if path_step_m is None:
        path_step_m = float(DERIVE.SPAWN_PATH_STEP_M)
    span = math.hypot(tx - sx, tz - sz)
    steps = max(1, int(math.ceil(span)))
    free: list[float] = []
    for k in range(steps + 1):
        t = k / steps
        px = int(round(sx + (tx - sx) * t))
        pz = int(round(sz + (tz - sz) * t))
        if not cor.contains(px, pz):
            return {"ok": False, "reason": "approach leaves the sampled corridor"}
        free.append(cor.at(px, pz)[0] - WATER_LEVEL)
    worst_wet = min(free)
    if worst_wet < path_freeboard_m:
        return {"ok": False, "reason": f"approach crosses water ({worst_wet:.2f} m relative to the water plane)"}
    jumps = [abs(free[k + 1] - free[k]) for k in range(len(free) - 1)]
    worst = max(jumps) if jumps else 0.0
    if worst > path_step_m:
        return {"ok": False, "reason": f"approach steps {worst:.2f} m in one metre"}
    return {
        "ok": True,
        "length_m": round(span, 1),
        "min_freeboard_m": round(worst_wet, 2),
        "max_step_m": round(worst, 2),
        "dry_to_footprint_edge": True,
    }


def doorstep(
    cor: Corridor,
    site: Site,
    toward: tuple[float, float],
    fields: dict[str, np.ndarray] | None = None,
    preset: str | None = None,
    footprint_xz_m: tuple[float, float] | None = None,
    clear_m: float | None = None,
    max_m: float | None = None,
    min_freeboard_m: float | None = None,
    disc_r_m: float | None = None,
    disc_freeboard_m: float | None = None,
) -> dict[str, Any]:
    """The named, integer doorstep of an installation: where a player spawns.

    Contract agreed with SpawnOnLand over hub:
      * x / y / z are INTEGERS, Valheim world metres, GetHeight frame. Integers at
        the point of truth -- a float that rounds into a bog is the failure mode.
      * freeboard_m = y - 30.0, so freeboard >= 2.0 and y >= 32.0 are the same
        statement. The datum constant is imported, never restated.
      * NO yaw in this block. ServerCharacters' CharacterTemplate.yml `spawn:` is a
        list of {x, y, z} integers and an unknown key is fatal to the template.
        `approach_yaw_deg` is a SEPARATE, router-only field and must be ignored by
        anything feeding a spawn.
      * `for_placement` is PRESET-QUALIFIED as "<preset>#<id>". `early-dock` is a
        placement id in BOTH pre-elder and deepnorth-sandbox and they solve to
        different sites, so an unqualified id is ambiguous. derive.py's own patch
        request ids use the same "<preset>#<placement>" form.
      * It is a PREFERRED CANDIDATE and NOT YET CONSUMED. As of 2026-09-14
        worlds/derive.py chooses the spawn itself from placements.yaml and reads
        nothing under tools/jumpstart/network/; wiring it in is a follow-up on
        derive.py's probe_spawn. Whatever happens, its 1 m probe stays authoritative.
    Every threshold is IMPORTED from worlds/derive.py by name -- SPAWN_FOOTPRINT_CLEAR_M,
    SPAWN_SEARCH_MAX_M, SPAWN_FREEBOARD_M, SPAWN_DISC_R_M, SPAWN_DISC_FREEBOARD_M --
    rather than restated, so this gate and derive.py's verdict cannot drift apart.
    """
    if clear_m is None:
        clear_m = float(DERIVE.SPAWN_FOOTPRINT_CLEAR_M)
    if max_m is None:
        max_m = float(DERIVE.SPAWN_SEARCH_MAX_M)
    if min_freeboard_m is None:
        min_freeboard_m = float(DERIVE.SPAWN_FREEBOARD_M)
    if disc_r_m is None:
        disc_r_m = float(DERIVE.SPAWN_DISC_R_M)
    if disc_freeboard_m is None:
        disc_freeboard_m = float(DERIVE.SPAWN_DISC_FREEBOARD_M)
    f = fields or _fields(cor)
    dx, dz = float(toward[0]) - site.x, float(toward[1]) - site.z
    norm = math.hypot(dx, dz) or 1.0
    ux, uz = dx / norm, dz / norm
    # Walk outward along the bearing toward the route's next stop, so the doorstep is
    # the point where the road leaves the building -- ground already proven walkable.
    chosen = None
    approach: dict[str, Any] = {}
    r = clear_m
    rejections: list[str] = []
    # The near edge of the building along this bearing, which is what the approach
    # walk has to reach. Without a footprint, fall back to the site centre.
    if footprint_xz_m:
        edge = max(footprint_xz_m) / 2.0
    else:
        edge = site.pad_m / 2.0
    tx, tz = site.x + ux * edge, site.z + uz * edge
    while r <= max_m:
        px, pz = int(round(site.x + ux * r)), int(round(site.z + uz * r))
        if cor.contains(px, pz):
            y, _ = cor.at(px, pz)
            if y - WATER_LEVEL < min_freeboard_m:
                rejections.append(f"{r:.0f} m: freeboard {y - WATER_LEVEL:.2f}")
            elif not _disc_ok(cor, px, pz, disc_r_m, disc_freeboard_m):
                rejections.append(f"{r:.0f} m: {disc_r_m:g} m disc is not dry")
            else:
                walk = dry_approach(cor, px, pz, tx, tz)
                if walk["ok"]:
                    chosen = (px, pz, y)
                    approach = walk
                    break
                rejections.append(f"{r:.0f} m: {walk['reason']}")
        r += 1.0
    if chosen is None:
        raise ValueError(
            f"{site.id}: no doorstep between {clear_m} m and {max_m} m along the bearing "
            f"to {toward} passes freeboard >= {min_freeboard_m} m, a dry {disc_r_m:g} m disc "
            f"and a dry walk to the footprint edge; last rejections {rejections[-4:]}"
        )
    px, pz, y = chosen
    yaw = (math.degrees(math.atan2(-ux, -uz))) % 360.0  # face back at the building
    return {
        "x": int(px),
        "y": int(round(y)),
        "z": int(pz),
        "freeboard_m": round(y - WATER_LEVEL, 2),
        "preset": preset,
        "for_placement": f"{preset}#{site.id}" if preset else site.id,
        "installation": site.id,
        "source": "network-router",
        "units": "Valheim world metres, WorldGenerator.GetHeight frame; water datum 30.0",
        "status": "preferred candidate; NOT consumed by worlds/derive.py as of 2026-09-14",
        "consumer_note": "SpawnOnLand's 1 m probe is authoritative and may refuse this point.",
        "approach": approach,
        # Router-only. NOT spawn-consumable: ServerCharacters has no facing field.
        "approach_yaw_deg": round(yaw, 1),
        "approach_yaw_note": "router-only; do NOT feed to a spawn template",
        "clearance_m": round(r, 1),
    }


def _disc_ok(cor: Corridor, x: int, z: int, radius: float, freeboard: float) -> bool:
    r = int(math.ceil(radius))
    for dz in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if math.hypot(dx, dz) > radius:
                continue
            if not cor.contains(x + dx, z + dz):
                return False
            if cor.at(x + dx, z + dz)[0] - WATER_LEVEL < freeboard:
                return False
    return True
