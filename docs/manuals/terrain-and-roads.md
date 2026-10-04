# Terrain and roads: how to shape ground and build a road network that works

A manual for building a Valheim road network programmatically, written for an agent
with no memory of the session that produced it. Every rule below carries the
measurement that produced it and the defect it prevents. Where a technique was
tried and failed, it is recorded as a refusal with its numbers, because the
failures cost more hours than the successes.

## How to read the provenance tags

Every fact is tagged, because the tag decides whether the fact survives the next
world.

| tag | meaning | survives a world REGENERATION on the same seed? | survives a NEW seed? |
|---|---|---|---|
| `[ENGINE]` | a constant or behaviour read out of `assembly_valheim` (IL, md5 `89ffdb64fefebc011f5a9a826f2968bf`, Valheim l-1.0.12) | yes | yes |
| `[SEED]` | derived from the world generator: heights, biomes, location request list, land components, node coordinates | yes | NO - re-derive everything |
| `[SAVE]` | derived from the save: ZDOs, `_TerrainCompiler` blobs, placed pieces, pad datums, the ledger | NO - gone on regeneration | no |
| `[DESIGN]` | a decision, not a measurement. Argue with it, but do not call it measured | n/a | n/a |
| `[INFERRED]` | generalised from one or few observations. Treat as suspect | n/a | n/a |

The `[SEED]`/`[SAVE]` split is the spine of this document. A road plan built
only from `[SEED]` and `[ENGINE]` facts can be recomputed on a fresh world; a
plan that depends on `[SAVE]` facts (pad datums, live markers, ZDO positions)
must be recomputed after every regeneration and must be measured, not remembered.

Proof the split is real: `tools/jumpstart/roads/spec.py`'s installation
coordinates are recorded as "MEASURED for this seed and unchanged by the world
rebuild (same seed Pirate68 / 147627509)". The world was rebuilt; the seed-derived
coordinates did not move. Every ZDO in it did.

---

## 1. The vertical frame. Get these four numbers right before anything else

```
c_WaterLevel            30.0 m    [ENGINE]  ZoneSystem::c_WaterLevel,
                                            static literal float32(30.)
TerrainComp clamp      +/-8.0 m   [ENGINE]  TerrainComp::ApplyToHeightmap
slide angle             38 deg    [ENGINE]  Character::GetSlideAngle (IsPlayer);
                        = 0.781             45 mounted, 90 monster
DECK_DATUM_Y            30.6 m    [DESIGN]  roads/spec.py; every deck in the
                                            world, bridge and dock alike
```

### 1.1 The water plane is y = 30.0, not zero

**Rule: the land test is `height > c_WaterLevel + freeboard`, NEVER `height > 0`.**

`tools/jumpstart/roads/field.py`:

```python
WATER_LEVEL = 30.0

def land(self, freeboard: float = 0.5) -> np.ndarray:
    return self.h > WATER_LEVEL + freeboard
```

The defect it prevents, MEASURED: a first land/water test written as `h > 0`
classifies **the entire seabed as dry land**. On 2026-09-16 that datum error had
already approved **nine base locations underwater, including a character spawn
4.2 m below the surface**, and two witness pads were sited 12 m under water and
quoted to the operator four times before anyone measured. The same shape recurred
three times in one night, in three different modules, which is why the response
was to make every agent grep its own code for a zero-comparison against a height
and report back even when the answer was "I was already right".

Corroboration that the heights are absolute world y and 30 is the shoreline
rather than an offset, MEASURED on seed `Pirate68` `[SEED]`: the Ocean biome's
height p99 is **3.88** and the non-Ocean p1 is **8.11**. There is no distribution
here in which 0 is a shoreline.

A second, related trap `[SAVE]`/`[SEED]`: `site_finder.WATER_LEVEL` once read the
**ocean-floor clamp (~4)** as the surface, so its `water_dist_m` measured distance
to the wrong isoline entirely (`tools/jumpstart/worlds/README.md:306`,
`worlds/derive.py:419`). Two different wrong water levels, same class of defect.

### 1.2 The clamp decides what is buildable, and it is not a policy

```
if (levelDelta != 0 || smoothDelta != 0)
    heights[i] = Clamp(heights[i] + levelDelta + smoothDelta,
                       baseHeights[i] - 8, baseHeights[i] + 8)
```

Two consequences `[ENGINE]`:

1. **The delta applies to the GENERATED height.** A flat delta field flattens
   nothing: `levelDelta[i]` must be `target - generated[i]`, and `generated` has to
   come from the world generator itself (PatchScan, 1 m, rivers included) `[SEED]`.
2. **The result is clamped to +/-8 m of generated.** A cut or fill deeper than that
   **cannot be produced by terrain data at all**, whatever is written. Vangard's
   save stores deltas down to **-44 m**, which the game clamps on apply. The stored
   number is not the applied number.

Therefore:

- **A mountain pass must be FOUND, not carved.** Any crossing needing more than
  8 m of cut is unbuildable as terrain. This turns "route through the pass" from an
  earthworks instruction into a ROUTING constraint.
- **A large pad terraces; it does not platform.** Pad census at two surveyed
  harbour sites `[SEED]` - site A (1050, 720) and site B (1190, 444), both 100%
  Meadows out to 200 m - candidates that are >= 97% dry, fit tested against the
  +/-8 m clamp about each pad's own median floor:

  | pad | site A candidates / fit | site B candidates / fit | cut needed med / p90 / max (site A) |
  |---|---|---|---|
  | 16x16 m | 1401 / **1390 (99%)** | 1506 / **1480 (98%)** | 2.15 / 4.69 / 10.25 m |
  | 24x24 m | 556 / 527 (95%) | 588 / 546 (93%) | 2.98 / 6.66 / 12.75 m |
  | 32x32 m | 283 / 242 (86%) | 314 / 250 (80%) | 3.77 / 8.94 / 12.98 m |
  | 48x48 m | 113 / **68 (60%)** | 126 / **64 (51%)** | 6.26 / 13.53 / 16.17 m |

  **Verdict: build a town as terraces of 16 m and 24 m pads, not as one platform.**
  A 48 m pad fails the clamp 40-49% of the time on measured Meadows ground.
  Stenvik's own record states the same outcome in prose: "no 176 m district of
  <= 12 m relief on this seed is both dry and coastal, so the town follows the
  ground on individual pads" `[SEED]`.

- **A blueprint captured on a slope cannot be fixed by sculpting.** MEASURED on
  `BjOrN_blueprint001.blueprint`, rasterised over 508 columns / 2,032 m2: local
  relief `-0.500 .. +13.342` = **13.84 m, five terraces**, with a 466 m2 deck at
  local +6.148 standing on nothing. 13.84 m > 8 m, so no pad can receive it. It was
  placed three times before anyone measured, and each time the operator could see
  it floating. The fix was a different body (`halvar-master-refinery`: 4.3%
  floating against 34.6%, floating walkable floor 132 m2 -> **0**), not a deeper cut.

**Proof the clamp was respected across the whole build** `[SAVE]`, aggregated from
`tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl`: 100 `terrain_write` records,
750 per-zone compiler entries, **361,460 height samples modified, 0 clamped**,
worst deltas `-7.881` / `+7.471` m. The ribbon refuses the whole write if any
sample lands past the clamp:

```
samples past the MEASURED +/-8 m apply clamp: {st['over_clamp']}
REFUSING: a sample past the clamp is road that will not be where the plan says.
```

Held-back margin `[DESIGN]`: `route.CLAMP_MARGIN_M = 1.0`, so routing plans against
`CUT_FILL_MAX = 7.0`. The reason is measured geometry, not caution: the routing
field is sampled on a half-integer lattice and the ribbon is written on the integer
lattice, so a written sample can sit up to **0.71 m** from any sample the profile
was fitted against.

### 1.3 A road is a terrain write, not a line of pieces

`piece_pavedroad` **has no persistent `ZNetView`** `[ENGINE]`, which is why **zero
road pieces exist in any of the five fleet world saves** `[SAVE]`. There is nothing
for a spawn to leave behind. Terrain, by contrast, persists and carries ground
PAINT as well as height: on Vangard, a world someone actually played, there are
**66,016 paved samples covering 84,031 m2** `[SAVE]`. So a road is a levelled,
painted ribbon of terrain, and that is also the mechanism players themselves use.

Paint channels, from `Heightmap`'s own constants `[ENGINE]`: `r` dirt, `g`
cultivated, `b` paved, `a` vegetation-still-standing (driven DOWN to clear it).
The ribbon writes `paved_cleared` on the carriageway and `dirt_cleared` on the
shoulder, and **writes no paint on the batter** - painting the batter would read as
a 29 m wide dirt highway instead of an 8 m road.

### 1.4 The ZDO cost of a road is PER ZONE

A zone holds exactly **one** `_TerrainCompiler`, because
`Heightmap::GetAndCreateTerrainCompiler` returns the FIRST compiler in the zone and
a second is dead weight `[ENGINE]`. MEASURED off the ROUTED network plan (`[SEED]`;
35 segments planned, 15 of them built on Ulfsland): **33,619.5 m of ribbon,
169,612 m2 paved, 470 zones = 470 ZDOs.** A crossing that clips a 64 m zone
costs one compiler whether it is 10 m or 90 m of ribbon through it. Corroborated
`[SAVE]`: the 15 built segments plus every pad and repair total 750 per-zone
compiler entries across 100 `terrain_write` records.

Recorded refusal of a wrong number: an earlier estimate of **956 ZDOs per 957 m**
was quoted at the operator and is wrong *in kind* - it measured a PIECE-based road,
the approach terrain replaces. Quoting it against a terrain ribbon overstates cost
by roughly the zone length.

### 1.5 A missing compiler is a missing ROAD - audit by what CLAIMS the zone

A zone's compiler IS its terrain delta. Lose it and `TerrainComp` adds nothing,
so the ground reverts to GENERATED height and the road there stops existing while
every piece census still reads clean `[ENGINE]`. So audit the compilers, and
audit them against the ledger's own claims.

MEASURED 2026-09-19 over all **435** zone centres the chain claims a
`terrain_write` for `[SAVE]`, calibrated **5/5** against zones known to hold one:

```
429 / 435 = 98.62%  hold the compiler the chain claims
  6                 do not
  0                 hold more than one   (the one-compiler invariant, live)
```

**Then ask what CLAIMS each absence, because that question splits a hole from a
hole nobody ever dug:**

```
(14,12) (15,11) (15,12)  claimed ONLY by an unrealised write (seq 2908)
                         live centreline in them: 0.0 m each
                         NOT DAMAGE - nothing was ever written there
(16,11)                  ulfsgard-dredge seq 2666, deleted and never respawned
                         (build-harness-and-protocol.md 16.3)
(13,11) (13,12)          claimed by T8 x4, T9 x3 and wt-town back to seq 218/234
                         REAL, cause UNKNOWN, predates 2026-09-19
```

Three of what was reported as "five unexplained zones" were phantoms of a write
that never landed. The report that produced them was honest and the inference
drawn from it was not: **"this zone holds no compiler" is only a defect if some
record claims it.**

The live damage, so the repair can be scoped `[SAVE]`:

```
(13,11)  T9 98.0 m of live centreline          compiler ABSENT
(13,12)  T8 20.5 m + T9 6.5 m                  compiler ABSENT
                    125.0 m of graded carriageway with no compiler
blob deltas those zones owed:
(13,11)  -1.434 .. +1.177 m      (13,12)  -2.938 .. +0.273 m
```

So **up to 2.94 m of the T8/T9 junction has moved** `[INFERRED from ENGINE
behaviour: `TerrainComp::ApplyToHeightmap` adds the delta to the generated
height, so an absent compiler is a zero delta. There is no height-read verb, so
the surface itself was not measured.]`

**Scope a re-emit to what the BLOBS claim, never to what the CENTRELINES cross.**
The correction that earned this rule: T8 has 0 m of centreline in (13,11)/(13,12),
which was read as "T8 is not exposed" - and seq 1946, the live T8 write, carries
**878 modified samples** for (13,11) and **175** for (13,12), because the
one-compiler invariant forces every blob to carry the prior zone contents forward
verbatim - **§1.7 measures how much of a shared zone that is (51% of 80,870
samples) and what it does to a repair**, and
`world-generation-and-replay.md` §5.5.4 calls it the laundering class. A
centreline-scoped repair would have under-scoped it and left the junction
broken.

**The instrument, and it needs no console sink:**

```
findObjects -near <zone centre x> 0.00 <zone centre z> 2.00 -prefab _TerrainCompiler
  ValheimRcon NATIVE, radius-bounded, prefab-scoped, ONE row per answer
  compilers sit at the zone centre at y = 0, so this is an exact test, not a search
  435 zones in 160 s   against 21 minutes for under 25 zones through the
                       console-sink path
```

**Corroborated by a second, independently written census** `[SAVE]`: a different
agent swept the same 435 written zones by its own instrument and returned the
same verdict - **429 present, 6 absent, 0 duplicated** - naming the identical six
zones `(13,11) (13,12) (14,12) (15,11) (15,12) (16,11)`. Two instruments that
could have disagreed and did not. It adds one figure the first sweep did not
have: **no settlement pad zone is affected**, and `wt-peak`'s (22,24)/(23,24)
both hold blobs byte-identical to the ledger.

And the (16,11) loss, quantified rather than described `[SAVE]`: the
`ulfsgard-dredge` basin now reads **+4.26 m ABOVE its 24.00 m target** across its
own **3,718 samples**, and **255 of those samples went with the compiler**. The
harbour basin is not "missing a record"; it is 4 m of seabed back where the
generator put it.

**Two containment rules, both of which retired a scare the same night** `[SAVE]`:

- **A delta measured over a search WINDOW is not a delta under a STRUCTURE.**
  Deltas of up to **1.296 m** (dockshore) and **0.779 m** (northcape) were found
  inside site search windows and read as damage; measured at the pieces instead,
  the delta at **every one of 1,033 shed pieces and 674 harbour B pieces is
  0.0000 m**, and all six bays' minimum depth is IDENTICAL to 3 d.p. applied
  versus generated-only. The window deltas were at 10 m-grid probe points, under
  nothing.
- **A compiler absent from a zone no record claims is normal, not damage.** Six
  compiler-absent zones near those sites - (10,-3) (11,-3) (12,-3) (12,-2)
  (-2,40) (-2,41) - are ADJACENT to the structures, not under them. Same
  question as the triage above: *what claims this zone?*

### 1.6 The height you SAMPLE is not the height the engine DRAWS

**Rule: level a pad, a ribbon or any datum against the surface
`HeightmapBuilder` RENDERS, never against `WorldGenerator::GetHeight`. They are
different functions and they disagree across every biome boundary.**

MEASURED from the deployed assembly's IL (md5
`47df8869abdf95abedf859fce3deb022`) `[ENGINE]`:

```
WorldGenerator::GetHeight(x,z)  = GetBiomeHeight(GetBiome(x,z), x, z)
                                  THE POINT'S OWN BIOME
HeightmapBuilder::Build         = per-vertex blend of GetBiomeHeight over
                                  THE ZONE'S FOUR CORNER BIOMES, SmoothStep-
                                  weighted, with a fast path that renders the
                                  WHOLE zone from corner b0 when all four
                                  corners agree
```

They agree only in a zone with no biome boundary in it. `blueprints/PatchScan.cs`
sampled `GetHeight`, and `terraform/flatten.py::run_patchscan` fed that straight
into every pad delta in this world.

The defect it produced, MEASURED `[SAVE]` + `[SEED]`: `wt-peak` sits in zone
**(22,24)**, whose corners are **Meadows / Meadows / Meadows / MOUNTAIN** while
**2,736 of its 4,225 interior samples are Mountain** - so the engine renders it
almost purely from the MEADOWS branch. The pad was levelled to **102.310**; the
rendered ground under it is **93.885 .. 99.427**, i.e. **5.27 m low at the pad
centre and 8.81 m low at its worst sample**. The 212-piece tower, the `u-wtpeak`
portal and its guidepost stand in **3.17 to 13.54 m of air, median 4.92 m.**

**The instrument was validated in BOTH directions** against **1,186**
worldgen-placed trees and rocks on unwritten ground - a vegetation ZDO's y IS its
ground contact point:

```
y - HEIGHTMAP surface   median -0.001   p05 -0.498   p95 +0.019   |resid|>1 m:  8
y - WORLDGEN  surface   median -0.001   min -10.516                |resid|>1 m: 43
```

`PATCHSCAN_SURFACE=worldgen|heightmap` now exists in `blueprints/PatchScan.cs`
and `run_patchscan.sh` (default unchanged), and all 435 written zones have been
re-scanned both ways.

**THE GATE IS CORNERS *AND* INTERIOR - four samples is NOT enough, and that
correction arrived after four agents had already gated on the corners alone.**
The fast path does not blend: when the four corners agree it evaluates **corner
b0's height function over the WHOLE zone**, while `GetHeight` uses each point's
own biome. So a zone with four agreeing corners and a different biome in its
INTERIOR still diverges - the fast path itself is the divergence. Three written
zones prove it, all four-corners-uniform and all wrong: **(-13,121) 5.751 m,
(-7,-3) 6.191 m, (7,3) 2.079 m.**

```
IMMUNE  <=>  all four corner biomes equal
             AND every interior sample's own biome is that same biome
zero exceptions in either direction over all 435 written zones:
  335 immune · 100 not
a 5x5 interior lattice catches (-7,-3); four corners does not
```

Gate a re-scan on that predicate and re-scan only what fails it. Two sites did
exactly this and their clearances SURVIVE the correction, because both sampled
an interior lattice as well as the corners:

```
R1 + R2 corridor, 14 zones  RE-TESTED on the strong predicate after the
                            correction: 65x65 = 4,225 samples per zone,
                            EXHAUSTIVE, one distinct biome (Meadows) in all
                            fourteen.  NON-UNIFORM 0 · UNKNOWN 0   -> IMMUNE
                            (the first pass used an 8 m / 81-point lattice; the
                            1 m raster was available and should have been used
                            first. A clearance is re-run, not argued.)
four waterfront structures, 15 piece-bearing zones
                            RE-TESTED exhaustively too: harbour B,
                            shed-dockshore and shed-vestvik IMMUNE
                            zone (-2,42) NOT: 3,414 of its 4,225 samples are
                            the other biome and its CORNERS are in the 19%
                            minority - the corner-only gate reads it backwards
                            274 of shed-northcape's 342 pieces sit in it -> FLAGGED
```

**And the flagged zone is what proves the gate discriminates rather than merely
refuses.** The `north` patch was re-scanned both ways `[SEED]`:

```
heightmap - worldgen, 90,601 samples of the north patch
  min -10.460   max +9.851   mean -0.035
  |diff| > 0.10 m on 23,169 samples = 25.57% of the patch
worst |heightmap - worldgen| under ANY of shed-northcape's 342 pieces
  -9.4634 m, under a `woodwall` at (-106.0, 2666.0)
```

**Inside a mixed zone the error is NOT uniform, so agreement at one part of a
structure clears nothing elsewhere on it.** That shed's two bay depths came back
**4.252 -> 4.245** and **6.432 -> 6.432** - the bays sit in deep water where the
two surfaces agree - while the shore end carries **9.46 m**. The first report
read the bays and generalised to the structure ("the defect misses this one"),
from a sentence written before the number existed. The structure went back to
the solver instead: apron, pile seats and gangway landing are all shore-end
geometry solved on a surface the engine does not draw there, and none of it may
be adjusted by hand (§3.2 of this document's own rule against tuning a limit
until correct output passes).

That also settles a question about stacked errors: R1's junction pin carries the
**deleted-compiler** error (§1.5) and **NOT** this one, because all 14 of its
zones are uniform. **Two candidate errors at one pin, measured separately, and
only one of them is there.**

**The road half, FIXED and SCOPED** `roads/ribbon.py::zone_patches` now sets
`PATCH_SURFACE = "heightmap"` as a constant rather than a parameter - there is
no question a terrain delta can ask of the worldgen surface and get a usable
answer - and passes `SURFACE` through to `run_patchscan.sh`.

**And the load-bearing half of that fix is the CACHE KEY.** `zone_patches`
reuses any patch file that covers the requested zones, and every patch file on
disk was written from the WORLDGEN surface under a name that says nothing about
which surface produced it. Without re-keying the output as
`{stem}.{PATCH_SURFACE}{suffix}`, the first call after the fix would have loaded
the old data, computed deltas against the wrong surface and reported success.
**A cached artefact MUST be keyed on the parameter that produced it** - the same
stale-output class the function's own SANDBOX comment already records, except
that one raised `KeyError` and this one would have been silent. It is also
**"an existence check is never a completeness check"** (Step 10) in its quietest
form: the file exists, it is non-empty, it covers the right zones, and it holds
the wrong surface. The consequence is the one that section already states - a
road written from stale ground heights is not a bumpy road, it is a road
somewhere else, permanently.

The scope, MEASURED exhaustively `[SEED]` - every 1 m sample in each zone's
65x65 lattice (median 4,225 samples per zone) off the 1 m biome raster, a second
instrument agreeing exactly with the corner-plus-lattice predicate and finding
the same `(-7,-3)` counter-example independently:

```
336 distinct zones under the 21 built segments
  189 uniform (IMMUNE)   47 NON-UNIFORM (RE-SCAN)   100 UNKNOWN (no field coverage)
  a corner-only gate would have passed 190 of them, 1 of which is non-uniform
RE-SCAN   N1 15 · T9 7 · S8 6 · T4 6 · W10 4 · W5 4 · S7 3 · N0/N0b/T10 2 each · N2 1
IMMUNE    T1 T3 T5 T8 T12 T13 S1 S12 - 8 of 21 segments need nothing
UNKNOWN   N2 28 · N3 14 · N4 53 - N3 and N4 are 67 zones, 3,521 m of the
          northern trunk, with NO 1 m field coverage at all
```

**Two things to read off that, both of which a summary would lose.** The defect
is **not** confined to the north: T4, T9, S8, S7, W5, W10 and T10 all cross
BlackForest/Meadows boundaries in the old network. And **UNKNOWN is not CLEAN** -
nobody may quote N3's or N4's profiles as sound until those zones are rastered
and classified. That same northern run is also the least-cleared road in the
world (zero clearing cylinders over 10,603 m, §7.1), so it carries two
independent unverified defects over the same 11.45 km.

**And `ribbon.py::walkability` is the road's version of the census that cannot
fail** (`structures-and-settlements.md` §3.7): it composes the surface from this
write's own deltas plus the PatchScan generated height, so if the generated half
is the wrong surface it returns WALKABLE against a road the engine draws
somewhere else - and it would return WALKABLE either way. That is one of the
four independent reasons it reported WALKABLE over 84 impassable locations;
§3.7 has the other three and the instrument that answered. R1's verdict (worst
8 m gradient **0.0839**, 0 holes, 441/441 stations on written road) is claimed
**only** because those 14 zones tested uniform. Re-derive it for any segment
outside a uniform corridor.

### 1.7 Union inheritance: a head blob is not a record of what its segment built

**Rule: a zone's blob is the UNION of everything that zone keeps, so the blob a
segment carries is a SNAPSHOT OF THE WHOLE ZONE at that segment's last emit -
never a record of what that segment authored.** This is the same one-compiler
invariant as §1.4 and §1.5, stated where it bites a repair.

MEASURED 2026-09-21 over the ledger's own `terrain_write` records `[SAVE]`:

```
zones written by roads                                   434
claimed by 2+ segments                                    46
   two-way 40 · three-way 4 · FIVE-way 2
the two five-way zones   (-1,-1) and (-1,0), the temple node,
                         claimed by T1, T3, T5, T12, T13
                         9 over-limit steps lie within 60 m of (-56,-34)
head-blob samples in shared zones                     80,870
   ground the carrying segment DID NOT AUTHOR         41,487 = 51%
all 24 sharing segments carry foreign samples; NOT ONE is clean
```

**The reconciliation, with line numbers, because two agents produced opposite
repair rules from the same code and only the code settled it:**

```
ribbon.py:3636-3640   deletes and respawns the zone's SINGLE compiler wholesale
                      -> the live surface is ENTIRELY the last write's blob
ribbon.py:3413-3450   builds that blob by unioning the ledger AT RUN TIME:
                      priors merged in FILE ORDER, later winning, written only
                      where THIS write authored nothing
```

Both are true at once, and together they give the answer neither gave alone: a
later emit **INHERITS a correction already in the file rather than reverting
it.** The last write wins the surface, but it re-reads the ledger to build what
it writes.

**Minimal correct rule for landing a terrain correction:**

1. the correction must be **in the ledger before the LAST write that touches the
   zone**; and
2. **no later write may re-author that sample wrongly.**

**The failure modes are authorship after the fact, and CONCURRENCY** - two
rasterisations that both read the ledger before either appends will each write a
blob that never saw the other's samples. **Emit serially.**

**The corollary that cost two agents an hour: two byte-identical steps reported
on two segments are ONE ground defect read twice, not two.** Both segments carry
the same sample because the union gave it to both. On that basis **86 obstacles
collapse to 84 distinct locations** (§3.7). De-duplicate by world position before
counting anything a walker meets.

### 1.8 "Nothing will revert my fix" is not "my fix will land"

**Rule: a correct mechanism plus a correct stability argument can still describe
a repair that never happens. Probe PER SAMPLE whether your write authors the cell
you intend to fix.**

MEASURED at the S1 pothole, `(-299.0, 214.0)` `[SAVE]`: a corrected S1 emit does
**not author that cell** - it is SKIPPED by the union rule of §1.7 - so the
`-1.578` is inherited and the **1.75 m step survives the repair**. The stability
argument was sound and the repair was still a no-op.

**And the cause is a 2.00 m DESIGN CONFLICT, not a surface error:**

```
S1's own profile at (-300, 214)                     35.1
T1 seq 1984, authored                               37.10
the portal hall's pad datum                         37.10
```

A "corrected" S1 would replace a 1.75 m pothole with a **2.00 m cut across the
hall's apron** - the first ground a player walks onto off a portal. **The repair
is refused and recorded here with its three numbers**, so whoever reconciles the
profile against the pad has them in one place rather than re-deriving them.

---

## 2. RAISING ground removes water. LOWERING it adds water. The rule is about direction, not about the plane

**This section stated the wrong rule until 2026-09-18 and the correction is a
measurement, not an opinion.** It said "never write terrain below the water
plane", and gave as its mechanism "cutting the bank down beside a shoreline
drains the water out from under whatever stands over it". The rule it protects
is real and every refusal it produced was correct. **The mechanism it named is
false, and a correct guard resting on a false reason is how a later session
"simplifies" it away.**

### 2.1 What was measured

`[ENGINE]` + `[SEED]`, by `tools/jumpstart/terraform/WaterProof.cs` in a sandbox
mirror of this server against a COPY of Ulfsland, driving the SAME
`tcdata.Compiler` blobs the live transport sends. Five patches in one run,
13 x 13 m each, 169 samples each, `clamped` 0 on every blob:

| operation | position | generated bed | applied bed | delta | depth before | depth after |
|---|---|---|---|---|---|---|
| **LOWER 7 m** | (1088, 720) | 23.726 | 16.726 | −7.0000 | 6.274 | **13.274** |
| **RAISE to 30.9** | (1064, 686) | 27.208 | 30.900 | +3.6917 | 2.792 | **−0.900, i.e. dry ground** |
| **LOWER a dry bank** | (1140, 720) | 33.493 | 26.493 | −7.0000 | −3.493 | **+3.507** |
| untouched control | (1088, 740) | 23.370 | 23.370 | 0.0000 | 6.630 | 6.630 |

**Adjacent cells lose nothing.** Ring samples at **1, 2, 4 and 8 m** outside all
five patches, in both +x and +z: `applied − generated` is **EXACTLY 0.0000** and
`depth after == depth before` to 4 dp. `TerrainComp::ApplyToHeightmap` adds
`levelDelta + smoothDelta` **per sample** with no spatial falloff, and
`tcdata.Compiler` writes `smoothDelta = 0`; the only spreading is the heightmap
MESH interpolating between adjacent 1 m samples.

A real `VikingShip` left to Unity physics over the 7 m cut settled at
**y = 30.1068** and bobbed 30.068…30.117 for 20 s, **13.381 m above the new
bed** — indistinguishable from the natural-water control at 29.7723.

### 2.2 Why, from the assembly

`WaterVolume::GetWaterSurface` `[ENGINE]` (asm md5
`89ffdb64fefebc011f5a9a826f2968bf`):

```
result = transform.position.y + wave + m_surfaceOffset
```

**The heightmap appears nowhere in it.** `Depth()` returns the NORMALISED depth
(`Heightmap::GetOceanDepth / 10`, clamped 0..1) and only scales the WAVE.
MEASURED per-zone water volume: `transform.y` **30.0000**, `m_surfaceOffset`
**0.0000**, `m_forceDepth` −1, a TRIGGER collider **64.000 x 60.000 x 64.000 m**
spanning **y −20 .. +40**, `GetLiquidType` Water. It exists in **every** zone,
including one measured at **0.0 % wet** (zone (20,10), ground 33.9–64.5), where
a cut reads a water surface of exactly **30.0000** — no wave, because
`GetOceanDepth` there is 0 so `CalcWave` is skipped.

The ±8 m clamp cannot cut deeper than 8 m below generated, and the deepest
seabed in harbour A is ~22.6, so **the deepest cut this project can physically
make is 35 m inside the water volume's own floor.** A dredge cannot leave it.

### 2.3 The rule, restated so the reason is true

**Rule: a terrain write must never move ground TOWARDS OR PAST y = 30.0 from
below it.** That is the direction that removes water, and removing water from
under a pier, a dock or a shoreline is the unrepairable class. Moving ground
AWAY from the plane deepens the water and is a dredge.

In code terms, the sign of the delta is the test, not the sign of
`generated − 30.0`. `settlements/harbour_city.py::dredge_plan` enforces it
structurally rather than by inspection — `target = min(applied, reg["bed"])`, and
a delta that still comes out positive is REPLACED by whatever delta the ledger
already holds at that sample (`delta, clipped = prior, False`, commented "never
fill; keep what is there"). **Verified against the source 2026-09-18: the function
does not raise; the fill direction is unreachable rather than guarded** — which is
the stronger arrangement, because a guard can be deleted and an arithmetic
impossibility cannot.

### 2.4 The two existing gates both survive — for the right reason now

The gate is asymmetric on purpose (`roads/ribbon.py::refusal`):

```python
if generated < WATER_LEVEL_M and any(
        math.hypot(wx - px, wz - pz) <= pr
        for px, pz, pr in (protected_discs or ())):
    return "underwater"        # carriageway: scoped to protected structures
...
if generated < WATER_LEVEL_M:
    return "water_edge"        # batter: absolute, everywhere
```

- **The carriageway's water rule is scoped to protected structures**, because a
  CAUSEWAY has to be possible: raising a pond bed to road freeboard is what a
  road legitimately does — and §2.1 is why that is the dangerous direction.
- **The batter's water rule is absolute, and it stands.** A batter beside a
  shoreline cuts the bank **DOWN FROM ABOVE the plane**, which is the removing
  direction. It is not "never write below the plane"; it is "never take a bank
  down to the waterline beside standing water". A batter is never load-bearing,
  so refusing it costs a recorded wall and nothing else.

**`ribbon.py`'s own comment on the batter gate still names the false mechanism
("cuts the bank down and drains the water out from under whatever stands over
it") and should be corrected in the same pass as this section.** The code is
right; its stated reason is not, and that pairing is the hazard.

MEASURED consequences, accepted rather than fixed `[SAVE]`:

- T4 (re-routed, ledger seq 2053, 2026-09-17T10:53Z): **5 batter samples clipped
  `water_edge`**, recorded with positions.
- S7 carries the network's **worst transverse step, 7.749 m**, unchanged by its own
  repair. It is a recorded `water_edge` clip and is **permanently closed**: the wall
  is correct and grading it would BE the defect. Do not reopen it.

### 2.5 What this does NOT license

Dredging is not a free operation and the same run measured its limit. Two hulls
dropped into 13 x 13 m dredged pockets holding 6 m of water went **AGROUND ON
THE RIM** at y 32.825 and 33.902: a 9.16 x 21.57 m hull does not fit a 13 m hole
and its bow and stern rest on the uncut ground outside (ring bed **35.806**).
**A dredged pocket smaller than the hull floats nothing.** Depth must still be
measured under the hull's OWN elliptical footprint at its ACTUAL yaw, on the
DREDGED surface.

And the corollary for waterfront STRUCTURES is unchanged: the harbour city's
132.0 m curtain wall is designed to **zero terrain writes** — wall height
11.866 m against max depth 6.598 m, pivot at foot, no `WearNTear`. A structure
that can stand on the seabed should stand on it. Cut the seabed only to make
water deeper, and only where a hull needs the depth.

`[INFERRED]`, and marked because the instrument cannot answer it: the visible
water SURFACE MESH is a client-side `MeshRenderer` and a headless server does
not draw it. It reads the same `m_normalizedDepth` this run measured as
UNCHANGED by a dredge (`GetOceanDepth` is generator-derived: 1.5685 at the cut
both before and after), so the surface is expected to render over a dredged
basin exactly as it does over natural water of the same `GetOceanDepth`. The
operator's eyes are the instrument for that one.

### 2.6 The instrument, and the two traps it had to survive

The measurement in §2.1 was taken by **`tools/jumpstart/terraform/WaterProof.cs`,
driven by `tools/jumpstart/terraform/run_waterproof.sh`** — a BepInEx plugin in a
SANDBOX MIRROR of the dedicated server, against a COPY of Ulfsland, with zero
live writes and no ledger token held. It spawns real zones, builds a real
`_TerrainCompiler` from a blob encoded by `terraform/tcdata.py::Compiler` — **the
same encoder the live transport sends**, so this is a result about the real write
path and not about a hoe — and then reads the GAME's own functions rather than any
of ours: `Heightmap.GetHeight`, `Floating.GetWaterLevel`, `Floating.IsUnderWater`,
`WaterVolume.GetWaterSurface`, `WaterVolume.Depth`, `Heightmap.GetOceanDepth`, and
floats real hulls under Unity physics. The generated bed each delta was computed
against is the lattice-aligned 1 m PatchScan patch `cityA` (1080, 700) half 250.5
`[SEED]`. Full tables, the reproduce command and the raw TSVs:
`local://dredge-experiment.md`.

Corroborated independently at build time `[SAVE]`: the game-read bed scored against
the computed dredge write at **2,680 cut samples — max error 0.0001 m, RMS
2.6e-05 m**, while the same readings scored against the bed reflected across the
basin diagonal came out at **RMS 0.935 m**, a ~37,000x margin. That is "applied to
the millimetre, nothing clamped" with an error bar instead of an adjective.

**Two traps inside the instrument itself, recorded because each returns a
confident WRONG answer** `[ENGINE]`:

1. **`Floating::GetWaterLevel` reads `s_waterVolumeMask` with NO lazy init**, while
   `Floating::GetLiquidLevel` initialises it. On a headless server the mask is 0,
   the cast finds nothing, and **every point in the world returns −10000** — including
   the 6.7 m of undisturbed NATURAL water used as the control. An instrument that
   reports "no water anywhere" over an ocean is not measuring water; it is
   measuring its own uninitialised field. Use `GetLiquidLevel`.
2. **`Game::FixedUpdate` calls `ZNet.SetReferencePosition(1000000, 0, 1000000)`
   unconditionally every physics tick**, so a dedicated server holds no active zones
   anywhere near the site and `ZNetScene::RemoveObjects` culls whatever a harness
   spawns. The first three float tests returned `DESTROYED_BEFORE_SETTLE` — a result
   that READS as "the hull sank" and MEANS "the harness was deleted".
   `RemoveObjects` consults only `ZNetScene.m_instances`, so the fix is to take the
   spawned hull out of that dictionary.

Both are the family this project keeps meeting and the one
`docs/proposed/2026-09-17-the-house-defect.md` is a taxonomy of: **a check that
answers a question it is not measuring.** What makes these two expensive is that
their wrong answers are PLAUSIBLE — −10000 reads as "dry", a deleted hull reads as
a sinking — so neither announces itself as an instrument fault. Cross-read a new
instrument against an UNTOUCHED control before believing anything it says about a
change: the natural-water control at (1088, 740) is what caught both.

**And record how long it survived, because the count is the lesson.** The absolute
was stated or relied upon in **eleven places in `water-harbours-and-vessels.md`** -
prose, a datum table's tail, a curtain-wall refusal, a procedure step, a
declaration step and a one-line invariant - and in **two more in this document**
outside this section (§4.4's repair note and Step 10's postcondition check). It was
enforced for weeks and was caught only when the operator asked for the OPPOSITE
operation and it was measured. A rule stated once is a rule; a rule restated
thirteen times is a rule that has stopped being checkable, because correcting it
means finding all thirteen. **State a rule ONCE, with its measurement, and cite it
from everywhere else** - and give every document a way to cite the instrument, so
the next reader can re-run the measurement instead of inheriting the conclusion.

---

## 3. Grading, batter and slope

### 3.1 The two slope numbers, and never quote one for the other

```
slide limit     0.781  = tan(38 deg)   [ENGINE]  the player SLIDES above this
design grades   trunk    8 %  (4.57 deg)   [DESIGN]
                spur    12 %  (6.84 deg)
                mountain 18 % (10.20 deg)
                ceiling 25 %  (14.04 deg)  never exceeded, any class
```

Why design far below the engine limit `[DESIGN]`, with its measurement: designing
to 38 deg produces a road that is technically stand-on-able, miserable to walk and
impossible to cart, because `Vagon` is a plain physics body - `m_baseMass`,
`m_itemWeightMassFactor`, `m_breakForce`, **no slope logic at all** (MEASURED). A
cart's behaviour on a grade is momentum, not a rule. Historic cart roads held to
<= 8%; that is the trunk number. At 8% the rise across one 1 m terrain sample is
8 cm, inside the terrain mesh's own noise, so the ribbon reads as flat underfoot.
The 25% ceiling keeps every sample under half the slide angle in degrees.

**The verdict baseline is 8 m, not 1 m** (`ribbon.VERDICT_BASELINE_M = 8.0`). They
are DIFFERENT QUANTITIES: the 1 m figure is the slope of one lattice edge, and a
1 m rise between adjacent samples on a fitted 8% profile is a rounding artefact of
the integer lattice, not a wall. Measuring the verdict at 1 m produced a false
NOT WALKABLE. Report both; take the verdict over 8 m, which is roughly the run the
capsule traverses while the collider averages the mesh under it.

**And the 1 m figure must still be COMPARED, not merely printed.** The verdict is
taken over 8 m; the 1 m number is what catches a wall, and a check that computes
it and compares it to nothing is how 84 impassable locations were reported
WALKABLE (§3.7, which also records that Valheim has no step-height constant at
all - on terrain the slide angle is the whole limit).

Final measured verdict on the re-routed T4 `[SAVE]`, the longest segment in the
world (ledger seq 2053):

```
verdict                 WALKABLE
max 8 m gradient        0.0999  at (568.0, 742.5)   against slide limit 0.781
max 1 m gradient        0.1622  at (570.9, 743.1)
centreline stations     2826, of which 2703 on road
holes                   0
gaps                    2, both cause=bridge, step_across_m -0.172 and 0.000
method                  MEASURED on the APPLIED surface (generated bilinear +
                        this write's delta), not on the plan
```

### 3.2 Longitudinal quantisation is the design grade, and it is not the defect

MEASURED on samples the road alone wrote: the applied surface matches the fitted
profile exactly and its **1 m step is 0.047-0.120 m median, 0.118-0.182 m worst** -
i.e. the 8% trunk / 12% spur design grade expressed on a 1 m lattice. Nothing to
fix here. If a report calls this blocky, the report is measuring the wrong axis.

### 3.3 The transverse edge wall IS the defect, and it is enormous

The operator's report was "roads on gradients are blocky rather than sloped". The
cause, MEASURED over the 15 standing segments: the ribbon wrote every sample out to
`edge = width/2 + shoulder` and **nothing at edge + 1**, so the height delta fell
from the full local cut or fill to 0.0 **across ONE 1 m lattice edge**.

```
step one metre outside the shoulder:  0.39-1.46 m MEDIAN
                                      up to 8.06 m  (~ the engine clamp)
                                      on 42-89 % of each segment's length
T12 at s = 302 m:  68.41 m across the whole 8 m bench, 65.27 m one metre later
```

A level bench with a vertical face on each side is a rectangular prism. On a side
slope that is exactly "blocky rather than sloped".

### 3.4 The batter is a PLANE AT GRADE, not a delta ramped to zero

This distinction cost a full repair cycle and is the single most important
mechanical detail in this section.

The first fix interpolated the HEIGHT CHANGE linearly to zero over
`run = |full| / grade`. On level ground that yields a 38 deg face. On a side slope -
**the only place a batter is ever needed** - it does not, because the applied
surface is `generated + delta`, so its transverse slope is the taper's grade PLUS
the hillside's own. MEASURED on T12 after that first batter: the step one metre
outside the shoulder read **p50 0.83 m against a 0.781 limit**. That 0.049 m is not
noise and not the design grade - it is the hill. A `step <= BATTER_GRADE` check
therefore FAILS on output that is correct.

So the batter is constructed as what it is on the ground: a PLANE through the
carriageway edge, falling (fill) or rising (cut) at exactly the grade, written as
`delta = target - generated`, and **terminated where it meets natural ground**, at
the sign flip of that delta. Three properties follow by construction rather than by
measurement:

- the transverse step of the applied surface IS the grade, so the verdict can be
  read against the grade;
- the taper ENDS AT GROUND - there is nothing left to step off;
- on a hillside steeper than the batter the two surfaces never meet, which becomes
  a MEASURABLE refusal (`steeper_than_batter`) instead of a silent wall.

```python
BATTER_GRADE = math.tan(math.radians(38.0))   # the slide limit, asserted equal
BATTER_MAX_M = 10.5                           # reach cap
SHOULDER_M   = 1.0
EDGE_EPS_M   = 1e-6
```

The grade is taken at the 38 deg slide limit rather than steeper so the batter is
WALKABLE: someone who steps off the carriageway on a hillside should walk down, not
slide. That costs reach - the full 8 m clamp needs `8 / 0.781 = 10.24 m` of run -
and `BATTER_MAX_M = 10.5` is what bounds it.

Measured outcome after the plane batter `[SAVE]`, T4 seq 2053:

```
batter rays           1837 total, 1499 met natural ground, 142 clipped
earthwork step        p50 0.189 m, p95 0.781 m  <- the design grade EXACTLY
carriageway step      p50 0.141 m, p95 0.779 m, max 3.759 m at lat 3.5
over-grade fraction   0.0378 of 7168 earthwork pairs
                      197 explained by a recorded clip, 74 UNEXPLAINED
walls left, by cause  steeper_than_batter 71, placed 66, shared_corridor 59,
                      reach 25, water_edge 5, pad 4, poi 1
                      worst 6.507 m (7.853 m before the repair)
```

Per-segment before/after, MEASURED: T12 carriageway 1 m step p50 **0.517 -> 0.152**;
T3 p50 **0.242 -> 0.136**, worst **3.676 -> 2.154**, earthwork p95 **0.782** = the
design slope exactly; T4 p50 **0.441 -> 0.233**, legacy band p50 **1.036 -> 0.781**;
over-grade fractions T5 **0.1917 -> 0.0515**, W5 **0.208 -> 0.0563**, S7
**0.1592 -> 0.0705**. `zone_ctrl` 1-of-1 in every zone, 0 past the clamp, 0 below
water, on all ten.

### 3.5 The carriageway edge is INCLUSIVE, and one float ULP decided it was not

MEASURED by the clearing census on T12 at s = 116 m:

```
lat -3 reads 67.00 m
lat -4 reads 65.24 m      <- a ONE METRE WIDE TRENCH, 1.76 m deep
lat -5 reads 68.13 m         at the road edge, deeper than the wall beside it
```

The sample sits at exactly `lat = 4.0 = half + shoulder`, and `lat` is a `hypot` of
two floats, so it returns `4.000000000000001` and a `lat <= edge` test drops it out
of the carriageway. Under a batter that notch is a HIDDEN HOLE beneath a graded
slope. `EDGE_EPS_M = 1e-6` is stated once and used at both band boundaries: twelve
orders of magnitude above the ULP, eleven below anything a player can stand in.
Proof it matters `[SAVE]`: T4 seq 2053 records `samples_at_exactly_edge: 657`.

### 3.6 Grade the WIDENED region, not the 7 m strip

Every stand-off the router computed was computed for a narrow strip. **A 38 deg
batter off an 8 m wall is about 10 m more per side**, so those stand-offs do not
transfer, and `ribbon.refusal` re-asks them PER SAMPLE against each POI instance's
own `reach + margin (+ protected extra)`. The measured corridor arithmetic, from
the constants:

```
trunk carriageway half   6.0 / 2      = 3.0 m
shoulder                               1.0 m   -> edge  = 4.0 m
batter reach cap                      10.5 m   -> total = 14.5 m PER SIDE
                                                 29.0 m shared-corridor width
```

A tool that clears, checks or reserves 8 m and then grades 29 m has measured the
wrong object. The clearing width follows the same rule: MEASURED on T4, the derived
`clear_half_m` is **13.568 m**, not 4 m.

### 3.7 The walkability check could not fail, and here is why

**Rule: A CHECK WHOSE REFERENCE IS DERIVED FROM THE SAME ARTEFACT AS THE THING
UNDER TEST CANNOT FAIL.** `ribbon.py::walkability` returned `WALKABLE, 0 holes,
0 gaps` on **every one** of the segments listed below while they were
impassable, and it was not buggy.

What was actually standing `[SAVE]` + `[SEED]`:

```
21 of 25 segments computed their deltas against WorldGenerator::GetHeight
   while the engine draws the HeightmapBuilder corner blend (§1.6)
residual against their OWN design   up to 11.516 m
   the four segments built after the fix   0.050 - 0.355 m
84 distinct impassable locations, 48 of them PROVEN
worst   8.93 m IN ONE METRE   at N4 (-634.8, 6365.4)
```

**Four independent reasons the check could not report any of it**, and each one
alone is sufficient, which is why fixing one would not have helped:

```
1 it composed its surface from the SAME PatchScan field the road was planned
  against - reference and subject share one artefact and one error
2 it averaged gradient over an 8 m baseline: a 6 m cliff inside one lattice
  metre averages to 0.75 over that baseline, under the limit
3 it excluded boundary steps from that average BY CONSTRUCTION - the step
  between a written and an unwritten sample is exactly the defect
4 it computed a 1 m figure and never compared it to anything
```

Reason 2 is not a contradiction of §3.1's 8 m verdict baseline: **the 8 m figure
is the right verdict for a fitted profile and the wrong verdict for a step**, so
a check that reports only the 8 m number cannot see a wall. Report both, and
compare the 1 m figure against the slide limit rather than printing it.

**The only instrument that answered read the surface the ENGINE composes, out of
the save:** `run_waterproof.sh FROM_SAVE=1`, calling `Heightmap.GetHeight` with
the save's own `_TerrainCompiler` ZDOs instantiated. Nothing derived from the
plan, the profile or the PatchScan field can substitute for it, because all
three are upstream of the error.

**The player limit, sourced from the game's code rather than guessed**
`[ENGINE]`:

```
Character::GetSlideAngle returns 38 for a player
  c_MinSlideDegreesPlayer = 38
  tan(38 deg) = 0.7813 per 1 m lattice edge
```

**AND THERE IS NO STEP-HEIGHT CONSTANT IN VALHEIM AT ALL.** `assembly_valheim.dll`
carries no `m_maxStepHeight`, `maxStep` or `stepHeight` on `Character` or on
`Player`; the only matches anywhere are `CharacterAnimEvent.m_footStepHeight`
(foot IK) and `AutoJumpLedge`, a trigger component attached to specific
prefabs. **On terrain the slide angle is the entire limit**, so a "step height a
player can climb" is a number nobody may quote - a 1 m lattice edge either sits
under `tan(38 deg)` or it is a wall.

---

## 4. A road must ARRIVE

**Rule: terminate on the DATUM of the thing the road meets - a town pad, a dock
deck, a bridge abutment - never on terrain alone.**

This is the signature failure of the entire build, and it occurred FOUR times in
different modules. Each instance below is a datum that already existed and was
never read. §4.6 is the fifth and it fails the other way round: a datum that was
READ OUT OF THE WRONG BOOK.

### 4.1 T4 stopped 10.4 m below Stenvik's floor

```
T4-meadhall-stathub, arriving at Stenvik near (598, 966)         [SAVE]
  longitudinal 8 m gradient   0.8849   NOT WALKABLE (limit 0.781)
  carriageway 1 m step max    6.556 m  (correctly unchanged - the pad's samples)
  residual step at the pad   -10.414 m on a 207.2 m run from (539.8, 814.2),
                             terminating on stenvik's levelled 45.57 m datum
```

A 10.4 m step is not a rough join. The player cannot climb it and cannot slide up
it, and the stretch before it exceeds the slide angle, so they slide back down. The
trunk road into the town ends at a wall.

The cause is worth stating precisely because every downstream component behaved
CORRECTLY: the pad won inside its footprint, the approach grading did what it could
within its run, the batter graded the sides. The profile was solved from terrain and
simply stopped. **Every component answered its own question correctly and nobody
asked whether the road arrives.**

The fix is arithmetic: 10.414 m of climb over 207 m of available run is 5%, well
under the 8% trunk design grade.

### 4.2 A road arrived at a 10.4 m cliff for a second reason (see section 6)

Reading `pad_radius_m: 100.0` as a footprint made the road refuse to write for
**293.9 m** of centreline, leaving a **-8.491 m step** across the gap at
(544.8, 809.2) and the town unreachable behind it.

### 4.3 `step_across_m -7.644` sat unread in the segment's own metadata

The sharpest lesson of the night: **the measurement was already in the log and
nothing consulted it.** A 7.644 m step across the road was recorded at build time,
in the segment's own record, and passed every gate because no gate asked that
question. T4 also carried **333 unwritten centreline metres, longest run 204 m**,
and **89 of 1443 centreline metres replaced by a later pad write** - at sample
(586, 954) the road wants `-4.470` and the live union holds `-1.571`, so the surface
JUMPS `45.05 -> 47.11` in one metre, mid-carriageway. Worst applied-vs-planned
deviation: T4 **6.391 m**, T13 **4.400**, T5 **3.574**.

### 4.4 `DECK_DATUM_Y = 30.6` exists precisely so a join has no step

```
ROAD-TO-DECK STEP   deck datum 30.6, T13's carriageway lands 31.195-32.230
                    closest written road sample 1.09 m away: step +0.648 m
                    3 m inland:                              step +1.63 m
                    cause: T13 built its last 2.1 m as ford f01 at ROAD
                           freeboard, not at the deck datum
```

`DECK_DATUM_Y` was adopted rather than invented: the early-dock placement already
declared `target_y 30.6` as "a dock DECK, not a sea floor", so every deck in the
world sits at one height and no join has a step in it. The terminus never read it.

**The repair for this class is a STAIR, not a terrain write.** Both directions are
refused here and for different reasons. Cutting ground DOWN to meet the deck means
taking a bank down to the waterline beside standing water in 60 cm of headroom -
the removing direction of §2.3. FILLING the 60 cm up to the deck datum is worse: it
raises a bed from below the plane to above it, which is precisely the defect §2.1
reproduced on purpose (bed 27.208 -> 30.900, 2.792 m of water -> 0.900 m of DRY
GROUND). A stair spends no terrain in either direction, which is why it is the
repair.

### 4.5 How the approach is actually solved

Two wrong constants, both justified by a proof about a different quantity.

**Wrong constant 1**, `APPROACH_MAX_M = 11.5`: "the apply clamp is +/-8 m, so the
largest step the road can owe a pad is 8 m, and `8 / 0.781 = 10.24 m` of run removes
it at grade." **The premise is false and was measured false in the same session.**
The clamp bounds what ONE write may move ground relative to GENERATED height; it
does not bound the step between a road's profile and a pad's LEVELLED datum, because
the pad spent a clamp budget of its own in the opposite direction. T4's measured
step was 10.414 m - 2.4 m larger than the largest step the constant assumed could
exist. Capped at 11.5 m the ramp grade saturates at the slide limit and the
longitudinal 8 m gradient came out **0.8849: NOT WALKABLE**.

**Wrong constant 2**, the replacement `run = step / design_grade` capped at 240 m.
That derivation is about GRADE and LENGTH, and the binding quantity is neither: it
is the FILL the write is allowed to deliver. MEASURED: the derived reach is 79.9 m
(a 6.391 m step at 8%), the cone that reach imposes lifts **175 profile nodes by up
to 6.861 m**, and five samples at (583-584, 998-1000) land **8.01-8.13 m** above
generated ground - past the clamp, which `ApplyToHeightmap` silently discards. The
write was REFUSED, correctly. A gentler ramp holds the road high far from the claim
and so fills a valley 40 m out that a shorter, steeper ramp simply walks down.

**The working method: ASK THE RASTERISER, down a ladder, with `over_clamp == 0` as a
hard constraint.** A ladder and not a bisection, because the objective is NOT
monotone in the reach. MEASURED on T4 across the full 14-step ladder, worst 8 m
gradient:

```
0.9219, 0.7763, 0.6798, 0.6230, 0.5890, 0.5322, 0.4963, 0.4717,
0.4826, 0.4583, 0.5021, 0.5093, 0.5144, 0.4847, 0.4686
        ^ falls, flattens, and RISES again
```

A bisection on that finds a local answer and reports it as the answer. At 67.9 m the
segment is inside the clamp; at 18.5 m its worst 8 m gradient is **0.4583** against
the 0.781 limit. Tie-break toward the LONGER reach when two reaches differ by less
than `APPROACH_G8_TIE = 0.02` (2.6% of the slide limit): gentler ramp, smaller
earthwork.

**One residual, accepted rather than forced** `[SAVE]`: the worst step at a building
is **-2.48 m**, and the two criteria move in OPPOSITE directions across the ladder -
the pad step goes 1.078 -> 2.063 m while the gradient goes 0.9219 -> 0.4583. No
approach length satisfies both, because the route crosses **six foundations whose
datums differ by 2.5 m over 17.7 m**. That needs a RE-ROUTE, not a regrade, and a
road through a terraced town having steps at building edges is honest.

**Prevention, demonstrated once**: on the harbour-city plan the junction's
carriageway datum was **pinned to 39.608** before grading; the unpinned profile
would have landed at **36.478**, a **3.130 m** step. Same defect as T4, caught in
the plan instead of after the cut. This is the only time in the record a known
failure was prevented rather than diagnosed - so pin the terminus datum FIRST.

### 4.6 Never resolve a terminus against `plan.json`

**Rule: a road, a pad or a body terminates on the DATUM of the thing it meets,
measured from WHAT IS STANDING - never on a planned coordinate, never on terrain
alone (§4.1).**

`plan.json` is the world GENERATOR'S REQUEST LIST: **12,301 entries, every one
reporting `placed: false`** `[SEED]`. It once put the nearest named location
**20-74 m** from clusters that sit **0.3-6.9 m** from a real placed marker
(`structures-and-settlements.md` §8.8 owns that measurement). It is not a map of
the world, and nothing in it is a datum.

Two instances from this build, one per artefact class:

- **A road terminus, MEASURED by `RoadsConnect` 2026-09-19** `[SAVE]`: Ulfsgard
  has **no land pad at all** - its only terrain write is the basin dredge - so
  its only datum is its quay: **367 distinct `wood_floor` tiles + 3 `wood_stair`,
  all at y = 30.503**, with the ground at the three stair feet reading
  30.852 / 30.683 / 31.089 at a live delta of exactly 0.000. A road aimed at the
  PLANNED city centre `(1000, 726)` would have arrived on open natural ground
  **18.1 m outside the city**. The terminus is the stair foot, and nothing else
  in that city is one. (Count discipline, because the first figure quoted was
  **387**: that is a ROW count over **20 co-located pairs**; a count-based
  postcondition at Ulfsgard pins **367** and expects 367 after a regeneration.
  The datum is unaffected either way - it is a height, not a tally.)
- **A pad, and the body anchored to it**: `wt-peak`'s pad was written to
  `target_y 102.31 m FROM PLAN.JSON` and the body was seated on the same number -
  **seq 545 / 546** `[SAVE]`. The structures-side consequence is
  `structures-and-settlements.md` §8.8; the rule is this one.

The price of getting this wrong is already quantified twice in this section: T4
terminating on a figure and ending **10.414 m** below Stenvik's floor (§4.1), and
the junction datum pinned to **39.608** against an unpinned **36.478** (§4.5) -
the one time the defect was prevented instead of diagnosed. **A planned
coordinate is a request. Pin the terminus to a measurement of what is standing,
and if nothing is standing yet, the terminus is not solvable yet.**

**And a datum composed from the LEDGER is not a measurement of the world once the
compiler is gone.** MEASURED 2026-09-19 at the R1 junction `(824, 738)`: the pin
of **39.608** was composed by `roads/applied.py` from blob deltas that are no
longer on any compiler, because zones (13,11) and (13,12) hold none (§1.5). The
live ground there is **generated 39.913 with zero delta** - **0.305 m** off at
the pin, and up to **-2.938 m** away from it where T8's blob owed the most. So a
road pinned on the ledger-composed surface arrives correctly at a datum the world
does not currently have. **Repair the compilers first, re-measure, then write** -
the ledger tells you what the surface SHOULD be, and only a live read tells you
what it IS.

---

## 5. Junctions and corridors

**Rule: two roads closer than the sum of their slope widths are ONE CORRIDOR, not
two roads with a gap. Grading them toward each other digs a ditch between them.**

The geometry, from the constants: a 0.781 plane off a 2 m wall needs 2.6 m per side
and off a 5 m wall 6.4 m, so two tapers plus two carriageways cannot fit in a 6 m
gap. The shared-corridor width is **29.0 m** (14.5 m per side).

MEASURED on T12 `[SAVE]`: T3 runs 6-10 m away near the temple with its carriageway
edge 1.18 m off its own centreline, so an ungated batter would **taper into a
finished road** - cutting up to 2.2 m out of T3's surface and leaving a trench
between the pair.

The gate, per ray (`ribbon.py`, the `road_claim_at` branch):

- if the two road profiles are within grade across the gap, the interstice is
  levelled **FLUSH at the higher of the two** (`action: levelled_flush`);
- if they are not, the interstice is **LEFT ALONE**, with the step recorded and both
  profiles named (`action: left_ungraded`, clip cause `shared_corridor`). An honest
  ditch beats a forced one, and the OUTSIDE batter is unaffected either way.

MEASURED outcome near the temple, and the operator was told rather than left to
discover it: **110 of 156 corridor rays are LEFT UNGRADED**, because the height
difference is **7.29 m across an 8 m gap** against **6.25 m** the grade can absorb.
One metre further out the gap is 10 m, the arithmetic passes, and it is levelled
flush - so a per-PAIR judgement would have had to pick one answer for both and
would have been wrong for one of them. The residual is a terrace with a real step
between an upper and a lower road; each road is independently walkable. Forcing it
means either an 8 m wall of fill or cutting into a finished carriageway. Building
staircases was explicitly forbidden: the operator asked for roads, not features.

### 5.1 The technique that answered the junction question: rank many, let the terrain answer

When a new road must join an existing network, do not pick a junction and validate
it. Rank many candidates and read the distribution.

MEASURED on the harbour-city plan, 2026-09-18:

```
62 candidate junctions ranked on T9/T8 within 260 m of the city
EVERY ONE came back with parallel stations - min separations 0.03-2.12 m
against the 29.0 m shared-corridor width
=> not 62 bad choices. ONE fact about the terrain: there is a SINGLE good
   corridor between the harbour and the network, and T9 already occupies it
```

So the new road **merges into the existing road rather than running beside it**.
Building a second ribbon alongside would have graded two batters toward each other
and dug exactly the ditch that already bit the temple. The junction was taken on T8
at (824, 738); the resulting legs measured:

```
R1  city -> junction on T8 at (824,738)   172.0 m, max grade 8.00%,
                                          cut/fill -1.35/+3.77 m, 0 crossings,
                                          2 shallow fords kept as terrain,
                                          0 of 86 stations in corridor conflict
R2  city -> village                       467.1 m, max grade 8.00%, 4 fords,
                                          min 106.77 m from any road
```

B hangs off A as ONE junction, not two: a direct junction from B would cost 395.6 m
more road plus a second junction geometry; via A, zero extra road.

Status note, so nobody looks for these roads in the world: **the harbour-city plan
was approved and NOT built** - as of 2026-09-18 it was holding with nothing placed,
pending the operator's keep-or-regenerate call. R1 and R2 are solved routes, not
`[SAVE]` facts. The 62-candidate result is a fact about the TERRAIN (`[SEED]`) and
about the existing carriageways' positions (`[SAVE]`), and it survives as a
technique regardless.

---

## 6. Keep-outs, and the ORDER they must be built in

**Rule: pads into the keep-out set BEFORE routing. Route before placing bodies.**

### 6.1 The router's keep-out knows nothing about your own buildings

`roads/poi.py` builds its forbid/penalty raster from the world-GENERATOR location
dump `[SEED]`. It has no idea what you have built. MEASURED, and the ledger proves
the ordering `[SAVE]`:

```
seq 213   2026-09-16T04:54:26Z  RoadBuild  terrain_write  road_T4_meadhall_stathub
seq 286   2026-09-16T05:53:21Z  SiteFinish terrain_write  stenvik_beehive_1
                                           merged_from: [121, 136, 213, 218]
```

The road wrote first; the town's pads wrote an hour later, and the pad record's own
`merged_from` names the road's seq. **The town was laid out on top of an
already-built road.** Result: **102 of our own pieces standing in T4's
carriageway** - the "circle of huts in the road" the operator found at Stenvik's far
end.

The repair was a RE-ROUTE, never a deletion (see section 7). MEASURED, 2026-09-17:
the re-route pulled **252 of our own building pieces and 22 mod-house pieces** out
of the carriageway, **keeping all 102 pieces of the hive intact with no building
deleted**; 4 carriageway pieces remain, recorded and accepted; T4 shortened from
**1525.1 m to 1363 m** and moved up to 73 m east. Live centreline is ledger seq
2053 / segments file line 2057; the old 1783/1779 reference is dead.

### 6.2 `pad_radius_m: 100.0` is a DISTRICT radius, not a footprint

MEASURED, and it is the same defect wearing three hats:

- For a watchtower or a castle the two readings agree: `wt-spawn`'s disc is 16.1 m
  against a 20x20 m pad; `hognest`'s 33.1 m against 44x44 m - the half-diagonal
  plus a margin.
- For a TOWN it is false, and `settlements/plan.py` says so in its own docstring:
  "taken from the district or the pad plus its own margin". `stenvik` and `vestvik`
  are `pad_radius_m: 100.0`.

Read as a footprint it produced:

```
293.9 m of T4's centreline written NOTHING
a -8.491 m step across the gap at (544.8, 809.2)
the trunk road into the town ending at a wall
```

The foundations it was standing in for are **15 per-building `site_pad`
rectangles**, worst half-extent **13.05 m** (`stenvik-hall-1`) - so the disc was
**7.7x** the thing it protected. Worse, it was in the wrong place:
`stenvik-house-3` at (606.5, 938.8) is **113 m from the district centre and OUTSIDE
the disc entirely**. Per-building pad radii actually in play: **26.1, 20.3, 16.0,
11.0 m**.

Third hat, the datum: the junction report used to find a gap's site by
`pad_radius_m` and quote that site's `pad_y`. For all six of T4's Stenvik gaps that
answered "stenvik, pad datum 45.57 m, pad_levelled_yet False" - a datum **no ground
in the town is at**. MEASURED: the gap at (584.3, 954.0) terminates on
`stenvik-beehive-1` seq 286, whose `target_y` is **47.11** and whose floor reads
**47.110** live - **1.54 m above the town figure**.

### 6.3 Build the keep-out by ASKING the write-time guard

Do not model the guard's geometry; ask it. `ribbon.pad_keepouts_from_chain` calls
`writer._pad_claims`, the same function `writer._pad_footprint_check` uses to refuse
a write at append time. Modelling it instead is how a closed-form shrink radius
**silently skipped 6 cylinders the gate itself passes**.

And **the rectangle is a TRIGGER, not the whole foundation.** A pad levels its
rectangle and FEATHERS PAST IT: MEASURED, `stenvik-stonehouse-1` seq 272 authored
**165 samples of which only 60 lie inside its 11.0 x 15.0 m rectangle**. The other
105 are caught by the per-sample `foreign` gate, which reads the AUTHOR of each
sample off the live blobs and is **blind to radii by design** - that is what caught
the original T4 clobber, which was OUTSIDE every circle. So:

```
authority         per-sample authorship   (applied.Applied.foreign_at)  [SAVE]
planning stand-off per-building rectangle + the pad's own levelled set  [SAVE]
fallback          the nominal disc, ONLY for a site with no live pad
```

A trap in the authorship gate, MEASURED and worth guarding: the portal hall's pad
had unioned S1's carriageway **BIT-IDENTICALLY** - sample (-300, 214) reads `-1.694`
in both blobs - so dropping S1's own records made the HALL look like the author, the
rasteriser refused **all 215 carriageway samples and stamped zero**, and the first
surface you walk onto off a portal became unrepairable. No name filter fixes that;
filter on actual authorship.

### 6.4 POI keep-out: use the measurement, never the declaration

Source `[SEED]`: `12,301 location instances, 177 types`, each carrying
`exteriorRadius`, `interiorRadius`, `clearArea`, `quantity`, `prioritized`,
`centerFirst`. Largest declared exteriorRadius **anywhere in the world is 32 m**
(`NorthVillage`, `bosslocation`, `GoblinKing`); smallest **3 m** (`Waymarker01`).
That order-of-magnitude spread is why a single global stand-off cleared **zero cells
out of 12,301**.

```
declared    = max(exteriorRadius, interiorRadius, znviewReachM)   MEASURED per type
ROAD_MARGIN_M = 6.0    [DESIGN]  a road is a 6 m ribbon passing by
PIECE_PAD_M   = 3.0    [DESIGN]  never level right up against a piece
half_width    = the ribbon's own half-width
```

`znviewReachM` is the furthest horizontal distance from a location prefab's root to
any child carrying a `ZNetView` - i.e. to the things that become ZDOs. MEASURED per
type over all 177 types: **12 of 177 reach past their declared radius, worst excess
+11.8 m (`TrollCave02`: declared 12, measured 35.8)**; largest reach anywhere
**35.77 m**.

**The rule is `max(declared, measured)` and NOT "prefer the measurement"**, because
**28 of 177 types carry <= 2 `ZNetView` children** (`WoodVillage1`, `NorthVillage`,
`FortressRuins` among them), so their measured reach is near zero - and "measured
beats declared" would have SHRUNK the stand-off around exactly the big village
locations most worth avoiding.

RETIRED constant, recorded so nobody re-derives it: an `OVERSHOOT_M = 11` was
`[INFERRED]` from ONE observation (a POI whose pieces reached 43 m against a 32 m
declared radius) and generalised to all 12,301 instances. It is now 0.0 and
superseded by the per-type measurement.

Protected test, accepted from Settlements: `prioritized or centerFirst or
quantity <= 5`. There is no sacredness flag on `ZoneLocation` - every field was
checked - and `unique` is FALSE even on `bosslocation`, so `unique` is **not** the
signal and must not be used. Two tiers: PROTECTED instances are a HARD forbid to the
full budget (the road bends); SCENERY is hard only to `declared + PIECE_PAD_M +
half_width` and a SOFT penalty beyond (a road passing a waymarker is thematic; a
road that undermines one is a defect).

**Feed the same number to the ROUTER, not only to the gate.** MEASURED: doing so
took a troll cave's radius from 26 m to 41.8 m, and the pathfinder simply went
around - **+149.6 m** of walking on one segment, **+97.1 m** on the other, and two
refusals disappeared for free. Two gates disagreeing cost two hours; two gates
sharing an input is the fix.

**Residual risk, stated in every return value rather than remembered:** the dump is
MOD-FREE by construction and cannot see `More_World_Locations_AIO`'s **~190 POI
types**. MEASURED: **10 of 18 Stenvik pads and 5 of 9 treehouse pads** held
furnished mod houses that had to be built around. A router planning against a
mod-free dump **will** plan through someone's house, so the only trustworthy check
is a LIVE marker sweep along the finished centreline after the zones are generated
(`network.py --marker-check`). The mod-free caveat is returned in every result
because a caveat that lives in somebody's head gets forgotten - this session lost
hours to exactly that.

---

## 7. Clearance: a road that only levels leaves trees standing in the carriageway

### 7.1 The forfeit nobody said out loud

The ribbon ran under a NON-DESTRUCTIVE clearance budget whose claim - "a road
ribbon emits only terrain writes, it deletes nothing" - is still true and is what
bought the loosened POI stand-off. What it FORFEITS: the ribbon levelled a 6 m paved
carriageway and a 1 m dirt shoulder **through standing forest and removed not one
trunk**. Every beech on the centreline was still there, standing in levelled ground.
The operator walked the roads and reported exactly that.

Two named proof objects on T12, MEASURED `[SAVE]`: a `FirTree_oldLog` buried
**2.11 m UNDER** the carriageway at (23.76, -1.57), and a `Beech1` floating
**3.15 m ABOVE** it at (96.3, -87.3). Both now answer "No objects found".

Independent proof that terrain writes move nothing: at zone (-18, 26), writing the
compiler moved no object at all - `Rock_4` n=12 sat at dY vs pad **-0.43
[-2.78, +4.58]** both before AND after the write.

So clearing is a SEPARATE op with a SEPARATE budget, and the ribbon's records still
say `destructive: false` and still mean it.

### 7.2 Explicit prefab list, never blanket removal

**`objects_remove id=*` is the command that deleted POI content in the pre-wipe
world and made the portal hub unclearable.** A prefab-filtered removal CANNOT delete
a building, a bridge or a furnished mod house, and that property is what makes a
clearing pass along 33 km of ribbon safe to run at all - these roads cross Stenvik's
streets, two bridge abutments and the hub ring.

MEASURED in this world's own corridors: at T12 s = 60 m the 12 m cylinder around the
centreline holds a `LocationProxy`, a `Music_MeadowsVillageFarm`, **12 `wood_floor`
and 14 `wood_roof_45`** - a furnished mod farmhouse ON the road. A blanket removal
there is the unrepairable class.

`roads/clear.py::CLEARABLE` is **56 explicit names**, every one OBSERVED in these 15
corridors by `objects_count id=*` rather than reasoned from a taxonomy. A prefab NOT
on the list is not assumed harmless: **its presence SKIPS the whole cylinder**
(default-deny, because a mod POI's pieces are ordinary building prefabs, so "I do not
recognise this" must mean "somebody built here").

Named refusals that look natural and are not: `RockDolmen_1/2`,
`Pickable_DolmenTreasure`, `TreasureChest_*`, `Pickable_ForestCrypt*`,
`Pickable_SurtlingCoreStand`, `LocationProxy`, every `Spawner_*`, `Beehive` ("it
FIGHTS BACK"), `piece_beehive`, `rock4_copper` (a deposit the operator mines),
`rock4_forest` (too big to be verge), `Music_*` ambience emitters.

One deliberate exemption, with its cost measured: **mobile creatures and their audio
emitters are neither clearable nor blockers.** On T12, **2 of 14 cylinders were
skipped whole - 37 trees, bushes and rocks left standing in the carriageway -
because ONE `Boar` was inside each at census time.** A boar cannot be deleted by a
prefab-scoped removal that does not name it, and its position is transient. They are
still RECORDED per cylinder: "ignored for the verdict" and "unmeasured" are different
claims and the ledger gets the first one.

**Four gates, each checked before a byte goes on the wire:** (1) the prefab filter;
(2) `settlements/clearance.py`'s per-type `max(exteriorRadius, interiorRadius,
znviewReachM)`, run destructively for this op; (3) any cylinder whose live census
holds a BUILDING piece is skipped WHOLE and its contents recorded - pieces are not
distinguished by owner, a bridge deck and a mod house are both "something I did not
put there"; (4) a LIVE `objects_count id=*` over THAT cylinder, in the SAME call, at
most seconds before the removal. Not minutes: **a census taken minutes earlier is a
census of a different world.**

### 7.3 Every removal is reversible by position

Measured over the whole Ulfsland ledger `[SAVE]`: **854 `objects_clear` records,
7,538 objects deleted, zero prefab names off the 56-name CLEARABLE list, and every
single deletion carries its prefab, ZDO id, world `xz`, `y` and `lat_m`** (854 of
854 records complete). That is what makes a removal reversible by position, and it
is why the ledger - 1,646 records at one mid-session checkpoint, 2,483 records at
head seq 2478 now - became the real artefact of the session. Most-removed
prefabs: `Beech_small1` 1092,
`Beech_small2` 1035, `Rock_4` 942, `Beech1` 861, `Pickable_Stone` 833, `Bush01` 802.

A bookkeeping trap, MEASURED: one agent derived its verified-removal set from its own
run output instead of the ledger. **918 of 3,291 objects matched across 90 cylinders,
764 of them removed by a PREVIOUS agent.** Trusting its own output would have
reported 764 already-cleared objects as still standing and mis-gated the entire
terrain write. Derive from the ledger.

### 7.4 A removal disc must SPLIT around blockers, never shrink concentrically

**This is the rule that pays for this whole section.**

```
MEASURED, at the exact cost of the ticket:
T12's disc at (77.58, -67.58) met a generated wood house 4.71 m from its centre,
SHRANK from 15.34 m to 3.84 m (ledger seq 937/952), removed 4 objects and left
14 standing - 13 of them then BURIED by this segment's own terrain write, worst
3.818 m. It recorded NO SKIP REASON, because nothing was skipped.
A SHRINK IS SILENT.
```

The hazard is the BLOCKER's own neighbourhood. Pulling the parent disc in
concentrically treats it as if it were the whole disc: a blocker near the centre
erases the disc, a blocker near the rim erases the rim.

So the disc is covered by SUB-DISCS, each:

- a strict SUBSET of the parent (so the over-reach fraction the parent's radius
  encodes is preserved exactly);
- WHOLLY outside every blocker's stand-off:
  `dist(sub_centre, blocker) >= sub_radius + BLOCKER_STANDOFF_M`, with
  `BLOCKER_STANDOFF_M = TERRAIN_SPREAD_M = 1.0`;
- centred on a TARGET, not on a geometric guess - a disc centred on the tree it
  exists to remove always reaches it, so the cover terminates with an explicit
  per-object reason rather than with a radius that happens to fall short.

Two precision details that are load-bearing:

- **Floor the radius to the sent precision, never round.** The wire carries `max=`
  at two decimals and `round` goes UP half the time, spending up to 5 mm of the
  stand-off that IS the guarantee. MEASURED before the floor existed: T12's worst
  emitted cylinder cleared its nearest blocker by **0.997 m against a 1.0 m
  stand-off** - the rounding, not the geometry, decided it.
- `SUBDISC_MIN_RADIUS_M = 0.05`. A cylinder's own centre object, rounded to two
  decimals, can sit up to `sqrt(2)*0.005 = 7.1 mm` outside a nominal radius of zero;
  5 cm is seven times that.
- `MAX_SUBDISCS_PER_TILE = 12`, bounded rather than trusted because the console sink
  **died at 160 such calls**. MEASURED: T12's worst blocked tile - the generated wood
  house beside the centreline at (72, -68), **35 non-clearable pieces inside a
  12.57 m disc** - is covered by 6.

**An object no sub-disc can reach is recorded PER OBJECT with the blocker that binds
it**, because "left standing" with no reason is what was reported to the operator as
fixed. And a split cannot help when the blocker has no position: `objects_count`
answers with counts only, so a cylinder whose live census holds an off-list prefab is
refused WHOLE and says so.

Measured clearing disposition on one pass: `cylinders_emitted 32` (of which 8 were
blocker-clear sub-discs), `cylinders_skipped_at_emit 28`, `cylinders_skipped_in_plan
19`, `cylinders_skipped_by_the_location_gate 0`.

### 7.5 The clearing width is derived, not chosen

A trunk is cleared if it satisfies EITHER criterion:

```
(a) IN THE CARRIAGEWAY   lat <= half + shoulder + TERRAIN_SPREAD_M
(b) ON THE EARTHWORK     lat <= edge + batter_run(station) + TERRAIN_SPREAD_M
    where batter_run = min(BATTER_MAX_M, |profile_y - terrain_y| / BATTER_GRADE)
```

`TERRAIN_SPREAD_M = 1.0` is MEASURED, not padding: `ribbon.delta_at` is non-zero up
to one full lattice pitch outside the written set, because `Heightmap` renders a mesh
that interpolates linearly between 1 m samples. **Ground moves out to exactly there
and no further**, and a tree whose ground moved is floating or buried. It doubles as
the trunk-lean allowance and is the only lean number on this host that is measured
rather than asserted - the trunk RADIUS is asset data in a compressed Unity bundle
and nothing here can read it, so the claim made is the one that can be checked.

Network-wide accepted residue, MEASURED `[SAVE]`: **268 trees remain inside the
cleared width**, every one inside a cylinder the safety gate skipped or split,
recorded with its reason, **with the ground graded AROUND it rather than under it**,
so none is buried or floating.

### 7.6 A building in a carriageway is a ROUTING failure

**Never delete a building to clear a road.** Road clearing has never removed one and
must not. A structure standing in a road is fixed by RE-ROUTING or RE-TERMINATING.
The proof this is workable is section 6.1: 252 of our pieces and 22 mod-house pieces
came out of T4's carriageway by moving the road 161 m, with zero deletions.

### 7.7 The census must be able to see a prefab nobody wrote down

The final corridor census was rebuilt to be blind to nothing:

```
DISJOINT 16 m cells on a global 16 m lattice that divides the zone grid exactly,
kept where the cell square intersects clear_half(station) + 1 m of any
non-bridge station. Per cell, ONE UNSCOPED `findObjects -near cx y cz 8` at each
of FIVE y levels (-32, -16, 0, +16, +32), NO PREFAB LIST.
```

Two measured reasons for that shape:

- **No prefab list**, so the census cannot be blind to the ~190 mod POI types
  `corridor_prefabs.json` cannot name.
- **The y stack**, because `findObjects -near` is a CUBE IN ALL THREE AXES: the old
  per-station census missed six `wood_beam_1` at T10 for exactly this reason. And
  the two geometries must never be confused - `findObjects -near x y z r` is a cube
  of half-extent r (MEASURED: at (90.8, -80.8) with r = 20 it answered with a trunk
  **21.9 m away in XZ** whose every axis offset was under 20), while
  `objects_count ... max=r` is an XZ CYLINDER with no height bound. Two geometries in
  one pipeline is how a census misses a corner, so every listing is re-filtered in XZ.

### 7.8 Answer the population the question named, and prove it blocks a walker

**Rule: a corridor number is worthless until two things are stated - WHICH
POPULATION it counts, and whether membership of that population actually
obstructs anything.** Both halves were got wrong on the same corridor in one
day, and both produced confident numbers.

**Error 1 - the wrong population entirely.** The figure "**184 objects left
standing**" was quoted in answer to an operator question about STRUCTURES in the
roads. It is a count of **CLEARABLE VEGETATION held back by a blocker** (§7.4):
structures were never in the clearance's scope at all, because a building piece
is not on `CLEARABLE` and its presence SKIPS the cylinder (§7.2). The number was
correct and the answer was not. Re-measured against the population actually
asked about `[SAVE]`:

```
non-clearable objects inside paved width + 1 m shoulder   5,318
  across bodies                                             122
  (334 of our own road lamps excluded - we put them there)
of those, pieces a walker must climb or go round            641
  across bodies                                              48
```

**Error 2 - "IN THE ROAD" is not "BLOCKS A WALKER".** Membership of the
carriageway band is a horizontal test; obstruction is a vertical one. Screened on
each prefab's own MEASURED y extent - which is invariant under yaw, so it costs
nothing to apply - **1,676 pieces reduce to 641** `[MEASURED]`:

```
bridge kerbs      tops sit 0.13 m BELOW their own deck   -> not an obstacle
roofs             above 2.2 m                            -> you walk under them
19 single posts   0.24 m                                 -> a step, not a wall
```

**And the raw ranking's top entries were the road itself.** **Nine of our own
bridge decks** led it - 123 / 98 / 83 / 82 / 82 pieces - and every one is the
deck a walker crosses on. A ranking that puts the bridge at the top of the
obstacle list is measuring presence, not passability.

The third population error of the same day is a PLACER error rather than a road
one - our own bodies carried as mod content, one of them a bridge held as a
keep-out - and it is stated once, with its numbers, in
`structures-and-settlements.md` §10.5, together with the hole in the clearable
allowlist that inflates a structure count.

---

## 8. Fords, bridges and ferries

### 8.1 The two ceilings that decide

```
free span         96 m    [MEASURED]  Crossings' absolute ceiling: 72 m free span
                                      at a 3x safety factor on an iron stringer
                                      under the deck, 96 m absolute, from its own
                                      implementation of WearNTear::UpdateSupport
pile reach       ~20 m    [MEASURED]  HardWood log piles, 140/10 at 0.1 vertical
ocean floor       30 m    [SEED]      the depth the seabed clamps to offshore
```

**Span under the ceiling -> bridge. Over it -> ferry.** And when BOTH ceilings are
exceeded the answer is a ferry with no argument: the east-isle crossing measures a
**223.65 m shore gap = 2.33x the 96 m ceiling**, over water that reaches the **30 m
ocean-floor clamp against ~20 m of pile reach**.

### 8.2 The reporting artefact that invented a 174.7 m run - a REFUSAL to inherit

For most of the session the ferry was justified by "a 174.7 m crossing exceeded the
96 m ceiling". **That figure is a reporting artefact and the code comment says so
explicitly** (`roads/route.py:605`):

```
Two narrow channels 16 m apart are one crossing; six narrow channels strung
along 200 m of broken shoreline are SIX crossings, and merging them reported a
single 200.6 m span that no bridge can reach across -- MEASURED on W1, plus
174.7 m on W4 and 114.8 m on T2, all three of which the search itself had
already proved crossable (it forbids water more than half the maximum span from
land, so every channel it used is under 96 m). The defect was purely in how the
runs were REPORTED.
```

The merge is now bounded: `merge_gap_m = 2*6.0 + 4.0 = 16.0 m` (two level runs plus
a working margin) AND `max_merged_m = 60.0 m`, so a merged run that stops being
buildable is not merged. **Do not quote 174.7 m as a span.** Three genuinely
different numbers were also being used interchangeably at the ferry and are worth
separating by name: shore gap **223.65 m**, pier-head to pier-head **187.65 m**,
berth-to-berth sail **196.82 m**. The sign's "223 m" was the shore gap and was
correct - it was just not the sail.

### 8.3 The length bound the geometry does NOT give you

The span ceiling is encoded geometrically (water further than half the maximum span
from land is untraversable), but that bounds a crossing's WIDTH, not its LENGTH: a
route running 200 m parallel to the coast 20 m offshore is inside the width bound at
every step and is still a 200 m bridge. MEASURED on three segments: **114.8 m,
174.7 m, 200.6 m.**

The fix is a **LAND-ONLY RETRY** rather than adding water-run length to the search
state (which would multiply a 640,000-cell grid by the run bound): re-route the
offending segment with water forbidden outright. If a land route exists it is the
right answer anyway; if none exists the rejection is a proof rather than a tuning
artefact. On W4 the land retry WON - a road that goes the long way costs nothing to
build, nothing to maintain and nothing from the bridge builder, and it beat both a
structure that cannot be built and a boat nobody asked for.

Recorded refusal in the spec, so nobody re-litigates it: **W1 is deliberately
absent.** Routed, it came out 346 m of which **330 m was a single bridged run** -
3.4x the 96 m ceiling. The loop closes INLAND instead, which is longer to walk and
is actually buildable.

### 8.4 Ford vs bridge is decided by DEPTH, not by length

```python
is_ford = (any_wet and need_fill <= CUT_FILL_MAX
           and (depth_m <= ford_max_depth_m or run_m <= ford_max_m))
# ford_max_depth_m = 1.0   ford_max_m = 8.0   ROAD_FREEBOARD_M = 1.0
```

The first version used a LENGTH cap alone (<= 8 m, Crossings' pier-bay spacing) and
was wrong in a way that showed immediately: a road hugging a beach crosses hundreds
of metres of ground sitting a few DECIMETRES below the water plane, and the length
cap turned that into **a 450 m "bridge" over a tidal flat** on the dock-to-watchtower
segment, plus **the entire 346 m west-isle link**. Neither is a bridge; both are
causeways, which are terrain.

The agreed ownership boundary, recorded once: **a FORD is TERRAIN and belongs to the
road; a BRIDGE is a structure and leaves the road's terrain entirely.** A bridge's
piles are protected footprints the road must grade AROUND, never through.

Classification must be by DOMINANT CAUSE, not by "was any station wet". Calling a
grade failure `water_body` produced a record describing **a 107 m "bridge" at y = 56
over dry hillside** - the wrong defect, and Crossings would have built the wrong
thing. The causes are `clamp_exceeded` (worst dry deviation past the usable clamp),
`water_body`, `chasm`.

Measured outcome over the 15 built segments `[SAVE]`: **11 fords kept as terrain**
(six of them on T9 alone) and **6 crossings handed over as bridges**. The two
built bridges:

```
S1   span 12.0 m   banks 30.72 / 30.64   bed 27.48   deck 30.6   bearing 270/90
S2   span 34.0 m   banks 30.26 / 30.02   bed 25.14   deck 30.6   bearing 298/118
```

A road's approach to a bridge deck legitimately writes terrain at the abutment, and
the price of the protection is recorded rather than smoothed: the ramp onto the S2
deck leaves a **0.37-0.44 m crest** because the last 1.5 m sits inside the bridge's
protected footprint. `BANK_LEVEL_M = 6.0` is the level run required at each abutment;
`DECK_CLEARANCE_M = 2.5` keeps a longship under the deck.

Bridge decks are 2 m tiles (`wood_floor` is a 2.000 x 0.130 x 2.000 collider solid),
so ribbon width per terminal is the DECK's width, not the road class default: a 6 m
trunk ribbon arriving at a 4 m jetty CREATES the pinch that matching widths avoid.

A ferry has TWO ends. `ferry-terminal-eastisle` was built with one, and the ledger
records the omission as deliberate rather than forgotten - seq 347 observe
(`wood_floor 0, wood_pole_log_4 0`, "structure_never_built"), seq 348 note ("road
segment S14-eastlobe-ferryeast validated but DELIBERATELY NOT BUILT because it
terminates at ferry-terminal-east, which has zero pieces in the world"). **A missing
terminal blocks a ROAD segment too.**

---

## 9. Road lighting

Operator's rule, verbatim: "the roads need to have lit lamposts. use the terrain to
pick a good spot where it is visible from both directions the farthest each within
30-60m of each other. the lamposts only really need to be in the forests or other
biomes with limited visibility. the meadows and other biomes with high visibility
they can be used sparringly."

That is a SITING rule with a spacing BOUND. A tool that divides the length by 45 and
stamps satisfies the bound and answers a different question.

### 9.1 The lamp, and why the brighter one loses

`piece_groundtorch` (Standing iron torch). Every claim MEASURED by
`roads/LampProbe.cs`:

```
LIGHT      one Light, type POINT, range 15.0 m, intensity 1.5,
           colour (1, 0.621, 0.482), local y +0.746
PIVOT      local y -0.65359 .. +0.82260; the RENDER MESH spans exactly the same
           as the collider, so the pivot is 0.654 m up the shaft
FLAME      surface + 0.65359 + 0.746 = 1.39959 m
PERSISTS   ZNetView present, m_persistent true - an ordinary piece ZDO
WEAR       WearNTear Wood, health 100, m_noSupportWear true, m_noRoofWear false
PIECE      m_notOnTiltingSurface = TRUE, m_notOnWood = FALSE
```

**`piece_hoodedlantern` LOSES despite double the range**, and this is the measurement
that decided it: it has NO `Fireplace` at all and a 30 m light whose aim is
`type Spot, spot_angle 30, aim_local [0, 0, 1]` - **a 30 degree cone fired
horizontally along the prefab's +Z, with only a 2 m omnidirectional component.**
A road is walked in BOTH directions, so 15 m of omnidirectional beats 30 m of
one-way wedge. Every other buildable lit candidate, for the record: bonfire 20 m
(and 4.1 m tall - not a lamppost), hearth 13 m, `piece_Lavalantern` 12 m point and
fuel-free but `m_notOnWood` and an Ashlands lava cage on a Meadows road,
`piece_walltorch` 12 m (needs a wall), the green/blue/mist standing torches 10 m,
`piece_dvergr_lantern_pole` 6 m with its pivot 3.0 m above its foot.

### 9.2 The +0.65359 m pivot offset, which is the portal defect at ten times the size

`piece_groundtorch`'s pivot is **0.654 m up the shaft**, so the visible foot is
0.654 m BELOW the pivot. **A lamp seated as though its pivot were its foot floats
0.654 m.**

The trap is that this is pure prefab authoring, not a rule: `CastleKit_groundtorch`
is the IDENTICAL MESH with its root at the foot (**-0.0516 .. 1.4246**), so the two
differ by **0.602 m of nothing but authoring**. `blueprints/base_geometry.py`'s
ground-resting guard lists `groundtorch` and then DROPS it because it fails the
pivot-at-base check - that module refusing to guess.

**Never restate the offset as a literal.** Ask the same box builder the placement
audit uses:

```python
def seat_offset_m(prefab=LAMP_PREFAB) -> float:
    box = fixtures.fixture_box(prefab, 0.0, 0.0, 0.0, 0.0)
    return -box[2]
```

Counter-example from the same session, for contrast: the `fb_fortress_*` kit has
`pivot_is_foot = true`, so applying the torch offset to it would be the same defect
mirrored - and that kit's tower has its **walkable platform at y 10.229 against a
15.402 m mesh AABB top**, a **5.173 m** gap that put beacons in mid-air. Seat on the
collider the player stands on, never on the mesh bound, and never on a constant
borrowed from a different prefab.

### 9.3 The everburning ZDO payload, in VANILLA

The prefab ships `m_infiniteFuel: false, m_startFuel: 2, m_maxFuel: 6,
m_secPerFuel: 20000, m_fuelItem: Resin` - about **11 hours** and then it wants resin,
once per lamp, forever. `TimedTorchesStayLit` v1.4.0 IS installed (DLL checked, not
merely its config - four orphaned configs were found in one session) and its
server-synced config sets `Metal Torch Stay Lit = true`, `Keep on when wet = true`
and a 17:00-05:30 timer. **That is good and it is NOT what the lamps rely on: a mod
is one uninstall away from 9 km of dark road plus a refuelling chore.**

So each lamp carries its own switch. MEASURED in `assembly_valheim` IL
(`ZNetView::LoadFields`): `Awake` calls `LoadFields`, which returns immediately
unless the ZDO holds bool `HasFields`; then per `MonoBehaviour` in the hierarchy it
requires bool `HasFields<TypeName>`; then it reflects that type's fields and reads
ZDO key `"<TypeName>.<fieldName>"` typed by the field.

```
HasFields                  = 1
HasFieldsFireplace         = 1
Fireplace.m_infiniteFuel   = 1
```

**All three go in the DataEntry INTS section, not a bool section**, because
`ZDO::GetBool(int32, bool)` is `ZDOExtraData::GetInt(...) != 0` (MEASURED in IL). A
bool section would be read as something else entirely. Each key is load-bearing:
`LoadFields` returns at once without `HasFields`, skips the component without
`HasFieldsFireplace`, and has nothing to assign without `Fireplace.m_infiniteFuel`.

**Round-trip the payload through the decoder before sending it.** `data_entry.decode`
is the read side of the same wire format the server uses, and **an unparseable
`data=` is accepted SILENTLY by `spawn_object`, which then places the piece with
default fields** - a dark torch that looks placed. The wire form, as appended:

```
spawn_object piece_groundtorch pos=-116.0801,-286.6039,33.4388 rot=63.287,0,0
             from=0,0,0 data=CAAAAANXEX/tAQAAALqdLvABAAAAlwxlWQEAAAA=
```

### 9.4 Siting: maximise `min(forward, backward)` against the APPLIED surface

The sightline in one direction is how far a walker gets before the applied surface
rises into the line between their eye and the flame. Both endpoints come off
`applied.Applied.surface_at` - the live union of the ledger's terrain blobs plus
generated height, bilinear - **not the planned profile**. MEASURED reason: **89 m of
T4's centreline sits on samples a settlement pad overwrote after the road was
written**, so the plan answers a question about the plan.

Visibility is the CONTIGUOUS run, not the farthest visible station:

```
blocked when  max over k in (i, j) of (surf[k] - flame) / (k - i)
              >= (surf[j] + EYE_M - flame) / t
```

A far hillside above an intervening ridge is genuinely visible but is not "a lamp in
view as you walk", so the run stops at the ridge. The running maximum makes this
O(cap) per direction instead of O(cap^2).

**The score is `min(forward, backward)`. MIN, not sum and not mean:** a lamp seen
80 m one way and 5 m the other lights one approach and ambushes the other.
Maximising the minimum is what puts lamps on crests and on the approaches to bends.

```
FLAME_ABOVE_SURFACE_M  = 0.65359 + 0.746   [MEASURED]
EYE_M                  = 1.7               [DESIGN] - no instrument on this host
                                           reads Player.m_eyePoint without a
                                           client, and inventing a measurement is
                                           worse than declaring a constant.
                                           `--eye-sweep` re-runs at 1.5 and 1.9 m
SIGHT_CAP_M            = 80.0              [DESIGN] a 1.5 m flame at 80 m is a
                                           pixel; candidates that HIT the cap are
                                           counted so a reader can see when the
                                           terrain, not the cap, stopped the answer
SIGHT_STEP_M           = 1.0               the lattice the terrain is written on -
                                           a coarser step steps over the ridge
                                           that does the blocking
SHOULDER_STANDOFF_M    = 0.55              puts the 0.23 x 0.24 m footprint wholly
                                           on the 1.0 m shoulder, clear air both
                                           sides; a cart uses the carriageway
FOOTPRINT_RELIEF_MAX_M = 0.15              refuse a spot whose relief across the
                                           lamp's own footprint exceeds this
```

The relief gate has independent corroboration found AFTER it was written, for a
different reason: `piece_groundtorch` is **`m_notOnTiltingSurface = True`**, so the
build menu's own rule for this exact prefab is "not on a slope" - and `spawn_object`
bypasses it, so a headless placement would put a torch on a bank the game would have
refused a player. MEASURED on S1, the 30.6 m ramp off the portal hall onto the bridge
deck, the worst footprint relief is **0.118 m**: a lamp there stands with 12 cm of its
base in the bank, which is fine.

(Recorded mistake of exactly the shape this tree keeps paying for: an earlier draft
said `m_notOnWood` was TRUE, on the strength of a hand-written column list that
omitted `distant` and so read `not_on_tilting` in the `not_on_wood` slot. Three
instruments - `LampProbe`, `crossings/data/piece_material.tsv` and a modded re-dump -
agree it is 0. **A display bug read as a measurement.**)

Biome density is READ per candidate from the same PatchScan zone patches the heights
come from (`heights.Patch.biomes` is `WorldGenerator.GetBiome` on the zone's own 1 m
lattice) `[SEED]` - not inferred from coordinates, not taken from the segment's name.
A biome the policy does not name is REFUSED rather than defaulted:

| biome | min / max gap | why |
|---|---|---|
| BlackForest, Swamp | 30 / 42 m | the road is a corridor with 5-10 m of visibility off it |
| Mistlands | 30 / 36 m | mist halves visibility again; tightest in the set |
| Mountain, DeepNorth | 40 / 60 m | open but broken relief and blizzards - the TERRAIN occludes |
| Meadows, Plains, AshLands | 55 / 90 m | "sparingly"; may exceed 60 m only where the lamp is measurably still in view of its neighbour |
| Ocean | 30 / 60 m | a road sample reading Ocean is a causeway or a ford |

Above 60 m the gap is allowed ONLY under an overlap condition: the lamp must be
visible BACKWARDS at least as far as the gap it just left, so the previous lamp is
still in view when this one comes into view. Without that, "sparingly" just means
"dark".

### 9.5 A lamp writes NO terrain

```
"flatten": "FORBIDDEN",
"flatten_reason": "lamps stand ON the road the segment already graded. A
 terrain_write here would re-level ground the road owns and re-open the clobber
 this network spent a night closing."
```

And note the guard that is NOT armed for this op: `writer._pad_footprint_check`
fires on `terrain_write` ONLY. A lamp writes no terrain, so nothing in `writer.py`
stops it landing on a foundation - which is why the keep-out is enforced inside
`lamps.py`, explicitly, against the same rectangles
(`ribbon.pad_keepouts_from_chain`, `ribbon.poi_standoff_discs`,
`ribbon.protected_piece_positions`, `applied.Applied.foreign_at`).

### 9.6 The measured outcome

Ledger seq 2168-2242, 2026-09-17T11:39-11:53Z, 15 `spawn_plan` + 15 `save` records
`[SAVE]`:

```
lamps placed                      133 across 15 segments
candidate stations REFUSED      2,024 (each recorded with its reason)
worst air under a lamp foot       0.0 m on all 15 segments
worst MID-SEGMENT dark run        8.0 m  (T4; every other segment 0.0 m)
worst dark run incl. segment tails 49.0 m (T13), 48.0 m (S7)
biomes                            Meadows 102, BlackForest 31
spacing achieved                  min 30.0 m, max 80.0 m, medians 36-80 m by biome
sightline min(fwd, bwd)           per-segment medians 11-80 m (the two 11 m and
                                  25 m medians are one-lamp segments S1 and S12);
                                  worst single lamp 1.0 m on T12
per-lamp postcondition            prefab_count piece_groundtorch == 1 within 1.5 m
```

Read the two dark-run numbers correctly: **the worst stretch of road with no lamp in
view, away from a segment end, is 8 m.** The 48-49 m figures are TAILS past the last
lamp on a segment, which the neighbouring segment's lamps light. Reporting the tail
as the road's worst gap would overstate the defect by 6x; reporting only the
mid-segment figure would hide a real gap at a segment boundary. Report both.

---

## 10. Procedure: laying a network on a fresh world, in order

Each step names its check. Do not advance on a step whose check has not passed.
`[SEED]` steps can be done before any world write; `[SAVE]` steps cannot be done
until the preceding write has happened and been measured.

### Step 0 - Accept or select the seed `[SEED]`

If a new seed is being generated, **continuous north/south walkable land is a seed
SELECTION criterion, measurable before any build starts**: rasterise the height
field, threshold at `c_WaterLevel`, and reject a seed whose largest north/south
walkable run is shorter than the corridor you want. `Pirate68` was chosen for a
measured **~7.9 km walkable corridor** toward the Deep North.

**Check:** the longest walkable run is measured and quoted with its threshold.

### Step 1 - Build the height/biome field, and audit the water test `[SEED]`

PatchScan `VHPATCH1` at 1 m with RIVERS INCLUDED, read with `numpy.frombuffer`
(the spawn landmass alone is 3,200 x 3,200 = 10.24 M samples; a Python list of
floats costs ~32 bytes each).

**Check:** `grep` every land/water comparison in every module that will consume this
field, and prove none of them compares a height against 0. Then sanity-check the
distribution: Ocean p99 and non-Ocean p1 must straddle 30, not 0.

### Step 2 - Measure the landmasses, and quote the resolution `[SEED]`

MEASURED sweep at `h > 30.5`, 8-connectivity, MAX-pooled decimation, on `Pirate68`:

```
region patch x[-4750,4750] z[-5000,4500]:  4 m -> 7,794 components
                                           8 m -> 1,991
                                          16 m ->   294
whole world:                              16 m -> 4,773
                                          32 m ->   824
                                          64 m ->   162
largest component: 5.4559 km2 at 16 m = spawn island (2.7754 at 1 m)
                   + west isle (2.6343), joined only by a 12.0 m and a 34 m channel
```

**Quote the cell size with any island count or do not quote the number** - the same
terrain reads as ONE landmass at 16 m and 7,794 at 4 m.

**Check:** every destination node is on the SAME component as its origin. Nodes that
are not get `NO_ROAD` with the reason recorded, not a silent drop. MEASURED
refusals on this world: `wteast` (component 224, 0.3955 km2 - routed anyway it needs
a **562 m bridge**), `ferry-terminal-eastisle` (component 273, 0.396 km2), `lheast`
(x = 2354 is outside the spawn island's own bbox x <= 1920), and the direct
dock-to-watchtower link (the dock's solved centre is over water, **7.0 m from a
0.031 km2 islet**, so a direct road needs **168 m of open water**).

### Step 3 - Survey the nodes `[SEED]`

- extremities: the farthest sample from the landmass centroid in each 45 deg sector
  **that is at least 25 m from any water edge**. The inland condition is the whole
  point: the farthest sample outright is a one-pixel spit, and **a road that ends on
  a spit ends in the sea**.
- abutments: Euclidean distance transform between the two land masks at 1 m, snapped
  to 2 m (the deck pitch). Then probe for the first DRY sample - MEASURED, Crossings'
  snapped bank (-324, -64) samples at **h 29.48** while the first land sample is
  x = -325 at **h 31.08**, so the ribbon starts there and the deck covers the extra
  metre.
- installations and spawn: from the placement solver / the location dump.

**Check:** every node carries a `provenance` string naming the instrument. Every
abutment carries `level_y = DECK_DATUM_Y`.

### Step 4 - Declare the settlements as PER-BUILDING rectangles, before routing `[SEED]`+`[DESIGN]`

**This is the ordering rule that section 6 exists for.** Feed BOTH the town/village
pads into the keep-out set BEFORE grading, and solve the route before any body is
placed.

**Check:** no keep-out in the set is a DISTRICT radius. For every site, compare the
declared `pad_radius_m` against the per-building rectangles; if they differ by more
than the half-diagonal plus a margin, the number is a district and must be replaced
by the rectangles. Record each site's intended datum (`pad_y` / `target_y`) as the
terminus datum for any road that arrives there.

### Step 5 - Build the POI keep-out raster `[SEED]`

`max(exteriorRadius, interiorRadius, znviewReachM) + ROAD_MARGIN_M + half_width` for
protected instances; `declared + PIECE_PAD_M + half_width` hard plus a soft penalty
to the full budget for scenery. Protected = `prioritized or centerFirst or
quantity <= 5`.

**Check:** the ROUTER and the clearance GATE read the same numbers (prove it by
construction, not by comparison). The mod-free caveat is returned in every result.

### Step 6 - Route, then fit the profile `[SEED]`

Phase 1: A* over cells against a SMOOTHED field (what the large-scale grade will
actually be), at the class grade with the 25% ceiling. Phase 2: fit the exact profile
against the raw 1 m field by **exact interval propagation** under the grade limit AND
the `+/-7 m` cut/fill box. Phase 2 is exact, so "this stretch needs a bridge" is a
PROOF, not an opinion. Relax the grade only down a recorded ladder
(class -> spur -> mountain -> ceiling).

**Check:** `over_clamp == 0` at plan time. Worst span <= 96 m; if not, run the
land-only retry and record `length_penalty_m`. Every unroutable edge is a recorded
FINDING with its error, never a silent omission - "why is there no road to the
watchtower" must have an answer in the file.

### Step 7 - Classify the crossings `[SEED]`

Ford (depth <= 1.0 m below the plane, fillable to `WATER_LEVEL + 1.0` inside the
clamp, at any length) vs bridge (anything deeper) vs chasm. Merge wet runs only
while the MERGED run stays buildable (`merge_gap_m = 16.0`, `max_merged_m = 60.0`).
Classify by DOMINANT cause.

**Check:** no crossing record describes a bridge over dry ground; no ford record
needs more fill than the usable clamp; no reported span is a merge of channels that
are individually crossable.

### Step 8 - Solve the junctions and PIN the termini `[SAVE]`+`[SEED]`

Rank many junction candidates on the existing network and read the distribution. If
every candidate returns parallel stations inside the 29.0 m shared-corridor width,
there is ONE corridor and the new road must MERGE into the existing one, not run
beside it.

**Check:** every terminus is pinned to the DATUM of the thing it meets - a pad's
levelled `target_y`, a deck's `DECK_DATUM_Y`, a junction carriageway's measured
height - BEFORE grading. Compute the unpinned profile too and report the difference;
if it is non-zero you have just prevented section 4's defect.

### Step 9 - Generate the zones `[SAVE]`

`zones_generate` before any terrain write or any census, always.

Two MEASURED reasons, one of them proven by deliberately breaking the rule:

- With no players online the zones have not been generated, so trees and rocks **do
  not exist as ZDOs** and a delete-only clearing step has nothing to delete.
  MEASURED on a sandbox of the same build: a fresh world had ghost-generated only 81
  zones, every `_ZoneCtrl` at (1000000, 0, 1000000), and **zero vegetation anywhere
  in the save**.
- A `_TerrainCompiler` written into an UNGENERATED zone is ignored at generation
  time. MEASURED on Ulfsland 2026-09-15, zone (-18, 28) at (-1152, 1792): a valid
  40x40 m flatten to y = 31.40 was spawned, then the zone was generated, and **all
  190 objects it planted inside the 30.28 m cylinder landed on the UNMODIFIED
  generator height** - median offset 0.000 m, worst 0.42 m - standing them up to
  **7.68 m above the finished pad**, as real ZDOs, permanently. The paint's
  vegetation-cleared alpha was ignored in the same breath: `Bush01`,
  `RaspberryBush`, `Beech1`, `Pickable_*` and eleven more were placed on ground
  painted alpha 0. **Paint alpha is NOT honoured on a zone that has never been
  generated.**

The cause, from IL: `Heightmap::Generate` ends by calling `ApplyModifiers`, which
finds its compiler through `TerrainComp::FindTerrainCompiler` - a scan of the static
`s_instances` list of INSTANTIATED components. A dedicated server with no peers
instantiates no `_TerrainCompiler` `ZNetView`, so the list is EMPTY and neither the
height deltas nor the cleared mask ever reach the heightmap. `SpawnZone` then runs
`PlaceLocations`, `PlaceVegetation` and `PlaceZoneCtrl` against that heightmap.

**Check:** probe for `_ZoneCtrl` within 1 m of each zone CENTRE and refuse to write
otherwise, before writing even the data-entry file - a stale entry in a watched data
directory is a loaded gun for the next run. `PlaceZoneCtrl` plants `_ZoneCtrl`
exactly at the zone centre, and `objects_count`'s `pos`/`max` filter is a vertical
cylinder on `Utils.DistanceXZ`, so a 1 m probe at the centre is an EXACT
generated/not-generated test. MEASURED that the refusal fires: a pad at (0, 0) in
ungenerated zone (0, 0) exited 1 and wrote nothing.

Also set the grass-reset fields, or the operator sees floating grass forever:
`operations = 2` (so a first load cannot equal `before + 1` and take the narrow
`ResetGrass` branch), `m_lastOpPoint` = the zone centre at pad height,
`m_lastOpRadius = 32`. MEASURED defect: with `operations = 1` and zeroed op point
and radius, every first load reset **a zero-sized box at the world origin**, and
because `m_lastDataRevision` starts at 0 on each newly instantiated component it
**recurred on every reload**.

### Step 10 - Write the ribbon `[SAVE]`

Carriageway (`paved_cleared`) + 1 m shoulder (`dirt_cleared`) + batter as a PLANE at
`BATTER_GRADE`, terminated at the sign flip, no paint. Densify zone selection to 1 m
between stations - at 2 m station spacing a ribbon that clips a zone corner between
two stations is missed, and **a missed zone is a GAP IN THE ROAD**. Union with any
earlier write to the same zone (one compiler per zone).

Refusal order per sample, cheapest-and-hardest first, because a sample inside a pad
is the pad's whatever else is true of it: `pad` -> `protected` -> `underwater` ->
`foreign` -> (batter only) `water_edge` -> `poi` -> `placed`.

**Check:** `over_clamp == 0` (refuse the whole segment otherwise);
`zone_ctrl` 1-of-1 in every zone; **0 carriageway or batter samples written where
`generated < 30.0`** - and read that as the ROAD ribbon's own gate (§2.4), NOT as a
physical prohibition on writing below the plane: a dredge writes 7 m below it
deliberately and the water follows it down (§2.1). Every refusal carries a
POSITION and a CAUSE - the counters say HOW MANY samples the road could not claim,
they cannot say WHERE the road therefore stops, and that is the only question a gap
report can be answered from. MEASURED why: 47.0 m of T4's centreline in zone (8, 7)
is refused because `tree-sth-2` seq 669 owns all 1,126 written samples in that zone,
and with no position recorded the walkability check could only call an EXPLAINED stop
a HOLE.

And re-assert cached inputs after their producer runs. MEASURED near-miss: a
root-owned temp directory made the terrain scan's clean step fail with "Permission
denied", so the scan never ran - and **the previous run's output file, existing and
non-empty, passed the check**. The new wider batter needed three zones that file did
not have. **An existence check is never a completeness check**, and writing a road
against stale ground heights does not make a bumpy road: it puts the road somewhere
else entirely, permanently, on a world someone is walking.

### Step 11 - Clear the corridor `[SAVE]`

`census` (per-cell, unscoped, five y levels) -> `plan` (one cylinder per tile,
split around blockers) -> `emit` (live `objects_count` per cylinder in the same
call). Explicit prefab list only.

**Check:** zero prefab names off `CLEARABLE` in any wire command; every deletion
records prefab, id, xz, y; every skip records its blocker; every object left standing
inside the derived clear width is named PER OBJECT with the blocker that binds it.
Derive the verified-removal set from the LEDGER, not from your own run output.

### Step 12 - Measure walkability on the APPLIED surface `[SAVE]`

Walk the centreline at 0.5 m; each station's height is the bilinear blend of the four
generated samples around it PLUS this write's bilinear delta (unwritten samples
contribute zero), which is how `Heightmap` composes the mesh.

**Check:** verdict over the 8 m baseline against 0.781 (report the 1 m figure
beside it, never instead of it); `holes == 0`; every gap has a cause AND a
`step_across_m` that someone actually reads; transverse earthwork p95 at or below the
design grade; `earthwork_over_grade_UNEXPLAINED` small and enumerated.

### Step 13 - Place the bodies, in the order the pad step demands `[SAVE]`

Per `tools/jumpstart/SITE_PREPARATION_ORDER.md`: generate -> clear -> flatten ->
structure -> loose objects. Clearing before or after the flatten is EQUIVALENT
(MEASURED on two pads 256 m apart, both left 2 objects - the `_TerrainCompiler` and
the `_ZoneCtrl` - because `objects_remove`'s filter is a vertical cylinder with NO
height bound, so an object 7 m in the air over a cut pad is exactly as removable as
one standing on it). Keep clearing first anyway, because that is where
`zones_generate` lives and the flatten step refuses to run until it has.

Datum: `placement_y = flatten_cost.target_y - blueprint_datum.base_y`, where
`base_y` is the blueprint's FLOOR PLANE measured from the game's own piece colliders.
A piece's PIVOT is not a surface - `stone_floor_2x2` is a 2 x 2 x 1 m solid pivoted at
mid-thickness. And `solved.y` is the HIGHEST terrain cell in the footprint, not the
pad: it is the raw ground drop from the site search and is not the altitude anything
should be placed at.

**Check:** no body's own local relief exceeds the +/-8 m clamp (if it does, change the
BODY, not the pad); and after placing, re-measure the road's arrival against the pad's
ACTUAL levelled datum, not its planned one.

### Step 14 - Light the roads, last `[SAVE]`

Lamps after the terrain is final, because siting reads the APPLIED surface.

**Check:** `flatten: FORBIDDEN` on every lamp record; worst air under a lamp foot
0.0 m; the everburning payload round-trips through the decoder before it is sent;
worst MID-SEGMENT dark run reported separately from segment tails; per-lamp
`prefab_count == 1` postcondition.

### Step 15 - Save, and verify what you claimed

**Check:** a `save` record after each write batch; live `objects_count` /
`prefab_count` postconditions matching each `spawn_plan`'s `expect`; and the two
proof objects a human can walk to - one thing that WAS buried and one that WAS
floating, both now answering "No objects found".

---

## 11. Quick reference: the numbers

```
c_WaterLevel                30.0 m      [ENGINE]
TerrainComp clamp          +/-8.0 m     [ENGINE]
usable clamp (planning)      7.0 m      [DESIGN] 1.0 m margin: lattice offset 0.71 m
slide angle / gradient    38 deg / 0.781 [ENGINE]
verdict baseline             8.0 m      [DESIGN]
DECK_DATUM_Y                30.6 m      [DESIGN]
ROAD_FREEBOARD_M             1.0 m      [DESIGN]
DECK_CLEARANCE_M             2.5 m      [DESIGN]
design grades      trunk 8% / spur 12% / mountain 18% / ceiling 25%   [DESIGN]
trunk / spur / mountain width      6.0 / 4.0 / 4.0 m                  [DESIGN]
SHOULDER_M                   1.0 m
BATTER_GRADE / BATTER_MAX_M  0.781 / 10.5 m
edge (trunk)                 4.0 m      half 3.0 + shoulder 1.0
half-corridor / corridor    14.5 / 29.0 m
EDGE_EPS_M                   1e-6 m
TERRAIN_SPREAD_M             1.0 m      [MEASURED] delta_at's bilinear reach
PROTECT_CLEAR_M / PLACED_CLEAR_M  1.0 / 1.0 m (Chebyshev)
BLOCKER_STANDOFF_M           1.0 m      SUBDISC_MIN_RADIUS_M 0.05 m
MAX_SUBDISCS_PER_TILE        12         (console sink died at 160 calls)
APPROACH_MIN_M / CAP         11.5 / 240.0 m   ladder 14 steps, tie 0.02
ford max depth / length      1.0 m / 8.0 m
crossing merge gap / max     16.0 m / 60.0 m
MAX_BRIDGE_SPAN_M           96.0 m      [MEASURED] 72 m free span at 3x safety
HardWood pile reach        ~20.0 m      [MEASURED]
zone size / lattice pitch   64.0 / 1.0 m, 65 samples, shared edge row
station spacing              2.0 m      snap 2.0 m
lamp light                  15.0 m point, intensity 1.5
lamp pivot offset            0.65359 m  flame at surface + 1.39959 m
lamp footprint relief max    0.15 m     (m_notOnTiltingSurface corroborates)
eye height                   1.7 m      [DESIGN]    sight cap 80.0 m [DESIGN]
```

## 12. Related documents

- `docs/proposed/2026-09-17-next-world-road-requirements.md` - the operator's
  requirements for the next world and the constraints they must live inside.
- `docs/proposed/2026-09-17-the-house-defect.md` - the failure taxonomy this
  document's defects are instances of: a check that answers a question it is not
  measuring.
- `tools/jumpstart/SITE_PREPARATION_ORDER.md` - the generate/clear/flatten/place
  contract, with the ordering rule proven by deliberately breaking it.
- `tools/jumpstart/roads/` - `spec.py` (nodes, edges, refusals), `field.py` (the
  1 m field), `route.py` (grade policy, crossings), `network.py` (routing, span
  ceiling), `ribbon.py` (the write, the batter, the approach solve), `clear.py`
  (clearance), `lamps.py` (lighting), `poi.py` (routing keep-out).
- `tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl` - 2,483 append-only records,
  head seq 2478;
  the authority for every `[SAVE]` number in this document.
