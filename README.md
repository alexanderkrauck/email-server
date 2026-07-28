# Email Server

Multi-user email sync, search, attachment retrieval, and sending through FastAPI and an OAuth-protected MCP endpoint.

## Security Model

- Google OpenID Connect identifies an application user by the stable Google `sub` claim.
- Each user owns multiple Gmail, Zoho, or generic IMAP/SMTP accounts.
- Mailbox credentials are separate from login identity and encrypted at rest.
- Account administration is available through the authenticated HTTP API and explicit MCP tools.
- MCP derives ownership from the authenticated token; callers never supply an owner ID.
- Mailbox passwords are write-only tool/API inputs and are never returned.
- Original attachment binaries are not stored. A signed URL refetches them from the provider on demand.
- Development mode is unauthenticated and must remain bound to loopback.

Google login does not grant access to arbitrary mailboxes. Gmail can be connected with a separate Gmail OAuth consent flow. Zoho and generic IMAP accounts use provider app passwords.

## Local Development

```bash
docker compose up -d --build
curl http://localhost:8002/api/v1/health
```

Local endpoints:

- HTTP API: `http://localhost:8002/api/v1`
- MCP: `http://localhost:8002/mcp`
- OpenAPI: `http://localhost:8002/api/v1/docs`

The development composition binds HTTP and SMTP to `127.0.0.1` and uses the migrated `development-owner` user.

## Production Google Setup

Create a Google OAuth Web application and configure:

- Authorized JavaScript origin: `https://mail.example.com`
- MCP callback: `https://mail.example.com/auth/callback`
- Gmail connection callback: `https://mail.example.com/api/v1/accounts/gmail/callback`

Create a private `.env`:

```dotenv
EMAILSERVER_DOMAIN=mail.example.com
POSTGRES_PASSWORD=replace-with-a-long-random-value
GOOGLE_CLIENT_ID=123.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=replace-me
JWT_SIGNING_KEY=replace-with-a-long-random-value
CREDENTIAL_ENCRYPTION_KEY=replace-with-a-long-random-value
SESSION_SECRET=replace-with-a-long-random-value
REGISTRATION_MODE=allowlist
ALLOWED_GOOGLE_EMAILS=["owner@example.com","second-user@example.com"]
ALLOWED_GOOGLE_SUBJECTS=[]
CLAIM_LEGACY_ACCOUNTS_ON_FIRST_LOGIN=true
```

Start the HTTPS deployment:

```bash
docker compose -f docker-compose.production.yml up -d --build
```

Caddy obtains and renews TLS certificates. PostgreSQL, FastMCP OAuth state, signing material, and encrypted credential keys use persistent volumes or explicit deployment secrets.

For the first production login after upgrading an existing installation, set `CLAIM_LEGACY_ACCOUNTS_ON_FIRST_LOGIN=true` and allowlist exactly the intended owner. After that user claims the existing accounts, set it back to `false`.

## Claude Connection

Add this remote MCP URL:

```text
https://mail.example.com/mcp
```

The server returns an OAuth challenge. Claude discovers the authorization metadata, registers its callback, redirects the user to Google, and then uses the FastMCP-issued audience-bound bearer token.

Claude's callback is allowed by the MCP server configuration, not by the Google OAuth client:

```text
https://claude.ai/api/mcp/auth_callback
```

The Google OAuth Web client needs the server callbacks instead:

```text
https://mail.example.com/auth/callback
https://mail.example.com/api/v1/accounts/gmail/callback
```

## ChatGPT Connection

Add the same remote MCP URL:

```text
https://mail.example.com/mcp
```

ChatGPT publishes connector-specific client metadata and uses a callback shaped
like:

```text
https://chatgpt.com/connector/oauth/<connector-id>
```

The server allows only that path under `chatgpt.com`. Do not add this callback
to the Google OAuth client. Google still redirects to the server-owned
`https://mail.example.com/auth/callback`; FastMCP then redirects the completed
authorization back to ChatGPT.

## MCP Tools

- `list_mail_accounts`
- `add_mail_account`
- `update_mail_account`
- `begin_mail_account_password_setup`
- `begin_gmail_connection`
- `search_mail`
- `search_mail_regex`
- `get_mail`
- `get_thread`
- `get_attachment`
- `send_mail`

`list_mail_accounts` includes non-secret connection settings and exact stored message counts.
`add_mail_account` and `update_mail_account` accept optional write-only passwords for
IMAP/SMTP accounts. When a client will not transmit passwords, omit the password and
use the returned setup URL or call `begin_mail_account_password_setup`; the linked form
asks only for the password. Failed connection tests retain both configuration and
encrypted credentials so settings can be corrected without entering the password again.
`begin_gmail_connection` returns a five-minute signed URL for Google consent.
Deletion, standalone connection tests, and manual sync remain outside the MCP surface.

`search_mail` and `search_mail_regex` return `total_count`, `raw_count`,
`returned_count`, `has_more`, and a signed `next_cursor`. Reuse the same filters
with `next_cursor` until `has_more` is false for an exhaustive result. The
default `exact` deduplication groups equal RFC `Message-ID` values while
retaining every source account and message ID; `mirror` additionally groups
normalized body copies, and `none` returns every stored row.

Search responses also report matching fields, participant-domain facets, and
per-account sync coverage. `get_mail` and `get_thread` return bounded plain text
by default; HTML must be requested explicitly. `send_mail` accepts owned
`attachment_ids` and refetches each original binary from its provider before
sending.

## Account API

Authenticated routes include:

- `GET /api/v1/me`
- `GET|POST /api/v1/accounts`
- `GET|PATCH|DELETE /api/v1/accounts/{id}`
- `POST /api/v1/accounts/{id}/test`
- `POST /api/v1/accounts/{id}/sync`
- `GET /api/v1/accounts/gmail/connect`
- `GET /api/v1/emails/search`
- `GET /api/v1/emails/search/regex`
- `GET /api/v1/emails/{id}`
- `GET /api/v1/attachments/{id}`
- `POST /api/v1/send`

Every account, message, attachment, sync, and send lookup is owner-scoped.

## Data And Synchronization

- Alembic applies versioned, data-preserving migrations.
- Message identity is unique by `(mail_account_id, provider_message_id)`; RFC `Message-ID` remains metadata.
- IMAP cursors persist UID, UIDVALIDITY, and folder.
- IMAP backfills are oldest-first and bounded per account cycle. A cursor moves
  only after its batch commits, and a UIDVALIDITY change resets that folder
  without comparing unrelated UID sequences.
- OAuth Gmail accounts use `messages.list/get` for resumable backfill and `history.list` for incremental changes.
- Expired Gmail history IDs trigger a generation-marked full sync before upstream deletions are reconciled.
- Periodic metadata-only reconciliation mirrors flags and upstream deletions;
  its durable checkpoint prevents a full metadata scan after every restart.
- Account work is bounded by a global concurrency limit and protected by
  expiring database leases, so multiple workers cannot sync one mailbox at the
  same time and a crashed worker cannot hold a permanent lock.
- PostgreSQL GIN indexes support lexical body and attachment search.
- Regex is a separate bounded search with scope, pattern, result, and statement-time limits.
- Send requests support idempotency keys and append an owner/account-scoped audit record.

## Verification

```bash
pytest -q
python -m scripts.verify_postgres_search
```

The suite covers tenant isolation, tool exposure and annotations, encryption, token tampering, attachment limits, IMAP cursor behavior, Gmail history behavior, and OAuth challenge metadata.
