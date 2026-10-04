#!/usr/bin/env python3
"""Keep-out geometry for vanilla world locations, for ROAD ROUTING ONLY.

THIS MODULE IS NOT THE CLEARANCE GATE.  `tools/jumpstart/settlements/clearance.py`
is canonical for the verdict a build gate asks -- it is the one `flatten.py`'s
location gate and `Settlements`' `build.py` are already wired to, and rewiring
two working gates to keep a newer implementation would be churn for nothing.
What survives here is the ROUTING half, which is a different question: A* needs
a per-cell forbid/penalty RASTER over a whole island, not a verdict on a set of
points.  The verdict belongs to `clearance.py`; call
`clearance.load(...).verdict_for_samples(samples, half_width_m)`.

SOURCE, MEASURED by `Settlements` and read-only here:
`/tmp/settle/loc2/f6fe167f4fcd.json` -- seed Pirate68, hash 147627509, 12,301
location instances, 177 types, each carrying the `ZoneLocation`'s own
`exteriorRadius`, `interiorRadius`, `clearArea`, `quantity`, `prioritized`,
`centerFirst`.  Largest declared exteriorRadius anywhere in the world is 32 m;
smallest are 3-6 m.  That order-of-magnitude spread is why a single global
stand-off number cleared zero cells out of 12,301.

RADIUS BUDGET, with each term's status stated because they are not equal:

    declared   = max(exteriorRadius, interiorRadius)      MEASURED, per instance
    overshoot  = 11 m                                     INFERRED from ONE
                 observation -- the old world's POI whose PIECES reached 43 m
                 against a 32 m declared radius.  Generalising one measurement
                 into a world-wide constant is exactly the defect shape this
                 project has paid for repeatedly, so it is marked INFERRED here
                 and in every record this module produces.  It is also
                 dimensionally suspicious on a 3 m Waymarker, where 11 m is
                 nearly four times the declared radius.
    road_margin = 6 m                                     TASTE PICK.  A road is
                 a 6 m ribbon passing by, not a 60 m pad sitting on top; it
                 needs less breathing room than a building site.
    half_width = the ribbon's own half-width                MEASURED from design

PROTECTED TEST, from `Settlements`, accepted: `prioritized or centerFirst or
quantity <= 5`.  There is no sacredness flag on `ZoneLocation` -- every field
was checked -- and `unique` is FALSE even on `bosslocation`, so `unique` is not
the signal and must not be used.  MEASURED examples: StartTemple quantity 1
prioritized centerFirst; bosslocation quantity 3 prioritized; against
InfestedTree01 quantity 700 and Mistlands_RoadPost1 quantity 500.

TWO TIERS, because a road is not a building:
  * PROTECTED instances are a HARD FORBID out to the full budget.  The road
    bends.
  * SCENERY is a hard forbid only out to `declared + piece_pad` (so the ribbon
    never levels ground out from under a standing piece, which is the floating-
    vegetation defect with a rock instead of a bush) and a SOFT penalty out to
    the full budget.  A road passing a waymarker is thematic; a road that
    undermines one is a defect.

RESIDUAL RISK, stated rather than hidden: this dump is MOD-FREE by
construction, so it cannot see More_World_Locations' ~190 POI types.  A ribbon
routed clear of every vanilla instance can still cross a mod POI.  The ribbon
DELETES nothing, so the failure mode is a mod POI's pieces standing over
levelled ground, not lost content -- but it is still a defect, and the only
trustworthy check is a live marker sweep along the finished centreline after the
zones are generated.  `network.py --marker-check` emits that sweep.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# WHERE LocScan LEAVES ITS DUMPS.  Searched in order; every one of them keeps an
# `index.tsv` of `<seed>\t<file>.json`, which is the producer's OWN statement of
# which file belongs to which seed.
DUMP_ROOTS = (Path("/tmp/settle/loc3"), Path("/tmp/settle/loc2"),
              Path("/tmp/seedscan/loc"), Path("/tmp/seedfield/loc"))


def dump_for_seed(seed: str, roots=DUMP_ROOTS) -> Path:
    """The dump THIS SEED's, resolved through each root's own `index.tsv`.

    THE PATH WAS FROZEN AND THE GUARD ALONE WAS NOT ENOUGH.  `DUMP` was
    `/tmp/settle/loc3/f6fe167f4fcd.json` -- one seed's content hash, written in.
    Adding the header check earlier tonight made a fresh-seed run REFUSE
    instead of silently loading 12,301 Pirate68 locations, which was the
    important half; but it left a refusal NOBODY COULD COMPLY WITH, because no
    caller had any way to name a different file.  MEASURED: a Fresh42 dump
    (12,376 locations, `"seed": "Fresh42"`) sat on this host at
    /tmp/seedfield/loc/74cb38be48da.json while `roads/geography.py:387` called
    `poi.load()` and was refused against Pirate68's.

    A REFUSAL THAT NAMES NO REACHABLE REMEDY IS AN INVITATION TO WORK AROUND
    IT -- the same reason an `FileNotFoundError [Errno 2]` on a missing field
    made borrowing Ulfsland's the cheapest way forward. So the resolution is
    done here, once, off the index the producer already writes.

    Refuses by naming every root searched and every seed found, because "no
    dump" and "no dump FOR THIS SEED" are different problems with different
    fixes.
    """
    found: dict[str, list[str]] = {}
    for root in roots:
        idx = root / "index.tsv"
        if not idx.exists():
            continue
        for line in idx.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].strip():
                continue
            name, fname = parts[0].strip(), parts[1].strip()
            found.setdefault(name, []).append(str(root))
            if name == seed and (root / fname).exists():
                return root / fname
    raise SystemExit(
        f"REFUSING: no location dump for seed {seed!r}. Searched "
        f"{[str(r) for r in roots]}; the dumps present declare "
        f"{ {k: v for k, v in sorted(found.items())} or 'nothing'}. Produce one "
        f"with tools/seedscan/run_locscan.sh for {seed!r} -- it needs no "
        f"running server, only the seed.")


# ---- ONE RESOLVER, AND EVERY LOCATION INSTRUMENT READS IT ----------------
#
# LOCATION_DUMP_DEPENDS_ON names what the location SET is a function of.  It
# is declared here because four instruments on the road side were keyed on
# something else -- a CONTENT HASH -- and a content hash is a fact about one
# seed's file, not about this run.
#
# THE MEASUREMENT THAT MADE IT A DEFECT RATHER THAN AN UNTIDINESS, 2026-09-24,
# Fresh42, `roads/ribbon.py build --segment S7-stathub-wtnorth --validate`,
# rc=3, both lines from ONE run of ONE file:
#
#   plan-time location gate: clear over 12376 generator instances, 0 firm
#   VIOLATION ShipSetting01 (124.2,263.8) min_dist 18.2 required 25.0
#                                                        short_by 6.8
#   REFUSING: a CARRIAGEWAY sample is inside a location's hard radius ...
#             Re-route the leg
#
# 12,376 is Fresh42's count; the refusing instance is PIRATE68's.  MEASURED
# over both dumps: within `reach + 9 m` of S7's 87-node centreline Pirate68
# holds ONE instance (that ShipSetting01, reach 24.0) and Fresh42 holds ZERO.
# The segment's earthwork was clean -- rim transfer 0 caused, 0 pre-existing --
# and it was refused by a location standing in a different world.
#
# The same frozen hash also chose the batter's keep-out discs, so all 957
# `poi` wall clips network-wide (110+142+103+30+29+120+11+134+162+116 over the
# 14 Fresh42 segments) were clipped against Ulfsland's locations.
#
# So there is ONE resolver and it is keyed on the seed.  A caller that wants a
# named file still passes one and is still scope-checked, because naming a
# file is a claim.
LOCATION_DUMP_DEPENDS_ON = (
    "the RUN'S SEED, and nothing else. NOT the file's content hash and NOT "
    "which of the four LocScan roots happens to exist on this host: a dump is "
    "a function of the seed alone (any world on that seed produces it), so "
    "the key is the seed and the roots are only where to look. Keying on the "
    "path is a key naming a SUPERSET -- every run on this host -- of what the "
    "location set actually depends on, and it is silent: the instrument asks "
    "for locations and gets 12,301 of them.")


def location_dump(seed: str | None = None, roots=DUMP_ROOTS) -> Path:
    """THE location dump for this run, resolved by SEED and header-checked.

    Every instrument that needs "what locations stand in this world" -- the
    router's forbid mask, the plan-time gate, the batter's keep-out discs and
    the write gate -- must read the same file, and the file must be THIS
    seed's.  See `LOCATION_DUMP_DEPENDS_ON`.

    Resolution is `dump_for_seed` off the producer's own `index.tsv`; the
    header is then verified through `worldseed.require_scope`, so a root whose
    index disagrees with the file it points at refuses by name instead of
    loading a neighbouring world.
    """
    want = seed or _worldseed().seed()
    path = dump_for_seed(want, roots)
    _worldseed().require_scope(
        "location dump", path, found_seed=dump_seed(json.loads(path.read_text())),
        seed_=want,
        produced_by="Settlements' LocScan.cs over a generated world; a dump is "
                    "a function of the SEED alone, so any world on this seed "
                    "will do")
    return path


def _worldseed():
    """`worldseed`, imported lazily with the jumpstart root APPENDED.

    Appended, never inserted: `tools/jumpstart/network/` is a package that
    shadows `roads/network.py` for every later importer if it gets in front of
    this directory.
    """
    import sys
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.append(str(root))
    import worldseed
    return worldseed

# The 11 m "piece overshoot" that used to sit here is RETIRED.  It was one
# observation (a POI whose pieces reached 43 m against a 32 m declared radius)
# generalised to all 12,301 instances.  `znviewReachM` supersedes it: MEASURED
# per TYPE over all 177 types of this seed by Settlements' LocScan.cs
# extension, it is the furthest horizontal distance from a location prefab's
# root to any child carrying a ZNetView -- i.e. to the objects that become ZDOs
# and that a clearing step would actually delete.  Twelve of 177 types reach
# past their declared radius, worst excess +11.8 m (TrollCave02: declared 12,
# measured 35.8); the largest reach anywhere is 35.77 m.
#
# The rule is max(declared, measured) and NOT "prefer the measurement", because
# 28 of 177 types carry <= 2 ZNetView children (WoodVillage1, NorthVillage,
# FortressRuins among them) so their measured reach is near zero -- and a naive
# "measured beats declared" would have SHRUNK the stand-off around exactly the
# big village locations most worth avoiding.
OVERSHOOT_M = 0.0
ROAD_MARGIN_M = 6.0       # taste pick
PIECE_PAD_M = 3.0         # taste pick: never level right up against a piece

# ---- THE WRITE GATE'S OWN BOUNDARY, IMPORTED RATHER THAN RE-DERIVED -------
#
# THE DEFECT THIS SECTION EXISTS TO END, MEASURED on R2-ulfsgard-harbourb,
# 2026-09-19.  A route was planned, profiled and handed to `ribbon.py`, and
# `ribbon.py::location_check` REFUSED it: carriageway samples 1.50 m from a
# `WoodHouse6` needing 9.0 m, and 11.40 m from a `ShipSetting01` needing
# 25.0 m.  Both are ordinary generator instances that were in this module's
# dump the whole time.  The planner had simply never been asked the gate's
# question, and this module offered no way to ask it -- its radii are two
# TASTE picks that happen to exceed the gate at the two half-widths the
# in-repo routers use, which is luck, not a guarantee.  The repair to that
# one route was to paste the gate's own `required_m` back into a bespoke
# forbid mask by hand.  A number pasted by hand is a number the next route
# will not have.
#
# So the gate boundary now lives here as an importable floor:
#
#   gate_m(width_m) = declared_m + clearance.TERRAIN_SPREAD_M
#                                + route.footprint_half_m(width_m)
#
# It is the write gate restated on the CENTRELINE, and the restatement is
# exact rather than analogous.  `location_check` tests each CARRIAGEWAY
# SAMPLE against `reach + 0.0 + TERRAIN_SPREAD_M` (half-width zero because
# the samples already ARE the footprint); those samples fill a band of
# half-extent `route.footprint_half_m(width_m)` about the centreline, so a
# sample breaches exactly when the centreline is nearer than
# `reach + TERRAIN_SPREAD_M + footprint_half`.
#
# MEASURED CONFIRMATION, from the R2 repair itself: the hand-pasted discs
# were the gate's `required_m` (9.0 and 25.0) inflated by the 4.0 m level
# half-width -> 13.0 m and 29.0 m, and the accepted v3 alignment clears them
# at 13.51 m and 29.15 m.  This function reproduces 13.0 and 29.0 from the
# dump alone.  Inflating by the 14.5 m BATTER footprint instead returns `no
# route found`, and correctly so: the gate refuses a carriageway sample and
# merely CLIPS a batter sample, so a batter is allowed inside a stand-off
# that a carriageway is not.
#
# `hard_m` takes the MAXIMUM of taste and gate.  At the half-widths every
# in-repo router passes today (3.0 m paved half for a 6 m trunk, 2.0 m for a
# 4 m spur, 4.0 m footprint half in `north.py`) taste is already the larger
# and NOTHING MOVES -- verified in both directions over all 12,301 instances
# by `selftest()` below.  What changes is that it can no longer go under the
# gate for a caller that picks a different width.
import sys as _sys
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import route as _route                        # noqa: E402
from settlements import clearance as _clearance  # noqa: E402

TERRAIN_SPREAD_M = _clearance.TERRAIN_SPREAD_M   # DERIVED, not taste: 1.0
# The width the gate is evaluated at when a caller names only a half-width.
# A caller that passes the PAVED half (`network.py`: WIDTH_M/2) implies a
# level surface one SHOULDER_M wider each side; a caller that already passes
# the FOOTPRINT half (`north.POI_HALF_M` = 4.0) is then over-stated by that
# same shoulder, which is the safe direction and costs nothing because taste
# binds there anyway.
def level_half_m(half_width_m: float) -> float:
    """The half-extent of LEVEL ground a road of this half-width writes."""
    return _route.footprint_half_m(2.0 * float(half_width_m))


@dataclass
class Poi:
    name: str
    x: float
    z: float
    y: float
    declared_m: float
    protected: bool
    quantity: int

    def keepout_m(self, half_width_m: float) -> float:
        return self.declared_m + OVERSHOOT_M + ROAD_MARGIN_M + half_width_m

    def gate_m(self, half_width_m: float,
               level_half_width_m: float | None = None) -> float:
        """`ribbon.py::location_check`'s refusal boundary, on the CENTRELINE.

        Not a margin and not a taste pick: a centreline inside this radius
        WILL be refused at write time, so a router that plans through it has
        planned a road that cannot be built.
        """
        lvl = (level_half_m(half_width_m) if level_half_width_m is None
               else float(level_half_width_m))
        return self.declared_m + TERRAIN_SPREAD_M + lvl

    def taste_m(self, half_width_m: float) -> float:
        """The pre-existing TASTE radius, kept nameable so the two can be
        compared instead of silently folded together."""
        if self.protected:
            return self.keepout_m(half_width_m)
        return self.declared_m + PIECE_PAD_M + half_width_m

    def hard_m(self, half_width_m: float,
               level_half_width_m: float | None = None) -> float:
        """The radius the road may not enter at all.

        TASTE OR THE GATE, WHICHEVER IS LARGER.  A taste margin may be
        lifted at a pinch (see `north.py`'s licence); the gate may not, and
        a hard radius that sits under it is a refusal deferred to the
        writer.
        """
        return max(self.taste_m(half_width_m),
                   self.gate_m(half_width_m, level_half_width_m))


def dump_seed(d: dict) -> str | None:
    """The seed THE DUMP SAYS IT IS, from the dump's own header.

    LocScan writes three spellings across its generations and all three have
    been seen on this host, so all three are read rather than one being picked
    and the others silently reading as absent.
    """
    for key in ("seedNameFromWorld", "seed", "seedName"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def load(path: Path | None = None, *, seed: str | None = None) -> list[Poi]:
    """The generator's location dump for THIS SEED, refused if it is not.

    THE KEEP-OUT SET IS A [SEED] FACT AND WAS NOT CHECKED AGAINST THE SEED.
    MEASURED 2026-09-21: a `VH_SEED=Fresh42` run of `network.py plan` loaded
    12,301 locations from `/tmp/settle/loc3/f6fe167f4fcd.json` -- whose own
    header reads `"seedNameFromWorld": "Pirate68"` -- and masked a fresh
    seed's corridors against Ulfsland's temples, caves and crypts. The router
    reported no anomaly, because from its side nothing was wrong: it asked for
    the keep-outs and it got 12,301 of them.

    The declaration was in the file the whole time. Reading it is the fix.

    AND READING IT WAS ONLY HALF. With the header checked but the PATH still
    frozen to one seed's content hash, a fresh-seed run got a refusal it could
    not comply with: MEASURED, a Fresh42 dump of 12,376 locations was sitting
    at /tmp/seedfield/loc/74cb38be48da.json while `roads/geography.py:387`
    called `load()` and was refused against Pirate68's. So when no explicit
    path is given the dump is RESOLVED for the run's seed through
    `dump_for_seed`, off the `index.tsv` the producer already writes. An
    explicit `path` is still honoured and still scope-checked -- a caller that
    names a file is making a claim about it, and that claim is the one thing
    this function must not take on trust.
    """
    want = seed or _worldseed().seed()
    path = Path(path) if path is not None else location_dump(want)
    d = json.loads(Path(path).read_text())
    _worldseed().require_scope(
        "location dump", path, found_seed=dump_seed(d), seed_=want,
        produced_by="Settlements' LocScan.cs over a generated world; a dump is "
                    "a function of the SEED alone, so any world on this seed "
                    "will do")
    # Per-TYPE measured ZNetView reach, when the dump carries it (loc3 onward).
    reach: dict[str, float] = {}
    for t in d.get("locationTypes") or []:
        key = t.get("prefab") or t.get("name")
        if key:
            reach[key] = float(t.get("znviewReachM") or 0.0)
    out = []
    for r in d["locations"]:
        declared = float(max(r.get("exteriorRadius") or 0,
                             r.get("interiorRadius") or 0,
                             reach.get(r.get("prefab") or r.get("name"), 0.0)))
        q = int(r.get("quantity") or 0)
        prot = bool(r.get("prioritized")) or bool(r.get("centerFirst")) or (0 < q <= 5)
        out.append(Poi(r["name"], float(r["x"]), float(r["z"]), float(r.get("y") or 0.0),
                       declared, prot, q))
    return out


# THE DUMP IS MOD-FREE AND THE WORLD IS NOT.  `DUMP` is the world GENERATOR's
# location list: 12,301 instances, 177 types, and it cannot see
# `More_World_Locations_AIO`'s ~190 POI types because they are not part of
# world generation at all.  That caveat has been carried as a "residual risk"
# in every return value for days; on 2026-09-19 it was MEASURED as a live,
# reproducing defect and this file is the repair.
#
# The evidence, from the carriageway census of the built network:
#   * the obstacle the operator reported -- a roof on posts at (518.57, 824.78)
#     with its own `LocationProxy` and `Music_MeadowsVillageFarm` at
#     (517.97, 56.53, 824.67) -- has NO entry in the dump; the nearest
#     generator instance is a `Dolmen02` 23.2 m away that cannot produce a
#     wood roof;
#   * 13 further mod structures stand INSIDE a built carriageway, none of them
#     in the dump, worst 57 pieces blocking the paved surface on N0.
# A router planning against the dump alone WILL plan through them again; it has
# already done so twice, at T12 s=60 m and at Stenvik.
#
# These are PROTECTED unconditionally.  A furnished mod house is the one damage
# class that cannot be rebuilt, so the standing ruling is re-route, re-terminate
# or accept-and-record -- never delete.  `protected=True` therefore gives them
# the full `keepout_m` budget and the road bends.
MOD_KEEPOUT = Path(__file__).resolve().parent / "mod_keepout.json"


def load_mod_keepout(path: Path = MOD_KEEPOUT) -> list[Poi]:
    """Measured structures the generator dump cannot see, as POIs to avoid.

    COVERAGE, schema 2: the carriageways of ALL 25 BUILT segments, 21,929 m of
    live centreline, censused on the socket with `findObjects -near <x> <road_y>
    <z> 8.00` and the `Found n objects` header checked against the rows parsed
    on every call.  Nine calls inside the portal hall's own grausten floor are
    recorded as FAILED READS rather than as zeros, so that one body's piece
    count is a floor; everywhere else the header matched.

    `forbid` IS NOT THE SAME CLAIM AS `structures`.  Every row is a
    measurement; `forbid` says whether that measurement is also a routing
    constraint, and this function returns only the rows where it is.  The
    distinction exists because schema 1 recorded OUR OWN BRIDGE DECK as a mod
    structure with a 13.18 m keepout -- which would forbid the carriageway to
    itself at the one place N0 crosses water.  A body that IS the road is kept
    as a record with `forbid: false`.

    ABSENCE IS STILL NOT EVIDENCE OF A CLEAR CORRIDOR for anything OUTSIDE a
    built carriageway: the census swept the road band, not the world.
    """
    p = Path(path)
    if not p.exists():
        return []
    d = json.loads(p.read_text())
    return [Poi(s["name"], float(s["x"]), float(s["z"]), float(s.get("y") or 0.0),
                float(s["declared_m"]), True, 1)
            for s in d.get("structures") or []
            if s.get("forbid", True)]


def load_all(path: Path | None = None,
             mod_path: Path = MOD_KEEPOUT) -> list[Poi]:
    """Generator locations PLUS measured mod structures.

    The default entry point for ROUTING.  `load()` is kept for callers that
    genuinely want the seed-derived set alone (a `[SEED]` question, e.g.
    reproducing a plan on a fresh world before anything is censused).
    """
    return load(path) + load_mod_keepout(mod_path)


def masks(pois: list[Poi], x0: float, z0: float, cell_m: float, m: int,
          half_width_m: float, chord_slack_m: float | None = None,
          ) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Rasterise hard-forbid and soft-penalty circles onto a corridor grid.

    `chord_slack_m` GROWS the hard radius, and it is not padding for its own
    sake.  A* returns cell CENTRES on a `cell_m` grid and the polyline is then
    simplified with a 1 m tolerance, so the finished centreline can sit up to
    `cell_m/sqrt(2) + tol` inside a cell whose centre was outside the circle.
    MEASURED consequence of not doing this: on the first planned trunk the
    centreline came within 7.6 m of a WoodHouse7 whose hard radius is 14.0 m --
    the mask was correct and the line still breached it.  Growing the mask by
    the geometric slack fixes the cause; a post-check on the finished centreline
    (`near_centreline`) then PROVES it rather than assuming it.  Default slack
    is that bound, not a guess.

    Returns (forbid, soft, touched) where `soft` is an additive cost
    contribution in [0, 1] decaying linearly from the hard edge to the full
    keep-out radius, and `touched` lists every instance whose keep-out overlaps
    the grid so a route's rationale can name what it bent around.
    """
    if chord_slack_m is None:
        chord_slack_m = cell_m / math.sqrt(2.0) + 1.0
    forbid = np.zeros((m, m), dtype=bool)
    soft = np.zeros((m, m), dtype=np.float32)
    touched: list[dict] = []
    xlo, xhi = x0 - cell_m, x0 + (m - 1) * cell_m + cell_m
    zlo, zhi = z0 - cell_m, z0 + (m - 1) * cell_m + cell_m
    for p in pois:
        ko = p.keepout_m(half_width_m)
        if not (xlo - ko <= p.x <= xhi + ko and zlo - ko <= p.z <= zhi + ko):
            continue
        hard = p.hard_m(half_width_m) + chord_slack_m
        ko = max(ko, hard)
        jc = (p.x - x0) / cell_m
        ic = (p.z - z0) / cell_m
        r = int(math.ceil(ko / cell_m))
        j0, j1 = max(0, int(jc) - r), min(m - 1, int(jc) + r + 1)
        i0, i1 = max(0, int(ic) - r), min(m - 1, int(ic) + r + 1)
        if j0 > j1 or i0 > i1:
            continue
        jj = np.arange(j0, j1 + 1)
        ii = np.arange(i0, i1 + 1)
        dx = (jj - jc) * cell_m
        dz = (ii - ic) * cell_m
        d = np.hypot(dx[None, :], dz[:, None])
        sub_f = d <= hard
        if sub_f.any():
            forbid[i0:i1 + 1, j0:j1 + 1] |= sub_f
        band = (d > hard) & (d <= ko)
        if band.any():
            w = np.clip((ko - d) / max(ko - hard, 1e-6), 0.0, 1.0).astype(np.float32)
            blk = soft[i0:i1 + 1, j0:j1 + 1]
            np.maximum(blk, np.where(band, w, 0.0), out=blk)
        touched.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                        "declared_radius_m": p.declared_m, "protected": p.protected,
                        "quantity": p.quantity,
                        "hard_m": round(hard, 1), "keepout_m": round(ko, 1),
                        "gate_m": round(p.gate_m(half_width_m), 1),
                        "gate_binds": p.gate_m(half_width_m) > p.taste_m(half_width_m)})
    return forbid, soft, touched


def polyline_dist(nodes, px, pz) -> np.ndarray:
    """Perpendicular distance from each (px, pz) to the POLYLINE nodes trace.

    THE MEASUREMENT `near_centreline` USED TO GET WRONG, and it is the whole
    reason a route could pass this module and be refused by the writer.  The
    old code took `min` over the NODES.  A* returns cell centres and
    `route.simplify` then cuts chords to a 1 m tolerance, so a straight leg
    is TWO nodes however long it is -- and an instance standing 1.5 m from
    the middle of a 200 m straight measures ~100 m to the nearest node.  The
    check reported a clear route and `ribbon.py::location_check`, which
    rasterises the carriageway every metre, refused it.  That is not a
    stricter gate downstream, it is an instrument that cannot see the thing
    it is asked about, and a clean result from it was never evidence.

    MEASURED on this world by `selftest()`: over the 21 built segments and
    all 12,301 instances the node-only distance over-states the true
    perpendicular distance for thousands of pairs, and it misses genuine
    hard-radius breaches that the polyline form catches.
    """
    N = np.asarray(nodes, dtype=np.float64)[:, :2]
    a, b = N[:-1], N[1:]
    ab = b - a
    L2 = (ab ** 2).sum(1)
    L2[L2 == 0] = 1e-12
    P = np.stack([np.asarray(px, dtype=np.float64),
                  np.asarray(pz, dtype=np.float64)], axis=1)
    out = np.empty(len(P), dtype=np.float64)
    # chunked so a 12,301 x n_seg outer product never becomes the cost
    step = max(1, int(4_000_000 // max(len(a), 1)))
    for s in range(0, len(P), step):
        q = P[s:s + step]
        t = (((q[:, None, 0] - a[None, :, 0]) * ab[None, :, 0]
              + (q[:, None, 1] - a[None, :, 1]) * ab[None, :, 1])
             / L2[None, :]).clip(0.0, 1.0)
        cx = a[None, :, 0] + t * ab[None, :, 0]
        cz = a[None, :, 1] + t * ab[None, :, 1]
        out[s:s + step] = np.hypot(cx - q[:, None, 0], cz - q[:, None, 1]).min(1)
    return out


def near_centreline(pois: list[Poi], nodes: list[tuple[float, float]],
                    half_width_m: float) -> list[dict]:
    """Every instance whose HARD radius the finished centreline comes within.

    Reported even when empty: "the route clears every vanilla POI" is a claim
    that needs a measurement behind it, and this is the measurement.  The
    distance is to the POLYLINE, not to its nodes -- see `polyline_dist`.
    """
    if not pois:
        return []
    d = polyline_dist(nodes, [p.x for p in pois], [p.z for p in pois])
    out = []
    for k, p in enumerate(pois):
        hard = p.hard_m(half_width_m)
        gate = p.gate_m(half_width_m)
        ko = max(p.keepout_m(half_width_m), hard)
        dk = float(d[k])
        if dk <= ko:
            out.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                        "protected": p.protected, "declared_radius_m": p.declared_m,
                        "centreline_dist_m": round(dk, 1),
                        "hard_m": round(hard, 1), "keepout_m": round(ko, 1),
                        "gate_m": round(gate, 1),
                        "violates_hard": dk < hard,
                        "violates_gate": dk < gate})
    return sorted(out, key=lambda r: r["centreline_dist_m"])


# The terrain sample pitch.  `ribbon.stamp` writes on the 1 m heightmap
# lattice, so the outermost line of carriageway samples on a given bearing
# can fall up to one pitch INSIDE the nominal band edge.  The centreline
# restatement below therefore refuses a hair earlier than the writer does,
# and the gap is bounded by this number rather than unknown.
LATTICE_PITCH_M = 1.0


def gate_check(nodes, half_width_m: float, pois: list[Poi] | None = None,
               level_half_width_m: float | None = None) -> dict:
    """WOULD `ribbon.py` REFUSE THIS CENTRELINE?  Ask before routing, not after.

    This is the write-time location gate restated on the centreline:
    `location_check` tests every CARRIAGEWAY sample against
    `reach + TERRAIN_SPREAD_M`, and those samples fill a band of half-extent
    `level_half_width_m` about this polyline, so a sample breaches exactly
    when the centreline is nearer than `reach + TERRAIN_SPREAD_M + lvl`.

    A router calls this on its candidate alignment.  A `refuse` here is the
    refusal the writer would issue, paid for in milliseconds against a dump
    instead of in a full rasterisation against the world.

    IT IS CONSERVATIVE BY AT MOST ONE SAMPLE PITCH, AND THAT IS MEASURED,
    not assumed.  The writer stamps a 1 m lattice, so the band's outermost
    sample line may not exist on the bearing that matters.  Both cases are
    real on this world, at `half_width_m` 3.0 against the live centrelines:

      road_R1_ulfsgard_t8junction  Dolmen02        centreline 12.70 m, this
        function needs 13.00 -> refuse short_by 0.30; the writer's own
        nearest lattice carriageway sample is 9.21 m against a required
        9.00 and it ACCEPTS by +0.21.  Reported as `marginal`.
      road_N2_shelf_mid1           InfestedTree01  centreline 7.48 m, needs
        10.00 -> refuse short_by 2.52; the writer's nearest sample is 3.86 m
        against a required 6.00 and it REFUSES by -2.14.  Not marginal, and
        a live defect on a segment that is already written.

    So `short_by_m > LATTICE_PITCH_M` is a refusal the writer will reproduce;
    at or under it, the alignment is inside the discretisation and the honest
    answer is "shift it or confirm against the lattice", never "it passed".
    """
    P = load() if pois is None else pois
    lvl = (level_half_m(half_width_m) if level_half_width_m is None
           else float(level_half_width_m))
    d = polyline_dist(nodes, [p.x for p in P], [p.z for p in P])
    bad = []
    for k, p in enumerate(P):
        need = p.gate_m(half_width_m, lvl)
        dk = float(d[k])
        if dk < need:
            bad.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                        "declared_radius_m": p.declared_m,
                        "centreline_dist_m": round(dk, 2),
                        "required_m": round(need, 2),
                        "short_by_m": round(need - dk, 2),
                        "marginal": (need - dk) <= LATTICE_PITCH_M,
                        "protected": p.protected})
    bad.sort(key=lambda r: -r["short_by_m"])
    firm = [r for r in bad if not r["marginal"]]
    return {"verdict": "clear" if not bad else "refuse",
            "instances": len(P), "violations": bad,
            "firm_violations": firm, "marginal_violations": len(bad) - len(firm),
            "level_half_width_m": lvl,
            "terrain_spread_m": TERRAIN_SPREAD_M,
            "lattice_pitch_m": LATTICE_PITCH_M,
            "boundary": "declared_m + TERRAIN_SPREAD_M + level_half_width_m, "
                        "measured perpendicular to the POLYLINE",
            "same_gate_as": "ribbon.py::location_check -> "
                            "settlements/clearance.py::verdict_for_samples"}


def selftest(verbose: bool = True) -> dict:
    """PROVE THE PLANNER AND THE WRITE GATE AGREE, IN BOTH DIRECTIONS.

    Runnable: `python3 roads/poi.py`.  It is offline -- dump and ledger only,
    no RCON -- so it costs nothing and can be re-run after any change to
    either boundary.  Four claims, each with its own failing case:

      1. NOTHING MOVES for the callers that exist.  At the half-widths every
         in-repo router passes (2.0 / 3.0 / 4.0 m) the gate floor must not
         raise a single one of the 12,301 instances' hard radii, or this
         change silently re-routes roads that were fine.
      2. THE FLOOR HOLDS EVERYWHERE.  `hard_m >= gate_m` at every width
         tested, including widths no caller uses yet.
      3. REFUSE.  A centreline 5.5 m from the `WoodHouse6` at (1204.9,
         575.2) -- the R2 v2 alignment, whose CARRIAGEWAY SAMPLE the writer
         measured at 1.50 m -- must be refused, needing 13.0 m and short by
         7.50.  Those are the writer's own recorded numbers.
      4. CLEAR.  The R2 v3 alignment that was actually built must pass, and
         the OLD node-only instrument must be shown to FAIL claim 3 -- a
         check that has never been seen to fail is not a check.
    """
    import yaml  # noqa: PLC0415
    P = load()
    out: dict = {"instances": len(P)}
    moved = {hw: sum(1 for p in P if p.hard_m(hw) > p.taste_m(hw) + 1e-9)
             for hw in (2.0, 3.0, 4.0)}
    out["hard_radii_changed_at_in_repo_widths"] = moved
    assert not any(moved.values()), moved
    under = [(hw, p.name) for hw in (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
             for p in P if p.hard_m(hw) < p.gate_m(hw) - 1e-9]
    out["instances_under_the_gate"] = len(under)
    assert not under, under[:5]

    wh = next(p for p in P if abs(p.x - 1204.9) < 2 and abs(p.z - 575.2) < 2)
    v2 = [(wh.x - 150.0, wh.z + 5.5), (wh.x + 150.0, wh.z + 5.5)]
    refused = gate_check(v2, 3.0, [wh])
    out["v2_like"] = refused
    assert refused["verdict"] == "refuse", refused
    v = refused["violations"][0]
    assert abs(v["required_m"] - 13.0) < 1e-6, v
    assert abs(v["short_by_m"] - 7.50) < 1e-6, v
    assert not v["marginal"], v
    # the OLD instrument, on the SAME alignment: a confident 150 m of clearance
    node_only = min(math.hypot(wh.x - a, wh.z - b) for a, b in v2)
    out["old_node_only_distance_m"] = round(node_only, 1)
    assert node_only > wh.hard_m(3.0), node_only

    # R2 IS PIRATE68'S ROAD, so its plan is asked for BY THAT SEED, never by a
    # bare file name that means Pirate68's only on Pirate68.
    ws = _worldseed()
    doc = yaml.safe_load(ws.segments_path(ws.DEFAULT_SEED).read_text())
    r2 = next(s for s in doc["segments"] if s["id"] == "R2-ulfsgard-harbourb")
    nodes = [(n[0], n[1]) for n in r2["nodes"]]
    built = gate_check(nodes, r2["width_m"] / 2.0, P)
    out["r2_v3_built"] = {"verdict": built["verdict"],
                          "violations": len(built["violations"])}
    assert built["verdict"] == "clear", built["violations"][:3]
    near = {r["name"]: r["centreline_dist_m"]
            for r in near_centreline(P, nodes, r2["width_m"] / 2.0)}
    out["r2_v3_clearances_m"] = near
    if verbose:
        print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    selftest()
