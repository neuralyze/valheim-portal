# Mod compatibility register

Packages this project removed from a heavily modded Valheim profile, and why. It exists
so the next person does not have to repeat the bisection.

Read the status before reusing anything here:

* **Confirmed** — the failure was reproduced, the mechanism identified, and removing the
  package fixed it.
* **Bisected out** — removed while narrowing down a failure, as part of a suspected
  class. The stated reason is the hypothesis that justified removal, not proof that this
  particular package was faulty. Some were probably innocent.
* **Superseded** / **Housekeeping** — no defect; replaced, rescoped, or simply unwanted.

Everything below was observed on a Valheim dedicated server with a roughly 114-package
client profile including ValheimVR. A package that misbehaves here may be perfectly fine
in a smaller or unmodded setup.

## Failure patterns worth knowing

These generalise beyond the specific packages and are the useful part of this document.

### A throwing Harmony postfix silently disables everything ordered after it

Harmony runs postfixes as a chain. If one throws, the rest of the chain is abandoned. The
exception surfaces as a single log line and the game keeps running, so the symptom
appears nowhere near the cause.

The concrete case: a signs package threw `NullReferenceException` from its
`ZNetScene.Awake` postfix, caused by a null TMP font in its own font setup. It sat at
roughly position 17 of about 40 postfixes on that method. Every later postfix was
skipped, including the one that re-enables the `ZNetScene` component. `ZNetScene.enabled`
stayed false, `ZNetScene.Update` never ran, no ZDO was ever instantiated, `IsAreaReady`
never became true, and **first spawn hung forever** — with nothing in the log pointing at
signs.

If first spawn hangs on a modded client, look for a thrown postfix on `ZNetScene.Awake`
before suspecting the networking layer.

### Admin and "fix" tools that patch the ZDO instantiation path break first spawn

A cluster of world-editing and admin utilities patch the client's ZDO to GameObject path:
`ZNetScene.CreateObject`, `ZNetView.Awake`, `LocationProxy.SpawnLocation`,
`ZoneSystem.Load`, `ZDOMan`. With several installed at once — particularly installed on
**both** client and server — the client receives ZDOs but never instantiates them.
`IsAreaReady` never becomes true and first spawn never completes.

One of them replaces the load path outright: a Harmony `Prefix` on `ZDOMan.FindObjects`
returning `false`, fully suppressing the original. Its own documentation describes
both-sides installation as its strongest-filtering mode, and its state initialiser sets
up only a handful of prefab hashes while the layer flags default to false.

Treat this whole category as suspect when a client connects but never finishes loading.

### Two packages can ship the same Unity asset bundle CAB id

Unity identifies asset bundles by an internal CAB id. If two packages ship bundles with
the same id, whichever loads first claims it and Unity rejects the second. The second
package's `Awake` then dies on a `NullReferenceException` while using an asset it
believes it loaded.

The concrete case: a dungeon package shipped bundle
`CAB-51213d6c67d4dfe2aef8ee3c5a62f283`, identical to the id inside a large all-in-one
locations package's Black Forest dungeon assets. The AIO package loaded first, so the
smaller package failed on both client and server. It was redundant anyway — the AIO
package already provided that content.

Duplicate CAB ids are invisible in a package listing. If a package fails in `Awake` with
a null asset, compare bundle ids before assuming the package itself is broken.

### VR integration fixes belong in client-only scope

Packages that exist purely to reconcile another mod with ValheimVR have no function on a
dedicated server. Shipping them server-side wastes load time and creates a version
surface that has to agree for no benefit. These were rescoped to client-only rather than
removed.

## Register

| Package | Status | Note |
|---|---|---|
| `ComfyMods-ComfySigns` | **Confirmed** | Throwing `ZNetScene.Awake` postfix aborted the chain; first spawn hung forever. Replaced by `cjayride-AdvancedSigns`. |
| `warpalicious-Underground_Ruins` | **Confirmed** | Unity bundle CAB collision with the AIO locations package; `Awake` threw on both sides. Content already provided by the AIO package. |
| `JereKuusela-Dungeon_Splitter` | Bisected out | Prefix on `ZDOMan.FindObjects` returning false, installed on both client and server. |
| `JereKuusela-Infinity_Hammer` | Bisected out | ZDO instantiation-path class. |
| `JereKuusela-Item_Stand_All_Items` | Bisected out | ZDO instantiation-path class. |
| `JereKuusela-Server_devcommands` | Bisected out | ZDO instantiation-path class. |
| `JereKuusela-Structure_Tweaks` | Bisected out | ZDO instantiation-path class. |
| `JereKuusela-Upgrade_World` | Bisected out | ZDO instantiation-path class. |
| `JereKuusela-World_Edit_Commands` | Bisected out | ZDO instantiation-path class. |
| `DragonMotion-VHModpackFix` | Bisected out | ZDO instantiation-path class; a non-content fix tool. |
| `geekstreet-BackpacksVRFix` | Rescoped | VR-only integration fix, moved to client-only scope. |
| `geekstreet-EpicLootVRFix` | Rescoped | VR-only integration fix, moved to client-only scope. |
| `Azumatt-SearchableBuildMenu` | Superseded | Replaced by `Azumatt-AAABuildMenu`. |
| `ComfyMods-ComfyAutoRepair` | Superseded | Replaced by `hoskope-RhythmicRepairs`. |
| `Korppis-Spearfishing` | Superseded | Replaced by `AstralBeauty-SpearFishing`. |
| `Searica-SkilledCarryWeight` | Superseded | Replaced by `95Shade-CarryWeightSkill`. |
| `Valphi-BetterLaddersContinued` | Superseded | Replaced by `ComfyMods-ComfyLadders`. |
| `jard_hu-ClearSkies` | Superseded | Replaced by `ZenDragon-ZenWorldSettings`. |
| `Yggdrah-BetterRiding` | Housekeeping | No selected package depended on it. |
| `Yggdrah-DragonRiders` | Housekeeping | Removed to match rebuilt client profiles. |
| `OdinPlus-QuickTeleport` | Housekeeping | Stale; removed on request. |
| `warpalicious-More_World_Traders` | Housekeeping | Complete removal requested. |

## Packages excluded rather than removed

Recorded in each profile's `profile-manifest.json` under `excluded_packages`, which stops
a package being reselected as somebody else's dependency:

| Package | Reason |
|---|---|
| `ValheimVR-ValheimVR` | Excluded from the true nonVR client profile and from every server. |
| `NightOfGames-Huginn_Map` | Experimental, not approved. |
| `OdinPlus-CrystalLights` | Requires Jewelcrafting, which conflicts with the selected Epic Loot. |
| `Smoothbrain-Jewelcrafting` | Conflicts with the selected Epic Loot. |

## How these were found

`tools/valheim_mods.py` records a reason and a backup for every package it removes, so a
diagnosis is captured at the moment it is made rather than reconstructed later. That is
the only reason this document could be written at all — and it is worth copying if you
run a modded server, because by the time you want the answer you will not remember it.

The bisection itself was manual: halve the suspect set, rebuild the profile, attempt
first spawn, repeat.

For the Valheim and VHVR internals these diagnoses rest on, see
[valheim-vr-knowledge.md](valheim-vr-knowledge.md). For a profile that loads but runs
badly, the performance tooling is described in [vr-impact-scan.md](vr-impact-scan.md).
The one deliberate modification this project makes to ValheimVR itself is in
[valheimvr-packaging.md](valheimvr-packaging.md).
