# Command reference

Every executable in this repository lives in one of three places.

`cmd/` holds Go programs. Each subdirectory is one `main` package built with
`go build ./cmd/<name>` or run with `go run ./cmd/<name>`. When the checkout
sits inside another VCS working copy — as the reference deployment's does — a
bare `go build ./...` fails with `multiple VCS detected`. Always build with:

```
go build -buildvcs=false ./...
```

See [repository-layout.md](repository-layout.md) for the detail. The build
scripts already pass the flag.

`scripts/` holds bash wrappers. They exist because the useful invocations of
the Go builders take six or more absolute paths that have to agree with each
other; the scripts derive those paths from a world/profile pair and fail early
when a source tree is incomplete. All of them resolve the repository root from
`$BASH_SOURCE` and `cd` there, so they can be run from any directory.

`hostops/` holds the world operation scripts the agent executes, and `tools/`
the Python they delegate to. Those are documented separately, one entry each,
in [script-reference.md](script-reference.md); this page does not repeat them.
The one exception is below: three tools in `tools/` are invoked by a human
rather than by a script, so a reader looking for "every command" would not
think to look in the script reference for them.

## Commands (cmd/)

Flags below are listed required-first, with optional flags in parentheses.
Go's flag package accepts either one or two leading dashes; the sources and
scripts mix both styles interchangeably.

| Command | Flags | Mutates | Purpose |
|---|---|---|---|
| `agent-runner` | (`-portal`, `-token-file`, `-state`, `-omp`, `-model`, `-poll`, `-once`) | Nothing directly: it asks the portal for verbs and the portal decides. Writes only its cursor file | Reads the operator conversation, asks omp what to do, and requests one verb per reply. Installed as `/usr/local/bin/valheim-agent-runner` when `PORTAL_ENABLE_AGENT_BRIDGE=true`, and normally started through systemd rather than by hand: `systemctl start valheim-agent-runner-once` for one pass, or the `valheim-agent-runner` service to poll. On demand it is normally started by `valheim-agent-runner-wake.path` when the operator sends a message or decides a verb, so neither flags nor a command are usually needed. Both units read `/etc/valheim-portal/agent-runner.env`, so the flags rarely need to be given. Holds no model credential - omp owns authentication - and cannot widen its own vocabulary: it reads it from `GET /api/agent/verbs`. |
| `map-tile-builder` | `-root -input -height-input -world -seed -worldgen-version` (`-size`, `-workers`) | Writes the tile pyramid under `-root`; prints the manifest as JSON on stdout | Renders a deterministic zoomable map tile pyramid from an authoritative terrain PNG plus a 16-bit height PNG. `-worldgen-version` must be positive; `-size` defaults to the package default and `-workers` to `min(GOMAXPROCS, 8)`. |
| `profile-definition-builder` | `-source-manifest -world -profile -client-type -audience -config-dir -output` (`-flat-companion`, `-package-base-url`, `-true-nonvr`, `-debug-logging`) | Writes the profile-definition ZIP at `-output`; prints that path on stdout | Turns a shared `profile-manifest.json` plus a client-config directory into one checksum-bound profile definition. `-client-type` is `flat` or `vr`. `-audience` is `player` or `admin` and has no default; it is **validated here but never written into the archive** — the audience is a portal-only fact and reaches `releases.audience` through `seed-release` instead (see [release-format.md](release-format.md#the-definition-format-is-frozen)). Config backups (`.before-`, `.bak`, `_changed.`, trailing `~`) are skipped rather than shipped to players. Flat profiles require `-flat-companion` unless `-true-nonvr` strips the ValheimVR packages instead. VR profiles reject `-flat-companion`. `-package-base-url` defaults to `VALHEIM_PACKAGE_BASE_URL`. `-output` may not sit inside `-config-dir`. |
| `seed-release` | Publishing: `-world -profile -client-type -version -profile-payload -audience` (`-vr-runtime`, `-flat-companion`, `-diag-plugin`, `-join-address`, `-server-version`, `-notes`, `-actor`, `-database`, `-artifact-root`). Archiving: `-archive-draft ID` or `-archive-release ID` | Writes the portal SQLite database and copies artifacts into the immutable artifact root | Publishes or archives a profile release. `-database` and `-artifact-root` default to the deployed locations and must be absolute. `-audience` is `player` or `admin`, required when publishing with no default, and is the only writer of the `releases.audience` column that decides whether an edition is offered to ordinary players or to admin logins only. `-client-type vr` requires `-vr-runtime` and forbids `-flat-companion`; `-client-type flat` forbids `-vr-runtime`. A world not yet registered additionally requires `-join-address` and `-server-version`. The two archive flags are mutually exclusive and skip every publishing requirement. |
| `valheim-portal` | `-mode portal\|agent` (default `portal`) | `portal`: the SQLite database. `agent`: world data, via the fixed operation scripts | The single binary for both halves of the deployment. `-mode portal` loads the portal configuration, opens the store, dials the agent socket and serves HTTP. `-mode agent` reads `AGENT_ALLOWED_WORLDS`, `AGENT_SOCKET`, `AGENT_TOKEN_FILE`, `AGENT_SCRIPT_DIR` and `AGENT_WORLD_ROOT` from the environment and serves the privileged host agent on its Unix socket. Any other mode is an error. |
| `valheim-profile-sync` | Not a conventional CLI - see below | The player's local profile store and the Doorstop bootstrap beside `valheim.exe` | The Windows GUI client. It is built only by `scripts/build-windows-client.sh` and is normally started by the operating system through the `valheim-profile-sync://` URL scheme, with the portal handoff URL as its single operand; there are no user-facing flags. Two internal entry points exist: both builds accept the hidden `--collect-diagnostics -game-dir -profile-root -pid` subcommand used to gather a bundle after the game exits, and the non-Windows stub build accepts `-launch` and `-game-dir` before the URL operand so the synchronization path can be exercised off Windows. |
| `vr-runtime-builder` | `-input -output` | Writes the immutable VR runtime ZIP at `-output` | Converts a validated ValheimVR release ZIP into the portal's VR runtime artifact. Rejects any positional argument. |
| `world-analysis` | `-archive -world` (`-catalog`) | None; prints the analysis snapshot as JSON on stdout | Analyses a completed world backup archive. `-catalog` optionally names a managed assembly or plugin DLL to resolve names against. |

## Scripts (scripts/)

Arguments are positional and order is exact. Brackets mark optional trailing
arguments.

| Script | Arguments | Mutates | Purpose |
|---|---|---|---|
| `build-flat-release-plan.sh` | `VERSION NOTES FLAT_COMPANION OUTPUT_DIR [TARGETS_JSON]` | Writes one profile ZIP per target plus `OUTPUT_DIR/publication-plan.json`; prints that plan path | Builds every Flat profile definition named by the release target catalog and emits a schema-1 publication plan for the publisher. `TARGETS_JSON` defaults to `release-targets.json` at the repository root — an **untracked operator data file** you must create from `deploy/release-targets.json.example` (see Notes); pass the fifth argument to build against a different catalog. Only the catalog's `flat` array is read. Source manifests and both client-config layers are read from `VALHEIM_PROFILE_SOURCE_ROOT/profiles/<source-profile>/`; a target whose manifest, `client-config` or `client-config-flat` is missing aborts the run. |
| `build-profile-definition.sh` | `WORLD PROFILE CLIENT_TYPE` or `WORLD PROFILE CLIENT_TYPE SOURCE_MANIFEST CONFIG_DIR OUTPUT [CONFIG_OVERLAY_DIR]` | Writes the profile-definition ZIP | Wrapper around `cmd/profile-definition-builder`. Both forms require `VALHEIM_PROFILE_AUDIENCE` to be `player` or `admin`. The three-argument form derives every path from `VALHEIM_PROFILE_SOURCE_ROOT/profiles/<PROFILE>/` and writes into the world's `mods/manager/exports`; it also resolves the Flat companion from the profile's `valheimvr-artifacts.json`, falling back to `VALHEIM_SHARED_MODS_ROOT`. The explicit form takes absolute paths. `CLIENT_TYPE` is `flat` or `vr`. When an overlay directory is present it is copied over the base config into a temporary merged directory. |
| `build-valheimvr-artifact.sh` | `--source-root DIR --template ZIP --output ZIP --client-type flat\|vr` (`--configuration Release\|SyncOnlyRelease`, `--refs DIR`, `--bepinex DIR`) | Writes the output ZIP; prints one JSON object with `artifact`, `sha256`, `valheimvr_dll_sha256` and `configuration` | Builds a ValheimVR client artifact: runs `build-valheimvr.sh`, swaps the DLL into the template archive and rezips deterministically. `--client-type flat` requires `nonVrPlayer = true` in the template config, strips the superseded `ValheimVRFlatDodgePatchFix.dll`, and yields the `flat_companion` artifact. `--client-type vr` requires `nonVrPlayer = false` and yields the ValheimVR release archive that `build-vr-runtime-artifact.sh` takes as input. Aborts if the Flat dodge guard is missing from `ControlPatches.cs`. Replaces the deleted PowerShell companion builder and the Windows `make-release.cmd` staging. |
| `build-valheimvr.sh` | `--source-root DIR --output DLL` (`--configuration Release\|Debug\|SyncOnlyRelease\|SyncOnlyDebug`, `--refs DIR`, `--bepinex DIR`) | Writes the DLL; prints one JSON object with `valheimvr_dll`, `valheimvr_dll_sha256`, `configuration`, `sources` and `references` | Compiles `ValheimVRMod.dll` from a ValheimVR checkout with Mono's `mcs`, using the defines `ValheimVRMod.csproj` sets per configuration. `--refs` defaults to `<source-root>/build/latest`; the reference assemblies are the game's own and are not vendored here. Requires `-langversion:latest`, which it passes — see [valheimvr-packaging.md](valheimvr-packaging.md). |
| `build-vr-runtime-artifact.sh` | `VALIDATED_VHVR_RELEASE_ZIP PORTAL_VR_RUNTIME_ZIP` | Writes the output ZIP | Two-line wrapper that runs `go run ./cmd/vr-runtime-builder` with the two paths. Its input is what `build-valheimvr-artifact.sh --client-type vr` produces. |
| `build-windows-client.sh` | `[OUTPUT_EXE]` | Writes the executable, default `dist/ValheimProfileSync.exe`; prints its path | Cross-compiles the Windows GUI client for `windows/amd64` from a VCS-free staging copy of the tree, with `-trimpath -buildvcs=false` and `-H=windowsgui`, stamping `internal/version.Version`. |
| `install-portal.sh` | `[ACTION] [options]` where `ACTION` is `install` (default), `verify`, `uninstall` or `print-config`; options are `--config FILE`, `--dry-run`, `--skip-build`, `--purge`, `--allow-broad-proxy-cidr`, `--allow-bridge-gateway-proxy-cidr`, `--allow-public-bind`, `--allow-insecure-base-url`, `-h`/`--help` | Provisions the host: users and groups, `/etc/valheim-portal`, the agent binary and unit, generated secrets, the compose deployment, and — when `PORTAL_ENABLE_AGENT_BRIDGE=true` — the runner binary, both runner units, and the path unit that wakes it | Installs, verifies or removes a complete portal deployment and refuses configurations that would expose administration to the public internet. `--allow-bridge-gateway-proxy-cidr` accepts an admin-token-only boundary when the proxy reaches a published port and Docker therefore NATs every request to the bridge gateway. See [installation.md](installation.md) for the required configuration and [deployment-layout.md](deployment-layout.md) for everything an install places outside the checkout. |
| `mount-windows.sh` | `[SHARE...]` | Creates mount points under `$VALHEIM_WINDOWS_MOUNT_ROOT` and mounts CIFS shares with `sudo` | Mounts the Windows gaming host's Valheim installs, which is how this Linux host reaches a real client installation. Requires `VALHEIM_WINDOWS_HOST` and `VALHEIM_WINDOWS_USER`; prefers `VALHEIM_WINDOWS_CREDENTIALS` over a prompt. Skips shares already mounted. |
| `publish-flat-release-plan.sh` | `PLAN DATABASE ARTIFACT_ROOT [ACTOR]` | Writes the portal SQLite database and the artifact root | Validates a schema-1 publication plan, checks that the companion and every payload exists, then runs `go run ./cmd/seed-release` once per target with `--client-type flat`. `DATABASE` and `ARTIFACT_ROOT` must be absolute. `ACTOR` defaults to `flat-release-publisher`. |

## Python tools run by hand (tools/)

Nothing in this repository invokes these; an operator runs them while deciding
whether a mod may enter a VR profile. Every path is an argument, so they need
no `VALHEIM_ROOT` and run from any directory. All three share one exit-code
contract: `0` nothing at or above the threshold, `1` findings at or above it,
`2` the tool could not run — which is what makes them usable as CI gates.

| Tool | Flags | Mutates | Purpose |
|---|---|---|---|
| `tools/vr_impact_scan.py` | `--packages DIR` (`--manifest`, `--json`, `--min-severity info\|low\|medium\|high`, `--package NAME` repeatable, `--vhvr-source`, `--vhvr-controls`, `--adopt-list`, `--cap N`, `--quiet`) | Nothing; reads the package cache and writes only `--json` | Stage 1 of mod onboarding. Opens every package ZIP, parses each managed assembly with `dnfile` and walks its IL, reporting the seven known VHVR-incompatibility classes with the concrete symbol and containing method behind each. Recovers the custom ZInput button names and `KeyboardShortcut` defaults that remediation needs. Requires the `dnfile` package. |
| `tools/vr_perf_ingest.py` | `--bundle ZIP\|DIR\|LOG` (`--baseline`, `--static`, `--json`, `--min-severity`, `--startup-ms`, `--frame-ms`, `--min-startup-ms`, `--top N`) | Nothing; writes only `--json` | Stage 5. Reads a client diagnostics bundle for per-plugin startup and steady-state cost, joins it to the stage-1 findings passed as `--static`, and emits one dossier per mod placing it in a cost/compatibility quadrant. `--baseline` adds deltas against a pre-install bundle. Standard library only. |
| `tools/vr_scan_common.py` | none — an import, not a program | Nothing | The severity vocabulary, manifest parsing, join-key normalisation and exit codes both scanners share. |

Read [vr-impact-scan.md](vr-impact-scan.md) for what each finding class means
and how to read the reports, and [mod-onboarding.md](mod-onboarding.md) for the
process the two stages sit in.

## Environment variables read by the build scripts

| Variable | Read by | Effect |
|---|---|---|
| `VALHEIM_PROFILE_SOURCE_ROOT` | `build-flat-release-plan.sh`, `build-profile-definition.sh` (three-argument form) | Root of the fleet tree holding the shared profile store, `profiles/<PROFILE>/`. Required with no default in both scripts: the old fallback was the original author's absolute path, so every other host silently built against profiles that were not there. |
| `VALHEIM_PROFILE_AUDIENCE` | `build-profile-definition.sh` | `player` or `admin`, required with no default, passed through as the builder's `-audience`. It decides who the portal offers the edition to, so a wrong value is how the admin console reaches ordinary players. |
| `VALHEIM_PACKAGE_BASE_URL` | `build-profile-definition.sh`; also read directly by `cmd/profile-definition-builder` as the default for `-package-base-url` | Base URL of an approved Thunderstore archive mirror. Unset means the public package endpoint. |
| `VALHEIM_SHARED_MODS_ROOT` | `build-profile-definition.sh` (three-argument form) | Directory holding the shared `valheimvr-artifacts.json`, used only when the profile has no artifact map of its own. Defaults to a path resolved relative to this repository. |
| `VALHEIM_DEBUG_LOGGING` | `build-profile-definition.sh` | Exactly `1` adds `-debug-logging` to the builder invocation, forcing verbose client diagnostics and startup profiling into the built profile. |
| `PORTAL_VERSION` | `build-windows-client.sh`, `install-portal.sh` | Overrides the build identity stamped into the client binary and into the portal container's `GET /version`. |
| `PORTAL_INSTALL_ROOT` | `install-portal.sh` | Staging prefix for every host path the installer writes. Tests and packagers set it; a real install leaves it empty. `PORTAL_ETC_DIR`, `PORTAL_BIN_DIR` and `PORTAL_UNIT_DIR` override individual locations in the same way. |

## Notes

`cmd/valheim-flat-release-publish/` and `cmd/midgard-sync/` are empty,
untracked directories containing no code, and are not commands. The usage text
of `scripts/build-flat-release-plan.sh` still says its plan is written "for
valheim-flat-release-publish"; the real publisher is
`scripts/publish-flat-release-plan.sh`, which shells out to
`go run ./cmd/seed-release`.

`scripts/mount-windows.sh` is an author-specific CIFS helper. It hardcodes a
private SMB host and a fixed set of share and mount-point names, and it is not
part of any supported workflow. Nothing else in the repository calls it.

`scripts/build-windows-client.sh` resolves its version from `PORTAL_VERSION`,
then `git describe --tags --always --dirty`, then the literal `dev`. Building
from a source tarball or any checkout without git therefore produces a client
stamped `dev` with no warning, and a support report from that binary cannot be
matched to a release. Set `PORTAL_VERSION` explicitly whenever git metadata may
be absent.

`release-targets.json` is operator data, not source: it names the owner's real worlds,
so it is gitignored and a fresh clone has only `deploy/release-targets.json.example`.
Copy it before publishing anything:

```sh
cp deploy/release-targets.json.example release-targets.json
```

Schema 1, with a `flat` array and a `vr` array. Every entry declares `world`,
`source_profile`, `published_profile`, `valheim_vr` (boolean) and `audience`
(`player` or `admin`). All five are required and none has a default.
`source_profile` names a profile in the shared store, so several editions are built
from one profile, and `valheim_vr: false` is what makes the builder run `-true-nonvr`
instead of attaching the Flat companion. The example ships the four-edition shape
for one world. The two consumers read different
halves. `build-flat-release-plan.sh` reads `flat` only. The mod controller,
`tools/valheim_mods.py`, reads **both** arrays to work out which
published releases a package removal invalidates — that is what raises the
client-release cutover that `start_valheim_server.sh` refuses to start past.

A missing file is not an error in that second path: `client_release_targets` returns
an empty list, so no cutover is ever recorded and the release-status gate always
passes. The visible effect is a server running without a package while every player's
client release still expects it. Copy the file even if you are not publishing yet.
