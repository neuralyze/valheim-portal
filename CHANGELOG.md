# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `tools/jumpstart/settlements/road_reach.py` + `roads/geography.py::RoadGraph` — A SITE IS
  WHERE A ROAD CAN GET TO, OR IT IS PORTAL-ONLY, AND SAYS WHICH WITH NUMBERS. With the 15 frozen
  site pins and the 14 outlier anchors removed, the solver ranked candidates by what a tower
  needs and never asked whether a road can get there: the provisional route came back 35
  segments, 7 UNROUTABLE (`wt-spawn` in a 186-cell pocket of the router's own graph on the
  spawn landmass; `wt-north`, `wt-east`, `lh-east`, `lh-west`, `tree-west`, `tree-near` on a
  62,618-cell island of it, against 269,270 cells in the spawn window's largest strong
  component). `road_priority` — authored, published, and read by nothing — is now the
  declaration (`trunk`/`spur` is a road class, `none` is portal-only by design) and the
  verdict is measured in the router's own terms, the graph the extremities of `geography.py`
  are already held to: the site's snap lies in the window's largest strong component of its
  class's graph, on the window's trunk landmass (its largest land component — a road is not a
  licence to bridge to every islet within 96 m), in a window a crossing roots. Every site
  `sites.yaml` publishes carries `road: {class, roaded, reason, why, evidence}`. The solver
  takes the best candidate a road reaches (`reach_refused` carries what it passed over, with
  numbers); only a kind with NO reachable candidate puts the site on the best unreachable one,
  published `roaded: false` — a building is never deleted and no road is promised to ground
  none reaches. `roads/spec.py` reads the verdict: a portal-only site is not a node, an edge
  that names one is dropped with the verdict's numbers in `NO_ROAD` (never an UNROUTABLE
  edge), and `NOT_ADOPTED` — `sudrberg-keep` by name, for the default pair — is gone with the
  `wteast` / `lheast` / `dockshore_direct_to_wtsouth` prose that described one seed's accident
  under a name the solver rebinds. The searches are asked for every candidate their
  thresholds admit, per window (a `limit` of 14 hid the 6th reachable spawn-window watchtower).

- `tools/jumpstart/settlements/plan.py` — A NAME IS WHAT THE SITE IS, NOT WHAT THE SEARCH
  RANKED. `wt-spawn` was whatever tower the search ranked first (1.9 km from the StartTemple on
  Pirate68), so every leg typed for one seed's name ran to another seed's accident. Each
  `OUTLIERS` record now carries the `role` its name says (the operator's own words, git
  845678d: `wt-spawn` "the first built thing a player on foot can see", `wt-peak` "the highest
  watchtower", `lh-west` "the west isle's north cape") and `TOWNS` the trunk `window` it
  stands on; `select_outliers` picks, per name in turn, the candidate that best fits it among
  the best remaining of its kind (as many as there are names still to place), so the SET of
  places is the search's own and only the names move. Pirate68 re-solved with nothing pinned
  lands within metres of the old pins that were the same search's earlier answer (`wt-spawn`
  (114,-108) vs 110,-104; `wt-north` (148,1190) vs 152,1194; `lh-west` (-102,2584);
  `tree-near` (-626,-234) vs -614,-236) — now by rule, on a seed that has none.

- `tools/jumpstart/roads/declared_sites.py::site_legs` — ONE JOIN RULE FOR EVERY SITE. The
  rule the owner-declared harbours already had (the nearest trunk node of the site's own
  landmass, by straight line, the router still finding the route) now also closes the gap in
  the authored topology for a roaded SOLVED site: one whose typed partner is portal-only, on
  another landmass, or was never typed gets ONE derived leg, `J<n>`, in its own declared
  class. A publication without verdicts derives nothing (its authored topology is all it
  ever had), so a seed solved before this change plans exactly as it did.

- `tools/jumpstart/buildstage.py` step 6.5 `decks` — THE BUILD NOW BUILDS A CROSSING.
  None of the 8 build steps built a bridge: pipeline row 12 handed the build the seed's
  `crossings` contract and no step consumed it, so every declared crossing on a fresh world
  stayed a hole (Fresh42: 9 declared, S4-meadhall-southeast-x01 carrying 0 of 27 m; all 22 of
  Ulfsland's bridges were laid by agents running tools by hand). The step runs
  `roads/boardwalk.py plan|build --from-network --spans <crossing>` — the one seed-driven deck
  builder; `crossings/` supplies its geometry — one unit per crossing in
  `crossings.<Seed>.yaml`, after `bodies` (the last step that writes ground) and before
  `roadside`/`recheck`, which now require it. A crossing whose bank's road step 4 refused is
  BLOCKED by name through the corpus's own pairing (`BuildStep.stands_on`); every other unit
  gets the builder's own verdict (terrain precondition, six guards incl. the structural and
  water-legality verdict). New import-time check `unbuilt_defects`: a label the pipeline hands
  the build that no commanded step consumes refuses the table ("a declared crossing with no
  builder"). Bridge roles are their own record kind, so Ulfsland's 4 false S1/S2 OUT_OF_ORDER
  rows are gone, and a deck is owed the recheck of the segment it stands on, taken after its
  last piece. `boardwalk.py`: `--spans` now selects AFTER the whole-population channel
  collapse (T8-stenvik-wttown-x01 is T3-temple-meadhall-x01's water and is SERVED BY it, not
  decked twice), every deck batch is stamped `meta.stands_on`, and `burial_guard` reads the
  gate's own lattice-vertex reduction (`assemble.bed_under`) — it had refused T3-x01 at
  "0.083 m" on a pole whose top sits exactly on the vertex the gate reads.

- `tools/jumpstart/pipeline.py` — THE THIRTEEN BUILD STAGES, DECLARED IN SOURCE, IN ORDER.
  Ulfsland was built by a sequence of agent decisions: every stage is a real tool with a real
  CLI and a real refusal, and the ORDER was nowhere but in the head of whoever was driving, so
  a fresh seed could not be re-run. The table declares what each stage PRODUCES and what it
  REQUIRES, by label, and `audit_graph()` refuses at IMPORT a table in which any requirement is
  UNOWNED (no stage makes it) or INVERTED (a later stage makes it). It caught a real defect on
  its own first run: the table put `route` before `census` and required it, which is a cycle —
  the resolution is that ROUTE HAPPENS TWICE, and `roads/network.py --provisional` already
  existed for exactly that. Artefacts are keyed BY SEED where their readers are
  (`spec.sites_path`, `geography.geography_path`) and scope-checked through
  `worldseed.require_scope` against the run's own seed/world, never against the file's name.
  Four states, not two: MISSING, OUT_OF_SCOPE, PRODUCER_SLOT_MISSING (the readers key per seed
  and the writer does not) and STALE (present, right seed, and OLDER THAN THE INPUT IT WAS
  DERIVED FROM — the fifth blocker, which no existence-or-header check can see). The default
  work directory is `ledger/runs/<World>/pipeline`, not `/tmp`: five of Ulfsland's thirteen
  stages left their interchange in `/tmp` and it is gone, which `pipeline.py status --world
  Ulfsland` now says out loud. `run` REFUSES the live `Pirate68`/`Ulfsland` pair outright
  (exit 5) because stages 5, 6 and 11 write the corpus the gate reads, in place.
  `pipeline.py selftest` is 9 cases through `accept/registry.py`'s own `run_cases`, on five
  DECLARED dependencies (`ordinal`, `owner_map`, `scope_header`, `presence`, `freshness`) each
  neutered IN BOTH DIRECTIONS — an `off` arm a must-REFUSE case flips under, and a
  wrong-answer arm (`reversed`, `blind`, `always-stale`) a must-ACCEPT case flips under.
  MEASURED on the first run: all four one-direction neuters failed to prove their cases,
  because switching a check off only ever protects the accepting arm. 9/9 PASS, every proof
  `verdict` and not one `assert`, and every neuter reported with the OTHER dependencies that
  still refuse under it, so a neuter that emptied the instrument cannot stamp a case PROVEN.
  DRIVEN 2026-09-23 on seed `Fresh42`, a world this tree had never built: stages 1-6 green
  (world save `28d54240…`, 34,426,919-byte grid, 12,555-entry location dump, 26-window
  1,105,747,603-byte VHPATCH1 field, 21 geography nodes, 16 solved sites), and stage 7 named
  as the wall — `worlds/<World>/world.yaml` and `worlds/<World>/<preset>/placements.yaml` have
  NO PRODUCER IN THIS TREE, which `worlds/README.md` has said in its own words all along.

- TEMPORARY, and deliberately easy to remove: `Smoothbrain-ServerCharacters` 1.4.17.1 reaches
  the Ulfsland server and the clients of its four editions from ONE archive that
  `tools/servercharacters/build.sh` compiles from upstream `bb7d3cd6` plus the patches in
  `tools/servercharacters/patches/`. A profile
  manifest entry can now declare `"source": "local-build"`, which makes
  `cmd/profile-definition-builder` publish the archive's SHA256 and size and no `url` at all,
  and makes `cmd/valheim-profile-sync` install the copy compiled into itself - after checking it
  against those two values - instead of downloading anything. Server and clients therefore run
  the same bytes, which is what makes ServerSync's `MinimumRequiredVersion` /
  `ModRequired = true` unable to mismatch; a client carrying a different build refuses the
  install and tells the player to download the current client rather than failing mid-join.
  Gated on the definition naming the package, so no world but Ulfsland is affected. The archive
  is NOT in git - it is built into the gitignored `internal/servercharacters/embedded/` - because
  the mod has no licence and this repository is published; running our own PATCHED compile on
  the operator's own machines was their call, on 2026-09-14 and again on 2026-09-15, and
  redistributing it is not. The patches are committed, because they are our own text and they
  are the record of exactly how our binary differs from the author's.
  `tools/servercharacters/README.md` and `deploy/upstream-sources.json` carry the record, the
  boundaries and the one-command retirement for when Thunderstore publishes 1.4.17. The Hexium
  backend added earlier is untouched and remains the route for the next off-Thunderstore mod.
- New characters spawn WEARING their kit, at the quality the preset asked for, with a filled
  backpack. `CharacterTemplate.yml` gains three keys in our ServerCharacters build -
  `quality` (prefab -> quality, clamped at runtime to the item's own `m_maxQuality` and
  logged when clamped), `equip` (ordered prefab list, highest priority first) and `contents`
  (container prefab -> items, which fills a Vapok-AdventureBackpacks backpack through that
  mod's own public API, reached by reflection so nothing is pinned to an AdventureBackpacks
  version and ServerCharacters still loads where that mod is absent). Upstream equips
  NOTHING - the string `EquipItem` occurs zero times in its whole assembly - and hardcodes
  quality to the literal `1`, so until now every templated character spawned unarmoured and
  unarmed holding a full set of gear, every `quality: 3` in every preset was silently
  ignored, and a granted Megingjord was a 2 kg no-op because its carry-weight status effect
  only applies while equipped. `tools/servercharacters/preset_to_template.py` renders the new
  keys straight from the preset and never guesses what to wear. Unknown top-level template
  keys are now ignored and logged instead of throwing away the entire template.

  ROLLOUT ORDER MATTERS ONCE, HERE: new DLL to the server and to all four client editions
  first, every player re-synced, and only THEN the new-schema template. ServerCharacters
  1.4.17 throws an uncaught `YamlException` on the new keys and discards the whole template -
  skills, items and spawn together - and `CharacterTemplate.yml` hot-reloads with no restart,
  so a template that arrives ahead of the DLL ruins every character created in that window.
  The DLL step itself is safe: the version check is symmetric, so a stale client is simply
  refused at the handshake.
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

- `tools/jumpstart/blueprints/solve_placements.py` (`LEVELLABLE_PAD_DEPENDS_ON`) — AN
  INSTALLATION IS WRITTEN WITH A PAD THE ENGINE CAN LEVEL, OR REFUSED WITH THE NUMBERS. The
  declared `footprint_m` pad is a floor (`flatten.PAD_EXTENT_DEPENDS_ON` `declared_pad`) and
  the only thing the solver did with a pad beyond the +/-8 m apply clamp was drop the fixture
  ring, "the body's pad is written" — a pad carrying the same floor, so on a site where the
  floor is the whole problem it re-billed the rectangle it had just refused. Fresh42 drive 11:
  pre-bonemass#iron-era-workshop declares 70 m over a 22.3 x 23.9 m body; all 13 candidates
  were past the clamp on 70 m (chosen site: 442 of 5041 samples, cut 10.54 m / fill 9.569 m),
  the block was recorded REFUSED and build step 1 refused the unit, losing the installation
  and everything standing on it — while the same centre levels a 50 m pad. Where NO candidate
  levels its declared pad, the floor now gives way 5 m at a time (the whole pool re-billed at
  each step, never below the body at its yaw and its fixture ring), and the pad written is the
  largest any candidate levels; a pool in which nothing levels on any rung is ranked by how far
  each candidate is past the clamp, and the least bad is recorded REFUSED with every rung's
  numbers (`pad_ladder`). A candidate that levels its declared pad is untouched. Offline
  re-solve, Fresh42: iron-era-workshop stays at (-844.5, 4995.5) on a 55 x 55 m pad, cut 7.869
  m / fill 7.58 m, 0 samples over (the writer's own `flatten.build`: 3192 samples, 0 over;
  step-1 `levelling_refusal`: none); the other 12 installations' solved blocks are
  byte-identical, and all 13 of Pirate68's are byte-identical to the shipped rule.
  `solve_placements.py --selftest` 63/63 (16 new, each stated with its neuter); `--verify`
  now audits a recorded block's own bill against the clamp.

- `tools/jumpstart/roads/network.py` (`BANK_CEILING_DEPENDS_ON`) — A DETECTED CROSSING'S LEVEL
  RUN NOW TAKES THE WALKED-RAY CEILING. `writer_water` asked a drop only of dry stations, so a
  refused ray whose foot fell on the deck's own stations asked nothing; and `build_segment`
  pinned both banks to one median-chosen height before the ceiling ran, which then skipped the
  closed bounds. Fresh42 drive 6: W9-vestvik-lhwest refused at build step 4 (0.9003 m rim step
  at (-770,601) beside crossing x01, held at 33.304 m), so its three crossings were never built.
  The drop is now asked of the deck stations when both foot stations are bridged, and the
  ceiling narrows the window's legal interval before its one height is chosen, never below
  deck clearance. Re-plan: W9 CLEAR by `ceiling 1 + terminal trim`, x01 at 32.797 m, all three
  crossings kept; the writer's own stamp: 0 caused rim steps, 0 drained, 0 approach failures.

- `tools/jumpstart/roads/network.py`, `roads/ribbon.py` — THE PLAN ASKS THE WRITER'S OWN QUESTION.
  `writer_water` stamped the half-grid routing field, which smooths away one-metre shore steps,
  and stamped with no pad keep-outs, so a batter the writer clips at a planned pad read CLEAR.
  Fresh42 drive 6: S7-stathub-wtnorth planned CLEAR and was refused at step 4 (4 steps, worst
  1.3868 m, `refused pad`). The plan now stamps the writer's integer Heightmap patches with the
  writer's pad and POI keep-outs, counts pad-owned rim steps, and judges each approach onto a
  pad by the acceptance gate's own lattice walk (`PadKeepOut.arrival_walks`), grading the whole
  gap with a bounded foundation collar. S4-meadhall-southeast (berm 0.573 m, step 1.3401 m at
  lh-south) and T5-temple-dockshore (step 0.8482 m at early-dock) now stamp with 0 caused rim
  steps and 0 approach failures. The first drive-7 plan LOST four legs to the stricter check
  (T1, S8, S11, T6 refused at plan time, all laid by the real writer in drive 6); three rules
  bring the plan to the writer's own reading: `ribbon.ARRIVAL_PADS_DEPENDS_ON` (a road's earthwork
  is pinned to, and collared around, only the pads it ARRIVES at, within its own reach; a cell the
  writer would veto is an anchor, not skipped), `PLAN_REFUSAL_DEPENDS_ON` (only rows the stamp
  explains -- `refused water_fill|pad|poi` -- refuse a leg; rows owed to a planned pad are split
  off as `ribbon.main` does), and `ARRIVAL_ADVISORY_DEPENDS_ON` (an approach the grading cannot
  fix is recorded for the gate, not a reason to delete the road). `PROBE_PAD_DEPENDS_ON`: the
  band-free probe judges a leg before the joint solve, so a probe refused ONLY on pad-boundary
  steps is SERVABLE and names its cells for the census -- S8's confined search found "no route"
  while its probe said "not the census's", a fixed point that disconnected wt-south. Drive-7
  plan: the same 15 legs as drive 6 (18,070 m), 9 crossings (drive 6: 5), every leg CLEAR after
  the joint solve. The first full drive-8 build then refused 5 of those 15 at step 4 (T1 25 steps /
  3.0465 m, T4 2, T6 4, S7 2, W9 1) -- all virgin ground beside the pad collar, owned by nobody --
  and the offline harness matched those counts exactly, so it is the instrument to iterate on.
  `ribbon.COLLAR_EDGE_DEPENDS_ON` (the ground just outside the collar is an anchor, so a cell at
  the edge is graded against it), `COLLAR_PEEL_DEPENDS_ON` (a collar cell that stands over the slide
  limit of ground or a pad cell this write leaves alone is put back to its previous height, repeated
  to a fixed point: a cell that cannot be graded all the way down is not graded), and a collar that
  is gated on a graded approach. Harness, 15 legs: 0 caused steps on 14; S11's 4 are the virgin-
  ground over-report at lh-south (the writer builds S11 after S4 has graded that edge and laid it
  clean). `network.COLLAR_CHOICE_DEPENDS_ON` asks a leg the collar leaves refused both ways;
  `buildstage.COLLAR_RETRY_DEPENDS_ON` asks the REAL writer once more without the collar
  (`ribbon.py build --no-connection-collar`) when `road_ground` refuses, because a refused write
  sends nothing and the plan cannot see the order-dependent ground. A plan that refuses every
  earthwork row instead lost S11 (unroutable) and T6 (rejected): 13 of 15 legs.
  `network.PAD_BATTER_DEPENDS_ON`: the router's pad keep-out grows 6 m more for an INSTALLATION pad,
  which the build lays at step 2 before any road and whose claimed ring the road's batter then
  cannot meet. Fresh42 drive 9, real writer: S5 and S11 both refused 3 steps (1.3009 / 1.2944 m) at
  the portal hub's north-west corner, `rim ground owned by sandbox-portal-hub#680`, collar on and
  off. They now route 19.5 m off the hub (were 8.1 m) and no leg is lost (15 legs, 9 crossings). A
  margin on every pad was measured and lost S4, T6 and W9: site pads are laid after the roads and
  claim nothing yet. `merge_pad_claims` gives a written pad its PLANNED role, because the ledger
  calls every pad a writer lays a `site_pad`, installations included.

- Fresh42 drive 10 closed 14 of the last 15 and showed the remaining defects were in the fixes
  themselves. `ribbon.COLLAR_PEEL`: the peel put a cell back as a modified cell at delta 0, which
  writes the generated ground over whatever an earlier leg left there -- W9 cut 1.14 m out of S5's
  fill (5 rim steps, worst 1.9211 m); it now restores the compiler's own flags exactly. `ribbon.
  SNAP_SHARED`: a leg that runs within 1.5 m of a built leg for 8+ stations lies on it (W9 ran 0.9 m
  off S5 and S11 at one height for 110 m). `network.BANK_BED`: a detected crossing's deck is never
  chosen under the dry ground at its two banks -- the walked-ray ceiling had put W9-x01 at 32.797 m
  against banks at 33.252 m and the gate refused it (`IS NOT A CROSSING`, +0.455 m); W9 now plans
  CLEAR at 33.5 m. `buildstage`: `--resume` treats a laid collar retry as the unit's answer.
  `boardwalk`: parallel crossings share an existing deck (S11-x01 onto T3-x01, datum merged within
  the bank-step limit, nothing removed); landfalls ramp up to 0.30 m per tile; the route guard asks
  the gate's blockers at every point of the carriageway, not only its 1 m stations (an existing T3
  rail post sat 0.0001 m short of one). `terraform/stock.py`: step 6 reads the seed before seating
  fixtures, so a chest no longer closes the arch face a tree left open (u-stathub).

- Fresh42 drive 11 passed every acceptance section but one: `roads` traversability, 1C, T7-stathub-
  stenvik station 222 at (336.6, 344.49997) -- a station on a half-lattice line, where the gate
  snaps half-way UP and the engine's RoundToInt snaps to EVEN, so one vertex read 81.7624 m and
  81.8475 m. `ribbon.HALF_LATTICE_DEPENDS_ON` moves a node 2 cm when a station of the gate's own
  resampling lies within 1 mm of one (bridged nodes and ends never move; a move that needs an
  unscanned zone is not made). The same drive left the workshop unbuilt: step 1 and, once
  `solve_placements` could pad it (above), step 5 refused `iron-era-workshop` on one roof hole in
  `halvar-master-refinery.blueprint`. `blueprints/data/authored_repairs.json` gained one
  checksum-bound roof panel for it and one checksum-bound pose correction for the source's row 573
  (`wood_wall_roof_45`, yaw 247.5 -> 67.5): the gate judged it inverted by a 0.3 m seam margin the
  plan's rounding hid. Both refuse on a base or row mismatch; the corpus file is untouched.
  `buildstage.py` resumes a laid collar retry as the unit's answer, and `roads/network.py` keeps
  the arrival-reach fixture on the centreline mask.

- The first from-scratch Ulfsland regeneration (seed Pirate68, over an archived ledger) reached the
  tools' limits on that seed and found five more defects, each with a known-bad case and neuter.
  `buildstage --replace-live` builds the live default pair only over a distinct archived ledger;
  `pipeline.FIELD_WINDOWS` scans it on `worldseed.ULFSLAND_WINDOWS` (derived windows left vestvik's
  half of the continent in no window: 1 town candidate for 2 towns); stage 10 reads the plan file
  stage 9 wrote (`segments.yaml`, not `segments.Pirate68.yaml`). In `roads/network.py`: a
  span-ceiling retry no longer forbids ALL water (`SPAN_REPRICE`: a leg needing a bridge 0.37 m past
  the 96 m ceiling was reported unroutable -- W10, T2, W1 now route), a self-adjacency refusal now
  re-routes (`ADJACENCY_REROUTE`, S14), a declared abutment bench is not made a crossing by
  freeboard alone (`ABUTMENT_BENCH`), and a plan-time rim row is attributed to the cause the stamp
  refused it for (`OWNER_CAUSE`). In `roads/ribbon.py` a pad-boundary row whose ground already
  stood past the slide limit from the pad's datum is owed to the pad, not charged to the road
  (`PLANNED_PAD_CLIFF`; S6 and S8). An edit to the probe `build()` dropped the stand-off mask on
  its retry paths and silently changed Fresh42 (S11 lost, seven other legs re-profiled); found by
  comparing a Fresh42 re-plan against the pre-edit commit and fixed -- the Fresh42 plan is again
  byte-identical (15 legs, nodes, profiles, crossings).

- The first from-scratch BUILD of the regenerated Ulfsland found two defects that Fresh42 could not
  show, because a fresh world has its own name. (1) Every save reader resolved the install root
  from `$VALHEIM_WORLD_ROOT` only and defaulted to the production tree, so on the default pair the
  regrade census, the step-5 settlement census and the acceptance gate read PRODUCTION's hand-built
  Ulfsland save instead of the world being built (read-only; nothing was written to it): 19 of 30
  roads were refused for burying pieces production had and the regenerated world did not. The
  resolver now reads `$VALHEIM_WORLD_ROOT`, then `$VALHEIM_ROOT`, then the default, `pipeline`
  exports the scope's root to every child, the ledger manifest pins the world uid, and every reader
  refuses a save whose uid differs from its ledger's pin. (2) Two builds against one staging
  server interleave -- Upgrade World keeps a single staged queue, so one run's `stop` cancels the
  other's staged removal. `pipeline.py run` and `buildstage.py run` now take an flock on
  `<work>/writer.lock` and refuse (rc 75) while another run holds the world, children inherit it,
  and `ledger/replay.py` holds a lock across the whole stop/verb/start bracket. Also: a sibling's
  true extent (not its circumscribed circle) is used when the circle leaves no clearing radius
  (vestvik-hut-1), and a zone that cannot generate is refused with its registered location
  (hognest-castle, zone (9,22)).

- `tools/jumpstart/roads/boardwalk.py` — A DECK'S RAILS NO LONGER STAND IN ANOTHER ROAD. The
  furniture guard read only the bridge's own straight span at its deck datum, so T3-temple-
  meadhall-x01's rail poles and beams stood across S11's built carriageway (Fresh42 drive 6
  `roads/obstruction`, 14 of 181 pieces, ACTIONABLE, owner step 6.5). The builder now reads the
  built roads' standing surfaces through the gate's own instruments and opens the rail where a
  road passes, keeping deck, piles and caps; any remaining obstruction still refuses. T3-x01:
  13 rail pieces opened at S11, 0 remaining blockers, structural verdict true.

- `tools/jumpstart/blueprints` — `blackforest-walled-base`'s AUTHORED ROOF GAPS ARE CLOSED. The
  selected `fox-0123456.blueprint` (third-party, not vendored) carries an open joint between
  its 45° and 26° roof courses and open chimney gable ends: `structures/roof_skin` 3 gaps,
  worst 4.677 m. `blueprints/data/authored_repairs.json` binds a project-owned additive
  blueprint of 10 timber pieces to the source's SHA-256, and `to_rcon_plan.read_objects`
  composes it for every consumer; a mismatched base or changed addition refuses. Emitted plan:
  0 roof_skin gaps, shelter check passes, chimney outlets kept.

- `tools/jumpstart/roads/ribbon.py` (`INTERSTICE_APRON_DEPENDS_ON`) — THE GROUND BETWEEN TWO
  ROADS THAT SHARE A CORRIDOR IS LAID AS A TAPER FROM ONE CARRIAGEWAY EDGE TO THE OTHER. The
  corridor rule's own test was "a batter-grade surface joins the two edges across this gap", but
  it then levelled the gap flush with the higher road. That left the whole height difference as
  one step at the lower edge, and the earth budget often clipped it on its first sample. Fresh42
  drive 5, step 4: J-southcape-wtsouth runs 8 m beside S8 out of wt-south, 66.13 against 64.65.
  It was refused rc 7 with 8 steps, worst 2.5455 m at (-676,-440) against S8's fill batter. The
  plan's claim boundary had approved that edge (1.484 m against an allowance of 1.824 m). The
  apron now descends 66.133 → 65.672 → 65.161 → 64.649 (steps ≤ 0.512 m), and J is CLEAR
  against S8's recorded write. An offline sequential step 4 on a scratch re-plan, from the
  drive-5 ledger cut, lays all 14 legs with 0 caused rim steps; 887 aprons were tapered. The
  known-bad case is `selftest_interstice_taper` (neuter `_INTERSTICE_APRON_MODE="flush"`).
  `selftest_interstice_trigger` now proves its trigger by the ground a neutered trigger would pave
  past its own toe (1081 samples), because under the taper the rim gate passes that case.

- `tools/jumpstart/roads/network.py`, `roads/boardwalk.py`, `crossings/assemble.py` — A CROSSING'S
  BANKS LIE ON THE LINE THE ROAD WRITES, AND A DECK'S RAILS STAND OFF THE ROAD IT CARRIES. On
  Fresh42 drive 3, 6 of 12 crossing records put a bank 3.25-4.98 m off their own
  `centreline_straight_from_to`. The detector took each bank from the searched polyline, snapped
  it to a 2 m grid, and never re-read it after `straighten` replaced the window with its chord.
  Overlapping windows on W9 moved each other's chords by up to 2.90 m. So step 6.5 refused
  T1-x02, T5-x01 and T13-x01 on `span_coverage`. `build_segment` now writes the banks from the
  straightened free-run ends and keeps windows disjoint (`CROSSING_BANKS_DEPENDS_ON`), and it
  stamps `terminal_banks` where a free run reaches the segment's own end. The offline re-emit has
  0 of 12 banks off their line (worst 0.006 m), and T1-x02 now builds. Every built deck's
  wood_poles were rail posts 0.20 m inside the carriageway (T1-x01 36/36, T3-x01 24/24, S4-x01
  32/32), which the gate refused as `roads/obstruction`. A deck is now as wide as the ribbon's
  levelled bench (8 m trunk, 6 m spur), which puts the rails at 3.8 / 2.8 m. A new `carriageway`
  guard asks the gate's obstruction question before a build. `assemble.bridge` drops rails and
  outer rows that the bank has swallowed whole: at 6 m, S4-x01's hillside buried them by up to
  0.669 m. `trim_buried_overhang` also drops an overhang station the burial guard would refuse
  (S13-x01, 0.065 m). `tile_covers` now counts a tile seam as covered. The abutment refusal on a
  terminal bank names the segment's terminus as the owner.

- `tools/jumpstart/roads/network.py` (`ARRIVAL_DEPENDS_ON`), `roads/junction.py`
  (`PAD_GAP_DEPENDS_ON`), `roads/ribbon.py` (`PLANNED_PAD_RIM_DEPENDS_ON`): A ROAD ARRIVES ON THE PAD
  IT SERVES. A* can only start outside a pad's grown mask, so every leg to a pad ended 7-10 m short
  of it. `enforce_pad_edge` trimmed nothing, nobody paved the gap between, and the junction solve
  never read the pad. On Fresh42 drive 3 the plan's 16 legs had 0 road-vs-pad pairs against 59
  live pads. That gave 5 `terminus_step` 1B risers onto `complete-station-hub` (the road at 79.45,
  the pad at 77.67, a 0.907 m cut face), 1 onto `sandbox-portal-hub` (1.045 m, even though the
  datums agree), and step-4 rim refusals on T8 (road at 50.01, wt-town at 56.0) and S8. Now a
  searched line carries a straight spur toward its node until the level cap meets the pad edge.
  The spur stops short at water, a POI or unread census ground, or another site's pad. The
  road-to-pad gap is read from the level footprint, so the joint solve holds the terminus to the
  pad datum. At step 4, a rim sample inside a pad the plan will lay next is judged against that
  pad's datum and recorded as `rim.owed_to_planned_pads`. Offline on the drive-3 plan, S8 goes
  from 16 refused steps to 0 caused and rc 0 (worst 0.2514 m against 72.48). T8 still refuses,
  and the datum owns that.

- `tools/jumpstart/roads/route.py` (`JUNCTION_CONFLICT_DEPENDS_ON`), `roads/network.py`
  (`ARRIVAL_REACH_DEPENDS_ON`, `WATER_CAP_DEPENDS_ON`): ONE LEG THAT CANNOT MEET ITS PAD NO
  LONGER COSTS THE PLAN TEN OTHERS, A LEG TO A PAD STARTS WHERE THE PAD'S DATUM CAN BE MET, AND
  ONE LEG'S WATER CEILING NO LONGER REFUSES ITS NEIGHBOUR. The Pirate68 provisional re-plan of
  2026-09-26 had 11 junction refusals and 562 pairs in 10 places over the allowance. Every
  refusal named one empty station, T4 754 [35.238, 34.794]. The stathub search had started at
  (640, 1090), on a knoll 40 m high, 7.47 m of spur from `complete-station-hub` (datum 31.85).
  Stage one refused one leg of the worst disagreement anywhere in the connected component (the
  temple, (505,1129), (1247,567), ...), and refused T4 itself only after ten others. It also
  climbed eight legs to 0.25 that the conflict never involved. Now:
  - A climb or a refusal is owed only to an irreducible conflict: the smallest set of legs, grown
    outward from the empty station, that has no profile and gets one back if any member is
    removed.
  - Where the nearest cell's spur cannot meet its pad's datum at the ceiling rung, the search
    starts at the ring cell nearest the one it replaces whose spur can. For stathub that is
    (640, 1086).
  - A walked-water ceiling that would refuse a leg the uncapped joint solve joined is withheld.
    The capped leg keeps its own writer-water refusal. On the first re-plan, S13's dock ceiling
    had refused S7.

  Re-plan with all three: Pirate68 has 2 places / 71 pairs over the allowance, 27 legs joined and
  1 refused (T6, as before the arrival spur). No leg that routed before is lost, and T4 keeps its
  4 water steps. Fresh42 (drive-4 census, pads cut at 608): the same 13 legs on the same lines, 0
  pairs over, 0 refused, and T1, T3, T5, S4, S5 and T7 no longer climbed past their design grade.

- `tools/jumpstart/settlements/build.py` (`site_portal_spot`, `seed_census`,
  `SITE_SEED_DEPENDS_ON`), `terraform/stock.py` (`Walkins(extra=)`, `gate_solid(s)`) — THE
  SETTLEMENT END OF EVERY PORTAL PAIR KEEPS ITS WALK-IN. On Fresh42 drive 4 the gate's `portals`
  approach_ray refused 5 of the 14 settlement-end arches (save af96bfb62427): u-wtspawn,
  u-wtpeak, u-wtnorth, u-lhwest and u-treenear. Each had our own piece on one face (the tower's
  stone_pillar 1.5 m behind the arch, the lighthouse's stone_wall_2x1, sibling tree-near-3's
  wood_door) and a seed Birch1, Beech1, Pinetree_01 or rock4 on the other, standing past the
  16.14 m clearing with its bounding box reaching the corridor. The hall had the walk-in book;
  the site end had none, and it was solved against `units[0]` alone. The site end is now solved
  through `stock.Walkins`. The book holds every sibling body whose pad reaches it (the arch must
  clear those too) and the seed's own trees and rocks round the pad. Those are read off the live
  world before the solve: an `objects_count id=*` tile census gives the names, a bounded
  `findObjects -detailed` stepped in height gives position and rotation, and the read is ledgered
  as an `observe`. A read the listing cannot complete refuses the pair. The body's yaw is tried
  first, then a quarter turn; with no spot left, the pair is refused with `walkin_reason`. The
  site board walks with the book. The remedy is to site the arch, not to clear seed objects, and
  that was chosen by measurement: offline over the frozen save, 14 of 14 site ends have a walk-in
  judged by the gate (9 before). 5 arches move 1.0–3.6 m on their pads, and nothing is deleted. A
  corridor clear would have deleted 11 seed ZDOs, among them a rock4_forest and four rock4_coast
  22.9–26.9 m out, on the strength of bounding boxes the gate books as upper bounds.

- `tools/jumpstart/terraform/stock.py`, `network/waypoints.py`, `settlements/build.py`,
  `blueprints/fixtures.py` (`free_spot(accept=)`) — EVERY ARCH KEEPS ITS WALK-IN, BY THE GATE'S
  OWN RULE. On Fresh42 drive 3 the gate's `portals` approach_ray refused 10 of the hall's 25
  arches (save 05eb7c6c05de), and every blocker was ours: neighbouring arches, a neighbour's board,
  the hall's own brazier (u-sudrberg stopped at 3.75 m by u-stenvik's board; u-wtspawn at 4.25 and
  5.25 m by arches; u-stathub / u-blacbase / u-meadhall 2 m apart on one facing axis). The packing
  tested each arch as a box among boxes. Now `stock.Walkins` (`WALKIN_DEPENDS_ON`) admits a
  fixture, arch or board only if every arch still has a face that the gate's `check_approach`
  can walk the Player capsule into (its `APPROACH_SPAN_M`/`START`/`STEP`, `_approach.approach_reach`
  over the gate's own `Solid`s, the capsule from `fixtures.walker_capsule`). An arch that has no
  such spot is refused with its number (`walkin_reason`). `hub_arch`, `solve_placement`,
  `pack_settlement_arches`, `portal_guidepost` and `fixture_ring` share one book, so stage 7
  sizes the ring for the arches' walk-ins. Measured offline on the drive-3 hall (real body,
  written 56.8 m pad): 27 of 27 arches (11 installation, 16 settlement) have a walk-in, judged by
  the gate over the frozen save's census with the re-solve's arches, boards and posts; before, 15 of
  25 built. 16 of 16 boards stand nearest their own arch, and the stage-7 ring still needs a 56.8 m
  pad. On the drive-3 state the book refuses the same 10 arches the gate does.

- `tools/jumpstart/terraform/stock.py`, `terraform/flatten.py`, `blueprints/solve_placements.py`,
  `settlements/build.py`, `network/waypoints.py` — A FIXTURE STANDS ON THE GROUND THAT WAS LEVELLED
  FOR IT. Step 6 stood stations, props, chests and portals at the pad height anywhere in the site
  square (`footprint_m / 2`) while step 2 levelled only the body's rectangle: on Fresh42 drive 2
  28 of 32 fixtures at `mistlands-blackforge-base` stood off their pad, from 2.95 m in the air to
  the cartography table (seq 1978) 1.6 m past the edge with its top 1.98 m under the bank. In the
  offline hall packing, 76 of 106 fixtures, arches and board pieces stood off the pad, among them
  13 of 16 settlement arches. Stage 7 now sizes each pad for its fixture ring
  (`flatten.PAD_EXTENT_DEPENDS_ON` `fixture_ring`, solved by `stock.fixture_ring`). The ring is
  dropped, and the site kept, where the clamp or the step-1 clearing bound forbids it. Every
  fixture, hall arch, board and settlement site end is searched on the written pad
  (`stock.SEAT_DEPENDS_ON`, the writer's own `stamps`), and one the pad does not reach is refused
  with its number. Measured offline at the drive-2 sites, the ring pads seat 106 of 106 (hall) and
  32 of 32 (blackforge) within the clamp. `flatten.declared_duplicate` is the one `duplicate_of`
  predicate for clear, flatten, stock and sites: drive 2 levelled `sandbox-harbour`'s pad twice
  (seqs 565 and 568).

- `tools/jumpstart/settlements/*`, `terraform/flatten.py`, `network/build_site.py` — EVERY FIXED
  WORLD/SEED READ IN THE SETTLEMENT, FLATTEN AND SITE TOOLS IS RESOLVED AT CALL TIME. The
  generator location dump (Pirate68's by content hash, in 9 files; drive 2's step-2 pad records
  carry it) now goes through `roads/poi.location_dump` (`clearance.location_dump`,
  `flatten.location_dump_path`). Ulfsland's ledger by path (7 sites) goes through
  `worldseed.ledger_path()`, `plan.json` by name (5) through `worldseed.plan_path()`, and
  `build._world_src` through `accept/instruments.world_save`, which refuses a save that is not
  the world's. The terminus-camp save directory and the build-site status file are keyed on the
  run's world. On the default seed every one resolves to the same path and bytes as before
  (MEASURED). `worldseed.py fixed-names`: 0 open in these files.

- `tools/jumpstart/roads/network.py`, `roads/route.py` — THE ROUTER ASKS THE WRITER ABOUT WATER
  BEFORE IT PLANS A ROAD. `route.WET_DEPENDS_ON` kept the batter out of the planner's wet test
  ("water inside it is not water this write removes"); `BATTER_WATER_DEPENDS_ON` then made
  `water_fill` refuse exactly that batter, and the planner kept approving it: on Fresh42 drive 2,
  T1, T5, S5, T9 and S13 were refused at step 4 on water-owned rim steps. `network.writer_water`
  now runs the WRITER'S OWN `ribbon.stamp` over the generated field, the build's
  `junction.rim_transfer` over the surface it leaves, `ribbon.rim_step_owner` and
  `ribbon.water_sign` (`route.WALKED_WET_DEPENDS_ON`) — the rays it walks, the feather and closure
  that repair what they can, and the TERMINAL CAP no station samples. MEASURED on the drive-2
  plan it refuses exactly the five legs step 4 refused and clears the other nine, including S4
  and T8 whose `water_fill` clips the feather closes. A refused leg gets, in order, the ALIGNMENT
  (`route.water_standoff_forbid`: an A* stand-off from the water the writer refused, sized by the
  refused ray's own reach `(y - 30) / 0.7813`, grown per round), a TERMINAL TRIM, and only then a
  CEILING on the stations whose walked rays are still refused; a leg the writer lays clear is
  never touched, and the joint junction solve is re-run with the ceiling in a leg's own bounds
  where it lifts one back into water. What is left is REJECTED at plan time with its number and
  its owner. `network.py selftest` carries BANK (the pond) and SHORE (the dock cap), each red on
  the shipped key (`_WET_TEST = "footprint"`) and the BANK ordering red on the ceiling alone
  (`_WET_REMEDY = "profile"`: it serves by cutting 1.91 m into the bank; the alignment cuts 0).
- `tools/jumpstart/roads/ribbon.py` — at step 4 the writer's pad keep-out now uses the pads the
  plan will lay (`planned_pad_claims`, moved here from `roads/network.py` so the router and the
  writer read one population), not a `pad_radius_m` disc per settlement. That disc was a town's
  100 m district or a watchtower's circumscribed circle plus 2 m. Before the change it looked
  harmless only because `sites_path` read Pirate68's `settlements/sites.yaml` on Fresh42. Once
  the path resolves by seed, T3, T7, S4, S7 and S8 refused at the rim inside those discs, 4.2 to
  26.5 m outside every planned pad. MEASURED offline: T3, T7, S4 and S7 lay again, S8 goes
  41 → 16 and T8 51 → 14 steps. The steps that remain sit inside real planned pads whose datum
  stands 3.7 to 6.9 m above the road beside them.
- `tools/jumpstart/roads/ribbon.py` — step 4 lays corridors it used to refuse at the rim.
  Replayed offline (`build --validate`, nothing sent) against the ledger each Fresh42 drive-2
  segment saw — the replay reproduces all nine refusals to the step and the metre — T4, T7 and
  T13 now pass the rim gate (3 → 0, 1 → 0, 4 → 0 caused steps), T8 65 → 1 and S13 154 → 102, and
  the five segments step 4 already laid still pass. Two classes closed. ANOTHER ROAD'S BATTER
  IS GROUND (`FOREIGN_BATTER_DEPENDS_ON`): the corridor rule read every sample another road
  authored as that road's carriageway, so beside another road's batter it left the ray
  ungraded; now a claimed batter cell is walked like ground (their surface is what the plane
  meets) and written only inside the other road's own band from its plan, their carriageway
  ends the ray, and the toe feather reads the live surface the rim gate reads (99 caused steps
  on five segments). THE RIM NO RAY OWNED (`RIM_CLOSURE_DEPENDS_ON`): where the line comes back
  beside itself the first cell outboard of one branch belongs to the other branch's ray, which
  had met ground; `close_rim` moves such cells, outward from the road, into the interval their
  written neighbours allow inside their own claim band and the gates (T4, T5, T7, T8, T9, S13).
  Also: `--validate` no longer writes the world's `blobs/index.json` through the guard
  sandbox's symlink (Ulfsland's index carries `guardcheck` entries from it), and
  `roads/boardwalk.py` resolves its crossing and segment corpora by seed. The water class
  (T1, T5, S5, T9, S13: 268 steps where the batter may not fill seed water) stays refused by
  name; its remedy is the route — see the RimWater report. `ribbon.py --selftest` gains three
  cases, each red on the shipped rule.
- `tools/jumpstart/blueprints/preflight.py`, `to_rcon_plan.py`, `base_geometry.py`,
  `terraform/sites.py` — step 5's installation bodies. MEASURED on the Fresh42 drive: 6 of 12
  bodies refused at plan, and two of those reasons were never printed, because `sites.py`
  kept the FIRST FOUR stderr lines of a refused plan and the emitter prints its repairs before
  its refusals (early-dock's only refusal, `co_located_rows` 6, and ashlands' second, 12, were
  cut away). The status is now every line from the first refusal line to the end, the notes
  are printed, the whole stderr goes to `index.json` (`sites.py --selftest`, the shipped rule
  red on both). The emitter's repairs, all DEFAULT, each named row by row: `upright_plan`
  matched the instrument's published rows by a double rounding (23.91052 → 23.9105 → 23.91 vs
  23.911) and handed over 31 of blackforest-walled-base's 35 inverted panels — now keyed at the
  instrument's own precision, a key two rows share is resolved by the instrument, a yaw that
  cures nothing is restored and named (sandbox-portal-hub's apex); `--drop-co-located-copies`
  drops a row the duplicate census calls a copy, by the section's own `buried_fraction` against
  KEPT siblings (complete-station-hub 1, early-dock 6, ashlands 12; every shared pivot kept);
  `--reorient-upside-down-panels` turns a face-down panel over IN ITS OWN SOLID — own prefab
  under the 24 frame rotations or its `_upsidedown` twin, vertices within 1 cm, the instrument
  choosing — and leaves one whose only upright pose rises away (ashlands 3 turned, 1 left, −0.674
  m); `--seat-sunk-props` lifts a `props_below_floor` prop onto the plane unless it would enter
  another physical solid (ashlands' hearth lifted 0.50 m, its table under the blackforge left).
  `shelter_plan` stays unapplied: 22 of the 23 holes it was ever pointed at are flues over a
  fire. The preflight now holds the gate corpus for the process: `structures._basis` caches by
  `id(part)` and never evicts, so a corpus reloaded per call lets a new part read a freed one's
  basis (reproduced in isolation: a y 5..6 box read as 0..1); 2 of ~120 early-dock runs under
  the per-call reload refused `unsheltered_deck` on a body the rest emitted [cause inferred].
  Offline plan, all 12 bodies: 6 → 9 clean; the 23 plans that emitted before on Fresh42 are
  byte-identical. Preflight selftest 39 → 68 checks, each new key neutered both ways, all
  verdicts.
- `tools/jumpstart/pipeline.py` — a stage is current only if its PRODUCER is. `satisfied` was
  keyed on the artefacts' existence, never on which version of their producer made them: MEASURED
  2026-09-25, `blueprints/solve_placements.py` was fixed twice, `worlds/Fresh42/*/placements.yaml`
  was the pre-fix solver's, and `status` read stage 7 `ok` (11 of 13 stages `ok`, stage 12 among
  them mid-build); the only escape was `--force`, which re-runs worldgen. Every successful stage
  now records a sha256 of its producer in `<work>/producers.json` — the files its Python
  processes actually loaded, read and executed under `tools/` (an audit hook injected for that
  stage only), the files its shell scripts name, and its own row — and `status` reports STALE
  naming the changed file, or UNKNOWN PRODUCER for anything with no record (UNREAD IS NOT CLEAR).
  `run` re-runs exactly the non-current stages and their descendants; `--from K` re-runs K and
  later and never an earlier stage; `--force` is unchanged. Stages that write the live world (1
  worldgen, 10 census, 12 build) run automatically only to create their output and are otherwise
  REFUSED (exit 6) with the reason and the `--from` command. Stage 12 also declares
  `<work>/build/buildstage.json`, because the census appends to the world ledger before any build.
  Selftest 14 → 25 cases, every new dependency neutered both ways, all proofs `verdict`.

- `tools/jumpstart/roads/network.py` now asks, AT PLAN TIME, whether a road's own branches can be
  joined — and re-profiles or refuses the line with its number instead of letting `ribbon.py`'s
  rim gate discover it at build step 4. MEASURED on seed `Fresh42`: step 4 held at 2 of 14 for
  three waves, five segments refused at the rim (T4 22 caused steps, T6 3, T7 7, T9 49, S13 1)
  and 60 of their 71 caused steps sat between two legs of one switchback; the refusal key
  `|y1 - y2| - BATTER_GRADE * (run1 + run2)` has no ground term, so it was a routing fact nobody
  asked. `route.fit_ladder` puts the line's own branches in the profile's constraint set, keyed on
  `route.SELF_ADJACENCY_DEPENDS_ON` (design height at both claiming feet, run past each claim's
  bench edge, one lattice edge of slide) over every 4-adjacent lattice pair the WRITER's own
  `ribbon.lateral_and_y_grid` hands to two feet of the line; the join is exact difference-
  constraint propagation, so an empty interval is a proof and the line is REFUSED with its two
  samples, stations, design heights and unabsorbable metres. On the plan step 4 ran against the key
  fires on exactly those five segments plus T8. Re-planned (after `SiteSurface`'s 18:47 site
  re-solve): 8 lines joined at plan time, 4 unchanged, 0 refused; `ribbon.py build --validate`,
  serial, takes S13 to rc 0 (3 of 14, from 2) and the rim's caused steps T6 3 → 2, T7 7 → 1,
  T4 22 → 15. Four `accept` roads cases, each proved by a flipped verdict: drop the check and the
  known-bad U returns at step 4 with 55 caused steps.

- `settlements/plan.py` no longer throws away a completed solve because its `--out` directory is
  absent. MEASURED 2026-09-23 driving seed `Fresh42`: the planner ran for 181.4 s, passed its own
  `emitscope.require_emission_scope` check on both documents, and then died in `write_text` with
  `FileNotFoundError` — every number lost, nothing on disk, exit 1. `--out` defaults to the
  package's own directory and so was never absent on the path anyone had run; a fresh seed's
  output directory is new BY DEFINITION. It now creates `--out` before emitting.

- `pipeline.py`'s VHPATCH1 scope read asks `roads/field.py::header()` and not `load()`. `load()`
  parses the whole file and THROWS THE HEADER AWAY (field.py:263 skips the seed hash, :265 skips
  the name), so a scope check built on it reported "carries no seed declaration" about a
  1,105,747,603-byte field whose first 4 KiB name `Fresh42` verbatim — MEASURED, and it refused
  a correct artefact rather than admitting a wrong one, which is the safe direction and still
  wrong.

- A jumpstart character no longer wakes up in the sea, and the two halves of the system that let
  it happen are now wired together. `tools/jumpstart/worlds/derive.py` copied the template's
  `spawn:` block verbatim out of `placements.yaml`'s `spawn_points:`, which the placement solver
  writes as a naive footprint-and-yaw offset with no terrain test at all, and nothing ever
  compared the shipped coordinate against the placement it was derived from. Measured on
  Ulfsland/`Pirate68`: the live template shipped `{x: -2187, y: 33, z: 2100}`, derived from a
  site that had since been re-solved 4.5 km away, on terrain of 26.74 - 3.26 m BELOW Valheim's
  water plane - and the operator swam. The coordinate the solver offered instead sampled 25.82,
  4.2 m under, so regenerating from it would have made the bug worse. `derive.py spawn` now
  chooses the coordinate itself: it probes Valheim's own `WorldGenerator` at 1 m through
  `run_patchscan.sh` with full pregeneration (so rivers and lakes are in the heights, which the
  8 m seedscan grid cannot see - it reports 30.17 at that same spawn), samples the INTEGER
  coordinate that actually gets written rather than a cell centre half a metre away, and accepts
  a point only if it clears the water plane by 2 m, holds 0.5 m of freeboard across a 4 m disc,
  sits 6-120 m outside the blueprint's yaw-rotated footprint, and is joined to that footprint by
  a metre-by-metre walk that never touches water and never steps more than 1.5 m. No provable
  point means `spawn: []` and a recorded refusal, never a least-bad wet guess. The evidence lands
  in a new generated `<preset>/spawn.yaml`, and `derive.py check` re-checks every point against
  the CURRENT `solved` blocks with no server and no grid: when a re-solve moves a base it names
  the preset, the spawn, where the base is now, how far it moved and the command to regenerate,
  and it HOLDS the template rather than publishing it. All 13 Ulfsland placements now have a
  spawn verified at +3.50 m to +78.85 m of freeboard, each re-confirmed by a second independent
  generator boot agreeing to 0.005 m.
- Sea level in `WorldGenerator.GetHeight` units is 30, not 0, and it is now written down where it
  is used. `site_finder.WATER_LEVEL` was 0.0 - the ocean-FLOOR clamp mistaken for the surface -
  which made `water_dist_m` measure the distance to 30-m-deep water and let 10 of 13 solved base
  sites sit below the waterline while recording a pass. Three independent measurements are
  recorded at `derive.SEA_LEVEL_M`: Swamp heights span 27.46..33.84 around a 29.84 median, Ocean
  cells clamp at 4.00 (= 0.02 x 200, `GetBiome`'s ocean threshold, i.e. the floor), and in the
  12,301-instance `ZoneSystem` dump every land location type floors at 30.50..31.11 because
  Valheim places land locations 1 m above the water while `ShipWreck01..04` sit at 29.0..30.9.

- A location a MOD places is named on the map again. Valheim 1.0 stores a location instance by
  prefab hash where 0.220 stored its name, so the map can only label what the prefab catalog can
  answer, and the catalog collected `*.dll` from the plugin roots plus the game's own two SoftRef
  manifests. A mod that adds locations indexes its bundles with a SoftRef manifest of its own,
  under its own name, and neither the plugin walk nor `CatalogFromFiles` would take it: More World
  Locations AIO calls its one `assetBundleManifest_full`. Measured on Ulfsland immediately after
  its re-roll onto seed `Pirate68`: 2,116 of 14,040 location instances, across 182 distinct prefab
  hashes, came back with an empty name and fell into the `other` category - every location that
  mod places, unlabelled. `worldintel.IsSoftRefManifest` now recognises a SoftRef manifest by what
  they all share, an extensionless name containing `manifest`, and `prefabCatalogPaths` collects
  them from the plugin roots under a budget of their own so a mod pack full of code cannot spend
  the budget that names the world. Re-running the analysis took unresolved locations to 0 and
  populated the `arena` and `shrine` categories for the first time on that world.

- Building a world's map tiles no longer kills the portal container. `maptiles.Build` held both
  12288x12288 map sources decoded in full plus a `float32` height and a 12-byte biome colour for
  every one of the 151 million pixels, and measured 6.73 GiB peak RSS on Ulfsland's real sources
  against the 1 GiB `mem_limit` `compose.yaml` sets. The kernel killed the portal mid-request
  twice on 2026-09-12 - `Memory cgroup out of memory: Killed process 1619955 (valheim-portal)
  total-vm:5093196kB, anon-rss:1010756kB`, with `dockerd` recording `exitCode=137
  restartCount=2` - leaving the analysis job row stuck at `queued`. `docker inspect` run after
  the restart policy had already replaced the container reported `ExitCode 0` and
  `OOMKilled false`, which describes the new instance and not the one that died; that is what
  made this look like a graceful 354s timeout when 354s was only how long the work took.
  The renderer now reads PNG scanlines incrementally and fills each zoom level's tile band from
  a single streaming pass, so its live set scales with the width of the map rather than its
  area: 150 MiB peak, faster than before, and all 770 tiles plus the manifest byte-identical to
  the previous renderer. Only Ulfsland could hit this, because the four older worlds already
  have terrain manifests on disk and take the reuse path that skips `Build` entirely.

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
- The VR sections of the player guide were re-derived from the shipped plugin sources and the
  shipped client config rather than from an earlier revision, and eight claims were corrected.
  The parry instruction was actively wrong: it told players to swing the weapon while blocking,
  which is the weapon-block rule, when a shield parry is a shove of the shield hand and the
  weapon-hand grip silently prevents one by routing the block timer to `WeaponBlock`, which zeroes
  its own parry while a shield is blocking. The guide now documents restored shield blocking and
  what `BlockingType = Realistic` asks of a player, the non-sticky two-handed release, the rebuilt
  wrist navigation - `CLOSE`/`BACK` and `<`/`>` sharing the leftmost footprint, the `Admin/Spawn`
  second level, and entries withheld for absent content - the left-grip anchor and which hulls
  carry an anchor fitting, boat-relative steering, and map zoom no longer dodging. Three stale
  claims went with them: the boat throttle is the **left** stick and the hover highlight the
  right, so there is no collision at a helm; the large map does have a zoom gesture; and
  MagicRevamp's unsummon key is `[Not Synced with Server]`, so this profile's `Keypad2` is what
  fires and the wrist strip's **Unsummon** - which sends the mod's `Left Shift` + `F` default -
  does not unsummon anything.

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
