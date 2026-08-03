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

## Publish a Flat ValheimVR release

1. Rebuild `ValheimVRMod.dll` on the Windows ValheimVR build host from the ValheimVR source project, using the established local build process; see [valheimvr-packaging.md](valheimvr-packaging.md). Package the rebuilt DLL into the known-good Flat companion ZIP, remove the temporary `ValheimVRFlatDodgePatchFix.dll`, and confirm its configuration contains `nonVrPlayer = true`.
2. Stage only that Flat companion ZIP for portal publication. Do not include Valheim game files, Unity runtime files, server files, or a VR runtime ZIP.
3. Generate both scoped definitions from the catalog. The source profiles and public profiles are deliberately separate:

   ```sh
   scripts/build-flat-release-plan.sh \
     2.1.3 "Player-facing release notes" \
     /incoming/valheimvr-flat-companion.zip \
     /srv/valheim-flat-2.1.3
   ```

   This reads `release-targets.json`, which maps each source profile to its published Flat profile — one entry per world, in the form `<WORLD>/<source-profile> → <published-profile>`. It is untracked operator data: create it once with `cp release-targets.json.example release-targets.json` and edit it to your worlds. Only its `flat` array is used here; pass a different catalog as the script's optional fifth argument. Inspect `publication-plan.json` and every generated profile ZIP before upload.
4. In authenticated `/admin`, create one Flat draft for every plan target, upload its matching `profile` ZIP and the same Flat companion ZIP, then submit their IDs to `POST /admin/releases/batch-publish`. The endpoint requires the trusted proxy identity and CSRF token, validates every release scope, skips already-published matching IDs on a retry, and publishes each remaining draft.
5. If staging is abandoned or invalid, submit `POST /admin/releases/{id}/discard`. It archives only a draft and retains the audit record. Never modify the SQLite database or artifact tree by hand.
6. From a Windows client with authorized Steam access, launch each world’s Flat Desktop shortcut twice: the first run downloads, validates, and atomically activates the new companion; the second must report no change. Confirm Flat keeps `nonVrPlayer = true` and that the VR profile remains unchanged.
## Publish a profile

1. Build and inspect the deterministic profile-definition ZIP with `scripts/build-profile-definition.sh`. Pass `client-config-flat/` or `client-config-vr/` as the optional final overlay directory; it is merged over the protected common `client-config/`.
2. For VR, rebuild and package ValheimVR locally using the existing local ValheimVR release process, then validate and stage its canonical runtime ZIP:

   ```sh
   ./scripts/build-vr-runtime-artifact.sh \
     /path/to/vhvr-release.zip \
     /path/to/valheimvr-vr-runtime.zip

   ```
3. Build `dist/ValheimProfileSync.exe` with `scripts/build-windows-client.sh`.
4. Create a draft in `/admin` with the exact world, profile, Flat/VR type, version, and player-facing notes.
5. Upload exactly one `profile` ZIP. For VR upload exactly one additional `vr_runtime` ZIP. For Flat upload the matching validated `flat_companion` ZIP. The `seed-release` command enforces the same contract and resumes only an exact matching draft after interrupted staging.
6. Publish. The portal re-verifies artifact SHA-256/size, scope binding, profile metadata, and the VR runtime or Flat companion archive allowlist.
7. With an authorized Steam account, verify the selected world card, scoped manifest/payload/runtime endpoints, VR installation, and switch back to Flat. Another-world and unauthenticated accounts must be denied.

VR-compatible Flat and VR definitions retain `BackpacksVRFix` and `EpicLootVRFix`. The reviewed Flat companion or VR runtime supplies `ValheimVRMod.dll`; it is never deployed to a dedicated server. True nonVR definitions exclude ValheimVR, both VR-fix packages, the ValheimVR config, and every companion/runtime artifact. Verify the Flat profile contains `nonVrPlayer = true`, the VR profile contains `nonVrPlayer = false`, and only the VR release has `vr_runtime`.

Archive, never delete, a bad release. Publishing a replacement archives only the prior release for the same world/profile/client-type scope.

## Client recovery

Each profile sync is locked and staged separately. A failed update preserves the active generation. Correct or archive the bad release, then run the profile card or Desktop shortcut again. If Steam contains a loader owned by another manager, Valheim Profile Sync refuses to replace it; resolve that ownership conflict before launching.

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

1. Open **New server** in `/admin`; choose an immutable world slug, display name, password, world generation mode, gameplay/network values, backup policy, and a controlled template profile or clean profile.
2. Review the exact filesystem/container plan, resolved package inventory, visibility, and launch behavior. Type `CREATE <world>` to authorize the short-lived, actor-bound request.
3. The agent reserves the three-port game range under a host lock, validates collisions against every configured world, builds a staging tree, writes the password only to the mode-0600 world environment file, and atomically renames the staging tree into place.
4. Seed creation writes current FWL metadata; import copies the selected save pair and preserves seed and UID while changing only the world name. Non-vanilla player limits pin the server-only MaxPlayerCount package and generated config.
5. Optional startup waits for the official connected readiness log, creates the first complete backup, analyzes it, and publishes the 6144 map. The portal publishes an enabled player card only after provisioning, readiness, and automatic map generation all succeed. A creation, startup, backup, or map failure leaves the world unpublished and recoverable. If startup is deferred, the first successful portal start or restart performs the missing map workflow once; worlds with an existing analysis are skipped.

Player world pages read the displayed seed from the current FWL through the read-only metadata operation; the database is not treated as the source of truth.
