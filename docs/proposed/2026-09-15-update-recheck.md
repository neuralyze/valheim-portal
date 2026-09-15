# Update re-check: the server is current, and that is the most useful thing here

Status: **proposal / reconnaissance only.** Nothing was installed, updated, deployed,
published, enabled or disabled. No profile manifest was edited. No `<world>/data/` or
`<world>/config_merged/` was written. No container was stopped, started or restarted. No RCON
command was issued to any world. No `bd`, no `git commit`, no `scripts/check.sh`.
`valheim-server-Ulfsland` was left running with `admin_mode` as found. The only writes were
into `/tmp/recheck/` and this file.

Everything marked **MEASURED** was measured on this host on 2026-09-15 between 22:30 and
23:10 UTC. Everything marked **[INFERENCE]** is reasoning from those measurements and is not
itself a measurement. Where a claim in the brief did not survive measurement, it is called out
under [Corrections](#corrections-three-briefed-claims-did-not-survive-measurement).

This is a delta against the sweep that landed ~2 hours ago. The 22 packages applied in that
window are **not** re-reported.

---

# Part 1 — the dedicated server

## The answer first: there is nothing to take

**MEASURED. The installed dedicated-server build and the current public-branch build are the
same build.** `buildid 25253791`. There is no server update available, and therefore no
network-version move, no world-version migration and no plugin blast radius to absorb tonight.

| | buildid | date | how it was measured |
|---|---|---|---|
| **Installed** (all 5 worlds) | `25253791` | app installed 2026-09-12T23:38:44Z (Ulfsland) / 2026-09-14T01:24:52Z (other four) | `"buildid"` read out of each install's own `steamapps/appmanifest_896660.acf` |
| **Current, `public` branch** | `25253791` | build pushed 2026-09-11T12:55:32Z; branch pointer moved 2026-09-11T13:08:24Z | `app_info_print 896660` for app 896660, `depots.branches.public` |

### Method, path by path

**Installed — MEASURED.** Read directly from the appmanifest each world's Steam download cache
owns. Ulfsland's is the live one:

```
/media/big4/projects/game/valheim/Ulfsland/data/dl/server/steamapps/appmanifest_896660.acf
  "name"        "Valheim Dedicated Server"
  "StateFlags"  "4"            # fully installed, no pending update
  "buildid"     "25253791"
  "LastUpdated" "1789256324"   # 2026-09-12T23:38:44Z
  "UpdateResult" "0"
```

The other four read identically (`buildid 25253791`, `StateFlags 4`, `UpdateResult 0`,
`LastUpdated 1789349092` = 2026-09-14T01:24:52Z):

```
/media/big4/projects/game/valheim/{Vangard,Storgard,Hrafnheim,Doggerland}/data/dl/server/steamapps/appmanifest_896660.acf
```

**Current public branch — MEASURED.** There is no `steamcmd` on the host (`which steamcmd`
empty; no `steamcmd.sh` anywhere under `/media/big4/projects/game/valheim`) — it lives only
inside the server image, and running it there means either `docker exec` into the live world or
creating a container, both of which the read-only constraint rules out. So the PICS
`app_info_print` payload was read out of band instead:

```
curl https://api.steamcmd.net/v1/info/896660
  .data["896660"].depots.branches.public = {
      "buildid":           "25253791",
      "timebuildupdated":  1789131332,   # 2026-09-11T12:55:32Z
      "timeupdated":       1789132104    # 2026-09-11T13:08:24Z
  }
  ._change_number = 38905983
```

That endpoint is a mirror of `app_info_print`, and a mirror can be stale — which is exactly the
failure mode the bulk Thunderstore index already demonstrated tonight. So it was corroborated
twice, independently:

- **MEASURED.** App **892970** (the Valheim *client*) shows `public buildid 25253764`,
  `timebuildupdated 2026-09-11T12:53:47Z`, `timeupdated 2026-09-11T13:08:42Z`. Client and
  server were pushed 105 seconds apart and their branch pointers moved 18 seconds apart —
  the signature of one paired release, and nothing after it.
- **MEASURED.** `ISteamNews/GetNewsForApp?appid=892970` — Iron Gate's most recent
  `Community Announcements` item is **"Hotfix 1.0.10 & 1.0.12", 2026-09-11T13:09:02Z**,
  27 minutes after the build push and 45 seconds after the branch pointer moved. The item
  before it is "Valheim 1.0 Has Arrived!" (2026-09-09T12:55:35Z). Nothing has been announced in
  the four days since.

**[INFERENCE]** Three sources agreeing on one timestamp, two of them from Valve infrastructure
and one from the publisher, is as close to "the branch has not moved" as can be established
without running `steamcmd`. The residual risk is a silent depot push with no announcement
inside the mirror's cache window.

Also checked, **MEASURED**: none of the six non-`public` branches is what the fleet is pinned
to, and `STEAMCMD_ARGS='validate -beta public'` in `Ulfsland/valheim.env` pins the update
explicitly to `public`. `default_old` sits at `25185644` ("Previous stable") and
`default_pre1_0` at `21981590`.

### No update is staged anywhere either

**MEASURED.** `require_matching_game_build` compares three copies of
`valheim_server_Data/Managed/assembly_valheim.dll` per world — the Steam cache
(`data/dl/server/`), the install (`data/server/`) and the BepInEx overlay (`data/bepinex/`),
which is the one that actually executes. All **fifteen** copies across all five worlds are
byte-identical:

```
sha256 1231fc2ffdbe6038ba622b8646c2084980d06962e5f8521ae5a0b886be0f1c61   2,560,000 bytes
```

So there is no pending download, no half-applied update, and no older cache waiting to be
rsynced backwards over a good install — the 2026-09-13 downgrade shape (`vhp-oxn`) is not
present.

### What the running binary actually is

**MEASURED**, from the live `valheim-server-Ulfsland` boot (2026-09-15 08:11–08:13) and from
the live world file — not from a config file that claims it:

| Fact | Value | Source |
|---|---|---|
| Game version | `l-1.0.12` | `[Message: BepInEx] Valheim Version: l-1.0.12` |
| Network version | **40** | `[Valheim10CompatibilityAdapter] [NetHandshake] … running game l-1.0.12, network version 40` |
| World version | **41** (generator 2) | `tools/valheim_world.py inspect …/worlds_local/Ulfsland/_main.118.fwl2` → `{"seed":"Pirate68","seed_value":147627509,"uid":171189434,"world_version":41,"generator_version":2}` |
| BepInEx | 5.4.23.3 / BepInExPack **5.4.2333** | `User is running BepInExPack Valheim version 5.4.2333 from Thunderstore` |
| Unity | 6000.0.75f1 | `Initialize engine version: 6000.0.75f1` |
| Plugin DLLs, Ulfsland | **117** in `plugins/` (104 mod DLLs + 13 BepInExPack core) + 3 patchers | `find data/bepinex/BepInEx/plugins -name '*.dll' \| wc -l` |
| Plugin DLLs, other 4 worlds | **113** in `plugins/` (100 + 13 core) + 3 patchers (Vangard 4: adds `LoadTimeProfiler.dll`) | same |

The brief's "113 plugin DLLs" is the four stopped worlds. The live world carries 117.

### What a bump *would* cost, when one comes

There is no bump, so none of this is owed tonight. It is recorded because it is the standing
blast radius and it was measurable now, cheaply, against the binary we actually have.

**Network version.** **MEASURED** at 40 from the handshake log. Iron Gate moves it on protocol
changes; **[INFERENCE]** any move refuses every un-updated client at the handshake — which is
the same failure the operator already paid an hour for tonight via mod-version lockstep, but
fleet-wide and not fixable by republishing a client edition, because the *game* has to update
on the player's machine first.

**World version.** **MEASURED** at 41, generator 2, on the live save
(`worlds_local/Ulfsland/_main.118.fwl2`). A world-version move is a one-way save migration
across five worlds. This fleet did 37 → 41 already and it was not free: **MEASURED** in
`tools/modpatches/README.md` and bead `vhp-e0h`, `ZDOMan.ConvertContainers` (which runs only on
a `WorldVersion 37` save) hit a null-key defect in `BlacksmithingExpanded 1.1.7` and aborted
Vangard's entire world load — 451,451 ZDOs read, then `Exiting without save`, in a ~186-second
restart loop, 13 cycles.

**Which plugins would need to move.** Rather than guess, the bridge-dependent set was measured
by exact-token reference scan over all 117 deployed DLLs:

- **`tools/everybodyshim` (ZDO.GetSector() → Vector2i) — 2 dependents, MEASURED.**
  `ServersideQoL.dll` and `CreatureLevelControl.dll` are the only deployed assemblies
  referencing `GetSector`. `EverybodyShim.cs`'s own measurement log puts the cost of removing
  that one bridge at **7,774 `MissingMethodException` per boot** from
  CreatureLevelAndLootControl, Serverside_Simulations and ServersideQoL. Any game update that
  changes `ZDO.GetSector` again lands squarely on this bridge, and it is ours to rebuild —
  `Valheim10Compatibility` refuses to emit it by policy (return-type-only overload).
- **`Valheim10Compatibility` 1.4.0 — fleet-wide, MEASURED.** Its `NetHandshake` adapter line
  names the exact game it is built for: `adapter 1.4.0 for Valheim 1.0.x+; running game
  l-1.0.12, network version 40`. **[INFERENCE]** a 1.1 game would need a new adapter from
  upstream before any of the ~100 pre-1.0-compiled plugins load cleanly; it is a single point
  of failure for the whole set, not for a few mods.
- **The blocked `m_availablePieces` alias — 5 dependents, MEASURED** (see Part 2).

**MEASURED**, today's whole Ulfsland boot matches `[Error|Exception` on exactly **8** lines,
and they account for only **three** things: the two `BLOCKED … AmbiguousMatchException`
warnings from `Valheim10Compatibility` (one per patcher pass) and **two** distinct plugin
defects, both in Part 2. That is the baseline a bump would be measured against.

## Does `valheim-updater` apply updates automatically?

**No periodic check is armed. MEASURED.**

`valheim-updater` is a *supervisord program* inside each world's container, not a separate
Docker container — `supervisord.conf` `[program:valheim-updater]`, `autostart=false`,
started by `valheim-bootstrap`. It is running right now in the live world (`docker top
valheim-server-Ulfsland` → `/bin/bash /usr/local/bin/valheim-updater`, elapsed 09:20:47,
matching the container's own uptime).

Two knobs govern whether it ever checks again after boot, and both are off:

- **`UPDATE_CRON` is set-but-empty. MEASURED.** `docker inspect valheim-server-Ulfsland`
  reports `UPDATE_CRON=` in `Config.Env` (it comes from `default.env`, which contains
  `RESTART_CRON=""` and `UPDATE_CRON=""`). The image's `defaults:67` uses
  `UPDATE_CRON=${UPDATE_CRON-*/15 * * * *}` — the `-` form, not `:-`, so an empty-but-set value
  is *kept* and the `*/15` default never applies. `valheim-bootstrap:93` then gates the crontab
  entry on `[ -n "$UPDATE_CRON" ]`, which is false, so **no cron line that would `kill -HUP` the
  updater is ever written.**
- **`UPDATE_INTERVAL` is unset. MEASURED.** It is absent from `Config.Env`, so
  `defaults:65` gives it `315360000` seconds — **9.99 years**. `valheim-updater:31` sets
  `next_update=$(date +%s)+UPDATE_INTERVAL` after its first pass, so the in-container loop will
  not check again this decade.

**Net: an update can only be initiated by a container start, or by an explicit `SIGHUP` to the
updater's pidfile. There is no schedule.** One mitigation is also armed: `UPDATE_IF_IDLE`
defaults to `true` (`defaults:68`), so `is_idle()` skips the update entirely while players are
connected — except on the just-started path, which deliberately bypasses the player check
(`valheim-updater:260`, and MEASURED in today's log: `Valheim updater was just started -
skipping connected players check`).

## Could an unattended game update start a world against a mismatched mod set?

**Yes. MEASURED, by two distinct routes, and the second one is not gated at all.**

The in-container sequence a container start triggers is:
`valheim-bootstrap` → `valheim-updater` → `steamcmd +force_install_dir … +app_update 896660
validate -beta public` → `rsync -a --delete "$valheim_download_path/" "$valheim_install_path"`
→ `check_mods updated` (touches the BepInEx mergefile, re-merging the overlay from the new
install) → `write_restart_file updated` → `check_server_restart` → `supervisorctl start
valheim-server`. **Every step of that is inside the container. It never calls
`require_matching_game_build`, which is a host function.**

**Route 1 — `valheim-watchdog.timer`: GATED (but the gate cannot answer this question).**
`systemctl list-unit-files` → `valheim-watchdog.timer  enabled`, `OnUnitActiveSec=5min`, last
fired 2026-09-15 17:42:31 CDT. It runs `/srv/valheim-portal/hostops/watch_valheim_servers.sh`,
whose `restart_world()` (lines 86–90) shells out to `stop_valheim_server.sh` then
`start_valheim_server.sh`. That is the gated path: `start_valheim_server.sh:33` calls
`require_matching_game_build`, and the **deployed** copy really has it (`grep -c
require_matching_game_build /srv/valheim-portal/hostops/lib/common.sh` → `2`, so the pull is not
behind the repo here). It iterates `docker ps --filter name=valheim-server-`, so today it covers
Ulfsland only.

But **MEASURED**, that gate compares the three *game* copies to each other. It does not know
what mod set is deployed or which game build that mod set was built against — stated as a known
limitation in `docs/proposed/2026-09-13-mod-set-provenance.md:46-49`. A legitimate Steam update
passes it trivially: all three copies agree *before* the restart, the container then updates all
three together, and the world opens on a new binary under the old mod set. **The gate closes the
downgrade hole it was written for. It does not close this one.**

**Route 2 — `valheim-hang-watchdog@.timer`: UNGATED. MEASURED.**
`valheim-hang-watchdog@Hrafnheim.timer` is **active right now** — `OnUnitActiveSec=2min`, last
fired 2026-09-15 17:41:52 CDT, next 17:43:52. Its service runs
`valheim_hang_watchdog.sh %i --silence-minutes 15 --restart`, and at line 93 that script does:

```sh
if docker restart "$CONTAINER_NAME" >/dev/null 2>&1; then
```

**A bare `docker restart`. No `hostops` call, no `require_matching_game_build`, no stop/start
pair.** It is a container start, so it runs the full updater sequence above, and it fires on a
two-minute timer with a 15-minute silence trigger. That is the 3 a.m. shape the brief is worried
about, and it exists today.

Two things are keeping it harmless, and neither is a control:

1. **The public branch has not moved** (Part 1). `app_update` finds nothing to do, the rsync
   itemizes no changes, and the updater logs "already the latest version".
2. **The armed instance is for Hrafnheim, which is stopped.** `valheim_hang_watchdog.sh:40-41`
   exits 0 when the container is absent or not running, so the timer is a no-op tonight. There
   is no `valheim-hang-watchdog@Ulfsland.timer`, so the live world is currently only reachable
   by the gated route.

**[INFERENCE]** The exposure is therefore: whenever Iron Gate does push, the first container
start of any world — a watchdog restart, an operator restart, a host reboot, or simply starting
one of the four stopped worlds — silently takes the new binary and opens the world on it. Route 1
would not refuse. Route 2 would not even look. The fix already has a written design
(`2026-09-13-mod-set-provenance.md`: record `game_build.overlay` at deploy time and give
`require_matching_game_build` a fourth comparison) and is not implemented; `docker restart` in
`valheim_hang_watchdog.sh` bypasses the host gate regardless of what the gate learns to check,
so that line needs to become a `stop`/`start` pair for any of it to bind.

### The trigger is not hypothetical: worlds are being restarted on false-empty reads

The paragraph above treats "a watchdog restart, an operator restart, a host reboot" as the
thing that arms this. That list understated the rate, and tonight supplied the correction.

**MEASURED by `WorldBuild`, corroborated by `Main`** (reported to me over `hub` on 2026-09-15;
not re-measured here, and flagged as second-hand for that reason).
`tools/jumpstart/terraform/console.py`'s `log_cursor()` built its `docker logs --since` cursor
at nanosecond precision (`date -u +%Y-%m-%dT%H:%M:%S.%NZ`) while supervisord stamps the
container log at one-second resolution. A cursor of `17:57:35.087Z` therefore sorts *after* the
stamp on output produced 0.3 s later, which supervisord writes as plain `17:57:35`, and
`docker logs --since` drops it. `run_console` returns `[]`, and the caller cannot distinguish
"printed nothing" from "printed, and I asked too late". It fired twice consecutively at
`pre-yagluth#plains-farm-base` and `pre-fader#ashlands-forward-base` while the container log
plainly held the full prefab table and its `Total` line.

**The operationally important part for this document: `Main` read one of those false empties as
a wedged RCON plugin and restarted a healthy world on the strength of it.**

That closes a loop this section left open. A restart is a container start, and a container start
is the *only* thing that can initiate a game update on this fleet (there is no schedule — see
above). So the arming event for the ungated-update hazard is not a rare operator action or a
reboot: **it is a diagnostic false negative, and at least one has already occurred tonight.**
Route 2's `docker restart` and an operator restart are indistinguishable to the updater.

**[INFERENCE]** This raises the priority of the `stop`/`start` conversion in
`valheim_hang_watchdog.sh` rather than changing what the fix is. Fixing `console.py`'s cursor
removes *this* source of spurious restarts; it does not make restarts safe, and the next
false-empty-shaped defect would re-arm the same hazard. The gate has to be in the path of every
restart regardless of why the restart was decided — which is the whole argument for routing
`docker restart` through `hostops` instead of hardening the callers that reach for it.

Worth preserving from `WorldBuild`'s account, because it is the reason this cost minutes and not
a world: the failure mode is a **false empty and never a wrong number**, because `box_query`
raises on an absent `Total` rather than reading it as zero. That refusal is the more valuable
half of the design and must survive the cursor fix. The equivalent guard on this side is
`require_matching_game_build`'s existing refusal (`hostops/lib/common.sh:474-484`), which
returns non-zero rather than starting when the cache is older than the install — the same shape,
and the reason the 2026-09-13 downgrade cannot silently repeat.

---

# Part 2 — mods, as a delta against two hours ago

## What has shipped since the sweep

Nine packages have a newer version than what the 7 profiles carry. **All nine were
cross-checked against the per-package experimental endpoint**
(`api/experimental/package/<owner>/<name>/`), not just the bulk index — and **MEASURED**, tonight
the two agreed exactly on all 110 packages in the union of the 7 profiles. The bulk index
is not stale right now; it was two hours ago, so the cross-check stays mandatory.

Every one of these was published **today**, seven of the nine after the sweep ran:

| Package | Installed | Latest | Published (UTC) | Profiles | Client lockstep? | What it buys |
|---|---|---|---|---|---|---|
| `Ross-RossItemDrawers` | 1.0.3 | **1.0.5** | 21:21:40 | all 7 | `EveryoneMustHaveMod` / **Minor** → 1.0.3 client **not** refused | Fixes "Try again" when taking from an *unowned* drawer — the normal case on a dedicated server; withdrawal refusals now hit the log |
| `JamesJonesTV-Ravenwood_Currency` | 1.0.0 | **1.0.1** | 21:12:56 | all 7 | `EveryoneMustHaveMod` / **Minor** → 1.0.0 client **not** refused | No changelog published |
| `warpalicious-More_World_Locations_AIO` | 5.1.0 | **5.1.1** | 18:39:49 | all 7 | ServerSync config only, no handshake refusal | **Five new Swamp locations**; needs `Upgrade_World`'s `locations_add` to reach existing worlds |
| `JereKuusela-Infinity_Hammer` | 1.83.0 | **1.84.0** | 18:33:04 | all 7 | none | Zoom-snap setting; fixes a NRE from favourites + tools + keep-equip-on-death |
| `ZenDragon-Zen_ModLib` | 1.14.0 | **1.14.2** | 16:11:22 | all 7 | `VersionCheckOnly` / Patch | Gamepad build-menu repair fix; radial-menu keybind; **version check now accepts 1.0.12 (Steam) and 1.0.11 (MS)** |
| `shudnal-ConfigurationManager` | 1.1.20 | **1.1.21** | 14:49:59 | admin, ulfsland-admin, ulfsland-dn | none | Fixes `KeyCode` dropdowns |
| `Balrond-balrond_constructions` | 1.4.4 | **1.4.5** | 12:54:27 | all 7 | none | Shader/material and missing-reference fixes |
| `ZenDragon-ZenWorldSettings` | 1.12.0 | **1.13.0** | 08:23:50 | all 7 | `ClientMustHaveMod` / **Patch** → **1.12.0 client REFUSED** | Traders can sell the new Deep North inventory-expansion items; Quests replaced by a per-player Progression system |
| `Vapok-AdventureBackpacks` | 2.0.1 | **2.0.4** | 01:20:02 | all 7 | `EveryoneMustHaveMod` / **Patch** → **2.0.1 client REFUSED** | Item-duplication fix — see below |

**Lockstep column, MEASURED.** Each candidate's assembly-level Jotunn
`NetworkCompatibilityAttribute` was decoded from the CustomAttribute blob, and the enum
literals were read out of the deployed `Jotunn.dll` itself rather than assumed:
`CompatibilityLevel {0 NoNeedForSync, 1 OnlySyncWhenInstalled, 2 EveryoneMustHaveMod,
3 ClientMustHaveMod, 4 ServerMustHaveMod, 5 VersionCheckOnly, 6 NotEnforced}` and
`VersionStrictness {0 None, 1 Major, 2 Minor, 3 Patch}`. **[INFERENCE]** on the *semantics*:
`Minor` compares major+minor only, so 1.0.3 → 1.0.5 and 1.0.0 → 1.0.1 are patch-level moves that
a `Minor`-strictness mod tolerates; `Patch` compares all three, so those bumps are refusals.
That mapping is Jotunn's documented behaviour, not something re-measured here.

**Also checked and current** — no update exists for any of: `Wubarrk-Valheim10Compatibility`
1.4.0 (published 2026-09-13), `Smoothbrain-CreatureLevelAndLootControl` 4.6.4 (2025-05-26),
`ValheimModding-Jotunn` 2.30.0, `95Shade-CarryWeightSkill` 1.0.2, `OdinPlus-BlacksmithingExpanded`
1.1.7, `OdinPlus-Basements` 1.4.1, `OdinPlus-OdinsHorsePen` 1.1.0, `OdinPlus-OdinsUndercroft`
1.3.3, `JamesJonesTV-RavenwoodRestorations` 1.0.1. None is deprecated.

## Verdicts on the six decision items

### 1. `95Shade-CarryWeightSkill` — no fix exists, and it is not what broke cooking

**MEASURED. There is no newer version, and the published 1.0.2 is bit-for-bit what we already
run.** The experimental endpoint gives `latest = 1.0.2`, `date_created 2026-09-15T00:28:24Z`.
Downloading it and hashing gives `sha256 2a9c39526ddfde6f34729968ea5ac625acfa9df0ac0a648d62d269fab1d791ed`
— identical to the deployed
`Ulfsland/data/bepinex/BepInEx/plugins/CarryWeightSkill/CarrySkill.dll`. The PatchAll failure
reproduces against the newest published build by definition, because it *is* the newest published
build.

**MEASURED, and this is the part that changes the decision: its IL failure is not on
`CookingStation`.** Today's live log, in full:

```
368: [Error :Carry Weight Skill] CarrySkill: PatchAll failed: HarmonyLib.HarmonyException:
     IL Compile Error ---> System.InvalidProgramException: Invalid IL code in
     (wrapper dynamic-method) InventoryGui:DMD<InventoryGui::RepairOneItem> (InventoryGui):
     IL_0149: call 0x00000053
393:   at CarrySkill.CarrySkillPlugin.Awake ()
```

Its target is `InventoryGui::RepairOneItem`. Cooking is broken by a *different* mod — see
Corrections. **MEASURED** by reference scan, the only deployed assemblies naming
`InventoryGui::RepairOneItem` are `CarrySkill.dll` and `TradersExtended.dll`; the only assembly
naming `RPC_RemoveDoneItem` is `CreatureLevelControl.dll`.

**Should it be removed server-side?** **Not blindly, and the brief's stated reason for removing
it is wrong.** Three measurements bear on it:

- **It is not client-tree-only.** Its DLL is present in both the deployed profile tree
  (`Ulfsland/config_merged/bepinex/plugins/CarryWeightSkill/CarrySkill.dll`) and the live server
  overlay (`Ulfsland/data/bepinex/BepInEx/plugins/…`), same 25,088 bytes, same hash. It has to
  be — a server-side `[Error :Carry Weight Skill]` line cannot be produced by a DLL that is not
  loaded server-side. MEASURED.
- **It is not server-synced.** `strings` finds no `ServerSync`/`ConfigSync`, and the
  assembly carries **no** `NetworkCompatibilityAttribute`. Removing it server-side would not
  refuse any client at the handshake. MEASURED.
- **But it registers a Jotunn custom skill.** MEASURED, `CarrySkill.dll` references
  `Jotunn.Managers.SkillManager`, `SkillConfig`, `AddSkill`, and the skill identifier
  `shadymods.carryskill.carry_weight`. MEASURED, the live server logs
  `[Jotunn.Managers.SkillManager] Registering 4 custom skills` at 08:13:54 — *after* the
  PatchAll error at 08:12:51, so the plugin caught its own exception and continued initialising.
  **[INFERENCE]** that skill is one of the four, and with `ServerCharacters` holding player
  profiles server-side, dropping the mod from the server risks the server not recognising the
  skill id in a saved profile. That is a data question, not a boot question, and it is the one
  thing to establish before removing it.

**Recommended verdict:** leave the server side alone. The failing patch is on a client UI
method that a dedicated server never invokes (`InventoryGui` is never instantiated headless), so
the cost server-side is one error line at boot and CarrySkill's own patches not applying — it is
noise, not a live defect. **[INFERENCE]** the same patch runs on clients, where `RepairOneItem`
*is* live, so the real exposure is client-side item repair; that was not measured tonight (no
client boot was run) and is the check worth doing next. If it is confirmed broken on clients, the
move is to drop the mod from **both** sides in one lockstep pass with the skill-data question
answered first — not to strip it server-side only, which buys nothing and risks the profiles.

### 2. `Vapok-AdventureBackpacks` 2.0.4 — still newest, changelog still claims the fix, and it is a full lockstep move

**MEASURED. 2.0.4 is still the newest**: experimental endpoint `latest = 2.0.4`,
`date_created 2026-09-15T01:20:02Z`, `date_updated 2026-09-15T01:20:04Z`, not deprecated. No
2.0.5.

**MEASURED. The changelog still claims the duplication fix**, verbatim from
`api/experimental/package/Vapok/AdventureBackpacks/2.0.4/changelog/`:

```
# 2.0.4 - Container Mod Compatibility & Item Duplication Fix
* Fixed: Item duplication and inventory reset when building or crafting with
  container-scanning mods (e.g. AzuCraftyBoxes, CraftFromContainers).
* Fixed: Inventory desynchronization between player container component and
  equipped backpack data.
* Fixed: Prevented container-saving logic from writing to the player character's
  network ZDO data.
* Minor stability and null-safety improvements during backpack resizing.
```

**MEASURED, the named trigger is live on this fleet.** `fedorovdgap-AzuCraftyBoxes` 1.8.18 is
deployed and announces itself in the handshake (`Sending AzuCraftyBoxes version 1.8.18 and
minimum version 1.8.18 to the client`), and `ServersideQoL.AutoStore.dll` is deployed alongside
it. (The brief said `AzuAutoStore`; the deployed auto-store is ServersideQoL's, and the
crafty-boxes mod is the `fedorovdgap` fork — neither changes the conclusion.)

**MEASURED, the lockstep cost is exact, not assumed.** Both 2.0.1 (deployed) and 2.0.4 carry
`NetworkCompatibility(EveryoneMustHaveMod, VersionStrictness.Patch)`. `Patch` strictness on a
2.0.1 → 2.0.4 move means **every client edition must be republished in the same window or it is
refused at the handshake.** 20 client editions.

**MEASURED, one thing 2.0.4 does *not* fix:** it still references the blocked
`m_availablePieces` alias, and only that name — see item 3.

**MEASURED, dependency check.** 2.0.4 declares `denikson-BepInExPack_Valheim-5.4.2350`,
`ValheimModding-Jotunn-2.26.1`, `ValheimModding-YamlDotNet-16.3.1`. The profiles declare
5.4.2350 / Jotunn 2.30.0 / YamlDotNet 16.3.1, so all three are satisfied on paper — but the
container's `PRE_SUPERVISOR_HOOK` rewrites `bepinex-updater` to pin BepInExPack to **5.4.2333**,
and the live boot confirms 5.4.2333 is what runs. 2.0.1 declares the identical dependency set, so
this divergence is pre-existing and not introduced by the bump. Worth noting, not blocking.

### 3. The blocked `List<List<Piece>>` alias — no update, and there are **five** mods, not four

**MEASURED. None of the four named packages has moved.** `OdinPlus-Basements` 1.4.1
(2026-02-08), `OdinPlus-OdinsHorsePen` 1.1.0 (2026-02-05), `OdinPlus-OdinsUndercroft` 1.3.3
(2026-07-24), `JamesJonesTV-RavenwoodRestorations` 1.0.1 (2026-09-08). Installed == latest for
all four. Their build pieces are in exactly the state the last sweep left them.

**MEASURED. The block is still in force**, from today's boot (twice, once per patcher pass):

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces
(List<List<Piece>>, alias of m_availablePiecesByCategory): … Left unaliased deliberately.
```

**MEASURED. Exact-token scan over all 117 deployed DLLs finds a fifth dependent the brief does
not list: `AdventureBackpacks`.** Distinguishing the two field names properly matters here,
because `m_availablePiecesByCategory` contains `m_availablePieces` as a prefix — a substring
match reports 13 mods and is wrong.

| Deployed DLL | old `m_availablePieces` | new `…ByCategory` | verdict |
|---|---|---|---|
| `Basements.dll` | yes | no | **blocked** |
| `OdinsHorsePen.dll` | yes | no | **blocked** |
| `OdinUndercroft.dll` | yes | no | **blocked** |
| `RavenwoodRestorations.dll` | yes | no | **blocked** |
| `AdventureBackpacks.dll` (2.0.1 **and** 2.0.4) | yes | no | **blocked** |
| `InfinityHammer.dll` (1.83.0 and 1.84.0) | yes | yes | handles both |
| `ItemDrawers.dll` (1.0.3 and 1.0.5) | yes | yes | handles both |
| `OdinShip.dll` | yes | yes | handles both |
| `OdinCampsite.dll`, `OdinsFoodBarrels.dll`, `OdinsKingdom.dll`, `CraftyCartsRemake1_patched.dll`, `Jotunn.dll` | no | yes | migrated |

All five blocked mods vendor the same library and the same two patch methods — MEASURED, each
contains `PieceManager`, `PiecePrefabManager`, `UpdateAvailable_Prefix`, `UpdateAvailable_Postfix`
— so it is one defect with five copies, exactly as the previous sweep characterised it.

**MEASURED**, today's server boot logs **no** error from any of them, and none from
`PiecePrefabManager` or `UpdateAvailable`. **[INFERENCE]** that is expected and not
reassuring: `PieceTable.UpdateAvailable` is invoked when a build menu is opened, which never
happens on a headless server, so the symptom is purely client-side. Nothing re-measured that
client-side symptom tonight; nothing changed that could have fixed it.

### 4. `OdinPlus-BlacksmithingExpanded` — 1.1.8 has NOT shipped, and the patch is already gone anyway

**MEASURED. Thunderstore's latest is still 1.1.7**, `date_created 2026-03-10T16:57:24Z`, not
deprecated. No 1.1.8. The Cecil patcher's IL-shape guard is not at risk from an upstream bump
tonight.

**MEASURED, and more usefully: the repair is not deployed anywhere right now.** All five worlds
carry a `BlacksmithingExpanded.dll` that is byte-identical to the stock Thunderstore 1.1.7
archive:

```
stock 1.1.7 (downloaded to /tmp/recheck)        sha256 2611ff7c…0477   435,200 bytes
Ulfsland   /config_merged/…/BlacksmithingExpanded.dll  2611ff7c…0477   435,200
Vangard    …                                            2611ff7c…0477   435,200
Storgard / Hrafnheim / Doggerland                       2611ff7c…0477   435,200
```

So the brief's hazard ("1.1.8 ships → the patcher refuses → Vangard silently loses its
`ConvertContainers` crash repair") cannot fire, for two reasons: 1.1.8 does not exist, **and the
patch was already reverted** — `tools/modpatches/README.md` documents exactly this
("`config_merged` is regenerated by `manage_mods.sh deploy`, so a deploy reverts this"), and
tonight's deploy of all five worlds did it. No `.stock-1.1.7` sibling remains beside any copy.

**Verdict: not a hazard, and not a regression.** The README's own reasoning holds:
`ZDOMan.ConvertContainers` runs only on a `WorldVersion 37` save and **MEASURED** all five
worlds are at 41, so the patch protects nothing today. It becomes load-bearing again only if a
world ever loads a pre-1.0 save — a restore from a pre-migration archive, which is precisely the
scenario the fleet used on 2026-09-13. **[INFERENCE]** the reportable item is therefore not
"1.1.8 shipped" but "the restore runbook must reapply this patch before loading any
WorldVersion 37 archive, and nothing in the tooling reminds anyone."

### 5. `Smoothbrain-ServerCharacters` — Thunderstore is still at 1.4.16; the local build stands

**MEASURED. Thunderstore's latest is 1.4.16, `date_created 2025-05-02T15:22:10Z`.** No
1.4.17 and no newer version has been published there. The author has not moved. The local build
and its licence position are unchanged, and nothing is retired.

**MEASURED, the deployed binary is the local build, not the Thunderstore one:**

```
Thunderstore 1.4.16 (downloaded)   sha256 cc8ced09…c38f   1,343,488 bytes
Ulfsland deployed                  sha256 831dde63…9d5e   1,317,888 bytes
  manifest: version_number 1.4.17.1
            "Local build: upstream bb7d3cd6 (1.4.17) plus 1 valheim-portal patch(es).
             NOT an upstream release."
            website_url .../ServerCharacters/tree/bb7d3cd6
```

Different hash, different size — 1.4.16 is not installed and the never-install rule is being
honoured. **MEASURED**, `tools/valheim_mods.py:38-58` enforces this in code:
`LOCAL_BUILD_PACKAGES` shadows both the Thunderstore and Hexium registry entries for this
identifier, and `index()` documents that falling back to 1.4.16 "would be worse than failing".

**MEASURED, the Hexium side has not moved either**:
`valheim.hexium.gg/api/experimental/package/Smoothbrain/ServerCharacters/` → `latest 1.4.17`,
`2026-09-09T17:47:34Z`. Our 1.4.17.1 is that release plus our patch, so there is no upstream
delta to pick up from either backend.

### 6. Cooking on Ulfsland — broken by CreatureLevelAndLootControl, and there is no version that fixes it

**MEASURED**, today's boot, the full attribution:

```
335: InvalidProgramException: Invalid IL code in (wrapper dynamic-method)
     CookingStation:DMD<CookingStation::RPC_RemoveDoneItem>
     (CookingStation,long,UnityEngine.Vector3,int): IL_0040: call 0x00000009
350: Rethrow as HarmonyException: IL Compile Error
359:   at CreatureLevelControl.CreatureLevelControl.Awake ()
```

**MEASURED. `Smoothbrain-CreatureLevelAndLootControl` 4.6.4 is the newest published version**
(`date_created 2025-05-26T00:47:56Z`), and it is what is installed. There is no update, so there
is no version bump that fixes cooking.

This is bead `vhp-j4s` exactly: CLLC transpiles `CookingStation.RPC_RemoveDoneItem` and emits a
call to the pre-1.0 3-parameter `SpawnItem`; 1.0 appended a required `bool cheated`. The bead
also records that a shim forward was tried and **rejected** — emitting a 3-parameter sibling
turned CLLC's own by-name `SpawnItem` resolution into an `AmbiguousMatchException`, breaking the
mod the forward was meant to repair. So this is not a missing bridge; it is a closed door with a
written reason.

## Corrections — three briefed claims did not survive measurement

1. **Cooking is not CarryWeightSkill's fault.** The brief attributes
   `CookingStation::RPC_RemoveDoneItem` / `InvalidProgramException` to
   `95Shade-CarryWeightSkill`. **MEASURED**, the stack trace terminates at
   `CreatureLevelControl.CreatureLevelControl.Awake()`, the two failures are 9 seconds apart in
   the boot, and a reference scan of all 117 DLLs finds `RPC_RemoveDoneItem` in
   `CreatureLevelControl.dll` **only**. CarrySkill's own IL error is on
   `InventoryGui::RepairOneItem`. **Consequence: updating or removing CarryWeightSkill would not
   restore cooking.** That reframes the priority — the live gameplay defect belongs to a mod with
   no available fix and a previously-rejected workaround.
2. **CarryWeightSkill's DLL is not client-tree-only.** **MEASURED** present and identical in
   both `config_merged/bepinex/plugins/` and the live server overlay
   `data/bepinex/BepInEx/plugins/`. Its `scope: shared` declaration matches the filesystem here.
   (The general warning in the brief is still right in principle — declarations were checked
   against the filesystem, not trusted — it just does not apply to this package.)
3. **Five mods reference the blocked alias, not four.** `AdventureBackpacks` is the fifth, in
   both 2.0.1 and the candidate 2.0.4.

---

# Part 3 — recommendation

**`SYNCED`** = a server-side version change refuses un-updated clients at the handshake. A stale
client is REFUSED, not degraded, so every `SYNCED` item is one atomic window: server + all 20
client editions, or neither.

## Take now — safe, no client republish required

These four are server-side-only or tolerate the version skew, so they can go in without touching
any client edition. **MEASURED** lockstep attributes as above.

| Take | Why now |
|---|---|
| `Ross-RossItemDrawers` 1.0.3 → **1.0.5** | `Minor` strictness, so 1.0.3 clients are **not** refused. Fixes withdrawal from an unowned drawer, which is the default state of every drawer on a dedicated server — a live player-facing bug, not a version number. |
| `JamesJonesTV-Ravenwood_Currency` 1.0.0 → **1.0.1** | `Minor` strictness, patch-level move, **not** refused. No changelog, so no claimed behaviour change to verify. |
| `shudnal-ConfigurationManager` 1.1.20 → **1.1.21** | No network attribute. Admin-audience only (`admin`, `ulfsland-admin`, `ulfsland-dn`). `KeyCode` dropdown fix. |
| `Balrond-balrond_constructions` 1.4.4 → **1.4.5** | No network attribute. Shader/material and missing-reference fixes. |

## Needs a maintenance window — `SYNCED`, lockstep with all 20 client editions

| Take | Lockstep cost | Why it is worth the window |
|---|---|---|
| **`Vapok-AdventureBackpacks` 2.0.1 → 2.0.4** — `SYNCED` (`EveryoneMustHaveMod` / `Patch`) | Every client edition republished in the same window or refused | It is a **live item-duplication and inventory-reset defect** with the named trigger (`AzuCraftyBoxes` 1.8.18) deployed and announcing itself in the handshake. This is the one item on the list whose *not* taking it costs player data. The hold from the last window was for a sibling's weight measurement; if that sibling is done, this is the highest-value move available. |
| **`ZenDragon-ZenWorldSettings` 1.12.0 → 1.13.0** — `SYNCED` (`ClientMustHaveMod` / `Patch`) | Same window | Traders cannot sell the new Deep North inventory-expansion items on 1.12.0. **But** 1.13.0 also *replaces* the Quests section with a per-player Progression system — a config-shape change, not a fix. Do not take it for the trader fix without reading what happens to existing quest config. |
| `ZenDragon-Zen_ModLib` 1.14.0 → 1.14.2 | Ride along with ZenWorldSettings | `VersionCheckOnly`, so not itself a refusal, but 1.13.0 is recompiled against Zen.ModLib 1.14.1 — **[INFERENCE]** these two move together or not at all. Also brings the `1.0.11` (Microsoft) version-check acceptance. |
| `JereKuusela-Infinity_Hammer` 1.83.0 → 1.84.0 | None (no attribute) | Fixes an NRE from favourites + tools + keep-equip-on-death. Admin tooling; low risk, no window strictly needed — grouped here only because it is worth pairing with a build-menu-adjacent pass. |

## Hold deliberately

- **`warpalicious-More_World_Locations_AIO` 5.1.0 → 5.1.1.** **MEASURED**, 5.1.1's headline is
  *five new Swamp locations*, and its own changelog says reaching an existing world requires
  `Upgrade_World`'s `locations_add … start`. That is a **world-content mutation across five
  worlds**, not a bug fix, and it only lands in unexplored zones — so the result is permanently
  uneven and not undoable in explored terrain. **MEASURED**, 5.1.1 does **not** fix the defect
  we actually have: `Prefabs: Could not find LocationConfig for MWL_StoneOutlook1` and
  `DungeonPackRegistrar: pack 'UndergroundRuins' failed to register expected prefabs:
  8_PuzzleStand` are still in today's boot log on 5.1.0 (bead `vhp-0kc`), and the changelog does
  not mention either. Take it as a deliberate content decision with the worlds quiet, or not
  at all — not as a routine bump.

## Blocked — no action available

- **Cooking on Ulfsland.** `CreatureLevelAndLootControl` 4.6.4 is the newest published version;
  there is no fix to install, and the shim forward was already measured and rejected
  (`vhp-j4s`). **This is the fleet's one live gameplay defect and it has no version-number
  answer.** The only remaining routes are a local IL patch under `tools/modpatches/` targeting
  CLLC's transpiler, or dropping CLLC — both real decisions, neither a sweep item.
- **`Basements` 1.4.1, `OdinsHorsePen` 1.1.0, `OdinsUndercroft` 1.3.3,
  `RavenwoodRestorations` 1.0.1, and `AdventureBackpacks` (2.0.1 *and* 2.0.4).** No update
  exists for any of them; the `m_availablePieces` block is still logged verbatim in today's
  boot. Build pieces stay dead client-side. Note that taking AdventureBackpacks 2.0.4 for the
  duplication fix does **not** also clear this — it carries the same old-name reference.
- **`95Shade-CarryWeightSkill` 1.0.2.** Latest is installed and byte-identical, so there is no
  fix. Do **not** remove it server-side on the brief's reasoning: it is not client-tree-only, it
  is not server-synced, and it did not break cooking. Its one real open question is client-side
  item repair (`InventoryGui::RepairOneItem`), which needs a client boot to measure — and if it
  is broken there, removal is a both-sides lockstep move that must first answer what happens to
  `shadymods.carryskill.carry_weight` in the server-held `ServerCharacters` profiles.

## The server-side item that is not a mod

**Nothing to install** — buildid `25253791` is both installed and current. But the reconnaissance
found one live gap worth the operator's attention independently of any update:

> **`valheim_hang_watchdog.sh:93` restarts a world with a bare `docker restart`, which is a
> container start, which runs the in-container `steamcmd app_update` + rsync + overlay re-merge +
> `supervisorctl start valheim-server` sequence with no host gate at all.** It is armed on a
> two-minute timer (`valheim-hang-watchdog@Hrafnheim.timer`, active). It is harmless only because
> the public branch has not moved and the instance points at a stopped world.
>
> The gated route (`valheim-watchdog.timer` → `start_valheim_server.sh` →
> `require_matching_game_build`) would not refuse a legitimate game update either, because that
> gate compares the three *game* copies to each other and never sees the mod set — the limitation
> already written up in `2026-09-13-mod-set-provenance.md:46-49`.

Two changes close it, and they are independent: give `require_matching_game_build` the fourth
comparison that proposal specifies, **and** make `valheim_hang_watchdog.sh` restart through the
`stop_valheim_server.sh` / `start_valheim_server.sh` pair so the gate is in its path at all.
Neither is done here — this document installs, deploys and commits nothing.
