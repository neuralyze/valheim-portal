# ServerCharacters

`Smoothbrain-ServerCharacters` stores each player's character on the server instead of on
their machine. On this fleet it is the mechanism for handing out kits and skills:
`CharacterTemplate.yml` gives a brand-new character a whole tier's gear and skills with no
command run anywhere, and the web API raises or resets a skill on a stored character
whether or not its owner is online. Both beat RCON `give`, which needs the player online
and drops the items on the ground for DropCleaner to eat five minutes later.

## Read this first

- **Install 1.4.17, from Hexium.** Thunderstore's newest is 1.4.16 and it is fatal on
  Valheim 1.0.12. `tools/valheim_mods.py` handles this; see "How it is installed" below.
- **Never pin `Smoothbrain-ServerCharacters` 1.4.16 on a 1.0.x server.**
- **Never fork, mirror or embed this mod.** It has no LICENSE file - all rights reserved -
  and the author refused a public fork-and-redistribute request on 2026-09-11. The full
  record and the quote are in `UPSTREAM-request-1.4.17-release.md`. That file is the one
  to read before touching anything here.
- **Never install `ReefTeam-ReefCharacters` alongside it.** The two refuse to load
  together.
- `build.sh` in this directory builds the mod from source. That build is a **diagnostic
  and cross-check tool only**. It is not shipped and must not be.

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

The author stopped publishing to Thunderstore and moved to **Hexium**, so 1.4.17 exists
only there. `tools/valheim_mods.py` carries a one-entry `HEXIUM_PACKAGES` allowlist and
shadows the Thunderstore entry unconditionally - if Hexium cannot be reached the
identifier is dropped from the registry and `add` reports it as unknown, because not
installing is better than installing 1.4.16.

```sh
python3 tools/valheim_mods.py --profile <p> add Smoothbrain-ServerCharacters
```

The manifest entry gains `"source": "hexium"`; nothing else changes, and no other
package's entry is touched.

At publish time `cmd/profile-definition-builder` resolves that source against Hexium's
versioned API, downloads the archive to hash it, and writes an absolute `url` into the
published definition alongside the usual `sha256` and `size`. `cmd/valheim-profile-sync`
then fetches from that URL instead of the Thunderstore CDN.

Three properties hold this together, and a change here must preserve all three:

- **We redistribute nothing.** The definition carries a URL and a hash. Each client
  downloads the author's unmodified archive from his own CDN, exactly as the Gale mod
  manager does and exactly as the author tells people to.
- **Integrity is unchanged.** The `sha256` in the definition is still authoritative and is
  still checked by `downloadVerified` on every byte that lands on disk. The URL only says
  where to look. `packageDownloadURL` additionally restricts it to https on
  `cdn.hexium.gg` or `gcdn.thunderstore.io`, because a profile definition is data handed
  to a client, not a capability to fetch from anywhere.
- **`url` is omitted for every Thunderstore package.** Installed clients decode
  `profile-manifest.json` with `DisallowUnknownFields`, so a key that appeared
  unconditionally would break every install - that is exactly what an `audience` field did
  on 2026-08-17. `TestThunderstorePackageEntriesCarryNoNewKeys` pins this: a definition
  containing no Hexium package encodes byte-identically to one built before the field
  existed, which is what keeps the four real worlds untouched.

A client that predates this support cannot install a profile that carries a Hexium
package - it rejects the unknown `url` key. That is unavoidable and not a regression: such
a client could not obtain the mod at all. **Publishing an Ulfsland edition carrying
ServerCharacters therefore requires shipping a rebuilt client first**, and ServerSync
enforces it from the other side too: 1.4.17 sets `MinimumRequiredVersion = "1.4.17"` with
`ModRequired = true`, so a 1.4.16 client is rejected at the handshake.

**Retire this when** the author publishes to Thunderstore again, or we drop the mod: delete
`HEXIUM_PACKAGES` in `tools/valheim_mods.py`, and once no published definition carries a
`url`, the `url` handling in the builder and the client too.

## Building from source (diagnosis only)

```sh
tools/servercharacters/build.sh [OUTPUT.zip]
```

Clones the pinned upstream commit, publicizes the game assemblies with `publicize.cs`,
compiles in a throwaway `mcr.microsoft.com/dotnet/sdk:8.0` container and ILRepacks. Three
files here exist because upstream's own build cannot run on this host: `ServerCharacters.csproj`
(upstream's wants a NuGet `packages/` tree and hand-publicized assemblies),
`publicize.cs`, and `Request.cs` - upstream generates that from `Request.proto` with
protogen, a .NET tool this build cannot run, so every member was read back out of the
shipped assembly with Cecil and reproduced exactly. `mcs` cannot build the sources at all;
they need C# 10 and 12 features.

This was worth doing once: it produced the defect list, and diffing its unresolved
reference set against the author's published 1.4.17 found the two **byte-for-byte equal**,
which validated both the artifact and the analysis. See the "reference graph" section of
`tools/modpatches/README.md` for the technique. Do not ship the output.

## CharacterTemplate.yml

The server reads `CharacterTemplate.yml` from its own plugin directory
(`config_merged/bepinex/plugins/ServerCharacters/`), watches it, and ServerSyncs the text
to every client. A client creating a NEW character wipes the default starting inventory
and applies the template. Measured: the file was read at plugin load, and re-read 17 ms
after being touched - switching tiers needs no restart.

```yaml
skills:                 # name -> float, via Skills.CheatRaiseSkill; modded skills work
  Swords: 30.0
items:                  # prefab -> count, via Inventory.AddItem(name, count, 1, 0, 0, "", false)
  SwordIron: 1
spawn:                  # list of INTEGER points; one is chosen per player by user-id hash
  - {x: 0, y: 30, z: 0}
```

Those three keys and nothing else: the deserializer does not ignore unmatched properties,
and the only `catch` around it does not catch YamlDotNet's exception. Quality is hardcoded
to 1, so an upgraded kit cannot be expressed. `CheatRaiseSkill` adds to the current level,
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
