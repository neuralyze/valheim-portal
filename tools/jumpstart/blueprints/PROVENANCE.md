# Blueprint corpus provenance and licence

**No licence is stated for any blueprint in this corpus, by any of its authors.
The bodies are therefore deliberately NOT vendored into this repository.**

This repo is published — it carries a `LICENSE`, a `NOTICE` and
`docs/public-distribution.md`, and it already handles third-party code by
*reference* (`deploy/upstream-sources.json` pins commits and licences rather
than copying source in). Committing ~112 files by named individual authors with
no licence grant would cut straight across that, and "do not publish this repo"
is not a constraint anyone can honour reliably — it takes one push.

So the repo carries the **manifest**, and `materialise.py` copies the bodies in
at use time.

```
data/corpus_manifest.json   identity + viability verdict for all 112 files
data/corpus_manifest.tsv    the same, one row per file, for reading
materialise.py              copy-from-source, verifying every SHA-256
```

## Where the corpus lives

```
/media/big4/projects/game/valheim/old/old/old/old_Storgard/config_merged/BepInEx/PlanBuild/blueprints   (69 .blueprint + 7 .vbuild + 60 .png)
/media/big4/projects/game/valheim/old/bp_old/config/default/bepinex_old/PlanBuild/blueprints            (30 .blueprint, older overlapping copy)
```

A path four `old`s deep is exactly what a future cleanup deletes. The manifest's
checksums are what will tell us that happened:

```bash
./materialise.py --verify-corpus
```

23 files appear in both directories with identical content; `materialise.py`
prefers the larger copy where they differ.

## Who made them

Every file's author is recorded in the manifest `creator` field, read from the
blueprint's own `#Creator:` header. The named authors include Olivanderr, PuP,
Пипкин, Arty, Randominis, Salty Dick, Sören, God, Fox-, Jaik, Drake, BjOrN,
Aodi, Halvar, Hiccup, Deardly, Cooties, Enslaver, YoU DiEd, Хельс, Торкель,
Азура Тираэцу, Creator Looney and Peeds.

The header shape (`#Name:`/`#Creator:`/`#Category:`/`#Center:`/`#Coordinates:`)
is PlanBuild's, and the author names are of the form used on community
blueprint-sharing sites, so the corpus was almost certainly downloaded from one
of those during an earlier era of this fleet. **Individual authors retain
copyright. Treat every file as local-use-only.**

## Why downloading more was not the answer

Two read-only scouts surveyed Thunderstore, Nexus Mods, valheimians.com,
r/ValheimBuilds and GitHub. The result, on licence alone:

| Source | Finding | Licence |
|---|---|---|
| OverDrive **BiomeBlueprints** 1.0.3 (Thunderstore) | Genuinely the best web candidate: 353 designs organised by biome, Meadows 31 / Black Forest 33 / Swamp 21 / Mountain 16 / Plains 42 / Mistlands 33 / Ashlands 89+45, vanilla prefabs only, updated 2026-08-27. **No Deep North tab.** Declares PlanBuild 0.18.4 as a hard dependency. | **No licence stated.** Its contributor form asks authors for permission for *that mod* to ship a file, which is not a downstream grant. Unusable for redistribution. |
| valheimians.com build library | Actively updated, per-build downloads, includes a "Deep North 1.0 Ready Base" (2026-08-28). | **No licence stated**; platform terms leave copyright with individual uploaders. |
| Nexus "Community starter home vbuild and blueprint" (MutantArtCat) | One Meadows starter home, ships both `.vbuild` and `.blueprint`. | Explicit: *"You are not allowed to upload this file to other sites under any circumstances."* Unusable. |
| Other Nexus `.vbuild` packs | Mostly 2021–2022 BuildShare era. | No grant, or explicit prohibition. |
| GitHub repos containing `.blueprint` | A few redistributable sets exist. | None is a current, complete, tiered base set. |
| r/ValheimBuilds | 100 recent posts sampled: image, gallery and video showcases. | Reddit posts state no licence. |

So the web offered **nothing** that is both 1.0-era complete and
redistribution-safe — while the corpus already on this fleet's own disk is
larger, measured, and 88/112 place clean on 1.0.12. That is the better source,
and it is why this ended up as a manifest rather than a download list.

## Exploit payloads — do not select these by name

Three files are loot-spam payloads, not buildings. They are marked
`payload: exploit` in the manifest.

| File | Author | What it actually is |
|---|---|---|
| `14464-Valheimian.blueprint` | Торкель | 491 loot objects, 488 of them `TreasureChest_mountaincave`, scattered from y −5064 to +5088 |
| `gnome_house.blueprint` | — | byte-identical content to the above |
| `15643-Valheimian.blueprint` | Азура Тираэцу | 361 loot objects: 329 `TreasureChest_mountaincave`, 23 `TreasureChest_dvergrtown`, 3 `TreasureChest_forestcrypt` |

Two further files hand out free progression rather than chests and are worth
knowing about even though they place cleanly: `vcastle.blueprint` carries 102
`Pickable_DragonEgg`, and `PuP_megaCastle.blueprint` carries 28 core stands
(`Pickable_MoltenCoreStand`, `Pickable_SurtlingCoreStand`,
`Pickable_BlackCoreStand`).

`to_rcon_plan.py` strips loot, creature and forage objects **by default**;
`--keep-loot` / `--keep-creatures` / `--keep-forage` opt back in deliberately.

## Twelve zero-byte files

Twelve entries are 0 bytes — truncated downloads from whenever the corpus was
fetched. They carry an empty `sha256` and verdict `EMPTY`:
`PuP_Owl-tree_house`, `PuP_rampart1`, `PuP_stable`, `PuP_tavern`,
`SerpentShip`, `SerpentShip_Empty`, `ragnar-warehouse`, `s-ren-dockhouse`
(the copy in `old_Storgard`; the `bp_old` copy is intact at 1225 pieces),
`salty-dick-cottage-final` (likewise), `salty-dick-mordach-castle-final`,
`salty-dick-tower-test`, `serpent`.
