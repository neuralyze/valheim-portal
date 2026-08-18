# Profile release format

A release is immutable and scoped by `world`, `profile`, `client_type` (`flat` or `vr`), and version. Publishing archives only the previous current release with the same scope.

## The release row

The `releases` table holds the portal's own facts about a release. One of them is
`releases.audience`, added by migration 20 (`internal/app/store.go:302-316`):

- Values are exactly `player` or `admin`; a `CHECK` constraint enforces it and an explicit
  bad value fails loudly rather than being corrected.
- `cmd/seed-release` writes it from `-audience`, which is required and has no default
  (`cmd/seed-release/main.go:48`, `:123-126`).
- The profile-card classifier reads it (`internal/app/profile_cards.go:172-181`) to decide
  which card kind a release is, and the admin edition is offered **only to an admin login**
  (`profile_cards.go:105-107`): an ordinary player's world page does not list it.

The audience is a portal-only fact, and [it has to live here](#the-definition-format-is-frozen)
rather than in the artifact players download.

## Artifacts

Every release has exactly one immutable `profile` ZIP, the profile definition. It contains
exactly two entries:

```text
profile-manifest.json
config/
```

### The definition format is frozen

`profile-manifest.json` carries exactly these keys and no others:

| key | value |
|---|---|
| `schema` | always `1` |
| `world` | the world the release is scoped to |
| `profile` | the published profile name |
| `client_type` | `flat` or `vr` |
| `packages` | Thunderstore package pins, sorted by filename, each with namespace, name, version, filename, size and SHA-256 |
| `companion` | optional, and only on a `flat` definition: the Flat companion's filename, size and SHA-256 |

**Nothing may be added to the file or to the archive.** The definition is a wire contract
with a separately installed consumer:

- An installed client decodes the manifest with `json.Decoder.DisallowUnknownFields()`
  (`cmd/valheim-profile-sync/sync.go:715-718`), so one unknown key fails the whole sync.
- The archive allowlist admits only `profile-manifest.json` and `config/`
  (`sync.go:696-712`), so an extra file fails it too.
- `schema` cannot be bumped. The client tests `schema == 1` by equality
  (`sync.go:622`), so a definition declaring `2` is rejected by every client already
  installed. The version number is not an escape hatch.

There is no self-update path in the client, and sync runs before the game launches - a
failed `synchronize` returns before `launchProfile` is ever reached
(`cmd/valheim-profile-sync/main.go:54` and `:117`) - so a definition a client cannot parse
locks players out of Valheim rather than pinning them to an old profile. On 2026-08-17 an
`audience` field was added to the manifest and every player's install failed with
`decode profile definition: json: unknown field "audience"`.

A portal-only fact therefore goes on the release row instead. That is where `audience`
lives now; `cmd/profile-definition-builder` still validates its `-audience` flag but does
not write it into the archive.

### What `config/` excludes

`config/` carries the client configs an operator owns, not saved copies of them. The
builder skips any name containing `.before-`, `.bak` or `_changed.`, or ending in `~`
(`isConfigBackup`, `cmd/profile-definition-builder/main.go:496-503`). The marker list is
shared with `BACKUP_MARKERS` in `tools/settings_history.py:61`, so the settings store and
the player download agree about what counts as a setting; a new marker has to be added in
both places. Before this exclusion every edition shipped 14-16 backup files to players.

### Auxiliary artifacts

A `flat` release may additionally contain exactly one immutable `flat_companion` ZIP. Its filename, size, and SHA-256 are declared by the profile manifest and must match the release artifact. A `vr` release instead requires exactly one immutable `vr_runtime` ZIP. Upload and publication verify every artifact's size and SHA-256; publication validates every profile, companion, and runtime payload again.

`flat_companion` and `vr_runtime` are bound through their parent release to the exact `{world, profile, client_type, version}`. Neither is a Thunderstore package or may attach to the opposite client type. Authorized device clients receive the selected auxiliary artifact's checksum and size only for their selected release, then fetch it using the same scoped device token.


## Flat companion archive allowlist

The Flat companion archive may contain `INSTALL.txt`, `BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg`, and only the reviewed ValheimVR DLL set: `ValheimVRMod.dll`, SteamVR and SteamVR actions, Unity XR/OpenVR/SpatialTracking/LegacyInputHelpers, bHaptics, Final IK, RootMotion, Valve Newtonsoft, NDesk.Options, and Amplify Occlusion. The legacy `ValheimVRFlatDodgePatchFix.dll` remains accepted only for already published releases; canonical new Flat artifacts omit it. `INSTALL.txt` is ignored during synchronization. The archive must contain `BepInEx/plugins/ValheimVRMod.dll`; game runtime paths and every unreviewed DLL are rejected.

## VR runtime archive allowlist

The archive may contain only the validated ValheimVR 0.9.21 runtime layout:

```text
BepInEx/plugins/ValheimVRMod.dll
BepInEx/plugins/bHaptics/*.tact
BepInEx/plugins/BackpacksVRFix/BackpacksVRFix.dll
Valheim_Data/Managed/<enumerated ValheimVR DLLs>
Valheim_Data/Plugins/x86_64/{openvr_api,ucrtbased,XRSDKOpenVR}.dll
Valheim_Data/StreamingAssets/<enumerated ValheimVR assets and SteamVR bindings>
Valheim_Data/UnitySubsystems/XRSDKOpenVR/UnitySubsystemsManifest.json
```

Paths are case-insensitively unique. Symlinks, traversal, drive/absolute paths, oversized entries or archives, malformed ZIPs, and every unlisted file are rejected. The archive cannot carry `valheim.exe`, `UnityPlayer.dll`, Docker data, server files, or world saves.

## Activation and rollback

The client verifies and stages the runtime outside Steam in the selected profile generation. It tracks every overlay file in a game-directory ownership state. It refuses to replace unknown, foreign, or non-regular files. On a VR update it removes only checksum-verified portal-owned files; on a Flat switch it removes that same owned set. A failed transition restores the prior portal-owned runtime and the previous profile generation. Steam game files unrelated to the active overlay are never removed.

Windows application distribution is independent of release artifacts. `installer_windows`, `installer_linux`, and `client_bundle` are not release artifact kinds.
