# Operator mod candidates — four packages, two installed

Assessed 2026-09-15 against the deployed Valheim **1.0.12** server assembly
(`Ulfsland/data/bepinex/valheim_server_Data/Managed/assembly_valheim.dll`), BepInEx
**5.4.2350**, and the 117 plugin DLLs deployed under Ulfsland.

Every claim below is marked **MEASURED** (I ran it) or **INFERRED** (reasoned from something
measured). Method: packages downloaded to `/tmp/modassess`, decompiled with a Mono.Cecil
reference resolver and IL dumper built for this assessment (`RefCheck.cs`, `ILDump.cs`,
`Members.cs`, `Patches.cs`), plus a UnityFS decompressor for the asset bundles. `monodis`
alone was not sufficient — it aborts on both `assembly_valheim.dll` and the L4zerShark
patchers — so the Cecil tools are what the measurements rest on.

## Verdicts in one line each

| Package | Verdict |
|---|---|
| `DeathMonger/PauseMyServer` 1.4.1 | **INSTALLED, all 7 profiles, `shared`.** Does not stop the server main thread. RCON unaffected. Cannot pause an empty server. |
| `L4zerShark_Team/CLLCCompatibility` 1.0.1 | **INSTALLED, all 7 profiles, `shared`.** Fixes the live broken-cooking defect and retires `tools/everybodyshim` — but **will deploy inert until the patcher hoist is fixed.** |
| `L4zerShark_Team/AdvancedTerrainCompatibility` 1.0.1 | **REJECT.** Rival route to a mod we do not run. Cannot even engage against our build. Does not touch the clamp. |
| `warpalicious/More_World_Locations_AIO` | Installed 5.1.0, published **5.1.1**. Minor. **It is NOT the source of the `portal_wood` instances** — that attribution was wrong. |

---

# Part 1 — PauseMyServer 1.4.1

## The main-thread verdict, first: the server keeps ticking. RCON is unaffected.

**MEASURED, three independent ways:**

1. **No time-scale write exists anywhere in the assembly.** I scanned every instruction of
   every method in `PauseMyServer.dll` for member references containing `timeScale`,
   `fixedDeltaTime` or `captureFramerate`. **Zero matches.** The mod never assigns
   `Time.timeScale`.

2. **The one patch that could freeze a server returns early headless.**
   `Game.IsPaused` carries a postfix, and vanilla's `Game.UpdatePause` is what actually sets
   `Time.timeScale = 0`. So if `IsPaused()` returned true on the dedicated server, vanilla
   would freeze it. It cannot. The postfix IL is:

   ```
   if (__result) return;                          // already paused, leave it
   if (Player.m_localPlayer == null) return;      // <-- the gate
   if (PauseSync.ClientPaused && (PauseSync.ClientForced || PauseSync.WantPause))
       __result = true;
   ```

   `Player.m_localPlayer` is always `null` on a dedicated server. The postfix therefore
   never sets `__result`, `Game.UpdatePause` never zeroes the time scale, and frames keep
   running.

3. **The three server-side patches are prefixes on three narrow methods, not on the loop.**
   `ZNet.UpdateNetTime`, `RandEventSystem.FixedUpdate` and `EnvMan.UpdateTimeSkip`, each
   with the identical body `return !PauseSync.ServerPaused;` — skip the original while
   paused, nothing else.

**What it suspends, exactly** (MEASURED — this is the complete list; the mod has nine patch
classes and no others):

| Patched | Side | Effect while paused |
|---|---|---|
| `ZNet.UpdateNetTime` (prefix) | server | world clock stops advancing |
| `RandEventSystem.FixedUpdate` (prefix) | server | raids/random events held |
| `EnvMan.UpdateTimeSkip` (prefix) | server | sleep time-skip held |
| `Game.Pause` / `Game.Unpause` (postfix) | client | tracks the local pause wish |
| `Game.IsPaused` (postfix) | client | reports paused once server confirms |
| `Player.OnSpawned` (postfix) | client | asks server for current state |
| `Game.Start` (postfix) | both | registers the six `PMS_*` routed RPCs |
| `ZNet.Shutdown` (prefix) | both | state teardown |

So it suspends **the world clock, random events and sleep-skip — and nothing else on the
server.** Not the Unity loop, not `ZDOMan`, not `ZNet`'s socket pump, not spawners directly,
not the frame. Client freezing is done by vanilla on the client, via time scale.

**The RCON consequence, spelled out.** RCON is the fleet's build transport and its liveness
probe's soundness rests entirely on "a reply proves a frame ran". If pausing had stopped the
main thread, a paused-but-healthy world would have been indistinguishable from a wedged one,
and the build tooling would have aborted or hung against it. It does not: the server's frame
keeps running by explicit design, and the mod's own README states the reason — at time scale
zero the server would stop sending player lists and world data, so a joining player could
never load in. **The probe stays sound and no build tooling change is needed.** [MEASURED for
the mechanism; that the probe therefore still answers is INFERRED — I issued no RCON command,
per the standing constraint.]

## It cannot pause an empty server

This was the other way it could have bitten us, and it is guarded. **MEASURED** from
`PauseSync.UpdateServer`:

- `ready` = peers with `IsReady()`; `total` = `ready + (Player.m_localPlayer != null ? 1 : 0)`,
  which on a dedicated server is just `ready`.
- `everyoneWants` is computed as: **if `total <= 0` → false**; else if `wantCount != total`
  → false; else true.
- Separately, an admin pause is actively dropped when the last player leaves — there is a
  literal log line `"[PauseMyServer] Server is empty; admin pause dropped."` guarded by
  `AdminPaused && ready == 0 && _lastReady > 0 && Player.m_localPlayer == null`.

So a pause requires **at least one connected player and unanimity among all of them**, or a
live admin pause with someone online. An idle Ulfsland is never paused by this mod.

## Fleet interactions, each answered

A finding that reframes several rows: **MEASURED**, vanilla 1.0.12 `ZNet.UpdateNetTime`
already contains `if (IsServer() && GetNrOfPlayers() <= 0) return;` — the world clock
**already** stops on an empty server, with no mod involved. PauseMyServer's prefix therefore
adds nothing in the empty case; it only extends the freeze to "players online, all of them
asked".

| Subsystem | Measured behaviour while paused |
|---|---|
| **RCON** | Unaffected. Main thread runs; no patch touches it. |
| **600 s heartbeat** (`Connections N ZDOS:`) | Unaffected. MEASURED: it is `Game.ServerLog()`, scheduled by `MonoBehaviour.InvokeRepeating("ServerLog", 600f, 600f)` in `Game.Awake`. Not patched, and `InvokeRepeating` is driven by the server's own frame time, which keeps running. |
| **A2S / `status.json`** | Unaffected. No patch touches the query/status path, and the server frame keeps running. [MEASURED that nothing is patched; INFERRED that the A2S responder therefore keeps answering.] |
| **`DropCleaner`** | Freezes with the pause, harmlessly. MEASURED: its `PerformCleanup` reads `ZNet.GetTime()` — net time — which is exactly what the pause stops. Uncollected drops are not swept during a pause. Desirable. |
| **Crop growth, berries (100 min), crops (1600–2000 s), stone (80 min), respawn, fermenters, beehives** | Freeze with the pause. All are net-time driven, and net time is what stops. Also already frozen whenever the server is empty, by vanilla. |
| **`AutomaticFuel`** | Halts for player-owned pieces. MEASURED: it hooks `Fireplace.UpdateFireplace`, `Smelter.UpdateSmelter`, `Smelter.UpdateSmoke` and `ShieldGenerator.UpdateShield` — owner-side vanilla update methods. Frozen clients stop calling them for pieces they own. [INFERRED for server-owned pieces: the server frame still calls them, but their vanilla progression is net-time based and net time is stopped.] |
| **`TimedTorchesStayLit`** | [INFERRED] Same class as AutomaticFuel — net-time/owner-driven, so it freezes rather than burning through a pause. Not separately measured. |
| **Autosave** | Keeps running (author's note; not independently measured — **INFERRED**). Harmless. |

## Scope and lockstep

**Scope: `shared`, established from package contents, not the manifest.** MEASURED: the
package is one DLL at `BepInEx/plugins/PauseMyServer.dll` containing **both** halves —
server-authoritative logic (`PauseSync.UpdateServer`, the three server prefixes, the
`pms_pause`/`pms_status` console commands, all reached behind `ZNet.IsServer()`) **and**
client logic (`PauseOverlay`, `UpdateInput`, the BepInEx config, the four client patches).
`PauseSync.Update()` branches on `ZNet.IsServer()` to one or the other. A single DLL that
must exist on both sides is exactly `shared`.

**Not `ModRequired` / `EveryoneMustHaveMod`.** MEASURED: zero `ServerSync` types in the
assembly, no `ConfigSync`, and no patch on `ZNet.RPC_PeerInfo` / `ZNet.SendPeerInfo` or any
version field — its nine patch classes are listed above and none is a handshake. A stale or
missing client is **not** refused: server without the mod → clients never pause; client
without the mod → that player cannot ask for a pause and is not frozen by an admin pause.
**This is therefore not a lockstep move across the 20 client editions**, unlike the
ServerCharacters episode.

**Compatibility: clean.** MEASURED: resolving all **164** member references in
`PauseMyServer.dll` against the deployed 1.0.12 assembly yields **0 unresolved**.

## What changed, per profile

`tools/valheim_mods.py --profile <P> add DeathMonger-PauseMyServer 1.4.1`, run for all seven.
Identical outcome in each: `added=denikson-BepInExPack_Valheim,DeathMonger-PauseMyServer`.

| Profile | Manifest entry | Cache sides written |
|---|---|---|
| `admin` | `DeathMonger-PauseMyServer` 1.4.1, `scope: shared`, in `packages` | server + client |
| `flat` | same | server + client |
| `vr` | same | server + client |
| `ulfsland-admin` | same | server + client |
| `ulfsland-dn` | same | server + client |
| `ulfsland-flat` | same | server + client |
| `ulfsland-vr` | same | server + client |

Verified afterwards by reading all seven manifests: one entry each, `shared`, no duplicates,
and `PauseMyServer.dll` present twice per profile (both cache sides). Nothing deployed,
nothing published, no RCON issued.

**Pre-existing noise, not caused by this change:** every `add` printed
`settings not recorded: git add -A failed ... dubious ownership in repository at
/media/big4/projects/game/valheim/settings-history`. Manifest and cache writes succeeded; only
the settings-history commit was skipped. Left alone rather than running a global git config.

---

# Part 2 — CLLCCompatibility 1.0.1

## What it actually is

**MEASURED.** Not a plugin. It is a BepInEx **preloader patcher** that Harmony-transpiles
`BepInEx.Bootstrap.Chainloader.Start`, replacing its single `Assembly.LoadFile(string)` call
with its own `PrepareCompatiblePath(string)`. That hook rewrites the target mod's IL with
Cecil and loads a **repaired cache copy** instead of the original DLL. The original mod stays
installed and untouched on disk.

Three preconditions, all **MEASURED as satisfied here**:

1. **Fingerprint.** Its `RepairCatalog` matches `CreatureLevelControl.dll` by exact SHA-256
   `601739B998F956BC49A871A4B0C7761E1F6389E5F8604D3234868F9E92D2761E`. Our deployed CLLC
   4.6.4 DLL hashes to **exactly that value**, identically in all seven profiles. An
   unrecognised hash would mean "no repair is known for this build; Chainloader will load the
   original file UNMODIFIED" plus a warning — so this engaging rather than warning off is a
   verified fact, not a hope.
2. **Hook site.** It requires exactly one `Assembly.LoadFile(string)` in `Chainloader.Start`.
   Our deployed `BepInEx.dll` (5.4.2350) has exactly **one**.
3. **Repair targets.** Every member it retargets to exists in our 1.0.12 assembly at the
   arity it demands (table below). Missing any one makes it fail loudly, not silently.

It reads the game assembly from **`Path.Combine(BepInEx.Paths.ManagedPath,
"assembly_valheim.dll")` — the on-disk file**, with `ReadWrite=false`. That matters: see the
Valheim10Compatibility ordering note below.

Both L4zerShark packages are splits of one archived `ThreeModCompatibility` assembly
(namespace `ValheimThreeCompatibility`, and the two DLLs differ only in their `BuildTarget`
name/target file — their `KNOWN-ISSUES.md` and `VALIDATION.md` are byte-identical). Each
carries all three repair catalogs (CLLC, TerrainTools, QuickTeleport) but guards to its own
target. They refuse to run beside the archived combined package and beside a second copy of
themselves, with explicit log lines for both.

## Question 1 — does it fix the broken cooking? **YES, and it targets the throwing class by name.**

This is the highest-value finding in the report. Cooking is broken on Ulfsland with:

```
InvalidProgramException: Invalid IL code in (wrapper dynamic-method)
  CookingStation:DMD<CookingStation::RPC_RemoveDoneItem>
Rethrow as HarmonyException: IL Compile Error
  at CreatureLevelControl.CreatureLevelControl.Awake ()
```

and `docs/proposed/2026-09-15-update-recheck.md` records that CLLC 4.6.4 is the newest
published version, so there is no version answer, and that our own shim forward was **tried
and rejected** — emitting a 3-parameter `SpawnItem` sibling turned CLLC's by-name resolution
into an `AmbiguousMatchException`, breaking the very patch class the forward was meant to
repair (`tools/everybodyshim/AppendedRequiredForwards.cs:90-107`, whose `Table` is
consequently empty).

**MEASURED, the link is exact:**

- CLLC's throwing class is `CreatureLevelControl.ItemControl/PatchCookingStationItemMultiplier`,
  and its Harmony attribute is literally `HarmonyPatch(CookingStation, RPC_RemoveDoneItem)`.
- CLLCCompatibility's `Repairs::Creature()` contains, as IL string literals, the call
  `UpgradeMultiplier("CreatureLevelControl.ItemControl/PatchCookingStationItemMultiplier",
  "multiplyItemSpawn", "CookingStation", "SpawnItem", 4, "cheated")`.
- `UpgradeMultiplier` rewrites CLLC's own multiplier helper to the 1.0 signature, appending
  the parameter and supplying a reviewed default — it checks the target signature against the
  literal `"System.String,System.Int32,UnityEngine.Vector3,System.Boolean"`, renames/retypes
  the wrapper parameters, and refuses with a named error (`"Unexpected multiplier API
  signature"`, `"Unexpected multiplier wrapper arity"`, `"No reviewed default for ..."`) rather
  than guessing.
- **It does not add a game overload.** That is why it cannot reproduce our failure: 1.0 has
  **exactly one** `CookingStation.SpawnItem` (MEASURED: `SpawnItem(4: String, Int32, Vector3,
  Boolean)`), so CLLC's by-name resolution still finds one match. It also rewrites CLLC's
  reflection handle (`ldtoken` → `GetMethodFromHandle`), which is unambiguous by construction.

The same mechanism repairs the crafting side:
`UpgradeMultiplier("...PatchCraftingMultiplier", "multiplyItemAdd", "Inventory", "AddItem",
10, "pickedUp")`, against CLLC's `HarmonyPatch(InventoryGui, DoCrafting)`.

**So: a working cooking station on five worlds, from an upstream package, with no patch of
ours to maintain.** No `tools/modpatches/` entry is needed.

## Question 2 — does it retire `tools/everybodyshim`? **YES — and for a better reason than the brief had.**

### CLLC's unresolved references, and who supplies each

**MEASURED.** Resolving all 2,652 member references in `CreatureLevelControl.dll` against the
raw deployed 1.0.12 assembly yields exactly **8 unresolved**. Supplier established by UTF-16
literal sweep of `Valheim10Compatibility.Patcher.dll` and `EverybodyShim.dll`:

| # | Unresolved reference in CLLC 4.6.4 | Supplied today by | CLLCCompatibility retargets it to | Exists in 1.0.12? |
|---|---|---|---|---|
| 1 | `Character::Message(MessageHud/MessageType, String, Int32, Sprite)` | Valheim10Compatibility | `Character.Message` arity 5 | yes — `(5: MessageType, String, Int32=0, Sprite=null, Boolean=False)` |
| 2 | `EffectList::Create(Vector3, Quaternion, Transform, Single, Int32)` | Valheim10Compatibility | `EffectList.Create` arity 6 | yes — 6 params, last `ZDOID=null` |
| 3 | `Inventory::AddItem(String,Int32,Int32,Int32,Int64,String,Boolean)` | Valheim10Compatibility | `Inventory.AddItem` arity 10 (`pickedUp`) | yes |
| 4 | `Inventory::Changed()` | Valheim10Compatibility | `Inventory.Changed` arity 2 | yes — `(2: Boolean=False, Boolean=False)` |
| 5 | `ZoneSystem::GetZonePos(Vector2i)` | Valheim10Compatibility | `ZoneSystem.GetZonePos` arity 1 | yes — takes `Vector2s`; converted |
| 6 | `CookingStation::SpawnItem(String,Int32,Vector3)` | **nobody** | `CookingStation.SpawnItem` arity 4 (`cheated`) | yes |
| 7 | `Minimap::noForest : UnityEngine.Color` | **nobody** | static field `Minimap.s_noForestColor` | yes — `[static] Color` |
| 8 | `ZDO::GetSector() -> Vector2i` | **`tools/everybodyshim`** | `ZDO.GetSector` arity 0 | yes — returns `Vector2s`; converted |

**Correction to the brief:** CLLC holds **5** Valheim10Compatibility references, not 6. The
sixth, `CookingStation::SpawnItem`, has **no supplier at all** — which is precisely why
cooking is broken. So CLLC's 8 split as 5 V10C + 1 EverybodyShim + **2 unbridged**.

`CLLCCompatibility` covers **all eight**, including both unbridged ones. Its return/parameter
conversion helper `Forward` handles `Vector2i ↔ Vector2s` in both directions (via the `x`/`y`
fields and an `Int32,Int32` constructor), which is exactly the conversion EverybodyShim
performs today.

### The shim member it retires, and whether the shim can then go

`tools/everybodyshim`'s live bridge is
`new WidenedReturnSpec("ZDO", "GetSector", "Vector2i")`
(`ReturnTypeWidenedForwards.cs:122`) — the `Vector2i`-returning overload that
Valheim10Compatibility deliberately BLOCKS because it differs from vanilla's `Vector2s`
overload by return type alone. CLLCCompatibility retargets CLLC's call to the real
`ZDO.GetSector()` and converts the result, so **CLLC stops needing it**.

**Two corrections to the brief here, both MEASURED:**

1. **The shim has three bridges, not one.** `ReturnTypeWidenedForwards`
   (`ZDO.GetSector`), `AppendedOptionalForwards`
   (`PlayerProfile.IncrementStat(PlayerStatType, System.Single)`) and
   `DelegatedReturnForwards` (`ZDOMan.GetPortals`). `AppendedRequiredForwards.Table` is empty.
   So retirement has to clear all three, not one.
2. **`ServersideQoL` is not a second dependent of `ZDO.GetSector`.** It references
   `ZDO::GetSector() -> **Vector2s**` — the current 1.0 form, which **resolves natively** and
   needs no bridge. CLLC references `-> **Vector2i**`. The shim exists *because* the
   difference is return type alone, so a check matching on member name cannot distinguish a
   dependent from a non-dependent. Corroborated by a UTF-16 literal sweep: `GetSector` as a
   by-name string appears in **zero** mods — only in `EverybodyShim.dll` and
   `Valheim10Compatibility.Patcher.dll`, the emitters naming their own targets. ServersideQoL's
   only unresolved `assembly_valheim` references are its own injected
   `ZDO::get_ServersideQoLZDO()` and `ZNetPeer::get_ServersideQoLPeer()`.

**The dependent scan.** Across all 117 deployed plugin DLLs, for each of the three bridges, by
**both** mechanisms (unresolved member reference, and UTF-16 by-name literal):

| Bridge | Member-reference consumers | By-name consumers |
|---|---|---|
| `ZDO.GetSector() -> Vector2i` | **1** — `CreatureLevelControl.dll` | 0 |
| `PlayerProfile.IncrementStat(PlayerStatType, Single)` | **0** | 0 |
| `ZDOMan.GetPortals` / `GetPortalList` | **0** | 0 |

**So the answer is YES.** Two of the three bridges are already dead — `Cross_Server_Portals`
no longer needs `GetPortals` — and CLLC is the **last consumer of the last live bridge**. Once
CLLCCompatibility supplies it, `tools/everybodyshim` has zero consumers and can be deleted
outright, along with its deployed `EverybodyShim.dll` and the client-side hoist in
`cmd/valheim-profile-sync/everybody_shim.go`.

**RECOMMENDATION, NOT AN ACTION — and it has a precondition.** Do **not** delete
`tools/everybodyshim` in the same change that installs CLLCCompatibility. Retire it only
after CLLCCompatibility is **deployed** and observed to supply `ZDO::GetSector() -> Vector2i`
in the running server — i.e. after a boot in which CLLC initialises, cooking works, and no
plugin reports a new unresolved reference. Deleting our own bridge alongside adding someone
else's is how a world comes up with 117 plugins and no forward. Note also that an inert
patcher (see the blocker below) would pass an "unresolved references" check while fixing
nothing, so **cooking working is the load-bearing half of that verification, not the
reference count.**

Valheim10Compatibility itself cannot be removed: the other 21 unresolved references across
the fleet still need it (`Character.Message` in 8 further mods, `PieceTable.m_availablePieces`
in 4, `EffectList.Create` in 3, and others). CLLC's contribution simply drops out.

## `Minimap::noForest` and the by-name `Character.Message` interaction

- **`Minimap::noForest`** — the unbridged cosmetic reference with no supplier. **MEASURED:
  `Repairs::Creature()` locates CLLC's single `Minimap.noForest` field access, requires the
  replacement to be a `static UnityEngine.Color`, and rewrites the instruction to load
  `Minimap.s_noForestColor`, which **MEASURED** exists in 1.0.12 as `[static] Color`. It
  guards with `"Unexpected minimap color access"` / `"Minimap replacement must be static
  Color"` / `"Expected one minimap color access"`. So yes — it fixes it properly, not by
  suppression.

- **The by-name `Character.Message` trap.** CLLCCompatibility's `MessageCalls()` requires
  exactly four `Character.Message` call sites in CLLC and retargets them to
  `Character.Message` arity 5. This is **compatible with, and does not duplicate**, our
  existing arrangement: Valheim10Compatibility's `[Harmony] ResolutionHook = true` detours
  HarmonyX's `AccessTools` lookups so by-name `[HarmonyPatch]` targets resolve past bridges.
  CLLCCompatibility works on a different object — it rewrites **CLLC's own IL** rather than
  injecting a game overload — so it adds no new ambiguity for the resolution hook to
  untangle. Our repo's record of why our own `Character.Message` forward was harmful
  (`EverybodyShim.cs:53-60`, re-breaking `AzuAreaRepair`) does not apply to it, for the same
  reason.

- **Ordering with Valheim10Compatibility: no conflict. MEASURED.** This was a genuine risk
  worth checking. `Repairs::Creature()` reads `ZRoutedRpc.Everybody` and **requires it to be
  a literal** (`"Unexpected RPC broadcast constant"`), then rewrites CLLC's five stale
  embedded constant loads to the current value. Meanwhile V10C's
  `ZRoutedRpcEverybodyStatic = true` de-literalizes that same field. If CLLCCompatibility saw
  V10C's rewritten field the requirement would fail and CLLC would refuse to load at all.
  It does not: it reads `assembly_valheim.dll` **from disk**, where the field is still
  `[static] [literal=0]` (MEASURED). V10C patches in memory. The two do not collide.

- **PACK-002 in the package's own `KNOWN-ISSUES.md`** is the same four-argument
  `Character.Message` `MissingMethodException` family this fleet has been chasing; the author
  records it as unresolved at full-pack scale and explicitly declines to attribute it. Their
  two open issues (PACK-001 `Player.Load` `ArgumentNullException`, PACK-002) are honest
  unknowns, not claims against us. Their validation was run on a 3-mod stack plus a
  RAFT/Azu baseline — **not** a 48-mod pack, and not our 94.

## Caveats worth keeping in view

- The package was created **today** (2026-09-15 20:27 UTC), has **26 downloads**, a
  `rating_score` of -1, and is tagged **"AI Generated"** by its author. Its documentation is
  unusually rigorous and self-limiting, and everything in it that I checked against the
  deployed binaries held up — but it is brand new code in the preload path.
- It is fingerprint-locked. **Any** future CLLC version bump invalidates the repair; CLLC
  would then load unrepaired with a warning, and cooking would break again. Pin CLLC 4.6.4
  and treat a CLLC update as gated on a matching CLLCCompatibility release.
- It Harmony-patches BepInEx's own `Chainloader.Start`. That is the most privileged place in
  the load order. It refuses to install beside a duplicate or beside the archived combined
  package, and logs when it does — worth grepping the boot log for
  `Prepared repaired CreatureLevelControl.dll`, which is its success line.

## Scope, lockstep, and directory — plus a defect found and FIXED

**Scope: `shared`**, matching CLLC itself (declared `shared` in all seven manifests). CLLC's
DLL is present in every profile's **server** cache and additionally in `ulfsland-dn`'s
**client** cache, so the repair must be available on both sides. **Not** `ModRequired`: it
ships no ServerSync and performs no handshake; it only rewrites a local DLL at load.

**Directory: `BepInEx/patchers/` — it is a PRELOADER PATCHER, not a plugin.**

> ### The defect: as first installed, it would have deployed INERT, silently — now FIXED
>
> **MEASURED.** CLLCCompatibility ships its DLL **one level deeper** than our three existing
> patchers:
>
> ```
> EverybodyShim/patchers/EverybodyShim.dll                            <- flat
> ServersideQoL/patchers/ServersideQoL.Patchers.dll                   <- flat
> Valheim10Compatibility/patchers/Valheim10Compatibility.Patcher.dll  <- flat
> CLLCCompatibility/patchers/L4zerShark_Team-CLLCCompatibility/CLLCCompatibility.dll   <- NESTED
> ```
>
> Both hoists looked exactly one level down and therefore missed it:
>
> - **Server** — `tools/valheim_mods.py`: `target.glob('*/patchers/*.dll')`. Running that
>   exact glob against the real staged cache
>   (`profiles/ulfsland-dn/manager-cache/server/BepInEx/plugins`) matched the **3** flat
>   patchers and **missed** CLLCCompatibility.
> - **Client** — `cmd/valheim-profile-sync/sync.go` in `hoistPackagePatchers`:
>   `if entry.IsDir() || !strings.EqualFold(filepath.Ext(entry.Name()), ".dll") { continue }`
>   — it explicitly skipped subdirectories under `patchers/`.
>
> Nothing flattened it upstream: `stale_cache_entries`' own docstring recorded that
> `<Package>/patchers/` is an author-chosen subfolder "the extractor is right to keep", which
> is why the nesting survived to disk exactly as measured — and which made that docstring the
> reason someone would conclude the nesting was already handled.
>
> Consequence, in the words of the hoist's own comment: *"one left under plugins/ is inert,
> silently."* CLLC would have loaded unrepaired, cooking would have stayed broken, and the
> boot would have looked clean. **This is the shape that matters most here: an inert patcher
> PASSES a boot-time unresolved-reference check while fixing nothing**, so the reference count
> is not evidence — a working cooking station is.
>
> **FIXED** (authorised hand-over; both hoists were confirmed unowned by other agents):
>
> - `tools/valheim_mods.py` — the hoist is now depth-independent, iterating
>   `<Package>/patchers/` and `rglob('*.dll')` beneath it. The `patcher_hoisted=` line now
>   names the **package** via `relative_to(target).parts[0]`; the old
>   `shipped.parent.parent.name` would have printed `patchers/CLLCCompatibility.dll` for a
>   nested layout, i.e. the proof line itself was wrong for exactly the case that was broken.
>   The mtime guard is unchanged.
> - `cmd/valheim-profile-sync/sync.go` — `hoistPackagePatchers` now walks with
>   `filepath.WalkDir` instead of skipping directories. The dedupe-by-name and
>   `sameFileContents` guards are unchanged; `io/fs` was already imported.
> - `stale_cache_entries`' docstring updated: it said **three** `patchers/`, it is now
>   **four**, and it now says explicitly that "the extractor is right to keep it" must not be
>   read as "and therefore something downstream handles it". A stale comment that explains
>   why a bug is not a bug is worse than no comment.
>
> **Regression tests, each proven to fail before the fix and pass after — both sides are
> testable in this repo, so both are covered:**
>
> - `tools/test_valheim_mods.py::DeployTest::test_hoists_patchers_at_both_depths_authors_ship_them`
>   — fixture carries a nested `patchers/<vendor>/<name>.dll` **and** a flat patcher as a
>   control, so a fix handling only the nested shape would not pass. Asserts destination
>   contents by file name, that the vendor directory is not reproduced, that a non-DLL is not
>   hoisted, and that the `patcher_hoisted=` line names the package. Against unmodified
>   `HEAD`: `FileNotFoundError: .../patchers/CLLCCompatibility.dll`.
> - `cmd/valheim-profile-sync/sync_test.go::TestHoistPackagePatchersHoistsAPatcherNestedBelowPatchersDirectory`
>   — same shape. Against the pre-fix function:
>   `hoisted CLLCCompatibility.dll = "", ... no such file or directory`. All four
>   `TestHoistPackagePatchers*` tests pass after, and `go vet` is clean.
>
> **Deploy gate.** Running the new server-side logic against the real staged
> `ulfsland-dn` cache emits exactly four lines:
>
> ```
> patcher_hoisted=CLLCCompatibility/CLLCCompatibility.dll
> patcher_hoisted=EverybodyShim/EverybodyShim.dll
> patcher_hoisted=ServersideQoL/ServersideQoL.Patchers.dll
> patcher_hoisted=Valheim10Compatibility/Valheim10Compatibility.Patcher.dll
> ```
>
> The first line is the deploy-time proof the cooking fix engaged. Absent it, the patcher did
> not run, whatever else the boot says. The other success signal to grep for is the mod's own
> `Prepared repaired CreatureLevelControl.dll`.

## What changed, per profile

`tools/valheim_mods.py --profile <P> add L4zerShark_Team-CLLCCompatibility 1.0.1`, all seven.
Identical outcome:
`added=Smoothbrain-CreatureLevelAndLootControl,denikson-BepInExPack_Valheim,L4zerShark_Team-CLLCCompatibility`
(the dependency was already pinned at 4.6.4 — verified no duplicate entry was created in any
profile).

| Profile | Manifest entry | Cache sides written |
|---|---|---|
| `admin` | `L4zerShark_Team-CLLCCompatibility` 1.0.1, `scope: shared`, in `packages` | server + client |
| `flat` | same | server + client |
| `vr` | same | server + client |
| `ulfsland-admin` | same | server + client |
| `ulfsland-dn` | same | server + client |
| `ulfsland-flat` | same | server + client |
| `ulfsland-vr` | same | server + client |


## Second change in the same file: the deploy now anchors the game build

Handed over from `UpdateGate` and landed in the same pass, because both changes touch
`cmd_deploy`'s output vocabulary.

**The gap.** `UpdateGate` landed a game-build anchor in `hostops/` so a Steam update cannot
silently change the binary under a deployed mod set. But the deploy path wrote no anchor: it
printed `deployed=true` and `settings_history=<hash>` and recorded nothing about the game
build, so the anchor existed only by trust-on-first-use or by an operator running
`hostops/anchor_game_build.sh` by hand. `record_game_build_anchor`'s own docstring already
claimed "the deploy path calls it after staging a mod set" — it did not.

**The change.** `cmd_deploy` now calls `hostops/anchor_game_build.sh <world> --reason deploy`
after the plugins are staged and the server config is deployed, and prints
`game_build_anchor=<sha256>` beside `deployed=true`. It is an **outcome line**, in the same
vocabulary as `generated_placed=`, `deploy_dropped=` and `patcher_hoisted=` — the lines that
turned three separate silent failures visible tonight — not a log message.

The ordering is the one `anchor_game_build.sh` documents for an operator: deploy first, anchor
second, because the deploy *is* the revalidation of the mod set against the build.

**The asymmetry is preserved deliberately, and it is the point of the gate:**

- A **CHANGED game build REFUSES a start.** That remains `require_matching_game_build`'s job
  and is untouched here.
- A **STALE `mod_set_fingerprint` only REPORTS.**
- And a deploy that cannot anchor **reports rather than fails**:
  `game_build_anchor=unavailable reason=<why>` (with the script's own last stderr line when
  it has one). A deploy that has already moved files must not fail afterwards on bookkeeping
  — leaving a world with half a mod set would be a worse outcome than a missing anchor, and
  collapsing the three behaviours into one is the difference between a gate and an outage.

**Tests** (both proven to fail against unmodified `HEAD`):

- `test_deploy_anchors_the_game_build_it_staged_against` — writes a fake
  `data/server/valheim_server_Data/Managed/assembly_valheim.dll`, deploys, and asserts the
  printed sha equals the file's real SHA-256, that `.game-build-anchor` is written with
  `recorded_reason=deploy` and `game_assembly_source=install`. This exercises the real shell
  helper end to end rather than mocking it.
- `test_deploy_reports_rather_than_fails_when_the_build_cannot_be_anchored` — no assembly
  present; asserts `game_build_anchor=unavailable`, that `deployed=true` still prints, and
  that the plugins still landed.

Full module: **58 passed**.

**Note for the operator, and it is a choice rather than an oversight:**
`anchor_game_build.sh` is deliberately **not** registered in `policy.yaml`, so the portal
agent cannot release a game-build hold. Releasing a hold is the human decision the gate
exists to force. The deploy writing an anchor does not weaken that: the deploy is itself an
operator action taken with the server stopped, and it is the moment the mod set is actually
validated against the build.

---

# Part 3 — AdvancedTerrainCompatibility 1.0.1: **REJECT**

## The relationship: a rival route to a mod we do not run

**MEASURED**, from the three packages' own metadata and binaries:

| Package | What it is | Last updated | Built against |
|---|---|---|---|
| `Searica/AdvancedTerrainModifiers` **1.4.1** | the **original**, now stale | 2024-12-07 | BepInEx 5.4.2202, Jotunn 2.22.0 (pre-1.0) |
| `Ostrix/AdvancedTerrainModifiersCompatible` **1.4.8** | a **maintained source fork** — "Maintained AdvancedTerrainModifiers compatibility fork for current Valheim, Jotunn and EpicLoot", source `github.com/MaikiOS/TerrainTools` | 2026-09-13 | BepInEx 5.4.2350, Jotunn 2.30.0 — **this is what we run** |
| `L4zerShark_Team/AdvancedTerrainCompatibility` **1.0.1** | a **binary-repair shim for the ORIGINAL**, declared dependency `Searica-AdvancedTerrainModifiers-1.4.1` | 2026-09-15 (today) | patches `TerrainTools.dll` by exact SHA-256 |

So it is **not** a fork, successor or upstream of what we run. It is a **rival route to the
same problem**: Ostrix fixed the source and ships a rebuilt DLL; L4zerShark leaves the 2024
DLL alone and binary-patches it at load. They are mutually exclusive alternatives to each
other, and ours is the source-level maintained one.

**It cannot even engage against our build. MEASURED:** its catalog keys `TerrainTools.dll` on
SHA-256 `BE8F51E45BAFA1C6B9BC2587360A22B4D33D69516EDF191A46383DE18E608D15`. Our deployed
`TerrainTools.dll` is `ed620c20d5186c6e65740b9906b0806f43d0f96392421687f0fa656df7e99e0a`,
identical across all seven profiles and both cache sides. Per the patcher's own rule an
unrecognised fingerprint is left **unchanged with a warning**. Installing it beside our Ostrix
fork is a **no-op plus a log line**. Nothing to gain, and a `Chainloader.Start` transpiler in
the preload path to lose.

## The three questions that matter

**1. Does it change the ±8 m clamp? NO.**

**MEASURED**, `Repairs::Terrain()` touches exactly one thing: the precision **painting**
prefix. It binds `TerrainTools.Helpers.PreciseTerrainModifier` →
`PaintClearedPrefix` / `PaintClearedLegacy141`, retargets `TerrainComp.PaintCleared` to the
1.0 `TerrainOp/Settings` contract, and rebinds the settings fields `m_paintRadius`,
`m_paintType`, `m_paintHeightCheck`, plus one terrain message API call.

A literal scan of the whole DLL for the terms that would have to appear if it touched height:
**`Clamp` 0, `ApplyToHeightmap` 0, `RaiseTerrain` 0, `SmoothTerrain` 0, `LevelTerrain` 0.**
It is a **paint** repair, not a height repair.

And the clamp is vanilla and load-bearing, which I verified rather than inherited. **MEASURED**
in the deployed 1.0.12 assembly: `TerrainComp::ApplyToHeightmap` contains
`UnityEngine.Mathf::Clamp(Single,Single,Single)` against `±8`. **MEASURED** in our own Ostrix
build: `TerrainTools.Helpers.PreciseTerrainModifier::RaiseTerrainPrefix` re-applies
`Mathf.Clamp(v, -8f, 8f)`. So the clamp that made Mistlands at 22 m spread and a harbour at
43 m unflattenable, and that forced replacement of a 13.8 m-relief blueprint, is **vanilla's**
and this package does not move it. Nothing here unblocks those sites.

**2. Is it usable headlessly? NO, and it changes nothing about that.**

**MEASURED:** zero `ConsoleCommand` and zero `Terminal` strings — it adds no console commands.
It only repairs a client-side paint prefix on a hoe-side tool. The Ostrix mod remains what it
was: hoe/Input-driven, unusable from a headless server. This package does not add a headless
path.

**3. Does it change the on-disk `TCData` format? NO — no risk to existing terrain.**

**MEASURED:** the literal `TCData` appears **0 times** in the assembly. It does not read,
write or migrate the `_TerrainCompiler` ZDO payload. The 43 levelled zones and 27,275 m² of
paving our pipeline synthesised by hand into that format are **not** at risk from it. (That
this is the good outcome rather than a feature is the point: a format change here would have
been a risk to existing work, not a capability.)

**Verdict: do not install.** It would be inert against our fingerprint, it does not address
the clamp, it adds no headless capability, and it puts a third-party transpiler into
`Chainloader.Start` for zero measured benefit. Keep `Ostrix-AdvancedTerrainModifiersCompatible`
1.4.8.

---

# Part 4 — More_World_Locations_AIO: currency, and a correction

## Currency: installed 5.1.0, published 5.1.1 — minor, and it does not retro-add anything

**MEASURED:** the installed DLL's assembly version is `5.1.0.0`, matching the manifest pin of
5.1.0 in all seven profiles. The current published version is **5.1.1**, released today
(2026-09-15 18:39 UTC). The delta, from the package's own changelog:

- **Adds five new Swamp locations** — `MWL_SwampTempleSmall1`, `MWL_SwampTemple2`,
  `MWL_SwampShrine1`, `MWL_SwampSanctuary1`, `MWL_SwampComplex1`.
- **Fixes** shipping-port discovery persistence when ValheimEnforcer saves player custom data
  during logout.
- Dependencies unchanged in kind (`Jotunn` 2.29.2, `JsonDotNET` 13.0.4, `YamlDotNet` 16.3.1,
  BepInExPack 5.4.2350).

**The operationally important half:** the five new locations do **not** appear on Ulfsland
just because the version is bumped. The changelog instructs installing **Upgrade World** and
running `locations_add MWL_SwampTempleSmall1,MWL_SwampTemple2,MWL_SwampShrine1,
MWL_SwampSanctuary1,MWL_SwampComplex1 start`. The mod treats already-generated zones as fixed.
So 5.1.0 → 5.1.1 is a **low-risk bump that adds no new geometry to the existing world** unless
someone deliberately runs that command. [MEASURED from the changelog and the version metadata;
the non-retroactivity is the author's documented behaviour — **INFERRED** for our world, and
consistent with the world-generation-time mechanism measured below.]

Handed to `UpdateRecheck`? No — MWL is one of my four packages, so the bump is recorded here.

## Correction: MWL is **NOT** the source of the 26 `portal_wood` instances

The brief attributed the 26 `portal_wood` world-location instances — including three within
5–29 m of the workshop pad — to this mod. **That attribution does not survive measurement.**

**MEASURED, two independent ways:**

1. **All 264 MWL asset bundles, decompressed and searched.** A first attempt to `grep` the
   bundles was **inconclusive, not negative**, and I nearly reported it as negative: the
   bundles are `UnityFS` with flags `0x243`, i.e. LZ4HC-compressed `blocksInfo` and
   **LZMA**-compressed data blocks, so plain string search cannot see inside them. I wrote a
   UnityFS decompressor (LZ4 block info + raw-LZMA data blocks, with the 16-byte alignment
   both before `blocksInfo` and before the block data) and decompressed **all 264 bundles
   with zero errors**. Searching every one for `/(JVLmock_)?portal[A-Za-z0-9_]*/` yields
   **zero hits in all 264**. Positive control: the same scan finds `JVLmock_MWL_Shrine`,
   `JVLmock_MWL_Waystone`, `JVLmock_stone_floor_2x2` and so on, so the search works.
2. **The installed 5.1.0 DLL.** An exact UTF-16 match for `portal_wood` (and for bare
   `portal`) returns **zero hits**. MWL's only teleport feature is its **own shipping-port
   network** (`Teleport To Ports`, `Teleport Cost Per Meter`) plus its own `MWL_Waystone`
   prefab — neither is `portal_wood`.

Also **MEASURED**: among all deployed plugins, only `Jotunn` (4 bundles) and MWL (264) ship
UnityFS bundles at all, and the four Jotunn bundles contain no portal prefab either. The four
DLLs that do carry the literal `portal_wood` are `Structure_Tweaks`, `ServersideQoL`,
`World_Edit_Commands` and `Cross_Server_Portals` — all of which *reference* the prefab rather
than placing locations.

**Consequence for the operator, and it is the actionable part:** turning MWL features off, or
bumping MWL, will **not** clear a portal shrine near a build site. Anyone reaching for that
lever is reaching for the wrong one. The true placer of those 26 instances remains
unattributed and is outside this assessment's four packages. (The brief's other MWL claim
**does** hold: MWL is the source of the 267-odd asset bundles whose prefab hashes the map
exporter could not name — 264 MWL + 4 Jotunn = 268 bundles measured.)

`WorldBuild` has been told directly, before acting on the wrong theory. Their mitigation is
tag-based and placer-agnostic, so it is unaffected.

## Are MWL's locations excludable or filterable? **YES at world generation — NO retroactively**

**MEASURED**, from literals in the installed DLL and the deployed config.

**Coarse feature switches**, in `Ulfsland/config_merged/bepinex/warpalicious.More_World_Locations_AIO.cfg`,
section `[0 - Features]` / `[0 - Shipment Ports]` — all currently **On**:
`Enable Shrines`, `Enable Waystones`, `Enable Traders`, `Enable Trainers`,
`Enable Port Locations`.

**Per-location quantity.** `Use Custom Location YAML` (currently **Off**) makes the mod load
`warpalicious.More_World_Locations_LocationConfigs.yml` from the BepInEx config folder,
auto-extracting defaults if absent. That file's own header, verbatim from the DLL:

```
# More World Locations AIO - Location Quantity Configuration
# Quantity = number of this location the game will attempt to place during world generation
# Set to 0 to disable a location
```

The deployed `.cfg` also still shows the older per-location form (`[121 - Location1]
Spawn Quantity = 20`), migrated to YAML in 4.2.0.

> **The caveat that closes off the obvious workaround.** Quantity is applied **during world
> generation**. Setting a location to 0 suppresses it in zones Ulfsland has **not yet
> generated**; it does **not** retract a `LocationProxy` from a zone already generated. MWL's
> own changelog confirms the direction of travel — it tells users to run Upgrade World
> `locations_add ...` to get new locations into an existing world, i.e. the mod itself treats
> generated zones as fixed.
>
> **So for `WorldBuild`'s three sites blocked by generated `LocationProxy` markers standing
> 11.08, 23.05, 17.12 and 24.14 m from pad centres — where the solver's own location dump
> recorded 60 m of clearance and the live world disagrees by up to 49 m — a config change
> cannot help.** Those zones are generated. The honest answer is that **pads move (or bodies
> shrink), rather than locations being deleted**; removing an already-placed marker is an
> Upgrade World `locations_remove` operation on live world data, not a config edit. This is
> worth stating plainly because "just turn the locations off" is the workaround someone will
> otherwise reach for, and it does not work.

MWL **is** a location placer — 190 advertised POI locations, registered via a Harmony patch on
`EntryPointSceneLoader.Start` (MEASURED) — so it is a live candidate for those markers even
though it is definitively not the portal source. Attributing a specific marker to MWL versus
vanilla was not in scope and is not claimed here.

---

# Corrections to the brief, consolidated

Each of these is an instance of the recurring shape — *a check that confidently answers a
question it is not measuring*.

1. **`ServersideQoL` is not a second `ZDO.GetSector` dependent.** It references the
   `-> Vector2s` (1.0) form, which resolves natively; CLLC references `-> Vector2i`. The bridge
   exists because the difference is **return type alone**, so a name-based check cannot tell a
   dependent from a non-dependent. Corroborated by a zero-hit by-name literal sweep.
2. **`tools/everybodyshim` has three bridges, not one** — `ZDO.GetSector`,
   `PlayerProfile.IncrementStat`, `ZDOMan.GetPortals`. The conclusion still holds, and more
   strongly: two are already dead, so CLLC is the last consumer of the last live bridge.
3. **CLLC holds 5 Valheim10Compatibility references, not 6.** The sixth,
   `CookingStation::SpawnItem`, has no supplier at all — which is exactly why cooking is
   broken.
4. **MWL_AIO is not the source of the 26 `portal_wood` instances.** Zero hits across 264
   decompressed bundles and zero in the DLL.
5. **My own near-miss, recorded deliberately.** My first bundle scan was a plain `grep` over
   LZMA-compressed `UnityFS` payloads. It returned zero hits and *looked* like a clean negative
   result. It was inconclusive — the same shape as the defects above, in my own work. The
   negative only became real once I decompressed all 264 bundles and demonstrated a positive
   control.
6. **`AdvancedTerrainCompatibility` is not related to the mod we run.** It depends on
   `Searica-AdvancedTerrainModifiers-1.4.1`; we run the `Ostrix` source fork. The name
   similarity is the only connection, and its fingerprint does not match our DLL.

# State on disk after this ticket

**Manifests (7 profiles: `admin`, `flat`, `vr`, `ulfsland-admin`, `ulfsland-dn`,
`ulfsland-flat`, `ulfsland-vr`)**

- `DeathMonger-PauseMyServer` **1.4.1**, `scope: shared`, in `packages` — all **7**, both
  cache sides.
- `L4zerShark_Team-CLLCCompatibility` **1.0.1**, `scope: shared`, in `packages` — all **7**,
  both cache sides. No duplicate `CreatureLevelAndLootControl` entry created in any profile.

**Code changed (authorised hand-over, both files confirmed unowned by other agents)**

- `tools/valheim_mods.py` — depth-independent patcher hoist; `patcher_hoisted=` now names the
  package; `stale_cache_entries` docstring corrected (three → four patchers, and no longer
  implies the nesting is handled downstream); `record_deploy_game_build_anchor` added and
  `game_build_anchor=` printed beside `deployed=true`.
- `cmd/valheim-profile-sync/sync.go` — `hoistPackagePatchers` walks recursively instead of
  skipping directories.
- `tools/test_valheim_mods.py` — 3 new tests (nested+flat hoist, anchor success, anchor
  unavailable). Module: **58 passed**.
- `cmd/valheim-profile-sync/sync_test.go` — 1 new test (nested hoist). All four
  `TestHoistPackagePatchers*` pass; `go vet` clean.
- All four new tests were **proven to fail** against the unmodified pre-change code and pass
  after. A regression test that cannot fail is not evidence.

**Not done, deliberately**

- Nothing deployed. Nothing published. No server stopped or restarted. **No RCON command
  issued.** No `bd`, no `git commit`, no `git push`, no `./scripts/check.sh`.
- `tools/everybodyshim` **untouched** — its retirement is a recommendation with a stated
  precondition (CLLCCompatibility deployed and observed to supply
  `ZDO::GetSector() -> Vector2i`, with cooking working), not an action.
- The `settings-history` `dubious ownership` warning left alone — it is host configuration on
  a repo owned by another uid, and a global git setting applied mid-flight with five agents
  writing would be its own hazard. Cost is the audit trail of these two `add`s only; the
  manifest and cache writes themselves succeeded.

**Deploy order for whoever sequences it**

1. Deploy (server stopped). Expect **four** `patcher_hoisted=` lines, including
   `patcher_hoisted=CLLCCompatibility/CLLCCompatibility.dll`, plus `game_build_anchor=<sha>`.
2. Boot. Grep for `Prepared repaired CreatureLevelControl.dll`.
3. **Verify cooking works.** This is the load-bearing check — an inert patcher passes an
   unresolved-reference count while fixing nothing.
4. Only then, as a separate change, retire `tools/everybodyshim`.
