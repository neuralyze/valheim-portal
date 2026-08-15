# Development and verification

Two different things get verified here, and conflating them is why the old single
command block did not work from a clean clone. The Go checks need nothing but this
repository. The compose smoke test needs a provisioned host.

See [prerequisites.md](prerequisites.md) for versions and
[repository-layout.md](repository-layout.md) for why `-buildvcs=false` is mandatory.

## What a clean clone can verify

Everything below runs against a fresh clone with no configuration, no world root, no
agent, and no Docker.

One command runs every gate CI runs, cheapest first, stopping at the first failure and naming
it. Prefer it to the list below: the list is what it does, and a list is what people skip.

```sh
scripts/check.sh                 # all thirteen gates; about ten seconds warm, a minute cold
scripts/check.sh --list          # what they are
scripts/check.sh --only gofmt    # reproduce one failure
scripts/check.sh --skip gotest   # everything but the race suite
```

It reports the failing gate and the command to reproduce it, because the reason this script
exists is that `shellcheck` sat red on `scripts/republish-profiles.sh` for weeks while everyone
"ran the tests".

```sh
cd "$(git rev-parse --show-toplevel)"
gofmt -l .                                      # must print nothing
go mod tidy && git diff --exit-code go.mod go.sum
go build -buildvcs=false ./...
go vet ./...
GOOS=windows go vet ./...                       # the client is Windows-only code
go test ./...
go test -race ./...
shellcheck scripts/*.sh
```

Plain `go build ./...` fails with `multiple VCS detected` in the standard layout. That
is expected; pass `-buildvcs=false`.

`gofmt -w` is the fix, but run it on the files you touched, not the tree — a
repository-wide reformat buries the change under noise.

Build the Windows client, which also cross-compiles from Linux:

```sh
PORTAL_VERSION=dev-local ./scripts/build-windows-client.sh
file dist/ValheimProfileSync.exe                # must report (GUI), not (console)
```

`(console)` means somebody built it with plain `go build`; Windows then opens an empty
console window beside the application, and the binary carries no version stamp. Both
`go test ./cmd/valheim-profile-sync` and the portal itself reject such an artifact.

### What the unit suite covers

`cmd/profile-definition-builder` tests use local HTTP fixtures and temporary managed
profile manifests and config inputs. `cmd/valheim-profile-sync` tests cover protocol
parsing, scoped manifest and payload behaviour, profile isolation, unchanged/changed/
removed packages, checksums, archive safety, rollback, locking, shortcuts, and launch
construction. `internal/app` tests cover Steam authorization, profile-scoped releases,
device exchange, artifact isolation, and measured world liveness.

The unit suite never contacts production Steam, Thunderstore, the portal volume, or
Valheim world data. Production profile builds may contact the fixed Thunderstore CDN,
and only to hash the exact packages declared in the managed profile manifest.

## What a clean clone cannot verify

`docker compose up -d --build` does not work from a clone. `compose.yaml` declares
eight variables with `:?` defaults, meaning Compose aborts if any is unset:

| Variable | What it needs |
|---|---|
| `PORTAL_TRUSTED_PROXY_CIDR` | The reverse proxy's address as the container sees it |
| `PORTAL_PUBLIC_BASE_URL` | The exact public HTTPS origin; no default |
| `PORTAL_AGENT_GID` | The resolved GID of the host agent's socket group |
| `VALHEIM_WORLD_ROOT` | An existing world root to mount read-only |
| `PORTAL_AGENT_SOCKET_DIR` | The directory the running agent's socket lives in |
| `PORTAL_CSRF_SECRET_FILE` | An existing 32-byte secret |
| `PORTAL_AGENT_TOKEN_FILE` | An existing 32-byte secret, shared with the agent |
| `PORTAL_ADMIN_TOKEN_FILE` | An existing 32-byte admin token, also sent by the proxy |

`/healthz` answers as soon as the process is serving. **`/readyz` does not**: it
reports whether the portal reached the agent over its Unix socket, so it fails until a
real agent is running, the socket exists at `PORTAL_AGENT_SOCKET_DIR`, the container's
`group_add` GID matches the socket group, and both halves hold the same agent token.
There is no way to satisfy it without the host side.

`docker compose config` is the part that *is* checkable from a clone, because it
resolves and validates the merged configuration without starting anything. It does not
open any of the files a path variable names, so placeholder paths are fine.

CI runs it against `.env.example`, which is therefore required to define all eight
variables above — `.env.example` failing `docker compose config` is a bug in
`.env.example`, not in the check. Reproduce it without touching your own `.env`:

```sh
docker compose --env-file .env.example config -q
```

## Compose smoke test

Prerequisites, all of them:

1. A world root with at least one world directory containing a `valheim.env`.
2. The host agent installed and running, with its socket present.
3. The three secret files, each at least 32 bytes.
4. A `.env` beside `compose.yaml` supplying all eight required variables.
   `scripts/install-portal.sh` writes a correct one; copying `.env.example` is enough
   for `docker compose config` but not for a working stack.

Then:

```sh
docker compose config                           # resolve and validate, start nothing
docker compose up -d --build
curl --fail http://127.0.0.1:18080/healthz
curl --fail http://127.0.0.1:18080/readyz       # proves the agent socket pairing
```

A `readyz` failure is almost always a `PORTAL_AGENT_GID` mismatch or a stopped agent;
see the troubleshooting table in [installation.md](installation.md#troubleshooting).

The installer does all of this for you, including the checks a curl cannot make:

```sh
sudo ./scripts/install-portal.sh verify
```

That is the only check that exercises the real trust boundary end to end, because it
also probes the public origin for administrative identity spoofing. Run it after any
change to the deployment or the reverse proxy.
