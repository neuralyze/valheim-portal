# Profile release format

A release is immutable and scoped by `world`, `profile`, `client_type` (`flat` or `vr`), and version. Publishing archives only the previous current release with the same scope.

## Artifacts

Every release has exactly one immutable `profile` ZIP. It contains only:

```text
profile-manifest.json
config/
```

The manifest has schema `1`, exact world/profile/client-type binding, and filename-sorted Thunderstore package pins with size and SHA-256.

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
