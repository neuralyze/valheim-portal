# The ServerCharacters archive is built here, not committed here

`ServerCharacters.zip` belongs in this directory and is **gitignored**. Everything that
ships it - the Windows client, the profile-definition builder, the server deploy - reads it
from exactly this path.

Produce it with:

```sh
tools/servercharacters/build.sh
```

That clones `blaxxun-boop/ServerCharacters@bb7d3cd6`, applies every patch in
`tools/servercharacters/patches/`, compiles the result, and writes the archive here. Without
it, `go build` still succeeds and a profile that does not select
`Smoothbrain-ServerCharacters` still publishes; a profile that DOES select it fails naming
this path, the same way `tools/worldseed` fails when its patch has not been built.

The build is **not** upstream's. It declares `ModVersion 1.4.17.1`, and the fourth component
exists so these bytes can never be mistaken for the author's 1.4.17. The patches are
committed - they are our own text and they are the record of exactly what diverges.

## Why it is not in git

The repository is published - it carries `LICENSE`, `NOTICE` and
`docs/public-distribution.md`. ServerCharacters has no licence file, so it is
all-rights-reserved, and its author declined a fork-and-redistribute request for a MODIFIED
build on 2026-09-11. Since 2026-09-15 a modified build is what we run, so this boundary is
doing more work than it was: running our own patched compile on the operator's own server and
their own clients is the operator's call about their own machines, which they made on
2026-09-14 and extended on 2026-09-15. Committing the binary here would turn that private
choice into public redistribution, which is not theirs to make.

`tools/servercharacters/README.md` holds the full record and the retirement condition.

## Retirement

When Thunderstore carries 1.4.17, delete this directory, `internal/servercharacters`, and
the blocks that reference them - each is marked `TEMPORARY: local ServerCharacters build`.
A plain `tools/valheim_mods.py add Smoothbrain-ServerCharacters` then replaces all of it -
but read `retire_when` in `deploy/upstream-sources.json` first: the Thunderstore build will
not carry the `quality`, `equip` and `contents` template keys, so swapping to it returns
every templated character to unarmoured, quality-1 gear unless the presets change too.
