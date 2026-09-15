# Blueprint and building tooling: what to standardise on

Status: proposed. Owner: BlueprintTooling. Date: 2026-09-15.
Nothing was installed, deployed, published or committed to produce this document.
`valheim-server-Ulfsland` was not touched. Every mod examined was downloaded into
`/tmp/bp-eval` and disassembled; the two runtime proofs were run on the throwaway
`/tmp/vhsandbox` server against world `ClearSandbox` with **zero players online**,
and that sandbox was reverted to its prior plugin and config set afterwards.

Every claim below is tagged **MEASURED** (I ran it, parsed it, or read it out of a
disassembly of the deployed binary) or **INFERRED** (reasoning over measurements).

---

## 0. Executive answer

There are two jobs and they have **different winners**.

**The pipeline — recommendation: keep no blueprint mod in the loop at all. Fix our
own reader, and switch the emitter from `spawn` to `spawn_object … data=`.**
The decisive measurement is not a parser comparison. It is this: Infinity Hammer
*cannot place a blueprint headlessly*. On the sandbox server with zero players,
`hammer_blueprint BlackForestRaiderTown1` answered **`Error: No player.`**
(MEASURED). PlanBuild is Jotunn + HookGenPatcher and drives placement through a
Blueprint Rune and a GUI. So neither candidate is a pipeline tool; both are admin
tools whose *file format* we consume. Meanwhile the thing we already have installed
server-side — World Edit Commands 1.77.0 — exposes `spawn_object … data=<base64>`,
and I proved headlessly that it applies the blueprint's 14th column onto the new
ZDO, **including a container's full inventory** (MEASURED, §4). That is a capability
gain available today with no new mod, no deploy, and no licence exposure.

**The admin seat — recommendation: Infinity Hammer 1.83.0 + Structure Tweaks for
building, `AdvancedTerrainModifiersCompatible` for terrain, and drop
PerfectPlacement.** Infinity Hammer already provides the whole PerfectPlacement
feature set as console commands (`hammer_move`, `hammer_rotate`, `hammer_scale`,
`hammer_mirror`, `hammer_offset`, `hammer_stack_*`, `no_cost`, `no_physics`,
`no_target`, `hammer_save` — MEASURED from its user-string heap), whereas
PerfectPlacement 1.2.2 is the oldest package in the build set (last updated
2025-03-06) and carries the only genuinely unresolved reference into our deployed
game assembly that I found anywhere in the set (§5).

Do **not** adopt PlanBuild, BiomeBlueprints or PrefabHammer.

---

## 1. Format capability matrix, MEASURED

Measured by writing a full-fidelity reader (`/tmp/bp-eval/measure.py`) that parses
every column of every row of the real corpus without discarding anything, and
comparing it against `tools/jumpstart/blueprints/inventory.py`, which deliberately
mirrors Infinity Hammer. Corpus resolved from `library_manifest.json` by SHA-256:
**164 of 176 manifest rows resolved to bodies on disk** (10 are 0-byte, 2 are
duplicate names — see §8), giving **407,860 piece rows across 157 `.blueprint`
and 7 `.vbuild` files**.

| Capability | `.blueprint` (PlanBuild/IH) | `.vbuild` (BuildShare) | recovered by our reader today |
|---|---|---|---|
| piece prefab name | yes — col 0 | yes — col 0 | **yes** |
| build category | yes — col 1 (`BuildingStonecutter`, …) | no | no (not needed) |
| position | yes — cols 2,3,4 | yes — cols 5,6,7 | **yes** |
| rotation (quaternion) | yes — cols 5–8, non-zero on 407,859/407,860 rows | yes — cols 1–4, non-zero on all 12,137 rows | **yes** (`to_rcon_plan.read_objects`) |
| scale | yes — cols 10,11,12; non-unit on **7,836** rows | **no column at all** (all 12,137 rows are exactly 8 fields) | read, then **discarded at emit** |
| `additionalInfo` (sign text, item-stand spec, PlanBuild inventory) | yes — col 9; non-empty on **11,006** rows, 595 distinct values | no | read, then **discarded at emit** |
| per-object ZDO blob (`Data`) | yes — col 13 on **125,314** rows, non-empty on **125,272** | no | read, then **discarded at emit** |
| snap points | yes — `#SnapPoints`, 43 files | no | counted, not used |
| terrain modifiers | yes — `#Terrain` (PlanBuild) 15 files / `#TerrainHeight:`+`#TerrainPaint:` (IH) 3 files | no | **discarded** |
| container contents | yes — inside col 13 and col 9 | no | **discarded** |
| item quality / durability / stack | yes — inside col 13 (898 rows) | no | **discarded** |
| crafter / creator name | yes — col 13 (2,146 rows) + a `creator` long on 65,547 rows | no | **discarded** |
| per-piece health & support | yes — col 13: `health` on 85,346 rows, `support` on 123,234 | no | **discarded** |

MEASURED consequence for `.vbuild`: the format carries **nothing** beyond prefab,
rotation and position. All 7 `.vbuild` files in the corpus are 8-field throughout.
There is no scale, no extra info, no placement-chance column, no terrain and no
snap points in any of them. Any `.vbuild`-lineage tool is therefore a strict
capability *subset* and can be dismissed for both jobs. (`inventory.py`'s comment
documents cols 8–9 as `extra info` / `placement chance` from the Infinity Hammer
disassembly; MEASURED, our corpus never populates them.)

### 1.1 The 14th column — what is actually in it

This is the most valuable thing measured tonight.

**Format, MEASURED from IL, not guessed.** The column is base64 of
`WorldEditCommands.Data.DataEntry`, read by `DataEntry::Load(ZPackage)` in the
deployed `World_Edit_Commands/WorldEditCommands.dll` 1.77.0. The layout is an
`int32` flag word followed by sections, each a `byte` count then
`(int32 key-hash, value)` pairs, in this order — note that **longs (0x40) are read
before strings (0x10)**, which is not bit order and is what defeats a naive decoder:

```
int32 flags
0x0001 floats      byte n, n × (int32 hash, float32)
0x0002 vector3     byte n, n × (int32 hash, 3 × float32)
0x0004 quaternion  byte n, n × (int32 hash, 4 × float32)
0x0008 ints        byte n, n × (int32 hash, int32)
0x0040 longs       byte n, n × (int32 hash, int64)
0x0010 strings     byte n, n × (int32 hash, 7-bit-length UTF-8)
0x0080 byteArrays  byte n, n × (int32 hash, int32 len, bytes)
0x0100 connection  byte type, int32 hash
0x0200 persistent / 0x0400 distant / 0x0800 priority
```

`DataEntry(string base64)` is literally `new ZPackage(base64)` → `Load(ZPackage)`
(MEASURED, `wec.il`). Keys are `StringExtensionMethods.GetStableHashCode` of the
key name; I reproduced that hash in Python and resolved names by hashing the user
strings of `assembly_valheim.dll`, every `<Type>.<field>` pair in it, and the
strings of all 250 deployed plugin DLLs.

**Decode result across the whole corpus: 125,576 of 125,580 data-bearing rows
decode cleanly and consume exactly their byte length (100.00%).** The 4 failures
are not base64 at all — they are rows where column 13 holds the literal string
`ShieldWood` (an item-stand payload written in the plain form, e.g.
`itemstand;;-21.976;4.538;3.928;…;1;1;1;ShieldWood`). Nothing is unreadable.

What the column carries, by rows (MEASURED):

| key | kind | rows | meaning |
|---|---|---|---|
| `support` | float | 123,234 | WearNTear structural support |
| `HasFieldsWearNTear` | int | 84,776 | component-present marker |
| `health` | float | 85,346 | per-piece health |
| `WearNTear.m_health` | float | 84,555 | Infinity Hammer component-field override |
| `HasFields` | int | 86,759 | ZNetView marker |
| `creator` | long | 65,547 | placing player id |
| `scale` | vector3 | 59,536 | non-uniform scale (+ `scaleScalar` on 2,178) |
| `WearNTear.m_supports` | int | 56,485 | IH override (indestructible/floating) |
| `Piece.m_primaryTarget` / `Piece.m_randomTarget` | int | 56,264 each | IH targeting overrides |
| `HasFieldsPiece` | int | 56,264 | component marker |
| `steamID` / `steamName` | string | 8,620 each | **the builder's Steam ID and name** |
| `HasFieldsFireplace` / `Fireplace.m_infiniteFuel` | int | 8,246 each | IH fire override |
| `xray_created` | long | 9,296 | IH bookkeeping |
| `StaticTarget.m_randomTarget` | int | 4,653 | IH override |
| `RandMatSeed` | int | 2,248 | material variant seed |
| `crafterName` (+ `crafterID`) | string/long | 2,146 / 537 | crafter attribution |
| `fuel` | float | 1,031 | fireplace/torch fuel level |
| `override_wear` / `override_interact` | int | 623 / 152 | **Structure Tweaks** keys |
| `durability`, `stack`, `quality` | float/int/int | 898 each | **item** quality on item stands |
| `items` | string or byteArray | **122** | **container inventories** |
| `text` | long/string | 53 | **sign text** |
| `Destructible.m_minToolTier` | int | 608 | IH override |
| `seed`, `level`, `max_health`, `spawnpoint`, `vel`, … | mixed | ≤270 | creature/pickable state |

Container inventories decode item-for-item. Example from the corpus (MEASURED,
inventory serialisation version 106):

```
Coins           x3  dur=100.0 slot=(0,0) q=1 var=0 crafter=''
BoneFragments   x1  dur=100.0 slot=(1,0) q=1 var=0 crafter=''
RS_EtherPotion  x1  dur=100.0 slot=(2,0) q=1 var=0 crafter=''
```

122 rows across 40 files carry an inventory, on prefabs
`TreasureChest_plains_stone` (17), `TreasureChest_meadows` (14),
`TreasureChest_dvergrtower` (13), `RS_Barrel_2` (10), and so on. 53 rows carry
sign text. 898 rows carry item quality, all on `itemstand`/`itemstandh`.

**Answering the three collisions directly.**

1. **Container contents.** The corpus does carry inventories, and §4 proves they
   land headlessly. The scope is however **122 rows in 40 files**, not 451. So
   `chest-manifest.yaml` is *not* fully redundant: it can be retired for the 122
   rows that the blueprints already describe, but the live world's 451–454
   containers are mostly not blueprint-sourced. **INFERRED**: keep the manifest
   mechanism, and make blueprint-carried inventories authoritative where present
   so the two mechanisms stop competing for the same chest.
2. **Quality.** MEASURED: the column carries **item** quality (898 rows, all on
   item stands), with `durability`, `stack` and `variant` alongside. It does **not**
   carry piece upgrade level, because Valheim does not store one — there is no
   piece-quality ZDO key in `ZDOVars`. The building-half analogue of the
   `ServerCharacters` quality bug does not exist. What the column *does* carry for
   pieces is `health`, `support`, `scale` and the Infinity Hammer/Structure Tweaks
   component overrides, and those are exactly what a placed structure currently
   loses.
3. **Terrain.** The column carries **no** terrain. Terrain lives in the `#Terrain`
   / `#TerrainHeight:` sections, and §3 and §6 cover it.

### 1.2 Does anything round-trip the column?

| tool | reads col 13 | writes col 13 | applies it to a ZDO | headless |
|---|---|---|---|---|
| Infinity Hammer 1.83.0 | **yes** (`BlueprintObject.Data`) | **yes** — `infinity_hammer.cfg` ships `Save data to blueprints = true` | yes, via `WorldEditCommands.Data.DataEntry` | **no** — `Error: No player.` |
| PlanBuild 0.18.6 | **no** — its whole format vocabulary is 8 literals (§3) and stops at col 9 | no | n/a | no (Jotunn GUI) |
| `spawn_object … data=` (WEC 1.77.0, installed) | n/a — takes base64 directly | n/a | **yes, MEASURED §4** | **yes, MEASURED §4** |
| our `to_rcon_plan.py` | **yes** (`p[13]` → `Obj.data`) | n/a | **no** — prints `object data lost on N` and emits bare `spawn` | yes |

So: Infinity Hammer round-trips the column perfectly and cannot be driven
headlessly. PlanBuild cannot read it at all. **Our own reader already reads it and
throws it away at the last step.** That is the gap, and it closes with an emitter
change, not a mod.

---

## 2. Our current dependency, audited

### 2.1 Is 1.83.0 the latest, and does a newer release fix the parser?

**MEASURED** (Thunderstore experimental API, 2026-09-15): `JereKuusela/Infinity_Hammer`
latest is **1.83.0**, updated 2026-09-13. We are on the latest. There is no newer
release to fix anything. Our dependencies are ahead of what it asks for:
it declares `Server_devcommands-1.112.0` / `World_Edit_Commands-1.76.0`; deployed
are **1.113.0** and **1.77.0** (MEASURED from the deployed `manifest.json` files).

### 2.2 What the parser loses, per file

The state machine is as `tools/jumpstart/library/classify.py` documents it and as I
re-measured: five states, initialised to `Pieces`; an unrecognised `#Header` whose
second character is not whitespace sets the section to `None`, and every row after
it is dropped until the next recognised header.

**18 of 176 files carry an unrecognised header; 481 rows are discarded; worst file
187.** Per file:

| file | pieces IH reads | rows discarded | of which are **pieces** | genuine terrain ops |
|---|---|---|---|---|
| BlackForestRaiderTown1.blueprint | 903 | 187 | 187 | 0 |
| PlainsRaiderTown1.blueprint | 1591 | 93 | 93 | 0 |
| MeadowRaiderTown1.blueprint | 906 | 73 | 73 | 0 |
| MeadowSettlerTown1.blueprint | 387 | 49 | 49 | 0 |
| MistlandRaiderTown1.blueprint | 527 | 43 | 43 | 0 |
| MeadowSettlerTown2.blueprint | 997 | 8 | 8 | 0 |
| aodi-skardvakt-the-black-tower.blueprint | 358 | 8 | 0 | 8 |
| MountainRaiderTown1.blueprint | 1018 | 6 | 6 | 0 |
| PlainsRaiderTower1.blueprint | 584 | 4 | 4 | 0 |
| 15643-Valheimian.blueprint | 5692 | 2 | 0 | 2 |
| god-ponte-plus.blueprint | 883 | 2 | 0 | 2 |
| terrain-future-section.blueprint | 1 | 2 | 1 | 1 |
| 14464-Valheimian.blueprint | 2329 | 1 | 0 | 1 |
| gnome_house.blueprint | 2329 | 1 | 0 | 1 |
| god-house33.blueprint | 2949 | 1 | 0 | 1 |
| god-shadowstone-fortress.blueprint | 8283 | 1 | 0 | 1 |
| PiNoKi_Longhouse.blueprint | 182 | 0 | 0 | 0 |
| PiNoKi_SmallHut.blueprint | 48 | 0 | 0 | 0 |
| **total** | | **481** | **464** | **17** |

**This reframes the bug, MEASURED.** 464 of the 481 discarded rows are not terrain
data — they are **14-field piece rows written under a `#Terrain` header** by whichever
tool produced the RustyMods VikingNPC packs: 391 × `FirTree_oldLog`, 70 × `stubbe`,
2 × `lox_ribs`, 1 fixture row. Only **17** rows are genuine PlanBuild terrain
operations (10 `circle`, 6 `square`, 1 fixture). So the real loss in our corpus is
**464 props — trees, stumps and bone piles — silently vanishing from nine
settlement blueprints**, not a lost ground flattening. `BlackForestRaiderTown1`
loses 187 of its 1,090 rows: **17.2% of the file.**

The two `PiNoKi_*` files carry four unrecognised headers each (`#BedZone`,
`#DoorZone`, `#LayoutProfile`, `#TableZone`) but lose nothing, because every one of
those sections is empty in those bodies. They are still a standing hazard: a future
body from that author with rows under those headers would lose them.

### 2.3 Terrain, and "one blueprint per placement"

MEASURED from `ih.il`: `InfinityHammer.Blueprint` has `TerrainHeight` and
`TerrainPaint` fields, so Infinity Hammer *can* carry terrain — in **its own**
`#TerrainHeight:` / `#TerrainPaint:` sections, present in 3 corpus files. It cannot
read PlanBuild's `#Terrain`. And because it discards those rows at load, a
save-reload round trip through `hammer_save` does **not** convert them: the data is
already gone. **INFERRED, but it follows directly from the discard.**

`#Height:` / `#Paint:` (the legacy terrain sections) make Infinity Hammer **throw**
and refuse the file outright. **MEASURED: zero corpus files use them**, so this is
a latent hazard for future web-sourced bodies only.

### 2.4 Does PlanBuild read the corpus losslessly?

No — and it is worse than Infinity Hammer on our bodies.

**MEASURED** from `PlanBuild.dll` 0.18.6's user-string heap, the complete section
vocabulary is exactly eight literals:

```
#Name:  #Creator:  #Description:  #Category:  #SnapPoints  #Terrain  #Pieces  #
```

It has **no** `#Center:`, `#Coordinates:`, `#Rotation:`, `#TerrainHeight:` or
`#TerrainPaint:`. Those five are Infinity Hammer extensions. So the two vocabularies
are disjoint in *both* directions:

- Infinity Hammer cannot read `#Terrain` → 481 rows lost across 18 files (§2.2).
- PlanBuild cannot read `#Center:` / `#Coordinates:` / `#Rotation:` → **MEASURED:
  47 of the 164 corpus bodies carry all three** (48 carry `#Center:`), which is the
  blueprint's own origin, pivot and captured yaw — lose those and the body has to be
  re-datumed by hand; nor `#TerrainHeight:`/`#TerrainPaint:` → 3 files; nor column
  13 → **89 files, 125,272 rows** of per-object ZDO state.

**So the single most decisive number in this evaluation is not the one that was
expected.** PlanBuild does not recover files Infinity Hammer drops; it drops far
more. It would recover 481 rows in 18 files and lose 125,272 data payloads in 89.
Reimplementing PlanBuild's parser would be a regression. The correct target is a
reader that is the **union** of both vocabularies, which is what we can write
ourselves and already nearly have.

PlanBuild is also unusable headlessly for independent reasons: it requires
`ValheimModding-HookGenPatcher-0.0.4`, which is **MEASURED absent** from our
deployment (no patcher DLL, no `MMHOOK_*` in the BepInEx cache) — its IL genuinely
references `MMHOOK_assembly_valheim`, which is why `monodis` aborts on it without a
HookGen output present. It registers its console commands through Jotunn
(`CommandManager.AddConsoleCommand`, commands `blueprints` and `planbuild`), and
its terrain is realised by placing a `piece_bpterrainmod` piece in-world.

---

## 3. What each candidate is, and what job it wins

1.0.12 status is **MEASURED** with a signature-aware unresolved-reference check
(`/tmp/bp-eval/refcheck3.py`) against the deployed
`Ulfsland/data/bepinex/valheim_server_Data/Managed/assembly_valheim.dll`
(Valheim l-1.0.12, network version 40 — confirmed by the sandbox banner). The check
is validated two ways, because a check that always answers "fine" is worthless:

- **Control A, synthetic**: drop `Terminal.AddString` from the expected surface and
  re-run Infinity Hammer → the check reports exactly that one miss. It fires.
- **Control B, real**: PlanBuild **0.14.9** (pre-1.0) → **3 genuine unresolved
  members**: `ArmorStandSlot.m_visualName`, `ItemStand.SetVisualItem(string,int,int)`,
  `Piece.SetCreator(long)`. It discriminates.

Generic methods and `[opt]` parameters are treated as arity-agnostic, which is why
`ZNetView.Register<T>` and `MessageHud.ShowMessage` are not false-flagged.

| mod | version | job it wins | 1.0.12 refs | on-disk scope | server-required | Jotunn net-compat | disconnects players server-side |
|---|---|---|---|---|---|---|---|
| **Infinity Hammer** | 1.83.0 (latest) | **admin seat** — and *only* the admin seat | 338 refs, **0 unresolved** | DLL in server tree; `shared` in admin editions, `client-only` in flat/vr | no | none (no Jotunn) | no evidence; runs server-side now |
| **World Edit Commands** | 1.77.0 | **pipeline** — `spawn_object … data=` | 333 refs, 0 unresolved | server tree; `shared` admin, `client-only` flat/vr | effectively yes for the pipeline | none | no |
| **Server Devcommands** | 1.113.0 | pipeline plumbing (console over RCON) | 391 refs, 0 unresolved | server tree | yes | none | no |
| **Upgrade World** | 1.82.0 | pipeline — zone generation, `objects_remove`, `objects_count` | 291 refs, 0 unresolved | server tree, `shared` | yes | none | no |
| **Structure Tweaks** | 1.37.0 | admin seat — and it is the **interpreter** of `override_wear` (623 rows) / `override_interact` (152 rows) in our corpus | 225 refs, 0 unresolved | DLL **in server tree** although declared `client-only` | no | none, but embeds **ServerSync** | **yes** (previously MEASURED; that is why it is behind the admin window) |
| **PerfectPlacement** | 1.2.2 (2025-03-06, oldest) | nothing Infinity Hammer does not already do | 127 refs, **1 unresolved** | DLL **in server tree** although declared `client-only` | no | none, embeds **ServerSync** | **yes** (previously MEASURED) |
| **OdinArchitect** | 1.7.2 (latest 1.7.5) | player-facing **content**, not a tool | 2 refs, 0 unresolved | server tree, `shared` in all six editions | yes (Jotunn) | **`EveryoneMustHaveMod` / Minor** | no |
| **AdvancedTerrainModifiersCompatible** (`TerrainTools.dll`) | 1.4.8 | **admin seat, terrain** | 95 refs, 0 unresolved | **DLL genuinely in the server tree**, `shared` in all six editions | no | `VersionCheckOnly` / **Patch** + `SynchronizationMode(IfOnServer)` | no |
| **PlanBuild** | 0.18.6 | nothing, here | 286 refs, 0 unresolved | not installed | would be | **`ServerMustHaveMod` / Minor** | unknown; needs HookGenPatcher |
| **BiomeBlueprints** | 1.0.3 | **content pack**, 353 bodies | 50 refs, 0 unresolved | not installed | no | **none declared** | no |
| **PrefabHammer** | 1.2.0 | admin seat, marginally | 105 refs, 0 unresolved | not installed | no | none (no Jotunn) | no |
| **BuildOnShip** | 2.2.14 | gameplay rule, not a blueprint tool | 81 refs, 0 unresolved | server tree, `shared` | yes | none | no |
| **RuinsMaker** | 1.0.0 | aesthetic decay tool, not a blueprint tool | 95 refs, 0 unresolved | server tree, `shared` admin / `client-only` flat+vr | no | none | no |
| **ZenRedecorate** | 1.5.0 | admin seat, re-skinning placed pieces | 111 refs, 0 unresolved | server tree, `shared` | no | none | no |
| **`.vbuild`/BuildShare-lineage tooling** | — | nothing | — | not installed | — | — | — |
| **Gizmo / AdvancedBuilding lineage** | — | superseded | not installed | — | — | — | — |

Notes that matter, all MEASURED:

- **Nothing named `Gizmo` or `AdvancedBuilding` is installed anywhere.** PlanBuild
  0.18.6 still carries a soft-detect for `bruce.valheim.comfymods.gizmo` in its
  strings; Infinity Hammer's own `hammer_rotate` / `hammer_mirror` / `hammer_offset`
  supersede that lineage entirely. No action.
- **`scope` declarations lie in our tree too.** `PerfectPlacement` and
  `Structure_Tweaks` are declared `client-only` in `ulfsland-admin`,
  `ulfsland-dn` and `admin`, and **both DLLs are in the server plugin tree right
  now** — that is the armed admin window, consistent with the brief. Separately,
  `repos/valheim-portal-main/deploy/profiles/admin/profile-manifest.json` is
  **stale**: it says Infinity Hammer 1.79.0, `Searica-AdvancedTerrainModifiers 1.4.1`
  and `PerfectPlacement scope: shared`, none of which match either
  `/media/big4/.../profiles/ulfsland-admin/profile-manifest.json` or the deployed
  filesystem. Worth a separate ticket; I own documentation only.
- **`Ostrix-AdvancedTerrainModifiersCompatible` 1.4.8 is `shared` in all six
  editions and its DLL is in the server tree.** So the operator's "if we don't have
  already" is answered: we have it, on the server, in every edition. We are not
  using it (§6).
- **PerfectPlacement's one unresolved reference is real and explains a known
  symptom.** `ldsfld int64 ZRoutedRpc::Everybody` occurs three times in
  `PerfectPlacement.dll`, and the deployed game declares `ZNetView.Everybody` but
  has **no** `ZRoutedRpc.Everybody`. All three sites are inside its embedded
  `ServerSync.ConfigSync`: `sendZPackage(long target, ZPackage)`,
  `<AddConfigEntry>b__0` and `<AddCustomValue>b__1` — i.e. the peer-broadcast path.
  The installed `EverybodyShim` patcher forwards *methods* (its own log line says
  "appended-optional forwards, return-type forwards, delegated-return forwards")
  and `Valheim10Compatibility` bridges HarmonyX `AccessTools` field lookups —
  **neither injects a static field for a raw `ldsfld`**. **INFERRED, strongly
  supported**: this is the mechanism behind "PerfectPlacement disconnects every
  connected player when loaded server-side" — its config sync throws on any peer
  event. That makes dropping it a fix, not a loss.

### 3.1 The three named candidates

**`OverDrive/BiomeBlueprints` 1.0.3 — a CONTENT pack, not a tool.** MEASURED: 353
`.blueprint` bodies + 353 `.png` previews + one 15 kB `BiomeBlueprints.dll` whose
only job is registering a vanilla console command and gating pieces by known
recipes. It depends on `MathiasDecrock-PlanBuild-0.18.4`. It therefore belongs in
the library evaluation, and the numbers are striking:

- **614,663 piece rows** across the 353 files — 1.5× our entire current corpus.
- **SHA-256 overlap with our 176 bodies: zero.** All new material.
- **Every single one of the 353 files carries a `#Terrain` section.** 21,533 terrain
  rows, all genuine 8-field `square` operations, y-deltas in `[-12.98, 0.00]`.
  **Infinity Hammer would discard all 21,533, in all 353 files. Not one file is
  unaffected.** Our reader, mirroring Infinity Hammer, does the same.
- 133,738 rows carry a col-13 data blob; 33,615 carry `additionalInfo`; 2,686 carry
  non-unit scale.
- **431 of the 21,533 terrain rows (2.0%) exceed ±8 m in magnitude** and so cannot
  be realised through `TerrainComp` at all (§6).

**Licence, MEASURED**: there is **no LICENSE file in the package**. Its README says
the mod "redistributes the file" under per-author permission collected via a
submission form, credits contributors by `#Creator:`, and offers removal on request.
So its 353 bodies are third-party works redistributed under an informal grant to
*that* author. **INFERRED**: vendoring them into this repo is not covered by that
grant. If adopted they must be manifest+SHA-256 referenced exactly like the existing
171, which keeps our licence position unchanged — and as a Thunderstore package with
stable version pins, SHA-256 referencing is actually easy. Adopting it as a *mod*
would additionally drag in PlanBuild (`ServerMustHaveMod`) and HookGenPatcher.

**`DrakosDJ/PrefabHammer` 1.2.0 — we already have its capability.** MEASURED:
it registers **zero** console commands (no `Terminal`/`ConsoleCommand` memberrefs
at all). It is driven entirely by keybinds (`SaveHotkey`, `SelectHotkey`) plus a
chat command `/prefabsave`, saves to its **own `.json`** format under a `Blueprints`
folder as `Prefab_yyyyMMdd_HHmmss`, and messages like "No piece hovered. Target a
piece first." and "Cannot select custom prefab pieces." confirm hover-based
selection. It also bundles an unrelated player-to-player **trade** system
("Trade cancelled: You died or entered combat.", partner range checks). Against
`consoleCommand spawn <prefab> <x> <y> <z>`: it cannot be driven headlessly at all,
it introduces a fourth blueprint format that nothing else in our stack reads, and
it adds a gameplay subsystem we did not ask for. **Do not adopt.** For the admin
seat, Infinity Hammer's `hammer` + `hammer_save` already selects and saves arbitrary
prefabs by name, from the console, with the corpus format.

**`Ostrix/AdvancedTerrainModifiersCompatible` 1.4.8 — see §6.**

---

## 4. The proof that matters: `spawn_object … data=` works headlessly

Run on `/tmp/vhsandbox` (`ClearSandbox`), Valheim l-1.0.12 network version 40,
plugins BepInExPack + Server Devcommands 1.113.0 + World Edit Commands 1.77.0 +
Upgrade World + Valheim10Compatibility + ValheimRcon. `players` → **`Online 0`**.

I took a real corpus blob — a 14th column from a `TreasureChest` row carrying the
`Coins`/`BoneFragments`/`RS_EtherPotion` inventory above — and ran, over RCON:

```
consoleCommand spawn_object piece_chest pos=0,40,0 data=WwAAAAOt8M6uAACAwMwmXssAAIC/…
```

Server log: `Console: Spawned: piece_chest at 40, 0, 0`, then
`objects_count id=piece_chest` → `Total: 1` / `piece_chest: 1`. After `save`, I
parsed the ZDO straight out of the on-disk chunk with a reader written from the
`ZDO::Save` IL. **Sent vs landed, MEASURED:**

| sent in `data=` | landed on the ZDO |
|---|---|
| `WearNTear.m_health` = −4.0 | ✅ −4.0 |
| `health` = −1.0 | ✅ −1.0 |
| `support` = 1e19 | ❌ dropped (recomputed on Awake) |
| `scale` = (1,1,1) | ✅ (1,1,1) |
| `WearNTear.m_supports`, `HasFields`, `Piece.m_primaryTarget`, `HasFieldsPiece`, `Piece.m_randomTarget`, `addedDefaultItems`, `HasFieldsWearNTear` | ✅ all 7 ints |
| `InUse` = 0 | ❌ dropped |
| `xray_created` | ✅ |
| `steamID` = "", `steamName` = "" | ✅ both |
| `items` (string, base64 inventory) | ✅ **migrated to a byteArray containing the live inventory** — `j\x00\x00\x00\x03\x00\x00\x00\x05Coins\x03…` = version 106, 3 items, `Coins` ×3 |

**The chest came out of the blueprint filled, on a dedicated server with nobody
online.** Two fields were dropped (`support`, `InUse`) because the components
recompute them — which is correct behaviour, not loss.

Two MEASURED gotchas for the emitter:

- **`pos=` is `x,z,y`, not `x,y,z`.** `ServerDevcommands.Parse::VectorXZY`, help
  text `" (vec x,z,y)"`. Vanilla `spawn x y z` is `x,y,z`. Getting this wrong
  silently swaps height and depth.
- **`rot=` is euler `y,x,z`.** `Parse::AngleYXZ`, help text `" (quat y,x,z)"`.
  `to_rcon_plan.py` currently emits `-rotation <ex> <ey> <ez>` for vanilla `spawn`;
  the component order must be permuted for `spawn_object`.
- `data=` accepts base64 **directly**: `DataHelper.Get(name)` looks the value up in
  `data.yaml` and, on a miss, feeds the string to `new DataEntry(base64)`. It throws
  `"Can't load data value: "` if that fails and the string contains `=` or exceeds
  32 characters, so a malformed blob is loud, not silent (MEASURED from IL).

And the negative proof, same server, same zero players, with Infinity Hammer 1.83.0
copied in from the live deployment and the blueprint staged in its configured folder
(`Blueprint folder = PlanBuild` — MEASURED, its default really is PlanBuild's):

```
consoleCommand hammer_blueprint BlackForestRaiderTown1
→ Console: Error: No player.
```

---

## 5. Recommendations

### 5.1 Pipeline: no blueprint mod. Union reader + `spawn_object … data=`.

*Read this paragraph and nothing else if you like.* Neither blueprint mod can place
anything on a server with nobody online — Infinity Hammer answers `Error: No player.`
and PlanBuild is a GUI rune that needs Jotunn plus a preloader patcher we do not
have. Adopting either buys the pipeline nothing and costs a server deploy. What the
pipeline actually needs is already installed: World Edit Commands 1.77.0 accepts
`spawn_object <prefab> pos=x,z,y rot=y,x,z data=<base64>`, and I proved headlessly
that the blueprint's own 14th column lands on the ZDO — a treasure chest came out
of the blueprint **already holding its coins, bone fragments and potion**, along
with per-piece health, scale and every Infinity Hammer/Structure Tweaks override.
The one code change worth making is to stop mirroring Infinity Hammer's bugs in our
reader: parse the **union** of both tools' section vocabularies so the 464 props and
17 terrain operations we currently discard come through, and pass column 13 to
`spawn_object` instead of printing "object data lost on N". That is a strictly
better pipeline than any mod on the list, it needs no deploy, no client republish
and no new licence exposure, and it is the only option that can also consume
BiomeBlueprints' 353 bodies without throwing away all 21,533 of their terrain rows.

### 5.2 Admin seat: Infinity Hammer + Structure Tweaks + ATM. Drop PerfectPlacement.

*Read this paragraph and nothing else if you like.* Infinity Hammer 1.83.0 is the
latest release, has zero unresolved references against our deployed 1.0.12 assembly,
needs no Jotunn, enforces no client version match, and already exposes free
placement, free rotation, scaling, mirroring, offsetting, stacking in six directions,
`no_cost`/`no_physics`/`no_target`, and capture via `hammer_save` — the entire
PerfectPlacement feature set, as console commands the operator can drive over RCON
rather than a keybind held in a client. Structure Tweaks stays because it is the
mod that *interprets* the `override_wear` and `override_interact` keys already
present on 775 rows of our own corpus; without it those pieces read as ordinary.
`AdvancedTerrainModifiersCompatible` stays and should finally be *used* — it is
already on the server in every edition, gives the hoe precise radius/hardness control
with an on-screen overlay, and cannot corrupt anything we have written (§6).
PerfectPlacement goes: it is the oldest package in the set by eighteen months, its
embedded ServerSync reads a static field (`ZRoutedRpc.Everybody`) that no longer
exists in our assembly and that neither installed shim can bridge, it is the reason
the admin window has to be time-boxed at all, and everything it does is already done
better from the console by a mod we keep.

---

## 6. `AdvancedTerrainModifiersCompatible`, on the four questions asked

All MEASURED from `TerrainTools.dll` 1.4.8 IL unless marked.

1. **Does it raise or remove the ±8 m clamp? No. It reproduces it.**
   Vanilla `TerrainComp` clamps in **three** places — `ApplyToHeightmap`
   (base ±8), `LevelTerrain` (`Mathf.Clamp(-8f, 8f)` on `m_levelDelta`) and
   `RaiseTerrain` (same). ATM patches `RaiseTerrain` with its own
   `RaiseTerrainPrefix`, and that prefix contains `ldc.r4 -8. / ldc.r4 8. /
   Mathf::Clamp` applied to `m_levelDelta` — **the identical clamp**. It does not
   patch `LevelTerrain` or `ApplyToHeightmap` at all, so those clamps remain
   vanilla. Its configuration surface is radius, hardness and smoothing
   (`MaxRadius`, `HardnessModifier`, `RadiusScrollScale`, `$atmc_raise_hardness`,
   `$atmc_smoothing_hardness`) — **there is no clamp, limit or max-delta setting**.
   The Mistlands site at 22 m spread and the harbour at 43 m stay unflattenable.
   That verdict does not change.
2. **Headless? No — it is a hoe-side client tool, plainly.** Zero `Terminal` and
   zero `ConsoleCommand` references; 321 `TerrainOp` references; 13 `Hoe`;
   11 `Player.m_localPlayer`; 11 `Input`; a `GUIManager`; and its entire class list
   is overlays and visualisers (`LevelGroundOverlayVisualizer`,
   `RaiseGroundOverlayVisualizer`, `SquarePathOverlayVisualizer`,
   `CultivateOverlayVisualizer`, `SeedGrassOverlayVisualizer`,
   `UndoModificationsOverlayVisualizer`, `GroundLevelSpinner`, `HoverInfo`,
   `IconCache`, `GameCameraPatch`, `PlayerPatch`, `HeightmapPaintGridPatch`).
   **Admin-seat win; irrelevant to the pipeline.**
3. **Does it threaten our hand-synthesised `TCData`? No.** **MEASURED: zero
   references to `TCData`, `TerrainComp::Save` or `TerrainComp::Load` anywhere in
   the assembly.** It patches operations and client rendering, never serialisation.
   The 43 levelled zones and 27,275 m² of paving already written are not at risk.
4. **Is the DLL genuinely server-side? Yes.**
   `Ulfsland/data/bepinex/BepInEx/plugins/AdvancedTerrainModifiersCompatible/TerrainTools.dll`
   exists, 244,736 bytes, with a `manifest.json` reading 1.4.8 and depending on
   `BepInExPack_Valheim-5.4.2350` + `Jotunn-2.30.0` — and **Jotunn 2.30.0 is itself
   deployed server-side**. It is `shared` in all six profile manifests. Unlike
   `95Shade-CarryWeightSkill`, the filesystem agrees with the declaration here.

**One live hazard to record**: ATM declares
`NetworkCompatibility(CompatibilityLevel.VersionCheckOnly, VersionStrictness.Patch)`
plus `SynchronizationMode(AdminOnlyStrictness.IfOnServer)` (MEASURED; enum values
read out of the deployed `Jotunn.dll`, semantics from `Jotunn.xml`:
"Version check is performed when both server and client have the mod" /
"Mods must have the same Patch version"). It is installed on the server and `shared`
in every edition, so **any client on a different patch of ATM is refused a join**.
That is already true today and is a reason to bump it in lockstep across all six
editions or not at all.

---

## 7. Cost of the recommendation

### 7.1 What changes in `tools/jumpstart/blueprints/`

Nothing is thrown away. In particular the sibling's new vertical datum stays
exactly as it is: `base_geometry.floor_datum()` and
`placement_y = pad_target_y − base_y` operate on the *parsed object list*, and this
proposal only makes that list bigger and more faithful. The 101 of 162 bodies whose
supports sit below the walkable plane are unaffected — they are still resolved from
the piece colliders, by the same code, with the same `--verify` round trip.

1. **`inventory.py` — add a lossless mode.** `header_section()` currently reproduces
   Infinity Hammer exactly, on purpose. Keep that as the *compatibility* mode
   (it is the honest model of what the mod would do) and add a union mode that
   recognises `#Terrain` as a real section and classifies its rows by arity:
   ≥13 fields → a piece; 8 fields → a terrain operation. MEASURED, that single rule
   recovers **464 pieces and 17 terrain ops** in our corpus and **21,533 terrain
   ops** in BiomeBlueprints, and it is not a guess — the arity split is exactly how
   the corpus is written. Keep the `LegacyTerrainFormat` throw for `#Height:`/
   `#Paint:` (0 corpus files, latent hazard). `Piece.__slots__` gains `rot`, `info`
   and `data` so the two readers stop diverging.
2. **`to_rcon_plan.py` — emit `spawn_object`.** Replace
   `spawn <prefab> <x> <y> <z> -rotation <ex> <ey> <ez>` with
   `spawn_object <prefab> pos=<x>,<z>,<y> rot=<ey>,<ex>,<ez> data=<col13>`,
   **honouring the MEASURED `x,z,y` and `y,x,z` orderings** — and keep `--verify`
   pointed at them, because a silently swapped axis is precisely the class of bug
   that cost tonight's hours. The existing `scale lost on N / object data lost on N`
   stderr line becomes the regression signal: it must read 0 for `data`.
   Non-unit scale (7,836 rows) still needs a decision: `spawn_object` has no scale
   parameter, but `scale` is a vector3 key *inside* the data blob, so writing it
   there is the natural route.
3. **`classify.py` / `build_manifest.py` — re-derive.** `pieces` counts change for
   the 9 settlement files that gain props; `discarded`/`unknown_sections` become
   "recovered" columns. The verdict vocabulary is unaffected.
4. **New: a terrain sink.** The 17 + 21,533 recovered `shape;x;y;z;radius;rotation;smooth`
   rows need to reach the existing `TCData` synthesis path in
   `tools/jumpstart/terraform/`. This is the one genuinely new piece of work, and it
   is bounded: `circle`/`square` + radius + delta is a direct feed into the
   heightmap array we already build by hand.
5. **Chest manifest.** Keep it; make blueprint-carried inventories authoritative for
   the 122 rows that have one, so a chest is filled once and by one mechanism.

### 7.2 Do the 17 unusable and the gapped bodies become usable? No. Numbers.

MEASURED — and this is a clean negative, because the cause is not the parser.
(Note the manifest's own verdict tally over all 176 rows is 126 `PLACES_CLEAN`,
20 `PLACES_WITH_GAPS`, 17 `UNUSABLE`, 10 `EMPTY`, 3 `EXPLOIT/PLACES_WITH_GAPS`; the
"60 / 13 / 17" in the brief is the web-sourced subset.)

- **All 17 `UNUSABLE` fail on missing mod prefabs, not on parsing.** Seven
  `sh_firtree_light_*` files are 2 pieces each and 50% missing, needing
  `CL_Raw_{Black,Blue,Green,Orange,Purple,Red,Yellow}_small`. Ten `floatingisland*`
  files are ~341 pieces at 2.64–2.65% missing, needing `CL_Raw_Blue` ×4,
  `CL_Raw_Orange` ×2, `CL_Raw_Black` ×1, `RS_B…`. **No reader change and no mod on
  this list supplies those prefabs. 0 of 17 become usable.** The only thing that
  would is installing whichever mod owns the `CL_Raw_*` / `RS_*` families, which is
  a separate content decision.
- **Same for the 20 gapped bodies**: every gap is a named missing prefab
  (`CL_Raw_*`, `RS_Book_6`, `BHP_Skull3`, `MarketPlaceNPC`, `h_chain`,
  `emberwood_pillar4_bal`, `Totem3`, …) or a ZDO-spam flag on `meadhall`.
  **0 of 20 change.**
- **What does change**: the 9 settlement files that currently place silently
  incomplete become complete — `BlackForestRaiderTown1` recovers 187 of 1,090 rows
  (17.2%), `PlainsRaiderTown1` 93, `MeadowRaiderTown1` 73, `MeadowSettlerTown1` 49,
  `MistlandRaiderTown1` 43, `MountainRaiderTown1` 6, `MeadowSettlerTown2` 8,
  `PlainsRaiderTower1` 4. And **89 files / 125,272 rows** stop losing their ZDO
  payload at the emitter, which means 122 containers arrive filled, 53 signs arrive
  with text, 898 item stands arrive with the right item at the right quality, and
  2,146 pieces keep their crafter attribution.

### 7.3 Licence consequence

**The recommendation makes the licence position strictly better, because it adopts
no mod.**

- 171 of 176 bodies stay manifest+SHA-256 referenced. 5 stay committed. No change.
- Nothing needs vendoring. The reader change is ours.
- **No viral licence enters the server.** We install nothing. For contrast: PlanBuild
  would be a new server-side dependency, and `AdvancedTerrainModifiersCompatible`
  already ships a 35,823-byte `LICENSE` in its plugin folder — already accepted,
  already deployed, unchanged by this proposal.
- If BiomeBlueprints' 353 bodies are wanted, take them **by manifest reference
  only**. There is no LICENSE file in that package; its bodies are third-party
  works redistributed under an informal per-author grant to that author, which
  **INFERRED** does not extend to us. Referencing by SHA-256 against a pinned
  Thunderstore version keeps us exactly where we are today.
- One privacy item worth flagging: **8,620 corpus rows carry a `steamID` and a
  `steamName`** inside column 13 (e.g. `76561197962116195` / `Shigzula`), and 2,146
  carry a crafter or creator name. Passing column 13 through to `spawn_object`
  writes third parties' Steam IDs into our world save. **Recommend stripping
  `steamID`, `steamName`, `creator`, `crafterID`, `crafterName` and `xray_created`
  from the blob before emit** — trivial with the decoder in §1.1, and it costs
  nothing we want.

### 7.4 Rollout cost

**Pipeline recommendation: zero server deploy, zero client republish, zero lockout
risk.** It is a change to `tools/jumpstart/blueprints/` only. `spawn_object`,
`objects_count` and `zones_generate` were all exercised over RCON against a
dedicated server with zero players (§4) using mods that are already deployed at the
versions already deployed.

**Admin-seat recommendation: one server deploy, and it is a removal.** Dropping
`Azumatt-PerfectPlacement` 1.2.2:

- It is declared `client-only` in `admin`, `ulfsland-admin` and `ulfsland-dn`, and
  absent from `flat`, `ulfsland-flat`, `vr`, `ulfsland-vr`. So the **client editions
  that must republish are the three admin editions only**; the four player editions
  are untouched.
- It embeds ServerSync but declares no `ModRequired` lock and no Jotunn
  `NetworkCompatibility` attribute, so **there is no join-refusal hazard on removal**
  — unlike the ServerSync 1.4.16-vs-1.4.17 refusal seen tonight.
- Its DLL must also be removed from the server tree, where it currently sits behind
  the admin window.

**Lockout hazards that exist regardless, recorded so they are not discovered later:**

| mod | enforcement | consequence |
|---|---|---|
| `OdinArchitect` 1.7.2 | `EveryoneMustHaveMod` / **Minor** | every client must have it at minor 7. 1.7.5 is safe (same minor); a 1.8.x bump forces all six editions to republish together or joins are refused. |
| `AdvancedTerrainModifiersCompatible` 1.4.8 | `VersionCheckOnly` / **Patch** | installed server-side and `shared` in all six editions: any client on a different **patch** is refused. Bump in lockstep or not at all. |
| `PlanBuild` 0.18.6 (if ever adopted) | `ServerMustHaveMod` / **Minor** | today the inverse bites: **an operator client running PlanBuild is refused by Ulfsland**, because the server does not have it. Adopting it would also require `HookGenPatcher`, absent from our deployment. |
| `BiomeBlueprints` 1.0.3 | **no `NetworkCompatibility` attribute** | not enforced. But it needs the pre-1.0 12-arg `Terminal.ConsoleCommand` constructor — MEASURED as injected by our `Valheim10Compatibility` patcher, so it would only work *because* of that shim. |

### 7.5 What to DROP

| drop | why |
|---|---|
| **`Azumatt-PerfectPlacement` 1.2.2** — **drop, superseded and broken** | Every function is already in Infinity Hammer as a console command (`hammer_move`, `hammer_rotate`, `hammer_scale`, `hammer_mirror`, `hammer_offset`, `hammer_stack_*`, `no_cost`, `no_physics`, `no_target`). It is the oldest package in the build set (2025-03-06 vs 2026-09-13 for the rest). It is the **only** mod in the whole set with a genuine unresolved reference into our deployed assembly (`ZRoutedRpc.Everybody`, 3 sites, all in its embedded ServerSync peer-broadcast path), which neither `EverybodyShim` nor `Valheim10Compatibility` can bridge because it is a raw `ldsfld` on a static field. It is half the reason the admin window must be time-boxed. Dropping it removes a 1.0-migration liability, a disconnect hazard and three client-edition republishes' worth of future churn, and loses nothing. |
| **`Neobotics-RuinsMaker` 1.0.0** — **drop candidate, out of job** | Not a blueprint tool: a decay/ruination aesthetic tool by Neobotics with an audio helper and a `ServerDelegate` config shim, declaring `BepInExPack_Valheim-5.4.2100` — the oldest BepInEx pin in the build set. `shared` in the three admin editions, `client-only` in the four player editions. Zero corpus bodies depend on any prefab it owns. Keep only if the operator actively uses it for set-dressing; otherwise it is pure 1.0-migration surface. |
| **`OdinPlus-OdinArchitect` 1.7.2** — **keep, but reclassify** | It is player-facing **content**, not a blueprint tool, and it is the single largest lockout liability in the set (`EveryoneMustHaveMod`/Minor) plus a 30.9 MB DLL. Measured corpus dependence is tiny: it supplies **3 prefabs** (`IG_Skull_1`, `IG_Skull_2`, `wooden_gate_1`) across **12 files, 12 rows** out of 407,860. So dropping it would cost 12 pieces and push two `brokkr-broken-bridge-2*` bodies and `BjOrN_blueprint001` from "mod-supplied" to a ≤0.1% gap. That is a content decision, not a tooling one — but it should stop being counted as one of "four build mods". |
| **`Skarif-BuildOnShip` 2.2.14** — **keep, out of scope** | A gameplay rule (build on ships), not a blueprint tool. No ServerSync, no `NetworkCompatibility`, 0 unresolved references. Judge it on gameplay, not on this axis. |
| **do NOT adopt `PlanBuild`** | Its parser is a *regression* on our corpus (§2.4): +481 rows in 18 files, −125,272 data payloads in 89 files, −`#Center:`/`#Coordinates:`/`#Rotation:` in 47 files. It cannot place headlessly, needs Jotunn + a `HookGenPatcher` preloader we do not have, and is `ServerMustHaveMod`. Take its **`#Terrain` section** into our own reader instead. |
| **do NOT adopt `PrefabHammer`** | Zero console commands, keybind/`/prefabsave`-driven, its own fourth `.json` format, plus an unrelated trade subsystem. `consoleCommand spawn`/`spawn_object` already does the job headlessly. |
| **do NOT adopt `BiomeBlueprints` as a mod** | It is content. Take the 353 bodies by manifest + SHA-256 if wanted; installing the package would drag in PlanBuild and HookGenPatcher for no placement capability. |
| **nothing to drop for `Gizmo`/`AdvancedBuilding`/BuildShare** | None are installed. `.vbuild` is a strict capability subset (§1) and needs no tool beyond our own reader, which already handles it. |

Net effect on the "four build mods": **Infinity Hammer stays** (admin seat, latest,
clean). **Structure Tweaks stays** (admin seat, and it interprets 775 rows of our
own corpus). **PerfectPlacement goes.** **OdinArchitect and BuildOnShip are content
and gameplay, not blueprint tooling, and should be judged on that basis.**

---

## 8. Corpus defects found while measuring (reported, not fixed)

**Ten 0-byte bodies.** The manifest claims each by SHA-256, but the field is empty
because `classify.sha256()` returns `""` for a zero-length file — so the manifest is
overstating what the library holds by 10 rows. All are `kind=empty`,
`origin=fleet corpus`, `licence=none stated`:

```
PuP_Owl-tree_house.blueprint          PuP_rampart1.blueprint
PuP_stable.blueprint                  PuP_tavern.blueprint
SerpentShip.blueprint                 SerpentShip_Empty.blueprint
serpent.blueprint                     ragnar-warehouse.blueprint
salty-dick-mordach-castle-final.blueprint
salty-dick-tower-test.blueprint
```

**Two duplicate names, and they are not the same case.**

- `salty-dick-cottage-final.blueprint` — **identical bodies.** Both rows carry
  `sha256=6b588d9c46e22456…`, 877 pieces, `PLACES_CLEAN`; the origins differ
  (`fleet corpus` and `offsetkeyz/valheim_mod_sync`). Harmless duplicate row.
- `brokkr-the-cathedral.blueprint` — **the bodies DIFFER.**
  `Oosquai/SavheimIV` gives `sha256=3060609d536bf8ab…` with **8,269** pieces;
  `offsetkeyz/valheim_mod_sync` gives `sha256=8127bac49f366004…` with **8,240**.
  Two different buildings under one name, 29 pieces apart. Any lookup by name is
  ambiguous and will silently pick one.

Net: 176 manifest rows → **174 unique names → 164 non-empty bodies resolvable on
disk**, all 164 of which I parsed for this document.

---

## 9. Reproducing this

Everything lives in `/tmp/bp-eval` (scratch, not committed):

| script | what it establishes |
|---|---|
| `resolve.py` | resolves manifest rows to on-disk bodies by SHA-256 → `resolved.json` |
| `measure.py` | full-fidelity vs Infinity-Hammer-equivalent parse of all 164 bodies → `measured.json` |
| `zdo2.py` | the column-13 `DataEntry` decoder, written from `DataEntry::Load(ZPackage)` IL |
| `chunk.py` | on-disk ZDO reader, written from `ZDO::Save` IL, used for the §4 read-back |
| `refcheck3.py` | signature-aware unresolved-reference check; `--drop=Type.Member` runs the control |
| `sbrcon.py` | minimal Source RCON client for the sandbox |

Disassembly: writable copies of `assembly_valheim.dll` plus the full `Managed/`
reference set in `/tmp/bp-eval/managed` — `monodis` aborts without the references
present — then `monodis --output=… <dll>` per plugin. Candidate mods were fetched
from Thunderstore into `/tmp/bp-eval/dl`.

The sandbox was left with the same plugin set and config it had before
(`Infinity_Hammer`, `config/PlanBuild`, `config/blueprints`, `infinity_hammer.cfg`
and `infinity_tools.yaml` removed again); the throwaway `ClearSandbox` world does
now contain one data-bearing chest, one control chest and one bed from §4.
