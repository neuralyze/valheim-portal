# Valheim + VHVR working knowledge

A reference for anyone modding Valheim, running it as a dedicated server, or trying to make
either work under VHVR (the ValheimVRMod community VR mod): mod authors, server operators,
and whoever inherits a VR profile that installs cleanly and then silently does nothing.

**Every claim here was verified, not inferred.** Each one was read in source, decompiled out
of the game's IL, or measured at runtime while debugging a real failure. Nothing was taken
from documentation, a mod's README, or a plausible-looking API name -- on this stack those
are wrong often enough that trusting them produced the false conclusions catalogued in
section 1. Anything reasoned rather than proven is marked **[INFERENCE]**.

It is long because it is a reference rather than a tutorial. Read section 1 in full once --
it is about how to measure this stack without fooling yourself, and it generalises beyond
Valheim -- then skim the headings and come back for the section you need. The process that
consumes these facts is [`mod-onboarding.md`](mod-onboarding.md), the tooling that automates
its static half is [`vr-impact-scan.md`](vr-impact-scan.md), the per-package verdicts are in
[`mod-decisions.md`](mod-decisions.md), and the diagnosed failures behind them -- with
confirmed root causes kept separate from packages merely bisected out -- are in
[`mod-compatibility-register.md`](mod-compatibility-register.md).

**Keep this updated.** When a new gotcha is found, add it with its evidence (`file:line`, IL,
or the measurement). When something here turns out wrong, correct it *in place* and say so —
a stale entry is worse than a missing one, because it will be trusted.

Conventions: **VERIFIED** = read in source/IL or measured. **[INFERENCE]** = reasoned, not
proven. Citations like `VRControls.cs:521` are VHVR upstream; bare `Player::X` is
`assembly_valheim.dll`.

---

## 1. Methodology — read this first

These cost more time than any technical detail below.

### A probe that can silently read zero will lie to you

Three separate instruments returned a confident `0` this session and each zero was acted on
as a finding, producing two shipped "fixes" based on nothing:

| probe | reported | actual cause |
|---|---|---|
| HUD delta | `dPos=0.000000` | sampled once per frame at the wrong point in the loop |
| hand velocity | `0.00 m/s` | **twice** — wrong method arity, then a `null` swallowed |
| eye resolution | `0x0` | `XRSettings` is never populated on SteamVR's OpenVR path |

**Rule: before trusting a zero, prove the instrument reads non-zero under a known-good
condition.** Every probe should emit `INSTRUMENT OK` / `INSTRUMENT DEAD` with the value that
justifies the verdict. "Measured zero" and "failed to measure" must never look alike.

### Reflection traps that fabricate zeros

```csharp
// TRAP 1: matching by argument count misses optional parameters.
// PhysicsEstimator.GetVelocity(Vector3? position = null) has ONE parameter.
// A search for a zero-arg GetVelocity finds nothing.

// TRAP 2: a bare null binds to the params ARRAY, not to one null argument.
Call(target, "GetVelocity", null)              // args == null -> args.Length throws
Call(target, "GetVelocity", new object[]{null}) // correct
```

Both were followed by `catch { return 0f; }`, which converted an exception into data.
**Never let a catch block return a plausible value.** Return a sentinel, or set a flag the
report carries.

### Ship new behaviour OFF

Three regressions this session, all from defaults that had never been exercised:

- crouch — flipping `m_crouchToggled` directly bypassed VHVR's bookkeeping (2.1.67)
- jump — gating dodge on grip made *every* jump a dodge, because grip is always lightly held
- broad breakage — a profiler that Harmony-patched **VHVR itself**, on by default

**Never instrument the mod you depend on.** **Never enable invasive measurement by default
while someone is testing gameplay.** New behaviour arrives off, gets enabled for one
deliberate session, becomes default only once confirmed.

### A code default is not a deployed value

BepInEx writes a plugin's `.cfg` **once**, on the client's first run, then leaves it alone.
Changing the default in code therefore has **no effect on any client that has already run the
plugin**. This bit hard: `ProfilePluginUpdateCost` was flipped to `false` in code to stop a
crash, the crash kept happening, and the reason was that the client's config still said
`true` from three releases earlier.

Made permanent by our own sync fix — `preserveUnmanagedConfig` deliberately carries unshipped
configs forward, so a stale value now survives every sync **forever**.

**The only way a release can correct a value already on a client is to ship the `.cfg` in the
profile payload.** Our plugin config is now shipped for exactly this reason. Corollary: if a
config value matters, it must be in `client-config/` — otherwise you cannot change it later.

### `IsAssignableFrom` loads field types, and throws

```csharp
foreach (Type t in asm.GetTypes())              // guarded - fine
    if (typeof(MonoBehaviour).IsAssignableFrom(t))   // THROWS TypeLoadException
```

`IsAssignableFrom` forces the CLR to resolve the type's **field types**. A mod whose
dependency assembly is absent throws `TypeLoadException` *here*, not from `GetTypes()`.
Observed: `AAA_Crafting` has a field of type `Auga.CraftingControls:InputAmount` and `ui_lib`
is not installed. Guard **per type**, not per assembly. `ReflectionTypeLoadException.Types`
containing nulls is the better-known half of this trap; this is the other half.

### Optional subsystems must never run unguarded in `Awake`

That `TypeLoadException` propagated out of `Awake`, so **every** feature of the plugin died at
once — jump, interaction, crouch, menu, radial ring — while the log showed only a stack trace
naming an unrelated mod. The symptom is "everything stopped working", which invites a hunt for
a systemic cause when the real fault is one optional line.

Wrap each optional subsystem individually and log which one was skipped:

```csharp
Guard("pluginProfiler", delegate { PluginProfiler.Install(harmony); });
```

**Degrade, never die.** And a plugin should log a single line at the *end* of `Awake` — its
absence is then a reliable signal that `Awake` aborted, which is how this was finally found.

### Measure before changing a config

`SwingSpeedRequirement` was lowered `3.0 → 1.5` to compensate for a frame-rate theory built
on a broken velocity probe. The change may have been harmless, but it was not evidence-based.

### IL tooling that actually works here

- `monodis` **segfaults** on `assembly_valheim.dll` — Unity's split assemblies
  (`UnityEngine.CoreModule`, `AnimationModule`) can't be resolved, and supplying them doesn't
  fix it.
- A hand-rolled opcode walker over `dnfile` **does** work: read `MethodDef.Rva`,
  `pe.get_offset_from_rva(rva)`, tiny header when `(b0 & 3) == 2` with size `b0 >> 2`, else fat
  header with size at `+4` and code at `+12`; resolve 4-byte tokens by `tok >> 24` against
  `Field` / `MemberRef` / `MethodDef` / `TypeRef` / `TypeDef`.
- Beware filtered dumpers. One in use showed only call-like instructions, so
  `Character::IsCrouching` printed **0 instructions** — which looked like an empty method but
  actually meant *no calls*, itself the clue that it reads a field rather than the animator.

### Diagnostics bundles are retroactively minable

Old bundles answer new questions. `LoadTimeProfiler` writes **exact per-plugin startup
sections**, so per-mod load cost was recoverable from bundles collected before any profiling
existed. Check what's already in the log before instrumenting.

---

## 2. Valheim internals (VERIFIED from IL)

### Crouch is not what it looks like

```
Character::IsCrouching  ->  ldc.i4.0 ; ret                      // base stub, ALWAYS false
Player::IsCrouching     ->  GetCurrentAnimHash() == s_animatorTagCrouch   // ANIMATOR query
Player::SetCrouch       ->  stfld m_crouchToggled                // the ONLY writer, anywhere
Character::SetCrouch    ->  ret                                  // empty base stub
```

**`IsCrouching()` reports animation state, not crouch state.** It returns false whenever the
crouch-tagged clip isn't active — so it is a false negative for a standing crouch.
`m_crouchToggled` is authoritative.

`Player::UpdateCrouch(dt)` calls `SetCrouch(false)` if **any** of: insufficient stamina,
`IsSwimming`, `InBed`, **`InPlaceMode`** (build/hammer mode), **`m_run`**, `IsBlocking`, or
not `IsFlying`. Two of those are easy to hit accidentally.

### `Player::SetControls` — the input chokepoint

```
SetControls(movedir, attack, attackHold, secondaryAttack, secondaryAttackHold,
            block, blockHold, jump, crouch, run, autoRun, dodge)   // 12 params, crouch is 9th
```

- `if (crouch) SetCrouch(!m_crouchToggled)` — a **toggle driven by a pulse**
- `m_run = run` — unconditional, no `&& !crouch` guard
- engaging auto-run calls `SetCrouch(false)` — a second way run kills crouch
- it calls `Dodge(...)` itself when `!IsCrouching() && m_crouchToggled` and move-dir magnitude
  passes, else `Jump(0)` — **vanilla already has a directional dodge**

**Only caller: `PlayerController::FixedUpdate`** — fixed timestep (~50 Hz), *not* frame rate.
At 25 FPS that's ~2 calls per rendered frame. When `TakeInput()` is false it still calls
`SetControls` with **all args zero**, so an input landing only in that window is dropped.

### Attack API shapes

```
Humanoid::StartAttack(target, secondaryAttack)      // public virtual
Character::StartAttack(target, charge)              // DIFFERENT arity - easy to grab the wrong one
Attack::Start(character, body, zanim, animEvent, visEquipment,
              weapon, previousAttack, timeSinceLastAttack, attackDrawPercentage)  // 9 params
Attack::OnAttackTrigger()      // PUBLIC - the cheap reflective target
Attack::DoMeleeAttack()        // private
Humanoid fields: m_currentAttack, m_currentAttackIsSecondary, m_unarmedWeapon
```

### Other useful symbols

```
Player::Dodge(dodgeDir)      private        Player::m_hovering, m_interactMask
Menu.instance + Show/Hide/IsVisible         InventoryGui.instance + Show/Hide/IsVisible
Minimap: m_smallRoot, m_largeRoot, m_mode, SetMapMode
Hud: m_hoverName, UpdateCrosshair, m_crosshair
```

### Hover text format

```
Branch
[<color="yellow">E</color>] To use
<sprite="xbox" name="button_lt">+<sprite="xbox" name="button_a"> To do something else
```

Keyboard prompts are **bracketed**; gamepad prompts are **TMP `<sprite=…>` tags with no
brackets**. Stripping only bracketed lines leaves half the prompts behind.

---

## 3. VHVR internals

### The clone pattern — the single biggest source of wasted effort

**VHVR clones HUD objects and repoints the vanilla reference at the clone.** Patch the field
you expect and you'll modify an orphan.

| element | reality |
|---|---|
| minimap | `Instantiate(m_smallRoot)`, then `Minimap.instance.m_smallRoot = clone`. **`m_smallRoot` is the wrist map**; the abandoned original is exposed as the public `Original` property |
| hover name | `hud.m_hoverName = hoverText` (the clone) at `CrosshairManager.cs:499` |
| hover name, left hand | a **separate** `hoverNameCloneLeftHand`, written directly by `HandBasedInteractionPatches.cs:103` |

Deactivating `m_smallRoot` to "remove the corner minimap" removes the **wrist** map instead.
Always check whether a field has been repointed before writing to it.

### Input: what VHVR silently discards

```csharp
// VRControls.cs — hardcoded, never cleared
ignoredZInputs = { "Crouch","Run","Hide","Block","Attack","SecondAttack",
                   "ToggleWalk","GPower","AutoRun","Forward","Backward","Left","Right", ... }

// VRControls.cs:482
if (!mainActionSet.IsActive() || ignoredZInputs.Contains(zinput)) return false;
if (zinput == "Jump" && !canJump()) return false;

// canJump():856
if (canRemovePiece() || SteamVR_Actions.valheim_UseLeft.state) return false;
return !BuildingManager.isCurrentlyMoving() && !isCurrentlyPreciseMoving() && !isHoldingPlace();
```

- `zInputToBooleanAction` has only **10** entries. **`Jump` and `Remove` both map to
  `valheim_Jump`** — upstream bug; A both jumps and removes a build piece.
- **`Unmapped ZInput Key: <name>` is logged once, then the name is added to a permanent
  ignore set** (`VRControls.cs:521`). A mod's custom action is dead for the session, silently,
  after one frame. 22 such actions were live on one client, including map zoom.

### Two public injection hooks

```csharp
ZInput_GetButtonDown_Patch.EmulateButtonDown(string)   // ControlPatches.cs:20
ZInput_GetKeyDown_Patch.EmulateKeyDown(KeyCode)        // ControlPatches.cs:79
```

Their prefix consumes from a `HashSet` **before every gate**, so they bypass `canJump()` and
the ignore list. **But it is single-consumption**: with ~100 mods polling input, whichever
calls `ZInput.GetButtonDown` first eats the pulse. Measured: 9 × `bridged 'Jump'` with zero
effect. Fine for a one-shot menu selection, **unusable as a general input bridge** — call the
gameplay method directly instead.

### Attacks: VHVR *replaces* `Attack.Start`

`CollisionPatches.cs:25-130` is a prefix that **is** the melee damage path:

```csharp
Collider col = StaticObjects.lastHitCollider;
Vector3  pos = StaticObjects.lastHitPoint;
Vector3  dir = StaticObjects.lastHitDir;
if (col == null || …) return false;      // :116-119 bails with __result ALREADY true
doMeleeAttack(…, pos, col, dir, …);      // :121 all damage happens here
```

So `StartAttack` returns **true** with no animation and no damage when those statics are
unset — silent success. To make an attack land: publish the three statics (as
`FistCollision.cs:445-447` does), then call `StartAttack`.

`FistCollision.canAttackWithCollision():539-552` requires `VRPlayer.inFirstPerson`, a non-null
`transform.parent`, no active grab — **and for a free hand, `valheim_Grab` HELD.** Bare-fist
punching requires holding the grip.

`PhysicsEstimator.GetVelocity(Vector3? = null)` returns `hand.GetTrackedObjectVelocity()` when
`hand` is set (SteamVR tracked velocity, **not** frame-derived), else a snapshot average that
is `Vector3.zero` when the snapshot list is empty.

### Interaction reach

`HandBasedInteractionPatches.cs` postfixes `Player.FindHoverObject` and resolves **both**
pointers — the right one writes vanilla `m_hovering`. But:

```csharp
hoverReference = null;                                   // :172 clears it first
if (Vector3.Distance(instance.m_eye.position, hitPosition)
        >= instance.m_maxInteractDistance) return;       // :201 range from the EYE
```

A laser that visibly reaches 50 m only interacts within ~2 m **of your head**. There is no
`rightHover` field; the left hand works via its own explicit path reading
`useLeftHandAction.GetStateDown` directly, which is why left-hand grab worked when the right
trigger appeared dead.

### GUI conversion

```csharp
VRGUI.cs:60-79    ADDITIONAL_GUI_CANVAS_NAMES   // hardcoded name whitelist
VRGUI.cs:747-779  ensureGuiCanvas               // scans ONCE at startup
VRGUI.cs:952-955  sets worldCamera + renderMode // NEVER sets the layer
VRGUI.cs:992      _guiCamera.cullingMask = 1 << LayerMask.NameToLayer("UI")
```

Consequences: a mod canvas created *after* startup is never found; a canvas can be adopted
and still invisible because its objects aren't on the `UI` layer. A canvas **nested** under
vanilla `GuiRoot/GUI` is fine and inherits WorldSpace — Jotunn does this deliberately
(*"Don't use own canvas for custom GUI (fixes compat with VHVR)"*).

Only **three** `UnityEngine.Input` members are patched: `get_mousePosition`
(`UIPatches.cs:114-123`), `GetKeyDownInt` and `GetKeyInt` (`TextInputPatches.cs:104,:111`).
**No `GetMouseButton`, no `mouseScrollDelta`, no scroll axis** — so mods using mouse buttons
are dead rather than misfiring, and drag/resize handlers behave inconsistently.

### Sneak, run, and the camera

```csharp
SneakInput = "CrouchingOnly" | "ControllerOnly" | "CrouchingOrController"   // default the last
RoomScaleSneakEnabled() => SneakInput != "ControllerOnly"
```

With roomscale sneak active, `handleRoomscaleSneak` **sends a crouch pulse to toggle crouch
OFF** whenever the latch is set but you're standing upright. `handleControllerOnlySneak` sets
`_isJoystickSneaking = true`, which is what protects the latch from that branch. Writing
`m_crouchToggled` directly bypasses the flag and gets you un-crouched next frame.

**`VRPlayer.cs:1227`**: the headset's crouch offset is gated on
`IsCrouching() && isJoystickSneaking` — i.e. on the **animator**. So you can be genuinely
crouched and not feel it while standing still. Diagnosed as cosmetic, not a state bug.

Run defaults that matter: `RunIsToggled = true` (so hold-run branches are inert),
`AutoRunThreshold = 1` (disables stick-Y auto-run; the only live route to `isAutoRunActive`
is gesture-running).

### Radial menus are extensible

```csharp
QuickAbstract: MAX_ELEMENTS = 11, MAX_EXTRA_ELEMENTS = 8
QuickMenuItem.useAsQuickAction(string itemName, Sprite sprite, QuickMenuItemCallback callback)  // PUBLIC
QuickMenuItem.useAsNoOp()
```

`extraElements` already holds non-item actions (Sit, Map) at `QuickAbstract.cs:583-644`. A
postfix on `refreshItems()` can fill unused slots, and paging the same ring gives extra
levels — no second UI needed. `sprite` may be null.

### `HideHotbar` is not a hide

Its patch prefixes `HotkeyBar.Update`, **destroys every element**, and skips the original. The
element list is then empty, so it is effectively one-time rather than per-frame churn. It does
**not** touch AzuEPI's own bar, which has its own prefix on the same method. Deactivating the
`HotkeyBar` GameObject is the cleaner hide — hotkeys are input-driven, not UI-driven.

### A missing plugin startup line means `Awake` aborted

If a plugin logs on load (`Loading [X 1.2.3]`) but its own "ready"/"loaded" line never
appears, `Awake` threw partway through. BepInEx does not treat that as fatal, the plugin
object exists, and its `Update`/`LateUpdate` never run — so the mod is inert with no error
attributable to it. Always check for the plugin's *own* final startup line, not BepInEx's
`Loading` line.

### Expected noise — do not chase these

- `Layer 23 is a named layer: blocker` — VHVR deliberately reuses vanilla layer 23
  (`LayerUtils`). Expected.
- `VR Camera is null while creating world space UI camera` — scene-transition noise
  (`CameraUtils.cs:59`), logged dozens of times per session.
- `incompatible world version -1` — appears in **working** sessions too. Not a version
  mismatch.

### SteamVR bindings ship with the runtime

`Valheim_Data/StreamingAssets/SteamVR/` — `actions.json` plus per-controller bindings. Quest
over Link presents as `oculus_touch`. `actions.json` declares 42 actions and several are bound
to **nothing**: `Sit`, `Dodge`, `HotbarUp/Down/Use`, `HoldRun`, `ToggleAutoPickup`,
`LaserPointers/Jump`. The shipped file is a German-locale *"ValheimVR [Testing]"* config and
contains a stale `right y` binding (Touch right controllers have no Y).

To find what a button *actually* does, ask SteamVR at runtime for the action's
**`activeOrigin`** and localised origin name. `activeBinding == true` only means SteamVR bound
*something* — it does not mean the press reaches the game.

---

## 4. Dedicated server in Docker

### The log-driver deadlock (cost a full outage)

Chain, all verified: **`/` fills → rsyslog can't write → Docker's `syslog` log driver blocks →
supervisord blocks → `valheim-logfilter` blocks in `pipe_write` → the server's stdout pipe
fills → the game's logging thread blocks *while holding Unity's log mutex* → 52 threads pile
onto that futex → total deadlock.** The container still reports **`Up`**.

Diagnosis technique:

```bash
# CPU time frozen? Use /proc ticks over >=20s. `ps time` rounds to seconds and lies at 6s.
cut -d' ' -f14,15 /proc/PID/stat
for t in /proc/PID/task/*/wchan; do cat $t; echo; done | sort | uniq -c
ls -l /proc/PID/fd/1 /proc/CONSUMER/fd/0     # same pipe inode == the chain
```

Signature: **1 thread in `pipe_write` + dozens in `futex_wait_queue`**. Corroboration:
`docker stop` burns the entire grace period then needs `SIGKILL`.

Fix, and its non-obvious catch:

```json
{ "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "live-restore": true }
```

**A container's `LogConfig` is fixed at creation.** Changing the daemon default only affects
*new* containers — existing ones need recreating. `live-restore: true` needs one bounce to
take effect, after which daemon restarts stop bouncing containers.

### Disk housekeeping traps

- **logrotate with no size cap.** Time-based `weekly` + `rotate 4` has no bound within a week.
  Use **`maxsize`** (keeps the schedule, adds an early trigger), not `size` (replaces it).
- **Never put a logrotate backup in `/etc/logrotate.d/`** — it's glob-included and parsed as a
  second config, producing duplicate-entry errors and a broken rotation.
- **Hidden journal namespaces.** `journalctl --vacuum-size` and `--disk-usage` don't see them;
  a `netdata` namespace held 192 MB invisibly. Use `--namespace=<name> --vacuum-size=`.
- **`atop` files must be matched on filename date, not mtime** — midnight rotation stamps
  yesterday's file with today's date, so `find -mtime` spares it.
- **TiKV `space_placeholder_file`** is pure reserved-space padding, regenerated on start.
  Deleting it under disk pressure is what TiKV itself does. (55 GB recovered.)
- **Ollama blobs are content-addressed sha256**, so identical filenames are byte-identical by
  construction — safe to hardlink across stores. Verify they aren't already hardlinks
  (distinct inodes, `links=1`).
- Container TZ here is `America/Chicago` (UTC-5) but `docker logs --since/--until` takes
  **UTC**. A wrong-timezone query returns an empty window and looks like silence.
- `BACKUPS_CRON="0 5 * * *"` — a 05:00 event correlating with an incident is probably backup.

### Profile sync semantics

The client builds each generation **from scratch** and swaps it in. It shipped **8** configs
while the client had **40** — so **32 mod configs were deleted every sync** and regenerated
from defaults, silently discarding every in-game setting. EpicLoot's welcome screen returning
after each sync was the visible symptom of a much larger loss.

Fixed by `preserveUnmanagedConfig`: shipped files win, unshipped files carry forward. The hash
covers shipped content only, so a mod rewriting its own config can't make the profile look
drifted. **Any config a mod needs at a non-default value must be added to `client-config/`.**

### Compose gotchas

- **Project-name collisions**: two different compose files sharing one `-p` name (seen with
  `gitea` and `ollama-docker`). Recreating one half makes the other's containers look like
  orphans. Omit `--remove-orphans`, or rename a project first.
- Containers with `RestartPolicy=no` do **not** come back after a daemon bounce and nothing
  will ever restart them.

---

## 5. Measured performance facts

Client: ~20-28 FPS against a 72-90 Hz headset, 115 client packages.

Startup cost, from `LoadTimeProfiler` per-plugin sections (exact, recovered retroactively):

| plugin | startup |
|---|---|
| Comfort Tweaks | **15.7 s** (3.4 Awake + 12.3 world) |
| SouthsilArmor | **11.9 s** (11.8 in `Awake`) |
| Jotunn | 7.7 s |
| Epic Loot | 6.2 s |
| More_World_Locations_AIO | 6.1 s |
| RavenwoodRestorations | 5.9 s |
| VHModpackFix | 5.4 s |

Two mods account for ~28 s of a ~3-minute world load.

Static VR scan of 116 packages: 577 findings, 69 flagged. **115 keyboard shortcuts across 28
packages** are unreachable in VR. Only **12** root-canvas findings, essentially all already
handled — the canvas class is closed; **input reach is the real gap**.

At 20-28 FPS, anything rebuilt per frame reads as flicker. AzuEPI's quick-slot bar was
measured destroying and recreating its icons **7-15 times/sec**; at 90 FPS that gap is 11 ms
and invisible, at 20 FPS it's ~50 ms and glaring. **Judder from compositor reprojection is
not the same thing as a UI defect** — measure before attributing.

---

## 6. Open questions

- Do native swings register? The velocity probe was broken until 2.1.72; never truly measured.
- CPU-bound or GPU-bound? Needs the render-scale sweep (frame time tracking pixel count = GPU).
- Is a standing crouch genuinely invisible, or does `crouchLatch` disagree with `crouchAnim`?
- Which of the 115 keyboard shortcuts are worth surfacing in the Misc radial menu?

### `grep` silently skips binaries - a fourth false-zero instrument

`grep` reported no matches for the literal string `Player` inside a 2.1 MB `assembly_valheim.dll`.
It refuses to search files it detects as binary and reports that as "no matches", not as "declined".
Every "X does not reference Y" conclusion drawn from grepping a managed assembly is worthless.
Use IL walking. Joins the HUD `dPos=0.000000`, the `GetVelocity` `params` null-bind and the
`catch { return 0f; }` swing reading in the fabricated-zero list.

### A published release is immutable - and copying an artifact into its directory does nothing

`seed-release` refuses to amend a published release, but the artifact upload happens *before* that
check, so a second attempt leaves the file on disk with **no row in `artifacts`**. Release 2.1.79
shipped with a `diag_plugin` file present in
`/var/lib/valheim-portal/artifacts/releases/<world>-vr-2.1.79/` and no artifact row referencing it,
which means the client would have installed the profile and runtime **without the plugin** - every
plugin fix silently absent, with a valid-looking release.

Verify releases against `select kind,name from artifacts where release_id=...`, never against
`ls` of the release directory. The fix is always a version bump; delete the orphan.

Root cause worth remembering: the glob was `valheim-vr-plugins-*.zip` but the stored artifact is
named `diag_plugin-valheim-vr-plugins-1.28.0.zip`. A glob that finds nothing is not evidence that
nothing exists.

### BepInEx config keys are human-readable strings, not identifiers

Mod authors bind keys with spaces and punctuation. The real names are
`Show Quick Slots on HUD` and `Number of Quick Slots` in section `3 - Quick Slots`
(AzuEPI), and `Fix Exploit - Attack Animation` in section `General` (GUID `Zen.ModLib`).
A report calling these `ShowQuickSlots` is paraphrasing, and a key written into the wrong section is
**silently inert** - it looks applied and changes nothing.

Resolve section + key from the `Bind` callsite, and note that wrapper helpers break naive scanning:
Azumatt's mods call a local `config(group, name, value, description)`, so the `ldstr` run precedes
`config`, not `Bind`. Match both.

### The physics rate is not the project value - SteamVR overwrites it every frame

`SteamVR_Render.Update` assigns `Time.fixedDeltaTime = 1f / hmd_DisplayFrequency` on every frame
because the shipped `SteamVR_Settings.asset` has `lockPhysicsUpdateRateToRenderFrequency: 1`.
Valheim's own 0.02 s (50 Hz) is only the fallback. At 20-28 FPS that is **3-5 complete Valheim
`FixedUpdate` cycles per rendered frame**, and none of the extra ones can improve hit detection: the
weapon collider is repositioned once per *rendered* frame (`poseUpdateMode: 8` = `OnPreCull`, guarded
to one update per frame) and consumed by physics at the start of the next.

A one-shot assignment cannot fix it. Restoring 0.02 requires a postfix that runs every frame.
Trade-off: `PhysicsEstimator`'s window is 8 **steps**, so it widens from ~80 ms to 160 ms.

Related trap: on Unity 6 the project fixed timestep is stored in `globalgamemanagers` as a rational
(`2822399 / 141120000`), not a float. Searching for the bytes of `0.02f` finds nothing and looks
like proof of a different value.

### `Attack.DoMeleeAttack` never executes in VR melee

VHVR's `Attack.Start` prefix (`CollisionPatches.cs:30-130`) calls its own inline `doMeleeAttack` and
`return false` at :129. All **11** installed mod patches on `Attack.DoMeleeAttack` - including 4
transpilers - are dead weight. EpicLoot backstab, ImpactfulSkills melee bonuses and ZenWorldSettings
PvP gating **silently do not apply to VR melee hits**. Not a cost problem; a correctness one.

### `MomentumScalesAttackDamage = false` silently deletes swings

VHVR default. With it false, `AttackTargetMeshCooldown` passes `overideMinAttackInterval = null`, and
a swing landing during the cooldown is dropped **entirely** - no damage, no haptic, no effect - for
0.40 s (dual axes) to 1.72 s (sledge). That reads as "combat ignores me", and it is a config value,
not a bug. `true` gives a 0.25 s minimum re-hit with damage scaled by
`min(speedFactor, 1 - remainingCooldown)`.

### Damage is purely networked, so a diagnostic exists

`Character::Damage` is 59 bytes: nothing but `InvokeRPC("RPC_Damage")`. `RPC_Damage` early-returns
unless `IsOwner`. With a remote dedicated server every hit confirmation costs a full RTT.
**Hit a tamed creature (client-owned ZDO) and then a wild one.** If tamed is crisp and wild is not,
the network dominates and no client-side tuning will help.

### Per-hit mod cost is noise - the useful negative result

The entire installed patch stack over one melee hit sums to ~0.05-0.2 ms against a 36-50 ms frame.
115 mods cannot be felt per-hit. What *is* felt is per-frame work, allocation churn, animation
reordering, and **synchronous logging**: BepInEx `Debug` on both sinks with an attached console turns
`log.Debug($"...")` inside `Character.RPC_Damage` and `Projectile.OnHit` patches into a blocking
console+disk write per hit. Turning Debug off removes the *write*, not the interpolation - the string
is still built at the callsite. Be precise about which half is fixed.

### Measured: the physics rate really is 72 Hz, and combat really does discard most swings

From a 2.1.77 session log (`PERF combat`, read directly off the client share):

```
swings=101 attackStarts=72 damageEvents=22 swallowed=90
threshold=1.50 fistGate=0.68 fixedDelta=13.9ms frameMs=42.1-82.9
swing->damage n=4 mean=693.9 p50=721.5 min=279.4 max=1089.8 ms framesMean=11.50 framesMax=19
swing->attackStart n=6 mean=564.3 min=26.9 max=931.1 ms
PERF frame fps=11.2-19.8 mean=50.6-89.0 p95=79-140 p99=114-201 max=273-301 ms
PERF compositor gpuMs=23.9-28.6 compositorGpuMs=0.01 reprojFlags=52 dropped=0
```

- `fixedDelta=13.9ms` = **1/72** exactly. The SteamVR `lockPhysicsUpdateRateToRenderFrequency`
  override is now **measured**, not inferred from IL. Valheim wants 20 ms, so a 42-83 ms frame
  runs 3-6 complete `FixedUpdate` cycles.
- **89 % of swings produce no damage** (90 swallowed of 101). This is the
  `MomentumScalesAttackDamage = false` cooldown drop, observed rather than argued.
- **`swing->damage` mean 694 ms is NOT code latency.** `swing->attackStart` has a **26.9 ms
  minimum**, which is one frame - the input path is fine. The mean is dominated by swings waiting
  out `AttackTargetMeshCooldown`. A probe that pairs a swing with the *next* damage event inherits
  the cooldown; state that whenever quoting it.
- `gpuMs` ~28 against a 50-89 ms frame ⇒ **CPU-bound, with a 28 ms GPU floor**. Even zero CPU cost
  caps at ~35 FPS. 72 Hz is unreachable without cutting GPU work (render scale).
- `reprojFlags=52` = `0x34`: async reprojection on, **3 frames of prediction**
  (`flags >> 4`). Samples with `36` (`0x24`) carry `dropped=1`.
- Own-instrument bug: `waitGetPosesMs` reports **negative** values. Do not trust that field.

### A delivered input is not a performed action

Every wrist-menu action fired - `misc zinput pulse '<name>'` appears eight times - and nothing
happened in game. `ZInput_GetButtonDown_Patch.Prefix` answers `true` once for a pulsed name and
its `Postfix` ORs rather than overwrites, so the value *does* reach the caller. The problem is
that nothing **asks**:

- `MapZoomIn` / `MapZoomOut` are polled only while the map is open.
- `Joy*` names are skipped entirely because `ZInput.IsGamepadEnabled()` is false in VR.
- Mod names need that mod polling every frame.

Prefer a direct API call: `Chat.SendText(Talker.Type, "/emote")` for emotes, `Minimap.m_largeZoom`
/ `m_smallZoom` selected by `m_mode` for zoom, `Player.StartGuardianPower()` for powers. Minimap
keeps **separate** zoom values for corner and full-screen views, so writing the wrong one is a
silent no-op.

### `head -N` on a `uniq -c | sort -rn` hides every once-only line

I concluded "no action callback ever fired" from a frequency-sorted log summary truncated at 40
rows. Install banners and one-shot action logs occur **once**, so they sort last and were cut. The
log had them all along. When the question is "did X ever happen", grep for X - never read it off a
ranked frequency table.

### Diagnostics upload is real but only fires when the launcher starts the game

`collectDiagnosticsAfterExit` waits on the Valheim PID and uploads on exit, and it works - but
`startDiagnosticsCollector` is called from exactly one place, `launch.go:110`, in the launcher's
own game-start path. Launch Valheim from Steam or SteamVR directly and no collector exists, so no
bundle is ever produced. There is no error, because nothing ran.

Reading the client share directly is strictly better:
`ValheimProfileSync/profiles/<World>--<profile>--<type>/active/BepInEx/LogOutput.log`.

### Releases are built by `profile-definition-builder`, and the payload carries the package list

The profile payload ZIP contains `profile-manifest.json` **plus** `config/`, and nothing else; the
package list lives in that manifest, so editing the source manifest changes nothing unless the
payload is rebuilt:

```
profile-definition-builder -source-manifest <profile-manifest.json> \
  -world W -profile P -client-type vr -audience player \
  -config-dir <MERGED cfg dir> -output <zip>
```

`-audience` is required and has no default. It is validated here but never written into the
archive: the built manifest carries only `schema`, `world`, `profile`, `client_type`, `packages`
and an optional `companion`, because an installed client rejects an unknown key outright. See
[release-format.md](release-format.md#the-definition-format-is-frozen).

`-config-dir` takes **one** directory, but a VR profile's configs live in **two**
(`client-config/` and `client-config-vr/`). Merge them, VR overlaying shared, or the build silently
omits `org.bepinex.plugins.valheimvrmod.cfg` - which is where `MomentumScalesAttackDamage` lives.
Hand-copying a previous payload and substituting files preserves the **old** package list.

### ComfortTweaks is not a "comfort tweak" mod - do not remove it for frame time

Its config covers the whole rested/teleport economy: `Rested per Comfort`, `Damage Modifier`,
`Run Speed Modifier`, `XP Multiplier`, `Added Max Carry Weight`, Health/Stamina/Eitr regen
percentages, a teleport tax (`Teleport Cost`, `Teleport Distance for Cost`,
`Block teleport if not rested enough`), group resting, potion cooldowns, custom conversion pieces,
and force-enabled seasonal build pieces.

The only per-frame case against it was a duplicate `AnimationSpeedManager` on
`CharacterAnimEvent.CustomFixedUpdate` - and **EpicLoot ships the same class**, so removing
ComfortTweaks leaves the mechanism running and loses all of the above. Its 15.7 s is load-time
only. It also offers its own mitigations: `Show Comfort Pieces List only in Shelter` and
`List update interval`.

### The Zen_ModLib attack-animation toggle cannot affect VR hit latency

`FixExploitAttackAnim` defers `ZSyncAnimation.SetBool` for 200 ms after a swing. In VR that is
**cosmetic**: VHVR's `Attack.Start` prefix returns false and applies damage inside its own
collision path (`CollisionPatches.cs:25-130`), so no animation event participates in the hit -
`Attack.OnAttackTrigger` never fires at all. Deferring an animation bool therefore cannot delay
damage. It was a flat-play assumption on my part; in VR the correct choice is to leave the exploit
fix enabled.

### Admin detection: `LocalPlayerIsAdminOrHost()` lies for numeric adminlist entries

Decompiled from `assembly_valheim` 0.221.12:

```
LocalPlayerIsAdminOrHost() => IsServer() || PlayerIsAdmin(GetLocalUser().UserId)
PlayerIsAdmin(id)          => GetAdminList().Contains(id.ToString())
GetAdminList()             => m_adminListForRpc        // pushed to clients by SendAdminList/RPC_AdminList
```

The data **is** on the client - the server sends its admin list over `RPC_AdminList`, and
`SendAdminList` writes `m_adminList.GetList()` verbatim. The defect is the comparison: an EXACT
string match between those entries and `PlatformUserID.ToString()`. A dedicated server configured
with bare numeric ids -- `ADMINLIST_IDS` holding raw 17-digit SteamID64 values -- stores bare digits,
while `ToString()` renders a platform-qualified form, so a real admin reads as a normal player.

Observed: `admin signals: ZNet=True ServerSync=False` followed by `admin=False`, for a player whose
SteamID is in the server's adminlist.

Fix: read `GetAdminList()` yourself and compare **digits only**, which matches either format. Log both
sides - `me=<digits> adminList=[...]` - because an admin check that silently returns false is
indistinguishable from a player who is not an admin.

Consequence beyond this plugin: **any mod using `LocalPlayerIsAdminOrHost()` will treat you as a
non-admin on a numeric-adminlist server.**

### Never gate a feature on an untrusted check with fail-closed default

The first version of the admin gate hid the entries when the check returned false, and "fail open if
nothing resolves" did not help - the vanilla check *did* resolve, and returned a wrong answer. Hiding
the admin console from its own admin is a worse failure than showing a button the server will refuse.
Gate on capability only when the signal is verified; otherwise show and let the authority decide.

### Resolution is the GPU lever, and `XRSettings.renderViewportScale` is inert here

The diagnostics probe reports a per-eye target of **1933x2066** - 4.0 MPix per eye, **8.0 MPix per
frame** - at 15-20 ms of GPU. Pixels dominate GPU cost, so scale is the largest single GPU lever.

`XRSettings.renderViewportScale` reads **0** and does nothing: Valheim renders through OpenVR, not
Unity's XR display subsystem. The property that works is Valve's
`SteamVR_Camera.sceneResolutionScale`. Any resolution setting written against `XRSettings` on this
stack is silently inert - which is exactly the "a code default is not a deployed value" trap in a
different costume.

### An instrument that writes to the screen needs the same discipline as a behaviour change

Two regressions in consecutive releases came from my own telemetry, not from the game:

- **MessageHud echo on every `Terminal.TryRunCommand`.** ServerDevcommands registers ~50 aliases at
  boot through that method, so every alias definition (`alias tool_area hammer from=<x>,<z>,<y>
  circle=...`) was painted centre-screen, surviving into the loading screen. Gate player-visible
  output on "the player asked for this"; keep the log echo unconditional, since it is free and it is
  what identified the bug.
- **`ProfilePluginUpdateCost`** Harmony-wraps `Update`/`LateUpdate`/`FixedUpdate` in every plugin
  assembly. It measured 3.26 ms/frame of `ValheimDiagnostics` and 0.93 ms of `SpawnProbe` - my own
  cost - while itself adding more. Measurement modes belong off by default and enabled for one
  session.

### Canvas adoption: three regressions, zero measured benefit

Converting a mod's screen-space canvas to VHVR's world-space GUI (`renderMode = WorldSpace`,
`worldCamera = guiCamera`, register in `_guiCanvases`) caused, in order: an AdminQoL window rendered
half off the panel; the same window parented onto a wrist; and the main menu drawn tiny in the
distance *and* duplicated on the wrist with general glitching.

Root cause of the clipping: a screen-space canvas keeps its **pixel** `RectTransform` - commonly
1920x1080 at scale 1 - which in world space is a quad hundreds of metres across, so the GUI camera
captures only the part inside its frustum. Copying geometry from a canvas VHVR set up itself is the
right idea, but the donor must be filtered: a hand/wrist-parented donor drags the window onto the
wrist, because copying geometry means copying the parent.

The layer sweep (`SweepUILayerOnAdoptedCanvases`) is worse - it re-layers **every** canvas VHVR knows
about, including the main menu's. Both are now shipped OFF. Re-introduce one canvas at a time with a
before/after geometry log, never as a default-on list.

### Per-mod frame cost, measured (115-package VR client)

```
6.4219 ms/frame  ServerDevcommands      <- "Show map coordinates" formats a position string per frame
5.2286 ms/frame  VNEI                   <- flatscreen item browser, unusable in VR
3.2621 ms/frame  ValheimDiagnostics     <- mine
0.9300 ms/frame  SpawnProbe             <- mine
0.4512 ms/frame  EpicLootVRFix
   ...22 plugins total, everything else under 0.25 ms
TOTAL 17.0 ms/frame against a ~55 ms frame
```

Removing VNEI and disabling the devcommands coordinate readout moved the client from **11-20 fps
(50-89 ms, GPU 24-29 ms)** to **23-27 fps (36-44 ms, GPU 15 ms)**. Note the caveat: frame rate varies
strongly with location and entity count, so two sessions are only comparable when the activity is.

Also: **`Show map coordinates` is a per-frame string format**, and its analogue exists in several
mods. Look for coordinate/timer/counter HUD readouts before blaming patch counts.

### Vanilla vs devcommands console commands

Verified by string-heap membership in `assembly_valheim` vs the mod assembly:

- **Vanilla** (survive removing ServerDevcommands): `god` `fly` `ghost` `nocost` `event` `find`
  `goto` `pos` `heal` `killall` `tame` `spawn` `save` `debugmode` `env` `tod` `players` `puke`
  `sleep` `removedrops`
- **ServerDevcommands only**: `calm` `repair` `resetdungeon` `tp`

Sanity check that the method discriminates: `debugmode` appears in vanilla **only**. `[INFERENCE]` -
a literal could appear in help text rather than a registration, so this is strong but not proof.

So devcommands is worth keeping for those four commands plus server-side execution, aliases and
parameter helpers - but its per-frame display work is pure cost and can be configured off.

### The console CAN work in VR, through VHVR's own handler

`InputManager.OnKeyboardClosed` only acts on typed text when `QuickAbstract.shouldStartChat` is set:

```csharp
if (text.StartsWith("/cmd")) Console.instance.TryRunCommand(text.Remove(0, 5));
else { Chat.instance.m_input.text = text; Chat.instance.InputText(); }
```

So `/cmd god` typed into VHVR's normal chat keyboard works with no mod code at all. Raising the
keyboard without setting that flag types into the field and never submits - which is why `god`
appeared to do nothing.

Two more traps in the same area:
- `Console.SetConsoleEnabledForThisSession()` takes **no arguments**; asking for a `(bool)` overload
  fails, cheats stay disabled, and commands are refused.
- Valheim's `god` prints nothing to `m_chatBuffer`, so "no output" is not evidence of failure. Read
  the state back with `Player.InGodMode()` / `InGhostMode()` / `IsDebugFlying()`.
- The console window's own output is unreadable in VR (screen-space pixel rect in world space).
  `MessageHud.ShowMessage(Center, ...)` is the surface that reliably renders; build its argument array
  from live `ParameterInfo`, because the trailing parameters have changed across game versions.

### An open text panel freezes VR locomotion, and that is wrong for VR

`Player.TakeInput()` is a chain of `IsVisible()` gates - `Chat.HasFocus`, `Console.IsVisible`,
`TextInput`, `Menu`, `StoreGui`, `InventoryGui`, `InFreeFly`, the barber GUI - and any one of them
returning true blocks **all** input.

Correct on flatscreen: the keyboard is typing. Actively harmful in VR: typing is on the SteamVR
overlay keyboard, locomotion is a thumbstick, and the button that closes the panel is on the wrist -
so a frozen player cannot walk to reach what they need. Override only the text surfaces; leave
inventory, menu and stores blocking.

### Which fixes are universal and which are VR-only

Universal - the mechanism is not VR-specific, so flat profiles benefit identically:
- `BepInEx.cfg`: Debug on both sinks plus an attached console costs a synchronous write per damage
  RPC on any client.
- `valheimoptimizer`: the `GC.Collect()` + `Resources.UnloadUnusedAssets()` hitch, and the global
  texture-mip churn that permanently downsamples every texture including UI atlases.
- Removing `AAABuildMenu`: the mod itself logs that SearsCatalog overhauls the same menu and it is
  standing down.

VR-only - the feature is genuinely useful on flat:
- Removing `VNEI` (mouse-driven item search is fine on flat).
- `server_devcommands` `Show map coordinates` (readable on a flat map).
- AzuEPI quick-slot hiding (quick slots work with a keyboard).
- `Zen.ModLib` `Fix Exploit - Attack Animation` - a real exploit fix on flat, and **only cosmetic in
  VR**, because VHVR's `Attack.Start` prefix applies damage itself so no animation event participates
  in the hit. Turning it off cannot reduce VR hit latency; it has been restored to `true`.

### Mod VR integration: five interaction shapes, not one problem

Treating "make mod X work in VR" as a single problem is why canvas adoption kept breaking things.
The scanner's class counts sort it:

| shape | machinery | scale |
|---|---|---|
| single action / toggle | one wrist entry | part of the 44 `C4` packages |
| action group | a wrist sub-page (paging already exists) | 5 packages with 7-8 shortcuts each |
| contextual | a `when:` predicate on the entry | container / build / crafting cases |
| real UI panel | canvas geometry work - the risky one | only **6** packages (`C1`) |
| passive | ship a config value, no UI at all | most of the 31 `C7` packages |

**Most of this is not a UI problem.** 44 packages need a button; 6 need a panel.

### Contextual entries need no new plumbing

VHVR already rebuilds the wrist strip on the events that change context - `Inventory.Changed`,
`Humanoid.UnequipItem` and `InventoryGui.OnSelectedItem` all call `refreshItems()` - and the entry
list is filtered on every rebuild. So a predicate evaluated at rebuild time is sufficient: open a
chest and container actions appear, close it and they go.

Usable predicates, all verified present:
- `InventoryGui.IsContainerOpen()` - instance method; VHVR itself gates on it
  (`ControlPatches.cs:983`, `VRGUI.cs:492`)
- `InventoryGui.IsVisible()`
- `Player.InPlaceMode()` (name has moved between versions - try the alternatives)
- `Player.IsDead()`

Design rule learned the hard way: an **unknown** predicate must show the entry and log a warning. A
missing button is the hardest failure to diagnose from inside a headset, and a predicate that
silently returns false is indistinguishable from a context that is genuinely inactive.

### These mods expose NO callable API - the keyboard pulse is the only route

Scanned `AzuAutoStore`, `Quick_Stack_Store_Sort_Trash_Restock`, `PlantEasily`,
`AzuExtendedPlayerInventory` and `AzuCraftyBoxes` for public static zero-argument action methods:
**none of them has any.** Their features are implemented inline in `Update()` behind
`KeyboardShortcut.IsPressed()`.

That makes `key:` the correct mechanism, and it is materially different from the `zinput:` failures:
`KeyboardShortcut.IsPressed()` reads `UnityEngine.Input.GetKeyDownInt`, which this plugin patches
directly, whereas the ZInput pulses were delivered to a name nothing polled.

Take the key values from the **live generated configs on the client**, never from mod defaults - the
player may have rebound them:

```
goldenrevolver.quick_stack_store.cfg   QuickStack=P  Restock=L  Sort=O  Trash=Delete
Azumatt.AzuAutoStore.cfg               Store Shortcut=Period
advize.PlantEasily.cfg                 ToggleAutoReplantKey=F6  EnableSnappingKey=F10
```

**Modifiers cannot be pulsed.** A one-frame answer to `GetKeyDown` cannot represent a HELD key, so
`EnableModKey`-style bindings and every "modifier" config are out of scope; only toggles work.
Chords are also fragile: if a mod tests its modifier with `GetKey` *before* the main key's
`GetKeyDown`, the hold set has not been populated yet. Prefer single-key bindings.

### Version strings do not sort as strings

`max(version)` over `2.1.9` and `2.1.31` returns `2.1.9`, so a "latest release" query ordered
lexically silently reports a stale release. Order by `published_at`. This produced a wrong picture of
which profiles were up to date until it was checked against artifact rows.

### Flat profiles need `-flat-companion` at BUILD time

`profile-definition-builder` refuses a flat profile without `-flat-companion` (or `-true-nonvr`);
passing the companion only to `seed-release` is not enough. Extract the companion artifact from the
profile's previous release and feed it to both.

### Offline Companions in VR (plugin 1.50.0, profiles 2.3.0)

The mod's AI ran in VR from the start; none of its UI did. Four surfaces were invisible:
`HC_StarterCompanionPanel` (pick-a-companion, fires once on first entry to a world),
`HC_CompanionHudPanelHost` (status beside the minimap), `HC_CompanionInteractPanel` (short Use),
`HC_CompanionRadialMenu` / `HC_RadialCanvas` (held Use).

**Input needed no work.** The mod reads `Use`/`JoyUse` through `ZInput.GetButton{,Down,Up}`, and VHVR
implements all three - `GetButton("Use")` maps to `valheim_Use.GetState()`, true for as long as the
trigger is held. Targeting uses vanilla `GetHoverObject`, which VHVR already drives from the hand
(`HandBasedInteractionPatches`). The mod's own config calls the radial *"hold while hovering companion"*.
So short trigger = inventory and hold trigger = radial were already wired; only the canvases were missing.

**Three bugs in this repo, not in the mod:**

1. `AdoptCanvasNames` shipped **empty**. The mechanism that converts mod canvases to VHVR world space
   was disabled in 2.1.91 after the *default-on* layer sweep duplicated the main menu, and never
   restored in its named form.
2. The default list used panel **class** names - `StarterCompanionPanel`, `ConfigPanel`,
   `HomeZonePanel`. Every GameObject is prefixed `HC_`, so three of six would never have matched.
3. Adoption was **one-shot per name**: `adopted.Add(name)` marked a name done forever. Companion panels
   are built on demand and the radial is *destroyed* on close (`[Radial] Despawn`), so a name adopted
   once is a fresh screen-space canvas the next time it appears.

`VRGuiBridge.EnsureAdopted` replaces `Adopt` on the timer: one `FindObjectsOfTypeAll<Canvas>` pass per
tick, skipping anything already in world space, so a name stays eligible all session. `AdoptPollSeconds`
defaults to 0.5.

Also fixed: the coroutine returned early when the name list was empty, which silently disabled the
UI-layer sweep and hotbar deactivation too.

**The HUD is parented, not adopted.** VHVR moves the minimap to a wrist canvas, so converting the
companion status panel in place leaves it where the *flat* minimap used to be. `CompanionWristHud`
parents `HC_CompanionHudPanelHost` into VHVR's `rightHudCanvas` - the same canvas VHVR parents its own
panel clones into. It is excluded from the adopt set (`ClaimNames`) so the two mechanisms cannot fight
over one RectTransform, and it bails immediately on Flat where `VRHud` does not exist.

`SweepUILayerOnAdoptedCanvases` stays **false**. Named adoption never broke anything; the sweep - which
reparents canvases this plugin does not own - is what shredded the main menu.

**Debug logging deliberately left off.** `[Radial] Show` and `[Interact] Gamepad hold` are Debug-level
and `LogLevels` excludes Debug, so their absence in a log proves nothing. Re-enabling it globally
reintroduces a measured regression: several mods call `log.Debug` with string interpolation inside
`Character.RPC_Damage` and `Projectile.OnHit`, and a console write is synchronous on the calling thread.
Verification uses this plugin's own Info-level `adopted canvas …` lines instead.
