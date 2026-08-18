# Operations

## Deploy the portal

The portal container and privileged host agent are separate deployables that
share an HMAC secret. `scripts/install-portal.sh` updates the agent first, then
rebuilds the unprivileged portal, and preserves existing secrets:

```sh
cd "$(git rev-parse --show-toplevel)"
sudo ./scripts/install-portal.sh install --config deploy/install.conf --dry-run
sudo ./scripts/install-portal.sh install --config deploy/install.conf
```

It renders the systemd unit from the deployment configuration, so no host paths
are committed. `deploy/valheim-portal-agent.service.example` documents the unit
for reference and is not installable as-is. First-time setup, the security
model, and the migration note for a `.env` predating required
`VALHEIM_WORLD_ROOT` are in [installation.md](installation.md).

Verify after any change to the deployment or the reverse proxy:

```sh
sudo ./scripts/install-portal.sh verify
```

Beyond `/healthz` and `/readyz`, this probes the public origin for identity
spoofing. A proxied route that forwards a client-supplied `X-Forwarded-User`
instead of blanking it hands a stranger one of the factors the proxy
authorisation path needs, so it is half a bypass rather than a harmless
omission — only the admin token is then left standing in the way. Treat a
CRITICAL result as blocking.

The portal remains loopback-only behind the authenticated HTTPS reverse proxy. Do not expose its admin routes directly. The portal does not mount Docker's socket and must not modify Valheim worlds, server mods, or save files.

## Reaching the administration site

Browse to the portal, sign in with Steam, and the **Administration** link is
there if your SteamID64 is listed in `PORTAL_ADMIN_STEAM_IDS`. Nothing else is
required: no password prompt, no URL to memorise, no session to renew. The list
is deployment configuration rather than data, so adding or removing an operator
is an edit to the portal's environment and a restart — deliberately not
something the running portal can grant itself.

This replaced an entry point that was gated behind itself. The link used to
appear only once the browser held a 12-hour admin cookie, and the only way to
get that cookie was to visit `/admin` directly. The header could never offer
the link, because the proxy deliberately blanks the admin headers on player
routes, so on the pages an operator actually browses the portal had no way to
tell an operator from any other signed-in player. You had to already know the
URL in order to be shown the URL, and twelve hours later the link disappeared
again with no explanation. Authorising on the signed-in Steam identity removes
both the circularity and the silent expiry.

**The in-game admin role is a different thing and grants no portal access.**
The per-world `admin` role in **Player access** writes that Steam ID into the
world's `adminlist.txt`, which is in-game power on that one server — kick, ban,
no-cost mode. It confers nothing in the portal. Portal administration comes
from `PORTAL_ADMIN_STEAM_IDS` alone, and the two lists are kept apart on
purpose: trusting someone with a ban hammer in one world is not the same as
trusting them to delete it.

Leaving `PORTAL_ADMIN_STEAM_IDS` empty means there are no Steam operators, and
administration then works exactly as it did before — through the reverse proxy
only. That path is retained either way as break-glass, for when Steam OpenID is
unreachable or the allowlist is wrong. Both paths, and the nginx `auth_basic`
change the allowlist requires, are in
[installation.md](installation.md#the-security-model).

## Mod profiles

A mod profile is **one shared definition, stored once**. It lives at
`<fleet>/profiles/<name>`, belongs to no world, and is the single place that mod set is
edited. A server *links* to a profile by name in `<world>/mods/.active-mod-profile`;
several servers may link to the same one, and editing a profile changes what every
linked server runs **at that server's next restart** — a link is a reference, not a
copy, so there is nothing to propagate and nothing to forget to propagate.

This replaced four unrelated 2.1 GB copies, one inside each world, with no record of
which came from which. Changing a mod across the fleet was four edits, and the copies
drifted: one world silently excluded VNEI, another was missing a plugin the rest had.

A copy is the other operation, and it is the opposite of a link: `copy` produces an
independent profile from the moment it exists, with no reference back to its source.
Two servers that must diverge get two profiles; two servers that must agree share one.

### The three primaries

| profile | packages | what it is |
|---|---|---|
| `flat` | 101 | the base set. Monitor and mouse, no VR fixes, no admin tools |
| `vr` | 103 | base plus the three `geekstreet-*VRFix` packages, minus `MSchmoecker-VNEI` |
| `admin` | 111 | base plus the ten admin tools |

`vr` drops VNEI because its UI needs a keyboard and a mouse to search, which a headset
does not have; VHVR maps controllers to ZInput game actions only, so a typed search box
is unreachable there. It is excluded rather than merely absent, so nothing re-adds it.

`admin` adds `JereKuusela-{Server_devcommands,Infinity_Hammer,Structure_Tweaks,World_Edit_Commands,Upgrade_World}`,
`Azumatt-PerfectPlacement`, `Neobotics-RuinsMaker`, `Tristan-ValheimRcon` and the
client-only `sighsorry-{AdminQoL,LoadTimeProfiler}`.

**Every server links to `admin`, because it is the superset a server must run.** Only
four packages in the fleet enforce client presence — `FearMe`, `OdinArchitect` and
`ZenRaids` through Jotunn's `EveryoneMustHaveMod`, and `ZenWorldSettings` through
`ClientMustHaveMod` — and all four are base packages, present in all three primaries.
That is what makes the split safe: a player on `flat` and an operator on `admin` can
join the same server.

### The four published editions

Each server publishes four client editions, built from those three profiles and declared
in `release-targets.json`:

| published name | built from | client type | ValheimVR | audience |
|---|---|---|---|---|
| `<world>-vr` | `vr` | vr | yes, as the VR runtime artifact | player |
| `<world>-vr-flat` | `flat` | flat | yes, as the Flat companion artifact | player |
| `<world>-non-vr` | `flat` | flat | no | player |
| `<world>-vr-flat-admin` | `admin` | flat | yes, as the Flat companion artifact | admin |

**The "vr" in `vr-flat` does not come from a package.** `ValheimVR-ValheimVR` is
excluded from every profile manifest; VR reaches a client only as a separately built,
checksum-validated artifact — the `vr_runtime` for a headset, the Flat companion for a
monitor. So `vr-flat` and `non-vr` are built from the same `flat` profile and differ
only in whether that companion is attached.

Which is why `valheim_vr` is declared per target rather than inferred from the profile
name: inferring it once shipped ValheimVR to `non-vr` players, who had asked for the
edition that does not have it.

`audience` is the other required field, `player` or `admin`. It is carried into the
`releases.audience` column when the release is published, and the portal offers an
`admin` edition **only to an admin login** (`PORTAL_ADMIN_STEAM_IDS`); an ordinary
player's world page does not list it. Without that, the four cards on a world page would
put a working developer console in front of everyone who can see the world.

**It is not recorded in the published profile definition.** On 2026-08-17 it was, and
every player's install failed to parse the definition — see
[release-format.md](release-format.md#the-definition-format-is-frozen).

### Creating, copying and linking

```bash
python3 tools/profile_store.py list                    # profiles, package counts, linked servers
python3 tools/profile_store.py linked <World>          # which profile one server runs
python3 tools/profile_store.py create <name>           # a new profile with no mods
python3 tools/profile_store.py copy <source> <name>    # an independent duplicate
python3 tools/profile_store.py delete <name>           # refused while any server links to it
python3 tools/profile_store.py link <World> <name>     # point one server at a profile
```

The same operations are on the mod controller, which is the form the agent verbs and the
admin site use:

```bash
python3 tools/valheim_mods.py --world <World> profile link <name>
python3 tools/valheim_mods.py --world <World> profile list      # marks that world's profile with *
python3 tools/valheim_mods.py --profile <name> list             # act on a profile directly
```

Every mod operation names the profile it acts on, either with `--profile <name>` or with
`--world <World>`, which resolves to whatever that world is linked to. A world with no
link is an error naming the `profile link` command rather than a guess.

A new server is created and *then* linked; it is never populated from another server. The
New Server form's mod field is **"Profile this server runs"**: it defaults to the profile
the most servers already run, and annotates each choice with how many servers run it.
Name a profile that exists and this server links to it; name one that does not and it is
created empty; name one that does not and pass a profile to copy from, and it starts as an
independent copy of that one. `tools/valheim_provision.py` takes that as its `copy_from`
positional.

A profile no server runs is normally an **edition source** rather than a mistake, which is
what the counts are for. `flat` and `vr` exist only as sources for published client
editions; `admin` is the only profile servers link to, because it is the superset —
linking a server to `flat` or `vr` would strip the admin tools that have to be
server-side.

### A profile owns its server settings

A profile may also carry the server side of its configuration. `<profile>/server-config/`
holds `*.cfg` files that are **canonical**: `deploy --apply` places them onto every linked
server, keeping what was there at
`<world>/mods/deployment-backups/<profile>/server-config.previous/`. A profile that
declares none behaves exactly as before — the plugins write their own on first run — so
this is additive rather than a migration.

`admin` was seeded from Hrafnheim's live settings, 108 cfgs. To do the same for another
profile:

```bash
python3 tools/migrate_profiles.py seed-server-config <profile> --from <World>
```

It takes only `*.cfg` directly under that world's `config_merged/bepinex` — the `plugins/`
subtree is the mods' own files, not settings — and refuses to overwrite settings a profile
already declares, so it cannot silently replace a curated set with one world's copy.

### When one server needs a different value

**Override the setting; do not fork the profile.** A second profile makes the whole mod
list diverge, and diverging mod lists are what the shared store exists to end. Two
override directories layer over the shared values instead:

| path | layered over | applied at |
|---|---|---|
| `<world>/mods/overrides/server/<file>.cfg` | the profile's `server-config/` | deploy |
| `<world>/mods/overrides/client/<file>.cfg` | the profile's client config | publish |

**The merge is per key, not per file** (`tools/config_merge.py`). An override file carries
only the keys that differ; every other key still comes from the profile, so a later
profile change still reaches that server. This is the whole point. Overriding one line
used to mean copying a 700-line config, and that copy then silently kept yesterday's
defaults for every other setting in the file — the same drift that left four worlds with
four different mod sets.

The merge keeps the profile's own text, comments, order and spacing, and replaces only the
overridden values, so a diff against the profile shows exactly what that server changed. A
key the profile does not define is appended under its section with a marker comment.

A file that is not INI cannot be key-merged — `Azumatt.FastLink_servers.yml`, for
instance. An override replaces those whole, and the tools report it as `(whole file)` so
that the stronger, drift-prone form is visible rather than assumed.

**An override never changes the mod list.** Packages, versions and scopes come from the
profile alone; a server that needs a different mod set needs a different profile.

### Recovering a setting a removal deleted

Removing a mod deletes its config files. `<fleet>/settings-history` is a git store
holding **only the text an operator owns**, mirrored out of the live trees on every
mutating mod operation. What it tracks, under the paths it stores them at:

| in the store | from |
|---|---|
| `profiles/<name>/profile-manifest.json` | the profile's package selection |
| `profiles/<name>/client-config*/…` | the settings a player's download carries |
| `profiles/<name>/server-config/…` | the profile's canonical server settings, as typed |
| `<World>/overrides/…` | `<world>/mods/overrides` — what this one server overrides |
| `<World>/server-config/…` | that server's merged `config_merged/bepinex` result |
| `<World>/.active-mod-profile` | which profile that server runs |
| `<World>/access/{adminlist,permittedlist,bannedlist}.txt` | `<world>/config_merged` — who may administer or join |

Only files whose suffix is `.cfg`, `.yml`, `.yaml`, `.json` or `.txt` count as settings.
Both the profile's `server-config/` and each server's merged result are tracked: recording
only the merge left the two files an operator actually types in unversioned, and recording
only the sources could not answer what a server was really running.

**`valheim.env` is deliberately never versioned.** It holds the server password and the
rest of a world's production secrets, and this store is an ordinary git repository that
gets pushed — versioning it would put those secrets in history permanently.

It also does not hold `manager-cache/`, or the DLLs and cfgs inside a package: those
belong to the mod, and the cache is 2.1 GB per profile and reproducible from the manifest.
Under a world's merged tree, `plugins/` is a mod's own files and `SullysAutoPinnerFiles/`
is data the pinner rewrites continuously; both are excluded as not-settings. Nor does it
hold the saved copies of configs — any name containing `.before-`, `.bak` or `_changed.`,
or ending in `~` — because history replaces that convention rather than versioning it.
That marker list (`BACKUP_MARKERS` in `tools/settings_history.py`) is shared with the
profile-definition builder, so the store and the player download agree about what counts
as a setting.

```bash
python3 tools/settings_history.py list <fleet>              # every settings file tracked
python3 tools/settings_history.py log <path> -n 20          # that file's commits, newest first
python3 tools/settings_history.py show <path>               # its newest recorded content
python3 tools/settings_history.py restore <path> --to /tmp/recovered.cfg
python3 tools/settings_history.py snapshot <fleet> -m "before the ward experiment"
```

`restore` requires `--to` and will not write into the live tree, so a recovery cannot
silently resurrect the settings of a mod that is no longer selected. Read the file out,
then put the value back through the normal mod operation.

### Seeding a profile store from this repository

`deploy/profiles/` is **example seed data**: the three primaries as manifests and nothing
else. No `manager-cache`, no client configs, no archives — the caches are 2.1 GB of
Thunderstore zips per profile and are reproducible from the manifest, and this repository
tracks no third-party binary at all (see
[public-distribution.md](public-distribution.md)).

To turn it into a live store, copy the manifests in and fill the cache from Thunderstore:

```bash
cp -r deploy/profiles/. <fleet>/profiles/
python3 tools/valheim_mods.py --profile flat list                    # confirm it parses
python3 tools/valheim_mods.py --profile flat sync <Author-Package>   # fetch one package
```

`sync` resolves one already-selected package and its dependencies into the profile's
cache, so it is the per-package form. A world's own deploy stages whatever the cache
holds:

```bash
python3 tools/valheim_mods.py --world <World> deploy            # the diff, no changes
python3 tools/valheim_mods.py --world <World> deploy --apply    # world-state; ask first, every time
```

A deploy stops and restarts the world, so it is a `world_state` action needing fresh
confirmation on every invocation, and a deploy whose plan shows no changes is refused
rather than confirmed.

### The migration is historical

`tools/migrate_profiles.py` moved the four per-world copies into the shared store and
linked the servers to them. **The fleet has already migrated**; these commands are
recorded for a deployment still on the old layout, and are not part of routine work:

```bash
python3 tools/migrate_profiles.py plan                     # the copies and how they differ
python3 tools/migrate_profiles.py apply --fold <name> --take <World>
python3 tools/migrate_profiles.py apply --separate         # one profile per world instead
python3 tools/migrate_profiles.py adopt <profile>          # link every server to an existing profile
```

## Publish a Flat ValheimVR release

1. Rebuild the Flat companion on this host from the ValheimVR source checkout. The script compiles `ValheimVRMod.dll` with Mono, swaps it into the template, deletes the superseded `ValheimVRFlatDodgePatchFix.dll`, and refuses a template whose configuration does not contain `nonVrPlayer = true`; see [valheimvr-packaging.md](valheimvr-packaging.md).

   ```sh
   ./scripts/build-valheimvr-artifact.sh \
     --source-root <ValheimVR checkout> \
     --template /path/to/known-good-flat-companion.zip \
     --output /incoming/valheimvr-flat-companion.zip \
     --client-type flat
   ```

   Record the `sha256` it prints; that is what the release must carry.
2. Stage only that Flat companion ZIP for portal publication. Do not include Valheim game files, Unity runtime files, server files, or a VR runtime ZIP.
3. Generate a definition for every Flat edition the catalog declares. The shared profiles and the published edition names are deliberately separate — several editions are built from one profile:

   ```sh
   scripts/build-flat-release-plan.sh \
     2.1.3 "Player-facing release notes" \
     /incoming/valheimvr-flat-companion.zip \
     /srv/valheim-flat-2.1.3
   ```

   This reads `release-targets.json`, whose every entry declares `world`, `source_profile`, `published_profile`, `valheim_vr` and `audience`, all required. `source_profile` names a profile in the shared store, so the three Flat editions of a world come from two profiles: `<world>-vr-flat` and `<world>-non-vr` from `flat`, `<world>-vr-flat-admin` from `admin`. It is untracked operator data: create it once with `cp deploy/release-targets.json.example release-targets.json` and edit it to your worlds. Only its `flat` array is used here; pass a different catalog as the script's optional fifth argument. Inspect `publication-plan.json` and every generated profile ZIP before upload.
4. In authenticated `/admin`, create one Flat draft for every plan target, upload its matching `profile` ZIP and the same Flat companion ZIP, then submit their IDs to `POST /admin/releases/batch-publish`. The endpoint requires the trusted proxy identity and CSRF token, validates every release scope, skips already-published matching IDs on a retry, and publishes each remaining draft.
5. If staging is abandoned or invalid, submit `POST /admin/releases/{id}/discard`. It archives only a draft and retains the audit record. Never modify the SQLite database or artifact tree by hand.
6. From a Windows client with authorized Steam access, launch each world’s Flat Desktop shortcut twice: the first run downloads, validates, and atomically activates the new companion; the second must report no change. Confirm Flat keeps `nonVrPlayer = true` and that the VR profile remains unchanged.

## Publish a profile

1. Build and inspect the deterministic profile-definition ZIP with `scripts/build-profile-definition.sh`. Pass `client-config-flat/` or `client-config-vr/` as the optional final overlay directory; it is merged over the protected common `client-config/`.
2. For VR, rebuild the ValheimVR release archive on this host, then validate and stage its canonical runtime ZIP:

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
3. Build `dist/ValheimProfileSync.exe` with `scripts/build-windows-client.sh`.
4. Create a draft in `/admin` with the exact world, profile, Flat/VR type, version, and player-facing notes.
5. Upload exactly one `profile` ZIP. For VR upload exactly one additional `vr_runtime` ZIP. For Flat upload the matching validated `flat_companion` ZIP. The `seed-release` command enforces the same contract and resumes only an exact matching draft after interrupted staging.
6. Publish. The portal re-verifies artifact SHA-256/size, scope binding, profile metadata, and the VR runtime or Flat companion archive allowlist.
7. With an authorized Steam account, verify the selected world card, scoped manifest/payload/runtime endpoints, VR installation, and switch back to Flat. Another-world and unauthenticated accounts must be denied.

The `vr` profile carries `BackpacksVRFix`, `EpicLootVRFix` and `CLLCVRFix` as client-only packages; the `flat` and `admin` profiles do not, so no Flat edition ships them. The reviewed Flat companion or VR runtime supplies `ValheimVRMod.dll`; it is never deployed to a dedicated server. A `valheim_vr: false` target excludes ValheimVR, its config, and every companion or runtime artifact. Verify a Flat definition contains `nonVrPlayer = true`, a VR definition contains `nonVrPlayer = false`, and only a VR release has `vr_runtime`.

Archive, never delete, a bad release. Publishing a replacement archives only the prior release for the same world/profile/client-type scope.

## Client recovery

Each profile sync is locked and staged separately. A failed update preserves the active generation. Correct or archive the bad release, then run the profile card or Desktop shortcut again. If Steam contains a loader owned by another manager, Valheim Profile Sync refuses to replace it; resolve that ownership conflict before launching.

## Driving the agent

Send a message on `/admin/agent`. That is the whole trigger: the portal writes its wake file, a
systemd path unit starts one runner pass, and the page shows the reply without a refresh. Approving
or denying a verb wakes it the same way, so approved work continues by itself.

```bash
systemctl status valheim-agent-runner-wake.path   # active (waiting) when sending is the trigger
journalctl -u valheim-agent-runner-once -n 20 --no-pager   # what the last pass did
sudo systemctl start valheim-agent-runner-once    # one pass deliberately
```

A read verb executes immediately and the reply quotes what the host printed. A mutating one stops
at **Approve** / **Deny** with its full arguments shown; nothing runs until someone clicks. A
forbidden verb is refused by class, and the agent is expected to say so and stop rather than look
for another route.

If a message sits with no reply: check the path unit above is active, then run a pass by hand. If
that pass fails naming the agent socket, the next section is the one you want.

## When the portal cannot reach the agent

Symptom: every operator action fails with
`Post "http://agent/v1/jobs": dial unix /run/agent/agent.sock: no such file or directory`,
while the socket plainly exists on the host.

```bash
curl -fsS localhost:18080/readyz          # {"status":"ready",...} or 503 naming the socket
ls -l /run/valheim-portal-agent/          # the host side: agent.sock should be here
docker exec valheim-portal-portal-1 ls -l /run/agent/   # the container's view: empty when broken
```

Cause: the agent's socket directory is a systemd `RuntimeDirectory`, which systemd deletes and
recreates on every restart. The portal container bind-mounts that directory, so a restart leaves
the running container holding a mount on the deleted inode — the host has a socket, the container
sees an empty directory, and `docker compose up -d` will not fix it because neither the image nor
the configuration changed.

The generated unit now carries `RuntimeDirectoryPreserve=yes`, so the directory survives a
restart and this cannot recur on a current deployment. To repair a container already in that
state:

```bash
sudo ./scripts/install-portal.sh install --config deploy/install.conf   # detects and recreates it
# or, directly:
sudo docker compose -f /srv/valheim-portal/compose.yaml up -d --force-recreate portal
```

Verify: `curl -fsS localhost:18080/readyz` prints `{"status":"ready","database":"ok","agent":"ok"}`.
That check tests the socket, not just the database — it used to ping SQLite alone while the
installer reported "the portal reached the agent socket", which is how a broken deployment passed
verification and was found instead by an operator typing into the chat page.

## Server logs

Two sources, and they answer different questions.

`hostops/collect_valheim_server_logs.sh` follows each running world container and appends its
output to `/var/log/valheim-worlds/<World>.log` **outside** the container. That file survives a
restart, a `compose down`, and the container being removed, which makes it the only source that can
say what happened before a crash. The admin site reads its tail:

```text
/admin/worlds/<World>/log            the last N lines, with an optional fixed-string filter
/admin/worlds/<World>/log.txt        the same view as a download, behind the same admin guard
```

The whole file is never rendered - the busiest world here passed 12 MB in eight days - and the
download carries the admin guard because a server log names players, their Steam IDs and their join
addresses.

**Install rotation, or the log eventually stops every world on the host:**

```sh
sudo cp deploy/valheim-worlds.logrotate.example /etc/logrotate.d/valheim-worlds
sudo chown root:root /etc/logrotate.d/valheim-worlds && sudo chmod 644 /etc/logrotate.d/valheim-worlds
sudo logrotate --debug /etc/logrotate.d/valheim-worlds   # dry run; refuses a group-writable file
```

`copytruncate` is required rather than preferred: the collector holds each file open, so renaming it
would leave the writer appending to an unlinked inode - the visible log would stop growing while the
disk kept filling.

A world that has not run since the collector started has no log at all, and the page says so instead
of showing an empty box. Note also that Valheim's startup markers (`Load world`, `DungeonDB Start`)
are Info-level, and Info is trimmed from these servers: their absence is not a fault.

## World status

A world's status on the public page is **measured, not declared**. There is no
operator control that sets a world "online", and there has not been one since
status became a probe rather than a stored field.

### Where the measurement comes from

The dedicated-server container runs `valheim-status --update`, which sends the
game a Steam A2S query every 10 seconds and writes the answer to
`htdocs/status.json` inside the container. The container's `/opt/valheim` is the
world's `data/` directory, so that file lands at
`<world-root>/<WORLD>/data/htdocs/status.json` — inside the tree the portal
already mounts read-only. Liveness therefore costs one small file read and no
agent round-trip.

The portal treats a world as **online** only when all of these hold:

| Condition | What its absence means |
|---|---|
| the file exists and parses | the world has never been started, or the status writer is off |
| `error` is `null` | the container is up but the game did not answer the A2S query — typically still loading mods |
| `last_status_update` is set and less than 60 seconds old | the container died and left its last healthy report behind |

All three matter independently. The staleness check is the one that stops a dead
server from showing green forever on the strength of a file nobody is updating.

When the world is online the portal also replaces the recorded server version
with the `g=` field from the A2S keywords, so the page shows the build the
server is actually running rather than the one an operator typed at
registration.

### `maintenance` is the only status you can set

`maintenance` is an editorial statement — "we are working on it, do not try" —
that no probe can infer, so a world in maintenance keeps that status and is
never overwritten by the measurement. Every other stored value was a human's
guess about a running process, and the process can answer for itself. Clear
maintenance and the world immediately reports whatever it actually is.

### Requirement: `STATUS_HTTP=true`

**Without `STATUS_HTTP=true` in a world's `valheim.env`, that world always
reads as offline, even while it is running and full of players.** The container
writes no `status.json` at all: the supervisor program that runs
`valheim-status --update` is created only when `STATUS_HTTP` is true, and the
image's own default is `false`. Worlds provisioned by the creation wizard get
it from the shipped defaults; a hand-built or imported world may not.

Check it before investigating anything else when a running world shows offline:

```sh
grep STATUS_HTTP <world-root>/<WORLD>/valheim.env
```

`STATUS_HTTP` also starts a small HTTP server publishing the same JSON on the
world's status port. The portal does not use it — it reads the file through the
read-only mount — so that port needs no external exposure and should not have
any. Only the status *writer* is required.

## World operations

The host agent accepts only fixed, HMAC-signed operations and serializes operations for the same world. Stop, restart, build, port changes, mod deployment, restore, and permanent deletion use fixed backup/stop/start or backup/stop/delete sequences. Restore requires a short-lived, actor-bound typed confirmation and replaces only the selected save pair. The backup recovery page shows only filenames bound to the selected world.

Each registered world has **Delete or unregister server** under **Server operations**. Unregistering requires `UNREGISTER <world>` and removes only the portal record and player memberships; server files, backups, and release history remain. Permanent deletion requires `DELETE <world>`, immediately disables player access, records a job, creates a final save backup, stops the server, and only then removes the verified server directory. If backup or stop fails, deletion does not run and the disabled registration remains for recovery. Success removes memberships and registration and archives current releases; external backups, artifacts, jobs, and audit events remain.

Existing directories are not published automatically. Keep `AGENT_ALLOWED_WORLDS` synchronized with the controlled servers, restart the agent, then use **Existing servers** in `/admin` to register a discovered world. Registration revalidates the world against the signed agent catalog and creates a disabled portal record; verify its public join address before enabling player access.

## Player access

World membership is granted per world from the **Player access** widget in `/admin`, and it keys off the 17-digit SteamID64 alone. Every name the widget displays is display metadata: it is never consulted for authorization, and an arbitrary Steam ID can still be typed into the grant form directly.

Each Steam identity carries a persona name fetched from Steam. The portal refreshes it the moment a player completes a Steam login, and **Refresh names from Steam** (`POST /admin/steam-identities/refresh`) looks up every Steam ID already on record, never-synced accounts first, so a deployment that collected IDs before names existed is backfilled in one action. It resolves up to 1000 accounts per run and never clears a name it cannot resolve; the identity list itself shows the 100 most recently seen accounts. Names appear in the grant form's Steam ID suggestions, the current-access table, and the Steam identities list, always beside the raw ID.

With `PORTAL_STEAM_API_KEY` empty the portal reads names from the public community profile XML endpoint, so only accounts whose Steam profile is public resolve and the rest stay unnamed. Setting the key switches to the official Steam Web API, which batches up to 100 IDs per request and also resolves private profiles; it needs outbound HTTPS to `api.steampowered.com` on top of the `steamcommunity.com` egress Steam OpenID already requires. The key is a credential, not a host fact: pass it through the environment or the generated `.env` and keep it out of the repository.

A manual player label can also be set per Steam ID (`POST /admin/steam-identities/label`). The label always overrides the fetched persona name and needs no network access at all, so it is the way to name an identity with a private profile on a deployment without an API key, or to correct a persona name that is useless for recognising who is being approved.

Every grant also carries a role, `member` or `admin`, per world. The admin role is what lands in that world's `adminlist.txt`, and that is in-game administrator power — kick, ban, no-cost mode, god mode — not portal administration. Membership is what lands in `permittedlist.txt`. The portal is the source of truth for both files: `adminlist.txt` is always generated from the admin-role grants for that world, and neither file is hand-maintained any more.

**Enforce permitted list** is a per-world flag and defaults to off. Off writes a header-only, empty `permittedlist.txt`, preserving the access every world has today: anyone holding the server password may join. On writes every granted member, which makes portal access authoritative on the server itself. Know what that costs before enabling it. A non-empty permitted list is exclusive, so Valheim then rejects every Steam ID that is not on it regardless of the password, and enabling enforcement on a world whose regulars joined on the password alone locks all of them out until each one holds a grant. Grant the members, confirm the current-access table, then enable.

Applying is explicit. **Apply access lists** syncs one world from the **Player access** widget, and the all-worlds action syncs every registered world. Each world reports `in sync` or `pending changes`, compared as a hash of the intended lists against the lists last applied, so a grant, a revoke, a role change, or an enforcement toggle shows as pending immediately and stays pending until it is applied. **Verify** is the stronger check: it reads the live files and environment back from the host through the agent and reports any out-of-band drift, which is the only way an edit made outside the portal — invisible to the hash — is caught.

A sync writes each list twice, and both writes are required. `adminlist.txt` and `permittedlist.txt` under `<world>/config_merged/` are what takes effect at once, because Valheim re-reads those files at runtime: no restart and no container recreate. `ADMINLIST_IDS` and `PERMITTEDLIST_IDS` in `<world>/valheim.env` are what survives a recreate, because the server image regenerates the list files only at bootstrap and only from a non-empty variable. A stale variable would therefore resurrect a stale list on the next recreate, and an empty one would leave whatever file is present untouched. Writing both keeps the running server and its next boot on the same list.

Two world-scoped agent operations back this. `access_apply` runs `hostops/portal_access_lists.sh` to write the files and the environment; `access_state` reads the current lists and environment values back for **Verify**. Both go through the same HMAC-signed, allowlisted agent path as every other operation.

The agent user `valheim-agent` must be able to write `<world>/config_merged/` and `<world>/valheim.env`, and the world root itself so the server-creation wizard can stage a new world. `scripts/install-portal.sh` grants exactly that with ACLs rather than by loosening the group bits on world data, and the step is idempotent:

```sh
sudo setfacl -m u:valheim-agent:rwx <world-root> <world> <world>/config_merged
sudo setfacl -d -m u:valheim-agent:rwx <world-root>
sudo setfacl -m u:valheim-agent:rw <world>/valheim.env
```

The default ACL on the world root makes every world created later inherit the same access. Only the worlds named in `AGENT_ALLOWED_WORLDS` are granted; archives and backup directories beside them are deliberately left alone. The script never chowns anything; the container fixes ownership when it starts.

The agent unit runs with `ProtectSystem=strict` and `ReadWritePaths` limited to its runtime directory and the world root, so host scripts must keep lock files inside those paths: `/run/lock` is read-only to the agent.

Hand-editing `adminlist.txt`, `permittedlist.txt`, `ADMINLIST_IDS`, or `PERMITTEDLIST_IDS` on the host is now discouraged. The next Apply overwrites the edit, and until then Verify reports it as drift. Change the grant or the flag in `/admin` and apply.

## World intelligence

Each registered world exposes **World intelligence** under **Server operations**. **Analyze latest backup** selects the newest complete `world-<world>-*.tgz`, verifies the archive contains one `.db`/`.fwl` pair, and decodes supported Valheim world-save versions read-only. It never opens or modifies the live save. Results are bounded by archive/member/object/property/inventory limits, stored as an immutable analysis snapshot, and replace only the current displayed snapshot after a complete successful parse. New servers started by the creation wizard automatically create their first complete backup and publish this analysis and map; servers created offline do the same on their first successful portal start or restart.

The explorer overlays deterministic seed terrain and biomes, generated zones, locations, settlement clusters, portals, containers and decoded inventories, production objects, persistent creatures, and terrain-edit risk. Coordinates, object properties, inventories, backup names, and health findings are admin-only; the public world page continues to expose only the seed. Treat unresolved prefab hashes as vanilla-or-mod-unknown until cataloged, and retain the analyzed backup before upgrades or regeneration work. Compare the current snapshot with the prior stored snapshot for object, zone, category, and location deltas.

## Create a server

1. Open **New server** in `/admin`; choose an immutable world slug, display name, password, world generation mode, gameplay/network values, backup policy, and the mod profile it runs. That field is labelled **"Profile this server runs"**, defaults to the profile the most servers already run, and shows beside each profile how many servers run it. Name an existing shared profile and the server links to it; name a new one and it is created, empty or copied from an existing profile. A server is never created from another server. A profile with a count of zero is normally an edition source, not a candidate: `flat` and `vr` exist only as sources for published client editions, and `admin` is the profile servers link to because it is the superset - linking a server to `flat` or `vr` would strip the admin tools that have to be server-side.
2. Review the exact filesystem/container plan, resolved package inventory, visibility, and launch behavior. Type `CREATE <world>` to authorize the short-lived, actor-bound request.
3. The agent reserves the three-port game range under a host lock, validates collisions against every configured world, builds a staging tree, writes the password only to the mode-0600 world environment file, and atomically renames the staging tree into place.
4. Seed creation writes current FWL metadata; import copies the selected save pair and preserves seed and UID while changing only the world name. Non-vanilla player limits pin the server-only MaxPlayerCount package and generated config.
5. Optional startup waits for the official connected readiness log, creates the first complete backup, analyzes it, and publishes the 6144 map. The portal publishes an enabled player card only after provisioning, readiness, and automatic map generation all succeed. A creation, startup, backup, or map failure leaves the world unpublished and recoverable. If startup is deferred, the first successful portal start or restart performs the missing map workflow once; worlds with an existing analysis are skipped.

Player world pages read the displayed seed from the current FWL through the read-only metadata operation; the database is not treated as the source of truth.
