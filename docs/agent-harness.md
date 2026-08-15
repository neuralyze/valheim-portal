# The agent harness

An operator chats to an agent that manages this deployment: mods, profiles, worlds, and the C#
VR plugin. `policy.yaml` in the repository root is the authoritative definition of what that
agent may do; this document explains it, and `tools/check_agent_policy.py` fails CI if the two
drift apart.

## Why the lane is drawn mechanically

This repository already ran the experiment. It carried standing rules in prose, and an agent
violated them repeatedly across a single working day: nine plugin releases chasing one behaviour,
a client manifest overwritten without being read, a setting shipped inverted, work published
while the operator was still asking a question. Two things stopped it, and neither was prose - a
pre-write hook that refused a file, and a policy layer that refused a capability.

So the agent's lane is what the harness refuses, not what the agent is told. Prose is the
explanation; the checks are the fence.

## Backend

The harness runs [omp](https://github.com/gastownhall/omp) (`omp v17.3.0` at time of writing).
The portal stores no provider configuration and no API keys: omp owns authentication.

```bash
omp setup                          # onboarding, optional feature dependencies
omp auth-broker login <provider>   # authenticate an account into omp's credential vault
omp auth-broker status             # confirm it took
omp acp                            # run omp as an ACP server over stdio, for the chat backend
```

Two flags matter for a deployment:

```bash
omp --profile valheim-portal ...   # isolated auth, sessions, settings and caches
omp auth-broker login <p> --via user@host   # authenticate against a remote host over SSH
```

An installer who has not logged in gets a chat that reports "no authenticated provider" and
stops. That is the intended behaviour: this project never holds anyone's key.

## Who the agent is

The chat inherits the portal's own authentication - device auth and the admin role that already
exist - so an agent can only touch worlds its user already administers. It runs as its own OS
user with no `sudo`, no read access to `.env`, and no git credential for a parent remote.

Capabilities the agent must not have are removed rather than refused. No shell means no
improvised failure modes; no credential means no upstream push.

## Approval classes

| class | approval | what it covers |
|---|---|---|
| `read` | none | status, logs, manifests, changelogs, deploy plans |
| `repo_write` | none | code, tests, branches, C# plugin authoring and builds in this checkout |
| `world_state` | every invocation | mods, deploys, starts, stops, backups, restores |
| `player_facing` | every invocation | publishing a profile players download; confirming a release |
| `forbidden` | never | upstream push, provisioning, server deletion, reading secrets |

`world_state` and `player_facing` require a fresh confirmation on **every** call, including
repeats against other worlds. `deploy_apply` is one compound operation - backup, stop, deploy,
start - so a single confirmation covers a reversible unit that begins with a backup. A deploy
whose plan shows no changes is refused rather than confirmed: it would take a world down and
restart it to change nothing.

## Verbs

Each verb maps to a command that already exists and already validates its own inputs.

| verb | class | maps to |
|---|---|---|
| `world_status` | read | portal operation "status" |
| `world_logs` | read | portal operation "logs" |
| `mod_inventory` | read | `tools/valheim_mods.py list` |
| `mod_check_updates` | read | `tools/valheim_mods.py check-updates` |
| `mod_notes` | read | `tools/valheim_mods.py notes` |
| `mod_search` | read | `tools/valheim_mods.py search` |
| `release_status` | read | `tools/valheim_mods.py release-status` |
| `deploy_plan` | read | `hostops/manage_mods.sh <World> deploy` |
| `repo_edit` | repo_write | edits within this checkout |
| `plugin_build` | repo_write | `tools/vrfixes/build.sh` |
| `mod_add` | world_state | `tools/valheim_mods.py add` |
| `mod_remove` | world_state | `tools/valheim_mods.py remove` |
| `mod_update` | world_state | `tools/valheim_mods.py update` |
| `deploy_apply` | world_state | portal operation "mod_deploy" |
| `world_start` | world_state | `hostops/start_valheim_server.sh` |
| `world_stop` | world_state | portal operation "stop" |
| `world_backup` | world_state | portal operation "backup" |
| `world_restore` | world_state | portal operation "restore" |
| `publish_profile` | player_facing | `scripts/republish-profiles.sh` |
| `release_confirm` | player_facing | `tools/valheim_mods.py release-confirm` |
| `upstream_push` | forbidden | git push to a parent repository |
| `delete_server` | forbidden | portal operation "delete_server" |
| `provision` | forbidden | portal operation "provision" |
| `secrets_read` | forbidden | reading `.env` or credential files |

## Evidence

Mutating verbs report what they did by reading it back, and the chat shows that readback rather
than the agent's summary of it.

- **Publishing** reads the change out of the published artifact - the setting's value, or a
  string unique to the new code. This is not theoretical hygiene: a fix committed at 00:11 was
  reported as live in a release built seventeen hours earlier, and the operator ran four test
  sessions against a build that could not contain it.
- **Settings changes carry a citation** - the config's own description text, or the code that
  binds the value. `LODBiasMax` shipped inverted because its direction was asserted from memory.
- **One behavioural change per publish.** Six changes in one release leave nothing to verify.
- **"Unmeasured" is not "negligible".** A once-per-second scene sweep was dismissed as too small
  to matter without ever being timed, and it was the cause of a day-long frame-rate collapse.

## Invariants

Checked after a change rather than assumed:

- **Client/server parity** - the lists must match or the join handshake refuses; seven
  client-only packages are the exception.
- **Dependencies complete** - a missing dependency throws repeatedly at load, not at install.
- **Rollback available** - no mutation runs unless its reversal exists.
- **Cutover recorded** - a removal affecting players records a client-release cutover, and
  `world_start` refuses while one is outstanding.

## What is wired today

Ten verbs execute through the portal. The other fourteen are declared, refused with a reason,
and recorded as refused - an approximation would be worse than a refusal, and this project has
already paid for the difference.

```
executes now    world_status world_logs mod_inventory mod_search
                mod_add mod_remove deploy_apply world_start world_stop world_backup
refused: no host operation exists
                mod_check_updates mod_notes release_status deploy_plan
                mod_update publish_profile release_confirm
refused: not the portal's job
                repo_edit plugin_build   the agent process edits and builds in its own workspace
refused: deliberately operator-only
                world_restore   keeps its typed two-step confirmation
forbidden       upstream_push delete_server provision secrets_read
```

Wiring the first group needs new host-agent operations, since the portal reaches the host only
through the agent socket. That is tracked, not forgotten.

## The bridge API

Enabled only when `PORTAL_AGENT_BRIDGE_TOKEN_FILE` points at a file holding at least 32
characters. Absent, the endpoints answer `503` and say which variable to set - a deployment must
opt in before an agent can drive anything.

```
GET  /api/agent/inbox?since=<cursor>   new turns, the next cursor, and calls awaiting approval
POST /api/agent/message                {"body": "..."}  the agent's own turn in the conversation
POST /api/agent/verb                   {"verb": "...", "world": "...", ...}
```

All three require `Authorization: Bearer <token>`. The verb endpoint answers:

```
200  executed          {"status":"succeeded","evidence":"...","id":"..."}
202  awaiting operator {"status":"pending_approval","id":"..."}
400  unknown verb      {"error":"unknown verb ...","known_verbs":[...]}
403  forbidden         {"status":"refused"}
501  declared, unwired {"status":"refused","error":"not available through the portal: ..."}
502  ran and failed    {"status":"failed","error":"..."}
```

A `202` is the normal answer for anything mutating: the call waits on `/admin/agent`, where an
operator approves or denies it. Approval runs the verb and writes the result back into the
conversation as a system turn, so the agent reads what happened from the record instead of
assuming its request succeeded.

## Tasks and memory

`bd` (beads) is the source of truth for outstanding work. The agent files what it discovers,
notes evidence against it, and stores durable facts as memories so an operator is not asked the
same question twice.

```bash
bd list --status=open     # what is actually outstanding
bd note <id> "..."        # release ids, artifact paths, file and line
bd remember "..." --key   # facts, each with a citation
```

A verb that fails after changing anything files or updates a bead before returning: three worlds
once ended with a half-finished mod removal and no record anywhere.

`bd init` inside this checkout inherits the parent workspace's `sync.remote` and clones an
unrelated project's tracker - 848 issues, demonstrated twice. Always pass `--remote ''` here.
`tools/check_beads_workspace.py` fails the build if the local workspace is not this project's.

## What the agent must not decide

Whether something looks right. Whether it feels better in a headset. Whether a mod people play
with should go. Anything requiring eyes on a screen it cannot see. It may ask, and an unanswered
question blocks the action rather than resolving into a guess.

## Extending the policy

1. Add the verb to `policy.yaml` with its class, what it maps to, and its preconditions,
   evidence and rollback where they apply.
2. Add the row to the verb table above.
3. Run `python3 tools/check_agent_policy.py` - it fails when the file and this table disagree.
