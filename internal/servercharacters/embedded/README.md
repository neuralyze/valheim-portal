# The ServerCharacters archive is built here, not committed here

`ServerCharacters.zip` belongs in this directory and is **gitignored**. Everything that
ships it - the Windows client, the profile-definition builder, the server deploy - reads it
from exactly this path.

Produce it with:

```sh
tools/servercharacters/build.sh
```

That clones `blaxxun-boop/ServerCharacters@bb7d3cd6`, compiles it unmodified, and writes
the archive here. Without it, `go build` still succeeds and a profile that does not select
`Smoothbrain-ServerCharacters` still publishes; a profile that DOES select it fails naming
this path, the same way `tools/worldseed` fails when its patch has not been built.

## Why it is not in git

The repository is published - it carries `LICENSE`, `NOTICE` and
`docs/public-distribution.md`. ServerCharacters has no licence file, so it is
all-rights-reserved, and its author declined a fork-and-redistribute request on 2026-09-11.
Running our own compile on the operator's own server and their own clients is the operator's
call about their own machines, which they made on 2026-09-14. Committing the binary here
would turn that private choice into public redistribution, which is not theirs to make.

`tools/servercharacters/README.md` holds the full record and the retirement condition.

## Retirement

When Thunderstore carries 1.4.17, delete this directory, `internal/servercharacters`, and
the blocks that reference them - each is marked `TEMPORARY: local ServerCharacters build`.
A plain `tools/valheim_mods.py add Smoothbrain-ServerCharacters` then replaces all of it.
