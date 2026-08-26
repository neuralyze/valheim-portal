# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A third world source when creating a server: upload a `.zip` of an existing world's save.
  The admin form now presents one exclusive switch - generate a new world on a random seed,
  generate from a typed seed, copy a world already on this host, or upload one - and the fields
  belonging to the sources you did not pick are `disabled`, so they are not submitted at all. A
  value supplied for an unselected source is refused rather than dropped: silently ignoring a
  typed seed hands the operator a different world than the form showed them and nothing in the
  result records it.
- The uploaded archive is validated before a single byte is decompressed, and each refusal names
  what is wrong: a member with an absolute path, a `..` traversal, a symbolic link, a duplicate
  base name, a member over 512 MiB or one declaring more than a 200:1 expansion, a `.db` with no
  `.fwl` or the reverse, several distinct worlds in one archive (which names them), and an
  archive holding only Valheim's own `.old` and `_backup_auto-` copies. Those game-written copies
  are ignored rather than refused when a live pair is present - a player who zips their
  `worlds_local` folder always ships them - and are listed on the review page, so an automatic
  backup can never be mistaken for the live save. The whole body is capped at 512 MiB, the
  ceiling `compose.yaml` already sizes the portal's `/tmp` tmpfs for.
- Renaming the two files is not sufficient and the upload does not pretend otherwise. A `.fwl`
  carries the world's own name in its body: Hrafnheim's 50 bytes are a 46-byte package holding
  world version 37, the length-prefixed string `Hrafnheim`, the seed name `qmrbecQI2K`, the seed,
  the UID and the generator version. Provisioning therefore rewrites the name field and carries
  the seed, seed value, UID, world version, generator version and trailer across untouched -
  those are what make the placed world the same world rather than a fresh one on the same map -
  reusing the `place_save_pair` path the existing "copy a world on this host" source already
  used. Proven by round trip: rewriting the placed file's name back reproduces the original
  bytes exactly.
- The save never travels over the agent socket, which caps a JSON payload at 32 MiB while a
  Valheim database routinely exceeds it. The portal streams the archive straight off the
  multipart spill file - `archive/zip` needs only an `io.ReaderAt`, so it is never held in memory
  or copied a second time - writes the validated pair into a shared spool, and sends the agent a
  32-character hex staging id. `hostops/provision_valheim_server.sh` is the one place that turns
  that id into a path, against a root from its own environment. Provisioning deletes the staged
  pair once it has copied it in; the portal sweeps anything abandoned after two hours.
- An upload never replaces a world. `tools/valheim_provision.py` refuses when the world directory
  already exists and the review step refuses when the portal already knows the name, so the files
  are only ever placed into a directory that did not exist, before the container is created and
  therefore before anything can be running over them. The confirmation phrase for an upload names
  the world the archive carries as well as the server being created - `CREATE <world> FROM
  <uploaded world>` - so a page prepared for a different archive cannot be confirmed from muscle
  memory.

- An admin-mode maintenance window, per world, that loads `JereKuusela-Structure_Tweaks` and
  `Azumatt-PerfectPlacement` server-side on one named world and stays on until an operator turns
  it off. Those two disconnect every connected player when they load server-side, which is why
  they were pulled from all four servers on 2026-08-20; that closed the incident and also removed
  the admin capability they provide. Per world rather than fleet-wide because the profile cannot
  express it: measured 2026-08-25, all four worlds link to the same `admin` profile and both
  server-side sources `cmd_deploy` reads are per profile, so `scope: shared` in the manifest would
  arm the whole fleet on the next deploy of each. The per-world lever is a new
  `<world>/mods/admin-mode/` plugin overlay, layered by `cmd_deploy` after the profile cache and
  `manual-mods` exactly as `manual-mods` is layered after the cache, and built by
  `hostops/portal_admin_mode.sh` from the archives that world's profile already pins - so arming
  reads no network. Entering is `backup_valheim_world.sh`, `stop_valheim_server.sh`,
  `portal_admin_mode.sh on`, `portal_mod_admin.sh deploy`, `start_valheim_server.sh`,
  `wait_valheim_server_ready.sh`, composed in `internal/agent/agent.go` from the scripts that
  already do each step. Leaving is the same without the backup: it is the path that gets players
  back in, so it carries the fewest steps that can refuse it.
- Three refusals on that window, each with a test. Entering is refused while any player is
  connected, read from the world's own `data/htdocs/status.json` - the same file liveness comes
  from - because arming would kick exactly the people it was opened to work around. Turning it off
  on a world that is not in one is a no-op rather than an error, so the recovery path can never
  itself be refused. And the window is stored in the portal database, migration 23
  (`world_admin_mode`: world, since, actor), not in memory: a world left armed kicks every player
  who joins it, and a portal restart during a window must not turn that back into a
  normal-looking fleet.
- The window, surfaced everywhere a world's status is. `withLiveStatus` in
  `internal/app/world_liveness.go` is the one funnel every world listing already passes through -
  the admin home, the player home, one world's page and the launcher's `/api/status` - so the mark
  is stamped there rather than pasted into four templates, where it would be missing from the
  fifth. The admin card carries it in the collapsed `<summary>`, which is the only part an
  operator sees while the card is closed, and the copy states the consequence rather than the
  label: "every player who joins is disconnected", with when the window opened, who opened it,
  and that there is no timer.

- A GPL-3.0 source offer for `ValheimVRMod.dll`, on the pages that hand it out. ValheimVR is not
  our program and not under the AGPL, so `PORTAL_SOURCE_URL` never covered it: the portal was
  conveying a GPL binary it compiled itself while offering the source of a Go web application.
  The new `PORTAL_VHVR_SOURCE_URL` defaults to `https://github.com/neuralyze/vhvr-mod`, where
  branch `neuralyze/local` is now public at `23f0ce4526cd` - seven commits on upstream `50d333d`
  that until today existed only on this host. The offer renders on the world page and on the
  release page of a release carrying a `flat_companion` or a `vr_runtime`, and nowhere else:
  those two kinds are exactly the archives that must contain the DLL, enforced on the way in by
  `ValidateFlatCompanionArtifact` and `requiredVRRuntimeFiles`, and a licence notice on a
  download that does not contain the program is a false statement rather than caution.
- Per-binary source anchors, because section 6 owes the source for the copy a person received and
  a portal release number means nothing in a ValheimVR checkout. Each published DLL is tagged in
  the fork as `shipped/valheimvrmod-<first 12 hex of its SHA-256>`, which a recipient can compute
  from the file in their hands. First one pushed: `shipped/valheimvrmod-f879224e030c` on
  `23f0ce4526cd`, the DLL inside `vr-runtime-2.2.5-nospawnprobe.zip` currently served as
  `hrafnheim-vr` 2.5.111 and 2.5.36 on the other three worlds. Hashes cannot prove that tie -
  `mcs` is not reproducible, and two compiles of one tree differ in ~77,800 of 600,064 bytes - so
  it rests on a `Release` rebuild of that commit landing on the exact 600,064-byte size that no
  other configuration produces, plus the `HarmonyPrepare` typeref and `LogDiagnostic` string that
  only commits `5ea5183` and `1d6ea02` introduce.
  One published binary is still unanchored and the page copy is written so it does not pretend
  otherwise: the Flat companion's DLL (`3fe2ce0874f2`, 567,296 bytes) matches no commit on the
  branch. It lacks both markers above, its size matches none of the four configurations at
  `23f0ce4526cd`, and its embedded PDB path names a checkout directory that no longer exists. It
  is byte-identical to a `build/latest` copy dated 2026-07-26, three weeks before the branch was
  authored, when the changes were still uncommitted; the reflog begins 2026-08-18, so that tree
  is not recoverable. It closes by rebuilding the companion from a commit and republishing, which
  is a fleet action, not a code change. Recorded in `deploy/upstream-sources.json`.
- The mods a player actually gets, listed at the bottom of each world page with a description and
  the operator's own note where one exists. The set is derived, not curated by hand: the union of
  the `vr` and `flat` profiles, so anything only the `admin` profile carries is gone by set
  difference and a new admin tool needs no code change. Measured on Hrafnheim, 108 installed
  packages become 93 entries - 5 removed as Thunderstore-categorised libraries and 10 named in
  `PLAYER_IRRELEVANT`, each with its reason. The library rule is a conjunction, not a tag test:
  Backpacks and CreatureLevelAndLootControl are categorised `Libraries` because their authors also
  publish an API, and dropping on the tag alone deleted two of the most visible mods on the server.
  Four admin tools are named explicitly because they ship to both player editions and the set
  difference cannot reach them.
- A player note per mod, written on the admin mods page and shown on the world page when it exists.
  Nothing generates this text and no entry acquires one it was not given: an instruction for using
  a mod that no person wrote is a fabrication, and the guide audit found twenty of those.
- The list is cached per world and stays static until the mods change. A portal mutation drops the
  cache, but that alone is not enough: mods are also changed by `tools/valheim_mods.py` on the host,
  where the portal sees nothing, and Hrafnheim's deployed plugin tree was already out of step with
  its profile manifests by four packages. So a read also checks a cheap fingerprint - the sorted
  `identifier@version` set of the player editions, no network - and rebuilds when it moved.

- Published profiles now carry a world's managed settings. `cmd/profile-definition-builder`
  layers the portal's stored authority over the profile's hand-maintained `client-config` files
  per KEY rather than replacing them: a key with a record is rewritten on its own line, and a
  key with no record is left byte-identical, because those files are how the fleet is actually
  configured and the VR profile's `neuralyze.vrfixes.cfg` and
  `org.bepinex.plugins.valheimvrmod.cfg` would be the first casualties of a whole-file rewrite.
  Measured on a real Hrafnheim VR build with four managed keys: exactly one changed line per
  file, every comment line identical including its terminator, and `ZenDragon.ZenBreeding.cfg`
  keeping its 9 CRLF lines beside its 31 LF ones. The comment blocks are the only
  machine-readable schema BepInEx publishes and the settings extractor reads them, so a writer
  that reflowed the file would destroy its own next input.
- `config/settings-baseline.json` in every profile built with an authority source, recording per
  managed key the policy and the exact value written. Without it `client_default` cannot be
  implemented: only a recorded baseline distinguishes "the player edited this" from "the admin
  changed the default", and comparing against the current server value wipes a player's
  customisation the first time an admin edits a default. It sits under `config/` rather than at
  the archive root because the installer's allowlist already admits anything under `config/`,
  while a new top-level member takes the `default:` branch and fails the sync - and a definition
  an installed client cannot parse stops players launching Valheim, exactly as the `audience`
  manifest field did on 2026-08-17. Measured against the pre-change client with the same
  archive: `config/settings-baseline.json` accepted, `settings-baseline.json` rejected with
  "profile definition contains an unsupported file". The placement therefore needs no new
  client, no publish ordering and no feature flag.
- A managed setting whose config file the profile does not ship is refused rather than written,
  and recorded in the baseline's `unshipped` list with its policy, its value and the reason. The
  refusal is about the destination: the schema an admin edits comes from the world's
  `config_merged/bepinex`, which is what the server reads, and only 22 of Hrafnheim's 113 config
  files belong to a plugin the client installs. Creating a client `.cfg` for one of the other 91
  would put the value where the plugin is never loaded and the file never read while never
  putting it where it would take effect - by default, for most of the corpus - which is a value
  that looks applied and is not. Shipping-set membership is tested directly against the profile
  being built rather than resolved through mod attribution, because a config's basename is the
  plugin's BepInEx GUID, the GUID is not derivable from the Thunderstore identifier, and that
  chain resolves only 95 of 113 files. Applying the rest needs the server-side write and a world
  restart, which does not exist yet.

- Shields block and parry again in VR, behind [0 - Combat] RestoreShieldBlock, default on. The
  cause was upstream and not ours: commit 666124e6 "Migrate to cache weapon collision using item
  hash instead of object name" (2026-05-08) dropped the injected item-name string, and the only
  thing reading it in the shield case was the TAIL of
  `meshFilter.gameObject.AddComponent<ShieldBlock>().itemName = ___m_leftItem;`, so the whole
  statement went and took the component's only attach site with it. `itemName` was write-only, so
  nothing warned. Measured from both directions in the binaries: v0.9.21 (2026-03-01, before the
  refactor) still has that AddComponent at IL_0210 of PatchSetLeftHandEquipped followed by
  `stfld itemName`, and the ValheimVRMod.dll this profile loads has ZERO ShieldBlock attach sites
  among 161 AddComponent calls, against three for WeaponBlock and one for FistBlock. Master is
  still broken - compare 50d333d..master is "identical", ahead_by 0 - so there was nothing newer
  to take. Restored from our side with a postfix on VisEquipment.SetLeftHandEquipped that attaches
  ShieldBlock to the same MeshFilter GameObject the v0.9.21 IL used, with the same lifetime; every
  consumer is already in the shipped mod and needed no change. Confirmed in the headset on
  2026-08-26: shields block, and the attacker is staggered, which is vanilla's perfect block. The
  per-hit probe that found the cause is gone with the same confirmation, so [9 - Profiling]
  LogShieldBlocks no longer exists - clients that already carry the key can ignore it.

- An anchor toggle on the left grip while seated at a helm, so dropping anchor no longer means
  opening the hover ring. It pulses the same LeftShift+F chord the ring's Anchor entry uses and
  drives the anchor mod's own reader in the same frame, because BoatAdditions reads that hotkey
  only inside GetHoverText, which the game calls only for what the player is hovering.
- Diagnostics for losing helm control, behind [9 - Diagnostics] WatchHelm. The reported 10-20
  second timeout has no timer behind it - there is no clock of any kind on the steering path in
  the game, VHVR or our plugin - so it logs which of four state transitions actually fires.

- Seven more location categories with their own glyphs: shrine, tower, ruins, monument, port, mine
  and arena, plus tombs moved to dungeon. A church 100 m from a player's raft used to draw the same
  blue diamond as a runestone, and it was one of 322 sacred structures split between two buckets.
  Ordering carries the meaning: shrine and tower beat ruins because a ruin is a condition rather
  than a building, tower beats fortress so 1127 guard towers stop reading as strongholds, and
  StartTemple stays the world spawn despite containing the word temple.
- Player map pins show on the admin map, drawn per pin type and attributed per player, with the
  crossed-off strike-through preserved. The operator's own markers were reaching the portal and
  had no way to be displayed there.

- VR players can reach the hover menu again, and the guide finally tells them how. The menu
  opened on the right grip while six places in the guide said the off hand, its `Modifier` setting
  was read by nothing, and its highlight could not move at all on foot because the thumbstick was
  only sampled while seated on a mount. Fixed in `tools/vrfixes`, and the player guide grew twenty
  corrections: the wrist menus never named a button, four values in the VR section quoted mod
  defaults the live config overrides, and holstering, spear throwing, crossbow reload, chat, the
  wrist minimap and every comfort setting were undocumented.

- The ValheimVR mod is built on this host now, with Mono's compiler, because the Windows build
  host is gone. `scripts/build-valheimvr.sh` compiles it in about two seconds;
  `scripts/build-valheimvr-artifact.sh --client-type flat|vr` produces the Flat companion or the
  VR release zip that feeds `scripts/build-vr-runtime-artifact.sh`, replacing both
  `tools/build-valheimvr-flat.ps1` (MSBuild) and the Windows `make-release.cmd` staging. Artifacts
  are byte-reproducible, and the portal's own validators accept both. The Mono build carries the
  identical 350-name type surface as the previous Roslyn one, differing only in compiler-generated
  closure and iterator types.
  Templates whose archives store no directory entries - the portal's own VR runtime artifact is
  one - need their staged modes normalised, or `unzip` gives the invented directories the 0600
  of the files inside and `BepInEx/plugins` cannot even be listed.

- The ValheimVR working copy is now six named commits on a branch instead of seventeen
  uncommitted files, rebased onto upstream `50d333d`, which brings in the refined gestured
  draw-from-back logic the VR bow work needs. Carrying our patches across an upstream update is
  a rebase now, so the Flat dodge guard arrives with the update rather than being overwritten by
  it; `docs/valheimvr-packaging.md` no longer tells you to reapply it by hand.

- A registry of the projects this deployment builds source from, with a check that fails
  while an upstream commit has not been read. `deploy/upstream-sources.json` records what each
  source is pinned to and what was last reviewed; `tools/upstream_sources.py status` reports the
  gap and `review` records the conclusion. The offline half is the `upstream` gate and asserts a
  checkout has not drifted off its pin, since published artifacts are built from those trees. It
  found two things on its first run: the VR mod was a commit behind, and the container project
  had changed owner from `lloesche` to `community-valheim-tools` months earlier, eighteen commits
  back, one of which had independently made a libdoorstop fix our own local commit already carried.

- Mod profiles are shared. A profile lives once at `<fleet root>/profiles/<name>` and a
  server links to one through `<world>/mods/.active-mod-profile`; editing the profile
  changes what every linked server runs at that server's next restart. Previously each
  world held its own 2.1 GB copy with nothing connecting them, and four worlds had drifted
  to four different mod sets. `tools/profile_store.py` owns the model, `tools/migrate_profiles.py`
  performed the one-time migration, and `deploy/profiles/` ships the three primaries as
  example seed manifests without their package caches.
- Four published editions per world, built from three primaries: `<world>-vr` from `vr`,
  `<world>-vr-flat` and `<world>-non-vr` from `flat`, and `<world>-vr-flat-admin` from
  `admin`. Each release target declares `valheim_vr` and `audience` explicitly, with no
  defaults, because both were previously inferred from the profile's name.
- `releases.audience` (`player` or `admin`, migration 20). The admin edition carries the
  console and world-editing tools and is offered only to admin logins; it renders as its
  own card kind rather than a second card identical to the ordinary desktop one.
- A profile can own its server-side settings in `<profile>/server-config/`, which
  `deploy --apply` places on every linked server. A single server overrides individual
  settings in `<world>/mods/overrides/{server,client}/`, merged per key by
  `tools/config_merge.py` so a later profile change still reaches that server.
- `tools/settings_history.py`, a git store of every settings file in the fleet, so removing
  a mod can no longer lose its configuration. It versions profile manifests, client and
  server settings, each server's overrides, the profile link and the admin, permitted and
  banned lists. It never versions `valheim.env`, which holds the server password.
- `hostops/tests/agent_argv_contract.sh`, which compares the host scripts against the
  callers that build their argv. Both breaks it now guards shipped silently because nothing
  tested that seam.

- A source-code link on the player-facing pages, carrying the official GitHub
  mark. This is the AGPL-3.0 section 13 offer, which the interface previously
  did not make anywhere: a network service running modified code owes its users
  the corresponding source. `PORTAL_SOURCE_URL` sets the target and defaults to
  the upstream repository, which is a truthful offer only for an unmodified
  build; a value a browser cannot follow is refused at startup. The mark is
  Octicons' `mark-github-16` (MIT, (c) GitHub), inlined so `fill:currentColor`
  colour-matches it to the navigation, and recorded in `NOTICE`, which no longer
  claims the repository vendors no third-party source, because it now does.
  Administration pages are unchanged: the offer is owed to remote users.
- `PORTAL_ADMIN_STEAM_IDS`, an optional comma-separated list of SteamID64s
  allowed to administer the portal. Empty or unset means there are no Steam
  operators, which preserves the previous behaviour exactly.

### Fixed

- Server creation works again. `prepare_profile` has called `profile_store` since 09e88b3 on
  2026-08-17 without the module ever being imported, in either branch of the dual-mode import
  block, so every creation in every world mode died with `NameError: name 'profile_store' is not
  defined`. Found on 2026-08-25 while exercising provisioning end to end, which means the fleet
  had no working way to create a server for eight days. The pytest gate was green throughout
  because nothing reached the line, and an import-time check would not have caught it either: an
  undefined global in Python resolves when the statement runs, not when the file loads. Fixed in
  its own commit with a regression test that calls `prepare_profile` and asserts on what it
  leaves on disk.

- A failed step in a composed host operation now says which step failed and whether the world is
  still down. Every failure in `execute`'s loop reported the bare string `operation failed`, which
  an operator cannot act on: a deploy that fails after the stop leaves the world down carrying a
  plugin set nobody chose, and the reply said only that something, somewhere, went wrong. It now
  names the step, says the world is STOPPED when the sequence had stopped it and not restarted it,
  and names the recovery - `manage_mods.sh <WORLD> deploy --apply` then `start_valheim_server.sh`
  and `wait_valheim_server_ready.sh` - recommending the deploy only for a sequence that had one.
  This affects `stop`, `restart`, `restore`, `set_port`, `mod_deploy` and `delete_server` as well
  as the new window, because it is the same hazard in all of them.

- The agent's JSON payload cap no longer refuses a world's settings schema. It was 4 MiB and
  the schema is 4.5 MiB for the smallest world - 119 files, 19,937 keys - so the settings page
  showed its unavailable state on every world. Raised to 32 MiB with the portal's reader to
  match, and an oversize payload now reports its size instead of sharing one message with
  malformed JSON, which had sent the first diagnosis hunting a parser fault that did not exist.
  The schema fetch also logs the agent's status and error rather than only its transport error,
  which is why the original failure read as `error=<nil>`.

- Zooming the large map no longer rolls the character. valheim_Dodge sits on right-stick-down,
  which is also the map's zoom-out, so reading the map dodged. Gated on the game's own
  Minimap.m_mode == MapMode.Large rather than Minimap.IsOpen(), which keeps answering true for
  two frames after the map closes and does not say which map.
- A single runtime exception no longer kills VR input for the session. Both the direct-action
  invoker and the input bridge latched a failure flag permanently, so one transient throw
  silenced jump, use, dodge, inventory, map and stand-up until restart, with one Warning line
  to show for it - indistinguishable from the operator's report that controls stop responding
  while sailing. Runtime failures now back off five seconds and retry, and only give up after
  five consecutive failures, saying so at Message level. Setup failures still latch, because an
  absent SteamVR or a missing method will not appear later.

- Towers and fortresses show on the map by default, and map layer choices survive a reload.
  Splitting one 1202-strong "fortress" bucket into 1455 towers and 75 real strongholds left both
  classes hidden behind a default written when they were one thing, so an operator reported that
  fortresses had disappeared. Nothing remembered a ticked box either, so every reload silently
  reverted to those defaults. Choices are now stored per world and per audience, and a stored
  key for a category that no longer exists is ignored rather than resurrecting it.

- Map locations read as place names instead of prefab ids: `MWL_AshlandsFort1` shows as Ashlands
  Fort with the id beside it. Splitting happens only at boundaries the author encoded - underscore,
  case change, digit - and never inside a lowercase run, which is what keeps Greydwarf, Dvergr,
  Volture and Mistlands whole without a table of exceptions. Variant numbers are dropped from the
  display because they index a mod's asset set rather than counting anything: StoneTowerRuins has
  03, 04, 05, 07, 08, 09, 10 and no 01. Derived over all 326 distinct names in the live snapshot.

- Exploration maps upload when a player exits, instead of at the start of their next launch. The
  inline upload was reached only from a `Game.Logout` prefix, so quitting to desktop wrote the map
  and sent nothing, silently. On 2026-08-20 four players finished a session and the portal received
  nothing at all - the two files it did hold predated the session and had been swept up by an
  earlier launch. `Game.Shutdown` is the funnel (proven: its only two callers are `ContinueLogout`
  and `OnApplicationQuit`), with `OnApplicationQuit` as a backstop and a once-per-exit guard.
- The reporter's loaded, wrote and sent lines are `LogMessage` now. They were `Info`, which the
  client's `BepInEx.cfg` excludes, so a pipeline that never uploaded produced 63 clean warnings and
  no trace of itself. `bundle.sh` also carried a trap that would have shipped the previous reporter
  DLL inside a new artifact.

- Three wrist entries pulsed keys nothing listens for, and one was missing. The server enforces
  `Cycle Spell = G`, `Unsummon = Shift+F` and `ForsakenPowerHotkey = F8`; our entries still sent the
  keypad bindings somebody had rebound locally before ServerSync reverted them, which is why they
  pressed cleanly and did nothing. `Reset Power = F9` added.
- The `F4` collision was ours: we shipped `WardHotKey = F4` on 2026-08-17, and IdentityCrisis reads
  F4 too, so each fired the other. WardIsLove moves to `Keypad4` - the only side of it we control,
  since we ship no copy of IdentityCrisis's config. `ForsakenPowerHotkey` in the shipped file was
  also corrected to the enforced F8, so an offline session cannot hand the collision back.
- One transient Thunderstore fetch no longer aborts a whole republish. `((failures++))` returns the
  pre-increment value, which is 0 on the first failure, and `set -e` killed the run there - three
  worlds went unattempted with no summary. The build-failed branch also reported `go run`'s own
  "exit status 1" trailer and let the EXIT trap delete the real error; it now keeps the log.

- Hover menu navigation follows the stick. VHVR's `GetJoyRightStickY` returns `-axis.y`
  (`VRControls.cs:661`), so pushing down moved the highlight up; the read is negated once and the
  arithmetic keeps up positive, with index 0 wrapping to Cancel.
- Sitting re-measures eye height. VHVR branches its height model on `Player.IsSitting()`
  (`VRPlayer.cs:1249`) and adds the result to a cached offset every frame, so a player already
  seated when the sit began kept a standing measurement and the character sat too high. The
  posture transition now triggers the same recentre the headset's own button does.
- The wrist `Reset Height` entry is gone. The SteamVR recenter hook covers it, confirmed in a live
  session, and a manual fallback nobody needs is clutter.

- The hover menu can be cancelled - Cancel is the last entry in every group, one push up from the
  opening highlight - and navigating it no longer dodges. That bug was ours: the plugin read
  `valheim_Dodge`, which is bound to the same right-stick-down the menu navigates with, and an
  earlier suppression prefixed `Player.UpdateDodge`, a method VHVR replaces outright, so it had
  never done anything. Proved with a Harmony probe rather than assumed. The suppression now sits on
  `Player.Dodge`, whose whole body is a queue write.
- Height resets on the headset's own recenter. The system button moves the tracking origin, which
  fixes facing, but VHVR caches its own eye-height offset until something nulls it, so height stayed
  wrong. The plugin now subscribes to the SteamVR zero-pose and chaperone events, calls
  `VRPlayer.RequestRecentering()`, and logs which event arrived - nobody knows yet which one a Quest
  long-press produces. A `Reset Height` wrist entry remains for the case where the runtime swallows
  the event entirely.

- Published profile definitions no longer carry portal-only fields. An `audience` field
  written into `profile-manifest.json` failed every install with
  `unknown field "audience"`: the client decodes that file with `DisallowUnknownFields`,
  and because sync runs before launch it locked players out of the game rather than pinning
  them to an old profile. The definition's keys are fixed, `schema` cannot be bumped, and
  the client rejects unknown files in the archive as well.
- Staged artifact filenames no longer accrete their kind prefix. `flat_companion-` had been
  prepended nine times, and the resulting 205-character name crossed the 180-character cap
  the builder and the client both enforce, which had already made every Flat publish fail.
- `hostops/portal_publish_profile.sh` resolves its target by published profile. Matching the
  source primary stopped identifying one target once a world published two Flat editions
  from `flat`, so every agent-driven Flat publish exited 2.
- `hostops/provision_valheim_server.sh` takes the 14 positionals the agent now sends; the
  `TEMPLATE_WORLD`/`TEMPLATE_PROFILE` pair became a single `COPY_FROM`.
- Published editions no longer ship the timestamped config backups the tooling leaves
  behind; every edition had been carrying fourteen to sixteen of them to players.
- A Flat edition whose ValheimVR comes only from the companion is classified as
  VR-compatible. Reading the package list alone was accidentally right until the VR fixes
  moved to the headset edition, after which two editions were offered as plain desktop.

### Changed

- Administration is now authorised by the signed-in Steam identity against
  `PORTAL_ADMIN_STEAM_IDS`, **or** by the previous proxy factors — trusted
  source range, non-empty identity header and matching admin token — which are
  retained as break-glass. The audit actor is the identity header for the proxy
  path and `steam:<steamid64>` for the allowlist path, so every privileged
  action stays attributed.
- The **Administration** link now appears on the player pages as soon as an
  allowlisted operator signs in. What disappears is the old entry point: the
  link used to require a prior manual visit to `/admin` to set a 12-hour admin
  cookie, and it lapsed silently when that cookie expired. The proxy blanks the
  admin headers on player routes, so the link could never advertise the page
  that was the only way to obtain it.
- Deployments setting the allowlist must remove `auth_basic` from the nginx
  `location ^~ /admin`, which would otherwise challenge an allowlisted operator
  before the portal saw the request. `$remote_user` is then empty, so the proxy
  path grants nothing and the allowlist governs; restoring the two lines
  restores break-glass. Keep the admin-token snippet include either way.
- The signed-in player headline is now "Fight trolls, not mods." It replaces
  "Valheim, ready when your world is.", which described the server's state rather
  than anything the portal does, and it carries no first-person claim: a headline
  reading "we handle the mods" would speak for whichever operator deployed the
  build, not for this project. The README masthead carries the same line, and the
  landing-page screenshot was regenerated to match.

## [0.1.0] - 2026-08-02

### Added

- AGPL-3.0 `LICENSE` and a `NOTICE` recording third-party components.
- README screenshots of the player world list, the profile chooser, the
  administration page and the world map, in `docs/images/`. They were captured
  from a disposable instance seeded with synthetic worlds, players and releases,
  so no real world name, player or address enters the repository.
- First-class admin token. Administrative access now requires a
  `X-Portal-Admin-Token` request header matching the contents of the file named
  by the new required `PORTAL_ADMIN_TOKEN_FILE`, compared in constant time,
  **in addition to** the trusted-proxy CIDR check and the non-empty
  `PORTAL_AUTH_HEADER`. The proxy injects the header; the browser never sends
  it. `scripts/install-portal.sh` generates the token (32 bytes of hex, same
  mode and owner as the CSRF secret).
- Public repository scaffolding: GitHub Actions CI, issue and pull request
  templates, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and this
  changelog.
- New documentation: `docs/repository-layout.md`, `docs/prerequisites.md`, and
  `docs/command-reference.md`.
- `hostops/`: the 20 world operation scripts the agent executes, plus the
  operator scripts they call, `lib/common.sh`, and five bash regression tests.
  They previously lived in a separate `ValheimConfig` Mercurial checkout, so no
  portal commit pinned the operations it invoked.
- `tools/portal_paths.py`: the world-root and `valheim-server-docker` resolvers
  for the Python half of the host tooling, matching `hostops/lib/common.sh`
  exit code for exit code.
- `VALHEIM_SERVER_DOCKER_DIR`, a new required setting naming a checkout of the
  modified valheim-server-docker fork. `install-portal.sh` validates it, writes
  it into the agent's environment file, and grants the unit read access to it.
- The Valheim and VHVR knowledge base, the mod-onboarding process, and the VR
  scanning tooling, all previously stranded in an unpublished `ValheimConfig`
  Mercurial checkout: `docs/valheim-vr-knowledge.md` (Valheim and VHVR internals
  verified against decompiled IL, plus the instrument-discipline rules behind
  them), `docs/mod-onboarding.md` (the gated process for admitting a mod),
  `docs/mod-decisions.md` (the per-package decision log),
  `docs/vr-impact-scan.md` (how to run and read the tooling), and
  `tools/vr_impact_scan.py`, `tools/vr_perf_ingest.py`, `tools/vr_scan_common.py`.
  None of it is deployment-specific and all of it was scrubbed of private world
  names, host paths and account identifiers on the way in.
- CI now runs the bash regression tests, the Python tool tests, a compile and
  import check over the VR scanners, and `shellcheck -S style` over `hostops/`.

### Changed

- `docs/installation.md` is now seven numbered steps, each opening with the exact
  commands to run, with the reasoning moved after the step it belongs to. It gained
  a step the old flow never had: configuring the reverse proxy. Administration is
  unreachable until the proxy sends `X-Portal-Admin-Token`, and the previous
  quick start went straight from `install` to `verify` without saying so. Also
  documents creating `default.env` in the `valheim-server-docker` checkout, which
  upstream does not ship and the installer requires. The README's first-run section
  is now the same sequence as one copy-pasteable block.
- **Breaking for existing checkouts.** `release-targets.json` is no longer tracked; it
  named the operator's real worlds. Copy `deploy/release-targets.json.example` to
  `release-targets.json` and edit it. `scripts/build-flat-release-plan.sh` reads its
  `flat` array (overridable as the script's fifth argument), and
  `tools/valheim_mods.py` reads both arrays for the client-release
  cutover guard — with the file absent that guard finds no targets and silently
  passes.
- **Breaking for existing deployments.** The repository is now self-contained.
  The Python tools moved from `ValheimConfig/tools` to `tools/`, and every
  outward path the scripts resolved relative to themselves is now configuration
  or repository-relative:
  - `../tools/*.py` resolves from `hostops/lib/common.sh`'s own location.
  - `../valheim/` is `VALHEIM_ROOT` (also `AGENT_WORLD_ROOT`,
    `VALHEIM_WORLD_ROOT`), which several scripts already used.
  - `../valheim-server-docker` is `VALHEIM_SERVER_DOCKER_DIR`.

  Neither path variable has a default; a script that needs one exits 78 naming
  it. `AGENT_SCRIPT_DIR` now defaults to the installed `hostops/` directory and
  an override must supply a `lib/common.sh` and a sibling `tools/`.
  **Migration:** point `AGENT_SCRIPT_DIR` in `/etc/valheim-portal/agent.env` at
  `<checkout>/hostops`, add `VALHEIM_SERVER_DOCKER_DIR`, and restart the agent —
  or re-run the installer, which also regenerates the unit's `ReadOnlyPaths`.
  `install-portal.sh verify` detects a stale value and prints both lines.
- `add_note_valheim_world.sh` writes to `$VALHEIM_ROOT/world_notes/` instead of
  a `notes/` directory inside the repository. Operator data does not belong in
  a published tree.
- Artifact upload bodies are capped at 512 MiB and rejected with HTTP 413 above it,
  replacing an unenforced 2 GiB. `compose.yaml` gained `mem_limit: 1g`,
  `pids_limit: 256`, and a size-capped `/tmp` tmpfs, since multipart spill past 16 MiB
  lands there and a tmpfs counts against container memory.
- `PORTAL_PUBLIC_BASE_URL` is now required with no default. Compose refuses to
  start without it rather than falling back to one specific host.
- World status is measured rather than operator-set. `internal/app/world_liveness.go`
  reads `<world>/data/htdocs/status.json`, which the game container's
  `valheim-status --update` rewrites every 10 seconds from an A2S query, and
  derives `online` or `offline` from it. `maintenance` remains the only
  operator-settable status and is returned untouched. Requires `STATUS_HTTP=true`
  on the game container.
- `scripts/install-portal.sh` preflight now refuses a `PORTAL_TRUSTED_PROXY_CIDR`
  equal to the container bridge gateway `/32`, which would trust the whole
  bridge network rather than the proxy.

### Fixed

- Corrected documentation throughout: the agent operation table is 20 scripts,
  not 18 or 19; the portal container does mount the whole world tree read-only,
  including each world's `valheim.env` and the live saves; a world publishes two
  host UDP ports, not three.

### Removed

- the deployment-specific nginx vhost. `deploy/nginx-portal.conf.example`
  is now the only nginx sample; `deploy/Caddyfile` remains, using `example.com`.

### Security

- Admin access can no longer be obtained by reaching the portal from inside the
  trusted proxy range and setting one header. The `PORTAL_ADMIN_TOKEN_FILE`
  secret is a second, unforgeable factor: the portal refuses to start if the
  variable is unset, the file is unreadable, or its trimmed contents are shorter
  than 32 bytes.
- The reverse proxy examples no longer inline the admin token in the site file.
  nginx sites are conventionally `0644`, so a pasted token was readable by every
  local user and provided no factor at all against a local attacker. The
  directive now lives in an `include`d snippet owned `root:www-data` at `0640`.

[Unreleased]: https://github.com/neuralyze/valheim-portal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/neuralyze/valheim-portal/releases/tag/v0.1.0
