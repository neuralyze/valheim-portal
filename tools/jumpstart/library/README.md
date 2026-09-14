# Jumpstart blueprint library

176 catalogued blueprints for Ulfsland. `../blueprints/` picks **one base per
progression tier**; this directory is the rest of the world — bridges, gateways,
portal shrines, watchtowers, refineries, gatehouses, market rows, ruins — and it
says out loud which of them you can stamp twenty times and which are one-offs.

Claims are marked **MEASURED** (computed on this box, or read on the live page)
or **INFERRED**.

```
classify.py          re-mine the fleet's own 86-file corpus under a role lens
web.py --fetch       download 90 web candidates from pinned commits
web.py               measure them: viability, footprint, tier, category
build_manifest.py    merge both halves -> data/library_manifest.{json,tsv}
materialise.py       put chosen bodies on disk, verifying every SHA-256
bodies/              the 5 bodies whose licence permits committing them
staging/             gitignored; refilled by web.py --fetch
```

Run order from scratch:

```bash
./classify.py && ./web.py --fetch && ./web.py && ./build_manifest.py
./materialise.py --verify        # 166 verified, 0 drifted, 10 zero-byte
```

## The headline numbers (MEASURED)

| | |
|---|---|
| Catalogued blueprints | **176** |
| Non-base rows | **124** |
| Repeatable modules | **83** |
| Bodies committed to git | **5** — everything else is referenced. See `PROVENANCE.md` |
| Web candidates downloaded and prefab-resolved | **90**: 60 `PLACES_CLEAN`, 13 `PLACES_WITH_GAPS`, 17 `UNUSABLE` |
| Corpus files prefab-resolved | **86**: 66 `PLACES_CLEAN`, 7 `PLACES_WITH_GAPS`, 3 `EXPLOIT/…`, 10 zero-byte |

## Categories

`category` answers *what is it for*, which is the question the base-oriented
first pass never asked:

| category | meaning | rows |
|---|---|---|
| `base` | somewhere to live | 49 |
| `flavour` | landmarks, ruins, shrines, markets, statues, settlements | 65 |
| `defence` | palisades, gatehouses, holds, fortresses, hostile camps | 15 |
| `outpost` | watchtowers, shelters, rest stops, forward camps | 13 |
| `bridge` | spans and crossings | 12 |
| `portal` | hubs, pyramids, gateway arches, roadside shrines | 10 |
| `production` | forges, refineries, smelting and workshop yards | 7 |
| `dock` | harbours and piers | 2 |
| `fixture` | Infinity Hammer's own parser-contract files; not for the world | 3 |

`kind` answers *can I use it more than once*:

* **`module`** — small, legible, stampable. 83 rows. A 63-piece footbridge that
  can cross twenty streams is worth more to this world than a 33 000-piece
  citadel that can go in one place and then dominates it.
* **`setpiece`** — a one-off showpiece. 80 rows.
* **`exploit`** — a loot or progression payload dressed as a building. 3 rows.
* **`empty`** — a 0-byte truncated download. 10 rows.

Query it:

```bash
./build_manifest.py --category bridge portal outpost --modules-only
./build_manifest.py --category flavour --print | grep ruin
```

### `top_tier` reads `tier(count)`, and the count is the point

`PuP_forge1` reads `ashlands(2)`. That is **not** an Ashlands build — it is a
343-piece wood workshop with two ashwood trim pieces, and swapping them for
plain wood makes it Meadows-placeable. Contrast
`aodi-skardvakt-the-black-tower` at `mistlands(355)`: 355 of its 358 pieces are
black marble, so it genuinely cannot exist before the Mistlands. The tier alone
would have told you neither.

## Placing one: the admin-client route

MEASURED, from disassembling the **deployed**
`Ulfsland/data/bepinex/BepInEx/plugins/Infinity_Hammer/InfinityHammer.dll`:
`HammerBlueprintCommand::LoadFiles` enumerates `*.blueprint` **and** `*.vbuild`
with `SearchOption.AllDirectories`, from both `<game>/BepInEx/config/<folder>`
and `Paths.ConfigPath/<folder>`, where `<folder>` is the config key *"6.
Blueprints" / "Blueprint folder"*, default value the literal string `PlanBuild`.
PlanBuild itself is not installed and is not needed.

All building happens from the **`ulfsland-admin`** seat: `hammer_restore`'s
handler calls `Hammer::Equip()`, which calls `Helper::GetPlayer()`, searches that
player's inventory and throws `Unable to find the hammer.` if there is none.
There is no headless path through Infinity Hammer.

```bash
# 1. put the bodies where the mod looks
./materialise.py --out '/path/to/ulfsland-admin/BepInEx/config/PlanBuild' \
  --category bridge portal outpost --modules-only --verdict PLACES_CLEAN

# 2. in game, as admin, hammer in hand
hammer_blueprint speeds-bridge2      # load it onto the hammer, then place
hammer_restore   speeds-bridge2      # or drop it at its own saved coordinates
```

`materialise.py` refuses to copy anything whose SHA-256 disagrees with the
manifest, so a corrupted or swapped source is caught before it reaches the game.

## Placing one with nobody online: the RCON route

`../blueprints/to_rcon_plan.py` converts a blueprint into one ValheimRcon
`spawn <prefab> <x> <y> <z> -rotation <x> <y> <z>` per object. MEASURED, `spawn`
resolves the prefab from `ZNetScene`, instantiates it, calls
`ZNetView::FinishGhostInit()` and destroys the GameObject — a persistent ZDO with
no live object, which is why it works against an empty server. Position and
rotation only: no scale, no object data, no WearNTear, no terrain.

**Materialise with `--strip-unknown-sections` before using that route.** Why, in
full:

MEASURED from the same disassembly, `GetPlanBuild` is a five-state machine
(`None, Pieces, SnapPoints, TerrainHeight, TerrainPaint`) initialised to
`Pieces`. On an unrecognised `#Header` whose second character is not whitespace,
it sets the section to **`None`**, and every row after that is dropped until the
next recognised header. The upstream author's own fixture says so in as many
words — `bodies/JereKuusela__valheim-infinity_hammer/terrain-future-section.blueprint`
carries `#Description:Unknown sections must not inherit the preceding parser
state`.

PlanBuild writes a `#Terrain` section — rows
`shape;x;y;z;radius;rotation;smooth;` — which is exactly such an unknown header.
So on the admin-client route those rows cost nothing beyond **losing the captured
ground flattening**, which you must then do by hand.

A reader that instead *keeps* the current section reads them as pieces named
`circle` and `square`. `../blueprints/inventory.py` does that, and
`to_rcon_plan.py` sits downstream of it, so the headless route would emit
`spawn circle …`. Worse, the bundle-token viability check cannot catch it:
`circle` and `square` both occur as ASCII tokens inside the game's own asset
bundles, so the files still score `PLACES_CLEAN`.

MEASURED across all 176 rows: **18 files carry an unknown section and 481 data
rows are discarded.** The heaviest are `RustyMods/VikingNPC`'s settlements — 187
rows in `BlackForestRaiderTown1`, 93 in `PlainsRaiderTown1`, 73 in
`MeadowRaiderTown1` — then seven corpus files totalling 16 rows, of which 8 are
in `aodi-skardvakt-the-black-tower`. PiNoKi's two builds carry four unknown
*metadata* headers each with no rows beneath them, so they lose nothing.

**No pieces are lost.** In every one of those files the block sits immediately
before a recognised header, and `pieces_after_unknown` equals the full piece
count for all of them. The single exception is
`terrain-future-section.blueprint`, whose `future_payload` row sits under
`#FuturePieceData` and *must* be dropped — which is exactly what this parser
does, and is the reason that fixture is committed: it is the upstream author's
own contract, and passing it is the proof that the state machine above is
modelled correctly rather than guessed at.

```bash
./materialise.py --out /tmp/plan --strip-unknown-sections --name MeadowSettlerTown1.blueprint
# MeadowSettlerTown1.blueprint: stripped 49 rows, demoted 1 headers
```

The strip keeps unknown *headers*, demoted to `# comment` form — PiNoKi's builds
carry `#LayoutProfile:`, `#TableZone:`, `#BedZone:` and `#DoorZone:` with no rows
beneath them, and that metadata is worth keeping. Only data rows are removed, and
the original SHA-256 is written into the first line.

The legacy `#Height`/`#Paint` terrain format is a different matter: Infinity
Hammer **throws** on it — *"Legacy #Height/#Paint terrain format is no longer
supported."* No file in this library uses it; `legacy_terrain_rejected` is false
for all 176 rows, and anything that trips it is rejected outright rather than
fixed up.

## What this library does NOT guarantee

1. **That a blueprint suits the spot.** Coordinates are owned by
   `../blueprints/solve_placements.py` and `worlds/*/placements.yaml`. This
   library records a *footprint* and a *fit* — biome, tier, purpose — and nothing
   more. `fit` is a curated judgement, not a solved site.
2. **That `PLACES_CLEAN` means it looks right.** The verdict is exactly one
   claim: every prefab name resolves against Valheim 1.0.12's own asset bundles
   or a deployed mod. It says nothing about whether the build floats, clips the
   terrain, or needs ground that does not exist where you put it.
3. **That the viability check has no blind spots.** The evidence set is every
   ASCII token in the bundles, which is a *superset* of the real prefab table.
   It can therefore produce false *clean*, never false *missing*. `circle` and
   `square` are the known case; assume there are others.
4. **Terrain.** Neither route restores a `#Terrain` block. `#TerrainHeight:` /
   `#TerrainPaint:` are restored by Infinity Hammer only, and no file in this
   library carries them except the three Unlicense fixtures.
5. **Material cost or build-station reach.** `top_tier` tells you the highest
   tier of material a build needs. It does not tell you whether the player has
   it, nor whether a workbench covers the whole footprint.
6. **Permanence of the sources.** Every web row is pinned to a commit and a
   SHA-256, but `staging/` is gitignored and the corpus lives four `old`s deep in
   a path a future cleanup will delete. `./materialise.py --verify` is how you
   find out that happened.
7. **A licence.** 171 of 176 bodies are not in this repo, and most of them may
   not be redistributed at all. Read `PROVENANCE.md` before copying anything
   anywhere.
