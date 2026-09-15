# Fleet mod sweep: four of these are repairs, not updates

Status: **proposal only.** Nothing was installed, deployed, published, enabled, disabled or
committed to write this. No profile manifest was edited, no `<world>/data/` or
`<world>/config_merged/` was written, no server was stopped or started, no release was cut, no
`bd` was run. `valheim-server-Ulfsland` was left running with admin mode as found. The only
writes were into `/tmp/modsweep/` and this file.

Everything marked **MEASURED** was measured on this host on 2026-09-15. Everything marked
**[INFERENCE]** is reasoning from those measurements, not itself a measurement.

---

## The headline: we are running four broken mods, and the fix is a version bump

22 packages have updates. That framing is wrong, and the reference graph says so. **Four of
the 22 are not "updates available" — they are repairs for mods that are broken on this fleet
right now**, measured against the assemblies the server actually runs, before installing
anything.

### 1. Six mods' build pieces are missing from the hammer. Two of them have a fix today.

**MEASURED.** Six deployed plugins reference `PieceTable::m_availablePieces` typed
`List<List<Piece>>` — the pre-1.0 shape. The 1.0.12 field of that name is a
`HashSet<Piece>`; the list-of-lists is now `m_availablePiecesByCategory`. The alias that would
restore the old name is `Valheim10Compatibility`'s `PieceTableAvailablePiecesAlias` bridge,
and **it is blocked on our profile**, from the live Ulfsland boot log:

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces
(List<List<Piece>>, alias of m_availablePiecesByCategory):
Valheim10Compatibility.Patcher.dll (AmbiguityGuard..cctor),
Valheim10Compatibility.Patcher.dll (Patcher.PatchPieceTableAlias)
look this field up by name through Type.GetField / Traverse.Field, which
throws AmbiguousMatchException the moment two fields share the name.
Left unaliased deliberately.
```

That is not a warning about a hypothetical. **MEASURED**, the *only* call sites of that field
in all six mods are two Harmony patches in the `PieceManager` library each of them vendors:

```
OdinCampsite.dll           PieceManager.PiecePrefabManager.UpdateAvailable_Prefix   (3 sites)
OdinCampsite.dll           PieceManager.PiecePrefabManager.UpdateAvailable_Postfix  (2 sites)
OdinsFoodBarrels.dll       …identical…
Basements.dll              …identical…
OdinsHorsePen.dll          …identical…
OdinUndercroft.dll         …identical…
RavenwoodRestorations.dll  …identical…
```

`PieceTable.UpdateAvailable` is the method the game calls to rebuild the hammer's available
piece list — on equip, and on every build-tab change. The patch body cannot JIT, because the
field it loads does not exist.

**What the player sees.** **[INFERENCE]**, from the above plus the author's own words: the
custom build pieces from those six mods do not appear in the build hammer, and refreshing the
build menu throws instead of refreshing. `OdinsFoodBarrels` 1.2.6 names it without hedging —
*"Fixed a build hammer crash caused by the recent Valheim update restructuring PieceTable's
per-category piece storage."* This is a silent, live, player-visible breakage: the barrels and
the campsite pieces are installed, paid for in load time, and unbuildable.

**MEASURED:** the new builds of the two that have updates — `OdinCampsite` 1.6.4 and
`OdinsFoodBarrels` 1.2.6 — contain **no reference to `m_availablePieces` at all**. They read
`m_availablePiecesByCategory` natively. The other four — `Basements`, `OdinsHorsePen`,
`OdinsUndercroft`, `RavenwoodRestorations` — have **no update available** and stay broken; see
the ledger in §8, because that is now a tracked number rather than a vague worry.

### 2. Crafting recipes are not showing. `AAA_Crafting 2.1.8` says so itself.

**MEASURED.** `AzuAntiArthriticCrafting.dll` 2.1.6, as deployed, has four unresolved
references against the running assembly set:

```
METHOD  ItemDrop/ItemData Inventory::AddItem(String,Int32,Int32,Int32,Int64,String,Boolean)
METHOD  ItemDrop/ItemData Inventory::AddItem(String,Int32,Int32,Int32,Int64,String,Vector2i,Boolean)
METHOD  UnityEngine.Vector3 ZInput::get_mousePosition()
TYPE    Fishlabs.GuiInputField   [ui_lib]
```

The two `AddItem` overloads are bridged by `Valheim10Compatibility`; `ZInput.get_mousePosition`
and `Fishlabs.GuiInputField` are bridged by **nothing** — `ui_lib.dll` is not in the server's
`Managed` directory at all. And 2.1.8's changelog reads *"Fixes for 1.0. Recipes not
showing."*, with 2.1.7 adding *"Remove/comment anything related to Auga. Causes edge case
issues with input fields in some other mods."* — which is exactly the `ui_lib` reference.

**What the player sees:** recipes missing from the crafting UI. This is the one an operator can
live with for weeks without connecting it to a mod, because a missing recipe looks like a
progression gate. **MEASURED:** 2.1.8 has **zero** unresolved references.

### 3. Two mods are only standing up because our own compatibility layer catches them

**MEASURED.** `OdinsFoodBarrels` 1.2.4 and `VikingsDoSwim` 1.4.1 both still call the pre-1.0
`Character.Message(MessageType, string, int, Sprite)`; `VikingsDoSwim` also calls the pre-1.0
`EffectList.Create(Vector3, Quaternion, Transform, float, int)`. Both of those members are
supplied at boot by a compatibility bridge — `Valheim10Compatibility`'s, as it happens, not
ours any more (§8 has the correction and why it matters). Their new builds call the 1.0
signatures directly and reference neither.

**This is the maintenance win worth naming:** every mod that stops needing a bridge is one
less thing pinned to a compatibility layer we have to keep working. A bridged call site is not
free — it is a member that exists only because a patcher put it there this boot, and it stops
existing the day the patcher is dropped, mis-orders, or gets blocked the way the
`PieceTable` alias just did. §8 measures how far we are from dropping ours entirely.

### What that means for tonight

The **coordinated group is worth a window on its own merits**, not as housekeeping. It
contains the build-hammer fix, the crafting-recipe fix, and — cleared by the sibling — the
ItemDrawers item-loss fix. §10 is the literal command sequence for one window.

---

## 1. What was measured, and how

Three throwaway harnesses, all in `/tmp/modsweep/`.

**(a) Reference-graph compatibility** (`refcheck.cs`, Mono.Cecil). Resolve every
`TypeReference` and every `MemberReference` of an assembly against the assemblies the server
actually runs, and list the failures. The technique is the one recorded in
`tools/modpatches/README.md` under "Checking compatibility by reference graph, not by version
number". It runs **before** installation, which is the whole point: a check that runs after is
worthless.

The reference set is a **single flat directory**, `/tmp/modsweep/world`, holding 247 DLLs — the
147 from `Ulfsland/data/bepinex/valheim_server_Data/Managed` plus `BepInEx/core`, then every
plugin DLL from `Ulfsland/data/bepinex/BepInEx/plugins` that did not collide by name. One
directory, deliberately:

> **MEASURED, and it nearly produced a sixth bug of the class we keep hitting.** Cecil 0.11 as
> shipped in `BepInEx/core` here mis-resolves when the resolver has more than one search
> directory — *including the same directory added twice*. `ItemDrawers.dll` 1.0.2 reports **46**
> unresolved with one search dir and **792** with `refs:refs`; the 746 extra are ordinary
> `assembly_valheim` members like `ItemDrop/ItemData::m_shared`. The first pass of this sweep
> produced counts in the hundreds for every package, which read like universal breakage and was
> noise. `refcheck.cs` now refuses a second directory and says why in a comment.

**Harness validation.** A negative is worth nothing unless the harness can produce a positive.
Run against `Smoothbrain-ServerCharacters 1.4.16` in the same workspace it reports **29**
unresolved: exactly the eight real faults `tools/modpatches/README.md` documents —
`PlayerProfile::m_playerStats`, `PlayerProfile::GetCharacterFolderPath(FileHelpers/FileSource)`,
the short `Inventory::AddItem`, `SEMan::AddStatusEffect(Int32,Boolean,Int32,Single)`,
`Character::Message(…,Sprite)`, `Game::SavePlayerProfile(Boolean)`,
`MessageHud::ShowMessage(…,Boolean)`, `Terminal/ConsoleCommand::.ctor(…)` — plus the 21-line
`System.Numerics.Vector` / `ImmutableArray.AddRange(ReadOnlySpan<T>)` baseline that Unity's own
BCL supplies at runtime. The sibling `ItemDrawersVerdict` reached the same 29 independently
with its own harness. So "zero unresolved" below is a measured negative, not a blind pass.

**(b) Network-gate scan** (`syncscan.cs`). Per assembly: which sync library, the literal
written to `ModRequired` and `MinimumRequiredVersion` by the *mod's own* code, and any Jotunn
`[NetworkCompatibility]` attribute with its arguments decoded. Run over the installed build and
the candidate build of all 22.

**(c) On-disk scope.** Plugin-directory presence in each world's deployed tree
(`<world>/data/bepinex/BepInEx/plugins/` and `<world>/config_merged/bepinex/plugins/`) for the
server side; presence in the operator's installed client edition
(`/mnt/vps-sync/profiles/Ulfsland--ulfsland-vr-flat-admin--flat/active/BepInEx/plugins/`) for
the client side. **Never** the manifest's `scope` field — §5.

Inventory came from the real surface: `tools/valheim_mods.py --profile P list --json` and
`check-updates` for all seven profiles.

---

## 2. The fleet, as it actually is

**MEASURED** — `tools/valheim_mods.py --profile ulfsland-dn profile list`:

| Profile | Packages | Serves worlds |
| --- | --- | --- |
| `admin` | 104 | **Doggerland, Hrafnheim, Storgard, Vangard** |
| `ulfsland-dn` | 106 | **Ulfsland** |
| `flat` | 97 | no server — client-edition primary |
| `vr` | 99 | no server — client-edition primary |
| `ulfsland-admin` | 105 | no server — client-edition primary |
| `ulfsland-flat` | 98 | no server — client-edition primary |
| `ulfsland-vr` | 100 | no server — client-edition primary |

Seven profiles, not four. The four non-Ulfsland worlds have **no profiles of their own**:
`<world>/mods/profiles/` is empty for all four and `<world>/mods/.active-mod-profile` reads
`admin` in every one, with pre-migration copies parked in
`<world>/mods/profiles.migrated/redesign-alpha/`. **One `admin` edit lands on four worlds.**

`release-targets.json` declares **20 published client editions**, 15 flat and 5 vr, across the
five worlds. Only `valheim-server-Ulfsland` is up right now (`docker ps`).

---

## 3. The per-package inventory

`installed` is identical across every profile carrying the package. `server` counts worlds whose
**deployed** tree contains the directory; `client` is presence in the installed admin client
edition. `gate` is the measured version-lock mechanism (§6).

| Package | Installed | Latest | Declared scope | **Server (disk)** | **Client (disk)** | Profiles | Gate | Unresolved old → new |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `Azumatt-AAA_Crafting` | 2.1.6 | 2.1.8 | shared | 5/5 | yes | 7 | ServerSync min→2.1.8 | **4 → 0** |
| `OdinPlus-OdinsFoodBarrels` | 1.2.4 | 1.2.6 | shared | 5/5 | yes | 7 | ServerSync min→1.2.6 | **2 → 0** |
| `OdinPlus-OdinCampsite` | 1.6.3 | 1.6.4 | shared | 5/5 | yes | 7 | ServerSync min→1.6.4 | **1 → 0** |
| `blacks7ar-VikingsDoSwim` | 1.4.1 | 1.4.2 | shared | 5/5 | yes | 7 | ServerSync min→1.4.2 | **2 → 0** |
| `Ross-RossItemDrawers` | 0.9.9 | **1.0.3** | shared | 5/5 | yes | 7 | Jotunn EveryoneMustHaveMod/Minor, **added** by 1.0.x | 0 → 0 |
| `Azumatt-AzuWearNTearPatches` | 1.0.8 | 1.0.9 | shared | 5/5 | yes | 7 | ServerSync min→1.0.9 | 0 → 0 |
| `Crystal-DeathPenalty` | 1.3.0 | 1.3.1 | shared | 5/5 | yes | 7 | CCS min→1.3.1.0, `ModRequired=true` | 0 → 0 |
| `Crystal-DigDeeper` | 1.3.0 | 1.3.1 | shared | 5/5 | yes | 7 | CCS min→1.3.1.0, `ModRequired=true` | 0 → 0 |
| `MidnightMods-ValheimArmory` | 1.29.3 | 1.31.0 | shared | 5/5 | yes | 7 | Jotunn ClientMustHaveMod/**Minor** | 0 → 0 |
| `blacks7ar-Endurance` | 1.1.2 | 1.1.3 | shared | 5/5 | yes | 7 | ServerSync min→1.1.3 | 0 → 0 |
| `blacks7ar-Herbalist` | 1.4.9 | 1.5.0 | shared | 5/5 | yes | 7 | ServerSync min→1.5.0, `ModRequired=true` | 0 → 0 |
| `blacks7ar-WieldEquipmentWhileSwimming` | 1.1.3 | 1.1.4 | shared | 5/5 | yes | 7 | ServerSync min→1.1.4 | 0 → 0 |
| `shudnal-HipLantern` | 1.1.7 | 1.1.8 | shared | 5/5 | yes | 7 | CCS min→1.1.8 | 0 → 0 |
| `tulivu-FearMe` | 0.2.0 | 1.0.0 | shared | 5/5 | yes | 7 | Jotunn gate **removed** by 1.0.0 | 0 → 0 |
| `Azumatt-TrueInstantLootDrop` | 1.0.3 | 1.0.4 | shared | 5/5 | yes | 7 | none | 0 → 0 |
| `Skarif-BuildOnShip` | 2.2.14 | 2.2.16 | shared | 5/5 | yes | 7 | none | 0 → 0 |
| `shudnal-ConditionalConfigSync` | 1.0.6 | 1.0.8 | shared | 5/5 | yes | 7 | none of its own | 0 → 0 |
| `MidnightMods-ImpactfulSkills` | 0.16.0 | 0.16.1 | shared | 5/5 | yes | 7 | Jotunn ClientMustHaveMod/**Minor** | 0 → 0 |
| `OdinPlus-OdinArchitect` | 1.7.2 | 1.7.5 | shared | 5/5 | yes | 7 | Jotunn EveryoneMustHaveMod/**Minor** | 0 → 0 |
| `95Shade-CarryWeightSkill` | 1.0.1 | 1.0.2 | **shared** | **0/5** | yes | 7 | none | 0 → 0 |
| `shudnal-ConfigurationManager` | 1.1.18 | 1.1.20 | client-only | **0/5** | yes | 3 | CCS min, server-absent → inert | 0 → 0 |
| `Vapok-AdventureBackpacks` *(held)* | 2.0.1 | 2.0.4 | shared | 5/5 | yes | 7 | Jotunn EveryoneMustHaveMod/**Patch** | 3 → 3 (unchanged) |

Also in the picture, **no update available**, nothing to do:

| Package | Installed | Upstream latest | Note |
| --- | --- | --- | --- |
| `Smoothbrain-ServerCharacters` *(held)* | **1.4.17** (Hexium) | 1.4.16 (Thunderstore) | Four Ulfsland profiles only; absent from `admin`/`flat`/`vr`. `check-updates` correctly offers nothing — it would be a downgrade into the build that must never be installed. |
| `OdinPlus-BlacksmithingExpanded` | 1.1.7 | 1.1.7 | Target of our only mod-DLL patch. §7, §9. |
| `Wubarrk-Valheim10Compatibility` | 1.4.0 | 1.4.0 | The compatibility preloader. Current, and pinned in effect — §8. |
| `ValheimModding-Jotunn` | 2.30.0 | 2.30.0 | Current; every Jotunn dependency below is satisfied. |
| `cybrp-ItemDrawers` | 1.2.6, **disabled** | 1.2.6 | Held — §7. |
| `shudnal-HarpoonExtended` | 1.1.12, **disabled** | 1.1.12 | Held — §7. |
| `EverybodyShim.zip` (custom) | ours | n/a | `tools/everybodyshim/`. No upstream. §8. |

**No candidate introduces a new dependency.** Every `dependencies` entry of all 22 candidates
is already installed at or above the required version, `ValheimModding-Jotunn-2.30.0` being the
highest ask and 2.30.0 what we run. `Crystal-DeathPenalty`/`DigDeeper` 1.3.1 newly call
`ConditionalConfigSync::set_ModRequirementMode`, API added in CCS **1.0.6** — the version we
already run, and the call resolved in the probe, so that ordering constraint is satisfied
before the window opens.

### One correction to the brief's version numbers

`check-updates` reported `Ross-RossItemDrawers 0.9.9 -> 1.0.2`. **MEASURED:** the live
`api/experimental/package/Ross/RossItemDrawers/` endpoint says latest is **1.0.3**, published
`2026-09-15T05:24:42Z`. The `api/v1/package/` bulk index that `tools/valheim_mods.py` reads
(`API`, line 19) was serving a snapshot roughly two hours stale. **[INFERENCE]** that is a
property of Thunderstore's cached bulk endpoint rather than our tooling, but it means a
`check-updates` answer is a **floor** on what is available, never a ceiling — worth knowing
before someone concludes a hotfix "isn't out yet". §10 step 2 turns this into a gate rather
than a pin, because `update` has no version argument and `add` refuses a package already in
the manifest — the mechanics are measured there.

---

## 4. Which profiles carry what

All 22 except one are in **all seven** profiles, so every bump is fleet-wide: five worlds and
20 client editions. The exception is `shudnal-ConfigurationManager`, in `admin`,
`ulfsland-admin` and `ulfsland-dn` only — the admin-audience profiles.

---

## 5. Declared scope is not on-disk scope — measured again, twice

- **`95Shade-CarryWeightSkill`** declares `"scope": "shared"` in **all seven** manifests.
  **MEASURED:** no `CarryWeightSkill` directory in the deployed tree of *any* of the five
  worlds, in either `config_merged/bepinex/plugins/` or `data/bepinex/BepInEx/plugins/`. It
  exists in `manager-cache/client/` and in the installed client edition. **Client-only in
  effect**: a carry-capacity skill running only on clients. Updating it needs a client
  republish and **no server deploy**.
- **`shudnal-ConfigurationManager`** declares `client-only`, and the disk agrees: 0/5. Listed
  because it changes the rollout (no deploy) and because its CCS gate is therefore inert (§6).

The reverse asymmetry is also real and is a trap in the other direction. The staged
`manager-cache/client/BepInEx/plugins/` tree of `ulfsland-dn` holds 88 directories against 95
server-side, and **20 packages are server-staged with no client-staged copy** —
`AAA_Crafting`, `AzuWearNTearPatches`, `OdinCampsite`, `TrueInstantLootDrop`, `VikingsDoSwim`,
`WieldEquipmentWhileSwimming`, `FearMe` and 13 more. **Absence from the client cache does not
mean absence from clients.** `cmd/profile-definition-builder/main.go:434` builds the published
definition from `source.Packages` **plus** `source.ClientOnlyPackages`, and the installed
client downloads each itself. Verified on the real artifact: the operator's
`ulfsland-vr-flat-admin` install has 118 plugin directories, named by full identifier, and
**every one of the 22 candidates is present**. So for client-side scope the authority is the
manifest package list as realised in an installed edition — which is where §3 measures it.

---

## 6. The three lockout gates, semantics read out of IL

There is no single "server-synced" flag. Three independent mechanisms are in play, they
disagree about what a version mismatch means, and the difference decides whether a bump is a
server-only change or a fleet window.

### 6a. Smoothbrain ServerSync — vendored into each mod

From `ServerSync.VersionCheck` in the deployed `AzuAntiArthriticCrafting.dll`:

```
get_MinimumRequiredVersion:  minimumRequiredVersion ?? (ModRequired ? CurrentVersion : "0.0.0")

IsVersionOk:  Version(CurrentVersion)         >= Version(ReceivedMinimumRequiredVersion)
           && Version(ReceivedCurrentVersion) >= Version(MinimumRequiredVersion)
```

1. **The check is bidirectional.** Both sides demand the peer meet their own minimum, and the
   failure strings are literally `" needs to be at least version "` and
   `" may not be higher than version "`. Every ServerSync mod here sets
   `MinimumRequiredVersion` to *its own version string* in its `..cctor` and bumps it each
   release, so a bump makes the requirement an **exact match** — not "client not older".
2. **`ModRequired` is beside the point when an explicit minimum is set.** It only chooses the
   *default* minimum. `blacks7ar-Endurance` has `ModRequired` unset (default `false`) and still
   version-locks, because it sets `MinimumRequiredVersion = "1.1.2"` explicitly. Classifying by
   `ModRequired` alone would have mislabelled **eight of the fifteen** lockstep packages as
   free — the seven ServerSync mods whose `ModRequired` is unset, plus `shudnal-HipLantern`,
   whose `ModRequired` is the literal `false`.

### 6b. shudnal ConditionalConfigSync — a shared library, four mods use it

`ConditionalConfigSync.VersionCheck` resolves the minimum identically
(`minimumRequiredVersion ?? (required ? CurrentVersion : "0.0.0")`) and its `GetFailure` runs
two `System.Version::op_LessThan` comparisons yielding `RemoteVersionTooOld` and
`LocalVersionTooOld` — bidirectional again. It adds a wire handshake (`ProtocolMissing`,
`ProtocolMismatch`, required `ProtocolVersion == 1`) and a server-side `ModRequirementMode`
policy override.

**MEASURED, and it is why CCS itself is safe to move:** `ProtocolVersion` is the literal `1` in
both deployed 1.0.6 and candidate 1.0.8, and 1.0.7 *reduces* false `HandshakeMissing`
rejections after transport recovery. CCS declares no `ModRequired` and no minimum for itself,
so **a CCS version skew does not by itself refuse a join** — only the consuming mods' minimums
do.

Consequence for `shudnal-ConfigurationManager`: it sets a CCS minimum, but client-side
`IsVersionOk()` returns `!ModRequired` when the server sent no handshake for that mod, and its
`ModRequired` is the literal `false`. Server has no copy (0/5), so the gate never fires.

### 6c. Jotunn `[NetworkCompatibility]` — five mods

Read out of the deployed `Jotunn.dll` 2.30.0, because **the enum values are not what older
Jotunn documentation says**:

```
CompatibilityLevel: NoNeedForSync=0  OnlySyncWhenInstalled=1  EveryoneMustHaveMod=2
                    ClientMustHaveMod=3  ServerMustHaveMod=4  VersionCheckOnly=5  NotEnforced=6
VersionStrictness : None=0  Major=1  Minor=2  Patch=3
```

and `ModModule.IsLowerVersion(base, compare, strictness)` compares `Major` at `>= Major`, also
`Minor` at `>= Minor`, also `Build` at `>= Patch`. So **`VersionStrictness.Minor` does not
compare the third component** — which splits this group cleanly:

| Package | Attribute | Bump | Verdict |
| --- | --- | --- | --- |
| `OdinPlus-OdinArchitect` | EveryoneMustHaveMod/Minor | 1.7.2 → 1.7.**5** | third component only → **no rejection** |
| `MidnightMods-ImpactfulSkills` | ClientMustHaveMod/Minor | 0.16.0 → 0.16.**1** | third component only → **no rejection** |
| `MidnightMods-ValheimArmory` | ClientMustHaveMod/Minor | 1.**29**.3 → 1.**31**.0 | minor moves → **stale clients refused** |
| `Vapok-AdventureBackpacks` | EveryoneMustHaveMod/**Patch** | 2.0.**1** → 2.0.**4** | Patch compares it → **stale clients refused** |
| `Ross-RossItemDrawers` | *added* by 1.0.x: EveryoneMustHaveMod/Minor | 0.9.9 → 1.0.3 | major+minor move, and the gate is new → **a 0.9.9 client against a 1.0.3 server is REFUSED at connect** |

`tulivu-FearMe` is the mirror image: 0.2.0 carries `EveryoneMustHaveMod/Minor` and **1.0.0
removes the attribute entirely**. After the move there is no gate. **[INFERENCE]** the
*transition* is still gated, because whichever side lags is still running 0.2.0's
`EveryoneMustHaveMod/Minor` against a 1.0.0 peer — so it belongs in the coordinated group for
this one window and leaves it afterwards.

---

## 7. Triage

### Safe — no lockout in either direction

| Package | Bump | Why safe | Needs |
| --- | --- | --- | --- |
| `95Shade-CarryWeightSkill` | 1.0.1 → 1.0.2 | **Client-only on disk** (0/5) despite declaring `shared`. No gate of any kind in either build. | republish only |
| `shudnal-ConfigurationManager` | 1.1.18 → 1.1.20 | Client-only, declared and measured; CCS gate inert. "Dropdown fixed"; 1.1.19 stops it breaking when another mod fails to load. | republish only |
| `Azumatt-TrueInstantLootDrop` | 1.0.3 → 1.0.4 | No gate in either build. Changelog: "1.0 update". | deploy |
| `Skarif-BuildOnShip` | 2.2.14 → 2.2.16 | No gate in either build; unresolved sets identical old and new. No changelog shipped. | deploy |
| `shudnal-ConditionalConfigSync` | 1.0.6 → 1.0.8 | No gate of its own; wire protocol `1` in both. 1.0.7 reduces spurious `HandshakeMissing` disconnects. | deploy |
| `MidnightMods-ImpactfulSkills` | 0.16.0 → 0.16.1 | Jotunn ClientMustHaveMod/**Minor**; only the third component moves. | deploy |
| `OdinPlus-OdinArchitect` | 1.7.2 → 1.7.5 | EveryoneMustHaveMod/**Minor**; third component only. Storage-size settings and localisation. | deploy |

"Safe" means *no join is refused if the fleet ends up mixed*. Five of the seven still run
server-side and still need a deploy to take effect there; only the two client-only ones skip it.

### Coordinated — server and all 20 client editions move together

| Package | Bump | Lockout hazard | Why it is worth the window |
| --- | --- | --- | --- |
| `Azumatt-AAA_Crafting` | 2.1.6 → 2.1.8 | ServerSync min `"2.1.6"`→`"2.1.8"`, bidirectional → exact match. A 2.1.6 client sees *"AAA Crafting needs to be at least version 2.1.8. You have version 2.1.6."* | **fixes "recipes not showing"** (4 unresolved → 0) |
| `OdinPlus-OdinsFoodBarrels` | 1.2.4 → 1.2.6 | ServerSync min `"1.2.4"`→`"1.2.6"` | **fixes the build-hammer crash** + drops a bridged `Character.Message` |
| `OdinPlus-OdinCampsite` | 1.6.3 → 1.6.4 | ServerSync min `"1.6.3"`→`"1.6.4"` | **fixes the build-hammer crash** |
| `blacks7ar-VikingsDoSwim` | 1.4.1 → 1.4.2 | ServerSync min `"1.4.1"`→`"1.4.2"` | drops two bridged call sites |
| `Ross-RossItemDrawers` | 0.9.9 → **1.0.3** | Jotunn EveryoneMustHaveMod/Minor, **new in 1.0.x**: a 0.9.9 client against a 1.0.3 server is refused at connect | **cleared by `ItemDrawersVerdict`** — fixes silent item loss with Quick Stack Store / ServersideQoL AutoStore / MultiUserChest / AutomaticFuel, all of which we run, plus the `GetIcon()` warnings the operator reported. Recommendation and evidence: `docs/proposed/2026-09-15-itemdrawers-verdict.md` |
| `Azumatt-AzuWearNTearPatches` | 1.0.8 → 1.0.9 | ServerSync min `"1.0.8"`→`"1.0.9"` | "1.0 Fixes" |
| `blacks7ar-Endurance` | 1.1.2 → 1.1.3 | ServerSync min `"1.1.2"`→`"1.1.3"`; `ModRequired` unset — the *minimum* locks it | skill-manager update |
| `blacks7ar-Herbalist` | 1.4.9 → 1.5.0 | ServerSync min `"1.4.9"`→`"1.5.0"` **and** `ModRequired=true`, so a client *without* the mod is refused too | skill-manager update |
| `blacks7ar-WieldEquipmentWhileSwimming` | 1.1.3 → 1.1.4 | ServerSync min `"1.1.3"`→`"1.1.4"` | 1.0 Deep North update |
| `shudnal-HipLantern` | 1.1.7 → 1.1.8 | CCS min `"1.1.7"`→`"1.1.8"`; `ModRequired=false` does **not** save it | utility-slot sync fixes |
| `Crystal-DeathPenalty` | 1.3.0 → 1.3.1 | CCS min `"1.3.0.0"`→`"1.3.1.0"` **and** `ModRequired=true` | changelog says *"no functional changes"* — the lockout is the entire cost. Lowest-value entry in the group; defensible to skip |
| `Crystal-DigDeeper` | 1.3.0 → 1.3.1 | identical to DeathPenalty | same; same call |
| `MidnightMods-ValheimArmory` | 1.29.3 → 1.31.0 | Jotunn ClientMustHaveMod/Minor, minor 29 → 31 | upgrader support for all VA weapons; config-change optimisation. ~29 MB download |
| `tulivu-FearMe` | 0.2.0 → 1.0.0 | installed 0.2.0 carries EveryoneMustHaveMod/Minor; 1.0.0 drops it, so whichever side lags still enforces | **riskiest behaviourally**: no changelog entry for 1.0.0 (its `CHANGELOG.md` stops at an unreleased `0.3.0`) and the reference set changed substantially — 62 references gone, 25+ new, i.e. a rewrite. Resolves clean, so not blocked; go last and play it before the window closes |

### Blocked — nothing is

**No candidate fails the pre-install compatibility check.** All 22 resolve every type and member
reference against the deployed 1.0.12 assembly set with **zero** unresolved, on a harness proven
to find all eight ServerCharacters 1.4.16 faults.

**No candidate invalidates a local patch.** `tools/modpatches/` contains exactly one mod-DLL
patcher, `blacksmithing_expanded_null_key.cs`, targeting `OdinPlus-BlacksmithingExpanded 1.1.7`
— which is Thunderstore latest, so `check-updates` offers no bump and there is nothing to
collide with. See §9 for the patch's measured state and the one sentence about per-world
byte divergence that belongs in three-months-from-now's memory.

### Deliberately held — visible in the plan as holds, not omissions

| Package | State | Reason, as found |
| --- | --- | --- |
| `Smoothbrain-ServerCharacters` | 1.4.17 from Hexium in the four Ulfsland profiles; Thunderstore latest is 1.4.16 | **A sibling is modifying our source build right now, and it will need its own lockstep rollout** — it is `ModRequired` on clients, so server and every client edition must move together, exactly like §10's coordinated group but on its own schedule. `tools/modpatches/README.md`: 1.4.16 has eight unresolvable members on 1.0.12 and "must never be installed". `settings-history` records four removal/restore cycles with reasons, the last *"operator's installed client is v1.0.1-297 whose embedded copy is empty; no client older than v1.0.1-299 can install this package."* **Not** in `disabled_packages`, contrary to the brief — it is enabled at 1.4.17 in all four Ulfsland profiles, and `disabled_packages` there holds only `shudnal-HarpoonExtended`. |
| `Vapok-AdventureBackpacks` | 2.0.1, update to 2.0.4 available | **A sibling is measuring its weight multiplier for the kit work.** Reported, not recommended. 2.0.4's headline is *"Item duplication and inventory reset when building or crafting with container-scanning mods (e.g. AzuCraftyBoxes, CraftFromContainers)"* and `AzuCraftyBoxes` + `AzuAutoStore` are on all seven profiles, so that path is live today — this hold has a real cost and should not linger. Jotunn EveryoneMustHaveMod/**Patch**, so it is a lockstep move whenever it lands. `InventoryOptions` has been messaged. |
| `cybrp-ItemDrawers` 1.2.6 | `disabled_packages` of **six** profiles (`admin`, `flat`, `vr`, `ulfsland-admin`, `ulfsland-flat`, `ulfsland-vr`), absent entirely from `ulfsland-dn` | Bead **`vhp-eeb`**, closed 2026-08-17: *"ItemDrawers blocked every login by breaking Jotunn mod compatibility."* `settings-history` records the `ulfsland-dn` removal with `--reason "disabled and unmaintained; replaced by Ross-RossItemDrawers"`. Already at Thunderstore latest, so there is no update to consider. |
| `shudnal-HarpoonExtended` 1.1.12 | `disabled_packages` of **all seven** | Disabled 2026-09-13 03:45–03:58 inside the mass 1.0-migration rebuild. `settings-history` captured **no `--reason`** — the `disable` verb takes none. Already at latest, so nothing to update. **Loose end, not an update:** `internal/app/assets/player-guide.md` still documents four HarpoonExtended keybinds (`T`, `Shift`, `Ctrl+T`, `Shift+Ctrl+T`) as active and lists it in the key-conflict table. Either the mod comes back or the guide stops promising it. **[INFERENCE]**, from the timing and the `profile-manifest.json.bak-bisect` files in every profile, this was a boot bisect never unwound. |
| Six `excluded_packages` entries | `ValheimVR-ValheimVR`, `NightOfGames-Huginn_Map`, `OdinPlus-CrystalLights`, `Smoothbrain-Jewelcrafting`, `Azumatt-AAABuildMenu`, `EchoesOfBunglas-New_Horizons_Treelines` (+ `MSchmoecker-VNEI` in `vr`) | Each carries its own `reason` in the manifest. Exclusions, not held updates. |

---

## 8. Shim-retirement ledger — how far we are from dropping our own compatibility layer

Nobody had measured this distance. Here it is, from resolving **all 113 deployed plugin DLLs**
in `Ulfsland/data/bepinex/BepInEx/plugins` against the reference set in one pass: 25 DLLs have
unresolved references, 54 distinct lines, of which 36 are genuine 1.0 API drift and the rest is
the known BCL/tooling baseline (`System.Numerics.Vector*`, `ImmutableArray.AddRange`,
multi-dimensional `Nullable<T>` array pseudo-methods, `HarmonyLib.ParameterByRefAttribute` from
the `0Harmony`/`0Harmony20` split, `ServersideQoL`'s own patcher-injected extension members,
and `winhttp.dll`, which is native).

### First, a correction to carry forward

**`Character.Message(MessageType, string, int, Sprite)` is not ours any more.** The deployed
`EverybodyShim.dll` — read out of
`Ulfsland/config_merged/bepinex/patchers/EverybodyShim.dll`, which is the copy the server image
rsyncs — contains exactly **four** forward rows:

| Table | Row | Exercised by the deployed set? |
| --- | --- | --- |
| AppendedOptional | `PlayerProfile.IncrementStat(PlayerStatType, float)` | **no** — no deployed plugin references it |
| AppendedRequired | *(empty)* | — |
| ReturnTypeWidened | `ZDO.GetSector() -> Vector2i` | **yes — 1 mod** |
| ReturnTypeWidened | `ZoneSystem.GetZone(Vector3) -> Vector2i` (static) | **no** — its own source comment records 9 mods referencing it when written; **MEASURED today: zero** |
| DelegatedReturn | `ZDOMan.GetPortals() -> List<ZDO>` via `GetPortalList` | **no** — `Cross_Server_Portals` is not in the deployed set |

Everything else — `Character.Message`, `SEMan.AddStatusEffect`, `Inventory.AddItem`,
`EffectList.Create`, `Inventory.Changed`, `MessageHud.ShowMessage`, `ItemDrop.OnCreateNew`,
`CookingStation.SpawnItem`, `ZoneSystem.GetZonePos` — is `Valheim10Compatibility`'s, as
`EverybodyShim.cs`'s own header says it should be ("five of those six are now DELETED"). An
earlier draft of this document credited those to our shim; it was wrong, and the distinction is
the whole point of a ledger.

### The ledger

| Supplier | Today | After the §10 updates | Mods affected today → after |
| --- | --- | --- | --- |
| **`EverybodyShim` (ours)** | **1** reference | **1** reference | `CreatureLevelAndLootControl` → unchanged |
| `Valheim10Compatibility` bridges | 26 references | **21** | 15 mods → **12** |
| **BLOCKED by `Valheim10Compatibility`** (broken today) | 6 references | **4** | 6 mods → **4** |
| Unbridged, no supplier at all | 3 references | **1** | 2 mods → **1** |

**Distance to dropping `tools/everybodyshim` entirely: one member, in one mod.**
`CreatureLevelAndLootControl` is the sole deployed plugin still referencing
`Vector2i ZDO::GetSector()`, and that is the one bridge `Valheim10Compatibility` refuses on
policy grounds (return-type-only overload). Three of our four table rows are dead weight
against the current plugin set and could be retired today without measurable effect — though
`GetZone` and `GetPortals` are cheap insurance if those mods come back, and the source already
flags `GetZone` as unexercised, so this is a judgement call, not a defect.

**Distance to dropping `Valheim10Compatibility`: 21 references across 12 mods** after the
updates, down from 26 across 15 — `AAA_Crafting`, `OdinsFoodBarrels` and `VikingsDoSwim` stop
needing it entirely. The remaining holders, by count:

- `CreatureLevelAndLootControl` — **6** bridged (`Character.Message`, `EffectList.Create`,
  `Inventory.AddItem`, `Inventory.Changed`, `CookingStation.SpawnItem`, `ZoneSystem.GetZonePos`)
  plus 1 unbridged (`Minimap::noForest`) and the one `EverybodyShim` row. **The single largest
  obstacle to dropping either layer, by a wide margin.**
- `ComfortTweaks`, `CraftyCartsRemake_1_0_Fix`, `PerfectPlacement`, `RavenwoodRestorations` — 2 each
- `BlacksmithingExpanded`, `Foraging`, `Lumberjacking`, `Mining`, `RavenwoodCurrency`,
  `VHModpackFix`, `Wisdom` — 1 each (all `Character.Message`, except `VHModpackFix`'s
  `EffectList.Create`)

**The four still broken after the window**, all on the blocked `PieceTable` alias, none with an
update available: `Basements`, `OdinsHorsePen`, `OdinsUndercroft`, `RavenwoodRestorations`.
Their build pieces are missing from the hammer for the same measured reason as §0.1. Three
routes exist and none of them is this sweep's to take: file the `AmbiguityGuard` self-block
report that `tools/everybodyshim/UPSTREAM-valheim10compatibility-selfblock.md` already drafts;
add the alias to our own shim, which `Valheim10Compatibility` would then log as "present
natively, no bridge needed"; or drop the four mods. **[INFERENCE]** the upstream report is the
cheapest, because the fix is a two-line self-exclusion in their guard and it repairs every
affected mod on every profile at once.

The one unbridged reference left afterwards is `CreatureLevelAndLootControl`'s
`Minimap::noForest`, a field 1.0 removed with no bridge anywhere. **[INFERENCE]** it is a
minimap colour, so the observable cost is cosmetic and confined to whatever CLLC draws with it.

---

## 9. The one local patch, and a per-world byte divergence worth naming

**MEASURED** against the upstream 1.1.7 archive (`md5 9bf9b3435f7651e8e09929caeabb81d6`):

| World | Deployed `BlacksmithingExpanded.dll` | State |
| --- | --- | --- |
| Ulfsland, Hrafnheim, Doggerland, Storgard | `9bf9b343…` | stock |
| **Vangard** | `79133afef773d2561ace233751cdcbc7`, with `BlacksmithingExpanded.dll.stock-1.1.7` beside it in **both** `config_merged` and `data` | **patched** |

**Write this down somewhere it will be found in three months: four worlds run stock
`BlacksmithingExpanded` bytes and Vangard runs hand-patched ones.** A per-world divergence in
mod *bytes* — not versions, not config, bytes — is obvious today because the `.stock-1.1.7`
sidecar is sitting right there, and baffling later when someone diffs two worlds that report
the same mod at the same version and behave differently. The reason is recorded:
`tools/modpatches/README.md` documents the `ArgumentNullException` in
`BlacksmithingItemData.Load()` that aborted Vangard's world load 13 times in a restart loop on
2026-09-13, and notes the patch only mattered for the one-time `WorldVersion 37`
`ConvertContainers` pass — all five worlds are at 41 now.

Two operational consequences:

1. **`deploy --apply` on Vangard reverts the patch.** It rebuilds `config_merged` from the
   profile cache. `hostops/portal_mod_admin.sh` already reports that rather than hiding it —
   `patch_reverted=`, `patch_stale=`, `patch_applied=`. Per the README the revert is acceptable
   now; the point is to decide it deliberately in §10 step 3 rather than discover it.
2. **The silent-failure hazard is routed, not ours.** If `1.1.8` ever ships, the Cecil patcher
   refuses by design and the repair is quietly lost. `TemplatePreservation` is adding a loud
   `patch_unapplied=` deploy outcome so that cannot happen silently; this sweep does not carry
   that work and only notes the dependency.

Nothing in this sweep touches that package: 1.1.7 is Thunderstore latest.

---

## 10. The command sequence for one maintenance window

### The ordering rule, and why it is the control

> **Deploy the servers before publishing the client editions. Never the reverse.**

**MEASURED.** `scripts/republish-profiles.sh:99-135` refuses to publish only when a running
world's server plugins "would change", and `server_plugin_change()` compares **a checksum of
directory names**, not versions. A version-only bump changes no directory name. So the guard
does **not** fire for any of these 22 updates, and a republish run before the deploy will
succeed against a running server — handing every player a client edition newer than the server
for all 14 lockstep packages in this window and locking the fleet out until the next one. The
guard is doing what it was written to do; it cannot see this class of change. The ordering
below is the control.

`deploy --apply` has its own guard and that one does bite: `cmd_deploy` calls
`require_stopped(world)`, then `require_release_cutover_complete`, then `validate_server_cache`,
which refuses if any cached server package disagrees with the manifest.

Throughout: `V=/media/big4/projects/game/valheim`, `R=<this repo>`, `export VALHEIM_ROOT=$V`.

### Step 0 — before the window. Servers up, no player impact. This is where the compatibility check runs.

```sh
export VALHEIM_ROOT=/media/big4/projects/game/valheim
cd "$R"

# 0.1  Re-read availability. check-updates is a FLOOR, not a ceiling (§3).
for P in admin flat vr ulfsland-admin ulfsland-dn ulfsland-flat ulfsland-vr; do
  echo "== $P"; python3 tools/valheim_mods.py --profile "$P" check-updates
done
python3 - <<'PY'
import json,urllib.request
u="https://thunderstore.io/api/experimental/package/Ross/RossItemDrawers/"
r=urllib.request.Request(u,headers={"User-Agent":"r2modman/3.1.57"})
d=json.load(urllib.request.urlopen(r,timeout=60))
print("RossItemDrawers latest:", d["latest"]["version_number"], d["latest"]["date_created"])
PY

# 0.2  THE COMPATIBILITY CHECK, BEFORE ANYTHING IS INSTALLED.
#      Reproduce §1: one flat reference dir, then resolve each candidate against it.
#      Do NOT add a second search directory (§1). Validate the harness on
#      ServerCharacters 1.4.16 first: it must report 29 unresolved, 8 of them real.
#      Accept a candidate only at ZERO new unresolved versus its installed build.

# 0.3  Read the crossed changelogs through the tool that fetches them.
python3 tools/valheim_mods.py --profile ulfsland-dn notes --lines 120

# 0.4  Read-only deploy plan per world. The ONLY way to catch cache_stale= and
#      patch_* outcomes BEFORE a window stops a world on them. Writes nothing.
hostops/portal_mod_admin.sh Ulfsland   ulfsland-dn deploy-plan
for W in Hrafnheim Doggerland Storgard Vangard; do
  hostops/portal_mod_admin.sh "$W" admin deploy-plan
done
```

Expect `patch_*` on Vangard (§9). Decide now whether the patch is reapplied afterwards.

### Step 1 — free group. Profile edits only; servers stay up; no player impact.

`update` writes the manifest and both cache sides and does **not** need a server stopped.

```sh
# CCS first: four mods hand their version handshake to it, and 1.0.7 reduces false
# HandshakeMissing rejections, so the rest of the window runs on the fixed handshake.
# Protocol stays 1 in both builds (§6b), so this is preference, not a constraint.
for P in admin flat vr ulfsland-admin ulfsland-dn ulfsland-flat ulfsland-vr; do
  python3 tools/valheim_mods.py --profile "$P" update shudnal-ConditionalConfigSync --apply
  python3 tools/valheim_mods.py --profile "$P" update Azumatt-TrueInstantLootDrop   --apply
  python3 tools/valheim_mods.py --profile "$P" update Skarif-BuildOnShip            --apply
  python3 tools/valheim_mods.py --profile "$P" update MidnightMods-ImpactfulSkills  --apply
  python3 tools/valheim_mods.py --profile "$P" update OdinPlus-OdinArchitect        --apply
  python3 tools/valheim_mods.py --profile "$P" update 95Shade-CarryWeightSkill      --apply
done

# Admin-audience profiles only (§4).
for P in admin ulfsland-admin ulfsland-dn; do
  python3 tools/valheim_mods.py --profile "$P" update shudnal-ConfigurationManager --apply
done
```

### Step 2 — coordinated group. Still profile edits only; still no player impact.

```sh
for P in admin flat vr ulfsland-admin ulfsland-dn ulfsland-flat ulfsland-vr; do
  # the repairs, first
  python3 tools/valheim_mods.py --profile "$P" update Azumatt-AAA_Crafting                  --apply
  python3 tools/valheim_mods.py --profile "$P" update OdinPlus-OdinsFoodBarrels             --apply
  python3 tools/valheim_mods.py --profile "$P" update OdinPlus-OdinCampsite                 --apply
  python3 tools/valheim_mods.py --profile "$P" update blacks7ar-VikingsDoSwim               --apply
  # cleared by ItemDrawersVerdict. See the gate note below: confirm the index has caught
  # up to 1.0.3 BEFORE running this, because `update` takes whatever latest() returns.
  python3 tools/valheim_mods.py --profile "$P" update Ross-RossItemDrawers              --apply
  # the rest
  python3 tools/valheim_mods.py --profile "$P" update Azumatt-AzuWearNTearPatches           --apply
  python3 tools/valheim_mods.py --profile "$P" update blacks7ar-Endurance                   --apply
  python3 tools/valheim_mods.py --profile "$P" update blacks7ar-Herbalist                   --apply
  python3 tools/valheim_mods.py --profile "$P" update blacks7ar-WieldEquipmentWhileSwimming --apply
  python3 tools/valheim_mods.py --profile "$P" update shudnal-HipLantern                    --apply
  python3 tools/valheim_mods.py --profile "$P" update Crystal-DeathPenalty                  --apply
  python3 tools/valheim_mods.py --profile "$P" update Crystal-DigDeeper                     --apply
  python3 tools/valheim_mods.py --profile "$P" update MidnightMods-ValheimArmory            --apply
  python3 tools/valheim_mods.py --profile "$P" update tulivu-FearMe                         --apply  # LAST
done
```

**MEASURED, and it constrains the ItemDrawers step:** `cmd_update` bumps to
`latest(index()[identifier])`, and `index()` is the cached `api/v1/package/` bulk endpoint —
the same snapshot that was two hours stale at 1.0.2 during this sweep (§3). There is no
version argument on `update`, and `add` refuses an already-present package outright
(*"Already present: Ross-RossItemDrawers"*), so **the tool offers no way to pin 1.0.3 by hand
for a package already in the manifest** — and `sync` reinstalls the manifest's pinned version
rather than bumping it. So the gate is: run step 0.1 and do not run the ItemDrawers line until
`check-updates` itself prints `-> 1.0.3`. If it still prints `1.0.2`, wait for the index rather
than reaching for `remove` + `add`, which would need a world, a reason, and a removal backup to
re-add a mod we are keeping. Confirm with `list --json` afterwards that all seven profiles read
`1.0.3` before step 3 deploys anything.

**Deliberately absent, and they are holds rather than omissions:**

```sh
# NOT in this window — Smoothbrain-ServerCharacters. A sibling is modifying our source
# build. It is ModRequired on clients, so it needs its own lockstep server + 20-edition
# rollout on its own schedule. Thunderstore's 1.4.16 must never be installed.
#
# NOT in this window — Vapok-AdventureBackpacks 2.0.1 -> 2.0.4. A sibling is measuring its
# weight multiplier for the kit work. Jotunn EveryoneMustHaveMod/Patch, so lockstep
# whenever it lands, and 2.0.4 fixes live item duplication with AzuCraftyBoxes/AzuAutoStore.
```

After step 2 the fleet is in the one state that must not be left standing — caches ahead of
both servers and clients. Steps 3 and 4 close it and belong in the same window.

### Step 3 — stop, deploy, start. Per world. This is the player-visible part.

Ulfsland first: it is the world with a live operator, and it is already up with **admin mode
armed**, which kicks every joiner, so its window is half-open already.

```sh
# --- Ulfsland (profile ulfsland-dn) ---
hostops/stop_valheim_server.sh Ulfsland
hostops/portal_mod_admin.sh   Ulfsland ulfsland-dn deploy     # == deploy --apply
hostops/start_valheim_server.sh Ulfsland
hostops/wait_valheim_server_ready.sh Ulfsland

# --- the admin profile serves FOUR worlds: one profile edit, four deploys ---
for W in Hrafnheim Doggerland Storgard Vangard; do
  hostops/stop_valheim_server.sh "$W"
  hostops/portal_mod_admin.sh   "$W" admin deploy
  hostops/start_valheim_server.sh "$W"
  hostops/wait_valheim_server_ready.sh "$W"
done
```

**Vangard needs one extra beat** (§9): the deploy reverts the `BlacksmithingExpanded` patch and
will say so via `patch_reverted=`. Either accept it — the README says that is fine now, since
`ConvertContainers` only runs on a `WorldVersion 37` world and all five are at 41 — or reapply
from `BlacksmithingExpanded.dll.stock-1.1.7` afterwards. Decide, do not discover.

**What a player experiences:** the world goes down and comes back several minutes later after
mod loading. Anyone connected is disconnected. Between step 3 and step 4 their old client
edition is **refused** by all 14 lockstep mods — that gap is why 3 and 4 are one window and not
one window per mod.

### Step 4 — republish all 20 client editions, immediately after, world by world.

```sh
N="Fleet mod sweep: build-hammer and crafting-recipe repairs, ItemDrawers 1.0.3. Update required."

# Ulfsland — 4 editions
hostops/portal_publish_profile.sh Ulfsland ulfsland-flat  flat "$N"   # ulfsland-vr-flat + ulfsland-non-vr
hostops/portal_publish_profile.sh Ulfsland ulfsland-admin flat "$N"   # ulfsland-vr-flat-admin
hostops/portal_publish_profile.sh Ulfsland ulfsland-vr    vr   "$N"   # ulfsland-vr

# Hrafnheim, Doggerland, Storgard, Vangard — 4 editions each
for W in Hrafnheim Doggerland Storgard Vangard; do
  hostops/portal_publish_profile.sh "$W" flat  flat "$N"   # <w>-vr-flat + <w>-non-vr
  hostops/portal_publish_profile.sh "$W" admin flat "$N"   # <w>-vr-flat-admin
  hostops/portal_publish_profile.sh "$W" vr    vr   "$N"
done
```

`portal_publish_profile.sh` resolves the single matching target out of `release-targets.json`,
so a caller cannot publish something an operator has not declared, and notes are mandatory
(8–500 characters, single line) because they become the release note. Publish each world
straight after its own deploy so no world sits with clients ahead of it.

**What a player experiences:** the portal offers a new edition, profile sync installs it, then
they can join. A player who declines stays locked out of all 14 lockstep mods — which is why
the release note says *"Update required"* rather than describing it as optional.

### Step 5 — verify, then play

```sh
hostops/portal_mod_admin.sh Ulfsland ulfsland-dn release-status
for W in Hrafnheim Doggerland Storgard Vangard; do
  hostops/portal_mod_admin.sh "$W" admin release-status
done
```

Then read one boot log and confirm the specific lines §0 and §8 predict:

- `BLOCKED PieceTable.m_availablePieces` is **still there** — expected; four mods still need it
  (§8) and `Valheim10Compatibility` still self-blocks.
- No `MissingFieldException` reaching `PieceManager.PiecePrefabManager.UpdateAvailable_*` from
  `OdinCampsite` or `OdinsFoodBarrels`.
- No `Character.Message(…,Sprite)` or `EffectList.Create(…,int)` bridge consumption attributed
  to `OdinsFoodBarrels` or `VikingsDoSwim`.

Then join with one real client and exercise the four claims: craft something whose recipe was
missing; equip the hammer and switch build tabs near a food barrel and a campsite piece; swim
while equipped; store into a drawer while a second client is nearby; and — because `FearMe
1.0.0` is a rewrite with no changelog — aggro something and watch it flee.

### If one window is too much

Steps 1 + 3 + 4 alone deliver both client-only bumps and the five free ones with **no lockout
risk at all**, in a short window. The coordinated fourteen then go in a second, announced
window. That costs one extra restart for a much smaller blast radius — but note the trade
honestly: the build-hammer and crafting-recipe repairs are in the *coordinated* group, so
splitting means the fleet keeps running four broken mods until the second window.
**[INFERENCE]** if the operator wants to play tonight rather than test, the better split is the
inverse: take the four repairs plus ItemDrawers in one coordinated window and defer
`tulivu-FearMe`, `Crystal-DeathPenalty` and `Crystal-DigDeeper`, which between them deliver a
rewrite with no changelog and two updates whose own changelogs say "no functional changes".

---

## 11. What was deliberately not done

No package was installed, added, removed, enabled or disabled. No profile manifest was written.
No `deploy` was run, with or without `--apply`. No release was published. No server was stopped,
started or restarted; `valheim-server-Ulfsland` was left up with admin mode as found. No
`<world>/data/` or `<world>/config_merged/` path was written. No `bd`. No `git commit`, no
`git push` — `git log` in `settings-history` was read for provenance only. `./scripts/check.sh`
was not run. Files owned by the five live siblings were not touched.

Candidate archives were downloaded into `/tmp/modsweep/dl` and extracted into
`/tmp/modsweep/{old,new}` purely to read their bytes; `/tmp/modsweep/world` is a copy of the
deployed reference assemblies. All of it is disposable.
