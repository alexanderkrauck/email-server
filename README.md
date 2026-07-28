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

## MCP Tools

- `list_mail_accounts`
- `add_mail_account`
- `update_mail_account`
- `begin_gmail_connection`
- `search_mail`
- `search_mail_regex`
- `get_mail`
- `get_thread`
- `get_attachment`
- `send_mail`

`list_mail_accounts` includes non-secret connection settings and exact stored message counts.
`add_mail_account` and `update_mail_account` accept write-only passwords for IMAP/SMTP
accounts. `begin_gmail_connection` returns a five-minute signed URL for Google consent.
Deletion, standalone connection tests, and manual sync remain outside the MCP surface.

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
- OAuth Gmail accounts use `messages.list/get` for resumable backfill and `history.list` for incremental changes.
- Expired Gmail history IDs trigger a generation-marked full sync before upstream deletions are reconciled.
- Periodic metadata-only reconciliation mirrors flags and upstream deletions.
- PostgreSQL GIN indexes support lexical body and attachment search.
- Regex is a separate bounded search with scope, pattern, result, and statement-time limits.
- Send requests support idempotency keys and append an owner/account-scoped audit record.

## Verification

```bash
pytest -q
```

The suite covers tenant isolation, tool exposure and annotations, encryption, token tampering, attachment limits, IMAP cursor behavior, Gmail history behavior, and OAuth challenge metadata.
