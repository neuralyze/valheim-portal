# crossings

Everything where land meets water: bridges, docks, harbours, boathouses, ferry
terminals and the boats that make a ferry a ferry. Owned by `Crossings`.

**Not here:** road ribbons and fords (terrain, `tools/jumpstart/roads/`),
vegetation clearing and the flatten datum (`tools/jumpstart/clearing/`,
`tools/jumpstart/terraform/`), towns, watchtowers, castles and lighthouses
(`tools/jumpstart/settlements/`), the ledger schema and writer
(`tools/jumpstart/ledger/`).

## The interchange file

`structures.yaml` is the single output other agents read. One record per
structure, emitted by `plan.py`, never hand-edited — so no number in it can
drift from the measurement that produced it.

## Files

| file | what it does |
| --- | --- |
| `water.py` | `VHPATCH1` loader straight into numpy, plus the water primitives: `wet`/`land`/`depth` masks, `profile()` for a straight line's span, bank heights and bed, `landmasses()`, `shoreline()` |
| `survey.py` | CLI: `landmasses`, `straits`, `bays`, `ferry`, `profile` |
| `assemble.py` | generates bridge / jetty / slip geometry from real pieces, and `verify()` runs the game's own support algorithm over the result |
| `plan.py` | the driver: declares the structures, aims each pier, verifies, writes `structures.yaml`, `out/*.commands.txt`, `out/ledger.jsonl`, `out/verify.json` |
| `ledger_ops.py` | turns a planned structure into BuildLedger v1.1 ops, in the proven order |
| `build_live.py` | sends them, one structure at a time, through `ledger/live.py` |
| `bodies.py` | shortlists the library's over-water bodies with their draught and ZDO cost |
| `PieceMaterial.cs`, `run_piecematerial.sh` | BepInEx scanner producing `data/piece_material.tsv`: real serialised `WearNTear`/`Piece`/`ZNetView`/`Ship`/`Rigidbody` fields for all 4,644 prefabs |

## The four measurements everything rests on

**The water plane is y = 30.0.** `ZoneSystem::c_WaterLevel` is a
`static literal float32(30.)`. Land is taken at `h > 30.5` so a shoreline
sample is never mistaken for buildable ground. A height test against zero calls
the entire seabed dry land.

**Heights must come from a 1 m field WITH RIVERS.** `run_patchscan.sh` runs
`WorldGenerator.Initialize` with full pregeneration. The 8 m `.biome` overview
grid is written with `SEEDSCAN_PREGEN=0` and is river-free: a dry-looking valley
on it can hold a 60–100 m river.

**A structure's span limit is structural, not aesthetic.** From
`WearNTear::GetMaterialProperties` + `UpdateSupport` + `HaveSupport`:

    support(child) = max over touching neighbours of support(n) * (1 - loss*(d+0.1))
    a piece overlapping the `terrain` layer is pinned to maxSupport
    the piece dies below minSupport

| material | max | min | horizontalLoss | verticalLoss |
| --- | --- | --- | --- | --- |
| Wood | 100 | 10 | 0.200000 | 0.125000 |
| Stone | 1000 | 100 | 1.000000 | 0.125000 |
| Iron | 1500 | 20 | 0.076923 | 0.076923 |
| HardWood | 140 | 10 | 0.166667 | 0.100000 |
| Marble | 1500 | 100 | 0.500000 | 0.125000 |
| Ashstone | 2000 | 100 | 0.333333 | 0.100000 |
| Ancient | 5000 | 100 | 0.250000 | 0.066667 |
| Ice | 1000 | 100 | 0.333333 | 0.125000 |
| Timberwood | 200 | 10 | 0.200000 | 0.076923 |

At a 2 m deck pitch this gives: wood bent spacing 8 m (mid-span support ~25
against a min of 10), wood free span 12 m absolute, iron stringer free span 72 m
at a 3× factor and 96 m absolute, HardWood log piles reaching ~20 m above the
bed. **Stone cannot span at all** — `horizontalLoss` 1.0 against a min of 100
means a stone piece 2 m horizontally from support has zero support.

**And the reason the check must run offline:** `UpdateWear` begins
`if (ZNetScene.OutsideActiveArea(pos)) { m_support = GetMaxSupport(); return; }`.
Support is only ever *evaluated* when a player is nearby. An unsound bridge
looks perfect in a save and collapses the instant somebody walks onto it.

## Why nothing here is flattened

Every record carries `flatten: "FORBIDDEN"` with a reason, and the ledger's
replay driver refuses a `terrain_write` overlapping one. At the earlier
`early-dock` site the solver found `water_dist_m 0.0`, the pad levelled the whole
rectangle, and after flattening there was no water left under the footprint — so
an over-water pier stood on dry levelled ground and read as floating. The
operator's rule is the correct one: **uneven bases are a defect on land and
expected over water.**

Zones are still *generated* and the landward end still *cleared* first. On a
server with no peers, `Heightmap::Generate` → `ApplyModifiers` looks for its
compiler in `TerrainComp::s_instances` (instantiated components only), finds
none, and `SpawnZone` runs `PlaceVegetation` against the unmodified collider in
the same call — so vegetation planted after a deck exists stands through it.

## Ferries

Viable, with one honest gap.

*Persistence is measured end to end.* All five ship prefabs carry
`ZNetView.m_persistent = 1` and `m_type = 1` (`ZDO.ObjectType.Prioritized`);
`ZNetView.Awake` copies that into the ZDO's `DataFlags` bit 4, which
`ZDO.get_Persistent` reads; and the save clone builder
`ZDOMan::PrepareSave → GetSaveClonePerChunk → AddObjectsPerChunk` keeps a ZDO iff
`zdo.Persistent` and its prefab is not in `Game.PortalPrefabHash`. A spawned
Longship is written to the chunked save. (Portals are excluded from the
per-chunk save and handled separately.)

*Drift is not measured and cannot be from this host.* Valheim has no mooring
mechanic. `Ship::CustomFixedUpdate` returns unless `IsOwner`, forces
`m_speed = Stop` and `m_rudderValue = 0` when `m_players.Count == 0`, and then
still applies buoyancy and damping through `AddForceAtPosition` plus
`ApplyEdgeForce` every tick. So an unmanned boat is inert while nobody is in its
active area and subject to wave forces while somebody is. Mitigated with
geometry rather than hope: a three-sided slip 11 m wide inside (sized from the
measured `VikingShip` hull, 9.16 × 21.56 m) and a boat at **both** ferry
terminals so a drifted boat cannot strand the crossing. The operator is the
first real test of drift.

## Siting a waterfront

Use the shoreline test, not distance to water. A site must be a sample that is
land (`h > 30.5`) **and** 4-connected to a wet sample, **and** it must admit a
pier: sweep bearings at 5°, require the outer half of the pier line to be
contiguously wet with the head deeper than the hull draught (2.5 m for a
Longship, from `Ship.m_waterLevelOffset = 1.5`). Distance to the nearest *wet
cell* answers a different question: at Vestvik a reported 94 m to water is
133.7 m to the nearest shoreline sample, and that shoreline admits no 22 m pier
at any of 72 bearings — the first one that does is 227.3 m away.
