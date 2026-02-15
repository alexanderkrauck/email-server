"""Email processing and orchestration."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from src.database.connection import get_db_session
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
                await asyncio.sleep(30)  # Check every 30 seconds
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

    async def _process_server(self, config: SMTPConfig):
        """Process emails from a single server."""
        config_id = config.id
        config_host = config.host

        try:
            # Get or create client for this server
            client_key = f"{config_id}_{config_host}"
            if client_key not in self.active_clients:
                self.active_clients[client_key] = SMTPClient(config)

            client = self.active_clients[client_key]

            # Fetch and process emails in batches
            # Stats are updated incrementally after each batch so progress
            # is persisted even if a later batch fails (e.g. IMAP timeout
            # on large mailboxes like Gmail All Mail).
            async for batch in client.fetch_new_emails():
                await self._process_emails(batch)
                if batch:
                    await self._update_server_stats(config, len(batch))

        except Exception as e:
            logger.error("Error processing server %s: %s", getattr(config, "name", "unknown"), e)
        finally:
            # Always update last_check, even if processing was interrupted
            try:
                with get_db_session() as db:
                    db_config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
                    if db_config:
                        db_config.last_check = datetime.now(tz=timezone.utc)
            except Exception as e:
                logger.error("Error updating last_check for config %s: %s", config_id, e)

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
                    existing_email = db.query(EmailLog).filter(EmailLog.message_id == email_data["message_id"]).first()

                    if existing_email:
                        logger.debug("Email already exists: %s", email_data["message_id"])
                        continue

                    # Get body content, converting HTML to plain text if needed
                    body_plain = email_data.get("body_plain", "")
                    body_html = email_data.get("body_html", "")
                    if not body_plain and body_html:
                        body_plain = text_extractor._extract_html(body_html.encode("utf-8", errors="replace")) or ""

                    email_log = EmailLog(
                        smtp_config_id=email_data["smtp_config_id"],
                        sender=email_data["sender"],
                        recipient=email_data["recipient"],
                        subject=email_data["subject"],
                        message_id=email_data["message_id"],
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
                    "Processed email: %s -> %s... (%s attachments)",
                    email_data["sender"],
                    email_data["subject"][:50],
                    email_data["attachment_count"],
                )

            except Exception as e:
                logger.error("Error processing email %s: %s", email_data.get("message_id", "unknown"), e)

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

    async def process_server_now(self, server_id: int) -> dict:
        """Manually trigger processing for a specific server."""
        try:
            with get_db_session() as db:
                config = db.query(SMTPConfig).filter(SMTPConfig.id == server_id).first()
                if not config:
                    return {"error": "Server not found"}

                if not config.enabled:
                    return {"error": "Server is disabled"}

                # Create a detached copy before closing the session, matching
                # the pattern used in _process_all_servers. This avoids nested
                # session conflicts when _process_server opens its own sessions.
                config_copy = SMTPConfig.create_detached(config)

            await self._process_server(config_copy)
            return {"success": True, "message": f"Processed emails from {config_copy.name}"}

        except Exception as e:
            logger.error("Manual processing failed for server %s: %s", server_id, e)
            return {"error": str(e)}
