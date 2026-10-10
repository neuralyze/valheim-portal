#!/usr/bin/env python3
"""The settlement plan: every site, every body, every pad, every portal tag --
measured, and emitted in the two forms other agents consume.

OUTPUTS
  sites.yaml        the interchange `RoadNet` and `Crossings` read. One record
                    per site: id, type, xz, pad_radius_m, road_priority, the
                    approach bearing where the LAYOUT forces one, the ROAD
                    VERDICT (`road`: whether the router's own graph reaches
                    the site on its trunk landmass -- road_reach.py -- with
                    the numbers; a site no road reaches is portal-only), and
                    the measured ground facts behind the choice.
  ledger_ops.jsonl  one JSON object per line, in build order, in
                    `tools/jumpstart/ledger/`'s v1.1 envelope, validated
                    against its own `schema.py`. This is the replay artefact.
  plan.json         everything, for humans and for re-deriving inputs.

WHAT IS MEASURED HERE AND WHAT IS NOT, stated per field rather than in general:
  * street profiles, biome fractions, island membership, distances to
    navigable water, and WHERE to look -- MEASURED at 1 m from the finding
    field (`--patches`: PatchScan `worldgen`, rivers included) by the scripts
    in this directory.
  * every WRITTEN pad's bill and clamp verdict -- MEASURED on the surface the
    engine levels (`site_finder.engine_bill`: PatchScan `heightmap`, per zone,
    over the samples `flatten.build` stamps), and CERTIFIED unit by unit
    against `flatten.build` itself before the plan is written (`site_bill`
    in the plan document).
  * POI stand-offs -- MEASURED per location type from the `LocScan.cs` dump,
    `max(exteriorRadius, interiorRadius, znviewReachM)`. The MOD-FREE limit
    stands: More_World_Locations' POIs are invisible to it and a live marker
    check after `zones_generate` is still required.
  * the margins, gaps, street widths and the protected-location extra --
    TASTE PICKS, labelled as such in `clearance.py` and `layout.py`.
  * the ZDO cost -- the body's own MEASURED piece count. One persistent piece
    is one ZDO.

The build-time fields of a `terrain_write` (the gzip'd `TCData` blob digest,
the generated-heights digest, the `_ZoneCtrl` probe reply) cannot exist before
the write is made, so those ops are emitted as a PLAN with the fields the
executor must fill named explicitly in `pending`. Nothing here pretends to have
measured something it has not.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bodies as BD  # noqa: E402
import clearance  # noqa: E402  the one boundary: TERRAIN_SPREAD_M
import harbour_site as HS  # noqa: E402  siting of the human-declared harbours
import layout as LY  # noqa: E402
import road_reach as RR  # noqa: E402  the road's verdict on every site
import sites as S  # noqa: E402
import terrain1m as T  # noqa: E402

sys.path.insert(0, str(HERE.parent / "terraform"))
import numpy as np  # noqa: E402
import flatten as FL  # noqa: E402  terraform/flatten.py: the WRITER, as a library
import tcdata  # noqa: E402  terraform/tcdata.py: the TerrainComp lattice
# THE site bill: the engine surface, the writer's stamp and
# SITE_BILL_SURFACE_DEPENDS_ON live in blueprints/site_finder.py, beside
# `flatten_cost`, so this solver and `solve_placements.py` bill one way.
import site_finder as SF  # noqa: E402

PATCHES = "/tmp/settle/h1m.bin"


def _field_population(f: T.Field, x: float, z: float, w: float, d: float,
                      yaw_deg: float) -> tuple[np.ndarray, ...]:
    """`terrain1m.pad_bill`'s population: the FINDING field's own lattice
    inside the ROTATED rectangle -- what the solver billed before this
    module read the writer.  `(wx, wz, field heights, field biomes)`."""
    reach = math.hypot(w / 2, d / 2)
    iz0, ix0 = f.idx(x - reach, z - reach)
    iz1, ix1 = f.idx(x + reach, z + reach)
    iz0, ix0 = max(iz0, 0), max(ix0, 0)
    iz1, ix1 = min(iz1, f.n - 1), min(ix1, f.n - 1)
    zz, xx = np.meshgrid(np.arange(iz0, iz1 + 1) * f.step + f.z0,
                         np.arange(ix0, ix1 + 1) * f.step + f.x0, indexing="ij")
    a = math.radians(yaw_deg)
    lx = (xx - x) * math.cos(a) + (zz - z) * math.sin(a)
    lz = -(xx - x) * math.sin(a) + (zz - z) * math.cos(a)
    inside = (np.abs(lx) <= w / 2) & (np.abs(lz) <= d / 2)
    return (xx[inside], zz[inside],
            f.h[iz0:iz1 + 1, ix0:ix1 + 1][inside].astype(np.float64),
            f.biome[iz0:iz1 + 1, ix0:ix1 + 1][inside])


def _field_at(f: T.Field, wx: np.ndarray, wz: np.ndarray) -> tuple:
    """The finding field at world points, by its own nearest-sample rule."""
    iz = np.clip(np.rint((wz - f.z0) / f.step).astype(int), 0, f.n - 1)
    ix = np.clip(np.rint((wx - f.x0) / f.step).astype(int), 0, f.n - 1)
    return f.h[iz, ix].astype(np.float64), f.biome[iz, ix]


def _engine_at(surface: "SF.EngineSurface", wx: np.ndarray, wz: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray, list]:
    """The engine surface at arbitrary points, each in the zone ZoneSystem
    assigns it, bilinear between that zone's lattice vertices -- how
    `Heightmap` renders ground between samples.  Also the unread zones."""
    half = tcdata.ZONE_SIZE / 2
    zx = np.floor((wx + half) / tcdata.ZONE_SIZE).astype(int)
    zz = np.floor((wz + half) / tcdata.ZONE_SIZE).astype(int)
    fx = wx - (zx * tcdata.ZONE_SIZE - half)
    fz = wz - (zz * tcdata.ZONE_SIZE - half)
    j0 = np.clip(np.floor(fx).astype(int), 0, tcdata.PITCH - 2)
    i0 = np.clip(np.floor(fz).astype(int), 0, tcdata.PITCH - 2)
    tx, tz = fx - j0, fz - i0
    h = np.full(wx.shape, np.nan)
    b = np.zeros(wx.shape, np.uint8)
    unread = []
    for key in sorted(set(zip(zx.tolist(), zz.tolist()))):
        m = (zx == key[0]) & (zz == key[1])
        if key not in surface.h:
            unread.append(key)
            continue
        H, i, j = surface.h[key], i0[m], j0[m]
        h[m] = (H[i, j] * (1 - tx[m]) * (1 - tz[m]) + H[i, j + 1] * tx[m] * (1 - tz[m])
                + H[i + 1, j] * (1 - tx[m]) * tz[m] + H[i + 1, j + 1] * tx[m] * tz[m])
        b[m] = surface.b[key][np.rint(fz[m]).astype(int).clip(0, 64),
                              np.rint(fx[m]).astype(int).clip(0, 64)]
    return h, b, unread


def site_bill(surface: "SF.EngineSurface | None", pads, w: float, d: float, *,
              field: T.Field | None = None, target_y: float | None = None,
              yaw_deg: float = 0.0, apron_m: float = 0.0,
              depends_on: tuple | None = None) -> dict:
    """The earthwork bill for `pads` -- one or more `w` x `d` pad centres
    written at ONE target -- AS THE ENGINE WILL LEVEL THEM.

    `target_y` None means choose it: the median of the billed population,
    rounded to the two decimals the plan records and the writer is handed,
    and every deviation below is taken from THAT number.  The result carries
    `terrain1m.pad_bill`'s keys so the searches can rank on it unchanged,
    plus `surface`, `population` and a `verdict`: clear, refused (a sample
    beyond TerrainComp's +/-8 m clamp) or UNREAD (a zone the engine surface
    could not read -- which refuses; it is never a clean bill).

    `depends_on` is the neuter surface, SF.SITE_BILL_SURFACE_DEPENDS_ON read at
    CALL time (so a neuter that edits the declaration reaches every caller):
      without `engine_surface` the heights are the finding field's -- the
        bill the solver made before 2026-09-24, and the one that called
        sudrberg-keep clean;
      without `written_samples` the population is `terrain1m.pad_bill`'s --
        the finding field's lattice inside the rotated rectangle -- rather
        than the samples `flatten.build` stamps.
    """
    if depends_on is None:
        depends_on = SF.SITE_BILL_SURFACE_DEPENDS_ON
    engine = "engine_surface" in depends_on
    written = "written_samples" in depends_on
    pads = [(float(px), float(pz)) for px, pz in pads]
    if (not engine or not written) and field is None:
        raise ValueError("a bill without the engine surface or the written "
                         "samples is a bill on the finding field; pass it")
    if engine and written:
        h, b, unread = SF.engine_samples(surface, pads, w, d, apron_m)
    else:
        # THE NEUTER ARMS: one term of the declaration dropped.
        unread, hs, bs = [], [], []
        for px, pz in pads:
            if written:
                for (zx, zz), rows, cols in SF.written_samples(px, pz, w, d,
                                                               apron_m):
                    zcx, zcz = tcdata.zone_centre(zx, zz)
                    gz, gx = np.meshgrid(zcz + rows - tcdata.PITCH // 2,
                                         zcx + cols - tcdata.PITCH // 2,
                                         indexing="ij")
                    fh, fb = _field_at(field, gx.ravel().astype(float),
                                       gz.ravel().astype(float))
                    hs.append(fh)
                    bs.append(fb)
                continue
            wx, wz, fh, fb = _field_population(field, px, pz, w, d, yaw_deg)
            if engine:
                surface.ensure({tcdata.zone_of(a, c)
                                for a, c in zip(wx.tolist(), wz.tolist())})
                eh, eb, miss = _engine_at(surface, wx, wz)
                unread += miss
                keep = ~np.isnan(eh)
                fh, fb = eh[keep], eb[keep]
            hs.append(fh)
            bs.append(fb)
        h = np.concatenate(hs) if hs else np.zeros(0)
        b = np.concatenate(bs) if bs else np.zeros(0, np.uint8)
    if h.size == 0 and not unread:
        raise ValueError(f"pads {pads} cover no samples")
    return {**SF.bill_stats(h, b, target_y, unread),
            "yaw_deg": round(yaw_deg, 1), "width_m": w, "depth_m": d,
            "apron_m": apron_m,
            "surface": (surface.surface if engine and surface is not None
                        else "worldgen (the finding field)"),
            "population": (SF.STAMP_POPULATION if written else
                           "terrain1m.pad_bill's: the finding field's lattice "
                           "inside the rotated rectangle"),
            "pads": [list(p) for p in pads],
            "depends_on": list(depends_on)}


def _writer():
    """`settlements/build.py` -- the EMITTER -- on first use.  The unit
    derivation (cluster members, 0.1 m rounding), the pad apron and the clamp
    verdict are its, and a bill is only the write's bill if it asks the
    writer rather than restating it.  Loaded BY PATH unless the module
    already imported as `build` is this file: the name trap
    `accept/sections/townsites.py::_plan` records for `plan`."""
    target = HERE / "build.py"
    for name in ("build", "settlements_build_for_plan"):
        mod = sys.modules.get(name)
        if mod is not None and Path(getattr(mod, "__file__", "")).resolve() == target:
            return mod
    import importlib.util                                   # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("settlements_build_for_plan",
                                                  target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["settlements_build_for_plan"] = mod
    spec.loader.exec_module(mod)
    return mod


def _candidate_units(rec: dict) -> list[dict]:
    """The units the EMITTER derives for one outlying record at its `x`, `z`
    -- one for a single body, `cluster` of them for a cluster, each rounded
    as it will be written -- by `build.unit_records` itself, over a stub in
    the shape it reads.  The site's pad, at yaw 0 and a target not yet
    chosen; the body's own footprint when the record carries one."""
    stub = {"id": rec["id"], "cluster": rec.get("cluster", 1),
            "pad_m": rec["pad_m"], "x": rec["x"], "z": rec["z"],
            "pad": {"yaw_deg": 0.0, "width_m": rec["pad_m"],
                    "depth_m": rec["pad_m"], "target_y": 0.0},
            "body": rec.get("body"), "body_sha256": None, "pieces": 0,
            "body_footprint": rec.get("body_footprint"),
            "kind": rec["kind"], "patch": rec.get("patch")}
    return _writer().unit_records({"towns": {}, "outliers": [stub]}, rec["id"])


def written_pads(rec: dict) -> list[tuple[float, float]]:
    """The pad centres the EMITTER derives for one outlying record, by
    `build.unit_records` itself, so the bill covers the pads that are
    written and not the one pad that was searched.  MEASURED on Fresh42: the
    plan billed each treehouse cluster at its centre, and the three written
    members differ from that bill by up to 0.823 m of fill (tree-near-3:
    plan 1.85 m, write 2.673 m)."""
    return [(float(u["x"]), float(u["z"])) for u in _candidate_units(rec)]


def engine_pad_bill(surface: SF.EngineSurface, apron_m: float):
    """`layout.TownPlanner`'s bill: one building pad, at the 0.1 m centre
    `Building` records and `build.unit_records` writes, on `surface` -- at
    its own median, or at `target_y` when the planner is asking whether it
    can share a neighbour's datum."""
    def bill(f: T.Field, x: float, z: float, w: float, d: float,
             yaw_deg: float = 0.0, target_y: float | None = None) -> dict:
        return site_bill(surface, [(round(x, 1), round(z, 1))], w, d,
                         field=f, target_y=target_y, yaw_deg=yaw_deg,
                         apron_m=apron_m)
    return bill


# CLEARING_GATE_DEPENDS_ON: what a sited pad's clearing cylinder must not reach
# -- `clearance.CLEARING_POPULATION_DEPENDS_ON`, asked through the build's own
# `clearing_gate` and nothing else.
#
# THE DEFECT, MEASURED on Fresh42 drive 5.  This solver sited wt-south at
# (-672.1, -372.8) and wt-east at (-844.1, 25.2) clear of every stand-off it
# knew, and the build then REFUSED both pads at step 5 (ledger seq 2077 and
# wt-east's) because their clearing cylinders held Crypt4's and Crypt3's
# INTERIOR doors and torches, at y 5061 -- stranding S8's A end and
# J-southcape-wtsouth with them.  Siting asked `loc.clear` (a stand-off disc
# about each marker) and the build asked what its cylinder would delete: two
# rules, and the siting's could not see a crypt's interior at all.  So the
# siting now asks the build's clearing gate itself, over every unit the
# writer derives for a candidate pad, and a pad whose clearing will refuse is
# not legal.  Neutered to ("pieces",) the drive-5 spots come back legal.
CLEARING_GATE_DEPENDS_ON = clearance.CLEARING_POPULATION_DEPENDS_ON


def clearing_refusals(loc: clearance.Locations, site: str, units: list[dict],
                      depends_on: tuple | None = None) -> list[dict]:
    """Every unit whose clearing the BUILD would refuse, with its numbers:
    `build.clearing_gate` over `build.unit_placement`, the placement the
    build writes for that unit and clears by.  Empty is the pass condition."""
    W = _writer()
    dep = CLEARING_GATE_DEPENDS_ON if depends_on is None else depends_on
    out = []
    for u in units:
        g = W.clearing_gate(loc, W.unit_placement(u, loc, site), dep)
        if g["verdict"] == "clear":
            continue
        room = g["room"] or {}
        b = room.get("binding") or {}
        out.append({"unit": u["id"], "xz": [u["x"], u["z"]],
                    "radius_m": g["radius_m"],
                    "max_radius_m": room.get("max_radius_m"),
                    "location": b.get("name"),
                    "population": b.get("population"),
                    "why": g["why"]})
    return out


# CENSUS_EVIDENCE_DEPENDS_ON: WHAT THE WORLD HOLDS WHERE A PAD WOULD STAND.
#
# THE DEFECT, MEASURED on the Ulfsland regeneration.  This solver sited lh-south
# against the mod-free location dump, and the build then REFUSED the pad at
# step 5 because its clearing cylinder held objects the dump cannot name
# ({'Flies': 3, 'mudpile_beacon': 1, 'StatueEvil': 1, 'vfx_swamp_mist': 3}) --
# a LOST site, which a human would re-site by hand.  The tool must re-site it.
#
# WHAT DATA EXISTS WHEN, stated so the loop sits where its evidence does:
#   stage 8  (this solver)   the mod-free location dump and the height field
#                            ONLY.  No object of the generated world has been
#                            read by any tool at this stage.
#   stage 10 (census)        `roads/precensus.py survey` GENERATES the zones
#                            the provisional routes cross and reads them live:
#                            `roads/census/<seed>-<segment>.json`, whose
#                            `bodies` are the STRUCTURE bodies found (position,
#                            declared radius, prefab histogram, `blocking`).
#                            Corridor zones only, and bodies only -- a lone
#                            prop is not a body.
#   stage 12 step 5 (build)  the exact per-cylinder `objects_count id=*` after
#                            `zones_generate` -- the only complete answer, and
#                            the refusal of last resort.
# So `CensusEvidence` is read from the stage-10 census and handed to a RE-SOLVE
# (`plan.py --census`): a candidate offset whose clearing cylinder meets a
# blocking body is not legal, the slide moves the pad off it, and the next
# legal candidate of the solver's own ranking is taken when none is.  The first
# solve (no census yet) is byte-identical to what shipped.  What this CANNOT see
# is a single prop outside any body (StatueEvil): that stays the step-5
# refusal, now with its exit code (`build.exit_code`).
CENSUS_EVIDENCE_DEPENDS_ON = ("stage_10_census_bodies",)


class CensusEvidence:
    """The blocking structure bodies the stage-10 census read, for a solver to
    keep a pad's clearing cylinder off.  Pure over the documents it is given."""

    def __init__(self, bodies: list[dict]):
        seen: dict[tuple[float, float], dict] = {}
        for b in bodies:
            if not b.get("blocking"):
                continue
            seen.setdefault((round(float(b["x"]), 1), round(float(b["z"]), 1)),
                            dict(b))
        self.bodies = list(seen.values())

    @classmethod
    def from_docs(cls, docs: list[dict]) -> "CensusEvidence":
        return cls([b for d in docs for b in d.get("bodies") or ()])

    @classmethod
    def load(cls, seed: str, census_dir: Path | None = None
             ) -> "CensusEvidence":
        """Every `<seed>-*.json` under `roads/census/`; REFUSES when there is
        none, because an empty evidence file read as 'nothing blocks' is the
        defect this class exists to close."""
        d = census_dir or (HERE.parent / "roads" / "census")
        docs = []
        for f in sorted(d.glob(f"{seed}-*.json")):
            doc = json.loads(f.read_text())
            if doc.get("seed") == seed:
                docs.append(doc)
        if not docs:
            raise SystemExit(
                f"REFUSING --census: no stage-10 census of seed {seed!r} under "
                f"{d}. The census is written by `roads/precensus.py survey` "
                f"(pipeline stage 10); until it exists the solver has no "
                f"object evidence and a re-solve would be the first solve.")
        return cls.from_docs(docs)

    def blockers(self, x: float, z: float, radius_m: float) -> list[dict]:
        """The blocking bodies whose own disc meets the cylinder."""
        out = []
        for b in self.bodies:
            d = math.hypot(float(b["x"]) - x, float(b["z"]) - z)
            reach = radius_m + float(b.get("declared_m") or 0.0)
            if d <= reach:
                out.append({"body": b.get("name"), "xz": [b["x"], b["z"]],
                            "dist_m": round(d, 2), "reach_m": round(reach, 2),
                            "blocking": b.get("blocking"),
                            "prefabs": dict(sorted(
                                (b.get("prefabs") or {}).items(),
                                key=lambda kv: -kv[1])[:6])})
        return sorted(out, key=lambda r: r["dist_m"])


def census_refusals(evidence: "CensusEvidence | None", units: list[dict],
                    radius_of) -> list[dict]:
    """Every unit whose clearing cylinder meets a blocking census body, with
    the numbers.  `radius_of(unit)` is the cylinder's own radius (the build's
    `clearing_gate`).  None evidence refuses nothing -- the first solve."""
    if evidence is None:
        return []
    out = []
    for u in units:
        r = float(radius_of(u))
        hit = evidence.blockers(float(u["x"]), float(u["z"]), r)
        if hit:
            out.append({"unit": u["id"], "xz": [u["x"], u["z"]],
                        "radius_m": round(r, 2), "blockers": hit})
    return out


def engine_outlier_pad(picker: S.Picker, surface: SF.EngineSurface,
                       rec: dict, apron_m: float, search_m: float = 16.0,
                       stride_m: float = 2.0,
                       claims: "SiteClaims | None" = None,
                       evidence: "CensusEvidence | None" = None) -> dict:
    """The outlying site's pad, SLID AND JUDGED ON THE ENGINE SURFACE: the
    same +/-16 m box `build` always searched, the same stand-off test, and
    every offset billed over the pads the emitter will write there.  A box
    with no offset to bill is a refusal, never an absence.

    `claims` is the ground this plan has already given to another site.  An
    offset whose written ground meets it is not LEGAL, exactly as an offset
    inside a location's stand-off is not: the slide moves the pad off it, and
    how many offsets it cost, and whose ground they were, is carried on the
    pad either way (`claim_refused_offsets`, `claimed_by`).

    Nor is an offset where any written unit's clearing cylinder would be
    refused by the build (CLEARING_GATE_DEPENDS_ON): counted the same way
    (`clearing_refused_offsets`, `clearing_bound_by`, per location).

    A DECLARED site (`centre_declared`) is not moved by the gate: it is a pin
    on ground that may be standing, and the gate's interior population is an
    upper bound (the whole zone) that the build's LIVE census then answers
    exactly.  Its kept pad carries the gate's verdict with the numbers
    (`clearing_gate`) wherever the gate would refuse it."""
    f = picker.fields[rec["patch"]]
    tally = {"offsets": 0, "by": {}}
    ctally = {"offsets": 0, "by": {}, "first": None}
    etally = {"offsets": 0, "by": {}, "first": None}
    gated = not rec.get("centre_declared")

    def bill(px: float, pz: float) -> dict:
        centre = {**rec, "x": round(px, 1), "z": round(pz, 1)}
        return site_bill(surface, written_pads(centre), rec["pad_m"],
                         rec["pad_m"], field=f, apron_m=apron_m)

    def units_at(px: float, pz: float) -> list[dict]:
        # THE UNITS THE WRITER WILL WRITE, body footprint included exactly as
        # `SiteClaims.outlier` asks it: without it `build.written_pad` grows
        # the pad to the PAD plus its margin (wt-south 26 m, not the 20 m
        # the plan writes) and the cylinder asked is 4.24 m too wide.
        r = {**rec, "x": round(px, 1), "z": round(pz, 1)}
        fp_of = getattr(claims, "footprint_of", None)
        if fp_of is not None and rec.get("body"):
            fp = fp_of(rec["body"])
            if fp is not None:
                r["body_footprint"] = fp
        return _candidate_units(r)

    def legal(px: float, pz: float, hd: float) -> bool:
        if not (picker.loc.clear(px, pz, hd)
                and picker.installation_conflict(px, pz, hd) is None):
            return False
        bad = (clearing_refusals(picker.loc, rec["id"], units_at(px, pz))
               if gated else [])
        if bad:
            ctally["offsets"] += 1
            ctally["first"] = ctally["first"] or bad[0]
            for b in bad:
                key = str(b["location"])
                ctally["by"][key] = ctally["by"].get(key, 0) + 1
            return False
        if evidence is not None:
            def _radius(u, _loc=picker.loc):
                g = _writer().clearing_gate(
                    _loc, _writer().unit_placement(u, _loc, rec["id"]),
                    CLEARING_GATE_DEPENDS_ON)
                ar = g.get("ar")
                return (ar.radius_m if ar is not None else
                        math.hypot(float(u["pad_w"]), float(u["pad_d"])) / 2
                        + 2.0)
            cbad = census_refusals(evidence, units_at(px, pz), _radius)
            if cbad:
                etally["offsets"] += 1
                etally["first"] = etally["first"] or cbad[0]
                for c in cbad:
                    for b in c["blockers"]:
                        key = str(b["body"])
                        etally["by"][key] = etally["by"].get(key, 0) + 1
                return False
        if claims is None:
            return True
        hits = claims.conflicts(claims.outlier(rec, px, pz))
        if not hits:
            return True
        tally["offsets"] += 1
        for h in hits:
            key = str(h.get("against"))
            tally["by"][key] = tally["by"].get(key, 0) + 1
        return False

    try:
        pad = S.best_pad(
            f, rec["x"], rec["z"], rec["pad_m"], rec["pad_m"],
            search_m=search_m, stride_m=stride_m, must_clear=legal, bill=bill)
    except ValueError as e:
        pad = {"verdict": "no_offset", "why": str(e), "clamp_ok": False}
    if claims is not None:
        pad["claim_refused_offsets"] = tally["offsets"]
        pad["claimed_by"] = dict(sorted(tally["by"].items()))
    # Recorded only where the gate refused an offset: a pad no clearing
    # cylinder was refused at is the pad this solver has always written.
    if ctally["offsets"]:
        pad["clearing_refused_offsets"] = ctally["offsets"]
        pad["clearing_bound_by"] = dict(sorted(ctally["by"].items()))
        pad["clearing_first_refusal"] = ctally["first"]
    # Recorded only where the census refused an offset: the plan carries the
    # numbers of every candidate the evidence moved the pad off.
    if etally["offsets"]:
        pad["census_refused_offsets"] = etally["offsets"]
        pad["census_bound_by"] = dict(sorted(etally["by"].items()))
        pad["census_first_refusal"] = etally["first"]
    if not gated and pad.get("x") is not None:
        kept = clearing_refusals(picker.loc, rec["id"],
                                 units_at(pad["x"], pad["z"]))
        if kept:
            pad["clearing_gate"] = {
                "verdict": "REFUSED", "refusals": kept,
                "depends_on": list(CLEARING_GATE_DEPENDS_ON),
                "kept_because": ("DECLARED: a pin is not moved by the solver; "
                                 "the interior term is the whole-zone upper "
                                 "bound and the build's live cylinder census "
                                 "decides this pad")}
            print(f"  ! {rec['id']} is declared and the clearing gate refuses "
                  f"its pad: {kept[0]['why']}", file=sys.stderr)
    return pad


# --- ONE SITE'S GROUND IS ONE SITE'S GROUND ---------------------------------
# SITE_CLAIM_DEPENDS_ON = (every_kind, written_samples, bodies)
#
# THE DEFECT, MEASURED 2026-09-24.  Nothing in this solver compared one
# site's ground with another's unless the two were the SAME KIND:
# `sites.dedupe` (called per kind in `select_outliers`) collapses one kind's
# candidates within 40 m, `sites.peaks` separates one kind's peaks on one
# window, and `layout.TownPlanner.place`'s `obb_gap` test keeps one town's
# BODIES 2.5 m apart.  A lighthouse was never compared with
# a treehouse, a tower with a town, a building's PAD with its neighbour's.
# So lh-west and tree-near stood 5.7 m apart in the first Fresh42 plan, their
# stamps sharing 168 + 272 + 238 lattice samples at targets 0.40 m apart,
# and every unit certified clean because `certify_plan` billed each ALONE.
# The re-solve moved them 22.4 m apart by luck, not by rule -- and
# tree-near-3 STILL writes 24 of lh-west's samples, 1.69 m under it: a
# centre distance is not a claim.  Inside the towns, with the pads the
# writer stamps (`build.unit_records`, grown to each body at its yaw): 15
# building pairs of Fresh42 and 21 of Ulfsland's plan.json write each
# other's samples at targets up to 2.79 m apart, most of them under the
# other building's BODY, because the gap was measured between bodies and
# every pad is its body plus 3 m.
#
# THE CLAIM IS THE WRITE, derived by asking the writer and nothing else:
#   written_samples  every TerrainComp lattice sample `flatten.build` stamps
#                    for every unit `build.unit_records` writes for the site
#                    -- each cluster member at its own centre, rounded as
#                    written -- through `SF.written_samples`, the stamp
#                    `certify_plan` holds to the writer.  Without it the
#                    claim is the one pad the search slid, and tree-near-3's
#                    24 samples come back unclaimed.
#   bodies           the body's footprint at its yaw, rotated as the EMITTER
#                    rotates it (`base_geometry.xz_footprint`:610, which is
#                    the MIRROR of `layout.obb_corners`).  A sample another
#                    write stamps within `clearance.TERRAIN_SPREAD_M` of it
#                    moves the ground under its pieces.  Without it, a yawed
#                    body whose corner stands off its own stamp --
#                    stenvik-hall-1, 20.1 m square at yaw 30 on a 26.1 m pad
#                    -- can have a neighbour levelled under that corner.
#   every_kind       the population a candidate is judged against: EVERY
#                    claim already in the plan, every town building and every
#                    outlier of every kind.  Without it, only its own kind --
#                    the population `sites.dedupe` compares -- and lh-west and
#                    tree-near at 5.7 m are both accepted.
#
# THE OWNER OF A SAMPLE IS ONE WRITE: a site, at one target.  A cluster's
# members overlap by design and are billed at one target, so they are one
# owner; two buildings of one town are two writes unless their targets agree
# to the centimetre the plan records, when the stamped delta is the same
# number whichever lands last.  Two SITES are always two owners.
#
# THE CLEARANCE IS DERIVED, NOT CHOSEN.  Zero samples in common -- a sample
# two writes stamp holds whichever landed last, so the other's bill is not
# its write -- and no foreign sample inside TERRAIN_SPREAD_M of a body.  That
# second figure is `clearance`'s ONE BOUNDARY (reach + half-width +
# TERRAIN_SPREAD_M, half-width zero because a stamp IS its footprint) and the
# ledger's flatten guard over an owned point (`accept/sections/settlements
# .py::repair_ownership`'s SAMPLE_M), applied to our own bodies instead of a
# location's.  No taste margin is added: a second number would be a second
# boundary.  The POI stand-off discs and recorded installations still apply
# as before, through `picker.loc` and `installation_conflict`.
#
# UNREAD IS NOT CLEAR: a claim with no unit, or a unit that stamps no sample,
# is REFUSED.  A claim index that returned nothing would otherwise accept
# every candidate against it.
SITE_CLAIM_DEPENDS_ON = ("every_kind", "written_samples", "bodies")


def _claim_terms(depends_on) -> tuple:
    """The declaration, read at CALL time so a neuter reaches every caller."""
    return tuple(SITE_CLAIM_DEPENDS_ON if depends_on is None else depends_on)


def unit_place(u: dict) -> dict:
    """The placement `flatten.build` is handed for one unit of
    `build.unit_records` -- `build.flatten_pad`'s own construction."""
    return {"id": u["id"], "solved": {
        "x": u["x"], "z": u["z"],
        "footprint_orientation": (f"{u['pad_w']:g} m along x by "
                                  f"{u['pad_d']:g} m along z"),
        "flatten_cost": {"target_y": u["target_y"]}}}


def stamped_samples(u: dict) -> np.ndarray:
    """Every lattice sample `flatten.build` stamps for one unit, as integer
    world `(x, z)`, each ONCE -- a seam row is stamped in both zones that
    hold it and is one point of ground."""
    w, d = FL.pad_extent(unit_place(u))
    off = (np.arange(tcdata.PITCH) - tcdata.PITCH // 2) * tcdata.SCALE
    pts = []
    for (zx, zz), rows, cols in SF.written_samples(
            float(u["x"]), float(u["z"]), w, d, _writer().APRON_M):
        zcx, zcz = tcdata.zone_centre(zx, zz)
        gx, gz = np.meshgrid(zcx + off[cols], zcz + off[rows])
        pts.append(np.stack([gx.ravel(), gz.ravel()], axis=1))
    if not pts:
        return np.zeros((0, 2), np.int64)
    return np.unique(np.rint(np.concatenate(pts)).astype(np.int64), axis=0)


def body_gap(pts: np.ndarray, u: dict) -> float:
    """Metres from the nearest of `pts` to the unit's BODY: `foot_w` x
    `foot_d` about its centre, yawed as the emitter yaws it --
    x' = x cos + z sin, z' = z cos - x sin (`base_geometry.xz_footprint`)."""
    if not len(pts):
        return math.inf
    a = math.radians(float(u.get("yaw") or 0.0))
    c, s = math.cos(a), math.sin(a)
    dx = pts[:, 0] - float(u["x"])
    dz = pts[:, 1] - float(u["z"])
    lx, lz = dx * c - dz * s, dx * s + dz * c
    ex = np.maximum(np.abs(lx) - float(u["foot_w"]) / 2.0, 0.0)
    ez = np.maximum(np.abs(lz) - float(u["foot_d"]) / 2.0, 0.0)
    return float(np.hypot(ex, ez).min())


def outlier_units(rec: dict, depends_on=None) -> list[dict]:
    """The units whose ground one outlying site claims at its `x`, `z`: the
    writer's (`written_samples`), or -- the neuter -- the one pad the search
    slid, at the site's centre."""
    if "written_samples" in _claim_terms(depends_on):
        return _candidate_units(rec)
    fw, fd = rec.get("body_footprint") or (rec["pad_m"], rec["pad_m"])
    return [{"id": rec["id"], "x": round(float(rec["x"]), 1),
             "z": round(float(rec["z"]), 1), "yaw": 0.0,
             "pad_w": rec["pad_m"], "pad_d": rec["pad_m"],
             "foot_w": fw, "foot_d": fd, "target_y": 0.0}]


def building_units(town: str, patch: str, b: dict) -> list[dict]:
    """The unit the EMITTER writes for one town building, by
    `build.unit_records` over a one-building town."""
    return _writer().unit_records(
        {"towns": {town: {"buildings": [b], "patch": patch}}, "outliers": []},
        town)


_KEY = 1 << 21        # |x|, |z| < 2^20 m: every world this tree can generate


class SiteClaims:
    """The ground every write already in this plan claims, and the one
    question asked of it: does THIS claim touch ground that belongs to
    another write?  One instance per solve, filled in placement order --
    towns, then declared outliers, then searched ones -- so what is already
    claimed is exactly what the next candidate must yield to."""

    def __init__(self, footprint_of=None):
        self.claims: list[dict] = []
        # `body name -> (footprint_x_m, footprint_z_m)`; absent, a unit's
        # body is its pad, which is `build.unit_records`' own fallback.
        self.footprint_of = footprint_of

    @staticmethod
    def claim(site: str, kind: str, units: list[dict],
              target_y: float | None = None) -> dict:
        """One write's claim: its units' stamped samples and bodies."""
        us = []
        for u in units:
            pts = stamped_samples(u)
            r = math.hypot(float(u["foot_w"]), float(u["foot_d"])) / 2.0
            x, z = float(u["x"]), float(u["z"])
            lo = pts.min(axis=0) if len(pts) else np.array([x, z])
            hi = pts.max(axis=0) if len(pts) else np.array([x, z])
            us.append({
                "id": u["id"], "x": x, "z": z,
                "yaw": float(u.get("yaw") or 0.0),
                "foot_w": float(u["foot_w"]), "foot_d": float(u["foot_d"]),
                "samples": pts,
                "keys": np.sort((pts[:, 0] + _KEY) * (2 * _KEY)
                                + (pts[:, 1] + _KEY)),
                "box": (min(float(lo[0]), x - r), min(float(lo[1]), z - r),
                        max(float(hi[0]), x + r), max(float(hi[1]), z + r))})
        return {"site": site, "kind": kind,
                "target_y": None if target_y is None else round(float(target_y), 2),
                "units": us}

    def outlier(self, rec: dict, x: float, z: float,
                target_y: float | None = None, depends_on=None) -> dict:
        """The claim of outlying site `rec` if its pad is centred at (x, z),
        rounded as the plan records and the writer is handed."""
        r = {**rec, "x": round(float(x), 1), "z": round(float(z), 1)}
        if self.footprint_of is not None and rec.get("body"):
            fp = self.footprint_of(rec["body"])
            if fp is not None:
                r["body_footprint"] = fp
        return self.claim(rec["id"], rec["kind"],
                          outlier_units(r, depends_on), target_y)

    def building(self, town: str, patch: str, b: dict) -> dict:
        """The claim of one town building `b` (a `layout.Building` as a
        dict), at the target its billed pad carries."""
        return self.claim(town, "town", building_units(town, patch, b),
                          b["pad"].get("target_y"))

    @staticmethod
    def building_unit(town: str, patch: str, b: dict) -> dict:
        """The unit the writer will write for building `b`, whose pad extent
        is what a town bill must price."""
        return building_units(town, patch, b)[0]

    def add(self, claim: dict) -> None:
        self.claims.append(claim)

    @staticmethod
    def _other_write(o: dict, c: dict) -> bool:
        if o["site"] != c["site"]:
            return True
        return (o["target_y"] is not None and c["target_y"] is not None
                and o["target_y"] != c["target_y"])

    def conflicts(self, claim: dict, depends_on=None) -> list[dict]:
        """Every place `claim` meets ground another write already claims,
        with its numbers.  Empty is the pass condition."""
        dep = _claim_terms(depends_on)
        spread = clearance.TERRAIN_SPREAD_M
        empty = [u["id"] for u in claim["units"] if not len(u["samples"])]
        if not claim["units"] or empty:
            return [{"site": claim["site"], "unit": empty[0] if empty else None,
                     "against": None, "verdict": "unread",
                     "why": (f"{claim['site']}: unit(s) {empty or '(none)'} "
                             f"stamp no lattice sample, so the ground this "
                             f"site would claim is unread, not clear")}]
        out: list[dict] = []
        for o in self.claims:
            if not self._other_write(o, claim):
                continue
            if "every_kind" not in dep and o["kind"] != claim["kind"]:
                continue
            for u in claim["units"]:
                for v in o["units"]:
                    ub, vb = u["box"], v["box"]
                    if (ub[0] > vb[2] + spread or vb[0] > ub[2] + spread
                            or ub[1] > vb[3] + spread or vb[1] > ub[3] + spread):
                        continue
                    shared = int(np.intersect1d(u["keys"], v["keys"],
                                                assume_unique=True).size)
                    under_mine = under_theirs = math.inf
                    if "bodies" in dep:
                        under_mine = body_gap(v["samples"], u)
                        under_theirs = body_gap(u["samples"], v)
                    if not (shared or under_mine < spread
                            or under_theirs < spread):
                        continue
                    out.append({
                        "site": claim["site"], "unit": u["id"],
                        "against": o["site"], "against_kind": o["kind"],
                        "against_unit": v["id"], "verdict": "claimed",
                        "shared_samples": shared,
                        "their_stamp_to_my_body_m": (
                            None if math.isinf(under_mine)
                            else round(under_mine, 3)),
                        "my_stamp_to_their_body_m": (
                            None if math.isinf(under_theirs)
                            else round(under_theirs, 3)),
                        "targets": [claim["target_y"], o["target_y"]],
                        "centre_m": round(math.hypot(u["x"] - v["x"],
                                                     u["z"] - v["z"]), 2)})
        return out


def claim_brief(hits: list[dict], limit: int = 4) -> str:
    """A refusal's numbers, one clause per conflict."""
    parts = []
    for h in hits[:limit]:
        if h.get("verdict") == "unread":
            parts.append(h["why"])
            continue
        parts.append(
            f"{h['unit']} meets {h['against_unit']} ({h['against']}, "
            f"{h['against_kind']}) {h['centre_m']} m centre to centre: "
            f"{h['shared_samples']} lattice sample(s) stamped by both, their "
            f"stamp {h['their_stamp_to_my_body_m']} m from this body, this "
            f"stamp {h['my_stamp_to_their_body_m']} m from theirs (clearance "
            f"{clearance.TERRAIN_SPREAD_M:g} m), targets {h['targets']}")
    more = len(hits) - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


def claim_audit(plan: dict, depends_on=None) -> dict:
    """EVERY WRITE THIS PLAN MAKES against every other: each site's
    `build.unit_records` units grouped by the target they are written at,
    judged in placement order.  The plan-level check -- it needs no solver
    and reads nothing but the plan and the writer."""
    B = _writer()
    recs = {r["id"]: r for r in plan["outliers"]}
    claims = SiteClaims()
    found: list[dict] = []
    writes = units = 0
    for site in list(plan["towns"]) + list(recs):
        if site in recs and "written_samples" not in _claim_terms(depends_on):
            rec = recs[site]
            groups = {rec["pad"]["target_y"]: outlier_units(rec, depends_on)}
        else:
            groups: dict = {}
            for u in B.unit_records(plan, site):
                groups.setdefault(round(float(u["target_y"]), 2), []).append(u)
        kind = recs[site]["kind"] if site in recs else "town"
        for target, us in groups.items():
            c = SiteClaims.claim(site, kind, us, target)
            found += claims.conflicts(c, depends_on)
            claims.add(c)
            writes += 1
            units += len(us)
    return {"depends_on": list(_claim_terms(depends_on)),
            "writes": writes, "units": units,
            "clearance_m": clearance.TERRAIN_SPREAD_M,
            "conflicts": found,
            "refused": [f"{h.get('unit') or h['site']}: ground claimed -- "
                        + claim_brief([h]) for h in found],
            "method": ("every lattice sample terraform/flatten.py::build "
                       "stamps for every settlements/build.py::unit_records "
                       "unit, owned by (site, target_y); two owners may not "
                       "stamp one sample, and no foreign sample may lie "
                       "within clearance.TERRAIN_SPREAD_M of a body at its "
                       "yaw (the emitter's rotation)")}


def certify_plan(plan: dict, surface: SF.EngineSurface) -> dict:
    """EVERY UNIT THE EMITTER WILL WRITE, billed by the WRITER ITSELF on the
    surface it writes to -- `flatten.build` over `build.unit_records`, the
    exact call `build.flatten_pad` makes with the scan swapped for the read
    zones -- against this module's own bill at the same target.  A unit
    whose two bills differ in any figure at 1 mm, whose zones were not read,
    or that `tcdata.clamp_verdict` refuses, is named in `refused`.

    AND THE WRITES AGAINST EACH OTHER.  Each unit is billed ALONE above, and
    a unit whose samples another write also stamps is not billed on the
    ground it will stand on: whichever lands last holds them.  `claim_audit`
    names every such pair, and every one is a refusal here."""
    B = _writer()
    units: dict[str, dict] = {}
    refused: list[str] = []
    for site in list(plan["towns"]) + [r["id"] for r in plan["outliers"]]:
        for u in B.unit_records(plan, site):
            place = unit_place(u)
            w, d = FL.pad_extent(place)
            zones = FL.zones_for(float(u["x"]), float(u["z"]),
                                 w + 2 * B.APRON_M, d + 2 * B.APRON_M)
            miss = surface.ensure(zones)
            if miss:
                refused.append(f"{u['id']}: engine surface unread for {miss}")
                units[u["id"]] = {"site": site, "verdict": "unread",
                                  "unread_zones": [list(z) for z in miss]}
                continue
            st = FL.build(place, {f"z_{a}_{b}": surface.patch((a, b))
                                  for a, b in zones},
                          zones, B.PAINT, B.APRON_M, {}, {})
            mine = site_bill(surface, [(u["x"], u["z"])], w, d,
                             target_y=u["target_y"], apron_m=B.APRON_M)
            agree = (st["samples"] == mine["samples"]
                     and st["over_clamp"] == mine["over_clamp"]
                     and abs(st["max_fill"] - mine["max_fill_m"]) < 1e-9
                     and abs(-st["max_cut"] - mine["max_cut_m"]) < 1e-9)
            verdict = tcdata.clamp_verdict(st)["verdict"]
            units[u["id"]] = {
                "site": site, "target_y": u["target_y"],
                "samples": st["samples"], "over_clamp": st["over_clamp"],
                "max_fill_m": st["max_fill"], "max_cut_m": st["max_cut"],
                "zones": [list(z) for z in zones],
                "bill_is_the_write": agree, "clamp_verdict": verdict}
            if not agree:
                refused.append(
                    f"{u['id']}: this module bills samples {mine['samples']} "
                    f"over_clamp {mine['over_clamp']} fill {mine['max_fill_m']} "
                    f"cut {mine['max_cut_m']}; flatten.build writes samples "
                    f"{st['samples']} over_clamp {st['over_clamp']} fill "
                    f"{st['max_fill']} cut {st['max_cut']}")
            elif verdict != "clear":
                refused.append(
                    f"{u['id']}: over_clamp {st['over_clamp']} fill "
                    f"{st['max_fill']} cut {st['max_cut']} at target "
                    f"{u['target_y']} on the {surface.surface} surface")
    claims = claim_audit(plan)
    refused += claims["refused"]
    return {"surface": surface.surface, "seed": surface.seed,
            "depends_on": list(SF.SITE_BILL_SURFACE_DEPENDS_ON),
            "units_billed": len(units), "refused": refused,
            "method": ("each unit of build.unit_records stamped by "
                       "terraform/flatten.py::build on the per-zone "
                       f"{surface.surface} PatchScan zones this solve read, "
                       "and compared figure for figure with "
                       "settlements/plan.py::site_bill at the same target; "
                       "then every write against every other by "
                       "settlements/plan.py::claim_audit"),
            "claims": {k: v for k, v in claims.items() if k != "refused"},
            "scans": surface.scans, "units": units}



# --- THE OUTLYING SITES: WHAT IS AUTHORED, AND WHAT IS A READING OF ONE SEED
# Same cut as the towns below. WHAT to build is authored design and survives
# any seed: the id, the portal tag, the body, the pad, how many of them, what
# road priority it gets. WHERE it stands is not, and neither is the prose
# justifying it -- every number in those `why` strings is a measurement of
# Pirate68's ground.
#
# AND `patch` IS THE SAME KIND OF THING AS `x` AND `z`, which is the part that
# had never been separated: `patch="mainland"` is not "which landmass the
# watchtower is on", it is "which of ULFSLAND'S SEVEN SCAN WINDOWS it was
# found in". MEASURED 2026-09-22 by driving a real derived field for another
# seed: `KeyError: 'mainland'` at the first outlier, because Fresh42's windows
# are trunk-primary / trunk-secondary / outlier-*, and 14 of 14 records named
# a window that seed does not have.
#
# `road_priority` IS A DECLARATION THE ROAD NETWORK READS (it was published and
# read by nothing -- `roads/spec.py` carried its own hand-typed legs): `trunk`
# or `spur` is the carriageway class the site is to be reached by, and `none`
# is "portal-only, by design".  Whether a road CAN reach a declared-roaded site
# is MEASURED, not declared (`road_reach.py`): the solver takes the best
# candidate a road can reach, and a site no candidate of which a road can
# reach publishes `road.roaded: false` with the numbers.
#
# `role` IS WHAT THE NAME MEANS, and it is what binds a name to a place.  The
# solver used to bind by SEARCH RANK -- `wt-spawn` was whatever tower the
# search ranked first -- so on Pirate68 with the pins removed `wt-spawn` stood
# 1.9 km from the StartTemple and `wt-north` on another island, and a leg typed
# for one seed's `wt-spawn` ran to another seed's accident.  The operator's own
# words for each site (git 845678d, `why`) say what it IS: `wt-spawn` "205 m
# from the StartTemple -- the first built thing a player on foot can see",
# `wt-town` the tower "that watches the town's own approach", `wt-peak` "the
# highest watchtower", `wt-east` "at the east end of the island", `lh-south`
# "the southern sea approach", `lh-west` "the west isle's north cape",
# `tree-near` "the nearest built thing to the temple".  `ROLES` encodes those
# as the ordering each name asks of the candidates; `select_outliers` picks,
# for each name in turn, the candidate that best fits it among the best
# candidates of its kind that remain (as many as there are names still to
# place), so the SET of places is still the search's own and only the NAMES
# move onto what they say.  `window` is the trunk landmass a name belongs on
# (the west-isle sites hang from west-isle nodes); it is a preference, and a
# window with no candidate falls back to the other and records that it did.
OUTLIERS: list[dict] = [
    dict(id="hognest-castle", kind="castle", tag="u-castle", pad_m=44.0,
         body="hs_ashlands_neletit_portalhubtower.blueprint",
         road_priority="spur", role="nearest_town"),
    dict(id="sudrberg-keep", kind="castle", tag="u-sudrberg", pad_m=44.0,
         body="hs_mistlands_sheepshank_trophytowerv2.blueprint",
         road_priority="none", role="farthest_spawn"),
    dict(id="wt-spawn", kind="watchtower", tag="u-wtspawn", pad_m=20.0,
         body="outposttower.blueprint", road_priority="trunk",
         approach_bearing_deg=None, role="nearest_spawn"),
    dict(id="wt-south", kind="watchtower", tag="u-wtsouth", pad_m=20.0,
         body="outposttower.blueprint", road_priority="trunk",
         role="south"),
    dict(id="wt-town", kind="watchtower", tag="u-wttown", pad_m=20.0,
         body="outposttower.blueprint", road_priority="trunk",
         role="nearest_town"),
    dict(id="wt-peak", kind="watchtower", tag="u-wtpeak", pad_m=20.0,
         body="outposttower.blueprint", road_priority="spur",
         role="highest"),
    dict(id="wt-north", kind="watchtower", tag="u-wtnorth", pad_m=20.0,
         body="outposttower.blueprint", road_priority="trunk",
         role="north"),
    dict(id="wt-east", kind="watchtower", tag="u-wteast", pad_m=20.0,
         body="outposttower.blueprint", road_priority="spur",
         role="east"),
    dict(id="lh-south", kind="lighthouse", tag="u-lhsouth", pad_m=22.0,
         body="drake-lighthouse.blueprint", road_priority="spur",
         role="south"),
    dict(id="lh-east", kind="lighthouse", tag="u-lheast", pad_m=22.0,
         body="drake-lighthouse.blueprint", road_priority="spur",
         role="east"),
    dict(id="lh-west", kind="lighthouse", tag="u-lhwest", pad_m=22.0,
         body="hs_plains_ulf_the_builder_lighthouse.blueprint",
         # ALTERNATES ARE AUTHORED TOO -- a second body this project is willing
         # to build for this site, in preference order, and NOT a licence to
         # substitute.  `select_outliers` may only reach for one where the site
         # itself is being chosen by the search; a DECLARED site keeps its
         # declared body verbatim and refuses, because it is published with
         # that body's piece count and sha256 and may be standing.
         #
         # MEASURED 2026-09-22 by SpawnableRepair through the live emitter:
         # the ulf body refuses on `unsheltered_deck` -- 10 deck columns
         # enclosed by walls and open to the sky, 50 open of 170, under a
         # 48-panel roof -- on EVERY seed, because the defect is in the file.
         # drake-lighthouse emits clean at 1255 commands and is already the
         # body of lh-south and lh-east.
         alternates=("drake-lighthouse.blueprint",),
         road_priority="spur", role="west", window="secondary"),
    dict(id="tree-sth", kind="treehouse", tag="u-treesth", pad_m=16.0,
         body="hs_meadows_stilt_house.blueprint", road_priority="spur",
         cluster=3, role="south"),
    dict(id="tree-west", kind="treehouse", tag="u-treewest", pad_m=16.0,
         body="hs_meadows_stilt_house.blueprint", road_priority="spur",
         cluster=3, role="west", window="secondary"),
    dict(id="tree-near", kind="treehouse", tag="u-treenear", pad_m=16.0,
         body="hs_meadows_stilt_house.blueprint", road_priority="spur",
         cluster=3, role="nearest_spawn", window="secondary"),
]

# WHAT EACH ROLE ASKS OF A CANDIDATE: a sort key (lower fits better) and the
# words the site's `why` quotes.  Compass roles use the world frame (+z north,
# +x east).  `nearest_spawn` / `farthest_spawn` read the candidate's own
# measured `dist_spawn_m`; `nearest_town` reads the first town's centre
# (`context["town"]`), the one place the solver has already put a town.
ROLES: dict[str, tuple[str, object]] = {
    "nearest_spawn": ("nearest the StartTemple",
                      lambda s, c: s.dist_spawn_m),
    "farthest_spawn": ("farthest from the StartTemple",
                       lambda s, c: -s.dist_spawn_m),
    "nearest_town": ("nearest the first town",
                     lambda s, c: math.hypot(s.x - c["town"][0],
                                             s.z - c["town"][1])),
    "south": ("southernmost", lambda s, c: s.z),
    "north": ("northernmost", lambda s, c: -s.z),
    "east": ("easternmost", lambda s, c: -s.x),
    "west": ("westernmost", lambda s, c: s.x),
    "highest": ("the most prominent", lambda s, c: -s.rise_m),
}

# THE NEUTER (accept townsites): "rank" restores the shipped binding -- each
# name takes the next candidate the search ranked, whatever it is.
_ROLE_MODE = "role"         # role | rank (the shipped)

# NO OUTLYING SITE IS DECLARED.  Every site of `OUTLIERS` is the SEARCH's own
# top-ranked candidate of its kind on the seed being solved, judged by the
# engine and the claims (`select_outliers`).  There used to be a 14-record
# `ANCHORED_OUTLIERS['Pirate68']` here -- coordinates, windows and "MEASURED"
# prose -- and it was FROZEN SOLVER OUTPUT: the places this same search had
# returned on an earlier run, kept so the built network would not move.  It
# decided nothing, and a regeneration that reads it replays a recording
# instead of applying the rules.  The only sites a person declares are the
# harbours in `worldseed.HUMAN_SITES` (`declared_harbours` below); an operator
# can still declare one outlier for one solve with `--expect-sites`.

# --- THE TOWNS: WHAT IS AUTHORED, AND WHAT IS A READING OF ONE SEED -------
# A town's DESIGN survives any seed: its id, its portal tag, its building
# spec, which way its gate faces, whether it is a town or a village. Its
# PLACE does not -- a coordinate is a reading of one world's ground -- and
# neither does the prose that justifies the place, every number of which is a
# measurement of Pirate68.
TOWNS = [
    dict(id="stenvik", tag="u-stenvik", spec=LY.STENVIK_SPEC, type="town",
         road_priority="trunk", window="primary",
         gate="south-west end of the main street"),
    dict(id="vestvik", tag="u-vestvik", spec=LY.VESTVIK_SPEC, type="village",
         road_priority="trunk", window="secondary",
         gate="east end of the main street, toward the quay"),
]

# NO TOWN CENTRE IS DECLARED.  `stenvik` and `vestvik` are the district
# search's own top-ranked candidates on the seed's land (`sites.Picker.town`:
# Meadows purity, relief, coast, spawn distance -- all measured on the seed's
# own height field), bound by RANK in TOWNS order.  There used to be an
# `ANCHORED_TOWNS['Pirate68']` here pinning (496, 904) and (-906, 1846) with
# prose; it was the search's own answer from an earlier run, frozen.  MEASURED
# on the regenerated Pirate68 field, with nothing declared, the search returns
# exactly those two as ranks 0 and 1 (and a third at (-262, 1998)), so
# removing the pin moves nothing the rules had not already chosen -- and a
# search that stops returning them now moves the towns instead of refusing
# against a recording.  An operator can still pin a town for one solve with
# `--expect-towns id=x,z`, and the refusal below then applies to that pin.

# Pairing: every portal's far end is the world's portal hall, whose hub-side
# tags are WayFinding's to allocate. Recorded as a declared counterparty rather
# than invented here, because a tag invented at this end is a one-ended pair.
PAIR_OTHER_END = "WayFinding: portal hall hub end"


def island_membership(picker: S.Picker, patches=None) -> dict:
    """Which landmass each patch's spawn-side component is, so 'walkable from
    spawn' is a measurement rather than a distance.

    `patches` are the TRUNK windows by role (`sites.trunk_patches`), not the
    literal ("mainland", "westisle") this iterated until 2026-09-21: those are
    Ulfsland's landmass names, and on a derived field there is no window called
    either, so every site would have fallen through `site_island`'s
    "separate landmass, portal-only" branch -- an INVENTED answer, not a
    measured one, and it reads the same as the real one.
    """
    out = {}
    for pid in (patches if patches is not None
                else S.trunk_patches(picker.fields)):
        f = picker.fields[pid]
        lab, comps = T.land_components(f, min_area_m2=100000.0)
        sx, sz = picker.loc.spawn
        spawn_label = None
        if f.contains(sx, sz):
            iz, ix = f.idx(sx, sz)
            spawn_label = int(lab[iz, ix]) or None
        out[pid] = {"labels": lab, "components": comps, "spawn_label": spawn_label}
    return out


def site_island(islands: dict, picker: S.Picker, patch: str, x: float, z: float) -> dict:
    info = islands.get(patch)
    if info is None:
        return {"same_landmass_as_spawn": False,
                "evidence": f"patch {patch} does not contain the spawn point; "
                            f"separate landmass, portal-only"}
    f = picker.fields[patch]
    iz, ix = f.idx(x, z)
    lab = int(info["labels"][iz, ix])
    same = bool(lab and info["spawn_label"] and lab == info["spawn_label"])
    area = next((c["area_m2"] for c in info["components"] if c["label"] == lab), None)
    return {"landmass_label": lab, "landmass_area_m2": area,
            "same_landmass_as_spawn": same,
            "evidence": ("MEASURED: 4-connected land (h > 30, the measured "
                         "c_WaterLevel) at 1 m puts this site in the same "
                         "component as the StartTemple"
                         if same else
                         "MEASURED: 4-connected land at 1 m puts this site in a "
                         "DIFFERENT component from the StartTemple -- it is not "
                         "walkable from spawn and needs a portal or a boat")}


def block_rise(picker: S.Picker, patch: str, x: float, z: float,
               radius_m: float = 160.0) -> float:
    """Height above the lowest ground within `radius_m`, read at the pad's own
    block. This is the prominence figure quoted for a tower or a castle: a
    local maximum says nothing about whether a tower there overlooks anything,
    and this does."""
    blk = picker.blocks[patch]
    rise = S.win_rise(blk, radius_m)
    iz = min(max(int(round((z - blk.z0) / blk.step)), 0), rise.shape[0] - 1)
    ix = min(max(int(round((x - blk.x0) / blk.step)), 0), rise.shape[1] - 1)
    return float(rise[iz, ix])


def scope_of(patch_path: str, loc_path: str) -> dict:
    """The seed and world THIS PLAN WAS ACTUALLY SOLVED ON, from its inputs.

    NOT A LITERAL, AND NOT `$VH_SEED` EITHER.  Until 2026-09-21 this was
    `{"seed": "Pirate68", "seed_hash": 147627509}` written straight into the
    dict, and `sites_yaml` wrote `world: Ulfsland` next to it.  Every
    `sites.yaml` this module has ever produced therefore declared Pirate68
    whatever it was solved on, which makes a downstream seed check a
    comparison against a constant -- the check passes, the plan is still the
    wrong planet, and now there is a signed declaration saying otherwise.  A
    false declaration is strictly worse than none.

    The seed is taken from the PatchScan header of the height field the solve
    read, because that is the artefact the coordinates actually came out of.
    `$VH_SEED` would be an assertion about intent; this is a reading of the
    input.  The two are cross-checked: a solve whose field and whose requested
    seed disagree is refused by `worldseed.require_scope`, which is the same
    refusal `roads/field.py` makes for the same reason.
    """
    sys.path.append(str(HERE.parent / "roads"))
    sys.path.append(str(HERE.parent))
    import field as fieldmod                              # noqa: PLC0415
    import worldseed                                      # noqa: PLC0415

    h = fieldmod.header(patch_path)
    # The requested seed, when one was requested, must be the field's seed.
    # `require_scope` raises; passing the field's own value as the expectation
    # when nothing was requested keeps a bare local solve working.
    worldseed.require_scope(
        "height field", patch_path, found_seed=h["seed"],
        seed_=worldseed.seed(default=h["seed"]),
        produced_by="blueprints/run_patchscan.sh for the seed being solved")
    if loc_path and Path(loc_path).exists():
        import poi as poimod                              # noqa: PLC0415
        worldseed.require_scope(
            "location dump", loc_path,
            found_seed=poimod.dump_seed(json.loads(Path(loc_path).read_text())),
            seed_=h["seed"],
            produced_by="Settlements' LocScan.cs on the seed being solved")
    # THE SOLVER KNOWS ITS SEED AND DOES NOT KNOW ITS WORLD NAME.  A solve is
    # a function of the seed; what the save ends up CALLED is an operator's
    # choice made later.  So the world is written only when it is actually
    # known -- `$VH_WORLD` was set, or this is the default pair, where the
    # answer has been Ulfsland since before this file existed -- and is null
    # otherwise.  `worldseed.require_scope` treats a null world as
    # seed-scoped-and-world-agnostic, which is exactly what this is; writing a
    # GUESS there would be the same false declaration the seed literal was.
    known_world = (os.environ.get(worldseed.ENV_WORLD)
                   or (worldseed.DEFAULT_WORLD
                       if h["seed"] == worldseed.DEFAULT_SEED else None))
    return {"seed": h["seed"], "seed_hash": h["seed_hash"],
            "world": known_world}


def parse_town_declaration(items) -> dict:
    """`--expect-towns stenvik=496,904` into the anchor shape `select_towns`
    reads, so the operator who solved a seed can pin a town for that solve --
    without editing this file, and without the pin being a constant that
    applies to every seed."""
    out: dict[str, dict] = {}
    for item in items or ():
        name, _, xz = str(item).partition("=")
        parts = [p for p in xz.replace(" ", "").split(",") if p]
        if not name or len(parts) != 2:
            raise ValueError(
                f"--expect-towns wants id=x,z and got {item!r}; a declaration "
                f"that cannot be parsed must not become 'no declaration', "
                f"because that is silently the permission to relocate")
        out[name] = {"xz": (float(parts[0]), float(parts[1])),
                     "why": f"DECLARED on the command line for this solve: the "
                            f"town centre was pinned at ({parts[0]}, {parts[1]}) "
                            f"and the district search returned a candidate there."}
    return out


def declared_town_centres(declared: dict | None = None) -> tuple[dict, str]:
    """The declaration in force for this solve, and WHO DECLARED IT: only an
    operator's `--expect-towns` (no seed carries a town declaration).

    The source string is carried because a refusal has to name the thing the
    operator must go and change; "the recorded centre" named nothing.
    """
    if declared:
        return declared, "--expect-towns on this run"
    return {}, ""


def _candidate_line(rank: int, s) -> str:
    n = s.notes
    return (f"#{rank + 1} ({s.x:.1f}, {s.z:.1f}) on {s.patch}: "
            f"{n.get('district_meadows_fraction')} Meadows over "
            f"{n.get('district_m')} m, {n.get('district_relief_m')} m district "
            f"relief, coast fraction {n.get('coast_fraction_400m')}, "
            f"{s.dist_spawn_m:.0f} m from spawn")


def _measured_why(rank: int, s, seed: str, town_id: str) -> str:
    """The justification for a town NOBODY DECLARED: what was measured on the
    seed actually being solved, plus the one sentence that makes the absence
    of a declaration actionable instead of invisible."""
    n = s.notes
    return (
        f"MEASURED on seed {seed}, and CHOSEN BY THE SEARCH rather than "
        f"declared: the #{rank + 1} town district candidate -- "
        f"{n.get('district_meadows_fraction')} Meadows over a "
        f"{n.get('district_m')} m district at {s.ground_y:.2f} m with "
        f"{n.get('district_relief_m')} m of district relief on patch "
        f"{s.patch}, coast fraction {n.get('coast_fraction_400m')} within "
        f"{2.2 * float(n.get('district_m') or 0):.0f} m, {s.dist_water_m:.1f} m "
        f"to the nearest wet cell, {s.dist_spawn_m:.0f} m from spawn. NO CENTRE "
        f"IS DECLARED for seed {seed}, so this one is the solver's and will "
        f"move if the search changes; pin it with "
        f"`--expect-towns {town_id}={s.x:.0f},{s.z:.0f}` and a move becomes a "
        f"refusal.")


def select_towns(candidates: list, seed: str, anchors: dict, source: str,
                 reach=None, windows: dict | None = None) -> list[dict]:
    """Which candidate each town in `TOWNS` is -- AND THE REFUSAL.

    A declared centre BINDS BY COORDINATE, not by rank: as long as the search
    still returns a candidate there the town does not move, whatever else the
    ranking did. A town with no declaration takes the best remaining candidate
    in the search's own order.

    Pure in its inputs -- a list of `sites.Site`, a seed name and a
    declaration -- so the state it refuses can be CONSTRUCTED in a fixture
    rather than borrowed from a 230 MB height field that only exists on hosts
    that have already built Ulfsland.
    """
    ranked = list(candidates)
    ids = [t["id"] for t in TOWNS]
    unknown = sorted(k for k in anchors if k not in ids)
    if unknown:
        raise ValueError(
            f"{source} declares a centre for {unknown}, which this plan has no "
            f"town for; it plans {ids}. A declaration nothing reads is not a "
            f"pin, it is a typo that reads as permission to place {ids} "
            f"anywhere the search likes")
    if len(ranked) < len(TOWNS):
        raise ValueError(
            f"seed {seed!r} yields {len(ranked)} town district candidate(s) and "
            f"this plan declares {len(TOWNS)} towns {ids}. "
            + (f"What the search returned: "
               + "; ".join(_candidate_line(i, s) for i, s in enumerate(ranked))
               if ranked else "The district search returned nothing at all.")
            + f" Either widen sites.Picker.town() for this seed -- its purity, "
              f"relief and coast thresholds are arguments -- or plan fewer "
              f"towns. A town planted where the district search rejected the "
              f"ground is not a measured site, and this plan emits only "
              f"measured sites.")
    taken: set[int] = set()
    bound: dict[str, tuple[int, dict]] = {}
    for t in TOWNS:
        a = anchors.get(t["id"])
        if a is None:
            continue
        want = (int(round(float(a["xz"][0]))), int(round(float(a["xz"][1]))))
        hit = next((i for i, s in enumerate(ranked)
                    if (int(round(s.x)), int(round(s.z))) == want), None)
        if hit is None:
            raise ValueError(
                f"the DECLARED centre for {t['id']} on seed {seed!r} is "
                f"({want[0]}, {want[1]}) -- declared by {source} -- and the "
                f"district search on this seed returns no candidate there. A "
                f"plan that silently relocates a town is worse than one that "
                f"stops: sites.yaml is the node set RoadNet and Crossings "
                f"build against, and the town may already be standing. What "
                f"the search DID return for seed {seed!r}, best first: "
                + "; ".join(_candidate_line(i, s) for i, s in enumerate(ranked))
                + f". Either the search regressed, in which case fix the "
                  f"search, or the declaration is stale for this seed, in "
                  f"which case re-declare it deliberately -- "
                  f"`--expect-towns {t['id']}=<x>,<z>` -- and deal with "
                  f"whatever is already built at ({want[0]}, {want[1]}).")
        bound[t["id"]] = (hit, a)
        taken.add(hit)
    spare = [i for i in range(len(ranked)) if i not in taken]
    windows = windows or {}
    picks: list[dict] = []
    for t in TOWNS:
        refused_by_road: list[dict] = []
        note = None
        if t["id"] in bound:
            rank, a = bound[t["id"]]
            why, anchored = a["why"], [ranked[rank].x, ranked[rank].z]
        else:
            # A TOWN'S WINDOW IS WHAT IT IS (the village is on the second
            # trunk landmass, because its legs hang from that island's
            # nodes), and a town no road reaches is no town: the best
            # remaining candidate of its window that a road reaches.
            want = windows.get(t.get("window"))
            mine = [i for i in spare
                    if want is None or ranked[i].patch == want]
            if t.get("window") and want is not None and not mine:
                note = (f"window {t['window']!r} ({want}) holds no town "
                        f"candidate that remains; bound among every trunk "
                        f"window")
                mine = list(spare)
            ok = ([i for i in mine if reach(ranked[i], t)["roaded"]]
                  if reach is not None else mine)
            rank = (ok or mine)[0]
            spare.remove(rank)
            if reach is not None:
                refused_by_road = [
                    {"search_rank": i, **reach(ranked[i], t)}
                    for i in mine
                    if i < rank and not reach(ranked[i], t)["roaded"]]
            why, anchored = _measured_why(rank, ranked[rank], seed, t["id"]), None
            if refused_by_road:
                why += (f" {len(refused_by_road)} candidate(s) ahead of it in "
                        f"the search were passed over because no road "
                        f"reaches them (road_refused).")
            if note:
                why += f" WINDOW FALLBACK: {note}."
        picks.append({"spec": t, "site": ranked[rank], "rank": rank,
                      "why": why, "declared_xz": anchored,
                      "road_refused": refused_by_road})
    return picks


# Which `Picker` search answers for each authored kind, AND WHICH WINDOWS IT
# COVERS.  Named one by one rather than by `getattr(picker, kind)`, because a
# typo'd kind must be a refusal here and not an AttributeError three frames
# down -- and the patch set is passed EXPLICITLY rather than left to the
# selector's default so that the plan can record what was actually searched.
#
# A POPULATION MUST DECLARE ITS OWN SCOPE, which is the same rule as an
# artefact declaring its own seed: MEASURED tonight in four separate artefacts
# (roads/structures.py::WRITTEN seeing 15 of 25 segments, placed_*.json holding
# no N-series at all, a carriageway column standing in for a batter set), the
# failure is never a wrong answer -- it is a right answer about a set nobody
# stated. An outlier that came up short must be readable as "searched THESE
# windows and found none" and never as "this seed has nowhere for a light".
#
# EVERY CANDIDATE THE SEARCH'S THRESHOLDS ADMIT, per window.  The searches take a
# `limit` -- how many candidates to stop at -- and a limit is the wrong place to
# decide how many a solve needs: which of them a road reaches, which the engine
# can level, and which another site has not claimed are all decided AFTER the
# search, so a limit that stops it early hides the reachable candidate that
# ranks 14th (MEASURED on Pirate68: the spawn window's watchtower search at
# limit 14 returns 10 candidates of which 5 are reachable; unbounded, 13 of
# which 6 are).  The solver picks how many it takes.
POOL_LIMIT = 400
KIND_SEARCH = {
    "castle": lambda p, patches: p.castle(patches=patches, limit=POOL_LIMIT),
    "watchtower": lambda p, patches: p.watchtower(patches=patches,
                                                  limit=POOL_LIMIT),
    "lighthouse": lambda p, patches: p.lighthouse(patches=patches,
                                                  limit=POOL_LIMIT),
    "treehouse": lambda p, patches: p.treehouse(patches=patches,
                                                limit=POOL_LIMIT),
}

# A castle stands on the highest ground there is; the rest stand on a trunk
# landmass, which is where the roads are.
KIND_PATCHES = {
    "castle": S.all_patches,
    "watchtower": S.trunk_patches,
    "lighthouse": S.trunk_patches,
    "treehouse": S.trunk_patches,
}


def declared_outlier_sites(declared: dict | None = None
                           ) -> tuple[dict, str]:
    """The outlying-site declaration in force for this solve, and WHO DECLARED
    IT: only an operator's `--expect-sites` (no seed carries one)."""
    if declared:
        return declared, "--expect-sites on this run"
    return {}, ""


def resolve_patch(fields: dict, name: str, what: str, source: str) -> str:
    """A DECLARED window name, resolved against the field that is loaded.

    `mainland` and `trunk-primary` are the same window under two names and
    `worldseed.aliases` already knows it, so a declaration written against one
    field's ids works on the other's.  A name that resolves to nothing is
    REFUSED here rather than raising `KeyError: 'mainland'` from an indexing
    expression 40 lines later -- which is exactly how this was found.
    """
    sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    hit = next((a for a in worldseed.aliases(name) if a in fields), None)
    if hit is None:
        raise ValueError(
            f"{what} is declared in patch window {name!r} by {source}, and "
            f"this field holds {sorted(fields)}. A window name is a reading of "
            f"ONE seed's scan request, not a landmass: re-declare this site "
            f"for the seed being solved, or drop it.")
    return hit


def _spawnable_check(name: str) -> dict:
    """The real emitter's verdict for one body.  Imported at call time so the
    library is only touched when a body actually has to be resolved."""
    import spawnable                                       # noqa: PLC0415
    return spawnable.check(name)


def resolve_body(rec: dict, seed: str, body_ok=None) -> tuple[str, dict, str]:
    """WHICH BODY AN UNDECLARED SITE GETS, measured by the thing that spawns it.

    `OUTLIERS` names one authored `body` and, optionally, ordered `alternates`
    -- a second body this project is willing to build there.  This tries them
    IN ORDER and keeps the first the emitter can actually spawn.

    ONLY EVER CALLED FOR A SITE THE SEARCH CHOSE.  A declared site keeps its
    declared body verbatim and refuses if it fails: `u-lhwest` is published in
    `sites.yaml` with its body's piece count and sha256 and may be STANDING, so
    substituting there would be a plan that stops describing the ground -- the
    same refusal as relocating a declared town, arrived at by machine instead
    of by hand.  Where the search chose the site, nothing is standing and the
    body is part of the placement decision this run is making.

    `body_ok` is injectable so a fixture can CONSTRUCT the verdicts instead of
    borrowing the blueprint library, and so nothing is memoised: a cached pass
    is a certification outliving the emission it was computed from.
    """
    check = body_ok or _spawnable_check
    tried: list[tuple[str, str]] = []
    for cand in (rec["body"], *rec.get("alternates", ())):
        report = check(cand)
        if report.get("ok"):
            note = ("the authored body" if cand == rec["body"] else
                    f"AUTHORED ALTERNATE: {rec['body']} refuses on this seed "
                    f"({tried[0][1]}), and {cand} is the next body this project "
                    f"declared it is willing to build here")
            return cand, report, note
        tried.append((cand, str(report.get("reason") or report.get("missing")),
                      report.get("refusals") or []))
    raise BodyRefused(
        rec["id"], tried,
        f"{rec['id']} ({rec['kind']}) on seed {seed!r}: none of the "
        f"{len(tried)} bodies this project declared for it can be spawned. "
        + "; ".join(f"{b}: {why}" for b, why, _r in tried)
        + ". Repair one of those blueprints at source, or declare another "
          "body in OUTLIERS -- a site whose every candidate the emitter "
          "refuses is not a site this plan can build, and an --allow-* flag "
          "would write the defect into the world.")


class BodyRefused(ValueError):
    """A SITE refused because no body it may be built with can be spawned.

    ONE SITE, NOT THE STAGE (BODY_REFUSAL_DEPENDS_ON `per_site`).  The
    refusal is the site's: `select_outliers` records it on the site
    (`refused`) and `build` moves the site to `plan["refused_sites"]` with
    every body tried and its measured reason, and the other sites solve.
    MEASURED 2026-09-27: Pirate68 stage 8 exited 1 on ONE body --
    `hs_plains_ulf_the_builder_lighthouse.blueprint`, lh-west's -- after
    solving all 16 sites."""

    def __init__(self, site: str, tried: list, message: str):
        super().__init__(message)
        self.site = site
        self.tried = [{"body": b, "reason": why, "refusals": r}
                      for b, why, r in tried]


# BODY_REFUSAL_DEPENDS_ON: what a body the emitter refuses takes down with it.
#   per_site  the site (or town building) that uses it, recorded with the
#             body's refusals and numbers; every other site still solves.
# Neuter `whole_stage`: the shipped gate, which raised for the whole plan.
BODY_REFUSAL_MODE = "per_site"


def _site_refusal(rec: dict, bodies: list[dict], why: str) -> dict:
    """One refused site, as `plan["refused_sites"]` carries it: the site, its
    kind, whether it was declared, every body tried with the emitter's own
    refusals and numbers, and the one-line reason."""
    return {"verdict": "REFUSED", "stage": "body", "site": rec["id"],
            "kind": rec["kind"], "tag": rec.get("tag"),
            "declared": bool(rec.get("centre_declared")),
            "bodies": bodies, "why": why}


def patch_containing(fields: dict, x: float, z: float, what: str,
                     source: str) -> str:
    """Which loaded window a declared point falls in, MEASURED.

    Trunk windows first, because Ulfsland's two trunk windows OVERLAP across a
    12 m channel and a point in the overlap belongs to the trunk that holds
    the roads. A point in no window is refused: a site outside every scan is a
    site whose ground has never been measured.
    """
    for pid in S.all_patches(fields):
        if fields[pid].contains(x, z):
            return pid
    raise ValueError(
        f"{what} is declared at ({x}, {z}) by {source} and that point is "
        f"inside none of this field's windows {sorted(fields)}; its ground has "
        f"never been scanned, so no pad can be priced there. Scan a window "
        f"that covers it (roads/field.py request) or re-declare the site.")


def _outlier_why(rank: int, s, seed: str, site_id: str, kind: str) -> str:
    return (
        f"MEASURED on seed {seed}, and CHOSEN BY THE SEARCH rather than "
        f"declared: the #{rank + 1} {kind} candidate -- {s.biome} at "
        f"{s.ground_y:.2f} m, {s.rise_m:.1f} m above the lowest ground in its "
        f"own search radius, {s.dist_water_m:.1f} m from the nearest wet cell, "
        f"{s.dist_spawn_m:.0f} m from spawn, on patch {s.patch}, nearest "
        f"location {s.nearest_location['prefab']} at "
        f"{s.nearest_location['dist_m']:.0f} m. NO CENTRE IS DECLARED for "
        f"{site_id} on seed {seed}, so this one is the solver's and will move "
        f"if the search changes.")


def _window_pid(fields: dict, which: str | None) -> str | None:
    """The loaded window that plays trunk role `which` (`primary` /
    `secondary`), by `worldseed.aliases` as `sites.trunk_patches` resolves
    it, or None when this field has no such window (or `which` is None)."""
    if which is None:
        return None
    sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    role = {"primary": worldseed.TRUNK_PRIMARY,
            "secondary": worldseed.TRUNK_SECONDARY}[which]
    return next((a for a in worldseed.aliases(role) if a in fields), None)


# WHAT A ROLE NEEDS OF THE SOLVE besides the candidate: the key of
# `context` that holds it.  A role whose context is absent is REFUSED, never
# quietly ranked by search order -- that is the binding this replaces.
ROLE_NEEDS = {"nearest_town": "town"}


def _roles_text(o: dict, scope_pid: str | None, among: int, s) -> str:
    words = ROLES[o["role"]][0]
    return (f" BOUND TO {o['id']} BY WHAT IT IS, NOT BY SEARCH RANK: "
            f"{words} of the {among} best {o['kind']} candidate(s) of "
            f"{('window ' + scope_pid) if scope_pid else 'its search'} that "
            f"remained when this name was placed.")


def select_outliers(picker: S.Picker, seed: str, anchors: dict, source: str,
                    body_ok=None, admit=None,
                    claims: SiteClaims | None = None,
                    reach=None, context: dict | None = None) -> list[dict]:
    """Every `OUTLIERS` record with its PLACE filled in -- declared or searched.

    A DECLARED site (an operator's `--expect-sites`) is taken at its declared
    coordinate in its declared window.  An UNDECLARED one -- every site of
    every seed, unless an operator says otherwise -- is the search's own
    top-ranked candidate of its kind on this seed's land.

    REFUSES rather than returning a short list. An empty candidate set is the
    failure this whole item is about: a parameterised selector reading a
    hardcoded window name returned `[]` and that was indistinguishable from a
    world with nowhere to put a lighthouse.

    `admit(rec, site)` is the ENGINE's verdict on a candidate: the pad the
    writer would level there, billed on the surface it levels.  The search
    FINDS on the finding field; a candidate the engine cannot level is
    refused and the next one of its kind is taken, and every refusal is
    carried on the record with its numbers (`engine_refused`).  A DECLARED
    site is never moved by this: the engine refusing it stops the plan.

    `claims` is the ground this plan has already given away -- every town
    building `layout.TownPlanner` placed -- and every site this call accepts
    is added to it, so no two sites of ANY kind claim one sample
    (SITE_CLAIM_DEPENDS_ON).  `admit(rec, claims)` slides the pad off
    claimed ground where it can; a candidate whose final pad still meets a
    claim is refused like an engine refusal and the next candidate of its
    kind is taken (`claim_refused`, with the numbers).  DECLARED sites are
    claimed FIRST, because they cannot move to another candidate and a
    searched one can; a declared site whose ground is claimed stops the plan.
    Absent, the claims are this call's own: the outliers against each other.

    `reach(rec, site)` is the ROAD's verdict on a candidate (`road_reach.py`):
    a site declared roaded (`road_priority` trunk / spur) takes the best
    candidate a road can reach, and the unreachable candidates it passed over
    are carried on the record with their numbers (`reach_refused`).  Only a
    kind none of whose candidates in scope is reachable puts the site on the
    best unreachable one, which publishes `roaded: false` -- the building is
    never deleted, and no road is promised to it.  Absent, every candidate
    is taken as reachable.

    `context` carries what a role needs besides the candidate: `town`, the
    first town's centre.  A name is bound to a candidate by its ROLE
    (`OUTLIERS[...]["role"]`, `ROLES`), not by search rank.
    """
    claims = SiteClaims() if claims is None else claims
    ids = [o["id"] for o in OUTLIERS]
    unknown = sorted(k for k in anchors if k not in ids)
    if unknown:
        raise ValueError(
            f"{source} declares a site for {unknown}, which this plan does not "
            f"build; it plans {ids}. A declaration nothing reads is not a pin.")
    need: dict[str, int] = {}
    for o in OUTLIERS:
        if o["id"] not in anchors:
            need[o["kind"]] = need.get(o["kind"], 0) + 1
    found: dict[str, list] = {}
    searched: dict[str, list] = {}
    context = dict(context or {})
    for kind, count in need.items():
        if kind not in KIND_SEARCH:
            raise ValueError(
                f"no site search is defined for kind {kind!r}; this plan knows "
                f"{sorted(KIND_SEARCH)}")
        patches = KIND_PATCHES[kind](picker.fields)
        searched[kind] = list(patches)
        # SEARCHED PER WINDOW, each window for everything its thresholds
        # admit: one pooled search spends its `limit` on the first window and
        # gives the second a handful, so a name that belongs on the second
        # trunk landmass would have nothing to choose from.
        pooled = [c for pid in patches
                  for c in KIND_SEARCH[kind](picker, (pid,))]
        cands = S.dedupe(picker, pooled)
        if len(cands) < count:
            raise ValueError(
                f"seed {seed!r} yields {len(cands)} {kind} candidate(s) and "
                f"this plan builds {count} of them "
                f"({[o['id'] for o in OUTLIERS if o['kind'] == kind]}) after "
                f"searching windows {list(patches)}. "
                + ("The search returned nothing at all on the windows it was "
                   "given. "
                   if not cands else
                   "What it returned: "
                   + "; ".join(f"#{i + 1} ({c.x:.1f}, {c.z:.1f}) on {c.patch}, "
                               f"{c.biome}, rise {c.rise_m:.1f} m"
                               for i, c in enumerate(cands)) + ". ")
                + f"Either widen sites.Picker.{kind}() for this seed -- its "
                  f"thresholds are arguments -- or build fewer of them. A site "
                  f"planted where its own search found nothing is not a "
                  f"measured site.")
        found[kind] = cands
    # THE CANDIDATES STILL OPEN, per kind, in search order; and each one's
    # search rank, which a name bound by role must still report.
    pending: dict[str, list] = {k: list(v) for k, v in found.items()}
    rank_of: dict[int, int] = {id(c): i for v in found.values()
                               for i, c in enumerate(v)}
    # HOW MANY NAMES STILL WAIT FOR A PLACE in each (kind, window): the width
    # of the best-candidate set a role chooses among, so the SET of places is
    # the search's own and only the names move.
    todo: dict[tuple, int] = {}
    for o in OUTLIERS:
        if o["id"] not in anchors:
            key = (o["kind"], o.get("window"))
            todo[key] = todo.get(key, 0) + 1
    verdicts: dict[tuple, dict] = {}

    def reaches(o: dict, c) -> dict:
        key = (id(c), o["road_priority"])
        if key not in verdicts:
            verdicts[key] = reach(o, c)
        return verdicts[key]
    placed: dict[str, dict] = {}
    # DECLARED FIRST (a stable sort keeps OUTLIERS order within each half):
    # a declared site cannot move to another candidate, so it claims its
    # ground before any site that can.
    for o in sorted(OUTLIERS, key=lambda o: o["id"] not in anchors):
        rec = dict(o)
        a = anchors.get(o["id"])
        if a is not None:
            rec["x"], rec["z"] = float(a["xz"][0]), float(a["xz"][1])
            rec["patch"] = (
                resolve_patch(picker.fields, a["patch"], f"site {o['id']!r}",
                              source) if a.get("patch")
                else patch_containing(picker.fields, rec["x"], rec["z"],
                                      f"site {o['id']!r}", source))
            rec["why"] = a["why"]
            rec["centre_declared"] = True
            # NULL MEANS "NOT LOOKED AT", NEVER "LOOKED AND FOUND NONE".  No
            # search was run for this site at all: it was taken where it was
            # declared.  `centre_declared` beside it is what says which kind
            # of null this is, and the two are written together for that
            # reason -- an empty reading and an absent reading are different
            # claims and only one of them is evidence.
            rec["search_rank"] = None
            rec["searched_windows"] = None
            # THE DECLARED BODY, VERBATIM.  No alternate is reached for here:
            # this site is published with this body's piece count and sha256
            # and may be standing.
            rec["body_why"] = "the authored body, kept because this site is declared"
            rec["body_report"] = None
            at, target = (rec["x"], rec["z"]), None
            if admit is not None:
                pad = admit(rec, claims)
                if pad.get("verdict") != "clear":
                    raise ValueError(
                        f"{o['id']} is DECLARED by {source} at "
                        f"({rec['x']}, {rec['z']}) and the engine cannot "
                        f"level it on ground no other site claims: "
                        f"{_engine_refusal(None, rec, pad)}. A declared site "
                        f"is not moved by the solver; re-declare it on ground "
                        f"the engine surface bills clear, or remove the "
                        f"declaration and let the search choose.")
                rec["pad"] = pad
                rec["engine_refused"] = []
                at, target = (pad["x"], pad["z"]), pad.get("target_y")
            mine = claims.outlier(rec, *at, target_y=target)
            hits = claims.conflicts(mine)
            if hits:
                raise ValueError(
                    f"{o['id']} is DECLARED by {source} at ({rec['x']}, "
                    f"{rec['z']}) and the ground its pad would write at "
                    f"({at[0]}, {at[1]}) is already claimed: "
                    f"{claim_brief(hits)}. A declared site is not moved by "
                    f"the solver; re-declare one of the two, or remove a "
                    f"declaration and let the search choose.")
            claims.add(mine)
            rec["claim_refused"] = []
        else:
            kind = o["kind"]
            refused: list[dict] = []
            claimed: list[dict] = []
            # THE BODY FIRST, because it is part of the ground the site claims
            # -- measured by the emitter that spawns it, and allowed here and
            # only here, because the search chooses this place and nothing is
            # standing on it.
            try:
                rec["body"], rec["body_report"], rec["body_why"] = \
                    resolve_body(o, seed, body_ok)
            except BodyRefused as exc:
                if BODY_REFUSAL_MODE != "per_site":
                    raise
                # THE SITE IS REFUSED, NOT THE STAGE: no candidate is
                # consumed and no ground is claimed for it.
                rec.update(refused=_site_refusal(rec, exc.tried, str(exc)),
                           centre_declared=False, search_rank=None,
                           searched_windows=searched[kind])
                placed[o["id"]] = rec
                todo[(kind, o.get("window"))] -= 1
                continue
            need_key = ROLE_NEEDS.get(o["role"])
            if need_key is not None and context.get(need_key) is None:
                raise ValueError(
                    f"{o['id']} is bound by role {o['role']!r}, which needs "
                    f"context[{need_key!r}], and this solve gave none. A name "
                    f"whose role cannot be read is not bound by search rank "
                    f"instead -- that is the defect roles replace.")
            role_key = ROLES[o["role"]][1]
            roaded = reach is not None and o["road_priority"] != "none"
            scope_pid = _window_pid(picker.fields, o.get("window"))
            window_note = None
            if o.get("window") and scope_pid is None:
                window_note = (f"window {o['window']!r} is not a window of "
                               f"this field; the name was bound among the "
                               f"candidates of every trunk window")
            while True:
                live = pending[kind]
                scope = [c for c in live
                         if scope_pid is None or c.patch == scope_pid]
                if o.get("window") and not scope:
                    window_note = (
                        f"window {o['window']!r} "
                        + (f"({scope_pid}) holds no {kind} candidate that "
                           f"remains" if scope_pid else
                           "is not a window of this field")
                        + "; the name was bound among the candidates of every "
                          "trunk window")
                    scope = list(live)
                if _ROLE_MODE == "rank":
                    scope = list(live)
                reachable = ([c for c in scope if reaches(o, c)["roaded"]]
                             if roaded else scope)
                pool = reachable or scope
                if not pool:
                    raise ValueError(
                        f"seed {seed!r}: every remaining {kind} candidate "
                        f"for {o['id']} was REFUSED -- {len(refused)} by the "
                        f"engine surface, {len(claimed)} on ground another "
                        f"site already claims -- after the candidates already "
                        f"taken: "
                        + "; ".join(str(r) for r in refused + claimed)
                        + f". Widen sites.Picker.{kind}() for this seed "
                          f"or build fewer; a site the engine cannot level, "
                          f"or that stands on another site's ground, is not "
                          f"a site.")
                among = pool[:max(todo[(kind, o.get("window"))], 1)]
                s = (among[0] if _ROLE_MODE == "rank" else
                     min(among, key=lambda c: (role_key(c, context),
                                               rank_of[id(c)])))
                rank = rank_of[id(s)]
                live.remove(s)
                rec["x"], rec["z"], rec["patch"] = s.x, s.z, s.patch
                at, target = (s.x, s.z), None
                if admit is not None:
                    pad = admit(rec, claims)
                    if pad.get("verdict") != "clear":
                        refused.append(_engine_refusal(rank, s, pad))
                        continue
                    at, target = (pad["x"], pad["z"]), pad.get("target_y")
                mine = claims.outlier(rec, *at, target_y=target)
                hits = claims.conflicts(mine)
                if not hits:
                    if admit is not None:
                        rec["pad"] = pad
                    break
                claimed.append(_claim_refusal(rank, s, at, hits))
            claims.add(mine)
            todo[(kind, o.get("window"))] -= 1
            rec["why"] = _outlier_why(rank, s, seed, o["id"], kind)
            rec["centre_declared"] = False
            rec["search_rank"] = rank
            # WHAT NAMED THIS SITE: its role, how many candidates it chose
            # among, and where.
            rec["bound_by"] = {
                "role": o["role"], "words": ROLES[o["role"]][0],
                "among": len(among), "window": scope_pid,
                "window_requested": o.get("window"),
                "window_fallback": window_note,
                "search_rank": rank,
                "role_value": (None if _ROLE_MODE == "rank" else round(
                    float(role_key(s, context)), 1))}
            if _ROLE_MODE != "rank":
                rec["why"] += _roles_text(o, scope_pid, len(among), s)
            if window_note:
                rec["why"] += f" WINDOW FALLBACK: {window_note}."
            # WHAT THE ROAD REFUSED ON THE WAY HERE, with its numbers: the
            # candidates ahead of this one in the search that no road reaches.
            if roaded:
                passed = [reaches(o, c) for c in scope
                          if rank_of[id(c)] < rank
                          and not reaches(o, c)["roaded"]]
                rec["road"] = reaches(o, s)
                rec["reach_refused"] = [
                    {"search_rank": rank_of[id(c)], **reaches(o, c)}
                    for c in sorted(
                        (c for c in scope if rank_of[id(c)] < rank
                         and not reaches(o, c)["roaded"]),
                        key=lambda c: rank_of[id(c)])][:12]
                rec["reach_refused_count"] = len(passed)
                if passed:
                    rec["why"] += (
                        f" {len(passed)} candidate(s) ahead of it in the "
                        f"search were passed over because no road reaches "
                        f"them (reach_refused).")
                if not rec["road"]["roaded"]:
                    rec["why"] += (
                        f" NO {kind} candidate in scope is reachable by a "
                        f"{o['road_priority']} road, so this site stands on "
                        f"the best unreachable one and is PORTAL-ONLY: "
                        f"{rec['road']['why']}.")
            # WHAT THE ENGINE REFUSED ON THE WAY HERE, with its numbers: a site
            # that is not its kind's top candidate must say why.
            if admit is not None:
                rec["engine_refused"] = refused
                if refused:
                    rec["why"] += (
                        f" Candidate(s) "
                        + ", ".join(f"#{r['search_rank'] + 1}" for r in refused)
                        + f" of this kind were REFUSED on the {SF.SITE_BILL_SURFACE}"
                          f" surface the engine levels (engine_refused), so the"
                          f" solver moved to #{rank + 1}.")
            # AND WHAT ANOTHER SITE'S GROUND REFUSED, the same way.
            rec["claim_refused"] = claimed
            if claimed:
                rec["why"] += (
                    f" Candidate(s) "
                    + ", ".join(f"#{r['search_rank'] + 1}" for r in claimed)
                    + f" of this kind were REFUSED because the ground they "
                      f"would write is already claimed by "
                    + ", ".join(sorted({a for r in claimed
                                        for a in r["claimed_by"]}))
                    + f" (claim_refused), so the solver moved to "
                      f"#{rank + 1}.")
            # AND WHAT THE BUILD'S CLEARING GATE REFUSED on the way here:
            # candidates with no legal offset left once the gate had refused
            # its share (CLEARING_GATE_DEPENDS_ON).  Offsets refused inside
            # the KEPT pad's box are on the pad (`clearing_refused_offsets`).
            gated = [r for r in rec.get("engine_refused") or ()
                     if r.get("clearing_bound_by")]
            if gated:
                rec["why"] += (
                    f" Candidate(s) "
                    + ", ".join(f"#{r['search_rank'] + 1}" for r in gated)
                    + " were left with no pad once the build's clearing gate "
                      "had REFUSED offsets whose cylinder reaches "
                    + ", ".join(sorted({k for r in gated
                                        for k in r["clearing_bound_by"]}))
                    + f" (engine_refused[].clearing_bound_by; first: "
                      f"{gated[0]['clearing_first_refusal']['why']}).")
            # WHAT WAS SEARCHED, carried on the record: a site that came up
            # short must be readable as "these windows, none found".
            rec["searched_windows"] = searched[o["kind"]]
        placed[o["id"]] = rec
    return [placed[o["id"]] for o in OUTLIERS]


def _engine_refusal(rank, where, pad: dict) -> dict:
    """One engine refusal, with the numbers it rests on."""
    get = (lambda k: getattr(where, k)) if not isinstance(where, dict) else where.get
    out = {"search_rank": rank, "xz": [get("x"), get("z")],
           "patch": get("patch"), "verdict": pad.get("verdict"),
           "surface": pad.get("surface"), "pad_xz": [pad.get("x"), pad.get("z")],
           "target_y": pad.get("target_y"), "samples": pad.get("samples"),
           "over_clamp": pad.get("over_clamp"),
           "max_fill_m": pad.get("max_fill_m"), "max_cut_m": pad.get("max_cut_m"),
           "unread_zones": pad.get("unread_zones"),
           "offsets_tried": pad.get("offsets_tried"),
           "claim_refused_offsets": pad.get("claim_refused_offsets"),
           "claimed_by": pad.get("claimed_by")}
    # THE BUILD'S CLEARING GATE, where it refused offsets of this box: the
    # location named and the first refusal's own numbers.
    for k in ("clearing_refused_offsets", "clearing_bound_by",
              "clearing_first_refusal"):
        if pad.get(k):
            out[k] = pad[k]
    return out


def _claim_refusal(rank, where, at, hits: list[dict]) -> dict:
    """One candidate refused on claimed ground, with the numbers."""
    return {"search_rank": rank, "xz": [where.x, where.z],
            "patch": where.patch, "verdict": "claimed",
            "pad_xz": [at[0], at[1]],
            "claimed_by": sorted({str(h.get("against")) for h in hits}),
            "shared_samples": sum(h.get("shared_samples") or 0 for h in hits),
            "conflicts": [{k: h.get(k) for k in (
                "unit", "against", "against_unit", "shared_samples",
                "their_stamp_to_my_body_m", "my_stamp_to_their_body_m",
                "centre_m")} for h in hits[:6]]}


def parse_site_declaration(items) -> dict:
    """`--expect-sites wt-spawn=110,-104[,mainland]` into anchor shape.

    The window is OPTIONAL and is measured from the coordinate when it is
    omitted -- `select_outliers` fills it from whichever loaded window
    contains the point -- because which scan window a point falls in is a fact
    about the field, not a decision for the operator.
    """
    out: dict[str, dict] = {}
    for item in items or ():
        name, _, rest = str(item).partition("=")
        parts = [p for p in rest.replace(" ", "").split(",") if p]
        if not name or len(parts) not in (2, 3):
            raise ValueError(
                f"--expect-sites wants id=x,z or id=x,z,patch and got {item!r}; "
                f"a declaration that cannot be parsed must not become 'no "
                f"declaration', because that is silently permission to move it")
        out[name] = {"xz": (float(parts[0]), float(parts[1])),
                     "patch": parts[2] if len(parts) == 3 else None,
                     "why": f"DECLARED on the command line for this solve: "
                            f"({parts[0]}, {parts[1]})."}
    return out


def declared_harbours(picker: S.Picker, seed: str, claims: "SiteClaims",
                      islands: dict) -> list[dict]:
    """THE SITES A PERSON DECLARED (`worldseed.HUMAN_SITES`), MEASURED.

    Each request is sited by the module that owns its kind
    (`settlements/harbour_site.py`: the real inlet and the real landing on
    THIS seed's field, judged against the same generator locations and
    recorded installations every solver site is) and its district is claimed
    FIRST -- before any town building or searched outlier -- so no solver site
    is planted inside it.  Nothing is moved silently: a request the field
    cannot honour raises with the numbers, and so does a district that meets
    another declared district.  A seed with no declaration returns [].
    """
    sys.path.insert(0, str(HERE.parent))
    import worldseed                                        # noqa: PLC0415

    def clear(x: float, z: float, half_diag_m: float) -> bool:
        return (picker.installation_conflict(x, z, half_diag_m) is None
                and picker.loc.clear(x, z, half_diag_m, protected_only=False))

    out: list[dict] = []
    for sid, rec in worldseed.human_sites(seed).items():
        m = HS.measure(picker.fields, picker.loc.spawn, sid, rec, clear=clear)
        m["island"] = site_island(islands, picker, m["patch"], *m["xz"])
        claim = SiteClaims.claim(sid, m["kind"], [HS.claim_unit(m)], m["pad_y"])
        hits = claims.conflicts(claim)
        if hits:
            raise ValueError(
                f"declared site {sid!r}: its {m['district_m']:g} m district "
                f"meets another declared site's -- {claim_brief(hits)}. Two "
                f"human declarations may not claim one piece of ground; "
                f"re-declare one of them.")
        claims.add(claim)
        out.append(m)
    return out


def build(picker: S.Picker, run_interior: bool = True,
          expect_towns: dict | None = None,
          expect_sites: dict | None = None,
          surface: SF.EngineSurface | None = None,
          evidence: "CensusEvidence | None" = None) -> dict:
    plan: dict = {**scope_of(picker.patch_path, picker.loc_path),
                  "patch_file": picker.patch_path,
                  "locations_dump": picker.loc_path,
                  "towns": {}, "outliers": [], "bodies": {}, "totals": {}}
    seed = plan["seed"]
    # NO ENGINE SURFACE, NO PLAN.  Every pad this document records is a WRITE,
    # and a write billed on the finding field is the bill that called
    # sudrberg-keep clean at 17.403 m of fill.  There is no fallback.
    if surface is None or surface.surface != SF.SITE_BILL_SURFACE:
        raise ValueError(
            f"settlements/plan.py::build needs the {SF.SITE_BILL_SURFACE!r} "
            f"engine surface to bill pads on (got "
            f"{getattr(surface, 'surface', None)!r}); an unread surface is "
            f"not a clean bill")
    if surface.seed != seed:
        raise ValueError(f"the engine surface is seed {surface.seed!r} and "
                         f"the field this plan is solved on is {seed!r}")
    apron = _writer().APRON_M
    rows = BD.load_audit()
    by_name = {r["name"]: r for r in rows}
    # ONE REGISTRY OF GROUND FOR THE WHOLE SOLVE: the towns claim first, then
    # the declared outliers, then the searched ones (SITE_CLAIM_DEPENDS_ON).
    claims = SiteClaims(footprint_of=lambda name: (
        (float(by_name[name]["footprint_x_m"]),
         float(by_name[name]["footprint_z_m"])) if name in by_name else None))
    planner = LY.TownPlanner(
        picker, audit_rows=rows, bill=engine_pad_bill(surface, apron),
        claims=claims,
        clearing=lambda town, patch, stub: clearing_refusals(
            picker.loc, town, building_units(town, patch, stub)))
    town_patches = S.trunk_patches(picker.fields)
    islands = island_membership(picker, town_patches)
    # THE ROAD'S OWN READING OF EVERY SITE (road_reach.py): the router's graph
    # over this seed's windows, so a site no road can reach is portal-only
    # with numbers instead of an edge nothing routes.
    site_reach = RR.SiteReach(picker.fields, picker.loc_path)
    ocean = {pid: S.ocean_distance(picker.fields[pid]) for pid in picker.fields}

    # THE HUMAN-DECLARED SITES CLAIM THEIR GROUND FIRST (and only when a seed
    # declares any: a seed without one keeps a byte-identical plan).
    declared = declared_harbours(picker, seed, claims, islands)
    if declared:
        plan["declared_sites"] = declared

    anchors, anchor_source = declared_town_centres(expect_towns)
    town_sites = S.dedupe(picker, picker.town(patches=town_patches))
    picks = select_towns(
        town_sites, seed, anchors, anchor_source,
        reach=lambda s, spec: site_reach.verdict(
            s.patch, s.x, s.z, spec["road_priority"]),
        windows={w: _window_pid(picker.fields, w)
                 for w in ("primary", "secondary")})
    plan["town_choice"] = {
        "seed": seed,
        "declared_by": anchor_source or None,
        "declared": {k: [float(v["xz"][0]), float(v["xz"][1])]
                     for k, v in anchors.items()},
        "candidates": [{"rank": i, "xz": [s.x, s.z], "patch": s.patch,
                        "notes": s.notes} for i, s in enumerate(town_sites)],
        "chosen": {p["spec"]["id"]: {"xz": [p["site"].x, p["site"].z],
                                     "search_rank": p["rank"],
                                     "declared": p["declared_xz"] is not None}
                   for p in picks},
        "patches_searched": list(town_patches),
    }
    for pick in picks:
        spec, site = pick["spec"], pick["site"]
        town = planner.plan(site, spec["id"], spec["spec"])
        f = picker.fields[site.patch]
        iz, ix = f.idx(site.x, site.z)
        town["why"] = pick["why"]
        town["tag"] = spec["tag"]
        town["type"] = spec["type"]
        town["road_priority"] = spec["road_priority"]
        town["gate"] = spec["gate"]
        town["search_rank"] = pick["rank"]
        town["centre_declared"] = pick["declared_xz"] is not None
        town["dist_wet_cell_m"] = round(float(picker.water(site.patch)[iz, ix]), 1)
        town["dist_navigable_water_m"] = round(float(ocean[site.patch][iz, ix]), 1)
        town["dist_spawn_m"] = site.dist_spawn_m
        town["island"] = site_island(islands, picker, site.patch, site.x, site.z)
        town["road"] = site_reach.verdict(
            site.patch, town["centre"][0], town["centre"][1],
            spec["road_priority"])
        town["road_refused"] = pick.get("road_refused", [])
        if not town["road"]["roaded"]:
            raise ValueError(
                f"town {spec['id']!r} is declared {spec['road_priority']} and "
                f"no road reaches it on seed {seed!r}: {town['road']['why']}. "
                f"A town is the trunk network's own node; a plan that "
                f"publishes it as portal-only has no town. Widen "
                f"sites.Picker.town() so a reachable district is a candidate, "
                f"or declare the centre with --expect-towns.")
        town["overlaps"] = LY.check_overlaps(town)
        town["site_notes"] = site.notes
        plan["towns"][spec["id"]] = town

    site_anchors, site_source = declared_outlier_sites(expect_sites)
    first_town = next(iter(plan["towns"].values()))
    placed = select_outliers(
        picker, seed, site_anchors, site_source,
        admit=lambda rec, claims: engine_outlier_pad(picker, surface, rec,
                                                     apron, claims=claims,
                                                     evidence=evidence),
        claims=claims,
        reach=lambda rec, s: site_reach.verdict(
            s.patch, s.x, s.z, rec["road_priority"]),
        context={"town": (first_town["centre"][0], first_town["centre"][1])})
    # A SITE REFUSED FOR ITS BODY is carried, not solved, and not the stage's
    # end (BODY_REFUSAL_DEPENDS_ON): `plan["refused_sites"]`, with every body
    # tried and the emitter's own numbers.
    plan["refused_sites"] = [o["refused"] for o in placed if o.get("refused")]
    placed = [o for o in placed if not o.get("refused")]
    plan["outlier_choice"] = {
        "declared_by": site_source or None,
        "declared": sorted(site_anchors),
        "chosen": {o["id"]: {"xz": [o["x"], o["z"]], "patch": o["patch"],
                             "search_rank": o["search_rank"],
                             "declared": o["centre_declared"],
                             "engine_refused": len(o.get("engine_refused")
                                                   or ()),
                             "claim_refused": len(o.get("claim_refused")
                                                  or ()),
                             "claim_refused_offsets": (o.get("pad") or {}).get(
                                 "claim_refused_offsets")}
                   for o in placed},
        "site_claim_depends_on": list(SITE_CLAIM_DEPENDS_ON),
        "claims_registered": len(claims.claims),
        # THE SET THAT WAS SEARCHED, per kind, for the sites nobody declared.
        # A population that does not declare its own scope is how "15 of 25"
        # and "no N-series at all" both survived: the answer was right about a
        # set nobody had stated.
        "searched_windows": {o["kind"]: o["searched_windows"] for o in placed
                             if o["searched_windows"]},
    }
    for o in placed:
        f = picker.fields[o["patch"]]
        row = planner.by_name.get(o["body"])
        if row is None:
            raise ValueError(f"{o['body']} is not in the library audit")
        if not BD.fits(row, o["pad_m"]):
            raise ValueError(
                f"{o['id']}: body {o['body']} spans {BD.span(row):.1f} m and does "
                f"not fit a {o['pad_m']} m pad with a {BD.PAD_MARGIN_M} m margin")
        # THE ENGINE'S PAD: slid, judged and billed by `engine_outlier_pad`
        # on the surface the writer levels, over the pads it writes.
        pad = o["pad"]
        iz, ix = f.idx(pad["x"], pad["z"])
        rec = dict(o)
        rec.update({
            "x": round(pad["x"], 1), "z": round(pad["z"], 1), "pad": pad,
            "body_footprint": [row["footprint_x_m"], row["footprint_z_m"]],
            "body_sha256": row["sha256"], "pieces": int(row["pieces"]),
            "grounded": row["flat_pad_verdict"],
            "floating_fraction": float(row["floating_fraction"]),
            "missing_prefabs": row["missing_prefabs"],
            "stations": int(row["stations"]), "beds": int(row["beds"]),
            "walkable_levels": int(row["walkable_levels"]),
            "base_y": float(row["base_y"]),
            "ground_y": round(float(f.h[iz, ix]), 2),
            "rise_160m_m": round(block_rise(picker, o["patch"], pad["x"], pad["z"]), 1),
            "dist_wet_cell_m": round(float(picker.water(o["patch"])[iz, ix]), 1),
            "dist_navigable_water_m": round(float(ocean[o["patch"]][iz, ix]), 1),
            "dist_spawn_m": round(math.hypot(pad["x"] - picker.loc.spawn[0],
                                             pad["z"] - picker.loc.spawn[1]), 1),
            "nearest_location": picker.loc.nearest(pad["x"], pad["z"]),
            "poi_violations": picker.loc.violations(
                pad["x"], pad["z"], math.hypot(o["pad_m"], o["pad_m"]) / 2),
            "island": site_island(islands, picker, o["patch"], pad["x"], pad["z"]),
            # THE ROAD'S VERDICT AT THE PLACE THE NODE WILL STAND: the pad's
            # slid centre, not the candidate the search proposed.
            "road": site_reach.verdict(o["patch"], pad["x"], pad["z"],
                                       o["road_priority"]),
            "cluster": o.get("cluster", 1),
        })
        plan["outliers"].append(rec)

    # --- THE BILL IS THE WRITE, CERTIFIED BY THE WRITER ------------------
    plan["site_bill"] = certify_plan(plan, surface)
    if plan["site_bill"]["refused"]:
        raise ValueError(
            f"{len(plan['site_bill']['refused'])} unit(s) of this plan are "
            f"not billed clean by terraform/flatten.py::build on the "
            f"{surface.surface} surface:\n  "
            + "\n  ".join(plan["site_bill"]["refused"]))

    # --- the NPC gate, measured per body actually used -------------------
    # SCOPE, because a gate applied to the wrong set is the same defect as a
    # check measured at the wrong baseline. The operator's remark was about a
    # TOWN that might later host villagers, so the gate is HARD for a DWELLING
    # and informational everywhere else: a watchtower, a lighthouse and a
    # beehive are not places a villager lives, and refusing them for having no
    # enclosed floor would refuse the operator's own brief.
    dwelling_roles = {"hall", "house", "cottage", "longhouse", "manor",
                      "quickhouse", "stonehouse", "hut", "workshop"}
    used: dict[str, int] = {}
    dwelling_bodies: set[str] = set()
    for town in plan["towns"].values():
        for b in town["buildings"]:
            used[b["body"]] = used.get(b["body"], 0) + 1
            if b["role"] in dwelling_roles:
                dwelling_bodies.add(b["body"])
    for rec in plan["outliers"]:
        used[rec["body"]] = used.get(rec["body"], 0) + rec["cluster"]
    for name, count in sorted(used.items()):
        row = planner.by_name[name]
        entry = {
            "count": count, "sha256": row["sha256"], "pieces": int(row["pieces"]),
            "footprint": [row["footprint_x_m"], row["footprint_z_m"]],
            "grounded": row["flat_pad_verdict"],
            "floating_fraction": float(row["floating_fraction"]),
            "missing_prefabs": row["missing_prefabs"],
            "base_y": float(row["base_y"]), "relief_m": float(row["relief_m"]),
            "max_buried_m": float(row["max_buried_m"]),
            "stations": int(row["stations"]), "beds": int(row["beds"]),
            "walkable_levels": int(row["walkable_levels"]),
            "is_town_dwelling": name in dwelling_bodies,
        }
        if run_interior:
            try:
                entry["interior"] = BD.interior_report(name)
            except Exception as e:
                entry["interior"] = {"error": f"{type(e).__name__}: {e}",
                                     "npc_ok": None}
            entry["npc_gate"] = (
                "PASS" if (not entry["is_town_dwelling"]
                           or entry["interior"].get("npc_ok")) else "FAIL")
            entry["npc_gate_scope"] = (
                "hard gate: this body houses a town dwelling"
                if entry["is_town_dwelling"] else
                "informational: not a dwelling (tower, light, production or "
                "outlying structure), so an unenclosed interior is not a defect")
        plan["bodies"][name] = entry
    failed = [n for n, e in plan["bodies"].items() if e.get("npc_gate") == "FAIL"]
    if failed:
        raise ValueError(
            f"these town dwellings have under {BD.MIN_INDOOR_M2:g} m2 of covered, "
            f"enclosed, reachable floor and a villager could never stand in them: "
            f"{failed}. Replace them in the town spec rather than lowering the gate.")

    # --- the spawnability gate -------------------------------------------
    # Asked of the thing that does the spawning, not of the audit. MEASURED,
    # they disagree: `base_audit.json` reports `missing_prefabs: []` for
    # `hs_blackforest_crimsonchaostownhall.blueprint` while `to_rcon_plan.py`
    # refuses it on `Placeable_HardRock: not in the evidence file`. Four of the
    # eighteen bodies first chosen here failed this way.
    #
    # AND THIS GATE THEN COMMITTED THE SAME DEFECT AGAINST ITSELF. It built
    # its rejection list from `rep["missing"]` and named `--drop-prefab`.
    # MEASURED over these 18 bodies: 15 refused, and THIRTEEN OF THEM HAD
    # `missing == []` -- their refusal was a GEOMETRY PREFLIGHT (inverted
    # panels, a storey standing clear of its own plinth, rows buried under
    # the pad), for which `--drop-prefab` does nothing at all. So the gate
    # printed an EMPTY LIST as the evidence for a refusal it had not
    # measured, and sent a builder to a flag that could not have helped.
    # A REFUSAL WHOSE STATED REASON IS NOT ITS MEASURED REASON is worse than
    # one that names none, because it is actionable and wrong.
    #
    # `spawnable.reason` is now the single rendering of that verdict, and the
    # emitter is run WITH the three deterministic repairs it names itself --
    # asking whether a body can be built while withholding the flags the
    # builder would use is asking about a configuration nobody runs.
    # ONE MEASUREMENT, TWO CONSUMERS.  `resolve_body` has already run the real
    # emitter for every body it resolved, so re-running it here emits the same
    # blueprint a second time to learn the same thing.  The report is carried
    # forward instead -- but a verdict handed forward is a CERTIFICATION
    # OUTLIVING THE EMISSION IT WAS COMPUTED FROM, so it is reusable only for
    # the exact question this gate is asking, and three things are checked
    # before it is:
    #
    #   ABSENT IS NOT PASSED. No report means this gate RUNS the emitter. It
    #     never means the body is fine -- that is `missing: []` again.
    #   THE REPORT MUST ANSWER THIS QUESTION. Same body name, same repair set.
    #     A report taken under a different `--upright-panels/...` set answers a
    #     different question and is discarded rather than reinterpreted.
    #   THE ARTEFACT SAYS WHICH. `verdict_from` is `selection` or `gate`, so a
    #     reused verdict and a fresh one are never byte-identical in
    #     plan.json and nobody has to guess which bodies this gate emitted.
    body_gate(plan)

    pieces = sum(e["pieces"] * e["count"] for e in plan["bodies"].values())
    earth = (sum(t["totals"]["earthwork_m3"] for t in plan["towns"].values())
             + sum(r["pad"]["moved_m3"] * r["cluster"] for r in plan["outliers"]))
    portals = len(plan["towns"]) + len(plan["outliers"])
    plan["totals"] = {
        "sites": len(plan["towns"]) + len(plan["outliers"]),
        "buildings": sum(t["totals"]["buildings"] for t in plan["towns"].values())
        + sum(r["cluster"] for r in plan["outliers"]),
        # WHAT WAS ASKED FOR, beside what stands in the plan.  `buildings` is
        # a post-drop count: `layout.TownPlanner.plan` drops a building that
        # finds no frontage slot, and until 2026-09-22 the only witness was a
        # `!` line on stdout.  MEASURED: Fresh42 asks 31, places 29; the
        # default path asks 32, places 31 (vestvik-villa-1).
        "buildings_requested": sum(
            t["totals"].get("buildings_requested", t["totals"]["buildings"])
            for t in plan["towns"].values()) + sum(
            r["cluster"] for r in plan["outliers"]),
        "buildings_unplaced": sum(
            t["totals"].get("buildings_unplaced", 0)
            for t in plan["towns"].values()),
        "unplaced": [u for t in plan["towns"].values()
                     for u in t.get("unplaced", ())],
        "pieces_total": pieces,
        "portals": portals,
        "signs": portals,
        "zdo_estimate": pieces + 2 * portals,
        "earthwork_m3": round(earth, 1),
        "beds": sum(t["totals"]["beds"] for t in plan["towns"].values())
        + sum(r["beds"] * r["cluster"] for r in plan["outliers"]),
        "stations": sum(t["totals"]["stations"] for t in plan["towns"].values())
        + sum(r["stations"] * r["cluster"] for r in plan["outliers"]),
        "all_clamp_ok": (all(t["totals"]["all_clamp_ok"] for t in plan["towns"].values())
                         and all(r["pad"]["clamp_ok"] for r in plan["outliers"])),
        "bodies_distinct": len(plan["bodies"]),
        "bodies_with_missing_prefabs": sum(
            1 for e in plan["bodies"].values() if e["missing_prefabs"]),
        "bodies_not_grounding": sum(
            1 for e in plan["bodies"].values() if e["grounded"] not in BD.GROUNDS),
    }
    if declared:
        plan["totals"]["declared_sites"] = len(declared)
    return plan


def refuse_bodies(plan: dict, refused: dict[str, dict]) -> None:
    """Take every user of a refused body out of the plan, with its numbers.

    An outlying site goes to `plan["refused_sites"]` (declared or searched,
    its body unchanged); a town building to its town's `unplaced`, and the
    town's totals are re-taken over what stands; the body leaves
    `plan["bodies"]` for `plan["bodies_refused"]`, which names its users.
    Each refusal is printed, so the stage's report carries it too."""
    if not refused:
        return
    import types                                            # noqa: PLC0415
    plan.setdefault("refused_sites", [])
    gone_bodies = plan.setdefault("bodies_refused", {})
    for name, rep in refused.items():
        why = f"the emitter refuses its body {name}: {rep['reason']}"
        users = []
        keep = []
        for r in plan["outliers"]:
            if r["body"] != name:
                keep.append(r)
                continue
            plan["refused_sites"].append(_site_refusal(
                r, [{"body": name, "reason": rep["reason"],
                     "refusals": rep["refusals"]}], f"{r['id']}: {why}"))
            users.append(r["id"])
        plan["outliers"] = keep
        for t in plan["towns"].values():
            hit = [b for b in t["buildings"] if b["body"] == name]
            if not hit:
                continue
            t["buildings"] = [b for b in t["buildings"] if b["body"] != name]
            for b in hit:
                t.setdefault("unplaced", []).append(
                    {"id": b["id"], "role": b["role"], "body": name,
                     "slots_tried": 0, "claim_refused_slots": 0,
                     "claimed_by": {}, "body_refused": rep["refusals"],
                     "why": why})
                users.append(b["id"])
            t["totals"] = LY.TownPlanner.totals(
                [types.SimpleNamespace(**b) for b in t["buildings"]],
                t["square"], t["unplaced"])
        entry = plan["bodies"].pop(name)
        gone_bodies[name] = {**entry["spawnable"], "used_by": users}
        print(f"  ! REFUSED {users}: {why}", file=sys.stderr)


def body_gate(plan: dict, check=None) -> None:
    """THE SPAWNABILITY GATE over every body the plan uses: each body's
    emitter verdict is recorded on `plan["bodies"]`, and a body the emitter
    refuses takes its USERS with it (`refuse_bodies`), never the stage
    (BODY_REFUSAL_DEPENDS_ON).  `check(name)` is `spawnable.check` unless a
    fixture constructs the verdicts."""
    import spawnable
    check = check or spawnable.check
    from_selection = {}
    for r in plan["outliers"]:
        rep = r.get("body_report")
        if (rep and rep.get("name") == r["body"]
                and list(rep.get("repairs") or ()) == list(spawnable.REPAIRS)):
            from_selection[r["body"]] = rep
    unspawnable = []
    refused_bodies: dict[str, dict] = {}
    for name in plan["bodies"]:
        rep = from_selection.get(name)
        source = "selection" if rep else "gate"
        if rep is None:
            rep = check(name)
        plan["bodies"][name]["spawnable"] = {
            "ok": rep["ok"], "commands": rep["commands"],
            "missing": rep["missing"],
            # The refusal's OWN code, numbers and override flag, and what the
            # repairs moved to get here -- so a body that passes records HOW
            # and a body that refuses records what survived a repair.
            "refusals": rep["refusals"],
            "repaired": rep["repaired"],
            "repairs": rep["repairs"],
            "resolved": rep["resolved"],
            "reason": rep["reason"],
            # WHICH RUN OF THE EMITTER THIS VERDICT CAME FROM. `selection`
            # means `resolve_body` emitted it while choosing the body and this
            # gate reused that report; `gate` means this gate emitted it. The
            # two are the same measurement on the same inputs, and saying so
            # is cheaper than leaving a reader to assume it.
            "verdict_from": source,
            "method": "MEASURED: a real run of blueprints/to_rcon_plan.py "
                      "--align floor-center " + " ".join(rep["repairs"])
                      + f", which is the emitter that puts the body in the "
                        f"world; emitted at body {source}",
            "tool": "tools/jumpstart/settlements/spawnable.py",
        }
        if not rep["ok"]:
            unspawnable.append(f"{name}: {rep['reason']}")
            refused_bodies[name] = rep
    if unspawnable and BODY_REFUSAL_MODE != "per_site":
        raise ValueError(
            "these bodies cannot be spawned, each with the reason the emitter "
            "MEASURED and the remedy that reason has:\n  "
            + "\n  ".join(unspawnable)
            + "\nReplace them in the spec, or repair the blueprint at source. "
              "A refusal above that names an --allow-* flag is a defect in "
              "the FILE and the flag would write it into the world.")
    # ONE BODY'S REFUSAL IS ITS USERS', NOT THE STAGE'S
    # (BODY_REFUSAL_DEPENDS_ON): every site built with it -- DECLARED ones
    # included, which keep their declared body and are refused with it --
    # and every town building, recorded with the emitter's own refusals.
    refuse_bodies(plan, refused_bodies)
    placement_gate(plan, check)


# PLACEMENT_GATE_DEPENDS_ON: the yaw a town building is judged at.
#   per_placement  the emitter is asked once per distinct (body, yaw) the
#                  plan places, at that yaw, and a building whose placement
#                  it refuses is unplaced with the refusal's numbers.
# Neuter `body_only`: the shipped gate, which asked every body once, at
# yaw 0.  MEASURED on Ulfsland: `hs_blackforest_kin_01_woodhouse` and
# `hs_blackforest_tester_meadows_log_cabin` both emit CLEAN at yaw 0 and
# carry a roof hole at the yaw their street turns them to (80 and 70), which
# the acceptance gate found in the built world.
PLACEMENT_GATE_MODE = "per_placement"


def placement_gate(plan: dict, check=None) -> None:
    """THE SAME EMITTER, ASKED AT EACH TOWN BUILDING'S OWN YAW.

    `body_gate` asks `spawnable.check(name)` once per body, and that emits at
    yaw 0.  One of the emitter's questions - `roof_skin_gap`, the gate's
    `structures/roof_skin` - is a property of the file AT A YAW (the gate's
    lattice has the world's axes: `blueprints/preflight.py::check_skin`), so
    a body that passes at yaw 0 can carry a hole at the yaw the town's street
    turns it to, and nothing before the built world would say so.  A town
    building's yaw is decided in this plan, so it is asked here: once per
    distinct (body, yaw), memoised, because each is a real emission.

    A refused placement unplaces ITS BUILDINGS only - the body's users at
    other yaws stand - with the emitter's own refusal on the record
    (`unplaced[].body_refused`, `plan["placements_refused"]`), exactly as
    `refuse_bodies` does for a whole body.  Nothing is forced and no
    `--allow-*` flag is passed.  An outlying site is placed at yaw 0, which
    `body_gate` has already asked."""
    if PLACEMENT_GATE_MODE != "per_placement":
        return
    import types                                            # noqa: PLC0415
    if check is None:
        import spawnable                                    # noqa: PLC0415
        check = spawnable.check
    verdicts: dict[tuple[str, float], dict] = {}
    for t in plan["towns"].values():
        for b in t["buildings"]:
            yaw = float(b.get("yaw_deg") or 0.0)
            if yaw and (b["body"], yaw) not in verdicts:
                rep = check(b["body"], yaw=yaw)
                verdicts[(b["body"], yaw)] = rep
                plan["bodies"][b["body"]].setdefault(
                    "spawnable_at_yaw", {})[f"{yaw:g}"] = {
                        "ok": rep["ok"], "commands": rep["commands"],
                        "refusals": rep["refusals"], "reason": rep["reason"]}
    refused = {k: r for k, r in verdicts.items() if not r["ok"]}
    if not refused:
        return
    plan.setdefault("placements_refused", [])
    for (name, yaw), rep in sorted(refused.items()):
        why = (f"the emitter refuses its body {name} at yaw {yaw:g}: "
               f"{rep['reason']}")
        users = []
        for t in plan["towns"].values():
            hit = [b for b in t["buildings"]
                   if b["body"] == name
                   and float(b.get("yaw_deg") or 0.0) == yaw]
            if not hit:
                continue
            t["buildings"] = [b for b in t["buildings"] if b not in hit]
            for b in hit:
                t.setdefault("unplaced", []).append(
                    {"id": b["id"], "role": b["role"], "body": name,
                     "yaw_deg": yaw, "slots_tried": 0,
                     "claim_refused_slots": 0, "claimed_by": {},
                     "body_refused": rep["refusals"], "why": why})
                users.append(b["id"])
            t["totals"] = LY.TownPlanner.totals(
                [types.SimpleNamespace(**b) for b in t["buildings"]],
                t["square"], t["unplaced"])
        entry = plan["bodies"][name]
        entry["count"] -= len(users)
        plan["placements_refused"].append(
            {"body": name, "yaw_deg": yaw, "reason": rep["reason"],
             "refusals": rep["refusals"], "used_by": users})
        if entry["count"] <= 0:
            plan["bodies"].pop(name)
        print(f"  ! REFUSED {users}: {why}", file=sys.stderr)


# --- SELFTEST: THE SITE BILL, BOTH WAYS, ON GROUND BUILT IN MEMORY ----------
# No field file, no scan, no /tmp: each fixture CONSTRUCTS the state it tests
# from the mechanism the IL states.  The finding field is each point's OWN
# biome's height (`WorldGenerator.GetHeight`); the engine surface is
# `HeightmapBuilder::Build`'s SmoothStep blend of the zone's FOUR CORNER
# biomes' heights (PatchScan.cs `HeightmapHeight`).  The real numbers each
# fixture stands for are in its `prior`.
_FIXTURE_BIOMES = {"Mountain": T.BIOME_CODE["Mountain"],
                   "BlackForest": T.BIOME_CODE["BlackForest"]}


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _fixture_zone(zone, corners, height_of) -> tuple[np.ndarray, np.ndarray]:
    """One zone of the ENGINE surface, by HeightmapBuilder's rule: the four
    corner biomes' height functions blended by SmoothStep of the vertex index
    fraction, or corner b0's alone when the four agree."""
    zx, zz = zone
    off = np.arange(tcdata.PITCH, dtype=np.float64)
    wx = (zx * tcdata.ZONE_SIZE - tcdata.ZONE_SIZE / 2) + off[None, :]
    wz = (zz * tcdata.ZONE_SIZE - tcdata.ZONE_SIZE / 2) + off[:, None]
    wx, wz = np.broadcast_to(wx, (65, 65)), np.broadcast_to(wz, (65, 65))
    b0, b1, b2, b3 = corners
    if b0 == b1 == b2 == b3:
        h = height_of[b0](wx, wz)
    else:
        tx = _smoothstep(off / 64.0)[None, :]
        tz = _smoothstep(off / 64.0)[:, None]
        h0, h1, h2, h3 = (height_of[b](wx, wz) for b in corners)
        lo, hi = h0 + (h1 - h0) * tx, h2 + (h3 - h2) * tx
        h = lo + (hi - lo) * tz
    biome = np.full((65, 65), _FIXTURE_BIOMES["Mountain"], np.uint8)
    for (i, j), b in zip(((0, 0), (0, 64), (64, 0), (64, 64)), corners):
        biome[i, j] = _FIXTURE_BIOMES[b]
    return np.asarray(h, dtype=np.float32).astype(np.float64), biome


def _fixture_field(cx, cz, half, height) -> T.Field:
    """The FINDING field on its own lattice (x0 = cx - half + 0.5), every
    point its own biome's height -- Mountain throughout."""
    n = int(round(2 * half))
    x0, z0 = cx - half + 0.5, cz - half + 0.5
    xs, zs = x0 + np.arange(n), z0 + np.arange(n)
    h = np.asarray(height(xs[None, :], zs[:, None]), dtype=np.float32)
    return T.Field("fixture", cx, cz, half, 1.0, n, h,
                   np.full((n, n), _FIXTURE_BIOMES["Mountain"], np.uint8))


def site_bill_fixture(name: str) -> dict:
    """`{surface, field, pads, w, d, prior}` for one named fixture."""
    if name == "sudrberg-keep":
        # Two zones whose WEST corners are BlackForest over an interior that
        # is all Mountain: the finding field reads Mountain everywhere and
        # the engine blends BlackForest's lower ground in toward that edge.
        mtn = lambda x, z: 116.0 + 1.5 * np.sin(x / 9.0) + 1.2 * np.cos(z / 7.0)
        hts = {"Mountain": mtn,
               "BlackForest": lambda x, z: mtn(x, z) - 15.0}
        zones = {(-3, -42): ("BlackForest", "Mountain", "Mountain", "Mountain"),
                 (-3, -41): ("Mountain", "Mountain", "BlackForest", "Mountain")}
        pads, w, d = [(-200.8, -2634.6)], 44.0, 44.0
        # outlier-blackforest112's lattice: x0 = -1198.3, z0 = -4512.1
        field = _fixture_field(-199.8, -2634.6, 60.0, mtn)
        prior = ("plan.Fresh42.json sudrberg-keep (-200.8, -2634.6), 44 x 44: "
                 "plan bill over_clamp 0 / fill 6.53 / cut 3.41 on the field; "
                 "write-time over_clamp 279 / fill 17.403 / cut -1.641 on the "
                 "heightmap surface; zones (-3,-42) corners BF/M/M/M and "
                 "(-3,-41) corners M/M/BF/M, 98% Mountain inside, surfaces "
                 "differ by up to 11.057 m under the pad")
    elif name == "hognest-castle":
        mtn = lambda x, z: 106.0 + 2.6 * np.sin(x / 11.0) + 2.0 * np.cos(z / 13.0)
        hts = {"Mountain": mtn}
        zones = {(-118, -1): ("Mountain",) * 4, (-118, 0): ("Mountain",) * 4}
        pads, w, d = [(-7556.5, -11.5)], 44.0, 44.0
        # trunk-secondary's lattice is the TerrainComp lattice: x0 = -8980.
        field = _fixture_field(-7556.5, -11.5, 60.0, mtn)
        prior = ("plan.Fresh42.json hognest-castle (-7556.5, -11.5), 44 x 44: "
                 "plan 4.77 / 5.06, write-time 4.768 / -5.060 -- agreement to "
                 "three decimals, surfaces within 0.004 m under the pad")
    elif name == "stenvik-hall-1":
        mtn = lambda x, z: (32.0 + 1.2 * np.sin(x / 5.0) + 0.9 * np.cos(z / 4.0)
                            + 0.03 * (x - 350.0))
        hts = {"Mountain": mtn}
        zones = {(zx, zz): ("Mountain",) * 4 for zx in (4, 5, 6)
                 for zz in (-4, -3, -2)}
        pads, w, d = [(350.1, -208.8)], 26.1, 26.1
        # trunk-primary's lattice: x0 = -1605.6, z0 = -1596.3 -- OFF the
        # TerrainComp lattice by 0.4 m and 0.7 m.
        field = _fixture_field(349.9, -208.8, 60.0, mtn)
        prior = ("plan.Fresh42.json stenvik-hall-1 (350.1, -208.8), 26.1 m at "
                 "yaw 30 on trunk-primary: plan 681 samples fill 1.05 / cut "
                 "1.28 on the rotated rectangle; the write stamps 756 samples "
                 "axis-aligned, fill 1.036 / cut -1.872, on IDENTICAL ground")
    else:
        raise KeyError(name)
    surface = SF.EngineSurface("fixture", SF.SITE_BILL_SURFACE, zones={
        z: _fixture_zone(z, c, hts) for z, c in zones.items()})
    return {"surface": surface, "field": field, "pads": pads, "w": w, "d": d,
            "prior": prior}


def bill_against_the_write(fx: dict, depends_on: tuple | None = None,
                           surface: SF.EngineSurface | None = None) -> dict:
    """This module's bill for a fixture and `flatten.build`'s write of the
    same pad at the same target.  `agree` is judged on the figures
    `tcdata.clamp_verdict` judges -- over_clamp, max fill, max cut -- at the
    three decimals both round to.  The sample COUNT is reported and not
    judged: the writer stamps a seam row once in each zone that owns it, so a
    pad across a seam writes one row twice and its extremes are unchanged
    (hognest-castle: 1,936 rectangle samples, 1,980 written).  A bill that
    could not be made is not an agreement."""
    surface = fx["surface"] if surface is None else surface
    (x, z), w, d = fx["pads"][0], fx["w"], fx["d"]
    mine = site_bill(surface, fx["pads"], w, d, field=fx["field"],
                     depends_on=depends_on)
    if mine["verdict"] == "unread":
        return {"agree": False, "mine": mine, "write": None}
    place = {"id": "fixture", "solved": {
        "x": x, "z": z,
        "footprint_orientation": f"{w:g} m along x by {d:g} m along z",
        "flatten_cost": {"target_y": mine["target_y"]}}}
    zones = FL.zones_for(x, z, w, d)
    if fx["surface"].missing(zones):
        return {"agree": False, "mine": mine, "write": None}
    st = FL.build(place, {f"z_{a}_{b}": fx["surface"].patch((a, b))
                          for a, b in zones}, zones, "paved_cleared", 0.0, {}, {})
    agree = (st["over_clamp"] == mine["over_clamp"]
             and abs(st["max_fill"] - mine["max_fill_m"]) < 5e-4
             and abs(-st["max_cut"] - mine["max_cut_m"]) < 5e-4)
    return {"agree": agree, "mine": mine, "write": {
        "samples": st["samples"], "over_clamp": st["over_clamp"],
        "max_fill_m": st["max_fill"], "max_cut_m": st["max_cut"]}}


def _brief(b: dict | None) -> str:
    if not b:
        return "none"
    return (f"verdict {b.get('verdict', '-')} samples {b.get('samples')} "
            f"over_clamp {b.get('over_clamp')} fill {b.get('max_fill_m')} "
            f"cut {b.get('max_cut_m')} target {b.get('target_y')}")


def _case_sudrberg_keep_is_REFUSED_on_the_engine_surface() -> list[str]:
    """THE KNOWN-BAD STATE.  Billed where the engine levels, it cannot be
    levelled inside TerrainComp's clamp."""
    fx = site_bill_fixture("sudrberg-keep")
    b = site_bill(fx["surface"], fx["pads"], fx["w"], fx["d"], field=fx["field"])
    return [] if b["verdict"] == "refused" else [
        f"sudrberg-keep billed {_brief(b)}; it had to be REFUSED"]


def _case_NEUTER_drop_engine_surface_sudrberg_keep_comes_back_CLEAN() -> list[str]:
    """LOSS ARM.  On the finding field the pre-fix bill returns: clean."""
    fx = site_bill_fixture("sudrberg-keep")
    b = site_bill(fx["surface"], fx["pads"], fx["w"], fx["d"], field=fx["field"],
                  depends_on=("written_samples",))
    return [] if b["verdict"] == "clear" else [
        f"without `engine_surface` the bill still refused ({_brief(b)}), so "
        f"the engine surface is not the term the refusal rests on"]


def _case_hognest_castle_bill_is_the_write_to_three_decimals() -> list[str]:
    """SURVIVOR, AS A VERDICT.  On the engine surface the bill IS the
    writer's -- which an instrument that read nothing cannot produce."""
    got = bill_against_the_write(site_bill_fixture("hognest-castle"))
    return [] if got["agree"] and got["mine"]["verdict"] == "clear" else [
        f"hognest-castle: bill {_brief(got['mine'])} vs write "
        f"{_brief(got['write'])}"]


def _case_the_yawed_town_pad_bill_is_the_write() -> list[str]:
    """The population term's pass direction: a pad at yaw 30 on a field
    lattice 0.4 m off the TerrainComp one is billed over what is stamped."""
    got = bill_against_the_write(site_bill_fixture("stenvik-hall-1"))
    return [] if got["agree"] else [
        f"stenvik-hall-1: bill {_brief(got['mine'])} vs write "
        f"{_brief(got['write'])}"]


def _case_NEUTER_drop_written_samples_the_yawed_pad_is_NOT_the_write() -> list[str]:
    """LOSS ARM for the population: the rotated rectangle on the finding
    field's lattice is a different set of samples from the one written."""
    got = bill_against_the_write(site_bill_fixture("stenvik-hall-1"),
                                 depends_on=("engine_surface",))
    return [] if not got["agree"] else [
        f"without `written_samples` the yawed pad still agreed with the write "
        f"({_brief(got['mine'])}), so the population term carries nothing"]


def _case_NEUTER_drop_written_samples_hognest_castle_SURVIVES() -> list[str]:
    """SURVIVOR.  An axis-aligned pad on a lattice-aligned field is the same
    population either way, so dropping the term must NOT disturb it: the
    loss arm above is the population, not a broken instrument."""
    got = bill_against_the_write(site_bill_fixture("hognest-castle"),
                                 depends_on=("engine_surface",))
    return [] if got["agree"] else [
        f"hognest-castle stopped agreeing without `written_samples`: bill "
        f"{_brief(got['mine'])} vs write {_brief(got['write'])}"]


def _case_an_unread_engine_surface_is_REFUSED() -> list[str]:
    """UNREAD IS NOT CLEAR.  Zones nobody read are a refusal, not a bill."""
    fx = site_bill_fixture("hognest-castle")
    empty = SF.EngineSurface("fixture", SF.SITE_BILL_SURFACE)
    b = site_bill(empty, fx["pads"], fx["w"], fx["d"], field=fx["field"])
    return [] if b["verdict"] == "unread" and not b["clamp_ok"] else [
        f"a pad on zones nobody read billed {_brief(b)}"]


SITE_BILL_CASES = (
    ("sudrberg_keep_is_REFUSED_on_the_engine_surface",
     _case_sudrberg_keep_is_REFUSED_on_the_engine_surface),
    ("NEUTER_drop_engine_surface_sudrberg_keep_comes_back_CLEAN",
     _case_NEUTER_drop_engine_surface_sudrberg_keep_comes_back_CLEAN),
    ("hognest_castle_bill_is_the_write_to_three_decimals",
     _case_hognest_castle_bill_is_the_write_to_three_decimals),
    ("the_yawed_town_pad_bill_is_the_write",
     _case_the_yawed_town_pad_bill_is_the_write),
    ("NEUTER_drop_written_samples_the_yawed_pad_is_NOT_the_write",
     _case_NEUTER_drop_written_samples_the_yawed_pad_is_NOT_the_write),
    ("NEUTER_drop_written_samples_hognest_castle_SURVIVES",
     _case_NEUTER_drop_written_samples_hognest_castle_SURVIVES),
    ("an_unread_engine_surface_is_REFUSED",
     _case_an_unread_engine_surface_is_REFUSED),
)


# --- SELFTEST: ONE SITE'S GROUND, BOTH WAYS, ON RECORDS BUILT IN MEMORY ------
# Every record below is CONSTRUCTED here; its units come from
# `build.unit_records` and its samples from the writer's own stamp.  The
# coordinates are plan.Fresh42.json's, and each fixture's `prior` names the
# state it stands for.
_FIXTURE_LIGHT = {"id": "lh-west", "kind": "lighthouse", "pad_m": 22.0,
                  "cluster": 1, "body": "drake-lighthouse.blueprint",
                  "body_footprint": (14.5, 14.5), "patch": "trunk-primary"}
_FIXTURE_TREES = {"id": "tree-near", "kind": "treehouse", "pad_m": 16.0,
                  "cluster": 3, "body": "hs_meadows_stilt_house.blueprint",
                  "body_footprint": (4.4, 8.4), "patch": "trunk-primary"}
_FIXTURE_TOWER = {"id": "wt-fixture", "kind": "watchtower", "pad_m": 20.0,
                  "cluster": 1, "body": "outposttower.blueprint",
                  "body_footprint": (12.4, 12.0), "patch": "trunk-primary"}


def site_claim_fixture(name: str, depends_on=None) -> dict:
    """`{claims, candidate, prior}`: a registry holding what is already
    claimed, and one candidate claim to judge against it.  Built FRESH per
    call, so a neuter of SITE_CLAIM_DEPENDS_ON reaches the candidate's own
    derivation as well as the judgement."""
    claims = SiteClaims()
    if name == "lh-west+tree-near@5.7m":
        claims.add(claims.outlier(_FIXTURE_LIGHT, -416.1, -738.8,
                                  depends_on=depends_on))
        cand = claims.outlier(_FIXTURE_TREES, -412.1, -734.8,
                              depends_on=depends_on)
        prior = ("the first Fresh42 plan (ledger/runs/Fresh42/pipeline/sites/"
                 "plan.Fresh42.json): lh-west (-416.1, -738.8) and tree-near "
                 "(-412.1, -734.8), 5.66 m apart, 168 + 272 + 238 lattice "
                 "samples stamped by both at targets 0.40 m apart, and "
                 "certify_plan clear on every one of its 49 units")
    elif name == "cluster-member":
        # The cluster at its re-solved centre; the lighthouse on the side
        # where a WRITTEN member reaches furthest past the searched CENTRE
        # pad, its nearest sample two lattice steps beyond the centre pad's.
        cx, cz = -406.1, -718.8
        tree = {**_FIXTURE_TREES, "x": cx, "z": cz}
        members = [stamped_samples(u) for u in _candidate_units(tree)]
        centre = stamped_samples({"id": "centre", "x": cx, "z": cz,
                                  "pad_w": tree["pad_m"], "pad_d": tree["pad_m"],
                                  "target_y": 0.0})
        best = None
        for axis in (0, 1):
            for sgn in (1, -1):
                edge = int((sgn * centre[:, axis]).max())
                reach = max(int((sgn * m[:, axis]).max()) for m in members)
                if best is None or reach - edge > best[0]:
                    best = (reach - edge, axis, sgn, edge)
        gain, axis, sgn, edge = best
        if gain < 3:
            raise ValueError(
                f"cluster-member fixture: no written member of tree-near "
                f"reaches 3 m past its centre pad (best {gain} m), so this "
                f"fixture no longer states the defect")
        at = [cx, cz]
        at[axis] = sgn * (edge + 2 + _FIXTURE_LIGHT["pad_m"] / 2
                          + SF.WRITE_EDGE_EPS_M - 0.05)
        claims.add(claims.outlier(_FIXTURE_LIGHT, at[0], at[1],
                                  depends_on=depends_on))
        cand = claims.outlier(_FIXTURE_TREES, cx, cz, depends_on=depends_on)
        prior = ("plan.Fresh42.json as re-solved: lh-west (-416.1, -738.8) "
                 "and tree-near (-406.1, -718.8) 22.4 m apart, the searched "
                 "centre pads 1.0 m apart with no sample in common, and "
                 "tree-near-3's WRITTEN stamp sharing 24 samples with "
                 "lh-west's, 1.69 m below it")
    elif name == "yawed-body":
        hall = {"id": "stenvik-hall-1", "x": 350.1, "z": -208.8, "yaw": 30.0,
                "pad_w": 26.1, "pad_d": 26.1, "foot_w": 20.1, "foot_d": 20.1,
                "target_y": 31.57}
        claims.add(SiteClaims.claim("stenvik", "town", [hall],
                                    hall["target_y"]))
        edge = int(stamped_samples(hall)[:, 0].max())
        a = math.radians(hall["yaw"])
        c, s = math.cos(a), math.sin(a)
        hw = hall["foot_w"] / 2
        kx, kz = max((hall["x"] + lx * c + lz * s, hall["z"] - lx * s + lz * c)
                     for lx in (-hw, hw) for lz in (-hw, hw))
        if not kx > edge:
            raise ValueError(
                f"yawed-body fixture: the body's corner x {kx:.2f} no longer "
                f"stands off its stamp (last column {edge})")
        cand = claims.outlier(
            _FIXTURE_TOWER,
            edge + 1 + _FIXTURE_TOWER["pad_m"] / 2 + SF.WRITE_EDGE_EPS_M - 0.05,
            kz, depends_on=depends_on)
        prior = ("plan.Fresh42.json stenvik-hall-1 (350.1, -208.8): 20.1 m "
                 "square body at yaw 30 needs a 27.46 m box and is stamped on "
                 "a 26.1 m axis-aligned pad, so its corner stands 0.68 m off "
                 "its own levelled ground")
    elif name == "far-apart":
        claims.add(claims.outlier(_FIXTURE_LIGHT, -416.1, -738.8,
                                  depends_on=depends_on))
        cand = claims.outlier(_FIXTURE_TREES, -216.1, -738.8,
                              depends_on=depends_on)
        prior = "the same two sites 200 m apart"
    elif name == "stamps-nothing":
        claims.add(claims.outlier(_FIXTURE_LIGHT, -416.1, -738.8,
                                  depends_on=depends_on))
        cand = SiteClaims.claim("void", "watchtower", [{
            "id": "void", "x": 100.5, "z": 100.5, "yaw": 0.0, "pad_w": 0.0,
            "pad_d": 0.0, "foot_w": 0.0, "foot_d": 0.0, "target_y": 0.0}])
        prior = "a unit whose stamp holds no lattice sample"
    else:
        raise KeyError(name)
    return {"claims": claims, "candidate": cand, "prior": prior}


def _claim_verdict(name: str, depends_on=None) -> list[dict]:
    fx = site_claim_fixture(name, depends_on)
    return fx["claims"].conflicts(fx["candidate"], depends_on)


def _drop(term: str) -> tuple:
    return tuple(t for t in SITE_CLAIM_DEPENDS_ON if t != term)


def _case_claim_the_5_7_m_pair_is_REFUSED() -> list[str]:
    """THE KNOWN-BAD STATE, across kinds: a lighthouse and a treehouse
    cluster 5.7 m apart."""
    hits = _claim_verdict("lh-west+tree-near@5.7m")
    shared = sum(h.get("shared_samples") or 0 for h in hits)
    return [] if hits and shared else [
        f"tree-near 5.7 m from lh-west came back with {len(hits)} "
        f"conflict(s), {shared} shared samples; it had to be REFUSED"]


def _case_claim_NEUTER_drop_every_kind_the_5_7_m_pair_is_ACCEPTED() -> list[str]:
    """LOSS ARM.  Judged against its own kind only -- `sites.dedupe`'s
    population -- the pair is accepted again."""
    hits = _claim_verdict("lh-west+tree-near@5.7m", _drop("every_kind"))
    return [] if not hits else [
        f"without `every_kind` the pair is still refused ({claim_brief(hits)}), "
        f"so the population is not the term the refusal rests on"]


def _case_claim_a_written_cluster_member_is_REFUSED() -> list[str]:
    """THE POPULATION OF ONE SITE: the member the writer puts on the
    lighthouse's side enters its stamp."""
    hits = _claim_verdict("cluster-member")
    return [] if hits and any(h.get("shared_samples") for h in hits) else [
        f"the cluster whose written member enters lh-west's stamp came back "
        f"with {len(hits)} conflict(s); it had to be REFUSED"]


def _case_claim_NEUTER_drop_written_samples_the_member_is_ACCEPTED() -> list[str]:
    """LOSS ARM.  With the claim cut back to the pad the search slid, the
    member's samples are nobody's and the cluster is accepted."""
    hits = _claim_verdict("cluster-member", _drop("written_samples"))
    return [] if not hits else [
        f"without `written_samples` the cluster is still refused "
        f"({claim_brief(hits)}), so the written units carry nothing"]


def _case_claim_a_yawed_body_is_REFUSED() -> list[str]:
    """THE BODY: a stamp one lattice step past a yawed hall's own stamp
    levels under the hall's corner."""
    hits = _claim_verdict("yawed-body")
    under = [h for h in hits if (h.get("my_stamp_to_their_body_m") is not None
                                 and h["my_stamp_to_their_body_m"]
                                 < clearance.TERRAIN_SPREAD_M)]
    return [] if under else [
        f"a watchtower stamped under stenvik-hall-1's corner came back with "
        f"{len(hits)} conflict(s), none under the body; it had to be REFUSED"]


def _case_claim_NEUTER_drop_bodies_the_yawed_body_is_ACCEPTED() -> list[str]:
    """LOSS ARM.  Stamps alone share no sample here, so without `bodies`
    the tower is accepted under the hall's corner."""
    hits = _claim_verdict("yawed-body", _drop("bodies"))
    return [] if not hits else [
        f"without `bodies` the tower is still refused ({claim_brief(hits)}), "
        f"so the body is not the term the refusal rests on"]


def _case_claim_far_apart_sites_are_ACCEPTED_under_every_arm() -> list[str]:
    """SURVIVOR, AS A VERDICT, under the full declaration and under each
    one-term drop: the loss arms above are the terms, not a broken index."""
    out = []
    for dep in (SITE_CLAIM_DEPENDS_ON,) + tuple(
            _drop(t) for t in SITE_CLAIM_DEPENDS_ON):
        hits = _claim_verdict("far-apart", dep)
        if hits:
            out.append(f"200 m apart under {list(dep)}: {claim_brief(hits)}")
    return out


def _case_claim_a_stamp_of_nothing_is_REFUSED() -> list[str]:
    """UNREAD IS NOT CLEAR: a claim with no sample in it is refused."""
    hits = _claim_verdict("stamps-nothing")
    return [] if hits and hits[0].get("verdict") == "unread" else [
        f"a unit that stamps nothing came back {hits or 'clear'}"]


def _fixture_plan_rec(base: dict, x: float, z: float, target_y: float) -> dict:
    return {**base, "x": x, "z": z, "body_sha256": None, "pieces": 0,
            "pad": {"yaw_deg": 0.0, "width_m": base["pad_m"],
                    "depth_m": base["pad_m"], "target_y": target_y}}


def _case_claim_published_keepout_covers_every_written_sample() -> list[str]:
    """THE KEEP-OUT ROADNET READS IS THE WRITTEN GROUND: every sample the
    writer stamps for a cluster lies inside its published ring less its
    2.0 m margin (the old ring, 13.3 m, left samples 15 m out), and a single
    body's ring is its pad's, unchanged."""
    plan = {"towns": {}, "outliers": [
        _fixture_plan_rec(_FIXTURE_TREES, -406.1, -718.8, 34.46),
        _fixture_plan_rec(_FIXTURE_LIGHT, -416.1, -738.8, 32.77)]}
    out = []
    for rec in plan["outliers"]:
        ring = site_keepout_m(plan, rec["id"]) - 2.0
        far = max(float(np.hypot(p[:, 0] - rec["x"], p[:, 1] - rec["z"]).max())
                  for p in (stamped_samples(u) for u in
                            _writer().unit_records(plan, rec["id"])))
        # A stamped sample may sit WRITE_EDGE_EPS_M past the pad's edge on
        # each axis, so past its corner by at most that times sqrt(2).
        if far > ring + SF.WRITE_EDGE_EPS_M * math.sqrt(2):
            out.append(f"{rec['id']}: a written sample stands {far:.2f} m "
                       f"from the centre, outside the published ring "
                       f"{ring:.1f} m + margin")
    single = site_keepout_m(plan, "lh-west")
    if single != round(_FIXTURE_LIGHT["pad_m"] * math.sqrt(2) / 2 + 2.0, 1):
        out.append(f"lh-west's ring moved to {single} m; one body's ring is "
                   f"its pad's")
    return out


def _case_claim_audit_refuses_the_first_Fresh42_pair() -> list[str]:
    """THE PLAN-LEVEL CHECK on the known-bad plan's two records: every one of
    the three written members shares samples with lh-west."""
    plan = {"towns": {}, "outliers": [
        _fixture_plan_rec(_FIXTURE_LIGHT, -416.1, -738.8, 32.37),
        _fixture_plan_rec(_FIXTURE_TREES, -412.1, -734.8, 32.77)]}
    got = claim_audit(plan)
    units = {h["unit"] for h in got["conflicts"] if h.get("shared_samples")}
    return [] if units == {"tree-near-1", "tree-near-2", "tree-near-3"} else [
        f"claim_audit named {sorted(units)} of tree-near's three members"]


def _case_claim_audit_one_cluster_is_one_owner() -> list[str]:
    """SURVIVOR, AS A VERDICT: a cluster's members overlap by design and are
    written at ONE target, so they are one write and not a conflict."""
    plan = {"towns": {}, "outliers": [
        _fixture_plan_rec(_FIXTURE_TREES, -406.1, -718.8, 34.46)]}
    got = claim_audit(plan)
    return [] if got["writes"] == 1 and not got["conflicts"] else [
        f"one cluster read as {got['writes']} write(s) with "
        f"{len(got['conflicts'])} conflict(s)"]


SITE_CLAIM_CASES = (
    ("claim_the_5_7_m_pair_is_REFUSED",
     _case_claim_the_5_7_m_pair_is_REFUSED),
    ("claim_NEUTER_drop_every_kind_the_5_7_m_pair_is_ACCEPTED",
     _case_claim_NEUTER_drop_every_kind_the_5_7_m_pair_is_ACCEPTED),
    ("claim_a_written_cluster_member_is_REFUSED",
     _case_claim_a_written_cluster_member_is_REFUSED),
    ("claim_NEUTER_drop_written_samples_the_member_is_ACCEPTED",
     _case_claim_NEUTER_drop_written_samples_the_member_is_ACCEPTED),
    ("claim_a_yawed_body_is_REFUSED",
     _case_claim_a_yawed_body_is_REFUSED),
    ("claim_NEUTER_drop_bodies_the_yawed_body_is_ACCEPTED",
     _case_claim_NEUTER_drop_bodies_the_yawed_body_is_ACCEPTED),
    ("claim_far_apart_sites_are_ACCEPTED_under_every_arm",
     _case_claim_far_apart_sites_are_ACCEPTED_under_every_arm),
    ("claim_a_stamp_of_nothing_is_REFUSED",
     _case_claim_a_stamp_of_nothing_is_REFUSED),
    ("claim_audit_refuses_the_first_Fresh42_pair",
     _case_claim_audit_refuses_the_first_Fresh42_pair),
    ("claim_audit_one_cluster_is_one_owner",
     _case_claim_audit_one_cluster_is_one_owner),
    ("claim_published_keepout_covers_every_written_sample",
     _case_claim_published_keepout_covers_every_written_sample),
)


# ---- CLEARING_GATE_DEPENDS_ON, neutered ------------------------------------
# The fixture is CONSTRUCTED from drive 5's own numbers: Crypt4 and Ruin1 as
# Fresh42's dump (`ledger/runs/Fresh42/pipeline/loc/74cb38be48da.json`)
# records them, and wt-south's unit as the drive-5 plan wrote it.
_CLEAR_LOCS = [
    {"name": "Crypt4", "prefab": "Crypt4", "x": -629.22, "z": -394.37,
     "exteriorRadius": 18, "interiorRadius": 21, "quantity": 200},
    {"name": "Ruin1", "prefab": "Ruin1", "x": -711.04, "z": -385.26,
     "exteriorRadius": 10.7, "interiorRadius": 0, "quantity": 200},
]
_CLEAR_TYPES = [
    {"prefab": "Crypt4", "name": "Crypt4", "exteriorRadius": 18,
     "interiorRadius": 21, "znviewReachM": 18.44},
    {"prefab": "Ruin1", "name": "Ruin1", "exteriorRadius": 10.7,
     "interiorRadius": 0, "znviewReachM": 7.46},
]
# MEASURED in the drive-5 frozen save (gate census 8c8c54096789): the four
# objects the build's live census refused wt-south on, all at y 5061.14.
_WTSOUTH_BLOCKERS = [("dungeon_forestcrypt_door", -662.09, -379.67),
                     ("dungeon_forestcrypt_door", -658.71, -376.74),
                     ("CastleKit_groundtorch", -661.2, -383.09),
                     ("dungeon_forestcrypt_door", -665.15, -387.06)]


def _clear_loc() -> clearance.Locations:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "loc.json"
        p.write_text(json.dumps({"seed": "fixture", "locations": _CLEAR_LOCS,
                                 "locationTypes": _CLEAR_TYPES}))
        return clearance.load(p)


def _wtsouth_unit(x: float = -672.1, z: float = -372.8) -> dict:
    return {"id": "wt-south", "body": "outposttower.blueprint", "sha256": None,
            "pieces": 212, "x": x, "z": z, "yaw": 0.0, "pad_w": 20.0,
            "pad_d": 20.0, "foot_w": 12.4, "foot_d": 12.0, "target_y": 72.48,
            "ground_y": 72.48, "role": "watchtower", "patch": "trunk-primary"}


def _case_clearing_the_drive5_wtsouth_spot_is_REFUSED() -> list[str]:
    """KNOWN-BAD: wt-south where drive 5 sited it; the build refused it on
    Crypt4's interior, and the siting must refuse it first, by number."""
    got = clearing_refusals(_clear_loc(), "wt-south", [_wtsouth_unit()])
    if len(got) != 1:
        return [f"{len(got)} refusal(s), want 1: {got}"]
    g = got[0]
    bad = []
    if (g["location"], g["population"]) != ("Crypt4", "interior_zone"):
        bad.append(f"bound by {g['location']} {g['population']}, want Crypt4 "
                   f"interior_zone")
    if g["radius_m"] != 16.14 or g["max_radius_m"] is None \
            or g["max_radius_m"] >= g["radius_m"]:
        bad.append(f"radius {g['radius_m']} vs room {g['max_radius_m']}")
    return bad


def _case_clearing_NEUTER_pieces_only_the_drive5_spot_is_LEGAL() -> list[str]:
    """LOSS ARM: the siting's old population (pieces about the marker) --
    26.0 m of room against a 16.14 m cylinder -- passes the spot the build
    refused."""
    got = clearing_refusals(_clear_loc(), "wt-south", [_wtsouth_unit()],
                            depends_on=("pieces",))
    return [] if not got else [f"pieces-only refused it: {got}"]


def _case_clearing_the_measured_blockers_are_in_the_population() -> list[str]:
    """THE POPULATION, AGAINST THE OBJECTS: every blocker the live census
    found stands inside Crypt4's interior zone, and outside its pieces disc
    -- so the new term is what covers them and the old could not."""
    loc, bad = _clear_loc(), []
    for name, x, z in _WTSOUTH_BLOCKERS:
        inner = loc.cylinder_room(x, z, ("interior_zone",))["max_radius_m"]
        piece = loc.cylinder_room(x, z, ("pieces",))["max_radius_m"]
        if inner + clearance.CLEARING_SLACK_M > 0:
            bad.append(f"{name} ({x}, {z}) is outside the interior zone")
        if piece + clearance.CLEARING_SLACK_M <= 0:
            bad.append(f"{name} ({x}, {z}) is inside the pieces disc")
    return bad


def _case_clearing_a_pad_clear_of_both_is_ACCEPTED_under_every_arm() -> list[str]:
    """SURVIVOR: 28 m west of the crypt's zone and 46 m from Ruin1."""
    loc, bad = _clear_loc(), []
    for dep in (CLEARING_GATE_DEPENDS_ON, ("pieces",), ("interior_zone",)):
        got = clearing_refusals(loc, "wt-south", [_wtsouth_unit(-700.0, -340.0)],
                                depends_on=dep)
        if got:
            bad.append(f"{dep}: {got}")
    return bad


def _case_clearing_NEUTER_interior_only_a_ruins_pieces_come_back() -> list[str]:
    """THE OTHER TERM IS LIVE: 22.0 m west of Ruin1's marker (reach 10.7,
    so 10.3 m of room for a 16.14 m cylinder, and farther than the cylinder
    so `area`'s own nearest-marker bound does not decide it) the pieces term
    refuses; drop it and the pad is accepted."""
    loc, at = _clear_loc(), _wtsouth_unit(-733.04, -385.26)
    full = clearing_refusals(loc, "wt-south", [at])
    neut = clearing_refusals(loc, "wt-south", [at],
                             depends_on=("interior_zone",))
    bad = []
    if not full or (full[0]["location"], full[0]["population"]) != (
            "Ruin1", "pieces"):
        bad.append(f"full arm: {full}")
    if neut:
        bad.append(f"interior-only arm refused: {neut}")
    return bad


CLEARING_GATE_CASES = (
    ("clearing_the_drive5_wtsouth_spot_is_REFUSED",
     _case_clearing_the_drive5_wtsouth_spot_is_REFUSED),
    ("clearing_NEUTER_pieces_only_the_drive5_spot_is_LEGAL",
     _case_clearing_NEUTER_pieces_only_the_drive5_spot_is_LEGAL),
    ("clearing_the_measured_blockers_are_in_the_population",
     _case_clearing_the_measured_blockers_are_in_the_population),
    ("clearing_a_pad_clear_of_both_is_ACCEPTED_under_every_arm",
     _case_clearing_a_pad_clear_of_both_is_ACCEPTED_under_every_arm),
    ("clearing_NEUTER_interior_only_a_ruins_pieces_come_back",
     _case_clearing_NEUTER_interior_only_a_ruins_pieces_come_back),
)


# ---- BODY_REFUSAL_DEPENDS_ON, neutered -------------------------------------
# CONSTRUCTED: lh-west DECLARED with the Pirate68 lighthouse body, whose
# emitter verdict is the two refusals it carried on 2026-09-27; lh-south
# with a body that emits; a town building with the refused body too.
_REFUSED_BODY = "hs_plains_ulf_the_builder_lighthouse.blueprint"
_GOOD_BODY = "drake-lighthouse.blueprint"
_REFUSED_VERDICT = [
    {"override": "--allow-unsheltered-deck", "code": "unsheltered_deck",
     "detail": "3 deck column(s) of this blueprint are ENCLOSED BY WALLS and "
               "OPEN TO THE SKY (37 open in all, of 170 deck columns, under "
               "a 48-panel roof)."},
    {"override": "--allow-blocked-seats", "code": "blocked_seat",
     "detail": "1 claimable bed(s) ...: row 180 bed at local "
               "(4.881,2.786,9.309): seat_penetration, seat_headroom - "
               "-0.146 m of room against wood_stair"}]


def _body_verdict(name: str) -> dict:
    ok = name != _REFUSED_BODY
    return {"name": name, "ok": ok, "commands": 1255 if ok else 0,
            "missing": [], "refusals": [] if ok else _REFUSED_VERDICT,
            "repaired": [], "repairs": [], "resolved": True,
            "reason": "emits 1255 command(s)" if ok else
                      "2 geometry preflight refusal(s)"}


def _body_gate_plan() -> dict:
    pad = {"moved_m3": 10.0, "clamp_headroom_m": 5.0, "width_m": 10.0,
           "depth_m": 10.0, "clamp_ok": True}
    b = {"id": "vik-house-1", "role": "house", "body": _REFUSED_BODY,
         "pad": pad, "pieces": 1267, "beds": 1, "stations": 0}
    square = {"moved_m3": 1.0, "width_m": 24.0, "depth_m": 24.0}
    town = {"buildings": [b], "square": square, "unplaced": []}
    town["totals"] = LY.TownPlanner.totals(
        [__import__("types").SimpleNamespace(**b)], square, [])
    body = {"pieces": 1, "count": 1, "missing_prefabs": [], "grounded": "x"}
    return {"towns": {"vik": town},
            "outliers": [
                {"id": "lh-west", "kind": "lighthouse", "tag": "u-lhwest",
                 "body": _REFUSED_BODY, "centre_declared": True,
                 "body_report": None},
                {"id": "lh-south", "kind": "lighthouse", "tag": "u-lhsouth",
                 "body": _GOOD_BODY, "centre_declared": True,
                 "body_report": None}],
            "bodies": {_REFUSED_BODY: dict(body), _GOOD_BODY: dict(body)},
            "refused_sites": []}


def _case_body_a_declared_site_whose_body_refuses_is_refused_and_named(
        ) -> list[str]:
    """KNOWN-BAD, PIRATE68 STAGE 8: the declared lh-west keeps its body and
    is REFUSED with it, the body's two refusals and their numbers on the
    record; the sibling lh-south still solves; the town building that used
    the same body is unplaced, and its town's totals no longer count it."""
    plan = _body_gate_plan()
    body_gate(plan, _body_verdict)
    bad = []
    got = {s["site"]: s for s in plan["refused_sites"]}
    lw = got.get("lh-west")
    if not lw or not lw["declared"] or lw["bodies"][0]["body"] != _REFUSED_BODY:
        bad.append(f"lh-west not refused with its declared body: {got}")
    elif [r["code"] for r in lw["bodies"][0]["refusals"]] != [
            "unsheltered_deck", "blocked_seat"]:
        bad.append(f"the refusal lost its numbers: {lw['bodies'][0]}")
    if [o["id"] for o in plan["outliers"]] != ["lh-south"]:
        bad.append(f"the sibling did not solve: {plan['outliers']}")
    t = plan["towns"]["vik"]
    if t["buildings"] or [u["id"] for u in t["unplaced"]] != ["vik-house-1"]:
        bad.append(f"the town building stayed: {t['buildings']}")
    if t["totals"]["buildings"] != 0 or t["totals"]["buildings_unplaced"] != 1:
        bad.append(f"town totals not re-taken: {t['totals']}")
    if _REFUSED_BODY in plan["bodies"] or \
            plan["bodies_refused"][_REFUSED_BODY]["used_by"] != [
                "lh-west", "vik-house-1"]:
        bad.append(f"the body's users are not named: "
                   f"{plan.get('bodies_refused')}")
    return bad


def _case_body_NEUTER_whole_stage_one_body_kills_the_stage() -> list[str]:
    """LOSS ARM: the shipped gate raised for the whole plan on one body."""
    global BODY_REFUSAL_MODE
    was = BODY_REFUSAL_MODE
    BODY_REFUSAL_MODE = "whole_stage"
    try:
        body_gate(_body_gate_plan(), _body_verdict)
    except ValueError as exc:
        return [] if _REFUSED_BODY in str(exc) else [f"raised: {exc}"]
    finally:
        BODY_REFUSAL_MODE = was
    return ["the whole-stage gate did not stop the plan"]


def _case_body_a_plan_whose_bodies_emit_is_untouched() -> list[str]:
    """SURVIVOR: every body emits, nothing is refused or moved."""
    plan = _body_gate_plan()
    plan["towns"]["vik"]["buildings"][0]["body"] = _GOOD_BODY
    plan["outliers"][0]["body"] = _GOOD_BODY
    plan["bodies"].pop(_REFUSED_BODY)
    body_gate(plan, _body_verdict)
    return [] if (not plan["refused_sites"] and len(plan["outliers"]) == 2
                  and "bodies_refused" not in plan) else [
        f"a clean plan was cut: {plan['refused_sites']}"]


# ---- PLACEMENT_GATE_DEPENDS_ON, neutered -----------------------------------
# CONSTRUCTED: one body that emits CLEAN at yaw 0 and at yaw 30 and is
# refused at yaw 80 for a roof hole - the shape MEASURED on Ulfsland for
# hs_blackforest_kin_01_woodhouse (clean at 0, hole 1.205 m at 80) - placed
# by one town at yaws 30, 80 and 80, beside a body that emits everywhere.
_YAW_BODY = "yaw_sensitive.blueprint"
_YAW_REFUSED = 80.0
_YAW_VERDICT = [
    {"override": "--allow-roof-skin-gaps", "code": "roof_skin_gap",
     "detail": "1 place(s) where this blueprint's roof skin jumps 1.179 m or "
               "more between adjacent 0.5 m columns with NOTHING IN THE GAP, "
               "worst 1.205 m, when emitted at yaw 80"}]


def _yaw_check(calls: list):
    def check(name: str, yaw: float = 0.0) -> dict:
        calls.append((name, yaw))
        bad = name == _YAW_BODY and yaw == _YAW_REFUSED
        return {"name": name, "ok": not bad, "commands": 0 if bad else 200,
                "missing": [], "refusals": _YAW_VERDICT if bad else [],
                "repaired": [], "repairs": [], "resolved": True,
                "reason": ("1 geometry preflight refusal(s): roof_skin_gap"
                           if bad else "emits 200 command(s)")}
    return check


def _yaw_plan() -> dict:
    pad = {"moved_m3": 10.0, "clamp_headroom_m": 5.0, "width_m": 10.0,
           "depth_m": 10.0, "clamp_ok": True}

    def b(i, body, yaw):
        return {"id": f"vik-house-{i}", "role": "house", "body": body,
                "yaw_deg": yaw, "pad": pad, "pieces": 216, "beds": 1,
                "stations": 0}
    houses = [b(1, _YAW_BODY, 30.0), b(2, _YAW_BODY, 80.0),
              b(3, _YAW_BODY, 80.0), b(4, _GOOD_BODY, 80.0)]
    square = {"moved_m3": 1.0, "width_m": 24.0, "depth_m": 24.0}
    town = {"buildings": houses, "square": square, "unplaced": []}
    town["totals"] = LY.TownPlanner.totals(
        [__import__("types").SimpleNamespace(**h) for h in houses], square, [])
    entry = {"pieces": 216, "missing_prefabs": [], "grounded": "x"}
    return {"towns": {"vik": town}, "outliers": [],
            "bodies": {_YAW_BODY: dict(entry, count=3),
                       _GOOD_BODY: dict(entry, count=1)},
            "refused_sites": []}


def _case_placement_a_body_clean_at_yaw_0_is_unplaced_at_the_yaw_it_is_refused(
        ) -> list[str]:
    """KNOWN-BAD, ULFSLAND STAGE 8: the body passes `body_gate`'s yaw-0
    question and the two buildings turned to 80 are unplaced with the
    emitter's refusal and its numbers; the yaw-30 sibling and the other body
    stand; the town's totals and the body's user count are re-taken; each
    distinct (body, yaw) is emitted ONCE."""
    plan = _yaw_plan()
    calls: list = []
    body_gate(plan, _yaw_check(calls))
    bad = []
    t = plan["towns"]["vik"]
    if [b["id"] for b in t["buildings"]] != ["vik-house-1", "vik-house-4"]:
        bad.append(f"survivors: {[b['id'] for b in t['buildings']]}")
    got = {u["id"]: u for u in t["unplaced"]}
    if sorted(got) != ["vik-house-2", "vik-house-3"]:
        bad.append(f"unplaced: {sorted(got)}")
    elif [r["code"] for r in got["vik-house-2"]["body_refused"]] != [
            "roof_skin_gap"] or "worst 1.205 m" not in (
            got["vik-house-2"]["body_refused"][0]["detail"]):
        bad.append(f"the refusal lost its numbers: {got['vik-house-2']}")
    if t["totals"]["buildings"] != 2 or t["totals"]["buildings_unplaced"] != 2:
        bad.append(f"town totals not re-taken: {t['totals']}")
    if plan["bodies"][_YAW_BODY]["count"] != 1:
        bad.append(f"user count not re-taken: {plan['bodies'][_YAW_BODY]}")
    pr = plan.get("placements_refused")
    if not pr or pr[0]["yaw_deg"] != _YAW_REFUSED or pr[0]["used_by"] != [
            "vik-house-2", "vik-house-3"]:
        bad.append(f"placements_refused: {pr}")
    if sorted(calls) != sorted([(_YAW_BODY, 0.0), (_GOOD_BODY, 0.0),
                                (_YAW_BODY, 30.0), (_YAW_BODY, 80.0),
                                (_GOOD_BODY, 80.0)]):
        bad.append(f"emissions asked: {sorted(calls)}")
    return bad


def _case_placement_NEUTER_body_only_the_yaw_80_buildings_are_PLACED(
        ) -> list[str]:
    """LOSS ARM: the shipped gate asked each body once, at yaw 0, and
    placed all four buildings - two of them under a roof hole."""
    global PLACEMENT_GATE_MODE
    was = PLACEMENT_GATE_MODE
    PLACEMENT_GATE_MODE = "body_only"
    try:
        plan = _yaw_plan()
        body_gate(plan, _yaw_check([]))
    finally:
        PLACEMENT_GATE_MODE = was
    t = plan["towns"]["vik"]
    return [] if (len(t["buildings"]) == 4 and not t["unplaced"]
                  and "placements_refused" not in plan) else [
        f"the body-only gate unplaced something: {t['unplaced']}"]


def _case_placement_a_plan_whose_placements_emit_is_untouched() -> list[str]:
    """SURVIVOR: no placement refused, nothing unplaced, no record added."""
    plan = _yaw_plan()
    for b in plan["towns"]["vik"]["buildings"]:
        b["yaw_deg"] = 30.0
    body_gate(plan, _yaw_check([]))
    t = plan["towns"]["vik"]
    return [] if (len(t["buildings"]) == 4 and not t["unplaced"]
                  and "placements_refused" not in plan
                  and plan["bodies"][_YAW_BODY]["count"] == 3) else [
        f"a clean placement was cut: {t['unplaced']}"]


BODY_GATE_CASES = (
    ("body_a_declared_site_whose_body_refuses_is_refused_and_named",
     _case_body_a_declared_site_whose_body_refuses_is_refused_and_named),
    ("body_NEUTER_whole_stage_one_body_kills_the_stage",
     _case_body_NEUTER_whole_stage_one_body_kills_the_stage),
    ("body_a_plan_whose_bodies_emit_is_untouched",
     _case_body_a_plan_whose_bodies_emit_is_untouched),
    ("placement_a_body_clean_at_yaw_0_is_unplaced_at_the_yaw_it_is_refused",
     _case_placement_a_body_clean_at_yaw_0_is_unplaced_at_the_yaw_it_is_refused),
    ("placement_NEUTER_body_only_the_yaw_80_buildings_are_PLACED",
     _case_placement_NEUTER_body_only_the_yaw_80_buildings_are_PLACED),
    ("placement_a_plan_whose_placements_emit_is_untouched",
     _case_placement_a_plan_whose_placements_emit_is_untouched),
)


# ---- SOLVE_WRITE_DEPENDS_ON, neutered in both directions -------------------
# The fixture seed has NO site set, NO placement set and NO geography anywhere
# in the tree -- the from-scratch state, constructed rather than borrowed.
_WRITE_FIXTURE_SEED = "RegenSelftestSeed"


def _write_fixture_plan(cited: Path) -> dict:
    """A solve of nothing, citing one artefact that declares the fixture seed
    -- the emission check refuses a document that cites nothing."""
    cited.write_text(f"seed: {_WRITE_FIXTURE_SEED}\n"
                     f"world: {_WRITE_FIXTURE_SEED}\n")
    return {"seed": _WRITE_FIXTURE_SEED, "seed_hash": 0,
            "world": _WRITE_FIXTURE_SEED, "patch_file": str(cited),
            "locations_dump": None, "towns": {}, "outliers": [],
            "bodies": {}, "totals": {}}


def _names_via_spec(seed_: str) -> tuple[str, str]:
    """THE DEFECT VERBATIM: the names asked of `roads/spec.py`, which loads
    the seed's node table -- its site set among it -- on import."""
    import importlib                                        # noqa: PLC0415
    saved = {k: sys.modules.pop(k) for k in ("spec", "roads.spec")
             if k in sys.modules}
    env = {k: os.environ.get(k) for k in ("VH_SEED", "VH_WORLD")}
    os.environ["VH_SEED"] = os.environ["VH_WORLD"] = seed_
    try:
        if str(HERE.parent) not in sys.path:
            sys.path.append(str(HERE.parent))
        spec = importlib.import_module("roads.spec")
        return spec.worldseed.plan_name(seed_), spec.worldseed.sites_name(seed_)
    finally:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in ("spec", "roads.spec"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)


def _names_without_the_seed(_seed: str) -> tuple[str, str]:
    """THE OTHER DIRECTION: one slot for every seed -- `plan.json` /
    `sites.yaml`, the names that once let a second seed destroy the first."""
    return "plan.json", "sites.yaml"


def _write(names=None) -> tuple[list[str], str]:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"
        plan = _write_fixture_plan(Path(td) / "field.yaml")
        try:
            write_solve(plan, out, names=names)
        except SystemExit as exc:
            return (sorted(p.name for p in out.iterdir()) if out.exists()
                    else []), str(exc)
        return sorted(p.name for p in out.iterdir()), ""


def _case_a_fresh_seed_solve_is_WRITTEN_under_the_readers_names() -> list[str]:
    """THE KNOWN-GOOD STATE FROM SCRATCH: no site set on disk, and the solve
    is written -- under exactly the names the readers resolve."""
    got, refused = _write()
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    want = sorted([worldseed.plan_path(_WRITE_FIXTURE_SEED).name,
                   worldseed.sites_path(_WRITE_FIXTURE_SEED).name])
    if refused:
        return [f"refused a fresh seed's write: {refused.splitlines()[0][:200]}"]
    return [] if got == want else [f"wrote {got}, the readers read {want}"]


def _case_NEUTER_names_via_spec_the_fresh_seed_solve_is_REFUSED() -> list[str]:
    """LOSS ARM, SUPERSET: names that depend on the node table refuse the
    one run that produces it, and nothing is written."""
    got, refused = _write(_names_via_spec)
    return [] if refused and not got else [
        f"the spec-named write was not refused (wrote {got})"]


def _case_NEUTER_names_drop_the_seed_the_solve_lands_in_the_DEFAULT_slot() -> list[str]:
    """LOSS ARM, SUBSET: names without the seed write a fresh seed's solve
    into the default seed's slot, which the reader check above refuses."""
    got, refused = _write(_names_without_the_seed)
    return [] if not refused and got == ["plan.json", "sites.yaml"] else [
        f"the seedless write gave {got} / refused={bool(refused)}"]


SOLVE_WRITE_CASES = (
    ("a_fresh_seed_solve_is_WRITTEN_under_the_readers_names",
     _case_a_fresh_seed_solve_is_WRITTEN_under_the_readers_names),
    ("NEUTER_names_via_spec_the_fresh_seed_solve_is_REFUSED",
     _case_NEUTER_names_via_spec_the_fresh_seed_solve_is_REFUSED),
    ("NEUTER_names_drop_the_seed_the_solve_lands_in_the_DEFAULT_slot",
     _case_NEUTER_names_drop_the_seed_the_solve_lands_in_the_DEFAULT_slot),
)


_CENSUS_DOC = {"bodies": [
    {"name": "census-S11-001", "x": 100.0, "z": 200.0, "declared_m": 8.0,
     "blocking": 12, "prefabs": {"woodwall": 9, "wood_floor": 3}},
    {"name": "census-S11-002", "x": 100.04, "z": 200.01, "declared_m": 8.0,
     "blocking": 12, "prefabs": {"woodwall": 9}},
    {"name": "census-S4-nature", "x": 400.0, "z": 400.0, "declared_m": 5.0,
     "blocking": 0, "prefabs": {"shrub_2": 4}}]}
_CENSUS_UNITS = [{"id": "fx-lh", "x": 120.0, "z": 200.0, "pad_w": 20.0,
                  "pad_d": 20.0}]


def _case_census_a_pad_whose_cylinder_meets_a_blocking_body_is_not_legal(
        ) -> list[str]:
    ev = CensusEvidence.from_docs([_CENSUS_DOC])
    bad = []
    if len(ev.bodies) != 1:
        bad.append(f"the same body read twice (two overlapping segments) "
                   f"and a body with nothing blocking must leave ONE body, "
                   f"got {[b['name'] for b in ev.bodies]}")
    got = census_refusals(ev, _CENSUS_UNITS, lambda u: 14.0)
    if len(got) != 1 or got[0]["blockers"][0]["dist_m"] != 20.0 \
            or got[0]["blockers"][0]["reach_m"] != 22.0:
        bad.append(f"the unit 20 m from a body of radius 8 with a 14 m "
                   f"cylinder must be refused at 20.0 against 22.0: {got}")
    far = [dict(_CENSUS_UNITS[0], x=160.0)]
    if census_refusals(ev, far, lambda u: 14.0):
        bad.append("a pad 60 m off the body was refused")
    return bad


def _case_census_NEUTER_no_evidence_the_first_solve_refuses_nothing(
        ) -> list[str]:
    return [] if not census_refusals(None, _CENSUS_UNITS, lambda u: 14.0) \
        else ["no evidence still refused a pad"]


def _case_census_an_empty_census_is_REFUSED_not_read_as_clear() -> list[str]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        try:
            CensusEvidence.load("NoSuchSeed", Path(td))
        except SystemExit as e:
            return [] if "no stage-10 census" in str(e) else [str(e)]
    return ["a missing census was read as 'nothing blocks'"]


CENSUS_CASES = (
    ("census_a_pad_whose_cylinder_meets_a_blocking_body_is_not_legal",
     _case_census_a_pad_whose_cylinder_meets_a_blocking_body_is_not_legal),
    ("census_NEUTER_no_evidence_the_first_solve_refuses_nothing",
     _case_census_NEUTER_no_evidence_the_first_solve_refuses_nothing),
    ("census_an_empty_census_is_REFUSED_not_read_as_clear",
     _case_census_an_empty_census_is_REFUSED_not_read_as_clear),
)


def selftest() -> int:
    bad_total = 0
    for title, cases in (("census evidence", CENSUS_CASES),
                         ("site bill surface", SITE_BILL_CASES),
                         ("site claim", SITE_CLAIM_CASES),
                         ("clearing gate", CLEARING_GATE_CASES),
                         ("body gate", BODY_GATE_CASES),
                         ("solve write", SOLVE_WRITE_CASES)):
        bad = 0
        for name, fn in cases:
            try:
                problems = fn()
            except BaseException as e:                     # noqa: BLE001
                problems = [f"raised {type(e).__name__}: {e}"]
            bad += bool(problems)
            print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
            for p in problems:
                print(f"          {p}")
        print(f"\n  {title} selftest {len(cases) - bad}/{len(cases)}  "
              f"{'PASS' if not bad else 'FAIL'}\n")
        bad_total += bad
    return 0 if not bad_total else 1


def site_keepout_m(plan: dict, site_id: str) -> float:
    """An outlying site's published `pad_radius_m`: the farthest corner of
    any pad the writer stamps for it (`build.unit_records`), from the site's
    centre, plus the 2.0 m margin this document has always carried.  For one
    body at yaw 0 that IS `pad_m * sqrt(2) / 2 + 2.0`, byte for byte.  For a
    cluster it is not: MEASURED on Fresh42, a treehouse cluster published
    13.3 m -- ONE 16 m pad -- while its written members reach 15.3 m, so a
    ribbon terminating on that ring (roads/network.py adds its level half to
    it) could level a member's pad corner."""
    rec = next(r for r in plan["outliers"] if r["id"] == site_id)
    far = max(math.hypot(abs(float(u["x"]) - rec["x"]) + float(u["pad_w"]) / 2,
                         abs(float(u["z"]) - rec["z"]) + float(u["pad_d"]) / 2)
              for u in _writer().unit_records(plan, site_id))
    return round(far + 2.0, 1)


def sites_yaml(plan: dict) -> str:
    """The interchange `RoadNet` asked for, in the shape it specified.

    `pad_radius_m` is the radius a ribbon must stay OUTSIDE and terminate at,
    taken from the district, or from every pad the writer stamps for the
    site plus its own margin (`site_keepout_m`).
    `approach_bearing_deg` is given ONLY where the layout forces one -- a gate
    or a downhill castle approach -- and omitted otherwise, because RoadNet has
    the height field and picking the lowest-gradient approach there is strictly
    better than guessing here.
    """
    import yaml as _yaml
    recs = []
    for name, t in plan["towns"].items():
        main = t["streets"][0]
        recs.append({
            # `type` is the town's own authored kind, carried through from
            # TOWNS.  It was `"town" if name == "stenvik" else "village"`,
            # which is Ulfsland's cast list deciding a record's type for
            # every seed that ever runs this emitter.
            "id": name, "type": t["type"],
            "xz": [t["centre"][0], t["centre"][1]],
            "pad_radius_m": 100.0, "road_priority": t["road_priority"],
            "approach_bearing_deg": round((main["bearing_deg"] + 180) % 360, 1),
            "portal_tag": t["tag"], "provisional": False,
            "pad_y": t["pad_y"], "patch": t["patch"],
            # WHICH SURFACE `pad_y` IS A HEIGHT ON.  A town's is its square's
            # median on the finding field: the square is not a written pad.
            "pad_y_surface": t["square"].get("surface"),
            "buildings": t["totals"]["buildings"],
            "pieces": t["totals"]["pieces_total"],
            "earthwork_m3": t["totals"]["earthwork_m3"],
            "clamp_headroom_min_m": t["totals"]["clamp_headroom_min_m"],
            "streets": [{"name": s["name"], "bearing_deg": s["bearing_deg"],
                         "length_m": s["profile"]["length_m"],
                         "max_grade_8m": s["profile"]["max_grade"],
                         "max_step_1m_m": s["profile"]["max_step_m"],
                         "walkable": s["walkable"]} for s in t["streets"]],
            "gate": t["gate"],
            "dist_navigable_water_m": t["dist_navigable_water_m"],
            "dist_spawn_m": t["dist_spawn_m"],
            "same_landmass_as_spawn": t["island"]["same_landmass_as_spawn"],
            # WHETHER A ROAD REACHES IT, measured (road_reach.py): the one
            # statement `roads/spec.py` builds the site's legs from.
            "road": t["road"],
            "why": t["why"],
        })
    for r in plan["outliers"]:
        rec = {
            "id": r["id"], "type": r["kind"], "xz": [r["x"], r["z"]],
            "pad_radius_m": site_keepout_m(plan, r["id"]),
            "road_priority": r["road_priority"], "portal_tag": r["tag"],
            "provisional": False, "pad_y": r["pad"]["target_y"],
            "pad_y_surface": r["pad"].get("surface"),
            "patch": r["patch"], "body": r["body"],
            "pieces": r["pieces"] * r["cluster"], "cluster": r["cluster"],
            "earthwork_m3": round(r["pad"]["moved_m3"], 1),
            "clamp_headroom_m": r["pad"]["clamp_headroom_m"],
            "rise_160m_m": r["rise_160m_m"],
            "dist_navigable_water_m": r["dist_navigable_water_m"],
            "dist_spawn_m": r["dist_spawn_m"],
            "same_landmass_as_spawn": r["island"]["same_landmass_as_spawn"],
            "road": r["road"],
            "why": r["why"],
        }
        if r["kind"] == "castle":
            rec["approach_bearing_deg"] = None
            rec["approach_note"] = ("gate faces downhill; RoadNet's switchback "
                                    "may terminate below the summit with a "
                                    "portal rather than reaching the door")
        recs.append(rec)
    # THE HUMAN-DECLARED SITES, MEASURED (`declared_harbours`).  Their own key,
    # not `sites`: a declared site is a ROAD NODE with a district and its own
    # builder, not a solved town or outlier, and every reader of `sites` --
    # the gate's census, `build.unit_records` -- counts solver sites.  Written
    # only when a seed declares any, so a seed without one is byte-identical.
    declared = [{
        "id": d["id"], "type": d["kind"], "node": d["node"],
        "requested_xz": d["requested_xz"], "xz": d["xz"],
        "pad_radius_m": d["pad_radius_m"], "district_m": d["district_m"],
        "road_priority": d["road_priority"], "road_to": d["road_to"],
        "patch": d["patch"], "pad_y": d["pad_y"],
        # THE LANDING THE ROADS ARE ROUTED TO AND THE BUILDER LEVELS: one
        # rectangle, one datum (`pad_y` IS its target), published once so a
        # road arrives at what is built and not at a disc with nothing in it.
        "landing_pad": d["landing_pad"],
        "same_landmass_as_spawn": d["island"]["same_landmass_as_spawn"],
        "measured": d["measured"], "why": d["why"],
    } for d in plan.get("declared_sites", ())]
    doc = {
        # `world` was the literal "Ulfsland" and `seed`/`seed_hash` came from a
        # dict that hardcoded Pirate68, so this header was a constant wearing
        # the shape of provenance.  All three now come from `scope_of`, which
        # reads them off the height field the solve actually ran on.
        "world": plan["world"], "seed": plan["seed"],
        "seed_hash": plan["seed_hash"],
        "owner": "Settlements",
        "provenance": {
            "height_field": plan["patch_file"],
            "height_field_note": "PatchScan 1 m with rivers; water plane y=30 "
                                 "(MEASURED ZoneSystem::c_WaterLevel)",
            "locations_dump": plan["locations_dump"],
            "clamp_m": T.CLAMP_M,
            "scripts": ["sites.py", "layout.py", "clearance.py", "terrain1m.py",
                        "bodies.py", "spawnable.py", "plan.py"],
        },
        "totals": plan["totals"],
        "sites": recs,
    }
    if declared:
        doc["declared_sites"] = declared
    return _yaml.safe_dump(doc, sort_keys=False, width=10 ** 9)


# SOLVE_WRITE_DEPENDS_ON = ("seed",): WHICH FILES A SOLVE IS WRITTEN TO.
#
# The two names are a function of the seed and nothing else, and they are
# asked of `worldseed`, which is where the readers ask too.  They were asked
# of `roads/spec.py`, and IMPORTING spec loads the road network's node table:
# this seed's solved site set -- THIS FUNCTION'S OWN OUTPUT -- and its solved
# installations and geography, each refused when absent.  MEASURED 2026-09-24
# on Fresh42 from scratch: the solve ran 157.2 s, then `from roads import
# spec` refused `settlements/sites.Fresh42.yaml is missing`, and the solve was
# thrown away.  A producer that can only name its output once its output
# exists cannot run on a fresh seed; every earlier drive had a stale copy on
# disk.  `names` is the one seam the selftest breaks, in both directions.
def _solve_names(seed_: str) -> tuple[str, str]:
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import worldseed                                        # noqa: PLC0415
    return worldseed.plan_name(seed_), worldseed.sites_name(seed_)


def write_solve(plan: dict, out: Path, names=None) -> tuple[Path, Path]:
    """Emission-scope-check and write the plan and the site set; return both
    paths.  REFUSES (SystemExit) a document that is not about its own seed."""
    # MAKE THE OUTPUT DIRECTORY, BEFORE THE SOLVE IS THROWN AWAY.  MEASURED
    # 2026-09-23 driving seed Fresh42: `--out` pointed at a directory that did
    # not exist, the solve ran for 181.4 s, passed its own emission scope check,
    # and then died in `write_text` with FileNotFoundError -- every number lost,
    # nothing on disk, exit 1.  `--out` defaults to this package's own directory
    # and so was never absent on the path anyone ran; a fresh seed's output
    # directory is new BY DEFINITION.
    out.mkdir(parents=True, exist_ok=True)
    # READ THE OUTPUT BACK, PARSED, BEFORE IT IS A FILE.
    #
    # `worldseed.require_scope` refuses a foreign artefact on the way IN and
    # this solve's inputs are guarded that way.  A run can consume ONLY ITS OWN
    # INPUTS and still emit an output pinned to another world, because nothing
    # reads the output back -- MEASURED 2026-09-22 on seed Fresh42, where a
    # road plan stamped `seed: Fresh42 / world: Fresh42` and exited 0 with 59%
    # of its paved metreage fitted to Ulfsland building coordinates.  These two
    # documents are that plan's interchange, so they are checked the same way:
    # the header must be this run's, and every artefact they CITE
    # (`provenance.height_field`, `provenance.locations_dump`) must itself be
    # in scope for the seed they declare.
    import emitscope                                        # noqa: PLC0415
    import yaml as _yaml                                    # noqa: PLC0415
    # THE NAME IS DECIDED BY THE SEED, NOT BY THIS FUNCTION.  Both documents
    # come out of ONE solve and both are read by name later; `sites.yaml` was
    # given a per-seed slot and `plan.json` was not, so a second seed's solve
    # destroyed the first's plan while its site set survived.
    plan_name, sites_name = (names or _solve_names)(str(plan["seed"]))
    plan_text = json.dumps(plan, indent=1, default=str)
    sites_text = sites_yaml(plan)
    emitscope.require_emission_scope(
        f"settlements/{plan_name}", out / plan_name, json.loads(plan_text),
        seed=plan["seed"], world=plan.get("world"), root=HERE.parent)
    emitscope.require_emission_scope(
        f"settlements/{sites_name}", out / sites_name,
        _yaml.safe_load(sites_text),
        seed=plan["seed"], world=plan.get("world"), root=HERE.parent)
    (out / plan_name).write_text(plan_text)
    (out / sites_name).write_text(sites_text)
    print(f"wrote {out / plan_name}\nwrote {out / sites_name}", file=sys.stderr)
    return out / plan_name, out / sites_name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default=PATCHES)
    ap.add_argument("--locations", default=None,
                    help="the generator location dump; default THIS seed's "
                         "(clearance.location_dump, resolved at call time)")
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--no-interior", action="store_true")
    ap.add_argument("--census", action="store_true",
                    help="RE-SOLVE with the stage-10 census as evidence "
                         "(roads/census/<seed>-*.json): a pad whose clearing "
                         "cylinder meets a blocking structure body is not "
                         "legal and the next legal candidate is taken. Refuses "
                         "when no census exists; the first solve omits it")
    ap.add_argument(
        "--expect-towns", action="append", default=[], metavar="ID=X,Z",
        help="DECLARE a town centre for this solve, e.g. "
             "--expect-towns stenvik=496,904. The plan REFUSES to run if the "
             "district search does not return a candidate there: a plan that "
             "silently relocates a town is worse than one that stops. Omit it "
             "and the towns are the search's own top-ranked districts on "
             "this seed's land.")
    ap.add_argument(
        "--expect-sites", action="append", default=[], metavar="ID=X,Z[,PATCH]",
        help="DECLARE an outlying site for this solve, e.g. "
             "--expect-sites wt-spawn=110,-104. Same rule as --expect-towns: "
             "declared sites are taken where they are declared, and everything "
             "undeclared is the search's own top-ranked candidate of its kind "
             "on this seed's land. The window is measured from the coordinate "
             "unless it is given.")
    ap.add_argument(
        "--engine-zones", default=None, metavar="DIR",
        help="where the per-zone PatchScan reads of the engine surface "
             f"({SF.SITE_BILL_SURFACE}) are cached and fetched to. Default: an "
             "`engine_zones/` directory beside --patches, i.e. with the field "
             "of the same seed. Zones not there are scanned on demand; each "
             "file's sidecar declares its seed and surface.")
    a = ap.parse_args()
    a.locations = a.locations or clearance.location_dump()
    scope = scope_of(a.patches, a.locations)
    surface = SF.EngineSurface(
        scope["seed"], SF.SITE_BILL_SURFACE,
        cache_dir=(Path(a.engine_zones) if a.engine_zones
                   else Path(a.patches).resolve().parent / "engine_zones"),
        fetch=True)
    # THE WORLD WHOSE BUILDINGS THE SETTLEMENTS KEEP OFF, and it is THIS one.
    # `Picker` read `worlds/Ulfsland` for every seed (sites.py,
    # SITE_INSTALLATIONS_DEPENDS_ON); `scope["world"]` is None unless the
    # world is actually known, and `installations_dir` refuses on None.
    picker = S.Picker(a.patches, a.locations,
                      S.installations_dir(scope["world"]),
                      seed_=scope["seed"], world_=scope["world"])
    print(f"installations kept off: {len(picker.installations)} solved "
          f"placement(s) of world {scope['world']!r} under {picker.world_dir}",
          file=sys.stderr)
    plan = build(picker, run_interior=not a.no_interior,
                 expect_towns=parse_town_declaration(a.expect_towns),
                 expect_sites=parse_site_declaration(a.expect_sites),
                 surface=surface,
                 evidence=(CensusEvidence.load(scope["seed"])
                           if a.census else None))
    write_solve(plan, Path(a.out))
    print(json.dumps(plan["totals"], indent=1))
    # THE STAGE'S REPORT NAMES EVERY REFUSED SITE, with its body and numbers;
    # the rest of the plan is written and the stage exits 0.
    for s in plan.get("refused_sites", ()):
        print(f"REFUSED site {s['site']} ({s['kind']}, "
              f"{'declared' if s['declared'] else 'searched'}): {s['why']}")
    print("\nbodies used:")
    for name, e in plan["bodies"].items():
        ind = e.get("interior", {})
        print(f"  {name[:52]:54s} x{e['count']:2d} pcs={e['pieces']:5d} "
              f"{e['grounded']:16s} float={e['floating_fraction']:.2f} "
              f"indoor={ind.get('indoor_covered_m2', 'n/a')!s:>7s} "
              f"npc={ind.get('npc_ok')}")
    if plan.get("declared_sites"):
        print("\ndeclared sites (worldseed.HUMAN_SITES), measured:")
        for d in plan["declared_sites"]:
            m = d["measured"]
            print(f"  {d['id']:26s} {d['kind']:16s} request "
                  f"({d['requested_xz'][0]:g}, {d['requested_xz'][1]:g}) "
                  f"{m['request_water_depth_m']:.2f} m under the sea -> node "
                  f"{d['node']} ({d['xz'][0]:g}, {d['xz'][1]:g}), "
                  f"{m['landing_gap_m']:g} m away, disc {d['pad_radius_m']:g} m, "
                  f"district {d['district_m']:g} m, "
                  f"same_landmass_as_spawn={d['island']['same_landmass_as_spawn']}")
    print("\noutliers:")
    for r in plan["outliers"]:
        print(f"  {r['id']:16s} {r['kind']:11s} ({r['x']:7.1f},{r['z']:7.1f}) "
              f"pad {r['pad']['width_m']:4.0f} y={r['pad']['target_y']:7.2f} "
              f"moved={r['pad']['moved_m3']:7.1f} head={r['pad']['clamp_headroom_m']:5.2f} "
              f"rise={r['rise_160m_m']:6.1f} ocean={r['dist_navigable_water_m']:6.1f} "
              f"island_same_as_spawn={r['island']['same_landmass_as_spawn']} "
              f"poi_violations={len(r['poi_violations'])}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(selftest())
    raise SystemExit(main())
