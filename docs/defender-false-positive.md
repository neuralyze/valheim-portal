# Reporting the Defender false positive

Windows Defender classified the client as `Trojan:Win32/Bearfoos.A!ml` and deleted it from
`%LOCALAPPDATA%`, which broke the Desktop shortcut — it launches a URL protocol whose registered
target had just been removed.

`!ml` marks a machine-learning verdict rather than a signature match. Reporting it to Microsoft
gets the file's hash cleared for **every** Defender installation, at no cost. This is the standard
remedy for unsigned open-source binaries.

## What to submit

<https://www.microsoft.com/en-us/wdsi/filesubmission>

| field | value |
|---|---|
| Submission type | Software developer |
| Company / product | Valheim Portal — Valheim Profile Sync |
| File | the exact `.exe` being served (see hash below) |
| Detection name | `Trojan:Win32/Bearfoos.A!ml` |
| Do you believe this is a false positive? | Yes |
| Product source | Published at `https://github.com/neuralyze/valheim-portal/releases` |

The current build:

```
SHA-256   3a311fdf36e08e83bbe73cb2394f26591084a556a2d7019e5f31d84ea3d9417f
size      14,851,584 bytes
source    https://valheim.neuralyze.com/client/ValheimProfileSync.exe
          https://github.com/neuralyze/valheim-portal/releases
```

## Text to paste into the description

> Valheim Profile Sync is an open-source (AGPL-3.0) installer that keeps a Valheim game directory in
> sync with a self-hosted mod profile server. Source: https://github.com/neuralyze/valheim-portal —
> the binary is built by GitHub Actions from a tagged commit with `-trimpath -buildvcs=false`, so the
> build is reproducible and can be verified against the published SHA-256.
>
> The program contacts only the portal it was installed from and `gcdn.thunderstore.io` to download
> the mod packages a profile names. It contains no analytics, no telemetry and no third-party SDKs.
> It writes to the user's Valheim installation, its own profile directory, and a Desktop shortcut.
>
> We believe the detection is heuristic. The previous build copied itself into %LOCALAPPDATA% and
> registered a URL protocol pointing at that copy; that behaviour has been removed in this build —
> the executable now runs from where the user saved it. What remains is downloading mod archives and
> launching the game, which is the program's stated purpose.
>
> The binary is unsigned. A certificate from a public CA requires a paid subscription or an
> HSM-backed key, and the free foundation programme for open-source projects requires an established
> user base that this project does not have.

Attaching the release URL matters: an analyst who can see the source and a reproducible build
resolves a case faster than one weighing an anonymous executable.

## Turnaround

Typically a few days. The verdict is per **file hash**, so it clears the exact binary submitted, for
every Defender installation, and cannot be reverted by a later definition update the way an untested
build can.

## Why this is not a per-build treadmill

A new hash needs a new report, so the question is how often the client binary genuinely changes.

**Almost never.** The profile releases published constantly — `hrafnheim-vr 2.5.x` and similar — are
mod and config bundles. They do not touch the client. Before this fix, the client had two builds in
its history: the original, and the one removing the self-copy.

That gives three rules worth keeping:

1. **Do not rebuild the client to ship game or mod changes.** Those flow through profile releases,
   which Defender never sees.
2. **Batch client changes.** If the client does need work, land several changes and cut one build,
   rather than a build per fix.
3. **Report the build you intend to keep.** Submitting a throwaway wastes the case.

If the client ever does need frequent rebuilds, a per-machine exclusion for the folder holding the
executable is a one-time action that survives every rebuild — appropriate for a private server with
a handful of known players, and strictly better than repeated submissions.

The durable answer beyond that is a signature, which is deferred: see
[code-signing.md](code-signing.md).
