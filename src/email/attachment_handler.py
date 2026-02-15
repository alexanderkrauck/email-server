"""Email attachment handling."""

import logging
from email import message_from_bytes
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.storage_config.resolver import StorageConfig

from src.models.attachment import EmailAttachment

logger = logging.getLogger(__name__)


class AttachmentHandler:
    """Handles email attachment extraction. Text is stored directly in DB."""

    async def extract_attachments(
        self, raw_email: bytes, email_log_id: int, storage_config: Optional["StorageConfig"] = None
    ) -> List[EmailAttachment]:
        """Extract attachments from raw email and return attachment objects."""
        if storage_config is None:
            from src.storage_config.resolver import resolve_storage_config

            storage_config = resolve_storage_config(None)

        try:
            msg = message_from_bytes(raw_email)
            attachments = []

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                content_disposition = part.get("Content-Disposition", "")

                if "attachment" in content_disposition or self._is_attachment(part):
                    attachment = await self._process_attachment(part, email_log_id, storage_config)
                    if attachment:
                        attachments.append(attachment)

            logger.info("Extracted %s attachments for email %s", len(attachments), email_log_id)
            return attachments

        except Exception as e:
            logger.error("Error extracting attachments for email %s: %s", email_log_id, e)
            return []

    def _is_attachment(self, part) -> bool:
        """Check if email part is an attachment."""
        filename = part.get_filename()
        content_disposition = part.get("Content-Disposition", "")
        content_type = part.get_content_type()

        if filename or "attachment" in content_disposition:
            return True

        return content_type.startswith(("image/", "audio/", "video/", "application/"))

    async def _process_attachment(
        self, part, email_log_id: int, storage_config: Optional["StorageConfig"] = None
    ) -> Optional[EmailAttachment]:
        """Process a single attachment part."""
        if storage_config is None:
            from src.storage_config.resolver import resolve_storage_config

            storage_config = resolve_storage_config(None)

        try:
            filename = part.get_filename() or f"attachment_{email_log_id}_unknown"
            content_type = part.get_content_type()
            content_id = part.get("Content-ID", "").strip("<>")

            payload = part.get_payload(decode=True)
            if not payload:
                logger.warning("Empty payload for attachment %s", filename)
                return None

            size = len(payload)

            attachment = EmailAttachment(
                email_log_id=email_log_id,
                filename=self._sanitize_filename(filename),
                content_type=content_type,
                content_id=content_id,
                size=size,
            )

            # Extract text directly from in-memory payload and store in DB column
            from src.email.text_extractor import TextExtractor

            text_extractor = TextExtractor()

            text_content = await text_extractor.extract(payload, content_type, storage_config)

            if text_content:
                attachment.text_content = text_content
                logger.info("Extracted text for %s (%s chars)", filename, len(text_content))
            else:
                logger.debug("No text extracted for %s (type: %s)", filename, content_type)

            return attachment

        except Exception as e:
            logger.error("Error processing attachment: %s", e)
            return None

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage."""
        from src.email import sanitize_filename

        return sanitize_filename(filename)
