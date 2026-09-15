# Proposal: inventory capacity for Ulfsland and the Neuralyze fleet

Trigger: the operator noticed there is no inventory mod installed and named a candidate —
*"also, there doesnt seem any inventory mods. i noticed that this works with 1.0: ExtraSlots .
what are our inventory options?"*

Status: **research and recommendation only.** Nothing was installed, added, deployed or
published. No mod tree, config tree or character file was written. `valheim-server-Ulfsland`
was not touched. Candidate packages were downloaded to `/tmp/invopt` and disassembled there.

Author context: written 2026-09-15, the night nine jumpstart kits are being authored against
a 32-slot grid (`KitSchema`) and equip-on-spawn is being added to our `ServerCharacters`
build (`ServerCharsEquip`).

Every capability claim below is marked **MEASURED** with the measurement named, or
**[INFERENCE]**. Where a peer supplied a fact it is attributed and was re-measured here
independently before being used.

---

## 0. Recommendation, in one paragraph

**Do not install an inventory mod. Raise capacity with two mechanisms Valheim 1.0.12 already
ships and this fleet is already running: the per-character `invrows` unique key for slots,
and the `carryweightrate` global key for weight.** MEASURED: vanilla `Player::OnSpawned`
reads a character unique key named `invrows` and resizes the inventory to that many rows —
and both live server-side characters in `Ulfsland/config_merged/characters_local/` already
carry the literal bytes `invrows 4` right now. Haldor sells two `+1 row` upgrades in shipped
game data, so 6 rows / 48 slots is reachable in vanilla with no mod on either side; the
row count itself has no clamp in vanilla code. Separately, the dedicated-server launch
argument `-setkey "carryweightrate 200"` sets `Game::m_carryWeightRate = 2.0`, and
`GetMaxCarryWeight()` is `(300 + status effects) × m_carryWeightRate`, so the 300 kg cap is
a server-side dial. Both levers are **server-only, need no client package, cannot produce a
version-handshake lockout, and are reversible per character or per world.** The operator's
candidate, `shudnal-ExtraSlots` 1.2.5, is genuinely the best mod in this space — actively
maintained, resolves 100% of its references and all 124 Harmony patch targets against our
deployed assembly, `ModRequired = false`, dependencies already present on the server, and
explicit compatibility code for four of our installed mods — and it is the right answer
*if* we decide we want dedicated ammo/food/misc/quick slots and a backpack that does not
cost the cape slot. It is not the right answer for "we want more room", because it buys
that at the price of a per-version client handshake and a save-format dependency that the
vanilla keys do not have. **If the operator wants the extra *panels* rather than extra
*room*, install ExtraSlots 1.2.5 + ExtraSlotsCustomSlots 1.0.23; if they want more room,
install nothing.**

**One caveat that outranks the whole question, found while measuring the baseline.**
"Do nothing" is not the null action it looks like. MEASURED: the already-installed
`Vapok-AdventureBackpacks` 2.0.1 carries a Jotunn
`[NetworkCompatibility(EveryoneMustHaveMod, VersionStrictness.Patch)]` attribute — so the
fleet is *already* pinned server-and-all-clients on its third version component — and
2.0.4's own changelog fixes *"item duplication and inventory reset when building or
crafting with container-scanning mods (e.g. AzuCraftyBoxes…)"*, with `AzuCraftyBoxes`
present in the live server plugin tree. **The status quo therefore carries both a hard
lockout and the highest save risk of any option in this document (§4.2).** Whatever is
decided about slots, AdventureBackpacks 2.0.1 → 2.0.4 is the highest-value inventory-
adjacent change available; it belongs to `ModUpdateSweep`, not to this proposal, but no
inventory decision should treat 2.0.1 as a stable floor.

---

## 1. What is actually installed today

MEASURED from the four `profile-manifest.json` files under
`/media/big4/projects/game/valheim/profiles/ulfsland-{flat,dn,vr,admin}/` and from the live
plugin tree `Ulfsland/data/bepinex/BepInEx/plugins/` (97 entries).

No mod increases what a player **carries**. Player inventory is vanilla 8×4 = 32 slots with
vanilla equipment slots. What exists is storage and logistics:

| Package | Version | Declared scope | What it touches |
| --- | --- | --- | --- |
| `Cytraen-BiggerChests` | 1.1.0 | shared | container size |
| `Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock` | 1.4.15 | shared | logistics |
| `MSchmoecker-MultiUserChest` | 0.6.2 | shared | concurrent chest access |
| `Neobotics-RequipMe` | 1.0.0 | shared | re-equip after death |
| `blacks7ar-WieldEquipmentWhileSwimming` | 1.1.3 | shared | equipment while swimming |
| `GoldenJude-Judes_Equipment` | 2.3.0 | shared | extra gear items |
| `Vapok-AdventureBackpacks` | 2.0.1 | shared | wearable containers |
| `geekstreet-BackpacksVRFix` | 1.0.1 | client-only (VR edition) | backpack UI in VR |

### 1.1 Two config files on this server have no mod behind them

This is the trap the fleet already met once tonight, and it is present twice more:

- **`Fortis-ItemStacksRewrite`** — MEASURED: `ls Ulfsland/data/bepinex/BepInEx/plugins |
  grep -i itemstack` returns nothing, yet
  `config_merged/bepinex/ItemStacksRewrite/fortis.mods.itemstacksrewrite.{stacks,weights}.cfg`
  are deployed (4,936 and 9,524 lines). MEASURED: 847 `<Prefab>_max_stack` entries and
  1,587 `<Prefab>_weight` entries, and **zero of either differ from the `# Default value:`
  comment the mod itself wrote next to them.** The mod writes those defaults from the live
  `ObjectDB`, so the defaults *are* the vanilla values. Reading these files today measures
  vanilla, which is exactly the state `tools/jumpstart/worlds/Ulfsland/world.yaml` already
  documents. (The value 810 quoted in the brief is low; `grep -cE '^[A-Za-z0-9_]+_max_stack
  *='` gives 847, and 847 unique prefabs, in a single `[Item Stacks]` section.)
- **`Azumatt-AzuExtendedPlayerInventory`** — MEASURED: absent from all four ulfsland
  manifests, absent from the live server plugin tree, and present only as
  `AzuExtendedPlayerInventory-2.4.{1,3,4}.zip` in `manager-cache/packages/`, never unpacked
  into any `manager-cache/*/BepInEx/plugins` tree. `world.yaml:89` cites
  `Azumatt.AzuExtendedPlayerInventory.cfg`'s `Extra Inventory Rows = 0` as evidence for
  `slots: 32`. The conclusion is right; the citation is a stale cfg. The load-bearing
  evidence is in §3.1.
- **`95Shade-CarryWeightSkill`** — declared `shared` in all four manifests; MEASURED, the
  DLL exists only at `profiles/*/manager-cache/client/BepInEx/plugins/CarryWeightSkill/`
  and is absent from every server tree and from the live plugin directory. Already
  correctly discounted in `world.yaml`.

**The rule this keeps proving: read the filesystem and the package contents, never the
manifest and never the Thunderstore page.**

### 1.2 What AdventureBackpacks actually buys, measured from the deployed config

The brief asks for this number because `KitSchema` needs it. MEASURED from
`Ulfsland/config_merged/config_merged/bepinex/vapok.mods.adventurebackpacks.cfg` — which is
the live file: `docker inspect valheim-server-Ulfsland` shows
`.../Ulfsland/config_merged -> /config`, and the in-container
`BepInEx/config` is a symlink to `/config/bepinex`.

| Backpack | Level 1 | Level 2 | Level 3 | Level 4 | Content weight × | Carry bonus (× item level) |
| --- | --- | --- | --- | --- | --- | --- |
| Satchel | 3×1 = 3 | 4×1 = 4 | 5×1 = 5 | 6×1 = 6 | 0.5 | 5 |
| Rugged Backpack | 3×2 = 6 | 4×2 = 8 | 5×2 = 10 | 6×2 = 12 | 0.5 | 10 |
| Bloodbag Wetpack | 2×3 = 6 | 3×3 = 9 | 4×3 = 12 | 5×3 = 15 | 0.5 | 15 |
| Arctic Sherpa Pack | 3×3 = 9 | 4×3 = 12 | 5×3 = 15 | 6×3 = 18 | 0.5 | 20 |
| Lox Hide Knappsack | 3×4 = 12 | 4×4 = 16 | 5×4 = 20 | 6×4 = 24 | 0.5 | 25 |
| Explorers Wisppack | 8×2 = 16 | 5×4 = 20 | 6×4 = 24 | 7×4 = 28 | 0.5 | 30 |
| Old Rugged Backpack | 6×3 = 18 | — | — | — | 0.5 | 25 |
| Old Arctic Backpack | 6×3 = 18 | — | — | — | 0.5 | 45 |

MEASURED, and worth stating precisely: I diffed every setting in that file against its own
`# Default value:` comment. **Six settings differ from plugin default, and all six are
`Drops from` tables** (boss drops removed: `Dragon`, `Bonemass`, `SeekerQueen`, `GoblinKing`,
`gd_king`, `Eikthyr`, `Unbjorn`, `Bjorn`, `Ulv`). Every capacity, weight multiplier and
carry bonus in the live config equals the plugin's default. So the numbers above are both
*deployed* and *default* — they are not a local tuning decision anyone made.

MEASURED: the file contains `[Server-Synced and Enforced Config]` / `Lock Config = true`,
and every capacity/weight line is annotated `[Synced with Server]`. `AdventureBackpacks.dll`
is present in the live server tree (48,651,264 bytes, md5
`127a2e483c1feec2e2cb2fbd5de06765`) **and** in both the `server` and `client` manager-cache
trees of all four ulfsland profiles. So unlike `CarryWeightSkill`, this one is genuinely
server-side and server-authoritative. (Independently confirmed by `KitSchema`; re-measured
here.)

**The cost nobody had written down.** MEASURED: `ItemDrop.ItemData.ItemType.Shoulder =
0x11 = 17` in the deployed assembly, and all nine AdventureBackpacks prefabs are
`m_itemType 17` with twelve references to `Humanoid::m_shoulderItem` in the deployed DLL.
Shoulder *is* the cape slot. **A backpack and a cape are mutually exclusive.** So the
"do nothing" option's real shape is: +3 to +28 container slots and a 0.5× content-weight
multiplier, paid for with the player's cape — `CapeTrollHide`'s sneak, `CapeLox`'s frost
resistance, `CapeFeather`'s fall damage. Extra inventory rows cost nothing equivalent.
(Reported by `ServerCharsEquip`; re-measured here from the enum and the deployed DLL.)

**The second cost nobody had written down: the baseline already carries a hard lockout.**
MEASURED from the deployed `AdventureBackpacks.dll` (`_version = "2.0.1"`), the assembly
carries
`[Jotunn.Utils.NetworkCompatibility(CompatibilityLevel, VersionStrictness)]` with blob
`01 00 | 02 00 00 00 | 03 00 00 00 | 00 00`, and MEASURED from the deployed `Jotunn.dll`
(assembly version **2.30.0.0**) those enum values are
`CompatibilityLevel.EveryoneMustHaveMod = 2` and `VersionStrictness.Patch = 3`.
MEASURED from `Jotunn.Utils.ModModule::IsLowerVersion(base, compare, strictness)`, the
`Patch` branch compares `Version.Build` — the third component — gated on the minor and
major components being equal-or-lower. **So AdventureBackpacks imposes a Jotunn-enforced
"everyone must have this mod, matched to the third version component" handshake on this
fleet today.** The "do nothing" option is therefore *not* lockout-free: it is
lockout-frozen. Any AdventureBackpacks version bump is a lockstep server + all-four-client
editions move, and a client left on the older build is refused as `IsLowerVersion`.

**And there is a live save hazard in the deployed version.** Flagged by `ModUpdateSweep`;
MEASURED here by downloading `Vapok-AdventureBackpacks` 2.0.4 to `/tmp/invopt/ab-2.0.4.zip`
and reading its own `CHANGELOG.md`:

> **2.0.4 — Container Mod Compatibility & Item Duplication Fix**
> * Fixed: Item duplication and inventory reset when building or crafting with
>   container-scanning mods (e.g. AzuCraftyBoxes, CraftFromContainers).
> * Fixed: Inventory desynchronization between player container component and equipped
>   backpack data.
> * Fixed: Prevented container-saving logic from writing to the player character's network
>   ZDO data.

MEASURED: `AzuCraftyBoxes` **is** in the live Ulfsland server plugin tree. So "inventory
reset when building or crafting" is a path that exists on this fleet right now, at 2.0.1.
2.0.2's changelog confirms the mechanism — it *"overhauled transpilers for requirement
counting and resource consumption (`Player.HaveRequirementItems`,
`Player.ConsumeResources`, `InventoryGui.SetupRequirement`) to support mods injecting
crafting logic (AzuCraftyBoxes, Valheim Plus, EpicLoot, AzuAutoStore)"* — and EpicLoot and
Valheim10Compatibility are both installed here too.

This reframes the whole comparison, and it is the single most decision-relevant thing in
this document after §3: **the status quo is not the safe option.** It is a mod with a hard
Jotunn lockout and a known item-duplication interaction with an installed mod, pinned three
patch releases behind the fix. Whatever else is decided, **AdventureBackpacks 2.0.1 → 2.0.4
is the highest-value inventory change available**, and it is a lockstep publish either way.
I am not recommending it in this document — `ModUpdateSweep` owns the update sweep and
deliberately left it to the kit work — but no inventory decision should be taken as if
2.0.1 were a stable floor.

---

## 2. Step 1 — the operator's candidate, measured

### 2.1 Resolving the package

The name "ExtraSlots" resolves to five Thunderstore packages. MEASURED from the
Thunderstore `package-listing-index` (11,172 Valheim packages, fetched 2026-09-15):

| Identifier | Latest | Updated | Deprecated | Score |
| --- | --- | --- | --- | --- |
| **`shudnal-ExtraSlots`** | **1.2.5** | **2026-09-15 01:52** | no | 121 |
| `shudnal-ExtraSlotsCustomSlots` | 1.0.23 | 2026-09-11 | no | 37 |
| `SAMURAIRSX-ExtraSlots_RU36` | 1.0.35 | 2025-08-20 | no | 2 |
| `PICS0UL-ExtraSlotsDepositGuard` | 1.1.0 | 2026-09-12 | no | 0 |
| `KaBooMa-ExtraSlots_MultipleHotkeyUsage` | 1.0.0 | 2024-12-29 | **yes** | 1 |

The operator means **`shudnal-ExtraSlots`**. It was updated *today*, and six releases have
shipped since the 1.0 boundary (1.2.1 → 1.2.5 between 09-10 and 09-15). That is an actively
maintained package, not a 1.0-compatibility claim on a stale page.

### 2.2 What it adds — numbers, from the DLL's own config binds

MEASURED by disassembling `ExtraSlots.dll` (v1.2.5.0) with `monodis` in a clean directory
containing the deployed `valheim_server_Data/Managed/*.dll`, `BepInEx/core/*.dll` and the
deployed `ConditionalConfigSync`/`YamlDotNet`/`Jotunn` assemblies. Section `Extra slots`:

| Config key | Default | `AcceptableValueRange` |
| --- | --- | --- |
| `Amount of quick slots` | 3 | 0 – 6 |
| `Amount of extra utility slots` | 2 | 0 – 4 |
| `Amount of extra inventory rows` | 0 | **−3 – +5** |
| `Enable ammo slots` | true | → 3 ammo slots |
| `Enable food slots` | true | → 3 food slots |
| `Enable misc slots` | true | → up to 2 misc slots |
| `Slots backup enabled` | true | — |
| `Slots progression enabled` | true | — |
| `Inventory rows progression enabled` | false | — |

So at **defaults**: 11 extra dedicated cells (3 quick + 3 ammo + 3 food + 2 misc), 2 extra
*utility* equipment slots, and **zero** extra regular rows. At **maximum config**: 14
dedicated cells, 4 extra utility slots, and +5 rows = +40 regular grid cells. The five
vanilla equipment slots cannot be disabled or removed.

MEASURED, the default progression gates (section `Progression - Global keys`, one entry per
slot, empty string = available from the start):

| Slot | Required global key |
| --- | --- |
| Quickslot 1, Quickslot 2 | `defeated_gdking` |
| Quickslot 3 | `defeated_bonemass` |
| Quickslot 4 | `defeated_dragon` |
| Quickslot 5 | `defeated_goblinking` |
| Quickslot 6 | `defeated_queen` |
| Ammo slots, Food slots, Misc slots | *(none — from the start)* |
| Extra utility slot 1 | `defeated_bonemass` |
| Extra utility slot 2 | `defeated_goblinking` |
| Extra utility slot 3, slot 4 | `defeated_queen` |

**MEASURED, not inferred** — I first wrote a guess here ("a `pre-queen` character would
arrive with five quick slots and three extra utility slots") and it was wrong on the
utility count, so here is the computed cross-product of the gates above against each
preset's actual `keys.yaml` `global_keys` list. The same table also answers, in its two
middle columns, what vanilla alone gives (§3.2), because Haldor's `invslot1` needs
`defeated_dragon` and `invslot2` needs `defeated_queen`:

| Preset | `global_keys` | Vanilla rows **buyable** | Vanilla slots reachable | ExtraSlots quick slots | ExtraSlots extra utility slots |
| --- | --- | --- | --- | --- | --- |
| `pre-eikthyr` | 0 | +0 | 32 | 0 | 0 |
| `pre-elder` | 1 | +0 | 32 | 0 | 0 |
| `pre-bonemass` | 2 | +0 | 32 | 2 | 0 |
| `pre-moder` | 3 | +0 | 32 | 3 | 1 |
| `pre-yagluth` | 4 | **+1** | **40** | 4 | 1 |
| `pre-queen` | 5 | **+1** | **40** | 5 | 2 |
| `pre-kall` | 7 | **+2** | **48** | 6 | 4 |
| `pre-fader` | 6 | **+2** | **48** | 6 | 4 |
| `deepnorth-sandbox` | 9 | **+2** | **48** | 6 | 4 |

So the progression does line up with the tiers for free and needs no configuration — but
the correct boundaries are **`pre-yagluth`** for the first purchasable row (not
`pre-queen`), and only the last three presets reach 48 slots. Ammo, food and misc slots
have no gate at all, so *every* preset including `pre-eikthyr` gets 3 + 3 + 2 of those
immediately; it is only quick slots and extra utility slots that are boss-gated.

### 2.3 Where the extra slots are stored — the question that decides everything

MEASURED, and the answer is in three parts.

**Part 1: the items live in the vanilla player inventory grid, at rows beyond the vanilla
height.** `ExtraSlots` patches `Player::Awake` with a postfix that calls
`m_inventory.SetHeight(Slots::InventoryHeightFull)`. `InventoryHeightFull` decomposes as:

```
NativeInventoryHeight   = Clamp(int.Parse(player.uniques["invrows"]), 0, 9)   // 4 if absent
InventoryHeightPlayer   = Max(1, NativeInventoryHeight + ExtraRowsPlayer)     // config -3..+5
InventoryHeightFull     = InventoryHeightPlayer + rows needed for the special slots
```

Those cells therefore serialize through **vanilla `Inventory::Save`** (which writes version
`0x6d` = 109 plus a `ushort` count plus each `ItemData`, each carrying its own `m_gridPos`).
There is **no separate slots file and no separate character file.**

**Part 2: the slot *bindings* live in vanilla extension points.** MEASURED, the mod parks
state in `Player::m_customData` — a vanilla `Dictionary<string,string>` that
`Player::Save(ZPackage)` serializes as count + key/value pairs — under these keys:

`ExtraSlotsInventoryBackup`, `ExtraSlotsDeferredInventory`, `ExtraSlotsDeferredTombstone`,
`ExtraSlotsEquipmentPanel`, `ExtraSlotsQuickSlotsHotBar`, `ExtraSlotsFoodHotBar`,
`ExtraSlotsAmmoHotBar`, `ExtraSlotsEquippedWeaponShield`, `ExtraSlotsMigrationEaQSBackup`,
`ExtraSlotsMigrationInventorySlotsBackup`.

Per item, it writes `ItemDrop.ItemData.m_customData["ExtraSlotsEquippedBy"]` and
`["ExtraSlotsEquippedSlot"]`.

**Part 3: what a vanilla client does with rows it does not know about.** This is the
reversal question, and vanilla 1.0.12 answers it two different ways depending on one thing.

MEASURED, `Inventory::AddItem(ItemData, amount, x, y, skipValidPositionCheck)`: the bounds
test rejects `x < 0`, `y < 0` and `x >= m_width` unconditionally, but `y >= m_height` is
tolerated **when `skipValidPositionCheck` is true**. MEASURED, `Inventory::Load(ZPackage)`
passes `skipValidPositionCheck: true` (`ldc.i4.1` at `IL_003e`). **So loading never loses an
out-of-bounds item.** It sits in `m_inventory` at its saved grid position: invisible in a
4-row GUI, unreachable, still counted by `GetAllItems()` and `UpdateTotalWeight()`, and
written back out by the next `Save`.

MEASURED, `Player::SetInventorySize(int rows)` does four things in order:
`m_inventory.SetHeight(rows)`; `AddUniqueKeyValue("invrows", rows)`;
`InventoryGui.SetInventorySize(rows)`; `Humanoid::DropInvalidItems()`. And
`Humanoid::DropInvalidItems()` collects every item whose `m_gridPos.x >= GetWidth()` or
`.y >= GetHeight()`, logs `"Dropping {0} invalid positioned items."`, and **drops each one
on the ground** via `Humanoid::DropItem`. Only two call sites exist:
`Player::SetInventorySize` and the `inventoryclean` console command.

So on a vanilla client:

- If the character has **no** `invrows` key, `OnSpawned` writes `invrows 4` and does **not**
  call `SetInventorySize` — items in rows ≥ 4 are silently retained, invisible, and
  recoverable by reinstalling the mod.
- If the character **has** an `invrows` key — which vanilla 1.0 always writes on first
  spawn, and which both our live characters already have — `OnSpawned` calls
  `SetInventorySize(invrows)` **every spawn**, so `DropInvalidItems` runs and anything above
  row `invrows` is **dropped at the player's feet as world `ItemDrop`s**.

Items are therefore **not destroyed** in either case. They are stranded or littered.
Crucially, `ExtraSlots` patches `Humanoid::DropInvalidItems` itself
(`Humanoid_DropInvalidItems_ReconcilePlayerItems`) precisely to prevent the littering while
it is installed, and parks anything it cannot place in
`m_customData["ExtraSlotsDeferredInventory"]` rather than dropping it — plus the
`ExtraSlotsInventoryBackup` snapshot written on every character save.

**Verdict on ExtraSlots' save risk: LOW, and structurally lower than the mod's reputation
in this space.** It stores nothing in a private side-file, nothing in a proprietary
character field, and nothing a vanilla client will refuse to round-trip: an unmodded client
reads the unknown `m_customData` keys into the dictionary and writes them straight back
out. The failure mode is "items you cannot reach until you reinstall, or items on the
ground at your login point", not "items destroyed".

### 2.4 Is it server-synced, and is it `ModRequired`?

MEASURED from the static constructor of `ExtraSlots.ExtraSlots`:

```
ldstr "shudnal.ExtraSlots"    newobj ConditionalConfigSync.ConfigSync::.ctor(string)
  DisplayName            = "Extra Slots"
  CurrentVersion         = "1.2.5"
  MinimumRequiredVersion = "1.2.5"
  set_ModRequired(false)          // ldc.i4.0
```

It uses `shudnal-ConditionalConfigSync`, not blaxxun `ServerSync`, and
**`ModRequired = false`**. So a client with **no** ExtraSlots can join normally.

But `ModRequired` only governs the "peer does not have the mod at all" case. MEASURED from
the deployed `ConditionalConfigSync.dll` (1.0.6), `VersionCheck::IsVersionOk()`:

```
if (receivedServerHandshake == null) return !ModRequired;
return GetFailure(handshake, localCurrentVersion, localMinimumRequiredVersion) == None;
```

and `GetFailure` returns `RemoteVersionTooOld` when `remote.CurrentVersion < localMinimum`,
or `LocalVersionTooOld` when `localCurrent < remote.MinimumRequiredVersion`. Because
ExtraSlots sets `CurrentVersion == MinimumRequiredVersion == "1.2.5"` on both sides,
**exact version equality is required between the server and any client that has the mod.**

This is the same arithmetic that cost the operator over an hour tonight. For comparison,
MEASURED from blaxxun `ServerSync` as embedded in `Fortis-ItemStacksRewrite`:

```
if (ReceivedMinimumRequiredVersion == null || ReceivedCurrentVersion == null)
    return !ModRequired;
return localCurrent >= ReceivedMinimumRequiredVersion
    && ReceivedCurrentVersion >= localMinimumRequiredVersion;
```

Identical shape. A 1.4.16 client against a 1.4.17 server fails the first conjunct on the
server side and the second on the client side.

**The explicit cost of installing ExtraSlots:** every publish of a new ExtraSlots version
must reach the server *and* all four client editions, and any client left on the previous
version is **refused at connect** — with the important mitigation that, unlike a
`ModRequired = true` mod, a locked-out player can recover by *deleting* the mod rather than
by obtaining the exact right build. Given the release cadence measured in §2.1 (five
releases in five days) that is a real, recurring operational cost.

Softening context, MEASURED: `ConditionalConfigSync` 1.0.6 is **already deployed** and
already consumed by six live plugins — `CheatDeath`, `DeathPenalty`, `DigDeeper`,
`HipLantern`, `LongshipUpgrades`, `TradersExtended`. Adding ExtraSlots adds one more package
to a per-mod handshake that this fleet is already running; it does not introduce the
mechanism. Its other two dependencies are satisfied too: it needs
`ConditionalConfigSync ≥ 1.0.5` (1.0.6 deployed) and `YamlDotNet 16.3.1` (16.3.1 deployed).

**The second lockout mechanism, checked separately.** A `ServerSync` /
`ConditionalConfigSync` search does not find a *Jotunn* lockout, because Jotunn's is a
custom attribute rather than a code path — which is exactly how the AdventureBackpacks
lockout in §1.2 went unnoticed. So I checked every candidate for
`Jotunn.Utils.NetworkCompatibilityAttribute` as well. MEASURED: **none of
`shudnal-ExtraSlots` 1.2.5, `shudnal-ExtraSlotsCustomSlots` 1.0.23,
`RandyKnapp-EquipmentAndQuickSlots` 3.1.3, `Fortis-ItemStacksRewrite` 1.0.2,
`shudnal-ItemStacksItemWeights` 1.1.1 or `sighsorry-InventorySlots` 1.4.17 carries one.**
The per-option lockout verdicts in §4.1 therefore account for both mechanisms, not just
the one the brief named.

### 2.5 Does it reference APIs that exist in our deployed assembly?

MEASURED. Technique: disassemble `assembly_valheim.dll` from
`Ulfsland/data/bepinex/valheim_server_Data/Managed/` (a writable copy plus `monodis` in
`/tmp`, with the full `Managed` tree present so references resolve — 581,271 lines),
index every type and member it defines, then check every `[assembly_valheim]Type::Member`
reference in each candidate. Then, separately, decode every `HarmonyPatch` custom-attribute
blob (SerString-decoded from the raw attribute bytes) and check the *patch targets* —
which is the stronger test, because Harmony resolves those by name at runtime and a missing
one is a startup crash, not a compile error.

| Package | `assembly_valheim` refs | Unresolved refs | HarmonyPatch targets | Unresolved targets |
| --- | --- | --- | --- | --- |
| `shudnal-ExtraSlots` 1.2.5 | 259 | **0** | 124 | **0** |
| `shudnal-ExtraSlotsCustomSlots` 1.0.23 | 56 | **0** | 67 | **0** |
| `RandyKnapp-EquipmentAndQuickSlots` 3.1.3 | 242 | **0** | 80 | **0** |
| `Fortis-ItemStacksRewrite` 1.0.2 | 64 | **0** | 16 | **0** |
| `shudnal-ItemStacksItemWeights` 1.1.1 | 32 | **0** | 10 | **0** |
| `sighsorry-InventorySlots` 1.4.17 | 431 | **0** | 138 | **0** |
| `Vapok-AdventureBackpacks` 2.0.1 *(deployed, control)* | — | — | 46 | **0** |

Targets in `assembly_guiutils`, `assembly_utils`, `gui_framework`, `UnityEngine.*` and each
mod's own embedded namespaces were resolved against those assemblies or excluded; the only
raw misses were `ItemDrop+ItemData` (nested-type `+` vs `/`) and generic methods
(`ZRoutedRpc::Register<T>`), both verified present by hand.

**This is the strongest compatibility evidence available short of running the mod, and it
is what it can and cannot prove.** It proves no `MissingMethodException`/
`MissingFieldException` at load and no Harmony `TargetMethod` resolution failure. It does
**not** prove that any *transpiler*'s assumptions about IL shape still hold — ExtraSlots
ships transpilers on `InventoryGui::DoCrafting`, `Humanoid::EquipItem` and
`Inventory::AddItem`, and a transpiler can silently no-op or corrupt a method whose IL was
reordered by the 1.0 update while every reference resolves perfectly. Confirming those
needs a run. **[INFERENCE]** A sandbox dedicated server in `/tmp` with a throwaway world
would settle it in one pass, and should be the gate if the operator chooses ExtraSlots.

### 2.6 Does it collide with what we already run?

MEASURED. I disassembled the *deployed* `RequipMe.dll`, `JudesEquipment.dll`,
`QuickStackStore.dll`, `ValheimBiggerChests.dll`, `MultiUserChest.dll` and
`AdventureBackpacks.dll` from the live plugin tree, decoded their HarmonyPatch attributes,
and intersected the target sets.

`shudnal-ExtraSlots` shares patch targets with:

- **`Vapok-AdventureBackpacks` — 17 shared targets**: `Container::Awake`,
  `FejdStartup::Start`, `Humanoid::{EquipItem, UnequipItem, UpdateEquipmentStatusEffects}`,
  `Inventory::{AddItem, Changed, MoveAll, UpdateTotalWeight}`,
  `InventoryGrid::{DropItem, UpdateGui}`,
  `InventoryGui::{DoCrafting, OnSelectedItem, Update}`, `ItemDrop/ItemData::GetWeight`,
  `Player::Awake`, `SEMan::RemoveStatusEffect`.
- **`MSchmoecker-MultiUserChest` — 6 shared targets**: `Container::Awake`,
  `Inventory::{AddItem, CanAddItem, Changed, MoveAll}`, `InventoryGui::Update`.
- **`Neobotics-RequipMe` — 3 shared targets**: `Player::Update`,
  `TombStone::EasyFitInInventory`, `TombStone::OnTakeAllSuccess`.
- **`Cytraen-BiggerChests` — 1**: `Container::Awake`.
- **`GoldenJude-Judes_Equipment` — 1**: `FejdStartup::Start`.
- **`Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock` — 0 vanilla targets in common.**

Shared targets are not by themselves a conflict. What matters is whether the mod *knows*.
MEASURED — ExtraSlots ships a `ExtraSlots.Compatibility` namespace with named handling for,
among others:

- `QuickStackStore` — detects GUID `goldenrevolver.quick_stack_store` and patches
  `QuickStackStore.CompatibilitySupport::InternalIsEquipOrQuickSlot`, logging *"method is
  patched to ignore items in equipment slots and restock other slots"*.
- `RequipMeCompat` — detects GUID `neobotics.valheim_mod.requipme` and **unpatches**
  `neobotics.ValheimMods.RequipMe+On_Take_All_Success_Patch::Postfix` and
  `…+EasyFitInInventory_Patch::Postfix`, with the reason string *"prevent inventory mess on
  tombstone equip"*. That is precisely the three-target overlap above, handled deliberately.
- `EpicLootCompat` — detects `randyknapp.mods.epicloot` and registers an equipment provider
  and a sacrifice filter through `EpicLoot.API` so slot items still carry magic effects.
  EpicLoot is installed here.
- `ServerCharactersCompat` — detects us by GUID
  `org.bepinex.plugins.servercharacters` via `Chainloader.PluginInfos`, and patches
  `Inventory::Save` and `FejdStartup::Start`.
- Also present: `ValheimPlusCompat`, `BetterArcheryCompat`, `BetterProgressionCompat`,
  `ComfyQuickSlotsCompat`, `InventorySlotsCompat`, `EquipmentAndQuickSlotsCompat`,
  `AzuAutoStore`, `Recycle_N_Reclaim`, `SimpleSort`, `PlantEasilyCompat`, `ZenUICompat`,
  `ZenBeehiveCompat`, `BBHCompat`, `ValheimRadial`.

The backpack case is handled in the **companion** package. MEASURED from
`ExtraSlotsCustomSlots.dll` 1.0.23: it detects `vapok.mods.adventurebackpacks`, creates a
dedicated `AdventureBackpacks` custom slot resolved through `AdventureBackpacks.API.ABAPI::
IsBackpack`, unpatches
`AdventureBackpacks.Patches.HumanoidPatches+HumanoidUnequipItemPatch::Prefix`, and patches
`AdventureBackpacks.Extensions.PlayerExtensions::IsBackpackEquipped` *"to make it work with
custom slot"*. It also ships a `GoldenJude_JudesEquipment` slot for the
`JudesEquipmentBackpack` prefab — and Judes_Equipment is installed here.

**This is the one thing a mod can do that the vanilla keys cannot: move the backpack out of
the cape slot.** Given §1.2 — every backpack is `ItemType.Shoulder`, i.e. the cape slot —
that is a genuine, concrete gameplay gain, not a convenience.

`MultiUserChest` is the overlap with **no** named compatibility class. **[INFERENCE]** Both
mods patch `Inventory::{AddItem, CanAddItem, Changed, MoveAll}`; MultiUserChest exists to
make concurrent container mutation safe, and ExtraSlots batches `Inventory::Changed`
notifications (`Inventory_Changed_DebounceMutationBatch`, with a config toggle described as
*"Disable this as a compatibility diagnostic if another mod depends on intermediate
Inventory.Changed callbacks"*). Debounced change notifications and a mod built around
change notifications is the plausible failure. That toggle exists for exactly this, and this
pairing is the specific thing a sandbox run should exercise.

---

## 3. The vanilla mechanism nobody in this fleet was using

This is the headline finding, and it was not on the option list in the brief.

### 3.1 Valheim 1.0.12 has a per-character inventory row count

MEASURED from the deployed `assembly_valheim.dll`:

```
Player.InventoryRowsKey = "invrows"                     // public static literal string

Humanoid..ctor:      new Inventory("Inventory", null, 8, 4)    // ldc.i4.8 / ldc.i4.4

Player::OnSpawned(bool):
    if (TryGetUniqueKeyValue("invrows", out s) && int.TryParse(s, out n))
        SetInventorySize(n);
    else
        AddUniqueKeyValue("invrows", "4");

Player::SetInventorySize(int rows):
    m_inventory.SetHeight(rows);
    AddUniqueKeyValue("invrows", rows.ToString());
    InventoryGui.instance.SetInventorySize(rows);
    DropInvalidItems();

Inventory::SetHeight(int h):  { m_height = h; }          // bare field store, NO CLAMP
InventoryGui::SetInventorySize(int rows):
    m_player.sizeDelta = (x, m_playerHeight + (rows - 4) * m_invGridHeight);
```

So the vanilla UI panel *grows with the row count on its own*. There is no clamp anywhere in
vanilla — `SetHeight` is a field store and `SetInventorySize` clamps nothing. (Both
ExtraSlots and EAQS 3.x clamp the key to `[0, 9]` when they read it; vanilla does not.)

MEASURED, the storage: `Player::AddUniqueKeyValue(key, value)` stores the single string
`"<key> <value>"` (space-joined) in `Player::m_uniques`, a `HashSet<string>`;
`TryGetUniqueKeyValue` splits on `' '` and matches element 0 case-insensitively.
`Player::Save(ZPackage)` writes `m_uniques.Count` then each string. So `invrows` is an
ordinary character unique key.

**MEASURED on live data — the decisive check.** The server-side character store is
`Ulfsland/config_merged/characters_local/`, holding full `.fch` `PlayerProfile` files
(`Steam_76561197987967077_lief.fch`, `…_minoc.fch`, plus `.fch.old` and timestamped
`_backup_auto-*` copies and a `backups/` directory). Scanning them for the raw bytes:

```
Steam_76561197987967077_lief.fch                          23832 B   contains b'invrows 4'
Steam_76561197987967077_minoc.fch                         23962 B   contains b'invrows 4'
Steam_76561197987967077_lief_backup_auto-20260914-053123.fch  20722 B   does NOT contain it
```

The mechanism is not theoretical and it is not dormant. **It is live on this server right
now, storing `invrows 4` inside the ServerCharacters-managed character files**, and the
2026-09-14 05:31 auto-backup predates those characters' first 1.0 spawn.

### 3.2 Vanilla already sells two extra rows

MEASURED from the game's own asset bundles. Technique: reuse
`tools/jumpstart/extract_prefab_names.py`'s `iter_bundle_blocks` UnityFS reader over all 796
bundles under `valheim_server_Data/StreamingAssets/SoftRef/Bundles` (raw `grep` finds
nothing — the blocks are LZ4/LZMA compressed), then parse the `Trader/TradeItem` records
around each hit. `invrows` occurs in bundles `17a773de` and `c4210710`, inside Haldor's
trade list (adjacent strings: `npc_haldor_random_talk1…7`).

`Trader/TradeItem` field order from the assembly is
`m_prefab, m_stack, m_price, m_requiredGlobalKey, m_levelUpEffect, m_buyPlayerEffects,
m_icon, m_name, m_tooltip, m_buyKey, m_incrementKey, m_incrementAmount`. Decoded:

| `m_name` | `m_price` | `m_requiredGlobalKey` | `m_buyKey` | `m_incrementKey` | `m_incrementAmount` |
| --- | --- | --- | --- | --- | --- |
| `$hud_extrainvslot1` | **1000** coins | `defeated_dragon` (Moder) | `invslot1` | `invrows` | **1** |
| `$hud_extrainvslot2` | **2000** coins | `defeated_queen` (The Queen) | `invslot2` | `invrows` | **1** |

MEASURED, `StoreGui::BuySelectedItem` handles `m_incrementKey` specially: it reads the
player's current value for that key, adds `m_incrementAmount`, and **if the key is
literally `"invrows"` calls `Player::SetInventorySize(newValue)`** instead of just writing
the key.

**So vanilla Valheim 1.0 ships a boss-gated, coin-gated inventory expansion to 6 rows / 48
slots.** Nobody needs a mod for it. MEASURED per preset against each `keys.yaml`
`global_keys` list (full table in §2.2): `defeated_dragon` — and therefore the first +1 row
— is set from **`pre-yagluth`** onward (5 of 9 presets, reaching 40 slots), and
`defeated_queen` — the second +1 row — only in **`pre-kall`, `pre-fader` and
`deepnorth-sandbox`** (3 of 9, reaching 48 slots). The four earliest presets
(`pre-eikthyr`, `pre-elder`, `pre-bonemass`, `pre-moder`) can buy neither and stay at 32.

### 3.3 The 300 kg cap is a server-side dial

MEASURED:

```
Player..ctor:                  ldc.r4 300. -> stfld Player::m_maxCarryWeight
Player::GetMaxCarryWeight():   v = m_maxCarryWeight;
                               m_seman.ModifyMaxCarryWeight(v, ref v);
                               return v * Game::m_carryWeightRate;
Player::IsEncumbered():        GetTotalWeight() > GetMaxCarryWeight()     // STRICT >

GlobalKeys.CarryWeightRate = 0x10
Game::UpdateWorldRates(globalKeys, globalKeysValues):
    trySetScalarKey(CarryWeightRate, out m_carryWeightRate,
                    defaultValue: 1f, multiplier: 100f)
trySetScalarKey(key, out value, def, mult):
    value = def;
    if (globalKeysValues.TryGetValue(key.ToString().ToLower(), out s)
        && float.TryParse(s, Any, InvariantCulture, out f))
        value = f / mult;
```

**The key name is `carryweightrate` and the value is divided by 100.** So
`carryweightrate 200` → `m_carryWeightRate = 2.0` → cap = 600 kg. Note the multiplication
is applied *after* `SEMan::ModifyMaxCarryWeight`, so it scales Megingjord's +150 and the
AdventureBackpacks carry bonuses too.

There are two ways to set it, and they cost different things.

**(a) The launch argument — free.** MEASURED from `FejdStartup`'s command-line loop, the
dedicated server accepts `-setkey "<key> <value>"`, and its handler is three lines:

```
if (!World.m_startingGlobalKeys.Contains(arg))
    World.m_startingGlobalKeys.Add(arg.ToLower());
```

**No cheat classification is consulted on this path at all.** Those starting keys become the
world's global keys, `ZoneSystem::GlobalKeyAdd` splits `"key value"` into
`m_globalKeysValues`, and `RPC_GlobalKeys` distributes them to every client. This is
strictly server-side, propagates automatically, and needs no client package.

Note the contrast: `-modifier <WorldModifiers> <WorldModifierOption>` only accepts the six
enum modifiers (`Combat`, `DeathPenalty`, `Resources`, `Raids`, `Portals`) — carry weight is
**not** a world modifier and cannot be set that way. This fleet already generates
`SERVER_ARGS='-preset Normal -modifier Resources MuchMore -modifier Portals Casual'` into
`tools/jumpstart/worlds/Ulfsland/<preset>/settings/world-modifiers.env`, which is exactly
where `-setkey "carryweightrate N"` belongs. It needs a restart.

**(b) The runtime console command — works here, with one caveat worth stating.** MEASURED,
`setkey` is registered with `isCheat=false, onlyServer=true, remoteCommand=true`, and its
handler gates on `ServerOptionsGUI::SetKeyAndValueIsCheat(cmd)`: if the exact `"key value"`
string is *not* one of `GetPossibleGlobalKeyValues()` (the World Modifiers GUI's own slider
and toggle values) it counts as a cheat and is applied only when
`Achievements::IsCheatedAtAll()` is already true.

MEASURED: `carryweightrate` does **not** appear in any of the 796 asset bundles, while
`resourcerate` appears in three. So `carryweightrate` is not a GUI slider value and *is*
cheat-classified. MEASURED: it does not matter here, because
`Achievements::IsCheatedAtAll()` returns true if `Game.isModded` is set, and
`Valheim.DisplayBepInExInfo.dll` — present in the live server plugin tree — does exactly
`Traverse.Create<Game>().Field<bool>("isModded").Value = true` in its `Awake`. **This
fleet is permanently `isModded`, so the gate is already satisfied and the runtime `setkey`
applies.** The launch-argument route avoids the question entirely and is the one to use.

MEASURED, the other relevant gate: the `inventorysize [rows]` console command
(`isCheat=true, onlyServer=true`) and `inventoryclean` both exist, but
`ConsoleCommand::IsValid` requires `ZNet.IsServer()` for `onlyServer` commands, so a
connected client on a dedicated server **cannot** run `inventorysize` — and
`JereKuusela-Server_devcommands` does not help: its `IsCheatsEnabledWithoutServerCheck`
patch relaxes `Terminal::IsCheatsEnabled` only, and its aliasing code copies `OnlyServer`
through verbatim. `inventorysize` also operates on `Player.m_localPlayer`, which does not
exist on a dedicated server. So the console is not the route to rows; the character key is.

---

## 4. The option space

### 4.1 Summary

| # | Option | Slots gained | Weight gained | Client package needed | Lockout hazard | Save-risk verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **Do nothing** | 32 + 3…28 in a worn backpack | 0.5× backpack contents; +5…45 × level | none new | **yes — already live:** AdventureBackpacks is Jotunn `EveryoneMustHaveMod` / `Patch` (§1.2) | **MODERATE — see §4.2.** Not "none": 2.0.1 has a documented item-duplication / inventory-reset path with `AzuCraftyBoxes`, which is installed |
| 2 | **`shudnal-ExtraSlots` 1.2.5** (+ CustomSlots) | +11 dedicated cells, +2 utility equip slots at default; up to +14 and +5 rows | none directly | **yes, exact version** | **yes — version-equality refusal** | **LOW** |
| 3 | **`RandyKnapp-EquipmentAndQuickSlots` 3.1.3** | 5 equip + 3 quick moved out of the grid | none | client-side only | **none** | **LOW** (3.x; **not** the 2.x reputation) |
| 4 | **Vanilla `invrows` + `carryweightrate`** | up to +2 rows in-game (48); arbitrary by key | ×N on the 300 kg cap | **none** | **none** | **NONE** |
| 5 | **Bigger stacks (`ItemStacksRewrite` / `ItemStacksItemWeights`)** | relieves slot pressure, adds none | none | ISR: optional; IIW: **required** | ISR: version-equality; **IIW: hard `ModRequired`** | **NONE** (stack size is not persisted per item) |
| — | `sighsorry-InventorySlots` 1.4.17 | — | — | **`ModRequired = true`** | **hard lockout** | **rejected, see §4.6** |

### 4.2 Option 1 — do nothing

32 vanilla slots, plus whichever backpack the player has earned, with the numbers in §1.2.
The 0.5× content multiplier on an Explorers Wisppack at level 4 is 28 slots of half-weight
cargo plus 120 kg of extra capacity — genuinely substantial. Two costs, both measured in
§1.2 and neither previously written down:

1. **The backpack occupies the cape slot.** All nine prefabs are `ItemType.Shoulder = 17`.
   A player wearing a backpack has no `CapeTrollHide` sneak, no `CapeLox` frost resistance,
   no `CapeFeather` fall protection. Extra inventory rows cost nothing equivalent, and
   `ExtraSlotsCustomSlots` is the only measured way to have both.
2. **The deployed version has a live save hazard and a hard lockout.**
   AdventureBackpacks 2.0.1 is Jotunn `EveryoneMustHaveMod` with `VersionStrictness.Patch`,
   so the fleet is already frozen in lockstep on the third version component; and 2.0.4's
   own changelog fixes *"item duplication and inventory reset when building or crafting
   with container-scanning mods (e.g. AzuCraftyBoxes…)"* — and `AzuCraftyBoxes` is in the
   live server plugin tree.

**Save risk: MODERATE, and it is the highest save risk in this entire document.** Every
other option's worst case is "items stranded or on the ground, recoverable". This one's
documented failure mode is *duplication and inventory reset*, on a path that is live on
this fleet, in the version we are running. **[INFERENCE]** "Do nothing" is therefore not a
null action — it is a decision to keep running a known-defective build. The remedy is not
in this document's scope (`ModUpdateSweep` owns it) but it should not be mistaken for
inaction.

### 4.3 Option 2 — `shudnal-ExtraSlots` 1.2.5

Fully measured in §2. Best-in-class, actively maintained, references and patch targets all
resolve, `ModRequired = false`, dependencies already deployed, and explicit compatibility
code for QuickStackStore, RequipMe, EpicLoot, ServerCharacters and (via CustomSlots)
AdventureBackpacks and Judes_Equipment. Its unique capability is moving the backpack off the
cape slot.

Costs: a per-version exact-match client handshake on a package that shipped five releases in
five days; one un-named patch overlap with MultiUserChest; three untested transpilers; and
a new save-format surface in `Player::m_customData` (benign, but new).

**Save risk: LOW.** Items live in the vanilla inventory grid and in vanilla
`m_customData`/`ItemData.m_customData`. Removing the mod strands or litters items; it does
not destroy them. The mod additionally writes `ExtraSlotsInventoryBackup` on every save and
parks unplaceable items in `ExtraSlotsDeferredInventory` rather than dropping them, and its
own README documents the vanilla-load-then-resave-then-reinstall recovery path. Because our
characters are stored server-side, that backup blob is inside a server-side `.fch` we
already snapshot (`.fch.old`, `_backup_auto-*`, `backups/`) — see §5.4.

### 4.4 Option 3 — `RandyKnapp-EquipmentAndQuickSlots`

The brief asks me to establish its real status and say plainly if it is dangerous. **Its
reputation does not describe the package that exists today, and I am not going to repeat it
as if it did.**

MEASURED: `RandyKnapp-EquipmentAndQuickSlots` latest is **3.1.3, published 2026-09-15
03:53** — today, and the highest-rated package in this space (rating 296). It is **not
deprecated**. Six releases since 2026-09-03. All 242 `assembly_valheim` references and all
80 HarmonyPatch targets resolve against our deployed assembly.

MEASURED, and this is the important part: **3.x was rewritten and no longer stores extra
slots the way the notorious 2.x did.** It binds each item to its slot with
`ItemDrop.ItemData.m_customData["eaqs_slot"]`, keeps a versioned recovery envelope in
`Player.m_customData["eaqs_backup"]` (with a `"Unknown backup envelope version {0}"` guard),
and **reads and honours the vanilla `invrows` key**, clamped to `[0, 9]`, in a
`Player::Load` postfix. There is no private side-file and no proprietary character field.

MEASURED: it contains **no `ServerSync`, no `ConditionalConfigSync`, and no version check of
any kind** — the strings `ModRequired`, `MinimumRequiredVersion` and `CurrentVersion` do not
occur in the assembly. Its Thunderstore categories are `Tweaks, Client-side, Utility`. It is
a **purely client-side** mod: **no server deploy, no republish gate, and no lockout hazard
whatsoever.**

MEASURED, the caveat: it has **no AdventureBackpacks compatibility class** (its named compat
code covers BetterArchery, BetterUI and EpicLoot), while sharing 14 patch targets with the
deployed AdventureBackpacks — including `Humanoid::{EquipItem, UnequipItem}` and
`SEMan::RemoveStatusEffect`. ExtraSlots handles that pairing explicitly; EAQS does not.
Its Thunderstore dependency is `ValheimModding-Jotunn-2.29.2`; MEASURED, the deployed
`Jotunn.dll` reports assembly version **2.30.0.0** (`monodis --assembly`), which satisfies
it. The plugin directory carries no `manifest.json`, so the Thunderstore package version is
unreadable, but the assembly version is the one that matters for reference binding.

**Save risk: LOW for 3.1.3 as measured — but recommend against it anyway**, for a reason
that is not about corruption: it is client-side only, so every player would have to install
it individually and the server could not enforce or configure anything. That is the opposite
of how this fleet is managed, and it means two players would see two different inventories
against the same server-side character. If the operator wants extra panels, ExtraSlots is
the server-authoritative way to get them.

### 4.5 Option 4 — vanilla capacity instead of vanilla slots

Fully measured in §3. Two independent dials:

- **Slots.** `invrows` is a per-character unique key inside the server-side `.fch`. Vanilla
  reaches 6 rows / 48 slots through Haldor; the row count itself is unclamped in vanilla
  code, so a higher value is possible, with the `DropInvalidItems` caveat below.
- **Weight.** `-setkey "carryweightrate N"` in `SERVER_ARGS` multiplies the cap by `N/100`,
  applied after status effects.

This is the **only** option in the list with zero client-side dependency. Given that the 1.0
migration removed 21 mods from this fleet, and that a ServerSync skew cost the operator over
an hour tonight, that property is worth real weight.

**Save risk: NONE for `carryweightrate`** — it is world state in `globalKeysValues`, touches
no character and no item.

**Save risk: NONE-to-LOW for `invrows`, with one rule.** Raising it is safe unconditionally:
`SetInventorySize` grows the grid, and `DropInvalidItems` finds nothing out of bounds.
*Lowering* it is the hazard: MEASURED, `Player::OnSpawned` calls `SetInventorySize(invrows)`
on **every** spawn, so on the first login after a reduction `DropInvalidItems` drops
everything above the new last row **on the ground at the player's login point**. Items are
not destroyed — they become world `ItemDrop`s — but "on the ground where you logged in" can
mean in water or on a boat. **Rule: raise freely, never lower a character's `invrows` while
its rows above the target are occupied.**

### 4.6 Option 5 — bigger stacks

Two candidates, and they are not equivalent.

**`Fortis-ItemStacksRewrite` 1.0.2** — the mod whose config we already carry (§1.1).
MEASURED: embeds blaxxun `ServerSync` with `CurrentVersion = MinimumRequiredVersion =
"1.0.2"` and `ModRequired` left at the constructor default **false**, so clients without it
can join but a client on a different version is refused. Last updated **2025-05-27**, i.e.
**before the 1.0 boundary** — its Thunderstore dependency is `BepInExPack_Valheim-5.4.2202`
while we run **5.4.2350**. Its references and patch targets do resolve against our assembly,
so it is not obviously broken, but it is the only candidate here that has not been touched
since 1.0 shipped.

**`shudnal-ItemStacksItemWeights` 1.1.1** (2026-09-10) — the maintained alternative, same
author as ExtraSlots. MEASURED: uses `ConditionalConfigSync` with `CurrentVersion =
MinimumRequiredVersion = "1.1.1"` and **`set_ModRequired(true)`** (`ldc.i4.1`). **That is a
hard lockout: every client must have it, at the matching version, or be refused.**
Recommend against on that basis alone.

**The consequence for `KitSchema` either way.** MEASURED: `tools/jumpstart/worlds/derive.py`
reads `inventory.stack_table` — a path relative to `VALHEIM_ROOT`, currently
`profiles/ulfsland-dn/server-config/ItemStacksRewrite/fortis.mods.itemstacksrewrite.stacks.cfg`
— with `STACK_RE = ^([A-Za-z0-9_]+)_max_stack\s*=\s*(\d+)\s*$`, and divides item counts by
the value to get slot cost. Since all 847 values equal the mod's own defaults, that table
currently *is* vanilla. **Installing the mod and editing that file changes every kit's slot
arithmetic, and `derive.py build Ulfsland` must be re-run against the ACTIVE values.** Until
then the file is measuring vanilla and reporting it as measured, which is correct but
coincidental.

**Save risk: NONE for either.** Stack size is not persisted per item — `m_maxStackSize` is
`SharedData` read from `ObjectDB` at runtime. MEASURED: `Inventory::AddItem` clamps an
incoming stack with `Mathf.Min(stack, m_shared.m_maxStackSize)` at load, so removing a
bigger-stacks mod truncates over-sized stacks down to the vanilla maximum. **That is a real
item loss — the excess is discarded, not dropped** — and it is the one save-risk in this
option: it bites on *uninstall*, not on install. **[INFERENCE]** Mitigation is the obvious
one: never uninstall a stack mod without first breaking stacks down, or accept the
truncation.

### 4.7 Rejected: `sighsorry-InventorySlots` 1.4.17

MEASURED and worth recording because it is almost certainly what bit tonight. It embeds
`ServerSync` with `CurrentVersion = MinimumRequiredVersion = "1.4.17"` and
**`ModRequired = true`**, and it is the only package in the inventory space with versions
`1.4.16` (2026-09-14 15:46) and `1.4.17` (2026-09-14 18:41) three hours apart — matching the
"ServerSync refused a 1.4.16 client against a 1.4.17 server" symptom exactly.
`InventorySlots-1.4.11.zip` is sitting in `profiles/ulfsland-dn/manager-cache/packages/`.
It is also the heaviest patcher measured here (138 targets, 285,891 lines of IL), overlaps
20 targets with AdventureBackpacks and 12 with MultiUserChest, and is tagged
`AI Generated` on Thunderstore. **Do not install it.**

---

## 5. Recommendation, and what it does to the live work

**Install nothing. Use `invrows` for slots and `carryweightrate` for weight.**

The defence is three sentences. First, it is the only option that cannot produce a
version-handshake lockout, and this fleet has spent an hour tonight and 21 removed mods on
exactly that failure class — a capacity change should not buy back that risk. Second, it is
not a workaround: it is the mechanism the game itself uses, it is already live on this
server writing `invrows 4` into our characters, and Haldor already sells it, so using it
is *less* of a deviation than installing a mod, not more. Third, it decomposes the operator's
question correctly — "more room" is answered for free, and the one thing the vanilla keys
genuinely cannot do (get the backpack off the cape slot) is now a separate, smaller decision
that can be taken on its own merits with ExtraSlots as the measured-good answer.

Concretely, and in increasing order of commitment:

1. **Free, today, no change at all:** tell players Haldor sells inventory rows. MEASURED
   boundaries (§2.2, §3.2): one row for 1000 coins from **`pre-yagluth`** onward (40
   slots), the second for 2000 coins only on **`pre-kall`, `pre-fader`,
   `deepnorth-sandbox`** (48 slots). The four earliest presets get neither and stay at 32,
   so this lever does nothing for `pre-eikthyr` — which is, MEASURED, exactly the preset
   whose kit overburdened the player at 410 kg. For those tiers the weight dial in (2) is
   the only one that helps.
2. **One launch argument, one restart:** add `-setkey "carryweightrate 150"` (450 kg) or
   `200` (600 kg) to `SERVER_ARGS` in the generated
   `worlds/Ulfsland/<preset>/settings/world-modifiers.env`. Server-side, no client change.
3. **If we want rows granted rather than bought:** write `invrows N` into the new
   character's `m_uniques` on the ServerCharacters side. That is `ServerCharsEquip`'s
   territory, not mine, and §5.2 states what I measured about it.

And ahead of all three, the thing this research turned up that is not about capacity at
all: **AdventureBackpacks 2.0.1 → 2.0.4 outranks every option above on expected value**,
because it is the only change on the table that removes a documented item-duplication and
inventory-reset path that is live on this fleet (§1.2, §4.2). It is a lockstep
server + all-four-editions publish, but the fleet is *already* pinned in lockstep by that
mod's Jotunn attribute, so it costs no new coupling. `ModUpdateSweep` owns it; I am
recording the priority here because it was discovered while measuring the baseline for
this question and it would be wrong to bury it under a slot recommendation.

### 5.1 What this does to the nine jumpstart kits

`KitSchema` has already ruled on this and I agree with the ruling: **derive.py keeps
partitioning against 32 slots and a flat 300 kg with nothing equipped.** Everything in this
document — `invrows` upgrades, `carryweightrate`, Megingjord's +150, AdventureBackpacks'
+5…45 per item level — is **headroom, never budget.** A kit that only fits because a lever
is set is one authoring slip from regressing, and tonight already produced five
"check that confidently answered a question it was not measuring" bugs.

So: **the slot partition still binds, deliberately.** What changes is only the framing in
the generated headers — the 32-slot grid is the *spawn* state, not a hard ceiling, and the
`implied_chests_at_32_slots` overflow figure is a floor on chest usage rather than a
statement about what the player can ever carry. `KitSchema` was messaged with the numbers
rather than being left to find them here.

One correction that should reach whoever next edits `world.yaml`: the `slots: 32` citation
at line 89 points at `Azumatt.AzuExtendedPlayerInventory.cfg`, and that mod is not installed
anywhere on this fleet (§1.1). The honest citation is
`Humanoid..ctor -> new Inventory("Inventory", null, 8, 4)` plus `Player::OnSpawned` writing
`invrows 4`, and it is worth adding that `Inventory::SetHeight` has no clamp.

### 5.2 What this does to equip-on-spawn

**Nothing.** Confirmed with `ServerCharsEquip`: the recommendation leaves equipment slots
vanilla (5 equipment + hotbar), so an ordered `equip` list of prefab names with the slot
derived by the game from `m_itemType` is agnostic to how many rows the bag has.

Three measured facts that matter if an extra-slot mod ever *does* land, recorded here so
nobody re-derives them:

- Under **both** candidates, "equipped" is more than `ItemData.m_equipped`. ExtraSlots binds
  an item to a panel slot with `ItemData.m_customData["ExtraSlotsEquippedBy"]` and
  `["ExtraSlotsEquippedSlot"]`; EAQS 3.1.3 uses `["eaqs_slot"]`. An equip path that sets only
  `m_equipped` will leave the item equipped in the *vanilla* panel, not placed in the mod's
  slot. Both keys ride vanilla `ItemData::Save`, so they are writable server-side if needed.
- ExtraSlots detects us by GUID `org.bepinex.plugins.servercharacters` through
  `Chainloader.PluginInfos` (`ExtraSlots.HasServerCharactersActive`, also false under
  `ZNet.IsSinglePlayer`) and patches `Inventory::Save` and `FejdStartup::Start`. Our local
  patch does not change that GUID, only `ModVersion`/`MinimumRequiredVersion`.
- `Player::SetInventorySize` calls `Humanoid::DropInvalidItems()`, which drops
  out-of-bounds items on the ground. `ServerCharsEquip` established — and it is the right
  reading — that this does **not** bite the grant path, because `Player::OnSpawned` runs
  inside `Game::SpawnPlayer` strictly *before* the ServerCharacters postfix that grants the
  kit, and `Inventory::AddItem` places through `FindEmptySlot` within the *current* bounds.
  **The hazard is only for something that changes rows after the grant.** If `invrows` is
  ever written by the spawn path, write it **before** the grant, not after.

One correction to my own earlier note, and it should not be repeated: the claim that
`CharacterTemplate.yml` permits exactly three top-level keys and throws on a fourth is
**true of upstream ServerCharacters 1.4.17 only.** MEASURED in
`tools/servercharacters/patches/0001-template-quality-and-equip.patch`: our build switches
to `new DeserializerBuilder().IgnoreFields().IgnoreUnmatchedProperties().Build()` and widens
the catch to `YamlException`, with
`knownTemplateKeys = { "skills", "items", "quality", "equip", "contents", "spawn" }` —
six keys, and unknown top-level keys ignored and logged rather than fatal. So the template
format is not the obstacle to templating `invrows`; whether ServerCharacters *should* do
anything with it is `ServerCharsEquip`'s call.

### 5.3 Rollout cost

**For the recommendation:** one line in `world-modifiers.env` → `valheim.env` `SERVER_ARGS`
→ **one server restart** (~5 minutes to joinable on Ulfsland, per the generated header in
that file). **No client republish. No client-side change of any kind. A stale client
experiences nothing at all** — the new carry cap arrives through `ZoneSystem::RPC_GlobalKeys`
like any other world key, and rows arrive through the character's own unique keys, read by
the client's own vanilla `Player::OnSpawned`. **There is no lockout hazard.** That is the
whole point of the recommendation.

Worth naming explicitly, because it changes the comparison: **the recommendation is the
only option here that does not add to a lockstep publish the fleet is already committed
to.** MEASURED (§1.2), AdventureBackpacks 2.0.1 is Jotunn `EveryoneMustHaveMod` with
`VersionStrictness.Patch`, so the server and all four client editions are *already* pinned
together on its third version component. Every mod option below adds a second, independent
handshake on top of that one. The vanilla keys add none.

**For ExtraSlots, if the operator chooses it instead:** server deploy **plus republish of
all four client editions** (`ulfsland-flat`, `-dn`, `-vr`, `-admin`), and a repeat of that
on every ExtraSlots release. **Named lockout hazard:** because `CurrentVersion ==
MinimumRequiredVersion == "1.2.5"`, a client left on **any** other version is refused at
connect by `ConditionalConfigSync.VersionCheck` with `RemoteVersionTooOld` or
`LocalVersionTooOld`. Because `ModRequired = false`, a client with **no** ExtraSlots joins
fine — so the recovery for a locked-out player is *delete the mod*, which is strictly better
than the `ModRequired = true` case where the only recovery is obtaining the exact build.
ExtraSlotsCustomSlots 1.0.23 pins `shudnal-ExtraSlots-1.2.2`, so the pair must be version-
managed together. **[INFERENCE]** Gate any ExtraSlots install on a sandbox dedicated server
in `/tmp` with a throwaway world, exercising specifically: the three transpilers
(`InventoryGui::DoCrafting`, `Humanoid::EquipItem`, `Inventory::AddItem`), the
MultiUserChest overlap (§2.6), and a backpack equipped into the CustomSlots slot while a
cape is worn.

### 5.4 Reversal cost

| Option | What backing out does to existing characters |
| --- | --- |
| **Recommendation — `carryweightrate`** | `removekey carryweightrate`, or drop the `-setkey` argument and restart. `m_carryWeightRate` returns to its `1f` default. **Zero character impact.** A character over the old 300 kg becomes encumbered until it drops weight — annoying, not destructive. |
| **Recommendation — `invrows`** | Raising is free to reverse *only if* the extra rows are empty. Lowering with occupied rows drops those items on the ground at the player's login point on the next spawn (MEASURED: `OnSpawned → SetInventorySize → DropInvalidItems`, log `"Dropping {0} invalid positioned items."`). **Recoverable, never destroyed.** Lower in stages, or clear the rows first. |
| **ExtraSlots** | Items in extra slots are stranded, not destroyed. With an `invrows` key present — which all our characters have — the vanilla client drops them on the ground at login instead. `ExtraSlotsInventoryBackup` in `Player.m_customData` survives (a vanilla client round-trips unknown keys), so reinstalling restores state. Recovery is documented by the mod and is: run once without it, save, reinstall. |
| **EAQS 3.1.3** | Same shape — `eaqs_slot` per item, `eaqs_backup` in `m_customData`, both round-tripped by a vanilla client. |
| **`ItemStacksRewrite` / `ItemStacksItemWeights`** | **The one genuinely lossy reversal.** MEASURED: `Inventory::AddItem` clamps with `Mathf.Min(stack, m_shared.m_maxStackSize)` at load, so over-sized stacks are **truncated and the excess discarded**, not dropped. Break stacks down before removing. |
| **`InventorySlots` 1.4.17** | Not evaluated for reversal — rejected at the `ModRequired = true` gate. |

### 5.5 The ServerCharacters angle on save risk

The brief asks how server-side character storage changes the blast radius. MEASURED, it
changes it in our favour, in three specific ways.

1. **There is exactly one authoritative copy, and we own the machine it is on.**
   `config_merged/characters_local/Steam_<id>_<name>.fch` — full `PlayerProfile` files, and
   `Player::Save` writes `m_uniques` and `m_customData` into them. A client's local `.fch`
   is not the source of truth, so a player who installs or removes a slot mod locally cannot
   unilaterally rewrite the canonical character.
2. **Backups already exist and are ours.** MEASURED: alongside each `.fch` there is a
   `.fch.old`, timestamped `_backup_auto-YYYYMMDD-HHMMSS.fch` copies, and a `backups/`
   directory. A bad slot-mod interaction is a file restore, not a lost character. That is
   the single biggest reason the historical "uninstalling an inventory mod destroyed my
   character" story does not transfer to this fleet.
3. **But the blast radius is now fleet-wide rather than per-player.** The same server-side
   character is loaded by four different client editions. If one edition ships a slot mod
   and another does not, the *same* character is read by both — and the vanilla-client
   `DropInvalidItems` path (§2.3) then fires on every login from the edition without it.
   **[INFERENCE]** This is the argument that a slot mod, if adopted, must be `scope: shared`
   and present in **all four** editions simultaneously, never rolled out one edition at a
   time. It is also the second independent reason to prefer the vanilla keys, which every
   edition understands by construction.

---

## Appendix — how everything here was measured

Reproducible, read-only, and none of it touched a live tree.

```console
# Deployed game assembly, disassembled in a clean writable copy
$ mkdir -p /tmp/invopt/mg && cd /tmp/invopt/mg
$ cp .../Ulfsland/data/bepinex/valheim_server_Data/Managed/*.dll .
$ cp .../Ulfsland/data/bepinex/BepInEx/core/*.dll .
$ cp .../BepInEx/plugins/{ConditionalConfigSync,YamlDotNet,Jotunn}/*.dll .
$ monodis --output=assembly_valheim.il assembly_valheim.dll     # 581,271 lines
```

- **Candidate packages** resolved from the Thunderstore `package-listing-index`
  (13 chunks, 11,172 Valheim packages) and downloaded to `/tmp/invopt/dl`; each DLL
  disassembled in `/tmp/invopt/work` alongside the deployed dependency set.
- **Unresolved-reference check**: every `[assembly_valheim]Type::Member` token in each
  candidate's IL checked against a type/member index built from `assembly_valheim.il`, with
  `assembly_guiutils`, `assembly_utils`, `gui_framework` and
  `SoftReferenceableAssets` disassembled and indexed as well.
- **Harmony patch-target check**: `HarmonyPatch` custom-attribute blobs decoded from their
  raw bytes (`01 00` prolog, then .NET SerString length-prefixed values) and each
  `(type, method)` pair checked against the same index. This is the stronger of the two
  checks, because Harmony resolves these by name at runtime.
- **Game data** (Haldor's trade items, World Modifier slider values) read from
  `valheim_server_Data/StreamingAssets/SoftRef/Bundles` by reusing
  `tools/jumpstart/extract_prefab_names.py`'s `iter_bundle_blocks` UnityFS/LZ4/LZMA reader
  over all 796 bundles, then parsing the `Trader/TradeItem` binary layout taken from the
  assembly. Raw `grep` over the bundles finds nothing — the blocks are compressed, which is
  why the first `grep -rl invrows` came back empty and had to be redone properly.
- **Live server state** read from `Ulfsland/data/bepinex/BepInEx/plugins/`,
  `Ulfsland/config_merged/bepinex/` and `Ulfsland/config_merged/characters_local/`, with the
  config location confirmed by
  `docker inspect valheim-server-Ulfsland --format '{{range .Mounts}}…'` →
  `.../Ulfsland/config_merged -> /config`, and the in-container `BepInEx/config` symlink to
  `/config/bepinex`.
- **Deployed-vs-default config diffs** computed by matching each `Key = Value` line against
  the `# Default value:` comment the plugin itself wrote immediately above it.
- **Jotunn network-compatibility attributes** decoded the same way as the Harmony
  attributes, with the `CompatibilityLevel` / `VersionStrictness` enum values read from the
  deployed `Jotunn.dll` and the comparison semantics read from
  `Jotunn.Utils.ModModule::IsLowerVersion`. This is the check that revealed the "do
  nothing" baseline is not lockout-free, and it is the one I had initially omitted — a
  `ModRequired`/`ServerSync` search does **not** find a Jotunn-attributed lockout, because
  it is a custom attribute rather than a code path. Any future mod-compatibility audit on
  this fleet must check **both** mechanisms.

What was deliberately **not** measured, and should be before any install:

- Whether ExtraSlots' three transpilers still match 1.0.12's IL. Reference resolution cannot
  answer this. It needs a run.
- Whether the AdventureBackpacks 2.0.1 duplication path actually reproduces here, as
  opposed to being documented as fixed in 2.0.4. I read the changelog and confirmed
  `AzuCraftyBoxes` is installed; I did not attempt to trigger it.
- The ExtraSlots × MultiUserChest interaction on `Inventory::{AddItem, CanAddItem, Changed,
  MoveAll}` — the one patch overlap with no named compatibility class on either side.
