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

**Five bodies. That is the entire committable set out of 538 catalogued
blueprints**, and the honest answer to "search online for more blueprints" is
that the web has plenty of blueprints and almost no licences. The 2026-09-15
sweep added 362 bodies and did not move that number by one.

Two sources found on 2026-09-15 **would** move it, and are recorded rather than
taken — see [§ 5](#5-permission-granted-not-yet-taken-2026-09-15).

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

### 1. The fleet's own corpus — 87 manifest rows, all `body: reference`

87 rows, not 86: `classify.py` now emits one row per distinct BODY rather than
per file name, and MEASURED over the two directories below,
`PuP_Minicastle.blueprint` is **two different castles** — 352,256 bytes /
3,334 rows in `old_Storgard` against 404,763 / 3,810 in `bp_old`, different
SHA-256. The old `max(…, key=size)` rule discarded the smaller one silently.
Of the 26 names present in both directories, 24 are byte-identical, 2 are a
0-byte stub beside the real body, and that one is two real bodies.

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
pulls them into `staging/` from the pinned commit.

`sighsorry1029/Homestead` @ `40fd385` (GPL-3.0, MEASURED via the GitHub licence
API) is the same reasoning and **is now fetched**: 4 sample blueprints, all 4
`PLACES_CLEAN`, all 4 `grounds`, none carrying a station. Copyleft, so
local-use-only; worth referencing because an unambiguous local-use grant beats
a body with no licence at all.

### 3. No licence stated — 421 rows across six sources

| Source | Pinned | Files | What makes it worth referencing anyway |
|---|---|---|---|
| [`OverDrive/BiomeBlueprints`](https://thunderstore.io/c/valheim/p/OverDrive/BiomeBlueprints/) | version `1.0.3`, archive sha256 `36c5a04df802…` | **353** | The **largest referenceable catalogue found**: 78 generated house designs and 275 community builds by 89 builders, biome-sorted, with ground flattening captured per body. MEASURED: 223 `grounds`, 68 `grounds_on_posts`, 62 `DEFECT_on_land`; **77 are `PLACES_CLEAN`, ground as captured, and carry a station.** Not a git host, so it is pinned by version + archive hash and `web.py` refuses to stage if that hash moves. |
| [`Oosquai/SavheimIV`](https://github.com/Oosquai/SavheimIV) | `ddb8895` | 44 | Brokkr's **broken-bridge set** — three designs, each full and short, 41–94 m spans — plus a graded ruins family. The best bridge and ruin content found anywhere. |
| [`RustyMods/VikingNPC`](https://github.com/RustyMods/VikingNPC) | `bf7bbce` | 11 | Whole **settler and raider towns**, one per biome, plus a tower and two ruins. Definitively 1.0-era: Ashlands prefabs present. 11 of 11 `PLACES_CLEAN`, 10 of 11 `grounds`. |
| [`offsetkeyz/valheim_mod_sync`](https://github.com/offsetkeyz/valheim_mod_sync) | `f079c75` | 9 (8 rows — `salty-dick-cottage-final` merged with the corpus copy) | Portal hubs and portal pyramids, a curved bridge, and the byte-identical `salty-dick-cottage-final` that pins the fleet corpus's origin. |
| [`mcarvall/Midgard-Valheim-Server`](https://github.com/mcarvall/Midgard-Valheim-Server) | `ebb14b0` | 4 | Four Rocket Raccoon bodies — Quick House, Barracas Media, Casa de Campo, Beliche. `Rocket Raccoon_Quick_House` is the **top-ranked `pre-eikthyr` body in the whole library**: 157 pieces, 11.2 m, 0.0% air, workbench + cooking station, pure Meadows material. MEASURED: `license: null` on the GitHub API and no LICENSE file in the root tree. |
| [`okeanz/LootGoblinsUtils`](https://github.com/okeanz/LootGoblinsUtils) | `d586297` | 1 | `Relicv_Raven_V2`, shipped as a Conquest location asset. MEASURED: `license: null`, no LICENSE file. |

**BiomeBlueprints' terms, read in full before any body was taken.** MEASURED on
the 1.0.3 archive itself (711 members): there is **no LICENSE file**, and the
strings `licen` and `copyright` occur **zero times** in its `README.md`. The
only permission language is its submission form, verbatim:

> "It also asks your permission to ship it — the mod redistributes the file, so
> that is the part that lets it."

That is a grant from each of the 89 credited builders **to `lg_9d`, for that
package**. It does not reach downstream consumers, and the README's own closing
line — *"If one of these is yours and you would rather it were not included, or
credited differently, say so and it will be changed or removed"* — is a courtesy
policy, not a licence. **Referenceable. Never committed.**

### 4. Sources surveyed and closed

| Source | Position | Evidence |
|---|---|---|
| **valheimians.com** | Closed. 1441 downloadable builds (MEASURED on the live downloadable-filtered index) — the largest catalogue in existence — behind terms that expressly forbid redistribution. | ToU ¶7 / ¶9, quoted above. |
| **Nexus Mods** | Closed. Every permissions block that could be read forbids reupload. | mods/701 and mods/1916, verbatim (MEASURED): *"Upload permission You are not allowed to upload this file to other sites under any circumstances"*; *"Asset use permission You must get permission from me before you are allowed to use any of the assets in this file"*. mods/1915 and others returned HTTP 403, so **no permission was inferred for them**. |
| **Thunderstore, other packages** | No other package ships bodies. `PlanBuild` and `Buildheim` are WTFPL but ship only test fixtures; `KGvalheim/Blueprint` 1.6.6, `sighsorry/Blueprint` 1.6.7 and `RustyMods/BlueprintPieces` 1.0.2 are blueprint **tools**, not body packs, and expose no licence field. | MEASURED package/experimental-API inspection, 2026-09-15. The full registry endpoint exceeds 50 MB, so per-package endpoints were used. |
| **Nexus, the dock family** | Closed, and this is where the over-water bodies are. mods/109 Floki's Dock, mods/173 TJLongDock, mods/131 MD's Dockhouse, mods/641 Frode's Dock, mods/1807 starter home, mods/1917 Long House. | Verbatim on every one (MEASURED): *"Upload permission You are not allowed to upload this file to other sites under any circumstances"*. |
| **Nexus, unreadable** | **Unknown — no permission inferred.** mods/897, mods/450, mods/1374, mods/463, mods/2321 returned HTTP 403 on the description tab. mods/1370 reads *"Mod unavailable / Removed by author"*. | MEASURED HTTP status. Public hosting is not a licence and a 403 is not a grant. |
| **Steam Community** | Closed, and it holds no bodies anyway. The Subscriber Agreement §6.A grants the UGC licence to *Valve* — *"you grant Valve and its affiliates the worldwide, non-exclusive right to use, reproduce, modify, create derivative works from, distribute…"* — not to downstream consumers. | MEASURED: 0 `.blueprint`/`.vbuild` attachments in the queried Valheim guide results; the useful ones are raster floorplans and material lists. |
| **PlanBuild server marketplace / BuildShare Discord** | Not a host-level source. PlanBuild's own code is WTFPL, which covers the mod and not uploader-authored bodies; the marketplace is per-server and not an enumerable index. | MEASURED: PlanBuild README — *"If a server has this feature enabled, upload your local blueprints to that server so others can download and build your creations as well."* |
| **GitLab / Codeberg / Gitea** | Nothing. Four targeted searches returned no repository with a verified body. A negative result, not proof of absence. | MEASURED, 2026-09-15. |
| **r/ValheimBuilds** | Showcases, not files. No licence. | Previously measured by the sibling sweep. |
| **GitHub, `.vbuild`** | **Zero** `.vbuild` bodies indexed anywhere. Every verified web body is `.blueprint`. | MEASURED: `extension:vbuild valheim` returns 0. |

### 5. Permission granted, not yet taken (2026-09-15)

These are the **first two web sources found whose authors expressly permit
re-upload.** They are recorded and not fetched, because taking them needs a
decision the operator owns and a credential this box does not have.

| Source | Verbatim permission, MEASURED | Body | Why it is not taken |
|---|---|---|---|
| [Nexus 1508](https://www.nexusmods.com/valheim/mods/1508) — Thysicus | *"Upload permission You can upload this file to other sites but you must credit me as the creator of the file"*; *"Asset use permission You are allowed to use the assets in this file without permission or crediting me"*; not in anything sold | 1 `.vbuild`, Medieval(ish) house, vanilla pieces only, version 1 | Needs a fourth `redistributable` value — `"yes-with-attribution"` — and the credit line has to live somewhere the repo keeps it. Nexus downloads are credential-gated. |
| [Nexus 648](https://www.nexusmods.com/valheim/mods/648) — Ser_Essovius | *"Upload permission You can upload this file to other sites but you must credit me as the creator of the file"*; *"Modification permission You must get permission from me before you are allowed to modify my files to improve it"* | `.vbuild`, Viking house **with attached workshop and dock**, version 1.5.1 — the only permissively-licensed over-water body found anywhere | Same, plus: **modification is forbidden without permission**, which rules out `--strip-unknown-sections` and `--drop-prefab` on this body. Verbatim re-hosting only. |

**[INFERENCE]**, and it is the operative reading: an explicit "you can upload
this file to other sites" is a redistribution grant, conditional on attribution.
If the operator wants them, the recipe is: fetch to `staging/`, add
`redistributable: "yes-with-attribution"` to `web.py`'s verdict vocabulary, and
commit under `bodies/nexusmods__1508/` **with the permission text vendored beside
the body**, exactly as `bygd`'s MIT notice sits beside its two files.

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

## Ten zero-byte corpus files — now marked unresolvable, not counted

Truncated downloads from whenever the corpus was fetched: `PuP_Owl-tree_house`,
`PuP_rampart1`, `PuP_stable`, `PuP_tavern`, `SerpentShip`, `SerpentShip_Empty`,
`ragnar-warehouse`, `salty-dick-mordach-castle-final`, `salty-dick-tower-test`,
`serpent`.

**They used to claim a body by SHA-256 and have none**, because
`classify.sha256()` returned `""` for a 0-byte file and the manifest carried that
empty string in a column whose contract is *"materialise.py fetches this at use
time and verifies the sha256 recorded here"*. They now carry `resolvable: false`
plus an `unresolved_reason`, `materialise.py --verify` prints them as
`UNRESOLVABLE` and counts them separately, and `locate()` refuses to resolve a
row with no hash. The empty hash stays empty deliberately — it is **not**
`e3b0c442…`, the valid SHA-256 of zero bytes, because writing that would make
`--verify` certify a truncated download as intact.

**No recovery route exists for any of the ten.** MEASURED, 2026-09-15, `find`
over the whole of `$VALHEIM_ROOT`: each has exactly one copy on disk and it is
0 bytes. `offsetkeyz/valheim_mod_sync` @ `f079c75` was re-checked — it carries
`salty-dick-cottage-final` (byte-identical to the intact corpus copy) and
`Salty Dick`'s portal hub and curved bridge, but **not**
`mordach-castle-final` or `tower-test`. No source found in the 2026-09-15 sweep
holds any of the ten.

Two of them were the only files of their type: **`PuP_rampart1`** (the sole pure
rampart/palisade) and **`PuP_stable`** (the sole stable/pen). The 362 bodies
added on 2026-09-15 cover neither, so **the library has no rampart and no
stable**, and that is now a stated gap rather than a row that looks filled.

The sibling `../blueprints/` manifest counts **12** zero-byte files and both
numbers are right: it rows one entry per file *per directory*, while this library
keys on CONTENT. `s-ren-dockhouse` (1225 pieces) and `salty-dick-cottage-final`
(877) are 0 bytes in `old_Storgard` and intact in `bp_old`, so they are recovered
here and lost there.
