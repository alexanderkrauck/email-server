"""SMTP/IMAP client for connecting to email servers."""

import asyncio
import contextlib
import json
import logging
import re
import ssl
from datetime import datetime, timezone
from email import policy
from email import message_from_bytes
from email.utils import getaddresses, parsedate_to_datetime
from typing import Dict, List, Optional

import aioimaplib

from src.models.smtp_config import SMTPConfig

logger = logging.getLogger(__name__)


class SMTPClient:
    """Client for connecting to SMTP/IMAP servers and fetching emails."""

    CONNECTION_FIELDS = (
        "host",
        "port",
        "username",
        "credential_ciphertext",
        "password",
        "auth_type",
        "imap_use_ssl",
        "imap_use_tls",
    )

    def __init__(self, smtp_config: SMTPConfig):
        self.config = smtp_config
        self.client = None
        self._connected = False
        cursors = getattr(smtp_config, "sync_cursors", {}) or {}
        self._last_uids: Dict[str, int] = {
            folder: state["last_uid"] for folder, state in cursors.items() if state.get("last_uid") is not None
        }
        self._uid_validities: Dict[str, int] = {
            folder: state["uid_validity"] for folder, state in cursors.items() if state.get("uid_validity") is not None
        }
        self._folder_backfill_complete: Dict[str, bool] = {
            folder: bool(state.get("backfill_complete"))
            for folder, state in cursors.items()
        }
        self._discovered_folders: List[str] = []
        self.backfill_complete = bool(cursors) and all(
            self._folder_backfill_complete.values()
        )

    async def update_config(self, smtp_config: SMTPConfig):
        """Apply changed account settings and reconnect when required."""
        old_settings = tuple(getattr(self.config, field, None) for field in self.CONNECTION_FIELDS)
        new_settings = tuple(getattr(smtp_config, field, None) for field in self.CONNECTION_FIELDS)
        self.config = smtp_config
        for folder, state in (getattr(smtp_config, "sync_cursors", {}) or {}).items():
            incoming_validity = state.get("uid_validity")
            validity_changed = (
                self._uid_validities.get(folder) is not None
                and incoming_validity is not None
                and self._uid_validities[folder] != incoming_validity
            )
            if state.get("last_uid") is not None:
                self._last_uids[folder] = (
                    state["last_uid"]
                    if validity_changed
                    else max(self._last_uids.get(folder, 0), state["last_uid"])
                )
            if incoming_validity is not None:
                self._uid_validities[folder] = incoming_validity
            self._folder_backfill_complete[folder] = bool(
                state.get("backfill_complete")
            )

        if old_settings != new_settings:
            await self.disconnect()

    async def connect(self) -> bool:
        """Connect to the IMAP server."""
        try:
            # Use IMAP-specific SSL/TLS settings
            imap_use_ssl = getattr(self.config, "imap_use_ssl", True)
            imap_use_tls = getattr(self.config, "imap_use_tls", False)

            # Create SSL context
            ssl_context = ssl.create_default_context()
            if not imap_use_ssl and not imap_use_tls:
                raise ValueError("Plaintext IMAP is disabled; configure SSL or STARTTLS")

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
            if getattr(self.config, "auth_type", "password") == "oauth2":
                from src.security.provider_tokens import refresh_access_token

                access_token = await asyncio.to_thread(
                    refresh_access_token, self.config.credential_ciphertext
                )
                login_response = await self.client.xoauth2(self.config.username, access_token.encode())
            else:
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
            self._discovered_folders = folders
            remaining = max(1, limit) if limit is not None else None

            for folder_index, folder in enumerate(folders):
                if remaining is not None and remaining <= 0:
                    for pending_folder in folders[folder_index:]:
                        self._folder_backfill_complete.setdefault(
                            pending_folder,
                            False,
                        )
                    break
                try:
                    async for batch in self._fetch_folder(
                        folder,
                        remaining,
                        since,
                    ):
                        yield batch
                        if remaining is not None:
                            remaining -= len(batch)
                except Exception as e:
                    logger.error(
                        "Error processing folder %s for %s: %s: %r",
                        folder,
                        self.config.name,
                        type(e).__name__,
                        e,
                    )
                    raise
            self.backfill_complete = bool(folders) and all(
                self._folder_backfill_complete.get(folder, False)
                for folder in folders
            )

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
            self._folder_backfill_complete[folder] = False
        if uid_validity:
            self._uid_validities[folder] = uid_validity

        last_uid = self._last_uids.get(folder)
        uid_next = self._extract_uid_next(select_response.lines)
        if last_uid is not None and uid_next is not None and last_uid >= uid_next - 1:
            self._folder_backfill_complete[folder] = True
            logger.debug("No new UIDs in folder %s for %s", folder, self.config.name)
            return

        if last_uid is not None:
            search_criteria = ("UID", f"{last_uid + 1}:*")
        elif since is not None:
            search_criteria = ("SINCE", self._format_imap_date(since))
        else:
            search_criteria = ("ALL",)

        if last_uid is not None:
            search_response = await self.client.search("UID", f"{last_uid + 1}:*")
        else:
            search_response = await self.client.search(*search_criteria)
        if search_response.result != "OK":
            raise RuntimeError(f"Search failed in folder {folder} for {self.config.name}")

        message_ids = search_response.lines[0].decode().split()
        if not message_ids:
            if uid_next is not None:
                self._last_uids[folder] = max(0, uid_next - 1)
            self._folder_backfill_complete[folder] = True
            logger.debug("No emails found in folder %s for %s", folder, self.config.name)
            return

        historical_complete = self._folder_backfill_complete.get(folder, False)
        truncated = bool(limit and len(message_ids) > limit)
        if truncated:
            # Always advance from the oldest remaining UID. Selecting the newest
            # messages here would permanently skip history when the cursor moves.
            message_ids = message_ids[:limit]

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
                fetch_response = await self.client.fetch(msg_id, "(UID FLAGS RFC822)")
                if fetch_response.result == "OK":
                    raw_email = self._extract_raw_email(fetch_response.lines)
                    message_uid = self._extract_message_uid(fetch_response.lines)
                    if last_uid is not None and message_uid is not None and message_uid <= last_uid:
                        logger.debug("Ignoring replayed UID %s in %s", message_uid, folder)
                        continue
                    email_data = await self._parse_email(
                        raw_email,
                        str(message_uid or msg_id),
                        folder=folder,
                        uid_validity=uid_validity,
                        flags=self._extract_flags(fetch_response.lines),
                    )
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
        if historical_complete or not truncated:
            self._folder_backfill_complete[folder] = True
        else:
            self._folder_backfill_complete[folder] = False

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

    @staticmethod
    def _extract_flags(lines) -> str:
        for line in lines:
            if isinstance(line, bytes):
                match = re.search(rb"\bFLAGS\s+\(([^)]*)\)", line)
                if match:
                    return match.group(1).decode("ascii", errors="ignore")
        return ""

    @staticmethod
    def _extract_raw_email(lines) -> bytes:
        candidates = [
            bytes(line)
            for line in lines
            if isinstance(line, (bytes, bytearray))
            and not re.match(rb"^\d+\s+\(", bytes(line))
            and bytes(line) not in {b")", b"", b"Success"}
        ]
        if not candidates:
            raise RuntimeError("IMAP FETCH returned no RFC822 payload")
        return max(candidates, key=len)

    async def _parse_email(
        self,
        raw_email: bytes,
        uid: str,
        *,
        folder: str,
        uid_validity: int | None,
        flags: str = "",
    ) -> Optional[Dict]:
        """Parse raw email data into structured format."""
        try:
            msg = message_from_bytes(raw_email, policy=policy.default)

            # Extract basic info
            sender = str(msg.get("From", ""))
            recipient = str(msg.get("To", ""))
            subject = str(msg.get("Subject", ""))
            message_id = str(msg.get("Message-ID", f"uid_{uid}_{self.config.id}"))
            date_str = msg.get("Date", "")
            to_addresses = [address for _, address in getaddresses(msg.get_all("To", []))]
            cc_addresses = [address for _, address in getaddresses(msg.get_all("Cc", []))]
            bcc_addresses = [address for _, address in getaddresses(msg.get_all("Bcc", []))]
            references = str(msg.get("References", ""))
            in_reply_to = str(msg.get("In-Reply-To", ""))
            reference_ids = re.findall(r"<[^>]+>", references)
            provider_thread_id = reference_ids[0] if reference_ids else (in_reply_to or message_id)

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
                    if part.get_content_disposition() == "attachment":
                        continue
                    try:
                        content = part.get_content()
                    except Exception:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        content = payload.decode(charset, errors="replace") if payload else ""
                    if content_type == "text/plain" and isinstance(content, str):
                        body_plain += content
                    elif content_type == "text/html" and isinstance(content, str):
                        body_html += content
            else:
                content_type = msg.get_content_type()
                try:
                    decoded = msg.get_content()
                except Exception:
                    payload = msg.get_payload(decode=True)
                    charset = msg.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="replace") if payload else ""
                if content_type == "text/plain" and isinstance(decoded, str):
                    body_plain = decoded
                elif content_type == "text/html" and isinstance(decoded, str):
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
                "provider_message_id": f"{folder}:{uid_validity or 0}:{uid}",
                "provider_thread_id": provider_thread_id[:768],
                "folder": folder,
                "imap_uid": int(uid) if uid.isdigit() else None,
                "uid_validity": uid_validity,
                "flags": flags,
                "to_addresses": json.dumps(to_addresses),
                "cc_addresses": json.dumps(cc_addresses),
                "bcc_addresses": json.dumps(bcc_addresses),
                "in_reply_to": in_reply_to[:255] or None,
                "references": references or None,
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

    async def fetch_raw_email(self, folder: str, uid: int, uid_validity: int | None = None) -> bytes:
        """Refetch one original RFC822 message without retaining its binary."""
        if not await self._ensure_connected():
            raise ConnectionError(f"Could not connect to {self.config.name}")
        selected = await self.client.select(f'"{folder}"')
        if selected.result != "OK":
            raise RuntimeError(f"Cannot select folder {folder}")
        current_validity = self._extract_uid_validity(selected.lines)
        if uid_validity and current_validity and uid_validity != current_validity:
            raise RuntimeError("Mailbox UIDVALIDITY changed; attachment reference is stale")
        response = await self.client.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if response.result != "OK":
            raise RuntimeError("Original message is no longer available from the provider")
        return self._extract_raw_email(response.lines)

    async def fetch_raw_by_message_id(self, message_id: str) -> bytes:
        """Resolve a legacy record that predates persisted UID provenance."""
        if not await self._ensure_connected():
            raise ConnectionError(f"Could not connect to {self.config.name}")
        safe_message_id = message_id.replace('"', "")
        for folder in await self._get_folders():
            selected = await self.client.select(f'"{folder}"')
            if selected.result != "OK":
                continue
            response = await self.client.search("HEADER", "Message-ID", f'"{safe_message_id}"')
            if response.result != "OK" or not response.lines:
                continue
            sequence_ids = response.lines[0].decode("ascii", errors="ignore").split()
            if not sequence_ids:
                continue
            fetched = await self.client.fetch(sequence_ids[-1], "(BODY.PEEK[])")
            if fetched.result == "OK":
                return self._extract_raw_email(fetched.lines)
        raise RuntimeError("Original message is no longer available from the provider")

    async def fetch_folder_state(self) -> dict[str, dict]:
        """Fetch UID and flag state only, for deletion/read-state reconciliation."""
        if not await self._ensure_connected():
            raise ConnectionError(f"Could not connect to {self.config.name}")
        snapshots = {}
        for folder in await self._get_folders():
            selected = await self.client.select(f'"{folder}"')
            if selected.result != "OK":
                continue
            uid_validity = self._extract_uid_validity(selected.lines)
            uids = set()
            flags = {}
            search = await self.client.search("ALL")
            if search.result != "OK":
                raise RuntimeError(f"Failed to list messages while reconciling {folder}")
            sequence_ids = search.lines[0].decode("ascii", errors="ignore").split() if search.lines else []
            # aioimaplib recursively parses untagged responses, so bound each metadata-only FETCH.
            for start in range(0, len(sequence_ids), 200):
                chunk = sequence_ids[start : start + 200]
                if not chunk:
                    continue
                sequence_set = chunk[0] if len(chunk) == 1 else f"{chunk[0]}:{chunk[-1]}"
                response = await self.client.fetch(sequence_set, "(UID FLAGS)")
                if response.result != "OK":
                    raise RuntimeError(f"Failed to reconcile folder state for {folder}")
                for line in response.lines:
                    if not isinstance(line, bytes):
                        continue
                    uid = self._extract_message_uid([line])
                    if uid is not None:
                        uids.add(uid)
                        flags[uid] = self._extract_flags([line])
            snapshots[folder] = {"uid_validity": uid_validity, "uids": uids, "flags": flags}
        return snapshots
