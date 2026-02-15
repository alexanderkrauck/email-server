"""Email processing and orchestration."""

import asyncio
import logging
from typing import List
from src.database.connection import get_db_session
from src.models.smtp_config import SMTPConfig
from src.models.email import EmailLog
from src.email.smtp_client import SMTPClient
from datetime import datetime

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
                logger.error(f"Error in email processing loop: {e}")
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

        # Process each server with detached config copies
        tasks = []
        for config_copy in config_copies:
            task = asyncio.create_task(self._process_server(config_copy))
            tasks.append(task)

            # Wait for all servers to complete
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_server(self, config: SMTPConfig):
        """Process emails from a single server."""
        try:
            # Store config attributes to avoid detachment issues
            config_id = config.id
            config_host = config.host

            # Get or create client for this server
            client_key = f"{config_id}_{config_host}"
            if client_key not in self.active_clients:
                self.active_clients[client_key] = SMTPClient(config)

            client = self.active_clients[client_key]

            # Fetch new emails
            emails = await client.fetch_new_emails()

            if emails:
                await self._process_emails(emails)
                await self._update_server_stats(config, len(emails))

            # Update last check time
            with get_db_session() as db:
                db_config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
                if db_config:
                    db_config.last_check = datetime.utcnow()

        except Exception as e:
            logger.error(f"Error processing server {getattr(config, 'name', 'unknown')}: {e}")

    async def _process_emails(self, emails: List[dict]):
        """Process a batch of emails."""
        from src.email.email_logger import EmailLogger
        from src.email.attachment_handler import AttachmentHandler
        from src.email.text_extractor import TextExtractor
        from src.storage_config.resolver import resolve_storage_config

        logger_instance = EmailLogger()
        attachment_handler = AttachmentHandler()
        text_extractor = TextExtractor()

        for email_data in emails:
            try:
                with get_db_session() as db:
                    smtp_config = db.query(SMTPConfig).filter(SMTPConfig.id == email_data["smtp_config_id"]).first()
                    storage_config = resolve_storage_config(smtp_config)
                    account_email = None
                    if smtp_config:
                        account_email = smtp_config.account_name if smtp_config.account_name else smtp_config.username
                    
                    # Check if email already exists (upsert pattern)
                    existing_email = db.query(EmailLog).filter(
                        EmailLog.message_id == email_data["message_id"]
                    ).first()
                    
                    if existing_email:
                        logger.debug(f"Email already exists: {email_data['message_id']}")
                        continue
                    
                    email_log = EmailLog(
                        smtp_config_id=email_data["smtp_config_id"],
                        sender=email_data["sender"],
                        recipient=email_data["recipient"],
                        subject=email_data["subject"],
                        message_id=email_data["message_id"],
                        email_date=email_data["email_date"],
                        attachment_count=email_data["attachment_count"]
                    )

                    db.add(email_log)
                    db.flush()

                    file_path = await logger_instance.log_email_to_file(email_data, email_log.id, account_email)
                    if not file_path:
                        raise ValueError(f"Failed to write email to file for message_id: {email_data['message_id']}")
                    email_log.log_file_path = file_path

                    if email_data["attachment_count"] > 0 and "raw_email" in email_data:
                        attachments = await attachment_handler.extract_attachments(
                            email_data["raw_email"], email_log.id, account_email, storage_config
                        )
                        for attachment in attachments:
                            db.add(attachment)

                        email_log.attachment_count = len(attachments)

                logger.info(f"Processed email: {email_data['sender']} -> {email_data['subject'][:50]}... ({email_data['attachment_count']} attachments)")

            except Exception as e:
                logger.error(f"Error processing email {email_data.get('message_id', 'unknown')}: {e}")

    async def _update_server_stats(self, config: SMTPConfig, email_count: int):
        """Update server statistics."""
        try:
            config_id = config.id  # Store ID to avoid detachment issues
            with get_db_session() as db:
                db_config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
                if db_config:
                    db_config.total_emails_processed += email_count
        except Exception as e:
            logger.error(f"Error updating server stats: {e}")

    async def process_server_now(self, server_id: int) -> dict:
        """Manually trigger processing for a specific server."""
        try:
            with get_db_session() as db:
                config = db.query(SMTPConfig).filter(SMTPConfig.id == server_id).first()
                if not config:
                    return {"error": "Server not found"}

                if not config.enabled:
                    return {"error": "Server is disabled"}

                # Create a new config object within the same session to avoid detachment
                await self._process_server(config)
                return {"success": True, "message": f"Processed emails from {config.name}"}

        except Exception as e:
            logger.error(f"Manual processing failed for server {server_id}: {e}")
            return {"error": str(e)}