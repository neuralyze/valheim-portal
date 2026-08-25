# Packaging ValheimVR for the portal

ValheimVR compatibility is shared by Flat and VR profiles. Both retain ValheimVR so Flat players can render synchronized VR-player movement. Flat sets `nonVrPlayer = true` and installs the locally built non-VR companion artifact. VR sets `nonVrPlayer = false` and installs a separate immutable `vr_runtime` artifact. Neither artifact is placed in the dedicated-server container.

## The one modification this project makes to ValheimVR

Everything else in the companion is upstream ValheimVR. This project changes exactly one
behaviour, and `scripts/build-valheimvr-artifact.sh` refuses to build without it.

**The problem.** ValheimVR applies a Harmony postfix to `Player.Update`
(`ValheimVRMod.Patches.ControlPatches+Player_UpdateDodge_Patch`) that implements
VR dodging from headset motion. Flat profiles keep ValheimVR loaded so desktop players
can see VR players' synchronized movement, but that dodge patch has no business running
for someone on a keyboard: it is driven by VR input that a Flat player does not produce.

**The fix, current generation.** In the local ValheimVR source, the dodge patch class
carries a Harmony `[HarmonyPrepare]` method returning `!VHVRConfig.NonVrPlayer()`.
Harmony calls it before patching and skips the patch when it returns false, so on a
Flat profile — which sets `nonVrPlayer = true` — the patch is never applied at all.

This lives in `ValheimVRMod/Patches/ControlPatches.cs` in the ValheimVR working copy, not
in this repository. It is the single reason the Flat companion needs a locally built
`ValheimVRMod.dll` rather than the upstream binary. The build script greps for both
markers and aborts with `Flat dodge guard is absent from ControlPatches.cs.` if a
ValheimVR update has overwritten them.

**Carrying it across an upstream update.** Since 2026-08-18 the working copy is not a tree
of uncommitted edits: our changes are seven named commits on a branch, `neuralyze/local`, and
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

It is superseded. `build-valheimvr-artifact.sh` deletes `ValheimVRFlatDodgePatchFix.dll`
from every companion it builds, and `release-format.md` accepts it only in
already-published releases. New companions must not contain it. `FlatDodgePatchFix.cs` is
retained in the shared mapping directory described under *Profile artifact mapping* below,
for the published releases that still reference it.

## Build on this host

The Windows build host and its Visual Studio are gone, and MSBuild with them. Nothing
about ValheimVR needed Windows: it is a BepInEx plugin compiled against the game's own
managed assemblies, which is what `tools/vrfixes/build.sh` has always done for our own
plugin. Mono's `mcs` compiles the whole mod on this host in about two seconds.

The source checkout is the ValheimVR working copy kept beside the world's custom mods,
reached over the CIFS mounts `scripts/mount-windows.sh` creates:

```text
<world-root>/<WORLD>/mods/custom/ValheimVR-latest
```

### Compiling the mod

```sh
cd <portal checkout>
./scripts/build-valheimvr.sh \
  --source-root <ValheimVR checkout> \
  --output /tmp/ValheimVRMod.dll \
  --bepinex <ValheimVR checkout>/build/bepinex
```

`--configuration` takes `Release` (the default), `Debug`, `SyncOnlyRelease` or
`SyncOnlyDebug`, which are the four `ValheimVRMod.csproj` declares and carry the same
defines. `--refs DIR` names the reference assemblies and defaults to
`<source-root>/build/latest`. The script prints one JSON object — `valheimvr_dll`,
`valheimvr_dll_sha256`, `configuration`, `sources`, `references`. A current full build is
107 sources against 110 references.

### Three things that will otherwise cost you a session

**`-nostdlib -noconfig` makes `mcs` hang, not fail.** It sits there indefinitely instead
of reporting a missing corlib. Let `mcs` use its own default configuration. This is why
`build-valheimvr.sh` does not pass those flags, and why it must not be "tidied up" to.

**`-langversion:latest` is required, not cosmetic.** The default language version rejects
this source outright with `CS1644` (feature not available in this language version) and
`CS1738` (named arguments before positional ones).

**The reference assemblies are the game's, and are not vendored.** They are
`UnityEngine*`, `assembly_valheim`, `SteamVR`, `final_ik` and the rest, published under
the game's own licence, so this repository does not and cannot carry them. Point `--refs`
at a directory holding them — a synchronized VR profile's `Valheim_Data/Managed` serves —
and `--bepinex` at a BepInEx core directory holding `BepInEx.dll` and `0Harmony.dll`.
**Copy the BepInEx core directory off the SMB profile share first.** Reading references
across the share makes the build appear to hang; it is only the compiler waiting on the
network, but it looks exactly like the `-nostdlib` failure above.

### Building the portal artifacts

`scripts/build-valheimvr-artifact.sh` compiles the mod, swaps the result into a template
archive and rezips. It replaces both the PowerShell Flat companion builder that needed
MSBuild and the staging upstream's `make-release.cmd` did on Windows, so the Flat
companion and the VR input archive now come out of one script that differs only in
`--client-type`:

```sh
./scripts/build-valheimvr-artifact.sh \
  --source-root <ValheimVR checkout> \
  --template <existing artifact of the same client type> \
  --output <artifact ZIP> \
  --client-type flat
```

`--configuration` here takes `Release` (the default) or `SyncOnlyRelease`; `--refs` and
`--bepinex` are passed through to `build-valheimvr.sh`. It prints one JSON object —
`artifact`, `sha256`, `valheimvr_dll_sha256`, `configuration`.

`--client-type flat` requires the template's
`BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg` to set `nonVrPlayer = true`. A
template without it would produce a companion running VR input handling for desktop
players, which is the whole failure the dodge guard exists to prevent. It also deletes the
superseded `ValheimVRFlatDodgePatchFix.dll`; a companion carrying both would unpatch what
was never patched. The output is the Flat companion, uploaded as the `flat_companion`
artifact.

`--client-type vr` requires `nonVrPlayer = false` and produces a ValheimVR release
archive with only top-level `BepInEx/` and `Valheim_Data/` — the canonical input,
currently ValheimVR 0.9.21, that the existing runtime builder takes as its first
argument:

```sh
./scripts/build-valheimvr-artifact.sh \
  --source-root <ValheimVR checkout> \
  --template /path/to/vhvr-release.zip \
  --output /tmp/vhvr-release-rebuilt.zip \
  --client-type vr

./scripts/build-vr-runtime-artifact.sh \
  /tmp/vhvr-release-rebuilt.zip \
  /path/to/valheimvr-vr-runtime.zip
```

`build-vr-runtime-artifact.sh` validates the fixed ValheimVR 0.9.21 allowlist before
atomically copying the archive. It rejects unknown files, symlinks, duplicate paths
ignoring case, traversal, malformed archives, and oversize content.

Neither artifact is ever placed in the dedicated-server container. The companion is a
client payload; the server has no use for `ValheimVRMod.dll` and must not receive it.

### Publish the source for the DLL you just built

ValheimVR is GPL-3.0 and both artifacts contain a `ValheimVRMod.dll` we compiled, so
handing either to a player obliges us to offer the source it was built from. Two things
discharge that, and the second is the one that gets forgotten.

The offer itself is already on the pages: the world page and the release page of any
release carrying a `flat_companion` or `vr_runtime` link `PORTAL_VHVR_SOURCE_URL`, which
defaults to `https://github.com/neuralyze/vhvr-mod`. Push the branch the build came from
before publishing, or the link is a public repository that does not contain the source:

```sh
git -C "$SOURCE_ROOT" push https://github.com/neuralyze/vhvr-mod.git \
  neuralyze/local:refs/heads/neuralyze/local
```

Then anchor the binary. The licence owes the source corresponding to the copy a person
received, and our release numbers (`hrafnheim-vr 2.5.111`) mean nothing in a ValheimVR
checkout, so the anchor is the DLL's own SHA-256 — which a recipient can compute from
the file in their hands. `build-valheimvr-artifact.sh` prints it as
`valheimvr_dll_sha256`:

```sh
git -C "$SOURCE_ROOT" push https://github.com/neuralyze/vhvr-mod.git \
  "$(git -C "$SOURCE_ROOT" rev-parse HEAD):refs/tags/shipped/valheimvrmod-${valheimvr_dll_sha256:0:12}"
```

Do not try to match a published DLL by rebuilding and comparing hashes: `mcs` is not
reproducible, and two compiles of one tree differ in roughly 77,800 of 600,064 bytes.
What a rebuild can establish is size and content — a `Release` build of the right commit
lands on the exact byte count, and strings and type references added by a given commit
are either present or not. That is how the currently published VR runtime DLL
(`f879224e030c`) was tied to `23f0ce4526cd`.

The Flat companion published today has no such tag, and the reason is on the record in
`deploy/upstream-sources.json` under `correspondence_gap`: its DLL was built on
2026-07-26 from a working tree three weeks older than the first commit on the branch,
and that tree is not in git. It closes when the companion is rebuilt from a commit and
republished. Tag every future build at the time you build it — reconstructing which
revision produced a binary after the fact is the work that gap is made of.

### How the Mono build was shown to match the Roslyn one

The DLL that shipped before this change was built by Roslyn on the Windows host. The
Mono-built DLL was compared against it by type surface: both declare the same **350
non-compiler-generated type names, with no difference at all** in that set. The two
assemblies differ only where a compiler is free to differ — Roslyn's `<>c` and
`<>c__DisplayClass*` closures and `<M>d__*` iterators against Mono's `c__AnonStorey*` and
`c__Iterator*`, plus two Roslyn-only marker attributes
(`Microsoft.CodeAnalysis.EmbeddedAttribute` and
`System.Runtime.CompilerServices.IsReadOnlyAttribute`) that Mono does not emit. The Flat
companion built around the Mono DLL was then run through the portal's own
`app.ValidateFlatCompanionArtifact` and accepted; the VR archive is gated the same way by
`app.ValidateVRRuntimeArtifact` at publication.

**That is a static equivalence result, not a play-test.** It says the two compilers
produced the same declared API from the same source, and that the packaging still passes
the checks the portal enforces. It does not say the mod was run in the game. Step 6 of
[Publish a Flat ValheimVR release](operations.md#publish-a-flat-valheimvr-release) is
still the thing that proves a companion works.

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
