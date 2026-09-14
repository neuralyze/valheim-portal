# Proposal: settle "profile" — one word, two things

Bead: `vhp-05s` — "Two different things are both called a profile".
Status: **proposal only.** No identifier, string, path, route or file was renamed to write
this. No code was changed.
Companion: `docs/proposed/2026-09-13-mod-set-provenance.md` (`vhp-b1m`). The two are
deliberately separate changes; see §7.

Counts below come from `git grep` at commit `d7dad13` on 2026-09-14 and are reproducible
with the commands shown. Anything marked **[INFERENCE]** is reasoning, not measurement.

---

## 1. What is true today

### 1.1 The two senses

| | **sense S — the editable mod set** | **sense P — the thing a player installs** |
|---|---|---|
| example values | `admin`, `flat`, `vr`, `ulfsland-dn` | `hrafnheim-vr-flat`, `hrafnheim-non-vr`, `doggerland-vr-flat-admin` |
| lives at | `<fleet>/profiles/<name>/` | a release row + artifacts in the portal DB, and a ZIP on a player's PC |
| who edits it | the operator, through `manage_mods.sh` / `/admin/mods` | nobody — it is built, published, immutable |
| cardinality | 7 today | 4 per world |
| owns | package pins, client/server config, package cache | a version, a `client_type`, an audience, checksums |
| authority file | `profiles/<n>/profile-manifest.json` | `profile-manifest.json` **inside the published ZIP** |

They are related by `release-targets.json`, which is the one place in the repo that is
already unambiguous:

```json
{ "world": "Hrafnheim", "source_profile": "flat",
  "published_profile": "hrafnheim-vr-flat", "valheim_vr": true, "audience": "player" }
```

### 1.2 The worst single collision: one filename, two incompatible schemas

`profile-manifest.json` names **both** files. 84 occurrences across 38 files.

```console
$ sudo -n python3 -c "import json;print(list(json.load(open('/media/big4/projects/game/valheim/profiles/admin/profile-manifest.json'))))"
['schema_version', 'profile_name', 'packages', 'client_only_packages',
 'manual_server_packages', 'excluded_packages', 'disabled_packages', 'custom_packages']
```

versus the published one (`internal/app/models.go:41-48`, `docs/release-format.md:40-49`):

```
schema, world, profile, client_type, packages, companion?
```

Different key for the schema version (`schema_version` vs `schema`), different key for the
name (`profile_name` vs `profile`), disjoint key sets otherwise. `profile_store.py:46`
defines the source name; `cmd/profile-definition-builder/main.go:644` writes the published
one; `cmd/valheim-profile-sync/sync.go:754` accepts exactly that entry name and nothing
else. A reader that finds `profile-manifest.json` cannot know which of the two it has
without looking inside.

### 1.3 Volume

```console
$ git grep -Iio profile -- . | wc -l          # 5061 occurrences
$ git grep -Iil profile -- . | wc -l          # 233 files
```

Classified by curating each file to a sense (the per-file lists are in §2.5):

| bucket | files | occurrences |
|---|---:|---:|
| sense P only (client, builder, releases, player surfaces) | 119 | 2144 |
| sense S only (mod store, mod admin, provisioning, hostops) | 46 | 1331 |
| both senses in one file | 31 | 1356 |
| unrelated (`LoadTimeProfiler`, `FrameProfiler`, pprof, Steam community profiles) | 37 | 230 |

138 distinct Go identifiers contain `Profile`/`profile`; 75 of them appear only in
sense-P-only packages.

### 1.4 There is already a winner, and it is written down

This is the finding that decides the bead. The repository **already** documents the
disambiguated vocabulary, in its own README:

```
README.md:578  ## Mod profiles and published editions
README.md:580  A **mod profile** is one shared definition, stored once at `<fleet>/profiles/<name>`…
README.md:599  A **published edition** is built from a profile rather than being one…
```

and in the operations guide:

```
docs/operations.md:110  ### The four published editions
docs/operations.md:332  The shared profiles and the published edition names are deliberately separate
docs/operations.md:174  A profile no server runs is normally an **edition source** rather than a mistake
```

`edition` already appears **162 times across 37 files**, including in code:
`tools/valheim_mods.py:696` (`manifest = profile_store.manifest_path(edition, store)`),
`internal/app/profile_cards.go:99,150,164,236,238`,
`cmd/profile-definition-builder/main.go`, `hostops/tests/agent_argv_contract.sh`.

The bead's *description* (17 Aug 23:47) suggests the opposite — keep "profile" for what
players install, call the editable one a "mod set". The bead's *notes* (17 Aug 23:53, and
the `vhp-b1m` operator direction of 18 Aug) reverse it: "'profile' should mean that shared
editable mod set, and the thing players install needs the distinct name". The notes are
newer, they match the operator direction, **and they match what the prose already says.**
The description is stale. §4 records this as a confirmation the operator should give
explicitly, but it is a confirmation, not an open question.

### 1.5 What cannot be renamed at all

There is no self-update path in the installed Windows client, and sync runs *before* the
game launches. A definition an installed client cannot parse does not pin a player to an
old build — it **stops them playing Valheim**. `docs/release-format.md:52-66` records the
2026-08-17 incident where adding one field named `audience` broke every install with
`decode profile definition: json: unknown field "audience"`.

So the following are **frozen**, permanently, regardless of which vocabulary wins:

| frozen thing | defined at | enforced by |
|---|---|---|
| ZIP entry name `profile-manifest.json` | `cmd/profile-definition-builder/main.go:644` | `cmd/valheim-profile-sync/sync.go:754`, archive allowlist `sync.go:696-712` |
| manifest keys `schema`,`world`,`profile`,`client_type`,`packages`,`companion` | `internal/app/models.go:41-48` | `DisallowUnknownFields`, `sync.go:715-718` |
| `schema == 1` by equality | `cmd/valheim-profile-sync/sync.go:663` (`definition.Schema != 1`), and `:526` for the local state | the version number is **not** an escape hatch. Note `docs/release-format.md:60` still cites the pre-move `sync.go:622`; that citation is stale and worth fixing in step 5 |
| device-auth request body `{"world","profile","client_type"}` | `cmd/valheim-profile-sync/portal.go:51-55` | installed clients send it |
| client manifest response keys `profile`, `profile_sha256`, `profile_size` | `internal/app/device_auth.go:553-560`, `portal.go:80-86` | installed clients decode it |
| local state file keys (`profile`, `profile_sha256`, …) | `cmd/valheim-profile-sync/sync.go:57-75` | existing installs read their own state |
| URL protocol scheme `valheim-profile-sync:` | `cmd/valheim-profile-sync/request.go:16` | registered in `HKCU\Software\Classes\`, baked into every Desktop `.url` |
| installed paths `%LOCALAPPDATA%\Programs\ValheimProfileSync`, `%LOCALAPPDATA%\ValheimProfileSync\profiles\` | `install.go:23,43`, `profile_storage.go:23-27`, `installed_profile.go:58` | already on players' disks |
| executable name `ValheimProfileSync.exe` | `install.go:23`, `internal/app/config.go:113`, `server.go:330` | code-signed; see §4.2 |

URL **path segments** are not frozen: `/client/manifest/{world}/{profile}/{clientType}`
(`internal/app/server.go:325-334`) is positional, and the client builds the URL from
values, never from the segment's Go name. Renaming the `{profile}` wildcard to `{edition}`
changes nothing on the wire. That matters a lot for how much of §3 is actually reachable.

---

## 2. Blast radius, enumerated

### 2.1 URL routes (all sense P; all safe to rename — positional)

```
internal/app/server.go:325  GET  /client/manifest/{world}/{profile}/{clientType}
internal/app/server.go:326  GET  /client/payload/{world}/{profile}/{clientType}
internal/app/server.go:327  GET  /client/runtime/{world}/{profile}/{clientType}
internal/app/server.go:328  GET  /client/companion/{world}/{profile}/{clientType}
internal/app/server.go:329  GET  /client/diagnostics-plugin/{world}/{profile}/{clientType}
internal/app/server.go:331  POST /client/diagnostics/{world}/{profile}/{clientType}
internal/app/server.go:334  POST /client/exploration/{world}/{profile}/{clientType}
internal/app/server.go:392  POST /admin/worlds/{world}/profiles/{profile}/debug-logging
internal/app/server.go:230  GET  /assets/admin-profile-autofill.js
internal/app/server.go:330  GET  /client/ValheimProfileSync.exe          <- FROZEN (§1.5)
```

`server.go:2244` renders `/admin/mods?world={{.World}}&profile={{.Profile}}` — and that
one is **sense S**: it is the mod-admin page for the world's linked mod set
(`internal/app/mod_admin.go:16`). Two `profile` query/path parameters in the same admin UI
meaning different things is the collision in its most user-visible form.

### 2.2 JSON keys

Sense P, frozen (§1.5): `cmd/valheim-profile-sync/sync.go:51,60,67,68`,
`portal.go:53,82,85,86,103`, `settings_merge.go:90,121`, `runtime_overlay.go:33`,
`cmd/profile-definition-builder/main.go:64`, `config_authority.go:93`,
`internal/app/models.go:47`, `device_auth.go:173`, `server.go:584`, `diagnostics.go:21`.

Sense P, **not** frozen (portal-internal / agent bridge):
`internal/app/agent_chat.go:559,565`, `agentclient.go:30,59`, `internal/agent/agent.go:52,85`
— these carry `profile` (sense S, the mod set the verb edits) *and* `published_profile`
(sense P) side by side in the same struct, which is the clearest existing proof the two
senses cannot share a word.

Sense S: `internal/app/mod_admin.go:16`, `provision.go:28`, plus every
`profile-manifest.json` key on the store side (`schema_version`, `profile_name`).

Already disambiguated: `"source_profile"` (27 occurrences, 12 files) and
`"published_profile"` (37 occurrences, 18 files) in `release-targets.json`,
`deploy/release-targets.json.example`, `hostops/portal_publish_profile.sh:54-65`,
`scripts/republish-profiles.sh`, `hostops/tests/agent_argv_contract.sh:68-109`,
`tools/valheim_mods.py:232`, `tools/test_valheim_config_schema.py:243`.

### 2.3 Database columns (sense P except where noted)

```
internal/app/store.go:142  releases.profile              TEXT NOT NULL      (P)
internal/app/store.go:163  CREATE TABLE profile_settings                    (P — debug-logging per world/edition)
internal/app/store.go:165  profile_settings.profile      TEXT NOT NULL      (P)
internal/app/store.go:169  PRIMARY KEY(world, profile)                      (P)
internal/app/store.go:247  agent_verb_calls.profile      TEXT DEFAULT ''    (S — the mod set a verb edits)
internal/app/store.go:269  agent_verb_calls.published_profile TEXT          (P — already disambiguated)
internal/app/store.go:475  releases_profile_v5 / artifacts_profile_v5       (migration table names)
internal/app/store.go:483  artifacts.kind CHECK(kind='profile')             (P — artifact kind literal,
internal/app/store.go:603  same CHECK in the current table                      a value, not a column name)
```

`agent_verb_calls` holding both `profile` (S) and `published_profile` (P) is the same
collision at the schema level.

### 2.4 CLI surface

Sense S: `manage_mods.sh --profile` and the `profile` subcommand tree
(`tools/valheim_mods.py:1546`, `:1573-1576`; `hostops/manage_mods.sh:45,65,100-132`);
`tools/profile_store.py:225` `--profiles-root`; `tools/migrate_profiles.py:316,325,328`;
`tools/valheim_provision.py:432,435`.

Sense P: `profile-definition-builder -profile -source-manifest -client-type -audience`
(`docs/command-reference.md:40`); `scripts/build-profile-definition.sh <WORLD>
<published-profile> <client-type>`; `seed-release --profile`; `VALHEIM_PROFILE_SOURCE_ROOT`
(**mixed**: names the root that holds *sense S* profiles but exists only to serve the
*sense P* publish, `scripts/republish-profiles.sh:29,44`).

### 2.5 Files, by sense

**Sense P only (119 files, 2144 occurrences).** `cmd/valheim-profile-sync/**` (39),
`cmd/profile-definition-builder/**` (5), `cmd/seed-release/**` (2),
`internal/profilecfg/**` (2); `internal/app/{profile_cards,device_auth,diagnostics,
exploration,debug_release,flat_release,player_home,profile_catalog,config_authority,
client_artifact,diag_plugin,artifact_names,models,player_guide,steam_auth,error_page}.go`
+ their tests; `internal/app/assets/{player-guide.md,site.css,admin-profile-autofill.js}`;
`scripts/{republish-profiles,build-profile-definition,build-flat-release-plan,
publish-flat-release-plan,build-windows-client,sign-windows-client,
make-selfsigned-signing-cert,mount-windows,install-portal,build-valheimvr}.sh`;
`hostops/portal_publish_profile.sh`; `docs/{release-format,client-install,code-signing,
defender-false-positive,public-distribution,valheimvr-packaging,prerequisites,
valheimvr-feature-roadmap}.md`; `.github/workflows/release-client.yml`;
`deploy/release-targets.json.example`.

**Sense S only (46 files, 1331 occurrences).** `tools/{profile_store,migrate_profiles,
valheim_profile_catalog,valheim_mods,valheim_provision,settings_history,
valheim_config_schema,config_merge,portal_paths}.py` + their tests;
`hostops/{manage_mods,portal_profile_catalog,portal_mod_admin,provision_valheim_server,
portal_admin_mode,capture_valheim_diagnostics}.sh` + `hostops/tests/*`;
`internal/app/{mod_admin,provision,admin_mode,mod_catalog,config_manager}.go` + tests;
`deploy/profiles/{admin,flat,vr}/profile-manifest.json`;
`docs/{mod-onboarding,mod-decisions,mod-compatibility-register}.md`.

**Both senses in one file (31 files, 1356 occurrences).** These are where the executor
must read every line rather than substitute:

```
165 internal/app/server.go        150 internal/app/store.go       121 docs/operations.md
 85 README.md                      56 internal/agent/agent.go      54 docs/script-reference.md
 53 internal/app/server_test.go    53 internal/app/store_test.go   51 docs/command-reference.md
 47 internal/app/agent_chat.go     43 CHANGELOG.md                 40 internal/agent/agent_surface_test.go
 29 docs/architecture.md           27 internal/app/verbs.go        25 internal/app/agent_chat_test.go
 23 internal/app/agent_chat_store.go  23 internal/app/agentclient.go
 21 hostops/tests/agent_argv_contract.sh  20 docs/agent-harness.md 16 policy.yaml
 15 CLAUDE.md                      11 docs/deployment-layout.md    11 docs/repository-layout.md
 11 internal/agent/agent_test.go   10 docs/development.md          10 docs/threat-model.md
  4 internal/app/agent_auto_approve_test.go  4 internal/app/verb_arguments_test.go
  3 internal/app/config.go          2 hostops/start_valheim_server.sh
173 .beads/issues.jsonl            (history; never rewrite)
```

**Unrelated (37 files, 230 occurrences) — must not be touched.** `tools/vrfixes/*.cs`
(`FrameProfiler`, `GameMethodProfiler`, `HookProfiler`, `InventoryProfiler`),
`sighsorry-LoadTimeProfiler` (a Thunderstore package name, 25 occurrences in 13 files),
`internal/app/steam_persona.go` (`https://steamcommunity.com/profiles/`),
`internal/runner/*` (pprof), `tools/vr_perf_ingest.py`, `docs/vr-impact-scan.md`.
A naive `s/profile/edition/` breaks the mod name and the Steam URL. Any executor doing a
mechanical pass **must** exclude these by path first.

### 2.6 Doc headings

```
README.md:578            ## Mod profiles and published editions      (already correct)
README.md:618            ## Profile releases                         (P — becomes "Editions and releases")
docs/operations.md:70    ## Mod profiles                             (S — correct)
docs/operations.md:110   ### The four published editions             (P — already correct)
docs/operations.md:180   ### A profile owns its server settings      (S — correct)
docs/operations.md:276   ### Seeding a profile store from this repository  (S — correct)
docs/operations.md:346   ## Publish a profile                        (P — becomes "Publish an edition")
docs/architecture.md:27  ## Profile delivery                         (P)
docs/release-format.md:1 # Profile release format                    (P — see §4.3)
docs/client-install.md:1 # Valheim Profile Sync for Windows          (FROZEN product name)
docs/client-install.md:9 ## Install or update a profile              (P — player-facing, see §4.2)
docs/valheimvr-packaging.md:214  ## Profile artifact mapping         (P)
docs/valheim-vr-knowledge.md:451 ### Profile sync semantics          (P)
docs/mod-onboarding.md:54        ### 3. Install into a probe profile (S — correct)
```

### 2.7 Player-visible strings (sense P; see §4.2 before touching any of them)

`internal/app/profile_cards.go:47` `"Profile unavailable"`;
`internal/app/assets/player-guide.md` — 42 occurrences, e.g. `:38-40`, `:1097`, `:1476`,
all meaning the installed edition; `internal/app/device_auth.go:53,80,81,97,129,133,140,307,310,312`
(sign-in pages, all saying "Valheim Profile Sync");
`cmd/valheim-profile-sync/installed_profile.go:13` `"No profile installed yet"`;
`launch.go:25,32,35,43,158,206,240,283,315,330`; `main.go:50,99,125,290`;
`main_windows.go:57,61,62,63`; and the Desktop shortcut filenames
`"<World> - <Profile>.url"` and
`"Valheim Profile Sync - <World> - <Profile> - <ClientType>.url"` (`main.go:213-214`).

### 2.8 The guard that must exist afterwards

`tools/check_agent_policy.py` (238 lines) already does prose-vs-code comparison and is
wired into `scripts/check.sh:26` and `.github/workflows/ci.yml:196`. It is the right place
for the terminology guard the bead asks for. It currently checks only `policy.yaml` vs
`docs/agent-harness.md` vs `internal/app/verbs.go`.

---

## 3. The proposed shape

**`profile` means the shared editable mod set (sense S). The thing a player installs is an
`edition`.** `release` keeps its existing meaning: a *versioned publication* of an edition
for one world and client type. So `hrafnheim-vr-flat` is an edition; `hrafnheim-vr-flat
2.2.24` is a release of it.

Reasoning, in order of weight:

1. **It is already the documented vocabulary** (§1.4). Choosing the other way means
   rewriting `README.md:578-615`, `docs/operations.md:110-176`, `release-targets.json`'s
   key names and 162 existing uses of `edition` — i.e. the "cheaper" option is the more
   expensive one.
2. **It matches the operator direction** recorded on both beads.
3. **It puts the renaming cost where renaming is possible.** Sense S lives entirely on our
   own disk and in our own code. Sense P's core vocabulary is frozen by installed clients
   (§1.5) and cannot be renamed at all, so a rule that says "sense P owns the word
   `profile`" would be unenforceable in exactly the places it matters most: the manifest,
   the state file, the protocol scheme.
4. **`release` is already load-bearing for sense P** — `Release`, `ReleaseStatus`,
   `CurrentRelease`, `release-targets.json`, `seed-release`, `release-confirm`,
   `docs/release-format.md`. The word for the *channel* is the only thing missing, and
   `edition` is already in use for it.

### The rule, in one line

> `profile` = a mod set in `<fleet>/profiles/`. `edition` = a published client build.
> `release` = one published version of an edition. Where the wire says `profile` and means
> an edition, that is a frozen legacy spelling with a named exception.

### What changes

- **Go**: `Release.Profile` → `Release.Edition`; `debugProfileView` → `debugEditionView`;
  `profileKind` → `editionKind`; `publishedProfileArtifact` → `publishedEditionArtifact`;
  `AgentClient.PublishedProfile` → `.Edition`; route wildcards `{profile}` → `{edition}` on
  the seven `/client/...` routes and the debug-logging route. Roughly 75 of the 138
  identifiers, all in sense-P-only packages.
- **DB**: `releases.profile` → `releases.edition`; `profile_settings` →
  `edition_settings(world, edition)`; `agent_verb_calls.published_profile` → `edition`.
  Additive-then-swap migration, same shape as the existing `releases_profile_v5` migration
  at `store.go:475-503`.
- **Docs/CLI**: headings and flag help in §2.4/§2.6 marked (P).
- **`VALHEIM_PROFILE_SOURCE_ROOT`** stays — it genuinely names the sense-S root.
- **Guard**: extend `tools/check_agent_policy.py` with a vocabulary check (§3, below).

### What this explicitly does NOT change

- **Nothing in §1.5.** Not the ZIP entry name, not a manifest key, not `schema`, not the
  protocol scheme, not `%LOCALAPPDATA%` paths, not the executable name, not the response
  JSON. Every one of those keeps the word `profile` meaning an edition, forever.
- **No on-disk fleet paths.** `<fleet>/profiles/<name>/` and `.active-mod-profile` are
  sense S and already correct. (`policy.yaml:263` still names the pre-fold
  `<World>/mods/profiles` and is simply stale — fix it in passing, it is not a rename.)
- **Not the source-side `profile-manifest.json` filename** — see §4.3, which is a real
  decision and the only place I recommend an on-disk rename.
- **No player-visible string** without §4.2 answered.
- **Not `.beads/issues.jsonl`, `CHANGELOG.md`, or any historical record.** They describe
  what was true when written.
- **Nothing in §2.5's unrelated bucket.**
- **No behaviour.** Every step below is name-only; any diff that changes what bytes are
  served is out of scope and should be rejected in review.

### The guard (extends `tools/check_agent_policy.py`)

Three assertions, cheap and non-clever:

1. **An allowlist of frozen `profile` spellings** — the §1.5 table, expressed as
   `(path, literal)` pairs. Present ⇒ pass. Missing ⇒ fail loudly: somebody renamed a wire
   token and is about to lock every player out of Valheim. This is the more valuable half
   of the guard and is worth shipping on its own, even if §4 is never answered.
2. **A denylist in sense-S-only files**: `published_profile`, `PublishedProfile`,
   `client_type`, `release_id` must not appear in `tools/profile_store.py`,
   `tools/valheim_profile_catalog.py`, `hostops/manage_mods.sh`.
3. **A denylist in sense-P-only files**: the bare identifier `Profile` must not be
   reintroduced in `internal/app/profile_cards.go`, `internal/app/debug_release.go`,
   `cmd/profile-definition-builder/` outside the frozen allowlist.

---

## 4. Decisions this plan cannot make — the operator must choose

### 4.1 Confirm the direction (low stakes, but it must be said out loud)

The bead *description* says keep "profile" for players and rename the editable one "mod
set"; the bead *notes*, the `vhp-b1m` operator direction, `README.md:578` and
`docs/operations.md:110` all say the opposite. §3 follows the latter.
**Needed: "yes, profile = mod set, edition = the player download."** If the answer is the
other way, §3 inverts and roughly triples in size, and §1.5 becomes a permanent
contradiction — please read §3's reason 3 before choosing it.

### 4.2 How far does the rename go into player-facing surfaces? **A / B / C**

The Windows client is *named* "Valheim Profile Sync". Its executable
`ValheimProfileSync.exe` is code-signed (`scripts/sign-windows-client.sh`,
`docs/code-signing.md`) and has accumulated SmartScreen/Defender reputation that
`docs/defender-false-positive.md` exists because of. Its URL protocol is
`valheim-profile-sync:`. Every player's Desktop holds `<World> - <Profile>.url`
(`main.go:213`).

- **A — vocabulary only in operator surfaces.** Admin UI, docs, code, CLI. Player-guide,
  card copy, client window text and the product name all keep saying "profile".
  Cost: ~0 player risk. Cost: the word stays ambiguous in the 42 player-guide occurrences.
  **[INFERENCE]** players never read the admin UI, so the ambiguity that actually costs
  the operator time is fully resolved by A.
- **B — A, plus player *copy*.** Change card and guide wording to "edition"; keep the
  product name, the exe, the protocol and the shortcut filenames. Cost: a documentation
  pass and a re-publish so the shipped guide matches; **no installed client breaks**.
- **C — B, plus rename the product.** New exe name, new protocol scheme, new shortcut
  names, re-sign, reputation reset, every existing Desktop shortcut dead. **I recommend
  against C.** It trades a live player-facing breakage for a word.

**Recommendation: B, but A is a perfectly good stopping point** and A is what the migration
in §5 delivers by step 5; B is step 7 and is independently revertible.

### 4.3 Rename the *source* manifest file? (the §1.2 collision)

`profiles/<n>/profile-manifest.json` (sense S) and the ZIP entry `profile-manifest.json`
(sense P) share a filename and share nothing else. The published one cannot be renamed
(§1.5). The source one can: it has 7 instances on disk plus 4 `.bak-*` siblings, and
~50 references in code and docs, all ours.

Renaming it to `mod-profile.json` would make "which manifest is this?" answerable by name.
It costs: `tools/profile_store.py:46` + every reader in §2.5 sense-S, a compat read for one
release, and a one-shot `git mv` inside `<fleet>/settings-history` so history follows
(`settings_history.py:159` tracks it as `profiles/<name>/profile-manifest.json`).

**I lean yes, and I am not confident.** Against it: the file has been that name since the
fleet existed, it appears in operator muscle memory and in `deploy/profiles/*/`, and a
rename during the same week as a world-format migration is more churn than the confusion
costs. **Needed: an explicit yes/no.** §5 places it last precisely so it can be dropped.

### 4.4 Is a DB column rename worth its migration?

`releases.profile` → `releases.edition` and `profile_settings` → `edition_settings` require
a table rebuild, which this codebase already knows how to do (`store.go:475-503`). The
portal is a single deployment with one database at
`/var/lib/valheim-portal/portal.sqlite`. Risk is low but non-zero, and the payoff is
internal clarity only — nobody outside the process reads those columns.
**[INFERENCE]** worth doing, because leaving `releases.profile` next to a Go field named
`Edition` is exactly the drift the guard is meant to prevent. But it is the one step with a
data-loss failure mode, so the operator should say yes before step 6 runs.

---

## 5. Migration path — ordered, each step independently revertible

Nothing here touches the running fleet, stops a world, or writes outside the repository.
The portal restarts once, at step 6.

### Step 0 — write the vocabulary down first (docs only)

Add a **Vocabulary** section to `README.md` immediately before `## Mod profiles and
published editions`, stating the §3 one-line rule and pointing at §1.5's frozen list.
Nothing else in this plan is safe to start before the rule is quotable in review.
Revert: delete the section.

### Step 1 — ship the frozen-spelling guard alone

Extend `tools/check_agent_policy.py` with assertion (1) only: the §1.5 allowlist must be
present, verbatim, in the named files. This lands **before** any rename, so that every
later step is protected from the moment it starts.
Verify: `python3 tools/check_agent_policy.py` passes now; deliberately change
`sync.go:754`'s literal in a scratch checkout and confirm it fails.
Revert: remove the assertion.

### Step 2 — rename inside sense-P-only Go packages (no wire, no DB)

`cmd/valheim-profile-sync/` (excluding the §1.5 literals and JSON tags),
`cmd/profile-definition-builder/`, `cmd/seed-release/`, `internal/profilecfg/`, and the
sense-P-only files in `internal/app/`. Identifiers, local variables, comments, test names.
**Struct field names may change; `json:"…"` tags may not.**

This is the one step where an AST codemod is appropriate — but only after excluding §2.5's
unrelated bucket by path, or `LoadTimeProfiler` and `steamcommunity.com/profiles/` become
casualties.
Verify: `go build ./... && go test ./cmd/... ./internal/profilecfg/`.
Revert: `git revert` — it is a self-contained, behaviour-free commit.

### Step 3 — rename the route wildcards

`{profile}` → `{edition}` on `server.go:325-334` and `:392`, and the matching
`r.PathValue(...)` calls in `device_auth.go:525`, `diagnostics.go`, `exploration.go`,
`server.go:1506-1507`. Positional on the wire (§1.5).
Verify: `go test ./internal/app/` plus an actual request —
`curl -sS localhost:<port>/client/manifest/Hrafnheim/hrafnheim-non-vr/flat` returns the same
status and body it did before the change.
Revert: `git revert`.

### Step 4 — split the two `profile` parameters in the admin UI

`server.go:2244` (`/admin/mods?world=…&profile=…`, sense S) stays `profile`;
`server.go:2255` (`/admin/worlds/{{.World}}/profiles/{{.Profile}}/debug-logging`, sense P)
becomes `.../editions/{{.Edition}}/debug-logging`. These are admin-only, session-authenticated
pages with no external callers; an open tab gets a 404 and a refresh fixes it.
Verify: click both controls in `/admin` and confirm the mod page still loads and the
debug-logging toggle still republishes.
Revert: `git revert`.

### Step 5 — docs, CLI help, admin copy (delivers decision 4.2-A)

§2.4 and §2.6 lists. Leave `.beads/issues.jsonl` and `CHANGELOG.md` alone.
Add assertions (2) and (3) of the guard here, once the files they police are clean.
Verify: `python3 tools/check_agent_policy.py`; read `docs/operations.md:70-180` end to end
and confirm no sentence needs a qualifier to be unambiguous.
Revert: `git revert`.

### Step 6 — the database rename (blocked on decision 4.4; portal restarts)

Same pattern as `store.go:475-503`: create `releases_edition_v<n>` / `edition_settings`,
copy rows, drop, rename, record the migration version. Back up first:

```sh
sudo -n sqlite3 /var/lib/valheim-portal/portal.sqlite \
  ".backup '/var/lib/valheim-portal/portal.sqlite.pre-edition-rename'"
```

Verify: after restart, `/admin` lists the same releases with the same versions, a player
download still resolves, and
`sudo -n sqlite3 …/portal.sqlite 'select count(*) from releases'` matches the pre-migration
count.
Revert: stop the portal, restore the `.pre-edition-rename` copy, redeploy the previous
binary. **This is the only irreversible-if-botched step; take the backup.**

### Step 7 — player-facing copy (only if decision 4.2 = B)

`internal/app/assets/player-guide.md`, `profile_cards.go` card copy,
`cmd/valheim-profile-sync` window text. **Not** the product name, exe, protocol or shortcut
filenames. Requires a republish so the shipped guide matches what players see.
Verify: `internal/app/player_guide_test.go` and `player_ux_test.go` pass; open the player
home page and read one card end to end.
Revert: `git revert` and republish.

### Step 8 — the source manifest filename (only if decision 4.3 = yes; worlds may stay up)

Change `profile_store.MANIFEST_NAME`, add a one-release compat read (`mod-profile.json`
preferred, `profile-manifest.json` accepted with a deprecation line), then on the host:

```sh
V=/media/big4/projects/game/valheim
for p in $V/profiles/*/; do sudo -n mv "$p/profile-manifest.json" "$p/mod-profile.json"; done
sudo -n git -C $V/settings-history add -A && \
  sudo -n git -C $V/settings-history -c user.name=operator -c user.email=operator@neuralyze.com \
    commit -m 'rename profile-manifest.json to mod-profile.json'
```

No world needs stopping: nothing reads the manifest at runtime — it is consulted by
`manage_mods.sh`, the catalog and the publish path, none of which run during play.
Verify: `manage_mods.sh <world> list` and `profile list` both still work against the
renamed files with the compat read *removed*.
Revert: `mv` back; the compat read makes both names work throughout.

---

## 6. What would prove it worked

1. **No installed client broke.** The decisive check, and it must be run against a real
   install, not a test: after steps 2–4, run the deployed `ValheimProfileSync.exe` against
   the portal for one published edition and confirm it syncs and launches. Any rename that
   reaches the wire fails here and nowhere else.
2. **Byte-identical published output.** Build the same edition before and after steps 2–5
   with `scripts/build-profile-definition.sh` and identical inputs; `sha256sum` the two
   ZIPs. **Equal.** This is a name-only change; a different artifact means behaviour moved.
3. **The guard fails when it should.** In a scratch checkout, change
   `cmd/valheim-profile-sync/sync.go:754`'s `"profile-manifest.json"` literal to
   `"mod-set-manifest.json"` and confirm `python3 tools/check_agent_policy.py` exits
   non-zero naming that file. A guard never observed failing is not a guard.
4. **The ambiguity is actually gone, measured.** After step 5:
   `git grep -Iin "profile" -- docs/ README.md | grep -v -f frozen-allowlist.txt` and read
   every remaining hit. Each must be sense S. Today that same command returns hits in both
   senses in 31 files (§2.5); the acceptance bar is **zero sense-P hits outside the frozen
   allowlist**.
5. **The unrelated bucket is untouched.**
   `git diff --stat <base>..HEAD -- tools/vrfixes/ internal/app/steam_persona.go internal/runner/`
   is empty, and `git grep -c "LoadTimeProfiler"` still returns 25 across 13 files.
6. **The admin flow still works end to end.** Open `/admin/mods?world=Hrafnheim&profile=admin`,
   toggle debug logging on a published edition, and confirm a new release is created with a
   bumped patch version — that path crosses both senses in one request and is where a
   half-done rename would surface.

---

## 7. Risks and rejected alternatives

### Risk 1 — a mechanical rename reaches a frozen literal, and every player is locked out

This is not hypothetical: `docs/release-format.md:52-66` records it happening from a
*single added field*. Mitigations, in order: step 1 ships the allowlist guard **before any
rename**; step 2 forbids touching `json:"…"` tags; check 6.1 exercises a real client; the
frozen surface is enumerated once, in §1.5, so there is one list to keep right.

### Risk 2 — the codemod eats `LoadTimeProfiler`

`sighsorry-LoadTimeProfiler` is a Thunderstore package pinned in every profile manifest.
Renaming it in code would produce a manifest that no longer matches a real package.
Mitigation: path exclusion before any pattern runs, plus check 6.5.

### Risk 3 — half a rename is worse than none

A tree where `Release.Edition` sits beside `releases.profile` beside `"published_profile"`
is harder to read than today's consistent ambiguity. Mitigation: steps 2–5 are one
reviewable sequence with a single reviewer, and the guard's assertions (2) and (3) land in
step 5 so the boundary is machine-checked from then on. If the operator answers 4.4 with
"no", the guard's allowlist must be extended to declare `releases.profile` a *frozen*
spelling too, so the inconsistency is deliberate and documented rather than drift.

### Rejected: keep "profile" for the player-facing thing and call the mod set a "mod set"

The bead description's original suggestion, and superficially the cheapest — it leaves the
frozen surface, the product name, the protocol and the player guide all consistent, and
renames only 46 sense-S files.

Rejected because it contradicts the operator direction on both beads, it contradicts
`README.md:578` and `docs/operations.md:110-176` which are already written the other way,
it strands 162 existing uses of `edition`, and it requires renaming the on-disk
`<fleet>/profiles/` root and `.active-mod-profile` on a live fleet in the same week as a
world-format migration. It also produces a worse steady state: `release-targets.json` would
have to become `mod_set` / `profile`, inverting a file that is currently the clearest thing
in the repo.

### Rejected: add qualifiers everywhere, rename nothing ("source profile" / "published profile")

Genuinely the cheapest — `source_profile` and `published_profile` already exist and already
work, in 64 occurrences across 26 files. Doing nothing but spreading that convention would
remove most real ambiguity for zero risk.

Rejected as the *whole* answer for two reasons. First, it does not survive: the bare word
`profile` still appears 5,061 times, so the qualifier is a convention that decays the moment
someone writes `profile` alone — which is exactly the drift the bead asks to make impossible,
and a guard cannot enforce "always qualify" without banning the bare word, which is the
rename by another route. Second, it leaves `profile-manifest.json` naming two incompatible
schemas (§1.2).

It *is* however the correct fallback if the operator declines §4: ship step 0 (write the
rule down) and step 1 (the frozen guard), spread the `source_`/`published_` qualifiers, and
stop. That is a real improvement and costs one afternoon.

### Rejected: bump the manifest `schema` to 2 and rename keys in a v2 wire format

`cmd/valheim-profile-sync/sync.go:663` tests `definition.Schema != 1` **by equality**, so every
installed client rejects a `2`. There is no self-update path. A v2 format can only ship to
clients that do not exist yet, and it would not retire v1 for years.
`docs/release-format.md:60-62` says this explicitly: "The version number is not an escape
hatch."

### Why this is a separate change from `vhp-b1m`

`vhp-b1m`'s plan moves bytes on a live fleet; this one moves names in a repository. Landing
them together means that when something breaks, the first question — "did the data move or
did the name move?" — has no cheap answer. The only ordering constraint between them is
that both touch `docs/operations.md`; whichever lands second rebases.
