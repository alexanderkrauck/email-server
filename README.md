# Email Server

Multi-account email sync and search service with PostgreSQL storage, FastAPI routes, attachment text extraction, and MCP integration.

This is an agent-infrastructure project: a local service that lets AI workflows search, inspect, and act on email through a structured API and MCP endpoint. It is not intended as a hosted SaaS product.

## Why I Built It

Email is still where a lot of operational work happens. For agent workflows, raw IMAP access is awkward: messages are hard to search, attachment content is hidden, state is spread across folders, and tool calls become brittle.

This project turns email into structured local infrastructure:

- sync email from IMAP accounts into PostgreSQL
- store body text, HTML, metadata, and extracted attachment text
- expose search and CRUD operations through FastAPI
- expose email tools through MCP for AI workflows
- support sending, replying, and forwarding through SMTP

## Key Technical Points

- FastAPI application with typed routes
- PostgreSQL 16 storage
- SQLAlchemy ORM models for accounts, emails, and attachments
- Background sync loop for IMAP accounts
- Attachment text extraction for PDF, DOCX, image OCR, and plaintext
- MCP endpoint mounted at `/llm/mcp`
- Docker Compose setup for local deployment
- Tests for models, database behavior, attachment handling, and extraction utilities

## Architecture

```text
email-server/
├── docker-compose.yml
├── Dockerfile
├── src/
│   ├── server.py              # FastAPI app and MCP setup
│   ├── config.py              # Pydantic settings
│   ├── handlers/              # API routes
│   ├── models/                # SQLAlchemy models
│   ├── database/              # engine/session initialization
│   ├── email/                 # IMAP sync, SMTP sending, attachment extraction
│   └── storage_config/        # account storage configuration
├── scripts/                   # reset utilities
└── tests/
```

## Quick Start

```bash
docker compose up -d
curl http://localhost:8002/api/v1/health
```

Add an email account:

```bash
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
```

Search synced email:

```bash
curl "http://localhost:8002/api/v1/emails/search?query=invoice"
```

## API Surface

- `GET /api/v1/health`
- `GET /api/v1/smtp-configs`
- `POST /api/v1/smtp-configs`
- `PUT /api/v1/smtp-configs/{id}`
- `DELETE /api/v1/smtp-configs/{id}`
- `POST /api/v1/smtp-configs/{id}/process`
- `GET /api/v1/emails`
- `GET /api/v1/emails/{id}`
- `GET /api/v1/emails/search`
- `POST /api/v1/send-email`
- `POST /api/v1/emails/{id}/reply`
- `POST /api/v1/emails/{id}/forward`

Swagger UI:

```text
http://localhost:8002/api/v1/docs
```

MCP endpoint:

```text
http://localhost:8002/llm/mcp
```

## Configuration

Environment variables use the `EMAILSERVER_` prefix.

| Variable | Default | Description |
| --- | --- | --- |
| `EMAILSERVER_DATABASE_URL` | `postgresql://emailserver:emailserver@postgres:5432/emailserver` | PostgreSQL connection string |
| `EMAILSERVER_API_HOST` | `0.0.0.0` | API bind address |
| `EMAILSERVER_API_PORT` | `8000` | API port |
| `EMAILSERVER_LOG_LEVEL` | `INFO` | Log level |
| `EMAILSERVER_EMAIL_CHECK_INTERVAL` | `30` | Sync interval in seconds |
| `EMAILSERVER_MAX_ATTACHMENT_SIZE` | `10485760` | Max attachment size |
| `EMAILSERVER_MCP_ENABLED` | `true` | Enable MCP endpoint |

## Status

Research/infrastructure project. Useful as a local component for agent workflows, not hardened as a public managed service.

## Notes

Use app passwords or dedicated test accounts. Do not commit real email credentials.
