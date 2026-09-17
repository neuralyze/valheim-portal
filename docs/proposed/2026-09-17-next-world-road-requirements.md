# Next world generation: road network requirements

Operator requirements, stated 2026-09-17 after walking the Ulfsland build. These are
DESIGN inputs for the next world, not observations about the current one. Everything
under "Measured constraints" is a number this project paid for; everything under
"Requirements" is the operator's call and is not negotiable by a solver that finds it
inconvenient.

## Requirements

1. **Roads must respect terrain, not fight it.** Route THROUGH mountain passes rather
   than cutting a shelf across a mountain face, and SKIRT swamps rather than crossing
   them. The current network was routed on distance and gradient alone; the operator
   wants routing that reads the landscape the way a road builder would.
2. **A main north/south trunk road the full length of the centre continent**, so a
   player can walk almost all the way to the Deep North without a portal. This is the
   spine of the network; every other segment is a spur off it or a link to it.
3. **A dock at the northernmost reachable point**, carrying a large ship from
   `Marlthon-OdinShip` (installed; 0.7.9 deployed, 0.8.1 available). The north dock is
   the terminus of the trunk road and should read as the end of the road.
4. **Long tall bridges and/or ferries where the road needs them.** The operator observed
   specific places on the current build that wanted one and had neither.
5. **A TALL lighthouse at the northernmost point, beside the north dock.** Height is part
   of the requirement, not decoration: it is the landmark that marks the end of the road.
   Three lighthouses already stand on the current world (`lh-south`, `lh-east`,
   `lh-west`), so a proven body exists - what is new is the height and the position.
6. **Lit lampposts along the roads, sited by TERRAIN rather than by interval.** Spacing is
   30-60 m, but the placement rule is the visibility one: put each lamp where the terrain
   gives the longest sightline in BOTH directions along the road. **Dense in forests and
   other low-visibility biomes; sparing in Meadows and other open ones.** A lamp every
   45 m on a straight open meadow is waste; a lamp on the crest before a Black Forest
   bend is the whole point.
7. **Two CLASSES of waterfront installation, and the geography decides which.**
   - **Small boathouse** - a slip for a small boat and a short hop, sited wherever
     crossing water saves a player a long walk around.
   - **Large harbour port** - only where the geography justifies it: a large harbour,
     inlet or open sea. It gets harbour WALLS, GATES, multiple large boathouses berthing
     several large ships, a LIGHTHOUSE, DEFENCES, and it sits NEXT TO A TOWN.
   **Berth real `Marlthon-OdinShip` vessels, various, matched to berth size.** A large
   boathouse berthing a rowboat is the same class of defect as a slip that cannot float a
   boat: both are scenery pretending to be infrastructure.
   The siting question is measurable in both cases. Small: does crossing here get a player
   somewhere they would otherwise walk a long way around? Large: is there a sheltered body
   of water big enough, AND a town adjacent?

## The fortification kit already exists

`JamesJonesTV-RavenwoodVikingHouses` 7.7.8 is installed on all 7 profiles and its bundle
carries 94 prefabs, enumerated by reading the bundle's own container index rather than by
guessing name shapes. It includes a genuine wall system, which is what requirement 7's
harbour walls, gates and defences should be built from:

```
mm_large_wall            + _corner _cross _end _gateway _glass _tower
mm_large_wall_ice        + _corner _gateway _glass _stairs _stairs_reverse _tower
mm_large_wall_lava       + _corner ...
mm_castle_wall  mm_castle_gateway  mm_castle_tower  mm_large_gate
mm_castle_floor  _floor_2x2  _floor_8x8
rp_wood_floor_1x1 / 2x2 / 4x4 / 8x8   rp_wood_angle_floor_1x1 / 2x2
mm_hedge_straight  _angle_15 _angle_45 _angle_90 _arch _quarter _half_*
mm_maple_tree_01/02   mm_sakura_tree_01-04   mm_displaycase
```

Two cautions measured at the same time:
- **14 of the 94 live under `assets/_customprefabs/_todo/`** (the hedges and trees). They
  ship and probably register, but the author has them marked unfinished. Do not make
  anything load-bearing out of a `_todo` prefab.
- **Its pieces are currently INVISIBLE in the hammer**, along with `OdinsHorsePen` and
  `OdinsUndercroft`, while `OdinPlus-Basements` works and `RavenwoodRestorations` is
  partly visible. That asymmetry is under diagnosis. **Design for this kit; do not build
  a port that depends on it until the registration failure is resolved.**
## Measured constraints these requirements must live inside

Every figure below was measured on this fleet, with its instrument.

### The engine bounds the shape of a road
- **Terrain writes clamp at +/-8 m against the GENERATED height** (`tcdata.CLAMP_M`).
  This is an engine limit, not a policy. **A mountain pass therefore has to be FOUND,
  not carved**: any crossing needing more than 8 m of cut is unbuildable as terrain,
  which is exactly why requirement 1 is a routing rule rather than an earthworks rule.
- **The walkability limit is the game's own 38 degree slide angle = 0.781** over an 8 m
  baseline. Report the 1 m figure beside it, never instead of it - they are different
  quantities and the 1 m number reads alarmingly on a correct road.
- Design grades used on the current network: 8% trunk, 12% spur, 18% mountain, 25%
  ceiling. These are TASTE, derived from historic cart roads, and are labelled as such:
  the cart has no slope logic in its physics, so cart behaviour on a grade is momentum
  rather than a rule.
- **A zone holds exactly ONE `_TerrainCompiler`**, so a road's cost is PER ZONE, not per
  metre. Measured: 33,619.5 m of ribbon over 470 zones = 470 ZDOs. A crossing that
  clips a 64 m zone costs one compiler whether it is 10 m or 90 m of ribbon through it.

### Water, swamps and bridges
- **NEVER write terrain below `c_WaterLevel` 30.0** beside or under a structure.
  Draining water out from under a pier is the "early dock" defect and belongs to the one
  damage class this project treats as unrepairable. S7 carries a 7.749 m transverse step
  that is a RECORDED `water_edge` clip and is permanently closed for this reason: the
  wall is correct and grading it would be the defect.
- **Bridges have a measured structural ceiling of ~96 m span.** On the current world a
  174.7 m crossing was refused against that ceiling and the answer was a ferry, not a
  bridge built in hope. So requirement 4 splits on a number: span under the ceiling ->
  bridge; over it -> ferry terminal.
- The working ferry pattern already exists and is proven: `ferry-terminal-eastisle` with
  a Longship, paired portal tag `x-ferry-e`, both ends read back off their ZDOs.
- **Roads cannot be built from pieces.** `piece_pavedroad` has no persistent `ZNetView`,
  so a road IS a terrain write. Bridges are the opposite - they are placed bodies, and
  their piles are protected footprints the road must grade AROUND, not through.
- A road's approach to a bridge deck legitimately writes terrain at the abutment. The
  ramp onto the S2 deck leaves a recorded 0.37-0.44 m crest because the last 1.5 m sits
  inside the bridge's protected footprint. That crest is the PRICE of the protection and
  is deliberately not smoothed.

### Routing must read the biome and the POI dump
- **The land/water test is `height > c_WaterLevel`, never `height > 0`.** The `h > 0`
  form classifies the entire seabed as dry land, and it is how two witness pads were
  sited 12 m under water and quoted at the operator four times.
- **190 mod POI types exist that the mod-free location dump cannot see**
  (`More_World_Locations_AIO`). 10 of 18 Stenvik pads and 5 of 9 treehouse pads held
  furnished mod houses that had to be built around. Any router that plans against a
  mod-free dump will plan through someone's house.
- POI clearance is per type: `max(exteriorRadius, interiorRadius, znviewReachM)`, and
  the per-type MEASURED reach differs from the declared radius by up to 3x
  (`TrollCave02` declares 12 m, measures 35.8 m). Use the measurement, not the
  declaration, and never a single global stand-off.
- **A town's `pad_radius_m: 100.0` is a DISTRICT radius, not a footprint.** Reading it as
  a footprint made the road refuse to write for 294 m into Stenvik and left the town
  unreachable behind a 10.4 m step. Foundations are the per-building `site_pad`
  rectangles, worst half-extent 13.05 m.

### Two rules the current build learned the hard way
- **A road must ARRIVE.** Solve the trunk profile so its terminus matches the DATUM of
  what it arrives at - a town pad, a dock deck, a bridge abutment. The T4 defect was a
  profile solved from terrain alone that stopped 10.4 m below Stenvik's floor, and every
  component downstream behaved correctly around a number that meant nothing.
- **Two roads closer than the sum of their batter runs are ONE CORRIDOR.** Grading them
  toward each other digs a ditch between two roads. Level the interstice flush at the
  higher profile and batter only the outside of the pair; where the height difference
  exceeds what the grade can absorb across the gap, leave a recorded terrace.

### Buildings in a carriageway
Road clearing removes vegetation by explicit prefab list and has never removed a
building. A structure standing in a road is a ROUTING failure and must be fixed by
re-routing or re-terminating - **never by deleting the building**. The operator found a
circle of huts in the road at Stenvik's far end on the current build.

## Seed note

The current world `Pirate68` was chosen for a measured ~7.9 km walkable corridor toward
the Deep North, which is what makes requirement 2 achievable here. Its largest landmass
is 5.4559 km2 - the spawn island (2.7754) plus the west isle (2.6343), joined only by a
12.0 m and a 34 m channel, both now bridged. Component counts by cell size: 4 m -> 7,794,
8 m -> 1,991, 16 m -> 294. **Quote the resolution with any island count or not at all** -
the same terrain reads as one landmass at 16 m and 7,794 at 4 m.

If a new seed is generated, the trunk-road requirement makes CONTINUOUS NORTH/SOUTH
WALKABLE LAND a seed SELECTION criterion, measurable before any build starts: rasterise
the height field, threshold at `c_WaterLevel`, and reject a seed whose largest
north/south walkable run is shorter than the corridor the operator wants.
