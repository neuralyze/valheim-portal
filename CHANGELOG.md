# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
