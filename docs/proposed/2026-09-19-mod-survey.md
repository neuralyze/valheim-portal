# Mod survey — updates, replacements, better alternatives — 2026-09-19

Read-only. **Nothing installed, deployed or published by this survey.** The Zen_ModLib
deploy and the client republish described below were done tonight by `ServerUpdate`, whose
window this is; I only measured the result.

This builds on `docs/proposed/2026-09-18-mod-update-and-piece-access-survey.md` and does not
repeat it. Where that survey's finding still stands I say so and move on; where the fleet or
Thunderstore has moved under it, the new value is here with its instrument.

---

## 0. What moved since the 2026-09-18 survey, and what the fleet is standing on now

### 0.1 The three anchors every claim below is checked against

| Anchor | Value tonight | Instrument |
| --- | --- | --- |
| Valheim server | **l-1.0.15 (network version 40)**, Steam buildid 25253791 → **25390671** | `ServerUpdate`: `appmanifest_896660.acf` buildid + `StateFlags` 6→4 + `UpdateResult` 6→0, and the console line `Valheim version: l-1.0.15 (network version 40)` at 01:24:52; `assembly_valheim.dll` 2,560,000 → 2,561,536 bytes, sha256 `1231fc2f…` → `6d79c900…` |
| Valheim client | **1.0.15** | `ServerUpdate`: client BepInEx fatal block, `Valheim installed: 1.0.15, required: (SteamWindows, 1.0.12)…` (pre-update) |
| **BepInEx loader — RUNNING** | **5.4.2333, unchanged** | `ServerUpdate`, this boot: `[Message: BepInEx] User is running BepInExPack Valheim version 5.4.2333 from Thunderstore`; `bepinex-updater` reported *"Local BepInEx archive is identical to remote archive"*. **Every floor in this document is checked against 2333, not against the 5.4.2350 manifest pin.** |

`ServerUpdate` corrected its own message mid-sentence on the loader; the unambiguous value is
**5.4.2333 running / 5.4.2350 pinned**, and I have used 2333 throughout.

### 0.2 The 5.4.2350 floor is advisory on this fleet — measured, not assumed

Ten of the sixteen update rows below declare `denikson-BepInExPack_Valheim-5.4.2350`, which is
**above** the running loader. That looks like a blocker. It is not, and the proof is already on
disk:

> **MEASURED: 37 of the 101 installed plugin directories carry an on-disk `manifest.json` that
> already declares `denikson-BepInExPack_Valheim-5.4.2350`, and they are running right now on
> loader 5.4.2333.** (`AdventureBackpacks`, `Basements 2.0.0`, `Blacksmiths_tools 3.0.0`,
> `ConditionalConfigSync`, `DeathPenalty`, `DigDeeper`, `Endurance`, `Herbalist`, `HipLantern`, …)

and `ServerUpdate` measured this boot, across the whole 123-DLL set and across a
1.0.12 → 1.0.15 game jump: **zero `MissingMethodException`, zero `MissingFieldException`, zero
`TypeLoadException`, zero "Failed to patch", zero `[Error` lines.**

So: a declared 5.4.2350 floor is a Thunderstore-manager hint here, not a runtime gate. I have
still tabulated it per row, because it is the thing that would block a *manager-driven* install,
but I am not classifying any row as blocked on it. Two rows declare 5.4.2333 exactly
(`EpicLoot`, `FearMe`, `BuildOnShip`) and two declare 5.4.2202 (`PotteryBarn`,
`ServerCharacters`) — all satisfied outright.

### 0.3 Deployed since the last survey — reported once, not again

| Package | Was | Now on the Ulfsland tree | When |
| --- | --- | --- | --- |
| `ValheimModding-Jotunn` | 2.30.0 | **2.30.1** | prior wave |
| `OdinPlus-Basements` | 1.4.1 | **2.0.0** | prior wave |
| `blacks7ar-Wisdom` | 1.0.5 | **1.0.6** | prior wave |
| `OdinPlus-OdinsKingdom` | 1.5.8 | **1.5.9** | prior wave |
| `AzuCraftyBoxes` | fork 1.8.18 | **official `Azumatt-AzuCraftyBoxes` 1.8.19** | prior wave |
| `warpalicious-More_World_Locations_AIO` | hold at 5.1.0 | **5.1.1, hold closed** | prior wave |
| **`ZenDragon-Zen_ModLib`** | **1.14.3** | **1.14.7** | **tonight, `ServerUpdate`** |

**Artefact-verified, not manifest-verified.** The deployed
`plugins/Zen_ModLib/Zen.ModLib.dll` is **254,464 bytes, sha256 `7bc322bf573126b8…`**, and a
`cmp` against a fresh `ZenDragon-Zen_ModLib-1.14.7.zip` pulled from
`gcdn.thunderstore.io` today returns **byte-identical**. Reading its `#US` heap: 687 user
strings, and the literal **`ABORT: Valheim version mismatch.` is ABSENT** — the surviving
banner is the non-fatal `!!! VERSION MISMATCH !!!` block. That is the string the client logged
seven times, and it is gone.

`ServerUpdate` adds the live config as the stronger instrument:
`Ulfsland/config_merged/bepinex/ZenDragon.Zen.ModLib.cfg` line 46 now reads
`## Required versions: (SteamWindows, 1.0.15), (SteamLinux, 1.0.15), (MicrosoftStore, 1.0.15)`
— it was `(1.0.12, 1.0.12, 1.0.11)`. With server and client both at 1.0.15 the check now
**passes outright**; the non-fatal relaxation is a second line of defence, not the thing
carrying it.

**Correction to the 2026-09-18 survey, and to this ticket's own brief:** the line
*"updated for support Valheim version v1.0.14"* belongs to **1.14.4**, not 1.14.5. 1.14.5 is a
container-scrollbar grid fix, 1.14.6 adds an *Unencumbered Gods* config, and only **1.14.7**
carries:

> v1.14.7
> - relaxed valheim version check to be non-fatal.
> - recompile for Valheim v1.0.15

**1.14.5 would not have fixed the connect failure.** (`ServerUpdate` measured the `ABORT`
literal *present* in both the 1.14.5 and 1.14.6 DLLs.)

**And the game update was not strictly required — worth recording, because it changes what the
failure actually was.** MEASURED by `ServerUpdate`: **network version 40 is shared across
1.0.12 and 1.0.15**, and the server log showed the client reaching `RPC_PeerInfo` and sending
its mod version data, so the *vanilla* handshake was passing across the version gap all along.
Nothing in Valheim rejected the client. **The rejection was entirely Jotunn's mod-compat check,
fed by six Zen mods that `Zen_ModLib` 1.14.3 refused to let load.** The 1.0.12 → 1.0.15 move
was taken not to fix the handshake but because **1.14.7 is a 1.0.15 recompile**, and running a
1.0.15-compiled library against a 1.0.12 server invites runtime faults; aligning both ends also
turns the version gate from *non-fatal* into *passing outright*. So the causal chain, end to
end, is: library version gate → six mods fail to load client-side → Jotunn reports six missing
mods → handshake refused. **Not a Valheim version problem, despite a Valheim version being the
thing that changed.**

**The SERVER side is now proven at runtime — and that is a narrower claim than it looks.**
`ServerUpdate`'s final boot, re-checked after 32 minutes of uptime rather than at readiness:
`Valheim version: l-1.0.15 (network version 40)`, `Game server connected`, **all seven Zen
components register** (`Zen.ModLib server mode: Active` through `ZenWorldSettings server mode:
Active`), **zero errors, zero exceptions and zero `[Fatal` lines** across those 32 minutes,
and the world intact behind it at **`ZDOS:433809`, stable across two heartbeats**. The deployed
library is md5 `fd15e831…` on the executing overlay. The server has *held* the new build under
its full mod set, not merely booted into it.

**What that does and does not establish, kept deliberately apart.** The six Zen mods were
failing to load on the **client**, and neither of us has an instrument on the operator's
machine — so:

| Claim | Status |
| --- | --- |
| Server registers all seven Zen components under 1.0.15 | **MEASURED** — boot log, 32 min uptime |
| The `ABORT: Valheim version mismatch.` literal is absent from the 1.14.7 binary | **MEASURED** — `#US` heap, two independent readers |
| The published 1.0.21 edition carries that same binary | **MEASURED** — identical 137,646-byte zip; artefacts re-hash to their DB `sha256` |
| ~~Server no longer emits `Missing mod on client`~~ | **NOT EVIDENCE — the zero is vacuous.** `Connections 0` at both the 01:35:43 and 01:45:43 heartbeats, and no `RPC_PeerInfo`, no `Got connection`, no disconnect of any kind since the 01:22 restart. **No client has attempted a join in that window at all**, so the absent line is *absence of a trial*, not a passed trial |
| *The client will therefore load the six Zen mods and join* | **`[INFERENCE]`** — well-founded (1.14.7 requires 1.0.15, the client **is** 1.0.15, the fatal literal is gone from the binary the published edition ships) but **not observed** |

So the honest measured set is **three rows, not four.** `ServerUpdate` caught the fourth by
going and counting connections rather than leaving my caveat as a caveat — which is the right
treatment, because a zero from an event that never had the chance to fire is the most
comfortable kind of wrong number.

His formulation is the one to keep: *"the handshake path is clear" is a statement about the
path, not about a completed traversal.* The first genuinely measured client-side fact will be
`state.json` reading **1.0.21 / 1.14.7**; the second will be a join that **appears** in the
server log and does **not** end in a disconnect. On a fleet where a config file was mistaken
for an installed mod eighteen times, that distinction is worth the extra lines.

Everything left is client-side, and it is §0.4.

### 0.4 The one thing still standing between the operator and a join

**MEASURED, `/mnt/vps-sync/profiles/Ulfsland--ulfsland-vr-flat-admin--flat/state.json`
(mtime 2026-09-19 00:50:54, i.e. *before* the 01:2x republish):**

```
release_id = ulfsland-vr-flat-admin-1.0.20
portal     = https://valheim.neuralyze.com
ZenDragon  Zen_ModLib  1.14.3
```

and `/mnt/vps-sync/packages/` holds `ZenDragon-Zen_ModLib-1.14.0.zip` and `-1.14.3.zip` —
**no 1.14.7 zip**, directory last written 00:50:55.

**What that mount is, corrected by `ServerUpdate` after I first read it the wrong way.**
`/mnt/vps-sync` is the **client's own cache mirrored back here**, not a share anybody
publishes into — the `portal` field in the same file shows the client pulls from
`https://valheim.neuralyze.com` and rewrites `state.json` itself. So the 1.0.20 / 1.14.3
content is **a record of the operator's last sync at 00:50:54, not a publishing failure**.
There is nothing missing on the serving side and nothing for anyone to write into that mount.

**The serving side is verified good.** `ServerUpdate`, portal-side: all four `ulfsland-*`
**1.0.21** rows are `status='published'`, every 1.0.20 row flipped to `'archived'`,
`maintenance=0` on all four, `ulfsland-vr-flat-admin-1.0.21` keeps `audience='admin'`, and
both its artefacts re-hash on disk to exactly their DB `sha256`
(profile 103,712 B `8ecf9d31…`, flat_companion 955,736 B `627c6175…`).

So the server tree is on 1.14.7, the four `ulfsland-*` manifests pin 1.14.7, the portal is
serving 1.0.21 — and the **operator's client is still on edition 1.0.20 carrying 1.14.3
because it has not synced since 00:50:54.** **The operator must re-run the client sync before
joining**, otherwise the client-side gate still fires and Jotunn still reports six missing
mods. This is a sync step, not a mod problem.

**Acceptance, and read the artefact rather than the exit code:** after syncing,
`state.json` must read **`release_id = ulfsland-vr-flat-admin-1.0.21`** and
**`Zen_ModLib 1.14.7`**. Read those two fields. If `release_id` still says 1.0.20 the sync did
not take, and nothing else is worth debugging yet.

**Out of `ServerUpdate`'s declared scope, flagged rather than assumed done:** the three
*shared* profiles `admin`, `flat` and `vr` still pin `ZenDragon-Zen_ModLib` at **1.14.3**
(measured across all seven `profile-manifest.json` files). Any client edition built from those
primaries will carry the broken gate. Only the four `ulfsland-*` manifests were moved.

### 0.5 Instruments used, and the three that are banned here

| Instrument | Where it is used | Note |
| --- | --- | --- |
| `dnfile` 0.18.0 metadata tables + my own IL body walker (`/tmp/ms2/deadref2.py`) | every dead-field and registration count | independently written this session; reproduces the 2026-09-18 numbers exactly — see the positive controls below |
| `.NET #US` user-string heap reader (`/tmp/ms2/us.py`) | every prefab-literal and orphan check | UTF-16LE, length-prefixed, walked from heap byte 1 |
| ECMA-335 field-signature decoder | separating the two 1.0 fields that share the name `m_availablePieces` | the *only* thing that tells them apart |
| Thunderstore `/c/valheim/api/v1/package/` | version, date, dependants, floors | fresh pull **2026-09-19 06:07 UTC, HTTP 200, 167,456,511 bytes, 11,463 packages** |
| Thunderstore `api/experimental/.../changelog/` | every quoted changelog line | fetched today |
| `gcdn.thunderstore.io` package zips | every "is installed the same as published" claim | `cmp`, not version strings |

**Banned, and why:** `strings` over an asset-bundle-carrying DLL is invalid on this fleet — it
produced 95 phantom piece removals from compression noise. Every prefab claim below comes from
the `#US` heap. And a package instrument is not a runtime instrument: `manifest.json` says what
was *intended*, the DLL says what is *there*.

**Positive controls — my instrument against the previous survey's, same numbers:**

| Quantity | 2026-09-18 (PieceAccess/ModDelta2, different code) | Today (`/tmp/ms2`) |
| --- | --- | --- |
| `RavenwoodVikingHouses` Jotunn `AddPiece` sites | 131 | **131** |
| `RavenwoodRestorations` `BuildPiece` ctor sites | 113 | **113** |
| `OdinsKingdom` `BuildPiece` ctor sites | 278 | **278** |
| `OdinsUndercroft` / `OdinsHorsePen` / `Basements` | 2 / 3 / 2 | **2 / 3 / 2** |
| dead `m_availablePieces` refs per carrier | 5 | **5** |
| `AddRWPieces` IL body, 7.7.8 vs 2.0.5 | 1 byte `ret` vs 65 bytes | **1 vs 65** |
| DLLs patching `PieceTable.UpdateAvailable` | 10 + Jotunn | **11** (10 + Jotunn) |

Seven independent reproductions. The measurement below is not resting on one tool.

---

## 1. Step 1 — the delta, current as of 2026-09-19

Scope as assigned: the **Ulfsland server tree** (101 plugin dirs, 98 mapped to a package) and
the **four `ulfsland-*` profile manifests**, plus the three Ulfsland client profiles for the
deployment gap. The four stopped worlds and the shared `admin`/`flat`/`vr` profiles are out of
scope and untouched, except for the Zen_ModLib flag in §0.4.

**Shape:** 98 server dirs map to a package. **83 sit at Thunderstore newest. 15 disagree with
newest — of which 14 are genuinely behind and 1 (`ServerCharacters`) is deliberately ahead.**

### 1a. Server tree disagreeing with newest — 15 rows (14 behind, 1 ahead by design)

Ordered by dependants. `TS-moved` marks a package whose Thunderstore newest changed **after**
the 2026-09-17 21:33 index pull the last survey used — i.e. **nine of these fifteen rows are
new information**, not a re-report. Two of them (`shudnal-ProtectiveWards` 2.0.11,
`tulivu-FearMe` 1.0.1) were published **today**, as was `shudnal-HarpoonExtended` 1.2.0, which
is pinned in the manifests but installed nowhere (§1d).

| # | Package | Installed | Newest | Published | Dep. | BepInEx floor (vs **running 2333**) | Jotunn floor (vs 2.30.1) | Class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `RandyKnapp-EpicLoot` | 0.14.7 | **0.14.10** `TS-moved` | 2026-09-18 | 1831 | 5.4.2333 ✓ | 2.29.2 ✓ | **DANGEROUS** |
| 2 | `Vapok-AdventureBackpacks` | 2.0.7 | **2.0.9** `TS-moved` | 2026-09-18 | 882 | 5.4.2350 ⚠ advisory | 2.30.1 ✓ | SAFE |
| 3 | `GoldenJude-Judes_Equipment` | 2.3.0 | **3.0.0** `TS-moved` | 2026-09-18 | 857 | 5.4.2350 ⚠ advisory | — | **DANGEROUS** |
| 4 | `OdinPlus-OdinsFoodBarrels` | 1.2.6 | **1.2.8** `TS-moved` | 2026-09-18 | 838 | none declared | — | SERVER-SIDE |
| 5 | `MidnightMods-ValheimArmory` | 1.31.0 | **1.32.0** `TS-moved` | 2026-09-18 | 494 | 5.4.2350 ⚠ advisory | 2.30.0 ✓ | SERVER-SIDE |
| 6 | `shudnal-TradersExtended` | 2.0.2 | **2.0.3** | 2026-09-17 | 391 | 5.4.2350 ⚠ advisory | — | SAFE |
| 7 | `shudnal-HipLantern` | 1.1.8 | **1.1.9** `TS-moved` | 2026-09-18 | 305 | 5.4.2350 ⚠ advisory | — | SAFE |
| 8 | `shudnal-LongshipUpgrades` | 1.0.20 | **1.0.21** `TS-moved` | 2026-09-18 | 205 | 5.4.2350 ⚠ advisory | — | SAFE |
| 9 | `ComfyMods-PotteryBarn` | 1.21.0 | **1.22.0** `TS-moved` | 2026-09-18 | 156 | 5.4.2202 ✓ | 2.30.1 ✓ | SERVER-SIDE |
| 10 | `MidnightMods-ImpactfulSkills` | 0.16.1 | **0.17.1** | 2026-09-17 | 135 | 5.4.2350 ⚠ advisory | 2.30.1 ✓ | **DANGEROUS** |
| 11 | `shudnal-ProtectiveWards` | 2.0.10 | **2.0.11** `TS-moved` | 2026-09-19 | 128 | 5.4.2350 ⚠ advisory | 2.30.0 ✓ | **DANGEROUS** |
| 12 | `tulivu-FearMe` | 1.0.0 | **1.0.1** `TS-moved` | 2026-09-19 | 20 | 5.4.2333 ✓ | — (dropped) | SERVER-SIDE |
| 13 | `Ross-RossItemDrawers` | 1.0.8 | **1.0.11** `TS-moved` | 2026-09-18 | 6 | 5.4.2350 ⚠ advisory | 2.30.0 ✓ | SERVER-SIDE |
| 14 | `Skarif-BuildOnShip` | 3.0.112 | **3.2.1** `TS-moved` | 2026-09-18 | 4 | 5.4.2333 ✓ | — | **DANGEROUS** |
| 15 | `Smoothbrain-ServerCharacters` | **1.4.17.1** | 1.4.16 | 2025-05-02 | 1566 | 5.4.2202 ✓ | — | **DO NOT TOUCH** |

Row 15 is not behind. Its own on-disk `manifest.json` reads
*"Local build: upstream bb7d3cd6 (1.4.17) plus 1 valheim-portal patch(es). NOT an upstream
release."* A bulk "update to newest" pass would **downgrade it to 1.4.16 and silently drop the
portal patch.** Exclude it by name. Unchanged from the last survey, restated because it is the
one row where "newest" is the wrong answer.

### 1b. Justification for every DANGEROUS call — each with its quoted line

**1 · `RandyKnapp-EpicLoot` 0.14.7 → 0.14.10 — DANGEROUS, and the biggest single win here.**
Three separate world-data defects are fixed across 0.14.8–0.14.10. Quoted from the
Thunderstore changelog for `RandyKnapp/EpicLoot/0.14.10`:

> **0.14.10**
> * Fix for items losing their enchantment when taken off an armor stand
> * Fixes for the bounty ledger which could reset when the server was restarted
>   * This could result in bounties being marked incomplete after being completed (but not turned in before a server restart)
> * Fixes for not persisting bounty or treasuremap data to the character
> * Fix for magic effect values being permanently reset to 1 when an item was reloaded
>   * **This hit Ancient items on servers whose magiceffects.json predates the Ancient rarity, since a server replaces the client's enchantment definitions on connect**

and, still uncollected from 0.14.8:

> * Increases placement allowance for bounties/treasures
> * Removes bounty/treasure relocation due to the potential for schisms

DANGEROUS because it writes to **character and item data** (`m_magicEffects` values,
bounty/treasure persistence) and to world-object placement. But note the direction: the current
0.14.7 is the version that **destroys rolled enchantment values** on a server whose
`magiceffects.json` predates Ancient rarity. Holding is not the safe option here; holding is the
option that keeps corrupting items. This is the row I would take first.

**2 · `GoldenJude-Judes_Equipment` 2.3.0 → 3.0.0 — DANGEROUS (config loss + a live library skew).**
Quoted in full:

> ## 3.0.0 JOTUNN PORT
> * Updated for game version 1.0.15
> * New external dependency for Jotunn. You must install this for the mod to function!
> * **Custom configurations currently not supported.**
> * Fixes an issue when logging out and back in multiple times in the same game session.

DANGEROUS on the third line: a major port that drops custom configuration support. **Measured
cost of that loss: `GoldenJude_JudesEquipment.cfg` on the Ulfsland tree is 463 bytes** — so the
loss is small and quantified, not a guess.

The reason this row is more urgent than its class suggests is a **skew that is already live and
was not in the last survey**. MEASURED from the two on-disk manifests:

```
Judes_Equipment   2.3.0   dependencies: [ ..., GoldenJude-Blacksmiths_tools-2.0.2, ... ]
Blacksmiths_tools 3.0.0   (installed, at newest, published 2026-09-13)
```

**The library was already major-bumped to 3.0.0 under a consumer that still declares 2.0.2.**
Judes 3.0.0 declares `GoldenJude-Blacksmiths_tools-3.0.0`, so taking it *resolves* the skew.
Leaving it leaves a 1.x consumer on a 3.x library. (No error appeared in `ServerUpdate`'s boot,
so the skew is not currently throwing — recorded as a latent, not an active, fault.)

**3 · `MidnightMods-ImpactfulSkills` 0.16.1 → 0.17.1 — DANGEROUS (placement geometry on
already-planted ground).** 0.17.1 itself is only `- Bump dependency versions again`; the
danger rides in 0.17.0, which this bump carries:

> - Grid spacing now accounts for the plant's own collider, not just its grow radius, which is what the game actually measures against. Barley and flax grids are wider; every other crop and all tree saplings keep the spacing they had
> - **An existing field that was already planted too tightly will now show red cells rather than letting you feed more doomed crops into it**
> - Farming level 50 (FarmingBiomeUnrestrictedLevel) lets your plants grow in any biome, ignoring the biome they were planted in as well as the heat of the Ashlands and the cold of the Mountains and Deep North

Changes behaviour on **already-planted** fields, and grants a biome override that persists on
the plant. Hard coupling: 0.17.1 declares `ValheimModding-Jotunn-2.30.1` — **already met**,
Jotunn 2.30.1 is deployed. The coupling that blocked this row in the last survey is gone.

**4 · `shudnal-ProtectiveWards` 2.0.10 → 2.0.11 — DANGEROUS (rewrites per-ward stored settings).**
Quoted:

> # 2.0.11
> * **Simplified per-ward settings: new wards copy their initial values from the global config and then keep explicit independent values without `Use default` fields.**
> * Added a per-ward option allowing permitted players to activate or deactivate the ward with the normal interaction.
> * **Fixed custom ward radius initialization on remote clients by synchronizing runtime ward fields before `PrivateArea.Awake`.**

DANGEROUS because it changes the **per-ward persisted setting model** (`Use default` fields
removed) and touches `PrivateArea` field initialisation. Wards are placed world objects with
per-instance ZDO state, and this world has `guard_stone` placed. The changelog does not say
existing wards are migrated, and I will not invent that they are.

**5 · `Skarif-BuildOnShip` 3.0.112 → 3.2.1 — DANGEROUS (ZDO zone sync for built pieces on ships).**
Quoted:

> ## v3.2.1
> - nothing
>
> ## v3.2.0
> - **Fix 17.09.26 patch & fix ZDOzone sync portal on ship (build)**
>
> ## v3.1.0
> - Fix black screen after poral teleport + object work optimization

DANGEROUS because "ZDOzone sync" is literally the mapping between a built object's ZDO and the
zone it is indexed under; getting that wrong is how pieces on a moving ship get lost. It is
also the row that most plausibly *fixes* an existing hazard: 3.2.0 names the **2026-09-17
patch** explicitly, and the fleet is running the pre-patch 3.0.112 against a game that has since
moved twice (1.0.12 → 1.0.15). Lowest dependants on the list (4), so upstream exposure is thin.

Honest note on the class: `3.2.1 - nothing` is the author's own entire changelog for the newest
version. That is not a reason to distrust 3.2.0, but it is a reason to pin **3.2.0 semantics**
in your head, not 3.2.1's.

### 1c. The SERVER-SIDE and SAFE rows, each with its line

| Package | Quoted line | Why that class |
| --- | --- | --- |
| `OdinPlus-OdinsFoodBarrels` 1.2.6 → **1.2.8** | `### Version 1.2.8` / `- Fixed piece catagory with build hammer.` and `### Version 1.2.7` / `- Added new Deep North resources.` | SERVER-SIDE, **purely additive — measured**. `#US` delta 1.2.6 → 1.2.8: **0 literals removed**, 11 added, of which four are new prefabs `OH_Kale`, `OH_LingonBerries`, `OH_Oats`, `OH_Poteitr`. The 1.2.8 line is also a *second* fix for exactly the 1.0 hammer-category defect this fleet has been chasing. |
| `MidnightMods-ValheimArmory` 1.31.0 → **1.32.0** | `- Adds a LoadPrefabsOnServer config to register Valheim Armory items on a dedicated server, for server side mods that need to resolve them` | SERVER-SIDE and directly useful: this fleet runs `EpicLoot`, `TradersExtended` and `ServersideQoL`, all of which resolve item prefabs server-side. Also `- Fixes the blackmetal spear and blackmetal dual axes not hitting targets at or below the player's elevation`. |
| `ComfyMods-PotteryBarn` 1.21.0 → **1.22.0** | `* Fixed for the v1.0.14 patch.` / `* Updated Jotunn dependency to v2.30.1.` | SERVER-SIDE, and **measured to be a pure recompile**: `#US` identifier-literal sets are **184 = 184, zero removed, zero added**. There is no recipe change hiding in it. Its Jotunn 2.30.1 floor is already met. |
| `Ross-RossItemDrawers` 1.0.8 → **1.0.11** | `## 1.0.9 - Built and tested against Jotunn 2.30.1, which the dependency now asks for. Nothing else changed.` · `## 1.0.10 - Drawers no longer feel sluggish on a dedicated server.` | SERVER-SIDE. `#US` delta 1.0.8 → 1.0.11: **0 removed**, 1 added (`piece`). The **coupling discrepancy from the last survey persists**: the changelog says Jotunn 2.30.1, the manifest declares 2.30.0. Installed 2.30.1 satisfies both. |
| `tulivu-FearMe` 1.0.0 → **1.0.1** | `- Rework config` / `- No longer requires Jotunn` / `- Updated with Deep North gear/mobs` | SERVER-SIDE. **`Rework config` means `tulivu.valheimmods.fearme.cfg` will be regenerated** — back it up if it has been tuned. Dropping the Jotunn dependency reduces coupling. |
| `shudnal-TradersExtended` 2.0.2 → **2.0.3** | `* fixed gamepad stick input reaching the game and free-fly camera through Valheim 1.0.14 vector getters while the configuration editor blocks game input` | SAFE, client input/UI. Its `shudnal-ConditionalConfigSync-1.0.5` floor is met (installed 1.0.8). |
| `shudnal-HipLantern` 1.1.8 → **1.1.9** | `* Made lantern and light/heat switch sounds follow the game's Master and SFX volume settings.` | SAFE, client audio only. |
| `shudnal-LongshipUpgrades` 1.0.20 → **1.0.21** | `* Routed the ship lantern and part-switch sounds through the game's volume controls without changing shared vanilla effects.` | SAFE, client audio only. The clause *"without changing shared vanilla effects"* is the author explicitly disclaiming side effects. |
| `Vapok-AdventureBackpacks` 2.0.7 → **2.0.9** | 2.0.9: `* Bugs Fixed from Submitted Error Reports: 5x NullReferenceExceptions, 3x ArgumentNullExceptions, 3x TypeLoadExceptions, 1x AmbiguousMatchException, 1x JsonReaderException, 1x FieldAccessException` and `* Telemetry is now unchecked when first loaded (Opt-In visibility)` | SAFE. **Operator-relevant:** the installed 2.0.6 shipped telemetry *"Defaults to enabled with auto-opt-in on launch"*; **2.0.9 flips it to opt-in and adds an in-game privacy policy**. That is a privacy improvement the fleet currently does not have. 3x `TypeLoadException` fixes matter on a 1.0.15 server. |

### 1d. Profile manifests — 18 pins behind, and two of them are scope bugs

All four `ulfsland-*` manifests agree with the Ulfsland server tree on every package, with
**three** exceptions, all measured:

| Identifier | Manifest pin | Server tree | What it is |
| --- | --- | --- | --- |
| `Azumatt-AzuExtendedPlayerInventory` | 2.4.8, `scope: shared` in `ulfsland-admin`/`-flat`/`-vr`; `client_only_packages` in `ulfsland-dn` | **absent from every server tree** | scope inversion — flagged in the last survey, **still open** |
| `Azumatt-AAABuildMenu` | 1.0.4, `scope: shared` in `ulfsland-admin`/`-flat` | **absent from every server tree** | same shape, newer. Client-side build-menu mod marked `shared` |
| `shudnal-HarpoonExtended` | 1.1.12 in the manifests | absent from every server tree; **`disabled_packages` residue** | pin exists for a package nothing installs |

The `shared` scope on two client-only mods is the live risk, not a cosmetic one: a future
`manage_mods.sh <World> deploy --apply` reading `scope: shared` is entitled to push
`AzuExtendedPlayerInventory` and `AAABuildMenu` **onto the server**, where neither belongs.
Fix the scope before the next apply, not after.

`HarpoonExtended` has also moved upstream: **1.1.12 → 1.2.0 (2026-09-19)**, and its changelog
is a scope change, not a bump:

> # 1.2.0
> * Updated for Valheim 1.0.15.
> * **Switched to standalone Conditional Config Sync; the mod is now required on the server and clients.** Removed the Enabled setting.

So if that pin is ever revived it becomes a server-side package. Today it is a dangling pin;
**remove the pin** rather than update it.

### 1e. Client-only packages behind

| Package | Client | Newest | Note |
| --- | --- | --- | --- |
| `95Shade-CarryWeightSkill` | 1.0.1 (10 profiles) | **1.0.2** (2026-09-15) | The last survey's correction stands: a fix exists, the "no upstream fix, slate for removal" premise was wrong. Description: *"Compatible with Valheim Sept 2026 (Skills.RaiseSkill / MessageHud fix)."* Never on any server tree → zero world risk. Fallback if 1.0.2 still fails: `Searica-SkilledCarryWeight 1.5.0` (2026-09-15, 109 dependants, already installed at 1.4.1 on two profiles). |
| `Azumatt-AzuExtendedPlayerInventory` | 2.4.8 | **2.4.14** | The fleet pinned **2.4.8 while 2.4.14 already existed** (both under a 2026-09-14 package date). 2.4.9–2.4.14 are all defect fixes: `2.4.12 • Fix some issues with inventory dynamic sizing. Fix loadouts, Fix Favoriting`; `2.4.13 • Restore the full inventory grid layout after death.`; `2.4.14 • AzuAutoStore compat fixes for favoriting.` **"Restore the full inventory grid layout after death" is a bug the operator will hit.** Pinning 2.4.8 over 2.4.14 looks like a string-sort (`2.4.8 > 2.4.14` lexically); worth checking the pinning path. |

### 1f. Client deployment gap — the largest number in this delta, and it is not upstream

Measured today, each Ulfsland client profile's installed set against the **live Ulfsland server
tree** (not against Thunderstore):

| Client profile | Packages disagreeing with the live server |
| --- | --- |
| `Ulfsland--ulfsland-vr-flat-admin--flat` | **1** — `Zen_ModLib` 1.14.3 vs 1.14.7 (the §0.4 sync lag) |
| `Ulfsland--ulfsland-non-vr--flat` | **36** |
| `Ulfsland--ulfsland-vr-flat--flat` | **36** |

The same 36, identically, on both non-admin profiles. Highlights, because these are not all
cosmetic: `Jotunn` 2.30.0 vs **2.30.1** (the build-menu category fix), `Basements` 1.4.1 vs
**2.0.0** (a major version), `Zen_ModLib` **1.14.0** vs 1.14.7 (three generations of the
version gate), `Valheim10Compatibility` 1.4.0 vs 1.4.1, `BuildOnShip` 2.2.14 vs 3.0.112,
`RossItemDrawers` 0.9.9 vs 1.0.8, `ValheimArmory` 1.29.3 vs 1.31.0.

`Valheim10Compatibility 1.4.0 → 1.4.1` still deserves its own emphasis, quoting its changelog:

> Because the Adapter bytes change, an AzuAntiCheat server must replace its whitelist folder
> with the 1.4.1 package (not add it beside 1.4.0 — two versions of one plugin in the whitelist
> lock every non-admin client out), and every client must update to 1.4.1 before joining.

**Those two profiles cannot join a Zen-gated server at all** — they are on `Zen_ModLib` 1.14.0,
which is *older* than the 1.14.3 that failed tonight. One client catch-up deploy closes all 36
rows, and it is free of world risk.

---

## 2. Step 2 — replacements: better mods instead of old ones

### 2.1 The three unfixed dead-field carriers

**Has any shipped a rebuild? No. Measured three ways.**

*Version, from today's index pull:*

| Package | Installed | Thunderstore newest | Published | Versions ever |
| --- | --- | --- | --- | --- |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | **1.3.3** | 2026-07-24 | 1.3.3 is the head |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | **1.1.0** | 2026-02-05 | 1.1.0 is the head |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | **1.0.1** | 2026-09-08 | 2 |

*Artefact, not version string — `cmp` of the installed DLL against a zip pulled from
`gcdn.thunderstore.io` today:*

```
IDENTICAL  OdinUndercroft.dll           3,546,112 bytes  sha256 de29673cc55b69b7…
IDENTICAL  OdinsHorsePen.dll              860,672 bytes  sha256 949083e757d51774…
IDENTICAL  RavenwoodRestorations.dll   59,241,984 bytes  sha256 090435b6b7c95dc3…
```

*IL, as assigned — `ldfld` operand references to a `MemberRef` named `m_availablePieces` on
`PieceTable`, split by decoded field signature:*

| DLL | `m_availablePieces` **`List<List<Piece>>`** (dead) | `m_availablePieces` `HashSet<Piece>` (1.0, legitimate) | `m_availablePiecesByCategory` **`List<List<Piece>>`** (correct) |
| --- | --- | --- | --- |
| `OdinUndercroft.dll` 1.3.3 | **5** | 0 | 0 |
| `OdinsHorsePen.dll` 1.1.0 | **5** | 0 | 0 |
| `RavenwoodRestorations.dll` 1.0.1 | **5** | 0 | 0 |
| `Basements.dll` **2.0.0 (deployed)** | **0** | 0 | **5** |
| `OdinsKingdom.dll` 1.5.9 | 0 | 0 | 2 |
| `OdinsFoodBarrels.dll` 1.2.6 | 0 | 0 | 2 |

All five dead references in each carrier sit in the same two methods —
`PieceManager.PiecePrefabManager::UpdateAvailable_Prefix` (3 sites, at IL offsets +1, +20, +37)
and `::UpdateAvailable_Postfix` (2 sites, at +7 and +29). **Zero means fixed. These are 5.**

**A full-tree sweep confirms these are the only three left.** All 123 DLLs on the Ulfsland
plugin and patcher trees (`winhttp.dll` is native, no CLI metadata, skipped):

```
DEAD (pre-1.0 List<List<Piece>>):   OdinsHorsePen 5 · OdinsUndercroft 5 · RavenwoodRestorations 5
LEGITIMATE (1.0 HashSet<Piece>):    AdventureBackpacks 5 · Infinity_Hammer 7 · Jotunn 2 · OdinShip 1 · RossItemDrawers 1
```

Basements has dropped off the dead list since the last survey — **the 2.0.0 deploy is measured
to have worked.**

#### 2.1.1 The blast radius, quantified — this is why one carrier matters

**MEASURED: 11 DLLs patch `PieceTable.UpdateAvailable`** — ten bundled-`PieceManager` copies
plus Jotunn's own transpiler:

```
AdventureBackpacks · Basements · CraftyCartsRemake_1_0_Fix · OdinCampsite · OdinShip ·
OdinsFoodBarrels · OdinsHorsePen · OdinsKingdom · OdinsUndercroft · RavenwoodRestorations
+ Jotunn (transpiler only)
```

and **435 piece registrations ride that chain**:

| Plugin | `BuildPiece::.ctor` sites | Dead refs |
| --- | --- | --- |
| `OdinsKingdom` | **278** | 0 |
| `RavenwoodRestorations` | 113 | **5 — aborts** |
| `OdinShip` | 18 | 0 |
| `CraftyCartsRemake_1_0_Fix` | 8 | 0 |
| `OdinCampsite` | 5 | 0 |
| `EpicLoot` | 3 | 0 |
| `OdinsHorsePen` | 3 | **5 — aborts** |
| `Basements` | 2 | 0 |
| `OdinsFoodBarrels` | 2 | 0 |
| `OdinsUndercroft` | 2 | **5 — aborts** |
| `AdventureBackpacks` | 1 | 0 |
| **total** | **435** | 15 |

**Only 118 of the 435 belong to the three broken carriers. 317 belong to innocent mods** — and
`OdinsKingdom` alone is 278 of them. That is the shape of the problem in one number: three mods
worth 118 pieces are holding 317 other pieces hostage. (Separately, 345 registrations go
through *Jotunn's* `PieceManager::AddPiece` — `OdinArchitect` 209, `RavenwoodVikingHouses` 131,
`Ravenwood_Currency` 3, `EpicLoot` 1, `RossItemDrawers` 1 — and are **not** on this chain;
Jotunn 2.30.1 handles their categories itself.)

#### 2.1.2 What each carrier actually provides, and the named replacement

**`OdinPlus-OdinsUndercroft` 1.3.3** — 2 `BuildPiece` registrations, bundle
`OdinUndercroft.assets.odins_undercroft`, **34 `OU_*` prefabs** read from the `#US` heap:
`OU_Hall`, `OU_Room_A`, `OU_Room_B`, `OU_StoneWall`, `OU_StoneHalfWall`, `OU_StoneArchway`,
`OU_Stone_Floor_1x1/2x1/2x2`, `OU_Stone_Roof_45`, `OU_StoneStair`, `OU_Large_Stone_Pillar`,
`OU_Sarcophagus`, `OU_Skeleton_*`, `OU_Iron_Cage`, `OU_Urn`, … The package describes itself as
*"A `Basement` alternative, a more castle-like private basement."*

> **Recommendation: `OdinPlus-Basements` 2.0.0 — already installed, already fixed, already
> deployed.** It is the same author's mod, the thing OdinsUndercroft describes itself as an
> alternative *to*, and it measures **0 dead refs / 5 correct `m_availablePiecesByCategory`
> refs**. Nothing to install. The stone/castle aesthetic is covered by `OdinsKingdom 1.5.9`
> (278 pieces, 0 dead refs, already installed). If the crypt dressing (sarcophagi, skeletons,
> urns) is specifically wanted, the maintained equivalent is **`blacks7ar-RefinedStonePieces`
> 1.1.1** (2026-09-14, 233 dependants, 129,622 downloads, floor BepInEx 5.4.2350, **104
> `BuildPiece` sites, 0 dead refs, 2 correct `m_availablePiecesByCategory` refs**).
> **Orphans: nothing** — see §3.

**`OdinPlus-OdinsHorsePen` 1.1.0** — 3 `BuildPiece` registrations, bundle
`OdinsHorsePen.assets.odinspen`, **2 prefabs**: `Odins_Pen_Trap_Custom` and
`Odins_Nest_Trap_Custom`. Last release 2026-02-05, seven months stale, 109 dependants.

> **Recommendation: remove it.** Three registrations and two prefabs are the entire payload,
> and they are costing the whole 435-piece availability chain. There is **no drop-in
> replacement** for a taming pen trap that I can name honestly — the closest maintained
> companion is `OdinPlus-OdinHorse` 1.7.0 (2026-09-15, 1,076 dependants), which provides the
> horse itself and its taming, but I did **not** measure a pen/nest trap prefab in it and will
> not claim one. This is the cheapest of the three removals by a wide margin: **cost 3
> registrations, benefit 317.**
> **Orphans: nothing** — see §3.

**`JamesJonesTV-RavenwoodRestorations` 1.0.1** — **113 `BuildPiece` registrations**, two
bundles (`ravenwood_bookshelves`, `ravenwood_restorations`), 59 MB. Self-described:
*"Adds bookcases, storage chests, chalkboards, mailboxes, 20 statues, 20 enterable barns."*
This is the expensive one, and the only carrier where removal is a real content loss.

> **Recommendation, ordered. All four candidates measured at IL level, all four at 0 dead refs:**
>
> | Rank | Candidate | Newest / date | Dependants | Floors | Registrations, measured | `UpdateAvailable` patch | Dead refs |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | **1** | **`blacks7ar-FineWoodFurnitures`** | 1.1.2, 2026-09-13 | 135 | BepInEx 5.4.2350 only | **94** `BuildPiece` | prefix + transpiler, **2 correct `…ByCategory` refs** | **0** |
> | **1=** | **`blacks7ar-DarkWoodFurnitures`** | 1.0.3, 2026-09-13 | 52 | BepInEx 5.4.2350 only | **56** `BuildPiece` | same | **0** |
> | 2 | `MagicMike-CoreWoodExtras` | 2.2.2, **2026-09-18** | 146 | BepInEx 5.4.2350, **Jotunn 2.30.0** ✓ | **308** Jotunn `AddPiece` + 6 `AddItem` | **none — not on the chain at all** | **0** |
> | 3 | `blacks7ar-KoreansPropsAndFurnitures` | 1.0.3, 2026-09-13 | 17 | BepInEx 5.4.2350 only | **216** `BuildPiece` | prefix + transpiler, 2 correct refs | **0** |
> | 4 | `Balrond-balrond_furniture_reborn` | 1.2.9, 2026-09-16 | 77 | none declared | not countable by my instrument — bespoke registration path, **no `BuildPiece` type, no `PieceManager` type** | **none** | **0** |
>
> **Take `blacks7ar-FineWoodFurnitures` + `DarkWoodFurnitures` (150 pieces between them) as the
> direct swap.** Reasons, each measured: the author is already trusted on this fleet — five
> blacks7ar mods are installed (`Wisdom`, `Herbalist`, `Endurance`, `VikingsDoSwim`,
> `WieldEquipmentWhileSwimming`) — and his bundled `PieceManager` is a **rebuilt generation**:
> it still patches `UpdateAvailable`, but with **zero references to `m_availablePieces` in
> either shape and two references to the renamed `m_availablePiecesByCategory`**. That is the
> same correction Basements 2.0.0 made, shipped across his whole catalogue.
>
> **`MagicMike-CoreWoodExtras` 2.2.2 is the structurally safest of all of them** and worth a
> line of its own: it registers 308 pieces **through Jotunn**, carries **no `BuildPiece` type
> and no `UpdateAvailable` patch at all**, and so **cannot ever join the fragile chain**. It is
> also the single most recently updated candidate (2026-09-18). If the goal is to reduce the
> number of mods that can break the build menu, this is the one that reduces it.
>
> **`Balrond-balrond_furniture_reborn` 1.2.9** is a legitimate fourth option — `balrond_constructions
> 1.4.5` is already installed and trusted — but I am declaring an instrument limit: my
> registration counter finds **no** `BuildPiece`/`PieceManager` type in it, so its advertised
> *"over 140 furniture and clutter build pieces"* is the author's number, not mine. It is also
> off the `UpdateAvailable` chain entirely, which is a point in its favour.
>
> **Orphans: nothing** — see §3.

#### 2.1.3 Is removing the three enough, and is the local patch plugin still needed?

**Removing all three heals the chain, and nothing else on the tree needs to change** — that is
now measured, not argued: the full 123-DLL sweep finds exactly three dead carriers, so after
their removal the count is zero and `UpdateAvailable` runs to completion for all 435
registrations.

`ServerUpdate` confirms the patch plugin is still blocking itself this boot:
the `Valheim10Compatibility` preloader still logs
`BLOCKED PieceTable.m_availablePieces (List<List<Piece>>, alias of m_availablePiecesByCategory)`.
The last survey's analysis of *why* a by-name alias cannot work (two fields share the name,
`Type.GetField`/`Traverse.Field` throw `AmbiguousMatchException`) is unchanged and I have
nothing to add to it. The workable mechanism remains a preloader **`MemberRef` operand
retarget** — 5 tokens per carrier, `m_availablePieces` → `m_availablePiecesByCategory`, identical
`List<List<Piece>>` signature, no reflection involved. My `/tmp/ms2/deadref2.py` already prints
the exact tokens (`0x0a000388` Undercroft, `0x0a0004a6` HorsePen, `0x0a000235` Restorations) and
their five IL offsets, so that patch is a 15-token edit if the operator prefers keeping the
content over removing it.

**The two options are genuinely different trades, and the operator should pick:**

| | Remove the three | IL-retarget the three |
| --- | --- | --- |
| Pieces lost | 118 (2 + 3 + 113) | 0 |
| Pieces recovered | 317 | 435 |
| Placed content orphaned | **0** (§3) | 0 |
| New code to maintain | none | a preloader, forever, per carrier |
| Upstream dependency | none | none |

### 2.2 Abandoned and deprecated packages, and whether a successor exists

**First, the flag is not the signal.** MEASURED today, unchanged from the last survey:
of Azumatt's **148** packages, **145 carry `is_deprecated=true`, including all 30 updated since
2026-09-01**. It is a blanket author-level retirement flag over a catalogue still shipping
releases. By contrast **blacks7ar flags 26 of 100** — selective, so a blacks7ar deprecation is
real information.

Ten installed packages carry the flag. Seven are Azumatt's blanket flag and are at newest
(`AAA_Crafting` 2.1.8, `AzuCraftyBoxes` 1.8.19, `AzuWearNTearPatches` 1.0.9,
`TrueInstantLootDrop` 1.0.4, `AAABuildMenu` 1.0.4, plus the retired fork
`fedorovdgap-AzuCraftyBoxes` which the fleet has already left). The three that mean something:

| Package | Last release | Flag | Verdict and successor |
| --- | --- | --- | --- |
| `Azumatt-PerfectPlacement` 1.2.2 | **2025-03-06** | deprecated | 18 months stale, pre-1.0, on server + client + 4 manifests. **Successors, both 1.0-current: `Searica-Extra_Snap_Points_Made_Easy` 2.1.0 (2026-09-15, 76 dependants) for snap points, and `Neobotics-UnRemove` 1.0.0 (2026-09-11) for overlapping placement.** Neither is a strict superset; evaluate, do not swap blind. |
| `blacks7ar-Mask` 1.0.3 | **2025-03-11** | deprecated — and **selectively**, by an author who flags only 26 of 100 | **This one is genuinely retired.** No successor in the author's catalogue. Cosmetic-only. Recommend **remove** unless someone is wearing it. |
| `Azumatt-FastLink` 1.4.8 | 2025-09-18 | deprecated (blanket) | client-only, 886 dependants, still the standard. Leave. |

**Genuinely stale-but-unflagged, with a successor the fleet can actually use** — this is the
part the last survey did not do:

| Installed | Last release | Successor, measured | Why it is better |
| --- | --- | --- | --- |
| `Cytraen-BiggerChests` 1.1.0 | **2025-04-08**, 8 dependants | **`ArgusMagnus-ServersideQoL_ContainerSizes` 2.0.11** (2026-09-16) | *"Make all container inventory sizes configurable and optionally let them grow infinitely if they contain only one item type."* Server-side only, so **no client package at all** — and the fleet already runs the `ServersideQoL` core it needs. |
| `Disboard-Ballista_Infinite_Ammo` 1.0.1 | **2025-05-07**, 1 dependant | **`ArgusMagnus-ServersideQoL_SmartDefense` 2.0.11** (2026-09-16) | *"Ballista don't target players or tames and reload from nearby containers."* Strict superset: infinite ammo becomes automatic container reload, plus friendly-fire prevention. |
| `SurplusTradingCo-SullysAutoPinner` 1.4.0 | **2025-06-21**, 17 dependants | **`ArgusMagnus-ServersideQoL_AutoMapTables` 2.0.11** and/or **`_SuperWishbone` 2.0.11** | *"Automatically update map tables with icons for portals, ships, ore deposits and more"* / *"Supercharges the wishbone so it can find dungeons, vegvisirs and configurably more."* Server-side, shared across all players instead of per-client. |
| `ChangosOF-DropCleaner` 1.1.0 | **2025-07-21**, **0 dependants**, declares Jotunn **2.12.0** | `ServersideQoL_LetItFloat` 2.0.11 covers the sinking-item half | Zero dependants and a Jotunn floor eighteen minor versions behind is the profile of an abandoned mod. Ulfsland-only. **Candidate for removal.** |
| `coemt-No_New_Recipe_Notifications` 1.0.2 | 2025-08-10, 16 dependants | `sighsorry-VeiledRecipes` 1.1.5 (2026-09-12) is adjacent, not equivalent | Tiny, no declared deps, nothing to break. Leave unless it misbehaves. |

### 2.3 `ServersideQoL` — the split modules, named

2.0.0 *"split up features into separate mods"*. The fleet installs **core `ServersideQoL`
2.0.13** plus **`ServersideQoL_AutoStore` 2.0.11** and nothing else — measured: the only two
server dirs matching `ServersideQoL*`, and the only two SQoL `.cfg` files in
`config_merged/bepinex` (`ArgusMagnus.ServersideQoL.cfg`,
`ArgusMagnus.ServersideQoL.AutoStore.cfg`). **The missing features are missing *packages*, not
unset options.**

**The two the brief asks about, by exact package name:**

| Want | Package | Version / date | Author's own description |
| --- | --- | --- | --- |
| **feed-from-containers** | **`ArgusMagnus-ServersideQoL_AutoProcess`** | 2.0.11, 2026-09-16 | *"Automatically feeds processing stations such as smelters, kilns, windmills, shield generators, etc. from nearby containers"* |
| **container sizes** | **`ArgusMagnus-ServersideQoL_ContainerSizes`** | 2.0.11, 2026-09-16 | *"Make all container inventory sizes configurable and optionally let the grow infinitely if they contain only one item type"* |

**Every module declares exactly one dependency: `ArgusMagnus-ServersideQoL-2.0.11`.** Installed
core is **2.0.13** — the floor is met for the entire family, with nothing else to install. The
whole family is **server-only**: the last survey settled that definitively at IL level (the
literal `Mod should only be installed on the host` at `#US` offset 9730, guarded by
`ZNet::IsServer`), so **none of these needs a client deploy and none can desync a client.**

**The rest of the family, ranked by what this fleet would actually get.** All 2.0.11,
2026-09-16, all floor `ServersideQoL-2.0.11` ✓:

| Module | Downloads | What it gives *this* fleet |
| --- | --- | --- |
| **`_AutoProcess`** | 2,779 | the feed-from-containers ask |
| **`_ContainerSizes`** | 3,288 | the container-sizes ask; **also retires `Cytraen-BiggerChests` (2025-04-08)** |
| **`_ContainerSigns`** | 3,144 | *"Adds signs to chests and barrels… Optionally show automated content list and configure the container for other SQoL mods."* A 1,004-piece harbour city with **46 placed chest rows** (29 `piece_chest_blackmetal`, 15 `piece_chest_wood`, 1 `piece_chest`, 1 `piece_chest_private`) is exactly the case for labelled storage |
| **`_TameAssist`** | 3,026 | *"Allow all tames to follow you, even through portals and into dungeons. Show taming and growing up/hatching…"* — and it makes `OdinsHorsePen`'s removal cheaper |
| **`_LetItFloat`** | 3,116 | *"Lets all sinking items float by putting them into cargo crates when they sink too deep."* This world has a harbour city, a lighthouse ring, and `CargoShip` / `VikingShip` / `LittleBoat` all appearing in the ledger's spawn records |
| **`_JustSleep`** | 2,251 | vote-to-skip-night with one player in bed |
| **`_MultiplayerTweaks`** | 2,177 | *"Networking options which help reduce the experienced effects of lag. Force map player pins to always be on."* |
| **`_PrefabConfigurator`** | 2,582 | *"Increase the range of workstations. Make fireplaces/light sources toggleable or have infinite fuel."* Workstation range on a 1,004-piece build is a real quality-of-life change |
| `_SmartDefense` | 1,649 | retires `Disboard-Ballista_Infinite_Ammo` |
| `_AutoMapTables` | 1,679 | retires half of `SullysAutoPinner` |
| `_AdminOptions` | 924 | admin build/landscaping toggles — overlaps the `ulfsland-admin` seat's existing tooling; evaluate against `Infinity_Hammer` before taking |
| `_PortalProgression` | 1,620 | overlaps `MidnightMods-AdjustablePortals` — **do not take both** |
| `_Backpack`, `_Player`, `_Skills`, `_CreatureLevelUp`, `_DropControl`, `_WorldOptions`, `_Signs`, `_AutoDoors`, `_AutoPortalHub`, `_SuperWishbone` | 556–1,530 | each overlaps something already installed (`AdventureBackpacks`, `ImpactfulSkills`, `CreatureLevelAndLootControl`, `Cross_Server_Portals`). **Collision risk, not free capability.** |

**Avoid entirely:** every `*_BETA` package (`ServersideQoL_BETA` 1.99.8 and its 14 satellites)
is flagged `is_deprecated=true` and pinned to the dead `ServersideQoL_BETA-1.99.x` line.

**Recommendation: take `_AutoProcess`, `_ContainerSizes`, `_ContainerSigns`, `_TameAssist`,
`_LetItFloat` — five packages, one deploy, zero client impact, zero world-data writes.** Then
retire `Cytraen-BiggerChests` and `Disboard-Ballista_Infinite_Ammo` in the same window.

One implementation note carried forward unchanged: both installed SQoL packages sit in
`packages` with `scope: "shared"`, and `manual_server_packages` is **empty in all seven
profiles**. Adding five more server-only packages as `shared` would push five more useless
plugin trees onto every client. **Establishing `manual_server_packages` is a prerequisite for
this recommendation, not an optional tidy-up.**

### 2.4 `PlanBuild` — verdict: **do not install it. Use the tooling you already have.**

**Status, measured.** `MathiasDecrock-PlanBuild` **0.19.0, published 2026-09-17**, 805
dependants, 600,155 downloads, floors `denikson-BepInExPack_Valheim-5.4.2350` and
`ValheimModding-Jotunn-2.30.0` (installed 2.30.1 ✓). It is alive and 1.0-ready — quoted:

> # Version 0.18.5
> * Compatible with Valheim 1.0.7 (thx shelledghost)
> * Fixed crash on player spawn (camera snapping to world origin and infinite player spawn loop) caused by `Hoverable` gaining a new `GetHoverOffset` member and **`PieceTable.m_availablePieces` changing shape in Valheim 1.0.7**
>
> # Version 0.19.0
> * Removed the HookGenPatcher dependency; all hooks now use Harmony
> * Fixed clipboard blueprints staying in the Blueprint Rune after leaving a world

IL-measured: **0 dead refs, 6 Jotunn `PieceManager::AddPiece` sites, 3 `ItemManager::AddItem`
sites, no `BuildPiece` type, no `UpdateAvailable` patch.** Technically it is one of the
cleanest packages in this document. The 1.0 fork `ShelledGhost-PlanBuild` 0.18.8 is now
**flagged deprecated**, upstream having resumed — so there is no fork question left.

**Blockers, in order of weight.**

1. **The stale config is PlanBuild's own, and installing would adopt it.** The orphaned
   `marcopogo.PlanBuild.cfg` (**29,006 bytes**, mtime 2026-09-17 22:41) is named for a BepInEx
   GUID, and **`marcopogo.PlanBuild` is present verbatim in PlanBuild 0.19.0's `#US` heap** —
   this is that mod's GUID, not a third-party namespace. (Correcting the last survey's orphan
   list, which recorded it as a package identifier `marcopogo-PlanBuild`; no such Thunderstore
   namespace exists.) A fresh install would read a 29 KB config written for a pre-1.0 build
   rather than generating defaults. Delete or archive it **before** any install.
2. **Duplicate capability the fleet already owns and has documented.** `tools/jumpstart/blueprints/README.md`
   records, MEASURED from `InfinityHammer.dll` 1.83.0: `HammerBlueprintCommand::LoadFiles`
   enumerates `*.blueprint` **and** `*.vbuild` recursively from `Paths.ConfigPath/<folder>`
   where `<folder>` defaults to the literal string **`PlanBuild`**, and
   `GetPlanBuild`/`GetPlanBuildObject` parse the PlanBuild header keys `#Name:`, `#Creator:`,
   `#Category:`, `#Center:`, `#Coordinates:`, `#Rotation:`, `#SnapPoints`, `#Pieces`.
   **Infinity Hammer 1.84.0 is installed and at newest.** The project already reads and writes
   the format, already places from it two ways (`hammer_blueprint`/`hammer_restore` on the admin
   seat, `to_rcon_plan.py` headless), and 112 corpus files already parse. PlanBuild adds a GUI
   and a Blueprint Rune, not a capability.
3. **Shared-directory collision, real but not a grid collision.** PlanBuild's *Save directory*
   defaults to `BepInEx/config/PlanBuild/blueprints` and its *Search directory* is
   *"Base directory to scan (recursively) for blueprints and vbuild files"* — the same tree the
   project materialises presets into (`<client>/BepInEx/config/PlanBuild/<preset>/`) and the
   same tree Infinity Hammer reads with `SearchOption.AllDirectories`. **Both mods would
   enumerate the same files**, and the operator would see every preset twice, in two different
   UIs, with two different placement semantics. Reassuringly, it is **not** a hammer-grid
   overflow: PlanBuild registers into its own **`_BlueprintPieceTable`**, measured in its `#US`
   heap, not `_HammerPieceTable`.
4. **It creates mod-provided placed ZDOs — the only item in this survey that would *add* orphan
   risk to this world.** Measured `#US` prefabs include `piece_plan_totem`, `piece_plan_delete`,
   `piece_bpcapture`, `piece_bpterrainmod`, `piece_bpsnappoint` and a `_planned` suffix family.
   Every plan ghost and Plan Totem placed becomes a ZDO for a prefab **only PlanBuild provides**.
   Today this world has **zero** such dependencies (§3). Installing PlanBuild and later removing
   it would orphan every one of them.

> **Verdict: not worth installing on Ulfsland.** It duplicates tooling the project has already
> measured, documented and built a 112-file corpus around; it would adopt a 29 KB stale config;
> and it is the one change in this survey that creates new mod-provided placed content on a world
> that currently has almost none. **Do the cheap half instead: delete the orphaned
> `marcopogo.PlanBuild.cfg`.** If the operator specifically wants the in-game Blueprint Rune GUI
> for hand-building, revisit it — but do it on a scratch world first, and delete that config.

---

## 3. Step 3 — the hazards: what would orphan placed content

**Method.** A mod can only orphan placed content if it is the thing that puts the prefab in
`ZNetScene` — which requires an asset bundle. So: (a) census what is actually placed, (b)
determine which plugins *provide* prefabs rather than merely naming them, (c) diff the `#US`
heaps of installed vs candidate for any provider. Never `strings`.

### 3.1 What this world is standing on — the census

*Blueprint-placed content*, from the 18 blueprints referenced by
`tools/jumpstart/settlements/placements/*.yaml`, parsed from their `#Pieces` sections:
**12,583 piece rows, 127 distinct prefabs.**

*RCON-placed content*, from `tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl`
(**2,905 records**, ops: `objects_clear` 854, `observe` 528, `zones_generate` 290,
`spawn_plan` 279, `retire` 274, `spawn` 228, `save` 169, `terrain_write` 107, `portal` 64):
16 distinct prefabs across `spawn`/`spawn_plan` params.

### 3.2 The finding: the orphan surface is ten prefabs from one mod

Sweeping all 118 readable plugin DLLs' `#US` heaps against the combined 137-prefab placed set:

* **87 placed prefabs are named by no installed plugin at all** — pure vanilla
  (`wood_wall_log_4x0.5` ×674, `wood_floor_1x1` ×666, `Piece_grausten_*`, `blackmarble_*`,
  `darkwood_*`, `ashwood_*`, `iron_floor_1x1_v2` ×352, …). **No mod change can touch them.**
* **50 are named by some plugin**, but naming is not providing. Cross-checked against the
  asset-bundle discriminator (`UnityEngine.AssetBundle` `MemberRef` usage + embedded resource
  streams), **29 installed plugins carry an asset bundle and none of them provides these**:
  `RossItemDrawers` names `blackmarble_1x1`/`wood_pole`/`wood_floor` and **has no bundle at
  all**; `ZenWorldSettings` names `piece_dvergr_metal_wall_2x2` ×271 and **has no bundle**;
  `Infinity_Hammer` names `woodwall`; `More_World_Locations_AIO` names `stone_pillar` inside its
  location compositions. These are config keys, material lists and station names.
* **Exactly 10 placed prefabs are mod-provided**, all from one bundle:

  | Prefab | Provider | Bundle |
  | --- | --- | --- |
  | `fb_fortress_wall`, `fb_fortress_wall_tower`, `fb_fortress_wall_gateway`, `fb_fortress_wall_corner`, `fb_fortress_wall_stair_left`, `fb_fortress_wall_stair_right`, `fb_fortress_portcullis`, `fb_bridge_01`, `fb_bridge_02`, `fb_bridge_stairs` | **`RavenwoodVikingHouses` 7.7.8** | **`villagestuff`** |

  Corroborated at runtime by the last survey's ledger probe (seq 2118–2123): `fb_bridge_01` and
  `fb_bridge_02` resolve in `ZNetScene` and place; `mm_large_wall` returns
  `Error: Entity id mm_large_wall not recognized` and places nothing.

> **So the entire orphan exposure of this world, across 15 settlements, a 1,004-piece harbour
> city, a 15-arch portal hall, 11.45 km of road and 2,905 ledger records, is ten `fb_*` prefabs
> from one asset bundle.** That is the single most useful number in this survey, and it reframes
> almost every recommendation below from "risky" to "cheap".

### 3.3 Per-change orphan verdict, every provider checked at the `#US` heap

| Change | `#US` literal delta | Orphan risk |
| --- | --- | --- |
| **`RavenwoodVikingHouses 7.7.8` → `RavenwoodPieces 2.0.5`** | identifier sets **217 = 217**, `fb_` **29 = 29**, `mm_` **78 = 78**, bundles identical (`ravenwoodpieces`, `ravenwoodpiecesnew`, **`villagestuff`**). **All ten placed `fb_*` present in both.** Removed: **none**. Added: **none**. | **NONE.** The swap is content-identical plus 102 re-enabled pieces. |
| `ComfyMods-PotteryBarn 1.21.0 → 1.22.0` | 184 = 184, **0 removed, 0 added** | **NONE** — pure recompile |
| `Ross-RossItemDrawers 1.0.8 → 1.0.11` | 122 → 123, **0 removed**, 1 added (`piece`) | **NONE** |
| `OdinPlus-OdinsFoodBarrels 1.2.6 → 1.2.8` | 293 → 303, **1 removed (the version string `1.2.6`)**, 11 added incl. 4 new prefabs | **NONE** |
| Remove `OdinsUndercroft` (34 `OU_*` prefabs) | — | **NONE placed.** No `OU_*` prefab appears in any blueprint or any ledger record |
| Remove `OdinsHorsePen` (`Odins_Pen_Trap_Custom`, `Odins_Nest_Trap_Custom`) | — | **NONE placed** |
| Remove `RavenwoodRestorations` (113 registrations) | — | **NONE placed** |
| Remove `Cytraen-BiggerChests` | provides no bundle | **NONE** — it resizes vanilla containers; `piece_chest*` are vanilla |
| Remove `Disboard-Ballista_Infinite_Ammo` | provides no bundle | **NONE** — vanilla ballista |
| Remove `blacks7ar-Mask` | bundle `Mask.assets.maskbundle` — **items, not pieces**; no placed prefab | **NONE for placed content**; a worn mask item would be lost |
| **Install `PlanBuild`** | adds `piece_plan_totem`, `piece_plan_delete`, `piece_bp*`, `_planned` family | **CREATES new orphan exposure** — see §2.4 |
| Add any `ServersideQoL_*` module | server-only, no bundles | **NONE** |

### 3.4 The one hazard I cannot close from here — declared

My placed-content census covers **blueprint placements and the project ledger**. It does **not**
cover pieces the operator or players built by hand in-game with the hammer, which leave no
record in either. If someone has hand-built with `OU_*`, `Odins_*_Trap_Custom` or
`RavenwoodRestorations`' 113 pieces, removing those mods orphans those ZDOs.

**This is answerable, and here is the exact check**, to be run from the `ulfsland-admin` seat or
by RCON before any removal:

```
objects_count OU_Hall
objects_count OU_Room_A
objects_count Odins_Pen_Trap_Custom
objects_count Odins_Nest_Trap_Custom
```

A non-zero count means that removal orphans real content and should become a
**DANGEROUS** row instead of a SERVER-SIDE one. I did not run it: this survey is read-only, the
world is `ServerUpdate`'s tonight, and an RCON call is not a read.

For `RavenwoodRestorations` the equivalent check needs its 113 prefab names; they are
extractable from `RavenwoodRestorations.dll`'s `#US` heap with
`python3 /tmp/ms2/prefabs.py <dll>` and I have not enumerated them here because the removal is
ranked last anyway.

---

## 4. Step 4 — the prioritised recommendation

### The binding constraint

**The binding constraint is not any mod version. It is the client sync in §0.4:** the operator's
client is on edition `ulfsland-vr-flat-admin-1.0.20` carrying `Zen_ModLib 1.14.3`, while the
server runs 1.14.7. The portal is already serving a published, hash-verified 1.0.21 for that
scope (§0.4), so this is purely **the client not having synced since 00:50:54**. Until it does,
the join still fails and nothing in the rest of this list matters to the operator's actual
experience. Everything below is non-binding beside it.

The next non-binding constraints, in order: the **three dead-field carriers** holding 317
innocent piece registrations hostage; the **36-package client drift** on the two non-admin
profiles; and the **`scope: shared` mislabel** on two client-only mods that a future
`deploy --apply` could act on.

### 4.1 CLIENT-ONLY — free, no world risk, no stopped server

| # | Action | Why |
| --- | --- | --- |
| **1** | **Have the operator re-run the client sync from `https://valheim.neuralyze.com`, so the admin seat moves from edition 1.0.20 to 1.0.21 with `Zen_ModLib 1.14.7`.** Acceptance: `state.json` must read `release_id = ulfsland-vr-flat-admin-1.0.21` **and** `Zen_ModLib 1.14.7`. Read those two fields, never the sync's exit code — if `release_id` still says 1.0.20, the sync did not take and nothing else is worth debugging | the binding constraint; nothing else unblocks the join. Server tree, manifests and portal are all already correct |
| **2** | Update `ZenDragon-Zen_ModLib` to **1.14.7** in the `admin`, `flat` and `vr` shared profile manifests | measured still at 1.14.3; any edition built from them ships the broken gate. Explicitly outside `ServerUpdate`'s scope tonight |
| **3** | One catch-up deploy for `ulfsland-non-vr` and `ulfsland-vr-flat` — **36 packages each** | includes `Jotunn` 2.30.0→2.30.1, `Zen_ModLib` **1.14.0**→1.14.7, `Basements` 1.4.1→2.0.0, `Valheim10Compatibility` 1.4.0→1.4.1. Those two profiles currently cannot join at all |
| **4** | `Azumatt-AzuExtendedPlayerInventory` **2.4.8 → 2.4.14** | *"Restore the full inventory grid layout after death"*, *"Fix loadouts, Fix Favoriting"*, *"AzuAutoStore compat fixes for favoriting"*. Check the pinning path for a lexical version sort while you are there |
| **5** | `95Shade-CarryWeightSkill` **1.0.1 → 1.0.2**; if it still fails, swap to `Searica-SkilledCarryWeight 1.5.0` | never on a server tree, so zero world risk. Reverses the old "slate for removal" |
| **6** | Fix `scope: shared` → client-only on `Azumatt-AzuExtendedPlayerInventory` and `Azumatt-AAABuildMenu`; **delete the dangling `shudnal-HarpoonExtended` pin** | prevents a future `deploy --apply` from pushing client mods onto the server |

### 4.2 SERVER-SIDE — needs the world stopped, no world-data risk

Take these in one window. Every one is measured to remove **zero** prefab literals.

| # | Action | Evidence |
| --- | --- | --- |
| **7** | `OdinPlus-OdinsFoodBarrels` **1.2.6 → 1.2.8** | *"Fixed piece catagory with build hammer"* + 4 new prefabs; `#US` delta purely additive. A second independent fix for the 1.0 hammer-category defect |
| **8** | `ComfyMods-PotteryBarn` **1.21.0 → 1.22.0** | *"Fixed for the v1.0.14 patch"*; `#US` 184 = 184, zero delta. The cheapest row here |
| **9** | `Ross-RossItemDrawers` **1.0.8 → 1.0.11** | *"Drawers no longer feel sluggish on a dedicated server"*; `#US` zero removed |
| **10** | `MidnightMods-ValheimArmory` **1.31.0 → 1.32.0** | `LoadPrefabsOnServer` — directly benefits `EpicLoot`, `TradersExtended`, `ServersideQoL` |
| **11** | `tulivu-FearMe` **1.0.0 → 1.0.1** | *"No longer requires Jotunn"*, Deep North content. **Back up `tulivu.valheimmods.fearme.cfg` — `Rework config` means regeneration** |
| **12** | `shudnal-HipLantern` 1.1.8→1.1.9, `shudnal-LongshipUpgrades` 1.0.20→1.0.21, `shudnal-TradersExtended` 2.0.2→2.0.3 | all three are audio/input only, with the author disclaiming shared-effect changes |
| **13** | `Vapok-AdventureBackpacks` **2.0.7 → 2.0.9** | 3× `TypeLoadException` fixed on a 1.0.15 server, **and telemetry flips from auto-opt-in to opt-in** |
| **14** | **Install 5 `ServersideQoL` modules: `_AutoProcess`, `_ContainerSizes`, `_ContainerSigns`, `_TameAssist`, `_LetItFloat`** (all 2.0.11) | answers the feed-from-containers and container-size asks with named packages. Floor `ServersideQoL-2.0.11`, installed core **2.0.13** ✓. **Prerequisite: establish `manual_server_packages`**, or these five land on every client |
| **15** | **Remove `OdinPlus-OdinsHorsePen`** | 3 registrations, 2 prefabs, **0 placed**, stale since 2026-02-05, and it is one third of what is killing the build menu. Cheapest carrier removal by far. **Run the §3.4 `objects_count` check first** |
| **16** | **Remove `OdinPlus-OdinsUndercroft`**, keeping `Basements 2.0.0` + `OdinsKingdom 1.5.9` for the same aesthetic | 2 registrations, **0 placed**. **Run the §3.4 check first** |
| **17** | Delete the orphaned **`marcopogo.PlanBuild.cfg`** (29,006 bytes) | PlanBuild is not installed; measured to be its GUID; leaving it means a future install silently adopts a pre-1.0 config |
| **18** | Retire `Cytraen-BiggerChests` (2025-04-08) and `Disboard-Ballista_Infinite_Ammo` (2025-05-07) once #14 lands | both superseded by SQoL modules; neither provides a bundle, so neither orphans anything |
| **19** | Remove `blacks7ar-Mask` 1.0.3 | selectively deprecated by an author who flags only 26 of 100; 18 months stale; cosmetic only |
| **20** | Evaluate `Azumatt-PerfectPlacement` 1.2.2 (2025-03-06) against `Searica-Extra_Snap_Points_Made_Easy` 2.1.0 | 18-month-stale pre-1.0 mod on server + client + 4 manifests. Evaluate, do not swap blind — not a strict superset |
| **21** | Consider removing `ChangosOF-DropCleaner` | **0 dependants**, Jotunn floor **2.12.0**, Ulfsland-only, last release 2025-07-21 |

### 4.3 DANGEROUS — touches world data, terrain or placement. One at a time, backup first.

Ordered by *net risk reduction*, not by risk. The first row is dangerous to **hold**.

| # | Action | The line that makes it dangerous — and why to take it anyway |
| --- | --- | --- |
| **22** | **`RandyKnapp-EpicLoot` 0.14.7 → 0.14.10** | *"Fix for magic effect values being permanently reset to 1 when an item was reloaded… This hit Ancient items on servers whose magiceffects.json predates the Ancient rarity"*, *"Fixes for the bounty ledger which could reset when the server was restarted"*. **The installed version is the one that destroys rolled enchantments. Holding is the riskier choice.** |
| **23** | **`Skarif-BuildOnShip` 3.0.112 → 3.2.1** | *"Fix 17.09.26 patch & fix ZDOzone sync portal on ship (build)"*. The fleet is two game patches behind on a mod that owns ZDO-to-zone mapping for pieces on moving ships. 4 dependants upstream, so test it |
| **24** | **`GoldenJude-Judes_Equipment` 2.3.0 → 3.0.0** | *"Custom configurations currently not supported"* — **measured cost 463 bytes of config**. Resolves a live skew: installed `Blacksmiths_tools` is already **3.0.0** while `Judes_Equipment 2.3.0` declares **2.0.2** |
| **25** | **`MidnightMods-ImpactfulSkills` 0.16.1 → 0.17.1** | *"An existing field that was already planted too tightly will now show red cells"* — behaviour change on already-planted ground. Its Jotunn 2.30.1 coupling is now **satisfied**, so the old blocker is gone |
| **26** | **`shudnal-ProtectiveWards` 2.0.10 → 2.0.11** | *"Simplified per-ward settings: new wards copy their initial values from the global config and then keep explicit independent values without `Use default` fields"* — rewrites the per-ward persisted setting model. No migration is claimed. Take last, after a fresh world backup |
| **27** | **`RavenwoodVikingHouses 7.7.8` → `RavenwoodPieces 2.0.5`** — the swap, **now measurably safe, and its prerequisite is met where it matters** | Not dangerous by content: `#US` heaps identical (217 = 217, 29 `fb_`, 78 `mm_`, same three bundles, all ten placed prefabs present in both), `AddRWPieces` **1 byte `ret` → 65 bytes live**, `AddRWEffects` likewise. It is DANGEROUS only for **grid arithmetic**: 131 pieces in one `Ravenwood-Pieces` category on a 90-cell hammer grid, so a build-menu mod is the author's stated prerequisite. **Correcting the 2026-09-18 survey, which measured no build-menu mod anywhere: `Azumatt-AAABuildMenu 1.0.4` is now installed on `Ulfsland--ulfsland-vr-flat-admin--flat`** — the admin seat, which is the only seat that builds (`hammer_*` needs an equipped hammer on a live player). Its changelog: *"Custom Jotunn/PieceManager categories get their own tag in the 1.0 menu"* and *"A modded piece with a broken recipe no longer breaks the build menu."* Still absent from the two non-admin client profiles and from every server tree. **So: safe to swap for the admin builder now; do #3 first if non-admin players need to see the 131 pieces too.** |

### 4.4 Explicitly hold, with the reason

| Hold | Reason |
| --- | --- |
| `Smoothbrain-ServerCharacters` | installed **1.4.17.1** is a local build **ahead** of Thunderstore's 1.4.16. "Update to newest" **downgrades it and drops the portal patch.** Exclude by name from every bulk pass |
| `MathiasDecrock-PlanBuild` 0.19.0 | §2.4 — duplicates owned tooling, adopts a 29 KB stale config, and is the only change that would *create* mod-provided placed ZDOs on a world that currently has ten |
| Remove `JamesJonesTV-RavenwoodRestorations` | **113** registrations is a real content loss. Do #15 and #16 first (5 registrations, 0 placed), then decide whether the remaining 113 are worth the 317 still held hostage, or whether the IL retarget in §2.1.3 is the better trade |
| `Azumatt-FastLink`, `Azumatt-AAA_Crafting`, `AzuWearNTearPatches`, `TrueInstantLootDrop`, `AzuCraftyBoxes` | the `is_deprecated` flag on all of these is Azumatt's blanket catalogue retirement — **145 of 148 flagged, including all 30 updated since 2026-09-01**. All are at newest. Not abandonment |
| Every `ServersideQoL_*_BETA` | deprecated, pinned to the dead `ServersideQoL_BETA-1.99.x` line |
| `warpalicious-More_World_Locations_AIO` `locations_add` | 5.1.1 is deployed and the hold is closed, but the five new Swamp locations reach an existing world **only** via an explicit `locations_add`. Do not run it unless they are wanted |

---

## 5. Limits, and what is inferred rather than measured

**Scope: no shrink.** All four assigned steps were completed. Extra work beyond the brief,
recorded so it is auditable: the 435-registration / 11-patcher blast-radius quantification, the
asset-bundle provider discriminator, the full placed-prefab census from blueprints *and* the
2,905-record ledger, the artefact-level `cmp` verification of the three carriers and of the
deployed Zen_ModLib, and the client-edition sync gap in §0.4.

**Labelled `[INFERENCE]`, not measured:**

* That `95Shade-CarryWeightSkill 1.0.2` actually fixes the `PatchAll` failure — taken from its
  description, not booted. The *measured* part is that a fix exists.
* That the candidate replacement mods in §2.1.2 render and behave well. I measured their
  registration mechanism, their field-shape correctness and their dead-ref count. **I did not
  boot any of them.**
* `Balrond-balrond_furniture_reborn`'s *"over 140 furniture and clutter build pieces"* is the
  author's claim; my instrument cannot count its bespoke registration path, and I have said so
  rather than quoting a number I did not derive.
* That no player has hand-built with `OU_*`, `Odins_*_Trap_Custom` or `RavenwoodRestorations`
  pieces. §3.4 gives the exact `objects_count` commands that settle it; **run them before any
  removal.**

**Five instrument errors found and corrected across this survey and `ServerUpdate`'s parallel
ticket, recorded rather than edited away because each produced a wrong verdict before it was
caught. Three are mine, two are his. All five are the same family as this fleet's eighteen
config-file-is-not-an-installed-mod findings and its 95 phantom piece removals: an artefact —
or a narrative, or a corpus — accepted as evidence for a question it cannot answer.**

**How every one of them was found, which matters more than the list — `ServerUpdate`'s
observation and the single most portable thing in this document.** All five were found the same
way: **two instruments disagreeing, and nobody picking a winner.** The `0x6` cracked because the
exit code and `content_log.txt` told different stories. `Ravenwood_Currency` surfaced because
his reader and mine returned different sets. The 119 surfaced because his count and my count
differed by two. Each time the tempting move was to reconcile **by authority** — trust the newer
tool, the bigger number, the one that ran without erroring — and each time the union was the
answer and **the disagreement itself was the finding**.

> **Meta-rule: a disagreement between instruments is not noise to be resolved, it is the only
> free signal you get that one of them is lying. Agreement tells you nothing unless the
> instruments are genuinely independent.**

Which is exactly why `119 + 4 = 123` across three separate paths (this survey's 123-DLL sweep,
`ServerUpdate`'s `mod_set_dll_count` anchor, and the reconciled enumeration) carries weight,
**and why his 117 agreeing with itself twice carried none.** Two instruments sharing a blind
spot produce agreement that looks identical to corroboration and is worth nothing. Check
independence before you take comfort from a matching number.

**(1) A client's cache read as a server's publish record.** I read `/mnt/vps-sync` as a
*publish* share and concluded from "no `ZenDragon-Zen_ModLib-1.14.7.zip` in
`/mnt/vps-sync/packages/`" that the republished edition **had not been served**. False defect.
`ServerUpdate` corrected it: the mount is the **client's own cache mirrored back**, the
`portal` field in the same `state.json` shows the client pulls from
`https://valheim.neuralyze.com`, and the client rewrites that file itself — so the listing is a
*record of the last sync*, never evidence about the server. He then verified the serving side
directly (four `ulfsland-*` 1.0.21 rows `published`, 1.0.20 rows `archived`, artefacts
re-hashing to their DB `sha256`) and it is intact. The observable — the operator's client is on
1.0.20 / 1.14.3 — was right; the mechanism I attached to it was wrong.
> **Rule: a directory whose contents a *client* writes cannot be read as evidence about what a
> *server* published. Establish which side owns the bytes before inferring a gap from their
> absence.**

**(2) A server-side measurement used to close a client-side question.** I wrote that the six
Zen mods "are loading" on the strength of the server registering all seven components. The six
were failing to load on the **client**. `ServerUpdate` caught it.
> **Rule: name the endpoint a measurement was taken at before letting it answer a question
> about the other endpoint.**

**(3) A vacuous zero counted as a pass.** I recorded "zero `Missing mod on client` across 32
minutes" in the MEASURED column with only a parenthetical caveat. `ServerUpdate` went and
counted: `Connections 0` at both the 01:35:43 and 01:45:43 heartbeats, no `RPC_PeerInfo`, no
`Got connection`, no disconnect since the 01:22 restart. **No client attempted a join in that
window**, so the line had no opportunity to fire and its absence carries no information. The
row is now struck through in §0.3 and the measured set is three, not four.
> **Rule: before counting an absent error as evidence, prove the code path had the chance to
> run. A zero from an untriggered path is indistinguishable from a pass and reads better than
> one.**

**(4) An error code read as a diagnosis — `ServerUpdate`'s, recorded here because the rule is
general and this is where the family is written down; the specifics belong to his ticket.**
steamcmd's `state is 0x6` names a *state*, not a cause. The cause was an `Access Denied` on the
request code for a **retired installed manifest, `9055200629726788899`**, and nothing but
`content_log.txt` and the `appmanifest` ever said that id out loud.
> **Rule: a status code reports that something failed, never why. Find the instrument that
> names the object it failed on.**

What makes that one worth carrying past its own ticket is the near-miss. Two convenient
explanations were sitting in plain view — `validate` was in the args, and the updater's own
message volunteered *"the download cache may be older"* — and both were **load-bearing wrong in
opposite directions**: one would have weakened a safety check that was not at fault, the other
would have destructively deleted 2.1 GB (the cache measured **0 files**). Both would have
"worked", because the fresh download would have succeeded either way, **and both would have
written down a false cause for the next operator to inherit.** That is the expensive part, not
the bytes — and it is the same failure this survey's rules exist to prevent: an artefact, or a
narrative, accepted as evidence for a question it cannot answer.

**(5) A selector acting as a silent filter — `ServerUpdate`'s, two instances, and the most
generally useful finding of the night.** It surfaced as a performance guard and then turned out
to be something broader.

*Instance A, by content.* His first game-version census carried
`if len(b) > 40_000_000: continue` so a 101-mod scan would finish. It reported **44 hits and no
error**. MEASURED, reproduced independently: that guard **silently dropped 7 of the tree's 119
plugin DLLs** — and not a random 7. They are large *because they embed asset bundles*, which is
precisely the class this fleet's oldest rule already flags as hardest to read:

```
SouthsilArmor.dll                 92,162,048      Ravenwood_Currency/…Currency.dll  81,200,640
More_World_Locations_AIO.dll      79,241,728      RavenwoodVikingHouses.dll         64,162,816
RavenwoodRestorations.dll         59,241,984      AdventureBackpacks.dll            48,791,040
BalrondConstructions.dll          40,396,800
```

One of those seven, `Ravenwood_Currency`, was a real game-version reader. The output was clean,
plausible, and short by one.

*Instance B, by path shape.* Chasing a three-way disagreement about corpus size found a second,
independent filter in the same scan: globs of the form `*/**/*.dll` and `*/*.dll` both require
**at least one directory component**, so a DLL sitting loose at the top of `plugins/` matches
neither. MEASURED, and I reproduced his number exactly: a `*/**/*.dll`-shaped selector sees
**117**, a plain recursive walk sees **119**, and the two it cannot reach are
**`PlayerIdentities.dll`** and **`Valheim.DisplayBepInExInfo.dll`**, both loose at
`plugins/`. No glob error is raised; the count is simply smaller.

**The reconciliation, now three instruments in agreement where two were previously broken:**

| Corpus | Count | Note |
| --- | --- | --- |
| `.dll` under `plugins/` | **119** | recursive walk, no guard — the basis for every count in this document |
| of which > 40 MB | 7 | what instance A dropped |
| loose at `plugins/` root | 2 | what instance B could not reach |
| `.dll` under `patchers/` | 4 | |
| **total mod-set DLLs** | **123** | matches `ServerUpdate`'s independent `mod_set_dll_count` anchor written at 01:18, and matches this survey's own 123-DLL dead-field sweep |

The two missed files are harmless here — both are server-only shims already known to be
legitimately absent from the client edition — so **no finding in either document changes.** But
the count moved twice and **neither error announced itself.**

> **Rule (`ServerUpdate`'s general form, which is better than my first narrow one): any
> selector is a filter, and a filter that cannot report its own exclusions launders a partial
> corpus into a confident total. Size thresholds, glob depth, extension lists, `gitignore`
> defaults. Count the corpus with an independent enumerator before trusting any count taken
> through a selector.**

This one arrived through a door the other four did not: **not a misread instrument, a truncated
corpus.** Both instances were invisible until a second instrument disagreed, and the decision to
treat the two censuses as a *union* rather than picking a winner is the only reason either
surfaced. Worth recording as method, not luck.

**And one rule that is not an error but the generalisation of the whole night, `ServerUpdate`'s
again.** A Valheim version changed, and it was not a Valheim version problem. The update was
the thing that moved, so it looked like the thing that broke — but the break was a third-party
library's *opinion* about that version, and six mods sitting behind that opinion. Measured
consequence: **`Zen_ModLib 1.14.7` alone would have restored the join**; the game update fixed
nothing about the handshake and was taken only to make a 1.0.15-compiled library safe to run.
> **Rule: when a version changes and something breaks, find what *read* the version before
> assuming the version is at fault. The reader is the suspect, not the value.**

**Who actually reads the game version on this tree — and what each does about it.** That rule
is only actionable if you know which mods it applies to, so `ServerUpdate` and I censused it
with **two independent instruments**: his two-parity UTF-16LE scan, and my `#US` heap reader.
**The two runs disagreed, reconciling them found a reader neither had alone, and chasing *why*
they disagreed produced error (5) above.** Final basis: **all 119 plugin DLLs, no size guard,
no exclusions.**

First, the class that dominates and is *not* this problem: **46 plugin directories carry only
mod-version literals** (`ServerSync VersionCheck`, `<Mod>_VersionCheck`,
`Mod version mismatch {0}: Server {1}, Client {2}`). Those compare mod version **client↔server**,
not against the game — a different failure class, and the one the manifest pipeline exists to
prevent. Jotunn's line sits here, which is worth noticing: **Jotunn is what refused tonight's
handshake, but it was reporting someone else's failure.**

The genuine game-version readers are **six** — five carrying a version literal, one
shape-checking instead:

| Plugin | On a game-version change | Literal |
| --- | --- | --- |
| **`Zen_ModLib`** | **FAILS CLOSED — aborts the plugin and everything behind it** | `!!! VERSION MISMATCH !!! Valheim installed: {1}, required: {2}` |
| `RossItemDrawers` | fails open — disables the affected patches | `… not found -- skipping that patch. Drawers will misbehave. See the compatibility check above.` |
| `VHModpackFix` | fails open — skips one patch | `… the current ParticleMist.Update layout did not match the supported Valheim version` |
| **`Ravenwood_Currency`** | fails open — **found only by the `#US` reader, absent from the first census** | `This Valheim version has no compatible inventory removal method.` |
| `BepInExPack_Valheim` | logs only | `Valheim Version:` , `GetValheimVersion() failed:` |

plus `CLLCCompatibility`, which `ServerUpdate` found and my regex correctly did **not** — it
shape-checks an API (`QuickTeleport expected three sector API calls, found …`) rather than
reading a version literal at all, so a literal-based reader cannot see it. Both instruments
were right and were answering slightly different questions; **the union is the answer, and
picking a winner would have lost one reader either way.**

For completeness, since the corrected census also settles it: the three asset-bundle-heavy mods
that error (5) had dropped — `More_World_Locations_AIO` 5.1.1, `RavenwoodRestorations` 1.0.1
and `SouthsilArmor` 3.1.9 — carry **mod**-version literals only and were **already inside my
46**, because my reader had no size guard. So that count stands at 46, not 49.

> **So the sharpened rule: `Zen_ModLib` is the only game-version gate on this tree that fails
> CLOSED, and the only one with fan-out — 360 dependants upstream and six installed mods behind
> one opinion. That is why it goes first after any Valheim update. The other five fail open and
> cost a feature, not a handshake — so if a future update breaks something and `Zen_ModLib` is
> clean, look at the degraders and expect a silently missing feature rather than a refused
> join.**

**`[INFERENCE]`, and it matters here of all places:** the fails-closed / fails-open column is
read from each mod's **own message text**, not from observing any of them fail. Tonight's boot
is supporting but not decisive — `RossItemDrawers` logged its graceful path
(`shader has no _TriplanarScale; leaving UV mapping in place`) and there were zero `[Fatal` and
zero `[Error` across the 1.0.12 → 1.0.15 move — but **none of the four was pushed to its failure
branch.** Only `Zen_ModLib`'s abort is measured behaviour, because it is the one that actually
fired.

**Unjustified-by-changelog, declared:** none. Every DANGEROUS row in §1b carries a quoted line.
`OdinsFoodBarrels` declares no dependencies at all and `Skarif-BuildOnShip 3.2.1`'s own
changelog entry is the single word `nothing` — in the latter case the justification is quoted
from **3.2.0**, which the bump carries, and I have said so explicitly rather than attributing it
to 3.2.1.

**Reusable instruments left on disk** (self-contained, each runs in its own `python3` process —
no shared-kernel exposure): `/tmp/ms2/deadref2.py` (dead-field counter with signature decoding),
`/tmp/ms2/us.py` (`#US` heap reader), `/tmp/ms2/prefabs.py`, `/tmp/ms2/pieces.py` (registration
counter, handles both `MemberRef` and ILRepacked `MethodDef` tokens), `/tmp/ms2/sweep.py`,
`/tmp/ms2/pmsweep.py`, `/tmp/ms2/bundles.py`, `/tmp/ms2/claims.py`, `/tmp/ms2/inv.py`,
`/tmp/ms2/delta.py`, `/tmp/ms2/cl.py`. Index snapshot: `/tmp/moddelta/slim_0919.json`
(11,463 packages, pulled 2026-09-19 06:07 UTC); changelogs:
`/tmp/moddelta/changelogs_0919.json`.
