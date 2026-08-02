# Repository layout

This repository is self-contained. Clone it anywhere: it carries the Go portal, the
host operation scripts the agent executes, and the Python tools those scripts delegate
to. Two paths remain outside it, and both are explicit configuration.

```text
valheim-portal/
├── cmd/                           # portal, agent, and the standalone builders
├── internal/                      # portal, agent, and domain packages
├── client/                        # Windows profile-sync client assets
├── hostops/                       # AGENT_SCRIPT_DIR: the world operation scripts
│   ├── lib/common.sh              # VALHEIM_ROOT / VALHEIM_SERVER_DOCKER_DIR resolvers
│   ├── tests/                     # bash regression tests for the scripts
│   ├── worlds.txt.example         # bulk-script world list; copy to worlds.txt
│   └── *.sh                       # 20 agent operations plus the operator scripts
├── tools/                         # Python the scripts delegate to, plus the VR scanners
│   ├── portal_paths.py            # the same two resolvers, for the Python half
│   ├── valheim_mods.py            # manifest-driven mod controller
│   ├── valheim_provision.py       # transactional world provisioning
│   ├── valheim_worldgen.py        # seeded world creation
│   ├── valheim_world.py           # .fwl parsing
│   ├── valheim_profile_catalog.py # profile listing
│   ├── vr_impact_scan.py          # static VHVR-compatibility scan of mod packages
│   ├── vr_perf_ingest.py          # per-mod cost from a client diagnostics bundle
│   ├── vr_scan_common.py          # severities, exit codes and joins shared by both
│   ├── map-source-exporter/       # C# BepInEx plugin source for map export
│   └── worldseed/                 # C# seed-forcing plugin source and build script
├── scripts/                       # installer and release/build tooling
├── deploy/                        # unit and proxy examples
└── docs/
```

`hostops/` and `tools/` must stay siblings: `hostops/lib/common.sh` resolves the tools
directory from its own location, so a copy that takes only the shell scripts passes
`install-portal.sh`'s script-presence check and then fails at the first mod, provision,
or world-metadata operation. The installer checks for the sibling too.

## What is deliberately not here

| Setting | Points at | Why it is not vendored |
|---|---|---|
| `VALHEIM_WORLD_ROOT`, also accepted as `VALHEIM_ROOT` or `AGENT_WORLD_ROOT` | The world root: one directory per world, plus `world_backups` | It is operator data — saves, passwords, mod caches. |
| `VALHEIM_SERVER_DOCKER_DIR` | A checkout of the modified [valheim-server-docker](https://github.com/lloesche/valheim-server-docker) fork | A separate 29 MB Apache-2.0 project. Redistributing a modified copy carries its own obligations; see [NOTICE](../NOTICE). |

Neither has a default, in either half. `hostops/lib/common.sh` and
`tools/portal_paths.py` both exit **78** (`EX_CONFIG`) naming the variable when it is
unset, relative, or absent:

```console
$ ./hostops/stop_valheim_server.sh MyWorld
VALHEIM_SERVER_DOCKER_DIR is not set.
...
$ echo $?
78
```

That is not defensiveness. These scripts run `docker compose down`,
`docker compose rm -v` and `rm -rf` against whatever is at the configured path, so a
guessed default would tear down or delete the wrong thing. `hostops/tests/valheim_root_resolution.sh`
holds both resolvers to that contract.

A world directory looks like this:

```text
<world root>/<WORLD>/
├── valheim.env                    # per-world server environment (holds SERVER_PASS)
├── config_merged/                 # BepInEx config + worlds_local save pair
├── data/                          # container /opt/valheim, incl. data/htdocs/status.json
└── mods/                          # managed profiles and package cache
```

`compose.yaml` mounts the world root read-only at `/var/lib/valheim-worlds`, and
`PORTAL_MAP_SOURCE_ROOT` points at the same mount. World status is read from
`<world root>/<WORLD>/data/htdocs/status.json` through it; see
[operations.md](operations.md#world-status).

## The build gotcha: nested version control

If the checkout sits inside another VCS working copy — which is how the original
deployment is laid out, with this git repository inside a Mercurial one — the Go
toolchain finds two VCS roots and refuses to stamp the build:

```console
$ go build ./...
error obtaining VCS status: multiple VCS detected: git in "<...>/portal",
and hg in "<...>"
	Use -buildvcs=false to disable VCS stamping.
```

This is not a broken checkout. Pass `-buildvcs=false` to every Go command that builds:

```sh
go build -buildvcs=false ./...
go test ./...                       # unaffected: test binaries are not stamped
go vet ./...                        # unaffected
```

The shipped build paths already do this — `scripts/build-windows-client.sh` and
`scripts/install-portal.sh` both pass `-buildvcs=false`, and CI builds with it. Only a
hand-typed `go build ./...` in a nested checkout hits the error.

`build-windows-client.sh` therefore resolves the version itself rather than relying on
VCS stamping: `PORTAL_VERSION`, else `git describe --tags --always --dirty`, else the
literal `dev`. A build on a host without `git` silently produces a client stamped
`dev`, which cannot be matched to a release in a support report.

## Working on the portal alone

The Go unit suite, the bash regression tests under `hostops/tests/`, and the Python
tests under `tools/` all run from a bare clone: none touches a real world root, the
agent socket, or Docker. What a clone alone cannot do is *deploy*, because it still
needs a world root and a `valheim-server-docker` checkout to point at. See
[development.md](development.md) for the split between clean-clone checks and the
full-stack smoke test.
