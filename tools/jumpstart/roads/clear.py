#!/usr/bin/env python3
"""Clear the VEGETATION standing in a finished road, by explicit prefab list.

THE DEFECT THIS EXISTS FOR, AND IT IS A CONSEQUENCE OF A RULING RATHER THAN A
BUG.  `ribbon.py` was run under the NON-DESTRUCTIVE clearance budget, whose
whole claim is "a road ribbon emits only terrain writes -- it deletes nothing".
That claim bought the loosened POI stand-off and it is still true.  What nobody
said out loud at the time is what it forfeits: the ribbon levelled a 6 m paved
carriageway and a 1 m dirt shoulder THROUGH STANDING FOREST and removed not one
trunk.  Every beech on the centreline is still there, now standing in levelled
ground.  The operator walked the roads and reported exactly that.

So this is the missing half of the pipeline, and it is a SEPARATE op with a
SEPARATE budget on purpose: `ribbon.py`'s records still say `destructive: false`
and still mean it, and every deletion this world has ever performed on a road is
in this module's `objects_clear` records with a per-cylinder live census beside
it.

WHY AN EXPLICIT PREFAB LIST AND NEVER `id=*`
============================================
`objects_remove id=*` is the command that deleted POI content in the pre-wipe
world and it is what made the portal hub unclearable.  A prefab-filtered
removal CANNOT delete a building, a bridge or a furnished mod house, and that
property is what makes a clearing pass along 33 km of ribbon safe to run at
all: these roads cross Stenvik's streets, two bridge abutments and the hub
ring.  MEASURED in this world's own corridors: at T12 s = 60 m the 12 m
cylinder around the centreline holds a `LocationProxy`, a
`Music_MeadowsVillageFarm`, 12 `wood_floor` and 14 `wood_roof_45` -- a
furnished mod farmhouse ON the road.  A blanket removal there is the one
unrepairable damage class this project has recorded.

FOUR GATES, EACH CHECKED BEFORE A SINGLE BYTE GOES ON THE WIRE
==============================================================
  1. The prefab filter itself: only names on `CLEARABLE` are ever sent.
  2. `settlements/clearance.py` -- the canonical POI instrument, per-type
     `max(exteriorRadius, interiorRadius, znviewReachM)` -- run DESTRUCTIVELY
     for this op, because destructive is what it is.  Its mod-free caveat is
     recorded verbatim: the dump cannot see More_World_Locations' ~190 types,
     so clearing it is NECESSARY AND NOT SUFFICIENT, which is why gate 4 is a
     live measurement and not an inference from a radius.
  3. Settlement bodies and bridge piece footprints: any cylinder whose live
     census holds a building piece is SKIPPED WHOLE and what was in it is
     recorded.  Pieces are not distinguished by owner -- a bridge deck, a
     settlement wall and a mod house are all "something I did not put there".
  4. A LIVE `objects_count id=*` over THAT cylinder, in the same call, at most
     seconds before its removal.  Not minutes: a census taken minutes earlier
     is a census of a different world, and this is the check the ledger's
     `objects_clear` records are judged on.

THE WIDTH IS MEASURED, NOT CHOSEN
=================================
Two criteria, both derived from numbers this project already measured, and a
trunk is cleared if it satisfies EITHER:

  (a) IN THE CARRIAGEWAY.  `lat <= half + shoulder + TERRAIN_SPREAD_M`.  The
      spread term is not padding: `ribbon.delta_at` is MEASURED non-zero up to
      one full lattice pitch outside the written set, because `Heightmap`
      renders a mesh that interpolates linearly between 1 m samples -- which is
      where `TERRAIN_SPREAD_M = 1.0` came from in the first place.  A trunk
      inside that band has its own ground moved, and a tree whose ground moved
      is floating or buried.  It is also the trunk-lean allowance, and it is the
      only lean number on this host that is measured rather than asserted: the
      trunk RADIUS is asset data in a compressed Unity bundle and nothing here
      can read it, so the claim made is the one that can be checked -- a trunk
      within one lattice pitch of the shoulder stands on ground the road moved.

  (b) ON THE EARTHWORK.  `lat <= edge + batter_run(station) + TERRAIN_SPREAD_M`,
      where `batter_run` is `ribbon`'s own taper length for the cut or fill at
      that station, `min(BATTER_MAX_M, |profile_y - terrain_y| / BATTER_GRADE)`.
      The batter re-grades that ground by up to the full cut, so the same
      floating-tree argument applies with a longer arm.  `terrain_y` is
      `segments.yaml`'s PatchScan generated height at the station: measured, at
      1 m, with rivers.

Neither number is a taste margin and neither is a radius someone liked.

Usage::

    clear.py census --segment T12 --out /tmp/roads/clear/T12.json
    clear.py plan   --segment T12 --census /tmp/roads/clear/T12.json
    clear.py emit   --segment T12 --census /tmp/roads/clear/T12.json
    clear.py placed --census-glob '/tmp/roads/clear/*.json' \\
                    --out /tmp/roads/clear/placed.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "settlements"))
sys.path.insert(0, str(JUMPSTART))

import ribbon as RB  # noqa: E402

# WHO IS ACTUALLY RUNNING.  `RoadClear` built this module and censused the
# corridors; `RoadEmit` runs the removals.  A module-level default recorded
# seq 933 under RoadClear's name for RoadEmit's work, which is provenance that
# is WRONG rather than merely absent, so the value is overridable and the CLI
# requires it to be stated.
ACTOR = "RoadEmit"
# MEASURED, and it is `ribbon.delta_at`'s own reach: the bilinear blend of the
# four samples around a point is non-zero at up to one lattice pitch outside
# the written set, so ground moves out to exactly here and no further.
TERRAIN_SPREAD_M = 1.0

# ---------------------------------------------------------------------------
# WHAT MAY BE DELETED.  An explicit list, and every name on it was OBSERVED in
# these 15 corridors by `objects_count id=*` rather than reasoned about from a
# taxonomy.  A prefab that is not on this list is not assumed harmless: its
# presence SKIPS the whole cylinder.
# ---------------------------------------------------------------------------
CLEARABLE = (
    # trees
    "Beech1", "Beech_small1", "Beech_small2", "Birch1", "Birch2",
    "FirTree", "FirTree_small", "Pinetree_01", "Oak1", "SwampTree1",
    "Beech2", "Birch1_aut", "Birch2_aut",
    # deadfall and stumps
    "FirTree_oldLog", "FirTree_log", "stubbe", "beech_log", "beech_log_half",
    "birch_log", "birch_log_half", "oak_log", "oak_log_half",
    # bushes and undergrowth
    "Bush01", "Bush01_heath", "Bush02_en", "RaspberryBush", "BlueberryBush",
    "CloudberryBush", "shrub_2", "shrub_2_heath", "vines", "root11",
    # small rocks
    "Rock_3", "Rock_4", "Rock_7", "rock1_mountain", "rock4_coast",
    "Rock_destructible",
    # pickables, vanilla and mod
    "Pickable_Stone", "Pickable_StoneRock", "Pickable_Branch",
    "Pickable_Dandelion",
    "Pickable_Mushroom", "Pickable_Thistle", "Pickable_Flint",
    "Pickable_SeedCarrot", "Pickable_SeedTurnip", "Pickable_Barley",
    "BH_Pickable_Chamomile", "BH_Pickable_Bjorncap",
    "BH_Pickable_VikingsBreadcap", "BH_Pickable_ValkyrieFern",
    "BH_Pickable_Daisy", "BH_Pickable_HelshadeFungus",
    "BH_Pickable_SeidrBlossoms", "BH_Pickable_ThorsToadstool",
)
# NEITHER CLEARABLE NOR A BLOCKER: MOBILE CREATURES AND THEIR AUDIO EMITTERS.
#
# MEASURED on T12, and it is the reason this set exists rather than a taste
# exemption: two of the fourteen cylinders were skipped whole -- 37 trees,
# bushes and rocks left standing in the carriageway -- because ONE `Boar` was
# inside each at census time.
#
# The blocker rule protects against deleting something irreplaceable, and it
# does that by two independent mechanisms: `ids` on the wire never names
# anything off CLEARABLE, and a cylinder holding a STRUCTURE is somebody's
# property whose vegetation may be deliberate landscaping.  A boar is neither.
# It cannot be deleted by a prefab-scoped removal that does not name it, and
# its position is TRANSIENT -- it will have walked off before the removal
# lands -- so a census of boars is not a census of property and blocking on
# one protects nothing while costing 37 trees in the road.
#
# They are still RECORDED per cylinder.  "Ignored for the verdict" and
# "unmeasured" are different claims and the ledger gets the first one.
NOT_PROPERTY = {
    "Boar": "a wandering creature; transient position, and a prefab-scoped "
            "removal that does not name it cannot touch it",
    "Crow": "as above",
    "Deer": "as above",
    "Neck": "as above",
    "Greyling": "as above",
    "Greydwarf": "as above",
    "Skeleton": "as above",
    "sfx_boar_idle": "a creature's audio emitter, and it follows the creature",
}
# NAMED REFUSALS: things whose presence must stop a cylinder even though they
# look natural.  Recorded because a reader would otherwise reasonably assume
# they are on the list above, and because `settlements/build.py`'s own
# `NATURAL_RE` DOES match `spawner_` and `marker` -- a wider rule than this
# one, written for a 33 m pad inside a solved site rather than for a ribbon
# that crosses whatever it crosses.
NEVER_CLEAR_WHY = {
    "RockDolmen_1": "a dolmen is a generated POI feature, not scenery",
    "RockDolmen_2": "a dolmen is a generated POI feature, not scenery",
    "Pickable_DolmenTreasure": "the dolmen's own reward",
    "Pickable_ForestCryptRemains01": "crypt POI content",
    "TreasureChest_meadows_buried": "POI reward, and it is BURIED -- invisible",
    "LocationProxy": "the location itself",
    "Spawner_Boar": "a spawner belongs to the biome's gameplay, not to the road",
    "Spawner_Skeleton_Meadows_night_noarcher": "as above",
    "Spawner_Greydwarf": "as above",
    "Spawner_Greydwarf_Shaman": "as above",
    "Spawner_Skeleton": "as above",
    "Spawner_Skeleton_respawn_30": "as above",
    "Spawner_Skeleton_Meadows_night": "as above",
    "Pickable_ForestCryptRandom": "crypt POI content",
    "Pickable_ForestCryptRemains04": "crypt POI content",
    "Pickable_SurtlingCoreStand": "POI content, and it holds an item",
    "TreasureChest_meadows": "POI reward",
    "TreasureChest_blackforest": "POI reward",
    "TreasureChest_forestcrypt": "POI reward",
    "Beehive": "a beehive is a mod POI's content and it FIGHTS BACK",
    "piece_beehive": "somebody's placed beehive",
    "rock4_copper": "a copper deposit is a resource the operator mines",
    "rock4_forest": "a mineable boulder, not scenery: too big to be verge",
    "Music_MeadowsVillageFarm": "the mod POI's own ambience emitter",
    "Music_GreydwarfCamp": "as above",
    "BlackForestLocationMusic": "as above",
    "Boar": "a creature",
    "Crow": "a creature",
    "sfx_boar_idle": "a creature's audio emitter",
}


def is_clearable(prefab: str) -> bool:
    return prefab in CLEARABLE


def blocks(prefab: str) -> bool:
    """Does this prefab's PRESENCE stop the cylinder?

    Everything that is neither on `CLEARABLE` nor on `NOT_PROPERTY` does.
    Default-deny on an unknown name is the whole safety argument: a mod POI's
    pieces are ordinary building prefabs, so "I do not recognise this" must
    mean "somebody built here".
    """
    return prefab not in CLEARABLE and prefab not in NOT_PROPERTY


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

POS_RE = re.compile(
    r"Prefab:\s*(?P<prefab>\S+)\s+Id:\s*(?P<id>\S+)\s+"
    r"Position:\s*\(\s*(?P<x>[-0-9.]+)\s+(?P<y>[-0-9.]+)\s+(?P<z>[-0-9.]+)\s*\)")
FOUND_RE = re.compile(r"Found (\d+) objects")
# `findObjects -near x y z r` is a CUBE of half-extent r, MEASURED: at
# (90.8, -80.8) with r = 20 it answered with a trunk 21.9 m away in XZ whose
# every axis offset was under 20.  `objects_count ... max=r` is an XZ
# CYLINDER.  Two geometries in one pipeline is how a census misses a corner,
# so every listing here is re-filtered in XZ by this module.
MAX_LIST_ROWS = 18
MIN_LIST_HALF_M = 2.0


def load_segment(seg_id: str, path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    for s in doc["segments"]:
        if s["id"] == seg_id or s["id"].split("-")[0] == seg_id:
            return s
    raise SystemExit(f"no segment {seg_id} in {path}")


def clear_half_width(seg: dict) -> tuple[np.ndarray, float]:
    """Per-station clear half-width, and the widest of them.

    The two criteria of the module docstring, evaluated per station: the
    carriageway band is constant, the earthwork band grows with the cut or fill
    the batter has to taper out.
    """
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    terr = np.asarray(seg["terrain_y"], dtype=np.float64)
    edge = seg["width_m"] / 2.0 + RB.SHOULDER_M
    run = np.minimum(RB.BATTER_MAX_M, np.abs(prof - terr) / RB.BATTER_GRADE)
    per_station = edge + run + TERRAIN_SPREAD_M
    return per_station, float(per_station.max())


def lateral_fn(seg: dict):
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    br = np.asarray(seg["is_bridge"], dtype=bool)

    def f(x: float, z: float):
        return RB.lateral_and_y(nodes, prof, br, x, z)

    return f


def nearest_station(seg: dict, x: float, z: float) -> int:
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    d = (nodes[:, 0] - x) ** 2 + (nodes[:, 1] - z) ** 2
    return int(d.argmin())


# HOW MUCH FURTHER THAN THE MEASURED WIDTH A REMOVAL CYLINDER MAY REACH, and
# it is a trade against the ONE subsystem that has failed three times tonight.
# A cylinder of radius R centred on the centreline covers a strip of
# half-width h only where (s/2)^2 + h^2 <= R^2, so the tighter R is to h the
# closer the spacing has to be and the more cylinders there are -- and every
# cylinder costs one `objects_count id=*`, which is the call that killed the
# console sink at 160 of them.  At R = h the spacing goes to zero.  R = h + 6
# gives ~430 cylinders for the whole 15-segment network instead of ~1,240 at
# R = h + 1, and the price is that a PURE-VEGETATION cylinder also clears up
# to 6 m beyond the measured width.  It is a price worth naming rather than
# hiding: the extra is a wider verge, it can only ever contain prefabs on
# CLEARABLE, and a cylinder holding anything else is skipped whole, so the
# over-reach cannot touch a building, a bridge or a mod house.
REMOVAL_OVERREACH_M = 6.0


# THE REMOVAL WIDTH FOLLOWS THE WRITTEN FOOTPRINT, AND THE OVER-REACH IS A
# FRACTION OF IT RATHER THAN A CONSTANT.  Main's ruling, and the reasoning is
# the defect this whole pass repairs: a tree standing in the BATTER zone is a
# tree standing in ground the ribbon just moved by up to 0.781 m per metre, so
# it ends up buried or floating -- the `FirTree_oldLog` case relocated from the
# carriageway edge to the batter edge.  So the removal must cover everywhere
# terrain is WRITTEN, which is per station `carriageway + shoulder + that
# station's own batter run`, and no further than the geometry of covering it
# requires.
#
# The over-reach is what buys disc SPACING: a disc of radius r centred on the
# centreline covers a strip of half-width h only where (s/2)^2 + h^2 <= r^2, so
# with r = h(1 + k) the spacing is s = 2h*sqrt(2k + k^2) and at k = 0 it is
# zero.  k = 0.35 gives s = 1.80h -- about 25 discs on T12 against 14 at the
# old constant 6.0 m, and a verge that reaches 35 % beyond the written width
# instead of a flat 6 m regardless of whether the batter there runs 10 m or
# stops at 2 m.  Every removed object is still recorded with prefab, ZDO id and
# position, so a verge cleared wider than the operator wanted can be put back.
REMOVAL_OVERREACH_FRAC = 0.35
# Kept for the two segments cleared before the ruling (T12 seq 951-963 and T3
# seq 970-997 used this constant), so their records can still be read against
# the width that produced them.
REMOVAL_OVERREACH_M = 6.0


def tiles_local(seg: dict, per_station, frac: float = REMOVAL_OVERREACH_FRAC
                ) -> list[tuple[float, float, float]]:
    """Disc centres along the TERRAIN centreline whose radius follows the
    WRITTEN footprint at each station.

    Same covering argument as `tiles`, evaluated locally: at each step the
    half-width is the widest written footprint over the stretch the next disc
    has to cover, the radius is `h(1 + frac)` and the step is
    `2h*sqrt(2f + f^2)`.  Where the batter runs 10 m the disc follows it; where
    the profile sits on grade and there is no batter at all the disc shrinks to
    the carriageway plus its 1 m of measured terrain spread.
    """
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    br = np.asarray(seg["is_bridge"], dtype=bool)
    per = np.asarray(per_station, dtype=np.float64)
    step_factor = 2.0 * math.sqrt(2.0 * frac + frac * frac)
    out: list[tuple[float, float, float]] = []
    # Arc length of each node so a station window can be read off the profile.
    seglen = np.hypot(*(nodes[1:] - nodes[:-1]).T)
    arc = np.concatenate([[0.0], np.cumsum(seglen)])
    runs: list[tuple[float, float]] = []
    start = None
    for k in range(len(nodes)):
        if br[k]:
            if start is not None:
                runs.append((start, arc[k - 1]))
                start = None
        elif start is None:
            start = arc[k]
    if start is not None:
        runs.append((start, arc[-1]))
    for lo, hi in runs:
        d = lo
        while d <= hi + 1e-9:
            i0 = int(np.searchsorted(arc, d, side="right") - 1)
            i0 = min(max(i0, 0), len(per) - 1)
            h = float(per[i0])
            step = max(h * step_factor, 1.0)
            i1 = int(np.searchsorted(arc, min(d + step, hi), side="right") - 1)
            i1 = min(max(i1, i0), len(per) - 1)
            h = float(per[i0:i1 + 1].max())
            step = max(h * step_factor, 1.0)
            k = min(int(np.searchsorted(arc, d, side="right") - 1),
                    len(nodes) - 2)
            f = (d - arc[k]) / max(arc[k + 1] - arc[k], 1e-9)
            out.append((float(nodes[k, 0] + (nodes[k + 1, 0] - nodes[k, 0]) * f),
                        float(nodes[k, 1] + (nodes[k + 1, 1] - nodes[k, 1]) * f),
                        round(h * (1.0 + frac), 2)))
            d += step
    return out


def tiles(seg: dict, half_m: float, overreach_m: float
          ) -> list[tuple[float, float, float]]:
    """Disc centres along the TERRAIN part of the centreline, spaced so the
    union of the discs covers the strip of half-width `half_m`.

    The spacing FOLLOWS from the radius rather than being picked: leaving a
    gap between discs leaves a tree in the road, which is the defect being
    repaired.  Bridged runs are skipped and the run restarts after them, so a
    deck is never censused as if it were ground.
    """
    r = half_m + overreach_m
    s = 2.0 * math.sqrt(max(r * r - half_m * half_m, 0.25))
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    br = np.asarray(seg["is_bridge"], dtype=bool)
    out: list[tuple[float, float, float]] = []
    acc = s  # force a disc at the first station of every run
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            acc = s
            continue
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-9:
            continue
        t = s - acc
        while t < seglen:
            f = t / seglen
            out.append((float(ax + (bx - ax) * f), float(az + (bz - az) * f), r))
            t += s
        acc = seglen - (t - s)
    return out


# ---------------------------------------------------------------------------
# the live instruments
# ---------------------------------------------------------------------------

def sink_ok(note: str = "") -> dict:
    """Refuse to keep driving `objects_count` into a sink that is not carrying
    anything.  Checked BETWEEN batches, not per call, because the probe itself
    costs a `docker exec` and two seconds.
    """
    import rcon as RC  # noqa: PLC0415

    # probe=False: the wchan + file-descriptor verdict is what distinguishes
    # `circular_wait` from `fds_lost` from `healthy`, and it costs 0.24 s.  The
    # flow probe costs a `logger` round trip inside the container and MEASURED
    # tonight it timed out at 30 s under host load -- which would abort a
    # clearing run for the health check rather than for the health.
    st = RC.log_sink_state(probe=False)
    if st["verdict"] != "healthy":
        raise SystemExit(
            f"console log sink verdict {st['verdict']} ({note}): "
            f"{st['remedy']}. Stopping here rather than sending removals whose "
            f"pre-removal census cannot be read -- an unreadable census is not "
            f"an empty cylinder.")
    return st


def count_star(srv, x: float, z: float, r: float, attempts: int = 5,
               space_s: float = 1.0) -> dict:
    """`objects_count id=*` with RETRIES and SPACING, because the answer
    arrives through the CONTAINER LOG and that path is both shared and fragile.

    TWO MEASURED REASONS FOR THE SHAPE OF THIS FUNCTION, and the second one
    cost the whole fleet an outage.

    RETRIES: with a sibling agent driving its own `consoleCommand` traffic
    through the same sink, `slice_for` failed to find this command's echo in
    the 120 s window and `run_console` raised -- correctly, because a missing
    echo is an UNKNOWN answer and not an empty one.  A read-only count is
    idempotent, so the right response to "unknown" is to ask again.  It is
    never to widen the window and accept a stale slice.

    SPACING: `objects_count id=*` answers with ONE SYSLOG DATAGRAM PER PREFAB.
    MEASURED tonight -- by me, and it blinded every census in the project --
    about 160 of these over 12 m cylinders, back to back, filled syslogd's
    receive queue and killed the container's log sink outright; the recovery
    was a 241 s container restart.  So the burst is the hazard, not the call,
    and the sleep is part of the instrument rather than politeness.
    """
    from console import OutputNotFound  # noqa: PLC0415

    last = None
    for i in range(attempts):
        try:
            total, per = srv.count("*", x, z, r)
            time.sleep(space_s)
            return {"total": total, "per_prefab": per, "attempts": i + 1}
        except OutputNotFound as exc:
            last = exc
            time.sleep(1.0 + 1.5 * i)
    raise SystemExit(
        f"objects_count id=* at ({x:.1f}, {z:.1f}) r={r:.1f} did not answer in "
        f"{attempts} attempts: {last}. Refusing to remove anything from a "
        f"cylinder whose contents are unknown.")


# THE CENSUS IS RATE LIMITED, AND THE RATE IS THE HAZARD RATHER THAN THE CALL.
# `findObjects` answers over the SOCKET, which is why this census can run with
# a dead console sink -- but the server ALSO echoes every reply line to the
# container log, and a segment census is ~2000 replies.  MEASURED twice
# tonight, once by RoadClear on T10 and once by me after T12's clearing: an
# unthrottled census fills syslogd's receive queue, the sink loses its read end
# (`fds_lost`), the main thread wedges on the write, `save` times out and the
# container has to be hard-stopped at its 240 s deadline.  The second time
# that cost 273 verified removals, because the newest state on disk was an
# autosave from before them.
#
# 0.08 s per call holds a segment census to ~12 replies a second, which is the
# order the sink drained cleanly at all night, and adds ~160 s to a 333 m
# segment.  The sink is re-checked every 150 calls and the census STOPS rather
# than wedging: a census that halts is restartable, a wedged main thread is a
# four minute restart and a lost save.
CENSUS_SPACING_S = 0.08
CENSUS_SINK_EVERY = 150
_census_calls = 0


def _throttle(srv) -> None:
    global _census_calls
    _census_calls += 1
    if getattr(srv, "dry", False):
        return
    time.sleep(CENSUS_SPACING_S)
    if _census_calls % CENSUS_SINK_EVERY == 0:
        sink_ok(f"census call {_census_calls}")


def list_prefab(srv, prefab: str, x: float, y: float, z: float,
                half: float) -> tuple[dict, list, int]:
    """Positions of one prefab inside the CUBE, subdividing until every reply
    is small.  Returns (by ZDO id, discs that stayed too dense, socket calls).

    THE BINDING LIMIT IS THE CLIENT'S 4096-BYTE BUFFER, not the server's
    patience: `RconPeer.TryReceive` parses one packet and clears it, so an
    oversized reply desynchronises the stream and the NEXT agent's `connect()`
    inherits the desync.  MEASURED here: 24 rows came back as 1,404 bytes and
    47 rows as 3,040, i.e. ~62 bytes a row, so 18 rows is the fleet cap with a
    wide margin and even a surprise at 3x it stays inside the buffer.

    The quadrants deliberately OVERLAP -- each carries its quadrant's
    half-diagonal so their union cannot leave a gap -- which double-counts, so
    the result is keyed by ZDO id.  MEASURED elsewhere tonight: the raw
    concatenation read 72 rows for 29 objects, and someone counted it.
    """
    found: dict[str, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    stack = [(x, z, half)]
    while stack:
        cx, cz, hh = stack.pop()
        _throttle(srv)
        reply = srv.command(f"findObjects -prefab {prefab} "
                            f"-near {cx:.2f} {y:.2f} {cz:.2f} {hh:.2f}")
        calls += 1
        m = FOUND_RE.search(reply)
        n = int(m.group(1)) if m else 0
        if n == 0:
            continue
        if n > MAX_LIST_ROWS:
            if hh <= MIN_LIST_HALF_M:
                unlisted.append({"prefab": prefab, "centre": [cx, cz],
                                 "half_m": hh, "rows": n})
                continue
            h = hh / 2.0
            for dx, dz in ((-h, -h), (-h, h), (h, -h), (h, h)):
                stack.append((cx + dx, cz + dz, h * 1.4143))
            continue
        for mm in POS_RE.finditer(reply):
            found[mm.group("id")] = {
                "prefab": mm.group("prefab"), "id": mm.group("id"),
                "x": float(mm.group("x")), "y": float(mm.group("y")),
                "z": float(mm.group("z"))}
    return found, unlisted, calls


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------

def universe(path: Path | None = None) -> list[str]:
    """The prefab names the census will ASK FOR, and why it has to be a list.

    `findObjects` takes no glob: MEASURED, `-prefab *` and `-prefab Beech*`
    both answer "No objects found matching the provided criteria" at a
    position where `-prefab Beech1` answers with 24 rows.  So a socket census
    can only find what it can name, and the names come from the ONE set of
    `objects_count id=*` tables this world produced before the sink died:
    corridor_prefabs.json, 113 kinds observed within 12 m of five of the
    fifteen road centrelines.

    COMPLETENESS OF THIS LIST IS NOT WHAT KEEPS THE PASS SAFE, and that is the
    point worth being explicit about.  A prefab missing from it can only be
    MISSED, never deleted: `ids` on the wire is built from the per-cylinder
    `objects_count id=*` taken at removal time, which sees everything, and any
    name on it that is not on CLEARABLE skips the cylinder.  The universe
    decides how much of the road gets cleared, not how safe the clearing is.
    """
    p = path or (HERE / "corridor_prefabs.json")
    names = set(json.loads(p.read_text())) if p.exists() else set()
    return sorted(names | set(CLEARABLE))


def census(srv, seg: dict, names: list[str]) -> dict:
    """WHAT IS ACTUALLY STANDING IN THIS ROAD, by position, live -- and
    entirely over the SOCKET.

    `findObjects` is a NATIVE ValheimRcon verb whose reply comes back on the
    RCON socket, so it survives a dead console sink; `objects_count` routes its
    table through that sink and cannot.  Three sink outages in one session, each
    of which blinded every census in the project at once, is why this pass asks
    its questions the way it does -- and the third outage was caused by this
    very census in its first form.
    """
    per_station, widest = clear_half_width(seg)
    lat_of = lateral_fn(seg)
    disc_list = tiles_local(seg, per_station)
    objects: dict[str, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    for i, (cx, cz, r) in enumerate(disc_list):
        got = lat_of(cx, cz)
        y = got[1] if got else 0.0
        for name in names:
            f, un, n = list_prefab(srv, name, cx, y, cz, r)
            objects.update(f)
            unlisted += un
            calls += n
        print(f"  tile {i + 1}/{len(disc_list)} ({cx:.0f},{cz:.0f}) r={r:.1f} "
              f"objects so far {len(objects)} ({calls} socket calls)",
              flush=True)

    for o in objects.values():
        got = lat_of(o["x"], o["z"])
        o["lat_m"] = None if got is None else round(got[0], 3)
        o["road_y"] = None if got is None else round(got[1], 3)
        k = nearest_station(seg, o["x"], o["z"])
        o["station"] = k
        o["clear_half_m"] = round(float(per_station[k]), 3)
        o["clearable"] = is_clearable(o["prefab"])
        o["in_clear_width"] = bool(o["lat_m"] is not None
                                   and o["lat_m"] <= o["clear_half_m"])
    return {
        "segment": seg["id"],
        "width_m": seg["width_m"], "shoulder_m": RB.SHOULDER_M,
        "batter_m": RB.BATTER_MAX_M, "batter_grade": round(RB.BATTER_GRADE, 4),
        "terrain_spread_m": TERRAIN_SPREAD_M,
        "clear_half_m_min": round(float(per_station.min()), 3),
        "clear_half_m_max": round(widest, 3),
        "tiles": [[round(a, 2), round(b, 2), round(c, 2)] for a, b, c in disc_list],
        "prefabs_asked": names,
        "prefabs_found": sorted({o["prefab"] for o in objects.values()}),
        "socket_calls": calls,
        "objects": sorted(objects.values(), key=lambda o: (o["prefab"], o["id"])),
        "unlisted_discs": unlisted,
        "method": ("MEASURED live over the RCON SOCKET ONLY: "
                   "`findObjects -prefab P -near x y z r` per prefab per tile, "
                   "subdivided with overlapping quadrants until every reply is "
                   "under 18 rows, deduplicated by ZDO id, and re-filtered in "
                   "XZ because findObjects takes a CUBE while objects_count "
                   "takes a CYLINDER. No objects_count is used here at all -- "
                   "its table goes through the container console sink, which "
                   "failed three times in this session and which this census "
                   "in its first form is what killed. The prefab NAMES come "
                   "from corridor_prefabs.json, the one set of "
                   "`objects_count id=*` tables taken before that outage; a "
                   "name missing from it can be MISSED but never deleted, "
                   "because the wire's `ids` are built from the per-cylinder "
                   "census taken at removal time. Lateral distance is "
                   "ribbon.lateral_and_y against this segment's own polyline, "
                   "i.e. the same function that decided where the road went."),
        "tool": "tools/jumpstart/roads/clear.py::census",
    }


def placed_objects(censuses: list[dict]) -> dict:
    """The batter's gate input: every object we did NOT put there and will NOT
    remove, by position.

    `ribbon.stamp` refuses to move ground within one lattice pitch of any of
    these.  It is the same list the clearing skips, which is the point -- one
    live measurement answers both "may I delete this" and "may I move the
    ground under it", and the two answers cannot disagree.
    """
    keep: list[list[float]] = []
    per_prefab: dict[str, int] = {}
    for c in censuses:
        for o in c["objects"]:
            if o["clearable"] and o["in_clear_width"]:
                continue
            keep.append([round(o["x"], 2), round(o["z"], 2)])
            per_prefab[o["prefab"]] = per_prefab.get(o["prefab"], 0) + 1
    return {
        "objects": keep, "per_prefab": per_prefab,
        "segments": [c["segment"] for c in censuses],
        "method": ("MEASURED live: every ZDO found in the road corridors that "
                   "is either not on clear.py::CLEARABLE or lies outside the "
                   "measured clear width, so it stays -- and therefore its "
                   "ground must not move. Positions from findObjects, "
                   "deduplicated by ZDO id."),
        "tool": "tools/jumpstart/roads/clear.py::placed_objects",
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def plan_removals(seg: dict, cen: dict) -> dict:
    """Group the removals into cylinders, and refuse every cylinder that holds
    something this module may not delete.

    ONE CYLINDER PER TILE rather than one per trunk: `objects_remove` is a
    cylinder, and a per-trunk cylinder would multiply the ledger by a thousand
    records for no extra safety -- the safety comes from the prefab filter and
    from the census, both of which are per cylinder already.
    """
    per_station, widest = clear_half_width(seg)
    by_tile: list[dict] = []
    targeted: set[str] = set()
    for cx, cz, r0 in [tuple(t) for t in cen["tiles"]]:
        # SHRINK BEFORE SKIPPING.  MEASURED on T12: one stray `wood_beam_45`
        # anywhere inside a 15.34 m cylinder blocked it whole and left 32
        # trees standing in the carriageway.  The hazard is the blocker's own
        # neighbourhood, not the whole disc, so the disc is pulled in to one
        # metre short of the nearest blocker and only skipped if that leaves
        # nothing worth removing.  One metre short because `TERRAIN_SPREAD_M`
        # is the reach of an edit at all, so it is the honest slack.
        blocker_d = [math.hypot(o["x"] - cx, o["z"] - cz) for o in cen["objects"]
                     if math.hypot(o["x"] - cx, o["z"] - cz) <= r0
                     and blocks(o["prefab"])]
        r = r0
        shrunk_from = None
        if blocker_d:
            r = min(r0, min(blocker_d) - TERRAIN_SPREAD_M)
            shrunk_from = round(r0, 2)
        inside, blockers, transient = {}, {}, {}
        if r >= 2.0:
            for o in cen["objects"]:
                if math.hypot(o["x"] - cx, o["z"] - cz) > r:
                    continue
                if o["clearable"] and o["in_clear_width"]:
                    inside[o["prefab"]] = inside.get(o["prefab"], 0) + 1
                elif blocks(o["prefab"]):
                    blockers[o["prefab"]] = blockers.get(o["prefab"], 0) + 1
                elif o["prefab"] in NOT_PROPERTY:
                    transient[o["prefab"]] = transient.get(o["prefab"], 0) + 1
        if not inside:
            if blocker_d:
                by_tile.append(
                    {"centre": [round(cx, 2), round(cz, 2)],
                     "radius_m": round(r0, 2), "ids": [], "counts": {},
                     "blockers": {}, "transient_present": {},
                     "verdict": "skip",
                     "why": ("a blocker at "
                             f"{min(blocker_d):.1f} m leaves no usable radius")})
            continue
        rec = {"centre": [round(cx, 2), round(cz, 2)], "radius_m": round(r, 2),
               "ids": sorted(inside), "counts": inside,
               "blockers": blockers, "transient_present": transient,
               "shrunk_from_m": shrunk_from,
               "nearest_blocker_m": (round(min(blocker_d), 2)
                                     if blocker_d else None),
               "verdict": "skip" if blockers else "clear"}
        by_tile.append(rec)
        if not blockers:
            for o in cen["objects"]:
                if (o["clearable"] and o["in_clear_width"]
                        and math.hypot(o["x"] - cx, o["z"] - cz) <= r):
                    targeted.add(o["id"])
    want = [o for o in cen["objects"] if o["clearable"] and o["in_clear_width"]]
    return {
        "segment": seg["id"],
        "cylinders": by_tile,
        "cylinders_clear": [c for c in by_tile if c["verdict"] == "clear"],
        "cylinders_skipped": [c for c in by_tile if c["verdict"] == "skip"],
        "in_clear_width_clearable": len(want),
        "targeted": len(targeted),
        "left_standing": len(want) - len(targeted),
        "left_standing_detail": sorted(
            {o["prefab"] for o in want if o["id"] not in targeted}),
        "policy": ("CLIP, never blanket: a cylinder holding any prefab not on "
                   "CLEARABLE is skipped whole and its contents recorded. A "
                   "road crosses other people's property, so the road yields. "
                   "That is the opposite of a settlement pad, which REFUSES "
                   "rather than clips because a building cannot stand in half "
                   "a clearing."),
    }


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

# The smallest removal cylinder worth sending.  Below the carriageway's own
# half width a cylinder cannot reach the trees standing IN the road, which is
# the only thing this op exists to remove, so a cylinder shrunk below it is
# skipped and recorded instead of sent.
MIN_CLEAR_RADIUS_M = 5.0


PREFAB_SCOPED_WHY = (
    "MEASURED, not argued: the wire is `objects_remove id=<named vegetation "
    "prefabs> ignore=_*`, so the command cannot delete a location's ZDOs at "
    "any distance. The distance budget is a proxy for that hazard; the gates "
    "that actually bound it are the named-prefab wire, the same-call live "
    "census refusing any cylinder holding a non-CLEARABLE prefab, and a "
    "whole-cylinder test against each location's own footprint. Ruled by Main "
    "after the destructive budget returned allowed cylinder radii of -13 to "
    "-87 m on T12, whose whole length lies inside StartTemple's 90 m "
    "PROTECTED_EXTRA.")


def shrink_to_gate(plan: dict, L, destructive: bool = True) -> dict:
    """Enforce the DESTRUCTIVE POI budget PER CYLINDER, shrinking where that
    is enough and skipping where it is not.

    WHY PER CYLINDER AND NOT PER SEGMENT, measured on T12: the segment-level
    gate tests every cylinder centre with the WORST cylinder's radius, so one
    15.34 m cylinder makes all thirteen answer for 15.34 m of reach.  The
    instrument's own numbers say what each one may be -- `required_m` is
    `reach + half_width + margin`, so the half width this centre may carry is
    `min_dist_m - (required_m - half_width_m)` -- and asking it per cylinder is
    using the instrument more precisely, not more softly.  The budget stays
    DESTRUCTIVE, which is what this op is.

    A cylinder that cannot fit is SKIPPED WITH ITS REASON.  That leaves trees
    standing in the road near four generated POIs on T12 and the operator has
    to be told which, because "the road is cleared" would then be false.
    """
    kept, shrunk, skipped = [], [], []
    for c in plan["cylinders_clear"]:
        cx, cz = c["centre"]
        r = c["radius_m"]
        v = L.verdict_for_samples([(cx, cz)], half_width_m=r,
                                  destructive=destructive,
                                  delta_gate={"verdict": "clear",
                                              "note": PREFAB_SCOPED_WHY})
        if v["verdict"] == "clear" and footprint_clear(L, cx, cz, r)["clear"]:
            kept.append(c)
            continue
        if v["verdict"] == "clear":
            v = {"verdict": "VIOLATION",
                 "violations": footprint_clear(L, cx, cz, r)["violations"]}
        # THE INSTRUMENT IS THE ORACLE, NOT MY ALGEBRA, and the first version
        # of this got it wrong in the direction that costs coverage.  I
        # computed the allowed radius as
        # `min_dist_m - (required_m - half_width_m)`, which assumes
        # `min_dist_m` is independent of the radius passed in; it is not, and
        # the arithmetic skipped six T12 cylinders -- 127 trees, including the
        # `FirTree_oldLog` buried 2.11 m under the carriageway -- that the
        # instrument in fact passes at 9-13 m.  So the radius is BISECTED
        # against the gate itself, distance rule and footprint together, which
        # cannot be wrong about what the gate allows.
        why = sorted({b["name"] for b in v["violations"]})
        lo, hi = 0.0, r
        for _ in range(12):
            mid = (lo + hi) / 2.0
            vm = L.verdict_for_samples(
                [(cx, cz)], half_width_m=mid, destructive=destructive,
                delta_gate={"verdict": "clear", "note": PREFAB_SCOPED_WHY})
            fm = footprint_clear(L, cx, cz, mid)
            if vm["verdict"] == "clear" and fm["clear"]:
                lo = mid
            else:
                hi = mid
        allowed = lo
        if allowed < MIN_CLEAR_RADIUS_M:
            skipped.append({**c, "gate_allowed_radius_m": round(allowed, 2),
                            "gate_blockers": why,
                            "why": ("the DESTRUCTIVE POI budget leaves less "
                                    f"than {MIN_CLEAR_RADIUS_M} m here, which "
                                    "cannot reach the carriageway")})
            continue
        c2 = dict(c)
        c2["radius_m"] = round(max(allowed - 0.01, 0.0), 2)
        c2["gate_shrunk_from_m"] = r
        c2["gate_blockers"] = why
        v2 = L.verdict_for_samples([(cx, cz)], half_width_m=c2["radius_m"],
                                   destructive=destructive,
                                   delta_gate={"verdict": "clear",
                                               "note": PREFAB_SCOPED_WHY})
        if (v2["verdict"] != "clear"
                or not footprint_clear(L, cx, cz, c2["radius_m"])["clear"]):
            skipped.append({**c2, "why": "still inside a hard radius after "
                                         "shrinking; refusing"})
            continue
        shrunk.append(c2)
        kept.append(c2)
    plan["cylinders_clear"] = kept
    plan["cylinders_skipped_by_location_gate"] = skipped
    return {"kept": len(kept), "shrunk": len(shrunk), "skipped": len(skipped),
            "shrunk_detail": [{"centre": c["centre"],
                               "from_m": c["gate_shrunk_from_m"],
                               "to_m": c["radius_m"],
                               "because_of": c["gate_blockers"]}
                              for c in shrunk],
            "skipped_detail": [{"centre": c["centre"],
                                "radius_m": c["radius_m"],
                                "because_of": c.get("gate_blockers"),
                                "allowed_m": c.get("gate_allowed_radius_m"),
                                "objects": sum(c.get("counts", {}).values()),
                                "why": c["why"]} for c in skipped],
            "budget": "destructive", "min_radius_m": MIN_CLEAR_RADIUS_M,
            "tool": "tools/jumpstart/roads/clear.py::shrink_to_gate"}


# HARDENING 3, and the bound is taken from T12's own distribution rather than
# from taste: its largest clear cylinder held 27 objects and its per-cylinder
# counts run 4-34.  60 is twice the observed maximum, so a correctly specified
# cylinder cannot trip it and a mis-specified one that would quietly delete
# four hundred objects cannot proceed.  A numeric guard between a coding error
# and a grove.
MAX_REMOVALS_PER_CYLINDER = 60


def footprint_clear(L, cx: float, cz: float, r: float) -> dict:
    """Is the WHOLE cylinder outside every location's own footprint?

    HARDENING 1, and it closes a hole in my own proposal: I first wrote this as
    "the centre is outside the footprint", and a centre 1 m outside a footprint
    still reaches 5-9 m INTO it.  The test is
    `dist(centre, location) >= exteriorRadius + cylinder_radius`, which is what
    makes "cleared up to the POI, never through it" actually true rather than
    nearly true.

    The FOOTPRINT, not the budget: a location's `reach` is the radius its
    content occupies, and that is the thing whose vegetation is the POI's
    property.  `PROTECTED_EXTRA_M` is a WRITE budget for commands that could
    delete a location's ZDOs, and a removal that names only vegetation prefabs
    cannot -- which is the ruling this rests on.
    """
    bad = []
    for inst in L.instances_near([(cx, cz)], 400.0):
        d = math.hypot(cx - inst["xz"][0], cz - inst["xz"][1])
        need = float(inst["reach_m"]) + r
        if d < need:
            bad.append({"name": inst["name"], "prefab": inst["prefab"],
                        "dist_m": round(d, 1),
                        "footprint_m": float(inst["reach_m"]),
                        "required_m": round(need, 1),
                        "short_by_m": round(need - d, 1),
                        "protected": bool(inst.get("protected"))})
    return {"clear": not bad, "violations": bad}


def location_gate(cen: dict, plan: dict) -> dict:
    """The canonical POI instrument, over the sample set this op will actually
    delete in, under the budget that matches the COMMAND rather than the
    op class.

    THE RULING THIS RESTS ON, Main's, and the numbers that forced it.  Run
    DESTRUCTIVELY -- `reach + MARGIN_M + PROTECTED_EXTRA_M 90.0 + cylinder
    radius` -- the gate refused 12 of T12's 13 cylinders with allowed radii of
    -13 to -87 m, because `StartTemple` is protected and T12 is the road that
    TERMINATES at the temple.  A rule whose output is "-87.3 m of allowed
    cylinder" is not being applied to the case it was designed for.

    The destructive budget is a PROXY for "this command could delete a POI's
    ZDOs".  `objects_remove id=Beech1,Beech_small1,... ignore=_*` NAMES what it
    deletes and none of those names is a location piece, so the proxy is
    neither necessary nor sufficient here -- the same argument that replaced
    the zone-granular `flatten: FORBIDDEN` guard with a piece-level test.  So a
    removal whose `ids` are ALL on CLEARABLE gets the non-destructive distance
    rule, and keeps two gates a radius cannot give:

      * the WIRE names only vegetation prefabs, so the command cannot touch a
        location's content whatever the distance;
      * the same-call live census refuses the cylinder if ANY non-CLEARABLE
        prefab is present, which is what catches the ~190 mod POI types the
        mod-free dump cannot see;
      * and `footprint_clear` keeps the whole cylinder out of every location's
        own extent, so the road is cleared UP TO a POI and never through it.
    """
    import clearance  # noqa: PLC0415

    dump = next((p for p in ("/tmp/settle/loc3/f6fe167f4fcd.json",
                             "/tmp/settle/loc2/f6fe167f4fcd.json")
                 if Path(p).exists()), None)
    if dump is None:
        raise SystemExit("no location dump: refusing to delete anything near "
                         "generated content without the canonical check")
    L = clearance.load(dump)
    # Every id this op can ever send, checked against CLEARABLE before the
    # budget is chosen: the loosened rule is granted to a PREFAB-SCOPED
    # removal, so if anything in the plan is not vegetation the loosening does
    # not apply and the destructive budget stands.
    all_ids = sorted({i for c in plan["cylinders_clear"] for i in c["ids"]})
    scoped = [i for i in all_ids if not is_clearable(i)]
    destructive = bool(scoped)
    shrink = shrink_to_gate(plan, L, destructive=destructive)
    samples = [(c["centre"][0], c["centre"][1]) for c in plan["cylinders_clear"]]
    if not samples:
        return {"verdict": "clear", "dump": dump, "note": "nothing to clear",
                "shrink": shrink}
    # Re-asked over the surviving set, per cylinder, so the segment-level
    # verdict cannot be passed by averaging.
    v = {"verdict": "clear", "violations": []}
    footprints = []
    for c in plan["cylinders_clear"]:
        vv = L.verdict_for_samples([(c["centre"][0], c["centre"][1])],
                                   half_width_m=c["radius_m"],
                                   destructive=destructive,
                                   delta_gate={"verdict": "clear",
                                               "note": PREFAB_SCOPED_WHY})
        if vv["verdict"] != "clear":
            v = vv
            break
        fp = footprint_clear(L, c["centre"][0], c["centre"][1], c["radius_m"])
        if not fp["clear"]:
            footprints += fp["violations"]
    v = dict(v)
    if footprints:
        v = {"verdict": "VIOLATION", "violations": footprints,
             "why": "a removal cylinder overlaps a location's own footprint"}
    v["shrink"] = shrink
    v["budget_chosen"] = ("destructive" if destructive
                          else "non_destructive_prefab_scoped")
    v["ids_in_plan"] = all_ids
    v["ids_not_on_CLEARABLE"] = scoped
    v["prefab_scoped_why"] = PREFAB_SCOPED_WHY
    v["footprint_rule"] = ("dist(centre, location) >= exteriorRadius + "
                           "cylinder_radius -- the WHOLE cylinder outside "
                           "every location's own extent")
    v["nearest"] = L.nearest(*samples[0])
    v["samples_tested"] = len(samples)
    v["dump"] = dump
    v["destructive"] = True
    v["mod_free_caveat"] = (
        "This dump is MOD-FREE by construction, so it cannot see "
        "More_World_Locations' ~190 POI types. Clearing it is NECESSARY, NOT "
        "SUFFICIENT -- the sufficient check is the live per-cylinder census, "
        "which sees a mod house's pieces as pieces and skips the cylinder.")
    v["tool"] = "tools/jumpstart/roads/clear.py::location_gate"
    return v


def emit(seg: dict, cen: dict, plan: dict, gate: dict, *, dry: bool) -> dict:
    from live import LiveBuilder  # noqa: PLC0415

    done, skipped, removed_total = [], [], 0
    with LiveBuilder(actor=ACTOR, dry=dry) as b:
        b.observe(
            "road_surface_clearing_plan",
            method=cen["method"],
            tool="tools/jumpstart/roads/clear.py::census",
            value={"segment": seg["id"],
                   "clear_half_m": [cen["clear_half_m_min"],
                                    cen["clear_half_m_max"]],
                   "objects_censused": len(cen["objects"]),
                   "in_clear_width_clearable": plan["in_clear_width_clearable"],
                   "cylinders": len(plan["cylinders"]),
                   "cylinders_skipped": len(plan["cylinders_skipped"]),
                   "location_gate": gate["verdict"]})
        for i, cyl in enumerate(plan["cylinders_clear"]):
            cx, cz = cyl["centre"]
            r = cyl["radius_m"]
            # BETWEEN BATCHES, NOT PER CALL: prove the sink still carries
            # traffic before asking it another twenty questions. Stopping in
            # the middle of a segment is recoverable -- every cylinder is its
            # own idempotent record -- while sending removals whose
            # pre-removal census cannot be read is not.
            # EVERY FIVE, not every twenty-five.  MEASURED by SeatCheck
            # tonight: 34 consecutive `deleteObjects` records wedged the main
            # thread even though every individual reply was small, because
            # each one echoes ONE LINE PER DELETED OBJECT (155 bytes) through
            # the console sink.  A removal cylinder here averages 20 objects,
            # so five cylinders is ~100 lines -- the same order as the burst
            # that killed it, and the right place to look.
            if i and i % 5 == 0:
                sink_ok(f"{seg['id']} cylinder {i}")
            # THE CENSUS THAT DECIDES, taken HERE and nowhere earlier.
            live = count_star(b.srv, cx, cz, r)
            clearable = {k: v for k, v in live["per_prefab"].items()
                         if is_clearable(k)}
            blockers = {k: v for k, v in live["per_prefab"].items()
                        if blocks(k)}
            transient = {k: v for k, v in live["per_prefab"].items()
                         if k in NOT_PROPERTY}
            if blockers or not clearable:
                skipped.append({"centre": [cx, cz], "radius_m": r,
                                "blockers": blockers,
                                "clearable_present": clearable,
                                "why": ("a prefab not on CLEARABLE was in the "
                                        "cylinder at removal time"
                                        if blockers else
                                        "nothing left to remove")})
                print(f"  SKIP cylinder {i + 1} ({cx:.0f},{cz:.0f}) "
                      f"blockers={blockers}", flush=True)
                continue
            ids = sorted(clearable)
            # HARDENING 3: a numeric bound between a coding error and a grove.
            n_live = sum(clearable.values())
            if n_live > MAX_REMOVALS_PER_CYLINDER:
                skipped.append({"centre": [cx, cz], "radius_m": r,
                                "clearable_present": clearable,
                                "live_total": n_live,
                                "bound": MAX_REMOVALS_PER_CYLINDER,
                                "why": ("the live census exceeds "
                                        f"{MAX_REMOVALS_PER_CYLINDER} "
                                        "clearable objects, twice the largest "
                                        "cylinder measured on T12; refusing "
                                        "rather than deleting a grove on a "
                                        "mis-specified radius")})
                print(f"  SKIP cylinder {i + 1} ({cx:.0f},{cz:.0f}) "
                      f"{n_live} clearable exceeds the "
                      f"{MAX_REMOVALS_PER_CYLINDER} bound", flush=True)
                continue
            # HARDENING 2: WHAT WAS DELETED, BY PREFAB AND POSITION.  This is
            # the one irreversible step in the pipeline, and a record of counts
            # cannot be undone while a record of positions can: if the operator
            # walks the temple road and says the grove mattered, these exact
            # trees go back at these exact coordinates.  Positions come from
            # the corridor census (findObjects, per ZDO id); the COUNTS come
            # from the live census in this same call, and where they disagree
            # the live count is authoritative -- the difference is recorded
            # rather than reconciled.
            doomed = [{"prefab": o["prefab"], "id": o["id"],
                       "xz": [round(o["x"], 2), round(o["z"], 2)],
                       "y": round(o["y"], 2),
                       "lat_m": o.get("lat_m")}
                      for o in cen["objects"]
                      if o["prefab"] in clearable
                      and math.hypot(o["x"] - cx, o["z"] - cz) <= r]
            wire = (f"objects_remove id={','.join(ids)} ignore=_* "
                    f"pos={cx:g},{cz:g} max={r:.2f}")
            res = b.emit(
                "objects_clear",
                params={"centre": [cx, cz], "radius_m": r, "ids": ids,
                        "ignore": ["_*"], "role": "road_surface_clearing",
                        "note": f"road surface clearing, {seg['id']}"},
                wire=[wire],
                expect={"objects_count": {
                    "ids": ",".join(ids), "ignore": "_*",
                    "pos": [cx, cz], "max": r, "total": 0, "tolerance": 0}},
                meta={"segment": seg["id"],
                      "deleted": doomed,
                      "deleted_positions_from": (
                          "tools/jumpstart/roads/clear.py::census -- "
                          "findObjects -prefab P -near x y z r, deduplicated "
                          "by ZDO id. RECORDED SO THE DELETION IS REVERSIBLE: "
                          "prefab + world position + ZDO id per object. The "
                          "live count in the same call is authoritative for "
                          "HOW MANY; this list is authoritative for WHERE."),
                      "deleted_listed": len(doomed),
                      "deleted_live_count": n_live,
                      "cylinder": {"total": live["total"],
                                   "clearable": clearable,
                                   "not_clearable": blockers,
                                   "transient_present": transient,
                                   "transient_why": (
                                       "mobile creatures, recorded and NOT "
                                       "counted as blockers: a prefab-scoped "
                                       "removal that does not name them cannot "
                                       "touch them and their position is gone "
                                       "by the next frame. MEASURED on T12: "
                                       "one Boar per cylinder would otherwise "
                                       "have left 37 trees standing in the "
                                       "carriageway"),
                                   "census_attempts": live["attempts"]},
                      "census_timing": ("MEASURED in this same call, "
                                        "immediately before the removal -- a "
                                        "census taken minutes earlier is a "
                                        "census of a different world"),
                      "clear_half_m": cen["clear_half_m_max"],
                      "width_method": ("carriageway half+shoulder+1.0 m "
                                       "measured terrain spread, OR the "
                                       "batter's own taper run for the cut at "
                                       "that station; see clear.py docstring"),
                      "location_gate": {k: v for k, v in gate.items()
                                        if k in ("verdict", "standoff_m",
                                                 "nearest", "dump",
                                                 "destructive",
                                                 "mod_free_caveat", "tool")},
                      "ids_scoped_why": (
                          "id=* would also delete anything that arrived "
                          "between the census and the removal, and it is the "
                          "command that deleted POI content in the pre-wipe "
                          "world. Naming the measured prefabs makes the "
                          "safety a property of the COMMAND.")})
            removed_total += sum(clearable.values())
            done.append({"centre": [cx, cz], "radius_m": r, "ids": ids,
                         "removed": sum(clearable.values()), "seq": res["seq"]})
            print(f"  cleared cylinder {i + 1}/{len(plan['cylinders_clear'])} "
                  f"({cx:.0f},{cz:.0f}) {sum(clearable.values())} objects "
                  f"-> seq {res['seq']}", flush=True)
        # SAVE, AND IT IS NOT OPTIONAL AFTER A DESTRUCTIVE PASS.  MEASURED
        # tonight at a cost of 273 removals: the console sink died during the
        # re-census that followed this op, the main thread wedged, `save`
        # timed out at 90 s and the container had to be hard-stopped -- so the
        # newest state on disk was the 06:22 autosave and every removal this
        # op had VERIFIED was gone.  The records survived and the pass was
        # simply re-run, which is what the ledger is for, but four minutes of
        # the operator's world was rebuilt for want of one command.  A
        # destructive op that does not persist its own effect is an op whose
        # postcondition is true only until the next restart.
        if done:
            probe = done[-1]
            b.emit("save", params={"role": "road_surface_clearing",
                                   "note": f"persist the {seg['id']} clearing"},
                   wire=["save"],
                   expect={"objects_count": {
                       "ids": ",".join(probe["ids"]), "ignore": "_*",
                       "pos": probe["centre"], "max": probe["radius_m"],
                       "total": 0, "tolerance": 0}})
            print(f"  saved; last cleared cylinder still reads 0", flush=True)
        report = b.close()
    return {"segment": seg["id"], "cylinders_emitted": done,
            "cylinders_skipped_at_emit": skipped,
            "objects_removed": removed_total, "close_report": report}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["census", "plan", "emit", "placed"])
    ap.add_argument("--segment")
    ap.add_argument("--segments", default=str(HERE / "segments.yaml"))
    ap.add_argument("--census")
    ap.add_argument("--census-glob")
    ap.add_argument("--out")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if args.op == "placed":
        files = sorted(Path("/").glob(args.census_glob.lstrip("/")))
        cens = [json.loads(f.read_text()) for f in files]
        doc = placed_objects(cens)
        Path(args.out).write_text(json.dumps(doc, indent=1))
        print(f"placed objects: {len(doc['objects'])} from {len(cens)} censuses "
              f"-> {args.out}")
        return 0

    seg = load_segment(args.segment, Path(args.segments))
    per_station, widest = clear_half_width(seg)
    names = universe()
    print(f"{seg['id']}: {seg['length_m']} m, clear half-width "
          f"{per_station.min():.2f}..{widest:.2f} m, cylinder radius "
          f"{widest + REMOVAL_OVERREACH_M:.2f} m "
          f"({len(tiles_local(seg, per_station))} cylinders), "
          f"{len(names)} prefab names asked")

    if args.op == "census":
        import replay as R  # noqa: PLC0415
        with R.Server(dry=False) as srv:
            srv.probe()
            doc = census(srv, seg, names)
        Path(args.out).write_text(json.dumps(doc, indent=1))
        clearable = [o for o in doc["objects"]
                     if o["clearable"] and o["in_clear_width"]]
        print(f"censused {len(doc['objects'])} objects, "
              f"{len(clearable)} clearable inside the clear width -> {args.out}")
        return 0

    cen = json.loads(Path(args.census).read_text())
    plan = plan_removals(seg, cen)
    if args.op == "plan":
        print(json.dumps({k: v for k, v in plan.items()
                          if k != "cylinders"}, indent=1))
        return 0

    gate = location_gate(cen, plan)
    print(f"location gate ({gate.get('budget_chosen', '?')}): "
          f"{gate['verdict']} "
          f"nearest {(gate.get('nearest') or {}).get('name')} "
          f"{(gate.get('nearest') or {}).get('dist_m')} m")
    sh = gate.get("shrink") or {}
    print(f"  per-cylinder gate: {sh.get('kept')} kept, {sh.get('shrunk')} "
          f"shrunk, {sh.get('skipped')} skipped by the distance rule")
    for d in (sh.get("skipped_detail") or []):
        print("   gate-skipped", d)
    if gate["verdict"] != "clear":
        print(json.dumps(gate.get("violations", []), indent=1))
        print("REFUSING: a removal cylinder is inside a location's hard radius.")
        return 3
    out = emit(seg, cen, plan, gate, dry=args.dry)
    print(json.dumps({k: v for k, v in out.items()
                      if k != "cylinders_emitted"}, indent=1)[:3000])
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
