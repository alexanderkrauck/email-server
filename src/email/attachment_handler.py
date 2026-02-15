"""Email attachment handling."""

# type: ignore[assignment]
# type: ignore[return-value]
# The above ignores are needed for SQLAlchemy Column proxy types

import logging
from email import message_from_bytes
from pathlib import Path
from typing import List, Optional

from src.config import settings
from src.models.attachment import EmailAttachment

logger = logging.getLogger(__name__)


class AttachmentHandler:
    """Handles email attachment extraction and storage."""

    def __init__(self):
        self.email_log_dir = Path(settings.email_log_dir)
        # Attachments will be stored under emails directory structure

    def _ensure_attachment_directory(self, attachment_dir: Path):
        """Ensure the attachment directory exists."""
        attachment_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        logger.debug(f"Attachment directory: {attachment_dir}")

    async def extract_attachments(
        self,
        raw_email: bytes,
        email_log_id: int,
        account_name: Optional[str] = None,
        storage_config: Optional["StorageConfig"] = None
    ) -> List[EmailAttachment]:
        """Extract attachments from raw email and return attachment objects."""
        if storage_config is None:
            from src.storage_config.resolver import resolve_storage_config
            storage_config = resolve_storage_config(None)
        
        try:
            msg = message_from_bytes(raw_email)
            attachments = []

            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue

                content_disposition = part.get('Content-Disposition', '')
                part.get_content_type()

                if 'attachment' in content_disposition or self._is_attachment(part):
                    attachment = await self._process_attachment(part, email_log_id, account_name, storage_config)
                    if attachment:
                        attachments.append(attachment)

            logger.info(f"Extracted {len(attachments)} attachments for email {email_log_id}")
            return attachments

        except Exception as e:
            logger.error(f"Error extracting attachments for email {email_log_id}: {e}")
            return []

    def _is_attachment(self, part) -> bool:
        """Check if email part is an attachment."""
        filename = part.get_filename()
        content_disposition = part.get('Content-Disposition', '')
        content_type = part.get_content_type()

        # Has filename or explicit attachment disposition
        if filename or 'attachment' in content_disposition:
            return True

        # Inline images or other media
        if content_type.startswith(('image/', 'audio/', 'video/', 'application/')):
            return True

        return False

    async def _process_attachment(
        self,
        part,
        email_log_id: int,
        account_name: Optional[str] = None,
        storage_config: Optional["StorageConfig"] = None
    ) -> Optional[EmailAttachment]:
        """Process a single attachment part."""
        if storage_config is None:
            from src.storage_config.resolver import resolve_storage_config
            storage_config = resolve_storage_config(None)
        
        try:
            filename = part.get_filename() or f"attachment_{email_log_id}_unknown"
            content_type = part.get_content_type()
            content_id = part.get('Content-ID', '').strip('<>')

            payload = part.get_payload(decode=True)
            if not payload:
                logger.warning(f"Empty payload for attachment {filename}")
                return None

            size = len(payload)

            attachment = EmailAttachment(
                email_log_id=email_log_id,
                filename=self._sanitize_filename(filename),
                content_type=content_type,
                content_id=content_id,
                size=size,
            )

            # Extract text directly from in-memory payload
            from src.email.text_extractor import TextExtractor
            text_extractor = TextExtractor()

            text_content = await text_extractor.extract(payload, content_type, storage_config)

            if text_content:
                text_file_path = await self._save_attachment_text(
                    text_content, filename, email_log_id, account_name
                )
                if text_file_path:
                    attachment.text_file_path = str(text_file_path)
                    logger.info(f"Saved extracted text for {filename} -> {text_file_path}")
            else:
                logger.debug(f"No text extracted for {filename} (type: {content_type})")

            return attachment

        except Exception as e:
            logger.error(f"Error processing attachment: {e}")
            return None

    async def _save_attachment_text(
        self,
        text_content: str,
        filename: str,
        email_log_id: int,
        account_name: Optional[str] = None
    ) -> Optional[Path]:
        """Save extracted text from attachment."""
        try:
            if account_name:
                account_safe = self._sanitize_account_name(account_name)
                attachment_dir = self.email_log_dir / account_safe / "emails" / "attachments"
            else:
                attachment_dir = self.email_log_dir / "emails" / "attachments"
            
            self._ensure_attachment_directory(attachment_dir)
            
            safe_filename = self._sanitize_filename(filename)
            text_filename = f"{email_log_id}_{safe_filename}.txt"
            text_path = attachment_dir / text_filename

            # Handle duplicate filenames within the same email
            counter = 1
            while text_path.exists():
                text_filename = f"{email_log_id}_{safe_filename}_{counter}.txt"
                text_path = attachment_dir / text_filename
                counter += 1

            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(text_content)
            
            return text_path
            
        except Exception as e:
            logger.error(f"Error saving attachment text: {e}")
            return None

    @staticmethod
    def _sanitize_account_name(account_name: str) -> str:
        """Sanitize account name for use as directory name, preserving @ for email addresses."""
        if not account_name:
            return "unknown"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            account_name = account_name.replace(char, '_')
        return account_name.strip('_.')

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage."""
        from src.email import sanitize_filename
        return sanitize_filename(filename)

    async def get_attachment_data(self, attachment: EmailAttachment) -> Optional[bytes]:
        """Get attachment text data from filesystem."""
        try:
            # Try text file path (new storage model)
            text_path = attachment.text_file_path
            if text_path and Path(text_path).exists():
                with open(text_path, 'rb') as f:
                    return f.read()
            
            logger.debug(f"Attachment {attachment.id} text file not found")
            return None

        except Exception as e:
            logger.error(f"Error retrieving attachment {attachment.id}: {e}")
            return None


    async def delete_attachment_files(self, email_log_id: int):
        """Delete all attachment text files for an email.
        
        Text files are stored as {account}/emails/attachments/{email_log_id}_{filename}.txt
        so we glob for the email_log_id prefix across all account directories.
        """
        try:
            prefix = f"{email_log_id}_"
            deleted = 0
            for text_file in self.email_log_dir.rglob(f"attachments/{prefix}*.txt"):
                text_file.unlink()
                deleted += 1
            if deleted:
                logger.info(f"Deleted {deleted} attachment text files for email {email_log_id}")
        except Exception as e:
            logger.error(f"Error deleting attachment files for email {email_log_id}: {e}")

    async def cleanup_orphaned_files(self):
        """Clean up orphaned attachment files."""
        try:
            # This would require database access to check which emails still exist
            # For now, just log the intent
            logger.info("Attachment cleanup would run here")
        except Exception as e:
            logger.error(f"Error during attachment cleanup: {e}")