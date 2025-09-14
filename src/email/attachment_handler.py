"""Email attachment handling."""

import os
import logging
from pathlib import Path
from typing import List, Dict, Optional
from email.message import EmailMessage
from src.models.attachment import EmailAttachment
from src.config import settings

logger = logging.getLogger(__name__)


class AttachmentHandler:
    """Handles email attachment extraction and storage."""

    def __init__(self):
        self.email_log_dir = Path(settings.email_log_dir)
        # Attachments will be stored under emails directory structure

    def _ensure_attachment_directory(self, attachment_dir: Path):
        """Ensure the attachment directory exists."""
        attachment_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Attachment directory: {attachment_dir}")

    async def extract_attachments(self, raw_email: bytes, email_log_id: int, account_name: str = None) -> List[EmailAttachment]:
        """Extract attachments from raw email and return attachment objects."""
        try:
            from email import message_from_bytes
            msg = message_from_bytes(raw_email)
            attachments = []

            for part in msg.walk():
                # Skip non-attachment parts
                if part.get_content_maintype() == 'multipart':
                    continue

                content_disposition = part.get('Content-Disposition', '')
                content_type = part.get_content_type()

                # Check if it's an attachment
                if 'attachment' in content_disposition or self._is_attachment(part):
                    attachment = await self._process_attachment(part, email_log_id, account_name)
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

    async def _process_attachment(self, part, email_log_id: int, account_name: str = None) -> Optional[EmailAttachment]:
        """Process a single attachment part."""
        try:
            # Get attachment metadata
            filename = part.get_filename() or f"attachment_{email_log_id}_unknown"
            content_type = part.get_content_type()
            content_id = part.get('Content-ID', '').strip('<>')

            # Get attachment data
            payload = part.get_payload(decode=True)
            if not payload:
                logger.warning(f"Empty payload for attachment {filename}")
                return None

            size = len(payload)

            # Create attachment object
            attachment = EmailAttachment(
                email_log_id=email_log_id,
                filename=self._sanitize_filename(filename),
                content_type=content_type,
                content_id=content_id,
                size=size
            )

            # Decide storage method based on size
            if size <= 1024 * 1024:  # 1MB - store in database
                attachment.content_data = payload
                logger.debug(f"Storing small attachment {filename} in database")
            else:
                # Store large attachments as files and create markdown
                file_paths = await self._save_attachment_with_markdown(payload, filename, content_type, size, email_log_id, account_name)
                if file_paths:
                    attachment.file_path = str(file_paths['binary'])
                    logger.debug(f"Stored large attachment {filename} at {file_paths['binary']} with metadata at {file_paths['markdown']}")
                else:
                    logger.error(f"Failed to save attachment file {filename}")
                    return None

            return attachment

        except Exception as e:
            logger.error(f"Error processing attachment: {e}")
            return None

    async def _save_attachment_file(self, data: bytes, filename: str, email_log_id: int, account_name: str = None) -> Optional[Path]:
        """Save attachment data to file under emails directory structure."""
        try:
            # Create attachment directory under emails structure
            if account_name:
                account_safe = self._sanitize_filename(account_name)
                attachment_dir = self.email_log_dir / account_safe / "attachments" / str(email_log_id)
            else:
                # Fallback to general attachments directory
                attachment_dir = self.email_log_dir / "attachments" / str(email_log_id)

            self._ensure_attachment_directory(attachment_dir)

            # Create unique filename if needed
            safe_filename = self._sanitize_filename(filename)
            file_path = attachment_dir / safe_filename

            # Handle duplicate filenames
            counter = 1
            original_path = file_path
            while file_path.exists():
                name = original_path.stem
                ext = original_path.suffix
                file_path = email_dir / f"{name}_{counter}{ext}"
                counter += 1

            # Write file
            with open(file_path, 'wb') as f:
                f.write(data)

            return file_path

        except Exception as e:
            logger.error(f"Error saving attachment file {filename}: {e}")
            return None

    async def _save_attachment_with_markdown(self, data: bytes, filename: str, content_type: str, size: int, email_log_id: int, account_name: str = None) -> Optional[dict]:
        """Save attachment data and create markdown metadata file."""
        try:
            # Create attachment directory under emails structure
            if account_name:
                account_safe = self._sanitize_filename(account_name)
                attachment_dir = self.email_log_dir / account_safe / "attachments" / str(email_log_id)
            else:
                attachment_dir = self.email_log_dir / "attachments" / str(email_log_id)

            self._ensure_attachment_directory(attachment_dir)

            # Create filenames
            safe_filename = self._sanitize_filename(filename)
            binary_path = attachment_dir / safe_filename
            markdown_path = attachment_dir / f"{safe_filename}.md"

            # Handle duplicate filenames
            counter = 1
            original_binary = binary_path
            original_markdown = markdown_path

            while binary_path.exists() or markdown_path.exists():
                name, ext = os.path.splitext(safe_filename)
                new_filename = f"{name}_{counter}{ext}"
                binary_path = attachment_dir / new_filename
                markdown_path = attachment_dir / f"{new_filename}.md"
                counter += 1

            # Save binary file
            with open(binary_path, 'wb') as f:
                f.write(data)

            # Create markdown metadata
            markdown_content = await self._create_attachment_markdown(filename, content_type, size, binary_path, data)
            with open(markdown_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            return {
                'binary': binary_path,
                'markdown': markdown_path
            }

        except Exception as e:
            logger.error(f"Error saving attachment with markdown {filename}: {e}")
            return None

    async def _create_attachment_markdown(self, filename: str, content_type: str, size: int, binary_path: Path, data: bytes) -> str:
        """Create markdown metadata for attachment."""
        from datetime import datetime

        md_content = []
        md_content.append(f"# Attachment: {filename}")
        md_content.append("")

        # Metadata table
        md_content.append("## Metadata")
        md_content.append("")
        md_content.append("| Field | Value |")
        md_content.append("|-------|-------|")
        md_content.append(f"| **Filename** | {filename} |")
        md_content.append(f"| **Content Type** | {content_type or 'unknown'} |")
        md_content.append(f"| **Size** | {self._format_file_size(size)} ({size} bytes) |")
        md_content.append(f"| **Binary Path** | `{binary_path.name}` |")
        md_content.append(f"| **Processed** | {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} |")
        md_content.append("")

        # Content preview for text files
        if content_type and content_type.startswith('text/'):
            try:
                text_content = data.decode('utf-8', errors='ignore')[:2000]  # First 2KB
                md_content.append("## Content Preview")
                md_content.append("")
                md_content.append("```")
                md_content.append(text_content)
                if len(data) > 2000:
                    md_content.append("...")
                    md_content.append(f"[Truncated - showing first 2KB of {size} bytes total]")
                md_content.append("```")
                md_content.append("")
            except Exception:
                pass

        # Image info
        elif content_type and content_type.startswith('image/'):
            md_content.append("## Content")
            md_content.append("")
            md_content.append(f"Image file: {content_type}")
            md_content.append(f"Binary data stored in: `{binary_path.name}`")
            md_content.append("")

        # Other file types
        else:
            md_content.append("## Content")
            md_content.append("")
            md_content.append(f"Binary file: {content_type or 'unknown type'}")
            md_content.append(f"Data stored in: `{binary_path.name}`")
            md_content.append("")

        md_content.append("---")
        md_content.append("*Attachment processed by Email Server*")

        return '\n'.join(md_content)

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage."""
        # Remove or replace dangerous characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        # Limit length
        if len(filename) > 100:
            name, ext = os.path.splitext(filename)
            filename = name[:95] + ext

        # Ensure not empty
        if not filename or filename.isspace():
            filename = "unnamed_attachment"

        return filename.strip()

    async def get_attachment_data(self, attachment: EmailAttachment) -> Optional[bytes]:
        """Get attachment data from database or filesystem."""
        try:
            # First try database storage
            if attachment.content_data:
                return attachment.content_data

            # Try filesystem storage
            if attachment.file_path and Path(attachment.file_path).exists():
                with open(attachment.file_path, 'rb') as f:
                    return f.read()

            logger.warning(f"Attachment {attachment.id} not found in database or filesystem")
            return None

        except Exception as e:
            logger.error(f"Error retrieving attachment {attachment.id}: {e}")
            return None


    async def delete_attachment_files(self, email_log_id: int):
        """Delete all attachment files for an email."""
        try:
            email_dir = self.attachment_dir / str(email_log_id)
            if email_dir.exists():
                for file_path in email_dir.iterdir():
                    file_path.unlink()
                email_dir.rmdir()
                logger.info(f"Deleted attachment files for email {email_log_id}")
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