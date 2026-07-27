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
        self._last_uids: Dict[str, int] = {}
        self._uid_validities: Dict[str, int] = {}

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
            logger.error(
                "Login failed for %s: %s - %s",
                self.config.name,
                login_response.result,
                getattr(login_response, "lines", []),
            )
            await self.disconnect()
            return False

        except Exception as e:
            logger.error("Connection failed for %s: %s: %r", self.config.name, type(e).__name__, e)
            await self.disconnect()
            return False

    async def disconnect(self):
        """Disconnect from the IMAP server."""
        client = self.client
        self.client = None
        self._connected = False
        if client:
            with contextlib.suppress(Exception):
                await client.logout()
            logger.info("Disconnected from %s", self.config.name)

    async def _ensure_connected(self) -> bool:
        """Reconnect when a server has closed a cached IMAP connection."""
        if self.client and self._connected:
            try:
                response = await self.client.noop()
                if response.result == "OK":
                    return True
                logger.warning("IMAP NOOP failed for %s: %s", self.config.name, response.result)
            except Exception as e:
                logger.warning("Cached IMAP connection is stale for %s: %s: %r", self.config.name, type(e).__name__, e)
            await self.disconnect()

        return await self.connect()

    BATCH_SIZE = 10

    async def fetch_new_emails(self, limit: Optional[int] = None, since: Optional[datetime] = None):
        """Fetch new emails from all folders, yielding batches of BATCH_SIZE.

        Yields:
            List[Dict]: A batch of parsed email dicts.
        """
        if not await self._ensure_connected():
            raise ConnectionError(f"Could not connect to {self.config.name}")

        try:
            folders = await self._get_folders()

            for folder in folders:
                try:
                    async for batch in self._fetch_folder(folder, limit, since):
                        yield batch
                except Exception as e:
                    logger.error(
                        "Error processing folder %s for %s: %s: %r",
                        folder,
                        self.config.name,
                        type(e).__name__,
                        e,
                    )
                    raise

        except Exception as e:
            await self.disconnect()
            logger.error("Error fetching emails from %s: %s: %r", self.config.name, type(e).__name__, e)
            raise

    async def _get_folders(self) -> List[str]:
        """Get list of folders to sync."""
        list_response = await self.client.list('""', "*")
        if list_response.result != "OK":
            raise RuntimeError(f"Failed to list folders for {self.config.name}: {list_response.result}")

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

    async def _fetch_folder(
        self, folder: str, limit: Optional[int] = None, since: Optional[datetime] = None
    ):
        """Fetch emails from a single folder, yielding batches.

        Yields:
            List[Dict]: A batch of parsed email dicts.
        """
        select_response = await self.client.select(f'"{folder}"')
        if select_response.result != "OK":
            raise RuntimeError(f"Cannot select folder {folder} for {self.config.name}")

        uid_validity = self._extract_uid_validity(select_response.lines)
        previous_uid_validity = self._uid_validities.get(folder)
        if previous_uid_validity and uid_validity and previous_uid_validity != uid_validity:
            logger.warning("UIDVALIDITY changed for %s in %s; resetting checkpoint", folder, self.config.name)
            self._last_uids.pop(folder, None)
        if uid_validity:
            self._uid_validities[folder] = uid_validity

        last_uid = self._last_uids.get(folder)
        uid_next = self._extract_uid_next(select_response.lines)
        if last_uid is not None and uid_next is not None and last_uid >= uid_next - 1:
            logger.debug("No new UIDs in folder %s for %s", folder, self.config.name)
            return

        if last_uid is not None:
            search_criteria = ("UID", f"{last_uid + 1}:*")
        elif since is not None:
            search_criteria = ("SINCE", self._format_imap_date(since))
        else:
            search_criteria = ("ALL",)

        search_response = await self.client.search(*search_criteria)
        if search_response.result != "OK":
            raise RuntimeError(f"Search failed in folder {folder} for {self.config.name}")

        message_ids = search_response.lines[0].decode().split()
        if not message_ids:
            logger.debug("No emails found in folder %s for %s", folder, self.config.name)
            return

        if limit and len(message_ids) > limit:
            message_ids = message_ids[-limit:]

        total = len(message_ids)
        logger.info(
            "Found %s emails in folder %s for %s using %s",
            total,
            folder,
            self.config.name,
            " ".join(search_criteria),
        )

        batch = []
        highest_uid = last_uid
        for i, msg_id in enumerate(message_ids):
            try:
                fetch_response = await self.client.fetch(msg_id, "(UID RFC822)")
                if fetch_response.result == "OK":
                    raw_email = fetch_response.lines[1]
                    message_uid = self._extract_message_uid(fetch_response.lines)
                    if last_uid is not None and message_uid is not None and message_uid <= last_uid:
                        logger.debug("Ignoring replayed UID %s in %s", message_uid, folder)
                        continue
                    email_data = await self._parse_email(raw_email, str(message_uid or msg_id))
                    if email_data:
                        batch.append(email_data)
                    if message_uid is not None:
                        highest_uid = max(highest_uid or 0, message_uid)
                else:
                    raise RuntimeError(
                        f"Failed to fetch message {msg_id} from {folder} in {self.config.name}"
                    )
            except Exception as e:
                logger.error(
                    "Error fetching message %s from %s in %s: %s: %r",
                    msg_id,
                    folder,
                    self.config.name,
                    type(e).__name__,
                    e,
                )
                raise

            # Yield batch when full
            if len(batch) >= self.BATCH_SIZE:
                logger.info("Progress: %s/%s fetched from %s in %s", i + 1, total, folder, self.config.name)
                yield batch
                batch = []

        # Yield remaining
        if batch:
            logger.info("Progress: %s/%s fetched from %s in %s", i + 1, total, folder, self.config.name)
            yield batch

        if highest_uid is not None:
            self._last_uids[folder] = highest_uid

    @staticmethod
    def _format_imap_date(value: datetime) -> str:
        """Format a date for an IMAP SEARCH command."""
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%d-%b-%Y")

    @staticmethod
    def _extract_message_uid(lines) -> Optional[int]:
        """Extract the stable UID returned with FETCH metadata."""
        for line in lines:
            if isinstance(line, bytes):
                match = re.search(rb"\bUID\s+(\d+)\b", line)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _extract_uid_validity(lines) -> Optional[int]:
        """Extract UIDVALIDITY from a SELECT response when available."""
        for line in lines:
            if isinstance(line, bytes):
                match = re.search(rb"\bUIDVALIDITY\s+(\d+)\b", line)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _extract_uid_next(lines) -> Optional[int]:
        """Extract the next predicted UID from a SELECT response."""
        for line in lines:
            if isinstance(line, bytes):
                match = re.search(rb"\bUIDNEXT\s+(\d+)\b", line)
                if match:
                    return int(match.group(1))
        return None

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
