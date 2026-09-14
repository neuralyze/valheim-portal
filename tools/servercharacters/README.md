# ServerCharacters

`Smoothbrain-ServerCharacters` stores each player's character on the server instead of on
their machine. On this fleet it is the mechanism for handing out kits and skills:
`CharacterTemplate.yml` gives a brand-new character a whole tier's gear and skills with no
command run anywhere, and the web API raises or resets a skill on a stored character
whether or not its owner is online. Both beat RCON `give`, which needs the player online
and drops the items on the ground for DropCleaner to eat five minutes later.

## Read this first

- **Install 1.4.17.** Thunderstore's newest is 1.4.16 and it is fatal on Valheim 1.0.12.
- **Never pin `Smoothbrain-ServerCharacters` 1.4.16 on a 1.0.x server.**
- **Never install `ReefTeam-ReefCharacters` alongside it.** The two refuse to load
  together.
- **1.4.17 comes from `build.sh` in this directory, and that build now SHIPS** - to the
  Ulfsland server and to the clients of its four editions, on the operator's instruction,
  as a temporary measure. The next section is the one to read before touching any of this.
- **The built binary must never be committed.** It lives at the gitignored path
  `internal/servercharacters/embedded/ServerCharacters.zip`. This repository is published.

## What we ship, and why that is temporary

This section supersedes the flat "never fork, mirror or embed this mod" rule that stood
here until 2026-09-14. That rule was not wrong about the licence; the operator made a
different call about their own machines, and this records it rather than hiding it.

**What changed.** On 2026-09-14 the operator decided to run our own compile of upstream
`bb7d3cd6` - the commit that fixes all eight 1.0.12 breakages and calls itself 1.4.17 -
rather than have each client fetch the author's build from Hexium:

> go ahead and use servercharacters 1.4.17 for now because it will get updated on
> thunderstore soon and we will fix the legal issue in the next couple days

and, asked which delivery route:

> i didnt say use the hexium path.. i mean the compile from source

**The licence facts have not changed, and they are why this is temporary.** The repository
has no LICENSE file, so it is all rights reserved, and on 2026-09-11 the author refused a
public fork-and-redistribute request - "No, sorry. Especially not on Thunderstore." The
full record and the quote are in `UPSTREAM-request-1.4.17-release.md`; read it before
touching anything here.

**The boundaries this arrangement stays inside**, all four of which are load-bearing:

- The binary is **not in git**. It is built locally into a gitignored path. Committing it
  is the step that would turn a private operational choice into public redistribution,
  which is not ours to make; see `docs/public-distribution.md`.
- It is **not published** to Thunderstore or any index, and is not offered on any portal
  page. It travels from this host into the client executable the operator downloads from
  their own portal, and nowhere else.
- It is **Ulfsland only**. The four `ulfsland-*` profiles select it; no shared profile
  does, so no Hrafnheim, Doggerland, Storgard or Vangard client receives it.
- It is **unmodified upstream source**. `build.sh` compiles `bb7d3cd6` as published - no
  patches, no retargeting, and no publicizing beyond the publicized GAME assemblies
  upstream's own build expects.

**Retire it when Thunderstore carries 1.4.17**, which the operator expects within days.
Then one command replaces the whole arrangement:

```sh
python3 tools/valheim_mods.py --profile <p> add Smoothbrain-ServerCharacters
```

and every block marked `TEMPORARY: local ServerCharacters build` comes out:
`internal/servercharacters` with its `embedded/` directory, the local-build branches in
`cmd/profile-definition-builder` and `cmd/valheim-profile-sync` (including
`servercharacters_package.go` and its test), `LOCAL_BUILD_PACKAGES` and
`local_build_package` in `tools/valheim_mods.py`, and the `.gitignore` entry. The Hexium
backend stays: it is the mechanism for the next mod that lives outside Thunderstore.

## The defect in 1.4.16

`ServerCharacters.Initialize()` calls
`PlayerProfile.GetCharacterFolderPath(FileHelpers.FileSource)`, which Valheim 1.0 moved to
a new `SaveSystem` type. Mono throws `MissingMethodException` when it JITs `Initialize`, so
the mod never initialises - but Harmony runs `Initialize` as a postfix on
`FejdStartup.Awake`, and `Awake` has already installed patches on
`PlayerProfile.LoadPlayerFromDisk`, `Player.Load`, `Skills.Load` and `Inventory.Load`. An
unpatched install is live save/load patches over an uninitialised profile store, on server
and client alike.

Seven further members are unresolvable against 1.0.12, several of them on the paths this
fleet wants. The table, and why a one-MemberRef binary patch was the wrong remedy, are in
`tools/modpatches/README.md`. Upstream fixed all eight in
`blaxxun-boop/ServerCharacters@bb7d3cd6` ("fix for deep north", 2026-09-09) = **1.4.17**.

## How it is installed

One archive, built here, installed on both sides. Build it first - nothing works without
it, and nothing carries a stale copy of it:

```sh
tools/servercharacters/build.sh
# -> internal/servercharacters/embedded/ServerCharacters.zip  (gitignored)
```

Then the ordinary mod commands do the rest:

```sh
python3 tools/valheim_mods.py --profile <p> add Smoothbrain-ServerCharacters
```

The manifest entry gains `"source": "local-build"`; nothing else changes, and no other
package's entry is touched. `tools/valheim_mods.py` shadows both indexes for this
identifier: it reads the version out of the archive's own `manifest.json`, copies the
archive into `manager-cache/packages/`, and re-copies it whenever the cached bytes differ
from the build output - which matters because the Hexium archive of the same version was
already sitting there under the same name, and reusing it would have put the author's
assembly on the server while the clients carried ours.

At publish time `cmd/profile-definition-builder` fetches nothing for this package: it
publishes the SHA256 and size of the embedded archive and **no `url`**.
`cmd/valheim-profile-sync` writes its own embedded copy into the package cache, after
checking it against those two values, and the ordinary extraction path installs it at
`BepInEx/plugins/Smoothbrain-ServerCharacters/`.

Four properties hold this together, and a change here must preserve all four:

- **Server and clients run the same bytes.** ServerSync declares
  `MinimumRequiredVersion = "1.4.17"` with `ModRequired = true`, so a client whose build
  differs from the server's is refused at the handshake. Both sides come from one archive,
  which removes that failure as a category instead of testing for it. MEASURED
  2026-09-14: the DLL the live Ulfsland server loads and the DLL a client installs are
  both `efe7d421…`.
- **The digest is still authoritative.** The definition carries the archive's SHA256 and
  size, and the client compares its embedded copy against them. A client built from a tree
  with a different archive refuses the install and says "download the current client",
  rather than installing a mismatched assembly and failing mid-join.
- **The gate is the profile, not the executable.** The client installs this only when the
  definition it is syncing names `Smoothbrain-ServerCharacters`. The four real worlds'
  editions do not, so they are untouched - MEASURED: their published definitions carry 96,
  98 and 103 packages, none of them this one.
- **No new keys in a definition.** Installed clients decode `profile-manifest.json` with
  `DisallowUnknownFields`, so a key that appeared unconditionally would break every
  install - that is exactly what an `audience` field did on 2026-08-17. A local-build
  package publishes no `url`, so an Ulfsland definition carries the same six keys per
  entry as every other world's, and `TestThunderstorePackageEntriesCarryNoNewKeys` still
  pins the Hexium side of that.

**Publishing an Ulfsland edition carrying ServerCharacters requires shipping the rebuilt
client first**, because the bytes are in the client. Build the client from the same tree as
the publish: `scripts/build-windows-client.sh`, then copy it to the deployment's `dist/`
and confirm the portal serves the new SHA256 rather than a cached older one.

The Hexium backend - `HEXIUM_PACKAGES` in `tools/valheim_mods.py`, `resolveHexiumPackageURL`
in the builder, and the `url` field plus its two-host allowlist in the client - is still
implemented and still works for any package whose author publishes there. It is simply not
what installs this one any more: the author's build is on Hexium, ours is in the client.

## Building from source

```sh
tools/servercharacters/build.sh [OUTPUT.zip]
```

Clones the pinned upstream commit, publicizes the game assemblies with `publicize.cs`,
compiles in a throwaway `mcr.microsoft.com/dotnet/sdk:8.0` container, ILRepacks, and writes
a Thunderstore-shaped archive - `ServerCharacters.dll` plus a `manifest.json` of ours - to
`internal/servercharacters/embedded/ServerCharacters.zip` unless given another path. Three
files here exist because upstream's own build cannot run on this host:
`ServerCharacters.csproj` (upstream's wants a NuGet `packages/` tree and hand-publicized
assemblies), `publicize.cs`, and `Request.cs` - upstream generates that from `Request.proto`
with protogen, a .NET tool this build cannot run, so every member was read back out of the
shipped assembly with Cecil and reproduced exactly. `mcs` cannot build the sources at all;
they need C# 10 and 12 features.

The output is reproducible, which it has to be: the archive's SHA256 is published in every
Ulfsland definition and checked by the client. MEASURED - two runs produce the same DLL
(`efe7d421…`) and the same archive (`6cdb1818…`, 482416 bytes); the file mtimes are pinned
to the DOS epoch before zipping, because `zip -X` keeps them and they were the only
remaining source of variation.

Diffing this build's unresolved reference set against the author's published 1.4.17 found
the two **byte-for-byte equal**, which validated both the artifact and the analysis. See
the "reference graph" section of `tools/modpatches/README.md` for the technique.

## CharacterTemplate.yml

The server reads `CharacterTemplate.yml` from its own plugin directory
(`config_merged/bepinex/plugins/ServerCharacters/`), watches it, and ServerSyncs the text
to every client. A client creating a NEW character wipes the default starting inventory
and applies the template. MEASURED 2026-09-14 on the live Ulfsland server: touching the
file made the server process open it and read all 7180 bytes of it (strace, one `openat`
plus reads of 4096 + 3084), which is the `FileSystemWatcher` -> `readCharacterTemplate()`
-> `playerTemplate.AssignLocalValue()` path - switching tiers needs no restart.

```yaml
skills:                 # name -> float, via Skills.CheatRaiseSkill; modded skills work
  Swords: 30.0
items:                  # prefab -> count, via Inventory.AddItem(name, count, 1, 0, 0, "", false)
  SwordIron: 1
spawn:                  # list of INTEGER points; one is chosen per player by user-id hash
  - {x: 0, y: 30, z: 0}
```

Those three keys and nothing else: the deserializer does not ignore unmatched properties,
and the only `catch` around it does not catch YamlDotNet's exception. MEASURED 2026-09-14
by deserializing through the shipped assembly's own ILRepacked YamlDotNet, with the same
`new DeserializerBuilder().IgnoreFields().Build()` call `ClientSide.cs` makes: the
`pre-bonemass` template parses to 23 skills, 30 items and 1 spawn point, and adding a
`quality:` key throws `YamlDotNet.Core.YamlException: Property 'quality' not found on type
'ServerCharacters.PlayerTemplate'` - out of a Harmony prefix, uncaught. Quality is
hardcoded to 1, so an upgraded kit cannot be expressed; `pre-bonemass` asks for quality 3
on 14 entries and they arrive unupgraded. `CheatRaiseSkill` adds to the current level,
which for a new character is the same as setting it. `Random.Range(0, spawn.Count - 1)`
has an exclusive upper bound, so the last spawn entry is never chosen unless there is
exactly one - list N+1 points for N.

`preset_to_template.py` renders any `tools/jumpstart` tier preset into that shape and
records in the file header what the format could not carry:

```sh
tools/servercharacters/preset_to_template.py pre-bonemass -o CharacterTemplate.yml
tools/servercharacters/preset_to_template.py --all -d /tmp/templates
```

It belongs in the linked server profile's `manual-mods/ServerCharacters/`, which the deploy
layers over the plugin cache; a copy dropped straight into `config_merged` is erased by the
next deploy. Note also that the running container copies `config_merged` at boot, so
editing the host copy does not reach a running server - edit inside the container, or
redeploy.

## Web API

`WebInterfaceAPI` listens on the `4 - Other` / `Webinterface listen address` config key,
default `127.0.0.1:5982`. Length-prefixed frames carrying protobuf bodies, dispatched by
reflection onto the public static methods of a private `Command` class: `GetPlayerList`,
`GetModList`, `SendIngameMessage`, `KickPlayer`, `SaveWorld`, `RaiseSkill`, `ResetSkill`,
`GiveItem`.

Request frame, all integers little-endian int32:

```
int32 totalLength            bytes that follow
int32 packetKey              caller's correlation id, non-zero for a request
int32 commandNameLength
utf8  commandName
int32 payloadLength
bytes payload                protobuf message, empty for the no-argument commands
```

The reply is framed the same way, echoes `packetKey`, and omits the command name when
`packetKey` is non-zero - so a reply body starts at offset 8, not at a name. On connect the
server pushes three unsolicited frames with `packetKey == 0` which DO carry names:
`ServerConfig`, `MaintenanceMessage`, `Ready`. A client must read past those.

`RaiseSkill` and `ResetSkill` work on OFFLINE characters: after messaging any matching
online peer they walk every `*.fch` in the character save directory, load the profile,
apply `CheatRaiseSkill`/`CheatResetSkill` and write it back. `GiveItem` is online-only - it
has no file path, it just invokes an RPC on a connected peer.

**There is no authentication.** Any process that can open the socket can raise a skill or
kick a player. The loopback bind is the only protection, and the listener is inside the
server container, so it is not reachable from the host either - verified by connecting to
`127.0.0.1:5982`, the container bridge address and `0.0.0.0` from the host, all refused.
Do not move it off `127.0.0.1`.
