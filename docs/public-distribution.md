# Public distribution readiness

An assessment of what stands between this repository and a portal other people
can deploy. Written while building `scripts/install-portal.sh`, so the findings
are what the installer actually ran into.

**Current state: publishable, with two open questions.** The architecture is
sound and unusually careful about privilege. Licensing and administrative
identity are settled; mod redistribution and compiled-in branding are not.

This page was written while building `scripts/install-portal.sh` and is kept as
a running record: each finding keeps its original diagnosis and gains a status
line, so a reader can see what was wrong and what was done about it.

## Blockers

These made a public release either impossible or irresponsible. Three are now
resolved.

### 1. The repository is not self-contained — resolved

`internal/agent/agent.go` maps 35 world operations onto 20 distinct shell
scripts in `AGENT_SCRIPT_DIR`, from `start_valheim_server.sh` to
`provision_valheim_server.sh`. None used to be in this repository: they lived in
a parent `ValheimConfig` directory, a *Mercurial* working copy, so they were not
versioned alongside the Go code that depends on them. A clone plus the installer
produced an agent that failed every operation. Worse, the portal's real
behaviour lived in a separately versioned repository, so no portal commit pinned
the operations it invoked and no release could be reproduced from this
repository alone.

*Status: resolved.* The scripts are now `hostops/` — one reference entry each in
[script-reference.md](script-reference.md) — the Python they delegate to is `tools/`, and `AGENT_SCRIPT_DIR` defaults to the installed `hostops/`
directory. One commit now pins both halves. The scripts no longer resolve
anything relative to a parent checkout: the two paths that genuinely live
outside the project — the world root and the `valheim-server-docker` fork — are
`VALHEIM_ROOT` and `VALHEIM_SERVER_DOCKER_DIR`, neither with a default, both
exiting 78 and naming themselves when unset.

`valheim-server-docker` was deliberately *not* vendored. It is a 593-file,
29 MB Apache-2.0 fork; carrying a modified copy would add that project's
redistribution obligations to every release of this one for no gain, since an
operator running Valheim already has it. Making it a required setting keeps the
dependency visible instead of implicit.

### 2. Administration has no authentication of its own — resolved

`isAdmin` in `internal/app/server.go` grants full administrative access to any
request from `PORTAL_TRUSTED_PROXY_CIDR` carrying a non-empty
`X-Forwarded-User`. The value is never verified.

For a single operator running a known proxy this is a reasonable division of
labour. As a public product it is a footgun with a very sharp edge, because the
failure is silent and total. Two independent misconfigurations each grant
strangers world deletion, restore, and mod deployment:

* a trusted range wider than the proxy, and
* a proxied route that does not set the header.

The second is the easy one to get wrong. nginx forwards unrecognised client
headers upstream unchanged, so *omitting* `proxy_set_header X-Forwarded-User`
is a bypass rather than a safe default. Measured directly against nginx 1.31:

```
location without proxy_set_header  -> upstream-saw:'attacker-supplied'
location with    ... ""            -> upstream-saw:None
```

*Status: resolved.* Administration now needs a third factor the portal verifies
itself: an `X-Portal-Admin-Token` header matching `PORTAL_ADMIN_TOKEN_FILE`,
compared in constant time. The portal refuses to start without that file, and
there is no header-only fallback. The finding that forced it: under the shipped
compose deployment Docker NATs every request to the bridge gateway, which *is*
the configured trusted range, so the network half of the old check was
unconditionally true and one unverified header was the whole boundary. The
installer's preflight now also rejects a trusted range equal to the bridge
gateway `/32`, still rejects wide ranges, keeps the bind on loopback, and probes
the public edge for spoofing after install.

The proxy remained part of the authentication path — it injects both headers —
but a merely *ordinary* proxy that forwards client headers unchanged no longer
granted administration, because the client cannot supply the token.

*Also resolved: the proxy is out of the path.* `PORTAL_ADMIN_STEAM_IDS` lists
the SteamID64s permitted to administer the portal, and a request carrying a
Steam session for one of them is authorised by the portal itself — no trusted
range, no identity header, no token. The audit actor is `steam:<steamid64>`, so
actions remain attributable. The proxy factors are kept as break-glass for when
Steam OpenID is unreachable or the allowlist is wrong, and an empty allowlist
leaves the previous behaviour untouched, so this is additive rather than a
migration. What it removes is the failure mode the diagnosis above kept
circling: administration no longer depends on an assertion made by a component
that has no idea who the user is.

One deployment consequence, because it bites immediately: the nginx `/admin`
location runs `auth_basic`, which challenges an allowlisted operator before the
portal ever sees the request. Drop those two lines when the allowlist is in
use. `$remote_user` is then empty, the proxy path grants nothing, and putting
them back re-enables break-glass in one reload.

### 3. No license — resolved

`LICENSE` is **AGPL-3.0**, and `v0.1.0` is tagged. `NOTICE` records third-party
attribution. Recipients have the right to use, modify, and redistribute the
code under those terms, including the network-use provision: anyone running a
modified portal as a service must offer its source to its users.

`valheim-server-docker`, which `VALHEIM_SERVER_DOCKER_DIR` points at, is an
Apache-2.0 fork carrying local modifications. It is not redistributed with this
repository, so those obligations fall on whoever redistributes that fork; they
are recorded in `NOTICE` so the dependency is not invisible.

This does not settle mod redistribution, which is a separate question; see
below.

### 4. Third-party redistribution is unresolved — blocks serving, not publishing

The portal builds and serves profile payloads containing BepInEx, ValheimVR,
EpicLoot, Backpacks, and other Thunderstore packages, and
`docs/operations.md` describes rebuilding and repackaging `ValheimVRMod.dll`.
Hosting those artifacts for the public is redistribution, and each package
carries its own license and its own view of repackaging.

**What this does and does not block.** This repository contains no third-party
binary: no DLL, no archive, no game or mod file is tracked here, so publishing
the source redistributes none of it. The obligation attaches to a *running*
portal that serves the payloads it builds. Publishing the code is therefore
safe; serving those artifacts to anyone outside the operator's own group is
what must wait for the fix below.

The archive validation is real but narrower than it looks, and an inspection of the
actual artifacts contradicts the comfortable reading:

* The Flat companion allowlist in `internal/app/flat_companion.go` **permits** Unity
  runtime assemblies (`Unity.XR.OpenVR.dll`, `UnityEngine.SpatialTracking.dll`,
  `UnityEngine.XR.LegacyInputHelpers.dll`) and two paid Unity Asset Store products,
  `final_ik.dll` and `amplify_occlusion.dll`. They are required for ValheimVR to run,
  so this is by design, not an oversight.
* The ValheimVR client archive additionally carries
  `Valheim_Data/Plugins/x86_64/ucrtbased.dll` — Microsoft's *debug* Universal CRT,
  which the Visual Studio licence does not permit redistributing.

Everything objectionable is inherited: `scripts/build-valheimvr-artifact.sh` takes an
upstream ValheimVR release archive as a template and swaps in one locally built
`ValheimVRMod.dll`.

*Fix:* stop redistributing the template. Have clients fetch ValheimVR from
Thunderstore directly, using the hashes the portal already verifies, exactly as they
already do for every other package — leaving the portal to ship only its own compiled
DLL. Enumerate the remaining managed packages and record each licence. Until that is
done, these artifacts should not be served to anyone outside the operator's own group.

## Changes needed, short of blocking

### Branding is compiled in

Not just cosmetic strings — identifiers a fork cannot avoid:

| Location | Detail |
|---|---|
| `go.mod` | Module path `github.com/neuralyze/valheim-portal` |
| `internal/app/server.go` | `go:embed` of `neuralyze-logo.svg`, `neuralyze.ico`, `neuralyze-{192,512,512-maskable}.png`, served at fixed `/assets` and `/icons` routes |
| `internal/app/server.go` CSS | `.brand::after{content:"Gaming"}` |
| `internal/app/config.go` | Historically defaulted `PORTAL_PUBLIC_BASE_URL` to a private domain. Now required with no default. |
| `internal/app/diag_plugin.go` | Plugin path `bepinex/plugins/neuralyzevrfixes/` |
| `deploy/` | The reference config named for a private domain has been removed; `deploy/nginx-portal.conf.example` and `deploy/Caddyfile` are the neutral samples. |

The installer no longer depends on any of it. `compose.yaml` requires
`PORTAL_PUBLIC_BASE_URL` and `VALHEIM_WORLD_ROOT` explicitly, and `config.go`
no longer supplies a host-specific default. The compiled-in assets and the
module path remain.

*Fix:* move branding to a theme directory resolved at runtime with a neutral
built-in default. The VR-fix plugin path is a compatibility contract with
published profiles — renaming it breaks existing installs, so it needs a
migration, not a rename. The module path can only change with a major version.

### The Windows client is unsigned and registers a URL scheme

`ValheimProfileSync.exe` is built unsigned and registers the
`valheim-profile-sync://` protocol handler. At public scale that means
SmartScreen and antivirus friction on every download, and a URL scheme is
attack surface: any web page can invoke it with attacker-chosen arguments.

*Fix:* Authenticode signing with a timestamp, and an explicit audit of protocol
argument parsing against hostile input. The client already validates checksums
and refuses to displace another mod loader, so the pattern is established.

### Host assumptions

The installer requires Linux, systemd, Docker with Compose v2, and Go 1.26.5 or
newer, and uses `useradd`/`usermod` and `/usr/local/bin`. That covers Debian and
Ubuntu and excludes non-systemd distributions. Now declared in
[prerequisites.md](prerequisites.md) rather than discovered.

The agent's `docker` group membership is root-equivalent on the host. This is
inherent to managing containers and is correctly confined by a fixed operation
table, `ProtectSystem=strict`, and `ReadWritePaths` limited to the world root —
but a public audience deserves it stated plainly.

### Single-tenant by construction

`AGENT_ALLOWED_WORLDS` is a static environment allowlist requiring an agent
restart to change, administration is one undifferentiated identity, and storage
is SQLite on a local volume. Fine for one operator and one host; not a hosted
multi-tenant service. Declare the intended scope so nobody discovers it at
scale.

## Already correct

Worth recording, because these are the parts that make the rest worth fixing:

* **Privilege separation.** The portal container holds no Docker socket and
  runs read-only with `no-new-privileges`. All privileged work goes through a
  fixed operation table behind an HMAC socket. The world mount is read-only —
  which stops writes, not reads; see [threat-model.md](threat-model.md).
* **Secret handling.** Both halves require ≥32 bytes and trim consistently, so
  a trailing newline cannot silently break HMAC pairing. The installer
  generates 32 bytes at 0640 and never regenerates an existing secret.
* **Schema migrations exist.** `internal/app/store.go` keeps a
  `schema_migrations` table with versioned, additive migrations through version
  7, so upgrades are already handled rather than improvised.
* **Steam auth needs no API key.** `internal/app/steam_auth.go` uses OpenID 2.0
  `check_authentication` against `steamcommunity.com` and pins the claimed-ID
  format, so operators need no Steam credential. [INFERENCE] The long-term risk
  is that OpenID 2.0 is a legacy protocol Steam has never modernised.
* **Destructive operations are gated.** Deletion and restore need typed,
  actor-bound confirmations; releases are archived rather than deleted; the
  audit trail is immutable.
* **Artifact integrity.** SHA-256 and size are re-verified at publish and at
  client install, with archive allowlists and scope binding.

## Suggested order

1. ~~Vendor the operation scripts.~~ Superseded: both repositories are
   published and the required layout is a documented contract. Independent
   versioning remains; pin both.
2. ~~Decide the license.~~ Done: AGPL-3.0 in both repositories. Mod
   redistribution is still open and is now the last legal blocker.
3. ~~Add first-class admin authentication.~~ Done twice over:
   `PORTAL_ADMIN_TOKEN_FILE` plus the proxy headers, then
   `PORTAL_ADMIN_STEAM_IDS`, which authorises the signed-in Steam identity and
   takes the proxy out of the path. The proxy factors survive as break-glass.
4. Settle mod redistribution — enumerate package licences, or have clients
   fetch from Thunderstore against the hashes the portal already verifies.
5. Extract branding to a runtime theme.
6. Sign the Windows client and audit the URL scheme.

Step 4 is the remaining prerequisite for hosting profile payloads publicly.
Steps 5 and 6 are what a fork and a first-time downloader respectively run into.
