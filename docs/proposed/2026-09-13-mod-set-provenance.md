# Proposal: provenance for a deployed mod set, and the duplication behind it

Bead: `vhp-b1m` — "A world's mod set is a 2 GB copy with no link to where it came from".
Status: **proposal only.** Nothing in this document has been implemented. No code was
changed to write it.
Author context: written 2026-09-13/14, the night five worlds were migrated to Valheim
1.0.12 and three of them were destroyed by a build mismatch and restored from backup.

Everything under "What is true today" was measured on this host on 2026-09-14 with the
commands shown. Everything marked **[INFERENCE]** is reasoning, not measurement.

---

## 0. The bead has already been half-answered, and the remainder is not what the title says

The bead was filed on 17 Aug against a fleet where each world held its own
`<world>/mods/profiles/<name>/` copy. That is no longer the shape. The fold described in
the bead's own `PROGRESS 18 Aug` note has run:

```console
$ sudo -n ls /media/big4/projects/game/valheim/profiles/
admin  flat  ulfsland-admin  ulfsland-dn  ulfsland-flat  ulfsland-vr  vr
$ sudo -n cat /media/big4/projects/game/valheim/{Hrafnheim,Doggerland,Storgard,Vangard,Ulfsland}/mods/.active-mod-profile
admin admin admin admin ulfsland-dn
```

Every "STILL TO DO" item in the bead note is also landed — `valheim_mods.py:11`,
`valheim_profile_catalog.py:15` and `valheim_provision.py:16` all import `profile_store`;
`hostops/manage_mods.sh:24` enumerates `$WORLDS_ROOT/profiles`; the server-level override
merge exists at `tools/valheim_mods.py:1249-1268` (`OVERRIDE_DIR = 'overrides'`, line 1208).

So the *storage* half of the bead is done. **What is still open is exactly the half the
title names: provenance.** Three concrete gaps remain, and the third is the one that cost
three saves tonight:

1. A profile's package cache is still a private 3.7 GB copy per profile — 7 profiles,
   **23.44 GB of byte-identical duplication** (§1.2).
2. Nothing on disk records that world *W* is running profile *P* at manifest revision *R*
   built against game build *B*. `cmd_deploy` prints `deployed=true`
   (`tools/valheim_mods.py:1360`) and writes no stamp (§1.3).
3. 36.81 GB of the fleet is seventeen copies of one identical 2.17 GB vanilla game tree,
   three of them per world, and **the one that executes is the least obvious**. A
   divergence between them booted a 0.221.12 binary against a 1.0 mod set and overwrote
   three saves (§1.1).

`require_matching_game_build` (`hostops/lib/common.sh:435`, landed tonight) closes the
*start-time* symptom of (3). It proves the three game copies agree at the moment a world
starts. It does not record what mod set is deployed, nor which game build that mod set
was deployed against, so it cannot tell you "this world's plugins were built for a
different binary than the one you are about to run".

---

## 1. What is true today, measured

Fleet root: `/media/big4/projects/game/valheim` — ext4 on `/dev/sdg1`, 7.3 T, 60 % used,
2.8 T free. **ext4: no reflink.** `cp --reflink` and filesystem-level dedup are not
available; hardlinks are the only sharing primitive. This constrains §3 heavily.

```console
$ findmnt -no SOURCE,FSTYPE,OPTIONS /media/big4
/dev/sdg1 ext4   rw,relatime
$ sudo -n du -sh /media/big4/projects/game/valheim
170G	/media/big4/projects/game/valheim
```

### 1.1 The vanilla game tree exists seventeen times

Each world holds three copies of the dedicated-server install, and `data/` is bind-mounted
whole into the container:

```console
$ sudo -n docker inspect valheim-server-Ulfsland \
    --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}} rw={{.RW}}{{"\n"}}{{end}}'
bind /media/big4/projects/game/valheim/Ulfsland/config_merged -> /config rw=true
bind /media/big4/projects/game/valheim/Ulfsland/data -> /opt/valheim rw=true
```

Structural fingerprint of each copy (`find -type f -printf '%P %s\n' | sort | md5sum`):

| world | `data/server` | `data/dl/server` | `data/bepinex` |
|---|---|---|---|
| Hrafnheim | `84325f3510f2` | `fab0cf8f0a35` | `f90a08532bb5` |
| Doggerland | `84325f3510f2` | `fab0cf8f0a35` | `096ea6d869a5` |
| Storgard | `84325f3510f2` | `fab0cf8f0a35` | `cb6a98c5cfc1` |
| Vangard | `84325f3510f2` | `fab0cf8f0a35` | `90163b817fe2` |
| Ulfsland | `84325f3510f2` | `fab0cf8f0a35` | `28a4b49ac9a5` |

`data/server` is **identical across all five worlds**: 991 files, 2,165,365,973 bytes.
`data/dl/server` likewise: 992 files, 2,165,366,818 bytes. The overlays differ only
because each carries a different plugin set; the vanilla base inside each overlay is
byte-for-byte the install:

```console
# per world: find data/server -printf '%P|%s\n' | sort  vs  the same over data/bepinex
Hrafnheim  overlap=991/991  overlapbytes=2165365973   bepinex-only=907 files / 887,132,091 B
Doggerland overlap=991/991  overlapbytes=2165365973
Storgard   overlap=991/991  overlapbytes=2165365973
Vangard    overlap=991/991  overlapbytes=2165365973
Ulfsland   overlap=991/991  overlapbytes=2165365973
```

Hrafnheim and Doggerland additionally carry a **fourth, flattened copy** directly under
`data/dl/` (`data/dl/valheim_server_Data` 1.9 G, `linux64`, `UnityPlayer.so`,
`steamclient.so`, …). `data/dl` is 4,333,444,865 B on those two worlds versus
2,168,078,091 B on Storgard, a difference of 2,165,366,774 B each.

No two copies share an inode anywhere:

```console
$ sudo -n stat -c "%i links=%h %n" \
    /media/big4/.../Hrafnheim/data/{server,bepinex,dl/server}/valheim_server_Data/Managed/assembly_valheim.dll
94179152  links=1 .../server/...
126827842 links=1 .../bepinex/...
127090941 links=1 .../dl/server/...
```

**17 copies × ~2.165 GB = 36.81 GB, of which 34.65 GB is redundant.**
(5 × `server`, 5 × `dl/server`, 5 × vanilla base inside `bepinex`, 2 × flattened `dl/`.)

### 1.2 The profile package caches are 27.38 GB holding 3.94 GB of distinct content

```console
$ sudo -n du -sh /media/big4/projects/game/valheim/profiles
26G	/media/big4/projects/game/valheim/profiles
$ sudo -n du -sh /media/big4/projects/game/valheim/profiles/*/manager-cache
3.7G admin  3.7G flat  3.7G vr  3.7G ulfsland-admin  3.7G ulfsland-dn  3.7G ulfsland-flat  3.7G ulfsland-vr
```

Walking all seven `manager-cache/{packages,client,server}` trees and keying by
`(path-relative-to-manager-cache, size)`:

```
total bytes across all 7 manager-caches: 27,380,822,516  (27.38 GB)
distinct (relpath,size) bytes:            3,942,859,934  ( 3.94 GB)
redundant:                                               (23.44 GB)
  of which packages/ alone: 15.19 GB total, 2.19 GB distinct, 13.00 GB redundant
```

`packages/` is the strongest case: `tools/valheim_mods.py:475` names every archive
`<Name>-<version>.zip`, and a Thunderstore package at a pinned version is immutable. Two
profiles holding `Azumatt-FastLink-1.2.3.zip` hold the same bytes by construction.

### 1.3 Nothing records what a world is running

`cmd_deploy` (`tools/valheim_mods.py:1286-1360`) stages
`profiles/<P>/manager-cache/server/BepInEx/plugins`, layers `manual-mods` and the
admin-mode overlay, renames it into `<world>/config_merged/bepinex/plugins`, hoists
preloader patchers, and prints `deployed=true`. It writes **no record** of:

- which profile it deployed from,
- the content of that profile's manifest at that moment,
- which game build the plugins were staged against,
- when, or by whom.

There is no marker file anywhere under a deployed world:

```console
$ sudo -n find /media/big4/.../Hrafnheim/data/bepinex -maxdepth 2 \
      \( -name '*.json' -o -name '.deploy*' -o -name '*manifest*' \)
.../valheim_server_Data/ScriptingAssemblies.json
.../valheim_server_Data/RuntimeInitializeOnLoads.json      # both are Unity's, not ours
```

The only link that exists is the six-byte `<world>/mods/.active-mod-profile`
(`tools/profile_store.py:45`, `:98-108`), which says which profile the world *should*
run, never which one it *does* run, and carries no revision.

Two partial records already exist and are worth building on rather than replacing:

- **`<fleet>/settings-history`** — a real git store, 738 commits, 74 MB, clean working
  tree, with commits literally named `Hrafnheim: deploy --apply`:

  ```console
  $ sudo -n git -C /media/big4/projects/game/valheim/settings-history log --oneline -5
  4ab1541 Vangard: deploy --apply
  997d045 Storgard: deploy --apply
  f69ea6c Doggerland: deploy --apply
  56b875f Doggerland: before deploy --apply
  c5ca5ce Hrafnheim: deploy --apply
  ```

  `tools/valheim_mods.py:1534-1542` already computes this commit and prints
  `settings_history=<commit>` — and then throws it away.
- **`require_matching_game_build`** (`hostops/lib/common.sh:435-...`) already hashes the
  three copies of `valheim_server_Data/Managed/assembly_valheim.dll` at start time.

### 1.4 Dead weight left over from tonight and from the fold

| path | bytes | live? |
|---|---|---|
| `<world>/mods/profiles.migrated` (H/D/S/V) | 15,178,631,502 | dead — the pre-fold per-world copies |
| `<world>/data/server.pre10-*` (D/S/V) | 5,270,615,703 | tonight's rollback copies |
| `Vangard/mods/manager` + `Vangard/mods/custom` | 11,496,819,467 | pre-fold leftovers; **needs operator confirmation** |
| stray flattened game in `data/dl/` (H, D) | 4,330,733,548 | dead |
| `<world>/mods/deployment-backups` (all 5) | 9,666,011,479 | retained by design, unbounded |
| `<world>/mods/removal-backups` (all 5) | 2,327,472,991 | retained by design |

### 1.5 The whole accounting

| category | on disk | redundant / recoverable |
|---|---:|---:|
| vanilla game tree × 17 copies | 36.81 GB | 34.65 GB |
| `profiles/*/manager-cache` × 7 | 27.38 GB | 23.44 GB |
| `mods/profiles.migrated` | 15.18 GB | 15.18 GB |
| `Vangard/mods/{manager,custom}` | 11.50 GB | 11.50 GB |
| `data/server.pre10-*` | 5.27 GB | 5.27 GB |
| `mods/deployment-backups` | 9.67 GB | 0 (by design) |
| `mods/removal-backups` | 2.33 GB | 0 (by design) |
| **total** | **108.13 GB** | **90.04 GB** |

The remaining ~62 GB of the 170 GB root is `archive/` (35.32 GB), `old/` (11.64 GB),
`phvalheim/` (8.51 GB), `config_merged/` (5.45 GB), `bak/` (0.78 GB), `world_backups/`
(0.44 GB), `settings-history/` (0.07 GB) — out of scope here.

**Headline for the bead:** the duplication is 90 GB, not 2 GB, and the 2 GB copy the bead
was filed about no longer exists. The provenance gap does.

---

## 2. Blast radius — everything that relies on the current shape

### 2.1 Readers and writers of `manager-cache` (§3 step B touches these)

| file:line | what it does |
|---|---|
| `tools/valheim_mods.py:474` | `def cache(root): return root / 'manager-cache'` — the single accessor |
| `tools/valheim_mods.py:475` | `archive_path()` → `manager-cache/packages/<Name>-<ver>.zip` |
| `tools/valheim_mods.py:477` | `cached_plugin()` → `manager-cache/<side>/BepInEx/plugins/<install>` |
| `tools/valheim_mods.py:132`, `:163`, `:450`, `:464` | remove / purge extracted plugin trees |
| `tools/valheim_mods.py:519` `install()`, `:535` `extract_package()` | the only writers into the cache |
| `tools/valheim_mods.py:502` | `assert_cached_version()` — the guard that caught a stale cache on Ulfsland |
| `tools/valheim_mods.py:1288` | `cmd_deploy` source = `cache(root)/'server'/'BepInEx'/'plugins'` |
| `tools/valheim_mods.py:1396` | admin-mode overlay reads `manager-cache/packages/<n>-<v>.zip` |
| `tools/profile_store.py:152-153` | `create()` makes `manager-cache/{client,server}/BepInEx/plugins` |
| `tools/profile_store.py:175` | `copy()` `shutil.copytree` — this is what makes the 3.7 GB duplicate |
| `tools/valheim_provision.py:156` | provision expects `profile/manager-cache/server/BepInEx/plugins` |
| `scripts/republish-profiles.sh:101` | `staged="$source_root/profiles/$profile/manager-cache/server/BepInEx/plugins"` |
| `tools/settings_history.py:15-17` | documents `manager-cache/` as deliberately **not** versioned |
| tests: `tools/test_valheim_mods.py:32,68,90,154,419,440,639`, `test_migrate_profiles.py:34`, `test_settings_history.py:27,39` | build fixture caches at that exact path |

### 2.2 Readers of the three game copies (§3 step A and the stamp touch these)

| file:line | what it does |
|---|---|
| `hostops/lib/common.sh:435` | `require_matching_game_build` — compares install / overlay / cache |
| `hostops/tests/start_valheim_server_gate.sh:107-133` | the gate's own fixtures write all three `assembly_valheim.dll` copies |
| `hostops/capture_valheim_diagnostics.sh:14` | `DATA_DIR="$WORLD_DIR/data/bepinex/BepInEx"` |
| `hostops/export_valheim_map_sources.sh:18` | `SERVER_ROOT="$WORLD_DIR/data/bepinex"` |
| `internal/agent/agent.go:1024-1027` | reads `data/server/valheim_server_Data/...` for world intel |
| `internal/agent/agent.go:1041` | scans both `config_merged/bepinex/plugins` and `data/bepinex/BepInEx/plugins` |
| `internal/worldintel/worldintel.go:42` | documents `Ulfsland/data/server/.../assembly_valheim.dll` |
| `tools/player-identities/build.sh:14-15` | hardcodes `Vangard/data/{server,bepinex}` |
| `tools/modpatches/README.md:48-49` | documents `<World>/data/bepinex/...` |
| external: `valheim-server-docker/…/valheim-updater:86` | `rsync -a --itemize-changes --delete "$valheim_download_path/" "$valheim_install_path"` |
| external: `valheim-server-docker/…/valheim-updater:162` | `steamcmd +force_install_dir "$valheim_download_path" +app_update 896660` |

### 2.3 The link and the deploy path (§3 step C writes here)

| file:line | what it does |
|---|---|
| `tools/profile_store.py:45` | `LINK_FILE = ".active-mod-profile"` |
| `tools/profile_store.py:98-108` | `link_path()` / `linked_profile()` |
| `tools/profile_store.py:111-124` | `link()` |
| `tools/profile_store.py:140-143` | `linked_servers()` — "the servers a change will reach at their **next restart**" |
| `tools/profile_store.py:162-180` | `copy()` — **deliberately** records no `copied_from`; see §4.1 |
| `tools/valheim_mods.py:1286-1360` | `cmd_deploy` |
| `tools/valheim_mods.py:1534-1542` | settings-history snapshot; prints `settings_history=<commit>` |
| `tools/valheim_mods.py:471-473` | `require_stopped()` — deploy refuses while the container runs |
| `hostops/manage_mods.sh:16`, `:24`, `:52-59` | completion enumerates linked worlds and the shared store |
| `policy.yaml:175-181` | verb `mod_deploy`, rollback `mods/deployment-backups/<profile>/legacy-plugins.previous` |
| `policy.yaml:263` | **stale**: `read_only: /media/big4/.../<World>/mods/profiles` — a path the fold removed |
| `internal/app/provision.go:24-40` | `profileCatalogChoice`, the admin UI's "Profile this server runs" |
| `tools/valheim_profile_catalog.py:30-34` | catalog rows for that UI |

---

## 3. The proposed shape

Three independent changes. They are ordered by value-per-risk, not by size. **The
provenance stamp is the one that addresses the failure that cost three saves; the dedup is
housekeeping.** An executor short on time should do A and C and skip B.

### A. Delete what is provably dead (36.28 GB, no design change)

`profiles.migrated`, `server.pre10-*`, the stray flattened `data/dl/` trees, and — after
operator confirmation — `Vangard/mods/{manager,custom}`. This is not a redesign; it is the
cleanup the fold and tonight's migration never did. It also removes four of the seventeen
game copies, which is a *safety* gain, not just a disk gain: fewer plausible-looking
`assembly_valheim.dll` files for a future repair to copy the wrong one from.

### B. One fleet package store, hardlinked into each profile (13.00 GB of the 23.44 GB)

Add `<fleet>/package-cache/packages/<Name>-<version>.zip` as the single download
destination. `tools/valheim_mods.py:519 install()` downloads there; `archive_path()`
resolves there; each profile's `manager-cache/packages/<Name>-<version>.zip` becomes a
**hardlink** to it.

Hardlinks are safe here and nowhere else in this proposal, because a Thunderstore archive
at a pinned version is immutable and every writer in the tree creates a *new* file for a
*new* version rather than rewriting an existing one (`install()` writes
`archive_path(root, p, ver)`, and `remove_package_files` unlinks). Unlinking a hardlink
never mutates a sibling.

The extracted `manager-cache/{client,server}/BepInEx/plugins/<Package>` trees (10.4 GB
redundant) are **explicitly out of scope**: they are the deploy source, `cmd_deploy`
copytrees them, and `assert_cached_version` mutates and validates them per profile. Making
those shared is how you get one profile's edit silently changing another's deploy.

### C. A deployment stamp — the actual provenance

`cmd_deploy`, as its final act inside the same atomic rename window, writes
`<world>/mods/.deployed.json`:

```json
{
  "schema": 1,
  "profile": "admin",
  "profile_manifest_sha256": "<sha256 of profiles/admin/profile-manifest.json>",
  "settings_history": "4ab1541…",
  "deployed_at": "2026-09-14T02:31:07Z",
  "deployed_by": "operator",
  "packages": 111,
  "game_build": {
    "install":  "<sha256 data/server/valheim_server_Data/Managed/assembly_valheim.dll>",
    "overlay":  "<sha256 data/bepinex/…/assembly_valheim.dll>",
    "cache":    "<sha256 data/dl/server/…/assembly_valheim.dll>"
  }
}
```

Every field is already computed somewhere:
`profile` from `profile_store.linked_profile`; `settings_history` from the commit
`tools/valheim_mods.py:1542` currently only prints; the three `game_build` hashes from the
same three paths `require_matching_game_build` already reads.

Then two consumers:

1. **`require_matching_game_build` gains a fourth comparison.** Today it asks "do the three
   game copies agree?". It should also ask "does the overlay still hash to the
   `game_build.overlay` recorded when the plugins were deployed?" A mismatch means the game
   moved under a mod set — which is precisely the 2026-09-13 failure — and must refuse to
   start with a message naming `manage_mods.sh <world> deploy --apply` as the fix.
2. **`status_valheim_server.sh` and the admin world page print the stamp**, and mark it
   **stale** when `profile_manifest_sha256` no longer matches the profile on disk. That
   turns "which worlds still need a deploy after I edited `admin`?" from a manifest diff
   into a field.

### D. One fleet-wide deploy (the bead's "better fix")

`manage_mods.sh --profile <P> deploy --all-linked` iterating
`profile_store.linked_servers(P, fleet)`, refusing outright if any linked world is
running, and reporting one stamp per world. This is the operation that turns "adding three
mods fleet-wide took four separate `mod_add` batches and 13 approvals" into one. It is
listed last because it is pure ergonomics: it changes no on-disk shape and is a thin loop
over an operation that already exists.

### What this explicitly does NOT change

- **`.active-mod-profile` stays.** It is the link, it is correct, and every tool reads it.
- **`profile-manifest.json` gains no fields.** The stamp is a separate file. (For the
  *published* manifest of the same name, adding a field is a wire break — see
  `docs/release-format.md:52-66`. Keeping the two apart is also why the stamp is not
  written into the manifest.)
- **`profile_store.copy()` still records no `copied_from`.** See §4.1.
- **No sharing of the game install, ever.** See §6.
- **No sharing of extracted plugin trees.** See §3.B.
- **No naming change.** The `profile` / `edition` vocabulary question is
  `docs/proposed/2026-09-13-profile-terminology.md` and is deliberately kept separate, so
  that a rename and a filesystem migration never land in the same change.
- **Nothing about published releases, client artifacts, or the portal database.**

---

## 4. Two decisions this plan cannot make

### 4.1 `copied_from`: the bead asks for it, the code refuses it on purpose

The bead's "minimum fix" is "write `copied_from {world, profile, at}` on provision and on
profile copy". `tools/profile_store.py:162-168` refuses, with a stated reason:

> No link back, no shared files, no `copied_from`: an operator asking "what is this
> profile" must not be answered with a chain to somewhere else. The whole point of
> copy-versus-link is that a copy stops tracking its source here.

Both positions are defensible and they contradict. **My recommendation, which I believe
satisfies both:** record the copy in `settings-history` — which is append-only, already
exists, already commits on every mutating mod operation, and already holds each profile's
manifest as text — as a commit `profile <new>: copied from <source>`. `git log --follow`
then answers "where did this come from" exactly once, at the moment it was true, without
making the live object a chain. The manifest stays a description of *what this profile
is*, which is what `profile_store` is protecting.

**This still needs the operator to say yes**, because it declines the bead's literal ask.

### 4.2 Is `Vangard/mods/{manager,custom}` (11.50 GB) dead?

Hrafnheim's `mods/manager` is 21 MB; Vangard's is 9.41 GB, plus 2.09 GB of `mods/custom`.
Vangard was the world the pre-fold tooling was developed against. **[INFERENCE]** these are
pre-fold leftovers, but `mods/manager/exports/` is where `build-profile-definition.sh`
writes published ZIPs (`README.md:629`), so some of it may be release history the operator
wants. An executor must not delete this without an explicit answer. Everything else in
§1.4's "dead" rows is unambiguous.

---

## 5. Migration path, safe on a live fleet

**Ulfsland is running and is the operator's live world.** No step below stops it, and no
step writes into `Ulfsland/` while it is up except step 6, which is additive and
crash-safe. Steps are ordered; each is independently revertible.

> Every `hostops` invocation in this section uses the standard preamble:
> ```sh
> cd /srv/valheim-portal && sudo -n env \
>   VALHEIM_ROOT=/media/big4/projects/game/valheim \
>   VALHEIM_SERVER_DOCKER_DIR=/media/big3/Projects/Game/valheim/server/ValheimConfig/valheim-server-docker \
>   timeout 900 hostops/<script>.sh <args>
> ```

### Step 1 — record the baseline (no world stopped)

```sh
sudo -n du -sb /media/big4/projects/game/valheim/{profiles,*/mods,*/data} \
  > /media/big4/projects/game/valheim/config_backups/disk-baseline-$(date -u +%Y%m%dT%H%M%SZ).txt
sudo -n git -C /media/big4/projects/game/valheim/settings-history rev-parse HEAD
```
Revert: delete the file.

### Step 2 — quarantine, do not delete, the dead trees (no world stopped for H/D/S/V; **skip Ulfsland entirely**, it has none of these)

`mv` inside the same filesystem is atomic and instant; it is the revert that matters.

```sh
V=/media/big4/projects/game/valheim
sudo -n mkdir -p $V/config_backups/quarantine-20260914
for w in Hrafnheim Doggerland Storgard Vangard; do
  [ -d $V/$w/mods/profiles.migrated ] && sudo -n mv $V/$w/mods/profiles.migrated $V/config_backups/quarantine-20260914/$w-profiles.migrated
done
for w in Doggerland Storgard Vangard; do
  sudo -n mv $V/$w/data/server.pre10-* $V/config_backups/quarantine-20260914/
done
```

`data/server.pre10-*` sits inside a bind-mounted `data/`, but those four worlds are
stopped, so nothing holds it. **Do not move anything out of `Ulfsland/data/` at all.**

Revert: `mv` back. Delete for real only after step 8 passes and the operator says so.

### Step 3 — the stray flattened `data/dl/` copies (worlds must be STOPPED: Hrafnheim, Doggerland)

These two worlds have a full game tree loose in `data/dl/` alongside `data/dl/server/`.
This is **measured, not inferred**: the updater's paths are declared in the
`valheim-server-docker` checkout, and the download directory is `dl/server`, not `dl`.

```console
$ sudo -n grep -n 'valheim_\(download\|install\)_path=' \
    /media/big3/Projects/Game/valheim/server/ValheimConfig/valheim-server-docker/common
16:valheim_download_path=/opt/valheim/dl/server    # Valheim server download directory
17:valheim_install_path=/opt/valheim/server        # Valheim server installation directory
```

So nothing writes the loose tree and nothing reads it; Storgard, Vangard and Ulfsland have
no such tree and update fine. Quarantine only the loose members — `valheim_server_Data`,
`linux64`, `UnityPlayer.so`, `steamclient.so`, `libsteamwebrtc.so`, `libdecor-*`,
`valheim_server.x86_64`, `start_server*.sh`, `docker*`, `steam_appid.txt`, `steamapps`,
the PDF — and **leave `dl/server/` and `dl/bepinex/` in place**: `dl/bepinex/merge` is the
signal `require_matching_game_build` (`hostops/lib/common.sh:423-426`) touches to make the
container re-merge its overlay. Frees 4.33 GB.

If a future checkout ever sets `valheim_download_path=/opt/valheim/dl`, this step inverts
and the loose tree becomes the live one — re-run the grep before acting.

Revert: `mv` back, then start the world.

### Step 4 — Vangard leftovers (world STOPPED; blocked on §4.2)

Only after the operator answers. Quarantine, never delete. Frees 11.50 GB.

### Step 5 — the fleet package store (no world stopped; **no world may be mid-`mod_add`**)

Code change in `tools/valheim_mods.py` (`archive_path`, `install`) plus a one-shot
`tools/link_package_cache.py plan|apply`:

1. For every `profiles/*/manager-cache/packages/*.zip`, group by basename.
2. Verify every member of a group has an identical SHA-256. **Any group that disagrees
   aborts the whole run and prints the group** — a name collision with differing content
   means a corrupt cache, and silently picking a winner is exactly the mistake
   `migrate_profiles.py` was written to refuse.
3. Move one member to `<fleet>/package-cache/packages/<name>`, then replace every member
   (including the moved one's origin) with a hardlink to it, one file at a time via
   `link()` + `rename()` so a crash leaves either the old file or the new link, never
   neither.

This is safe with worlds running: nothing reads `manager-cache/packages` at runtime — it
is a download cache consulted by `install()` and the admin-mode stager
(`valheim_mods.py:1396`). It is *not* safe concurrently with a `mod_add`, which writes
there.

Frees 13.00 GB. Revert: `cp --remove-destination` each link back to a real file, or simply
delete `<fleet>/package-cache` after copying members back — the content is also
re-downloadable from the manifest.

### Step 6 — the deployment stamp (additive; no world stopped to *add* the code, one deploy per world to *populate* it)

Ship the `cmd_deploy` change. `<world>/mods/.deployed.json` appears at the next deploy of
each world. Until then it is absent, and every consumer must treat absent as "unknown",
never as "mismatch" — a fleet where the stamp's absence refuses to start a world is a fleet
that cannot boot after a rollback of this change.

Populate the four stopped worlds with a normal deploy while they are already down. **Do not
deploy Ulfsland to populate its stamp** — `require_stopped` (`valheim_mods.py:471`) will
refuse anyway; its stamp appears at the operator's next real deploy.

Revert: the file is inert; delete it and revert the code.

### Step 7 — the build-drift check (needs step 6 populated on that world)

Extend `require_matching_game_build` to compare `data/bepinex/…/assembly_valheim.dll`
against `.deployed.json:game_build.overlay`. Gate it: absent stamp → pass with a note.

This must land **after** at least one world has a populated stamp and must be exercised
against `hostops/tests/start_valheim_server_gate.sh`, which already builds all three
`assembly_valheim.dll` fixtures (`:107-109`) and is the right place for the new case.

Revert: remove the comparison. It is a pure addition to one function.

### Step 8 — `deploy --all-linked` (every linked world STOPPED)

Pure ergonomics, no on-disk change, revertible by removing the flag.

---

## 6. What would prove it worked

Not "tests pass". Six falsifiable checks:

1. **Disk.** `sudo -n du -sh /media/big4/projects/game/valheim` drops from 170 G to
   ≈ 120 G after steps 2–5 (36.28 GB dead + 13.00 GB package links). Re-run the step-1
   baseline command and diff.
2. **The links are real links.**
   `sudo -n stat -c '%i %h %n' /media/big4/.../profiles/*/manager-cache/packages/Azumatt-FastLink-*.zip`
   shows one inode with `links=7`, not seven inodes with `links=1`. This is the exact
   check that proves the §1.1 "no sharing" finding has been fixed for the cache.
3. **The deploy is byte-identical across the change.** Before step 5, fingerprint a stopped
   world's deploy target:
   `sudo -n find <W>/config_merged/bepinex/plugins -type f -printf '%P %s\n' | sort | sha256sum`.
   After step 5, run `manage_mods.sh <W> deploy --apply` and fingerprint again. **Equal.**
   A dedup that changes what gets deployed is a dedup that has broken something.
4. **The stamp is truthful and detects staleness.** On a stopped world: deploy, confirm
   `.deployed.json:profile_manifest_sha256` equals
   `sha256sum profiles/<P>/profile-manifest.json`. Then `mod_add` one package to `<P>`
   without deploying, and confirm `status_valheim_server.sh` reports the world as **stale**.
   Then deploy and confirm it reports current. Three observations, one of which must be
   negative or the check is vacuous.
5. **The drift check actually refuses.** In `hostops/tests/start_valheim_server_gate.sh`,
   write a `.deployed.json` whose `game_build.overlay` disagrees with the fixture overlay
   and assert the start is refused naming `deploy --apply`; then make them agree and assert
   it starts. This reproduces the 2026-09-13 failure in miniature — a mod set deployed
   against one binary, started against another — and proves the guard fires on it.
6. **Ulfsland was never touched.** `sudo -n docker ps --format '{{.Names}} {{.Status}}' |
   grep Ulfsland` shows continuous uptime across the whole migration, and
   `sudo -n git -C <fleet>/settings-history log --oneline` shows no Ulfsland commit
   introduced by any step.

---

## 7. Risks, and the alternatives rejected

### Risk 1 — hardlinking anything the updater writes will corrupt the fleet, silently

This is the one that would turn a disk-saving change into another three-save night.

`steamcmd +force_install_dir "$valheim_download_path" +app_update`
(`valheim-updater:162`) applies **delta patches in place**. **[INFERENCE]** — I did not run
steamcmd to confirm, and an executor should confirm before ever considering it, by
hardlinking one file in a scratch world's `dl/server`, forcing an update, and checking
whether the link count dropped to 1 (safe: steamcmd replaced the file) or stayed at 2 with
changed content (fatal: steamcmd wrote through the link into every world at once).

If steamcmd writes in place, a shared hardlinked `dl/` means one world's Steam update
rewrites all five worlds' download caches mid-flight. The rsync onto `server/` would then
carry a half-written binary into an install, and `require_matching_game_build` would refuse
to start — five worlds, at once, from one update. **This proposal therefore shares nothing
under `data/`.** The 34.65 GB stays duplicated on purpose.

The package cache is the opposite case: immutable content keyed by version, written by our
own code, never patched. That is why B is in and a `dl/` share is not.

### Risk 2 — the stamp becomes a second source of truth

If anything ever *trusts* `.deployed.json` over the filesystem, a hand-edited stamp becomes
a way to start a mismatched world. Mitigation, and it is not optional: the stamp is only
ever compared against freshly hashed bytes, never substituted for them, and an absent or
unparseable stamp degrades to "unknown → allow with a note". `valheim_mods.py:589` already
states the principle — "the filesystem is the authority for what a deploy will copy".

### Risk 3 — step 5 running concurrently with a mod operation

Two writers to `manager-cache/packages`. Mitigation: `tools/link_package_cache.py` takes
the same provisioning lock the fleet already uses — `tools/valheim_provision.py:368-370`
takes `flock(LOCK_EX)` on `<fleet>/.portal-provision.lock` — and refuses if any world's
agent has an in-flight verb.

### Rejected: filesystem-level dedup (`duperemove`, `rdfind -makehardlinks`, reflinks)

Cheapest to write — one command, ~57 GB back across game trees and caches. Rejected on
three counts: ext4 has no reflink, so the only mechanism is hardlinks; a blind hardlinker
cannot tell the immutable package zips from the `assembly_valheim.dll` that steamcmd
patches in place, so it walks straight into Risk 1 across all five worlds; and it records
nothing, so it answers none of the bead — the fleet would be smaller and exactly as unable
to say what any world is running.

### Rejected: one shared read-only game install bind-mounted into every container

Correct in principle and the largest single win (34.65 GB). Rejected for now because it
requires changing the container launch — `data/` is bind-mounted whole and rw at
`/opt/valheim`, so a shared install needs a second mount and a matching change in
`valheim-server-docker`, a **separate checkout this repository's gates cannot see**
(`hostops/lib/common.sh:417-420` says exactly this about the last fix that lived there).
Tonight's incident came from a change in that checkout interacting badly with this one.
Worth revisiting once the stamp exists and can prove which build each world ran; not worth
doing in the same change as the thing that would detect it going wrong.

### Rejected: `copied_from` in `profile-manifest.json`

The bead's literal minimum fix. Rejected in favour of a settings-history commit — see
§4.1, which is an operator decision, not mine.

### Rejected: doing nothing about disk and shipping only the stamp

Genuinely tempting: the stamp is the fix for the failure, and 2.8 T is free. Rejected only
because 36.28 GB of the waste is *dead copies of the game binary* sitting in the same
directories a human reaches for during a 3 a.m. repair — and hand-cloning `data/dl` and
`data/server` between worlds is literally what tonight's recovery consisted of. Removing
wrong-looking candidates is a correctness change wearing a housekeeping costume.
