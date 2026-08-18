# Which directory is the portal

Three checkouts of this repository exist on the host and only one of them serves players.
Confusing them cost a session: an operator ran the mod tool from the wrong one, got a
traceback from code that had been fixed months earlier, and concluded the tool was broken.

| path | role |
| --- | --- |
| `/srv/valheim-portal` | **the deployment.** `docker compose` was started here; the container mounts its `dist/` as `/srv/client`, and the portal reads `release-targets.json` from it |
| `/media/docker/projects/vibe/vibewright/repos/valheim-portal-main` | **the working checkout.** Current with `main`; where code is written, tested and committed |
| `/media/big3/Projects/Game/valheim/server/ValheimConfig/portal` | an older copy. Holds `.env` and the `boilerplate/` mod artifacts, but its tooling predates fixes in `main`. Marked with `NOT-THE-DEPLOYMENT.md` |

Confirm rather than assume, at any time:

```bash
docker inspect valheim-portal-portal-1 \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

## Paths the scripts expect

`republish-profiles.sh` and `seed-release` default to `/var/lib/valheim-portal`, which is
the path recorded inside every release row — the container's own view of its data. On this
host that path is a symlink to the docker volume, so the same string resolves for both:

```bash
/var/lib/valheim-portal -> /media/docker/var/lib/docker/volumes/valheim-portal_portal-data/_data
```

Remove that symlink and the publish scripts stop finding the database from the host. Publishing
through the portal's HTTP API works either way, because then the server writes the artifacts
itself, but the scripts are the supported path.

## What an install places outside the checkout

| path | role |
| --- | --- |
| `/etc/valheim-portal/{csrf-secret,admin-token,agent-token}` | the three secrets the portal and agent share, mode 0640 `root:valheim-agent`, preserved across installs |
| `/etc/valheim-portal/agent-bridge-token` | the bridge token, generated whether or not the bridge is on so enabling it later is one config line |
| `/etc/valheim-portal/agent.env` | the agent's fixed operating parameters |
| `/etc/valheim-portal/agent-runner.env` | shared by both runner units, so an on-demand pass and the poller cannot disagree |
| `/usr/local/bin/valheim-portal` | the agent binary; the portal itself runs from the container image |
| `/usr/local/bin/valheim-agent-runner` | the runner, installed only when the bridge is enabled |
| `/etc/systemd/system/valheim-portal-agent.service` | the agent, always enabled |
| `/etc/systemd/system/valheim-agent-runner.service` | the poller, enabled only when `AGENT_RUNNER_SERVICE=true` |
| `/etc/systemd/system/valheim-agent-runner-once.service` | one pass on demand; installed but never enabled |
| `/var/lib/valheim-agent-runner/cursor` | the runner's inbox position, so a restart does not re-answer a question |
| `/etc/systemd/system/valheim-agent-runner-wake.path` | watches the portal's wake file and starts one pass; enabled with the bridge, off in polling mode |
| `<portal data volume>/agent-wake` | written by the portal when the operator sends a message or decides a verb. The only writable path it has: every other mount is read-only |
| `/srv/valheim-portal/.env` | generated from `deploy/install.conf` on every install. Hand-edits here are lost on the next one; the previous file is kept as `.env.replaced` |

`deploy/install.conf` is untracked operator data and is the only file to edit. Everything in the
table above is derived from it, which is why a value that is right in `.env` but missing from
`install.conf` reappears wrong after a reinstall.

## The fleet tree

`VALHEIM_WORLD_ROOT` holds one directory per server plus two directories that belong to
no server, because what is in them is shared:

| path | role |
| --- | --- |
| `<fleet>/<World>/` | one server: its saves, `valheim.env`, `config_merged/`, and `mods/.active-mod-profile` naming the profile it runs |
| `<fleet>/profiles/<name>/` | the profile store. One definition per name, edited once, read by every server linked to it |
| `<fleet>/settings-history/` | the git store of settings text, so removing a mod cannot lose its configuration |

Which servers a profile drives, and which profile a server runs:

```bash
python3 tools/profile_store.py list
python3 tools/profile_store.py linked <World>
```

## release-targets.json

Gitignored, because the set of published editions is per-deployment. It therefore drifts
between copies: the live one lacked the two `-non-vr` targets for a while, so a republish
silently skipped them and both kept shipping a mod that had been removed everywhere else.
When an edition is added, update the copy in `/srv/valheim-portal` **and** the working
checkout, and check the count — four editions per world:

```bash
python3 -c "import json;d=json.load(open('release-targets.json'));print(len(d['flat'])+len(d['vr']))"
```

## Publishing without taking a world down

Publishing writes a release; deploying swaps a server's plugin folder. Only the second needs the
world stopped. `republish-profiles.sh` checks whether the profile's server plugin set differs from
what is deployed and refuses only then, so a client-side change — a config value, a plugin build —
publishes while players stay connected.

Two overrides exist for shipping a rebuilt artifact through that same path rather than by hand:

```bash
VALHEIM_CLIENT_PLUGIN=/path/to/plugin.zip     # the client plugin bundle
VALHEIM_VR_RUNTIME=/path/to/vr-runtime.zip    # the VR runtime bundle
```

Both matter because a republish otherwise carries forward whatever the previous release attached —
which is correct for an untouched artifact and wrong for one you just fixed.
