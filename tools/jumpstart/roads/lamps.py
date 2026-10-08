#!/usr/bin/env python3
"""ROAD LIGHTING: site a lamp where the TERRAIN gives the longest sightline in
BOTH directions, dense where the biome hides the road and sparing where it does
not.

THE OPERATOR'S RULE, VERBATIM: "the roads need to have lit lamposts. use the
terrain to pick a good spot where it is visible from both directions the
farthest each within 30-60m of each other. the lamposts only really need to be
in the forests or other biomes with limited visibility. the meadows and other
biomes with high visibility they can be used sparringly."

That is a SITING rule with a spacing BOUND, and the two halves are not the same
instruction.  30-60 m is the window a lamp may land in; WHERE inside that
window it lands is decided by measuring the ground.  A tool that divides the
length by 45 and stamps satisfies the bound and answers a different question.

--------------------------------------------------------------------------
THE LAMP, AND EVERY CLAIM HERE IS MEASURED BY `roads/LampProbe.cs`
--------------------------------------------------------------------------
`piece_groundtorch`, the Standing iron torch, spawned with a ZDO payload that
turns its own `Fireplace` everburning.

  * EMITS LIGHT: one `Light`, type Point, range 15.0 m, intensity 1.5, colour
    (1, 0.621, 0.482), at local y +0.746 -- ground + 1.400 once seated.  Point
    and not Spot is the whole reason it wins.  `piece_hoodedlantern` has NO
    Fireplace at all and a 30 m light, and it was the front-runner until the
    probe measured the light's aim: `type Spot`, `spot_angle 30`,
    `aim_local [0, 0, 1]` -- a 30 degree cone fired horizontally along the
    prefab's +Z.  Its omnidirectional component is a 2 m point light.  A road
    is walked in both directions, so 15 m of omnidirectional beats 30 m of
    one-way wedge.  For the record, every other buildable lit candidate:
    bonfire 20 m (and 4.1 m tall, a bonfire is not a lamppost), hearth 13 m,
    `piece_Lavalantern` 12 m point and fuel-free but `m_notOnWood` and an
    Ashlands lava cage on a Meadows road, `piece_walltorch` 12 m (needs a
    wall), the green/blue/mist standing torches 10 m,
    `piece_dvergr_lantern_pole` 6 m with its pivot 3.0 m above its foot.

  * NEEDS NO FUEL AND CANNOT BURN OUT, and NOT because a mod says so.  The
    prefab ships `m_infiniteFuel: false, m_startFuel: 2, m_maxFuel: 6,
    m_secPerFuel: 20000, m_fuelItem: Resin` -- about 11 hours and then it wants
    resin, once per lamp, forever.  `TimedTorchesStayLit` v1.4.0 IS installed
    (DLL checked, not merely its config -- four orphaned configs have been
    found in this session alone) and its server-synced, admin-locked config
    sets `Metal Torch Stay Lit = true`, `Keep on when wet = true` and a
    17:00-05:30 night timer, and its own DLL names `piece_groundtorch(Clone)`
    and patches `Fireplace.IsBurning` / `Interact` / `GetHoverText`.  That is
    good, and it is NOT what this module relies on: a mod is one uninstall away
    from 9 km of dark road plus a refuelling chore.

    So each lamp carries its own everburning switch, in VANILLA.  MEASURED in
    `assembly_valheim` IL (`/tmp/roads/av.il`, `ZNetView::LoadFields`):
    `Awake` calls `LoadFields`, which returns immediately unless the ZDO holds
    bool `HasFields`; then per `MonoBehaviour` in the hierarchy it requires
    bool `HasFields<TypeName>`; then it reflects that type's fields and, for
    each, reads ZDO key `"<TypeName>.<fieldName>"` typed by the field --
    `GetInt`, `GetFloat`, `GetBool`, `GetVec3`, `GetString`, prefab, ItemDrop.
    `Fireplace.m_infiniteFuel` is a bool field read by `UpdateFireplace`,
    `IsBurning`, `GetHoverText`, `Interact`, `UseItem`, `TryGetItems` and
    `CanUseItems`.  And `ZDO::GetBool(int32, bool)` is
    `ZDOExtraData::GetInt(...) != 0`, so these three go in the DataEntry INTS
    section as 1 -- a bool section would be read as something else entirely.

  * SERVER-SYNCED AND PERSISTED: `ZNetView` present, `m_persistent true`.
    It is an ordinary piece ZDO; it survives save and load like the roads do.

  * DOES NOT DECAY: `WearNTear` material Wood, health 100, `m_noSupportWear
    true` so it needs no support, `m_noRoofWear false` so in VANILLA an
    exposed torch would take weather damage.  `AzuWearNTearPatches` v1.0.9 is
    installed (DLL checked) with `No Weather Damage to buildings = On`,
    `No Damage to player buildings = On` and `Disable Structural Integrity
    system = On`, all server-synced and admin-locked.

  * PIVOT: local y -0.65359 .. +0.82260, and the RENDER MESH spans exactly the
    same as the collider, so the pivot is 0.654 m up the shaft and the visible
    foot is 0.654 m BELOW it.  This is the trap: `base_geometry`'s
    ground-resting guard lists `groundtorch` and then DROPS it because it fails
    the pivot-at-base check, which is that module refusing to guess -- and
    `CastleKit_groundtorch` is the identical mesh with its root at the foot
    (-0.0516 .. 1.4246), so the two differ by 0.602 m of nothing but prefab
    authoring.  A lamp seated as though its pivot were its foot floats 0.654 m,
    which is the portal defect at ten times the size.  The offset is not
    restated as a literal here: `seat_offset_m()` asks
    `fixtures.fixture_box`, the SAME box builder the placement audit uses.

--------------------------------------------------------------------------
THE SIGHTLINE, AS A MEASURED MAXIMISATION
--------------------------------------------------------------------------
For a candidate station on the centreline, the sightline in one direction is
how far a walker can get before the APPLIED SURFACE rises into the line between
their eye and the lamp's flame.  Both endpoints are read off
`applied.Applied.surface_at` -- the live union of the ledger's own terrain
blobs plus generated height, bilinear, which is the only instrument in this
project that measures what a player actually stands on.  The generated height
is the HEIGHTMAP lattice the deltas were written against, proved per zone by
digest: the same deltas on the worldgen lattice put a Fresh42 lamp's top 1.168
m underground (see `LAMP_SEAT_SURFACE_DEPENDS_ON`).  Reading the PLANNED
profile instead would answer a question about the plan: 89 m of T4's centreline
sits on samples a settlement pad overwrote after the road was written.

Visibility is the CONTIGUOUS run, not the farthest visible station.  From the
flame at height `FLAME_M` above the surface at the lamp's own XZ, for each
station `j` ahead at distance `t`:

    blocked when   max over k in (i, j) of (surf[k] - flame) / (k - i)
                   >=  (surf[j] + EYE_M - flame) / t

and the sightline is the last `t` before the first block.  A far hillside above
an intervening ridge is genuinely visible, but it is not what "a lamp in view
as you walk" means, so the run stops at the ridge.  The running maximum makes
this O(cap) per direction rather than O(cap^2).

The score of a candidate is `min(forward, backward)`.  MIN, not sum and not
mean: a lamp seen 80 m one way and 5 m the other lights one approach and
ambushes the other.  Maximising the minimum is what puts lamps on crests and on
the approaches to bends, and the report prints the profile of the window so the
claim is visible rather than asserted.

--------------------------------------------------------------------------
WHERE A LAMP IS NEEDED IS THE LIGHTING GATE'S QUESTION, NOT A SPACING
--------------------------------------------------------------------------
`accept/sections/lighting.py` decides whether a road is lit: every station
has a corridor flame in view, within its sight distance, from its eye height
over the applied surface.  This module IMPORTS that criterion rather than
stating one of its own (`LAMP_COVERAGE_DEPENDS_ON`): the walk goes to the
first station the gate would call dark, sites the legal spot that lights the
most road onward from it (the sightline above breaks ties), and stops when
none is dark.  The biome spacing below bounds where a lamp may stand; it no
longer decides whether the road is finished.  A station no legal lamp can
light is REFUSED by number with the owner of every spot that could have lit
it.  MEASURED reason: the spacing walk left T7's last 115 m into Stenvik dark
on Fresh42 drive 4 and reported a different stretch.

--------------------------------------------------------------------------
BIOME DENSITY
--------------------------------------------------------------------------
The biome is READ, per candidate, from the same PatchScan zone patches the
heights come from: `heights.Patch.biomes` is `WorldGenerator.GetBiome` sampled
on the zone's own 1 m lattice.  It is not inferred from coordinates and not
taken from the segment's name.

--------------------------------------------------------------------------
WHAT THIS MODULE WILL NOT DO
--------------------------------------------------------------------------
It refuses rather than shrinks, and it records what it refused and why.  A
guard that narrows a request instead of declining it is silent, and this
project has paid for four of those.  Keep-outs are asked of the instruments
that own them: `ribbon.pad_keepouts_from_chain` (the rectangles
`writer._pad_footprint_check` will judge a write against),
`ribbon.poi_standoff_discs`, `ribbon.protected_piece_positions` for bridge and
harbour decks and their recorded extents, and `applied.Applied.foreign_at` for
per-sample authorship, which is blind to radii by design.

Note the one guard that is NOT armed for this op: `_pad_footprint_check` fires
on `terrain_write` only.  A lamp writes no terrain, so nothing in `writer.py`
stops it landing on a foundation.  That is why the keep-out is enforced here,
explicitly, against the same rectangles.

--------------------------------------------------------------------------
AND THE KEEP-OUT THAT WAS MISSING: OTHER LAMPS
--------------------------------------------------------------------------
`KeepOut` asks about pads, POIs, protected structures and ground authorship.
It never asked about LAMPS, and `_biome_allows` enforced the minimum spacing
against `last_s` -- the previous lamp in one call's own chain on one segment.
That is a decision made from local state about a shared world, and it produced
34 pairs closer than the 30 m minimum on the live network, the tightest at
2.814 m.  `Standing` is that missing keep-out: a live, network-wide,
cylindrical, y-aware census of every ground-torch variant near these
corridors, consulted per candidate and re-asked per position immediately
before the write.  Its own section below carries the four measured mechanisms
and the two traps it is shaped around.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import weakref
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, HERE, JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints", JUMPSTART / "settlements"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import applied as appliedmod   # noqa: E402
import ribbon                  # noqa: E402
import tcdata                  # noqa: E402
import worldseed               # noqa: E402
# THE LIGHTING GATE, imported as the one authority on "is this station lit".
# See `LAMP_COVERAGE_DEPENDS_ON` below for what is read off it.
from accept.sections import lighting as GATE  # noqa: E402

# RESOLVED, not literal.  `main()` rebinds both from `--seed`/`--world`.
WORLD = worldseed.world()
SEED = worldseed.seed()
# THE PROVENANCE DEFAULT, AND IT IS THE ONE THIS PROJECT HAS ALREADY PAID FOR
# TWICE.  `clear.py`'s module constant said `RoadClear` while `RoadEmit` was
# running it, and the ledger then recorded work under the name of an agent
# that had yielded an hour earlier: provenance that is WRONG is worse than
# provenance that is missing, because it reads as evidence.  This name is the
# fallback for OFFLINE reporting only; `--actor` is REQUIRED for `--place`
# and `--dry`, and `main` rebinds this global to it so the patchscan sandbox
# and the live builder cannot disagree about who is running.
ACTOR = "Lampposts"
SCRATCH = Path("/tmp/roads/lamps")

LAMP_PREFAB = "piece_groundtorch"

# `ribbon.WATER_LEVEL_M`, restated with its provenance rather than imported
# blind: a lamp at the water plane is a lamp in the surf.  The freeboard is the
# same 0.5 m `field.Field.land` uses and for the same reason.
WATER_LEVEL_M = ribbon.WATER_LEVEL_M
FREEBOARD_M = 0.5

# MEASURED by roads/LampProbe.cs: the Point light sits at local y +0.746 and the
# pivot is 0.65359 above the visible foot, so the flame is 1.39959 m above the
# ground the lamp stands on.  Restated rather than re-derived at runtime
# because the probe writes to /tmp and /tmp does not survive a reboot; the
# module docstring carries the provenance and `--probe` re-measures it.
FLAME_ABOVE_SURFACE_M = 0.65359 + 0.746

# A standing player's eye height -- THE GATE'S, and not a second copy of it.
# DESIGN, not measured: there is no instrument on this host that reads
# `Player.m_eyePoint` without a client.  `accept/sections/lighting.py` said
# "roads/lamps.py declares the same 1.7 m for the same reason; this is that
# number, not a second one", which was a promise two literals kept by luck; it
# is one binding now.  `--eye-sweep` re-runs the siting at 1.5 and 1.9 m and
# prints how much the answer moves, so the sensitivity is a number rather than
# a hope.
EYE_M = GATE.EYE_M

# How far the sightline search looks.  DESIGN: the lamp ILLUMINATES 15 m (its
# measured light range) and is VISIBLE as a beacon much further, but a 1.5 m
# torch flame at 80 m is a pixel, and a cap that flatters the report is worse
# than a short one.  Candidates that hit the cap are counted in the report so a
# reader can see when the terrain, rather than the cap, stopped the answer.
SIGHT_CAP_M = 80.0

# Sightline sampling along the road.  1 m, the lattice the terrain is written
# on: a coarser step steps over the ridge that does the blocking.
SIGHT_STEP_M = 1.0

# ---- THE LIGHTING CRITERION: THE GATE'S OWN, IMPORTED --------------------
#
# THE DEFECT, MEASURED ON FRESH42 DRIVE 4 (save af96bfb62427).  Gate
# `lighting/unlit_run`, ACTIONABLE: at 30 m sight distance the worst unlit run
# is 115 m on road_T7_stathub_stenvik, ending at (335.9, -274.8), and 2,252 of
# 12,866 stations have no flame in view from eye height 1.7 m.  Replayed
# OFFLINE -- the ledger cut before step 7, the frozen save's torch census minus
# step 7's own lamps -- the shipped walk reproduces all 132 drive-4 lamps
# exactly and the gate's 115 m / 2,252 / 602 exactly, so the defect is the
# walk and not the instrument.
#
# THE MECHANISM.  The walk sized its windows from `BIOME_POLICY` spacing
# constants, and nothing in it ever asked the gate's question.  T7 runs beside
# T4 and then S4 for most of its 1,758 m, and their lamps put window after
# window inside somebody's minimum spacing, so the walk broke its chain,
# advanced 30 m and broke it again, until it ran off the end at 1,754 m with
# ONE lamp placed -- walking past the last 115 m into Stenvik, which no
# standing flame reaches.  Its own report could not see that either:
# `dark_stretches` counted this segment's lamps only, at its own 80 m cap, and
# said "803 m dark at 0 m" -- a number about the plan, not about the road.
#
# THE FIX: THE GATE'S CRITERION IS THE WALK'S STOPPING RULE.  A station is
# lit exactly when `accept/sections/lighting.py` would call it lit: a corridor
# torch (`classify_lamps`, the gate's population -- standing in the world or
# staged by this run), mapped to a station by `lamp_index`, within the gate's
# sight distance, whose flame (`FLAME_ABOVE_PIVOT_M`) is in view from `EYE_M`
# over the applied surface by `_approach.visible`.  The walk goes to the first
# DARK station and sites the best-sightline legal lamp that lights it, and
# stops when no station is dark.  A dark station no legal lamp can light is
# REFUSED by number, with the reason every spot that could have lit it gave --
# pad, deck, water, POI, spacing -- which names its owner.  `Coverage.verify`
# re-measures the finished plan with the gate's own `unlit_runs` and REFUSES
# if the two ever disagree, so this copy of the loop cannot drift from the
# gate's.
#
# THE SHORTEST SIGHT DISTANCE THE GATE REPORTS BINDS: the same flames are
# indexed at every distance, so a station with one in view within 30 m has
# one within 50 m.
LAMP_COVERAGE_DEPENDS_ON = (
    "accept.sections.lighting.SIGHT_DISTANCES_M",
    "accept.sections.lighting.EYE_M",
    "accept.sections.lighting.FLAME_ABOVE_PIVOT_M",
    "accept.sections.lighting.classify_lamps",
    "accept.sections.lighting.lamp_index",
    "accept.sections.lighting._approach.visible")
SIGHT_M = min(GATE.SIGHT_DISTANCES_M)

_COVERAGE_MODE = "gate"     # gate | spacing (the shipped known-bad walk)

# Where the lamp stands relative to the centreline.  The carriageway is
# `half_width_m` each side and `ribbon.SHOULDER_M` of shoulder beyond it, so
# this offset puts the lamp's 0.24 m footprint wholly on the shoulder: at
# half_width + 0.55 the shaft spans half_width+0.43 .. half_width+0.67, inside
# a 1.0 m shoulder with clear air both sides.  A cart uses the carriageway; the
# shoulder is graded ground that nothing drives on.
SHOULDER_STANDOFF_M = 0.55

# ---- THE CARRIAGEWAY KEEP-OUT -------------------------------------------
#
# THE DEFECT, MEASURED.  `SHOULDER_STANDOFF_M` above is CONSTRUCTED, not
# VERIFIED: `build()` steps `half_width + 0.55` along the unit normal of a
# five-station chord.  On a bend that chord is not the local tangent, and
# where the polyline doubles back the offset point lands near a DIFFERENT
# part of the same polyline.  So the lamp is exactly 3.55 m from its own
# station and, for instance, 0.081 m from the centreline as a whole.
#
# MEASURED at population scale on Ulfsland's own 25 built segments, every
# 1 m station, both sides - 43,908 candidate positions the CURRENT geometry
# can produce:
#     3,338 land inside their OWN segment's carriageway (worst 0.001 m
#           from the centreline of T3 at s=65)
#     1,294 land inside ANOTHER segment's carriageway (worst 0.000 m,
#           N1 s=0 into N0b at the junction they share)
# and 23 of them are STANDING: 19 in their own road, 4 in a neighbour's,
# the closest 0.081 m from T5's centreline.
#
# The fix is not a different offset.  It is that the offset is a
# CONSTRUCTION and the requirement is a MEASUREMENT: a lamp is legal only
# where its TRUE PERPENDICULAR distance to every built centreline - its
# own included, over the whole polyline and not just the station it was
# derived from - is at least that centreline's own half width plus the
# lamp's own half footprint.  `KeepOut.why_not` is where that is asked,
# because it is the one place that answers "may a lamp stand here".
#
# The lamp's own half footprint: MEASURED 0.23 x 0.24 m at the foot, so
# 0.12 m is the half-diagonal rounded up.  A lamp whose SHAFT is clear but
# whose foot overhangs the carriageway is still an obstacle in it.
LAMP_HALF_FOOTPRINT_M = 0.12


def perpendicular_m(px: float, pz: float,
                    nodes: list[tuple[float, float]]) -> float:
    """True distance from a point to a centreline POLYLINE.

    Not the distance to the nearest station: on a bend that overstates the
    offset, and the offset is the quantity that decides whether a lamp is
    designed occupancy or an obstacle.  Same definition the acceptance
    gate's `lighting` section uses, so the builder and the gate cannot
    disagree about what "lateral offset" means.
    """
    best = math.inf
    for i in range(len(nodes) - 1):
        ax, az = nodes[i]
        bx, bz = nodes[i + 1]
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx
                                                   + (pz - az) * vz) / ll))
        best = min(best, math.hypot(px - (ax + t * vx), pz - (az + t * vz)))
    return best


# ---- THE PROXIMITY GATE'S OWN CONSTANTS ----------------------------------
# Every ground-torch variant, because the gate asks "is this spot already
# lit", not "did the lighting pass put something here".  MEASURED on Ulfsland:
# the 158 torches beside these roads are 138 `piece_groundtorch` at a median
# 3.44 m lateral offset -- road markers and structure lamps -- plus 20
# `piece_groundtorch_wood` at a median 11.09 m and up to 35.53 m, which are
# blueprint interiors standing INSIDE houses.  A torch in a house 11 m off the
# carriageway lights the carriageway, so siting a road lamp 3 m from it is
# exactly the clustering the operator reported; the gate must see it.  Which
# of them may be REMOVED is a different question with a different answer --
# see `--cull`.
STANDING_PREFABS = (LAMP_PREFAB, "piece_groundtorch_wood",
                    "piece_groundtorch_green", "piece_groundtorch_blue",
                    "piece_groundtorch_mist")
# Half-height of the gate's CYLINDER.  A lamp on the road and a lamp on the
# clifftop 12 m above it do not read as a cluster from either one, and
# refusing the road lamp because of it would be the cube answering a question
# about a cylinder.  See `Standing.why_not`.
STANDING_Y_HALF_M = 10.0
# How far either side of the centreline the standing census reads.  A
# candidate sits `half_width + SHOULDER_STANDOFF_M` (~4 m) off the centreline
# and the widest minimum spacing any biome asks for is 45 m, so 120 m covers
# every cylinder a candidate can ask about with room for a structure lamp
# standing back from the road.
STANDING_REACH_M = 120.0
# The census lattice.  The hazard is reply BYTES -- `CommandResult.Text` is
# written in full on the Unity main thread and truncates silently at 4050 --
# while the cost is CALLS, because `findObjects` walks every ZDO once per call
# regardless of its filter.  A 32 m tiling of this network is ~1700 calls; one
# world-radius query is a single call whose reply for a few hundred lamps
# would be ~18 KB and truncated.  512 m cells clipped to the corridors is
# ~120 calls with every reply a few hundred bytes, and `clear.list_prefab`
# subdivides any cell whose `Found n objects` header exceeds 18 rows.
# MEASURED: 58 cells, 256 socket calls, 496 reply lines, 64 s, sink healthy.
STANDING_CELL_M = 512.0
# THE CENSUS'S Y BAND, DECLARED AND THEN ENFORCED.
#
# `Segment` carries `nodes` as (x, z) only -- the terrain_write profile has no
# per-node height -- so the census has NO height for its cells, and the first
# version of this gate quietly centred every cube at y = 0 and leaned on the
# 256 m half-extent to cover the road anyway.  That works for this network,
# whose lamps sit at y 31 to 133, and it is still the same y-blindness that
# cost this world 64 duplicate pieces: it is a cube that happens to contain
# the answer rather than a question with a stated range.
#
# So the band is declared, covered by STACKED levels, and any query outside it
# is REFUSED rather than answered from a cube that never looked there.  Two
# levels of a 512 m cube cover -256..+640 m, which contains every buildable
# height in Valheim (ocean floor to the highest mountain peak); the cost is
# one extra call per cell per prefab.
STANDING_Y_LEVELS = (0.0, 384.0)
STANDING_Y_MIN_M = STANDING_Y_LEVELS[0] - STANDING_CELL_M / 2.0
STANDING_Y_MAX_M = STANDING_Y_LEVELS[-1] + STANDING_CELL_M / 2.0

# Clearance the lamp keeps from any piece already in the world.  `PLACED_CLEAR_M`
# is what the batter uses against a placed object; a lamp is a placed object
# itself, so it keeps the same distance plus its own half-width.
PIECE_CLEAR_M = ribbon.PLACED_CLEAR_M + 0.15

# How much the ground may rise and fall ACROSS the lamp's own 0.23 x 0.24 m
# footprint before the spot is refused.  Seating on the deepest corner means a
# lamp never floats, but on broken ground it buys that by burying the base:
# the burial equals the footprint's relief.  MEASURED on S1, the 30.6 m ramp
# off the portal hall onto the bridge, the worst footprint relief is 0.118 m,
# so a lamp there stands with 12 cm of its base in the bank -- fine.  At some
# point it stops being fine, and rather than silently accepting whatever the
# ground gives, a spot above this is REFUSED and recorded with its measured
# relief.  The window then picks a neighbouring station, which is what the
# window is for.
#
# AND THE GAME AGREES WITH THIS GATE, which is the best kind of corroboration
# because it was found after the gate was written and for a different reason.
# MEASURED by `LampProbe.cs` off the prefab's own `Piece` fields:
# `piece_groundtorch` is `m_notOnTiltingSurface = True`.  So the build menu's
# own rule for this exact prefab is "not on a slope", and `spawn_object`
# bypasses it -- a headless placement would put a torch on a bank the game
# would have refused a player.  This gate is that rule, expressed against the
# applied surface rather than against a collider normal.
#
# (`m_notOnWood` is FALSE, and an earlier draft of this file said otherwise on
# the strength of a hand-written column list that omitted `distant` and so
# read `not_on_tilting` in the `not_on_wood` slot.  Three instruments --
# `LampProbe`, `crossings/data/piece_material.tsv` and a modded re-dump --
# agree it is 0.  Recorded because the shape of that mistake is the one this
# tree keeps paying for: a display bug read as a measurement.)
FOOTPRINT_RELIEF_MAX_M = 0.15

# --------------------------------------------------------------------------
# biome policy
# --------------------------------------------------------------------------
# PUBLISHED PER BIOME, with the reason, because "dense in forests, sparing in
# meadows" is a design decision and it belongs where it can be argued with.
#
# `min_m`/`max_m` bound the gap to the PREVIOUS lamp.  The operator's 30-60 m
# is the bound for a low-visibility biome; for an open one they said "used
# sparingly", and the task's reading of that is "near the 60 m bound or beyond
# it where sightlines are long".  So an open biome may stretch to `max_m`
# ABOVE 60, and only under the overlap condition in `_biome_allows`: the lamp
# must be visible backwards at least as far as the gap it just left, so the
# previous lamp is still in view when this one comes into view.  Without that
# condition "sparingly" would just mean "dark".
BIOME_POLICY: dict[str, dict] = {
    "BlackForest": {"min_m": 30.0, "max_m": 42.0, "why":
                    "dense conifer forest, the road is a corridor with 5-10 m "
                    "of visibility off it; the operator named forests as the "
                    "place lamps are actually needed"},
    "Swamp":       {"min_m": 30.0, "max_m": 42.0, "why":
                    "permanent fog and standing trees; the darkest walkable "
                    "biome in the game"},
    "Mistlands":   {"min_m": 30.0, "max_m": 36.0, "why":
                    "mist halves visibility again; tightest spacing in the set"},
    "Mountain":    {"min_m": 40.0, "max_m": 60.0, "why":
                    "open ground but broken relief and blizzards; the terrain "
                    "does the occluding rather than the vegetation, which is "
                    "exactly what the sightline search reads"},
    "DeepNorth":   {"min_m": 40.0, "max_m": 60.0, "why":
                    "as Mountain, plus permanent low light"},
    "Meadows":     {"min_m": 55.0, "max_m": 90.0, "why":
                    "long open sightlines; the operator said sparingly, so the "
                    "gap runs to 60 m and past it wherever the lamp is "
                    "measurably still in view of its neighbour"},
    "Plains":      {"min_m": 55.0, "max_m": 90.0, "why":
                    "open and flat; tall grass hides ground detail, not the "
                    "horizon, so a lamp is visible a long way"},
    "AshLands":    {"min_m": 55.0, "max_m": 90.0, "why":
                    "open, and lit by its own lava glow"},
    "Ocean":       {"min_m": 30.0, "max_m": 60.0, "why":
                    "a road sample reading Ocean is a causeway or a ford; "
                    "spacing is the trunk default and the water guard decides "
                    "whether a lamp can stand there at all"},
}
# A biome the policy does not name is refused rather than defaulted, because a
# default here is a silent decision about where the operator's lamps go.
DEFAULT_POLICY = None


# --------------------------------------------------------------------------
# THE COORDINATES AS WRITTEN, AS THE GATE READS THEM BACK, AND THE MARGIN
# --------------------------------------------------------------------------
#
# THE DEFECT, MEASURED ON THE REGENERATED ULFSLAND (seed Pirate68, save
# c4f653104b10).  Gate `lighting/lamp_spacing`: two lamps on
# road_W7_wwest_wnorthwest, 29.999712 m apart in xz against the 30 m minimum
# (`accept/sections/lighting.py::check_spacing` refuses `d < MIN_SPACING_M`).
# The same two lamps through every stage between the walk and the gate:
#
#   as the ledger plan holds them (`lamp_unit`, 4 dp)   29.999995 m
#       (-1420.8311, 2155.5626)   (-1399.6179, 2176.7758)
#   as float32 in the ZDO                                30.000076 m
#   as the census TSV prints them (the gate's input)     29.999712 m
#       (-1420.831, 2155.563)     (-1399.618, 2176.776)
#
# THE WALK HAD ACCEPTED THE PAIR.  `Standing.near` refuses `d <= min_m`, and
# the build log records one lamp on W7 sited on the gate's 30 m floor tier
# ("1 stand on the gate's 30 m minimum instead of the biome's"), so on the
# UNROUNDED coordinates the walk staged the distance was above 30 [INFERENCE:
# the ledger keeps 4 dp, so the unrounded value is not recorded, and that the
# floor-tier lamp is this one follows from `d <= 30` being false for it or the
# lamp would have been refused].  A candidate 30 stations along a straight 45
# degree road is 30.000000 m from the last one to within float noise, so
# "exactly on the limit" is not rare, it is what a 1 m lattice produces.
# Then each lamp was rounded to 4 dp, stored as float32, and printed by
# `terraform/WaterProof.cs::WriteZdoCensus` as `float.ToString("0.####")`,
# which goes through SEVEN SIGNIFICANT DIGITS and so prints 3 decimals at
# |coordinate| 1000..9999 and only 2 at 10000+.  Each stage moves a pair by up
# to its own rounding; the 4 dp write and the census print both moved this one
# closer, the float32 store moved it the other way, and the sum is the gate's
# 29.999712.
#
# THE RULE THAT WAS MISSING: a placement is judged on the number the GATE
# WILL READ, not on the number the walk holds.  Two parts, both here:
#
#   1. DECIDE ON WHAT IS WRITTEN.  `written_xz` is the position `lamp_unit`
#      puts on the wire (4 dp).  `_candidate` applies it, so the staged lamp,
#      the ledger plan and the spawn command are one coordinate and the walk
#      cannot hold a value nothing else ever sees.
#   2. KEEP A MARGIN OVER EVERY LIMIT THE GATE MEASURES, larger than what is
#      lost after the write.  `READBACK_MARGIN_M` is added to the 30 m
#      minimum and the biome minima (`Standing.near`, `Standing.confirm`,
#      `_biome_allows`), to the cylinder's +/-10 m half-height (the same
#      cliff edge in y: a lamp 10.0001 m above is "not a neighbour" to the
#      walk and 9.9999 m above to the gate), and taken OFF the sight
#      distance (`Coverage.sees`: a station 30.0000 m from a flame is lit to
#      the walk and, after the flame's coordinates move 0.0003 m, dark to
#      the gate).  `roads/relight.py` uses the same constant.
#
# WHAT IS AFTER THE WRITE, MEASURED.  `readback_coord` models the census
# print (float32, 7 significant digits, half up, at most 4 dp).  Over all 343
# `piece_groundtorch` plans of this build against the saved census, 323 are
# reproduced exactly and 20 differ by one digit in the last place; the
# excess over a plain float32 round trip plus the print's half step is at
# most 0.89 float32 ulp over the 686 coordinates, so the bound below allows
# 1.5 ulp per axis (0.5 for the round trip, 1.0 for what was measured on
# top).  The LIVE census a placement run reads its standing lamps from
# (`findObjects`) prints `(-913.6 35.56 1887.1)`, two decimals at most, so
# the neighbour a new lamp is judged against can itself be 0.005 m off per
# axis.  `readback_shrink_bound_m` adds those up for the worst coordinate in
# the world; the selftest requires the margin to exceed it.
#
# CHECKED AND NOT A MARGIN QUESTION.  `BIOME_POLICY` `max_m` bounds the
# ARCLENGTH between this run's own consecutive lamps and no gate check reads
# it (the gate reads unlit RUNS, `MAX_UNLIT_RUN_M`, which the sight margin
# makes conservative).  The gate's lateral limit is kept 0.12 m clear by
# `KeepOut` (`LAMP_HALF_FOOTPRINT_M`) and the designed offset is constructed
# at 0.00 m from itself against the gate's 0.10 m tolerance: both two orders
# over the quantisation.
LAMP_SPACING_DEPENDS_ON = (
    "accept.sections.lighting.MIN_SPACING_M",
    "accept.sections.lighting.SPACING_Y_HALF_M",
    "accept.sections.lighting.check_spacing",
    "accept.sections.lighting.unlit_runs",
    "terraform/WaterProof.cs::WriteZdoCensus",
    "clear.POS_RE (the live findObjects reply)")

# The 4 decimals `lamp_unit` puts on the wire.
WIRE_DECIMALS = 4
# `WriteZdoCensus`: float32 `ToString("0.####")` -- at most this many decimals,
# and at most this many significant digits, whichever bites first.
CENSUS_MAX_DECIMALS = 4
CENSUS_SIG_DIGITS = 7
# `findObjects`, the census a placement run reads standing lamps from:
# `Position: (-913.6 35.56 1887.1)`, i.e. "0.##".
LIVE_CENSUS_DECIMALS = 2
# Valheim's world radius plus its edge band: the largest |coordinate| a lamp
# can have, which is where the census prints the fewest decimals.
WORLD_EXTENT_M = 10500.0
# How far inside every limit the gate measures a placement must stand.  50 mm
# is 2.7 x `readback_shrink_bound_m` at the world's edge (0.018 m) and 6 x it
# at |coordinate| 2,200 m (0.008 m); it is the value `relight.py` already
# carried as `SEPARATION_MARGIN_M`, now one number for both.
READBACK_MARGIN_M = 0.05

_SPACING_MODE = "readback"      # readback | bare (the shipped known-bad)


def readback_margin_m() -> float:
    """The margin every spacing and sight limit is kept inside by."""
    return READBACK_MARGIN_M if _SPACING_MODE == "readback" else 0.0


def written_xz(x: float, z: float) -> tuple[float, float]:
    """The position `lamp_unit` writes for `(x, z)`: what the walk decides on."""
    if _SPACING_MODE != "readback":
        return x, z
    return round(x, WIRE_DECIMALS), round(z, WIRE_DECIMALS)


def readback_coord(v: float) -> float:
    """One coordinate as `WriteZdoCensus` prints it and the gate reads it:
    float32, then 7 significant digits rounded half up, at most 4 decimals."""
    f = struct.unpack("f", struct.pack("f", v))[0]
    if f == 0.0:
        return 0.0
    d = Decimal(f)
    decimals = max(0, min(CENSUS_MAX_DECIMALS,
                          CENSUS_SIG_DIGITS - 1 - d.adjusted()))
    return float(d.quantize(Decimal(1).scaleb(-decimals),
                            rounding=ROUND_HALF_UP))


def readback_xz(x: float, z: float) -> tuple[float, float]:
    """`(x, z)` as the gate will read it back out of the saved census."""
    return readback_coord(x), readback_coord(z)


def readback_shrink_bound_m(extent_m: float = WORLD_EXTENT_M) -> float:
    """The most a pair's xz separation can fall between the walk's decision
    and the gate's measurement, for coordinates up to `extent_m`.

    Per axis: a NEW lamp loses 1.5 float32 ulp (see the block above) plus the
    census print's half step; the OTHER lamp of the pair loses the same if it
    is also new, or the live census's half step if it is standing.  The pair
    is xz, hence the root of two.  The walk decides on 4 dp so the wire
    rounding is not in it.
    """
    c = abs(extent_m)
    ulp32 = 2.0 ** (math.floor(math.log2(c)) - 23)
    decimals = max(0, min(CENSUS_MAX_DECIMALS,
                          CENSUS_SIG_DIGITS - 1 - math.floor(math.log10(c))))
    new_lamp = 1.5 * ulp32 + 0.5 * 10.0 ** -decimals
    other = max(new_lamp, 0.5 * 10.0 ** -LIVE_CENSUS_DECIMALS)
    return math.sqrt(2.0) * (new_lamp + other)


# --------------------------------------------------------------------------
# geometry off the instruments
# --------------------------------------------------------------------------

def seat_offset_m(prefab: str = LAMP_PREFAB) -> float:
    """How far ABOVE the walking surface the prefab's pivot goes, asked of
    `fixtures.fixture_box` rather than restated.

    `fixture_box(prefab, x, y, z, yaw)` returns the world AABB with `y` treated
    as the surface it stands on, so `ymin - y` is the solid's own offset from
    the pivot and its negation is the lift that puts the solid's underside on
    the ground.  Asking the audit's own box builder is the rule: a closed form
    over half-extents was wrong at yaw 45 in `waypoints.py` and a restated
    literal is a copy that can go stale.
    """
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(prefab, 0.0, 0.0, 0.0, 0.0)
    return -box[2]


def lamp_unit(x: float, z: float, surface_y: float, yaw_deg: float) -> dict:
    """One lamp: prefab, world position, yaw, and the seat arithmetic shown."""
    off = seat_offset_m()
    return {"prefab": LAMP_PREFAB, "x": round(x, WIRE_DECIMALS),
            "y": round(surface_y + off, WIRE_DECIMALS),
            "z": round(z, WIRE_DECIMALS),
            "yaw_deg": round(yaw_deg % 360.0, 3),
            "surface_y": round(surface_y, 4), "seat_offset_m": round(off, 5),
            "flame_y": round(surface_y + FLAME_ABOVE_SURFACE_M, 4)}


def everburning_payload() -> tuple[str, dict]:
    """`(base64, the decoded map)` that makes this lamp's own Fireplace
    everburning.

    Three ZDO INTs, and each one is load-bearing: `LoadFields` returns at once
    without `HasFields`, skips the component without `HasFieldsFireplace`, and
    has nothing to assign without `Fireplace.m_infiniteFuel`.  INTs and not a
    bool section because `ZDO::GetBool` is `ZDOExtraData::GetInt(...) != 0` --
    MEASURED in IL, see the module docstring.
    """
    import data_entry  # noqa: PLC0415
    keys = {"HasFields": 1, "HasFieldsFireplace": 1,
            "Fireplace.m_infiniteFuel": 1}
    entry = data_entry.DataEntry()
    for name, value in keys.items():
        entry.ints[data_entry.stable_hash(name)] = value
    blob = entry.encode()
    # ROUND-TRIP THE PAYLOAD BEFORE IT IS SENT.  `data_entry.decode` is the
    # read side of the same wire format the server will use, so a payload that
    # does not decode to what was asked for is a payload the game will parse
    # into something else -- and an unparseable `data=` is accepted silently by
    # `spawn_object`, which places the piece with default fields.
    back = data_entry.decode(blob)
    got = {data_entry.HASH_TO_NAME.get(h, f"0x{h & 0xFFFFFFFF:08x}"): v
           for h, v in back.ints.items()}
    if got != keys:
        raise SystemExit(
            f"REFUSING: the everburning payload does not round-trip. asked "
            f"{keys}, decoded {got}. A `data=` the game cannot read is spawned "
            f"as a torch with default fields, i.e. one that burns out, and "
            f"nothing reports it.")
    return blob, keys


# --------------------------------------------------------------------------
# the segments, out of the ledger
# --------------------------------------------------------------------------
# WHICH SEGMENT A ROAD RECORD IS, AND THIS FILE ANSWERED WITH A PREFIX.
# `segments_from_ledger` keyed every built road on `name.split("_")[1]` --
# `road_S7_stathub_wtnorth` -> `S7` -- which is a key naming a SUBSET of the
# segment id: everything after the first underscore was thrown away.
#
# TWO THINGS MEASURED 2026-09-24, both on the first run step 7 has ever had:
#   THE CALLER COULD NOT NAME A SEGMENT.  `buildstage` step 7 passes the
#   corpus unit, `S7-stathub-wtnorth`, and this file answered "no such built
#   segment(s): ['S7-stathub-wtnorth']; have ['S7', 'T13']".  Both roads this
#   build laid were unlightable BY THEIR OWN NAME.
#   TWO SEGMENTS SHARING A PREFIX COLLIDE SILENTLY.  `road_S7_stathub_wtnorth`
#   and a sibling `road_S7_stathub_wtsouth` both key `S7`, and the dict keeps
#   the last one: one road would be lit twice and the other not at all, with
#   no error anywhere. Ulfsland's ids happen to be prefix-unique, which is
#   luck and not a guard.
#
# ONE VOCABULARY, EVERYWHERE.  The id is the CORPUS id -- what
# `segments.<Seed>.yaml`, `roads/spec`, `buildstage` and the corridor recheck
# all call this ground -- read from `meta.segment` where the ribbon now stamps
# it, and reconstructed from `params.name` for the records written before that
# stamp existed.  A record that answers neither is "" and is REPORTED: UNREAD
# IS NOT CLEAR.
LAMP_SEGMENT_KEY_DEPENDS_ON = ("meta.segment", "params.name")

_SEGKEY_MODE = "real"       # real | prefix (the known-bad) | off


def segment_id_of(rec: dict) -> str:
    """The corpus segment id this road record names, or "" when it names none."""
    if _SEGKEY_MODE == "off":
        return ""
    name = ((rec.get("params") or {}).get("name")) or ""
    if _SEGKEY_MODE == "prefix":
        return name.split("_")[1] if name.startswith("road_") else name
    seg = ((rec.get("meta") or {}).get("segment")) or ""
    if isinstance(seg, str) and seg:
        return seg
    if name.startswith("road_"):
        # `road_S7_stathub_wtnorth` -> `S7-stathub-wtnorth`: the writer's own
        # spelling of the corpus id with `-` replaced by `_`.
        return name[len("road_"):].replace("_", "-")
    return ""


@dataclass
class Segment:
    """One built road segment as the LEDGER holds it."""

    name: str
    seg_id: str
    file_line: int
    seq: int
    half_width_m: float
    nodes: list[tuple[float, float]]      # (x, z), centreline order
    cum: list[float]                      # arclength at each node
    length_m: float


def segments_from_ledger(world: str = WORLD,
                         actor: str = ACTOR) -> dict[str, Segment]:
    """Every built segment's LATEST centreline, keyed by CORPUS SEGMENT ID.

    LATEST IN FILE ORDER, and both halves matter.  A segment is re-written when
    it is re-graded -- T4 has four `terrain_write` records -- and only the last
    one describes the ground that is there now.  File order rather than `seq`
    because the chain forked at file line 47 and carries duplicate seq labels,
    so `seq` is not a total order on this artefact; `applied.py` makes the same
    choice for the same reason.

    This is also the answer to T4 being re-routed under me: the centreline is
    read here, at the moment of siting, from the record that is currently last.
    """
    from writer import Ledger  # noqa: PLC0415
    led = Ledger.open(world, actor=actor)
    latest: dict[str, tuple[int, dict]] = {}
    for line, rec in enumerate(led.records()):
        if rec.get("op") != "terrain_write":
            continue
        p = rec["params"]
        if p.get("role") != "road_segment":
            continue
        latest[p["name"]] = (line, rec)
    out: dict[str, Segment] = {}
    unkeyed: list[str] = []
    for name, (line, rec) in latest.items():
        seg_id = segment_id_of(rec)
        if not seg_id:
            unkeyed.append(name)
            continue
        prof = rec["params"]["profile"]
        nodes = [(float(n[0]), float(n[1])) for n in prof["nodes"]]
        cum = [0.0]
        for k in range(1, len(nodes)):
            cum.append(cum[-1] + math.dist(nodes[k - 1], nodes[k]))
        out[seg_id] = Segment(name=name, seg_id=seg_id, file_line=line,
                              seq=rec["seq"],
                              half_width_m=float(prof["half_width_m"]),
                              nodes=nodes, cum=cum, length_m=cum[-1])
    if unkeyed:
        # NAMED, never dropped: a graded road nothing can name is a road this
        # step cannot light and cannot report as unlit either.
        sys.stderr.write(f"WARNING: {len(unkeyed)} built road record(s) name "
                         f"no segment and are not lightable: {unkeyed}\n")
    return out


def resample(seg: Segment, step: float = SIGHT_STEP_M) -> list[tuple[float, float, float]]:
    """`(s, x, z)` every `step` metres along the centreline.

    Linear in arclength, which is what the record's own
    `interp: linear_arclength` says the profile is.  Sampling the NODES instead
    would sample unevenly -- node spacing runs from 0.3 m on a bend to 2 m on a
    straight -- and an uneven station list makes "30 m apart" a lie.
    """
    out: list[tuple[float, float, float]] = []
    total = seg.length_m
    k = 0
    s = 0.0
    while s <= total + 1e-9:
        while k + 2 < len(seg.cum) and seg.cum[k + 1] < s:
            k += 1
        span = seg.cum[k + 1] - seg.cum[k]
        t = 0.0 if span <= 0 else (s - seg.cum[k]) / span
        x0, z0 = seg.nodes[k]
        x1, z1 = seg.nodes[k + 1]
        out.append((s, x0 + (x1 - x0) * t, z0 + (z1 - z0) * t))
        s += step
    return out


def zones_for(stations: list[tuple[float, float, float]],
              pad_m: float = 12.0) -> list[tuple[int, int]]:
    """Every zone the corridor plus `pad_m` of slack touches.

    The slack covers the lateral offset, the shoulder and the neighbouring
    zone's shared lattice row.  `applied.surface_at` needs the patch for EVERY
    zone one of the four samples under a point lands in, and a missing patch
    makes it return None -- which reads as "no ground here" and is not.
    """
    zs: set[tuple[int, int]] = set()
    for _s, x, z in stations:
        for dx in (-pad_m, 0.0, pad_m):
            for dz in (-pad_m, 0.0, pad_m):
                zs.add(tcdata.zone_of(x + dx, z + dz))
    # The lattice's boundary row is SHARED between two zones, so a sample on a
    # boundary lives in both and `zone_of` picks one. Ask for the ring.
    ring = set(zs)
    for zx, zz in zs:
        for dzx in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                ring.add((zx + dzx, zz + dzz))
    return sorted(ring)


# --------------------------------------------------------------------------
# the surface, the biome, and the sightline
# --------------------------------------------------------------------------

# ---- THE SEAT SURFACE: THE LATTICE THE GROUND WAS WRITTEN AGAINST --------
#
# THE DEFECT, MEASURED ON FRESH42 DRIVE 3.  Gate `structures/buried` 1B:
# T6-stathub-northcape's roadside `piece_groundtorch` at (-72.331, 39.707,
# 700.750), placed at step 7 (spawn_plan seq 2716), has its OWN TOP 1.168 m
# under the lowest ground anywhere under it.  NOTHING WROTE THERE AFTER IT:
# the only terrain_write in zones (-1,11)/(-2,11) is T6's own carriageway,
# seq 1951, and its blob is byte-exact in the frozen save.  At the vertex
# (-72, 701) that write's delta is -2.4636 m, and the same delta was summed
# onto two different GENERATED surfaces:
#
#     heightmap (HeightmapBuilder, what the engine draws)  44.1612 -> 41.6976
#     worldgen  (WorldGenerator.GetHeight)                 41.5573 -> 39.0937
#
# The gate reads 41.6976 off the save's own Heightmap.  The lamp read 39.0937
# and seated its foot at 39.053: `zone_patches_own` never passed `SURFACE`, so
# PatchScan sampled its default, worldgen, while every terrain_write computes
# `levelDelta = target - generated` against `ribbon.PATCH_SURFACE` and records
# that lattice's digest.  The two agree only in a zone with no biome boundary;
# T6 crosses Meadows/BlackForest.  And the cache `/tmp/roads/lamps/<seg>.bin`
# was keyed on neither surface nor seed: drive 3 read drive 2's file (written
# 2026-09-25 14:45) without looking at what it held.
#
# THE POPULATION, all 115 drive-3 road lamps re-measured on heightmap + ledger
# deltas -- which reproduces the frozen save's Heightmap.GetHeight to <= 0.131 m
# over 4,149 gate points, where worldgen + ledger is off by up to 2.604 m:
# 30 lamps stand > 5 cm off their ground and 17 > 0.5 m, worst 3.780 m of AIR
# under S2-stathub-eastlobe's lamp at (558.3, 291.6) and 2.589 m of burial
# under T6's.  The gate named one; the class is thirty.  And in the ledger, 53
# of the 255 terrain_write zone entries the drive-3 lamp caches cover record a
# lattice those caches do not reproduce, while 263 of 263 checked against the
# heightmap lattice do.
#
# THE FIX IS TWO THINGS, because either alone leaves the class open:
#   1. SAMPLE THE SURFACE THE DELTAS WERE WRITTEN AGAINST -- `ribbon.
#      PATCH_SURFACE`, cached under `ribbon.patch_cache_name`, keyed on the
#      surface AND the seed, so no file can answer for another question.
#   2. PROVE IT, PER ZONE.  Every terrain_write entry records the digest of the
#      generated lattice its deltas are relative to.  A zone whose lattice here
#      digests to anything else -- a stale cache, another seed, another
#      surface, a changed worldgen -- is ground this module has NOT measured,
#      so its samples read as no surface, the seat refuses the spot with the
#      reason, and the window picks a neighbour.  UNREAD IS NOT CLEAR.
LAMP_SEAT_SURFACE_DEPENDS_ON = (
    "ribbon.PATCH_SURFACE",
    "terrain_write.entries[].generated_heights_sha256")

_SEAT_SURFACE_MODE = "real"     # real | worldgen (the shipped known-bad)


def patch_surface() -> str:
    """The generated surface PatchScan is asked to sample for the seat.

    `worldgen` is `run_patchscan.sh`'s own default, which is what the shipped
    request got by not naming one.
    """
    return ribbon.PATCH_SURFACE if _SEAT_SURFACE_MODE == "real" else "worldgen"


def patch_cache_path(out: Path) -> Path:
    """Where the patches asked for as `out` are cached, keyed on SURFACE and
    SEED by the ribbon's own key.  The shipped name was `out` itself."""
    if _SEAT_SURFACE_MODE != "real":
        return out
    return out.with_name(ribbon.patch_cache_name(out, ribbon.PATCH_SURFACE,
                                                 SEED))


_RECORDED: "weakref.WeakKeyDictionary[appliedmod.Applied, dict]" = (
    weakref.WeakKeyDictionary())


def recorded_lattices(live: appliedmod.Applied
                      ) -> dict[tuple[int, int], list[dict]]:
    """Per zone, every terrain_write entry's recorded generated-lattice digest.

    Read off `live.led`, the SAME handle the applied surface decodes its
    deltas from, so the digests and the deltas cannot come from two chains.
    Cached per `Applied` because every segment's `Ground` asks.
    """
    got = _RECORDED.get(live)
    if got is not None:
        return got
    got = {}
    for rec in live.led.records():
        if rec.get("op") != "terrain_write":
            continue
        p = rec.get("params") or {}
        for e in p.get("entries") or ():
            got.setdefault((int(e["zone"][0]), int(e["zone"][1])), []).append(
                {"seq": rec.get("seq"), "name": p.get("name"),
                 "digest": e.get("generated_heights_sha256")})
    _RECORDED[live] = got
    return got


def unverified_lattices(patches: dict,
                        live: appliedmod.Applied) -> dict[str, str]:
    """`{patch id: why}` for every zone whose generated lattice HERE is not
    the one a terrain_write in that zone computed its deltas against.

    A zone nothing ever wrote has no delta to misplace and is not listed.  A
    write that recorded NO digest cannot be checked, and is listed: a seat
    over it would be a guess reported as a measurement.
    """
    if _SEAT_SURFACE_MODE != "real":
        return {}
    rec = recorded_lattices(live)
    out: dict[str, str] = {}
    for pid, patch in patches.items():
        _, zx, zz = pid.split("_")
        writes = rec.get((int(zx), int(zz)))
        if not writes:
            continue
        have = ribbon.generated_digest(patch)
        for w in writes:
            if w["digest"] != have:
                out[pid] = (
                    f"generated lattice is not the one the ground was "
                    f"written against: zone ({zx},{zz}) digests "
                    f"{have[:12]} here, terrain_write seq {w['seq']} "
                    f"({w['name']}) wrote its deltas against "
                    f"{str(w['digest'])[:12]}; the same delta on another "
                    f"generated surface is another ground, so no lamp is "
                    f"seated on it")
                break
    return out


class Ground:
    """The applied surface and the biome, both read off PatchScan zone patches
    plus the ledger's own blobs.

    One object because the two questions share the patch set and decoding it
    twice costs a Unity boot.  The SURFACE reads only lattices proved to be
    the ones the ledger's deltas were written against; see
    `LAMP_SEAT_SURFACE_DEPENDS_ON`.
    """

    def __init__(self, zones: list[tuple[int, int]], out: Path,
                 live: appliedmod.Applied, scan=None):
        self.patches = zone_patches_own(zones, out, scan=scan)
        self.live = live
        self.surface_name = patch_surface()
        self.refused = unverified_lattices(self.patches, live)
        self._seat = {k: v for k, v in self.patches.items()
                      if k not in self.refused}
        self._surf: dict[tuple[float, float], float | None] = {}

    def surface(self, x: float, z: float) -> float | None:
        key = (round(x, 3), round(z, 3))
        got = self._surf.get(key, "miss")
        if got != "miss":
            return got  # type: ignore[return-value]
        res = self.live.surface_at(self._seat, x, z)
        val = None if res is None else float(res[0])
        self._surf[key] = val
        return val

    def why_unmeasured(self, x: float, z: float,
                       reach: float = 0.0) -> str | None:
        """Why ground within `reach` of (x, z) reads no surface, when the
        answer is a refused lattice rather than a zone nobody scanned."""
        for sx in range(math.floor(x - reach), math.floor(x + reach) + 2):
            for sz in range(math.floor(z - reach), math.floor(z + reach) + 2):
                zx, zz = tcdata.zone_of(sx, sz)
                why = self.refused.get(f"z_{zx}_{zz}")
                if why:
                    return why
        return None

    def describe(self) -> str:
        rec = recorded_lattices(self.live) if _SEAT_SURFACE_MODE == "real" \
            else {}
        checked = sum(1 for pid in self.patches
                      if (int(pid.split("_")[1]), int(pid.split("_")[2])) in rec)
        return (f"{self.surface_name} lattice over {len(self.patches)} zone(s), "
                f"{checked} proved against the digests their terrain_writes "
                f"recorded, {len(self.refused)} REFUSED"
                + (f" -- first: {next(iter(self.refused.values()))}"
                   if self.refused else ""))

    def biome(self, x: float, z: float) -> str:
        """`WorldGenerator.GetBiome` at the NEAREST 1 m sample.

        Nearest and not blended, because a biome is a label: the mean of
        Meadows and BlackForest is not a biome and averaging their codes would
        produce Mountain.
        """
        from field import BIOME_NAMES  # noqa: PLC0415
        sx, sz = int(round(x)), int(round(z))
        zx, zz = tcdata.zone_of(sx, sz)
        patch = self.patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return "Unknown"
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return "Unknown"
        return BIOME_NAMES.get(int(patch.biomes[gy * patch.n + gx]), "Unknown")


def zone_patches_own(zones: list[tuple[int, int]], out: Path,
                     scan=None) -> dict:
    """`ribbon.zone_patches`' request geometry AND SURFACE, in THIS agent's
    sandbox.

    Not a call to `ribbon.zone_patches` because that function's sandbox is
    named for ribbon's own actor, and its own docstring records what a shared
    sandbox did: the clean step failed on another uid's BepInEx tree, the scan
    never ran, the stale non-empty output passed the existence check and the
    caller silently got the previous run's zones.  Three writers are live on
    this ledger tonight, so the sandbox is this actor's and the completeness
    check is re-asserted after the scan -- a silent fallback to old ground is
    worse than a crash.  The SURFACE and the cache key are ribbon's, because
    those are what make the patches the ones the deltas were written against.

    `scan(zones, out, surface) -> patches` is the PatchScan run; the selftest
    hands in a constructed one.
    """
    out = patch_cache_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as patchheights  # noqa: PLC0415

    if out.exists() and out.stat().st_size:
        try:
            cached = patchheights.load(out)
            if all(f"z_{zx}_{zz}" in cached for zx, zz in zones):
                return cached
        except Exception:
            pass
    return (scan or _patchscan)(zones, out, patch_surface())


def _patchscan(zones: list[tuple[int, int]], out: Path, surface: str) -> dict:
    """Run PatchScan for `zones` on `surface` into `out`, and prove coverage."""
    import os
    import subprocess
    import heights as patchheights  # noqa: PLC0415

    req = out.with_suffix(".req.tsv")
    lines = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines.append(f"z_{zx}_{zz}\t{cx:g}\t{cz:g}\t32.5\t1")
    req.write_text("\n".join(lines) + "\n")
    env = {**os.environ,
           "VH_SRC": "/media/big4/projects/game/valheim/Ulfsland/data/bepinex",
           "SEED": SEED, "REQ": str(req), "OUT": str(out),
           "SANDBOX": f"/tmp/patchscan/{ACTOR.lower()}",
           "SURFACE": surface}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    got = patchheights.load(out)
    missing = [(zx, zz) for zx, zz in zones if f"z_{zx}_{zz}" not in got]
    if missing:
        raise SystemExit(
            f"patchscan did not produce {len(missing)} requested zones "
            f"{missing[:8]}: the output file exists and is non-empty, so it is "
            f"a PREVIOUS run's data. Refusing to site lamps against ground "
            f"that does not cover them.\n{proc.stderr[-2000:]}")
    return got


def sightline(surf: list[float | None], i: int, direction: int,
              flame_above: float = FLAME_ABOVE_SURFACE_M,
              eye: float = EYE_M, cap_m: float = SIGHT_CAP_M,
              step: float = SIGHT_STEP_M) -> tuple[float, str]:
    """`(metres, why it stopped)` -- the CONTIGUOUS run over which a lamp at
    station `i` stays in view walking `direction`.

    The running maximum of the ground's slope from the flame is the horizon.  A
    station is visible while the slope to the WALKER'S EYE beats it; the first
    station that does not ends the run, because what a walker experiences is
    the stretch over which the lamp is continuously in view, not the farthest
    point from which some line of sight exists.

    `>=` and not `>` on the block test: a station exactly on the horizon is
    grazing, and calling a grazing sightline visible is the kind of rounding
    that turns into "the report said 60 m and I could not see it".
    """
    base = surf[i]
    if base is None:
        return 0.0, "lamp station has no measured surface"
    flame = base + flame_above
    horizon = -math.inf
    reached = 0.0
    n = len(surf)
    steps = int(cap_m / step)
    for t in range(1, steps + 1):
        j = i + direction * t
        if j < 0 or j >= n:
            return reached, "segment end"
        sj = surf[j]
        if sj is None:
            return reached, "unmeasured surface ahead"
        dist = t * step
        if (sj + eye - flame) / dist < horizon:
            return reached, "terrain"
        reached = dist
        # The OCCLUDER is the ground, with no eye height added: the ridge
        # blocks at its own height. Updated after the visibility test so the
        # station being tested cannot occlude itself.
        horizon = max(horizon, (sj - flame) / dist)
    return reached, "cap"


# --------------------------------------------------------------------------
# keep-outs
# --------------------------------------------------------------------------

class KeepOut:
    """Everywhere a lamp may not stand, with the reason it may not.

    Every member is asked of the instrument that owns it.  Nothing here is a
    radius this module invented.
    """

    def __init__(self, zones: list[tuple[int, int]], live: appliedmod.Applied,
                 actor: str = ACTOR):
        import yaml  # noqa: PLC0415
        from writer import Ledger  # noqa: PLC0415

        sites_path = worldseed.sites_path()
        site_list = ([dict(s) for s in
                      yaml.safe_load(sites_path.read_text())["sites"]]
                     if sites_path.exists() else [])
        led = Ledger.open(WORLD, actor=actor)
        self.pads = ribbon.pad_keepouts_from_chain(site_list, led.records(),
                                                   ledger=led)
        if self.pads.undecodable:
            raise SystemExit(
                f"REFUSING: {len(self.pads.undecodable)} pad terrain_write "
                f"entry(ies) could not be decoded, so the ground those pads "
                f"levelled is unknown and a lamp could land on a floor. "
                f"{self.pads.undecodable[:4]}")
        self.poi = ribbon.poi_standoff_discs(zones)
        prot = ribbon.protected_piece_positions(zones)
        self.pieces = prot["pieces"]
        self.discs = prot["discs"]
        self.prot_by_site = prot["by_site"]
        self.without_pieces = prot["without_pieces"]
        self.live = live
        # EVERY BUILT CARRIAGEWAY, with its OWN half width, from the record
        # that graded it.  Not the segment being lit - every one of them -
        # because `temple` is an endpoint of six segments and a lamp on one
        # road's shoulder is in the next road's lane.  Read from the same
        # `segments_from_ledger` the siting pass uses, so the keep-out and
        # the offset cannot be computed from two different worlds.
        self.carriageways = [(s.name, s.nodes, s.half_width_m)
                             for s in segments_from_ledger(
                                 WORLD, actor=actor).values()]

    def why_not(self, x: float, z: float) -> str | None:
        """The reason this spot is unusable, or None.

        A string and not a bool: "refused" is not something an operator can
        act on, and the refusal list in the report is only useful if each row
        names its cause.
        """
        sid = self.pads.contains(x, z)
        if sid is not None:
            return f"inside site pad footprint {sid}"
        for cx, cz, r in self.poi:
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r:
                return f"inside POI stand-off {r:.1f} m of ({cx:.1f}, {cz:.1f})"
        for cx, cz, r in self.discs:
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r:
                return (f"inside a protected structure's own recorded extent "
                        f"{r:.1f} m of ({cx:.1f}, {cz:.1f})")
        for px, pz in self.pieces:
            if (x - px) ** 2 + (z - pz) ** 2 <= PIECE_CLEAR_M ** 2:
                return (f"within {PIECE_CLEAR_M:.2f} m of an existing piece at "
                        f"({px:.1f}, {pz:.1f})")
        road = self.carriageway_why_not(x, z)
        if road is not None:
            return road
        foreign = self.live.foreign_at(x, z)
        if foreign is not None:
            return (f"ground here is authored by {foreign.get('name')} "
                    f"seq {foreign.get('seq')} role {foreign.get('role')}, "
                    f"not the road")
        return None

    def carriageway_why_not(self, x: float, z: float) -> str | None:
        """Is this spot INSIDE any built carriageway?

        THE OFFSET IS A CONSTRUCTION; THIS IS THE MEASUREMENT.  See
        `LAMP_HALF_FOOTPRINT_M` above for the population that made this
        necessary: 4,632 of 43,908 candidate positions the offset geometry
        can produce land inside a carriageway, 3,338 of them inside the
        lamp's OWN road.  Asked against every segment's own half width, over
        the whole polyline, with the lamp's own foot included - because a
        lamp whose shaft clears the lane and whose foot does not is still
        something a cart hits.
        """
        worst = None
        for name, nodes, hw in self.carriageways:
            d = perpendicular_m(x, z, nodes)
            need = hw + LAMP_HALF_FOOTPRINT_M
            if d < need and (worst is None or d - need < worst[1]):
                worst = ((name, d, hw), d - need)
        if worst is None:
            return None
        name, d, hw = worst[0]
        return (f"{d:.3f} m from the centreline of {name}, INSIDE its "
                f"{hw:.1f} m half-width (+{LAMP_HALF_FOOTPRINT_M:.2f} m for "
                f"the lamp's own foot): that is the carriageway, not the "
                f"shoulder. A lamp there is an obstacle, not designed "
                f"occupancy. The designed offset is "
                f"{hw + SHOULDER_STANDOFF_M:.2f} m and this candidate's "
                f"lateral was CONSTRUCTED from one station's chord normal, "
                f"never measured against the whole polyline.")


# --------------------------------------------------------------------------
# THE PROXIMITY GATE: WHAT IS ALREADY STANDING
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS, from the operator: "some of the lamposts were very close to
# each other.. probably a fence-post error or something. you might add to the
# lamp post logic to check if there is already a lamp-post nearby."
#
# THE DEFECT, NAMED IN THIS FILE.  The spacing decision is
# `_biome_allows`'s `gap = s - last_s; if gap < pol["min_m"]: return False`,
# and the population it considers is `last_s` ALONE -- the previous lamp in
# THIS call's own greedy chain on THIS segment.  `last_s` is a local of
# `site()` initialised to None per segment, and `site()` reads no world.  So
# the minimum spacing was enforced against exactly one lamp, and the pass was
# blind to:
#
#   1. LAMPS ON A NEIGHBOURING SEGMENT.  `temple` and `stathub` are each an
#      endpoint of SIX segments and `w-mid` of four, and this project has
#      already measured that two roads closer than their combined slope widths
#      are ONE corridor (29.0 m shared, 14.5 m per side).  MEASURED: 7 of the
#      34 sub-30 m pairs are cross-segment at a shared endpoint, the tightest
#      being T4@1395 against T7@52 at 6.346 m with CONSECUTIVE ZDO ids
#      97768:1 / 97767:1 -- one run sited T4, then T7, and lit stathub twice.
#
#   2. LAMPS ALREADY STANDING FROM AN EARLIER RUN, which is the LARGEST
#      contributor and was not in the brief.  MEASURED: the 138 markers fall
#      into 15 ZDO-id families, and 11 of the 34 sub-30 m pairs span families
#      thousands of ids apart -- 4.019 m at |did| 1943, 8.302 m at 1966,
#      13.020 m at 1825.  W10-brgs1-treenear is the worst: 9 markers over
#      466 m with consecutive gaps [5.8, 30.6, 0.4, 29.6, 12.4, 22.6, 19.4,
#      13.9] and two interleaved id families.  A greedy walk that starts from
#      `last_s = None` cannot tell lit ground from dark ground, so a second
#      pass re-lights the road from whatever phase it happens to start in.
#
#   3. LAMPS PLACED BY SOMETHING THAT IS NOT THE LIGHTING PASS.  MEASURED: of
#      152 recorded `piece_groundtorch` placements, 133 are `Lampposts`, 14 are
#      the Sudrberg keep (`KeepBuild` seq 753), 3 the harbour (`WaterfrontRun`
#      seq 2080) and 2 the west lighthouse (`OutlierBuild` seq 613).  A
#      segment-local counter can never see any of those.
#
#   4. ITS OWN CHAIN BREAK.  `site()` sets `last_s = None` when a window holds
#      nothing legal, and `_biome_allows` then takes its `last_s is None`
#      branch, which tests ONLY `s - base_s <= pol["max_m"]`.  There is no
#      minimum in that branch at all, so the first lamp after a break has its
#      spacing unchecked against the lamp before the break.  The gate closes
#      this one too, and closes it WITHOUT touching the chain-break logic:
#      the previous lamp is in `staged`, so the census refuses the candidate
#      whatever the arclength arithmetic decides.
#
# THE FIX IS A READ, NOT A COUNTER.  Asking the world is the only rule that is
# correct under all four, because all four are the same defect -- a decision
# made from local state about a shared world.
#
# TRAP ONE, THE CUBE.  `findObjects -near x y z r` and `deleteObjects -near`
# are CUBES of half-extent r in ALL THREE AXES, not cylinders -- MEASURED in
# `clear.py::list_prefab`, where one prefab answered 2 rows for a 7.42 m box
# and 8 for a 16 m one because six beams stood above the small box's ceiling.
# A y-blind census on this world has already produced 64 duplicate pieces,
# caught only at 1319 for a 1255-piece body.  So the gate asks the CUBE that
# strictly CONTAINS the cylinder it wants and then filters the returned
# positions down to the cylinder in Python.
#
# TRAP TWO, THE RUN ITSELF.  A live census sees what is STANDING, not what
# this same pass decided four candidates ago.  Hence `stage()`: the gate tests
# against the union of live and staged, and without it one run still
# duplicates -- it just does it faster than the server can be asked.
#
# IDEMPOTENCE IS A CONSEQUENCE, NOT AN EXTRA FEATURE.  Re-running the pass
# over ground it has already lit finds every candidate occupied by its own
# earlier lamp, places nothing, and reports the skips.

class GateRefusal(RuntimeError):
    """The gate could not answer, so nothing may be placed on its word."""


class Standing:
    """Every lamp already in the world near these corridors, plus this run's.

    TWO READS, AT TWO DIFFERENT COSTS, ON PURPOSE.  `why_not` answers from a
    census taken once for the whole run, because the siting walk asks about
    thousands of candidates and one RCON round trip each would be a pass
    measured in hours.  `confirm` re-asks the LIVE world about ONE position at
    the moment of writing, which is the read-before-write the harness manual
    specifies and which no builder in this tree had -- its absence is why a
    lighthouse interrupted at 272 of 1255 pieces had to be demolished rather
    than resumed.  Cheap while searching, strict at the boundary that matters.
    """

    def __init__(self, srv, segs: list["Segment"],
                 reach_m: float = STANDING_REACH_M,
                 cell_m: float = STANDING_CELL_M,
                 prefabs: tuple = STANDING_PREFABS,
                 y_half_m: float = STANDING_Y_HALF_M):
        import clear as C  # noqa: PLC0415

        self._C = C
        self.prefabs = tuple(prefabs)
        self.y_half_m = float(y_half_m)
        # DEDUPLICATED BY ZDO ID, and this was a defect before it was a
        # comment.  The cells tile the plane but the census is STACKED over
        # `STANDING_Y_LEVELS` and `list_prefab` subdivides a dense box into
        # eight children, so one ZDO is legitimately returned by more than one
        # query -- a lamp at y 128..256 lands in BOTH y levels' cubes.  A list
        # extended per query therefore holds it twice.  That does not change
        # `why_not` (a duplicate still refuses), which is exactly why it would
        # have survived: it is invisible until something COUNTS `live`, and
        # then it reports 83 mist torches where 26 stand.  MEASURED on the
        # post-cull re-census, which is the only reason this is a dict.
        self._live: dict[str, dict] = {}
        self.staged: list[dict] = []
        self.calls = 0
        self.cells = self._cells(segs, reach_m, cell_m)
        half = cell_m / 2.0
        for n, (cx, cy, cz) in enumerate(self.cells, 1):
            for prefab in self.prefabs:
                try:
                    got, unlisted, k = C.list_prefab(srv, prefab, cx, cy, cz,
                                                     half)
                except C.AskTimeout as exc:
                    raise GateRefusal(
                        f"the standing-lamp census could not read the "
                        f"{cell_m:g} m cell at ({cx:.0f}, {cy:.0f}, {cz:.0f}) "
                        f"for {prefab}: {exc}. Refusing to site lamps against "
                        f"a partially-read world -- an unanswerable gate is "
                        f"not an empty one.") from exc
                if unlisted:
                    raise GateRefusal(
                        f"the standing-lamp census left {len(unlisted)} "
                        f"box(es) too dense to list even at "
                        f"{C.MIN_LIST_HALF_M} m half-extent: {unlisted[:3]}. "
                        f"Those cylinders' contents are unknown and a lamp "
                        f"will not be sited against them.")
                self.calls += k
                self._live.update(got)
            print(f"  standing census cell {n}/{len(self.cells)} "
                  f"({cx:.0f},{cz:.0f}) lamps {len(self._live)} "
                  f"calls {self.calls}", flush=True)

    @property
    def live(self) -> list[dict]:
        """The standing lamps, ONE ENTRY PER ZDO ID."""
        return list(self._live.values())

    @classmethod
    def from_records(cls, records: list[dict], *,
                     prefabs: tuple = STANDING_PREFABS,
                     y_half_m: float = STANDING_Y_HALF_M) -> "Standing":
        """A gate seeded from a census ALREADY TAKEN, with no server.

        Two real uses and neither is a shortcut around the live read.  First,
        `confirm` still re-asks the live world before any write, so a
        `from_records` gate is exactly as strict at the write boundary as a
        freshly censused one.  Second, it is how the gate is TESTED: a
        proximity rule whose behaviour can only be observed by mutating a live
        world is a rule nobody will check again.

        `records` is required positionally and is not defaulted: an
        accidentally EMPTY gate passes everything, which is the failure this
        whole class exists to prevent.
        """
        self = cls.__new__(cls)
        import clear as C  # noqa: PLC0415
        self._C = C
        self.prefabs = tuple(prefabs)
        self.y_half_m = float(y_half_m)
        self._live = {str(r.get("id", f"seed/{i}")): dict(r)
                      for i, r in enumerate(records)}
        self.staged = []
        self.calls = 0
        self.cells = []
        return self

    @staticmethod
    def _cells(segs, reach_m: float, cell_m: float) -> list[tuple]:
        """Disjoint `cell_m` XZ cells covering these corridors, STACKED over
        `STANDING_Y_LEVELS`.

        The cells tile the plane, so each object is echoed once per y level
        rather than once per overlapping query region.  The y levels are
        DECLARED rather than derived: `Segment` has no per-node height to
        derive one from, and a cube centred on a guessed y is a question whose
        range nobody stated.  `_assert_in_band` enforces the declared range at
        query time so a road above it cannot be answered from a cube that
        never looked there.
        """
        h = cell_m / 2.0
        keep: set[tuple[int, int]] = set()
        for seg in segs:
            pts = resample(seg)
            # Every 16th 1 m station: a 512 m cell cannot be entered and left
            # inside 16 m, so this cannot miss a cell the polyline crosses.
            for i in range(0, len(pts), 16):
                _s, sx, sz = pts[i]
                i0 = int(math.floor((sx - reach_m) / cell_m))
                i1 = int(math.floor((sx + reach_m) / cell_m))
                j0 = int(math.floor((sz - reach_m) / cell_m))
                j1 = int(math.floor((sz + reach_m) / cell_m))
                for ii in range(i0, i1 + 1):
                    for jj in range(j0, j1 + 1):
                        cx = ii * cell_m + h
                        cz = jj * cell_m + h
                        dx = max(abs(sx - cx) - h, 0.0)
                        dz = max(abs(sz - cz) - h, 0.0)
                        if dx * dx + dz * dz <= reach_m * reach_m:
                            keep.add((ii, jj))
        out = []
        for (ii, jj) in sorted(keep):
            for cy in STANDING_Y_LEVELS:
                out.append((ii * cell_m + h, cy, jj * cell_m + h))
        return out

    @staticmethod
    def _assert_in_band(y: float, y_half_m: float) -> None:
        """REFUSE a query whose cylinder leaves the censused y band.

        Without this the gate answers "nothing nearby" for a lamp above the
        band, which is the most dangerous answer it can give: a clean pass
        that placed a duplicate. MEASURED reason to care: this network's lamps
        span y 31 to 133, and TrunkNorth's northern trunk enters Mountain.
        """
        if (y - y_half_m) < STANDING_Y_MIN_M or \
                (y + y_half_m) > STANDING_Y_MAX_M:
            raise GateRefusal(
                f"a lamp at y {y:.2f} m (+/-{y_half_m:g} m) is outside the "
                f"standing census's declared y band "
                f"{STANDING_Y_MIN_M:.0f}..{STANDING_Y_MAX_M:.0f} m, so the "
                f"census never looked there and CANNOT say the spot is free. "
                f"Add a level to STANDING_Y_LEVELS covering it and re-census. "
                f"Answering 'nothing nearby' here would be a clean-looking "
                f"pass that placed a duplicate.")

    def cylinder(self, min_m: float) -> tuple[float, float]:
        """`(radius, half_height)` a lamp must keep clear of its neighbours:
        the gate's limits PLUS the readback margin on both axes.  The gate
        refuses a pair at `d < min_m` and `dy <= y_half_m`, measured on the
        coordinates it reads back, so a placement standing exactly on either
        limit here can stand on the wrong side of it there -- see the block
        at `READBACK_MARGIN_M`."""
        margin = readback_margin_m()
        return min_m + margin, self.y_half_m + margin

    def near(self, x: float, y: float, z: float, min_m: float) -> list[dict]:
        """Lamps inside the CYLINDER of radius `min_m` (and the readback
        margin), half-height `y_half_m` (likewise), about this point -- live
        and staged together."""
        radius, y_reach = self.cylinder(min_m)
        self._assert_in_band(y, y_reach)
        out = []
        for rec in (self.live + self.staged):
            d = math.hypot(rec["x"] - x, rec["z"] - z)
            dy = abs(rec["y"] - y)
            if d <= radius and dy <= y_reach:
                out.append({**rec, "dist_m": round(d, 3),
                            "dy_m": round(dy, 3)})
        out.sort(key=lambda r: r["dist_m"])
        return out

    def why_not(self, x: float, y: float, z: float,
                min_m: float) -> str | None:
        """The reason a lamp may not stand here, or None.

        A string and not a bool, matching `KeepOut.why_not`: "refused" is not
        something an operator can act on, and the refusal list in the report
        is only useful if each row names its cause.
        """
        got = self.near(x, y, z, min_m)
        if not got:
            return None
        n = got[0]
        return (f"a {n['prefab']} already stands {n['dist_m']:.2f} m away "
                f"({n['dy_m']:.2f} m in y) at "
                f"({n['x']:.1f}, {n['y']:.1f}, {n['z']:.1f}) ZDO "
                f"{n.get('id', 'staged')}, inside this biome's {min_m:.0f} m "
                f"minimum spacing"
                + (f"; {len(got)} lamp(s) in the cylinder" if len(got) > 1
                   else ""))

    def stage(self, x: float, y: float, z: float, seg: str,
              prefab: str = LAMP_PREFAB) -> None:
        """Record a lamp this run has decided to place.

        The server has not been told yet, so nothing else can see it -- which
        is precisely why it has to be held here.
        """
        self.staged.append({"prefab": prefab, "x": float(x), "y": float(y),
                            "z": float(z), "id": f"staged/{seg}",
                            "source": "staged"})

    def confirm(self, srv, x: float, y: float, z: float,
                min_m: float) -> dict:
        """RE-ASK THE LIVE WORLD about ONE position, immediately before the
        write.  This is the read-before-write census gate.

        The cube asked for is `max(radius, y_reach)` of `cylinder(min_m)`,
        which strictly CONTAINS the cylinder of radius `min_m` and half-height
        `y_half_m` (each plus the readback margin), so nothing inside the
        cylinder can fall outside the question; the reply is then filtered to
        the cylinder here.  Handing the cube's verdict back would refuse a
        lamp 8 m away and 40 m below, which no player reads as a cluster.

        Raises `GateRefusal` rather than answering "free" when the question
        could not be asked.
        """
        radius, y_reach = self.cylinder(min_m)
        self._assert_in_band(y, y_reach)
        half = max(radius, y_reach)
        found: list[dict] = []
        for prefab in self.prefabs:
            try:
                got, unlisted, _ = self._C.list_prefab(srv, prefab, x, y, z,
                                                       half)
            except self._C.AskTimeout as exc:
                raise GateRefusal(
                    f"the read-before-write census at ({x:.2f}, {y:.2f}, "
                    f"{z:.2f}) for {prefab} did not answer: {exc}. Refusing "
                    f"to place a lamp into an unread cylinder.") from exc
            if unlisted:
                raise GateRefusal(
                    f"the read-before-write census at ({x:.2f}, {y:.2f}, "
                    f"{z:.2f}) left {len(unlisted)} box(es) unlistable; that "
                    f"cylinder's contents are unknown.")
            for rec in got.values():
                d = math.hypot(rec["x"] - x, rec["z"] - z)
                dy = abs(rec["y"] - y)
                if d <= radius and dy <= y_reach:
                    found.append({**rec, "dist_m": round(d, 3),
                                  "dy_m": round(dy, 3)})
        found.sort(key=lambda r: r["dist_m"])
        return {
            "occupied": bool(found),
            "position": [round(x, 3), round(y, 3), round(z, 3)],
            "min_spacing_m": min_m, "y_half_m": self.y_half_m,
            "readback_margin_m": readback_margin_m(),
            "cube_half_asked_m": half,
            "nearest_m": found[0]["dist_m"] if found else None,
            "neighbours": found,
            "method": (f"MEASURED live: `findObjects -prefab P -near x y z "
                       f"{half:g}` per ground-torch prefab -- a CUBE that "
                       f"strictly contains the target cylinder -- then "
                       f"filtered to horizontal distance <= {radius:g} m AND "
                       f"|dy| <= {y_reach:g} m ({min_m:g} m and "
                       f"{self.y_half_m:g} m plus the readback margin)."),
            "tool": "tools/jumpstart/roads/lamps.py::Standing.confirm"}

    def describe(self) -> str:
        by: dict[str, int] = {}
        for r in self.live:
            by[r["prefab"]] = by.get(r["prefab"], 0) + 1
        return (f"{len(self.live)} lamp(s) already standing within "
                f"{STANDING_REACH_M:g} m of these corridors {by or '{}'}, "
                f"read in {self.calls} socket call(s) over "
                f"{len(self.cells)} cell-level(s), y band "
                f"{STANDING_Y_MIN_M:.0f}..{STANDING_Y_MAX_M:.0f} m")


# --------------------------------------------------------------------------
# siting
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    s: float
    x: float
    z: float
    side: int
    lamp_x: float
    lamp_z: float
    surface_y: float
    yaw_deg: float
    biome: str
    fwd_m: float
    back_m: float
    fwd_why: str
    back_why: str
    relief_m: float = 0.0

    @property
    def score(self) -> float:
        return min(self.fwd_m, self.back_m)


@dataclass
class Plan:
    seg: str
    lamps: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The dark stretches no legal lamp could light, each with its owners --
    # see `site`.  Empty is the only answer the lighting gate cannot refuse.
    unlit: list[dict] = field(default_factory=list)
    # The finished plan re-measured by the gate's own `unlit_runs`.
    coverage: dict = field(default_factory=dict)


def _tangent(stations: list[tuple[float, float, float]], i: int) -> tuple[float, float]:
    a = max(0, i - 2)
    b = min(len(stations) - 1, i + 2)
    dx = stations[b][1] - stations[a][1]
    dz = stations[b][2] - stations[a][2]
    n = math.hypot(dx, dz)
    return (1.0, 0.0) if n == 0 else (dx / n, dz / n)


def _candidate(seg: Segment, stations: list[tuple[float, float, float]],
               surf: list[float | None], ground: "Ground", i: int, side: int,
               eye: float = EYE_M) -> tuple[Candidate | None, str | None]:
    """The lamp spot beside station `i` on `side`, seated and scored -- or the
    reason there is none.

    The sightline is computed from the LAMP's own surface, so the two sides
    can score differently -- and on a side-cut road they do.
    """
    s, cx, cz = stations[i]
    tx, tz = _tangent(stations, i)
    # Left of travel is (-tz, tx); side=+1 is left, -1 is right.
    off = seg.half_width_m + SHOULDER_STANDOFF_M
    lx = cx + side * (-tz) * off
    lz = cz + side * (tx) * off
    # DECIDE ON WHAT WILL BE WRITTEN: the staged lamp, the ledger plan and the
    # spawn command are then one coordinate, and a separation the walk
    # measures is a separation `lamp_unit` can reproduce -- see
    # `READBACK_MARGIN_M` for the 29.999712 m pair this closes.
    lx, lz = written_xz(lx, lz)
    # SEAT ON THE DEEPEST GROUND UNDER THE FOOTPRINT, NOT ON THE CENTRE.
    # The lamp's foot is a 0.23 x 0.24 m rectangle and the shoulder is
    # graded, not flat: MEASURED on S1, seating on the centre sample left
    # 4.93 cm of air under the downhill corner. Air under a foot is the
    # floating defect in miniature; a centimetre of burial is invisible.
    # So the seat takes the minimum over the centre and the four corners
    # and the residual is reported either way by `seat_check`.
    sy, relief = seat_surface(ground, lx, lz)
    if sy is None:
        hx, hz = footprint_half_extents()
        return None, (ground.why_unmeasured(lx, lz, max(hx, hz))
                      or "footprint reaches ground with no measured "
                         "surface")
    if sy <= WATER_LEVEL_M + FREEBOARD_M:
        return None, (f"surface {sy:.3f} m is at or under the water plane "
                      f"+ {FREEBOARD_M} m freeboard")
    if relief > FOOTPRINT_RELIEF_MAX_M:
        return None, (f"ground rises and falls {relief:.3f} m across the "
                      f"lamp's own footprint, over the "
                      f"{FOOTPRINT_RELIEF_MAX_M} m limit; a lamp here "
                      f"stands in a bank")
    biome = ground.biome(lx, lz)
    # Yaw: face the lamp across the road, so its shaft's flat faces read
    # as a roadside post rather than a random rotation. The light is a
    # POINT light, so yaw does not change what it lights -- measured, and
    # that is why this is a cosmetic choice and is allowed to be one.
    yaw = math.degrees(math.atan2(side * -tz, side * tx))
    fwd, fwhy = sightline(surf, i, +1, eye=eye)
    back, bwhy = sightline(surf, i, -1, eye=eye)
    return Candidate(s=s, x=cx, z=cz, side=side, lamp_x=lx, lamp_z=lz,
                     surface_y=sy, yaw_deg=yaw, biome=biome,
                     fwd_m=fwd, back_m=back, fwd_why=fwhy, back_why=bwhy,
                     relief_m=relief), None


def _surface_profile(seg: Segment, ground: "Ground", plan: Plan
                     ) -> tuple[list[tuple[float, float, float]],
                                list[float | None]]:
    stations = resample(seg)
    surf = [ground.surface(x, z) for _s, x, z in stations]
    holes = sum(1 for v in surf if v is None)
    if holes:
        plan.notes.append(
            f"{holes} of {len(surf)} centreline stations have no measured "
            f"applied surface; sightlines terminate there rather than "
            f"assuming flat ground, and the gate counts them unmeasured, "
            f"not dark")
    return stations, surf


class Coverage:
    """Which stations of ONE segment have a flame in view -- asked the way
    `accept/sections/lighting.py` asks it, with its own functions.

    The population is the gate's (`classify_lamps`: every ground-torch
    variant within its corridor of ANY built centreline, so a flame filed
    under a neighbouring road lights this one), the lamp-to-station map is
    the gate's (`lamp_index`), and the sightline is the gate's
    (`_approach.visible`, `EYE_M` over the applied surface to the flame
    `FLAME_ABOVE_PIVOT_M` over the pivot).  The one thing restated is the
    per-station loop of `unlit_runs`, because a walk has to know WHICH station
    is dark and the gate only says how many -- so `verify` asks `unlit_runs`
    itself and REFUSES on any disagreement.

    THE SIGHT DISTANCE IS A LIMIT TOO.  The gate calls a station lit when a
    flame is within `sight_m` of it, measured on the flame's coordinates as
    the census prints them back, so a flame sited 30.0000 m from a station is
    lit here and can be 30.0003 m from it there.  The walk therefore counts a
    station lit only inside `sight_m - readback_margin_m()` (`seen_m`), and
    `verify` asks the gate's own `unlit_runs` at that same distance, so the
    two stay one loop: the walk errs toward a lamp, never toward a dark
    station.
    """

    def __init__(self, stations: list[tuple[float, float, float]],
                 surf: list[float | None], carriageways: list[tuple],
                 flames: list[dict], eye: float = EYE_M,
                 sight_m: float = SIGHT_M):
        self.stations = stations
        self.surf = surf
        self.eye = eye
        self.sight_m = sight_m
        self.seen_m = sight_m - readback_margin_m()
        self.reach_m = max(GATE.SIGHT_DISTANCES_M)
        self.segs = {name: {"nodes": nodes, "half_width_m": hw}
                     for name, nodes, hw in carriageways}
        corridor = GATE.classify_lamps(
            [(f["prefab"], f["x"], f["y"], f["z"]) for f in flames], self.segs)
        self.idx: list[tuple] = GATE.lamp_index(stations, corridor,
                                                self.reach_m)
        self.lit = [self.sees(i, self.idx) for i in range(len(stations))]

    def entry(self, x: float, y: float, z: float) -> tuple | None:
        """A lamp whose PIVOT is here, as the gate indexes it:
        `(station, x, z, flame_y)`.  A candidate stands at its own road's
        designed offset, inside the gate's corridor by construction."""
        got = GATE.lamp_index(self.stations, [{"x": x, "y": y, "z": z}],
                              self.reach_m)
        return got[0] if got else None

    def sees(self, i: int, entries: list[tuple]) -> bool:
        """Has station `i` one of `entries` in view?  `unlit_runs`' own test."""
        y = self.surf[i]
        if y is None:
            return True     # the gate's rule: UNMEASURED IS NOT DARK
        eye = y + self.eye
        _s, x, z = self.stations[i]
        for (j, lx, lz, flame) in entries:
            if math.hypot(lx - x, lz - z) > self.seen_m:
                continue
            if GATE.A.visible(self.surf, i, j, eye, flame):
                return True
        return False

    def first_dark(self, start: int) -> int | None:
        for k in range(start, len(self.lit)):
            if not self.lit[k]:
                return k
        return None

    def reach(self, ent: tuple, d: int) -> int:
        """Stations from `d` onward that would be lit, unbroken, with `ent`."""
        k = d
        while k < len(self.lit) and (self.lit[k] or self.sees(k, [ent])):
            k += 1
        return k - d

    def add(self, ent: tuple) -> None:
        self.idx.append(ent)
        for k, ok in enumerate(self.lit):
            if not ok and self.sees(k, [ent]):
                self.lit[k] = True

    def verify(self) -> dict:
        """The gate's own `unlit_runs` at every sight distance it reports,
        over the same index -- and a refusal if it disagrees with the walk."""
        out = {}
        for sight in GATE.SIGHT_DISTANCES_M:
            worst, at, unlit, unread = GATE.unlit_runs(
                self.stations, self.surf, self.idx, sight)
            out[f"sight_{sight:.0f}m"] = {
                "worst_unlit_run_m": worst,
                "ends_at": [round(at[0], 1), round(at[1], 1)] if at else None,
                "unlit_stations": unlit, "stations": len(self.stations),
                "stations_with_no_applied_reading": unread}
        mine = sum(1 for v in self.lit if not v)
        theirs = GATE.unlit_runs(self.stations, self.surf, self.idx,
                                 self.seen_m)[2]
        if self.eye == GATE.EYE_M and mine != theirs:
            raise RuntimeError(
                f"REFUSING: the siting walk counts {mine} dark station(s) and "
                f"the lighting gate's own unlit_runs counts {theirs} over the "
                f"same flames and surface. The walk's per-station test is a "
                f"restatement of the gate's loop and has drifted from it; a "
                f"placement that passes its own check and fails the world's "
                f"is the defect this class exists to close.")
        return out


def site(seg: Segment, ground: Ground, keep: KeepOut,
         standing: "Standing | None" = None, eye: float = EYE_M) -> Plan:
    """Lamps along one segment until every station has a flame in view AS THE
    LIGHTING GATE MEASURES IT -- or, where no lamp can stand, the dark stretch
    REFUSED by number with the owners of every spot that could have lit it.

    THE WALK.  Go to the first station with no flame in view (`Coverage`,
    counting every corridor torch standing in the world or staged by this
    run).  Among the legal spots that would put a flame in view of it, take
    the one that lights the most road onward from it -- the gate's own
    quantity, through the terrain, so a crest that hides the next 20 m loses
    to a spot that does not -- then the operator's two-direction sightline,
    then the later station.  Stage it and go again.

    LEGAL is every rule that was already here -- the seat on the drawn
    ground, the water plane, the footprint relief, the keep-outs, the biome's
    published minimum against every lamp standing or staged, and, while this
    run's own chain is unbroken, the biome's maximum gap, which is what keeps
    a forest road denser than the gate alone asks.  Each is relaxed ONLY for a
    station that would otherwise be dark and only as far as the gate itself
    goes: first the maximum gap (the chain breaks), then the biome minimum
    down to the gate's own `MIN_SPACING_M`.  MEASURED on T7's last 115 m: every
    spot is within Meadows' 55 m of a Stenvik house torch 42-53 m away that
    stands too far off the road to light it, and 30 m clear of all of them.
    Keep-outs, water, relief and seating are never relaxed.

    Greedy from the start, deliberately: a global optimum makes every lamp's
    position depend on every other one, so a re-route of the far end moves the
    near end.  A greedy walk is also what a road builder does.
    """
    if _COVERAGE_MODE == "spacing":
        return _site_by_spacing(seg, ground, keep, standing, eye)
    plan = Plan(seg=seg.seg_id)
    stations, surf = _surface_profile(seg, ground, plan)
    # THE WORLD'S LAMPS AND THIS RUN'S.  Offline, with no census, a gate of
    # this segment's own lamps only: their spacing still holds, and the
    # report can only say what this run could see.
    gate = standing if standing is not None else Standing.from_records([])
    cov = Coverage(stations, surf, keep.carriageways,
                   gate.live + gate.staged, eye=eye)
    off = seg.half_width_m + SHOULDER_STANDOFF_M
    spots: dict[tuple[int, int], tuple] = {}
    kept: dict[tuple[int, int], str | None] = {}
    entries: dict[tuple[int, int], tuple | None] = {}
    recorded: set[tuple[int, int]] = set()

    def refuse(key, cand, reason: str, **extra) -> None:
        if key in recorded:
            return
        recorded.add(key)
        row = {"s_m": round(stations[key[0]][0], 1), "side": key[1],
               "reason": reason, **extra}
        if cand is not None:
            row.update(x=round(cand.lamp_x, 2), z=round(cand.lamp_z, 2))
        plan.refusals.append(row)

    def window(d: int, last: dict | None, floor: str):
        """Legal spots that put a flame in view of station `d`, and why every
        other spot within the gate's sight distance of it could not."""
        _sd, dx, dz = stations[d]
        out, why = [], []
        for i, (_s, x, z) in enumerate(stations):
            if math.hypot(x - dx, z - dz) > cov.sight_m + off:
                continue
            for side in (+1, -1):
                key = (i, side)
                if key not in spots:
                    spots[key] = _candidate(seg, stations, surf, ground, i,
                                            side, eye)
                cand, bad = spots[key]
                if cand is None:
                    refuse(key, None, bad)
                    why.append(bad)
                    continue
                pol = BIOME_POLICY.get(cand.biome, DEFAULT_POLICY)
                if pol is None:
                    bad = (f"biome {cand.biome} has no published spacing "
                           f"policy; refusing rather than defaulting")
                    refuse(key, cand, bad)
                    why.append(bad)
                    continue
                if last is not None and cand.s - last["s"] > pol["max_m"]:
                    continue        # the density bound: a window, not a refusal
                if key not in kept:
                    kept[key] = keep.why_not(cand.lamp_x, cand.lamp_z)
                if kept[key]:
                    refuse(key, cand, kept[key])
                    why.append(kept[key])
                    continue
                # THE PROXIMITY GATE, asked of the world and of this run's
                # staged lamps.  The y is the lamp's PIVOT, the datum the
                # census reads off the standing lamps.
                y = cand.surface_y + seat_offset_m()
                min_m = (pol["min_m"] if floor == "biome"
                         else GATE.MIN_SPACING_M)
                bad = gate.why_not(cand.lamp_x, y, cand.lamp_z, min_m)
                if bad:
                    refuse(key, cand, bad, gate="proximity")
                    why.append(bad)
                    continue
                if key not in entries:
                    entries[key] = cov.entry(cand.lamp_x, y, cand.lamp_z)
                ent = entries[key]
                if ent is None or not cov.sees(d, [ent]):
                    if ent is not None and math.hypot(
                            ent[1] - dx, ent[2] - dz) <= cov.sight_m:
                        why.append(f"terrain: the applied surface hides the "
                                   f"flame of the legal spot at "
                                   f"s={cand.s:.0f} m from this station")
                    continue
                out.append((cand, ent, pol, min_m))
        return out, why

    last: dict | None = None
    dark_why: dict[int, list[str]] = {}
    relaxed = {"chain": 0, "floor": 0}
    p = 0
    while True:
        d = cov.first_dark(p)
        if d is None:
            break
        tiers = ([(last, "biome")] if last is not None else []) + [
            (None, "biome"), (None, "gate")]
        got, why, used = [], [], None
        for used in tiers:
            got, why = window(d, *used)
            if got:
                break
        if not got:
            dark_why[d] = why
            last = None
            p = d + 1
            continue
        if last is not None and used[0] is None:
            relaxed["chain"] += 1
        if used[1] == "gate":
            relaxed["floor"] += 1
        best, ent, pol, min_m = max(
            got, key=lambda c: (cov.reach(c[1], d), round(c[0].score, 2),
                                round(c[0].fwd_m + c[0].back_m, 2), c[0].s))
        rec = lamp_unit(best.lamp_x, best.lamp_z, best.surface_y,
                        best.yaw_deg)
        rec.update({
            "s_m": round(best.s, 2),
            "gap_m": None if last is None else round(best.s - last["s"], 2),
            "side": "left" if best.side > 0 else "right",
            "biome": best.biome,
            # WHICH DARK STATION THIS LAMP WAS SITED TO LIGHT, and under which
            # spacing floor -- the biome's own, or the gate's where the
            # biome's would have left that station dark.
            "lights_dark_station_m": round(stations[d][0], 1),
            "min_spacing_m": min_m,
            "min_spacing_rule": ("BIOME_POLICY" if min_m == pol["min_m"]
                                 else "lighting.MIN_SPACING_M"),
            "sight_fwd_m": round(best.fwd_m, 1),
            "sight_back_m": round(best.back_m, 1),
            "sight_min_m": round(best.score, 1),
            # A DIRECTION THAT RAN OUT OF SEGMENT IS A LOWER BOUND, NOT A
            # MEASUREMENT.  MEASURED: the last lamp on T12 sits 2 m from the
            # segment's end, so its forward sightline is 1 m -- and the road
            # does not end there, it becomes T3.  The number is kept because
            # it is what was measured; the flag stops it being read as
            # terrain.
            "sight_min_is_lower_bound":
                best.fwd_why == "segment end" or best.back_why == "segment end",
            "sight_fwd_limit": best.fwd_why,
            "sight_back_limit": best.back_why,
            "window_size": len(got),
            "footprint_relief_m": round(best.relief_m, 4),
            "window_best_minus_median": round(
                best.score - _median([c[0].score for c in got]), 2),
        })
        plan.lamps.append(rec)
        # STAGE IT BEFORE THE NEXT ITERATION ASKS.  Without this the walk is
        # blind to its own last decision until the server is told, and one
        # run duplicates faster than it can be asked.
        gate.stage(best.lamp_x, best.surface_y + seat_offset_m(), best.lamp_z,
                   seg.seg_id)
        cov.add(ent)
        last = {"s": best.s}
        p = d
    if relaxed["chain"] or relaxed["floor"]:
        plan.notes.append(
            f"{relaxed['chain']} lamp(s) broke the chain's biome maximum gap "
            f"and {relaxed['floor']} stand on the gate's "
            f"{GATE.MIN_SPACING_M:.0f} m minimum instead of the biome's, each "
            f"because the stricter rule would have left a station dark")
    plan.unlit = unlit_stretches(stations, cov, dark_why)
    plan.coverage = cov.verify()
    return plan


def _owner(reason: str) -> str:
    """WHO a refusal names: the thing that would have to move for a lamp to
    stand there.  Every reason in this module is written cause-first, which
    is what makes this a lookup and not a guess."""
    r = reason
    if r.startswith("inside site pad footprint "):
        return "site pad " + r[len("inside site pad footprint "):].split()[0]
    if r.startswith("inside POI stand-off"):
        return "POI stand-off " + r.rsplit(" of ", 1)[-1]
    if r.startswith("inside a protected structure"):
        return "protected structure extent " + r.rsplit(" of ", 1)[-1]
    if r.startswith("within ") and "existing piece" in r:
        return "protected piece " + r.rsplit(" at ", 1)[-1]
    if "from the centreline of " in r and "INSIDE" in r:
        return ("carriageway of "
                + r.split("from the centreline of ", 1)[1].split(",")[0])
    if r.startswith("ground here is authored by "):
        return r[len("ground here is authored by "):].split(",")[0]
    if r.startswith("surface ") and "water plane" in r:
        return "water plane (no dry shoulder)"
    if r.startswith("ground rises and falls"):
        return (f"shoulder relief over the {FOOTPRINT_RELIEF_MAX_M} m "
                f"footprint limit")
    if " already stands " in r:
        zdo = r.split(" ZDO ", 1)[-1].split(",")[0]
        return (f"spacing: {r.split()[1]} {zdo} inside the "
                f"{GATE.MIN_SPACING_M:.0f} m network minimum")
    if r.startswith("biome "):
        return "BIOME_POLICY has no row for " + r.split()[1]
    if r.startswith("terrain:"):
        return "terrain: the applied surface hides every legal flame"
    if "generated lattice" in r or "no measured surface" in r:
        return "ground this pass has not measured"
    return r[:80]


def unlit_stretches(stations: list[tuple[float, float, float]],
                    cov: Coverage, dark_why: dict[int, list[str]]
                    ) -> list[dict]:
    """Every run of stations still dark after the walk, NUMBERED -- where it
    runs, how long, whether the gate refuses it -- with the owners of every
    spot that could have lit it, most frequent first."""
    out: list[dict] = []
    k, n = 0, len(cov.lit)
    while k < n:
        if cov.lit[k]:
            k += 1
            continue
        a = k
        while k < n and not cov.lit[k]:
            k += 1
        b = k - 1
        owners: dict[str, int] = {}
        for i in range(a, b + 1):
            for why in set(dark_why.get(i, ())):
                o = _owner(why)
                owners[o] = owners.get(o, 0) + 1
        length = (b - a + 1) * SIGHT_STEP_M
        out.append({
            "from_m": round(stations[a][0], 1), "to_m": round(stations[b][0], 1),
            "length_m": length,
            "from_xz": [round(stations[a][1], 1), round(stations[a][2], 1)],
            "to_xz": [round(stations[b][1], 1), round(stations[b][2], 1)],
            "refused_by_gate": length > GATE.MAX_UNLIT_RUN_M,
            "owners": dict(sorted(owners.items(), key=lambda kv: -kv[1])[:4])
                      or {"no lamp spot within the gate's sight distance": 1}})
    return out


def _site_by_spacing(seg: Segment, ground: Ground, keep: KeepOut,
                     standing: "Standing | None" = None,
                     eye: float = EYE_M) -> Plan:
    """THE SHIPPED WALK, kept only as `_COVERAGE_MODE = "spacing"`'s known-bad
    state: windows sized by `BIOME_POLICY` spacing, the best sightline in
    each, and no question ever asked about what the gate will see.  It is
    what left T7's last 115 m dark; see `LAMP_COVERAGE_DEPENDS_ON`.
    """
    plan = Plan(seg=seg.seg_id)
    stations, surf = _surface_profile(seg, ground, plan)

    def build(i: int, side: int) -> tuple[Candidate | None, str | None]:
        return _candidate(seg, stations, surf, ground, i, side, eye)

    last_s: float | None = None
    # WHERE the previous lamp actually stands, for the real-separation half
    # of the spacing minimum.  `last_s` alone is arclength and a bend makes
    # arclength longer than separation.
    last_xz: tuple[float, float] | None = None
    # The arclength the current window is measured FROM.  It is the previous
    # lamp when there is one and the window's own start when there is not --
    # which matters after an advance past a pad or a bridge deck, because an
    # absolute test would then measure the first lamp's window from the start
    # of the segment and refuse the whole remainder.
    i_from = 0
    guard = 0
    while True:
        guard += 1
        if guard > len(stations) + 8:
            plan.notes.append("siting walk hit its own iteration guard")
            break
        # The window: every station whose gap from the previous lamp is legal
        # for the biome AT THAT STATION.  The biome is read per candidate, so a
        # segment that crosses from Meadows into Black Forest tightens as it
        # goes rather than using the segment's opening biome throughout.
        window: list[Candidate] = []
        refused_here: list[dict] = []
        base_s = last_s if last_s is not None else stations[i_from][0]
        for i in range(i_from, len(stations)):
            s = stations[i][0]
            for side in (+1, -1):
                cand, why = build(i, side)
                if cand is None:
                    if why:
                        refused_here.append(
                            {"s_m": round(s, 1), "side": side, "reason": why})
                    continue
                pol = BIOME_POLICY.get(cand.biome, DEFAULT_POLICY)
                if pol is None:
                    refused_here.append(
                        {"s_m": round(s, 1), "x": round(cand.lamp_x, 2),
                         "z": round(cand.lamp_z, 2),
                         "reason": f"biome {cand.biome} has no published "
                                   f"spacing policy; refusing rather than "
                                   f"defaulting"})
                    continue
                if not _biome_allows(last_s, base_s, s, cand, pol,
                                     last_xz):
                    continue
                bad = keep.why_not(cand.lamp_x, cand.lamp_z)
                if bad:
                    refused_here.append(
                        {"s_m": round(s, 1), "x": round(cand.lamp_x, 2),
                         "z": round(cand.lamp_z, 2), "side": side,
                         "reason": bad})
                    continue
                # THE PROXIMITY GATE.  Asked of the WORLD and of this run's
                # own staged lamps, not of `last_s`, which is why it is
                # correct across segments, across runs and across a chain
                # break.  The y is the lamp's PIVOT -- surface plus the
                # measured seat offset -- so the cylinder is compared against
                # the same datum the census read off the standing lamps.
                if standing is not None:
                    lamp_y = cand.surface_y + seat_offset_m()
                    occupied = standing.why_not(cand.lamp_x, lamp_y,
                                                cand.lamp_z, pol["min_m"])
                    if occupied:
                        refused_here.append(
                            {"s_m": round(s, 1), "x": round(cand.lamp_x, 2),
                             "z": round(cand.lamp_z, 2), "side": side,
                             "gate": "proximity", "reason": occupied})
                        continue
                window.append(cand)
            # Stop widening once past the loosest max for any biome present.
            if s - base_s > 90.0:
                break
        if not window:
            # NOTHING LEGAL IN THE WINDOW.  Advance past it rather than
            # stopping the whole segment: a settlement pad or 120 m of ground
            # under the water plane must not cost the kilometre behind it.
            #
            # AND BREAK THE CHAIN, WHICH IS THE BUG THIS COMMENT EXISTS FOR.
            # The first version advanced `i_from` and left `last_s` pointing
            # at the lamp it had just failed to chain from, so the window's
            # 90 m ceiling stayed nailed to that lamp: once `i_from` passed
            # `last_s + 90` the scan range was EMPTY and every further pass
            # examined nothing while dutifully logging that it had advanced.
            # MEASURED on S8, whose last 212.8 m went unlit with ONE recorded
            # refusal to explain it -- a walk that reports progress it is not
            # making is worse than one that stops.
            #
            # A break is the honest model: the run of lamps ENDS here, and the
            # next lamp starts a fresh run whose first gap is not a spacing
            # decision and is recorded as None rather than as a 212 m gap.
            step_from = i_from
            i_from = _advance(stations, last_s, i_from)
            if i_from <= step_from or i_from >= len(stations):
                plan.refusals.extend(refused_here)
                break
            plan.refusals.extend(refused_here)
            plan.notes.append(
                f"no legal lamp spot in the window from "
                f"{'the start' if last_s is None else f'{last_s:.0f} m'}; "
                f"chain broken, window restarted at "
                f"{stations[i_from][0]:.0f} m")
            last_s = None
            last_xz = None
            continue
        plan.refusals.extend(refused_here)
        # MAXIMISE THE MINIMUM OF THE TWO DIRECTIONS.  Ties broken by the sum
        # (a genuinely better spot), then by the LATER station, which spends
        # fewer lamps for the same visibility.
        best = max(window, key=lambda c: (round(c.score, 2),
                                          round(c.fwd_m + c.back_m, 2), c.s))
        rec = lamp_unit(best.lamp_x, best.lamp_z, best.surface_y, best.yaw_deg)
        rec.update({
            "s_m": round(best.s, 2),
            "gap_m": None if last_s is None else round(best.s - last_s, 2),
            "side": "left" if best.side > 0 else "right",
            "biome": best.biome,
            "sight_fwd_m": round(best.fwd_m, 1),
            "sight_back_m": round(best.back_m, 1),
            "sight_min_m": round(best.score, 1),
            # A DIRECTION THAT RAN OUT OF SEGMENT IS A LOWER BOUND, NOT A
            # MEASUREMENT.  MEASURED: the last lamp on T12 sits 2 m from the
            # segment's end, so its forward sightline is 1 m -- and the road
            # does not end there, it becomes T3.  Scoring that as a 1 m
            # sightline is the instrument answering a question about the
            # segment's extent while appearing to answer one about the
            # terrain, which is this project's signature defect. The number is
            # kept because it is what was measured; the flag is what stops it
            # being read as terrain.
            "sight_min_is_lower_bound":
                best.fwd_why == "segment end" or best.back_why == "segment end",
            "sight_fwd_limit": best.fwd_why,
            "sight_back_limit": best.back_why,
            "window_size": len(window),
            "footprint_relief_m": round(best.relief_m, 4),
            "window_best_minus_median": round(
                best.score - _median([c.score for c in window]), 2),
        })
        plan.lamps.append(rec)
        if standing is not None:
            # STAGE IT BEFORE THE NEXT ITERATION ASKS.  Without this the walk
            # is blind to its own last decision until the server is told, and
            # one run duplicates faster than it can be asked.
            standing.stage(best.lamp_x, best.surface_y + seat_offset_m(),
                           best.lamp_z, seg.seg_id)
        last_s = best.s
        last_xz = (best.lamp_x, best.lamp_z)
        i_from = int(best.s / SIGHT_STEP_M) + 1
        if i_from >= len(stations):
            break
        # The tail: if what is left is shorter than the biome minimum, there is
        # no legal spot and the segment ends. Recorded so the report can say
        # how much road follows the last lamp.
        if seg.length_m - best.s < 30.0:
            break
    if plan.lamps:
        plan.notes.append(
            f"tail after the last lamp: "
            f"{seg.length_m - plan.lamps[-1]['s_m']:.1f} m")
    return plan


def _biome_allows(last_s: float | None, base_s: float, s: float,
                  cand: Candidate, pol: dict,
                  last_xz: tuple[float, float] | None = None) -> bool:
    """Is a lamp at `s` legal given the previous one and this biome's policy?

    THE STRETCH RULE IS THE HONEST HALF.  An open biome may exceed the
    operator's 60 m upper bound -- they asked for lamps to be used sparingly
    there -- but only where the lamp can still SEE back to its neighbour:
    `sight_back >= gap`.  Otherwise "sparingly" is a word for a dark road.  A
    gap at or under 60 m needs no such proof; that is inside the operator's own
    bound.

    THE MINIMUM IS ENFORCED ON THE SEPARATION THE WORLD HAS, NOT ON
    ARCLENGTH, and that is a fix and not a refinement.  `s - last_s` is
    distance ALONG THE CENTRELINE; two lamps 30 m apart along a bend are
    less than 30 m apart in the world, and they are on opposite shoulders,
    which shortens it again.  MEASURED on Ulfsland: 5 same-segment pairs
    survived the arclength rule and are refused by the gate's cylinder --
    N1 and N2 at 29.9995 m from an arclength of exactly 30.0, N4 at
    29.524 / 29.764, T4 at 27.259, T10 at 20.027.  The upper bounds stay on
    arclength because "a 60 m gap of road" is a length of road.
    """
    if last_s is None:
        # The first lamp: anywhere in the first `max_m`, and it still has to
        # earn its place on sightline like the rest.
        return s - base_s <= pol["max_m"]
    gap = s - last_s
    sep = (gap if last_xz is None
           else math.hypot(cand.lamp_x - last_xz[0],
                           cand.lamp_z - last_xz[1]))
    # THE MINIMUM IS A LIMIT THE GATE MEASURES: keep it by the readback
    # margin, on the arclength and on the separation alike.
    if min(gap, sep) < pol["min_m"] + readback_margin_m():
        return False
    if gap <= 60.0:
        return gap <= pol["max_m"]
    if gap > pol["max_m"]:
        return False
    return cand.back_m >= gap


def _advance(stations, last_s: float | None, i_from: int) -> int:
    """Skip the window that held nothing legal."""
    base = 0.0 if last_s is None else last_s
    target = base + 30.0
    nxt = max(i_from + 30, int(target / SIGHT_STEP_M) + 1)
    return min(nxt, len(stations))


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# --------------------------------------------------------------------------
# verification: what a player walking at night actually sees
# --------------------------------------------------------------------------

def _causes(refusals: list[dict]) -> dict[str, int]:
    """Refusals grouped by CAUSE rather than listed one by one.

    A refusal list with 687 rows is not readable, and "687 refused" without
    causes is not actionable.  The cause is the leading clause of the reason,
    which is why every reason in this module is written cause-first.
    """
    out: dict[str, int] = {}
    for r in refusals:
        reason = str(r.get("reason", ""))
        for marker in ("inside site pad footprint", "inside POI stand-off",
                       "inside a protected structure's own recorded extent",
                       "within", "ground here is authored by",
                       "ground rises and falls",
                       "surface", "footprint reaches ground",
                       "generated lattice is not the one",
                       "biome"):
            if reason.startswith(marker):
                out[marker] = out.get(marker, 0) + 1
                break
        else:
            out["other"] = out.get("other", 0) + 1
    return out


def night_walk(seg: Segment, plan: Plan, ground: Ground, keep: KeepOut,
               standing: "Standing | None" = None) -> dict:
    """The finished segment AS THE LIGHTING GATE WILL READ IT: its own
    `unlit_runs` at every sight distance it reports, over every corridor
    torch standing or staged plus this plan's -- and the stretches the walk
    refused, with their owners.

    This replaced `dark_stretches`, which counted THIS plan's lamps only, at
    this module's own 80 m cap, and on T7 said "803 m dark at 0 m" while the
    road's real dark stretch was the last 115 m and nobody could see it.  It
    also excused a dark run at a segment END as "the approach into a
    settlement, whose buildings carry their own light" -- the T7 tail is
    exactly that approach, and the gate does not excuse it.
    """
    stations, surf = _surface_profile(seg, ground, Plan(seg=seg.seg_id))
    flames = ((standing.live + standing.staged) if standing is not None
              else plan.lamps)
    got = Coverage(stations, surf, keep.carriageways, flames).verify()
    head = got[f"sight_{SIGHT_M:.0f}m"]
    return {"sight_m": SIGHT_M, "eye_m": EYE_M, "stations": len(stations),
            "worst_dark_run_m": head["worst_unlit_run_m"],
            "worst_dark_run_ends_at": head["ends_at"],
            "unlit_stations": head["unlit_stations"],
            "gate_limit_m": GATE.MAX_UNLIT_RUN_M,
            "by_sight": got, "refused_stretches": plan.unlit,
            "flames": ("every torch standing in the world or staged by this "
                       "run" if standing is not None else
                       "THIS PLAN'S LAMPS ONLY -- no census was taken")}


def footprint_half_extents(prefab: str = LAMP_PREFAB) -> tuple[float, float]:
    """The prefab's own XZ half-extents, from the audit's box builder."""
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(prefab, 0.0, 0.0, 0.0, 0.0)
    return max(abs(box[0]), abs(box[1])), max(abs(box[4]), abs(box[5]))


def seat_surface(ground: "Ground", x: float,
                 z: float) -> tuple[float | None, float]:
    """`(lowest applied surface under the footprint, its relief)`.

    Nine probes -- centre plus the eight points of the footprint rectangle --
    each at its own XZ, which is the rule that got 32 of 36 portal ends flush:
    probe where the object actually is, not where its station is.  `None` when
    any probe is uncovered, because a footprint partly off the measured ground
    is a seat nobody has measured, and the relief comes back with it because
    seating on the lowest point buys "never floats" at the price of exactly
    that much burial -- so the caller has to be able to see the price.
    """
    hx, hz = footprint_half_extents()
    lo: float | None = None
    hi: float | None = None
    for dx in (-hx, 0.0, hx):
        for dz in (-hz, 0.0, hz):
            sy = ground.surface(x + dx, z + dz)
            if sy is None:
                return None, 0.0
            lo = sy if lo is None else min(lo, sy)
            hi = sy if hi is None else max(hi, sy)
    return lo, (hi - lo if lo is not None and hi is not None else 0.0)


def seat_check(plan: Plan, ground: Ground) -> dict:
    """How far every lamp's FOOT is from the applied surface under its own
    footprint.

    Probed at the lamp's own XZ and at the four corners of its measured
    footprint, and the seat uses the DEEPEST of those: a lamp seated on the
    centre of a sloping sample shows a sliver of air on the downhill corner,
    and a sliver of air is the floating defect in miniature.  Reported as the
    worst gap either way so the number is a measurement and not a target.
    """
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(LAMP_PREFAB, 0.0, 0.0, 0.0, 0.0)
    hx, hz = footprint_half_extents()
    rows = []
    for lamp in plan.lamps:
        foot = lamp["y"] + box[2]
        gaps = []
        for dx in (-hx, 0.0, hx):
            for dz in (-hz, 0.0, hz):
                sy = ground.surface(lamp["x"] + dx, lamp["z"] + dz)
                if sy is not None:
                    gaps.append(foot - sy)
        rows.append({"s_m": lamp["s_m"], "x": lamp["x"], "z": lamp["z"],
                     "gap_at_centre_m": round(foot - lamp["surface_y"], 4),
                     "max_air_under_foot_m": round(max(gaps), 4) if gaps else None,
                     "max_burial_m": round(-min(gaps), 4) if gaps else None})
    airs = [r["max_air_under_foot_m"] for r in rows
            if r["max_air_under_foot_m"] is not None]
    buried = [r["max_burial_m"] for r in rows if r["max_burial_m"] is not None]
    return {"lamps": len(rows),
            "worst_air_under_foot_m": round(max(airs), 4) if airs else 0.0,
            "worst_burial_m": round(max(buried), 4) if buried else 0.0,
            "footprint_half_extents_m": [round(hx, 4), round(hz, 4)],
            "rows": rows}


# --------------------------------------------------------------------------
# the wire, and the live write
# --------------------------------------------------------------------------

def wire_lines(plan: Plan, payload: str) -> list[str]:
    """One `spawn_object` per lamp.

    `pos=` is z,x,y and `rot=` is euler y,x,z -- three component orders in one
    command, all MEASURED from IL, and the argument builders are
    `to_rcon_plan`'s rather than this module's f-strings.
    """
    import to_rcon_plan  # noqa: PLC0415
    out = []
    for lamp in plan.lamps:
        out.append(
            f"spawn_object {lamp['prefab']}"
            f" pos={to_rcon_plan.pos_arg(lamp['x'], lamp['y'], lamp['z'])}"
            f" rot={to_rcon_plan.rot_arg(0.0, lamp['yaw_deg'], 0.0)}"
            f" {to_rcon_plan.FROM_ORIGIN} data={payload}")
    return out


def expect_for(plan: Plan) -> dict:
    """One `objects_count` probe per lamp: exactly 1 of this prefab within
    1.5 m of where it was asked for.

    Per lamp and not one big cylinder: a segment is hundreds of metres long, a
    radius that covered it would also cover the neighbouring segment's lamps at
    every junction, and a count that can be satisfied by somebody else's object
    is not a postcondition.  1.5 m because the lamps are 30 m apart, so the
    discs cannot overlap and each probe answers about one lamp.
    """
    return {"prefab_count": [
        {"prefab": LAMP_PREFAB, "pos": [lamp["x"], lamp["z"]],
         "max": 1.5, "count": 1, "tolerance": 0}
        for lamp in plan.lamps]}


def selftest_segment_key(verbose: bool = True) -> dict:
    """BOTH DIRECTIONS OF `LAMP_SEGMENT_KEY_DEPENDS_ON`, over CONSTRUCTED
    records -- never this checkout's ledger, which stops describing anything
    the day it moves.
    """
    global _SEGKEY_MODE
    north = {"params": {"role": "road_segment",
                        "name": "road_S7_stathub_wtnorth"}}
    south = {"params": {"role": "road_segment",
                        "name": "road_S7_stathub_wtsouth"}}
    stamped = {"params": {"role": "road_segment",
                          "name": "road_S7_stathub_wtnorth"},
               "meta": {"segment": "S7-stathub-wtnorth"}}
    nameless = {"params": {"role": "road_segment"}, "meta": {}}

    real = {k: segment_id_of(r) for k, r in
            (("north", north), ("south", south), ("stamped", stamped),
             ("nameless", nameless))}
    # THE CALLER'S OWN VOCABULARY: what buildstage step 7 passes.
    assert real["north"] == "S7-stathub-wtnorth", real
    # THE STAMP WINS AND AGREES WITH THE RECONSTRUCTION.
    assert real["stamped"] == real["north"], real
    # TWO SEGMENTS SHARING A PREFIX ARE TWO SEGMENTS.
    assert real["south"] == "S7-stathub-wtsouth", real
    assert real["north"] != real["south"], real
    # UNREAD IS NOT CLEAR: a record naming nothing answers "", never a guess.
    assert real["nameless"] == "", real

    prior = _SEGKEY_MODE
    try:
        # NEUTER 1, THE SHIPPED KNOWN-BAD: the prefix. Both roads collide on
        # one key and neither can be named by its corpus id.
        _SEGKEY_MODE = "prefix"
        pref = {k: segment_id_of(r) for k, r in
                (("north", north), ("south", south))}
        assert pref["north"] == pref["south"] == "S7", pref
        # NEUTER 2, OFF: the key names nothing, so nothing is lightable and
        # nothing is reported as unlit either.
        _SEGKEY_MODE = "off"
        assert segment_id_of(stamped) == "", "off"
    finally:
        _SEGKEY_MODE = prior
    if verbose:
        print(f"  lamp segment key: real -> {real['north']!r} / "
              f"{real['south']!r} (2 segments); NEUTER prefix -> "
              f"{pref['north']!r} / {pref['south']!r} (1 key, one road lost); "
              f"NEUTER off -> '' (none)")
        print("lamps segment-key selftest OK")
    return {"real": real, "prefix": pref}


# --------------------------------------------------------------------------
# selftest, through `accept/registry.py`'s own runner
# --------------------------------------------------------------------------
_NEUTERS: dict[str, tuple[str, str, str, object]] = {
    # label: (dependency, module, global, value)
    # LAMP_SEAT_SURFACE_DEPENDS_ON, the SHIPPED reading: PatchScan's default
    # worldgen surface, the cache named for neither surface nor seed, and no
    # proof that a zone's lattice is the one its writes were computed against.
    "seat surface worldgen": ("ribbon.PATCH_SURFACE", __name__,
                              "_SEAT_SURFACE_MODE", "worldgen"),
    # LAMP_COVERAGE_DEPENDS_ON, the SHIPPED walk: windows sized by the biome
    # spacing constants, and nothing asked about what the gate will see.
    "coverage spacing": ("accept.sections.lighting (the lit criterion)",
                         __name__, "_COVERAGE_MODE", "spacing"),
    # LAMP_SPACING_DEPENDS_ON, the SHIPPED reading: every limit the gate
    # measures kept to the exact digit on the walk's own unrounded numbers,
    # which the write and the census then rounded across the line.
    "spacing bare": ("accept.sections.lighting.check_spacing / unlit_runs",
                     __name__, "_SPACING_MODE", "bare"),
}


def _neuter(label: str):
    """Break exactly ONE declared dependency, in exactly ONE direction."""
    _, modname, key, value = _NEUTERS[label]

    def apply():
        g = (globals() if modname == __name__
             else sys.modules[modname].__dict__)
        prior = g[key]
        g[key] = value

        def undo() -> None:
            g[key] = prior
        return undo
    return apply


# THE DRIVE-3 T6 SHAPE, CONSTRUCTED AT ITS OWN COORDINATES -- never borrowed:
# a case that reads this checkout's ledger or save stops describing anything
# the day that artefact moves.  Zone (-1,11); ONE road write, seq 1951's
# measured delta at (-72, 701) laid over the whole zone and recorded against
# the HEIGHTMAP lattice; and the two generated surfaces, which disagree there
# by the measured 2.604 m.
_SEAT_ZONE = (-1, 11)
_SEAT_XZ = (-72.3314, 700.75)
_SEAT_HEIGHTMAP_M = 44.1612
_SEAT_WORLDGEN_M = 41.5573
_SEAT_DELTA_M = -2.4636
# The torch as drive 3 placed it, spawn_plan seq 2716.
_SEAT_PLACED_Y = 39.707


def _seat_lattice(height: float):
    import heights as patchheights  # noqa: PLC0415
    zx, zz = _SEAT_ZONE
    cx, cz = tcdata.zone_centre(zx, zz)
    n = tcdata.PITCH
    return patchheights.Patch(f"z_{zx}_{zz}", cx, cz, 32.5, 1.0, n,
                              [height] * (n * n), [1] * (n * n))


class _SeatLedger:
    """The one record the drive-3 ground under the torch is."""

    def __init__(self):
        import hashlib  # noqa: PLC0415
        comp = tcdata.Compiler(*_SEAT_ZONE)
        for gy in range(tcdata.PITCH):
            for gx in range(tcdata.PITCH):
                comp.set_height(gx, gy, _SEAT_DELTA_M)
        self._blob = comp.blob()
        self._recs = [{"op": "terrain_write", "seq": 1951, "params": {
            "name": "road_T6_stathub_northcape", "role": "road_segment",
            "entries": [{
                "zone": list(_SEAT_ZONE),
                "blob_sha256": hashlib.sha256(self._blob).hexdigest(),
                "generated_heights_sha256": ribbon.generated_digest(
                    _seat_lattice(_SEAT_HEIGHTMAP_M))}]}}]

    def records(self) -> list[dict]:
        return self._recs

    def read_blob(self, _sha: str) -> bytes:
        return self._blob


def _seat_judged(y: float, live: appliedmod.Applied, R) -> "object":
    """The gate's question, asked of the ground the ENGINE draws: the
    heightmap lattice plus the write's delta, probed over the lamp's own
    footprint.  Refuses a lamp whose own top is under the lowest of it, or
    whose foot is off it by more than the gate's burial tolerance."""
    import fixtures  # noqa: PLC0415
    from accept.sections.structures import BURIED_TOL_M  # noqa: PLC0415
    box = fixtures.fixture_box(LAMP_PREFAB, 0.0, 0.0, 0.0, 0.0)
    hx, hz = footprint_half_extents()
    drawn = _seat_lattice(_SEAT_HEIGHTMAP_M)
    x, z = _SEAT_XZ
    lo = min(live.surface_at({drawn.id: drawn}, x + dx, z + dz)[0]
             for dx in (-hx, 0.0, hx) for dz in (-hz, 0.0, hz))
    foot, top = y + box[2], y + box[3]
    nums = {"pivot_y": round(y, 4), "foot_y": round(foot, 4),
            "top_y": round(top, 4), "lowest_drawn_ground_y": round(lo, 4)}
    if top < lo - BURIED_TOL_M:
        return R.Finding(
            check="seat", ok=False, where=(x, round(y, 3), z),
            measured=round(lo - top, 3), limit=BURIED_TOL_M, numbers=nums,
            message=(f"lamp COMPLETELY under the ground the engine draws: "
                     f"its OWN TOP is {lo - top:.3f} m below the lowest "
                     f"ground under it"))
    if abs(foot - lo) > BURIED_TOL_M:
        return R.Finding(
            check="seat", ok=False, where=(x, round(y, 3), z),
            measured=round(foot - lo, 3), limit=BURIED_TOL_M, numbers=nums,
            message=(f"lamp foot {foot - lo:+.3f} m off the lowest ground "
                     f"the engine draws under it"))
    return R.Finding(check="seat", ok=True, numbers=nums,
                     message=f"foot {foot - lo:+.3f} m off the drawn ground")


def _seat_case(stale: bool, R) -> "object":
    """Seat the torch through `Ground` -> `seat_surface` -> `lamp_unit`, the
    production path, with PatchScan replaced by a scanner that returns the
    lattice for the surface it is ASKED for -- or, `stale`, the worldgen one
    whatever it is asked, which is a cache from another question."""
    import tempfile  # noqa: PLC0415
    live = appliedmod.Applied(world="lamps-selftest", led=_SeatLedger())
    lattices = {"heightmap": _seat_lattice(_SEAT_HEIGHTMAP_M),
                "worldgen": _seat_lattice(_SEAT_WORLDGEN_M)}

    def scan(_zones, _out, surface):
        p = lattices["worldgen" if stale else surface]
        return {p.id: p}
    x, z = _SEAT_XZ
    with tempfile.TemporaryDirectory() as td:
        ground = Ground([_SEAT_ZONE], Path(td) / "T6-stathub-northcape.bin",
                        live, scan=scan)
    sy, _relief = seat_surface(ground, x, z)
    if sy is None:
        why = ground.why_unmeasured(x, z, 0.2)
        assert why and "wrote its deltas against" in why, why
        return R.Finding(check="seat", ok=True, numbers={"seated": 0},
                         message=f"refused, not seated: {why}")
    return _seat_judged(lamp_unit(x, z, sy, 0.0)["y"], live, R)


# THE DRIVE-4 T7 TAIL, CONSTRUCTED AT ITS OWN COORDINATES -- never borrowed.
# road_T7_stathub_stenvik from s = 1643.5 m to its end at Stenvik, its
# centreline's own corners; the ground flat at the 36.68 m the save's
# Heightmap reads on average there; and EVERY ground torch the frozen save
# (af96bfb62427) has within 60 m of it -- eleven Stenvik house torches 42-53 m
# off the road and two S4 road lamps 30.3 and 30.8 m off it, none of them
# close enough to light it.  The gate read all of it dark: 115 m, its worst.
_T7_TAIL_NODES = ((432.66, -262.8), (424.66, -262.8), (423.03, -263.67),
                  (396.16, -290.54), (394.26, -290.8), (376.26, -290.8),
                  (374.74, -289.64), (360.6, -275.5), (358.89, -274.8),
                  (335.9, -274.8))
_T7_TAIL_Y = 36.68
_T7_TAIL_TORCHES = (
    ("piece_groundtorch_wood", 342.934, 36.945, -327.24),
    ("piece_groundtorch_wood", 339.052, 36.945, -328.682),
    ("piece_groundtorch_wood", 341.139, 37.242, -319.417),
    ("piece_groundtorch_wood", 342.675, 37.242, -324.558),
    ("piece_groundtorch_wood", 336.667, 37.242, -326.41),
    ("piece_groundtorch_wood", 359.628, 39.193, -329.623),
    ("piece_groundtorch_wood", 357.813, 39.193, -333.517),
    ("piece_groundtorch_wood", 374.462, 35.172, -227.404),
    ("piece_groundtorch_wood", 376.681, 35.172, -225.618),
    ("piece_groundtorch_wood", 382.051, 35.172, -236.608),
    ("piece_groundtorch_wood", 383.643, 35.172, -235.426),
    ("piece_groundtorch", 424.401, 39.134, -305.905),
    ("piece_groundtorch", 461.384, 35.088, -264.923))


class _TailGround:
    """The tail's applied surface: flat, measured, Meadows."""
    surface_name = "fixture"

    def surface(self, _x: float, _z: float) -> float:
        return _T7_TAIL_Y

    def biome(self, _x: float, _z: float) -> str:
        return "Meadows"

    def why_unmeasured(self, _x: float, _z: float, _reach: float = 0.0):
        return None


class _TailKeep:
    """No keep-out but the carriageway -- or, `pad`, a site pad over all of it."""

    def __init__(self, seg: Segment, pad: str | None = None):
        self.carriageways = [(seg.name, seg.nodes, seg.half_width_m)]
        self.pad = pad

    def why_not(self, x: float, z: float) -> str | None:
        if self.pad:
            return f"inside site pad footprint {self.pad}"
        return KeepOut.carriageway_why_not(self, x, z)


def _t7_tail(pad: str | None = None):
    """Site the tail through the production `site`; return what the GATE
    reads of it -- its own `check_unlit`, `check_spacing` and `check_lateral`
    over every torch in the fixture plus the plan's -- and the plan."""
    nodes = [tuple(n) for n in _T7_TAIL_NODES]
    cum = [0.0]
    for k in range(1, len(nodes)):
        cum.append(cum[-1] + math.dist(nodes[k - 1], nodes[k]))
    seg = Segment(name="road_T7_stathub_stenvik", seg_id="T7-stathub-stenvik",
                  file_line=0, seq=0, half_width_m=3.0, nodes=nodes, cum=cum,
                  length_m=cum[-1])
    ground = _TailGround()
    standing = Standing.from_records(
        [{"prefab": p, "x": x, "y": y, "z": z, "id": f"d4/{k}"}
         for k, (p, x, y, z) in enumerate(_T7_TAIL_TORCHES)])
    plan = site(seg, ground, _TailKeep(seg, pad), standing=standing)
    stations = resample(seg)
    segs = {seg.name: {"nodes": seg.nodes, "half_width_m": seg.half_width_m}}
    corridor = GATE.classify_lamps(
        [(r["prefab"], r["x"], r["y"], r["z"])
         for r in standing.live + standing.staged], segs)
    fs, stats = GATE.check_unlit(
        {seg.name: stations},
        {seg.name: [ground.surface(x, z) for _s, x, z in stations]}, corridor)
    return fs + GATE.check_spacing(corridor)[0] + GATE.check_lateral(
        corridor), stats, plan


def _t7_tail_lit(R) -> list:
    """MUST ACCEPT: the gate finds no unlit run over its limit, no pair under
    its spacing and no lamp in the carriageway."""
    return _t7_tail()[0]


def _t7_tail_behind_a_pad(R) -> list:
    """MUST REFUSE, BY NUMBER AND OWNER: the same tail under a site pad that
    refuses every spot.  The walk cannot light it, and must SAY so -- the
    stretch, its length, and the pad as its owner.  A refusal that names the
    wrong owner is not emitted, so the case then fails to refuse."""
    _fs, _st, plan = _t7_tail(pad="stenvik")
    return [R.Finding(check="unlit_refused", ok=False,
                      measured=st["length_m"], limit=GATE.MAX_UNLIT_RUN_M,
                      message=(f"s={st['from_m']:.0f}..{st['to_m']:.0f} m "
                               f"({st['length_m']:.0f} m) dark, owners "
                               f"{st['owners']}"), numbers=st)
            for st in plan.unlit
            if "site pad stenvik" in st["owners"]]


# THE W7 PAIR, CONSTRUCTED AT ITS OWN COORDINATES -- never borrowed.  Gate
# lighting/lamp_spacing on the regenerated Ulfsland (seed Pirate68), two lamps
# on road_W7_wwest_wnorthwest: planned at 4 dp as `_LIMIT_PLAN`, printed back
# by the census as `_LIMIT_CENSUS`, and 29.999712 m apart there.
_LIMIT_PLAN = ((-1420.8311, 2155.5626), (-1399.6179, 2176.7758))
_LIMIT_CENSUS = ((-1420.831, 2155.563), (-1399.618, 2176.776))
_LIMIT_GATE_M = 29.999712
# A straight 45 degree road in the same quadrant, half width 2.0 as W7's, from
# a start chosen so that the walk's left-hand spot beside station 29 stands
# 30.00007 m from the standing lamp as the walk holds the two -- on the
# unrounded side of the 30 m limit -- and 29.999712298620256 m from it as the
# gate reads them back: the W7 figure to its last digit.  The standing lamp is
# at 3 dp, the way the census holds a lamp in this quadrant.
_LIMIT_START = (-1439.9992, 2135.0055)
_LIMIT_STANDING = (-1400.083, 2178.528)
_LIMIT_LENGTH_M = 240.0
_ROOT_HALF = math.sqrt(0.5)


def _straight_segment(start: tuple[float, float], dx: float, dz: float,
                      length_m: float, half_width_m: float,
                      name: str = "road_W7_wwest_wnorthwest") -> Segment:
    nodes = [start, (start[0] + length_m * dx, start[1] + length_m * dz)]
    cum = [0.0, math.dist(*nodes)]
    return Segment(name=name, seg_id=name[len("road_"):].replace("_", "-"),
                   file_line=0, seq=0, half_width_m=half_width_m,
                   nodes=nodes, cum=cum, length_m=cum[-1])


class _ForestGround(_TailGround):
    """Flat BlackForest, whose published minimum spacing IS the gate's 30 m."""

    def biome(self, _x: float, _z: float) -> str:
        return "BlackForest"


def _gate_reads(seg: Segment, rows: list[tuple]) -> list:
    """The lighting gate's own `check_spacing` over `(prefab, x, y, z)` rows,
    every coordinate AS THE CENSUS PRINTS IT BACK: the number the gate gets."""
    segs = {seg.name: {"nodes": seg.nodes, "half_width_m": seg.half_width_m}}
    back = []
    for prefab, x, y, z in rows:
        rx, rz = readback_xz(x, z)
        back.append((prefab, rx, readback_coord(y), rz))
    return GATE.check_spacing(GATE.classify_lamps(back, segs))[0]


def _limit_pair() -> list:
    """Site the W7-shaped road beside the standing lamp through the
    production `site`, and return what the gate reads of everything standing
    after it."""
    seg = _straight_segment(_LIMIT_START, _ROOT_HALF, _ROOT_HALF,
                            _LIMIT_LENGTH_M, 2.0)
    standing = Standing.from_records([{
        "prefab": LAMP_PREFAB, "x": _LIMIT_STANDING[0],
        "y": _T7_TAIL_Y + seat_offset_m(), "z": _LIMIT_STANDING[1],
        "id": "fx/standing"}])
    plan = site(seg, _ForestGround(), _TailKeep(seg), standing=standing)
    assert plan.lamps and not plan.unlit, (
        "the fixture road must be lit end to end, or it is not a siting of "
        f"anything: {len(plan.lamps)} lamp(s), unlit {plan.unlit}")
    return _gate_reads(seg, [(r["prefab"], r["x"], r["y"], r["z"])
                             for r in standing.live + plan.lamps])


def _limit_ceiling() -> list:
    """THE SAME CLIFF EDGE IN Y.  A lamp 10.00003 m above its neighbour is
    outside the gate's +/-10 m cylinder as the walk holds it, and writes as
    exactly 10.0000 m, which is inside.  The walk asks the production
    `Standing.why_not`, writes through `lamp_unit` if it is told yes, and the
    gate reads back whatever stands."""
    seg = _straight_segment((0.0, 0.0), 1.0, 0.0, 200.0, 3.0, "road_X_a_b")
    off = seg.half_width_m + SHOULDER_STANDOFF_M
    surface_y = 60.00003 - seat_offset_m()
    standing = Standing.from_records([{
        "prefab": LAMP_PREFAB, "x": 100.0, "y": 50.0, "z": off,
        "id": "fx/below"}])
    rows = [(LAMP_PREFAB, 100.0, 50.0, off)]
    if standing.why_not(105.0, surface_y + seat_offset_m(), off,
                        GATE.MIN_SPACING_M) is None:
        unit = lamp_unit(105.0, off, surface_y, 0.0)
        rows.append((unit["prefab"], unit["x"], unit["y"], unit["z"]))
    return _gate_reads(seg, rows)


def _limit_sight(R) -> list:
    """THE SIGHT DISTANCE IS A LIMIT TOO.  A flame 30 m less 0.02 mm from a
    station is lit to the walk; the census prints the flame's x 0.4 mm
    further (-1420.2104 -> -1420.21), which is 30.0004 m, which the gate calls
    dark.  Refuse if the walk calls lit what the gate reads back as dark."""
    hw = 3.0
    off = hw + SHOULDER_STANDOFF_M
    along = math.sqrt(SIGHT_M ** 2 - off ** 2)
    lamp_x, lamp_z = -1420.2104, 2150.0 + off
    station_x = lamp_x - (along - 0.00002)
    seg = _straight_segment((station_x - 40.0, 2150.0), 1.0, 0.0, 120.0, hw,
                            "road_X_a_b")
    stations = resample(seg)
    surf = [_T7_TAIL_Y] * len(stations)
    y = _T7_TAIL_Y + seat_offset_m()
    rx, rz = readback_xz(lamp_x, lamp_z)
    near, far = (math.hypot(lamp_x - station_x, off),
                 math.hypot(rx - station_x, off))
    assert near <= SIGHT_M < far, (
        f"the fixture must put station 40 inside the sight limit as the walk "
        f"holds the flame ({near:.6f}) and outside it as the census reads it "
        f"({far:.6f}); it does not")
    cov = Coverage(stations, surf, [(seg.name, seg.nodes, seg.half_width_m)],
                   [{"prefab": LAMP_PREFAB, "x": lamp_x, "y": y,
                     "z": lamp_z}])
    mine = sum(1 for v in cov.lit if not v)
    segs = {seg.name: {"nodes": seg.nodes, "half_width_m": seg.half_width_m}}
    idx = GATE.lamp_index(
        stations, GATE.classify_lamps(
            [(LAMP_PREFAB, rx, readback_coord(y), rz)], segs),
        max(GATE.SIGHT_DISTANCES_M))
    theirs = GATE.unlit_runs(stations, surf, idx, SIGHT_M)[2]
    if mine >= theirs:
        return []
    return [R.Finding(
        check="lit_station", ok=False, measured=mine, limit=theirs,
        numbers={"walk_dark": mine, "gate_dark": theirs,
                 "walk_xz_m": round(near, 6), "gate_xz_m": round(far, 6)},
        message=(f"the walk counts {mine} dark station(s) where the gate, "
                 f"reading the flame back from the census, counts {theirs}: "
                 f"a station {near:.6f} m from the flame as the walk holds it "
                 f"and {far:.6f} m as the gate reads it"))]


def _margin_outruns_the_loss(R) -> list:
    """The margin must exceed what the write and the census can take away.
    First the model of the census is held to the one measurement that
    matters: it must reproduce the W7 pair's read-back to the last digit."""
    got = tuple(readback_xz(x, z) for x, z in _LIMIT_PLAN)
    assert got == _LIMIT_CENSUS, (
        f"the census model prints {got}, the saved census holds "
        f"{_LIMIT_CENSUS}")
    gap = math.hypot(got[0][0] - got[1][0], got[0][1] - got[1][1])
    assert abs(gap - _LIMIT_GATE_M) < 5e-7, (
        f"the read-back pair is {gap:.6f} m apart, the gate measured "
        f"{_LIMIT_GATE_M}")
    bound, margin = readback_shrink_bound_m(), readback_margin_m()
    if margin > bound:
        return []
    return [R.Finding(
        check="margin", ok=False, measured=margin, limit=bound,
        numbers={"margin_m": margin, "worst_loss_m": round(bound, 5),
                 "extent_m": WORLD_EXTENT_M},
        message=(f"a {margin:g} m margin does not cover the {bound:.5f} m "
                 f"the write and the census can take off a pair's "
                 f"separation at |coordinate| {WORLD_EXTENT_M:g} m"))]


def _limit_pair_as_the_census_holds_it() -> list:
    """MUST REFUSE: the two W7 lamps as the census holds them, through the
    gate's own spacing check -- the instrument the other cases lean on, shown
    to refuse the state they are about.  The road is the straight 45 degree
    one both lamps stand 2.55 m left of."""
    (ax, az), (bx, bz) = _LIMIT_PLAN
    c = _ROOT_HALF
    start = (ax + (SHOULDER_STANDOFF_M + 2.0) * c - 20.0 * c,
             az - (SHOULDER_STANDOFF_M + 2.0) * c - 20.0 * c)
    seg = _straight_segment(start, c, c, 70.0, 2.0)
    return _gate_reads(seg, [(LAMP_PREFAB, ax, 44.0941, az),
                             (LAMP_PREFAB, bx, 40.4668, bz)])


def selftest() -> int:
    """Every case that needs no world, no seed and no server."""
    from accept import registry as R  # noqa: PLC0415
    selftest_segment_key()

    def placed():
        live = appliedmod.Applied(world="lamps-selftest", led=_SeatLedger())
        return _seat_judged(_SEAT_PLACED_Y, live, R)

    cases = [
        R.Case("a_lamp_across_a_biome_boundary_stands_on_the_drawn_ground",
               False, lambda: _seat_case(False, R),
               prior_state="MEASURED Fresh42 drive 3: T6's torch seated on "
                           "worldgen 41.5573 - 2.4636 = 39.0937 where the "
                           "engine draws heightmap 44.1612 - 2.4636 = "
                           "41.6976; own top 1.168 m under; 30 of 115 lamps "
                           "> 5 cm off their ground",
               neuter=_neuter("seat surface worldgen"),
               neuters="seat surface worldgen"),
        R.Case("a_lattice_the_ground_was_not_written_against_is_refused",
               False, lambda: _seat_case(True, R),
               prior_state="MEASURED: drive 3 step 7 read drive 2's "
                           "/tmp/roads/lamps/T6-stathub-northcape.bin, whose "
                           "zone (-1,11) digests bbf6b72b7883 where seq 1951 "
                           "recorded 08ced50835b7",
               neuter=_neuter("seat surface worldgen"),
               neuters="seat surface worldgen"),
        R.Case("the_drive3_t6_torch_as_placed_is_refused", True, placed,
               prior_state="gate structures/buried 1B at (-72.3, 39.707, "
                           "700.8): own top 1.168 m below the lowest ground"),
        R.Case("the_drive4_t7_tail_is_lit_as_the_lighting_gate_reads_it",
               False, lambda: _t7_tail_lit(R),
               prior_state="MEASURED Fresh42 drive 4, gate lighting/unlit_run "
                           "ACTIONABLE: 115 m unlit at 30 m sight on "
                           "road_T7_stathub_stenvik ending (335.9, -274.8); "
                           "the shipped walk placed 1 lamp in 1,758 m and "
                           "walked past this tail",
               neuter=_neuter("coverage spacing"),
               neuters="coverage spacing"),
        R.Case("a_dark_stretch_no_lamp_can_stand_in_is_refused_with_its_owner",
               True, lambda: _t7_tail_behind_a_pad(R),
               prior_state="the shipped walk left T7's tail dark and said "
                           "'803 m dark at 0 m' about a different stretch",
               neuter=_neuter("coverage spacing"),
               neuters="coverage spacing"),
        R.Case("a_lamp_on_the_spacing_limit_survives_the_census_readback",
               False, _limit_pair,
               prior_state="MEASURED regenerated Ulfsland, gate lighting/"
                           "lamp_spacing on road_W7_wwest_wnorthwest: lamps "
                           "at (-1420.8, 2155.6) and (-1399.6, 2176.8) "
                           "29.999712 m apart, 3.627 m in y, against the "
                           "30 m minimum; the ledger plan holds them "
                           "29.999995 m apart and the walk, which refuses "
                           "d <= 30, had accepted them [INFERENCE: on "
                           "unrounded numbers over 30]",
               neuter=_neuter("spacing bare"),
               neuters="spacing bare"),
        R.Case("a_lamp_a_hair_above_the_cylinder_ceiling_is_still_a_neighbour",
               False, _limit_ceiling,
               prior_state="the same cliff edge in y: the gate's cylinder is "
                           "+/-10 m and a lamp 10.00003 m above its "
                           "neighbour writes as 10.0000 m",
               neuter=_neuter("spacing bare"),
               neuters="spacing bare"),
        R.Case("a_station_on_the_sight_limit_is_not_called_lit_by_the_walk",
               False, lambda: _limit_sight(R),
               prior_state="the sight distance is a limit the gate measures "
                           "on the flame's read-back coordinates: 30 m less "
                           "0.02 mm to the walk is 30.0004 m to the gate",
               neuter=_neuter("spacing bare"),
               neuters="spacing bare"),
        R.Case("the_margin_outruns_what_the_write_and_the_census_take_off",
               False, lambda: _margin_outruns_the_loss(R),
               prior_state="MEASURED 343 planned lamps against the saved "
                           "census: the census model reproduces 323 exactly "
                           "and 20 to one digit; the live findObjects census "
                           "prints 2 decimals",
               neuter=_neuter("spacing bare"),
               neuters="spacing bare"),
        R.Case("the_w7_pair_as_the_census_holds_it_is_refused",
               True, _limit_pair_as_the_census_holds_it,
               prior_state="gate lighting/lamp_spacing 29.999712 m against "
                           "30.0; the instrument the cases above lean on, "
                           "shown to refuse the state they are about"),
    ]
    section = R.Section(
        name="lamps",
        measures="whether a road lamp's foot is seated on the ground the "
                 "engine draws under its own footprint, and whether the "
                 "lamps sited leave any station dark as the lighting gate "
                 "reads it -- or name the owner of the stretch they cannot",
        instrument="lamps.Ground + seat_surface + lamp_unit over "
                   "applied.Applied.surface_at; lamps.site against "
                   "accept/sections/lighting.py check_unlit / check_spacing "
                   "/ check_lateral",
        population="one constructed piece_groundtorch in the drive-3 T6 "
                   "zone (-1,11) shape; the drive-4 T7 tail with the "
                   "thirteen torches the frozen save has beside it; the W7 "
                   "wwest-wnorthwest lamp pair at its own coordinates and "
                   "the straight 45 degree road that reproduces it",
        run=lambda ctx: [], cases=cases)
    results = R.run_cases(section)
    bad = 0
    for c, ok, why in results:
        if not ok:
            bad += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {c.name}  "
              f"proof={c.proof_mode or 'unproven'}  "
              f"neuter={c.neuters or '-'}" + (f"  {why}" if why else ""))
    shown = []
    for label in ("real", "seat surface worldgen"):
        undo = _neuter(label)() if label != "real" else (lambda: None)
        try:
            f = _seat_case(False, R)
        finally:
            undo()
        shown.append(f"{label}: {f.message}")
    print("  seat of the T6 torch -- " + "; ".join(shown))
    shown = []
    for label in ("real", "coverage spacing"):
        undo = _neuter(label)() if label != "real" else (lambda: None)
        try:
            _fs, stats, plan = _t7_tail()
        finally:
            undo()
        s30 = stats[f"sight_{SIGHT_M:.0f}m"]
        shown.append(f"{label}: {len(plan.lamps)} lamp(s), worst unlit run "
                     f"{s30['worst_unlit_run_m']:.0f} m, {s30['unlit_stations']}"
                     f" of {s30['stations']} stations dark")
    print(f"  drive-4 T7 tail at {SIGHT_M:.0f} m sight -- " + "; ".join(shown))
    shown = []
    for label in ("real", "spacing bare"):
        undo = _neuter(label)() if label != "real" else (lambda: None)
        try:
            fs = _limit_pair()
            ceil = _limit_ceiling()
        finally:
            undo()
        shown.append(f"{label}: gate reads {len(fs)} pair(s) under "
                     f"{GATE.MIN_SPACING_M:.0f} m after the walk"
                     + (f" ({fs[0].measured:.6f} m)" if fs else "")
                     + f"; ceiling pair {len(ceil)}")
    print("  W7-shaped road beside a standing lamp -- " + "; ".join(shown))
    print(f"  readback margin {READBACK_MARGIN_M:g} m against a worst loss of "
          f"{readback_shrink_bound_m():.5f} m at |coordinate| "
          f"{WORLD_EXTENT_M:g} m, {readback_shrink_bound_m(2200.0):.5f} m at "
          f"2,200 m")
    print(f"\nlamps selftest: {len(results) - bad}/{len(results)} "
          f"({'PASS' if not bad else 'FAIL'}), declared dependencies "
          f"{LAMP_SEGMENT_KEY_DEPENDS_ON + LAMP_SEAT_SURFACE_DEPENDS_ON}"
          f" + {LAMP_COVERAGE_DEPENDS_ON} + {LAMP_SPACING_DEPENDS_ON}")
    return 0 if not bad else 1


def main() -> int:
    # THE CASE RUNS WITHOUT A WORLD, A SEED OR A SERVER.
    if "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true",
                    help="run the cases that need no world and exit")
    ap.add_argument("--segments", default="",
                    help="comma-separated CORPUS segment ids, e.g. "
                         "T1-portalhub-temple; default all built")
    ap.add_argument("--plan", action="store_true", help="site and report only")
    ap.add_argument("--place", action="store_true",
                    help="site and WRITE to the live world, one record per "
                         "segment plus a save with a verified postcondition")
    ap.add_argument("--dry", action="store_true",
                    help="build and validate the record without appending")
    ap.add_argument("--eye-sweep", action="store_true",
                    help="re-site at eye 1.5/1.7/1.9 m and print the spread")
    ap.add_argument("--json", default="", help="write the report here")
    ap.add_argument(
        "--no-gate", action="store_true",
        help="skip the live standing-lamp census. FOR OFFLINE REPORTING "
             "ONLY: without it the pass is blind to every lamp already in "
             "the world, which is the defect the gate exists to fix. "
             "`--place` refuses to run with it.")
    ap.add_argument(
        "--refuse-file-line", action="append", default=[], metavar="SEG=LINE",
        help="REFUSE if a segment's latest centreline is still this ledger "
             "file line. Use it to make a peer's 'that record is dead' "
             "mechanical instead of remembered: `--refuse-file-line T4=1783` "
             "aborts rather than siting T4 against the pre-reroute geometry.")
    ap.add_argument(
        "--actor", default="",
        help="the agent id ACTUALLY running this pass. REQUIRED for --place "
             "and --dry: every record this run appends is attributed to it, "
             "and the module constant is a default in disguise -- it names "
             "the agent that wrote this file, not the one running it.")
    worldseed.add_arguments(ap)
    args = ap.parse_args()

    # REBIND THE GLOBALS, so the patchscan sandbox path, the Applied reader
    # and the LiveBuilder cannot disagree about who is running.  Three
    # callsites read `ACTOR` at call time and two take it as a default bound
    # at import; the defaulted two are passed explicitly below.
    global ACTOR, SEED, WORLD  # noqa: PLW0603
    SEED, WORLD = worldseed.apply(args)
    if args.actor:
        ACTOR = args.actor
    elif args.place or args.dry:
        raise SystemExit(
            "REFUSING: --place/--dry without --actor. Every record would be "
            f"appended under {ACTOR!r}, which is this module's author and not "
            "whoever is running it. Provenance that is wrong reads as "
            "evidence; this project has paid for that twice.")

    # A WRITE MAY NOT RUN UNGATED.  `--no-gate` exists so the report can be
    # produced with no server, and that is the only thing it is for: a
    # placement pass without the census is the pass that produced 34 pairs
    # closer than the minimum, 11 of them because it could not see its own
    # previous run's output.
    if args.no_gate and (args.place or args.dry):
        raise SystemExit(
            "REFUSING: --no-gate with --place/--dry. The standing-lamp "
            "census is what makes this pass idempotent and what keeps it from "
            "re-lighting ground it has already lit. Offline reporting only.")

    # THE RESOLVED WORLD, PASSED: the default argument was bound at import,
    # before `worldseed.apply` above rebinds WORLD, so `--world Fresh42`
    # without VH_WORLD in the environment read ULFSLAND's segments.
    segs = segments_from_ledger(WORLD, actor=ACTOR)
    want = ([s.strip() for s in args.segments.split(",") if s.strip()]
            or sorted(segs))
    missing = [w for w in want if w not in segs]
    if missing:
        raise SystemExit(f"no such built segment(s): {missing}; "
                         f"have {sorted(segs)}")

    # THE DEAD-RECORD GUARD.  `HutsInRoad` re-routed T4 and told me file line
    # 1783 was dead; "I will remember not to use it" is the class of promise
    # that fails, so the promise is an assertion instead.  It reads the same
    # LATEST-IN-FILE-ORDER record the siting will use, so it cannot disagree
    # with the thing it is guarding.
    for spec in args.refuse_file_line:
        if "=" not in spec:
            raise SystemExit(f"--refuse-file-line wants SEG=LINE, got {spec!r}")
        short, line = spec.split("=", 1)
        seg = segs.get(short.strip())
        if seg is None:
            raise SystemExit(f"--refuse-file-line names {short!r}, which is "
                             f"not a built segment; have {sorted(segs)}")
        if seg.file_line == int(line):
            raise SystemExit(
                f"REFUSING: {short}'s latest centreline is still ledger file "
                f"line {seg.file_line} (seq {seg.seq}), which was declared "
                f"superseded. Siting against a re-routed segment's OLD "
                f"geometry would put lamps where the road no longer is -- and "
                f"on T4 specifically, inside buildings the re-route exists to "
                f"avoid. Wait for the new terrain_write.")
        print(f"dead-record guard: {short} is at file line {seg.file_line} "
              f"(seq {seg.seq}), not the refused {line}")

    live = appliedmod.Applied(actor=ACTOR)
    payload, keys = everburning_payload()

    # THE STANDING CENSUS IS TAKEN ONCE FOR THE WHOLE RUN, BEFORE ANY SITING.
    # Once, because it must span every segment in the run: a per-segment
    # census is the per-segment blindness in a new costume, and the shared
    # junction is exactly where two segments' lamps collide.  Before, because
    # the first segment's siting has to see the last segment's lamps from the
    # PREVIOUS run.
    standing = None
    if not args.no_gate:
        import replay as R  # noqa: PLC0415
        with R.Server(dry=False) as srv:
            srv.probe()
            standing = Standing(srv, [segs[w] for w in want])
        print(f"proximity gate: {standing.describe()}")
    print(f"lamp {LAMP_PREFAB}, seat offset +{seat_offset_m():.5f} m, "
          f"flame +{FLAME_ABOVE_SURFACE_M:.5f} m, everburning payload "
          f"{keys} = {payload}")

    report = {"lamp": LAMP_PREFAB, "seat_offset_m": seat_offset_m(),
              "flame_above_surface_m": FLAME_ABOVE_SURFACE_M,
              "eye_m": EYE_M, "sight_cap_m": SIGHT_CAP_M,
              "lit_criterion": {"module": GATE.__name__, "sight_m": SIGHT_M,
                                "sight_distances_m": GATE.SIGHT_DISTANCES_M,
                                "max_unlit_run_m": GATE.MAX_UNLIT_RUN_M,
                                "depends_on": LAMP_COVERAGE_DEPENDS_ON},
              "everburning_zdo_ints": keys, "data_b64": payload,
              "biome_policy": BIOME_POLICY, "segments": {}}

    for short in want:
        seg = segs[short]
        stations = resample(seg)
        zones = zones_for(stations)
        print(f"\n=== {short} ({seg.name}) {seg.length_m:.1f} m, "
              f"half-width {seg.half_width_m} m, ledger file line "
              f"{seg.file_line} seq {seg.seq}, {len(zones)} zones")
        ground = Ground(zones, SCRATCH / f"{short}.bin", live)
        print(f"  seat surface: {ground.describe()}")
        keep = KeepOut(zones, live, actor=ACTOR)
        print(f"  keep-outs: {keep.pads.describe()}; "
              f"{len(keep.poi)} POI disc(s); {len(keep.pieces)} protected "
              f"piece(s) {keep.prot_by_site or '{}'}; "
              f"{len(keep.discs)} recorded structure extent(s)")
        plan = site(seg, ground, keep, standing=standing)
        gaps = [l["gap_m"] for l in plan.lamps if l["gap_m"] is not None]
        mins = [l["sight_min_m"] for l in plan.lamps]
        tmins = [l["sight_min_m"] for l in plan.lamps
                 if not l["sight_min_is_lower_bound"]]
        biomes: dict[str, int] = {}
        for l in plan.lamps:
            biomes[l["biome"]] = biomes.get(l["biome"], 0) + 1
        dark = night_walk(seg, plan, ground, keep, standing)
        seats = seat_check(plan, ground)
        entry = {
            "name": seg.name, "ledger_file_line": seg.file_line,
            "ledger_seq": seg.seq, "length_m": round(seg.length_m, 1),
            "half_width_m": seg.half_width_m,
            "lamps": plan.lamps, "refusals": plan.refusals,
            "notes": plan.notes,
            "spacing_m": {"n": len(gaps),
                          "min": round(min(gaps), 1) if gaps else None,
                          "median": round(_median(gaps), 1) if gaps else None,
                          "max": round(max(gaps), 1) if gaps else None},
            # TWO SUMMARIES, because they are two different facts.
            # `sight_min_m` is every lamp, including the terminal one whose
            # forward view ran out of segment rather than out of ground.
            # `sight_min_m_terrain_limited` is only the lamps whose BOTH
            # directions ended on terrain or on the cap, which is the set the
            # operator's rule is actually about.  MEASURED: reporting the
            # first alone made T12 read as a 1 m sightline when the terrain
            # there is flat to the horizon and the segment simply ends.
            "sight_min_m": {"min": round(min(mins), 1) if mins else None,
                            "median": round(_median(mins), 1) if mins else None,
                            "max": round(max(mins), 1) if mins else None},
            "sight_min_m_terrain_limited": {
                "n": len(tmins),
                "min": round(min(tmins), 1) if tmins else None,
                "median": round(_median(tmins), 1) if tmins else None,
                "max": round(max(tmins), 1) if tmins else None},
            "lamps_truncated_by_segment_end": len(mins) - len(tmins),
            "biomes": biomes, "dark": dark, "seating": seats,
            # DID THE TERRAIN ACTUALLY CHOOSE THE SPOT?  Where every candidate
            # in the window sees 80 m both ways -- which is what Meadows looks
            # like -- the sightline score is saturated and gives no signal, so
            # the tie-break (the later station, i.e. fewer lamps) decides. That
            # is a legitimate answer and a DIFFERENT answer from "the crest
            # won", and a report that does not separate them is claiming the
            # terrain picked spots it did not pick.
            "sightline_chose_the_spot": sum(
                1 for l in plan.lamps
                if l["window_best_minus_median"] > 0.5),
            "sightline_saturated_at_cap": sum(
                1 for l in plan.lamps
                if l["sight_fwd_limit"] == "cap"
                and l["sight_back_limit"] == "cap"),
            "refusal_causes": _causes(plan.refusals),
            "seat_surface": {"surface": ground.surface_name,
                             "zones": len(ground.patches),
                             "refused_lattices": ground.refused},
        }
        report["segments"][short] = entry
        print(f"  {len(plan.lamps)} lamp(s); spacing "
              f"{entry['spacing_m']}; two-direction sightline min "
              f"{entry['sight_min_m_terrain_limited']} over the "
              f"{entry['sight_min_m_terrain_limited']['n']} terrain-limited "
              f"lamp(s) ({entry['lamps_truncated_by_segment_end']} truncated "
              f"by the segment end); biomes {biomes}")
        print(f"  seating: worst air under a foot "
              f"{seats['worst_air_under_foot_m']} m, worst burial "
              f"{seats['worst_burial_m']} m")
        print(f"  night walk (the lighting gate's own unlit_runs, eye "
              f"{EYE_M} m, {dark['flames']}): "
              + "; ".join(f"{k} worst unlit run {v['worst_unlit_run_m']:.0f} m"
                          f" ending {v['ends_at']}, {v['unlit_stations']} of "
                          f"{v['stations']} stations dark"
                          for k, v in dark["by_sight"].items()))
        for st in plan.unlit:
            print(f"  REFUSED dark stretch s={st['from_m']:.0f}..{st['to_m']:.0f}"
                  f" m ({st['length_m']:.0f} m, {st['from_xz']} -> "
                  f"{st['to_xz']}"
                  f"{', OVER the gate limit' if st['refused_by_gate'] else ''}"
                  f"): no legal lamp puts a flame in view; owners "
                  f"{st['owners']}")
        for note in plan.notes:
            print(f"  note: {note}")
        print(f"  terrain chose the spot for "
              f"{entry['sightline_chose_the_spot']}/{len(plan.lamps)} lamp(s); "
              f"{entry['sightline_saturated_at_cap']} saturated at the "
              f"{SIGHT_CAP_M:.0f} m cap")
        if plan.refusals:
            print(f"  refused {len(plan.refusals)} candidate spot(s); "
                  f"first: {plan.refusals[0]['reason']}")

        if args.place or args.dry:
            _write_segment(seg, plan, payload, keys, entry, dry=args.dry,
                           standing=standing)

    if args.eye_sweep:
        report["eye_sweep"] = _eye_sweep(segs, want, live)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"\nreport -> {args.json}")
    return 0


def _eye_sweep(segs, want, live) -> dict:
    """How much the siting moves if the eye-height DESIGN constant is wrong.

    The one number in this module that is declared rather than measured, so it
    gets a sensitivity rather than a footnote.
    """
    out = {}
    for short in want:
        seg = segs[short]
        stations = resample(seg)
        ground = Ground(zones_for(stations), SCRATCH / f"{short}.bin", live)
        keep = KeepOut(zones_for(stations), live, actor=ACTOR)
        rows = {}
        for eye in (1.5, 1.7, 1.9):
            plan = site(seg, ground, keep, eye=eye)
            rows[f"eye_{eye}"] = {
                "lamps": len(plan.lamps),
                "positions_s_m": [l["s_m"] for l in plan.lamps][:40]}
        base = rows["eye_1.7"]["positions_s_m"]
        for k, v in rows.items():
            moved = sum(1 for a, b in zip(base, v["positions_s_m"]) if a != b)
            v["stations_differing_from_eye_1.7"] = moved
        out[short] = rows
    return out


def _show(res: dict, what: str) -> None:
    """Print one emit result, INCLUDING the thing a dry run exists to show.

    `schema_problems` is only populated on a dry run, and a dry run that
    prints `status` alone is a check that confidently reports success for a
    record the schema would refuse.
    """
    bad = res.get("schema_problems")
    print(f"  {what}: {res['status']} seq {res['seq']}")
    if bad:
        print(f"  {what}: SCHEMA PROBLEMS ({len(bad)}):")
        for problem in bad:
            print(f"    - {problem}")
    for check in res.get("checks", []) or []:
        if not check.get("ok"):
            print(f"  {what}: FAILED CHECK {check}")


def _write_segment(seg: Segment, plan: Plan, payload: str, keys: dict,
                   entry: dict, dry: bool,
                   standing: "Standing | None" = None) -> None:
    """One `spawn_plan` for the segment's lamps, then a `save`.

    A `spawn_plan` and not one `spawn` per lamp because the postcondition is
    the same either way -- `expect.prefab_count` carries one probe per lamp --
    and 200 chain appends for one decision is a log nobody can read.  The plan
    body goes in as a blob so the exact command list is recoverable, which is
    what `protected_piece_positions` reads for everybody else's structures.
    """
    from live import LiveBuilder  # noqa: PLC0415

    if not plan.lamps:
        print("  nothing to write")
        return
    # READ-BEFORE-WRITE, ONE POSITION AT A TIME, AT THE BOUNDARY THAT MATTERS.
    # The siting gate answered from a census taken at the start of the run.
    # Between then and now this pass has walked several segments and another
    # agent may have written; the world is shared. So every chosen position is
    # re-asked of the LIVE world immediately before its command is built, and
    # an occupied one is DROPPED from the wire rather than shrinking the
    # radius or overwriting. Occupied is reported, never silently skipped.
    if standing is not None:
        import replay as R  # noqa: PLC0415
        kept, dropped = [], []
        with R.Server(dry=False) as srv:
            srv.probe()
            for l in plan.lamps:
                # THE FLOOR THE LAMP WAS SITED UNDER, not the biome's by
                # default: a lamp that stands on the gate's minimum because
                # the biome's would have left a station dark would otherwise
                # be dropped here by the very house torch the walk measured
                # it clear of.
                pol = BIOME_POLICY.get(l["biome"], DEFAULT_POLICY)
                min_m = l.get("min_spacing_m") or (
                    pol["min_m"] if pol else GATE.MIN_SPACING_M)
                got = standing.confirm(srv, l["x"], l["y"], l["z"], min_m)
                if got["occupied"]:
                    # A lamp this run staged is not a reason to drop it: the
                    # confirm sees the world, and the world does not yet hold
                    # this pass's own staged lamps. Anything it DOES see
                    # inside the cylinder is somebody else's and wins.
                    dropped.append({**l, "confirm": got})
                else:
                    kept.append(l)
        entry["gate_confirm"] = {
            "checked": len(plan.lamps), "kept": len(kept),
            "dropped": len(dropped),
            "dropped_detail": [
                {"x": d["x"], "y": d["y"], "z": d["z"],
                 "nearest_m": d["confirm"]["nearest_m"],
                 "neighbours": d["confirm"]["neighbours"]} for d in dropped],
            "method": ("MEASURED live per position immediately before the "
                       "wire was built; see Standing.confirm")}
        if dropped:
            print(f"  read-before-write: DROPPED {len(dropped)} of "
                  f"{len(plan.lamps)} chosen position(s), occupied since the "
                  f"run's census")
            for d in dropped:
                print(f"    ({d['x']:.1f},{d['y']:.1f},{d['z']:.1f}) "
                      f"nearest {d['confirm']['nearest_m']} m")
        plan.lamps = kept
        if not plan.lamps:
            print("  every chosen position is occupied; nothing to write")
            return
    wire = wire_lines(plan, payload)
    body = "\n".join(wire) + "\n"
    import hashlib
    sha = hashlib.sha256(body.encode()).hexdigest()
    with LiveBuilder(actor=ACTOR, dry=dry) as b:
        blob = b.blob(body.encode(),
                      note=f"lamp spawn commands for {seg.seg_id}")
        b.observe(
            f"{seg.seg_id} two-direction sightline at the chosen lamp spots",
            "MEASURED: min(forward, backward) contiguous visibility of the "
            "flame at +1.39959 m against the LIVE APPLIED SURFACE "
            "(applied.Applied.surface_at over the ledger's own blobs plus "
            "PatchScan generated height, bilinear), eye 1.7 m DESIGN, 1 m "
            "stations, 80 m cap",
            "tools/jumpstart/roads/lamps.py::sightline",
            {"all_lamps": entry["sight_min_m"],
             "terrain_limited_only": entry["sight_min_m_terrain_limited"],
             "truncated_by_segment_end":
                 entry["lamps_truncated_by_segment_end"]}, units="m")
        b.observe(
            f"{seg.seg_id} worst stretch with no lamp in view at night",
            f"MEASURED: accept/sections/lighting.py::unlit_runs over its own "
            f"lamp_index of every corridor torch standing or staged, eye "
            f"{EYE_M} m over the applied surface, at the gate's shortest "
            f"sight distance {SIGHT_M:.0f} m",
            "tools/jumpstart/roads/lamps.py::night_walk",
            entry["dark"]["worst_dark_run_m"], units="m")
        b.observe(
            f"{seg.seg_id} worst air under a lamp foot",
            "MEASURED: fixtures.fixture_box solid underside minus "
            "applied.Applied.surface_at, probed at the lamp's own XZ and at "
            "the four corners of its measured footprint",
            "tools/jumpstart/roads/lamps.py::seat_check",
            entry["seating"]["worst_air_under_foot_m"], units="m")
        res = b.emit("spawn_plan", params={
            "role": "road_lighting",
            # THE UNIT IS THE SEGMENT, NOT A VOCABULARY OF ITS OWN.
            # MEASURED on Ulfsland: lamp records carry `site_id
            # lighting-S7`, the road carries NO unit at all, and the corridor
            # recheck carries `T13-temple-harboursouth` -- three vocabularies
            # for one piece of ground, so `buildstage.replay` could never see
            # a lamp as belonging to the road it stands on. That is the key
            # half of "14 of 47 walker-blocking bodies are our own lamps".
            "site_id": seg.seg_id,
            "plan_sha256": sha,
            "anchor": {"x": plan.lamps[0]["x"], "y": plan.lamps[0]["y"],
                       "z": plan.lamps[0]["z"], "yaw": plan.lamps[0]["yaw_deg"]},
            "command_count": len(wire),
            "prefabs": [LAMP_PREFAB],
            "datum": ("each lamp's own applied surface -- the "
                      f"{patch_surface()} generated lattice, digest-proved per "
                      "zone against its terrain_writes, plus their deltas -- "
                      f"+ {seat_offset_m():.5f} m, the prefab's measured "
                      "pivot offset from fixtures.fixture_box"),
            # RESEAT, NOT FORBIDDEN, AND THE DIFFERENCE IS MEASURED.
            #
            # This record used to declare `FORBIDDEN` with the reason "lamps
            # stand ON the road the segment already graded. A terrain_write
            # here would re-level ground the road owns" -- a statement about
            # the ROAD'S interest, not about any hazard this lamp carries,
            # and asserted where nothing has proven the road walkable:
            # `buildstage.py` declares `roadside` (7) after `road_ground`
            # (4), and the only step that re-reads the graded corridor is
            # `recheck` (8), AFTER the lamps.
            #
            # WHAT IT COST, MEASURED ON ULFSLAND save bc484171e3039bc0.  ONE
            # piece_groundtorch at (-907.414, 7713.005), seq 2784, froze
            # FOUR N4-mid2-terminus stations -- 2962/2964/2965/2967, rises
            # 2.106 / 2.416 / 2.538 / 3.041 m against a 0.7813 m slide limit
            # -- at owner distances 1.087 / 0.414 / 1.078 / 1.155 m.  The
            # torch's own footprint is 0.233 x 0.237 m (fixtures.fixture_box,
            # half-diagonal 0.166 m); the repair cones it refused reach
            # 3.125 to 4.321 m, because a cut of depth d must touch out to
            # d / slide_limit.  A 3.041 m step in a carriageway that nothing
            # could ever repair, held by a 0.166 m torch.
            #
            # The claim itself is REAL -- cut the shoulder and the lamp hangs
            # in the air, which `seat_check` above measures -- but it is a
            # claim on the lamp's OWN SEATING, and `relight.py` retires and
            # re-spawns lamps as one recorded operation.  So it is
            # DISCHARGEABLE: a terrain_write passes by naming this seq in
            # `params.reseats` and re-seating afterwards, and is still
            # REFUSED if it does not.  Nothing is waived.
            "flatten": "RESEAT",
            "flatten_reason":
                "a lamp's claim is on its OWN SEATING: cut this ground and "
                "the lamp is left hanging above it. The ground itself is "
                "the road's, and the road may regrade it -- a terrain_write "
                "discharges this claim by naming this seq in "
                "params.reseats and re-seating the lamps afterwards "
                "(roads/relight.py). A write that does not name it is still "
                "refused.",
        }, wire=wire, requires={"blobs": [blob],
                                "mods": ["WorldEditCommands", "ServerDevcommands"],
                                "prefabs": [LAMP_PREFAB]},
            expect=expect_for(plan),
            meta={"lamps": len(plan.lamps),
                  "spacing_m": entry["spacing_m"],
                  "biomes": entry["biomes"],
                  "sight_min_m": entry["sight_min_m"],
                  "sight_min_m_terrain_limited":
                      entry["sight_min_m_terrain_limited"],
                  "worst_dark_run_m": entry["dark"]["worst_dark_run_m"],
                  "unlit_stations": entry["dark"]["unlit_stations"],
                  "unlit_refused_stretches": plan.unlit[:12],
                  "worst_air_under_foot_m":
                      entry["seating"]["worst_air_under_foot_m"],
                  "everburning_zdo_ints": keys,
                  "refused": len(plan.refusals),
                  "why": ("sited to put a flame in view of every station as "
                          "accept/sections/lighting.py measures it, each at "
                          "the legal spot that lights the most road onward, "
                          "then the longest two-direction terrain sightline; "
                          "see lamps.py")})
        # A DRY RUN'S WHOLE VALUE IS `schema_problems`, and printing only the
        # status hides it: `status` says "validated, NOT appended" either way.
        _show(res, "spawn_plan")
        sv = b.emit("save", params={"role": "road_lighting"},
                    wire=["save"],
                    expect=expect_for(plan))
        _show(sv, "save")
        print(f"  {b.close()}")


if __name__ == "__main__":
    raise SystemExit(main())
