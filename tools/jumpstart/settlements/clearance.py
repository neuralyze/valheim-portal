#!/usr/bin/env python3
"""How close may a settlement stand to a world location, and which locations
must not be touched at all.

THE DEFECT THIS EXISTS FOR
--------------------------
In the previous Ulfsland world the stand-off from a vanilla location was
computed from the LocationProxy MARKER, because that was the only per-location
datum the `run_locscan.sh` dump carried. Location PIECES were then found 43 m
from their marker, and clearing at three sites deleted POI content. The fix
attempted first was a bigger global number, and that fails differently:
MEASURED, a single 43 m + town-half-diagonal (177 m) stand-off against all
12,301 instances of `Pirate68` clears ZERO cells anywhere in the world, because
location density puts a marker within ~155 m of nearly everywhere on land.

So clearance is PER LOCATION TYPE, and the numbers come from the game's own
data. `LocScan.cs` now dumps, per type:

    exteriorRadius   what ZoneSystem::PlaceLocations clears around the marker
    interiorRadius   the dungeon/interior radius where there is one
    znviewReachM     MEASURED: the furthest horizontal distance from the
                     location prefab's root to any child carrying a ZNetView.
                     Those children are exactly the objects that become ZDOs
                     and that a clearing step would delete, which makes this the
                     operationally correct reach.
    rendererReachM   ALSO dumped and DELIBERATELY NOT USED here. MEASURED it is
                     not trustworthy: `AbandonedLogCabin04` reports 528.7 m
                     against a 5.0 m ZNetView reach, because `Renderer.bounds`
                     on an un-instantiated prefab is not a world measurement.
                     Reported in the dump so the next reader can see why it was
                     rejected rather than rediscovering it.

WHAT THE MEASUREMENT SHOWED, over all 177 types of this seed
------------------------------------------------------------
  * the largest DECLARED exterior radius in the whole world is 32 m
  * the largest MEASURED ZNetView reach is 35.77 m (`TrollCave02`, declared 12)
  * 12 of 177 types reach FURTHER than their declared radius, worst excess
    +11.8 m (`TrollCave02`)
  * 28 of 177 types carry <= 2 ZNetView children in the prefab, so their content
    is either non-networked static geometry or spawned by a generator at
    placement time. For those the declared radius is the only usable number and
    the measured reach must NOT be read as "this location is small".

That last point is why `standoff_m` takes the MAX of declared and measured
rather than preferring the measurement.

The earlier +11 m overshoot constant, which Main correctly flagged as INFERRED
from one observation, is GONE: with a per-type measured reach there is nothing
left for it to do.

SACREDNESS: THERE IS NO FLAG FOR IT
-----------------------------------
Checked every field on `ZoneSystem.ZoneLocation` in the IL. There is no
"protected", "important" or "boss" boolean. What the data does carry is how the
generator TREATS an instance, and that is the proxy used here:

    prioritized OR centerFirst OR quantity <= 5   ->  PROTECTED

MEASURED examples for: `StartTemple` quantity 1 prioritized centerFirst,
`bosslocation` quantity 3 prioritized, `GoblinKing` quantity 4 prioritized,
`Mistlands_DvergrBossEntrance1` quantity 5 prioritized. Against:
`InfestedTree01` 700, `Mistlands_RoadPost1` 500, `GoblinCamp2` 200 -- scenery by
the generator's own treatment. `unique` is NOT the signal and must not be used:
MEASURED it is FALSE even on `bosslocation`.

A PROTECTED location gets `PROTECTED_EXTRA_M` on top of its own reach. That
number is a TASTE PICK, not a measurement, and is labelled as one.

LIMIT, unchanged and load-bearing: this dump is MOD-FREE by construction
(`run_locscan.sh` excludes `BepInEx/`), so it cannot see More_World_Locations'
POIs. Clearing this check is necessary and NOT sufficient; a live marker check
after `zones_generate` is still required.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# TASTE PICK, not measured: breathing room between our pad edge and the reach of
# a location's own objects, so that a building does not read as crowding a ruin.
MARGIN_M = 12.0
# TASTE PICK, not measured: extra stand-off for a location the generator treats
# as load bearing (a boss altar, the trader, a dungeon entrance, the temple).
PROTECTED_EXTRA_M = 90.0
# DERIVED, not picked: how far past its own written samples a TERRAIN write can
# still move ground.  `TerrainComp::ApplyToHeightmap` applies each sample's
# delta to that sample only -- there is no spatial falloff, and RoadNet's
# ribbon writer leaves `smoothDelta` at 0 -- so the only spreading is the
# heightmap MESH, which interpolates linearly between adjacent samples at
# `Heightmap`'s 1 m pitch (SCALE = 1.0, pitch = m_width + 1 = 65 over a 64 m
# zone, MEASURED).  A written sample therefore tilts the surface out to the
# next UNWRITTEN sample and no further: exactly one sample pitch.
TERRAIN_SPREAD_M = 1.0
# The non-destructive hazard is not deletion, it is a location piece left
# FLOATING or BURIED because the ground under it moved.  A piece whose ground
# does not move is unaffected at any distance; one whose ground moves is
# damaged at any distance.  So the gate on a non-destructive write is a
# per-piece height-delta tolerance, and this is its starting value -- 0.10 m,
# to be MEASURED against what actually reads as a visible step underfoot.
DELTA_TOL_M = 0.10
# MEASURED, not picked: `ZoneSystem.m_zoneSize`, the 64 m zone the generator
# places every location in (the same number `crossings/plan.py::ZONE_SIZE` and
# the TCData codec read), and `ZoneSystem.GetZone`'s own floor((p + 32) / 64).
ZONE_M = 64.0
# One metre of slack between a clearing cylinder and a location's population:
# the number `settlements/build.py::clear_radius_room` has always held.
CLEARING_SLACK_M = 1.0

# CLEARING_POPULATION_DEPENDS_ON: what a clearing cylinder must not reach.
#
# THE DEFECT, MEASURED on Fresh42 drive 5.  The build REFUSED wt-south's pad
# (ledger seq 2077, step 5) because its 16.14 m cylinder at (-672.1, -372.8)
# held 3 `dungeon_forestcrypt_door` and 1 `CastleKit_groundtorch`, and wt-east
# on 1 `CastleKit_groundtorch` -- and the offline gate before it, and the
# siting before THAT, had both passed the same pads: Crypt4's marker stands
# 48.0 m from wt-south's centre and its dump reach is 21 m, so the room was
# 26.0 m against 16.14.  The objects were not on the ground: every one stood
# at y = 5061.14, the crypt's INTERIOR, which the generator builds in the sky
# above the location and which `objects_count`/`objects_remove` see because
# their filter is `Utils.DistanceXZ` -- a cylinder, height excluded.
#   pieces         the location's own pieces: max(exteriorRadius,
#                  interiorRadius, znviewReachM) about its marker -- the only
#                  population the dump ever described.
#   interior_zone  a location that declares an interior (`interiorRadius` >
#                  0) owns the WHOLE 64 m zone its marker stands in, at every
#                  height.  MEASURED over the drive-5 frozen save's whole-ZDO
#                  census (gate fingerprint 8c8c54096789): 992 of 992 objects
#                  above y = 1000 stand inside the zone square of a location
#                  whose type declares an interior (Crypt2 343, Crypt3 348,
#                  Crypt4 247, TrollCave02 54); 0 stand outside one; the
#                  farthest is 45.0 m from its own marker against a declared
#                  interiorRadius of 21.  [INFERENCE] the bound is the
#                  dungeon generator confining its rooms to its own zone; the
#                  zone square is what was measured to hold, and it is used
#                  rather than the 45.0 m maximum because a maximum over the
#                  instances one save generated is not a bound on the next.
CLEARING_POPULATION_DEPENDS_ON = ("pieces", "interior_zone")


def zone_square(x: float, z: float) -> tuple[float, float, float, float]:
    """(x0, x1, z0, z1) of the generator zone holding (x, z)."""
    zx = math.floor((x + ZONE_M / 2) / ZONE_M)
    zz = math.floor((z + ZONE_M / 2) / ZONE_M)
    return (zx * ZONE_M - ZONE_M / 2, zx * ZONE_M + ZONE_M / 2,
            zz * ZONE_M - ZONE_M / 2, zz * ZONE_M + ZONE_M / 2)



@dataclass
class Locations:
    xz: np.ndarray                 # (n, 2) float32
    names: list[str]
    prefabs: list[str]
    reach: np.ndarray              # (n,) float32, per-instance own reach
    protected: np.ndarray          # (n,) bool
    standoff: np.ndarray           # (n,) float32, reach + margins
    spawn: tuple[float, float]
    by_type: dict[str, dict]
    # (n,) bool: the instance declares an interior (`interiorRadius` > 0),
    # whose population fills its marker's zone -- CLEARING_POPULATION_DEPENDS_ON.
    interior: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.names)

    def nearest(self, x: float, z: float) -> dict:
        d = np.hypot(self.xz[:, 0] - x, self.xz[:, 1] - z)
        i = int(np.argmin(d))
        return {"name": self.names[i], "prefab": self.prefabs[i],
                "dist_m": round(float(d[i]), 1),
                "reach_m": float(self.reach[i]),
                "protected": bool(self.protected[i]),
                "standoff_m": float(self.standoff[i])}

    # ---- THE CLEARING CYLINDER'S ROOM: ONE RULE FOR SITING AND FOR BUILD ---
    def cylinder_room(self, cx: float, cz: float,
                      depends_on: tuple = CLEARING_POPULATION_DEPENDS_ON
                      ) -> dict:
        """How wide an `objects_remove` cylinder about (cx, cz) may be.

        THE clearing gate's offline rule, one function for both askers: the
        siting refuses a pad by the full population before it places one
        (`settlements/plan.py::clearing_refusals` via `build.clearing_gate`),
        and the build asks it over `build.BUILD_ROOM_DEPENDS_ON` before it
        clears and records the full room beside its exact live census.

        The bound is a MINIMUM OVER ALL INSTANCES, not the nearest marker,
        over every population in `depends_on` (CLEARING_POPULATION_DEPENDS_ON):
        `pieces` is distance less the instance's own reach; `interior_zone`
        is the distance to the instance's whole zone square when it declares
        an interior.  Less `CLEARING_SLACK_M`.  The cylinder has no height:
        `Utils.DistanceXZ` is the removal's filter, so a population in the
        sky is inside it."""
        # float32, as `clear_radius_room` always computed it: the recorded
        # `clear_radius_room_m` of a pieces-bound pad is unchanged to the cm.
        d = np.hypot(self.xz[:, 0] - cx, self.xz[:, 1] - cz)
        inf = np.full(len(self.names), np.inf)
        pieces = ((d - self.reach).astype(np.float64) if "pieces" in depends_on
                  else inf)
        inner, sq = inf, None
        if "interior_zone" in depends_on and self.interior is not None:
            h = ZONE_M / 2
            zx = np.floor((self.xz[:, 0].astype(np.float64) + h) / ZONE_M) * ZONE_M
            zz = np.floor((self.xz[:, 1].astype(np.float64) + h) / ZONE_M) * ZONE_M
            ox = np.maximum(np.abs(cx - zx) - h, 0.0)
            oz = np.maximum(np.abs(cz - zz) - h, 0.0)
            inner = np.where(self.interior, np.hypot(ox, oz), np.inf)
            sq = (zx, zz)
        room = np.minimum(pieces, inner)
        i = int(np.argmin(room))
        via_zone = bool(inner[i] < pieces[i])
        binding = {"name": self.names[i], "prefab": self.prefabs[i],
                   "dist_m": round(float(d[i]), 2),
                   "reach_m": round(float(self.reach[i]), 2),
                   "population": "interior_zone" if via_zone else "pieces"}
        if via_zone:
            h = ZONE_M / 2
            binding["zone"] = [float(sq[0][i] - h), float(sq[0][i] + h),
                               float(sq[1][i] - h), float(sq[1][i] + h)]
            binding["zone_dist_m"] = round(float(inner[i]), 2)
            binding["why"] = (
                f"{self.names[i]}'s INTERIOR -- it declares interiorRadius "
                f"> 0, and an interior fills its marker's whole {ZONE_M:g} m "
                f"zone x {binding['zone'][:2]} z {binding['zone'][2:]} at "
                f"y ~5000, {binding['zone_dist_m']} m from this centre")
        else:
            binding["why"] = (
                f"{self.names[i]}'s PIECES -- it stands {binding['dist_m']} m "
                f"away with a measured {binding['reach_m']} m reach")
        return {"max_radius_m": round(float(room[i]) - CLEARING_SLACK_M, 2),
                "binding": binding,
                "depends_on": list(depends_on),
                "method": ("MEASURED: min over all instances of (distance - "
                           "max(exteriorRadius, interiorRadius, znviewReachM)) "
                           "and, for an instance declaring an interior, of the "
                           "distance to its marker's 64 m zone square; less "
                           f"{CLEARING_SLACK_M:g} m of slack. The clearing "
                           "cylinder is height-blind and must not reach a "
                           "location's PIECES or its INTERIOR."),
                "tool": "tools/jumpstart/settlements/clearance.py::"
                        "Locations.cylinder_room"}

    # ---- THE ONE BOUNDARY, AND THE TWO ANSWERS TO CROSSING IT ------------
    #
    # THE DEFECT THIS METHOD EXISTS TO END.  A road write is TWO bands of
    # ground with the same relationship to a location and OPPOSITE failure
    # modes, and until tonight they were measured by two implementations that
    # disagreed.  `ribbon.py::poi_standoff_discs` built discs at
    # `reach + TERRAIN_SPREAD_M` and CLIPPED the batter per sample against
    # them; `violations_for_samples` below built `reach + half_width_m +
    # TERRAIN_SPREAD_M` and REFUSED THE WHOLE WRITE.  MEASURED consequence on
    # the northern trunk, seed Pirate68: `N0-brgs2-wmid` carried 13,238
    # carriageway samples and the refusal named ONE of them, 8.7 m from a
    # `Ruin2` whose reach is 9.89 m -- a metre of shoulder costing 1,779.4 m
    # of road, while a batter sample at the same 8.7 m would merely have been
    # clipped and counted.  Two gates, one sample, opposite verdicts.
    #
    # SO THERE IS ONE BOUNDARY AND IT LIVES HERE.  It is
    # `reach + half_width_m + TERRAIN_SPREAD_M`, and `half_width_m` is ZERO
    # for a sample set that already IS the footprint -- which a rasterised
    # carriageway is, every 1 m sample out to its own edge.  What differs
    # between the bands is not the boundary, it is what crossing it COSTS:
    #
    #   BATTER       -> CLIP the sample, record cause `poi` and the residual
    #                   wall.  A batter is earthwork that tapers to nothing
    #                   and is never load bearing, so refusing one metre of
    #                   it costs a recorded blemish.
    #   CARRIAGEWAY  -> REFUSE the write.  The carriageway is the surface the
    #                   operator walks on; a clipped carriageway sample is a
    #                   HOLE, and a road with a hole in it beside a ruin is
    #                   not a cheaper road, it is a different defect.
    #
    # Both answers read the same number from the same array, which is the only
    # property that makes them reconcilable at all.
    def hard_radius(self, half_width_m: float = 0.0) -> np.ndarray:
        """Per-instance NON-DESTRUCTIVE boundary, as a (n,) array of metres.

        `half_width_m` is the CALLER's own half-extent about its samples and
        MUST be 0.0 when the samples already are the footprint.  Passing the
        road half-width beside a full-width rasterised sample set counts the
        same 3 m twice: MEASURED on `N0-brgs2-wmid`, that inflated `Ruin2`'s
        required distance from 10.89 m to 13.9 m and is the difference between
        a gate that measures the written ground and one that measures a claim
        about it.
        """
        return (self.reach.astype(np.float64) + float(half_width_m)
                + TERRAIN_SPREAD_M)

    def keepout_discs(self, xlo: float, xhi: float, zlo: float, zhi: float,
                      half_width_m: float = 0.0
                      ) -> list[tuple[float, float, float]]:
        """The SAME boundary as `(x, z, r)` discs, for a caller that clips per
        sample instead of refusing per write.

        Bounded to a box because a per-sample clip on a 12,301-instance world
        must cost a few dozen comparisons rather than twelve thousand.  Handing
        the discs out from here rather than rebuilding them in the caller is
        the whole point: a second implementation of a boundary is a second
        boundary, and this project has now paid for that three times.
        """
        r = self.hard_radius(half_width_m)
        out: list[tuple[float, float, float]] = []
        for i in range(len(self.names)):
            x, z = float(self.xz[i, 0]), float(self.xz[i, 1])
            if xlo <= x <= xhi and zlo <= z <= zhi:
                out.append((x, z, float(r[i])))
        return out

    def violations(self, x: float, z: float, own_half_diagonal_m: float,
                   protected_only: bool = False) -> list[dict]:
        """Every location whose stand-off our footprint would enter. Empty is
        the pass condition; the list is the evidence when it is not.

        `protected_only` is the DISTRICT test rather than the pad test, and the
        distinction is a design decision worth stating. A town is a scatter of
        small pads and streets, not one solid rectangle: a ruin, a runestone or
        a dolmen standing untouched INSIDE a village is scenery the operator
        walks past, and demanding a whole district be empty of all 12,301
        instances is what made every district in the world fail (MEASURED:
        0 of 818 terrain-passing 176 m districts cleared the all-instance test).
        What a district genuinely must not contain is a location the generator
        treats as load bearing -- a boss altar, the trader, the start temple --
        because a town centre on top of one is wrong at any earthwork cost.
        So: DISTRICTS clear `protected_only=True`; every PAD and every STREET
        clears the full set at its own footprint.
        """
        d = np.hypot(self.xz[:, 0] - x, self.xz[:, 1] - z)
        need = self.standoff + own_half_diagonal_m
        room = need - d
        if protected_only:
            room = np.where(self.protected, room, -np.inf)
        bad = np.nonzero(room > 0)[0]
        out = []
        for i in bad[np.argsort(room[bad])[::-1]]:
            out.append({"name": self.names[i], "prefab": self.prefabs[i],
                        "dist_m": round(float(d[i]), 1),
                        "required_m": round(float(need[i]), 1),
                        "short_by_m": round(float(room[i]), 1),
                        "protected": bool(self.protected[i])})
        return out

    def clear(self, x: float, z: float, own_half_diagonal_m: float,
              protected_only: bool = False) -> bool:
        return not self.violations(x, z, own_half_diagonal_m, protected_only)

    def violations_for_samples(self, samples, protected_only: bool = False,
                               half_width_m: float = 0.0, chunk: int = 4096,
                               destructive: bool = True) -> list[dict]:
        """Every location whose own reach is entered by ANY of these samples.

        `samples` is an iterable of (x, z) -- the sample centres a write will
        actually touch: a pad's 1 m lattice, a ribbon's centreline, a bridge
        deck's tiles. This is strictly better than a half-diagonal disc, which
        over-states a long thin footprint (a 6 m x 200 m ribbon has a 100 m
        half-diagonal and would refuse everything) and under-states nothing.

        `half_width_m` is YOUR OWN geometry's half-extent about those samples
        and is added to each instance's own radius -- `RoadNet`'s idea and the
        right question, because what must not overlap is the ribbon or the deck
        rather than its centreline. Zero when the samples already ARE the
        footprint, as a pad lattice is.

        Per violation it reports the MINIMUM distance from the written set to
        that instance, so the number in the log is the one that decides the
        verdict rather than a summary of the footprint. Empty list is the pass
        condition.
        """
        pts = np.asarray(list(samples), dtype=np.float32).reshape(-1, 2)
        if not len(pts):
            return []
        best = np.full(len(self.names), np.inf, np.float32)
        for i in range(0, len(pts), chunk):
            block = pts[i:i + chunk]
            d = np.hypot(self.xz[:, 0][:, None] - block[None, :, 0],
                         self.xz[:, 1][:, None] - block[None, :, 1]).min(axis=1)
            np.minimum(best, d, out=best)
        # `destructive` picks the budget, and the distinction is the operation
        # rather than the geometry.  The unrepairable damage this stand-off
        # exists to prevent came from `objects_remove` DELETING a location's
        # ZDOs.  An operation that emits only `zones_generate` + `terrain_write`
        # cannot perform that deletion at all, so `MARGIN_M` (taste) and
        # `PROTECTED_EXTRA_M` (taste) are budgeting against a hazard it does not
        # have -- and MEASURED, applying them to a 6 m road refused 25 of 34
        # segments on a world with 12,301 instances, including every segment out
        # of StartTemple.  A non-destructive write is bounded instead by the
        # location's own measured reach plus the caller's own half-extent plus
        # the DERIVED terrain spread.
        #
        # THAT IS NOT THE WHOLE GUARD.  A non-destructive write still has one
        # real hazard -- levelling ground under a piece that stays standing
        # leaves it FLOATING or BURIED, which is the defect the operator
        # condemned a world for -- and distance does not measure it.  The caller
        # MUST also pass the per-piece delta gate (`delta_gate` below); this
        # function deliberately cannot check that, because only the caller holds
        # the per-sample deltas.  Default stays True so no existing caller
        # changes behaviour.
        if destructive:
            need = self.standoff + float(half_width_m)
        else:
            need = self.hard_radius(half_width_m)
        room = need - best
        if protected_only:
            room = np.where(self.protected, room, -np.inf)
        out = []
        for i in np.nonzero(room > 0)[0][np.argsort(-room[room > 0])]:
            out.append({"name": self.names[i], "prefab": self.prefabs[i],
                        "min_dist_m": round(float(best[i]), 1),
                        "reach_m": float(self.reach[i]),
                        "required_m": round(float(need[i]), 1),
                        "short_by_m": round(float(room[i]), 1),
                        "protected": bool(self.protected[i]),
                        "budget": "destructive" if destructive
                                  else "non_destructive"})
        return out

    def clear_samples(self, samples, protected_only: bool = False,
                      half_width_m: float = 0.0,
                      destructive: bool = True) -> bool:
        return not self.violations_for_samples(
            samples, protected_only, half_width_m, destructive=destructive)

    def instances_near(self, samples, radius_m: float, chunk: int = 4096
                       ) -> list[dict]:
        """Every instance within `radius_m` of the written set, for the delta
        gate.  Returns position and reach so the caller can sample its own
        deltas at the piece positions it might be standing on."""
        pts = np.asarray(list(samples), dtype=np.float32).reshape(-1, 2)
        if not len(pts):
            return []
        best = np.full(len(self.names), np.inf, np.float32)
        for i in range(0, len(pts), chunk):
            block = pts[i:i + chunk]
            d = np.hypot(self.xz[:, 0][:, None] - block[None, :, 0],
                         self.xz[:, 1][:, None] - block[None, :, 1]).min(axis=1)
            np.minimum(best, d, out=best)
        out = []
        for i in np.nonzero(best <= radius_m)[0]:
            out.append({"name": self.names[i], "prefab": self.prefabs[i],
                        "xz": [float(self.xz[i, 0]), float(self.xz[i, 1])],
                        "reach_m": float(self.reach[i]),
                        "protected": bool(self.protected[i]),
                        "min_dist_m": round(float(best[i]), 2)})
        return sorted(out, key=lambda r: r["min_dist_m"])

    def verdict_for_samples(self, samples, half_width_m: float = 0.0,
                            protected_only: bool = False,
                            destructive: bool = True,
                            delta_gate: dict | None = None,
                            clipped: dict | None = None) -> dict:
        """The same measurement, packaged so a caller cannot lose the caveats.

        `method`, `tool` and `mod_free_caveat` travel IN THE RESULT rather than
        in this docstring -- `RoadNet`'s idea and the best one in either
        implementation. A caveat that lives in the return value cannot be
        forgotten by the caller, and this project has lost hours to caveats
        that lived in somebody's head. `method` is also exactly what the
        ledger's `observe` and `terrain_write.location_check` require, so a
        result can be handed to the log unmodified.

        `clipped` IS WHAT THIS VERDICT DOES NOT COVER, AND IT IS REQUIRED
        READING RATHER THAN A COURTESY.  The caller may satisfy this boundary
        two ways: by not writing near a location, or by DROPPING the samples
        that would have.  The second is a SHRINK, and a shrink that leaves no
        trace is indistinguishable from a write that never needed one -- so a
        caller that clips passes its per-cause census here and it travels in
        the result beside the verdict.  `{"poi": 7}` next to `clear` says
        "clear BECAUSE seven samples were dropped at a location boundary";
        `clear` with no census says the write never approached one.  Those are
        different facts and the ledger must not read them the same.
        """
        pts = list(samples)
        v = self.violations_for_samples(pts, protected_only, half_width_m,
                                        destructive=destructive)
        near = self.nearest(*pts[0]) if pts else None
        # A non-destructive write is only clear when BOTH gates pass: the
        # distance gate above and the caller's own per-piece delta gate.  A
        # missing delta gate on a non-destructive call is a REFUSAL, not a
        # pass -- the loosened distance budget was granted on the strength of
        # the delta measurement, so accepting the loosening without it would be
        # a check answering a question it is not measuring.
        gate_bad = []
        if not destructive:
            if delta_gate is None:
                gate_bad = [{"reason": "destructive=False requires delta_gate: "
                                       "the loosened distance budget is granted "
                                       "ONLY against a measured per-piece "
                                       "height-delta result"}]
            elif delta_gate.get("verdict") != "clear":
                gate_bad = delta_gate.get("violations") or [
                    {"reason": "delta_gate verdict is not clear"}]
        return {
            "verdict": "clear" if not (v or gate_bad) else "VIOLATION",
            "budget": "destructive" if destructive else "non_destructive",
            "delta_gate": delta_gate,
            "delta_gate_violations": gate_bad,
            "violations": v,
            "samples_tested": len(pts),
            "half_width_m": float(half_width_m),
            "scope": "protected-only" if protected_only else "all instances",
            "nearest": near,
            "standoff_m": (near or {}).get("standoff_m"),
            # THE SHRINK, NAMED AND COUNTED.  `None` and `{}` are different
            # statements: `None` is "this caller does not clip", `{}` is "it
            # clips and had nothing to clip here".
            "clipped": clipped,
            "clipped_total": (None if clipped is None
                              else int(sum(clipped.values()))),
            "method": ("MEASURED per location TYPE: "
                       "max(exteriorRadius, interiorRadius, znviewReachM) from the "
                       "LocScan.cs dump, plus a 12 m margin (taste) and a 90 m extra "
                       "(taste) where the generator treats the instance as load "
                       "bearing -- prioritized OR centerFirst OR quantity <= 5. "
                       "Compared against the MINIMUM distance from the caller's own "
                       "written samples, plus half_width_m for the caller's geometry. "
                       "The declared radius is kept in the max() because 28 of 177 "
                       "types carry <= 2 ZNetView children and their measured reach "
                       "understates them."),
            "tool": "tools/jumpstart/settlements/clearance.py::verdict_for_samples",
            "mod_free_caveat": (
                "This dump is MOD-FREE by construction (run_locscan.sh excludes "
                "BepInEx/), so it CANNOT see More_World_Locations' ~190 POI types. "
                "MEASURED: three of four live markers in GroundTruth's test zones were "
                "invisible to it. A live LocationProxy sweep after zones_generate is "
                "the only complete test -- live positions for POSITION, this dump for "
                "per-type RADIUS. Clearing this check is NECESSARY, NOT SUFFICIENT."),
        }


def is_protected(rec: dict) -> bool:
    return bool(rec.get("prioritized") or rec.get("centerFirst")
                or int(rec.get("quantity", 0)) <= 5)


def location_dump(seed: str | None = None) -> str:
    """THIS seed's generator location dump, at CALL time: `roads/poi.py::
    location_dump`, the one resolver, keyed on the run's seed and
    header-checked.  Every settlement tool's `--locations` default was
    Pirate68's dump named by its content hash under `/tmp/settle/loc3/`, so
    a Fresh42 run read Pirate68's 12,301 locations; on Pirate68 this resolves
    to that same file.  `roads/` is APPENDED, so `roads/clear.py`
    never shadows `clearing/clear.py` for a later importer."""
    import sys  # noqa: PLC0415
    roads = str(Path(__file__).resolve().parent.parent / "roads")
    if roads not in sys.path:
        sys.path.append(roads)
    import poi  # noqa: PLC0415
    return str(poi.location_dump(seed))


def load(path: str | Path) -> Locations:
    d = json.loads(Path(path).read_text())
    types = {t["prefab"]: t for t in d.get("locationTypes", [])}
    if not types:
        raise ValueError(
            f"{path} carries no `locationTypes` block, so per-type piece reach is "
            f"unavailable and clearance would fall back to a global guess. Re-run "
            f"tools/seedscan/run_locscan.sh with the current LocScan.cs.")
    locs = d["locations"]
    xz = np.array([[l["x"], l["z"]] for l in locs], np.float32)
    names = [l["name"] for l in locs]
    prefabs = [l["prefab"] for l in locs]
    reach = np.empty(len(locs), np.float32)
    prot = np.zeros(len(locs), bool)
    for i, l in enumerate(locs):
        t = types.get(l["prefab"], {})
        reach[i] = max(float(l.get("exteriorRadius", 0.0)),
                       float(l.get("interiorRadius", 0.0)),
                       float(t.get("znviewReachM", 0.0)))
        prot[i] = is_protected(l)
    standoff = reach + MARGIN_M + np.where(prot, PROTECTED_EXTRA_M, 0.0).astype(np.float32)
    spawn = (-64.68, 3.31)
    for l in locs:
        if l["name"] == "StartTemple":
            spawn = (float(l["x"]), float(l["z"]))
            break
    by_type: dict[str, dict] = {}
    for p, t in types.items():
        by_type[p] = {
            "name": t.get("name"), "exteriorRadius": t.get("exteriorRadius"),
            "interiorRadius": t.get("interiorRadius"),
            "znviewPieces": t.get("znviewPieces"),
            "znviewReachM": t.get("znviewReachM"),
        }
    interior = np.array([float(l.get("interiorRadius") or 0.0) > 0.0
                         for l in locs], bool)
    return Locations(xz, names, prefabs, reach, prot, standoff, spawn, by_type,
                     interior)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locations", default=None,
                    help="default: THIS seed's dump (`location_dump`)")
    ap.add_argument("--at", nargs=2, type=float, metavar=("X", "Z"))
    ap.add_argument("--half-diagonal", type=float, default=0.0)
    a = ap.parse_args()
    L = load(a.locations or location_dump())
    print(f"{len(L)} instances, {len(L.by_type)} types, spawn {L.spawn}")
    print(f"protected instances {int(L.protected.sum())}, "
          f"reach [{L.reach.min():.1f}, {L.reach.max():.1f}] m, "
          f"standoff [{L.standoff.min():.1f}, {L.standoff.max():.1f}] m")
    excess = []
    for p, t in L.by_type.items():
        dec = max(float(t["exteriorRadius"] or 0), float(t["interiorRadius"] or 0))
        excess.append((float(t["znviewReachM"] or 0) - dec, p, dec, t["znviewReachM"],
                       t["znviewPieces"]))
    excess.sort(reverse=True)
    print("types whose MEASURED ZNetView reach exceeds their DECLARED radius:")
    for e, p, dec, m, pcs in excess:
        if e <= 0:
            break
        print(f"  {p:32s} declared {dec:5.1f}  measured {m:5.1f}  (+{e:4.1f})  pieces {pcs}")
    if a.at:
        x, z = a.at
        print(f"\nat ({x}, {z}) half-diagonal {a.half_diagonal} m")
        print("  nearest:", L.nearest(x, z))
        v = L.violations(x, z, a.half_diagonal)
        print(f"  violations: {len(v)}")
        for r in v[:8]:
            print("   ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
