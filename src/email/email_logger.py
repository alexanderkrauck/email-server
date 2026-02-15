"""Email content logging to files."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from src.config import settings
from src.email.text_extractor import TextExtractor

logger = logging.getLogger(__name__)


class EmailLogger:
    """Handles logging email content to files in text + JSON format."""

    def __init__(self):
        self.log_dir = Path(settings.email_log_dir)
        self._ensure_log_directory()

    def _ensure_log_directory(self):
        """Ensure the log directory exists."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Email log directory: {self.log_dir}")

    async def log_email_to_file(self, email_data: Dict, email_id: int, account_email: str = None) -> Optional[str]:
        """Log email content to text file with metadata JSON, organized by email account."""
        try:
            account_dir = self._get_account_directory(account_email, email_data.get("recipient"))
            
            email_date = email_data.get("email_date", datetime.utcnow())
            if isinstance(email_date, str):
                email_date = datetime.fromisoformat(email_date.replace('Z', '+00:00'))
            
            year_month = email_date.strftime("%Y-%m")
            email_dir = account_dir / "emails" / year_month
            email_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = email_date.strftime("%Y%m%d_%H%M%S")
            
            text_path = email_dir / f"{timestamp}_{email_id}.txt"
            meta_path = email_dir / f"{timestamp}_{email_id}.meta.json"
            
            await self._write_text_file(text_path, email_data, email_id)
            await self._write_meta_file(meta_path, email_data, email_id, account_email)
            
            logger.debug(f"Email logged to: {text_path}")
            return str(text_path)

        except Exception as e:
            logger.error(f"Error logging email to file: {e}")
            return None

    def _get_account_directory(self, account_email: Optional[str], recipient: str) -> Path:
        """Get account-specific directory."""
        if account_email:
            return self.log_dir / account_email
        return self.log_dir / (recipient or "unknown@unknown.com")

    async def _write_text_file(self, file_path: Path, email_data: Dict, email_id: int):
        """Write email content as plain text."""
        text_extractor = TextExtractor()

        body_text = email_data.get("body_plain", "")
        
        if not body_text and email_data.get("body_html"):
            body_text = text_extractor._extract_html(
                email_data["body_html"].encode('utf-8')
            )
        
        content = []
        content.append(f"From: {email_data.get('sender', '')}")
        content.append(f"To: {email_data.get('recipient', '')}")
        content.append(f"Subject: {email_data.get('subject', '')}")
        content.append(f"Date: {email_data.get('email_date', '')}")
        content.append(f"Message-ID: {email_data.get('message_id', '')}")
        content.append("")
        content.append(body_text or "")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))

    async def _write_meta_file(self, file_path: Path, email_data: Dict, email_id: int, account_email: str):
        """Write email metadata as JSON."""
        email_date = email_data.get("email_date")
        meta = {
            "id": email_id,
            "account": account_email,
            "sender": email_data.get("sender", ""),
            "recipient": email_data.get("recipient", ""),
            "subject": email_data.get("subject", ""),
            "message_id": email_data.get("message_id", ""),
            "email_date": email_date.isoformat() if email_date else None,
            "processed_at": datetime.utcnow().isoformat(),
            "attachment_count": email_data.get("attachment_count", 0),
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def get_log_files(self, limit: int = 100) -> list:
        """Get list of recent log files."""
        try:
            files = []
            for file_path in self.log_dir.rglob("*.txt"):
                if file_path.is_file():
                    files.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": file_path.stat().st_size,
                        "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                    })

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

            for file_path in self.log_dir.rglob("*.txt"):
                # Skip attachment text files — those are managed by AttachmentHandler
                if "attachments" in file_path.parts:
                    continue
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1

            logger.info(f"Cleaned up {deleted_count} old log files")
            return deleted_count

        except Exception as e:
            logger.error(f"Error cleaning up old logs: {e}")
            return 0