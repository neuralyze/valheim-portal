# Valheim Profile Sync for Windows

## Install once

Download `ValheimProfileSync.exe` from the authorized portal page, then double-click it. The application installs itself, registers the `valheim-profile-sync://` profile link, and shows a clear confirmation window.

You do not need PowerShell, Command Prompt, r2modman, or manual file copying.

## Install or update a profile

1. Install Valheim through Steam.
2. Sign in at the portal through Steam.
3. Open an authorized world page.
4. Select **Install or update** on the card that matches how you play: Desktop, Desktop VR-compatible, or VR headset.
5. The application opens and shows a short code, eight characters in two dash-separated groups.
6. The browser shows a confirmation page naming the world, profile, client type, and your Steam ID. Type the code the application is showing and submit.

Step 6 is the authorization. Opening the page does not authorize anything, which is what makes a profile link safe to click: the request only becomes a grant once you confirm it with a code that exists solely on your own screen. Check that the world and profile on the page are the ones you meant to install before you submit. Five wrong codes cancel the request; start again from the profile card.

The browser opens the installed application with only the portal URL, world, profile, and client type. Once you confirm, the application obtains a short-lived scoped authorization, compares the local profile manifest, downloads only missing or changed package archives, verifies every SHA-256 and size, and atomically activates the selected profile. Its scrollable activity log keeps each successfully installed or updated mod on a separate line for review.

When needed, the application checks the saved folder, Steam registry roots, and standard Steam locations. If none contains `valheim.exe`, use the folder control on the left to select the existing Steam `Valheim` folder. A valid selection displays a green checkmark and enables **Done**; invalid selections leave **Done** disabled. The application remembers the selection for shortcuts and future updates. It does not scan every drive, copy the game, or require an environment variable.

Each completed profile receives a `<profile>.url` Desktop shortcut with the Neuralyze icon. Creating it removes earlier `world - profile.url` and verbose Valheim Profile Sync shortcut names for the same profile. Opening the shortcut repeats the sync check and then launches Steam Valheim with that profile.

## Automatic diagnostics upload

The current installer issues a world/profile-scoped diagnostics credential when Steam authorizes a profile. After Valheim exits, the launcher uploads one ZIP to the portal containing `Player.log`, `BepInEx/LogOutput.log`, and the newest `LoadTimeProfiler` report when present. The bundle is limited to 64 MiB; no save data, game binaries, or unrelated files are collected.

Administrators retrieve recent bundles from `GET /admin/diagnostics` and download an individual bundle from `GET /admin/diagnostics/{id}`. The credential is restricted to diagnostics uploads for the selected release, expires after 30 days, and is invalidated immediately if the player's world access or that release changes.

## Local state and recovery

Profiles are isolated at:

```text
%LOCALAPPDATA%\ValheimProfileSync\profiles\<world>--<profile>--<client-type>
```

The Steam game is not copied. Valheim Profile Sync installs only the controlled Doorstop bootstrap files required to select the active profile. It refuses to overwrite a loader it does not own.

An interrupted, altered, unsafe, or failed update leaves the previously active profile unchanged. Run the profile link or Desktop shortcut again after the issue is corrected. Do not extract profile ZIPs, import them into r2modman, or copy profile files into a dedicated-server directory.

Flat profiles still install the shared ValheimVR compatibility plugin and VR-fix packages. Their immutable profile config sets `nonVrPlayer = true`, so the plugin keeps remote VR animation synchronization but skips local SteamVR/OpenVR initialization. Flat releases never receive a `vr_runtime` artifact.

## VR runtime and switching back

A VR profile downloads a second, release-bound `vr_runtime` artifact after Steam authorizes the selected world/profile. The application verifies its SHA-256 and size, validates the strict ValheimVR path allowlist, and stages it under the isolated profile before changing Steam's game directory.

Only files recorded as portal-owned runtime files may be installed or removed. An existing unknown file, a file owned by another manager, or an altered portal-owned file stops the change without overwriting it. Select and synchronize a Flat profile to remove the active portal-owned ValheimVR runtime; unrelated Steam files remain untouched. If a VR activation fails, the prior working profile and runtime are restored.
