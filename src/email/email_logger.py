"""Email content logging to files."""

import json
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import logging
from src.config import settings
from src.email.markdown_converter import EmailToMarkdownConverter

logger = logging.getLogger(__name__)


class EmailLogger:
    """Handles logging email content to files in readable formats."""

    def __init__(self):
        self.log_dir = Path(settings.email_log_dir)
        self.markdown_converter = EmailToMarkdownConverter()
        self._ensure_log_directory()

    def _ensure_log_directory(self):
        """Ensure the log directory exists."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Email log directory: {self.log_dir}")

    async def log_email_to_file(self, email_data: Dict, email_id: int, account_email: str = None) -> Optional[str]:
        """Log email content to a markdown file, organized by email account."""
        try:
            # Create account-specific directory using full email address
            if account_email:
                account_dir = self.log_dir / account_email
            else:
                # Fallback: use recipient email as account identifier
                recipient = email_data.get("recipient", "unknown@unknown.com")
                account_dir = self.log_dir / recipient

            account_dir.mkdir(parents=True, exist_ok=True)

            # Create individual email directory with meaningful ID
            email_date = email_data.get("email_date", datetime.utcnow())
            if isinstance(email_date, str):
                email_date = datetime.fromisoformat(email_date.replace('Z', '+00:00'))

            timestamp = email_date.strftime("%Y%m%d_%H%M%S")
            subject_safe = self._sanitize_filename(email_data.get("subject", "no-subject")[:50])
            sender_safe = self._sanitize_filename(email_data.get("sender", "unknown")[:20])

            # Create unique directory name based on timestamp, subject, and sender
            email_dir_name = f"{timestamp}_{subject_safe}_{sender_safe}"
            email_dir = account_dir / email_dir_name
            email_dir.mkdir(parents=True, exist_ok=True)

            # Create email filename
            filename = f"{timestamp}_{subject_safe}.md"
            file_path = email_dir / filename

            # Convert to markdown and save
            markdown_content = self.markdown_converter.convert_email_to_markdown(email_data)
            await self._write_markdown_file(file_path, markdown_content)

            logger.debug(f"Email logged to: {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"Error logging email to file: {e}")
            return None

    async def _write_markdown_file(self, file_path: Path, markdown_content: str):
        """Write markdown content to file."""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

    async def _write_json_log(self, file_path: Path, email_data: Dict, email_id: int):
        """Write email data in JSON format."""
        log_data = {
            "email_id": email_id,
            "timestamp": datetime.utcnow().isoformat(),
            "smtp_config_id": email_data.get("smtp_config_id"),
            "sender": email_data.get("sender", ""),
            "recipient": email_data.get("recipient", ""),
            "subject": email_data.get("subject", ""),
            "message_id": email_data.get("message_id", ""),
            "email_date": email_data.get("email_date").isoformat() if email_data.get("email_date") else None,
            "content_size": email_data.get("content_size", 0),
            "attachment_count": email_data.get("attachment_count", 0),
            "body": {
                "plain": email_data.get("body_plain", ""),
                "html": email_data.get("body_html", "")
            }
        }

        # Write JSON file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False, default=str)

    async def _write_text_log(self, file_path: Path, email_data: Dict, email_id: int):
        """Write email data in human-readable text format."""
        content = []
        content.append("=" * 80)
        content.append(f"Email ID: {email_id}")
        content.append(f"Processed: {datetime.utcnow().isoformat()}")
        content.append(f"SMTP Config ID: {email_data.get('smtp_config_id')}")
        content.append("=" * 80)
        content.append(f"From: {email_data.get('sender', '')}")
        content.append(f"To: {email_data.get('recipient', '')}")
        content.append(f"Subject: {email_data.get('subject', '')}")
        content.append(f"Message ID: {email_data.get('message_id', '')}")

        email_date = email_data.get("email_date")
        if email_date:
            content.append(f"Date: {email_date.isoformat()}")

        content.append(f"Content Size: {email_data.get('content_size', 0)} bytes")
        content.append(f"Attachments: {email_data.get('attachment_count', 0)}")
        content.append("-" * 80)

        # Plain text body
        body_plain = email_data.get("body_plain", "")
        if body_plain:
            content.append("PLAIN TEXT BODY:")
            content.append(body_plain)
            content.append("-" * 80)

        # HTML body
        body_html = email_data.get("body_html", "")
        if body_html:
            content.append("HTML BODY:")
            content.append(body_html)
            content.append("-" * 80)

        content.append("")  # End with empty line

        # Write text file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename by removing invalid characters, spaces, and encoding issues."""
        import re

        # Handle None or empty strings
        if not filename:
            return "unknown"

        # Remove/decode common email encoding artifacts
        filename = filename.replace('=_utf-8_B_', '').replace('=_utf-8_Q_', '')
        filename = filename.replace('_utf-8_', '').replace('=C3=A4', 'ae').replace('=C3=BC', 'ue')
        filename = filename.replace('=C3=B6', 'oe').replace('=C3=9F', 'ss')

        # Replace spaces with underscores
        filename = filename.replace(' ', '_')

        # Remove invalid filesystem characters
        invalid_chars = '<>:"/\\|?*[](){}!@#$%^&+=`~;,\'\"'
        for char in invalid_chars:
            filename = filename.replace(char, '')

        # Replace multiple underscores with single underscore
        filename = re.sub(r'_{2,}', '_', filename)

        # Remove leading/trailing underscores and dots
        filename = filename.strip('_.')

        # Ensure it's not empty after cleaning
        if not filename:
            return "cleaned_subject"

        return filename[:100]  # Limit length to prevent filesystem issues

    async def log_raw_email(self, raw_email: bytes, email_id: int, sender: str) -> Optional[str]:
        """Log raw email bytes to file for debugging."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            sender_safe = self._sanitize_filename(sender[:50])
            filename = f"{timestamp}_{email_id}_{sender_safe}_raw.eml"
            file_path = self.log_dir / filename

            with open(file_path, 'wb') as f:
                f.write(raw_email)

            logger.debug(f"Raw email logged to: {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"Error logging raw email: {e}")
            return None

    def get_log_files(self, limit: int = 100) -> list:
        """Get list of recent log files."""
        try:
            files = []
            for file_path in self.log_dir.iterdir():
                if file_path.is_file():
                    files.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": file_path.stat().st_size,
                        "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                    })

            # Sort by modification time, newest first
            files.sort(key=lambda x: x["modified"], reverse=True)
            return files[:limit]

        except Exception as e:
            logger.error(f"Error listing log files: {e}")
            return []

    async def cleanup_old_logs(self, days_old: int = 30):
        """Clean up log files older than specified days."""
        try:
            cutoff_time = datetime.utcnow().timestamp() - (days_old * 24 * 3600)
            deleted_count = 0

            for file_path in self.log_dir.iterdir():
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1

            logger.info(f"Cleaned up {deleted_count} old log files")
            return deleted_count

        except Exception as e:
            logger.error(f"Error cleaning up old logs: {e}")
            return 0