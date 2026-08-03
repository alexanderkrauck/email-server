# Security

This software asks you for the password to your email. That deserves a straight
account of what it does with it, what it protects against, and what it does not.

## Reporting a vulnerability

Email **alexander.krauck@gmail.com** with `mailindex-mcp security` in the
subject. Please do not open a public issue for anything exploitable.

Include what you did, what happened, and what you expected. A proof of concept
helps but is not required. You will get an acknowledgement within a week. This
is a single-maintainer project, so please calibrate your expectations of
response time accordingly — but a real vulnerability will be treated as the
most important thing in the queue.

## What is protected

- **Mailbox credentials are encrypted at rest** with a key you supply in
  `CREDENTIAL_ENCRYPTION_KEY`, separate from your login identity. They are
  write-only inputs: no API response or MCP tool ever returns one.
- **Every row is owner-scoped.** Ownership comes from the authenticated token;
  callers never supply an owner id, so a caller cannot ask for another tenant's
  mail by guessing an id.
- **Attachment binaries are never stored.** They are refetched from the provider
  through a signed URL that expires in five minutes.
- **Search cursors are signed** and bound to the query that produced them, so a
  cursor cannot be edited to page through results a filter excluded.
- **Writes are confirmed before they are recorded.** Nothing is written to the
  local index that the mail server did not acknowledge.

## What is not protected, and you should know it

- **`development` mode has no authentication at all.** It is bound to
  `127.0.0.1` by Docker and is meant for trying the software on your own
  machine. Anything that reaches it can read all mail in it. Never expose it.
- **`REGISTRATION_MODE=open` disables the allowlist entirely.** With it set, any
  Google account can register on your server; `ALLOWED_GOOGLE_EMAILS` is not
  read at all. Tenant isolation still prevents them from seeing your mail, but
  they get an account on your machine and can attach mailboxes through it. Use
  `allowlist` on anything reachable from the internet.
- **The database holds the plain text of your mail**, including text extracted
  from attachments. Anyone with database access, a backup, or the disk has your
  correspondence. Encrypt the volume if that matters to you.
- **There is no audit log for reads.** Sends are recorded; searches, message
  reads and mailbox writes are not.
- **No secret rotation.** Changing `CREDENTIAL_ENCRYPTION_KEY` makes existing
  stored credentials undecryptable; you would re-enter each mailbox password.
- **Dependencies are pinned but not automatically monitored.** There is no
  Dependabot or scheduled scan yet.

## Deployment expectations

The three compose files are not interchangeable in this respect:

| File | Auth | Safe to expose |
|---|---|---|
| `docker-compose.yml` | none by default | **no** — loopback only |
| `docker-compose.single-user.yml` | static bearer token, TLS via Caddy | yes |
| `docker-compose.production.yml` | Google OAuth, TLS via Caddy | yes, with `REGISTRATION_MODE=allowlist` |

`EMAILSERVER_API_TOKEN` must be at least 32 characters; the server refuses to
start otherwise. Generate every secret with `openssl rand -base64 32`.

## Supported versions

Pre-1.0. Only the latest release receives fixes. There is no backporting.
