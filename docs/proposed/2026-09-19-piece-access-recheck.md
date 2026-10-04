# Re-check: the three "broken" build-piece mods, and the DANGEROUS rows

2026-09-19. Read-only. Nothing installed, changed, committed or deployed.
Premise corrected: **nobody has ever played Ulfsland.** No characters, no progress, no loot.

---

## VERDICT

**There is no build-menu problem on the operator's clients today. Leave all three mods installed.**

The mods *are* compiled against a field shape Valheim 1.0 no longer has. That would abort the build
menu. It does not, because `Wubarrk-Valheim10Compatibility 1.4.1` — a BepInEx **preloader patcher**,
correctly placed in `BepInEx/patchers/` on all three client editions — injects a second field with the
old name and old type. The dead references bind to it.

On the **dedicated server** that same alias is **BLOCKED** (evidence below). That costs nothing,
because the server never calls the method at all.

---

## 1. What 1.0.15 actually did to the field

MEASURED — `Field` table of the live server's own `assembly_valheim.dll`
(`sha256 6d79c900e3035141e7f02f716d26d8c42f28f82a25b2f92b42cd225603b07312`), parsed from the CLI
metadata directly.

| field on `PieceTable` in 1.0.15 | type |
|:--|:--|
| `m_availablePieces` | `HashSet<Piece>` |
| `m_availablePiecesByCategory` | `List<List<Piece>>` |

The field was **not removed and not renamed away**. It was **retyped**, and the old shape moved to a
new name. Both names exist. Earlier reporting said "1.0 renamed `m_availablePieces` to
`m_availablePiecesByCategory`" — that is wrong in a way that matters: a rename would fail by name, a
retype fails by *signature*, and only the signature story explains why a compatibility alias is even
possible.

## 2. What the three carriers reference

MEASURED — `MemberRef` table + `ldfld` operand scan of each installed DLL.

| package | `PieceTable::m_availablePieces` signature referenced | sites |
|:--|:--|:--|
| `OdinPlus-OdinsUndercroft` 1.3.3 | `List<List<Piece>>` | 5 |
| `OdinPlus-OdinsHorsePen` 1.1.0 | `List<List<Piece>>` | 5 |
| `JamesJonesTV-RavenwoodRestorations` 1.0.1 | `List<List<Piece>>` | 5 |

All five sites in every case are inside `PiecePrefabManager` — 3 in `UpdateAvailable_Prefix`,
2 in `UpdateAvailable_Postfix`. Prefix/postfix, i.e. **runtime**, not a transpiler.

Population for context: of **124** DLLs in the admin client's `plugins` tree, **10** embed
PieceManager's `UpdateAvailable_Prefix`; **3** carry the dead signature; **6** reference the live
`HashSet<Piece>` shape (`Jotunn`, `Infinity_Hammer`, `AAABuildMenu`, `RossItemDrawers`, `OdinShip`,
`AdventureBackpacks`). Upstream PieceManager **has** been fixed — `OdinPlus-Basements 2.0.0`
changelog says so in as many words: *"Updated embedded PieceManager for Valheim 1.0's hammer
category data."* The three carriers simply have not rebuilt.

## 3. Would that abort the chain? Yes — measured, not assumed

Two mechanisms, each measured on the real artefacts.

**(a) Mono throws at METHOD ENTRY, not at the instruction.** Built a `List<List<Piece>>` assembly,
compiled a consumer against it, swapped in a `HashSet<Piece>` assembly, invoked:

```
[A] guard NOT taken, field never reached -> MissingFieldException   (thrown at method entry / JIT)
[B] guard taken, field read              -> MissingFieldException
```
No internal guard or early return can dodge it. The prefix throws the first time it is invoked.

**(b) HarmonyX 2.9.0.0** — the actual `BepInEx/core/0Harmony.dll` this fleet runs. Three prefixes,
one throwing, moved through every position:

```
broken prefix sorts FIRST  -> ran: []               ORIGINAL RAN? NO
broken prefix sorts MIDDLE -> ran: [good1]          ORIGINAL RAN? NO
broken prefix sorts LAST   -> ran: [good1, good2]   ORIGINAL RAN? NO
```
Exception reaches the caller in all three. **Patch order cannot rescue the original method** — the
original is called *after* the last prefix, so any throwing prefix at any index prevents it. With a
finalizer present the exception is swallowed but the original still never runs.

Neither carrier uses `[HarmonyWrapSafe]` and none registers a finalizer (TypeRef tables checked).

So the chain-abort assumption was **correct**. The "order might save it" hypothesis is refuted.

## 4. Why it nonetheless does not break — `Valheim10Compatibility`

`Wubarrk-Valheim10Compatibility 1.4.1` is installed on the server and on **all three** client
editions. From its own README:

> **The `PieceTable.m_availablePieces` alias** — 1.0 renamed the per-category piece lists to private
> `m_availablePiecesByCategory` and reused the old name for a flat `HashSet<Piece>`. Fifteen
> build-piece mods on the list read the old field. The alias is a second field with the old name and
> type, assigned in the constructor to the **same list object**, so the two views cannot diverge. IL
> binds fields on name *and* signature, so vanilla and 1.0-built plugins keep the HashSet and pre-1.0
> plugins get the lists.

Placement and configuration, MEASURED:

| | `BepInEx/patchers/Valheim10Compatibility.Patcher.dll` | duplicate under `BepInEx/plugins/` | `PieceTableAvailablePiecesAlias` |
|:--|:--|:--|:--|
| server (in-container) | present | **YES** — `plugins/Valheim10Compatibility/patchers/` | `true` |
| `ulfsland-non-vr` | present | no | `true` |
| `ulfsland-vr-flat` | present | no | `true` |
| `ulfsland-vr-flat-admin` | present | no | `true` |

The patcher refuses the alias if any plugin in `BepInEx/plugins` looks the field up by *string*,
because two same-named fields make `Type.GetField` ambiguous. Server log
(`/var/log/supervisor/valheim-server-stdout---supervisor-advfrr61.log`, inside the container):

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces (List<List<Piece>>, alias of
m_availablePiecesByCategory): Valheim10Compatibility.Patcher.dll (AmbiguityGuard..cctor),
Valheim10Compatibility.Patcher.dll (Patcher.PatchPieceTableAlias) look this field up by name through
Type.GetField / Traverse.Field ... Left unaliased deliberately.
```

**The only thing blocking it is the patcher's own DLL**, visible to the guard because a second copy
of it sits under `BepInEx/plugins/Valheim10Compatibility/patchers/`. That is a packaging artefact on
the server, not a real conflict.

Client side, MEASURED — `#US` (user-string) heap of every DLL, UTF-16LE, never `strings`:

```
BepInEx/plugins   124 DLLs, 0 unreadable -> carry the literal "m_availablePieces": 0
BepInEx/patchers    5 DLLs, 0 unreadable -> carry it: 1  (Valheim10Compatibility.Patcher.dll)
```

The guard scans `plugins`, not `patchers`. **Zero blockers on the clients → the alias is injected →
the three carriers' references resolve → no exception, no abort, build menu works.**

[INFERENCE] The client side is inferred from placement + config + an empty blocker set, not from a
client log, because no client log exists since the patcher was installed. It is the same guard, the
same version, and the blocker set is measured empty.

## 5. Is the server affected? No — measured

MEASURED — scan of every method body in `assembly_valheim.dll` for calls to
`PieceTable::UpdateAvailable` (MethodDef row 3823):

```
callers of PieceTable::UpdateAvailable inside assembly_valheim:
   Player::UpdateAvailablePiecesList
```

Exactly one caller, and it needs a `Player`. A dedicated server constructs none. The server-side
copies of all three mods, and the blocked alias, are **inert**.

## 6. Why "other people are using it just fine"

All four candidates tested. Three refuted, one confirmed:

- **Patch order** — refuted, §3(b). Order changes nothing.
- **Client vs server** — confirmed as a real discriminator, §5. Server installs never trigger it.
- **`HammerFix`** — present only on `ulfsland-vr-flat-admin` (release 1.0.20,
  `Repair = true`); absent from `ulfsland-non-vr` and `ulfsland-vr-flat` (both 1.0.11). Measured that
  selectively unpatching the bad prefix restores `good1 -> good2 -> ORIGINAL -> postfix` clean, so it
  does work — but it is a second safety net, not the reason.
- **Game version** — confirmed as the headline reason. The retype landed in **Valheim 1.0**
  (Deep North). `OdinsHorsePen 1.1.0` was published 2026-02-05 and `OdinsUndercroft 1.3.3` on
  2026-07-24, both pre-1.0. Everyone still on 0.221.x is genuinely unaffected. On 1.0.x you either
  run a compatibility patcher, or you get an empty build menu **with nothing in `LogOutput.log`** —
  because `WriteUnityLog = false` means a Unity-side exception is never written to disk. It is a
  silent failure, which is exactly why it is not a famous one.

### An instrument that could not read
The operator's client log was proposed as deciding evidence. It cannot be: with
`WriteUnityLog = false`, an exception escaping into Unity's `Update` loop never reaches
`LogOutput.log`. The log *is* clean, and that proves nothing either way. (What it does show, and is
a real finding: `Zen.ModLib` fatally aborted **7** ZenDragon plugins — Zen.ModLib, ZenBreeding,
ZenItemStands, ZenPath, ZenRaids, ZenRedecorate, ZenWorldSettings — on
`Valheim installed: 1.0.15, required: 1.0.12`.)

## 7. Are any of their pieces standing? No

MEASURED. Population: **the entire Ulfsland save** — all 34 saved `.chunk` files plus
`_main.397.db2`, 15,741,069 bytes, copied out of the running container. Instrument: the exact 4-byte
little-endian Valheim `GetStableHashCode` of each prefab name, counted across the whole save. Chunk
files verified uncompressed first (raw position floats readable). Position-independent, so it has
none of the cylinder-vs-cube height blindness that produced phantom zeros before.

```
CALIBRATION   wood_floor 7109 · stone_floor_2x2 4590 · wood_wall_log 1025
              portal_wood 30 · piece_groundtorch 394
CONTROL       zz_not_a_real_prefab_xyz 0 · OU_NotAPiece_Zzz 0

OdinsUndercroft        38 prefab names   ->  0
OdinsHorsePen           2 prefab names   ->  0
RavenwoodRestorations 121 prefab names   ->  0
```

Prefab names taken from each DLL's `#US` heap, never `strings`.

Independent corroboration: the ledger copy at
`/srv/valheim-portal/tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl` (1,768 records) has zero
placements of any of them; the single line matching `Ravenwood_` is the seq-0 world manifest's mod
list, not a placement. Calibration on the same file: `wood_floor` 99, `piece_groundtorch` 49.

**The earlier claim "you do have pieces from RavenwoodRestorations, so it needs a swap rather than a
removal" is refuted.** Removal would cost nothing. It is also unnecessary.

## 8. Still broken today? Version, bytes and IL — three ways

| package | installed | Thunderstore latest today | published | installed DLL sha256 vs fresh download | dead `ldfld` refs |
|:--|:--|:--|:--|:--|:--|
| `OdinPlus-OdinsUndercroft` | 1.3.3 | 1.3.3 | 2026-07-24 | `de29673c…` **byte-identical** | 5 |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | 1.1.0 | 2026-02-05 | `949083e7…` **byte-identical** | 5 |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | 1.0.1 | 2026-09-08 | `090435b6…` **byte-identical** | 5 |

Confirmed, not inherited: all three are on the newest published version, the artefacts match
Thunderstore today byte for byte, and the dead references are still there. There is no update to take.

## 9. Which Ravenwood package owns which finding

Three separate packages, and the findings had been mixed up.

| finding | actually belongs to |
|:--|:--|
| 5 dead `ldfld` refs to `m_availablePieces` | **`JamesJonesTV-RavenwoodRestorations` 1.0.1** |
| 1-byte `ret` stubs `AddRWPieces` / `AddRWEffects`, in a type named `RavenwoodPiecesPlugin` | **`JamesJonesTV-RavenwoodVikingHouses` 7.7.8** |
| the `fb_` / `mm_` string split — 29 `fb_`, 78 `mm_` | **`JamesJonesTV-RavenwoodVikingHouses` 7.7.8** |
| `newobj BuildPiece(..)` registrations | 112 Restorations · 0 VikingHouses · 0 Currency |

Also: the GUID inside RavenwoodRestorations' DLL is `Azumatt.RavenwoodRestorations` and its config
file is `Azumatt.RavenwoodRestorations.cfg`, but `Azumatt/RavenwoodRestorations` **does not exist on
Thunderstore** (404). It is PieceManager boilerplate carried over, not a second package. That is
probably where the author confusion came from. `JamesJonesTV-RavenwoodPieces 2.0.5` is a **fourth**
package and is **not installed** on this fleet.

Corrected population figure: **425** `newobj BuildPiece(..)` registrations across **11** DLLs in the
admin client tree, of which **115** belong to the three carriers (112 + 2 + 1). Previously reported
as 435 / 118.

## 10. The only residual cost

From the patcher's own README, and it is not a crash:

> 1.0 inserted `PieceCategory.DeepNorth = 5`, pushing Feasts/Food/Meads to 6/7/8 and `Max` to 9. A
> pre-1.0 mod bakes those numbers at compile time, so a piece it files under old `Feasts` (5) lands
> in the DeepNorth tab, and a custom tab it created at old `Max` (8) shares the Meads list. **Wrong
> tab, not a crash.**

MEASURED — all three file into custom tabs, not into Feasts/Food/Meads:
`Build Table Category = Custom` with `Custom Build Category` = `Undercroft`, `Raven Restorations`,
and HorsePen's own. So the Feasts-drift half does not apply; the custom-tab-at-old-`Max` half may.
[INFERENCE] Observable in one minute by equipping the hammer and looking at the Meads tab. Cosmetic.

---

## Replacements — withdrawn, with coverage measured

The valid definition: a replacement must cover the **piece set**, measured piece by piece.

| original | pieces | candidate | covers | coverage | verdict |
|:--|--:|:--|--:|:--|:--|
| `OdinsUndercroft` 1.3.3 | 38 prefab names (34 build pieces + 3 interior layers, own `Undercroft` tab) | `OdinPlus-Basements` 2.0.0 | 1 (`newobj BuildPiece` count = 1) | **1/34** | **NOT A REPLACEMENT** |
| `OdinsHorsePen` 1.1.0 | 2 (`Odins_Pen_Trap_Custom`, `Odins_Nest_Trap_Custom`) | — | — | 0/2 | **NO CANDIDATE EXISTS** |
| `RavenwoodRestorations` 1.0.1 | 121 prefab names / 112 registrations | `RavenwoodVikingHouses` 7.7.8 | houses only | ~0/112 on function | **NOT A REPLACEMENT** |

**Withdrawing "Basements replaces OdinsUndercroft".** Basements places a basement. Uncovered if you
swapped: `OU_StoneArchway`, `OU_StoneWall`, `OU_StoneHalfWall`, `OU_Stone_Floor_1x1/2x1/2x2`,
`OU_Stone_Roof_45`, `OU_Stone_Outter_Corner`, `OU_Stone_Roof_Corner`, `OU_StoneRoof_Tile`,
`OU_StoneFloor`, `OU_StoneStair`, `OU_StoneBeam`, `OU_StoneBeam_Small`, `OU_CornerCap`,
`OU_CornerCap_Small`, `OU_Large_Stone_Pillar`, `OU_Medium_Stone_Pillar`, `OU_DrainPipe`,
`OU_MetalGrate`, `OU_Iron_Cage`, `OU_Metal_Cage`, `OU_Urn`, `OU_Sarcophagus`, `OU_Sarcophagus_Lid`,
`OU_Skeleton_Full/Ribs/Hanging/Pile`, `OU_Swords_Crossed`, `OU_Wall_Shield`, `OdinsMinicroft`,
`OH_Undercroft_BuildSkull` — the entire crypt set. Basements is *a mod that also does basements*.

Uncovered for RavenwoodRestorations: 5 chest tiers, 5 stack containers, 6 mail chests, 9 boards,
8 bookshelf-door variants, 6 book stacks, 20 statues, 20 barns, 10 farm signs, 5 crafting stations,
4 rune floors, the ration collector.

**None of this is needed. Nothing is broken; keep all three.**

---

## The five DANGEROUS rows, re-decided against the unplayed world

Every row restated as **the precondition its risk depends on**, then that precondition measured.

| # | mod | the risk as written | precondition it needs | measured | verdict |
|--:|:--|:--|:--|:--|:--|
| 1 | `EpicLoot` 0.14.7 → **0.14.10** | "permanently resets magic-effect values; bounty ledger clears" | magic items / bounties exist | `piece_enchanter` 0, `piece_augmenter` 0, `EpicLoot_BountyTarget` 0, `TreasureMapChest` 0, `Player` 0 | **SAFE — TAKE IT** |
| 2 | `Judes_Equipment` 2.3.0 → **3.0.0** | "drops custom configuration support" | custom config exists | `GoldenJude_JudesEquipment.cfg` 3 settings, **0 non-default**; BlacksmithTools 3 settings, **0 non-default** | **SAFE** |
| 3 | `ImpactfulSkills` 0.16.1 → **0.17.1** | placement geometry / skill data | characters with skills exist | `Player` 0, `Player_tombstone` 0 | **SAFE** |
| 4 | `ProtectiveWards` 2.0.10 → **2.0.11** | "rewrites per-ward stored settings" | wards placed | `guard_stone` **3** | **PRECONDITION MET** — smallest real risk on the list: re-check 3 wards after |
| 5 | `BuildOnShip` 3.0.112 → **3.2.1** | "ZDO zone sync for built pieces on ships" | a ship with pieces on it exists | `Karve` 0, `VikingShip` 0, `VikingShip_Ashlands` 0, `Raft` 0 (`Cart` 1) | **SAFE — risk cannot fire** |

### EpicLoot: the error was polarity, not premise
The quoted lines are **fix** lines, reported as **consequences**. Verbatim from the 0.14.10 changelog
(fetched from Thunderstore, published 2026-09-18):

> * **Fix** for magic effect values being permanently reset to 1 when an item was reloaded
> * **Fixes** for the bounty ledger which could reset when the server was restarted
> * **Fixes** for not persisting bounty or treasuremap data to the character

0.14.10 is the version that **stops** the reset. Skipping it was advice to keep the bug. And on this
world the bug had nothing to reset either way. Wrong twice.

---

## ServersideQoL modules

Namespace is **`ArgusMagnus`**. All modules pin core `2.0.11`; installed core is **2.0.13**, so every
floor is met.

| package | latest | installed | what it actually gives the operator |
|:--|:--|:--|:--|
| `ArgusMagnus-ServersideQoL` | 2.0.13 | **2.0.13** | The engine every module plugs into; on its own it changes nothing visible. |
| `ArgusMagnus-ServersideQoL_AutoStore` | 2.0.11 | **2.0.11** | Dropped items and player inventory file themselves into nearby chests. |
| `ArgusMagnus-ServersideQoL_AutoProcess` | 2.0.11 | **missing** | Smelters, kilns and fermenters feed themselves from adjacent containers — this is the "feed from containers" feature. |
| `ArgusMagnus-ServersideQoL_ContainerSizes` | 2.0.11 | **missing** | Set slot counts per container type, so chests hold more. |
| `ArgusMagnus-ServersideQoL_PrefabConfigurator` | 2.0.11 | **missing** | Per-prefab property overrides in YAML; needs core ≥ 2.0.10. Not previously mentioned. |

Note, per the rule that a config file is not evidence: `Azumatt.AzuAutoStore.cfg` is present in
`config_merged/bepinex/` with **no AzuAutoStore DLL installed** — an orphan config.

---

## Measurement caveats

- The fleet-wide installed-vs-latest sweep (105 packages) was **rate-limited by Thunderstore** and
  returned HTTP errors for most rows. No conclusions are drawn from it. The packages named in this
  document were each queried individually and succeeded.
- Client-side alias injection is [INFERENCE] — see §4.
- The world census reads the **on-disk save**. Chunk mtimes are 2026-09-17/18 with `_main.397.*`
  at 01:54 today; ZDOs created since the last autosave and still only in server memory would not
  appear. Consistent with nobody having played.

## Instruments used

- CLI metadata parser (`#~` tables: `TypeDef`/`Field`/`MethodDef`/`MemberRef`, `#Blob` signature
  decode, `#Strings`, `#US` as UTF-16LE) — not `strings`, not `monodis`.
- IL operand scan for `ldfld`/`newobj`/`call` against specific metadata tokens.
- Mono 6.8.0.105 + `mcs`: two live experiments, §3(a) and §3(b).
- Valheim `GetStableHashCode` over the raw save, calibrated and controlled, §7.
- Thunderstore experimental package API; CDN byte-for-byte artefact download.
- In-container supervisor stdout log, §4.
