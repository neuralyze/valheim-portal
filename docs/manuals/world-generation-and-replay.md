# World generation and replay

How to choose a seed, stand a world up, and rebuild one from the ledger.

Written 2026-09-18 for an agent with no memory of the session that produced it.
Every rule below carries the measurement that produced it and the defect it
prevents. Where a technique was tried and failed, it is recorded as a refusal
with its numbers, because the failures cost more than the successes did.

Scope: this document owns **seed selection, world provisioning, and the
rebuild**. Terrain writing, structures, waterfront and the agent harness are
other manuals in this directory.

Instrument labels used throughout:

| Label | Meaning |
| --- | --- |
| `MEASURED` | a number a named instrument produced, with the instrument named |
| `INFERRED` | a number derived from IL, from one observation, or by argument |
| `UNMEASURED` | nobody has measured it; it is not estimated here |

---

## The regeneration contract: what a replay reproduces, and what it cannot repair

**Read this before proposing a rebuild.** It answers the operator's own question,
asked 2026-09-21: *"would these things be better to fix during a regeneration or
do we need to fix these first and document so that regeneration works
properly?"* The answer is **fix first**, and the reason is mechanical rather
than cautious.

`project.py` folds `terrain_write` **by zone and re-emits the RECORDED BLOB**. It
does **not** re-derive the surface from the design profile. So a replay
reproduces any defect whose bytes are in the record, perfectly and by design.

That is PROVEN rather than argued. The 286 m ghost road's values were laundered
into the SURVIVING record's blob by the union rule (`terrain-and-roads.md` §1.7),
and dropping the superseded records -- seqs **213 / 1693 / 1779** -- changed
NOTHING, because the bytes live in **seq 2053**. **Zones whose surviving blob
belongs to a retired write: 0 of 5.**

```
SURVIVES A REGENERATION   wrong-surface road grading (21 of 25 segments, up to
                          11.52 m off their own design); the 1.75 m pothole at
                          S1 (-299.0, 214.0); the four bridge abutments off
                          their decks by 0.84-2.45 m; 286 m of ghost road
FIXED BY A REGENERATION   only defects whose records COLLIDE at one key -- e.g.
                          the 52 duplicate pieces at the Ulfsgard boathouse,
                          and only because the fold key now includes y
UNAFFECTED EITHER WAY     structures in roads. Mod POIs regenerate from the same
                          seed into the same places; our own bodies replay to the
                          same coordinates. The same conflicts reappear.
```

**THE OPERATIVE RULE: WHAT SURVIVES A REGENERATION IS THE FIXES THAT LIVE IN THE
TOOLS, NOT THE FIXES THAT LIVE IN THE WORLD.** A world fix is bytes in a blob and
a replay re-sends them either way; a tool fix changes what the next emit
computes. Section 7 is the general form of this distinction; the worked example
is this session's own repair set, every item of which is re-earned by a rebuild
because none of it is data:

```
ribbon.py            PATCH_SURFACE = "heightmap" as a constant, plus the
                     SURFACE-KEYED patch cache (terrain-and-roads.md 1.6)
harbour_b.py         boathouse roofs emit 270 west / 90 east, so the panels
harbour_city.py      pitch away from the ridge (structures 3.8)
terminus_camp.py     ::pitch_sense, the SIGNED check ::cover could never be
                     (structures-and-settlements.md 3.8)
poi.py               polyline_dist + gate_m
writer.py            the flatten-guard census and its rewritten refusal
build.py             two verification radii rather than one
project.py           a 3D qkey, so the fold stops joining stacked pieces
```

**The sequence, plainly: fix the world, fix the tools, document, and only then
regenerate.** A regeneration is a way to re-run corrected tools over a clean
save. **It is not a repair mechanism**, and every time it has been proposed as
one the defect under discussion turned out to be in the record rather than in
the fold (5.5.4 gives the three-class model and the per-class discriminators).

---

## 0. The spine: SEED-DERIVED versus SAVE-DERIVED

Every fact about a world belongs to exactly one of two classes, and the entire
rebuild question is decided by which class a fact is in.

**SEED-DERIVED facts survive a regeneration.** They are functions of
`(seed, worldGenVersion, game build, registered mod set)` and nothing else.
Re-generate the world on the same seed and they come back identical.

| Fact | Produced by | Survives regeneration |
| --- | --- | --- |
| Generated height at any (x, z) | `WorldGenerator.GetHeight` | yes |
| Biome at any (x, z) | `WorldGenerator.GetBiome` | yes |
| River and lake beds | `WorldGenerator.Pregenerate` + `AddRivers` | yes |
| Where `ZoneSystem` will REQUEST a location | `ZoneSystem.GenerateLocations` | yes |
| Vegetation layout per zone | `PlaceVegetation`, RNG seeded `worldSeed + zoneX*4271 + zoneZ*9187 + prefabHash` | INFERRED (IL read by the clearing work; never proven by an actual regeneration) |
| A solved pad, road profile, harbour depth profile | our solvers, run against the above | yes |

**SAVE-DERIVED facts do not survive.** They exist only because something was
written into a particular `.db2`.

| Fact | Why it dies |
| --- | --- |
| A live `findObjects` census | it lists ZDOs in *this* save |
| An applied terrain delta | `TCData` is a delta on top of the generated height, stored in a `_TerrainCompiler` ZDO |
| A ZDO id | ids are `(server uid, counter)`, assigned at creation. MEASURED across the round-trip proof: ids 1115-1118 became 1119-1122 for byte-identical objects; a later de-duplication pass measured `23163:1 -> 22958:1` and `76580:1 -> 76288:1`, and "the offset isn't even constant" |
| A portal PAIRING | `Game::FindRandomUnconnectedPortal` re-draws the pairing on every load; the ledger can guarantee a tag has exactly two ends, never which two share a connection hash |
| Which zones are generated / explored | save state |
| Anything a player did | loot, hoeing, damage, wards, map pins |

**Consequence, and this is the whole reason the project survives a wipe:** a
survey solved against the *generated* field can be re-run against a fresh world
of the same seed and gets the same answer. A survey solved against a *live
census* is worthless the moment the save is replaced.

**Consequence for provenance discipline:** when an agent reports a number, the
first question is which class it is in. A worked example from this session, in
an agent's own words at 2026-09-18T03:01Z, of a correctly-classified holding:

> EVERY number I currently hold is generator- or disk-derived and survives a
> restart. Provenance: (a) a sandboxed PatchScan `VHPATCH1` raster of the
> GENERATED height+biome field for seed `Pirate68`, run against a mirror copy of
> the server install with the live BepInEx excluded; (b) the world seed read from
> `_main.302.fwl2` on disk; (c) the append-only ledger; (d) `roads/spec.py
> SETTLEMENT_SITES`; (e) the mod-free locscan dump. [...] The one thing I
> deliberately have NOT measured yet, precisely because it can only come from the
> live world, is the `LocationProxy` marker set near both sites -- which decides
> my keep-out. That is my first post-restart action, not a pre-restart one.

That is the shape to copy: seed-derived work first and offline, save-derived
probes last and live.

---

## 1. Seed selection as a MEASUREMENT

### 1.1 The operator's requirement

Stated 2026-09-17 after walking the Ulfsland build
(`docs/proposed/2026-09-17-next-world-road-requirements.md`):

1. A **main north/south trunk road the full length of the centre continent**, so
   a player can walk almost all the way to the Deep North without a portal.
2. Mountain passes **FOUND, not carved**. Swamps **skirted, not crossed**.
3. A **dock at the northernmost reachable point**, with a tall lighthouse, as the
   terminus of the trunk road.

Requirement 2 is not taste; it is forced by an engine limit. Terrain writes clamp
at **+/-8 m against the generated height** (`tcdata.CLAMP_M`). Any crossing
needing more than 8 m of cut is *unbuildable as terrain*. So "find the pass" is
the only implementation of "there is a road over that mountain".

### 1.2 The criterion, computable before a single piece is placed

> **Rasterise the GENERATED height field. Threshold at the water plane
> `y = 30.0`. Take the 8-connected components of the WALKABLE mask. The seed's
> score is the north-south extent of the component containing spawn. Reject any
> seed whose extent, plus the extent unlocked by crossings under the bridge
> ceiling, is shorter than the corridor required.**

Written out, with every constant carrying its source:

```python
WATER      = 30.0   # ZoneSystem::c_WaterLevel -- a `static literal float32(30.)`
                    # in assembly_valheim.dll 1.0.12.  MEASURED by disassembly.
FREEBOARD  = 0.5    # a sample AT y=30.0 is awash; do not call it land.
SLIDE_DEG  = 38.0   # Character::GetSlideAngle returns literal 38 for IsPlayer
                    # (45 mounted, 90 monster).  A player on ground steeper than
                    # this SLIDES.  Absolute ceiling, not a design target.

land     = h > WATER + FREEBOARD
walkable = land & (slope_deg(h) <= SLIDE_DEG)
label, n = scipy.ndimage.label(walkable, structure=np.ones((3, 3), bool))
# score = (max row - min row) * step, for the component containing spawn
```

`h` is a `roads/field.py` `Field.h` array straight out of a `VHPATCH1` raster
(section 2.4), and `slope_deg` is `Field.slope_deg()` -- `np.gradient` over the
step, `degrees(arctan(hypot(gx, gz)))`. **Provenance for every "MEASURED today"
figure in 1.2, 1.3 and 1.6:** that recipe, run as a standalone script in ONE
invocation against the rasters `/tmp/harbour/world.bin:world16` (step 16 m,
centre (0,0), half 4800 m) and `/tmp/settle/h1m.bin:mainland` (step 1 m, centre
(798,600), half 1600 m), both PatchScan `Pirate68` output with rivers
pregenerated. Those `/tmp` files are not durable; the recipe above is, and
re-running it costs one `run_patchscan.sh` invocation.

Three details that are the difference between a real criterion and a plausible
one:

- **`h > c_WaterLevel`, NEVER `h > 0`.** The `h > 0` form classifies the entire
  seabed as dry land. It is how two witness pads were sited **12 m under water**
  and quoted at the operator four times before anyone checked. On `Pirate68`, the
  Ocean biome's height p99 is 3.88 and the non-Ocean p1 is 8.11, so the heights
  are absolute world y and 30 is the shoreline -- not an offset, not a sea floor.
- **Connectivity, not per-column runs.** MEASURED today on `Pirate68` at 16 m
  over the central 9.6 km square: the largest walkable component spans
  **3,648 m** north-south, while the naive "longest unbroken column of walkable
  cells" tops out at **2,800 m**. The naive metric under-reads by 848 m (23%),
  because a road is allowed to deviate laterally and a column is not.
- **The slope mask barely changes the answer, and that is worth knowing.** Same
  raster: land is 31.7% of cells, walkable is 29.2%. The largest component's NS
  extent is 3,648 m under both masks. Slope removes *area* (5.442 -> 5.303 km2)
  and not *reach*. Keep the slope term anyway; it is free, and it is what stops a
  cliff face counting as a corridor.

### 1.3 Resolution: what to sample at, proven

Land connectivity is resolution-stable, unlike water connectivity. MEASURED
today, **same window** (the 1 m `mainland` patch, centre (798, 600), half 1600 m,
block-decimated by taking the centre sample of each coarse cell -- i.e. exactly
what a coarser generator grid would have produced):

| step | cells | walkable components | longest NS walkable extent |
| --- | --- | --- | --- |
| 1 m | 10,240,000 | 2,013 | 3,066 m |
| 2 m | 2,560,000 | 1,106 | 3,064 m |
| 4 m | 640,000 | 642 | 3,060 m |
| 8 m | 160,000 | 296 | 3,072 m |
| 16 m | 40,000 | 121 | 3,056 m |
| 32 m | 10,000 | 38 | 3,104 m |

Spread across a 32x resolution range: **48 m, or 1.6%.** So **measure seeds at
32 m**; it costs 1/1024 of the samples and answers the same question. This
reproduces, with our own instrument and a controlled window, the `tools/seedscan`
finding that `dn_walk_path` moved only 8,129 -> 8,116 m between 32 m and 16 m
sampling on `Pirate68`.

The **component COUNT is not resolution-stable and must never be quoted bare.**
Same terrain, same threshold, same 8-connectivity:

```
region patch x[-4750,4750] z[-5000,4500]:   4 m -> 7,794 components
                                            8 m -> 1,991
                                           16 m ->   294
whole world:                               16 m -> 4,773
                                           32 m ->   824
                                           64 m ->   162
```

**Quote the resolution with any island count or do not quote the number.** The
same terrain reads as one landmass at 16 m and 7,794 at 4 m.

Two metrics that are NOT stable and must not be used to rank seeds:

- **`dn_sail` (water route) is resolution-unstable.** Same seed `8JiFcknsJd`, the
  same route: 4,922 m at 128 m spacing, 12,397 m at 64 m, 27,444 m at 32 m.
  Finer sampling opens new channels *and* resolves new blocking land.
- **`dn_walkable` as a boolean is worthless.** True in 29 of 30 curated seeds.
  Valheim's world rim is near-continuous land (88-91% land coverage in the
  9,600-10,000 m radius band) and Ashlands wraps the south, so nearly every
  continent touches every other somewhere. Only the geodesic distance
  discriminates -- and a "walkable" route may cross Ashlands, which is not a
  route a player can use.

### 1.4 What a re-roll cannot buy: reject these criteria before you spend on them

MEASURED over **1,001 random seeds plus 30 curated community seeds = 1,031
seeds, zero exceptions**, by `tools/seedscan` at `STEP=32 HEIGHT=1`:

- **Deep North proximity is fixed geometry.** `WorldGenerator.IsDeepnorth(x, y)`
  is *seed-independent*: `magnitude(x, y + 4000) > 12000 + 100 * sin(20 *
  atan2(x, y))`. Minimising radius subject to that gives **exactly 8,000 m due
  north**. Over 1,001 seeds `dn_near` ranged only **7,908-8,100 m**; over the 30
  curated seeds `dn_reach_near` spanned 7,910-8,298 m. All 1,001/1,001 had Deep
  North water-reachable from the spawn sea.
- **Deep North content is fixed.** Every one of the 30 curated seeds had all 17
  Deep North location types and 992-999 DN instances (30 `DN_gammeltrollFrac01`,
  40 `morkborg`, 40 `icepond`, 40 `thehole01`, ~45 `NorthVillage`).
- **"All three traders on the starting island" is not a differentiator.** 25 of
  30 seeds had all three trader camps and all five early boss altars on the spawn
  landmass, furthest trader 2,946-4,762 m in *every* seed. The game places 10
  camps each for Haldor, Hildir and the Bog Witch.
- **Biome radius gates are seed-independent.** Swamp needs `dist > 2000`
  (`minMarshDistance`); Plains `> 3000 + 100*sin(20*theta)`; Mistlands
  `> 6000 + 100*sin(20*theta)`; Black Forest `> 600 + 100*sin(20*theta)`. No seed
  puts Swamp inside 2 km of spawn. Across all 14 finalists the 2 km disc held
  Meadows, Black Forest and Mountain and **zero** Swamp or Plains cells.
- Mistlands' and Ashlands' nearest water-connected coasts are near-constants
  (~5.9 km, ~7.9-8.1 km).

**What DOES vary, over the 30 curated seeds:**

| metric | range | why it matters |
| --- | --- | --- |
| `dn_walk_path` | 8,129 - 27,604 m | the land route north; **3.4x spread** |
| furthest early boss on the spawn landmass, on foot | 3,125 - 6,785 m | early progression travel |
| `spawn_coast_dist` | 182 - 3,007 m | how soon a boat is useful |
| `spawn_land_km2` | 33 - 228 km2 | buildable home continent |
| `dn_biggest_km2` | 2.7 - 7.8 km2 | largest single DN landmass |

**The land corridor is the only prize a re-roll buys.** Best case improved the
Deep North trip 9,132 -> 8,242 m: 10% on a journey made once. The *route quality*
is what moved 3.4x.

### 1.5 `Pirate68`, measured

`Pirate68` was chosen on its corridor, and the figure the requirements document
records is **"a measured ~7.9 km walkable corridor toward the Deep North"**.
Unpack it, because it is a rounding of two different quantities and a future
session must not conflate them:

| Quantity | Value | Instrument |
| --- | --- | --- |
| Deep North inner edge due north (seed-independent) | **7,908 m** min over 1,001 seeds; 8,000 m exactly by the closed form | `WorldGenerator.IsDeepnorth`, IL |
| `Pirate68` straight-line to nearest reachable DN | 8,031 m | seedscan, 32 m |
| `Pirate68` **land route** `dn_walk_path` | **8,129 m** at 32 m; **8,116 m** at 16 m | seedscan 8-connected land graph |
| detour factor | **1.01** | the two above |
| biome sequence along that route | Meadows -> Black Forest -> Swamp -> Plains -> Mistlands -> Mountain -> Deep North, **no Ashlands** | seedscan |
| control `8JiFcknsJd` for comparison | 24,338 m, 39% of it beach-hugging | seedscan |

A detour factor of 1.01 over 8 km is a near-perfect northern corridor and is
exactly what the community claim about that seed said. It is also the one thing
the measurement agreed with the blog post about.

**Trade-off, measured and worth stating at selection time:** a continuous land
bridge north blocks the sea lane. `Pirate68`'s water route is the *worst* of the
finalists (15,161 m at 16 m sampling) precisely because its land corridor is in
the way. You cannot have both.

### 1.6 The corridor is not one landmass, and the criterion must say so

This is the correction a future seed selection most needs, and it was measured
today rather than assumed.

MEASURED, `Pirate68`, PatchScan raster `world16` (step 16 m, window
x,z in [-4800, +4800], river-inclusive), walkable mask as in 1.2:

```
sample nearest world origin (0,0): index (300,300), h = 70.08 m, component 852
spawn component: 20,715 cells = 5.303 km2,
                 z -936 .. +2712  (NS extent 3,648 m)
northernmost walkable z on the spawn continent: +2712 m
Deep North inner edge due north: 8000 m
shortfall on foot, with no crossing:            5,288 m
```

So `dn_walk_path = 8,129 m` and "the spawn continent stops at z = +2712" are
**both true**. The 8,129 m figure is an 8-connected route over the *whole world*
at 32 m, which hops isthmuses and rim land that a 16 m raster of the central
square resolves as separate components. A trunk road is not a lattice path: it
needs a buildable crossing at every gap.

**So the acceptance criterion has a second half: what does each crossing buy.**
Measured by joining components separated by at most `2r` metres (binary dilation
by `r`) and re-asking how far north the spawn component reaches:

| r | gap joined | north reach | gain | joined component |
| --- | --- | --- | --- | --- |
| 0 m | 0 m | +2,712 m | +0 m | 5.303 km2 |
| 16 m | <= 32 m | **>= +4,792 m** | **+2,080 m** | 9.202 km2 |
| 32 m | <= 64 m | >= +4,792 m | +2,080 m | 10.115 km2 |
| 64 m | <= 128 m | >= +4,792 m | +2,080 m | 11.836 km2 |
| 96 m | <= 192 m | >= +4,792 m | +2,080 m | 21.762 km2 |
| 256 m | <= 512 m | >= +4,792 m | +2,080 m | 79.937 km2 |

Read it carefully, with its two caveats:

- **`+4,792 m` is the raster edge**, not a shoreline. The window is
  `half = 4800 m`, so the true reach is `>= +4,792 m` and the measurement is
  TRUNCATED. Requesting a larger patch is the fix; it was not run here because it
  boots a sandboxed server and nothing in this document depends on the exact
  value.
- **Dilation thickens land as well as joining it**, so each row is an UPPER
  BOUND: it proves a gap of that width exists on the route, not that one bridge
  of that span suffices.

What the table does prove, and it is decision-grade: **the first 32 m crossing is
worth 2,080 m of corridor, and nothing wider buys any more reach inside this
window.** That is a 32 m bridge against a measured structural ceiling of ~96 m
span. It is the cheapest 2 km in the world.

This matches what Ulfsland actually is. From `roads/spec.py`, MEASURED by
`roads/archipelago.py`:

> The largest land component in the entire world on seed `Pirate68` is
> **5.4559 km2 at 16 m cells**, bounding box x[-1520, 1904] z[-928, 2720] [...]
> that box is exactly the union of spawn island (x[-326, 1920] z[-938, 2128],
> **2.7754 km2** at 1 m) and west isle (x[-1534, 12] z[-322, 2700],
> **2.6343 km2** at 1 m). The two read as ONE landmass at 16 m because the only
> thing between them is a **12.0 m channel and a 34 m channel**, both MEASURED at
> 1 m -- and both now bridged.

Cross-check: my land-mask run today gives that component as **5.442 km2** against
their 5.4559 km2 -- two independent runs, different code, 0.26% apart.

### 1.7 The acceptance procedure

```
1. Generate candidate seed strings with the game's own alphabet:
     tools/seedscan/gen_seeds.py 1000 --rng <n> --include <control> > /tmp/seeds.txt
   Always include a CONTROL seed so every result is comparable.

2. Terrain scan, ~1 s/seed at 32 m:
     VH_SRC=<server install with BepInEx>  SEEDS=/tmp/seeds.txt \
     OUT=/tmp/seedscan/out  STEP=32 HEIGHT=1  tools/seedscan/run_scan.sh
   HEIGHT=1 is REQUIRED.  Without it `GetBiome` tests IsAshlands BEFORE its
   water test and the whole southern sea reports AshLands.

3. Rank on the land route, never the sail:
     tools/seedscan/analyze.py /tmp/seedscan/out --top 20 --walk

4. Apply the gate of 1.2 to the shortlist, and the crossing table of 1.6 to
   the survivors.  Stated as a decidable threshold for the operator's
   requirement ("walk almost all the way to the Deep North"):

     REACH  = northernmost walkable z reachable from spawn, using only
              crossings whose span is <= 96 m (the measured bridge ceiling)
     GATE   = REACH >= 8000 - TOLERANCE
     CROSS  = number of such crossings the route needs

   8000 m is `IsDeepnorth`'s inner edge and is seed-independent (1.4).
   TOLERANCE and the acceptable CROSS are the OPERATOR'S call, not a
   solver's -- record the two numbers he picks in the authorising document
   BEFORE the scan, so a seed cannot be talked past the gate afterwards.
   For calibration: `Pirate68` measures REACH +2712 m with CROSS=0, and
   >= +4792 m with CROSS=1 at a 32 m span (1.6, truncated by the raster
   window -- widen the patch before quoting a final REACH).

5. Placed content, ~60 s/seed, only for the shortlist:
     VH_SRC=... SEEDS=... OUT=/tmp/seedscan/loc  tools/seedscan/run_locscan.sh
     tools/seedscan/analyze_loc.py /tmp/seedscan/loc --detail <seed>

6. Record the ACCEPTED seed with its measured corridor, in the document that
   authorises the build.  Not in a transcript.
```

Two refusals that belong in step 3 and 5 respectively:

- **`STEP=64` and `STEP=128` mis-report `spawn_coast_dist`.** Beach cells (Ocean
  biome, height above 0) sever narrow channels, the world sea fragments and spawn
  looks inland. MEASURED on 14 finalists: 32 m and 16 m agreed within 10 m, while
  64 m inflated it to a floor of ~1,005 m on 8 of the 14. Use `STEP=32` or finer
  for anything touching water connectivity.
- **`analyze.py` assumes spawn is (0,0); `analyze_loc.py` does not.** The real
  start temple is wherever `ZoneSystem` put it. MEASURED on `8JiFcknsJd`,
  `StartTemple` sits at (61.0, 196.8), i.e. **206 m** from the origin. Where the
  two disagree, trust `analyze_loc.py`.

### 1.8 Where the seed lives on disk

A Valheim 1.0 world save is a **directory**, not a pair. MEASURED on Ulfsland
2026-09-12 and again today:

```
<VALHEIM_ROOT>/<World>/config_merged/worlds_local/<World>/
    _main.<N>.fwl2       world metadata      253 bytes   <- the seed is here
    _main.<N>.db2        the world database
    _main.<N>.chunks     the chunk index
    _main.<N>.ok         4 bytes: int32 41, the world version
    NN_NN__N_N.chunk     one file per saved zone
```

`_main.<N>.fwl2` is a `ZPackage`: `int32 payload length`, `int32 version`,
`string name`, `string seedName`, `int32 seed`, `int64 uid`,
`int32 worldGenVersion`, with 7-bit-encoded string lengths.

MEASURED today by parsing the newest `.fwl2` directly:

```
file            _main.307.fwl2   253 bytes
sha256          8833746a3f1299d3fd22191056479280a1f24b589ed160a4671680ce394effc1
world_version   41
name            Ulfsland
seed_name       Pirate68
seed            147627509
uid             2212643513
world_gen_version 2
chunk files     22
```

**`<N>` is a generation counter that increments on every save.** The ledger
header was written against `_main.1.fwl2`; the seed was read from
`_main.302.fwl2` on 2026-09-18; today it is `_main.307.fwl2`. **Never hardcode
the generation.** Take the newest `*.fwl2`, exactly as `hostops/lib/common.sh`'s
`resolve_world_save` does.

This is also why `replay.py`'s drift check deliberately does **not** compare
`fwl_sha256` (recorded as `c5fdbf8f...` for generation 1, which no longer
matches anything on disk) and instead compares `world`, `seed_name`, `seed`,
`world_gen_version`, `world_version`, `game_build_anchor`, the mod digest and the
modifier set. A digest over a file the server rewrites on every save is not an
identity.

---

## 2. Rastering the generated field: PatchScan and LocScan

### 2.1 Why it must run inside the game

`WorldGenerator.GetBiome` depends on `UnityEngine.Mathf.PerlinNoise`, and the
generator's per-seed offsets come from `UnityEngine.Random.InitState` /
`Random.Range`. Both are **native internal calls implemented in
`UnityPlayer.so`**. MEASURED:

| Host | Result |
| --- | --- |
| stock `mono` + the game's managed DLLs | `cant resolve internal call to "UnityEngine.Mathf::PerlinNoise"` -> `MissingMethodException` |
| Unity Doorstop entrypoint (`Doorstop.Entrypoint.Start`) | same failure -- it runs *before* Unity registers its icalls |
| **BepInEx plugin `Awake()`** | **works** -- runs on the Unity main thread after the player is initialised |

So there is no reimplementation and no third-party seed-map site. Every
seed-derived number in this project comes out of the game's own generator.

### 2.2 The sandbox, which is what makes it safe

`tools/jumpstart/blueprints/run_patchscan.sh` (and its sibling
`tools/seedscan/run_scan.sh`) never write to the live install:

```
rsync -a --exclude 'BepInEx/' --exclude '*.pdf' "$VH_SRC/" "$SANDBOX/"
rm -rf "$SANDBOX/BepInEx"
mkdir -p "$SANDBOX/BepInEx/plugins" "$SANDBOX/BepInEx/config" "$SANDBOX/BepInEx/patchers"
cp -a "$VH_SRC/BepInEx/core" "$SANDBOX/BepInEx/core"
mcs ... -out:"$SANDBOX/BepInEx/plugins/PatchScan.dll" "$HERE/PatchScan.cs"
./valheim_server.x86_64 -nographics -batchmode \
  -name patchscan -port 3457 -world PatchScanScratch -password patchscan1 \
  -savedir "$SANDBOX/scratch-save"
```

The three properties that matter, stated so nobody weakens them:

1. **The live `BepInEx/` is EXCLUDED from the mirror and the core is copied back
   in alone.** Exactly one plugin runs. The live world's 120 mods cannot
   influence a generated-height reading, and a mod cannot write anything.
2. **Its own port (3457, vs the live 2456/2458) and its own `-savedir`.** It
   creates a throwaway world named `PatchScanScratch`.
3. **Validated by OUTPUT, never by exit status.** The plugin `SIGKILL`s the
   process once the file is flushed, so a success is always `raw_exit=137`. The
   script tests `[ -s "$OUT" ]`.

Confirmed clean for a whole 1,001-seed sweep: "Zero files modified under
`Ulfsland/data` (install), `Ulfsland/mods`, or `Ulfsland/valheim.env` in the last
4 hours; zero under Hrafnheim, Doggerland, Storgard, Vangard."

### 2.3 The two planes, and why there are two

MEASURED against a 1 m ground truth over 2,048 x 2,048 m of `Pirate68` -- the
fraction of windows a coarse grid calls flat that really ARE flat:

| footprint / max_flat | 8 m grid | 4 m grid | 2 m grid |
| --- | --- | --- | --- |
| 32 m / 2.5 m | 0.18 | 0.44 | 0.62 |
| 60 m / 3.0 m | 0.74 | 0.82 | 0.83 |
| 70 m / 3.5 m | 0.83 | 0.90 | 0.90 |
| 80 m / 4.0 m | 0.85 | 0.94 | 0.95 |

Sampling the whole 21 km world at 1 m is 440 M generator calls. So: **search
coarse, decide fine.**

| criterion | resolution needed | served by |
| --- | --- | --- |
| flatness | 1 m | `VHPATCH1` patch, footprint re-slid to the flattest 1 m offset |
| biome purity | 1 m | `VHPATCH1` patch (coarse grid pre-filters only) |
| location clearance | exact | KD-tree against the `ZoneSystem` dump |
| water distance | ~8 m | 8 m EDT; the coastal test threshold is 120 m |
| overland routing | 32 m | 32 m land graph |

**The coarse grid has no rivers.** `run_scan.sh` defaults `SEEDSCAN_PREGEN=0` and
`WorldGenerator.AddRivers` reads the dictionary `Pregenerate()` fills.
`run_patchscan.sh` always pregenerates. This is not academic: MEASURED with a
fresh 1 m river-inclusive PatchScan (2.08 M samples), the operator's spawn at
(-2187, 33, 2100) sits on terrain **26.74 = 3.26 m BELOW the water plane** --
which is literally why they were swimming -- while the river-free 8 m grid
reports 30.17 there. **10 of 13 solved base sites were below the water plane**,
the worst at -15.8 m. Use the coarse grid to search. Never to decide.

Skipping `Pregenerate()` for BIOMES is safe and is the reason the search plane
can: biome grids for `8JiFcknsJd` at 128 m are byte-identical with and without
it, and with heights the river data changes 5,964 cell heights by up to 76 m
while changing the water mask on **zero** cells.

### 2.4 The `VHPATCH1` format and its sample geometry

One reader per container, both parsing the same bytes:
`tools/jumpstart/terraform/heights.py` (Python lists, right for a 65x65 pad =
4,225 samples) and `tools/jumpstart/roads/field.py` (`numpy.frombuffer`
zero-copy, required for a 3,200 x 3,200 = 10.24 M sample island).

```
magic   b"VHPATCH1"
int32   seed hash
int32   name length, then the name
int32   patch count
  per patch: int32 id length, id bytes,
             float32 cx, cz, half, step,
             int32 n,
             n*n float32 heights (little-endian),
             n*n uint8 biomes
```

Sample geometry, from `PatchScan.cs`, identical in both readers:

```
x = cx - half + j*step + step/2
z = cz - half + i*step + step/2      row-major, i outer (z ascending)
```

Request a patch at `half = 32.5, step = 1` centred on a zone centre and its
65x65 lattice lands **exactly** on that zone's `TerrainComp` sample centres
(`patch j == mask x`, `patch i == mask y`). That alignment is what makes a
terrain delta computable offline.

Four independent agreements confirming the plane is the same plane the game
builds on -- PatchScan bilinear at the meadows placement centre (241.5, 353.5) =
**77.700 m** vs an independently solved `placements.yaml` `y_centre_m` =
**77.72**; generated spread over the 24 x 24 m footprint **2.105** vs **2.12**;
computed cut/fill **-0.888 / +1.217** vs recorded **0.89 / 1.23**.

### 2.5 LocScan: where locations will be requested

`WorldGenerator` does not know where anything is. Boss altars, traders, dungeons,
runestones and the start temple are placed by `ZoneSystem`, a separate
rejection-sampling pass that needs the location *prefabs* loaded from the asset
bundles and runs as a Unity coroutine. That is why `LocScan.cs` has to let the
server actually boot a world, and why it costs ~60 s a seed instead of ~1 s.

What it gives you that nothing else does: the per-type `exteriorRadius`,
`interiorRadius`, `clearArea`, `quantity`, `prioritized` and `centerFirst` from
the game's own `ZoneLocation` data. That is the only source for a per-type
stand-off.

What it does **not** give you is the subject of the next section.

---

## 3. `plan.json` is the generator's REQUEST list, not the world

**This is the single most expensive misreading in the project's history and it
looks completely reasonable until you check it.**

MEASURED: the `locations_dump` carries **12,301 entries and EVERY ONE reports
`placed: false`** -- including `StartTemple`, which demonstrably exists. The flag
means "the prefab has been instantiated in a loaded zone", which only happens
once a player visits. Positions are final regardless; the flag is not a
placement record.

**What resolving against it cost.** MEASURED: it put the nearest named location
**20-74 m** from clusters that were actually sitting **0.3-6.9 m** from a real
placed marker -- i.e. it answered LOOSE for eleven mod houses that were right
there.

**Only a live probe is authoritative for POSITION:**

```
findObjects -prefab LocationProxy -near <x> <y> <z> <r>
```

and the reason is that the dump is **mod-free**. `More_World_Locations_AIO`
contributes ~190 POI types that no seed-derived dump can see. MEASURED at one
site: **60 of 65 live `LocationProxy` markers were modded POI invisible to the
dump.** A build sited off the mod-free dump would have put buildings inside sixty
invisible things. MEASURED at a different site over 24 zones, four live markers:

```
(-1081.88, 1792.22)  WoodHouse2, d = 0.00 m  -> identified, radius 8 m
(-1146.27, 1797.76)  nearest at 15.16 m      -> unidentified, worst case
(-1150.16, 1853.63)  nearest at 29.69 m      -> unidentified
(-1223.76, 1777.60)  nearest at 71.77 m      -> unidentified
```

One in four matched **exactly** -- which is why the identity tolerance is 0.5 m
and not a search radius.

**The authority ordering, which is the rule to copy:**

> The LIVE markers decide what has to be respected. The mod-free dump is used
> ONLY to identify a live marker by exact position and thereby earn it a SMALLER
> stand-off. A marker the dump cannot identify gets the world's worst case.
> An unidentified marker must never be treated as harmless.

The worst case is taken from the dump's own maximum -- **32 m is the largest
declared `exteriorRadius` among the 12,301 instances** -- rather than a
hand-picked number. Stand-off, with provenance per term, because one of three
numbers is a measurement and two are not:

```
standoff = max(exteriorRadius, interiorRadius)   MEASURED per type, from ZoneLocation
         + 11 m piece overshoot                  INFERRED from ONE observation
         + 15 m safety margin                    a taste pick
```

The 11 m is INFERRED from a single observation: a location declaring a 32 m
exterior radius was measured with pieces **43 m** from its marker. It is one
observation generalised to 177 location types and it is labelled INFERRED
precisely so nobody later reads it as a fact. Per-type MEASURED reach differs
from the declaration by up to 3x (`TrollCave02` declares 12 m, measures 35.8 m).
**Use the measurement, not the declaration, and never a single global
stand-off.**

**The defect this prevents**, measured on Ulfsland 2026-09-15 at zone (-18, 28):
a 40 x 40 m flatten cut up to **6.65 m** of ground out from under a
`LocationProxy` holding a `TreasureChest_meadows_buried`, and reported nothing.
Same class as the POI damage recorded as unrepairable in the pre-wipe world.

**And a corollary that catches the other direction:** demanding a whole district
be empty of all 12,301 instances **clears ZERO cells anywhere in the world** --
the constraint is unsatisfiable, so a naive clearance solver silently produces an
empty plan rather than an error.

### 3.1 The 118 unplaced registrations

MEASURED world-wide on Ulfsland: **118 unplaced-location registrations exist, and
any zone holding one cannot be spawned by the game at all.** A player walking
into such a zone walks into a zone that never spawns -- not into a cave.

The repair, and the one-zone worked example:

- The fault is precise: a zone that IS in the save's generated set and holds no
  `_ZoneCtrl`.
- `zones_generate` can never fix it. Upgrade World's `ZonesGenerate` sets
  `TargetZones = Ungenerated` and `RestoreZones` sets `TargetZones = Generated`,
  so `Zones.GetZones` hands them **disjoint** candidate sets. MEASURED on
  Ulfsland: `zones_restore pos=2368,-3328 max=1` answered *"0 zones: 448 skipped
  by the command"* -- 448 being the whole generated set, distance-filtered to
  nothing -- which is the cleanest possible proof that zone (37,-52) was NOT in
  that set.
- The repair used was: mark the zone generated **without spawning anything**
  (Upgrade World's `empty` flag -- `Generate.ExecuteZone` does
  `m_generatedZones.Add(zone)` and returns), then `zones_restore` to plant the
  missing `_ZoneCtrl`. Measured 0 -> 1 with a probe disc strictly inside the
  zone.
- **The declared cost:** that zone permanently forfeits its ungenerated content.
  Its vegetation and any location registered there will never be placed. This is
  why the ledger schema requires a `fault` field on both ops -- the MEASURED
  reason ordinary generation cannot be used. An op that mutates a zone on a hunch
  is a guess.
- **The probe radius is 31.9 m, not 32.** A 64 m zone square has a 32 m
  half-extent, so a 32 m disc touches the edge and counts a neighbour's control
  object -- which is how a check comes to confidently answer a question it is not
  measuring. `schema.ZONE_INSIDE_MAX_R_M = 31.9`.

---

## 4. World provisioning and lifecycle

### 4.1 Where a world lives

```
$VALHEIM_ROOT/                              (one directory per world + world_backups)
  <World>/
    valheim.env                             compose env for this world
    data/bepinex/                           the server install (VH_SRC for scanners)
    mods/.game-build-anchor                 the validated game build digest
    config_merged/
      bepinex/plugins/                      120 DLLs on Ulfsland
      bepinex/org.tristan.rcon.cfg          RCON config
      worlds_local/<World>/                 the save directory (section 1.8)
  world_backups/                            world-<World>-<label>-<ts>.tgz
```

On this host, `VALHEIM_ROOT=/media/big4/projects/game/valheim`.

**There is deliberately no default for the root, and the refusal is load-bearing.**
`hostops/lib/common.sh: require_valheim_root` accepts `VALHEIM_ROOT`,
`AGENT_WORLD_ROOT` or `VALHEIM_WORLD_ROOT`, in that order, and exits 78 with a
message naming the variable if none is set. From its own comment:

> Every one of these scripts used to hardcode the original author's absolute
> path, which meant the installer's documented `VALHEIM_WORLD_ROOT` was silently
> ignored and backup, list, delete and restore operated on a directory that does
> not exist on anyone else's machine. A loud failure naming the variable is the
> only safe behaviour for scripts that delete and overwrite world data.

It also refuses a relative path (`exit 78`) and a non-directory (`exit 78`).

### 4.2 Standing a world up on a CHOSEN seed

**A dedicated server takes no seed argument.** Handing it a fabricated `.fwl`
loses the seed *silently*: `World.LoadWorld` runs `World.CheckDbFile()`, and a
`.fwl` whose `.db` is missing is not a world at all, so `World.GetCreateWorld`
calls `World.GenerateSeed()` and overwrites the `.fwl` with a fresh random seed.

The fix is the `NeuralyzeWorldSeed` BepInEx plugin, which patches
`GenerateSeed`, so the game creates the world itself -- correct seed, pristine
database, nothing stale.

```
hostops/portal_create_valheim_world.sh <World> <Seed>
  -> tools/valheim_worldgen.py <World> <Seed>
```

Order of operations inside `recreate()`, and every line of it is a scar:

```
world_root = preflight(world, seed)          # EVERY precondition, before anything moves
run_script("stop_valheim_server.sh", world)
archive = archive_world_files(world_root)    # the previous save is ARCHIVED, never deleted
deploy_seed_plugin(world_root, seed)         # writes neuralyze.worldseed.cfg + the DLL
run_script("start_valheim_server.sh", world)
```

- **`preflight` runs before the stop**, because on 2026-09-14 the missing-plugin
  refusal fired AFTER the stop and AFTER the archive: the run left the world down
  with an empty `worlds_local` and its only copy inside
  `world-archive-20260914T085408-worldgen`. Preflight also checks
  `start_valheim_server.sh` exists, not just `stop` -- discovering that one
  missing after the stop strands the world down.
- **There is no success check and no rollback.** Three different success checks
  were wrong -- a `.db` that Valheim does not write for twenty minutes, a stop
  five seconds after start, and `"Game server connected"`, which is a Steam event
  that fires before the world even loads. **Each wrong check then triggered a
  rollback that destroyed a world which had in fact generated perfectly, on the
  correct seed.** Nothing is deleted, so there is no state to roll back to. Use
  `valheim_worldgen.py <World> --status` to inspect; being read-only it can be
  wrong for free.
- **World building takes several minutes.** Do not measure it at five seconds.

### 4.3 The restart nobody predicts, and the readiness probe

MEASURED by `GroundTruth`: `ZNet::ServerLoadWorld` branches on
`World::IsChunkedSave()`, and a world **created this session** has
`m_chunkedSave` false -- so creation takes `ZNet::LoadOldWorld`, ValheimRcon's
Harmony finalizer on `ZNet.LoadWorld` **never fires**, its listener never starts,
and TCP 2458 is absent from `/proc/net/tcp` entirely. Stopping the server writes
the real chunked save (`_main.N.db2` + `.chunks` + `.fwl2`) and the next boot
takes the chunked path and binds.

> **RULE: after creating a world from scratch, restart the server once before
> expecting RCON.**

It reads as "the server is wedged" and the diagnosis was got wrong twice before
it was measured.

**The proof of readiness is a successful `players` probe** -- a reply proves the
main thread ran a frame. **Never a log read:** the heartbeat period is 600 s, so
the log cannot distinguish "quiet" from "dead" inside ten minutes.

### 4.4 A directory that is not a world will hang the server forever

MEASURED 2026-09-12: a directory parked inside `worlds_local` as
`Ulfsland.halfcreated-192409` left the 1.0 server hanging in the start scene
forever. Chainloader finished, the log repeated *"Waiting for server to listen on
UDP query port 2470"* and *"NullReferenceException: The WorldGenerator instance
was null at `Heightmap.OnEnable`"*, and *"Zonesystem Awake"* never appeared.
Moving the directory out of `worlds_local` fixed it immediately. **No log line
named the cause.**

> **RULE: put nothing in `worlds_local` except a world.** Staging goes in
> `config_merged/.portal-restore.XXXXXX` -- same filesystem, so a rename into
> place stays atomic, and not scanned for worlds.

### 4.5 Backup

```
export VALHEIM_ROOT=/media/big4/projects/game/valheim     # or it refuses, exit 78
hostops/backup_valheim_world.sh <World> <label>
```

It prints the archive path. Shape:
`$VALHEIM_ROOT/world_backups/world-<World>-<label>-YYYY-MM-DD_HH-MM-SS.tgz`.
`<label>` must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` because
`restore_valheim_world.sh` parses it back out.

Both save layouts are detected **per world and never assumed**: a 1.0 world is
one directory member holding a `*.fwl2`; a 0.220.x world is the two members
`<stem>.db` + `<stem>.fwl`. Both sides of the test are positive -- "not a pair"
must not imply 1.0, because `worlds_local` also holds the game's own rolling
`<World>_backup_auto-*` copies, which under 1.0 are *also* directories.

**`require_readable_world_save` runs BEFORE the archive file exists.** MEASURED
as the real service user on 2026-09-12: `tar` streams as it goes, so a save the
user cannot open leaves a **122-byte stub holding only the directory entry** --
and that stub is then the NEWEST archive for the world. The script also `rm -f`s
the archive if `tar` fails mid-stream, for the same reason.

### 4.6 Verify a backup by its MEMBERS, never by its exit code

This is a specific instance of a rule the project paid for five times over:
`update --apply` returned `rc=0 / updated=1` while the pin never moved, because
the download timed out and the failure was swallowed.

> **RULE: verify the artefact, never the exit code.**

For a backup that means listing what is actually inside it:

```bash
A=$(ls -1t "$VALHEIM_ROOT/world_backups/world-<World>-<label>-"*.tgz | head -1)
tar -tzf "$A" | wc -l                          # member count
tar -tzf "$A" | grep -c '\.chunk$'             # one per saved zone
tar -tzf "$A" | grep -E '\.(fwl2|db2|chunks|ok)$'
stat -c '%s bytes  %y' "$A"
```

Compare the `.chunk` count against the live save. MEASURED on Ulfsland today:
**22 `.chunk` files**, plus exactly one each of `_main.307.{fwl2,db2,chunks,ok}`,
9,147,356 bytes total.

This is not hypothetical. On 2026-09-13 two Ulfsland archives
(`world-Ulfsland-backup-2026-09-12_19-47-02.tgz` and `...19-47-17.tgz`) were
found to contain nothing, and were quarantined into
`world_backups/.quarantine-empty-archives/` rather than deleted, so the evidence
survived. Both had been produced by commands that exited zero.

### 4.7 Restore

```
hostops/restore_valheim_world.sh <World> <archive-basename>
```

Its validation, in order: the archive's members must all sit under a single
top-level directory named for this world (own casing or lowercase), no member may
contain `..`, and at least one must be a `*.fwl2`. Then:

- Extract into the private staging dir (4.4).
- **Ownership and modes come from the live `worlds_local`, not from the
  archive.** MEASURED 2026-09-12: the game writes 0775/0664. A 1.0 world whose
  directory the container user cannot write is a world that **silently stops
  saving** -- the server creates `_main.<N+1>.db2` inside that directory on every
  save.
- The outgoing save is **moved OUT** of `worlds_local` into the staging dir, never
  renamed aside inside it (4.4).

### 4.8 Teardown

```
hostops/stop_valheim_server.sh <World>       # graceful; writes the chunked save
hostops/remove_valheim_server.sh <World>     # docker compose rm -v
hostops/portal_delete_valheim_server.sh      # full removal
```

`stop` is not optional before anything that touches the save directory: the
chunked save is written on stop, and a save read mid-rewrite is the mid-stream
`tar` failure of 4.5.

---

## 5. THE REBUILD-FROM-LEDGER PLAYBOOK

### 5.1 What the ledger is

`tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl` -- **2,483 records**,
2026-09-16T02:28:11Z to 2026-09-18T03:52:20Z, append-only, hash-chained, one JSON
object per line, UTF-8, no embedded newlines.

```json
{"seq": 1,
 "ts": "2026-09-16T02:00:00Z",
 "actor": "RoadNet",
 "op": "terrain_write",
 "params": {...},          // values AS SENT, not as intended
 "wire": ["deleteObjects -zone ...", "spawn_object _TerrainCompiler ..."],
 "requires": {"mods": [...], "prefabs": [...], "blobs": ["<sha256>"]},
 "expect": {...},          // a cheap postcondition, MANDATORY when mutating
 "meta": {...},            // free-form, unverified, for humans
 "prev": "<sha256 of the previous line's canonical bytes>"}
```

`wire` is the literal console command strings. Blobs -- emitted plan files,
`TCData` byte arrays, patchscan lattices -- live in `blobs/<sha256>` beside the
ledger, content-addressed.

Op histogram, MEASURED today over all 2,483 records:

| op | count | mutating | idempotent |
| --- | --- | --- | --- |
| `objects_clear` | 854 | yes | yes |
| `observe` | 488 | no | n/a |
| `zones_generate` | 242 | yes | yes |
| `spawn` | 185 | yes | **guard** |
| `retire` | 173 | yes | yes |
| `spawn_plan` | 158 | yes | **guard** |
| `save` | 123 | yes | yes |
| `terrain_write` | 100 | yes | yes |
| `note` | 97 | no | n/a |
| `portal` | 59 | yes | **guard** |
| `zones_restore` | 2 | yes | yes |
| `world_manifest` | 1 | no | n/a |
| `chain_reset` | 1 | no | n/a |

24 distinct actors. Top: `RoadLast` 503, `OutlierBuild` 374, `NorthLight` 219,
`RoadEmit` 205, `SeatCheck` 169, `RoadDone` 154, `SettleBuild` 130,
`RoadClean` 125, `RoadBuild` 109, `WaterfrontRun` 108.

### 5.2 Why you must NOT replay it as a history

`replay.py` replays the ledger record by record, in file order, postcondition by
postcondition. **That is the right instrument for resuming an interrupted build
and the wrong one for rebuilding a world**, because the history contains its own
mistakes followed by its own repairs:

- a lighthouse built to **272 of 1,255 pieces** and then retired whole,
- **18 portal arches** placed, retired, and re-placed inside a hall,
- **764 objects** cleared and then cleared again,
- a road graded, re-graded and re-battered **four times**,
- **64 duplicate pieces** found and de-duplicated,
- a **Longship** berthed and then retired as aground.

Replaying that literally repeats twenty hours of defects.

### 5.3 The PROJECTION

`tools/jumpstart/ledger/project.py` folds the history to **final state**: for
each `(prefab, world position)` it decides what exists NOW after every retire,
clear, zone restore and re-place, and emits only that.

```bash
tools/jumpstart/ledger/project.py fold        --root runs/Ulfsland --out manifest.json
tools/jumpstart/ledger/project.py census-plan --root runs/Ulfsland --reach 24
```

MEASURED today, re-running the fold over all 2,483 records:

```
placements                      15,683      (of 28,501 spawn commands issued)
distinct_prefabs                   136
terrain_zones                      191      (559 superseded writes dropped)
clears                             832      (22 superseded)
zones_generated                    205
zones_restored                       2
portal tag groups                   21      = 20 real tags + "<untagged>"
vessels                              6
ZDO text payloads                  368
collapsed_duplicate_commands    12,299
retire_removed                     407
retire_removed_fuzzy                 2
retire_matched_nothing               5
clear_removed_placements           110
```

All 20 real tags carry **exactly 2 ends**: `u-castle`, `u-lheast`, `u-lhsouth`,
`u-lhwest`, `u-stenvik`, `u-sudrberg`, `u-treenear`, `u-treesth`, `u-treewest`,
`u-vestvik`, `u-wteast`, `u-wtnorth`, `u-wtpeak`, `u-wtsouth`, `u-wtspawn`,
`u-wttown`, `x-ferry-e`, `x-harbour`, `x-north`, `x-vestvik`. Verified by
**subtracting retirements**, not by counting records.

#### 5.3.1 The key MUST be `prefab` + WORLD POSITION, never ZDO id -- and the code's key is not that

Not-ZDO-id is deliberate, and the ledger says why: ZDO ids are reassigned on
load (section 0). An id-keyed fold would treat one object as two across any
restart boundary, and the ledger spans many. Position survives. Quantum is 1 mm
(`QUANT = 1000.0`); the wire carries 4 decimals and two distinct pieces are
never closer than centimetres.

**What the implementation actually does is NOT what this heading promised, and
the gap is a 36% demolition.** MEASURED 2026-09-19: `project.py:97` folds on
`qkey(prefab, u, v)` -- prefab plus HORIZONTAL position, **y omitted** -- so two
storeys of one wall are one key. **11,861 of 12,550 collapses joined pieces at
DIFFERENT y**, worst separation **38.00 m**. Read 5.5.4 before trusting any fold
count in this document; the repaired counts are `[PENDING FoldKeyFix]`.

#### 5.3.2 THE AXIS TRANSPOSITION -- read this before writing any rebuild code

`params["pos"]` and the `spawn_object` wire string do **not** list the axes in
the same order. MEASURED on seq 3 (`spawn`, `wood_pole2`):

```
params pos = [13.62, 32.316, -256.38]
wire       = spawn_object wood_pole2 pos=-256.3787,13.6213,32.3161
```

so `wire[0] == params[2]`, `wire[1] == params[0]`, `wire[2] == params[1]`. The
same relation holds on `portal` and on every `spawn_plan` command checked against
its own `anchor` (`bridge-s1-temple-strait`: anchor x=-318.0 z=-64.0, wire
pos=-66,-310,30.5 -> wire[1]~=anchor.x, wire[0]~=anchor.z).

`retire` is **different**: its wire is `deleteObjects -prefab X -near x y z r`, a
3D sphere in the **same** frame as `params["pos"]`. MEASURED on seq 6: wire
`-near 481.10 47.03 920.40 20.00` against params pos `[481.1, 47.03, 920.4]` --
straight through, no transposition.

> **The WIRE is authoritative. It is the byte sequence the game actually
> accepted. A rebuild that re-derives commands from `params["pos"]` instead of
> replaying the wire would MIRROR THE ENTIRE WORLD across u=v.** That is a
> finding, not a detail.

`spawn_object` itself has three component orders in one command, all MEASURED
from IL: `pos=` is `z,x,y`, `rot=` is euler `y,x,z`, `from=` is `x,z,y`.

#### 5.3.3 A retire's centre is a MEASUREMENT; a spawn's position is a REQUEST

They differ, and the fold has to cope. MEASURED: seq 854 retires a
`wood_pole_log` at `[-275.3433, 37.2630, 224.6567]` with `r=0.6`, while seq 927
spawned that pole at `[-275.34, 37.95, 224.007]` -- **0.95 m apart in 3D**, so a
strict sphere misses the very object the record names. The live save proves the
retire won: there is no `wood_pole_log` within 0.05 m of the spawned position and
the nearest is 5.78 m away. Hence `RETIRE_MEASUREMENT_SLACK_M = 1.5`, applied
only when the strict sphere finds nothing, and always recorded in the events as
`retire-fuzzy` with its offset so the number is never mistaken for an exact
match. Used twice in 173 retires.

### 5.4 What each record type carries, and therefore what can be reconstructed

| op | carries | reconstructs |
| --- | --- | --- |
| `world_manifest` (seq 0) | `world`, `seed_name`, `seed`, `world_version`, `world_gen_version`, `game_build_anchor`, `modifiers` (+ source), `mods` (count, digest, per-DLL path/bytes/sha256), `tool` (repo, branch, commit, dirty, python), `host`, `db_bytes`, `fwl_sha256`, `started` | the ENVIRONMENT the rest was recorded against. Replay refuses on mismatch |
| `zones_generate` | `pos`, `max_m`, `zones[]`, plus the literal wire; `empty` + `fault` for the destructive-by-omission variant | which zones must exist before anything else. **Keep the literal wire** -- re-deriving a disc from the zone list sends a different command than the one that worked |
| `zones_restore` | `pos`, `max_m`, `zones[]`, `fault` | a missing `_ZoneCtrl` repair, with the measurement that justified it |
| `objects_clear` | `centre`, `radius_m`, `ids[]`, `ignore[]`, per-object prefab/position/reason in meta | the exact cylinder, replayable verbatim; `id=*` is REQUIRED or it silently removes nothing |
| `terrain_write` | `name`, `paint`, `datum`, `location_check`, `target_y` XOR `profile`, and per zone: `zone`, `centre`, **`blob_sha256`** (the TCData bytes) AND **`generated_heights_sha256`** (the lattice it was computed against), `data_entry`, `op_record`, `merged_from`, `merge_policy` | the terrain, byte-exactly, **plus the ability to detect that the ground underneath has changed** |
| `spawn` | `prefab`, `pos` (world order x,y,z), `yaw_deg`, `rot_sent`, `from`, `data_b64` + `data_sha256`, `zdo_strings`, `scale`, `datum`, `guard_radius_m` | one object with its full ZDO payload |
| `spawn_plan` | **`plan_sha256`** (the emitted plan blob, recomputable), `anchor`, `command_count`, `prefabs{}`, `body{corpus_path, sha256}`, `datum`, `pad_height`, `align`, `reach_m` | a whole building; `body.sha256` is what detects a blueprint name resolving to a *different file* |
| `portal` | everything `spawn` carries plus `tag` and `pair_tag` | both ends of a pair; the ZDO string key is `tag`, `StableHashCode("tag") = 696029674 = ZDOVars.s_tag` |
| `stock` | `target`, `items[]` | container inventory |
| `retire` | `prefab`, `pos`, `radius_m`, **`retires[]`** (the seq numbers this removes), `reason` | a demolition, **so that a replay ENDS WITH THE OBJECTS GONE rather than faithfully rebuilding litter** |
| `save` | nothing | where a crash-interrupted replay resumes from |
| `observe` | `what`, `method` (must begin MEASURED or INFERRED), `tool`, `value`, `units`, `at` | a measurement WITH ITS PROVENANCE |
| `note` | `text` | a boundary agreement or a ruling, so it is not re-litigated from a lost hub transcript |
| `chain_reset` | `broken_at_line`, `expected_prev`, `actual_prev` | a DOCUMENTED chain fork |

Three schema facts a rebuild depends on:

- **`expect` is mandatory on every mutating op.** An operation that cannot state
  a postcondition is a fact worth failing on, not a field worth skipping.
- **`retires[]` is what lets the portal-pair invariant stop counting a deleted
  end.** Without it the ledger asserts something true about the log and false
  about the world.
- **The chain is never renumbered and no `prev` is ever rewritten.** Two
  concurrent appends wrote two branches carrying the same `prev` -- MEASURED at
  **file line 47** of Ulfsland's ledger. The fork is *documented* by a
  `chain_reset` naming the file line and both digests. `replay.py` refuses an
  UNDOCUMENTED fork and verifies both digests, so a reset cannot launder a fork
  it does not accurately describe. **Consequence: `seq` is not a total order past
  line 47. FILE ORDER is the only total order the artefact has.** Two separate
  consumers needed that distinction in one night.

### 5.5 Measured fidelity of the projection

Two separate measurements, and they answer two different questions. The
**same-world reconciliation** asks "does the projection describe the world it was
folded from". The **fresh-world replay** asks "does the projection rebuild a
world from nothing but the seed and the ledger". The second is the one that
decides whether a rebuild costs the operator his world, and as of 2026-09-18 it
is MEASURED.

#### 5.5.1 Same-world reconciliation

MEASURED by `ReplayProof`, 2026-09-18, projecting all 2,483 records (ledger
snapshot sha256 `77a9e9ed7612...`) and reconciling against **the live Ulfsland
save**, with three independent instruments:

| Class | Result | Instrument |
| --- | --- | --- |
| placements | **15,674 / 15,683 = 99.94%** present at +/-0.05 m `[UNVERIFIED 2026-09-19 -- 5.5.4]` | byte scan of the 22 live `.chunk` files for a `Vector3` immediately followed by the prefab's stable hash; corroborated by 7 bounded `objects_count` probes on the live server, **7/7 agreed** (including three zeros and a 20-vs-20 disc census) |
| terrain | **191 / 191 = 100.00% byte-exact** | every projected terrain blob appears verbatim in the live save |
| portals | **20/20 tags, both ends present** | projection + save |
| sign texts | **40 / 44 verbatim** | ZDO string scan |

**The 9 discrepancies, named rather than rounded away:**

1. **7 portal-hall waypoint pieces** (4 sign + 3 `wood_pole_log`, seq 926-932,
   actor `SeatCheck`) absent from the world though the ledger records them.
2. **1 `CargoShip`** present but drifted **0.58 m**. Vessels do not hold
   position, so a 0-tolerance check cannot verify them.
3. **1 `mm_large_wall`** that never existed, because the prefab is not
   registered. The live server answers
   `Error: Entity id mm_large_wall not recognized.`

**The root cause of (1), and it is a property of the whole ledger:**
`tools/jumpstart/ledger/live.py` appends the record at **line 148**, sends the
wire at **line 163**, and verifies at **line 170**.

> **A ledger record is evidence of INTENT, not of a landed object.**

That is how 7 records can claim pieces the world does not have. The failure path
is honest about it -- a failed postcondition raises `PostconditionFailed` saying
"the record stays in the ledger -- fix the cause and re-emit, or record a
`retire`. Nothing is repaired for you" -- but a record written before the wire is
sent will survive a crash between the two.

#### 5.5.2 Fresh-world replay -- MEASURED

MEASURED by `ReplayProof`, 2026-09-18. The projection was replayed onto a
**throwaway world named `Replaytest`**, provisioned from scratch on seed
`Pirate68` -- the only legitimate way to test it, since Ulfsland is read-only and
is the reference. Seed identity confirmed on both sides before a command was
sent, and stated in the field names 1.8 uses: **both worlds carry `Pirate68` /
`147627509` / `world_version 41`** -- `Replaytest`'s `_main.0.fwl2` against
Ulfsland's `_main.306.fwl2`. That identity is what makes every terrain write
replay against the baseline it was computed from.

> Label note, because the raw bytes invite a mislabelling that was made once
> already: the four bytes `f5 9d cc 08` read little-endian are
> `0x08CC9DF5 = 147,627,509`, i.e. they ARE the seed int, not a separate
> "derived seed". And the `0x29 = 41` immediately after the seed name is
> `world_version`; `worldGenVersion` is the later field and reads **2**. Use
> 1.8's names and parse order, not a hexdump's apparent ones.

| Class | Fresh world | Same world (5.5.1) |
| --- | --- | --- |
| placements at +/-0.05 m | **15,682 / 15,683 = 99.994%** `[UNVERIFIED 2026-09-19 -- 5.5.4]` | 15,674 / 15,683 = 99.94% `[UNVERIFIED]` |
| terrain zones, byte-exact | **191 / 191 = 100.00%** | 191 / 191 = 100.00% |
| portal tags, both ends | **20 / 20** | 20 / 20 |
| sign texts verbatim | **44 / 44** | 40 / 44 |

**The fresh world is MORE faithful than the world the ledger was recorded
against**, and the reason is 5.5.1's root cause: the 7 portal-hall waypoint
pieces and the 4 missing sign texts were records written before a wire that never
landed. A replay sends the wire and verifies it, so it *has* the pieces the
original build lost. The projection is not merely reproducing Ulfsland; it is
reproducing what Ulfsland's ledger says Ulfsland should have been.

**Cost, MEASURED, zero failures and zero console-sink stalls in all four phases:**

| Phase | Work | Wall clock | Share |
| --- | --- | --- | --- |
| `zones_generate` | 142 zones | 318.5 s | 14% |
| `objects_clear` | 832 clears | **1,874.6 s** | **83%** |
| `terrain_write` | 191 zones / 382 commands | 21.6 s | 1% |
| placements | 15,683 in 385 batches | 27.7 s | 1% |
| **replay total** | **3,690 commands** | **2,247 s = 37m28s** | |
| end to end | + provisioning, first-boot creation, the RCON-binding restart (4.3), the prefab probe | **~47 min** | |

**Read the cost table before optimising the wrong thing.** Placing 15,683 objects
costs 27.7 seconds. Clearing costs 1,874.6 seconds -- **83% of the replay** --
because staged Upgrade World operations run across frames and each one must be
waited out (`STAGED_SETTLE_S = 180.0`). A rebuild is a clearing job with a build
attached, not the reverse.

Save postcondition verified by **generation advance**: `_main.3.fwl2` ->
`_main.4.fwl2` (section 1.8), not by an exit code.

**The one piece that cannot replay, at any tolerance, after any number of
passes:** `mm_large_wall`, ledger **seq 2118**, site `probe-znetscene`. Three
independent instruments agree it is *unregisterable*, not merely missing:

1. live Ulfsland answers `Error: Entity id mm_large_wall not recognized.`
2. the fresh world's prefab probe puts it in the unregistered set -- **135 of 136
   distinct prefabs register**
3. a direct re-send answers
   `Object reference not set to an instance of an object`

That is the `mm_` Ravenwood family whose registration entry point is a 1-byte
`ret` (5.6.1). **So the honest ceiling on a faithful replay of this ledger is
15,682 / 15,683, and the missing one is a record of a probe, not of a
building** -- `[UNVERIFIED 2026-09-19]`: the fold these counts came from emits
**19,777** survivors, so the denominator itself is in question until
`FoldKeyFix` reports (5.5.4).

#### 5.5.3 The repair pass, and why a projection replay is safe to run twice

This is the operator's real safety margin and it is worth its own subsection.

MEASURED: the first replay pass landed **15,680**. A **census-gated repair pass**
(`project.py repair` -- it reads the world's own save first and re-sends only what
is absent) took it to **15,682** and sent **ZERO duplicates**. The save then held
exactly **174 of 174** `wood_pole_log_4`, one per position. **An ungated re-run
would have made 176.**

That is the same read-before-write gate the idempotency audit found **missing from
10 of 19 mutating entrypoints**, and it is the difference between a rebuild you
can retry and one where the second attempt doubles a building. `spawn`,
`spawn_plan`, `portal` and `stock` are `idempotent = "guard"` for exactly this
reason (5.6.2); the guard is what makes a partial replay resumable instead of
destructive.

> **RULE: a rebuild is allowed to be interrupted. Never re-send a placement
> without a census gate, and never "just run it again" against a tool that has
> not proven it has one.**

The remaining INFERENCE, now much narrower than it was: that the same seed
regenerates identical zone *contents* is still INFERRED from `PlaceVegetation`
seeding its RNG with `worldSeed + zoneX*4271 + zoneZ*9187 + prefabHash` (read in
IL by the clearing work). What the `Replaytest` run MEASURED is that the
generated HEIGHT field replays terrain byte-exactly (191/191) and that every
recorded placement lands where it was recorded. Vegetation identity across a
regeneration was not the subject of the proof and remains UNMEASURED.

**Teardown, confirmed after the proof.** `Replaytest` and its container,
network, image and 4.4 GB copied profile were all torn down, and Ulfsland
re-measured **bit-for-bit identical** afterwards (15,674/15,683 placements,
191/191 terrain) with the four stopped worlds still dated 2026-09-15 and
containerless. That re-measurement is the proof that the reference world was
never touched, and a rebuild proof that cannot show it is not finished.

Sources: `agent://ReplayProof`, `history://ReplayProof`.

#### 5.5.4 A fold is not a replay, and a fidelity figure measures AGREEMENT WITH THE RECORD

**`15,682 / 15,683 = 99.994% at +/-0.05 m` says the replay agrees with what was
recorded. It says nothing about whether what was recorded was right** -- and as
of 2026-09-19 it does not even say that much, because the fold it was computed
through cannot have emitted 15,683 rows (see the `[UNVERIFIED]` note at the end
of this subsection). A faithfully replayed defect scores 100%; a defect the fold
removes scores as a miss; a legitimate piece the fold DESTROYS scores as a miss
too. Those are three verdicts wearing one number, and a replay report that does
not separate them is unreadable:

```
differs because the REPLAY is wrong    -> a regression. Fix the replay.
differs because the SOURCE WORLD is    -> a repair. Fix nothing; the fold already did.
  wrong
```

**A defect does not fold away because it is a defect, and a fold is not a repair
unless the KEY IS EXACT.** MEASURED 2026-09-19, three outcomes from the same
ledger and the same fold:

```
FOLDS AWAY  records that collide at the key AND are the SAME object
            the duplicate pier at Ulfsgard: 52 duplicate pieces standing
            (20 wood_floor, 19 wood_pole_log_4, 13 wood_roof)
            -> collapsed to one each -> the defect is ERASED by the rebuild
            (structures-and-settlements.md 3.6)

SURVIVES    defects LAUNDERED into a surviving record's payload
            the ghost road: the retired road's values were CARRIED FORWARD
            VERBATIM into the surviving blob -> the fold replays the winner
            faithfully -> the defect is REPRODUCED
            zones whose surviving blob belongs to a RETIRED write:  0 of 5
            dropping seqs 213 / 1693 / 1779 changes NOTHING; the bytes are
            in seq 2053

DESTROYS    DISTINCT objects that collide at the key BECAUSE THE KEY IS TOO
            COARSE -- every storey of every building above the ground course

REPLAYS EXACTLY, AND THAT IS THE PROBLEM
            defects in the INPUT that produced a recorded blob. A replay
            re-sends the blob verbatim, so the defect lands again, perfectly.
            Every pad in this world was levelled against
            WorldGenerator::GetHeight while the engine renders
            HeightmapBuilder::Build (terrain-and-roads.md 1.6): wt-peak's pad
            102.310 against rendered ground 93.885..99.427, 212 pieces plus a
            portal and a guidepost in 3.17-13.54 m of air, median 4.92 m.
            ONLY a RE-DERIVATION repairs this class -- rebuild with
            PATCHSCAN_SURFACE=heightmap and re-solve the pads, and they land
            correctly. Replay the blobs and they land wrong again.

THREE DISCRIMINATORS, and the first alone is what made this section wrong for
one night:
  1. does the defect occupy a key another record also claims?
     if nothing collides with it, nothing collapses it.
  2. IS THE KEY SUFFICIENT TO TELL TWO REAL OBJECTS APART?
     if not, every collision it reports is a coin flip between a repair and
     a demolition.
  3. is the defect in the RECORD, or in the INPUT that produced it?
     a replay re-sends records. It cannot repair a bad input; only
     re-deriving the record from a corrected instrument can.
```

**THE KEY WAS NOT EXACT, AND THAT IS WHY A REGENERATION REPAIRED SO LITTLE.**
`project.py:97` folded on `qkey(prefab, u, v)` -- prefab plus HORIZONTAL
position -- while the module's own docstring said the key is "prefab + WORLD
POSITION". Y was omitted. **`project.py` now keys in 3D**, so the only class a
regeneration repairs for free - records that collide at one key AND are the same
object - is finally a class it can identify. MEASURED before that fix, by
running the fold with `_place` instrumented to record the y of both sides of
every collapse:

```
placements_applied                32,946
collapsed_duplicate_commands      12,550
    SAME y  - true duplicate, the fold REPAIRS it              689
    DIFF y  - legitimate stacked piece, the fold DESTROYS it 11,861
                                                   worst dy   38.00 m
survivors                         19,777
top losses  woodwall 1,391 · Piece_grausten_pillarbase_medium 890
            Piece_flametal_pillar 852 · stone_wall_2x1 728
Ulfsgard    1,004 rows -> 828 · woodwall 164 -> 68
            wood_pole_log_4 183 -> 133
            fb_fortress_portcullis 6 -> 3, lower row y 23.774, upper y 29.481,
              same three xz -- the sea gate replays HALVED, on the
              underwater side
```

**A regeneration run through the 2D key would have been a 36% demolition, and
every multi-storey structure on this world would have rebuilt as a single
course.** Those are the figures the 2D key produced and they stand as the
measurement of the defect. **Post-fix counts are `[UNMEASURED]`:** the 3D key is
in `project.py`, nobody has re-run the fold through it, and nothing in this
document may quote a post-fix fold count until somebody does.

**The cause of the survival class is an invariant, not an accident.** A zone
holds exactly one `_TerrainCompiler` and `terrain_write` deletes it before
respawning, so every blob must be the UNION of everything the zone keeps --
`roads/applied.py`'s own docstring states it: *"Every write into an already-
written zone re-reads the prior blob and unions it PER SAMPLE INDEX. A sample
both writes claim goes to the LATER writer."* Seq 2053 carried forward the
alignment it was replacing because the format gave it no choice. **The same
invariant that stops a later write from erasing T8's grading is what preserved
the decoy.**

**The instrument that makes the survival class visible is a pair of questions,
and either one alone answers clean:**

```
OWNER   whose blob physically HOLDS the sample   -> what the fold emits
AUTHOR  who last CHANGED the value               -> where the defect originated

over 55 ghost samples:
  OWNER   2053 x44, 1946 x11
  AUTHOR  1693 x30, 1779 x10,  213 x3
```

Asking only "who owns this ground" says the LIVE road owns it and finds nothing
wrong. The defect lives in the gap between the two answers. Run both.

**The corollary, CORRECTED, and the correction is the point:** a fold repairs a
colliding-duplicate defect **only where the key is exact, and this project's key
is not.** The 52 Ulfsgard duplicates really do collapse for free -- and they
collapse alongside **130 legitimate pieces destroyed at the same site** (1,004
rows to 828, of which only the 52 are duplicates). The earlier form of this
paragraph read that as an argument for rebuilding rather than culling; **that
recommendation is WITHDRAWN and the cull is warranted.** "The regeneration fixes
it for free" MUST be proven per defect, against both discriminators, and never
assumed -- for anything terrain-shaped assume the laundering case until the
owner/author split says otherwise, and for anything stacked assume the
destroying case until the key says otherwise.

**How the retraction happened, which is the most instructive thing in this
subsection.** The agent that found the key defect had recommended the opposite
one message earlier. It then tested its own recommendation against the rule
issued minutes before -- *"prove it per defect, never assume"* -- and found it
had **asserted a mechanism it had never opened.** A rule issued at 21:00 caught
a claim made at 20:58. That is the manual working, and it is the only reason
this page is not still wrong.

**The argument a rebuild actually has, and it is not the one this subsection
carried an hour earlier.** A replay of the blobs *as recorded* reproduces every
mis-levelled pad exactly, because the blob is faithful to a bad input. **A
rebuild that re-derives the pads with `PATCHSCAN_SURFACE=heightmap` places them
on the surface the engine draws.** That is a repair no cull and no re-seating
pass can deliver at scale, and it is the strongest case for regenerating that
this project has measured -- but it is conditional on one open question:
**whether every pad is re-derivable from its plan, or whether the blob is the
only surviving record of it.** `[PENDING GroundingFix]`. If a pad is
blob-only, a rebuild replays its defect and the discriminator above says so.

**`[UNVERIFIED]` -- the headline fidelity figure, and the reason is worse than a
wrong denominator.** MEASURED by `FoldKeyFix` 2026-09-19, who reproduced the old
fold exactly: **15,683 IS this fold's survivor count** on the 2,483-record prefix
(15,683 survivors, 191 terrain, 832 clears), with **12,299 commands already
collapsed away**, most of them legitimately stacked pieces. So the number is real
-- it is simply **not a count of the world**. On the full 2,914-record ledger the
fixed fold yields **30,348 rows** where the 2D fold yielded 19,777.

**And the census it was reconciled against was y-blind TOO, and matched WITHOUT
CONSUMING**, so one live object could satisfy every projected row in its column.
Demonstrated offline: a five-course wall of which the world holds **ONE** course
scored **5 / 5 = 100.000%** under the old census and **1 / 5 = 20%** under the
fixed one.

> **`15,682 / 15,683 = 99.994%` measures that every row the projection emitted
> was matched. It does not measure that the world was reproduced, because both
> the fold and the census identified objects by HORIZONTAL POSITION ONLY.**

Two errors that cancel are the hardest kind to see, and a clean agreement
between two instruments sharing one blindness is rule 4 of the doctrine wearing
a percentage. The figures stay in place, undeleted and undefended, until a
re-run through the fixed fold and a consuming, y-aware census replaces them
`[PENDING FoldKeyFix]`.

### 5.6 Preconditions for a faithful replay

`replay.py preflight --root runs/Ulfsland` answers `PROCEED` or `REFUSE`. It
checks, and these are the preconditions:

**FATAL on mismatch** -- replaying would build something other than what the log
says:

| Field | Why fatal |
| --- | --- |
| `world` | a replay pointed at a different world builds into someone else's save |
| `seed_name`, `seed` | **every TCData delta in this ledger is relative to this seed's generated heights** |
| `world_gen_version` | `WorldGenerator` output is versioned; a different generator makes the same delta land at a different absolute height |
| `game_build_anchor` | the mod set was validated against this build |
| `mods` digest | `ZoneSystem::PlaceLocations` runs whatever locations the loaded mods registered, so **a different mod set generates different POIs under the same seed** -- and a pad solved against a location that no longer exists (or now does) is a pad that deletes location content |
| `modifiers` | never in the save (`ZoneSystem::Save` strips every key resolving to a named `GlobalKeys` member; `SetStartingGlobalKeys` re-derives them each boot from launch args), so they can only be compared as OBSERVATIONS -- and `resources_muchmore` / `portals_casual` change what the built world MEANS even when every coordinate matches |
| any `requires.blobs` sha missing or corrupt | the payload has been modified |
| any `spawn_plan` body unresolved or digest-mismatched | a body that resolves to a different file is a different building |
| any `requires.prefabs` entry that does not register | see below |

**ADVISORY**: `world_version` -- the save format version. It does not change
generated terrain, but a large jump means a game update nobody validated the mod
set against.

**"NOT MEASURED" is treated as FATAL, not as "no drift."** If the header was
written without hashing the plugin set, "the same mods" cannot be asserted. An
empty global-key list in a save is CORRECT and is not evidence of no modifiers.

#### 5.6.1 The prefab precondition, with the live example

> **A prefab that no longer registers cannot be replayed.** `spawn_object` on an
> unknown prefab is a no-op with a cheerful echo.

MEASURED live on Ulfsland at ledger **seq 2118-2123**, actor `WaterfrontRun`,
2026-09-17T11:07Z -- two independent console instruments, opposite results:

| Prefab | `spawn_object` | `objects_count` | In `ZNetScene`? |
| --- | --- | --- | --- |
| `mm_large_wall` | `Object reference not set to an instance of an object`, placed **nothing** | `Error: Entity id mm_large_wall not recognized` | **NO** |
| `fb_bridge_01` | placed | `1` | **YES** |
| `fb_bridge_02` | placed | `1` | **YES** |

The cause, from IL. `JamesJonesTV-RavenwoodVikingHouses` 7.7.8 has three
registration entry points and only one is live:

| Entry point | IL bytes | Body |
| --- | --- | --- |
| `AddRWEffects` | 1 | `2a` = `ret` -- dead |
| `AddRWPieces` | 1 | `2a` = `ret` -- dead |
| `AddFBPieces` | **65** | `call FB_Pieces::AddPieces`, live |

`FB_Pieces` has 29 `PieceManager::AddPiece` call sites, all reachable.
`RW_Pieces` has 102, none reachable. So the 102 `mm_`/`RW_` prefabs -- massive
and castle walls, trees, hedges, roads, floors -- are **not in `ZNetScene` at
all**, while the 29 `fb_` village pieces register normally.

Two corollaries for a rebuild:

- **`fb_` IS replayable and is already standing.** Ledger seq 2102-2117 built
  `fb_fortress_wall`, `fb_fortress_wall_tower` and `fb_fortress_wall_gateway`
  into `harbour-vestvik` with measured geometry (wall 20.009 x 11.866 x 6.8 m;
  tower **15.402 m tall with its walkable platform at y=10.229, not the mesh
  top**; `pivot_is_foot=true`).
- **Compare the DLL hash, never the version string.** 7.7.8 is a
  documentation-only republish of 7.7.7: both packages carry a byte-identical
  `RavenwoodVikingHouses.dll` (`md5 7a85c9fcb086633d8dc8fcd49a33f46b`, 64,162,816
  bytes). A yesterday-dated release carried the same binary as 2025-12-08.

#### 5.6.2 Idempotence, honestly, because resume depends on it

```
zones_generate  yes    SpawnZone skips a zone that IsZoneGenerated
zones_restore   yes    a zone that already holds its _ZoneCtrl gains none
objects_clear   yes    removing nothing is a no-op
terrain_write   yes    it deletes the zone's compiler before spawning
save            yes
spawn / spawn_plan / portal / stock   NO -- re-running DUPLICATES
```

The non-idempotent four are **guarded**: the driver counts what is already at the
anchor and SKIPS when the world holds the op's output. `GUARD_EPS_M = 0.5` --
smaller than the closest spacing any two same-prefab pieces will ever have,
because the guard answers "is THIS op's output present" and a neighbour answering
on its behalf silently drops a piece. **When the guard cannot measure, it stops
rather than risking a double-built house.**

**This is the defect that forced the lighthouse demolition.** A builder with no
read-before-write census gate places everything AGAIN when re-run against a site
that already holds it. Treat "is each tool idempotent?" as a first-class result
of any rebuild, not an assumption.

#### 5.6.3 One zone holds one compiler

`Heightmap::GetAndCreateTerrainCompiler` returns the **FIRST** compiler in a
zone, and the write path issues
`deleteObjects -zone ZX ZZ -prefab _TerrainCompiler -force` before spawning. So a
second `terrain_write` to a zone **DESTROYS the first one's terrain**. A 20 km
ribbon crosses ~300 zones and will meet pad zones, so this is the normal case,
not a corner case.

The ledger refuses a `terrain_write` to a zone an earlier `terrain_write`
touched unless it names that earlier seq in `entries[].merged_from` and sets
`merge_policy`. The projection records `writes_to_this_zone` per zone and dropped
**559 superseded writes** to reach 191 final zones.

**And the union must resolve in FILE ORDER, later wins.** MEASURED: a union loop
that carried the EARLIEST prior would have restored `RoadBuild` seq 213's road
delta over `SiteFinish` seq 286's pad **inside the pad footprint** -- a road
re-asserting itself under a building's foundation, reintroducing a 45.05 -> 47.11
step while looking like a faithful union.

**Confirmed live, and it is also the audit every rebuild should run before and
after.** MEASURED 2026-09-19 over all **435** zone centres the chain claims a
`terrain_write` for: **429 hold the claimed compiler (98.62%), 6 do not, and 0
hold more than one** -- the invariant is real in the save, not just in the
source. Of the 6 absences, 3 are claimed only by a write that never landed and
are not damage at all. The triage, the instrument (a native, socket-borne,
one-row `findObjects`, 435 zones in 160 s) and the live damage it found are in
`terrain-and-roads.md` §1.5.

**The corollary that bit three separate agents in one night: because every blob
is the UNION of what the zone keeps, a blob carries samples its own segment
never crosses.** Seq 1946 (live T8) holds **878** modified samples in (13,11) and
**175** in (13,12) while T8's centreline enters neither. So scope any repair or
re-emit to **what the BLOBS claim, never to what the centrelines cross** -- and
recognise the shape: it is the same laundering mechanism as the ghost road
(5.5.4).

### 5.7 What the ledger explicitly CANNOT capture

Written down because this is where the next surprise lives, and because an
artefact that does not state its limits gets trusted past them.

1. **World generation itself.** The ledger replays EDITS. It can DETECT that the
   generated heights under a zone changed (`generated_heights_sha256`) and
   refuse; it cannot correct for it and cannot reproduce the terrain.
2. **The fresh-regeneration half of the round trip is now PROVEN for edits and
   still UNMEASURED for vegetation** (5.5.2, 5.5.3). A from-scratch world on the
   same seed replayed 15,682/15,683 placements `[UNVERIFIED 2026-09-19 -- the
   fold key omits y, 5.5.4]` and 191/191 terrain zones byte-exactly; that the
   same seed re-places identical *vegetation* is still INFERRED from IL, not
   measured.
3. **ZDO identity and anything keyed off it** (section 0).
4. **Anything a player did.** Loot, hoeing, damage, repairs, wards, map pins,
   character state.
5. **Time and spawner state.** `_ZoneCtrl` spawn state, raids, creature
   populations, item despawn, wear-and-tear decay. **A replayed world is the
   built world at t=0**, not the world as it was when it was measured.
6. **The bodies and the mods themselves.** Blueprint bodies are not vendored and
   mod DLLs are not copied into the repo. The ledger carries digests and REFUSES
   on mismatch; it cannot restore a corpus that has been deleted. **Back the
   blueprint corpus up separately or the ledger is unreplayable.**
7. **Per-piece payload verification at scale.** A single spawn's ZDO strings read
   back (`findObjects -detailed` prints a sign's `Text:` and a portal's tag --
   MEASURED). A 1,907-piece body cannot: listing it is the question whose answer
   wedges the console sink. So a `spawn_plan` is verified by COUNT and by the
   plan's digest; its per-piece `data=` payloads are verified only transitively.
8. **Out-of-band writes.** The zone-clobber and flatten-FORBIDDEN invariants see
   only what goes through this ledger file. A player, another tool or an ad-hoc
   RCON command is invisible to them -- including the unrecorded `wipe` the
   round-trip proof itself used.
9. **Whether the build is any good.** This proves REPRODUCTION, not quality. A
   road that is unwalkable replays perfectly.
10. **The rest of the deployment.** Server config, `SERVER_ARGS`, the
    `admin_mode` overlay and `ServerCharacters` templates are not captured. A
    replay into a correctly-seeded world with a different server config will pass
    preflight.

### 5.8 The console sink, which will bite the rebuild

Not a ledger property; a transport property that shapes every verification step.

MEASURED by disassembling the deployed `ValheimRcon.dll` 1.6.2:
`RconProxy.HandleCommandAsync` queues every command through
`ThreadingUtil.RunInMainThread`, and on resume does
`Log.Message("Command completed: " + command + "\n" + result.Text)` on the
**full, untruncated** text, inline on the Unity **main thread**.
`ValidatePayloadLength` truncates at 4,050 bytes but runs later, when the reply
packet is built -- **truncation never protects the log.**

So the wedge is a function of one thing: how many bytes land in
`CommandResult.Text`. That splits the command surface cleanly:

- **Placement is safe to batch.** `InvokeConsoleCommand.OnHandle` returns exactly
  `"Command '<joined>' executed."` and never carries the console command's
  output, so a batch cannot grow with world size.
- **Verification is the dangerous half.** `findObjects`' per-ZDO listing IS
  `result.Text`. An `objects_count id=*` over a freshly generated zone answered
  `Total: 6149` followed by one line per prefab, went straight to the sink, and
  **wedged the server's stdout while the Unity main thread carried on.**

> **RULE: NEVER ASK A QUESTION WHOSE ANSWER IS LONG.** Scope every check to named
> prefabs or to a small radius. `replay.py` refuses an unscoped count before it
> reaches the wire. `project.py census-plan` exists for exactly this: it groups
> the projection into bounded discs (`--reach 24`) so the census asks one named
> prefab at a time.

A `retire`'s reply is one line **per deleted object** (155 bytes MEASURED), so a
retire must be scoped small enough that its ANSWER is short. A 1,255-piece record
wedged the pipe and stopped the main thread; keep records to ~48-64 pieces with a
host-side sink check between.

### 5.9 The rebuild, as a runnable sequence

```
 0. BACK UP EVERYTHING FIRST.
      hostops/backup_valheim_world.sh <World> pre-rebuild
      tar -tzf <archive> | grep -c '\.chunk$'          # verify MEMBERS (4.6)
    And back up the blueprint corpus, which is NOT in the ledger (5.7 item 6).

 0b. POINT THE TOOLCHAIN AT THE TARGET.
      export VALHEIM_WORLD=<World>
    terraform/rcon.py used to hard-code CONTAINER = "valheim-server-Ulfsland"
    and replay.py:552-555 restarts that constant BY NAME, so an unparameterised
    toolchain sends every replay command into the REFERENCE world (7.2).

 1. PROVISION on the recorded seed.
      hostops/portal_create_valheim_world.sh <World> <seed_name>
      # then RESTART ONCE before expecting RCON (4.3)
      # readiness = a successful `players` probe, never a log read

 2. PREFLIGHT.  Refuse, do not repair.
      tools/jumpstart/ledger/replay.py preflight --root runs/<World>
    EXPECT REFUSE.  It refuses against Ulfsland itself today -- 120 -> 119 DLLs,
    2 removed, 1 added, 23 changed (7.1.2).  Read every fatal row and reconcile
    the mod set and the modifiers BEFORE touching the world.
    Re-run the LIVE LocationProxy census here too: More_World_Locations_AIO is
    one of the changed DLLs, so old keep-outs are not trustworthy (7.1.1 item 3).

 3. PROJECT.
      tools/jumpstart/ledger/project.py fold --root runs/<World> --out manifest.json
    Sanity-check the counts against 5.3 before trusting the manifest.
    HARD STOP as of 2026-09-19: do NOT run a rebuild through a fold whose key
    omits y (5.3.1).  Measured on this ledger it collapses 11,861 distinct
    stacked pieces into their ground course -- 32,946 applied, 19,777 survivors,
    a 36% demolition.  The gate is: the fold key distinguishes two pieces at the
    same xz and different y, proven by a same-xz/different-y fixture, BEFORE
    step 4.  Repaired counts are `[PENDING FoldKeyFix]`.

 4. EMIT ZONES from `generate_wires`, LITERALLY.
    205 zones on Ulfsland.  Re-derived discs are a different command.
    A terrain write into a zone with no `_ZoneCtrl` at its centre is REFUSED
    by flatten.py, so this is a hard precondition, not a convenience (7.1).

 5. EMIT CLEARS from `clears` (832), then TERRAIN from `terrain` (191 zones),
    honouring `merged_from` and unioning in FILE ORDER, later wins.
    Each terrain entry re-checks `generated_heights_sha256` against a fresh
    PatchScan of the new world.  A mismatch is a REFUSAL, not a warning.
    MEASURED cost: clears 1,874.6 s, terrain 21.6 s.  The clears ARE the job.

 6. EMIT PLACEMENTS from `placements` (15,683), guarded, batched at 48-64
    pieces, with a host-side sink check between batches.
    REPLAY THE WIRE.  Do not re-derive from params["pos"] (5.3.2).
    MEASURED: 385 batches, 27.7 s, zero sink stalls.

 7. REPAIR, census-gated:
      tools/jumpstart/ledger/project.py repair --root runs/<World>
    Reads the target world's own save first and re-sends ONLY what is absent.
    MEASURED on Replaytest: 15,680 -> 15,682 with ZERO duplicates (5.5.3).
    NEVER re-run step 6 instead of this; an ungated re-send made 176 of a
    prefab whose correct count is 174.

 8. RECONCILE the rebuilt world against the projection.
    Report present / absent / drifted, per named prefab, with the tolerance.
    Expect 15,682 / 15,683 `[UNVERIFIED -- 5.5.4]`: `mm_large_wall` is
    unregisterable (5.5.2), and the fold count itself is in question until
    FoldKeyFix reports.
    Classify every difference BEFORE reporting it (5.5.4), three ways:
      COLLIDED AND SAME OBJECT   reads "missing", is a REPAIR -- do not close it
        the 52 Ulfsgard duplicates
      LAUNDERED INTO A SURVIVOR  reads CLEAN, is a defect -- find it with the
        owner/author split, never with the reconciliation count
      COLLIDED AND DISTINCT      reads "missing", is a DEMOLITION -- the key
        omits y, so every storey above the ground course lands here

 9. SAVE, verify by GENERATION ADVANCE (_main.N -> _main.N+1), then back up
    and verify the backup by its members (4.6).

Budget, MEASURED end to end on Replaytest: replay 37m28s for 3,690 commands;
~47 minutes including provisioning, first-boot world creation, the
RCON-binding restart and the prefab probe.
```

---

## 6. Build order for a FRESH world

Each step names what it measures and what gates the next one. The ordering is not
preference: three of the seven gates were proven by deliberately breaking them.

### Step 1 -- Seed selection and acceptance

**Measures:** NS walkable extent of the spawn component at 32 m (section 1.2);
the crossing table (1.6); `dn_walk_path` and detour factor; `spawn_coast_dist`;
`spawn_land_km2`.
**Gate:** the corridor meets the operator's stated requirement, *including the
crossings needed and their spans against the ~96 m bridge ceiling*. Record the
accepted seed with its measured corridor in a document, not a transcript.
**Refusal:** do not re-roll for Deep North proximity, trader placement, biome
proximity or "walkability" as a boolean. 1,031 seeds say those are fixed
(section 1.4).

### Step 2 -- Provision

**Measures:** the seed read back out of the newest `*.fwl2` on disk; the world
version and worldGenVersion; the mod digest; the modifier set from a live
`globalKeys` observation.
**Gate:** the seed on disk equals the seed requested, **and the server has been
restarted once and answers a `players` probe** (4.3).
**Refusal:** do not judge the generation by a success check. Three different
success checks each destroyed a correctly generated world (4.2).

### Step 3 -- Survey OFFLINE against the generated field

**Measures:** everything seed-derived. Route corridors, pad flatness, harbour
depth profiles against y=30, biome purity, relief, clamp headroom -- all against
a sandboxed PatchScan raster (section 2), coarse to search and 1 m
river-inclusive to decide.
**Gate:** every candidate site has a 1 m verdict, not an 8 m one. A 32 m
footprint judged flat on an 8 m grid is really flat only **18%** of the time
(2.3).
**Refusal:** never use the river-free coarse grid to decide a height. It put 10
of 13 solved base sites under water (2.3).

### Step 4 -- LIVE marker probe

**Measures:** `findObjects -prefab LocationProxy -near x y z r`, bounded, around
every candidate site.
**Gate:** every site has a per-marker stand-off, computed from the live marker
set with the dump used only to *identify* markers and earn a smaller stand-off.
Unidentified markers get the 32 m worst case (section 3).
**Refusal:** never resolve against `plan.json`. 12,301 entries, every one
`placed: false`; 60 of 65 live markers at one site were invisible to it.
**Sequencing:** this is the FIRST save-derived step and it must come after a
stable world exists, never before. An agent that measures it early has to
re-measure it.

### Step 5 -- ROAD NETWORK FIRST

**Why first, with its number.** A pad laid on an existing road puts a building in
the carriageway, and the reverse ordering leaves the road clobbered. MEASURED on
Ulfsland: **89 of 1,443 centreline metres of T4 sit on samples that `SiteFinish`
seq 286 (`stenvik-beehive-1`) overwrote AFTER `RoadBuild` seq 213.** At sample
(586, 954) the road delta is -4.470 while the live union holds -1.571, so the
applied surface jumps **45.05 -> 47.11 in one metre, in the middle of the road.**
Worst applied-vs-planned deviation by segment: T4 **6.391 m**, T13 4.400, T5
3.574, T3 2.113, T12 1.064, T1 0.900, W5 0.854, the rest under 0.2.

The ruling that came out of it, which a fresh world should implement rather than
rediscover: **inside a settlement pad's footprint the PAD wins -- it is a
building's foundation and it must stay flat -- but the road owns the approach and
MUST re-emit a graded transition into the pad edge instead of ending at a cliff.**

**Measures:** ribbon length, zones touched, transverse 1 m step distribution,
over-grade fraction, pad-approach step.
**Gate:** every segment ARRIVES. Solve the terminus against the DATUM of the
thing it meets -- a town pad, a dock deck (`DECK_DATUM_Y = 30.6`), a bridge
abutment. **T4 stopped 10.4 m below Stenvik's floor** and every component
downstream behaved correctly around a number that meant nothing. **333 unwritten
centreline metres in four runs** (204, 68, 41, 5 m) where the road simply stops
at pads -- and seq 213's own meta ALREADY RECORDED `step_across_m -7.644` at
s=888.3. The measurement existed in the record and nothing read it.
**Cost model:** a road's cost is PER ZONE, not per metre -- one
`_TerrainCompiler` per zone. MEASURED: 33,619.5 m of ribbon over **470 zones =
470 ZDOs**. A crossing that clips a 64 m zone costs one compiler whether it is
10 m or 90 m of ribbon through it.
**Refusal:** roads cannot be built from pieces -- `piece_pavedroad` has no
persistent `ZNetView`, so a road IS a terrain write. Bridges are the opposite:
placed bodies whose piles are protected footprints the road must grade AROUND.

### Step 6 -- Settlements

**Measures:** per-building pad rectangles (rotated, not circles -- a
circumscribing circle over-claims 39 m2 of street on `stenvik-longhouse-2`, 8.4 x
17.0 m at yaw 30), pad target_y = the median terrain height over the footprint,
cut/fill against the +/-8 m clamp.
**Gate:** every pad passes the location gate (step 4's stand-offs) and the
zone-generated gate before a single byte is written.
**Refusal:** **a town's `pad_radius_m: 100.0` is a DISTRICT radius, not a
footprint.** Read as a footprint it made a road refuse to write for **294 m** into
Stenvik and left the town unreachable behind a **10.4 m step**. Foundations are
the per-building rectangles, worst half-extent **13.05 m**.
**Refusal:** **two roads closer than the sum of their batter runs are ONE
CORRIDOR.** Grading them toward each other digs a ditch between two roads.

### Step 7 -- Waterfront

**Measures:** depth profile against y=30, floatable-water thresholds per vessel
class, berth length against the vessel, span against the bridge ceiling.
**Gate:** span under ~96 m -> bridge; over it -> ferry terminal. A 174.7 m
crossing was refused against that ceiling and the answer was a ferry, not a
bridge built in hope.
**Refusal, and this is the one damage class the project treats as unrepairable:**
**NEVER write terrain below `c_WaterLevel` 30.0 beside or under a structure.**
Draining the water out from under a pier is the "early dock" defect: the pad
levelled the rectangle, the water under the pier went away, and an over-water
body stood on dry ground reading as the floating defect. The ledger enforces it
at WRITE time -- a structure op may declare `params.flatten = "FORBIDDEN"` and a
`terrain_write` whose footprint overlaps one is REFUSED.

### Step 8 -- Lighting

**Measures:** sightline in BOTH directions along the road from each candidate
lamp position.
**Gate:** spacing 30-60 m, but sited by TERRAIN, not by interval: dense in
forests and other low-visibility biomes, sparing in Meadows. A lamp every 45 m on
a straight open meadow is waste; a lamp on the crest before a Black Forest bend
is the whole point.
**Note for the ledger:** `road_lighting` places objects and writes no terrain, so
the pad and POI keep-outs for a lamp are enforced by the tool rather than by
`schema._pad_footprint_check`, which fires on `terrain_write` only.

### Step 9 -- Verification

**Measures:** bounded per-prefab censuses; transverse step distributions; portal
tag pairing by subtracting retirements; a `save`; a backup verified by its
members.
**Gate:** the projection of the new ledger reconciles against the new world
(section 5.5's instruments).
**Refusal:** never verify with an unscoped count (5.8).

**One rule that spans every step**, and it is the cheapest lesson in this
document: **a structure standing in a road is a ROUTING failure and must be fixed
by re-routing or re-terminating -- never by deleting the building.** Road clearing
removes vegetation by explicit prefab list and has never removed a building. The
operator found a circle of huts in the road at Stenvik's far end.

---

## 7. What a rebuild re-earns for free, and what must be ported

### 7.1 The distinction

A fix that lives in **TOOL CODE** is re-earned free: the rebuild runs the fixed
tool and the defect cannot recur. A fix that was applied as a **one-off per site**
lives only in the ledger, and a rebuild either ports it deliberately or loses it.

MEASURED by `ReplayProof`, 2026-09-18, by walking the build session's fixes and
decisions against the code and the ledger:

| | Count |
| --- | --- |
| distinct build-session fixes / decisions | **22** |
| live in TOOL CODE -- **re-earned free** | **16** |
| ledger/data decisions -- **must be ported** | **6** |
| of those 6, having **no home outside the ledger itself** | **4** |
| plus: rebuild blockers found *during* the replay proof, now tool code | **1** |

Quote it as **"22 build-session fixes (16 free / 6 to port), plus one rebuild
blocker found and fixed during the replay proof."** The 23rd is listed in 7.2
because it is the one that would have stopped a rebuild dead.

**This distinction is the whole of the regeneration contract at the head of this
document**, and the 2026-09-21 repair set listed there is its most recent worked
example: seven tool-code fixes, zero of them data, therefore all seven re-earned
free by a rebuild while every defect still recorded as BYTES replays untouched.

### 7.1.1 The six that must be ported, by name

A rebuild that runs without these silently drops six decisions, four of which
exist nowhere but the ledger file.

1. **`crossing-no-flatten`** -- over-water crossings are generated and cleared
   but carry `flatten = FORBIDDEN`, so a terrain write cannot remove the water
   the piles stand in. Evidence: ledger **seq 8-9, 25-43**;
   `crossings/assemble.py:545-568`; `crossings/ledger_ops.py:14-17`.
   **Porting:** carry the `flatten=FORBIDDEN` flag *plus* each crossing's
   `zones_generate` / `objects_clear` / `spawn_plan` records into the new ledger.
   Without it a generic terrain pass recreates the floating-pier defect.
2. **`world-manifest-environment`** -- seq 0 pins the seed, the modifier set and
   120 mod DLLs by digest, and `replay.py:345-373` treats a mismatch as FATAL.
   **Porting:** recreate the exact seed / modifier / mod set, or accept that
   preflight refuses. See 7.1.2 -- this one is already broken.
3. **`live-location-position-correction`** -- settlements resolve
   `LocationProxy` positions from a LIVE census, never from a seed-derived dump,
   because the mod-free dump's coordinates differ (section 3). Evidence:
   `settlements/build.py:61-103`. **Porting:** re-run the live marker census on
   the new world **before any site is placed**.
4. **`crossing-road-ownership-and-clear-policy`** *(ledger-only)* -- fords and
   causeways are RoadNet **terrain**; bridges are Crossings **structures**; and
   POI clear behaviour is per-operation: Crossings **clip**, RoadNet **omit**,
   SettleBuild **refuse**. Evidence: ledger **seq 4-6, 46-47**. The code enforces
   the consequences; the ownership decision exists only as ledger notes.
   **Porting:** copy it into planning data, or it gets re-litigated.
5. **`skipped-redundant-or-unbuilt-segments`** *(ledger-only)* -- **T11** omitted
   because it is **T10 reversed**; **S13** and **S14** omitted because their
   endpoint structures were never built. Evidence: ledger **seq 253, 352-353**.
   There is no reusable rule for this anywhere in `roads/`. **Porting:** copy the
   omission decisions, or re-prove duplication and absent endpoints from scratch.
6. **`adhoc-ordering-experiment`** *(ledger-only, and UNPORTABLE)* -- the
   earliest terrain write was made by **ad-hoc RCON** and its exact data entry was
   never appended as a `terrain_write`. The only named tool is
   `/tmp/gt/verify_ordering.py`, which is not a repo artefact and no longer
   exists. Evidence: ledger **seq 2**. **Porting: impossible. It must be redone,
   and this time recorded.** This is schema limitation 8 (out-of-band writes)
   arriving as an actual cost.

The fourth ledger-only item is the **seq-52 pre-settlement backup provenance**:
the record of which backup the settlement phase was allowed to proceed from.

### 7.1.2 `replay.py preflight` refuses TODAY, against the world it recorded

This is the most important single fact in section 7, and it was measured rather
than assumed. MEASURED today by `writer.fingerprint("Ulfsland", heavy=True)`
against the ledger's own seq 0:

```
ledger seq 0 :  120 DLLs, digest f57ed005401fe7fbcfc3121729f0716fa33b6e3025142d915b68ac48c41ff10f
live Ulfsland:  119 DLLs, digest 6cf9b9b89ec48d47a8933c88f7e87646e56c5f2ab695eb03a7cafc164fd65b19
ulfsland-dn   :  117 DLLs, digest 1eae8fb3...                    (reported by ReplayProof)

removed since seq 0 (2):  CarryWeightSkill/CarrySkill.dll
                          NeuralyzeWorldSeed/NeuralyzeWorldSeed.dll
added   since seq 0 (1):  RavenwoodVikingHouses/RavenwoodVikingHouses.dll
changed since seq 0 (23): AdventureBackpacks, AzuCraftyBoxes, Basements, BuildOnShip,
                          EpicLoot, EverybodyShim(patcher), Infinity_Hammer, Jotunn,
                          More_World_Locations_AIO, OdinShip, OdinsKingdom,
                          RavenwoodCurrency, RossItemDrawers(x2), ServersideQoL(x2),
                          ServersideQoL_AutoStore, Valheim10Compatibility(x2),
                          Wisdom, ZenWorldSettings, Zen.ModLib, BalrondConstructions
```

The seed, world version, worldGenVersion and game-build anchor all still match
(`Pirate68` / 147627509 / 41 / 2 / `1231fc2f...`). **The mod set does not.** 26 of
120 entries moved inside the two-day build window.

Two consequences, and they are not the same consequence:

- **`replay.py preflight` will answer REFUSE against Ulfsland right now.** That
  is the gate working, not the gate malfunctioning. Plan for it: a rebuild begins
  by reconciling the mod set, and "preflight passed first time" is not a
  realistic expectation.
- **`More_World_Locations_AIO.dll` is one of the 23 changed.** That is the mod
  that decides where ~190 POI types land, and it is precisely the drift the
  schema's `mods` comparison was written for: *"a pad solved with a clearance to
  a location that no longer exists (or now does) is a pad that deletes location
  content."* A rebuild must re-run the live marker census (ported item 3) and
  must not assume a pad solved in the old world is still clear in the new one.

`NeuralyzeWorldSeed.dll` being **removed** is benign and expected: it is the
creation-time seed-forcing plugin (4.2), deployed for the generation and gone
afterwards. A rebuild re-deploys it and it will appear in the *new* header.

### 7.2 Worked examples of each class

**The rebuild blocker found during the replay proof (the 23rd), now tool code:**

- `tools/jumpstart/terraform/rcon.py` hard-coded
  `CONTAINER = "valheim-server-Ulfsland"` and the Ulfsland RCON config path, and
  `replay.py:552-555` restarts that constant **by name**. So the entire toolchain
  **could not address a rebuild target at all** -- every command a replay sent
  would have gone to Ulfsland regardless of which world it was replaying into.
  Now parameterised by `VALHEIM_WORLD`, with the Ulfsland default unchanged.
  Found and fixed 2026-09-18 by `ReplayProof` while standing up the throwaway
  world. **If you are reading a copy of this toolchain from before that change,
  check this first: it is silent, and it writes into the reference world.**

**Re-earned free (in tool code):**

- `terraform/flatten.py apply` refuses to write a compiler into a zone with no
  `_ZoneCtrl` at its centre, and refuses **before writing even the data-entry
  file**, because a stale entry in the watched data directory is a loaded gun for
  the next run. MEASURED to fire: a pad at (0,0) in ungenerated zone (0,0)
  exited 1 and wrote nothing.
- `terraform/locations.py` gates every flatten on the live marker set with the
  authority ordering of section 3.
- `tcdata.Compiler` defaults `operations` to 2 and writes
  `m_lastOpPoint` = the zone centre at pad height with `m_lastOpRadius` = 32 m.
  The old value of 1 meant `1 == 0 + 1`, so `TerrainComp::CheckLoad` took the
  narrow branch and called `ClutterSystem.ResetGrass` on a **zero-sized box at
  the world origin** -- which is why the operator's grass stayed floating, and
  why it recurred on every reload (`m_lastDataRevision` starts at 0 again on each
  newly instantiated component).
- `schema.validate` enforces the four write-time invariants: `expect` mandatory;
  flatten FORBIDDEN under an over-water structure; one compiler per zone
  (`merged_from` or refuse); a portal tag has exactly two ends.
- `live.py` no longer appends on `--dry`. MEASURED: `clear.py emit --dry`, run
  only to find out whether the POI gate would refuse, left `observe` seq 933 in
  the chain under `RoadClear`'s name for work `RoadEmit` was doing.
- `roads/structures.py` classifies every prefab explicitly and **raises** on an
  unknown one, because defaulting would put a building in NATURE or a tree in
  STRUCTURE, and both have cost a wrong answer.

**Further SITE-LEVEL one-offs, beyond the six of 7.1.1.** These are rulings and
accepted residuals attached to one place in one world. They live in `note`
records and in segment metadata; no tool enforces any of them, and a rebuild that
does not carry them forward will either re-litigate them or re-introduce the
defect they close:

- The T4 ruling of step 5: pad wins inside the footprint, road grades into the
  pad edge. It is a *ruling*, recorded as a `note`; no tool enforces it.
- The `water_edge` clip on S7: a **7.749 m transverse step** that is a recorded
  clip and is **permanently closed**, because the wall is correct and grading it
  would be the defect.
- The S2 bridge abutment's recorded **0.37-0.44 m crest**, deliberately not
  smoothed because the last 1.5 m sits inside the bridge's protected footprint.
  That crest is the PRICE of the protection.
- `ferry-terminal-eastisle` is **deliberately absent** from the road network: its
  root (1951.5, 556.5) sits on land component 273, 0.396 km2, a SEPARATE island,
  so it is portal-only and the operator reaches it by portal and rides the ferry
  back. "Why is there no road to the far ferry terminal" must have an answer, and
  this is it.
- The `zones_generate --empty` decision at the 118-registration zone (3.1), with
  its declared permanent cost.

---

## 8. Index of refusals

Everything in this document that a future session should decline to re-attempt,
with the number that closed it.

| Refusal | Number |
| --- | --- |
| Re-roll the seed for Deep North proximity | fixed geometry; 1,031 seeds, `dn_near` 7,908-8,100 m, zero exceptions |
| Re-roll for traders or early bosses on the spawn island | 25 of 30 curated seeds already have all three traders and five altars there |
| Rank seeds on `dn_sail` | 4,922 / 12,397 / 27,444 m for the same route at 128 / 64 / 32 m |
| Rank seeds on `dn_walkable` as a boolean | true in 29 of 30 |
| Use `h > 0` as the land test | put two witness pads 12 m under water, quoted four times |
| Use the coarse seedscan grid to decide a height | river-free; 10 of 13 solved sites below the water plane, worst -15.8 m |
| Use an 8 m grid to decide flatness of a 32 m footprint | correct 18% of the time |
| Quote an island count without its resolution | 7,794 at 4 m vs 294 at 16 m, same terrain |
| Use a per-column run as the corridor metric | under-reads by 848 m (2,800 vs 3,648) on `Pirate68` |
| Resolve a site against `plan.json` | 12,301 entries, all `placed: false`; 20-74 m vs 0.3-6.9 m |
| Rely on a mod-free dump for POI keep-outs | 60 of 65 live markers at one site were invisible to it |
| Use a single global POI stand-off | per-type measured reach differs from the declaration by up to 3x |
| Trust `zones_generate` to fix an ungenerated-looking zone | disjoint target sets; "0 zones: 448 skipped by the command" |
| Probe a zone with a 32 m disc | 31.9 m is the widest disc wholly inside a 64 m zone |
| Carve a mountain pass | terrain writes clamp at +/-8 m against the generated height |
| Build a bridge over 96 m | measured structural ceiling; a 174.7 m crossing became a ferry |
| Build a road out of pieces | `piece_pavedroad` has no persistent `ZNetView` |
| Write terrain below y=30 under a structure | the "early dock" defect; treated as unrepairable |
| Re-derive replay commands from `params["pos"]` | would mirror the whole world across u=v |
| Key a projection on ZDO id | ids are reassigned on load; the offset is not even constant |
| Order a ledger by `seq` | not a total order past file line 47; use FILE ORDER |
| Union terrain priors earliest-first | would restore seq 213's road under seq 286's pad |
| Ask `objects_count id=*` over a generated zone | `Total: 6149` + one line per prefab; wedged the server's stdout |
| Batch more than ~64 pieces per record | a 1,255-piece record wedged the pipe and stopped the main thread |
| Verify a backup by its exit code | two zero-exit archives contained nothing and were the newest for the world |
| Verify a world generation with a success check | three different checks each destroyed a correctly generated world |
| Read the log to decide the server is ready | heartbeat is 600 s; use a `players` probe |
| Expect RCON on a freshly created world | `m_chunkedSave` is false; restart once |
| Park a non-world directory in `worlds_local` | server hangs forever in the start scene, no log line names it |
| Trust a mod's version string | 7.7.8 and 7.7.7 ship a byte-identical DLL |
| Assume every prefab in a record still registers | `mm_large_wall`: `Error: Entity id mm_large_wall not recognized` |
| Compute anything load-bearing across two eval cells | the Python kernel is SHARED between subagents; globals `W`, `fl`, `f`, `B`, `F` were rebound mid-run, with no error and no signal |
| Let a timeout produce a measurement | a 6,000-FRAME wait produced a complete, well-formed TSV in which every prefab read `znet=0` -- the exact opposite of the truth, from an output with no error in it |
| Expect `replay.py preflight` to pass first time | it REFUSES against Ulfsland today: 120 -> 119 DLLs, 2 removed, 1 added, 23 changed inside the two-day build window |
| Assume the toolchain can address a rebuild target | `terraform/rcon.py` hard-coded `CONTAINER = "valheim-server-Ulfsland"`; every replay command would have gone to the reference world |
| Make a world-mutating change outside the ledger | ledger seq 2's ad-hoc RCON terrain write is UNPORTABLE: the data entry was never recorded and `/tmp/gt/verify_ordering.py` is gone |
| Assume a pad solved in the old world is still clear in the new one | `More_World_Locations_AIO.dll` is one of the 23 changed DLLs, and it decides where ~190 POI types land |
| Re-run the placement pass to fix a partial replay | ungated re-send makes 176 of a prefab whose correct count is 174; use `project.py repair`, MEASURED 15,680 -> 15,682 with zero duplicates |
| Assume a builder has a read-before-write gate | the idempotency audit found one MISSING from 10 of 19 mutating entrypoints |
| Optimise the placement pass | placements are 27.7 s of a 2,247 s replay; `objects_clear` is 1,874.6 s = 83% |
| Expect a 100% replay of this ledger | the honest ceiling was measured at 15,682 / 15,683 (`mm_large_wall` is unregisterable, confirmed by three instruments) -- and that figure is `[UNVERIFIED]` since the fold key defect: the fold emits 19,777 survivors (5.5.4) |
| Read a fidelity percentage as a correctness score | it measures AGREEMENT WITH THE RECORD: a faithfully replayed defect scores 100%, and the 52 duplicates the fold removes score as 52 misses (5.5.4) |
| Treat a regeneration as a repair mechanism | the fold re-emits the RECORDED BLOB and does not re-derive the surface, so a replay reproduces the wrong-surface grading on 21 of 25 segments, the S1 pothole, the four bridge abutments and 286 m of ghost road; the fixes that survive are the ones in the TOOLS (the regeneration contract at the head of this document) |
| Assume a regeneration repairs a defect for free | true of the 52 colliding duplicates, FALSE of the ghost road (0 of 5 zones' surviving blobs belong to a retired write; dropping seqs 213/1693/1779 changes nothing, the bytes are in seq 2053), and ACTIVELY DESTRUCTIVE while the key was too coarse: 11,861 of 12,550 collapses joined pieces at DIFFERENT y, worst dy 38.00 m (5.5.4) |
| Fold on a key that cannot tell two real objects apart | `project.py:97` keyed on `qkey(prefab, u, v)` while the docstring claimed world position; a rebuild through it was a 36% demolition and Ulfsgard's sea gate replayed halved, 6 `fb_fortress_portcullis` -> 3. The key is 3D now; the post-fix counts are `[UNMEASURED]` (5.3.1, 5.5.4) |
| Ask only who OWNS a sample | owner says the live road owns the ghost ground and finds nothing wrong; over 55 ghost samples OWNER is 2053 x44 / 1946 x11 while AUTHOR is 1693 x30 / 1779 x10 / 213 x3 (5.5.4) |
