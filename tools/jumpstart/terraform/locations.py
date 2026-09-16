#!/usr/bin/env python3
"""Refuse a terrain write that would cut ground out from under generated world content.

WHY THIS EXISTS, and why it is a separate module from the clearing stand-off.

`clearing/area.py` already refuses a CLEARING cylinder that reaches a generated
location, but it only runs when clearing runs.  `flatten.py` had no check of its
own and inherited none.  MEASURED on Ulfsland on 2026-09-15 at zone (-18, 28):
a 40 x 40 m flatten cut up to 6.65 m of ground out from under a `LocationProxy`
holding a `TreasureChest_meadows_buried`, and reported nothing.  That is the same
defect class as the POI damage recorded as unrepairable in the pre-wipe world,
where marker-based stand-off deleted location content because a location's
PIECES were measured reaching 43 m past their own marker.

TWO SOURCES, BECAUSE NEITHER ONE IS SUFFICIENT.

  * THE LIVE WORLD knows WHERE every generated location is, including the ~190
    POIs More_World_Locations injects, which no mod-free dump can see.  What it
    does NOT carry is which TYPE a marker is: every generated location plants a
    prefab literally named `LocationProxy`, so a live query gives a position and
    nothing else.  MEASURED: `findObjects -prefab LocationProxy -near x y z r`
    answers with positions; the type is inside the ZDO's `location` int hash,
    which `findObjects` does not print.
  * THE MOD-FREE DUMP (`tools/seedscan`, `LocScan.cs`) knows the TYPE and, since
    2026-09-15, each `ZoneLocation`'s own declared `exteriorRadius`,
    `interiorRadius`, `clearArea`, `quantity`, `prioritized` and `centerFirst`.
    What it does NOT contain is any modded location at all.

So the rule is: the LIVE markers decide what has to be respected, and the dump
is used only to IDENTIFY a live marker by position and thereby earn it a
SMALLER stand-off than the world-wide worst case.  A marker the dump cannot
identify -- i.e. any modded POI -- gets the worst case.  That ordering of
authority is the whole point: an unidentified marker must never be treated as
harmless.

MEASURED that this cross-read is necessary AND that it works, over the 24 zones
the ordering experiment generated on Ulfsland on 2026-09-15.  Four live
`LocationProxy` markers exist in them, and against the mod-free dump:

    (-1081.88, 1792.22)  WoodHouse2, d = 0.00 m  -> identified, radius 8 m
    (-1146.27, 1797.76)  nearest at 15.16 m      -> unidentified, worst case
    (-1150.16, 1853.63)  nearest at 29.69 m      -> unidentified, worst case
    (-1223.76, 1777.60)  nearest at 71.77 m      -> unidentified, worst case

So one in four matched EXACTLY -- which is why `IDENTITY_TOLERANCE_M` is half a
metre rather than a search radius -- and three do not appear in the mod-free
dump at all, which is what a More_World_Locations POI looks like from here.
Neither source alone would have been safe: the dump would have missed three
markers entirely, and the live query cannot name even the one it shares.

Do NOT read the dump's `placed` field as "this one is in the world".  MEASURED:
it is false on all 12,301 records, including `StartTemple`.

STAND-OFF, WITH ITS PROVENANCE ATTACHED PER TERM:

    standoff = max(exteriorRadius, interiorRadius)   MEASURED per type, from the
                                                     game's own ZoneLocation data
             + PIECE_OVERSHOOT_M                     INFERRED from ONE observation
             + SAFETY_MARGIN_M                       a TASTE pick, not a measurement

`PIECE_OVERSHOOT_M` is 11 m because a location whose declared exterior radius
was 32 m was measured with pieces 43 m from its marker.  That is a single
observation generalised to 177 location types, and it is recorded here as
INFERRED rather than measured precisely so nobody later reads it as a fact.  For
a `Waymarker01` with a declared radius of 3 m it is nearly four times the
declared radius and is probably far too conservative; being too conservative
costs a re-solved site, while being too small costs deleted world content.

The distance test is to the PAD RECTANGLE, not to the pad centre, because the
rectangle is what actually gets cut.  That is strictly tighter than a
half-diagonal circle and needs no margin to compensate for its own shape.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

# MEASURED per type, from ZoneLocation, in the dump.
# INFERRED from one observation: pieces 43 m out against a 32 m declared radius.
PIECE_OVERSHOOT_M = 11.0
# A taste pick. Not measured, not derived.
SAFETY_MARGIN_M = 15.0
# How close two positions must be to be the same location instance.  The dump
# and the world both report the marker's own position, so this is float noise
# plus the dump's 2-decimal rounding, not a search radius.
IDENTITY_TOLERANCE_M = 0.5

POSITION_RE = re.compile(
    r"^-Prefab: (\S+) Id: \S+ Position: \((-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)", re.M)
LOCATION_MARKER = "LocationProxy"


class TerrainWriteRefused(Exception):
    """The write would reach generated world content, and guessing is worse."""


@dataclass(frozen=True)
class Marker:
    """A live location marker, with whatever the dump could tell us about it."""

    x: float
    z: float
    y: float
    name: str            # "unidentified" when no dump record matches
    radius_m: float      # max(exteriorRadius, interiorRadius), or the worst case
    protected: bool      # prioritized or centerFirst or quantity <= 5
    identified: bool

    @property
    def standoff_m(self) -> float:
        return self.radius_m + PIECE_OVERSHOOT_M + SAFETY_MARGIN_M


def load_dump(path: Path) -> list[dict]:
    doc = json.loads(path.read_text())
    locations = doc.get("locations")
    if not locations:
        raise TerrainWriteRefused(f"{path}: no locations in the dump")
    return locations


def worst_case_radius_m(locations: list[dict]) -> float:
    """The largest declared radius anywhere in the dump.

    Used for a marker the dump cannot identify, which on this world means a
    More_World_Locations POI.  Taking the world's own maximum rather than a
    hand-picked number means the fallback tracks the data: MEASURED on Pirate68,
    the largest declared exterior radius among 12,301 instances is 32 m.
    """
    return max(max(float(loc.get("exteriorRadius") or 0.0),
                   float(loc.get("interiorRadius") or 0.0))
               for loc in locations)


def is_protected(loc: dict) -> bool:
    """Whether a location reads as world content rather than scenery.

    The game carries NO sacredness flag -- every field on `ZoneLocation` was
    checked and `unique` is False even on `bosslocation`, so `unique` is not the
    signal.  What the generator's own treatment does distinguish is scarcity and
    placement priority: StartTemple is quantity 1 prioritized centerFirst and
    `bosslocation` is quantity 3 prioritized, against `InfestedTree01` at
    quantity 700 and `Mistlands_RoadPost1` at 500.  This proxy is
    Settlements' and is reused here rather than invented a second time.
    """
    return bool(loc.get("prioritized") or loc.get("centerFirst")
                or int(loc.get("quantity") or 0) <= 5)


def rect_distance_m(px: float, pz: float, cx: float, cz: float,
                    half_w: float, half_d: float) -> float:
    """Distance from a point to an axis-aligned rectangle; 0.0 inside it."""
    dx = max(abs(px - cx) - half_w, 0.0)
    dz = max(abs(pz - cz) - half_d, 0.0)
    return math.hypot(dx, dz)


def live_markers(reply: str) -> list[tuple[float, float, float]]:
    """Marker positions out of a `findObjects -prefab LocationProxy` reply."""
    return [(float(x), float(y), float(z))
            for prefab, x, y, z in POSITION_RE.findall(reply)
            if prefab == LOCATION_MARKER]


def describe(locations: list[dict], positions: list[tuple[float, float, float]],
             cx: float, cz: float, half_w: float, half_d: float) -> list[Marker]:
    """Attach dump knowledge to live markers, worst case where it cannot."""
    fallback = worst_case_radius_m(locations)
    index = {(round(float(loc["x"]), 1), round(float(loc["z"]), 1)): loc
             for loc in locations}
    out = []
    for x, y, z in positions:
        match = None
        # The dump rounds to 2dp and the world does not, so probe the
        # neighbourhood of the rounded key rather than trusting equality.
        for ddx in (-0.1, 0.0, 0.1):
            for ddz in (-0.1, 0.0, 0.1):
                cand = index.get((round(x + ddx, 1), round(z + ddz, 1)))
                if cand and math.hypot(float(cand["x"]) - x,
                                       float(cand["z"]) - z) <= IDENTITY_TOLERANCE_M:
                    match = cand
                    break
            if match:
                break
        if match:
            radius = max(float(match.get("exteriorRadius") or 0.0),
                         float(match.get("interiorRadius") or 0.0))
            out.append(Marker(x=x, z=z, y=y, name=str(match.get("name") or "?"),
                              radius_m=radius, protected=is_protected(match),
                              identified=True))
        else:
            # Not in a mod-free dump: a modded POI. No radius, no quantity, no
            # priority -- so the worst declared radius in the world and
            # protected by default.
            out.append(Marker(x=x, z=z, y=y, name="unidentified",
                              radius_m=fallback, protected=True, identified=False))
    return sorted(out, key=lambda m: rect_distance_m(m.x, m.z, cx, cz, half_w, half_d))


def violations(markers: list[Marker], cx: float, cz: float,
               half_w: float, half_d: float) -> list[tuple[Marker, float]]:
    """Markers whose stand-off the pad rectangle reaches into."""
    hits = []
    for m in markers:
        gap = rect_distance_m(m.x, m.z, cx, cz, half_w, half_d)
        if gap < m.standoff_m:
            hits.append((m, gap))
    return hits


def probe_radius_m(locations: list[dict], half_w: float, half_d: float) -> float:
    """How far to look for markers: far enough that none can be missed.

    The pad's own half-diagonal plus the largest stand-off any marker in the
    world could claim.  A marker outside this cannot violate any stand-off, so
    the query is bounded by the DATA rather than by a guess, and it stays small
    enough that the reply cannot wedge the console.
    """
    return (math.hypot(half_w, half_d)
            + worst_case_radius_m(locations) + PIECE_OVERSHOOT_M + SAFETY_MARGIN_M)


def report(markers: list[Marker], hits: list[tuple[Marker, float]],
           cx: float, cz: float, half_w: float, half_d: float) -> str:
    lines = []
    for marker, gap in hits:
        source = "declared" if marker.identified else "WORST CASE (not in the mod-free dump)"
        lines.append(
            f"    {marker.name} at ({marker.x:.1f}, {marker.z:.1f}): {gap:.1f} m from the pad "
            f"rectangle, stand-off {marker.standoff_m:.1f} m "
            f"= {marker.radius_m:.0f} radius [{source}] + {PIECE_OVERSHOOT_M:g} overshoot "
            f"[INFERRED] + {SAFETY_MARGIN_M:g} margin [taste]"
            + ("" if marker.protected else "  (scenery by the quantity/priority proxy)"))
    nearest = ""
    if markers and not hits:
        m = markers[0]
        nearest = (f"    nearest marker {m.name} at ({m.x:.1f}, {m.z:.1f}), "
                   f"{rect_distance_m(m.x, m.z, cx, cz, half_w, half_d):.1f} m from the pad "
                   f"rectangle against a {m.standoff_m:.1f} m stand-off")
    return "\n".join(lines or ([nearest] if nearest else ["    no markers in range"]))
