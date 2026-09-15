# Verdict: does the new RossItemDrawers work better than the 0.9.9 we run?

Beads: `vhp-tnx` — "RossItemDrawers GetIcon throws on items whose icon array is empty in
1.0" (open), and `vhp-eeb` — "ItemDrawers blocked every login by breaking Jotunn mod
compatibility" (closed 2026-08-17), which is why `cybrp-ItemDrawers` is shelved.

Status: **proposal only.** Nothing was installed, deployed, published or committed to
write this. No profile manifest, no `Ulfsland/data/`, no `config_merged/` was touched.
The only writes were into `/tmp/idv/` and this file.

Everything under a `$` prompt or marked **MEASURED** was measured on this host on
2026-09-15. Everything marked **[INFERENCE]** is reasoning, not measurement.

---

## The answer, in one paragraph

**Yes — and specifically for the defect you hit.** `Ross-RossItemDrawers 1.0.3`, published
2026-09-15T05:24:42Z (about four hours ago), fixes the three `GetIcon()` warnings you saw,
by the author's own hand in response to *our* report. It does **not** change the
`Unlit/Transparent` line, which was never a fault — it is a fallback notice and the
fallback works. The recommendation is **update**, but not for the log noise: the 1.0.0
release fixes silent **item loss** when Quick Stack Store, ServersideQoL AutoStore,
MultiUserChest or AutomaticFuel deposits into a drawer, and all seven profiles run all
four of those mods alongside 0.9.9 today. We carry **no local patch** for this mod, so
there is nothing to drop and nothing to collide. The cost is that 1.0.x introduces a
Jotunn version gate that 0.9.9 does not have: server and every client must move in
lockstep or joins are refused.

---

## 1. What we run now, and whether it is ours

### 1.1 Seven profiles, not four — and it is the whole fleet

```console
$ cd /media/big4/projects/game/valheim/profiles
$ # Ross-RossItemDrawers entry in each profile-manifest.json
admin           Ross-RossItemDrawers@0.9.9 (shared)   97 packages
flat            Ross-RossItemDrawers@0.9.9 (shared)   89
vr              Ross-RossItemDrawers@0.9.9 (shared)   89
ulfsland-admin  Ross-RossItemDrawers@0.9.9 (shared)   98
ulfsland-dn     Ross-RossItemDrawers@0.9.9 (shared)   99
ulfsland-flat   Ross-RossItemDrawers@0.9.9 (shared)   90
ulfsland-vr     Ross-RossItemDrawers@0.9.9 (shared)   90

$ cat {Hrafnheim,Doggerland,Storgard,Vangard,Ulfsland}/mods/.active-mod-profile
admin admin admin admin ulfsland-dn
```

**MEASURED.** The brief said four profiles. It is seven, and because `admin` is the active
profile for **Hrafnheim, Doggerland, Storgard and Vangard**, this mod is on every world on
the fleet, not just Ulfsland. Any decision here is a fleet decision.

### 1.2 The DLL we run is upstream 0.9.9, unpatched — byte for byte

```console
$ md5sum profiles/*/manager-cache/*/BepInEx/plugins/RossItemDrawers/ItemDrawers.dll \
    | awk '{print $1}' | sort | uniq -c
     14 1b0ab899f9852915d5ef31c5594ed060

$ unzip -o profiles/ulfsland-dn/manager-cache/packages/RossItemDrawers-0.9.9.zip -d /tmp/idv/zip999
$ md5sum /tmp/idv/zip999/plugins/ItemDrawers.dll \
         Ulfsland/data/bepinex/BepInEx/plugins/RossItemDrawers/ItemDrawers.dll \
         /mnt/vps-sync/profiles/Ulfsland--ulfsland-vr-flat-admin--flat/active/BepInEx/plugins/Ross-RossItemDrawers/ItemDrawers.dll
1b0ab899f9852915d5ef31c5594ed060  (upstream zip)
1b0ab899f9852915d5ef31c5594ed060  (deployed server)
1b0ab899f9852915d5ef31c5594ed060  (operator's client, over the CIFS mount)
```

`ItemDrawers.Core.dll` is likewise `f614c122…` in all of them.

**MEASURED: there is no local patch for this mod.** 14 cached copies (7 profiles x
server+client), the deployed server tree, and the operator's live client all hash equal to
the file inside the upstream Thunderstore zip. `tools/modpatches/` contains exactly one
patcher, `blacksmithing_expanded_null_key.cs`, for `OdinPlus-BlacksmithingExpanded`; the
only `.stock-<version>` sidecars on the box are Vangard's BlacksmithingExpanded pair.
Nothing of ours is at risk from this bump.

This was the question that determined everything else, and the answer is the simple one.

### 1.3 The defensive wording is the author's, not ours

All four strings the operator saw live inside the upstream 0.9.9 assembly:

```console
$ strings -el /tmp/idv/zip999/plugins/ItemDrawers.dll | grep -E "icon atlas|candidate|Unlit"
Skipping '
' in the icon atlas: GetIcon() threw (
No usable shader found for the drawer icon atlas material (tried
Shader.Find("
") returned null; trying next candidate.
Unlit/Transparent
```

**MEASURED.** The "compatibility fallback" tone is `rossxwest`'s house style, not a local
edit. (The similar-sounding `Requirement item 'Moss_bal' not found. Using 'Wood' as its
compatibility fallback.` lines four rows above them in the same log are BalrondConstructions,
a different mod entirely.)

---

## 2. What the three log lines actually are

### 2.1 `GetIcon() threw (IndexOutOfRangeException)` — a real defect, in the mod

The game's accessor is unguarded. From the deployed `assembly_valheim.dll`
(`monodis`, method line 10403):

```
ItemDrop/ItemData::GetIcon()
  ldarg.0
  ldfld  ItemDrop/ItemData::m_shared
  ldfld  ItemDrop/ItemData/SharedData::m_icons      // Sprite[]
  ldarg.0
  ldfld  ItemDrop/ItemData::m_variant               // int32
  ldelem.ref                                        // <- unchecked
  ret
```

**MEASURED:** `GetIcon()` is `m_icons[m_variant]` with no bounds check, so it throws
`IndexOutOfRangeException` exactly when `m_icons.Length <= m_variant`. 0.9.9 calls it raw
inside the atlas loop (`DrawerIconAtlas::BuildInternal`, `IL_008a callvirt … GetIcon()`
inside a `try`), catches `Exception`, and `LogWarning`s the skip.

Why these three items and nothing else: the loop only considers items with
`m_shared.m_maxStackSize > 1` (`IL_007d ble`), i.e. stackables. `draugr_arrow`,
`GoblinSpear` and `GoblinSpearDeepNorth` are mob-only weapons — the player never holds
them, so they were authored with **no icon at all**, an empty `m_icons` array, and with
`m_variant` at its default 0 that is `m_icons[0]` on a zero-length array.

That the array is empty rather than the variant being out of range is corroborated three
ways and I am calling it **MEASURED**: (a) the upstream author, replying to our issue on
2026-09-14T04:12:45Z — *"Those items don't appear to actually have icons, so they will
still have no icon, just the errors will be caught now"*; (b) the 1.0.3 changelog —
*"mob-only items carrying no icon at all"*; (c) the shape of the fix in §3.1, which
separates "no icons at all" (silent) from "variant out of range" (reported), and the
upstream position is that these three land in the first bucket. `GoblinSpearDeepNorth`
being new in 1.0 is what made this look like a 1.0 data-shape change when `vhp-tnx` was
filed; it is better described as a long-standing missing bounds check that 1.0 gave a
third item to trip on.

Impact: **cosmetic**. The item is skipped, the atlas is built without it, and a mob-only
item can never be in a drawer to need an icon. 6 warning lines per boot (3 items x 2 log
sinks).

### 2.2 `Shader.Find("Unlit/Transparent") returned null` — not a fault at all

`DrawerIconAtlas::.cctor` (0.9.9, `IL_0000`–`IL_001e`) builds a three-element candidate
list and `ResolveIconShader` walks it, returning the first that resolves:

```
ShaderCandidates = { "Unlit/Transparent", "Sprites/Default", "UI/Default" }
```

Only if **all three** miss does it `LogError` *"No usable shader found for the drawer icon
atlas material (tried …). Drawer icons will not render until this is fixed."*, destroy the
texture and give up (`IL_0295`–`IL_02d3`).

```console
$ L=/mnt/vps-sync/profiles/Ulfsland--ulfsland-vr-flat-admin--flat/active/BepInEx/LogOutput.log
$ grep -c 'Error' "$L"          # 0
$ grep -oE '^\[[A-Za-z]+' "$L" | sort | uniq -c
     52 [Message
    160 [Warning
$ sed -n '79,101p' ~/…/BepInEx/config/BepInEx.cfg   # [Logging.Disk]
LogLevels = Fatal, Error, Warning, Message
```

**MEASURED:** the disk sink *does* record `Error`, and the log contains zero `Error` lines
of any kind. So `No usable shader found` never fired, which means a later candidate —
`Sprites/Default` — resolved and the atlas was built. The confirming line
(`Icon atlas built: {N} icons in {W}x{H}, material shader = …`) is `LogInfo`, and `Info`
is excluded from the disk sink, which is why you cannot see it; its absence is not
evidence of failure.

`Unlit/Transparent` is a built-in Unity shader that is only present if something in the
build references it. Valheim 1.0's player build does not ship it, so `Shader.Find` returns
null. **[INFERENCE]** on the "stripped from the build" mechanism specifically; the
*consequence* — first candidate misses, second one works, icons render — is measured.

This line is a one-per-boot notice that a fallback engaged. Warning is arguably the wrong
level for it, but there is nothing here to fix.

---

## 3. 1.0.3 versus 0.9.9

Latest is **1.0.3**, published **2026-09-15T05:24:42Z**. The chain since we filed:

| version | published (UTC) |
|---|---|
| 0.9.9 (ours) | 2026-09-13 |
| 0.9.10 | 2026-09-14T04:12:12Z |
| 1.0.0 | 2026-09-14T20:50:07Z |
| 1.0.1 | 2026-09-15T01:57:13Z |
| 1.0.2 | 2026-09-15T03:30:27Z |
| 1.0.3 | 2026-09-15T05:24:42Z |

**MEASURED**, Thunderstore experimental API. Five releases in 25 hours. 0.9.10 landed 45
minutes *after* the author answered our issue and 34 minutes *before* `vhp-tnx` was last
touched, which is why we missed it: the bead's note "nothing further for us until the
author responds" was already stale when it was written.

### 3.1 The icon-atlas throw: **FIXED**

0.9.9 called `GetIcon()` directly. 1.0.3 routes through a new `ItemFacts::SafeIcon`
(method line 230), which is the whole fix:

```
SafeIcon(ItemData item, out bool variantOutOfRange)
  variantOutOfRange = false
  icons = item?.m_shared?.m_icons
  if (icons == null || icons.Length == 0) return null;          // <- the three items
  v = item.m_variant
  if (v < icons.Length) return icons[v];
  variantOutOfRange = true; return icons[0];                    // <- graceful fallback
```

**MEASURED** from `id103.il:18074-18133`. Two distinct behaviours where 0.9.9 had one
`catch`:

- **No icons at all** → `null`, the caller's `op_Equality` check skips the item
  (`IL_0108 brtrue`), **nothing is logged**. This is our three items. Warning gone.
- **Variant out of range on a non-empty array** → falls back to the first icon and is
  collected into a list, reported once at the end as a single `LogInfo`:
  `"{0} item(s) have an icon variant outside their own icon array and use their first
  icon: …"` (`id103.il:6755-6767`). `LogInfo`, so it will not even reach our disk sink.

The `try`/`catch` around the call survives as a last resort, with its wording changed from
`GetIcon() threw` to `reading its icon threw` — so if you ever *do* see a skip line again
it is a genuinely new fault, not this one.

The changelog names us: *"Stopped warning every boot about items whose icon cannot be
read. On Valheim 1.0.12 that is draugr arrows and the two goblin spears, which are
mob-only items carrying no icon at all. … Thanks to neuralyze for the report (#3)."*
Upstream issue #3 is still open, unclosed, at the time of writing.

### 3.2 The shader line: **NOT FIXED — and correctly so**

```console
$ grep -n -A2 'returned null; trying next candidate' /tmp/idv/id103.il
6812:  ldstr "\") returned null; trying next candidate."
6814:  callvirt … ManualLogSource::LogWarning(object)
```

**MEASURED:** `ResolveIconShader` in 1.0.3 (`id103.il:6771-6830`) is instruction-for-
instruction the same 91-byte method as 0.9.9 (`id999.il:4960-5019`): same candidate list
with `Unlit/Transparent` still first, same `LogWarning`, same fallthrough. **You will still
see this line once per boot after updating.** Since §2.2 establishes it is a notice and
not a failure, that is the right outcome — but the operator asked a comparative question
and the honest answer for this line is "unchanged".

### 3.3 Scoreboard

| log line | 0.9.9 | 1.0.3 | evidence |
|---|---|---|---|
| `Skipping 'draugr_arrow' … GetIcon() threw` | warns every boot | **FIXED**, silent | `SafeIcon` returns null for empty `m_icons`; caller skips without logging |
| `Skipping 'GoblinSpear' …` | warns | **FIXED**, silent | same path |
| `Skipping 'GoblinSpearDeepNorth' …` | warns | **FIXED**, silent | same path |
| `Shader.Find("Unlit/Transparent") returned null; trying next candidate.` | warns | **NOT FIXED**, identical IL | `ResolveIconShader` byte-identical between versions |

Net: 6 lines per boot removed, 2 remain (1 line x 2 sinks).

### 3.4 The reason to actually update is item loss, not log noise

This is the finding that changes the recommendation from "cosmetic, defer" to "do it".

```console
$ # every profile, same answer:
BiggerChests            Cytraen-BiggerChests@1.1.0                              shared
Quick Stack Store       Goldenrevolver-Quick_Stack_Store_Sort_Trash_Restock@1.4.15  shared
MultiUserChest          MSchmoecker-MultiUserChest@0.6.2                        shared
AutomaticFuel           TastyChickenLegs-AutomaticFuel@1.5.1                    shared
ServersideQoL AutoStore ArgusMagnus-ServersideQoL_AutoStore@2.0.8               shared
```

**MEASURED:** all seven profiles run four container-writing mods next to drawers 0.9.9.
The 1.0.0 changelog: *"Deposits of a drawer's item are added to its count. **Previously
those mods saw the deposit succeed and the items were lost**, and Quick Stack Store's
quick-stack failed for every container once a drawer was nearby."*

I did not take that on trust. Type inventory of both assemblies:

```console
$ # in 1.0.3 and not in 0.9.9:
AddItemPatch  AddItemAtPositionPatch  AddItemToSlotPatch  DropItemPatch
InventoryStackGuard  InventoryAccess  DrawerView  ZNetViewResetZdoPatch
DrawerDiagnostics  PieceSetCreatorPatch
$ # Harmony patch attributes: 0.9.9 = 7, 1.0.3 = 13
$ # Container members touched: 0.9.9 {Awake, GetInventory, m_privacy, m_checkGuardStone}
$ #                            1.0.3 adds {IsInUse, Load, CheckAccess}
```

**MEASURED.** 0.9.9 has **no** interception of `Inventory.AddItem` in any of its three
overloads. A mod that writes into the drawer's proxy inventory therefore has nowhere for
the items to land. The changelog's claim is structurally consistent with the code; the
1.0.x series adds exactly the missing half. **[INFERENCE]** that every deposit through
those four mods is losing items *today on our profiles* — I have not reproduced the loss
in game, and I will not without a running client — but the exposure is real and the
mechanism is present.

1.0.1 through 1.0.3 are then follow-ups to that work: hotbar keys no longer silently
deposit into an assigned drawer (1.0.1), two nearby clients no longer fight over a drawer
until every withdrawal is refused (1.0.2), and the mod no longer refuses your own keypress
because its own housekeeping touched the drawer (1.0.3). 1.0.3 is the settled end of that
chain, not a fresh unknown.

### 3.5 Compatibility with 1.0.12: clean on both, which means this is not a compat fix

Every `TypeReference` and `MemberReference` in each assembly, resolved against the
assemblies the server actually runs — `Ulfsland/data/bepinex/valheim_server_Data/Managed`
plus `BepInEx/core`, plus Jotunn 2.30.0 from the profile cache:

```console
$ mono refcheck.exe work/ItemDrawers.dll work          # 0.9.9      unresolved=0
$ mono refcheck.exe work/ItemDrawers.Core.dll work     # 0.9.9      unresolved=0
$ mono refcheck.exe work103/ItemDrawers.dll work103    # 1.0.3      unresolved=0
$ mono refcheck.exe work103/ItemDrawers.Core.dll work103  # 1.0.3   unresolved=0
```

**MEASURED, and the harness was validated before I believed it.** Run against
`Smoothbrain-ServerCharacters 1.4.16` in the same workspace it returns `unresolved=29`,
reproducing all five known real faults — `PlayerProfile::GetCharacterFolderPath`,
`Character::Message`, `Game::SavePlayerProfile`, `MessageHud::ShowMessage`,
`Terminal/ConsoleCommand::.ctor` — plus the known `System.Numerics.Vector` /
`ImmutableArray` BCL noise baseline. A zero from this tool is a measurement, not a
vacuous pass.

So neither version is broken on 1.0.12 by reference graph. **0.9.9 is not an emergency.**
The case for moving is §3.1 and §3.4, not compatibility.

Related, because a sibling raised it: `tools/everybodyshim/UPSTREAM-valheim10compatibility-
selfblock.md:50` lists RossItemDrawers among 13 plugins referencing
`PieceTable.m_availablePieces`. That exposure is **unchanged by the bump** — 0.9.9 and
1.0.3 each carry exactly 4 references, one `HashSet<Piece> PieceTable::m_availablePieces`
and two `List<List<Piece>> PieceTable::m_availablePiecesByCategory`, same declared types
(`id999.il:9455-9572`, `id103.il:11588-11705`). The field it reads is the **native 1.0.12
`HashSet`**, not the pre-1.0 alias the blocked Valheim10Compatibility bridge would inject,
which is why the reference check resolves it. Nothing in `tools/everybodyshim/` needs to
change for this update. **MEASURED.**

### 3.6 The cost: 1.0.x adds a version gate that 0.9.9 does not have

```console
$ grep -c NetworkCompatibility /tmp/idv/id999.il   # 0
$ grep    NetworkCompatibility /tmp/idv/id103.il
  .custom instance void [Jotunn]Jotunn.Utils.NetworkCompatibilityAttribute::.ctor(…)
      = (01 00 02 00 00 00 02 00 00 00 00 00)
$ # decoded against Jotunn.dll 2.30.0:
  CompatibilityLevel  2 = EveryoneMustHaveMod       (0 NoNeedForSync, 1 OnlySyncWhenInstalled)
  VersionStrictness   2 = Minor                     (1 Major, 3 Patch)
```

**MEASURED.** Consequences, stated plainly:

- **Today**, 0.9.9 declares no network compatibility at all. A client without the mod, or
  with a different version of it, joins fine.
- **After the update**, the server and **every** client must have the mod, and versions
  must match to **minor** precision. A client still on 0.9.9 against a 1.0.3 server is
  **refused at connect** — the same class of failure that cost an hour on ServerCharacters
  1.4.16-vs-1.4.17 tonight, and the same mechanism that `vhp-eeb` records for cybrp.
- The strictness is `Minor`, not `Patch`, so **1.0.1 / 1.0.2 / 1.0.3 interoperate**. That
  is real slack: a client that is one patch behind still joins. A client on 0.9.x does not.

Carry-over is clean:

```console
$ # config sections bound by 1.0.3 vs the deployed cfg
1.0.3 binds:   Capacity  Display  Pickup  Recipe
com.rossdwest.itemdrawers.cfg: [Capacity] [Display] [Pickup] [Recipe]
$ # RPC and prefab identifiers, 0.9.9 vs 1.0.3
identical: RID_ReqDeposit RID_GrantDeposit RID_ReqWithdraw RID_GrantWithdraw RID_ReqClear
           rid_drawer_wood rid_drawer_stone rid_drawer_blackmarble  (+21 more)
1.0.3 adds only: rid_probe
```

**MEASURED.** No config migration, no prefab rename, the five RPCs are unchanged, so
existing drawers and their contents survive — consistent with the changelog's *"Existing
drawers need nothing done."*

---

## 4. The rival: why `cybrp-ItemDrawers 1.2.6` is shelved

Found, not guessed. Bead **`vhp-eeb`**, closed 2026-08-17, commit `5c42802` "Record the
login failure ItemDrawers caused":

> Operator could not log in to Hrafnheim. Server log: `Jotunn.Utils.ModCompatibility
> RPC_Jotunn_ReceiveVersionData: Disconnecting modded client with incompatible version
> message` plus `Missing mod on client: ImpactfulSkills`. ImpactfulSkills was **NOT**
> missing — identical DLL md5 on both sides. The client log named the real cause:
> `[Cybrp ItemDrawers] PieceManager was accessed before Jotunn Awake … Please make sure to
> add [BepInDependency(Jotunn.Main.ModGuid)]`. **ItemDrawers initialises before Jotunn
> because it declares no Jotunn dependency, which corrupts Jotunn's plugin enumeration**;
> ImpactfulSkills then never appears in the client's advertised mod list and the server
> refuses the peer. Timeline: 18:53 sync had 125 plugins and no ItemDrawers and played
> fine; the 19:20 republish shipped ItemDrawers, the next sync gave 129 plugins, and
> logins stopped.

And the closing note sets the condition for un-shelving explicitly:

> NOTE the mod is DISABLED on Hrafnheim, not removed, so its entry survives in
> `disabled_packages` as a record. **Do not re-enable without the author adding
> `BepInDependency(Jotunn)`** — it has now broken the server boot once and every client
> login once.

That condition is **not met**, and cannot be met by any published build:

```console
$ monodis kg_ItemDrawers.dll | grep -E "BepInPlugin|BepInDependency"
  .custom instance void [BepInEx]BepInEx.BepInPlugin::.ctor(string,string,string)
$ # no BepInDependency of any kind, anywhere in the assembly
$ curl -s https://thunderstore.io/api/experimental/package/cybrp/ItemDrawers/
  latest 1.2.6   2026-08-06T07:48:58Z   deps [BepInExPack 5.4.2333, Jotunn 2.29.2]
$ mono refcheck.exe workcyb/kg_ItemDrawers.dll workcyb
MEMBER System.Void Character::Message(MessageHud/MessageType,System.String,System.Int32,UnityEngine.Sprite)
unresolved=1
```

**MEASURED**, three independent disqualifications:

1. **The defect is still there.** 1.2.6 has a `BepInPlugin` attribute and **no
   `BepInDependency` at all**, so it still loads before Jotunn. The Thunderstore manifest
   declaring a Jotunn dependency is a *download* dependency, not a load-order one — that
   distinction is precisely what bit us in August.
2. **There is no newer build.** Latest is still 1.2.6 from 2026-08-06, unchanged in 40
   days, and the package is no longer reachable at its documented capitalised URL
   (`/c/valheim/p/Cybrp/ItemDrawers/` → 404; only the lowercase API path resolves).
3. **It is broken on 1.0.12 anyway.** One unresolved member —
   `Character::Message(MessageType, string, int, Sprite)`, which gained a trailing
   `bool log` in 1.0 — the same trap that was patched out of the CLLC path tonight.
   `MissingMethodException` at first call.

There is also a superseded operator decision worth recording so nobody re-litigates it in
the wrong direction: bead `vhp-7yd`, 2026-08-17 — *"DRAWERS: DROPPED, DECISION IS CHESTS.
Operator ruled out the whole drawer path … Do not revisit without a fresh request."* That
ruling was about the **cybrp/kg lineage**, and it has since been overtaken by a fresh
request: the operator adopted `Ross-RossItemDrawers`, a different implementation by a
different author, in September, and it is in all seven profiles. The August ruling stands
for cybrp and does not bind Ross.

**Do not switch.** Leave the `disabled_packages` entry exactly where it is; it is the
record of why.

---

## 5. Recommendation

**UPDATE** `Ross-RossItemDrawers` 0.9.9 → 1.0.3, fleet-wide, as one lockstep move.

Ranked against the alternatives:

- **Stay** — loses on §3.4. Four container-writing mods sit next to a drawer
  implementation with no `Inventory.AddItem` interception on every profile. The log noise
  alone would not justify a fleet move; silent item loss does.
- **Switch to cybrp** — disqualified three times over in §4.
- **Update and drop our patch** — **not applicable, and that is a measured finding, not an
  omission.** There is no ItemDrawers patch to drop. `tools/modpatches/` holds only
  `blacksmithing_expanded_null_key.cs` (target `OdinPlus-BlacksmithingExpanded` 1.1.7), and
  all 14 deployed copies of our ItemDrawers DLLs hash equal to the upstream zip (§1.2). No
  by-name patcher is invalidated by this bump, and `tools/everybodyshim/` is unaffected
  (§3.5). Nothing dies quietly here.

The one judgement call is **1.0.3 is four hours old**. It is a patch release at the end of
a tight five-release chain, all of it fixing regressions in 1.0.0's container work, and
`VersionStrictness.Minor` means a later 1.0.4 would not break a fleet sitting on 1.0.3.
Taking 1.0.3 rather than waiting is the better trade: 1.0.1 and 1.0.2 both fix defects
you would otherwise ship.

### Rollout

**All seven profiles**, because `admin` serves four worlds:
`admin`, `flat`, `vr`, `ulfsland-admin`, `ulfsland-dn`, `ulfsland-flat`, `ulfsland-vr`.

Server side, per world: Ulfsland (profile `ulfsland-dn`) and — whenever they are next
started — Hrafnheim, Doggerland, Storgard, Vangard (all profile `admin`).

Client side, **a republish is REQUIRED**, and this is the part that must not be split.
`release-targets.json` lists 15 flat targets plus the VR set; the ones carrying this mod
are every published edition of every world, including all three Ulfsland editions:
`ulfsland-vr-flat`, `ulfsland-non-vr`, `ulfsland-vr-flat-admin` (the operator's own), and
the `ulfsland-vr` VR target.

Because 1.0.x is `EveryoneMustHaveMod` + `VersionStrictness.Minor`:

1. Bump the manifests in all seven profiles in one change, not world by world.
2. Republish every affected client target **before or with** the server deploy. A client
   still on 0.9.9 is refused at connect; a client on any 1.0.x is fine.
3. Ulfsland is live with admin mode armed — the server deploy must be scheduled by the
   operator, not slipped in. Nothing in this document touched it.

### Loose end worth closing

`vhp-tnx` should move to closed-fixed once 1.0.3 is deployed and a boot log shows the
three lines gone, and upstream issue #3 (still open) can be closed with that confirmation.
The author responded to our report in 45 minutes and shipped the fix the same night;
saying so is cheap and buys goodwill on the next report.
