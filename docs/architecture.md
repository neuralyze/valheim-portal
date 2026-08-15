# Neuralyze Valheim Portal architecture

## Trust boundaries

The public portal owns release metadata and immutable profile-definition artifacts. It holds no Docker socket and no write access to anything on the host.

It is not, however, isolated from world data. `compose.yaml` mounts the entire world root read-only at `/var/lib/valheim-worlds`, which is what makes seed metadata, map sources, and measured world status readable without an agent round-trip. That mount includes each world's `valheim.env` — the file that holds `SERVER_PASS` — and the live `.db`/`.fwl` saves. A read primitive in the portal process is therefore a read of server credentials and player saves, and the containment checks on every path the portal opens under that root are load-bearing, not defensive decoration. Nothing in the portal writes there: mutation goes through the agent.

Administrative mutations require three independent facts: a source address inside `PORTAL_TRUSTED_PROXY_CIDR`, a non-empty `PORTAL_AUTH_HEADER` identity, and an `X-Portal-Admin-Token` matching `PORTAL_ADMIN_TOKEN_FILE`. All three come from the reverse proxy, and a signed same-site CSRF token is required on top. The agent is isolated behind a Unix socket, HMAC capability, configured roots, and fixed allowlisted operations. The portal never receives a Docker socket.

The agent bridge adds a third process, and one deliberate exception to "the portal cannot cause
anything to happen on the host". The runner is an operator-account process that reads the operator
conversation over `/api/agent/*` with a bearer token and requests verbs; the portal decides what is
allowed and records what actually happened, so the runner holds no authority of its own and cannot
widen its vocabulary — it reads that vocabulary from the portal.

The exception is the wake signal. So that sending a message starts a pass, the portal writes one
file in its own data volume and a systemd path unit on the host starts
`valheim-agent-runner-once.service` when that file changes. What the portal can therefore cause is
exactly one thing: a fixed unit runs. It passes no arguments, names no command, chooses no user, and
learns nothing back — the runner's configuration comes from `/etc/valheim-portal/agent-runner.env`,
which the portal cannot read or write. The upper bound on abuse is repeated passes: `Type=oneshot`
means systemd will not run two at once, and the persisted cursor means a pass with nothing new to
answer does nothing. That bound is model spend, not host access, and it is stated in the threat
model rather than left implicit.

## Profile delivery

A published profile release is selected by world, profile, and client type. Steam world membership gates browser pages, device authorization, manifests, and profile definitions.

A portal profile card invokes the fixed `valheim-profile-sync://sync` scheme with only four non-secret fields: portal, world, profile, and client type. The local application creates an RFC 8628 device authorization transaction and displays the short user code the portal issued with it. The browser proves Steam identity and current membership, then shows a confirmation page naming the world, profile, client type, and Steam ID; the player must type the code the desktop application is showing and submit it. Only that confirmation authorizes the transaction — visiting the page does not — and it binds the browser's Steam identity to the application that started the flow, so a link alone cannot authorize anything. The portal then returns a one-use, short-lived bearer scoped to the exact release. Tokens, artifact paths, host details, and secrets never appear in profile links or manifests.

The manifest identifies an immutable profile-definition ZIP. That ZIP contains a canonical package list with exact package sizes and SHA-256 values plus profile config. The client caches verified packages, stages the complete desired profile generation, and atomically activates it. It maintains a prior generation for recovery and isolates every profile under the user's local application data.

## Steam integration

Valheim Profile Sync launches the existing Steam `valheim.exe`; it never copies the game. It owns only the minimal Doorstop bootstrap files needed for profile selection, records their hashes, and refuses to replace a bootstrap owned by another manager. All BepInEx mods and profile config remain outside the Steam installation.

## World operation policy

The portal process is unprivileged: no Docker access, and no write access to world data. It can read world data, because the world root is mounted read-only, but it changes nothing there directly. It sends timestamped, HMAC-signed requests over a group-restricted Unix socket. The host agent accepts only fixed scripts and typed scalar arguments, resolves both script and world roots against symlink escapes, refreshes controlled worlds for each request, and sanitizes command output.

State-changing operations use fixed sequences: stop/build back up first; port changes back up, stop, validate under a host lock, update atomically, and restart; mod deployment backs up, stops, stages an immutable profile, and restarts; restore creates a fresh backup, stops, and replaces only one validated save pair; permanent deletion backs up, stops, and removes only the resolved allowlisted server directory. Per-world agent locks prevent lifecycle operations from racing. Server creation validates a password-bound signed request, reserves ports, creates a complete staging tree, atomically renames it, and optionally starts plus health-checks it. Publication remains disabled unless readiness succeeds. Portal unregister and permanent-delete cleanup revoke memberships transactionally; permanent cleanup also archives current releases while preserving artifacts, external backups, jobs, and audit events.

Read-only profile-catalog and FWL-metadata operations return size-limited valid JSON. The creation wizard uses the catalog to expose controlled templates, while player pages derive the displayed seed from current FWL metadata rather than copied database state.
