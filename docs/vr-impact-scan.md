# VR impact scan and perf ingest

Tooling for the VR mod-onboarding pipeline. The process itself - stages, gates, and the
decision record - lives in [`mod-onboarding.md`](mod-onboarding.md). This file documents the
two tools only. Both live in [`tools/`](../tools) at the repository root.

| Stage | Tool | What it answers |
| --- | --- | --- |
| 1 static VR scan | `vr_impact_scan.py` | Will this mod be broken in VR, and where exactly? |
| 5 ingest and compare | `vr_perf_ingest.py` | What did it actually cost once installed? |

Both share `tools/vr_scan_common.py` (severity vocabulary, manifest parsing, join-key
normalisation, exit codes). They are deliberately separate programs: stage 1 needs
`dnfile` and the package zips, stage 5 needs neither and runs against a diagnostics bundle
collected on the player's machine, and each needs its own gate exit code.

Requirements: Python 3, stdlib, and `dnfile` (stage 1 only). Neither tool reads anything
else in this repository, so both run from any working directory once given their paths.

## Exit codes

Both tools use the same contract so a pipeline can gate on them:

| Code | Meaning |
| --- | --- |
| `0` | Nothing at or above the threshold. |
| `1` | Findings at or above the threshold. |
| `2` | The tool could not run (bad path, unreadable manifest). |

Stage 1's threshold is `--min-severity`. Stage 5's is "any mod in a VR-broken quadrant",
tuned by `--min-severity`, `--startup-ms` and `--frame-ms`.

---

## Stage 1 - `vr_impact_scan.py`

```
vr_impact_scan.py --packages <dir>
                  [--manifest <profile-manifest.json>]
                  [--json out.json]
                  [--min-severity info|low|medium|high]
                  [--package NAME]           # repeatable substring filter
                  [--vhvr-source VRGUI.cs]
                  [--vhvr-controls VRControls.cs]
                  [--adopt-list FILE]
                  [--cap N] [--quiet]
```

It opens every package zip, reads every `.dll` that is not a known dependency library,
parses the .NET metadata with `dnfile`, and walks the IL of every method body with its own
opcode walker, resolving `call` / `callvirt` / `newobj` / `ldstr` tokens against the
metadata tables. Nothing is written to the packages directory.

`--manifest` restricts the scan to the packages the profile actually pins, at the pinned
version. Without it, every zip in the cache is scanned, including stale versions.

`--vhvr-source` and `--vhvr-controls` re-parse the live VHVR source so the "already
handled" lists cannot silently rot; if a file is missing the tool warns on stderr and falls
back to a baked-in copy.

### The seven classes

#### C1 - screen-space canvas not converted to world space

VHVR converts a canvas to world space only if its GameObject name is in the hardcoded
`ADDITIONAL_GUI_CANVAS_NAMES` list (`VRGUI.cs` ~:60-79) or is the menu/password canvas
(~:757), and it only scans for canvases once, at startup (`ensureGuiCanvas` ~:747-779). A
mod that creates its own root `Canvas` therefore renders in screen space: invisible, or
pinned to one eye, or drawn at the wrong depth.

Discriminator: a call to `UnityEngine.Canvas::set_renderMode`. Nesting matters, not just
the call - a canvas parented under the vanilla `GuiRoot/GUI` is already handled, which is
exactly what Jotunn's `GUIManager.CreateCustomGUI` does. The tool grades it:

* **info, "already handled"** - the assembly uses Jotunn `GUIManager` custom-GUI symbols,
  or every recovered canvas name is in VHVR's list / your `--adopt-list`.
* **medium** - `set_renderMode` and a `Transform.SetParent` in the same method. The parent
  is a runtime value, so this is *not statically decidable*: it may be a correct nested
  canvas or a root canvas parented to another root.
* **high** - `set_renderMode` with no reparenting call in that method. Probably a root
  canvas. Candidate names recovered from the surrounding `ldstr` values are listed.

#### C2 - UI object never assigned the UI layer

`onGuiCanvasFound` (`VRGUI.cs:952-955`) sets `worldCamera` and `renderMode` but never the
layer, and the VHVR GUI camera is built with `cullingMask = 1 << LayerMask.NameToLayer("UI")`
(`VRGUI.cs:992`). A canvas can therefore be adopted and still invisible.

Reported only when C1 fired, and only when the whole assembly contains no
`GameObject::set_layer`, `LayerMask::NameToLayer` or `LayerMask::GetMask`.

#### C3 - custom ZInput action with no VR binding

VHVR maps a small set of ZInput names to SteamVR actions (`VRControls.cs` ~:876-887).
Anything else logs `Unmapped ZInput Key: <name>` **once** and is then added to
`ignoredZInputs` permanently (`VRControls.cs:521`, `initIgnoredZInputs` :986-996), so the
feature is silently dead for the rest of the session.

Three sub-checks:

* `ZInput::AddButton` and Jotunn's `InputManager::AddButton` - the button **name** string
  is recovered from the argument window. High severity.
* Action-name literals passed to a registration wrapper (`ButtonConfig.Name`,
  `ControlInputs.Create`, and similar). Mods rarely hand the literal straight to
  `AddButton`; this is how the real name is recovered when they do not. Medium confidence.
* `ZInput::GetButton*("name")` reads of an action VHVR never bound. **high** if the name is
  in `initIgnoredZInputs` (the read can never return true), **medium** otherwise.

#### C4 - keyboard-only hotkey / KeyboardShortcut config

VHVR patches `Input.GetKeyDownInt` / `Input.GetKeyInt` only to handle the text-input return
key (`TextInputPatches.cs:104,:111`). Nothing else routes a keyboard press from VR.

* BepInEx `ConfigEntry<KeyboardShortcut>` binds - the tool recovers the **section**, the
  **config key** and the **default keycode(s)**, following `Bind<KeyboardShortcut>` calls
  and also single-assembly wrapper helpers. The default comes from the nearest preceding
  `KeyboardShortcut::.ctor`, which is what makes loop-generated hotkeys recoverable.
  Section/key print as `<runtime>` when the mod builds them with string interpolation.
* Raw `Input.GetKeyDown` / `GetKey` / `GetKeyUp` polling, with the literal `KeyCode`s
  recovered where they are constants.

These strings are the most valuable output in the tool: they are what you paste into a VR
menu entry or a SteamVR binding.

#### C5 - mouse-dependent UI

VHVR patches `Input.get_mousePosition` (`UIPatches.cs:114-123`) and nothing else. It does
**not** patch `GetMouseButton*`, `mouseScrollDelta`, or `GetAxis("Mouse ScrollWheel")`.

`Input.mousePosition` is downgraded to info ("already handled"). Button, wheel and axis
reads are medium, as is implementing `IDragHandler` / `IScrollHandler` and friends, since
the VHVR software cursor does not emit pointer deltas.

#### C6 - hover/tooltip text naming keyboard or gamepad keys

Cosmetic but pervasive. Matches user-string literals against Valheim key-prompt markup
(`[<color=...>E</color>]`), TMP gamepad glyphs (`<sprite=...>`), `$KEY_` tokens, modifier
combos, click prompts and scroll-wheel prompts. Low severity, and openly a string
heuristic.

#### C7 - camera or quality-settings mutation

Writing `QualitySettings.masterTextureLimit`, `vSyncCount`, `lodBias`,
`Application.targetFrameRate`, `Screen.SetResolution` or `XRSettings.*` at runtime wrecks
VR frame pacing. Creating extra `Camera` objects doubles per-eye draw cost. High for the
frame-pacing setters, medium for camera property mutation and extra cameras.

### Recall and precision per class

Emitted verbatim into the `--json` payload as `class_recall`:

| Class | Character |
| --- | --- |
| C1 | high recall, medium precision - nesting is only decidable when the reparenting call sits in the same method |
| C2 | high recall, low precision - absence-of-symbol test across the whole assembly |
| C3 | high recall, high precision - direct `ZInput.AddButton` reference |
| C4 | high recall, high precision for KeyboardShortcut binds; medium precision for raw `Input.GetKey*` |
| C5 | high recall, low precision - mouse input is not always UI-bound |
| C6 | high recall, low precision - string heuristics over the user-string heap |
| C7 | high recall, high precision - direct property setter references |

False positives are acceptable and are labelled with a `confidence` level. Silent false
negatives are not: anything the tool cannot decide is reported at reduced confidence with
the reason, never dropped.

### Reading the output

```
stage=1-static-vr-scan packages=116 assemblies=116 skipped_dlls=1 flagged_packages=69 ...
severity high=255 medium=173 low=149 info=0
classes  C1=12 C2=3 C3=88 C4=178 C5=27 C6=149 C7=120
already_handled=12 findings downgraded to info because VHVR or one of our fixes covers them
```

* `already_handled` is the count of findings suppressed because VHVR's canvas list, VHVR's
  ZInput bindings, VHVR's `mousePosition` patch, or one of our shipped fixes already covers
  them. Run with `--min-severity info` to list them. They are **downgraded, not
  re-reported** - the tool tells you what is NEW.

Then a ranked package table (score = sum of severity weights, `info` 0 / `low` 1 /
`medium` 4 / `high` 10), then per-finding detail:

```
  C3/high   conf=high   AzuAutoStore.dll
      evidence: ZInput.AddButton("MyAction") default=F5
      method:   AzuAutoStore.Plugin::Awake
      note:     VHVR logs "Unmapped ZInput Key:" once then adds the name to ignoredZInputs ...
      hint:     map the ZInput action to a SteamVR action or a VR radial-menu entry ...
```

Every finding carries package, assembly, class, severity, confidence, the concrete symbol,
and the containing method, so a human can open the assembly and verify it.

Three summary lists close the report and are the handoff to remediation work:
**recovered ZInput button registrations**, **ZInput actions read but never bound in VR**,
and **recovered KeyboardShortcut config defaults**.

### What the tool CANNOT detect

* **Runtime-only behaviour.** Whether a code path ever executes, whether a canvas is
  actually shown, whether a hotkey is reachable in practice. Static analysis sees the
  possibility, not the occurrence. Stages 3-5 exist for this.
* **Reflection-based canvas creation.** `AddComponent(Type.GetType("UnityEngine.Canvas"))`,
  `gameObject.AddComponent(someType)` and anything routed through `AccessTools` /
  `MethodInfo.Invoke` are invisible: there is no `set_renderMode` token to find.
* **String-built member and action names.** Anything assembled with `string.Concat`,
  interpolation, `Localization.Localize`, or read from config. Recovered names print as
  `<computed>` or `<runtime>` rather than being guessed. Cross-check `Unmapped ZInput Key:`
  in the client log.
* **Obfuscated or packed assemblies.** Renamed metadata makes the symbol evidence
  meaningless; encrypted method bodies fail the IL walk and land in the skip list.
* **Native DLLs.** Correctly skipped as "not a managed assembly" (e.g. `winhttp.dll`).
* **Cross-assembly dataflow.** A name defined in assembly A and registered in assembly B is
  only recovered if both patterns happen to be in the same assembly.
* **Harmony patch effects.** The tool sees that a mod patches something, not what the patch
  does to VHVR's own patches. Ordering conflicts are a runtime concern.
* **Asset-bundle content.** Canvases and prefabs authored inside a `.assetbundle` never
  appear in IL at all. This is the largest structural blind spot.

---

## Stage 5 - `vr_perf_ingest.py`

```
vr_perf_ingest.py --bundle <diagnostics.zip | dir | LogOutput.log>
                  [--baseline <bundle>]
                  [--static <stage1.json>]
                  [--json out.json]
                  [--min-severity info|low|medium|high]   # what counts as VR-broken
                  [--startup-ms 1000] [--frame-ms 0.5]    # what counts as expensive
                  [--min-startup-ms 0] [--top 40]
```

Merges measured cost with the stage-1 static findings so each mod gets one dossier.
`--static` joins on the `BepInPlugin` name that stage 1 extracts from each assembly,
normalised, so `Epic Loot` and `EpicLoot-0.9.x` fold together.

### Where the numbers come from

**Startup cost per plugin.** Primary source is `LoadTimeProfiler/latest.log` inside the
bundle, which carries exact per-plugin sections: construction/Awake/OnEnable, `Start()`,
and scoped deep lobby attribution (exclusive Harmony callback time inside `ObjectDB.Awake`
and `ZNetScene.Awake`). The tool sums them into `startup_ms` and also reports each part.

Fallback is the delta between consecutive `Loading [<Name> <Version>]` lines in
`BepInEx/LogOutput.log`, which works retroactively on any timestamped log with no new
instrumentation. Three timestamp shapes are recognised, and console-wrapper re-emissions
are deduped so a line is never counted twice. BepInEx 5 writes `LogOutput.log` without
timestamps by default, so on most real bundles this path yields nothing and the header says
so. The header always names the source actually used.

**Steady-state cost per mod.** Parsed from our plugin's machine-readable `PERF` lines:

```
PERF frame fps=.. mean=.. p50=.. p95=.. p99=.. min=.. max=..
PERF plugin name=<assembly> msPerFrame=.. pct=.. calls=..
PERF vr perEyeW=.. perEyeH=.. mpixStereo=..
PERF compositor present=.. dropped=.. reprojFlags=.. gpuMs=.. compositorGpuMs=.. clientIntervalMs=..
PERF sweep scale=.. fps=.. mean=.. p95=..
```

**CPU vs GPU verdict** from the `PERF sweep` rows: frame time falling as render scale falls
means GPU-bound, flat means CPU-bound. With fewer than three usable sweep rows the tool
prints `insufficient data` rather than guessing.

### Reading the output

The dossier table ranks by measured `ms/frame`, then startup cost, then static severity,
and assigns each mod a quadrant:

| Quadrant | Action |
| --- | --- |
| expensive AND VR-broken | reject or remove candidate |
| cheap but VR-broken | fix candidate |
| expensive but VR-fine | frame-rate decision, not a VR decision |
| cheap and VR-fine | no action |

With `--baseline`, each row also carries `startup_delta_ms` and `frame_delta_ms` against
the pre-install bundle, and the header lists `new_plugins`.

### Limitations, stated plainly

* **`PERF plugin` figures are lower bounds.** They cover `Update` + `LateUpdate` +
  `FixedUpdate` only. A mod's Harmony patches on game methods, its coroutines and its GC
  pressure are all excluded. Never present them as total cost.
* **Startup deltas assume serial load order.** The BepInEx Chainloader is serial, so this
  holds - but it means one slow plugin cannot be separated from a stall that happened to
  land during its load window.
* **A/B through separate probe profiles is the only route to a true total-cost number.**
  Without a baseline, no cost can be attributed to the mod you just added.
* Bundles collected before the plugin emitted `PERF` lines have **no** steady-state data.
  The tool says so explicitly instead of reporting zeros; only startup cost is real there.

---

## Worked example

```sh
cd tools

# Stage 1 - static scan of everything the VR profile pins.
# <world-root> is the directory holding one subdirectory per world plus the shared
# profile store -- the same path tools/portal_paths.py resolves from VALHEIM_ROOT.
# Neither tool reads it itself.
PROFILE=<world-root>/profiles/vr
python3 vr_impact_scan.py \
  --packages "$PROFILE/manager-cache/packages" \
  --manifest "$PROFILE/profile-manifest.json" \
  --json /tmp/vrscan.json

# Gate a single candidate mod: exit 1 if it has any high-severity finding.
python3 vr_impact_scan.py --packages <dir> --package SomeNewMod --min-severity high

# Stage 5 - what did it cost, and is it broken as well as expensive?
python3 vr_perf_ingest.py \
  --bundle  /tmp/after/valheim-diagnostics.zip \
  --baseline /tmp/before/valheim-diagnostics.zip \
  --static  /tmp/vrscan.json \
  --json    /tmp/vrperf.json
```

## Background

The VHVR behaviour each class is derived from, with `file:line` citations, is documented in
[`valheim-vr-knowledge.md`](valheim-vr-knowledge.md).
