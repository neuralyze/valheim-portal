# Prerequisites

What you need before `scripts/install-portal.sh` can produce a working deployment.
The installer checks most of this and reports every missing item at once, but three
things it cannot check for you — DNS, TLS, and a Windows build host — are the ones
that block a release rather than an install.

Read [repository-layout.md](repository-layout.md) first: it names the two paths that
stay outside this repository and must be configured.

## Build and run the portal

| Requirement | Why |
|---|---|
| **Go 1.26.5 or newer** | `go.mod` declares `go 1.26.5`. The installer builds the agent binary from source, and `scripts/build-windows-client.sh` cross-compiles the client. |
| **Docker with Compose v2** | The portal ships as a container; `compose.yaml` uses Compose v2 syntax and the installer invokes `docker compose`, not `docker-compose`. |
| **`setfacl` (the `acl` package)** | The installer grants the `valheim-agent` user write access to `<world>/config_merged/`, `<world>/valheim.env`, and the world root with POSIX ACLs rather than by loosening group bits. Without it the installer warns and continues, and **Apply access lists** then fails with the lists left unchanged. |
| **`git`** | Not needed to run, but `scripts/build-windows-client.sh` derives the build identity from `PORTAL_VERSION`, else `git describe --tags --always --dirty`, else the literal string `dev`. On a host without git it succeeds and silently produces a client stamped `dev`, which cannot be matched to a release in a support report. Set `PORTAL_VERSION` explicitly if you build without git. |
| **Linux with systemd** | The host agent is a systemd unit running as a dedicated user. Non-systemd distributions are not supported. |
| **`python3`, `curl`, `zip`, `unzip`, and either `openssl` or `/dev/urandom`** | Used by the installer and by the host operation scripts. |
| **A `valheim-server-docker` checkout** | The world operation scripts drive the compose project in a checkout of the modified [valheim-server-docker](https://github.com/lloesche/valheim-server-docker) fork, and `tools/valheim_provision.py` reads its `default.env` for the container PGID. It is a separate Apache-2.0 project and is not vendored here. Set `VALHEIM_SERVER_DOCKER_DIR` to it; there is no default, and a script without it exits 78 naming the variable. |

## Two directories you must decide on

Neither is created for you and neither has a default, because these scripts stop,
delete and overwrite servers: guessing a path could destroy the wrong one.

**`VALHEIM_WORLD_ROOT`** — where your world data lives. One subdirectory per world,
plus a `world_backups` directory the backup scripts manage:

```text
/srv/valheim/
├── MyWorld/
│   ├── valheim.env
│   ├── config_merged/
│   └── data/
├── AnotherWorld/
└── world_backups/
```

**`VALHEIM_SERVER_DOCKER_DIR`** — a checkout of the modified `valheim-server-docker`
fork. It is a separate Apache-2.0 project, not vendored here, and every start, stop and
backup script drives its compose project.

Set both in `deploy/install.conf` and run the installer; it propagates them to the
container and the agent. You will also see `AGENT_WORLD_ROOT` and `VALHEIM_ROOT` in
generated files and error messages — those are the same world directory under the names
the agent and the scripts use internally. You never set them yourself.

## Operator data files you must create

Two files hold the operator's real world names, so they are deliberately untracked and
a fresh clone has neither. Copy both and edit them before running anything:

```sh
cp hostops/worlds.txt.example hostops/worlds.txt
cp deploy/release-targets.json.example release-targets.json
```

**`hostops/worlds.txt`** — one world name per line. The bulk
`*_valheim_servers.sh` scripts and `backup_valheim_worlds.sh` iterate exactly this
list. A world missing from it is never backed up, and nothing reports that: the backup
run succeeds having quietly skipped it. Reconcile the file whenever you add or rename
a world.

**`release-targets.json`** — schema 1, one entry per published edition, naming the
shared profile it is built from, the published name, `valheim_vr` and `audience`.
Four editions per world is the shape here; the example ships that shape for one world.
`scripts/build-flat-release-plan.sh` reads
the `flat` array to decide what to build (override with its optional fifth argument);
`tools/valheim_mods.py` reads both arrays to build the client-release
cutover guard. If the file is absent that guard silently returns no targets, so a
package removed from a server raises no cutover and `start_valheim_server.sh` starts
the world anyway — copy the file even before you publish anything.

Neither is checked by `install-portal.sh`.

## Per-world server requirements

Each world the agent controls needs its own environment file and its own ports.

**A per-world `valheim.env`.** `<world-root>/<WORLD>/valheim.env`, mode 0600, holding at
minimum `WORLD_NAME`, `SERVER_NAME`, `SERVER_PASS`, `SERVER_PORT`,
`CONTAINER_VALHEIM_PORT`, `CONFIG_DIR`, and `DATA_DIR`. `tools/valheim_provision.py`
writes a complete one when the creation wizard provisions a world; an imported world
needs one written by hand. The agent refuses any world without it.

**`STATUS_HTTP=true`.** Required for the portal to report world status at all. See
[operations.md](operations.md#world-status).

**Five host ports per world**, all allocated by `tools/valheim_provision.py` and
recorded in that world's `valheim.env`:

| Ports | Protocol | Variable | Notes |
|---|---|---|---|
| `<base>` and `<base>+1` | UDP | `CONTAINER_VALHEIM_PORT="<base>-<base+1>"` | Game traffic and the Steam A2S query port. Exactly two: the container publishes `2456-2457/udp`, and Docker Compose rejects the service outright if the host range is a different size. |
| one in 30000-39999 | TCP | `CONTAINER_STATUS_PORT` | Status HTTP server. |
| one in 40000-44999 | TCP | `SUPERVISOR_PORT` | supervisord, normally left unpublished. |
| one in 20000-29999 | TCP | `DISCORD_BOT_PORT` | Discord bridge, off by default. |

Only the two UDP ports need to be reachable from the internet. The port range is
shared across worlds, so `hostops/configure_valheim_port.sh` takes a host-wide lock
and rejects a range that overlaps another world's.

## Network and identity

**DNS and TLS.** `PORTAL_PUBLIC_BASE_URL` is required and has no default. It must be
the exact HTTPS origin players reach, because Steam OpenID uses it verbatim for the
authentication callback — a mismatch fails every sign-in. You therefore need a DNS
name pointing at the host and a certificate for it before the first login can succeed.

**A reverse proxy terminating TLS.** The portal binds `127.0.0.1` and the installer
refuses a public bind without `--allow-public-bind`. The proxy is also part of the
authentication path: it must set the identity header and the admin token header on
administrative routes, and clear the identity header on player routes. See
[installation.md](installation.md#the-security-model). `deploy/nginx-portal.conf.example`
and `deploy/Caddyfile` show both patterns.

`PORTAL_AUTHENTICATOR_URL` is a **Caddy-only key**. Nothing in the Go code reads it;
`deploy/Caddyfile` substitutes it into `forward_auth {$PORTAL_AUTHENTICATOR_URL}`, so
it names your identity provider's verification endpoint. Ignore it on an nginx
deployment.

**Outbound HTTPS to `steamcommunity.com`.** Steam OpenID authentication is not
optional; without it nobody can sign in.

**A Steam Web API key — optional.** Get one free at
`https://steamcommunity.com/dev/apikey` and set `PORTAL_STEAM_API_KEY`.

What degrades without it: nothing breaks and nobody is locked out, because
authorization keys off the 17-digit SteamID64 alone and never off a name. Persona
names are display metadata only. With the key empty the portal falls back to the
public community profile XML endpoint, which resolves a name only for accounts whose
Steam profile is public — private profiles stay unnamed in the grant form, the
current-access table, and the Steam identities list, so an operator approving a grant
sees a bare 17-digit number. Setting the key switches to the official Steam Web API,
which batches up to 100 IDs per request and resolves private profiles too, and adds an
egress requirement to `api.steampowered.com`. A per-identity manual label
(`POST /admin/steam-identities/label`) overrides any fetched name and needs no network
access, so it is the workaround on a deployment without a key.

**A Windows host for the ValheimVR build.** Cross-compiling the client is enough for
`ValheimProfileSync.exe` — `scripts/build-windows-client.sh` runs on Linux. But
rebuilding `ValheimVRMod.dll` requires building `ValheimVRMod.sln` on Windows against a
disposable Valheim installation, and the resulting archive is the input to
`scripts/build-vr-runtime-artifact.sh`. There is no Linux path for that step. See
[valheimvr-packaging.md](valheimvr-packaging.md). A Windows machine is also the only
way to smoke-test a released client end to end.

## Container resource ceilings

`compose.yaml` caps the portal container at `mem_limit: 1g` and `pids_limit: 256`, and
gives it a size-capped `/tmp` tmpfs. These are not arbitrary: a multipart artifact
upload is held in memory up to 16 MiB and then spills to `/tmp`, and because that is a
tmpfs the spill counts against the container's memory. The `/tmp` size is set to hold
one artifact at the maximum accepted size.

That maximum is **512 MiB per artifact**; a larger body is rejected with HTTP 413. It
is far above any real profile bundle. Raising it means raising the tmpfs size and the
memory limit together, or the container is killed instead of returning an error.

## Not required

* A code-signing certificate. The Windows client is unsigned; users see SmartScreen
  friction and must verify the release SHA-256. Tracked in
  [public-distribution.md](public-distribution.md).
* A Docker socket for the portal container. It has none by design, and must never be
  given one.
