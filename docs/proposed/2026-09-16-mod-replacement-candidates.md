# Mod replacement candidates — what can be retired, repaired, or swapped

Assessed 2026-09-16 against the deployed Valheim **1.0.12** server assemblies
(`Ulfsland/data/server/valheim_server_Data/Managed/`), BepInEx **5.4.23.3**
(`BepInExPack_Valheim` 5.4.2350), the **120 plugin DLLs** deployed under Ulfsland, and the
**113 packages** named across the seven profile manifests.

**Nothing was installed, deployed, restarted, or written outside this file.** No RCON, no
`valheim_mods.py ... --apply`, no container touched. Every measurement is a read of a file
that was already on disk, or a read of Thunderstore's public API.

Every claim is marked **MEASURED** (I ran the instrument named) or **INFERRED** (reasoned
from something measured). Where a claim cannot be settled read-only it is marked
**BLOCKER** and the thing that would settle it is named.

---

## Instruments

Three Cecil tools were built for this pass in `/tmp/refscan` (throwaway, not committed).
`monodis` was insufficient: `--memberref` prints member **names without signatures**, and a
full disassembly aborts on both `assembly_valheim.dll` and the L4zerShark patchers for want
of resolvable dependencies. Signatures are the whole question here, so:

| Tool | What it answers |
|---|---|
| `RefScan.cs` | every `MemberReference` a plugin emits, with its **full signature** (return type included) |
| `ResolveScan.cs` | for each reference into the game assemblies, whether an **exact-signature definition exists**. No match ⇒ that callsite throws `MissingMethod/FieldException` unless a preloader injects it. Generic methods are matched on `declaringType::name/argc/genericArity`, because a definition spells its generic parameters `T` where a reference spells them `!!0` |
| `DefScan.cs` | game-assembly definitions, `[HarmonyPatch]` attribute targets, and raw IL of a named method |

Non-code instruments: the live BepInEx logs under `<world>/data/bepinex/BepInEx/`, the live
`config_merged` config files, the seven `profile-manifest.json` files, and Thunderstore's
`v1/package` index for `valheim` (fetched 2026-09-16, 11,313 packages, 166 MB) plus the
`experimental/package` endpoints for per-package readme and changelog.

**One limitation, stated up front.** `ResolveScan` sees direct IL callsites. It cannot see
reflection-by-name (`AccessTools.Method(typeof(ZDO), "GetSector")`). That is the right
instrument for the question *"who needs a bridge"* — a by-name lookup does not need a bridge,
it needs the bridge to be **absent** or the resolution hook to steer it — but it is the wrong
instrument for *"who would be broken by adding an overload"*, and no claim below rests on it
for that.

---

## Corrections to inherited context, measured

These were handed to me as fact and are wrong or imprecise. They matter because two of them
change a recommendation.

| Inherited claim | Measured reality | Instrument |
|---|---|---|
| "`95Shade-CarryWeightSkill` is BROKEN" | **Partly.** Its skill registration and its three manual carry-weight patches run and succeed; only the trailing `PatchAll()` throws. IL offset `0x0068` in `CarrySkillPlugin::Awake` is the `PatchAll` call, and the logged frame is `Awake () [0x00068]` — an exact match | `DefScan il` + live log line 22 |
| "`CLLCompatibility` 4.6.4 was installed today" | Conflates two packages. `L4zerShark_Team-CLLCCompatibility` is **1.0.1**; `4.6.4` is `Smoothbrain-CreatureLevelAndLootControl` | 7 × `profile-manifest.json` |
| "`tools/everybodyshim` has THREE bridges and CLLC is the last consumer of the last one" | It has **four** rows across four tables (one table is empty). **Three of the four rows have zero consumers anywhere in the fleet.** CLLC is the sole consumer of the fourth | `RefScan` over 7 profiles + `ResolveScan` |
| "`Valheim10Compatibility` — our local shim" | It is **not ours**. `Wubarrk-Valheim10Compatibility` is a published Thunderstore/Hexium package, 1.4.0 installed, **1.4.1 published today**. Our local shim is `EverybodyShim` | manifests + Thunderstore |
| The shim's own note: "13 deployed mods reference `PieceTable.m_availablePieces`" | **Four do**, and that is the whole dead-build-piece population. The 13 was a name-based `monodis --memberref` count; nine of those thirteen reference the 1.0 field and resolve natively | `ResolveScan` over 120 DLLs |
| The shim's own note: "9 deployed mods reference `ZoneSystem.GetZone`" | Nine reference it **by name**; **zero** reference the `Vector2i` form the shim injects. All nine emit `Vector2s ZoneSystem::GetZone(Vector3)`, which is native | `RefScan` signatures |
| `ServersideQoL` was called a shim dependant | Confirmed **not** a dependant, by resolved signature: it emits `Vector2s ZDO::GetSector()`, the native form. Its only two unresolved references are members **its own patcher injects** (`ZDO::get_ServersideQoLZDO`, `ZNetPeer::get_ServersideQoLPeer`) | `ResolveScan` |

---

## Fleet health: who is actually broken on 1.0

`ResolveScan` over all 120 Ulfsland plugin DLLs: **33 unresolved references in 19 DLLs**, out
of **14,409 references into the game assemblies across 119 readable DLLs** (one is not a
managed assembly — `BepInExPack_Valheim/winhttp.dll`). MEASURED. The other 100 DLLs are clean.

| Unresolved member | DLLs affected | Covered by |
|---|---|---|
| `List<List<Piece>> PieceTable::m_availablePieces` | Basements, OdinUndercroft, OdinsHorsePen, RavenwoodRestorations | **nothing — this is the dead-build-piece bug.** See R3 |
| `Character::Message(…, Sprite)` (5th param appended in 1.0) | BlacksmithingExpanded, ComfortTweaks, CraftyCartsRemake, Foraging, Lumberjacking, Mining, RavenwoodCurrency, Wisdom, CreatureLevelControl | V10C append-only bridge (INFERRED — its README documents the bridge; the `Injected` log line is at `[Info]`, which is filtered in every log on disk) |
| `EffectList::Create(…, 5 args)` (6th `ZDOID` appended) | CraftyCartsRemake, PerfectPlacement, VHModpackFix, CreatureLevelControl | V10C bridge (INFERRED, same reason) |
| `SEMan::AddStatusEffect(int,bool,int,float)` | ComfortTweaks | V10C bridge (INFERRED) |
| `MessageHud::ShowMessage(…, 5 args)` | PerfectPlacement | V10C bridge (INFERRED) |
| `RoutedMethod`4/Method<…>::.ctor`, `ZRpc/RpcMethod`4` | EpicLoot, LongshipUpgrades, ServerCharacters | generic-delegate shapes; **INFERRED** benign — these three mods demonstrably work on this fleet |
| `Vector2i ZDO::GetSector()` | **CreatureLevelControl only** | `EverybodyShim`. See R2 |
| `Vector3 ZoneSystem::GetZonePos(Vector2i)`, `CookingStation::SpawnItem(3 args)`, `Inventory::Changed()`, `Inventory::AddItem(7 args)`, `Color Minimap::noForest` | CreatureLevelControl | `CLLCCompatibility` 1.0.1, staged. See R2 |
| `ItemDrop::OnCreateNew(GameObject)` | RavenwoodRestorations | **nothing identified.** BLOCKER, see R3 |

**No installed package is deprecated on Thunderstore** (`is_deprecated: false` for all 113)
and **all 113 still exist** there. MEASURED from the index.

---

# Ranked recommendations, value over risk

## R1 — REMOVE `95Shade-CarryWeightSkill` 1.0.2. We already run a better carry-weight skill.

**Value: highest. Risk: lowest. This is a deletion, not a swap.**

`MidnightMods-ImpactfulSkills` 0.16.1 — installed on all 7 profiles, `shared` — already ships
a **Hauling** skill that does the same job, and it is **already enabled and tuned on the live
fleet**. MEASURED from the installed DLL's own config keys and from
`config_merged/bepinex/MidnightsFX.ImpactfulSkills.cfg` lines 471–539:

```
[Hauling]
EnableHauling = true                     EnableCarryWeightBonus = true
HaulingMaxWeightBonus = 50               HaulingXPRate = 0.1
EnableHaulingCarryWeightXP = true        HaulingCarryWeightXPMinWeight = 275
HaulingCarryWeightXPRate = 3             HaulingMaxLoadRatio = 1.5
EnableHaulingCartMassReduction = true    HaulingCartMassReduction = 0.8
```

### Axis 1 — feature parity, itemised

| CarryWeightSkill 1.0.2 does | ImpactfulSkills Hauling does |
|---|---|
| a trainable skill that raises max carry weight | yes — `$skill_Hauling`, own icon, `EnableCarryWeightBonus` |
| XP while moving with ≥50 % load | yes — `EnableHaulingCarryWeightXP`, `HaulingCarryWeightXPMinWeight = 275`, `HaulingMaxLoadRatio` |
| heavier loads train faster | yes — `Raising hauling skill from carried weight: {0} = {1} load ratio * {2}` |
| no XP while over-encumbered | INFERRED equivalent via `HaulingMaxLoadRatio = 1.5` |
| **+3/level, up to +300 at level 100** | **`HaulingMaxWeightBonus = 50`** ← the one real gap |
| — | cart mass reduction (`HaulingCartMassReduction = 0.8`) — a feature we gain |
| — | XP from hauling **by cart** as well as by carrying |

**The only thing lost is headroom, and it is a config value, not a missing mod.** If the
operator wants the +300 ceiling, raise `HaulingMaxWeightBonus`. That is a one-line config
change with no new package, no new version gate, and no new DLL.

### Axis 2 — what it takes over

Nothing save-affecting. `Player.GetMaxCarryWeight` is a client-side derived value. MEASURED:
CarrySkill's own patch set is `GetMaxCarryWeight`, `UpdateWalking`, swimming, `Skills.RaiseSkill`,
`Player.Message`, `InventoryGui.{RepairOneItem, OnCraftPressed, OnTabCraftPressed, OnTabUpgradePressed}`,
`Character.Jump` — all UI and client derivation. `ResolveScan`: **0 unresolved references**,
so its failure is not signature drift.

### Axis 3 — maturity, measured

| | CarryWeightSkill | ImpactfulSkills |
|---|---|---|
| version | 1.0.2 | 0.16.1 |
| updated | 2026-09-15 | 2026-09-15 |
| downloads | 4,893 | 147,944 |
| dependants (packages depending on it, counted across the whole index) | 5 | 133 |
| Thunderstore `AI Generated` tag | **yes** | no |
| versions ever published | 2 | — |

### Axis 4 — version coupling

Removing a package removes a handshake; it cannot add one. CarrySkill has no `ServerSync`
reference at all (MEASURED: 0 hits), so it never gated joins. ImpactfulSkills is already
published in all 20 client editions, so nothing republishes for this.

### Axis 5 — performance and load time

**One plugin fewer at chainloader time; no per-frame claim made.** What is MEASURED is a
defect that costs more than any load-time delta: CarrySkill's transpiler on
`InventoryGui.RepairOneItem` fails, and HarmonyX leaves a **failed transpiler registered on
that method**. Wubarrk's 1.4.1 changelog records exactly this mechanism for SearsCatalog:

> "HarmonyX keeps the failed transpiler registered, so any later patch on
> `Player.UpdateBuildGuiInput` by any plugin fails too."

Applied here: `InventoryGui.RepairOneItem` is **poisoned for every other mod on the fleet**
for as long as CarrySkill is installed. Today no other installed mod patches it (MEASURED:
`DefScan harmony` across all 120 DLLs returns only CarrySkill's own two classes), so the
damage is latent — but `RepairOneItem` is exactly the method a repair-QoL mod would target,
and `hoskope-RhythmicRepairs` and `VentureValheim-Venture_Area_Repair` are both in the set.

To measure rather than infer a load-time win: `sighsorry-LoadTimeProfiler` 1.3.2 is already in
three profiles as `client-only`. Its per-plugin timings before and after removal are the number.

### Axis 6 — orphaned prefabs on removal

**None. No prefab is involved.** The mod registers no prefab and no piece; it registers a
Jotunn *skill*. MEASURED from `Awake` IL:

```
000b  call Jotunn.Managers.SkillManager::get_Instance()
0016  ldstr com.shadymods.carryskill.carry_weight_v1     ← SkillConfig.Identifier
0021  ldstr Carry Weight                                 ← SkillConfig.Name
0041  callvirt SkillManager::AddSkill(SkillConfig)
```

The analogue of an orphan here is **character data, not world data**: a Jotunn custom skill is
stored in the character file under a `Skills.SkillType` derived from that identifier. Removing
the mod leaves an unknown `SkillType`, which vanilla drops on load — so **accumulated "Carry
Weight" skill progress is lost**, on the server-side character files (`ServerCharacters`
1.4.17.1). It is not transferable to Hauling: different identifier, different hash.

Three things bound that loss, all MEASURED:
1. The skill has existed at most since 2026-04-06 (package creation date), and the current
   build since 2026-09-15.
2. `SkillConfig.IncreaseStep = 1` (IL offset `0x0037`) — one XP per grant.
3. Players already gain **Hauling** in parallel from the same activity, and have since
   ImpactfulSkills was installed.

**BLOCKER if the operator cares about the exact number**: I did not read any character file, so
I cannot say what level anyone reached. `ServerCharacters` profiles under the world's
`characters` directory would answer it. That is a read the operator may authorise; I did not
make it, because it is player data outside my assignment.

---

## R2 — RETIRE `tools/everybodyshim`. Three of its four bridges are already dead; the fourth is superseded by a package already staged.

**Value: high (deletes a locally-maintained preloader patcher from five worlds and 20 client
editions). Risk: low, and gated on one observable.**

### The bridge inventory versus measured demand

`RefScan` across **all seven profiles, both the `manager-cache/client` and
`manager-cache/server` trees, and the five deployed world plugin trees** — every DLL the shim
could ever face:

| Shim table | Row | Consumers, fleet-wide |
|---|---|---|
| `AppendedOptionalForwards` | `PlayerProfile::IncrementStat(PlayerStatType, Single)` | **0** — EpicLoot and LongshipUpgrades both emit the native 3-arg form |
| `AppendedRequiredForwards` | *(table empty)* | — |
| `ReturnTypeWidenedForwards` | `Vector2i ZDO::GetSector()` | **1** — `CreatureLevelControl.dll` |
| `ReturnTypeWidenedForwards` | `Vector2i ZoneSystem::GetZone(Vector3)` | **0** — all 14 references fleet-wide are `Vector2s` |
| `DelegatedReturnForwards` | `List<ZDO> ZDOMan::GetPortals()` | **0** — `Cross_Server_Portals` 1.3.0, named in the source header as "the SOLE referencing mod", **no longer references it**; its ZDOMan references are `ForceSendZDO` only |

Native forms confirmed in `assembly_valheim.dll` by `DefScan defs`: `Vector2s ZDO::GetSector()`,
`Vector2s ZoneSystem::GetZone(Vector3)`, `Dictionary<SectorIndex,List<ZDO>> ZDOMan::GetPortals()`
alongside `List<ZDO> ZDOMan::GetPortalList()`, and `PlayerProfile::IncrementStat(PlayerStatType,Single,Boolean)`.

**Three rows can be deleted today on the evidence above.** The `GetPortals` row in particular
carries a documented hazard in its own header — a mutating caller would silently write to a
throwaway list — and it now protects nobody.

### The fourth row, and what supersedes it

`L4zerShark_Team-CLLCCompatibility` 1.0.1 does **not** bridge the game. It **repairs CLLC's own
assembly**. MEASURED from its IL and strings:

- `ValheimThreeCompatibility.Loader::TargetDLLs` / `Patch(AssemblyDefinition)`;
  `Repairs(AssemblyDefinition mod, AssemblyDefinition game)` with fields `mod`, `game`,
  `bridges`, `forwards`, and `RewriteCalls(Func<MethodReference,MethodDefinition>)`.
- Repair units: `MessageCalls()`, `Teleport()`, `Terrain()`, `Creature()`, `UpgradeMultiplier(...)`.
- Target strings: `CreatureLevelControl.dll`, `assembly_valheim.dll`, `GetSector`, `Vector2i`,
  `Vector2s`, `noForest` → `s_noForestColor`, `AddItem` →
  `System.String,Int32,Int32,Int32,Int64,String,Vector2i,Boolean,Boolean,Boolean`,
  `CookingStation`, `EffectList`, `Changed`, `"Changed sector query parameter types"`,
  `"Changed terrain paint contract"`.
- Delivery: `PrepareCompatiblePath`, `LoadCompatible`, `"Prepared repaired "`,
  `", repaired sha256 "`, and a transpiler on `Chainloader.Start`'s single
  `Assembly.LoadFile(string)` call.

That set is a **superset of CLLC's eight unresolved references** as I measured them. So once it
runs, CLLC's callsites no longer name `Vector2i ZDO::GetSector()`, and the shim's last consumed
row has zero consumers.

### The gate: it has not run yet

MEASURED:
- Live patcher directories (`<world>/data/bepinex/BepInEx/patchers/`):
  **Ulfsland has `CLLCCompatibility.dll`; Doggerland, Hrafnheim, Storgard and Vangard do not.**
- Durable sources (`<world>/config_merged/bepinex/patchers/`): **all five have it**, mtime
  2026-09-15 18:06–18:07.
- Every boot log shows exactly one `Preloader started`. Ulfsland's boot header is
  `9/12/2026 6:38:10 PM`; the other four logs are 95 lines, last written 2026-09-15 02:49–02:50.
  All five predate the 18:06 install.
- `grep -i 'CLLCCompat\|ThreeCompat'` over Ulfsland's 8,407-line log: **no lines**.

So: the durable source is in place fleet-wide, the hoist into the live tree has happened on
Ulfsland only, and no world has booted with it. **`EverybodyShim` is load-bearing for CLLC in
all five running processes right now.**

### Retirement sequence, which the operator gates

1. Delete the three zero-consumer rows now (source change only, no deploy).
2. On the next restart each world takes anyway, confirm in that world's log:
   `Prepared repaired …, repaired sha256 …` and no
   `will NOT be repaired this session`. The patcher refuses if `Chainloader.Start` was already
   patched by the archived combined `ThreeModCompatibility`, and says so.
3. Re-run `ResolveScan` against the **repaired** CLLC image. Zero `Vector2i ZDO::GetSector()`
   ⇒ remove `EverybodyShim.dll` from `config_merged/bepinex/patchers/` on all five worlds and
   from `custom_packages` in all seven manifests, and delete `tools/everybodyshim`.

**Do not skip step 2 for the four worlds whose live patcher directory lacks the DLL.** Removing
the shim before their hoist completes returns CLLC to `MissingMethodException` on every
`GetSector` call — the 6,813-per-boot failure the shim was built for.

### Axis 6 — orphaned prefabs

**None.** A preloader patcher injects methods into an in-memory assembly. It registers no
prefab, writes no ZDO, and changes no save format. Removing it cannot orphan anything.

---

## R3 — RESTORE the `PieceTable.m_availablePieces` alias. It repairs all four dead-build-piece mods at once, and it is not a replacement.

**Value: high — it is the only fix in this document that makes dead content live again.
Risk: low. Orphan risk: zero, by construction.**

### One root cause, measured

The four mods with dead build pieces — `OdinPlus-Basements` 1.4.1, `OdinPlus-OdinsHorsePen`
1.1.0, `OdinPlus-OdinsUndercroft` 1.3.3, `JamesJonesTV-RavenwoodRestorations` 1.0.1 — share
**exactly one** unresolved reference, and it is the same one:

```
List`1<List`1<Piece>> PieceTable::m_availablePieces
```

They are the **only four** DLLs in the fleet that still emit it. MEASURED, `ResolveScan` over
120 DLLs. 1.0 renamed the per-category lists to `m_availablePiecesByCategory` and reused the old
name for a flat `HashSet<Piece>`; IL binds fields on name **and** signature, so the old
reference does not resolve and the mods' pieces never reach the build table.

`Wubarrk-Valheim10Compatibility` carries a bridge for precisely this and **refuses to emit it on
our profile**. Live, in all five world logs today:

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces (List<List<Piece>>,
alias of m_availablePiecesByCategory): Valheim10Compatibility.Patcher.dll (AmbiguityGuard..cctor),
Valheim10Compatibility.Patcher.dll (Patcher.PatchPieceTableAlias) look this field up by name
through Type.GetField / Traverse.Field …
```

Both named reflectors are **its own patcher assembly**. The guard does not exclude itself. Our
config has the bridge switched **on** — MEASURED,
`config_merged/bepinex/Valheim10Compatibility.cfg`:

```
[Bridges]
PieceTableAvailablePiecesAlias = true
```

so this is a self-block, not a misconfiguration. It is what
`tools/everybodyshim/UPSTREAM-valheim10compatibility-selfblock.md` reported on 2026-09-13.

**1.4.1, published today, does not fix it.** Its changelog scopes itself to `HarmonyResolver.cs`
and states "no IL change, same 54+1 bridges" and "same 54+1 injected / 0 skipped / **2 blocked**".
MEASURED from the changelog text.

### Is emitting the alias safe here?

The guard's stated trigger is any installed plugin passing the literal `"m_availablePieces"` to
`Type.GetField` or `Traverse.Field`. Two measurements:

- 13 third-party DLLs contain that string literal (MEASURED, `strings` over the plugin tree):
  Infinity_Hammer, CraftyCartsRemake, Jotunn, OdinUndercroft, OdinsKingdom, OdinsFoodBarrels,
  OdinCampsite, AdventureBackpacks, OdinShip, RavenwoodRestorations, RossItemDrawers, Basements,
  OdinsHorsePen.
- **V10C's own guard scan already cleared every one of them.** Its BLOCKED line names only its
  two own assemblies. Containing the string is not the same as passing it to `GetField` —
  `AccessTools.Field` and `___m_availablePieces` injection both go through the resolution hook,
  which the README says answers a field lookup "from the caller's own IL". MEASURED: the log
  names no third-party reflector.

Two routes, in order of preference:

1. **Upstream.** The report is already written and unaddressed as of 1.4.1. The fix is a
   self-exclusion in `AmbiguityGuard`.
2. **Locally**, as one field-alias row. This is new code: all four existing shim tables emit
   *method* forwards, so a field emitter does not exist yet. It is also the opposite direction
   from R2 — it would keep the shim alive for a different reason. The honest framing: R2 retires
   the shim's *method* bridges on evidence; R3 is a separate decision about whether to own a
   *field* alias that upstream already implements and merely mis-guards. **My recommendation is
   to press upstream first**, because the alias carries a caveat we would then own (below).

### The caveat that comes with the alias, from its own README

> "1.0 inserted `PieceCategory.DeepNorth = 5`, pushing Feasts/Food/Meads to 6/7/8. A pre-1.0 mod
> bakes those numbers at compile time, so a piece it files under old *Feasts* lands in the
> DeepNorth tab… Wrong tab, not a crash."

So the alias restores the pieces; it may file some of them under the wrong build tab. That is a
cosmetic regression against four mods of dead content, and it is reversible from a config switch.

### Axis 6 — orphaned prefabs

**Zero, by construction.** Nothing is removed, replaced, or renamed. The four mods stay
installed at the same versions; a field that failed to resolve starts resolving. A placed piece
resolves through `ZNetScene`, which is a different code path from the piece-table read, and it is
untouched either way.

### RavenwoodRestorations has a second, unexplained reference

`ItemDrop::OnCreateNew(GameObject)` — unresolved, and I did not find a bridge or a repair for it
in either patcher. **BLOCKER for a clean bill of health on that one mod**: restoring the alias
may still leave some of its behaviour broken. What would settle it: boot a world with the alias
active and read RavenwoodRestorations' own log lines at `[Info]` (all five logs on disk are
`[Info]`-filtered — `grep -c '\[Info'` returns **0** on every one, which is also why no bridge
`Injected` line can be quoted anywhere in this document).

### Replacement alternatives, for completeness — and there are none better

MEASURED from the Thunderstore index (`date_updated`, `downloads`, index-wide dependant counts):

| Our mod | ours | updated | dl | Best alternative found | its version / updated / dl | Verdict |
|---|---|---|---|---|---|---|
| `OdinPlus-Basements` | 1.4.1 | 2026-02-08 | 49,689 | `OdinPlus-OdinsUndercroft` (which we also run), `sbtoonz-Basements` 1.1.7 (2024-04-16, 32,049), `OdinPlus-BasementJVLedition` 1.0.9 (2022-03-06), `Rolo-Basement` (2021) | all **older** | **keep** |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | 2026-07-24 | 159,237 | none in the same niche | — | **keep** |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | 2026-02-05 | 68,513 | nothing equivalent; `Horem-Stronghold` 0.0.8 is from 2022 | — | **keep** |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | 2026-09-08 | 12,533 | content is unique (bookcases, 20 statues, chalkboards, mailboxes) | — | **keep** |

Note `OdinPlus-OdinsKingdom` is the control case: same author, same `m_availablePieces` string
literal present, **not** in the unresolved list, published **1.5.8 today**. OdinPlus has
already moved that one to the 1.0 field and not the other three. So "upstream will fix these"
is a live possibility, not a hope — and a version bump would beat both routes above.

**Swapping any of these four is the worst available option**: it is the only option that can
orphan a placed piece, and the fix that does not is available.

---

## R4 — `Wubarrk-Valheim10Compatibility` 1.4.0 → 1.4.1 (published today)

Not a replacement — the smallest, best-evidenced upgrade in the set, and it is upstream of 19
of our DLLs.

**What 1.4.1 fixes**, quoted from its changelog: 1.3.0–1.4.0 redirected *every* typed
`AccessTools.Method(type, name, Type[])` lookup that landed on an append-only bridge to the
longer native method — "right when the `MethodInfo` becomes a Harmony patch target …
**wrong when the plugin uses it for `MethodInfo.Invoke`, because the arity no longer matches**".
1.4.1 redirects only while Harmony's own patch machinery is resolving.

**Does it bite us?** Their census found the `Invoke` sites in Historical Heritage 2.1.5 and
Magic Supremacy 3.0.7. **We run neither** (MEASURED against all 113 manifest entries). So this
is a latent-risk reduction, not a live fix — labelled accordingly.

**What it costs**, from the changelog's own deployment note: the Adapter bytes change, so both
DLLs bump together, and "every client must update to 1.4.1 before joining". For this fleet that
is a full 20-edition client republish. `AzuAntiCheat` whitelist hygiene is also called out —
**we do not run AzuAntiCheat** (MEASURED), so that paragraph does not apply to us.

**Axis 6:** no prefab, no save format. `README`: "It touches no network protocol and changes no
save format, so a patched server and an unpatched client talk to each other exactly as they did
before." Nothing orphans.

---

## R5 — `maks2204-BestAutoSort` 0.3.1 — SANDBOX ONLY. Not a fleet candidate.

I was asked to overturn this verdict if the evidence allowed. **It does not.** One axis moves in
the mod's favour and it is the orphan axis; the decisive axis stands.

### Axis 1 — feature parity, itemised

Our overlapping stack, all 7 profiles, versions MEASURED from the manifests:

| Ours | version | latest | updated | downloads | dependants | BestAutoSort covers it? |
|---|---|---|---|---|---|---|
| `ArgusMagnus-ServersideQoL_AutoStore` | 2.0.8 | 2.0.11 | 2026-09-16 | 3,799 | 3 | yes — autostore/restock |
| `Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock` | 1.4.15 | 1.4.15 | 2026-09-12 | **880,041** | **1,319** | yes — quick-stack, store, sort, trash, restock |
| `MSchmoecker-MultiUserChest` | 0.6.2 | 0.6.2 | 2026-09-12 | **705,499** | **1,403** | **claims to** — see axis 2 |
| `TastyChickenLegs-AutomaticFuel` | 1.5.1 | 1.5.1 | 2026-09-10 | 343,889 | 372 | **partially** — it has a `KilnWoodPolicy` only. AutomaticFuel covers torches, fires and smelters, and we separately run `AutomaticFermenters`, `CandlesForever` and `TimedTorchesStayLit` |
| `Cytraen-BiggerChests` | 1.1.0 | 1.1.0 | 2025-04-08 | 14,680 | 8 | no — capacity, out of scope |
| `Ross-RossItemDrawers` | 1.0.3 | **1.0.8** | 2026-09-16 | 6,311 | 3 | no — display piece, out of scope |

So it is **3 of 4 replaced plus a partial**, not 4 — the fuel axis is where it misses, and fuel
automation on this fleet is four mods, not one. A 90 %-parity swap that drops smelter and torch
fuelling is a regression, which was the operator's first question.

### Axis 2 — what it takes over. **This is the decisive axis and it is unchanged.**

It reimplements container writes with its own transaction protocol. MEASURED, its own config
description string:

> "Allow players running the same BestAutoSort version to use one stationary chest concurrently.
> Mutations are serialized by the authoritative chest manager (ZDO owner) as idempotent
> transactions; ownership is never ping-ponged."

and its own machinery: `[ChestTX]` transaction logging ("requests, commits, rejects, duplicates,
handoffs"), `revision=`, `stale_revision client=`, `lease-locked=`, `local-owner=`,
`BestAutoSort.AutoFeedLeaseUntil.v1`, `ItemStackLock`, `AutoFeedContainerStackLeasePatch`,
`AutoFeedContainerTakeAllLeasePatch`, and a **transpiler** `TxRenderPatch` that "replaced {0}
owner-check(s)" by rewriting `Container.IsOwner` calls inside `InventoryGui.UpdateContainer`.

`MultiUserChest` 0.6.2 is the mature implementation of exactly that problem: **705,499
downloads, 1,403 dependants, updated 2026-09-12**, and **0 unresolved references** against our
1.0 assemblies (MEASURED — it needs no bridge at all).

**A bug in a container transaction loses items irreversibly**, and this fleet has stocked
containers on five worlds — Stenvik's pads were sited around furnished mod houses containing
chests, measured live by `tools/jumpstart/settlements/nudge.py`'s census ("a bed, 2 chests, 4
ground torches, 2 deer rugs" for `stenvik-cottage-1` alone). And we have a fresh, local
demonstration of the failure mode this class of patch carries: **CarrySkill's transpiler on
`InventoryGui.RepairOneItem` emits invalid IL on this exact build and takes its own `PatchAll`
down** (R1). `TxRenderPatch` is a transpiler on `InventoryGui.UpdateContainer`.

### Axis 3 — maturity, measured

| | BestAutoSort | MultiUserChest | QSSST |
|---|---|---|---|
| version | **0.3.1** | 0.6.2 | 1.4.15 |
| first published | **2026-09-14 (2 days ago)** | — | — |
| updated | 2026-09-15 | 2026-09-12 | 2026-09-12 |
| downloads | **1,627** | 705,499 | 880,041 |
| dependants | **0** | 1,403 | 1,319 |
| `AI Generated` tag | **yes** | no | no |
| versions in 2 days | **4** | — | — |

Four releases in two days is MEASURED (`versions` length 4, created 2026-09-14, latest 0.3.1).
Note the `v1` index lags: it served `0.2.0` as latest while the `experimental` endpoint served
`0.3.1` — so a version read from the bulk index is not authoritative within the hour.

### Axis 4 — version coupling

MEASURED: RPC `BestAutoSort_MultiUserHello`, and the shared-chest feature is gated on "players
running the **same** BestAutoSort version". At four releases in two days, an exact-match gate
means every bump locks out every not-yet-updated player, across **21 client staging trees**
(MEASURED: 7 profiles × `client-config`, `client-config-flat`, `client-config-vr`; the
operator's figure of 20 published client editions is inherited, not re-measured here). That is
a real, recurring cost.

For contrast, MEASURED: `MultiUserChest` and `QSSST` are already in the published set and stable
for days; and among the carry-weight candidates below, `Smoothbrain-PackHorse` also carries a
ServerSync `VersionCheck` gate ("needs to be at least version", "This may happen if client and
server versions of the mod do not match", plus a patch on `FejdStartup.ShowConnectError`), while
`Searica-SkilledCarryWeight` has **no ServerSync reference at all**.

### Axis 5 — performance and load time

**No performance claim is made, in either direction.** Consolidating four plugins into one
310 KB DLL plausibly shortens chainloader work, but that is a hypothesis and I did not measure
it. What is worth stating is that its own text points at the cost class this fleet has already
measured: its shared-chest path adds a per-mutation transaction round trip, and its sort/rule
engine classifies items through `ItemCategoryClassifier` / `ItemCategoryCatalog` per operation.

What to measure, if a sandbox trial happens:
1. `sighsorry-LoadTimeProfiler` 1.3.2 (already in three profiles) per-plugin timings, before and
   after.
2. Frame time with a chest open — the fleet's known hot-path cost is name-based lookups
   (a `TypeByName` scan at 100 ms, a failed one at 1,980 ms, a sprite rebuild per menu slot at
   80 ms), so the number that matters is per-slot cost with a full 63-slot container open.
3. `[ChestTX]` commit latency with two players in one chest, against `MultiUserChest` doing the
   same.

### Axis 6 — orphaned prefabs on removal. **RESOLVED, and it comes out in the mod's favour.**

The chest tiers are **vanilla prefabs**. MEASURED: the DLL's prefab strings are
`piece_chest_wood`, `piece_chest`, `piece_chest_blackmetal`, `piece_chest_grausten` (plus
`chest_hildir1/2/3`); its only prefab API calls are `ZNetScene::GetPrefab`,
`ZNetScene::FindInstance`, `ObjectDB::GetItemPrefab`, with state written through `ZDO::Set`.
There is **no Jotunn dependency** (manifest deps: `denikson-BepInExPack_Valheim-5.4.2350`
only), no asset bundle in the package (contents: `manifest.json`, `plugins/BestAutoSort.dll`,
`icon.png`, `README.md`, `CHANGELOG.md`), and no prefab-registration call. The
`BestAutoSort_Upgrade{Reinforced,BlackMetal,Grausten}` strings are its own button/recipe
identifiers, not prefabs — its own text says it "converted this legacy upgrade to a compact
vanilla" shell.

**So an upgraded chest is a vanilla `piece_chest_blackmetal` and stands with or without the
mod. Removal orphans no prefab.** Its own custom ZDO keys (`CustomDataTags`,
`BestAutoSort.AutoFeedLeaseUntil.v1`) become inert data on the ZDO, which vanilla ignores.
`ResolveScan`: **0 unresolved references** — it is a clean 1.0 build.

### Verdict

**Sandbox world only.** Axis 6 is safe and axis 3/4 are not; axis 2 is disqualifying for a live
fleet with stocked containers. The reversibility finding does make a *sandbox* trial cheap and
genuinely informative, which is the right place for it.

---

## R6 — If the operator wants a dedicated trainable carry skill beyond Hauling

Only relevant if R1's Hauling parity is judged insufficient. Ranked.

| | `Searica-SkilledCarryWeight` 1.5.0 | `Smoothbrain-PackHorse` 1.0.4 | `MBOA-BeastOfBurden` 1.0.9 |
|---|---|---|---|
| updated | **2026-09-15** | 2026-02-05 (222 days) | 2026-02-06 |
| downloads / dependants | 90,659 / 109 | **363,448 / 1,003** | 3,468 / 2 |
| declared game era | **`Deep North Update`** | `Ashlands Update` | — |
| declared deps | BepInEx **5.4.2350** + Jotunn **2.30.0** — exactly what we run | BepInEx 5.4.2333 — **stale**; 1.0 needs 2350+ | BepInEx 5.4.2202 — stale |
| `ResolveScan` vs our 1.0 assemblies | **0 unresolved** | **1**: `Character::Message(…,Sprite)` — a V10C bridge, the same posture as our four working Smoothbrain mods | 0 refs into game assemblies at all (12 KB, reflection-only) |
| adds a NEW skill? | **no** — scales carry weight off existing vanilla skills | **yes** — "Pack horse", via Smoothbrain's `SkillManager`; patches `Skills.GetSkillDef`, `CheatRaiseSkill`, `CheatResetSkill`, `Skills.OnDeath`, `Skills.IsSkillValid` | yes |
| version handshake | **none** — no `ServerSync` reference | **yes** — ServerSync `VersionCheck` + `FejdStartup.ShowConnectError` patch ⇒ every bump forces a client republish | none found |
| conflicts in our set | stacks on `GetMaxCarryWeight` with ImpactfulSkills Hauling and `ComfortTweaks` `Secret_Pocket/IncreaseCarryWeight` (both MEASURED) — additive, and it duplicates Hauling's cart-mass feature | same stack, plus `Character.UpdateWalking`, which `ImpactfulSkills` **transpiles** twice (Sneaking, Running) — two transpilers on one method is the riskiest overlap in this table | unmeasured |
| orphaned prefabs | **none** — no prefab; and no new skill, so **no character-data loss on removal either** | **none** — no prefab; a new skill means progress is lost if later removed | none |

**Recommendation: neither, unless Hauling's ceiling is genuinely the blocker — and then
`SkilledCarryWeight`**, because it is the only 1.0-declared, 1.0-clean, handshake-free option,
and because it adds no skill and therefore nothing that can be orphaned out of a character file.
`PackHorse` has the better download history and the worse 1.0 posture: pre-1.0 build, stale
BepInEx floor, a bridge dependency, an enforced version gate, and a `UpdateWalking` overlap with
a mod we already run.

---

# Do not touch

Replacing or removing any of these risks the live worlds. Each reason is measured, not felt.

| Mod | Why it must not be swapped |
|---|---|
| **`warpalicious-More_World_Locations_AIO`** 5.1.0 (5.1.1 available) | Supplies 190 POI types and the furnished mod houses that 10 of 18 Stenvik pads and 5 of 9 treehouse pads were sited around, plus 26 portal shrines. Its locations are **already instantiated in the saves**. MEASURED as clean on 1.0: 0 unresolved references, 184,506 downloads, 202 dependants, updated 2026-09-15. **DANGEROUS by default.** Before any change: (a) enumerate its placed location instances per world with `Upgrade_World`'s location listing, not a mod-free dump — the mod-free dump cannot see them; (b) confirm every prefab a replacement would stop registering has zero placed instances; (c) take a world backup that has been restore-tested. An unverified swap is unrepairable damage to a world the operator is walking. Even 5.1.0 → 5.1.1 is a **minor** with no read changelog — treat it as a world-touching change, not a routine bump |
| **`Ostrix-AdvancedTerrainModifiersCompatible`** 1.4.8 | Owns the format behind 43 levelled zones and 27,275 m² of hand-synthesised `TCData` paving. A terrain mod that writes that structure differently corrupts it. Note the operator-linked `L4zerShark_Team-AdvancedTerrainCompatibility` is a **different package targeting a mod we do not run** — not a candidate. MEASURED: 1.4.8 is current (updated 2026-09-13), 0 unresolved references. Also note Wubarrk's own boot log lists "Advanced Terrain Modifiers" as one of two persistent `[Error]` lines on their corpus — a known, tolerated condition, not a reason to move |
| **`MSchmoecker-MultiUserChest`** 0.6.2 | The mature implementation of concurrent container writes: 705,499 downloads, 1,403 dependants, updated 2026-09-12, **0 unresolved references on 1.0**. Item loss here is irreversible. `donkeytuesday-MultiUserChest_Valheim1Compat` 1.0.0 exists (2026-09-11, AI Generated) — **we do not need it**, because 0.6.2 already resolves natively against our assemblies |
| **The four dead-build-piece mods** — Basements, OdinsHorsePen, OdinsUndercroft, RavenwoodRestorations | A swap is the only route that can orphan a placed piece, and R3 fixes all four without one. No better-maintained alternative exists for any of them (dates above) |
| **`Smoothbrain-ServerCharacters`** 1.4.17.1 | We run **ahead of** Thunderstore's 1.4.16 (2025-05-02) — a local build. Character files are server-authoritative through it; a downgrade or swap puts every player profile through a different writer. 1,566 dependants upstream |
| **`lunarbin-Cross_Server_Portals`** 1.3.0 + **`Neobotics-TagConnectedPortals`** 1.0.0 | Own 18 tagged portals and the portal hall. Tag data lives in ZDO strings written by these mods; a replacement with different tag semantics silently unpairs portals. Cross_Server_Portals is MEASURED clean on 1.0 and, notably, **no longer needs the shim's `GetPortals` bridge** |
| **`ValheimModding-Jotunn`** 2.30.0 | **21 deployed DLLs reference it** (MEASURED, `RefScan` for `Jotunn.` over the plugin tree): AdvancedTerrainModifiersCompatible, AdventureBackpacks, CarryWeightSkill, DragoonCapes, EpicLoot, ImpactfulSkills, More_World_Locations_AIO, MultiUserChest, OdinArchitect, PotteryBarn, ProtectiveWards, Ravenwood_Currency, RossItemDrawers, ValheimArmory, ZenBreeding, ZenItemStands, ZenPath, ZenRaids, ZenRedecorate, ZenWorldSettings, Zen_ModLib. Not a swap target. Note the four R3 mods are **not** among them — they register pieces without Jotunn, which is why the raw `PieceTable` field read is on their own critical path |

---

# Blockers — unknowns I could not settle read-only

Stated as blockers, not caveats, per the brief.

1. **Which of CarrySkill's eight attributed patch classes applied before `PatchAll` aborted.**
   Harmony's `PatchAll` enumerates types and an exception ends the enumeration, so an unknown
   prefix of the eight applied. The skill registration and the three manual `TryPatch*` calls are
   MEASURED to precede `PatchAll` in the IL and therefore ran. What would settle the rest: one
   boot with BepInEx `[Info]` logging enabled. **This does not gate R1** — removal makes all
   eight moot.
2. **Accumulated "Carry Weight" skill levels per character.** Not read; player data outside
   scope. `ServerCharacters` profiles would answer it. Gates only *how much* progress R1 discards.
3. **Whether any piece from the four R3 mods is already placed in any of the five worlds.** A
   complete placed-prefab census needs `objects_count`/`findObjects` over RCON, which is
   forbidden here, and the saves store prefab **hashes**, not names, so the `.db` cannot be
   grepped for a name. The partial censuses on disk (`roads/corridor_prefabs.json`, 113 kinds
   within 12 m of five road centrelines) are scoped to road corridors and cannot answer it.
   **This blocks any replacement of those four — and is exactly why R3 is a bridge and not a
   swap.** R3 itself is unaffected: it removes nothing.
4. **`ItemDrop::OnCreateNew(GameObject)` for RavenwoodRestorations** — unresolved, no bridge or
   repair identified in either patcher. Blocks a clean bill of health for that one mod even after
   R3.
5. **Which V10C bridges are actually injected on our profile.** Its log reports every outcome at
   `[Info]`, and `grep -c '\[Info'` is **0** in all five logs on disk. Every "covered by V10C"
   cell in the fleet-health table is therefore INFERRED from its README and changelog, not
   observed. One boot with `[Info]` enabled converts the whole table to MEASURED.

---

# Features we lack, and the most mature mod that provides each

The operator's question was where the real value is. Two of the three "new" features in the
BestAutoSort brief **are already installed on all seven profiles** — measured from the manifests
and from the DLLs themselves.

| Feature | Already ours? | Evidence |
|---|---|---|
| **Craft from chests** | **YES** — `fedorovdgap-AzuCraftyBoxes` 1.8.18, all 7 profiles, `shared`, current (2026-09-10), `Mod Enabled = On` in `Azumatt.AzuCraftyBoxes.cfg` line 16 | MEASURED: patches `ConsumeResources`, `CookingStation.FindCookableItem`, `CookingStation.OnAddFuelSwitch`, and covers `$piece_repair`, i.e. **build-piece** requirements too — pulling fuel and cookables as well as craft materials |
| **Animal autofeed** | **YES, capability present** — `ArgusMagnus-ServersideQoL` 2.0.10, all 7 profiles, `Enabled = true` | MEASURED: `TameableRegistryProcessor` with config keys `FeedFromContainers`, `FeedFromContainersMaxRange`. **Whether it is switched on cannot be read from disk**: `ArgusMagnus.ServersideQoL.cfg` is 20 lines containing only `[General]`, so the per-processor keys are runtime defaults. This is a **config question for an installed server-side mod**, not a missing-mod question |
| **Chest tier upgrades** | **NO — and no mature mod provides it** | MEASURED: the only two providers in 11,313 packages are `maks2204-BestAutoSort` 0.3.1 (1,627 downloads, 0 dependants, AI Generated) and `KuliTeam-ChestUpgradeMod` 1.1.1 (2026-09-16, **95 downloads**, 0 dependants, AI Generated). Neither is a fleet candidate. `MilkyTeam-OpenKeep` 1.1.0 (2026-09-14, 709 downloads) is a third consolidation mod in the same class and no more mature |

**So the value in the operator's question is not a swap.** It is: (a) one config line to enable
or confirm `FeedFromContainers`; (b) one config line if the Hauling ceiling should be +300
rather than +50; and (c) accepting that chest tier upgrades have no mature provider today — a
sandbox trial of BestAutoSort is how to find out whether the feature is even wanted, and axis 6
above says such a trial is reversible.

---

# Summary table

| # | Action | Kind | Placed prefab orphaned? | Risk | Blocked on |
|---|---|---|---|---|---|
| R1 | **Remove** `95Shade-CarryWeightSkill` 1.0.2 | deletion | **no** — no prefab. Loses custom-skill progress in character files | low | nothing. Optionally raise `HaulingMaxWeightBonus` |
| R2 | **Retire** `tools/everybodyshim` | deletion | **no** — preloader patcher, no save format | low | 3 rows: nothing. 4th row: one restart per world with `CLLCCompatibility` active, then re-scan |
| R3 | **Restore** the `PieceTable.m_availablePieces` alias | repair | **no** — nothing removed or renamed | low | upstream `AmbiguityGuard` self-exclusion, or a local field-alias emitter (new code) |
| R4 | `Valheim10Compatibility` 1.4.0 → **1.4.1** | upgrade | **no** | low | 20-edition client republish |
| R5 | `maks2204-BestAutoSort` 0.3.1 | **sandbox only** | **no** — vanilla chest prefabs (resolved) | **high on a live fleet** — owns container writes | — |
| R6 | `Searica-SkilledCarryWeight` 1.5.0 | optional | **no** — no prefab, no new skill | low-medium (third `GetMaxCarryWeight` patcher) | only if Hauling's ceiling is the blocker |
| — | Replace any of the four build-piece mods | **rejected** | **UNKNOWN — blocker** | unrepairable | R3 makes it unnecessary |
| — | Replace `More_World_Locations_AIO` or the terrain mod | **rejected** | **UNKNOWN — blocker** | unrepairable | see Do not touch |
