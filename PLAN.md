# Serverless GCP Deployment Plan

Status: **Not started**. This document captures the full plan for deploying the email server to GCP Cloud Run with Neon Postgres.

## Architecture

```
Cloud Scheduler (per account, every 5 min)
        │  POST /api/v1/smtp-configs/{id}/process
        │  (authenticated via OIDC / API key)
        ▼
┌─── Cloud Run Service ───────────┐
│  FastAPI (API + MCP)            │
│  SYNC_ENABLED=false             │
│  No Tesseract (OCR skipped)     │──────► Neon Postgres
│  512MB memory, scales 0-N       │
│  Auth required for all routes   │
└─────────────────────────────────┘
```

Same Docker image runs locally (docker compose, bundled Postgres, background sync, no auth) and on GCP (Cloud Run, Neon, scheduled sync, auth required).

The only difference is environment variables:

| Setting | Docker Compose (local) | Cloud Run (GCP) |
|---|---|---|
| `DATABASE_URL` | `...@postgres:5432/emailserver` (bundled) | `...@neon.tech/emailserver` (external) |
| `SYNC_ENABLED` | `true` (background loop) | `false` (Cloud Scheduler triggers) |
| `AUTH_ENABLED` | `false` (local dev) | `true` (production) |
| OCR | Installed (Tesseract) | Not installed (build arg) |

---

## OPEN ISSUE: Authentication & User Separation

**This must be designed before implementation.**

### The Problem

Currently the API is completely open. Anyone with the URL can:
- Read all emails from all accounts
- Send emails from any configured account
- Delete/modify SMTP configs
- Trigger syncs

For a public Cloud Run deployment, this is unacceptable.

### What Needs Deciding

#### 1. Auth Mechanism

Options to consider:
- **API key** (simplest): single shared secret in `Authorization: Bearer <key>` header. Good enough for single-user or internal use. Key stored as env var / GCP secret.
- **OAuth2 / OIDC**: Google Identity, Auth0, etc. Proper user sessions. More complex.
- **Cloud Run IAM**: Lock down the service to only authenticated Google accounts. Cloud Scheduler uses OIDC service account. No app-level auth code needed, but limits access to GCP IAM principals.
- **mTLS / VPN**: Network-level restriction. No code changes but requires infrastructure.

#### 2. User Separation (Multi-tenancy)

Currently all SMTP configs and emails live in a flat namespace. If multiple users share the deployment:
- Who owns which SMTP config?
- Can user A see user B's emails?
- Can user A send from user B's account?

Options:
- **Single-tenant** (simplest): One deployment per user. No code changes. Each user gets their own Cloud Run service + Neon database. Auth is just "can you reach the service."
- **Multi-tenant with user_id**: Add `user_id` column to `smtp_configs`, `email_logs`, etc. Filter all queries by authenticated user. Every endpoint needs auth middleware + row-level filtering.
- **Multi-tenant with separate databases**: Each user gets their own Neon database. A routing layer maps auth token -> database URL. More isolation, more infrastructure.

#### 3. MCP Endpoint Auth

The MCP endpoint at `/llm/mcp` is used by AI agents (Claude, etc.). How should this authenticate?
- Same API key as the REST API?
- Separate MCP-specific token?
- No auth (if behind Cloud Run IAM)?

#### 4. Cloud Scheduler Auth

Cloud Scheduler needs to call `POST /smtp-configs/{id}/process`. Options:
- **Cloud Run IAM + OIDC**: Scheduler uses a service account with `run.invoker` role. No app-level auth needed. Cloud Run validates the OIDC token at the infrastructure level.
- **API key**: Scheduler sends the same API key as a header.

### Recommendation (to be confirmed)

For the initial deployment, the simplest viable approach:

1. **Cloud Run IAM** for infrastructure-level auth (Scheduler, admin access)
2. **API key** for app-level auth (optional, for when the service is exposed publicly)
3. **Single-tenant** (one deployment per user, no multi-tenancy code)
4. Add auth later if multi-user becomes a requirement

This means zero code changes for auth -- it's all infrastructure config. The `gcp-setup.sh` script would:
- Deploy Cloud Run with `--no-allow-unauthenticated`
- Grant the Scheduler service account `roles/run.invoker`
- Users access the API via `gcloud run services proxy` or by adding their Google account as an invoker

---

## Phase 1: IMAP UID Incremental Sync

Currently the sync downloads every email every cycle (`SEARCH ALL`). This must be fixed before serverless deployment -- a cold-start Cloud Run container cannot re-download 16k emails.

### New model: `src/models/folder_sync_state.py`

```python
class FolderSyncState(Base):
    __tablename__ = "folder_sync_states"
    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id"), nullable=False)
    folder_name = Column(String(255), nullable=False)
    last_uid = Column(Integer, default=0)
    uid_validity = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    # Unique constraint: one state per (account, folder)
    __table_args__ = (UniqueConstraint("smtp_config_id", "folder_name"),)
```

### Changes to `src/email/smtp_client.py`

Rewrite `_fetch_folder`:
- Accept `last_uid` and `uid_validity` params
- After `SELECT` folder, read `UIDVALIDITY` from response
- If `uid_validity` changed, signal caller to reset state
- Use `uid("search", None, f"UID {last_uid+1}:*")` instead of `search("ALL")`
- Use `uid("fetch", msg_id, "(RFC822)")` instead of sequence-number fetch
- Track and return highest UID seen

### Changes to `src/email/email_processor.py`

- Before fetching: read `FolderSyncState` from DB for this config + folder
- After each batch: update `last_uid` in DB
- On `UIDVALIDITY` change: delete old state, re-sync from 0
- Add `pg_try_advisory_lock(config.id)` to prevent concurrent syncs of same account
- Register new model in `src/database/connection.py:init_database()`

---

## Phase 2: Conditional Background Sync

### New config: `src/config.py`

```python
sync_enabled: bool = True  # False for API-only mode (Cloud Run)
```

### Changes to `src/server.py`

- Guard `asyncio.create_task(email_processor.start_processing())` with `if settings.sync_enabled`
- Guard shutdown block accordingly
- Fix existing bug: `final_app.state.cancel()` -> `final_app.state.processing_task.cancel()`

### Changes to `docker-compose.yml`

- Add `EMAILSERVER_SYNC_ENABLED=true` explicitly

---

## Phase 3: Dockerfile (OCR Build Arg)

Add build arg to `Dockerfile`:

```dockerfile
ARG INSTALL_OCR=true
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      apk add --no-cache tesseract-ocr tesseract-ocr-data-eng; \
    fi
```

- Local docker-compose: default `INSTALL_OCR=true` (Tesseract installed)
- GCP deploy: `--build-arg INSTALL_OCR=false` (no Tesseract, smaller image, less memory)
- The `text_extractor.py` already gracefully skips OCR if Tesseract isn't installed (try/except around import)

---

## Phase 4: GCP Deployment Scripts (raw gcloud CLI)

### `deploy/gcp-setup.sh` (one-time)

Params: `PROJECT_ID`, `REGION`, `DATABASE_URL`, `SERVICE_NAME`

1. Enable APIs: `run`, `cloudscheduler`, `artifactregistry`, `secretmanager`
2. Create Artifact Registry repo
3. Create secret for `DATABASE_URL`
4. Build + push image with `--build-arg INSTALL_OCR=false`
5. Deploy Cloud Run service:
   - `--no-allow-unauthenticated`
   - `EMAILSERVER_SYNC_ENABLED=false`
   - `EMAILSERVER_DATABASE_URL` from secret ref
   - `--memory 512Mi --min-instances 0 --max-instances 3 --port 8000`
6. Create service account for Cloud Scheduler
7. Grant `roles/run.invoker` to scheduler service account
8. Output: service URL + instructions for adding scheduler jobs

### `deploy/gcp-deploy.sh` (redeploy)

Rebuild image, push, update Cloud Run service to new revision.

### `deploy/gcp-scheduler.sh` (per account)

Params: `CONFIG_ID`, `SERVICE_URL`, `INTERVAL` (default `*/5 * * * *`)

Creates a Cloud Scheduler job:
- HTTP target: `POST {SERVICE_URL}/api/v1/smtp-configs/{CONFIG_ID}/process`
- OIDC auth with scheduler service account
- Retry config: 1 retry, 30s timeout

---

## Phase 5: Cleanup

### Factory reset scripts

Add `folder_sync_states` to:
- `scripts/factory_reset.py` PURGE_TABLES list
- `scripts/factory_reset.sh` TRUNCATE commands
- `scripts/factory_reset.sql` TRUNCATE commands

### README.md

Add GCP deployment section with:
- Prerequisites (gcloud CLI, Neon account)
- Setup instructions
- Adding accounts + scheduler jobs
- Redeploying

---

## File Change Summary

| File | Action | Phase |
|---|---|---|
| `src/models/folder_sync_state.py` | New | 1 |
| `src/database/connection.py` | Edit (1 line) | 1 |
| `src/email/smtp_client.py` | Edit (rewrite _fetch_folder) | 1 |
| `src/email/email_processor.py` | Edit (sync state + advisory lock) | 1 |
| `src/config.py` | Edit (1 line) | 2 |
| `src/server.py` | Edit (conditional sync + bug fix) | 2 |
| `docker-compose.yml` | Edit (1 line) | 2 |
| `Dockerfile` | Edit (OCR build arg) | 3 |
| `deploy/gcp-setup.sh` | New | 4 |
| `deploy/gcp-deploy.sh` | New | 4 |
| `deploy/gcp-scheduler.sh` | New | 4 |
| `scripts/factory_reset.py` | Edit (add table) | 5 |
| `scripts/factory_reset.sh` | Edit (add table) | 5 |
| `scripts/factory_reset.sql` | Edit (add table) | 5 |
| `README.md` | Edit (add GCP section) | 5 |

---

## Memory Budget (Cloud Run)

| Component | Memory |
|---|---|
| Python + FastAPI + SQLAlchemy baseline | ~80-120MB |
| PDF extraction (pypdf, in-memory) | ~2-3x file size (max 30MB for 10MB PDF) |
| DOCX/XLSX extraction | ~3-5x file size (max 50MB for 10MB file) |
| **No Tesseract OCR** | **0MB** (not installed) |
| **Total peak** | **~200MB** |
| **Cloud Run setting** | **512MB** (comfortable headroom) |
