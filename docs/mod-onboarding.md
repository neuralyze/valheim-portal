# Mod onboarding

> Background on *why* each check exists — VHVR internals, Valheim IL facts, and the
> instrument-discipline lessons behind the gates — is in
> [`valheim-vr-knowledge.md`](valheim-vr-knowledge.md). Read that before changing this
> process. The two scanners the stages below invoke are documented in
> [`vr-impact-scan.md`](vr-impact-scan.md).

How a new mod gets into a VR profile. Eight stages, three gates, one recorded decision.

This exists because the failure mode is not "the mod crashes" — it is **the mod installs
cleanly, looks fine, and silently does nothing in VR**, or quietly costs frames nobody
attributes to it. Both happened repeatedly on a live world before this process existed. Two
concrete examples, both diagnosed from code rather than guessed at:

- A mod registers its own ZInput button. VHVR logs `Unmapped ZInput Key: <name>` **once**,
  then adds the name to a permanent ignore set (`VRControls.cs:521`, `initIgnoredZInputs`
  `:986-996`). The feature is dead for the rest of the session with no error, no warning
  after the first frame, and no way back. 22 distinct actions were in this state on a live
  client, including map zoom.
- A mod creates its own root `Canvas`. VHVR converts only canvases whose GameObject name is
  in a hardcoded list (`VRGUI.cs:60-79`) and scans once at startup (`:747-779`). Anything
  else renders in screen space. Worse, `onGuiCanvasFound` sets `worldCamera` and
  `renderMode` but never the **layer** (`:952-955`), while the GUI camera culls to
  `1 << UI` (`:992`) — so a canvas can be adopted and still be invisible.

Neither produces an error. That is the whole problem: **absence of a crash is not evidence
of working.**

---

## Stages

### 1. Static VR scan — before installing anything

```
tools/vr_impact_scan.py --packages <manager-cache/packages> --package <NewMod> --json out.json
```

Classifies the mod against the known VR-incompatibility classes and, critically, **recovers
the strings you need for remediation**: custom ZInput button names and `KeyboardShortcut`
defaults. Findings already covered by VHVR's name list or our plugin's adopt list are
downgraded, so the output is what is *new*.

Exit code is non-zero when findings meet the severity threshold. That makes this gateable.

### 2. Baseline capture — before installing anything

Run one measurement session on the **current** profile and keep the bundle.

Without a baseline, no cost can be attributed to the new mod later. This is the stage most
likely to be skipped and the one whose absence cannot be repaired after the fact.

### 3. Install into a probe profile, never the live one

Publish the mod into a probe profile (`<world>-vr-probe-*`), not the profile people play.
The portal supports multiple profiles per world, so this costs nothing but a release.

**Gate A — nothing reaches the live profile until stages 4-6 pass.**

### 4. Measurement session

Enable in `neuralyze.vrfixes.cfg`:

```
[9 - Profiling]
FrameAndVrReport        = true    frame pacing, OpenVR resolution, cameras, compositor
ProfilePluginUpdateCost = true    per-mod ms/frame
SweepRenderScaleOnce    = true    CPU-vs-GPU verdict
```

Then: same fixed spot, same view direction, ~2 minutes, exercise the mod's features, quit so
the bundle uploads. Same spot and view as the baseline, or the comparison is meaningless.

### 5. Ingest and compare

```
tools/vr_perf_ingest.py --bundle <post.zip> --baseline <pre.zip> \
                        --static <stage1.json> --json dossier.json
```

Stage 5 is a different tool from stage 1. `vr_impact_scan.py` reads package archives
and has no bundle flags at all; `vr_perf_ingest.py` reads diagnostics bundles.
`--static` is what merges the stage-1 findings in, so pass the `--json` file stage 1
wrote or the dossier carries measurements with no VR-impact classes beside them.

Produces one dossier per mod: static VR-impact classes, startup ms, steady-state ms/frame,
and the delta against baseline.

**Read the caveat the tool prints.** Per-mod steady-state figures cover
`Update`/`LateUpdate`/`FixedUpdate` only. They exclude a mod's Harmony patches on game
methods, its coroutines, and its GC pressure. They are **lower bounds**, not total cost. The
only route to a true total is A/B between probe profiles.

### 6. Decision

| verdict | when |
|---|---|
| **Accept** | works in VR, cost acceptable |
| **Accept with config** | a config value fixes it (cheapest remediation, always prefer it) |
| **Accept with remediation** | needs an adopt-list entry, a Misc-menu action, or a patch |
| **Reject** | expensive *and* VR-broken, or duplicates an installed mod |

Two quadrants deserve naming because they are decided differently:

- **Expensive and VR-broken** → reject. Do not spend effort fixing something that also costs
  frames on a client already at 20-28 FPS against a 72-90 Hz headset.
- **Expensive but VR-fine** → this is a frame-rate decision, not a VR decision. Judge it
  against the frame budget, not against compatibility.

**Gate B — a verdict is recorded before remediation begins.** Undocumented decisions get
re-litigated months later by someone with less context, including your future self.

### 7. Remediation, cheapest first

1. **Config value.** Always try this first. `SneakInput = ControllerOnly` fixed crouch;
   `Show Quick Slots on HUD = Off` fixed a HUD conflict. No code, no release risk.
2. **Adopt-list entry.** Canvas not converted → add its name to
   `[3 - Canvases] AdoptCanvasNames`. The scan gives you the exact name.
3. **Misc-menu action.** Unreachable input → add a line to `[10 - Misc controls] Actions`:
   `Label = zinput:<Name>` or `Label = key:<Chord>`. The scan gives you both strings. This is
   why stage 1 recovers them.
4. **Patch in `NeuralyzeVRFixes`.** Only when the above cannot express the fix.
5. **Upstream.** Slowest path; worth it for defects affecting everyone, and worth checking
   first whether a VR-fix mod already exists — `geekstreet` publishes several.

### 8. Promote and record

Publish to the live profile, then append to [`mod-decisions.md`](mod-decisions.md): package,
version, date, verdict, evidence (scan findings + measured cost), remediation applied, and
who decided.

**Gate C — configs the mod needs must be in the shipped set.** The sync builds each
generation from only what the release ships and preserves local files it does not ship, so a
config the mod needs at a non-default value must be added to `client-config/`. Confirm it is
in the published payload, not merely on disk.

---

## Instrument discipline

Every probe in this pipeline states whether it could read its source, printing
`INSTRUMENT OK` or `INSTRUMENT DEAD` with the value that justifies the verdict.

This is not decoration. Three separate instruments in this project silently returned zero and
each zero was acted on as a finding:

- a HUD delta probe read `dPos=0.000000` because it sampled once per frame at the wrong point
- a hand-velocity probe read `0.00 m/s` **twice**, once from calling a method by the wrong
  arity and once from passing a bare `null` into a `params object[]`, where the resulting
  `NullReferenceException` was swallowed by a `catch { return 0f; }`
- `XRSettings.eyeTextureWidth` read `0x0` because SteamVR's OpenVR path never populates it

Two "fixes" were shipped on the strength of those false zeros. **A probe that cannot
distinguish "measured zero" from "failed to measure" is worse than no probe**, because it
manufactures confident wrong conclusions. Before trusting a zero, confirm the instrument
reads non-zero under a known-good condition.

---

## What this process cannot tell you

State these limits when reporting a result, rather than letting a reader over-trust it:

- **Static scanning misses runtime behaviour** — reflection-built canvases, member names
  assembled from strings, obfuscated assemblies.
- **Per-mod cost is a lower bound**, for the reasons in stage 5.
- **Interaction effects are invisible to per-mod measurement.** Two mods patching the same
  method can cost more together than apart, and one can silently disable another —
  `AAABuildMenu` logged *"SearsCatalog detected … standing down to avoid conflicts"*, so a
  mod was installed, measured, and doing nothing.
- **One session is one sample.** Frame cost varies with location, biome, entity count and
  time of day. A 10% difference between two sessions is noise.
