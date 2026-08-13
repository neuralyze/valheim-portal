# Code signing the Windows client

## Why

Windows Defender classified an unsigned build as `Trojan:Win32/Bearfoos.A!ml` and deleted it from
`%LOCALAPPDATA%`. The Desktop shortcut then reported **Application Not Found** — it launches a URL
protocol whose registered target had just been removed, so the shortcut was intact and pointing at
nothing.

`!ml` means the detection came from a machine-learning heuristic, not a signature match. The
behaviour it matched was, in order of how suspicious each part looks on its own:

| behaviour | why it looked wrong | status |
|---|---|---|
| copied itself into `%LOCALAPPDATA%` | duplicating an executable into a data directory is what droppers do | **removed** |
| unsigned | nothing vouches for the publisher | signing, below |
| registered a URL protocol pointing at that copy | a handler aimed at a self-made duplicate | now points at where the player put the file |
| downloads executables and launches them | that is the entire job of a mod installer | unavoidable |

The self-copy bought nothing — the executable now stays where the player saved it, and the protocol
registration is refreshed on every run, so moving, restoring, or replacing the file repairs its own
shortcut. That removed the loudest signal for free. Signing removes the second.

## Free options, in the order worth trying

### 1. Report the false positive

<https://www.microsoft.com/en-us/wdsi/filesubmission> — submit as a software developer. Microsoft
re-analyses, usually within a few days, and whitelists the **file hash** for every Defender
installation. Costs nothing and helps every player at once.

The hash is what gets cleared, so a rebuild starts over. Submit a build you intend to keep.

### 2. SignPath Foundation — free, but not for a project this small

<https://signpath.org/apply> issues code-signing certificates to open-source projects at no cost and
signs from CI. A public repository and an OSI licence are necessary but not sufficient: the
foundation vouches for the projects it signs, so it expects an established user base and visible
community. A self-hosted portal serving one private server does not qualify, and applying wastes
the reviewer's time as well as yours.

Worth revisiting only if this is ever used beyond one server.

Publishing the artifact as a **downloadable release asset** is worth doing regardless — a CI
artifact is not enough, because those expire and are not public. `release-client.yml` therefore
publishes every tag as a GitHub Release carrying the executable and a `SHA256SUMS` file, using the
signed build when one exists and the unsigned build until then. That release is the canonical
download; the portal should serve the same bytes.

```bash
git tag v2.6.0 && git push origin v2.6.0     # builds, signs if configured, publishes the release
```

`.github/workflows/release-client.yml` already implements the build side. Once the project is
approved, set these and the signing job starts running:

```
repository variables   SIGNPATH_ORGANIZATION_ID, SIGNPATH_PROJECT_SLUG
repository secret      SIGNPATH_API_TOKEN
```

Until they are set, the signing job is skipped and the workflow still produces an unsigned build —
a signing outage must never be the reason a fix cannot ship.

### 3. Sign locally

`scripts/sign-windows-client.sh` signs with `osslsigncode` on Linux, from either a `.pfx` or a
PKCS#11 token, always adds an RFC3161 timestamp, and verifies its own output. `build-windows-client.sh`
calls it when credentials are present:

```
PORTAL_SIGNING_PKCS12       a .pfx holding certificate and key
PORTAL_SIGNING_PASSWORD     its password
PORTAL_SIGNING_PKCS11_MODULE / _CERT / _KEY    for a hardware or cloud-HSM key
```

Public certificate authorities have required hardware-backed keys since June 2023, so a purchasable
`.pfx` no longer exists. Paid routes, for reference: Azure Trusted Signing (~$10/month, signs from
Linux, no token), Certum open-source (~€90/year, USB token), standard OV (~$200–400/year, token).

### What not to do

`scripts/make-selfsigned-signing-cert.sh` exists for testing the signing pipeline. A self-signed
certificate is only trusted where its certificate is imported, so distributing one would mean asking
every player to install a root certificate — which defeats the point of an installer that is meant
to make things easier. Use it to verify the pipeline works, not to ship.

## Verifying a download

Every release publishes the SHA-256 of the file players actually download. To check a copy:

```powershell
Get-FileHash .\ValheimProfileSync.exe -Algorithm SHA256
```

```bash
sha256sum ValheimProfileSync.exe
osslsigncode verify -in ValheimProfileSync.exe    # once signing is in place
```

## If Defender quarantines a build anyway

1. Windows Security → Protection history → the item → Actions → **Restore**.
2. Report it (step 1 above) so the next player does not hit it.
3. An exclusion is a last resort, only worth doing once the binary's origin has been verified by
   hash, and only for a dedicated folder such as `C:\Tools\ValheimProfileSync\` — never for
   `Downloads`, which is where untrusted files arrive.
