# Implementation Plan: Ephemeral Attachments + Gmail API

## Phase 1: Ephemeral Attachment Pipeline (Days 1-3)

### Step 1.1: Create `attachment_streamer.py`
```python
"""Stream attachments to temp files, extract, cleanup immediately."""

import tempfile
import asyncio
from pathlib import Path
from typing import Optional, AsyncIterator
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class StreamResult:
    text: Optional[str]
    metadata: dict
    checksum: str
    skipped_reason: Optional[str] = None

class AttachmentStreamer:
    """Process attachments without permanent binary storage."""
    
    # Size thresholds
    MAX_MEMORY_SIZE = 10 * 1024 * 1024      # 10MB - process in RAM
    MAX_PROCESS_SIZE = 50 * 1024 * 1024     # 50MB - temp file
    
    def __init__(self, temp_dir: str = "/tmp/email_attachments"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    async def stream_process(
        self, 
        payload: bytes, 
        filename: str,
        content_type: str
    ) -> StreamResult:
        """
        Process attachment payload without saving permanently.
        
        Flow:
        1. Small files → memory → extract → done
        2. Medium files → temp file → extract → delete temp
        3. Large files → skip extraction, return metadata only
        """
        size = len(payload)
        checksum = hashlib.sha256(payload).hexdigest()[:16]
        
        metadata = {
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "checksum": checksum,
        }
        
        # Skip very large files
        if size > self.MAX_PROCESS_SIZE:
            logger.info(f"Skipping large attachment: {filename} ({size} bytes)")
            return StreamResult(
                text=None,
                metadata=metadata,
                checksum=checksum,
                skipped_reason="too_large"
            )
        
        # Small files: process in memory
        if size <= self.MAX_MEMORY_SIZE:
            logger.debug(f"Processing in memory: {filename}")
            text = await self._extract_memory(payload, content_type)
            return StreamResult(
                text=text,
                metadata={**metadata, "processed_in": "memory"},
                checksum=checksum
            )
        
        # Medium files: temp file with immediate cleanup
        return await self._extract_temp_file(payload, filename, content_type, metadata, checksum)
    
    async def _extract_memory(self, payload: bytes, content_type: str) -> Optional[str]:
        """Extract text from bytes in memory."""
        from src.email.text_extractor import TextExtractor
        extractor = TextExtractor()
        return await extractor.extract(payload, content_type, storage_config=None)
    
    async def _extract_temp_file(
        self, 
        payload: bytes, 
        filename: str,
        content_type: str,
        metadata: dict,
        checksum: str
    ) -> StreamResult:
        """Stream to temp file, extract, delete immediately."""
        tmp_path = None
        try:
            # Create temp file
            suffix = Path(filename).suffix or ".bin"
            with tempfile.NamedTemporaryFile(
                dir=self.temp_dir,
                suffix=suffix,
                delete=False
            ) as tmp:
                tmp.write(payload)
                tmp_path = Path(tmp.name)
            
            logger.debug(f"Temp file created: {tmp_path}")
            
            # Extract text from temp file
            from src.email.text_extractor import TextExtractor
            extractor = TextExtractor()
            text = await extractor.extract_from_file(tmp_path, content_type)
            
            return StreamResult(
                text=text,
                metadata={**metadata, "processed_in": "temp_file"},
                checksum=checksum
            )
            
        finally:
            # IMMEDIATE cleanup - critical for ephemeral design
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
                logger.debug(f"Temp file deleted: {tmp_path}")
    
    async def cleanup_orphaned(self, max_age_seconds: int = 300):
        """Cleanup any orphaned temp files (safety net)."""
        cutoff = time.time() - max_age_seconds
        cleaned = 0
        
        for tmp_file in self.temp_dir.glob("*"):
            if tmp_file.stat().st_mtime < cutoff:
                tmp_file.unlink()
                cleaned += 1
        
        if cleaned:
            logger.warning(f"Cleaned up {cleaned} orphaned temp files")
```

### Step 1.2: Modify `attachment_handler.py`
```python
# In extract_attachments() method:

async def _process_attachment_ephemeral(
    self,
    part,
    email_log_id: int,
    account_name: Optional[str] = None
) -> Optional[EmailAttachment]:
    """Process attachment using ephemeral streaming."""
    
    from src.email.attachment_streamer import AttachmentStreamer
    
    filename = part.get_filename() or f"attachment_{email_log_id}_unknown"
    content_type = part.get_content_type()
    content_id = part.get('Content-ID', '').strip('<>')
    
    payload = part.get_payload(decode=True)
    if not payload:
        return None
    
    # Stream process - no permanent binary storage
    streamer = AttachmentStreamer()
    result = await streamer.stream_process(payload, filename, content_type)
    
    # Create attachment record with derived data only
    attachment = EmailAttachment(
        email_log_id=email_log_id,
        filename=self._sanitize_filename(filename),
        content_type=content_type,
        content_id=content_id,
        size=len(payload),
        file_path=None,  # No permanent storage
        text_file_path=None,  # Text stored separately if needed
        # New fields:
        checksum=result.checksum,
        extracted_text=result.text,  # Store directly in DB (compressed)
        metadata_json=json.dumps(result.metadata)
    )
    
    return attachment
```

### Step 1.3: DB Schema Update
```sql
-- Add ephemeral-aware columns
ALTER TABLE email_attachments ADD COLUMN checksum VARCHAR(32);
ALTER TABLE email_attachments ADD COLUMN extracted_text TEXT;  -- Compressed
ALTER TABLE email_attachments ADD COLUMN metadata_json TEXT;
ALTER TABLE email_attachments ADD COLUMN skipped_reason VARCHAR(50);

-- Index for deduplication
CREATE INDEX idx_attachment_checksum ON email_attachments(checksum);
```

---

## Phase 2: Gmail API Sync (Days 4-7)

### Step 2.1: Gmail API Client
```python
# src/sync/gmail_api_client.py

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

class GmailAPIClient:
    """Gmail API client with rate limiting and retry logic."""
    
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.metadata'
    ]
    
    def __init__(self, credentials: Credentials):
        self.service = build('gmail', 'v1', credentials=credentials)
        self.rate_limiter = GmailRateLimiter()
    
    async def list_messages(
        self, 
        query: str = None,
        page_token: str = None
    ) -> dict:
        """List message IDs matching query."""
        await self.rate_limiter.acquire(5)  # list = 5 quota units
        
        result = self.service.users().messages().list(
            userId='me',
            q=query,
            pageToken=page_token,
            maxResults=500
        ).execute()
        
        return result
    
    async def get_message(self, msg_id: str, format: str = 'full') -> dict:
        """Get full message content."""
        await self.rate_limiter.acquire(5)  # get = 5 quota units
        
        return self.service.users().messages().get(
            userId='me',
            id=msg_id,
            format=format
        ).execute()
    
    async def get_history(self, start_history_id: str) -> dict:
        """Get changes since historyId."""
        await self.rate_limiter.acquire(2)  # history = 2 quota units
        
        return self.service.users().history().list(
            userId='me',
            startHistoryId=start_history_id
        ).execute()
    
    async def watch(self, topic_name: str) -> dict:
        """Set up push notifications."""
        return self.service.users().watch(
            userId='me',
            body={'topicName': topic_name}
        ).execute()
```

### Step 2.2: Sync Orchestrator
```python
# src/sync/gmail_sync.py

class GmailSync:
    """Full Gmail sync with backfill + incremental."""
    
    def __init__(self, config: SMTPConfig):
        self.config = config
        self.client = GmailAPIClient(self._get_credentials())
        self.db = get_db_session()
    
    async def sync(self):
        """Main sync entry point."""
        last_history_id = self._get_last_history_id()
        
        if last_history_id:
            # Incremental sync
            await self._incremental_sync(last_history_id)
        else:
            # Initial backfill
            await self._backfill_sync()
    
    async def _backfill_sync(self):
        """Initial full sync with pagination."""
        logger.info("Starting Gmail backfill...")
        
        page_token = None
        total = 0
        
        while True:
            result = await self.client.list_messages(page_token=page_token)
            messages = result.get('messages', [])
            
            for msg_ref in messages:
                await self._process_message(msg_ref['id'])
                total += 1
                
                if total % 100 == 0:
                    logger.info(f"Processed {total} messages...")
            
            page_token = result.get('nextPageToken')
            if not page_token:
                break
        
        logger.info(f"Backfill complete: {total} messages")
    
    async def _incremental_sync(self, start_history_id: str):
        """Sync changes since last historyId."""
        logger.info(f"Starting incremental sync from historyId {start_history_id}")
        
        try:
            history = await self.client.get_history(start_history_id)
            
            for change in history.get('history', []):
                # Process message additions
                for msg_added in change.get('messagesAdded', []):
                    await self._process_message(msg_added['message']['id'])
                
                # Process deletions
                for msg_deleted in change.get('messagesDeleted', []):
                    await self._soft_delete_message(msg_deleted['message']['id'])
                
                # Update historyId
                self._save_last_history_id(change['id'])
                
        except Exception as e:
            if 'historyId not found' in str(e):
                # History expired, fallback to full sync
                logger.warning("History expired, falling back to full sync")
                await self._backfill_sync()
            else:
                raise
    
    async def _process_message(self, msg_id: str):
        """Fetch and process a single message."""
        # Check if already exists
        existing = self.db.query(EmailLog).filter_by(
            gmail_message_id=msg_id
        ).first()
        
        if existing:
            return  # Already synced
        
        # Fetch full message
        msg = await self.client.get_message(msg_id)
        
        # Extract headers
        headers = {h['name']: h['value'] for h in msg['payload']['headers']}
        
        # Process with ephemeral attachment handler
        attachments = await self._process_attachments_ephemeral(msg['payload'])
        
        # Store email
        email_log = EmailLog(
            smtp_config_id=self.config.id,
            gmail_message_id=msg_id,
            history_id=msg['historyId'],
            thread_id=msg['threadId'],
            sender=headers.get('From', ''),
            recipient=headers.get('To', ''),
            subject=headers.get('Subject', ''),
            message_id=headers.get('Message-ID', msg_id),
            # ... other fields
        )
        
        self.db.add(email_log)
        self.db.commit()
```

### Step 2.3: OAuth2 Flow
```python
# src/auth/gmail_oauth.py

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

class GmailOAuth:
    """OAuth2 flow for Gmail API."""
    
    def __init__(self, client_secrets_file: str):
        self.client_secrets_file = client_secrets_file
    
    def get_auth_url(self, redirect_uri: str) -> tuple[str, str]:
        """Get authorization URL and state."""
        flow = Flow.from_client_secrets_file(
            self.client_secrets_file,
            scopes=GmailAPIClient.SCOPES,
            redirect_uri=redirect_uri
        )
        
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'  # Force refresh token
        )
        
        return auth_url, state
    
    def exchange_code(self, code: str, redirect_uri: str) -> Credentials:
        """Exchange auth code for credentials."""
        flow = Flow.from_client_secrets_file(
            self.client_secrets_file,
            scopes=GmailAPIClient.SCOPES,
            redirect_uri=redirect_uri
        )
        
        flow.fetch_token(code=code)
        return flow.credentials
```

---

## Phase 3: Deployment Plan

### Week 1
- [ ] Day 1: Implement `AttachmentStreamer` with temp files
- [ ] Day 2: Update `attachment_handler.py` to use streaming
- [ ] Day 3: DB migration, testing, cleanup cron job

### Week 2
- [ ] Day 4: Gmail API client + OAuth flow
- [ ] Day 5: Backfill sync implementation
- [ ] Day 6: Incremental sync + history tracking
- [ ] Day 7: Integration testing

### Week 3
- [ ] Day 8: Push notifications (watch)
- [ ] Day 9: Rate limiting, retries, monitoring
- [ ] Day 10: Feature flags, gradual rollout
- [ ] Day 11: Performance optimization

### Migration
```bash
# Feature flag rollout
1. Deploy new code with USE_GMAIL_API=false
2. Enable for 1 test account
3. Compare IMAP vs API results for 1 week
4. Enable for 50% of Gmail accounts
5. Full cutover
6. Remove IMAP code path
```

### Monitoring
```python
# Key metrics
gmail_sync_messages_total
    .labels(direction=['backfill', 'incremental'])
    
gmail_sync_errors_total
    .labels(error_type=['rate_limit', 'auth', 'parse'])
    
gmail_attachment_processing_seconds
    .labels(size_bucket=['<1MB', '1-10MB', '10-50MB', '>50MB'])
    
temp_file_cleanup_total
orphaned_temp_files_gauge
```
