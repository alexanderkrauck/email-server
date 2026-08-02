# Email Server

**Your mail, indexed and searchable, as a remote MCP server you host yourself.**

Most email MCP servers are a local process that forwards each question straight to
IMAP. That works for "show me my last 10 messages" and falls apart on "find every
invoice I was sent in 2024" — IMAP `SEARCH` is inconsistent between providers, it
cannot see inside attachments, and it never tells the model whether it actually
searched everything.

This one keeps its own copy. It syncs your mailboxes into PostgreSQL, extracts text
from attachments, indexes all of it, and answers questions over that index with
**exact counts, stable pagination, and an explicit report of how much of each
mailbox it has seen**. It runs as an authenticated HTTP endpoint, so you paste a URL
into your AI client instead of installing anything on your laptop.

![Claude answering a question about a mailbox through this server](docs/demo.gif)

<sub>A real question against a live index of about 59,000 messages across six
accounts. One string is blurred: a case number belonging to a real filing.</sub>

## Why this instead of the other email MCP servers

|  | This project | Typical email MCP |
|---|---|---|
| Transport | Remote HTTP endpoint | Local stdio process on your machine |
| Setup on the client | Paste a URL | Install a runtime, edit config JSON, store credentials locally |
| Search | Own PostgreSQL index, GIN full-text, stemmed across languages | Live IMAP `SEARCH` per call |
| Writing | Move, mark, delete, draft — confirmed by the server before the index records it | Read-only, or unguarded |
| Result completeness | Exact `total_count`, signed cursors, per-account coverage | Whatever the folder returned |
| Attachments | Text extracted and indexed at sync time (PDF, DOCX, XLSX, PPTX, OCR) | Base64 into the model's context, or not at all |
| Attachment binaries | Never stored; refetched through a 5-minute signed URL | Stored on disk or inlined |
| Users | Multi-tenant, every row owner-scoped | Single user |
| Tool surface | 16 tools, all annotated | Frequently 40+ |

The trade is honest: you run a database and a container. In exchange the model can
actually answer questions about your mail history, and your mail never leaves your
own machine.

## Quickstart

Five minutes, local only, no accounts to create anywhere.

```bash
git clone https://github.com/alexanderkrauck/email-server.git
cd email-server
cp .env.example .env
docker compose up -d --build
```

Check it came up:

```bash
curl http://localhost:8002/api/v1/health
```

The default `development` mode is **unauthenticated** and Docker binds it to
`127.0.0.1` only. It is meant for exactly this: trying the thing out on your own
machine.

### Connect a mailbox

Use an app password, not your login password. Gmail, Zoho, Fastmail, iCloud and
most other providers issue these in their security settings.

```bash
curl -X POST http://localhost:8002/api/v1/accounts \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "personal",
    "account_name": "you@example.com",
    "username": "you@example.com",
    "password": "your-app-password",
    "host": "imap.example.com",
    "port": 993,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465
  }'
```

Synchronization starts on its own. Watch it fill up:

```bash
docker compose logs -f email-server
```

### Connect your AI client

```bash
claude mcp add --transport http mail http://localhost:8002/mcp
```

For other clients, point them at `http://localhost:8002/mcp` over streamable HTTP.
Then ask something your inbox search would struggle with — a phrase inside a PDF
someone sent you three years ago works well.

## Deployment modes

`EMAILSERVER_AUTH_MODE` picks how callers are authenticated. Mailbox credentials are
always separate from this.

| Mode | Who can call it | What you need | Works with |
|---|---|---|---|
| `development` | anyone on loopback | nothing | local clients only |
| `single_user` | one owner with a static bearer token | a token, a domain | Claude Code, Cursor, `mcp-remote`, anything that sends a header |
| `google` | multiple users via Google OAuth + dynamic client registration | a Google Cloud OAuth client, a domain | Claude.ai and ChatGPT web connectors, plus all of the above |

### Production, single user, no Google project

The shortest path to a real deployment. Caddy obtains and renews TLS.

```bash
cat > .env <<'ENV'
EMAILSERVER_DOMAIN=mail.example.com
POSTGRES_PASSWORD=...
EMAILSERVER_API_TOKEN=...
CREDENTIAL_ENCRYPTION_KEY=...
SESSION_SECRET=...
ENV

docker compose -f docker-compose.single-user.yml up -d --build
```

Generate each secret with `openssl rand -base64 32`. `EMAILSERVER_API_TOKEN` must be
at least 32 characters; the server refuses to start otherwise.

```bash
claude mcp add --transport http mail https://mail.example.com/mcp \
  --header "Authorization: Bearer $EMAILSERVER_API_TOKEN"
```

### Production, multiple users, Google OAuth

Needed if you want Claude.ai or ChatGPT web connectors, which negotiate OAuth and
cannot send a static header.

Create a Google OAuth **Web application** and configure:

- Authorized JavaScript origin: `https://mail.example.com`
- Authorized redirect URIs:
  - `https://mail.example.com/auth/callback`
  - `https://mail.example.com/api/v1/accounts/gmail/callback`

Do **not** add the AI vendor's callback to the Google client. The MCP server allows
`https://claude.ai/api/mcp/auth_callback` and `https://chatgpt.com/connector/oauth/*`
itself, then redirects the completed authorization back to the client.

```bash
cat > .env <<'ENV'
EMAILSERVER_DOMAIN=mail.example.com
POSTGRES_PASSWORD=...
GOOGLE_CLIENT_ID=123.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
JWT_SIGNING_KEY=...
CREDENTIAL_ENCRYPTION_KEY=...
SESSION_SECRET=...
REGISTRATION_MODE=allowlist
ALLOWED_GOOGLE_EMAILS=["owner@example.com"]
ENV

docker compose -f docker-compose.production.yml up -d --build
```

Add `https://mail.example.com/mcp` in the client. It receives an OAuth challenge,
discovers the authorization metadata, registers its callback, and sends the user
through Google.

Two things to expect from Google: an unverified-app warning until you submit the
consent screen for review, and a 100-user cap while unverified. Neither matters for a
personal or family deployment.

Upgrading an existing single-owner installation: set
`CLAIM_LEGACY_ACCOUNTS_ON_FIRST_LOGIN=true`, allowlist exactly the intended owner, let
that user log in once to claim the existing accounts, then set it back to `false`.

## Security model

- Google OpenID Connect identifies an application user by the stable `sub` claim.
- Each user owns multiple Gmail, Zoho, or generic IMAP/SMTP accounts.
- Mailbox credentials are separate from login identity and encrypted at rest.
- MCP derives ownership from the authenticated token; callers never supply an owner ID.
- Mailbox passwords are write-only tool and API inputs, and are never returned.
- Original attachment binaries are not stored. A signed URL refetches them on demand.
- Development mode is unauthenticated and must remain bound to loopback.

Signing in does not grant access to any mailbox. Gmail is connected through a separate
Gmail OAuth consent flow; Zoho and generic IMAP use provider app passwords.

## MCP tools

Sixteen tools, each annotated with read-only, destructive and open-world hints.
It is a mail client, not a search box: everything you can do in Thunderbird you
can do here, over an index instead of a folder listing.

| Tool | |
|---|---|
| `list_mail_accounts` | accounts, non-secret settings, exact stored message counts |
| `add_mail_account` | add an IMAP/SMTP mailbox |
| `update_mail_account` | change one mailbox's settings |
| `begin_mail_account_password_setup` | short-lived password-only browser form |
| `begin_gmail_connection` | five-minute signed URL for Google consent |
| `search_mail` | exhaustive lexical search |
| `search_mail_regex` | bounded regex search |
| `get_mail` | one message, bounded body |
| `get_thread` | reconstructed thread with confidence |
| `get_attachment` | metadata, extracted text, expiring download URL |
| `send_mail` | send or reply, with owned attachments |
| `list_mail_folders` | folders of one mailbox, with declared roles and indexed counts |
| `mark_mail` | set or clear read and flagged state |
| `move_mail` | move a message to another folder |
| `delete_mail` | move to Trash; `permanent` only from Trash |
| `save_draft` | write a draft into the mailbox's Drafts folder |

Standalone connection tests and manual sync are deliberately outside the MCP
surface.

**Writes.** Every write goes to the mailbox first and is only recorded locally
once the server confirms it, so the index never claims a change that did not
happen. They hold the same lease the synchronizer uses, keyed on
`(host, port, username)` rather than on an account row, because two accounts can
name one physical mailbox and an untagged `EXPUNGE` landing during a folder
census renumbers the sequence numbers that census is reading. Writes address the
live copy of a message rather than one sitting in Trash. `delete_mail` moves to
Trash and needs a second, explicit call to destroy anything.

**Passwords.** `add_mail_account` and `update_mail_account` take an optional
write-only password. When a client will not transmit secrets, omit it and open the
returned setup URL, or call `begin_mail_account_password_setup`; the linked form asks
only for the password. A failed connection test keeps both the configuration and the
encrypted credential, so settings can be corrected without re-entering it.

**Search.** `search_mail` and `search_mail_regex` return `total_count`, `raw_count`,
`returned_count`, `has_more`, and a signed `next_cursor`. Reuse the same filters with
`next_cursor` until `has_more` is false for an exhaustive result. Deduplication
defaults to `exact`, which groups equal RFC `Message-ID` values while retaining every
source account and message ID; `mirror` also groups normalized body copies; `none`
returns every stored row. Responses additionally report matching fields,
participant-domain facets, and per-account sync coverage.

**Stemming.** `match` defaults to `stemmed`, which finds inflected forms of a
word: on a real 52,000-message mailbox `invoices` goes from 104 hits to 1,016 and
`Verträge` from 133 to 1,168. The cost is precision — `meeting` also matches
`meet` — so pass `match="exact"` for order numbers, identifiers and surnames,
which a stemmer would widen. Both modes are indexed; every response reports which
one ran.

**Read state.** `is_unread`, `is_flagged` and `is_answered` filter on flags
mirrored from the provider. They are tri-state: a message whose provider never
reported flags matches neither `true` nor `false`, and search returns a
`FLAG_STATE_UNKNOWN` warning saying how many messages that removed, rather than
quietly reporting them as read. Gmail publishes no answered label, so
`is_answered` is always unknown for Gmail OAuth accounts. Flags are refreshed by
the periodic reconciler, so they lag the mailbox by up to
`EMAILSERVER_DELETION_RECONCILE_INTERVAL`.

`get_mail` and `get_thread` return bounded plain text by default; HTML must be
requested explicitly. `send_mail` accepts owned `attachment_ids` and refetches each
original binary from its provider before sending.

## HTTP API

`GET /api/v1/docs` serves the OpenAPI browser. Every account, message, attachment,
sync and send lookup is owner-scoped.

```text
GET    /api/v1/me
GET    /api/v1/accounts
POST   /api/v1/accounts
GET    /api/v1/accounts/{id}
PATCH  /api/v1/accounts/{id}
DELETE /api/v1/accounts/{id}
POST   /api/v1/accounts/{id}/test
POST   /api/v1/accounts/{id}/sync
GET    /api/v1/accounts/gmail/connect
GET    /api/v1/emails/search
GET    /api/v1/emails/search/regex
GET    /api/v1/emails/{id}
GET    /api/v1/attachments/{id}
POST   /api/v1/send
```

## How synchronization works

The parts that make search trustworthy rather than best-effort:

- Alembic applies versioned, data-preserving migrations on startup.
- Message identity is the normalised RFC `Message-ID`, which travels with the
  message. Location lives separately in `message_placements`, one row per folder,
  so moving a message upstream relocates it instead of deleting and re-creating
  it. Gmail API messages keep the provider's own id, which already survives a
  label change.
- IMAP cursors persist UID, UIDVALIDITY and folder. Backfills run oldest-first and are
  bounded per cycle. A cursor advances only after its batch commits, and a UIDVALIDITY
  change resets just that folder.
- Gmail OAuth accounts use `messages.list`/`get` for resumable backfill and
  `history.list` for incremental change. An expired history ID triggers a
  generation-marked full sync before upstream deletions are reconciled.
- Periodic metadata-only reconciliation mirrors flags and upstream deletions, with a
  durable checkpoint so a restart does not force a full rescan.
- Account work is bounded by a global concurrency limit and protected by expiring
  database leases, so two workers cannot sync one mailbox and a crashed worker cannot
  hold a permanent lock.
- PostgreSQL GIN indexes back lexical body and attachment search. The same text
  is indexed once per configured language and stored as one combined vector, so
  a German invoice and an English newsletter are both stemmed correctly in the
  same mailbox; identical lexemes collapse, so the union costs about as much as
  the unstemmed index it sits beside. `EMAILSERVER_SEARCH_TEXT_CONFIGS` selects
  the languages and defaults to `["simple", "english", "german"]`. Changing it
  needs a matching index, which the migration only builds once:

  ```sql
  CREATE INDEX CONCURRENTLY ix_email_logs_search_fts_simple_english_french
    ON email_logs USING gin ((
        to_tsvector('simple',  coalesce(sender,'')||' '||coalesce(recipient,'')||' '||coalesce(subject,'')||' '||coalesce(body_plain,''))
     || to_tsvector('english', coalesce(sender,'')||' '||coalesce(recipient,'')||' '||coalesce(subject,'')||' '||coalesce(body_plain,''))
     || to_tsvector('french',  coalesce(sender,'')||' '||coalesce(recipient,'')||' '||coalesce(subject,'')||' '||coalesce(body_plain,''))
    ));
  ```

  Without it search still returns the same answer, by scanning every stored body.
- Provider flags are normalised at sync time into `is_unread`, `is_flagged` and
  `is_answered`. IMAP reports that a message *was read* and the Gmail API reports
  that it *was not*, in two different encodings; neither is filterable as stored.
  A message the provider never reported flags for stays null rather than
  defaulting to read.
- Attachment text is extracted at sync time. Image attachments are OCR'd in
  every language installed in the image, because tesseract given no language
  assumes English and mangles accented scripts: German umlauts come back as
  `dirfen Fuboden` instead of `dürfen Fußboden`, so the text is indexed but can
  never be found by searching the words on the page. The image ships
  `eng deu fra ita spa nld por`; add or trim with
  `docker build --build-arg TESSERACT_LANGS="eng deu jpn"`, and pin a subset at
  runtime with `EMAILSERVER_OCR_LANGUAGES=deu+eng` to trade coverage for speed.
- A `Date` header that parses but is implausible, such as a year of 2611, is
  discarded rather than stored, because search sorts and paginates on that
  column. Gmail falls back to the provider timestamp; IMAP leaves it null.
  `scripts/repair_email_dates.py` clears values written before this check.
- Regex search is separate and bounded by scope, pattern, result and statement time.
- Sends support idempotency keys and append an owner-scoped audit record.

## Development

The test suite is self-contained. It uses in-memory SQLite and a temporary data
directory, so it needs no PostgreSQL, no Docker and no network.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q
ruff check .
```

It covers tenant isolation, tool exposure and annotations, static-token and OAuth
authentication, encryption, token tampering, attachment limits, IMAP cursor behavior,
Gmail history behavior, and OAuth challenge metadata.

`scripts/verify_postgres_search.py` is a different thing: a read-only smoke test that
runs against a **populated** deployment to confirm exhaustive search, dedup, facets and
regex behave on real data.

```bash
docker compose exec email-server python -m scripts.verify_postgres_search
```

## License

MIT. See [LICENSE](LICENSE).
