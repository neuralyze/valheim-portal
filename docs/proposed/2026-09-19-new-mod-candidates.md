# New-mod candidates for Ulfsland — 2026-09-19

Not a version audit. Every package in the four `ulfsland-*` profiles is already at
Thunderstore latest. This asks the other question: **what is this setup missing, or
doing badly, that a different mod would do better?**

Read-only survey. Nothing installed, deployed, published, or written to the world.

---

## 0. Instruments, and what each one can and cannot see

| Instrument | What it reads | Validated by |
|---|---|---|
| `/tmp/nms/refcheck.py` | ECMA-335 `TypeRef` / `MemberRef` tables via `dnfile`, resolved against the live `assembly_valheim.dll` (sha256 `6d79c900…b07312`) plus `assembly_utils` / `assembly_guiutils` | Reproduces the known CLLC defect `Minimap::noForest` and nothing else across 93 installed assemblies |
| `tools/vr_impact_scan.py` (repo) | the seven VHVR incompatibility classes C1–C7, with method-level evidence | Already the project's stage-1 VR gate |
| `.NET #US` user-string heap | UTF-16LE literals in the metadata heap | Used instead of `strings(1)`, which is invalid over asset-bundle mods |
| PieceManager-generated `.cfg` section list | the pieces a mod **actually registered at runtime** | This is a runtime instrument, not a package instrument |

**Limits I hit, stated rather than papered over:**

1. **A name-only reference check cannot see a retyped field.** `PieceTable::m_availablePieces`
   still exists by that name in 1.0.15 — it changed *type* from `List<List<Piece>>` to
   `HashSet<Piece>`. Every mod that touches it therefore scans **clean** on my instrument.
   The instrument for that defect class is the `Valheim10Compatibility.Patcher` string table,
   which names the field and the alias it injects.
2. **RavenwoodUnderground registers from an asset bundle** (`BalrondRavenWoodBuildPieces`).
   Its `#US` heap holds no piece names, so I could not enumerate it piece-by-piece from the
   package. Recorded as unmeasured; it is disqualified on other grounds below.

---

## 1. Every "gap" checked against what is already installed — BEFORE calling it missing

This is the step that was skipped last time. Three of the six named gaps turn out to be
**already covered by something installed**.

### 1.1 CONTAINER CAPACITY — **NOT a gap. It is installed TWICE, and the two disagree.**

| Installed | Where | Mechanism | Measured |
|---|---|---|---|
| `ArgusMagnus-ServersideQoL_ContainerSizes 2.0.11` | `ulfsland-dn` (server) only | server-authoritative; sets `Container::m_width/m_height` through the ServersideQoL ZDO field accessor | `Enabled = true`; config generated with **110 distinct container types**, all at **vanilla** values |
| `Cytraen-BiggerChests 1.1.0` | all four profiles, `scope=shared` | Harmony prefix on `Container.Awake`, writes `m_width`/`m_height` on the instance | **hardcoded, no config file exists**; covers 6 container types |

BiggerChests' exact hardcoded values, read from IL (`ContainerSizePatch::PatchContainerSize`):

| Container | BiggerChests | ContainerSizes config (= vanilla) |
|---|---|---|
| cart (`m_wagon`) | 8x4 = 32 | 6x3 = 18 |
| `VikingShip` | 7x4 = 28 | 6x3 = 18 |
| `Karve` | 3x3 = 9 | 2x2 = 4 |
| `piece_chest_wood` | 5x3 = 15 | 5x2 = 10 |
| `piece_chest_private` | 5x2 = 10 | 3x2 = 6 |
| `piece_chest(` (reinforced) | 7x4 = 28 | 6x4 = 24 |
| `piece_chest_blackmetal` | **explicitly skipped** | 8x4 = 32 |

`ContainerProcessor::Process` reads the container's current `Inventory.GetWidth/GetHeight`,
looks the prefab up in `_containerSizes`, and writes the configured size. **There is no
"skip if unchanged" gate.** ContainerSizes runs on the server as a ZDO processor after
`Awake`; BiggerChests is not on the server-authoritative path for ZDO state.

Net shape: **clients render BiggerChests' larger grid, the server enforces vanilla.**
A server-side resize *down* is the classic way overflow items are dropped or erased.

It is harmless **today** only because nobody has ever played and no container holds anything.
That is also the only window in which it can be fixed for free.

> **Verdict: remove `Cytraen-BiggerChests 1.1.0`; configure the already-installed
> `ServersideQoL_ContainerSizes` instead.** BiggerChests registers **no prefab**, so removal
> orphans nothing and cannot break replay. It was last updated **2025-04-08** — pre-Valheim-1.0,
> no config, rating 2, 8 dependants. ContainerSizes is at 2026-09-16, server-authoritative,
> covers **110** container types against BiggerChests' 6 — and unlike BiggerChests it already
> enumerates the *modded* containers in this profile (`rae_bronze_storage_*`, `rae_iron_storage_*`
> from OdinArchitect, `rid_drawer_wood/stone/blackmarble` from RossItemDrawers,
> `stone_cart`, `workbench_cart`) — and is **already paid for**.
> *This costs zero new packages and is the highest-value change in this document.*

### 1.2 CLLC `Minimap::noForest` — **NOT a gap. Already repaired by an installed patcher.**

`L4zerShark_Team-CLLCCompatibility 1.0.1` is installed in all four profiles at
`BepInEx/patchers/CLLCCompatibility.dll`. Its `#US` heap contains, in order:

```
'Minimap', 'noForest', 'Minimap replacement must be static Color', 's_noForestColor'
```

It rewrites `CreatureLevelControl.dll` in memory, mapping the removed instance field to the
1.0.15 static `s_noForestColor`. Its README states it "Updates … minimap color access."
**That is why the reference appears zero times in the operator's `Player.log`** — it never
reaches the runtime. No fork or fixed build is needed.

Worth knowing anyway: `Smoothbrain-CreatureLevelAndLootControl` is **deprecated on
Thunderstore** and last updated **2025-05-26**. The compat patcher is the maintained half
of that pair now.

### 1.3 CRAFT-FROM-CHESTS and STORAGE — already dense. Do not add to it.

`Azumatt-AzuCraftyBoxes 1.8.19`, `Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock 1.4.15`,
`MSchmoecker-MultiUserChest 0.6.2`, `ArgusMagnus-ServersideQoL_AutoStore 2.0.11`,
`Ross-RossItemDrawers 1.0.11`. Any "all-in-one storage" mod (`ChestFlow`, `BestAutoSort`,
`SmartCraftStorage`, `StoreAndCraft`, `Hearthkeeper`, `GrizzlyAutomations`) would collide
with four installed mods at once. **Rejected on sight, all of them.**

### 1.4 ANIMAL AUTOFEED — **genuinely absent. Confirmed against five near-misses.**

| Installed, checked | What it actually does |
|---|---|
| `ZenDragon-ZenBreeding 1.0.0` | *sterilises offspring* to stop exponential herds. README: "Only wild animals that you tame can produce offspring." No feeding. |
| `TastyChickenLegs-AutomaticFuel 1.5.1` | fuels torches/kilns/smelters. No creatures. |
| `TastyChickenLegs-AutomaticFermenters 1.1.2` | fermenters only. |
| `ServersideQoL_AutoProcess 2.0.11` | "processing stations such as smelters, kilns, windmills … and shield generators". No creatures. |
| `Azumatt-AzuCraftyBoxes 1.8.19` | craft-from-chest. No creatures. |

I also downloaded and read `ServersideQoL_TameAssist 2.0.11` — the module in the family
already installed four times over, the one that *sounds* like it would cover this.
Its feature list is "fed duration, taming time, follow, through-portals, progress display."
**It does not feed.** Checked before recommending, as required.

### 1.5 CHEST TIER UPGRADES — **genuinely absent.**

Nothing installed upgrades a chest in place. `RavenwoodRestorations` registers five *extra*
chest pieces (Wood / Fine Wood / Core Wood / Old Wood / Raven Wood Chest) — additional pieces,
not tier upgrades. `BiggerChests` and `ContainerSizes` change slot counts, not tiers.
`Ravenwood_Currency`'s "six upgrade extensions" are crafting-station extensions.

### 1.6 BUILD MENU — the installed mod is **deprecated**, and there is no VR-safe replacement.

`Azumatt-AAABuildMenu 1.0.4` is **`is_deprecated: true` on Thunderstore**, and is already
excluded from `ulfsland-vr` (custom 2D panel, four unreachable hotkeys). The vanilla grid
is `m_gridWidth` × `m_gridHeight` = 15 × 6 = 90 cells and `BuildingWorkbench` alone registers
90 pieces in stock 1.0.12, so the base game overflows its own menu before any mod.
Candidates measured in §2.4 — **all three fail in VR**.

---

## 2. Candidates, measured

Dependency floors are recorded but **never used as a gate** — BepInEx does not enforce them,
Jotunn's compare ignores them, and 37 installed packages already declare 5.4.2350 against a
5.4.2333 loader.

Every DLL below was reference-checked against the live 1.0.15 assembly. **Unresolvable
references are called out explicitly; everything not called out resolved clean.**

### 2.1 Animal autofeed

| Candidate | Updated | dl | Dep'ts | Floors | Registration | 1.0.15 refs | VR (repo scanner) |
|---|---|---|---|---|---|---|---|
| **`Vapok-AutoFeedRedux 2.0.7`** | **2026-09-19** | 742 | 75 | Jotunn 2.30.1, YamlDotNet 16.3.1, BepInEx 5.4.2350 | AI patch only, **no new prefab** | **clean** | 42 high (C4×2, C7×4) |
| `Narolith-AutoFeed 1.0.3` | 2026-05-29 | 8430 | 33 | Jotunn 2.20.1 | no new prefab | clean | **0 findings** |
| `StonedParadise-AnimalFeedingTrough 1.0.4` | 2026-09-18 | 836 | 0 | Jotunn 2.30.1, BepInEx 5.4.2350 | **new prefab** (Furniture piece) | clean | **0 findings** |
| `Dafini-Manger 1.0.2` | 2026-09-18 | 126 | 0 | BepInEx 5.4.2350 | **new prefab** | clean | 3 low (C6×3) |
| `Gizmorphium-AutoFeedWithContainerFilter 0.1.1` | 2025-06-15 | 4352 | — | BepInEx 5.4.2202 | no new prefab | clean | 20 high (C4×2) |

**AutoFeedRedux's VR score of 42 is misleading, and the measurement says so:**

- Its **C7×4** findings are all inside `<SnapshotItem>g__Do|0` — the PieceManager/ItemManager
  one-shot icon renderer. The installed `blacks7ar-Mask 1.0.3` and
  `OdinPlus-OdinsFoodBarrels 1.2.8` each score **exactly 22 high with the same C7×4
  `SnapshotItem` pattern** and already ship on VR. This is **baseline, not a new risk**.
- Its **C4×2** findings are in the shared `Vapok.Common.Tools.KeyPressTool`. The scanner's
  "recovered KeyboardShortcut config defaults" section lists **zero** entries for AutoFeedRedux
  — the mod **binds no keyboard shortcut at all**. Its entire config surface is toggles and
  ranges. The `Input.GetKey` is unreached shared-library code.
- For scale: the **installed and VR-shipping** `Vapok-AdventureBackpacks 2.1.0` scores
  **101 high** (C3×4, C4×5, C6×1, C7×4) with three *real* bound keys (I, Y, L).

**Recommendation: `Vapok-AutoFeedRedux 2.0.7`.**

- **Adds:** tames pathfind to existing containers and eat from them directly.
- **Already provided by:** nothing (five near-misses ruled out in §1.4).
- **Displaces / orphans:** nothing. No prefab is registered, so **zero replay exposure** —
  which matters now that the ledger is the deliverable.
- Its three declared dependencies are **exactly** the three versions already installed.
- README documents explicit compatibility with **MultiUserChest** and **AdventureBackpacks**,
  both installed — MUC matters, because it rewrites container inventory access.
- "Feeding searches are event-driven with zero per-frame update loops" — the right shape for
  a 100-mod server. ServerSync-enforced.
- Author pushed 2.0.7 **today**.

*Runner-up:* `Dafini-Manger 1.0.2` if a piece is preferred to an AI patch — it patches nothing
at all ("Nothing about feeding is patched"), just parks a real item where vanilla's own hunger
AI finds it, and it is the VR-cleanest thing in the batch at score 3. Costs a new prefab.
`Narolith-AutoFeed` is VR-clean but runs feeding **on each client**, defaults to a
`piece_chest` prefix filter that would miss every modded container here, and has not moved
since May. `Gizmorphium` is an unmaintained Aedenthorn fork — reject.

### 2.2 Chest tier upgrades

| Candidate | Updated | dl | Dep'ts | Floors | 1.0.15 refs | VR |
|---|---|---|---|---|---|---|
| `KuliTeam-ChestUpgradeMod 1.2.1` | 2026-09-19 | **136** | **0** | BepInEx 5.4.2350 | clean | **unusable** |

Both of its input paths are dead in VR, measured:

- `ZInput.GetButton("Attack")` — *"VHVR blacklists this name in `initIgnoredZInputs` (:986-996),
  so the read never returns true."* The hammer-click replace path never fires.
- `UpgradeKey` via raw `Input.GetKeyDown` — *"VHVR patches `Input.GetKeyDownInt/GetKeyInt`
  only for the text-input return key, so nothing else reaches VR."*

**Recommendation: nothing. Do not install.** It is 136 downloads and zero dependants at one
day old, it is 100% unusable in VR, and its whole function — swapping the prefab at a fixed
position — is the one operation the ledger's prefab+position key cannot follow silently.
This is the lowest-value gap on the list on an unplayed world with zero chests in use.

### 2.3 Piece-set replacements — see §3. Short answer: **none, and do not disturb them.**

### 2.4 A VR-safe build menu — **nothing good exists. This is the honest answer.**

| Candidate | Updated | dl | Dep'ts | 1.0.15 refs | VR score | Why it fails in VR |
|---|---|---|---|---|---|---|
| `jg224-CraftIndex 0.5.8` | 2026-09-18 | 381 | 2 | clean | **112 high** | C3×7 unbound ZInput + **4 real KeyboardShortcut binds** (PageUp, PageDown, `Hammer/FavoriteHotkey`=F) |
| `ValheimEnthusiestKakimaki-Build_Hotbar 0.9.1` | 2026-09-17 | 1211 | 4 | clean | **107 high** | its advertised "controller radial menu" polls `JoyButtonA/B`, `JoyDPad*`, `JoyLTrigger`, `JoyRTrigger` — **all blacklisted by VHVR**, so the radial menu never opens from a headset |
| `JamesJonesTV-Legacy_Build_Menu 1.0.0` | 2026-09-11 | 756 | 1 | clean | **69 high** | C3×7 including `JoyBuildMenu`; movable 2D panel + text search bar |

`Build_Hotbar` was the most promising hypothesis on paper — "paged radial menu, keyboard **and
controller**" is exactly the VR-friendly shape. **The measurement killed it:** VHVR ignores
every gamepad button it reads.

**Recommendation: nothing for VR.** Keep `AAABuildMenu 1.0.4` on flat/admin as-is despite its
deprecation — it works, and no maintained successor is better. If the VR build menu is to be
fixed properly, the correct shape is a small local patcher that raises
`PieceTable::m_gridWidth`/`m_gridHeight` (both still present in 1.0.15) so the **vanilla** grid
holds more than 90 cells. That introduces no panel, no hotkey, and no new prefab. **No mod on
Thunderstore does this** — I searched the full 11,506-package index on grid/rows/scroll/page/
category/hammer/build-menu terms.

---

## 3. Replacements for the three abandoned piece sets — measured piece by piece

### 3.0 The motive does not survive contact

The stated reason to replace them is that they reference `PieceTable::m_availablePieces`,
retyped in 1.0, and only work because `Valheim10Compatibility 1.4.1` aliases it.

**Measured across all 93 installed assemblies, seven reference that field — not three:**

```
AdventureBackpacks/AdventureBackpacks.dll          <- installed, maintained, NOT a piece set
Jotunn/Jotunn.dll                                  <- 2.30.1, 1.0-aware, handles both shapes
OdinShip/OdinShip.dll                              <- installed, maintained, NOT a piece set
RossItemDrawers/ItemDrawers.dll                    <- installed, maintained, NOT a piece set
OdinsHorsePen/OdinsHorsePen.dll                    <- abandoned piece set
OdinsUndercroft/OdinUndercroft.dll                 <- abandoned piece set
RavenwoodRestorations/RavenwoodRestorations.dll    <- abandoned piece set
```

**Removing all three piece sets would not let you drop `Valheim10Compatibility`**, because
AdventureBackpacks 2.1.0, OdinShip 0.8.1 and RossItemDrawers 1.0.11 need the same alias.
The patcher stays either way. The only thing a replacement buys is a maintained upstream —
and it costs a prefab-name change, which orphans placed content in a world with 2,905 ledger
records.

### 3.1 `OdinPlus-OdinsUndercroft 1.3.3` — **34 distinct registered pieces**

Source: 37 sections in the generated `gravebear.odinsundercroft.cfg`, minus three
duplicate-name pairs (`OU_SkeletonFull`/`Odins Skeleton Full`, `OU_SkeletonHanging`/
`Odins Skeleton Hanging`, `OdinsMinicroft`/`Odins Minicroft`). Matches the operator's "~34".

**Candidate: `blacks7ar-RefinedStonePieces 1.1.1`** — 2026-09-14, 3994 dl, **233 dependants**,
rating 33, author active and already trusted here (6 blacks7ar mods installed), 1.0.15 refs
clean, VR score 22 = **exactly the installed `Mask`/`OdinsFoodBarrels` baseline**.
**104 registered `BRP_*` pieces**, enumerated from the `#US` heap.

**Coverage: 15 of 34 = 44%.**

Covered (RefinedStonePieces equivalent in brackets):

| # | OdinsUndercroft piece | RefinedStonePieces |
|---|---|---|
| 1 | Odins Large Stone Pillar | `BRP_StoneColumn1` / `BRP_StoneColumn2` |
| 2 | Odins Medium Stone Pillar | `BRP_StonePillar1` / `BRP_StonePillar_BaseMedium` |
| 3 | Odins Stone Archway | `BRP_StoneArch` … `BRP_StoneArch6` |
| 4 | Odins Stone Beam | `BRP_StoneBeam2m` |
| 5 | Odins Stone Beam Small | `BRP_StoneBeam1m` |
| 6 | Odins Stone Floor 1x1 | `BRP_StoneFloor_1x1` |
| 7 | Odins Stone Floor 2x2 | `BRP_StoneFloor_2x2` |
| 8 | Odins Stone Floor | `BRP_StoneFloor1` / `BRP_StoneFloor_Large` |
| 9 | Odins Stone Outside Corner | `BRP_StoneBlock_OutCorner` |
| 10 | Odins Stone Roof 45 | `BRP_StoneRoof45` |
| 11 | Odins Stone Roof Corner | `BRP_StoneRoof_OCorner45` / `_ICorner45` |
| 12 | Odins Stone Stairs | `BRP_StoneStair1` / `BRP_StoneStair2` / `BRP_RefinedStone_SpiralStair` |
| 13 | Odins StoneHalfWall | `BRP_StoneHalfWall_1x2` |
| 14 | Odins StoneRoof Tile | `BRP_StoneRoof26` |
| 15 | Odins StoneWall | `BRP_StoneWall2x2` / `BRP_StoneWall4x2` |

**Uncovered — 19 pieces, NAMED:**

> **Odins Undercroft**, **Odins Minicroft**, Odins Stone Floor 2x1, Odins CornerCap,
> Odins CornerCap Small, Odins Drainpipe, Odins BlackMetal Cage, Odins Iron Cage,
> Odins MetalGrate, Odins Crafting Skull, Odins Crossed Swords, Odins Sarcophagus,
> Odins Sarcophagus Lid, Odins Skeleton Full, Odins Skeleton Hanging, Odins Skeleton Pile,
> Odins Skeleton Ribs, Odins Urn, Odins Wall Shield.

The two in bold are the **instanced private-basement rooms** — the entire reason the mod
exists ("A `Basement` alternative, a more castle-like private basement"). The remaining 17
are the crypt-dressing vocabulary, which RefinedStonePieces has no analogue for at all.

**Verdict: PARTIAL (15/34, 44%) — and not a replacement in any useful sense, because the
two headline pieces and the whole decorative vocabulary are missing.** Prefab names are
disjoint (`BRP_*` vs `Odins *`), so a swap cannot carry placed pieces either.

*Other candidate considered:* `JamesJonesTV-RavenwoodUnderground 1.0.1` — "nearly 100
building structures from the vanilla burial chambers, crypts, caves", 9590 dl, refs clean.
**Coverage unmeasured**: it registers from the `BalrondRavenWoodBuildPieces` asset bundle and
its `#US` heap holds no piece names, so I could not enumerate it from the package.
**Disqualified anyway on maintenance**: last updated **2025-06-23**, pre-Valheim-1.0 — the
identical abandonment it would be curing. Trading one abandoned set for an older abandoned set.

### 3.2 `OdinPlus-OdinsHorsePen 1.1.0` — **2 registered pieces**

The generated `org.bepinex.plugins.odinshorsepen.cfg` has exactly two sections:
`Odins Pen Trap Custom` (horse pen) and `Odins Nest Trap Custom` (chicken pen).
The README confirms the whole feature set: "Wood pen for horse or other tames / Chicken Pen
for smaller tames."

Already installed and adjacent: `OdinsKingdom 1.5.9` registers `Old Iron Fence`,
`Old Iron Fence Door`, `Iron Fence Endcap`, `Wood Rope Fence`; vanilla has `wood_fence`.
These hold tames when assembled by hand — what OdinsHorsePen adds is a **one-click
prefabricated enclosure**, not a capability.

**Verdict: no replacement needed and none worth naming.** This is 2 convenience pieces,
not a set. Leave it.

### 3.3 `JamesJonesTV-RavenwoodRestorations 1.0.1` — **120 registered pieces**

From `Azumatt.RavenwoodRestorations.cfg`: 122 sections minus `[1 - General]` and
`[2 - Collector]`. It is not a small set — 20 `Farmhouse*` prefabs, 24 bookshelf variants,
20 animal statues in stone and metal, 6 mailboxes, 9 board variants, 5 chests, 7 Raven*
material stacks, 6 Raven* crafting stations, 4 runestone tile floors, 10 farm signs.

There is **no single maintained mod on Thunderstore that covers 120 pieces of that mix**.
Replacement would take four or five mods, each with disjoint prefab names.

Also note the package itself was **updated 2026-09-08** — eleven days ago — and the author
also shipped `RavenwoodVikingHouses 7.7.8` on **2026-09-17**. Calling this one "abandoned"
does not survive the dates; only `OdinsHorsePen` (2026-02-05) and `OdinsUndercroft`
(2026-07-24) have genuinely gone quiet, and neither is very old either.

**Verdict: NOT A REPLACEMENT — nothing comes close.**

### 3.4 Honest conclusion on §3

**Leave all three alone.** Zero of their pieces are standing, the compat patcher makes them
work, the patcher cannot be removed regardless because three *maintained* installed mods need
the same alias, the best candidate covers 44% of one set while missing both its headline
pieces, and every swap renames prefabs in a world with 2,905 ledger records whose replay keys
on prefab + position. **A PARTIAL is not worth disturbing a working setup for. It is not
worth it here.**

---

## 4. Beyond the named gaps — three I would defend

Judged for a 100-mod, Valheim 1.0.15, flat + VR setup on an **unplayed** world with a
1,004-piece harbour city, 15 settlements, 11,453.8 m of road and 15,683 surviving placements.
All three register **no new prefab**, so all three are **replay-neutral**.

### 4.1 `MidnightMods-AsyncSave 0.5.0` — the biggest felt win

2026-09-09 · 1335 dl · 7 dependants · floor BepInEx 5.4.2333 (no Jotunn) ·
**1.0.15 refs: clean** · **VR scan: 0 findings**

Vanilla's `ZNet.SaveWorld` clones every ZDO in one frame. On a world this size that is the
single worst stall a player will feel, and in VR a main-thread stall is nausea, not an
annoyance. AsyncSave walks `ZDOMan`'s sectors across frames with atomic per-sector scratch
copies, `AddToSector` catch-up tracking, and revision reconciliation.

Decisive line from its README for this project specifically:

> "Nothing about the save *format* changes — the bytes it writes are the bytes vanilla would
> have written."

Given that the ledger is now the deliverable and the world gets folded and replayed onto a
fresh seed, a save-path mod that provably does not alter the byte stream is the only kind
that is safe to take.

Vendor already trusted here (`ImpactfulSkills`, `ValheimArmory` installed). No network sync;
the same DLL is inert where its half does not apply.

> **One thing to verify before adoption:** `Smoothbrain-ServerCharacters 1.4.17.1` (the local
> build held ahead of Thunderstore by design — never update it) intercepts player-profile
> saves on the server. AsyncSave's character half is scoped "anywhere there is a local player",
> and a dedicated server has no local `Player`, so on the README's own terms the two do not
> overlap. That is a reading, not an observation — one smoke check on the server is enough.

**Rejected alternative, and this is why the reference check exists:**
`Smoothbrain-SmoothSave 1.0.5` (118,703 downloads — by far the popular answer) has
**four unresolvable member references against live 1.0.15**:

```
ZDOMan.SaveData::m_nextUid
ZDOMan.SaveData::m_sessionID
ZDOMan.SaveData::m_zdos
ZDOMan::m_objectsByOutsideSector
```

It would throw at `PatchAll()`. **This is the exact defect shape that shipped the build which
broke the operator's main menu.** Do not install it. Popularity measured nothing here; the
typeref table did.

### 4.2 `ArgusMagnus-ServersideQoL_LetItFloat 2.0.11` — fitted to this specific world

2026-09-16 · 1178 dl · floor `ArgusMagnus-ServersideQoL-2.0.11` (core installed at **2.0.13**;
the same skew already runs fine for AutoProcess / AutoStore / ContainerSizes) ·
**1.0.15 refs: clean** · **VR scan: 0 findings** · server-side only, **zero client install**

"Non-floating items are automatically put into cargo crates if they sink too deep."

This world is a **walled harbour city with a working ship gate and 16 decked boardwalk spans**.
Every ingot, every tool, every stack fumbled off a boardwalk or a deck is currently gone.
Nothing installed floats items — `VikingsDoSwim`, `WieldEquipmentWhileSwimming` and
`TrueInstantLootDrop` were all checked and do not. It reuses only the vanilla `CargoCrate`
prefab, adds no piece, and touches no client profile — so it cannot affect VR and cannot
affect replay.

### 4.3 `MidnightMods-ValheimCommunityPatch 0.28.0` — free correctness

2026-09-18 · 3979 dl · **22 dependants** · rating 13 · floors BepInEx 5.4.2333, Jotunn 2.27.1 ·
**1.0.15 refs: clean** · VR scan: 3 low (C6 hover-text strings only)

"Vanilla bug fixes and performance fixes for Valheim. **No gameplay changes.**"

No gameplay change means no balance surprise for a world nobody has played yet, and no prefab
registration means no replay exposure. Same vendor as AsyncSave, shipped yesterday, 22
dependants, open source. It is the cheapest correctness this setup can buy.

### Considered and rejected at §4

| Rejected | Why |
|---|---|
| `ontrigger-ValheimPerformanceOptimizations 1.2.2` | refs clean, but **VR score 26 high, C7×5 real** — `VPOReflectionRenderer` adds its own `Camera` and churns `QualitySettings.lodBias` and `shadowDistance` **per reflection face**. That is frame-pacing churn in a headset, on top of VHVR's own rig. It also ships DLLs into `valheim_Data/Managed/` (`Unity.Burst`, `Unity.Mathematics`), outside profile plugin scope. Flat-only at best; not worth the install shape. |
| `RockBreaker-ValheimBuildOptimization 0.5.5` | is really `BuildPieceProfiler` — an IMGUI diagnostic window on a raw `Input.GetKeyDown` (C4 high). Unusable in VR, and a profiler, not an optimiser. |
| `ServersideQoL_AutoPortalHub 2.0.11` | would **generate** a portal hub. This world already has a hand-built 15-arch portal hall placed through the ledger. Direct conflict with placed content. |
| `ServersideQoL_AutoMapTables 2.0.11` | refs clean, VR-clean, genuinely tempting for a 15-settlement world — but `SurplusTradingCo-SullysAutoPinner 1.4.0` is **already installed** client-side and already auto-pins. AutoMapTables adds only the *shared map table* dimension, which is worth little at zero characters. Held, not recommended. |
| every all-in-one storage suite | collides with four installed storage mods (§1.3). |
| `CW_Jesse-BetterNetworking` and forks | superseded here by `ServersideQoL_MultiplayerTweaks` if networking is ever the complaint; not proposed without a measured symptom. |

---

## 5. Ranked, best first

| # | Action | Gap | Cost |
|---|---|---|---|
| 1 | **Remove `Cytraen-BiggerChests 1.1.0`**, configure the installed `ServersideQoL_ContainerSizes 2.0.11` | container capacity — was never missing, was installed twice and conflicting | **−1 package, 0 new** |
| 2 | Add `MidnightMods-AsyncSave 0.5.0` | main-thread save stall on a 15,683-placement world | +1, no Jotunn dep |
| 3 | Add `Vapok-AutoFeedRedux 2.0.7` | animal autofeed — genuinely absent | +1, deps already installed |
| 4 | Add `ArgusMagnus-ServersideQoL_LetItFloat 2.0.11` | items sunk off the harbour boardwalks | +1, server-only |
| 5 | Add `MidnightMods-ValheimCommunityPatch 0.28.0` | vanilla bugs, no gameplay change | +1 |
| — | Do nothing about the three piece sets | measured: best candidate is 44%, patcher stays regardless | 0 |
| — | Do nothing about the VR build menu | measured: all three candidates VR-dead | 0 |
| — | Do nothing about chest tier upgrades | measured: only candidate is VR-dead, 0 dependants | 0 |
| — | CLLC needs nothing | already repaired by installed `CLLCCompatibility 1.0.1` | 0 |
| ✗ | **Never install `Smoothbrain-SmoothSave 1.0.5`** | 4 unresolvable refs → throws at `PatchAll()` | — |

---

## 6. Appendix — installed-set baseline scan

93 assemblies under `Ulfsland/config_merged/bepinex/plugins` reference-checked against live
1.0.15. **Three flagged, no surprises:**

| Assembly | Finding | Status |
|---|---|---|
| `BepInExPack_Valheim/winhttp.dll` | not managed | expected (native loader) |
| `CreatureLevelAndLootControl/CreatureLevelControl.dll` | `Minimap::noForest` | **repaired in memory by installed `CLLCCompatibility 1.0.1`** |
| `ServersideQoL/ServersideQoL.dll` | `ZDO::get_ServersideQoLZDO`, `ZNetPeer::get_ServersideQoLPeer` | **injected by installed `BepInEx/patchers/ServersideQoL.Patchers.dll`** |

The retyped-field class (`PieceTable::m_availablePieces`) is invisible to this instrument by
construction — see §0 and §3.0.

Working artefacts: `/tmp/nms/refcheck.py`, `/tmp/nms/installed_scan.json`,
`/tmp/nms/cand_scan.json`, `/tmp/nms/vr.json`, `/tmp/nms/vr2.json`, `/tmp/nms/piecetable.txt`.
