# Library provenance and licence position

This repository is **published** — it carries a `LICENSE`, a `NOTICE` and
`docs/public-distribution.md`, and it already handles third-party material by
*reference* rather than by copying. So the rule for this library is a single
line, and it is not negotiable:

> **A blueprint body may be committed only if its source states a licence that
> permits redistribution on terms this repo can meet. Everything else is
> referenced by manifest and fetched at use time.**

"It is on a public host" is not a licence. "The author posted it for people to
use" is not a licence. Absence of a statement is **not** permission.

Claims below are marked **MEASURED** (read on the live page, or computed on this
box) or **INFERRED**.

## What is committed, and why it is allowed

| Path | Files | Source | Licence | Why committable |
|---|---|---|---|---|
| `bodies/JereKuusela__valheim-infinity_hammer/` | 3 | [valheim-infinity_hammer](https://github.com/JereKuusela/valheim-infinity_hammer) @ `ad56b79` | **Unlicense / public domain** — MEASURED, `LICENSE` vendored beside them: *"This is free and unencumbered software released into the public domain."* | Public domain. No conditions to meet. |
| `bodies/constXife__bygd/` | 2 | [bygd](https://github.com/constXife/bygd) @ `1bb0bf7` | **MIT** — MEASURED, `LICENSE` vendored beside them: *"Permission is hereby granted … to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies"* | MIT permits redistribution with the notice retained, which is why the notice sits in the same directory. |

**Five bodies. That is the entire committable set out of 176 catalogued
blueprints**, and the honest answer to "search online for more blueprints" is
that the web has plenty of blueprints and almost no licences.

### The one caveat on the MIT pair, stated plainly

`bygd`'s `LICENSE` reads `Copyright (c) 2026 constxife`, while both blueprint
bodies declare `#Creator:PiNoKi` (MEASURED from their headers). A repository
licence can only license what its owner owns. If `constxife` is not `PiNoKi`,
the MIT grant does not reach these two files. This is recorded rather than
resolved — it cannot be resolved without asking them — and the files are small
enough (48 and 182 pieces) that dropping them costs nothing if the operator
would rather not carry the ambiguity. `redistributable: "yes"` in `web.py` is
the single switch to flip.

## What is deliberately NOT committed

### 1. The fleet's own corpus — 86 manifest rows, all `body: reference`

Where it lives (MEASURED):

```
/media/big4/projects/game/valheim/old/old/old/old_Storgard/config_merged/BepInEx/PlanBuild/blueprints
/media/big4/projects/game/valheim/old/bp_old/config/default/bepinex_old/PlanBuild/blueprints
```

The sibling `../blueprints/PROVENANCE.md` records "no licence stated for any
file by any author". **This library upgrades that finding from *silence* to
*express prohibition*, and names the source.**

Three independent measurements point the corpus at **valheimians.com**:

1. The corpus contains `Portal11.vbuild` … `Portal15.vbuild`, five tiny
   single-portal gateway structures. valheimians.com carries
   [*Decorative Portal Buildings 11–15*](https://www.valheimians.com/build/decorative-portal-buildings-11-15/)
   by **Artyfacts**: *"Five new decorative builds for a single portal"*, 1.3k
   downloads (MEASURED). The corpus also holds `arty-magetower` and
   `arty-dragontavern`, both `#Creator:Arty`.
2. `hiccup-simplewoodbeambridge.blueprint` (`#Creator:Hiccup`) matches
   [*Simple Wood Beam Bridge*](https://www.valheimians.com/build/simple-wood-beam-bridge/)
   by **HiccupTheHermit**, 896 downloads (MEASURED). `Cooties`, another corpus
   `#Creator`, has *Midsomer Bridge* on the same site.
3. `salty-dick-cottage-final.blueprint` in the corpus is **byte-identical**
   (sha256 `6b588d9c…`) to the copy in the public repo
   `offsetkeyz/valheim_mod_sync` (MEASURED on this box), and that repo also
   carries builds by `Пипкин` and `Salty Dick` — two more corpus `#Creator`
   names.

And valheimians.com's own Terms of Use are explicit (MEASURED, verbatim):

> General Site Use ¶7 — users agree not to *"Redistribute any content,
> including data, provided by us in any manner whatsoever including by means of
> printed publication, fax broadcast, web pages, e-mail, web newsgroups or
> forums, or any other electronic or paper-based service or method"*.
>
> ¶9 — nor to *"Embed or import any site data provided by us into any
> information services … data files or application software … except as …
> specifically permitted in writing by Valheimians."*

Its upload clause grants a *non-exclusive, world-wide, royalty free, perpetual,
non-revocable license* **to the site**, not to downstream consumers.

So the corpus is not merely unlicensed: redistributing it would cut directly
across the terms under which it was obtained. **Local use only. Never committed.
Never redistributed.** `materialise.py` copies it in at use time and verifies
every SHA-256.

### 2. AGPL-3.0 — 21 rows, licensed but incompatible

[`RickardAndreasAgren/ValheimBlueprintCruncher`](https://github.com/RickardAndreasAgren/ValheimBlueprintCruncher)
@ `8fbda70`, **AGPL-3.0** (MEASURED via the GitHub licence API and the `LICENSE`
file). It is the best non-base find on the entire web: 21 blueprints by
`#Creator:Raimod`, four structure families each in intact / damaged / ruined /
remnant states, **21 of 21 `PLACES_CLEAN` on 1.0.12**, none over 585 pieces, none
over 19 m across.

AGPL-3.0 permits redistribution but only if the combined work is itself AGPL.
This repo is not, so the bodies stay out. The licence still buys something real:
**local use is unambiguous**, unlike everything in category 1. `web.py --fetch`
pulls them into `staging/` from the pinned commit. Same reasoning applies to
`sighsorry1029/Homestead` (GPL-3.0, 4 base samples, not fetched).

### 3. No licence stated — 64 rows across three repos

| Source | Commit | Files | What makes it worth referencing anyway |
|---|---|---|---|
| [`Oosquai/SavheimIV`](https://github.com/Oosquai/SavheimIV) | `ddb8895` | 44 | Brokkr's **broken-bridge set** — three designs, each full and short, 41–94 m spans — plus a graded ruins family. The best bridge and ruin content found anywhere. |
| [`RustyMods/VikingNPC`](https://github.com/RustyMods/VikingNPC) | `bf7bbce` | 11 | Whole **settler and raider towns**, one per biome, plus a tower and two ruins. Definitively 1.0-era: Ashlands prefabs present. 11 of 11 `PLACES_CLEAN`. |
| [`offsetkeyz/valheim_mod_sync`](https://github.com/offsetkeyz/valheim_mod_sync) | `f079c75` | 9 | Portal hubs and portal pyramids, a curved bridge, and the byte-identical `salty-dick-cottage-final` that pins the fleet corpus's origin. |

### 4. Sources surveyed and closed

| Source | Position | Evidence |
|---|---|---|
| **valheimians.com** | Closed. 1441 downloadable builds (MEASURED on the live downloadable-filtered index) — the largest catalogue in existence — behind terms that expressly forbid redistribution. | ToU ¶7 / ¶9, quoted above. |
| **Nexus Mods** | Closed. Every permissions block that could be read forbids reupload. | mods/701 and mods/1916, verbatim (MEASURED): *"Upload permission You are not allowed to upload this file to other sites under any circumstances"*; *"Asset use permission You must get permission from me before you are allowed to use any of the assets in this file"*. mods/1915 and others returned HTTP 403, so **no permission was inferred for them**. |
| **Thunderstore** | No licensed blueprint pack exists. `PlanBuild` and `Buildheim` are WTFPL but ship only test fixtures; `OverDrive BiomeBlueprints` 1.0.3 (353 designs by biome, no Deep North tab) states no licence, and its contributor form licenses *that mod*, not downstream users. | MEASURED package/source inspection. |
| **GitLab / Codeberg** | Nothing. Searches for Valheim `.blueprint` bodies returned only unrelated projects. | MEASURED. |
| **r/ValheimBuilds** | Showcases, not files. No licence. | Previously measured by the sibling sweep. |
| **GitHub, `.vbuild`** | **Zero** `.vbuild` bodies indexed anywhere. Every verified web body is `.blueprint`. | MEASURED: `extension:vbuild valheim` returns 0. |

## Exploit and hazard payloads — do not select these by name

Carried forward and extended. Three corpus files are loot payloads, not
buildings; the manifest marks them `kind: exploit` and their verdict is prefixed
`EXPLOIT/`.

| File | What it actually is |
|---|---|
| `14464-Valheimian.blueprint` | 491 loot objects, 488 `TreasureChest_mountaincave` |
| `gnome_house.blueprint` | byte-identical to the above, under an innocuous name |
| `15643-Valheimian.blueprint` | 361 loot objects; also names 4 mod-only pieces including turrets |

Three more hand out progression or wreck performance while placing cleanly, and
are flagged in their manifest `gaps` column:

| File | Hazard (MEASURED) |
|---|---|
| `vcastle.blueprint` | 102 `Pickable_DragonEgg` |
| `PuP_megaCastle.blueprint` | 28 core stands |
| `gold_castle.blueprint` | **2177 `blackforge_ext1` anvils used as wall cladding** — 12% of the build. A ZDO hazard *and* an unearned crafting-station level bonus. Not previously catalogued. |
| `magic-tower.blueprint` | 1853 `piece_groundtorch_mist` — 31% of the build is lit torches |
| `PuP_Minicastle.blueprint` | 1408 blank `sign_notext` used as decorative panels |
| `god-shadowstone-fortress.blueprint` | 464 signs + 448 itemstands |
| `arty-magetower.blueprint` | 558 itemstands |
| `citadel.blueprint` | 361 unlit castle torches |

`../blueprints/to_rcon_plan.py` strips loot, creature and forage objects by
default. It does **not** strip decorative ZDO spam — that is a judgement call the
operator should make per build.

## Ten zero-byte corpus files

Truncated downloads from whenever the corpus was fetched. They carry an empty
`sha256` and `kind: empty`: `PuP_Owl-tree_house`, `PuP_rampart1`, `PuP_stable`,
`PuP_tavern`, `SerpentShip`, `SerpentShip_Empty`, `ragnar-warehouse`,
`salty-dick-mordach-castle-final`, `salty-dick-tower-test`, `serpent`.

The sibling manifest counts **12**, and both numbers are right: it rows one entry
per file *per directory*, while this library keys on filename and takes the
larger copy. `s-ren-dockhouse` (1225 pieces) and `salty-dick-cottage-final` (877)
are 0 bytes in `old_Storgard` and intact in `bp_old`, so they are recovered here
and lost there.

For three of the ten there is a live recovery route: `offsetkeyz/valheim_mod_sync`
@ `f079c75` carries builds by the same `Salty Dick`, and its
`salty-dick-cottage-final.blueprint` is byte-identical to the intact corpus copy
(MEASURED). It does not hold `mordach-castle-final` or `tower-test`, but it is
the right place to look first, and `Salty Dick`'s portal hub and curved bridge
there are better finds than the truncated files anyway.

Two of them are the only files of their type in the whole corpus and are worth
re-fetching first: **`PuP_rampart1`** (the sole pure rampart/palisade) and
**`PuP_stable`** (the sole stable/pen). Nothing else covers either category.
