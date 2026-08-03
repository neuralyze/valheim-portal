# Installation

`scripts/install-portal.sh` provisions a complete deployment: the unprivileged
portal container, the privileged host agent, the HMAC secret they share, and the
systemd unit that supervises the agent.

Follow the seven steps below in order. Every step is a command you can paste;
the prose after each block explains only what you must decide or watch for.

## What you end up with

The portal is deliberately two processes with different privilege:

| Component | Runs as | Privilege |
|---|---|---|
| Portal container | `portal` inside the image, loopback-only | No Docker socket, read-only root filesystem, world data mounted read-only |
| Host agent | `valheim-agent` via systemd | Writes world data, manages server containers through the `docker` group |

They communicate over a mode 0660 Unix socket and authenticate every operation
with an HMAC token from a shared file. The portal cannot ask the agent to do
anything outside the agent's fixed operation table, and the agent refuses any
world outside `AGENT_ALLOWED_WORLDS`.

## Before you begin

Every command below must print a version:

```sh
docker --version
docker compose version
go version                        # 1.26.5 or newer
python3 --version
curl --version | head -1
setfacl --version | head -1       # the acl package
systemctl --version | head -1
```

You also need a reverse proxy that terminates TLS and can reach the portal on
loopback. Step 6 configures it.

Versions, and what degrades if an optional piece is missing, are in
[prerequisites.md](prerequisites.md). The installer re-checks all of this and
reports every missing item at once rather than failing on the first.

## Step 1 — Get the code

```sh
git clone https://github.com/neuralyze/valheim-portal.git /srv/valheim-portal
cd /srv/valheim-portal
```

Every remaining command runs from this directory. It is self-contained: the Go
portal, the host operation scripts the agent executes, and the Python tools those
scripts delegate to all live here.

## Step 2 — Provide the valheim-server-docker checkout

Every lifecycle operation — start, stop, pause, unpause, build, logs, status,
remove, shell — drives the compose project in a checkout of
[valheim-server-docker](https://github.com/lloesche/valheim-server-docker). That
is a separate Apache-2.0 project and is **not** vendored here, so you must supply
it:

```sh
git clone https://github.com/lloesche/valheim-server-docker.git /srv/valheim-server-docker
```

Then create `default.env` in that checkout. Upstream does not ship one, and
provisioning reads it for the PGID the container chowns its mounts to:

```sh
cat > /srv/valheim-server-docker/default.env <<'EOF'
PUID=1000
PGID=1000
EOF
```

Set `PGID` to the group that should own world files. A numeric `chmod` on
container start resets POSIX ACL masks, so this value interacts with the ACLs the
installer grants in step 5 — see [operations.md](operations.md#player-access).

The installer refuses to continue if the directory or its `default.env` is
missing, and a lifecycle script run without `VALHEIM_SERVER_DOCKER_DIR` exits 78
naming the variable rather than guessing a compose project to tear down.

## Step 3 — Create the two operator data files

These carry your real world names, so they are untracked and absent from a fresh
clone. Copy both, then edit them:

```sh
cp hostops/worlds.txt.example hostops/worlds.txt
cp release-targets.json.example release-targets.json
```

Both fail **quietly** if you skip this, which is why they come before anything
else:

* A world absent from `hostops/worlds.txt` is never included in a bulk backup,
  and the backup run still reports success.
* An absent `release-targets.json` makes the client-release cutover guard return
  no targets, so a server-side package removal raises no cutover and the world
  starts anyway.

The installer does **not** check either file. Schemas are in
[prerequisites.md](prerequisites.md#operator-data-files-you-must-create).

## Step 4 — Write the deployment configuration

```sh
cp deploy/install.conf.example deploy/install.conf
$EDITOR deploy/install.conf
```

Five values are required and have no defaults:

| Key | What it is |
|---|---|
| `PORTAL_PUBLIC_BASE_URL` | Public HTTPS origin, matching the proxy's certificate |
| `PORTAL_TRUSTED_PROXY_CIDR` | The proxy's exact source address, as the portal sees it, with a `/32` or `/128` prefix |
| `VALHEIM_WORLD_ROOT` | Directory holding one subdirectory per world, plus `world_backups` |
| `VALHEIM_SERVER_DOCKER_DIR` | The checkout from step 2 |
| `AGENT_ALLOWED_WORLDS` | Comma-separated worlds the agent may control; it refuses every other |

Check how the installer resolved them before changing the host:

```sh
./scripts/install-portal.sh print-config --config deploy/install.conf
```

Everything else in the file is optional and commented out. Leave
`AGENT_SCRIPT_DIR` unset unless the operation scripts live outside this
repository — see [the world operation scripts](#the-world-operation-scripts).

One optional key belongs here too, because it decides how you reach the
administration site at all:

| Key | What it is |
|---|---|
| `PORTAL_ADMIN_STEAM_IDS` | Comma-separated SteamID64s allowed to administer the portal once signed in with Steam |

```
PORTAL_ADMIN_STEAM_IDS=76561198000000001,76561198000000002
```

A request carrying a Steam session for one of those IDs may administer the
portal, and the portal decides that itself — no proxy assertion is involved.
Leaving the key empty or unset means there are no Steam operators, which
preserves the previous behaviour exactly: the proxy factors in
[the security model](#the-security-model) are then the only way in. Setting it
is what puts the **Administration** link in front of you without your having to
know a URL, and it changes what step 6 must configure. See
[reaching the administration site](operations.md#reaching-the-administration-site).

One more optional key belongs in the same file, and it is a licence obligation
rather than a preference:

| Key | What it is |
|---|---|
| `PORTAL_SOURCE_URL` | Where the player-facing pages link for this program's source. Absolute http or https URL |

```
PORTAL_SOURCE_URL=https://git.example.com/your-org/valheim-portal
```

The player pages carry a source-code link because this is AGPL-3.0 network
server software: section 13 obliges anyone running a **modified** version as a
service to offer its users the corresponding source. The default names the
upstream project, which is a truthful offer only while you run it unmodified.
**If you deploy local changes, point this at the repository that holds them** —
otherwise the link tells your players something false about what they are
running. A value the browser cannot follow is refused at startup, because an
offer that looks discharged and leads nowhere is worse than none.

## Step 5 — Install

Preview first. This changes nothing:

```sh
sudo ./scripts/install-portal.sh install --config deploy/install.conf --dry-run
```

Resolve every reported problem, then install:

```sh
sudo ./scripts/install-portal.sh install --config deploy/install.conf
```

The run is idempotent: re-running upgrades the binary and regenerates derived
configuration while preserving secrets. See
[what the installer does](#what-the-installer-does).

## Step 6 — Configure the reverse proxy

**Nothing is reachable until this step is done**, including Steam sign-in: the
portal binds to loopback and the proxy is what terminates TLS on
`PORTAL_PUBLIC_BASE_URL`. The portal also cannot detect a proxy that is merely
absent — it can only refuse requests that arrive without proof. Start from the
shipped example:

```sh
sudo cp deploy/nginx-portal.conf.example /etc/nginx/sites-available/valheim-portal.conf
sudo ln -s /etc/nginx/sites-available/valheim-portal.conf /etc/nginx/sites-enabled/
sudo $EDITOR /etc/nginx/sites-available/valheim-portal.conf   # server_name, certificates
```

The administrative location must send the admin token. Put it in its own file
rather than in the site, because sites are conventionally `0644` and an inlined
token is readable by every local user:

```sh
printf 'proxy_set_header X-Portal-Admin-Token "%s";\n' \
  "$(sudo cat /etc/valheim-portal/admin-token)" \
  | sudo tee /etc/nginx/snippets/valheim-portal-admin-token.conf >/dev/null
sudo chown root:www-data /etc/nginx/snippets/valheim-portal-admin-token.conf
sudo chmod 0640 /etc/nginx/snippets/valheim-portal-admin-token.conf
```

Give the administrative location a verified identity and that snippet, and blank
both headers on player routes:

```nginx
location ^~ /admin {
    auth_basic "Valheim portal administration";
    auth_basic_user_file /etc/nginx/valheim-portal.htpasswd;
    proxy_set_header X-Forwarded-User $remote_user;
    include snippets/valheim-portal-admin-token.conf;
    proxy_pass http://127.0.0.1:18080;
}

location / {
    proxy_set_header X-Forwarded-User "";
    proxy_set_header X-Portal-Admin-Token "";
    proxy_pass http://127.0.0.1:18080;
}
```

**Delete the two `auth_basic` lines if `PORTAL_ADMIN_STEAM_IDS` is set.** nginx
would otherwise demand a password before the portal ever sees the request, so
an allowlisted operator is stopped at the door by the very proxy the allowlist
exists to bypass:

```diff
 location ^~ /admin {
-    auth_basic "Valheim portal administration";
-    auth_basic_user_file /etc/nginx/valheim-portal.htpasswd;
     proxy_set_header X-Forwarded-User $remote_user;
     include snippets/valheim-portal-admin-token.conf;
     proxy_pass http://127.0.0.1:18080;
 }
```

This does not expose administration. Without `auth_basic`, `$remote_user` is
empty, so the identity factor fails and the proxy grants nothing at all; the
portal authorises against the signed-in Steam identity and the allowlist, and
returns 401 to everyone else. Leave the token snippet and the
`proxy_set_header` line in place either way — both are harmless when the
identity is empty, and putting the two `auth_basic` lines back restores the
break-glass path in one reload.

Then reload:

```sh
sudo nginx -t && sudo systemctl reload nginx
```

Do not leave a backup copy in `sites-enabled/`: nginx includes every file there,
so a stray `.bak` loads as a second server block for the same name. `deploy/Caddyfile`
shows the same patterns for Caddy, which reads the token from its environment
instead. Read [the security model](#the-security-model) before serving traffic.

## Step 7 — Verify end to end

```sh
sudo ./scripts/install-portal.sh verify --config deploy/install.conf
```

* `GET /healthz` — the portal is serving.
* `GET /readyz` — the portal reached the agent socket, proving the socket group
  and token pairing are correct.
* **Identity spoofing probe** — requests `PORTAL_PUBLIC_BASE_URL/admin` while
  supplying its own `X-Forwarded-User` and `X-Portal-Admin-Token`. A 401, 403,
  or 404 is the expected result. A 2xx or 3xx is reported as CRITICAL: the proxy
  is forwarding browser-controlled administrative credentials instead of
  overwriting them.

Confirm administration works for a real operator too, since the probe only proves
that a forged identity is rejected:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' -u <operator> https://<your-host>/admin
```

Run `verify` again after any proxy change. It is the only check that exercises
the real trust boundary end to end.

## What the installer does

1. **Preflight** — tools, configuration, the security gates below, world root,
   the 20 operation scripts, and the world allowlist.
2. **Agent identity** — creates the `valheim-agent` system user and group and
   adds the supplementary groups it needs.
3. **Secrets** — generates a 32-byte CSRF secret, agent token, and admin token
   as mode 0640 `root:valheim-agent`. **Existing secrets are always
   preserved**: rotating the CSRF secret invalidates live sessions, rotating
   the agent token breaks the portal/agent pair until both restart, and
   rotating the admin token locks administration out until the proxy is
   reloaded with the new value. A secret shorter than 32 bytes is a hard error
   rather than a silent regeneration.
4. **Agent binary** — builds with `CGO_ENABLED=0 -trimpath -buildvcs=false` and
   installs `/usr/local/bin/valheim-portal`.
5. **Agent environment** — writes `/etc/valheim-portal/agent.env` (0640).
6. **Agent service** — renders and installs the systemd unit. Paths come from
   configuration; nothing host-specific is baked into the repository.
7. **Compose environment** — writes `.env` beside `compose.yaml`, containing
   paths, policy, the resolved agent GID, and the optional
   `PORTAL_STEAM_API_KEY` if one is configured. No generated secrets. An
   existing file is copied to `.env.replaced` first.
8. **Start** — enables the agent, waits for its socket, then brings up the
   container.
9. **Verify** — as in step 7.

### World data access

The portal generates `adminlist.txt` and `permittedlist.txt` from its own grant
records, so `access_apply` writes `<world>/config_merged/` and
`<world>/valheim.env` under the world root as `valheim-agent`, and the
server-creation wizard stages new world directories in the world root itself.
The installer grants exactly that with ACLs, for the world root and for every
world named in `AGENT_ALLOWED_WORLDS`, rather than by loosening the group bits
on world data:

```text
sudo setfacl -m u:valheim-agent:rwx <world-root> <world> <world>/config_merged
sudo setfacl -d -m u:valheim-agent:rwx <world-root>
sudo setfacl -m u:valheim-agent:rw <world>/valheim.env
```

The default ACL on the world root makes later worlds inherit the same access.
The step is idempotent and re-running the installer repairs a lost ACL; if
`setfacl` is unavailable the installer warns and continues, and **Apply access
lists** then fails with the lists left unchanged. The script never chowns; the
container fixes ownership on start. See
[operations.md](operations.md#player-access).

### The world operation scripts

The agent does not implement world operations itself. It executes a fixed
script per operation from `AGENT_SCRIPT_DIR`, and it derives that list from
the `operations` map in `internal/agent/agent.go`. That map has 35 operations
resolving to **20 distinct scripts** — `start_valheim_server.sh`,
`backup_valheim_world.sh`, `provision_valheim_server.sh`, `portal_mod_admin.sh`,
`portal_access_lists.sh`, and fifteen more. Three further operations
(`world_catalog`, `world_analysis`, `access_state`) are marked `@internal` and
run inside the agent with no script at all. Several operations deliberately
share a script: every `mod_*` operation runs `portal_mod_admin.sh`, and both
`stop` and `restart` run `stop_valheim_server.sh`.

They ship in this repository as `hostops/`, which is the default, so leave
`AGENT_SCRIPT_DIR` unset unless the scripts are installed elsewhere. An override
must name a directory laid out like `hostops/` — the scripts, `lib/common.sh`,
and a sibling `tools/` directory — because the scripts resolve their Python
helpers from it. The installer verifies all of that and refuses to continue
otherwise:

```
AGENT_SCRIPT_DIR is missing 20 world operation scripts: backup_valheim_world.sh ...
AGENT_SCRIPT_DIR has no sibling tools/ directory: /srv/tools ...
```

## The security model

Read this before exposing the portal.

There are two ways a request may administer the portal. It is refused unless
one of them holds completely.

**Path A — the Steam operator allowlist.** The request carries a valid Steam
session whose SteamID64 is listed in `PORTAL_ADMIN_STEAM_IDS`. The portal
verifies this end to end by itself: the identity comes from a completed Steam
OpenID sign-in, and the list is deployment configuration no request can
influence. Nothing about the network path is trusted, so a misconfigured proxy
cannot forge it. With the key empty or unset there are no Steam operators and
this path never grants anything.

**Path B — the proxy, kept as break-glass.** All three of the following, and
the portal refuses the request if any one is missing:

1. a source address inside `PORTAL_TRUSTED_PROXY_CIDR`,
2. a non-empty `PORTAL_AUTH_HEADER` (default `X-Forwarded-User`), and
3. an `X-Portal-Admin-Token` request header equal to the contents of
   `PORTAL_ADMIN_TOKEN_FILE`, compared in constant time.

Only the third is verified by the portal itself. The first two are assertions
the proxy makes, and the proxy is what injects the third — a browser never
sends it and never sees it.

Path B is retained rather than removed because it is the way back in when Steam
OpenID is unreachable, when the allowlist is wrong, or when no operator has
signed in yet. Everything below about the proxy therefore still applies in
full, whether or not you use path A day to day.

**Why the third factor exists.** Under the shipped compose deployment Docker
NATs every inbound request to the bridge gateway address, and that gateway is
what the trusted range names. The network half of the check was therefore
unconditionally true for every request, which left a single unverified header
standing between the internet and world deletion. The installer's preflight now
rejects a `PORTAL_TRUSTED_PROXY_CIDR` equal to the bridge gateway `/32` for the
same reason.

The portal will not start if `PORTAL_ADMIN_TOKEN_FILE` is unset, unreadable, or
holds fewer than 32 bytes after trimming. There is no header-only fallback.

**The audit log records which path authorised.** Actor is the identity header
for path B and `steam:<steamid64>` for path A, so every privileged action stays
attributed to a person and it is visible afterwards whether the proxy or the
allowlist let them in.

**The per-world `admin` role is not portal administration.** The admin role
granted in **Player access** writes a Steam ID into that world's
`adminlist.txt` — in-game powers on one server. It grants nothing in the
portal, and no portal permission is derived from it.

**The trusted range must still be exactly the proxy.** A range covering more
hosts means any of them can attempt administration with a guessed or leaked
token. The installer rejects `0.0.0.0/0`, publicly routable ranges, and
anything wider than a small subnet; `--allow-broad-proxy-cidr` overrides this
deliberately.

**The proxy must set both headers on every route it forwards.** nginx forwards
unrecognised client request headers upstream unchanged, so omitting
`proxy_set_header X-Forwarded-User` is not a neutral default — it is one half of
an authentication bypass. Verified directly:

```
# location without proxy_set_header, client sends X-Forwarded-User: attacker-supplied
upstream-saw:'attacker-supplied'
# same request, location with proxy_set_header X-Forwarded-User ""
upstream-saw:None
```

**Do not paste the token into the site file.** nginx sites are conventionally
`0644`, so an inlined token is readable by every local user, which defeats the
factor it provides. Keep the single `proxy_set_header` directive in a snippet
owned `root:www-data` at `0640` and `include` it from the administrative
location, as step 6 does. Caddy reads the value from its environment instead, so
protect the environment file the same way.

**The proxy must also set `X-Forwarded-For`.** Rate limiting keys on the
left-most entry when the request arrives from the trusted proxy, and falls back
to the remote address otherwise. Without it every client shares one bucket, and
the portal logs a loud warning saying so. Device-token polling has its own
bucket.

The installer keeps the portal bound to `127.0.0.1` and refuses a public bind
without `--allow-public-bind`, because that port is the last barrier if the
proxy is misconfigured.

## Upgrades

```sh
git pull
sudo ./scripts/install-portal.sh install --config deploy/install.conf
```

The agent restarts and the container is rebuilt. Secrets survive, so sessions
and the portal/agent pairing are unaffected.

## Uninstall

```sh
sudo ./scripts/install-portal.sh uninstall           # keep secrets and data
sudo ./scripts/install-portal.sh uninstall --purge   # also remove them
```

Neither form touches world data or backups. `--purge` removes only the files
the installer created, so unrelated material in `/etc/valheim-portal` such as a
proxy password file survives.

## Migrating an existing deployment

`compose.yaml` no longer defaults `VALHEIM_WORLD_ROOT` or
`PORTAL_PUBLIC_BASE_URL` to one specific host. A `.env` written before this
change may omit `VALHEIM_WORLD_ROOT`, and `docker compose up` will stop with
`set the Valheim world root`. Run the installer once to regenerate `.env`; your
previous file is kept as `.env.replaced`.

### The world operation scripts moved into this repository

They used to live in a separate `ValheimConfig` checkout at
`ValheimConfig/scripts`, and a deployment installed before that move still has
that path in `/etc/valheim-portal/agent.env`. The running agent reads that file,
not this repository, so upgrading the checkout alone leaves it executing scripts
that are no longer there and every operation fails with `operation unavailable`.

`./scripts/install-portal.sh verify` detects it and prints the exact lines to
change. There are two: the script directory, and the compose checkout that used
to be resolved relative to the scripts.

```diff
 # /etc/valheim-portal/agent.env
-AGENT_SCRIPT_DIR=/path/to/ValheimConfig/scripts
+AGENT_SCRIPT_DIR=/path/to/valheim-portal/hostops
+VALHEIM_SERVER_DOCKER_DIR=/path/to/valheim-server-docker
```

```sh
sudo systemctl restart valheim-portal-agent
```

Re-running the installer does both, and also regenerates the systemd unit —
whose `ReadOnlyPaths` must now cover `tools/` and the `valheim-server-docker`
checkout as well. Prefer it to a hand edit unless the unit is already current.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `readyz` fails | The container cannot reach the socket. Check `PORTAL_AGENT_GID` matches the agent group and that the agent is running. |
| `agent token must contain at least 32 bytes` | The token file is truncated. Stop both halves, remove it, re-run the installer, restart both. |
| Agent socket never appears | `journalctl -u valheim-portal-agent`. Usually a missing operation script or an unreadable `AGENT_SCRIPT_DIR`. |
| `/admin` returns 401 for a real operator | With `PORTAL_ADMIN_STEAM_IDS` set: they are not signed in with Steam, or their SteamID64 is not in the list. Otherwise: the proxy is not sending `X-Portal-Admin-Token`, or not setting `X-Forwarded-User`. Re-check step 6. |
| A password prompt appears before `/admin` | `auth_basic` is still in the administrative location. Remove it when using the Steam allowlist; step 6 has the diff. |
| Admin routes return 401 from the proxy | The proxy's source address is outside `PORTAL_TRUSTED_PROXY_CIDR`. |
| Spoofing probe reports CRITICAL | A proxied location forwards without setting the header. Fix before serving traffic. |
| `conflicting server name` from nginx | A backup copy of the site is still in `sites-enabled/`; nginx includes every file there. |
| `VALHEIM_SERVER_DOCKER_DIR holds no default.env` | Step 2 was skipped. Provisioning reads the container PGID from that file. |
| `PORTAL_PUBLIC_BASE_URL is required` from `verify` | `verify` was run without `--config`. |
| `multiple VCS detected` during a manual build | The checkout sits inside another repository. The installer passes `-buildvcs=false`. |
