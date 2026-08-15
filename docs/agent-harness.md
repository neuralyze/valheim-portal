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
| `world_log_tail` | read | `hostops/portal_world_log.sh` |
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

Eighteen verbs execute through the portal. The rest are declared, refused with a reason, and
recorded as refused - an approximation would be worse than a refusal.

```
executes now    world_status world_logs world_log_tail mod_inventory mod_search mod_check_updates
                mod_notes release_status deploy_plan
                mod_add mod_remove mod_update deploy_apply
                world_start world_stop world_backup
                publish_profile release_confirm
refused: not the portal's job
                repo_edit plugin_build   the agent edits and builds in its own workspace
refused: deliberately operator-only
                world_restore   keeps its typed two-step confirmation
forbidden       upstream_push delete_server provision secrets_read
```

Every mod verb is profile-scoped: the host refuses one without a valid profile name, so the
portal requires it before dispatch rather than discovering it downstream.

Two verbs carry arguments that decide what players receive, and both are checked before a request
is even recorded:

```
publish_profile   world + source profile + client_type (vr|flat) + notes (8-500 chars, one line)
release_confirm   published_profile + client_type + release_id + archive (a plain .zip name)
mod_notes         lines, bounded to 1-200, because every crossed version's changelog is fetched
```

A publish takes **no artifact paths**. `hostops/portal_publish_profile.sh` resolves the single
matching target out of `release-targets.json` and lets the publish script carry the newest plugin
and VR runtime forward from that profile's own previous release, so a request can neither aim a
release at an arbitrary file on the host nor publish something an operator never declared. It
also never stops a server: publishing is a client-side change, and taking a world down for one
was a real incident here.

## The bridge API

Enabled only when `PORTAL_AGENT_BRIDGE_TOKEN_FILE` points at a file holding at least 32
characters. Absent, the endpoints answer `503` and say which variable to set - a deployment must
opt in before an agent can drive anything. That opt-in is `PORTAL_ENABLE_AGENT_BRIDGE=true` in
`deploy/install.conf`: the installer then generates the token, mounts it into the container, and
points the variable at it. Setting the variable by hand works but drifts from the config the next
install reads, which is how a deployment ends up refusing requests nobody changed.

```
GET  /api/agent/verbs                  the vocabulary: class, required arguments, the optional ones
                                       a caller may set, and why anything unavailable is unavailable
GET  /api/agent/inbox?since=<cursor>   new turns, the next cursor, and calls awaiting approval
POST /api/agent/message                {"body": "..."}  the agent's own turn in the conversation
POST /api/agent/verb                   {"verb": "...", "world": "...", ...}
```

A runner reads its vocabulary from `/api/agent/verbs` rather than carrying a copy, so it cannot
believe it holds a verb the portal has withdrawn - and it is told *why* something is unavailable,
because a runner that cannot see the reason will keep asking.

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

## The runner

`cmd/agent-runner` is the process that drives all this. It reads the conversation, asks omp what to
do, and requests verbs; the portal decides what is allowed and records what happened.

Installing with `PORTAL_ENABLE_AGENT_BRIDGE=true` builds it to `/usr/local/bin/valheim-agent-runner`
and sets up both ways to run it. They share `/etc/valheim-portal/agent-runner.env`, so a pass run
by hand is a rehearsal of what the poller does rather than a different configuration:

```bash
# on demand - the default. One pass, then it exits.
sudo systemctl start valheim-agent-runner-once
journalctl -u valheim-agent-runner-once -n 20 --no-pager

# polling - AGENT_RUNNER_SERVICE=true in deploy/install.conf, then reinstall.
journalctl -u valheim-agent-runner -f
```

Setting `AGENT_RUNNER_SERVICE` back to `false` and reinstalling stops and disables the poller, so
the switch describes the machine rather than the intention. Enabling it while the bridge is off is
refused at preflight: the runner would poll a portal answering 503.

Running it directly is still the fastest way to check wiring against a portal on another port:

```bash
sudo -n env PORTAL_BASE_URL=http://127.0.0.1:18080 \
  PORTAL_AGENT_BRIDGE_TOKEN_FILE=/etc/valheim-portal/agent-bridge-token \
  valheim-agent-runner -once
```

It holds no model credential - omp owns authentication - so it cannot run as the agent account,
which has no login and runs with `ProtectHome`. It runs as an operator account instead, and the
boundary is what that account cannot reach: no world tree, no docker socket, no `sudo`, no read
access to any world's `.env`, and no git credential. Those limits are enforced by absence rather
than by rules.

## The loop, from the operator's side

On demand nothing is automatic, and that is the whole point: a message waits in the conversation
until a pass is triggered, so the agent acts when an operator decides it should.

1. Send a message on `/admin/agent`. It is stored immediately; `agent_messages` gains a row with
   role `operator`.
2. A pass starts by itself. The portal writes its wake file, a systemd path unit
   (`valheim-agent-runner-wake.path`) sees the write and starts `valheim-agent-runner-once`, and the
   runner reads from its persisted cursor - so it answers what it has not answered before and
   nothing else. `sudo systemctl start valheim-agent-runner-once` does the same thing by hand.

   The portal holds no host access, so it cannot start a unit: the file is the whole signal and
   systemd owns the reaction. A deployment with no wake file configured behaves exactly as it did
   before - the operator triggers passes.
3. The page shows the reply without a refresh. It polls `/admin/agent/status.json` - the latest
   message id, the pending-approval count, and whether the agent owes a turn - and reloads only
   when that token changes: every 2 seconds while a reply is owed, 5 while an approval waits, 30
   when idle. While there is text in the message box it does not reload at all; it says so instead,
   because a half-written message disappearing reads as a broken page.

   While the agent owes a turn the page shows a spinner and an elapsed counter that ticks locally
   once a second. The motion is the point: a static "working" label and a dead page look identical.
   After 90 seconds it becomes a warning naming the units to check, because at that age the likely
   answer is that nothing is running rather than that the model is slow.

   "The agent owes a turn" is computed - the newest message's role is `operator` - not tracked. A
   flag would need setting when a pass starts and clearing when it ends, and a runner killed
   between the two would leave the page claiming work forever.
4. A mutating verb stops at `pending_approval` and waits there for **Approve** or **Deny**. The
   runner reports what it is waiting for and stops rather than treating the wait as a failure.
   Deciding writes the wake file too, so an approval continues the work without a second step.

In polling mode step 2 disappears and everything else is identical, which is why both units share
one environment file: what you rehearse on demand is what the poller does.

What it will not do, each covered by a test:

- ask for a verb outside the vocabulary the portal reports, or one reported unavailable
- retry a forbidden verb, or look for another route to the same effect
- treat "awaiting approval" as a failure; it says what it is waiting for and stops
- start new work while an approval is outstanding
- answer a system turn as though it were a request from the operator
- re-answer a question after a restart: the cursor is persisted, and a single pass respects it

The model is asked for one JSON object - `{"say": ..., "verb": ..., "args": {...}}` - and anything
else is reported to the operator as an unusable answer rather than guessed at. One verb per reply:
if more is needed, it says so and waits.

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
