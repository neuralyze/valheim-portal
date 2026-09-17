# Mod update delta — measured 2026-09-16

**Read-only reconnaissance. Nothing was installed, deployed, restarted or committed.**
Every version below was read from a file on this machine or from the Thunderstore API at the
timestamp given. Where a claim is not a direct reading it is marked `[INFERRED]`.

## Instruments

| Instrument | What it measures | Path / endpoint |
|---|---|---|
| **M1 profile manifest** | the version the fleet *intends* to run | `$VALHEIM_ROOT/profiles/<p>/profile-manifest.json`, all 7 profiles |
| **M2 package manifest on disk** | the Thunderstore package version *actually extracted* | `<world>/config_merged/bepinex/plugins/<Pkg>/manifest.json` → `version_number`, all 5 worlds |
| **M3 assembly `BepInPlugin`** | the version the *DLL itself* declares at load | `.NET` metadata `CustomAttribute` → `BepInPlugin(guid, name, version)`, decoded with `dnfile` over all 118 plugin DLLs |
| **M4 profile cache sides** | whether a package reaches the **server**, the **client**, or both | `profiles/<p>/manager-cache/{server,client}/BepInEx/plugins/` directory listing |
| **M5 Thunderstore API** | latest published version + release date | `https://thunderstore.io/api/experimental/package/<ns>/<name>/` — queried 2026-09-16 |
| **M6 Thunderstore changelog** | the author's own words | `.../<ns>/<name>/<version>/changelog/` (`markdown`) |
| **M7 container state** | which world is actually live | `docker ps -a` |
| **M8 active profile** | which profile a world deploys from | `<world>/mods/.active-mod-profile` |

## Fleet shape (M1, M7, M8) — this drives the deploy order

- **7 profiles**, **113 distinct packages** in the union of `packages` + `client_only_packages` +
  `disabled_packages`, plus **1 custom package** (`EverybodyShim.zip`).
- **Zero version splits.** Every package that appears in more than one profile carries the *same*
  pinned version in all of them (M1). There is no per-profile drift to reconcile.
- Live plugin trees: Ulfsland **100** package dirs / 120 DLLs; Hrafnheim, Doggerland, Storgard,
  Vangard **95** / 115 each, and those four are **byte-identical in dir set** to one another (M2).
  Ulfsland's five extras are `DropCleaner`, `NeuralyzeWorldSeed`, `PerfectPlacement`,
  `ServerCharacters`, `Structure_Tweaks`.
- **Active profiles (M8):** `Ulfsland → ulfsland-dn`; `Hrafnheim`, `Doggerland`, `Storgard`,
  `Vangard → admin` (one shared profile, four worlds).
- **Only one container exists at all (M7):**

  ```
  valheim-server-Ulfsland   Up 43 minutes
  valheim-portal-portal-1   Up 43 hours
  ```

  `docker ps -a` lists **no container** for Hrafnheim, Doggerland, Storgard or Vangard. A
  server-side deploy to profile `admin` therefore costs **zero player downtime right now**;
  `require_stopped()` in `tools/valheim_mods.py:596` only fails on a *running* container.
  **Ulfsland is the only world a deploy would interrupt**, and it is the one being walked.

---

## THE TABLE — everything actionable, top to bottom

Risk key: **SAFE** = client-only or cosmetic, no save/world-data format involvement ·
**SERVER-SIDE** = changes server behaviour, needs the world stopped to deploy ·
**DANGEROUS** = touches world data, terrain format, location placement or save migration.

| # | Package | Installed (M1/M2/M3) | Latest (M5) | Released | Profiles | Reaches | Risk | Justification |
|---|---|---|---|---|---|---|---|---|
| 1 | `shudnal-ConfigurationManager` | 1.1.20 | **1.1.21** | 2026-09-15 | 3 (admin, ulfsland-admin, ulfsland-dn) | client cache only (0s/3c, M4) | **SAFE** | Client-side config GUI; never enters a server cache (M4). Changelog 1.1.21 in full: "Fixed `KeyCode` dropdowns" (M6). |
| 2 | `JereKuusela-Infinity_Hammer` | 1.83.0 | **1.84.0** | 2026-09-15 | 7 | 3s/7c (M4) | **SAFE** (admin tool) | "Adds new setting to snap zoom scaling at specific increments. Fixes favorites + tools + keep equip on death combination causing null reference exception." (M6). No world-data change. Note it *is* in three server caches, so a server deploy would carry it — but the mod is an admin build tool, not a world-format writer. |
| 3 | `Vapok-AdventureBackpacks` | 2.0.1 | **2.0.7** | 2026-09-16 23:29Z | 7 | 7s/7c | **SERVER-SIDE** | Skips 2.0.2→2.0.6. 2.0.4: "Fixed: Item duplication and inventory reset when building or crafting with container-scanning mods (e.g. AzuCraftyBoxes, CraftFromContainers)." and "Fixed: Prevented container-saving logic from writing to the player character's network ZDO data." (M6). **This fleet runs AzuCraftyBoxes** — the named-conflict fix applies directly. Writing to a player ZDO is player-save territory, so the *fix* is wanted but the deploy is a server event. 2.0.6 adds opt-out-able "Anonymous Telemetry … Defaults to enabled with auto-opt-in on launch" — set it off in config before deploy. |
| 4 | `Ross-RossItemDrawers` | 1.0.3 | **1.0.8** | 2026-09-16 19:56Z | 7 | 7s/7c | **SERVER-SIDE** | 1.0.5: "Fixed 'Try again' when taking items from a drawer nobody had touched. On a server a drawer commonly has no owner, and taking from one was refused outright with no way for the player to make it work." 1.0.6: "Taking items from a drawer no longer says 'Try again' on a server. Background bookkeeping was taking the drawer over mid-use" (M6). Both are dedicated-server defects; the fix only lands server-side. 1.0.8 rebinds the take-one/store-all keys to the mod's own `[Controls]` block, so client config changes too. |
| 5 | `ArgusMagnus-ServersideQoL` | 2.0.10 | **2.0.13** | 2026-09-16 15:41Z | 7 | 7s/7c | **SERVER-SIDE** | 2.0.13: "Fixed StackOverflowException (infinite recursion) [#248]". 2.0.12: "Fixed crash on startup on first time installations". 2.0.11: "New option to use a single config file for all SQoL mods: `UnifiedConfig`" and "New option to have a separate set of config files for each world: `ConfigPerWorld`" (M6). A StackOverflow fix in the mod that owns server-side entity rewriting is the strongest *reason to move* in this list; `ConfigPerWorld` changes where config is read from, so the per-world generated overlay must be re-checked after. |
| 6 | `ArgusMagnus-ServersideQoL_AutoStore` | 2.0.8 | **2.0.11** | 2026-09-16 09:27Z | 7 | 7s/7c | **SERVER-SIDE** | "Moved `AutoPickupMaxRange` config option from ContainerSigns to AutoStore" and "Support for the new core options `UnifiedConfig` and `ConfigPerWorld`" (M6). **Hard dependency: `ArgusMagnus-ServersideQoL-2.0.11`** (M5 `dependencies`) — we hold 2.0.10, so #5 and #6 must move together or #6 will refuse. A moved config key means the current `AutoPickupMaxRange` value is silently dropped unless re-homed. |
| 7 | `ZenDragon-Zen_ModLib` | 1.14.0 | **1.14.3** | 2026-09-16 03:36Z | 7 | 7s/7c | **SERVER-SIDE** | Library under seven Zen mods. 1.14.1: "fix: version check accepts both v1.0.12 (steam) and v1.0.11 (microsoft)"; 1.14.2/1.14.3 are gamepad and translation fixes (M6). Server-side because every dependent Zen plugin loads against it; a version skew between `Zen.ModLib` and `ZenWorldSettings` is what the dependency pin exists to prevent. |
| 8 | `ZenDragon-ZenWorldSettings` | 1.12.0 | **1.13.0** | 2026-09-15 08:23Z | 7 | 7s/7c | **SERVER-SIDE** | "replaced the Quests section with a generalized Progression system to handle a more broad per-player concept" and "fixed a bug with traders not being able to sell the new inventory expansion items introduced with the Deep North update" (M6). Replacing a Quests section with a per-player Progression system is a **player-data shape change**; there is no migration note. **Hard dependency `ZenDragon-Zen_ModLib-1.14.1`** (M5) — must follow #7. |
| 9 | `Wubarrk-Valheim10Compatibility` | 1.4.0 | **1.4.1** | 2026-09-16 12:20Z | 7 | 7s/7c | **SERVER-SIDE** (preloader patcher) | "## 2026-09-16 — 1.4.1: typed Harmony lookups hand a plugin's own reflection the bridge it named … The resolution hook redirected **every** typed `AccessTools.Method` / `AccessTools.DeclaredMethod(type, name, Type[])` lookup that landed on an append-only bridge to the longer native method. Right when the `MethodInfo` becomes a Harmony patch target …; wrong when the plugin uses it for `MethodInfo.Invoke`, because the arity no longer matches." (M6). This is a **preloader patcher** (`config_merged/bepinex/patchers/Valheim10Compatibility.Patcher.dll`, M2) — it rewrites game assemblies before anything loads. It cannot be hot-swapped and a bad one takes every mod down. Named-broken plugins are Historical Heritage 2.1.5 / Magic Supremacy 3.0.7, **neither of which this fleet runs** (M1) — so the fix is real but not urgent for us. |
| 10 | `Marlthon-OdinShip` | 0.7.9 | **0.8.1** | 2026-09-16 10:09Z | 7 | 7s/7c | **SERVER-SIDE** | Changelog for the target version, in full: "## 0.8.1 — Fixed custom AssetBundle sound effects so they follow Valheim's Effects (SFX) volume slider." (M6). The changelog **has no entry at all for 0.8.0**, the version we would pass through. A ship mod adds prefabs that become ZDOs in the world; passing through an undocumented minor with no notes is the risk, not 0.8.1's audio fix. |
| 11 | `OdinPlus-OdinsKingdom` | 1.5.6 | **1.5.8** | 2026-09-16 14:58Z | 7 | 7s/7c | **SERVER-SIDE** | Build-piece pack. "### Version 1.5.7 — **Fixes:** shader replacer not blocking rain effects and rain damaging roofs." (M6). Rain damaging roofs is live structural wear on already-built pieces. Note the changelog lists **1.5.7 twice and has no 1.5.8 entry** — the top half of the fix list for the version we would install is not documented. |
| 12 | `Balrond-balrond_constructions` | 1.4.4 | **1.4.5** | 2026-09-15 12:54Z | 7 | 7s/7c | **SERVER-SIDE** | Build-piece pack, **no 1.4.5 entry in the changelog** — the published changelog stops at 1.1.3 (M6). `[INFERRED]` from the package class: this author's own history includes "all building made of listed pieces prior to 0.0.2 will self destroy" and "reindexing of all build pieces with `_bal`" (M6, v1.0.1) — i.e. this author has previously shipped a piece-rename that destroyed existing builds. Undocumented version + demonstrated history of destructive reindexing = do not move this one blind. |
| 13 | `JamesJonesTV-Ravenwood_Currency` | 1.0.0 | **1.0.1** | 2026-09-15 21:12Z | 7 | 7s/7c | **SERVER-SIDE** | **No Thunderstore changelog endpoint** (404, M6); the only note is inside the README: "- 1.0.1: Updated for Deep North." (M6 readme). Classification **INFERRED** — it registers six coin items, a crafting station, six upgrade extensions and a storage chest, all of which are server-registered prefabs. |
| 14 | `RandyKnapp-EpicLoot` | 0.14.5 | **0.14.7** | 2026-09-16 19:18Z | 7 | 7s/7c | **SERVER-SIDE** | "**0.14.7** — Recompiled against current game build (fixes building destruction issues)" and "**0.14.6** — Destroying an enchanting table now refunds the full upgrade cost of every unlocked feature. Amounts above an item's stack size (Surtling Cores, berries, mushrooms, fish) were previously lost" (M6). "Fixes building destruction issues" on a world holding 15 built sites is a *reason to move*, and the loot/enchant tables are server-authoritative. |
| 15 | `Skarif-BuildOnShip` | 2.2.16 | **3.0.112** | 2026-09-16 18:23Z | 7 | 7s/7c | **DANGEROUS** | Major-version jump 2.x→3.x. The changelog entries for the three newest published versions read, verbatim and in full: "## v3.0.112 — nothing", "## v3.0.111 — nothing", "## v3.0.11 — nothing" (M6). The only substantive 3.x note is "## v3.0.0 — Server player on ship sync fix". The changelog also contains a "## v3.1.0 — Fix black screen after poral teleport + object work optimization" section for a version that **does not exist** on Thunderstore (`/3.1.0/` → HTTP 404, M5). This mod parents built pieces to a moving ship ZDO; a major version with a fictional changelog entry and three "nothing" releases is not something to put in front of built structures. |
| 16 | `warpalicious-More_World_Locations_AIO` | 5.1.0 | **5.1.1** | 2026-09-15 18:39Z | 7 | 7s/7c | **DANGEROUS** | "5.1.1 — Added five new Swamp locations: `MWL_SwampTempleSmall1`, `MWL_SwampTemple2`, `MWL_SwampShrine1`, `MWL_SwampSanctuary1`, and `MWL_SwampComplex1`. — To add the new locations to an existing world, install Upgrade World and run: `locations_add MWL_SwampTempleSmall1,MWL_SwampTemple2,MWL_SwampShrine1,MWL_SwampSanctuary1,MWL_SwampComplex1 start` — Fixed shipping-port discovery persistence when ValheimEnforcer saves player custom data during logout." (M6). The author's own upgrade path is an explicit `locations_add` **write into the existing world's zone data**. This package supplies the 26 portal shrines and the mod houses under 10 of 18 Stenvik pads and 5 of 9 treehouse pads. `JereKuusela-Upgrade_World` 1.82.0 is already installed in 3 profiles (M1), so the destructive command is one line away from being runnable. |

### Two hard ordering constraints, read from `dependencies` (M5)

- `ServersideQoL_AutoStore 2.0.11` **requires** `ArgusMagnus-ServersideQoL-2.0.11`. We hold 2.0.10.
  → rows **5 and 6 move in the same window, core first**.
- `ZenWorldSettings 1.13.0` **requires** `ZenDragon-Zen_ModLib-1.14.1`. We hold 1.14.0.
  → rows **7 and 8 move in the same window, ModLib first**.

Every other update's declared dependencies are already satisfied by what is pinned (M5 vs M1):
BepInEx 5.4.2350, Jotunn 2.30.0, JsonDotNET 13.0.4, YamlDotNet 16.3.1 all meet or exceed
every requested floor.

### Not in the table, because there is nothing to move

**96 of 113 packages are already at the latest published version** (M1 vs M5). They are listed in
Appendix A for completeness.

---

## Version disagreements — manifest vs DLL vs Thunderstore

Three different numbers can disagree: what the profile pins (M1), what the package manifest on
disk says (M2), and what the DLL announces to BepInEx at load (M3). M1 and M2 agree for **every
package in every world** — there is no stale extraction anywhere. The disagreements are all
M1/M2 vs **M3**, and they split into two kinds.

### Real disagreements — the loaded plugin is not the version we pinned

| Package dir | Pinned + on disk (M1/M2) | DLL announces (M3) | Reading |
|---|---|---|---|
| `DragoonCapes` | 1.3.5 | **1.3.2** | Package 1.3.5 ships a 1.3.2 assembly. Author did not bump `BepInPlugin`; the package contents are what we asked for. |
| `Judes_Equipment` | 2.3.0 | **2.2.4** | Same shape: `JudesEquipment.dll` assembly version is also 2.2.4.0. |
| `DropCleaner` | 1.1.0 | **1.0.5** | Ulfsland only. |
| `Ballista_Infinite_Ammo` | 1.0.1 | **1.0.0** | GUID is `kuuhaku.ValheimMod`, assembly name `Valheim-mod`. |
| `BuildOnShip` | 2.2.16 | **3.0.0** | The **DLL is ahead of the package**. The 2.2.16 package contains an assembly that calls itself 3.0.0. Read row 15 with this in mind: some of "3.x" may already be loaded. |
| `CraftyCartsRemake_1_0_Fix` | 1.0.0 | **3.1.12** (GUID `Azumatt.CraftyCarts`) | Expected — this package is a repack of Azumatt CraftyCarts 3.1.12 under a fix wrapper. |

None of these is a deployment error; all six are author-side version hygiene. They matter because
`check-updates` compares package numbers, so **none of these will ever be reported as a gap**.

### `Smoothbrain-ServerCharacters` — pinned to a version that does not exist upstream

- Pinned and extracted **1.4.17.1** in all four `ulfsland-*` profiles, server **and** client
  caches, and in Ulfsland's live tree; the DLL announces 1.4.17.1 (M1/M2/M3 — fully consistent).
- **Thunderstore's newest is 1.4.16, published 2025-05-02** (M5). Probing the API directly:
  `/1.4.16/` → **200**, `/1.4.17/` → **404**, `/1.4.17.1/` → **400**.
- This is by design, not drift: `tools/valheim_mods.py:57-63` registers it as
  `LOCAL_BUILD_SOURCE`, built from `internal/servercharacters/embedded/ServerCharacters.zip`, with
  the retirement note *"when Thunderstore publishes 1.4.17, delete LOCAL_BUILD_PACKAGES …"*.
- **Consequence for this report:** ServerCharacters is the one package where "latest published"
  is *behind* us. It appears as `TS older` in Appendix A. Do not let a future `update` run
  downgrade it.

---

## Client/server cache asymmetry — the finding that outranks the updates

Measured with **M4**: the directory listings of `manager-cache/server/BepInEx/plugins` and
`manager-cache/client/BepInEx/plugins`, for all seven profiles.

`install_sides()` at `tools/valheim_mods.py:604-625` states the contract: *"`'client'` always."*
The cache does not match that contract.

### 15 shared-scope packages are in every profile's SERVER cache and in NO profile's client cache

`Ballista_Infinite_Ammo`, **`Basements`**, `BiggerChests`, `BlacksmithingExpanded`,
`ComfortTweaks`, `DragoonCapes`, `Foraging`, `Judes_Equipment`, `Mask`,
`No_New_Recipe_Notifications`, **`OdinsHorsePen`**, **`OdinsUndercroft`**, `PotteryBarn`,
`SullysAutoPinner`, `Wisdom` — plus the loose file `Valheim.DisplayBepInExInfo.dll`.

All 15 are declared `"scope": "shared"` in M1. All 15 are present in all seven server caches and
absent from all seven client caches. The cache directories were both last written
**2026-09-15 18:06:32**, four milliseconds apart, so this is not a half-finished sync — it is the
state a completed sync left behind.

**Three of those 15 are exactly three of the four mods reported this morning with dead build
pieces.** `[INFERRED]` — the measurement is the cache asymmetry; the causal claim that a
client with no `Basements.dll` cannot show Basements pieces is inference, but it is the only
hypothesis consistent with all three appearing in the same list. The fourth,
`RavenwoodRestorations`, is present in **both** caches (M4) and therefore has a different cause.

### 3 shared-scope packages are in every CLIENT cache and no server cache

`AstralBeauty-SpearFishing` 2.0.1, `ComfyMods-ComfyLadders` 1.1.0, `hoskope-RhythmicRepairs`
1.0.0 — all declared `shared` (M1), none present in any server cache, none present in any world's
live plugin tree (M2). `Ulfsland/config_merged/bepinex/org.bepinex.plugins.spearfishing.cfg`
exists with no DLL to read it. All three are already at the latest published version (M5), so
there is nothing to update — the gap is deployment, not version.

### 2 excluded packages are still in the client cache

`AAABuildMenu` and `New_Horizons_Treelines` sit in every profile's client cache (M4) while
appearing in `excluded_packages` in every profile manifest (M1).

### 1 orphan plugin on the live server

`Ulfsland/config_merged/bepinex/plugins/NeuralyzeWorldSeed/NeuralyzeWorldSeed.dll` —
5,120 bytes, written **2026-09-15 20:41**, no `manifest.json`, `BepInPlugin` GUID
`neuralyze.worldseed` version 1.0.0 (M2/M3). It is in **no** profile manifest, in **no** cache,
and in **no** other world. `cmd_deploy` rebuilds `plugins/` wholesale from the cache
(`tools/valheim_mods.py:1808-1840`), so **the next deploy to Ulfsland deletes it** — it would be
reported by `report_dropped()` and then gone. If it is load-bearing, it needs a home in
`manual-mods/` before any Ulfsland deploy.

---

## Step 3 — the five known-broken mods: has anything shipped?

Queried M5 + M6 on 2026-09-16. **Answer for all five: no. Nothing has shipped.**

| Mod | Installed | Latest published | Date of that release | Anything new? |
|---|---|---|---|---|
| `95Shade-CarryWeightSkill` | 1.0.2 | **1.0.2** | 2026-09-15 00:28Z | **No.** We are on the latest build. The most recent release is *yesterday* and it is the broken one. The package has **no changelog endpoint at all** (404, M6) — the README documents only mechanics ("Train it by sprinting or swimming… Each level adds +3 max carry weight"), nothing about Harmony or `InventoryGui`. Package created 2026-04-06, last updated 2026-09-15 (M5). There is no upstream fix and no evidence one is being worked on. `PatchAll` failing on `InventoryGui::RepairOneItem` stands. |
| `OdinPlus-Basements` | 1.4.1 | **1.4.1** | **2026-02-08** 01:39Z | **No.** Seven months without a release. Last changelog line: "1.4.1 — Localization Manager Update - Fixed 0.221.10 game update" (M6) — i.e. it was last fixed for game build 0.221.10, and this fleet is on buildid 25253791 (Valheim 1.0.x). **But see the cache asymmetry above: `Basements` is missing from every client cache**, which is a local cause that needs no upstream release. |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | **1.1.0** | **2026-02-05** 03:10Z | **No.** Seven months. **No changelog endpoint** (404, M6). Also missing from every client cache. |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | **1.3.3** | **2026-07-24** 16:00Z | **No.** Almost two months. Changelog exists but stops well before 1.3.3 (M6). Also missing from every client cache. Historical note from its own changelog, relevant to any future move: "# Version 1.0.4 - Had to move the undercrofts to prevent some issues, I am sorry if you had builds in the previous version" — this author has relocated placed structures before. |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | **1.0.1** | 2026-09-08 18:50Z | **No.** Eight days, we are current. **No changelog endpoint** (404, M6). Unlike the other three it **is** present in both cache sides (M4), so the client/server asymmetry does not explain it. Its cause is still open. |

Also checked, since it was on the morning's broken list:
`OdinPlus-BlacksmithingExpanded` — installed **1.1.7**, latest **1.1.7**, released **2026-03-10**
(M5). **No.** Six months, nothing shipped; the `ConvertContainers` `WorldVersion 37` crash has no
upstream fix and our patch stays reverted.

---

## One deprecation

`fedorovdgap-AzuCraftyBoxes` 1.8.18 is flagged **`is_deprecated: true`** on Thunderstore (M5),
and 1.8.18 (2026-09-10) is still its latest — so there is no version to move to, only a package
to eventually leave. It is installed in all 7 profiles and live in all 5 worlds. It is also the
mod named in `AdventureBackpacks` 2.0.4's duplication fix (row 3). Replacement analysis is not
in this document's scope; flagged here because a deprecated package with no successor version is
a standing decision, not an update.

---

## Deploy order if you approve everything

`cmd_deploy` refuses on a running container (`tools/valheim_mods.py:596`) and rebuilds
`config_merged/bepinex/plugins` wholesale from the profile cache, then hoists preloader patchers
(`:1863-1871`). Client-side delivery is a separate path from the profile's client cache.

### Wave 0 — free, no world stops, do first

Hrafnheim, Doggerland, Storgard and Vangard have **no container at all** (M7). They all run
profile `admin` (M8). Every server-side row below can be applied to those four worlds **without
stopping anything**, because there is nothing running to stop.

### Wave 1 — client-safe, no server deploy at all

| Row | Package | Note |
|---|---|---|
| 1 | `shudnal-ConfigurationManager` 1.1.20 → 1.1.21 | client cache only; touches no server |
| 2 | `JereKuusela-Infinity_Hammer` 1.83.0 → 1.84.0 | admin build tool; can ride Wave 2 for the three profiles that carry it server-side |

### Wave 2 — one server window, profile `admin` (Hrafnheim + Doggerland + Storgard + Vangard)

Free right now per Wave 0. Apply as one batch so the four worlds move together:

1. `ArgusMagnus-ServersideQoL` 2.0.10 → **2.0.13** *(must precede 2)*
2. `ArgusMagnus-ServersideQoL_AutoStore` 2.0.8 → **2.0.11**
3. `ZenDragon-Zen_ModLib` 1.14.0 → **1.14.3** *(must precede 4)*
4. `ZenDragon-ZenWorldSettings` 1.12.0 → **1.13.0**
5. `Vapok-AdventureBackpacks` 2.0.1 → **2.0.7**
6. `Ross-RossItemDrawers` 1.0.3 → **1.0.8**
7. `RandyKnapp-EpicLoot` 0.14.5 → **0.14.7**
8. `Wubarrk-Valheim10Compatibility` 1.4.0 → **1.4.1** *(preloader patcher — verify it is hoisted into `config_merged/bepinex/patchers/` after deploy; `tools/valheim_mods.py:1845-1871`)*
9. `Marlthon-OdinShip` 0.7.9 → **0.8.1**
10. `OdinPlus-OdinsKingdom` 1.5.6 → **1.5.8**
11. `JamesJonesTV-Ravenwood_Currency` 1.0.0 → **1.0.1**

Before the window: re-home `AutoPickupMaxRange` (moved from ContainerSigns to AutoStore, row 6)
and decide `UnifiedConfig` / `ConfigPerWorld` (row 5), because both change where config is read.
Set AdventureBackpacks' `Enable Anonymous Telemetry` to off (row 3), which defaults on.

### Wave 3 — one server window, profile `ulfsland-dn` (Ulfsland only)

**This is the only stop that costs the operator anything.** Same eleven rows as Wave 2, applied
after Wave 2 has been observed good on the four quiet worlds. Do it in the same window as the
`NeuralyzeWorldSeed` decision — the deploy will delete that orphan (see above).

### Held back pending verification — do not batch these

| Row | Package | What must be verified first |
|---|---|---|
| 12 | `Balrond-balrond_constructions` 1.4.4 → 1.4.5 | No changelog for 1.4.5 exists. Get the 1.4.5 piece list and diff prefab names against 1.4.4 before installing — this author has shipped a `_bal` reindex that self-destroyed existing builds. Until that diff exists, this is an unquantified risk to built structures. |
| 15 | `Skarif-BuildOnShip` 2.2.16 → 3.0.112 | Three consecutive "nothing" changelogs and a changelog section for a version that 404s. Confirm on a throwaway world that 3.0.112 loads existing ship-parented pieces, and reconcile the fact that our 2.2.16 package already contains a DLL announcing 3.0.0. |
| 16 | `warpalicious-More_World_Locations_AIO` 5.1.0 → 5.1.1 | **DANGEROUS.** The author's own upgrade path writes into existing world zone data via `Upgrade_World`'s `locations_add`. Before touching it: (a) full world backup of Ulfsland; (b) confirm all 26 portal shrines and the mod houses under the 10 Stenvik and 5 treehouse pads still resolve on 5.1.1; (c) decide explicitly **not** to run `locations_add` — the five new Swamp locations are optional and skipping them leaves the existing world untouched. 5.1.1's only non-additive change is a shipping-port-discovery persistence fix that names `ValheimEnforcer`, which this fleet does not run (M1). **There is no reason to take this risk today.** |

### Not a deploy, but higher value than any row above

The **client cache asymmetry** costs nothing to investigate and no world stop, and it is the only
measured candidate cause for three of the four dead-build-piece reports. A `sync` run that
repopulates `manager-cache/client` for the 15 missing shared packages should be evaluated before
any of Wave 2 — because if it is the cause, three "broken mods with no upstream fix" stop being
broken without a single upstream release.

---

## Appendix A — full union inventory, 113 packages

Columns: pinned version (M1) · `BepInPlugin` version of the loaded DLL (M3, `—` = not deployed to
a server) · latest published (M5) · that release's date · gap · profile count (of 7) ·
which cache sides carry it (M4) · how many of the 5 worlds have it live (M2).

| Package | Pinned | DLL says | Latest | Released | Gap | Prof | Reaches | Live |
|---|---|---|---|---|---|---|---|---|
| `95Shade-CarryWeightSkill` | 1.0.2 | 1.0.2 | 1.0.2 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `Advize-PlantEasily` | 2.2.0 | 2.2.0 | 2.2.0 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `Advize-PlantEverything` | 1.21.2 | 1.21.2 | 1.21.2 | 2026-09-12 | same | 7 | both (7s/7c) | 5/5 |
| `ArgusMagnus-ServersideQoL` | 2.0.10 | 2.0.10 | 2.0.13 | 2026-09-16 | **2.0.13** | 7 | both (7s/7c) | 5/5 |
| `ArgusMagnus-ServersideQoL_AutoStore` | 2.0.8 | 2.0.8 | 2.0.11 | 2026-09-16 | **2.0.11** | 7 | both (7s/7c) | 5/5 |
| `AstralBeauty-SpearFishing` | 2.0.1 | — | 2.0.1 | 2026-07-11 | same | 7 | client-only (0s/7c) | 0/5 |
| `Azumatt-AAA_Crafting` | 2.1.8 | 2.1.8 | 2.1.8 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `Azumatt-AzuWearNTearPatches` | 1.0.9 | 1.0.9 | 1.0.9 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `Azumatt-FastLink` | 1.4.8 | — | 1.4.8 | 2025-09-18 | same | 7 | client-only (0s/7c) | 0/5 |
| `Azumatt-PerfectPlacement` | 1.2.2 | 1.2.2 | 1.2.2 | 2025-03-06 | same | 3 | NEITHER (0s/0c) | 1/5 |
| `Azumatt-TrueInstantLootDrop` | 1.0.4 | 1.0.4 | 1.0.4 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `Balrond-balrond_constructions` | 1.4.4 | 1.4.4 | 1.4.5 | 2026-09-15 | **1.4.5** | 7 | both (7s/7c) | 5/5 |
| `blacks7ar-Endurance` | 1.1.3 | 1.1.3 | 1.1.3 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `blacks7ar-Herbalist` | 1.5.0 | 1.5.0 | 1.5.0 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `blacks7ar-Mask` | 1.0.3 | 1.0.3 | 1.0.3 | 2025-03-11 | same | 7 | server-only (7s/0c) | 5/5 |
| `blacks7ar-VikingsDoSwim` | 1.4.2 | 1.4.2 | 1.4.2 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `blacks7ar-WieldEquipmentWhileSwimming` | 1.1.4 | 1.1.4 | 1.1.4 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `blacks7ar-Wisdom` | 1.0.5 | 1.0.5 | 1.0.5 | 2026-02-05 | same | 7 | server-only (7s/0c) | 5/5 |
| `ChangosOF-DropCleaner` | 1.1.0 | 1.0.5 | 1.1.0 | 2025-07-21 | same | 1 | both (1s/1c) | 1/5 |
| `cjayride-AdvancedSigns` | 0.5.0 | — | 0.5.0 | 2026-09-10 | same | 7 | client-only (0s/7c) | 0/5 |
| `coemt-No_New_Recipe_Notifications` | 1.0.2 | 1.0.2 | 1.0.2 | 2025-08-10 | same | 7 | server-only (7s/0c) | 5/5 |
| `ComfyMods-ComfyLadders` | 1.1.0 | — | 1.1.0 | 2026-04-16 | same | 7 | client-only (0s/7c) | 0/5 |
| `ComfyMods-PotteryBarn` | 1.21.0 | 1.21.0 | 1.21.0 | 2026-07-18 | same | 7 | server-only (7s/0c) | 5/5 |
| `Crystal-DeathPenalty` | 1.3.1 | 1.3.1.0 | 1.3.1 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `Crystal-DigDeeper` | 1.3.1 | 1.3.1.0 | 1.3.1 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `cybrp-ItemDrawers` | 1.2.6 | — | 1.2.6 | 2026-08-06 | same | 6 | NEITHER (0s/0c) | 0/5 |
| `Cytraen-BiggerChests` | 1.1.0 | 1.1.0 | 1.1.0 | 2025-04-08 | same | 7 | server-only (7s/0c) | 5/5 |
| `DeathMonger-PauseMyServer` | 1.4.1 | 1.4.1 | 1.4.1 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `denikson-BepInExPack_Valheim` | 5.4.2350 | — | 5.4.2350 | 2026-09-09 | same | 7 | both (7s/7c) | 5/5 |
| `Disboard-Ballista_Infinite_Ammo` | 1.0.1 | 1.0.0 | 1.0.1 | 2025-05-07 | same | 7 | server-only (7s/0c) | 5/5 |
| `DragonMotion-VHModpackFix` | 1.1.24 | 1.1.24 | 1.1.24 | 2026-07-23 | same | 7 | both (7s/7c) | 5/5 |
| `fedorovdgap-AzuCraftyBoxes` | 1.8.18 | 1.8.18 | 1.8.18 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `geekstreet-BackpacksVRFix` | 1.0.1 | — | 1.0.1 | 2026-06-13 | same | 2 | client-only (0s/2c) | 0/5 |
| `geekstreet-CLLCVRFix` | 2.0.1 | — | 2.0.1 | 2026-06-02 | same | 2 | client-only (0s/2c) | 0/5 |
| `geekstreet-EpicLootVRFix` | 1.0.17 | — | 1.0.17 | 2026-06-13 | same | 2 | client-only (0s/2c) | 0/5 |
| `GoldenJude-Blacksmiths_tools` | 3.0.0 | 3.0.0 | 3.0.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `GoldenJude-Judes_Equipment` | 2.3.0 | 2.2.4 | 2.3.0 | 2025-03-06 | same | 7 | both (7s/1c) | 5/5 |
| `Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock` | 1.4.15 | 1.4.15 | 1.4.15 | 2026-09-12 | same | 7 | both (7s/7c) | 5/5 |
| `HappyDragoon-DragoonCapes` | 1.3.5 | 1.3.2 | 1.3.5 | 2025-07-06 | same | 7 | server-only (7s/0c) | 5/5 |
| `hoskope-RhythmicRepairs` | 1.0.0 | — | 1.0.0 | 2026-06-15 | same | 7 | client-only (0s/7c) | 0/5 |
| `JamesJonesTV-Ravenwood_Currency` | 1.0.0 | 1.0.0 | 1.0.1 | 2026-09-15 | **1.0.1** | 7 | both (7s/7c) | 5/5 |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | 1.0.1 | 1.0.1 | 2026-09-08 | same | 7 | both (7s/7c) | 5/5 |
| `JereKuusela-Infinity_Hammer` | 1.83.0 | 1.83 | 1.84.0 | 2026-09-15 | **1.84.0** | 7 | both (3s/7c) | 5/5 |
| `JereKuusela-Item_Stand_All_Items` | 1.26.0 | 1.26 | 1.26.0 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `JereKuusela-Server_devcommands` | 1.113.0 | 1.113 | 1.113.0 | 2026-09-12 | same | 7 | both (3s/7c) | 5/5 |
| `JereKuusela-Structure_Tweaks` | 1.37.0 | 1.37 | 1.37.0 | 2026-09-10 | same | 3 | client-only (0s/3c) | 1/5 |
| `JereKuusela-Upgrade_World` | 1.82.0 | 1.82 | 1.82.0 | 2026-09-13 | same | 3 | both (3s/3c) | 5/5 |
| `JereKuusela-Wearable_Trophies` | 1.11.0 | 1.11 | 1.11.0 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `JereKuusela-World_Edit_Commands` | 1.77.0 | 1.77 | 1.77.0 | 2026-09-13 | same | 7 | both (3s/7c) | 5/5 |
| `L4zerShark_Team-CLLCCompatibility` | 1.0.1 | — | 1.0.1 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `lunarbin-Cross_Server_Portals` | 1.3.0 | 1.3.0 | 1.3.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `Marlthon-OdinShip` | 0.7.9 | 0.7.9 | 0.8.1 | 2026-09-16 | **0.8.1** | 7 | both (7s/7c) | 5/5 |
| `MaxGerman-Skip_Intro_Video` | 1.0.2 | — | 1.0.2 | 2026-09-09 | same | 7 | client-only (0s/7c) | 0/5 |
| `MidnightMods-ImpactfulSkills` | 0.16.1 | 0.16.1 | 0.16.1 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `MidnightMods-ValheimArmory` | 1.31.0 | 1.31.0 | 1.31.0 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `MSchmoecker-MultiUserChest` | 0.6.2 | 0.6.2 | 0.6.2 | 2026-09-12 | same | 7 | both (7s/7c) | 5/5 |
| `MSchmoecker-VNEI` | 0.17.6 | — | 0.17.6 | 2026-09-10 | same | 5 | client-only (0s/5c) | 0/5 |
| `Neobotics-RequipMe` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `Neobotics-RuinsMaker` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-11 | same | 7 | both (3s/7c) | 5/5 |
| `Neobotics-TagConnectedPortals` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `OdinPlus-Basements` | 1.4.1 | 1.4.1 | 1.4.1 | 2026-02-08 | same | 7 | server-only (7s/0c) | 5/5 |
| `OdinPlus-BlacksmithingExpanded` | 1.1.7 | 1.1.7 | 1.1.7 | 2026-03-10 | same | 7 | both (7s/1c) | 5/5 |
| `OdinPlus-OdinArchitect` | 1.7.5 | 1.7.5 | 1.7.5 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `OdinPlus-OdinCampsite` | 1.6.4 | 1.6.4 | 1.6.4 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `OdinPlus-OdinsFoodBarrels` | 1.2.6 | 1.2.6 | 1.2.6 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | 1.1.0 | 1.1.0 | 2026-02-05 | same | 7 | server-only (7s/0c) | 5/5 |
| `OdinPlus-OdinsKingdom` | 1.5.6 | 1.5.6 | 1.5.8 | 2026-09-16 | **1.5.8** | 7 | both (7s/7c) | 5/5 |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | 1.3.3 | 1.3.3 | 2026-07-24 | same | 7 | server-only (7s/0c) | 5/5 |
| `Ostrix-AdvancedTerrainModifiersCompatible` | 1.4.8 | 1.4.8 | 1.4.8 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `RandyKnapp-EpicLoot` | 0.14.5 | 0.14.5 | 0.14.7 | 2026-09-16 | **0.14.7** | 7 | both (7s/7c) | 5/5 |
| `Ross-RossItemDrawers` | 1.0.3 | 1.0.3 | 1.0.8 | 2026-09-16 | **1.0.8** | 7 | both (7s/7c) | 5/5 |
| `shudnal-CheatDeath` | 1.0.8 | 1.0.8 | 1.0.8 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `shudnal-ConditionalConfigSync` | 1.0.8 | 1.0.8 | 1.0.8 | 2026-09-15 | same | 7 | both (7s/7c) | 5/5 |
| `shudnal-ConfigurationManager` | 1.1.20 | — | 1.1.21 | 2026-09-15 | **1.1.21** | 3 | client-only (0s/3c) | 0/5 |
| `shudnal-HarpoonExtended` | 1.1.12 | — | 1.1.12 | 2025-03-09 | same | 7 | NEITHER (0s/0c) | 0/5 |
| `shudnal-HipLantern` | 1.1.8 | 1.1.8 | 1.1.8 | 2026-09-14 | same | 7 | both (7s/7c) | 5/5 |
| `shudnal-LongshipUpgrades` | 1.0.20 | 1.0.20 | 1.0.20 | 2026-09-12 | same | 7 | both (7s/7c) | 5/5 |
| `shudnal-ProtectiveWards` | 2.0.10 | 2.0.10 | 2.0.10 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `shudnal-TradersExtended` | 2.0.2 | 2.0.2 | 2.0.2 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `sighsorry-AdminQoL` | 1.1.3 | — | 1.1.3 | 2026-09-09 | same | 3 | client-only (0s/3c) | 0/5 |
| `sighsorry-LoadTimeProfiler` | 1.3.2 | — | 1.3.2 | 2026-09-09 | same | 3 | client-only (0s/3c) | 0/5 |
| `Skarif-BuildOnShip` | 2.2.16 | 3.0.0 | 3.0.112 | 2026-09-16 | **3.0.112** | 7 | both (7s/7c) | 5/5 |
| `Smoothbrain-ComfortTweaks` | 3.3.10 | 3.3.10 | 3.3.10 | 2026-07-18 | same | 7 | server-only (7s/0c) | 5/5 |
| `Smoothbrain-CreatureLevelAndLootControl` | 4.6.4 | 4.6.4 | 4.6.4 | 2025-05-26 | same | 7 | both (7s/7c) | 5/5 |
| `Smoothbrain-Foraging` | 1.0.10 | 1.0.10 | 1.0.10 | 2026-02-05 | same | 7 | server-only (7s/0c) | 5/5 |
| `Smoothbrain-Lumberjacking` | 1.0.6 | 1.0.6 | 1.0.6 | 2026-02-05 | same | 7 | both (7s/7c) | 5/5 |
| `Smoothbrain-Mining` | 1.1.6 | 1.1.6 | 1.1.6 | 2026-02-05 | same | 7 | both (7s/7c) | 5/5 |
| `Smoothbrain-ServerCharacters` | 1.4.17.1 | 1.4.17.1 | 1.4.16 | 2025-05-02 | TS older | 4 | both (4s/4c) | 1/5 |
| `southsil-SouthsilArmor` | 3.1.9 | 3.1.9 | 3.1.9 | 2026-09-12 | same | 7 | both (7s/7c) | 5/5 |
| `SurplusTradingCo-SullysAutoPinner` | 1.4.0 | 1.4.0 | 1.4.0 | 2025-06-21 | same | 7 | server-only (7s/0c) | 5/5 |
| `TastyChickenLegs-AutomaticFermenters` | 1.1.2 | 1.1.2 | 1.1.2 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `TastyChickenLegs-AutomaticFuel` | 1.5.1 | 1.5.1 | 1.5.1 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `TastyChickenLegs-BedRules` | 2.0.6 | 2.0.6 | 2.0.6 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `TastyChickenLegs-CandlesForever` | 1.0.5 | 1.0.5 | 1.0.5 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `TastyChickenLegs-TimedTorchesStayLit` | 1.4.0 | 1.4.0 | 1.4.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `Tristan-ValheimRcon` | 1.6.2 | 1.6.2 | 1.6.2 | 2026-09-10 | same | 3 | both (3s/3c) | 5/5 |
| `tulivu-FearMe` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `turbero-ForsakenPowersPlusRemastered` | 2.0.4 | 2.0.4 | 2.0.4 | 2026-09-10 | same | 7 | both (7s/7c) | 5/5 |
| `ValheimModding-Jotunn` | 2.30.0 | 2.30.0 | 2.30.0 | 2026-09-09 | same | 7 | both (7s/7c) | 5/5 |
| `ValheimModding-JsonDotNET` | 13.0.4 | 1.0.0 | 13.0.4 | 2025-06-15 | same | 7 | both (7s/7c) | 5/5 |
| `ValheimModding-YamlDotNet` | 16.3.1 | 1.0.0 | 16.3.1 | 2025-06-15 | same | 7 | both (7s/7c) | 5/5 |
| `Vapok-AdventureBackpacks` | 2.0.1 | 2.0.1 | 2.0.7 | 2026-09-16 | **2.0.7** | 7 | both (7s/7c) | 5/5 |
| `VentureValheim-Venture_Area_Repair` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-11 | same | 7 | both (7s/7c) | 5/5 |
| `warpalicious-More_World_Locations_AIO` | 5.1.0 | 5.1.0 | 5.1.1 | 2026-09-15 | **5.1.1** | 7 | both (7s/7c) | 5/5 |
| `wocky-CraftyCartsRemake_1_0_Fix` | 1.0.0 | 3.1.12 | 1.0.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `Wubarrk-Valheim10Compatibility` | 1.4.0 | 1.4.0 | 1.4.1 | 2026-09-16 | **1.4.1** | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-Zen_ModLib` | 1.14.0 | 1.14.0 | 1.14.3 | 2026-09-16 | **1.14.3** | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenBreeding` | 1.0.0 | 1.0.0 | 1.0.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenItemStands` | 1.1.0 | 1.1.0 | 1.1.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenPath` | 1.1.0 | 1.1.0 | 1.1.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenRaids` | 1.2.0 | 1.2.0 | 1.2.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenRedecorate` | 1.5.0 | 1.5.0 | 1.5.0 | 2026-09-13 | same | 7 | both (7s/7c) | 5/5 |
| `ZenDragon-ZenWorldSettings` | 1.12.0 | 1.12.0 | 1.13.0 | 2026-09-15 | **1.13.0** | 7 | both (7s/7c) | 5/5 |

Reading `NEITHER (0s/0c)`: the package is in no profile cache on either side.

- `Azumatt-PerfectPlacement` 1.2.2 — nevertheless **live in Ulfsland** (M2/M3). Its cache copy sits
  in `manager-cache/.removed-enforcing-admin-tools/PerfectPlacement` in `admin`, `ulfsland-admin`
  and `ulfsland-dn` (M4), so it was deliberately pulled out of the active cache while remaining
  deployed. **The next Ulfsland deploy removes it**, exactly like `NeuralyzeWorldSeed`.
- `cybrp-ItemDrawers` 1.2.6 and `shudnal-HarpoonExtended` 1.1.12 — both in `disabled_packages`
  (M1) and live in no world (M2). Correctly absent.

`JereKuusela-Structure_Tweaks` 1.37.0 reads `client-only (0s/3c)` but is live in Ulfsland (M2);
a second copy also sits in `.removed-enforcing-admin-tools/Structure_Tweaks` in those same three
profiles (M4), and it carries `"scope": "client-only"` in M1 while a server copy is deployed.
Same next-deploy exposure.

Custom (not a Thunderstore package): `EverybodyShim.zip`, sha256 `0ca96a39b367bfa2b6f9d5b9a781d8a92a10e6579a708a234b97a15ac15a40e0`, 10,164 bytes, enabled in all 7 profiles (M1 `custom_packages`); deployed as `config_merged/bepinex/patchers/EverybodyShim.dll` in all 5 worlds (M2).

Orphan (on disk, in no manifest): `NeuralyzeWorldSeed` 1.0.0, Ulfsland only (M2/M3).

Excluded in all 7 profiles (M1 `excluded_packages`): `ValheimVR-ValheimVR` 0.9.2100, `NightOfGames-Huginn_Map` 1.0.5, `OdinPlus-CrystalLights` 1.1.8, `Smoothbrain-Jewelcrafting` 2.0.1, `Azumatt-AAABuildMenu`, `EchoesOfBunglas-New_Horizons_Treelines`; plus `MSchmoecker-VNEI` excluded in `vr` only.

Disabled (M1 `disabled_packages`): `shudnal-HarpoonExtended` 1.1.12 in all 7; `cybrp-ItemDrawers` 1.2.6 in 6 (not in `ulfsland-dn`) — superseded by `Ross-RossItemDrawers`, which is the one that is live.
