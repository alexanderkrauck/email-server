"""SMTP/IMAP client for connecting to email servers."""

import aioimaplib
import asyncio
import logging
import ssl
from typing import List, Dict, Optional, Tuple
from email import message_from_bytes
from email.message import EmailMessage
from src.models.smtp_config import SMTPConfig
from datetime import datetime

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
            # Create SSL context
            ssl_context = ssl.create_default_context()
            if not self.config.use_ssl:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            # Connect to IMAP server
            if self.config.use_ssl:
                self.client = aioimaplib.IMAP4_SSL(
                    host=self.config.host,
                    port=self.config.port,
                    ssl_context=ssl_context
                )
            else:
                self.client = aioimaplib.IMAP4(
                    host=self.config.host,
                    port=self.config.port
                )

            await self.client.wait_hello_from_server()

            # Start TLS if required
            if self.config.use_tls and not self.config.use_ssl:
                await self.client.starttls(ssl_context=ssl_context)

            # Login
            login_response = await self.client.login(
                self.config.username,
                self.config.password
            )

            if login_response.result == "OK":
                self._connected = True
                logger.info(f"Successfully connected to {self.config.name} ({self.config.host})")
                return True
            else:
                logger.error(f"Login failed for {self.config.name}: {login_response.result} - {login_response.data}")
                return False

        except Exception as e:
            logger.error(f"Connection failed for {self.config.name}: {type(e).__name__}: {e}")
            return False

    async def disconnect(self):
        """Disconnect from the IMAP server."""
        if self.client and self._connected:
            try:
                await self.client.logout()
                self._connected = False
                logger.info(f"Disconnected from {self.config.name}")
            except Exception as e:
                logger.error(f"Error disconnecting from {self.config.name}: {e}")

    async def fetch_new_emails(self, limit: int = None) -> List[Dict]:
        """Fetch new emails from the server."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            # Select inbox
            await self.client.select("INBOX")

            # Search for all emails
            search_response = await self.client.search("ALL")
            if search_response.result != "OK":
                logger.warning(f"Search failed for {self.config.name}")
                return []

            # Get message UIDs
            message_ids = search_response.lines[0].decode().split()
            if not message_ids:
                logger.debug(f"No emails found for {self.config.name}")
                return []

            # Limit the number of emails to process (if limit specified)
            if limit and len(message_ids) > limit:
                message_ids = message_ids[-limit:]

            emails = []
            for msg_id in message_ids:
                try:
                    # Fetch email
                    fetch_response = await self.client.fetch(msg_id, "(RFC822)")
                    if fetch_response.result == "OK":
                        raw_email = fetch_response.lines[1]
                        email_data = await self._parse_email(raw_email, msg_id)
                        if email_data:
                            emails.append(email_data)

                        # Don't mark as seen - preserve original read status
                    else:
                        logger.warning(f"Failed to fetch message {msg_id} from {self.config.name}")

                except Exception as e:
                    logger.error(f"Error processing message {msg_id} from {self.config.name}: {e}")
                    continue

            logger.info(f"Fetched {len(emails)} emails from {self.config.name}")
            return emails

        except Exception as e:
            logger.error(f"Error fetching emails from {self.config.name}: {e}")
            return []

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
                    from email.utils import parsedate_to_datetime
                    email_date = parsedate_to_datetime(date_str)
                except:
                    email_date = datetime.utcnow()

            # Extract body content
            body_plain = ""
            body_html = ""

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        body_plain += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    elif content_type == "text/html":
                        body_html += part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                if payload:
                    decoded = payload.decode('utf-8', errors='ignore')
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
                "raw_email": raw_email
            }

        except Exception as e:
            logger.error(f"Error parsing email from {self.config.name}: {e}")
            return None

    def __del__(self):
        """Cleanup on deletion."""
        if self.client and self._connected:
            try:
                # This is a sync method, so we can't await
                self.client.close()
            except:
                pass