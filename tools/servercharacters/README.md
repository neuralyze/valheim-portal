# ServerCharacters

`Smoothbrain-ServerCharacters` stores each player's character on the server instead of on
their machine. On this fleet it is the mechanism for handing out kits and skills:
`CharacterTemplate.yml` gives a brand-new character a whole tier's gear and skills with no
command run anywhere, and the web API raises or resets a skill on a stored character
whether or not its owner is online. Both beat RCON `give`, which needs the player online
and drops the items on the ground for DropCleaner to eat five minutes later.

## Read this first

- **Install 1.4.17.1, our build.** Thunderstore's newest is 1.4.16 and it is fatal on
  Valheim 1.0.12; upstream's unreleased 1.4.17 fixes that but equips nothing and hardcodes
  item quality to 1, which breaks the whole kit system.
- **Never pin `Smoothbrain-ServerCharacters` 1.4.16 on a 1.0.x server.**
- **Never install `ReefTeam-ReefCharacters` alongside it.** The two refuse to load
  together.
- **1.4.17.1 comes from `build.sh` in this directory, is PATCHED, and SHIPS** - to the
  Ulfsland server and to the clients of its four editions, on the operator's instruction,
  as a temporary measure. The next section is the one to read before touching any of this.
- **`patches/` is the whole divergence from upstream.** Read it before believing anything
  about this build's behaviour matches the author's.
- **The built binary must never be committed.** It lives at the gitignored path
  `internal/servercharacters/embedded/ServerCharacters.zip`. This repository is published.
  The patches, being our own text, ARE committed.

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

On 2026-09-15, after the kit system was measured to deliver unarmoured characters holding
unupgraded gear, they authorised modifying it:

> go ahead and modify the mod all you want

and, in the same session:

> also, see if you can add equipped and filled backpacks to default servercharacter as well

**The licence facts have not changed, and modifying the mod makes them matter MORE.** The
repository has no LICENSE file, so it is all rights reserved, and on 2026-09-11 the author
refused a public request to fork and redistribute a MODIFIED build - "No, sorry. Especially
not on Thunderstore." A modified build is now exactly what we run, so the only thing
separating our position from the one he declined is that we publish nothing. The full record
and the quote are in `UPSTREAM-request-1.4.17-release.md`; read it before touching anything
here.

**The boundaries this arrangement stays inside**, all four of which are load-bearing:

- The binary is **not in git**. It is built locally into a gitignored path. Committing it
  is the step that would turn a private operational choice into public redistribution,
  which is not ours to make; see `docs/public-distribution.md`.
- It is **not published** to Thunderstore or any index, and is not offered on any portal
  page. It travels from this host into the client executable the operator downloads from
  their own portal, and nowhere else.
- It is **Ulfsland only**. The four `ulfsland-*` profiles select it; no shared profile
  does, so no Hrafnheim, Doggerland, Storgard or Vangard client receives it.
- It is **patched, and the patch is visible**. `build.sh` compiles `bb7d3cd6` plus every
  file in `patches/`; nothing else - no retargeting, and no publicizing beyond the
  publicized GAME assemblies upstream's own build expects. The build REFUSES to produce an
  artifact whose `ModVersion` still equals upstream's, so a patched binary can never claim
  to be the author's. `patches/` is text we wrote and is the provenance record.

**Retire it when Thunderstore carries 1.4.17**, which the operator expects within days -
but retiring is no longer a pure subtraction, because the Thunderstore build will not carry
`quality`, `equip` or `contents`. Read `retire_when` in `deploy/upstream-sources.json`.
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
  `MinimumRequiredVersion = ModVersion` with `ModRequired = true`, so a client whose build
  differs from the server's is refused at the handshake - in BOTH directions, because
  `VersionCheck.IsVersionOk` is `ours >= their minimum && theirs >= our minimum` (MEASURED
  in IL: two `new System.Version(string)` calls and `op_GreaterThanOrEqual`, which is also
  why the version string must stay parseable by `System.Version` and a semver pre-release
  suffix would throw). Both sides come from one archive, which removes that failure as a
  category instead of testing for it. MEASURED 2026-09-14, before the patches: the DLL the
  live Ulfsland server loads and the DLL a client installs were both `efe7d421…`. The
  patched build is `831dde63…` in archive `f4acc024…`, and until both sides carry it the
  live server is still on `efe7d421…` / 1.4.17.
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
Ulfsland definition and checked by the client. MEASURED - the UNPATCHED build of `bb7d3cd6`
produces DLL `efe7d421…` and archive `6cdb1818…` (482416 bytes) on every run, which was
re-confirmed on 2026-09-15 before any patch was written; the patched build produces DLL
`831dde63…` and archive `f4acc024…` (486149 bytes). The file mtimes are pinned to the DOS
epoch before zipping, because `zip -X` keeps them and they were the only remaining source of
variation.

Diffing the UNPATCHED build's unresolved reference set against the author's published 1.4.17
found the two **byte-for-byte equal**, which validated both the artifact and the analysis.
That property belonged to the unpatched build and does not transfer: our build is no longer
the author's, which is what the fourth version component says. Reproducing it is one command
- `SERVERCHARACTERS_WORK=/tmp/x build.sh /tmp/unpatched.zip` with `patches/` moved aside - and
is worth doing before trusting any claim that a behaviour difference comes from the patch.
See the "reference graph" section of `tools/modpatches/README.md` for the technique.

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
items:                  # prefab -> count, via Inventory.AddItem
  SwordIron: 1
  ArmorIronChest: 1
  ArmorRootChest: 1
  CapeIronBackpack: 1
quality:                # prefab -> quality; absent means 1. OUR BUILD ONLY (1.4.17.1+)
  SwordIron: 3
  ArmorIronChest: 3
equip:                  # ordered prefab list, HIGHEST PRIORITY FIRST. OUR BUILD ONLY
  - ArmorIronChest      # wins the chest slot
  - ArmorRootChest      # loses it, stays in the bag
  - SwordIron
  - CapeIronBackpack
contents:               # container prefab -> (prefab -> count). OUR BUILD ONLY
  CapeIronBackpack:
    Wood: 100
spawn:                  # list of INTEGER points; one is chosen per player by user-id hash
  - {x: 0, y: 30, z: 0}
```

`skills`, `items` and `spawn` are upstream's. `quality`, `equip` and `contents` exist only
in our patched build - see `patches/` - and are FATAL to upstream's, which is the rollout
hazard described under "The three keys upstream does not have" below.

`CheatRaiseSkill` adds to the current level, which for a new character is the same as
setting it. `Random.Range(0, spawn.Count - 1)` has an exclusive upper bound, so the last
spawn entry is never chosen unless there is exactly one - list N+1 points for N.

### The three keys upstream does not have

Upstream 1.4.17 equips **nothing** and hardcodes quality to **1**. MEASURED: the string
`EquipItem` occurs zero times in the whole 1.4.17 assembly, and the third argument of its
`Inventory::AddItem` call is the literal `ldc.i4.1`. So on upstream a templated character
spawns unarmoured and unarmed holding a full kit, every `quality: 3` in every preset is
silently ignored, and a granted `BeltStrength` is a 2 kg no-op - `SE_Stats.m_addMaxCarryWeight`
only reaches `SEMan` through `SetupEquipment` -> `UpdateEquipmentStatusEffects`, which walks
the equipped slot fields and never the inventory. The patch fixes all three.

- `quality` is clamped at runtime to `[1, m_shared.m_maxQuality]` read live from `ObjectDB`,
  and the clamp is logged. `Inventory.AddItem` does not clamp it (MEASURED: it stores
  `m_quality` straight from the argument and reads `m_maxQuality` nowhere), and an over-max
  item is not an error - it just becomes permanently non-upgradeable, with the upgrade panel
  showing `$inventory_maxquality`. Clamping loses a level; not clamping loses the item's
  future. Watch `ShieldBanded` and `ShieldIronTower` (max 3, not 4) and `BeltStrength`,
  `CapeIronBackpack`, `CapeSilverBackpack` (max 1).
- `equip` is walked BACKWARDS. `Humanoid.EquipItem` unequips whatever holds the target slot
  before storing (MEASURED: every slot branch calls `UnequipItem` first), so for one slot the
  LAST successful equip wins; walking backwards makes the FIRST name in the list win, which
  is the order an author writes. The slot is never named in the template - it is a property
  of the item (`m_itemType`) and naming it would create a second source of truth. An entry
  that loses its slot is logged at Info; one that is not in `items`, or is not equipable, at
  Warning. Equipping at this point in the lifecycle is safe because vanilla itself does it:
  `Humanoid.GiveDefaultItem`, reached from the `GiveDefaultItems()` call one line earlier,
  calls `EquipItem(item, false)`. The one guard that can be transiently true is
  `IsSwimming() && !IsOnGround()`; refused equips are retried from a `Player.Update` postfix
  until it clears.
- `contents` fills a Vapok-AdventureBackpacks backpack. Its contents are not inventory items
  - they live in the backpack `ItemData`'s `m_customData` - so `AddItem` cannot put anything
  in one. The patch calls `AdventureBackpacks.API.ABAPI.GetBackpack(itemData).Value.Inventory`,
  which is the mod's own initialisation path (`GetBackpack` itself runs
  `GetOrCreate<BackpackComponent>("")` -> `FirstLoad()`, allocating a correctly sized
  inventory and writing a valid payload), and adds to that live `Inventory`. No payload bytes
  are ever hand-written. It is reached ENTIRELY by reflection: AdventureBackpacks has no
  version handshake (MEASURED: zero hits for `ServerSync`, `ModRequired` or
  `MinimumRequiredVersion` in its assembly; its only RPCs are PieceManager admin-list sync),
  so `contents` pins no AdventureBackpacks version, and ServerCharacters still loads where
  AdventureBackpacks is absent. Missing mod, renamed API or a non-backpack item all degrade
  to one warning and an empty container. Filling does NOT require the pack to be equipped.
  All nine backpack prefabs are `m_itemType 17` = Shoulder, so a pack and a cape contend for
  the same slot - do not list both under `equip`.

Unknown top-level keys are IGNORED and logged by our build: the deserialiser is shared
across all three of the mod's template-reading sites and uses `IgnoreUnmatchedProperties`,
and the `catch` clauses were widened from `catch (SerializationException)` - a type
YamlDotNet never throws, so upstream's handler never fired - to also catch `YamlException`.
UPSTREAM's build behaves the opposite way, and that is the hazard. MEASURED 2026-09-15 by
deserialising through each build's own real `PlayerTemplate` type in two mono processes:
upstream's has exactly `{skills, items, spawn}` and a new-schema template throws
`YamlDotNet.Core.YamlException: Property 'quality' not found on type
'ServerCharacters.PlayerTemplate'`, uncaught, out of the `Game.SpawnPlayer` postfix - which
discards the ENTIRE template, skills and items and spawn together, leaving a naked character
at the world default spawn. Ours parses the new schema and still parses the live 9,261-byte
template identically (23 skills, 27 item entries, 1 spawn point).

**So the rollout order is: DLL first, everywhere, then the template.** `CharacterTemplate.yml`
is hot-reloaded by a `FileSystemWatcher` and ServerSynced as text with no restart, so a
new-schema template dropped onto a server still running 1.4.17 ruins every character created
in that window. The DLL is version-locked in both directions -
`ServerSync.VersionCheck.IsVersionOk` is symmetric, so a 1.4.17 client is refused by a
1.4.17.1 server and vice versa - which is a loud, harmless failure, unlike the template one.

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
