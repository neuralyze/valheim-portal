# Threat model and security boundaries

## Protected assets

- Dedicated-server saves, environment files, webhooks, and Steam credentials are sensitive. They are **not** out of the portal's reach: the container mounts the whole world root read-only, so `<world>/valheim.env` (containing `SERVER_PASS`) and every live `.db`/`.fwl` save are readable by the portal process. Treat arbitrary-read in the portal as credential and save-file disclosure.
- Docker access and all write access to world data remain host-private, held only by the agent.
- Profile definitions, package pins, checksums, publisher identity, and audit events are integrity-sensitive.
- FastLink and profile configuration are available only to Steam accounts granted access to the associated world.

## Controls

- `/admin` requires all three of a trusted-proxy source address, a non-empty identity header, and an `X-Portal-Admin-Token` matching `PORTAL_ADMIN_TOKEN_FILE`, plus signed CSRF, request limits, and per-client rate limiting. The token is the factor the portal verifies itself; the other two are assertions the proxy makes. Under the shipped compose deployment Docker NATs every request to the bridge gateway, so the source-address half is true for any container on the host and cannot carry the boundary alone.
- Browser profile pages require a valid Steam session and current world membership.
- Custom profile links contain no bearer, cookie, artifact URL, host path, or secret. They carry only the portal, world, profile, and client type.
- Device authorization binds Steam ID, world, profile, client type, and release ID. Authorization needs an explicit, CSRF-protected confirmation carrying the short user code the desktop application is displaying — following the page link is not consent, so a link a player is tricked into opening authorizes nothing. Five wrong user-code submissions destroy the grant, which bounds guessing at the short code's entropy. Exchange codes are opaque, short-lived, one-use, and expire on cleanup. Bearers are short-lived and recheck membership and current release scope when used.
- Published artifacts are immutable application records. The portal checks path containment, byte size, and SHA-256 before serving.
- Profile ZIPs reject traversal, duplicate paths, symlinks, unexpected roots, malformed manifests, wrong scope, duplicate packages, and unsafe metadata.
- The client verifies profile ZIP and every package ZIP size/SHA-256 before extracting into a staged profile generation. It rejects unsafe package paths, collisions, oversized archives, corrupted state, and concurrent synchronizations.
- Activation is atomic. Failed downloads, invalid manifests, extraction failures, or interrupted updates leave the last active generation intact.
- The Steam installation is never copied. The client records hashes for its two loader files and refuses to replace an unmanaged loader.
- The host agent accepts only HMAC-authenticated, time-bounded fixed operations; it never accepts shell fragments, arbitrary paths, or Docker requests from the portal.
- The world root is mounted read-only and every portal read under it is path-contained and size-limited. Read-only is a mount option, not a capability check: it stops writes, not reads.
- Request bodies are bounded: 64 KiB for ordinary forms, 512 MiB for an artifact upload, over which the portal answers HTTP 413. A multipart upload is buffered to 16 MiB and then spills to the container's `/tmp` tmpfs, which is size-capped, and `compose.yaml` sets `mem_limit` and `pids_limit`. The ceilings are one mechanism: an unbounded body would become unbounded tmpfs, and unbounded tmpfs is container memory. An upload big enough to matter now fails as a 413 rather than as an OOM kill.
- The agent bridge is off unless a deployment sets `PORTAL_ENABLE_AGENT_BRIDGE=true`. When on, `/api/agent/*` requires a bearer matching `PORTAL_AGENT_BRIDGE_TOKEN_FILE` (32 characters minimum), and the runner that presents it holds no authority of its own: it reads its whole vocabulary from the portal, cannot request a verb the portal does not offer, and every mutating verb stops for an operator's Approve or Deny unless the deployment named it in `PORTAL_AGENT_AUTO_APPROVE`, which covers `world_state` only and is empty by default. Forbidden verbs are refused by class, not by argument.
- The runner runs as an operator account, never as `valheim-agent` and never as root. It has no world tree, no Docker socket, no `sudo`, and no git credential. It shells out to omp, which owns model authentication, so the portal and the runner between them hold no model key. The account that has credentials is deliberately the account with no reach.
- The wake signal is a one-bit, one-way channel from the portal to the host: the portal writes a file in its own data volume and a systemd path unit starts `valheim-agent-runner-once.service`. The portal supplies no arguments, no command, no user, and no environment - the runner's configuration lives in `/etc/valheim-portal/agent-runner.env`, which the portal can neither read nor write - and learns nothing back from the unit. `Type=oneshot` prevents concurrent passes and the persisted cursor makes a pass with nothing new a no-op.

## Residual risks

- Package availability depends on the fixed Thunderstore CDN. The release captures expected size and SHA-256, so unavailable or changed remote bytes fail closed.
- Windows executable signing requires an organization code-signing certificate. Until one is configured, users must verify the release SHA-256 before running the installer.
- A local user with write access to the profile root can alter their own client installation. Portal and server trust do not depend on that local state.
- Rotate the CSRF secret and agent token after suspected exposure, restart services, and inspect the audit trail.
- The read-only world mount is a deliberate trade. It buys seed metadata, map sources, and measured world status with no agent round-trip, and it costs the portal process the ability to read `SERVER_PASS` and every save file. A path-traversal or arbitrary-read defect in the portal is therefore a credential-disclosure defect. Removing the mount would mean routing all of that through the agent instead.
- Rotate `PORTAL_ADMIN_TOKEN_FILE` after suspected exposure of the proxy configuration, and reload the proxy so it sends the new value. An admin token leaked to a client is not self-limiting: it is a bearer for administration for as long as it is valid.
- A compromised portal process can cause repeated runner passes by writing its wake file in a loop. The ceiling is model spend and log noise, not host access: the unit it starts is fixed, takes nothing from the portal, and cannot run twice concurrently. A deployment that will not accept that ceiling should leave `PORTAL_ENABLE_AGENT_BRIDGE=false`, which installs no runner and no watcher at all, or run the poller instead, which disables the watcher and takes the portal out of the loop entirely.
- The bridge token is a bearer for the whole agent surface for as long as it is valid. It grants no mutation by itself while `PORTAL_AGENT_AUTO_APPROVE` is empty - every mutating verb waits for an operator - and it grants reads either way, which include world status and mod inventories. Rotate `/etc/valheim-portal/agent-bridge-token` after suspected exposure and restart the portal and the runner.
- An operator approving a verb is the last line, and it is a human one. The page shows the full arguments, the class, and what the world already serves precisely because "Approve" is otherwise a habit; a publish approval also shows how many releases went out in the last day, which is the brake that replaces a rate limit.
- Auto-approval (`PORTAL_AGENT_AUTO_APPROVE`) removes that human line for the verbs it names, and
  that is the whole of what it does: an agent asking to stop a world stops it. It is off by
  default, covers `world_state` only, and can never cover a release players download or the two
  destructive verbs. Nothing is hidden - the row, the audit entry and the conversation turn are
  identical to an approved call, with `auto-approve (policy)` as the decider - but nothing is
  reviewed either. A deployment with more than one operator, or one exposed to players, should
  leave it empty; the case it exists for is a single operator batching work on their own box.
