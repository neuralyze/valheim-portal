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
import socket
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
        except (OutputNotFound, TimeoutError, socket.timeout,
                ConnectionError, OSError) as exc:
            last = exc
            # A MISSING ECHO IS THE SINK'S SYMPTOM, SO REPAIR THE SINK BEFORE
            # RE-ASKING.  This answer comes back through the container log, so
            # the one failure mode that eats it is the same circular wait the
            # host drain releases -- and a drain can itself eat an in-flight
            # console answer, which is exactly why a missing answer is
            # re-asked instead of read as a zero.
            note = f"objects_count retry {i + 1} at ({x:.0f},{z:.0f})"
            try:
                sink_watch(note)
            except RuntimeError as sexc:
                raise SystemExit(
                    f"objects_count at ({x:.1f}, {z:.1f}) has no answer and "
                    f"the log sink cannot be repaired in place: {sexc}") from exc
            rc = getattr(srv, "rc", None)
            if rc is not None and isinstance(exc, (TimeoutError, OSError)):
                try:
                    rc.close()
                except Exception:  # noqa: BLE001
                    pass
                rc.connect()
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
# WAS 150, AND THE COST OF A CHECK IS WHY IT MOVED.  `sink_ok` probes through
# `docker exec`, which costs two seconds and cannot be trusted while the sink
# is stalled -- the exact case it exists to detect.  `rcon.host_sink_state`
# reads the same two `/proc/<pid>/wchan` entries from the HOST in 0.5 s and
# tells the drainable circular wait apart from the shape that needs a restart,
# so the check is cheap enough to run four times as often and its remedy is a
# two-second drain instead of a four-minute restart.
CENSUS_SINK_EVERY = 40
_census_calls = 0
# WHAT THE CENSUS COSTS THE ONE FRAGILE SUBSYSTEM, COUNTED RATHER THAN
# ESTIMATED.  The hazard is not the number of calls, it is the number of LINES
# the server writes to its own stdout, because `RconProxy` logs the FULL reply
# text inline on the Unity main thread.  Both census shapes -- per station and
# per cell -- go through `list_prefab`, so counting here counts them on the
# same instrument and the comparison between them is a measurement rather than
# an argument.
_ECHO = {"socket_calls": 0, "socket_reply_lines": 0,
         "console_calls": 0, "console_reply_lines": 0}


def echo_reset() -> dict:
    for k in _ECHO:
        _ECHO[k] = 0
    return dict(_ECHO)


def sink_watch(note: str = "") -> dict:
    """MEASURE the sink from the host and DRAIN it if it is in the drainable
    wait.  Raises for the shape a drain cannot fix.

    This replaces "stop with a named verdict" with "repair and continue"
    wherever the repair is real, and it is: MEASURED three times on this
    world, supervisord in `unix_wait_for_peer` with syslogd in `pipe_write`
    releases in under two seconds of sustained reading from supervisord's own
    read end, with the game process untouched.  Stopping instead would cost a
    restart per segment and there are thirteen segments left.

    The bytes drained are container log lines, so this is safe for the
    socket-only half of the census (where the echoes are pure cost) and a
    `consoleCommand` whose OUTPUT is read must re-ask after a drain.
    """
    import rcon as RC  # noqa: PLC0415

    return RC.keep_sink_clear(note=note)


def _throttle(srv) -> None:
    global _census_calls
    _census_calls += 1
    if getattr(srv, "dry", False):
        return
    time.sleep(CENSUS_SPACING_S)
    if _census_calls % CENSUS_SINK_EVERY == 0:
        sink_watch(f"census call {_census_calls}")


# How many times one READ-ONLY question may be re-asked after the transport
# times out.  A `findObjects` is idempotent, and a timeout means the main
# thread was blocked on a log write rather than that the answer is unknowable,
# so re-asking after draining the sink is the correct response.  MEASURED: the
# first T10 census attempt died on exactly this, with the sink in the
# drainable wait and the in-container probe misreporting it as `fds_lost`.
ASK_ATTEMPTS = 4


class AskTimeout(RuntimeError):
    """A read-only question that would not answer, drained sink and all.

    RAISED RATHER THAN FATAL, and the distinction is the whole point.  A
    timeout on `findObjects` is not "the answer is unknowable": it is evidence
    that the answer is TOO BIG.  `ValidatePayloadLength` caps a reply at 4,050
    bytes and the CLIENT buffer is 4,096, so a box holding a few hundred ZDOs
    does not come back at all -- MEASURED at the portal hall, cell
    (-296, 216) y 37, which is the hall's own pad plus the portal ring: four
    attempts, the sink drained and healthy every time, no reply.

    The right response is the SAME one an over-dense reply gets: ask smaller
    questions that TILE the same volume. That is not treating an unanswered
    question as empty -- it is asking a question the transport can carry.
    Refusal is still the answer when even a `MIN_LIST_HALF_M` box is silent,
    because then the transport is genuinely broken rather than merely narrow.
    """


def ask(srv, cmd: str) -> str:
    """One socket round trip that survives a stalled sink.

    THE SOCKET IS RECONNECTED AFTER A TIMEOUT rather than reused.  The reply
    to a timed-out command can still arrive later, and `RconPeer` parses one
    packet per receive and clears the buffer, so a reused socket would read
    the LATE reply as the answer to the NEXT question -- every position after
    that would be attributed to the wrong prefab.  A fresh connection cannot
    inherit that.
    """
    last = None
    for i in range(ASK_ATTEMPTS):
        try:
            return srv.command(cmd)
        # `rcon.py` raises a bare RuntimeError("rcon connection closed
        # mid-packet") when the server drops the stream part way through a
        # length-prefixed reply, and that is the SAME failure as a timeout
        # with the same remedy: the socket is unusable and a fresh one cannot
        # inherit the half-read packet.  MEASURED on T1 at the portal hall,
        # where the densest boxes in the world are: 17 of 85 cells in, the
        # game closed the connection mid-reply and the census died with the
        # partial file intact but the run stopped. Letting it through the
        # reconnect path is not suppressing the error -- the reply is re-asked
        # and an unanswered question is still a refusal after
        # `ASK_ATTEMPTS`.
        except RuntimeError as exc:
            if "closed mid-packet" not in str(exc):
                raise
            last = exc
            got = sink_watch(f"stream closed on `{cmd[:70]}`")
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            last = exc
            got = sink_watch(f"timeout on `{cmd[:70]}`")
        rc = getattr(srv, "rc", None)
        if rc is not None:
            try:
                rc.close()
            except Exception:  # noqa: BLE001 -- closing a dead socket
                pass
            time.sleep(1.0 + 2.0 * i)
            rc.connect()
        print(f"    [sink] {type(last).__name__} on attempt {i + 1}; "
              f"{got.get('action')} -> {got.get('verdict')}; retrying",
              flush=True)
    raise AskTimeout(
        f"`{cmd}` did not answer in {ASK_ATTEMPTS} attempts ({last}) even "
        f"after draining the log sink.")


# HOW MUCH A SUBDIVIDED BOX OVERLAPS ITS SIBLINGS, and this number was 1.4143.
# Eight sub-boxes of half `hh/2` centred at `+/-hh/2` TILE the parent box
# exactly -- there is no gap to close -- so the old half-diagonal expansion was
# covering a gap that does not exist and paying 2.83x the parent's VOLUME for
# it: every object inside a subdivided box was echoed about three times per
# level, and the children reached 0.41*hh OUTSIDE the parent, pulling in
# objects that belong to the neighbouring cell and breaking the disjointness
# the per-cell census is built on.  1.02 is float safety on an inclusive
# comparison and nothing more; the result is still keyed by ZDO id.
LIST_OVERLAP = 1.02


def list_prefab(srv, prefab: str, x: float, y: float, z: float,
                half: float) -> tuple[dict, list, int]:
    """Positions of one prefab inside the CUBE, subdividing until every reply
    is small.  Returns (by ZDO id, boxes that stayed too dense, socket calls).

    `findObjects -near x y z r` IS A CUBE IN ALL THREE AXES, and that is not a
    footnote -- it is a defect this function shipped with.  MEASURED on T10 at
    the spawn hall: the same prefab answered 2 rows for a 7.42 m disc centred
    on the road profile and 8 rows for a 16 m one, because six `wood_beam_1`
    stand at y 84-85 and the small box's ceiling is 83.15.  So the old
    per-station census, whose discs are 7-17 m, was BLIND TO EVERYTHING MORE
    THAN A DISC RADIUS ABOVE THE ROAD -- i.e. blind to exactly the structures
    whose presence is supposed to skip a removal cylinder.  The y extent is
    therefore part of the question and `census_zoned` stacks boxes to cover it.

    THE REPLY IS CAPPED AT 4050 BYTES BY `ValidatePayloadLength`, which runs
    when the packet is built and truncates: rows run ~62 bytes (MEASURED: 24
    rows = 1,404 bytes, 47 = 3,040), so a reply over ~65 rows loses rows with
    no error.  The subdivision trigger is the `Found n objects` HEADER rather
    than the rows parsed, so truncation can never be read as a short list --
    18 rows is the cap with a 3x margin, and anything denser is asked again
    smaller.
    """
    found: dict[str, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    stack = [(x, y, z, half)]
    while stack:
        cx, cy, cz, hh = stack.pop()
        _throttle(srv)
        try:
            reply = ask(srv, f"findObjects -prefab {prefab} "
                             f"-near {cx:.2f} {cy:.2f} {cz:.2f} {hh:.2f}")
        except AskTimeout:
            if hh <= MIN_LIST_HALF_M:
                raise
            h = hh / 2.0
            for dx in (-h, h):
                for dy in (-h, h):
                    for dz in (-h, h):
                        stack.append((cx + dx, cy + dy, cz + dz,
                                      h * LIST_OVERLAP))
            print(f"    [dense] no reply for a {hh:.2f} m box at "
                  f"({cx:.0f},{cy:.0f},{cz:.0f}); asking its eight tiling "
                  f"children instead", flush=True)
            calls += 1
            continue
        calls += 1
        # The SERVER logged exactly these lines on its main thread.  `n` rows
        # plus the "Found n objects" header when there is anything, one line
        # when there is not.
        _ECHO["socket_calls"] += 1
        _ECHO["socket_reply_lines"] += len(
            [ln for ln in reply.splitlines() if ln.strip()]) or 1
        m = FOUND_RE.search(reply)
        n = int(m.group(1)) if m else 0
        if n == 0:
            continue
        if n > MAX_LIST_ROWS:
            if hh <= MIN_LIST_HALF_M:
                unlisted.append({"prefab": prefab, "centre": [cx, cy, cz],
                                 "half_m": hh, "rows": n})
                continue
            h = hh / 2.0
            for dx in (-h, h):
                for dy in (-h, h):
                    for dz in (-h, h):
                        stack.append((cx + dx, cy + dy, cz + dz,
                                      h * LIST_OVERLAP))
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

    annotate(seg, objects, per_station, lat_of)
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

# ---------------------------------------------------------------------------
# THE CENSUS, PER CELL RATHER THAN PER STATION, AND OVER THE SOCKET ONLY
# ---------------------------------------------------------------------------
#
# THE DEFECT THIS REPLACES, MEASURED THREE TIMES.  `census` above asks its
# questions on the REMOVAL discs, which are spaced `1.80h` apart and have
# radius `1.35h`, so consecutive discs overlap by about two thirds and every
# trunk in the corridor is returned -- and therefore ECHOED TO THE CONTAINER
# LOG ON THE MAIN THREAD -- roughly ten times.  T12 cost 2,014 calls for 650
# objects, T3 4,500 for 1,200, and T10 (569 m, 45 discs, 139 names) never
# finished: it died at ~300 calls with the sink already gone.  It also asks
# BY NAME, 139 names per region, so the cost is names x regions and the answer
# is bounded by a list somebody wrote down.
#
# THREE CHANGES, and each one is about a measured failure:
#
#   1. THE QUERY REGIONS ARE DISJOINT.  A 16 m cell on a global 16 m lattice,
#      which divides the zone grid exactly (zone boundaries are at 64k +/- 32),
#      so the cells tile the plane and a `findObjects` cube of half 8 centred
#      on a cell IS that cell.  Each object is echoed ONCE instead of ~10
#      times.  Cells are kept only where the cell square actually intersects
#      the corridor swath, so the census covers everywhere the earthwork
#      writes and no further.
#
#   2. NO PREFAB LIST AT ALL.  One UNSCOPED `findObjects -near cx cy cz 8`
#      returns every ZDO in the box with its prefab and position, so the cost
#      is one call per box instead of 139, and the answer cannot be blind to a
#      prefab nobody wrote down -- including the ~190 More_World_Locations POI
#      types `corridor_prefabs.json` cannot name.  `rcon.guard` allows the
#      unscoped form only inside `UNSCOPED_NEAR_MAX_M`, which is where the
#      reply stays two orders of magnitude below the outage.
#
#   3. NOTHING GOES THROUGH THE CONSOLE.  The first version of this census
#      took the per-cell prefab list from `objects_count id=*`, whose table
#      comes back through the CONTAINER LOG.  MEASURED on T4: that census died
#      at cell 50 of 66 because the log path stopped carrying anything --
#      `docker logs` went silent while the game itself answered `players` in
#      34 ms -- so the name list was unreadable and the census refused, 11
#      minutes in.  The socket survives that failure; the log does not.  The
#      clearing EMIT still needs the console (its per-cylinder census and its
#      postcondition are `objects_count`), but the long phase no longer does.
#
# THE CELL SIZE IS BOUNDED BY TWO MEASURED LIMITS, not by taste.  Upward:
# `ValidatePayloadLength` truncates the reply at 4050 bytes and a row is ~62
# bytes (MEASURED: 24 rows = 1,404 bytes, 47 = 3,040), so a reply over ~65
# rows loses rows with no error, and the full untruncated text is what the
# main thread writes to the log.  The densest 32 m cell measured on these
# corridors holds 334 objects (the stathub end of T4), i.e. ~0.33 per square
# metre, so a 16 m cell is ~85 objects and ~5 KB at the worst place a road
# touches.  Downward: every cell costs a call per y level, so halving the cell
# again would quadruple the count for no completeness gain.
CENSUS_CELL_M = 16.0
CENSUS_CELL_HALF_M = CENSUS_CELL_M / 2.0
# How far past the clear half-width a cell still counts as "on the corridor".
# One metre, and it is `TERRAIN_SPREAD_M`: the reach of a terrain edit past the
# written lattice, i.e. the last place ground can move at all.
CENSUS_MARGIN_M = TERRAIN_SPREAD_M
# THE VERTICAL EXTENT OF THE QUESTION, and it exists because the wire takes
# ONE radius for all three axes.  MEASURED on T10 at the spawn hall: six
# `wood_beam_1` stand at y 84-85 and a 7.42 m box centred on the road profile
# at 75.73 cannot contain them, so the old per-station census was blind to
# everything more than a disc radius above the road -- i.e. blind to exactly
# the structures whose presence is supposed to skip a removal cylinder.  So
# the box is STACKED in y: five cubes of half 8 at -32, -16, 0, +16, +32 cover
# y-40..y+40 over exactly the same 16 m square, they are disjoint in y so an
# object is still echoed once, and 40 m is five times the earthwork's own
# +/-8 m apply clamp.  Ground variation inside one 16 m cell cannot exceed
# ~12 m at the 0.781 batter grade, so the stack covers the cell's own relief
# with margin.
CENSUS_Y_LEVELS = (0.0, -2.0 * CENSUS_CELL_HALF_M, 2.0 * CENSUS_CELL_HALF_M,
                   -4.0 * CENSUS_CELL_HALF_M, 4.0 * CENSUS_CELL_HALF_M)
# Prefabs the census RECORDS but never treats as corridor content: the
# engine's own per-zone objects.  `_*` is exactly what every removal wire
# passes as `ignore`, so a removal can never touch them, and counting them as
# blockers would skip every cylinder in every generated zone.  They are kept
# in a separate tally because `_ZoneCtrl` per zone is independently useful:
# one per zone is the generation signal the terrain write depends on.
ENGINE_PREFIX = "_"


def census_cells(seg: dict, per_station) -> list[tuple[float, float]]:
    """The disjoint 16 m cells that cover this segment's corridor swath.

    A cell is kept when its SQUARE intersects the disc of radius
    `clear_half(station) + CENSUS_MARGIN_M` around any non-bridge station --
    square-to-point distance, not centre-to-centre, because a cell whose corner
    clips the corridor holds trees that stand in the road.
    """
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    br = np.asarray(seg["is_bridge"], dtype=bool)
    per = np.asarray(per_station, dtype=np.float64)
    h = CENSUS_CELL_HALF_M
    keep: dict[tuple[int, int], tuple[float, float]] = {}
    for k in range(len(nodes)):
        if br[k]:
            continue
        sx, sz = float(nodes[k, 0]), float(nodes[k, 1])
        R = float(per[k]) + CENSUS_MARGIN_M
        i0 = int(math.floor((sx - R) / CENSUS_CELL_M))
        i1 = int(math.floor((sx + R) / CENSUS_CELL_M))
        j0 = int(math.floor((sz - R) / CENSUS_CELL_M))
        j1 = int(math.floor((sz + R) / CENSUS_CELL_M))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cx = i * CENSUS_CELL_M + h
                cz = j * CENSUS_CELL_M + h
                dx = max(abs(sx - cx) - h, 0.0)
                dz = max(abs(sz - cz) - h, 0.0)
                if dx * dx + dz * dz <= R * R:
                    keep[(i, j)] = (cx, cz)
    return [keep[k] for k in sorted(keep)]


def list_box(srv, cx: float, cy: float, cz: float,
             half: float = CENSUS_CELL_HALF_M) -> tuple[dict, list, int, dict]:
    """EVERY ZDO in one box, by ZDO id -- no prefab filter, one call.

    Returns (corridor objects, boxes that stayed too dense, socket calls,
    engine objects).  Subdivides into eight sub-boxes that TILE the parent
    whenever the `Found n objects` header exceeds `MAX_LIST_ROWS`, so no reply
    approaches the 4050-byte truncation and a dense cell costs calls rather
    than completeness.  The header, not the rows parsed, drives the
    subdivision: a truncated reply must never be read as a short list.
    """
    found: dict[str, dict] = {}
    engine: dict[str, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    stack = [(cx, cy, cz, half)]
    while stack:
        bx, by, bz, hh = stack.pop()
        _throttle(srv)
        try:
            reply = ask(srv, f"findObjects -near {bx:.2f} {by:.2f} {bz:.2f} "
                             f"{hh:.2f}")
        except AskTimeout:
            # TOO BIG IS NOT UNKNOWABLE.  MEASURED at the portal hall: the
            # 8 m cell box at (-296, 37, 216) holds the hall's pad and the
            # portal ring and never answers, while its eight children each
            # answer at once. The children TILE the parent, so nothing is
            # skipped and no object is attributed to a neighbouring cell.
            if hh <= MIN_LIST_HALF_M:
                raise
            h = hh / 2.0
            for dx in (-h, h):
                for dy in (-h, h):
                    for dz in (-h, h):
                        stack.append((bx + dx, by + dy, bz + dz,
                                      h * LIST_OVERLAP))
            print(f"    [dense] no reply for a {hh:.2f} m box at "
                  f"({bx:.0f},{by:.0f},{bz:.0f}); asking its eight tiling "
                  f"children instead", flush=True)
            calls += 1
            continue
        calls += 1
        _ECHO["socket_calls"] += 1
        _ECHO["socket_reply_lines"] += len(
            [ln for ln in reply.splitlines() if ln.strip()]) or 1
        m = FOUND_RE.search(reply)
        n = int(m.group(1)) if m else 0
        if n == 0:
            continue
        if n > MAX_LIST_ROWS:
            if hh <= MIN_LIST_HALF_M:
                unlisted.append({"centre": [bx, by, bz], "half_m": hh,
                                 "rows": n})
                continue
            h = hh / 2.0
            for dx in (-h, h):
                for dy in (-h, h):
                    for dz in (-h, h):
                        stack.append((bx + dx, by + dy, bz + dz,
                                      h * LIST_OVERLAP))
            continue
        for mm in POS_RE.finditer(reply):
            rec = {"prefab": mm.group("prefab"), "id": mm.group("id"),
                   "x": float(mm.group("x")), "y": float(mm.group("y")),
                   "z": float(mm.group("z"))}
            if rec["prefab"].startswith(ENGINE_PREFIX):
                engine[rec["id"]] = rec
            else:
                found[rec["id"]] = rec
    return found, unlisted, calls, engine


def annotate(seg: dict, objects: dict, per_station, lat_of) -> None:
    """Per-object road geometry, computed LOCALLY from the returned position.

    The whole saving of the per-cell census is that distance-to-ribbon is
    arithmetic on a position we already have rather than another question to
    the server.  Same function the per-station census used, factored out so the
    two shapes cannot annotate differently and make an equivalence proof
    meaningless.
    """
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


def census_zoned(srv, seg: dict, cells: list | None = None,
                 progress: bool = True, partial: Path | None = None) -> dict:
    """WHAT IS STANDING IN THIS ROAD, censused once per object, socket only.

    Same output shape as `census` -- including `tiles`, which stay the REMOVAL
    discs, because the removal geometry is not what changed.  What changed is
    where the questions are asked and what they cost.

    RESUMABLE, and the reason is a measured loss rather than tidiness: T4's
    first census reached cell 50 of 66 -- eleven minutes and 2,900 calls -- and
    then refused, and every object it had already found went with it.  The
    census is read-only and idempotent, so a partial file costs one write per
    cell and turns "start again" into "carry on".
    """
    per_station, widest = clear_half_width(seg)
    lat_of = lateral_fn(seg)
    disc_list = tiles_local(seg, per_station)
    cell_list = cells if cells is not None else census_cells(seg, per_station)
    objects: dict[str, dict] = {}
    engine: dict[str, dict] = {}
    unlisted: list[dict] = []
    calls = 0
    done_cells: set[str] = set()
    if partial and partial.exists():
        prev = json.loads(partial.read_text())
        if prev.get("segment") == seg["id"]:
            objects = {o["id"]: o for o in prev.get("objects", [])}
            engine = {o["id"]: o for o in prev.get("engine", [])}
            unlisted = prev.get("unlisted_boxes", [])
            calls = int(prev.get("socket_calls", 0))
            done_cells = set(prev.get("cells_done", []))
            print(f"  resuming: {len(done_cells)} cells already censused, "
                  f"{len(objects)} objects carried forward", flush=True)
    for i, (cx, cz) in enumerate(cell_list):
        key = f"{cx:.1f},{cz:.1f}"
        if key in done_cells:
            continue
        got = lat_of(cx, cz)
        y = got[1] if got else 0.0
        for dy in CENSUS_Y_LEVELS:
            f, un, n, eng = list_box(srv, cx, y + dy, cz)
            objects.update(f)
            engine.update(eng)
            unlisted += un
            calls += n
        done_cells.add(key)
        if progress:
            print(f"  cell {i + 1}/{len(cell_list)} ({cx:.0f},{cz:.0f}) "
                  f"objects so far {len(objects)} ({calls} socket calls, "
                  f"{_ECHO['socket_reply_lines']} echoed lines)", flush=True)
        if partial:
            partial.write_text(json.dumps(
                {"segment": seg["id"], "cells_done": sorted(done_cells),
                 "socket_calls": calls,
                 "objects": list(objects.values()),
                 "engine": list(engine.values()),
                 "unlisted_boxes": unlisted}))
    annotate(seg, objects, per_station, lat_of)
    zone_ctrl: dict[str, int] = {}
    for o in engine.values():
        if o["prefab"] == "_ZoneCtrl":
            import tcdata as _tc  # noqa: PLC0415
            zx, zz = _tc.zone_of(o["x"], o["z"])
            k = f"{zx},{zz}"
            zone_ctrl[k] = zone_ctrl.get(k, 0) + 1
    return {
        "segment": seg["id"],
        "width_m": seg["width_m"], "shoulder_m": RB.SHOULDER_M,
        "batter_m": RB.BATTER_MAX_M, "batter_grade": round(RB.BATTER_GRADE, 4),
        "terrain_spread_m": TERRAIN_SPREAD_M,
        "clear_half_m_min": round(float(per_station.min()), 3),
        "clear_half_m_max": round(widest, 3),
        "tiles": [[round(a, 2), round(b, 2), round(c, 2)]
                  for a, b, c in disc_list],
        "cells": [[round(a, 2), round(b, 2)] for a, b in cell_list],
        "cell_m": CENSUS_CELL_M,
        "y_levels": list(CENSUS_Y_LEVELS),
        "prefabs_asked": ["*  (unscoped findObjects: no name list at all)"],
        "prefabs_found": sorted({o["prefab"] for o in objects.values()}),
        "socket_calls": calls,
        "echo": dict(_ECHO),
        "objects": sorted(objects.values(),
                          key=lambda o: (o["prefab"], o["id"])),
        "engine_objects": sorted(engine.values(),
                                 key=lambda o: (o["prefab"], o["id"])),
        "zone_ctrl_seen": zone_ctrl,
        "unlisted_discs": unlisted,
        "method": (
            "MEASURED live over the RCON SOCKET ONLY, PER CELL rather than per "
            "station, and with NO PREFAB LIST. The corridor is covered by "
            "DISJOINT 16 m cells on a global 16 m lattice that divides the "
            "zone grid exactly, kept where the cell square intersects "
            "clear_half(station) + 1 m of any non-bridge station. Per cell, "
            "one UNSCOPED `findObjects -near cx y cz 8` at each of FIVE y "
            "levels (-32, -16, 0, +16, +32) returns every ZDO in that box with "
            "its prefab and position -- so the census cannot be blind to a "
            "prefab nobody wrote down, including the ~190 mod POI types "
            "corridor_prefabs.json cannot name. The y stack exists because "
            "`findObjects -near` is a CUBE IN ALL THREE AXES: MEASURED, the "
            "old per-station census missed six `wood_beam_1` at the T10 spawn "
            "hall because they stand at y 84-85 and its box, centred on the "
            "road profile at 75.73 with a 7.42 m half, could not contain "
            "them. A box answering more than 18 rows is re-asked as eight "
            "sub-boxes that TILE it (half/2 at +/-half/2, 1.02 float margin), "
            "so no reply approaches the 4050-byte truncation. Deduplicated by "
            "ZDO id; `_*` engine objects are tallied separately because a "
            "removal wire can never touch them. Nothing goes through the "
            "container console: the log path died mid-census on T4 and the "
            "socket did not. Distance to the ribbon, station, clear "
            "half-width and in_clear_width are computed LOCALLY from the "
            "returned position by ribbon.lateral_and_y -- the same function "
            "that decided where the road went -- so no distance costs a call."),
        "tool": "tools/jumpstart/roads/clear.py::census_zoned",
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


def removed_ids(cen: dict, emitted: list[dict]) -> dict:
    """Which censused ZDOs this session's VERIFIED removals actually deleted.

    DERIVED, NOT RE-MEASURED.  Each `objects_clear` record is a cylinder plus
    the prefab names on its wire, and its postcondition -- `objects_count` for
    exactly those names in exactly that cylinder, total 0, tolerance 0 -- was
    verified in the same call before the record was accepted.  So "every
    censused object whose prefab is on that wire and whose position is inside
    that cylinder" is not an estimate of what went: it is what the
    postcondition proved went.  A live re-census would be the alternative, and
    it is the thing that killed the console sink twice.
    """
    gone: dict[str, dict] = {}
    for cyl in emitted:
        cx, cz = cyl["centre"]
        r = float(cyl["radius_m"])
        ids = set(cyl["ids"])
        for o in cen["objects"]:
            if o["prefab"] in ids and math.hypot(o["x"] - cx,
                                                 o["z"] - cz) <= r:
                gone[o["id"]] = o
    return gone


def placed_after(cen: dict, emitted: list[dict]) -> dict:
    """THE BATTER'S GATE INPUT AFTER A CLEARING PASS: everything still
    standing in the corridor, by position.

    WHY THIS EXISTS RATHER THAN `placed_objects`, and it is a defect that one
    measured T10 case exposed.  `placed_objects` keeps an object only when it
    is off CLEARABLE or outside the clear width, i.e. it ASSUMES every
    clearable object inside the width was removed.  On T10 twelve were not: a
    `RockDolmen_1` with its skeleton spawner and location music stands at
    (394.31, 16.99) beside the carriageway, so `plan_removals` correctly pulled
    two cylinders in to 3.29 m and 6.45 m and five `Rock_4`/`Rock_7` and a
    `Pickable_Stone` are still standing 2.5-6.3 m from the centreline -- inside
    the earthwork.  Under the old predicate the batter would have been told
    those positions are free and would have moved the ground out from under
    them, which is EXACTLY the buried/floating defect this whole pass repairs,
    re-created by the repair.

    So the set is the census MINUS the verified removals, and nothing else.
    """
    gone = removed_ids(cen, emitted)
    keep: list[list[float]] = []
    per_prefab: dict[str, int] = {}
    standing_in_width: list[dict] = []
    for o in cen["objects"]:
        if o["id"] in gone:
            continue
        keep.append([round(o["x"], 2), round(o["z"], 2)])
        per_prefab[o["prefab"]] = per_prefab.get(o["prefab"], 0) + 1
        if o["clearable"] and o["in_clear_width"]:
            standing_in_width.append(
                {"prefab": o["prefab"], "id": o["id"],
                 "xz": [round(o["x"], 2), round(o["z"], 2)],
                 "y": round(o["y"], 2), "lat_m": o.get("lat_m"),
                 "clear_half_m": o.get("clear_half_m"),
                 "station": o.get("station")})
    return {
        "objects": keep, "per_prefab": per_prefab,
        "segments": [cen["segment"]],
        "left_standing_inside_clear_width": standing_in_width,
        "derived_from": {"before_count": len(cen["objects"]),
                         "deleted_matched": len(gone),
                         "after_count": len(keep),
                         "cylinders": len(emitted)},
        "method": (
            "DERIVED, not re-measured: the pre-clearing per-cell census MINUS "
            "every object this session's own `objects_clear` records name as "
            "deleted -- prefab on that cylinder's wire and position inside "
            "that cylinder -- each of whose postconditions (`objects_count` "
            "for those names in that cylinder, total 0, tolerance 0) was "
            "verified in the same call. Everything else is still standing and "
            "its ground must not move, INCLUDING clearable objects inside the "
            "clear width that a POI-shrunk cylinder could not reach: those are "
            "listed separately because they are the ones the old predicate "
            "wrongly assumed gone."),
        "tool": "tools/jumpstart/roads/clear.py::placed_after",
    }

# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

# HOW CLOSE A REMOVAL CYLINDER MAY COME TO SOMETHING IT MAY NOT DELETE.  One
# metre because `TERRAIN_SPREAD_M` is the reach of a terrain edit at all, so
# it is the honest slack -- the same figure the concentric shrink used, kept
# so the guarantee is unchanged and only the geometry that enforces it moves.
BLOCKER_STANDOFF_M = TERRAIN_SPREAD_M

# HOW MANY SUB-DISCS ONE BLOCKED TILE MAY BECOME.  Each costs one live
# `objects_count` inside `emit` and one ledger record, and the console sink
# died at 160 such calls, so the split is BOUNDED rather than trusted.
# MEASURED on T12: the worst blocked tile -- the generated wood house beside
# the centreline at (72,-68), 35 non-clearable pieces inside a 12.57 m disc --
# is covered by 6.
MAX_SUBDISCS_PER_TILE = 12

# THE SMALLEST SUB-DISC WORTH SENDING, and it is a precision floor rather
# than a taste one.  Centres and radii go on the wire at two decimals, so a
# cylinder's own centre object can sit up to sqrt(2)*0.005 = 7.1 mm outside a
# nominal radius of zero; 5 cm is seven times that.  A target whose blocker
# clearance is thinner than this is recorded as unreachable WITH THE BLOCKER,
# not sent as a cylinder that might reach nothing.
SUBDISC_MIN_RADIUS_M = 0.05

SPLIT_WHY = (
    "SPLIT, NOT SHRUNK. The hazard is the blocker's own neighbourhood, and "
    "pulling the parent disc in CONCENTRICALLY treats it as if it were the "
    "whole disc: a blocker near the centre erases the disc, and a blocker "
    "near the rim erases the rim. MEASURED, at the exact cost of this "
    "ticket: T12's disc at (77.58,-67.58) met a generated wood house 4.71 m "
    "from its centre, shrank from 15.34 m to 3.84 m (seq 937/952), removed 4 "
    "objects and left 14 standing -- 13 of them then BURIED by this "
    "segment's own terrain write, worst 3.818 m. It recorded no skip reason "
    "because nothing was skipped; a shrink is silent. So the disc is now "
    "covered by SUB-DISCS, each a strict SUBSET of the parent (so the "
    "over-reach fraction the parent's radius encodes is preserved exactly) "
    "and each WHOLLY outside every blocker's standoff "
    "(dist(sub_centre, blocker) >= sub_radius + BLOCKER_STANDOFF_M) -- the "
    "same shape as `footprint_clear`'s rule for a POI's own extent. The "
    "per-cylinder live census in `emit` still refuses any sub-disc that "
    "turns out to hold a non-CLEARABLE prefab, so the mod-POI gate is "
    "untouched. An object no sub-disc can reach is recorded PER OBJECT with "
    "the blocker that binds it, because 'left standing' with no reason is "
    "what was reported to the operator as fixed.")


def split_around_blockers(cx: float, cz: float, r0: float,
                          targets: list[dict], blockers: list[dict]
                          ) -> tuple[list[dict], list[dict]]:
    """Cover `targets` with sub-discs of the parent disc `(cx, cz, r0)` that
    each stand wholly clear of every blocker.  See `SPLIT_WHY`.

    Greedy by coverage, and the candidate centres are THE TARGETS THEMSELVES:
    a disc centred on the tree it exists to remove always reaches it, so the
    cover terminates with an explicit per-object reason rather than with a
    radius that happens to fall short.
    """
    remaining = {o["id"]: o for o in targets}
    subs: list[dict] = []

    def binding(o: dict) -> tuple[float, dict]:
        return min(((math.hypot(o["x"] - b["x"], o["z"] - b["z"]), b)
                    for b in blockers), key=lambda t: t[0])

    while remaining and len(subs) < MAX_SUBDISCS_PER_TILE:
        best = None
        for o in remaining.values():
            nb_d, nb = binding(o)
            # A SUBSET OF THE PARENT DISC: the parent's radius is the written
            # footprint plus the over-reach fraction, and a sub-disc that
            # reached outside it would be clearing ground nothing measured.
            room = r0 - math.hypot(o["x"] - cx, o["z"] - cz)
            # FLOORED TO THE SENT PRECISION, NOT ROUNDED.  The wire carries
            # `max=` at two decimals, and `round` goes UP half the time --
            # which spends up to 5 mm of the standoff that is the whole
            # guarantee.  MEASURED before this line existed: T12's worst
            # emitted cylinder cleared its nearest blocker by 0.997 m against
            # a 1.0 m standoff, i.e. the rounding, not the geometry, decided
            # it.  The floor also has to hold the cylinder's OWN centre
            # object, whose position is rounded to the same two decimals and
            # can therefore sit up to 7.1 mm outside a radius of zero.
            r = math.floor(min(nb_d - BLOCKER_STANDOFF_M, room) * 100.0) / 100.0
            if r < SUBDISC_MIN_RADIUS_M:
                continue
            got = [q for q in remaining.values()
                   if math.hypot(q["x"] - o["x"], q["z"] - o["z"]) <= r]
            if best is None or (len(got), r) > (len(best[0]), best[1]):
                best = (got, r, o, nb_d, nb)
        if best is None:
            break
        got, r, o, nb_d, nb = best
        counts: dict[str, int] = {}
        for q in got:
            counts[q["prefab"]] = counts.get(q["prefab"], 0) + 1
        subs.append({
            "centre": [round(float(o["x"]), 2), round(float(o["z"]), 2)],
            "radius_m": r,
            "ids": sorted(counts), "counts": counts,
            "covered_ids": sorted(q["id"] for q in got),
            "nearest_blocker_m": round(nb_d, 2),
            "nearest_blocker": {"prefab": nb["prefab"], "id": nb["id"],
                                "xz": [round(nb["x"], 2), round(nb["z"], 2)]},
        })
        for q in got:
            remaining.pop(q["id"], None)
    unreachable = []
    for o in remaining.values():
        nb_d, nb = binding(o)
        room = r0 - math.hypot(o["x"] - cx, o["z"] - cz)
        unreachable.append({
            "prefab": o["prefab"], "id": o["id"],
            "xz": [round(o["x"], 2), round(o["z"], 2)],
            "y": round(o["y"], 2), "lat_m": o.get("lat_m"),
            "clear_half_m": o.get("clear_half_m"),
            "parent_disc": [round(cx, 2), round(cz, 2), round(r0, 2)],
            "nearest_blocker": {"prefab": nb["prefab"], "id": nb["id"],
                                "xz": [round(nb["x"], 2), round(nb["z"], 2)],
                                "dist_m": round(nb_d, 2)},
            "room_in_parent_m": round(room, 2),
            "why": (f"a non-clearable {nb['prefab']} stands {nb_d:.2f} m away, "
                    f"which leaves "
                    f"{max(nb_d - BLOCKER_STANDOFF_M, 0.0):.2f} m inside the "
                    f"{BLOCKER_STANDOFF_M:g} m standoff -- less than the "
                    f"{SUBDISC_MIN_RADIUS_M:g} m minimum sendable cylinder, "
                    f"so no cylinder that reaches this object can avoid it"
                    if (math.floor(min(nb_d - BLOCKER_STANDOFF_M, room) * 100.0)
                        / 100.0) < SUBDISC_MIN_RADIUS_M else
                    f"the sub-disc budget of {MAX_SUBDISCS_PER_TILE} per tile "
                    f"was spent before this object was covered"),
        })
    return subs, unreachable


def plan_removals(seg: dict, cen: dict) -> dict:
    """Group the removals into cylinders, and keep every cylinder clear of
    everything this module may not delete.

    ONE CYLINDER PER TILE rather than one per trunk: `objects_remove` is a
    cylinder, and a per-trunk cylinder would multiply the ledger by a thousand
    records for no extra safety -- the safety comes from the prefab filter and
    from the census, both of which are per cylinder already.

    A tile that holds nothing non-clearable keeps its full derived radius,
    which is the path the thirteen already-cleared segments took.  A tile that
    does is SPLIT rather than shrunk; see `SPLIT_WHY` for the measurement that
    forced it.
    """
    per_station, widest = clear_half_width(seg)
    by_tile: list[dict] = []
    targeted: set[str] = set()
    want = [o for o in cen["objects"] if o["clearable"] and o["in_clear_width"]]
    deferred: list[tuple] = []
    for cx, cz, r0 in [tuple(t) for t in cen["tiles"]]:
        # Blockers out to TWICE the parent radius, because a sub-disc centred
        # near the parent's rim can otherwise reach one the parent does not
        # contain -- and a standoff computed against an incomplete blocker set
        # is not a standoff.
        blockers = [o for o in cen["objects"]
                    if blocks(o["prefab"])
                    and math.hypot(o["x"] - cx, o["z"] - cz) <= 2.0 * r0]
        near = [b for b in blockers
                if math.hypot(b["x"] - cx, b["z"] - cz) <= r0]
        inside = [o for o in want
                  if math.hypot(o["x"] - cx, o["z"] - cz) <= r0]
        transient: dict[str, int] = {}
        for o in cen["objects"]:
            if (o["prefab"] in NOT_PROPERTY
                    and math.hypot(o["x"] - cx, o["z"] - cz) <= r0):
                transient[o["prefab"]] = transient.get(o["prefab"], 0) + 1
        if near:
            deferred.append((cx, cz, r0, inside, blockers, near, transient))
            continue
        if not inside:
            continue
        counts: dict[str, int] = {}
        for o in inside:
            counts[o["prefab"]] = counts.get(o["prefab"], 0) + 1
        by_tile.append({"centre": [round(cx, 2), round(cz, 2)],
                        "radius_m": round(r0, 2),
                        "ids": sorted(counts), "counts": counts,
                        "blockers": {}, "transient_present": transient,
                        "shrunk_from_m": None, "nearest_blocker_m": None,
                        "verdict": "clear"})
        targeted.update(o["id"] for o in inside)
    unreachable: list[dict] = []
    for cx, cz, r0, inside, blockers, near, transient in deferred:
        blk: dict[str, int] = {}
        for b in near:
            blk[b["prefab"]] = blk.get(b["prefab"], 0) + 1
        nearest = min(math.hypot(b["x"] - cx, b["z"] - cz) for b in near)
        # Objects an unblocked tile already covers are not re-targeted: an
        # overlapping sub-disc would be a second record and a second live
        # census for an object that is already gone.
        tgt = [o for o in inside if o["id"] not in targeted]
        if not tgt:
            by_tile.append({"centre": [round(cx, 2), round(cz, 2)],
                            "radius_m": round(r0, 2), "ids": [], "counts": {},
                            "blockers": blk, "transient_present": transient,
                            "nearest_blocker_m": round(nearest, 2),
                            "verdict": "skip",
                            "why": ("nothing inside the derived width here is "
                                    "left to remove; the nearest blocker is "
                                    f"{nearest:.2f} m from this centre")})
            continue
        subs, un = split_around_blockers(cx, cz, r0, tgt, blockers)
        unreachable += un
        for i, s in enumerate(subs):
            s.update({
                "blockers": {}, "transient_present": transient,
                "verdict": "clear",
                "split_of": {"centre": [round(cx, 2), round(cz, 2)],
                             "radius_m": round(r0, 2),
                             "nearest_blocker_m": round(nearest, 2),
                             "blockers_in_parent": blk,
                             "sub_index": i + 1, "sub_count": len(subs),
                             "standoff_m": BLOCKER_STANDOFF_M,
                             "why": SPLIT_WHY}})
            by_tile.append(s)
            targeted.update(s["covered_ids"])
        if un:
            by_tile.append({"centre": [round(cx, 2), round(cz, 2)],
                            "radius_m": round(r0, 2), "ids": [], "counts": {},
                            "blockers": blk, "transient_present": transient,
                            "nearest_blocker_m": round(nearest, 2),
                            "verdict": "skip",
                            "why": (f"{len(un)} object(s) inside the derived "
                                    "width here cannot be reached by any "
                                    "blocker-clear sub-disc; see "
                                    "`unreachable` for the blocker that binds "
                                    "each one")})
    left = [o for o in want if o["id"] not in targeted]
    un_by_id = {u["id"]: u for u in unreachable}
    return {
        "segment": seg["id"],
        "cylinders": by_tile,
        "cylinders_clear": [c for c in by_tile if c["verdict"] == "clear"],
        "cylinders_skipped": [c for c in by_tile if c["verdict"] == "skip"],
        "in_clear_width_clearable": len(want),
        "targeted": len(targeted),
        "left_standing": len(left),
        "left_standing_detail": sorted({o["prefab"] for o in left}),
        "left_standing_objects": [
            un_by_id.get(o["id"], {
                "prefab": o["prefab"], "id": o["id"],
                "xz": [round(o["x"], 2), round(o["z"], 2)],
                "y": round(o["y"], 2), "lat_m": o.get("lat_m"),
                "clear_half_m": o.get("clear_half_m"),
                "why": ("NO REASON RECORDED: this object is inside the "
                        "derived clearing width and no derived disc contains "
                        "it, which is a tiling defect rather than a refusal")})
            for o in left],
        "unreachable": unreachable,
        "blocker_standoff_m": BLOCKER_STANDOFF_M,
        "policy": ("CLIP, never blanket, and SPLIT, never shrink: a cylinder "
                   "may not hold any prefab off CLEARABLE, and a tile that "
                   "does is covered by sub-discs that each stand wholly "
                   "outside every blocker's standoff. A road crosses other "
                   "people's property, so the road yields -- but it yields "
                   "the blocker's neighbourhood, not the whole disc, and "
                   "every object it still cannot reach is named with the "
                   "blocker that binds it. That is the opposite of a "
                   "settlement pad, which REFUSES rather than clips because "
                   "a building cannot stand in half a clearing."),
        "split_why": SPLIT_WHY,
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


def emit(seg: dict, cen: dict, plan: dict, gate: dict, *, dry: bool,
         actor: str) -> dict:
    from live import LiveBuilder  # noqa: PLC0415

    done, skipped, removed_total = [], [], 0
    with LiveBuilder(actor=actor, dry=dry) as b:
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
            # ...AND REPAIRED RATHER THAN MERELY REPORTED.  `sink_ok` stopped
            # the pass with a named verdict, which is right when the only
            # remedy is a restart -- but the drainable circular wait is the
            # shape this actually hits, and the host drain clears it in two
            # seconds without touching the game.  A clearing pass stopped
            # halfway leaves a half-cleared road until someone re-runs it.
            if i and i % 5 == 0:
                sink_watch(f"{seg['id']} cylinder {i}")
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
                          "HOW MANY; this list is authoritative for WHERE. "
                          "THE DURABLE KEY IS PREFAB + POSITION, NOT THE ZDO "
                          "ID -- MEASURED tonight by re-censusing T12 across "
                          "a container restart: all 414 objects came back at "
                          "byte-identical x, z and y with DIFFERENT ZDO ids "
                          "(23163:1 -> 22958:1, 76580:1 -> 76288:1), so the "
                          "ids are reassigned on world load and an id alone "
                          "cannot identify an object to put back."),
                      "deleted_listed": len(doomed),
                      "deleted_live_count": n_live,
                      "split_of": cyl.get("split_of"),
                      "nearest_blocker_m": cyl.get("nearest_blocker_m"),
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
                         "removed": sum(clearable.values()),
                         "seq": res["seq"],
                         "split_of": cyl.get("split_of"),
                         "nearest_blocker_m": cyl.get("nearest_blocker_m")})
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
        # WHAT WAS NOT CLEARED, AND WHY, PER CYLINDER AND PER OBJECT --
        # APPENDED, not merely returned.  MEASURED at a cost paid twice:
        # `plan['cylinders_skipped']`, `gate['shrink']['skipped_detail']`,
        # `plan['left_standing_objects']` and this call's own `skipped` were
        # all BUILT and all thrown away when the process exited, so of 1,734
        # records ZERO carried per-cylinder skip detail while 262 of the 268
        # objects still standing network-wide had no recorded reason anywhere
        # in the chain -- and "every remaining object is recorded with its
        # skip reason" was relayed to the operator as fact.  A reason that
        # lives only in a run's stdout is not a record.
        #
        # A SHRINK IS SILENT, so a SPLIT states its parent: each emitted
        # sub-disc is listed with the derived disc it is a strict subset of
        # and the blocker that bound it, because the reader's question is
        # never "what radius was sent" but "was the full request honoured".
        # THE DURABLE KEY IS PREFAB + WORLD POSITION: ZDO ids are reassigned
        # on world load (MEASURED: 414 T12 objects, byte-identical x/y/z,
        # every id different), so an object named by id alone cannot be found
        # again to answer "why is this one still here".
        left = plan.get("left_standing_objects") or []
        splits = [c for c in done if c.get("split_of")]
        b.emit("observe", params={
            "what": f"road_surface_clearing_left_standing::{seg['id']}",
            "method": (
                "MEASURED -- the per-cylinder disposition of THIS clearing "
                "pass, appended at the end of the same session that sent the "
                "removals: the plan's blocked-tile skips, the location "
                "gate's per-cylinder skips, the skips this call's own "
                "immediately-prior live census forced, the blocker-clear "
                "sub-discs actually sent with the derived parent disc each "
                "one is a subset of, and every object left inside the "
                "derived clearing width named by prefab + world position "
                "with the blocker that binds it."),
            "tool": "tools/jumpstart/roads/clear.py::emit",
            "value": {
                "segment": seg["id"],
                "in_clear_width_clearable": plan["in_clear_width_clearable"],
                "targeted": plan["targeted"],
                "objects_removed": removed_total,
                "cylinders_emitted": len(done),
                "cylinders_emitted_as_blocker_clear_sub_discs": len(splits),
                "cylinders_skipped_in_plan": len(plan["cylinders_skipped"]),
                "cylinders_skipped_by_the_location_gate": len(
                    (gate.get("shrink") or {}).get("skipped_detail") or []),
                "cylinders_skipped_at_emit": len(skipped),
                "objects_left_standing": len(left),
                "objects_left_standing_with_a_recorded_reason": sum(
                    1 for o in left
                    if o.get("why") and "NO REASON RECORDED" not in o["why"]),
            }},
            meta={
                "segment": seg["id"],
                "durable_key": ("prefab + world position; the ZDO id is "
                                "recorded alongside and is NOT durable "
                                "across a world load"),
                "objects_left_standing": left,
                "unreachable": plan.get("unreachable") or [],
                "cylinders_skipped_in_plan": plan["cylinders_skipped"],
                "cylinders_skipped_by_the_location_gate": (
                    (gate.get("shrink") or {}).get("skipped_detail") or []),
                "cylinders_skipped_at_emit": skipped,
                "cylinders_split_around_blockers": splits,
                "split_why": plan.get("split_why"),
                "blocker_standoff_m": plan.get("blocker_standoff_m"),
                "policy": plan.get("policy")})
        print(f"  recorded disposition: {len(left)} left standing, "
              f"{len(plan['cylinders_skipped'])} plan skips, "
              f"{len(skipped)} emit skips, {len(splits)} sub-discs",
              flush=True)
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
    ap.add_argument("--actor", required=True,
                    help="the agent id that is ACTUALLY running this pass. "
                         "Stated rather than defaulted because a module-level "
                         "constant once recorded seq 933 under RoadClear's "
                         "name for RoadEmit's work, and provenance that is "
                         "wrong reads as evidence.")
    ap.add_argument("--per-station", action="store_true",
                    help="census on the removal discs with the 139-name "
                         "universe -- the OLD shape, kept only so the cheap "
                         "one can be measured against it (census_equiv.py)")
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
    cells = census_cells(seg, per_station)
    print(f"{seg['id']}: {seg['length_m']} m, clear half-width "
          f"{per_station.min():.2f}..{widest:.2f} m "
          f"({len(tiles_local(seg, per_station))} removal cylinders), "
          f"census over {len(cells)} disjoint {CENSUS_CELL_M:g} m cells "
          f"x {len(CENSUS_Y_LEVELS)} y levels"
          + (f", {len(names)} prefab names asked" if args.per_station
             else ", UNSCOPED (no prefab list, no console)"))

    if args.op == "census":
        import replay as R  # noqa: PLC0415
        with R.Server(dry=False) as srv:
            srv.probe()
            echo_reset()
            doc = (census(srv, seg, names) if args.per_station
                   else census_zoned(srv, seg, cells,
                                     partial=(Path(str(args.out) + ".partial")
                                              if args.out else None)))
            doc["echo"] = dict(_ECHO)
        Path(args.out).write_text(json.dumps(doc, indent=1))
        clearable = [o for o in doc["objects"]
                     if o["clearable"] and o["in_clear_width"]]
        print(f"censused {len(doc['objects'])} objects, "
              f"{len(clearable)} clearable inside the clear width; "
              f"{_ECHO['socket_calls']} socket calls / "
              f"{_ECHO['socket_reply_lines']} echoed lines, "
              f"{_ECHO['console_calls']} console calls / "
              f"{_ECHO['console_reply_lines']} table lines -> {args.out}")
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
    out = emit(seg, cen, plan, gate, dry=args.dry, actor=args.actor)
    print(json.dumps({k: v for k, v in out.items()
                      if k != "cylinders_emitted"}, indent=1)[:3000])
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
