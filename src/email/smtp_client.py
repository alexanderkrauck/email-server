"""SMTP/IMAP client for connecting to email servers."""

import logging
import re
import ssl
from typing import List, Dict, Optional

import aioimaplib
from datetime import datetime
from email import message_from_bytes
from email.utils import parsedate_to_datetime

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
            imap_use_ssl = getattr(self.config, 'imap_use_ssl', True)
            imap_use_tls = getattr(self.config, 'imap_use_tls', False)

            # Create SSL context
            ssl_context = ssl.create_default_context()
            if not imap_use_ssl:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            # Connect to IMAP server
            if imap_use_ssl:
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
            if imap_use_tls and not imap_use_ssl:
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
        """Fetch new emails from all folders on the server."""
        if not self._connected:
            if not await self.connect():
                return []

        try:
            # Get list of all folders
            list_response = await self.client.list('""', '*')
            if list_response.result != "OK":
                logger.warning(f"Failed to list folders for {self.config.name}")
                return []

            # Parse folder names from the response
            folders = []
            for line in list_response.lines:
                # Parse IMAP LIST response format
                # Example: (\HasNoChildren) "/" "INBOX"
                # or: (\HasNoChildren) "." "INBOX.Sent"
                decoded = line.decode('utf-8', errors='ignore')
                # Extract folder name - it's usually the last quoted string
                matches = re.findall(r'"([^"]+)"', decoded)
                if matches and len(matches) >= 2:
                    # The last match is usually the folder name
                    folder_name = matches[-1]
                    # Skip hierarchy delimiters returned as folders
                    if folder_name not in ['.', '/', '\\']:
                        folders.append(folder_name)

            if not folders:
                # Fallback to INBOX if no folders found
                folders = ["INBOX"]

            logger.info(f"Found {len(folders)} folders for {self.config.name}: {folders}")

            all_emails = []
            for folder in folders:
                try:
                    # Select the folder
                    select_response = await self.client.select(f'"{folder}"')
                    if select_response.result != "OK":
                        logger.debug(f"Cannot select folder {folder} for {self.config.name}, skipping")
                        continue

                    # Search for all emails in this folder
                    search_response = await self.client.search("ALL")
                    if search_response.result != "OK":
                        logger.warning(f"Search failed in folder {folder} for {self.config.name}")
                        continue

                    # Get message UIDs
                    message_ids = search_response.lines[0].decode().split()
                    if not message_ids:
                        logger.debug(f"No emails found in folder {folder} for {self.config.name}")
                        continue

                    # Limit the number of emails to process per folder (if limit specified)
                    if limit and len(message_ids) > limit:
                        message_ids = message_ids[-limit:]

                    folder_emails = []
                    for msg_id in message_ids:
                        try:
                            # Fetch email
                            fetch_response = await self.client.fetch(msg_id, "(RFC822)")
                            if fetch_response.result == "OK":
                                raw_email = fetch_response.lines[1]
                                email_data = await self._parse_email(raw_email, msg_id)
                                if email_data:
                                    folder_emails.append(email_data)

                                # Don't mark as seen - preserve original read status
                            else:
                                logger.warning(f"Failed to fetch message {msg_id} from folder {folder} in {self.config.name}")

                        except Exception as e:
                            logger.error(f"Error processing message {msg_id} from folder {folder} in {self.config.name}: {e}")
                            continue

                    if folder_emails:
                        logger.info(f"Fetched {len(folder_emails)} emails from folder {folder} in {self.config.name}")
                        all_emails.extend(folder_emails)

                except Exception as e:
                    logger.error(f"Error processing folder {folder} for {self.config.name}: {e}")
                    continue

            logger.info(f"Total fetched {len(all_emails)} emails from all folders in {self.config.name}")
            return all_emails

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
                    email_date = parsedate_to_datetime(date_str)
                except Exception:
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
            except Exception:
                pass