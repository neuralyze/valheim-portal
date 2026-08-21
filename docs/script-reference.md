# Script reference

`hostops/` holds the host-side shell entry points for every world operation; `tools/`
holds the Python they delegate to when the work is more than a `docker compose` call.
The portal's host agent executes nothing but files in `hostops/`, which is what
`AGENT_SCRIPT_DIR` points at by default, and it may only run the fixed set named in the
`agent operation` column below — everything else in `hostops/` is operator-only.

`scripts/` is the third directory and the agent never reaches it: build and install entry
points an operator runs by hand. Two of them build ValheimVR on this host, now that the
Windows build host is gone — `scripts/build-valheimvr.sh` compiles `ValheimVRMod.dll`
with Mono's `mcs`, and `scripts/build-valheimvr-artifact.sh` stages that DLL into either
the Flat companion or the VR release archive, selected by `--client-type`. Their flags are
in [command-reference.md](command-reference.md#scripts-scripts); why the build looks the
way it does, and the two `mcs` traps it encodes, are in
[valheimvr-packaging.md](valheimvr-packaging.md).

`tools/upstream_sources.py` tracks the projects this deployment builds source from, which is a
different question from mod freshness: `verify` is the offline `upstream` gate, `status` reaches
GitHub and reports any upstream commit nobody has reviewed, and `review` records the decision.
See [upstream sources](upstream-sources.md).

`hostops/` and `tools/` ship together and must stay siblings: `hostops/lib/common.sh`
resolves the tools directory from its own location. Everything the scripts need from
outside the repository is configuration with no default — the world root as
`VALHEIM_ROOT` (see [The world root](#the-world-root-valheim_root)) and the
valheim-server-docker checkout as `VALHEIM_SERVER_DOCKER_DIR` (see
[The server-docker checkout](#the-server-docker-checkout-valheim_server_docker_dir)).
Scripts that were unsafe or dead have been removed; see Removed scripts below.

## Host operation scripts (hostops/)

| script | arguments | mutates | agent operation | purpose |
|---|---|---|---|---|
| `hostops/add_note_valheim_world.sh` | `WORLD NOTE...` | `$VALHEIM_ROOT/world_notes/notes_<WORLD>.txt` | - | Appends a timestamped free-text note for a world. Creates the notes directory if it does not exist. Requires `VALHEIM_ROOT`. |
| `hostops/backup_valheim_world.sh` | `WORLD [BACKUP_NAME]` | backup archives | `backup` | Tars the live `.db`/`.fwl` save pair into `$VALHEIM_ROOT/world_backups/world-<WORLD>-<name>-<timestamp>.tgz`, detecting whether the pair uses the world's casing or lowercase. `BACKUP_NAME` defaults to `backup` and must match `[A-Za-z0-9][A-Za-z0-9._-]*`, because `restore_valheim_world.sh` parses it back out of the filename. Requires `VALHEIM_ROOT`. |
| `hostops/backup_valheim_worlds.sh` | `-` | backup archives | - | Fan-out: backs up every world in `hostops/worlds.txt` in parallel. Exits non-zero if any world's backup failed, naming each one. |
| `hostops/build_valheim_server.sh` | `WORLD [SERVICE]` | container image | `build` | `docker compose build` for the world's project, optionally one service. |
| `hostops/capture_valheim_diagnostics.sh` | `WORLD [OUTPUT_DIR]` | diagnostics bundle directory | - | Collects container runtime facts, discord-redacted docker logs, the BepInEx log, LoadTimeProfiler output, plugin/config SHA-256 inventories, and save-file metadata. Defaults to a timestamped directory under the world. |
| `hostops/clean_backups.sh` | `[--dry-run\|--delete] [DAYS_OLD]` | backup archives | - | Prunes backup archives older than `DAYS_OLD` days (default 30, must be a positive integer — `0` is refused). On a terminal `--dry-run` is the default: it lists what it would delete and deletes nothing. Without a terminal (cron, the agent) `--delete` is the default, so a scheduled prune is not a silent no-op. Either way it reports the count. Requires `VALHEIM_ROOT`. |
| `hostops/configure_valheim_port.sh` | `WORLD BASE_PORT` | `valheim.env` | `set_port` | Rewrites `CONTAINER_VALHEIM_PORT` to `BASE_PORT-BASE_PORT+1` under a cross-world lock, refusing any range that overlaps another world. The range must be two ports; three makes compose reject the service. |
| `hostops/export_valheim_map_sources.sh` | `WORLD` | map source output tree | `world_map` | Boots a throwaway headless server from the newest world backup, exports map sources, and publishes them as a content-addressed object with a `current` symlink. Never touches the live save. Requires `VALHEIM_ROOT`; honours `PORTAL_MAP_SOURCE_ROOT`. |
| `hostops/list_valheim_world_backups.sh` | `WORLD` | no | `backups` | Prints the world's backup archive filenames, newest first. Requires `VALHEIM_ROOT`. |
| `hostops/logs_valheim_server.sh` | `WORLD [SERVICE]` | no | - | Follows `docker compose logs -f` for the world. Interactive; never returns on its own. |
| `hostops/logs_valheim_server_snapshot.sh` | `WORLD` | no | `logs` | Prints the last 200 compose log lines and exits. |
| `hostops/manage_mods.sh` | `WORLD SUBCOMMAND [ARGS...]`, or `--world`/`--manifest` passthrough, or `--interactive` / `--worlds` / `--bash-completion` / `--fish-completion` | delegates to `tools/valheim_mods.py` | - | Operator wrapper for the mod controller, plus a `select`-driven interactive mode and shell completions. With no arguments it enters interactive mode and requires a terminal. |
| `hostops/pause_valheim_server.sh` | `WORLD [SERVICE]` | container lifecycle | `pause` | `docker compose pause`. |
| `hostops/pause_valheim_servers.sh` | `-` | container lifecycle | - | Fan-out: pauses every world in `hostops/worlds.txt` in parallel. |
| `hostops/portal_access_lists.sh` | `WORLD ADMIN_IDS PERMITTED_IDS` | `adminlist.txt`, `permittedlist.txt` | `access_apply` | Writes the generated admin and permitted lists. Each argument is a comma-separated list of SteamID64 values or `-` for empty; at most 200 entries, no duplicates. Refuses symlinked or non-regular targets, and stages every file before renaming any. |
| `hostops/portal_create_valheim_world.sh` | `WORLD SEED` | world save pair | `world_create` | Regenerates an existing world's save pair on a chosen seed via `tools/valheim_worldgen.py`. The world directory must already exist. |
| `hostops/portal_delete_valheim_server.sh` | `WORLD` | whole world directory | `delete_server` | Deletes the entire world directory after verifying it is a real directory inside the world root and carries a `valheim.env`. External backups are retained. Requires `VALHEIM_ROOT`. |
| `hostops/portal_mod_admin.sh` | `WORLD PROFILE ACTION [ARGS...]` | profile manifest, package cache, staged plugins | `mod_inventory`, `mod_search`, `mod_custom_list`, `mod_add`, `mod_remove`, `mod_enable`, `mod_disable`, `mod_custom_add`, `mod_custom_remove`, `mod_custom_enable`, `mod_custom_disable`, `mod_deploy` | The portal's only mod entry point. Validates the argument count and scope per action, then execs `tools/valheim_mods.py`. Its stderr is what the admin UI shows, so rejections name the action and what it wanted. Actions: `inventory`, `search QUERY`, `custom-list`, `add ID VERSION SCOPE`, `remove ID REASON`, `enable ID`, `disable ID`, `custom-add ID SCOPE`, `custom-remove ID`, `custom-enable ID`, `custom-disable ID`, `deploy`. |
| `hostops/portal_profile_catalog.sh` | `WORLD` | no | `profile_catalog` | Emits the world's controlled mod profiles as JSON via `tools/valheim_profile_catalog.py`. |
| `hostops/portal_world_config_schema.sh` | `WORLD [state]` | no | `world_config_schema`, `world_config_schema_state` | Emits the typed schema of one world's BepInEx settings as `world-config-schema/v1` JSON via `tools/valheim_config_schema.py extract`, which is what the settings manager page renders and what the authority store validates a stored value against. `state` prints only a sha256 fingerprint over each config's path, size and mtime, which is what the portal checks on every page view; the full build parses 19,936 setting blocks for Hrafnheim and emits a 4.5 MB payload, and the cache exists to avoid doing that per view. The schema is the UNION of two sources, because neither alone is the set an admin needs. The world's own `config_merged/bepinex` is where the plugins wrote their metadata out and is the only source rich enough to build from - 107 of its 108 top-level files carry `# Setting type` comments against 12 of 18 in a client tree - but a mod that never runs server-side never generated anything there, and `neuralyze.vrfixes.cfg` is absent from all four worlds for exactly that reason. So the client config trees a publish would copy are read too, resolved from `release-targets.json` and `republish-profiles.sh:161-163` rather than a hardcoded profile list. Every file carries `source` (`config_merged`, `client_profile` or `both`) and `shipped`, both unconditional, because a value in an unshipped file is recorded and not in force and the page must be able to say which. A file in both takes the world tree's metadata, which is measurably fuller: `org.bepinex.plugins.valheimvrmod.cfg` is 145 keys there against the profile overlay's 26. Where one basename differs between profiles the copy describing the most keys wins and the disagreement is listed in `divergent` - `neuralyze.vrfixes.cfg` is 31 keys in `vr` and 30 in `flat` and `admin`, and publish-order precedence would have silently dropped `LogShieldBlocks`. Configs in `plugins/` are the packages' own data and are excluded; subdirectories are not, so a `file` may contain a slash. Each file is attributed to a mod by walking real files - plugin GUID to assembly to plugin directory to that directory's manifest to the matching profile-manifest identifier - and a file that does not resolve is listed in `unattributed` with an empty `mod_identifier` rather than given a guessed owner, which is the normal state for the 14 configs left behind by removed mods. |
| `hostops/portal_world_metadata.sh` | `WORLD` | no | `world_metadata` | Inspects the world's `.fwl` and emits seed and version metadata as JSON via `tools/valheim_world.py inspect`. |
| `hostops/portal_world_mod_catalog.sh` | `WORLD [state]` | no | `world_mod_catalog`, `world_mod_catalog_state` | Emits the player-visible mod list of one world as JSON via `tools/valheim_mods.py player-catalog`. `state` prints only the fingerprint of the installed player set and reads no network, which is what the portal checks on every world page view; the full build fetches the Thunderstore index. The list is the union of the `vr` and `flat` profiles minus Thunderstore-categorised libraries and the `PLAYER_IRRELEVANT` entries in `tools/valheim_mods.py`. |
| `hostops/provision_valheim_server.sh` | `WORLD SERVER_NAME PORT PUBLIC CROSSPLAY PLAYER_LIMIT PRESET BACKUP_INTERVAL BACKUP_AGE BACKUP_COUNT PROFILE SEED SOURCE_WORLD COPY_FROM` (exactly 14) | new world directory tree | `provision` | Transactionally creates a portal-managed world directory via `tools/valheim_provision.py`. Requires the `PORTAL_SERVER_PASSWORD` environment variable; refuses to run without it. A wrong argument count prints the full positional order rather than a bare count; `python3 tools/valheim_provision.py --help` explains each one. `hostops/tests/agent_argv_contract.sh` holds this list against the argv `internal/agent/agent.go` actually sends. |
| `hostops/remove_valheim_server.sh` | `WORLD [SERVICE]` | container lifecycle | - | `docker compose rm -v`: removes stopped containers and their anonymous volumes. |
| `hostops/restore_valheim_world.sh` | `WORLD BACKUP_NAME` | world save pair | `restore` | Restores a save pair from a named archive in the backup inventory. Verifies the archive holds exactly the expected `.db`/`.fwl` pair, extracts to a staging directory inside the world, and installs both files mode 0640. Requires `VALHEIM_ROOT`. |
| `hostops/shell_valheim_server.sh` | `WORLD [BASH_ARGS]` | no (whatever you type does) | - | Opens an interactive `bash` inside the world's `valheim` service container. |
| `hostops/start_valheim_server.sh` | `WORLD [SERVICE]` | container lifecycle | `start` | `docker compose up -d`, gated on the mod release-status check (see Notes). The agent passes no service; an operator may name one. |
| `hostops/start_valheim_servers.sh` | `-` | container lifecycle | - | Fan-out: starts every world in `hostops/worlds.txt` in parallel. |
| `hostops/status_valheim_server.sh` | `WORLD` | no | `status` | `docker compose ps` for the world's project. |
| `hostops/stop_valheim_server.sh` | `WORLD [SERVICE]` | container lifecycle | `stop`, `restart` | `docker compose down`. Both agent operations route here; the agent supplies the surrounding backup and restart steps itself. |
| `hostops/stop_valheim_servers.sh` | `-` | container lifecycle | - | Fan-out: stops every world in `hostops/worlds.txt` in parallel. |
| `hostops/unpause_valheim_server.sh` | `WORLD [SERVICE]` | container lifecycle | `resume` | `docker compose unpause`. |
| `hostops/unpause_valheim_servers.sh` | `-` | container lifecycle | - | Fan-out: unpauses every world in `hostops/worlds.txt` in parallel. |
| `hostops/wait_valheim_server_ready.sh` | `WORLD` | no | `health` | Polls the container for up to 600 seconds, waiting for `Game server connected` in the logs. Fails early if the container exits, dumping the log tail. |

## Python tools (tools/)

| tool | arguments | subcommands | mutates | purpose |
|---|---|---|---|---|
| `tools/test_valheim_mods.py` | standard `unittest` arguments | - | no | Unit tests for the mod controller: deploy staging, dependency resolution, manifest handling. |
| `tools/valheim_mods.py` | `[--world WORLD] [--profile PROFILE] [--manifest PATH] SUBCOMMAND ...` | `list [--json]`, `check-updates`, `search QUERY [--json]`, `add ID [VERSION] [--client-only]`, `sync ID`, `remove ID --reason R`, `purge ID --reason R`, `exclude ID VERSION --reason R`, `disable ID`, `enable ID`, `custom-list`, `custom-add ID [--scope shared\|client-only]`, `custom-remove ID`, `custom-enable ID`, `custom-disable ID`, `update [ID] [--all] [--apply]`, `export-code`, `deploy [--apply]`, `release-status [--require-complete]`, `release-confirm PROFILE_NAME {flat\|vr} RELEASE_ID ARCHIVE`, `profile {list, create NAME, copy SOURCE NAME, remove NAME}`, `player-catalog [--state]` | profile manifest, downloaded package cache, staged plugin trees (mutating subcommands only) | The manifest-driven Valheim mod controller. Resolves Thunderstore packages and their dependencies, records every change in the profile manifest, and stages plugins for the next server start. Read-only unless the subcommand says otherwise; `update` and `deploy` need `--apply` to write. `player-catalog` is the only subcommand that names no profile: it spans the `vr` and `flat` player editions and needs `--world` for that world's installed plugin manifests. |
| `tools/valheim_profile_catalog.py` | `WORLD` | - | no | Emits the world's controlled mod profiles as JSON, skipping symlinked directories and manifests that do not name the requested world. |
| `tools/valheim_provision.py` | 14 positionals: `WORLD SERVER_NAME PORT PUBLIC CROSSPLAY PLAYER_LIMIT PRESET BACKUP_INTERVAL BACKUP_AGE BACKUP_COUNT PROFILE SEED SOURCE_WORLD COPY_FROM` | - | new world directory tree, `valheim.env`, port allocations | Transactionally creates a portal-managed world: directory layout, `valheim.env`, linking the server to the profile it runs (created empty, or copied from `COPY_FROM`, when it does not exist), and host port allocation (two UDP game ports plus status, supervisor and discord TCP ports). Reads the server password from `PORTAL_SERVER_PASSWORD`. |
| `tools/valheim_world.py` | see subcommands | `inspect PATH`, `generate PATH NAME SEED [--templates DIR] [--force]`, `clone SOURCE DESTINATION NAME [--force]` | `.fwl` metadata files (`generate`, `clone` only) | Reads and writes Valheim `.fwl` world metadata: seed, world version, generator version. `inspect` is read-only and emits JSON. `generate` and `clone` refuse an existing destination unless `--force`, and print the path and seed they wrote. |
| `tools/valheim_worldgen.py` | `WORLD [SEED] [--status]` | - | world save pair | Creates or resets a world on a chosen seed by letting the game's own generator run under the seed plugin, rather than fabricating a `.fwl`. `--status` is a read-only report and makes `SEED` optional; without it, `SEED` is required. |
| `tools/vr_impact_scan.py` | `--packages DIR` (`--manifest`, `--json`, `--min-severity`, `--package`, `--vhvr-source`, `--vhvr-controls`, `--adopt-list`, `--cap`, `--quiet`) | - | no | Stage 1 of mod onboarding: walks the IL of every assembly in a package cache and reports the seven known VHVR-incompatibility classes with symbol evidence. Needs `dnfile`. Exits 1 when findings reach `--min-severity`, so it gates. |
| `tools/vr_perf_ingest.py` | `--bundle PATH` (`--baseline`, `--static`, `--json`, `--min-severity`, `--startup-ms`, `--frame-ms`, `--min-startup-ms`, `--top`) | - | no | Stage 5: reads a client diagnostics bundle, merges the stage-1 findings named by `--static`, and produces one cost dossier per mod. Same exit-code contract. |
| `tools/vr_scan_common.py` | not executable on its own | - | no | Severity vocabulary, manifest parsing, join-key normalisation and exit codes shared by the two scanners. Imported, never run. |

No host script calls these three. They are run by hand during mod onboarding and take
every path as an argument, so unlike the tools above they need neither `VALHEIM_ROOT` nor
`tools/portal_paths.py`. The classes they report, and how to read their output, are in
[vr-impact-scan.md](vr-impact-scan.md); the process that invokes them is
[mod-onboarding.md](mod-onboarding.md).

## Required first-run setup: the operator data files

Two files these scripts depend on are **operator data, not source**. They hold your
real world names, so they are deliberately not tracked and a fresh clone does not have
them. Copy both before running anything, then edit them:

```sh
cp hostops/worlds.txt.example hostops/worlds.txt
cp deploy/release-targets.json.example release-targets.json
```

### `hostops/worlds.txt`

One world name per line; blank lines are skipped and there is no comment syntax. It is
the list the five bulk scripts iterate — `backup_valheim_worlds.sh`,
`start_valheim_servers.sh`, `stop_valheim_servers.sh`, `pause_valheim_servers.sh`, and
`unpause_valheim_servers.sh`. On a fresh clone, where the file does not exist yet, each
of them exits 78 and prints the `cp` command that creates it.

**The failure mode worth knowing: a world missing from this file is simply never
backed up.** Nothing errors. `backup_valheim_worlds.sh` reports success because every
world it was told about succeeded, and the omitted world stays silently unprotected
until the day you need its backup. The same omission means it is never started,
stopped, paused, or resumed in bulk either, but those you notice. Reconcile this file
against `ls valheim/` whenever you add or rename a world.

The per-world scripts do not read it. `./hostops/start_valheim_server.sh <WORLD>`
works regardless.

### `release-targets.json`

Schema 1, with a `flat` array and a `vr` array. Each entry names one published
edition: the shared profile it is built from, the name players see, whether the
edition carries ValheimVR, and who it is offered to.

```json
{
  "world": "<WORLD>",
  "source_profile": "admin",
  "published_profile": "<world>-vr-flat-admin",
  "valheim_vr": true,
  "audience": "admin"
}
```

All five fields are required and none has a default. ValheimVR used to be inferred
from the profile name, which shipped it to `-non-vr` players; `valheim_vr: false` now
makes the builder run `-true-nonvr` instead. `audience` here is the catalog's declaration
of who the edition is for; it is carried into the `releases.audience` column at publish
time and is what keeps the admin edition off an ordinary player's world page. It is never
written into the published definition — see
[release-format.md](release-format.md#the-definition-format-is-frozen). Four editions per
world is the shape here — see [operations.md](operations.md#the-four-published-editions).

Two consumers, and they read different parts:

* `scripts/build-flat-release-plan.sh` reads the **`flat` array only**, to
  decide which profile definitions to build. Its optional fifth argument
  (`TARGETS_JSON`) overrides the file, which is how you build against an alternative
  catalog without editing this one.
* `tools/valheim_mods.py` reads **both arrays**, to find every published target
  affected by a package removal from a source profile. That is what creates the
  client-release cutover guard.

**Second failure mode: if this file is absent, the cutover guard silently does
nothing.** `client_release_targets` returns an empty list for a missing file rather
than failing, so `manage_mods.sh <WORLD> remove` records no pending targets and
`start_valheim_server.sh`'s release-status gate always passes. You can then remove a
package from the server and start it while every player is still on a client release
that expects the package — exactly the state the guard exists to prevent. Copy the
file even if you are not publishing releases yet.

## Notes

The plural `*_valheim_servers.sh` scripts are fan-outs, not separate implementations.
Each reads `hostops/worlds.txt` (see above — you must create it), one world per line,
and runs the singular script for every world in the background, then waits for all of
them. There is no ordering. A world whose singular script fails is named on stderr with
its exit status, and the fan-out exits 1 once every job has been reaped, so a failure in
one world neither aborts nor hides the others.

`start_valheim_server.sh` refuses to start a world when
`manage_mods.sh <WORLD> release-status --require-complete` fails. That means a
client-release cutover is still pending: a profile changed but the matching client
release was never published. Republish the pending targets and record each with
`manage_mods.sh <WORLD> release-confirm <published-profile> <client-type> <release-id> <profile-zip>`,
then start again.

Stop the server and take a backup before running any of these:
`portal_delete_valheim_server.sh` (unrecoverable deletion of the whole world
directory), `restore_valheim_world.sh` (overwrites the live save pair),
`portal_create_valheim_world.sh` (replaces the save pair with a freshly generated
world), and `clean_backups.sh` (deletes backup archives outright, so it can destroy the
very backup you would restore from — on a terminal it dry-runs by default, but under
cron it does not). The portal agent already wraps its destructive operations in a
backup-then-stop sequence; a hand run does not.

Every script that takes a world name validates it against
`^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$` and exits 2 otherwise. This is the same shape the
portal agent enforces in `internal/agent/agent.go`, but the agent lives in a
different repository: the check is repeated here so a hand run, or a future caller, is
never the thing that makes an unvalidated name reach the filesystem.

## The world root (`VALHEIM_ROOT`)

`hostops/lib/common.sh` is the single place the world root is resolved. Every script
that reads or writes world data sources it and calls `require_valheim_root`, which
takes the first of `VALHEIM_ROOT`, `AGENT_WORLD_ROOT` (exported by the portal agent's
systemd unit) and `VALHEIM_WORLD_ROOT` (the installer's name for the same directory)
that is set. The backup inventory is always `$VALHEIM_ROOT/world_backups`, matching
what the agent's `resolveBackupRoot` requires.

```sh
VALHEIM_ROOT=/srv/valheim ./hostops/backup_valheim_world.sh MyWorld
```

**There is no default, and that is deliberate.** These scripts used to hardcode one
absolute path, so the installer's documented `VALHEIM_WORLD_ROOT` was silently ignored
and backup, list, delete and restore ran against a directory that exists on exactly one
machine. An unset root now fails with exit 78 and a message naming the variable, rather
than deleting or overwriting something nobody chose. A relative or nonexistent root is
refused the same way.

`scripts/build-flat-release-plan.sh` and
`scripts/build-profile-definition.sh` take the same line with
`VALHEIM_PROFILE_SOURCE_ROOT`: required, no default, exit 78 when unset.

## The server-docker checkout (`VALHEIM_SERVER_DOCKER_DIR`)

Every lifecycle script — build, logs, logs snapshot, pause, remove, shell, start,
status, stop, unpause — drives the compose project in a checkout of the modified
[valheim-server-docker](https://github.com/lloesche/valheim-server-docker) fork, and
`tools/valheim_provision.py` reads its `default.env` for the PGID the container chowns
its mounts to. `require_server_docker_dir` in `hostops/lib/common.sh` resolves it, and
`tools/portal_paths.py` does the same for the Python half.

```sh
VALHEIM_SERVER_DOCKER_DIR=/srv/valheim-server-docker ./hostops/start_valheim_server.sh MyWorld
```

That tree is a separate Apache-2.0 project and is deliberately not vendored, so there
is **no default and no fallback**. These scripts run `docker compose down` and
`docker compose rm -v`; a guessed path would tear down whatever project happened to be
there. Unset, relative, missing, or holding no compose file all fail with exit 78 and a
message naming the variable.

## Removed scripts

These were judged unsafe, broken, or dead and have been deleted. Nothing referenced
them: they appeared in no operation map, no other script, and no Go or Python source.
They are listed here so an operator who remembers one knows where its job went.

| removed file | replaced by |
|---|---|
| `create_valheim_server.sh` | `hostops/provision_valheim_server.sh` |
| `delete_valheim_world.sh` | `hostops/portal_delete_valheim_server.sh` |
| `update_mods.sh` | `hostops/manage_mods.sh` |
| `manage_mod_profiles.sh` | `hostops/manage_mods.sh` profile subcommands |
| `check_deprecated_mods.sh` | `hostops/manage_mods.sh` inventory reporting |
| `backup_valheim_config.sh` | `hostops/backup_valheim_world.sh` |
| `phvalheim/docker-compose.yaml` | nothing; this product does not deploy phvalheim |
| `worlds_all.txt` | `hostops/worlds.txt` |

`scripts/mount-windows.sh` was briefly removed with them and has been kept
instead: the CIFS mounts it creates are live infrastructure. The ValheimVR artifacts
and the profile-sync client are assembled on this Linux host against a real Valheim
installation that lives on the Windows gaming machine, reached through those mounts.
It no longer hardcodes a host or account - see `VALHEIM_WINDOWS_HOST`,
`VALHEIM_WINDOWS_USER` and `VALHEIM_WINDOWS_CREDENTIALS` in its `--help`.

## Regression tests

`hostops/tests/` holds standalone bash tests. They use temporary directories and stub
binaries, never a real world or container, so they are safe to run anywhere:

```sh
for t in hostops/tests/*.sh; do bash "$t"; done
```

| test | proves |
|---|---|
| `agent_argv_contract.sh` | The host scripts still match the callers that build their argv: `provision_valheim_server.sh`'s `POSITIONALS` array is counted against the argv `internal/agent/agent.go` sends and refuses the pre-`COPY_FROM` template pair, and `portal_publish_profile.sh` resolves exactly one catalog target per published edition. |
| `clean_backups_guard.sh` | `clean_backups.sh` refuses a non-integer or zero `DAYS_OLD`, dry-runs by default on a terminal, deletes by default without one, and reports the count either way. |
| `portal_mod_admin_messages.sh` | `portal_mod_admin.sh` explains every rejection instead of exiting silently. |
| `restore_valheim_world_roundtrip.sh` | A save pair survives `backup_valheim_world.sh`'s archive naming and `restore_valheim_world.sh`, in both save-file casings, and a foreign archive is refused. |
| `start_valheim_server_gate.sh` | `start_valheim_server.sh` honours the client-release cutover gate and never reaches `docker` when it is pending. |
| `valheim_root_resolution.sh` | Every root-consuming script honours `VALHEIM_ROOT` (and the two deployment aliases) and fails loudly, naming the variable, when none is set. |

The Python tools have their own suite: `cd tools && python3 -m pytest test_valheim_mods.py -q`, after
`python3 -m pip install -r tools/requirements.txt`. Those imports happen at module
scope, so without them pytest fails in collection rather than reporting a test.

Both suites run in CI alongside `shellcheck -S style` over `hostops/`.
