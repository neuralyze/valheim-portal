# Keeping up with what we build from

This deployment depends on other people's work in three different ways, and each has its
own freshness check. Two of them existed already. The third did not, and its absence showed:
on 2026-08-18 the VR mod's checkout was a commit behind upstream, and the container project
had changed owner months earlier — `lloesche/valheim-server-docker` is now
`community-valheim-tools/valheim-server-docker` — while our remote URL and our docs still
named the old owner.

| What | Where it comes from | How freshness is checked |
| --- | --- | --- |
| Mods players and servers run | Thunderstore | `manage_mods.sh <WORLD> check-updates` |
| Go libraries | `go.mod` | `go mod tidy` in the check suite; upgrades are deliberate edits |
| **Projects we build source from** | git checkouts outside this repo | `deploy/upstream-sources.json` plus `tools/upstream_sources.py` |

## The rule

Not "always be on the newest commit". Forcing that would fail unrelated work every time
somebody else pushes, and would pull in changes nobody had read.

The rule is that upstream movement has to be **seen**. The registry records two commits per
source, and they answer different questions:

- `pinned_commit` — what our checkout is actually on. It may be a commit of **ours**: the
  container checkout sits on a local commit on top of upstream, and the artifacts we ship are
  built from that tree, so this is the commit that describes what we published.
- `reviewed_commit` — the newest **upstream** commit somebody has read. Never a local commit,
  or `status` could never clear.

`status` fails while upstream is ahead of the reviewed commit. Clearing it is a decision
somebody makes and records:

```bash
python3 tools/upstream_sources.py status                     # where is everything
python3 tools/upstream_sources.py review vhvr \
  --commit 50d333d4fcae \
  --note "Read 1 commit: gestured draw refinement. Take it with the next VR build."
```

A review may name a commit we are **not** on — reading what is coming before taking it is
normal — but then it has to carry a note, or `verify` fails.

## The two checks

`verify` is offline and runs in the ordinary check suite as the `upstream` gate. It asserts
that the registry is well formed and that any checkout present on this machine is on the
commit the registry pins. That matters because our published artifacts are built from those
trees: the commit is part of what we shipped. It also fails when a checkout has modified
files and the registry does not say what they are.

`status` reaches GitHub, so it is a periodic run rather than a build step. Run it weekly, and
whenever a build is about to ship something to players.

## What is registered today

`vhvr` — `brandonmousseau/vhvr-mod`, GPL-3.0. We build it and ship the result as the Flat
companion and the VR runtime, which is why its licence obliges us to offer the corresponding
source for what we ship. The checkout carries uncommitted local changes: two are offered
upstream (the non-VR `HarmonyPrepare` guards and a `csproj` PostBuild condition), the rest
are ours — a body-tracking revert, Linux toolchain fixes and diagnostics.

`valheim-server-docker` — `community-valheim-tools/valheim-server-docker`, Apache-2.0. The
container the world operation scripts drive; `valheim_provision.py` reads its `default.env`
for the PGID a world's mounts are chowned to. The checkout sits on one local commit,
`3ad0632`, and carries untracked `default.env` and `discord.env` which hold deployment values
and must never be offered upstream. Eighteen upstream commits are read but not taken: two
base-image upgrades (Debian 12 then 13), busybox cron replacing Debian's, a UMASK change that
would land on the directories provisioning chowns, and a libdoorstop env-var rename our local
commit already made independently — so upgrading converges rather than conflicts there.

## Adding a source

Add an entry to `deploy/upstream-sources.json` with every field in `REQUIRED_FIELDS`, then
run `python3 tools/upstream_sources.py verify`. `why` is not decoration: it is what tells the
next person whether a given upstream change can affect players, a server, or only a build.
