"""Email processing and orchestration."""

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import func

from src.database.connection import SessionLocal, get_db_session
from src.email import sanitize_db_text
from src.email.gmail_api_client import GmailApiClient, GmailHistoryExpired
from src.email.smtp_client import SMTPClient
from src.email.text_extractor import TextExtractor
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig

logger = logging.getLogger(__name__)


class EmailProcessor:
    """Main email processing orchestrator."""

    def __init__(self):
        self.active_clients = {}
        self.processing = False
        self._last_reconciliations = {}
        self._failure_counts = {}
        self._retry_after = {}

    async def start_processing(self):
        """Start the email processing loop."""
        if self.processing:
            logger.warning("Email processing already running")
            return

        self.processing = True
        logger.info("Starting email processing")

        while self.processing:
            try:
                await self._process_all_servers()
                from src.config import settings

                await asyncio.sleep(settings.email_check_interval)
            except Exception as e:
                logger.error("Error in email processing loop: %s", e)
                await asyncio.sleep(60)  # Wait longer on error

    async def stop_processing(self):
        """Stop email processing and cleanup."""
        logger.info("Stopping email processing")
        self.processing = False

        # Disconnect all clients
        for client in self.active_clients.values():
            await client.disconnect()
        self.active_clients.clear()

    async def _process_all_servers(self):
        """Process emails from all enabled SMTP servers."""
        with get_db_session() as db:
            # Get all enabled SMTP configs
            configs = db.query(SMTPConfig).filter(SMTPConfig.enabled).all()

            if not configs:
                logger.debug("No enabled SMTP configurations found")
                return

            # Create detached config copies to avoid session issues
            config_copies = [SMTPConfig.create_detached(config) for config in configs]

        # Process each server with detached config copies (in parallel)
        tasks = []
        for config_copy in config_copies:
            task = asyncio.create_task(self._process_server(config_copy))
            tasks.append(task)

        # Wait for all servers to complete (outside the loop for parallel processing)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_server(self, config: SMTPConfig, force: bool = False):
        """Process emails from a single server."""
        config_id = config.id
        completed = False
        lock_db = SessionLocal()

        try:
            retry_after = self._retry_after.get(config_id)
            if not force and retry_after and datetime.now(tz=timezone.utc) < retry_after:
                return False
            acquired = lock_db.query(func.pg_try_advisory_lock(config_id)).scalar()
            if not acquired:
                logger.info("Skipping account %s because another worker holds its sync lock", config_id)
                return False

            if config.provider == "gmail" and config.auth_type == "oauth2":
                await self._process_gmail_api(config)
            else:
                # Get or create an IMAP client for this server.
                client_key = config_id
                if client_key not in self.active_clients:
                    self.active_clients[client_key] = SMTPClient(config)
                else:
                    await self.active_clients[client_key].update_config(config)

                client = self.active_clients[client_key]
                sync_since = self._get_sync_since(config_id)

                # Stats are updated after each batch so an interrupted
                # mailbox scan can resume from its durable folder cursor.
                async for batch in client.fetch_new_emails(since=sync_since):
                    await self._process_emails(batch)
                    if batch:
                        await self._update_server_stats(config, len(batch))
                        self._persist_sync_cursors(batch)
                self._persist_client_cursors(config_id, client)
                try:
                    await self._reconcile_provider_state(config_id, client)
                except Exception as exc:
                    logger.warning(
                        "Provider state reconciliation failed for account %s: %s",
                        config_id,
                        exc,
                    )
            completed = True
            self._failure_counts.pop(config_id, None)
            self._retry_after.pop(config_id, None)

        except Exception as e:
            failures = self._failure_counts.get(config_id, 0) + 1
            self._failure_counts[config_id] = failures
            retry_seconds = min(3600, 30 * (2 ** min(failures - 1, 7)))
            self._retry_after[config_id] = datetime.now(tz=timezone.utc) + timedelta(seconds=retry_seconds)
            logger.error(
                "Error processing server %s: %s: %r; retrying in %ss",
                getattr(config, "name", "unknown"),
                type(e).__name__,
                e,
                retry_seconds,
            )
        finally:
            with contextlib.suppress(Exception):
                lock_db.query(func.pg_advisory_unlock(config_id)).scalar()
            lock_db.close()
            if completed:
                try:
                    with get_db_session() as db:
                        db_config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
                        if db_config:
                            db_config.last_check = datetime.now(tz=timezone.utc)
                except Exception as e:
                    logger.error("Error updating last_check for config %s: %s", config_id, e)

        return completed

    async def _process_gmail_api(self, config: SMTPConfig) -> None:
        """Use Gmail history checkpoints for OAuth-backed Gmail accounts."""
        client = GmailApiClient(config)
        try:
            with get_db_session() as db:
                account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                initial_sync_complete = account.initial_sync_complete
                history_id = account.provider_sync_token

            if not initial_sync_complete:
                await self._process_gmail_backfill(config, client)
                return

            if not history_id:
                self._reset_gmail_backfill(config.id)
                await self._process_gmail_backfill(config, client)
                return

            try:
                await self._process_gmail_history(config, client, history_id)
            except GmailHistoryExpired:
                logger.warning(
                    "Gmail history expired for account %s; starting a resumable full sync",
                    config.id,
                )
                self._reset_gmail_backfill(config.id)
                await self._process_gmail_backfill(config, client)
        finally:
            await client.close()

    async def _process_gmail_backfill(
        self, config: SMTPConfig, client: GmailApiClient
    ) -> None:
        """Process a bounded number of Gmail listing pages and persist progress."""
        from src.config import settings

        with get_db_session() as db:
            account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
            needs_snapshot = not account.initial_sync_complete and not account.sync_page_token

        if needs_snapshot:
            profile = await client.get_profile()
            history_id = str(profile.get("historyId") or "")
            if not history_id:
                raise RuntimeError("Gmail profile did not include a history ID")
            with get_db_session() as db:
                account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                if not account.initial_sync_complete and not account.sync_page_token:
                    account.sync_generation = (account.sync_generation or 0) + 1
                    account.provider_sync_token = history_id

        for _ in range(settings.gmail_backfill_pages_per_cycle):
            with get_db_session() as db:
                account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                if account.initial_sync_complete:
                    return
                page_token = account.sync_page_token
                generation = account.sync_generation

            page = await client.list_messages(
                page_token=page_token,
                max_results=settings.gmail_page_size,
            )
            message_ids = [str(item["id"]) for item in page.get("messages", [])]
            processed_count, missing_ids = await self._fetch_and_process_gmail_messages(
                client,
                message_ids,
                sync_generation=generation,
            )
            if processed_count:
                await self._update_server_stats(config, processed_count)

            next_page_token = page.get("nextPageToken")
            if next_page_token:
                with get_db_session() as db:
                    account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                    account.sync_page_token = str(next_page_token)
                continue

            self._complete_gmail_backfill(config.id, generation)
            logger.info(
                "Completed Gmail full sync generation %s for account %s (%s vanished during fetch)",
                generation,
                config.id,
                len(missing_ids),
            )
            return

    async def _process_gmail_history(
        self,
        config: SMTPConfig,
        client: GmailApiClient,
        start_history_id: str,
    ) -> None:
        """Apply bounded Gmail history pages without advancing early."""
        from src.config import settings

        for _ in range(settings.gmail_history_pages_per_cycle):
            with get_db_session() as db:
                account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                page_token = account.sync_page_token

            page = await client.list_history(start_history_id, page_token=page_token)
            changed_ids, deleted_ids = self._gmail_history_changes(page)
            processed_count, missing_ids = await self._fetch_and_process_gmail_messages(
                client,
                sorted(changed_ids - deleted_ids),
            )
            self._apply_gmail_deletions(config.id, deleted_ids | missing_ids)
            if processed_count:
                await self._update_server_stats(config, processed_count)

            next_page_token = page.get("nextPageToken")
            with get_db_session() as db:
                account = db.query(SMTPConfig).filter(SMTPConfig.id == config.id).one()
                if next_page_token:
                    account.sync_page_token = str(next_page_token)
                else:
                    account.provider_sync_token = str(
                        page.get("historyId") or start_history_id
                    )
                    account.sync_page_token = None
            if not next_page_token:
                return

    async def _fetch_and_process_gmail_messages(
        self,
        client: GmailApiClient,
        message_ids: list[str],
        *,
        sync_generation: int | None = None,
    ) -> tuple[int, set[str]]:
        """Fetch and commit small RFC822 chunks to cap peak memory use."""
        from src.config import settings

        concurrency = max(1, settings.gmail_request_concurrency)
        processed_count = 0
        missing: set[str] = set()
        for offset in range(0, len(message_ids), concurrency):
            chunk_ids = message_ids[offset : offset + concurrency]
            fetched = await asyncio.gather(
                *(client.get_parsed_message(message_id) for message_id in chunk_ids)
            )
            messages = []
            for message_id, message in zip(chunk_ids, fetched):
                if message is None:
                    missing.add(message_id)
                    continue
                if sync_generation is not None:
                    message["last_seen_sync_generation"] = sync_generation
                messages.append(message)
            await self._process_emails(messages)
            processed_count += len(messages)
        return processed_count, missing

    @staticmethod
    def _gmail_history_changes(page: dict) -> tuple[set[str], set[str]]:
        changed_ids: set[str] = set()
        deleted_ids: set[str] = set()
        for history in page.get("history", []):
            changed_ids.update(
                str(message["id"]) for message in history.get("messages", [])
            )
            for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                changed_ids.update(
                    str(event["message"]["id"])
                    for event in history.get(key, [])
                )
            deleted_ids.update(
                str(event["message"]["id"])
                for event in history.get("messagesDeleted", [])
            )
        return changed_ids, deleted_ids

    @staticmethod
    def _reset_gmail_backfill(config_id: int) -> None:
        with get_db_session() as db:
            account = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).one()
            account.initial_sync_complete = False
            account.provider_sync_token = None
            account.sync_page_token = None

    @staticmethod
    def _complete_gmail_backfill(config_id: int, generation: int) -> None:
        """Reconcile upstream removals only after every listing page succeeds."""
        from src.config import settings

        now = datetime.now(tz=timezone.utc)
        with get_db_session() as db:
            account = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).one()
            stale_messages = (
                db.query(EmailLog)
                .filter(
                    EmailLog.smtp_config_id == config_id,
                    (
                        EmailLog.last_seen_sync_generation.is_(None)
                        | (EmailLog.last_seen_sync_generation != generation)
                    ),
                )
                .all()
            )
            for message in stale_messages:
                if settings.upstream_delete_policy == "hard_delete":
                    db.delete(message)
                elif settings.upstream_delete_policy == "tombstone":
                    message.deleted_at = now
            account.initial_sync_complete = True
            account.sync_page_token = None

    @staticmethod
    def _apply_gmail_deletions(config_id: int, provider_message_ids: set[str]) -> None:
        if not provider_message_ids:
            return
        from src.config import settings

        now = datetime.now(tz=timezone.utc)
        with get_db_session() as db:
            messages = (
                db.query(EmailLog)
                .filter(
                    EmailLog.smtp_config_id == config_id,
                    EmailLog.provider_message_id.in_(provider_message_ids),
                )
                .all()
            )
            for message in messages:
                if settings.upstream_delete_policy == "hard_delete":
                    db.delete(message)
                elif settings.upstream_delete_policy == "tombstone":
                    message.deleted_at = now

    def _get_sync_since(self, config_id: int):
        """Resume near the newest stored email instead of rescanning the mailbox."""
        with get_db_session() as db:
            latest_email_date = (
                db.query(func.max(EmailLog.email_date))
                .filter(EmailLog.smtp_config_id == config_id)
                .scalar()
            )

        if latest_email_date is None:
            return None

        if latest_email_date.tzinfo is None:
            latest_email_date = latest_email_date.replace(tzinfo=timezone.utc)

        now = datetime.now(tz=timezone.utc)
        return min(latest_email_date, now) - timedelta(days=1)

    async def _process_emails(self, emails: List[dict]):
        """Process a batch of emails."""
        from src.email.attachment_handler import AttachmentHandler
        from src.storage_config.resolver import resolve_storage_config

        text_extractor = TextExtractor()
        attachment_handler = AttachmentHandler()

        for email_data in emails:
            try:
                with get_db_session() as db:
                    smtp_config = db.query(SMTPConfig).filter(SMTPConfig.id == email_data["smtp_config_id"]).first()
                    storage_config = resolve_storage_config(smtp_config)

                    # Check if email already exists (upsert pattern)
                    existing_email = (
                        db.query(EmailLog)
                        .filter(
                            EmailLog.smtp_config_id == email_data["smtp_config_id"],
                            EmailLog.provider_message_id == email_data["provider_message_id"],
                        )
                        .first()
                    )

                    if existing_email:
                        existing_email.provider_thread_id = sanitize_db_text(
                            email_data.get("provider_thread_id")
                        )
                        existing_email.folder = sanitize_db_text(email_data.get("folder"))
                        existing_email.flags = sanitize_db_text(email_data.get("flags"))
                        existing_email.deleted_at = None
                        if email_data.get("last_seen_sync_generation") is not None:
                            existing_email.last_seen_sync_generation = email_data[
                                "last_seen_sync_generation"
                            ]
                        logger.debug("Email already exists: %s", email_data["message_id"])
                        continue

                    # Upgrade a pre-cursor row in place on first replay after migration.
                    legacy_email = (
                        db.query(EmailLog)
                        .filter(
                            EmailLog.smtp_config_id == email_data["smtp_config_id"],
                            EmailLog.message_id == email_data["message_id"],
                            EmailLog.provider_message_id == EmailLog.message_id,
                        )
                        .first()
                    )
                    if legacy_email:
                        legacy_email.provider_message_id = email_data["provider_message_id"]
                        legacy_email.folder = email_data.get("folder")
                        legacy_email.imap_uid = email_data.get("imap_uid")
                        legacy_email.uid_validity = email_data.get("uid_validity")
                        legacy_email.flags = sanitize_db_text(email_data.get("flags"))
                        legacy_email.last_seen_sync_generation = email_data.get(
                            "last_seen_sync_generation"
                        )
                        logger.debug("Upgraded legacy provider reference for %s", email_data["message_id"])
                        continue

                    # Get body content, converting HTML to plain text if needed
                    body_plain = sanitize_db_text(email_data.get("body_plain", ""))
                    body_html = sanitize_db_text(email_data.get("body_html", ""))
                    if not body_plain and body_html:
                        body_plain = text_extractor._extract_html(body_html.encode("utf-8", errors="replace")) or ""

                    email_log = EmailLog(
                        smtp_config_id=email_data["smtp_config_id"],
                        sender=sanitize_db_text(email_data["sender"]),
                        recipient=sanitize_db_text(email_data["recipient"]),
                        subject=sanitize_db_text(email_data["subject"]),
                        message_id=sanitize_db_text(email_data["message_id"])[:255],
                        provider_message_id=sanitize_db_text(email_data["provider_message_id"])[:768],
                        provider_thread_id=sanitize_db_text(email_data.get("provider_thread_id"))[:768]
                        if email_data.get("provider_thread_id")
                        else None,
                        folder=sanitize_db_text(email_data.get("folder")),
                        imap_uid=email_data.get("imap_uid"),
                        uid_validity=email_data.get("uid_validity"),
                        flags=sanitize_db_text(email_data.get("flags")),
                        to_addresses=sanitize_db_text(email_data.get("to_addresses")),
                        cc_addresses=sanitize_db_text(email_data.get("cc_addresses")),
                        bcc_addresses=sanitize_db_text(email_data.get("bcc_addresses")),
                        in_reply_to=sanitize_db_text(email_data.get("in_reply_to")),
                        references=sanitize_db_text(email_data.get("references")),
                        last_seen_sync_generation=email_data.get(
                            "last_seen_sync_generation"
                        ),
                        email_date=email_data["email_date"],
                        attachment_count=email_data["attachment_count"],
                        body_plain=body_plain,
                        body_html=body_html,
                    )

                    db.add(email_log)
                    db.flush()

                    if email_data["attachment_count"] > 0 and "raw_email" in email_data:
                        attachments = await attachment_handler.extract_attachments(
                            email_data["raw_email"], email_log.id, storage_config
                        )
                        for attachment in attachments:
                            db.add(attachment)

                        email_log.attachment_count = len(attachments)

                logger.info(
                    "Processed provider message for account %s (%s attachments)",
                    email_data["smtp_config_id"],
                    email_data["attachment_count"],
                )

            except Exception as e:
                logger.error("Error processing email %s: %s", email_data.get("message_id", "unknown"), e)
                raise

    async def _update_server_stats(self, config: SMTPConfig, email_count: int):
        """Update server statistics."""
        try:
            config_id = config.id  # Store ID to avoid detachment issues
            with get_db_session() as db:
                db_config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
                if db_config:
                    db_config.total_emails_processed += email_count
        except Exception as e:
            logger.error("Error updating server stats: %s", e)

    def _persist_sync_cursors(self, emails: List[dict]) -> None:
        """Advance a folder cursor only after its message batch commits."""
        from src.models.sync_cursor import MailSyncCursor

        by_folder = {}
        for email in emails:
            folder = email.get("folder")
            uid = email.get("imap_uid")
            if folder and uid is not None:
                state = by_folder.setdefault(
                    (email["smtp_config_id"], folder),
                    {"last_uid": uid, "uid_validity": email.get("uid_validity")},
                )
                state["last_uid"] = max(state["last_uid"], uid)

        with get_db_session() as db:
            for (account_id, folder), state in by_folder.items():
                cursor = (
                    db.query(MailSyncCursor)
                    .filter(
                        MailSyncCursor.smtp_config_id == account_id,
                        MailSyncCursor.folder == folder,
                    )
                    .first()
                )
                if not cursor:
                    cursor = MailSyncCursor(smtp_config_id=account_id, folder=folder)
                    db.add(cursor)
                cursor.uid_validity = state["uid_validity"]
                cursor.last_uid = max(cursor.last_uid or 0, state["last_uid"])
                cursor.last_success_at = datetime.now(tz=timezone.utc)
                cursor.last_error = None

    def _persist_client_cursors(self, account_id: int, client: SMTPClient) -> None:
        """Persist idle-folder checkpoints as well as checkpoints from message batches."""
        from src.models.sync_cursor import MailSyncCursor

        with get_db_session() as db:
            for folder, last_uid in client._last_uids.items():
                cursor = (
                    db.query(MailSyncCursor)
                    .filter(
                        MailSyncCursor.smtp_config_id == account_id,
                        MailSyncCursor.folder == folder,
                    )
                    .first()
                )
                if not cursor:
                    cursor = MailSyncCursor(smtp_config_id=account_id, folder=folder)
                    db.add(cursor)
                cursor.uid_validity = client._uid_validities.get(folder)
                cursor.last_uid = max(cursor.last_uid or 0, last_uid)
                cursor.last_success_at = datetime.now(tz=timezone.utc)
                cursor.last_error = None

    async def _reconcile_provider_state(self, config_id: int, client: SMTPClient) -> None:
        """Periodically mirror upstream deletions and flags without downloading bodies."""
        from src.config import settings

        now = datetime.now(tz=timezone.utc)
        last = self._last_reconciliations.get(config_id)
        if last and (now - last).total_seconds() < settings.deletion_reconcile_interval:
            return
        snapshots = await client.fetch_folder_state()
        with get_db_session() as db:
            for folder, state in snapshots.items():
                messages = (
                    db.query(EmailLog)
                    .filter(
                        EmailLog.smtp_config_id == config_id,
                        EmailLog.folder == folder,
                        EmailLog.uid_validity == state["uid_validity"],
                        EmailLog.imap_uid.is_not(None),
                    )
                    .all()
                )
                available = state["uids"]
                flags = state["flags"]
                for message in messages:
                    if message.imap_uid not in available:
                        if settings.upstream_delete_policy == "hard_delete":
                            db.delete(message)
                        elif settings.upstream_delete_policy == "tombstone":
                            message.deleted_at = now
                        continue
                    message.deleted_at = None
                    if message.imap_uid in flags:
                        message.flags = flags[message.imap_uid]
        self._last_reconciliations[config_id] = now

    async def process_server_now(self, server_id: int, owner_user_id: int | None = None) -> dict:
        """Manually trigger processing for a specific server."""
        try:
            with get_db_session() as db:
                query = db.query(SMTPConfig).filter(SMTPConfig.id == server_id)
                if owner_user_id is not None:
                    query = query.filter(SMTPConfig.owner_user_id == owner_user_id)
                config = query.first()
                if not config:
                    return {"error": "Server not found"}

                if not config.enabled:
                    return {"error": "Server is disabled"}

                # Create a detached copy before closing the session, matching
                # the pattern used in _process_all_servers. This avoids nested
                # session conflicts when _process_server opens its own sessions.
                config_copy = SMTPConfig.create_detached(config)

            completed = await self._process_server(config_copy, force=True)
            if not completed:
                return {"error": f"Processing failed for {config_copy.name}; check server logs"}
            return {"success": True, "message": f"Processed emails from {config_copy.name}"}

        except Exception as e:
            logger.error("Manual processing failed for server %s: %s", server_id, e)
            return {"error": str(e)}
