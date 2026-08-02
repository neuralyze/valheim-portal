# Security policy

## Reporting a vulnerability

Report privately. **Do not open a public issue.**

Use a GitHub private security advisory: open the
repository's **Security** tab and choose **Report a vulnerability**. That keeps
the report, the discussion, and the fix in one place until a release exists.

Advisories are the only private channel this project offers. There is
deliberately no contact address here rather than an unmonitored one: a mailbox
nobody reads is worse than none, because it looks like a channel. If you cannot
use advisories, open a public issue saying only that you have a security report
and asking for a private channel — no details.

Useful reports name the trust boundary they cross. A request transcript, the
relevant `PORTAL_*` settings, and the observed versus expected authorization
outcome are worth more than a scanner score.

We aim to acknowledge a report within 7 days. This is a small volunteer-run
project, so that is an intention rather than a service-level agreement; a fix
lands when someone has the time to write and verify one.

## Supported versions

Only the latest tagged release receives security fixes. `v0.1.0` is the first
tagged release. Older tags and arbitrary commits from the default branch are not
supported: if you are running one, upgrade before reporting.

## Why this project needs careful reporting

The portal is a web application whose privileged half executes host scripts, so
the usual severity intuitions do not apply.

**The agent is root-equivalent.** The host agent runs as a dedicated system user
that is a member of the `docker` group. Membership in `docker` is root-equivalent
on the host, because anything that can talk to the Docker socket can mount the
host filesystem into a container it controls.

**The agent executes a fixed table of 20 shell scripts.** They are enumerated in
`portal/docs/repository-layout.md` and documented in `docs/script-reference.md`
in the outer repository. Between them they stop, rebuild, back up, restore, and
permanently delete Valheim server directories. There is no read-only subset:
`portal_delete_valheim_server.sh` and `restore_valheim_world.sh` destroy data by
design.

**The portal container mounts the entire world tree read-only.** That mount
includes every world's `valheim.env`, which holds `SERVER_PASS`, and the live
`.db`/`.fwl` saves. Read-only is a write control, not a confidentiality control:
any path traversal or arbitrary-read primitive inside the portal process exposes
server passwords and world saves.

Therefore a bypass of any of the following is a host privilege-escalation bug,
not merely a web bug, and should be reported as such:

* the admin identity check (trusted-proxy CIDR, `PORTAL_AUTH_HEADER`, and the
  `X-Portal-Admin-Token` file comparison);
* the HMAC-authenticated agent socket;
* the agent's world allowlist (`AGENT_ALLOWED_WORLDS`);
* script or world path resolution on either side of the socket.

**The Windows client registers a URL scheme.** `ValheimProfileSync.exe`
registers `valheim-profile-sync://`, so any web page the player visits can
invoke it with attacker-chosen arguments. Argument parsing, path handling, and
profile-authorization checks in the client are in scope.

## Out of scope

* Findings that already require host root, or write access to the world root.
  Both are inside the trust boundary by construction.
* SmartScreen and antivirus warnings on the unsigned Windows executable. Known
  and tracked; code signing is a cost decision, not a defect.
* Denial of service against the operator's own game containers by an operator
  or an already-authorized administrator.
