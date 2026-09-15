# The blueprint library was lying about its size, and nobody had measured the thing that matters

Status: **proposal + tooling only.** No world was written, no server was stopped or started, no
placement was made or removed, nothing was deployed, published, committed or `bd`-ed. No
`worlds/` file was touched. `tools/jumpstart/library/staging/` is gitignored and **no blueprint
body was added to git** — the committed set is still the same five files it was this morning.

Claims are **MEASURED** (computed on this host on 2026-09-15, or read verbatim on a live page)
or **[INFERENCE]** (reasoning from those measurements, not itself a measurement). Where a field
is computed from another field rather than from the bytes, it says **DERIVED** and names its
input.

---

## The headline, in the operator's own terms

The instruction was *"choose another blueprint. there are tons and tons. search online if you
need to. blueprints that are mostly flat are best unless they are for building over water..
then the bases are often uneven. keep going"*.

That last clause turned out to be the whole design problem. It is two rules, not one, and every
check that existed before tonight conflated them:

| | |
|---|---|
| Library rows, before | **176** claimed, **164 reachable** |
| Library rows, after | **538** rows, **528 resolvable and every one hash-verified** |
| Bodies vendored into git | **5**, unchanged. 533 of 538 are referenced by manifest + SHA-256 |
| Bodies that **ground on a flat pad as captured** | **332** MEASURED |
| Bodies that ground **on posts** — piles, arches, balconies | **98** MEASURED |
| Bodies uneven **and meant for water/a gorge** — correct, wrong site | **9** MEASURED |
| Bodies uneven **on a land site** — the actual defect | **89** MEASURED |
| Rows with no body at all, now marked so | **10** |

And the finding the parent needs first:

> **`jaik-mistvale-keep`, the current `pre-moder` pick, is a worse floater than the one the
> operator saw tonight.** MEASURED: 61.2% of its footprint stands clear of a flat pad against
> BjOrN's 34.1%, and 3,765 m³ of air against BjOrN's 3,414 m³. Placing `pre-moder` as written
> reproduces tonight's defect on a 40 m pad.

Verdicts for all thirteen currently-referenced bodies are in
[§ The nine sites as they stand](#the-nine-sites-as-they-stand).

---

## 1. Three catalogue defects, all of them the same mistake

Every one of these is *treating a file name as an identity*, and each had already produced a
confidently wrong answer.

### 1.1 Ten rows claimed a body by SHA-256 and had no body — fixed

`classify.sha256()` returned `""` for a zero-byte file, and the manifest carried that empty
string in a column whose contract is *"materialise.py fetches this at use time and verifies the
sha256 recorded here"*. Ten rows therefore claimed a hash they did not have, and every consumer
that counted rows over-counted the library by ten.

**MEASURED**, `find` over the whole of `$VALHEIM_ROOT` (`/media/big4/projects/game/valheim`):
each of the ten has exactly **one** copy on disk and it is **0 bytes**. There is no recovery
route — `offsetkeyz/valheim_mod_sync` @ `f079c75` was re-checked and carries none of them, and
no new source found tonight holds `PuP_rampart1` or `PuP_stable` either.

So they are **marked, not counted**: `resolvable: false`, `sha256: ""`, plus an
`unresolved_reason` that says which of the three states it is in. The empty string is
deliberate and is **not** `e3b0c442…`, the valid SHA-256 of zero bytes — writing that would
make `materialise.py --verify` certify a truncated download as intact.

```
$ ./materialise.py --verify
UNRESOLVABLE  PuP_rampart1.blueprint  zero-byte source: the only copy on disk is 0 bytes, so
there is no content to hash, materialise, parse or place. …
528 verified, 0 drifted, 0 unavailable, 10 unresolvable (no body exists)
```

The ten: `PuP_Owl-tree_house`, `PuP_rampart1`, `PuP_stable`, `PuP_tavern`, `SerpentShip`,
`SerpentShip_Empty`, `serpent`, `ragnar-warehouse`, `salty-dick-mordach-castle-final`,
`salty-dick-tower-test`. `PuP_rampart1` was the corpus's only pure rampart and `PuP_stable` its
only stable; the new bulk source below covers neither, so those two categories remain empty and
that is now stated rather than implied.

### 1.2 `brokkr-the-cathedral` is two buildings — and so is `PuP_Minicastle`, which nobody knew

The assignment named the cathedral. Auditing it found a **third** collision that had never been
catalogued, and that one was actively corrupting measurements.

**MEASURED**, over the two corpus directories, 26 file names appear in both:

| | count | example |
|---|---|---|
| byte-identical | 24 | `BjOrN_blueprint001` (186,313 B, `020d4a2a64a8`, both) |
| one real body beside a 0-byte stub | 2 | `s-ren-dockhouse`, `salty-dick-cottage-final` |
| **two different real bodies** | **1** | `PuP_Minicastle` |

`PuP_Minicastle.blueprint` is **352,256 bytes / 3,334 rows** in `old_Storgard` and **404,763 /
3,810** in `bp_old`, different SHA-256 (`dcbdd295f512` vs `556fd22e78ea`). `classify.collect()`
took `max(…, key=size)` under the comment *"12 files are 0-byte in one directory and intact in
the other"* — true of 12 names, wrong about this one — and silently discarded a 3,334-piece
castle. Worse: `survey_datum.resolve_bodies()` resolved by name in root order and returned the
**smaller** one, so the survey measured a body the manifest did not claim. Exactly the failure
shape the datum work was written to stop.

Three fixes, and they compose:

1. **`classify.collect()` emits one row per distinct CONTENT**, not per name. A 0-byte copy is
   the absence of a body rather than a distinct one, so it is only kept when the name has
   nothing else.
2. **`build_manifest.identity()`** gives every distinct body one unique handle. Byte-identical
   rows from two sources are **merged** into one row listing both in `origins`
   (`salty-dick-cottage-final`, sha `6b588d9c46e2` from both). Different bodies sharing a name
   are **suffixed with the first 12 hex of their own content hash**, so the handle is a function
   of the bytes and not of directory order. `build_manifest.py` now **raises** if two rows still
   share a handle.
3. **`materialise.locate()`** requires the body to hash to what the row claims, refuses
   zero-byte candidates outright, and refuses rows with no hash at all. `locate_by_filename()`
   accepts either handle or source name and **raises, naming both handles**, when a source name
   covers two bodies.

```
$ ./materialise.py --verify --name brokkr-the-cathedral.blueprint
brokkr-the-cathedral.blueprint names 2 DIFFERENT bodies -- ask for one by handle:
brokkr-the-cathedral__3060609d536b.blueprint, brokkr-the-cathedral__8127bac49f36.blueprint
```

`renamed_for_collision` and `merged_duplicates` are recorded in the manifest totals, so the
rename is auditable rather than mysterious.

**Net effect on the count, MEASURED:** 176 rows → 176 (one duplicate merged away, one hidden
castle recovered) → **166 resolvable bodies where 164 were reachable.** Two previously
unreachable buildings are now addressable: the second cathedral and the smaller Minicastle.

### 1.3 Two resolvers became one

`survey_datum.py` carried its own root-walking, name-keyed resolver. It is deleted; the module
now calls `library/materialise.locate()`. One resolver, hash-verified, with the zero-byte rule
in a single place. `blueprints/remove_placement.py` used `survey_datum.CORPUS_ROOTS` and was
switched to `locate_by_filename()` by its owner after I published the API over `hub`.

**MEASURED** after the swap: `survey_datum.py` surveys 166 bodies, 166 placeable, 0 refused, 0
parser errors. A diff of old-vs-new resolution over every row: 174 name keys became 166 real
bodies + 10 explicitly unresolvable; **7 rows resolved to a different path**, of which 5 were
`bodies/` versus `staging/` copies of the same bytes (no measurement change), 1 was the second
cathedral becoming reachable, and 1 was `PuP_Minicastle` — the bug above.

---

## 2. Sourcing: four new referenceable sources, 362 new bodies, nothing vendored

Three parallel read-only sweeps ran over GitHub/GitLab/Codeberg, Thunderstore/Nexus, and the
community hosts. **Every source's terms were read before any body was fetched**, and the
standing rule was applied unchanged: a body may be committed only if its source states a licence
permitting redistribution on terms this repo can meet. Absence of a statement is not permission.

### 2.1 Added and fetched — all reference-only

| Source | Pinned by | Bodies | Licence, MEASURED | Committed? |
|---|---|---|---|---|
| **`OverDrive/BiomeBlueprints`** (Thunderstore) | version `1.0.3` + archive sha256 `36c5a04df802…` | **353** | **none stated** | **No** |
| `mcarvall/Midgard-Valheim-Server` | commit `ebb14b0fe118` | 4 | none stated (`license: null`) | No |
| `okeanz/LootGoblinsUtils` | commit `d586297301 55` | 1 | none stated (`license: null`) | No |
| `sighsorry1029/Homestead` | commit `40fd3850c0ba` | 4 | **GPL-3.0** (spdx via API) | No — copyleft |

`web.py` grew an **archive** source kind for the Thunderstore package. Thunderstore is not a git
host, so there is no commit to pin; the package version plus the SHA-256 of the downloaded zip
is the same guarantee, and `fetch_archive()` **refuses to stage** if the archive hash has moved,
with the message *"the pinned release has been replaced; re-read its licence before re-pinning
it."*

**BiomeBlueprints' licence position, MEASURED on the 1.0.3 archive itself** (sha256
`36c5a04df80294c6b49b21c916d808fce60698b17479bfab7c95f850be12c707`, 31,547,180 bytes, 711
members): there is **no LICENSE file**, and the strings `licen` and `copyright` occur **zero
times** in `README.md`. The only permission language is the submission form, verbatim:

> "It also asks your permission to ship it — the mod redistributes the file, so that is the part
> that lets it."

That is a grant from each of the 89 credited builders to `lg_9d` for **that package**. It does
not reach downstream consumers. So the recorded position — *referenceable, not committable* — is
confirmed rather than overturned. `staging/` is gitignored; `git check-ignore` confirms
`staging/OverDrive__BiomeBlueprints/` is excluded.

What it brings, MEASURED: 353 bodies, name-prefix distribution `ashlands 164, blackforest 43,
plains 42, meadows 34, mistlands 33, swamp 21, mountain 16`; its own README says 78 are generated
house designs and 275 are community builds by 89 builders. **223 of the 353 ground on a flat pad
as captured, 68 ground on posts, 62 are `DEFECT_on_land`** — and **77 of them are
`PLACES_CLEAN`, ground as captured, and carry at least one crafting station**, which is 77 new
usable bases against the 49 `base`-category rows the library held this morning.

**Two things about it are worth recording because they were checked rather than assumed.**

1. **All 353 carry a `#Terrain` block and none of them can inject a phantom piece.** MEASURED:
   21,533 terrain rows, **every single one exactly 8 fields**, every shape token `square`, and
   **0 rows with ≥13 fields**. `to_rcon_plan.read_objects` discriminates by field count, so it
   reads zero of them as pieces. The captured ground flattening is lost on both placement routes
   (documented behaviour) but `spawn square` cannot happen.
2. **It republishes designs the fleet corpus already has, with the flattening added.** MEASURED:
   `hs_swamp_halvar_master_refinery` is Halvar's `halvar-master-refinery` — same 917 piece rows,
   byte-different because BiomeBlueprints prepends a 49-row `#Terrain` block and a full material
   list in `#Description`. Both copies measure identically (1.5% air, 25.3 m span, 917 pieces),
   which is the field-count rule working on a source it was not written for. So "zero SHA
   overlap with our 176" is true and *not* the same claim as "no overlap in content".

### 2.2 Found, terms permit redistribution, deliberately NOT fetched

These are the **first two web finds whose authors expressly permit re-upload**, and they are
recorded rather than taken, because taking them needs a decision the operator owns (the
attribution condition) and a credential this box does not have.

| Source | Verbatim permission, MEASURED | What it is |
|---|---|---|
| [Nexus 1508](https://www.nexusmods.com/valheim/mods/1508) — Thysicus | *"Upload permission You can upload this file to other sites but you must credit me as the creator of the file"*; *"Asset use permission You are allowed to use the assets in this file without permission or crediting me"*; not in anything sold | 1 `.vbuild`, Medieval(ish) house, vanilla pieces only |
| [Nexus 648](https://www.nexusmods.com/valheim/mods/648) — Ser_Essovius | *"Upload permission You can upload this file to other sites but you must credit me as the creator of the file"*; modification and asset reuse **need permission**; not in anything sold | Viking house **with attached workshop and dock** — the only permissively-licensed over-water body found anywhere |

**[INFERENCE]**, and it is the operative one: verbatim re-hosting with attribution is permitted;
altering the body is not (648 forbids modification without permission, which would rule out
`--strip-unknown-sections` and `--drop-prefab` on it). Recommended handling if the operator wants
them: fetch to `staging/`, add a `redistributable: "yes-with-attribution"` value to `web.py`'s
three-value verdict, and commit the bodies under `bodies/nexusmods__1508/` **with the permission
text vendored beside them**, exactly as `bygd`'s MIT notice is. I have not done it: a fourth
licence state and a credential-gated fetch are both the operator's call, and inventing the state
to make a number look better is how a library starts lying again.

### 2.3 Read, and closed

| Source | Position | Verbatim evidence, MEASURED |
|---|---|---|
| Nexus 109 Floki's Dock, 173 TJLongDock, 131 MD's Dockhouse, 641 Frode's Dock, 1807 starter home, 1917 Long House | **Closed** | *"Upload permission You are not allowed to upload this file to other sites under any circumstances"* on every one |
| Nexus 897, 450, 1374, 463, 2321 | **Unknown — no permission inferred** | HTTP 403 on the description tab. Public hosting is not permission |
| Nexus 1370 Hytte House | Gone | *"Mod unavailable / Removed by author"* |
| Thunderstore `KGvalheim/Blueprint`, `sighsorry/Blueprint`, `RustyMods/BlueprintPieces` | Not body sources | Blueprint **tools**, no pre-made bodies |
| Steam Community | **Closed** | Subscriber Agreement §6.A grants the UGC licence to *Valve*, not downstream; 0 `.blueprint`/`.vbuild` bodies in the queried guide results — they are floorplans and material lists |
| PlanBuild server marketplace / BuildShare Discord | Not a host-level source | PlanBuild's own code is WTFPL; that covers the mod, not uploader-authored bodies. Per-server, not an enumerable index |
| GitLab / Codeberg / Gitea | **Nothing** | Four targeted searches, no repository with a verified body. A negative result, not proof of absence |
| valheimians.com | **Closed**, unchanged | ToU ¶7/¶9 forbid redistribution. 1,441 builds; still the largest catalogue and still unusable |

`web.py`'s `REFERENCE_ONLY_SOURCES` records the closed ones so the next sweep does not re-walk
them. **Net: 4 new referenced sources, 362 new bodies on disk, 0 new bodies in git.**

---

## 3. The audit: what "mostly flat" actually is, and where the operator's rule lives

`tools/jumpstart/library/audit_bases.py` — new. It runs the base-profile raster over every
resolvable body and answers two questions instead of one.

**Question 1** was already answered by `base_geometry.BaseProfile.air`: how much of the footprint
stands clear of a flat pad. **Question 2 is new**, and it is what separates a pier from a
hillside capture:

```
air_isolation_max_m   over every FLOATING column of the footprint raster, the distance to the
                      nearest column whose lowest solid reaches the pad
```

Computed as a true nearest-neighbour search, not a fixed-radius neighbourhood test — because a
466 m² floating deck has no grounded cell in *any* fixed neighbourhood, so a radius test would
report the same "no support nearby" for a 5 m balcony and for a whole floating wing.

The pair that proves it works — **MEASURED**, near-identical air fraction, opposite structure:

| body | air fraction | isolation | reading |
|---|---|---|---|
| `drake-lighthouse` | 33.3% | **2.00 m** | a tower on legs |
| `BjOrN_blueprint001` | 34.1% | **33.29 m** | needs a hillside |

Over the 528 resolvable bodies the two populations barely touch: **2.00–6.00 m (n=98, median
4.00)** against **6.32–100.58 m (n=98, median 16.79)**. `ISOLATION_TOL_M = 6.0` sits in that gap
— it admits a 4 m pile grid with its 5.66 m diagonal and rejects any column more than three 2 m
cells from support.

### 3.1 Why the verdict is split in two, and where the honesty line is

I tried to make "is this meant for water?" a measurement and **it is not one**. MEASURED:
Olivanderr's `dock` — the archetypal harbour — is **70.1% floating with 13.42 m of isolation**,
which is arithmetically indistinguishable from a slope capture, because a pier's open basin *is*
a large unsupported span. A single verdict would have condemned it.

So the audit reports three fields, and each says where it comes from:

| field | values | source |
|---|---|---|
| `setting` | `land_flat` · `low_plinth` · `supported_air` · `needs_support` | **MEASURED** — a function of air fraction, worst gap and isolation, nothing else |
| `intended_setting` | `land` · `over_water` | **DERIVED FROM `category`** — curated for 176 rows, derived-from-measurements for 362. Geometry cannot supply it |
| `flat_pad_verdict` | `grounds` · `grounds_on_posts` · `expected_over_water` · `DEFECT_on_land` | the two combined |

`needs_support` is deliberately **not** called `needs_terrain`: terrain is one of three things
that could be under it. `DEFECT_on_land` is the only verdict that condemns a body, and it is the
operator's rule stated exactly — **uneven base, land site**.

Corroboration rather than proof, MEASURED: **89 of the 98 `grounds_on_posts` bodies (91%) contain
pole/beam/log/column pieces.** The verdict does not use that count — the verdict is geometry —
but the evidence agrees with it.

### 3.2 Relief is not the answer, and here is the proof

Judging by base relief was the obvious cheap test and it is wrong in both directions. MEASURED:

* `PuP_megaCastle` has **86.2 m of relief** and **grounds** — all of its spread is foundation
  below the datum, which is a foundation doing its job.
* `hs_blackforest_kitchenbench_brigames` has **0.77 m of relief** and was condemned.

That second one is why a third gate exists. `PLINTH_TOL_M = 1.0` — one wall course, the same
bound `base_geometry.MAJOR_LEVEL_CLIMB_M` is set from. A body whose **worst** gap is under a
course has no room for a storey under it: it is a worktop, a plinth or a step, which is what
furniture looks like to a raster when the datum lands on a station standing on the floor.
MEASURED: the gate reclassifies exactly **5 bodies of 528** (1 from `DEFECT_on_land`, 4 from
`supported_air`), and the 10th percentile of worst-gap among the other 89 condemned bodies is
**5.12 m** — so it separates the furniture and touches nothing else.

### 3.3 Per-source results

**MEASURED**, `flat_pad_verdict` by origin:

| source | n | grounds | on posts | over water | DEFECT | no body |
|---|---|---|---|---|---|---|
| OverDrive/BiomeBlueprints | 353 | 223 | 68 | 0 | 62 | 0 |
| fleet corpus | 87 | 52 | 14 | 2 | 9 | 10 |
| Oosquai/SavheimIV | 44 | 19 | 4 | 6 | 15 | 0 |
| RickardAndreasAgren/…Cruncher | 21 | 10 | 11 | 0 | 0 | 0 |
| RustyMods/VikingNPC | 11 | 10 | 0 | 0 | 1 | 0 |
| offsetkeyz/valheim_mod_sync | 8 | 6 | 0 | 1 | 1 | 0 |
| sighsorry1029/Homestead | 4 | 4 | 0 | 0 | 0 | 0 |
| mcarvall/Midgard-Valheim-Server | 4 | 3 | 0 | 0 | 1 | 0 |
| JereKuusela fixtures | 3 | 3 | 0 | 0 | 0 | 0 |
| constXife/bygd | 2 | 2 | 0 | 0 | 0 | 0 |
| okeanz/LootGoblinsUtils | 1 | 0 | 1 | 0 | 0 | 0 |

Worth noting: **`ValheimBlueprintCruncher` is 21 of 21 clean** on the pad question — 10 ground,
11 ground on posts, none condemned. It remains AGPL-3.0 and therefore local-use-only, but it is
the most reliably flat-bottomed set in the library. `Homestead` is 4 of 4 grounding, GPL-3.0, and
carries no stations — useful as scenery, not as a base.

The nine `expected_over_water` bodies, all MEASURED: the seven `brokkr-broken-bridge-*` spans
(25.6–62.0% air, 6.32–10.20 m isolation), `salty-dick-bridge-curved-final` (71.3%, 18.00 m,
**zero** pole/beam pieces — the deck is self-supporting stone), `long-bridge` (25.6%, 10.20 m),
and `dock` (70.1%, 13.42 m, 1,155 pole/beam pieces). None of these is a bad blueprint. Each is a
blueprint for a different site.

### 3.4 Artefacts

```
tools/jumpstart/library/audit_bases.py            the audit and the shortlist
tools/jumpstart/library/data/base_audit.json      538 rows, every measured field + presets
tools/jumpstart/library/data/base_audit.tsv       the same as a table
tools/jumpstart/library/data/base_shortlist.txt   the ranked per-preset output
```

Per body: `floating_fraction`, `floating_area_m2`, `mean_air_m`, `max_air_m`, `air_volume_m3`,
`max_buried_m`, `air_isolation_max_m`, `air_isolation_mean_m`, `relief_m`, `columns`,
`footprint_area_m2`, `footprint_x_m`/`_z_m`/`_span_m`, `base_y`, `datum_method`,
`walkable_levels`, `pieces`, `distinct_prefabs`, `stations` + `station_kinds`, `portals`,
`beds`, `pile_pieces`, `missing_prefabs` + `missing_pieces`, `top_tier` + `top_tier_pieces`,
`setting` + `setting_evidence`, `intended_setting` + `intended_setting_source`,
`flat_pad_verdict`, `metadata` (`curated` / `derived`), `licence`, `origin`, `sha256`.

---

## 4. The nine sites as they stand

**MEASURED**, every currently-referenced body. `fit` compares the body's occupied span to the
site's own `requirement.footprint_m` — the square `solve_placements` flattens.

| preset / site | body | verdict | air | isolation | air volume | pad | span | fit |
|---|---|---|---|---|---|---|---|---|
| `pre-eikthyr` meadows-starter-hall | `PuP_house10` | grounds | 1.6% | 2.00 m | 10 m³ | 24 | 20.9 | fits |
| `pre-elder` blackforest-walled-base | `fox-0123456` | grounds | 0.7% | 2.00 m | 54 m³ | 56 | 48.3 | fits |
| `pre-elder` early-dock | `dock` | **expected_over_water** | 70.1% | 13.42 m | 5,644 m³ | 48 | 44.5 | fits |
| `pre-bonemass` iron-era-workshop | ~~`BjOrN_blueprint001`~~ | **DEFECT_on_land** | 34.1% | 33.29 m | 3,414 m³ | 70 | 67.2 | fits |
| `pre-bonemass` iron-era-workshop | `halvar-master-refinery` *(replaced tonight)* | grounds | 1.5% | 2.00 m | 18 m³ | 70 | 25.3 | fits |
| `pre-moder` mountain-outpost | `jaik-mistvale-keep` | **DEFECT_on_land** | **61.2%** | 17.09 m | **3,765 m³** | 40 | 30.4 | fits |
| `pre-yagluth` plains-farm-base | `cozy_house` | grounds | 5.0% | 2.83 m | 624 m³ | 60 | 55.2 | fits |
| `pre-queen` mistlands-blackforge-base | `PuP_black_house_full` | grounds | **0.0%** | 0.00 m | 0 m³ | 80 | **81.6** | **overhangs 1.6 m** |
| `pre-fader` ashlands-forward-base | `deardly-casa-drly` | grounds | 6.5% | 2.00 m | 271 m³ | 50 | 34.0 | fits |
| `pre-kall` deepnorth-landing-portal | `Portal13.vbuild` | grounds | 0.0% | 0.00 m | 0 m³ | 32 | 7.6 | fits |
| `pre-kall` deepnorth-landing-camp | `wagon-camp` | grounds | 0.0% | 0.00 m | 0 m³ | 32 | 15.7 | fits |
| `deepnorth-sandbox` complete-station-hub | `Ultimate_OutPost` | grounds | 0.7% | 2.00 m | 6 m³ | 56 | 53.5 | fits |
| `deepnorth-sandbox` sandbox-portal-hub | `god-portaaoo` | grounds_on_posts | 28.4% | 2.83 m | 2,991 m³ | 50 | 37.0 | fits |
| `deepnorth-sandbox` sandbox-harbour | `dock` | **expected_over_water** | 70.1% | 13.42 m | 5,644 m³ | 48 | 44.5 | fits |

`pre-bonemass` was replaced while this was being written: `placements.yaml` now reads
`halvar-master-refinery` (MEASURED, read off the file), which grounds at 1.5% with 18 m³ of air
against BjOrN's 3,414 m³. Twelve of thirteen bodies fit their pad; the thirteenth overhangs by
1.6 m and is discussed under `pre-queen` below.

`dock` appears twice and is correct *for a water site* — its placement requirement is `coastal:
true, max_shore_dist_m: 20`, which is the right site, so nothing needs to change there beyond
knowing that the air under it is the pier and not a bug. `god-portaaoo` is a stone portal
arcade on columns: 28.4% air, 2.83 m isolation, and a player walks under it on purpose.

**Two things still need a decision, and neither is mine to make.**

1. **`pre-moder` will reproduce tonight's defect.** `jaik-mistvale-keep` is 61.2% floating with
   3,765 m³ of air — worse than the body the operator watched float — and it is assigned to
   nobody. §5 has three grounding replacements that fit the 40 m pad.
2. **`pre-queen`'s pad is 2 m short of its body.** Not a floating defect; a fit defect, and it
   is the only one in thirteen.

---

## 5. The per-preset shortlist

`./audit_bases.py --shortlist` is the live artefact; `data/base_shortlist.txt` is tonight's run.
Ranking, in strict precedence — and the order is the argument:

1. **it grounds**, then grounds on posts, then everything else — the operator's complaint was
   that the building floats, and no amount of station coverage fixes that;
2. **its materials are within the tier**, by more than a trim allowance — *a station can be
   stripped, a wall cannot*;
3. every station the preset wants;
4. no station it forbids — `--drop-prefab` can strip one, so bleed costs a rank rather than
   disqualifying;
5. nothing unspawnable; 6. a bed where the preset asks for one; 7. least air;
8. fewest pieces — at one RCON `spawn` per object, 900 pieces is 900 commands and 30,000 is an
   outage.

Bodies whose span exceeds the pad are dropped outright, as are exploit payloads, the ten 0-byte
rows and Infinity Hammer's three parser fixtures.

Two measurement bugs were found *in this ranking* and fixed before it was published, both of the
same kind — a check that confidently answers a question it is not measuring:

* **`"forge" in "blackforge"` is `True`.** Substring station matching credited a Mistlands black
  forge as satisfying a bronze-age forge requirement, and reported `forge` as tier bleed on
  `pre-kall` for bodies carrying only a black forge. Replaced with `STATION_GROUPS`, exact
  prefab names, base plus extensions, with an exhaustiveness check against `classify.STATIONS`
  that **raises** if a station is not in some group.
* **Material tier was not ranked at all.** The first run put `hs_ashlands_cm_thug_outpost` —
  **928 pieces of Ashlands material** — at the top of `pre-bonemass`. `OVER_TIER_PIECE_FLOOR =
  20` gates it, and the floor exists because the label alone lies in both directions: MEASURED,
  `PuP_forge1` reads `ashlands(2)` for two trim pieces on a 343-piece wood workshop, and
  `SettlerRuins_Ashlands1` reads `meadows` while being Ashlands-themed because its Ashlands
  content is world statics. The trim cases in the corpus are 2, 4, 14 and 16 pieces.

Top per preset, MEASURED, from tonight's `data/base_shortlist.txt`. `air` is the floating
FRACTION of the footprint, `worst` the largest single gap, `iso` the isolation; `st n/m` is
wanted stations present; tier is `top_tier(pieces at that tier)`. A high `air` with a low
`worst` is a raised floor plate, not a floating building — which is exactly why both columns
are printed.

### `pre-eikthyr` — 24 m, Meadows, 268 bodies fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| `Rocket Raccoon_Quick_House` | 11.2 m | 0.0% | 0.00 | 157 | 2/2 | meadows(157) |
| `hs_blackforest_tester_meadows_log_cabin` | 17.0 m | 0.0% | 0.00 | 207 | 2/2 | meadows(207) |
| `hs_blackforest_xxx_starthome` | 22.9 m | 2.0% | 2.00 | 466 | 2/2 | meadows(466) |

All three beat the incumbent `PuP_house10`, which ranks **#47** — it carries 0 of 2 wanted
stations. Smaller stamps also available and grounding: `hs_meadows_arkitek_repair_shack` (50
pieces, 4.6 m), `hs_blackforest_aodi_quickrest_1_0a` (69 pieces, 4.7 m).

### `pre-elder` — 56 m, BlackForest, 426 fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| **`fox-0123456`** *(incumbent, #1)* | 48.3 m | 0.7% | 2.00 | 1,229 | 6/6 | meadows(1229) |
| `hs_swamp_xxx_blackforest_house` | 18.5 m | 1.2% | 2.00 | 544 | 6/6 | meadows(544) |
| `hs_swamp_vrolok_caba_a_2` | 33.5 m | 1.6% | 2.00 | 343 | 6/6 | meadows(343) |

The incumbent is already the best body in 426. **Keep it.** The alternatives matter only as
cheaper placements — 343 pieces against 1,229.

### `pre-bonemass` — 70 m, BlackForest, 469 fit
| body | span | air | iso | pieces | st | tier | bleed |
|---|---|---|---|---|---|---|---|
| `hs_swamp_sheepshank_danamermhomefinal` | 45.7 m | 2.6% | 2.00 | 3,009 | 6/6 | mistlands(16) | — |
| `hs_plains_tester_swamp_to_mountains_bungalow` | 40.1 m | 3.0% | 2.00 | 875 | 6/6 | plains(6) | artisan, blastfurnace, windmill |
| `fox-0123456` | 48.3 m | 0.7% | 2.00 | 1,229 | 5/6 | meadows(1229) | — |
| **`halvar-master-refinery`** *(FlatPick's pick, #9)* | 25.3 m | **1.5%** | 2.00 | 917 | 4/6 | mistlands(14) | artisan |

`halvar` ranks 9th of 469 on stations — it has no stonecutter and no cauldron — and **1st among
the ten on least air**. It is the right pick anyway: 917 spawns against 3,009, and it is the
better thematic iron workshop. Recorded so the choice has numbers beside it. Against the
incumbent `BjOrN_blueprint001` at 34.1% air and 3,414 m³, any row in this table is an
improvement.

### `pre-moder` — 40 m, Mountain, 365 fit — **this one needs a decision**
| body | span | air | worst | iso | pieces | st | tier |
|---|---|---|---|---|---|---|---|
| `hs_plains_startinghouse_plainsmodel` | 26.3 m | 31.6% | **0.74 m** | 4.00 | 868 | **5/5** | plains(1) |
| `hs_swamp_xxx_blackforest_house` | 18.5 m | 1.2% | 1.64 m | 2.00 | 544 | 4/5 | meadows(544) |
| `hs_swamp_vrolok_caba_a_2` | 33.5 m | 1.6% | 0.65 m | 2.00 | 343 | 4/5 | meadows(343) |
| `hs_blackforest_tester_duo_black_forest_home` | 30.8 m | 2.2% | 4.86 m | 2.00 | 586 | 4/5 | meadows(586) |

The #1 row is the case the `low_plinth` gate exists for: 31.6% of its footprint is clear, and
the worst gap in the whole body is **0.74 m** — a raised floor plate, under one wall course. It
is the only body in 365 with all five wanted stations, at 868 pieces and 1 piece of over-tier
material.

The incumbent `jaik-mistvale-keep` is **61.2% floating with 3,765 m³ of air** and cannot be
fixed by a datum: `TerrainComp::ApplyToHeightmap` clamps terrain deltas to ±8 m and this body
has 32.97 m of base relief. Any row above grounds, fits the 40 m pad with room, and needs
343–868 spawns against 3,472.

### `pre-yagluth` — 60 m, Plains, 438 fit
| body | span | air | iso | pieces | st | tier | bleed |
|---|---|---|---|---|---|---|---|
| `hs_plains_tester_swamp_to_mountains_bungalow` | 40.1 m | 3.0% | 2.00 | 875 | **7/7** | plains(6) | — |
| `halvar-master-refinery` | 25.3 m | 1.5% | 2.00 | 917 | 4/7 | mistlands(14) | — |
| `hs_swamp_halvar_master_refinery` *(same build, BiomeBlueprints copy)* | 25.3 m | 1.5% | 2.00 | 917 | 4/7 | mistlands(14) | — |

The bungalow is the find of this sweep for this tier: **7 of 7 wanted stations, zero tier bleed,
Plains materials, grounds at 3.0%, 875 pieces.** The incumbent `cozy_house` grounds too (5.0%)
but ranks **#200 of 438**, and the reason is material rather than air: MEASURED, it carries
**332 pieces of Mistlands material** on a Plains-tier site, so the tier gate drops it below
every body that does not. It also misses the spinning wheel and costs 6,034 spawns against 875.

### `pre-queen` — 80 m, Mistlands, 483 fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| **`Ultimate_OutPost`** | 53.5 m | 0.7% | 2.00 | 1,820 | **6/6** | mistlands(36) |
| `hs_mistlands_thrad_workshop` | 19.1 m | **0.0%** | 0.00 | **493** | 3/6 | mistlands(36) |
| `hs_plains_tester_swamp_to_mountains_bungalow` | 40.1 m | 3.0% | 2.00 | 875 | 3/6 | plains(6) |

The incumbent `PuP_black_house_full` is **not in this list, and that is a second finding.**
MEASURED: its occupied span is **81.6 m against a `footprint_m: 80` pad** — 1.6 m of the body
stands outside the flattened square, on natural ground. It also grounds at **0.0% air**, the
cleanest large base in the library despite 59.45 m of relief, because every metre of that
spread is foundation. So the body is right and the pad is 2 m short: either widen
`pre-queen`'s `footprint_m` to 84 or accept that a 1.6 m skirt lands on unflattened terrain.
**[INFERENCE]**: widening is the cheaper fix, and it is `solve_placements`' call, not this
library's.

Of the 483 bodies that do fit, only `Ultimate_OutPost` matches its station coverage. The
Ashlands-material bases that scored 6/6 in an earlier run are now correctly ranked below, since
a Mistlands party cannot build a grausten wall.

### `pre-fader` — 50 m, AshLands, 405 fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| `hs_ashlands_compact_endgame_base1` | 27.3 m | 2.3% | 2.00 | **537** | **4/4** | ashlands(113) |
| `hs_ashlands_cm_thug_outpost` | 40.2 m | 4.7% | 2.00 | 1,649 | **4/4** | ashlands(928) |
| `hs_ashlands_gorthein_fancy_house_redux` | 36.4 m | **0.0%** | 0.00 | 1,962 | 3/4 | ashlands(1110) |

Incumbent `deardly-casa-drly` grounds at 6.5% and holds 3 of 4. Two bodies now beat it on
stations at a third of the piece count.

### `pre-kall` — 32 m, DeepNorth, 326 fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| **`wagon-camp`** *(incumbent, #1)* | 15.7 m | 0.0% | 0.00 | **160** | 2/2 | mistlands(22) |
| `hs_ashlands_madzobuild_ashlandcamp` | 18.9 m | 0.0% | 0.00 | 266 | 2/2 | ashlands(186) |
| `hs_ashlands_minicastlevanilla` | 14.2 m | 3.7% | 2.00 | 980 | 2/2 | ashlands(522) |

`wagon-camp` is #1 of 326. **Keep it.** `Portal13.vbuild` is a portal shelter, ranks on no
stations by design, and grounds at 0.0% — also keep.

### `deepnorth-sandbox` — 56 m, Meadows, 426 fit
| body | span | air | iso | pieces | st | tier |
|---|---|---|---|---|---|---|
| `hs_ashlands_cm_thug_outpost` | 40.2 m | 4.7% | 2.00 | 1,649 | **16/16** | ashlands(928) |
| **`Ultimate_OutPost`** *(incumbent, #2)* | 53.5 m | 0.7% | 2.00 | 1,820 | 15/16 | mistlands(36) |
| `hs_ashlands_compact_endgame_base1` | 27.3 m | 2.3% | 2.00 | 537 | 14/16 | ashlands(113) |

`cm_thug_outpost` carries **every one of the sixteen stations** this sandbox wants, which
`Ultimate_OutPost` misses by one (`piece_preptable`). `Ultimate_OutPost` has 5× less air and is
the incumbent; the difference is one prep table. **[INFERENCE]**: not worth re-placing for.

---

## 6. What I changed, and what I deliberately did not

Changed, all inside `tools/jumpstart/library/` except two lines of resolver plumbing:

```
library/classify.py        one row per distinct BODY; resolvable/unresolved_reason; honest sha256
library/web.py             4 new sources; archive fetch pinned by zip hash; derived metadata
library/build_manifest.py  identity(): merge identical, suffix colliding, assert unique handles
library/materialise.py     hash-verified locate(); locate_by_filename(); refuses ambiguity
library/audit_bases.py     NEW -- the grounding audit and the per-preset shortlist
library/README.md          rewritten headline; identity and grounding sections
library/PROVENANCE.md      4 new sources with terms; the permission-granted pair; 10 unresolvable
library/data/*.json,tsv    regenerated; base_audit.{json,tsv} and base_shortlist.txt are new
blueprints/survey_datum.py deleted its duplicate resolver, calls the library's
tools/test_jumpstart_library_identity.py  NEW -- 12 tests over the identity contract
```

### Verification

Every claim below was run, not reasoned about:

```
./classify.py && ./web.py --fetch && ./web.py && ./build_manifest.py
  538 rows, 528 resolvable, 10 unresolvable, handles unique (build_manifest RAISES otherwise)
./materialise.py --verify
  528 verified, 0 drifted, 0 unavailable, 10 unresolvable (no body exists)
./materialise.py --verify --name brokkr-the-cathedral.blueprint
  refuses: "names 2 DIFFERENT bodies -- ask for one by handle: ..."
./materialise.py --out /tmp/mtest --name <both cathedral handles> <a Minicastle handle> \
                 hs_swamp_halvar_master_refinery.blueprint --strip-unknown-sections
  4 materialised, 0 skipped -- both cathedrals sit in ONE folder under distinct names, and the
  BiomeBlueprints halvar loses exactly its 49 terrain rows. The stripped copy re-measures at
  917 pieces / base_y 0.000 / 1.5% air, identical to the original: the strip is piece-lossless.
./materialise.py --out /tmp/mtest2 --name PuP_stable.blueprint
  0 materialised, 1 skipped, exit 1, with the zero-byte reason printed
survey_datum.py           166 surveyed, 166 placeable, 0 refused, 0 parser errors
audit_bases.py            538 audited: 332 grounds, 98 on posts, 9 over water, 89 DEFECT, 10 no body
pytest tools/test_jumpstart_library_identity.py        12 passed
pytest tools/test_jumpstart_network.py                 32 passed  (consumes the manifest by name)
pytest tools/test_jumpstart_blueprint_{parser,centring}.py tools/test_jumpstart_data_column.py
                                                       46 passed
git status --porcelain | grep -c '\.\(blueprint\|vbuild\)$'   ->  0
```

The new tests are regression tests, not decoration. **MEASURED** against the pre-fix code on the
same fixtures: the old resolver returned the **0-byte stub** where the new one returns the
101,856-byte body, and it **returned an empty file to satisfy an empty hash claim** where the
new one returns `None`; and keying the current 538 rows on `source_name` — the pre-fix key —
yields 536 unique names, i.e. the two collisions are still there to be reproduced.

Not done, and each for a stated reason:

1. **The two Nexus bodies are not fetched or committed.** Their terms permit it *with
   attribution*; that needs a fourth `redistributable` state and an operator decision about
   carrying the credit, and 648 forbids modification, which would forbid the strip and drop
   passes the RCON route uses. §2.2 has the exact recipe.
2. **`PuP_rampart1` and `PuP_stable` are not recovered.** No source found tonight holds them.
   The library has no rampart and no stable, and now says so.
3. **No `worlds/` file is edited and no placement is made.** `pre-moder` is flagged, not
   changed — it is not mine, and `FlatPick` was told over `hub`.
4. **`BASE_PROFILE_AIR_AREA_FRACTION`, `BASE_PROFILE_FLAT_TOL_M` and the datum rule are
   untouched.** The audit consumes them; changing a shared constant mid-flight was exactly what
   the two-owner split was meant to prevent.
5. **No formatter, linter or project-wide test run.** `./scripts/check.sh` is the parent's.

## 7. Known limits of this audit

1. **`intended_setting` is only as good as `category`.** For 362 of 538 rows the category is
   DERIVED from station/portal/bed counts, so a stilt house that reads `base` will be judged as
   a land building. The measured half (`setting`, air, isolation) is unaffected.
2. **A `DEFECT_on_land` verdict is about a FLAT pad.** It does not say the body is bad — it says
   it needs ground that `flatten_required: true` does not produce. `TerrainComp::ApplyToHeightmap`
   clamps deltas to ±8 m, so bodies above that relief cannot be accommodated by sculpting at all.
3. **The raster is 2 m.** A 1 m-wide cantilever reads as a 2 m column. Finer sampling reports
   sub-piece noise as relief, which is why the cell size is the piece module.
4. **Air is measured at the body's own chosen datum.** A different datum moves every number
   here. `datum_method` is recorded per row so it is visible which rule was applied.
5. **`PLACES_CLEAN` still only means the prefab names resolve** against the bundles, and the
   evidence set is a superset of the real prefab table — it can produce false *clean*, never
   false *missing*.
6. **Bulk-source `role` and `fit` text is generated**, not read by a human. Every such row is
   `metadata: derived`, and the sentences contain only measured numbers.
