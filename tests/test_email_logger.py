"""Tests for email logger."""

import pytest
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

os.environ["EMAIL_LOG_DIR"] = "/tmp/test_email_logger"


@pytest.mark.asyncio
async def test_email_logger_initialization():
    """Test EmailLogger initialization."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = "/tmp/test_emails"
        
        logger = EmailLogger()
        assert logger.log_dir == Path("/tmp/test_emails")


@pytest.mark.asyncio
async def test_log_email_to_file(temp_dir, sample_email_data):
    """Test logging email to file."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(sample_email_data, 1, "test@example.com")
        
        assert result is not None
        assert Path(result).exists()
        assert Path(result).suffix == ".txt"


@pytest.mark.asyncio
async def test_log_email_with_html(temp_dir):
    """Test logging email with HTML content."""
    from src.email.email_logger import EmailLogger

    email_data = {
        "smtp_config_id": 1,
        "sender": "test@example.com",
        "recipient": "recipient@example.com",
        "subject": "Test Email",
        "message_id": "<test-123@example.com>",
        "email_date": datetime(2025, 1, 15, 10, 30, 0),
        "body_plain": "",
        "body_html": "<html><body><p>Hello World</p></body></html>",
        "attachment_count": 0,
    }

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(email_data, 1, "test@example.com")
        
        assert result is not None
        content = Path(result).read_text()
        assert "Hello World" in content


@pytest.mark.asyncio
async def test_log_email_creates_meta_file(temp_dir, sample_email_data):
    """Test that meta file is created alongside text file."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(sample_email_data, 1, "test@example.com")
        
        assert result is not None
        txt_path = Path(result)
        meta_path = txt_path.with_suffix(".meta.json")
        
        assert meta_path.exists()
        import json
        meta = json.loads(meta_path.read_text())
        assert meta["id"] == 1
        assert meta["sender"] == "test@example.com"


def test_sanitize_filename():
    """Test shared filename sanitization."""
    from src.email import sanitize_filename

    assert sanitize_filename("normal_file.txt") == "normal_file.txt"
    assert sanitize_filename("") == "unknown"
    # @ is stripped by the filename sanitizer (not meant for account names)
    assert "@" not in sanitize_filename("user@example.com")


@pytest.mark.asyncio
async def test_get_log_files(temp_dir):
    """Test getting log files list."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        
        # Create some test files
        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("content")
        
        files = logger.get_log_files()
        assert len(files) >= 1
        assert files[0]["name"] == "test.txt"


@pytest.mark.asyncio
async def test_cleanup_old_logs_skips_attachments(temp_dir):
    """Test that cleanup_old_logs does not delete attachment text files."""
    from src.email.email_logger import EmailLogger
    import os
    import time

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir

        logger = EmailLogger()

        # Create an old email log file
        email_file = Path(temp_dir) / "old_email.txt"
        email_file.write_text("email content")
        old_time = time.time() - (60 * 24 * 3600)  # 60 days ago
        os.utime(email_file, (old_time, old_time))

        # Create an old attachment text file
        att_dir = Path(temp_dir) / "attachments"
        att_dir.mkdir()
        att_file = att_dir / "1_test.txt"
        att_file.write_text("attachment text")
        os.utime(att_file, (old_time, old_time))

        deleted = await logger.cleanup_old_logs(days_old=30)

        assert deleted == 1  # Only the email file
        assert not email_file.exists()
        assert att_file.exists()  # Attachment file preserved


@pytest.mark.asyncio
async def test_get_account_directory(temp_dir):
    """Test account directory creation."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        
        # With account email
        account_dir = logger._get_account_directory("test@example.com", None)
        assert account_dir.name == "test@example.com"
        
        # Without account email, use recipient
        account_dir = logger._get_account_directory(None, "recipient@example.com")
        assert account_dir.name == "recipient@example.com"
