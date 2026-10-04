# Mod update and piece-access survey — 2026-09-18

Read-only survey. Nothing installed, deployed or published.

## Piece access

Owner: PieceAccess. Scope: P1 (village pieces unreachable) and P2 (the four dead-field
carriers). Read-only: nothing installed, deployed, published or restarted.

### Instruments used

| Instrument | What it is | Why |
| --- | --- | --- |
| `dnfile` 0.18.0 + own IL reader (`/tmp/pa/ilprobe.py`) | parses the PE/CLI metadata, resolves each `MethodDef.Rva` to a file offset, decodes the tiny/fat method header and reports the real `CodeSize` | `monodis` is unusable on ILRepacked assemblies here |
| own IL disassembler (`/tmp/pa/ildis.py`) | decodes opcodes and resolves `MethodDef`/`MemberRef`/`Field`/`TypeRef` tokens and `#US` user strings | needed to see *which* call is made, not just body size |
| own signature decoder (`/tmp/pa/deadref.py`) | decodes each `MemberRef` field-signature blob (ECMA-335 compressed ints) to a printable type | the two 1.0 fields share the name `m_availablePieces`; only the signature separates them |
| Thunderstore `api/experimental` + `/c/valheim/api/v1/package/` (167 MB index, pulled 2026-09-17 21:33) | version history, publish dates, dependency strings, changelog markdown | primary source |
| installed `CHANGELOG.md` shipped inside the Jotunn package | vendor's own release notes on disk | quotable, and proves what *this* build says about itself |

A correction to an earlier fat-header bug in my own tooling, recorded because it produced
nonsense once: the fat method header's `CodeSize` is at offset **4**, not offset 8
(offset 8 is `LocalVarSigTok`). Reading offset 8 yields values like `285212673`
(`0x11000001`, a `StandAloneSig` token) rather than a byte count. All sizes below are
post-fix.

**Provenance of every number below — no shared-kernel exposure.** The shared Python eval
kernel rebinds globals between task subagents (found by `HarbourCity`: its `W` and `fl`
were silently reassigned mid-survey by another agent's cell). No figure in this section
came from that kernel. Every measurement ran as a standalone script in its own `python3`
process. After the hazard was broadcast I additionally re-derived **all six** headline
result groups — the `AddRWPieces` sizes, the md5 identity, the 29/102 registration split,
the four-carrier dead-ref counts, the 1.0 `PieceTable` field layout, and the build-menu
inventory — in a **single invocation** of one self-contained script
(`/tmp/pa/reverify_all.py`). Every value reproduced identically. That re-run also widened
the build-menu sweep to walk all five server trees rather than Ulfsland alone, and
confirmed `any under a server plugins/ tree? False`.

**Positive controls against checker failure — and a third tooling bug they caught.**
Internal consistency is not proof; on ModDelta2's prompting I checked the numbers against
values from *outside* my own tooling (`/tmp/pa/control_checks.py`). The weakest link was
the 29/102 split, because it is derived by string-matching *resolved token text* — so any
token that failed to resolve would silently **undercount** and still look plausible.

| Control | Known-good source, external to my tooling | Result |
| --- | --- | --- |
| every `call`/`callvirt`/`newobj` token must resolve | self-evident: an unresolved token cannot be string-matched | **PASS** (0 of 2,542, after the fix below) |
| total registrations >= 130 | parent package's own `manifest.json`: *"Adds 130+ new building pieces"* | **PASS** (131) |
| reachable `FB_Pieces` count == author's itemised list | installed `README.md`: 10 Medieval Walls + 2 Gates + 2 Bridges + 1 Door + 1 Stairs + 13 Viking Houses = **29** | **PASS** (29 == 29) |
| both DLLs share one registration codebase | same source tree; only `AddRWPieces` differs | **PASS** (131 == 131, 29 == 29) |

The first control **failed on its first run**: 1 unresolved token out of 2,542 call sites
in each DLL, both in `Elevator::Update`. Root cause was a genuine gap in my opcode table —
`switch` (`0x45`) is variable-length (4-byte count then N 4-byte targets) and I did not
handle it, and the `conv.*`/arith/`ldelem`/`stelem` ranges were missing entirely. An
unknown operand length **desynchronises every following instruction**, so the decoder was
silently reading operand bytes as opcodes. Fixed by completing the single-byte opcode
table, decoding `switch` properly, and adding a `strict` mode that **raises `OpcodeGap`
rather than mis-striding**. Re-run strict: **0 opcode gaps, 0 unresolved tokens, and the
split is unchanged at 29 / 102 / 131.**

That the number did not move is the point worth recording: the desync was confined to
`Elevator::Update`, a runtime-behaviour class that registers nothing, and decoding
restarts at each method body. So the split was never affected — but that is now
*measured*, where before it was merely true by luck. The third control is the strongest
single piece of evidence in this section: the author's own itemised README total is
**exactly 29**, independently matching the count of reachable registrations I derived from
IL. Two unrelated instruments, one number.

**Restart safety, and the one volatile input.** Audited with
`/tmp/pa/provenance_audit.py` ahead of the Ulfsland stop/deploy window: RCON calls made
= 0, world-data reads = 0, ledger tokens held = 0, world writes = 0, live
`ZNetScene.m_prefabs` censuses relied on = 0. Every figure here derives from a static
file on disk or a downloaded package, so a container stop/restart cannot invalidate any
of it and nothing needs re-taking afterwards.

Exactly **one** input was restart-volatile: the live `LogOutput.log`, which backs the
`Valheim10Compatibility BLOCKED` quote in the P2 section. BepInEx **truncates that file
at process start**, so the restart would have made the quote unreproducible. It was
snapshotted *before* the stop to
`/tmp/pa/evidence/Ulfsland-LogOutput.log.snapshot-20260917T220043`
(`md5 9a5e08cb041b0703…`, verified matching the live file at snapshot time, quoted line
present). Anyone re-checking that quote after tonight's restart must read the snapshot,
not the live log.

---

### P1 — why the village pieces are unreachable

#### The headline measurement: `AddRWPieces` IL body size

| Build | Provenance | `RavenwoodPiecesPlugin::AddRWPieces` | IL bytes | Body |
| --- | --- | --- | --- | --- |
| **7.7.8 — installed** | `Ulfsland/config_merged/bepinex/plugins/RavenwoodVikingHouses/RavenwoodVikingHouses.dll` | tiny header | **1** | `2a` = `ret` |
| **7.7.8 — newest published** | fresh download, `gcdn.thunderstore.io/.../JamesJonesTV-RavenwoodVikingHouses-7.7.8.zip`, HTTP 200, 64,291,753 bytes | tiny header | **1** | `2a` = `ret` |
| 7.7.7 — prior release (2025-12-08) | fresh download, 64,241,928 bytes | tiny header | **1** | `2a` = `ret` |

MEASURED. Three separate artefacts, **one identical DLL**: installed, the freshly
downloaded newest 7.7.8, and the freshly downloaded prior 7.7.7 all carry
`md5 7a85c9fcb086633d8dc8fcd49a33f46b`, size 64,162,816 bytes. The two package zips are
genuinely distinct downloads (`md5 7074665c…` for 7.7.7 vs `7aa2fdc1…` for 7.7.8,
different byte counts), so this is not an extraction slip — I re-extracted 7.7.7 to a
clean path and re-hashed to confirm.

**7.7.8 is a documentation-only republish. It contains no code change whatsoever.** Full
member diff of the two packages:

| Member | 7.7.7 | 7.7.8 | Identical |
| --- | --- | --- | --- |
| `plugins/RavenwoodVikingHouses/RavenwoodVikingHouses.dll` | 64,162,816 | 64,162,816 | **yes — byte for byte** |
| `README.md` | 1,698 | 9,583 | no |
| `icon.png` | 102,532 | 148,466 | no |
| `manifest.json` | 341 | 341 | no — version string only; dependency floors unchanged at BepInEx 5.4.2202 / Jotunn 2.24.3 |

7.7.8 was published 2026-09-17T03:07:00Z; the package has only ever had **two** versions,
and its 7.7.8 changelog endpoint returns an empty body — consistent with a republish that
changed only the store page's README and icon.

**Verdict on "does an update fix piece access": no update exists, and the one that looks
like an update is not one.** Installed *is* newest, byte for byte. `AddRWPieces` was
already a 1-byte `ret` in 7.7.7, so this is not a regression awaiting restoration. The
practical consequence for change control: a version bump on this package is **not**
evidence that code moved. A yesterday-dated release here carries the same binary as
2025-12-08. Compare the DLL hash, never the version string.

#### But the stub is not the defect — and it is not why the village pieces are missing

This is a correction to the earlier diagnosis, and it changes the remedy. Three
measurements force it.

**1. The plugin has three registration entry points, and only one of them is stubbed.**
`RavenwoodPiecesPlugin::Awake` (139 IL bytes) subscribes all three to
`Jotunn.Managers.PrefabManager::add_OnVanillaPrefabsAvailable`:

| Entry point | IL bytes | Body |
| --- | --- | --- |
| `AddRWEffects` | 1 | `ret` — dead |
| `AddRWPieces` | 1 | `ret` — dead |
| `AddFBPieces` | **65** | `call FB_Pieces::AddPieces`, wrapped in try/catch + `remove_OnVanillaPrefabsAvailable` — **live** |

`FB_Pieces::AddPieces` (151 IL bytes) issues 30 `call`s —
`AddFortressWall*`, `AddFortressWallPortcullis`, `AddPortcullisGate`,
`AddFortressGates`, `AddBridge01/02`, `AddBridgeStairs`, `AddShelter01`,
`AddHouse01..04`, `AddVikingHouse01..08`, `AddWoodDoor01`. That is exactly the
advertised content. `RW_Pieces::AddPieces` (516 IL bytes) is the one nothing calls.

**2. The three asset bundles are named, and the village is in the live one.** From
`RavenwoodPiecesPlugin::.cctor`:

| Field | Bundle | Registered by | Live? |
| --- | --- | --- | --- |
| `piecesBundle` | `ravenwoodpieces` | `RW_Pieces` | no — `AddRWPieces` is `ret` |
| `piecesBundleNew` | `ravenwoodpiecesnew` | `RW_Pieces` | no |
| `piecesBundleFB` | **`villagestuff`** | `FB_Pieces` | **yes** |

**3. Counting the registration call sites by declaring class** (`PieceManager::AddPiece`
callvirt sites, `/tmp/pa/piecescan.py`):

| Class | `AddPiece` sites | Reachable |
| --- | --- | --- |
| `FB_Pieces` | 29 | **yes** |
| `RW_Pieces` | 102 | no |
| total | 131 | 29 |

So the stub kills the 102 `RW_`/`mm_` pieces — the massive/castle walls, trees, hedges,
roads, creature signs, floors, monster-protector lights. It does **not** touch the 29
`fb_` village pieces. Those register normally. From `FB_Pieces::AddVikingHouse01`:

```
ldstr 'fb_viking_house_01'          // loaded from the villagestuff bundle
ldstr 'piece_stonecutter'  -> PieceConfig::set_CraftingStation
ldstr '_HammerPieceTable'  -> PieceConfig::set_PieceTable
ldstr 'Ravenwood-Pieces'   -> PieceConfig::set_Category
...
call Jotunn.Managers.PieceManager::get_Instance
callvirt Jotunn.Managers.PieceManager::AddPiece
```

All 131 sites use `_HammerPieceTable` and the single category `Ravenwood-Pieces`.

**The earlier prefab-dump evidence never tested for this.** The live `ZNetScene.m_prefabs`
dump was searched for `mm_`, `RW_` and `jj_` — three prefixes that all belong to the
*stubbed* families. The village prefabs are prefixed **`fb_`**, which was not among the
search terms. The zero result is therefore consistent with the stub and says nothing
about the village pieces either way.

**Now confirmed live, by a runtime instrument — this label is no longer inferred.**
`HarbourCity` located the exact probe in our own Ulfsland ledger: **seq 2118–2123, actor
`WaterfrontRun`, 2026-09-17T11:07Z**, i.e. against the running 2.30.0 world this section
describes, before tonight's restart. Two independent console instruments, opposite
results, exactly as the IL predicts:

| Prefab | `spawn_object` | `objects_count` | In `ZNetScene`? |
| --- | --- | --- | --- |
| `mm_large_wall` | `Object reference not set to an instance of an object` (NRE from a null lookup), placed **nothing** | `Error: Entity id mm_large_wall not recognized` | **NO** — stub confirmed at runtime |
| `fb_bridge_01` | placed | `1` | **YES** |
| `fb_bridge_02` | placed | `1` | **YES** |

And ledger **seq 2102–2117** actually built `fb_fortress_wall`,
`fb_fortress_wall_tower` and `fb_fortress_wall_gateway` into harbour-vestvik with
measured geometry (wall 20.009 m x 11.866 m x 6.8 m; tower 15.402 m tall with its
walkable platform at y=10.229, not the mesh top; `pivot_is_foot=true`). So the Ravenwood
fortress kit is **already standing on this world**.

Two consequences worth carrying. First, the IL reading in this section is independently
corroborated by runtime: the `AddRWPieces` stub really does keep `mm_` out of
`ZNetScene`, and the live `AddFBPieces` path really does put `fb_` in. The pieces were
never missing from the game — only from the build menu, which is precisely the Jotunn
2.30.0 category defect below. Second, **these pieces have no `WearNTear`**, which I had
flagged as unproven from IL alone (I measured a `GetComponent<Piece>()` and a
`stfld Piece::m_placeEffect`, but no `WearNTear` access). Confirmed absent: they need no
structural support, and they will not decay or take damage.

#### The actual cause: Jotunn 2.30.0 does not map custom categories into the 1.0 build menu

Installed Jotunn is **2.30.0** (`plugins/Jotunn/manifest.json`). Quoting the
`CHANGELOG.md` shipped inside that very package, on disk:

> ## Version 2.30.0
> * Updated the majority of systems for Valheim 1.0.7 (thx warp)
> * **Piece categories are not updated yet, Valheim overhauled the system completely.
>   Custom pieces show up in the build menu, but don't have a category associated**

That is the mechanism, stated by the vendor: a Jotunn-registered piece carrying
`Category = "Ravenwood-Pieces"` is added to `_HammerPieceTable` but gets **no category
association**, so there is no tab under which to reach it. Pieces present, pieces
unreachable — precisely the operator's complaint.

Jotunn **2.30.1** (published 2026-09-17T21:31:33Z, i.e. after the fleet's snapshot) fixes
it. Quoting the Thunderstore changelog endpoint for `ValheimModding/Jotunn/2.30.1`:

> * **Added custom piece categories to the new build menu, pieces of a custom category
>   are shown under an own tag**
> * Added `PieceConfig.Usage` to set the build menu tags of a piece, Jötunn guesses them
>   from the piece if left empty
> * Deprecated the piece table category API, piece tables don't own categories since
>   Valheim 1.0
> * **Fixed selecting a piece placing the piece of another category, the category list of
>   a piece table is kept up to date again**

#### Second, independent gap: the bundled-`PieceManager` mods are not covered by Jotunn

Jotunn 2.30.1 only fixes pieces registered through *Jotunn's* API. Two large installed
piece mods do not use it at all — they use the bundled `PieceManager`/`BuildPiece` helper
library (MEASURED: zero `Jotunn...PieceManager::AddPiece` sites, N `BuildPiece::.ctor`
sites):

| Mod | `BuildPiece` ctor sites | `Jotunn PieceManager::AddPiece` sites |
| --- | --- | --- |
| `OdinsKingdom 1.5.8` | 278 | 0 |
| `RavenwoodRestorations 1.0.1` | 113 | 0 |

This corrects the brief: `RavenwoodRestorations` does **not** register through Jotunn. It
registers through the bundled `PieceManager` — and that same bundled copy is one of the
four dead-field carriers in P2. Its 113 pieces depend on the `UpdateAvailable` patch that
P2 shows is aborting.

`Azumatt-AAABuildMenu 1.0.4` (2026-09-14, zero declared dependencies) covers both
families. Quoting its changelog table row for 1.0.4:

> * Valheim 1.0 support. New Can Build, Most Used and Pinned tabs, resizable/movable
>   window, icon size, sort button, material counts and recipe pinning in the 1.0 build menu.
> * **Custom Jotunn/PieceManager categories get their own tag in the 1.0 menu.**
> * **A modded piece with a broken recipe no longer breaks the build menu.**
> * Reset Size & Position also resets the 1.0 menu. Stays out of PieceManager's way.

#### The grid arithmetic, and an unmet prerequisite

The mod author's own README, shipped in the installed package, states the requirement:

> Pick one of the below mods for your build menu needs, but only one. ... **Both mods add
> a scroll bar to make sure you can always use all build pieces.**
> — `Legacy_Build_Menu`, `AAABuildMenu`

MEASURED: **no build-menu mod is present on the live world or on any server tree.** The
only two copies anywhere on the fleet are a pre-1.0 `Azumatt-SearchableBuildMenu` on two
client profiles belonging to deliberately-stopped worlds. Detail, with a correction to my
own first sweep:

| Where | Build-menu mod present? |
| --- | --- |
| all 5 server plugin trees (Ulfsland 99 dirs + 4 others) | none |
| the 3 Ulfsland client profiles | none |
| `Hrafnheim--hrafnheim-flat--flat` (world stopped) | `Azumatt-SearchableBuildMenu`, `SearchableBuildMenu.dll` |
| `Vangard-Redesign--vangard-redesign-flat--flat` (world stopped) | `Azumatt-SearchableBuildMenu`, `SearchableBuildMenu.dll` |
| remaining 8 client profiles | none |

Correction recorded because it was a real instrument failure: my first sweep of
`/mnt/vps-sync/profiles/` used `find -maxdepth 4`, but the plugin path is
`<profile>/active/BepInEx/plugins/<pkg>` — depth 5. The glob could not reach it and
returned a false zero. Re-run at `-maxdepth 6`, the two hits above appear (plus their
`previous/` snapshots). Caught by ModDelta2, who swept all five server trees and all 13
client profiles independently.

That copy does not change the conclusion. Its assembly version reads **1.0.4.0** (read
from the `Assembly` metadata row of the DLL on disk) and `SearchableBuildMenu`'s newest
published version is 1.0.4 from 2024-12-09 — a pre-1.0 build. It drives the legacy
piece-selection window, so it is almost certainly inert where it sits, and in any case it
is on two stopped worlds and on no Ulfsland client. **The prerequisite the author names is
unmet everywhere that currently matters.**

Against the 15x6 = 90-cell grid, one category holding all of a mod's pieces:

| Configuration | Pieces in `Ravenwood-Pieces` | vs 90 cells |
| --- | --- | --- |
| current — `RavenwoodVikingHouses 7.7.8`, only `FB_Pieces` live | 29 | fits |
| if swapped to `RavenwoodPieces 2.0.5` (all 131 live) | 131 | **overflows by 41** |

So the 29 village pieces do *not* need a build-menu mod once they have a category tag;
the 131-piece parent mod does.

---

### P1, Step 2 — alternatives for the `RW_`/`mm_` fortification kit

The 102 `RW_`/`mm_` pieces are permanently absent from the stand-alone edition, at the
newest published version, by the author's design. Candidates for an equivalent kit
(houses, curtain walls with corners/cross/T/end, gateways, towers, portcullis, bridges),
all dates and dependency floors from the Thunderstore index, all registration mechanisms
MEASURED by IL:

| Rank | Package | Newest / date | Dependency floors | Registration mechanism | Pieces / category | Grid collision |
| --- | --- | --- | --- | --- | --- | --- |
| **1** | `JamesJonesTV-RavenwoodPieces` | **2.0.5**, 2026-09-14 | BepInEx 5.4.2202, **Jotunn 2.24.3** | Jotunn `PieceManager::AddPiece`; `AddRWPieces` is **65 IL bytes** and calls `RW_Pieces::AddPieces` | 131 in one category `Ravenwood-Pieces` | **yes** — 131 > 90, needs a build-menu mod |
| 2 | `JamesJonesTV-RavenwoodDwarvenKingdoms` | 1.1.0, 2026-09-13 | BepInEx 5.4.2202, Jotunn 2.20.3 | Jotunn `PieceManager::AddPiece`, 50 sites | 50 in own category `Ravenwood-Dwarven` | no — 50 < 90 |
| 3 | `OdinPlus-OdinsKingdom` *(already installed at 1.5.8; newest 1.5.9, 2026-09-17)* | 1.5.9 | none declared | bundled `PieceManager`/`BuildPiece`, 278 sites, own custom build tool (separate piece table) | 278, own tool | no — own piece table, not the hammer grid |
| 4 | `JamesJonesTV-Dragon_Keep` | 1.0.1, 2026-09-15 | BepInEx 5.4.2333, Jotunn 2.28.0, `Ravenwood_Currency 1.0.0` | Jotunn (same author pattern) | fortress/keep set | no |

**Recommendation: `JamesJonesTV-RavenwoodPieces 2.0.5`, as a replacement for
`RavenwoodVikingHouses 7.7.8` — not alongside it.** Reasons, each measured:

1. It is the **same codebase with the dead path enabled**. Both DLLs contain the
   identical 131 `AddPiece` sites, the identical `_HammerPieceTable` /
   `Ravenwood-Pieces` / station split (`piece_stonecutter` 72, `piece_workbench` 33,
   `forge` 3), and the identical `FB_Pieces::AddPieces` at 151 IL bytes. The only
   difference is `AddRWPieces`: **1 byte (`ret`) in 7.7.8 vs 65 bytes (live call) in
   2.0.5.**
2. It is **asset-compatible, so no existing build is lost.** Its `.cctor` loads the same
   three bundles by the same names, including **`villagestuff`** — the bundle the 29
   already-placed `fb_*` village prefabs come from. Prefab names are unchanged, so placed
   structures keep resolving.
3. The author's README forbids running both: *"Do not use this mod alongside with the
   Pieces mod!"* — so this is a swap, and the swap is strictly additive in content
   (29 -> 131 pieces).
4. Caveat, measured: 131 pieces in one category **overflows the 90-cell grid by 41**. The
   swap therefore **requires** a build-menu mod. That is the author's own stated
   prerequisite and it is currently unmet on this fleet.

If the goal is only "make the operator's village pieces reachable" and not "add the
massive walls", then **no mod change is needed at all** — bump Jotunn 2.30.0 -> 2.30.1 and
the existing 29 pieces get a category tag. That is the cheaper of the two paths and it is
the one I would do first.

---

### P2 — the four dead-field carriers

Valheim 1.0's `PieceTable` really does declare two fields whose names collide. MEASURED
by decoding the field signatures in the live server's
`valheim_server_Data/Managed/assembly_valheim.dll`:

| Field | Signature |
| --- | --- |
| `m_enabledPieces` | `HashSet<Piece>` |
| **`m_availablePieces`** | **`HashSet<Piece>`** — new in 1.0 |
| **`m_availablePiecesByCategory`** | **`List<List<Piece>>`** — the pre-1.0 `m_availablePieces`, renamed |

So a pre-1.0 `ldfld List<List<Piece>> PieceTable::m_availablePieces` resolves to a field
that no longer has that type — `MissingFieldException` — and this also explains why the
existing local `Valheim10Compatibility 1.4.1` patcher **deliberately refused** to alias
it. From the live Ulfsland `LogOutput.log`:

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces
(List<List<Piece>>, alias of m_availablePiecesByCategory): Valheim10Compatibility.Patcher.dll
(AmbiguityGuard..cctor), Valheim10Compatibility.Patcher.dll (Patcher.PatchPieceTableAlias)
look this field up by name through Type.GetField / Traverse.Field, which throws
AmbiguousMatchException the moment two fields share the name. Left unaliased deliberately.
```

#### Measured dead-reference counts

I swept **all 119 DLLs** on the Ulfsland server tree, not just the four named ones
(`winhttp.dll` is native and has no CLI metadata — skipped). Counting IL operand
references to a `MemberRef` named `m_availablePieces` on `PieceTable`, split by decoded
signature:

| Package | Installed | Newest | Dead refs — installed | Dead refs — newest | Status |
| --- | --- | --- | --- | --- | --- |
| `OdinPlus-Basements` | 1.4.1 | **2.0.0** (2026-09-17T16:20:03) | **5** | **0** | **FIXED upstream** |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | 1.3.3 (2026-07-24) | **5** | 5 — same build | no update exists |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | 1.1.0 (2026-02-05) | **5** | 5 — same build | no update exists |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | 1.0.1 (2026-09-08) | **5** | 5 — same build | no update exists |

"Newest" re-checked live against `api/experimental/package/<owner>/<name>/` after the
index pull; only Basements moved. All five references in every carrier sit in the same
two methods: `PieceManager.PiecePrefabManager::UpdateAvailable_Prefix` (3) and
`::UpdateAvailable_Postfix` (2).

Correct copies, for contrast — these reference the **renamed** field and so were never
broken: `OdinsKingdom 1.5.8` (2 refs to `m_availablePiecesByCategory`),
`OdinCampsite 1.6.4` (2). Four further DLLs reference the *new* 1.0
`HashSet<Piece> m_availablePieces` legitimately and are not implicated:
`AdventureBackpacks` (5), `Infinity_Hammer` (7), `OdinShip` (1), `RossItemDrawers` (1).

#### What Basements 2.0.0 actually did — the shape of the correct fix

| Build | `m_availablePieces` refs | `m_availablePiecesByCategory` refs |
| --- | --- | --- |
| Basements 1.4.1 installed | 5 (`List<List<Piece>>`) | 0 |
| Basements 2.0.0 newest | 0 | **5 (`List<List<Piece>>`)** |

It moved all five references to the renamed field, same signature, and kept the same
three patch methods (`UpdateAvailable_Prefix`, `_Postfix`, `_Transpiler`). Its changelog
row says exactly that:

> | 2.0.0 | - Valheim 1.0 Deep North Update compatibility and current publicized
> assemblies<br/> - **Updated embedded PieceManager for Valheim 1.0's hammer category
> data**<br/> - Updated ServerSync to v1.20<br/> - Restored basement floor and wall
> rendering with Valheim 1.0 materials<br/> - Hardened config reload and environment
> initialization |

Dependency-floor note: Basements 2.0.0 declares `denikson-BepInExPack_Valheim-5.4.2350`.
The fleet's installed pack is **5.4.2350**, so that floor is already met. (The live log
line `User is running BepInExPack Valheim version 5.4.2333` is the running server's
older loader, not the installed package version — worth reconciling before applying.)

#### Is the local patch plugin still required?

**Yes. Updating these four is not sufficient, and updating the one that moved changes
nothing on its own.**

- Only **1 of 4** carriers has a fix (`Basements`). The other **3 have no newer release
  at all** — installed already equals newest published for `OdinsUndercroft 1.3.3`,
  `OdinsHorsePen 1.1.0` and `RavenwoodRestorations 1.0.1`.
- The failure is **not additive, it is a chain abort**: a throwing prefix on
  `PieceTable.UpdateAvailable` stops the remaining prefixes in that Harmony chain. One
  surviving broken carrier keeps `UpdateAvailable` dead for **all ten** copies. Removing
  one of four broken carriers leaves the chain just as dead.
- The blast radius of leaving it is concentrated: `RavenwoodRestorations` alone puts
  **113** `BuildPiece` registrations behind that dead availability pass
  (`OdinsUndercroft` 2, `OdinsHorsePen` 3, `Basements` 2).
- The patch must **not** be a by-name field alias. The installed
  `Valheim10Compatibility 1.4.1` already tried, blocked itself, and logged why: two
  fields named `m_availablePieces` make `Type.GetField`/`Traverse.Field` throw
  `AmbiguousMatchException`. The workable mechanism is the one Basements 2.0.0 used
  upstream — **retarget the 5 `MemberRef` operands per carrier from `m_availablePieces`
  to `m_availablePiecesByCategory`** (identical `List<List<Piece>>` signature), as a
  preloader IL rewrite. That is a metadata-level fix and raises no reflection ambiguity.

Scope note: no shrink. Both assigned steps were completed as specified, plus the
all-119-DLL sweep rather than only the four named packages.

## Update delta

> **SNAPSHOT NOTICE.** Everything below describes the fleet as it stood at the Thunderstore
> index pull of **2026-09-17 21:33**. `InventoryMod` deployed to Ulfsland at **~22:08**,
> after this survey was written. The survey is deliberately **not** rewritten — it is the
> evidence the deploy was chosen from — but the fleet has moved, and the moves are recorded
> in the addendum at the end of Step 1 (`1i`). Re-run `/tmp/moddelta/rederive.py` for the
> current state, or `rederive.py --snapshot` to assert the pre-deploy values published here.
>
> **SCOPE FENCE, added after the survey and governing every recommendation in it.** The
> operator has since directed: **Ulfsland only.** `Doggerland`, `Hrafnheim`, `Storgard` and
> `Vangard` are not to be touched, and the `admin`, `flat` and `vr` profiles are not to be
> edited — `admin` is the linked server profile for all four stopped worlds, so one edit
> there changes four worlds at once. Only the four `ulfsland-*` profiles are in scope.
>
> This survey deliberately measured all five worlds and all seven profiles, and that breadth
> is why several of its findings exist. Read the out-of-Ulfsland rows as **recorded, measured,
> accepted state** — not as a work queue. Two in particular are explicitly deferred and must
> not be acted on: the `admin`-profile `AzuCraftyBoxes` fork (see 1i) and the three
> `shared`-scope AzuEPI pins (see 1i). Everything I recommend for action is Ulfsland-scoped:
> the client catch-up publish in 1f, the eight still-outstanding rows of the twelve in
> 1a-bis, and the P3/P4/P5 recommendations in Steps 2 and 3.

Owner: ModDelta2. Scope: the full installed-vs-newest delta across the fleet, plus P3
(hammer grid overflow), P4 (craft-from-chests / animal autofeed / chest tier), P5
(`ServersideQoL` scope) and P6 (known-pending items). Read-only: nothing installed,
deployed, published or restarted. Package zips fetched for inspection live in `/tmp` only.

### Instruments

| Instrument | What it measured |
| --- | --- |
| `manifest.json` (`version_number`) **beside the plugin DLL** in every plugin dir, all 5 server trees | installed server version + declared dependency floors. DLL filenames recorded alongside, so a bare config can never be mistaken for an install |
| all 7 `$VALHEIM_ROOT/profiles/*/profile-manifest.json` | pinned version + `scope` per profile |
| all 13 `/mnt/vps-sync/profiles/*/state.json` (`packages[]`) | actually-deployed client versions |
| Thunderstore `/c/valheim/api/v1/package/` — full index, 11,400 packages, 167 MB, pulled **2026-09-17 21:33** | newest version, publish dates, deprecation flag, dependency strings, dependants counts (computed by counting packages whose newest version depends on each) |
| Thunderstore `api/experimental/package/<ns>/<name>/<ver>/changelog/` | the quoted changelog lines below |
| `dnfile` 0.18.0, `MemberRef` table walk (`/tmp/moddelta/compare_refs.py`) | before/after field references, installed DLL vs downloaded candidate |
| `dnfile` + raw IL decode (`/tmp/moddelta/sqol_guard.py`) | the `ServersideQoL` host-only guard |
| UTF-16LE byte search over DLLs | .NET string literals (an ASCII grep finds nothing) |

Counts: **154** distinct packages across the fleet; **98** of them installed as DLLs on
the server trees (101 plugin dirs = 98 packaged + 2 loose DLLs + 1 patcher-only dir).
Ulfsland has 101 dirs, the four stopped worlds 97 each; the four Ulfsland-only extras are
`DropCleaner`, `PerfectPlacement`, `ServerCharacters`, `Structure_Tweaks`.

---

### Step 1 — the delta

#### 1a. Server trees behind newest — 12 of 98

All five server trees are **byte-for-byte identical in version terms** for every one of
these: no per-world drift at all. Classification legend: **SAFE** = client/cosmetic,
**SERVER-SIDE** = needs a stopped world to deploy, **DANGEROUS** = touches world data,
terrain format or object placement.

| Package | Installed | Newest | Published | Dependants | Class |
| --- | --- | --- | --- | --- | --- |
| `ValheimModding-Jotunn` | 2.30.0 | **2.30.1** | 2026-09-17 | 5484 | **DANGEROUS** |
| `RandyKnapp-EpicLoot` | 0.14.7 | **0.14.8** | 2026-09-17 | 1826 | **DANGEROUS** |
| `MidnightMods-ImpactfulSkills` | 0.16.1 | **0.17.1** | 2026-09-17 | 134 | **DANGEROUS** |
| `Smoothbrain-ServerCharacters` | 1.4.17.1 | 1.4.16 | 2025-05-02 | 1566 | **not an update — do not touch** |
| `OdinPlus-Basements` | 1.4.1 | **2.0.0** | 2026-09-17 | 83 | SERVER-SIDE (elevated) |
| `OdinPlus-OdinsFoodBarrels` | 1.2.6 | **1.2.7** | 2026-09-17 | 837 | SERVER-SIDE |
| `OdinPlus-OdinsKingdom` | 1.5.8 | **1.5.9** | 2026-09-17 | 825 | SERVER-SIDE |
| `ZenDragon-Zen_ModLib` | 1.14.3 | **1.14.5** | 2026-09-17 | 358 | SERVER-SIDE |
| `blacks7ar-Wisdom` | 1.0.5 | **1.0.6** | 2026-09-17 | 129 | SERVER-SIDE |
| `Ross-RossItemDrawers` | 1.0.8 | **1.0.10** | 2026-09-18 | 6 | SERVER-SIDE |
| `Vapok-AdventureBackpacks` | 2.0.7 | **2.0.8** | 2026-09-17 | 877 | SAFE |
| `shudnal-TradersExtended` | 2.0.2 | **2.0.3** | 2026-09-17 | 390 | SAFE |

The other **86** server-installed packages are already at newest. Eleven of the twelve
above were published on 2026-09-17 and one (`RossItemDrawers 1.0.10`) on 2026-09-18 —
this is a live upstream wave, not accumulated drift.

#### 1a-bis. Priority order, and overlap with tonight's deploy set

`InventoryMod` is deploying five packages tonight: `Jotunn 2.30.1`, possibly
`AAABuildMenu`, `RavenwoodPieces 2.0.5` replacing `RavenwoodVikingHouses 7.7.8`,
`OdinsKingdom 1.5.9`, and `AzuEPI 2.4.8`. Against my 12 rows:

| Row | Covered by tonight's set? |
| --- | --- |
| `ValheimModding-Jotunn` 2.30.0 -> 2.30.1 | **yes** — do not report twice |
| `OdinPlus-OdinsKingdom` 1.5.8 -> 1.5.9 | **yes** — do not report twice |
| the other **10** rows | **no** — still outstanding |

`AAABuildMenu` and `RavenwoodPieces` are not in my 12 because neither is currently
installed anywhere on the fleet; they are additions, not updates.

**`AzuEPI` is not in my 12 either, and it needs a flag.** MEASURED:
`Azumatt-AzuExtendedPlayerInventory` is on **no** server tree and in **no** profile
manifest — it is client-only, on 10 client profiles at 2.4.1 (x5), 2.4.3 (x1) and
2.4.4 (x4). And **2.4.8 is not newest**: newest is **2.4.14**, published
2026-09-14T17:16:33Z, while 2.4.8 dates from 2026-08-31T21:13:59Z — six releases and
two weeks behind.

**Resolved, and it is a deliberate pin — the implied criticism above does not stand.**
`InventoryMod` chose 2.4.8 on dated evidence as a floor-versus-running-loader pin, not by
mistaking it for latest: 2.4.14 declares `denikson-BepInExPack_Valheim-5.4.2350`, and the
**running** pack is 5.4.2333, so 2.4.14's floor is unmet on the loader that is actually
executing while 2.4.8's is not. They also corrected my count on the way past — there are
**four** cached versions in `manager-cache`, not three, and 2.4.8 (cached 2026-09-12, DLL
built 2026-08-31) is absent from the brief entirely. So 2.4.8 is the newest version that
actually runs here, and they held 2.4.14 for the same reason. Recorded because "behind
newest" is the wrong frame whenever the running loader, not the manifest, sets the floor —
which on this fleet is always.

Priority order for the operator, highest first:

1. `blacks7ar-Wisdom` 1.0.5 -> 1.0.6 — the only row that is a **currently-broken** state
   rather than a missed improvement: installed 1.0.5 is a pre-1.0 build running on 1.0.x.
2. `ValheimModding-Jotunn` 2.30.0 -> 2.30.1 — DANGEROUS, but the highest-leverage row on
   the fleet and 5,484 packages sit on it. *(covered tonight)*
3. `OdinPlus-Basements` 1.4.1 -> 2.0.0 — the only upstream fix for any of the four P2
   dead-field carriers, verified at IL level.
4. The client catch-up deploy in 1f — 33 packages on two live client profiles, including
   `Valheim10Compatibility 1.4.0 -> 1.4.1`, whose own changelog says every client must
   update before joining.
5. `ZenDragon-Zen_ModLib` 1.14.3 -> 1.14.5 — **gate unverified**; six Zen mods fall
   together if 1.14.5 rejects game 1.0.12. Verify before deploying, not after.
6. `RandyKnapp-EpicLoot` 0.14.7 -> 0.14.8 and `MidnightMods-ImpactfulSkills`
   0.16.1 -> 0.17.1 — both DANGEROUS by object placement; ImpactfulSkills is hard-coupled
   to Jotunn 2.30.1 and cannot go earlier than row 2.
7. `OdinPlus-OdinsKingdom` 1.5.9 *(covered tonight)*, `OdinPlus-OdinsFoodBarrels` 1.2.7,
   `Ross-RossItemDrawers` 1.0.10 (real Jotunn floor 2.30.1, so after row 2),
   `Vapok-AdventureBackpacks` 2.0.8, `shudnal-TradersExtended` 2.0.3 — low risk.
8. `Smoothbrain-ServerCharacters` — **exclude by name from every bulk pass.**

Confirming a peer measurement rather than contradicting it: `marcopogo-PlanBuild` is
**not installed anywhere** and is **not** in my 12-row table. Re-derived in one
invocation: absent from all 7 profile manifests, absent from every plugin dir on all 5
server trees, and absent from all 13 client `state.json`. The orphaned
`marcopogo.PlanBuild.cfg` exists in **all five** server `config_merged/bepinex`
directories (`InventoryMod` counted three profile-level copies — both are true, they are
different locations), and it is listed among the four true orphans in 1h.

**A floor constraint I recorded as met, and should not have.** My own check confirmed the
*installed package* is `denikson-BepInExPack_Valheim-5.4.2350`, and on that basis I treated
the 5.4.2350 floors declared by `OdinPlus-Basements 2.0.0` and
`Ross-RossItemDrawers 1.0.10` as satisfied. `InventoryMod` has since measured the
**running** pack properly and it is not the same thing: the on-disk core assembly reports
version `5.4.23.3`, which cannot identify a pack at all, because packs 2333 and 2350 both
ship core 5.4.23. They replaced the inference with md5 of every one of the 22 on-disk core
files against each cached pack zip — **22/22 identical to 5.4.2333, 11/18 to 5.4.2350,
differing in exactly the decisive files (`BepInEx.Preloader.dll`, `BepInEx.dll`,
`HarmonyXInterop.dll`)**. So pack 2350 is staged on disk but **2333 is what is loaded**, and
the live log line `User is running BepInExPack Valheim version 5.4.2333` is accurate rather
than stale. Treat the running pack as the live constraint for both 5.4.2350 floors. Their
measurement supersedes my manifest-level reading; see `InventoryMod`'s own report for it.

#### 1b. Justification for every DANGEROUS call

**`ValheimModding-Jotunn` 2.30.0 -> 2.30.1** — two independent triggers, both quoted from
the Thunderstore changelog for `ValheimModding/Jotunn/2.30.1`:

> * Fixed TerrainOp not being registed into the new `ObjectDB.m_terrainOps` /
>   `ObjectDB.m_terrainOpsByHash` (thx MaikiOS)

— terrain-op registry, i.e. terrain format.

> * Fixed selecting a piece placing the piece of another category, the category list of a
>   piece table is kept up to date again

— object placement. Corroborated at IL level: 2.30.0's `Jotunn.dll` references only
`PieceTable::m_availablePiecesByCategory`, while 2.30.1 references **both**
`m_availablePieces` and `m_availablePiecesByCategory` (`dnfile` `MemberRef` walk, installed
DLL vs downloaded 2.30.1). This is also the single highest-leverage row on the fleet — see
`## Piece access` above for the build-menu category analysis, which PieceAccess owns; I make
no claim here about whether it causes the `fb_` unreachability.

**`RandyKnapp-EpicLoot` 0.14.7 -> 0.14.8** — quoted:

> * Increases placement allowance for bounties/treasures
> * Removes bounty/treasure relocation due to the potential for schisms

Both lines are about where world objects get placed, and "schisms" is the author's own word
for divergence between stored and expected world state. Note the newest package still
declares floors `denikson-BepInExPack_Valheim-5.4.2333` and `ValheimModding-Jotunn-2.29.2`,
both already exceeded here.

**`MidnightMods-ImpactfulSkills` 0.16.1 -> 0.17.1** — 0.17.1 itself is only
`- Bump dependency versions again`; the danger is in 0.17.0, which this bump carries:

> * Grid spacing now accounts for the plant's own collider, not just its grow radius, which
>   is what the game actually measures against. Barley and flax grids are wider; every other
>   crop and all tree saplings keep the spacing they had
> * An existing field that was already planted too tightly will now show red cells rather
>   than letting you feed more doomed crops into it

That is placement geometry, and the second line changes behaviour on *already-planted*
fields. **Hard dependency coupling, measured:** `ImpactfulSkills 0.17.1` declares
`ValheimModding-Jotunn-2.30.1`, so it cannot be taken without the Jotunn row above.

#### 1c. The three rows that are not simple bumps

**`Smoothbrain-ServerCharacters` — installed 1.4.17.1, Thunderstore newest 1.4.16.**
Installed is **ahead** of upstream and is a local build. From its own on-disk
`manifest.json`:

> "description": "Local build: upstream bb7d3cd6 (1.4.17) plus 1 valheim-portal patch(es).
> NOT an upstream release."

Its `website_url` pins the GitHub tree `blaxxun-boop/ServerCharacters/tree/bb7d3cd6`. Any
"update everything to newest" pass would **downgrade** this to 1.4.16 and silently drop the
local patch. It is installed on the Ulfsland tree only (4 profile manifests). Exclude it by
name from any bulk update. `manager-cache` holds `ServerCharacters-1.4.16.zip`, `-1.4.17.zip`
and `-1.4.17.1.zip`, so the provenance chain is intact.

**`OdinPlus-Basements` 1.4.1 -> 2.0.0 — SERVER-SIDE, elevated, and it is the P2 fix.**
Quoted:

> | 2.0.0 | - Valheim 1.0 Deep North Update compatibility and current publicized
> assemblies<br/> - Updated embedded PieceManager for Valheim 1.0's hammer category
> data<br/> - Updated ServerSync to v1.20<br/> - Restored basement floor and wall rendering
> with Valheim 1.0 materials<br/> - Hardened config reload and environment initialization |

I verified the claim rather than trusting it. `dnfile` `MemberRef` walk, installed vs
downloaded 2.0.0:

| Build | `PieceTable::m_availablePieces` | `PieceTable::m_availablePiecesByCategory` |
| --- | --- | --- |
| 1.4.1 installed | present | absent |
| **2.0.0 candidate** | **absent** | **present** |

So the embedded `PieceManager` really was rebuilt against 1.0's field layout. **Declared
honestly: I am *not* calling this DANGEROUS.** No changelog line asserts a world-data,
terrain or placement change, and inventing one would be a guess. What it is instead is a
major-version bump (1.x -> 2.0.0) of a mod that owns *placed* pieces on a live world — a
risk-posture concern, not a changelog-justified one, and labelled as posture.

**`ZenDragon-Zen_ModLib` 1.14.3 -> 1.14.5 — one unverified gate.** Quoted:

> v1.14.5
> - fix vanilla: when opening containers with scroll bars the alignment of the grid was not
>   reset when switching to containers without scrollbars.
>
> v1.14.4
> - updated for support Valheim version v1.0.14

and, from 1.14.1:

> - fix: version check accepts both v1.0.12 (steam) and v1.0.11 (microsoft)

This library performs a **hard game-version check** (MEASURED: the literal
`Valheim version check is disabled!` exists in `Zen.ModLib.dll` as a UTF-16LE string, i.e.
there is a config escape hatch). 1.14.4 names 1.0.14; the fleet's server binary was last
written 2026-09-12 and the brief records the game as 1.0.12. **I could not confirm from the
package whether 1.14.5's accepted-version list still includes 1.0.12** — that needs a boot,
which is out of scope here. Field-layout risk is nil: installed 1.14.3 and downloaded 1.14.5
have identical interesting-reference sets (`ZNetView::Everybody`,
`ZRoutedRpc::GetServerPeerID`). Five installed Zen mods sit behind this library
(`ZenBreeding`, `ZenItemStands`, `ZenPath`, `ZenRaids`, `ZenRedecorate`, plus
`ZenWorldSettings` which declares a 1.14.1 floor), so a failed gate takes all six down at
once. Verify the gate before deploying.

#### 1d. The remaining SERVER-SIDE and SAFE rows, each with its line

| Package | Quoted changelog line | Class + note |
| --- | --- | --- |
| `blacks7ar-Wisdom` 1.0.5 -> 1.0.6 | `- updated for valheim 1.0 deepnorth` | SERVER-SIDE, and **not optional**: installed 1.0.5's own line is `- updated for the latest valheim build (221.10)`, i.e. a **pre-1.0 build currently running on 1.0.x** |
| `OdinPlus-OdinsFoodBarrels` 1.2.6 -> 1.2.7 | `- Added new Deep North resources.` | SERVER-SIDE, purely additive (new pieces need server+client parity). Its 1.2.6 line `- Fixed a build hammer crash caused by the recent Valheim update restructuring PieceTable's per-category piece storage.` is the already-installed correct `PieceManager` copy |
| `OdinPlus-OdinsKingdom` 1.5.8 -> 1.5.9 | `* **Fixes:** Fixed missing rope on rope fence.` | SERVER-SIDE, cosmetic mesh only. Its 1.5.6 line `* **Fixes:** Peicetable errors for Valheim 1.0.` is the already-installed correct copy |
| `Ross-RossItemDrawers` 1.0.8 -> 1.0.10 | `## 1.0.9 - Built and tested against Jotunn 2.30.1, which the dependency now asks for. Nothing else changed.` and `## 1.0.10 - Drawers no longer feel sluggish on a dedicated server.` | SERVER-SIDE. **Coupling discrepancy, measured:** the changelog says the dependency asks for Jotunn 2.30.1, but the 1.0.10 manifest actually declares `ValheimModding-Jotunn-2.30.0`. Treat 2.30.1 as the real floor |
| `Vapok-AdventureBackpacks` 2.0.7 -> 2.0.8 | `* Fixed: Jewelcrafting packages it's own font which was overriding part of a vanilla font, causing the Splash screen to appear blank.` | SAFE, client cosmetic. Operator note: the *already-installed* 2.0.6 introduced `Anonymous Telemetry ... Defaults to enabled with auto-opt-in on launch` — pre-existing, not new in 2.0.8, but worth an explicit opt-out decision |
| `shudnal-TradersExtended` 2.0.2 -> 2.0.3 | `* fixed gamepad stick input reaching the game and free-fly camera through Valheim 1.0.14 vector getters while the configuration editor blocks game input` | SAFE, client input/UI. Declares `shudnal-ConditionalConfigSync-1.0.5`; installed is 1.0.8, floor met |

#### 1e. Profile manifest pins

All 7 profile manifests pin exactly the same 12 packages behind newest as the server trees —
the manifests and the deployed server trees agree perfectly, with one exception:
`Smoothbrain-ServerCharacters 1.4.17.1` appears in only **4** of 7 manifests (the rest do not
carry it). There is **no manifest-vs-server-tree drift** anywhere on this fleet.

#### 1f. Client deployments — the real operational gap

This is the largest finding in the delta and it is not an upstream problem.

**LIVE world (Ulfsland), 3 client profiles, measured against the Ulfsland *server tree*
rather than against newest:**

| Client profile | Packages disagreeing with the live server tree |
| --- | --- |
| `ulfsland-vr-flat-admin` | **0** — perfect lockstep across all 98 server packages |
| `ulfsland-non-vr` | **33** |
| `ulfsland-vr-flat` | **33** |

The same 33, identically, on both non-admin profiles: `ServersideQoL` 2.0.10 vs 2.0.13,
`ServersideQoL_AutoStore` 2.0.8 vs 2.0.11, `AAA_Crafting` 2.1.6 vs 2.1.8,
`AzuWearNTearPatches` 1.0.8 vs 1.0.9, `TrueInstantLootDrop` 1.0.3 vs 1.0.4,
`balrond_constructions` 1.4.4 vs 1.4.5, `DeathPenalty` 1.3.0 vs 1.3.1, `DigDeeper` 1.3.0 vs
1.3.1, `Ravenwood_Currency` 1.0.0 vs 1.0.1, `Infinity_Hammer` 1.83.0 vs 1.84.0, `OdinShip`
0.7.9 vs 0.8.1, `ImpactfulSkills` 0.16.0 vs 0.16.1, `ValheimArmory` 1.29.3 vs 1.31.0,
`OdinArchitect` 1.7.2 vs 1.7.5, `OdinCampsite` 1.6.3 vs 1.6.4, `OdinsFoodBarrels` 1.2.4 vs
1.2.6, `OdinsKingdom` 1.5.6 vs 1.5.8, `EpicLoot` 0.14.5 vs 0.14.7, `RossItemDrawers` 0.9.9 vs
1.0.8, `BuildOnShip` 2.2.14 vs 3.0.112, `ServerCharacters` 1.4.17 vs 1.4.17.1,
`AdventureBackpacks` 2.0.1 vs 2.0.7, **`Valheim10Compatibility` 1.4.0 vs 1.4.1**,
`ZenWorldSettings` 1.12.0 vs 1.13.0, `Zen_ModLib` 1.14.0 vs 1.14.3, `Endurance` 1.1.2 vs
1.1.3, `Herbalist` 1.4.9 vs 1.5.0, `VikingsDoSwim` 1.4.1 vs 1.4.2,
`WieldEquipmentWhileSwimming` 1.1.3 vs 1.1.4, `ConditionalConfigSync` 1.0.6 vs 1.0.8,
`HipLantern` 1.1.7 vs 1.1.8, `FearMe` 0.2.0 vs 1.0.0, **`More_World_Locations_AIO` 5.1.0 vs
5.1.1**. Every package is `scope: shared`. The two non-admin profiles simply never received
the 2026-09-17 deploy wave that the admin profile and the servers did.

`Valheim10Compatibility 1.4.0 -> 1.4.1` on those two profiles deserves separate emphasis
because its own changelog says the mismatch is not cosmetic:

> Because the Adapter bytes change, an AzuAntiCheat server must replace its whitelist folder
> with the 1.4.1 package (not add it beside 1.4.0 — two versions of one plugin in the
> whitelist lock every non-admin client out), and every client must update to 1.4.1 before
> joining.

**Stopped worlds, 10 client profiles** (7x Hrafnheim, 2x Vangard, 1x Vangard-Redesign): a
full generation behind — `Jotunn 2.29.2` (vs 2.30.0 on the live servers) on all 10,
`Infinity_Hammer 1.79.0` on all 10, `World_Edit_Commands 1.72.0` on all 10, and
`More_World_Locations_AIO 5.0.8` on **9** of them (`Vangard-Redesign--vangard-redesign-flat`
does not carry MWL at all — MWL is on 12 of the 13 client profiles, not 13). Those worlds are
deliberately stopped, so this is **expected, not a defect**, and I am not proposing to touch
them. Recorded only so a bulk pass does not treat them as drift to be "corrected".

#### 1g. Deprecated-upstream packages, and what the flag actually means

23 installed packages carry `is_deprecated=true`. **Do not read that as abandonment.**
MEASURED: of Azumatt's 148 packages, **145 are flagged deprecated, including all 30 updated
since 2026-09-01** — it is a blanket author-level retirement flag applied across a catalogue
that is still receiving releases. The fleet already runs `Azumatt-AAA_Crafting 2.1.8`
published 2026-09-14 under that same flag.

Installed-and-flagged, still at newest: `AAA_Crafting` 2.1.8, `AzuWearNTearPatches` 1.0.9,
`PerfectPlacement` 1.2.2, `TrueInstantLootDrop` 1.0.4, `blacks7ar-Mask` 1.0.3,
`Skarif-BuildOnShip` 3.0.112, `fedorovdgap-AzuCraftyBoxes` 1.8.18 (see P4).
Flagged **and** behind, all client-only: `AzuAreaRepair` 1.1.6 -> 1.1.7, `AzuAutoStore`
3.0.14 -> 3.1.4, `AzuExtendedPlayerInventory` 2.4.1/2.4.3/2.4.4 -> 2.4.14,
`Azumatt-AzuCraftyBoxes` 1.8.14/1.8.15 -> 1.8.19, `Yggdrah-BetterRiding` 1.3.5 -> 1.3.6,
`Yggdrah-DragonRiders` 2.1.6 -> 2.1.8.

#### 1h. Free finding — configs on the server with no plugin behind them

Screened all 116 `.cfg` files in `Ulfsland/config_merged/bepinex` by searching every one of
the 119 installed plugin DLLs for the config's BepInEx GUID as a UTF-16LE literal.

> **Instrument limit, stated because it bit me.** That screen has a **false-negative** mode:
> a plugin that builds its GUID from constants, or stores it only in an attribute blob, will
> not match even though it is installed. I originally resolved the 26 misses by
> cross-checking them against the installed package list **using hand-written name guesses**
> — which is the substring trap `InventoryMod` named, and it produced a wrong call. Re-done
> properly: the owning GUID is resolved by search, and where that finds nothing the question
> is settled by whether a plugin directory or loose DLL for the package actually exists.
>
> **The reusable rule, and it is `InventoryMod`'s formulation, not mine.** My GUID screen
> and their `strings` sweep are exact mirrors. Mine can only ever *propose* an orphan —
> `com.yourname.dropcleaner` and `org.bepinex.valheim.displayinfo` are both installed yet
> invisible to a UTF-16LE string search, so **absence proves nothing**. Theirs invented 95
> prefab removals that were all compression noise from a 17 MB embedded asset bundle, so
> **presence proves nothing**. One instrument cannot conclude absence, the other cannot
> conclude presence, and both emitted clean well-formed output while failing. Neither is
> wrong as a *screen*; both are wrong as a *verdict*. **A screen proposes; a second,
> independent instrument concludes.** Every count in this section that came from a screen is
> closed out by a second instrument — a manifest beside a DLL, an IL reference walk, or a
> declared list in the profile manifest — and where no second instrument exists the claim is
> marked `[INFERENCE]`.
>
> **The necessary refinement, from `HarbourCity`, and it tests the sentence above.** They ran
> a mod-free and a modded location dump and got **12,301 locations, 177 types, identical
> hash** — which reads as two instruments corroborating each other, when in fact one had been
> silently disarmed (More_World_Locations' `Awake` threw on a missing config, so its ~178
> locations never registered) and both shared the same failure. Their formulation:
> **agreement between two instruments is only evidence when they are capable of
> disagreeing.** That is harder to catch than a wrong number, because a wrong number looks
> wrong and this looked right.
>
> So "closed out by a second instrument" above is a claim that has to be audited, not
> asserted. Auditing it here: the three headline cross-checks pass the disagreement test,
> because each pair could have come apart and the answer would have been visibly different —
> the Basements `PieceManager` rebuild (author prose in a changelog **vs** field references
> in binary metadata), the `AzuCraftyBoxes` fork-vs-official equivalence (changelogs **vs**
> `ZRoutedRpc` reference sets), and the `ServersideQoL` 5-second interval (the operator's
> observed client log **vs** the `ldc.r4 5.0` operand feeding `WaitForSeconds`).
>
> **One claim did not pass it, so I closed it rather than leaving it stated.** Every
> "installed version" here is read from the package's own `manifest.json` beside its DLL. A
> mis-staged DLL under a correct `manifest.json` would be invisible to that, and the
> Thunderstore index is **not** an independent check, because both derive from the same
> author-published metadata — they are not capable of disagreeing about the bytes on disk.
> The instrument that is independent is the one `InventoryMod` used on their own deploy:
> hashing the staged bytes against the cached package zip. I have now run it
> (`/tmp/moddelta/verify_staged_bytes.py`): for every package named anywhere in this section,
> sha256 each DLL on the Ulfsland server tree against the same-named member of the cached
> zip for the version its `manifest.json` claims.
>
> **Result: 17 packages considered, 17 byte-compared, 0 mismatches.** Including the five the
> deploy changed and `ServerCharacters 1.4.17.1`, whose staged bytes match
> `ServerCharacters-1.4.17.1.zip` — so the local build is intact as staged, not drifted. The
> version delta is therefore byte-backed, not just manifest-asserted.
>
> Two notes, because the verifier itself failed twice before it worked and both failures were
> the house shape. Its **first** run printed `0 byte mismatch(es)` from **zero actual
> comparisons** — my zip-stem matcher expected `<Namespace>-<Name>-<Version>` when the cache
> uses `<Name>-<Version>` — a clean pass reported out of an empty set. It now prints the
> compared count beside the mismatch count and **exits non-zero rather than report a clean
> result from zero comparisons.** Its **second** run left Jotunn unchecked, because Jotunn
> ships its DLL as the zip member `plugins\Jotunn.dll` and `os.path.basename` does not split
> backslashes on POSIX. Both were instruments emitting well-formed output while measuring
> nothing — caught only because the note column reported *how many* comparisons backed the
> verdict. A count of evidence beside a verdict is cheap and it is what made both visible.
>
> **The four rules, as the reusable output of this section.** Settled jointly with
> `HarbourCity` and `InventoryMod` after five instruments failed this way in one night.
> Rules 1-3 follow immediately; **rule 4 is the residual case and is stated after the
> declared-vs-arrived table below**, because it only makes sense once that framing is in hand:
>
> 1. **A screen proposes; a second *independent* instrument concludes — and independence
>    must be established before concurrence may be read as confirmation.**
> 2. **Every verdict carries the count of evidence behind it, and `n=0` is a failure, not a
>    pass.**
> 3. **The evidence must be distinct, not merely counted** — a silently overwritten check
>    *looks* like coverage and contributes nothing. Rule 3 is `HarbourCity`'s promotion of
>    something I had buried under rule 2, and they are right that it is co-equal: the count
>    alone cannot catch it, because a duplicate label is `n=0` hiding inside a
>    healthy-looking `n`. You need the count **and** the labels to be distinct. My
>    re-derivation harness now refuses on either.
>
> Rule 1 needs the second clause or it is not usable, and the manifest/Thunderstore pair is
> why. `HarbourCity`'s sharper statement of my own defect, which I am adopting over my
> wording: those two sources do not merely fail to be independent — a mis-staged DLL under a
> correct `manifest.json` was **unfalsifiable** by that pair. It is worse than an instrument
> that happens to be broken, because that comparison could never have discriminated even
> working perfectly. Two agreeing readings of one source are one reading.
>
> Rule 2 is the cheap general guard, and it is the one to reach for first. An empty set
> cannot produce a suspicious *number*, but it can always produce a suspicious `n=0`.
> The same shape recurred across the whole night in five different instruments: my verifier's
> clean pass over zero comparisons, my GUID screen concluding absence it cannot see,
> `InventoryMod`'s `strings` sweep concluding presence from compression noise,
> `PieceAccess`'s `find -maxdepth 4` returning a confident zero it could not have reached
> past, and `HarbourCity`'s frame wait writing a complete table of `znet=0`. Every one
> emitted well-formed output. Not one raised. All five would have been visible immediately
> from a printed evidence count.
>
> **What the rules are actually about, and this is the line to keep.** Settled with
> `HarbourCity` at the end of the session: **the declared state and the arrived state are
> different objects, and only an instrument pointed at the arrival can tell them apart.**
> Every defect found tonight, across four agents and two problem domains, sits in that gap —
> and in every row the declaration was *correct*. Nothing was mis-specified:
>
> | Declared | Arrived |
> | --- | --- |
> | seven manifests say BepInEx `5.4.2350` | the loader says `5.4.2333` |
> | 94 prefabs in the bundle's container index | 29 registered into the game |
> | four `PieceManager` carriers with correct configs and pins | the Harmony chain aborts, the menu stays empty |
> | `ServersideQoL` scoped `shared`, reaching clients as designed | a client instance that only warns and sleeps |
> | a road's fitted profile | the road's height where it meets what it joins |
> | a derived plan listing 12,301 locations | 32 markers standing, 28 of them invisible to that file |
>
> Rules 1-3 are instructions for staying in the right-hand column. **Independence**,
> because two readings of the declaration are still one reading of the declaration.
> **Evidence counts**, because an arrival you failed to look at reads identically to an
> arrival that matched. **Distinctness**, because a duplicated look is one look.
>
> And the hardest case to catch is neither column but a disguise: `HarbourCity`'s modded
> location scan was an arrival instrument *by construction*, silently disarmed, reporting the
> declaration back with an identical hash as reassurance. An arrival instrument that has
> stopped arriving looks exactly like agreement.
>
> **Rule 4, the residual case, and it defeats the obvious remedy.** It exists because rules
> 1-3 tell you to pick an arrival instrument, point it at the arrival, and count and
> distinguish what it saw — **none of them tells you to check that the instrument is still an
> instrument.** Choosing a better one cannot help here; only verifying that the one you chose
> still discriminates can:
>
> 4. **Carry a positive control in the same run: make the comparison visibly disagree with
>    something it should disagree with, and refuse a clean result if it cannot.**
>
> `InventoryMod`'s pack measurement did this without being asked — `22/22` identical to
> 5.4.2333 **and `11/18` to 5.4.2350** — where the second figure is what proves the hashing
> could discriminate at all. My own byte verifier did **not**, and I only noticed because
> this rule was being written: `17/17 MATCH` is indistinguishable from a comparison that
> cannot fail. It now opens with a control and refuses (exit 3) if the control passes when it
> should not. Current run:
>
> ```
> POSITIVE CONTROL: AAA_Crafting 2.1.8 vs cached AAA_Crafting-2.1.6 -> 1/1 dll DIFFER,
> as required. The comparison discriminates.
> 17 packages considered; 17 actually byte-compared; 0 mismatch(es).
> ```
>
> The same shape is the acceptance test for `HarbourCity`'s restaged location scan, stated by
> `InventoryMod` and worth repeating because it inverts the intuition: **success is the dump
> *disagreeing* with the mod-free one. A second `12,301 / 177 / identical hash` means `Awake`
> threw again — that identity is a failure signal, not a checksum.**
>
> **Attribution, corrected at `HarbourCity`'s request so the record is accurate rather than
> gracious.** Rule 1's generalisation is `InventoryMod`'s as much as `HarbourCity`'s, and the
> positive-control *practice* of rule 4 was `InventoryMod`'s by demonstration before it was
> anyone's by statement. Rule 2 is mine — I built the evidence-count check and it caught two
> of my own bugs before it caught anything else. Rule 3 is `HarbourCity`'s promotion of
> something I had buried. Rule 4 is mine for stating it, and for being the first to fail it
> deliberately. The framing that unifies all four — declared state versus arrived state — is
> `HarbourCity`'s. None of the four came from one agent, and none would have been found
> without a peer contradicting a published number.

Three of the 26 misses are configs for plugins that **are** installed — so not orphans — but
only two for the reason I first gave:

| Config | Verdict | Evidence |
| --- | --- | --- |
| `com.yourname.dropcleaner.cfg` | installed | `plugins/DropCleaner/DropCleaner.dll` present |
| `org.bepinex.valheim.displayinfo.cfg` | installed | loose `plugins/Valheim.DisplayBepInExInfo.dll` present |
| `kg.ItemDrawers.cfg` | **I was wrong — this is residue** | no DLL anywhere carries the GUID `kg.ItemDrawers`; the live drawer mod is `Ross-RossItemDrawers`, whose DLL is `RossItemDrawers/ItemDrawers.dll` and whose GUID is `com.rossdwest.itemdrawers`, with its own separate `com.rossdwest.itemdrawers.cfg` |

**So there is a third expected-residue entry, found by `InventoryMod`:** `kg.ItemDrawers.cfg`
belongs to `cybrp-ItemDrawers`, which the manifest puts in **`disabled_packages` in 6 of 7
profiles**. The one profile that does not disable it is `ulfsland-dn` — and the reason is a
*different package*: `ulfsland-dn` carries `Ross-RossItemDrawers 1.0.8` in `packages` as
`shared`, deployed and live. The fleet deliberately disables one item-drawer mod and runs a
different implementation instead. **A residue checker keyed on the substring `ItemDrawers`
reads that deliberate substitution as contradictory state** — which is exactly how I got it
wrong, by mapping `kg.ItemDrawers` to `Ross-RossItemDrawers` on the strength of the name.

> **CORRECTED 2026-09-17 23:0x — my first classification below was wrong, and the reason is
> my own reader.** I had been reading only the `packages` array out of
> `profile-manifest.json`. The schema (`schema_version: 2`) actually carries **six** lists:
> `packages`, `client_only_packages` (9-12 entries per profile), `manual_server_packages`,
> `excluded_packages`, `disabled_packages` and `custom_packages`. Caught by `InventoryMod`,
> who showed that `ulfsland-dn` has AzuEPI at `scope: client-only` in
> `client_only_packages` — I had reported it "removed entirely" because it was not in
> `packages`. My package *totals* survive, because the union with the 13 client
> `state.json` files already covered every client-only package; what did not survive is
> every statement that read a package's **placement** from `packages` alone. The
> classification below is re-derived against all six lists.

**18 configs for mods in NO manifest list at all** — true orphans, and **14 of these I had
mis-filed as "client-only leaks"**: `Azumatt.BetterWards`, `CW_Jesse.BetterNetworking`,
`marcopogo.PlanBuild`, `neuralyze.worldseed` (the four I had right), plus
`Azumatt.AzuAreaRepair`, `Azumatt.BowsBeforeHoes`, `Azumatt.WardIsLove.Localization`,
`CookieMilk.IdentityCrisis`, `CookieMilk.ReadMe`, `CookieMilk.YouAreBeingWatched`,
`MVP.Valheim_Serverside_Simulations`, `RustyMods.RustyBuildPieces`, `RustyMods.Shipwright`,
`blacks7ar.BoatAdditions`, `blacks7ar.CoreWoodPieces`, `blacks7ar.MagicRevamp`,
`com.yourname.valheimoptimizer`, `redseiko.valheim.searscatalog`. These mods are not in
`packages`, not in `client_only_packages`, not excluded and not disabled — they are simply
not managed any more, and only their configs remain. **The orphan count is 18, not 4.**

**3 genuine client-only leaks** — mod correctly scoped in `client_only_packages`, config
nevertheless copied into the server tree: `Avo.BackpacksVRFix`
(`geekstreet-BackpacksVRFix`), `EpicLootVRFix` (`geekstreet-EpicLootVRFix`), and
`com.maxsch.valheim.vnei` (`MSchmoecker-VNEI`). VNEI needs its own sentence because a
package can appear in two lists at once and I got it wrong the first time even *after*
widening the reader: it is `client_only_packages` in `admin`, `flat`, `ulfsland-admin`,
`ulfsland-dn` and `ulfsland-flat`, **and separately** `excluded_packages` in `vr`. So its
server-side config is a real leak from the five profiles that manage it client-only, not
residue from the one that excludes it. Taking the first non-`packages` list found is not
enough; placement is a set, not a value.

**3 are expected residue, not defects** — the manifest deliberately says so, which my first
pass could not see at all: `org.bepinex.plugins.valheimvrmod` (`ValheimVR-ValheimVR`,
`excluded_packages` in all 7), `shudnal.HarpoonExtended` (`shudnal-HarpoonExtended`,
**`disabled_packages` in all 7**), and `kg.ItemDrawers` (`cybrp-ItemDrawers`,
**`disabled_packages` in 6 of 7** — see the substitution note above). A config belonging to
an explicitly disabled or excluded
package is the system working as designed.

The brief's warning holds, and now cuts both ways: a config file is not evidence a mod is
installed — **and the absence of a package from one manifest list is not evidence it is
unmanaged.** Both directions need the full vocabulary to answer, and a package's placement
has to be read as the full set of lists it appears in.

And a **scope inversion in the other direction**: `mvp-Serverside_Simulations 1.1.9`,
described upstream as *"Run world and monster simulations on a dedicated server"*, is
installed on **10 client profiles and zero server trees**. A dedicated-server mod deployed
only to clients. Same class of defect as P5 below, mirrored.

##### 1h-bis. Why the orphans keep coming back, and a second orphan shape

`InventoryMod` handed over two items from their deploy window that belong here. I verified
both and **both are wider than reported**.

**The orphans are re-created on every deploy, because removal is copy-only.** The source of
truth for a server config is the profile's own `server-config/` directory, and the deploy
copies from there into the world tree — it never deletes. So deleting a config from
`<World>/config_merged/bepinex` does not remove it; it re-lands on the next deploy.
MEASURED for the example `InventoryMod` raised, `Azumatt.AzuExtendedPlayerInventory.cfg`:

| Where | State |
| --- | --- |
| world trees | present in **all five** (`Ulfsland`, `Hrafnheim`, `Doggerland`, `Storgard`, `Vangard`) — they reported Ulfsland |
| source profiles | `profiles/admin/server-config/`, `profiles/ulfsland-admin/server-config/`, `profiles/ulfsland-dn/server-config/`, all mtime 2026-08-17 22:29:33 — **3** sources, they reported 1 |
| the plugin itself | **absent from every server tree** — AzuEPI is client-only, so nothing on the server can read it |

This is the mechanism behind the whole of 1h: the 4 true orphans and 19 client-only leaks
are not one-off litter, they are re-copied every deploy. Any cleanup has to remove the
file from the **source profile's `server-config/`**, not from the world tree.

**A second, different orphan shape: stale extracted staging.** This one is not a config,
it is an extracted plugin directory for a package the profile's manifest does not list —
so an inventory taken by listing staged directories reports a package that will never
reach a client. MEASURED across all 7 profiles, staged `version_number` read from each
directory's own `manifest.json` and compared against the profile manifest:

| Profile | Staged `AAABuildMenu` | Profile manifest says |
| --- | --- | --- |
| `admin` | 1.0.1, 2026-07-28 07:45:49 | **absent** |
| `flat` | 1.0.1, 2026-07-28 07:45:49 | **absent** |
| `ulfsland-dn` | 1.0.1, 2026-07-28 07:45:49 | **absent** |
| `ulfsland-vr` | 1.0.1, 2026-07-28 07:45:49 | **absent** |
| `vr` | 1.0.1, 2026-07-28 07:45:49 | **absent** |
| `ulfsland-flat` | 1.0.4, 2026-09-17 22:08:49 | 1.0.4, `shared` |
| `ulfsland-admin` | 1.0.4, 2026-09-17 22:08:56 | 1.0.4, `shared` |

**Five** profiles carry the stale 1.0.1 tree, not two, and **two** carry tonight's 1.0.4,
not one. `InventoryMod` verified the publish path reads the manifest rather than the
staging directory, so none of the five reaches a client — harmless today. The finding is
the instrument warning: **staged directories are not an installed-package list.** That is
the same trap as 1h's configs, one layer further out, and it is the reason every count in
this section is anchored on a manifest beside a DLL rather than on a directory listing.

**And the one failure on this fleet tonight that no instrument would have prevented.** My
delta recorded AzuEPI as client-only on 10 client profiles and on no server tree, and handed
that to `InventoryMod`. They read "zero on server" as evidence of **absence** when it was
evidence of **scope**, added it as `shared`, and put a client-side vanity UI mod on a
dedicated server, where its `GetIcon`/`IconPatches` postfix threw `BadImageFormatException`
and took `ComfortTweaks.Awake()` and Herbalist's init down with it — which is the only
reason `ValheimRcon` never bound. Both victims verified recovered by their own
config-rewrite timestamps on the next boot, i.e. by positive evidence rather than by absence
of error.

It is recorded here, in their words rather than mine, because it is the sharpest case in this
document and it points the opposite way from everything else in it: **the data was correct,
present, and in their hands. No better instrument would have saved them; only reading the one
they already had.** Every other failure tonight — my GUID screen, my name guesses, my
manifest reader, their `strings` sweep, their core-version inference, a `find -maxdepth 4`,
an incomplete opcode table — was an instrument returning a confident wrong answer. This one
was a correct answer read wrongly, and it is the more expensive of the two shapes because no
amount of instrument hardening reaches it.

The operative lesson for this report: stating a package's *presence* without its *scope* is
not a complete finding. On this fleet `scope` is not metadata — it is the difference between
a working server and a dead RCON, and, as it turned out, also the difference between the
operator's VR and flat clients being independently configurable or not, since removing the
server-side copy removed the ServerSync peer that would have overridden VR at the connect
handshake. **The correct scope was also the scope that made the operator's intent
achievable.** Every scope claim in this section is stated as a list-placement measured
across all six manifest lists, for that reason.

#### 1i. Addendum — what the deploy changed, measured after it landed

`InventoryMod` deployed to **Ulfsland only** at ~22:08, after this survey was written. I
re-ran the harness and it **failed**, which is the correct outcome: it is built to notice the
fleet moving under it. Two of its assertions broke (`fork installed version` and the
deprecated-and-installed count), and chasing them produced the state below. The harness now
takes `--snapshot` to assert this report's pre-deploy values and defaults to asserting the
post-deploy reality.

**Server trees — five packages moved, Ulfsland only:**

| Package | Ulfsland | Hrafnheim / Doggerland / Storgard / Vangard |
| --- | --- | --- |
| `ValheimModding-Jotunn` | **2.30.1** | 2.30.0 |
| `OdinPlus-Basements` | **2.0.0** | 1.4.1 |
| `OdinPlus-OdinsKingdom` | **1.5.9** | 1.5.8 |
| `blacks7ar-Wisdom` | **1.0.6** | 1.0.5 |
| craft-from-chests | **`Azumatt-AzuCraftyBoxes` 1.8.19** (official) | `fedorovdgap-AzuCraftyBoxes` 1.8.18 (fork) |

Three consequences that change statements made above, and one that vindicates one:

1. **"Zero per-world version drift" is no longer true.** It held at snapshot time; the fleet
   now has drift on exactly these five packages, Ulfsland-ahead in every case. That is
   intended — the other four worlds are deliberately stopped — but a future sweep must not
   read it as decay. The harness asserts the drift set explicitly rather than asserting zero.
2. **Four of my 12 rows are now closed on the live world**: Jotunn 2.30.1, Basements 2.0.0,
   OdinsKingdom 1.5.9, Wisdom 1.0.6 — all at newest on Ulfsland. The fifth change is a
   package *swap*, not a version bump, so it closes the P4 craft-from-chests recommendation
   instead. **Eight rows remain outstanding on the live world**, and all twelve remain
   outstanding on the four stopped worlds.
   **And the deploy unblocked the *Jotunn* floor on two of those eight** —
   `MidnightMods-ImpactfulSkills 0.17.1` (declares `ValheimModding-Jotunn-2.30.1` outright)
   and `Ross-RossItemDrawers 1.0.10` (declares 2.30.0, but its changelog says *"Built and
   tested against Jotunn 2.30.1, which the dependency now asks for"*, so 2.30.1 is the real
   floor). Rows 6 and 7 of the 1a-bis ordering no longer have to wait on row 2.

   **But one floor is cleared, not both, and the distinction matters.**
   `RossItemDrawers 1.0.10` also declares `denikson-BepInExPack_Valheim-5.4.2350`, and by
   `InventoryMod`'s md5 measurement the **running** pack is **5.4.2333** — a pin no deploy
   can move, because the pack that is loaded is not the pack that is staged. So that row is
   Jotunn-clear and BepInEx-**un**clear. `InventoryMod` flagged this against their own credit
   rather than let it stand, and it is the same check that had them hold `PlanBuild 0.19.0`
   and `AzuEPI 2.4.14` while taking `Basements 2.0.0` and `Wisdom 1.0.6` as **stated
   acceptances rather than passed checks** — which is the honest label for those two and is
   how they should be read in 1a-bis as well.
3. **The deploy temporarily widened the client/server gap it was meant to narrow.** At
   snapshot the admin client profile was in perfect lockstep with the server tree (0
   mismatches). Measured now, before the client republish lands:

   | Client profile | Behind the live server tree |
   | --- | --- |
   | `ulfsland-vr-flat-admin` | **5** — was 0 |
   | `ulfsland-non-vr` | **37** — was 33 |
   | `ulfsland-vr-flat` | **37** — was 33 |

   The admin profile's five are exactly the five deployed packages. This is expected and
   transient, but it is the state a player would join into right now, and it makes the
   client catch-up publish in 1f more urgent than the survey implied, not less.
4. **The `scope` fix held.** `Azumatt-AzuExtendedPlayerInventory` and `Azumatt-AAABuildMenu`
   are confirmed **absent from all five server trees** — which is the correct placement for
   both, and for AzuEPI it is the difference between a working server and the
   `BadImageFormatException` cascade described in 1h-bis. The harness now asserts that
   absence permanently, so a future deploy cannot quietly put either back on a server.

Two counts shifted mechanically and are recorded so they are not read as errors:
installed-packages-carrying-the-deprecation-flag went **23 -> 24** (the official
`Azumatt-AzuCraftyBoxes` is flagged under the same blanket author flag dissected in 1g, and
the fork it replaced was flagged too), and the Ulfsland plugin-dir count is unchanged at
**101** because the swap removed one directory and added one.

Nothing in Steps 2 or 3 is affected: P3's candidates were never installed, P5's verdict is a
property of the `ServersideQoL` package rather than of the fleet, and all four P6 items were
resolved against packages the deploy did not touch.

**One live defect the addendum surfaced, flagged to `InventoryMod` and not yet closed.** The
AzuEPI scope fix described in 1h-bis landed on **one profile of four**. Reading `scope`
directly out of each `profile-manifest.json`:

| Profile | AzuEPI entry | manifest mtime |
| --- | --- | --- |
| `ulfsland-dn` | **removed entirely**; this file does carry `client-only` entries | 22:41:17 |
| `ulfsland-admin` | `2.4.8`, **`scope: shared`** | 22:11:05 |
| `ulfsland-flat` | `2.4.8`, **`scope: shared`** | 22:11:03 |
| `ulfsland-vr` | `2.4.8`, **`scope: shared`** | 22:11:07 |

`ulfsland-flat`'s entire scope vocabulary is `['shared']` — it has no `client-only` entry at
all — so if a scope change was intended there it was never expressed. This is harmless *at
this instant* because AzuEPI is absent from all five server plugin trees, which I verified.
But that is the transient state: by the copy-only mechanism in 1h-bis the **manifest is the
source of truth and the deploy copies from it**, so a `shared` pin re-lands the DLL on the
server on the next deploy of `admin`/`flat`/`vr` — re-triggering the
`BadImageFormatException` cascade that took down `ComfortTweaks`, `Herbalist` and
`ValheimRcon`. A clean world tree is not the fix; the manifest is. `scope: "client-only"` on
the three is preferable to removal, since removal also takes the package away from that
profile's clients, which was the point of the ticket.

Related, same read: the AzuCraftyBoxes swap is complete on all four `ulfsland-*` profiles
(official `1.8.19`, `shared`), but the deprecated fork `fedorovdgap-AzuCraftyBoxes 1.8.18`
is **still pinned in `admin`, `flat` and `vr`** — the three non-`ulfsland-` profiles — so
those will keep shipping the single-release fork that lacks the mead-base deletion fix.
`admin` is the linked server profile for Doggerland, Hrafnheim, Storgard and Vangard, so one
edit would cover all four.

> **DEFERRED BY OPERATOR DIRECTIVE — do not act on this.** The operator has since fenced all
> work to Ulfsland only: `admin`, `flat` and `vr` are not to be edited (an `admin` edit
> changes four worlds at once), and this row is explicitly out of scope and stays untouched.
> It is recorded here as a **known, measured, accepted defect on four stopped worlds**, not as
> a pending action. The same fence applies to the three `shared`-scope AzuEPI pins discussed
> in 1i: they are to be left exactly as they are, because they cannot reach any server tree
> under the current linkage, both scopes reach clients, and "fixing" them would register a
> pending client-release cutover that blocks the next `deploy --apply`.

**A third instance of tonight's defect class, in my own hands, caught before it reached this
page.** Chasing the above I wrote a throwaway mapping from bare package *name* to plugin
directory across the whole 11,400-package index. It confidently returned `ReefTeam-Jotunn`,
`Timoksa-Jotunn`, `sbtoonz-Basements`, `TheBestL-OdinsKingdom` and thirty more as
"installed and behind" — all of them re-uploader namespaces colliding on a package name,
none of them installed. The real harness keys on the fleet's own identifier set and was
unaffected, and nothing from the throwaway reached this report. Recorded because the shape is
now familiar: a plausible, confident, wrong list from an instrument that never raised. Any
mapping across that index must key on the full `owner-name` identifier, never the bare name.

---

### Step 2 — newly-updated mods that address P3 and P4

#### P3 — the hammer grid overflow

The brief's measurement stands: stock 1.0.12 with zero mods registers 90 pieces in
`BuildingWorkbench` and 91 available at full recipe knowledge, against a 15x6 = 90-cell grid.

First, a factual correction to a related line in `## Piece access` above, agreed with
PieceAccess over hub: it is **not** true that no build-menu mod exists anywhere on the fleet.
MEASURED on disk — `Azumatt-SearchableBuildMenu 1.0.4` with `SearchableBuildMenu.dll` at
`/mnt/vps-sync/profiles/Hrafnheim--hrafnheim-flat--flat/active/BepInEx/plugins/Azumatt-SearchableBuildMenu/`
and the same in `Vangard-Redesign--vangard-redesign-flat--flat`. That is 2 of 13 client
profiles, **both on deliberately-stopped worlds**; zero on all 5 server trees and zero on all
3 Ulfsland (live) client profiles. PieceAccess has since confirmed this independently and
traced their own false zero to a `find -maxdepth 4` that could not reach the depth-5
`active/BepInEx/plugins/` path; they also read the version from the DLL's own `Assembly`
metadata row (`1.0.4.0`). It is a **pre-1.0 build** — newest published is 1.0.4 from
2024-12-09. `[INFERENCE]` that it is therefore inert on 1.0: I did not boot it and did not
read the patch that would prove it, so that reads as likely, not measured. The operative
conclusion needs neither inference and is unchanged: **no build-menu mod on the live world or
any server tree.**

Candidates, newest-first, all dates and dependants from the index pull:

| Package | Newest / date | Dependants / downloads | Deps | What it closes | What it leaves open |
| --- | --- | --- | --- | --- | --- |
| **`Azumatt-AAABuildMenu`** | **1.0.4**, 2026-09-14 | 8 / 4,856 (flagged, see 1g) | **none** | grid overflow via resizable window + icon size + a `Can Build` tab; modded-category reachability; broken-recipe crashes | opinionated UI change; author-retired catalogue |
| `JamesJonesTV-Legacy_Build_Menu` | 1.0.0, 2026-09-11 | 1 / 645, **not flagged** | BepInEx 5.4.2350 | overflow via the pre-1.0 scrolling menu + a search bar; explicitly "Works with PieceManager and Jotunn pieces" | single release, 645 downloads, no changelog at all |
| `jg224-CraftIndex` | 0.5.7, 2026-09-12 | 2 / 2,912, not flagged | BepInEx 5.4.2333, `jg224-ModCore-0.5.0` | literally widens the grid — see quote below | drags in a second library; reorganises every tab to the author's own layout |
| `VerdantsAscent-FiresAdminPrefabs` | 1.1.59, 2026-09-16 | 1 / 135 | — | "every vanilla prefab in the hammer with tabs + search" | an admin tool, not a player-facing fix |
| ~~`Easy_Develope-Pageable_Build_Hud`~~ | 1.0.2, **2021-10-05**, deprecated | 137 / 39,665 | — | — | **dead**: the obvious "pagination" answer is five years stale |
| ~~`Captain-BuildExpansion_Renewed`~~ / ~~`ComfyMods-ComfyBuildExpansion`~~ / ~~`MixoneFinallyHere-BuildExpansion`~~ | 2021-2023, all deprecated | 25 / 99 / 45 | — | — | **dead**; the whole "expand the Hammer build tables" family is abandoned |

`AAABuildMenu 1.0.4`, quoted in full because every clause matters here:

> * Valheim 1.0 support. New Can Build, Most Used and Pinned tabs, resizable/movable window,
>   icon size, sort button, material counts and recipe pinning in the 1.0 build menu.
> * Optional classic (pre-1.0) build menu with all the old features: `Use Classic Build Menu`.
> * Custom Jotunn/PieceManager categories get their own tag in the 1.0 menu.
> * A modded piece with a broken recipe no longer breaks the build menu.
> * Classic menu: picking a tab clears the search, tab counts show real category sizes, Deep
>   North gets a proper tab name.
> * Reset Size & Position also resets the 1.0 menu. Stays out of PieceManager's way.

`jg224-CraftIndex`, quoted — the only candidate that changes the grid's dimensions:

> ## 0.5.6 - Added a usable fourteenth column to the organized build menu.
> ## 0.5.7 - Added a one-tile gap between Vanilla and Plant Everything sections.
> - Reorganized Hammer tabs and pieces to follow the edited workbook.
> - Kept gaps for Wood, Darkwood, Ashwood, Timberwood, and Stone; other groups compact as
>   pieces unlock.

**Recommendation for P3: `Azumatt-AAABuildMenu 1.0.4`.** It is the only candidate that closes
the overflow *and* the modded-category reachability *and* the broken-recipe crash in one
package, it declares **zero dependencies** so it adds no new coupling, and it explicitly
"Stays out of PieceManager's way" — which matters given the four dead-field carriers in P2.
The deprecation flag is the blanket author flag dissected in 1g, not abandonment, and the
fleet already runs an Azumatt package published the same day. **If the operator will not
accept a flagged package on principle, the fallback is `JamesJonesTV-Legacy_Build_Menu 1.0.0`**
— unflagged, same author as the Ravenwood pieces, and the mod the Ravenwood README itself
names as a prerequisite — accepting that it has one release and 645 downloads behind it.
Do **not** reach for `Pageable_Build_Hud` or any `BuildExpansion`; all are deprecated and
pre-Ashlands.

**Independent corroboration, measured at source level by `InventoryMod` after the deploy.**
I had argued for AAABuildMenu partly on its claim to cover "Custom Jotunn/PieceManager
categories". That claim is now backed by a second instrument and by a matching negative:
reading Jotunn 2.30.1's own source, its category fix **gates on Jotunn's `PieceCategories`
registry**, so it reaches only pieces registered through Jotunn's API. The bundled-
`PieceManager` mods are therefore genuinely **not** covered — `OdinsKingdom` and
`RavenwoodRestorations` carry **zero Jotunn `AssemblyRef`** among 17 references each.

This matters for the recommendation in both directions. It confirms Jotunn 2.30.1 alone does
**not** make every modded piece reachable, so the P3 row is not closed by the deploy — and
`RavenwoodRestorations` alone has 113 `BuildPiece` registrations behind the availability pass
that P2 shows is still aborting on three unfixed carriers. And it is the strongest argument
for the named recommendation: **AAABuildMenu is the only candidate that spans both
registration families**, which is exactly the gap Jotunn cannot close by design. On that
evidence `InventoryMod` deployed it to `flat`/`admin` only and left the exclusion intact on
`ulfsland-vr` and `ulfsland-dn` — a partial, evidence-based reversal rather than a blanket
one, which is the right shape for a recommendation carrying an author-retirement flag.

#### P4 — craft-from-chests, animal autofeed, chest tier

**First, the installed reality, because the brief has it slightly wrong.** What is installed
on all 5 server trees is not Azumatt's mod but a fork: `fedorovdgap-AzuCraftyBoxes 1.8.18`.
From its own on-disk `manifest.json`:

> "description": "Unofficial rebuild of Azumatt's AzuCraftyBoxes for the Valheim 1.0 update.
> Fixes the ZRoutedRpc.Everybody startup crash. Mod source unchanged, built from upstream
> master. **All credit to Azumatt; switch back to the official release.**"

and its changelog:

> ## 1.8.18
> - Rebuilt from upstream `master` (commit `43d01e8`) for the Valheim 1.0 update.
> - Fixes `MissingFieldException` on `ZRoutedRpc.Everybody`, caused by an outdated bundled
>   ServerSync.
> - No changes to mod source code or behaviour.

The fork was published **2026-09-10**, is itself flagged `is_deprecated=true`, has 5
dependants, and has exactly one version ever.

**"Deprecated upstream with no successor version" is no longer true.** MEASURED:
`Azumatt-AzuCraftyBoxes` newest is **1.8.19, published 2026-09-14T17:02:05Z** — four days
*after* the fork. Its changelog rows:

> | 1.8.19 | - Fix mead bases (and other fermenter items) being deleted when the fermenter
> pulled them from nearby containers. |
> | 1.8.18 | - Deeper Fix for 1.0 (recomplile) |
> | 1.8.17 | - Fix for 1.0 (recomplile) |

So Azumatt shipped their *own* 1.0 fix (1.8.17, 1.8.18) and then an **item-deletion bugfix**
the fleet's fork does not have. I verified the two are equivalent on the 1.0 issue rather than
assuming it — `dnfile` `MemberRef` walk, installed fork vs downloaded 1.8.19:

| Build | `ZRoutedRpc::Everybody` | `ZRoutedRpc::GetServerPeerID` |
| --- | --- | --- |
| `fedorovdgap` 1.8.18 installed | absent | present |
| **`Azumatt` 1.8.19 candidate** | absent | present |

Identical. The official 1.8.19 is a strict superset of the fork: same 1.0 routed-RPC shape,
plus a fix for silently deleting players' mead bases.

**Recommendation for craft-from-chests: `Azumatt-AzuCraftyBoxes 1.8.19`**, replacing
`fedorovdgap-AzuCraftyBoxes 1.8.18`. 1,889 dependants, 1,559,280 downloads, and the fork's own
author tells you to do it. The blanket flag is not a reason to stay on a single-release fork
that is *also* flagged and is missing a data-loss fix. Maintained alternatives if the operator
wants off the Azumatt lineage entirely, all active and all unflagged:
`ZenDragon-ZenConstruction 1.4.0` (2026-09-13, 61 dependants, 45,584 dl — and the fleet
already runs six ZenDragon mods plus `Zen_ModLib`, so it is the lowest-friction switch),
`Chazman-RunicCrafting 1.1.1` (2026-09-15, 7 dependants), `UnderHeiz-CraftFromContainers
4.0.20` (2026-09-13, 4 dependants).

**Now the two features the brief measured as genuinely absent — and why.** The cause is not
configuration. `ServersideQoL` **v2.0.0 split its features into separate packages**:

> ### v2.0.0
> - Major rewrite, split up features into [separate mods]

The fleet installs only the core plus one feature package (`ServersideQoL` 2.0.13 +
`ServersideQoL_AutoStore` 2.0.11 — confirmed by directory listing on all 5 server trees).
`FeedFromContainers` *does* still appear in the core DLL, but only as a registry key: the
UTF-16LE strings around it are `TameableRegistryProcessor.cs`, `AutoPickup`,
`AutoPickupMaxRange`, `FeedFromContainers`, `FeedFromContainersMaxRange`, `Shared config`,
` already set`. And the option's real owner is named in a changelog:

> ### v2.0.11 (`ServersideQoL_AutoProcess`)
> - Moved `FeedFromContainersMaxRange` config option from ContainerSigns to AutoProcess

So the correct statement is **not** "shipped but unconfigured" — it is "the owning package is
not installed". Confirmed on disk: the whole installed core config surface is 31 lines of
`[General]` (`Enabled`, `DiagnosticLogs`, `UnifiedConfig`, `ConfigPerWorld`, `FarMessageRange`)
plus a 15-line advanced YAML with only `ProcessingDelays`, `Containers`, `Players`. There is no
feed option to configure because there is no feed package.

The family is alive — **every** `ServersideQoL_*` feature package was published 2026-09-16:

| Package | Newest / date | Quoted capability |
| --- | --- | --- |
| `ArgusMagnus-ServersideQoL_AutoProcess` | 2.0.11, 2026-09-16 | "Automatically feeds processing stations such as smelters, kilns, windmills, shield generators, etc. from nearby containers" |
| `ArgusMagnus-ServersideQoL_ContainerSizes` | 2.0.11, 2026-09-16 | "Make all container inventory sizes configurable and optionally let the grow infinitely if the contain only one item type" |
| `ArgusMagnus-ServersideQoL_TameAssist` | 2.0.11, 2026-09-16 | "Configure various aspects around tameable creatures. Allow all tames to follow you, even through portals and into dungeons." |

Each declares exactly `ArgusMagnus-ServersideQoL-2.0.11`; installed core is 2.0.13, so the
floor is met and nothing else moves.

Dedicated candidates for the two gaps:

| Gap | Package | Newest / date | Dependants / downloads | Flag |
| --- | --- | --- | --- | --- |
| animal autofeed | **`Vapok-AutoFeedRedux`** | **2.0.5**, 2026-09-17 | 75 / 26,096 | not flagged |
| animal autofeed | `Azumatt-PetPantry` | 1.0.6, 2026-09-15 | 397 / 218,022 | flagged (blanket) |
| animal autofeed | `Narolith-AutoFeed` | 1.0.3, **2026-05-29** | 33 / 50,741 | not flagged, but pre-1.0 |
| chest tier | `ArgusMagnus-ServersideQoL_ContainerSizes` | 2.0.11, 2026-09-16 | 2 / 3,057 | not flagged |
| chest tier | `KuliTeam-ChestUpgradeMod` | 1.1.1, **created 2026-09-16** | 0 / 241 | not flagged, one day old |
| both + crafting | `jg224-ChestFlow` | 0.5.7, 2026-09-12 | 2 / 7,209 | not flagged |

`Vapok-AutoFeedRedux`, quoted:

> ### 2.0.0 - Valheim 1.0 Update & Direct Container Feeding
> * Updated for Valheim 1.0.
> * Overhauled animal feeding navigation: tames now pathfind and walk directly to food
>   containers when feeding.
> * Food is now consumed directly from the container upon arrival.
> * Updated default configuration values: * Auto Feeder enabled by default. * Feed Range
>   increased to 30 meters. * Move Proximity updated to 1 meter.

**Recommendation for animal autofeed: `Vapok-AutoFeedRedux 2.0.5`.** Active (published
2026-09-17), unflagged, 75 dependants, and **Vapok is already a trusted author on this fleet**
— `Vapok-AdventureBackpacks` is installed on all 5 server trees. It declares
`ValheimModding-Jotunn-2.30.0`, which is exactly what is installed, so it adds no coupling.
Two caveats, both from the changelog: it carries the same `Anonymous Telemetry ... Defaults to
enabled with auto-opt-in on launch` as AdventureBackpacks, and `Auto Feeder enabled by default`
with a 30 m range — set both deliberately rather than inheriting them.

**Recommendation for chest tier: `ArgusMagnus-ServersideQoL_ContainerSizes 2.0.11`.** It is
server-only (so no client redeploy and no version-parity risk), it sits on a core already
installed and already at a satisfying version, and it is the only candidate here with a
maintained home rather than a one-day-old package. It grants configurable capacity rather than
literal Wood -> Reinforced -> Black Metal tier upgrades; if the operator specifically wants the
in-place tier upgrade interaction, `KuliTeam-ChestUpgradeMod 1.1.1` is the only mod that does
it (*"Upgrade chests in place with a keypress: Wood -> Reinforced -> Black Metal, using
configurable material costs"*) and it is one day old with zero dependants — hold it.

What each leaves open: `ContainerSizes` does not do autofeed; `AutoFeedRedux` does not do
capacity; `AzuCraftyBoxes 1.8.19` does neither. `jg224-ChestFlow 0.5.7` claims all three
("Shared multiplayer chests, automatic animal feeding, crafting, restocking, upgrades, storage
rules, stacking, and sorting") but it was created 2026-09-09, has 2 dependants, and would drag
in `jg224-ModCore` — noted as the one-package option, not recommended.

#### Piece registration / build-menu categorisation

The one structural answer is `ValheimModding-Jotunn 2.30.1` (row 1b above). Everything else in
this space is either an author-level piece mod or a menu replacement. Analysis of what that
means for piece reachability is in `## Piece access`.

---

### Step 3 — P5 and P6

#### P5 — `ServersideQoL` is server-only. Definitive.

Three independent instruments, all agreeing.

**1. Package metadata**, from the installed `manifest.json` on the server tree:

> "description": "**Serverside-only QoL mod**, compatible with vanilla/console (e.g. XBox, PS,
> Switch) clients. ..."

**2. The shipped README**, on disk in the same directory:

> This mod adds some QoL features as a **serverside-only** mod.
>
> It's designed and tested for **dedicated servers** with **vanilla/console clients** (e.g.
> XBox/PS/Switch users).

**3. The IL itself.** The literal `Mod should only be installed on the host` exists in
`ServersideQoL.dll` as a UTF-16LE user string at `#US` heap offset 9730 (an ASCII grep finds
nothing). Decoding the `ldstr` site and resolving its tokens with `dnfile`:

```
call     ZNet::get_instance
brtrue.s -> retry loop, newobj UnityEngine.WaitForSeconds(0.2f)   ; wait for ZNet
call     ZNet::get_instance
callvirt ZNet::IsServer
brtrue.s +0x51                                                    ; IsServer -> skip entirely
call     get_Logger
ldstr    "Mod should only be installed on the host"
callvirt LogWarning
ldc.r4   5.0 -> newobj UnityEngine.WaitForSeconds::.ctor          ; then wait 5s and loop
```

The `5.0f` operand (`0000a040`) is the measured origin of the operator's "repeating every ~5
seconds". On a host the entire block is branched over. **Verdict: server-only, unambiguously.
A client instance does nothing except warn and sleep.**

**Blast radius, measured on disk**: `ArgusMagnus-ServersideQoL` and
`ArgusMagnus-ServersideQoL_AutoStore` are present in `active/BepInEx/plugins/` on **all 3
Ulfsland client profiles**, and the preloader `ServersideQoL.Patchers.dll` in
`active/BepInEx/patchers/` on all 3 as well. Zero on the 10 stopped-world client profiles
(those carry `mvp-Serverside_Simulations` instead).

**Recommendation, corrected against the real manifest schema.** My first wording said
"change both packages from `scope: shared` to server-only", which named a mechanism this
schema does not have. MEASURED: both `ArgusMagnus-ServersideQoL` and
`ArgusMagnus-ServersideQoL_AutoStore` sit in the `packages` list with `scope: "shared"`, and
the list that would express the opposite — **`manual_server_packages` — is empty in all
seven profiles**, so making these server-only would be its first use on this fleet. That is
a change to the deploy vocabulary, not a one-field edit, and it should be costed as one.
`client_only_packages` is the established and well-populated list (9-12 entries per
profile); there is no symmetric precedent for the server side.

The verdict itself is unaffected: the mod is server-only, the client copy provably does
nothing but warn and sleep, and the preloader `ServersideQoL.Patchers.dll` must move with
the plugins. What changes is the implementation route — establish
`manual_server_packages`, or exclude both from the client editions by whatever mechanism the
publish path already honours. I am not specifying which without reading that path, and I
have not read it.

#### P6 — status and recommendation for each item

**(1) `warpalicious-More_World_Locations_AIO` 5.1.1 — already deployed. The hold is stale.**
MEASURED: 5.1.1 is the installed `version_number` beside
`More_World_Locations_AIO.dll` in **all five** server plugin trees, and is the pin in **all
seven** profile manifests. 5.1.1 is also Thunderstore newest (published 2026-09-15T18:39:49Z).
The only thing still behind is client deployment, and MWL is present on only **12 of the 13**
client profiles: 5.1.1 on `ulfsland-vr-flat-admin`, 5.1.0 on the two non-admin Ulfsland
profiles, 5.0.8 on **9** stopped-world profiles, and absent entirely from
`Vangard-Redesign--vangard-redesign-flat`. The changelog confirms the brief's read exactly:

> | 5.1.1 | - Added five new Swamp locations: `MWL_SwampTempleSmall1`, `MWL_SwampTemple2`,
> `MWL_SwampShrine1`, `MWL_SwampSanctuary1`, and `MWL_SwampComplex1`. |
> | | - To add the new locations to an existing world, install Upgrade World and run:
> `locations_add MWL_SwampTempleSmall1,MWL_SwampTemple2,MWL_SwampShrine1,MWL_SwampSanctuary1,MWL_SwampComplex1 start` |
> | | - Fixed shipping-port discovery persistence when ValheimEnforcer saves player custom data
> during logout. |

Purely additive: the five new locations reach an existing world **only** via an explicit
`locations_add`. The 15 built sites on its houses are safe — 5.1.0's line
`- Removed stale MWL_StoneOutlook1 location config and creature mapping entries` was the
removal, and that already shipped. **Recommendation: close the hold as satisfied server-side,
and bring the two non-admin Ulfsland client profiles from 5.1.0 to 5.1.1 as part of the single
client catch-up deploy in 1f. Do not run `locations_add` unless the five new Swamp locations are
actually wanted.**

**(2) `95Shade-CarryWeightSkill` — do not remove. There is an upstream fix, two days old.**
This is the sharpest correction in my scope. The brief records it as "broken (PatchAll IL
failure, no upstream fix) and slated for removal". MEASURED: installed is **1.0.1**
(published 2026-04-06) on 10 client profiles; newest is **1.0.2, published
2026-09-15T00:28:24Z**, not deprecated, 5 dependants. There is no changelog markdown, but the
package description states the fix in terms that match the observed failure precisely:

> Adds a Carry Weight skill: train while sprinting or swimming heavy; +3 max weight per level.
> **Compatible with Valheim Sept 2026 (Skills.RaiseSkill / MessageHud fix).**

A `PatchAll` IL failure is exactly what a changed `Skills.RaiseSkill` / `MessageHud` signature
produces. **Recommendation: try 1.0.2 before removing anything.** Note it is installed on
client profiles only — never on any server tree — so this is a client-side change with no world
risk. Declared limit: I have *not* booted 1.0.2, so "the fix works" is INFERRED from the
description; the MEASURED part is that a fix exists and the brief's premise that none does is
wrong. If 1.0.2 still fails, the drop-in replacement is `Searica-SkilledCarryWeight 1.5.0`
(2026-09-15, 109 dependants, 91,817 downloads, declares Jotunn 2.30.0) — **already installed at
1.4.1 on two client profiles**, so the fleet has it in hand.

**(3) `Infinity_Hammer`'s `World_Edit_Commands` dependency — already satisfied. The premise is
wrong.** MEASURED across all five server trees: `World_Edit_Commands` **1.77.0** with
`WorldEditCommands.dll` (304,128 bytes) present in every one of them. Also present on all 13
client profiles (1.77.0 on the three Ulfsland, 1.72.0 on the ten stopped-world) and pinned in 3
profile manifests. `Infinity_Hammer 1.84.0`'s declared floors are
`JereKuusela-World_Edit_Commands-1.76.0` and `JereKuusela-Server_devcommands-1.112.0`;
installed are **1.77.0** and **1.113.0**. Both floors exceeded. **There is no unsatisfied
dependency, and nothing to install.** PieceAccess reached the same conclusion independently
from the Ulfsland tree alone.

On the underlying question — *would adding World Edit Commands add capability we want?* — it is
already here, so the question resolves to whether the fleet is at the right version, and it is
(1.77.0 is newest, published 2026-09-13T18:24:07Z, 437 dependants). For the record, the three
JereKuusela tools this project's tooling drives are all installed and all at newest:

| Package | Installed (all 5 trees) | Newest | Published | Dependants |
| --- | --- | --- | --- | --- |
| `JereKuusela-Server_devcommands` | 1.113.0 | 1.113.0 | 2026-09-12 | 1437 |
| `JereKuusela-World_Edit_Commands` | 1.77.0 | 1.77.0 | 2026-09-13 | 437 |
| `JereKuusela-Upgrade_World` | 1.82.0 | 1.82.0 | 2026-09-13 | 611 |
| `JereKuusela-Infinity_Hammer` | 1.84.0 | 1.84.0 | 2026-09-15 | 347 |

Worth knowing, since it is capability the fleet already owns and may not be using —
`Infinity_Hammer 1.80` added:

> - Adds support for the permission system from Server Devcommands mod. This allows granularly
>   granting access to specific commands and features.

and `World_Edit_Commands 1.77` fixed two dedicated-server-specific defects:

> - Fixes command `spawn_location` causing error on dedicated server. Thanks Synistro!
> - Fixes parameter `rot` being ingored on dedicated server. Thanks Synistro!

Both matter for a fleet whose tooling drives `Upgrade_World` and `Server_devcommands`
constantly, and both are already in place. The only genuine gap in that family is the one the
MWL changelog names: `locations_add` needs `Upgrade_World`, which is installed at newest.

---

### Scope

No shrink. All four assigned areas plus the full delta were completed as specified. Extra work
beyond the brief, recorded so it is auditable: the delta covers all five server trees (not just
Ulfsland), all 7 profile manifests and all 13 client profiles; three upstream claims
(Basements 2.0.0's PieceManager rebuild, AzuCraftyBoxes 1.8.19 vs the fork, Zen_ModLib 1.14.5's
field shape) were verified by IL reference comparison rather than taken from changelogs; and the
orphaned-config and scope-inversion findings in 1h were not requested.

Three claims in this section are explicitly INFERRED, not measured, and are labelled as such
inline: that `95Shade-CarryWeightSkill 1.0.2` actually fixes the PatchAll failure (from its
description; not booted), whether `Zen_ModLib 1.14.5`'s game-version gate still accepts
1.0.12 (needs a boot), and that the stopped-world `SearchableBuildMenu 1.0.4` is inert on 1.0.
Everything else names its instrument.

#### Instrument failure found and corrected during this survey

`HarbourCity` measured that the Python eval kernel is **shared between task subagents**: a
global can be silently rebound by another agent's cell, so any number computed across two
cells may have been computed against someone else's variables. Every load-bearing number in
this section was therefore **re-derived from disk in one self-contained script invocation**
(`/tmp/moddelta/rederive.py`, 100 assertions covering inventory shape, per-world drift, the
12-row behind-table, manifest-vs-tree drift, the Ulfsland client mismatch sets, every P3/P4
candidate row, every dependency floor, and all four P6 items), each assertion comparing the
freshly computed value against the value published above.

That pass found **one real error, now corrected**: `More_World_Locations_AIO 5.0.8` is on
**9** stopped-world client profiles, not 10 — MWL is absent from
`Vangard-Redesign--vangard-redesign-flat` entirely, so it is installed on 12 of 13 client
profiles rather than all 13. Fixed in 1f and in P6 item (1). Every other published number
re-derived identically. It also surfaced two bugs in the *harness itself* (wrong path-segment
index when deriving a profile name from a glob result), which is worth recording as the same
defect class: the first run of a checking instrument reported `['BepInEx','BepInEx','BepInEx']`
as a list of profile names and would have been read as a failure of the data rather than of
the checker.

**The limit of this re-derivation, stated plainly.** `rederive.py` compares freshly computed
values against the values published above. That catches a number that *changed* — which is
how the MWL error surfaced — but a re-derivation that only checks itself against itself will
confirm a wrong answer twice. The two defences that actually worked tonight were neither
internal: a value absurd enough to eyeball (`['BepInEx','BepInEx','BepInEx']` as a list of
profile names), and a peer with a wider sweep. Where I had an external anchor I used it, and
it is why several numbers here are quoted from two unrelated instruments — the Basements
PieceManager rebuild from both a changelog line and an IL reference walk; the
`AzuCraftyBoxes` fork-vs-official equivalence from both changelogs and identical
`ZRoutedRpc` reference sets; the `ServersideQoL` 5-second interval from both the operator's
observed log and the `ldc.r4 5.0` operand feeding `WaitForSeconds`. Where I had no external
anchor I said so: the three `[INFERENCE]` claims above, and the `Zen_ModLib` version gate.
`PieceAccess` took the same guard further and it found a third bug in their own IL decoder
(an incomplete opcode table silently desynchronising on `switch`), caught only by a control
anchored outside the instrument — their reachable-piece count of 29 independently matching
the installed README's own itemised list of 29. That is the shape worth copying: assert
against a known-good value from a different source, not against your own previous run.

Artefacts, read-only, under `/tmp/moddelta/`: **`rederive.py`** (the single-invocation
re-derivation harness — run it to reproduce every number in this section),
`inventory.json` (fleet inventory), `slim.json` (11,400-package index),
`changelogs.json` (33 changelogs), `dl/` (4 candidate packages), `compare_refs.py`,
`sqol_guard.py`, `verify_dlls.py`, `fetch_ts.py`.
