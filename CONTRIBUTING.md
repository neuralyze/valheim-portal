# Contributing

## The repository is self-contained; two paths are not

Clone it anywhere. `hostops/` holds the world operation scripts the agent
executes and `tools/` holds the Python they delegate to; the scripts resolve
`tools/` as a sibling of `hostops/`, so keep those two together and nothing else
about the checkout matters.

What is *not* in the repository is the Valheim world root and the
`valheim-server-docker` checkout. Both are configuration -- `VALHEIM_ROOT` (or
`AGENT_WORLD_ROOT` / `VALHEIM_WORLD_ROOT`) and `VALHEIM_SERVER_DOCKER_DIR` --
resolved in `hostops/lib/common.sh` and `tools/portal_paths.py`. Neither has a
default, and a script that needs one exits 78 naming it. Do not add a default,
and do not reintroduce a path relative to the checkout: these scripts stop and
delete servers, and a guessed path stops or deletes the wrong one.

See [docs/repository-layout.md](docs/repository-layout.md) for the full tree and
[docs/prerequisites.md](docs/prerequisites.md) for what you need installed
before any of the commands below will work.

## Always pass `-buildvcs=false`

Plain `go build ./...` fails when the checkout sits inside another VCS working
copy, which is how the original deployment is laid out:

```
error obtaining VCS status: multiple VCS detected: .git, .hg
```

The Go toolchain refuses to guess which repository stamps the build. Every build
here therefore passes `-buildvcs=false`, including the ones in
`scripts/build-windows-client.sh` and `scripts/install-portal.sh`. It is
harmless in a standalone clone and required in a nested one.

## The local check loop

Run all of these before opening a pull request:

```sh
gofmt -l .
go mod tidy && git diff --exit-code go.mod go.sum
go build -buildvcs=false ./...
go vet ./...
go test ./...
go test -race ./...
shellcheck -S style scripts/*.sh hostops/*.sh hostops/lib/common.sh hostops/tests/*.sh
for t in hostops/tests/*.sh; do bash "$t"; done
( cd tools && python3 -m pytest -q )
```

`gofmt -l .` must print nothing. `git diff --exit-code go.mod go.sum` must exit
zero, which is what makes `go mod tidy` a check rather than a mutation.

## `dist/ValheimProfileSync.exe` is a tracked artifact

The Windows client binary is committed. Produce it only with:

```sh
./scripts/build-windows-client.sh
```

A plain `go build` of `./cmd/valheim-profile-sync` produces a binary that looks
correct on disk and is wrong for a player: it links the console subsystem, so
Windows opens an empty console window beside the application, and it leaves the
build identity unstamped, so a support bundle cannot be matched to a release.
The build script sets `-H=windowsgui -s -w` and stamps
`internal/version.Version` from `git describe`.

This is enforced, not merely requested. `go test ./cmd/valheim-profile-sync`
rejects a tracked artifact that is console-linked, unstamped, or unstripped, and
the portal itself refuses to serve one, reporting the reason on `/admin` and to
the download.

## Commits and pull requests

* One logical change per commit. A commit that both moves code and changes
  behaviour cannot be reviewed or reverted cleanly.
* Write the commit message for someone bisecting in a year: what changed, and
  why it had to change.
* Say in the pull request which commands from the check loop you ran, and on
  what. If a change touches the installer, the agent, or the admin path, say
  whether you exercised it against a real deployment or only in tests.
* Documentation changes go in the same commit as the behaviour they describe.

## Behavioural changes need a test

Any change to observable behaviour needs a test that fails before the change and
passes after it. Write the test first and watch it fail; a test that passes
against the unfixed code is not evidence of anything. Refactors that preserve
behaviour need no new test, but must leave the existing suite green, including
`go test -race ./...`.

## The installer derives its own requirements

`scripts/install-portal.sh` reads the list of required host operation scripts out
of `internal/agent/agent.go` rather than carrying its own copy. Keep it that way.
It has already paid for itself: the requirement grew from 18 to 20 scripts without
anyone touching the installer, and a hand-maintained list would have drifted
silently.

## Traps that have already cost someone a day

* **`set -e` and trailing conditionals.** A shell function ending in
  `[[ x ]] && warn ...` returns 1 when the test is false, which kills the whole
  script under `set -e`. This bit the installer twice. End check functions with an
  explicit `return 0`.
* **Verify in a clean environment.** `docker compose config` once passed only
  because a variable had leaked into the shell from an earlier `set -a; . ./.env`.
  Under `env -i` it failed correctly. Sourcing `.env` in a persistent shell hides
  exactly the breakage you are testing for.
* **Permission-blind checks lie.** As a non-root user `[[ -f /etc/... ]]` is false
  when the directory is not searchable, so a dry run once reported "generated" for
  a secret that already existed. Report "cannot tell" rather than guessing.
* **A staging prefix must cover every path.** An uninstall routine that resolves
  most paths through the prefix and one path directly will delete the real thing
  during a sandbox test. Route every path through one helper.
* **The database is WAL-mode.** Copying `portal.sqlite` without
  `portal.sqlite-wal` yields a stale snapshot in which recently archived releases
  still look published. Copy all three files, or query through the running service.

## House rules

* The licence is **AGPL-3.0** (`LICENSE`). Contributions are accepted under it.
  By opening a pull request you agree your contribution is licensed that way.
  AGPL was chosen because this is network server software: plain GPL lets someone
  run a modified copy as a public service and never publish the changes, and
  section 13 closes that gap. A consequence worth knowing before you send a large
  patch: relicensing later only works while one party holds all the copyright, so
  accepting outside contributions without a CLA or DCO forecloses that option
  permanently.
* No emoji, in code, comments, commit messages, or documentation.
* Comments explain **why**. The code already says what it does; a comment that
  restates it is noise that will drift out of date.
* Never commit a secret, a real world name, a hostname, or an absolute host
  path. Use `<WORLD>`, `<world-root>`, and `portal.example.com` style
  placeholders in documentation, examples, and tests.
