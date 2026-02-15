# Attachment Pipeline Redesign: Ephemeral Processing

## Current Problem
- Binary attachments saved permanently to disk
- 71 orphaned records from before the fix
- Storage grows unbounded
- IMAP throttling issues with Gmail

## Proposed Solution: Stream + Temp Files

### Architecture

```
Incoming Email
      │
      ▼
┌─────────────────┐
│ Extract Attach. │──┐
└─────────────────┘  │
                     ▼
        ┌──────────────────────┐
        │  Stream to Temp File │  ← tempfile.mkstemp() in /tmp
        │  (max 30s lifetime)  │
        └──────────────────────┘
                     │
                     ▼
        ┌──────────────────────┐
        │   Extract Text/      │
        │   Generate Metadata  │
        └──────────────────────┘
                     │
                     ▼
        ┌──────────────────────┐
        │   Store Derived Data │  ← text, metadata, embeddings
        │   (DB + Search Index)│
        └──────────────────────┘
                     │
                     ▼
        ┌──────────────────────┐
        │   Delete Temp File   │  ← immediate cleanup
        └──────────────────────┘
```

### Key Changes

1. **No Permanent Binary Storage**
   - Use `tempfile.NamedTemporaryFile()` with `delete=False` for processing
   - Or stream directly to memory if < 10MB
   - Immediate unlink after extraction

2. **Immediate Processing Pipeline**
   - Extract text (PDF, DOCX, images via OCR)
   - Generate metadata (size, type, checksum)
   - Virus scan (optional, via ClamAV or cloud)
   - All within the same async context

3. **TTL-Based Safety Net**
   - Temp files auto-delete after 30 seconds via cron/systemd timer
   - Even if process crashes, files don't persist

4. **Size Thresholds**
   - < 10MB: Stream to memory, process in-RAM
   - 10MB - 50MB: Temp file, process, immediate delete
   - > 50MB: Skip binary extraction, store metadata only

### Code Changes

```python
# New: attachment_handler_v2.py
import tempfile
import asyncio
from pathlib import Path

class EphemeralAttachmentHandler:
    def __init__(self):
        self.temp_dir = "/tmp/email_attachments"
        self.max_memory_size = 10 * 1024 * 1024  # 10MB
        self.max_process_size = 50 * 1024 * 1024  # 50MB
    
    async def process_attachment(self, payload: bytes, content_type: str) -> AttachmentResult:
        size = len(payload)
        
        # Skip large files
        if size > self.max_process_size:
            return AttachmentResult(
                metadata=Metadata(size=size, type=content_type),
                text=None,
                skipped_reason="too_large"
            )
        
        # Small files: process in memory
        if size <= self.max_memory_size:
            text = await self._extract_text(payload, content_type)
            return AttachmentResult(metadata=..., text=text)
        
        # Medium files: temp file
        with tempfile.NamedTemporaryFile(
            dir=self.temp_dir,
            suffix=self._get_extension(content_type),
            delete=False
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        
        try:
            text = await self._extract_text_from_file(tmp_path, content_type)
            return AttachmentResult(metadata=..., text=text)
        finally:
            # Immediate cleanup
            tmp_path.unlink(missing_ok=True)
```

---

## Gmail Sync Strategy

### Why IMAP Fails
- Google throttles IMAP `SEARCH` across all folders
- No reliable way to get "all emails since X" without scanning
- Connection drops, timeouts

### Solution: Gmail API

#### Phase 1: Initial Backfill
```
1. Users.messages.list() with pagination
2. For each message ID:
   - Users.messages.get(format='full')
   - Extract headers, body, attachments
   - Store messageId, historyId, threadId
3. Track last historyId per account
4. Resume support (checkpoint every 1000 messages)
```

#### Phase 2: Incremental Sync
```
1. Users.history.list(startHistoryId=last_historyId)
2. Process changes:
   - messagesAdded: fetch full message
   - messagesDeleted: soft-delete in DB
   - labelsAdded/Removed: update tags
3. Update last_historyId
4. Handle 404 (history too old) → fallback to full list
```

#### Phase 3: Watch (Push Notifications)
```
1. Users.watch() → get expiration, historyId
2. Cloud Pub/Sub or webhook endpoint
3. On notification: trigger incremental sync
4. Auto-renew watch before expiration
```

### Deduplication
```sql
-- Use Gmail's native IDs
CREATE UNIQUE INDEX idx_email_gmail_id ON email_logs(gmail_message_id);

-- Upsert pattern
INSERT INTO email_logs (gmail_message_id, history_id, ...)
VALUES (...)
ON CONFLICT(gmail_message_id) DO UPDATE SET
    history_id = excluded.history_id,
    labels = excluded.labels;
```

### Rate Limiting
```python
# Gmail API: 250 quota units per user per second
# Users.messages.get = 5 units
# Users.messages.list = 5 units
# Users.history.list = 2 units

class GmailRateLimiter:
    def __init__(self):
        self.quota_per_second = 250
        self.used = 0
        self.reset_time = time.time() + 1
    
    async def acquire(self, units: int):
        now = time.time()
        if now >= self.reset_time:
            self.used = 0
            self.reset_time = now + 1
        
        if self.used + units > self.quota_per_second:
            wait = self.reset_time - now
            await asyncio.sleep(wait)
            return await self.acquire(units)
        
        self.used += units
```

### Implementation Plan

| Week | Task |
|------|------|
| 1 | Ephemeral attachment handler, temp file pipeline |
| 2 | Gmail API client, OAuth2 flow, message fetching |
| 3 | History API incremental sync, deduplication |
| 4 | Watch/push notifications, rate limiting, monitoring |

### Migration Path

1. **Feature Flag**: `USE_GMAIL_API` per account
2. **Dual Run**: Run both IMAP and API for 1 week, compare
3. **Cutover**: Disable IMAP for Gmail accounts
4. **Cleanup**: Remove IMAP code path

### Monitoring

```python
# Metrics to track
gmail_sync_latency_histogram.observe(duration)
gmail_api_quota_gauge.set(remaining_quota)
gmail_history_gap_gauge.set(current_history_id - last_synced_history_id)
gmail_attachments_processed_counter.inc()
gmail_attachments_skipped_counter.inc(reason="too_large")
```
