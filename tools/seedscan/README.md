# seedscan — measure Valheim world seeds with the game's own generator

`seedscan` samples biomes and terrain heights for a list of seeds by calling
**Valheim's own `WorldGenerator`** inside the real dedicated-server process, then
scores the seeds offline. No reimplementation of world generation, no
third-party seed-map website, no guesswork about noise functions.

Built for one question — *which seed puts Deep North closest to spawn and
reachable by water?* — but the grid dump is generic and reusable for any
seed-selection question (Mistlands proximity, biome mix near spawn, how much
ocean a world has, and so on).

## Why it has to run inside the game

`WorldGenerator.GetBiome` depends on `UnityEngine.Mathf.PerlinNoise`, and the
generator's per-seed offsets come from `UnityEngine.Random.InitState` /
`Random.Range`. Both are **native internal calls implemented in
`UnityPlayer.so`**, so:

| Host | Result |
| --- | --- |
| stock `mono` + the game's managed DLLs | `cant resolve internal call to "UnityEngine.Mathf::PerlinNoise"` → `MissingMethodException` |
| Unity Doorstop entrypoint (`Doorstop.Entrypoint.Start`) | same failure — the entrypoint runs *before* Unity registers its icalls |
| **BepInEx plugin `Awake()`** | **works** — runs on the Unity main thread after the player is initialised |

So `SeedScan.cs` is a BepInEx plugin. It scans, writes its grids, and hard-kills
the process before Unity ever reaches `ZNet`, port binding or world loading.

## Layout

There are two scanners, answering two different questions.

| File | Role |
| --- | --- |
| `SeedScan.cs` | BepInEx plugin; drives `WorldGenerator` — **terrain**: biomes + heights. ~1 s/seed |
| `LocScan.cs` | BepInEx plugin; drives `ZoneSystem` — **placed content**: boss altars, traders, dungeons, the start temple. ~60 s/seed |
| `build.sh` | compiles both into one `SeedScan.dll` with `mcs` against the game's own assemblies |
| `run_scan.sh` | sandbox + inject + run the terrain scanner over a seed list |
| `run_locscan.sh` | sandbox + inject + boot one throwaway world per seed for the location scanner |
| `gen_seeds.py` | emits candidate seed strings using the game's own alphabet |
| `analyze.py` | scores and ranks `.biome` grids (terrain) |
| `analyze_loc.py` | scores the `.json` dumps (bosses, traders, Deep North content) |

**`WorldGenerator` does not know where anything is.** It only produces terrain.
Boss altars, the three traders, dungeons, runestones and the start temple are
placed by `ZoneSystem`, a separate rejection-sampling pass that needs the game's
location *prefabs* loaded from the asset bundles and runs as a Unity coroutine.
That is why `LocScan` has to let the server actually boot a world, and why it
costs a minute a seed instead of a second.

## Running it

```bash
# 1. candidate seeds (control first so every result is comparable)
tools/seedscan/gen_seeds.py 1000 --rng 20260914 --include 8JiFcknsJd > /tmp/seeds.txt

# 2. scan. VH_SRC is the dir holding valheim_server.x86_64 + UnityPlayer.so + BepInEx
VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
SEEDS=/tmp/seeds.txt \
OUT=/tmp/seedscan/out \
STEP=32 HEIGHT=1 \
  tools/seedscan/run_scan.sh

# 3. rank on terrain
tools/seedscan/analyze.py /tmp/seedscan/out --top 20 --csv /tmp/seedscan/out.csv
tools/seedscan/analyze.py /tmp/seedscan/out --map 8JiFcknsJd     # ASCII biome map
tools/seedscan/analyze.py /tmp/seedscan/out --top 10 --sail      # + water-route estimate
tools/seedscan/analyze.py /tmp/seedscan/out --top 10 --walk      # + land-route estimate

# 4. placed content: bosses, traders, Deep North locations (~60 s per seed)
VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
SEEDS=/tmp/seeds.txt OUT=/tmp/seedscan/loc \
  tools/seedscan/run_locscan.sh
tools/seedscan/analyze_loc.py /tmp/seedscan/loc --detail 8JiFcknsJd
```

`run_scan.sh` never writes to the live install. It `rsync`s the server tree into
`$SANDBOX` (default `/tmp/seedscan/vh`), copies in **only** `BepInEx/core` plus a
plugins directory containing nothing but `SeedScan.dll`, and launches from there
with `-port 3456` and its own `-savedir`. The live server's mods, configs, worlds
and profiles are read-only inputs.

Knobs: `STEP` (grid spacing, m), `EXTENT` (half-extent sampled, default 10496 —
the world is a disc of radius 10000 with a water edge at 10500), `HEIGHT=1`
(also dump float32 terrain heights — required for correct land/water),
`PREGEN=1` (also run `Pregenerate()`, i.e. lakes and rivers).

Cost per seed, measured, 21 x 21 km sampled:

| STEP | cells | HEIGHT=0 | HEIGHT=1 | HEIGHT=1 PREGEN=1 |
| --- | --- | --- | --- | --- |
| 128 | 26 896 | 22 ms | ~40 ms | ~3 000 ms |
| 64 | 107 584 | — | 322 ms | 3 297 ms |
| 32 | 430 336 | — | 1 036 ms | — |

1001 seeds at `STEP=32 HEIGHT=1` took 17 minutes wall clock and 2.1 GB of grids.

**Use `STEP=32` or finer for water connectivity.** `STEP=64` and `STEP=128`
both mis-report `spawn_coast_dist`: beach cells (Ocean biome, height above 0)
sever narrow channels, so the world sea fragments and spawn looks far inland.
Measured on 14 finalist seeds, `spawn_coast_dist` agreed within 10 m between
32 m and 16 m sampling, but 64 m inflated it to a floor of ~1005 m on 8 of the
14. `dn_biggest_km2` was identical at 16 m and 32 m on all 14.

## What it measures

Per seed `analyze.py` reports:

- `dn_near` — straight-line distance from `(0,0)` to the nearest Deep North cell.
- `spawn_coast_dist` (`toSea`) — straight-line distance from `(0,0)` to the
  nearest cell of the **world sea** (the largest connected body of water).
  Inland lakes are excluded; this is how far you must get overland before a boat
  helps.
- `dn_reach_near` (`dnLanding`) — straight-line distance to the nearest Deep
  North beach that is *on that same sea*. This is the reachability headline.
- `dn_sail` (`sail~`) — 8-connected Dijkstra estimate of the actual water route.
  **Indicative only** (see below).
- `dn_km2`, `dn_reach_km2`, `dn_biggest_km2` — total Deep North land, the part
  that touches the world sea, and the single largest contiguous landmass.
- the same set for Mistlands (`ml_*`) and Ashlands (`as_*`).
- `near2k_*` — cell counts per biome within 2 km of spawn.

Ranking is `spawn_coast_dist + dn_reach_near`, gated on water reachability, with
a penalty for a sliver-sized largest Deep North landmass and a partial-weight
bonus for convenient Mistlands / Ashlands.

### Distances in sailing terms

1 world unit = 1 metre. Converting to travel time needs a ship speed, which this
tool does **not** measure — treat any minutes figure as external to these
results.

## What this does NOT prove

Read this section before quoting a number.

1. **Grid sampling, not geometry.** Everything is point samples on a square
   lattice. A channel narrower than `STEP` can be missed entirely, and an isthmus
   narrower than `STEP` can be missed just as easily. Water connectivity is
   therefore approximate in *both* directions.
2. **`dn_sail` is resolution-unstable.** Measured on seed `8JiFcknsJd`, the same
   route came out 4 922 m at 128 m spacing, 12 397 m at 64 m and 27 444 m at
   32 m. Finer sampling opens new channels *and* resolves new blocking land. Rank
   on the straight-line metrics; treat `sail~` as an order of magnitude.
3. **`dn_walk_path` IS resolution-stable, unlike `dn_sail`.** Land is ~63% of
   the map, so land connectivity barely moves with sampling. Measured on the
   same seeds at 32 m vs 16 m: `Pirate68` 8 129 → 8 116 m, `8JiFcknsJd` 24 338 →
   24 295 m, `spawn_land_km2` identical. Rank on the walk, not the sail.
4. **`dn_walkable` as a boolean is worthless.** It is `True` in 29 of 30
   curated seeds. Valheim's world rim is near-continuous land (measured 88–91%
   land coverage in the 9 600–10 000 m radius band) and the Ashlands landmass
   wraps the south, so nearly every continent touches every other somewhere.
   Only the geodesic distance discriminates — and a "walkable" route may still
   cross Ashlands, which is not a route a player can use.
5. **`analyze.py` assumes spawn is `(0,0)`; `analyze_loc.py` does not.** The
   real start temple is wherever `ZoneSystem` put it. Measured on `8JiFcknsJd`:
   `StartTemple` sits at `(61.0, 196.8)`, i.e. **206 m** from the origin. So the
   terrain metrics carry a few-hundred-metre error that the location metrics do
   not. Where the two disagree, trust `analyze_loc.py`.
6. **Terrain difficulty is ignored in the straight-line metrics.** `toSea` is a
   straight line. `dn_walk_path` does at least follow land, and the biome mix
   along it can be sampled, but neither accounts for cliffs, slope or hostiles.
7. **Ashlands land area needs `HEIGHT=1`.** `GetBiome` tests `IsAshlands`
   *before* its water test, so with biomes alone the whole southern sea reports
   `AshLands`. Deep North is the opposite — its water test runs first — so
   `DeepNorth` cells are always land, with or without heights.
8. **LocScan reports where locations were PLACED, not what is inside them.** It
   reads `m_locationInstances` after generation. It does not know a dungeon's
   interior layout, loot, or which creatures spawn. `m_placed` is almost always
   `false` in a fresh world because that flag means "the prefab has been
   instantiated in a loaded zone", which only happens once a player visits;
   positions are final regardless.
9. **Trader and boss counts are instance counts, not uniqueness.** Valheim
   places several altars per boss (measured on `8JiFcknsJd`: 3 Eikthyr, 4 Elder,
   5 Bonemass, 3 Moder, 4 Yagluth, 5 Queen, 3 Fader, 3 Deep North boss) and 10
   camps each for Haldor, Hildir and the Bog Witch. `analyze_loc.py` reports the
   NEAREST of each from the real spawn; there are others further out.
10. **No ship, no frost.** Deep North has no entry key and needs no special ship,
   but it does need frost resistance and, for its content chain, Ashlands
   materials. Proximity is not readiness.
11. **One game version.** Measured against Valheim 1.0.12. World generation has
   changed between versions before; re-run after a game update rather than
   trusting a stored ranking.
12. **Community claims are not measurements.** Where this tool and a blog post
   disagree, the tool measured it and the blog did not. But claims about a
   trader's *inventory*, a dungeon's *contents*, or how "fun" a layout is are
   outside what either scanner can see.

## Measured facts about Deep North placement

These came out of the IL and are worth knowing before optimising a seed:

- `WorldGenerator.IsDeepnorth(x, y)` is **seed-independent**:
  `magnitude(x, y + 4000) > 12000 + 100 * sin(20 * atan2(x, y))`.
  Minimising radius subject to that gives **exactly 8000 m due north**. No seed
  can put Deep North closer to spawn than ~8 km, and measured over 1001 seeds
  `dn_near` ranged only 7908–8100 m.
- Ashlands is the mirror image: `ashlandsMinDistance = 12000`,
  `ashlandsYOffset = -4000`.
- The `sin(20 * theta)` term makes 20 shallow lobes, so the Deep North boundary
  dips closer to spawn at some bearings than others — but only by ~±100 m at the
  nearest point.
- Consequently the useful seed differentiators are **not** "how far is Deep
  North" but: how far spawn is from the sea, whether Deep North land actually
  sits at the near edge of its region, how big the largest Deep North landmass
  is, and how convenient Mistlands and Ashlands are.
- Sea level is `0` in `WorldGenerator.GetHeight` units. Measured on
  `8JiFcknsJd`: `Ocean`-biome cells span −400.0 … +4.0 m with a median of exactly
  0.0, while the lowest `Meadows` cell is +6.1 m. So `height <= 0` is water and
  `Ocean`-biome cells above 0 are beaches.
- Skipping `Pregenerate()` is safe for this work. `GetBiome` reads only
  `m_offset0/1/2/4`, `maxMarshDistance`, `minDarklandNoise` and `GetBaseHeight`,
  all assigned before the `Pregenerate()` call. Verified empirically: biome grids
  for `8JiFcknsJd` at 128 m are **byte-identical** with and without it, and with
  heights the river data changes 5 964 cell heights by up to 76 m while changing
  the water mask on **zero** cells.

## What 1001 seeds actually showed

Scanned at `STEP=32 HEIGHT=1`, control `8JiFcknsJd` included:

| metric | min | p10 | median | p90 | max | control |
| --- | --- | --- | --- | --- | --- | --- |
| `dn_near` (m) | 7908 | 7908 | 7908 | 7929 | 8030 | 7908 |
| `dn_reach_near` (m) | 7908 | 7927 | 8001 | 8128 | 8349 | 8127 |
| `spawn_coast_dist` (m) | 93 | 811 | 1002 | 1052 | 5119 | 1005 |
| `dn_km2` | 20 | 22 | 24 | 26 | 28 | 26.0 |
| `dn_reach_km2` | 19 | 22 | 24 | 26 | 28 | 26.0 |
| `dn_biggest_km2` | 2 | 3 | 4 | 6 | 8 | 6.7 |
| `home_sea_km2` | 130 | 148 | 154 | 158 | 163 | 155.1 |
| `ml_reach_near` (m) | 5902 | 5905 | 5920 | 5960 | 6246 | 5922 |
| `as_reach_near` (m) | 7908 | 7908 | 7980 | 8147 | 9164 | 8122 |

**All 1001 of 1001 seeds had Deep North water-reachable from the spawn sea.**
Deep North proximity is therefore not something a seed re-roll can buy: the
region's inner edge is fixed geometry and every world has land on it.

Two more consequences of the biome gates, both seed-independent, both relevant
to any "put players right before boss N" work:

- `GetBiome` gates each biome on radius from `(0,0)`. Swamp needs
  `dist > 2000` flat (`minMarshDistance`); Plains needs
  `dist > 3000 + 100*sin(20*theta)` (`minHeathDistance`); Mistlands needs
  `dist > 6000 + 100*sin(20*theta)` (`minDarklandDistance`); Black Forest needs
  `dist > 600 + 100*sin(20*theta)` (`minDeepForestDistance`). So no seed can put
  Swamp inside 2 km of spawn or Plains inside ~2.9 km. Measured: across all 14
  finalist seeds the 2 km disc around spawn held Meadows, Black Forest and
  Mountain cells and **zero** Swamp or Plains cells.
- Mistlands' and Ashlands' nearest water-connected coast are near-constants too
  (~5.9 km and ~7.9-8.1 km), so they are not differentiators either.

## What 30 curated community seeds showed

A second population: every named seed from the BisectHosting "Top 10 Best
Valheim 1.0 Seeds" (Sep 8 2026) and All Things How "Best World Seeds to Start in
the Deep North" (Sep 10 2026) lists, plus `vaoxsr` / `18mang` from KeenGamer,
measured at `STEP=32 HEIGHT=1` plus a full `LocScan` pass. These seeds are
*selected* for virtues, so they are the strongest available test of whether the
structural findings survive curation. They do.

- **Deep North distance is still fixed.** `dn_reach_near` spanned 7 910–8 298 m
  across all 30. Nothing broke the ~8 km geometry. Combined with the 1 001
  random seeds: **1 031 seeds, zero exceptions.**
- **Deep North *content* is also fixed.** Every seed had all 17 Deep North
  location types and 992–999 DN location instances: 30 `DN_gammeltrollFrac01`,
  40 `morkborg`, 40 `icepond`, 40 `thehole01`, ~45 `NorthVillage`. The Deep
  North progression chain is fully present in every world. "Is the Deep North
  big enough to hold its locations" is answered: yes, always.
- **The Deep North boss is fixed too.** `bosslocation` (biome mask `DeepNorth`,
  i.e. Kall Fimbulbringer) sat 8 497–9 762 m from spawn. `FaderLocation`
  (Ashlands) 8 488–9 345 m. `Mistlands_DvergrBossEntrance1` (the Queen)
  6 545–6 994 m.
- **"All three traders on the starting island" is not a differentiator.** 25 of
  30 seeds had all three trader camps AND all five early boss altars on the
  spawn landmass, with the furthest trader 2 946–4 762 m away in *every* seed.
  Valheim places 10 camps each for Haldor, Hildir and the Bog Witch, so the
  nearest is always close. Community lists sell this as special; it is normal.
- **What DOES vary, a lot:**

  | metric | range over the 30 | why it matters |
  | --- | --- | --- |
  | `dn_walk_path` | 8 129 – 27 604 m | the land route north; 3.4x spread |
  | `b5_same` (furthest early boss on the spawn landmass, on foot) | 3 125 – 6 785 m | early progression travel |
  | `spawn_coast_dist` | 182 – 3 007 m | how soon a boat is useful |
  | `spawn_land_km2` | 33 – 228 km² | buildable home continent |
  | `dn_biggest_km2` | 2.7 – 7.8 km² | largest single DN landmass |

- **The land corridor is the real prize.** `Pirate68` walks to Deep North in
  8 129 m against an 8 031 m straight line — a detour factor of **1.01**, a
  near-perfect northern corridor, and the biome sequence along it is Meadows →
  Black Forest → Swamp → Plains → Mistlands → Mountain → Deep North with **no
  Ashlands**. The control `8JiFcknsJd` needs 24 338 m, 39% of it beach-hugging.
  That is the one thing a re-roll genuinely buys, and it is exactly what the
  community claim about that seed said.
- **Trade-off worth knowing:** a continuous land bridge north blocks the sea
  lane. `Pirate68`'s water route is the worst of the finalists (15 161 m at
  16 m sampling) precisely because its land corridor is in the way.
