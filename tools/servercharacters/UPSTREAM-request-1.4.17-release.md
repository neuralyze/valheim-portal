# ServerCharacters and upstream: what we found, and why we filed nothing

Written 2026-09-14. Read this before opening an issue against
`blaxxun-boop/ServerCharacters`, before forking it, and before pinning any version of it.

## Short version

1. Thunderstore's newest `Smoothbrain-ServerCharacters` is **1.4.16** (2025-05-02). It does
   not work on Valheim 1.0.12 and **must never be installed on it**.
2. **1.4.17 exists and fixes it.** It is published on **Hexium**, not Thunderstore:
   <https://valheim.hexium.gg/mods/Smoothbrain/ServerCharacters>, 2026-09-09.
3. The author has **left Thunderstore** and has **refused a fork-and-redistribute
   request**. Do not fork this mod, do not mirror it, do not embed it in anything we ship.
4. So there was nothing to file. The bug is fixed, the build is published, and the only
   thing we needed was to install from the right index.

## Why 1.4.16 is fatal on 1.0.12

`ServerCharacters.Initialize()` calls
`PlayerProfile.GetCharacterFolderPath(FileHelpers.FileSource)`. Valheim 1.0 moved that
method to a new `SaveSystem` type with an identical signature, so Mono throws
`MissingMethodException` when it JITs `Initialize`, and the mod never initialises.

That alone would be survivable if it failed clean. It does not. Harmony runs `Initialize`
as a postfix on `FejdStartup.Awake`, and `Awake` has by then already installed patches on
`PlayerProfile.LoadPlayerFromDisk`, `Player.Load`, `Skills.Load` and `Inventory.Load`. An
unpatched install is therefore **live save/load patches over an uninitialised profile
store**, on both server and client. This is a character-storage mod; that is the worst
possible failure mode.

Resolving every member reference in the shipped 1.4.16 assembly against 1.0.12's
`assembly_valheim.dll` finds **eight** genuinely unresolvable members, not one:

| Unresolvable in 1.4.16 | 1.0.12 has |
| --- | --- |
| `string PlayerProfile::GetCharacterFolderPath(FileHelpers/FileSource)` | moved to `SaveSystem`, same signature |
| `PlayerProfile/PlayerStats PlayerProfile::m_playerStats` | now `PlayerStats[]` |
| `void Character::Message(MessageType, string, int, Sprite)` | gained a trailing `bool log` |
| `void MessageHud::ShowMessage(MessageType, string, int, Sprite, bool)` | signature changed |
| `ItemData Inventory::AddItem(string, int, int, int, long, string, bool)` | signature changed |
| `StatusEffect SEMan::AddStatusEffect(int, bool, int, float)` | signature changed |
| `void Game::SavePlayerProfile(bool)` | signature changed |
| `Terminal/ConsoleCommand::.ctor(...12 args...)` | signature changed |

Several sit on the paths this fleet actually wants. `m_playerStats` is inside
`Utils.GetPlayerListFromFiles()`, which is the web API's `GetPlayerList`. `Inventory.Load`
gained an overload, so the mod's `AccessTools.DeclaredMethod(typeof(Inventory), "Load")` is
ambiguous and the transpiler that consumes it gets nothing.

**This is why a one-line binary patch was the wrong remedy.** The mod has exactly one
MemberRef row for `GetCharacterFolderPath`, shared by all three call sites, so retargeting
it to `SaveSystem` is trivial and does clear the exception - and leaves seven live faults
behind a mod that now looks like it works. See `tools/modpatches/README.md`.

## Upstream already fixed all eight

`blaxxun-boop/ServerCharacters@bb7d3cd6`, *"fix for deep north"*, 2026-09-09. It retargets
the three `GetCharacterFolderPath` call sites to `SaveSystem`, indexes `m_playerStats[0]`,
pins `Inventory.Load` to the `ZPackage` overload, widens `DummyPlayer.Message`, rewrites
the `LoadPlayerFromDisk` transpiler with a `CodeMatcher` instead of a fixed `Skip(2)`,
drops the `ZNet.RPC_PeerInfo` and `GameVersion.ToString` handshake patches, and sets
`ModVersion = "1.4.17"` with `MinimumRequiredVersion = "1.4.17"` and `ModRequired = true`.

It is the only commit after the 1.4.16 release commit, so 1.4.17 is 1.4.16 plus this fix
and nothing else.

## What we verified against 1.0.12

Both from a from-source build of `bb7d3cd6` (`tools/servercharacters/build.sh`) and from
the author's official Hexium artifact:

- **Reference graph clean.** 486/488 typerefs and 5159/5178 memberrefs resolve against the
  running server's assemblies. All eight rows above disappear; nothing new appears. What
  remains unresolved is `System.Numerics.Vector` intrinsics and one
  `ImmutableArray.AddRange(ReadOnlySpan<T>)` overload, identical in stock 1.4.16 and
  supplied by Unity's own BCL at runtime.
- **The two builds agree exactly.** The unresolved sets of our source build and the
  author's published binary are byte-for-byte the same set. Independent confirmation that
  the published artifact is the fix, and that our build reproduced it.
- **It runs.** On Ulfsland (Valheim 1.0.12, 92 plugins), 1.4.17 initialised with no
  `MissingMethodException`, the `WebInterfaceAPI` listener came up on `127.0.0.1:5982`,
  `GetModList` returned `Server Characters 1.4.17`, `GetPlayerList` decoded a real
  profile's statistics, `RaiseSkill` rewrote an **offline** stored character
  (`Skill Swords = 34.1232`, then `39.1232` on a second call), the mod logged its own
  profile backup, and `CharacterTemplate.yml` was read at load and re-read within 17 ms of
  being touched.

## Why we filed nothing

GitHub issue **#98, "Fork and LICENSE"**, opened 2026-09-10, asked the author for
permission to fork ServerCharacters and publish a trimmed version on Thunderstore. His
reply, 2026-09-11:

> No, sorry. Especially not on Thunderstore. I am done with that website. Plus, if you
> expect this to improve your performance in any shape or form, you would be wrong.
>
> Just download ServerCharacters from Hexium and use it as is. We recommend to use the
> Gale mod manager for that, since it can download mods from both backends (Thunderstore +
> Hexium) seamlessly.

That answers both questions we would have asked.

- *"Please publish 1.4.17 to Thunderstore."* Already declined, three days before we
  thought of it. Filing it would waste his time.
- *"May we ship our own build?"* Also declined, and the repository has **no LICENSE file**,
  so it is all rights reserved. GitHub's licence API returns nothing for it.

## Consequences for this fleet

> **SUPERSEDED on 2026-09-14, and again on 2026-09-15.** This section described the Hexium
> route and said our from-source build "is not shipped and must not be". Both halves are now
> false: the operator decided on 2026-09-14 to ship our own compile instead of the author's
> Hexium archive, and on 2026-09-15 to PATCH it - upstream's template code equips nothing
> and hardcodes item quality to 1, which broke the whole kit system. The current position,
> its four boundaries and its retirement condition are in
> `tools/servercharacters/README.md`, section "What we ship, and why that is temporary", and
> in `deploy/upstream-sources.json`. What is written below is kept because the LICENCE
> findings above are unchanged and this is the record of what we believed before the
> operator decided otherwise; do not act on it.

- ~~Install from **Hexium**, pinned to 1.4.17.~~ `tools/valheim_mods.py` has a one-entry
  `HEXIUM_PACKAGES` allowlist, and the mechanism still exists, but ServerCharacters no
  longer uses it. **We host nothing publicly**, which is still true and still load-bearing;
  "and modify nothing" is not, as of 2026-09-15.
- **Never pin 1.4.16 on a 1.0.x server.** Unchanged. If Hexium is unreachable,
  `tools/valheim_mods.py` deliberately drops the identifier from the registry rather than
  falling back to Thunderstore, because not installing is better than installing 1.4.16.
- **Do not fork, mirror or embed this mod** without the author's written permission - as a
  PUBLIC act. Unchanged, and now the only thing separating our position from the one the
  author declined: we publish no copy, commit no binary, and host nothing. The refusal is
  recorded in `deploy/upstream-sources.json` so nobody has to rediscover it.
- ~~Our from-source build under `tools/servercharacters/` is a diagnostic and cross-check
  tool. It is not shipped and must not be.~~ It ships, to the Ulfsland server and the four
  `ulfsland-*` client editions only, and it is patched. See the README.

## If you still want to contact upstream

The only thing worth saying is a thank-you: "1.4.17 fixed Valheim 1.0 for us, verified on a
92-plugin 1.0.12 server, here is what we checked." A request to publish to Thunderstore
will be declined again.
