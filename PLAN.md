# Deployment Status

The original single-tenant GCP proposal has been superseded by the implemented multi-user architecture.

## Implemented

- Google OAuth identity for MCP and browser sessions
- Owner-scoped users, accounts, messages, attachments, sync cursors, and send audits
- Explicit OAuth-protected `/mcp` endpoint
- Encrypted mailbox credentials and persistent OAuth state
- Versioned Alembic migrations
- Persistent incremental IMAP synchronization and metadata reconciliation
- Indexed body and attachment search
- Ephemeral original-attachment refetch through signed URLs
- HTTPS production composition using Caddy

See [README.md](README.md) for deployment and Google OAuth configuration.

## Optional Cloud Run Work

Cloud Run remains possible, but the always-running background synchronizer should first be moved to a dedicated worker or scheduled job. A Cloud Run deployment would also need:

- external PostgreSQL
- shared persistent OAuth state, such as Redis
- Secret Manager values for signing and encryption keys
- a scheduler or queue for per-account sync
- a separate extraction worker with the same resource limits

This is an infrastructure alternative, not a prerequisite for the current Docker deployment.
