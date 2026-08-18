# Project instructions for AI agents

This file governs automated work in this repository. It is authoritative here and supersedes
instructions inherited from any parent directory - this checkout sits inside another project
whose rules are written for a Godot game, not for this one.

## What this repository is

A self-hosted portal that manages modded Valheim servers and the client profiles players download.
Go for the portal and agent, Bash under `hostops/` for host operations, Python under `tools/` for
mod management, C# under `tools/vrfixes/` for the VR client plugin.

## Scope

The machine-readable version of everything below is `policy.yaml`, and
`docs/agent-harness.md` explains how a harness consumes it. When this file and
`policy.yaml` disagree, `policy.yaml` wins and CI says so.


Primary purpose: managing and developing the Valheim deployment — mods, profiles, worlds, the VR
plugin. Changing the portal's own code is permitted but is not the point of the work.

**C# is legitimate in this repository.** `tools/vrfixes/` and `tools/worldseed/` are C# by design,
because Valheim is a Unity game and BepInEx plugins are C# assemblies. A parent project forbids
authoring C#; that rule is about Godot game code and does not apply here.

**Three client types are live, not one.** Every world publishes `<world>-flatvr` (monitor, with the
ValheimVR companion installed), `<world>-nonvr` (monitor, ValheimVR stripped) and `<world>-vr`
(headset). Hrafnheim currently serves all three, and the player guide ships a desktop edition and a
VR edition from one source. Flat is a real target: do not dismiss flat performance, flat keybinds or
mouse-driven mod UI as irrelevant.

What VR genuinely cannot do is separate: VHVR maps controllers to ZInput game actions only, so a mod
that reads its own BepInEx keybind, or needs a typed search box, is unreachable in a headset even
though it works on a monitor. That is an input limit, not a reason to treat flat as absent.

## Where you may write

```
repos/valheim-portal-main        the dev checkout - this is where code changes belong
/srv/valheim-portal              host copy the scripts run from - NEVER write directly
/media/big3/.../ValheimConfig/portal   the LIVE deployment - NEVER write directly
/media/big4/projects/game/valheim/<World>   world data - only through hostops verbs
```

World directories hold running servers, player saves and production secrets in `.env`. Change them
through `hostops/manage_mods.sh` and the portal's operations, never with direct edits, and never
run a command that discards untracked files there.

## Approval

```
read            logs, versions, diffs, changelogs, manifests           proceed
repo-write      code, tests, branches in this checkout                 proceed
world-state     deploy --apply, start, stop, mod add/remove            ask every time
player-facing   publishing a profile players download                  ask every time
upstream        pushing to a parent repository                         not permitted yet
```

`deploy --apply` requires a fresh confirmation on every invocation, including repeats on other
worlds. A deploy whose plan shows no changes must be refused rather than confirmed: it takes a world
down and restarts it for nothing.

## Evidence

1. **Never claim something is shipped without reading it back out of the published artifact.** On
   13 Aug a fix committed at 00:11 was reported as live in a release built seventeen hours earlier.
   The operator ran four test sessions against a build that could not contain it.
2. **Quote the source line that proves a setting's direction before changing it.** A mod's own
   config description, or the code that binds it. `LODBiasMax` was shipped backwards because its
   direction was asserted from memory instead of read.
3. **One behavioural change per publish.** Six changes in one release leave nothing to verify.
4. **Say "unmeasured" instead of "negligible".** A once-per-second scene sweep was dismissed as too
   small to matter, without ever being timed. It was the cause of a day-long frame-rate collapse.

## Reporting to the operator

The operator is the only reader. Length is a cost they pay, not a measure of work done.

1. **Answer the question, then stop.** No evidence trail, no implications, no next steps, no
   recommendations unless asked. On 18 Aug a two-line question about one mod was answered with
   thirty lines of enum decoding, and the session before it ended with the operator quitting.
2. **Verify silently. Report a finding only when it contradicts what was asked for.** Checking is
   the job; narrating the check is not. "I confirmed X from source" is worth one line at most, and
   usually zero.
3. **Never restate what the operator has already said.** Repeating their own reasoning back reads
   as not having listened, which is how it is meant.
4. **When told a fact, use it.** Do not re-derive it to see if it holds. If it turns out to be
   wrong, say so in one line at the point it matters.
5. **When told to stop discussing something, stop - including in closing summaries.** Listing
   "still outstanding" items after being told they are not issues is the same error twice.

`detail` is the word that lifts these; until it is said, assume the short form.

## Task tracking

`bd` (beads) is the source of truth for outstanding work — not chat, not markdown checklists.

```bash
bd list --status=open     # what is actually outstanding
bd note <id> "..."        # evidence: release ids, artifact paths, file and line
bd remember "..." --key   # durable facts, each with its citation
```

A verb that fails after changing anything must file or update a bead before returning. Three worlds
once ended in a half-finished mod removal with no record anywhere.

`bd init` in this checkout inherits the parent workspace's `sync.remote` and clones an unrelated
project's tracker. Always pass `--remote ''` here.

## Validation

What CI runs, and what should pass before a commit that touches these areas:

```bash
gofmt -l .                       # must print nothing
go mod tidy && git diff --exit-code go.mod go.sum
go build ./... && go vet ./... && go test -race ./...
GOOS=windows go build ./...      # the profile-sync client is a Windows binary
shellcheck hostops/*.sh
python3 -m pytest tools/ -q
```

## Guard rails that already exist

- `manage_mods.sh` refuses to run against a running server.
- `start_valheim_server.sh` refuses to start a world with a pending client-release cutover.
- Removals write a backup and an audit record before mutating anything.
- The VR runtime builder refuses an exclusion that matches nothing, or that would drop a required
  file, and validates the rewritten archive before replacing its output.

Do not route around these. If one blocks the work, that is the answer, not an obstacle.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
