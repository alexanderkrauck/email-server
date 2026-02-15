"""SMTP/IMAP client for connecting to email servers."""

import contextlib
import logging
import re
import ssl
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import aioimaplib

from src.models.smtp_config import SMTPConfig

logger = logging.getLogger(__name__)


class SMTPClient:
    """Client for connecting to SMTP/IMAP servers and fetching emails."""

    def __init__(self, smtp_config: SMTPConfig):
        self.config = smtp_config
        self.client = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to the IMAP server."""
        try:
            # Use IMAP-specific SSL/TLS settings
            imap_use_ssl = getattr(self.config, "imap_use_ssl", True)
            imap_use_tls = getattr(self.config, "imap_use_tls", False)

            # Create SSL context
            ssl_context = ssl.create_default_context()
            if not imap_use_ssl:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            # Connect to IMAP server
            if imap_use_ssl:
                self.client = aioimaplib.IMAP4_SSL(
                    host=self.config.host, port=self.config.port, ssl_context=ssl_context
                )
            else:
                self.client = aioimaplib.IMAP4(host=self.config.host, port=self.config.port)

            await self.client.wait_hello_from_server()

            # Start TLS if required
            if imap_use_tls and not imap_use_ssl:
                await self.client.starttls(ssl_context=ssl_context)

            # Login
            login_response = await self.client.login(self.config.username, self.config.password)

            if login_response.result == "OK":
                self._connected = True
                logger.info("Successfully connected to %s (%s)", self.config.name, self.config.host)
                return True
            logger.error("Login failed for %s: %s - %s", self.config.name, login_response.result, login_response.data)
            return False

        except Exception as e:
            logger.error("Connection failed for %s: %s: %s", self.config.name, type(e).__name__, e)
            return False

    async def disconnect(self):
        """Disconnect from the IMAP server."""
        if self.client and self._connected:
            try:
                await self.client.logout()
                self._connected = False
                logger.info("Disconnected from %s", self.config.name)
            except Exception as e:
                logger.error("Error disconnecting from %s: %s", self.config.name, e)

    BATCH_SIZE = 10

    async def fetch_new_emails(self, limit: Optional[int] = None):
        """Fetch new emails from all folders, yielding batches of BATCH_SIZE.

        Yields:
            List[Dict]: A batch of parsed email dicts.
        """
        if not self._connected and not await self.connect():
            return

        try:
            folders = await self._get_folders()
            if not folders:
                return

            for folder in folders:
                try:
                    async for batch in self._fetch_folder(folder, limit):
                        yield batch
                except Exception as e:
                    logger.error("Error processing folder %s for %s: %s", folder, self.config.name, e)
                    continue

        except Exception as e:
            logger.error("Error fetching emails from %s: %s", self.config.name, e)

    async def _get_folders(self) -> List[str]:
        """Get list of folders to sync."""
        list_response = await self.client.list('""', "*")
        if list_response.result != "OK":
            logger.warning("Failed to list folders for %s", self.config.name)
            return []

        folders = []
        for line in list_response.lines:
            decoded = line.decode("utf-8", errors="ignore")
            matches = re.findall(r'"([^"]+)"', decoded)
            if matches and len(matches) >= 2:
                folder_name = matches[-1]
                if folder_name not in [".", "/", "\\"]:
                    folders.append(folder_name)

        if not folders:
            folders = ["INBOX"]

        # For Gmail, sync All Mail which contains everything in one folder
        if "gmail.com" in self.config.host.lower():
            all_mail_folders = [f for f in folders if "All Mail" in f or "Alle Nachrichten" in f]
            if all_mail_folders:
                folders = all_mail_folders
                logger.info("Using Gmail All Mail folder: %s", folders)
            else:
                folders = ["INBOX"]
                logger.warning("Gmail All Mail folder not found, falling back to INBOX")

        logger.info("Found %s folders for %s: %s", len(folders), self.config.name, folders)
        return folders

    async def _fetch_folder(self, folder: str, limit: Optional[int] = None):
        """Fetch emails from a single folder, yielding batches.

        Yields:
            List[Dict]: A batch of parsed email dicts.
        """
        select_response = await self.client.select(f'"{folder}"')
        if select_response.result != "OK":
            logger.debug("Cannot select folder %s for %s, skipping", folder, self.config.name)
            return

        search_response = await self.client.search("ALL")
        if search_response.result != "OK":
            logger.warning("Search failed in folder %s for %s", folder, self.config.name)
            return

        message_ids = search_response.lines[0].decode().split()
        if not message_ids:
            logger.debug("No emails found in folder %s for %s", folder, self.config.name)
            return

        total = len(message_ids)
        logger.info("Found %s emails in folder %s for %s", total, folder, self.config.name)

        if limit and total > limit:
            message_ids = message_ids[-limit:]

        batch = []
        for i, msg_id in enumerate(message_ids):
            try:
                fetch_response = await self.client.fetch(msg_id, "(RFC822)")
                if fetch_response.result == "OK":
                    raw_email = fetch_response.lines[1]
                    email_data = await self._parse_email(raw_email, msg_id)
                    if email_data:
                        batch.append(email_data)
                else:
                    logger.warning("Failed to fetch message %s from %s in %s", msg_id, folder, self.config.name)
            except Exception as e:
                logger.error("Error fetching message %s from %s in %s: %s", msg_id, folder, self.config.name, e)
                continue

            # Yield batch when full
            if len(batch) >= self.BATCH_SIZE:
                logger.info("Progress: %s/%s fetched from %s in %s", i + 1, total, folder, self.config.name)
                yield batch
                batch = []

        # Yield remaining
        if batch:
            logger.info("Progress: %s/%s fetched from %s in %s", i + 1, total, folder, self.config.name)
            yield batch

    async def _parse_email(self, raw_email: bytes, uid: str) -> Optional[Dict]:
        """Parse raw email data into structured format."""
        try:
            msg = message_from_bytes(raw_email)

            # Extract basic info
            sender = msg.get("From", "")
            recipient = msg.get("To", "")
            subject = msg.get("Subject", "")
            message_id = msg.get("Message-ID", f"uid_{uid}_{self.config.id}")
            date_str = msg.get("Date", "")

            # Parse date
            email_date = None
            if date_str:
                try:
                    email_date = parsedate_to_datetime(date_str)
                except Exception:
                    email_date = datetime.now(tz=timezone.utc)

            # Extract body content
            body_plain = ""
            body_html = ""

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    if content_type == "text/plain":
                        body_plain += payload.decode("utf-8", errors="ignore")
                    elif content_type == "text/html":
                        body_html += payload.decode("utf-8", errors="ignore")
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                if payload:
                    decoded = payload.decode("utf-8", errors="ignore")
                    if content_type == "text/plain":
                        body_plain = decoded
                    elif content_type == "text/html":
                        body_html = decoded

            # Count attachments
            attachment_count = 0
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_disposition() == "attachment":
                        attachment_count += 1

            return {
                "smtp_config_id": self.config.id,
                "sender": sender[:500],  # Limit length
                "recipient": recipient[:500],
                "subject": subject,
                "message_id": message_id,
                "body_plain": body_plain,
                "body_html": body_html,
                "email_date": email_date,
                "content_size": len(raw_email),
                "attachment_count": attachment_count,
                "raw_email": raw_email,
            }

        except Exception as e:
            logger.error("Error parsing email from %s: %s", self.config.name, e)
            return None

    def __del__(self):
        """Cleanup on deletion."""
        if self.client and self._connected:
            with contextlib.suppress(Exception):
                # This is a sync method, so we can't await
                self.client.close()
