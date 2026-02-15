# Email Server

Multi-account email server with PostgreSQL storage, REST API, and MCP integration. Connects to IMAP accounts, syncs emails into Postgres (body + attachment text), and exposes everything via FastAPI.

## Quick Start

```bash
# Start Postgres + email server
docker compose up -d

# Check health
curl http://localhost:8002/api/v1/health

# Add an email account
curl -X POST http://localhost:8002/api/v1/smtp-configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Gmail",
    "host": "imap.gmail.com",
    "port": 993,
    "username": "you@gmail.com",
    "password": "your-app-password",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "imap_use_ssl": true,
    "smtp_use_tls": true,
    "enabled": true
  }'

# Emails sync automatically. Search them:
curl "http://localhost:8002/api/v1/emails/search?query=invoice"
```

## Architecture

- **Database**: PostgreSQL 16 (all email content stored in DB, no filesystem)
- **Framework**: FastAPI + SQLAlchemy ORM
- **MCP**: FastMCP mounted at `/llm/mcp`
- **Deployment**: Docker Compose (stateless compute + one Postgres connection)

```
email-server/
├── docker-compose.yml         # Postgres + email-server
├── Dockerfile                 # Python 3.11 Alpine, non-root user
├── requirements.txt
├── src/
│   ├── main.py                # Entry point, logging config
│   ├── server.py              # FastAPI app + MCP setup
│   ├── config.py              # Pydantic settings (env vars)
│   ├── models/
│   │   ├── base.py            # SQLAlchemy declarative base
│   │   ├── email.py           # EmailLog (body_plain, body_html)
│   │   ├── smtp_config.py     # SMTPConfig (IMAP/SMTP credentials)
│   │   └── attachment.py      # EmailAttachment (text_content in DB)
│   ├── database/
│   │   └── connection.py      # Engine, session factory, init_database()
│   ├── handlers/
│   │   └── email_handler.py   # All API routes (CRUD, search, send, reply, forward)
│   ├── email/
│   │   ├── smtp_client.py     # IMAP client (batch fetch, folder scanning)
│   │   ├── email_processor.py # Background sync loop
│   │   ├── smtp_sender.py     # SMTP sending (reply, forward, attachments)
│   │   ├── attachment_handler.py  # Extract attachments, store text_content in DB
│   │   └── text_extractor.py  # PDF, DOCX, image OCR, plaintext extraction
│   └── storage_config/
│       └── resolver.py        # Per-account storage config resolution
├── scripts/
│   ├── factory_reset.py       # Wipe all emails, preserve accounts (Python)
│   ├── factory_reset.sh       # Same but shell script
│   └── factory_reset.sql      # Same but raw SQL
└── tests/
    ├── conftest.py
    ├── test_database.py
    ├── test_attachment_handler.py
    ├── test_models.py
    ├── test_smtp_client.py
    ├── test_storage_config.py
    └── test_text_extractor.py
```

## Configuration

Environment variables with `EMAILSERVER_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAILSERVER_DATABASE_URL` | `postgresql://emailserver:emailserver@postgres:5432/emailserver` | PostgreSQL connection string |
| `EMAILSERVER_API_HOST` | `0.0.0.0` | API bind address |
| `EMAILSERVER_API_PORT` | `8000` | API port |
| `EMAILSERVER_LOG_LEVEL` | `INFO` | Logging level |
| `EMAILSERVER_LOG_FILE` | `""` (stdout) | Log file path (empty = stdout only) |
| `EMAILSERVER_EMAIL_CHECK_INTERVAL` | `30` | Sync interval in seconds |
| `EMAILSERVER_MAX_ATTACHMENT_SIZE` | `10485760` | Max attachment size (10MB) |
| `EMAILSERVER_MCP_ENABLED` | `true` | Enable MCP endpoint |

For external Postgres, just set `EMAILSERVER_DATABASE_URL` to your connection string.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/health` | Health check |
| `GET /api/v1/smtp-configs` | List email accounts |
| `POST /api/v1/smtp-configs` | Add email account |
| `PUT /api/v1/smtp-configs/{id}` | Update account |
| `DELETE /api/v1/smtp-configs/{id}` | Delete account |
| `POST /api/v1/smtp-configs/{id}/process` | Trigger manual sync |
| `GET /api/v1/emails` | List emails (paginated) |
| `GET /api/v1/emails/{id}` | Get full email content |
| `GET /api/v1/emails/search` | Regex search (body, subject, sender, attachments) |
| `POST /api/v1/send-email` | Send email |
| `POST /api/v1/emails/{id}/reply` | Reply to email |
| `POST /api/v1/emails/{id}/forward` | Forward email |

- **Swagger UI**: http://localhost:8002/api/v1/docs
- **MCP endpoint**: http://localhost:8002/llm/mcp

### Search

Search uses PostgreSQL `~*` (case-insensitive regex):

```bash
# Simple word search
curl "http://localhost:8002/api/v1/emails/search?query=invoice"

# Regex pattern
curl "http://localhost:8002/api/v1/emails/search?query=invoice|receipt"

# Search in attachment text
curl "http://localhost:8002/api/v1/emails/search?query=500.*EUR&search_attachments=true"

# Filter by field
curl "http://localhost:8002/api/v1/emails/search?query=alice&field=sender"

# Filter by date range
curl "http://localhost:8002/api/v1/emails/search?date_from=2025-01-01&date_to=2025-12-31"
```

## Email Provider Setup

### Gmail
- Host: `imap.gmail.com`, Port: `993`, SSL: `true`
- SMTP: `smtp.gmail.com`, Port: `587`, TLS: `true`
- Use an [App Password](https://myaccount.google.com/apppasswords) (requires 2FA)

### Outlook
- Host: `outlook.office365.com`, Port: `993`, SSL: `true`
- SMTP: `smtp.office365.com`, Port: `587`, TLS: `true`

### Generic IMAP/SMTP
- Set `host`, `port`, `smtp_host`, `smtp_port` to your provider's values
- Set `imap_use_ssl`/`smtp_use_tls` as appropriate

## Development

### Code Style

- **Python**: 3.11+
- **Line length**: 120 characters
- **Quotes**: Double quotes
- **Linter**: `ruff check --line-length 120`
- **Formatter**: `ruff format --line-length 120`
- **Logging**: Use lazy `%s` formatting, not f-strings (`logger.info("msg %s", val)`)
- **Imports**: stdlib, then third-party, then local (`from src.xxx import ...`)
- **Types**: Use `Optional[X]` for nullable, type hints on all functions
- **Models**: SQLAlchemy declarative with `from src.models.base import Base`
- **Pydantic**: Use `Optional[str] = None` (not `str = None`) for optional fields

### Running Tests

```bash
# Requires a running Postgres (tests use real DB, not SQLite)
pytest -v
pytest --cov=src --cov-report=html
pytest tests/test_attachment_handler.py -v
```

### Running Locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start Postgres (via docker compose or external)
docker compose up -d postgres

# Run the server
EMAILSERVER_DATABASE_URL=postgresql://emailserver:emailserver@localhost:5432/emailserver \
  python -m src.main
```

### Factory Reset

Wipes all email data while preserving account configurations:

```bash
# Preview
python scripts/factory_reset.py --dry-run

# Execute
python scripts/factory_reset.py --force

# Or via shell
./scripts/factory_reset.sh --dry-run
./scripts/factory_reset.sh --force

# Or raw SQL
psql $DATABASE_URL -f scripts/factory_reset.sql
```

## License

This project follows the same license as the parent goedlike project.
