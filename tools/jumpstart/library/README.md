# Jumpstart blueprint library

**538 catalogued blueprints, 528 of them resolvable and hash-verified.** `../blueprints/` picks
**one base per progression tier**; this directory is the rest of the world — bridges, gateways,
portal shrines, watchtowers, refineries, gatehouses, market rows, ruins — and it says out loud
which of them you can stamp twenty times, which are one-offs, and **which of them actually sit
on flat ground**.

Claims are marked **MEASURED** (computed on this box, or read on the live page), **DERIVED**
(computed from another measured field, with its input named) or **INFERRED**.

```
classify.py          re-mine the fleet's own corpus under a role lens
web.py --fetch       download every pinned body into staging/ (gitignored)
web.py               measure them: viability, footprint, tier, category
build_manifest.py    merge both halves -> data/library_manifest.{json,tsv}
audit_bases.py       does each body SIT on a flat pad? -> data/base_audit.{json,tsv}
audit_bases.py --shortlist   the ranked per-preset pick list
materialise.py       put chosen bodies on disk, verifying every SHA-256
bodies/              the 5 bodies whose licence permits committing them
staging/             gitignored; refilled by web.py --fetch
```

Run order from scratch:

```bash
./classify.py && ./web.py --fetch && ./web.py && ./build_manifest.py
./materialise.py --verify   # 528 verified, 0 drifted, 0 unavailable, 10 unresolvable
./audit_bases.py --json data/base_audit.json --tsv data/base_audit.tsv
```

## Identity: one handle, one body

`name` is the library HANDLE and it is **unique**. Two things make that true, and each of them
fixed a bug that had already produced a wrong measurement:

* **Byte-identical bodies from two sources are ONE row**, with both listed in `origins`.
  `salty-dick-cottage-final` is sha `6b588d9c46e2` from both the fleet corpus and
  `offsetkeyz/valheim_mod_sync` (MEASURED). Counting it twice inflated the library by a row no
  consumer could ask for separately.
* **Two different bodies sharing a file name are SUFFIXED with their own content hash**, so the
  handle is a function of the bytes and not of directory order. MEASURED, there are two such
  names and only one of them was known: `brokkr-the-cathedral` is an 8,269-piece and an
  8,240-piece cathedral, and **`PuP_Minicastle` is a 3,334-row and a 3,810-row castle** whose
  smaller half was being silently discarded by `max(…, key=size)` while a name-keyed resolver
  downstream returned it. `build_manifest.py` now **raises** if two rows share a handle.

`source_name` is the file name at the source — what `materialise.py` looks for on disk, and
what `placements.yaml` and `hammer_blueprint` use. `locate_by_filename()` accepts either and
**raises, naming both handles**, when a source name covers two bodies.

## `resolvable: false` — 10 rows that are names, not bodies

Ten corpus files are 0 bytes and, MEASURED by `find` over the whole of `$VALHEIM_ROOT`, have
exactly one copy each and it is that empty one. They carry `resolvable: false`, an empty
`sha256` and an `unresolved_reason`. The empty hash is deliberate and is **not** `e3b0c442…`,
the valid SHA-256 of zero bytes: writing that would make `--verify` certify a truncated
download as intact. **Nothing should count them as bodies.**

`PuP_rampart1` was the corpus's only pure rampart and `PuP_stable` its only stable. No source
surveyed to date holds either, so the library has no rampart and no stable.

## The headline numbers (MEASURED)

| | |
|---|---|
| Manifest rows | **538** |
| Resolvable bodies, every one hash-verified | **528** |
| Rows that are a name with no body (`resolvable: false`) | **10** |
| Non-base rows | **402** |
| Repeatable modules | **260** |
| Bodies committed to git | **5** — everything else is referenced. See `PROVENANCE.md` |
| Metadata `curated` (a human verdict) / `derived` (computed) | **176** / **362** |
| Web candidates prefab-resolved | **452**: 418 `PLACES_CLEAN`, 17 `PLACES_WITH_GAPS`, 17 `UNUSABLE` |
| Corpus rows prefab-resolved | **87**: 67 `PLACES_CLEAN`, 7 `PLACES_WITH_GAPS`, 3 `EXPLOIT/…`, 10 unresolvable |

## Does it sit on flat ground? `audit_bases.py`

This is the question the catalogue could not answer until now, and it is the one the operator
asked: *"blueprints that are mostly flat are best unless they are for building over water..
then the bases are often uneven"*. Two rules, not one.

| `flat_pad_verdict` | rows | meaning |
|---|---|---|
| `grounds` | **332** | sits on the pad as captured |
| `grounds_on_posts` | **98** | air, but never more than 6 m from support: piles, arches, a balcony |
| `expected_over_water` | **9** | uneven base AND a dock/bridge — right body, water site |
| `DEFECT_on_land` | **89** | uneven base on a land site. **This is the defect.** |
| `no_body` | **10** | `resolvable: false` |

`setting` is the MEASURED half (air fraction, worst gap, isolation). `intended_setting` is
DERIVED FROM `category`, because geometry cannot supply intent: MEASURED, Olivanderr's `dock` is
70.1% floating with 13.42 m of isolation, arithmetically the same shape as a hillside castle.

**Relief is not the test.** MEASURED: `PuP_megaCastle` has 86.2 m of base relief and grounds,
because all of its spread is foundation. `air_isolation_max_m` is the test — the distance from
the worst floating column to the nearest one that reaches the pad. `drake-lighthouse` 33.3% air
/ 2.00 m isolation is a tower on legs; `BjOrN_blueprint001` 34.1% air / 33.29 m isolation needs
a hillside. See `docs/proposed/2026-09-15-blueprint-library-expansion.md`.

## Categories

`category` answers *what is it for*, which is the question the base-oriented
first pass never asked:

| category | meaning | rows |
|---|---|---|
| `flavour` | landmarks, ruins, shrines, markets, statues, settlements, furniture | 236 |
| `base` | somewhere to live | 133 |
| `production` | forges, refineries, smelting and workshop yards | 68 |
| `portal` | hubs, pyramids, gateway arches, roadside shrines | 56 |
| `defence` | palisades, gatehouses, holds, fortresses, hostile camps | 15 |
| `outpost` | watchtowers, shelters, rest stops, forward camps | 13 |
| `bridge` | spans and crossings | 12 |
| `dock` | harbours and piers | 2 |
| `fixture` | Infinity Hammer's own parser-contract files; not for the world | 3 |

For the 362 rows from bulk sources the category is **DERIVED** from measured station, portal and
bed counts rather than curated by hand — see `web.py:derive_catalogue`. Those rows carry
`metadata: derived`, and their `role` and `fit` text contains only measured numbers.

`kind` answers *can I use it more than once*:

* **`module`** — small, legible, stampable. 260 rows, bounded at 600 pieces and 35 m span, both
  taken from the hand-curated set: every row a human called `module` is inside both.
* **`setpiece`** — a one-off showpiece. 265 rows.
* **`exploit`** — a loot or progression payload dressed as a building. 3 rows.
* **`empty`** — a 0-byte truncated download, `resolvable: false`. 10 rows.

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

MEASURED across all 538 rows: **369 files carry an unknown section and 22,014 data
rows are discarded.** 353 of those files and 21,533 of those rows are
`OverDrive/BiomeBlueprints`, every body of which ships a PlanBuild `#Terrain`
block — and **every one of its 21,533 rows is exactly 8 fields, shape token
`square`, with 0 rows reaching the 13 fields a piece row needs** (MEASURED). So
the whole pack loses its captured ground flattening on both routes and cannot
inject a phantom `spawn square` on either.

Of the rest, the heaviest are `RustyMods/VikingNPC`'s settlements — 187
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
for all 538 rows, and anything that trips it is rejected outright rather than
fixed up.

## What this library does NOT guarantee

1. **That a blueprint suits the spot.** Coordinates are owned by
   `../blueprints/solve_placements.py` and `worlds/*/placements.yaml`. This
   library records a *footprint* and a *fit* — biome, tier, purpose — and nothing
   more. `fit` is a curated judgement, not a solved site.
2. **That `PLACES_CLEAN` means it looks right.** The verdict is exactly one
   claim: every prefab name resolves against Valheim 1.0.12's own asset bundles
   or a deployed mod. It says nothing about whether the build clips the terrain,
   or needs ground that does not exist where you put it. **Whether it FLOATS is
   now answered** — by `audit_bases.py`'s `flat_pad_verdict`, and by nothing else
   in this directory.
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
6. **Permanence of the sources.** Every web row is pinned to a commit — or, for
   the one packaged release, to a version plus the SHA-256 of the archive — but
   `staging/` is gitignored and the corpus lives four `old`s deep in a path a
   future cleanup will delete. `./materialise.py --verify` is how you find out
   that happened.
7. **A licence.** 533 of 538 bodies are not in this repo, and most of them may
   not be redistributed at all. Read `PROVENANCE.md` before copying anything
   anywhere.
8. **That a `derived` category is right.** 362 rows have their category, kind,
   role and fit computed from measured station/portal/bed counts, not read by a
   human. The measurements are sound; the labels on top of them are a heuristic,
   and `intended_setting` inherits that uncertainty.
