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


def is_protected(rec: dict) -> bool:
    return bool(rec.get("prioritized") or rec.get("centerFirst")
                or int(rec.get("quantity", 0)) <= 5)


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
    return Locations(xz, names, prefabs, reach, prot, standoff, spawn, by_type)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locations", default="/tmp/settle/loc3/f6fe167f4fcd.json")
    ap.add_argument("--at", nargs=2, type=float, metavar=("X", "Z"))
    ap.add_argument("--half-diagonal", type=float, default=0.0)
    a = ap.parse_args()
    L = load(a.locations)
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
