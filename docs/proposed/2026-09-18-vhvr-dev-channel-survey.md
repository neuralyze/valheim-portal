# ValheimVR dev channel survey — what we are missing, what we can retire

Read-only survey of `ValheimVR/ValheimVR_Dev` against what this fleet runs and what
`NeuralyzeVRFixes` patches. Nothing was installed, deployed or published. Every claim below is
marked MEASURED (with the instrument named) or INFERRED.

**Headline, up front:**

1. We do not install either Thunderstore channel on any VR edition. We compile VHVR ourselves
   from `brandonmousseau/vhvr-mod` and are pinned at upstream tag **v0.10.0**, which is the same
   source generation as **dev 0.10.0**. We are already on the dev channel's code, one build behind.
2. **Do not move to dev 0.10.2.** Its shipped DLL patches `ZInput.GetJoyLeftStick`, a method that
   does not exist in Valheim 1.0.12 — the version this fleet runs. That is a hard blocker, measured
   from both binaries.
3. The mount-steering premise in the brief is **five weeks stale**. Our mount steering was deleted
   from the repo on 2026-08-13 and upstream has owned saddle control since before our pin. There is
   nothing left to retire and nothing left to calibrate; the physical-stick question is answered
   below, and the answer is that it was never the relevant variable.
4. **`ShieldBlockAttach.cs` is retirable today** — upstream restored the deleted line on 2026-09-02
   and we have been shipping that restore since 2026-09-13. Our 424-line fix has been a no-op for
   four days.

---

## Premise correction: our mount steering is already gone

The brief describes mount steering as "the largest piece of work" with an unresolved stick mapping.
That is no longer the state of the repo.

MEASURED — `git log --all --oneline -S "lookDir" -- tools/vrfixes/` in
`repos/valheim-portal-main` returns exactly two commits, and the later one is the removal:

```
1569a4b  Thu Aug 13 02:12:13 2026 -0500
Public preparation: a shortcut button, no silent diagnostics, no horse controls

    The mount steering came out entirely: nine releases of it never produced a horse
    that answered the stick, and it left per-frame work and a logger behind.
```

MEASURED — `grep -rn "Sadle\|lookDir\|autoRun\|Quaternion.Euler" tools/vrfixes/*.cs` returns
**zero matches**. `git status --porcelain tools/vrfixes/` is clean on `main` at `fbd2c11`. There is
no heading writer, no self-calibrating channel detector and no `autoRun` clear in this tree.

What `MountControls.cs` (1215 lines) actually contains today, MEASURED by reading it:

| Class | Target | Job |
|---|---|---|
| `MountControls` | none (reflection reader) | per-frame-cached stick sampling while attached |
| `ShipAnchor`, `ShipStick` | none (reflection readers) | raw `valheim_Walk` axis, deadzoned |
| `ShipStick_TurnAxis` | `ZInput.GetJoyLeftStickX(bool)` postfix | replace VHVR's look-rotated X |
| `ShipStick_SpeedAxis` | `ZInput.GetJoyLeftStickY(bool)` postfix | replace VHVR's look-rotated Y |
| `DoodadRelease_Vanilla` | `Player.StopDoodadControl` | name the release path in the log |
| `DoodadRelease_Vhvr` | `ValheimVRMod.Utilities.MountedAttackUtils:UnmountIfJumping` | same, for VHVR's own release |
| `HelmWatch` | none | helm-request diagnostics |

This is a **helm/boat** fix plus release diagnostics. It is not a mount fix. I am declaring this
rather than answering a question about code that does not exist.

---

## Step 1 — the two channels, and what we actually install

### Version histories, side by side

MEASURED — `curl https://thunderstore.io/c/valheim/p/ValheimVR/ValheimVR{,_Dev}/versions/`, parsed
from the `package-versions__version` / `package-versions__upload-date` table rows. Download counts
are as rendered (the stable 0.9.2100 figure is comma-formatted in HTML and truncates to `2`; the
API reports 2284 for that version).

| `ValheimVR` (stable) | uploaded | | `ValheimVR_Dev` (dev) | uploaded |
|---|---|---|---|---|
| | | | 0.10.2 | **2026-09-17 20:14** |
| | | | 0.10.1 | 2026-09-12 03:48 |
| | | | 0.10.0 | 2026-09-11 06:16 |
| | | | 0.9.22001 | 2026-09-10 05:47 |
| **0.9.2100** | **2026-03-22 17:33** | | 0.9.21001 | 2026-04-03 19:23 |
| 0.9.2002 | 2025-04-06 01:54 | | 0.9.17006 | 2024-06-03 04:21 |
| 0.9.2001 | 2025-04-06 01:22 | | 0.9.17005 | 2024-05-30 16:45 |
| 0.9.2000 | 2025-04-06 01:13 | | 0.9.17004 | 2024-05-30 15:52 |
| 0.9.1901 | 2024-12-04 14:03 | | 0.9.17003 | 2024-05-30 15:49 |
| 0.9.19 | 2024-12-04 13:50 | | 0.9.17002 | 2024-05-30 15:43 |
| 0.9.17 | 2024-06-21 03:41 | | 0.9.17001 | 2024-05-30 15:31 |

- Stable: 7 versions, first 2024-06-21, newest **0.9.2100 (2026-03-22)** — ~6 months old.
- Dev: 11 versions, first 2024-05-30, newest **0.10.2 (2026-09-17, today)**.
- Cadence: dev is bursty, not continuous — six releases in three days in May 2024, then a
  ten-month gap, then four releases in seven days this month. Stable releases in pairs/triples a
  few days apart, roughly twice a year.
- MEASURED — both packages point at the same `website_url`,
  `https://github.com/brandonmousseau/vhvr-mod`. **The brief names `Brycey92/ValheimVRMod` as
  upstream; that is wrong for this package.** `deploy/upstream-sources.json` already records the
  correct repo (`"repo": "brandonmousseau/vhvr-mod"`).
- Dependency floors differ: dev 0.10.2 requires `denikson-BepInExPack_Valheim-5.4.2350`,
  stable 0.9.2100 requires `5.4.2202`.
- Descriptions, verbatim from the API: dev is *"specifically made for development test which might
  have a lot of unintended bugs"*; stable is *"Release Version"*.

### What the channels actually differ by

MEASURED — `BepInPlugin` version string extracted from each package's `ValheimVRMod.dll`
(attribute blob scan for `01 00 <len>org.bepinex.plugins.valheimvrmod <len>ValheimVR Mod <len>ver`):

| package | DLL bytes | md5 | embedded plugin version |
|---|---|---|---|
| stable 0.9.17 | 525312 | `45d9306e` | 0.9.17 |
| stable 0.9.19 / 0.9.1901 | 560128 | `501b4c01` (identical) | 0.9.19 |
| stable 0.9.2000 / 0.9.2001 / 0.9.2002 | 571904 | `9dd06298` (identical) | 0.9.20 |
| stable 0.9.2100 | 534016 | `cd6097b5` | 0.9.20 |
| dev 0.9.17006 | 523776 | `be1c9c0d` | 0.9.16 |
| dev 0.9.21001 | 602624 | `cc5010d6` | 0.9.20 |
| dev 0.9.22001 | 633856 | `581dfe8d` | 0.9.22 |
| dev 0.10.0 | 636928 | `bf145e30` | 0.10.0 |
| dev 0.10.1 | 640000 | `6656d3ab` | 0.10.0 |
| dev 0.10.2 | 668160 | `74e96a7f` | 0.10.0 |

Note that three stable *packages* ship the same DLL byte-for-byte (0.9.2000/2001/2002) — the
package version is not a code version. The channels diverge on game-version target: stable
0.9.2100 is the Ashlands/Bog Witch generation; dev 0.10.x is the Deep North / Valheim 1.0
generation. From the packages' own `CHANGELOG.md`:

- dev 0.10.2: *"a lot of fix, check github commit"* / *"fix for update 1.0.14 valheim version"*
- dev 0.10.1: *"New config option flatScreenMode {"auto", "true", "false"} replaces nonVrPlayer"*
- dev 0.10.0: *"a lot of fix, check github commit to v0.10 for details"*
- dev 0.9.22001: *"Support for 1.0 and some of its weapon"*
- stable 0.9.2100 top entry: *"Support for latest Valheim version (0.221.12)"*

### Which channel our VR editions install: **neither**

MEASURED — `md5sum /mnt/vps-sync/profiles/*/active/BepInEx/plugins/ValheimVRMod.dll` against the
DLL inside every package above. Four distinct binaries are installed; **none matches any
Thunderstore package**:

| profile | client_type | release_id | DLL md5 | size | plugin ver | corresponds to |
|---|---|---|---|---|---|---|
| `hrafnheim-vr` | vr | 2.5.118 | `69a2644f` | 600064 | 0.9.20 | our build `f879224e030c` ← `neuralyze/local 23f0ce4526cd` |
| `vangard-vr` | vr | 2.5.1 | `0beed51d` | 566784 | 0.9.20 | our build `298a0bc3ab68` (untagged) |
| `hrafnheim-vr-flat`, `-vr-flat-admin`, `hrafnheim-flatvr`, `vangard-flatvr` | flat | 1.0.8 etc. | `a1e38de9` | 567296 | 0.9.20 | our build `3fe2ce0874f2` (Flat companion) |
| `ulfsland-vr-flat`, `ulfsland-vr-flat-admin` | flat | 1.0.11 | `7f3ffad8` | 617984 | **0.10.0** | our build `dd38ede2f298` ← `neuralyze/v0.10.0 cb9e0f7dccb6` |
| `hrafnheim-flat` | flat | — | `d3419036` | 597504 | 0.9.20 | our build, unclassified in the registry |

Sizes and provenance cross-check against `deploy/upstream-sources.json:published_binaries`.
MEASURED — the whole VHVR build path is in-repo: `scripts/build-valheimvr.sh`,
`scripts/build-valheimvr-artifact.sh`, `docs/valheimvr-packaging.md`, against the source checkout at
`/media/big4/projects/game/valheim/Vangard/mods/custom/ValheimVR-latest`. That checkout is on branch
`neuralyze/v0.10.0` at `cb9e0f7` with seven commits on top of tag `v0.10.0`
(`git log --oneline v0.10.0..HEAD`):

```
cb9e0f7 Drop the frame-scaling patch Valheim 1.0 removed the target of
a67c633 Build with Mono on Linux, without the base asset bundle
9397d67 Resolve tracker roles by name so older SteamVR compiles
dff0324 Report which VR startup stage failed
c7a536d Stop registering the BodyPose action at runtime
e3b58b5 Allow build without release packaging
f8854f4 Skip dodge patches for non-VR player
```

**The one exception, and it is a real defect.** MEASURED — exactly one profile installs a
Thunderstore package:

```
/mnt/vps-sync/profiles/Vangard-Redesign--vangard-redesign-flat--flat/
  packages/ValheimVR-ValheimVR-0.9.2100.zip
  active/BepInEx/plugins/ValheimVR-ValheimVR/plugins/ValheimVRMod.dll
      md5 cd6097b5ae5d006e0afc11cffb407d4e   534016 bytes   2026-07-27 00:19
```

That md5 is byte-identical to the **stable** package's DLL. And its config reads
`nonVrPlayer = false` (line 350 of `org.bepinex.plugins.valheimvrmod.cfg`) on a profile whose
`state.json` says `client_type: flat` — i.e. VR input handling active for a desktop player, which is
precisely the failure `build-valheimvr-artifact.sh` was written to prevent and which the dodge guard
exists for. Every other VR/flat profile is correct: MEASURED, 11 of 11 have `nonVrPlayer = true` on
flat and `false` on vr. `vangard-redesign-flat` is the twelfth and it is wrong.

### The game version, which governs everything below

MEASURED — `monodis --output=/tmp/av.il assembly_valheim.dll` on the publicized reference set
`build/latest-1.0.12/`, then reading `Version..cctor`:

```
IL_0000:  ldc.i4.1
IL_0001:  ldc.i4.0
IL_0002:  ldc.i4.s 0x0c
IL_0004:  newobj instance void valuetype GameVersion::'.ctor'(int32, int32, int32)
IL_0009:  stsfld valuetype GameVersion Version::'<CurrentVersion>k__BackingField'
```

`Version.CurrentVersion = GameVersion(1, 0, 12)`. **This fleet is on Valheim 1.0.12.** Dev 0.10.2
is built for 1.0.14.

---

## Step 2 — the mount-steering question

### 2a. Which physical thumbstick is VHVR's "left" / "right" channel? ANSWERED

**Instrument:** the SteamVR binding manifests shipped inside each Thunderstore package
(`plugins/Valheim_Data/StreamingAssets/SteamVR/bindings_*.json`), parsed for every source whose
`inputs.position.output` is `walk` or `pitchandyaw`.

MEASURED, and identical in stable 0.9.2100, dev 0.10.0 and dev 0.10.2:

| controller profile | physical path | action |
|---|---|---|
| `bindings_oculus_touch.json` | `/user/hand/left/input/joystick` | `/actions/valheim/in/walk` |
| `bindings_oculus_touch.json` | `/user/hand/right/input/joystick` | `/actions/valheim/in/pitchandyaw` |
| `bindings_knuckles.json` | `/user/hand/left/input/thumbstick` | `/actions/valheim/in/walk` |
| `bindings_knuckles.json` | `/user/hand/right/input/thumbstick` | `/actions/valheim/in/pitchandyaw` |
| `bindings_vive_controller.json` | `/user/hand/left/input/trackpad` | `/actions/valheim/in/walk` |
| `bindings_vive_controller.json` | `/user/hand/right/input/trackpad` | `/actions/valheim/in/pitchandyaw` |

And MEASURED in `VRCore/UI/VRControls.cs` (both our pin and upstream master):
`walk = SteamVR_Actions.valheim_Walk` and `pitchAndYaw = SteamVR_Actions.valheim_PitchAndYaw`
(:896-897); `GetJoyLeftStickX/Y()` read `GetJoyLeftStickInput()` which reads `walk.axis` (:601-645);
`GetJoyRightStickX/Y()` read `combinedPitchAndYawX` and `-pitchAndYaw.axis.y` (:647-665).

**The API naming is not inverted.** VHVR's "left stick" channel is the PHYSICAL LEFT thumbstick and
its "right stick" channel is the PHYSICAL RIGHT thumbstick, in every shipped controller profile, in
both channels. Any mapping guessed the other way was guessing against the manifest.

The mapping was never the variable, and the operator's report is *consistent with correct wiring* —
see 2c.

### 2b. Is the forward axis inverted against the game's? YES, and it is a double negation

MEASURED, `VRControls.GetJoyLeftStickInput()`, identical at our pin and on master:

```csharp
if (!VHVRConfig.UseLookLocomotion() || ...) {
    var input = walk.axis;
    input.y = -input.y;      // <- the inversion
    return input;
}
...
return new Vector2(Vector3.Dot(velocity, playerTransform.right),
                   -Vector3.Dot(velocity, playerTransform.forward));   // <- same sign convention
```

Both branches return `y` negated relative to the raw SteamVR axis. This exactly reproduces the
measurement in the brief (`moveDir=(0.36,-0.93)` vs `apiLeft=(0.36,0.93)`): same `x`, flipped `y`.

MEASURED, why it is a *double* negation — `monodis` on `assembly_utils.dll` (1.0.12):

```
.method public static default float32 GetJoyLeftStickY ([opt] bool smooth)
  IL_0000: ldstr "StickL"
  IL_0006: call !!0 ZInput::ReadValueDef<Vector2>(string, bool)
  IL_000b: ldfld float32 Vector2::y
  IL_0010: neg            // <- the game negates too
```

So a sign-blind channel detector has no chance here: the correct channel and the wrong one differ
only by a sign that is applied twice on different sides of the boundary. A detector that scored
`1.86` on the right channel and `0.93` on a silent one and picked the silent one was reading a
magnitude through an odd number of negations. This is the documented reason `MountControls.cs:568`
negates deliberately rather than self-calibrating.

### 2c. Does the dev channel handle saddle control itself? YES — and it did before our pin

MEASURED — `Patches/ControlPatches.cs` in the source checkout at tag `v0.10.0`:

```csharp
[HarmonyPatch(typeof(Sadle), nameof(Sadle.ApplyControlls))]
class SadleControlPatch
{
    static void Prefix(ref Vector3 lookDir, ref bool block)
    {
        if (VHVRConfig.NonVrPlayer()) { return; }
        if (VHVRConfig.UseVrControls()) { block = Reining.turnInPlace; }
        lookDir =
            Reining.shouldOverrideSpeedOrDirection ?
            (Vector3)Reining.targetDirection :
            Valve.VR.InteractionSystem.Player.instance.hmdTransform.forward; // This makes the mounts try to follow the hmd eyedir
    }
}
```

and `Scripts/Reining.cs` (367 lines at our pin, 421 on master), which drives the saddle *directly*
every `Update()`:

```csharp
sadle.ApplyControlls(
    stickOutput, (Vector3)targetDirection, targetSpeed == Sadle.Speed.Run, autoRun: false, turnInPlace);
```

MEASURED — `git log --diff-filter=A -- ValheimVRMod/Scripts/Reining.cs` → `a10f24f`
(2026-07-25), and `git tag --contains a10f24f` includes `v0.10.0`, `shipped/valheimvrmod-dd38ede2f298`
and `shipped/valheimvrmod-2ee889227063`. **Upstream's reining system is in the binaries we
published on 2026-09-13.** It reached Ulfsland (`7f3ffad8`, plugin version 0.10.0) and is in the
VR runtime `2ee889227063`.

Upstream also patches `Player.StartDoodadControl` (postfix → `Reining.SetReinAttach()`) and
transpiles `Player.Update` and `Player.SetControls` to route `StopDoodadControl` through
`MountedAttackUtils.UnmountIfJumping`.

### 2d. Why the operator sees the right stick drive the horse and the left do nothing

This is the part worth keeping. MEASURED from the game's own IL,
`monodis assembly_valheim.dll` (1.0.12), `Sadle::ApplyControlls(moveDir, lookDir, run, autoRun, block)`
at `av.il:59417-59520`, with `Sadle/Speed` decoded at `av.il:60319` as
`Stop=0, Walk=1, Run=2, Turn=3, NoChange=4`:

- The direction sent over the `"Controls"` RPC is built from **`lookDir` alone** — `ldarg.2`
  (`lookDir`) → copy, `stfld y = 0`, `Normalize()`, store (IL_0045-IL_005b).
- `moveDir` (`ldarg.1`) is read **only for its `.z`**, and only to pick the speed enum:
  `run` → `Run`; `moveDir.z > 0.5` → `Walk`; `moveDir.z < -0.5` → `Stop`; `block` → `Turn`
  (IL_005c-IL_0094).
- Direction is written at all only when `(block || moveDir.z > 0.5) || run`.
- **`autoRun` (`ldarg.s 4`) is never read.** It does not appear once in the method body. Upstream
  independently passes `autoRun: false`, and the `block → Turn` mapping in the IL corroborates the
  argument indexing, since that is exactly what upstream's `block = Reining.turnInPlace` relies on.

Therefore:

- The **right** stick "drives" the horse because it is bound to `valheim_PitchAndYaw`, which turns
  the *view*. Upstream's `SadleControlPatch` prefix overwrites `lookDir` with
  `hmdTransform.forward` on every call, so rotating the view rotates the mount. That is the horse
  following your head, not the stick, and it is upstream's intended fallback behaviour —
  the source comment says so: *"This makes the mounts try to follow the hmd eyedir"*.
- The **left** stick does "nothing" for heading because heading never comes from `moveDir` at all;
  and its speed contribution is gated twice — by VHVR's own `IsAttached()` deadzone
  (`ZInput_GetJoyLeftStickY_Patch` zeroes `__result` unless `|joystick| >= 0.9`) and then by the
  game's strict `|moveDir.z| > 0.5`.
- A stick-channel fix on `moveDir` **cannot ever steer a mount**, whichever stick it reads. That is
  the measured reason nine releases of ours never produced a horse that answered the stick, and it
  is not a calibration problem. `1569a4b` was the right call.

The remaining honest non-answer: I cannot verify from here *whether reining works in the headset*.
That needs a ride. What I can say is that the mechanism is present in the shipped binary, its
controls are hand gestures on rendered reins (not a stick), and the only stick that has any effect
on a mount is the right one, by design.

**Was `MountControls.cs` retirable? Its mount half already was, and was retired.** The surviving
helm/boat half is not covered upstream — see Step 3.

---

## Step 3 — per patch surface: retire, keep, or conflict

Harmony-target overlap was computed mechanically: a paren-balancing scanner over every
`[HarmonyPatch(...)]` attribute in the upstream tree (163 distinct targets at our pin `v0.10.0`,
165 on master) plus hand-verified extraction of our manual `harmony.Patch(...)` call sites, since
`DirectActions.cs` and `ShieldBlockAttach.cs` use no attributes at all.

### `MountControls.cs` — **KEEP** (with a 1.0.13+ port required)

**Game behaviour corrected.** VHVR removes the keyboard route for vehicle speed — `"Forward"`,
`"Backward"`, `"AutoRun"`, `"Left"`, `"Right"` are all in `ignoredZInputs` — and rotates the left
stick through the look-locomotion heading. At a helm that means the player can turn but not move:
at the operator's measured 31° off heading with the stick fully forward, VHVR computes
`-0.95·cos(31°) = -0.81`, which is inside its own 0.9 deadzone, so it writes `0f`. Our postfixes
replace the rotated value with the raw `valheim_Walk` axis, deadzoned at 0.3 (turn) and 0.5 (speed).

**Harmony overlap check (explicit).**

| our target | upstream at our pin `v0.10.0` | upstream `master` / dev 0.10.2 |
|---|---|---|
| `ZInput.GetJoyLeftStickX(bool)` | **OVERLAP** — `ControlPatches.cs` `ZInput_GetJoyLeftStickX_Patch` | **GONE** |
| `ZInput.GetJoyLeftStickY(bool)` | **OVERLAP** — `ControlPatches.cs` `ZInput_GetJoyLeftStickY_Patch` | **GONE** |
| `Player.StopDoodadControl` | no upstream patch (upstream patches 30 other `Player` methods) | no upstream patch |
| `MountedAttackUtils:UnmountIfJumping` | no upstream patch (it is upstream's own method) | no upstream patch |

The overlap at our pin is handled and correct: `[HarmonyPriority(Priority.Last)]` +
`[HarmonyAfter("com.valheimvrmod.patches")]` — and note that id is VHVR's *Harmony* id from
`HarmonyPatcher.cs:7`, not its BepInPlugin GUID. Our postfixes assign rather than accumulate, so
the composed result is ours. **Not a conflict today.**

**But it silently breaks on the next game/VHVR generation.** MEASURED from the shipped binaries
(scan of `ValheimVRMod.dll` for the Harmony patch class names):

| package | `ZInput_GetJoyLeftStickX_Patch` | `..StickY_Patch` | `ZInput_GetJoyLeftStick_Patch` |
|---|---|---|---|
| stable 0.9.2100 | 1 | 1 | 0 |
| dev 0.10.0 | 1 | 1 | 0 |
| dev 0.10.1 | 1 | 1 | 0 |
| **dev 0.10.2** | **0** | **0** | **1** |

The commit is `dc5a228b "Update control patch for 1.0.14"` (2026-09-17), which deletes both wrapper
patches and adds one on the `Vector2` overload, with this comment:

> *"PlayerController#FixedUpdate reads the left stick through this Vector2 overload;
> GetJoyLeftStickX() and GetJoyLeftStickY() are only thin wrappers over it, so this single postfix
> reaches every caller. Patching the wrappers as well would apply the VR input twice for the menus
> that read them."*

On 1.0.12 that is not yet true. MEASURED — `assembly_utils.dll` (1.0.12) declares only
`GetJoyLeftStickX([opt] bool smooth)` and `GetJoyLeftStickY([opt] bool smooth)`, each reading
`ReadValueDef<Vector2>("StickL")` **directly**; there is no `GetJoyLeftStick()` in
`assembly_utils.dll` or `assembly_valheim.dll` at all. And MEASURED — `PlayerController::FixedUpdate`
(`av.il:55617`) calls `GetJoyLeftStickX(bool)` at `IL_010e` and `GetJoyLeftStickY(bool)` at
`IL_011f`, which are exactly our two hooks. Our fix is correct on 1.0.12 and would go **silently
dead** on a game version where `FixedUpdate` reads the `Vector2` instead — no exception, just a boat
that stops answering. Port `ShipStick_*` to `ZInput.GetJoyLeftStick` at the same time the fleet
moves off 1.0.12, not after.

### `DirectActions.cs` — **KEEP**, no overlap

**Game behaviour corrected.** Synthesised ZInput never reaches the game. Its own header records the
measurement: all eight wrist-menu actions provably fired (`misc zinput pulse '<name>'` ×8 in the
log) and nothing happened, because each name has a further gate — `MapZoomIn/Out` are polled only
while the map is open, `Joy*` names are skipped when `ZInput.IsGamepadEnabled()` is false, and mod
names need that mod polling every frame. So it calls the game directly: emotes, map zoom, chat and
console keyboards, guardian power, mount release, ship speed, height reset, panel close, command
sequences, and `AdoptAndShow(canvasName)` for a single named mod canvas.

**Harmony overlap check (explicit).** Our four manual patch sites, none of which upstream touches at
either revision:

| our target | upstream `v0.10.0` | upstream `master` |
|---|---|---|
| `Terminal.TryRunCommand(string,bool,bool)` prefix+postfix | none | none |
| `ShipControlls.Interact` postfix | none | none |
| `ShipControlls.RPC_RequestControl` prefix | none | none |
| `ShipControlls.RPC_RequestRespons` postfix | none | none |

**Is any of it fixed upstream?** Partly, and only in dev 0.10.2 — see Step 4. The
`ignoredZInputs` blockade itself is **unchanged**: MEASURED, `diff` of the sorted
`ignoredZInputs.Add("…")` lines between our pin and master is **empty** (54 `Add` calls, 51 distinct
names, both revisions). The keyboard-hotkey unreachability that makes 152 of 159 bindings
inaccessible is not addressed and there is nothing to retire here.

### `NeuralyzeVRFixes.cs` — **KEEP**, one benign overlap

**Game behaviour corrected**, per its own header, three fixes plus the plugin entrypoint:

1. `VRGUI.updateUiPanelScaleAndPosition` runs from `OnRenderObject`, which Unity calls once per
   rendering camera, with two unit errors compounding: `Time.unscaledDeltaTime` passed as
   `Vector3.MoveTowards`'s `maxDistanceDelta` (metres given seconds), and
   `GuiRecenterSpeed · Deg2Rad · dt` passed as `Slerp`/`Lerp`'s 0..1 fraction. At 20 fps with ten
   calls the probe measured the panel moving 0.77 m and 14.8° *within one frame*, so each eye
   renders the HUD in a different place. Our `PanelFrameGuard` prefix lets the first call per frame
   through and skips the rest.
2. Mod canvases are never converted to world space (see Step 4).
3. AzuEPI's quick-slot bar is redundant in VR but its visibility is `[Synced with Server]`, so it
   cannot be disabled for VR alone by configuration.

**Still broken upstream, MEASURED on `master`:** `OnRenderObject()` (VRGUI.cs:157) still calls
`updateUiPanelScaleAndPosition()` (:369) with **no frame guard anywhere** — `grep Time.frameCount`
in `VRGUI.cs` returns nothing at either revision — and the unit errors are intact at :452
(`angularRecenterStep = GuiRecenterSpeed * Mathf.Deg2Rad * Time.unscaledDeltaTime` fed to
`Vector3.Slerp`/`Quaternion.Lerp`) and :473/:478 (`Time.unscaledDeltaTime` and
`Time.unscaledDeltaTime * 2` fed to `Vector3.MoveTowards`). Nothing to retire.

**Harmony overlap check (explicit).** One attribute target and one manual site:

| our target | upstream `v0.10.0` | upstream `master` |
|---|---|---|
| `Hud.UpdateCrosshair` | **OVERLAP** — `BowAndFishingPatches.cs`, `HandBasedInteractionPatches.cs` | **OVERLAP**, same two files |
| `VRGUI.updateUiPanelScaleAndPosition` (private, via reflection) | it is upstream's own method | unchanged |

The `Hud.UpdateCrosshair` overlap is not a collision: three independent patches on one method
compose normally, and the `updateUiPanelScaleAndPosition` prefix is a deliberate rate-limiter on an
upstream private method — which is a real fragility (an upstream rename makes it inert; the code
logs `panel guard inert` rather than failing silently, which is the right shape) but not a conflict.

### `ShieldBlockAttach.cs` — **RETIRE**

**Game behaviour corrected.** Upstream commit `666124e6` *"Migrate to cache weapon collision using
item hash instead of object name"* (2026-05-08) deleted
`meshFilter.gameObject.AddComponent<ShieldBlock>().itemName = ___m_leftItem;` whole, because the only
consumer of the removed `___m_leftItem` parameter was the assignment tail of that statement. With no
`ShieldBlock` component, shields neither block nor parry in VR.

**Upstream fixed it. Quoted line, from the source checkout at our own pin and from master:**

```
ValheimVRMod/Patches/EquipPatches.cs:498   (tag v0.10.0)
ValheimVRMod/Patches/EquipPatches.cs:518   (master)
                case EquipType.Shield:
                    meshFilter.gameObject.AddComponent<ShieldBlock>();
```

MEASURED — `git log --oneline -S "AddComponent<ShieldBlock>" -- ValheimVRMod/Patches/EquipPatches.cs`
returns `4e1d9da "Restore shield block patch"`, dated **2026-09-02**. That commit is an ancestor of
tag `v0.10.0`, which is the base of `neuralyze/v0.10.0 cb9e0f7`, which is the commit
`dd38ede2f298` and `2ee889227063` were built from and published on **2026-09-13**.

So the restore has been live on this fleet for four days. The file's own header is now factually
stale — it asserts *"Master is still broken today (compare 50d333d..master is 'identical',
ahead_by 0)"*, which was true against the old base `50d333d` and is not true against `v0.10.0`.

**Harmony overlap check (explicit).** `VisEquipment.SetLeftHandEquipped(int,int)` — **OVERLAP**, with
upstream's `EquipPatches.cs`, at both revisions. This would be the dangerous case, except that
MEASURED at `ShieldBlockAttach.cs:258` the attach is guarded:

```csharp
if (host.GetComponent(_shieldBlockType) != null) { Skip("alreadyPresent"); return; }
```

so it no-ops rather than double-attaching. Confirmed benign in operation too: `RetryPending()` and
`ReassertInstance()` are called only from inside that postfix (:222-223), never per-frame.

**Verdict: RETIRE.** It is dead weight, not a live collision — 424 lines, a Harmony postfix on a
hot equip path, a reflection resolve of five `VHVRConfig` predicates, a `RestoreShieldBlock` config
option and a `SHIELD block attach armed` log line that now misinforms whoever reads it. Retiring it
is safe and should be done on the next `NeuralyzeVRFixes` release. `23d9478 "Retire the shield probe
now that the headset says shields block"` already removed the diagnostic half; this is the other half.

### Summary table

| file | verdict | basis |
|---|---|---|
| `MountControls.cs` | **KEEP** | helm fix, no upstream equivalent; mount half already retired at `1569a4b`; needs porting to `ZInput.GetJoyLeftStick` before the fleet leaves 1.0.12 |
| `DirectActions.cs` | **KEEP** | zero Harmony overlap; `ignoredZInputs` byte-identical upstream |
| `NeuralyzeVRFixes.cs` | **KEEP** | panel-per-camera bug and both unit errors intact on `master`; `Hud.UpdateCrosshair` overlap composes |
| `ShieldBlockAttach.cs` | **RETIRE** | `4e1d9da "Restore shield block patch"` (2026-09-02), shipped by us 2026-09-13; guarded no-op since |

No CONFLICT verdicts. Two overlaps exist (`ZInput.GetJoyLeftStickX/Y` at our pin,
`VisEquipment.SetLeftHandEquipped`) and both are explicitly ordered or guarded in our source.

---

## Step 4 — what we are missing, prioritised against measured defects

The upstream delta from our pin to master is **64 commits, 50 files** (MEASURED, GitHub
`compare/v0.10.0...master`, `ahead_by: 64`, `behind_by: 0`). Dev 0.10.2's shipped DLL was checked
directly for each feature below, by scanning for its distinguishing type and literal names:

| string in `ValheimVRMod.dll` | stable 0.9.2100 | dev 0.10.0 | dev 0.10.1 | dev 0.10.2 |
|---|---|---|---|---|
| `nonVrPlayer` | present | present | **gone** | gone |
| `flatScreenMode` | — | — | **present** | present |
| `LaserPointerChords` | — | — | — | **present** |
| `AddMapPin` / `SplitStack` / `DiscardItem` / `MiddleClick` | — | — | — | **present** |
| `HEAD_REIN_ATTACHES` | — | — | — | **present** |
| `VRCinematicScreen` | — | — | — | **present** |
| `ZInput_GetJoyLeftStick_Patch` | — | — | — | **present** |

### P1 — Four new bindable SteamVR actions and a chord system (dev 0.10.2)

Directly attacks the **152-of-159 unreachable bindings** defect.

MEASURED — `actions.json` action counts: stable 0.9.2100 = 41, dev 0.10.0 = 42 (adds
`/actions/Valheim/in/BodyPose`), dev 0.10.2 = **46**, adding:

```
/actions/LaserPointers/in/AddMapPin
/actions/LaserPointers/in/DiscardItem
/actions/LaserPointers/in/MiddleClick
/actions/LaserPointers/in/SplitStack
```

**What it does**, from the new `VRCore/UI/LaserPointerChords.cs` (234 lines, added by
`67862043 "Move map pin, ping to their own actions and add new action for favoriting building
pieces"`, `12227105 "Restore discard item and split item actions"`, `60284c9e "Consolidate middle
button actions"`), quoting its header:

> *"AddMapPin adds a pin on the large map... DiscardItem moves the inventory item under the pointer
> to the open container (or back from it), or drops it when no container is open... SplitStack splits
> the inventory item stack under the pointer... MiddleClick is sent to the GUI as a middle mouse
> button (see VRGUI), which vanilla uses to ping on the map, favorite a build piece and delete a
> favorite category."*

and `d2fff75d "Make splitStack action binding mandatory since it has no workaround in VR"` — an
upstream maintainer independently reaching the same conclusion we did about VR reachability.

**How it would be enabled:** it is not a config switch. These are SteamVR actions, so they arrive
with the mod's `actions.json` + `bindings_*.json` and become rebindable in the SteamVR UI. Adopting
them means adopting dev 0.10.2's whole binding set, i.e. adopting dev 0.10.2.

**Risk: currently blocking.** Same package carries the 1.0.14 control patch. See Step 5.

### P2 — `flatScreenMode {auto,true,false}` replacing `nonVrPlayer` (dev 0.10.1)

This one is **a hazard to us before it is a feature**, and it is the single most actionable finding
for the build pipeline.

MEASURED — `scripts/build-valheimvr-artifact.sh`:

```sh
118:  grep -Eq 'nonVrPlayer[[:space:]]*=[[:space:]]*true' "$config" || {
119:      echo "Template is not a Flat ValheimVR companion: nonVrPlayer must be true." >&2
126:  if [ -f "$config" ] && grep -Eq 'nonVrPlayer[[:space:]]*=[[:space:]]*true' "$config"; then
127:      echo "Template is a Flat companion, not a VR release: nonVrPlayer must not be true." >&2
```

Against a dev-0.10.1+ template these two gates behave asymmetrically:

- `--client-type flat` **fails closed** — the key is gone, the grep fails, the build aborts. Noisy,
  safe.
- `--client-type vr` **fails open** — a *flat* template no longer contains `nonVrPlayer = true`, so
  the guard at :126 does not trip and a Flat companion template would be accepted as a VR release.
  That is exactly the confusion that already happened once: `upstream-sources.json` records the
  first 2026-09-13 companion attempt producing the VR binary `2ee889227063` instead, *"caught before
  publishing because the companion had historically been a different size"*. Size is the only thing
  that caught it then, and here it would not differ.

Good news for the rest of the stack: `VHVRConfig.NonVrPlayer()` still **exists** on master
(`VHVRConfig.cs:1460`), now delegating to a cached `ResolveFlatScreenMode()`. Our six carried
`HarmonyPrepare` guards (`ControlPatches.cs:1332`, `:1389`) call that method and survive unchanged.
Only the **config key name and the build script's greps** break.

**What the feature buys us** (`8ec621c9 "Add option to automatically choose VR vs flatscreen mode
based whether SteamVR is running"`): `auto` resolves by looking for the SteamVR processes —
deliberately *not* by asking OpenVR, since *"every OpenVR entry point that can answer this would
launch SteamVR itself and therefore always report it as running"*. Risk if adopted naively: a VR
player who launches before SteamVR is up silently gets a flatscreen session.

**Recommendation:** when the pipeline moves past v0.10.0, rewrite both greps to accept either key
(`nonVrPlayer\s*=\s*true` **or** `flatScreenMode\s*=\s*true`) and, importantly, make the
`--client-type vr` gate assert the *positive* VR condition rather than the absence of the flat one.
Do this **before** the version bump, not with it. This is small, in-repo, and does not need dev
0.10.2 — hand it to whoever owns the next pipeline change.

### P3 — Reining polish (4 commits, dev 0.10.2)

Relevant only because mounts are the thing nine of our releases failed at. MEASURED,
`diff Reining.cs` pin→master, 121 added lines:

- `HEAD_REIN_ATTACHES` — per-mount head-bone rein anchors for `$enemy_lox`, `$enemy_asksvin`,
  `$enemy_moose`, `$spiritcaller_moose`, with the rig axis conventions documented per mount
  (`02237e45 "Attach rein to head bones"`, `912f750e "Tune rein attach"`).
- Rein rendering moved `Update` → `LateUpdate`, *"since they can follow its head"* — i.e. drawn
  after the animator has posed the mount, which fixes a one-frame lag.
- `639bdc17 "Use hip tracking (if available) to detect leaning and cue galloping when reining"`,
  and `MIN_LEANING_DISTANCE_TO_START_GALLOPPING` retuned 0.4 → 0.75.
- `sadle.m_monsterAI.m_character` → `sadle.m_character` (fewer hops, one less null risk).

**Risk:** low in itself; unavailable without dev 0.10.2.

### P4 — GUI / cinematics work (dev 0.10.2)

New `VRCore/UI/VRCinematicScreen.cs` (186 lines), `VRGUI.cs` +119, `SoftwareCursor.cs` +41. Commits:
`7d1f61b3 "Initialize VR before intro cinematics and display the cinematics on VRGUI"`,
`b9832eca "Move in-game cinematics and NPC text VRGUI"`,
`a9a4f480 "GUI changes: display intro cinematics on VRGUI and fix displayed cursor position"`,
`ec9b4a75 "Disabled duplicated handling of mouse events by vanilla game"`.

That last one is worth reading if anyone has seen phantom inventory drops in flatscreen mode — its
new comment explains that the vanilla input module also handled mouse input at the hardware cursor
position locked to screen centre, so *"clicks land on the inventory's drop outside area, so an item
picked up from the inventory would immediately get dropped"*.

### NOT fixed upstream — custom 2D panels still do not appear in VR

This is the one measured defect where the dev channel offers **nothing**, and it is worth stating
plainly so nobody waits for it.

MEASURED — `VRGUI.ensureGuiCanvas()` still matches canvases by name against four constants plus a
hardcoded `ADDITIONAL_GUI_CANVAS_NAMES` allowlist, and `diff` of that array between our pin and
master is **empty**. Its 19 entries are all vanilla canvases:

```
Chat, Connecting, EnemyHud, TextViewer, JoinCodeOverlay, Store_Screen, ClosedCaptions,
Inventory_screen, Menu, MiniMap, TopLeftMessage, TextInput, HudMessage, ConnectionPanel,
Tutorial, BarberGui, Scaled 3D Viewport
```

No mod canvas is on it, there is still no registration API (`VRGUI` is internal with private
fields), and `hasFoundGuiCanvas()` still short-circuits the scan forever once the vanilla set is
found. `DirectActions.AdoptAndShow` remains the only route, and the reason `AAABuildMenu` is
excluded for VR stands. **Nothing to adopt; keep ours.**

### NOT fixed upstream — per-frame name-based lookup cost

No commit in the 64 addresses it, and the pattern is unchanged: canvas discovery is still
`GameObject.FindObjectsOfType<Canvas>(includeInactive: true)`, and the panel update still runs once
per rendering camera with no frame guard. The one performance item in the wider changelog is
0.9.21's *"Added option to hide damage text to increase performance"*, which we already have.
Our `TypeCache`, the `AccessTools.TypeByName` discipline in `DirectActions.Refs`, and
`PanelFrameGuard` remain the only mitigations. **Nothing to adopt.**

---

## Step 5 — recommendation

**Stay on stable-equivalent. Do not put dev 0.10.2 on the operator's headset.** To be explicit, since
the brief asked for it plainly: a dev channel on a daily-driver headset is a real risk, and in this
case it is not even a judgement call — it is measurably broken against the game we run.

The blocking measurement: dev 0.10.2's `ValheimVRMod.dll` contains `ZInput_GetJoyLeftStick_Patch`
and zero `ZInput_GetJoyLeftStickX/Y_Patch`, while Valheim 1.0.12's `assembly_utils.dll` declares no
`GetJoyLeftStick` method at all. `[HarmonyPatch(typeof(ZInput), nameof(ZInput.GetJoyLeftStick))]`
against a missing method throws during `PatchAll`, taking the whole `ControlPatches` batch with it —
which is every ZInput bridge, the saddle prefix, the dodge patches and the build controls. Left-stick
locomotion would be the *least* of it.

Note also that we are not really choosing between the two channels: we are already on dev-generation
code (tag `v0.10.0` = dev 0.10.0), one dev build behind, with seven local commits we would have to
rebase each time. "Moving to dev" for us means bumping the pin, not switching a package source.

Ordered list:

1. **Retire `ShieldBlockAttach.cs`** — safe now, independent of any version bump. Upstream's
   `4e1d9da` has been in our shipped binaries since 2026-09-13 and our fix has been a guarded no-op
   since. Removes 424 lines, a hot-path postfix, a config option and a misleading log line.
   *Hand to `InventoryMod` or whoever owns the next `NeuralyzeVRFixes` release; do not ship it
   during tonight's window.*
2. **Fix the two `nonVrPlayer` greps in `scripts/build-valheimvr-artifact.sh`** — accept either key,
   and make the `--client-type vr` gate assert the positive VR condition instead of the absence of
   the flat one. Do this *before* any version bump. The flat gate fails closed; the VR gate fails
   open, and size was the only thing that caught the equivalent mistake on 2026-09-13.
3. **Correct the two stale records this survey found**, both cheap:
   - `ShieldBlockAttach.cs:52` still claims master is broken. It is not, since `4e1d9da`.
   - `deploy/upstream-sources.json` is right about the repo (`brandonmousseau/vhvr-mod`); the
     working brief that named `Brycey92/ValheimVRMod` is wrong, and `Frackn` does not appear
     anywhere in the package metadata. Worth not propagating.
4. **Fix `vangard-redesign-flat`** — it is the only profile running a Thunderstore VHVR binary
   (stable 0.9.2100, md5 `cd6097b5`, installed 2026-07-27) and it has `nonVrPlayer = false` on a
   `client_type: flat` profile. That is a desktop player running VR input handling and VR dodging:
   the exact failure the dodge guard and the build script exist to prevent. Either move it to the
   Flat companion artifact or set the key. *Measured, and outside this survey's remit to change.*
5. **Leave `MountControls.cs`, `DirectActions.cs` and `NeuralyzeVRFixes.cs` alone.** All three
   correct defects that are present and unchanged on upstream `master`. Do not reopen mount
   steering: the game's own IL says heading comes from `lookDir` and nothing else, so no stick-side
   fix can work, and upstream owns saddle control through `Reining` in the binary we already ship.
6. **Do not bump the VHVR pin past `v0.10.0` until the fleet is on Valheim 1.0.13+.** When that
   happens, three things must move in the same change, or the boat quietly stops working:
   port `ShipStick_TurnAxis`/`ShipStick_SpeedAxis` to `ZInput.GetJoyLeftStick(ref Vector2)`;
   update both `build-valheimvr-artifact.sh` greps for `flatScreenMode`; rebase the seven
   `neuralyze/v0.10.0` commits and re-verify the `HarmonyPrepare` guards still resolve
   `VHVRConfig.NonVrPlayer()` (they do on master today).
7. **Then, and only then, take dev 0.10.2's binding work** — `LaserPointerChords` plus the four new
   `LaserPointers` actions (`AddMapPin`, `DiscardItem`, `SplitStack`, `MiddleClick`). It is the only
   upstream work in this survey that moves the 152-of-159 number, and it arrives with the SteamVR
   binding manifests rather than as a config switch.

## Instruments used

- Thunderstore experimental API and the rendered `/versions/` pages, for both channels.
- All 13 package zips downloaded to `/tmp/vhvr_pkgs` and opened: `CHANGELOG.md`, `manifest.json`,
  `actions.json`, `bindings_*.json`, and `plugins/ValheimVRMod.dll`.
- `BepInPlugin` version strings read from attribute blobs; Harmony patch-class names and feature
  literals located by byte scan (UTF-8 for attribute blobs, UTF-16LE checked alongside).
- `monodis` on `assembly_valheim.dll` and `assembly_utils.dll` from the publicized 1.0.12 reference
  set — 580,392 and 56,121 lines of IL respectively. It works fine on the game's assemblies; the
  ILRepack problem in the brief applies to `ValheimVRMod.dll`, which is why every VHVR-side claim
  here comes from source or from a byte scan, never from `monodis`.
- The VHVR source checkout at `/media/big4/.../ValheimVR-latest` (branch `neuralyze/v0.10.0`,
  `cb9e0f7`) and `brandonmousseau/vhvr-mod` `master` (`3e3cf2df`, 2026-09-18) fetched as a tarball.
- A paren-balancing `[HarmonyPatch]` scanner over both upstream revisions (163 and 165 distinct
  targets) plus hand-verified extraction of our own manual `harmony.Patch` sites.
- `/mnt/vps-sync/profiles/` read-only: `state.json`, `org.bepinex.plugins.valheimvrmod.cfg`,
  `packages/`, and `md5sum` of all 10 installed `ValheimVRMod.dll` copies — the full installed VR
  plugin set across all 5 worlds, not a sample.
- `git log`/`git show`/`git tag --contains` in both repositories; no writes, no fetch, no commit.

All load-bearing byte scans were run as standalone scripts in single `bash` invocations
(`/tmp/vhvrdev_*.py`), never through the shared Python eval kernel, and every global is prefixed
`vhvrdev_`.
