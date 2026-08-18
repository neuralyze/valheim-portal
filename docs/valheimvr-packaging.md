# Packaging ValheimVR for the portal

ValheimVR compatibility is shared by Flat and VR profiles. Both retain ValheimVR so Flat players can render synchronized VR-player movement. Flat sets `nonVrPlayer = true` and installs the locally built non-VR companion artifact. VR sets `nonVrPlayer = false` and installs a separate immutable `vr_runtime` artifact. Neither artifact is placed in the dedicated-server container.

## The one modification this project makes to ValheimVR

Everything else in the companion is upstream ValheimVR. This project changes exactly one
behaviour, and `tools/build-valheimvr-flat.ps1` refuses to build without it.

**The problem.** ValheimVR applies a Harmony postfix to `Player.Update`
(`ValheimVRMod.Patches.ControlPatches+Player_UpdateDodge_Patch`) that implements
VR dodging from headset motion. Flat profiles keep ValheimVR loaded so desktop players
can see VR players' synchronized movement, but that dodge patch has no business running
for someone on a keyboard: it is driven by VR input that a Flat player does not produce.

**The fix, current generation.** In the local ValheimVR source, the dodge patch class
carries a Harmony `[HarmonyPrepare]` method returning `!VHVRConfig.NonVrPlayer()`.
Harmony calls it before patching and skips the patch when it returns false, so on a
Flat profile — which sets `nonVrPlayer = true` — the patch is never applied at all.

This lives in `ValheimVRMod/Patches/ControlPatches.cs` in the ValheimVR working copy on
the build host, not in this repository. It is the single reason the Flat companion needs
a locally built `ValheimVRMod.dll` rather than the upstream binary. The build script
greps for both markers and aborts with `Flat dodge guard is absent from
ControlPatches.cs.` if a ValheimVR update has overwritten them.

**Carrying it across an upstream update.** Since 2026-08-18 the working copy is not a tree
of uncommitted edits: our changes are six named commits on a branch, `neuralyze/local`, and
the guard is the first of them. Taking upstream work is
`git fetch origin master && git rebase origin/master`, which replays them — so the guard
arrives with the update instead of being overwritten by it, and the build script's grep
passes without anyone reapplying anything by hand. Record the new head in
`deploy/upstream-sources.json`; the `upstream` gate compares the two. What the branch
carries, and which parts are offered upstream, is listed in
[upstream sources](upstream-sources.md).

**The fix, previous generation.** Before the in-mod guard, the same outcome was reached
from outside with a small standalone BepInEx plugin, `FlatDodgePatchFix.cs`
(`com.valheimvr.flat-dodge-compatibility`). It waited for ValheimVR to load, found that
postfix on `Player.Update` by owner and declaring type, and called `Harmony.Unpatch` on
it. It worked, but it was strictly worse: it let the patch be applied and then removed
it, so correctness depended on plugin load order, and a silent failure to find the patch
left VR dodging active for Flat players.

It is superseded. `build-valheimvr-flat.ps1` deletes `ValheimVRFlatDodgePatchFix.dll`
from the companion, and `release-format.md` accepts it only in already-published
releases. New companions must not contain it. The source is retained under
`tools/valheimvr/` for the published releases that still reference it.

## Build on the Windows ValheimVR host

The source project lives on the Windows build host, under the ValheimVR working copy
kept beside the world's custom mods:

```text
<world-root>/<WORLD>/mods/custom/ValheimVR-latest/ValheimVRMod
```

Build `ValheimVRMod.sln` in `Release|AnyCPU`. Its existing `make-release.cmd` must run only against a disposable/development Windows Valheim installation; it stages the required runtime output and may copy files into `GAME_DIR`.

The canonical input archive is the release ZIP that build produces, currently
ValheimVR 0.9.21:

```text
<world-root>/<WORLD>/mods/custom/ValheimVR-latest/vhvr-0.9.21.zip
```

It must have only top-level `BepInEx/` and `Valheim_Data/`. Create the portal artifact with:

```sh
cd <portal checkout>
./scripts/build-vr-runtime-artifact.sh \
  /path/to/vhvr-release.zip \
  /path/to/valheimvr-vr-runtime.zip
```

The command validates the fixed ValheimVR 0.9.21 allowlist before atomically copying the archive. It rejects unknown files, symlinks, duplicate paths ignoring case, traversal, malformed archives, and oversize content.

## Profile artifact mapping

The shared mapping lives in the separate `ValheimConfig` repository, which holds the
mod boilerplate this project does not carry:

```text
<ValheimConfig>/boilerplate/mods/valheimvr/
├── valheimvr-artifacts.json    # the mapping
├── FlatDodgePatchFix.cs
└── README.md
```

**The archives it names are not in the repository, and cannot be.** They bundle
non-redistributable content — Microsoft debug CRT files, paid Unity Asset Store
assemblies, and Unity XR runtime files — so `valheimvr-artifacts.json` is a mapping to
archives you build and place yourself, not an index of shipped files. See that
directory's `README.md`. The names follow
`ValheimVR-<commit>-nonvrfix-flat-companion.zip` and
`ValheimVR-<commit>-BackpacksVRFix-<version>-client.zip`; `valheimvr-artifacts.json`
records the exact ones for the current build.

`build-profile-definition.sh` uses a profile-local `valheimvr-artifacts.json` only when one exists. Otherwise it uses this shared mapping automatically. New worlds and profiles therefore reuse the same reviewed ValheimVR artifacts without copying them into their own `mods/custom` directory. Set `VALHEIM_SHARED_MODS_ROOT` only when intentionally using a different shared artifact directory.

The Flat entry identifies the locally built non-VR companion ZIP. The profile-definition builder records its filename, SHA-256, and size in each generated Flat profile definition. Upload that same ZIP as a `flat_companion` artifact for the Flat release.

The VR entry identifies the local VR client ZIP. Build the validated `vr_runtime` artifact from that ZIP and upload it only to the VR release.

The portal validates a Flat companion before publication. It permits only `BepInEx/plugins/` and the ValheimVR immutable config path, requires `BepInEx/plugins/ValheimVRMod.dll`, and rejects Steam runtime files, server files, unknown paths, symlinks, traversal, duplicate paths, and oversized archives.

## True nonVR package policy

`internal/valheimvr/packages.json` is the authoritative list of ValheimVR integration packages. The profile-definition builder embeds and validates that JSON when it builds a `-true-nonvr` profile, rejecting every listed package.

True nonVR editions have no ValheimVR package, configuration, Flat companion, or VR
runtime. They are built from the `flat` profile with `valheim_vr: false` in the release
target, and named `<world>-non-vr`; they retain every package outside that JSON policy.

## Release publication

1. Build every edition the release target catalog declares for each active world. Four per world here: `<world>-vr` (from the `vr` profile), `<world>-vr-flat` and `<world>-non-vr` (both from `flat`), and `<world>-vr-flat-admin` (from `admin`). Only `<world>-non-vr` sets `valheim_vr: false`; the other two Flat editions carry the companion.
2. Create separate Flat and VR drafts with the exact world, profile identifier, and version.
3. Upload the Flat profile ZIP and its mapped `flat_companion` ZIP to the Flat draft. Upload the VR profile ZIP and its validated `vr_runtime` ZIP to the VR draft.
4. Publish. The portal scopes each artifact to the selected release and verifies checksum, size, structure, and client type.
5. The Flat synchronizer installs normal packages first, then verifies and stages the non-VR companion in the isolated BepInEx generation. The VR synchronizer stages its VR runtime separately.
6. Switching releases activates a new profile generation; failed downloads or extraction leave the previous generation intact.

## Safety boundary

The portal client never accepts `valheim.exe`, `UnityPlayer.dll`, arbitrary Steam files, Docker files, dedicated-server plugins, world saves, or server configuration in a runtime artifact. The portal service has no Docker socket or world-save access. Windows is the supported player installation target.
